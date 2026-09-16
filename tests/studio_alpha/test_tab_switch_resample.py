"""Switching Canvas documents must not resample the layers. AR4.5.

THE DEFECT THIS CLOSES

Found during AR4.4's live browser journeys, by measuring rather than reading.
A layer painted with a crisp `fillRect(10, 10, 100, 200)` holds exactly 20000
opaque pixels. After ONE ordinary `StudioDocs.switchDoc` into that document:

    painted originally             20000
    restored from recovery         20000     <- recovery is exact
    after one tab switch in        20400     <- 400 feathered pixels, alpha 27
    doc object after switching out 20400     <- the damage is saved back

It COMPOUNDS. Every switch resamples again and writes the result into the
document, so the degradation accumulates for as long as the owner keeps
working. Silent, irreversible, on an action carrying no warning.

Instrumenting `resizeCanvas` across one switch (document A 256x320, B 192x448):

    resizeCanvas(192, 320)  from 192x448   app.js  <- _writeField
    resizeCanvas(192, 448)  from 192x320   app.js  <- _writeField
    resizeCanvas(192, 448)  from 192x448   _maybeResizeCanvas (no-op)

Width and height are two separate fields. `_writeField` dispatches `change`
after each, and app.js's handler reads BOTH inputs and resizes immediately --
so the first write drove the canvas through NEW-width x OLD-height, a size that
is neither document's.

`applyWorkflowState`'s tail already performed ONE correct resize. The per-field
handlers simply got there first and left it a rubber stamp on damage already
done.

THE EXTENSION HAS THE SAME DEFECT

All four pieces are byte-identical upstream: the `["paramWidth","paramHeight"]`
change handler, `_writeField`'s number case, `_maybeResizeCanvas`, and the
`dimensionsApplied` tail. So this is a shared upstream bug Studio inherited,
not a regression and not a parity gap.

Studio diverges DELIBERATELY. Parity is the target for behaviour, not for data
loss. `SharedDefectDivergenceTests` records that decision so a later parity
sweep does not "correct" it back.

Review: `Evidence/source-review/AR4.5-tab-switch-resample.md`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_RESAMPLE_TESTS = 14

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
WORKFLOW_JS = (FRONTEND / "workflow-state.js").read_text(encoding="utf-8")
DOCS_JS = (FRONTEND / "studio-docs.js").read_text(encoding="utf-8")


def _handler_body() -> str:
    """The dimension `change` handler, sliced from its own binding."""

    start = APP_JS.index('["paramWidth", "paramHeight"].forEach(id => {')
    return APP_JS[start:start + 2600]


def _apply_body() -> str:
    start = WORKFLOW_JS.index("function applyWorkflowState(")
    return WORKFLOW_JS[start:WORKFLOW_JS.index("\n}", start)]


class BatchResizeTests(unittest.TestCase):
    """One resize per batch, with the FINAL pair."""

    def test_the_handler_yields_while_a_batch_is_writing(self) -> None:
        """THE test. Fails against the intermediate resize."""

        body = _handler_body()
        guard_at = body.index("isApplyingBatch()")
        resize_at = body.index("window.StudioCore.resizeCanvas(w, h)")
        self.assertLess(guard_at, resize_at,
                        "the handler resizes before checking for a batch")
        self.assertIn("return;", body[guard_at:resize_at],
                      "the batch check does not actually skip the resize")

    def test_the_batch_marks_itself(self) -> None:
        self.assertIn("_applyingBatch = true;", _apply_body())
        self.assertIn("isApplyingBatch", WORKFLOW_JS)

    def test_the_flag_is_cleared_in_a_finally(self) -> None:
        """A field write that throws must not latch the flag: every later
        hand-typed dimension would stop resizing and the owner would have no
        way to get it back."""

        body = _apply_body()
        self.assertRegex(
            body,
            r"finally\s*\{\s*\n?\s*_applyingBatch = false;",
            "the batch flag is not released on a throwing field write")

    def test_the_tail_still_performs_exactly_one_resize(self) -> None:
        body = _apply_body()
        self.assertIn("_maybeResizeCanvas(w, h)", body)
        self.assertEqual(1, body.count("_maybeResizeCanvas("),
                         "the batch tail must resize once, not per field")

    def test_the_tail_resize_uses_the_final_pair(self) -> None:
        """Catches a tail that resizes from a single field's value."""

        body = _apply_body()
        self.assertIn('var w = parseInt(norm.settings.width, 10);', body)
        self.assertIn('var h = parseInt(norm.settings.height, 10);', body)


