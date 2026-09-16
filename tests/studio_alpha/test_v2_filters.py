"""Brush V2 smoothing modes and dynamics. V2-04, spec BE4.

Overnight handoff §4.4 names seven pieces of evidence; each has a class below.

TWO FIXTURES IN THIS SUITE WERE WRONG IN THEIR FIRST DRAFT and both are worth
knowing about, because both failed in the direction that looks like a pass:

* the independence guards compared filtered output against input after setting
  a strength to 0, which produced an INFINITESIMAL window rather than no
  window. The result equalled the input to within floating point and the guard
  failed on rounding. Strength 0 is now genuinely off, which is what "position
  and pressure smoothing are independent" has to mean;
* the rate-invariance fixture built its two rates as a fixed tremor per SAMPLE,
  which is two different zig-zags (15 px period against 3.75) rather than one
  path reported at two rates -- the same trap `oracle/fixtures.js:8-14` records
  and the sampler hit one package earlier. Measured wrong: 0.465 against 0.149.
  The vertices are fixed and subdivided now, and the comparison is made at
  CORRESPONDING vertices rather than by averaging over two different sample
  sets. Drift: exactly 0.

Review: `Evidence/source-review/V2-04-smoothing-and-dynamics.md`.
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

EXPECTED_V2_FILTER_TESTS = 37

PROBE = Path(__file__).with_name("v2_04_probe.js")
V2 = APP_ROOT / "forge_studio" / "frontend" / "v2"
FILTERS = V2 / "filters.js"
DYNAMICS = V2 / "dynamics.js"
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


class TheThreeModesAreDistinctTests(_Probed):
    """§4.4 evidence 1-3, §5.2, §20 Correctness 3."""

    def test_the_three_the_spec_names_are_the_only_three(self) -> None:
        self.assertEqual(["raw", "natural", "stabilized"],
                         self.report["modes"]["list"])

    def test_an_unknown_mode_is_refused(self) -> None:
        self.assertTrue(self.report["modes"]["unknownModeRefused"])

    def test_raw_is_identity(self) -> None:
        """§4.4 evidence 1: "Raw reproduces the canonical sampled path without
        hidden filtering."

        Identity, not "close". A Raw that filtered even slightly would make
        every measurement of the other two relative to an unstated baseline."""

        self.assertTrue(self.report["modes"]["rawIsIdentity"])

    def test_natural_reduces_jitter(self) -> None:
        """§4.4 evidence 2. Measured against the tremor fixture's own
        amplitude, not against a threshold picked afterwards."""

        d = self.report["modes"]["deviation"]
        self.assertEqual(3, d["input"]["mean"])
        self.assertLess(d["natural"]["mean"], d["input"]["mean"] / 2)
        self.assertTrue(self.report["modes"]["naturalIsNotIdentity"])

    def test_stabilized_corrects_more_than_natural(self) -> None:
        """§4.4 evidence 3: "deliberate stronger correction"."""

        d = self.report["modes"]["deviation"]
        self.assertLess(d["stabilized"]["mean"], d["natural"]["mean"])

    def test_the_ordering_is_raw_then_natural_then_stabilized(self) -> None:
        """The property that makes them a scale rather than three settings."""

        d = self.report["modes"]["deviation"]
        self.assertGreater(d["raw"]["mean"], d["natural"]["mean"])
        self.assertGreater(d["natural"]["mean"], d["stabilized"]["mean"])


class TheWindowIsArcLengthTests(_Probed):
    """BE9's finding, carried: a window counted in samples covers a quarter of
    the arc at 240 Hz that it covers at 60 Hz, and measured through the real
    pipeline that was LARGER than the placement defect BE3 fixed."""

    def test_two_rates_over_one_path_agree_exactly_at_shared_points(self) -> None:
        """Zero drift, not a tolerance. An arc-length centroid is an integral,
        and subdividing a segment changes how many terms the sum has rather
        than what it adds up to."""

        self.assertEqual(0, self.report["rateInvariance"]["vertexDrift"])

    def test_the_two_streams_really_are_different_samplings(self) -> None:
        """Calibration: agreement between two identical streams would prove
        nothing."""

        r = self.report["rateInvariance"]
        self.assertEqual(21, r["coarseCount"])
        self.assertEqual(81, r["fineCount"])


class SmoothingDoesNotMoveTheEndsTests(_Probed):
    """§5.1 and §20 Input 5, under a filter that lags by design."""

    def test_the_first_sample_is_the_contact_point(self) -> None:
        self.assertTrue(self.report["endpoints"]["firstMatches"])

    def test_the_last_sample_is_what_actually_arrived(self) -> None:
        """"A stabilized stroke MUST flush real buffered samples to the actual
        endpoint; it MUST NOT invent predicted continuation." A causal filter
        lags; the answer is to emit what arrived, not to extrapolate."""

        self.assertTrue(self.report["endpoints"]["lastMatches"])

    def test_no_sample_is_lost_or_invented(self) -> None:
        e = self.report["endpoints"]
        self.assertEqual(e["inputCount"], e["count"])


class CornerPreservationIsOptionalTests(_Probed):
    """§4.4 evidence 4, §5.1."""

    def test_it_defaults_off(self) -> None:
        """§5.1: "MUST be a separate toggle and MUST default off.\""""

        self.assertTrue(self.report["corners"]["defaultIsOff"])

    def test_turning_it_on_passes_measurably_closer_to_the_vertex(self) -> None:
        """The named metric: minimum distance from any filtered position to the
        true vertex, in document pixels. Both values are reported so this is a
        comparison rather than a threshold."""

        c = self.report["corners"]
        self.assertLess(c["nearestOn"], c["nearestOff"])

    def test_the_difference_is_material_rather_than_marginal(self) -> None:
        c = self.report["corners"]
        self.assertGreater(c["nearestOff"] - c["nearestOn"], 1.0)


