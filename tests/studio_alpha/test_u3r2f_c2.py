"""U3-R2F C2 — same-contact union semantics.

WHAT CHANGED. At Flow 1 the engine skipped its accumulator entirely, leaving
`peak` -- a MAX over the swept polyline -- as the whole deposition. Because
`shapeAt` is non-increasing, `max(f(d1), f(d2))` is `f(min(d1,d2))`, and the min
of two distance fields has a gradient discontinuity exactly on the medial axis
between two arms of one stroke. On the owner's own recorded stroke that measured
115,319 seam pixels, 2.681% of the mark, and a 14.12-level step in the displayed
red channel. Deposition is now optical depth integrated along the path.

WHY THAT FORM AND NOT ANOTHER. Two cheaper candidates were built and measured
first, and both failed for the same structural reason -- a formulation that
partitions one pass among segments must be continuous in the pixel position AND
additive across a segment split:

    coverage from the infinite line   max kink 18 -> 88; a pixel past the stroke
                                      end sits on the line, so the round cap
                                      became a 172 px square extension
    binary segment ownership          max kink 18 -> 160; discontinuous at every
                                      segment boundary

An integral along the path is additive by construction, truncates honestly at a
cap, and contains no max at all.

THE NUMBERS THESE TESTS GUARD, measured on this probe's own fixtures at the
commit that introduced them. They are regression ceilings, not targets:

    90 deg crossing      seam  335   max kink 10
    45 deg crossing      seam  506   max kink 10
    20 deg crossing      seam  825   max kink 31
    one contact vs two   seam   48 against 0, in a disc around the crossing
    straight line        seam    0
    tap                  0 pixels shrink when the pointer then moves

`SuiteIntegrityTests` is last, and its count moves with every added test.
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

EXPECTED_U3R2F_C2_TESTS = 31

PROBE = Path(__file__).with_name("u3r2f_c2_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"

#: Ceilings, from the run that introduced this module. A candidate that makes
#: any of them worse has regressed the repair.
#:
#: RE-BASELINED at U3-TF, and the reason is not "the numbers moved". These
#: fixtures declare `smoothing: 0`, and until U3-TF the adapter hard-coded
#: `MODE_NATURAL` regardless -- so every fixture in this file was drawn through
#: a stabiliser it had explicitly asked not to have. Wiring `S.smoothing`
#: through means the probe now draws the RAW path, which is a different
#: geometry, so the seam is measured on a slightly different figure:
#:
#:     90 deg   seam 335 -> 333   kink 10 -> 11
#:     45 deg   seam 506 -> 475   kink 10 -> 10
#:     20 deg   seam 825 -> 795   kink 31 -> 31
#:
#: Seam AREA fell at every angle and the worst kink moved by one level at one
#: angle. That is a fixture change, not a repair regression, and it is only
#: visible because the ceilings were recorded rather than computed.
SEAM_CEILING = {90: 333, 45: 475, 20: 795}
KINK_CEILING = {90: 11, 45: 10, 20: 31}

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


def crossings() -> dict:
    return {c["deg"]: c for c in probe()["crossings"]}


class TheDepositionModelTests(unittest.TestCase):
    """The branch that produced the crease is gone, and gone for every flow."""

    def test_full_flow_accumulates(self) -> None:
        self.assertTrue(probe()["model"]["flow100Accumulates"])

    def test_reduced_flow_still_accumulates(self) -> None:
        self.assertTrue(probe()["model"]["flow050Accumulates"])

    def test_the_threshold_is_unreachable_rather_than_deleted(self) -> None:
        """Kept as a named constant so this test has something to move and a
        mutation that reinstates the Flow-1 short circuit has a name."""

        self.assertGreater(probe()["model"]["noAccumulateAbove"], 1.0)

    def test_only_a_swept_deposition_builds_the_pass_table(self) -> None:
        """`passTau` is reached from `sweep`'s accumulating branch and nowhere
        else, so a stamp train would build a table it never reads."""

        self.assertTrue(probe()["model"]["sweptHasPassTable"])
        self.assertFalse(probe()["model"]["stampedHasPassTable"])


class TheCreaseIsGoneTests(unittest.TestCase):
    """Crossings at three angles, including the shallow one that is worst."""

    def test_each_crossing_stays_under_its_seam_ceiling(self) -> None:
        for deg, ceiling in SEAM_CEILING.items():
            with self.subTest(deg=deg):
                self.assertLessEqual(crossings()[deg]["seam"], ceiling)

    def test_each_crossing_stays_under_its_kink_ceiling(self) -> None:
        for deg, ceiling in KINK_CEILING.items():
            with self.subTest(deg=deg):
                self.assertLessEqual(crossings()[deg]["maxKink"], ceiling)

    def test_the_shallow_crossing_is_the_hard_one(self) -> None:
        """Recorded because it is the case a future repair must not trade away:
        the medial-axis kink scales with `cos(half-angle)`, so it is WORST when
        the arms are nearly parallel, not when they meet squarely."""

        self.assertGreater(crossings()[20]["seam"], crossings()[90]["seam"])

    def test_the_threshold_is_derived_not_chosen(self) -> None:
        """Twice the larger of the 8-bit rounding floor and the tip profile's
        own curvature. A fixed threshold reports curvature as seam."""

        for deg in SEAM_CEILING:
            with self.subTest(deg=deg):
                self.assertGreater(crossings()[deg]["threshold"], 2.0)


class OneContactAgreesWithTwoTests(unittest.TestCase):
    """§12. At Flow 1 / Opacity 1 the coverage physics in the shared window must
    not depend on how the transaction was split."""

    def test_the_crossing_reaches_the_same_alpha_either_way(self) -> None:
        a = probe()["oneVsTwo"]["alphaAtCrossing"]
        self.assertEqual(a["two"], a["one"])

    def test_two_contacts_have_no_seam_at_all(self) -> None:
        """The control: source-over across contacts is a union and cannot kink.
        If this ever fails the metric is wrong, not the engine."""

        self.assertEqual(0, probe()["oneVsTwo"]["two"]["seam"])

    def test_one_contact_is_within_the_recorded_margin_of_two(self) -> None:
        """MEASURED, NOT CHOSEN, and not byte identity. One contact leaves 48
        seam pixels of 32,681 in the disc where two contacts leave none. The two
        formulations are not algebraically identical -- one integrates a
        continuous path, the other composites two finished planes in 8 bits --
        so a tight ceiling is the honest gate."""

        o = probe()["oneVsTwo"]
        self.assertLessEqual(o["one"]["seam"], 60)
        self.assertLessEqual(o["one"]["seam"] / max(1, o["one"]["painted"]), 0.003)

    def test_both_cover_the_same_area(self) -> None:
        o = probe()["oneVsTwo"]
        self.assertEqual(o["two"]["painted"], o["one"]["painted"])


class CapsAndEndpointsTests(unittest.TestCase):
    """§7. The cap overshoot was the first of the two C2 blockers."""

    def test_no_cap_overshoots_the_body(self) -> None:
        """At `target = 1`, `fEff = 1-(1-1)^(1/K)` collapsed to 1, so the
        opening stamp claimed a whole pass on top of the sweeps -- 121 -> 149 at
        hardness 0, a 23% blob shipping shows at no flow. A stamp has zero arc
        length, so its exposure is now zero and the idempotent `peak` floor
        carries its footprint."""

        for row in probe()["caps"]:
            with self.subTest(size=row["sizePx"], hardness=row["hardness"],
                              flow=row["flow"]):
                body = max(1, row["bodyRim"])
                self.assertLessEqual(row["openingRim"] / body, 1.02)
                self.assertLessEqual(row["closingRim"] / body, 1.02)

    def test_no_cap_undershoots_either(self) -> None:
        """A cap that vanished would be the opposite failure, and the peak floor
        is what prevents it."""

        for row in probe()["caps"]:
            with self.subTest(size=row["sizePx"], hardness=row["hardness"],
                              flow=row["flow"]):
                body = max(1, row["bodyRim"])
                self.assertGreaterEqual(row["openingRim"] / body, 0.98)

    def test_an_exact_tap_paints_one_full_footprint(self) -> None:
        t = probe()["tap"]
        self.assertEqual(1, t["tapMarks"])
        self.assertGreater(t["tapStats"]["painted"], 0)
        self.assertGreaterEqual(t["tapStats"]["peak"], 254)

    def test_a_tap_that_becomes_a_stroke_does_not_shrink(self) -> None:
        """§7 forbids an initially displayed tap shrinking once the pointer
        moves, and forbids fixing it by repainting after release."""

        self.assertEqual(0, probe()["tap"]["pixelsThatShrankOnMove"])
        self.assertEqual(0, probe()["tap"]["worstShrink"])


class NonOverlapIsUntouchedTests(unittest.TestCase):
    """§16 rejects a candidate that changes strokes which never overlap."""

    def test_a_straight_line_has_no_seam(self) -> None:
        self.assertEqual(0, probe()["retrace"]["straight"]["seam"])

    def test_a_retrace_deposits_more_than_one_pass(self) -> None:
        """A retrace IS a second geometric pass, so D1 says it may darken. This
        is the behaviour the owner ruled on, asserted rather than assumed."""

        r = probe()["retrace"]
        self.assertGreater(r["retraced"]["mean"], r["straight"]["mean"])

    def test_a_retrace_covers_no_more_ground(self) -> None:
        """Darker, not fatter. A repair that widened the mark would show here."""

        r = probe()["retrace"]
        self.assertLessEqual(r["retraced"]["painted"],
                             r["straight"]["painted"] * 1.01)


class EventDensityInvarianceTests(unittest.TestCase):
    """D1 authorises geometric repetition to accumulate. It explicitly does NOT
    authorise event-frequency buildup."""

    def test_regrouping_the_same_geometry_changes_nothing(self) -> None:
        for row in probe()["density"]:
            with self.subTest(perEvent=row["perEvent"]):
                self.assertEqual(0, row["vsOnePerEvent"]["differing"])
                self.assertEqual(0, row["vsOnePerEvent"]["maxDelta"])

    def test_the_mean_is_identical_across_groupings(self) -> None:
        means = {row["mean"] for row in probe()["density"]}
        self.assertEqual(1, len(means))


class FlowContinuityTests(unittest.TestCase):
    """§13. 0.994 -> 0.995 -> 0.999 -> 1.0 must have no branch discontinuity,
    because the branch that used to sit at 0.995 is what creased."""

    def test_the_axis_value_is_monotone_in_flow(self) -> None:
        rows = probe()["flowContinuity"]
        for a, b in zip(rows, rows[1:]):
            with self.subTest(a=a["flow"], b=b["flow"]):
                self.assertGreaterEqual(b["axis"], a["axis"])

    def test_no_step_across_the_retired_threshold(self) -> None:
        """The old branch sat between 0.994 and 0.995. A step there would mean
        it is still live under another name."""

        rows = {r["flow"]: r for r in probe()["flowContinuity"]}
        self.assertLessEqual(abs(rows[0.995]["axis"] - rows[0.994]["axis"]), 1)
        self.assertLessEqual(abs(rows[0.995]["mean"] - rows[0.994]["mean"]), 0.5)


class OpacityIsAppliedOnceTests(unittest.TestCase):
    """§7.4: brush coverage is UNSELECTED, UNMULTIPLIED intrinsic coverage.
    Opacity belongs to the merge and must not reach the stroke buffer."""

    def test_the_stroke_buffer_ignores_opacity(self) -> None:
        rows = probe()["opacity"]
        self.assertEqual(1, len({r["axis"] for r in rows}))
        self.assertEqual(1, len({r["mean"] for r in rows}))


class AccumulatorLifecycleTests(unittest.TestCase):
    """§15/§20. State that survived a contact would make the second stroke of a
    session differ from the first."""

    def test_two_identical_contacts_produce_identical_pixels(self) -> None:
        r = probe()["reset"]["identical"]
        self.assertEqual(0, r["differing"])
        self.assertEqual(0, r["maxDelta"])


class TheSourceSaysWhatItDoesTests(unittest.TestCase):
    """Guards against the repair being reverted by a plausible-looking edit."""

    def test_the_magnitude_comes_from_coverage_not_from_the_table(self) -> None:
        """`code_of` STRIPS COMMENTS, so a guard written against prose can never
        pass -- it would only be asserting that a sentence exists. This asserts
        the arithmetic: the table returns a dimensionless fraction of a pass and
        the magnitude is the optical depth of the same supersampled `cov` the
        peak floor uses. Sampling the density at the pixel centre instead cost
        F1's tiny-tip isotropy, 21.8% directional spread at radius 0.75."""

        src = code_of(COVERAGE)
        self.assertIn("frac * negLogOf(target * cov)", src)

    def test_the_opening_mark_keys_off_the_renderer(self) -> None:
        """`dep.swept`, not "this is a stamp". U3-TF wires real tip routing, and
        a genuine stamp train still needs its accumulator."""

        src = code_of(COVERAGE)
        self.assertIn("dep.swept ? 0 : cov * dep.fEff", src)


