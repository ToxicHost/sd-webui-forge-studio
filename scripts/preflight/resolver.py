"""Owner-gated pip dry-run planning and structured-report consumption.

Resolve execution remains disabled pending a human-owner signature trust
anchor and preventive network/filesystem containment. Nothing in Static,
Contract, Verify, tests, or documentation can grant that authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from packaging.markers import InvalidMarker
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from . import __version__ as PREFLIGHT_VERSION
from .boundary import WorkspaceBoundary, WorkspaceFS
from .contracts import ContractError, FilesystemPolicyContract
from .constraints import (
    ConstraintDecision,
    ConstraintDecisionEngine,
    DependencyConstraint,
)
from .inspection import launcher_requirements_from_text
from .reports import sanitize_text


class ResolveAuthorizationError(PermissionError):
    """Raised before resolver filesystem mutation or process execution."""


class ResolverExecutionError(RuntimeError):
    """Raised when a future authorized pip dry run fails."""


class ReportCoverage(str, Enum):
    """Closed set of resolver-report coverage claims."""

    SELECTED_CANDIDATE_SET = "pip-dry-run-selected-candidate-set"


REQUIRED_MARKER_ENVIRONMENT_KEYS = (
    "implementation_name",
    "implementation_version",
    "os_name",
    "platform_machine",
    "platform_python_implementation",
    "platform_release",
    "platform_system",
    "platform_version",
    "python_full_version",
    "python_version",
    "sys_platform",
)
RESOLVE_WORKSPACE_ROOTS = {
    "cache_root": "Evidence/preflight/cache",
    "temp_root": "Evidence/preflight/temp",
    "report_root": "Evidence/preflight/reports",
}
RESOLVE_AUTHORITY = {
    "network_resolution": True,
    "process": "pip-structured-dry-run-only",
    "package_install": False,
    "package_uninstall": False,
    "package_upgrade": False,
    "application_launch": False,
    "model_loading": False,
    "generation": False,
    "private_local_access": False,
}


def _parse_utc_timestamp(raw: Any, reason_code: str) -> datetime:
    if not isinstance(raw, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        raw,
    ) is None:
        raise ResolveAuthorizationError(reason_code)
    try:
        return datetime.strptime(
            raw,
            "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ResolveAuthorizationError(reason_code) from exc


def _read_repository_head(fs: WorkspaceFS, app_root: Path) -> str:
    """Read one in-workspace Git HEAD without invoking Git or following gitfiles."""

    git_directory = app_root / ".git"
    if not fs.is_dir(git_directory):
        raise ResolveAuthorizationError("RESOLVE_REPOSITORY_GITDIR_INVALID")
    raw_head = fs.read_text(
        git_directory / "HEAD",
        max_chars=4_096,
    ).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", raw_head):
        return raw_head.casefold()
    if not raw_head.startswith("ref: "):
        raise ResolveAuthorizationError("RESOLVE_REPOSITORY_HEAD_INVALID")
    reference = raw_head.removeprefix("ref: ")
    if (
        re.fullmatch(r"refs/[A-Za-z0-9._/-]+", reference) is None
        or ".." in reference.split("/")
        or reference.endswith("/")
    ):
        raise ResolveAuthorizationError("RESOLVE_REPOSITORY_REF_INVALID")
    loose_reference = git_directory / Path(reference)
    if fs.exists(loose_reference):
        value = fs.read_text(loose_reference, max_chars=4_096).strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value) is None:
            raise ResolveAuthorizationError(
                "RESOLVE_REPOSITORY_REF_VALUE_INVALID"
            )
        return value.casefold()
    packed_refs = git_directory / "packed-refs"
    if not fs.is_file(packed_refs):
        raise ResolveAuthorizationError("RESOLVE_REPOSITORY_REF_UNRESOLVED")
    matches = []
    for raw_line in fs.read_text(
        packed_refs,
        max_chars=4_000_000,
    ).splitlines():
        if not raw_line or raw_line.startswith(("#", "^")):
            continue
        fields = raw_line.split(" ")
        if len(fields) == 2 and fields[1] == reference:
            matches.append(fields[0])
    if (
        len(matches) != 1
        or re.fullmatch(
            r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}",
            matches[0],
        )
        is None
    ):
        raise ResolveAuthorizationError("RESOLVE_REPOSITORY_REF_UNRESOLVED")
    return matches[0].casefold()


@dataclass(frozen=True)
class ResolverPaths:
    cache: Path
    temp: Path
    reports: Path
    authorizations: Path
    resolve_plans: Path
    authorization_ledger: Path

    @classmethod
    def from_boundary(cls, boundary: WorkspaceBoundary) -> "ResolverPaths":
        evidence = boundary.allowed_roots["Evidence"]
        base = evidence / "preflight"
        paths = cls(
            cache=base / "cache",
            temp=base / "temp",
            reports=base / "reports",
            # Legacy scope/receipt readers are confined beneath the one
            # approved report root; Stage E0 exposes no executable path.
            authorizations=base / "reports" / "authorizations",
            resolve_plans=base / "resolve-plans",
            authorization_ledger=base / "authorization-ledger",
        )
        for path in (
            paths.cache,
            paths.temp,
            paths.reports,
            paths.authorizations,
            paths.resolve_plans,
            paths.authorization_ledger,
        ):
            boundary.authorize(path, "resolver-path-plan")
        return paths

    def ensure_runtime_directories(self, fs: WorkspaceFS) -> None:
        fs.mkdir(self.cache)
        fs.mkdir(self.temp)
        fs.mkdir(self.reports)

    def ensure_report_directory(self, fs: WorkspaceFS) -> None:
        fs.mkdir(self.reports)

    def report_dict(self, boundary: WorkspaceBoundary) -> dict[str, str]:
        return {
            "PIP_CACHE_DIR": boundary.report_path(self.cache),
            "TEMP": boundary.report_path(self.temp),
            "TMP": boundary.report_path(self.temp),
            "reports": boundary.report_path(self.reports),
            "resolve_plans": boundary.report_path(self.resolve_plans),
            "authorization_ledger": boundary.report_path(
                self.authorization_ledger
            ),
            "PIP_CONFIG_FILE": "NUL",
        }


@dataclass(frozen=True)
class ResolverScope:
    """Non-authorizing network scope used to build an owner-reviewable plan."""

    index_url: str
    allowed_endpoints: tuple[str, ...]
    redirect_risk_acknowledged: bool
    target_environment: Mapping[str, str]
    filesystem_policy: Mapping[str, Any]
    timeout_seconds: int = 1800
    max_output_chars: int = 2_000_000

    def __post_init__(self) -> None:
        if not isinstance(self.filesystem_policy, Mapping):
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            )
        try:
            normalized = FilesystemPolicyContract.from_dict(
                self.filesystem_policy
            ).to_dict()
        except ContractError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            ) from exc
        object.__setattr__(self, "filesystem_policy", normalized)

    @classmethod
    def load(
        cls,
        fs: WorkspaceFS,
        paths: ResolverPaths,
        path: str | os.PathLike[str] | None,
    ) -> "ResolverScope":
        if path is None:
            raise ResolveAuthorizationError("RESOLVE_SCOPE_PROPOSAL_REQUIRED")

        scope_path = fs.boundary.authorize(path, "read-resolve-scope")
        expected_parent = os.path.normcase(os.path.normpath(os.fspath(paths.authorizations)))
        actual_parent = os.path.normcase(
            os.path.normpath(os.fspath(scope_path.parent))
        )
        if actual_parent != expected_parent:
            raise ResolveAuthorizationError(
                "RESOLVE_SCOPE_MUST_BE_PLACED_UNDER_EVIDENCE"
            )
        if scope_path.suffix.casefold() != ".json":
            raise ResolveAuthorizationError("RESOLVE_SCOPE_MUST_BE_JSON")

        value = fs.read_json(scope_path, max_chars=64_000)
        if not isinstance(value, Mapping):
            raise ResolveAuthorizationError("RESOLVE_SCOPE_INVALID")
        allowed_fields = {
            "schema_version",
            "operation",
            "index_url",
            "allowed_endpoints",
            "redirect_risk_acknowledged",
            "target_environment",
            "filesystem_policy",
            "timeout_seconds",
            "max_output_chars",
        }
        if set(value) != allowed_fields:
            raise ResolveAuthorizationError("RESOLVE_SCOPE_FIELDS_INVALID")
        required = {
            "schema_version": "forge-resolve-scope/v1",
            "operation": "pip-structured-dry-run-plan",
            "redirect_risk_acknowledged": True,
        }
        for key, expected in required.items():
            observed = value.get(key)
            if (
                observed is not expected
                if isinstance(expected, bool)
                else observed != expected
            ):
                raise ResolveAuthorizationError(
                    f"RESOLVE_SCOPE_FIELD_INVALID:{key}"
                )
        return cls._from_mapping(value, fs=fs)

    @classmethod
    def _from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        fs: WorkspaceFS | None = None,
    ) -> "ResolverScope":
        index_url = value.get("index_url")
        if not isinstance(index_url, str):
            raise ResolveAuthorizationError(
                "RESOLVE_INDEX_URL_NOT_APPROVED"
            )
        try:
            parsed_index = urlsplit(index_url)
            index_port = parsed_index.port
        except ValueError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_INDEX_URL_NOT_APPROVED"
            ) from exc
        if (
            parsed_index.scheme.casefold() != "https"
            or not parsed_index.hostname
            or parsed_index.username is not None
            or parsed_index.password is not None
            or parsed_index.query
            or parsed_index.fragment
        ):
            raise ResolveAuthorizationError("RESOLVE_INDEX_URL_NOT_APPROVED")
        endpoints = value.get("allowed_endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise ResolveAuthorizationError("RESOLVE_ENDPOINT_SCOPE_MISSING")
        if not all(
            isinstance(endpoint, str)
            and re.fullmatch(
                r"(?i)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?",
                endpoint,
            )
            for endpoint in endpoints
        ):
            raise ResolveAuthorizationError("RESOLVE_ENDPOINT_SCOPE_INVALID")
        for endpoint in endpoints:
            if ":" in endpoint:
                port = int(endpoint.rsplit(":", 1)[1])
                if not 1 <= port <= 65535:
                    raise ResolveAuthorizationError(
                        "RESOLVE_ENDPOINT_SCOPE_INVALID"
                    )
        normalized_endpoints = tuple(
            sorted({str(endpoint).casefold() for endpoint in endpoints})
        )
        index_endpoint = parsed_index.hostname.casefold()
        if index_port is not None:
            index_endpoint = f"{index_endpoint}:{index_port}"
        if index_endpoint not in normalized_endpoints:
            raise ResolveAuthorizationError(
                "RESOLVE_INDEX_ENDPOINT_OUTSIDE_APPROVED_SCOPE"
            )
        raw_target_environment = value.get("target_environment")
        if (
            not isinstance(raw_target_environment, Mapping)
            or set(raw_target_environment)
            != set(REQUIRED_MARKER_ENVIRONMENT_KEYS)
            or not all(
                isinstance(key, str) and isinstance(child, str)
                for key, child in raw_target_environment.items()
            )
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_TARGET_ENVIRONMENT_INVALID"
            )
        target_environment = {
            key: str(raw_target_environment[key])
            for key in REQUIRED_MARKER_ENVIRONMENT_KEYS
        }
        raw_timeout = value.get("timeout_seconds", 1800)
        raw_output_limit = value.get("max_output_chars", 2_000_000)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, int)
            or isinstance(raw_output_limit, bool)
            or not isinstance(raw_output_limit, int)
        ):
            raise ResolveAuthorizationError("RESOLVE_NUMERIC_SCOPE_INVALID")
        timeout_seconds = raw_timeout
        max_output_chars = raw_output_limit
        if not 60 <= timeout_seconds <= 3600:
            raise ResolveAuthorizationError("RESOLVE_TIMEOUT_SCOPE_INVALID")
        if not 1024 <= max_output_chars <= 5_000_000:
            raise ResolveAuthorizationError("RESOLVE_OUTPUT_SCOPE_INVALID")
        raw_policy = value.get("filesystem_policy")
        if not isinstance(raw_policy, Mapping):
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            )
        try:
            policy = FilesystemPolicyContract.from_dict(raw_policy)
            if fs is not None:
                policy.validate_current(fs)
        except ContractError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            ) from exc
        return cls(
            index_url=index_url,
            allowed_endpoints=normalized_endpoints,
            redirect_risk_acknowledged=True,
            target_environment=target_environment,
            filesystem_policy=policy.to_dict(),
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "index_url": self.index_url,
            "allowed_endpoints": list(self.allowed_endpoints),
            "redirect_risk_acknowledged": self.redirect_risk_acknowledged,
            "target_environment": dict(self.target_environment),
            "filesystem_policy": dict(self.filesystem_policy),
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
        }


@dataclass(frozen=True)
class ResolveAuthorization:
    """Exact, owner-provided authorization record for one dry-run plan."""

    plan_digest: str
    dependency_plan_sha256: str
    repository_head: str
    scope: ResolverScope
    single_use: bool
    expires_at_utc: datetime

    @property
    def index_url(self) -> str:
        return self.scope.index_url

    @property
    def allowed_endpoints(self) -> tuple[str, ...]:
        return self.scope.allowed_endpoints

    @property
    def timeout_seconds(self) -> int:
        return self.scope.timeout_seconds

    @property
    def max_output_chars(self) -> int:
        return self.scope.max_output_chars

    @classmethod
    def load(
        cls,
        fs: WorkspaceFS,
        paths: ResolverPaths,
        path: str | os.PathLike[str] | None,
    ) -> "ResolveAuthorization":
        if path is None:
            raise ResolveAuthorizationError("RESOLVE_NETWORK_AUTHORIZATION_REQUIRED")

        authorized_path = fs.boundary.authorize(path, "read-resolve-authorization")
        expected_parent = os.path.normcase(os.path.normpath(os.fspath(paths.authorizations)))
        actual_parent = os.path.normcase(
            os.path.normpath(os.fspath(authorized_path.parent))
        )
        if actual_parent != expected_parent:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_MUST_BE_OWNER_PLACED_UNDER_EVIDENCE"
            )
        if authorized_path.suffix.casefold() != ".json":
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_MUST_BE_JSON"
            )

        value = fs.read_json(authorized_path, max_chars=64_000)
        if not isinstance(value, Mapping):
            raise ResolveAuthorizationError("RESOLVE_AUTHORIZATION_INVALID")
        allowed_fields = {
            "schema_version",
            "authorization_notice",
            "authorization_origin",
            "approved_by",
            "purpose",
            "network_authorized",
            "single_use",
            "plan_digest",
            "dependency_plan_sha256",
            "repository_head",
            "issued_at_utc",
            "expires_at_utc",
            "index_url",
            "allowed_endpoints",
            "redirect_risk_acknowledged",
            "target_environment",
            "timeout_seconds",
            "max_output_chars",
            "workspace_roots",
            "filesystem_policy",
            "authority",
            "owner_signature",
        }
        if set(value) != allowed_fields:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_FIELDS_INVALID"
            )
        required = {
            "schema_version": "forge-resolve-owner-grant/v2",
            "authorization_notice": "HUMAN OWNER AUTHORIZATION",
            "authorization_origin": "human-owner-supplied",
            "approved_by": "human-owner",
            "purpose": "pip-dependency-resolution-dry-run",
            "network_authorized": True,
            "single_use": True,
            "redirect_risk_acknowledged": True,
        }
        for key, expected in required.items():
            observed = value.get(key)
            if (
                observed is not expected
                if isinstance(expected, bool)
                else observed != expected
            ):
                raise ResolveAuthorizationError(
                    f"RESOLVE_AUTHORIZATION_FIELD_INVALID:{key}"
                )
        digest = str(value.get("plan_digest", ""))
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ResolveAuthorizationError("RESOLVE_PLAN_DIGEST_MISSING")
        dependency_plan_sha256 = value.get("dependency_plan_sha256")
        if (
            not isinstance(dependency_plan_sha256, str)
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                dependency_plan_sha256,
            )
            is None
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_DEPENDENCY_PLAN_DIGEST_MISSING"
            )
        repository_head = value.get("repository_head")
        if (
            not isinstance(repository_head, str)
            or re.fullmatch(
                r"[0-9a-f]{40}|[0-9a-f]{64}",
                repository_head,
            )
            is None
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_REPOSITORY_HEAD_BINDING_INVALID"
            )
        current_head = _read_repository_head(
            fs,
            fs.boundary.workspace_root / "app",
        )
        if repository_head != current_head:
            raise ResolveAuthorizationError(
                "RESOLVE_REPOSITORY_HEAD_MISMATCH"
            )
        raw_workspace_roots = value.get("workspace_roots")
        if (
            not isinstance(raw_workspace_roots, Mapping)
            or dict(raw_workspace_roots) != RESOLVE_WORKSPACE_ROOTS
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_WORKSPACE_ROOT_SCOPE_INVALID"
            )
        raw_authority = value.get("authority")
        if (
            not isinstance(raw_authority, Mapping)
            or set(raw_authority) != set(RESOLVE_AUTHORITY)
            or any(
                raw_authority[key] is not expected
                if isinstance(expected, bool)
                else raw_authority[key] != expected
                for key, expected in RESOLVE_AUTHORITY.items()
            )
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORITY_SCOPE_INVALID"
            )
        scope = ResolverScope._from_mapping(value, fs=fs)
        issued_at = _parse_utc_timestamp(
            value.get("issued_at_utc"),
            "RESOLVE_AUTHORIZATION_ISSUED_AT_INVALID",
        )
        expires_at = _parse_utc_timestamp(
            value.get("expires_at_utc"),
            "RESOLVE_AUTHORIZATION_EXPIRATION_INVALID",
        )
        now = datetime.now(timezone.utc)
        if issued_at > now:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_NOT_YET_VALID"
            )
        if expires_at <= now:
            raise ResolveAuthorizationError("RESOLVE_AUTHORIZATION_EXPIRED")
        if expires_at <= issued_at or (expires_at - issued_at).total_seconds() > 86_400:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_WINDOW_INVALID"
            )
        if (expires_at - now).total_seconds() <= scope.timeout_seconds:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_EXPIRES_BEFORE_TIMEOUT"
            )
        raw_signature = value.get("owner_signature")
        if not isinstance(raw_signature, Mapping) or set(raw_signature) != {
            "algorithm",
            "key_id",
            "value_base64",
        }:
            raise ResolveAuthorizationError(
                "RESOLVE_OWNER_SIGNATURE_FIELDS_INVALID"
            )
        if (
            raw_signature.get("algorithm") != "ed25519"
            or not isinstance(raw_signature.get("key_id"), str)
            or re.fullmatch(
                r"[A-Za-z0-9._-]{1,64}",
                raw_signature["key_id"],
            )
            is None
            or not isinstance(raw_signature.get("value_base64"), str)
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_OWNER_SIGNATURE_INVALID"
            )
        try:
            signature = base64.b64decode(
                raw_signature["value_base64"],
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_OWNER_SIGNATURE_INVALID"
            ) from exc
        if len(signature) != 64:
            raise ResolveAuthorizationError(
                "RESOLVE_OWNER_SIGNATURE_INVALID"
            )
        raise ResolveAuthorizationError(
            "RESOLVE_OWNER_SIGNATURE_VERIFIER_NOT_CONFIGURED"
        )


@dataclass(frozen=True)
class ResolverPlan:
    digest: str
    run_id: str
    repository_head: str
    dependency_plan_sha256: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    cwd: Path
    requirements_text: str
    requirements_sha256: str
    launcher_source_sha256: str
    launcher_requirements: tuple[str, ...]
    local_dependency_gate_sha256: str
    filesystem_policy: Mapping[str, Any]
    coverage: ReportCoverage
    tool_manifest: Mapping[str, str]
    execution_manifest: Mapping[str, Any]
    scope: ResolverScope
    cache_directory: Path
    temp_directory: Path
    report_directory: Path
    combined_requirements: Path
    netrc_file: Path
    pip_report: Path
    stdout_report: Path
    stderr_report: Path
    authorization_receipt: Path
    attempt_manifest: Path

    def review_dict(self) -> dict[str, Any]:
        """Sanitized plan view for the human authorization handshake."""

        return {
            "schema_version": "forge-resolver-plan-review/v1",
            "plan_digest": self.digest,
            "run_id": self.run_id,
            "repository_head": self.repository_head,
            "dependency_plan_sha256": self.dependency_plan_sha256,
            "coverage": self.coverage.value,
            "requirements_sha256": self.requirements_sha256,
            "launcher_source_sha256": self.launcher_source_sha256,
            "launcher_requirements": list(self.launcher_requirements),
            "local_dependency_gate_sha256": self.local_dependency_gate_sha256,
            "filesystem_policy": dict(self.filesystem_policy),
            "tool_manifest": dict(self.tool_manifest),
            "execution_manifest": dict(self.execution_manifest),
            "scope": self.scope.to_digest_dict(),
            "argv": [
                "app/venv/Scripts/python.exe",
                "-I",
                "-S",
                "-B",
                "app/scripts/preflight/pip_bootstrap.py",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--only-binary=:all:",
                "--report",
                (
                    "Evidence/preflight/reports/"
                    f"{self.run_id}/pip-dry-run-report.json"
                ),
                "--index-url",
                self.scope.index_url,
                "-r",
                (
                    "Evidence/preflight/temp/"
                    f"{self.run_id}/combined-requirements.txt"
                ),
            ],
            "environment": {
                "PIP_CACHE_DIR": f"Evidence/preflight/cache/{self.run_id}",
                "TEMP": f"Evidence/preflight/temp/{self.run_id}",
                "TMP": f"Evidence/preflight/temp/{self.run_id}",
                "PIP_CONFIG_FILE": "NUL",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_KEYRING_PROVIDER": "disabled",
                "PIP_NO_INPUT": "1",
                "NETRC": (
                    "Evidence/preflight/temp/"
                    f"{self.run_id}/empty.netrc"
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            },
            "network_execution_authorized": False,
            "selected_replacement": None,
            "recommended_replacement": None,
        }


@dataclass(frozen=True)
class PipReportContext:
    """Trusted, plan-bound inputs for deterministic report consumption."""

    report_path: Path
    plan_digest: str
    coverage: ReportCoverage
    requirements_text: str
    launcher_requirements: tuple[str, ...]
    allowed_endpoints: tuple[str, ...]
    expected_environment: Mapping[str, str]
    filesystem_policy: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.filesystem_policy, Mapping):
            raise ResolverExecutionError(
                "PIP_REPORT_FILESYSTEM_POLICY_INVALID"
            )
        try:
            normalized = FilesystemPolicyContract.from_dict(
                self.filesystem_policy
            ).to_dict()
        except ContractError as exc:
            raise ResolverExecutionError(
                "PIP_REPORT_FILESYSTEM_POLICY_INVALID"
            ) from exc
        object.__setattr__(self, "filesystem_policy", normalized)

    @classmethod
    def from_plan(cls, plan: ResolverPlan) -> "PipReportContext":
        return cls(
            report_path=plan.pip_report,
            plan_digest=plan.digest,
            coverage=plan.coverage,
            requirements_text=plan.requirements_text,
            launcher_requirements=plan.launcher_requirements,
            allowed_endpoints=plan.scope.allowed_endpoints,
            expected_environment=dict(plan.scope.target_environment),
            filesystem_policy=dict(plan.filesystem_policy),
        )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class ProcessRunner:
    """Exact-executable, no-shell process runner used only by Resolve."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
        max_output_chars: int,
    ) -> CommandResult:
        if max_output_chars < 0:
            raise ValueError("PROCESS_OUTPUT_LIMIT_INVALID")
        process = subprocess.Popen(
            list(argv),
            cwd=os.fspath(cwd),
            env=dict(environment),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        captured: dict[str, list[str]] = {"stdout": [], "stderr": []}
        sizes = {"stdout": 0, "stderr": 0}
        truncated = {"stdout": False, "stderr": False}
        reader_errors: list[BaseException] = []

        def drain(name: str, stream: Any) -> None:
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    remaining = max_output_chars - sizes[name]
                    if remaining > 0:
                        retained = chunk[:remaining]
                        captured[name].append(retained)
                        sizes[name] += len(retained)
                    if len(chunk) > max(remaining, 0):
                        truncated[name] = True
            except BaseException as exc:  # pragma: no cover - OS pipe failure
                reader_errors.append(exc)

        stdout_thread = threading.Thread(
            target=drain,
            args=("stdout", process.stdout),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=drain,
            args=("stderr", process.stderr),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            returncode = process.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            process.stdout.close()
            process.stderr.close()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            raise ResolverExecutionError("PROCESS_OUTPUT_DRAIN_TIMEOUT")
        process.stdout.close()
        process.stderr.close()
        if reader_errors:
            raise ResolverExecutionError("PROCESS_OUTPUT_CAPTURE_FAILED") from (
                reader_errors[0]
            )
        return CommandResult(
            returncode=returncode,
            stdout="".join(captured["stdout"]),
            stderr="".join(captured["stderr"]),
            timed_out=timed_out,
            stdout_truncated=truncated["stdout"],
            stderr_truncated=truncated["stderr"],
        )


class Resolver:
    """Build and, only after exact owner authorization, execute a pip dry run."""

    def __init__(
        self,
        fs: WorkspaceFS,
        app_root: Path,
        paths: ResolverPaths,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.fs = fs
        self.app_root = app_root
        self.paths = paths
        self.runner = runner or ProcessRunner()
        self.venv_python = app_root / "venv" / "Scripts" / "python.exe"
        self.requirements = app_root / "requirements.txt"
        self.launch_utils = app_root / "modules" / "launch_utils.py"
        self.pip_bootstrap = app_root / "scripts" / "preflight" / "pip_bootstrap.py"

    def require_authorization(
        self, authorization_path: str | os.PathLike[str] | None
    ) -> ResolveAuthorization:
        """Authorization is the first Resolve gate."""

        return ResolveAuthorization.load(
            self.fs, self.paths, authorization_path
        )

    def build_plan(
        self,
        *,
        scope: ResolverScope,
        local_dependency_decision: Mapping[str, Any],
        filesystem_policy: Mapping[str, Any],
    ) -> ResolverPlan:
        """Build a deterministic plan; this performs no process execution."""

        if str(local_dependency_decision.get("decision")) != "GO":
            raise ResolveAuthorizationError("DEPENDENCY_GATE_BLOCKED_RESOLVE_PLAN")
        requirements_text = self.fs.read_text(
            self.requirements,
            max_chars=4_000_000,
        )
        launcher_source_text = self.fs.read_text(
            self.launch_utils,
            max_chars=4_000_000,
        )
        launcher_requirements = launcher_requirements_from_text(
            launcher_source_text
        )
        _validate_resolver_inputs(
            requirements_text,
            launcher_requirements,
        )
        requirements_sha256 = hashlib.sha256(
            requirements_text.encode("utf-8")
        ).hexdigest()
        launcher_source_sha256 = hashlib.sha256(
            launcher_source_text.encode("utf-8")
        ).hexdigest()
        dependency_gate_bytes = json.dumps(
            local_dependency_decision,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        local_dependency_gate_sha256 = hashlib.sha256(
            dependency_gate_bytes
        ).hexdigest()
        repository_head = _read_repository_head(self.fs, self.app_root)
        dependency_plan_payload = {
            "schema_version": "forge-dependency-plan/v1",
            "requirements_sha256": requirements_sha256,
            "launcher_source_sha256": launcher_source_sha256,
            "launcher_requirements": list(launcher_requirements),
            "local_dependency_gate_sha256": local_dependency_gate_sha256,
            "target_environment": dict(scope.target_environment),
        }
        dependency_plan_sha256 = "sha256:" + hashlib.sha256(
            json.dumps(
                dependency_plan_payload,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not isinstance(filesystem_policy, Mapping):
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            )
        try:
            checked_policy = FilesystemPolicyContract.from_dict(
                filesystem_policy
            )
            checked_policy.validate_current(self.fs)
        except ContractError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_INVALID"
            ) from exc
        normalized_filesystem_policy = checked_policy.to_dict()
        if dict(scope.filesystem_policy) != normalized_filesystem_policy:
            raise ResolveAuthorizationError(
                "RESOLVE_SCOPE_FILESYSTEM_POLICY_MISMATCH"
            )
        tool_manifest = self._tool_manifest()
        execution_manifest = self._execution_manifest()
        coverage = ReportCoverage.SELECTED_CANDIDATE_SET
        digest_payload = {
            "schema_version": "forge-resolver-plan/v2",
            "tool_version": PREFLIGHT_VERSION,
            "requirements_sha256": requirements_sha256,
            "repository_head": repository_head,
            "dependency_plan_sha256": dependency_plan_sha256,
            "launcher_source_sha256": launcher_source_sha256,
            "local_dependency_gate_sha256": local_dependency_gate_sha256,
            "filesystem_policy": normalized_filesystem_policy,
            "tool_manifest": tool_manifest,
            "execution_manifest": execution_manifest,
            "launcher_requirements": list(launcher_requirements),
            "coverage": coverage.value,
            "argv": [
                "app/venv/Scripts/python.exe",
                "-I",
                "-S",
                "-B",
                "app/scripts/preflight/pip_bootstrap.py",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--only-binary=:all:",
                "--report",
                "Evidence/preflight/reports/<RUN_ID>/pip-dry-run-report.json",
                "--index-url",
                scope.index_url,
                "-r",
                "Evidence/preflight/temp/<RUN_ID>/combined-requirements.txt",
            ],
            "environment": {
                "PIP_CACHE_DIR": "Evidence/preflight/cache/<RUN_ID>",
                "TEMP": "Evidence/preflight/temp/<RUN_ID>",
                "TMP": "Evidence/preflight/temp/<RUN_ID>",
                "PIP_CONFIG_FILE": "NUL",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_KEYRING_PROVIDER": "disabled",
                "PIP_NO_INPUT": "1",
                "NETRC": "Evidence/preflight/temp/<RUN_ID>/empty.netrc",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            },
            "scope": scope.to_digest_dict(),
        }
        digest = "sha256:" + hashlib.sha256(
            json.dumps(
                digest_payload,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        run_id = digest.removeprefix("sha256:")[:16]
        cache_directory = self.paths.cache / run_id
        temp_directory = self.paths.temp / run_id
        report_directory = self.paths.reports / run_id
        combined_requirements = temp_directory / "combined-requirements.txt"
        netrc_file = temp_directory / "empty.netrc"
        pip_report = report_directory / "pip-dry-run-report.json"
        stdout_report = report_directory / "pip-dry-run-stdout.txt"
        stderr_report = report_directory / "pip-dry-run-stderr.txt"
        authorization_receipt = (
            self.paths.reports / f"{run_id}.authorization-consumed.json"
        )
        attempt_manifest = report_directory / "resolve-attempt.json"
        environment = {
            "PIP_CACHE_DIR": os.fspath(cache_directory),
            "TEMP": os.fspath(temp_directory),
            "TMP": os.fspath(temp_directory),
            "PIP_CONFIG_FILE": "NUL",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_KEYRING_PROVIDER": "disabled",
            "PIP_NO_INPUT": "1",
            "NETRC": os.fspath(netrc_file),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        argv = (
            os.fspath(self.venv_python),
            "-I",
            "-S",
            "-B",
            os.fspath(self.pip_bootstrap),
            "install",
            "--dry-run",
            "--ignore-installed",
            "--only-binary=:all:",
            "--report",
            os.fspath(pip_report),
            "--index-url",
            scope.index_url,
            "-r",
            os.fspath(combined_requirements),
        )
        return ResolverPlan(
            digest=digest,
            run_id=run_id,
            repository_head=repository_head,
            dependency_plan_sha256=dependency_plan_sha256,
            argv=argv,
            environment=environment,
            cwd=temp_directory,
            requirements_text=requirements_text,
            requirements_sha256=requirements_sha256,
            launcher_source_sha256=launcher_source_sha256,
            launcher_requirements=tuple(launcher_requirements),
            local_dependency_gate_sha256=local_dependency_gate_sha256,
            filesystem_policy=normalized_filesystem_policy,
            coverage=coverage,
            tool_manifest=tool_manifest,
            execution_manifest=execution_manifest,
            scope=scope,
            cache_directory=cache_directory,
            temp_directory=temp_directory,
            report_directory=report_directory,
            combined_requirements=combined_requirements,
            netrc_file=netrc_file,
            pip_report=pip_report,
            stdout_report=stdout_report,
            stderr_report=stderr_report,
            authorization_receipt=authorization_receipt,
            attempt_manifest=attempt_manifest,
        )

    def execute_authorized_dry_run(
        self,
        *,
        authorization_path: str | os.PathLike[str] | None,
        local_dependency_decision: Mapping[str, Any],
        filesystem_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Future-only operation. Never called by current tests or Static mode."""

        authorization = self.require_authorization(authorization_path)
        if str(local_dependency_decision.get("decision")) != "GO":
            return {
                "schema_version": "forge-preflight-report/v2",
                "mode": "RESOLVE",
                "decision": "NO_GO",
                "contract_decision": "NO_GO",
                "effective_decision": "NO_GO",
                "reason_code": "DEPENDENCY_GATE_BLOCKED_RESOLVE",
                "filesystem_policy": dict(filesystem_policy),
                "network_access": False,
                "process_executed": False,
                "package_mutation": False,
            }

        plan = self.build_plan(
            scope=authorization.scope,
            local_dependency_decision=local_dependency_decision,
            filesystem_policy=filesystem_policy,
        )
        if plan.digest != authorization.plan_digest:
            raise ResolveAuthorizationError("RESOLVE_PLAN_DIGEST_MISMATCH")
        if (
            plan.dependency_plan_sha256
            != authorization.dependency_plan_sha256
        ):
            raise ResolveAuthorizationError(
                "RESOLVE_DEPENDENCY_PLAN_DIGEST_MISMATCH"
            )
        if plan.repository_head != authorization.repository_head:
            raise ResolveAuthorizationError(
                "RESOLVE_REPOSITORY_HEAD_MISMATCH"
            )

        if any(
            self.fs.exists(path)
            for path in (
                plan.cache_directory,
                plan.temp_directory,
                plan.report_directory,
                plan.authorization_receipt,
            )
        ):
            raise ResolveAuthorizationError("RESOLVE_ONE_TIME_PLAN_ALREADY_USED")

        self.paths.ensure_report_directory(self.fs)
        self.fs.mkdir(self.paths.cache)
        self.fs.mkdir(self.paths.temp)
        self.fs.mkdir(plan.cache_directory)
        self.fs.mkdir(plan.temp_directory)
        self.fs.mkdir(plan.report_directory)
        if hashlib.sha256(
            self.fs.read_text(
                self.requirements,
                max_chars=4_000_000,
            ).encode("utf-8")
        ).hexdigest() != plan.requirements_sha256:
            raise ResolveAuthorizationError("RESOLVE_REQUIREMENTS_CHANGED_AFTER_PLAN")
        if hashlib.sha256(
            self.fs.read_text(
                self.launch_utils,
                max_chars=4_000_000,
            ).encode("utf-8")
        ).hexdigest() != plan.launcher_source_sha256:
            raise ResolveAuthorizationError(
                "RESOLVE_LAUNCHER_SOURCE_CHANGED_AFTER_PLAN"
            )
        if self._tool_manifest() != plan.tool_manifest:
            raise ResolveAuthorizationError("RESOLVE_TOOL_CHANGED_AFTER_PLAN")
        if self._execution_manifest() != plan.execution_manifest:
            raise ResolveAuthorizationError(
                "RESOLVE_EXECUTION_IDENTITY_CHANGED_AFTER_PLAN"
            )
        if _read_repository_head(self.fs, self.app_root) != plan.repository_head:
            raise ResolveAuthorizationError(
                "RESOLVE_REPOSITORY_HEAD_CHANGED_AFTER_PLAN"
            )
        try:
            current_policy = FilesystemPolicyContract.current(self.fs)
        except ContractError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_CHANGED_AFTER_PLAN"
            ) from exc
        if current_policy.to_dict() != dict(plan.filesystem_policy):
            raise ResolveAuthorizationError(
                "RESOLVE_FILESYSTEM_POLICY_CHANGED_AFTER_PLAN"
            )
        remaining_authorization_seconds = (
            authorization.expires_at_utc - datetime.now(timezone.utc)
        ).total_seconds()
        if remaining_authorization_seconds <= authorization.timeout_seconds:
            raise ResolveAuthorizationError(
                "RESOLVE_AUTHORIZATION_WINDOW_INSUFFICIENT_BEFORE_PROCESS"
            )

        combined = plan.requirements_text.rstrip() + "\n"
        combined += "\n".join(plan.launcher_requirements) + "\n"
        self.fs.write_text(plan.combined_requirements, combined)
        self.fs.write_text(plan.netrc_file, "\n")
        checked_python = self.fs.boundary.authorize(
            self.venv_python, "resolve-executable-recheck"
        )
        if (
            not self.fs.is_file(checked_python)
            or os.path.normcase(os.fspath(checked_python))
            != os.path.normcase(plan.argv[0])
        ):
            raise ResolveAuthorizationError("RESOLVE_EXECUTABLE_CHANGED")
        for path in (
            plan.cwd,
            plan.cache_directory,
            plan.report_directory,
            plan.combined_requirements,
            plan.netrc_file,
            plan.pip_report,
            plan.stdout_report,
            plan.stderr_report,
            plan.authorization_receipt,
        ):
            self.fs.boundary.authorize(path, "resolve-process-path-recheck")
        protected_before = self._protected_manifest(plan)
        tool_before = self._tool_manifest()
        execution_before = self._execution_manifest()

        attempt = {
            "schema_version": "forge-resolve-attempt/v1",
            "plan_digest": plan.digest,
            "dependency_plan_sha256": plan.dependency_plan_sha256,
            "repository_head": plan.repository_head,
            "run_id": plan.run_id,
            "filesystem_policy": dict(plan.filesystem_policy),
            "process_attempted": True,
            "network_attempted": True,
            "completed": False,
            "returncode": None,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "protected_state_unchanged": None,
            "tool_state_unchanged": None,
            "execution_identity_unchanged": None,
        }
        # This legacy execution path remains unreachable in Stage E0. If a
        # later owner-approved design enables it, reserve its one-time marker
        # only after every final recheck and immediately before the process
        # operation. A failed process start remains consumed.
        try:
            self.fs.create_json_exclusive(
                plan.authorization_receipt,
                {
                    "schema_version": "forge-resolve-authorization-use/v1",
                    "plan_digest": plan.digest,
                    "dependency_plan_sha256": plan.dependency_plan_sha256,
                    "repository_head": plan.repository_head,
                    "run_id": plan.run_id,
                    "state": (
                        "consumed-immediately-before-network-operation"
                    ),
                    "authorization_kind": (
                        "consumption-tombstone-not-authorization"
                    ),
                    "filesystem_policy": dict(plan.filesystem_policy),
                },
            )
        except FileExistsError as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_ONE_TIME_PLAN_ALREADY_USED"
            ) from exc
        try:
            result = self.runner.run(
                plan.argv,
                cwd=plan.cwd,
                environment=plan.environment,
                timeout_seconds=authorization.timeout_seconds,
                max_output_chars=authorization.max_output_chars,
            )
        except Exception as exc:
            attempt.update(
                {
                    "completed": False,
                    "error": type(exc).__name__,
                    "protected_state_unchanged": (
                        protected_before == self._protected_manifest(plan)
                    ),
                    "tool_state_unchanged": tool_before == self._tool_manifest(),
                    "execution_identity_unchanged": (
                        execution_before == self._execution_manifest()
                    ),
                }
            )
            self.fs.write_json(plan.attempt_manifest, attempt)
            raise ResolverExecutionError(
                "PIP_DRY_RUN_PROCESS_START_FAILED"
            ) from exc
        stdout = sanitize_text(result.stdout)
        stderr = sanitize_text(result.stderr)
        stdout_truncated = result.stdout_truncated
        stderr_truncated = result.stderr_truncated
        self.fs.write_text(
            plan.stdout_report, stdout[: authorization.max_output_chars]
        )
        self.fs.write_text(
            plan.stderr_report, stderr[: authorization.max_output_chars]
        )
        attempt.update(
            {
                "completed": not result.timed_out,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        )
        protected_unchanged = protected_before == self._protected_manifest(plan)
        tool_unchanged = tool_before == self._tool_manifest()
        execution_unchanged = execution_before == self._execution_manifest()
        attempt["protected_state_unchanged"] = protected_unchanged
        attempt["tool_state_unchanged"] = tool_unchanged
        attempt["execution_identity_unchanged"] = execution_unchanged
        self.fs.write_json(plan.attempt_manifest, attempt)
        if not (protected_unchanged and tool_unchanged and execution_unchanged):
            raise ResolverExecutionError("RESOLVE_PROTECTED_STATE_CHANGED")
        if result.timed_out:
            raise ResolverExecutionError("PIP_DRY_RUN_TIMEOUT")
        if result.returncode != 0:
            raise ResolverExecutionError(
                f"PIP_DRY_RUN_FAILED:{result.returncode}"
            )
        report = self.consume_pip_report(plan)
        if report["decision"] != "GO":
            blocked_decision = "NO_GO"
            return {
                "schema_version": "forge-preflight-report/v2",
                "mode": "RESOLVE",
                "decision": blocked_decision,
                "contract_decision": blocked_decision,
                "effective_decision": blocked_decision,
                "reason_code": report["reason_code"],
                "filesystem_policy": dict(plan.filesystem_policy),
                "network_access": True,
                "process_executed": True,
                "package_mutation": False,
                "pip_report": report,
            }
        return {
            "schema_version": "forge-preflight-report/v2",
            "mode": "RESOLVE",
            "decision": "GO",
            "contract_decision": "GO",
            "effective_decision": "GO",
            "reason_code": "PIP_DRY_RUN_REPORT_CONSUMED",
            "filesystem_policy": dict(plan.filesystem_policy),
            "network_access": True,
            "process_executed": True,
            "package_mutation": False,
            "pip_report": report,
        }

    def _tool_manifest(self) -> dict[str, str]:
        root = self.app_root / "scripts" / "preflight"
        names = (
            "__init__.py",
            "__main__.py",
            "bootstrap.py",
            "boundary.py",
            "cli.py",
            "constraints.py",
            "contracts.py",
            "inspection.py",
            "orchestrator.py",
            "pip_bootstrap.py",
            "reports.py",
            "resolve_authorization.py",
            "resolver.py",
        )
        return {
            name: hashlib.sha256(
                self.fs.read_text(root / name).encode("utf-8")
            ).hexdigest()
            for name in names
        }

    def _execution_manifest(self) -> dict[str, Any]:
        """Bind the exact workspace Python, pip, and packaging implementations."""

        site_packages = self.app_root / "venv" / "Lib" / "site-packages"
        pip_package = site_packages / "pip"
        packaging_package = site_packages / "packaging"
        pip_dist_info = self.fs.find_child(
            site_packages,
            prefix="pip-",
            suffix=".dist-info",
        )
        packaging_dist_info = self.fs.find_child(
            site_packages,
            prefix="packaging-",
            suffix=".dist-info",
        )
        python_bytes = self.fs.read_bytes(
            self.venv_python,
            max_bytes=64_000_000,
        )
        pip_tree = self._tree_digest(
            (
                pip_package,
                pip_dist_info / "METADATA",
                pip_dist_info / "RECORD",
                pip_dist_info / "WHEEL",
                pip_dist_info / "entry_points.txt",
            ),
            root_label="app/venv/Lib/site-packages/pip",
        )
        packaging_tree = self._tree_digest(
            (
                packaging_package,
                packaging_dist_info / "METADATA",
                packaging_dist_info / "RECORD",
                packaging_dist_info / "WHEEL",
            ),
            root_label="app/venv/Lib/site-packages/packaging",
        )
        return {
            "python": {
                "path": "app/venv/Scripts/python.exe",
                "sha256": hashlib.sha256(python_bytes).hexdigest(),
                "size": len(python_bytes),
            },
            "pip": pip_tree,
            "packaging": packaging_tree,
            "base_interpreter_inspected": False,
            "automatic_site_processing": False,
        }

    def _tree_digest(
        self,
        roots: Sequence[Path],
        *,
        root_label: str,
    ) -> dict[str, Any]:
        records: list[tuple[str, str, int]] = []
        total_bytes = 0

        def visit(path: Path) -> None:
            nonlocal total_bytes
            if self.fs.is_file(path):
                content = self.fs.read_bytes(path, max_bytes=16_000_000)
                total_bytes += len(content)
                if total_bytes > 256_000_000:
                    raise ResolveAuthorizationError(
                        "RESOLVE_EXECUTION_MANIFEST_SIZE_LIMIT_EXCEEDED"
                    )
                records.append(
                    (
                        self.fs.boundary.report_path(path),
                        hashlib.sha256(content).hexdigest(),
                        len(content),
                    )
                )
                if len(records) > 10_000:
                    raise ResolveAuthorizationError(
                        "RESOLVE_EXECUTION_MANIFEST_FILE_LIMIT_EXCEEDED"
                    )
                return
            for child in self.fs.list_children(path):
                visit(child)

        for root in roots:
            visit(root)
        encoded = json.dumps(
            records,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "root": root_label,
            "file_count": len(records),
            "total_bytes": total_bytes,
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def consume_pip_report(
        self,
        plan: ResolverPlan,
    ) -> dict[str, Any]:
        return consume_pip_report(
            self.fs,
            context=PipReportContext.from_plan(plan),
        )

    def _protected_manifest(self, plan: ResolverPlan) -> dict[str, str]:
        site_packages = self.app_root / "venv" / "Lib" / "site-packages"
        exact_versions: dict[str, str] = {}
        for raw in (
            *plan.requirements_text.splitlines(),
            *plan.launcher_requirements,
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            requirement = Requirement(line)
            name = canonicalize_name(requirement.name)
            if name not in {"gradio", "pillow", "pillow-heif"}:
                continue
            versions = [
                specifier.version
                for specifier in requirement.specifier
                if specifier.operator in {"==", "==="}
                and not specifier.version.endswith(".*")
            ]
            if len(versions) != 1:
                raise ResolveAuthorizationError(
                    f"RESOLVE_PROTECTED_VERSION_NOT_EXACT:{name}"
                )
            exact_versions[name] = versions[0]
        if set(exact_versions) != {"gradio", "pillow", "pillow-heif"}:
            raise ResolveAuthorizationError(
                "RESOLVE_PROTECTED_REQUIREMENT_SET_INCOMPLETE"
            )
        metadata_paths = []
        for name in ("gradio", "pillow", "pillow-heif"):
            distribution_prefix = name.replace("-", "_")
            dist_info = self.fs.find_child(
                site_packages,
                prefix=f"{distribution_prefix}-{exact_versions[name]}",
                suffix=".dist-info",
            )
            metadata_paths.append(dist_info / "METADATA")
        paths = (
            self.requirements,
            self.launch_utils,
            self.app_root / "launch.py",
            self.app_root / "webui.bat",
            *metadata_paths,
        )
        return {
            self.fs.boundary.report_path(path): hashlib.sha256(
                self.fs.read_text(path, max_chars=16_000_000).encode("utf-8")
            ).hexdigest()
            for path in paths
        }


def consume_pip_report(
    fs: WorkspaceFS,
    *,
    context: PipReportContext,
) -> dict[str, Any]:
    """Audit one plan-bound pip report as candidate evidence, never authority."""

    value = fs.read_json(context.report_path, max_chars=32_000_000)
    if not isinstance(value, Mapping):
        raise ResolverExecutionError("PIP_REPORT_MUST_BE_AN_OBJECT")
    source_version = value.get("version")
    if source_version != "1":
        raise ResolverExecutionError("PIP_REPORT_VERSION_UNSUPPORTED")
    installs = value.get("install", [])
    if not isinstance(installs, list):
        raise ResolverExecutionError("PIP_REPORT_INSTALL_MUST_BE_A_LIST")
    environment = value.get("environment")
    if not isinstance(environment, Mapping):
        raise ResolverExecutionError("PIP_REPORT_ENVIRONMENT_REQUIRED")

    base = {
        "schema_version": "pip-report-consumption/v2",
        "source_report_version": source_version,
        "coverage": context.coverage.value,
        "plan_digest": context.plan_digest,
        "filesystem_policy": dict(context.filesystem_policy),
        "selected_replacement": None,
        "recommended_replacement": None,
        "endpoint_scope_enforcement": "post-hoc-report-audit-only",
    }
    missing_environment = [
        key
        for key in REQUIRED_MARKER_ENVIRONMENT_KEYS
        if key not in environment or not isinstance(environment[key], str)
    ]
    if missing_environment:
        return {
            **base,
            "decision": "INCONCLUSIVE",
            "reason_code": "PIP_REPORT_MARKER_ENVIRONMENT_INCOMPLETE",
            "detail": (
                "Structured report marker environment is missing required "
                "string fields: " + ", ".join(sorted(missing_environment))
            ),
            "target_environment": {},
            "observed_report_candidates": [],
            "download_endpoints": [],
            "constraints": [],
            "canonical_constraints": [],
        }
    target_environment = {
        key: environment[key] for key in REQUIRED_MARKER_ENVIRONMENT_KEYS
    }
    expected_environment = {
        key: context.expected_environment.get(key)
        for key in REQUIRED_MARKER_ENVIRONMENT_KEYS
    }
    if target_environment != expected_environment:
        return {
            **base,
            "decision": "INCONCLUSIVE",
            "reason_code": "PIP_REPORT_MARKER_ENVIRONMENT_PLAN_MISMATCH",
            "detail": (
                "The structured report marker environment does not match the "
                "owner-reviewed plan."
            ),
            "target_environment": target_environment,
            "expected_environment": expected_environment,
            "observed_report_candidates": [],
            "download_endpoints": [],
            "constraints": [],
            "canonical_constraints": [],
        }

    candidates: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    download_endpoints: list[dict[str, Any]] = []
    normalized_allowed = {
        endpoint.casefold() for endpoint in context.allowed_endpoints
    }
    for install_index, item in enumerate(installs):
        if not isinstance(item, Mapping):
            raise ResolverExecutionError("PIP_REPORT_INSTALL_ENTRY_INVALID")
        metadata = item.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ResolverExecutionError("PIP_REPORT_METADATA_INVALID")
        name = metadata.get("name")
        raw_version = metadata.get("version")
        if not isinstance(name, str) or not isinstance(raw_version, str):
            raise ResolverExecutionError("PIP_REPORT_METADATA_IDENTITY_REQUIRED")
        normalized_name = canonicalize_name(name)
        try:
            version = str(Version(raw_version))
        except InvalidVersion as exc:
            raise ResolverExecutionError(
                "PIP_REPORT_CANDIDATE_VERSION_INVALID"
            ) from exc
        if normalized_name in candidates:
            return {
                **base,
                "decision": "INCONCLUSIVE",
                "reason_code": "PIP_REPORT_DUPLICATE_CANDIDATE",
                "detail": f"Multiple selected candidates were reported for {normalized_name}.",
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }

        download_info = item.get("download_info")
        if not isinstance(download_info, Mapping) or not isinstance(
            download_info.get("url"), str
        ):
            return {
                **base,
                "decision": "INCONCLUSIVE",
                "reason_code": "PIP_REPORT_DOWNLOAD_ORIGIN_MISSING",
                "detail": (
                    f"Candidate {normalized_name} lacks a structured download URL."
                ),
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }
        archive_info = download_info.get("archive_info")
        archive_hashes = (
            archive_info.get("hashes")
            if isinstance(archive_info, Mapping)
            else None
        )
        sha256_hash = (
            archive_hashes.get("sha256")
            if isinstance(archive_hashes, Mapping)
            else None
        )
        if (
            not isinstance(sha256_hash, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", sha256_hash) is None
        ):
            return {
                **base,
                "decision": "INCONCLUSIVE",
                "reason_code": "PIP_REPORT_ARCHIVE_SHA256_MISSING",
                "detail": (
                    f"Candidate {normalized_name} lacks a valid archive SHA-256."
                ),
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }
        try:
            parsed_download = urlsplit(str(download_info["url"]))
            download_port = parsed_download.port
        except ValueError as exc:
            raise ResolverExecutionError(
                "PIP_REPORT_DOWNLOAD_URL_INVALID"
            ) from exc
        if (
            parsed_download.scheme.casefold() != "https"
            or not parsed_download.hostname
            or parsed_download.username is not None
            or parsed_download.password is not None
        ):
            return {
                **base,
                "decision": "NO_GO",
                "reason_code": "PIP_REPORT_DOWNLOAD_URL_OUTSIDE_HTTPS_SCOPE",
                "detail": (
                    "A candidate download URL was not an unauthenticated HTTPS URL."
                ),
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }
        if parsed_download.query or parsed_download.fragment:
            return {
                **base,
                "decision": "NO_GO",
                "reason_code": "PIP_REPORT_DOWNLOAD_URL_PRIVATE_SUFFIX_PRESENT",
                "detail": (
                    "A candidate download URL contained a query or fragment. "
                    "The raw Evidence report requires sensitive-data review."
                ),
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }
        endpoint = parsed_download.hostname.casefold()
        if download_port is not None:
            endpoint = f"{endpoint}:{download_port}"
        endpoint_observation = {
            "candidate": normalized_name,
            "endpoint": endpoint,
            "archive_sha256": sha256_hash.casefold(),
            "within_declared_scope": endpoint in normalized_allowed,
        }
        download_endpoints.append(endpoint_observation)
        if endpoint not in normalized_allowed:
            return {
                **base,
                "decision": "NO_GO",
                "reason_code": (
                    "PIP_REPORT_DOWNLOAD_ENDPOINT_OUTSIDE_DECLARED_SCOPE"
                ),
                "detail": (
                    f"Candidate {normalized_name} was reported from an endpoint "
                    "outside the owner-declared scope. Detection is post-hoc."
                ),
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": sorted(
                    download_endpoints,
                    key=lambda child: (
                        child["candidate"],
                        child["endpoint"],
                    ),
                ),
                "constraints": [],
                "canonical_constraints": [],
            }

        raw_requirements = metadata.get("requires_dist", []) or []
        if not isinstance(raw_requirements, list) or not all(
            isinstance(raw, str) for raw in raw_requirements
        ):
            raise ResolverExecutionError(
                "PIP_REPORT_REQUIRES_DIST_MUST_BE_A_STRING_LIST"
            )
        parsed_requirements: list[tuple[int, str, Requirement, int]] = []
        occurrence_by_requirement: dict[str, int] = {}
        for source_index, raw in enumerate(raw_requirements):
            try:
                requirement = Requirement(raw)
            except (InvalidRequirement, InvalidMarker) as exc:
                raise ResolverExecutionError(
                    "PIP_REPORT_REQUIREMENT_INVALID"
                ) from exc
            if requirement.url is not None:
                return {
                    **base,
                    "decision": "INCONCLUSIVE",
                    "reason_code": (
                        "PIP_REPORT_DIRECT_URL_REQUIREMENT_UNSUPPORTED"
                    ),
                    "detail": (
                        f"Candidate {normalized_name} declares a direct URL "
                        "requirement whose exact source cannot be proven by "
                        "the constraint engine."
                    ),
                    "target_environment": target_environment,
                    "observed_report_candidates": observations,
                    "download_endpoints": download_endpoints,
                    "constraints": [],
                    "canonical_constraints": [],
                }
            normalized_raw = str(requirement)
            occurrence = occurrence_by_requirement.get(normalized_raw, 0)
            occurrence_by_requirement[normalized_raw] = occurrence + 1
            parsed_requirements.append(
                (source_index, raw, requirement, occurrence)
            )
        candidates[normalized_name] = {
            "version": version,
            "requested": bool(item.get("requested", False)),
            "install_index": install_index,
            "requirements": tuple(parsed_requirements),
        }
        observations.append(
            {
                "name": normalized_name,
                "version": version,
                "requested": bool(item.get("requested", False)),
            }
        )

    try:
        root_constraints = _root_constraints(context)
    except (InvalidRequirement, InvalidMarker, ValueError) as exc:
        return {
            **base,
            "decision": "INCONCLUSIVE",
            "reason_code": "PIP_REPORT_ROOT_REQUIREMENTS_UNSUPPORTED",
            "detail": sanitize_text(str(exc)),
            "target_environment": target_environment,
            "observed_report_candidates": sorted(
                observations, key=lambda child: (child["name"], child["version"])
            ),
            "download_endpoints": sorted(
                download_endpoints,
                key=lambda child: (child["candidate"], child["endpoint"]),
            ),
            "constraints": [],
            "canonical_constraints": [],
        }

    # Seed only active trusted roots, then traverse active dependency edges.
    selected_extras: dict[str, set[str]] = {}
    reachable: set[str] = set()
    for root_constraint in root_constraints:
        root_requirement = root_constraint.parsed
        active_contexts = _active_extra_contexts(
            root_requirement,
            target_environment,
            set(),
        )
        if not active_contexts:
            continue
        root_name = root_constraint.normalized_dependency
        reachable.add(root_name)
        selected_extras.setdefault(root_name, set()).update(
            str(extra) for extra in root_requirement.extras
        )

    changed = True
    iterations = 0
    while changed:
        changed = False
        iterations += 1
        if iterations > max(4, len(candidates) * 4):
            return {
                **base,
                "decision": "INCONCLUSIVE",
                "reason_code": "PIP_REPORT_EXTRA_PROPAGATION_DID_NOT_CONVERGE",
                "detail": "Selected-extra propagation exceeded its deterministic bound.",
                "target_environment": target_environment,
                "observed_report_candidates": observations,
                "download_endpoints": download_endpoints,
                "constraints": [],
                "canonical_constraints": [],
            }
        for parent_name in sorted(reachable):
            if parent_name not in candidates:
                continue
            parent_extras = selected_extras.get(parent_name, set())
            for _, _, requirement, _ in candidates[parent_name]["requirements"]:
                active_contexts = _active_extra_contexts(
                    requirement,
                    target_environment,
                    parent_extras,
                )
                if not active_contexts:
                    continue
                child_name = canonicalize_name(requirement.name)
                if child_name not in reachable:
                    reachable.add(child_name)
                    changed = True
                child_extras = selected_extras.setdefault(child_name, set())
                before = len(child_extras)
                child_extras.update(str(extra) for extra in requirement.extras)
                changed = changed or len(child_extras) != before

    constraints: list[DependencyConstraint] = list(root_constraints)
    for parent_name in sorted(candidates):
        candidate = candidates[parent_name]
        parent_extras = selected_extras.get(parent_name, set())
        for source_index, raw, requirement, occurrence in candidate["requirements"]:
            active_contexts = (
                _active_extra_contexts(
                    requirement,
                    target_environment,
                    parent_extras,
                )
                if parent_name in reachable
                else ()
            )
            normalized_requirement = str(requirement)
            content_digest = hashlib.sha256(
                normalized_requirement.encode("utf-8")
            ).hexdigest()[:12]
            dependency = canonicalize_name(requirement.name)
            constraints.append(
                DependencyConstraint(
                    constraint_id=(
                        f"pip-report:{parent_name}:{candidate['version']}:"
                        f"{dependency}:{content_digest}:{occurrence}"
                    ),
                    dependency=dependency,
                    raw_requirement=normalized_requirement,
                    owner={
                        "kind": "pip_report_candidate",
                        "name": parent_name,
                        "version": candidate["version"],
                        "display_name": (
                            f"{parent_name} {candidate['version']} "
                            "pip report candidate"
                        ),
                    },
                    source={
                        "kind": "pip-structured-dry-run-report",
                        "install_index": candidate["install_index"],
                        "field": (
                            "install["
                            f"{candidate['install_index']}].metadata."
                            f"requires_dist[{source_index}]"
                        ),
                        "original_requirement": raw,
                    },
                    mandatory=bool(active_contexts),
                    marker_environment=(
                        {"extra": active_contexts[0]}
                        if requirement.marker is not None and active_contexts
                        else None
                    ),
                )
            )

    observed_versions = {
        name: candidate["version"] for name, candidate in candidates.items()
    }
    decision = ConstraintDecisionEngine().evaluate(
        constraints,
        target_environment={**target_environment, "extra": ""},
        observed_versions=observed_versions,
        require_observed_versions=True,
    )
    if decision.decision == "GO":
        unexpected_candidates = sorted(set(candidates) - reachable)
        if unexpected_candidates:
            decision = ConstraintDecision(
                decision="INCONCLUSIVE",
                reason_code="PIP_REPORT_UNJUSTIFIED_SELECTED_CANDIDATE",
                dependency=unexpected_candidates[0],
                constraints=decision.constraints,
                detail=(
                    "The report selected candidates with no active trusted "
                    "root or dependency requirement: "
                    + ", ".join(unexpected_candidates)
                ),
            )
    ordered_constraints = sorted(
        constraints,
        key=lambda item: (
            item.constraint_id.casefold(),
            item.constraint_id,
            item.raw_requirement,
        ),
    )
    return {
        **base,
        "decision": decision.decision,
        "reason_code": decision.reason_code,
        "detail": decision.detail,
        "target_environment": target_environment,
        "selected_extras": {
            name: sorted(extras)
            for name, extras in sorted(selected_extras.items())
            if extras
        },
        "observed_report_candidates": sorted(
            observations, key=lambda child: (child["name"], child["version"])
        ),
        "download_endpoints": sorted(
            download_endpoints,
            key=lambda child: (child["candidate"], child["endpoint"]),
        ),
        "dependency_decision": decision.to_dict(),
        "constraints": [
            constraint.to_result_dict() for constraint in ordered_constraints
        ],
        "canonical_constraints": [
            constraint.to_fixture_dict() for constraint in ordered_constraints
        ],
    }


def _validate_resolver_inputs(
    requirements_text: str,
    launcher_requirements: Sequence[str],
) -> None:
    """Reject pip directives and direct references before any future network."""

    for line_number, raw_line in enumerate(
        requirements_text.splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "--")):
            raise ResolveAuthorizationError(
                "RESOLVE_REQUIREMENTS_DIRECTIVE_UNSUPPORTED:"
                f"requirements.txt:{line_number}"
            )
        try:
            requirement = Requirement(line)
        except (InvalidRequirement, InvalidMarker) as exc:
            raise ResolveAuthorizationError(
                "RESOLVE_REQUIREMENT_INVALID:"
                f"requirements.txt:{line_number}"
            ) from exc
        if requirement.url is not None:
            raise ResolveAuthorizationError(
                "RESOLVE_DIRECT_URL_REQUIREMENT_UNSUPPORTED:"
                f"requirements.txt:{line_number}"
            )

    for index, raw in enumerate(launcher_requirements):
        try:
            requirement = Requirement(raw)
        except (InvalidRequirement, InvalidMarker) as exc:
            raise ResolveAuthorizationError(
                f"RESOLVE_LAUNCHER_REQUIREMENT_INVALID:{index}"
            ) from exc
        if requirement.url is not None:
            raise ResolveAuthorizationError(
                f"RESOLVE_LAUNCHER_DIRECT_URL_UNSUPPORTED:{index}"
            )


def _root_constraints(
    context: PipReportContext,
) -> tuple[DependencyConstraint, ...]:
    constraints: list[DependencyConstraint] = []

    for line_number, raw_line in enumerate(
        context.requirements_text.splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "--")):
            raise ValueError(
                f"Unsupported repository requirement directive at line {line_number}"
            )
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        constraints.append(
            DependencyConstraint(
                constraint_id=f"repository:{name}:requirements.txt:{line_number}",
                dependency=name,
                raw_requirement=str(requirement),
                owner={
                    "kind": "repository_direct_requirement",
                    "name": name,
                    "display_name": (
                        f"repository requirements.txt:{line_number}"
                    ),
                },
                source={
                    "path": "app/requirements.txt",
                    "line": line_number,
                },
                direct=True,
                mandatory=True,
                marker_environment=(
                    {"extra": ""} if requirement.marker is not None else None
                ),
            )
        )

    for index, raw in enumerate(context.launcher_requirements):
        requirement = Requirement(raw)
        name = canonicalize_name(requirement.name)
        constraints.append(
            DependencyConstraint(
                constraint_id=f"launcher:{name}:GRADIO_PACKAGE:{index}",
                dependency=name,
                raw_requirement=str(requirement),
                owner={
                    "kind": "launcher_requirement",
                    "name": name,
                    "display_name": "modules.launch_utils GRADIO_PACKAGE",
                },
                source={
                    "path": "app/modules/launch_utils.py",
                    "function": "prepare_environment",
                    "setting": "GRADIO_PACKAGE",
                    "item": index,
                },
                direct=True,
                mandatory=True,
                marker_environment=(
                    {"extra": ""} if requirement.marker is not None else None
                ),
            )
        )
    return tuple(constraints)


def _active_extra_contexts(
    requirement: Requirement,
    target_environment: Mapping[str, str],
    selected_extras: set[str],
) -> tuple[str, ...]:
    contexts = tuple(sorted(selected_extras)) or ("",)
    if requirement.marker is None:
        return contexts
    active: list[str] = []
    for extra in contexts:
        environment = {**target_environment, "extra": extra}
        if requirement.marker.evaluate(environment=environment):
            active.append(extra)
    return tuple(active)
