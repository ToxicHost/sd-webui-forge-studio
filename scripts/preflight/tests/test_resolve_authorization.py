from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest import mock
import zlib

from scripts.preflight import cli
from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.orchestrator import ModeResult, PreflightOrchestrator
from scripts.preflight.resolve_authorization import (
    ARTIFACT_NAMES,
    MAX_CANDIDATES,
    PLAN_SCHEMA,
    PREFLIGHT_VERSION,
    PREFLIGHT_TOOL_MANIFEST_NAMES,
    AuthorizationValidationResult,
    ResolveAuthorizationValidationError,
    ResolveAuthorizationWorkflow,
    _authorization_blockers,
    _bound_worktree_state,
    build_plan_bundle,
    canonical_sha256,
    candidate_metadata_record,
    load_plan_bundle,
    render_validation_json,
    render_validation_markdown,
    validate_authorization_time_window,
    validate_candidate_scope,
    validate_hosts,
    validate_operations,
    validate_receipt_plan_bindings,
    validate_requested_indexes,
    validate_safe_permissions,
)
from scripts.preflight.resolver import ResolverPaths


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]
APP = WORKSPACE / "app"
FIXED_NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def resolver_paths() -> ResolverPaths:
    base = WORKSPACE / "Evidence" / "preflight"
    return ResolverPaths(
        cache=base / "cache",
        temp=base / "temp",
        reports=base / "reports",
        authorizations=base / "reports" / "authorizations",
        resolve_plans=base / "resolve-plans",
        authorization_ledger=base / "authorization-ledger",
    )


def filesystem_policy(seed: str = "a") -> dict[str, object]:
    return {
        "filesystem_policy_version": "forge-filesystem-policy/v1",
        "normalized_permitted_roots": [
            "app/",
            "Evidence/",
            "Reference/",
        ],
        "private_local_status": "PROHIBITED",
        "policy_source": "app/AGENTS.md",
        "policy_source_sha256": "sha256:" + (seed * 64),
        "outside_access_allowed": False,
    }


def repository_binding(
    head: str = "1",
    *,
    clean_worktree: bool | None = True,
    clean_worktree_state: str = "CLEAN",
    dirty_bound_paths: list[str] | None = None,
    staged_changes: bool | None = False,
    untracked_files: list[str] | None = None,
    complete_git_status: bool = True,
) -> dict[str, object]:
    neo_head = "9" * 40
    dirty = list(dirty_bound_paths or [])
    untracked = list(untracked_files or [])
    complete = complete_git_status
    return {
        "head": head * 40,
        "active_branch": "tools/preflight-orchestrator",
        "clean_worktree": clean_worktree,
        "clean_worktree_state": clean_worktree_state,
        "dirty_bound_paths": dirty,
        "staged_changes": staged_changes,
        "untracked_files": untracked,
        "complete_git_status": complete,
        "index_matches_head": (
            not staged_changes if complete else None
        ),
        "worktree_matches_index": (
            not dirty if complete else None
        ),
        "untracked_scan_complete": complete,
        "index_sha256": (
            "sha256:" + ("3" * 64) if complete else None
        ),
        "ignore_rules_sha256": (
            "sha256:" + ("4" * 64) if complete else None
        ),
        "snapshot_rechecked": complete,
        "neo_ref": neo_head,
        "upstream_neo_ref": neo_head,
        "neo_parity_left": 0,
        "neo_parity_right": 0,
        "neo_parity_state": "PARITY",
    }


def git_object(kind: str, content: bytes) -> tuple[bytes, str]:
    raw = f"{kind} {len(content)}\0".encode("ascii") + content
    return raw, hashlib.sha1(
        raw,
        usedforsecurity=False,
    ).hexdigest()


def git_index(entries: list[tuple[str, bytes, int]]) -> bytes:
    body = bytearray(b"DIRC" + (2).to_bytes(4, "big"))
    body.extend(len(entries).to_bytes(4, "big"))
    for path, object_id, stage in entries:
        encoded = path.encode("utf-8")
        start = len(body)
        record = bytearray(62)
        record[24:28] = (0o100644).to_bytes(4, "big")
        record[36:40] = (1).to_bytes(4, "big")
        record[40:60] = object_id
        flags = len(encoded) | (stage << 12)
        record[60:62] = flags.to_bytes(2, "big")
        body.extend(record)
        body.extend(encoded + b"\0")
        while (len(body) - start) % 8:
            body.extend(b"\0")
    checksum = hashlib.sha1(
        bytes(body),
        usedforsecurity=False,
    ).digest()
    return bytes(body) + checksum


def git_state_values(
    *,
    head_content: bytes,
    index_content: bytes | None = None,
    worktree_content: bytes | None = None,
    untracked: bool = False,
) -> dict[Path, bytes]:
    expected_content = (
        index_content if index_content is not None else head_content
    )
    observed_content = (
        worktree_content
        if worktree_content is not None
        else expected_content
    )
    _, head_blob = git_object("blob", head_content)
    _, index_blob = git_object("blob", expected_content)
    tree_content = (
        b"100644 tracked.txt\0" + bytes.fromhex(head_blob)
    )
    _, tree_id = git_object("tree", tree_content)
    commit_body = (
        f"tree {tree_id}\n".encode("ascii")
        + b"author Fixture <fixture@example.invalid> 0 +0000\n"
        + b"committer Fixture <fixture@example.invalid> 0 +0000\n"
        + b"\nfixture\n"
    )
    commit_raw, commit_id = git_object("commit", commit_body)
    values = {
        APP / ".git" / "HEAD": (
            b"ref: refs/heads/tools/test-cleanliness\n"
        ),
        (
            APP
            / ".git"
            / "refs"
            / "heads"
            / "tools"
            / "test-cleanliness"
        ): commit_id.encode("ascii") + b"\n",
        APP / ".git" / "index": git_index(
            [
                (
                    "tracked.txt",
                    bytes.fromhex(index_blob),
                    0,
                )
            ]
        ),
        (
            APP
            / ".git"
            / "objects"
            / commit_id[:2]
            / commit_id[2:]
        ): zlib.compress(commit_raw),
        APP / ".git" / "info" / "exclude": b"",
        APP / "tracked.txt": observed_content,
    }
    if untracked:
        values[APP / "untracked.txt"] = b"untracked\n"
    return values


