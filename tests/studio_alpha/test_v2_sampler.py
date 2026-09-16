"""Brush V2 arc-length sampling and exact endpoints. V2-03, spec BE3.

`OVERNIGHT_HANDOFF_V2_02_AND_GPU_2026-08-25.md` §4.3 names six pieces of
evidence. Each has a class below and the mapping is stated, so coverage can be
checked rather than trusted.

WHY THE FREQUENCY COMPARISON IS EXACT AND NOT TOLERANCED. §4.3 asks for "the
same geometric path sampled at materially different event frequencies" to agree
"within the contract's named tolerance". The named tolerance here is ZERO, and
that is a claim about the fixtures rather than about the arithmetic:
`oracle/fixtures.js:8-14` records the trap -- sampling a curve at 15 points and
at 120 points does not vary the event rate, it varies the GEOMETRY, because a
coarse polyline cuts every corner the fine one follows. The fixtures fix their
vertices first and subdivide between them, so every rate walks the identical
path. Under identical geometry an arc-length rule with carried debt must place
marks at identical distances, and a tolerance would be hiding something.

THE MODEL IS LEGACY'S AND THE CODE IS NOT. BE3, BE7 and BE9 solved this once;
the review record §1 lists the four decisions carried and why re-deriving them
would mean re-discovering their defects.

Review: `Evidence/source-review/V2-03-arc-length-sampling.md`.
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

EXPECTED_V2_SAMPLER_TESTS = 32

PROBE = Path(__file__).with_name("v2_03_probe.js")
MODULE = (APP_ROOT / "forge_studio" / "frontend" / "v2" / "sampler.js")
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


class PlacementFollowsDistanceNotEventRateTests(_Probed):
    """§4.3 evidence 1 — and §20 Correctness 1."""

    def test_every_rate_places_the_same_number_of_marks(self) -> None:
        counts = self.report["frequency"]["counts"]
        self.assertEqual([counts[0]] * 4, counts,
                         "30, 60, 120 and 240 Hz disagreed on how many marks "
                         "the same path earns")

    def test_every_rate_places_them_in_exactly_the_same_positions(self) -> None:
        """EXACT, not toleranced. See the module docstring."""

        self.assertTrue(self.report["frequency"]["allPositionsIdentical"])

    def test_the_path_actually_produced_marks(self) -> None:
        """"Too clean is the tell." A sampler that emitted nothing would
        satisfy both assertions above."""

        self.assertGreater(self.report["frequency"]["markCount"], 50)


class TheDebtSurvivesADispatchBoundaryTests(_Probed):
    """§4.3 evidence 2.

    The same samples delivered as one coalesced dispatch, as dispatches of
    three, and as one event each. The carried debt is what makes these agree; a
    sampler that reset it per dispatch would bunch a mark at every boundary.
    """

    def test_one_dispatch_and_many_agree(self) -> None:
        self.assertTrue(self.report["grouping"]["oneMatchesMany"])

    def test_one_dispatch_and_one_event_each_agree(self) -> None:
        self.assertTrue(self.report["grouping"]["oneMatchesPerEvent"])

    def test_the_counts_are_equal_and_not_zero(self) -> None:
        g = self.report["grouping"]
        self.assertEqual(g["oneDispatchCount"], g["manyDispatchCount"])
        self.assertEqual(g["oneDispatchCount"], g["perEventCount"])
        self.assertGreater(g["oneDispatchCount"], 50)


class EndpointsAreExactTests(_Probed):
    """§4.3 evidence 3, §5.1, and §20 Input 5."""

    def test_a_long_stroke_starts_exactly_at_the_contact_point(self) -> None:
        for key in ("line", "corner"):
            with self.subTest(fixture=key):
                e = self.report["endpoints"][key]
                self.assertEqual(e["expectedFirst"], e["firstAt"])
                self.assertEqual("begin", e["firstSource"])

    def test_a_long_stroke_ends_exactly_where_the_pen_lifted(self) -> None:
        for key in ("line", "corner"):
            with self.subTest(fixture=key):
                e = self.report["endpoints"][key]
                self.assertEqual(e["expectedLast"], e["lastAt"])

    def test_a_stroke_that_does_not_divide_evenly_still_ends_exactly(self) -> None:
        """THE ONE THAT MATTERS. The fixtures above are whole numbers of gaps,
        so their last path mark lands on the endpoint by arithmetic and a
        severed flush would pass. 118 px against a 3.0 px gap leaves a
        remainder, so only the finish rule can place the end."""

        u = self.report["unevenEndpoint"]
        self.assertEqual(u["expectedLast"], u["lastAt"])
        self.assertEqual("finish", u["lastSource"])

    def test_the_final_gap_is_allowed_to_be_shorter_than_the_spacing(self) -> None:
        """The consequence of unconditional exactness, and it is correct: a
        stroke ends where the pen lifted, not where the arithmetic last landed.
        Legacy lets its spacing debt veto this; V2 does not -- review record
        §2."""

        u = self.report["unevenEndpoint"]
        self.assertGreater(u["lastGapPx"], 0)
        self.assertLess(u["lastGapPx"], 3.0)

    def test_a_flick_shorter_than_one_gap_still_reaches_its_end(self) -> None:
        """Two px of travel against a 5 px gap. Without the endpoint rule this
        is a dot, and the owner's flick disappears."""

        f = self.report["endpoints"]["flick"]
        self.assertEqual([100, 100], f["firstAt"])
        self.assertEqual([102, 100], f["lastAt"])
        self.assertEqual("finish", f["lastSource"])

    def test_a_tap_places_exactly_one_mark(self) -> None:
        """Down and up in one place. The endpoint is the contact point, already
        marked, and a second mark there would deposit twice."""

        t = self.report["endpoints"]["tap"]
        self.assertEqual(1, t["count"])
        self.assertEqual([160, 160], t["at"])
        self.assertEqual("begin", t["source"])


