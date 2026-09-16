"""BE14: fifteen brushes, or fifteen labels?

THIS IS THE PACKAGE THE COMPLAINT WAS ABOUT. The owner painted with all ten
presets and reported that they "feel smoother but functionally look identical".
Every package from BE1 to BE13 was a prerequisite for answering that and none
of them answered it: the LIST was still ten entries tuned before any of the new
capability existed.

THE FINDING THAT SHAPED THE PACKAGE. BE6 made Ratio, Spikes, Density, Angle and
Falloff work on every tip and verified it by rendering -- and not one shipped
preset set any of them except Scatter Dust's density. Five controls, proven
alive, with no preset demonstrating them.

That was not two coincidences. `applyBrushPreset` wrote nine of the fifteen
supported fields and skipped exactly those six, so they LEAKED between presets
and no preset dared use them. BE1's own harness worked around it -- `reset()`
in be1_measure.js clears "the six leaking fields" by hand, with a comment
saying the harness must not inherit the bug it is measuring.

NEAREST NEIGHBOUR IS THE TEST. A set where every preset differs from SOME other
preset is easy and worthless; the complaint was that they felt ALIKE, which is
a statement about neighbours. Every preset is compared to its most similar
sibling on four measurable axes -- painted area, mean alpha, width, and the
standard deviation of alpha, which is where paper and density live.

WHAT THIS CANNOT PROVE. The acceptance is an owner painting with all of them in
one browser session. These tests can show fifteen presets are measurably
distinct from their nearest neighbours. They cannot show they FEEL like fifteen
different brushes, and the contact sheet exists because of that.

Source review: Evidence/source-review/BE14-preset-rebuild.md
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

DRIVER = Path(__file__).with_name("be14_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE14_TESTS = 25

#: The brief asks for "approximately 12-20 truthful presets, with exact count
#: governed by quality". Pinned so an accidental deletion is loud.
EXPECTED_PRESET_COUNT = 16

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=600)
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
class NoPresetIsAnotherPresetTests(unittest.TestCase):
    """The complaint, answered as directly as a number can answer it."""

    def test_no_two_presets_render_identically(self):
        row = M["noTwoRenderAlike"]
        self.assertEqual([], row["duplicates"], f"{row}")

    def test_every_preset_differs_from_its_NEAREST_neighbour(self):
        """The demanding version. Differing from SOME other preset is easy;
        the complaint was about neighbours."""

        row = M["nearestNeighbour"]
        close = [(n, d) for n, d in row["at"].items() if d["distance"] < 0.12]
        self.assertEqual(
            [], close,
            "these presets are within 12% of a sibling on every axis: "
            f"{close}")

    def test_the_closest_pair_is_named_in_the_output(self):
        """So a future change that narrows the set has something to read
        rather than a pass/fail."""

        row = M["nearestNeighbour"]["closestPair"]
        self.assertIn("preset", row)
        self.assertIn("neighbour", row)


@needs_node
class EveryFieldIsWrittenTests(unittest.TestCase):
    """THE LEAK THAT KEPT THE SET FLAT.

    `applyBrushPreset` wrote nine of fifteen supported fields. The six it
    skipped -- ratio, spikes, density, angle, taperIn, falloff -- are exactly
    the six BE6 proved alive and exactly the six no preset had ever set.
    """

    def test_no_field_survives_a_preset_switch(self):
        row = M["everyFieldIsWritten"]
        self.assertEqual({}, row["leaks"], f"{row}")

    def test_the_six_are_written_by_name(self):
        code = _code_only()
        body = code[code.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        for field in ("S.brushRatio", "S.brushSpikes", "S.brushDensity",
                      "S.brushAngle", "S.brushTaperIn", "S.brushFalloff"):
            self.assertIn(field + " =", body)

    def test_the_driver_does_not_clear_them_itself(self):
        """BE1's harness had to, and said so. This one deliberately does not:
        if any field still leaks, the nearest-neighbour comparison sees two
        presets agreeing when they should not."""

        driver = DRIVER.read_text(encoding="utf-8")
        self.assertIn("NOTHING IS CLEARED BY HAND HERE", driver)
        body = driver[driver.index("function reset("):]
        body = body[:body.index("\n}")]
        self.assertNotIn("S.brushRatio =", body)
        self.assertNotIn("S.brushFalloff =", body)


@needs_node
class TheEngineIsActuallyUsedTests(unittest.TestCase):
    """A brush set that never uses half its own engine is a brush set that
    feels alike. Every capability the packages before this one built must be
    demonstrated by at least one preset."""

    def test_no_capability_is_unused(self):
        row = M["capabilitiesAreUsed"]
        self.assertEqual([], row["unused"], f"{row['at']}")

    def test_the_shape_controls_BE6_proved_are_each_used(self):
        at = M["capabilitiesAreUsed"]["at"]
        for control in ("ratio", "spikes", "density", "angle", "falloffGauss"):
            with self.subTest(control=control):
                self.assertGreater(at[control], 0)

    def test_spikes_is_used_by_exactly_the_preset_that_advertises_it(self):
        """BE6 proved Spikes alive on every tip in a package whose own record
        noted that no preset set it. This is the first that does."""

        self.assertIn("Bristle Rake", M["capabilitiesAreUsed"]["detail"]["spikes"])

    def test_angle_is_used_with_follow_stroke_OFF(self):
        """Which is what a calligraphy nib IS, and the case CT3's Angle control
        was added for: with Follow stroke on, Angle only offsets the heading."""

        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        entry = table[table.index('name: "Calligraphy"'):]
        entry = entry[:entry.index("    },")]
        self.assertIn("angle: 45", entry)
        self.assertIn("followStroke: false", entry)


@needs_node
class ThePaperIsRevealedDifferentlyTests(unittest.TestCase):
    """BE10's acceptance criterion, shipped as presets rather than as a test
    fixture: "Pencil and Pastel share the same paper location but reveal it
    differently"."""

    def test_named_neighbour_pairs_differ_on_the_same_paper(self):
        for row in M["onPaper"]["pairsDiffer"]:
            with self.subTest(pair=row["pair"]):
                material = (row["meanGap"] > 8 or row["widthGap"] > 3
                            or row["sdGap"] > 5)
                self.assertTrue(material, f"{row}")

    def test_charcoal_and_pastel_are_not_the_same_brush(self):
        """The pair the brief names. Both live at nearly full tooth, so if the
        paper were the only thing separating presets they would collapse."""

        rows = M["onPaper"]["at"]
        self.assertNotEqual(rows["Charcoal"], rows["Pastel"])


