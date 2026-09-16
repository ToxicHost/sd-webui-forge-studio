"""U3-TF — tip geometry, and the adapter that finally forwards it.

WHAT WAS WRONG. `describeStroke` read eleven fields off Studio state and
hard-coded or dropped the rest, so fifteen of the sixteen shipping presets
reached the engine as something other than what they declare. The tip KIND was
among them, which is why every tip rendered round: Flat Chisel declares
`ratio: 1.0` and gets its flatness entirely from `TIP_ASPECT.flat`, so dropping
the kind flattened nothing.

And the kernel could not have drawn them anyway. `stamp` measured
`sqrt(dx*dx + dy*dy)` for every tip, so `rendererFor` would route a flat tip to
the stamp renderer and the stamp renderer would draw a circle. `ratio` appeared
in `coverage.js` exactly once, inside `rendererFor`, as a routing condition.

WHAT WAS DONE. Legacy's tip geometry is ported into the V2 kernel --
`TIP_ASPECT`, `TIP_NORM`, `TIP_EXTENT`, the spike fold, and above all the ORDER:

    translate -> ROTATE by -angle -> FOLD (spikes) -> ANISOTROPY (ratio) -> norm

THE ORDER IS THE PART A NAIVE TEST MISSES, and `canvas-core.js:2262` says why:
"Rotation preserves an isotropic norm, so folding the angle and THEN measuring
sqrt(dx^2+dy^2) cannot change any pixel: Spikes was not unwired, it was
algebraically incapable of doing anything." A test asserting that a frame was
built passes against that broken port. Everything here measures PIXELS.

THE NUMBERS THESE TESTS GUARD, at radius 60 on a 420 document:

    round        120 x 120   fill 0.785 = pi/4, a true circle
    flat  a=0    120 x  36   aspect 0.3
    flat  a=90    36 x 120   same 3,404 px -- rotation preserves area
    marker        96 x  42   fill 1.000 -- Chebyshev gives a true rectangle
    ratio 0.6    120 x  72   ellipse
    spikes 6     120 x 108   5,672 -> 9,280 px against the unspiked tip

`SuiteIntegrityTests` is last, and its count moves with every added test.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_U3TF_TESTS = 36

PROBE = Path(__file__).with_name("u3tf_tips_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"
ADAPTER = APP_ROOT / "forge_studio" / "frontend" / "v2" / "canvas-adapter.js"
HARNESS_TIPS = (APP_ROOT.parent / "Evidence" / "u3r2f-owner"
                / "_u3tf_tips.js")

_RESULT: dict | None = None


def probe() -> dict:
    global _RESULT
    if _RESULT is None:
        out = subprocess.run(["node", str(PROBE)], capture_output=True,
                             text=True, check=False, cwd=str(APP_ROOT))
        if out.returncode != 0:
            raise AssertionError(f"probe failed: {out.stderr[-2000:]}")
        _RESULT = json.loads(out.stdout)
    return _RESULT


def shape(name: str) -> dict:
    return probe()["shapes"][name]


class ATipIsNoLongerAlwaysRoundTests(unittest.TestCase):
    """The owner's report, in one sentence: "all the brushtips are round"."""

    def test_a_round_tip_is_a_circle(self) -> None:
        """The control. A disc fills pi/4 of its bounding box; anything else
        here would mean the port broke the case it must not touch."""

        s = shape("round")
        self.assertEqual(s["width"], s["height"])
        self.assertAlmostEqual(s["fill"], math.pi / 4, places=2)

    def test_a_flat_tip_is_flat(self) -> None:
        """`TIP_ASPECT.flat` is 0.3 of the RADIUS. At radius 60 that is a 120 x
        36 footprint -- and Flat Chisel declares `ratio: 1.0`, so this comes
        entirely from the kind the adapter used to drop."""

        s = shape("flat0")
        self.assertEqual(120, s["width"])
        self.assertEqual(36, s["height"])

    def test_rotating_a_flat_tip_turns_it_without_resizing_it(self) -> None:
        """A rotation is an isometry. Equal painted area at 0 and 90 degrees is
        what says the angle rotates the shape rather than rescaling it."""

        a, b = shape("flat0"), shape("flat90")
        self.assertEqual(a["height"], b["width"])
        self.assertEqual(a["width"], b["height"])
        self.assertEqual(a["painted"], b["painted"])

    def test_a_marker_is_a_rectangle(self) -> None:
        """`TIP_NORM.marker` is Chebyshev, which is the tip's identity and not a
        setting. A Chebyshev ball IS its bounding box, so the fill is exactly 1
        -- the sharpest available statement that the norm ported."""

        s = shape("marker")
        self.assertEqual(1.0, s["fill"])
        self.assertEqual(s["painted"], s["width"] * s["height"])

    def test_marker_keeps_its_own_extent(self) -> None:
        """`TIP_EXTENT.marker` is 0.8: a scale on the long axis, not an aspect.
        Legacy's table records that reading it as a half-width narrowed Bold
        Marker from 0.35r to 0.28r and only the tenth of ten presets caught it."""

        s = shape("marker")
        self.assertEqual(96, s["width"])
        self.assertEqual(42, s["height"])

    def test_ratio_makes_an_ellipse(self) -> None:
        s = shape("roundRatio06")
        self.assertEqual(120, s["width"])
        self.assertEqual(72, s["height"])
        self.assertAlmostEqual(s["fill"], math.pi / 4, places=2)