def stage_e0_dependency_decision() -> dict[str, object]:
    constraints = [
        {
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
        {
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
        {
            "constraint_id": "repository:Pillow:requirements.txt:2",
            "dependency": "pillow",
            "owner": "repository requirements.txt:2",
            "requirement": "Pillow==12.3.0",
            "source": {"path": "app/requirements.txt", "line": 2},
        },
    ]
    return {
        "schema_version": "dependency-constraint-result/v1",
        "decision": "NO_GO",
        "reason_code": "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY",
        "dependency": "pillow",
        "constraints": constraints,
        "normalized_intersection": "EMPTY",
        "minimal_unsatisfiable_cores": [
            [
                "distribution:gradio:4.40.0:pillow",
                "distribution:pillow-heif:1.4.0:pillow",
            ],
            [
                "distribution:gradio:4.40.0:pillow",
                "repository:Pillow:requirements.txt:2",
            ],
        ],
        "minimal_core_enumeration_complete": True,
        "observed_version": {
            "version": "12.3.0",
            "satisfies": [
                "distribution:pillow-heif:1.4.0:pillow",
                "repository:Pillow:requirements.txt:2",
            ],
            "violates": ["distribution:gradio:4.40.0:pillow"],
        },
        "selected_replacement": None,
        "recommended_replacement": None,
        "next_action": "OWNER_DECISION_REQUIRED",
        "detail": "Stage E0 verified Pillow dependency conflict.",
    }


def marker_environment() -> dict[str, str]:
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
        "extra": "",
    }


def logical_plan_fields(
    *,
    head: str = "1",
    policy_seed: str = "a",
    candidate_scope: dict[str, object] | None = None,
    repository: dict[str, object] | None = None,
) -> dict[str, object]:
    dependency_decision = stage_e0_dependency_decision()
    normalized_scope = validate_candidate_scope(
        candidate_scope
        or {
            "package": "gradio",
            "explicit_versions": ["4.44.1", "4.40.0"],
        }
    )
    selected_repository = repository or repository_binding(head)
    clean_repository = (
        selected_repository["clean_worktree"] is True
        and selected_repository["complete_git_status"] is True
        and selected_repository["index_matches_head"] is True
        and selected_repository["worktree_matches_index"] is True
        and selected_repository["untracked_scan_complete"] is True
        and selected_repository["staged_changes"] is False
        and not selected_repository["dirty_bound_paths"]
        and not selected_repository["untracked_files"]
        and selected_repository["snapshot_rechecked"] is True
    )
    neo_parity = (
        selected_repository["neo_parity_state"] == "PARITY"
        and selected_repository["neo_parity_left"] == 0
        and selected_repository["neo_parity_right"] == 0
        and selected_repository["neo_ref"]
        == selected_repository["upstream_neo_ref"]
    )
    blockers = []
    if not clean_repository:
        blockers.append("RESOLVE_PLAN_DIRTY_WORKTREE")
    if not neo_parity:
        blockers.append("RESOLVE_PLAN_NEO_PARITY_NOT_ESTABLISHED")
    repository_eligible = not blockers
    return {
        "schema_version": PLAN_SCHEMA,
        "tool_version": PREFLIGHT_VERSION,
        "mode": "RESOLVE_PLAN",
        "plan_status": "PLAN_READY",
        "authorization_status": "NETWORK_NOT_AUTHORIZED",
        "decision": "NO_GO",
        "effective_decision": "NO_GO",
        "reason_code": "NETWORK_NOT_AUTHORIZED",
        "authorization_eligible": repository_eligible,
        "authorization_blockers": blockers,
        "repository": selected_repository,
        "filesystem_policy": filesystem_policy(policy_seed),
        "dependency": {
            "decision": "NO_GO",
            "reason_code": "DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY",
            "decision_sha256": canonical_sha256(dependency_decision),
            "conflict_core": dependency_decision[
                "minimal_unsatisfiable_cores"
            ],
            "selected_replacement": None,
            "recommended_replacement": None,
        },
        "runtime": {
            "python_path": "app/venv/Scripts/python.exe",
            "python_version": "3.13.5",
            "pip_version": "test-only",
            "packaging_version": "test-only",
            "marker_environment_sha256": canonical_sha256(
                marker_environment()
            ),
        },
        "scope": {
            "approved_cache_root": "Evidence/preflight/cache",
            "approved_temp_root": "Evidence/preflight/temp",
            "approved_report_root": "Evidence/preflight/reports",
            "requested_package_indexes": [
                "https://packages.example.invalid/simple"
            ],
            "requested_hosts": ["packages.example.invalid"],
            "network_operation_classes": ["CANDIDATE_METADATA"],
            "candidate_scope_status": "BOUNDED",
            "candidate_scope": normalized_scope,
            "no_install_policy": True,
            "no_launch_policy": True,
            "no_model_download_policy": True,
            "single_use_required": True,
            "maximum_validity_seconds": 86_400,
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
                "PIP_CACHE_DIR": "Evidence/preflight/cache",
                "TEMP": "Evidence/preflight/temp",
                "TMP": "Evidence/preflight/temp",
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


def dependency_inputs(
    requirements_seed: str = "a",
    *,
    candidate_scope: dict[str, object] | None = None,
    normalized_decision: dict[str, object] | None = None,
) -> dict[str, object]:
    decision = normalized_decision or stage_e0_dependency_decision()
    scope = (
        validate_candidate_scope(candidate_scope)
        if candidate_scope is not None
        else {
            "package": "gradio",
            "explicit_versions": ["4.40.0", "4.44.1"],
        }
    )
    return {
        "schema_version": "forge-resolve-dependency-inputs/v1",
        "repository_requirements": [
            {
                "name": "pillow",
                "requirement": f"Pillow==12.3.{ord(requirements_seed) % 10}",
            }
        ],
        "launcher_requirements": ["gradio==4.40.0"],
        "normalized_dependency_decision": decision,
        "dependency_decision_sha256": canonical_sha256(decision),
        "candidate_scope": scope,
        "candidate_research_question": (
            "Find one bounded metadata candidate without selecting it."
        ),
        "selected_candidate": None,
        "recommended_candidate": None,
    }


def source_bindings(
    *,
    head: str = "1",
    requirements_seed: str = "a",
    policy_seed: str = "a",
    tool_seed: str = "a",
    repository: dict[str, object] | None = None,
    policy_value: dict[str, object] | None = None,
) -> dict[str, object]:
    policy = policy_value or filesystem_policy(policy_seed)
    manifest = {
        name: tool_seed * 64
        for name in PREFLIGHT_TOOL_MANIFEST_NAMES
    }
    marker = marker_environment()
    return {
        "schema_version": "forge-resolve-source-bindings/v1",
        "repository": repository or repository_binding(head),
        "filesystem_policy": policy,
        "filesystem_policy_sha256": policy["policy_source_sha256"],
        "requirements": {
            "path": "app/requirements.txt",
            "sha256": "sha256:" + (requirements_seed * 64),
        },
        "launch_utils": {
            "path": "app/modules/launch_utils.py",
            "sha256": "sha256:" + ("b" * 64),
        },
        "preflight_tool": {
            "version": PREFLIGHT_VERSION,
            "manifest": manifest,
            "source_sha256": canonical_sha256(manifest),
        },
        "python": {
            "path": "app/venv/Scripts/python.exe",
            "sha256": "d" * 64,
            "size": 1_024,
            "version": "3.13.5",
        },
        "pip": {
            "version": "test-only",
            "metadata_sha256": "sha256:" + ("e" * 64),
            "tree": {
                "root": "app/venv/Lib/site-packages/pip",
                "file_count": 10,
                "total_bytes": 100,
                "sha256": "f" * 64,
            },
        },
        "packaging": {
            "version": "test-only",
            "metadata_sha256": "sha256:" + ("1" * 64),
            "tree": {
                "root": "app/venv/Lib/site-packages/packaging",
                "file_count": 10,
                "total_bytes": 100,
                "sha256": "2" * 64,
            },
        },
        "marker_environment": marker,
        "marker_environment_sha256": canonical_sha256(marker),
        "external_base_interpreter_inspected": False,
        "external_command_processor_inspected": False,
    }


def make_bundle(
    *,
    logical: dict[str, object] | None = None,
    dependencies: dict[str, object] | None = None,
    sources: dict[str, object] | None = None,
):
    selected_logical = deepcopy(logical or logical_plan_fields())
    if dependencies is None:
        scope = selected_logical["scope"]
        selected_dependencies = dependency_inputs(
            candidate_scope=(
                scope["candidate_scope"]
                if scope["candidate_scope_status"] == "BOUNDED"
                else None
            ),
        )
    else:
        selected_dependencies = dependencies
    selected_sources = sources or source_bindings(
        repository=deepcopy(selected_logical["repository"]),
        policy_value=deepcopy(selected_logical["filesystem_policy"]),
    )
    return build_plan_bundle(
        resolver_paths(),
        logical_plan_fields=selected_logical,
        dependency_inputs=selected_dependencies,
        source_bindings=selected_sources,
    )


class ReadOnlyMemoryFS(WorkspaceFS):
    """Workspace-bound byte store whose mutation methods always fail."""

    def __init__(
        self,
        values: dict[Path, bytes] | None = None,
        *,
        existing: tuple[Path, ...] = (),
    ) -> None:
        super().__init__(WorkspaceBoundary(WORKSPACE))
        self.values: dict[str, bytes] = {}
        self.existing = {
            self._key(path)
            for path in existing
        }
        self.reads: list[str] = []
        self.mutations: list[tuple[str, str]] = []
        for path, value in (values or {}).items():
            self.add(path, value)

    @staticmethod
    def _key(path: str | os.PathLike[str]) -> str:
        return os.path.normcase(
            os.path.normpath(os.path.abspath(os.fspath(path)))
        )

    def add(self, path: Path, value: bytes | str) -> None:
        self.values[self._key(path)] = (
            value.encode("utf-8") if isinstance(value, str) else value
        )

    def _checked_key(
        self,
        path: str | os.PathLike[str],
        operation: str,
    ) -> str:
        checked = self.boundary.authorize_lexically(path, operation)
        return self._key(checked)

    def read_bytes(self, path, *, max_bytes=None):
        key = self._checked_key(path, "memory-read-bytes")
        self.reads.append(key)
        if key not in self.values:
            raise FileNotFoundError(key)
        value = self.values[key]
        if max_bytes is not None and len(value) > max_bytes:
            raise ValueError("BINARY_INPUT_EXCEEDS_SIZE_LIMIT")
        return value

    def read_text(
        self,
        path,
        encoding="utf-8",
        *,
        max_chars=None,
    ):
        value = self.read_bytes(path).decode(encoding)
        if max_chars is not None and len(value) > max_chars:
            raise ValueError("TEXT_INPUT_EXCEEDS_SIZE_LIMIT")
        return value

    def is_file(self, path):
        return self._checked_key(path, "memory-is-file") in self.values

    def is_dir(self, path):
        key = self._checked_key(path, "memory-is-directory")
        prefix = key + os.sep
        return any(value.startswith(prefix) for value in self.values)

    def exists(self, path):
        key = self._checked_key(path, "memory-exists")
        if key in self.values or key in self.existing:
            return True
        prefix = key + os.sep
        return any(value.startswith(prefix) for value in self.values)

    def list_children(self, path):
        checked = self.boundary.authorize_lexically(
            path, "memory-list-children"
        )
        parent_key = self._key(checked)
        prefix = parent_key + os.sep
        children = set()
        for key in self.values:
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix) :]
            first = remainder.split(os.sep, 1)[0]
            children.add(Path(parent_key) / first)
        return tuple(sorted(children, key=lambda item: item.name.casefold()))

    def _reject_mutation(self, operation, path):
        self.mutations.append((operation, os.fspath(path)))
        raise AssertionError(f"unexpected mutation: {operation}")

    def mkdir(self, path):
        return self._reject_mutation("mkdir", path)

    def write_text(self, path, content, encoding="utf-8"):
        del content, encoding
        return self._reject_mutation("write-text", path)

    def write_json(self, path, value):
        del value
        return self._reject_mutation("write-json", path)

    def create_text_exclusive(
        self,
        path,
        content,
        encoding="utf-8",
    ):
        del content, encoding
        return self._reject_mutation("create-text-exclusive", path)

    def create_json_exclusive(self, path, value):
        del value
        return self._reject_mutation("create-json-exclusive", path)

    def replace(self, source, destination):
        del source
        return self._reject_mutation("replace", destination)


class EvidenceWritingMemoryFS(ReadOnlyMemoryFS):
    """Permit only in-memory Evidence writes for public orchestration tests."""

    def _evidence_path(self, path, operation):
        checked = self.boundary.authorize_lexically(path, operation)
        evidence_root = self._key(WORKSPACE / "Evidence")
        checked_key = self._key(checked)
        if not (
            checked_key == evidence_root
            or checked_key.startswith(evidence_root + os.sep)
        ):
            raise AssertionError(f"non-Evidence mutation: {operation}")
        return checked, checked_key

    def mkdir(self, path):
        checked, _ = self._evidence_path(path, "memory-mkdir")
        self.mutations.append(("mkdir", os.fspath(checked)))
        return checked

    def write_text(self, path, content, encoding="utf-8"):
        checked, _ = self._evidence_path(path, "memory-write-text")
        self.mutations.append(("write-text", os.fspath(checked)))
        self.add(checked, content.encode(encoding))
        return checked

    def write_json(self, path, value):
        return self.write_text(
            path,
            json.dumps(
                value,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

    def create_text_exclusive(
        self,
        path,
        content,
        encoding="utf-8",
    ):
        checked, key = self._evidence_path(
            path, "memory-create-text-exclusive"
        )
        if key in self.values:
            raise FileExistsError(key)
        self.mutations.append(
            ("create-text-exclusive", os.fspath(checked))
        )
        self.add(checked, content.encode(encoding))
        return checked

    def create_json_exclusive(self, path, value):
        return self.create_text_exclusive(
            path,
            json.dumps(
                value,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )


def memory_fs_for_bundle(bundle) -> ReadOnlyMemoryFS:
    return ReadOnlyMemoryFS(
        {
            bundle.directory / name: content
            for name, content in bundle.artifacts.items()
        }
    )


def public_orchestrator(fs: WorkspaceFS) -> PreflightOrchestrator:
    orchestrator = object.__new__(PreflightOrchestrator)
    orchestrator.workspace_root = WORKSPACE
    orchestrator.app_root = APP
    orchestrator.boundary = fs.boundary
    orchestrator.fs = fs
    orchestrator.resolver_paths = resolver_paths()
    orchestrator.default_contract = (
        APP
        / "scripts"
        / "preflight"
        / "fixtures"
        / "static-contract.json"
    )
    decision = stage_e0_dependency_decision()
    static_result = ModeResult(
        mode="STATIC",
        decision="NO_GO",
        reason_code="DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY",
        payload={
            "dependency_decision": decision,
            "filesystem_policy": filesystem_policy(),
        },
    )
    orchestrator.run_static = mock.Mock(return_value=static_result)
    return orchestrator


def non_authorizing_plan_bindings(bundle) -> dict[str, object]:
    scope = bundle.logical_plan["scope"]
    repository = bundle.logical_plan["repository"]
    policy = bundle.logical_plan["filesystem_policy"]
    return {
        "fixture_notice": "NOT AUTHORIZATION",
        "repository_head": repository["head"],
        "plan_sha256": bundle.plan_sha256,
        "filesystem_policy_sha256": policy["policy_source_sha256"],
        "approved_hosts": list(scope["requested_hosts"]),
        "approved_cache_root": scope["approved_cache_root"],
        "approved_temp_root": scope["approved_temp_root"],
        "approved_report_root": scope["approved_report_root"],
        "approved_operations": list(
            scope["network_operation_classes"]
        ),
        "candidate_scope": dict(scope["candidate_scope"]),
    }


class InjectedValidationWorkflow(ResolveAuthorizationWorkflow):
    """Inject already-classified test outcomes, never a raw owner receipt."""

    def __init__(
        self,
        fs,
        *,
        expected_sources,
        expected_dependencies,
        current_sources=None,
        current_dependencies=None,
        authorization_id: str = "opaque-validation-only-id",
    ) -> None:
        super().__init__(
            fs,
            APP,
            resolver_paths(),
            now=lambda: FIXED_NOW,
        )
        self.expected_sources = deepcopy(expected_sources)
        self.expected_dependencies = deepcopy(expected_dependencies)
        self.current_sources = deepcopy(
            current_sources
            if current_sources is not None
            else expected_sources
        )
        self.current_dependencies = deepcopy(
            current_dependencies
            if current_dependencies is not None
            else expected_dependencies
        )
        self.authorization_id = authorization_id

    def _validated_receipt(self, raw, bundle, *, now=None):
        del raw, bundle, now
        return (
            {"authorization_id": self.authorization_id},
            FIXED_NOW + timedelta(hours=1),
        )

    def _source_bindings(self, filesystem_policy):
        del filesystem_policy
        return deepcopy(self.current_sources)

    def _dependency_inputs(
        self,
        dependency_decision,
        *,
        candidate_scope,
    ):
        del dependency_decision, candidate_scope
        return deepcopy(self.current_dependencies)


class GitCleanlinessBindingTests(unittest.TestCase):
    def test_clean_complete_git_state_is_proven(self) -> None:
        fs = ReadOnlyMemoryFS(
            git_state_values(head_content=b"tracked\n")
        )
        state = _bound_worktree_state(fs, APP)
        self.assertIs(True, state["clean_worktree"])
        self.assertEqual("CLEAN", state["clean_worktree_state"])
        self.assertIs(False, state["staged_changes"])
        self.assertEqual([], state["untracked_files"])
        self.assertIs(True, state["complete_git_status"])

    def test_dirty_tracked_git_state_is_detected(self) -> None:
        fs = ReadOnlyMemoryFS(
            git_state_values(
                head_content=b"tracked\n",
                worktree_content=b"changed\n",
            )
        )
        state = _bound_worktree_state(fs, APP)
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_BOUND_INPUTS",
            state["clean_worktree_state"],
        )
        self.assertEqual(
            ["app/tracked.txt"],
            state["dirty_bound_paths"],
        )

    def test_staged_git_state_is_detected(self) -> None:
        fs = ReadOnlyMemoryFS(
            git_state_values(
                head_content=b"tracked\n",
                index_content=b"staged\n",
            )
        )
        state = _bound_worktree_state(fs, APP)
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_STAGED_CHANGES",
            state["clean_worktree_state"],
        )
        self.assertIs(True, state["staged_changes"])
        self.assertIs(False, state["index_matches_head"])

    def test_untracked_git_state_is_detected(self) -> None:
        fs = ReadOnlyMemoryFS(
            git_state_values(
                head_content=b"tracked\n",
                untracked=True,
            )
        )
        state = _bound_worktree_state(fs, APP)
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_UNTRACKED_FILES",
            state["clean_worktree_state"],
        )
        self.assertEqual(
            ["app/untracked.txt"],
            state["untracked_files"],
        )

    def test_incomplete_git_state_fails_closed(self) -> None:
        values = git_state_values(head_content=b"tracked\n")
        values[APP / ".git" / "index"] = b"DIRC"
        state = _bound_worktree_state(
            ReadOnlyMemoryFS(values),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )
        self.assertIs(False, state["complete_git_status"])

    def test_binary_crlf_difference_is_not_normalized_clean(
        self,
    ) -> None:
        state = _bound_worktree_state(
            ReadOnlyMemoryFS(
                git_state_values(
                    head_content=b"\0binary\n",
                    worktree_content=b"\0binary\r\n",
                )
            ),
            APP,
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            ["app/tracked.txt"],
            state["dirty_bound_paths"],
        )

    def test_ignored_untracked_attributes_file_fails_closed(
        self,
    ) -> None:
        values = git_state_values(head_content=b"tracked\n")
        values[APP / ".git" / "info" / "exclude"] = (
            b".gitattributes\n"
        )
        values[APP / ".gitattributes"] = b"* text=auto\n"
        state = _bound_worktree_state(
            ReadOnlyMemoryFS(values),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_local_info_attributes_file_fails_closed(self) -> None:
        values = git_state_values(head_content=b"tracked\n")
        values[APP / ".git" / "info" / "attributes"] = (
            b"* filter=fixture\n"
        )
        state = _bound_worktree_state(
            ReadOnlyMemoryFS(values),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_attributes_appearance_during_proof_fails_closed(
        self,
    ) -> None:
        class AppearingAttributesFS(ReadOnlyMemoryFS):
            def __init__(self, values):
                super().__init__(values)
                self.attribute_checks = 0

            def exists(self, path):
                if self._key(path) == self._key(
                    APP / ".gitattributes"
                ):
                    self.attribute_checks += 1
                    if self.attribute_checks > 1:
                        return True
                return super().exists(path)

        state = _bound_worktree_state(
            AppearingAttributesFS(
                git_state_values(head_content=b"tracked\n")
            ),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertIs(False, state["snapshot_rechecked"])

    def test_tracked_change_during_proof_fails_closed(self) -> None:
        class ChangingTrackedFS(ReadOnlyMemoryFS):
            def __init__(self, values):
                super().__init__(values)
                self.tracked_reads = 0

            def read_bytes(self, path, *, max_bytes=None):
                if self._key(path) == self._key(
                    APP / "tracked.txt"
                ):
                    self.tracked_reads += 1
                    if self.tracked_reads > 1:
                        return b"changed-during-proof\n"
                return super().read_bytes(path, max_bytes=max_bytes)

        state = _bound_worktree_state(
            ChangingTrackedFS(
                git_state_values(head_content=b"tracked\n")
            ),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertIs(False, state["snapshot_rechecked"])

    def test_untracked_appearance_during_proof_fails_closed(
        self,
    ) -> None:
        class AppearingUntrackedFS(ReadOnlyMemoryFS):
            def __init__(self, values):
                super().__init__(values)
                self.root_lists = 0

            def list_children(self, path):
                if self._key(path) == self._key(APP):
                    self.root_lists += 1
                    if self.root_lists > 1:
                        self.add(
                            APP / "appeared.txt",
                            b"appeared-during-proof\n",
                        )
                return super().list_children(path)

        state = _bound_worktree_state(
            AppearingUntrackedFS(
                git_state_values(head_content=b"tracked\n")
            ),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertIs(False, state["snapshot_rechecked"])


class ResolvePlanBundleTests(unittest.TestCase):
    def test_eight_file_bundle_is_deterministic_and_in_memory(self) -> None:
        first = make_bundle()
        second = make_bundle()
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.logical_plan, second.logical_plan)
        self.assertEqual(first.artifacts, second.artifacts)
        self.assertEqual(set(ARTIFACT_NAMES), set(first.artifacts))
        self.assertEqual(8, len(first.artifacts))

    def test_dirty_tracked_plan_is_diagnostic_only(self) -> None:
        repository = repository_binding(
            clean_worktree=False,
            clean_worktree_state="DIRTY_BOUND_INPUTS",
            dirty_bound_paths=["app/scripts/preflight/cli.py"],
        )
        plan = make_bundle(
            logical=logical_plan_fields(repository=repository)
        ).logical_plan
        self.assertIs(False, plan["authorization_eligible"])
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            plan["authorization_blockers"],
        )

    def test_staged_plan_is_diagnostic_only(self) -> None:
        repository = repository_binding(
            clean_worktree=False,
            clean_worktree_state="DIRTY_STAGED_CHANGES",
            staged_changes=True,
        )
        plan = make_bundle(
            logical=logical_plan_fields(repository=repository)
        ).logical_plan
        self.assertIs(False, plan["authorization_eligible"])
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            plan["authorization_blockers"],
        )

    def test_untracked_plan_is_diagnostic_only(self) -> None:
        repository = repository_binding(
            clean_worktree=False,
            clean_worktree_state="DIRTY_UNTRACKED_FILES",
            untracked_files=["app/untracked.txt"],
        )
        plan = make_bundle(
            logical=logical_plan_fields(repository=repository)
        ).logical_plan
        self.assertIs(False, plan["authorization_eligible"])
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            plan["authorization_blockers"],
        )

    def test_incomplete_git_state_plan_is_diagnostic_only(self) -> None:
        repository = repository_binding(
            clean_worktree=None,
            clean_worktree_state="INCONCLUSIVE_GIT_STATE",
            staged_changes=None,
            complete_git_status=False,
        )
        plan = make_bundle(
            logical=logical_plan_fields(repository=repository)
        ).logical_plan
        self.assertIs(False, plan["authorization_eligible"])
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            plan["authorization_blockers"],
        )

    def test_clean_complete_plan_may_be_authorization_eligible(
        self,
    ) -> None:
        plan = make_bundle().logical_plan
        self.assertIs(True, plan["authorization_eligible"])
        self.assertEqual([], plan["authorization_blockers"])

    def test_clean_eligible_plan_can_still_require_owner_input(
        self,
    ) -> None:
        logical = logical_plan_fields()
        logical["scope"]["candidate_scope_status"] = (
            "OWNER_INPUT_REQUIRED"
        )
        logical["scope"]["candidate_scope"] = {}
        dependencies = dependency_inputs()
        dependencies["candidate_scope"] = None
        plan = make_bundle(
            logical=logical,
            dependencies=dependencies,
        ).logical_plan
        self.assertIs(True, plan["authorization_eligible"])
        self.assertEqual([], plan["authorization_blockers"])
        self.assertEqual(
            "OWNER_INPUT_REQUIRED",
            plan["scope"]["candidate_scope_status"],
        )

    def test_plan_hash_binds_head_requirements_policy_and_tool_source(
        self,
    ) -> None:
        baseline = make_bundle()
        cases = {
            "repository-head": make_bundle(
                logical=logical_plan_fields(head="2"),
            ),
            "requirements": make_bundle(
                dependencies=dependency_inputs("b"),
            ),
            "filesystem-policy": make_bundle(
                logical=logical_plan_fields(policy_seed="b"),
            ),
            "tool-source": make_bundle(
                sources=source_bindings(tool_seed="b"),
            ),
        }

        for label, changed in cases.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    baseline.plan_sha256,
                    changed.plan_sha256,
                )
                self.assertNotEqual(baseline.plan_id, changed.plan_id)
        self.assertNotEqual(
            baseline.logical_plan["dependency_inputs_sha256"],
            cases["requirements"].logical_plan[
                "dependency_inputs_sha256"
            ],
        )
        self.assertNotEqual(
            baseline.logical_plan["source_bindings_sha256"],
            cases["tool-source"].logical_plan[
                "source_bindings_sha256"
            ],
        )

    def test_candidate_order_canonicalizes_to_the_same_bundle(self) -> None:
        first = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "explicit_versions": ["4.44.1", "4.40.0"],
                }
            )
        )
        second = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "explicit_versions": ["4.40.0", "4.44.1"],
                }
            )
        )
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.artifacts, second.artifacts)

    def test_plan_json_and_markdown_have_one_safety_decision(self) -> None:
        bundle = make_bundle()
        envelope = json.loads(bundle.artifacts["resolve-plan.json"])
        plan = envelope["plan"]
        markdown = bundle.artifacts["resolve-plan.md"]
        self.assertEqual("PLAN_READY", plan["plan_status"])
        self.assertEqual(
            "NETWORK_NOT_AUTHORIZED",
            plan["authorization_status"],
        )
        self.assertEqual("NO_GO", plan["decision"])
        self.assertEqual("NO_GO", plan["effective_decision"])
        self.assertIs(True, plan["authorization_eligible"])
        self.assertEqual([], plan["authorization_blockers"])
        self.assertEqual(bundle.plan_id, envelope["plan_id"])
        self.assertEqual(bundle.plan_sha256, envelope["plan_sha256"])
        for expected in (
            "- Plan status: `PLAN_READY`",
            "- Authorization status: `NETWORK_NOT_AUTHORIZED`",
            "- Decision: `NO_GO`",
            "- Effective decision: `NO_GO`",
            "- Authorization eligible: `true`",
            "- Authorization blockers: `none`",
            f"- Plan ID: `{bundle.plan_id}`",
            f"- Plan SHA-256: `{bundle.plan_sha256}`",
            "**NOT AUTHORIZATION**",
            "Network access and Resolve execution remain blocked.",
        ):
            self.assertIn(expected, markdown)
        self.assertTrue(
            all(value is False for value in plan["capabilities"].values())
        )

    def test_generated_template_is_unmistakably_non_authorizing(self) -> None:
        bundle = make_bundle()
        template = json.loads(
            bundle.artifacts["authorization-request.json"]
        )
        self.assertEqual("NOT_AUTHORIZED", template["authorization_id"])
        self.assertEqual("NOT_AUTHORIZED", template["issued_by"])
        self.assertEqual("NOT AUTHORIZATION", template["issued_at"])
        self.assertEqual("NOT AUTHORIZATION", template["expires_at"])
        self.assertEqual("NOT AUTHORIZATION", template["owner_statement"])
        self.assertIs(False, template["single_use"])
        for field in (
            "allow_install",
            "allow_uninstall",
            "allow_upgrade",
            "allow_launch",
            "allow_model_download",
            "allow_extension_update",
        ):
            self.assertIs(False, template[field])
        self.assertIn(
            "**NOT AUTHORIZATION**",
            bundle.artifacts["authorization-request.md"],
        )

    def test_bundle_tampering_fails_closed(self) -> None:
        bundle = make_bundle()
        mutations = {
            "json-plan-status": (
                "resolve-plan.json",
                ("PLAN_READY", "PLAN_TAMPERED"),
            ),
            "json-authorization-status": (
                "resolve-plan.json",
                (
                    "NETWORK_NOT_AUTHORIZED",
                    "NETWORK_AUTHORIZED",
                ),
            ),
            "json-plan-digest": (
                "resolve-plan.json",
                (bundle.plan_sha256, "sha256:" + ("f" * 64)),
            ),
            "json-candidate-scope": (
                "resolve-plan.json",
                ('"package": "gradio"', '"package": "other"'),
            ),
            "json-policy": (
                "resolve-plan.json",
                (
                    filesystem_policy()["policy_source_sha256"],
                    "sha256:" + ("f" * 64),
                ),
            ),
            "json-capability": (
                "resolve-plan.json",
                (
                    '"outside_filesystem_access": false',
                    '"outside_filesystem_access": true',
                ),
            ),
            "markdown-status": (
                "resolve-plan.md",
                (
                    "Authorization status: `NETWORK_NOT_AUTHORIZED`",
                    "Authorization status: `NETWORK_AUTHORIZED`",
                ),
            ),
        }
        for label, (name, replacement) in mutations.items():
            with self.subTest(label=label):
                values = {
                    bundle.directory / artifact_name: content
                    for artifact_name, content in bundle.artifacts.items()
                }
                original, changed = replacement
                self.assertIn(original, values[bundle.directory / name])
                values[bundle.directory / name] = values[
                    bundle.directory / name
                ].replace(original, changed, 1)
                fs = ReadOnlyMemoryFS(values)
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ):
                    load_plan_bundle(
                        fs,
                        resolver_paths(),
                        bundle.plan_json,
                    )
                self.assertFalse(fs.mutations)

    def test_closed_plan_schema_rejects_safety_fact_tampering(self) -> None:
        cases = {}

        authorization = logical_plan_fields()
        authorization["authorization_status"] = "NETWORK_AUTHORIZED"
        cases["authorization-status"] = authorization

        decision = logical_plan_fields()
        decision["decision"] = "GO"
        cases["decision"] = decision

        capability = logical_plan_fields()
        capability["capabilities"]["network_access"] = True
        cases["capability"] = capability

        no_install = logical_plan_fields()
        no_install["scope"]["no_install_policy"] = False
        cases["no-install"] = no_install

        tool_version = logical_plan_fields()
        tool_version["tool_version"] = "tampered"
        cases["tool-version"] = tool_version

        for label, logical in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    make_bundle(logical=logical)
                self.assertEqual(
                    "RESOLVE_PLAN_SCHEMA_INVALID",
                    raised.exception.reason_code,
                )

    def test_closed_plan_schema_rejects_dependency_cross_binding(self) -> None:
        logical = logical_plan_fields()
        logical["dependency"]["reason_code"] = "TAMPERED_REASON"
        with self.assertRaises(
            ResolveAuthorizationValidationError
        ) as raised:
            make_bundle(
                logical=logical,
                dependencies=dependency_inputs(),
            )
        self.assertEqual(
            "RESOLVE_PLAN_CROSS_BINDING_MISMATCH",
            raised.exception.reason_code,
        )

    def test_authorization_eligibility_cross_invariants_are_closed(
        self,
    ) -> None:
        cases = []
        eligible_false = logical_plan_fields()
        eligible_false["authorization_eligible"] = False
        cases.append(eligible_false)

        blocker_on_clean = logical_plan_fields()
        blocker_on_clean["authorization_eligible"] = False
        blocker_on_clean["authorization_blockers"] = [
            "RESOLVE_PLAN_DIRTY_WORKTREE"
        ]
        cases.append(blocker_on_clean)

        eligible_dirty = logical_plan_fields(
            repository=repository_binding(
                clean_worktree=False,
                clean_worktree_state="DIRTY_BOUND_INPUTS",
                dirty_bound_paths=["app/changed.py"],
            )
        )
        eligible_dirty["authorization_eligible"] = True
        eligible_dirty["authorization_blockers"] = []
        cases.append(eligible_dirty)

        for logical in cases:
            with self.subTest(logical=logical):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    make_bundle(logical=logical)
                self.assertEqual(
                    "RESOLVE_PLAN_CROSS_BINDING_MISMATCH",
                    raised.exception.reason_code,
                )

    def test_stage_e0_dependency_pair_and_core_are_closed(self) -> None:
        changed_pair = dependency_inputs()
        changed_pair["normalized_dependency_decision"]["decision"] = "GO"
        changed_pair["normalized_dependency_decision"][
            "reason_code"
        ] = "DEPENDENCY_CONSTRAINTS_SATISFIABLE"
        changed_pair["dependency_decision_sha256"] = canonical_sha256(
            changed_pair["normalized_dependency_decision"]
        )

        changed_core = dependency_inputs()
        changed_core["normalized_dependency_decision"][
            "minimal_unsatisfiable_cores"
        ] = []
        changed_core["dependency_decision_sha256"] = canonical_sha256(
            changed_core["normalized_dependency_decision"]
        )

        for label, dependencies in (
            ("decision-pair", changed_pair),
            ("conflict-core", changed_core),
        ):
            with self.subTest(label=label):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    make_bundle(dependencies=dependencies)
                self.assertEqual(
                    "RESOLVE_PLAN_SCHEMA_INVALID",
                    raised.exception.reason_code,
                )

    def test_closed_source_execution_identity_rejects_private_fields(self) -> None:
        cases = {}
        python = source_bindings()
        python["python"]["private_runtime_path"] = "forbidden"
        cases["python-extra-field"] = python

        pip = source_bindings()
        pip["pip"]["tree"]["root"] = "app/private-pip"
        cases["pip-tree-root"] = pip

        marker = source_bindings()
        marker["marker_environment"]["private_marker"] = "forbidden"
        cases["marker-extra-field"] = marker

        for label, sources in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    make_bundle(sources=sources)
                self.assertEqual(
                    "RESOLVE_PLAN_SCHEMA_INVALID",
                    raised.exception.reason_code,
                )