class NothingIsLostBySuppressingTheHandlerTests(unittest.TestCase):
    """The end state must be identical, not merely close."""

    def test_the_tail_performs_the_handler_s_full_sync(self) -> None:
        start = WORKFLOW_JS.index("function _syncCanvasChrome(")
        body = WORKFLOW_JS[start:WORKFLOW_JS.index("\n}", start)]
        for call in ("StatusBar", "setDimensions", "syncCanvasToViewport",
                     "zoomFit", "updateStatus", "redraw", "canvasStatus"):
            with self.subTest(call=call):
                self.assertIn(call, body)

    def test_the_resize_path_invokes_that_sync(self) -> None:
        start = WORKFLOW_JS.index("function _maybeResizeCanvas(")
        body = WORKFLOW_JS[start:WORKFLOW_JS.index("\n}", start)]
        self.assertIn("_syncCanvasChrome(width, height)", body)

    def test_the_sync_cannot_break_a_document_switch(self) -> None:
        """A chrome failure must not propagate: the pixels are already
        correct by then, and throwing here would abort the tab switch."""

        start = WORKFLOW_JS.index("function _syncCanvasChrome(")
        body = WORKFLOW_JS[start:WORKFLOW_JS.index("\n}", start)]
        self.assertIn("try {", body)
        self.assertIn("catch (e)", body)


class HandTypedDimensionsStillWorkTests(unittest.TestCase):
    """The owner typing a width is NOT a batch and must still resize."""

    def test_the_guard_is_conditional_not_unconditional(self) -> None:
        body = _handler_body()
        self.assertNotIn("return;\n      const w = parseInt", body,
                         "the handler returns unconditionally")
        self.assertIn("window.StudioWorkflowState", body)

    def test_the_guard_tolerates_the_module_being_absent(self) -> None:
        """workflow-state.js is a separate script. If it has not loaded, the
        handler must behave as it always did rather than stop resizing."""

        body = _handler_body()
        guard = body[body.index("// A BATCH RESIZES ONCE"):body.index("const w = parseInt")]
        self.assertIn('typeof window.StudioWorkflowState.isApplyingBatch === "function"',
                      guard)


class SharedDefectDivergenceTests(unittest.TestCase):
    """This is an inherited upstream bug, and the divergence is deliberate.

    Recorded so a later parity sweep does not restore the damage in the name
    of matching the Extension.
    """

    def test_the_divergence_is_documented_in_the_code(self) -> None:
        start = WORKFLOW_JS.index("function _maybeResizeCanvas(")
        preamble = WORKFLOW_JS[max(0, start - 2000):start]
        self.assertIn("Extension", preamble)
        self.assertIn("AR4.5-tab-switch-resample.md", preamble)

    def test_the_review_record_exists(self) -> None:
        record = (APP_ROOT.parent / "Evidence" / "source-review"
                  / "AR4.5-tab-switch-resample.md")
        self.assertTrue(record.is_file(), "the mandated review record is absent")
        text = record.read_text(encoding="utf-8")
        self.assertIn("NOT-APPLICABLE", text, "Neo's disposition is unrecorded")
        self.assertIn("20000", text, "the measurement is not in the record")

    def test_the_switch_path_still_applies_the_saved_panel(self) -> None:
        """The fix must not have severed the panel restore it runs through."""

        self.assertIn("_loadGenPanel(doc.genPanel)", DOCS_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_RESAMPLE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
