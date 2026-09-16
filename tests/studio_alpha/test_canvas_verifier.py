"""Guards on the Canvas verifier, one per mistake that invalidated a live leg.

These are deliberately mutation-style: each asserts the *absence* of a specific
bad pattern, so reintroducing it fails here rather than during an authorized
live run where the budget is one generation.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha import canvas_verifier as cv  # noqa: E402

DISCOVER = cv.DISCOVER_RESULT_JS
ORACLE = cv.NATIVE_CANVAS_ORACLE_JS
FRONTEND = APP_ROOT / "forge_studio" / "frontend"


class LayoutAwarenessTests(unittest.TestCase):
    def test_classic_layout_does_not_require_the_session_strip(self) -> None:
        # THE first defect. Classic renders to #outputGrid; requiring the strip
        # made discovery impossible in the layout that was actually active.
        self.assertIn("#outputGrid", DISCOVER)
        self.assertIn(".output-thumb", DISCOVER)
        # The strip is consulted only to DETECT the layout, never assumed.
        self.assertIn("offsetParent", DISCOVER,
                      "layout must be detected, not assumed")

    def test_both_containers_are_encoded(self) -> None:
        for token in ("#sessionStripScroll", ".session-thumb",
                      "#outputGrid", ".output-thumb"):
            self.assertIn(token, DISCOVER)

    def test_identity_is_by_data_idx_not_position(self) -> None:
        self.assertIn("dataset.idx", DISCOVER)
        self.assertIn("expectedIdx", DISCOVER)

    def test_exactly_one_match_is_required(self) -> None:
        self.assertIn("matches.length !== 1", DISCOVER)


class ExcludedControlTests(unittest.TestCase):
    def test_every_excluded_control_is_named(self) -> None:
        for control in cv.EXCLUDED_CONTROL_IDS:
            self.assertIn(control, DISCOVER, f"{control} is not excluded")

    def test_clear_is_excluded(self) -> None:
        # The control that was actually clicked, destroying the session.
        self.assertIn("sessionStripClear", DISCOVER)

    def test_strip_header_descendants_are_excluded(self) -> None:
        self.assertIn(".session-strip-head", DISCOVER)

    def test_no_generic_first_element_selection(self) -> None:
        # The shape of the original bug: a broad querySelector over a container
        # that also holds header controls.
        for bad in ('querySelector("button")', "querySelector('button')",
                    'querySelectorAll("button")', "[0].click()"):
            self.assertNotIn(bad, DISCOVER)
        # No bare tag/role selectors that could match a toolbar control.
        self.assertNotRegex(DISCOVER, r'querySelector\(\s*["\'](button|img|div)["\']')


class NativeOracleTests(unittest.TestCase):
    def test_the_viewport_canvas_is_never_the_oracle(self) -> None:
        # THE second defect. #studio-canvas is a scaled viewport surface.
        self.assertNotIn("studio-canvas", ORACLE)
        self.assertNotIn("canvasPreview", ORACLE)

    def test_the_native_output_layer_is_the_oracle(self) -> None:
        self.assertIn('l.name === "Output"', ORACLE)
        self.assertIn("out.ctx.getImageData", ORACLE)
        self.assertIn("out.canvas.width", ORACLE)

    def test_both_state_shapes_are_supported(self) -> None:
        # The shipped product does `const S = Core.state` -- an OBJECT. An
        # earlier version of this test asserted the opposite and locked in a
        # regression that made the oracle reject the real build. Both shapes
        # must work, and neither may be assumed.
        self.assertIn('(typeof raw === "function") ? raw() : raw', ORACLE)
        self.assertNotIn(
            '(typeof StudioCore.state === "function") ? StudioCore.state() : null',
            ORACLE,
            "the function-only accessor regression is back",
        )

    def test_the_product_reads_state_as_a_property(self) -> None:
        # Pins the fact the regression got wrong, at its source.
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("const S = Core.state;", app)

    def test_invalid_state_is_rejected(self) -> None:
        self.assertIn("StudioCore state unavailable or invalid", ORACLE)
        self.assertIn("Array.isArray(s.layers)", ORACLE)

    def test_document_dimensions_are_guarded_before_comparing(self) -> None:
        # displayOnCanvas falls back to drawImage(img, 0, 0, S.W, S.H), which
        # SCALES. A hash comparison without this guard is not identity.
        self.assertIn("s.W !== expectedW", ORACLE)
        self.assertIn("s.H !== expectedH", ORACLE)
        self.assertIn("would have scaled", ORACLE)

    def test_confirmation_requires_exact_hash_equality(self) -> None:
        self.assertIn("nativeSha === expectedPixelSha", ORACLE)

    def test_canvas_changed_is_never_sufficient(self) -> None:
        for weak in ("!== before", "changed", "isBlank", "toDataURL"):
            if weak == "changed":
                # allow the word inside a message, not as a decision
                self.assertNotRegex(ORACLE, r"CANVAS_OPEN_CONFIRMED\s*[:=][^\n]*changed")
            else:
                self.assertNotIn(weak, ORACLE)

    def test_success_is_not_inferable_from_weak_signals(self) -> None:
        for weak in ("selectedOutputIdx", "preview", "Gallery", "lightbox"):
            self.assertNotIn(weak, ORACLE)


class ProductFactTests(unittest.TestCase):
    """The codified constants must still match the shipped frontend."""

    def test_send_to_canvas_control_exists_in_the_shell(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="outputToCanvas"', html)
        self.assertEqual(cv.SEND_TO_CANVAS_CONTROL, "#outputToCanvas")

    def test_the_output_layer_name_matches_the_product(self) -> None:
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn('layerName: "Output"', app)
        self.assertEqual(cv.OUTPUT_LAYER_NAME, "Output")

    def test_the_scaling_fallback_still_exists(self) -> None:
        # If this disappears the dimension guard could be relaxed - but only
        # deliberately, which is why it is pinned.
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("drawImage(imgEl, 0, 0, S.W, S.H)", app)

    def test_the_excluded_controls_still_exist_in_the_shell(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        for control in cv.EXCLUDED_CONTROL_IDS:
            self.assertIn(f'id="{control}"', html)

    def test_both_result_containers_still_exist(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sessionStripScroll"', html)
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("outputGrid", app)


class SyntheticRehearsalRecordTests(unittest.TestCase):
    """The rehearsal ran in a browser; this pins its result as a fact."""

    SYNTHETIC_PIXEL_SHA = (
        "4d592c44d32fc6b6b36bc28bd24d56816d7b513cb3d4c8b16b629e401bddc06d"
    )

    def test_the_recorded_synthetic_hash_is_a_sha256(self) -> None:
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", self.SYNTHETIC_PIXEL_SHA))

    def test_the_rehearsal_is_documented(self) -> None:
        doc = (APP_ROOT.parent / "Evidence" / "studio-canvas-verifier-repair"
               / "CANVAS_NATIVE_DOCUMENT_TRACE.md")
        text = doc.read_text(encoding="utf-8")
        self.assertIn("CANVAS_VERIFIER_SYNTHETIC_REHEARSAL_PASSED", text)
        self.assertIn(self.SYNTHETIC_PIXEL_SHA, text)


if __name__ == "__main__":
    unittest.main()
