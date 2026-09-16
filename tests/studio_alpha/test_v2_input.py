"""Brush V2 input normalization and deterministic replay. V2-02, spec BE2.

`OVERNIGHT_HANDOFF_V2_02_AND_GPU_2026-08-25.md` §3.3 names eight properties this
package must prove. Every one has a test below, and the mapping is stated in
each class so a reader can check the coverage rather than trust it.

THE VERDICT THIS SUITE SUPPORTS IS "CLASSIFICATION PROVEN; ROUTING DEFERRED",
and §3.3 says so explicitly. Nothing here demonstrates palm rejection or touch
navigation, because neither is implemented: whether a finger paints or pans is
BE8's decision (§1.1), and `TheNormalizerMakesNoRoutingDecisionTests` asserts
the module contains no machinery for it.

THE ONE TEST THAT CARRIES THE PACKAGE is
`test_the_same_samples_regrouped_produce_the_same_stream`. §3.1 requires
fixtures and replay "independent of browser dispatch grouping", and that is not
a property you can inspect -- it is one you measure by taking the same twelve
physical samples, cutting them into dispatches four different ways, and
demanding byte-identical canonical streams. A normalizer that keyed anything off
dispatch boundaries passes every other test here and fails that one.

Legacy findings L1-L4 are recorded in
`Evidence/source-review/V2-02-normalized-input.md` §5 and are NOT fixed by this
package -- handoff §1.2. They are BE8/migration obligations.

Review: `Evidence/source-review/V2-02-normalized-input.md`.
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

EXPECTED_V2_INPUT_TESTS = 37

PROBE = Path(__file__).with_name("v2_02_probe.js")
MODULE = (APP_ROOT / "forge_studio" / "frontend" / "v2" / "input.js")
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


class PointerKindIsClassifiedTests(_Probed):
    """§3.3 #1 and #2."""

    def test_pen_mouse_and_touch_each_get_their_own_discriminator(self) -> None:
        c = self.report["classification"]
        self.assertEqual("pen", c["pen"])
        self.assertEqual("mouse", c["mouse"])
        self.assertEqual("touch", c["touchMeasured"])
        self.assertEqual("touch", c["touchConstant"])

    def test_a_primary_pen_and_a_primary_touch_classify_separately(self) -> None:
        """§3.3 #1. AND THIS IS NOT PALM REJECTION -- see the next test.

        The Pointer Events specification defines the primary pointer PER
        POINTER TYPE, so a pen and a touch can both be primary at the same
        instant. Both are classified, both are produced, neither is dropped.
        """

        s = self.report["classification"]["simultaneous"]
        self.assertEqual("pen", s["pen"])
        self.assertEqual("touch", s["touch"])
        self.assertTrue(s["bothProduced"])
        self.assertEqual([0, 1], s["sequences"])

    def test_the_normalizer_does_not_treat_isprimary_as_palm_rejection(self) -> None:
        """The honest half of the test above.

        `canvas-input.js:185-187 isDrivingPointer` filters `isPrimary === false`
        and READS like palm rejection. It is not: a second finger during a pinch
        is correctly ignored, and a palm resting while the pen draws is a
        PRIMARY touch and is not. Whether the palm lands before or after the pen
        changes the outcome.

        V2's normalizer therefore never BRANCHES on `isPrimary` --
        classification is not rejection, and rejection is BE8's.

        The guard is "never branched on", not "never mentioned": `recordEvent`
        stores `isPrimary` in a fixture because a recording should carry what
        the device reported. The first version of this test asserted absence and
        failed on exactly that, which is the right failure for the wrong
        reason -- so it is narrowed to the decision-making functions.
        """

        code = code_of(MODULE)
        recording = code[code.index("function recordEvent"):
                         code.index("function replay")]
        deciding = code.replace(recording, "")
        self.assertNotIn("isPrimary", deciding)
        # Calibration: the mention DOES exist, in the one place it belongs.
        self.assertIn("isPrimary", recording)

    def test_an_unrecognised_device_becomes_mouse_rather_than_a_refusal(self) -> None:
        """§8.1: "Missing sensors MUST fall back without disabling an otherwise
        usable preset." The contract refuses an unknown kind, so something must
        map -- and `mouse` is what is actually true of a device Studio cannot
        identify: no measured pressure, no tilt."""

        c = self.report["classification"]
        self.assertEqual("mouse", c["unknownDevice"])
        self.assertEqual("mouse", c["emptyType"])

    def test_an_unrecognised_device_still_produces_a_usable_sample(self) -> None:
        s = self.report["sensors"]
        self.assertIs(False, s["unknownPressureMeasured"])
        self.assertEqual(0.5, s["unknownPressureValue"])


