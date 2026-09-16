from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import unittest

from scripts.preflight.boundary import WorkspaceBoundary, WorkspaceFS
from scripts.preflight.contracts import (
    ContractError,
    FilesystemPolicyContract,
    PreflightContract,
    load_contract,
)


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]
CONTRACT_PATH = (
    WORKSPACE
    / "app"
    / "scripts"
    / "preflight"
    / "fixtures"
    / "static-contract.json"
)


class ControlledContractFS(WorkspaceFS):
    def __init__(self, contract, *, policy_bytes=None):
        super().__init__(WorkspaceBoundary(WORKSPACE))
        self.contract_text = json.dumps(contract, sort_keys=True)
        self.policy_bytes = policy_bytes

    def read_text(self, path, encoding="utf-8", *, max_chars=None):
        if Path(path) == CONTRACT_PATH:
            if (
                max_chars is not None
                and len(self.contract_text) > max_chars
            ):
                raise ValueError("TEXT_INPUT_EXCEEDS_SIZE_LIMIT")
            return self.contract_text
        return super().read_text(path, encoding, max_chars=max_chars)

    def read_bytes(self, path, *, max_bytes=None):
        normalized = str(path).replace("\\", "/")
        if (
            self.policy_bytes is not None
            and normalized.endswith("/app/AGENTS.md")
        ):
            return self.policy_bytes
        return super().read_bytes(path, max_bytes=max_bytes)


class FilesystemPolicyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fs = WorkspaceFS(WorkspaceBoundary(WORKSPACE))
        self.contract = self.fs.read_json(CONTRACT_PATH)

    def test_checked_in_contract_matches_current_policy(self) -> None:
        loaded = load_contract(self.fs, os.fspath(CONTRACT_PATH))
        self.assertEqual(
            FilesystemPolicyContract.current(self.fs).to_dict(),
            loaded.filesystem_policy.to_dict(),
        )
        denied_probes = [
            event
            for event in self.fs.boundary.events
            if event.operation == "filesystem-policy-denial-probe"
        ]
        self.assertEqual(4, len(denied_probes))
        self.assertEqual(
            {
                "PRIVATE_LOCAL_OWNER_AUTHORIZATION_REQUIRED",
                "FILESYSTEM_PATH_OUTSIDE_WORKSPACE_BOUNDARY",
            },
            {event.reason_code for event in denied_probes},
        )

    def test_policy_schema_is_exact_and_strictly_typed(self) -> None:
        mutations = []
        wrong_version = deepcopy(self.contract)
        wrong_version["filesystem_policy"]["filesystem_policy_version"] = (
            "forge-filesystem-policy/v0"
        )
        mutations.append(wrong_version)
        extra = deepcopy(self.contract)
        extra["filesystem_policy"]["unexpected"] = True
        mutations.append(extra)
        wrong_roots = deepcopy(self.contract)
        wrong_roots["filesystem_policy"]["normalized_permitted_roots"] = [
            "app/",
            "Reference/",
            "Evidence/",
        ]
        mutations.append(wrong_roots)
        duplicate_root = deepcopy(self.contract)
        duplicate_root["filesystem_policy"]["normalized_permitted_roots"] = [
            "app/",
            "Evidence/",
            "Evidence/",
        ]
        mutations.append(duplicate_root)
        private_enabled = deepcopy(self.contract)
        private_enabled["filesystem_policy"]["private_local_status"] = (
            "OWNER_AUTHORIZED"
        )
        mutations.append(private_enabled)
        outside_enabled = deepcopy(self.contract)
        outside_enabled["filesystem_policy"]["outside_access_allowed"] = True
        mutations.append(outside_enabled)
        outside_integer = deepcopy(self.contract)
        outside_integer["filesystem_policy"]["outside_access_allowed"] = 0
        mutations.append(outside_integer)
        wrong_source = deepcopy(self.contract)
        wrong_source["filesystem_policy"]["policy_source"] = "AGENTS.md"
        mutations.append(wrong_source)
        wrong_hash = deepcopy(self.contract)
        wrong_hash["filesystem_policy"]["policy_source_sha256"] = (
            "sha256:" + ("A" * 64)
        )
        mutations.append(wrong_hash)
        network_integer = deepcopy(self.contract)
        network_integer["network"]["authorized"] = 0
        mutations.append(network_integer)

        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    PreflightContract.from_dict(value)

    def test_policy_hash_detects_exact_byte_and_newline_drift(self) -> None:
        current = self.fs.read_bytes(WORKSPACE / "app" / "AGENTS.md")
        for changed in (current + b"\n", current.replace(b"\r\n", b"\n")):
            if changed == current:
                changed = current + b" "
            with self.subTest(length=len(changed)):
                with self.assertRaises(ContractError) as raised:
                    load_contract(
                        ControlledContractFS(
                            self.contract,
                            policy_bytes=changed,
                        ),
                        os.fspath(CONTRACT_PATH),
                    )
                self.assertEqual(
                    "FILESYSTEM_POLICY_SOURCE_HASH_MISMATCH",
                    str(raised.exception),
                )

    def test_boundary_root_and_private_local_drift_fail_before_policy_read(
        self,
    ) -> None:
        cases = ("roots", "private")
        for case in cases:
            with self.subTest(case=case):
                boundary = WorkspaceBoundary(WORKSPACE)
                if case == "roots":
                    boundary.allowed_roots["Evidence"] = (
                        WORKSPACE / "Reference"
                    )
                else:
                    boundary.private_local_root = WORKSPACE / "Evidence"
                controlled = WorkspaceFS(boundary)
                with self.assertRaises(ContractError):
                    FilesystemPolicyContract.current(controlled)


if __name__ == "__main__":
    unittest.main()
