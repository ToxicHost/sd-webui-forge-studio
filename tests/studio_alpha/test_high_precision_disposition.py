"""High Precision is removed, and Develop's reader is not. AR5.

THE DEFECT THIS CLOSES

Studio shipped a Settings toggle that promised, in four languages:

    Saves high-precision float data alongside each image. Eliminates banding
    and gives the Develop module ~0.5 stops of highlight headroom. Adds
    ~0.5-3s and ~12 MB per image. Best with fp32 VAE.

and nothing behind it. `high_precision` was sent on every generation and:

    grep -rn "high_precision" --include=*.py app/   ->  NOTHING
    grep -rn "float32.bin"    --include=*.py app/   ->  NOTHING

Zero matches, Neo's `modules/` included. An owner who turned it on and accepted
the stated cost got none of it, with no way to tell. That is worse than an
absent feature: it is a specific, testable, false claim.

WHY IT EXISTED

A porting gap, not an invention. The Extension implements the whole feature --
`studio_api.py:1440` writes the `.float32.bin` and `.float32.json` sidecars,
`:3011` gates on `req.high_precision and req.save_outputs`, `studio_gallery.py`
serves them, and `studio_generation.py` carries ~50 `_hp_*` sites that capture
the floats by monkey-patching `modules.processing.decode_latent_batch`. Studio
inherited the FRONTEND of all that and not the backend.

THE DISPOSITION IS PRE-AUTHORIZED

`docs/16_ALPHA_MATRIX.md` already said "disable unless AR5 clears it". AR5 did
not clear it, so the control is removed for Friends Alpha 0.1. Porting the
Extension's capture remains a real post-alpha option.

WHAT THESE TESTS REFUSE TO ACCEPT

**Taking the working half with the dead one.** `develop.js`'s float32 sidecar
reader is complete, is INDEPENDENT of the toggle (it never read
`State.highPrecision` -- verified), and is exactly what a future port needs.
`DevelopReaderIsIntactTests` is the guard against a tidy-up that deletes it.

**A resurrection through saved state.** The key travelled in DEFAULTS_PARAMS
and in the workflow FIELD_MAP, so a saved defaults document or Workflow Profile
could otherwise put a dead control back on screen.

Review: `Evidence/source-review/AR5-high-precision.md`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_HP_TESTS = 13

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
SHELL = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
WORKFLOW_JS = (FRONTEND / "workflow-state.js").read_text(encoding="utf-8")
SETTINGS_JS = (FRONTEND / "settings-page.js").read_text(encoding="utf-8")
DEVELOP_JS = (FRONTEND / "develop.js").read_text(encoding="utf-8")


class TheControlIsGoneTests(unittest.TestCase):
    """A control that cannot do what it says must not be rendered."""

    def test_the_shell_no_longer_carries_the_toggle(self) -> None:
        self.assertNotIn("toggleHighPrecision", SHELL)

    def test_the_shell_no_longer_carries_the_promise(self) -> None:
        """The tooltip was the false claim, not the switch."""

        for phrase in ("highlight headroom", "Eliminates banding",
                       "high-precision float data"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, SHELL)

    def test_no_listener_or_state_remains(self) -> None:
        self.assertNotIn("toggleHighPrecision", APP_JS)
        self.assertNotIn("highPrecision", APP_JS)

    def test_the_request_no_longer_carries_a_field_nothing_reads(self) -> None:
        """How this started: a field sent on every generation, read by
        nothing."""

        self.assertNotIn("high_precision", APP_JS)

    def test_the_settings_page_has_no_orphan_category(self) -> None:
        self.assertNotIn("toggleHighPrecision", SETTINGS_JS)


class ItCannotComeBackThroughSavedStateTests(unittest.TestCase):
    """Saved documents outlive the code that wrote them."""

    def test_it_is_absent_from_the_workflow_field_map(self) -> None:
        self.assertNotIn("high_precision", WORKFLOW_JS)
        self.assertNotIn("toggleHighPrecision", WORKFLOW_JS)

    def test_it_is_absent_from_the_defaults_parameters(self) -> None:
        self.assertNotIn("toggleHighPrecision", APP_JS)

    def test_no_locale_still_carries_the_promise(self) -> None:
        for locale in sorted((FRONTEND / "locales").glob("*.json")):
            with self.subTest(locale=locale.name):
                data = json.loads(locale.read_text(encoding="utf-8"))
                offenders = [k for k in data if "highPrecision" in k]
                self.assertEqual([], offenders)

    def test_every_locale_is_still_valid_json(self) -> None:
        """The removal was line-based to preserve the translators' section
        grouping; that must not have broken the file."""

        for locale in sorted((FRONTEND / "locales").glob("*.json")):
            with self.subTest(locale=locale.name):
                self.assertIsInstance(
                    json.loads(locale.read_text(encoding="utf-8")), dict)


class DevelopReaderIsIntactTests(unittest.TestCase):
    """The working half must survive the removal of the dead one.

    `develop.js` reads a `.float32.bin` sidecar, its `.float32.json` metadata
    and a `.blend_mask.png`, and classifies range/clamp/headroom for its badge.
    It never consulted the toggle, so it was never the broken part -- it is a
    consumer waiting on a producer, and it is what a port would build against.
    """

    def test_the_sidecar_reader_is_still_present(self) -> None:
        for marker in (".float32.bin", ".float32.json", "_updateHpBadge",
                       "_floatSrc"):
            with self.subTest(marker=marker):
                self.assertIn(marker, DEVELOP_JS)

    def test_the_reader_never_depended_on_the_removed_toggle(self) -> None:
        """Why removing the control could not break it."""

        self.assertNotIn("State.highPrecision", DEVELOP_JS)
        self.assertNotIn("toggleHighPrecision", DEVELOP_JS)


class PortIsRecognisableAsPortingTests(unittest.TestCase):
    """A future implementation should start from the Extension, not a blank
    page. The record names the exact sources."""

    def test_the_review_record_names_the_extension_sources(self) -> None:
        record = (APP_ROOT.parent / "Evidence" / "source-review"
                  / "AR5-high-precision.md")
        self.assertTrue(record.is_file(), "the mandated review record is absent")
        text = record.read_text(encoding="utf-8")
        for citation in ("studio_api.py:1440", "studio_generation.py",
                         "decode_latent_batch", "modules/processing.py:623"):
            with self.subTest(citation=citation):
                self.assertIn(citation, text)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_HP_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
