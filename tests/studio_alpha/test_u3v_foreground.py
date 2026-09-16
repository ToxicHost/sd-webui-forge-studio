"""U3-V — the foreground evidence harness, and the gates that decide its verdicts.

WHAT IS UNDER TEST HERE IS THE HARNESS, NOT THE BRUSH. U3-V's product question
-- does V2 feel immediate -- is answered by a visible browser and by the owner
painting, and neither can be asserted from Python. What CAN be asserted, and
what §14 requires, is that the harness refuses a run when any of twelve named
failures is present, and that it does NOT refuse an otherwise clean one.

THE GATES ARE A PURE FUNCTION FOR EXACTLY THIS REASON. A refusal buried inside
an async browser routine cannot be exercised without a browser, and an untested
gate is a comfortable assumption in a guard's uniform. `_u3v_harness.js` keeps
every refusal in `evaluateGates(observation)`; `u3v_gates_probe.js` loads the
real harness in a stub context and drives that function with observations that
differ from a clean one in exactly one field.

WHY THE ASSERTIONS READ BEHAVIOUR AND NOT SPELLING. Both E0 and U3 had a guard
that asserted an identifier was PRESENT while the mutation that deleted its CALL
went undetected. So these tests never grep for a gate's name in the harness;
they feed it an observation and require the refusal to come back.

§7.1 SAYS THE U2-VF EVIDENCE IS HISTORICAL. Its harness is pinned by hash here,
so a later edit to it fails a test rather than quietly rewriting the record that
U2's Alpha clearance rests on.

Review: `Evidence/source-review/U3V-foreground-feel.md`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_U3V_TESTS = 56

WORKSPACE = APP_ROOT.parent
EVIDENCE = WORKSPACE / "Evidence" / "u3v-foreground"
HARNESS = EVIDENCE / "_u3v_harness.js"
U2VF_HARNESS = WORKSPACE / "Evidence" / "u2vf-foreground" / "_u2vf_harness.js"
PROBE = Path(__file__).with_name("u3v_gates_probe.js")

# The committed U2-VF harness, read from disk at the start of U3-V and recorded
# in the source-review record before any of this was written.
U2VF_SHA256 = "7a971a470c46de588ed6a54ec7c192efc45f072bfb68301ea0a10805d740eed3"


def _probe() -> dict:
    out = subprocess.run(
        ["node", str(PROBE)], capture_output=True, text=True, check=False,
        cwd=str(APP_ROOT))
    if out.returncode != 0:
        raise AssertionError(f"probe failed: {out.stderr[-2000:]}")
    return json.loads(out.stdout)


_RESULT: dict | None = None


def probe() -> dict:
    global _RESULT
    if _RESULT is None:
        _RESULT = _probe()
    return _RESULT


def case(name: str) -> dict:
    for c in probe()["cases"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"probe has no case {name!r}")


def refusals(name: str) -> list[str]:
    return case(name)["refusals"]


class BaselineTests(unittest.TestCase):
    """A clean observation must be refused for nothing.

    Without this every "the gate fired" result below would be unfalsifiable: a
    function that refuses everything detects everything.
    """

    def test_a_clean_v2_observation_is_not_refused(self) -> None:
        self.assertEqual([], refusals("clean-v2"))

    def test_a_clean_legacy_control_is_not_refused(self) -> None:
        self.assertEqual([], refusals("clean-legacy"))

    def test_every_probe_case_matched_its_expectation(self) -> None:
        bad = [c["name"] for c in probe()["cases"]
               if sorted(c["refusals"]) != sorted(c["expected"])]
        self.assertEqual([], bad)

    def test_the_probe_covers_more_than_the_two_baselines(self) -> None:
        self.assertGreaterEqual(len(probe()["cases"]), 20)


class GateOneFlagOffTests(unittest.TestCase):
    def test_a_v2_row_with_the_flag_off_is_refused(self) -> None:
        self.assertIn("v2-flag-off-in-a-v2-row", refusals("1-flag-off-in-a-v2-row"))

    def test_the_flag_gate_does_not_fire_on_a_legacy_control(self) -> None:
        self.assertNotIn("v2-flag-off-in-a-v2-row", refusals("clean-legacy"))


class GateTwoNoMarksTests(unittest.TestCase):
    def test_a_contact_that_placed_no_marks_is_refused(self) -> None:
        self.assertIn("v2-placed-no-marks", refusals("2-contacts-but-no-marks"))

    def test_a_contact_that_consumed_no_samples_is_refused(self) -> None:
        self.assertIn("v2-consumed-no-samples", refusals("v2-consumed-no-samples"))

    def test_a_row_with_no_contact_at_all_is_refused(self) -> None:
        self.assertIn("v2-accepted-no-contact", refusals("v2-accepted-no-contact"))

    def test_a_named_adapter_refusal_is_carried_into_the_verdict(self) -> None:
        self.assertIn("v2-refused:target-is-not-a-raster-layer",
                      refusals("v2-refused-the-contact"))


class GateThreeLegacyContaminationTests(unittest.TestCase):
    """The U3 defect: Legacy's dab painted and V2 did not.

    It looked almost correct in a browser, and the only tell was a counter.
    """

    def test_pixels_with_no_v2_marks_are_attributed_to_legacy(self) -> None:
        self.assertIn("legacy-produced-the-pixels-in-a-v2-row",
                      refusals("3-legacy-produced-the-pixels"))

    def test_the_attribution_gate_is_silent_when_v2_did_the_work(self) -> None:
        self.assertNotIn("legacy-produced-the-pixels-in-a-v2-row",
                         refusals("clean-v2"))


class GateFourStaleBuildTests(unittest.TestCase):
    """Studio's loader appends a CONSTANT `?v=`, so a loaded module ignores edits.

    Two U3 browser results were contaminated this way and both looked exactly
    like product defects.
    """

    def test_a_runtime_that_disagrees_with_served_source_is_refused(self) -> None:
        self.assertIn("runtime-does-not-match-served-source", refusals("4-stale-module"))

    def test_agreeing_markers_are_fresh(self) -> None:
        self.assertTrue(probe()["freshness"]["agreeingIsFresh"])

    def test_source_has_it_and_runtime_does_not_is_refused(self) -> None:
        self.assertTrue(probe()["freshness"]["staleIsRefused"])

    def test_runtime_has_it_and_source_does_not_is_also_refused(self) -> None:
        # A server older than the page. Equally a mismatch, and a direction the
        # first draft of this comparison ignored.
        self.assertTrue(probe()["freshness"]["backwardsIsRefused"])

    def test_a_runtime_probe_that_throws_is_a_mismatch_not_a_pass(self) -> None:
        self.assertTrue(probe()["freshness"]["throwingIsRefused"])


class GateFiveDisplayedPixelTests(unittest.TestCase):
    """§7.4. Wrong sample region, cleared buffer and genuinely-unchanged display
    must not collapse into one verdict -- that mistake made every U2-VF row
    WITHHELD on its first run."""

    def test_a_gl_readback_with_no_non_zero_pixels_is_refused(self) -> None:
        self.assertIn("gl-readback-was-empty-but-reported-as-gl",
                      refusals("5-gl-region-read-back-empty"))

    def test_an_unchanged_display_is_refused(self) -> None:
        self.assertIn("no-displayed-pixel-change", refusals("no-displayed-change"))

    def test_an_absent_readback_is_reported_separately_from_an_unchanged_one(self) -> None:
        only = refusals("no-displayed-readback")
        self.assertIn("no-displayed-readback", only)
        self.assertNotIn("no-displayed-pixel-change", only)

    def test_an_unchanged_canonical_document_is_refused(self) -> None:
        self.assertIn("no-canonical-pixel-change", refusals("no-canonical-change"))


class GateSixVisibilityTests(unittest.TestCase):
    def test_a_hidden_page_is_refused(self) -> None:
        self.assertIn("page-was-not-visible", refusals("6a-page-hidden"))

    def test_a_blurred_window_is_refused(self) -> None:
        self.assertIn("window-lost-focus-after-start", refusals("6b-window-blurred"))

    def test_a_run_with_almost_no_frames_is_refused(self) -> None:
        self.assertIn("too-few-frames-executed", refusals("too-few-frames"))

    def test_a_row_may_declare_a_lower_frame_floor(self) -> None:
        # The gate catches "the page never animated". A tap is one pointerdown
        # and one pointerup -- a complete interaction, not a truncated run.
        self.assertEqual([], refusals("a-tap-declares-its-own-frame-floor"))


class GateSevenEndpointTests(unittest.TestCase):
    def test_a_dropped_final_endpoint_is_refused(self) -> None:
        self.assertIn("final-endpoint-missing-from-the-painted-bounds",
                      refusals("7-final-endpoint-dropped"))

    def test_a_dropped_opening_contact_is_refused(self) -> None:
        # U3's second self-inflicted defect: strokes began at the first MOVE.
        self.assertIn("opening-contact-missing-from-the-painted-bounds",
                      refusals("7b-opening-contact-dropped"))


class GateEightPendingTests(unittest.TestCase):
    def test_an_unacknowledged_generation_is_refused(self) -> None:
        self.assertIn("presentation-work-left-pending",
                      refusals("8-generation-left-pending"))


class GateNineBoundedWorkTests(unittest.TestCase):
    def test_full_document_work_for_a_local_stroke_is_refused(self) -> None:
        self.assertIn("full-document-work-for-a-local-stroke",
                      refusals("9-full-document-work-for-a-local-stroke"))

    def test_a_large_share_is_allowed_when_the_stroke_is_not_local(self) -> None:
        # §11: "long diagonal rows may legitimately dirty a large bounding
        # rectangle". A gate that fired here would force the harness to lie
        # about a correct result.
        self.assertEqual([], refusals("9b-large-share-on-a-non-local-stroke"))


class GateTenGrowthTests(unittest.TestCase):
    def test_per_move_work_growing_with_history_is_refused(self) -> None:
        self.assertIn("per-move-work-grew-with-stroke-history",
                      refusals("10-work-grew-with-history"))

    def test_growth_below_the_clock_resolution_is_not_refused(self) -> None:
        # §11 allows the waiver explicitly. Firefox's 1 ms clamp is why: a ratio
        # of two sub-resolution numbers is noise, and U2-VF's first growth
        # figure was exactly that.
        self.assertEqual([], refusals("10b-growth-below-clock-resolution"))


class GateElevenFallbackTests(unittest.TestCase):
    def test_a_canvas2d_row_still_reading_from_gl_is_refused(self) -> None:
        self.assertIn("canvas2d-fallback-was-not-used",
                      refusals("11-canvas2d-fallback-skipped"))

    def test_a_canvas2d_row_that_really_used_the_overlay_passes(self) -> None:
        self.assertEqual([], refusals("11b-canvas2d-fallback-actually-used"))


class GateTwelveLegacyRoutingTests(unittest.TestCase):
    def test_a_legacy_control_routed_through_v2_is_refused(self) -> None:
        self.assertIn("legacy-control-was-routed-through-v2",
                      refusals("12-legacy-control-routed-through-v2"))


class PointerUpEndpointSeamTests(unittest.TestCase):
    """The Z-that-became-an-hourglass, guarded where it actually lived.

    `finishStroke` walks from LEGACY's last dab to the release point. During a
    V2 stroke Legacy never plots, so its last dab is still the POINTER-DOWN
    point and this drew a line from the stroke's start to its end. Measured at
    43,909 extra pixels on a 1024-square Z, and confirmed by suppressing
    `finishStroke` -- which removed the diagonal and left V2's own endpoint
    fully painted.

    `pointerleave` already carried a correct copy of this seam, so any test that
    merely searched the file for `_v2.finish` was green for the whole period the
    defect existed. These run the branch's real source text instead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        out = subprocess.run(
            ["node", str(Path(__file__).with_name("u3v_pointerup_probe.js"))],
            capture_output=True, text=True, check=False, cwd=str(APP_ROOT))
        if out.returncode != 0:
            raise AssertionError(f"pointerup probe failed: {out.stderr[-2000:]}")
        cls.r = json.loads(out.stdout)

    def _names(self, key: str) -> list[str]:
        return [c[0] for c in self.r[key]]

    def test_legacy_completion_does_not_run_while_v2_owns_the_contact(self) -> None:
        self.assertNotIn("legacy.finishStroke", self._names("v2Active"))

    def test_v2_completes_its_own_endpoint(self) -> None:
        self.assertIn("v2.finish", self._names("v2Active"))

    def test_v2_receives_the_raw_pointerup_event(self) -> None:
        # Not a normalized copy: V2 runs its own normalizer, and handing it
        # Legacy's stream would put two normalizers in series.
        call = next(c for c in self.r["v2Active"] if c[0] == "v2.finish")
        self.assertEqual("the-real-pointerup-event", call[2])
        self.assertEqual("toDoc", call[3])

    def test_the_endpoint_is_placed_before_the_commit(self) -> None:
        # After `commitStroke` the alpha map is gone, so ordering is load-bearing.
        names = self._names("v2Active")
        self.assertLess(names.index("v2.finish"), names.index("commitStroke"))

    def test_legacy_keeps_its_own_be9_completion_when_v2_is_idle(self) -> None:
        # The repair must not cost Legacy its endpoint: BE9 exists because the
        # stabiliser lags and the stroke otherwise falls short of the pointer.
        self.assertIn("legacy.finishStroke", self._names("v2Idle"))
        self.assertNotIn("v2.finish", self._names("v2Idle"))

    def test_studio_without_the_v2_modules_still_finishes_the_stroke(self) -> None:
        self.assertIn("legacy.finishStroke", self._names("adapterAbsent"))

    def test_the_eraser_takes_the_same_exclusive_path(self) -> None:
        # E0's repair rides on this branch too.
        self.assertNotIn("legacy.finishStroke", self._names("v2ActiveEraser"))
        self.assertIn("v2.finish", self._names("v2ActiveEraser"))
        self.assertIn("legacy.finishStroke", self._names("v2IdleEraser"))

    def test_every_path_still_commits_exactly_once(self) -> None:
        for key in ("v2Active", "v2Idle", "adapterAbsent",
                    "v2ActiveEraser", "v2IdleEraser"):
            self.assertEqual(1, self._names(key).count("commitStroke"), msg=key)


