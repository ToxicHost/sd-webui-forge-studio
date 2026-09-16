"""CT2: the shared input seam, EXECUTED rather than grepped.

Every other frontend guard in this suite reads source text, because most of
what they protect is structural — where a `return` sits, which collector runs.
This one is different: `canvas-input.js` is pure logic with no DOM at module
scope, deliberately, so it can be RUN. A guard that asserts
`pressureIsMeasured` exists proves nothing about what it answers for a pen
reporting 0.0, and that answer is the entire point of the file.

So these tests load the real module into Node and call it. `node --check` is
already used in this workflow; if `node` is missing the module skips rather
than passing vacuously, because a silently-skipped behavioural test is worse
than none.

The source-position guards that DO belong here — the seam loading before
`canvas-ui.js`, the transaction closing on commit — are in the last class.
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

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
FIXTURES = APP_ROOT.parent / "Evidence" / "ct2-input-seam"

EXPECTED_CT2_TESTS = 27

NODE = shutil.which("node")

CANVAS_INPUT = (FRONTEND / "canvas-input.js").read_text(encoding="utf-8")
CANVAS_UI = (FRONTEND / "canvas-ui.js").read_text(encoding="utf-8")
CANVAS_CORE = (FRONTEND / "canvas-core.js").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")


def _run_in_node(body: str):
    """Load the real `canvas-input.js` and run `body`, returning its JSON.

    The module publishes onto `window`, so a two-line shim is all the
    environment it needs — which is itself the property being relied on.
    """

    FIXTURES.mkdir(parents=True, exist_ok=True)
    harness = FIXTURES / "harness.js"
    harness.write_text(
        "globalThis.window = globalThis;\n"
        + CANVAS_INPUT
        + "\nconst I = window.StudioInput;\n"
        + "const toDoc = (x, y) => ({ x: x / 2, y: y / 2 });\n"
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(harness)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(
            f"node exited {result.returncode}:\n{result.stderr[:2000]}")
    return json.loads(result.stdout)


def _event(**fields):
    """A synthetic PointerEvent literal, as JS source."""

    base = {
        "pointerId": 1, "pointerType": "mouse", "isPrimary": True,
        "clientX": 100, "clientY": 200, "pressure": 0.5,
        "tiltX": 0, "tiltY": 0, "twist": 0,
        "buttons": 1, "button": 0, "timeStamp": 1000,
    }
    base.update(fields)
    return json.dumps(base)


@unittest.skipIf(NODE is None, "node is not on PATH; the seam cannot be RUN")
class PressureResolutionTests(unittest.TestCase):
    """The one decision the whole file exists to make."""

    def test_a_mouse_never_reports_a_measured_pressure(self):
        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.resolvePressure({_event(pressure=0.5)}),"
            f"I.resolvePressure({_event(pressure=0)}),"
            f"I.resolvePressure({_event(pressure=1)})"
            "]));")
        for answer in out:
            self.assertFalse(answer["available"])
            self.assertEqual(0.5, answer["pressure"])

    def test_a_released_mouse_does_not_paint_at_zero(self):
        """The requirement in the handoff's own words: "Mouse fallback must not
        interpret an unavailable pressure value as zero-opacity paint"."""

        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.resolvePressure({_event(pressure=0, buttons=0)})));")
        self.assertEqual(0.5, out["pressure"])
        self.assertFalse(out["available"])

    def test_a_pen_reports_its_real_pressure_including_zero(self):
        """The case the old `e.pressure || 0.5` could not express. A pen barely
        touching the tablet reports a real 0.0 and used to be handed 0.5."""

        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.resolvePressure({_event(pointerType='pen', pressure=0)}),"
            f"I.resolvePressure({_event(pointerType='pen', pressure=0.13)}),"
            f"I.resolvePressure({_event(pointerType='pen', pressure=1)})"
            "]));")
        self.assertEqual([0.0, 0.13, 1.0], [a["pressure"] for a in out])
        self.assertEqual([True, True, True], [a["available"] for a in out])

    def test_a_pen_outside_the_spec_range_is_clamped_not_trusted(self):
        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.resolvePressure({_event(pointerType='pen', pressure=-3)}),"
            f"I.resolvePressure({_event(pointerType='pen', pressure=7)})"
            "]));")
        self.assertEqual([0.0, 1.0], [a["pressure"] for a in out])

    def test_a_touch_reporting_only_contact_is_not_a_measurement(self):
        """Many digitisers report a constant 1 for contact and 0 for none.
        Neither is a force, and treating 1 as full pressure would make every
        finger stroke maximal."""

        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.resolvePressure({_event(pointerType='touch', pressure=0)}),"
            f"I.resolvePressure({_event(pointerType='touch', pressure=1)})"
            "]));")
        for answer in out:
            self.assertFalse(answer["available"])
            self.assertEqual(0.5, answer["pressure"])

    def test_a_touch_reporting_a_real_force_is_believed(self):
        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.resolvePressure({_event(pointerType='touch', pressure=0.4)})));")
        self.assertTrue(out["available"])
        self.assertEqual(0.4, out["pressure"])

    def test_an_unknown_device_is_treated_as_having_none(self):
        """Conservative on purpose: substituting a known-safe 0.5 is
        recoverable, painting at zero opacity is not."""

        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.resolvePressure({_event(pointerType='')}),"
            "I.resolvePressure({})"
            "]));")
        for answer in out:
            self.assertFalse(answer["available"])
            self.assertEqual(0.5, answer["pressure"])

    def test_the_raw_value_is_always_carried(self):
        """So a diagnostic can show what the device actually said, rather than
        what Studio decided to use."""

        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.resolvePressure({_event(pointerType='mouse', pressure=0.25)})));")
        self.assertEqual(0.25, out["raw"])
        self.assertEqual(0.5, out["pressure"])


