from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.contracts import FilesystemPolicyContract
from scripts.preflight.orchestrator import ModeResult
from scripts.preflight.reports import (
    REPORT_JSON_MAX_CHARS,
    ReportError,
    ReportRenderer,
    VerificationRenderer,
    sanitize_structure,
    verification_failure_result,
    verify_report_pair,
)


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]


class InMemoryFS(WorkspaceFS):
    def __init__(self, values, *, policy_bytes=None):
        super().__init__(WorkspaceBoundary(WORKSPACE))
        self.values = dict(values)
        self.policy_bytes = policy_bytes

    def read_text(self, path, *, max_chars=None):
        key = str(path)
        if key not in self.values:
            return super().read_text(path, max_chars=max_chars)
        value = self.values[key]
        if max_chars is not None and len(value) > max_chars:
            raise ValueError("TEXT_INPUT_EXCEEDS_SIZE_LIMIT")
        return value

    def read_bytes(self, path, *, max_bytes=None):
        normalized = str(path).replace("\\", "/")
        if (
            self.policy_bytes is not None
            and normalized.endswith("/app/AGENTS.md")
        ):
            return self.policy_bytes
        return super().read_bytes(path, max_bytes=max_bytes)

    def mkdir(self, path):
        return self.boundary.authorize(path, "in-memory-mkdir")

    def write_text(self, path, content, encoding="utf-8"):
        target = self.boundary.authorize(path, "in-memory-write")
        self.values[str(target)] = content
        return target

    def replace(self, source, destination):
        checked_source = self.boundary.authorize(
            source,
            "in-memory-replace-source",
        )
        checked_destination = self.boundary.authorize(
            destination,
            "in-memory-replace-destination",
        )
        self.values[str(checked_destination)] = self.values.pop(
            str(checked_source)
        )
        return checked_destination


def current_policy():
    return FilesystemPolicyContract.current(
        WorkspaceFS(WorkspaceBoundary(WORKSPACE))
    ).to_dict()


def sample_result(decision="NO_GO"):
    return {
        "schema_version": "forge-preflight-report/v2",
        "mode": "STATIC",
        "decision": decision,
        "contract_decision": decision,
        "effective_decision": decision,
        "reason_code": "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY",
        "summary": "The mandatory Pillow intersection is empty.",
        "runtime": {
            "python": {
                "path": "app/venv/Scripts/python.exe",
                "version": "3.13.5",
                "base_interpreter_inspected": False,
            },
            "command_processor": {"discovery_performed": False},
        },
        "inputs": [],
        "installed": {},
        "checks": [],
        "capabilities": {
            "filesystem_roots": [
                "app/",
                "Evidence/",
                "Reference/",
            ],
            "private_local_access": False,
            "outside_filesystem_access": False,
            "network_access": False,
            "package_mutation": False,
            "application_launch": False,
            "model_loading": False,
            "generation": False,
        },
        "privacy": {
            "absolute_paths_in_report": False,
            "environment_dump_performed": False,
            "secrets_collected": False,
        },
        "filesystem_policy": current_policy(),
        "dependency_decision": {
            "decision": decision,
            "reason_code": "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY",
            "normalized_intersection": "EMPTY",
            "constraints": [
                {
                    "owner": "gradio 4.40.0",
                    "requirement": "pillow>=8.0,<11.0",
                    "source": {
                        "path": "app/venv/Lib/site-packages/gradio-4.40.0.dist-info/METADATA"
                    },
                }
            ],
        },
    }


