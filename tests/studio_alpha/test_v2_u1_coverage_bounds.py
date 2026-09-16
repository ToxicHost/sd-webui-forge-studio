"""U1 — bounded V2 coverage. Dirty regions from the first primitive.

§16.3 of the spec: "V2 MUST emit dirty tiles/bounds from the first coverage
primitive through commit. Canvas MUST NOT perform full-document compositing for
every ordinary pointer move."

WHY THIS IS A CORRECTNESS UNIT AND NOT A PERFORMANCE ONE. The engine V2 replaces
is already bounded -- BE8 gave `alphaMapToImageData` a rect and `commitStroke`
walks `S.stroke.dirty`. V2 as built was NOT: `read()` allocated a
document-sized array per call and `merge`, `totalCoverage` and `paintedPixels`
each called it. Integrating that would have been a measurable regression
presented as an upgrade.

THE MEASUREMENTS HERE ARE COUNTS, NOT CLOCKS. Wall-clock cannot tell "bounded"
from "unbounded but the allocator was warm", and a threshold in milliseconds
fails on someone else's laptop for no engineering reason. So the buffer counts
what it visited, extracted and allocated, and the tests assert on those.

Review: `Evidence/source-review/U1-bounded-coverage.md`.
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

EXPECTED_V2_U1_TESTS = 52

PROBE = Path(__file__).with_name("v2_u1_probe.js")
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


class TheRegionIsHalfOpenTests(_Probed):
    """`x0`/`y0` inclusive, `x1`/`y1` exclusive — the same convention as every
    JavaScript slice, so width is a subtraction with no off-by-one."""

    def test_a_one_pixel_tap_is_exactly_one_by_one(self) -> None:
        tap = self.report["tap"]
        self.assertEqual(1, tap["width"])
        self.assertEqual(1, tap["height"])
        self.assertEqual(1, tap["area"])
        self.assertFalse(tap["empty"])

    def test_an_empty_region_is_empty_and_not_a_fake_one_by_one(self) -> None:
        """The tempting shortcut is clamping an off-document stroke to a 1x1 at
        the origin. That reports a painted pixel that does not exist, and a
        consumer would upload, composite or journal it."""

        empty = self.report["emptyRegion"]
        self.assertTrue(empty["empty"])
        self.assertEqual(0, empty["area"])
        self.assertEqual(0, empty["width"])
        self.assertEqual(0, empty["height"])

    def test_width_is_x1_minus_x0(self) -> None:
        self.assertEqual(10, self.report["regionMath"]["halfOpenWidth"])

    def test_union_covers_both(self) -> None:
        union = self.report["regionMath"]["union"]
        self.assertEqual({"x0": 10, "y0": 5, "x1": 40, "y1": 20},
                         {k: union[k] for k in ("x0", "y0", "x1", "y1")})

    def test_clipping_into_the_document_bounds(self) -> None:
        clipped = self.report["regionMath"]["clipped"]
        self.assertEqual({"x0": 0, "y0": 0, "x1": 10, "y1": 10},
                         {k: clipped[k] for k in ("x0", "y0", "x1", "y1")})

    def test_a_region_clipped_away_becomes_empty(self) -> None:
        self.assertTrue(self.report["regionMath"]["clippedAway"]["empty"])

    def test_expanding_by_an_empty_box_is_a_no_op(self) -> None:
        """Not a collapse. A degenerate box must not shrink a real region."""

        self.assertTrue(self.report["regionMath"]["expandingByEmptyIsNoop"])

    def test_an_empty_region_reports_zero_area(self) -> None:
        self.assertTrue(self.report["regionMath"]["emptyIsEmpty"])
        self.assertTrue(self.report["regionMath"]["emptyHasZeroArea"])

    def test_region_for_returns_a_copy(self) -> None:
        """A caller that keeps the returned region must not be able to move the
        buffer's own bounds by writing to it."""

        self.assertTrue(self.report["regionMath"]["regionForIsACopy"])


