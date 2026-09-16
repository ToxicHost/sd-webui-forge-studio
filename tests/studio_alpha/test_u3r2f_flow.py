"""U3-R2F F2 — continuous flow across swept segments.

THE HANDOFF'S MECHANISM IS NOT THE ONE THAT MATTERED, and finding that out was
the unit. §3.3 says "each swept segment receives one deposition value, so a
pressure ramp becomes a staircase". True. Removing that staircase -- giving the
sweep both endpoints' depositions and interpolating at each pixel's own position
along the segment -- moved the measured banding from 3.79% to 3.84%.

The cause was in `depositionFor`. It divides the stroke's flow by `overlapK`,
the EXPECTED number of contributions covering a pixel, but the actual number is
an integer and `2r/gap` is not. At radius 27 and an 8.1 px gap the count
alternates between 7 and 8 against an expectation of 6.67, and in the
accumulating branch one extra contribution is one extra multiplication.

Three predictions separated that from the staircase, and all three held:

  1. it must be the BRANCH, not the pressure mode -- at flow 1 the accumulator
     does not run and banding was 0%; at 0.99 it was 0.47%, rising to 6.64% at
     flow 0.3;
  2. the ripple period must FOLLOW the mark gap -- 4 -> 4, 6 -> 6, 8.1 -> 8,
     12 -> 12, 16 -> 16 px;
  3. deposition must therefore depend on how the path was CHOPPED -- the same
     geometric stroke at constant flow 0.5 read mean alpha 127.5 at a 4 px gap
     and 145.5 at 16 px.

The counter-evidence was already in hand before any of this: a CONSTANT-pressure
opacity stroke, which has no staircase to remove, banded at 1.53%.

(3) is the sample-density dependency §14 names, and §14 authorises the repair by
name: "normalise deposition by geometric distance/arc length rather than by the
number of browser events". A segment is now weighted by the arc length it
actually contributes: a pixel `d` from the stroke's axis is under a tip of
radius `r` for a travel of `2*sqrt(r^2 - d^2)`, and this segment covers whatever
part of that window it overlaps. The shares sum to 1 by construction, so the
accumulator telescopes to `target * cov` exactly -- which is what the MAX floor
already says a full pass is worth.

SO F2 IS TWO CHANGES, and the gradient alone would have been invisible.

COST. The arc-length branch adds a `sqrt` and a power per painted pixel where
there were none. The power is tabulated (`unionShare`, worst error 0.0095 of
255) and the projection is computed once instead of twice through two closures;
together those took the accumulating branch from 4.0x to 1.7-1.8x. At full flow
the accumulator does not run and the cost is unchanged at 1.01x.

Review: `Evidence/source-review/U3R2F-tiny-and-flow.md`.
Mechanism: `Evidence/u3r2f-tiny/f2_mechanism_probe.js`.
Cost: `Evidence/u3r2f-tiny/f2_cost_probe.js`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_U3R2F_FLOW_TESTS = 63

PROBE = Path(__file__).with_name("u3r2f_flow_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"
ADAPTER = APP_ROOT / "forge_studio" / "frontend" / "v2" / "canvas-adapter.js"

#: The same fixture, measured on the pre-F2 engine. Not carried over from the
#: handoff: §3.3's 6.1%/6.2% came from a browser session whose fixture is not in
#: the repository, and a repair measured against a number nobody can reproduce
#: is not measured. See `Evidence/u3r2f-tiny/f2_banding_probe.js`, which runs
#: both engines side by side over `git show HEAD:`.
BEFORE_BANDING_PCT = {"none": 0.0, "size": 0.0, "opacity": 4.29, "both": 1.13}
NON_FLOW_BASELINE_PCT = 0.0

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


def by_mode() -> dict:
    return {r["mode"]: r for r in probe()["modes"]}


def by_profile() -> dict:
    return {r["profile"]: r for r in probe()["profiles"]}


class TheBandingGateTests(unittest.TestCase):
    """§16: "no worse than the non-flow baseline plus 1.5 percentage points,
    with an absolute target of ≤4.0% on the saved ramp fixture"."""

    def test_flow_driven_modes_meet_the_absolute_gate(self) -> None:
        for mode in ("opacity", "both"):
            with self.subTest(mode=mode):
                self.assertLessEqual(by_mode()[mode]["bandingPct"], 4.0)

    def test_flow_driven_modes_stay_within_the_baseline_allowance(self) -> None:
        for mode in ("opacity", "both"):
            with self.subTest(mode=mode):
                self.assertLessEqual(by_mode()[mode]["bandingPct"],
                                     NON_FLOW_BASELINE_PCT + 1.5)

    def test_the_non_flow_modes_are_the_baseline_and_did_not_move(self) -> None:
        for mode in ("none", "size"):
            with self.subTest(mode=mode):
                self.assertEqual(NON_FLOW_BASELINE_PCT,
                                 by_mode()[mode]["bandingPct"])

    def test_the_worst_mode_improved_against_the_pre_repair_engine(self) -> None:
        # Read against 4.29%, measured on the same fixture, not against the
        # handoff's 6.1% from a fixture nobody can re-run.
        self.assertLess(by_mode()["opacity"]["bandingPct"],
                        BEFORE_BANDING_PCT["opacity"] / 2)