class TheOrderIsTheRepairTests(unittest.TestCase):
    """Rotate, THEN fold, THEN apply anisotropy. Get it wrong and the frame is
    still built, the tests still find a shape, and spikes still do nothing."""

    def test_spikes_change_a_shaped_tip(self) -> None:
        o = probe()["order"]
        self.assertTrue(o["shapedChangesWithSpikes"])
        self.assertNotEqual(o["shapedPaintedNoSpikes"], o["shapedPaintedWithSpikes"])

    def test_spikes_cannot_change_a_circle(self) -> None:
        """Asserted rather than promised, in Legacy's words: a circle has no
        orientation. `TIP_CAPABILITIES` calls this "needs-shape"."""

        self.assertTrue(probe()["order"]["circleIgnoresSpikes"])

    def test_an_angle_cannot_change_a_circle(self) -> None:
        self.assertTrue(probe()["order"]["circleIgnoresAngle"])

    def test_a_spiked_tip_is_not_an_ellipse(self) -> None:
        """A star fills less of its box than the ellipse it was folded from."""

        self.assertLess(shape("spikes6")["fill"], shape("roundRatio06")["fill"])


class ARoundTipDidNotMoveTests(unittest.TestCase):
    """§26's stop condition. Guaranteed by construction: `tipFrame` returns null
    for a circle and `stamp` takes the closure it always took."""

    def test_a_round_tip_is_byte_identical_to_a_bare_deposition(self) -> None:
        r = probe()["roundUnchanged"]
        self.assertEqual(0, r["differing"])
        self.assertEqual(0, r["maxDelta"])
        self.assertGreater(r["painted"], 0)

    def test_the_frame_builder_is_what_declines(self) -> None:
        """Not a special case in `stamp`. One definition of "is this a circle",
        and `rendererFor` asks the same question."""

        self.assertIn("const frame = tipFrame(r, tipAtMark);", code_of(COVERAGE))


class TheRendererIsChosenByTheRealTipTests(unittest.TestCase):

    def test_a_round_tip_keeps_the_analytic_sweep(self) -> None:
        """Which is where C2's crease repair lives. Routing a round tip to the
        stamp renderer would quietly undo it."""

        r = probe()["routing"]
        self.assertEqual(r["SWEEP"], r["round"])
        self.assertEqual(r["SWEEP"], r["bare"])

    def test_every_shaped_tip_routes_to_the_stamp_renderer(self) -> None:
        r = probe()["routing"]
        for k in ("flat", "marker", "ratio", "spiked", "scatter"):
            with self.subTest(tip=k):
                self.assertEqual(r["STAMP"], r[k])

    def test_the_nominal_radius_defeats_the_half_pixel_floor(self) -> None:
        """`tipFrame` floors both axes at half a pixel, so asked at radius 1 a
        flat tip's 0.3 aspect floors to 0.5 and reads round. `rendererFor` asks
        at 1024."""

        self.assertIn("tipFrame(1024, t)", code_of(COVERAGE))


