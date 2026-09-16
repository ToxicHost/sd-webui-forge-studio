"""BE10: the document has a surface, and marks land on it.

THE COMPLAINT THIS CLOSES. BE1 through BE9 changed the SHAPE of a mark -- which
pixels a tip covers, where dabs land, how coverage accumulates, how hard the
falloff is, which way the tip points. Every one was a real defect and every one
is fixed. None of them changed what the mark lands ON, and a stroke still laid
down a smooth field of colour with a smooth edge because there was nothing
under it.

FOUR PROPERTIES separate a paper from a noise filter, and each is guarded here:

    THE PAPER IS THE DOCUMENT'S. Two strokes crossing a point reveal the same
    fibres, because the height map is indexed by DOCUMENT coordinate. Measured
    at 1.0000 agreement over 2,914 solidly covered overlap pixels.

    ZOOM MOVES NOTHING. A screen-space texture is the easy mistake and it looks
    correct until the owner zooms. Byte-identical at 0.25x, 1x and 4x.

    OVERLAP CANNOT ERASE IT. This is why paper is applied at MERGE and not per
    dab. A per-dab grain multiplies on every overlap: each pass takes its share
    of the reduction and the total climbs back, so the fibres wash out exactly
    where the owner pressed hardest. Scrubbing 1, 4 and 16 times moves the
    texture by 0.01 of an alpha step.

    NEUTRAL IS EXACT. Four different ways of saying "no paper" all produce a
    BYTE-IDENTICAL mark to the unpapered one. A control that is nearly neutral
    at zero is a control the owner cannot turn off.

THE SIGN IS NOT DECORATION. Positive Depth reveals the peaks -- paint catching
raised fibres, a pencil on rough paper. Negative reveals the valleys -- paint
settling into the tooth, a wash. Reveal correlation between the two: -1.0000.

Source review: Evidence/source-review/BE10-document-paper.md
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

DRIVER = Path(__file__).with_name("be10_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE10_TESTS = 43

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(
            f"the driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only(name: str = "canvas-core.js") -> str:
    source = (FRONTEND / name).read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


def _html() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


@needs_node
class PaperReachesThePixelsTests(unittest.TestCase):
    """The first question, and the one the owner's complaint is about: does
    turning it on change the mark at all?"""

    def test_it_changes_the_mark(self):
        row = M["paperChangesTheMark"]
        self.assertFalse(
            row["identical"],
            "paper on and paper off produce the same pixels; the control is "
            f"inert: {row}")

    def test_it_changes_the_mark_by_a_visible_amount(self):
        """Not merely different -- DIFFERENT ENOUGH. A one-alpha-step change
        would satisfy the guard above and be invisible."""

        row = M["paperChangesTheMark"]
        self.assertLess(
            row["on"]["mean"], row["off"]["mean"] * 0.75,
            f"paper barely dims the mark: {row}")

    def test_it_does_not_change_which_pixels_are_covered(self):
        """Paper gates coverage; it does not move the stroke. The painted
        count may drop where the deepest pits reach zero, but the mark must
        not be a different SHAPE."""

        row = M["paperChangesTheMark"]
        self.assertLess(
            abs(row["on"]["painted"] - row["off"]["painted"]),
            row["off"]["painted"] * 0.05,
            f"paper moved the stroke rather than gating it: {row}")


@needs_node
class NeutralIsExactTests(unittest.TestCase):
    """Four ways to mean "no paper", all byte-identical to no paper.

    This is the property Krita's plain MULTIPLY does not have and its "soft
    texturing" variant does -- `1 - strength*(1 - h)` is exactly 1 at strength
    0, where `mul(src, dst, strength)` is not.
    """

    def test_depth_zero_is_exactly_off(self):
        self.assertTrue(M["neutralIsExact"]["depthZero"])

    def test_a_preset_with_no_tooth_is_exactly_off(self):
        self.assertTrue(M["neutralIsExact"]["grainZero"])

    def test_no_texture_is_exactly_off(self):
        self.assertTrue(M["neutralIsExact"]["textureNone"])

    def test_an_unknown_texture_name_is_exactly_off(self):
        """A document written by a later Studio, or a corrupted recovery
        manifest, must paint normally rather than throw."""

        self.assertTrue(M["neutralIsExact"]["unknownTexture"])


@needs_node
class ThePaperBelongsToTheCanvasTests(unittest.TestCase):
    """The acceptance criterion in the brief: "two strokes crossing the same
    canvas location reveal the same fibres"."""

    def test_two_different_strokes_find_the_same_fibres(self):
        row = M["paperIsLockedToTheCanvas"]
        self.assertGreater(
            row["overlapPixels"], 500,
            f"the fixture produced too small an overlap to mean anything: {row}")
        self.assertEqual(
            1.0, row["overlapAgreement"],
            "two strokes crossing the same pixels revealed different amounts "
            f"of paper: {row}")

    def test_the_same_stroke_repeats(self):
        self.assertTrue(M["paperIsLockedToTheCanvas"]["repeatable"])

    def test_a_different_position_finds_different_fibres(self):
        """Guards the guard. A height map that returned a constant would pass
        every agreement test above."""

        self.assertTrue(M["paperIsLockedToTheCanvas"]["positionMatters"])


