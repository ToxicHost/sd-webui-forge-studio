"""BE7: per-dab direction, anisotropic spacing, and Density that means one thing.

THREE DEFECTS IN ONE LINE.

    if (Math.hypot(dx, dy) > 2) { ...update the heading... }

A segment of two pixels or less did not update the direction AT ALL, so a
slowly drawn curve -- the case where a chisel's angle matters most -- kept
whatever heading it had before. The update happened once per EVENT while
`plotTo` may emit many dabs along a segment, so every dab in a fast stroke
shared one angle. And the smoothing coefficient was a fixed 0.3 per event,
which makes the filter's strength depend on how often the browser reports the
pointer -- the same event-rate defect BE3 removed from spacing, living one line
above it.

THE SMOOTHED-HEADING REPAIR WAS WRITTEN, MEASURED, AND REJECTED.

Making the coefficient per-DISTANCE is the obvious fix and it does not work,
for a reason worth keeping. BE7 also makes the dab gap depend on the tip's
extent along the direction of travel -- so the gap depends on the heading, and
the heading advances once per gap. That is a feedback loop, and it left the
trajectory sampling-dependent after all: the same quarter turn ended at -59
degrees when reported 20 times and -22.7 degrees when reported 240 times,
against a true final tangent of -90.

A first-order filter also lags a turn by roughly `tau x turn-rate`, which for a
one-dab-width tau is about 29 degrees on the reference arc. A chisel held 29
degrees off its own direction of travel through every curve is precisely the
complaint that opened this program.

So the heading is the path's TANGENT. That is a property of the path rather
than of the event stream: exactly rate-invariant, no lag, no feedback. Input
noise is a separate concern and belongs to BE9, which smooths POSITION -- and a
smoothed path yields a smooth tangent for free.
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

EXPECTED_BE7_TESTS = 18

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
class TheStaleAngleGateIsGoneTests(unittest.TestCase):
    """A stroke of sub-2px steps must still turn the tip."""

    def test_the_heading_follows_a_curve_walked_in_tiny_steps(self):
        """A quarter turn in ~1.5px steps -- every one below the old gate. The
        heading must arrive near the final tangent of -90 degrees; under the
        gate it would not have moved at all."""

        heading = M["headingTracksSmallSteps"]["finalHeadingDeg"]
        self.assertAlmostEqual(
            -90, heading, delta=5,
            msg=f"after a quarter turn in 1.5px steps the heading is {heading} "
                "degrees; the stale-angle gate is back")

    def test_the_gate_is_absent_from_source(self):
        code = _code_only()
        self.assertNotIn("if (Math.hypot(dx, dy) > 2)", code)


@needs_node
class TheHeadingIsRateInvariantTests(unittest.TestCase):
    """Same arc, same speed, different report rates: same final heading."""

    def test_the_spread_across_event_rates_is_negligible(self):
        spread = M["headingRateInvariance"]["spreadDeg"]
        self.assertLess(
            spread, 4.0,
            f"the heading ended {spread} degrees apart across event rates: "
            f"{M['headingRateInvariance']['at']}")

    def test_every_rate_reaches_the_true_tangent(self):
        for rate, deg in M["headingRateInvariance"]["at"].items():
            with self.subTest(rate=rate):
                self.assertAlmostEqual(-90, deg, delta=5)

    def test_the_rates_actually_differ(self):
        """Guards the guard: identical rates would make invariance vacuous."""

        self.assertGreaterEqual(len(M["headingRateInvariance"]["at"]), 4)


class TheHeadingIsTheTangentTests(unittest.TestCase):
    """Structural, because the rejected design is the one someone will
    reintroduce -- a smoothed heading looks more sophisticated."""

    def test_there_is_no_per_event_smoothing_coefficient(self):
        code = _code_only()
        self.assertNotIn("* 0.3;", code)

    def test_the_heading_is_assigned_not_filtered(self):
        code = _code_only()
        body = code[code.index("function _advanceHeading("):]
        body = body[:body.index("\n}")]
        self.assertIn("_saSmooth = target;", body)
        self.assertNotIn("Math.exp", body)

    def test_the_three_rotation_terms_stay_separate(self):
        """Heading, base Angle and jitter are summed at the call site and never
        folded into one another -- the brief requires jitter to stay out of the
        direction term."""

        code = _code_only()
        self.assertIn(
            "let stampRot = (dyn.followStroke ? _saSmooth : 0) + _brushAngleRad();",
            code)


@needs_node
class SpacingFollowsTheTipsExtentTests(unittest.TestCase):
    """The gap tracks the tip's extent along the direction of travel.

    MEASURED DIRECTLY, not inferred from continuity. Continuity is the wrong
    instrument: it conflates the gap with the tip's footprint along travel, and
    both change together -- a chisel can stay solid with the feature disabled
    and still gap with it enabled. A mutation proved that, staying green while
    the whole anisotropic branch was switched off.

    Asking `spacingFor` what gap it returns for a given travel direction is
    unambiguous, and the expected answer is arithmetic: for a flat tip of
    aspect 0.3, travelling along the long axis should cost about 1/0.3 times
    the gap of travelling across it.
    """

    def test_a_flat_tip_spaces_itself_by_its_extent_along_travel(self):
        gap = M["anisotropicGap"]
        self.assertAlmostEqual(
            1 / 0.3, gap["ratio"], delta=0.2,
            msg=f"a flat tip's gap along its long axis is {gap['flatAlong']} "
                f"and across it {gap['flatAcross']} -- ratio {gap['ratio']}, "
                "expected about 3.33")

    def test_a_round_tip_is_isotropic(self):
        """The calculation must be a no-op on a circle, not merely harmless:
        a round tip has the same extent in every direction."""

        self.assertTrue(M["anisotropicGap"]["roundIsIsotropic"])

    def test_the_scalar_gap_is_the_long_axis(self):
        """With no travel direction -- `beginStroke` seeding the first debt --
        the gap falls back to the tip's nominal width rather than to something
        arbitrary."""

        gap = M["anisotropicGap"]
        self.assertAlmostEqual(gap["flatAlong"], gap["flatScalar"], delta=0.01)

    def test_a_chisel_is_continuous_at_shipped_settings(self):
        """The owner-facing guarantee, at the spacing Flat Shader ships with.

        Deliberately NOT asserted at wide spacing: measured there, the
        anisotropic gap improves oblique travel (3px of gap down to 1px at 45
        and 135 degrees) but does not close it at 60 and 90. Claiming
        continuity at every spacing would be claiming more than was built.
        """

        for key, row in M["chiselAtAngles"].items():
            with self.subTest(angle=key):
                self.assertEqual(
                    0, row["longestGapPx"],
                    f"a flat tip travelling at {row['deg']} degrees left a "
                    f"{row['longestGapPx']}px gap along its own line")

    def test_the_chisel_actually_painted(self):
        for key, row in M["chiselAtAngles"].items():
            with self.subTest(angle=key):
                self.assertGreater(row["paintedOnLine"], 100)

    def test_spacing_reads_the_tip_extent(self):
        code = _code_only()
        self.assertIn("travelAngle", code)
        self.assertIn("TIP_EXTENT[kind]", code)


@needs_node
class DensityIsIndependentOfSpacingTests(unittest.TestCase):
    """Density and Spacing were multiplying.

    The skip is per pixel per dab and dabs overlap, so what an owner sees is
    the UNION over every dab touching a pixel. Measured at Density 0.35 before
    the fix, coverage ran 0.698 at spacing 0.32 up to 1.000 at 0.02 -- at close
    spacing the control did nothing whatsoever.
    """

    def test_coverage_does_not_climb_as_spacing_tightens(self):
        spread = M["densityAgainstSpacing"]["spread"]
        self.assertLess(
            spread, 0.12,
            f"coverage varies by {spread} across spacings: "
            f"{M['densityAgainstSpacing']['rows']}")

    def test_coverage_is_near_the_requested_density(self):
        """Approximately, and deliberately not tightly.

        The normalisation estimates overlap as `1 / spacingFraction`, which
        treats a dab as a uniform disc. It is not one -- the falloff means edge
        pixels are covered by fewer effective hits -- so the result lands near
        the request rather than on it. A tighter bound here would be asserting
        a precision the model does not have.
        """

        for key, coverage in M["densityAgainstSpacing"]["rows"].items():
            with self.subTest(spacing=key):
                self.assertAlmostEqual(0.35, coverage, delta=0.15)

    def test_density_one_still_fills(self):
        """The normalisation must be a no-op at full density, or every brush
        gets quietly stippled."""

        self.assertGreater(M["rates"]["1x"]["pixels"], 1000)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE7_TESTS, found)


if __name__ == "__main__":
    unittest.main()
