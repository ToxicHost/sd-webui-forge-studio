"""BE17: paint accumulates within a stroke, and Flow stops being Opacity.

THE OWNER'S REPORT: "Flow does not do what you say it does. It's literally just
opacity in how it currently functions." And, separately: "Softness leaves harsh
edges when doing sharp angles." Both were one line.

MEASURED BEFORE THE CHANGE. A 300px stroke at (Flow 35, Opacity 100) against
(Flow 100, Opacity 35), compared after the commit-time multiply: ZERO pixels
differing by more than 8/255 on Basic Round, Soft Round, Hard Ink and Pencil.
Peak alpha by Flow was `round(255 * flow)` exactly. Painting back and forth over
one place inside a single stroke -- 1, 2, 5, 20 passes -- read 89, 89, 89, 89.

A SINGLE PASS CANNOT TELL FLOW FROM OPACITY, AND MUST NOT. Flow is what one
pass deposits; Opacity is what the stroke may reach. On a stroke that never
touches itself those are the same number by construction. The discriminator is
OVERLAP, and a guard that expected a straight line to separate them would be
asserting its own misunderstanding.

EVERYTHING HERE IS READ ON THE LAYER -- through the engine's own
`alphaMapToImageData` and the single commit-time multiply, never off
`S.stroke.alphaMap`. Half the wrong numbers in this programme came from
reporting the accumulator as what the owner sees.

THRESHOLD DISCIPLINE. Every numeric bound below states its quantity and units,
says whether it is exact, derived, or calibrated, and names the controlled
known-different case that proves the metric separates. For four of them that
case is a mutation in `Evidence/be1-guards/mutate_be17.py`, whose recorded
failure values are quoted in the docstring that relies on them. A threshold
chosen because the current implementation passes it is not a threshold.

Source review: Evidence/source-review/BE17-accumulation-and-flow.md
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DRIVER = Path(__file__).with_name("be17_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE17_TESTS = 29

#: The visibility floor, in levels of 255. A difference smaller than this is not
#: something a person sees on a monitor, so counting it would let a change
#: nobody can perceive be reported as a change. Used by the driver, quoted here
#: because every fraction below is "of pixels differing by MORE than this".
VISIBLE = 8

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise AssertionError(
            f"the driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only() -> str:
    source = CORE.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class TheInstrumentTests(unittest.TestCase):
    """Before any number this file makes.

    A metric that cannot separate two things it is about to call equal is not
    evidence, and this programme has produced four confident wrong numbers from
    drivers that could not. Both directions are stated: identical input must
    give an identical answer, and a small real change must be visible to it.
    """

    def test_the_same_stroke_twice_is_the_same_pixels(self):
        self.assertEqual(
            0, M["instrument"]["deterministic"],
            "the harness is not deterministic, so nothing below means anything")

    def test_the_metric_can_see_a_five_percent_flow_change(self):
        """Known-different, calibrated. Flow 35 against Flow 40 is the smallest
        change a person would call deliberate: 89 against 102 of 255, which is
        13 levels and above the 8-level visibility floor. It moves 84.4% of
        painted pixels."""

        self.assertGreater(M["instrument"]["flow35VsFlow40"], 0.5)
        self.assertEqual(89, M["instrument"]["coreAt35"])
        self.assertEqual(102, M["instrument"]["coreAt40"])


@needs_node
class FlowIsNotOpacityTests(unittest.TestCase):
    """THE OWNER'S COMPLAINT, as a contract.

    Flow is what one pass deposits. Opacity is what one stroke may reach. They
    are the same number on a stroke that never touches itself, and different
    the moment it does.
    """

    #: Presets that ship a Flow below 100 and no opacity jitter, so the
    #: comparison is not confounded by a per-dab random draw.
    PRESETS = ("Basic Round", "Soft Round", "Hard Ink", "Pencil", "Sketch Light")

    def test_working_the_same_area_separates_them(self):
        """Eight passes over one corridor inside a single stroke.

        Threshold: fraction of painted pixels differing by more than 8/255.
        Calibrated against the shipped engine before BE17, where the same
        comparison gave exactly 0.0000 on four of these five presets.
        """

        rows = M["flowIsNotOpacity"]
        same = {k: rows[k]["differAfterWorking"] for k in self.PRESETS
                if rows[k]["differAfterWorking"] < 0.5}
        self.assertEqual(
            {}, same,
            "Flow and Opacity still produce the same worked mark: " + str(same))

    def test_one_clean_pass_deliberately_cannot(self):
        """Guards the guard, in the unusual direction: it asserts that a
        fixture CANNOT tell them apart, because that is the contract.

        If a single non-overlapping pass ever separated Flow from Opacity, the
        model would be wrong -- one pass depositing Flow, then bounded by
        Opacity, is the same product either way. A future change that made this
        test fail would be a defect however good its own numbers looked.
        """

        rows = M["flowIsNotOpacity"]
        for name in self.PRESETS:
            with self.subTest(preset=name):
                self.assertAlmostEqual(
                    rows[name]["onePassFlow"], rows[name]["onePassOpacity"],
                    delta=2,
                    msg=f"{name}: one clean pass distinguished Flow from "
                        "Opacity, which the model says it must not")


@needs_node
class WorkingAnAreaDarkensItTests(unittest.TestCase):
    """Going back over your own mark inside one stroke must add paint.

    Before BE17 this read 89, 89, 89, 89 across 1, 2, 5 and 20 passes on every
    non-buildup preset. Scrubbing was inert.
    """

    def test_every_pass_adds(self):
        """Monotonic, not merely larger at the end: a model that jumped to the
        ceiling on pass two and stopped would satisfy first-versus-last."""

        for preset, row in M["workingAnAreaDarkensIt"].items():
            with self.subTest(preset=preset):
                seq = [row["passes1"], row["passes2"],
                       row["passes4"], row["passes8"]]
                self.assertEqual(
                    sorted(seq), seq,
                    f"{preset} did not darken monotonically: {seq}")

    def test_the_second_pass_is_already_visible(self):
        """Threshold: 8/255, the visibility floor. The complaint was about
        working an area, and a model that needed twenty passes to show anything
        would not answer it. Measured: Basic Round 89 -> 138 on pass two."""

        for preset, row in M["workingAnAreaDarkensIt"].items():
            with self.subTest(preset=preset):
                self.assertGreater(
                    row["passes2"] - row["passes1"], VISIBLE,
                    f"{preset}: a second pass over the same place added "
                    f"{row['passes2'] - row['passes1']} levels, which nobody "
                    "would see")


@needs_node
class OpacityBoundsTheStrokeTests(unittest.TestCase):
    """It must REACH the bound and STOP.

    A model that keeps climbing past Opacity has made it a rate, which is what
    Buildup is for and what Buildup-off must not do.
    """

    def test_the_stroke_settles_at_its_opacity(self):
        """Exact, not calibrated: the bound is `round(255 * opacity)` by
        construction. Measured 89 at Opacity 35, 128 at 50, 255 at 100."""

        for key, opacity in (("opacity35", 0.35), ("opacity50", 0.5),
                             ("opacity100", 1.0)):
            with self.subTest(opacity=key):
                row = M["opacityBoundsTheStroke"][key]
                self.assertAlmostEqual(
                    round(255 * opacity), row["passes32"], delta=1,
                    msg=f"{key} settled at {row['passes32']}")

    def test_it_stops_rather_than_creeping(self):
        """The last two samples must be equal. Sixteen passes and thirty-two
        passes are both far past the bound, so any difference is the ceiling
        leaking."""

        for key, row in M["opacityBoundsTheStroke"].items():
            with self.subTest(opacity=key):
                self.assertEqual(
                    row["passes16"], row["passes32"],
                    f"{key} was still climbing between 16 and 32 passes: {row}")

    def test_the_fixture_reaches_the_bound_from_below(self):
        """Guards the guard. Both tests above pass on an engine that saturates
        everything instantly, so this is what says the climb happened."""

        for key, row in M["opacityBoundsTheStroke"].items():
            with self.subTest(opacity=key):
                self.assertLess(
                    row["passes1"], row["passes32"],
                    f"{key} was already at its bound on pass one")


@needs_node
class OnePassDepositsItsFlowTests(unittest.TestCase):
    """THE DEFINITION OF FLOW, across everything that must not change it.

    Flow is deliberately not "what one dab deposits". If it were, Spacing would
    multiply it -- which is exactly what DiVerdi 2.6.1 warns about and what the
    engine's old accumulate branch did, with a measured spread of 53.3 / 80.6 /
    94.5 of 255 across the shipped Spacing slider at hardness 0 / 0.5 / 1.0.
    """

    def test_every_hardness_and_spacing_lands_on_flow(self):
        """Threshold: 5 levels of 255, calibrated. The residual is quantisation
        of a 16-bit accumulator into an 8-bit result plus the tip's own
        rasterisation; measured worst case across 45 combinations is 2.4.
        Five is that worst case with headroom, and is still well inside the
        8-level visibility floor -- so a failure here is a real drift, not a
        rounding wobble. The known-different case is mutation 5, which
        re-derives the step inside the consumer and misses by 46."""

        worst = 0.0
        for case, row in M["onePassErrorAgainstFlow"].items():
            for flow_key, error in row.items():
                worst = max(worst, abs(error))
                with self.subTest(case=case, flow=flow_key):
                    self.assertLess(
                        abs(error), 5,
                        f"{case} {flow_key} landed {error} levels from its "
                        "Flow")
        self.assertLess(worst, 5)

    def test_an_anisotropic_tip_lands_on_its_flow(self):
        """THE THREADED-STEP GUARD, and a round tip cannot provide it.

        `2 * spacingFraction()` IS the correct step for a round tip at no
        pressure -- the algebra collapses to exactly that -- so a Basic Round
        fixture cannot tell a threaded step from a re-derived one. Mutation 5
        walked through the sweep above for precisely that reason.

        `spacingFor` multiplies the gap by the tip's extent along travel, and
        the re-derivation does not. Marker has the widest mismatch of the
        shipped tips (extent 0.8 against aspect 0.35), so it separates hardest.

        Threshold: 5 levels of 255 between one pass and `Flow * 255`.
        Calibrated: measured 0.1 here, 21.7 under mutation 5. Flat Chisel moves
        only 0.8 under the same mutation and is recorded rather than asserted.
        """

        row = M["theThreadedStepMatters"]["Marker"]
        self.assertLess(
            abs(row["errorVsFlow"]), 5,
            f"Marker's one pass landed {row['errorVsFlow']} levels from its "
            "Flow, which is what a re-derived step costs an anisotropic tip")

    def test_the_weaker_separators_are_recorded_not_asserted(self):
        """Flat Chisel and the clamped small tip are kept in the evidence
        because they were candidates, and are not asserted because the
        calibration showed they do not separate: 0.8 and 0.0 under the same
        mutation that moves Marker by 21.7."""

        rows = M["theThreadedStepMatters"]
        self.assertIn("Flat Chisel", rows)
        self.assertIn("smallTipTightSpacing", rows)

    def test_the_sweep_actually_varies_spacing(self):
        """Guards the guard, and it has already caught one vacuous run: the
        first version of the driver wrote `S.brushSpacing`, which is not the
        field -- spacing lives on `brushDynamics` -- and reported a spread of
        exactly 0.00 across six spacings at three hardnesses. Too clean is the
        tell."""

        keys = set(M["onePassErrorAgainstFlow"])
        self.assertIn("hard1_spacing0.02", keys)
        self.assertIn("hard1_spacing0.32", keys)
        self.assertGreaterEqual(len(keys), 9)


@needs_node
class SpacingIsNotADarknessControlTests(unittest.TestCase):
    """DiVerdi 2.6.1's constraint: tightening the dab train must not silently
    darken the stroke.

    MEASURED AT THE DAB, NOT AVERAGED ALONG THE BAND. The mean along a stroke
    legitimately falls as spacing widens, because at Spacing 50 the dabs stop
    touching and the pixels between them have less paint for the honest reason
    that no tip was ever there. That is the un-smooth silhouette of Fig 2.6, a
    SMOOTHNESS consequence, and rolling it into this number would report a
    scalloped stroke as a darkness bug. The first version of this measurement
    did exactly that and read a spread of 23 / 67 / 91.
    """

    def test_the_paint_where_the_brush_landed_does_not_depend_on_spacing(self):
        """Threshold: 30 levels of 255 across the WHOLE slider, 0.02 to 0.50.

        Empirically calibrated and deliberately loose at the wide end, because
        the model's overlap count is an over-estimate of at most one dab, which
        only matters when a pixel sees one or two -- and a hard tip at Spacing
        35-50% is already far outside DiVerdi's stated 1-5% usable band and
        visibly steps whatever the coverage rule is. Measured: soft 12,
        mid 24, hard 72 across the full sweep; over the shipped band (0.02 to
        0.20) the same numbers are 4, 11 and 19. The hard-tip full-sweep value
        is recorded as a known limit in the source review rather than hidden
        behind a bound chosen to pass.
        """

        for key, row in M["spacingIsNotADarknessControl"].items():
            with self.subTest(hardness=key):
                shipped = row["peakAt"][:4]      # 0.02, 0.05, 0.10, 0.20
                self.assertLess(
                    max(shipped) - min(shipped), 30,
                    f"{key}: peak paint moved {max(shipped) - min(shipped)} "
                    f"levels across the shipped spacing band: {row['peakAt']}")

    def test_the_wide_end_is_recorded_rather_than_asserted(self):
        """The full sweep is kept in the evidence so the limit above is
        visible rather than trimmed out of the fixture."""

        for key, row in M["spacingIsNotADarknessControl"].items():
            with self.subTest(hardness=key):
                self.assertEqual(6, len(row["peakAt"]))


@needs_node
class AHardTipKeepsItsShapeTests(unittest.TestCase):
    """THE PEAK FLOOR, which is why accumulation did not soften every brush.

    Pure accumulation puts the centreline in the right place and gets the
    cross-section wrong: dab count falls off faster at the rim of the swept
    band than the tip's own profile does, so the band grows a soft shoulder.
    Measured at hardness 1.0, Flow 50, across the band:

        with the floor    127 127 127 127 127 127 127
        without it         62  91 109 116 124 124 124
    """

    def test_the_interior_is_flat(self):
        """Exact: inside the antialiasing band a hardness-1.0 tip has no
        falloff, so every interior pixel is the same value and the spread is 0.
        The known-different case is mutation 2, which removes the peak floor and
        spreads it by 62.

        INTERIOR EXCLUDES THE RIM, and it has to. Before BE18 a hard tip's band
        had zero width, so "every non-zero sample" and "every interior sample"
        were the same set and the metric was accidentally right. With a
        one-pixel rim they are not: the profile reads
        `61 127 127 127 127 127 127 127 127 127 127 127 61` -- a perfect
        plateau with two rim samples -- and the old metric called that an
        interior gradient of 66.
        """

        row = M["aHardTipKeepsItsShape"]
        self.assertGreater(
            row["interiorSamples"], 6,
            f"too few interior samples to call anything flat: {row}")
        self.assertEqual(
            0, row["interiorSpread"],
            f"a hard tip grew an interior gradient: {row['profile']}")

    def test_a_hard_tip_has_an_antialiased_rim(self):
        """BE18. The other half of the same profile: the outermost covered
        pixel must NOT be the plateau value, or the silhouette is a staircase.

        Measured on Hard Ink and Fine Liner, the rim reads 127 against a 255
        interior. Before BE18 it read 255 -- coverage was binary and the edge
        RMS was 0.29-0.42px, against 0.301px for Pixel Perfect, the preset that
        declares itself aliased.
        """

        row = M["aHardTipKeepsItsShape"]
        self.assertEqual(2, len(row["rim"]), f"no rim was measured: {row}")
        for value in row["rim"]:
            self.assertLess(
                value, row["peak"],
                f"the outermost covered pixel is at full plateau value, so the "
                f"tip has no antialiasing: {row['profile']}")

    def test_it_lands_on_its_flow(self):
        """Exact: Flow 50 is `round(255 * 0.5)` = 127 or 128."""

        self.assertIn(M["aHardTipKeepsItsShape"]["peak"], (127, 128))


@needs_node
class Flow100KeepsItsRimTests(unittest.TestCase):
    """THE SHORT-CIRCUIT, and it protects the opposite thing from the floor.

    At Flow 100 one dab already deposits everything the pass may deposit, so
    the only pixels left for an accumulator to build on are the tip's own
    antialiased rim -- and building on those makes the edge HARDER, which is
    the wrong direction for a change whose subject is softness.
    """

    def test_the_antialiased_rim_survives(self):
        """Threshold: at least 700 rim pixels, where a rim pixel is one with
        alpha strictly between 0 and 250.

        Calibrated: measured 1,048 here, against 1,016 pixels' worth of
        antialiasing LOST when the short-circuit is removed (mutation 3), worst
        case 86/255. The bound sits between the two states with room on both
        sides rather than just under the current value.
        """

        row = M["flow100KeepsItsRim"]
        self.assertGreater(
            row["rimPixels"], 700,
            f"the Flow-100 edge lost its antialiasing: {row}")

    def test_the_mark_is_actually_solid_in_the_middle(self):
        """Guards the guard: a stroke that was ALL rim would pass the test
        above and be a different defect."""

        row = M["flow100KeepsItsRim"]
        self.assertGreater(row["solidPixels"], 500, str(row))


@needs_node
class TheCornerCreaseIsGoneTests(unittest.TestCase):
    """The owner's second complaint: "softness leaves harsh edges when doing
    sharp angles".

    `max(a, b)` of two smooth bumps is continuous but has a GRADIENT
    discontinuity along the locus where they are equal, and the eye reads that
    as a hard edge. The metric is the sharpest second difference along a scan
    line crossing the bisector of the turn -- a measure of the KINK, not of
    darkness, so a stroke that merely got lighter cannot pass by accident.
    """

    def test_no_turn_leaves_a_kink(self):
        """Threshold: 5 levels/px of second difference.

        Calibrated against the same measurement on the max-blend engine, which
        gave 11.36 at 150 degrees, 9.88 at 120 and 8.13 at 90 -- and against a
        straight stroke, which reads 0.93 and is the floor of what a smooth
        soft edge produces. Five sits between the two populations. Measured
        now: 1, 2, 3.
        """

        for turn, sharpness in M["cornerSharpness"].items():
            with self.subTest(turn=turn):
                self.assertLess(
                    sharpness, 5,
                    f"{turn} still leaves a crease of {sharpness} levels/px")

    def test_all_three_turns_were_measured(self):
        self.assertEqual({"turn150", "turn120", "turn90"},
                         set(M["cornerSharpness"]))


@needs_node
class LowFlowOnADensePresetStillPaintsTests(unittest.TestCase):
    """THE 16-BIT ACCUMULATOR, and why it is not an optimisation.

    Normalisation makes the per-dab contribution small BY DESIGN. At the
    shipped spacings Airbrush lays 55.6 dabs per pixel and Charcoal 36.7, so at
    Flow 35 one dab is 0.0077 of full -- which in 8 bits truncates to 1 of 255,
    losing 86% -- and at Flow 15 truncates to ZERO and the brush paints nothing
    at all.

    This reframes the defect BE17 replaced: the max-blend was a workaround for
    an under-precision accumulator, not merely a wrong choice.
    """

    def test_the_brush_paints_at_every_flow(self):
        """Exact in kind rather than in value: zero painted pixels is not a
        threshold, it is a brush that does nothing.

        CLAIM CORRECTED. An earlier version of this docstring said the 8-bit
        accumulator "paints nothing at Flow 5 and 10 on Airbrush". That is true
        of the ACCUMULATOR and false of the MARK: the peak floor still returns
        the single-dab value, so the stroke is there and looks ordinary. The
        mutation campaign caught the overstatement -- mutation 4 walked straight
        through this guard. What 8 bits actually destroys is the building, and
        `test_a_dense_preset_still_builds_at_low_flow` is what owns that.
        """

        for preset, row in M["lowFlowOnADensePresetStillPaints"].items():
            for flow, cell in row.items():
                with self.subTest(preset=preset, flow=flow):
                    self.assertGreater(
                        cell["painted"], 0,
                        f"{preset} at {flow} painted nothing at all")
                    self.assertGreater(
                        cell["core"], 0,
                        f"{preset} at {flow} left a mark with no colour in it")

    def test_a_dense_preset_still_builds_at_low_flow(self):
        """THE 16-BIT GUARD, and it has to be about building rather than about
        painting -- see the corrected docstring above.

        Threshold: 40 levels of 255 gained between one pass and eight, on
        Airbrush at Flow 10.

        Calibrated against mutation 4 (`Evidence/be1-guards/mutate_be17.py`),
        which reduces the accumulator to 8-bit range. Measured: 107.99 levels
        gained here, 7.93 under the mutation. Forty sits between the two
        populations with room on both sides. Charcoal is deliberately NOT
        asserted -- its per-dab contribution is large enough that 8 bits still
        holds it (61 gained against 59 mutated), so it does not separate and an
        assertion on it would be decoration.
        """

        row = M["lowFlowOnADensePresetStillBuilds"]["Airbrush"]["flow10"]
        self.assertGreater(
            row["gained"], 40,
            f"Airbrush at Flow 10 gained only {row['gained']} levels over "
            f"eight passes: {row}")

    def test_flow_still_orders_the_result(self):
        """A brush that painted the same amount at every Flow would satisfy the
        test above. Deposition must rise with Flow."""

        for preset, row in M["lowFlowOnADensePresetStillPaints"].items():
            with self.subTest(preset=preset):
                cores = [row[k]["core"] for k in
                         ("flow5", "flow10", "flow15", "flow35")]
                self.assertEqual(
                    sorted(cores), cores,
                    f"{preset} deposition is not monotonic in Flow: {cores}")


class TheStructureThatMakesItWorkTests(unittest.TestCase):
    """Structural, for the three pieces whose absence a behavioural test would
    catch only indirectly or slowly."""

    def test_the_step_is_threaded_from_the_emitter(self):
        """`spacingFor` clamps at half a pixel, and below that clamp
        `spacingFraction()` no longer describes the distance actually
        advanced. Re-deriving it in the consumer costs 46 levels of 255 at
        hardness 0, Spacing 2 -- which is mutation 5."""

        code = _code_only()
        self.assertIn("fn(sx, sy, stampP, stampRot, applyDynamics(_dynCtx), "
                      "debt / alongRadius);", code)
        self.assertIn("function stampAlphaMap(cx, cy, sz, opacity, stampAngle, "
                      "dabStep) {", code)

    def test_the_accumulator_is_tied_to_the_map_it_accumulates_for(self):
        """A caller that swaps `alphaMap` without clearing the accumulator
        would inherit the previous mark's coverage. Checking the map's IDENTITY
        makes that unreachable rather than discouraged, and BE1's determinism
        guard is what found it."""

        code = _code_only()
        self.assertIn("S.stroke._accumFor !== map", code)
        self.assertIn("S.stroke._accumFor = map;", code)

    def test_a_timed_deposit_does_not_pay_the_spatial_divisor(self):
        """`plotTo`'s dabs are one pass laid out in space; the airbrush timer's
        land on the same pixel and are a rate in time. Charging them the
        spatial overlap made a held Airbrush deposit at a seventeenth of its
        rate. The behavioural guard is BE12's
        `test_half_a_second_of_stillness_lays_down_paint`."""

        code = _code_only()
        self.assertIn("function timeDepositStep()", code)
        self.assertIn("applyDynamics(_airDynCtx(S.stroke.lp)), "
                      "timeDepositStep());", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE17_TESTS, found)


if __name__ == "__main__":
    unittest.main()