class TheCrossSectionIsTheProfileTests(unittest.TestCase):
    """The one ABSOLUTE assertion in this module, and the mutation campaign is
    why it exists.

    Every other measurement here is an invariance or a ratio. Halving the `peak`
    floor moved 85,596 pixels by up to 126 levels and every one of them still
    passed, because a uniform scale preserves both. This compares the body
    cross-section against `shapeAt` in levels, which no scale survives.

    It also confirms the model end to end: a single pass deposits exactly the
    tip's own profile, measured to within one 8-bit level at every hardness and
    flow tried.
    """

    def test_the_body_matches_shape_at_within_one_level(self) -> None:
        for row in probe()["profile"]:
            with self.subTest(hardness=row["hardness"], flow=row["flow"]):
                self.assertLessEqual(row["worstDeviation"], 1)


class SweepIsTotalTests(unittest.TestCase):
    """`sweep` must work for any deposition it is handed, not only one whose
    caller remembered to declare `swept: true`."""

    def test_sweep_does_not_require_a_swept_deposition(self) -> None:
        """REGRESSION, and one I introduced. Building the pass table only for a
        swept deposition saves a stamp train 12 ms it never uses -- but `sweep`
        is reachable with ANY deposition, and the first version of that
        optimisation left it a null table. Two probe suites died in
        `setUpClass`, which surfaces as 31 errors with no obvious cause."""

        self.assertIsNone(probe()["notSwept"]["threw"])
        self.assertGreater(probe()["notSwept"]["touched"], 0)

    def test_it_covers_the_same_footprint_either_way(self) -> None:
        """`overlapK` differs between the two, so deposited values may; the
        footprint may not."""

        self.assertTrue(probe()["notSwept"]["sameFootprint"])


class SuiteIntegrityTests(unittest.TestCase):

    def test_every_test_in_this_module_is_counted(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_U3R2F_C2_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
