from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
import unittest
from unittest import mock

from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.contracts import FilesystemPolicyContract
from scripts.preflight.pip_bootstrap import _validate_pip_argv
from scripts.preflight.resolver import (
    ProcessRunner,
    PipReportContext,
    ReportCoverage,
    ResolveAuthorization,
    ResolveAuthorizationError,
    Resolver,
    ResolverExecutionError,
    ResolverPaths,
    ResolverScope,
    _read_repository_head,
    _validate_resolver_inputs,
    consume_pip_report,
)


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]
APP = WORKSPACE / "app"


class ExplodingRunner(ProcessRunner):
    def run(
        self,
        argv,
        *,
        cwd,
        environment,
        timeout_seconds,
        max_output_chars,
    ):
        raise AssertionError("resolver process must not execute")


class InMemoryReportFS:
    def __init__(self, value):
        self.value = value

    def read_json(self, path, *, max_chars=16_000_000):
        return self.value


class InMemoryAuthorizationFS(WorkspaceFS):
    def __init__(self, boundary, value):
        super().__init__(boundary)
        self.value = value

    def read_json(self, path, *, max_chars=16_000_000):
        return self.value


class FakePipe:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def read(self, _size):
        return self.chunks.pop(0) if self.chunks else ""

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdout_chunks, stderr_chunks):
        self.stdout = FakePipe(stdout_chunks)
        self.stderr = FakePipe(stderr_chunks)
        self.killed = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class ResolverSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boundary = WorkspaceBoundary(WORKSPACE)
        self.fs = WorkspaceFS(self.boundary)
        self.paths = ResolverPaths.from_boundary(self.boundary)
        self.resolver = Resolver(
            self.fs, APP, self.paths, runner=ExplodingRunner()
        )

    def test_resolve_requires_authorization_before_side_effects(self) -> None:
        event_count = len(self.boundary.events)
        with self.assertRaises(ResolveAuthorizationError) as raised:
            self.resolver.require_authorization(None)
        self.assertEqual(
            "RESOLVE_NETWORK_AUTHORIZATION_REQUIRED", str(raised.exception)
        )
        self.assertEqual(event_count, len(self.boundary.events))

    def test_resolver_mutable_paths_are_all_under_evidence(self) -> None:
        report = self.paths.report_dict(self.boundary)
        for key in ("PIP_CACHE_DIR", "TEMP", "TMP", "reports"):
            self.assertTrue(report[key].startswith("Evidence/preflight/"))
        self.assertEqual("NUL", report["PIP_CONFIG_FILE"])

    def test_checked_in_denied_fixture_can_never_authorize(self) -> None:
        fixture = self.fs.read_json(
            APP
            / "scripts"
            / "preflight"
            / "tests"
            / "fixtures"
            / "resolve-authorization-denied.json"
        )
        self.assertEqual("NOT AUTHORIZATION", fixture["authorization_notice"])
        with self.assertRaises(ResolveAuthorizationError):
            ResolveAuthorization.load(
                InMemoryAuthorizationFS(self.boundary, fixture),
                self.paths,
                self.paths.authorizations / "denied-fixture.json",
            )

    def test_scope_proposal_is_non_authorizing_and_strict(self) -> None:
        value = {
            "schema_version": "forge-resolve-scope/v1",
            "operation": "pip-structured-dry-run-plan",
            "index_url": "https://packages.example.invalid/simple",
            "allowed_endpoints": ["packages.example.invalid"],
            "redirect_risk_acknowledged": True,
            "target_environment": self._marker_environment(),
            "filesystem_policy": self._filesystem_policy(),
            "timeout_seconds": 600,
            "max_output_chars": 100000,
        }
        scope_path = self.paths.authorizations / "owner-review-scope.json"
        scope = ResolverScope.load(
            InMemoryAuthorizationFS(self.boundary, value),
            self.paths,
            scope_path,
        )
        self.assertEqual(
            ("packages.example.invalid",),
            scope.allowed_endpoints,
        )
        self.assertNotIn("network_authorized", scope.to_digest_dict())
        with self.assertRaises(ResolveAuthorizationError):
            ResolverScope.load(
                InMemoryAuthorizationFS(
                    self.boundary,
                    {**value, "network_authorized": True},
                ),
                self.paths,
                scope_path,
            )

    def test_dry_run_plan_uses_exact_venv_python_and_no_shell(self) -> None:
        scope = ResolverScope(
            index_url="https://owner-approved.invalid/simple",
            allowed_endpoints=("owner-approved.invalid",),
            redirect_risk_acknowledged=True,
            target_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        plan = self.resolver.build_plan(
            scope=scope,
            local_dependency_decision={
                "decision": "GO",
                "reason_code": "TEST_LOCAL_GATE",
            },
            filesystem_policy=self._filesystem_policy(),
        )
        self.assertEqual(
            os.path.normcase(os.fspath(APP / "venv" / "Scripts" / "python.exe")),
            os.path.normcase(plan.argv[0]),
        )
        self.assertEqual(("-I", "-S", "-B"), plan.argv[1:4])
        self.assertEqual(
            os.path.normcase(
                os.fspath(APP / "scripts" / "preflight" / "pip_bootstrap.py")
            ),
            os.path.normcase(plan.argv[4]),
        )
        self.assertIn("--dry-run", plan.argv)
        self.assertIn("--only-binary=:all:", plan.argv)
        self.assertNotIn("--upgrade", plan.argv)
        self.assertNotIn("uninstall", plan.argv)
        self.assertNotIn("-m", plan.argv)
        self.assertEqual("NUL", plan.environment["PIP_CONFIG_FILE"])
        self.assertEqual("*", plan.environment["NO_PROXY"])
        self.assertEqual("*", plan.environment["no_proxy"])
        self.assertEqual(
            "disabled",
            plan.environment["PIP_KEYRING_PROVIDER"],
        )
        for key in ("PIP_CACHE_DIR", "TEMP", "TMP"):
            self.assertIn(
                os.path.normcase(os.fspath(WORKSPACE / "Evidence")),
                os.path.normcase(plan.environment[key]),
            )
        self.assertIn(
            os.path.normcase(os.fspath(WORKSPACE / "Evidence")),
            os.path.normcase(plan.environment["NETRC"]),
        )
        self.assertEqual(plan.netrc_file, Path(plan.environment["NETRC"]))
        self.assertEqual(plan.run_id, plan.cache_directory.name)
        self.assertEqual(plan.run_id, plan.temp_directory.name)
        self.assertEqual(plan.run_id, plan.report_directory.name)
        self.assertIn(
            os.path.normcase(os.fspath(WORKSPACE / "Evidence")),
            os.path.normcase(os.fspath(plan.cwd)),
        )
        self.assertEqual(
            ReportCoverage.SELECTED_CANDIDATE_SET,
            plan.coverage,
        )
        self.assertIn("python", plan.execution_manifest)
        self.assertIn("pip", plan.execution_manifest)
        self.assertIn("packaging", plan.execution_manifest)
        self.assertEqual(
            ("gradio==4.40.0", "gradio_rangeslider==0.0.8"),
            plan.launcher_requirements,
        )
        self.assertRegex(plan.launcher_source_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(
            plan.local_dependency_gate_sha256,
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            self._filesystem_policy(),
            plan.filesystem_policy,
        )
        self.assertEqual(
            _read_repository_head(self.fs, APP),
            plan.repository_head,
        )
        self.assertRegex(
            plan.dependency_plan_sha256,
            r"^sha256:[0-9a-f]{64}$",
        )
        review = plan.review_dict()
        self.assertEqual(
            plan.launcher_source_sha256,
            review["launcher_source_sha256"],
        )
        self.assertEqual(
            plan.local_dependency_gate_sha256,
            review["local_dependency_gate_sha256"],
        )
        self.assertEqual(plan.repository_head, review["repository_head"])
        self.assertEqual(
            plan.dependency_plan_sha256,
            review["dependency_plan_sha256"],
        )
        self.assertEqual(
            self._filesystem_policy(),
            review["filesystem_policy"],
        )
        protected = self.resolver._protected_manifest(plan)
        self.assertIn("app/modules/launch_utils.py", protected)
        self.assertIn(
            "app/venv/Lib/site-packages/gradio-4.40.0.dist-info/METADATA",
            protected,
        )
        self.assertFalse(
            plan.execution_manifest["base_interpreter_inspected"]
        )

    def test_pip_bootstrap_accepts_only_exact_dry_run_argv(self) -> None:
        run_id = "0123456789abcdef"
        report = (
            WORKSPACE
            / "Evidence"
            / "preflight"
            / "reports"
            / run_id
            / "pip-dry-run-report.json"
        )
        requirements = (
            WORKSPACE
            / "Evidence"
            / "preflight"
            / "temp"
            / run_id
            / "combined-requirements.txt"
        )
        valid = (
            "install",
            "--dry-run",
            "--ignore-installed",
            "--only-binary=:all:",
            "--report",
            os.fspath(report),
            "--index-url",
            "https://packages.example.invalid/simple",
            "-r",
            os.fspath(requirements),
        )
        self.assertEqual(
            (report, requirements),
            _validate_pip_argv(valid, APP),
        )
        invalid = (
            (*valid, "--upgrade"),
            ("uninstall", *valid[1:]),
            (*valid[:1], "--user", *valid[1:-1]),
            (*valid[:1], "--target", "Evidence/other", *valid[1:]),
            (*valid[:5], os.fspath(APP / "outside.json"), *valid[6:]),
            (*valid[:9], os.fspath(APP / "requirements.txt")),
            (
                *valid[:9],
                os.fspath(
                    WORKSPACE
                    / "Evidence"
                    / "preflight"
                    / "temp"
                    / "fedcba9876543210"
                    / "combined-requirements.txt"
                ),
            ),
            (*valid[:7], "https://user:secret@packages.example.invalid/simple", *valid[8:]),
            (*valid[:7], "https://packages.example.invalid/simple?token=x", *valid[8:]),
            (*valid[:7], "http://packages.example.invalid/simple", *valid[8:]),
        )
        for argv in invalid:
            with self.subTest(argv=argv):
                with self.assertRaises(RuntimeError):
                    _validate_pip_argv(argv, APP)

    def test_plan_and_report_context_reject_invalid_policy(self) -> None:
        policy = self._filesystem_policy()
        invalid_policy = {
            **policy,
            "outside_access_allowed": True,
        }
        scope = ResolverScope(
            index_url="https://owner-approved.invalid/simple",
            allowed_endpoints=("owner-approved.invalid",),
            redirect_risk_acknowledged=True,
            target_environment=self._marker_environment(),
            filesystem_policy=policy,
        )
        with self.assertRaises(ResolveAuthorizationError):
            self.resolver.build_plan(
                scope=scope,
                local_dependency_decision={
                    "decision": "GO",
                    "reason_code": "TEST_LOCAL_GATE",
                },
                filesystem_policy=invalid_policy,
            )
        with self.assertRaises(ResolverExecutionError):
            PipReportContext(
                report_path=Path("unused"),
                plan_digest="sha256:" + ("a" * 64),
                coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
                requirements_text="",
                launcher_requirements=(),
                allowed_endpoints=("owner-approved.invalid",),
                expected_environment=self._marker_environment(),
                filesystem_policy=invalid_policy,
            )

    def test_process_runner_bounds_output_without_shell(self) -> None:
        process = FakeProcess(
            stdout_chunks=("abcdef", "gh"),
            stderr_chunks=("12345",),
        )
        with mock.patch(
            "scripts.preflight.resolver.subprocess.Popen",
            return_value=process,
        ) as popen:
            result = ProcessRunner().run(
                ("app/venv/Scripts/python.exe", "--future-dry-run"),
                cwd=WORKSPACE / "Evidence" / "preflight" / "temp",
                environment={"PIP_NO_INPUT": "1"},
                timeout_seconds=60,
                max_output_chars=4,
            )
        self.assertEqual("abcd", result.stdout)
        self.assertEqual("1234", result.stderr)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertFalse(result.timed_out)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_pip_structured_report_is_consumed_as_candidate_only(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/files/candidate.whl"
                    ),
                    "metadata": {
                        "name": "candidate",
                        "version": "1.0",
                        "requires_dist": [
                            "Pillow<15; python_version >= '3.13'",
                            "Pillow>=12",
                        ],
                    },
                },
                {
                    "requested": False,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/files/pillow.whl"
                    ),
                    "metadata": {
                        "name": "Pillow",
                        "version": "12.3.0",
                        "requires_dist": [],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("b" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="candidate==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual(
            ReportCoverage.SELECTED_CANDIDATE_SET.value,
            result["coverage"],
        )
        self.assertEqual(
            self._filesystem_policy(),
            result["filesystem_policy"],
        )
        self.assertEqual("GO", result["decision"])
        self.assertIsNone(result["selected_replacement"])
        self.assertIsNone(result["recommended_replacement"])
        self.assertEqual("candidate", result["observed_report_candidates"][0]["name"])
        self.assertTrue(
            any(
                "python_version" in item["requirement"]
                for item in result["constraints"]
            )
        )
        canonical = result["canonical_constraints"]
        self.assertTrue(canonical)
        self.assertTrue(
            all("raw_requirement" in item for item in canonical)
        )
        self.assertTrue(all(isinstance(item["owner"], dict) for item in canonical))
        marker_constraint = next(
            item
            for item in canonical
            if "python_version" in item["raw_requirement"]
        )
        self.assertEqual(
            "install[0].metadata.requires_dist[0]",
            marker_constraint["source"]["field"],
        )

    def test_selected_candidate_outside_constraints_is_no_go(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/pillow.whl"
                    ),
                    "metadata": {
                        "name": "Pillow",
                        "version": "12.3.0",
                        "requires_dist": [],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("c" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="Pillow>=8,<11\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("NO_GO", result["decision"])
        self.assertEqual(
            "DEPENDENCY_SELECTED_VERSION_OUTSIDE_CONSTRAINTS",
            result["reason_code"],
        )
        self.assertEqual(
            "NOT_PROVEN_EMPTY",
            result["dependency_decision"]["normalized_intersection"],
        )

    def test_missing_marker_environment_is_inconclusive(self) -> None:
        fixture = {
            "version": "1",
            "environment": {
                "implementation_name": "cpython",
                "python_version": "3.13",
            },
            "install": [],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("d" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_MARKER_ENVIRONMENT_INCOMPLETE",
            result["reason_code"],
        )

    def test_marker_environment_mismatch_is_inconclusive(self) -> None:
        observed_environment = {
            **self._marker_environment(),
            "python_version": "3.12",
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("d" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(
                {
                    "version": "1",
                    "environment": observed_environment,
                    "install": [],
                }
            ),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_MARKER_ENVIRONMENT_PLAN_MISMATCH",
            result["reason_code"],
        )

    def test_unsupported_report_version_is_rejected(self) -> None:
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("d" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        with self.assertRaises(ResolverExecutionError) as raised:
            consume_pip_report(
                InMemoryReportFS(
                    {
                        "version": "2",
                        "environment": self._marker_environment(),
                        "install": [],
                    }
                ),
                context=context,
            )
        self.assertEqual(
            "PIP_REPORT_VERSION_UNSUPPORTED",
            str(raised.exception),
        )

    def test_missing_artifact_sha_is_inconclusive(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": {
                        "url": "https://packages.example.invalid/root.whl"
                    },
                    "metadata": {
                        "name": "root",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("d" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="root==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_ARCHIVE_SHA256_MISSING",
            result["reason_code"],
        )

    def test_direct_url_inputs_and_report_constraints_are_rejected(self) -> None:
        with self.assertRaises(ResolveAuthorizationError) as raised:
            _validate_resolver_inputs(
                "root @ https://packages.example.invalid/root.whl\n",
                (),
            )
        self.assertEqual(
            "RESOLVE_DIRECT_URL_REQUIREMENT_UNSUPPORTED:requirements.txt:1",
            str(raised.exception),
        )

        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/root.whl"
                    ),
                    "metadata": {
                        "name": "root",
                        "version": "1.0",
                        "requires_dist": [
                            (
                                "child @ "
                                "https://packages.example.invalid/child.whl"
                            )
                        ],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("d" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="root==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_DIRECT_URL_REQUIREMENT_UNSUPPORTED",
            result["reason_code"],
        )

    def test_download_endpoint_is_a_post_hoc_no_go_gate(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://unapproved.example.invalid/pkg.whl"
                    ),
                    "metadata": {
                        "name": "candidate",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("e" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="candidate==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("NO_GO", result["decision"])
        self.assertEqual(
            "PIP_REPORT_DOWNLOAD_ENDPOINT_OUTSIDE_DECLARED_SCOPE",
            result["reason_code"],
        )
        self.assertEqual(
            "post-hoc-report-audit-only",
            result["endpoint_scope_enforcement"],
        )

    def test_download_url_private_suffix_is_no_go_and_not_retained(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/candidate.whl"
                        "?signed=private-value"
                    ),
                    "metadata": {
                        "name": "candidate",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("e" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="candidate==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("NO_GO", result["decision"])
        self.assertEqual(
            "PIP_REPORT_DOWNLOAD_URL_PRIVATE_SUFFIX_PRESENT",
            result["reason_code"],
        )
        self.assertNotIn(
            "private-value",
            json.dumps(result, sort_keys=True),
        )

    def test_unjustified_selected_candidate_is_inconclusive(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/root.whl"
                    ),
                    "metadata": {
                        "name": "root",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                },
                {
                    "requested": False,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/orphan.whl"
                    ),
                    "metadata": {
                        "name": "orphan",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                },
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("f" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="root==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_UNJUSTIFIED_SELECTED_CANDIDATE",
            result["reason_code"],
        )

    def test_orphan_dependency_cycle_is_inconclusive(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/root.whl"
                    ),
                    "metadata": {
                        "name": "root",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                },
                {
                    "requested": False,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/orphan-a.whl"
                    ),
                    "metadata": {
                        "name": "orphan-a",
                        "version": "1.0",
                        "requires_dist": ["orphan-b==1.0"],
                    },
                },
                {
                    "requested": False,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/orphan-b.whl"
                    ),
                    "metadata": {
                        "name": "orphan-b",
                        "version": "1.0",
                        "requires_dist": ["orphan-a==1.0"],
                    },
                },
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("f" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="root==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("INCONCLUSIVE", result["decision"])
        self.assertEqual(
            "PIP_REPORT_UNJUSTIFIED_SELECTED_CANDIDATE",
            result["reason_code"],
        )

    def test_selected_extras_are_evaluated_per_parent(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/parent.whl"
                    ),
                    "metadata": {
                        "name": "parent",
                        "version": "1.0",
                        "requires_dist": [
                            "child>=1; extra == 'feature'",
                            "unused>=1; extra == 'other'",
                        ],
                    },
                },
                {
                    "requested": False,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/child.whl"
                    ),
                    "metadata": {
                        "name": "child",
                        "version": "1.0",
                        "requires_dist": [],
                    },
                },
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("1" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text="parent[feature]==1.0\n",
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("GO", result["decision"])
        self.assertEqual(["feature"], result["selected_extras"]["parent"])
        optional = next(
            item
            for item in result["canonical_constraints"]
            if item["normalized_dependency"] == "unused"
        )
        self.assertFalse(optional["mandatory"])

    def test_inactive_root_marker_does_not_seed_extras(self) -> None:
        fixture = {
            "version": "1",
            "environment": self._marker_environment(),
            "install": [
                {
                    "requested": True,
                    "download_info": self._download_info(
                        "https://packages.example.invalid/parent.whl"
                    ),
                    "metadata": {
                        "name": "parent",
                        "version": "1.0",
                        "requires_dist": [
                            "child>=1; extra == 'feature'",
                        ],
                    },
                }
            ],
        }
        context = PipReportContext(
            report_path=Path("unused"),
            plan_digest="sha256:" + ("2" * 64),
            coverage=ReportCoverage.SELECTED_CANDIDATE_SET,
            requirements_text=(
                "parent[feature]==1.0; sys_platform == 'linux'\n"
                "parent==1.0\n"
            ),
            launcher_requirements=(),
            allowed_endpoints=("packages.example.invalid",),
            expected_environment=self._marker_environment(),
            filesystem_policy=self._filesystem_policy(),
        )
        result = consume_pip_report(
            InMemoryReportFS(fixture),
            context=context,
        )
        self.assertEqual("GO", result["decision"])
        self.assertNotIn("parent", result["selected_extras"])
        optional = next(
            item
            for item in result["canonical_constraints"]
            if item["normalized_dependency"] == "child"
        )
        self.assertFalse(optional["mandatory"])

    def test_execute_api_requires_authorization_path(self) -> None:
        parameters = inspect.signature(
            Resolver.execute_authorized_dry_run
        ).parameters
        self.assertIn("authorization_path", parameters)
        self.assertNotIn("authorization", parameters)

    def _filesystem_policy(self) -> dict[str, object]:
        return FilesystemPolicyContract.current(self.fs).to_dict()

    @staticmethod
    def _download_info(url: str) -> dict[str, object]:
        return {
            "url": url,
            "archive_info": {
                "hashes": {
                    "sha256": (
                        "0123456789abcdef"
                        "0123456789abcdef"
                        "0123456789abcdef"
                        "0123456789abcdef"
                    )
                }
            },
        }

    @staticmethod
    def _marker_environment() -> dict[str, str]:
        return {
            "implementation_name": "cpython",
            "implementation_version": "3.13.5",
            "os_name": "nt",
            "platform_machine": "AMD64",
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": "Windows",
            "platform_version": "",
            "python_full_version": "3.13.5",
            "python_version": "3.13",
            "sys_platform": "win32",
        }


if __name__ == "__main__":
    unittest.main()
