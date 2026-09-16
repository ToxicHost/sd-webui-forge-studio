"""BE5: one hardness curve, one spacing rule, and Flow that means one thing.

TWO CLIFFS AT THE SAME THRESHOLD.

    dabAlpha        hardness >= 0.01  smoothstep from a solid core, peak 1.0
                    hardness <  0.01  a hand-written ramp, peak 0.4

    spacingFraction hardness <  0.01  a flat 0.10
                    otherwise         baseSpacing * (0.3 + 0.7 * hardness)

So a brush at hardness 0.011 painted a full-strength core spaced at 1.8% of its
width, and one at 0.009 painted a 40% core spaced at 10% -- a four-fold change
in dab count and a two-and-a-half-fold change in strength, for one hundredth of
a step on a control that says nothing about either.

The brief requires both removed together, and it is right: fixing one alone
leaves the acceptance sweep failing on the half nobody looked at.

THE TWO FIXES TURNED OUT TO BE ONE.

`stampAlphaMap` forced accumulation for soft tips, with a flow-level ceiling to
stop it running away. The comment justifying that said a max-blend "would cap a
soft brush at 40% grey no matter how long you painted" -- true, and true only
because of the 0.4 curve. With the curve reaching full strength, a max-blended
soft dab caps at FLOW, which is what Flow is supposed to mean. So the ceiling
had nothing left to do and Buildup became the only accumulation switch.

WHAT THIS PACKAGE DOES NOT FIX.

The owner's report that "Soft Brush is just flow 40% and looks like it's
actually 50% opacity" is NOT closed by BE5. At flow 40 with buildup off the
stroke peaks at 102 before and after, because 102 is what flow 40 means. The
measured change at that setting is a 2px flat core becoming a peak. What BE5
does fix at the top of the range is real -- a fully-flowing soft brush could
previously never exceed 206 of 255 -- but the preset shipping at flow 40 with
buildup off is a CURATION question and belongs to BE14.
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

DRIVER = Path(__file__).with_name("be3_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE5_TESTS = 22

#: The brief's sweep. 0.009 / 0.010 / 0.011 straddle the removed threshold.
HARDNESSES = ("h0", "h0.001", "h0.005", "h0.009", "h0.01", "h0.011",
              "h0.1", "h0.5", "h1")

#: Either side of the old cliff. Nothing may jump across this pair.
ACROSS = ("h0.009", "h0.01")

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


def _code_only() -> str:
    source = CORE.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class NothingJumpsAtTheOldThresholdTests(unittest.TestCase):
    """Addendum section 8.2, quantity by quantity."""

    def _pair(self, key):
        return (M["hardnessSweep"][ACROSS[0]][key],
                M["hardnessSweep"][ACROSS[1]][key])

    def test_spacing_does_not_jump(self):
        low, high = self._pair("spacingFraction")
        self.assertAlmostEqual(
            low, high, delta=0.002,
            msg=f"spacing jumped from {low} to {high} across hardness 0.01")

    def test_the_emitted_gap_does_not_jump(self):
        low, high = self._pair("gapPx")
        self.assertAlmostEqual(low, high, delta=0.5)

    def test_core_alpha_does_not_jump(self):
        low, high = self._pair("coreAlpha")
        self.assertEqual(
            low, high,
            f"core alpha jumped from {low} to {high} across hardness 0.01 -- "
            "the two falloff curves are back")

    def test_the_edge_profile_does_not_jump(self):
        low, high = self._pair("edgeProfile")
        for i, (a, b) in enumerate(zip(low, high)):
            with self.subTest(sample=i):
                self.assertAlmostEqual(a, b, delta=3)

    def test_stroke_coverage_does_not_jump(self):
        low, high = self._pair("strokePixels")
        self.assertAlmostEqual(low, high, delta=max(20, low * 0.02))


@needs_node
class TheCurveIsContinuousThroughoutTests(unittest.TestCase):
    """Not merely at the old threshold -- everywhere on the control."""

    def test_every_hardness_reaches_full_strength_at_the_core(self):
        """A soft brush is soft at its EDGE. A core that peaks at 40% is not a
        soft brush, it is a weak one, and no control said so."""

        for key in HARDNESSES:
            with self.subTest(hardness=key):
                self.assertEqual(255, M["hardnessSweep"][key]["coreAlpha"])

    def test_spacing_is_monotonic_in_hardness(self):
        fracs = [M["hardnessSweep"][k]["spacingFraction"] for k in HARDNESSES]
        self.assertEqual(sorted(fracs), fracs, f"spacing is not monotonic: {fracs}")

    def test_the_edge_hardens_monotonically(self):
        """Sampled a few pixels out, alpha must rise with hardness -- that is
        what the control means."""

        at6 = [M["hardnessSweep"][k]["edgeProfile"][3] for k in HARDNESSES]
        self.assertEqual(sorted(at6), at6, f"edge alpha is not monotonic: {at6}")

    def test_a_fully_hard_tip_is_still_hard(self):
        """REWRITTEN BY BE18, because the old form asserted the defect.

        It required `profile[-2]` -- the sample at dx = 10 on a 10.5px radius,
        half a pixel inside the rim -- to be a full 255. That is a statement
        that the antialiasing band has ZERO width, which is exactly what made
        Hard Ink, Fine Liner, Marker, Calligraphy and the default brush state
        indistinguishable from Pixel Perfect on their silhouettes: edge RMS
        0.29-0.42px against Pixel Perfect's 0.301px.

        The property worth keeping is that a hard tip is hard WHERE IT SHOULD
        BE HARD, which is the interior. The rim is now one pixel wide by
        construction (`MIN_AA_PX`), and BE18 owns the guard that it exists at
        all; this one owns that the floor did not soften the whole tip.
        """

        profile = M["hardnessSweep"]["h1"]["edgeProfile"]
        self.assertEqual(255, profile[0], "the core of a hard tip is not solid")
        # dx = 0, 2, 4, 6 on a 10.5px radius: comfortably inside the rim.
        for i, value in enumerate(profile[:4]):
            with self.subTest(dx=i * 2):
                self.assertEqual(
                    255, value,
                    f"a hard tip is not solid at dx={i * 2} of a 10.5px "
                    f"radius: {profile}")

    def test_a_fully_hard_tip_still_has_an_edge(self):
        """The other direction, so the rewrite above cannot be satisfied by a
        tip that is solid all the way out and then stops."""

        profile = M["hardnessSweep"]["h1"]["edgeProfile"]
        self.assertEqual(
            0, profile[-1],
            f"coverage did not reach zero by the last sample: {profile}")


@needs_node
class FlowAndBuildupMeanOneThingEachTests(unittest.TestCase):
    """REWRITTEN BY BE17, and the rewrite is the package.

    This class used to say "Flow is what one DAB contributes" and pinned the
    consequence: a non-buildup stroke settles at Flow and never moves again,
    however long it is worked. That was true, and it was the defect. It made
    Flow and Opacity the same control -- measured, (Flow 35, Opacity 100) and
    (Flow 100, Opacity 35) differed by ZERO pixels -- and it meant going back
    over your own mark inside one stroke added nothing at all.

    The contract now: Flow is what one PASS deposits, independent of Spacing
    and Size; Opacity is what one STROKE may reach; Buildup is whether that
    bound applies within the stroke.
    """

    def test_one_pass_deposits_exactly_its_flow(self):
        """THE DEFINITION, measured on a pass laid out in space.

        Not on the scrubbing fixture the rest of this class uses: ten to two
        hundred dabs on one spot is several passes, and cannot measure what one
        pass deposits. That distinction is the whole of BE17.
        """

        for key, flow in (("onePass_flow25", 0.25),
                          ("onePass_flow40", 0.40),
                          ("onePass_flow85", 0.85)):
            with self.subTest(case=key):
                self.assertAlmostEqual(
                    flow * 255, M["flowSemantics"][key], delta=2,
                    msg=f"one pass at Flow {flow} deposited "
                        f"{M['flowSemantics'][key]}, not {flow * 255:.0f}")

    def test_working_an_area_darkens_it(self):
        """The owner's complaint, inverted into a requirement. Ten dabs over
        one spot must be darker than one pass, and two hundred darker still."""

        ten = M["flowSemantics"]["flow40_noBuildup_10"]
        many = M["flowSemantics"]["flow40_noBuildup_200"]
        one_pass = M["flowSemantics"]["onePass_flow40"]
        self.assertGreater(
            ten, one_pass + 8,
            f"scrubbing ten dabs over one spot left it at {ten} against a "
            f"single pass's {one_pass}: the mark is not accumulating")
        self.assertGreater(many, ten, "two hundred dabs added nothing over ten")

    def test_opacity_bounds_a_non_buildup_stroke_however_long_it_is(self):
        """Stability AND value, which is the shape the old guard had and the
        reason it is worth keeping: a mutation proved that "10 equals 200"
        passes on an engine that saturates everything, so the settled value has
        to be checked against the number it is supposed to be.

        That number is now OPACITY rather than Flow. Measured at Opacity 50: a
        worked stroke settles at 128 and stops.
        """

        settled = M["flowSemantics"]["op50_noBuildup_200"]
        self.assertAlmostEqual(
            128, settled, delta=2,
            msg=f"a stroke at Opacity 50 settled at {settled}, not at its "
                "opacity bound")
        self.assertLess(
            M["flowSemantics"]["op50_noBuildup_10"], settled,
            "ten dabs already reached the bound, so this fixture cannot tell "
            "a bound from a saturation")

    def test_buildup_is_measurably_distinct(self):
        """MEASURED WHERE THE DISTINCTION EXISTS, which is not where the old
        version looked.

        Buildup means the Opacity bound does not apply within the stroke. At
        Opacity 100 there is no bound to ignore, so the two settings are the
        same thing and measure the same -- 185 against 185. The old guard ran
        at the preset's Opacity 100 and would now be asserting that a control
        does something in a case where it correctly does nothing.

        At Opacity 50 the difference is the whole point: 128 against 255.
        """

        bounded = M["flowSemantics"]["op50_noBuildup_200"]
        past = M["flowSemantics"]["op50_buildup_200"]
        self.assertGreater(
            past, bounded + 64,
            f"Buildup reached {past} against {bounded} without it; the switch "
            "means nothing")

    def test_buildup_accumulates_past_flow(self):
        self.assertGreater(M["flowSemantics"]["flow40_buildup_200"], 240)


@needs_node
class TheSoftEdgeSurvivesTests(unittest.TestCase):
    """The cross-section perpendicular to the stroke -- what an owner sees.

    A ceiling filled the dab's core to a flat plateau; max-blending a dab whose
    falloff reaches full strength leaves the falloff intact, scaled by flow.
    """

    def test_a_soft_stroke_has_a_gradient_not_a_plateau(self):
        section = M["crossSection"]["soft_flow40_noBuildup"]
        self.assertGreaterEqual(
            section["distinctLevels"], 12,
            f"the stroke edge has only {section['distinctLevels']} distinct "
            f"levels: {section['column']}")

    def test_a_soft_stroke_still_respects_flow(self):
        self.assertAlmostEqual(
            102, M["crossSection"]["soft_flow40_noBuildup"]["peak"], delta=2)


class TheSpecialCasesAreGoneTests(unittest.TestCase):
    """Structural, so neither cliff can be reintroduced quietly."""

    def test_the_falloff_has_no_hardness_branch(self):
        code = _code_only()
        start = code.index("function dabAlpha(")
        body = code[start:code.index("\n}", start)]
        self.assertNotIn("0.4", body)
        self.assertNotIn("0.43", body)
        self.assertNotIn("hardness >= 0.01", body)

    def test_the_spacing_rule_has_no_hardness_branch(self):
        code = _code_only()
        start = code.index("function spacingFraction(")
        body = code[start:code.index("\n}", start)]
        self.assertNotIn("0.01", body)
        self.assertIn("baseSpacing * (0.3 + 0.7 * hardness)", body)

    def test_the_flow_ceiling_is_gone(self):
        code = _code_only()
        self.assertNotIn("ceiling", code)

    def test_buildup_is_the_only_accumulation_switch(self):
        code = _code_only()
        self.assertIn("const useSoftBlend = S.brushBuildup;", code)
        self.assertNotIn("softTip || S.brushBuildup", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE5_TESTS, found)


if __name__ == "__main__":
    unittest.main()
