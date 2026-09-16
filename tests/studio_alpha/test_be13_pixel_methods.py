"""BE13: the Pixel brush becomes a pixel brush.

WHAT BE2 LEFT. BE2 made Size 1 mean one literal document pixel on every
document, and said in the preset's own comment what it was NOT doing:

    "This is literal SIZING only. Pixel-perfect corner handling, aliased
     coverage and cell-centre placement are BE13 and are not claimed here."

So the Pixel brush was an ANTIALIASED one-pixel brush: `dabAlpha` returns a
smoothstep, the dab carried fractional alpha at the edge of its own cell, and a
diagonal run left soft shoulders. The owner's word for it was "just a small
thin brush", and that was accurate.

TWO FEATURES, AND ONLY ONE IS ABOUT THE PIXEL PRESET.

    ALIASED COVERAGE is a METHOD on the ordinary dab pipeline, not a second
    engine. The coverage loop already computes `nd`, the normalised distance in
    the tip's own frame, and already exits on `nd >= 1` -- so `nd < 1` IS "this
    cell is inside the tip", and aliased is that test without the falloff. The
    eraser gets it for free, because the eraser has always used this same
    stamp.

    PIXEL PERFECT is a stroke-level filter about which CELLS get a dab.

DEFERRED, NOT RETRACTED. `S.stroke.alphaMap` is an accumulator with no undo of
its own, so "paint it and then unpaint it" means writing 0 -- which erases what
an EARLIER part of the same stroke put there and cannot tell "this stroke
painted it" from "it was already 255". The filter holds the most recent cell
tentative and writes it only once the next cell proves it is not a corner. A
dropped cell was never painted.

BRESENHAM, because BE3's spacing debt places dabs along the PATH and on a
diagonal that lands at cells which skip. A corner filter needs a connected run
or it has no corners to find. This also closes a defect nobody had named: a
fast diagonal drag with the Pixel brush left gaps.

Source review: Evidence/source-review/BE13-aliased-and-pixel-perfect.md
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

DRIVER = Path(__file__).with_name("be13_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE13_TESTS = 37

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


@needs_node
class AliasedIsAllOrNothingTests(unittest.TestCase):
    """One alpha level, and it is the flow. That is the whole definition."""

    def test_an_aliased_mark_has_exactly_one_alpha_level(self):
        row = M["aliasedIsAllOrNothing"]
        self.assertEqual(1, row["aliasedLevels"], f"{row}")

    def test_an_antialiased_mark_has_many(self):
        """Guards the guard. "One level" is not evidence of anything unless the
        other method has more."""

        row = M["aliasedIsAllOrNothing"]
        self.assertTrue(row["antialiasedHasMany"], f"{row}")

    def test_it_is_a_method_on_the_ordinary_pipeline(self):
        """The brief says "not a second engine", and the code obliges: one
        branch in the loop that was already there, hoisted out of it with
        everything else BE8 hoisted."""

        code = _code_only()
        body = code[code.index("function stampAlphaMap("):]
        # Anchored on CODE, not on a comment: `_code_only` strips comments, so
        # a comment anchor is a substring that is never there.
        body = body[:body.index('if (preset === "scatter")')]
        self.assertIn("const aliased = !!S.brushAliased;", body)
        # BE17 RENAMED THE VALUE, and the rename is the point of the rename.
        # It used to be `const a = aliased ? opacity : falloff(...) * opacity`
        # -- coverage and Flow multiplied together in one step, which is only
        # possible while a dab's whole contribution is one number. Accumulation
        # needs them apart: coverage shapes the dab, Flow says what fraction of
        # a pass the dab is worth. So the branch this test owns is still one
        # branch in the loop that was already there, and it now yields COVERAGE.
        self.assertIn("const cov = aliased", body)

    def test_the_eraser_uses_the_same_method(self):
        """It needs no eraser code at all -- the eraser has always used this
        stamp, and BE4's note in `commitStroke` records that the two differ at
        commit and nowhere else."""

        row = M["theEraserUsesTheSameMethod"]
        self.assertEqual(1, row["levels"], f"{row}")
        self.assertGreater(row["cells"], 20, f"{row}")


@needs_node
class OrdinaryBrushesAreUnchangedTests(unittest.TestCase):
    """An acceptance criterion in as many words."""

    def test_a_round_brush_is_still_antialiased(self):
        self.assertGreater(M["ordinaryBrushesAreUntouched"]["levels"], 5)

    def test_no_ordinary_preset_declares_either_method(self):
        row = M["ordinaryBrushesAreUntouched"]
        self.assertFalse(row["aliasedFlag"])
        self.assertFalse(row["pixelPerfectFlag"])


@needs_node
class ThePixelPresetTests(unittest.TestCase):

    def test_it_declares_both_methods(self):
        row = M["thePixelPresetDeclaresBoth"]["pixel"]
        self.assertTrue(row["aliased"], f"{row}")
        self.assertTrue(row["pp"], f"{row}")

    def test_they_are_in_effect_at_its_own_size(self):
        """A toggle that is on but out of regime is exactly the defect BE1
        exists to catch, so the preset that declares them must be inside the
        regime that supports them."""

        self.assertTrue(M["thePixelPresetDeclaresBoth"]["pixel"]["active"])

    def test_they_do_not_leak_to_the_next_preset(self):
        """The sixth and seventh fields in this programme to need the
        unconditional-write rule."""

        self.assertTrue(M["thePixelPresetDeclaresBoth"]["resets"])

    def test_the_write_is_unconditional(self):
        code = _code_only()
        body = code[code.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.brushAliased = !!p.aliased;", body)
        self.assertIn("S.brushPixelPerfect = !!p.pixelPerfect;", body)


@needs_node
class PixelPerfectRemovesTheCornersTests(unittest.TestCase):
    """THE DEFECT IT REMOVES, measured on the shape that shows it.

    A line at a shallow angle staircases, and at every step the corner cell
    makes the line two pixels thick in that column.
    """

    def test_a_shallow_line_has_no_double_columns(self):
        row = M["aShallowLineHasNoDoubleColumns"]
        self.assertEqual(0, row["onThickColumns"], f"{row}")

    def test_the_same_line_without_it_does(self):
        """Guards the guard. Zero thick columns proves nothing if the
        unfiltered line had none either."""

        row = M["aShallowLineHasNoDoubleColumns"]
        self.assertGreater(row["offThickColumns"], 5, f"{row}")

    def test_it_removes_cells_rather_than_moving_them(self):
        row = M["aShallowLineHasNoDoubleColumns"]
        self.assertLess(row["onCells"], row["offCells"], f"{row}")

    def test_it_removes_exactly_the_corners_and_no_more(self):
        """ADDED AFTER A MUTATION ESCAPED. `_ppIsCorner` returning `true`
        unconditionally drops every second cell of a straight run -- and that
        also produces zero doubled columns and fewer cells, so it passed both
        guards above. One cell per doubled column, counted."""

        row = M["aShallowLineHasNoDoubleColumns"]
        self.assertTrue(row["removedExactlyTheCorners"], f"{row}")

    def test_a_diagonal_after_an_orthogonal_step_is_not_a_corner(self):
        """ADDED AFTER A SECOND MUTATION ESCAPED, for a different reason.

        Dropping the second leg's length check was invisible to every probe:
        a pure diagonal is rejected at the FIRST leg check, and a slow hand
        produces only orthogonal legs. The case that discriminates is a
        vertical step followed by a diagonal one, where the unchecked version
        reads 0-versus-1 on the x components and drops a cell that is not a
        corner at all.
        """

        row = M["aDiagonalAfterAnOrthogonalIsNotACorner"]
        self.assertTrue(row["identical"], f"{row}")

    def test_a_right_angle_also_loses_its_corner(self):
        """The documented method, not a defect. Pixel-perfect turns every L
        into a diagonal step -- which is what makes a hand-drawn curve read as
        one pixel thick -- and Aseprite behaves the same way. An owner who
        wants a square corner turns the mode off, which is what the toggle is
        for."""

        row = M["aRightAngleTurn"]
        self.assertLess(row["onCells"], row["offCells"], f"{row}")

    def test_a_perfect_diagonal_is_left_alone(self):
        """Every step is already diagonal, so there are no corners and the
        filter must find none. A filter that dropped cells here would be
        thinning a line that was already one pixel thick."""

        row = M["aPerfectDiagonalIsUntouched"]
        self.assertTrue(row["identical"], f"{row}")


@needs_node
class TheCellWalkIsItsOwnFeatureTests(unittest.TestCase):
    """SEPARATED FROM THE CORNER FILTER, and the first version of this package
    had them as one question.

    That was wrong in a way the measurements caught. With the filter off the
    engine fell back to BE3's arc-length spacing, so the "unfiltered" baseline
    had no doubled corners either -- and a comparison whose control case cannot
    exhibit the defect proves nothing.

    It is also wrong on its own terms. Arc-length spacing on a diagonal lands
    at cells that SKIP, so a fast diagonal drag with the Pixel brush left gaps.
    That is a defect nobody had named and it belongs to `aliased`.
    """

    def test_the_walk_leaves_no_gaps(self):
        row = M["theWalkLeavesNoGaps"]
        self.assertEqual(0, row["gaps"], f"{row}")

    def test_arc_length_spacing_did(self):
        """Guards the guard, and names the defect this closed."""

        row = M["theWalkLeavesNoGaps"]
        self.assertGreater(row["arcGaps"], 5, f"{row}")

    def test_the_walk_follows_aliased_not_the_filter(self):
        code = _code_only()
        body = code[code.index("function plotTo("):]
        body = body[:body.index("\n}")]
        self.assertIn("if (pixelWalkActive()) {", body)
        self.assertNotIn("if (pixelPerfectActive()) {", body)

    def test_the_filter_is_the_walk_plus_one_condition(self):
        code = _code_only()
        body = code[code.index("function pixelPerfectActive("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.brushPixelPerfect && pixelWalkActive()", body)


@needs_node
class NothingIsRetractedTests(unittest.TestCase):
    """"existing artwork beneath tentative pixels is preserved", and the reason
    the filter defers instead of unpainting."""

    def test_nothing_beneath_a_dropped_cell_is_lost(self):
        row = M["nothingBeneathIsLost"]
        self.assertEqual(0, row["lost"], f"{row}")

    def test_the_underlying_mark_was_actually_there(self):
        self.assertGreater(M["nothingBeneathIsLost"]["underlyingCells"], 100)

    def test_the_filter_never_writes_zero(self):
        """The structural half. A future edit that "optimised" the deferral
        into a retraction would pass every behavioural test above on a canvas
        that happened to be empty."""

        code = _code_only()
        body = code[code.index("function _ppOffer("):]
        body = body[:body.index("\n}")]
        self.assertNotIn("= 0;", body)
        self.assertNotIn("alphaMap", body)


@needs_node
class TheRegimeIsRespectedTests(unittest.TestCase):
    """"restrict Pixel Perfect to the supported small/one-pixel regime"."""

    def test_it_is_active_at_one_pixel(self):
        self.assertTrue(M["restrictedToItsRegime"]["atOne"])

    def test_it_is_not_active_on_a_wide_brush(self):
        """A corner filter on a 40px brush is meaningless: its cells are dab
        centres of a wide tip, and dropping one leaves a bite out of the
        stroke."""

        self.assertFalse(M["restrictedToItsRegime"]["atForty"])

    def test_it_needs_aliased_coverage(self):
        self.assertFalse(M["restrictedToItsRegime"]["withoutAlias"])

    def test_it_is_not_active_on_another_tool(self):
        self.assertFalse(M["restrictedToItsRegime"]["wrongTool"])

    def test_the_control_reports_when_it_is_out_of_regime(self):
        """A control that silently does nothing is the defect BE1's whole guard
        set exists to catch, and it would be a poor package that reintroduced
        it while fixing a different one."""

        ui = _code_only("canvas-ui.js")
        self.assertIn("function _syncPixelButtons()", ui)
        body = ui[ui.index("function _syncPixelButtons()"):]
        body = body[:body.index("\n}")]
        self.assertIn("C.pixelPerfectActive", body)
        self.assertIn("not in effect", body)
        # ONE WRITER. A mutation left the honest branch in place but dead --
        # `pp.title = "Pixel Perfect"; if (false) pp.title = ...` -- and a
        # substring search cannot tell a dead branch from a live one. Counting
        # the assignments can.
        self.assertEqual(
            1, body.count("pp.title ="),
            "the report has more than one writer, so a substring guard cannot "
            "tell which one runs")

    def test_both_toggles_are_reachable(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="aliasedBtn"', html)
        self.assertIn('id="pixelPerfectBtn"', html)

    def test_they_are_shown_for_the_eraser_too(self):
        """The eraser has no preset system of its own, so the context bar is
        the only way it can reach the method the acceptance says it can use."""

        ui = _code_only("canvas-ui.js")
        row = ui[ui.index("eraser:    ["):]
        row = row[:row.index("]")]
        self.assertIn("pixel-opts", row)


@needs_node
class TheStrokeEndsWhereItStoppedTests(unittest.TestCase):

    def test_the_last_cell_is_painted(self):
        """The filter holds one cell tentative. Without a flush every stroke
        would end one cell short -- which would look exactly like the filter
        being too aggressive and would be very hard to tell apart from it."""

        row = M["theLastCellIsPainted"]
        self.assertTrue(row["reachesTheEnd"], f"{row}")

    def test_commit_flushes_as_a_backstop(self):
        """For callers that commit without finishing -- the headless drivers,
        and any future caller that has not been taught."""

        code = _code_only()
        body = code[code.index("function commitStroke("):]
        body = body[:body.index("\n}")]
        self.assertIn("flushPixelPerfect();", body)


@needs_node
class ZoomChangesNothingTests(unittest.TestCase):
    """Everything here is in document coordinates and zoom reaches none of it.
    Asserted anyway, because BE9 found a zoom dependency in the stabiliser that
    nobody had suspected."""

    def test_the_output_is_identical_at_every_zoom(self):
        row = M["zoomChangesNothing"]
        self.assertTrue(row["identical"], f"{row['at']}")


@needs_node
class EvenDiametersTests(unittest.TestCase):
    """BE2 left this as an expectedFailure with BE13's name on it:

        "At an integer centre a diameter-d round dab covers 2*ceil(d/2)-1
         pixels, so Size 2 paints 1 px and Size 4 paints 3. Even widths need
         the dab centred on a half pixel -- cell-centre placement, which BE13
         owns."

    The engine's convention is that a pixel's integer index IS its centre. An
    odd diameter is therefore symmetric about an integer index and an even one
    must straddle a cell boundary.
    """

    def test_every_size_paints_its_own_width(self):
        row = M["evenDiametersPaintEvenWidths"]
        self.assertTrue(row["exact"], f"{row['at']}")

    def test_the_snap_is_aliased_only(self):
        """An antialiased dab spreads across the boundary anyway, and snapping
        it would quantise every stroke to the pixel grid -- a visible change to
        nine presets in service of a property only the tenth needs."""

        code = _code_only()
        body = code[code.index("function stampAlphaMap("):]
        body = body[:body.index("\n    const r = sz / 2;")]
        self.assertIn("if (S.brushAliased) {", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE13_TESTS, found)


if __name__ == "__main__":
    unittest.main()
