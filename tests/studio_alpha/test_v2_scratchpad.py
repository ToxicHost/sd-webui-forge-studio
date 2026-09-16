"""The Brush V2 standalone kernel, end to end. V2-06, overnight handoff §4.6.

THE EXECUTION GUARD IS WHAT THIS PACKAGE IS FOR. §20 Performance 2 requires a
benchmark to REFUSE to report timing if the real listener was not reached,
coordinates were outside the document, counters did not advance, or pixels did
not change -- and this repository has the scar that rule is written from. BE0-E's
own header records a programme that "once reported a 0.0 ms dispatch for a
stroke that never reached the engine".

So `run()` returns `timings: null` -- never zero -- with a named refusal. A zero
reads as "instant", which is exactly the confident wrong number the guard
prevents.

WHAT THIS SUITE MAY NOT CLAIM, stated because the temptation is real: nothing
here says anything about Canvas dispatch performance. §4.6 is explicit -- "do
not claim Canvas dispatch improvement from a standalone-kernel benchmark." The
BE0-E baseline measures a real pointermove through the shipping app including
compositing, which is not present here at all.

CORRECTED, not deleted: this docstring used to put a figure on that -- "96-99%
of that number". The Canvas integration gate found the figure is STALE. It comes
from BE0-E at HEAD `06fc740f`, and eighteen commits have touched
`canvas-core.js` since, including `6443616b` (BE8) which was written to attack
that very measurement. Nobody should quote the percentage again until it is
re-measured. See `Evidence/source-review/V2-canvas-integration-gate.md` §6.3.

Review: `Evidence/source-review/V2-06-scratchpad-and-gate2.md`.
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

EXPECTED_V2_SCRATCHPAD_TESTS = 35

PROBE = Path(__file__).with_name("v2_06_probe.js")
MODULE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "scratchpad.js"
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


class TheWholeKernelRunsTests(_Probed):
    """§6.1's pipeline, composed and counted stage by stage."""

    def test_every_stage_advanced_its_counter(self) -> None:
        """A stage that silently did nothing would report zero here, and the
        run would still look like a success."""

        e = self.report["endToEnd"]
        self.assertGreater(e["dispatches"], 1)
        self.assertGreater(e["samples"], e["dispatches"])
        self.assertGreater(e["marks"], e["samples"])
        self.assertEqual(e["marks"], e["contributions"])
        self.assertGreater(e["paintedPixels"], 1000)

    def test_the_target_gained_the_pixels_the_buffer_covered(self) -> None:
        e = self.report["endToEnd"]
        self.assertEqual(0, e["targetPixelsBefore"])
        self.assertEqual(e["paintedPixels"], e["targetPixelsAfter"])

    def test_coalesced_samples_survived_the_whole_way(self) -> None:
        e = self.report["endToEnd"]
        self.assertEqual(e["samples"], e["coalescedSamples"])

    def test_a_successful_run_reports_timings(self) -> None:
        self.assertTrue(self.report["endToEnd"]["hasTimings"])
        self.assertIsNone(self.report["endToEnd"]["refusal"])

    def test_the_total_is_the_sum_of_the_stages(self) -> None:
        """So a stage that stops being measured cannot hide inside the total."""

        e = self.report["endToEnd"]
        self.assertEqual(["filter", "input", "merge", "render", "total"],
                         e["timingStages"])
        self.assertTrue(e["totalIsSumOfStages"])