class MissingSensorsFallBackTests(_Probed):
    """§3.3 #6 — and the operative word is FALL BACK, not disable."""

    def test_a_pen_reports_measured_pressure(self) -> None:
        self.assertIs(True, self.report["sensors"]["penPressureMeasured"])
        self.assertEqual(0.7, self.report["sensors"]["penPressureValue"])

    def test_a_mouse_reports_a_substituted_pressure_and_says_so(self) -> None:
        s = self.report["sensors"]
        self.assertIs(False, s["mousePressureMeasured"])
        self.assertEqual(0.5, s["mousePressureValue"])

    def test_touch_pressure_counts_only_strictly_between_the_constants(self) -> None:
        """CT2's rule, kept: many digitisers report a constant 1 for contact and
        0 for none, and only a value strictly between the two could be a
        measurement."""

        s = self.report["sensors"]
        self.assertIs(True, s["touchMeasuredFlag"])
        self.assertIs(False, s["touchConstantFlag"])
        self.assertEqual(0.5, s["touchConstantValue"])

    def test_mouse_pressure_is_configurable(self) -> None:
        """§8.2: "Mouse MUST use a fixed configurable pressure." Configurable by
        the caller; a Settings control is a product surface V2-02 does not
        own."""

        self.assertEqual(0.25, self.report["sensors"]["configuredMousePressure"])

    def test_a_sample_with_no_usable_pressure_is_still_a_sample(self) -> None:
        """The whole point of "fall back without disabling"."""

        self.assertTrue(self.report["sensors"]["missingPressureStillProduces"])

    def test_tilt_is_available_only_on_a_pen_that_reports_it(self) -> None:
        s = self.report["sensors"]
        self.assertIs(True, s["penTiltAvailable"])
        self.assertIs(False, s["mouseTiltAvailable"])
        self.assertEqual([12, -3], s["penTilt"])

    def test_an_unproduced_sensor_is_null_and_not_a_fabricated_zero(self) -> None:
        """A defaulted 0 tells an azimuth dynamic it has a reading. Nothing
        derives azimuth today, so it is absent."""

        self.assertIsNone(self.report["sensors"]["azimuthAbsent"])

    def test_barrel_rotation_reaches_the_contract_in_radians(self) -> None:
        """`twist` is measured by `canvas-input.js:127` and read by NOTHING in
        Legacy. It reaches V2's contract, converted once, here."""

        self.assertAlmostEqual(3.14159265, self.report["sensors"]["barrelFromTwist"],
                               places=6)


class CoalescedSamplesAreConsumedInOrderTests(_Probed):
    """§3.3 #3."""

    def test_source_order_is_preserved(self) -> None:
        self.assertEqual([1, 2, 3], self.report["coalesced"]["orderPreserved"])

    def test_sequence_numbers_are_monotonic_across_the_group(self) -> None:
        self.assertEqual([0, 1, 2], self.report["coalesced"]["sequences"])

    def test_a_genuine_coalesced_sample_says_it_is_one(self) -> None:
        self.assertTrue(self.report["coalesced"]["allMarkedCoalesced"])

    def test_a_degraded_path_is_distinguishable_from_a_one_sample_list(self) -> None:
        """Three degraded paths -- method missing, method threw, empty list --
        all fall back to the dispatched event, marked NOT coalesced so a
        consumer can tell."""

        c = self.report["coalesced"]
        self.assertEqual(1, c["throwFallbackCount"])
        self.assertIs(False, c["throwFallbackCoalesced"])
        self.assertEqual(1, c["emptyFallbackCount"])
        self.assertIs(False, c["emptyFallbackCoalesced"])

    def test_a_null_entry_does_not_kill_the_stroke(self) -> None:
        """CT2 wraps only the `getCoalescedEvents()` CALL
        (`canvas-input.js:155`), so a null inside the returned array throws at
        `raw.clientX`, escapes the handler and loses the rest of the contact.
        §8.2 says input must not be dropped; one malformed entry is a smaller
        loss than the remainder."""

        c = self.report["coalesced"]
        self.assertIsNone(c["nullEntryThrew"])
        self.assertEqual(2, c["nullEntrySurvivors"])


class TheStreamIsIndependentOfDispatchGroupingTests(_Probed):
    """§3.3 #4, and the property the whole package exists for.

    Twelve physical samples, cut into dispatches four different ways: as
    recorded (4+5+3), as one dispatch of twelve, as twelve dispatches of one,
    and as fours. All four must produce BYTE-IDENTICAL canonical streams.

    A normalizer that keyed sequence off dispatches, or reset dedupe state per
    event, passes every other test in this file and fails this one.
    """

    def test_one_dispatch_and_the_recorded_grouping_agree(self) -> None:
        self.assertTrue(self.report["grouping"]["oneDispatchMatches"])

    def test_one_sample_per_dispatch_agrees_too(self) -> None:
        self.assertTrue(self.report["grouping"]["singlesMatch"])

    def test_an_arbitrary_regrouping_agrees(self) -> None:
        self.assertTrue(self.report["grouping"]["threesMatch"])

    def test_the_streams_being_compared_are_not_empty(self) -> None:
        """"Too clean is the tell." Four identical empty streams would satisfy
        every assertion above."""

        g = self.report["grouping"]
        self.assertEqual(12, g["recordedCount"])
        self.assertEqual([12, 12, 12, 12], g["sampleCounts"])


