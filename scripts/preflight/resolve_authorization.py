"""Deterministic ResolvePlan artifacts and validation-only owner receipts.

This module deliberately contains no network client, package operation, or
process runner.  A structurally valid receipt can be reviewed as data, but the
result never authorizes execution.  Executable Resolve and the independent pip
bootstrap gate remain disabled elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import Parser
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit
import zlib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import __version__ as PREFLIGHT_VERSION
from .boundary import BoundaryViolation, WorkspaceBoundary, WorkspaceFS
from .contracts import ContractError, FilesystemPolicyContract
from .inspection import StaticInspector, launcher_requirements_from_text
from .resolver import Resolver, ResolverPaths, _read_repository_head


PLAN_SCHEMA = "forge-resolve-plan/v1"
AUTHORIZATION_SCHEMA = "forge-resolve-authorization/v1"
VALIDATION_SCHEMA = "forge-resolve-authorization-validation/v1"
ARTIFACT_NAMES = (
    "resolve-plan.json",
    "resolve-plan.md",
    "authorization-request.json",
    "authorization-request.md",
    "dependency-inputs.json",
    "source-bindings.json",
    "privacy-review.md",
    "tool-version.txt",
)
APPROVED_OPERATIONS = frozenset({"CANDIDATE_METADATA", "PIP_DRY_RUN"})
APPROVED_ROOTS = {
    "cache": "Evidence/preflight/cache",
    "temp": "Evidence/preflight/temp",
    "reports": "Evidence/preflight/reports",
}
MAX_CANDIDATES = 50
MAX_AUTHORIZATION_SECONDS = 86_400
OWNER_STATEMENT_PREFIX = "HUMAN OWNER AUTHORIZATION:"
AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "issued_by",
        "issued_at",
        "expires_at",
        "single_use",
        "repository_head",
        "plan_sha256",
        "filesystem_policy_sha256",
        "approved_hosts",
        "approved_cache_root",
        "approved_temp_root",
        "approved_report_root",
        "approved_operations",
        "candidate_scope",
        "allow_install",
        "allow_uninstall",
        "allow_upgrade",
        "allow_launch",
        "allow_model_download",
        "allow_extension_update",
        "owner_statement",
    }
)
UNSAFE_PERMISSION_FIELDS = (
    "allow_install",
    "allow_uninstall",
    "allow_upgrade",
    "allow_launch",
    "allow_model_download",
    "allow_extension_update",
)
LOGICAL_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "tool_version",
        "mode",
        "plan_status",
        "authorization_status",
        "decision",
        "effective_decision",
        "reason_code",
        "authorization_eligible",
        "authorization_blockers",
        "repository",
        "filesystem_policy",
        "dependency",
        "runtime",
        "scope",
        "future_pip_dry_run",
        "candidate_metadata_report",
        "capabilities",
        "dependency_inputs_sha256",
        "source_bindings_sha256",
    }
)
REPOSITORY_BINDING_FIELDS = frozenset(
    {
        "head",
        "active_branch",
        "clean_worktree",
        "clean_worktree_state",
        "dirty_bound_paths",
        "staged_changes",
        "untracked_files",
        "complete_git_status",
        "index_matches_head",
        "worktree_matches_index",
        "untracked_scan_complete",
        "index_sha256",
        "ignore_rules_sha256",
        "snapshot_rechecked",
        "neo_ref",
        "upstream_neo_ref",
        "neo_parity_left",
        "neo_parity_right",
        "neo_parity_state",
    }
)
FILESYSTEM_POLICY_FIELDS = frozenset(
    {
        "filesystem_policy_version",
        "normalized_permitted_roots",
        "private_local_status",
        "policy_source",
        "policy_source_sha256",
        "outside_access_allowed",
    }
)
DEPENDENCY_BINDING_FIELDS = frozenset(
    {
        "decision",
        "reason_code",
        "decision_sha256",
        "conflict_core",
        "selected_replacement",
        "recommended_replacement",
    }
)
RUNTIME_BINDING_FIELDS = frozenset(
    {
        "python_path",
        "python_version",
        "pip_version",
        "packaging_version",
        "marker_environment_sha256",
    }
)
SCOPE_FIELDS = frozenset(
    {
        "approved_cache_root",
        "approved_temp_root",
        "approved_report_root",
        "requested_package_indexes",
        "requested_hosts",
        "network_operation_classes",
        "candidate_scope_status",
        "candidate_scope",
        "no_install_policy",
        "no_launch_policy",
        "no_model_download_policy",
        "single_use_required",
        "maximum_validity_seconds",
    }
)
CAPABILITY_FIELDS = frozenset(
    {
        "outside_filesystem_access",
        "private_local_access",
        "network_access",
        "process_execution",
        "package_mutation",
        "application_launch",
        "model_loading",
        "model_download",
        "generation",
        "extension_update",
    }
)
FUTURE_PIP_FIELDS = frozenset(
    {"required_operation", "argv_shape", "environment", "enabled"}
)
FUTURE_PIP_ARGV_SHAPE = (
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
    "Evidence/preflight/reports/<RUN_ID>/pip-report.json",
    "--index-url",
    "<OWNER_APPROVED_INDEX>",
    "-r",
    "Evidence/preflight/temp/<RUN_ID>/combined-requirements.txt",
)
FUTURE_PIP_ENVIRONMENT = {
    "PIP_CACHE_DIR": APPROVED_ROOTS["cache"],
    "TEMP": APPROVED_ROOTS["temp"],
    "TMP": APPROVED_ROOTS["temp"],
    "PIP_CONFIG_FILE": "NUL",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INPUT": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
CANDIDATE_REPORT_FIELDS = (
    "package",
    "version",
    "python_requirement",
    "requires_dist",
    "pillow_constraint",
    "compatible_with_pillow_12_3_0",
    "compatible_with_python_3_13_5",
    "source_hostname",
    "metadata_url_sanitized",
    "artifact_type",
    "yanked",
    "compatibility_result",
    "rejection_reasons",
)
CANDIDATE_REPORT_BINDING_FIELDS = frozenset(
    {"fields", "automatic_selection", "automatic_recommendation"}
)
DEPENDENCY_INPUT_FIELDS = frozenset(
    {
        "schema_version",
        "repository_requirements",
        "launcher_requirements",
        "normalized_dependency_decision",
        "dependency_decision_sha256",
        "candidate_scope",
        "candidate_research_question",
        "selected_candidate",
        "recommended_candidate",
    }
)
NORMALIZED_DEPENDENCY_DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "decision",
        "reason_code",
        "dependency",
        "constraints",
        "normalized_intersection",
        "minimal_unsatisfiable_cores",
        "minimal_core_enumeration_complete",
        "observed_version",
        "selected_replacement",
        "recommended_replacement",
        "next_action",
        "detail",
    }
)
DEPENDENCY_CONSTRAINT_FIELDS = frozenset(
    {
        "constraint_id",
        "dependency",
        "owner",
        "requirement",
        "source",
    }
)
EXPECTED_STAGE_E0_CONSTRAINT_IDS = frozenset(
    {
        "repository:Pillow:requirements.txt:2",
        "distribution:gradio:4.40.0:pillow",
        "distribution:pillow-heif:1.4.0:pillow",
    }
)
EXPECTED_STAGE_E0_CONFLICT_CORES = (
    (
        "distribution:gradio:4.40.0:pillow",
        "distribution:pillow-heif:1.4.0:pillow",
    ),
    (
        "distribution:gradio:4.40.0:pillow",
        "repository:Pillow:requirements.txt:2",
    ),
)
EXPECTED_STAGE_E0_CONSTRAINTS = {
    "distribution:gradio:4.40.0:pillow": {
        "constraint_id": "distribution:gradio:4.40.0:pillow",
        "dependency": "pillow",
        "owner": "gradio 4.40.0",
        "requirement": "pillow<11.0,>=8.0",
        "source": {
            "field": "Requires-Dist",
            "path": (
                "app/venv/Lib/site-packages/"
                "gradio-4.40.0.dist-info/METADATA"
            ),
        },
    },
    "distribution:pillow-heif:1.4.0:pillow": {
        "constraint_id": "distribution:pillow-heif:1.4.0:pillow",
        "dependency": "pillow",
        "owner": "pillow-heif 1.4.0",
        "requirement": "pillow>=11.1.0",
        "source": {
            "field": "Requires-Dist",
            "path": (
                "app/venv/Lib/site-packages/"
                "pillow_heif-1.4.0.dist-info/METADATA"
            ),
        },
    },
    "repository:Pillow:requirements.txt:2": {
        "constraint_id": "repository:Pillow:requirements.txt:2",
        "dependency": "pillow",
        "owner": "repository requirements.txt:2",
        "requirement": "Pillow==12.3.0",
        "source": {
            "line": 2,
            "path": "app/requirements.txt",
        },
    },
}
SOURCE_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "filesystem_policy",
        "filesystem_policy_sha256",
        "requirements",
        "launch_utils",
        "preflight_tool",
        "python",
        "pip",
        "packaging",
        "marker_environment",
        "marker_environment_sha256",
        "external_base_interpreter_inspected",
        "external_command_processor_inspected",
    }
)
PREFLIGHT_TOOL_MANIFEST_NAMES = frozenset(
    {
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
    }
)
MARKER_ENVIRONMENT_FIELDS = frozenset(
    {
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
        "extra",
    }
)
AUTHORIZATION_ELIGIBILITY_BLOCKERS = frozenset(
    {
        "RESOLVE_PLAN_DIRTY_WORKTREE",
        "RESOLVE_PLAN_NEO_PARITY_NOT_ESTABLISHED",
    }
)
MAX_GIT_INDEX_BYTES = 64_000_000
MAX_GIT_ATTRIBUTES_BYTES = 4_000_000
MAX_GIT_OBJECT_BYTES = 16_000_000
MAX_GIT_ENTRIES = 1_000_000
SUPPORTED_GIT_INDEX_MODES = frozenset({0o100644, 0o100755})


class ResolveAuthorizationValidationError(ValueError):
    """Stable fail-closed error used by E0 planning and validation."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical JSON serialization used for all digests."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        ) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def pretty_json(value: Any) -> str:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        ) from exc


def strict_json_from_bytes(
    payload: bytes,
    *,
    reason_code: str,
    max_bytes: int = 1_000_000,
) -> Any:
    if len(payload) > max_bytes:
        raise ResolveAuthorizationValidationError(reason_code)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResolveAuthorizationValidationError(reason_code) from exc

    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ResolveAuthorizationValidationError(reason_code)
            value[key] = child
        return value

    def reject_nonfinite(_value: str) -> None:
        raise ResolveAuthorizationValidationError(reason_code)

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
        )
    except ResolveAuthorizationValidationError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ResolveAuthorizationValidationError(reason_code) from exc


def _require_exact_mapping(
    value: Any,
    keys: set[str] | frozenset[str],
    reason_code: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ResolveAuthorizationValidationError(reason_code)
    if not all(isinstance(key, str) for key in value):
        raise ResolveAuthorizationValidationError(reason_code)
    return value


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def validate_candidate_scope(value: Any) -> dict[str, Any]:
    """Validate and canonicalize one bounded, owner-supplied Gradio scope."""

    if not isinstance(value, Mapping):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
        )
    keys = set(value)
    explicit_keys = {"package", "explicit_versions"}
    specifier_keys = {
        "package",
        "version_specifier",
        "maximum_candidate_count",
    }
    if keys == explicit_keys:
        package = value.get("package")
        versions = value.get("explicit_versions")
        if (
            not isinstance(package, str)
            or package.casefold() != "gradio"
            or not isinstance(versions, list)
            or not 1 <= len(versions) <= MAX_CANDIDATES
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        normalized: list[str] = []
        for raw in versions:
            if (
                not isinstance(raw, str)
                or not raw
                or any(token in raw for token in ("@", "/", "\\", ":"))
            ):
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
                )
            try:
                parsed = Version(raw)
            except InvalidVersion as exc:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
                ) from exc
            canonical = str(parsed)
            if canonical != raw:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
                )
            normalized.append(canonical)
        if len(set(normalized)) != len(normalized):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        normalized.sort(key=Version)
        return {
            "package": "gradio",
            "explicit_versions": normalized,
        }

    if keys == specifier_keys:
        package = value.get("package")
        raw_specifier = value.get("version_specifier")
        maximum = value.get("maximum_candidate_count")
        if (
            not isinstance(package, str)
            or package.casefold() != "gradio"
            or not isinstance(raw_specifier, str)
            or not raw_specifier.strip()
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= MAX_CANDIDATES
            or "@" in raw_specifier
            or "://" in raw_specifier
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        try:
            specifier = SpecifierSet(raw_specifier)
        except InvalidSpecifier as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            ) from exc
        members = tuple(specifier)
        if not members:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        lower = any(
            member.operator in {">", ">=", "==", "~="}
            for member in members
        )
        upper = any(
            member.operator in {"<", "<=", "==", "~="}
            for member in members
        )
        if not (lower and upper):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        return {
            "package": "gradio",
            "version_specifier": str(specifier),
            "maximum_candidate_count": maximum,
        }

    raise ResolveAuthorizationValidationError(
        "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
    )


def validate_hosts(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
        )
    normalized: list[str] = []
    for raw in values:
        if (
            not isinstance(raw, str)
            or "*" in raw
            or re.fullmatch(
                r"(?i)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?"
                r"(?::[0-9]{1,5})?",
                raw,
            )
            is None
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
            )
        host = raw.casefold()
        if ":" in host:
            try:
                port = int(host.rsplit(":", 1)[1])
            except ValueError as exc:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
                ) from exc
            if not 1 <= port <= 65_535:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
                )
        normalized.append(host)
    if len(set(normalized)) != len(normalized):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
        )
    return sorted(normalized)