@needs_node
class OldNamesStillResolveTests(unittest.TestCase):
    """A preset name is a KEY. The per-tool memory and any recovered document
    may carry it, and `applyBrushPreset` returns false on an unknown name and
    changes NOTHING -- so a rename without aliases makes an owner's saved
    choice stop applying quietly rather than loudly."""

    def test_every_old_name_resolves_to_its_new_preset(self):
        row = M["oldNamesResolve"]
        self.assertTrue(row["all"], f"{row['at']}")

    def test_an_unknown_name_still_changes_nothing(self):
        """Which is what makes the alias table necessary rather than
        decorative."""

        self.assertTrue(M["oldNamesResolve"]["unknownIsRefused"])

    def test_the_alias_table_covers_every_rename(self):
        code = _code_only()
        table = code[code.index("const BRUSH_PRESET_ALIASES"):]
        table = table[:table.index("\n};")]
        for old in ("Soft Brush", "Flat Shader", "Bold Marker", "Pixel"):
            self.assertIn('"' + old + '"', table)

    def test_no_alias_shadows_a_live_preset_name(self):
        """An alias that matched a current name would make the lookup answer
        the wrong preset for a name that is still valid."""

        names = set(M["presets"].keys())
        code = _code_only()
        table = code[code.index("const BRUSH_PRESET_ALIASES"):]
        table = table[:table.index("\n};")]
        aliases = re.findall(r'^\s*"([^"]+)":', table, flags=re.M)
        self.assertTrue(aliases)
        for alias in aliases:
            self.assertNotIn(alias, names, f"{alias} is both an alias and a preset")


@needs_node
class EveryPresetSaysWhatItIsTests(unittest.TestCase):

    def test_every_preset_has_a_description(self):
        row = M["descriptions"]
        self.assertTrue(row["all"], f"{row['at']}")

    def test_the_descriptions_are_distinct(self):
        """Fifteen copies of "a brush" would satisfy the test above."""

        row = M["descriptions"]
        self.assertEqual(EXPECTED_PRESET_COUNT, row["distinct"], f"{row}")

    def test_no_description_claims_hardware_that_is_absent(self):
        """The brief requires unavailable hardware dependencies to be declared,
        and BE11's fallbacks make every preset paint its pre-BE11 mark on a
        mouse. A description that promised pressure behaviour would be a claim
        a mouse cannot honour."""

        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        descs = re.findall(r'desc: "([^"]+)"', table)
        self.assertEqual(EXPECTED_PRESET_COUNT, len(descs))
        for d in descs:
            low = d.lower()
            for claim in ("pressure", "tilt", "pen ", "stylus"):
                self.assertNotIn(claim, low, f"{d!r} claims hardware")


class TheSetIsWhatTheBriefAskedForTests(unittest.TestCase):

    def test_the_count_is_in_the_briefs_range(self):
        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        count = table.count('name: "')
        self.assertEqual(EXPECTED_PRESET_COUNT, count)
        self.assertGreaterEqual(count, 12)
        self.assertLessEqual(count, 20)

    def test_no_mask_preset_ships(self):
        """UNCHANGED FROM CT3, and the reason is unchanged too: mask mode
        forces the tip round and the hardness to 1, and `exportMask` binarises
        at alpha > 0 -- so a soft mask preset could not paint a soft mask, and
        Mask Hard alone would be a preset for the only behaviour there is.

        BE10 reinforces it rather than weakening it: paper is deliberately not
        applied in mask mode, because a binarised grainy mask is a mask full of
        holes.
        """

        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        self.assertNotIn('name: "Mask', table)

    def test_no_smudge_preset_ships(self):
        """"Smudge does not become a brush preset until CT10 migrates Smudge to
        the shared engine." It still reads `toolStrength`."""

        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        self.assertNotIn('name: "Smudge', table)

    def test_no_preset_asks_for_a_custom_tip(self):
        """Custom Tip has no importer -- CT3 disabled the button on the owner's
        decision rather than silently degrading it to Round -- so a preset that
        asked for one would fall through to a plain round brush."""

        code = _code_only()
        table = code[code.index("const DEFAULT_BRUSH_PRESETS"):]
        table = table[:table.index("\n];")]
        self.assertNotIn('preset: "custom"', table)

    def test_the_picker_is_built_from_the_table(self):
        """So fifteen presets need no markup change, and a sixteenth would
        appear without one. The four buttons beside it are TIP SHAPES, which is
        a different thing that happens to share the word."""

        ui = _code_only("canvas-ui.js")
        self.assertIn("for (const entry of list)", ui)
        self.assertIn("DEFAULT_BRUSH_PRESETS", ui)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE14_TESTS, found)


if __name__ == "__main__":
    unittest.main()
