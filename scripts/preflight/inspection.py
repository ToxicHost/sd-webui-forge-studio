"""Static, workspace-only inspection of Forge dependency ownership."""

from __future__ import annotations

from email.parser import Parser
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .boundary import WorkspaceFS
from .constraints import (
    ConstraintDecisionEngine,
    DependencyConstraint,
)
from .contracts import PreflightContract


class StaticInspectionError(RuntimeError):
    """Raised when required local static evidence is incomplete."""


def launcher_requirements_from_text(text: str) -> tuple[str, ...]:
    """Extract the exact launcher-owned GRADIO_PACKAGE default from source."""

    matches = re.findall(
        r"""os\.environ\.get\(\s*["']GRADIO_PACKAGE["']\s*,\s*"""
        r"""["'](?P<requirements>[^"']+)["']\s*\)""",
        text,
    )
    if len(matches) != 1:
        raise StaticInspectionError(
            "Could not establish one launcher-owned GRADIO_PACKAGE default."
        )
    requirements = tuple(matches[0].split())
    parsed = tuple(Requirement(raw) for raw in requirements)
    names = tuple(canonicalize_name(requirement.name) for requirement in parsed)
    if names != ("gradio", "gradio-rangeslider"):
        raise StaticInspectionError(
            "Launcher GRADIO_PACKAGE does not contain the expected owned roots."
        )
    if any(requirement.url is not None for requirement in parsed):
        raise StaticInspectionError(
            "Launcher GRADIO_PACKAGE contains an unsupported direct URL."
        )
    return tuple(str(requirement) for requirement in parsed)


