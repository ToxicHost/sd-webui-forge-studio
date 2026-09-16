"""Data-only contracts for workspace preflight modes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Any, Mapping

from .boundary import BoundaryViolation, WorkspaceFS


class ContractError(ValueError):
    """Raised when a preflight contract is unsafe or malformed."""


_ABSOLUTE_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s\"'])[a-z]:[\\/]")
_UNC_OR_DEVICE_PATH = re.compile(r"(?:^|[\s\"'])(?:\\\\|//)(?:\?|\.|[^/\\])")
FILESYSTEM_POLICY_VERSION = "forge-filesystem-policy/v1"
NORMALIZED_PERMITTED_ROOTS = ("app/", "Evidence/", "Reference/")
PRIVATE_LOCAL_STATUS = "PROHIBITED"
POLICY_SOURCE = "app/AGENTS.md"


def _walk_strings(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            values.extend(_walk_strings(key))
            values.extend(_walk_strings(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            values.extend(_walk_strings(child))
    return values


def assert_sanitized_structure(value: Any) -> None:
    """Reject user-specific absolute and UNC/device paths in contracts."""

    for text in _walk_strings(value):
        if text == "<WORKSPACE>":
            continue
        if _ABSOLUTE_WINDOWS_PATH.search(text) or _UNC_OR_DEVICE_PATH.search(text):
            raise ContractError("CONTRACT_CONTAINS_UNSANITIZED_ABSOLUTE_PATH")


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    reason_code: str,
) -> None:
    if set(value) != expected:
        raise ContractError(reason_code)


@dataclass(frozen=True)
class FilesystemPolicyContract:
    """Exact, sanitized binding to the owner filesystem policy."""

    filesystem_policy_version: str
    normalized_permitted_roots: tuple[str, ...]
    private_local_status: str
    policy_source: str
    policy_source_sha256: str
    outside_access_allowed: bool

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "FilesystemPolicyContract":
        _require_exact_keys(
            value,
            {
                "filesystem_policy_version",
                "normalized_permitted_roots",
                "private_local_status",
                "policy_source",
                "policy_source_sha256",
                "outside_access_allowed",
            },
            "FILESYSTEM_POLICY_FIELDS_INVALID",
        )
        raw_roots = value.get("normalized_permitted_roots")
        if (
            not isinstance(raw_roots, list)
            or not all(isinstance(root, str) for root in raw_roots)
        ):
            raise ContractError("FILESYSTEM_POLICY_ROOTS_INVALID")
        outside_access_allowed = value.get("outside_access_allowed")
        if outside_access_allowed is not False:
            raise ContractError("FILESYSTEM_POLICY_OUTSIDE_ACCESS_ENABLED")
        fields = (
            value.get("filesystem_policy_version"),
            value.get("private_local_status"),
            value.get("policy_source"),
            value.get("policy_source_sha256"),
        )
        if not all(isinstance(field, str) for field in fields):
            raise ContractError("FILESYSTEM_POLICY_VALUE_TYPES_INVALID")
        policy = cls(
            filesystem_policy_version=value["filesystem_policy_version"],
            normalized_permitted_roots=tuple(raw_roots),
            private_local_status=value["private_local_status"],
            policy_source=value["policy_source"],
            policy_source_sha256=value["policy_source_sha256"],
            outside_access_allowed=False,
        )
        policy.validate_schema()
        return policy

    @classmethod
    def current(cls, fs: WorkspaceFS) -> "FilesystemPolicyContract":
        boundary = fs.boundary
        actual_roots = {
            name: os.path.normcase(os.path.normpath(os.fspath(path)))
            for name, path in boundary.allowed_roots.items()
        }
        expected_roots = {
            name: os.path.normcase(
                os.path.normpath(
                    os.fspath(boundary.workspace_root / name)
                )
            )
            for name in ("app", "Evidence", "Reference")
        }
        if actual_roots != expected_roots:
            raise ContractError("FILESYSTEM_POLICY_BOUNDARY_ROOTS_DRIFT")
        expected_private = os.path.normcase(
            os.path.normpath(
                os.fspath(boundary.workspace_root / "Private-Local")
            )
        )
        if os.path.normcase(
            os.path.normpath(os.fspath(boundary.private_local_root))
        ) != expected_private:
            raise ContractError("FILESYSTEM_POLICY_PRIVATE_LOCAL_DRIFT")
        probes = (
            (
                boundary.workspace_root
                / "Private-Local"
                / "preflight-policy-probe",
                "PRIVATE_LOCAL_OWNER_AUTHORIZATION_REQUIRED",
            ),
            (
                boundary.workspace_root
                / ".."
                / "preflight-policy-probe",
                "FILESYSTEM_PATH_OUTSIDE_WORKSPACE_BOUNDARY",
            ),
        )
        for probe, expected_reason in probes:
            try:
                boundary.authorize_lexically(
                    probe,
                    "filesystem-policy-denial-probe",
                )
            except BoundaryViolation as exc:
                if exc.reason_code != expected_reason:
                    raise ContractError(
                        "FILESYSTEM_POLICY_DENIAL_PROBE_DRIFT"
                    ) from exc
            else:
                raise ContractError(
                    "FILESYSTEM_POLICY_DENIAL_PROBE_ALLOWED"
                )
        policy_path = boundary.workspace_root / "app" / "AGENTS.md"
        content = fs.read_bytes(policy_path, max_bytes=4_000_000)
        return cls(
            filesystem_policy_version=FILESYSTEM_POLICY_VERSION,
            normalized_permitted_roots=NORMALIZED_PERMITTED_ROOTS,
            private_local_status=PRIVATE_LOCAL_STATUS,
            policy_source=POLICY_SOURCE,
            policy_source_sha256=(
                "sha256:" + hashlib.sha256(content).hexdigest()
            ),
            outside_access_allowed=False,
        )

    def validate_schema(self) -> None:
        if self.filesystem_policy_version != FILESYSTEM_POLICY_VERSION:
            raise ContractError("FILESYSTEM_POLICY_VERSION_INVALID")
        if self.normalized_permitted_roots != NORMALIZED_PERMITTED_ROOTS:
            raise ContractError("FILESYSTEM_POLICY_ROOTS_INVALID")
        if self.private_local_status != PRIVATE_LOCAL_STATUS:
            raise ContractError("FILESYSTEM_POLICY_PRIVATE_LOCAL_ENABLED")
        if self.policy_source != POLICY_SOURCE:
            raise ContractError("FILESYSTEM_POLICY_SOURCE_INVALID")
        if (
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                self.policy_source_sha256,
            )
            is None
        ):
            raise ContractError("FILESYSTEM_POLICY_SOURCE_HASH_INVALID")
        if self.outside_access_allowed:
            raise ContractError("FILESYSTEM_POLICY_OUTSIDE_ACCESS_ENABLED")

    def validate_current(self, fs: WorkspaceFS) -> None:
        current = self.current(fs)
        if self.policy_source_sha256 != current.policy_source_sha256:
            raise ContractError("FILESYSTEM_POLICY_SOURCE_HASH_MISMATCH")
        if self.to_dict() != current.to_dict():
            raise ContractError("FILESYSTEM_POLICY_CURRENT_STATE_DRIFT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystem_policy_version": self.filesystem_policy_version,
            "normalized_permitted_roots": list(
                self.normalized_permitted_roots
            ),
            "private_local_status": self.private_local_status,
            "policy_source": self.policy_source,
            "policy_source_sha256": self.policy_source_sha256,
            "outside_access_allowed": False,
        }


@dataclass(frozen=True)
class CommandProcessorContract:
    """Human-supplied identity only; discovery and verification stay blocked."""

    identity: str
    discovery_permitted: bool
    verification_status: str

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "CommandProcessorContract":
        _require_exact_keys(
            value,
            {
                "identity",
                "discovery_permitted",
                "verification_status",
            },
            "COMMAND_PROCESSOR_FIELDS_INVALID",
        )
        if (
            not isinstance(value.get("identity"), str)
            or not isinstance(value.get("verification_status"), str)
            or value.get("discovery_permitted") is not False
        ):
            raise ContractError("COMMAND_PROCESSOR_VALUE_TYPES_INVALID")
        return cls(
            identity=value["identity"],
            discovery_permitted=False,
            verification_status=value["verification_status"],
        )

    def validate(self) -> None:
        if self.discovery_permitted:
            raise ContractError("COMMAND_PROCESSOR_DISCOVERY_PROHIBITED")
        if self.verification_status != "OWNER_APPROVAL_REQUIRED":
            raise ContractError("COMMAND_PROCESSOR_VERIFICATION_NOT_DEFERRED")


@dataclass(frozen=True)
class PreflightContract:
    """Sanitized inputs shared by Static, Contract, and Verify modes."""

    schema_version: str
    workspace_root: str
    venv_python: str
    command_processor: CommandProcessorContract
    network_authorized: bool
    private_local_authorized: bool
    filesystem_policy: FilesystemPolicyContract

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreflightContract":
        assert_sanitized_structure(value)
        _require_exact_keys(
            value,
            {
                "schema_version",
                "workspace_root",
                "venv_python",
                "command_processor",
                "network",
                "private_local",
                "filesystem_policy",
            },
            "PREFLIGHT_CONTRACT_FIELDS_INVALID",
        )
        if not all(
            isinstance(value.get(key), str)
            for key in ("schema_version", "workspace_root", "venv_python")
        ):
            raise ContractError("PREFLIGHT_CONTRACT_VALUE_TYPES_INVALID")
        for key in ("command_processor", "network", "private_local", "filesystem_policy"):
            if not isinstance(value.get(key), Mapping):
                raise ContractError("PREFLIGHT_CONTRACT_VALUE_TYPES_INVALID")
        _require_exact_keys(
            value["network"],
            {"authorized"},
            "PREFLIGHT_NETWORK_FIELDS_INVALID",
        )
        _require_exact_keys(
            value["private_local"],
            {"authorized"},
            "PREFLIGHT_PRIVATE_LOCAL_FIELDS_INVALID",
        )
        if (
            value["network"].get("authorized") is not False
            or value["private_local"].get("authorized") is not False
        ):
            raise ContractError("PREFLIGHT_AUTHORITY_VALUE_INVALID")
        contract = cls(
            schema_version=value["schema_version"],
            workspace_root=value["workspace_root"],
            venv_python=value["venv_python"],
            command_processor=CommandProcessorContract.from_dict(
                value["command_processor"]
            ),
            network_authorized=False,
            private_local_authorized=False,
            filesystem_policy=FilesystemPolicyContract.from_dict(
                value["filesystem_policy"]
            ),
        )
        contract.validate()
        return contract

    def validate(self) -> None:
        if self.schema_version != "forge-preflight-contract/v2":
            raise ContractError("UNSUPPORTED_PREFLIGHT_CONTRACT_SCHEMA")
        if self.workspace_root != "<WORKSPACE>":
            raise ContractError("WORKSPACE_ROOT_MUST_BE_SANITIZED")
        normalized_python = self.venv_python.replace("\\", "/")
        if normalized_python.casefold() != "app/venv/scripts/python.exe":
            raise ContractError("WORKSPACE_VENV_PYTHON_REQUIRED")
        self.command_processor.validate()
        if self.network_authorized:
            raise ContractError("CONTRACT_CANNOT_AUTHORIZE_NETWORK")
        if self.private_local_authorized:
            raise ContractError("CONTRACT_CANNOT_AUTHORIZE_PRIVATE_LOCAL")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_root": self.workspace_root,
            "venv_python": self.venv_python.replace("\\", "/"),
            "command_processor": {
                "identity": self.command_processor.identity,
                "discovery_permitted": False,
                "verification_status": self.command_processor.verification_status,
            },
            "network": {"authorized": False},
            "private_local": {"authorized": False},
            "filesystem_policy": self.filesystem_policy.to_dict(),
        }


def load_contract(fs: WorkspaceFS, path: str) -> PreflightContract:
    value = fs.read_json(path, max_chars=1_000_000)
    if not isinstance(value, Mapping):
        raise ContractError("PREFLIGHT_CONTRACT_MUST_BE_AN_OBJECT")
    contract = PreflightContract.from_dict(value)
    contract.filesystem_policy.validate_current(fs)
    return contract