class AShortMoveKeepsItsDistanceTests(_Probed):
    """§4.3: "allow a short sub-spacing move to emit no intermediate dab
    without losing its distance" — the case a residual cannot express and a
    debt can."""

    def test_twenty_one_pixel_moves_equal_one_twenty_pixel_move(self) -> None:
        s = self.report["subSpacing"]
        self.assertEqual(s["strideCount"], s["crawlCount"])

    def test_and_they_land_in_the_same_places(self) -> None:
        self.assertTrue(self.report["subSpacing"]["samePlacements"])

    def test_the_comparison_is_not_between_two_empty_strokes(self) -> None:
        self.assertGreater(self.report["subSpacing"]["crawlCount"], 1)


class DegenerateInputIsBoundedTests(_Probed):
    """§4.3 evidence 6."""

    def test_a_repeated_position_emits_only_the_opening_mark(self) -> None:
        d = self.report["degenerate"]
        self.assertEqual(1, d["repeatedCount"])

    def test_a_repeated_position_produces_no_nan(self) -> None:
        self.assertTrue(self.report["degenerate"]["repeatedFinite"])

    def test_samples_sharing_a_timestamp_still_place_marks(self) -> None:
        """Time is not the placement authority; distance is. Three samples at
        one timestamp that move 100 px must still earn marks."""

        d = self.report["degenerate"]
        self.assertTrue(d["sharedTimeAdvances"])
        self.assertTrue(d["sharedTimeFinite"])
        self.assertGreater(d["sharedTimeCount"], 1)


class StationaryInputManufacturesNoMotionTests(_Probed):
    """§4.3: "define stationary pressure/time evolution explicitly rather than
    manufacturing spatial motion."

    A pointer held still travels zero distance, so no mark is due. Airbrush
    buildup is time-driven and is §9.4's — a different unit. The sampler
    reports the elapsed time so that unit can read it, and places nothing.
    """

    def test_holding_still_places_nothing_beyond_the_opening_mark(self) -> None:
        self.assertEqual(1, self.report["stationary"]["dabCount"])

    def test_the_elapsed_time_is_reported_rather_than_discarded(self) -> None:
        self.assertEqual(200000, self.report["stationary"]["stationaryUs"])