class NoPeakAlignsWithSegmentBoundariesTests(unittest.TestCase):
    """§16: "no periodic peak may align systematically with segment boundaries".

    The statistic is the residual's mean at pixels within a quarter gap of a
    RECORDED segment endpoint, minus its mean at segment midpoints. Recorded,
    not nominal: the sweep is instrumented, because the nominal spacing and the
    measured gap are not the same number.
    """

    def test_no_mode_leans_on_its_segment_boundaries(self) -> None:
        for mode, row in by_mode().items():
            with self.subTest(mode=mode):
                self.assertLess(abs(row["boundaryMinusMidpoint"]), 1.0)

    def test_the_worst_mode_was_leaning_hard_before(self) -> None:
        # Recorded so the guard reads against the +3.915 it replaced. If this
        # ever becomes true again the repair has been undone.
        self.assertLess(by_mode()["opacity"]["boundaryMinusMidpoint"], 1.0)

    def test_constant_pressure_is_perfectly_flat(self) -> None:
        # The case that disproved §3.3's mechanism: no staircase exists here,
        # and it banded at 1.53% before.
        flat = by_profile()["flat"]
        self.assertEqual(0, flat["bandingPct"])
        self.assertEqual(0, flat["boundaryMinusMidpoint"])

    def test_every_constant_flow_profile_is_flat(self) -> None:
        for name in ("flat", "low", "high"):
            with self.subTest(profile=name):
                self.assertEqual(0, by_profile()[name]["bandingPct"])


class TheGradientIsUsedWhereItShouldBeTests(unittest.TestCase):
    """§14: `opacity` and `both` use the gradient; `none` and `size` do not
    accidentally gain pressure-flow variation.

    Asserted from what `sweep` was actually CALLED with, not from the source
    text: §25 requires guards to assert behaviour and use.
    """

    def rows(self) -> dict:
        return {r["mode"]: r for r in probe()["gradientUse"]}

    def test_flow_driven_modes_pass_a_gradient_to_every_segment(self) -> None:
        for mode in ("opacity", "both"):
            with self.subTest(mode=mode):
                row = self.rows()[mode]
                self.assertEqual(row["sweeps"], row["withGradient"])

    def test_non_flow_modes_pass_none(self) -> None:
        for mode in ("none", "size"):
            with self.subTest(mode=mode):
                self.assertEqual(0, self.rows()[mode]["withGradient"])

    def test_there_are_segments_to_have_missed(self) -> None:
        # A mode with zero sweeps would satisfy both tests above vacuously.
        for mode, row in self.rows().items():
            with self.subTest(mode=mode):
                self.assertGreater(row["sweeps"], 50)