class HistoricalEvidenceTests(unittest.TestCase):
    """§7.1. U2's Alpha clearance rests on the U2-VF rows; the harness that
    produced them is not editable by a later unit."""

    def test_the_u2vf_harness_is_byte_identical_to_the_recorded_hash(self) -> None:
        got = hashlib.sha256(U2VF_HARNESS.read_bytes()).hexdigest()
        self.assertEqual(U2VF_SHA256, got)

    def test_u3v_ships_its_own_harness_rather_than_editing_that_one(self) -> None:
        self.assertTrue(HARNESS.is_file())
        self.assertNotEqual(HARNESS.resolve(), U2VF_HARNESS.resolve())


class HarnessDisciplineTests(unittest.TestCase):
    """A handful of properties of the harness file itself.

    These ARE spelling checks, and they are labelled as such: each one guards a
    trap that has already cost this programme a run, and none of them is offered
    as evidence that a gate works -- that is what every test above is for.
    """

    def setUp(self) -> None:
        # Comments stripped. A word-ban test that matched the comment
        # EXPLAINING the ban has caught this programme out four separate times.
        self.code = code_of(HARNESS)

    def test_it_does_not_weaken_the_browser_timer_to_flatter_a_benchmark(self) -> None:
        self.assertNotIn("privacy.reduceTimerPrecision", self.code)

    def test_it_measures_the_clock_rather_than_assuming_its_resolution(self) -> None:
        self.assertIn("timerResolutionMs", self.code)

    def test_it_does_not_persist_the_v2_flag(self) -> None:
        # The flag's only guarantee is that a reload returns to Legacy. A test
        # harness that stored it would silently change the owner's default.
        self.assertNotIn("localStorage", self.code)
        self.assertNotIn("sessionStorage", self.code)

    def test_it_posts_nothing_off_the_origin(self) -> None:
        # Studio's CSP ends with `connect-src 'self'`; results are delivered as
        # a download instead. Discovered by trying the other way in U2-VF.
        self.assertNotIn("XMLHttpRequest", self.code)
        self.assertNotIn("navigator.sendBeacon", self.code)

    def test_it_reads_the_whole_gl_buffer_rather_than_a_fixed_corner(self) -> None:
        self.assertIn("cv.width", self.code)
        self.assertIn("cv.height", self.code)

    def test_the_owner_card_asks_the_load_bearing_question(self) -> None:
        # The human gate, and "technically functional" is never a substitute.
        #
        # U3-R MOVED IT. U3-V asked "would you voluntarily keep painting with V2
        # in this state?" and the owner answered yes, which is why V2 reached
        # CONDITIONAL PASS. U3-R §24 asks a strictly stronger question, because
        # the remaining weakness was the one blocking the next unit: whether the
        # engine is ready to WIDEN. Keeping the old wording would have let a
        # softer answer clear a harder gate.
        self.assertIn("Would you now describe V2 raster painting as ready to widen?",
                      self.code)

    def test_the_owner_card_asks_every_question(self) -> None:
        for token in ("beadsGone", "evenThroughCurves", "lineArtUnchanged",
                      "catchup", "eraserAgrees", "pressureModes", "vsLegacy",
                      "readyToWiden"):
            self.assertIn(f'["{token}"', self.code,
                          msg=f"feel question {token} is missing")

    def test_the_card_still_asks_whether_the_accepted_build_regressed(self) -> None:
        # U3-V's acceptance is a thing U3-R can break. The card must ask.
        self.assertIn("still feel as good as the accepted build", self.code)

    def test_synthetic_pressure_is_never_labelled_as_stylus_feel(self) -> None:
        # §17 forbids the promotion. The label travels inside the row so it
        # cannot be separated from the number it qualifies.
        self.assertIn("NOT stylus feel", self.code)

    def test_the_smoothing_probe_declares_itself_kernel_proven(self) -> None:
        # Raw and stabilized cannot be produced through a real stroke without a
        # settings surface, which §13 forbids in this unit.
        self.assertIn("kernel-proven", self.code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3V_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
