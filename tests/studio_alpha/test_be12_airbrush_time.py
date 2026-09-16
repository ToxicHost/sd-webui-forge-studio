"""BE12: the Airbrush preset earns its name.

WHAT IT WAS. `DEFAULT_BRUSH_PRESETS` has shipped "Airbrush" since before this
programme started -- size 30, hardness 0, opacity 50, flow 15, buildup true.
Every one of those is real. None of them is an airbrush. An airbrush deposits
paint WHILE IT IS HELD OVER A SPOT, and Studio's deposited only when the
pointer moved, because `plotTo` is the only thing that stamps and only a
pointermove calls it. Holding perfectly still did nothing at all.

THE RATE MUST NOT DEPEND ON THE CALLBACK, and that is the third time this
programme has written that sentence. BE3 removed it from spacing, BE9 removed
it from smoothing, and here it is again wearing a timer: `setInterval` is a
request, not a promise, so a stroke held for one second would deposit however
many callbacks the browser felt like delivering.

Time carries a DEBT, exactly as distance does in `plotTo`. Measured over 400ms
delivered in 1, 4, 20 and 100 callbacks: the same dabs and the same paint,
spread 0.

A STALE CALLBACK CANNOT WRITE ANYTHING, and clearing the interval is necessary
rather than sufficient -- a callback can already be queued when `clearInterval`
runs. Every tick re-checks a token captured at stroke start, so a callback that
survives commit, abort, a tool switch or a document switch deposits nothing
even when it fires.

Source review: Evidence/source-review/BE12-airbrush-time.md
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

DRIVER = Path(__file__).with_name("be12_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE12_TESTS = 31

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
class HoldingStillDepositsTests(unittest.TestCase):
    """The whole claim, in one measurement."""

    def test_half_a_second_of_stillness_lays_down_paint(self):
        row = M["holdingStillDeposits"]
        self.assertTrue(row["depositsMore"], f"{row}")
        self.assertGreater(row["dabs"], 0, f"{row}")

    def test_an_ordinary_brush_deposits_nothing(self):
        """The second half of the acceptance, and the half that makes the
        first mean something: if every preset deposited on a timer this would
        be a global behaviour change wearing an airbrush's name."""

        row = M["anOrdinaryBrushDoesNot"]
        self.assertEqual(0, row["basicRoundDabs"], f"{row}")
        self.assertTrue(row["basicRoundUnchanged"], f"{row}")

    def test_the_airbrush_and_the_round_brush_are_being_compared_fairly(self):
        """Guards the guard: both were held for the same 500ms in the same 30
        callbacks, so the difference is the preset and nothing else."""

        self.assertGreater(M["anOrdinaryBrushDoesNot"]["airbrushDabs"], 0)


@needs_node
class TheRateSurvivesTheSchedulerTests(unittest.TestCase):
    """THE PROBE THIS PACKAGE EXISTS FOR.

    Same elapsed time, wildly different callback counts. A timer that stamps
    once per callback cannot pass this, and no amount of test-writing would
    rescue one that did.
    """

    def test_the_same_time_lays_the_same_dabs(self):
        row = M["rateIsIndependentOfTheCallback"]
        self.assertEqual(0, row["dabSpread"], f"{row['at']}")

    def test_the_same_time_lays_the_same_paint(self):
        row = M["rateIsIndependentOfTheCallback"]
        self.assertEqual(0, row["totalSpread"], f"{row['at']}")

    def test_the_callback_counts_actually_differ(self):
        at = M["rateIsIndependentOfTheCallback"]["at"]
        self.assertIn("cb1", at)
        self.assertIn("cb100", at)

    def test_deposition_follows_elapsed_time(self):
        """Guards the guard. A timer that deposited NOTHING would have a spread
        of zero above and would satisfy every invariance test ever written."""

        row = M["depositionFollowsElapsedTime"]
        self.assertTrue(row["doubles"], f"{row['at']}")

    def test_the_debt_is_carried_rather_than_reset(self):
        code = _code_only()
        body = code[code.index("function airbrushTick("):]
        body = body[:body.index("\n}")]
        self.assertIn("_airDebt += elapsed;", body)
        self.assertIn("_airDebt -= interval;", body)
        self.assertNotIn("_airDebt = elapsed", body)


