"""U3-D — Density, ported as a stipple.

WHAT WAS WRONG. Both engines read Density and spent it differently. Legacy
STIPPLES: `canvas-core.js:2604` skips a pixel on `Math.random() > density`, so
the control removes pixels. V2 folded it into `overlapK`, the accumulation
count, so a low-density preset deposited fewer and DARKER contributions and
removed nothing at all.

Measured across the sixteen shipping presets, V2 painted more than Legacy
everywhere, and grouping by cause showed one mechanism dominating:

    density < 1.0   (4 presets)   median delta  353%   range   67 .. 637
    density = 1.0  (12 presets)   median delta   24%   range   -6 ..  81

WHAT WAS DONE, and the one design decision worth stating. Legacy re-rolls the
draw per dab, so what an owner sees is the UNION over every dab covering a
pixel. BE7 measured that union climbing with spacing while the control stood
still -- at Density 0.35, spacing 0.32 gave 0.698 coverage and spacing 0.02
gave 1.000, "at close spacing the control does nothing at all" -- and corrected
it by pre-shrinking the per-dab probability to `1 - (1 - density)^(1/overlap)`.

THE CORRECTION IS NOT PORTED, because the artefact it corrects is not
reproduced. The draw here is a deterministic hash of (pixel, stroke), so
re-covering a pixel cannot re-roll it and the union is exactly `density` at any
spacing. Measured below at BE7's own four spacings: 0.3454 / 0.3455 / 0.3455 /
0.3455.

That also fixes the isolated dab for free. BE7's `overlap` comes from the
SPACING SETTING, which is not the overlap a tap has, so Legacy stipples a lone
Bristle Rake tap to `1 - 0.15^(1/25) = 7.3%` -- 132 px of ~1800, measured. Here
a tap keeps 0.8462 of its footprint at density 0.85.

WHAT IT COSTS, stated rather than discovered later: scrubbing within one
contact does not fill the holes in, because the mask is fixed for the stroke.
A second stroke carries a different seed and does fill in.

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

EXPECTED_U3D_TESTS = 18

PROBE = Path(__file__).with_name("u3d_density_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"
ADAPTER = APP_ROOT / "forge_studio" / "frontend" / "v2" / "canvas-adapter.js"
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

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


class TheControlIsOffAboveTheThresholdTests(unittest.TestCase):
    """Twelve of the sixteen shipping presets declare density 1.0.

    Not "close to" what they painted before. Identical, because `depositionFor`
    returns a null stipple and the hot loop takes the branch it always took.
    """

    def test_a_solid_preset_carries_no_stipple_at_all(self) -> None:
        self.assertTrue(probe()["offIsOff"]["stippleIsNull"])

    def test_the_threshold_is_legacys(self) -> None:
        """`stipple = density < 0.99` in `canvas-core.js:2477`. A different
        threshold would make some preset stochastic that Legacy leaves solid."""

        off = probe()["offIsOff"]
        self.assertTrue(off["stippleIsNullAt099"])
        self.assertTrue(off["stippleExistsAt098"])
        self.assertIn("const STIPPLE_BELOW = 0.99;", code_of(COVERAGE))

    def test_the_seed_cannot_reach_a_solid_preset(self) -> None:
        """If it can, the threshold is wrong and every solid preset just became
        stochastic. Zero differing pixels across two seeds, not a tolerance."""

        self.assertEqual(0, probe()["offIsOff"]["differingAcrossSeeds"])


class ATapKeepsItsDeclaredDensityTests(unittest.TestCase):
    """Legacy's own failure case, and the reason for the one divergence.

    Its `overlap` is read off the Spacing setting, which is not the overlap an
    isolated dab has -- so a lone Bristle Rake tap survives at 7.3% rather than
    85%. Legacy's comment calls this "the correct trade" because Density is a
    stroke control; the owner's decision was to fix it.
    """

    def test_a_tap_at_085_keeps_about_085(self) -> None:
        kept = probe()["tapKeepsItsDeclaredFraction"]["kept"]["0.85"]
        self.assertAlmostEqual(0.85, kept, delta=0.02)

    def test_a_tap_at_050_keeps_about_050(self) -> None:
        kept = probe()["tapKeepsItsDeclaredFraction"]["kept"]["0.5"]
        self.assertAlmostEqual(0.50, kept, delta=0.02)

    def test_a_tap_at_025_keeps_about_025(self) -> None:
        """Three points, not one. A single ratio is satisfied by any monotone
        function of density; three pin the identity."""

        kept = probe()["tapKeepsItsDeclaredFraction"]["kept"]["0.25"]
        self.assertAlmostEqual(0.25, kept, delta=0.02)


class DensityAndSpacingAreIndependentTests(unittest.TestCase):
    """BE7's measurement, repeated against this implementation.

    This is the property the whole design turns on, and the one a single-dab
    test cannot see. Legacy before its correction, at Density 0.35:

        spacing 0.32 -> 0.698      spacing 0.04 -> 0.999
        spacing 0.16 -> 0.902      spacing 0.02 -> 1.000
    """

    def test_the_union_is_the_requested_density_at_every_spacing(self) -> None:
        """ON THE SPINE, which is the only window where the overlap is the
        `2/step` that BE7's correction is derived from.

        This read the WHOLE-FOOTPRINT fraction and expected 0.35 within 0.02,
        and it passed for the wrong reason: the probe called `stamp` without a
        `dabIndex`, so every dab took `pOpening` and one fixed hash -- U3-D v1,
        the build the owner rejected. On the shipped path the whole footprint
        sits at 0.285, below the request by a geometric factor: the rim and the
        two ends are covered by fewer dabs than the spine, at every spacing
        alike. That factor belongs to the footprint, not to the control, so the
        absolute claim is measured where the overlap is uniform and the
        spacing-invariance claim keeps the whole footprint below.

        Tolerance is 0.05 against 980 spine samples, whose binomial noise alone
        is about 0.015. Measured 0.310 / 0.359 / 0.331 / 0.354."""

        u = probe()["unionAcrossSpacing"]
        self.assertGreaterEqual(u["spineSamples"], 900)
        for spacing, kept in u["spineKept"].items():
            with self.subTest(spacing=spacing):
                self.assertAlmostEqual(u["requested"], kept, delta=0.05)

    def test_tightening_the_spacing_does_not_fill_it_in(self) -> None:
        """The specific regression BE7 names: "at close spacing the control
        does nothing at all". Legacy moved by 0.302 over this 16x range.

        ALL FOUR SPACINGS, not just the two ends -- a non-monotonic drift would
        slip past a loosest-against-tightest comparison. Measured spread 0.010
        (0.2819 .. 0.2919).

        Tolerance is 0.02. It was 0.01, set against U3-D v1, whose draw never
        re-rolled and whose spread was therefore 0.0001 -- a tolerance that
        described the absence of the mechanism rather than its accuracy. The
        shipped draw re-rolls per dab and BE7's correction holds it flat to
        0.010, so 0.02 is two-fold headroom on a stochastic measurement and
        still fifteen times tighter than the defect it exists to catch."""

        kept = probe()["unionAcrossSpacing"]["kept"]
        values = [kept[k] for k in ("0.32", "0.16", "0.04", "0.02")]
        spread = max(values) - min(values)
        self.assertLess(spread, 0.02, kept)
        #: The scale that makes 0.02 mean something: Legacy's own uncorrected
        #: measurement, which this must stay nowhere near.
        self.assertLess(spread, 0.302 / 10)

    def test_the_overlap_correction_is_in_the_kernel(self) -> None:
        """INVERTED FROM WHAT IT ASSERTED, because the implementation it was
        written against was replaced.

        U3-D v1 drew once per (pixel, stroke). Re-covering a pixel could not
        re-roll it, so the union was exactly `density` at any spacing and BE7's
        `1/overlap` power was genuinely redundant -- this test forbade it. The
        owner rejected that build: a mask that never re-rolls has the same hole
        fraction at the spine as at the rim, which reads as a flat dither with a
        saturated core rather than as a stroke with a density gradient.

        U3-D2 restored the per-dab draw, and with it the climb BE7 measured. The
        correction is now the only thing holding the union flat, so its absence
        is the defect and its presence is the contract. It stayed green through
        the inversion because the spelling changed too (`1 / Math.max(1, 2 /
        step)`), which is exactly why this asserts the arithmetic and not a
        phrase.

        `spacingFraction` is still forbidden: the kernel takes `step`, and
        reaching for Legacy's own spacing helper would be a second source of
        truth for the overlap."""

        code = code_of(COVERAGE)
        self.assertIn(
            "p: 1 - Math.pow(1 - density, 1 / Math.max(1, 2 / step)),", code)
        #: And `pOpening` is NOT corrected -- an isolated dab overlaps nothing.
        self.assertIn("pOpening: density,", code)
        self.assertNotIn("spacingFraction", code)


class TheDrawIsDeterministicTests(unittest.TestCase):
    """Not `Math.random()`, and the reason is on the record.

    `brushGrain`'s comment describes a grain-off/grain-on comparison that
    "diverged the seeded RNG between the runs" and reported 72% of pixels
    changed, "almost all of it RNG divergence rather than grain" -- and a
    shipped default was picked off that number.
    """

    def test_one_seed_paints_one_result(self) -> None:
        self.assertEqual(
            0, probe()["determinism"]["differingAcrossTwoRunsOfOneSeed"])

    def test_a_second_stroke_punches_different_holes(self) -> None:
        """Or painting twice would never fill in, and the mask would read as a
        fixed screen door rather than as texture."""

        self.assertGreater(
            probe()["determinism"]["differingAcrossTwoSeeds"], 1000)

    def test_the_kernel_does_not_reach_for_the_global_stream(self) -> None:
        self.assertNotIn("Math.random(", code_of(COVERAGE))
        #: Legacy still does, and this test is not asking it to stop.
        self.assertIn("Math.random(", code_of(CORE))

    def test_every_mark_of_one_stroke_shares_the_strokes_seed(self) -> None:
        """`_depositionForMark` rebuilds a deposition per flow bucket. Re-seeding
        there would give a pressure-varying stroke a different mask at each
        bucket -- a shimmer rather than a texture."""

        code = code_of(ADAPTER)
        self.assertIn("stippleSeed: strokeSeed", code)
        self.assertIn("seed: st.stippleSeed", code)


class DensityLeftTheAccumulationCountTests(unittest.TestCase):
    """It was making low-density dabs DARKER, which is the opposite of a
    stipple, and would double-count against the mask."""

    def test_overlap_k_does_not_move_with_density(self) -> None:
        k = probe()["overlapK"]
        self.assertEqual(k["atDensity1"], k["atDensity025"])

    def test_the_deposited_alpha_does_not_move_with_density(self) -> None:
        """`fEff` follows `overlapK`. If density still reached it, the surviving
        pixels would be the wrong alpha and the stipple would be paid for
        twice."""

        k = probe()["overlapK"]
        self.assertEqual(k["fEffAtDensity1"], k["fEffAtDensity025"])


class BothRenderersStippleTests(unittest.TestCase):
    """Charcoal and Pastel are round with ratio 1 and `textured` is hard-coded
    false, so they SWEEP, while Scatter Dust and Bristle Rake STAMP. Stippling
    only the stamp path would fix two of the four presets and quietly leave the
    other two."""

    def test_the_swept_renderer_stipples(self) -> None:
        """A TRAIN OF SEGMENTS WITH REAL INDICES, measured on the spine.

        This was one indexless segment compared on the whole footprint, so it
        exercised `pOpening` and a single fixed hash -- the same blind spot the
        stamp train had. The adapter sweeps between consecutive marks and hands
        each one `j`, so the probe now does too, and the absolute claim moves to
        the spine for the reason `DensityAndSpacingAreIndependentTests`
        records."""

        s = probe()["sweepStipples"]
        self.assertEqual("sweep", s["renderer"])
        self.assertGreater(s["segments"], 10)
        self.assertAlmostEqual(0.5, s["spineKeptAt05"], delta=0.05)

    def test_there_is_one_accumulator_and_the_stipple_is_inside_it(self) -> None:
        """Both renderers reach `depositWith`. Putting the test in each caller
        instead would be two implementations that can drift."""

        code = code_of(COVERAGE)
        self.assertEqual(1, code.count("function depositWith("))
        self.assertEqual(1, code.count("hash01(i, (stipple.seed ^"))


class SuiteIntegrityTests(unittest.TestCase):

    def test_every_test_in_this_module_is_counted(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_U3D_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
