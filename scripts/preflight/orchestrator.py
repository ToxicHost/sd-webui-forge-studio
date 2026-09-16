"""Mode dispatcher for the Forge Preflight Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Any

from .boundary import WorkspaceBoundary, WorkspaceFS
from .contracts import FilesystemPolicyContract, load_contract
from .inspection import StaticInspector
from .reports import (
    ReportError,
    ReportRenderer,
    VerificationRenderer,
    verification_failure_result,
    verify_report_pair,
)
from .resolve_authorization import (
    AuthorizationValidationResult,
    ResolveAuthorizationValidationError,
    ResolveAuthorizationWorkflow,
    render_validation_json,
    render_validation_markdown,
)
from .resolver import ResolverPaths


@dataclass(frozen=True)
class ModeResult:
    mode: str
    decision: str
    reason_code: str
    payload: dict[str, Any]
    json_report: str | None = None
    markdown_report: str | None = None

    @property
    def effective_decision(self) -> str:
        return str(self.payload.get("effective_decision", self.decision))

    @property
    def exit_code(self) -> int:
        if self.mode == "VERIFY":
            verification_status = self.payload.get("verification_status")
            contract_decision = self.payload.get("contract_decision")
            effective_decision = self.payload.get("effective_decision")
            if (
                verification_status != "PASS"
                or contract_decision
                not in {"GO", "GO_WITH_WARNINGS", "NO_GO"}
                or effective_decision != contract_decision
            ):
                return 2
        return {
            "GO": 0,
            "GO_WITH_WARNINGS": 1,
            "NO_GO": 2,
        }.get(self.effective_decision, 2)


class PreflightOrchestrator:
    """Own mode capabilities and enforce the workspace boundary."""

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if not sys.flags.isolated or not sys.flags.no_site:
            raise RuntimeError("ISOLATED_NO_SITE_PYTHON_REQUIRED")
        if not sys.dont_write_bytecode:
            raise RuntimeError("BYTECODE_WRITES_MUST_BE_DISABLED")

        module_path = Path(os.path.abspath(__file__))
        anchored_workspace = module_path.parents[3]
        requested_workspace = (
            Path(os.path.abspath(os.fspath(workspace_root)))
            if workspace_root is not None
            else anchored_workspace
        )
        if os.path.normcase(os.path.normpath(os.fspath(requested_workspace))) != (
            os.path.normcase(os.path.normpath(os.fspath(anchored_workspace)))
        ):
            raise ValueError("WORKSPACE_ROOT_OVERRIDE_REJECTED")

        self.workspace_root = anchored_workspace
        self.app_root = self.workspace_root / "app"
        self.boundary = WorkspaceBoundary(self.workspace_root)
        self.fs = WorkspaceFS(self.boundary)
        self.resolver_paths = ResolverPaths.from_boundary(self.boundary)
        self.default_contract = (
            self.app_root
            / "scripts"
            / "preflight"
            / "fixtures"
            / "static-contract.json"
        )

    def run_static(
        self,
        *,
        contract_path: str | os.PathLike[str] | None = None,
        report_stem: str = "static-preflight",
        write_reports: bool = True,
    ) -> ModeResult:
        report_paths = (
            self._report_paths(report_stem) if write_reports else None
        )
        contract = load_contract(self.fs, self._contract_path(contract_path))
        payload = self._static_inspector().inspect(contract)
        json_report = None
        markdown_report = None
        if write_reports:
            assert report_paths is not None
            json_path, markdown_path = report_paths
            self.resolver_paths.ensure_report_directory(self.fs)
            prepared = ReportRenderer().write_bundle(
                self.fs,
                payload,
                os.fspath(json_path),
                os.fspath(markdown_path),
            )
            payload = prepared
            json_report = self.boundary.report_path(json_path)
            markdown_report = self.boundary.report_path(markdown_path)
        return ModeResult(
            mode="STATIC",
            decision=str(payload["decision"]),
            reason_code=str(payload["reason_code"]),
            payload=payload,
            json_report=json_report,
            markdown_report=markdown_report,
        )

    def run_contract(
        self,
        *,
        contract_path: str | os.PathLike[str] | None = None,
        write_reports: bool = False,
        report_stem: str = "contract-preflight",
    ) -> ModeResult:
        report_paths = (
            self._report_paths(report_stem) if write_reports else None
        )
        contract = load_contract(self.fs, self._contract_path(contract_path))
        payload = self._static_inspector().inspect(contract)
        payload = dict(payload)
        payload["mode"] = "CONTRACT"
        payload["contract"] = contract.to_dict()
        payload["checks"] = [
            *payload.get("checks", []),
            {
                "id": "command-processor-contract",
                "status": "PASS",
                "basis": "human-supplied sanitized identity only",
                "expected": "OWNER_APPROVAL_REQUIRED",
                "observed": contract.command_processor.verification_status,
            },
        ]

        json_report = None
        markdown_report = None
        if write_reports:
            assert report_paths is not None
            json_path, markdown_path = report_paths
            self.resolver_paths.ensure_report_directory(self.fs)
            payload = ReportRenderer().write_bundle(
                self.fs,
                payload,
                os.fspath(json_path),
                os.fspath(markdown_path),
            )
            json_report = self.boundary.report_path(json_path)
            markdown_report = self.boundary.report_path(markdown_path)
        return ModeResult(
            mode="CONTRACT",
            decision=str(payload["decision"]),
            reason_code=str(payload["reason_code"]),
            payload=payload,
            json_report=json_report,
            markdown_report=markdown_report,
        )

    def run_verify(
        self,
        *,
        json_path: str | os.PathLike[str],
        markdown_path: str | os.PathLike[str],
    ) -> ModeResult:
        checked_json, checked_markdown = self._verify_paths(
            json_path,
            markdown_path,
        )
        output_json, output_markdown = self._verification_report_paths(
            checked_json,
            checked_markdown,
        )
        try:
            payload = verify_report_pair(
                self.fs,
                os.fspath(checked_json),
                os.fspath(checked_markdown),
            )
        except ReportError as exc:
            payload = verification_failure_result(
                verification_reason=self._verification_reason(str(exc)),
                filesystem_policy=FilesystemPolicyContract.current(
                    self.fs
                ).to_dict(),
            )
        prepared = VerificationRenderer().write_bundle(
            self.fs,
            payload,
            os.fspath(output_json),
            os.fspath(output_markdown),
        )
        return ModeResult(
            mode="VERIFY",
            decision=str(prepared["effective_decision"]),
            reason_code=str(prepared["verification_reason"]),
            payload=prepared,
            json_report=self.boundary.report_path(output_json),
            markdown_report=self.boundary.report_path(output_markdown),
        )

    def run_resolve(
        self,
        *,
        authorization_path: str | os.PathLike[str] | None,
        contract_path: str | os.PathLike[str] | None = None,
    ) -> ModeResult:
        """Remain unconditionally non-executable during Stage E0."""

        del authorization_path, contract_path
        payload = {
            "schema_version": "forge-preflight-report/v2",
            "mode": "RESOLVE",
            "decision": "NO_GO",
            "contract_decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": "RESOLVE_EXECUTION_DISABLED_STAGE_E0",
            "summary": (
                "Executable Resolve remains disabled. Use ResolvePlan and "
                "validation-only authorization review instead."
            ),
            "network_access": False,
            "process_executed": False,
            "package_mutation": False,
            "application_launch": False,
            "model_loading": False,
            "generation": False,
        }
        return ModeResult(
            mode="RESOLVE",
            decision="NO_GO",
            reason_code="RESOLVE_EXECUTION_DISABLED_STAGE_E0",
            payload=payload,
        )

    def run_resolve_plan(
        self,
        *,
        repository_root: str | os.PathLike[str] = ".",
        evidence_root: str | os.PathLike[str] = "..\\Evidence\\preflight",
        contract_path: str | os.PathLike[str] | None = None,
        candidate_scope: dict[str, Any] | None = None,
        requested_indexes: tuple[str, ...] = (),
        requested_hosts: tuple[str, ...] = (),
        approved_operations: tuple[str, ...] = (),
    ) -> ModeResult:
        """Write a deterministic no-network review bundle."""

        static_result = self.run_static(
            contract_path=contract_path,
            write_reports=False,
        )
        dependency_decision = static_result.payload.get("dependency_decision")
        filesystem_policy = static_result.payload["filesystem_policy"]
        if not isinstance(dependency_decision, dict):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_DEPENDENCY_GATE_UNSUPPORTED"
            )
        checked_repository_root = self._app_relative_root(
            repository_root
        )
        checked_evidence_root = self._app_relative_root(evidence_root)
        workflow = ResolveAuthorizationWorkflow(
            self.fs,
            self.app_root,
            self.resolver_paths,
        )
        plan = workflow.create_plan(
            repository_root=checked_repository_root,
            evidence_root=checked_evidence_root,
            dependency_decision=dependency_decision,
            filesystem_policy=filesystem_policy,
            candidate_scope=candidate_scope,
            requested_indexes=requested_indexes,
            requested_hosts=requested_hosts,
            approved_operations=approved_operations,
        )
        payload = {
            "schema_version": "forge-preflight-report/v2",
            "mode": "RESOLVE_PLAN",
            "plan_status": "PLAN_READY",
            "authorization_status": "NETWORK_NOT_AUTHORIZED",
            "authorization_eligible": plan.logical_plan[
                "authorization_eligible"
            ],
            "authorization_blockers": list(
                plan.logical_plan["authorization_blockers"]
            ),
            "decision": "NO_GO",
            "contract_decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": "NETWORK_NOT_AUTHORIZED",
            "summary": (
                "A deterministic workspace-only plan bundle is ready for "
                "human-owner review. It does not authorize execution."
            ),
            "filesystem_policy": filesystem_policy,
            "dependency_decision": dependency_decision,
            "plan_id": plan.plan_id,
            "plan_sha256": plan.plan_sha256,
            "plan_directory": self.boundary.report_path(plan.directory),
            "artifacts": [
                self.boundary.report_path(plan.directory / name)
                for name in plan.artifacts
            ],
            "network_access": False,
            "process_executed": False,
            "package_mutation": False,
            "application_launch": False,
            "model_loading": False,
            "generation": False,
        }
        return ModeResult(
            mode="RESOLVE_PLAN",
            decision="NO_GO",
            reason_code="NETWORK_NOT_AUTHORIZED",
            payload=payload,
            json_report=self.boundary.report_path(plan.plan_json),
            markdown_report=self.boundary.report_path(
                plan.plan_markdown
            ),
        )

    def run_resolve_authorization_validation(
        self,
        *,
        plan_path: str | os.PathLike[str],
        authorization_path: str | os.PathLike[str] | None,
        contract_path: str | os.PathLike[str] | None = None,
    ) -> ModeResult:
        """Validate one receipt without execution or ledger consumption."""

        static_result = self.run_static(
            contract_path=contract_path,
            write_reports=False,
        )
        dependency_decision = static_result.payload.get(
            "dependency_decision"
        )
        if not isinstance(dependency_decision, dict):
            result = AuthorizationValidationResult(
                validation_status="FAIL",
                reason_code="RESOLVE_PLAN_DEPENDENCY_GATE_UNSUPPORTED",
                plan_id=None,
                plan_sha256=None,
                filesystem_policy=static_result.payload.get(
                    "filesystem_policy"
                ),
            )
        else:
            result = ResolveAuthorizationWorkflow(
                self.fs,
                self.app_root,
                self.resolver_paths,
            ).validate_authorization(
                plan_path=self._workspace_relative_path(plan_path),
                authorization_path=(
                    self._workspace_relative_path(authorization_path)
                    if authorization_path is not None
                    else None
                ),
                dependency_decision=dependency_decision,
            )
        self.resolver_paths.ensure_report_directory(self.fs)
        json_path = (
            self.resolver_paths.reports
            / "resolve-authorization-validation.json"
        )
        markdown_path = (
            self.resolver_paths.reports
            / "resolve-authorization-validation.md"
        )
        self.fs.write_text(json_path, render_validation_json(result))
        self.fs.write_text(
            markdown_path,
            render_validation_markdown(result),
        )
        payload = result.to_dict()
        return ModeResult(
            mode="RESOLVE_AUTHORIZATION",
            decision="NO_GO",
            reason_code=result.reason_code,
            payload=payload,
            json_report=self.boundary.report_path(json_path),
            markdown_report=self.boundary.report_path(markdown_path),
        )

    def _static_inspector(self) -> StaticInspector:
        return StaticInspector(
            self.fs,
            self.workspace_root,
            self.app_root,
        )

    def _app_relative_root(
        self,
        path: str | os.PathLike[str],
    ) -> Path:
        candidate = Path(os.fspath(path))
        if candidate.is_absolute():
            return candidate
        return Path(
            os.path.abspath(os.fspath(self.app_root / candidate))
        )

    def _workspace_relative_path(
        self,
        path: str | os.PathLike[str],
    ) -> Path:
        candidate = Path(os.fspath(path))
        if candidate.is_absolute():
            return candidate
        if candidate.parts and candidate.parts[0].casefold() in {
            "app",
            "evidence",
            "reference",
        }:
            return self.workspace_root / candidate
        return Path(
            os.path.abspath(os.fspath(self.app_root / candidate))
        )

    def _contract_path(
        self, path: str | os.PathLike[str] | None
    ) -> str:
        candidate = self.boundary.authorize(
            path or self.default_contract, "contract-input"
        )
        expected_parent = self.default_contract.parent
        if (
            os.path.normcase(os.fspath(candidate.parent))
            != os.path.normcase(os.fspath(expected_parent))
            or candidate.suffix.casefold() != ".json"
        ):
            raise ValueError("CONTRACT_INPUT_OUTSIDE_OWNED_FIXTURE_DIRECTORY")
        return os.fspath(candidate)

    def _report_paths(self, report_stem: str) -> tuple[Path, Path]:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", report_stem) is None:
            raise ValueError("REPORT_STEM_INVALID")
        json_path = self.resolver_paths.reports / f"{report_stem}.json"
        markdown_path = self.resolver_paths.reports / f"{report_stem}.md"
        for path in (json_path, markdown_path):
            checked = self.boundary.authorize(path, "report-output-plan")
            if os.path.normcase(os.fspath(checked.parent)) != os.path.normcase(
                os.fspath(self.resolver_paths.reports)
            ):
                raise ValueError("REPORT_OUTPUT_OUTSIDE_REPORT_DIRECTORY")
        return json_path, markdown_path

    def _verify_paths(
        self,
        json_path: str | os.PathLike[str],
        markdown_path: str | os.PathLike[str],
    ) -> tuple[Path, Path]:
        checked_json = self.boundary.authorize(json_path, "verify-json-input")
        checked_markdown = self.boundary.authorize(
            markdown_path, "verify-markdown-input"
        )
        expected_parent = os.path.normcase(
            os.fspath(self.resolver_paths.reports)
        )
        if any(
            os.path.normcase(os.fspath(path.parent)) != expected_parent
            for path in (checked_json, checked_markdown)
        ):
            raise ValueError("VERIFY_INPUT_OUTSIDE_REPORT_DIRECTORY")
        if (
            checked_json.suffix.casefold() != ".json"
            or checked_markdown.suffix.casefold() != ".md"
            or checked_json.stem.casefold() != checked_markdown.stem.casefold()
            or checked_json.stem.casefold().endswith(".verification")
        ):
            raise ValueError("VERIFY_REPORT_PAIR_INVALID")
        return checked_json, checked_markdown

    def _verification_report_paths(
        self,
        checked_json: Path,
        checked_markdown: Path,
    ) -> tuple[Path, Path]:
        output_json = checked_json.with_name(
            f"{checked_json.stem}.verification.json"
        )
        output_markdown = checked_markdown.with_name(
            f"{checked_markdown.stem}.verification.md"
        )
        expected_parent = os.path.normcase(
            os.fspath(self.resolver_paths.reports)
        )
        for path in (output_json, output_markdown):
            checked = self.boundary.authorize(
                path,
                "verification-report-output-plan",
            )
            if os.path.normcase(os.fspath(checked.parent)) != expected_parent:
                raise ValueError(
                    "VERIFY_OUTPUT_OUTSIDE_REPORT_DIRECTORY"
                )
        return output_json, output_markdown

    @staticmethod
    def _verification_reason(reason: str) -> str:
        if reason == "CURRENT_STATE_DRIFT":
            return "CURRENT_STATE_DRIFT"
        if reason in {
            "REPORT_ID_MISMATCH",
            "REPORT_JSON_CANONICAL_VIEW_MISMATCH",
        }:
            return "CONTRACT_HASH_MISMATCH"
        if reason.startswith("REPORT_MARKDOWN_"):
            return "CURRENT_STATE_DRIFT"
        return "CONTRACT_SCHEMA_INVALID"
