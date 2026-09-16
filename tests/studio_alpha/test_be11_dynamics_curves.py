"""BE11: presets that RESPOND, and a fallback that is declared rather than
assumed.

Everything before this package gave a preset a fixed shape and a fixed weight.
This lets a preset say how it reacts to the hand: pressure, speed, direction
and tilt, mapped through a named curve onto size, flow, angle or ratio.

THE ONE THAT MATTERS. CT2 has reported `pressureAvailable` on every sample
since it landed, and wrote it for exactly this:

    "a pressure dynamic must not act on a substituted value, or every mouse
     stroke would be drawn as though the owner pressed exactly half way."

The substituted value IS 0.5 -- a mouse reports 0.5 while a button is down. So
a naive pressure curve does not merely guess, it guesses the MIDDLE of the
range, and a size rule mapped 0.2 to 1.0 would draw every mouse stroke at 60%
width for a reason nothing in the UI explains.

Every rule therefore declares its own fallback INPUT value. Measured at three:

    fallback 0     mean  55.89
    fallback 0.5   mean 145.36
    fallback 1     mean 235.66  -- byte-identical to no curve at all

NO PEN ON THIS MACHINE. What a synthetic ramp proves is the arithmetic and the
plumbing: that a curve maps an input to a per-dab modifier, that the modifier
reaches the stamp, and that an unavailable input takes the declared fallback.
It proves nothing about hardware and nothing here claims otherwise.

Source review: Evidence/source-review/BE11-dynamics-curves.md
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

DRIVER = Path(__file__).with_name("be11_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE11_TESTS = 35

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
class NeutralIsExactTests(unittest.TestCase):
    """The acceptance criterion "presets using no curves remain identical", and
    the one every other probe depends on."""

    def test_an_empty_rule_list_changes_nothing(self):
        self.assertTrue(M["emptyIsNeutral"]["identical"])

    def test_a_unit_range_is_exactly_neutral(self):
        """`min === max === 1` must be neutral by ARITHMETIC, not by a special
        case that a later edit could forget."""

        self.assertTrue(M["theRangeIsHonest"]["unitIsNeutral"])

    def test_a_half_range_is_not(self):
        """Guards the guard: a neutral test on an implementation that ignored
        the range entirely would pass."""

        self.assertTrue(M["theRangeIsHonest"]["halfIsHalf"])
        self.assertLess(M["theRangeIsHonest"]["ratio"], 0.9)


@needs_node
class TheFallbackIsTheRulesOwnTests(unittest.TestCase):
    """THE HEART OF THE PACKAGE.

    Same curve, same stroke, same ramp, and a device that reports no pressure.
    Three declared fallbacks must give three different marks.
    """

    def test_three_fallbacks_give_three_marks(self):
        row = M["unavailableUsesTheDeclaredFallback"]
        self.assertEqual(3, row["distinct"], f"{row}")

    def test_a_fallback_of_one_is_byte_identical_to_no_curve(self):
        self.assertTrue(M["unavailableUsesTheDeclaredFallback"]["oneIsNeutral"])

    def test_a_fallback_of_zero_is_not_silently_maximal(self):
        """The named defect. A fake maximal value would make all three equal to
        the top of the range."""

        row = M["unavailableUsesTheDeclaredFallback"]
        self.assertTrue(row["zeroIsNotMaximal"], f"{row}")
        self.assertLess(row["at"]["f0"], row["at"]["f05"])
        self.assertLess(row["at"]["f05"], row["at"]["f1"])

    def test_a_pen_and_a_mouse_are_told_apart(self):
        self.assertTrue(M["penAndMouseDiffer"]["differ"])

    def test_the_availability_flag_is_what_is_read(self):
        """Not the pressure VALUE. A mouse reports a perfectly well-formed 0.5
        and it means nothing, which is the whole reason CT2 carries the flag."""

        code = _code_only()
        body = code[code.index("function _dynInput("):]
        body = body[:body.index("\n}")]
        self.assertIn("pen.pressureAvailable", body)
        self.assertIn("pen.tiltAvailable", body)

    def test_no_rule_may_inherit_someone_elses_fallback(self):
        """Every shipped rule declares its own. A rule that omitted it would
        take a default this file picked, which is the assumption the package
        exists to remove."""

        self.assertTrue(M["shippedPresetsDeclareCurves"]["allWellFormed"])


@needs_node
class TheTargetsAreDistinguishableTests(unittest.TestCase):
    """Size and flow must not be one control with two names -- which is the
    defect BE5 removed one level up."""

    def test_a_size_rule_narrows_the_stroke(self):
        row = M["sizeCurveActs"]
        self.assertTrue(row["narrowerEarly"], f"{row}")

    def test_a_size_rule_follows_the_curve_rather_than_scaling_flat(self):
        """A rule that narrowed the whole stroke equally would be a size
        CHANGE, not a size CURVE."""

        row = M["sizeCurveActs"]
        self.assertTrue(row["widensAlongTheRamp"], f"{row}")

    def test_a_flow_rule_dims_without_narrowing(self):
        row = M["flowCurveActs"]
        self.assertTrue(row["dimmer"], f"{row}")
        self.assertTrue(row["sameWidthEarly"], f"{row}")
        self.assertTrue(row["sameWidthLate"], f"{row}")

    def test_tilt_drives_the_tip_ratio(self):
        row = M["tiltDrivesRatio"]
        self.assertTrue(row["leaningNarrows"], f"{row}")

    def test_a_device_without_tilt_gets_the_shape_it_always_had(self):
        self.assertTrue(M["tiltDrivesRatio"]["mouseIsUpright"])

    def test_the_angle_target_is_added_and_not_multiplied(self):
        """A multiplicative angle is meaningless -- zero degrees times anything
        is zero degrees -- and BE7 already treats the tip angle as a sum of
        three separate terms."""

        code = _code_only()
        body = code[code.index("function applyDynamics("):]
        body = body[:body.index("\n}")]
        self.assertIn('out.angle += factor', body)

    def test_opacity_and_grain_are_not_targets(self):
        """Both are named in the brief and both are refused here, for reasons
        that come from earlier packages.

        `opacity` is applied ONCE at commit and bounds the whole stroke -- BE5's
        contract, and the reason Flow exists. `grain` is applied ONCE at merge
        so overlap cannot wash the paper out -- BE10's contract, and a per-dab
        grain factor would restore the amount-versus-rate defect that package
        removed, in the package immediately after it.
        """

        code = _code_only()
        line = re.search(r"const DYN_TARGETS = \[(.*?)\];", code, flags=re.S)
        self.assertIsNotNone(line)
        self.assertNotIn("opacity", line.group(1))
        self.assertNotIn("grain", line.group(1))


@needs_node
class TheCurveShapesAreRealTests(unittest.TestCase):
    """Six named shapes. A table of six names that all evaluated the same would
    be the exact complaint this programme started from, one level down."""

    def test_every_shape_gives_a_different_mark(self):
        row = M["curveShapesDiffer"]
        self.assertEqual(row["count"], row["distinct"], f"{row['at']}")

    def test_the_names_mean_what_they_say(self):
        """easeIn below linear below easeOut on a rising ramp. A table with
        them in a different order would still pass a distinctness count."""

        self.assertTrue(M["curveShapesDiffer"]["ordered"],
                        f"{M['curveShapesDiffer']['at']}")

    def test_flat_pins_the_target_at_the_top_of_its_range(self):
        self.assertTrue(M["curveShapesDiffer"]["flatIsTheTop"])

    def test_they_are_analytic_rather_than_an_interpolated_table(self):
        """"Deterministic interpolation" is the requirement. A named closed
        form is deterministic by construction -- there is no interpolation code
        to get wrong -- and the curve EDITOR is explicitly out of V1 scope, so a
        control-point list would be a format with no producer."""

        code = _code_only()
        body = code[code.index("const DYN_CURVES = {"):]
        body = body[:body.index("\n};")]
        for banned in ("Math.random", "for (", "while (", "[i]"):
            self.assertNotIn(banned, body)


@needs_node
class InputsAreHonestAboutAvailabilityTests(unittest.TestCase):

    def test_direction_is_always_available(self):
        """It is a property of the path, not of the device. BE7 already
        advances a smoothed heading per dab so the tip can point where the
        stroke is going, and this reads it."""

        row = M["directionIsAlwaysAvailable"]
        self.assertEqual(3, row["distinct"], f"{row['at']}")

    def test_speed_needs_a_timestamp_and_says_so_when_it_has_none(self):
        row = M["speedNeedsATimestamp"]
        self.assertTrue(row["untimedIsNeutral"], f"{row}")

    def test_speed_acts_when_it_has_one(self):
        row = M["speedNeedsATimestamp"]
        self.assertTrue(row["fasterIsLighter"], f"{row}")

    def test_the_timestamp_reaches_the_engine_from_the_pointer_seam(self):
        """One line, at the one place a sample exists. CT2 has produced the
        timestamp on every sample since it landed and nothing had asked."""

        code = _code_only("canvas-ui.js")
        self.assertIn("if (C.noteSample) C.noteSample(s);", code)
        note = code.index("C.noteSample(s)")
        stab = code.index("C.stab(s.x, s.y, s.pressure)", note - 400)
        self.assertLess(
            note, stab,
            "the sample is noted after the stabiliser has already run, so the "
            "dab it describes has already been placed")

    def test_speed_is_measured_per_segment_and_not_per_dab(self):
        """The dabs along one segment are interpolated positions between two
        real samples. They share the hand movement that produced them, and a
        per-dab speed would be an invention with a plausible shape."""

        code = _code_only()
        body = code[code.index("function plotTo("):]
        body = body[:body.index("\n}")]
        self.assertIn("const segSpeed = _segmentSpeed(dist);", body)
        loop = body[body.index("while (dist > 0"):]
        self.assertNotIn("_segmentSpeed", loop)


@needs_node
class StateCannotLeakTests(unittest.TestCase):
    """The fourth field in this programme to need the unconditional-write rule,
    and the first to also need a deep copy."""

    def test_a_preset_switch_resets_the_rules(self):
        row = M["curvesDoNotLeak"]
        self.assertTrue(row["resets"], f"{row}")

    def test_editing_the_live_rules_cannot_edit_the_preset_table(self):
        """A shared array would let one edit reach the table itself -- a leak
        that survives a document reload and that nothing in the UI explains."""

        self.assertTrue(M["curvesDoNotLeak"]["tableIsIntact"])

    def test_the_write_is_unconditional(self):
        code = _code_only()
        body = code[code.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.brushCurves = (p.curves || []).map(", body)

    def test_the_pen_state_is_cleared_at_the_start_of_a_stroke(self):
        """A mouse stroke following a pen stroke would otherwise inherit the
        pen's availability flags and act on a pressure nothing measured."""

        code = _code_only()
        body = code[code.index("function beginStroke("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.stroke._pen = null;", body)
        self.assertIn("S.stroke._lastTime = null;", body)


@needs_node
class TheShippedPresetsTests(unittest.TestCase):
    """Nine of ten declare curves. The tenth is Pixel and that is a contract."""

    def test_all_but_two_presets_declare_rules(self):
        """BE14 rebuilt the set from ten presets to sixteen. Fourteen of them
        declare curves; the two that do not are Fine Liner, whose identity is
        the stabiliser rather than the response, and Pixel Perfect, whose lack
        of them is BE2's contract."""

        row = M["shippedPresetsDeclareCurves"]
        self.assertEqual(14, row["withCurves"])
        without = [n for n, r in row["at"].items() if r["rules"] == 0]
        self.assertEqual(["Fine Liner", "Pixel Perfect"], sorted(without))

    def test_the_pixel_brush_declares_none(self):
        """BE2 CONTRACT. One literal document pixel at full alpha on every
        document. Any curve at all would make that conditional on how the owner
        was holding the pen."""

        self.assertTrue(M["shippedPresetsDeclareCurves"]["pixelHasNone"])

    def test_on_a_mouse_every_shipped_preset_paints_what_it_did_before(self):
        """Every fallback in the shipped table maps to the preset's pre-BE11
        behaviour.

        That is a CURATION decision about this table, not a property of the
        mechanism -- `TheFallbackIsTheRulesOwnTests` shows the mechanism takes
        any fallback and that three of them give three marks. This is the test
        that holds the table to it, so the owner's mouse strokes do not change
        under them tonight.
        """

        row = M["aMouseSeesNoChange"]
        self.assertTrue(row["all"],
                        f"presets that changed: "
                        f"{[k for k, v in row['at'].items() if not v]}")


class NoRealPenIsClaimedTests(unittest.TestCase):
    """The brief requires this in as many words: "real pen claims remain
    unmade without hardware"."""

    def test_the_driver_says_what_a_synthetic_ramp_proves(self):
        driver = DRIVER.read_text(encoding="utf-8")
        self.assertIn("There is no pen on this machine", driver)

    def test_the_speed_constant_is_admitted_rather_than_derived(self):
        """The same treatment BE9's STAB_SCREEN_PX_PER_STEP gets. A feel
        constant dressed up as a measurement is worse than one that says so."""

        code = (FRONTEND / "canvas-core.js").read_text(encoding="utf-8")
        block = code[code.index("const DYN_SPEED_REF") - 600:
                     code.index("const DYN_SPEED_REF") + 40]
        self.assertIn("chosen rather than derived", block)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE11_TESTS, found)


if __name__ == "__main__":
    unittest.main()
