"""BE6: one tip-local frame, and a capability matrix that is checked.

FOUR TIP FAMILIES, FOUR DIFFERENT OPINIONS ABOUT WHICH CONTROLS EXIST.

    shapeDistRound   took NO angle. Applied ratio, THEN folded for spikes,
                     THEN took an isotropic norm.
    shapeDistFlat    took an angle. Hardcoded `ry = r * 0.3`.
    shapeDistMarker  took an angle. Hardcoded `0.8 / 0.35`.
    scatter          called none of them -- a plain circle, inline, with no
                     density test either.

Measured before: Ratio reached one tip of four, Spikes reached none, Angle
reached two, Density reached three.

THE ORDER OF OPERATIONS IS THE WHOLE FIX FOR SPIKES. Rotation preserves an
isotropic norm, so folding the angle and then measuring `sqrt(dx^2 + dy^2)`
cannot change a pixel -- Spikes was not unwired, it was algebraically incapable.
Folding first and applying anisotropy after leaves the fold on a shape that
rotation does not preserve.

WHAT CANNOT BE FIXED, and is asserted rather than promised: a circle has no
orientation. Angle and Spikes do nothing to a round tip at Ratio 1.0 and no
implementation can change that. They are declared `needs-shape` and measured at
Ratio 0.4, which is the difference between "mathematically inert here" and "not
connected" -- the two states BE6 exists because nobody could tell apart.
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

DRIVER = Path(__file__).with_name("be1_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE6_TESTS = 21

TIPS = ("round", "flat", "marker", "scatter")

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
class TheDeclaredMatrixMatchesRenderedPixelsTests(unittest.TestCase):
    """A capability matrix nobody checks is a comment.

    `TIP_CAPABILITIES` is a PROMISE the engine makes. These compare it against
    what the engine actually paints, control by control and tip by tip.
    """

    def test_the_engine_declares_a_matrix(self):
        self.assertIsNotNone(
            M["declaredCapabilities"],
            "TIP_CAPABILITIES is not exported; the contract cannot be checked")
        for tip in TIPS:
            self.assertIn(tip, M["declaredCapabilities"])

    def test_every_true_promise_is_kept(self):
        """Declared `true` means it changes pixels on a neutral tip."""

        for tip in TIPS:
            for control, promise in M["declaredCapabilities"][tip].items():
                if promise is not True:
                    continue
                key = "brush" + control[0].upper() + control[1:]
                with self.subTest(tip=tip, control=control):
                    self.assertEqual(
                        "works", M["supportMatrix"][tip][key],
                        f"{tip} declares {control} supported and it changes "
                        "no pixels")

    def test_every_needs_shape_promise_is_kept(self):
        """Declared `needs-shape` means two things, and BOTH are asserted: it
        does nothing on a circle, and it works once Ratio gives the tip an
        axis. Only checking the second would let a genuinely dead control hide
        behind the label."""

        for tip in TIPS:
            for control, promise in M["declaredCapabilities"][tip].items():
                if promise != "needs-shape":
                    continue
                key = "brush" + control[0].upper() + control[1:]
                with self.subTest(tip=tip, control=control):
                    self.assertEqual(
                        "dead", M["supportMatrix"][tip][key],
                        f"{tip} declares {control} needs-shape but it acts on "
                        "a circle -- the declaration is wrong, not the code")
                    self.assertEqual(
                        "works", M["needsShapeMatrix"][tip][key],
                        f"{tip} declares {control} needs-shape and it does "
                        "nothing even at Ratio 0.4 -- it is simply dead")

    def test_no_control_is_left_undeclared(self):
        """Every control the matrix measures must appear in every tip's row."""

        measured = {k.replace("brush", "").lower()
                    for k in M["supportMatrix"]["round"]}
        for tip in TIPS:
            declared = {k.lower() for k in M["declaredCapabilities"][tip]}
            with self.subTest(tip=tip):
                self.assertEqual(
                    measured, declared,
                    f"{tip} declares {sorted(declared)} but the engine is "
                    f"measured on {sorted(measured)}")


@needs_node
class EveryControlReachesEveryTipTests(unittest.TestCase):
    """The headline result, stated per control rather than per tip."""

    def test_ratio_reaches_all_four(self):
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushRatio"])

    def test_density_reaches_all_four(self):
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushDensity"])

    def test_falloff_reaches_all_four(self):
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushFalloff"])

    def test_angle_reaches_all_four_once_the_tip_has_an_axis(self):
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertEqual("works", M["needsShapeMatrix"][tip]["brushAngle"])

    def test_spikes_reaches_all_four_once_the_tip_has_an_axis(self):
        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertEqual("works", M["needsShapeMatrix"][tip]["brushSpikes"])