@needs_node
class FlowSetsTheRateTests(unittest.TestCase):
    """One control doing one thing. The control the owner already has for "how
    much paint" also governs how fast it arrives, rather than a second slider
    that interacts with the first."""

    def test_more_flow_deposits_faster(self):
        row = M["flowSetsTheRate"]
        self.assertTrue(row["faster"], f"{row['at']}")

    def test_the_interval_has_a_floor(self):
        """A Flow of 0.01 would otherwise ask for one dab every 100 seconds,
        which is a control that appears broken rather than slow."""

        code = _code_only()
        self.assertIn("const AIR_MIN_RATE", code)
        body = code[code.index("function airbrushInterval("):]
        body = body[:body.index("\n}")]
        self.assertIn("AIR_MIN_RATE", body)


@needs_node
class StationaryOnlyTests(unittest.TestCase):
    """A decision, not a limitation.

    A moving pointer already deposits through BE3's spacing debt. Adding
    time-driven dabs on top would make a slow drag darker for two independent
    reasons at once -- which is why Krita and Photoshop both need a rate
    control to manage the interaction. V1 takes the narrow honest version.
    """

    def test_a_moving_pointer_deposits_nothing_extra(self):
        row = M["movingDepositsNothingExtra"]
        self.assertTrue(row["none"], f"{row}")

    def test_the_debt_is_discarded_rather_than_banked_while_moving(self):
        """Banked, it would pay out a burst the moment the hand stopped."""

        code = _code_only()
        body = code[code.index("function airbrushTick("):]
        body = body[:body.index("\n}")]
        moved = body[body.index("AIR_STILL_PX"):]
        self.assertIn("_airDebt = 0;", moved[:200])


@needs_node
class AStaleCallbackWritesNothingTests(unittest.TestCase):
    """Clearing the interval is NECESSARY AND NOT SUFFICIENT. A callback can
    already be queued when `clearInterval` runs, and "we remembered to clear
    it" is a weaker promise than "a stale callback cannot do damage"."""

    def test_after_commit(self):
        self.assertEqual(0, M["staleCallbacksWriteNothing"]["at"]["afterCommit"])

    def test_after_abort(self):
        self.assertEqual(0, M["staleCallbacksWriteNothing"]["at"]["afterAbort"])

    def test_after_an_explicit_stop(self):
        """What a tool switch and a document switch both call."""

        self.assertEqual(0, M["staleCallbacksWriteNothing"]["at"]["afterStop"])

    def test_after_a_new_stroke_has_started(self):
        """The token must not be reusable. A callback from stroke N firing
        during stroke N+1 would deposit into the wrong mark."""

        self.assertEqual(0, M["staleCallbacksWriteNothing"]["at"]["afterANewStroke"])

    def test_the_token_is_what_makes_it_safe(self):
        code = _code_only()
        body = code[code.index("function airbrushTick("):]
        body = body[:body.index("\n}")]
        self.assertIn("if (token !== _airToken) return 0;", body)
        stop = code[code.index("function stopAirbrush("):]
        stop = stop[:stop.index("\n}")]
        self.assertIn("_airToken++;", stop)


