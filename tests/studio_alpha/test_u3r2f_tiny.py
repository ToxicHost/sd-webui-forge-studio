"""U3-R2F F1 — tiny and subpixel round-tip coverage.

THE DEFECT, STATED CORRECTLY. The handoff describes "radius <= 0.5 can paint
nothing" via `nd >= 1`. That is true and incomplete: the emptiness is the worst
phase of a subpixel-sampling instability, and the instability reaches well above
the empty range. `stamp` and `sweep` asked the radial profile for its value at
ONE point per pixel, so a tip about a pixel across depended violently on where it
landed between pixel centres -- measured across sixteen subpixel phases, total
paint swung 1600% at radius 0.25, 178% at 0.5, 107% at 1.0.

WHY LEGACY LOOKED FINE, AND WHY IT IS NOT THE ORACLE HERE. Legacy point-samples
too, floors the radius at 0.5 too, and rejects `nd >= 1` too. It differs only in
where a pixel's sample point is:

    Legacy   dy = py - ccy            the pixel INDEX is its centre
    V2       dy = y + 0.5 - mark.y    the centre is index + 0.5

Legacy's own comment states its convention. So an axis-aligned stroke on integer
coordinates -- the commonest case there is -- lands exactly ON Legacy's sample
points and exactly BETWEEN V2's. Neither engine touches Canvas2D for the alpha
map, so this is arithmetic and not a browser rasteriser artefact, which answers
the source gate's questions 2 and 3.

V2's half-pixel convention is the standard one and is consistent with its own
dirty rectangles and transfer, so the repair is NOT to flip it: that would move
every stroke in the engine by half a pixel to fix a subpixel case.

THE REPAIR integrates the profile over the pixel square below radius 2, using a
fixed symmetric 4x4 grid with no jitter. A centroid-weighted area model was tried
first and REJECTED: fine at hardness 1, but 81%-118% swing at hardness 0, because
one centroid cannot stand in for a profile that varies steeply across a pixel.

THE THRESHOLD IS 2 AND THAT IS THE EXPENSIVE CHOICE. Switching at 3 or 6 would
leave a smaller seam, but §26 makes "changes ordinary radius >= 2 output
materially" a stop condition. Radius >= 2 is byte-identical, verified against the
pre-repair engine at r2/r3/r4/r6/r27.

INTEGRATION ALONE WAS NOT ENOUGH, AND THE ORACLE IS WHAT SAID SO. Every other
measurement here is V2 against V2 -- swing, parity, monotonicity -- and all of
them passed while the brush was still unusable. Run against Legacy through the
shipping input path:

                      Legacy        V2 before      V2 integrated only
    1 px soft tap     1/255/255     0/0/0          4/56/14
    1 px hard tap     1/255/255     0/0/0          4/188/47
    1 px hard stroke  21/5355/255   1/255/255      44/4628/111
                      (painted px / total alpha / PEAK alpha)

A peak of 14 IS "near alpha 18", and 44 pixels of grey where Legacy paints 21 of
black IS the "two-pixel blur" §9 forbids by name. A disc of radius 0.5 has an
area of 0.785 px, so no placement covers any pixel by more than 78.5% and a tip
on a pixel corner splits that four ways: the box filter is correct and useless.

SO THE RADIUS FLOOR SNAPS AS WELL AS CLAMPS. Below one pixel there is no
sub-pixel shape left to place, so the tip goes on the pixel lattice and the
integration lands it in one row instead of straddling two: the 1 px hard stroke
becomes 21/5291/255 against Legacy's 21/5355/255. Legacy reaches the same place
by accident and pays for it by losing hardness entirely down there; snapping
keeps the profile, so V2's soft 1 px tip is half the ink of its hard one.

NO FADE, AND THAT WAS MEASURED. Blending the snap out between 0.5 and 0.75
looks tidier and made phase swing at radius 0.6 go from 1.2% to 27.6%.

Review: `Evidence/source-review/U3R2F-tiny-and-flow.md`.
Candidates: `Evidence/u3r2f-tiny/f1_visibility_candidates_probe.js`.
Oracle run: `Evidence/u3r2f-tiny/f1_legacy_oracle_probe.js`.
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

EXPECTED_U3R2F_TINY_TESTS = 45

PROBE = Path(__file__).with_name("u3r2f_tiny_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"

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


class NothingIsEmptyTests(unittest.TestCase):
    """§9: every accepted radius paints, at every alignment and angle.

    This is the owner's defect: a 1 px brush drew nothing.
    """

    def test_no_radius_hardness_alignment_or_angle_paints_nothing(self) -> None:
        n = probe()["nonEmpty"]
        self.assertEqual([], n["empties"])

    def test_the_matrix_is_actually_large(self) -> None:
        # A guard that checked three cases could pass while the defect survived.
        self.assertGreaterEqual(probe()["nonEmpty"]["checked"], 1000)


class PhaseStabilityTests(unittest.TestCase):
    """§9: subpixel translation redistributes paint; it must not delete it."""

    def rows(self) -> list[dict]:
        return probe()["phaseStability"]

    def test_no_tiny_radius_swings_more_than_eight_percent(self) -> None:
        # Was 178% at radius 0.5 and 1600% at 0.25.
        for r in self.rows():
            with self.subTest(radius=r["radius"], hardness=r["hardness"]):
                self.assertLessEqual(r["swingPct"], 8.0)

    def test_no_phase_produces_zero_total(self) -> None:
        for r in self.rows():
            with self.subTest(radius=r["radius"], hardness=r["hardness"]):
                self.assertGreater(r["minTotal"], 0)

    def test_the_worst_phase_is_within_half_the_best(self) -> None:
        for r in self.rows():
            with self.subTest(radius=r["radius"], hardness=r["hardness"]):
                self.assertGreater(r["minTotal"], r["maxTotal"] * 0.5)


class DirectionalParityTests(unittest.TestCase):
    """§9: horizontal, diagonal and vertical must weigh comparably.

    Read against the baseline this replaced, not against zero: a hard-edged disc
    narrower than a pixel cannot have identical weight at every angle without
    antialiasing its rim, and that would change ordinary output (§26).
    """

    def rows(self) -> list[dict]:
        return probe()["directionalParity"]

    def test_soft_tips_are_near_isotropic(self) -> None:
        # PER PIXEL OF TRAVEL, which is what "comparable apparent weight" means.
        # The raw total also carries the fixture's own length, and below the
        # floor the lattice snap moves a fixed-length segment's endpoints -- so
        # the raw spread reads 5.5% where the ink per pixel of travel is 0.4%,
        # BETTER than the 0.6% before the snap. Asserting the raw number would
        # have been asserting an artefact of this file's fixture.
        for r in self.rows():
            if r["hardness"] == 0:
                with self.subTest(radius=r["radius"]):
                    self.assertLessEqual(r["spreadPerLengthPct"], 3.0)

    def test_hard_tiny_tips_improved_severalfold(self) -> None:
        before = probe()["parityBaseline"]["beforeH1"]
        for r in self.rows():
            if r["hardness"] != 1:
                continue
            key = str(r["radius"])
            if key not in before:
                continue
            with self.subTest(radius=r["radius"]):
                self.assertLess(r["spreadPerLengthPct"], before[key])

    def test_no_angle_drops_out_entirely(self) -> None:
        for r in self.rows():
            with self.subTest(radius=r["radius"], hardness=r["hardness"]):
                self.assertTrue(all(v > 0 for v in r["byAngle"]), r["byAngle"])


class ContinuityTests(unittest.TestCase):
    """§9: no empty range and no jump where the engine used to fall empty."""

    def test_paint_rises_with_radius_at_every_hardness(self) -> None:
        # NON-DECREASING WITHIN 1.5%, not strictly rising, and the tolerance
        # is one specific measured thing rather than slack: at the floor the
        # same one pixel's worth of ink is placed two different ways --
        # concentrated in one pixel below, spread over up to four above -- and
        # 8-bit rounding of the same quantity lands 2/255 apart. Measured, the
        # single dip in the whole series is 191 -> 189 at hardness 1, 1.05%.
        # The concentration change itself is pinned by TheFloorSeamTests.
        for r in probe()["continuity"]:
            totals = r["meanTotals"]
            for i in range(1, len(totals)):
                with self.subTest(hardness=r["hardness"], step=i):
                    self.assertGreaterEqual(totals[i], totals[i - 1] * 0.985,
                                            totals)

    def test_no_discontinuity_across_the_old_empty_threshold(self) -> None:
        # 0.49 -> 0.51 used to cross from empty to empty; it must now be smooth.
        for r in probe()["continuity"]:
            with self.subTest(hardness=r["hardness"]):
                self.assertLessEqual(r["jumpAcrossHalfPct"], 10.0)

    def test_the_smallest_radius_still_paints_less_than_the_largest(self) -> None:
        for r in probe()["continuity"]:
            with self.subTest(hardness=r["hardness"]):
                self.assertLess(r["meanTotals"][0], r["meanTotals"][-1])


class VisibilityTests(unittest.TestCase):
    """§9: useful, not merely non-zero."""

    def test_a_one_pixel_class_hard_brush_reaches_a_strong_alpha(self) -> None:
        for r in probe()["visibility"]:
            if r["hardness"] == 1 and r["radius"] >= 0.75:
                with self.subTest(radius=r["radius"]):
                    self.assertGreaterEqual(r["maxMaxAlpha"], 200)

    def test_a_soft_tiny_brush_is_visible_at_every_phase(self) -> None:
        for r in probe()["visibility"]:
            if r["hardness"] == 0:
                with self.subTest(radius=r["radius"]):
                    self.assertGreater(r["minMaxAlpha"], 0)

    def test_visibility_rises_with_radius(self) -> None:
        hard = sorted([r for r in probe()["visibility"] if r["hardness"] == 1],
                      key=lambda r: r["radius"])
        alphas = [r["maxMaxAlpha"] for r in hard]
        self.assertEqual(alphas, sorted(alphas), alphas)


class OnePixelIsUsableTests(unittest.TestCase):
    """§9's gate as a NUMBER, against the Legacy oracle.

    "a 1 px-class hard brush produces an intentional crisp antialiased line
    rather than nothing or a two-pixel blur"; "a 1 px-class hardness-0 brush has
    a visible centre/core and does not peak near alpha 18 when the equivalent
    Legacy result is about 222".

    This is the only class here that runs the other engine. Everything else is
    V2 against V2, and all of it passed while the brush was still unusable.
    """

    def rows(self) -> dict:
        return {r["hardness"]: r for r in probe()["onePixelUsable"]["rows"]}

    def legacy(self) -> dict:
        return probe()["onePixelUsable"]["legacyOracle"]

    def test_a_one_pixel_line_is_one_pixel_wide(self) -> None:
        # 21 painted for a 20 px run, exactly Legacy's count. It read 44 -- two
        # rows of grey -- with the integration alone.
        for h, r in self.rows().items():
            with self.subTest(hardness=h):
                self.assertEqual(self.legacy()["strokePainted"],
                                 r["strokePainted"])

    def test_a_one_pixel_hard_line_reaches_full_alpha(self) -> None:
        self.assertEqual(255, self.rows()[1]["strokePeak"])

    def test_a_one_pixel_hard_line_carries_legacys_ink(self) -> None:
        legacy = self.legacy()["strokeTotal"]
        self.assertGreaterEqual(self.rows()[1]["strokeTotal"], legacy * 0.95)

    def test_a_soft_one_pixel_tip_is_nowhere_near_alpha_eighteen(self) -> None:
        # §9's number. The integration alone gave 14; the tap is now 59 and the
        # stroke 127. Not Legacy's 255, and deliberately not: Legacy point-
        # samples the peak of the falloff and so paints hardness 0 and hardness
        # 1 identically down here, which is the flattening the next class
        # forbids.
        soft = self.rows()[0]
        self.assertGreater(soft["tapPeakMin"], 3 * 18)
        self.assertGreater(soft["strokePeak"], 5 * 18)

    def test_the_tap_no_longer_depends_on_where_it_lands(self) -> None:
        for h, r in self.rows().items():
            with self.subTest(hardness=h):
                self.assertEqual(r["tapPeakMin"], r["tapPeakMax"])

    def test_a_one_pixel_tap_marks_one_pixel(self) -> None:
        for h, r in self.rows().items():
            with self.subTest(hardness=h):
                self.assertEqual(1, r["tapPaintedMax"])

    def test_it_beats_what_the_integration_alone_produced(self) -> None:
        # Recorded so "255" is read against the 111 it replaced.
        was = probe()["onePixelUsable"]["integrationOnly"]
        self.assertGreater(self.rows()[1]["strokePeak"], was["strokePeakH1"])
        self.assertGreater(self.rows()[0]["tapPeakMin"], was["tapPeakH0"])


class TheSnapIsConfinedToTheFloorTests(unittest.TestCase):
    """The snap quantises POSITION, which is right below one pixel and wrong
    above it. §9: "round brushes remain round; do not turn them into the future
    Pixel family."

    Detected by drawing the same tip at two sub-pixel positions inside one pixel
    and asking whether the output moved -- so this measures the behaviour, not
    the spelling of the condition.
    """

    def test_sub_pixel_tips_are_quantised(self) -> None:
        for row in probe()["snapExtent"]["snappedAt"]:
            with self.subTest(radius=row["radius"]):
                self.assertTrue(row["quantised"])

    def test_everything_above_the_floor_keeps_its_exact_position(self) -> None:
        for row in probe()["snapExtent"]["freeAt"]:
            with self.subTest(radius=row["radius"]):
                self.assertFalse(row["quantised"])

    def test_the_boundary_is_the_radius_floor_itself(self) -> None:
        # Not an independently chosen number: the floor already declares "this
        # tip is smaller than one pixel", and the snap is its consequence.
        self.assertEqual(0.5, probe()["snapExtent"]["floorRadius"])


class TheFloorSeamTests(unittest.TestCase):
    """§9: "there is no discontinuous jump at radius 0.5/0.6".

    The AMOUNT of paint is continuous across the floor. The CONCENTRATION is
    not, and cannot be: one pixel's worth of ink is either in one pixel or
    spread over four. That is the price of the snap, and it is pinned here
    rather than left to be found by painting a pressure taper.
    """

    def rows(self) -> list[dict]:
        return probe()["floorSeam"]

    def test_the_amount_of_paint_barely_moves(self) -> None:
        for r in self.rows():
            with self.subTest(hardness=r["hardness"]):
                self.assertLessEqual(abs(r["totalDeltaPct"]), 5.0)

    def test_the_snapped_side_is_a_single_pixel(self) -> None:
        for r in self.rows():
            with self.subTest(hardness=r["hardness"]):
                self.assertEqual(1, r["below"]["meanPainted"])

    def test_the_concentration_change_is_recorded_not_hidden(self) -> None:
        # Deliberately asserts the seam EXISTS at the size measured, so a later
        # change that quietly widened or removed the snap fails here.
        for r in self.rows():
            with self.subTest(hardness=r["hardness"]):
                self.assertGreater(r["paintedDeltaPct"], 100.0)
                self.assertLess(r["peakDeltaPct"], -20.0)


class HardnessIsNotFlattenedTests(unittest.TestCase):
    """§9: no hidden minimum solid core turning hardness 0 into hardness 1."""

    def test_every_hardness_produces_a_different_tiny_dot(self) -> None:
        self.assertTrue(probe()["hardnessDistinct"]["allDifferent"],
                        probe()["hardnessDistinct"]["signatures"])

    def test_paint_rises_with_hardness(self) -> None:
        self.assertTrue(probe()["hardnessDistinct"]["risesWithHardness"])

    def test_the_softest_tiny_dot_is_not_opaque(self) -> None:
        sigs = probe()["hardnessDistinct"]["signatures"]
        self.assertLess(sigs["0"]["maxA"], 255)


class OrdinaryRadiiAreUntouchedTests(unittest.TestCase):
    """§26: changing ordinary radius >= 2 output materially is a stop condition.

    The recorded values are MEASURED from the pre-repair engine, not estimated.
    An earlier draft of the probe carried guessed numbers and two were wrong.
    """

    def test_every_ordinary_radius_is_byte_identical(self) -> None:
        o = probe()["ordinaryUnchanged"]
        for key, before in o["recordedBeforeRepair"].items():
            with self.subTest(radius=key):
                self.assertEqual(before, o["now"][key])

    def test_the_threshold_is_two(self) -> None:
        self.assertEqual(2, probe()["ordinaryUnchanged"]["threshold"])

    def test_the_baseline_covers_a_large_ordinary_brush(self) -> None:
        # r27 is the shipping Basic Round at size 14. If the integrator ever
        # leaked upward, this is where it would show.
        self.assertIn("r27", probe()["ordinaryUnchanged"]["recordedBeforeRepair"])


class TheFootprintIsTheTipsTests(unittest.TestCase):
    """§11: "inflating the radius rather than integrating coverage".

    No gate above would catch that. Growing the tip fills the empty phases,
    steadies the swing and keeps every angle weighted -- while making a 1 px
    brush paint a 3 px blob. This is the invariant that separates the two.
    """

    def test_no_paint_lands_on_a_pixel_the_tip_never_touches(self) -> None:
        f = probe()["footprint"]
        # A pixel whose CENTRE is beyond the radius is legitimate: that is what
        # integrating a partly-covered pixel produces. A pixel whose SQUARE
        # misses the disc entirely is inflation and nothing else.
        self.assertLessEqual(f["maxOverextentPx"], 0.0, f["worstCase"])


class BoundedAndCheapTests(unittest.TestCase):
    """§10: the tiny path must stay tiny."""

    def test_a_subpixel_dot_dirties_a_subpixel_rectangle(self) -> None:
        b = probe()["bounded"]
        self.assertLessEqual(b["area"], 9)
        self.assertLess(b["area"], b["documentPixels"])

    def test_supersampling_only_engages_below_the_threshold(self) -> None:
        for row in probe()["cost"]:
            with self.subTest(radius=row["radius"]):
                self.assertEqual(row["radius"] < 2, row["supersampled"])

    def test_the_tiny_path_costs_less_than_an_ordinary_brush(self) -> None:
        # A cheap specialisation must not become the expensive case.
        tiny = [r for r in probe()["cost"] if r["radius"] < 2]
        big = [r for r in probe()["cost"] if r["radius"] == 27][0]
        for row in tiny:
            with self.subTest(radius=row["radius"]):
                self.assertLess(row["usPerStamp"], big["usPerStamp"])

    def test_an_ordinary_brush_did_not_get_slower(self) -> None:
        big = [r for r in probe()["cost"] if r["radius"] == 27][0]
        self.assertFalse(big["supersampled"])


class TheIntegratorContractTests(unittest.TestCase):
    """Labelled spelling checks on the seam, alongside the behaviour above."""

    def setUp(self) -> None:
        self.code = code_of(COVERAGE)

    def test_both_renderers_share_one_integrator(self) -> None:
        # If either renderer kept its own sampling, one would silently regress.
        # Count the two CALL SITES and the one definition separately -- a bare
        # count of the name is three, and asserting three would pass if a
        # renderer's call were deleted and a second definition added.
        self.assertEqual(1, self.code.count("function coverageAt("))
        self.assertEqual(2, self.code.count("= coverageAt("))

    def test_neither_renderer_point_samples_directly_any_more(self) -> None:
        self.assertNotIn("const nd = Math.sqrt(dx * dx + dy * dy) * invR", self.code)

    def test_the_sample_grid_is_not_jittered(self) -> None:
        # §7.B: a seeded jitter would make the same stroke differ between runs.
        self.assertNotIn("Math.random", self.code)

    def test_the_threshold_and_grid_are_named_constants(self) -> None:
        self.assertIn("SUPERSAMPLE_BELOW_RADIUS", self.code)
        self.assertIn("SUPERSAMPLE_N", self.code)

    def test_the_floor_is_one_named_constant_both_renderers_share(self) -> None:
        # A second literal 0.5 in either renderer is how the clamp and the snap
        # drift apart.
        self.assertEqual(0, self.code.count("Math.max(0.5, radiusPx)"))
        self.assertEqual(2, self.code.count(
            "Math.max(SUBPIXEL_FLOOR_RADIUS, radiusPx)"))

    def test_both_renderers_snap_at_the_floor(self) -> None:
        self.assertEqual(2, self.code.count("radiusPx <= SUBPIXEL_FLOOR_RADIUS"))
        # One definition and six calls: the stamp's mark is two coordinates and
        # the sweep's two endpoints are four.
        self.assertEqual(1, self.code.count("function snapToPixelCentre("))
        self.assertEqual(6, self.code.count(": snapToPixelCentre("))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3R2F_TINY_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
