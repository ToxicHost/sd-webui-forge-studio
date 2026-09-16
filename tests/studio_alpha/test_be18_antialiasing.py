"""BE18: a hard tip keeps one pixel of antialiasing.

THE DEFECT. `dabAlpha` sets `innerR = radius * hardness`, so at hardness 1.0
the inner radius equals the outer one: every pixel inside the tip returns
exactly 1, every pixel outside returns 0, and the antialiasing band has ZERO
width. Coverage is binary and the silhouette is a staircase.

Measured against an 8x supersampled render by the same engine, edge departure
from the true line, RMS:

    Hard Ink 0.29-0.42px   Fine Liner 0.29-0.42px   Marker 0.29-0.42px
    Calligraphy 0.29-0.42px
    PIXEL PERFECT -- which declares itself aliased -- 0.301px
    Basic Round, hardness 0.85 -- 0.014px

Four shipped presets were indistinguishable from the pixel brush on their
silhouette, and so was the default state before any preset is chosen.

OWNER RULING, 2026-08-23: fix it and accept the divergence from the shipping
Extension, which has the same behaviour. Asked as an explicit choice.

THE FLOOR IS IN PIXELS, NOT IN HARDNESS. A fraction collapses on a small tip:
Fine Liner's whole radius is 3px, and on a 6px tip the band is already gone by
hardness 0.99. A floor of "never harder than 0.95" would leave the small tips
exactly as aliased and soften the big ones for nothing.

AND IT IS BOUNDED AS A FRACTION TOO, which the first version was not. A flat
one-pixel band leaves a half-pixel core at radius 1.5, so the dab is mostly
ramp: Hard Ink at Size 3 lost 33.8% of its ink, the same footprint painted a
third paler, while every other size moved by under half a percent.

Source review: Evidence/source-review/BE18-antialiasing-floor.md
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DRIVER = Path(__file__).with_name("be18_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE18_TESTS = 14

#: Presets that ship hardness 1.0 and are therefore what this package is about.
HARD_PRESETS = ("Hard Ink", "Fine Liner", "Marker", "Calligraphy")

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise AssertionError(
            f"the driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only() -> str:
    source = CORE.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class EveryHardPresetHasARimTests(unittest.TestCase):
    """PARTIAL-COVERAGE PIXELS ARE THE WHOLE CLAIM.

    A staircase silhouette has exactly none: every pixel is 0 or full. The
    known-different case needs no mutation at all -- the engine at HEAD before
    this package produced zero partial pixels on all four presets, which is
    what "binary coverage" means.
    """

    def test_every_hard_preset_has_partial_coverage(self):
        rows = M["hardTipsHaveARim"]
        binary = {k: rows[k] for k in HARD_PRESETS
                  if rows[k]["partialPixels"] == 0}
        self.assertEqual(
            {}, binary,
            "these tips still paint binary coverage, so their silhouette is a "
            f"staircase: {binary}")

    def test_the_rim_is_a_rim_and_not_the_whole_tip(self):
        """Guards the guard in the other direction. A tip that was ALL partial
        coverage would satisfy the test above and be a soft brush wearing a
        hard brush's name.

        Threshold: the rim is under 10% of painted pixels. Measured 0.3% to
        4.8% across the four, which scales with perimeter-to-area as it should
        -- Fine Liner at radius 3 has the least, Calligraphy at 26.5 the most.
        """

        rows = M["hardTipsHaveARim"]
        for name in HARD_PRESETS:
            with self.subTest(preset=name):
                self.assertLess(
                    rows[name]["partialFraction"], 0.10,
                    f"{name} is mostly rim, which is a soft brush: {rows[name]}")
                self.assertGreater(rows[name]["solidPixels"], 100)


@needs_node
class ARimMustNotEatASmallTipTests(unittest.TestCase):
    """THE RISK THE FLOOR INTRODUCES, and it was real.

    A flat one-pixel band leaves a half-pixel core at radius 1.5, so the dab is
    mostly ramp. Before the fraction cap, Hard Ink at Size 3 lost 33.8% of its
    ink -- the same footprint, painted a third paler.
    """

    def test_every_size_keeps_a_dominant_solid_core(self):
        """Threshold: the solid core is over 60% of painted pixels, at every
        size. ABSOLUTE, not a delta.

        Calibrated against the same engine with the fraction cap removed. At
        Size 3, radius 1.5:

            with the cap     solid 0.9953   rim 0.0047
            without it       solid 0.3310   rim 0.6690

        -- the dab becomes mostly ramp, and the mark goes a third paler for the
        same footprint. 60% sits between the two populations.

        THIS GUARD WAS FIRST WRITTEN ON THE INK DELTA AND WAS VACUOUS. The
        delta only exists when the driver is given a second engine to compare
        against, the module gives it one argument, and the comprehension
        therefore filtered an empty set and passed unconditionally. The
        fraction-cap mutation walked straight through it. A guard whose subject
        can be absent must not be written as a filter over what is present.
        """

        rows = M["theRimDoesNotEatASmallTip"]
        thin = {k: v["solidFraction"] for k, v in rows.items()
                if v["solidFraction"] < 0.60}
        self.assertEqual(
            {}, thin,
            "these sizes are mostly antialiasing rim rather than mark: "
            f"{thin}")

    def test_the_solid_fraction_is_actually_populated(self):
        """Guards the guard that the guard above replaced. Every row must carry
        the field the assertion filters on, or the filter is empty again."""

        rows = M["theRimDoesNotEatASmallTip"]
        self.assertGreaterEqual(len(rows), 8)
        for key, row in rows.items():
            with self.subTest(size=key):
                self.assertIn("solidFraction", row)
                self.assertGreater(row["ink"], 0, f"{key} painted nothing")

    def test_the_sweep_covers_the_sizes_where_it_binds(self):
        """The cap binds only below radius 4, so a sweep that started at Size
        12 would prove nothing about the case it exists for."""

        rows = M["theRimDoesNotEatASmallTip"]
        small = [k for k, v in rows.items() if v["radius"] <= 2]
        self.assertGreaterEqual(
            len(small), 3, f"too few small tips in the sweep: {list(rows)}")

    def test_a_sub_pixel_tip_is_left_alone(self):
        """At radius 0.5 the tip is smaller than any band worth adding, and
        Pixel Perfect lives there by design."""

        rows = M["theRimDoesNotEatASmallTip"]
        for key in ("size1", "size2"):
            with self.subTest(size=key):
                self.assertEqual(0, rows[key].get("inkChangePercent", 0))


@needs_node
class AMaskEdgeStaysBinaryTests(unittest.TestCase):
    """THE EXEMPTION, and it is the one that would have caused real damage.

    `stampWet` forces round/hardness-1 when `S.editingMask`, and `exportMask`
    binarises at `alpha > 0`:

        for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0) { od[i] = 255; ... }

    So ANY non-zero alpha becomes a fully white mask pixel. A one-pixel
    antialiased rim would expand every inpaint mask by a pixel in every
    direction, changing what the model is asked to repaint -- and the softness
    would be discarded by the binarisation anyway. A mask edge is a decision
    boundary, not a mark.
    """

    def test_no_partial_coverage_in_mask_mode(self):
        rows = M["aMaskEdgeStaysBinary"]
        soft = {k: v for k, v in rows.items() if v["partialPixels"] > 0}
        self.assertEqual(
            {}, soft,
            "a mask gained an antialiased rim, which `exportMask` will "
            f"binarise into a mask one pixel larger in every direction: {soft}")

    def test_the_mask_fixture_actually_painted(self):
        """Guards the guard: zero partial pixels is also what an empty canvas
        has."""

        rows = M["aMaskEdgeStaysBinary"]
        for name, row in rows.items():
            with self.subTest(preset=name):
                self.assertGreater(row["solidPixels"], 100, str(row))

    def test_the_exemption_is_declared_where_it_is_decided(self):
        code = _code_only()
        self.assertIn("function effectiveHardness(hardness, radius)", code)
        body = code[code.index("function effectiveHardness(hardness, radius)"):]
        body = body[:body.index("\n}")]
        self.assertIn("if (S.editingMask) return h;", body)


@needs_node
class AlreadySoftIsUntouchedTests(unittest.TestCase):
    """The floor is a `min`, so a brush already softer than it comes through
    byte-identical. That is most of the shipped set."""

    def test_the_soft_presets_did_not_move(self):
        rows = M["alreadySoftIsUntouched"]
        moved = {k: v.get("inkChangePercent", 0) for k, v in rows.items()
                 if abs(v.get("inkChangePercent", 0)) > 0.01}
        self.assertEqual({}, moved, f"a soft preset changed: {moved}")

    def test_pixel_perfect_is_still_aliased(self):
        """It takes the aliased branch, which computes coverage 1 and never
        calls the falloff, so it cannot be affected. Asserted rather than
        assumed, because "cannot be affected" is what everyone says."""

        self.assertEqual(0, M["alreadySoftIsUntouched"]["Pixel Perfect"]["partialPixels"])


class TheFloorIsExpressedInPixelsTests(unittest.TestCase):
    """Structural, because the units are the design decision."""

    def test_the_band_has_a_pixel_floor_and_a_fraction_cap(self):
        code = _code_only()
        self.assertIn("const MIN_AA_PX = 1.0;", code)
        self.assertIn("const MAX_AA_FRACTION = 0.25;", code)
        self.assertIn(
            "const band = Math.min(MIN_AA_PX, radius * MAX_AA_FRACTION);", code)

    def test_it_is_resolved_once_where_hardness_is_resolved(self):
        """One call site, so the falloff, the custom-tip path and BE17's
        `profileMean` all receive the same effective value. A floor applied
        inside `dabAlpha` instead would leave `profileMean`'s closed form
        describing a profile the engine no longer draws."""

        code = _code_only()
        self.assertIn("const hard = effectiveHardness(S.brushHardness, r);", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE18_TESTS, found)


if __name__ == "__main__":
    unittest.main()
