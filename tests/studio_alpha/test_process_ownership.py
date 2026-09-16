"""Cleanup must only ever touch processes the run started.

Pins the two live-leg process failures: an owner Studio was killed because
cleanup matched every `launch_studio` process, and a rehearsal process survived
a broad kill and was present during a live attempt.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha.process_ownership import (  # noqa: E402
    ProcessOwnershipError,
    ProcessRegistry,
)

# A process that simply waits, using this interpreter. Nothing is imported.
SLEEPER = [sys.executable, "-I", "-S", "-B", "-c", "import time; time.sleep(120)"]


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ProcessRegistry(run_id="test-run")
        self.addCleanup(self.registry.terminate_all)

    def test_a_spawned_process_is_owned(self) -> None:
        owned = self.registry.spawn(SLEEPER, label="rehearsal")
        self.assertTrue(self.registry.owns(owned.pid))
        self.assertEqual(owned.run_id, "test-run")

    def test_terminating_an_unowned_pid_is_refused(self) -> None:
        # THE defect: another run's process must be untouchable, even though
        # it looks exactly like ours.
        stranger = subprocess.Popen(SLEEPER)
        self.addCleanup(stranger.kill)
        try:
            with self.assertRaises(ProcessOwnershipError):
                self.registry.terminate(stranger.pid)
            self.assertIsNone(stranger.poll(), "an unowned process was killed")
        finally:
            stranger.kill()

    def test_terminate_all_leaves_a_stranger_alone(self) -> None:
        stranger = subprocess.Popen(SLEEPER)
        self.addCleanup(stranger.kill)
        mine = self.registry.spawn(SLEEPER, label="mine")
        try:
            results = self.registry.terminate_all()
            self.assertTrue(results[mine.pid])
            self.assertNotIn(stranger.pid, results)
            self.assertIsNone(stranger.poll(), "terminate_all killed a stranger")
        finally:
            stranger.kill()

    def test_termination_is_verified_not_assumed(self) -> None:
        owned = self.registry.spawn(SLEEPER)
        self.assertTrue(self.registry.terminate(owned.pid))
        self.assertEqual(self.registry.live_owned(), ())

    def test_a_survivor_is_detected_rather_than_hoped_away(self) -> None:
        # The second failure: a rehearsal process outliving cleanup must be a
        # loud gate failure, not something a later run discovers.
        self.registry.spawn(SLEEPER, label="rehearsal")
        with self.assertRaises(ProcessOwnershipError):
            self.registry.assert_none_running()
        self.registry.terminate_all()
        self.registry.assert_none_running()

    def test_the_registry_cannot_express_a_broad_kill(self) -> None:
        # A guard on the shape of the API itself: there is no name-, pattern-
        # or port-based selector to misuse.
        surface = {name for name in dir(ProcessRegistry) if not name.startswith("_")}
        for forbidden in ("kill_by_name", "kill_matching", "kill_all_named",
                          "terminate_by_command", "kill_by_port"):
            self.assertNotIn(forbidden, surface)
        # Inspect CODE, not prose: the module's docstring names `pkill`
        # precisely because it explains the incident that motivated it.
        import ast

        tree = ast.parse(
            (TEST_ROOT / "studio_alpha" / "process_ownership.py").read_text(
                encoding="utf-8"
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body[0].value.value = ""
        code = ast.unparse(tree)
        for forbidden in ("pkill", "taskkill", "killall", "Get-Process"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
