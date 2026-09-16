"""The baseline artefact's two promises: it redacts, and it can say no.

`scripts/capture_baseline.py` writes the WP0.1 evidence envelope. Section 17 of
the execution handoff requires private values redacted, and Studio's standing
rule is that no ordinary output carries an owner path -- so `redact()` is a
contract, not a convenience.

The verdict matters just as much. An evidence directory that only ever says
"here is the environment" reads as an endorsement of whatever sits beside it.
This one has to be able to refuse: a dirty tree means "this HEAD" does not
describe what ran, and a modified inference directory means the patch
inventory's central claim is false. Both must produce NOT BINDABLE.

These build envelopes by hand rather than running git, so the suite stays fast
and tests the logic rather than this machine.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts"))

import capture_baseline  # noqa: E402

EXPECTED_BASELINE_CAPTURE_TESTS = 7


def envelope(*, tree: str = "clean", untouched: list | None = None) -> dict:
    return {
        "captured_utc": "2026-08-18T00:00:00+00:00",
        "source": {"branch": "b", "head": "a" * 40, "tree": tree},
        "neo": {
            "tracking_vs_upstream": "0\t0",
            "tracking_vs_product_head": "0\t324",
            "tracking_proves": "tracking",
            "product_proves": "product",
            "neo_owned_diffstat": "21 files changed",
            "untouched_directories_changed": untouched or [],
        },
        "non_claims": capture_baseline.NON_CLAIMS,
    }


class RedactionTests(unittest.TestCase):
    def test_the_home_directory_is_removed(self) -> None:
        home = str(Path.home())
        self.assertNotIn(home, capture_baseline.redact(f"error at {home}/x"))

    def test_both_separator_spellings_are_removed(self) -> None:
        """A Windows path reaches the recorder spelled either way, depending on
        whether git or Python produced it."""

        home = str(Path.home())
        for spelling in (home, home.replace("\\", "/")):
            with self.subTest(spelling=spelling):
                self.assertNotIn(
                    spelling, capture_baseline.redact(f"path={spelling}\\f.txt"))

    def test_unrelated_text_survives(self) -> None:
        self.assertEqual("nothing private here",
                         capture_baseline.redact("nothing private here"))


class VerdictTests(unittest.TestCase):
    def test_a_clean_untouched_tree_is_bindable(self) -> None:
        self.assertIn("**BINDABLE.**",
                      capture_baseline.verdict(envelope()))

    def test_a_dirty_tree_refuses(self) -> None:
        """The failure this exists for: binding a claim to a HEAD that is not
        what actually ran."""

        text = capture_baseline.verdict(envelope(tree="dirty (3 paths)"))
        self.assertIn("**NOT BINDABLE AS-IS.**", text)
        self.assertIn("does not describe what ran", text)

    def test_a_touched_inference_directory_refuses(self) -> None:
        """backend/ changing falsifies the patch inventory's central claim, so
        a clean tree alone must not be enough to bind."""

        text = capture_baseline.verdict(
            envelope(untouched=["backend/loader.py"]))
        self.assertIn("**NOT BINDABLE AS-IS.**", text)
        self.assertNotIn("**BINDABLE.**", text)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_BASELINE_CAPTURE_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
