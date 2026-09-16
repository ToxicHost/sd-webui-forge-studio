"""Brush V2 coverage semantics. V2-05, spec BE5.

Overnight handoff §4.5 names six pieces of evidence; each has a class below.

THE FIXTURE THAT MATTERS IS THE WORKED AREA. BE17 put it in one sentence and it
governs this whole suite: "A SINGLE PASS CANNOT TELL FLOW FROM OPACITY, AND MUST
NOT. Flow is what one pass deposits; Opacity is what the stroke may reach. On a
stroke that never touches itself those are the same number by construction. The
discriminator is OVERLAP, and a guard that expected a straight line to separate
them would be asserting its own misunderstanding."

So the Flow/Opacity guards work an area eight times and the single-pass case is
asserted to be CLOSE rather than different -- which is the correct result and the
one a careless suite would call a failure.

Review: `Evidence/source-review/V2-05-coverage-semantics.md`.
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

EXPECTED_V2_COVERAGE_TESTS = 35

PROBE = Path(__file__).with_name("v2_05_probe.js")
MODULE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"
INDEX_HTML = APP_ROOT / "forge_studio" / "frontend" / "index.html"


def _probe() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)], cwd=str(APP_ROOT), capture_output=True,
        text=True, check=False, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"the probe did not run:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


class _Probed(unittest.TestCase):
    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _probe()


class BothRenderersProduceCoverageTests(_Probed):
    """§4.5 evidence 1 — straight, curved and sharp-turn fixtures."""

    def test_stamping_covers_all_three_fixtures(self) -> None:
        r = self.report["reference"]
        for key in ("stampStraight", "stampCurve", "stampSharp"):
            with self.subTest(fixture=key):
                self.assertGreater(r[key]["painted"], 1000)
                self.assertGreater(r[key]["total"], 100000)

    def test_sweeping_covers_the_ones_it_is_for(self) -> None:
        r = self.report["reference"]
        for key in ("sweepStraight", "sweepSharp"):
            with self.subTest(fixture=key):
                self.assertGreater(r[key]["painted"], 1000)

    def test_a_sweep_and_a_stamp_train_cover_a_similar_area(self) -> None:
        """Different renderers, same footprint. Coverage totals differ -- a
        sweep deposits once per pixel for a whole segment while a stamp train
        accumulates -- but the painted AREA must agree, or one of them is
        drawing a different shape."""

        r = self.report["reference"]
        for stamped, swept in (("stampStraight", "sweepStraight"),
                               ("stampSharp", "sweepSharp")):
            with self.subTest(pair=stamped):
                ratio = r[swept]["painted"] / r[stamped]["painted"]
                self.assertGreater(ratio, 0.95)
                self.assertLess(ratio, 1.05)


class TheRendererIsChosenByTheTipTests(_Probed):
    """§11.1, and the choice is made in the kernel rather than by a caller.

    DiVerdi §2.6.2 warns that a swept contour with a constant fill "loses the
    natural media quality". So a sweep is offered only where there is no media
    character to lose -- which is a statement about TEXTURE, not about hardness.

    U3-R MOVED THE SOFT CASE. This suite originally read the warning as covering
    soft tips too, and required them to stamp. Measured, that cost 16.6% alpha
    ripple at hardness 0.25 and beads on the CENTRELINE at hardness 0, because
    stamping takes MAX of overlapping falloffs and the max of two smoothsteps
    dips between their centres. `sweep` does not fill a contour: it evaluates
    `shapeAt(distanceToSegment / r)`, which for a round procedural tip is the
    continuous limit of an infinitely dense stamp train -- 0% ripple, and a
    cross-section matching the stamp train to within 5 of 255.

    The warning still holds where it applies, and the three tips below still
    stamp for exactly the reason it gives.
    """

    def test_a_hard_round_tip_sweeps(self) -> None:
        self.assertEqual("sweep", self.report["renderer"]["hardRoundSweeps"])

    def test_a_soft_round_tip_also_sweeps(self) -> None:
        # U3-R. A soft ROUND procedural tip has no texture to lose, and the
        # analytic sweep is what removes the scalloping the owner reported.
        self.assertEqual("sweep", self.report["renderer"]["softStamps"])

    def test_anything_with_media_character_stamps(self) -> None:
        r = self.report["renderer"]
        self.assertEqual("stamp", r["texturedStamps"])
        self.assertEqual("stamp", r["scatterStamps"])
        self.assertEqual("stamp", r["anisotropicStamps"])


class EventRateDoesNotChangeDarknessTests(_Probed):
    """§4.5 evidence 2, §10's "ordinary non-Airbrush darkness MUST NOT increase
    merely because the device reports more events", and §20 Correctness 1 at the
    coverage layer rather than the placement layer."""

    def test_total_coverage_is_identical_at_every_rate(self) -> None:
        """Zero spread, not a tolerance. Placement is rate-invariant (V2-03) and
        deposition is a function of placement, so the coverage must be too."""

        self.assertEqual(0, self.report["frequency"]["spread"])

    def test_the_painted_area_is_identical_too(self) -> None:
        painted = self.report["frequency"]["painted"]
        self.assertEqual([painted[0]] * 4, painted)

    def test_the_rates_actually_painted_something(self) -> None:
        self.assertGreater(self.report["frequency"]["totals"][0], 100000)


class FlowAndOpacityAreDistinctTests(_Probed):
    """§4.5 evidence 3, §10."""

    def test_coverage_carries_no_opacity_at_all(self) -> None:
        """Coverage is INTRINSIC. Opacity is applied at merge and must not
        appear in the buffer -- so the Opacity-35 run's raw coverage is FULL,
        and only the merged result is dark."""

        f = self.report["flowVsOpacity"]
        self.assertEqual(255, f["opacityLowCoverage"])

    def test_a_worked_area_separates_them(self) -> None:
        """The discriminator is OVERLAP. Eight passes over one corridor."""

        f = self.report["flowVsOpacity"]
        self.assertTrue(f["workedAreaSeparates"])
        self.assertGreater(f["flowLowMerged"], f["opacityLowMerged"] * 2)

    def test_a_single_pass_cannot_tell_them_apart_and_must_not(self) -> None:
        """BE17's sentence, asserted as a PASS rather than a failure. On a
        stroke that never works over itself these are the same number by
        construction, and a guard expecting a difference here would be
        asserting its own misunderstanding."""

        f = self.report["flowVsOpacity"]
        self.assertLess(abs(f["onePassFlowLow"] - f["onePassOpacityLow"]), 12)

    def test_buildup_decides_whether_opacity_bounds_the_stroke(self) -> None:
        """Off: target is Flow, merge multiplies by Opacity once, and the stroke
        can never exceed Opacity however long it is worked. On: target is
        Flow x Opacity and Opacity acts as a rate."""

        targets = self.report["model"]["buildupChangesTarget"]
        self.assertEqual([0.5, 0.25], targets)


class SelectionIsAppliedOnceAtMergeTests(_Probed):
    """§4.5 evidence 4, §7.4, and the failure with the sharpest shape.

    Applied per contribution a 50% selection MULTIPLIES: over N overlapping
    dabs it yields 0.5^N, so a worked area inside a soft selection goes black
    at the centre and vanishes at the edge.
    """

    def test_a_fifty_percent_selection_halves_the_result(self) -> None:
        self.assertEqual(127, self.report["selection"]["onceAtMergePeak"])

    def test_applying_it_per_contribution_is_decisively_different(self) -> None:
        s = self.report["selection"]
        self.assertLess(s["perContributionPeak"], s["onceAtMergePeak"] / 4)

    def test_and_it_destroys_most_of_the_mark(self) -> None:
        """Not a subtle shift: the summed coverage collapses by more than an
        order of magnitude."""

        s = self.report["selection"]
        self.assertGreater(s["onceAtMergeSum"], s["perContributionSum"] * 10)


class ThePeakFloorKeepsAHardEdgeTests(_Probed):
    """§4.5 evidence 6, and BE17's measurement.

    Pure accumulation puts the centreline right and the cross-section wrong:
    the number of dabs sweeping a pixel falls off faster at the rim of the band
    than the tip's own profile does. Measured at hardness 1.0, Flow 50:
    127 127 127 127 against 62 91 109 116 -- "a soft shoulder on a brush whose
    whole point is that it has none".
    """

    def test_no_pixel_falls_below_what_one_pass_would_lay(self) -> None:
        """The floor is `max(acc, peak)`, so the accumulator can only ever ADD
        to a single pass. Flow 0.5 means a floor of 127."""

        for value in self.report["peakFloor"]["hard"]:
            self.assertGreaterEqual(value, 127)

    def test_a_hard_tip_has_no_soft_shoulder(self) -> None:
        """The cross-section stays flat across the band. A shoulder would show
        as a large spread between the centre and the rim."""

        hard = self.report["peakFloor"]["hard"]
        self.assertLess(max(hard) - min(hard), 10)

    def test_a_soft_tip_still_has_a_falloff(self) -> None:
        """Calibration: the flatness above must be the tip's hardness, not the
        measurement being blind."""

        soft = self.report["peakFloor"]["soft"]
        self.assertGreater(max(soft) - min(soft), 30)

    def test_accumulation_adds_above_the_floor(self) -> None:
        """"Going over it again makes it darker" -- the accumulator's whole
        purpose. Some pixel must exceed the single-pass value."""

        self.assertGreater(max(self.report["peakFloor"]["hard"]), 127)


class ErasingIsTheSameCoverageTests(_Probed):
    """§12.5: "Every brush MUST be usable as an eraser with the same footprint
    and dynamics." One flag on merge, not a second renderer."""

    def test_erasing_removes_where_the_brush_covered(self) -> None:
        e = self.report["erase"]
        self.assertGreater(e["erasedToZero"], 100)

    def test_it_does_not_erase_everywhere(self) -> None:
        """Calibration: an erase that cleared the whole target would satisfy
        the assertion above."""

        e = self.report["erase"]
        self.assertLess(e["erasedToZero"], 200 * 200 / 2)


