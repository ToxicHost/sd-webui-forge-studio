"""R1-A — the live brush preview stops applying Opacity twice.

THE DEFECT, as the owner met it:

> Ink Wash does not appear while drawing and becomes visible only on release.
> Airbrush differs markedly between press and release.

One cause, and it is not in V2. BE17 (`6af88c7a`) gave Buildup its meaning --
"the target is Flow x Opacity and COMMIT MULTIPLIES BY 1" (`canvas-core.js`
:2525) -- and changed exactly one line to match, `commitStroke`'s composite
alpha. It touched no display path. E0 (`c9a44a3b`) later gave the ERASER
preview the same alpha, saying in the file that otherwise "applying it again
here would square it". The BRUSH preview was never given it, so all four of its
display sites went on multiplying by Opacity that was already inside the stroke
buffer.

    preset      opacity  flow   per-pass target   preview / commit
    Airbrush      0.50   0.15        0.075             0.50x
    Ink Wash      0.35   0.13        0.0455            0.35x
    Pastel        0.85   0.45        0.3825            0.85x

Ink Wash is worst because it is faint before the attenuation: one pass peaks at
`(1 x 0.0455 x 255) | 0` = 11 of 255, about 4% alpha, and 0.35x of that is 1.6%
-- the "does not appear while drawing". On release the commit drops the extra
factor and the stroke appears. Airbrush is the same mechanism at half strength.

BOTH ENGINES. V2 writes `S.stroke.alphaMap` through `transfer`, Legacy
accumulates into it directly, and the display converts the one map through
`alphaMapToImageData` into the one `S.stroke.canvas` these four sites draw.
Legacy Ink Wash previews at the same 0.35x. `canvas-core.js` is clean at
`56ae976d`, so this predates the uncommitted U3-D/G/J work entirely.

WHY FOUR SITES AND NOT ONE. The WebGL path (`imagePreviewActive`) is the
shipping default and is the only one the owner normally meets, so repairing it
alone would look complete and leave the Canvas2D fallback, the cache-build path
and the layer-stack composite previewing a different picture -- the half-port
this programme keeps finding. The count guard below is what stops a fifth
display path being added without one.

WHAT THESE TESTS CAN AND CANNOT DO. The pixel proof needs a real 2D context and
belongs to the owner's browser journey, exactly as E0's record says of its own
claim. These guards pin the STRUCTURE that produces it, in the style E0 and BE8
established for this file. Recorded in
`Evidence/source-review/R1A-buildup-preview-alpha.md`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_R1A_TESTS = 14

CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

#: The alpha every path that draws a wet BRUSH stroke must use.
THE_ALPHA = "S.brushBuildup ? 1 : S.brushOpacity"


def _function_body(code: str, name: str) -> str:
    start = code.index(f"function {name}(")
    end = code.index("\n}\n", start)
    return code[start:end]


def _branch(body: str, opener: str, closer: str) -> str:
    """One `if` branch of a long function, by its own code.

    `composite` carries four display paths that differ only in which guard
    reaches them, so a bare string search cannot tell them apart. Slicing on the
    guard and the statement that ends the branch can."""

    start = body.index(opener)
    end = body.index(closer, start + len(opener))
    return body[start:end]


class TheCommitIsTheReferenceTests(unittest.TestCase):
    """Everything else is measured against what actually lands on the layer."""

    def test_commit_applies_one_not_opacity_under_buildup(self) -> None:
        body = _function_body(code_of(CORE), "commitStroke")
        self.assertIn(f"wasMask ? 1 : ({THE_ALPHA})", body)

    def test_the_dab_target_already_contains_opacity_under_buildup(self) -> None:
        """The reason the commit multiplies by 1. If this line stopped folding
        Opacity in, the whole argument inverts and the preview would be the
        correct one -- so it is pinned rather than assumed."""

        self.assertIn("useSoftBlend ? opacity * (S.brushOpacity ?? 1) : opacity",
                      code_of(CORE))

    def test_v2_folds_it_in_the_same_place(self) -> None:
        """Both engines feed one alpha map, so a divergence here would make the
        repair correct for one engine and wrong for the other."""

        coverage = code_of(APP_ROOT / "forge_studio" / "frontend" / "v2"
                           / "coverage.js")
        self.assertIn("s.buildup ? flow * opacity : flow", coverage)


class EveryBrushDisplayPathUsesTheCommitAlphaTests(unittest.TestCase):
    """Four sites. A preview at a different alpha is a preview of a different
    picture -- E0's sentence, applied to the brush."""

    def setUp(self) -> None:
        self.code = code_of(CORE)

    def test_the_layer_stack_composite(self) -> None:
        """`_composite2D` draws the wet stroke into the stack under the layer's
        own opacity, which is a separate factor and stays."""

        body = _function_body(self.code, "_composite2D")
        self.assertIn(f"({THE_ALPHA}) * L.opacity", body)

    def test_the_canvas2d_dirty_fast_path(self) -> None:
        """BE8's fast path. Reachable whenever the WebGL preview does not own
        the display, which is where the owner's Canvas2D rows run."""

        body = _function_body(self.code, "composite")
        self.assertIn(f"onMask ? S.mask.opacity\n                        : ({THE_ALPHA})",
                      body)

    def test_the_composite_cache_build_path(self) -> None:
        """SLICED BY CODE, never by the comment that explains it. `code_of`
        strips comments, so an anchor spanning one cannot match -- and an anchor
        that quotes one would pass on the explanation while the line beneath it
        was wrong."""

        body = _function_body(self.code, "composite")
        branch = _branch(body, "if (_canBuildCache) {", "} else if")
        self.assertIn(f"c.globalAlpha = {THE_ALPHA};", branch)
        self.assertIn("c.drawImage(strokeDrawCanvas, 0, 0);", branch)

    def test_the_webgl_shipping_default(self) -> None:
        """`imagePreviewActive`. The one the owner actually meets."""

        body = _function_body(self.code, "composite")
        branch = _branch(body, "if (S.imagePreviewActive && strokeDrawCanvas) {",
                         "c.globalAlpha = 1;")
        self.assertIn(f"c.globalAlpha = {THE_ALPHA};", branch)

    def test_the_two_bare_sites_are_the_only_two(self) -> None:
        """The fast path spells it parenthesised on a continuation line, so the
        bare form belongs to exactly the cache-build and WebGL branches. A third
        occurrence is a display path nobody reviewed."""

        body = _function_body(self.code, "composite")
        self.assertEqual(2, body.count(f"c.globalAlpha = {THE_ALPHA};"))

    def test_no_brush_display_path_applies_opacity_unconditionally(self) -> None:
        """THE COUNT GUARD, and the reason this file gets one.

        Four sites had to be found by reading rather than by a failing test,
        because each is a plausible-looking line on its own. A fifth display
        path added later would be too. `globalAlpha = S.brushOpacity` with
        nothing guarding it must not reappear anywhere in a display function.

        `commitShape` is excluded by name: it is the shape tool, which is not a
        stroke, does not accumulate and has no buildup."""

        for name in ("_composite2D", "composite", "_drawErasedActiveLayer"):
            with self.subTest(function=name):
                body = _function_body(self.code, name)
                self.assertNotIn("globalAlpha = S.brushOpacity", body)


class TheThingsThatMustNotChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code = code_of(CORE)

    def test_the_eraser_preview_keeps_the_alpha_e0_gave_it(self) -> None:
        body = _function_body(self.code, "_drawErasedActiveLayer")
        self.assertIn(THE_ALPHA, body)

    def test_the_mask_overlay_keeps_its_own_display_opacity(self) -> None:
        """`S.mask.opacity` is how visible the mask overlay is on screen. It is
        not brush Opacity and buildup has nothing to say about it."""

        body = _function_body(self.code, "composite")
        self.assertIn("c.globalAlpha = S.mask.opacity;", body)

    def test_the_shape_tool_is_untouched(self) -> None:
        """A shape is committed in one draw. There is no stroke buffer, nothing
        accumulates, and `S.brushBuildup` is not consulted anywhere in it."""

        body = _function_body(self.code, "commitShape")
        self.assertIn("T.ctx.globalAlpha = S.brushOpacity;", body)
        self.assertNotIn("brushBuildup", body)

    def test_the_thirteen_non_buildup_presets_cannot_move(self) -> None:
        """THE SAFETY PROPERTY, and it is structural rather than a tolerance.

        With `S.brushBuildup` false the expression evaluates to
        `S.brushOpacity` -- the identifier that was already there -- so every
        preset that does not declare buildup is byte-identical BY CONSTRUCTION.
        Three of the sixteen declare it; this pins the count so a fourth cannot
        arrive without this test being reconsidered."""

        core = code_of(CORE)
        self.assertEqual(3, core.count("buildup: true"))
        self.assertEqual(13, core.count("buildup: false"))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_R1A_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