class DynamicsAreInterpolatedAtThePlacementTests(_Probed):
    """§9.2: interpolate at every placed mark rather than copying the latest
    event."""

    def test_pressure_rises_monotonically_along_a_ramp(self) -> None:
        self.assertTrue(self.report["interpolation"]["monotonic"])

    def test_the_ends_carry_the_measured_values(self) -> None:
        i = self.report["interpolation"]
        self.assertAlmostEqual(0.2, i["firstPressure"], places=4)
        self.assertAlmostEqual(1.0, i["lastPressure"], places=4)

    def test_interior_marks_do_not_carry_the_newest_sample(self) -> None:
        """The discriminating case. Copying the latest event would make every
        interior mark on a one-segment ramp read the far end's pressure."""

        self.assertIs(False, self.report["interpolation"]["copiedLatestSample"])


class SpacingFollowsTheTipAlongTravelTests(_Probed):
    """§4.3: "spacing based on the resolved footprint/travel extent, including
    anisotropic tips".

    BE7's finding: "a chisel travelling along its long axis presents several
    times the width it presents travelling across it, so one number either
    bunches the dabs on one heading or leaves gaps on the other" — most of why
    "the flat brushes behave weirdly when doing turns".
    """

    def test_a_tip_twice_as_long_earns_twice_the_gap_along_its_axis(self) -> None:
        a = self.report["anisotropy"]
        self.assertAlmostEqual(2.0, a["alongGap"] / a["acrossGap"], places=6)

    def test_and_therefore_about_half_the_marks(self) -> None:
        a = self.report["anisotropy"]
        ratio = a["acrossAxisCount"] / a["alongAxisCount"]
        self.assertGreater(ratio, 1.7)
        self.assertLess(ratio, 2.1)

    def test_the_sampler_owns_no_tip_table(self) -> None:
        """The extent is INJECTED. `alongExtentFor` in Legacy reads
        `S.brushPreset`, `TIP_ASPECT`, `TIP_EXTENT` and `S.brushRatio`; a
        sampler that owned those would be the second place tips are
        described."""

        code = code_of(MODULE)
        for token in ("TIP_ASPECT", "TIP_EXTENT", "brushPreset", "brushRatio"):
            with self.subTest(token=token):
                self.assertNotIn(token, code)


class TheGapIsMeasuredNotAssumedTests(_Probed):
    """A defect this package found in its own first draft, kept as a guard.

    `gapPx` was reported from the spacing DEBT, which at emit time is only the
    remainder of the gap whenever the previous mark fell in an earlier segment.
    A chisel whose gap should have doubled reported an unchanged 5 while
    correctly placing half as many marks: the counts were right and the field
    was wrong, which is the worst combination because the number looks measured.

    Legacy has the same construction (`canvas-core.js:3053`), so its value
    carries the same under-statement into BE17's overlap divisor. Not repaired
    there — Legacy is frozen — and not reproduced here.
    """

    def test_the_reported_gap_matches_the_distance_between_marks(self) -> None:
        a = self.report["anisotropy"]
        # 20 px tip, 0.25 spacing, extent 2 along the axis -> 10 px.
        self.assertAlmostEqual(10.0, a["alongGap"], places=6)
        self.assertAlmostEqual(5.0, a["acrossGap"], places=6)


class NoFieldIsSilentlyDroppedTests(_Probed):
    def test_the_declared_and_produced_fields_agree(self) -> None:
        e = self.report["enumeration"]
        self.assertEqual(e["declared"], e["produced"])


class TheSamplerRendersNothingTests(unittest.TestCase):
    """It decides WHERE, never WHAT. Coverage is V2-05's."""

    def test_it_contains_no_rendering_or_canvas_machinery(self) -> None:
        code = code_of(MODULE)
        for token in ("getContext", "ImageData", "putImageData", "alphaMap",
                      "document.", "canvas", "globalAlpha", "fillRect"):
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
        self.assertIn("v2/sampler.js", html)
        self.assertNotIn("v2/scratchpad.js", html)

    def test_it_still_has_the_code_the_guards_are_about(self) -> None:
        code = code_of(MODULE)
        self.assertIn("window.StudioBrushSamplerV2", code)
        self.assertIn("ArcSampler.prototype.push", code)
        self.assertIn("ArcSampler.prototype.finish", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_SAMPLER_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
