"""BE2: the Pixel family sizes in literal document pixels.

THE DEFECT, AND WHY THE FIRST DIAGNOSIS WAS WRONG.

The owner reported that "the pixel brush preset is just a small thin brush".
The first explanation blamed a `Math.max(2, ...)` floor in the stamp path, and
the planned fix was to lower it to 1. BE0 measured that and it was false: Size 1
already painted exactly one pixel with the floor in place.

The real cause is that Brush Size is RELATIVE to the document's short side
through a 1.5-power curve. The same Pixel preset was one document pixel on a
small canvas and four on a 24 MP one, and Sizes 1 through 4 collapsed onto the
same mark at 512 square. A brush whose width depends on the canvas it is used
on is not a pixel brush.

So the ordinary curve is preserved -- the owner ratified it, and it is right for
ordinary brushes -- and the Pixel family opts into a second, explicit mode where
Size means literal document pixels.

WHAT THIS PACKAGE DOES NOT CLAIM.

Literal SIZING only. Pixel-perfect corner handling, aliased coverage and
cell-centre placement are BE13. One consequence is visible here and is asserted
rather than hidden: at an integer centre a round dab of diameter d covers
2*ceil(d/2)-1 pixels, so even Sizes still produce the odd mark below them.

These tests execute the real engine through `be1_measure.js` for the same
reason BE1 does: a control that resolves a number correctly and paints the
wrong pixels passes every source-text check.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DRIVER = Path(__file__).with_name("be1_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"
UI = APP_ROOT / "forge_studio" / "frontend" / "canvas-ui.js"

NODE = shutil.which("node")

EXPECTED_BE2_TESTS = 16

#: Every document the contract must hold across. A single-document test would
#: pass on the broken engine too, which is the whole reason this list exists.
DOCUMENTS = ("256x256", "512x512", "2048x2048", "4096x4096", "6000x4000")

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(
            f"the measurement driver exited {result.returncode}:\n"
            f"{result.stderr[:2000]}")
    M.update(json.loads(result.stdout)["sizeContract"])


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


@needs_node
class PixelIsDocumentIndependentTests(unittest.TestCase):
    """The acceptance criterion the package exists for."""

    def test_pixel_size_1_is_one_pixel_on_every_document(self):
        for doc in DOCUMENTS:
            with self.subTest(document=doc):
                self.assertEqual(
                    1, M["pixelAcross"][doc]["widthPx"],
                    f"Pixel Size 1 painted "
                    f"{M['pixelAcross'][doc]['widthPx']} px on a {doc} document")

    def test_pixel_resolves_without_a_document_term(self):
        resolved = {M["pixelAcross"][doc]["resolved"] for doc in DOCUMENTS}
        self.assertEqual(
            {1}, resolved,
            f"Pixel Size 1 resolved to different diameters per document: {resolved}")

    def test_pixel_declares_the_literal_mode(self):
        for doc in DOCUMENTS:
            with self.subTest(document=doc):
                self.assertEqual("document_pixels", M["pixelAcross"][doc]["mode"])


@needs_node
class OrdinaryBrushesAreUnchangedTests(unittest.TestCase):
    """The owner ratified the relative curve. BE2 must not quietly replace it.

    This is the half of the package that is about NOT changing something, and
    it is the half most likely to be broken by accident, because the easy
    implementation is to make every brush literal.
    """

    def test_basic_round_stays_relative(self):
        for doc in DOCUMENTS:
            with self.subTest(document=doc):
                self.assertEqual("relative", M["ordinaryAcross"][doc]["mode"])

    def test_basic_round_still_scales_with_the_document(self):
        widths = [M["ordinaryAcross"][doc]["resolved"] for doc in DOCUMENTS]
        self.assertEqual(
            sorted(set(widths))[:4], sorted(widths)[:4],
            "the relative curve should grow with the document")
        self.assertGreater(
            widths[DOCUMENTS.index("4096x4096")],
            widths[DOCUMENTS.index("256x256")] * 10,
            "a 4096 document should resolve a far larger dab than a 256 one")

    def test_the_relative_curve_is_the_ratified_one(self):
        """Pinned by value, so a change to the curve cannot pass silently.

        pct = size^1.5 / sqrt(100) / 100, times the short side.
        Basic Round is size 12: 12^1.5 / 10 / 100 * 512 = 21.
        """

        self.assertEqual(21, M["ordinaryAcross"]["512x512"]["resolved"])
        self.assertEqual(85, M["ordinaryAcross"]["2048x2048"]["resolved"])


@needs_node
class TheModeCannotLeakTests(unittest.TestCase):
    """Selecting Pixel must not make the NEXT preset literal.

    This is the exact defect BE1 documented for six other fields, so
    reintroducing it here -- in the package that added the field -- would be
    unforgivable. `applyBrushPreset` writes the mode for every preset, not only
    for the one that declares it.
    """

    #: BE14 renamed it, because BE13 gave it the methods the name claims. A
    #: preset name is a KEY -- `applyBrushPreset` looks it up -- so the rename
    #: carries an alias, and this constant is the one place the tests below
    #: name it.
    LITERAL_PRESET = "Pixel Perfect"

    def test_every_preset_returns_to_relative_after_pixel(self):
        for name, mode in M["modeAfterPreset"].items():
            if name == self.LITERAL_PRESET:
                continue
            with self.subTest(preset=name):
                self.assertEqual(
                    "relative", mode,
                    f"{name} inherited the literal sizing mode")

    def test_pixel_still_declares_its_own_mode(self):
        self.assertEqual("document_pixels",
                         M["modeAfterPreset"][self.LITERAL_PRESET])

    def test_the_old_name_still_resolves_to_it(self):
        """BE14 ADDED THIS. A preset name is a key that the per-tool memory and
        any recovered document may carry, and `applyBrushPreset` returns false
        on an unknown name and changes NOTHING -- so a rename without an alias
        makes an owner's saved brush stop applying quietly."""

        code = CORE.read_text(encoding="utf-8")
        table = code[code.index("const BRUSH_PRESET_ALIASES"):]
        table = table[:table.index("\n};")]
        self.assertIn('"Pixel": "Pixel Perfect"', table)