class DirtyBoundsSurviveTheSeamTests(_Probed):
    """U1. §16.3 requires dirty bounds to travel "from the first coverage
    primitive THROUGH COMMIT", so the kernel's own boundary has to carry them.

    A pipeline that computed exact bounds and dropped them at its edge would
    pass every test inside `coverage.js` and hand an integrator nothing."""

    def test_the_run_reports_a_dirty_region(self) -> None:
        dirty = self.report["endToEnd"]["dirty"]
        self.assertFalse(dirty["empty"])
        self.assertGreater(dirty["area"], 0)

    def test_the_region_is_smaller_than_the_document(self) -> None:
        """Otherwise "bounded" would be true and useless."""

        e = self.report["endToEnd"]
        self.assertLess(e["dirty"]["area"], e["documentPixels"])

    def test_the_region_contains_every_painted_pixel(self) -> None:
        e = self.report["endToEnd"]
        self.assertGreaterEqual(e["dirty"]["area"], e["paintedPixels"])

    def test_the_kernel_visits_only_bounded_work(self) -> None:
        """Visits must be a whole number of bounded passes over the region, not
        a multiple of the document."""

        e = self.report["endToEnd"]
        self.assertEqual(0, e["visitedPixels"] % e["dirty"]["area"])
        self.assertGreater(e["visitedPixels"], 0)


class TimingsAreWithheldWhenWorkCannotBeProvenTests(_Probed):
    """§20 Performance 2, one test per named condition."""

    def test_a_run_with_no_samples_refuses(self) -> None:
        g = self.report["guard"]
        self.assertEqual("no-samples-reached-the-kernel", g["noSamplesRefusal"])
        self.assertIsNone(g["noSamplesTimings"])

    def test_a_stroke_outside_the_document_refuses(self) -> None:
        g = self.report["guard"]
        self.assertEqual("every-sample-fell-outside-the-document",
                         g["outsideRefusal"])
        self.assertIsNone(g["outsideTimings"])

    def test_a_stroke_that_changed_no_pixels_refuses(self) -> None:
        g = self.report["guard"]
        self.assertEqual("no-pixels-changed", g["noPixelsRefusal"])
        self.assertIsNone(g["noPixelsTimings"])

    def test_the_refusal_is_null_timings_and_not_a_zero(self) -> None:
        """A zero reads as "instant" and is the confident wrong number the
        guard exists to prevent. BE0-E's header records a programme that
        reported 0.0 ms for a stroke that never reached the engine."""

        g = self.report["guard"]
        for key in ("noSamplesTimings", "outsideTimings", "noPixelsTimings"):
            with self.subTest(case=key):
                self.assertIsNone(g[key])

    def test_a_refused_run_still_reports_diagnostics(self) -> None:
        """The numbers that say WHY are the ones a reader needs. A refusal
        without them would be a different kind of unhelpful."""

        self.assertTrue(self.report["guard"]["noPixelsStillDiagnoses"])

    def test_a_tap_is_not_refused(self) -> None:
        """One contact, one mark. A guard that refused a legitimate minimal
        stroke would be worse than no guard -- it would teach its reader to
        ignore refusals."""

        g = self.report["guard"]
        self.assertIsNone(g["tapRefusal"])
        self.assertEqual(1, g["tapMarks"])
        self.assertTrue(g["tapHasTimings"])


class EverySmoothingModeReachesTheRendererTests(_Probed):
    def test_the_mode_travels_end_to_end(self) -> None:
        m = self.report["modes"]
        self.assertEqual("raw", m["raw"])
        self.assertEqual("natural", m["natural"])
        self.assertEqual("stabilized", m["stabilized"])

    def test_all_three_paint(self) -> None:
        for painted in self.report["modes"]["painted"]:
            self.assertGreater(painted, 1000)

    def test_more_smoothing_shortens_the_path_it_paints(self) -> None:
        """Raw follows the tremor, Stabilized cuts inside it, so the swept area
        falls. A rounding of the same path, not a different one."""

        coverage = self.report["modes"]["coverage"]
        self.assertGreater(coverage[0], coverage[2])


class DispatchGroupingIsInvisibleEndToEndTests(_Probed):
    """The property V2-02 established at the sample layer, carried through the
    whole kernel to the merged pixels."""

    def test_every_grouping_produces_the_same_coverage(self) -> None:
        self.assertTrue(self.report["grouping"]["allEqual"])

    def test_the_comparison_is_not_between_empty_runs(self) -> None:
        self.assertTrue(self.report["grouping"]["nonZero"])