@needs_node
class ZoomDoesNotMoveTheGrainTests(unittest.TestCase):
    """A screen-space texture is the easy mistake. It looks right until the
    owner zooms, and then the paper swims."""

    def test_the_output_is_identical_at_every_zoom(self):
        row = M["zoomDoesNotMoveTheGrain"]
        self.assertTrue(row["identical"], f"{row['at']}")

    def test_the_zooms_actually_differ(self):
        self.assertIn("zoom0.25", M["zoomDoesNotMoveTheGrain"]["at"])
        self.assertIn("zoom4", M["zoomDoesNotMoveTheGrain"]["at"])


@needs_node
class OverlapCannotEraseTheGrainTests(unittest.TestCase):
    """THE REASON PAPER IS APPLIED AT MERGE.

    A per-dab grain multiplies with every overlap: before accumulation it turns
    the reveal from an AMOUNT into a RATE, each pass contributes its share of a
    reduced value, and the total climbs back toward full. The texture would
    then vanish exactly where the owner worked hardest.

    Identical in form to the selection defect BE4 found, in the same loop, with
    the same fix.
    """

    def test_scrubbing_does_not_wash_the_texture_out(self):
        row = M["overlapDoesNotEraseTheGrain"]
        self.assertLess(
            row["spread"], 1.0,
            f"the texture changes with how many times the owner went over it: {row}")

    def test_the_passes_actually_overlap(self):
        """Guards the guard: if the extra passes painted nothing, the spread
        would be zero for the wrong reason."""

        at = M["overlapDoesNotEraseTheGrain"]["at"]
        self.assertGreater(at["passes1"]["rough"], 5)


@needs_node
class TheSignReversesTheRevealTests(unittest.TestCase):
    """"signed Roughness/reveal amount" in the brief. The sign means something
    specific: which half of the surface the paint reaches."""

    def test_the_two_signs_are_opposites(self):
        row = M["theSignReversesTheReveal"]
        self.assertLess(
            row["revealCorrelation"], -0.95,
            "positive and negative Depth do not reveal opposite halves of the "
            f"surface: {row}")

    def test_the_comparison_had_something_to_compare(self):
        self.assertGreater(M["theSignReversesTheReveal"]["solidPixels"], 1000)


@needs_node
class PresetsRevealTheSamePaperDifferentlyTests(unittest.TestCase):
    """The brief's own wording: "Pencil and Pastel share the same paper
    location but reveal it differently". Without a per-preset strength the
    paper is a document filter, and every preset finds the tooth identically --
    which is the complaint this programme started from, one level up."""

    def test_strength_is_monotonic(self):
        row = M["presetStrengthMatters"]
        self.assertTrue(row["monotonic"], f"{row['at']}")

    def test_presets_declare_their_own_tooth(self):
        """READ FROM THE TABLE, not matched as a source string.

        This asserted `'name: "Pencil", grain: 0.85'` -- one field order on one
        line -- and BE14 reformatted the table onto several lines per preset.
        The claim was always about the VALUES, so it reads them.
        """

        rows = M["shippedTooth"]
        self.assertEqual(0.85, rows["Pencil"])
        self.assertEqual(0.0, rows["Hard Ink"])
        self.assertEqual(0.9, rows["Sketch Light"])

    def test_the_pixel_brush_has_none(self):
        """BE2 CONTRACT. One literal document pixel at full alpha on every
        document. A height map would dim it by position, which is the least
        debuggable way to break that."""

        # BE14 renamed it to Pixel Perfect, because BE13 gave it the methods
        # the name claims.
        self.assertEqual(0.0, M["shippedTooth"]["Pixel Perfect"])

    def test_every_preset_declares_one(self):
        """A preset that omitted it would inherit the previous preset's tooth
        -- the exact state-leak defect BE1 exists to catch."""

        rows = M["shippedTooth"]
        missing = [n for n, g in rows.items() if not isinstance(g, (int, float))]
        self.assertEqual([], missing, f"presets with no grain value: {missing}")
        self.assertGreaterEqual(len(rows), 12)

    @needs_node
    def test_a_preset_switch_does_not_leak_the_last_one(self):
        row = M["presetsResetTheirGrain"]
        self.assertEqual(0.85, row["afterPencil"])
        self.assertEqual(0, row["afterInk"])
        self.assertEqual(0.85, row["backToPencil"])