class BoundsEqualTheNonZeroPixelBboxTests(_Probed):
    """Not the tip's bounding box — the pixels that actually ended non-zero.
    The probe computes the true bbox by scanning every pixel and compares."""

    def test_a_stamp_agrees_with_the_true_bbox(self) -> None:
        self.assertTrue(self.report["stampAgrees"])

    def test_a_hard_sweep_agrees(self) -> None:
        self.assertTrue(self.report["sweepAgrees"])

    def test_a_soft_sweep_agrees(self) -> None:
        """The soft case is the one that can disagree: the rim deposits a value
        that rounds to zero, and a bbox taken from the geometry would include
        pixels the reader sees as blank."""

        self.assertTrue(self.report["softSweepAgrees"])

    def test_flow_100_bounds_agree_with_the_accumulator_running(self) -> None:
        """U3-R2F C2 REMOVED THE SHORT CIRCUIT THIS WAS NAMED FOR.

        The bound this test exists to check is unchanged -- the dirty region
        must still equal the true non-zero bbox -- but the path reaching it is
        no longer a Flow-1 special case. `assertFalse(accumulating)` asserted
        the branch that produced the medial-axis crease; what matters here is
        that the bounds are exact whichever branch runs, so the assertion moves
        to the accumulator being live and the bounds still agreeing."""

        flow = self.report["flow100"]
        self.assertTrue(flow["accumulating"])
        self.assertTrue(flow["agrees"])

    def test_disconnected_islands_union_to_cover_both(self) -> None:
        islands = self.report["islands"]
        self.assertTrue(islands["agrees"])
        self.assertGreater(islands["dirty"]["area"], 9000)


class EveryDocumentEdgeTests(_Probed):
    def test_each_edge_agrees_with_the_true_bbox(self) -> None:
        for name, case in self.report["edges"].items():
            with self.subTest(edge=name):
                self.assertTrue(case["agrees"])

    def test_no_edge_case_reports_outside_the_document(self) -> None:
        for name, case in self.report["edges"].items():
            with self.subTest(edge=name):
                self.assertTrue(case["withinDoc"])

    def test_a_partially_outside_stamp_bounds_only_the_visible_part(self) -> None:
        partial = self.report["partial"]
        self.assertTrue(partial["agrees"])
        self.assertEqual(0, partial["dirty"]["x0"])


class OffDocumentIsObservablyEmptyTests(_Probed):
    """A stroke that missed the document entirely. §20 Performance 2's
    "coordinates were outside the document", at the coverage layer."""

    def test_a_stroke_entirely_outside_is_empty(self) -> None:
        self.assertTrue(self.report["outside"]["dirty"]["empty"])

    def test_it_merges_nothing(self) -> None:
        self.assertEqual(0, self.report["outside"]["merged"])

    def test_it_reports_no_coverage(self) -> None:
        self.assertEqual(0, self.report["outside"]["painted"])
        self.assertEqual(0, self.report["outside"]["total"])

    def test_it_allocates_nothing_proportional_to_the_document(self) -> None:
        """A 2048x2048 buffer, two stamps that missed, and the metrics and merge
        that followed must not have touched a single pixel."""

        stats = self.report["outside"]["stats"]
        self.assertEqual(0, stats["scratchBytes"])
        self.assertEqual(0, stats["visitedPixels"])

    def test_the_contributions_are_still_counted(self) -> None:
        """The dabs happened. They simply landed nowhere, and the counter must
        not pretend the stroke never ran."""

        self.assertEqual(2, self.report["outside"]["contributions"])


class AZeroCoverageWriteDirtiesNothingTests(_Probed):
    """Flow 0 deposits at every pixel under the tip and leaves them all reading
    zero. Expanding on the ATTEMPT rather than the RESULT would make the bounds
    the tip's bounding box, which is the thing this unit exists to stop."""

    def test_flow_zero_dirties_nothing(self) -> None:
        self.assertTrue(self.report["zeroFlow"]["dirty"]["empty"])

    def test_the_deposits_were_genuinely_attempted(self) -> None:
        """Otherwise this test would pass because the loop never ran."""

        self.assertGreater(self.report["zeroFlow"]["touched"], 100)
        self.assertEqual(1, self.report["zeroFlow"]["contributions"])

    def test_nothing_is_painted(self) -> None:
        self.assertEqual(0, self.report["zeroFlow"]["painted"])