class StaticInspector:
    """Inspect source text and exact installed metadata without importing Forge."""

    def __init__(
        self,
        fs: WorkspaceFS,
        workspace_root: Path,
        app_root: Path,
    ) -> None:
        self.fs = fs
        self.workspace_root = workspace_root
        self.app_root = app_root
        self.runtime_executable = sys.executable
        self.runtime_version = (
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        )

    def inspect(self, contract: PreflightContract) -> dict[str, Any]:
        expected_python = self.app_root / "venv" / "Scripts" / "python.exe"
        expected_text = os.path.normcase(
            os.path.normpath(os.path.abspath(os.fspath(expected_python)))
        )
        observed_text = os.path.normcase(
            os.path.normpath(os.path.abspath(self.runtime_executable))
        )

        if observed_text != expected_text:
            return self._runtime_blocked_result(contract)
        if not self.fs.is_file(expected_python):
            return self._runtime_blocked_result(contract)

        requirements_path = self.app_root / "requirements.txt"
        launch_utils_path = self.app_root / "modules" / "launch_utils.py"
        requirements_text = self.fs.read_text(requirements_path)
        launch_utils_text = self.fs.read_text(launch_utils_path)

        direct = self._direct_requirements(requirements_text)
        pillow_requirement, pillow_line = self._required_requirement(
            direct, "pillow"
        )
        heif_requirement, _ = self._required_requirement(
            direct, "pillow-heif"
        )
        gradio_version = self._gradio_launcher_version(launch_utils_text)

        pillow_version = self._exact_version(pillow_requirement)
        heif_version = self._exact_version(heif_requirement)
        metadata_root = self.app_root / "venv" / "Lib" / "site-packages"

        gradio_metadata_path = self._metadata_path(
            metadata_root, "gradio", gradio_version
        )
        pillow_metadata_path = self._metadata_path(
            metadata_root, "pillow", pillow_version
        )
        heif_metadata_path = self._metadata_path(
            metadata_root, "pillow_heif", heif_version
        )

        gradio_metadata = self._read_metadata(gradio_metadata_path)
        pillow_metadata = self._read_metadata(pillow_metadata_path)
        heif_metadata = self._read_metadata(heif_metadata_path)

        target_environment = self._target_environment()
        gradio_pillow = self._metadata_requirement(
            gradio_metadata, "pillow", target_environment
        )
        heif_pillow = self._metadata_requirement(
            heif_metadata, "pillow", target_environment
        )

        constraints = (
            DependencyConstraint(
                constraint_id=f"repository:Pillow:requirements.txt:{pillow_line}",
                dependency="pillow",
                raw_requirement=str(pillow_requirement),
                owner={
                    "kind": "repository_direct_requirement",
                    "display_name": f"repository requirements.txt:{pillow_line}",
                },
                source={"path": "app/requirements.txt", "line": pillow_line},
                direct=True,
            ),
            DependencyConstraint(
                constraint_id=f"distribution:gradio:{gradio_version}:pillow",
                dependency="pillow",
                raw_requirement=str(gradio_pillow),
                owner={
                    "kind": "distribution",
                    "name": "gradio",
                    "version": gradio_version,
                    "display_name": f"gradio {gradio_version}",
                },
                source={
                    "path": self.fs.boundary.report_path(gradio_metadata_path),
                    "field": "Requires-Dist",
                },
            ),
            DependencyConstraint(
                constraint_id=f"distribution:pillow-heif:{heif_version}:pillow",
                dependency="pillow",
                raw_requirement=str(heif_pillow),
                owner={
                    "kind": "distribution",
                    "name": "pillow-heif",
                    "version": heif_version,
                    "display_name": f"pillow-heif {heif_version}",
                },
                source={
                    "path": self.fs.boundary.report_path(heif_metadata_path),
                    "field": "Requires-Dist",
                },
            ),
        )

        dependency_decision = ConstraintDecisionEngine().evaluate(
            constraints,
            target_environment=target_environment,
            observed_versions={"pillow": str(pillow_metadata["Version"])},
        )
        top_decision = dependency_decision.decision
        top_reason_code = dependency_decision.reason_code
        if top_decision == "GO":
            top_decision = "GO_WITH_WARNINGS"
            top_reason_code = "STATIC_DEPENDENCY_COVERAGE_INCOMPLETE"
        checks = [
            {
                "id": "filesystem-policy",
                "status": "PASS",
                "basis": "contract fields plus current app/AGENTS.md SHA-256",
                "expected": contract.filesystem_policy.to_dict(),
                "observed": contract.filesystem_policy.to_dict(),
            },
            {
                "id": "workspace-venv",
                "status": "PASS",
                "basis": "exact executing interpreter path",
                "expected": "app/venv/Scripts/python.exe",
                "observed": "app/venv/Scripts/python.exe",
            },
            {
                "id": "base-interpreter-inspection",
                "status": "PASS",
                "basis": "prohibited operation remains disabled",
                "expected": False,
                "observed": False,
            },
            {
                "id": "command-processor-discovery",
                "status": "PASS",
                "basis": "Static mode has no command-processor probe",
                "expected": False,
                "observed": False,
            },
            {
                "id": "network-access",
                "status": "PASS",
                "basis": "Static mode contains no network operation",
                "expected": False,
                "observed": False,
            },
            {
                "id": "dependency-intersection",
                "status": (
                    "FAIL"
                    if dependency_decision.decision == "NO_GO"
                    else top_decision
                ),
                "basis": "repository requirements plus installed METADATA",
                "expected": "non-empty mandatory intersection",
                "observed": dependency_decision.to_dict()[
                    "normalized_intersection"
                ],
            },
        ]

        return {
            "schema_version": "forge-preflight-report/v2",
            "mode": "STATIC",
            "decision": top_decision,
            "contract_decision": top_decision,
            "effective_decision": top_decision,
            "reason_code": top_reason_code,
            "summary": (
                "Static inspection proved an empty mandatory Pillow "
                "constraint intersection."
                if dependency_decision.decision == "NO_GO"
                else (
                    "The narrow known-conflict gate did not prove a conflict, "
                    "but its evidence is insufficient for a complete "
                    "dependency GO."
                )
            ),
            "runtime": {
                "python": {
                    "path": "app/venv/Scripts/python.exe",
                    "version": ".".join(str(part) for part in self.runtime_version),
                    "base_interpreter_inspected": False,
                    "isolated": bool(sys.flags.isolated),
                    "automatic_site_processing": not bool(sys.flags.no_site),
                    "bytecode_writes": not bool(sys.dont_write_bytecode),
                },
                "command_processor": {
                    "discovery_performed": False,
                    "verification_status": (
                        contract.command_processor.verification_status
                    ),
                    "identity": contract.command_processor.identity,
                },
            },
            "capabilities": {
                "filesystem_roots": list(
                    contract.filesystem_policy.normalized_permitted_roots
                ),
                "private_local_access": False,
                "outside_filesystem_access": False,
                "network_access": False,
                "package_mutation": False,
                "application_launch": False,
                "model_loading": False,
                "generation": False,
            },
            "inputs": [
                "app/AGENTS.md",
                "app/requirements.txt",
                "app/modules/launch_utils.py",
                self.fs.boundary.report_path(gradio_metadata_path),
                self.fs.boundary.report_path(pillow_metadata_path),
                self.fs.boundary.report_path(heif_metadata_path),
            ],
            "installed": {
                "gradio": str(gradio_metadata["Version"]),
                "pillow": str(pillow_metadata["Version"]),
                "pillow-heif": str(heif_metadata["Version"]),
            },
            "checks": checks,
            "filesystem_policy": contract.filesystem_policy.to_dict(),
            "dependency_decision": dependency_decision.to_dict(),
            "privacy": {
                "absolute_paths_in_report": False,
                "environment_dump_performed": False,
                "secrets_collected": False,
            },
        }

    def _runtime_blocked_result(
        self, contract: PreflightContract
    ) -> dict[str, Any]:
        return {
            "schema_version": "forge-preflight-report/v2",
            "mode": "STATIC",
            "decision": "NO_GO",
            "contract_decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": "WORKSPACE_VENV_PYTHON_REQUIRED",
            "summary": (
                "Static mode must execute through "
                "app/venv/Scripts/python.exe; no fallback discovery is allowed."
            ),
            "runtime": {
                "python": {
                    "path": "app/venv/Scripts/python.exe",
                    "version": None,
                    "base_interpreter_inspected": False,
                    "isolated": bool(sys.flags.isolated),
                    "automatic_site_processing": not bool(sys.flags.no_site),
                    "bytecode_writes": not bool(sys.dont_write_bytecode),
                },
                "command_processor": {
                    "discovery_performed": False,
                    "verification_status": (
                        contract.command_processor.verification_status
                    ),
                    "identity": contract.command_processor.identity,
                },
            },
            "capabilities": {
                "filesystem_roots": list(
                    contract.filesystem_policy.normalized_permitted_roots
                ),
                "private_local_access": False,
                "outside_filesystem_access": False,
                "network_access": False,
                "package_mutation": False,
                "application_launch": False,
                "model_loading": False,
                "generation": False,
            },
            "inputs": ["app/AGENTS.md"],
            "installed": {},
            "filesystem_policy": contract.filesystem_policy.to_dict(),
            "checks": [
                {
                    "id": "workspace-venv",
                    "status": "FAIL",
                    "basis": "exact executing interpreter path",
                    "expected": "app/venv/Scripts/python.exe",
                    "observed": "UNAPPROVED_OR_MISSING",
                },
                {
                    "id": "base-interpreter-inspection",
                    "status": "PASS",
                    "basis": "prohibited operation remains disabled",
                    "expected": False,
                    "observed": False,
                },
            ],
            "dependency_decision": None,
            "privacy": {
                "absolute_paths_in_report": False,
                "environment_dump_performed": False,
                "secrets_collected": False,
            },
        }

    @staticmethod
    def _direct_requirements(
        text: str,
    ) -> dict[str, tuple[Requirement, int]]:
        requirements: dict[str, tuple[Requirement, int]] = {}
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = Requirement(line)
            requirements[canonicalize_name(parsed.name)] = (
                parsed,
                line_number,
            )
        return requirements

    @staticmethod
    def _required_requirement(
        requirements: Mapping[str, tuple[Requirement, int]], name: str
    ) -> tuple[Requirement, int]:
        normalized = canonicalize_name(name)
        try:
            return requirements[normalized]
        except KeyError as exc:
            raise StaticInspectionError(
                f"Required repository constraint is missing: {normalized}"
            ) from exc

    @staticmethod
    def _exact_version(requirement: Requirement) -> str:
        exact = [
            specifier.version
            for specifier in requirement.specifier
            if specifier.operator in ("==", "===")
            and not specifier.version.endswith(".*")
        ]
        if len(exact) != 1:
            raise StaticInspectionError(
                f"Expected one exact version for {requirement.name}"
            )
        return exact[0]

    @staticmethod
    def _gradio_launcher_version(text: str) -> str:
        gradio = Requirement(launcher_requirements_from_text(text)[0])
        return StaticInspector._exact_version(gradio)

    def _metadata_path(
        self, metadata_root: Path, normalized_name: str, version: str
    ) -> Path:
        directory = self.fs.find_child(
            metadata_root,
            prefix=f"{normalized_name}-{version}",
            suffix=".dist-info",
        )
        metadata_path = directory / "METADATA"
        if not self.fs.is_file(metadata_path):
            raise StaticInspectionError(
                f"Installed metadata missing: "
                f"{self.fs.boundary.report_path(metadata_path)}"
            )
        return metadata_path

    def _read_metadata(self, path: Path) -> Mapping[str, Any]:
        message = Parser().parsestr(self.fs.read_text(path))
        if not message.get("Name") or not message.get("Version"):
            raise StaticInspectionError(
                f"Installed metadata identity incomplete: "
                f"{self.fs.boundary.report_path(path)}"
            )
        return {
            "Name": message["Name"],
            "Version": message["Version"],
            "Requires-Dist": message.get_all("Requires-Dist", []),
        }

    @staticmethod
    def _metadata_requirement(
        metadata: Mapping[str, Any],
        dependency_name: str,
        target_environment: Mapping[str, str],
    ) -> Requirement:
        normalized = canonicalize_name(dependency_name)
        matches: list[Requirement] = []
        for raw in metadata.get("Requires-Dist", ()):
            requirement = Requirement(raw)
            if canonicalize_name(requirement.name) != normalized:
                continue
            if requirement.marker is not None and not requirement.marker.evaluate(
                environment=dict(target_environment)
            ):
                continue
            matches.append(requirement)
        if len(matches) != 1:
            raise StaticInspectionError(
                f"Expected one mandatory {normalized} requirement in "
                f"{metadata.get('Name')} {metadata.get('Version')}; "
                f"found {len(matches)}"
            )
        return matches[0]

    def _target_environment(self) -> dict[str, str]:
        major, minor, micro = self.runtime_version
        return {
            "implementation_name": "cpython",
            "implementation_version": f"{major}.{minor}.{micro}",
            "os_name": "nt",
            "platform_machine": "AMD64",
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": "Windows",
            "platform_version": "",
            "python_full_version": f"{major}.{minor}.{micro}",
            "python_version": f"{major}.{minor}",
            "sys_platform": "win32",
            "extra": "",
        }