class TheAdapterForwardsTheTipTests(unittest.TestCase):
    """The seam. A frame test and an adapter spelling test both pass with the
    two never connected, which is the condition that has held since U3."""

    def _by(self, label: str) -> dict:
        return next(r for r in probe()["adapter"] if r["label"] == label)

    def test_the_kind_reaches_the_kernel(self) -> None:
        self.assertEqual("flat", self._by("flat")["tipKind"])

    def test_ratio_and_spikes_reach_the_kernel(self) -> None:
        b = self._by("bristle")
        self.assertAlmostEqual(0.55, b["ratio"])
        self.assertEqual(6, b["spikes"])

    def test_the_angle_is_converted_from_degrees(self) -> None:
        """`canvas-core.js:205` calls it "0-360 degrees" and `_brushAngleRad` is
        the only place Legacy converts. 45 degrees is pi/4."""

        self.assertAlmostEqual(math.pi / 4, self._by("calligraphy")["angleRad"],
                               places=5)

    def test_density_reaches_the_deposition(self) -> None:
        """Was the literal 1, so four presets asking for a lighter dab train got
        a full one."""

        self.assertAlmostEqual(0.85, self._by("bristle")["density"])

    def test_a_shaped_tip_is_not_swept(self) -> None:
        for label in ("flat", "calligraphy", "bristle"):
            with self.subTest(label=label):
                self.assertFalse(self._by(label)["swept"])

    def test_a_round_tip_is_still_swept(self) -> None:
        self.assertTrue(self._by("pixel-perfect")["swept"])

    def test_a_following_tip_gets_its_angle_as_an_argument(self) -> None:
        """The sampler FREEZES its marks -- §19.9's contract that nothing may
        alter a contact mid-flight. The first version of this wrote
        `mark.tipAngle` and threw "object is not extensible" the moment a flat
        tip was drawn. The freeze was right and the write was wrong.

        U3-J made the same contract bite a second time: scatter has to move a
        dab, and moving it by assigning `mark.x` would throw for exactly the
        same reason. It builds a new object instead, which is why the argument
        is now `placed` -- so this asserts the angle is still an ARGUMENT and
        that nothing anywhere assigns through a mark."""

        code = code_of(ADAPTER)
        self.assertIn("V.stamp(st.buffer, placed, r, dep, tipAngle, j);", code)
        for forbidden in ("mark.x =", "mark.y =", "mark.tipAngle ="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, code)


class FollowStrokeIsADynamicNotAKindTests(unittest.TestCase):
    """The owner saw this one immediately: "the flat brushes did the rotating
    every stamp issue again".

    The first version read follow-stroke off the tip KIND, from
    `canvas-core.js:2381`. That is not the live path. `dabRotation`
    (`canvas-core.js:3438`) is, and it reads `S.brushDynamics.followStroke` --
    so Calligraphy, whose own description is "A held nib. Angle is fixed, so
    width follows direction", declares `followStroke: false` and must be held at
    a fixed 45 degrees. Keying off `preset === "flat"` rotated it at every mark.

    Measured as the SPREAD of the angles the kernel was handed over an arc,
    which is the quantity "rotates at every stamp" actually names. A test that
    only checked the flag would pass with the angle still moving.
    """

    def _f(self) -> dict:
        return probe()["followStroke"]

    def test_a_held_nib_never_rotates(self) -> None:
        self.assertEqual(0, self._f()["heldOverrides"])
        self.assertEqual(0, self._f()["heldSpreadRad"])

    def test_a_following_tip_tracks_the_arc(self) -> None:
        """The other half. A gate that held everything still would pass the test
        above and be just as wrong."""

        self.assertGreater(self._f()["followingSpreadRad"], 3.0)
        self.assertGreater(self._f()["followingOverrides"], 50)

    def test_both_lay_the_same_number_of_marks(self) -> None:
        """Rotation changes the tip's extent along travel, which prices the gap.
        Equal counts say the fix moved the ANGLE and not the spacing."""

        f = self._f()
        self.assertEqual(f["heldMarks"], f["followingMarks"])

    def test_the_opening_mark_does_not_guess_a_heading(self) -> None:
        """The sampler places it with a null heading (`sampler.js:124`) because a
        stroke has no direction until the pointer moves. Legacy DEFERS that dab
        rather than guess. V2 paints immediately, so it falls back to the
        contact's frozen angle -- substituting 0 pointed the first dab of every
        following tip the wrong way."""

        self.assertTrue(self._f()["followingFirstIsUndefined"])


class SmoothingIsTheOwnersTests(unittest.TestCase):
    """BR-03, and the bundle's product principle that Studio must never secretly
    reshape a stroke."""

    def _rows(self) -> list[dict]:
        return probe()["smoothing"]

    def test_zero_means_no_smoothing(self) -> None:
        """Pixel Perfect declares `smoothing: 0` and is the one preset for which
        that is the entire point. It was smoothed anyway."""

        row = next(r for r in self._rows() if r["asked"] == 0)
        self.assertEqual("raw", row["mode"])

    def test_the_owners_level_reaches_the_filter(self) -> None:
        """Omitted entirely before, so Fine Liner's 8 and Scatter Dust's 1
        produced the same window."""

        for asked in (3, 8):
            with self.subTest(asked=asked):
                row = next(r for r in self._rows()
                           if r["asked"] == asked and r["zoom"] == 1)
                self.assertEqual(asked, row["strength"])
                self.assertEqual("natural", row["mode"])

    def test_the_window_follows_zoom(self) -> None:
        """`filters.js:102` computes `strength * SCREEN_PX_PER_STEP / scale`,
        the same form as Legacy's stabiliser. The adapter passed the literal 1,
        so the window was wrong at every zoom but 100%."""

        row = next(r for r in self._rows() if r["zoom"] == 2.5)
        self.assertAlmostEqual(2.5, row["scale"])