class CandidateScopeTests(unittest.TestCase):
    def test_explicit_versions_are_exact_bounded_and_canonical(self) -> None:
        result = validate_candidate_scope(
            {
                "package": "GRADIO",
                "explicit_versions": ["4.44.1", "4.40.0"],
            }
        )
        self.assertEqual(
            {
                "package": "gradio",
                "explicit_versions": ["4.40.0", "4.44.1"],
            },
            result,
        )

    def test_bounded_specifier_and_candidate_limit_validate(self) -> None:
        result = validate_candidate_scope(
            {
                "package": "gradio",
                "version_specifier": ">=4.40,<5",
                "maximum_candidate_count": 8,
            }
        )
        self.assertEqual("gradio", result["package"])
        self.assertEqual(8, result["maximum_candidate_count"])
        self.assertEqual(
            {"<5", ">=4.40"},
            set(result["version_specifier"].split(",")),
        )

    def test_candidate_scope_rejects_unbounded_or_ambiguous_input(
        self,
    ) -> None:
        invalid = (
            None,
            {},
            {"package": "other", "explicit_versions": ["1.0"]},
            {"package": "gradio", "explicit_versions": []},
            {
                "package": "gradio",
                "explicit_versions": ["4.40.0", "4.40.0"],
            },
            {"package": "gradio", "explicit_versions": ["04.40.0"]},
            {"package": "gradio", "explicit_versions": ["https://x.invalid"]},
            {
                "package": "gradio",
                "explicit_versions": [
                    f"4.{index}.0"
                    for index in range(MAX_CANDIDATES + 1)
                ],
            },
            {
                "package": "gradio",
                "version_specifier": ">=4.40",
                "maximum_candidate_count": 8,
            },
            {
                "package": "gradio",
                "version_specifier": "<5",
                "maximum_candidate_count": 8,
            },
            {
                "package": "gradio",
                "version_specifier": ">=4.40,<5",
                "maximum_candidate_count": True,
            },
            {
                "package": "gradio",
                "version_specifier": ">=4.40,<5",
                "maximum_candidate_count": 0,
            },
            {
                "package": "gradio",
                "version_specifier": ">=4.40,<5",
                "maximum_candidate_count": MAX_CANDIDATES + 1,
            },
            {
                "package": "gradio",
                "explicit_versions": ["4.40.0"],
                "version_specifier": ">=4.40,<5",
                "maximum_candidate_count": 8,
            },
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    validate_candidate_scope(value)
                self.assertEqual(
                    "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH",
                    raised.exception.reason_code,
                )

    def test_candidate_scope_changes_plan_hash(self) -> None:
        explicit = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "explicit_versions": ["4.40.0"],
                }
            )
        )
        changed_version = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "explicit_versions": ["4.44.1"],
                }
            )
        )
        bounded = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "version_specifier": ">=4.40,<5",
                    "maximum_candidate_count": 8,
                }
            )
        )
        changed_limit = make_bundle(
            logical=logical_plan_fields(
                candidate_scope={
                    "package": "gradio",
                    "version_specifier": ">=4.40,<5",
                    "maximum_candidate_count": 9,
                }
            )
        )
        self.assertNotEqual(explicit.plan_sha256, changed_version.plan_sha256)
        self.assertNotEqual(bounded.plan_sha256, changed_limit.plan_sha256)

    def test_hosts_indexes_and_operation_classes_are_closed(self) -> None:
        self.assertEqual(
            ["packages.example.invalid:443"],
            validate_hosts(["PACKAGES.EXAMPLE.INVALID:443"]),
        )
        self.assertEqual(
            ["CANDIDATE_METADATA", "PIP_DRY_RUN"],
            validate_operations(["PIP_DRY_RUN", "CANDIDATE_METADATA"]),
        )
        self.assertEqual(
            ["https://packages.example.invalid/simple"],
            validate_requested_indexes(
                ["https://packages.example.invalid/simple"],
                hosts=["packages.example.invalid"],
            ),
        )
        invalid_hosts = (
            ["*.example.invalid"],
            ["packages.example.invalid", "PACKAGES.EXAMPLE.INVALID"],
            ["packages.example.invalid:0"],
            ["packages.example.invalid:65536"],
            "packages.example.invalid",
        )
        for hosts in invalid_hosts:
            with self.subTest(hosts=hosts):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ):
                    validate_hosts(hosts)
        for operations in (
            ["install"],
            ["launch"],
            ["PIP_DRY_RUN", "PIP_DRY_RUN"],
            "PIP_DRY_RUN",
        ):
            with self.subTest(operations=operations):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ):
                    validate_operations(operations)
        with self.assertRaises(
            ResolveAuthorizationValidationError
        ) as raised:
            validate_requested_indexes(
                ["https://other.example.invalid/simple"],
                hosts=["packages.example.invalid"],
            )
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED",
            raised.exception.reason_code,
        )

    def test_candidate_metadata_adapter_is_pure_and_non_selecting(
        self,
    ) -> None:
        candidate = {
            "package": "gradio",
            "version": "4.44.1",
            "requires_python": ">=3.10,<4",
            "requires_dist": ["Pillow>=10,<13"],
            "source_hostname": "packages.example.invalid",
            "metadata_url": (
                "https://packages.example.invalid/metadata/gradio.json"
            ),
            "artifact_type": "metadata",
            "yanked": False,
        }
        result = candidate_metadata_record(
            candidate,
            candidate_scope={
                "package": "gradio",
                "explicit_versions": ["4.44.1"],
            },
            approved_hosts=["packages.example.invalid"],
            marker_environment={"python_version": "3.13"},
        )
        self.assertEqual("COMPATIBLE", result["compatibility_result"])
        self.assertTrue(result["compatible_with_pillow_12_3_0"])
        self.assertTrue(result["compatible_with_python_3_13_5"])
        self.assertEqual([], result["rejection_reasons"])
        self.assertIs(False, result["selected"])
        self.assertIs(False, result["recommended"])
        self.assertEqual(
            (
                "https://packages.example.invalid/"
                "<redacted-metadata-path>"
            ),
            result["metadata_url_sanitized"],
        )
        self.assertNotIn(
            "/metadata/gradio.json",
            result["metadata_url_sanitized"],
        )

        outside_scope = candidate_metadata_record(
            {**candidate, "version": "4.45.0", "yanked": True},
            candidate_scope={
                "package": "gradio",
                "explicit_versions": ["4.44.1"],
            },
            approved_hosts=["other.example.invalid"],
            marker_environment={"python_version": "3.13"},
        )
        self.assertEqual(
            "INCOMPATIBLE",
            outside_scope["compatibility_result"],
        )
        self.assertEqual(
            {
                "VERSION_OUTSIDE_OWNER_SCOPE",
                "SOURCE_HOST_NOT_APPROVED",
                "CANDIDATE_YANKED",
            },
            set(outside_scope["rejection_reasons"]),
        )
        self.assertIs(False, outside_scope["selected"])
        self.assertIs(False, outside_scope["recommended"])

    def test_time_window_and_unsafe_permissions_are_pure_denials(
        self,
    ) -> None:
        issued_at, expires_at = validate_authorization_time_window(
            "2026-07-24T11:00:00Z",
            "2026-07-24T13:00:00Z",
            now=FIXED_NOW,
        )
        self.assertLess(issued_at, FIXED_NOW)
        self.assertGreater(expires_at, FIXED_NOW)
        with self.assertRaises(
            ResolveAuthorizationValidationError
        ) as expired:
            validate_authorization_time_window(
                "2026-07-24T10:00:00Z",
                "2026-07-24T11:59:59Z",
                now=FIXED_NOW,
            )
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_EXPIRED",
            expired.exception.reason_code,
        )
        with self.assertRaises(ResolveAuthorizationValidationError):
            validate_authorization_time_window(
                "2026-07-24T11:00:00Z",
                "2026-07-25T11:00:01Z",
                now=FIXED_NOW,
            )

        safe = {
            "fixture_notice": "NOT AUTHORIZATION",
            "allow_install": False,
            "allow_uninstall": False,
            "allow_upgrade": False,
            "allow_launch": False,
            "allow_model_download": False,
            "allow_extension_update": False,
        }
        validate_safe_permissions(safe)
        for field in (
            "allow_install",
            "allow_uninstall",
            "allow_upgrade",
            "allow_launch",
            "allow_model_download",
            "allow_extension_update",
        ):
            for unsafe in (True, 0, None):
                with self.subTest(field=field, unsafe=unsafe):
                    with self.assertRaises(
                        ResolveAuthorizationValidationError
                    ) as raised:
                        validate_safe_permissions(
                            {**safe, field: unsafe}
                        )
                    self.assertEqual(
                        "RESOLVE_AUTHORIZATION_UNSAFE_PERMISSION",
                        raised.exception.reason_code,
                    )

    def test_non_authorizing_plan_binding_matrix(self) -> None:
        bundle = make_bundle()
        baseline = non_authorizing_plan_bindings(bundle)
        boundary = WorkspaceBoundary(WORKSPACE)
        validate_receipt_plan_bindings(
            baseline,
            bundle,
            boundary,
        )
        cases = (
            (
                "RESOLVE_AUTHORIZATION_HEAD_MISMATCH",
                {**baseline, "repository_head": "f" * 40},
            ),
            (
                "RESOLVE_AUTHORIZATION_PLAN_MISMATCH",
                {
                    **baseline,
                    "plan_sha256": "sha256:" + ("f" * 64),
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_POLICY_MISMATCH",
                {
                    **baseline,
                    "filesystem_policy_sha256": (
                        "sha256:" + ("f" * 64)
                    ),
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED",
                {
                    **baseline,
                    "approved_hosts": ["other.example.invalid"],
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH",
                {
                    **baseline,
                    "approved_operations": ["PIP_DRY_RUN"],
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH",
                {
                    **baseline,
                    "candidate_scope": {
                        "package": "gradio",
                        "explicit_versions": ["4.45.0"],
                    },
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH",
                {
                    **baseline,
                    "approved_temp_root": "Evidence/preflight/other",
                },
            ),
            (
                "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE",
                {
                    **baseline,
                    "approved_temp_root": (
                        "Private-Local/preflight/temp"
                    ),
                },
            ),
        )
        for reason, value in cases:
            with self.subTest(reason=reason, value=value):
                with self.assertRaises(
                    ResolveAuthorizationValidationError
                ) as raised:
                    validate_receipt_plan_bindings(
                        value,
                        bundle,
                        boundary,
                    )
                self.assertEqual(reason, raised.exception.reason_code)


class PublicOrchestrationSafetyTests(unittest.TestCase):
    def test_real_resolve_plan_orchestration_has_only_evidence_effects(
        self,
    ) -> None:
        fs = EvidenceWritingMemoryFS()
        orchestrator = public_orchestrator(fs)
        sources = source_bindings()
        dependencies = dependency_inputs()
        before_modules = set(sys.modules)
        with mock.patch.object(
            ResolveAuthorizationWorkflow,
            "_source_bindings",
            return_value=deepcopy(sources),
        ), mock.patch.object(
            ResolveAuthorizationWorkflow,
            "_dependency_inputs",
            return_value=deepcopy(dependencies),
        ), mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network access"),
        ) as socket_call, mock.patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("process execution"),
        ) as popen, mock.patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process execution"),
        ) as run:
            result = orchestrator.run_resolve_plan(
                repository_root=".",
                evidence_root="..\\Evidence\\preflight",
                candidate_scope={
                    "package": "gradio",
                    "explicit_versions": ["4.40.0", "4.44.1"],
                },
                requested_indexes=(
                    "https://packages.example.invalid/simple",
                ),
                requested_hosts=("packages.example.invalid",),
                approved_operations=("CANDIDATE_METADATA",),
            )
        self.assertEqual("NO_GO", result.decision)
        self.assertEqual("NETWORK_NOT_AUTHORIZED", result.reason_code)
        self.assertEqual("PLAN_READY", result.payload["plan_status"])
        self.assertEqual(
            "NETWORK_NOT_AUTHORIZED",
            result.payload["authorization_status"],
        )
        self.assertIs(True, result.payload["authorization_eligible"])
        self.assertEqual([], result.payload["authorization_blockers"])
        for field in (
            "network_access",
            "process_executed",
            "package_mutation",
            "application_launch",
            "model_loading",
            "generation",
        ):
            self.assertIs(False, result.payload[field])
        plan_directory = (
            WORKSPACE / result.payload["plan_directory"]
        )
        self.assertEqual(
            set(ARTIFACT_NAMES),
            {
                Path(path).name
                for path in fs.values
                if fs._key(Path(path).parent) == fs._key(plan_directory)
            },
        )
        evidence_key = fs._key(WORKSPACE / "Evidence")
        self.assertTrue(fs.mutations)
        self.assertTrue(
            all(
                fs._key(path) == evidence_key
                or fs._key(path).startswith(evidence_key + os.sep)
                for _, path in fs.mutations
            )
        )
        self.assertFalse(
            any("authorization-ledger" in path for _, path in fs.mutations)
        )
        self.assertFalse(fs.reads)
        socket_call.assert_not_called()
        popen.assert_not_called()
        run.assert_not_called()
        newly_loaded = set(sys.modules) - before_modules
        self.assertFalse(
            {"launch", "webui", "gradio", "PIL", "torch"}.intersection(
                newly_loaded
            )
        )


class AuthorizationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = make_bundle()
        self.fs = memory_fs_for_bundle(self.bundle)
        self.workflow = ResolveAuthorizationWorkflow(
            self.fs,
            APP,
            resolver_paths(),
            now=lambda: FIXED_NOW,
        )
        self.decision = deepcopy(
            self.bundle.dependency_inputs[
                "normalized_dependency_decision"
            ]
        )

    def authorization_path(self, name="denied-input.json") -> Path:
        return (
            resolver_paths().reports
            / "authorizations"
            / name
        )

    def add_non_authorization(self, value, name="denied-input.json"):
        path = self.authorization_path(name)
        self.fs.add(path, json.dumps(value, sort_keys=True) + "\n")
        return path

    def test_missing_authorization_is_structured_no_go(self) -> None:
        result = self.workflow.validate_authorization(
            plan_path=self.bundle.plan_json,
            authorization_path=None,
            dependency_decision=self.decision,
        )
        rendered = result.to_dict()
        self.assertEqual("FAIL", result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_MISSING",
            result.reason_code,
        )
        self.assertEqual("NO_GO", rendered["decision"])
        self.assertEqual("NO_GO", rendered["effective_decision"])
        self.assertIs(False, rendered["execution_authorized"])
        self.assertFalse(self.fs.mutations)

    def test_ineligible_plan_rejects_receipt_before_input_read(
        self,
    ) -> None:
        repository = repository_binding(
            clean_worktree=False,
            clean_worktree_state="DIRTY_BOUND_INPUTS",
            dirty_bound_paths=["app/scripts/preflight/cli.py"],
        )
        bundle = make_bundle(
            logical=logical_plan_fields(repository=repository)
        )
        fs = memory_fs_for_bundle(bundle)
        authorization_path = self.authorization_path(
            "ineligible-plan-input.json"
        )
        fs.add(
            authorization_path,
            '{"fixture_notice":"NOT AUTHORIZATION"}\n',
        )
        workflow = ResolveAuthorizationWorkflow(
            fs,
            APP,
            resolver_paths(),
            now=lambda: FIXED_NOW,
        )
        result = workflow.validate_authorization(
            plan_path=bundle.plan_json,
            authorization_path=authorization_path,
            dependency_decision=self.decision,
        )
        self.assertEqual("FAIL", result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE",
            result.reason_code,
        )
        self.assertEqual("NO_GO", result.to_dict()["decision"])
        self.assertNotIn(fs._key(authorization_path), fs.reads)
        self.assertFalse(fs.mutations)

    def test_ineligible_plan_cannot_consume_receipt(self) -> None:
        repository = repository_binding(
            clean_worktree=False,
            clean_worktree_state="DIRTY_UNTRACKED_FILES",
            untracked_files=["app/untracked.txt"],
        )
        bundle = make_bundle(
            logical=logical_plan_fields(repository=repository)
        )
        fs = memory_fs_for_bundle(bundle)
        authorization_path = self.authorization_path(
            "ineligible-consumption-input.json"
        )
        fs.add(
            authorization_path,
            '{"fixture_notice":"NOT AUTHORIZATION"}\n',
        )
        workflow = ResolveAuthorizationWorkflow(
            fs,
            APP,
            resolver_paths(),
            now=lambda: FIXED_NOW,
        )
        with self.assertRaises(
            ResolveAuthorizationValidationError
        ) as raised:
            workflow.consume_for_future_network_start(
                plan_path=bundle.plan_json,
                authorization_path=authorization_path,
                dependency_decision=self.decision,
                result_report_identity="sha256:" + ("a" * 64),
            )
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE",
            raised.exception.reason_code,
        )
        self.assertNotIn(fs._key(authorization_path), fs.reads)
        self.assertFalse(fs.mutations)

    def test_template_and_assistant_approval_cannot_validate(self) -> None:
        template = json.loads(
            self.bundle.artifacts["authorization-request.json"]
        )
        template_path = self.add_non_authorization(
            template,
            "generated-template.json",
        )
        template_result = self.workflow.validate_authorization(
            plan_path=self.bundle.plan_json,
            authorization_path=template_path,
            dependency_decision=self.decision,
        )
        self.assertEqual("FAIL", template_result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_SCHEMA_INVALID",
            template_result.reason_code,
        )

        assistant = {
            **template,
            "authorization_id": "assistant-denied-input",
            "issued_by": "assistant",
            "owner_statement": "NOT AUTHORIZATION: assistant said Approved",
        }
        assistant_path = self.add_non_authorization(
            assistant,
            "assistant-approval-denied.json",
        )
        assistant_result = self.workflow.validate_authorization(
            plan_path=self.bundle.plan_json,
            authorization_path=assistant_path,
            dependency_decision=self.decision,
        )
        self.assertEqual("FAIL", assistant_result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_NOT_OWNER_ISSUED",
            assistant_result.reason_code,
        )
        self.assertFalse(self.fs.mutations)

    def test_owner_statement_requires_substantive_text_after_prefix(
        self,
    ) -> None:
        template = json.loads(
            self.bundle.artifacts["authorization-request.json"]
        )
        denied = {
            **template,
            "authorization_id": "owner-review-denied",
            "issued_by": "human-owner",
            "owner_statement": "HUMAN OWNER AUTHORIZATION:   ",
        }
        path = self.add_non_authorization(
            denied,
            "empty-owner-statement-denied.json",
        )
        result = self.workflow.validate_authorization(
            plan_path=self.bundle.plan_json,
            authorization_path=path,
            dependency_decision=self.decision,
        )
        self.assertEqual("FAIL", result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_NOT_OWNER_ISSUED",
            result.reason_code,
        )
        self.assertFalse(self.fs.mutations)

    def test_outside_and_private_paths_fail_before_target_read(self) -> None:
        initial_reads = tuple(self.fs.reads)
        paths = (
            WORKSPACE.parent / "outside-authorization.json",
            WORKSPACE / "Private-Local" / "authorization.json",
            APP / "not-under-evidence.json",
        )
        for path in paths:
            with self.subTest(path=path):
                result = self.workflow.validate_authorization(
                    plan_path=self.bundle.plan_json,
                    authorization_path=path,
                    dependency_decision=self.decision,
                )
                self.assertEqual("FAIL", result.validation_status)
                self.assertEqual(
                    "RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE",
                    result.reason_code,
                )
        target_keys = {
            self.fs._key(path)
            for path in paths
        }
        self.assertFalse(target_keys.intersection(self.fs.reads))
        self.assertGreater(len(self.fs.reads), len(initial_reads))
        self.assertFalse(self.fs.mutations)

    def test_source_and_dependency_drift_fail_before_consumption(self) -> None:
        authorization_path = self.add_non_authorization(
            {"fixture_notice": "NOT AUTHORIZATION"}
        )
        cases = {}
        changed = deepcopy(self.bundle.source_bindings)
        changed["repository"]["head"] = "2" * 40
        cases["RESOLVE_AUTHORIZATION_HEAD_MISMATCH"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["repository"]["active_branch"] = "other-branch"
        cases["RESOLVE_AUTHORIZATION_BRANCH_MISMATCH"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["repository"]["upstream_neo_ref"] = "8" * 40
        changed["repository"]["neo_parity_left"] = None
        changed["repository"]["neo_parity_right"] = None
        changed["repository"]["neo_parity_state"] = "INCONCLUSIVE"
        cases["RESOLVE_AUTHORIZATION_NEO_PARITY_MISMATCH"] = (
            changed,
            None,
        )

        changed = deepcopy(self.bundle.source_bindings)
        changed["repository"].update(
            {
                "clean_worktree": False,
                "clean_worktree_state": "DIRTY_UNTRACKED_FILES",
                "untracked_files": ["app/untracked.txt"],
            }
        )
        cases["RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE"] = (
            changed,
            None,
        )

        changed = deepcopy(self.bundle.source_bindings)
        changed["filesystem_policy"] = filesystem_policy("b")
        cases["RESOLVE_AUTHORIZATION_POLICY_MISMATCH"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["requirements"]["sha256"] = "sha256:" + ("b" * 64)
        cases["RESOLVE_REQUIREMENTS_CHANGED_AFTER_PLAN"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["preflight_tool"]["source_sha256"] = (
            "sha256:" + ("b" * 64)
        )
        cases["RESOLVE_TOOL_CHANGED_AFTER_PLAN"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["launch_utils"]["sha256"] = "sha256:" + ("c" * 64)
        cases["RESOLVE_LAUNCHER_SOURCE_CHANGED_AFTER_PLAN"] = (
            changed,
            None,
        )

        changed = deepcopy(self.bundle.source_bindings)
        changed["marker_environment"]["python_version"] = "3.14"
        cases["RESOLVE_MARKER_ENVIRONMENT_CHANGED_AFTER_PLAN"] = (
            changed,
            None,
        )

        changed = deepcopy(self.bundle.source_bindings)
        changed["python"]["version"] = "3.13.6"
        cases["RESOLVE_PYTHON_CHANGED_AFTER_PLAN"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["pip"]["version"] = "changed"
        cases["RESOLVE_PIP_CHANGED_AFTER_PLAN"] = (changed, None)

        changed = deepcopy(self.bundle.source_bindings)
        changed["packaging"]["version"] = "changed"
        cases["RESOLVE_PACKAGING_CHANGED_AFTER_PLAN"] = (changed, None)

        changed_dependencies = deepcopy(self.bundle.dependency_inputs)
        changed_dependencies["candidate_research_question"] = "changed"
        cases["RESOLVE_DEPENDENCY_INPUTS_CHANGED_AFTER_PLAN"] = (
            self.bundle.source_bindings,
            changed_dependencies,
        )

        for reason, (current_sources, current_dependencies) in cases.items():
            with self.subTest(reason=reason):
                workflow = InjectedValidationWorkflow(
                    self.fs,
                    expected_sources=self.bundle.source_bindings,
                    expected_dependencies=self.bundle.dependency_inputs,
                    current_sources=current_sources,
                    current_dependencies=(
                        current_dependencies
                        if current_dependencies is not None
                        else self.bundle.dependency_inputs
                    ),
                )
                result = workflow.validate_authorization(
                    plan_path=self.bundle.plan_json,
                    authorization_path=authorization_path,
                    dependency_decision=self.decision,
                )
                self.assertEqual("FAIL", result.validation_status)
                self.assertEqual(reason, result.reason_code)
        self.assertFalse(self.fs.mutations)

    def test_replay_is_detected_without_writing_a_ledger_entry(self) -> None:
        authorization_id = "opaque-replay-test-id"
        ledger = (
            resolver_paths().authorization_ledger
            / (
                hashlib.sha256(
                    authorization_id.encode("ascii")
                ).hexdigest()
                + ".json"
            )
        )
        authorization_path = self.authorization_path()
        values = {
            self.bundle.directory / name: content
            for name, content in self.bundle.artifacts.items()
        }
        values[authorization_path] = (
            '{"fixture_notice":"NOT AUTHORIZATION"}\n'
        )
        fs = ReadOnlyMemoryFS(values, existing=(ledger,))
        workflow = InjectedValidationWorkflow(
            fs,
            expected_sources=self.bundle.source_bindings,
            expected_dependencies=self.bundle.dependency_inputs,
            authorization_id=authorization_id,
        )
        result = workflow.validate_authorization(
            plan_path=self.bundle.plan_json,
            authorization_path=authorization_path,
            dependency_decision=self.decision,
        )
        self.assertEqual("FAIL", result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_REUSED",
            result.reason_code,
        )
        self.assertFalse(fs.mutations)

    def test_validation_only_pass_is_repeatable_and_has_zero_effects(
        self,
    ) -> None:
        authorization_path = self.add_non_authorization(
            {"fixture_notice": "NOT AUTHORIZATION"}
        )
        workflow = InjectedValidationWorkflow(
            self.fs,
            expected_sources=self.bundle.source_bindings,
            expected_dependencies=self.bundle.dependency_inputs,
        )
        with mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network access"),
        ) as socket_call, mock.patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("process execution"),
        ) as popen, mock.patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process execution"),
        ) as run:
            first = workflow.validate_authorization(
                plan_path=self.bundle.plan_json,
                authorization_path=authorization_path,
                dependency_decision=self.decision,
            )
            second = workflow.validate_authorization(
                plan_path=self.bundle.plan_json,
                authorization_path=authorization_path,
                dependency_decision=self.decision,
            )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual("PASS", first.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_VALIDATION_ONLY_PASS",
            first.reason_code,
        )
        rendered = first.to_dict()
        self.assertEqual("NO_GO", rendered["decision"])
        self.assertEqual("NO_GO", rendered["effective_decision"])
        self.assertIs(False, rendered["execution_authorized"])
        for field in (
            "network_access",
            "process_executed",
            "package_mutation",
            "application_launch",
            "model_loading",
            "generation",
        ):
            self.assertIs(False, rendered[field])
        socket_call.assert_not_called()
        popen.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.fs.mutations)
        self.assertTrue(
            all(
                key.startswith(
                    self.fs._key(WORKSPACE / "Evidence") + os.sep
                )
                for key in self.fs.reads
            )
        )

    def test_final_receipt_recheck_can_expire_without_consumption(self) -> None:
        authorization_path = self.add_non_authorization(
            {"fixture_notice": "NOT AUTHORIZATION"}
        )
        workflow = InjectedValidationWorkflow(
            self.fs,
            expected_sources=self.bundle.source_bindings,
            expected_dependencies=self.bundle.dependency_inputs,
        )
        original = workflow._validated_receipt
        calls = 0

        def expire_on_final(raw, bundle, *, now=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ResolveAuthorizationValidationError(
                    "RESOLVE_AUTHORIZATION_EXPIRED"
                )
            return original(raw, bundle, now=now)

        with mock.patch.object(
            workflow,
            "_validated_receipt",
            side_effect=expire_on_final,
        ):
            result = workflow.validate_authorization(
                plan_path=self.bundle.plan_json,
                authorization_path=authorization_path,
                dependency_decision=self.decision,
            )
        self.assertEqual("FAIL", result.validation_status)
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_EXPIRED",
            result.reason_code,
        )
        self.assertEqual(2, calls)
        self.assertFalse(self.fs.mutations)

    def test_future_ledger_rejects_untrusted_report_identity_first(
        self,
    ) -> None:
        validation = AuthorizationValidationResult(
            validation_status="PASS",
            reason_code="RESOLVE_AUTHORIZATION_VALIDATION_ONLY_PASS",
            plan_id=self.bundle.plan_id,
            plan_sha256=self.bundle.plan_sha256,
            filesystem_policy=filesystem_policy(),
            authorization_id="opaque-validation-only-id",
            authorization_sha256="sha256:" + ("a" * 64),
        )
        invalid_identities = (
            "report-one",
            "Evidence/preflight/reports/report.json",
            "token=private-value",
            "x" * 10_000,
        )
        for identity in invalid_identities:
            with self.subTest(identity=identity[:80]):
                with mock.patch.object(
                    self.workflow,
                    "validate_authorization",
                    return_value=validation,
                ):
                    with self.assertRaises(
                        ResolveAuthorizationValidationError
                    ) as raised:
                        self.workflow.consume_for_future_network_start(
                            plan_path=self.bundle.plan_json,
                            authorization_path=self.authorization_path(),
                            dependency_decision=self.decision,
                            result_report_identity=identity,
                        )
                self.assertEqual(
                    "RESOLVE_RESULT_REPORT_IDENTITY_INVALID",
                    raised.exception.reason_code,
                )
        self.assertFalse(self.fs.mutations)

    def test_future_ledger_rechecks_expiry_immediately_before_create(
        self,
    ) -> None:
        authorization_path = self.add_non_authorization(
            {"fixture_notice": "NOT AUTHORIZATION"},
            "expiry-recheck-denied.json",
        )
        validation = AuthorizationValidationResult(
            validation_status="PASS",
            reason_code="RESOLVE_AUTHORIZATION_VALIDATION_ONLY_PASS",
            plan_id=self.bundle.plan_id,
            plan_sha256=self.bundle.plan_sha256,
            filesystem_policy=filesystem_policy(),
            authorization_id="opaque-validation-only-id",
            authorization_sha256="sha256:" + ("a" * 64),
        )
        with mock.patch.object(
            self.workflow,
            "validate_authorization",
            return_value=validation,
        ), mock.patch.object(
            self.workflow,
            "_validated_receipt",
            side_effect=ResolveAuthorizationValidationError(
                "RESOLVE_AUTHORIZATION_EXPIRED"
            ),
        ):
            with self.assertRaises(
                ResolveAuthorizationValidationError
            ) as raised:
                self.workflow.consume_for_future_network_start(
                    plan_path=self.bundle.plan_json,
                    authorization_path=authorization_path,
                    dependency_decision=self.decision,
                    result_report_identity="sha256:" + ("b" * 64),
                )
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_EXPIRED",
            raised.exception.reason_code,
        )
        self.assertFalse(self.fs.mutations)

    def test_validation_json_and_markdown_agree(self) -> None:
        result = AuthorizationValidationResult(
            validation_status="FAIL",
            reason_code="RESOLVE_AUTHORIZATION_MISSING",
            plan_id=self.bundle.plan_id,
            plan_sha256=self.bundle.plan_sha256,
            filesystem_policy=filesystem_policy(),
        )
        rendered_json = json.loads(render_validation_json(result))
        rendered_markdown = render_validation_markdown(result)
        self.assertEqual("NO_GO", rendered_json["decision"])
        self.assertEqual("NO_GO", rendered_json["effective_decision"])
        self.assertIs(False, rendered_json["execution_authorized"])
        for expected in (
            "- Validation status: `FAIL`",
            "- Decision: `NO_GO`",
            "- Effective decision: `NO_GO`",
            "- Reason code: `RESOLVE_AUTHORIZATION_MISSING`",
            "- Execution authorized: `false`",
            f"- Plan ID: `{self.bundle.plan_id}`",
            f"- Plan SHA-256: `{self.bundle.plan_sha256}`",
            (
                "- Validation SHA-256: "
                f"`{rendered_json['validation_sha256']}`"
            ),
            "**NOT AUTHORIZATION**",
        ):
            self.assertIn(expected, rendered_markdown)

    def test_validation_cli_reports_missing_without_execution(self) -> None:
        fs = EvidenceWritingMemoryFS(
            {
                self.bundle.directory / name: content
                for name, content in self.bundle.artifacts.items()
            }
        )
        orchestrator = public_orchestrator(fs)
        output = io.StringIO()
        with mock.patch.object(
            cli,
            "PreflightOrchestrator",
            return_value=orchestrator,
        ), mock.patch.object(
            socket,
            "socket",
            side_effect=AssertionError("network access"),
        ) as socket_call, mock.patch.object(
            subprocess,
            "Popen",
            side_effect=AssertionError("process execution"),
        ) as popen, mock.patch.object(
            subprocess,
            "run",
            side_effect=AssertionError("process execution"),
        ) as run, redirect_stdout(output):
            exit_code = cli.main(
                [
                    "validate-authorization",
                    "--plan",
                    (
                        "Evidence/preflight/resolve-plans/"
                        f"{self.bundle.plan_id}/resolve-plan.json"
                    ),
                ]
            )
        self.assertEqual(2, exit_code)
        rendered = json.loads(output.getvalue())
        self.assertEqual("FAIL", rendered["validation_status"])
        self.assertEqual("NO_GO", rendered["decision"])
        self.assertEqual(
            "RESOLVE_AUTHORIZATION_MISSING",
            rendered["reason_code"],
        )
        self.assertIs(False, rendered["execution_authorized"])
        socket_call.assert_not_called()
        popen.assert_not_called()
        run.assert_not_called()
        self.assertEqual(
            {
                "resolve-authorization-validation.json",
                "resolve-authorization-validation.md",
            },
            {
                Path(path).name
                for operation, path in fs.mutations
                if operation == "write-text"
            },
        )
        self.assertFalse(
            any(
                "authorization-ledger" in path
                for _, path in fs.mutations
            )
        )
        self.assertTrue(
            all(
                fs._key(path).startswith(
                    fs._key(WORKSPACE / "Evidence") + os.sep
                )
                for _, path in fs.mutations
            )
        )


REPOSITORY_ATTRIBUTES = (
    b"# Keep the pinned Forge Studio frontend byte-stable across "
    b"Windows checkouts.\n"
    b"forge_studio/frontend/** text eol=lf\n"
    b"forge_studio/frontend/** whitespace=-trailing-space\n"
    b"forge_studio/frontend/fonts/OFL.txt -text\n"
    b"forge_studio/frontend/**/*.png -text\n"
    b"forge_studio/frontend/**/*.ttf -text\n"
)


def git_tree_id(blobs: dict[str, bytes]) -> tuple[str, dict[str, bytes]]:
    """Build nested Git tree objects from ``path -> blob id`` pairs."""

    root: dict[str, object] = {}
    for path, object_id in blobs.items():
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})  # type: ignore[assignment]
        node[parts[-1]] = object_id

    objects: dict[str, bytes] = {}

    def build(node: dict[str, object]) -> str:
        records: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            encoded = name.encode("utf-8")
            if isinstance(value, dict):
                sub_id = build(value)
                records.append(
                    (
                        encoded + b"/",
                        b"40000 "
                        + encoded
                        + b"\0"
                        + bytes.fromhex(sub_id),
                    )
                )
            else:
                records.append(
                    (
                        encoded,
                        b"100644 "
                        + encoded
                        + b"\0"
                        + bytes.fromhex(str(value)),
                    )
                )
        body = b"".join(
            record for _, record in sorted(records, key=lambda item: item[0])
        )
        raw, tree_id = git_object("tree", body)
        objects[tree_id] = raw
        return tree_id

    return build(root), objects