@unittest.skipIf(NODE is None, "node is not on PATH; the seam cannot be RUN")
class NormalizationTests(unittest.TestCase):
    def test_the_sample_carries_document_and_css_coordinates(self):
        """Both, so no caller has to keep the raw event to get the other one:
        tools want document space, pan and zoom-drag want CSS pixels."""

        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.normalize({_event(clientX=100, clientY=200)}, toDoc, null)));")
        self.assertEqual((50, 100), (out["x"], out["y"]))
        self.assertEqual((100, 200), (out["clientX"], out["clientY"]))

    def test_tilt_is_zero_rather_than_absent_on_a_mouse(self):
        """"Flat" is the honest answer and means a tool does not have to
        branch on whether the field exists."""

        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.normalize({_event()}, toDoc, null)));")
        self.assertEqual((0, 0, 0), (out["tiltX"], out["tiltY"], out["twist"]))
        self.assertFalse(out["tiltAvailable"])

    def test_a_pen_reports_tilt_as_available(self):
        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.normalize({_event(pointerType='pen', tiltX=-31, tiltY=12)},"
            " toDoc, null)));")
        self.assertEqual((-31, 12), (out["tiltX"], out["tiltY"]))
        self.assertTrue(out["tiltAvailable"])

    def test_normalization_without_a_transform_does_not_throw(self):
        out = _run_in_node(
            "console.log(JSON.stringify("
            f"I.normalize({_event()}, null, null)));")
        self.assertEqual((0, 0), (out["x"], out["y"]))


@unittest.skipIf(NODE is None, "node is not on PATH; the seam cannot be RUN")
class CoalescedSampleTests(unittest.TestCase):
    """A pointermove fired once per frame can stand for a dozen physical
    samples. Nothing in Studio had ever asked for them."""

    def test_every_coalesced_sample_is_returned_in_order(self):
        out = _run_in_node(
            "const inner = [10, 20, 30, 40].map((x, i) => ("
            "{pointerId: 1, pointerType: 'mouse', isPrimary: true,"
            " clientX: x, clientY: x, pressure: 0.5, timeStamp: 100 + i}));"
            "const e = {pointerId: 1, pointerType: 'mouse', isPrimary: true,"
            " clientX: 40, clientY: 40, pressure: 0.5, timeStamp: 103,"
            " getCoalescedEvents: () => inner};"
            "console.log(JSON.stringify(I.samplesFrom(e, toDoc).map(s => s.x)));")
        self.assertEqual([5, 10, 15, 20], out)

    def test_every_coalesced_sample_is_flagged_as_one(self):
        out = _run_in_node(
            "const inner = [{clientX: 2, clientY: 2, pressure: 0.5, timeStamp: 1,"
            " pointerType: 'mouse', isPrimary: true}];"
            "const e = {clientX: 2, clientY: 2, pressure: 0.5, timeStamp: 1,"
            " pointerType: 'mouse', isPrimary: true,"
            " getCoalescedEvents: () => inner};"
            "console.log(JSON.stringify(I.samplesFrom(e, toDoc)"
            ".map(s => s.coalesced)));")
        self.assertEqual([True], out)

    def test_identical_repeats_are_dropped(self):
        """Deduped on the RAW coordinates and timestamp, before the document
        transform: at high zoom the transform can separate two samples that
        were the same sample."""

        out = _run_in_node(
            "const one = {clientX: 5, clientY: 5, pressure: 0.5, timeStamp: 9,"
            " pointerType: 'mouse', isPrimary: true};"
            "const e = Object.assign({}, one,"
            " {getCoalescedEvents: () => [one, one, one]});"
            "console.log(JSON.stringify(I.samplesFrom(e, toDoc).length));")
        self.assertEqual(1, out)

    def test_a_browser_without_the_method_still_paints(self):
        out = _run_in_node(
            f"console.log(JSON.stringify(I.samplesFrom({_event()}, toDoc)"
            ".map(s => s.x)));")
        self.assertEqual([50], out)

    def test_a_method_that_throws_still_paints(self):
        out = _run_in_node(
            "const e = {clientX: 8, clientY: 8, pressure: 0.5, timeStamp: 1,"
            " pointerType: 'mouse', isPrimary: true,"
            " getCoalescedEvents: () => { throw new Error('nope'); }};"
            "console.log(JSON.stringify(I.samplesFrom(e, toDoc).map(s => s.x)));")
        self.assertEqual([4], out)

    def test_an_empty_list_still_paints(self):
        """A browser that returns nothing must not produce a stroke with no
        samples in it."""

        out = _run_in_node(
            "const e = {clientX: 6, clientY: 6, pressure: 0.5, timeStamp: 1,"
            " pointerType: 'mouse', isPrimary: true,"
            " getCoalescedEvents: () => []};"
            "console.log(JSON.stringify(I.samplesFrom(e, toDoc).map(s => s.x)));")
        self.assertEqual([3], out)


