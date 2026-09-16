"""Deterministic, sanitized JSON and Markdown preflight reports."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .boundary import BoundaryViolation, WorkspaceFS
from .contracts import (
    ContractError,
    FilesystemPolicyContract,
    PreflightContract,
)


class ReportError(ValueError):
    """Raised when report generation or verification fails closed."""


_WINDOWS_ABSOLUTE = re.compile(r"(?im)(?<![A-Za-z0-9_])[A-Z]:[\\/].*$")
_UNC_PATH = re.compile(r"(?m)(?<![:A-Za-z0-9_])(?:\\\\|//)[^:\r\n]*$")
_URL = re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_KEYS = {
    "authorization",
    "access_token",
    "api_key",
    "client_secret",
    "cookie",
    "password",
    "proxy_password",
    "refresh_token",
    "set_cookie",
    "signature",
    "secret",
    "sig",
    "token",
}
REPORT_JSON_MAX_CHARS = 4_000_000
REPORT_MARKDOWN_MAX_CHARS = 4_000_000
CONTRACT_DECISIONS = {"GO", "GO_WITH_WARNINGS", "NO_GO"}


def _sanitize_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname or "<HOST_REDACTED>"
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"{hostname}{port}"
        path = "/<PATH_REDACTED>" if parsed.path not in ("", "/") else parsed.path
        query = "<QUERY_REDACTED>" if parsed.query else ""
        fragment = "<FRAGMENT_REDACTED>" if parsed.fragment else ""
        return urlunsplit((parsed.scheme, netloc, path, query, fragment))
    except (ValueError, UnicodeError):
        return "<URL_REDACTED>"


def sanitize_text(value: str) -> str:
    sanitized = _WINDOWS_ABSOLUTE.sub("<ABSOLUTE_PATH_REDACTED>", value)
    sanitized = _UNC_PATH.sub("<UNC_PATH_REDACTED>", sanitized)
    sanitized = _URL.sub(_sanitize_url, sanitized)
    sanitized = _BEARER.sub("Bearer <REDACTED>", sanitized)
    return sanitized


def sanitize_structure(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _SECRET_KEYS:
        return "<REDACTED>"
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        sanitized_mapping: dict[str, Any] = {}
        for child_key, child in value.items():
            raw_key = str(child_key)
            sanitized_key = sanitize_text(raw_key)
            if sanitized_key in sanitized_mapping:
                raise ReportError("REPORT_KEY_COLLISION_AFTER_SANITIZATION")
            sanitized_mapping[sanitized_key] = sanitize_structure(
                child, key=raw_key
            )
        return sanitized_mapping
    if isinstance(value, list):
        return [sanitize_structure(child) for child in value]
    if isinstance(value, tuple):
        return [sanitize_structure(child) for child in value]
    return value


def canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def report_fingerprint(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_payload(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class ReportRenderer:
    """Render two views from one sanitized canonical result."""

    def prepare(self, result: Mapping[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_structure(deepcopy(dict(result)))
        if not isinstance(sanitized, dict):
            raise ReportError("REPORT_RESULT_MUST_BE_AN_OBJECT")
        sanitized.pop("report_id", None)
        sanitized["report_id"] = report_fingerprint(sanitized)
        return sanitized

    @staticmethod
    def render_json(prepared: Mapping[str, Any]) -> str:
        return json.dumps(
            prepared,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    @staticmethod
    def render_markdown(prepared: Mapping[str, Any]) -> str:
        report_id = str(prepared["report_id"])
        mode = str(prepared.get("mode", "UNKNOWN"))
        decision = str(prepared.get("decision", "INCONCLUSIVE"))
        contract_decision = str(
            prepared.get("contract_decision", decision)
        )
        effective_decision = str(
            prepared.get("effective_decision", contract_decision)
        )
        reason_code = str(prepared.get("reason_code", "UNSPECIFIED"))
        summary = str(prepared.get("summary", ""))
        runtime = prepared.get("runtime", {})
        python = runtime.get("python", {}) if isinstance(runtime, Mapping) else {}
        constraints = prepared.get("dependency_decision") or {}
        constraint_rows = constraints.get("constraints", [])
        capabilities = prepared.get("capabilities")
        privacy = prepared.get("privacy")
        filesystem_policy = prepared.get("filesystem_policy")
        if (
            not isinstance(capabilities, Mapping)
            or not isinstance(privacy, Mapping)
            or not isinstance(filesystem_policy, Mapping)
        ):
            raise ReportError("REPORT_SAFETY_FIELDS_REQUIRED")

        def display_optional(value: Any) -> str:
            return "none" if value is None else str(value)

        def markdown_cell(value: Any) -> str:
            return (
                str(value)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("|", "\\|")
                .replace("\r", " ")
                .replace("\n", " ")
            )

        lines = [
            "# Forge Preflight Report",
            "",
            f"<!-- forge-preflight-report-id: {report_id} -->",
            "",
            f"- Report ID: `{report_id}`",
            f"- Mode: `{mode}`",
            f"- Decision: `{decision}`",
            f"- Contract decision: `{contract_decision}`",
            f"- Effective decision: `{effective_decision}`",
            f"- Reason code: `{reason_code}`",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Runtime boundary",
            "",
            f"- Python: `{python.get('path', 'UNAVAILABLE')}`",
            f"- Python version: `{python.get('version', 'UNAVAILABLE')}`",
            "- Base interpreter inspected: "
            f"`{str(python.get('base_interpreter_inspected', False)).lower()}`",
            "- Command-processor discovery performed: "
            f"`{str(runtime.get('command_processor', {}).get('discovery_performed', False)).lower()}`",
            "",
            "## Filesystem policy",
            "",
            "- Policy version: "
            f"`{filesystem_policy.get('filesystem_policy_version', 'UNAVAILABLE')}`",
            "- Permitted roots: `"
            + ", ".join(
                str(root)
                for root in filesystem_policy.get(
                    "normalized_permitted_roots", []
                )
            )
            + "`",
            "- Private-Local status: "
            f"`{filesystem_policy.get('private_local_status', 'UNAVAILABLE')}`",
            "- Policy source: "
            f"`{filesystem_policy.get('policy_source', 'UNAVAILABLE')}`",
            "- Policy source SHA-256: "
            f"`{filesystem_policy.get('policy_source_sha256', 'UNAVAILABLE')}`",
            "- Outside access allowed: "
            f"`{str(filesystem_policy.get('outside_access_allowed', True)).lower()}`",
            "",
            "## Dependency decision",
            "",
            f"- Decision: `{constraints.get('decision', decision)}`",
            f"- Reason code: `{constraints.get('reason_code', reason_code)}`",
            f"- Intersection: `{constraints.get('normalized_intersection', 'UNAVAILABLE')}`",
            "- Selected replacement: "
            f"`{display_optional(constraints.get('selected_replacement'))}`",
            "- Recommended replacement: "
            f"`{display_optional(constraints.get('recommended_replacement'))}`",
            "",
            "| Owner | Requirement | Source |",
            "|---|---|---|",
        ]
        for row in constraint_rows:
            source = row.get("source", {})
            source_text = source.get("path") or source.get("source_id") or "UNAVAILABLE"
            if source.get("line"):
                source_text = f"{source_text}:{source['line']}"
            lines.append(
                f"| {markdown_cell(row.get('owner', 'UNAVAILABLE'))} | "
                f"`{markdown_cell(row.get('requirement', 'UNAVAILABLE'))}` | "
                f"`{markdown_cell(source_text)}` |"
            )

        lines.extend(
            [
                "",
                "## Safety",
                "",
                "- Private-Local access performed: "
                f"`{str(bool(capabilities.get('private_local_access'))).lower()}`",
                "- Outside filesystem access performed: "
                f"`{str(bool(capabilities.get('outside_filesystem_access'))).lower()}`",
                "- Network access performed: "
                f"`{str(bool(capabilities.get('network_access'))).lower()}`",
                "- Package mutation performed: "
                f"`{str(bool(capabilities.get('package_mutation'))).lower()}`",
                "- Application launch performed: "
                f"`{str(bool(capabilities.get('application_launch'))).lower()}`",
                "- Model loading performed: "
                f"`{str(bool(capabilities.get('model_loading'))).lower()}`",
                "- Generation performed: "
                f"`{str(bool(capabilities.get('generation'))).lower()}`",
                "- User-specific absolute paths retained: "
                f"`{str(bool(privacy.get('absolute_paths_in_report'))).lower()}`",
                "",
            ]
        )
        return "\n".join(lines)

    def write_bundle(
        self,
        fs: WorkspaceFS,
        result: Mapping[str, Any],
        json_path: str,
        markdown_path: str,
    ) -> dict[str, Any]:
        prepared = self.prepare(result)
        json_partial = f"{json_path}.partial"
        markdown_partial = f"{markdown_path}.partial"
        fs.write_text(json_partial, self.render_json(prepared))
        fs.write_text(markdown_partial, self.render_markdown(prepared))
        fs.replace(json_partial, json_path)
        fs.replace(markdown_partial, markdown_path)
        return prepared


class VerificationRenderer:
    """Render one canonical JSON/Markdown view of a Verify outcome."""

    def prepare(self, result: Mapping[str, Any]) -> dict[str, Any]:
        sanitized = sanitize_structure(deepcopy(dict(result)))
        if not isinstance(sanitized, dict):
            raise ReportError("VERIFICATION_RESULT_MUST_BE_AN_OBJECT")
        sanitized.pop("verification_report_id", None)
        _validate_verification_model(sanitized)
        sanitized["verification_report_id"] = report_fingerprint(sanitized)
        return sanitized

    @staticmethod
    def render_json(prepared: Mapping[str, Any]) -> str:
        _validate_verification_model(prepared, prepared=True)
        return json.dumps(
            prepared,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    @staticmethod
    def render_markdown(prepared: Mapping[str, Any]) -> str:
        _validate_verification_model(prepared, prepared=True)
        verification_status = str(
            prepared.get("verification_status", "FAIL")
        )
        contract_decision = prepared.get("contract_decision")
        effective_decision = str(
            prepared.get("effective_decision", "NO_GO")
        )
        verification_reason = str(
            prepared.get("verification_reason", "CONTRACT_SCHEMA_INVALID")
        )
        report_id = str(
            prepared.get("verification_report_id", "UNAVAILABLE")
        )
        filesystem_policy = prepared.get("filesystem_policy", {})
        if not isinstance(filesystem_policy, Mapping):
            raise ReportError("VERIFICATION_FILESYSTEM_POLICY_REQUIRED")
        lines = [
            "# Forge Preflight Verification",
            "",
            f"<!-- forge-preflight-verification-id: {report_id} -->",
            "",
            f"- Verification report ID: `{report_id}`",
            f"- Verification status: `{verification_status}`",
            "- Contract decision: "
            f"`{contract_decision if contract_decision is not None else 'UNTRUSTED'}`",
            f"- Effective decision: `{effective_decision}`",
            f"- Verification reason: `{verification_reason}`",
            "",
            "## Effective outcome",
            "",
        ]
        if verification_status == "PASS" and effective_decision == "NO_GO":
            lines.extend(
                [
                    (
                        "**Verification passed, but launch remains blocked by "
                        "the contract decision.**"
                    ),
                    "",
                ]
            )
        elif verification_status == "PASS":
            lines.extend(
                [
                    (
                        "Verification passed and preserved the contract's "
                        "effective decision."
                    ),
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    (
                        "**Verification failed. The effective decision is "
                        "NO_GO.**"
                    ),
                    "",
                ]
            )
        lines.extend(
            [
                "## Filesystem policy",
                "",
                "- Policy version: "
                f"`{filesystem_policy.get('filesystem_policy_version', 'UNAVAILABLE')}`",
                "- Permitted roots: `"
                + ", ".join(
                    str(root)
                    for root in filesystem_policy.get(
                        "normalized_permitted_roots", []
                    )
                )
                + "`",
                "- Private-Local status: "
                f"`{filesystem_policy.get('private_local_status', 'UNAVAILABLE')}`",
                "- Policy source: "
                f"`{filesystem_policy.get('policy_source', 'UNAVAILABLE')}`",
                "- Policy source SHA-256: "
                f"`{filesystem_policy.get('policy_source_sha256', 'UNAVAILABLE')}`",
                "- Outside access allowed: "
                f"`{str(filesystem_policy.get('outside_access_allowed', True)).lower()}`",
                "",
                "## Safety",
                "",
                "- Network access performed: `false`",
                "- Package mutation performed: `false`",
                "- Application launch performed: `false`",
                "- Model loading performed: `false`",
                "- Generation performed: `false`",
                "",
            ]
        )
        return "\n".join(lines)

    def write_bundle(
        self,
        fs: WorkspaceFS,
        result: Mapping[str, Any],
        json_path: str,
        markdown_path: str,
    ) -> dict[str, Any]:
        prepared = self.prepare(result)
        json_partial = f"{json_path}.partial"
        markdown_partial = f"{markdown_path}.partial"
        fs.write_text(json_partial, self.render_json(prepared))
        fs.write_text(markdown_partial, self.render_markdown(prepared))
        fs.replace(json_partial, json_path)
        fs.replace(markdown_partial, markdown_path)
        return prepared


_VERIFICATION_BASE_FIELDS = {
    "schema_version",
    "mode",
    "verification_status",
    "contract_decision",
    "effective_decision",
    "verification_reason",
    "filesystem_policy",
    "network_access",
    "command_processor_discovery",
    "package_mutation",
    "application_launch",
    "model_loading",
    "generation",
}
_VERIFICATION_SOURCE_FIELDS = {
    "source_report_id",
    "source_mode",
    "contract_reason_code",
}


def _validate_verification_model(
    value: Mapping[str, Any],
    *,
    prepared: bool = False,
) -> None:
    status = value.get("verification_status")
    expected = set(_VERIFICATION_BASE_FIELDS)
    if status == "PASS":
        expected.update(_VERIFICATION_SOURCE_FIELDS)
    elif status != "FAIL":
        raise ReportError("VERIFICATION_STATUS_INVALID")
    if prepared:
        expected.add("verification_report_id")
    if set(value) != expected:
        raise ReportError("VERIFICATION_FIELDS_INVALID")
    if (
        value.get("schema_version") != "forge-preflight-verification/v2"
        or value.get("mode") != "VERIFY"
        or not isinstance(value.get("verification_reason"), str)
        or re.fullmatch(
            r"[A-Z][A-Z0-9_:-]{0,127}",
            value["verification_reason"],
        )
        is None
    ):
        raise ReportError("VERIFICATION_SCHEMA_INVALID")
    contract_decision = value.get("contract_decision")
    effective_decision = value.get("effective_decision")
    if status == "PASS":
        if (
            contract_decision not in CONTRACT_DECISIONS
            or effective_decision != contract_decision
            or not isinstance(value.get("source_report_id"), str)
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                value["source_report_id"],
            )
            is None
            or not isinstance(value.get("source_mode"), str)
            or not value["source_mode"]
            or not isinstance(value.get("contract_reason_code"), str)
            or not value["contract_reason_code"]
        ):
            raise ReportError("VERIFICATION_DECISION_MODEL_INVALID")
    elif contract_decision is not None or effective_decision != "NO_GO":
        raise ReportError("VERIFICATION_DECISION_MODEL_INVALID")
    for key in (
        "network_access",
        "command_processor_discovery",
        "package_mutation",
        "application_launch",
        "model_loading",
        "generation",
    ):
        if value.get(key) is not False:
            raise ReportError("VERIFICATION_SAFETY_FIELD_INVALID")
    raw_policy = value.get("filesystem_policy")
    if not isinstance(raw_policy, Mapping):
        raise ReportError("VERIFICATION_FILESYSTEM_POLICY_REQUIRED")
    try:
        FilesystemPolicyContract.from_dict(raw_policy)
    except ContractError as exc:
        raise ReportError("VERIFICATION_FILESYSTEM_POLICY_INVALID") from exc
    if prepared:
        report_id = value.get("verification_report_id")
        if (
            not isinstance(report_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", report_id) is None
        ):
            raise ReportError("VERIFICATION_REPORT_ID_INVALID")
        unsigned = dict(value)
        unsigned.pop("verification_report_id")
        if report_fingerprint(unsigned) != report_id:
            raise ReportError("VERIFICATION_REPORT_ID_MISMATCH")


def verification_failure_result(
    *,
    verification_reason: str,
    filesystem_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a fail-closed Verify model without trusting source decisions."""

    return {
        "schema_version": "forge-preflight-verification/v2",
        "mode": "VERIFY",
        "verification_status": "FAIL",
        "contract_decision": None,
        "effective_decision": "NO_GO",
        "verification_reason": verification_reason,
        "filesystem_policy": dict(filesystem_policy),
        "network_access": False,
        "command_processor_discovery": False,
        "package_mutation": False,
        "application_launch": False,
        "model_loading": False,
        "generation": False,
    }