class WhatIsStillNotImplementedTests(unittest.TestCase):
    """Recorded as tests so a later reader cannot mistake silence for support.
    The bundle's rule: no visible control may silently do nothing."""

    def test_grain_is_absent_from_the_kernel(self) -> None:
        """14 of 16 presets declare it. Phase 6 owns it; claiming it here would
        route tips to a renderer that cannot deliver it."""

        self.assertEqual(0, code_of(COVERAGE).count("grain"))

    def test_the_gaussian_falloff_is_never_the_default(self) -> None:
        """3 presets ask for it. The owner reaffirmed on 2026-08-27 that B --
        the union rule with Studio's existing smoothstep -- is the target, so
        gaussian is a per-preset OPT-IN and never a default.

        U3-G implements it under exactly that constraint, and this test now
        guards the constraint rather than the absence. `profileFor` hands back
        the gaussian only to a tip that names it; everything else, including an
        unrecognised name, gets the smoothstep. The behaviour itself lives in
        `test_u3g_gaussian.py`."""

        code = code_of(COVERAGE)
        self.assertIn('falloff === "gaussian" ? GAUSSIAN : SMOOTHSTEP', code)
        #: The default is reached by falling through, not by naming "default" --
        #: so a preset with no falloff field, or a misspelt one, cannot land on
        #: the bell.
        self.assertEqual(1, code.count("function profileFor("))

    def test_scatter_routes_but_does_not_scatter(self) -> None:
        """`rendererFor` sends it to the stamp renderer, which then places one
        dab per mark exactly where the sampler put it. The routing is honest;
        the placement is not implemented."""

        r = probe()["routing"]
        self.assertEqual(r["STAMP"], r["scatter"])
        self.assertEqual(shape("scatter")["painted"], shape("round")["painted"])


#: U3-D. `DensityMeansTwoDifferentThingsTests` lived here and RECORDED the
#: divergence rather than fixing it -- V2 spent density on the accumulation
#: count, Legacy stippled. It is fixed now, so the subject moved from "a
#: difference we have written down" to "a feature with an implementation", and
#: the tests moved with it to `test_u3d_density.py`.

class TheHarnessAsksForPixelsTests(unittest.TestCase):
    """The instrument that produces the owner's evidence.

    `applyBrushPreset` writes `brushSizeMode` from the preset, unconditionally,
    and every shipping preset leaves it `"relative"` -- so a PIXEL COUNT
    assigned to `brushSize` afterwards is read through
    `(v^1.5 / 10) / 100 * min(W, H)` instead.

    The 2026-08-27 session shipped sixteen gallery cells that each measured
    1024x1024 at fill 1.0000 and peak 255. They were not fabricated and the
    reader was not broken: the gallery asked for a 174 px brush and the engine
    used 9401, more than twice the width of the whole document, so every cell
    was one saturated block and the sixteen numbers agreed with each other
    because none of them could have been anything else. The taps asked for 84
    and got 592; the four smoothing bands asked for 49 and got 1405.

    A measurement that cannot vary reads as evidence, which is what makes this
    worth a guard rather than a comment. Every other probe in that directory
    sets the mode; these three were the only callers that did not.
    """

    def test_the_size_helper_sets_the_mode_before_the_number(self) -> None:
        code = code_of(HARNESS_TIPS)
        self.assertIn('S.brushSizeMode = "document_pixels";', code)
        mode = code.index('S.brushSizeMode = "document_pixels"')
        size = code.index("S.brushSize = px", mode)
        self.assertLess(mode, size)

    def test_no_run_sets_a_size_outside_the_helper(self) -> None:
        """One helper, so there is one place to be right. A second bare
        `S.brushSize = ...` is the fault coming back."""

        code = code_of(HARNESS_TIPS)
        self.assertEqual(1, code.count("S.brushSize ="))

    def test_every_run_reports_what_the_engine_actually_used(self) -> None:
        """`askedPx` next to `brushPx` in the record. The gallery, the taps and
        the smoothing bands each carry it, so a disagreement between what was
        requested and what was rendered is visible in the file rather than
        needing to be re-derived from the document size."""

        code = code_of(HARNESS_TIPS)
        #: One definition plus one call from each of the three runs -- the
        #: gallery, the taps and the smoothing sweep. Written as the sum so a
        #: run that stops calling it fails here rather than quietly dropping to
        #: three and still matching a bare count.
        self.assertEqual(1 + 3, code.count("sizeInPixels("))
        self.assertEqual(1, code.count("function sizeInPixels("))
        self.assertIn("brushPx: cellPx", code)
        self.assertIn("brushPx: tapPx", code)
        self.assertIn("band.brushPx = bandPx", code)


class SuiteIntegrityTests(unittest.TestCase):

    def test_every_test_in_this_module_is_counted(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_U3TF_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