class PositionAndPressureAreIndependentTests(_Probed):
    """§4.4 evidence 5 — and both halves, because "unchanged" is also true of a
    filter that does nothing."""

    def test_a_position_filter_leaves_every_pressure_byte_identical(self) -> None:
        self.assertTrue(
            self.report["independence"]["pressuresUnchangedByPositionFilter"])

    def test_a_pressure_filter_moves_zero_geometry(self) -> None:
        self.assertTrue(
            self.report["independence"]["geometryUnchangedByPressureFilter"])

    def test_the_position_filter_did_move_geometry(self) -> None:
        """Calibration for the assertion above."""

        self.assertTrue(
            self.report["independence"]["positionFilterMovedGeometry"])

    def test_the_pressure_filter_did_move_pressure(self) -> None:
        self.assertTrue(
            self.report["independence"]["pressureFilterMovedPressure"])

    def test_pressure_gets_a_shorter_window_than_position(self) -> None:
        """Legacy's reason, carried: "position jitter is a hand tremor an owner
        wants removed, while pressure lag is felt immediately as a brush that
        will not respond. Averaging them over the same window trades one for
        the other.\""""

        self.assertEqual(0.4,
                         self.report["independence"]["pressureWindowFraction"])


class DynamicsResolveAgainstWhatWasMeasuredTests(_Probed):
    """§4.4 evidence 7, §10."""

    def test_the_inputs_and_targets_are_the_ones_the_spec_names(self) -> None:
        d = self.report["dynamics"]
        self.assertEqual(["pressure", "speed", "direction", "tilt"], d["inputs"])
        self.assertEqual(["size", "flow", "angle", "ratio"], d["targets"])

    def test_two_targets_are_deliberately_absent(self) -> None:
        """`opacity` is applied once at commit and bounds the whole stroke, so a
        per-mark curve would have to choose which mark it was right about --
        Flow is the per-mark quantity. `grain` is applied once to accumulated
        coverage precisely so overlap cannot wash the texture out."""

        d = self.report["dynamics"]
        self.assertTrue(d["opacityIsNotATarget"])
        self.assertTrue(d["grainIsNotATarget"])

    def test_no_rules_returns_the_shared_neutral(self) -> None:
        self.assertTrue(self.report["dynamics"]["noRulesIsNeutral"])

    def test_a_measured_pressure_drives_the_curve(self) -> None:
        self.assertAlmostEqual(0.6, self.report["dynamics"]["penSize"], places=6)

    def test_an_unmeasured_input_uses_the_rules_own_fallback(self) -> None:
        """§8.1, and the fallback is the RULE's rather than one the evaluator
        picked on its behalf. A mouse has no measured pressure."""

        d = self.report["dynamics"]
        self.assertEqual(1, d["mouseSize"])
        self.assertAlmostEqual(0.73, d["mouseFlow"], places=6)

    def test_an_unavailable_tilt_uses_its_declared_fallback(self) -> None:
        self.assertAlmostEqual(0.42,
                               self.report["dynamics"]["tiltUnavailableUsesFallback"],
                               places=6)

    def test_an_unavailable_speed_uses_its_declared_fallback(self) -> None:
        self.assertAlmostEqual(0.33,
                               self.report["dynamics"]["speedUnavailableUsesFallback"],
                               places=6)

    def test_angle_adds_in_degrees_while_size_multiplies(self) -> None:
        """"A multiplicative angle is meaningless -- zero degrees times
        anything is zero degrees.\""""

        d = self.report["dynamics"]
        # Two identical size rules COMPOUND; two identical angle rules SUM.
        #
        # Measured at a heading that yields a non-zero factor. The first draft
        # used heading 0, where the factor is 0 and `0 + 0` is
        # indistinguishable from `0 * 0` -- the angle mutation survived it, and
        # the guard was reporting a pass over a fixture that could not fail.
        self.assertAlmostEqual(0.36, d["sizeMultiplies"], places=6)
        self.assertEqual(45, d["angleFromOneRule"])
        self.assertEqual(90, d["angleAdds"])

    def test_an_unknown_target_or_input_is_ignored_rather_than_guessed(self) -> None:
        d = self.report["dynamics"]
        self.assertEqual(1, d["unknownTargetIgnored"])
        self.assertEqual(1, d["unknownInputIgnored"])

    def test_the_named_feels_cover_the_ordinary_ui(self) -> None:
        """§5.3: "The ordinary UI MUST present: Soft; Balanced; Firm; Custom."
        Custom is the raw rule form, so the three named ones are what a preset
        needs."""

        self.assertEqual(["balanced", "firm", "soft"],
                         self.report["dynamics"]["feels"])

    def test_every_curve_shape_is_available(self) -> None:
        self.assertEqual(
            ["easeIn", "easeOut", "flat", "linear", "sShape", "sharp"],
            self.report["dynamics"]["curves"])


