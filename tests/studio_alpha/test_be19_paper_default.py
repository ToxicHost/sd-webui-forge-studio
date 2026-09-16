"""BE19: the document has a surface, and it ships switched on.

BE10 built the paper system and it reached ZERO PIXELS. Every preset was
bit-identical with grain declared versus grain forced to zero, and the BE10
commit produced byte-for-byte the same stroke as its parent for all sixteen
presets. An entire work package, invisible, behind three neutral gates -- a
Surface must be chosen, Depth must move off zero, the preset must declare a
Tooth -- and a controls panel that ships `display:none`.

Owner ruling, 2026-08-23: "Ship it on at a modest default."

THE SEAM THAT WOULD HAVE MADE THIS CHANGE REACH NOTHING TOO. `_createBlankDoc`
does not set `paper`, and `_loadDoc` then falls back to its own literal -- so a
new document takes the fallback, not the state default. Changing the state
default alone would have changed nothing at all, which is the same failure
committed a second time inside the package fixing it.

THE DEFAULT WAS CHOSEN TWICE. The first sweep did not reset the seeded RNG
between the grain-off and grain-on runs, so Charcoal's stipple -- Density 0.95,
consuming the random stream -- diverged between them and reported 72% of pixels
changed at depth 0.10. Almost all of it was RNG divergence rather than grain,
and 0.10 was chosen off that number. Reseeded, 0.10 reaches 9.1% on Charcoal
and 1.1% on Pencil, which is BE10's problem again. The shipped value is 0.20.

Source review: Evidence/source-review/BE19-paper-on-by-default.md
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

DRIVER = Path(__file__).with_name("be19_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"
DOCS = FRONTEND / "studio-docs.js"

NODE = shutil.which("node")

EXPECTED_BE19_TESTS = 18

#: Presets whose declared Tooth is high enough that the shipped depth shows.
TEXTURED = ("Charcoal", "Pastel", "Pencil")

#: Presets that declare no Tooth at all and must never find the surface.
SMOOTH = ("Hard Ink", "Pixel Perfect")

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


def _code_only(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class TheGrainReachesPixelsTests(unittest.TestCase):
    """THE BE10 FAILURE, AS A NUMBER, with the paper left at whatever the
    engine ships rather than forced on by the fixture."""

    def test_the_textured_presets_show_their_surface(self):
        """Threshold: 5% of painted pixels differing by more than 8/255 from
        the same stroke with the paper off.

        Calibrated against the shipped default before this package, which gave
        exactly 0.0% on every preset -- three neutral gates in series. Measured
        after: Pencil 71.3%, Charcoal 31.2%, Pastel 18.1%. Five sits well above
        the floor and well below all three.
        """

        rows = M["grainReachesPixels"]
        invisible = {k: rows[k]["fractionDiffering"] for k in TEXTURED
                     if rows[k]["fractionDiffering"] < 0.05}
        self.assertEqual(
            {}, invisible,
            "the shipped surface does not reach these presets, which is the "
            f"defect this package exists to fix: {invisible}")

    def test_the_smooth_presets_are_left_alone(self):
        """A modest default must not texture a brush nobody asked to texture.
        Basic Round declares a Tooth of 0.15 and Soft Round 0.10, and both stay
        at 0.0% visible at the shipped depth."""

        rows = M["grainReachesPixels"]
        for name in ("Basic Round", "Soft Round"):
            with self.subTest(preset=name):
                self.assertLess(
                    rows[name]["fractionDiffering"], 0.01,
                    f"{name} picked up visible grain at the shipped default: "
                    f"{rows[name]}")

    def test_the_shipped_depth_is_the_one_that_was_measured(self):
        self.assertEqual("fine", M["shippedDefault"]["texture"])
        self.assertAlmostEqual(0.20, M["shippedDefault"]["depth"], places=6)


@needs_node
class NoToothMeansNoGrainTests(unittest.TestCase):
    """`paperReveal` multiplies by `S.brushGrain`, so a preset declaring 0.0 is
    unaffected at any depth. An ink pen does not find the tooth.

    Measured across the WHOLE slider rather than at the default, because
    "untouched" is a property of the preset and not of the setting.
    """

    def test_a_preset_without_tooth_is_identical_at_every_depth(self):
        rows = M["noToothMeansNoGrain"]
        for name in SMOOTH:
            with self.subTest(preset=name):
                self.assertEqual(0, rows[name]["grain"])
                for depth, identical in rows[name]["identicalAtEveryDepth"].items():
                    with self.subTest(depth=depth):
                        self.assertTrue(
                            identical,
                            f"{name} declares no Tooth and still changed at "
                            f"{depth}")

    def test_the_sweep_went_past_the_shipped_depth(self):
        """Guards the guard: a sweep that only checked the default would prove
        nothing about a preset's immunity."""

        self.assertIn("depth100", M["noToothMeansNoGrain"]["Hard Ink"]["identicalAtEveryDepth"])