class ReplayIsByteIdenticalTests(_Probed):
    """§20 Correctness 10, demonstrated end to end rather than per module."""

    def test_two_runs_of_one_recording_agree_on_every_pixel(self) -> None:
        r = self.report["replay"]
        self.assertIsNone(r["refusal"])
        self.assertTrue(r["equal"])


class DynamicsReachTheFootprintTests(_Probed):
    """§6.1 evaluates dynamics after resampling, and the resolved size has to
    arrive at the renderer or the rule is decorative."""

    def test_a_rule_is_applied_at_every_mark(self) -> None:
        d = self.report["dynamics"]
        self.assertEqual(d["withRuleMarks"], d["withRuleApplied"])

    def test_no_rules_means_no_evaluation(self) -> None:
        self.assertEqual(0, self.report["dynamics"]["withoutRuleApplied"])

    def test_a_pressure_to_size_rule_changes_the_mark(self) -> None:
        """At pressure 0.3 with a 0.2-1.0 range the footprint shrinks, and the
        coverage has to fall with it."""

        d = self.report["dynamics"]
        self.assertLess(d["withRuleCoverage"], d["withoutRuleCoverage"] / 2)

    def test_the_placement_is_unchanged_by_the_rule(self) -> None:
        """Dynamics drive the FOOTPRINT, not where marks land -- §6.1 puts them
        after resampling for exactly this reason."""

        d = self.report["dynamics"]
        self.assertEqual(d["withoutRuleMarks"], d["withRuleMarks"])


class SelectionAndEraseReachTheMergeTests(_Probed):
    def test_a_fifty_percent_selection_halves_the_merged_peak(self) -> None:
        m = self.report["merge"]
        self.assertAlmostEqual(m["plainPeak"] / 2, m["selectedPeak"], delta=1)

    def test_erasing_reduces_a_pre_painted_target(self) -> None:
        m = self.report["merge"]
        self.assertLess(m["erasedMin"], 255)
        self.assertIsNone(m["erasedRefusal"])


class TheModelIsCarriedThroughTests(_Probed):
    def test_the_overlap_divisor_reaches_the_diagnostics(self) -> None:
        self.assertAlmostEqual(5.0, self.report["model"]["overlapK"], places=6)

    def test_the_effective_flow_is_the_normalised_root(self) -> None:
        self.assertGreater(self.report["model"]["fEff"], 0)
        self.assertLess(self.report["model"]["fEff"], 0.5)

    def test_the_three_performance_modes_are_declared(self) -> None:
        """§16.4. Declared and inert -- they may change preview resolution and
        cadence and must not change canonical pixels, and there is no preview
        here to vary."""

        self.assertEqual(["auto", "responsive", "quality"],
                         self.report["model"]["performanceModes"])


class TheKernelStandsAloneTests(unittest.TestCase):
    """§4.6: "no dependency on the shipping Canvas compositor"."""

    LEGACY = ("StudioCore", "canvas-core", "alphaMap", "plotTo", "S.stroke",
              "composite", "drawTarget", "getContext", "document.")

    def test_it_references_nothing_from_the_shipping_engine(self) -> None:
        code = code_of(MODULE)
        for token in self.LEGACY:
            with self.subTest(token=token):
                self.assertNotIn(token, code)

    def test_the_page_does_not_load_it(self) -> None:
        self.assertNotIn("v2/scratchpad.js",
                         INDEX_HTML.read_text(encoding="utf-8"))

    def test_it_still_contains_what_the_guards_are_about(self) -> None:
        code = code_of(MODULE)
        self.assertIn("window.StudioBrushScratchpadV2", code)
        self.assertIn("function run", code)
        self.assertIn("function replayMatches", code)

    def test_it_composes_all_five_kernel_modules(self) -> None:
        """A kernel surface that quietly dropped one would still run."""

        code = code_of(MODULE)
        for name in ("StudioBrushV2", "StudioBrushInputV2",
                     "StudioBrushSamplerV2", "StudioBrushFiltersV2",
                     "StudioBrushDynamicsV2", "StudioBrushCoverageV2"):
            with self.subTest(module=name):
                self.assertIn(name, code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_SCRATCHPAD_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