class TheModelIsBE17sTests(_Probed):
    """§10 mandates it; these pin the pieces with their derivations."""

    def test_the_profile_mean_matches_its_closed_form(self) -> None:
        """1.0 at hardness 1 -- a flat disc -- and 0.5 at hardness 0. BE17
        calls this "the sanity check for the whole derivation"."""

        m = self.report["model"]
        self.assertEqual(1, m["profileMeanFlat"])
        self.assertEqual(0.5, m["profileMeanSoft"])

    def test_flow_100_accumulates_like_every_other_flow(self) -> None:
        """U3-R2F C2 INVERTED THIS, AND THE OLD ASSERTION WAS THE CREASE.

        It used to read `assertIs(False, ...)`: at Flow 1 one contribution was
        held to deposit everything the pass may, so the accumulator was skipped
        and `peak` -- a MAX over the swept polyline -- was the whole deposition.
        `max(f(d1), f(d2))` is `f(min(d1,d2))`, and the min of two distance
        fields has a gradient discontinuity exactly on the medial axis between
        two arms of one stroke. That is the light crease the owner rejected:
        115,319 seam pixels on their own recorded stroke, a 14.12-level step in
        the displayed red channel.

        The engine already unioned the same geometry when it was painted as two
        contacts -- `commitStroke` composites source-over -- so the short
        circuit made one contact and two contacts disagree about coverage
        physics. Deposition is now ONE model at every flow.

        `NO_ACCUMULATE_ABOVE` is deliberately kept above 1 rather than deleted,
        so this test has a named constant to move and the flow-continuity tests
        around it have something to name."""

        self.assertIs(True, self.report["model"]["flow100Accumulates"])

    def test_a_lower_flow_does_accumulate(self) -> None:
        self.assertIs(True, self.report["model"]["flow35Accumulates"])

    def test_the_overlap_divisor_matches_the_geometry(self) -> None:
        """`K = density * (2 / step) * profileMean`. At step 0.3 and hardness
        0.5 that is 1 * 6.667 * 0.75 = 5.0 -- the number of dabs a pixel sees as
        the band sweeps past it."""

        self.assertAlmostEqual(5.0, self.report["model"]["overlapKAtTightSpacing"],
                               places=4)

    def test_the_per_contribution_flow_is_the_normalised_root(self) -> None:
        """`fEff = 1 - (1 - target)^(1/K)`. At target 0.35 and K 5 that is
        0.0825, which is what makes five overlapping contributions arrive at
        0.35 rather than at five times it."""

        self.assertAlmostEqual(0.082549,
                               self.report["model"]["fEffAtTightSpacing"],
                               places=6)


