"""The harness must launch Studio like the owner, and prove it before a live leg.

Pins the defect that invalidated a two-leg live diagnostic: the verifier
launched Studio with `-I -S -B`, the canonical test runner's flags, which
remove site-packages. `torch` was then unimportable and every load failed at
`startup_globals_installed` before touching a payload.

These tests need no Torch and open no payload.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha.live_harness import (  # noqa: E402
    FORBIDDEN_LAUNCH_FLAGS,
    HarnessPreflightError,
    assert_owner_equivalent,
    owner_launch_command,
)

WORKSPACE_ROOT = APP_ROOT.parent
OWNER_LAUNCHER = WORKSPACE_ROOT / "start_studio.py"


class OwnerEquivalenceTests(unittest.TestCase):
    def test_the_command_matches_the_owner_launcher(self) -> None:
        # Derived from start_studio.py, which builds
        # [PYTHON, LAUNCHER, "--config", CONFIG]. If the owner launcher changes
        # shape, this is where it shows up.
        source = OWNER_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('command = [str(PYTHON), str(LAUNCHER), "--config", str(CONFIG)]',
                      source)
        command = owner_launch_command(Path("cfg.json"))
        self.assertEqual(len(command), 4)
        self.assertTrue(command[1].endswith("launch_studio.py"))
        self.assertEqual(command[2], "--config")

    def test_the_owner_launcher_uses_no_isolating_flags(self) -> None:
        source = OWNER_LAUNCHER.read_text(encoding="utf-8")
        for flag in FORBIDDEN_LAUNCH_FLAGS:
            self.assertNotIn(
                f'"{flag}"', source,
                f"the owner launcher gained {flag}; the harness assumption is stale",
            )

    def test_a_command_with_test_runner_flags_is_refused(self) -> None:
        # THE defect, as a guard.
        bad = [
            "python.exe", "-I", "-S", "-B",
            str(APP_ROOT / "launch_studio.py"), "--config", "cfg.json",
        ]
        with self.assertRaises(HarnessPreflightError) as caught:
            assert_owner_equivalent(bad)
        self.assertIn("-S", str(caught.exception))

    def test_each_forbidden_flag_is_caught_individually(self) -> None:
        for flag in FORBIDDEN_LAUNCH_FLAGS:
            command = [
                "python.exe", flag,
                str(APP_ROOT / "launch_studio.py"), "--config", "cfg.json",
            ]
            with self.assertRaises(HarnessPreflightError, msg=flag):
                assert_owner_equivalent(command)

    def test_the_generated_command_passes_its_own_check(self) -> None:
        assert_owner_equivalent(owner_launch_command(Path("cfg.json")))

    def test_a_command_that_does_not_start_studio_is_refused(self) -> None:
        with self.assertRaises(HarnessPreflightError):
            assert_owner_equivalent(["python.exe", "-c", "print(1)"])


class PreflightGateTests(unittest.TestCase):
    def test_the_gate_refuses_when_torch_is_unimportable(self) -> None:
        # Simulate the exact observed condition without needing a Torch-free
        # interpreter: the probe reports failure, the gate must refuse.
        from tests.studio_alpha import live_harness

        original = live_harness.probe_target_python
        live_harness.probe_target_python = lambda: live_harness.TargetProbe(
            torch_importable=False, torch_version="", cuda_available=False,
            cuda_device="", detail="ModuleNotFoundError",
        )
        try:
            with self.assertRaises(HarnessPreflightError) as caught:
                live_harness.require_live_preflight()
        finally:
            live_harness.probe_target_python = original
        message = str(caught.exception)
        self.assertIn("TARGET_PYTHON_IMPORT_TORCH = FAIL", message)
        self.assertIn("refusing to consume a live load", message)

    def test_the_gate_passes_and_reports_when_torch_is_importable(self) -> None:
        from tests.studio_alpha import live_harness

        original = live_harness.probe_target_python
        live_harness.probe_target_python = lambda: live_harness.TargetProbe(
            torch_importable=True, torch_version="2.11.0+cu130",
            cuda_available=True, cuda_device="NVIDIA GeForce RTX 5060 Ti",
        )
        try:
            probe = live_harness.require_live_preflight()
        finally:
            live_harness.probe_target_python = original
        self.assertTrue(probe.torch_importable)
        self.assertEqual(probe.to_dict()["torch_version"], "2.11.0+cu130")

    def test_launching_runs_the_gate_before_spawning(self) -> None:
        # A refused preflight must consume nothing: no process, no live load.
        from tests.studio_alpha import live_harness
        from tests.studio_alpha.process_ownership import ProcessRegistry

        registry = ProcessRegistry(run_id="gate-test")
        original = live_harness.probe_target_python
        live_harness.probe_target_python = lambda: live_harness.TargetProbe(
            torch_importable=False, torch_version="", cuda_available=False,
            cuda_device="", detail="ModuleNotFoundError",
        )
        try:
            with self.assertRaises(HarnessPreflightError):
                live_harness.launch_studio(
                    Path("cfg.json"), registry=registry,
                    log_path=Path("unused.log"),
                )
        finally:
            live_harness.probe_target_python = original
        self.assertEqual(registry.owned, (), "a process was spawned despite the gate")


if __name__ == "__main__":
    unittest.main()
