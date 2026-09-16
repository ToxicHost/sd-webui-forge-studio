"""E0 — the eraser preview stops deleting the layer it is erasing from.

THE DEFECT, as the owner met it: during an eraser drag every stroke on the
active layer disappeared, and came back correctly erased on release. Canonical
pixels were never wrong; the transient display was.

`_composite2D` substituted `S.stroke.canvas` for the active layer during an
eraser stroke. That was right when the eraser PRE-FILLED the stroke canvas with
a copy of the layer and erased from the copy. `ade5fde7` ("CT3d: the Eraser is
the brush with an erase composite") made the eraser accumulate into the alpha
map like the brush and removed the pre-fill -- and left the substitution.
Measured before the repair: a pixel far from the eraser read (58,58,58,255), the
checkerboard, while the layer still held (255,0,0,255); and the stroke canvas
had ZERO painted pixels where the display expected a copy of the layer.

Predates U1 and U2. Present at `8c73801f`, before this programme's first commit.

WHY THE OBVIOUS FIX IS A WORSE BUG. By the time the active layer is reached,
`_compBuffer` already holds every layer beneath it, so a `destination-out` there
punches through the whole stack. It looks perfect on a single layer over the
checkerboard -- which is exactly how it would ship -- and destroys lower layers
in a real document. The guards below pin the erase to a scratch holding the
ACTIVE LAYER ALONE.

WHAT THESE TESTS CAN AND CANNOT DO. The pixel proof needs a real 2D context and
lives in the browser journey recorded in
`Evidence/source-review/E0-eraser-preview.md`: during a drag, a point far from
the eraser stays RED, the erased path reveals BLUE from the lower layer rather
than transparency, and a GREEN upper stripe is untouched -- with preview and
canonical flatten byte-identical at [13,0,242,255]. These guards pin the
STRUCTURE that produces it, in the style BE8 established for this file.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_E0_TESTS = 18

CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"


def _function_body(code: str, name: str) -> str:
    start = code.index(f"function {name}(")
    end = code.index("\n}\n", start)
    return code[start:end]


class TheStaleSubstitutionIsGoneTests(unittest.TestCase):
    def test_the_eraser_branch_no_longer_draws_the_stroke_canvas_as_the_layer(self) -> None:
        """The single line that made the layer vanish."""

        body = _function_body(code_of(CORE), "_composite2D")
        i = body.index("eraserActive && L === AL")
        branch = body[i:i + 220]
        self.assertNotIn("x.drawImage(S.stroke.canvas, 0, 0)", branch)

    def test_the_eraser_branch_calls_the_layer_isolated_preview(self) -> None:
        body = _function_body(code_of(CORE), "_composite2D")
        i = body.index("eraserActive && L === AL")
        self.assertIn("_drawErasedActiveLayer(x, L, w, h)", body[i:i + 220])

    def test_the_helper_exists(self) -> None:
        self.assertIn("function _drawErasedActiveLayer(",
                      code_of(CORE))


class TheEraseIsAppliedToTheACTIVELAYERAloneTests(unittest.TestCase):
    """§6's warning, pinned. `_compBuffer` already holds the lower layers when
    the active layer is reached."""

    def setUp(self) -> None:
        self.body = _function_body(code_of(CORE), "_drawErasedActiveLayer")

    def test_it_draws_the_real_layer(self) -> None:
        self.assertIn("drawImage(L.canvas", self.body)

    def test_destination_out_is_applied_to_the_scratch(self) -> None:
        self.assertIn('e.globalCompositeOperation = "destination-out"', self.body)

    def test_destination_out_is_NEVER_applied_to_the_composite(self) -> None:
        """The whole point. `x` is `_compBuffer`'s context and already holds
        every layer below this one."""

        self.assertNotIn('x.globalCompositeOperation = "destination-out"',
                         self.body)

    def test_the_layer_is_drawn_in_two_non_overlapping_pieces(self) -> None:
        """Even-odd over two rects is the region between them, so a non-normal
        blend mode blends each pixel exactly once."""

        self.assertIn('clip("evenodd")', self.body)

    def test_the_scratch_holds_only_the_active_layer(self) -> None:
        """It is seeded from `L.canvas`, never from `_compBuffer` or a
        flattened document."""

        self.assertIn("e.drawImage(L.canvas,", self.body)
        self.assertNotIn("_compBuffer", self.body)
        self.assertNotIn("getFlattenedImageData", self.body)


class ThePreviewIsBoundedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = _function_body(code_of(CORE), "_drawErasedActiveLayer")

    def test_it_is_scoped_to_the_stroke_rectangle(self) -> None:
        """The RECTANGLE must be derived from the stroke's dirty bounds, not
        merely read from them.

        The first version of this guard asserted `S.stroke.dirty` appeared
        somewhere in the function, and a mutation that replaced the whole
        rectangle with the document's dimensions sailed past it -- the read was
        still there, feeding nothing. Asserting a name is present is not the
        same as asserting it is used."""

        self.assertIn("const d = S.stroke.dirty;", self.body)
        self.assertIn("Math.max(0, d.x0)", self.body)
        self.assertIn("Math.min(S.W, d.x1 + 1) - dx", self.body)
        self.assertIn("Math.min(S.H, d.y1 + 1) - dy", self.body)

    def test_the_scratch_is_sized_to_the_stroke_not_the_document(self) -> None:
        self.assertIn("_eraseScratch.width < dw", self.body)
        self.assertIn("_eraseScratch.height < dh", self.body)

    def test_the_scratch_is_capped_at_the_document(self) -> None:
        """It grows to fit and never shrinks, so the cap is what stops a
        pathological rectangle allocating past the document."""

        self.assertIn("Math.min(S.W,", self.body)
        self.assertIn("Math.min(S.H,", self.body)

    def test_it_allocates_no_full_document_canvas_per_move(self) -> None:
        self.assertNotIn("_createCanvas(S.W, S.H)", self.body)
        self.assertNotIn("_createCanvas(w, h)", self.body)


class ThePreviewMatchesWhatWillCommitTests(unittest.TestCase):
    def test_it_uses_the_same_alpha_commit_uses(self) -> None:
        """`commitStroke` applies `S.brushBuildup ? 1 : S.brushOpacity`.
        A preview at a different alpha is a preview of a different picture."""

        body = _function_body(code_of(CORE), "_drawErasedActiveLayer")
        self.assertIn("S.brushBuildup ? 1 : S.brushOpacity", body)

    def test_selection_is_not_applied_a_second_time(self) -> None:
        """It is already baked into `S.stroke.canvas` by `alphaMapToImageData`
        (BE4). Applying it here would square it. Measured in the browser:
        selections of 100/50/25 percent removed 240/120/60 -- exactly once."""

        body = _function_body(code_of(CORE), "_drawErasedActiveLayer")
        self.assertNotIn("S.selection", body)


class TheEraserCoverageIsActuallyConvertedTests(unittest.TestCase):
    """The second half of the defect: the conversion was gated on the BRUSH, so
    an eraser stroke left `S.stroke.canvas` empty and even a correct preview
    would have had nothing to erase with. Measured: 0 painted pixels."""

    def test_the_wet_stroke_conversion_runs_for_the_eraser(self) -> None:
        code = code_of(CORE)
        i = code.index("const wetTool")
        clause = code[i:i + 220]
        self.assertIn('S.tool === "brush"', clause)
        self.assertIn('S.tool === "eraser"', clause)

    def test_the_conversion_is_still_bounded_to_the_frame(self) -> None:
        """U2's bound must survive E0."""

        code = code_of(CORE)
        i = code.index("const wetTool")
        block = code[i:i + 1200]
        self.assertIn("S.stroke.frameDirty", block)
        self.assertIn("alphaMapToImageData(col, frame)", block)

    def test_the_wet_stroke_canvas_stays_brush_only_for_its_consumers(self) -> None:
        """Every `strokeDrawCanvas` consumer means "there is a wet BRUSH stroke
        to bake into the stack". Handing it an eraser stroke would put erase
        coverage into the paint path."""

        self.assertIn('if (wetTool === "brush") strokeDrawCanvas', code_of(CORE))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_E0_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
