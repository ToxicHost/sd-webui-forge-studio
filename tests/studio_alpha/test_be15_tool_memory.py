"""Every brush field travels with the tool, or it is a different brush.

FOUND BY AN ACCEPTANCE JOURNEY, NOT BY A PACKAGE'S OWN TESTS.

After an F5 in a real browser the DOCUMENT's paper came back exactly -- BE10's
chain works at all five of its stages -- and the BRUSH came back with
`brushGrain: 0`. `_saveToolSettings` saved fourteen fields and none of the five
added between BE10 and BE13:

    brushGrain         BE10   how strongly this brush finds the paper
    brushCurves        BE11   its dynamics rules
    brushAirbrush      BE12   whether it deposits on a timer
    brushAliased       BE13   hard pixel edges
    brushPixelPerfect  BE13   one-pixel corners

The consequence is owner-visible and quiet: switch Brush -> Eraser -> Brush, or
reload, and Size, Opacity, Flow, Hardness, Smoothing, Angle, Taper, Ratio,
Spikes, Falloff and Density all come back while the tooth, the curves and the
pixel methods do not. The brush is a different brush and nothing says so.

WHY NO PACKAGE CAUGHT IT. Each of BE10 through BE13 tested its field through
`applyBrushPreset`, through the document chain, or through the engine. None of
them looked at the per-tool memory, which is a FIFTH place brush state lives
and which none of them had a reason to visit.

WHY THIS MODULE EXISTS RATHER THAN FIVE MORE ASSERTIONS IN FIVE PLACES. The
next field someone adds will have the same problem, and a guard that enumerates
the CONTRACT catches it; five guards that each name one field do not. The list
below is derived from the state block itself, so a new brush field is a failure
here until somebody decides whether it travels.

The precedent is in the same function, written for `brushSizeMode`:
"Travels WITH brushSize, never apart from it."
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
UI = FRONTEND / "canvas-ui.js"

EXPECTED_BE15_TESTS = 8

#: Brush state that MUST survive a tool switch, and the package that added it.
#:
#: Deliberately not derived from `S` by pattern: a field that should travel and
#: a field that should not both start with `brush`, and the difference is a
#: decision rather than a naming convention. `customTip` does not travel
#: because there is no importer to produce one; `brushPreset` does not because
#: it is the TIP SHAPE and the preset picker owns it.
MUST_TRAVEL = {
    "brushSize": "the size itself",
    "brushSizeMode": "BE2 -- a literal 5 and a relative 5 are different marks",
    "brushOpacity": "BE5 -- the stroke-wide bound",
    "brushFlow": "BE5 -- the per-dab contribution",
    "brushBuildup": "CT3 -- whether dabs accumulate",
    "brushHardness": "BE5",
    "smoothing": "BE9",
    "brushAngle": "CT3 / BE6",
    "brushTaperIn": "CT3",
    "brushRatio": "BE6",
    "brushSpikes": "BE6",
    "brushFalloff": "BE6",
    "brushDensity": "BE6 / BE7",
    "brushGrain": "BE10 -- the tooth",
    "brushCurves": "BE11 -- the dynamics rules",
    "brushAirbrush": "BE12 -- time deposition",
    "brushAliased": "BE13 -- hard pixel edges",
    "brushPixelPerfect": "BE13 -- one-pixel corners",
}


def _code_only(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


def _block(code: str, header: str) -> str:
    body = code[code.index(header):]
    return body[:body.index("\n}")]


class EveryFieldIsSavedTests(unittest.TestCase):

    def setUp(self):
        self.code = _code_only(UI)
        self.save = _block(self.code, "function _saveToolSettings(")

    def test_every_field_that_must_travel_is_written_to_the_store(self):
        missing = [f for f in MUST_TRAVEL if (f + ":") not in self.save]
        self.assertEqual(
            [], missing,
            "these are saved nowhere, so a tool switch loses them: "
            + ", ".join(f"{f} ({MUST_TRAVEL[f]})" for f in missing))

    def test_the_curves_are_copied_rather_than_shared(self):
        """BE11's reason, one level out: a shared array would let a later edit
        reach a SAVED setting, and a saved setting is exactly the thing an
        owner expects to stay put."""

        self.assertIn("brushCurves: (S.brushCurves || []).map(", self.save)


class EveryFieldIsRestoredTests(unittest.TestCase):

    def setUp(self):
        self.code = _code_only(UI)
        self.restore = _block(self.code, "function _restoreToolSettings(")

    def test_every_field_that_must_travel_is_read_back(self):
        missing = [f for f in MUST_TRAVEL
                   if ("S." + f + " =") not in self.restore]
        self.assertEqual(
            [], missing,
            "these are saved but never restored, which is the same loss with "
            "an extra step: " + ", ".join(missing))

    def test_the_new_fields_use_the_nullish_default(self):
        """`??`, not `||`, for the reason the Flow line already gives: a saved
        `brushGrain` of 0 is Hard Ink's deliberate choice, and `||` would
        replace it with whatever default the line named.

        Every one of the five is a value whose zero or false is meaningful."""

        for field, default in (("brushGrain", "0"), ("brushAirbrush", "false"),
                               ("brushAliased", "false"),
                               ("brushPixelPerfect", "false")):
            with self.subTest(field=field):
                self.assertIn(
                    f"S.{field} = saved.{field} ?? {default};", self.restore)

    def test_the_curves_are_copied_on_the_way_back_too(self):
        self.assertIn("S.brushCurves = (saved.brushCurves ?? []).map(",
                      self.restore)


class TheContractIsCompleteTests(unittest.TestCase):
    """The half that catches the NEXT field, rather than the last five."""

    def test_no_brush_field_in_the_state_block_is_unaccounted_for(self):
        """Every `brush*` field declared in `S` is either in MUST_TRAVEL or in
        the exclusion list below WITH A REASON. A new one is a failure here
        until somebody decides which it is.

        That is the whole point of this module: five separate packages each
        added a field, each tested it thoroughly in its own terms, and all five
        missed the same fifth place.
        """

        #: Brush state that deliberately does NOT travel with the tool.
        excluded = {
            # The TIP SHAPE, not a brush setting. The preset picker owns it and
            # the four buttons beside the picker set it directly.
            "brushPreset",
            # A per-STROKE dynamics bundle -- spacing and the jitters -- which
            # `applyBrushPreset` rewrites wholesale. Saving it here would give
            # the tool memory and the preset table two owners for one value.
            "brushDynamics",
        }
        core = _code_only(FRONTEND / "canvas-core.js")
        state = core[core.index("const S = {"):]
        state = state[:state.index("\n};")]
        declared = set(re.findall(r"^\s{4}(brush[A-Za-z]*)\s*:", state, flags=re.M))
        self.assertTrue(declared, "the state block's brush fields did not parse")
        unaccounted = declared - set(MUST_TRAVEL) - excluded
        self.assertEqual(
            set(), unaccounted,
            "new brush state that nobody has decided about: "
            f"{sorted(unaccounted)}. Add it to MUST_TRAVEL and to "
            "_saveToolSettings, or to the exclusion list with a reason.")

    def test_the_exclusions_are_genuinely_absent_from_the_store(self):
        """Guards the guard. An exclusion that is actually saved would mean the
        list is describing something other than the code."""

        save = _block(_code_only(UI), "function _saveToolSettings(")
        for field in ("brushPreset", "brushDynamics"):
            with self.subTest(field=field):
                self.assertNotIn(field + ":", save)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE15_TESTS, found)


if __name__ == "__main__":
    unittest.main()