class ContinuityAtSharedEndpointsTests(unittest.TestCase):
    """§14: "continuity at shared segment endpoints"; "no single-value-per-
    segment staircase"."""

    def test_each_segment_starts_where_the_previous_one_ended(self) -> None:
        for row in probe()["gradientUse"]:
            with self.subTest(mode=row["mode"]):
                self.assertEqual(0, row["endpointDiscontinuities"])

    def test_the_ramp_rises_all_the_way(self) -> None:
        self.assertTrue(probe()["monotonic"]["rampRises"],
                        probe()["monotonic"]["rampBlocks"])

    def test_the_falling_ramp_falls_all_the_way(self) -> None:
        self.assertTrue(probe()["monotonic"]["rampDownFalls"],
                        probe()["monotonic"]["rampDownBlocks"])


class TheFinalEndpointCarriesItsPressureTests(unittest.TestCase):
    """§14: "the final endpoint uses its actual pressure/flow".

    NOT "the last target equals the last pressure": `describeStroke` builds a
    pressure-to-flow rule with a feel curve, so the target is a FUNCTION of the
    pressure. An equality test there asserts the curve is the identity, which it
    is not. Two strokes differing only in their final sample are compared.
    """

    def row(self) -> dict:
        return probe()["finalEndpoint"]

    def test_a_heavier_final_touch_ends_darker(self) -> None:
        self.assertTrue(self.row()["ordered"], self.row())

    def test_the_difference_is_far_more_than_one_flow_bucket(self) -> None:
        self.assertGreater(self.row()["targetSeparation"], 8 * (1 / 256))

    def test_it_reaches_the_committed_pixels(self) -> None:
        # A target that moved but never landed would pass the two above.
        self.assertGreater(self.row()["alphaSeparation"], 20)

    def test_a_lighter_final_touch_does_not_shorten_the_stroke(self) -> None:
        self.assertTrue(self.row()["sameExtent"], self.row())


class WidthAndFlowStayIndependentTests(unittest.TestCase):
    """§16: width-only identical to no-flow in the flow metric; opacity-only
    keeps geometric width constant; both varies the two independently."""

    def row(self) -> dict:
        return probe()["independence"]

    def test_opacity_only_keeps_the_width_constant(self) -> None:
        self.assertEqual(self.row()["noneWidthSpread"],
                         self.row()["opacityWidthSpread"])

    def test_size_only_varies_the_width(self) -> None:
        self.assertGreater(self.row()["sizeWidthSpread"],
                           self.row()["noneWidthSpread"])

    def test_size_only_leaves_the_flow_metric_where_no_pressure_leaves_it(self) -> None:
        self.assertEqual(self.row()["flowNone"], self.row()["flowSize"])

    def test_opacity_only_moves_the_flow_metric(self) -> None:
        self.assertLess(self.row()["flowOpacity"], self.row()["flowNone"])

    def test_both_moves_the_width_and_the_flow(self) -> None:
        self.assertGreater(self.row()["bothWidthSpread"],
                           self.row()["noneWidthSpread"])
        self.assertLess(self.row()["flowBoth"], self.row()["flowNone"])