def validate_operations(values: Any) -> list[str]:
    if (
        not isinstance(values, list)
        or any(not isinstance(value, str) for value in values)
        or not set(values).issubset(APPROVED_OPERATIONS)
        or len(set(values)) != len(values)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
        )
    return sorted(values)


def validate_requested_indexes(
    values: Any,
    *,
    hosts: Sequence[str],
) -> list[str]:
    if not isinstance(values, list):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    normalized: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_SCHEMA_INVALID"
            )
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_SCHEMA_INVALID"
            ) from exc
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_SCHEMA_INVALID"
            )
        endpoint = parsed.hostname.casefold()
        if port is not None:
            endpoint = f"{endpoint}:{port}"
        if endpoint not in hosts:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
            )
        normalized.append(raw)
    if len(set(normalized)) != len(normalized):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    return sorted(normalized)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def _logical_schema_error() -> None:
    raise ResolveAuthorizationValidationError(
        "RESOLVE_PLAN_SCHEMA_INVALID"
    )


def _logical_binding_error() -> None:
    raise ResolveAuthorizationValidationError(
        "RESOLVE_PLAN_CROSS_BINDING_MISMATCH"
    )


def _validate_repository_binding(value: Any) -> Mapping[str, Any]:
    repository = _require_exact_mapping(
        value,
        REPOSITORY_BINDING_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    head = repository.get("head")
    branch = repository.get("active_branch")
    dirty_paths = repository.get("dirty_bound_paths")
    untracked_files = repository.get("untracked_files")
    if (
        not isinstance(head, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None
        or not isinstance(branch, str)
        or re.fullmatch(r"[A-Za-z0-9._/-]+", branch) is None
        or ".." in branch.split("/")
        or not isinstance(dirty_paths, list)
        or not isinstance(untracked_files, list)
        or any(
            not isinstance(path, str)
            or not path.startswith("app/")
            or "\\" in path
            or ".." in path.split("/")
            for path in (*dirty_paths, *untracked_files)
        )
        or len(set(dirty_paths)) != len(dirty_paths)
        or len(set(untracked_files)) != len(untracked_files)
    ):
        _logical_schema_error()

    clean = repository.get("clean_worktree")
    clean_state = repository.get("clean_worktree_state")
    staged = repository.get("staged_changes")
    complete = repository.get("complete_git_status")
    index_matches_head = repository.get("index_matches_head")
    worktree_matches_index = repository.get("worktree_matches_index")
    untracked_complete = repository.get("untracked_scan_complete")
    snapshot_rechecked = repository.get("snapshot_rechecked")
    index_sha256 = repository.get("index_sha256")
    ignore_sha256 = repository.get("ignore_rules_sha256")
    if clean is True:
        if (
            clean_state != "CLEAN"
            or dirty_paths
            or staged is not False
            or untracked_files
            or complete is not True
            or index_matches_head is not True
            or worktree_matches_index is not True
            or untracked_complete is not True
            or snapshot_rechecked is not True
            or not _is_sha256(index_sha256)
            or not _is_sha256(ignore_sha256)
        ):
            _logical_schema_error()
    elif clean is False:
        if (
            clean_state
            not in {
                "DIRTY_BOUND_INPUTS",
                "DIRTY_STAGED_CHANGES",
                "DIRTY_UNTRACKED_FILES",
                "DIRTY_REPOSITORY",
            }
            or not (dirty_paths or staged is True or untracked_files)
            or not _is_bool(staged)
            or complete is not True
            or not _is_bool(index_matches_head)
            or not _is_bool(worktree_matches_index)
            or untracked_complete is not True
            or snapshot_rechecked is not True
            or not _is_sha256(index_sha256)
            or not _is_sha256(ignore_sha256)
        ):
            _logical_schema_error()
    elif clean is None:
        if (
            clean_state != "INCONCLUSIVE_GIT_STATE"
            or dirty_paths
            or staged is not None
            or untracked_files
            or complete is not False
            or index_matches_head is not None
            or worktree_matches_index is not None
            or untracked_complete is not False
            or index_sha256 is not None
            or ignore_sha256 is not None
            or snapshot_rechecked is not False
        ):
            _logical_schema_error()
    else:
        _logical_schema_error()

    neo = repository.get("neo_ref")
    upstream_neo = repository.get("upstream_neo_ref")
    for reference in (neo, upstream_neo):
        if reference is not None and (
            not isinstance(reference, str)
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", reference)
            is None
        ):
            _logical_schema_error()
    parity_state = repository.get("neo_parity_state")
    left = repository.get("neo_parity_left")
    right = repository.get("neo_parity_right")
    if parity_state == "PARITY":
        if (
            neo is None
            or upstream_neo is None
            or neo != upstream_neo
            or left != 0
            or right != 0
            or isinstance(left, bool)
            or isinstance(right, bool)
        ):
            _logical_schema_error()
    elif parity_state == "INCONCLUSIVE":
        if left is not None or right is not None:
            _logical_schema_error()
    else:
        _logical_schema_error()
    return repository


def _authorization_blockers(
    repository: Mapping[str, Any],
) -> list[str]:
    """Return the closed, deterministic blockers for owner authorization."""

    blockers: list[str] = []
    if (
        repository.get("clean_worktree") is not True
        or repository.get("clean_worktree_state") != "CLEAN"
        or repository.get("complete_git_status") is not True
        or repository.get("index_matches_head") is not True
        or repository.get("worktree_matches_index") is not True
        or repository.get("untracked_scan_complete") is not True
        or repository.get("staged_changes") is not False
        or repository.get("dirty_bound_paths")
        or repository.get("untracked_files")
        or repository.get("snapshot_rechecked") is not True
    ):
        blockers.append("RESOLVE_PLAN_DIRTY_WORKTREE")
    if (
        repository.get("neo_parity_state") != "PARITY"
        or repository.get("neo_parity_left") != 0
        or repository.get("neo_parity_right") != 0
        or repository.get("neo_ref") is None
        or repository.get("neo_ref")
        != repository.get("upstream_neo_ref")
    ):
        blockers.append("RESOLVE_PLAN_NEO_PARITY_NOT_ESTABLISHED")
    return sorted(blockers)


def _validate_filesystem_policy(value: Any) -> Mapping[str, Any]:
    policy = _require_exact_mapping(
        value,
        FILESYSTEM_POLICY_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        policy.get("filesystem_policy_version")
        != "forge-filesystem-policy/v1"
        or policy.get("normalized_permitted_roots")
        != ["app/", "Evidence/", "Reference/"]
        or policy.get("private_local_status") != "PROHIBITED"
        or policy.get("policy_source") != "app/AGENTS.md"
        or not _is_sha256(policy.get("policy_source_sha256"))
        or policy.get("outside_access_allowed") is not False
    ):
        _logical_schema_error()
    return policy


def _validate_bound_path_hash(
    value: Any,
    *,
    expected_path: str,
) -> None:
    binding = _require_exact_mapping(
        value,
        {"path", "sha256"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        binding.get("path") != expected_path
        or not _is_sha256(binding.get("sha256"))
    ):
        _logical_schema_error()


def _validate_distribution_binding(
    value: Any,
    *,
    expected_root: str,
) -> Mapping[str, Any]:
    distribution = _require_exact_mapping(
        value,
        {"version", "metadata_sha256", "tree"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    tree = _require_exact_mapping(
        distribution.get("tree"),
        {"root", "file_count", "total_bytes", "sha256"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    file_count = tree.get("file_count")
    total_bytes = tree.get("total_bytes")
    if (
        not isinstance(distribution.get("version"), str)
        or not distribution["version"]
        or not _is_sha256(distribution.get("metadata_sha256"))
        or tree.get("root") != expected_root
        or isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count <= 0
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or total_bytes <= 0
        or not isinstance(tree.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", tree["sha256"]) is None
    ):
        _logical_schema_error()
    return distribution


def _validate_stage_e0_dependency_decision(
    value: Any,
) -> Mapping[str, Any]:
    decision = _require_exact_mapping(
        value,
        NORMALIZED_DEPENDENCY_DECISION_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    constraints = decision.get("constraints")
    cores = decision.get("minimal_unsatisfiable_cores")
    observed = decision.get("observed_version")
    if (
        decision.get("schema_version")
        != "dependency-constraint-result/v1"
        or decision.get("decision") != "NO_GO"
        or decision.get("reason_code")
        != "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY"
        or decision.get("dependency") != "pillow"
        or decision.get("normalized_intersection") != "EMPTY"
        or decision.get("minimal_core_enumeration_complete") is not True
        or decision.get("selected_replacement") is not None
        or decision.get("recommended_replacement") is not None
        or decision.get("next_action") != "OWNER_DECISION_REQUIRED"
        or not isinstance(decision.get("detail"), str)
        or not isinstance(constraints, list)
        or len(constraints) != len(EXPECTED_STAGE_E0_CONSTRAINT_IDS)
        or not isinstance(cores, list)
        or any(
            not isinstance(core, list)
            or any(not isinstance(identifier, str) for identifier in core)
            for core in cores
        )
        or tuple(tuple(core) for core in cores)
        != EXPECTED_STAGE_E0_CONFLICT_CORES
        or not isinstance(observed, Mapping)
    ):
        _logical_schema_error()
    observed = _require_exact_mapping(
        observed,
        {"version", "satisfies", "violates"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        observed.get("version") != "12.3.0"
        or observed.get("satisfies")
        != [
            "distribution:pillow-heif:1.4.0:pillow",
            "repository:Pillow:requirements.txt:2",
        ]
        or observed.get("violates")
        != ["distribution:gradio:4.40.0:pillow"]
    ):
        _logical_schema_error()

    identifiers: list[str] = []
    for raw_constraint in constraints:
        constraint = _require_exact_mapping(
            raw_constraint,
            DEPENDENCY_CONSTRAINT_FIELDS,
            "RESOLVE_PLAN_SCHEMA_INVALID",
        )
        identifier = constraint.get("constraint_id")
        if (
            not isinstance(identifier, str)
            or identifier not in EXPECTED_STAGE_E0_CONSTRAINTS
            or dict(constraint)
            != EXPECTED_STAGE_E0_CONSTRAINTS.get(identifier)
        ):
            _logical_schema_error()
        identifiers.append(identifier)
    if frozenset(identifiers) != EXPECTED_STAGE_E0_CONSTRAINT_IDS:
        _logical_schema_error()
    return decision


def validate_logical_plan(
    logical_plan: Any,
    *,
    dependency_inputs: Any,
    source_bindings: Any,
) -> None:
    """Validate the closed E0 plan schema and every duplicated safety fact."""

    plan = _require_exact_mapping(
        logical_plan,
        LOGICAL_PLAN_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    dependencies = _require_exact_mapping(
        dependency_inputs,
        DEPENDENCY_INPUT_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    sources = _require_exact_mapping(
        source_bindings,
        SOURCE_BINDING_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or plan.get("mode") != "RESOLVE_PLAN"
        or plan.get("plan_status") != "PLAN_READY"
        or plan.get("authorization_status")
        != "NETWORK_NOT_AUTHORIZED"
        or plan.get("decision") != "NO_GO"
        or plan.get("effective_decision") != "NO_GO"
        or plan.get("reason_code") != "NETWORK_NOT_AUTHORIZED"
        or plan.get("tool_version") != PREFLIGHT_VERSION
    ):
        _logical_schema_error()
    authorization_eligible = plan.get("authorization_eligible")
    authorization_blockers = plan.get("authorization_blockers")
    if (
        not _is_bool(authorization_eligible)
        or not isinstance(authorization_blockers, list)
        or any(
            not isinstance(blocker, str)
            or blocker not in AUTHORIZATION_ELIGIBILITY_BLOCKERS
            for blocker in authorization_blockers
        )
        or len(set(authorization_blockers)) != len(
            authorization_blockers
        )
        or authorization_blockers != sorted(authorization_blockers)
    ):
        _logical_schema_error()
    if (
        plan.get("dependency_inputs_sha256")
        != canonical_sha256(dependencies)
        or plan.get("source_bindings_sha256")
        != canonical_sha256(sources)
    ):
        _logical_binding_error()

    capabilities = _require_exact_mapping(
        plan.get("capabilities"),
        CAPABILITY_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if any(
        not _is_bool(capabilities[field])
        or capabilities[field] is not False
        for field in CAPABILITY_FIELDS
    ):
        _logical_schema_error()

    repository = _validate_repository_binding(plan.get("repository"))
    source_repository = _validate_repository_binding(
        sources.get("repository")
    )
    policy = _validate_filesystem_policy(
        plan.get("filesystem_policy")
    )
    source_policy = _validate_filesystem_policy(
        sources.get("filesystem_policy")
    )
    if repository != source_repository or policy != source_policy:
        _logical_binding_error()
    expected_blockers = _authorization_blockers(repository)
    if (
        authorization_eligible != (not expected_blockers)
        or authorization_blockers != expected_blockers
    ):
        _logical_binding_error()
    if (
        sources.get("schema_version")
        != "forge-resolve-source-bindings/v1"
        or sources.get("filesystem_policy_sha256")
        != policy.get("policy_source_sha256")
        or sources.get("external_base_interpreter_inspected") is not False
        or sources.get("external_command_processor_inspected") is not False
    ):
        _logical_schema_error()

    _validate_bound_path_hash(
        sources.get("requirements"),
        expected_path="app/requirements.txt",
    )
    _validate_bound_path_hash(
        sources.get("launch_utils"),
        expected_path="app/modules/launch_utils.py",
    )
    tool = _require_exact_mapping(
        sources.get("preflight_tool"),
        {"version", "manifest", "source_sha256"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    manifest = tool.get("manifest")
    if (
        tool.get("version") != plan.get("tool_version")
        or not isinstance(manifest, Mapping)
        or set(manifest) != set(PREFLIGHT_TOOL_MANIFEST_NAMES)
        or any(
            not isinstance(name, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for name, digest in manifest.items()
        )
        or tool.get("source_sha256") != canonical_sha256(manifest)
    ):
        _logical_binding_error()

    python = _require_exact_mapping(
        sources.get("python"),
        {"path", "sha256", "size", "version"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    pip = _validate_distribution_binding(
        sources.get("pip"),
        expected_root="app/venv/Lib/site-packages/pip",
    )
    packaging = _validate_distribution_binding(
        sources.get("packaging"),
        expected_root="app/venv/Lib/site-packages/packaging",
    )
    marker = _require_exact_mapping(
        sources.get("marker_environment"),
        MARKER_ENVIRONMENT_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    python_size = python.get("size")
    if (
        python.get("path") != "app/venv/Scripts/python.exe"
        or not isinstance(python.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", python["sha256"]) is None
        or isinstance(python_size, bool)
        or not isinstance(python_size, int)
        or python_size <= 0
        or not isinstance(python.get("version"), str)
        or not python.get("version")
        or any(
            not isinstance(key, str) or not isinstance(child, str)
            for key, child in marker.items()
        )
        or sources.get("marker_environment_sha256")
        != canonical_sha256(marker)
    ):
        _logical_schema_error()
    runtime = _require_exact_mapping(
        plan.get("runtime"),
        RUNTIME_BINDING_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if runtime != {
        "python_path": python.get("path"),
        "python_version": python.get("version"),
        "pip_version": pip.get("version"),
        "packaging_version": packaging.get("version"),
        "marker_environment_sha256": sources.get(
            "marker_environment_sha256"
        ),
    }:
        _logical_binding_error()

    if (
        dependencies.get("schema_version")
        != "forge-resolve-dependency-inputs/v1"
        or not isinstance(dependencies.get("repository_requirements"), list)
        or not isinstance(dependencies.get("launcher_requirements"), list)
        or not isinstance(
            dependencies.get("candidate_research_question"), str
        )
        or not dependencies.get("candidate_research_question")
        or dependencies.get("selected_candidate") is not None
        or dependencies.get("recommended_candidate") is not None
    ):
        _logical_schema_error()
    normalized_decision = _validate_stage_e0_dependency_decision(
        dependencies.get("normalized_dependency_decision")
    )
    if dependencies.get(
        "dependency_decision_sha256"
    ) != canonical_sha256(normalized_decision):
        _logical_schema_error()
    dependency = _require_exact_mapping(
        plan.get("dependency"),
        DEPENDENCY_BINDING_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    expected_core = normalized_decision.get(
        "minimal_unsatisfiable_cores",
        normalized_decision.get("minimal_unsatisfiable_core"),
    )
    if (
        dependency.get("decision") != "NO_GO"
        or dependency.get("reason_code")
        != "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY"
        or dependency.get("decision_sha256")
        != dependencies.get("dependency_decision_sha256")
        or dependency.get("conflict_core") != expected_core
        or dependency.get("selected_replacement") is not None
        or dependency.get("recommended_replacement") is not None
    ):
        _logical_binding_error()

    scope = _require_exact_mapping(
        plan.get("scope"),
        SCOPE_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        scope.get("approved_cache_root") != APPROVED_ROOTS["cache"]
        or scope.get("approved_temp_root") != APPROVED_ROOTS["temp"]
        or scope.get("approved_report_root") != APPROVED_ROOTS["reports"]
        or scope.get("no_install_policy") is not True
        or scope.get("no_launch_policy") is not True
        or scope.get("no_model_download_policy") is not True
        or scope.get("single_use_required") is not True
        or isinstance(scope.get("maximum_validity_seconds"), bool)
        or scope.get("maximum_validity_seconds")
        != MAX_AUTHORIZATION_SECONDS
    ):
        _logical_schema_error()
    hosts = validate_hosts(scope.get("requested_hosts"))
    operations = validate_operations(
        scope.get("network_operation_classes")
    )
    indexes = validate_requested_indexes(
        scope.get("requested_package_indexes"),
        hosts=hosts,
    )
    if (
        hosts != scope.get("requested_hosts")
        or operations != scope.get("network_operation_classes")
        or indexes != scope.get("requested_package_indexes")
    ):
        _logical_schema_error()
    candidate_status = scope.get("candidate_scope_status")
    candidate_scope = scope.get("candidate_scope")
    dependency_candidate_scope = dependencies.get("candidate_scope")
    if candidate_status == "BOUNDED":
        normalized_scope = validate_candidate_scope(candidate_scope)
        if (
            normalized_scope != candidate_scope
            or normalized_scope != dependency_candidate_scope
        ):
            _logical_binding_error()
    elif candidate_status == "OWNER_INPUT_REQUIRED":
        if candidate_scope != {} or dependency_candidate_scope is not None:
            _logical_binding_error()
    else:
        _logical_schema_error()

    future_pip = _require_exact_mapping(
        plan.get("future_pip_dry_run"),
        FUTURE_PIP_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        future_pip.get("required_operation") != "PIP_DRY_RUN"
        or future_pip.get("argv_shape") != list(FUTURE_PIP_ARGV_SHAPE)
        or future_pip.get("environment") != FUTURE_PIP_ENVIRONMENT
        or future_pip.get("enabled") is not False
    ):
        _logical_schema_error()
    candidate_report = _require_exact_mapping(
        plan.get("candidate_metadata_report"),
        CANDIDATE_REPORT_BINDING_FIELDS,
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    if (
        candidate_report.get("fields")
        != list(CANDIDATE_REPORT_FIELDS)
        or candidate_report.get("automatic_selection") is not False
        or candidate_report.get("automatic_recommendation") is not False
    ):
        _logical_schema_error()


def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        value,
    ) is None:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
        )
    try:
        return datetime.strptime(
            value, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
        ) from exc


def validate_authorization_time_window(
    issued_at_value: Any,
    expires_at_value: Any,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Validate exact UTC timestamps without requiring owner-shaped data."""

    issued_at = _parse_utc_timestamp(issued_at_value)
    expires_at = _parse_utc_timestamp(expires_at_value)
    if now.tzinfo is None:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
        )
    checked_now = now.astimezone(timezone.utc)
    if issued_at > checked_now:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
        )
    if expires_at <= checked_now:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_EXPIRED"
        )
    if (
        expires_at <= issued_at
        or (expires_at - issued_at).total_seconds()
        > MAX_AUTHORIZATION_SECONDS
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
        )
    return issued_at, expires_at


def validate_safe_permissions(value: Mapping[str, Any]) -> None:
    """Require every authority-expanding flag to be the exact false bool."""

    for field in UNSAFE_PERMISSION_FIELDS:
        observed = value.get(field)
        if not _is_bool(observed) or observed is not False:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_UNSAFE_PERMISSION"
            )


def validate_receipt_plan_bindings(
    receipt: Mapping[str, Any],
    bundle: "ResolvePlanBundle",
    boundary: WorkspaceBoundary,
) -> None:
    """Validate plan/host/path/scope equality independent of owner origin."""

    if bundle.logical_plan.get("authorization_eligible") is not True:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE"
        )
    repository = bundle.logical_plan.get("repository")
    policy = bundle.logical_plan.get("filesystem_policy")
    scope = bundle.logical_plan.get("scope")
    if not all(
        isinstance(child, Mapping)
        for child in (repository, policy, scope)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    if receipt.get("repository_head") != repository.get("head"):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_HEAD_MISMATCH"
        )
    if receipt.get("plan_sha256") != bundle.plan_sha256:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_PLAN_MISMATCH"
        )
    if receipt.get(
        "filesystem_policy_sha256"
    ) != policy.get("policy_source_sha256"):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_POLICY_MISMATCH"
        )
    expected_hosts = list(scope.get("requested_hosts", []))
    observed_hosts = validate_hosts(receipt.get("approved_hosts"))
    if observed_hosts != expected_hosts:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED"
        )
    expected_operations = list(
        scope.get("network_operation_classes", [])
    )
    observed_operations = validate_operations(
        receipt.get("approved_operations")
    )
    if observed_operations != expected_operations:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
        )
    expected_candidate = scope.get("candidate_scope")
    if (
        scope.get("candidate_scope_status") != "BOUNDED"
        or not isinstance(expected_candidate, Mapping)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
        )
    observed_candidate = validate_candidate_scope(
        receipt.get("candidate_scope")
    )
    if observed_candidate != dict(expected_candidate):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
        )
    expected_roots = {
        "approved_cache_root": scope.get("approved_cache_root"),
        "approved_temp_root": scope.get("approved_temp_root"),
        "approved_report_root": scope.get("approved_report_root"),
    }
    for field, expected in expected_roots.items():
        observed = receipt.get(field)
        if not isinstance(observed, str):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )
        try:
            checked = boundary.authorize_lexically(
                observed,
                f"resolve-authorization-{field}",
            )
        except BoundaryViolation as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
            ) from exc
        if (
            boundary.report_path(checked) != expected
            or observed.replace("\\", "/") != expected
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
            )


def candidate_metadata_record(
    value: Mapping[str, Any],
    *,
    candidate_scope: Mapping[str, Any],
    approved_hosts: Sequence[str],
    marker_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate one future, already-fetched candidate metadata record.

    This pure adapter performs no fetch and makes no recommendation.
    """

    expected_keys = {
        "package",
        "version",
        "requires_python",
        "requires_dist",
        "source_hostname",
        "metadata_url",
        "artifact_type",
        "yanked",
    }
    candidate = _require_exact_mapping(
        value,
        expected_keys,
        "RESOLVE_CANDIDATE_METADATA_INVALID",
    )
    scope = validate_candidate_scope(candidate_scope)
    package = candidate.get("package")
    raw_version = candidate.get("version")
    if (
        not isinstance(package, str)
        or package.casefold() != "gradio"
        or not isinstance(raw_version, str)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    try:
        version = Version(raw_version)
    except InvalidVersion as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        ) from exc
    if str(version) != raw_version:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    if "explicit_versions" in scope:
        in_scope = raw_version in scope["explicit_versions"]
    else:
        in_scope = version in SpecifierSet(scope["version_specifier"])

    requires_python = candidate.get("requires_python")
    if not isinstance(requires_python, str) or not requires_python:
        python_compatible: bool | None = None
        python_rejection = "REQUIRES_PYTHON_MISSING"
    else:
        try:
            python_specifier = SpecifierSet(requires_python)
        except InvalidSpecifier as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_CANDIDATE_METADATA_INVALID"
            ) from exc
        python_compatible = Version("3.13.5") in python_specifier
        python_rejection = (
            None
            if python_compatible
            else "PYTHON_3_13_5_NOT_SUPPORTED"
        )

    requires_dist = candidate.get("requires_dist")
    if (
        not isinstance(requires_dist, list)
        or any(not isinstance(item, str) for item in requires_dist)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    environment = dict(marker_environment or _marker_environment())
    pillow_requirements: list[Requirement] = []
    for raw_requirement in requires_dist:
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_CANDIDATE_METADATA_INVALID"
            ) from exc
        if requirement.name.casefold().replace("_", "-") != "pillow":
            continue
        if (
            requirement.marker is not None
            and not requirement.marker.evaluate(environment=environment)
        ):
            continue
        pillow_requirements.append(requirement)
    pillow_constraints = sorted(
        {str(requirement.specifier) for requirement in pillow_requirements}
    )
    if not pillow_requirements:
        pillow_compatible: bool | None = None
        pillow_rejection = "PILLOW_CONSTRAINT_MISSING"
    else:
        pillow_compatible = all(
            Version("12.3.0") in requirement.specifier
            for requirement in pillow_requirements
        )
        pillow_rejection = (
            None
            if pillow_compatible
            else "PILLOW_12_3_0_NOT_SUPPORTED"
        )

    hostname = candidate.get("source_hostname")
    approved = validate_hosts(list(approved_hosts))
    if not isinstance(hostname, str):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    normalized_hostname = validate_hosts([hostname])[0]
    host_approved = normalized_hostname in approved
    metadata_url = candidate.get("metadata_url")
    if not isinstance(metadata_url, str):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    try:
        parsed_url = urlsplit(metadata_url)
        parsed_port = parsed_url.port
    except ValueError as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        ) from exc
    if (
        parsed_url.scheme.casefold() != "https"
        or parsed_url.hostname is None
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    parsed_endpoint = parsed_url.hostname.casefold()
    if parsed_port is not None:
        parsed_endpoint = f"{parsed_endpoint}:{parsed_port}"
    if parsed_endpoint != normalized_hostname:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    artifact_type = candidate.get("artifact_type")
    if artifact_type not in {"wheel", "metadata"}:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )
    yanked = candidate.get("yanked")
    if not isinstance(yanked, bool):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_CANDIDATE_METADATA_INVALID"
        )

    rejection_reasons = [
        reason
        for reason in (
            None if in_scope else "VERSION_OUTSIDE_OWNER_SCOPE",
            python_rejection,
            pillow_rejection,
            None if host_approved else "SOURCE_HOST_NOT_APPROVED",
            "CANDIDATE_YANKED" if yanked else None,
        )
        if reason is not None
    ]
    compatibility = (
        "COMPATIBLE"
        if not rejection_reasons
        else (
            "INCONCLUSIVE"
            if any(
                reason
                in {
                    "REQUIRES_PYTHON_MISSING",
                    "PILLOW_CONSTRAINT_MISSING",
                }
                for reason in rejection_reasons
            )
            else "INCOMPATIBLE"
        )
    )
    return {
        "package": "gradio",
        "version": raw_version,
        "python_requirement": requires_python,
        "requires_dist": list(requires_dist),
        "pillow_constraint": (
            " AND ".join(pillow_constraints)
            if pillow_constraints
            else None
        ),
        "compatible_with_pillow_12_3_0": pillow_compatible,
        "compatible_with_python_3_13_5": python_compatible,
        "source_hostname": normalized_hostname,
        "metadata_url_sanitized": (
            f"https://{normalized_hostname}/<redacted-metadata-path>"
        ),
        "artifact_type": artifact_type,
        "yanked": yanked,
        "compatibility_result": compatibility,
        "rejection_reasons": rejection_reasons,
        "selected": False,
        "recommended": False,
    }


@dataclass(frozen=True)
class ResolvePlanBundle:
    plan_id: str
    plan_sha256: str
    directory: Path
    logical_plan: Mapping[str, Any]
    dependency_inputs: Mapping[str, Any]
    source_bindings: Mapping[str, Any]
    artifacts: Mapping[str, str]

    @property
    def plan_json(self) -> Path:
        return self.directory / "resolve-plan.json"

    @property
    def plan_markdown(self) -> Path:
        return self.directory / "resolve-plan.md"


@dataclass(frozen=True)
class AuthorizationValidationResult:
    validation_status: str
    reason_code: str
    plan_id: str | None
    plan_sha256: str | None
    filesystem_policy: Mapping[str, Any] | None
    authorization_id: str | None = None
    authorization_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": VALIDATION_SCHEMA,
            "mode": "RESOLVE_AUTHORIZATION",
            "validation_status": self.validation_status,
            "decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": self.reason_code,
            "execution_authorized": False,
            "network_access": False,
            "process_executed": False,
            "package_mutation": False,
            "application_launch": False,
            "model_loading": False,
            "generation": False,
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "authorization_id": self.authorization_id,
            "authorization_sha256": self.authorization_sha256,
            "filesystem_policy": (
                dict(self.filesystem_policy)
                if self.filesystem_policy is not None
                else None
            ),
        }
        value["validation_sha256"] = canonical_sha256(value)
        return value


def render_validation_json(result: AuthorizationValidationResult) -> str:
    return pretty_json(result.to_dict())


def render_validation_markdown(
    result: AuthorizationValidationResult,
) -> str:
    value = result.to_dict()
    return "\n".join(
        (
            "# Resolve Authorization Validation",
            "",
            "> **NOT AUTHORIZATION**",
            ">",
            "> This validation result cannot authorize Resolve or network access.",
            "",
            f"- Validation status: `{value['validation_status']}`",
            f"- Decision: `{value['decision']}`",
            f"- Effective decision: `{value['effective_decision']}`",
            f"- Reason code: `{value['reason_code']}`",
            f"- Execution authorized: `{str(value['execution_authorized']).lower()}`",
            f"- Plan ID: `{value['plan_id']}`",
            f"- Plan SHA-256: `{value['plan_sha256']}`",
            f"- Validation SHA-256: `{value['validation_sha256']}`",
            "",
        )
    )


def _authorization_template(
    logical_plan: Mapping[str, Any],
    *,
    plan_sha256: str,
) -> dict[str, Any]:
    scope = logical_plan.get("scope")
    repository = logical_plan.get("repository")
    policy = logical_plan.get("filesystem_policy")
    if (
        not isinstance(scope, Mapping)
        or not isinstance(repository, Mapping)
        or not isinstance(policy, Mapping)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    candidate_scope = scope.get("candidate_scope")
    if not isinstance(candidate_scope, Mapping):
        candidate_scope = {}
    return {
        "schema_version": AUTHORIZATION_SCHEMA,
        "authorization_id": "NOT_AUTHORIZED",
        "issued_by": "NOT_AUTHORIZED",
        "issued_at": "NOT AUTHORIZATION",
        "expires_at": "NOT AUTHORIZATION",
        "single_use": False,
        "repository_head": repository.get("head"),
        "plan_sha256": plan_sha256,
        "filesystem_policy_sha256": policy.get(
            "policy_source_sha256"
        ),
        "approved_hosts": list(scope.get("requested_hosts", [])),
        "approved_cache_root": scope.get("approved_cache_root"),
        "approved_temp_root": scope.get("approved_temp_root"),
        "approved_report_root": scope.get("approved_report_root"),
        "approved_operations": list(
            scope.get("network_operation_classes", [])
        ),
        "candidate_scope": dict(candidate_scope),
        "allow_install": False,
        "allow_uninstall": False,
        "allow_upgrade": False,
        "allow_launch": False,
        "allow_model_download": False,
        "allow_extension_update": False,
        "owner_statement": "NOT AUTHORIZATION",
    }


def _render_plan_markdown(
    logical_plan: Mapping[str, Any],
    *,
    plan_id: str,
    plan_sha256: str,
) -> str:
    dependency = logical_plan.get("dependency")
    scope = logical_plan.get("scope")
    repository = logical_plan.get("repository")
    if not all(
        isinstance(value, Mapping)
        for value in (dependency, scope, repository)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    return "\n".join(
        (
            "# Forge ResolvePlan",
            "",
            "> **NOT AUTHORIZATION**",
            ">",
            "> `PLAN_READY` means only that this deterministic review bundle",
            "> was produced. Network access and Resolve execution remain blocked.",
            "",
            f"- Plan status: `{logical_plan.get('plan_status')}`",
            (
                "- Authorization status: "
                f"`{logical_plan.get('authorization_status')}`"
            ),
            f"- Decision: `{logical_plan.get('decision')}`",
            f"- Effective decision: `{logical_plan.get('effective_decision')}`",
            (
                "- Authorization eligible: "
                f"`{str(logical_plan.get('authorization_eligible')).lower()}`"
            ),
            (
                "- Authorization blockers: `"
                + (
                    ", ".join(
                        logical_plan.get("authorization_blockers", [])
                    )
                    or "none"
                )
                + "`"
            ),
            f"- Plan ID: `{plan_id}`",
            f"- Plan SHA-256: `{plan_sha256}`",
            f"- Repository HEAD: `{repository.get('head')}`",
            f"- Active branch: `{repository.get('active_branch')}`",
            (
                "- Clean-worktree state: "
                f"`{repository.get('clean_worktree_state')}`"
            ),
            (
                "- Neo parity: "
                f"`{repository.get('neo_parity_left')} "
                f"{repository.get('neo_parity_right')}`"
            ),
            (
                "- Dependency decision: "
                f"`{dependency.get('decision')}`"
            ),
            (
                "- Dependency reason: "
                f"`{dependency.get('reason_code')}`"
            ),
            (
                "- Candidate scope status: "
                f"`{scope.get('candidate_scope_status')}`"
            ),
            "",
            "No replacement Gradio or Pillow version is selected or recommended.",
            "",
        )
    )


def _render_authorization_request_markdown(
    template: Mapping[str, Any],
) -> str:
    return "\n".join(
        (
            "# Resolve Authorization Request Template",
            "",
            "> **NOT AUTHORIZATION**",
            ">",
            "> This generated review template cannot authorize a network or",
            "> process operation. The tool and Codex must not convert it into",
            "> a valid receipt.",
            "",
            f"- Schema: `{template.get('schema_version')}`",
            f"- Issued by: `{template.get('issued_by')}`",
            f"- Owner statement: `{template.get('owner_statement')}`",
            f"- Plan SHA-256: `{template.get('plan_sha256')}`",
            "- Install allowed: `false`",
            "- Launch allowed: `false`",
            "- Model download allowed: `false`",
            "",
            "A later receipt must be manually supplied by the human owner.",
            "",
        )
    )


def _privacy_review_text() -> str:
    return "\n".join(
        (
            "# ResolvePlan Privacy Review",
            "",
            "Automated status: no external absolute path, credential, token,",
            "URL query, URL fragment, prompt, model name, or generated content",
            "is intentionally included in this bundle.",
            "",
            "This is an automated construction claim, not a completed human",
            "privacy review. The human owner must review all future raw network",
            "metadata and pip reports before sharing or proposing them for Git.",
            "",
        )
    )


def build_plan_bundle(
    paths: ResolverPaths,
    *,
    logical_plan_fields: Mapping[str, Any],
    dependency_inputs: Mapping[str, Any],
    source_bindings: Mapping[str, Any],
) -> ResolvePlanBundle:
    """Render eight deterministic artifacts from one logical plan."""

    if not isinstance(logical_plan_fields, Mapping):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    logical_plan = dict(logical_plan_fields)
    logical_plan["dependency_inputs_sha256"] = canonical_sha256(
        dependency_inputs
    )
    logical_plan["source_bindings_sha256"] = canonical_sha256(
        source_bindings
    )
    if "plan_sha256" in logical_plan or "plan_id" in logical_plan:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    validate_logical_plan(
        logical_plan,
        dependency_inputs=dependency_inputs,
        source_bindings=source_bindings,
    )
    plan_sha256 = canonical_sha256(logical_plan)
    plan_id = plan_sha256.removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", plan_id) is None:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ID_MISMATCH"
        )
    directory = paths.resolve_plans / plan_id
    envelope = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": plan_id,
        "plan_sha256": plan_sha256,
        "plan": logical_plan,
    }
    template = _authorization_template(
        logical_plan,
        plan_sha256=plan_sha256,
    )
    artifacts = {
        "resolve-plan.json": pretty_json(envelope),
        "resolve-plan.md": _render_plan_markdown(
            logical_plan,
            plan_id=plan_id,
            plan_sha256=plan_sha256,
        ),
        "authorization-request.json": pretty_json(template),
        "authorization-request.md": (
            _render_authorization_request_markdown(template)
        ),
        "dependency-inputs.json": pretty_json(dependency_inputs),
        "source-bindings.json": pretty_json(source_bindings),
        "privacy-review.md": _privacy_review_text(),
        "tool-version.txt": f"{PREFLIGHT_VERSION}\n",
    }
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ARTIFACT_SET_INVALID"
        )
    return ResolvePlanBundle(
        plan_id=plan_id,
        plan_sha256=plan_sha256,
        directory=directory,
        logical_plan=logical_plan,
        dependency_inputs=dict(dependency_inputs),
        source_bindings=dict(source_bindings),
        artifacts=artifacts,
    )


def write_plan_bundle(
    fs: WorkspaceFS,
    bundle: ResolvePlanBundle,
) -> ResolvePlanBundle:
    """Publish a complete bundle, with resolve-plan.json as completion marker."""

    directory = fs.boundary.authorize(
        bundle.directory, "resolve-plan-directory"
    )
    if fs.exists(directory):
        observed_names = {
            child.name for child in fs.list_children(directory)
        }
        if observed_names != set(ARTIFACT_NAMES):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_DIRECTORY_ALREADY_EXISTS"
            )
        for name in ARTIFACT_NAMES:
            if fs.read_text(
                directory / name,
                max_chars=4_000_000,
            ) != bundle.artifacts[name]:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_PLAN_DIRECTORY_ALREADY_EXISTS"
                )
        return bundle

    fs.mkdir(directory)
    try:
        for name in ARTIFACT_NAMES:
            if name == "resolve-plan.json":
                continue
            fs.create_text_exclusive(
                directory / name,
                bundle.artifacts[name],
            )
        fs.create_text_exclusive(
            directory / "resolve-plan.json",
            bundle.artifacts["resolve-plan.json"],
        )
    except FileExistsError as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_DIRECTORY_ALREADY_EXISTS"
        ) from exc
    return bundle


def load_plan_bundle(
    fs: WorkspaceFS,
    paths: ResolverPaths,
    plan_path: str | os.PathLike[str],
) -> tuple[ResolvePlanBundle, bytes]:
    """Load and byte-verify a complete canonical eight-file plan bundle."""

    try:
        checked = fs.boundary.authorize(
            plan_path, "resolve-plan-validation-input"
        )
    except BoundaryViolation as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
        ) from exc
    if (
        checked.name != "resolve-plan.json"
        or checked.parent.parent != paths.resolve_plans
        or re.fullmatch(r"[0-9a-f]{64}", checked.parent.name) is None
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    observed_names = {child.name for child in fs.list_children(checked.parent)}
    if observed_names != set(ARTIFACT_NAMES):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ARTIFACT_SET_INVALID"
        )
    raw_plan = fs.read_bytes(checked, max_bytes=4_000_000)
    envelope = strict_json_from_bytes(
        raw_plan,
        reason_code="RESOLVE_PLAN_SCHEMA_INVALID",
        max_bytes=4_000_000,
    )
    envelope = _require_exact_mapping(
        envelope,
        {"schema_version", "plan_id", "plan_sha256", "plan"},
        "RESOLVE_PLAN_SCHEMA_INVALID",
    )
    logical_plan = envelope.get("plan")
    if (
        envelope.get("schema_version") != PLAN_SCHEMA
        or not isinstance(logical_plan, Mapping)
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    expected_hash = canonical_sha256(logical_plan)
    if envelope.get("plan_sha256") != expected_hash:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_HASH_MISMATCH"
        )
    expected_id = expected_hash.removeprefix("sha256:")
    if (
        envelope.get("plan_id") != expected_id
        or checked.parent.name != expected_id
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ID_MISMATCH"
        )
    dependency_inputs = strict_json_from_bytes(
        fs.read_bytes(
            checked.parent / "dependency-inputs.json",
            max_bytes=4_000_000,
        ),
        reason_code="RESOLVE_PLAN_ARTIFACT_CHANGED",
        max_bytes=4_000_000,
    )
    source_bindings = strict_json_from_bytes(
        fs.read_bytes(
            checked.parent / "source-bindings.json",
            max_bytes=4_000_000,
        ),
        reason_code="RESOLVE_PLAN_ARTIFACT_CHANGED",
        max_bytes=4_000_000,
    )
    if not isinstance(dependency_inputs, Mapping) or not isinstance(
        source_bindings, Mapping
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ARTIFACT_CHANGED"
        )
    if logical_plan.get("dependency_inputs_sha256") != canonical_sha256(
        dependency_inputs
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ARTIFACT_CHANGED"
        )
    if logical_plan.get("source_bindings_sha256") != canonical_sha256(
        source_bindings
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_ARTIFACT_CHANGED"
        )
    validate_logical_plan(
        logical_plan,
        dependency_inputs=dependency_inputs,
        source_bindings=source_bindings,
    )
    rebuilt = build_plan_bundle(
        paths,
        logical_plan_fields={
            key: value
            for key, value in logical_plan.items()
            if key
            not in {
                "dependency_inputs_sha256",
                "source_bindings_sha256",
            }
        },
        dependency_inputs=dependency_inputs,
        source_bindings=source_bindings,
    )
    for name in ARTIFACT_NAMES:
        observed = fs.read_text(
            checked.parent / name,
            max_chars=4_000_000,
        )
        if observed != rebuilt.artifacts[name]:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_ARTIFACT_CHANGED"
            )
    return rebuilt, raw_plan


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_result_report_identity(value: Any) -> str:
    """Accept only a content-addressed report identity for the future ledger."""

    if not _is_sha256(value):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_RESULT_REPORT_IDENTITY_INVALID"
        )
    return str(value)


def _read_ref(
    fs: WorkspaceFS,
    app_root: Path,
    reference: str,
) -> str | None:
    if (
        re.fullmatch(r"refs/[A-Za-z0-9._/-]+", reference) is None
        or ".." in reference.split("/")
        or reference.endswith("/")
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    git_directory = app_root / ".git"
    loose = git_directory / Path(reference)
    if fs.exists(loose):
        value = fs.read_text(loose, max_chars=4_096).strip().casefold()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_SCHEMA_INVALID"
            )
        return value
    packed = git_directory / "packed-refs"
    if not fs.is_file(packed):
        return None
    matches: list[str] = []
    for line in fs.read_text(packed, max_chars=4_000_000).splitlines():
        if not line or line.startswith(("#", "^")):
            continue
        fields = line.split(" ")
        if len(fields) == 2 and fields[1] == reference:
            matches.append(fields[0].casefold())
    if len(matches) > 1:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    if not matches:
        return None
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", matches[0]) is None:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    return matches[0]


@dataclass(frozen=True)
class _GitIndexEntry:
    path: str
    mode: int
    object_id: bytes
    stage: int
    ctime_seconds: int
    ctime_nanoseconds: int
    mtime_seconds: int
    mtime_nanoseconds: int
    size: int


@dataclass(frozen=True)
class _GitIgnoreRule:
    base: tuple[str, ...]
    pattern: tuple[str, ...]
    anchored: bool
    directory_only: bool


@dataclass(frozen=True)
class _GitAttributesRule:
    """One supported attributes line restricted to checkout semantics."""

    pattern: tuple[str, ...]
    anchored: bool
    text: str | None
    eol: str | None


@dataclass(frozen=True)
class _CheckoutSemantics:
    """Effective checkout semantics resolved for one tracked path."""

    text: str | None
    eol: str | None
    specified: bool


@dataclass(frozen=True)
class _GitAttributesModel:
    """Parsed attributes sources bound to the proof they were read for."""

    rules: tuple[_GitAttributesRule, ...]
    sources: dict[str, bytes]
    digest: str


def _git_sha1(value: bytes) -> bytes:
    return hashlib.sha1(value, usedforsecurity=False).digest()


def _parse_git_index(
    raw: bytes,
) -> tuple[tuple[_GitIndexEntry, ...], tuple[str, ...], str]:
    """Parse and checksum one complete SHA-1 Git index v2."""

    if (
        len(raw) < 32
        or len(raw) > MAX_GIT_INDEX_BYTES
        or raw[:4] != b"DIRC"
        or _git_sha1(raw[:-20]) != raw[-20:]
    ):
        raise ValueError("git-index-invalid")
    version = int.from_bytes(raw[4:8], "big")
    count = int.from_bytes(raw[8:12], "big")
    if version != 2 or not 0 <= count <= MAX_GIT_ENTRIES:
        raise ValueError("git-index-version")

    entries: list[_GitIndexEntry] = []
    seen_paths: set[str] = set()
    seen_casefolded_paths: set[str] = set()
    offset = 12
    payload_end = len(raw) - 20
    for _ in range(count):
        entry_start = offset
        if entry_start + 62 > payload_end:
            raise ValueError("git-index-truncated")
        mode = int.from_bytes(
            raw[entry_start + 24 : entry_start + 28],
            "big",
        )
        ctime_seconds = int.from_bytes(
            raw[entry_start : entry_start + 4],
            "big",
        )
        ctime_nanoseconds = int.from_bytes(
            raw[entry_start + 4 : entry_start + 8],
            "big",
        )
        mtime_seconds = int.from_bytes(
            raw[entry_start + 8 : entry_start + 12],
            "big",
        )
        mtime_nanoseconds = int.from_bytes(
            raw[entry_start + 12 : entry_start + 16],
            "big",
        )
        size = int.from_bytes(
            raw[entry_start + 36 : entry_start + 40],
            "big",
        )
        object_id = raw[entry_start + 40 : entry_start + 60]
        flags = int.from_bytes(
            raw[entry_start + 60 : entry_start + 62],
            "big",
        )
        if flags & 0x4000:
            raise ValueError("git-index-extended-entry")
        stage = (flags >> 12) & 0b11
        path_start = entry_start + 62
        try:
            path_end = raw.index(b"\0", path_start, payload_end)
        except ValueError as exc:
            raise ValueError("git-index-path-truncated") from exc
        path_bytes = raw[path_start:path_end]
        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("git-index-path-encoding") from exc
        path_parts = path.split("/")
        if (
            not path
            or path.startswith(("/", "\\"))
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path_parts)
            or path in seen_paths
            or path.casefold() in seen_casefolded_paths
            or mode not in SUPPORTED_GIT_INDEX_MODES
        ):
            raise ValueError("git-index-path-or-mode")
        encoded_length = flags & 0x0FFF
        if encoded_length < 0x0FFF and encoded_length != len(path_bytes):
            raise ValueError("git-index-path-length")
        seen_paths.add(path)
        seen_casefolded_paths.add(path.casefold())
        entries.append(
            _GitIndexEntry(
                path=path,
                mode=mode,
                object_id=object_id,
                stage=stage,
                ctime_seconds=ctime_seconds,
                ctime_nanoseconds=ctime_nanoseconds,
                mtime_seconds=mtime_seconds,
                mtime_nanoseconds=mtime_nanoseconds,
                size=size,
            )
        )
        entry_size = path_end + 1 - entry_start
        offset = entry_start + ((entry_size + 7) & ~7)

    extensions: list[str] = []
    while offset < payload_end:
        if offset + 8 > payload_end:
            raise ValueError("git-index-extension-truncated")
        try:
            signature = raw[offset : offset + 4].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("git-index-extension-signature") from exc
        size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        offset += 8
        if offset + size > payload_end or signature != "TREE":
            raise ValueError("git-index-extension-unsupported")
        extensions.append(signature)
        offset += size
    if len(extensions) > 1:
        raise ValueError("git-index-extension-duplicate")
    return (
        tuple(entries),
        tuple(extensions),
        _hash_bytes(raw),
    )


def _index_tree_object_id(
    entries: Sequence[_GitIndexEntry],
) -> bytes:
    """Build the canonical root tree ID from stage-zero index entries."""

    root: dict[str, Any] = {}
    for entry in entries:
        if entry.stage != 0:
            continue
        node = root
        parts = entry.path.split("/")
        for part in parts[:-1]:
            existing = node.get(part)
            if isinstance(existing, _GitIndexEntry):
                raise ValueError("git-index-file-directory-collision")
            if existing is None:
                existing = {}
                node[part] = existing
            node = existing
        name = parts[-1]
        if name in node:
            raise ValueError("git-index-path-collision")
        node[name] = entry

    def build_tree(node: Mapping[str, Any]) -> bytes:
        records: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            encoded_name = name.encode("utf-8")
            if isinstance(value, _GitIndexEntry):
                sort_name = encoded_name
                record = (
                    f"{value.mode:o} ".encode("ascii")
                    + encoded_name
                    + b"\0"
                    + value.object_id
                )
            elif isinstance(value, Mapping):
                sort_name = encoded_name + b"/"
                record = (
                    b"40000 "
                    + encoded_name
                    + b"\0"
                    + build_tree(value)
                )
            else:
                raise ValueError("git-index-tree-invalid")
            records.append((sort_name, record))
        content = b"".join(
            record for _, record in sorted(records, key=lambda item: item[0])
        )
        return _git_sha1(
            f"tree {len(content)}\0".encode("ascii") + content
        )

    return build_tree(root)


def _loose_commit_tree_object_id(
    fs: WorkspaceFS,
    app_root: Path,
    commit_id: str,
) -> bytes:
    """Read a bounded in-workspace loose commit and return its tree ID."""

    if re.fullmatch(r"[0-9a-f]{40}", commit_id) is None:
        raise ValueError("git-object-format-unsupported")
    compressed = fs.read_bytes(
        app_root
        / ".git"
        / "objects"
        / commit_id[:2]
        / commit_id[2:],
        max_bytes=MAX_GIT_OBJECT_BYTES,
    )
    inflater = zlib.decompressobj()
    decoded = inflater.decompress(compressed, MAX_GIT_OBJECT_BYTES + 1)
    if (
        len(decoded) > MAX_GIT_OBJECT_BYTES
        or inflater.unconsumed_tail
        or not inflater.eof
        or inflater.unused_data
    ):
        raise ValueError("git-object-compression-invalid")
    decoded += inflater.flush(MAX_GIT_OBJECT_BYTES + 1 - len(decoded))
    if (
        len(decoded) > MAX_GIT_OBJECT_BYTES
        or _git_sha1(decoded).hex() != commit_id
    ):
        raise ValueError("git-object-invalid")
    header, separator, body = decoded.partition(b"\0")
    if (
        not separator
        or not header.startswith(b"commit ")
        or not header.removeprefix(b"commit ").isdigit()
        or int(header.removeprefix(b"commit ")) != len(body)
    ):
        raise ValueError("git-commit-invalid")
    first_line = body.split(b"\n", 1)[0]
    if re.fullmatch(b"tree [0-9a-f]{40}", first_line) is None:
        raise ValueError("git-commit-tree-invalid")
    return bytes.fromhex(first_line.removeprefix(b"tree ").decode("ascii"))


def _match_git_pattern(
    patterns: tuple[str, ...],
    parts: tuple[str, ...],
) -> bool:
    if not patterns:
        return not parts
    pattern = patterns[0]
    if pattern == "**":
        return _match_git_pattern(patterns[1:], parts) or (
            bool(parts) and _match_git_pattern(patterns, parts[1:])
        )
    if not parts:
        return False
    observed = parts[0].casefold() if os.name == "nt" else parts[0]
    expected = pattern.casefold() if os.name == "nt" else pattern
    return fnmatch.fnmatchcase(observed, expected) and _match_git_pattern(
        patterns[1:],
        parts[1:],
    )


def _parse_git_ignore_rules(
    *,
    source_path: str,
    raw: bytes,
) -> tuple[_GitIgnoreRule, ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("git-ignore-encoding") from exc
    base = tuple(source_path.split("/")[:-1])
    if source_path == ".git/info/exclude":
        base = ()
    rules: list[_GitIgnoreRule] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if (
            line != line.rstrip(" ")
            or line.startswith(("!", "\\#", "\\!"))
            or "\\" in line
        ):
            raise ValueError("git-ignore-rule-unsupported")
        directory_only = line.endswith("/")
        if directory_only:
            line = line[:-1]
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        components = tuple(line.split("/"))
        if (
            not line
            or any(
                component in {"", ".", ".."}
                for component in components
            )
        ):
            raise ValueError("git-ignore-rule-invalid")
        rules.append(
            _GitIgnoreRule(
                base=base,
                pattern=components,
                anchored=anchored or len(components) > 1,
                directory_only=directory_only,
            )
        )
    return tuple(rules)


def _git_path_is_ignored(
    relative_path: str,
    *,
    is_directory: bool,
    rules: Sequence[_GitIgnoreRule],
) -> bool:
    parts = tuple(relative_path.split("/"))
    for rule in rules:
        if len(parts) < len(rule.base) or parts[: len(rule.base)] != rule.base:
            continue
        candidate = parts[len(rule.base) :]
        if rule.directory_only and not is_directory:
            continue
        if rule.anchored:
            matches = _match_git_pattern(rule.pattern, candidate)
        else:
            matches = any(
                fnmatch.fnmatchcase(
                    (
                        part.casefold()
                        if os.name == "nt"
                        else part
                    ),
                    (
                        rule.pattern[0].casefold()
                        if os.name == "nt"
                        else rule.pattern[0]
                    ),
                )
                for part in candidate
            )
        if matches:
            return True
    return False


def _repository_ignore_rules(
    fs: WorkspaceFS,
    app_root: Path,
    entries: Sequence[_GitIndexEntry],
) -> tuple[tuple[_GitIgnoreRule, ...], str, dict[str, bytes]]:
    sources: dict[str, bytes] = {}
    for entry in entries:
        if entry.stage == 0 and (
            entry.path == ".gitignore"
            or entry.path.endswith("/.gitignore")
        ):
            sources[entry.path] = fs.read_bytes(
                app_root / Path(entry.path),
                max_bytes=4_000_000,
            )
    exclude = app_root / ".git" / "info" / "exclude"
    if fs.is_file(exclude):
        sources[".git/info/exclude"] = fs.read_bytes(
            exclude,
            max_bytes=4_000_000,
        )
    rules: list[_GitIgnoreRule] = []
    manifest: list[dict[str, str]] = []
    for source_path in sorted(sources):
        raw = sources[source_path]
        rules.extend(
            _parse_git_ignore_rules(
                source_path=source_path,
                raw=raw,
            )
        )
        manifest.append(
            {"path": source_path, "sha256": _hash_bytes(raw)}
        )
    return tuple(rules), canonical_sha256(manifest), sources


def _untracked_repository_files(
    fs: WorkspaceFS,
    app_root: Path,
    entries: Sequence[_GitIndexEntry],
    rules: Sequence[_GitIgnoreRule],
) -> list[str]:
    tracked = {
        entry.path.casefold() if os.name == "nt" else entry.path
        for entry in entries
        if entry.stage == 0
    }
    untracked: list[str] = []
    pending = [app_root]
    while pending:
        directory = pending.pop()
        for child in reversed(fs.list_children(directory)):
            relative = child.relative_to(app_root).as_posix()
            if relative == ".git":
                continue
            is_directory = fs.is_dir(child)
            ignored = _git_path_is_ignored(
                relative,
                is_directory=is_directory,
                rules=rules,
            )
            if is_directory:
                if not ignored:
                    pending.append(child)
                continue
            normalized = (
                relative.casefold() if os.name == "nt" else relative
            )
            if normalized not in tracked and not ignored:
                untracked.append(f"app/{relative}")
    return sorted(untracked)


def _inconclusive_worktree_state() -> dict[str, Any]:
    return {
        "clean_worktree": None,
        "clean_worktree_state": "INCONCLUSIVE_GIT_STATE",
        "dirty_bound_paths": [],
        "staged_changes": None,
        "untracked_files": [],
        "complete_git_status": False,
        "index_matches_head": None,
        "worktree_matches_index": None,
        "untracked_scan_complete": False,
        "index_sha256": None,
        "ignore_rules_sha256": None,
        "snapshot_rechecked": False,
    }


def _index_stat_matches_worktree(
    fs: WorkspaceFS,
    app_root: Path,
    entry: _GitIndexEntry,
) -> bool:
    target = fs.boundary.authorize(
        app_root / Path(entry.path),
        "git-index-stat",
    )
    observed = os.stat(target, follow_symlinks=False)
    mtime_ns = int(observed.st_mtime_ns)
    return (
        observed.st_size == entry.size
        and mtime_ns // 1_000_000_000 == entry.mtime_seconds
        and mtime_ns % 1_000_000_000 == entry.mtime_nanoseconds
    )


def _repository_attribute_paths(
    app_root: Path,
    entries: Sequence[_GitIndexEntry],
) -> tuple[Path, ...]:
    """Return every attributes path that can affect an indexed path."""

    candidates = {
        app_root / ".git" / "info" / "attributes",
        app_root / ".gitattributes",
    }
    for entry in entries:
        if entry.stage != 0:
            continue
        parents = entry.path.split("/")[:-1]
        for depth in range(1, len(parents) + 1):
            candidates.add(
                app_root
                / Path(*parents[:depth])
                / ".gitattributes"
            )
    return tuple(
        sorted(
            candidates,
            key=lambda path: os.path.normcase(os.fspath(path)),
        )
    )


def _match_attribute_pattern(
    patterns: tuple[str, ...],
    parts: tuple[str, ...],
    *,
    fold: bool,
) -> bool:
    """Match one anchored attributes pattern, honouring ``**`` segments."""

    if not patterns:
        return not parts
    pattern = patterns[0]
    if pattern == "**":
        return _match_attribute_pattern(
            patterns[1:],
            parts,
            fold=fold,
        ) or (
            bool(parts)
            and _match_attribute_pattern(patterns, parts[1:], fold=fold)
        )
    if not parts:
        return False
    observed = parts[0].casefold() if fold else parts[0]
    expected = pattern.casefold() if fold else pattern
    return fnmatch.fnmatchcase(
        observed,
        expected,
    ) and _match_attribute_pattern(patterns[1:], parts[1:], fold=fold)


def _parse_git_attributes_rules(
    *,
    source_path: str,
    raw: bytes,
) -> tuple[_GitAttributesRule, ...]:
    """Parse one attributes file or fail closed on unsupported syntax.

    Only the checkout-affecting subset this tool can model is accepted:
    ``text``, ``-text``, ``text=auto``, ``eol=lf``, ``eol=crlf``, and the
    ``whitespace`` family, which never alters checked-out bytes. Every other
    form -- macros, ``filter``, ``working-tree-encoding``, ``ident``,
    ``binary``, negations, quoting, escapes, and unknown names -- raises.
    """

    if source_path != ".gitattributes":
        raise ValueError("git-attributes-nested-unsupported")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("git-attributes-encoding") from exc
    rules: list[_GitAttributesRule] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[attr]"):
            raise ValueError("git-attributes-macro-unsupported")
        if (
            "\\" in stripped
            or '"' in stripped
            or stripped.startswith("!")
        ):
            raise ValueError("git-attributes-rule-unsupported")
        fields = stripped.split()
        pattern_text = fields[0]
        tokens = fields[1:]
        if not tokens:
            raise ValueError("git-attributes-rule-invalid")

        text_state: str | None = None
        eol_state: str | None = None
        for token in tokens:
            if token == "text":
                text_state = "set"
            elif token == "-text":
                text_state = "unset"
            elif token == "text=auto":
                text_state = "auto"
            elif token == "eol=lf":
                eol_state = "lf"
            elif token == "eol=crlf":
                eol_state = "crlf"
            elif (
                token == "whitespace"
                or token == "-whitespace"
                or token.startswith("whitespace=")
            ):
                # Affects diff/apply warnings only, never checked-out bytes.
                continue
            else:
                raise ValueError("git-attributes-attribute-unsupported")

        if pattern_text.endswith("/"):
            raise ValueError("git-attributes-rule-unsupported")
        anchored = pattern_text.startswith("/")
        if anchored:
            pattern_text = pattern_text[1:]
        components = tuple(pattern_text.split("/"))
        if not pattern_text or any(
            component in {"", ".", ".."} for component in components
        ):
            raise ValueError("git-attributes-rule-invalid")
        rules.append(
            _GitAttributesRule(
                pattern=components,
                anchored=anchored or len(components) > 1,
                text=text_state,
                eol=eol_state,
            )
        )
    return tuple(rules)


def _effective_checkout_semantics(
    relative_path: str,
    rules: Sequence[_GitAttributesRule],
    *,
    fold: bool,
) -> _CheckoutSemantics:
    """Resolve last-match-wins checkout semantics for one tracked path."""

    parts = tuple(relative_path.split("/"))
    basename = parts[-1] if parts else ""
    text_state: str | None = None
    eol_state: str | None = None
    specified = False
    for rule in rules:
        if rule.anchored:
            matches = _match_attribute_pattern(
                rule.pattern,
                parts,
                fold=fold,
            )
        else:
            observed = basename.casefold() if fold else basename
            expected = (
                rule.pattern[0].casefold() if fold else rule.pattern[0]
            )
            matches = fnmatch.fnmatchcase(observed, expected)
        if not matches:
            continue
        specified = True
        if rule.text is not None:
            text_state = rule.text
        if rule.eol is not None:
            eol_state = rule.eol
    return _CheckoutSemantics(
        text=text_state,
        eol=eol_state,
        specified=specified,
    )


def _bound_checkout_semantics(
    relative_path: str,
    rules: Sequence[_GitAttributesRule],
) -> _CheckoutSemantics:
    """Resolve semantics, failing closed on case-sensitivity ambiguity."""

    exact = _effective_checkout_semantics(relative_path, rules, fold=False)
    if os.name != "nt":
        return exact
    folded = _effective_checkout_semantics(relative_path, rules, fold=True)
    if exact != folded:
        raise ValueError("git-attributes-case-ambiguous")
    return exact


def _repository_attributes_model(
    fs: WorkspaceFS,
    app_root: Path,
    entries: Sequence[_GitIndexEntry],
) -> _GitAttributesModel:
    """Build the supported attributes model or fail closed."""

    root = app_root / ".gitattributes"
    root_key = os.path.normcase(os.fspath(root))
    present = [
        path
        for path in _repository_attribute_paths(app_root, entries)
        if fs.exists(path)
    ]
    for path in present:
        if os.path.normcase(os.fspath(path)) != root_key:
            # Nested .gitattributes and .git/info/attributes are outside the
            # modelled subset. Fail closed rather than guess their effect.
            raise ValueError("git-attributes-unsupported")
    if not present:
        return _GitAttributesModel(
            rules=(),
            sources={},
            digest=canonical_sha256([]),
        )
    raw = fs.read_bytes(root, max_bytes=MAX_GIT_ATTRIBUTES_BYTES)
    rules = _parse_git_attributes_rules(
        source_path=".gitattributes",
        raw=raw,
    )
    return _GitAttributesModel(
        rules=rules,
        sources={".gitattributes": raw},
        digest=canonical_sha256(
            [{"path": ".gitattributes", "sha256": _hash_bytes(raw)}]
        ),
    )


def _attribute_normalized_matches(
    entry: _GitIndexEntry,
    content: bytes,
    semantics: _CheckoutSemantics,
) -> bool:
    """Compare worktree bytes to the index under known text semantics."""

    text_state = semantics.text
    if text_state is None and semantics.eol is not None:
        # Since Git 2.10 an explicit eol implies text.
        text_state = "set"
    if text_state is None or text_state == "unset":
        # Binary or text-unspecified: checked-out bytes are the blob bytes.
        return False
    if text_state == "auto" and b"\0" in content:
        return False
    if b"\r\n" not in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    normalized = content.replace(b"\r\n", b"\n")
    normalized_header = f"blob {len(normalized)}\0".encode("ascii")
    return _git_sha1(normalized_header + normalized) == entry.object_id


def _tracked_blob_matches(
    fs: WorkspaceFS,
    app_root: Path,
    entry: _GitIndexEntry,
    content: bytes,
    semantics: _CheckoutSemantics | None = None,
) -> bool:
    header = f"blob {len(content)}\0".encode("ascii")
    if _git_sha1(header + content) == entry.object_id:
        return True
    if semantics is not None and semantics.specified:
        return _attribute_normalized_matches(entry, content, semantics)
    if (
        b"\r\n" not in content
        or b"\0" in content
        or not _index_stat_matches_worktree(fs, app_root, entry)
    ):
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    normalized = content.replace(b"\r\n", b"\n")
    normalized_header = f"blob {len(normalized)}\0".encode("ascii")
    return _git_sha1(normalized_header + normalized) == entry.object_id


def _bound_worktree_state(
    fs: WorkspaceFS,
    app_root: Path,
) -> dict[str, Any]:
    """Prove complete Git cleanliness in-process or fail closed."""

    try:
        if (
            fs.exists(app_root / ".git" / "commondir")
            or fs.exists(
                app_root / ".git" / "objects" / "info" / "alternates"
            )
        ):
            raise ValueError("git-layout-unsupported")
        raw_before = fs.read_bytes(
            app_root / ".git" / "index",
            max_bytes=MAX_GIT_INDEX_BYTES,
        )
        entries, _, index_sha256 = _parse_git_index(raw_before)
        head_before = _read_repository_head(fs, app_root)
        head_tree = _loose_commit_tree_object_id(
            fs,
            app_root,
            head_before,
        )
        index_tree = _index_tree_object_id(entries)
        staged_changes = (
            any(entry.stage != 0 for entry in entries)
            or index_tree != head_tree
        )
        attributes = _repository_attributes_model(fs, app_root, entries)
        dirty: list[str] = []
        tracked_snapshots: dict[str, str] = {}
        for entry in entries:
            if entry.stage != 0:
                continue
            try:
                content = fs.read_bytes(
                    app_root / Path(entry.path),
                    max_bytes=MAX_GIT_OBJECT_BYTES,
                )
            except FileNotFoundError:
                dirty.append(f"app/{entry.path}")
                continue
            tracked_snapshots[entry.path] = _hash_bytes(content)
            if not _tracked_blob_matches(
                fs,
                app_root,
                entry,
                content,
                _bound_checkout_semantics(entry.path, attributes.rules),
            ):
                dirty.append(f"app/{entry.path}")

        for source_path, source_content in attributes.sources.items():
            # An attributes file that is untracked or modified cannot be
            # trusted to describe the checkout that produced this worktree.
            # Reuse the verdict the tracked loop already reached for it so
            # the attributes file is held to exactly the same clean
            # definition as every other tracked path.
            if (
                source_path not in tracked_snapshots
                or f"app/{source_path}" in dirty
                or tracked_snapshots[source_path]
                != _hash_bytes(source_content)
            ):
                raise ValueError("git-attributes-tracked-snapshot-changed")

        rules, ignore_rules_sha256, ignore_sources = (
            _repository_ignore_rules(fs, app_root, entries)
        )
        for source_path, source_content in ignore_sources.items():
            if source_path == ".git/info/exclude":
                continue
            if tracked_snapshots.get(source_path) != _hash_bytes(
                source_content
            ):
                raise ValueError("git-ignore-tracked-snapshot-changed")
        untracked = _untracked_repository_files(
            fs,
            app_root,
            entries,
            rules,
        )
        raw_after = fs.read_bytes(
            app_root / ".git" / "index",
            max_bytes=MAX_GIT_INDEX_BYTES,
        )
        head_after = _read_repository_head(fs, app_root)
        if raw_after != raw_before or head_after != head_before:
            raise ValueError("git-snapshot-changed")
        for entry in entries:
            if entry.stage != 0:
                continue
            content = fs.read_bytes(
                app_root / Path(entry.path),
                max_bytes=MAX_GIT_OBJECT_BYTES,
            )
            if tracked_snapshots.get(entry.path) != _hash_bytes(content):
                raise ValueError("git-worktree-snapshot-changed")
        for source_path, expected in ignore_sources.items():
            observed = fs.read_bytes(
                app_root / Path(source_path),
                max_bytes=4_000_000,
            )
            if observed != expected:
                raise ValueError("git-ignore-snapshot-changed")
        if (
            _untracked_repository_files(
                fs,
                app_root,
                entries,
                rules,
            )
            != untracked
        ):
            raise ValueError("git-untracked-snapshot-changed")
        if (
            fs.read_bytes(
                app_root / ".git" / "index",
                max_bytes=MAX_GIT_INDEX_BYTES,
            )
            != raw_before
            or _read_repository_head(fs, app_root) != head_before
        ):
            raise ValueError("git-snapshot-changed")
        if (
            _repository_attributes_model(fs, app_root, entries).digest
            != attributes.digest
        ):
            raise ValueError("git-attributes-snapshot-changed")

        categories = (
            bool(dirty),
            staged_changes,
            bool(untracked),
        )
        clean = not any(categories)
        if clean:
            clean_state = "CLEAN"
        elif sum(categories) > 1:
            clean_state = "DIRTY_REPOSITORY"
        elif dirty:
            clean_state = "DIRTY_BOUND_INPUTS"
        elif staged_changes:
            clean_state = "DIRTY_STAGED_CHANGES"
        else:
            clean_state = "DIRTY_UNTRACKED_FILES"
        return {
            "clean_worktree": clean,
            "clean_worktree_state": clean_state,
            "dirty_bound_paths": sorted(dirty),
            "staged_changes": staged_changes,
            "untracked_files": untracked,
            "complete_git_status": True,
            "index_matches_head": not staged_changes,
            "worktree_matches_index": not dirty,
            "untracked_scan_complete": True,
            "index_sha256": index_sha256,
            "ignore_rules_sha256": ignore_rules_sha256,
            "snapshot_rechecked": True,
        }
    except (
        BoundaryViolation,
        OSError,
        PermissionError,
        ValueError,
        UnicodeError,
        zlib.error,
    ):
        return _inconclusive_worktree_state()


def _repository_binding(
    fs: WorkspaceFS,
    app_root: Path,
) -> dict[str, Any]:
    raw_head_before = fs.read_text(
        app_root / ".git" / "HEAD",
        max_chars=4_096,
    ).strip()
    head = _read_repository_head(fs, app_root)
    active_branch: str | None = None
    if raw_head_before.startswith("ref: refs/heads/"):
        active_branch = raw_head_before.removeprefix("ref: refs/heads/")
    if (
        active_branch is None
        or re.fullmatch(r"[A-Za-z0-9._/-]+", active_branch) is None
    ):
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    neo = _read_ref(fs, app_root, "refs/heads/neo")
    upstream_neo = _read_ref(
        fs,
        app_root,
        "refs/remotes/upstream/neo",
    )
    parity_equal = (
        neo is not None and upstream_neo is not None and neo == upstream_neo
    )
    worktree_state = _bound_worktree_state(fs, app_root)
    raw_head_after = fs.read_text(
        app_root / ".git" / "HEAD",
        max_chars=4_096,
    ).strip()
    if (
        raw_head_after != raw_head_before
        or _read_repository_head(fs, app_root) != head
        or _read_ref(fs, app_root, "refs/heads/neo") != neo
        or _read_ref(
            fs,
            app_root,
            "refs/remotes/upstream/neo",
        )
        != upstream_neo
    ):
        worktree_state = _inconclusive_worktree_state()
    return {
        "head": head,
        "active_branch": active_branch,
        **worktree_state,
        "neo_ref": neo,
        "upstream_neo_ref": upstream_neo,
        "neo_parity_left": 0 if parity_equal else None,
        "neo_parity_right": 0 if parity_equal else None,
        "neo_parity_state": "PARITY" if parity_equal else "INCONCLUSIVE",
    }


def _metadata_version(
    fs: WorkspaceFS,
    dist_info: Path,
) -> tuple[str, str]:
    metadata_path = dist_info / "METADATA"
    raw = fs.read_bytes(metadata_path, max_bytes=16_000_000)
    try:
        message = Parser().parsestr(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        ) from exc
    version = message.get("Version")
    if not isinstance(version, str) or not version:
        raise ResolveAuthorizationValidationError(
            "RESOLVE_PLAN_SCHEMA_INVALID"
        )
    return version, _hash_bytes(raw)


def _normalized_direct_requirements(
    requirements_text: str,
) -> list[dict[str, Any]]:
    parsed = StaticInspector._direct_requirements(requirements_text)
    return [
        {
            "line": line,
            "requirement": str(requirement),
        }
        for requirement, line in sorted(
            parsed.values(),
            key=lambda item: item[1],
        )
    ]


def _marker_environment() -> dict[str, str]:
    major, minor, micro = (
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )
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


class ResolveAuthorizationWorkflow:
    """Build plans and validate receipts without execution or consumption."""

    def __init__(
        self,
        fs: WorkspaceFS,
        app_root: Path,
        paths: ResolverPaths,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.fs = fs
        self.app_root = app_root
        self.paths = paths
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.resolver = Resolver(fs, app_root, paths)

    def _source_bindings(
        self,
        filesystem_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            checked_policy = FilesystemPolicyContract.from_dict(
                filesystem_policy
            )
            checked_policy.validate_current(self.fs)
        except ContractError as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_POLICY_MISMATCH"
            ) from exc
        policy = checked_policy.to_dict()
        requirements_path = self.app_root / "requirements.txt"
        launch_utils_path = self.app_root / "modules" / "launch_utils.py"
        requirements_bytes = self.fs.read_bytes(
            requirements_path,
            max_bytes=4_000_000,
        )
        launcher_bytes = self.fs.read_bytes(
            launch_utils_path,
            max_bytes=4_000_000,
        )
        tool_manifest = self.resolver._tool_manifest()
        execution_manifest = self.resolver._execution_manifest()
        site_packages = (
            self.app_root / "venv" / "Lib" / "site-packages"
        )
        pip_dist = self.fs.find_child(
            site_packages,
            prefix="pip-",
            suffix=".dist-info",
        )
        packaging_dist = self.fs.find_child(
            site_packages,
            prefix="packaging-",
            suffix=".dist-info",
        )
        pip_version, pip_metadata_sha = _metadata_version(
            self.fs, pip_dist
        )
        packaging_version, packaging_metadata_sha = _metadata_version(
            self.fs, packaging_dist
        )
        marker = _marker_environment()
        return {
            "schema_version": "forge-resolve-source-bindings/v1",
            "repository": _repository_binding(self.fs, self.app_root),
            "filesystem_policy": policy,
            "filesystem_policy_sha256": policy["policy_source_sha256"],
            "requirements": {
                "path": "app/requirements.txt",
                "sha256": _hash_bytes(requirements_bytes),
            },
            "launch_utils": {
                "path": "app/modules/launch_utils.py",
                "sha256": _hash_bytes(launcher_bytes),
            },
            "preflight_tool": {
                "version": PREFLIGHT_VERSION,
                "manifest": tool_manifest,
                "source_sha256": canonical_sha256(tool_manifest),
            },
            "python": {
                **dict(execution_manifest["python"]),
                "version": (
                    f"{sys.version_info.major}."
                    f"{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
            },
            "pip": {
                "version": pip_version,
                "metadata_sha256": pip_metadata_sha,
                "tree": execution_manifest["pip"],
            },
            "packaging": {
                "version": packaging_version,
                "metadata_sha256": packaging_metadata_sha,
                "tree": execution_manifest["packaging"],
            },
            "marker_environment": marker,
            "marker_environment_sha256": canonical_sha256(marker),
            "external_base_interpreter_inspected": False,
            "external_command_processor_inspected": False,
        }

    def _dependency_inputs(
        self,
        dependency_decision: Mapping[str, Any],
        *,
        candidate_scope: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        requirements_text = self.fs.read_text(
            self.app_root / "requirements.txt",
            max_chars=4_000_000,
        )
        launch_text = self.fs.read_text(
            self.app_root / "modules" / "launch_utils.py",
            max_chars=4_000_000,
        )
        normalized_scope = (
            validate_candidate_scope(candidate_scope)
            if candidate_scope is not None
            else None
        )
        return {
            "schema_version": "forge-resolve-dependency-inputs/v1",
            "repository_requirements": (
                _normalized_direct_requirements(requirements_text)
            ),
            "launcher_requirements": list(
                launcher_requirements_from_text(launch_text)
            ),
            "normalized_dependency_decision": dict(
                dependency_decision
            ),
            "dependency_decision_sha256": canonical_sha256(
                dependency_decision
            ),
            "candidate_scope": normalized_scope,
            "candidate_research_question": (
                "Find metadata-supported Gradio candidates compatible with "
                "Python 3.13.5, Pillow 12.3.0, pillow-heif 1.4.0, and the "
                "existing repository dependency set."
            ),
            "selected_candidate": None,
            "recommended_candidate": None,
        }

    def create_plan(
        self,
        *,
        repository_root: str | os.PathLike[str],
        evidence_root: str | os.PathLike[str],
        dependency_decision: Mapping[str, Any],
        filesystem_policy: Mapping[str, Any],
        candidate_scope: Mapping[str, Any] | None = None,
        requested_indexes: Sequence[str] = (),
        requested_hosts: Sequence[str] = (),
        approved_operations: Sequence[str] = (),
    ) -> ResolvePlanBundle:
        checked_repository = self.fs.boundary.authorize(
            repository_root, "resolve-plan-repository-root"
        )
        checked_evidence = self.fs.boundary.authorize(
            evidence_root, "resolve-plan-evidence-root"
        )
        expected_evidence = self.paths.resolve_plans.parent
        if (
            os.path.normcase(os.fspath(checked_repository))
            != os.path.normcase(os.fspath(self.app_root))
            or os.path.normcase(os.fspath(checked_evidence))
            != os.path.normcase(os.fspath(expected_evidence))
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
            )
        if not isinstance(dependency_decision, Mapping):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_PLAN_SCHEMA_INVALID"
            )
        hosts = validate_hosts(list(requested_hosts))
        operations = validate_operations(list(approved_operations))
        indexes = validate_requested_indexes(
            list(requested_indexes),
            hosts=hosts,
        )
        normalized_scope = (
            validate_candidate_scope(candidate_scope)
            if candidate_scope is not None
            else None
        )
        source_bindings = self._source_bindings(filesystem_policy)
        dependency_inputs = self._dependency_inputs(
            dependency_decision,
            candidate_scope=normalized_scope,
        )
        repository = source_bindings["repository"]
        policy = source_bindings["filesystem_policy"]
        authorization_blockers = _authorization_blockers(repository)
        logical_plan = {
            "schema_version": PLAN_SCHEMA,
            "tool_version": PREFLIGHT_VERSION,
            "mode": "RESOLVE_PLAN",
            "plan_status": "PLAN_READY",
            "authorization_status": "NETWORK_NOT_AUTHORIZED",
            "decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": "NETWORK_NOT_AUTHORIZED",
            "authorization_eligible": not authorization_blockers,
            "authorization_blockers": authorization_blockers,
            "repository": repository,
            "filesystem_policy": policy,
            "dependency": {
                "decision": dependency_decision.get("decision"),
                "reason_code": dependency_decision.get("reason_code"),
                "decision_sha256": canonical_sha256(
                    dependency_decision
                ),
                "conflict_core": dependency_decision.get(
                    "minimal_unsatisfiable_cores",
                    dependency_decision.get("minimal_unsatisfiable_core"),
                ),
                "selected_replacement": None,
                "recommended_replacement": None,
            },
            "runtime": {
                "python_path": "app/venv/Scripts/python.exe",
                "python_version": source_bindings["python"]["version"],
                "pip_version": source_bindings["pip"]["version"],
                "packaging_version": source_bindings["packaging"][
                    "version"
                ],
                "marker_environment_sha256": source_bindings[
                    "marker_environment_sha256"
                ],
            },
            "scope": {
                "approved_cache_root": APPROVED_ROOTS["cache"],
                "approved_temp_root": APPROVED_ROOTS["temp"],
                "approved_report_root": APPROVED_ROOTS["reports"],
                "requested_package_indexes": indexes,
                "requested_hosts": hosts,
                "network_operation_classes": operations,
                "candidate_scope_status": (
                    "BOUNDED"
                    if normalized_scope is not None
                    else "OWNER_INPUT_REQUIRED"
                ),
                "candidate_scope": (
                    normalized_scope if normalized_scope is not None else {}
                ),
                "no_install_policy": True,
                "no_launch_policy": True,
                "no_model_download_policy": True,
                "single_use_required": True,
                "maximum_validity_seconds": MAX_AUTHORIZATION_SECONDS,
            },
            "future_pip_dry_run": {
                "required_operation": "PIP_DRY_RUN",
                "argv_shape": [
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
                    "Evidence/preflight/reports/<RUN_ID>/pip-report.json",
                    "--index-url",
                    "<OWNER_APPROVED_INDEX>",
                    "-r",
                    (
                        "Evidence/preflight/temp/<RUN_ID>/"
                        "combined-requirements.txt"
                    ),
                ],
                "environment": {
                    "PIP_CACHE_DIR": APPROVED_ROOTS["cache"],
                    "TEMP": APPROVED_ROOTS["temp"],
                    "TMP": APPROVED_ROOTS["temp"],
                    "PIP_CONFIG_FILE": "NUL",
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "PIP_NO_INPUT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                "enabled": False,
            },
            "candidate_metadata_report": {
                "fields": [
                    "package",
                    "version",
                    "python_requirement",
                    "requires_dist",
                    "pillow_constraint",
                    "compatible_with_pillow_12_3_0",
                    "compatible_with_python_3_13_5",
                    "source_hostname",
                    "metadata_url_sanitized",
                    "artifact_type",
                    "yanked",
                    "compatibility_result",
                    "rejection_reasons",
                ],
                "automatic_selection": False,
                "automatic_recommendation": False,
            },
            "capabilities": {
                "outside_filesystem_access": False,
                "private_local_access": False,
                "network_access": False,
                "process_execution": False,
                "package_mutation": False,
                "application_launch": False,
                "model_loading": False,
                "model_download": False,
                "generation": False,
                "extension_update": False,
            },
        }
        bundle = build_plan_bundle(
            self.paths,
            logical_plan_fields=logical_plan,
            dependency_inputs=dependency_inputs,
            source_bindings=source_bindings,
        )
        return write_plan_bundle(self.fs, bundle)

    @staticmethod
    def _failure(
        reason_code: str,
        *,
        bundle: ResolvePlanBundle | None = None,
        authorization_id: str | None = None,
        authorization_sha256: str | None = None,
    ) -> AuthorizationValidationResult:
        policy = None
        if bundle is not None:
            candidate = bundle.logical_plan.get("filesystem_policy")
            if isinstance(candidate, Mapping):
                policy = candidate
        return AuthorizationValidationResult(
            validation_status="FAIL",
            reason_code=reason_code,
            plan_id=bundle.plan_id if bundle is not None else None,
            plan_sha256=(
                bundle.plan_sha256 if bundle is not None else None
            ),
            filesystem_policy=policy,
            authorization_id=authorization_id,
            authorization_sha256=authorization_sha256,
        )

    def _authorization_input_path(
        self,
        value: str | os.PathLike[str],
    ) -> Path:
        try:
            checked = self.fs.boundary.authorize_lexically(
                value,
                "resolve-authorization-input-lexical",
            )
        except BoundaryViolation as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
            ) from exc
        expected_parent = self.paths.reports / "authorizations"
        if (
            checked.suffix.casefold() != ".json"
            or os.path.normcase(os.fspath(checked.parent))
            != os.path.normcase(os.fspath(expected_parent))
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
            )
        try:
            return self.fs.boundary.authorize(
                checked,
                "resolve-authorization-input",
            )
        except BoundaryViolation as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE"
            ) from exc

    def _validated_receipt(
        self,
        raw: bytes,
        bundle: ResolvePlanBundle,
        *,
        now: datetime | None = None,
    ) -> tuple[Mapping[str, Any], datetime]:
        value = strict_json_from_bytes(
            raw,
            reason_code="RESOLVE_AUTHORIZATION_SCHEMA_INVALID",
        )
        receipt = _require_exact_mapping(
            value,
            AUTHORIZATION_FIELDS,
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID",
        )
        if receipt.get("schema_version") != AUTHORIZATION_SCHEMA:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
            )
        authorization_id = receipt.get("authorization_id")
        if (
            not isinstance(authorization_id, str)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
                authorization_id,
            )
            is None
            or authorization_id.casefold().startswith(
                ("not_authorized", "test", "fixture", "template")
            )
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
            )
        if receipt.get("issued_by") != "human-owner":
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_NOT_OWNER_ISSUED"
            )
        owner_statement = receipt.get("owner_statement")
        substantive_statement = (
            owner_statement.removeprefix(OWNER_STATEMENT_PREFIX).strip()
            if isinstance(owner_statement, str)
            else ""
        )
        if (
            not isinstance(owner_statement, str)
            or not owner_statement.startswith(OWNER_STATEMENT_PREFIX)
            or not substantive_statement
            or re.search(r"[A-Za-z0-9]", substantive_statement) is None
            or len(owner_statement) > 2_000
            or any(
                token in owner_statement.casefold()
                for token in (
                    "not authorization",
                    "assistant",
                    "codex",
                    "agent",
                    "tool-generated",
                    "template",
                    "fixture",
                )
            )
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_NOT_OWNER_ISSUED"
            )
        if receipt.get("single_use") is not True:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_SCHEMA_INVALID"
            )
        validate_safe_permissions(receipt)
        _, expires_at = validate_authorization_time_window(
            receipt.get("issued_at"),
            receipt.get("expires_at"),
            now=now if now is not None else self.now(),
        )
        validate_receipt_plan_bindings(
            receipt,
            bundle,
            self.fs.boundary,
        )
        return receipt, expires_at

    @staticmethod
    def _drift_reason(
        expected: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> str | None:
        expected_repository = expected.get("repository")
        current_repository = current.get("repository")
        if (
            not isinstance(expected_repository, Mapping)
            or not isinstance(current_repository, Mapping)
            or expected_repository.get("head")
            != current_repository.get("head")
        ):
            return "RESOLVE_AUTHORIZATION_HEAD_MISMATCH"
        if expected_repository.get(
            "active_branch"
        ) != current_repository.get("active_branch"):
            return "RESOLVE_AUTHORIZATION_BRANCH_MISMATCH"
        neo_fields = (
            "neo_ref",
            "upstream_neo_ref",
            "neo_parity_left",
            "neo_parity_right",
            "neo_parity_state",
        )
        if any(
            expected_repository.get(field)
            != current_repository.get(field)
            for field in neo_fields
        ):
            return "RESOLVE_AUTHORIZATION_NEO_PARITY_MISMATCH"
        if _authorization_blockers(current_repository):
            return "RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE"
        if expected.get("filesystem_policy") != current.get(
            "filesystem_policy"
        ):
            return "RESOLVE_AUTHORIZATION_POLICY_MISMATCH"
        if expected.get("requirements") != current.get("requirements"):
            return "RESOLVE_REQUIREMENTS_CHANGED_AFTER_PLAN"
        if expected.get("launch_utils") != current.get("launch_utils"):
            return "RESOLVE_LAUNCHER_SOURCE_CHANGED_AFTER_PLAN"
        if expected.get("preflight_tool") != current.get(
            "preflight_tool"
        ):
            return "RESOLVE_TOOL_CHANGED_AFTER_PLAN"
        if expected.get("marker_environment") != current.get(
            "marker_environment"
        ):
            return "RESOLVE_MARKER_ENVIRONMENT_CHANGED_AFTER_PLAN"
        if expected.get("python") != current.get("python"):
            return "RESOLVE_PYTHON_CHANGED_AFTER_PLAN"
        if expected.get("pip") != current.get("pip"):
            return "RESOLVE_PIP_CHANGED_AFTER_PLAN"
        if expected.get("packaging") != current.get("packaging"):
            return "RESOLVE_PACKAGING_CHANGED_AFTER_PLAN"
        if expected != current:
            return "RESOLVE_AUTHORIZATION_PLAN_MISMATCH"
        return None

    def validate_authorization(
        self,
        *,
        plan_path: str | os.PathLike[str],
        authorization_path: str | os.PathLike[str] | None,
        dependency_decision: Mapping[str, Any],
    ) -> AuthorizationValidationResult:
        """Validate a receipt as data without writing or consuming anything."""

        try:
            bundle, raw_plan_before = load_plan_bundle(
                self.fs,
                self.paths,
                plan_path,
            )
        except ResolveAuthorizationValidationError as exc:
            return self._failure(exc.reason_code)
        if bundle.logical_plan.get("authorization_eligible") is not True:
            return self._failure(
                "RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE",
                bundle=bundle,
            )
        if authorization_path is None:
            return self._failure(
                "RESOLVE_AUTHORIZATION_MISSING",
                bundle=bundle,
            )
        try:
            checked_authorization = self._authorization_input_path(
                authorization_path
            )
            if not self.fs.is_file(checked_authorization):
                return self._failure(
                    "RESOLVE_AUTHORIZATION_MISSING",
                    bundle=bundle,
                )
            raw_authorization_before = self.fs.read_bytes(
                checked_authorization,
                max_bytes=1_000_000,
            )
            authorization_sha256 = _hash_bytes(
                raw_authorization_before
            )
            receipt, _ = self._validated_receipt(
                raw_authorization_before,
                bundle,
            )
            authorization_id = str(receipt["authorization_id"])
            ledger_name = (
                hashlib.sha256(
                    authorization_id.encode("ascii")
                ).hexdigest()
                + ".json"
            )
            ledger_path = self.paths.authorization_ledger / ledger_name
            if self.fs.exists(ledger_path):
                return self._failure(
                    "RESOLVE_AUTHORIZATION_REUSED",
                    bundle=bundle,
                    authorization_id=authorization_id,
                    authorization_sha256=authorization_sha256,
                )

            planned_policy = bundle.logical_plan.get(
                "filesystem_policy"
            )
            if not isinstance(planned_policy, Mapping):
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_PLAN_SCHEMA_INVALID"
                )
            current_source_bindings = self._source_bindings(
                planned_policy
            )
            drift = self._drift_reason(
                bundle.source_bindings,
                current_source_bindings,
            )
            if drift is not None:
                return self._failure(
                    drift,
                    bundle=bundle,
                    authorization_id=authorization_id,
                    authorization_sha256=authorization_sha256,
                )
            candidate_scope = bundle.logical_plan.get(
                "scope", {}
            )
            if not isinstance(candidate_scope, Mapping):
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_PLAN_SCHEMA_INVALID"
                )
            current_dependency_inputs = self._dependency_inputs(
                dependency_decision,
                candidate_scope=candidate_scope.get("candidate_scope"),
            )
            if current_dependency_inputs != bundle.dependency_inputs:
                return self._failure(
                    "RESOLVE_DEPENDENCY_INPUTS_CHANGED_AFTER_PLAN",
                    bundle=bundle,
                    authorization_id=authorization_id,
                    authorization_sha256=authorization_sha256,
                )
            try:
                bundle_after, raw_plan_after = load_plan_bundle(
                    self.fs,
                    self.paths,
                    bundle.plan_json,
                )
            except ResolveAuthorizationValidationError:
                return self._failure(
                    "RESOLVE_AUTHORIZATION_CHANGED_AFTER_VALIDATION",
                    bundle=bundle,
                    authorization_id=authorization_id,
                    authorization_sha256=authorization_sha256,
                )
            raw_authorization_after = self.fs.read_bytes(
                checked_authorization,
                max_bytes=1_000_000,
            )
            if (
                raw_plan_after != raw_plan_before
                or bundle_after.plan_sha256 != bundle.plan_sha256
                or bundle_after.artifacts != bundle.artifacts
                or raw_authorization_after
                != raw_authorization_before
            ):
                return self._failure(
                    "RESOLVE_AUTHORIZATION_CHANGED_AFTER_VALIDATION",
                    bundle=bundle,
                    authorization_id=authorization_id,
                    authorization_sha256=authorization_sha256,
                )
            self._validated_receipt(
                raw_authorization_after,
                bundle_after,
            )
            return AuthorizationValidationResult(
                validation_status="PASS",
                reason_code=(
                    "RESOLVE_AUTHORIZATION_VALIDATION_ONLY_PASS"
                ),
                plan_id=bundle.plan_id,
                plan_sha256=bundle.plan_sha256,
                filesystem_policy=planned_policy,
                authorization_id=authorization_id,
                authorization_sha256=authorization_sha256,
            )
        except ResolveAuthorizationValidationError as exc:
            return self._failure(
                exc.reason_code,
                bundle=bundle,
            )
        except (FileNotFoundError, OSError, UnicodeError, ValueError):
            return self._failure(
                "RESOLVE_AUTHORIZATION_SCHEMA_INVALID",
                bundle=bundle,
            )

    def consume_for_future_network_start(
        self,
        *,
        plan_path: str | os.PathLike[str],
        authorization_path: str | os.PathLike[str],
        dependency_decision: Mapping[str, Any],
        result_report_identity: str,
    ) -> Path:
        """Future-only O_EXCL replay marker; never called by E0."""

        validation = self.validate_authorization(
            plan_path=plan_path,
            authorization_path=authorization_path,
            dependency_decision=dependency_decision,
        )
        if (
            validation.validation_status != "PASS"
            or validation.reason_code
            != "RESOLVE_AUTHORIZATION_VALIDATION_ONLY_PASS"
            or not validation.authorization_id
            or not validation.authorization_sha256
            or not validation.plan_sha256
        ):
            raise ResolveAuthorizationValidationError(
                validation.reason_code
            )
        checked_report_identity = _validate_result_report_identity(
            result_report_identity
        )
        ledger_name = (
            hashlib.sha256(
                validation.authorization_id.encode("ascii")
            ).hexdigest()
            + ".json"
        )
        ledger_path = self.paths.authorization_ledger / ledger_name
        try:
            final_bundle, _ = load_plan_bundle(
                self.fs,
                self.paths,
                plan_path,
            )
            checked_authorization = self._authorization_input_path(
                authorization_path
            )
            final_authorization = self.fs.read_bytes(
                checked_authorization,
                max_bytes=1_000_000,
            )
            final_now = self.now()
            final_receipt, _ = self._validated_receipt(
                final_authorization,
                final_bundle,
                now=final_now,
            )
        except ResolveAuthorizationValidationError:
            raise
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_CHANGED_AFTER_VALIDATION"
            ) from exc
        if (
            final_bundle.plan_sha256 != validation.plan_sha256
            or final_receipt.get("authorization_id")
            != validation.authorization_id
            or _hash_bytes(final_authorization)
            != validation.authorization_sha256
        ):
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_CHANGED_AFTER_VALIDATION"
            )
        record = {
            "schema_version": "forge-resolve-authorization-ledger/v1",
            "authorization_id": validation.authorization_id,
            "authorization_sha256": validation.authorization_sha256,
            "plan_sha256": validation.plan_sha256,
            "validation_timestamp": final_now.astimezone(
                timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "consumption_state": (
                "consumed-immediately-before-network-operation"
            ),
            "result_report_identity": checked_report_identity,
            "contains_secrets": False,
            "authorization_kind": (
                "single-use-ledger-entry-not-authorization"
            ),
        }
        try:
            return self.fs.create_json_exclusive(
                ledger_path,
                record,
            )
        except FileExistsError as exc:
            raise ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_REUSED"
            ) from exc
