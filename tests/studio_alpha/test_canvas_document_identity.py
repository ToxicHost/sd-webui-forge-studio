"""Which Canvas document a queued job captured, and which version. AR2.1.

THE DEFECT THESE CATCH

Studio QUEUES. `/api/generate` returns 202 and the job runs later, so between
admission and execution the owner can paint, undo, resize, add a layer or open
something else. Nothing on the request said which Canvas state it was built
from, so a queued job that executed against repainted pixels was silently
wrong and nothing downstream could tell.

The Extension has no equivalent and does not need one: `run_generation`
receives the canvas and mask as arguments and consumes them in the same call.
This is Studio-owned by NECESSITY, not parity debt -- the same argument that
produced `InputAsset.content_hash`, applied to the document rather than to the
bytes. Review: `Evidence/source-review/AR2-canvas-document-identity.md`.

WHY THE REVISION IS BUMPED WHERE IT IS

Four sites: `saveUndo`, `saveStructuralUndo`, `undo`, `redo`. Not the six
`undoStack.push` occurrences -- three of those live inside undo/redo and
restore rather than mutate, so bumping there would count one owner action
twice.

Everything generation-affecting passes through the two save functions
("Each op pushes one undo step before mutating", canvas-core.js). Selection,
tool changes, zoom and panel state do not. So "cosmetic changes must not
increment the revision" holds BY CONSTRUCTION rather than by enumerating every
cosmetic action and remembering to skip it -- which is the kind of list that
goes wrong the first time someone adds a tool.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_DOCUMENT_IDENTITY_TESTS = 18

CANVAS_CORE = (APP_ROOT / "forge_studio" / "frontend"
               / "canvas-core.js").read_text(encoding="utf-8")
APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")


def refusal():
    """Resolved at CALL time -- `test_import_boundaries` purges the package."""

    import forge_studio.presentation

    return forge_studio.presentation.PresentationError


def admitted(**generation):
    from forge_studio.contracts import GenerationRequest
    from forge_studio.presentation import _validated_request_payload

    body = {"model": "m", "generation": {
        "positive_prompt": "p", "negative_prompt": "", "seed": 7, "steps": 4,
        "cfg_scale": 4.0, "width": 64, "height": 64}}
    body["generation"].update(generation)
    return GenerationRequest(**_validated_request_payload(body))


DOCUMENT = {"document_id": "a" * 32, "revision": 5}


class AdmissionTests(unittest.TestCase):
    def test_the_pair_reaches_the_request(self) -> None:
        document = admitted(document=dict(DOCUMENT)).document
        self.assertEqual("a" * 32, document.document_id)
        self.assertEqual(5, document.revision)

    def test_absent_means_absent(self) -> None:
        """An API caller has no Canvas. That is legitimate, and must not be
        turned into a fabricated identity."""

        self.assertIsNone(admitted().document)

    def test_a_revision_of_zero_is_a_real_revision(self) -> None:
        """Catches: falsy-testing the revision and dropping a fresh document."""

        document = admitted(document={"document_id": "b" * 32,
                                      "revision": 0}).document
        self.assertIsNotNone(document)
        self.assertEqual(0, document.revision)

    def test_a_path_cannot_be_a_document_id(self) -> None:
        """THE point of the strict pattern. Identity must not become a
        filesystem channel -- the rule the asset service already holds."""

        for bad in ("C:/Users/x/doc.png", "../../etc/passwd", "doc.psd",
                    "/home/x/a", "a" * 31, "a" * 33, "A" * 32, "g" * 32):
            with self.subTest(document_id=bad):
                with self.assertRaises(refusal()):
                    admitted(document={"document_id": bad, "revision": 0})

    def test_a_negative_revision_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            admitted(document={"document_id": "a" * 32, "revision": -1})

    def test_a_boolean_is_not_a_revision(self) -> None:
        with self.assertRaises(refusal()):
            admitted(document={"document_id": "a" * 32, "revision": True})

    def test_an_unknown_field_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            admitted(document={"document_id": "a" * 32, "revision": 0,
                               "title": "my painting"})

    def test_a_non_object_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            admitted(document="a" * 32)


class ImmutabilityTests(unittest.TestCase):
    def test_the_captured_pair_cannot_be_rewritten(self) -> None:
        """Catches: a later stage 'refreshing' the revision to whatever the
        Canvas is NOW, which would defeat the entire point."""

        import dataclasses

        document = admitted(document=dict(DOCUMENT)).document
        with self.assertRaises(dataclasses.FrozenInstanceError):
            document.revision = 99  # type: ignore[misc]

    def test_the_request_holding_it_is_frozen(self) -> None:
        import dataclasses

        request = admitted(document=dict(DOCUMENT))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.document = None  # type: ignore[misc]


class CanvasBumpSiteTests(unittest.TestCase):
    """Where the revision moves, asserted against the source."""

    def test_both_save_functions_bump(self) -> None:
        for fn in ("function saveUndo(label) {\n    S._canvasDirty = true;\n    _bumpRevision();",
                   "function saveStructuralUndo(label) {\n    S._canvasDirty = true;\n    _bumpRevision();"):
            with self.subTest(fn=fn.split("(")[0]):
                self.assertIn(fn, CANVAS_CORE)

    def test_undo_and_redo_produce_a_new_revision(self) -> None:
        """Catches: rewinding the counter on undo, which would let two
        different document states share one identity."""

        for fn in ("function undo() {", "function redo() {"):
            index = CANVAS_CORE.index(fn)
            window = CANVAS_CORE[index:index + 220]
            with self.subTest(fn=fn):
                self.assertIn("_bumpRevision();", window)
        self.assertNotIn("S.canvasRevision -= ", CANVAS_CORE)
        self.assertNotIn("S.canvasRevision = 0", CANVAS_CORE.replace(
            "S.canvasRevision = 0;", "", 1))

    def test_a_reset_mints_a_new_document(self) -> None:
        index = CANVAS_CORE.index("function resetCanvasState(opts) {")
        self.assertIn("_startNewDocument();",
                      CANVAS_CORE[index:index + 400])

    def test_the_id_is_not_derived_from_a_filename(self) -> None:
        index = CANVAS_CORE.index("function _newDocumentId()")
        body = CANVAS_CORE[index:index + 400]
        self.assertIn("getRandomValues", body)
        for banned in ("name", "path", "file", "title"):
            with self.subTest(token=banned):
                self.assertNotIn(banned, body.lower().split("return")[0]
                                 .replace("_newdocumentid", ""))

    def test_identity_is_exposed_on_the_public_api(self) -> None:
        self.assertIn("documentIdentity:", CANVAS_CORE)
        self.assertIn("restoreDocumentIdentity:", CANVAS_CORE)


class CollectorSeamTests(unittest.TestCase):
    """frontend -> contract, the join that has silently dropped fields four
    times in this project."""

    def test_the_collector_captures_identity_with_the_pixels(self) -> None:
        self.assertIn("eng.documentIdentity ? eng.documentIdentity() : null",
                      APP_JS)

    def test_the_body_sends_it_for_every_operation(self) -> None:
        """Not only for inpaint. A blank Canvas is still a document at a
        revision, and the first draft of this landed inside the inpaint-only
        spread -- which would have recorded identity for one operation in
        three."""

        index = APP_JS.index("...(_source.document ? { document: _source.document } : {})")
        operation_at = APP_JS.index("operation: _source.operation,")
        inpaint_at = APP_JS.index('...(_source.operation === "inpaint" ?')
        self.assertGreater(index, operation_at)
        self.assertLess(index, inpaint_at)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_DOCUMENT_IDENTITY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