class TheKernelIsDeterministicTests(_Probed):
    """§13 and §20 Correctness 10."""

    def test_two_runs_of_one_stroke_are_identical(self) -> None:
        self.assertTrue(self.report["determinism"]["identical"])

    def test_the_kernel_uses_no_ambient_randomness(self) -> None:
        """ON THE CODE, NOT THE TEXT. The module names `Math.random` in a
        comment saying it never calls one -- the eighth time this repository
        has hit that trap.

        Randomness must arrive as a seeded stream the caller supplies, or a
        recorded stroke cannot replay byte-identically."""

        self.assertNotIn("Math.random", code_of(MODULE))


class CoverageAndMergeAreSeparateTests(unittest.TestCase):
    """§7.4's boundary, asserted structurally rather than by convention."""

    def test_the_generators_do_not_read_opacity_or_selection(self) -> None:
        """`stamp` and `sweep` produce INTRINSIC coverage. If either could see
        a selection, "applied exactly once at merge" would be unenforceable."""

        code = code_of(MODULE)
        start = code.index("function stamp(")
        end = code.index("function rendererFor(")
        generators = code[start:end]
        for token in ("selection", "opacity", "erase"):
            with self.subTest(token=token):
                self.assertNotIn(token, generators)

    def test_merge_is_the_only_place_selection_appears(self) -> None:
        code = code_of(MODULE)
        merge_start = code.index("function merge(")
        merge_end = code.index("function totalCoverage(")
        before = code[:merge_start]
        self.assertIn("selection", code[merge_start:merge_end])
        # `depositionFor` legitimately reads opacity for the buildup target;
        # selection must appear nowhere before merge.
        self.assertNotIn("selection", before)

    def test_the_kernel_touches_no_canvas(self) -> None:
        code = code_of(MODULE)
        for token in ("getContext", "document.", "ImageData", "putImageData"):
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_the_page_does_not_load_it(self) -> None:
        """CHANGED BY U3, recorded rather than quietly relaxed.

        Until U3 this asserted the page did not load the module at all, which
        was the right invariant while V2 had no consumer. U3 gives it one, so
        the module IS loaded -- and the invariant that now carries the weight
        is that the ADAPTER is off by default, which
        `test_u3_canvas_adapter.py` drives rather than spells.

        The acceptance surface is still not shipped."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("v2/coverage.js", html)
        self.assertNotIn("v2/scratchpad.js", html)

    def test_the_module_still_contains_what_the_guards_are_about(self) -> None:
        code = code_of(MODULE)
        self.assertIn("window.StudioBrushCoverageV2", code)
        self.assertIn("function deposit", code)
        self.assertIn("function merge", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_COVERAGE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