def attributes_state_values(
    *,
    tracked: dict[str, bytes],
    worktree: dict[str, bytes] | None = None,
    head: dict[str, bytes] | None = None,
    extra_files: dict[str, bytes] | None = None,
    deleted: tuple[str, ...] = (),
    exclude: bytes = b"",
) -> dict[Path, bytes]:
    """Build a multi-file in-memory repository for attribute scenarios.

    ``tracked`` is the index content per path. ``head`` overrides committed
    content to create staged differences. ``worktree`` overrides on-disk
    content to create modifications. ``deleted`` removes tracked paths from
    the worktree entirely.
    """

    worktree = worktree or {}
    head = head or {}
    extra_files = extra_files or {}

    objects: dict[str, bytes] = {}
    index_entries: list[tuple[str, bytes, int]] = []
    head_blobs: dict[str, str] = {}
    for path in sorted(tracked):
        index_raw, index_id = git_object("blob", tracked[path])
        objects[index_id] = index_raw
        index_entries.append((path, bytes.fromhex(index_id), 0))
        committed = head.get(path, tracked[path])
        head_raw, head_id = git_object("blob", committed)
        objects[head_id] = head_raw
        head_blobs[path] = head_id

    tree_id, tree_objects = git_tree_id(head_blobs)
    objects.update(tree_objects)
    commit_body = (
        f"tree {tree_id}\n".encode("ascii")
        + b"author Fixture <fixture@example.invalid> 0 +0000\n"
        + b"committer Fixture <fixture@example.invalid> 0 +0000\n"
        + b"\nfixture\n"
    )
    commit_raw, commit_id = git_object("commit", commit_body)
    objects[commit_id] = commit_raw

    values: dict[Path, bytes] = {
        APP / ".git" / "HEAD": b"ref: refs/heads/tools/test-attributes\n",
        (
            APP
            / ".git"
            / "refs"
            / "heads"
            / "tools"
            / "test-attributes"
        ): commit_id.encode("ascii") + b"\n",
        APP / ".git" / "index": git_index(index_entries),
        APP / ".git" / "info" / "exclude": exclude,
    }
    for object_id, raw in objects.items():
        values[
            APP / ".git" / "objects" / object_id[:2] / object_id[2:]
        ] = zlib.compress(raw)
    for path, content in tracked.items():
        if path in deleted:
            continue
        values[APP / Path(path)] = worktree.get(path, content)
    for path, content in extra_files.items():
        values[APP / Path(path)] = content
    return values


