"""Command-line interface for the workspace-bound preflight tool."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from .orchestrator import PreflightOrchestrator
from .reports import sanitize_text
from .resolve_authorization import ResolveAuthorizationValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge-preflight")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    static = subparsers.add_parser("static")
    static.add_argument("--contract")
    static.add_argument("--report-stem", default="static-preflight")

    contract = subparsers.add_parser("contract")
    contract.add_argument("--contract")
    contract.add_argument("--report-stem", default="contract-preflight")
    contract.add_argument("--write-reports", action="store_true")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--json", required=True, dest="json_path")
    verify.add_argument("--markdown", required=True, dest="markdown_path")

    resolve_plan = subparsers.add_parser("resolve-plan")
    resolve_plan.add_argument("--repository-root", default=".")
    resolve_plan.add_argument(
        "--evidence-root",
        default="..\\Evidence\\preflight",
    )
    resolve_plan.add_argument(
        "--explicit-version",
        action="append",
        default=[],
    )
    resolve_plan.add_argument("--version-specifier")
    resolve_plan.add_argument(
        "--maximum-candidate-count",
        type=int,
    )
    resolve_plan.add_argument(
        "--index-url",
        action="append",
        default=[],
    )
    resolve_plan.add_argument(
        "--host",
        action="append",
        default=[],
    )
    resolve_plan.add_argument(
        "--operation",
        action="append",
        choices=("CANDIDATE_METADATA", "PIP_DRY_RUN"),
        default=[],
    )
    resolve_plan.add_argument("--contract")

    validate_authorization = subparsers.add_parser(
        "validate-authorization"
    )
    validate_authorization.add_argument("--plan", required=True)
    validate_authorization.add_argument("--authorization")
    validate_authorization.add_argument("--contract")

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--authorization", required=True)
    resolve.add_argument("--contract")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    orchestrator = PreflightOrchestrator()
    try:
        if args.mode == "static":
            result = orchestrator.run_static(
                contract_path=args.contract,
                report_stem=args.report_stem,
            )
        elif args.mode == "contract":
            result = orchestrator.run_contract(
                contract_path=args.contract,
                write_reports=args.write_reports,
                report_stem=args.report_stem,
            )
        elif args.mode == "verify":
            result = orchestrator.run_verify(
                json_path=args.json_path,
                markdown_path=args.markdown_path,
            )
        elif args.mode == "resolve-plan":
            candidate_scope = None
            if args.explicit_version:
                if (
                    args.version_specifier is not None
                    or args.maximum_candidate_count is not None
                ):
                    raise ResolveAuthorizationValidationError(
                        "RESOLVE_AUTHORIZATION_SCOPE_MISMATCH"
                    )
                candidate_scope = {
                    "package": "gradio",
                    "explicit_versions": list(
                        args.explicit_version
                    ),
                }
            elif (
                args.version_specifier is not None
                or args.maximum_candidate_count is not None
            ):
                candidate_scope = {
                    "package": "gradio",
                    "version_specifier": args.version_specifier,
                    "maximum_candidate_count": (
                        args.maximum_candidate_count
                    ),
                }
            result = orchestrator.run_resolve_plan(
                repository_root=args.repository_root,
                evidence_root=args.evidence_root,
                contract_path=args.contract,
                candidate_scope=candidate_scope,
                requested_indexes=tuple(args.index_url),
                requested_hosts=tuple(args.host),
                approved_operations=tuple(args.operation),
            )
        elif args.mode == "validate-authorization":
            result = (
                orchestrator.run_resolve_authorization_validation(
                    plan_path=args.plan,
                    authorization_path=args.authorization,
                    contract_path=args.contract,
                )
            )
        elif args.mode == "resolve":
            result = orchestrator.run_resolve(
                authorization_path=args.authorization,
                contract_path=args.contract,
            )
        else:
            parser.error("unsupported mode")
    except ResolveAuthorizationValidationError as exc:
        output = {
            "mode": str(args.mode).replace("-", "_").upper(),
            "decision": "NO_GO",
            "effective_decision": "NO_GO",
            "reason_code": exc.reason_code,
        }
        print(json.dumps(output, allow_nan=False, sort_keys=True))
        return 2
    except Exception as exc:
        if args.mode == "verify":
            output = {
                "mode": "VERIFY",
                "verification_status": "FAIL",
                "contract_decision": None,
                "effective_decision": "NO_GO",
                "verification_reason": "CONTRACT_SCHEMA_INVALID",
            }
        else:
            output = {
                "mode": str(args.mode).replace("-", "_").upper(),
                "decision": "NO_GO",
                "effective_decision": "NO_GO",
                "reason_code": "PREFLIGHT_INTERNAL_ERROR",
                "error_type": sanitize_text(type(exc).__name__),
            }
        print(json.dumps(output, allow_nan=False, sort_keys=True))
        return 2

    if result.mode == "VERIFY":
        output = {
            "mode": "VERIFY",
            "verification_status": result.payload["verification_status"],
            "contract_decision": result.payload["contract_decision"],
            "effective_decision": result.payload["effective_decision"],
            "verification_reason": result.payload["verification_reason"],
            "json_report": result.json_report,
            "markdown_report": result.markdown_report,
        }
    elif result.mode == "RESOLVE_AUTHORIZATION":
        output = {
            "mode": result.mode,
            "validation_status": result.payload[
                "validation_status"
            ],
            "decision": result.decision,
            "effective_decision": result.effective_decision,
            "reason_code": result.reason_code,
            "execution_authorized": False,
            "json_report": result.json_report,
            "markdown_report": result.markdown_report,
        }
    else:
        output = {
            "mode": result.mode,
            "decision": result.decision,
            "effective_decision": result.effective_decision,
            "reason_code": result.reason_code,
            "json_report": result.json_report,
            "markdown_report": result.markdown_report,
        }
    if args.mode == "resolve-plan":
        # The no-network handshake is useful only if the exact digest and
        # review payload reach the human owner. Other modes keep terse output.
        output["payload"] = result.payload
    print(json.dumps(output, allow_nan=False, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