class BoundedWorkDoesNotScaleWithDocumentAreaTests(_Probed):
    """The same stroke geometry on 1 MP, 16.8 MP and 24 MP documents."""

    def test_the_same_stroke_dirties_the_same_region_at_every_size(self) -> None:
        regions = [
            {k: s["dirty"][k] for k in ("x0", "y0", "x1", "y1")}
            for s in self.report["sizes"]
        ]
        self.assertEqual(1, len({json.dumps(r, sort_keys=True) for r in regions}))

    def test_visited_pixels_are_identical_at_every_size(self) -> None:
        """This is the assertion that would fail on an unbounded algorithm, and
        it is a count rather than a clock so it fails the same way everywhere."""

        visited = {s["stats"]["visitedPixels"] for s in self.report["sizes"]}
        self.assertEqual(1, len(visited))

    def test_the_extraction_is_identical_at_every_size(self) -> None:
        extracted = {s["extractedPixels"] for s in self.report["sizes"]}
        scratch = {s["stats"]["scratchBytes"] for s in self.report["sizes"]}
        self.assertEqual(1, len(extracted))
        self.assertEqual(1, len(scratch))

    def test_the_coverage_itself_is_identical_at_every_size(self) -> None:
        self.assertEqual(1, len({s["painted"] for s in self.report["sizes"]}))
        self.assertEqual(1, len({s["total"] for s in self.report["sizes"]}))

    def test_only_the_base_buffer_scales(self) -> None:
        """Stated rather than hidden: a full-document coverage surface is
        inherent to the design and is what Legacy's alphaMap costs too. What
        must not scale is the WORK."""

        sizes = self.report["sizes"]
        self.assertEqual(3, len({s["stats"]["bufferBytes"] for s in sizes}))
        self.assertLess(sizes[0]["stats"]["bufferBytes"],
                        sizes[1]["stats"]["bufferBytes"])


class MetricsDoNotMaterialiseAFullCopyTests(_Probed):
    """Under the old API `paintedPixels`, `totalCoverage` and `merge` each
    called `read()`, allocating a document-sized array every time — three
    full-document allocations to describe one small stamp."""

    def test_no_metric_extracts_at_all(self) -> None:
        metrics = self.report["metrics"]
        self.assertEqual(0, metrics["extractionsBefore"])
        self.assertEqual(0, metrics["extractionsAfter"])

    def test_no_scratch_is_allocated_by_metrics(self) -> None:
        self.assertEqual(0, self.report["metrics"]["scratchBytes"])

    def test_each_metric_visits_only_the_dirty_region(self) -> None:
        """Three calls over a 400-pixel region is 1,200 visits. On a 1024x1024
        document the unbounded version would have visited 3,145,728."""

        metrics = self.report["metrics"]
        self.assertEqual(3 * metrics["dirtyArea"], metrics["visitedDelta"])


class BoundedMergeIsByteIdenticalTests(_Probed):
    """Outside the dirty region coverage is zero by construction, and the
    unbounded loop's first act on a zero pixel was to `continue`. So bounding it
    changes what is VISITED and nothing that is WRITTEN."""

    def test_bounded_and_whole_document_merges_agree(self) -> None:
        self.assertEqual(0, self.report["mergeDiffers"])

    def test_they_agree_with_a_selection_too(self) -> None:
        """Selection is sampled document-indexed inside a region-bounded loop,
        which is exactly where an indexing slip would show."""

        self.assertEqual(0, self.report["selDiffers"])


class EraseAndPaintShareAFootprintTests(_Probed):
    """§12.5: erase is the same footprint and the same coverage with a different
    target operation. One flag, not a second renderer."""

    def test_the_bounds_are_the_same(self) -> None:
        footprint = self.report["eraseFootprint"]
        self.assertEqual(footprint["paint"], footprint["erase"])