class TheStaircaseIsGoneTests(unittest.TestCase):
    """§14: "no single-value-per-segment staircase".

    THIS CLASS EXISTS BECAUSE FIVE MUTATIONS PROVED NOTHING WAS WATCHING.
    Reverting the gradient entirely, or averaging it to each segment's midpoint,
    passed every other gate in this file: once the arc-length normalisation is
    in place a flat segment bands only ~2.6 alpha per 8 px step, and a detrend
    window two gaps wide absorbs it.

    So the question is asked directly. Of all the alpha change along the stroke,
    how much happens within a pixel of a segment boundary? A staircase puts
    essentially all of it there; a gradient spreads it, leaving a share near
    three pixels in every `gap`.
    """

    def row(self) -> dict:
        return probe()["staircase"]

    def test_the_change_is_spread_rather_than_stepped(self) -> None:
        r = self.row()
        self.assertLessEqual(r["boundaryShare"], 2 * r["evenShare"], r)

    def test_it_is_close_to_an_even_spread(self) -> None:
        r = self.row()
        self.assertLess(abs(r["boundaryShare"] - r["evenShare"]), 0.15, r)

    def test_there_is_change_to_have_measured(self) -> None:
        # A stroke with no ramp would satisfy the two above vacuously.
        self.assertGreater(self.row()["totalChange"], 20)

    def test_there_are_boundaries_to_have_stepped_at(self) -> None:
        self.assertGreater(self.row()["segments"], 40)


class TheStrokeReachesThePenTests(unittest.TestCase):
    """§14: "the final endpoint uses its actual pressure/flow".

    Also a gap a mutation found: dropping the closing sample shortened every
    stroke by the same amount, so the light-versus-heavy comparison above could
    not see it. This asks the absolute question.
    """

    def test_the_paint_reaches_the_final_pointer_position(self) -> None:
        r = probe()["reachesFinalPosition"]
        self.assertGreaterEqual(r["lastPaintedX"], r["expectedAtLeast"], r)

    def test_the_expectation_is_the_tips_own_radius(self) -> None:
        r = probe()["reachesFinalPosition"]
        self.assertEqual(r["finalSampleX"] + r["radiusPx"] - 2,
                         r["expectedAtLeast"])


class CostDoesNotGrowWithHistoryTests(unittest.TestCase):
    """§17: "interpolation cost is O(affected pixels/segments), never O(stroke
    history)"; "long-stroke growth ≤ 1.15"."""

    def test_a_late_segment_costs_what_an_early_one_did(self) -> None:
        # Measured as a ratio within one run, alternated and taken as medians.
        # A first-versus-last reading of the same buffer gave 1.199 on a quiet
        # machine -- JIT warm-up, which would have been reported against the
        # 1.15 gate as if it meant something.
        self.assertLessEqual(probe()["historyGrowth"]["ratio"], 1.15,
                             probe()["historyGrowth"])

    def test_the_estimator_has_enough_samples_to_mean_anything(self) -> None:
        self.assertGreaterEqual(probe()["historyGrowth"]["batches"], 10)


class TheGradientVariesWithinOneSegmentTests(unittest.TestCase):
    """§14: `flow(t) = interpolate(flow0, flow1, t)`, evaluated at the geometric
    position the coverage uses.

    MEASURED ON ONE SEGMENT, because in a real stroke it is nearly invisible and
    a suite that claimed otherwise would be asserting something it cannot see.
    At the shipping spacing a pixel sits inside about 2r/gap = 6.7 capsules, so
    its alpha is already a union over a 54 px window of the ramp; interpolating
    within the one segment that currently contains it moves that union by about
    15% of one segment's step, under half an alpha level. Two mutations --
    removing the gradient outright, and averaging it to each segment's midpoint
    -- produced BYTE-IDENTICAL fixtures at spacing 0.15 for exactly that reason.

    That is not a reason to skip the contract. It is a reason to measure it
    where it is not averaged away, which is also where it bites in practice: the
    ends of a stroke, an abrupt pressure step, and any brush whose spacing
    approaches its own diameter.
    """

    def row(self) -> dict:
        return probe()["singleSegmentGradient"]

    def test_the_alpha_rises_along_the_segment(self) -> None:
        self.assertTrue(self.row()["rising"], self.row()["profile"])

    def test_it_spans_most_of_the_two_endpoint_flows(self) -> None:
        r = self.row()
        full = r["highTarget"] - r["lowTarget"]
        self.assertGreater(r["span"], full * 0.8, r)

    def test_it_starts_near_the_opening_flow(self) -> None:
        r = self.row()
        self.assertLess(abs(r["startAlpha"] - r["lowTarget"]), 20, r)

    def test_it_ends_near_the_closing_flow(self) -> None:
        r = self.row()
        self.assertLess(abs(r["endAlpha"] - r["highTarget"]), 20, r)

    def test_it_is_not_one_averaged_value(self) -> None:
        # A segment given a single value -- either endpoint's, or their mean --
        # reads flat. This is the assertion those two mutations fail.
        r = self.row()
        self.assertGreater(r["midAlpha"] - r["startAlpha"], 30, r)
        self.assertGreater(r["endAlpha"] - r["midAlpha"], 30, r)