@needs_node
class AMaskHasNoSurfaceTests(unittest.TestCase):
    """`stampWet` passes `S.editingMask ? null : paperReveal()`. A mask is a
    decision boundary, and `exportMask` binarises it at alpha > 0 anyway -- so
    grain there would be discarded after having moved the boundary."""

    def test_a_mask_is_identical_with_and_without_paper(self):
        self.assertTrue(
            M["aMaskHasNoSurface"]["identical"],
            f"a mask picked up the surface: {M['aMaskHasNoSurface']}")

    def test_the_mask_fixture_actually_painted(self):
        self.assertGreater(M["aMaskHasNoSurface"]["paintedPixels"], 1000)


@needs_node
class TheSurfaceIsAnchoredToTheDocumentTests(unittest.TestCase):
    """It must not swim when the VIEW moves.

    BE10 built this and nobody could check it, because at the shipped defaults
    there was no grain to watch. It is checkable now.
    """

    def test_the_grain_does_not_move_with_zoom_or_pan(self):
        for label, row in M["theSurfaceIsAnchoredToTheDocument"].items():
            with self.subTest(view=label):
                self.assertTrue(
                    row["identical"],
                    f"the surface moved with the view at {label}: {row}")

    def test_all_three_views_were_checked(self):
        self.assertEqual(
            {"zoom4", "zoomQuarter", "panned"},
            set(M["theSurfaceIsAnchoredToTheDocument"]))


@needs_node
class TheSurfaceIsNotReRolledPerDabTests(unittest.TestCase):
    """A surface is a surface, not jitter.

    THE CONTROL IS THE SAME SWEEP WITH THE PAPER OFF, and it is what makes the
    number mean anything. Dab placement has its own small residue across sample
    densities -- about 1% to 3.4% of pixels here -- so a bare "the marks differ
    by 3%" cannot tell an unstable grain from ordinary placement noise. What
    the surface owes is that switching it on does not make that residue worse.
    """

    def test_the_grain_adds_nothing_to_the_sampling_residue(self):
        """Threshold: the grain's contribution stays under 0.5% of painted
        pixels. Measured: -0.06%, -0.10%, -0.19% -- negative at every density,
        so turning the paper on slightly REDUCES the spread rather than adding
        to it.

        NO MUTATION BACKS THIS ROW, and the reason is worth stating rather than
        hiding. The grain is applied ONCE at merge, inside
        `alphaMapToImageData`, over the finished alpha map -- there is no
        per-dab grain path to re-roll, so the defect this measures cannot be
        written. A first attempt multiplied the strength by `Math.random()` in
        `paperReveal`, which is called once per MERGE rather than per dab, and
        with a seeded stream both runs drew the same number and nothing moved.
        The property is guaranteed by architecture, and
        `test_the_grain_is_applied_once_at_merge` pins that architecture. This
        row is the measurement that the architecture behaves as claimed.
        """

        for label, row in M["theSurfaceIsNotReRolledPerDab"].items():
            with self.subTest(density=label):
                self.assertLess(
                    row["grainContribution"], 0.005,
                    f"the surface is being re-rolled per dab at {label}: {row}")

    def test_the_control_is_actually_a_control(self):
        """Guards the guard: if placement had no residue of its own, the
        subtraction would be meaningless and the raw number would have done."""

        rows = M["theSurfaceIsNotReRolledPerDab"]
        self.assertGreater(
            max(r["placementOnly"] for r in rows.values()), 0.005,
            "placement has no residue in this fixture, so subtracting it "
            f"proves nothing: {rows}")

    def test_a_split_stroke_keeps_one_surface(self):
        """The grain must not restart at a seam. Two strokes covering a span
        against one stroke covering it: where they overlap, the texture agrees
        to within 0.25% of pixels."""

        row = M["aSplitStrokeKeepsOneSurface"]
        self.assertGreater(row["comparedPixels"], 500)
        self.assertLess(
            row["fraction"], 0.02,
            f"the surface restarted at the seam: {row}")


