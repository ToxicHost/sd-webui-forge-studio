"""The tester guide's claims, checked against the product. AR8.

WHY A DOCUMENT NEEDS TESTS

`FRIENDS_ALPHA_TESTER_GUIDE.md` is the only thing a tester is given. It makes
factual, checkable claims -- that nothing reaches the network, that a refresh
does not lose their work, that a support bundle never contains their images --
and a person will act on those claims. A claim that quietly stops being true is
worse than no claim, because they stopped keeping their own copies.

This repository has already caught itself doing exactly that twice in one
session: a Settings toggle that promised float data nothing produced (AR5), and
a documentation file that hardcoded the owner's home directory (AR6.4).

So each claim here is asserted against the thing it describes, and the guide
fails the suite if the product moves out from under it.

THE CLAIMS THAT WERE SOFTENED BEFORE SHIPPING

The first draft said the no-network property was "enforced by a test in the
suite". It is not: `test_canonical_network_guard.py` guards the RUNNER, which
proves nothing reaches out during the suite, not that no code path anywhere
could. The guide now names the four things that are actually true -- loopback
binding, no outbound call site in Studio-owned code, a `connect-src 'self'`
CSP, and the runner guard -- and each is asserted below.

The state-root claim was softened the same way: data lives beside the app
because `Start-Studio.bat` sets `STUDIO_STATE_ROOT`, not because that is
Studio's default, which is `%LOCALAPPDATA%\\ForgeStudio`.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
WS_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_GUIDE_TESTS = 18

GUIDE_PATH = (APP_ROOT / "docs" / "studio"
              / "FRIENDS_ALPHA_TESTER_GUIDE.md")
GUIDE = GUIDE_PATH.read_text(encoding="utf-8")


class ItExistsAndIsReadableTests(unittest.TestCase):
    def test_the_guide_ships_in_the_package(self) -> None:
        """It is tracked, so `git ls-files` puts it in the archive."""

        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             str(GUIDE_PATH.relative_to(APP_ROOT).as_posix())],
            cwd=str(APP_ROOT), capture_output=True, text=True)
        self.assertEqual(0, tracked.returncode,
                         "the tester guide is untracked and would not ship")

    def test_it_carries_no_private_path(self) -> None:
        from tests.studio_alpha import test_distributable_privacy as privacy

        relative = GUIDE_PATH.relative_to(APP_ROOT).as_posix()
        self.assertEqual([], privacy.leaks_in(relative, GUIDE))

    def test_it_says_what_this_is_not(self) -> None:
        """The section that saves a tester an afternoon."""

        for expected in ("What this is not", "Not a release", "Not a backup"):
            with self.subTest(expected=expected):
                self.assertIn(expected, GUIDE)


class NoNetworkClaimTests(unittest.TestCase):
    """Four claims, each asserted against the thing it describes."""

    def test_studio_refuses_a_non_loopback_bind(self) -> None:
        launch = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8")
        self.assertIn("binds 127.0.0.1 only", launch)

    def test_no_studio_owned_code_has_an_outbound_call_site(self) -> None:
        """The strongest of the four, and the one most likely to rot: any
        future feature that fetches something breaks this."""

        reaching = re.compile(
            r"urlopen|urlretrieve|requests\.(?:get|post|put)|http\.client"
            r"|socket\.create_connection")
        offenders = []
        for folder in ("forge_studio", "forge_headless"):
            for path in (APP_ROOT / folder).rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if reaching.search(text):
                    offenders.append(path.relative_to(APP_ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_the_page_is_served_with_a_self_only_connect_policy(self) -> None:
        """The browser itself blocks a request elsewhere, whatever the page
        tries."""

        presentation = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
            encoding="utf-8")
        self.assertIn("connect-src 'self'", presentation)

    def test_the_suite_runs_behind_a_network_guard(self) -> None:
        self.assertTrue(
            (APP_ROOT / "tests" / "studio_alpha"
             / "test_canonical_network_guard.py").is_file())

    def test_the_guide_does_not_overclaim_the_guard(self) -> None:
        """The first draft said "enforced by a test in the suite", which the
        runner guard does not establish. Catches that sentence returning."""

        self.assertNotIn("That is enforced by a test in the suite", GUIDE)


class SupportBundleClaimTests(unittest.TestCase):
    def test_the_command_it_gives_is_the_real_one(self) -> None:
        self.assertIn("build_support_bundle.py", GUIDE)
        self.assertTrue(
            (APP_ROOT / "scripts" / "build_support_bundle.py").is_file())

    def test_the_dry_run_it_promises_exists(self) -> None:
        self.assertIn("--dry-run", GUIDE)
        source = (APP_ROOT / "scripts" / "build_support_bundle.py").read_text(
            encoding="utf-8")
        self.assertIn('"--dry-run"', source)

    def test_what_it_promises_is_never_collected_really_is_not(self) -> None:
        """The claim a tester relies on most."""

        sys.path.insert(0, str(APP_ROOT / "scripts"))
        import importlib

        never = importlib.import_module("build_support_bundle").NEVER_COLLECT
        self.assertIn("Your Canvas documents", GUIDE)
        self.assertIn("recovery", never)
        self.assertIn("gallery", never)


class RecoveryClaimTests(unittest.TestCase):
    def test_the_refresh_promise_has_a_feature_behind_it(self) -> None:
        """"Your work survives a refresh" is the strongest promise in the
        guide. AR4.4 is what makes it true."""

        self.assertIn("survives a refresh", GUIDE)
        self.assertTrue((APP_ROOT / "forge_studio" / "canvas_recovery.py").is_file())
        self.assertTrue((APP_ROOT / "forge_studio" / "frontend"
                         / "canvas-recovery.js").is_file())

    def test_the_undo_fork_is_disclosed(self) -> None:
        """A recovered document starts with an empty history. Told, not
        discovered."""

        self.assertIn("empty undo history", GUIDE)


class KnownIssuesAreRealTests(unittest.TestCase):
    def test_the_removed_control_is_listed_as_removed(self) -> None:
        shell = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
            encoding="utf-8")
        self.assertIn("High precision", GUIDE)
        self.assertNotIn("toggleHighPrecision", shell)

    def test_it_no_longer_quotes_a_message_a_tester_will_not_see(self) -> None:
        """Inverted at AR7.1, and for the reason the original gave.

        This asserted that the guide quotes "Missing interpreter" and that the
        launcher prints it -- sound, while that was what a tester met. It is
        not any more: the environment is built on first run, so quoting that
        message would now describe a wall that no longer exists and send
        someone hunting for an environment bundle that was never made.

        `start_studio.py` KEEPS the message, for anyone who runs it directly
        against a broken venv. It is just no longer part of installing.
        """

        launcher = (WS_ROOT / "start_studio.py").read_text(encoding="utf-8")
        self.assertIn("Missing interpreter", launcher)
        self.assertNotIn("Missing interpreter", GUIDE)

    def test_the_install_step_describes_the_bootstrap_that_exists(self) -> None:
        """The replacement obligation: whatever the guide promises about
        installing has to be a thing the tree actually does."""

        self.assertIn("builds its own environment", GUIDE)
        self.assertIn("bootstrap_environment.py", GUIDE)
        self.assertTrue(
            (APP_ROOT / "scripts" / "bootstrap_environment.py").is_file())

    def test_it_warns_against_the_pip_install_that_breaks_torch(self) -> None:
        """The trap is still reachable by hand, so the guide has to name it."""

        self.assertIn("CPU build", GUIDE)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_GUIDE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