@needs_node
class SpikesTests(unittest.TestCase):
    """The fold, and the one case that must stay neutral."""

    def test_spikes_is_neutral_on_a_perfect_circle(self):
        """Correct and permanent. Rotating a rotationally symmetric shape
        gives the same shape; no ordering of operations changes that."""

        self.assertTrue(M["spikes"]["neutralOnCircle"])

    def test_spikes_bites_on_an_ellipse(self):
        """The fix. Was false before BE6 even at Ratio 0.4, because the norm
        was taken last and was isotropic."""

        self.assertTrue(M["spikes"]["bitesOnEllipse"])


@needs_node
class PresetsStayDistinctTests(unittest.TestCase):
    def test_no_two_presets_stamp_identical_pixels(self):
        self.assertEqual([], M["distinctness"]["collisions"])

    def test_flat_and_marker_remain_different_shapes(self):
        """They share a frame now, so the risk is that they collapse into one.
        Their ASPECT, EXTENT and NORM still differ."""

        # BE14 renamed both -- Flat Shader -> Flat Chisel, Bold Marker ->
        # Marker -- and the rename is the reason the alias table exists.
        fp = M["distinctness"]["fingerprints"]
        self.assertNotEqual(fp["Flat Chisel"]["digest"], fp["Marker"]["digest"])


@needs_node
class EachTipKeepsItsOwnNormTests(unittest.TestCase):
    """The norm, isolated from size, aspect and extent.

    Comparing the Flat Shader and Bold Marker PRESETS cannot do this: they also
    differ in size, so they would look different even if both were ellipses. A
    mutation proved it -- deleting the Chebyshev norm entirely left that
    comparison passing.

    FILL RATIO is norm-specific by construction. A Chebyshev norm fills its
    bounding box; a Euclidean one inscribes an ellipse in it.

        euclidean   ~ pi/4 = 0.785
        chebyshev   ~ 1.0

    A corner probe was tried first and was worse: sampling at 0.7r on both axes
    falls outside Marker's 0.35 aspect entirely, so it reported every tip
    identical.
    """

    def test_marker_is_a_rectangle(self):
        self.assertGreater(
            M["tipShapes"]["marker"]["fillRatio"], 0.95,
            "Marker is no longer filling its bounding box -- it has lost its "
            "Chebyshev norm and collapsed into an ellipse like Flat")

    def test_round_and_flat_are_ellipses(self):
        for tip in ("round", "flat"):
            with self.subTest(tip=tip):
                self.assertAlmostEqual(
                    0.785, M["tipShapes"][tip]["fillRatio"], delta=0.05)

    def test_every_tip_painted_enough_to_measure(self):
        """So a fill ratio cannot be satisfied by a handful of pixels."""

        for tip in TIPS:
            with self.subTest(tip=tip):
                self.assertGreater(M["tipShapes"][tip]["paintedPixels"], 500)


class TheGeometryIsFactoredOnceTests(unittest.TestCase):
    """Structural. Three shape functions became one frame; they must not grow
    back, and the tip's identity must stay declarative."""

    def test_the_three_shape_functions_are_gone(self):
        code = _code_only()
        for name in ("shapeDistRound", "shapeDistFlat", "shapeDistMarker"):
            with self.subTest(function=name):
                self.assertNotIn(f"function {name}", code)

    def test_the_tip_tables_are_declarative(self):
        code = _code_only()
        for table in ("TIP_ASPECT", "TIP_NORM", "TIP_EXTENT", "TIP_CAPABILITIES"):
            with self.subTest(table=table):
                self.assertIn(f"const {table} = ", code)

    def test_the_fold_happens_before_the_anisotropy(self):
        """The entire reason Spikes works. Asserted by ORDER in the source,
        because a test on pixels alone would pass on an implementation that
        got the right answer for the wrong reason.

        Reads `tipFrame`, not `tipDistance`. BE8 hoisted everything invariant
        across a dab out of the per-pixel path, so `tipDistance` is now a
        one-line delegate and the order lives in the frame's `at()`. The
        anisotropy is applied as a precomputed reciprocal -- `invRx` -- rather
        than a division, so that is what the fold has to precede.
        """

        code = _code_only()
        body = code[code.index("function tipFrame("):]
        body = body[:body.index("\n}\n")]
        self.assertLess(
            body.index("_applySpikeRotation"), body.index("* invRx"),
            "anisotropy is being applied before the spike fold again, which "
            "makes the fold invisible to an isotropic norm")

    def test_scatter_uses_the_shared_coverage_loop(self):
        code = _code_only()
        stamp = code[code.index("function stampAlphaMap("):]
        self.assertIn("const cover = (ccx, ccy, cr) =>", stamp)
        self.assertNotIn("const dist = Math.sqrt((px - sx)", stamp)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE6_TESTS, found)


if __name__ == "__main__":
    unittest.main()