class WorkDoesNotScaleWithTheDocumentTests(unittest.TestCase):
    """§17: "no full-document work".

    A per-segment scan of the whole document is CONSTANT per segment, so neither
    a visited-pixel count nor a growth-over-time ratio can see it: the first does
    not count it and the second sees no growth. What it does is scale with the
    document, which is what this measures.
    """

    def test_a_sixteenfold_area_costs_the_same_per_segment(self) -> None:
        r = probe()["documentScaleCost"]
        self.assertLess(r["ratio2048over512"], 1.5, r)

    def test_the_area_really_does_change_sixteenfold(self) -> None:
        self.assertEqual(16, probe()["documentScaleCost"]["areaRatio"])


class GroupingAndDensityInvarianceTests(unittest.TestCase):
    """§16: "event grouping must not change final pixels outside declared
    rounding tolerance"; §15's "different geometric segment lengths over the
    same continuous path"; §26's stop condition on event-frequency dependence.
    """

    def test_coalesced_grouping_changes_nothing_at_all(self) -> None:
        rows = probe()["grouping"]
        first = rows[0]
        for r in rows[1:]:
            with self.subTest(perEvent=r["perEvent"]):
                self.assertEqual(first["painted"], r["painted"])
                self.assertEqual(first["total"], r["total"])
                self.assertEqual(first["peak"], r["peak"])
                self.assertEqual(first["marks"], r["marks"])

    def test_input_sample_density_changes_nothing(self) -> None:
        rows = probe()["sampleDensity"]
        first = rows[0]
        for r in rows[1:]:
            with self.subTest(samples=r["samples"]):
                self.assertEqual(first["marks"], r["marks"])
                self.assertEqual(first["meanAlpha"], r["meanAlpha"])

    def test_the_grouping_matrix_is_not_trivial(self) -> None:
        self.assertGreaterEqual(len(probe()["grouping"]), 4)


class DepositionDoesNotDependOnTheChoppingTests(unittest.TestCase):
    """The arc-length property, at the kernel where the gap can be set directly.

    Before the repair the same geometric stroke at constant flow 0.5 read mean
    alpha 127.5 at a 4 px gap and 145.5 at 16 px -- a 14% difference from
    nothing but how the path was cut up.
    """

    def rows(self) -> list[dict]:
        return probe()["gapInvariance"]

    def test_every_gap_deposits_the_same_amount(self) -> None:
        means = {r["meanAlpha"] for r in self.rows()}
        self.assertEqual(1, len(means), sorted(means))

    def test_every_gap_lands_on_what_a_full_pass_is_worth(self) -> None:
        for r in self.rows():
            with self.subTest(gap=r["gap"]):
                self.assertLessEqual(abs(r["centreAlpha"] - r["expected"]), 1)

    def test_no_gap_bands_at_all(self) -> None:
        for r in self.rows():
            with self.subTest(gap=r["gap"]):
                self.assertEqual(0, r["bandingAbs"])

    def test_the_gaps_span_a_wide_range(self) -> None:
        gaps = [r["gap"] for r in self.rows()]
        self.assertGreaterEqual(max(gaps) / min(gaps), 4)


