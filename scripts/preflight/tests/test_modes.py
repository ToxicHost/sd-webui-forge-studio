from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import site
import subprocess
import sys
import sysconfig
import unittest
from unittest import mock

from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.inspection import StaticInspector
from scripts.preflight import cli
from scripts.preflight.orchestrator import ModeResult, PreflightOrchestrator
from scripts.preflight.reports import ReportRenderer, verify_report_pair


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]
APP = WORKSPACE / "app"
PREFLIGHT = APP / "scripts" / "preflight"


class InMemoryFS(WorkspaceFS):
    def __init__(self, values):
        super().__init__(WorkspaceBoundary(WORKSPACE))
        self.values = dict(values)

    def read_text(self, path, *, max_chars=None):
        key = str(path)
        if key not in self.values:
            return super().read_text(path, max_chars=max_chars)
        value = self.values[key]
        if max_chars is not None and len(value) > max_chars:
            raise ValueError("TEXT_INPUT_EXCEEDS_SIZE_LIMIT")
        return value


class PreflightModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orchestrator = PreflightOrchestrator()

    def test_static_mode_uses_existing_workspace_venv(self) -> None:
        result = self.orchestrator.run_static(write_reports=False)
        self.assertEqual("STATIC", result.mode)
        self.assertEqual("app/venv/Scripts/python.exe", result.payload["runtime"]["python"]["path"])
        self.assertEqual("3.13.5", result.payload["runtime"]["python"]["version"])
        self.assertFalse(
            result.payload["runtime"]["python"]["base_interpreter_inspected"]
        )
        self.assertTrue(result.payload["runtime"]["python"]["isolated"])
        self.assertFalse(
            result.payload["runtime"]["python"]["automatic_site_processing"]
        )
        self.assertFalse(result.payload["runtime"]["python"]["bytecode_writes"])
        self.assertFalse(
            result.payload["runtime"]["command_processor"]["discovery_performed"]
        )

    def test_static_fails_closed_for_non_workspace_python_without_discovery(self) -> None:
        with mock.patch(
            "scripts.preflight.inspection.sys.executable",
            "Z:\\outside-preflight-test\\python.exe",
        ):
            result = self.orchestrator.run_static(write_reports=False)
        self.assertEqual("NO_GO", result.decision)
        self.assertEqual("WORKSPACE_VENV_PYTHON_REQUIRED", result.reason_code)
        self.assertEqual(["app/AGENTS.md"], result.payload["inputs"])

    def test_static_fails_closed_when_workspace_python_is_missing(self) -> None:
        original_is_file = self.orchestrator.fs.is_file

        def controlled_is_file(path):
            if str(path).replace("\\", "/").endswith(
                "app/venv/Scripts/python.exe"
            ):
                return False
            return original_is_file(path)

        with mock.patch.object(
            self.orchestrator.fs,
            "is_file",
            side_effect=controlled_is_file,
        ):
            result = self.orchestrator.run_static(write_reports=False)
        self.assertEqual("NO_GO", result.decision)
        self.assertEqual("WORKSPACE_VENV_PYTHON_REQUIRED", result.reason_code)

    def test_contract_mode_requires_no_command_processor(self) -> None:
        result = self.orchestrator.run_contract(write_reports=False)
        self.assertFalse(
            result.payload["runtime"]["command_processor"]["discovery_performed"]
        )
        self.assertEqual(
            "OWNER_APPROVAL_REQUIRED",
            result.payload["contract"]["command_processor"]["verification_status"],
        )

    def test_resolve_mode_remains_unconditionally_disabled(self) -> None:
        result = self.orchestrator.run_resolve(authorization_path=None)
        self.assertEqual("NO_GO", result.decision)
        self.assertEqual(
            "RESOLVE_EXECUTION_DISABLED_STAGE_E0",
            result.reason_code,
        )
        self.assertEqual("NO_GO", result.effective_decision)
        self.assertEqual(2, result.exit_code)
        for field in (
            "network_access",
            "process_executed",
            "package_mutation",
            "application_launch",
            "model_loading",
            "generation",
        ):
            self.assertIs(False, result.payload[field])

    def test_resolve_plan_cli_emits_owner_review_payload(self) -> None:
        expected_payload = {
            "mode": "RESOLVE_PLAN",
            "plan_status": "PLAN_READY",
            "authorization_status": "NETWORK_NOT_AUTHORIZED",
            "authorization_eligible": False,
            "authorization_blockers": [
                "RESOLVE_PLAN_DIRTY_WORKTREE"
            ],
            "decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": "NETWORK_NOT_AUTHORIZED",
            "plan": {
                "plan_digest": "sha256:" + ("a" * 64),
            },
            "network_access": False,
            "process_executed": False,
            "package_mutation": False,
        }
        result = ModeResult(
            mode="RESOLVE_PLAN",
            decision="NO_GO",
            reason_code="NETWORK_NOT_AUTHORIZED",
            payload=expected_payload,
        )
        fake_orchestrator = mock.Mock()
        fake_orchestrator.run_resolve_plan.return_value = result
        output = io.StringIO()
        with mock.patch.object(
            cli,
            "PreflightOrchestrator",
            return_value=fake_orchestrator,
        ), redirect_stdout(output):
            exit_code = cli.main(
                [
                    "resolve-plan",
                    "--repository-root",
                    ".",
                    "--evidence-root",
                    "..\\Evidence\\preflight",
                    "--explicit-version",
                    "4.40.0",
                    "--host",
                    "packages.example.invalid",
                    "--index-url",
                    "https://packages.example.invalid/simple",
                    "--operation",
                    "CANDIDATE_METADATA",
                ]
            )
        self.assertEqual(2, exit_code)
        rendered = json.loads(output.getvalue())
        self.assertEqual(expected_payload, rendered["payload"])
        self.assertEqual("PLAN_READY", rendered["payload"]["plan_status"])
        self.assertEqual(
            "NETWORK_NOT_AUTHORIZED",
            rendered["payload"]["authorization_status"],
        )
        self.assertIs(
            False,
            rendered["payload"]["authorization_eligible"],
        )
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            rendered["payload"]["authorization_blockers"],
        )
        self.assertEqual(
            "sha256:" + ("a" * 64),
            rendered["payload"]["plan"]["plan_digest"],
        )
        fake_orchestrator.run_resolve_plan.assert_called_once_with(
            repository_root=".",
            evidence_root="..\\Evidence\\preflight",
            contract_path=None,
            candidate_scope={
                "package": "gradio",
                "explicit_versions": ["4.40.0"],
            },
            requested_indexes=(
                "https://packages.example.invalid/simple",
            ),
            requested_hosts=("packages.example.invalid",),
            approved_operations=("CANDIDATE_METADATA",),
        )

    def test_verify_cli_exit_matrix_preserves_contract_decision(self) -> None:
        matrix = (
            ("PASS", "GO", "GO", 0),
            ("PASS", "GO_WITH_WARNINGS", "GO_WITH_WARNINGS", 1),
            ("PASS", "NO_GO", "NO_GO", 2),
            ("FAIL", None, "NO_GO", 2),
        )
        for status, contract_decision, effective_decision, expected in matrix:
            with self.subTest(
                status=status,
                contract_decision=contract_decision,
            ):
                payload = {
                    "verification_status": status,
                    "contract_decision": contract_decision,
                    "effective_decision": effective_decision,
                    "verification_reason": "TEST_VERIFICATION",
                }
                result = ModeResult(
                    mode="VERIFY",
                    decision=effective_decision,
                    reason_code="TEST_VERIFICATION",
                    payload=payload,
                    json_report=(
                        "Evidence/preflight/reports/test.verification.json"
                    ),
                    markdown_report=(
                        "Evidence/preflight/reports/test.verification.md"
                    ),
                )
                fake_orchestrator = mock.Mock()
                fake_orchestrator.run_verify.return_value = result
                output = io.StringIO()
                with mock.patch.object(
                    cli,
                    "PreflightOrchestrator",
                    return_value=fake_orchestrator,
                ), redirect_stdout(output):
                    exit_code = cli.main(
                        [
                            "verify",
                            "--json",
                            "Evidence/preflight/reports/test.json",
                            "--markdown",
                            "Evidence/preflight/reports/test.md",
                        ]
                    )
                self.assertEqual(expected, exit_code)
                rendered = json.loads(output.getvalue())
                self.assertEqual(
                    effective_decision,
                    rendered["effective_decision"],
                )
                self.assertNotIn("decision", rendered)

    def test_static_contract_verify_and_rejected_resolve_do_not_mutate_product(self) -> None:
        protected = (
            APP / "requirements.txt",
            APP / "launch.py",
            APP / "webui.bat",
            APP
            / "venv"
            / "Lib"
            / "site-packages"
            / "gradio-4.40.0.dist-info"
            / "METADATA",
            APP
            / "venv"
            / "Lib"
            / "site-packages"
            / "pillow-12.3.0.dist-info"
            / "METADATA",
            APP
            / "venv"
            / "Lib"
            / "site-packages"
            / "pillow_heif-1.4.0.dist-info"
            / "METADATA",
        )
        fs = WorkspaceFS(WorkspaceBoundary(WORKSPACE))

        def hashes():
            return {
                fs.boundary.report_path(path): hashlib.sha256(
                    fs.read_text(path).encode("utf-8")
                ).hexdigest()
                for path in protected
            }

        before = hashes()
        forbidden_modules = {"gradio", "PIL", "torch", "launch", "webui"}
        before_modules = set(sys.modules)
        with mock.patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process execution occurred"),
        ) as run_process, mock.patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("process execution occurred"),
        ) as open_process, mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network socket created"),
        ) as create_socket:
            static = self.orchestrator.run_static(write_reports=False)
            self.orchestrator.run_contract(write_reports=False)
            prepared = ReportRenderer().prepare(static.payload)
            json_text = ReportRenderer.render_json(prepared)
            markdown_text = ReportRenderer.render_markdown(prepared)
            verify_report_pair(
                InMemoryFS(
                    {"report.json": json_text, "report.md": markdown_text}
                ),
                "report.json",
                "report.md",
            )
            resolve = self.orchestrator.run_resolve(
                authorization_path=None
            )
            self.assertEqual("NO_GO", resolve.decision)
            self.assertEqual(
                "RESOLVE_EXECUTION_DISABLED_STAGE_E0",
                resolve.reason_code,
            )
        run_process.assert_not_called()
        open_process.assert_not_called()
        create_socket.assert_not_called()
        newly_loaded = set(sys.modules) - before_modules
        self.assertFalse(forbidden_modules.intersection(newly_loaded))
        self.assertFalse(
            any(
                event.operation.startswith(
                    ("write", "mkdir", "replace", "create")
                )
                for event in self.orchestrator.boundary.events
            )
        )
        self.assertEqual(before, hashes())

    def test_runtime_modules_do_not_discover_base_python_or_command_processor(self) -> None:
        forbidden = (
            "base_prefix",
            "_base_executable",
            "pyvenv.cfg",
            "cmd.exe",
            "COMSPEC",
            "shutil.which",
            "get_exec_path",
        )
        modules = (
            "boundary.py",
            "bootstrap.py",
            "contracts.py",
            "constraints.py",
            "inspection.py",
            "pip_bootstrap.py",
            "reports.py",
            "resolver.py",
            "orchestrator.py",
            "cli.py",
        )
        fs = WorkspaceFS(WorkspaceBoundary(WORKSPACE))
        for module in modules:
            source = fs.read_text(PREFLIGHT / module)
            for token in forbidden:
                with self.subTest(module=module, token=token):
                    self.assertNotIn(token, source)

    def test_static_does_not_call_discovery_apis(self) -> None:
        with mock.patch.object(
            shutil,
            "which",
            side_effect=AssertionError("command discovery occurred"),
        ) as which, mock.patch.object(
            os,
            "get_exec_path",
            side_effect=AssertionError("PATH discovery occurred"),
        ) as get_exec_path, mock.patch.object(
            site,
            "getsitepackages",
            side_effect=AssertionError("site discovery occurred"),
        ) as getsitepackages, mock.patch.object(
            sysconfig,
            "get_paths",
            side_effect=AssertionError("base path discovery occurred"),
        ) as get_paths, mock.patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process execution occurred"),
        ) as run_process:
            self.orchestrator.run_static(write_reports=False)
        for probe in (
            which,
            get_exec_path,
            getsitepackages,
            get_paths,
            run_process,
        ):
            probe.assert_not_called()

    def test_all_static_filesystem_events_are_inside_approved_roots(self) -> None:
        self.orchestrator.run_static(write_reports=False)
        for event in self.orchestrator.boundary.events:
            if event.allowed:
                self.assertTrue(
                    event.path.startswith(
                        ("app/", "Reference/", "Evidence/")
                    )
                )
            else:
                self.assertEqual(
                    "filesystem-policy-denial-probe",
                    event.operation,
                )
                self.assertIn(
                    event.reason_code,
                    {
                        "PRIVATE_LOCAL_OWNER_AUTHORIZATION_REQUIRED",
                        "FILESYSTEM_PATH_OUTSIDE_WORKSPACE_BOUNDARY",
                    },
                )

    def test_static_report_contains_no_user_specific_absolute_path(self) -> None:
        result = self.orchestrator.run_static(write_reports=False)
        prepared = ReportRenderer().prepare(result.payload)
        rendered = ReportRenderer.render_json(prepared)
        self.assertNotIn(str(WORKSPACE), rendered)
        self.assertNotIn("C:\\Users\\", rendered)

    def test_requirement_source_line_is_derived_from_input(self) -> None:
        parsed = StaticInspector._direct_requirements(
            "# header\nexample==1\nPillow==12.3.0\n"
        )
        requirement, line = StaticInspector._required_requirement(
            parsed, "pillow"
        )
        self.assertEqual("Pillow", requirement.name)
        self.assertEqual(3, line)

    def test_report_stem_traversal_is_rejected_before_write(self) -> None:
        with mock.patch.object(
            self.orchestrator.fs,
            "write_text",
            side_effect=AssertionError("report write occurred"),
        ) as write_text:
            with self.assertRaises(ValueError) as raised:
                self.orchestrator.run_static(
                    report_stem="..\\..\\..\\app\\blocked",
                    write_reports=True,
                )
        write_text.assert_not_called()
        self.assertEqual("REPORT_STEM_INVALID", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
