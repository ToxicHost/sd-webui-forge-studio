"""BE9: the stabiliser follows the path, and the stroke reaches the pointer.

THE DEFECT BE3 FOUND AND COULD NOT FIX.

    const w = Math.max(1, Math.min(S.smoothing, pts.length));
    ...mean of the last w SAMPLES...

A window counted in samples covers a quarter of the arc at 240 Hz that it
covers at 60. So the filter's real strength -- how much of the path it averages
-- depended on how often the browser reported the pointer.

BE3 could not fix it, and the reason matters: its tests call `plotTo` directly
and bypass the stabiliser entirely, so the defect was structurally invisible to
them. It only appeared through the real pointer pipeline, where it was LARGER
than the placement defect BE3 did fix:

    smoothing 0   identical at 1x / 3x / 8x / 20x
    smoothing 3   7,173 / 10,825 / 13,252 / 14,096 painted pixels

At one event per segment a stroke covered half the pixels twenty events did.

THE WINDOW IS NOW AN ARC LENGTH, in SCREEN pixels, and the average is weighted
by the path length each sample represents. That makes the output a property of
the polyline -- the same answer however finely it is sampled -- and makes the
control feel the same at any zoom.

THE ENDPOINT. A lagging filter ends the mark behind the lifted pointer, by up
to the window: 14 document pixels at Smoothing 6, measured. `finishStroke`
walks the remainder. It is NOT an unconditional extra dab -- it calls `plotTo`,
so BE3's spacing debt still decides whether a dab is due at all.
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
UI = APP_ROOT / "forge_studio" / "frontend" / "canvas-ui.js"

NODE = shutil.which("node")

EXPECTED_BE9_TESTS = 20

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


def _code_only(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class TheStabiliserIsRateInvariantTests(unittest.TestCase):
    """The same polyline, sampled 1x through 50x, must stabilise identically."""

    def test_every_smoothing_level_is_invariant(self):
        for level, row in M["stabiliserRateInvariance"].items():
            with self.subTest(level=level):
                self.assertLess(
                    row["spreadPx"], 0.5,
                    f"{level} stabilised to positions {row['spreadPx']}px apart "
                    f"across sampling densities: {row['at']}")

    def test_the_sampling_densities_actually_differ(self):
        """Guards the guard -- identical densities make invariance vacuous."""

        at = M["stabiliserRateInvariance"]["smoothing3"]["at"]
        self.assertGreaterEqual(len(at), 4)

    def test_smoothing_zero_is_exactly_the_input(self):
        row = M["stabiliserRateInvariance"]["smoothing0"]
        self.assertEqual(0, row["spreadPx"])


@needs_node
class TheStabiliserIsZoomStableTests(unittest.TestCase):
    """The window is in SCREEN pixels, so the control feels the same at any
    magnification.

    A document-space window would smooth four times as hard at 4x zoom, because
    the same hand movement covers a quarter of the document distance -- a
    change in behaviour the owner did not ask for by zooming in.
    """

    def test_the_lag_is_the_same_at_every_zoom(self):
        self.assertLess(
            M["stabiliserZoomStability"]["spread"], 0.5,
            f"{M['stabiliserZoomStability']['screenLagPx']}")

    def test_the_zooms_actually_differ(self):
        zooms = M["stabiliserZoomStability"]["screenLagPx"]
        self.assertIn("zoom0.25", zooms)
        self.assertIn("zoom4", zooms)


@needs_node
class PositionAndPressureAreSeparateTests(unittest.TestCase):
    """The brief requires them to be separate concerns, and they are.

    Position jitter is a tremor an owner wants removed; pressure lag is felt
    immediately as a brush that will not respond. One window trades one for the
    other.
    """

    def test_pressure_catches_up_before_position_does(self):
        row = M["pressureWindowIsShorter"]
        self.assertGreater(
            row["pressure"], 0.9,
            f"pressure has not caught up past its own window: {row}")
        self.assertGreater(
            row["positionLagPx"], 2,
            "position is not lagging, so the measurement cannot distinguish "
            f"the two windows: {row}")

    def test_the_two_windows_are_declared_separately(self):
        code = _code_only(CORE)
        self.assertIn("const STAB_PRESSURE_FRACTION", code)
        self.assertIn("windowDoc * STAB_PRESSURE_FRACTION", code)


class TheWindowIsAnArcLengthTests(unittest.TestCase):
    """Structural, because a sample-count window is the natural thing to write
    and is what was there before."""

    def test_the_window_is_measured_in_screen_pixels(self):
        code = _code_only(CORE)
        self.assertIn("const STAB_SCREEN_PX_PER_STEP", code)
        self.assertIn("(level * STAB_SCREEN_PX_PER_STEP) / scale", code)

    def test_the_average_is_arc_weighted(self):
        code = _code_only(CORE)
        self.assertIn("function _arcCentroid(", code)
        self.assertIn("sum += mid * take;", code)

    def test_the_stabiliser_does_not_use_a_sample_count_window(self):
        """Scoped to `stab`, not to the whole file.

        `_sampleMean` still exists and still contains
        `Math.min(S.smoothing, pts.length)` -- deliberately, as the mutation
        harness's target. A mutation that has to invent its own replacement is
        testing the mutation rather than the code, so the original expression is
        kept where the harness can reach it. Nothing in the engine calls it, and
        this guard is what says so.
        """

        code = _code_only(CORE)
        body = code[code.index("function stab("):]
        body = body[:body.index("\n}")]
        self.assertNotIn("Math.min(S.smoothing, pts.length)", body)
        self.assertNotIn("_sampleMean", body)
        self.assertIn("_arcCentroid", body)

    def test_the_legacy_mean_has_no_caller(self):
        """It is a mutation target, not a code path. If something starts
        calling it, that is the defect coming back."""

        code = _code_only(CORE)
        callers = code.count("_sampleMean(")
        self.assertEqual(
            1, callers,
            f"_sampleMean is referenced {callers} times; it should appear only "
            "at its own definition")

    def test_smoothing_zero_exits_before_arithmetic(self):
        """The inherited NaN defect is removed by construction rather than
        guarded against: at level 0 there is nothing to divide."""

        code = _code_only(CORE)
        body = code[code.index("function stab("):]
        body = body[:body.index("\n}")]
        self.assertIn("if (level <= 0 || pts.length < 2) return", body)


class TheStrokeReachesThePointerTests(unittest.TestCase):
    """The endpoint flush, and the constraint it must not violate."""

    def test_finish_stroke_exists_and_is_exported(self):
        code = _code_only(CORE)
        self.assertIn("function finishStroke(x, y, p) {", code)
        self.assertIn("commitStroke, finishStroke,", code)

    def test_it_runs_before_commit_on_pointer_up(self):
        """Order AND the exact guarded form.

        A mutation proved order alone is not enough: `if (false)
        C.finishStroke(...)` satisfies "the call comes before the commit" while
        disabling it completely. The condition is pinned too.
        """

        code = _code_only(UI)
        self.assertIn("if (fp) C.finishStroke(fp.x, fp.y, fp.pressure);", code)
        flush = code.index("C.finishStroke(fp.x, fp.y, fp.pressure)")
        commit = code.index("C.commitStroke();", flush)
        self.assertLess(
            flush, commit,
            "the stroke is committed before it is finished, so the catch-up "
            "never reaches the layer")

    def test_it_goes_through_plot_to_rather_than_stamping_directly(self):
        """This is what keeps it from being an unconditional extra dab. `plotTo`
        consults BE3's spacing debt, so a stroke that already ended on the
        pointer emits nothing."""

        code = _code_only(CORE)
        body = code[code.index("function finishStroke("):]
        body = body[:body.index("\n}")]
        self.assertIn("plotTo(x, y,", body)
        self.assertNotIn("stampWet(", body)

    def test_a_zero_length_catch_up_does_nothing(self):
        """BE13 CHANGED THE RETURN, and the change is right.

        This read `return false` until BE13 gave the stroke a pixel-perfect
        cell that can still be pending when the pointer has not moved. The
        function's answer means "did this call paint anything", so flushing
        that cell IS something and `return flushed` is the truthful answer.
        With Pixel Perfect off -- which is every other preset -- `flushed` is
        false and the behaviour is byte-identical to what it was.

        The BEHAVIOURAL half of this guard lives in
        `TheCatchUpActuallyCatchesUpTests`, which executes the engine and is
        unaffected either way.
        """

        code = _code_only(CORE)
        body = code[code.index("function finishStroke("):]
        body = body[:body.index("\n}")]
        # BE16 ADDED A SECOND THING TO FLUSH, for the same reason. It defers
        # the OPENING dab until the stroke has a direction, so a stroke that
        # never moves -- a tap -- has one owed at the end. `flushed` is now the
        # OR of the two, and it means what it always meant: did this call paint
        # anything. Both flushes are asserted rather than the exact expression,
        # so a third one does not have to rewrite this line again.
        self.assertIn("if (dx * dx + dy * dy < 1e-12) return flushed;", body)
        self.assertIn("flushPixelPerfect()", body)
        self.assertIn("flushOpeningDab(null)", body)


@needs_node
class TheCatchUpActuallyCatchesUpTests(unittest.TestCase):
    """Measured, not inferred from source order.

    A guard that only checks the call site is satisfied by a disabled call, and
    a mutation proved it. What the catch-up DOES has to be measured.
    """

    def test_it_closes_the_shortfall(self):
        row = M["endpointCatchUp"]["withFlush"]
        self.assertGreater(
            row["shortfallBefore"], 20,
            f"the fixture did not leave a shortfall to close: {row}")
        self.assertLessEqual(
            row["shortfallAfter"], 0,
            f"the mark still ends {row['shortfallAfter']}px short of where the "
            f"pointer stopped: {row}")

    def test_without_it_the_shortfall_remains(self):
        """So the test above cannot pass because there was nothing to fix."""

        row = M["endpointCatchUp"]["without"]
        self.assertEqual(row["shortfallBefore"], row["shortfallAfter"])

    def test_a_catch_up_with_nothing_to_catch_does_nothing(self):
        """The brief forbids an unconditional extra dab. A stroke already at
        the pointer must emit no pixels and say so."""

        noop = M["endpointCatchUp"]["noop"]
        self.assertFalse(noop["returned"])
        self.assertTrue(noop["unchanged"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE9_TESTS, found)


if __name__ == "__main__":
    unittest.main()