@needs_node
class LiteralSizeInterpretationTests(unittest.TestCase):
    """Size N is a DIAMETER in document pixels, and the rasterisation of that
    diameter is documented rather than promised away."""

    def test_the_resolved_diameter_equals_the_requested_size(self):
        for key, row in M["literalSizes"].items():
            with self.subTest(size=key):
                self.assertEqual(int(key[4:]), row["resolved"])

    def test_odd_sizes_paint_their_exact_width(self):
        for size in (1, 3, 5):
            with self.subTest(size=size):
                self.assertEqual(size, M["literalSizes"][f"size{size}"]["widthPx"])

    def test_even_sizes_paint_their_exact_width(self):
        """WAS AN expectedFailure OWNED BY BE13, AND BE13 FLIPPED IT.

        The original note read: "At an integer centre a diameter-d round dab
        covers 2*ceil(d/2)-1 pixels, so Size 2 paints 1 px and Size 4 paints 3.
        Even widths need the dab centred on a half pixel -- cell-centre
        placement, which BE13 owns along with aliased coverage."

        BE13's answer is that the engine's convention -- a pixel's integer
        index IS its centre -- makes an ODD diameter symmetric about an integer
        and an EVEN one symmetric about a half-integer. `stampAlphaMap` snaps
        to whichever the diameter calls for, on the ALIASED path only: an
        antialiased dab spreads across the boundary anyway, and snapping it
        would quantise every stroke to the pixel grid.

        Kept here rather than moved to BE13's module, because this is where the
        contract it belongs to lives, and because a promoted expectedFailure
        that nobody can find later teaches nothing.
        """

        for size in (2, 4, 8):
            self.assertEqual(size, M["literalSizes"][f"size{size}"]["widthPx"])


class TheModeTravelsWithTheSizeTests(unittest.TestCase):
    """Source guards for the per-tool memory, which has no headless surface.

    A restored size WITHOUT its mode is a different brush: a 5 that meant five
    pixels coming back as a 5 that means eighty. These assert the field is
    written and read in the same places `brushSize` is.
    """

    @staticmethod
    def _ui() -> str:
        import re
        text = UI.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return re.sub(r"^[ \t]*//.*$", "", text, flags=re.M)

    def test_the_mode_is_saved_with_the_tool(self):
        self.assertIn("brushSizeMode: S.brushSizeMode", self._ui())

    def test_the_mode_is_restored_with_the_tool(self):
        ui = self._ui()
        self.assertEqual(
            2, ui.count('saved.brushSizeMode === "document_pixels"'),
            "the mode must be restored in BOTH the per-tool restore and the "
            "startup path, or one of them hands back a bare size")

    def test_a_missing_saved_mode_defaults_to_relative(self):
        """Settings written before this field existed carry no mode. Those are
        all ordinary brushes and must not become literal on upgrade."""

        self.assertIn('? "document_pixels" : "relative"', self._ui())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE2_TESTS, found)


if __name__ == "__main__":
    unittest.main()