class GitAttributesCleanlinessTests(unittest.TestCase):
    """Semantic .gitattributes support for the in-process cleanliness proof.

    Row numbers refer to the owner-approved in-process test matrix that
    replaced the porcelain/subprocess rows in the controlling handoff.
    """

    LF_PAGE = b"<!doctype html>\n<title>studio</title>\n"
    PNG = b"\x89PNG\r\n\x1a\n\x00fixture\x00"

    def state(self, values: dict[Path, bytes]) -> dict[str, object]:
        return _bound_worktree_state(ReadOnlyMemoryFS(values), APP)

    def clean_repository(self) -> dict[Path, bytes]:
        return attributes_state_values(
            tracked={
                ".gitattributes": REPOSITORY_ATTRIBUTES,
                "forge_studio/frontend/index.html": self.LF_PAGE,
                "forge_studio/frontend/img/logo.png": self.PNG,
                "forge_studio/frontend/fonts/OFL.txt": b"OFL\r\ntext\r\n",
                "README.md": b"readme\n",
            }
        )

    # -- Row 1 -- regression: the defect this change fixes ----------------

    def test_clean_repository_with_repository_attributes_is_clean(
        self,
    ) -> None:
        """Row 1. Before the fix this returned INCONCLUSIVE_GIT_STATE."""

        state = self.state(self.clean_repository())
        self.assertIs(True, state["clean_worktree"])
        self.assertEqual("CLEAN", state["clean_worktree_state"])
        self.assertIs(True, state["complete_git_status"])
        self.assertIs(True, state["index_matches_head"])
        self.assertIs(True, state["worktree_matches_index"])
        self.assertIs(True, state["untracked_scan_complete"])
        self.assertIs(True, state["snapshot_rechecked"])
        self.assertIs(False, state["staged_changes"])
        self.assertEqual([], state["dirty_bound_paths"])
        self.assertEqual([], state["untracked_files"])

    def test_clean_attributes_repository_is_authorization_eligible(
        self,
    ) -> None:
        """Row 1. The corrected state must clear the eligibility blocker."""

        state = self.state(self.clean_repository())
        repository = {
            **state,
            "neo_parity_state": "PARITY",
            "neo_parity_left": 0,
            "neo_parity_right": 0,
            "neo_ref": "a" * 40,
            "upstream_neo_ref": "a" * 40,
        }
        self.assertEqual([], _authorization_blockers(repository))

    def test_crlf_worktree_under_text_eol_lf_is_clean(self) -> None:
        """Row 1. Check-in normalization applies when text is set."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                },
                worktree={
                    "forge_studio/frontend/index.html": (
                        self.LF_PAGE.replace(b"\n", b"\r\n")
                    )
                },
            )
        )
        self.assertIs(True, state["clean_worktree"])
        self.assertEqual("CLEAN", state["clean_worktree_state"])

    # -- Rows 2-3 -- real modifications stay ineligible --------------------

    def test_modified_tracked_file_under_text_eol_lf_is_dirty(self) -> None:
        """Row 2."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                },
                worktree={
                    "forge_studio/frontend/index.html": b"<!doctype html>\n"
                },
            )
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_BOUND_INPUTS",
            state["clean_worktree_state"],
        )
        self.assertEqual(
            ["app/forge_studio/frontend/index.html"],
            state["dirty_bound_paths"],
        )

    def test_modified_tracked_file_under_minus_text_is_dirty(self) -> None:
        """Row 3. A -text path gets no normalization rescue."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/img/logo.png": self.PNG,
                },
                worktree={
                    "forge_studio/frontend/img/logo.png": (
                        self.PNG.replace(b"\r\n", b"\n")
                    )
                },
            )
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            ["app/forge_studio/frontend/img/logo.png"],
            state["dirty_bound_paths"],
        )

    def test_text_attribute_does_not_normalize_binary_content(self) -> None:
        """Row 3. text=auto must not normalize NUL-bearing content."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": b"* text=auto\n",
                    "data.bin": b"\0payload\n",
                },
                worktree={"data.bin": b"\0payload\r\n"},
            )
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(["app/data.bin"], state["dirty_bound_paths"])

    # -- Rows 4-8 -- non-attribute detection must survive ------------------

    def test_staged_change_remains_ineligible(self) -> None:
        """Row 4."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "README.md": b"staged\n",
                },
                head={"README.md": b"committed\n"},
            )
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_STAGED_CHANGES",
            state["clean_worktree_state"],
        )
        self.assertIs(True, state["staged_changes"])
        self.assertIs(False, state["index_matches_head"])

    def test_deleted_tracked_file_remains_ineligible(self) -> None:
        """Row 5. Preserved pre-existing behaviour: fail closed, not dirty.

        The snapshot-recheck loop re-reads every stage-zero index entry with
        no ``FileNotFoundError`` guard, so a tracked path missing from the
        worktree has always produced ``INCONCLUSIVE_GIT_STATE`` rather than
        ``DIRTY_BOUND_INPUTS``. This change does not alter that. Both outcomes
        are authorization-ineligible; the assertion records which one holds so
        a future change cannot silently downgrade it to clean.
        """

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                },
                deleted=("forge_studio/frontend/index.html",),
            )
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )
        self.assertIs(False, state["complete_git_status"])

    def test_renamed_tracked_file_remains_ineligible(self) -> None:
        """Row 6. A rename is a missing tracked path plus an untracked one.

        Inherits the Row 5 fail-closed path for the missing source name.
        """

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                },
                deleted=("forge_studio/frontend/index.html",),
                extra_files={
                    "forge_studio/frontend/renamed.html": self.LF_PAGE
                },
            )
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_deleted_tracked_file_is_never_eligible(self) -> None:
        """Row 5. Whatever the classification, eligibility must be denied."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                },
                deleted=("forge_studio/frontend/index.html",),
            )
        )
        repository = {
            **state,
            "neo_parity_state": "PARITY",
            "neo_parity_left": 0,
            "neo_parity_right": 0,
            "neo_ref": "a" * 40,
            "upstream_neo_ref": "a" * 40,
        }
        self.assertEqual(
            ["RESOLVE_PLAN_DIRTY_WORKTREE"],
            _authorization_blockers(repository),
        )

    def test_untracked_file_remains_ineligible(self) -> None:
        """Row 7."""

        state = self.state(
            attributes_state_values(
                tracked={".gitattributes": REPOSITORY_ATTRIBUTES},
                extra_files={"scratch.txt": b"scratch\n"},
            )
        )
        self.assertIs(False, state["clean_worktree"])
        self.assertEqual(
            "DIRTY_UNTRACKED_FILES",
            state["clean_worktree_state"],
        )
        self.assertEqual(["app/scratch.txt"], state["untracked_files"])

    def test_ignored_file_does_not_block_eligibility(self) -> None:
        """Row 8."""

        state = self.state(
            attributes_state_values(
                tracked={".gitattributes": REPOSITORY_ATTRIBUTES},
                extra_files={"build.log": b"log\n"},
                exclude=b"build.log\n",
            )
        )
        self.assertIs(True, state["clean_worktree"])
        self.assertEqual("CLEAN", state["clean_worktree_state"])
        self.assertEqual([], state["untracked_files"])

    # -- Row 9 -- supported non-checkout metadata --------------------------

    def test_whitespace_only_attribute_is_supported(self) -> None:
        """Row 9. whitespace never changes checked-out bytes."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": b"*.md whitespace=-trailing-space\n",
                    "README.md": b"readme\n",
                }
            )
        )
        self.assertIs(True, state["clean_worktree"])
        self.assertEqual("CLEAN", state["clean_worktree_state"])

    # -- Rows 10-13 -- unsupported checkout-affecting attributes ----------

    def assert_attributes_fail_closed(self, attributes: bytes) -> None:
        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": attributes,
                    "README.md": b"readme\n",
                }
            )
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )
        self.assertIs(False, state["complete_git_status"])

    def test_filter_attribute_fails_closed(self) -> None:
        """Row 10."""

        self.assert_attributes_fail_closed(b"* filter=lfs\n")

    def test_working_tree_encoding_attribute_fails_closed(self) -> None:
        """Row 11."""

        self.assert_attributes_fail_closed(
            b"*.txt working-tree-encoding=UTF-16\n"
        )

    def test_ident_attribute_fails_closed(self) -> None:
        """Row 12."""

        self.assert_attributes_fail_closed(b"*.c ident\n")

    def test_macro_definition_fails_closed(self) -> None:
        """Row 13."""

        self.assert_attributes_fail_closed(b"[attr]bin -text -diff\n")

    def test_binary_macro_use_fails_closed(self) -> None:
        """Row 13. The built-in binary macro is outside the modelled set."""

        self.assert_attributes_fail_closed(b"*.png binary\n")

    def test_unknown_attribute_name_fails_closed(self) -> None:
        """Row 13."""

        self.assert_attributes_fail_closed(b"*.txt export-subst\n")

    def test_unspecified_attribute_form_fails_closed(self) -> None:
        """Row 13. The !attr unspecified form is not modelled."""

        self.assert_attributes_fail_closed(b"*.txt !text\n")

    def test_unsupported_eol_value_fails_closed(self) -> None:
        """Row 13."""

        self.assert_attributes_fail_closed(b"*.txt text eol=native\n")

    def test_negative_pattern_fails_closed(self) -> None:
        """Row 13. Negative patterns are invalid in .gitattributes."""

        self.assert_attributes_fail_closed(b"!*.txt text\n")

    def test_escaped_pattern_fails_closed(self) -> None:
        """Row 13. Escapes and quoting are outside the modelled grammar."""

        self.assert_attributes_fail_closed(b'"quoted path.txt" text\n')

    # -- Row 14 -- malformed attributes files -----------------------------

    def test_pattern_without_attributes_fails_closed(self) -> None:
        """Row 14."""

        self.assert_attributes_fail_closed(b"*.txt\n")

    def test_non_utf8_attributes_file_fails_closed(self) -> None:
        """Row 14."""

        self.assert_attributes_fail_closed(b"*.txt text\n\xff\xfe\n")

    def test_dot_dot_pattern_fails_closed(self) -> None:
        """Row 14."""

        self.assert_attributes_fail_closed(b"../escape text\n")

    # -- Row 15 -- unreadable attributes file -----------------------------

    def test_unreadable_attributes_file_fails_closed(self) -> None:
        """Row 15. Present to exists() but absent from the byte store."""

        values = attributes_state_values(
            tracked={"README.md": b"readme\n"},
        )
        fs = ReadOnlyMemoryFS(
            values,
            existing=(APP / ".gitattributes",),
        )
        state = _bound_worktree_state(fs, APP)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_untracked_attributes_file_fails_closed(self) -> None:
        """Row 15. An untracked attributes file cannot be trusted."""

        values = attributes_state_values(tracked={"README.md": b"readme\n"})
        values[APP / ".gitattributes"] = b"* text=auto\n"
        values[APP / ".git" / "info" / "exclude"] = b".gitattributes\n"
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_modified_attributes_file_fails_closed(self) -> None:
        """Row 15. A modified attributes file cannot describe the checkout."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "README.md": b"readme\n",
                },
                worktree={".gitattributes": b"* text=auto\n"},
            )
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    # -- Row 16 -- corrupt index ------------------------------------------

    def test_corrupt_index_fails_closed(self) -> None:
        """Row 16."""

        values = self.clean_repository()
        values[APP / ".git" / "index"] = b"DIRC\x00\x00\x00\x02corrupt"
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_unreadable_index_fails_closed(self) -> None:
        """Row 16."""

        values = self.clean_repository()
        del values[APP / ".git" / "index"]
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    # -- Row 17 -- missing Git objects ------------------------------------

    def test_missing_head_ref_fails_closed(self) -> None:
        """Row 17."""

        values = self.clean_repository()
        del values[
            APP / ".git" / "refs" / "heads" / "tools" / "test-attributes"
        ]
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_missing_commit_object_fails_closed(self) -> None:
        """Row 17."""

        values = self.clean_repository()
        head = values[
            APP / ".git" / "refs" / "heads" / "tools" / "test-attributes"
        ].decode("ascii").strip()
        del values[APP / ".git" / "objects" / head[:2] / head[2:]]
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    # -- Row 18 -- nested attributes files --------------------------------

    def test_nested_attributes_file_fails_closed(self) -> None:
        """Row 18. Directory-relative precedence is not modelled."""

        state = self.state(
            attributes_state_values(
                tracked={
                    ".gitattributes": REPOSITORY_ATTRIBUTES,
                    "forge_studio/frontend/.gitattributes": b"*.js text\n",
                    "forge_studio/frontend/index.html": self.LF_PAGE,
                }
            )
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    def test_local_info_attributes_fails_closed(self) -> None:
        """Row 18. .git/info/attributes stays outside the modelled subset."""

        values = self.clean_repository()
        values[APP / ".git" / "info" / "attributes"] = b"* text\n"
        state = self.state(values)
        self.assertIsNone(state["clean_worktree"])
        self.assertEqual(
            "INCONCLUSIVE_GIT_STATE",
            state["clean_worktree_state"],
        )

    # -- Snapshot integrity -----------------------------------------------

    def test_attributes_change_during_proof_fails_closed(self) -> None:
        """The recheck must reject attributes that change mid-proof."""

        class MutatingAttributesFS(ReadOnlyMemoryFS):
            def __init__(self, values):
                super().__init__(values)
                self.attribute_reads = 0

            def read_bytes(self, path, *, max_bytes=None):
                if self._key(path) == self._key(APP / ".gitattributes"):
                    self.attribute_reads += 1
                    if self.attribute_reads > 2:
                        return b"*.md text\n"
                return super().read_bytes(path, max_bytes=max_bytes)

        state = _bound_worktree_state(
            MutatingAttributesFS(self.clean_repository()),
            APP,
        )
        self.assertIsNone(state["clean_worktree"])
        self.assertIs(False, state["snapshot_rechecked"])


if __name__ == "__main__":
    unittest.main()