class TheAccumulatorTelescopesTests(unittest.TestCase):
    """The property the repair rests on: the shares sum to 1, so a full pass
    lands on `target * cov` whatever the boundaries did."""

    def test_every_flow_lands_on_its_target(self) -> None:
        for r in probe()["telescoping"]:
            with self.subTest(flow=r["flow"]):
                self.assertLessEqual(abs(r["delta"]), 1)

    def test_it_holds_across_the_whole_flow_range(self) -> None:
        flows = [r["flow"] for r in probe()["telescoping"]]
        self.assertLessEqual(min(flows), 0.2)
        self.assertGreaterEqual(max(flows), 0.9)


class EveryPathShapeWorksTests(unittest.TestCase):
    """§15: straight, diagonal, S-curve, circle, zigzag and figure-eight,
    including the self-crossing ones."""

    def rows(self) -> dict:
        return {r["path"]: r for r in probe()["paths"]}

    def test_every_shape_paints(self) -> None:
        for name, r in self.rows().items():
            with self.subTest(path=name):
                self.assertGreater(r["painted"], 1000)
                self.assertGreater(r["peak"], 200)

    def test_every_shape_sweeps_rather_than_stamping(self) -> None:
        for name, r in self.rows().items():
            with self.subTest(path=name):
                self.assertGreaterEqual(r["sweeps"], r["marks"] - 1)

    def test_the_self_crossing_shapes_are_covered(self) -> None:
        self.assertIn("figureEight", self.rows())
        self.assertIn("circle", self.rows())

    def test_curved_paths_carry_the_gradient_too(self) -> None:
        # Not only the straight one: the arc-share model is exact for a straight
        # segment and approximate across a turn, so the curves are where a
        # broken approximation would show.
        for name in ("scurve", "circle", "figureEight"):
            with self.subTest(path=name):
                r = self.rows()[name]
                self.assertGreater(r["withGradient"], r["sweeps"] * 0.9)


class ConstantFlowDidNotRegressTests(unittest.TestCase):
    """§16: "constant-flow rows must not regress R1 soft-ripple results".

    At full flow the accumulator does not run at all, so F2's code is not
    reached and this is a byte-identity claim rather than a tolerance.
    """

    def rows(self) -> list[dict]:
        return probe()["r1Constant"]

    def test_no_hardness_ripples(self) -> None:
        for r in self.rows():
            with self.subTest(hardness=r["hardness"]):
                self.assertEqual(0, r["bandingPct"])

    def test_every_hardness_still_reaches_full_alpha(self) -> None:
        for r in self.rows():
            with self.subTest(hardness=r["hardness"]):
                self.assertGreaterEqual(r["centreAlpha"], 253)

    def test_the_soft_end_is_covered(self) -> None:
        self.assertIn(0, [r["hardness"] for r in self.rows()])


class NoExtraMarksAndBoundedWorkTests(unittest.TestCase):
    """§17: "no additional marks solely to smooth flow"; §26's stop condition
    "F2 requires denser marks rather than continuous deposition"."""

    def test_the_mark_count_is_the_same_in_every_pressure_mode(self) -> None:
        counts = {r["marks"] for r in probe()["markCount"]}
        self.assertEqual(1, len(counts), probe()["markCount"])

    def test_work_stays_bounded_to_the_stroke(self) -> None:
        b = probe()["bounded"]
        self.assertLess(b["area"], b["documentPixels"] * 0.25)

    def test_the_dirty_region_is_not_the_whole_document(self) -> None:
        b = probe()["bounded"]
        self.assertGreater(b["dirty"]["y0"], 0)


