"""BE8: the display stops doing full-canvas work on every pointer move.

WHY THESE ASSERT STRUCTURE AND NOT MILLISECONDS.

Repeated runs within one page state reproduce to about 0.5%. Across page states
the SAME configuration swings by 4x -- the first benchmark after a fresh load
measured a p50 of 28 ms where the fourth measured 118, with nothing changed but
heap pressure and GC. A millisecond threshold in this file would fail on
someone else's laptop for no engineering reason, and would pass on this one for
reasons that have nothing to do with the code.

So the timings live in `Evidence/be0-perf/` where they can carry their context,
and these guards assert the WORK THAT WAS REMOVED, which is not machine
dependent.

THE ONE THAT MATTERS MOST is `test_the_pixel_loop_has_no_hoistable_work`. BE6's
`tipDistance` recomputed two trig calls, four table lookups and two divisions
for every pixel of every dab -- and a 170 px dab is about 22,000 pixels. It cost
a measured 4.8x regression in stamping across BE1-BE7. Inlining `tipFrame` back
into the loop "for readability" would reintroduce it silently, and that guard is
what stops it.
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

CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"
HARNESS = APP_ROOT.parent / "Evidence" / "be0-perf" / "dispatch_baseline.js"

EXPECTED_BE8_TESTS = 14


def _code_only() -> str:
    source = CORE.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


def _function_body(code: str, name: str) -> str:
    start = code.index(f"function {name}(")
    return code[start:code.index("\n}\n", start)]


class ThePixelLoopIsCleanTests(unittest.TestCase):
    """Nothing that could be decided once may be decided per pixel."""

    def test_the_pixel_loop_has_no_hoistable_work(self):
        """The guard that stops the 4.8x regression coming back.

        `cover`'s inner loop must contain no trigonometry, no table lookup and
        no division -- all of those belong to `tipFrame`, which runs once per
        dab.
        """

        code = _code_only()
        stamp = _function_body(code, "stampAlphaMap")
        loop = stamp[stamp.index("const cover = "):]
        loop = loop[:loop.index("\n    };")]
        for banned in ("Math.cos", "Math.sin", "TIP_ASPECT", "TIP_EXTENT",
                       "TIP_NORM", "S.brushRatio", "S.brushSpikes"):
            with self.subTest(banned=banned):
                self.assertNotIn(
                    banned, loop,
                    f"`{banned}` is being evaluated per pixel again; it belongs "
                    "in tipFrame, which runs once per dab")

    def test_the_frame_precomputes_reciprocals(self):
        """Multiplies, not divides, in the inner path."""

        body = _function_body(_code_only(), "tipFrame")
        self.assertIn("const invRx = 1 / rx, invRy = 1 / ry;", body)
        self.assertIn("* invRx", body)
        self.assertIn("* invRy", body)

    def test_the_frame_skips_rotation_when_it_cannot_matter(self):
        """A circular tip has no orientation, so the transform is the identity
        and is skipped outright rather than computed and wasted."""

        body = _function_body(_code_only(), "tipFrame")
        self.assertIn("const circular =", body)
        self.assertIn("const rotates =", body)

    def test_the_density_test_and_falloff_dispatch_are_hoisted(self):
        code = _code_only()
        stamp = _function_body(code, "stampAlphaMap")
        self.assertIn("const stipple = density < 0.99;", stamp)
        self.assertIn("const falloff = gaussian ? dabAlphaGauss : dabAlpha;", stamp)


class NoCacheIsBuiltThatNothingCanReadTests(unittest.TestCase):
    """`_compositeCache` has one consumer, and it is gated.

    The dirty-rect fast path is skipped when the WebGL preview owns the display
    -- the SHIPPING DEFAULT -- because a Canvas2D snapshot is not what the owner
    is looking at. The cache was built anyway, every pointer move, with a
    full-canvas getImageData.
    """

    def test_the_cache_build_is_gated_on_the_display_path(self):
        code = _code_only()
        self.assertIn("!S.imagePreviewActive &&\n        !S.layers.slice", code)

    def test_the_mask_mode_cache_build_is_gated_too(self):
        code = _code_only()
        self.assertIn(
            'S.editingMask && !S.imagePreviewActive) {', code,
            "the mask-mode branch still snapshots the canvas on a display path "
            "that cannot use the snapshot")

    def test_the_fast_path_is_still_gated_the_same_way(self):
        """The gate and the build must agree, or one of them is wrong."""

        code = _code_only()
        self.assertIn("if (_canUseDirtyFastPath && !S.imagePreviewActive) {", code)


class TheLiveCompositeIsScopedTests(unittest.TestCase):
    """Per-FRAME dirty, not per-STROKE dirty."""

    def test_a_per_frame_dirty_rect_exists(self):
        code = _code_only()
        self.assertIn("S.stroke.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };", code)

    def test_the_coverage_loop_expands_it(self):
        code = _code_only()
        stamp = _function_body(code, "stampAlphaMap")
        self.assertIn("const fd = S.stroke.frameDirty;", stamp)

    def test_the_fast_path_consumes_and_resets_it(self):
        """If it were not reset, it would grow into the stroke's whole bounding
        box and the scoping would buy nothing after the first few moves."""

        code = _code_only()
        self.assertIn("const d = S.stroke.frameDirty || S.stroke.dirty;", code)
        self.assertIn("if (S.stroke.frameDirty) {", code)

    def test_the_restore_and_the_draw_use_different_coordinate_systems(self):
        """`putImageData` ignores the transform and needs DEVICE coordinates;
        `drawImage` respects it and needs DOCUMENT ones. Getting those the same
        way round is the whole trick."""

        code = _code_only()
        self.assertIn("c.putImageData(_compositeCache, 0, 0, ex, ey, ew, eh);", code)
        self.assertIn(
            "c.drawImage(S.stroke.canvas, dx, dy, dw, dh, dx, dy, dw, dh);", code)

    def test_the_alpha_map_conversion_accepts_a_rect(self):
        code = _code_only()
        self.assertIn("function alphaMapToImageData(color, rect) {", code)
        self.assertIn("const d = rect || S.stroke.dirty;", code)


class TheHarnessRecordsItsContextTests(unittest.TestCase):
    """A comparison across page states is invalid and must not look valid."""

    def test_the_harness_records_a_page_state_ordinal(self):
        self.assertTrue(HARNESS.exists(), f"missing harness at {HARNESS}")
        text = HARNESS.read_text(encoding="utf-8")
        self.assertIn("pageStateOrdinal", text)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE8_TESTS, found)


if __name__ == "__main__":
    unittest.main()