def _iter_report_paths(
    value: Any,
    *,
    parent_key: str | None = None,
) -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if key in {"path", "policy_source", "venv_python"}:
                if not isinstance(child, str):
                    raise ReportError("CONTRACT_SCHEMA_INVALID")
                paths.append(child)
            elif key == "inputs":
                if not isinstance(child, list) or not all(
                    isinstance(item, str) for item in child
                ):
                    raise ReportError("CONTRACT_SCHEMA_INVALID")
                paths.extend(child)
            else:
                paths.extend(_iter_report_paths(child, parent_key=key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            paths.extend(_iter_report_paths(child, parent_key=parent_key))
    return paths


def _validate_report_contract(
    fs: WorkspaceFS,
    payload: Mapping[str, Any],
) -> tuple[str, FilesystemPolicyContract]:
    if payload.get("schema_version") != "forge-preflight-report/v2":
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    mode = payload.get("mode")
    expected_fields = {
        "schema_version",
        "mode",
        "decision",
        "contract_decision",
        "effective_decision",
        "reason_code",
        "summary",
        "runtime",
        "capabilities",
        "inputs",
        "installed",
        "checks",
        "filesystem_policy",
        "dependency_decision",
        "privacy",
        "report_id",
    }
    if mode == "CONTRACT":
        expected_fields.add("contract")
    elif mode != "STATIC":
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    if set(payload) != expected_fields:
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    decision = payload.get("decision")
    if not isinstance(decision, str) or decision not in CONTRACT_DECISIONS:
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    if (
        payload.get("contract_decision") != decision
        or payload.get("effective_decision") != decision
        or not isinstance(payload.get("reason_code"), str)
        or not payload["reason_code"]
    ):
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    raw_policy = payload.get("filesystem_policy")
    if not isinstance(raw_policy, Mapping):
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    try:
        policy = FilesystemPolicyContract.from_dict(raw_policy)
        policy.validate_current(fs)
    except ContractError as exc:
        if str(exc) in {
            "FILESYSTEM_POLICY_SOURCE_HASH_MISMATCH",
            "FILESYSTEM_POLICY_CURRENT_STATE_DRIFT",
        }:
            raise ReportError("CURRENT_STATE_DRIFT") from exc
        raise ReportError("CONTRACT_SCHEMA_INVALID") from exc
    if mode == "CONTRACT":
        raw_contract = payload.get("contract")
        if not isinstance(raw_contract, Mapping):
            raise ReportError("CONTRACT_SCHEMA_INVALID")
        try:
            embedded_contract = PreflightContract.from_dict(raw_contract)
        except ContractError as exc:
            raise ReportError("CONTRACT_SCHEMA_INVALID") from exc
        if embedded_contract.filesystem_policy.to_dict() != policy.to_dict():
            raise ReportError("CONTRACT_SCHEMA_INVALID")
    capabilities = payload.get("capabilities")
    expected_capability_fields = {
        "filesystem_roots",
        "private_local_access",
        "outside_filesystem_access",
        "network_access",
        "package_mutation",
        "application_launch",
        "model_loading",
        "generation",
    }
    if (
        not isinstance(capabilities, Mapping)
        or set(capabilities) != expected_capability_fields
        or capabilities.get("filesystem_roots")
        != list(policy.normalized_permitted_roots)
        or any(
            capabilities.get(key) is not False
            for key in expected_capability_fields - {"filesystem_roots"}
        )
    ):
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    privacy = payload.get("privacy")
    if (
        not isinstance(privacy, Mapping)
        or set(privacy)
        != {
            "absolute_paths_in_report",
            "environment_dump_performed",
            "secrets_collected",
        }
        or any(value is not False for value in privacy.values())
    ):
        raise ReportError("CONTRACT_SCHEMA_INVALID")
    try:
        for path in sorted(set(_iter_report_paths(payload))):
            fs.boundary.authorize_lexically(
                path,
                "verify-contract-referenced-path",
            )
    except BoundaryViolation as exc:
        raise ReportError("CONTRACT_SCHEMA_INVALID") from exc
    return decision, policy


def verify_report_pair(
    fs: WorkspaceFS,
    json_path: str,
    markdown_path: str,
) -> dict[str, Any]:
    """Fail closed unless JSON and Markdown describe the same result."""

    try:
        payload = WorkspaceFS.read_json(
            fs,
            json_path,
            max_chars=REPORT_JSON_MAX_CHARS,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason.startswith("JSON_INPUT_EXCEEDS_SIZE_LIMIT"):
            raise ReportError("REPORT_JSON_EXCEEDS_SIZE_LIMIT") from exc
        if reason.startswith("JSON_DUPLICATE_KEY_REJECTED"):
            raise ReportError("REPORT_JSON_DUPLICATE_KEY_REJECTED") from exc
        raise ReportError("REPORT_JSON_INVALID") from exc

    try:
        raw_json = fs.read_text(
            json_path,
            max_chars=REPORT_JSON_MAX_CHARS,
        )
    except ValueError as exc:
        if str(exc) == "TEXT_INPUT_EXCEEDS_SIZE_LIMIT":
            raise ReportError("REPORT_JSON_EXCEEDS_SIZE_LIMIT") from exc
        raise
    if sanitize_text(raw_json) != raw_json:
        raise ReportError("REPORT_JSON_CONTAINS_PRIVATE_OR_SECRET_VALUE")
    if not isinstance(payload, dict):
        raise ReportError("REPORT_JSON_MUST_BE_AN_OBJECT")

    sanitized_payload = sanitize_structure(payload)
    if sanitized_payload != payload:
        raise ReportError("REPORT_CONTAINS_PRIVATE_OR_SECRET_VALUE")

    report_id = payload.get("report_id")
    if not isinstance(report_id, str):
        raise ReportError("REPORT_ID_MISSING")
    unsigned = dict(payload)
    unsigned.pop("report_id", None)
    if report_fingerprint(unsigned) != report_id:
        raise ReportError("REPORT_ID_MISMATCH")
    if raw_json != ReportRenderer.render_json(payload):
        raise ReportError("REPORT_JSON_CANONICAL_VIEW_MISMATCH")
    contract_decision, filesystem_policy = _validate_report_contract(
        fs,
        payload,
    )

    try:
        raw_markdown = fs.read_text(
            markdown_path,
            max_chars=REPORT_MARKDOWN_MAX_CHARS,
        )
    except ValueError as exc:
        if str(exc) == "TEXT_INPUT_EXCEEDS_SIZE_LIMIT":
            raise ReportError("REPORT_MARKDOWN_EXCEEDS_SIZE_LIMIT") from exc
        raise
    header, separator, _ = raw_markdown.partition("## Summary")
    if not separator:
        raise ReportError("REPORT_MARKDOWN_SUMMARY_BOUNDARY_MISSING")

    markers = {
        "report_id": rf"^- Report ID: `{re.escape(report_id)}`$",
        "mode": rf"^- Mode: `{re.escape(str(payload.get('mode')))}`$",
        "decision": rf"^- Decision: `{re.escape(str(payload.get('decision')))}`$",
        "contract_decision": (
            rf"^- Contract decision: "
            rf"`{re.escape(str(payload.get('contract_decision')))}`$"
        ),
        "effective_decision": (
            rf"^- Effective decision: "
            rf"`{re.escape(str(payload.get('effective_decision')))}`$"
        ),
        "reason": (
            rf"^- Reason code: "
            rf"`{re.escape(str(payload.get('reason_code')))}`$"
        ),
    }
    for label, pattern in markers.items():
        if re.search(pattern, header, flags=re.MULTILINE) is None:
            raise ReportError(f"REPORT_MARKDOWN_{label.upper()}_MISMATCH")
    comment = f"<!-- forge-preflight-report-id: {report_id} -->"
    if comment not in raw_markdown:
        raise ReportError("REPORT_MARKDOWN_ID_MISMATCH")
    if sanitize_text(raw_markdown) != raw_markdown:
        raise ReportError("REPORT_MARKDOWN_CONTAINS_PRIVATE_OR_SECRET_VALUE")
    expected_markdown = ReportRenderer.render_markdown(payload)
    if raw_markdown != expected_markdown:
        raise ReportError("REPORT_MARKDOWN_CANONICAL_VIEW_MISMATCH")

    return {
        "schema_version": "forge-preflight-verification/v2",
        "mode": "VERIFY",
        "verification_status": "PASS",
        "contract_decision": contract_decision,
        "effective_decision": contract_decision,
        "verification_reason": "REPORTS_SEMANTICALLY_AGREE",
        "source_report_id": report_id,
        "source_mode": payload.get("mode"),
        "contract_reason_code": payload.get("reason_code"),
        "filesystem_policy": filesystem_policy.to_dict(),
        "network_access": False,
        "command_processor_discovery": False,
        "package_mutation": False,
        "application_launch": False,
        "model_loading": False,
        "generation": False,
    }