@unittest.skipIf(NODE is None, "node is not on PATH; the seam cannot be RUN")
class PrimaryPointerTests(unittest.TestCase):
    def test_a_secondary_contact_does_not_drive(self):
        """Every finger after the first in a multi-touch gesture reports
        `isPrimary === false`, and nothing checked it, so a second contact
        opened a second stroke into the same buffers."""

        out = _run_in_node(
            "console.log(JSON.stringify(["
            f"I.isDrivingPointer({_event(isPrimary=True)}),"
            f"I.isDrivingPointer({_event(isPrimary=False)}),"
            "I.isDrivingPointer({}),"
            "I.isDrivingPointer(null)"
            "]));")
        self.assertEqual([True, False, True, False], out)


class TheSeamIsWiredTests(unittest.TestCase):
    """Position guards, which is what these genuinely are."""

    def test_the_seam_loads_before_the_canvas_ui(self):
        order = re.search(r"const scripts = \[(.*?)\];", INDEX_HTML, re.S)
        self.assertIsNotNone(order)
        names = re.findall(r'"([^"]+\.js)"', order.group(1))
        self.assertIn("canvas-input.js", names)
        self.assertLess(names.index("canvas-input.js"), names.index("canvas-ui.js"))

    def test_the_pointer_reader_goes_through_the_seam(self):
        body = CANVAS_UI[CANVAS_UI.index("function pos(e) {"):]
        body = body[:body.index("\n}\n") + 3]
        self.assertIn("I.normalize(e, C.screenToDoc, null)", body)

    def test_the_move_handler_consumes_every_coalesced_sample(self):
        self.assertIn("const stream = posSamples(e);", CANVAS_UI)
        self.assertIn("I.samplesFrom(e, C.screenToDoc)", CANVAS_UI)

    def test_the_transaction_closes_when_a_stroke_commits(self):
        body = CANVAS_CORE[CANVAS_CORE.index("function commitStroke() {"):]
        body = body[:2000]
        self.assertIn("clearStrokeUndo();", body)

    def test_a_cancel_rolls_the_stroke_back_and_releases_capture(self):
        start = CANVAS_UI.index('cv.addEventListener("pointercancel"')
        body = CANVAS_UI[start:start + 900]
        self.assertIn("StudioInput.release(cv, e.pointerId)", body)
        self.assertIn("C.abortStroke()", body)

    def test_a_cancelled_stroke_is_not_redoable(self):
        """`abortStroke` deliberately does not call `undo()`: that pushes onto
        the redo stack, and a stroke the owner never finished is not one they
        can ask to have back."""

        body = CANVAS_CORE[CANVAS_CORE.index("function abortStroke() {"):]
        body = body[:body.index("\nfunction ", 10)]
        self.assertNotIn("redoStack", body)
        self.assertIn("S.undoStack.pop()", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_CT2_TESTS, found)

    def test_node_is_actually_available_here(self):
        """Recorded rather than silently skipped. If this fails, the
        behavioural half of this module did not run and its guards proved
        nothing on this machine."""

        self.assertIsNotNone(
            NODE,
            "node is not on PATH, so every executed test above was skipped",
        )


if __name__ == "__main__":
    unittest.main()