class TheGrainIsAppliedOnceAtMergeTests(unittest.TestCase):
    """Structural, and it is what actually protects the two properties above.

    The grain is a property of the PAPER, so it is applied once over the
    finished alpha map and indexed by DOCUMENT coordinate. Applying it inside
    the stamp path would make it a property of the dab instead -- re-rolled per
    stamp, doubled wherever dabs overlap, and impossible to keep still under a
    moving view. BE4 made exactly this argument for the selection.
    """

    def test_the_grain_is_applied_once_at_merge(self):
        code = _code_only(CORE)
        merge = code[code.index("function alphaMapToImageData("):]
        merge = merge[:merge.index(chr(10) + "}")]
        self.assertIn("const grain = S.editingMask ? null : paperReveal();", merge)
        self.assertIn("gLut[gTile[((py & 511) << 9) | (px & 511)]]", merge)

    def test_no_grain_reaches_the_stamp_path(self):
        """A second application inside `stampAlphaMap` would turn an AMOUNT
        into a RATE -- the same defect shape BE4 removed from the selection."""

        code = _code_only(CORE)
        stamp = code[code.index("function stampAlphaMap("):]
        stamp = stamp[:stamp.index(chr(10) + "}")]
        self.assertNotIn("paperReveal", stamp)
        self.assertNotIn("gTile", stamp)


class TheDefaultIsDefinedOnceTests(unittest.TestCase):
    """Structural, because the seam is the whole risk.

    Three places held a copy of the neutral default: the state block, and both
    sides of the document save/load. A fourth copy is how a default stops
    reaching anything.
    """

    def test_there_is_one_shipped_default_and_it_is_exported(self):
        code = _code_only(CORE)
        self.assertIn(
            'const DEFAULT_PAPER = { texture: "fine", scale: 1, depth: 0.20 };',
            code)
        self.assertIn("PAPER_LIBRARY, PAPER_TILE, DEFAULT_PAPER,", code)
        self.assertIn("paper: Object.assign({}, DEFAULT_PAPER),", code)

    def test_a_new_document_carries_the_surface(self):
        """Without this the state default reaches nothing: `_loadDoc` falls
        back to its own literal for a document with no `paper` field."""

        code = _code_only(DOCS)
        self.assertIn("paper: (C && C.DEFAULT_PAPER)", code)
        self.assertIn("? Object.assign({}, C.DEFAULT_PAPER)", code)

    def test_a_legacy_document_is_left_alone(self):
        """The migration policy, as an asymmetry: a document that LACKS the
        field predates it and reopens looking exactly as it did. The ruling was
        "ship it on", not "repaint what people already made"."""

        code = _code_only(DOCS)
        self.assertIn(
            'S.paper = Object.assign({ texture: "none", scale: 1, depth: 0 }, '
            'doc.paper || {});',
            code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE19_TESTS, found)


if __name__ == "__main__":
    unittest.main()