class ClearCannotLeakTests(_Probed):
    def test_clearing_empties_the_region(self) -> None:
        reuse = self.report["reuse"]
        self.assertFalse(reuse["beforeClear"]["empty"])
        self.assertTrue(reuse["afterClear"]["empty"])

    def test_clearing_zeroes_the_coverage(self) -> None:
        """Checked over the WHOLE document, not the dirty region — a clear that
        only reset the bounds would leave coverage behind and pass a bounded
        check."""

        self.assertEqual(0, self.report["reuse"]["afterClearPainted"])

    def test_reuse_reports_only_the_new_region(self) -> None:
        reuse = self.report["reuse"]
        self.assertTrue(reuse["agrees"])
        self.assertEqual(94, reuse["afterReuse"]["x0"])


class ReplayAndRegroupingAgreeTests(_Probed):
    """§20 Correctness 10. The bounds must be as deterministic as the pixels,
    or a replayed stroke would upload a different region."""

    def test_every_grouping_produces_the_same_bounds(self) -> None:
        self.assertTrue(self.report["regrouping"]["boundsEqual"])

    def test_every_grouping_produces_the_same_pixels(self) -> None:
        self.assertEqual(0, self.report["regrouping"]["pixelDiffs"])

    def test_every_grouping_produces_the_same_total(self) -> None:
        r = self.report["regrouping"]
        self.assertEqual(r["totalA"], r["totalB"])
        self.assertEqual(r["totalB"], r["totalC"])

    def test_the_comparison_is_not_between_empty_runs(self) -> None:
        self.assertGreater(self.report["regrouping"]["totalA"], 1000)


class TheExtractionIsOwnedAndReusedTests(_Probed):
    """"One explicitly owned compact extraction with documented lifetime" —
    a fresh array per call is what made the old API allocate four times for one
    merge."""

    def test_two_reads_share_one_backing_array(self) -> None:
        self.assertTrue(self.report["owned"]["sameBacking"])

    def test_it_grows_once_and_then_stops(self) -> None:
        self.assertTrue(self.report["owned"]["grewOnce"])

    def test_mutating_the_extraction_cannot_corrupt_coverage(self) -> None:
        """The view is a copy of the coverage, not a window onto it."""

        self.assertTrue(self.report["owned"]["mutationIsolated"])

    def test_the_extraction_carries_real_coverage(self) -> None:
        self.assertGreater(self.report["owned"]["firstByte"], 0)


class TheUnboundedApiIsGoneTests(_Probed):
    """`read()` was REMOVED rather than redefined. A caller that missed the
    change gets a TypeError instead of silently reading the wrong pixels — the
    old array was document-indexed and the new one is region-local, so a quiet
    redefinition would have been the worst of both."""

    def test_read_was_removed_outright(self) -> None:
        self.assertTrue(self.report["readRemoved"])

    def test_the_api_version_declares_the_bounded_generation(self) -> None:
        self.assertEqual(2, self.report["apiVersion"])

    def test_no_read_path_allocates_a_document_sized_array(self) -> None:
        """The exact shape of the removed allocation, so reintroducing it by
        copying the old line back fails here."""

        code = code_of(MODULE)
        self.assertNotIn("new Uint8Array(this.width * this.height)", code)
        self.assertIn("readRegion", code)

    def test_the_page_loads_it_but_it_is_inert_by_default(self) -> None:
        """CHANGED BY U3, and recorded rather than quietly relaxed.

        Until U3 this asserted the page did not load `coverage.js` at all,
        which was the right invariant while V2 had no consumer. U3 gives it
        one, so the module is loaded -- and the invariant that now carries the
        weight is that the ADAPTER is off by default, which
        `test_u3_canvas_adapter.py` drives rather than spells."""

        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("v2/coverage.js", html)
        # The acceptance surface is still not shipped.
        self.assertNotIn("v2/scratchpad.js", html)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_U1_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