class EveryExitStopsItTests(unittest.TestCase):
    """Five exits the brief names, four funnels. `abortStroke` covers two of
    them because CT2 already routes both `pointercancel` and window `blur`
    there -- which is why stopping the timer at that funnel cannot drift from
    either later."""

    def test_commit_stops_it_before_it_tears_anything_down(self):
        code = _code_only()
        body = code[code.index("function commitStroke("):]
        body = body[:body.index("\n}")]
        self.assertIn("stopAirbrush();", body)
        self.assertLess(
            body.index("stopAirbrush();"), body.index("clearStrokeUndo();"),
            "the stroke is torn down before the timer is stopped, so a tick "
            "can land in between")

    def test_abort_stops_it(self):
        code = _code_only()
        body = code[code.index("function abortStroke("):]
        body = body[:body.index("\n}")]
        self.assertIn("stopAirbrush();", body)

    def test_abort_is_where_cancel_and_blur_both_arrive(self):
        """The reason one line covers two exits. Asserted so a later change
        that gives them separate handlers cannot quietly lose one."""

        ui = _code_only("canvas-ui.js")
        cancel = ui.index('addEventListener("pointercancel"')
        self.assertIn("C.abortStroke();", ui[cancel:cancel + 700])
        blur = ui.index('addEventListener("blur"')
        self.assertIn("C.abortStroke();", ui[blur:blur + 500])

    def test_a_tool_switch_stops_it(self):
        ui = _code_only("canvas-ui.js")
        body = ui[ui.index("function setTool(t) {"):]
        body = body[:body.index("\n}")]
        # THE EXACT GUARDED FORM, not merely the call.
        #
        # A mutation proved the looser version worthless: `if (false)
        # C.stopAirbrush();` contains the call, satisfies "the call is
        # present", and disables it completely. BE9 was caught by the identical
        # trap on `finishStroke` and the lesson did not transfer -- so it is
        # written down here as well as there.
        self.assertIn("if (C.stopAirbrush) C.stopAirbrush();", body)

    def test_a_document_switch_stops_it(self):
        docs = _code_only("studio-docs.js")
        body = docs[docs.index("function _saveDoc(idx) {"):]
        body = body[:body.index("\n}")]
        self.assertIn(
            "if (window.StudioCore.stopAirbrush) window.StudioCore.stopAirbrush();",
            body)


@needs_node
class OneStrokeOneTransactionTests(unittest.TestCase):

    def test_timer_dabs_add_no_undo_entries(self):
        row = M["oneStrokeOneUndo"]
        self.assertTrue(row["timerAddsNoEntries"], f"{row}")

    def test_they_go_through_the_same_stamp_as_every_other_dab(self):
        """Which is what puts them inside the same transaction by construction
        rather than by care."""

        code = _code_only()
        body = code[code.index("function airbrushTick("):]
        body = body[:body.index("\n}")]
        self.assertIn("stampWet(x, y, S.stroke.lp,", body)

    def test_a_backgrounded_tab_cannot_dump_a_minute_of_paint(self):
        row = M["aBackgroundedTabCannotDump"]
        self.assertTrue(row["bounded"], f"{row}")


@needs_node
class OnlyTheAirbrushDeclaresItTests(unittest.TestCase):

    def test_exactly_one_preset_declares_it(self):
        row = M["onlyAirbrushDeclaresIt"]
        self.assertEqual(1, row["count"], f"{row['at']}")
        self.assertTrue(row["at"]["Airbrush"])

    def test_it_does_not_leak_to_the_next_preset(self):
        """The fifth field in this programme to need the unconditional-write
        rule. A preset that left it alone would keep depositing on a timer
        because the owner happened to pick Airbrush first."""

        self.assertTrue(M["onlyAirbrushDeclaresIt"]["resets"])

    def test_the_write_is_unconditional(self):
        code = _code_only()
        body = code[code.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.brushAirbrush = !!p.airbrush;", body)


class TheClockIsInjectableTests(unittest.TestCase):
    """The acceptance asks for deterministic fake-clock tests. A timer measured
    against the real clock is a timer measured against the machine's mood."""

    def test_the_clock_has_a_single_writer(self):
        code = _code_only()
        self.assertIn("function setAirbrushClock(fn)", code)
        # `_airClock` is assigned at its declaration and inside the setter, and
        # nowhere else. Anything else writing it would be a second source of
        # time that a test could not control.
        self.assertEqual(2, len(re.findall(r"_airClock\s*=", code)))

    def test_nothing_in_the_tick_reads_the_wall_clock_directly(self):
        code = _code_only()
        body = code[code.index("function airbrushTick("):]
        body = body[:body.index("\n}")]
        self.assertNotIn("Date.now", body)
        self.assertNotIn("performance.now", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE12_TESTS, found)


if __name__ == "__main__":
    unittest.main()
