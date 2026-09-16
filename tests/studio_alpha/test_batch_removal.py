"""Batch Count and Batch Size are gone. AR5.1.

THE DEFECT THIS CLOSES

Two visible controls that reached nothing:

    index.html:672  <label>Batch</label>      id="paramBatch"      1..16
    index.html:673  <label>Batch Size</label> id="paramBatchSize"  1..8
    app.js:3076-77  batch_count / batch_size -- BELOW the lifecycle `return`

Neither was in `_GENERATION_FIELDS`; neither was in `GenerationRequest`. An
owner setting Batch 4 got one image and was told nothing.

WHY REMOVED RATHER THAN WIRED UP

The first of these dead controls where the answer is not "wire it up", and for
an architectural reason rather than effort:

  * `batch_count` is what the QUEUE already does, and does better -- per-job
    cancel, reorder and visible progress, where a batch is one opaque row with
    no way to stop the third image.
  * `batch_size` would need a multi-result contract Studio does not have.
    `FirstImageRequest` is singular by name and by construction, and the port
    takes `images[0]` at four separate sites.

Owner decision, 2026-08-20. Same shape as the Inpaint Sketch ruling: a
capability the product covers better another way, removed rather than
reimplemented.

WHY REMOVED RATHER THAN HIDDEN

D8 says an ABSENT service is hidden, not labelled -- and `9533093d` hid the
inpaint controls on exactly that basis, correctly, because their backend was
coming. Here it is not: a hidden control is a promise deferred, and these are
cancelled. The Wildcards cleanup set the precedent by REMOVING a Roll button
whose listener had been taken away.

WHAT THESE TESTS REFUSE TO ACCEPT

**A removal that took the seed with it.** The section was "Seed & Batch" and
both halves lived together; the dice, the recycle button and the variation
seed are real, working controls and must survive. `SeedSurvivedTests` is the
guard, and it is the one worth having.

Review: `Evidence/source-review/AR5.1-batch-removal.md`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_BATCH_TESTS = 14

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
SHELL = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
WORKFLOW_JS = (FRONTEND / "workflow-state.js").read_text(encoding="utf-8")
CODEX_JS = (FRONTEND / "codex.js").read_text(encoding="utf-8")

BANNED = ("paramBatch", "paramBatchSize", "batch_count", "batch_size")


def _section() -> str:
    start = SHELL.index('data-block="seedbatch"')
    return SHELL[start:SHELL.index("collapse-section", start + 10)]


class TheControlsAreGoneTests(unittest.TestCase):
    def test_no_shipping_script_still_names_them(self) -> None:
        for name, text in (("index.html", SHELL), ("app.js", APP_JS),
                           ("workflow-state.js", WORKFLOW_JS),
                           ("codex.js", CODEX_JS)):
            for banned in BANNED:
                with self.subTest(file=name, id=banned):
                    self.assertNotIn(banned, text)

    def test_the_section_is_now_just_seed(self) -> None:
        self.assertIn('<span class="collapse-title">Seed</span>', SHELL)
        self.assertNotIn("Seed &amp; Batch", SHELL)

    def test_the_panel_label_matches_the_section(self) -> None:
        """A panel list that still said "Seed & Batch" would advertise a
        control the page no longer has."""

        self.assertNotIn('label: "Seed & Batch"', APP_JS)


class SeedSurvivedTests(unittest.TestCase):
    """The guard worth having: the two halves lived in one section."""

    def test_the_seed_controls_are_all_still_there(self) -> None:
        section = _section()
        for control in ("paramSeed", "seedRandom", "seedRecycle"):
            with self.subTest(control=control):
                self.assertIn(control, section)

    def test_variation_seed_survived(self) -> None:
        section = _section()
        for control in ("paramVarSeed", "paramVarStrength"):
            with self.subTest(control=control):
                self.assertIn(control, section)

    def test_the_badge_still_reports_the_seed(self) -> None:
        """It used to report seed AND a batch multiplier. Removing the
        multiplier must not have removed the seed."""

        at = APP_JS.index("seedbatch() {")
        body = APP_JS[at:at + 700]
        self.assertIn("paramSeed", body)
        self.assertIn('"random"', body)

    def test_the_panel_id_was_not_renamed(self) -> None:
        """`seedbatch` is the STORED id of the panel's collapse state.
        Renaming it to `seed` would silently orphan every saved layout."""

        self.assertIn('id: "seedbatch"', APP_JS)
        self.assertIn('data-block="seedbatch"', SHELL)


class ItCannotComeBackThroughSavedStateTests(unittest.TestCase):
    """Saved documents outlive the code that wrote them."""

    def test_it_is_absent_from_the_defaults_parameters(self) -> None:
        self.assertNotIn('["paramBatch"', APP_JS)

    def test_it_is_absent_from_the_workflow_field_map(self) -> None:
        self.assertNotIn("batch_count", WORKFLOW_JS)
        self.assertNotIn("batch_size", WORKFLOW_JS)

    def test_no_locale_still_documents_it(self) -> None:
        for locale in sorted((FRONTEND / "locales").glob("*.json")):
            with self.subTest(locale=locale.name):
                data = json.loads(locale.read_text(encoding="utf-8"))
                self.assertNotIn("codex.entry.param_seed_batch.title", data)
                self.assertNotIn("codex.entry.param_seed_batch.content", data)

    def test_every_locale_is_still_valid_json(self) -> None:
        for locale in sorted((FRONTEND / "locales").glob("*.json")):
            with self.subTest(locale=locale.name):
                self.assertIsInstance(
                    json.loads(locale.read_text(encoding="utf-8")), dict)


class TheCapabilityIsRedirectedTests(unittest.TestCase):
    """Removed is not the same as ignored: a tester still wants four images."""

    def test_the_codex_points_at_the_queue(self) -> None:
        at = CODEX_JS.index('id: "param_seed_batch"')
        entry = CODEX_JS[at:at + 1400]
        self.assertIn("queue", entry.lower())

    def test_the_tester_guide_says_how_to_make_several(self) -> None:
        """Asserted on the KNOWN-ISSUES ROW, not on the word "queue" -- the
        guide's opening paragraph already says "queue", so a bare substring
        check here would have passed without the row ever being written."""

        guide = (APP_ROOT / "docs" / "studio"
                 / "FRIENDS_ALPHA_TESTER_GUIDE.md").read_text(encoding="utf-8")
        rows = [line for line in guide.splitlines()
                if line.startswith("| No Batch Count or Batch Size")]
        self.assertEqual(1, len(rows), "the known-issues row is missing")
        self.assertIn("queue", rows[0].lower())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_BATCH_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
