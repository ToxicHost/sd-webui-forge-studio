"""Result paths follow the install, not the shell. AR6.1.

WHY THIS EXISTS, AND THE CLAIM IT RETIRES

A live harness failed once with `GENERATION_FAILED reason=OSError` when Studio
was launched with the working directory set to `app/` instead of the workspace
root. I diagnosed that as a relative `result_root` resolving against the
process CWD, relaunched from the workspace root, saw it work, and recorded
"result-root CWD sensitivity" as an alpha blocker.

THAT DIAGNOSIS WAS WRONG. `forge_studio/launch.py:104-105` already anchors a
relative `result_root` to `WORKSPACE_ROOT`, and `WORKSPACE_ROOT` is derived
from `__file__` (:41-42), not from the CWD. Measured from three different
working directories -- the workspace root, `app/`, and an unrelated temporary
directory -- it resolves to the same path every time.

So the requirement AR6.1 states ("result paths are anchored to configured or
project state, not process CWD") was already satisfied, and the blocker was an
artefact of a plausible guess I never verified. The tests below pin the real
behaviour so it stays true, and the original OSError is recorded as UNDIAGNOSED
rather than quietly treated as fixed -- it was never reproduced, and relaunching
from a different directory is not evidence about its cause.

WHAT A FRIENDS BUILD NEEDS FROM THIS

A tester will launch from a desktop shortcut, from Explorer, or by
double-clicking a .bat in the extracted folder. Each gives a different working
directory. None of them may change where results land or whether they land at
all.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_RESULT_ROOT_TESTS = 7

LAUNCH_SOURCE = (APP_ROOT / "forge_studio" / "launch.py").read_text(
    encoding="utf-8")


class AnchoringTests(unittest.TestCase):
    """The anchor is the install, and the test proves it by moving the shell."""

    def _workspace_root(self) -> Path:
        from forge_studio.launch import WORKSPACE_ROOT

        return WORKSPACE_ROOT

    def test_the_anchor_does_not_move_with_the_working_directory(self) -> None:
        """Catches: anchoring to `Path.cwd()`, which would make results land
        wherever the shortcut happened to point."""

        original = os.getcwd()
        seen = set()
        try:
            for where in (APP_ROOT, APP_ROOT.parent, tempfile.gettempdir()):
                os.chdir(where)
                seen.add(str(self._workspace_root()))
        finally:
            os.chdir(original)
        self.assertEqual(1, len(seen), f"anchor moved with the shell: {seen}")

    def test_the_anchor_is_derived_from_the_module_file(self) -> None:
        self.assertIn("APP_ROOT = Path(__file__).resolve().parents[1]",
                      LAUNCH_SOURCE)
        self.assertIn("WORKSPACE_ROOT = APP_ROOT.parent", LAUNCH_SOURCE)

    def test_no_cwd_lookup_decides_where_results_go(self) -> None:
        """Catches the defect directly, by name."""

        for banned in ("Path.cwd()", "os.getcwd()", 'Path(".")'):
            with self.subTest(token=banned):
                self.assertNotIn(banned, LAUNCH_SOURCE)


class ContainmentTests(unittest.TestCase):
    """A result root outside the workspace is refused, not silently accepted."""

    def test_a_relative_root_is_anchored_rather_than_left_relative(self) -> None:
        self.assertIn("if not result_root.is_absolute():", LAUNCH_SOURCE)
        self.assertIn("result_root = WORKSPACE_ROOT / result_root",
                      LAUNCH_SOURCE)

    def test_an_escaping_root_is_refused_by_name(self) -> None:
        self.assertIn("result_root.relative_to(WORKSPACE_ROOT)", LAUNCH_SOURCE)
        self.assertIn("must live inside the Studio workspace", LAUNCH_SOURCE)

    def test_a_missing_root_is_required_rather_than_defaulted(self) -> None:
        """Catches: inventing a default location for the owner's work."""

        self.assertIn("result_root is required", LAUNCH_SOURCE)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_RESULT_ROOT_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