class TheSeamContractTests(unittest.TestCase):
    """Labelled spelling checks alongside the behaviour above."""

    def setUp(self) -> None:
        self.coverage = code_of(COVERAGE)
        self.adapter = code_of(ADAPTER)

    def test_the_sweep_takes_a_second_deposition(self) -> None:
        """`depTo` is the far end of the flow gradient, and it must be a
        parameter rather than something the sweep re-derives.

        PINS THE PARAMETERS, NOT THE ARITY -- the closing paren is deliberately
        absent. This asserted the whole signature and U3-J broke it by APPENDING
        `dabIndex`, which is neither a deposition nor a gradient: the density
        stipple needs to know which dab is asking. That is the third time a
        signature pin in this class has fired on a widening it does not exist to
        catch (see `test_the_stamp_did_not_gain_a_gradient`, whose own docstring
        records the first). Six named parameters, in order, is the contract."""

        self.assertIn("function sweep(buffer, from, to, radiusPx, dep, depTo",
                      self.coverage)

    def test_the_adapter_passes_both_ends(self) -> None:
        """The whole call up to the two depositions, so the near end really is
        `st.lastDep || dep` and the far end really is `dep`.

        STRONGER THAN THE `st.lastDep || dep, dep)` IT REPLACES on everything
        except the trailing paren: it now anchors the buffer, both marks and the
        radius too, so the pair cannot be read off some other call site."""

        self.assertIn(
            "V.sweep(st.buffer, st.lastMark, mark, r, st.lastDep || dep, dep",
            self.adapter)

    def test_the_stamp_did_not_gain_a_gradient(self) -> None:
        """F2's objective is swept segments (§13). Widening it to the stamp
        train would be a different unit and a different overlap model.

        NAMES WHAT IT FORBIDS RATHER THAN PINNING THE SIGNATURE. This used to
        assert the exact text `function stamp(buffer, mark, radiusPx, dep)`, so
        U3-TF's `tipAngle` -- a per-mark ANGLE for a tip that follows the
        stroke, not a second deposition -- read as a gradient leak. A guard that
        fires on any widening cannot distinguish the thing it exists to catch
        from the thing it does not.

        AND THE REWRITE WAS ONLY HALF-APPLIED. The `assertNotIn` below is the
        guard that docstring describes; the `assertIn` under it went on pinning
        the exact signature anyway, so U3-J's `dabIndex` -- a per-mark INDEX for
        the density stipple's draw, not a second deposition -- fired it for the
        same reason `tipAngle` had. The paren is gone now: five named
        parameters, in order, and nothing said about what may follow.
        """

        body = self.coverage[self.coverage.index("function stamp("):]
        body = body[:body.index("\nfunction ")]
        #: The forbidden thing, by name: a second deposition to interpolate to.
        self.assertNotIn("depTo", body)
        self.assertIn("function stamp(buffer, mark, radiusPx, dep, tipAngle",
                      self.coverage)

    def test_the_accumulator_has_one_implementation(self) -> None:
        # `deposit` delegates, so the ordinary and the arc-length paths cannot
        # drift apart.
        self.assertEqual(1, self.coverage.count("function depositWith("))
        self.assertIn("buffer.acc[i] = next > 65535 ? 65535 : next;",
                      self.coverage)

    def test_the_power_is_tabulated_rather_than_called_per_pixel(self) -> None:
        """Both tables, and the two live readers that make them worth having.

        This used to assert `function unionShare(` as well. That function was
        the accumulator C2 replaced and it had no caller left in the whole
        frontend, so the assertion pinned a NAME rather than the behaviour the
        test is named for -- the same fault this suite has caught elsewhere.
        `NEGLOG` and `EXPN` are still live: `negLogOf` reads the first and the
        sweep's optical-depth weight reads the second, which is why removing
        the dead function did not take the tables with it."""

        self.assertIn("NEGLOG", self.coverage)
        self.assertIn("EXPN", self.coverage)
        self.assertIn("function negLogOf(", self.coverage)
        self.assertNotIn("function unionShare(", self.coverage)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3R2F_FLOW_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