class PredictedEventsNeverEnterTheStreamTests(_Probed):
    """§3.3 #5."""

    def test_offered_predicted_samples_are_ignored(self) -> None:
        """The probe hands the event a `getPredictedEvents` returning samples at
        (999,999) and (1000,1000). Neither may appear."""

        p = self.report["predicted"]
        self.assertEqual(1, p["producedCount"])
        self.assertEqual(1, p["maxX"])

    def test_the_module_does_not_call_the_prediction_api(self) -> None:
        """ON THE CODE, NOT THE TEXT. The module names `getPredictedEvents` in
        a comment explaining why it never calls it, and a text scan matches
        that -- the seventh time this repository has hit the trap.

        §8.2's prohibition is satisfied by never asking. This is what turns
        "correct by absence" into "correct by construction": a future author
        adding prediction has to delete a test that says why not."""

        self.assertNotIn("getPredictedEvents", code_of(MODULE))

    def test_the_contract_has_no_predicted_flag(self) -> None:
        """A predicted sample is not a canonical sample with a flag set. It is
        one that must not be in the stream at all, so there is nowhere to
        record that it was."""

        contracts = (APP_ROOT / "forge_studio" / "frontend" / "v2"
                     / "brush-contracts.js")
        self.assertNotIn('"predicted"', code_of(contracts))


class OrderingSurvivesBadClocksTests(_Probed):
    """Derived from the review record §6.3 rather than from §3.3's list."""

    def test_sequence_always_advances(self) -> None:
        self.assertTrue(self.report["ordering"]["sequencesStrictlyIncrease"])

    def test_time_never_goes_backwards(self) -> None:
        """A negative interval reaching a speed dynamic is worse than a repeated
        one. `sequence` remains the ordering authority."""

        self.assertTrue(self.report["ordering"]["timesNeverGoBackwards"])

    def test_a_backwards_timestamp_is_clamped_to_the_previous(self) -> None:
        self.assertEqual(100000, self.report["ordering"]["backwardsClampedTo"])

    def test_two_samples_sharing_a_timestamp_still_order(self) -> None:
        """Coalesced samples can share `event.timeStamp` -- the browser reports
        the frame's time, not the digitiser's."""

        o = self.report["ordering"]
        self.assertEqual([0, 1], o["sharedTimeSequences"])
        self.assertEqual([50000, 50000], o["sharedTimeTimes"])

    def test_time_is_microseconds(self) -> None:
        """§7.1. The producer has float milliseconds; the conversion happens
        once, in the normalizer, not in every consumer wanting a rate."""

        self.assertEqual(100000, self.report["ordering"]["microseconds"])


class NoFieldIsSilentlyDroppedTests(_Probed):
    """§3.3 #7 — the translation seam between the contract and the normalizer."""

    def test_every_declared_field_is_produced(self) -> None:
        self.assertEqual([], self.report["enumeration"]["declaredNotProduced"])

    def test_no_produced_field_is_undeclared(self) -> None:
        self.assertEqual([], self.report["enumeration"]["producedNotDeclared"])


class TheNormalizerMakesNoRoutingDecisionTests(unittest.TestCase):
    """§3.2 — what V2-02 explicitly does NOT own.

    "Classification proven; routing deferred" is only an honest verdict if the
    module has no routing in it to accidentally exercise.
    """

    ROUTING = ("pan", "zoom", "pinch", "scroll", "tool", "target", "layer",
               "setPointerCapture", "releasePointerCapture", "preventDefault",
               "touchAction", "fingerPaint")

    def test_it_contains_no_routing_machinery(self) -> None:
        code = code_of(MODULE)
        for token in self.ROUTING:
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_it_touches_no_dom(self) -> None:
        code = code_of(MODULE)
        for token in ("document.", "getContext", "addEventListener",
                      "requestAnimationFrame", "localStorage"):
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
        self.assertIn("v2/input.js", html)
        self.assertNotIn("v2/scratchpad.js", html)

    def test_it_still_has_the_code_the_guards_are_about(self) -> None:
        """Calibration. A stripper or a path that returned nothing would make
        every assertion above pass."""

        code = code_of(MODULE)
        self.assertIn("window.StudioBrushInputV2", code)
        self.assertIn("StrokeInput.prototype.samplesFrom", code)
        self.assertIn("function classify", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_INPUT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