@needs_node
class ThePapersAreDistinctTests(unittest.TestCase):
    """Four surfaces, not one noise field at four amplitudes -- which is the
    mistake this whole programme exists to stop."""

    def test_every_paper_produces_a_different_mark(self):
        row = M["papersAreDistinct"]
        self.assertEqual(
            row["count"], row["distinct"],
            f"two papers rendered identically: {row['at']}")

    def test_scale_changes_the_surface(self):
        row = M["scaleIsAControl"]
        self.assertEqual(3, row["distinct"], f"{row['at']}")

    def test_the_tile_wraps(self):
        """The tile repeats every 512 document pixels, so a discontinuity at
        its edge is not a subtle artefact -- it is a ruled line across the
        canvas every 512 pixels, in both directions."""

        row = M["theTileIsSane"]
        self.assertLess(
            row["colSeam"], row["colInterior"] * 1.5 + 1,
            f"the wrap seam is worse than an ordinary interior column: {row}")

    def test_the_tile_uses_its_whole_range(self):
        """Normalised at build time so Depth means the same thing on every
        paper. Without it a low-contrast surface would need Depth 1 to show at
        all while a high-contrast one blew out at 0.3, and the control would be
        reading the paper rather than the owner."""

        row = M["theTileIsSane"]
        self.assertEqual(0, row["min"])
        self.assertEqual(255, row["max"])

    def test_the_tile_is_cached(self):
        """Rebuilding is about 20ms. Per stroke that is a stutter; per dab it
        would be unusable."""

        self.assertTrue(M["theTileIsSane"]["cached"])


@needs_node
class TheCurveIsExactTests(unittest.TestCase):
    """The reveal curve read directly out of its lookup table.

    ADDED AFTER A MUTATION ESCAPED. `lut[i] = h + (1-h)*(1-strength)*0.995` --
    the same curve, half a percent short -- passed every guard in this module.
    Depth 0 was safe because `paperReveal` returns null before it builds a
    table at all, and at every other strength the error sat below the 8-bit
    quantisation of `coverage * reveal` for most coverages.

    A curve that is nearly right is the hardest kind of wrong to find later, so
    the two exact endpoints are asserted rather than inferred from pixels:

        a PEAK is untouched at any strength      lut[255] == 1
        a PIT is reduced to exactly 1 - strength lut[0]   == 1 - s

    Both follow from `1 - s*(1 - h)`, which is Krita's soft-texturing MULTIPLY
    written out -- see Evidence/source-review/BE10-document-paper.md section
    3.1.
    """

    def test_a_peak_is_untouched_at_every_strength(self):
        for name, row in M["theCurveIsExact"].items():
            with self.subTest(strength=name):
                self.assertEqual(1, row["peak"], f"{row}")

    def test_a_pit_is_reduced_to_exactly_one_minus_strength(self):
        for name, row in M["theCurveIsExact"].items():
            with self.subTest(strength=name):
                self.assertEqual(row["expectedPit"], row["pit"], f"{row}")

    def test_the_curve_never_turns_back(self):
        """A higher fibre must never take LESS paint than a lower one."""

        for name, row in M["theCurveIsExact"].items():
            with self.subTest(strength=name):
                self.assertTrue(row["monotonic"], f"{row}")