class ReportRendererTests(unittest.TestCase):
    def test_json_and_markdown_are_views_of_one_result(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        json_text = renderer.render_json(prepared)
        markdown_text = renderer.render_markdown(prepared)
        verification = verify_report_pair(
            InMemoryFS({"report.json": json_text, "report.md": markdown_text}),
            "report.json",
            "report.md",
        )
        payload = json.loads(json_text)
        self.assertEqual("PASS", verification["verification_status"])
        self.assertEqual("NO_GO", verification["contract_decision"])
        self.assertEqual("NO_GO", verification["effective_decision"])
        self.assertNotIn("decision", verification)
        self.assertEqual(
            payload["report_id"],
            verification["source_report_id"],
        )
        self.assertEqual(
            payload["decision"], verification["contract_decision"]
        )
        self.assertEqual(
            payload["reason_code"], verification["contract_reason_code"]
        )

    def test_verify_decision_matrix_and_rendered_views(self) -> None:
        matrix = (
            ("GO", 0),
            ("GO_WITH_WARNINGS", 1),
            ("NO_GO", 2),
        )
        for decision, exit_code in matrix:
            with self.subTest(decision=decision):
                report_renderer = ReportRenderer()
                report = report_renderer.prepare(sample_result(decision))
                verification = verify_report_pair(
                    InMemoryFS(
                        {
                            "report.json": report_renderer.render_json(report),
                            "report.md": report_renderer.render_markdown(report),
                        }
                    ),
                    "report.json",
                    "report.md",
                )
                verification_renderer = VerificationRenderer()
                prepared = verification_renderer.prepare(verification)
                rendered_json = verification_renderer.render_json(prepared)
                rendered_markdown = verification_renderer.render_markdown(
                    prepared
                )
                self.assertEqual("PASS", verification["verification_status"])
                self.assertEqual(decision, verification["contract_decision"])
                self.assertEqual(decision, verification["effective_decision"])
                self.assertNotIn("decision", verification)
                self.assertEqual(
                    decision,
                    json.loads(rendered_json)["effective_decision"],
                )
                self.assertIn(
                    f"- Effective decision: `{decision}`",
                    rendered_markdown,
                )
                mode_result = ModeResult(
                    mode="VERIFY",
                    decision=decision,
                    reason_code=verification["verification_reason"],
                    payload=verification,
                )
                self.assertEqual(exit_code, mode_result.exit_code)
                if decision == "NO_GO":
                    self.assertIn(
                        (
                            "Verification passed, but launch remains blocked "
                            "by the contract decision."
                        ),
                        rendered_markdown,
                    )

    def test_verification_failure_forces_no_go(self) -> None:
        failure = verification_failure_result(
            verification_reason="CONTRACT_HASH_MISMATCH",
            filesystem_policy=current_policy(),
        )
        renderer = VerificationRenderer()
        prepared = renderer.prepare(failure)
        rendered_json = json.loads(renderer.render_json(prepared))
        rendered_markdown = renderer.render_markdown(prepared)
        self.assertEqual("FAIL", rendered_json["verification_status"])
        self.assertIsNone(rendered_json["contract_decision"])
        self.assertEqual("NO_GO", rendered_json["effective_decision"])
        self.assertNotIn("decision", rendered_json)
        self.assertIn(
            "- Effective decision: `NO_GO`",
            rendered_markdown,
        )
        result = ModeResult(
            mode="VERIFY",
            decision="GO",
            reason_code="CONTRACT_HASH_MISMATCH",
            payload=failure,
        )
        self.assertEqual(2, result.exit_code)

    def test_verification_renderer_rejects_illegal_decision_models(
        self,
    ) -> None:
        valid_pass = {
            "schema_version": "forge-preflight-verification/v2",
            "mode": "VERIFY",
            "verification_status": "PASS",
            "contract_decision": "NO_GO",
            "effective_decision": "NO_GO",
            "verification_reason": "REPORTS_SEMANTICALLY_AGREE",
            "source_report_id": "sha256:" + ("a" * 64),
            "source_mode": "STATIC",
            "contract_reason_code": "TEST_CONTRACT",
            "filesystem_policy": current_policy(),
            "network_access": False,
            "command_processor_discovery": False,
            "package_mutation": False,
            "application_launch": False,
            "model_loading": False,
            "generation": False,
        }
        invalid = (
            {**valid_pass, "decision": "GO"},
            {**valid_pass, "contract_decision": None},
            {**valid_pass, "contract_decision": "INCONCLUSIVE"},
            {**valid_pass, "effective_decision": "GO"},
            {**valid_pass, "verification_status": "UNKNOWN"},
            {**valid_pass, "network_access": True},
            {
                **verification_failure_result(
                    verification_reason="CONTRACT_SCHEMA_INVALID",
                    filesystem_policy=current_policy(),
                ),
                "contract_decision": "GO",
            },
        )
        renderer = VerificationRenderer()
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ReportError):
                    renderer.prepare(value)
        mismatched = ModeResult(
            mode="VERIFY",
            decision="GO",
            reason_code="TEST",
            payload={
                "verification_status": "PASS",
                "contract_decision": "NO_GO",
                "effective_decision": "GO",
            },
        )
        self.assertEqual(2, mismatched.exit_code)

    def test_orchestrated_tamper_writes_fail_closed_verification_pair(
        self,
    ) -> None:
        from scripts.preflight.orchestrator import PreflightOrchestrator

        report_renderer = ReportRenderer()
        report = report_renderer.prepare(sample_result("GO"))
        json_path = (
            WORKSPACE
            / "Evidence"
            / "preflight"
            / "reports"
            / "tamper-matrix.json"
        )
        markdown_path = json_path.with_suffix(".md")
        values = {
            str(json_path): report_renderer.render_json(report),
            str(markdown_path): report_renderer.render_markdown(report).replace(
                "- Decision: `GO`",
                "- Decision: `NO_GO`",
                1,
            ),
        }
        orchestrator = PreflightOrchestrator()
        orchestrator.fs = InMemoryFS(values)
        result = orchestrator.run_verify(
            json_path=json_path,
            markdown_path=markdown_path,
        )
        self.assertEqual("FAIL", result.payload["verification_status"])
        self.assertIsNone(result.payload["contract_decision"])
        self.assertEqual("NO_GO", result.payload["effective_decision"])
        self.assertEqual(2, result.exit_code)
        rendered_json = json.loads(
            orchestrator.fs.values[
                str(json_path.with_name("tamper-matrix.verification.json"))
            ]
        )
        rendered_markdown = orchestrator.fs.values[
            str(
                markdown_path.with_name(
                    "tamper-matrix.verification.md"
                )
            )
        ]
        self.assertEqual("FAIL", rendered_json["verification_status"])
        self.assertEqual("NO_GO", rendered_json["effective_decision"])
        self.assertIn(
            "- Effective decision: `NO_GO`",
            rendered_markdown,
        )

    def test_verify_rejects_policy_tampering_and_current_policy_drift(
        self,
    ) -> None:
        mutations = (
            ("filesystem_policy_version", "forge-filesystem-policy/v0"),
            ("normalized_permitted_roots", ["app/", "Reference/", "Evidence/"]),
            ("private_local_status", "OWNER_AUTHORIZED"),
            ("policy_source", "app/OTHER_POLICY.md"),
            ("outside_access_allowed", True),
            ("policy_source_sha256", "sha256:" + ("0" * 64)),
        )
        renderer = ReportRenderer()
        for field, value in mutations:
            with self.subTest(field=field):
                result = sample_result()
                result["filesystem_policy"][field] = value
                prepared = renderer.prepare(result)
                with self.assertRaises(ReportError):
                    verify_report_pair(
                        InMemoryFS(
                            {
                                "report.json": renderer.render_json(prepared),
                                "report.md": renderer.render_markdown(prepared),
                            }
                        ),
                        "report.json",
                        "report.md",
                    )

        prepared = renderer.prepare(sample_result())
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": renderer.render_json(prepared),
                        "report.md": renderer.render_markdown(prepared),
                    },
                    policy_bytes=b"changed policy bytes",
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual("CURRENT_STATE_DRIFT", str(raised.exception))

    def test_verify_rejects_unapproved_referenced_path_and_open_decision(
        self,
    ) -> None:
        renderer = ReportRenderer()
        outside = sample_result()
        outside["inputs"] = ["../outside-workspace"]
        unsupported = sample_result()
        unsupported["decision"] = "INCONCLUSIVE"
        unsupported["contract_decision"] = "INCONCLUSIVE"
        unsupported["effective_decision"] = "INCONCLUSIVE"
        unknown_field = sample_result()
        unknown_field["unexpected"] = "not in the report schema"
        private_access = sample_result()
        private_access["capabilities"]["private_local_access"] = True
        outside_access = sample_result()
        outside_access["capabilities"]["outside_filesystem_access"] = True
        changed_roots = sample_result()
        changed_roots["capabilities"]["filesystem_roots"] = [
            "app/",
            "Evidence/",
        ]
        for result in (
            outside,
            unsupported,
            unknown_field,
            private_access,
            outside_access,
            changed_roots,
        ):
            with self.subTest(result=result):
                prepared = renderer.prepare(result)
                with self.assertRaises(ReportError):
                    verify_report_pair(
                        InMemoryFS(
                            {
                                "report.json": renderer.render_json(prepared),
                                "report.md": renderer.render_markdown(prepared),
                            }
                        ),
                        "report.json",
                        "report.md",
                    )

    def test_verify_rejects_semantic_drift(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        json_text = renderer.render_json(prepared)
        canonical_markdown = renderer.render_markdown(prepared)
        tampered_views = (
            canonical_markdown.replace(
                "- Decision: `NO_GO`", "- Decision: `GO`", 1
            ),
            canonical_markdown.replace(
                "The mandatory Pillow intersection is empty.",
                "A tampered summary.",
            ),
            canonical_markdown.replace(
                "`pillow&gt;=8.0,&lt;11.0`", "`pillow&gt;=12`"
            ),
            canonical_markdown.replace(
                "- Network access performed: `false`",
                "- Network access performed: `true`",
            ),
        )
        for markdown_text in tampered_views:
            with self.subTest(markdown=markdown_text):
                with self.assertRaises(ReportError):
                    verify_report_pair(
                        InMemoryFS(
                            {
                                "report.json": json_text,
                                "report.md": markdown_text,
                            }
                        ),
                        "report.json",
                        "report.md",
                    )

    def test_paths_credentials_and_secret_fields_are_sanitized(self) -> None:
        unsafe = {
            "path": "C:\\Users\\Example Person\\private\\file.txt and suffix",
            "url": (
                "https://person:password@example.invalid/file"
                "?access_token=abc123#private"
            ),
            "message": "Bearer abc.def.ghi",
            "client_secret": "top-secret",
        }
        serialized = json.dumps(sanitize_structure(unsafe))
        self.assertNotIn("Users", serialized)
        self.assertNotIn("Example Person", serialized)
        self.assertNotIn("abc123", serialized)
        self.assertNotIn("/file", serialized)
        self.assertNotIn("abc.def.ghi", serialized)
        self.assertNotIn("top-secret", serialized)
        self.assertIn("REDACTED", serialized)

    def test_verify_uses_strict_parser_for_duplicate_keys(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        json_text = renderer.render_json(prepared)
        duplicate = json_text.replace(
            "{\n",
            '{\n  "summary": "discarded duplicate",\n',
            1,
        )
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": duplicate,
                        "report.md": renderer.render_markdown(prepared),
                    }
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual(
            "REPORT_JSON_DUPLICATE_KEY_REJECTED",
            str(raised.exception),
        )

    def test_duplicate_cannot_hide_private_path_or_secret(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        json_text = renderer.render_json(prepared)
        hidden_value = json.dumps(
            {
                "client_secret": "should-never-survive",
                "private_path": "C:\\Users\\Example Person\\private.txt",
            },
            sort_keys=True,
        )
        duplicate = json_text.replace(
            "{\n",
            f'{{\n  "summary": {hidden_value},\n',
            1,
        )
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": duplicate,
                        "report.md": renderer.render_markdown(prepared),
                    }
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual(
            "REPORT_JSON_DUPLICATE_KEY_REJECTED",
            str(raised.exception),
        )

    def test_verify_rejects_oversized_json_before_parsing(self) -> None:
        oversized = '{"padding":"' + ("x" * REPORT_JSON_MAX_CHARS) + '"}'
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": oversized,
                        "report.md": "",
                    }
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual(
            "REPORT_JSON_EXCEEDS_SIZE_LIMIT",
            str(raised.exception),
        )

    def test_verify_rejects_noncanonical_json_view(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        noncanonical_json = json.dumps(prepared, sort_keys=True) + "\n"
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": noncanonical_json,
                        "report.md": renderer.render_markdown(prepared),
                    }
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual(
            "REPORT_JSON_CANONICAL_VIEW_MISMATCH",
            str(raised.exception),
        )

    def test_verify_rejects_nonfinite_json_numbers(self) -> None:
        renderer = ReportRenderer()
        prepared = renderer.prepare(sample_result())
        json_text = renderer.render_json(prepared)
        nonfinite = json_text.replace(
            '"summary": "The mandatory Pillow intersection is empty."',
            '"summary": "The mandatory Pillow intersection is empty.",'
            '\n  "unknown_numeric_field": NaN',
            1,
        )
        with self.assertRaises(ReportError) as raised:
            verify_report_pair(
                InMemoryFS(
                    {
                        "report.json": nonfinite,
                        "report.md": renderer.render_markdown(prepared),
                    }
                ),
                "report.json",
                "report.md",
            )
        self.assertEqual("REPORT_JSON_INVALID", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