class SpeedIsMeasuredPerSegmentTests(_Probed):
    """Legacy's rule: "The dabs along one segment are interpolated positions
    between two real samples; they share the hand movement that produced them,
    and a per-dab speed would be an invention with a plausible shape.\""""

    def test_a_real_interval_produces_a_speed(self) -> None:
        self.assertEqual(1, self.report["dynamics"]["segmentSpeed"])

    def test_two_samples_sharing_a_timestamp_have_no_measurable_speed(self) -> None:
        """Null rather than Infinity. Dividing by zero would feed a curve a
        value it was never defined over."""

        self.assertIsNone(self.report["dynamics"]["sharedTimestampSpeedIsNull"])


class NothingIsRewrittenAfterRelease(unittest.TestCase):
    """§4 and §5.1 forbid post-release cleanup or curve replacement.

    Asserted structurally: the filter is causal by construction and exposes no
    API that returns a revised history.
    """

    FORBIDDEN = ("simplify", "smoothAll", "refit", "rewrite", "beautify",
                 "resample", "replaceStroke", "postProcess")

    def test_the_filter_has_no_after_the_fact_rewrite(self) -> None:
        code = code_of(FILTERS)
        for token in self.FORBIDDEN:
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_neither_module_touches_a_canvas(self) -> None:
        for path in (FILTERS, DYNAMICS):
            code = code_of(path)
            for token in ("getContext", "document.", "ImageData", "canvas"):
                with self.subTest(module=path.name, token=token):
                    self.assertNotIn(token, code)

    def test_neither_is_loaded_by_the_page(self) -> None:
        """CHANGED BY U3, recorded rather than quietly relaxed.

        Until U3 this asserted the page did not load the module at all, which
        was the right invariant while V2 had no consumer. U3 gives it one, so
        the module IS loaded -- and the invariant that now carries the weight
        is that the ADAPTER is off by default, which
        `test_u3_canvas_adapter.py` drives rather than spells.

        The acceptance surface is still not shipped."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("v2/filters.js", html)
        self.assertIn("v2/dynamics.js", html)
        self.assertNotIn("v2/scratchpad.js", html)

    def test_the_modules_still_contain_what_the_guards_are_about(self) -> None:
        self.assertIn("StrokeFilter.prototype.push", code_of(FILTERS))
        self.assertIn("function evaluate", code_of(DYNAMICS))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_FILTER_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