@needs_node
class MasksAreNotPaperedTests(unittest.TestCase):
    """`exportMask` binarises at alpha > 0, so a grainy mask would not export
    as soft tooth -- it would export as a mask full of holes wherever the pits
    fell."""

    def test_a_mask_stroke_ignores_the_paper(self):
        self.assertTrue(M["masksAreNotPapered"]["identical"])


class TheMergeIsTheOnlyPlaceItAppliesTests(unittest.TestCase):
    """Structural, because applying it per dab is the natural thing to write
    and is what Krita does -- Krita can, because its wash mode composites the
    stroke indirectly, and Studio's accumulator cannot."""

    def test_it_is_applied_in_the_merge(self):
        code = _code_only()
        body = code[code.index("function alphaMapToImageData("):]
        body = body[:body.index("\n}")]
        self.assertIn("paperReveal()", body)
        self.assertIn("gLut[gTile[", body)

    def test_it_is_not_applied_per_dab(self):
        code = _code_only()
        body = code[code.index("function stampAlphaMap("):]
        body = body[:body.index("\n}")]
        self.assertNotIn("paperReveal", body)
        self.assertNotIn("paperTile", body)

    def test_the_reveal_is_a_lookup_and_not_arithmetic_per_pixel(self):
        """BE8 got this loop from 750ms to 159ms per 4096 dabs. The reveal
        depends only on the height byte, so the curve is evaluated 256 times
        rather than once per pixel."""

        code = _code_only()
        body = code[code.index("function paperReveal("):]
        body = body[:body.index("\n}")]
        self.assertIn("new Float32Array(256)", body)


class ThePaperIsTheDocumentsTests(unittest.TestCase):
    """It has to survive a reload, and every stage of that chain enumerates its
    fields by hand -- there is no passthrough anywhere in it. A field added at
    four of the five stages is silently dropped."""

    def test_the_document_snapshot_carries_it(self):
        self.assertIn("doc.paper = Object.assign(", _code_only("studio-docs.js"))

    def test_the_document_restore_carries_it(self):
        code = _code_only("studio-docs.js")
        self.assertIn("S.paper = Object.assign(", code)

    def test_recovery_writes_it(self):
        self.assertIn("paper: doc.paper ?", _code_only("canvas-recovery.js"))

    def test_recovery_reads_it_back(self):
        self.assertIn("paper: manifest.paper || null,",
                      _code_only("canvas-recovery.js"))

    def test_no_filesystem_path_can_reach_the_manifest(self):
        """The acceptance criterion is "no private filesystem path appears in
        document/session state". The papers are GENERATED from their names, so
        there is no path to leak -- which is the strongest form of that
        guarantee available and the reason they are procedural rather than
        shipped images."""

        code = _code_only()
        body = code[code.index("const PAPER_LIBRARY"):]
        body = body[:body.index("\n};")]
        for banned in ("fetch(", "XMLHttpRequest", "new Image(", ".src =", "require("):
            self.assertNotIn(banned, body)


class TheControlsExistTests(unittest.TestCase):
    """A capability with no control is a capability the owner does not have --
    which is what BE1's whole guard set is about, one level up."""

    def test_the_four_controls_are_in_the_markup(self):
        html = _html()
        for control in ("paperTexture", "paperDepth", "paperScale", "paperTooth"):
            self.assertIn('id="' + control + '"', html)

    def test_every_library_paper_is_offered(self):
        code = _code_only()
        library = code[code.index("const PAPER_LIBRARY"):]
        library = library[:library.index("\n};")]
        names = re.findall(r"^    (\w+): \{$", library, flags=re.M)
        self.assertEqual(4, len(names), f"parsed {names}")
        html = _html()
        for name in names:
            self.assertIn('<option value="' + name + '"', html)

    def test_the_depth_control_runs_through_zero(self):
        """Signed. A control that could not go negative would make half the
        feature unreachable."""

        html = _html()
        depth = html[html.index('id="paperDepth"') - 200:html.index('id="paperDepth"') + 60]
        self.assertIn('min="-100"', depth)

    def test_the_panel_reads_the_document_rather_than_its_own_defaults(self):
        code = _code_only("canvas-ui.js")
        body = code[code.index("function _syncDynamicsPanel("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.paper", body)
        self.assertIn("paperTooth", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE10_TESTS, found)


if __name__ == "__main__":
    unittest.main()
