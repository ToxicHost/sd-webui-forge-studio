"""Layered Canvas crash recovery. AR4.4.

THE DEFECT THIS CLOSES

`studio-docs.js` held every open document in JavaScript memory and nowhere
else -- its own header says the working state is "a window into the active
document". So F5, a renderer crash or an OS kill discarded the owner's layers,
mask and Regional state outright. Reproduced and preserved BEFORE any code was
written, in `Evidence/ar44-f5/BEFORE-IMPLEMENTATION.md`:

    before   document c164f3...  256x320  cyan paint  mask painted
    after    document 048efe...  768x768  gone        gone

89 session parameters survived that same refresh, because AR4.3 had already
made them server-owned. The artwork did not, and that asymmetry is the whole
argument for this work.

WHAT THESE TESTS REFUSE TO ACCEPT

**A ceiling.** The owner's authorized scope forbids an image-dimension limit, a
pixel limit, a document-size ceiling, a recovery budget and silent LRU
eviction. `NoCeilingTests` scans the module for a size constant and proves a
blob larger than the 16 MB HTTP request bound round-trips anyway, because
chunked append is the ONLY reason that bound is not one by the back door.

**A flattened substitute.** A recovered document must be a real document.
`RoundTripTests` asserts layer pixels, layer ORDERING, geometry, mask, Regional
state, active layer, `document_id` and revision all survive -- a single
composited PNG would pass a naive "did it come back" check and lose everything
that makes it editable.

**An export clearing recovery.** Saving a PNG is not a project save. The owner
still has unsaved layers behind that flat picture, so only an explicit close or
discard may remove recovery.

**A torn write read as truth.** An interrupted commit must leave the LAST
COHERENT snapshot, not a half-written one. Recovery that returns a broken
document is worse than recovery that returns the previous good one.

Review: `Evidence/source-review/AR4.4-canvas-crash-recovery.md`.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_RECOVERY_TESTS = 54

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
RECOVERY_JS = (FRONTEND / "canvas-recovery.js").read_text(encoding="utf-8")
DOCS_JS = (FRONTEND / "studio-docs.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
CORE_JS = (FRONTEND / "canvas-core.js").read_text(encoding="utf-8")
RECOVERY_PY = (APP_ROOT / "forge_studio" / "canvas_recovery.py").read_text(
    encoding="utf-8")


def _code_only(source: str, *, python: bool) -> str:
    """Source with comments and docstrings removed.

    These guards ban a WORD. The modules explain at length that they do not do
    the thing -- "no silent LRU eviction", "`toDataURL` would encode
    synchronously" -- so a naive substring scan fails on the very prose that
    documents the rule. Stripping first is what makes the guard test the code
    rather than the commentary.
    """

    if python:
        import io
        import tokenize

        kept, previous = [], None
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            kind, text = token.type, token.string
            if kind == tokenize.COMMENT:
                continue
            if kind == tokenize.STRING and previous in (
                    None, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                    tokenize.DEDENT):
                continue            # a docstring, not a value
            kept.append(text)
            if kind not in (tokenize.NL, tokenize.NEWLINE):
                previous = kind
            else:
                previous = kind
        return " ".join(kept)

    import re as _re

    without_block = _re.sub(r"/\*[\s\S]*?\*/", " ", source)
    return _re.sub(r"(?m)^\s*//.*$", " ", without_block)

DOC = "a" * 32
OTHER = "b" * 32


def store(root):
    """Resolved at CALL time.

    `test_import_boundaries` purges every `forge_studio.*` module, so a class
    bound at import time here is a stale object two files later.
    """

    from forge_studio.canvas_recovery import CanvasRecoveryStore

    return CanvasRecoveryStore(root)


def errors():
    import forge_studio.canvas_recovery as recovery

    return recovery


def url(payload: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode()


def write(s, document_id=DOC, blobs=None, manifest=None):
    """One complete staged write, the way the page performs it."""

    s.begin(document_id)
    for name, data in (blobs or {}).items():
        s.put_blob(document_id, name, url(data))
    return s.commit(document_id, manifest if manifest is not None else {"W": 8, "H": 8})


class RoundTripTests(unittest.TestCase):
    """A real document, not a flattened substitute."""

    def test_a_document_survives_a_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"PIXELS"})
            loaded = s.load(DOC)
            self.assertEqual(DOC, loaded["document_id"])
            self.assertEqual(
                b"PIXELS",
                base64.b64decode(
                    loaded["blob_data"]["layer-1.png"].split(",", 1)[-1]))

    def test_layer_ordering_survives(self) -> None:
        """Catches: layers restored as a set. Order IS the composite -- a
        document whose layers come back shuffled is a different picture."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            order = [{"id": 3, "blob": "layer-3.png"},
                     {"id": 1, "blob": "layer-1.png"},
                     {"id": 2, "blob": "layer-2.png"}]
            write(s, manifest={"W": 8, "H": 8, "layers": order},
                  blobs={f"layer-{n}.png": b"P" for n in (1, 2, 3)})
            self.assertEqual(
                [3, 1, 2],
                [L["id"] for L in s.load(DOC)["manifest"]["layers"]])

    def test_geometry_survives_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, manifest={"W": 256, "H": 320})
            manifest = s.load(DOC)["manifest"]
            self.assertEqual((256, 320), (manifest["W"], manifest["H"]))

    def test_mask_and_regional_state_survive(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, manifest={
                "W": 8, "H": 8,
                "mask": {"blob": "mask.png", "visible": True, "opacity": 0.5},
                "regions": [{"id": 1, "color": "#ff0000", "prompt": "a cat",
                             "weight": 1.0, "denoise": 0.7,
                             "blob": "region-1.png"}],
                "regionMode": True, "activeRegionId": 1},
                blobs={"mask.png": b"M", "region-1.png": b"R"})
            manifest = s.load(DOC)["manifest"]
            self.assertEqual("mask.png", manifest["mask"]["blob"])
            self.assertTrue(manifest["regionMode"])
            self.assertEqual("a cat", manifest["regions"][0]["prompt"])
            self.assertIn("region-1.png", s.load(DOC)["blob_data"])

    def test_the_active_layer_and_revision_survive(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, manifest={"W": 8, "H": 8, "activeLayerIdx": 2,
                               "canvas_revision": 17})
            manifest = s.load(DOC)["manifest"]
            self.assertEqual(2, manifest["activeLayerIdx"])
            self.assertEqual(17, manifest["canvas_revision"])

    def test_documents_are_independent(self) -> None:
        """Two open tabs. Before AR4.4 both reported the same `document_id`,
        so one would have overwritten the other's recovery."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, DOC, {"layer-1.png": b"FIRST"})
            write(s, OTHER, {"layer-1.png": b"SECOND"})
            self.assertEqual(
                b"FIRST",
                base64.b64decode(
                    s.load(DOC)["blob_data"]["layer-1.png"].split(",", 1)[-1]))
            self.assertEqual(2, len(s.list()))

    def test_a_later_write_replaces_the_earlier_one(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"V1", "layer-2.png": b"OLD"})
            write(s, blobs={"layer-1.png": b"V2"})
            loaded = s.load(DOC)
            self.assertEqual(
                b"V2",
                base64.b64decode(
                    loaded["blob_data"]["layer-1.png"].split(",", 1)[-1]))
            self.assertNotIn("layer-2.png", loaded["blob_data"],
                             "a deleted layer must not survive as a ghost")


class NoCeilingTests(unittest.TestCase):
    """The owner forbade every form of limit. These prove there is none."""

    def test_no_size_constant_exists_in_the_module(self) -> None:
        """Catches a ceiling reintroduced later, by name."""

        code = _code_only(RECOVERY_PY, python=True)
        for banned in ("MAX_DOCUMENT_BYTES", "MAX_RECOVERY", "MAX_BLOB",
                       "MAX_PIXELS", "RECOVERY_BUDGET", "MAX_DOCUMENTS"):
            with self.subTest(constant=banned):
                self.assertNotIn(banned, code)

    def test_no_eviction_exists(self) -> None:
        """Silent LRU eviction was forbidden by name. A recovery store that
        quietly drops the oldest document loses work without ever saying so."""

        code = _code_only(RECOVERY_PY, python=True).lower()
        for banned in ("lru", "evict", "prune", "trim_oldest"):
            with self.subTest(term=banned):
                self.assertNotIn(banned, code)

    def test_a_blob_larger_than_the_request_bound_round_trips(self) -> None:
        """THE decisive one. `_MAX_SOURCE_REQUEST_BYTES` is 16 MB, so a blob
        that had to arrive in one request would make that bound a
        document-size ceiling. Chunked append is why it is not."""

        from forge_studio.presentation import _MAX_SOURCE_REQUEST_BYTES

        payload = b"\xa7" * (_MAX_SOURCE_REQUEST_BYTES + 1024)
        encoded = base64.b64encode(payload).decode()
        chunk = 4 * 1024 * 1024          # a multiple of 4, so each decodes
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.begin(DOC)
            first = True
            for offset in range(0, len(encoded), chunk):
                s.put_blob(
                    DOC, "layer-1.png",
                    ("data:image/png;base64," if first else "")
                    + encoded[offset:offset + chunk],
                    append=not first)
                first = False
            s.commit(DOC, {"W": 4096, "H": 4096})
            restored = base64.b64decode(
                s.load(DOC)["blob_data"]["layer-1.png"].split(",", 1)[-1])
        self.assertEqual(payload, restored)
        self.assertGreater(len(restored), _MAX_SOURCE_REQUEST_BYTES)

    def test_many_documents_are_all_kept(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            for n in range(24):
                write(s, f"{n:032x}", {"layer-1.png": b"P"})
            self.assertEqual(24, len(s.list()))

    def test_no_dimension_is_validated(self) -> None:
        """A dimension limit was forbidden. The store must not have an
        opinion about how large a canvas may be."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, manifest={"W": 16384, "H": 16384})
            self.assertEqual(16384, s.load(DOC)["manifest"]["W"])


class TornWriteTests(unittest.TestCase):
    def test_an_interrupted_write_leaves_the_last_coherent_state(self) -> None:
        """Catches: recovery that returns a half-written document. The
        previous good snapshot is the correct answer, and a partial one that
        LOOKS valid is the failure mode worth spending a test on."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"V1"}, manifest={"W": 8, "H": 8})
            s.begin(DOC)                          # a write that never commits
            s.put_blob(DOC, "layer-1.png", url(b"V2"))
            loaded = s.load(DOC)
            self.assertEqual(8, loaded["manifest"]["W"])
            self.assertEqual(
                b"V1",
                base64.b64decode(
                    loaded["blob_data"]["layer-1.png"].split(",", 1)[-1]))

    def test_a_crash_between_the_two_renames_is_reclaimed(self) -> None:
        """Catches a defect the FIRST torn-write test could not see.

        `commit` moves the live directory aside and then moves the staged one
        into place. A process killed BETWEEN those two renames leaves the last
        coherent snapshot under `.old` and nothing at the real name. The
        earlier test covered the other half -- an upload abandoned before
        commit -- and passed while this case silently reported "no documents"
        to an owner whose work was one rename away.

        Found by simulating the crash on a real filesystem during the live
        journeys, not by reading the code.
        """

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"KEEP"}, manifest={"W": 256, "H": 320})
            live = Path(root) / "recovery" / DOC
            live.rename(live.with_name(live.name + ".99999.old"))
            self.assertFalse(live.exists(), "the fixture must really strand it")

            self.assertEqual([DOC], [e["document_id"] for e in store(root).list()])
            loaded = store(root).load(DOC)
            self.assertEqual(256, loaded["manifest"]["W"])
            self.assertEqual(
                b"KEEP",
                base64.b64decode(
                    loaded["blob_data"]["layer-1.png"].split(",", 1)[-1]))

    def test_a_commit_referencing_a_missing_blob_is_refused(self) -> None:
        """An incomplete snapshot must not be sealed. Committing it would
        replace a good document with one whose layer cannot be drawn."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.begin(DOC)
            with self.assertRaises(errors().MalformedRecovery):
                s.commit(DOC, {"W": 8, "H": 8,
                               "layers": [{"id": 1, "blob": "layer-9.png"}]})

    def test_a_blob_without_an_open_write_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(errors().MalformedRecovery):
                store(root).put_blob(DOC, "layer-1.png", url(b"P"))

    def test_a_corrupt_manifest_does_not_return_a_broken_document(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"P"})
            (Path(root) / "recovery" / DOC / "manifest.json").write_text(
                "{ torn", encoding="utf-8")
            with self.assertRaises(errors().RecoveryError):
                s.load(DOC)


class RefusalTests(unittest.TestCase):
    def test_an_unknown_document_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(errors().UnknownDocument):
                store(root).load("f" * 32)

    def test_a_traversing_document_id_is_refused(self) -> None:
        """Catches: recovery writing outside the state root. The id reaches
        the filesystem as a directory name, so it is validated, not trusted."""

        with tempfile.TemporaryDirectory() as root:
            for bad in ("../escape", "..\\escape", "a" * 31, "A" * 32,
                        "g" * 32, "", "a/b"):
                with self.subTest(document_id=bad):
                    with self.assertRaises(errors().MalformedRecovery):
                        store(root).begin(bad)

    def test_a_traversing_blob_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.begin(DOC)
            for bad in ("../x.png", "x.exe", "x.png.exe", "/abs.png",
                        "..\\x.png", ".hidden.png"):
                with self.subTest(name=bad):
                    with self.assertRaises(errors().MalformedRecovery):
                        s.put_blob(DOC, bad, url(b"P"))

    def test_a_non_base64_blob_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.begin(DOC)
            with self.assertRaises(errors().MalformedRecovery):
                s.put_blob(DOC, "layer-1.png", "data:image/png;base64,!!!!")

    def test_no_response_carries_a_filesystem_path(self) -> None:
        """The owner's state root must not leak to the page. Same rule the
        session store follows."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            written = write(s, blobs={"layer-1.png": b"P"})
            for payload in (written, s.load(DOC), {"l": s.list()}):
                text = json.dumps(payload).lower()
                self.assertNotIn("path", text)
                self.assertNotIn(root.replace("\\", "/").lower(), text)


class DiscardTests(unittest.TestCase):
    def test_discard_removes_the_whole_document(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"P"})
            s.discard(DOC)
            self.assertEqual([], s.list())
            self.assertFalse((Path(root) / "recovery" / DOC).exists())

    def test_a_discarded_document_does_not_resurrect(self) -> None:
        """Catches: a close that stops LISTING a document while leaving it on
        disk, so it reappears at the next launch the owner did not ask for."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, blobs={"layer-1.png": b"P"})
            s.discard(DOC)
            self.assertEqual([], store(root).list())

    def test_discarding_one_document_leaves_the_others(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            write(s, DOC, {"layer-1.png": b"P"})
            write(s, OTHER, {"layer-1.png": b"P"})
            s.discard(DOC)
            self.assertEqual([OTHER], [e["document_id"] for e in s.list()])

    def test_discarding_nothing_is_not_an_error(self) -> None:
        """Closing a tab that never got a snapshot must not raise."""

        with tempfile.TemporaryDirectory() as root:
            store(root).discard("c" * 32)


class AdapterActionTests(unittest.TestCase):
    """The operations the page actually calls."""

    def _adapter(self, root):
        from forge_studio.canvas_recovery import CanvasRecoveryStore
        from forge_studio.preferences import DefaultsStore
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        adapter = SourceFrontendAdapter.__new__(SourceFrontendAdapter)
        adapter._defaults = DefaultsStore(root)
        adapter._recovery = CanvasRecoveryStore(root)
        return adapter

    def _write(self, a, document_id=DOC):
        a._recovery_action("recovery_begin", {"document_id": document_id})
        a._recovery_action("recovery_blob", {
            "document_id": document_id, "name": "layer-1.png",
            "data": url(b"PIXELS")})
        return a._recovery_action("recovery_commit", {
            "document_id": document_id, "manifest": {"W": 256, "H": 320}})

    def test_the_staged_write_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            a = self._adapter(root)
            self._write(a)
            loaded = a._recovery_action(
                "recovery_load", {"document_id": DOC})["settings"]
            self.assertEqual(256, loaded["manifest"]["W"])
            self.assertIn("layer-1.png", loaded["blob_data"])

    def test_listing_with_nothing_stored_is_an_explicit_absence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual([], self._adapter(root)._recovery_action(
                "recovery_list", {})["settings"]["documents"])

    def test_a_missing_document_refuses_with_404(self) -> None:
        """A named refusal, not HTTP 200 with `{"ok": false}`."""

        from forge_studio.source_api_adapter import SourceFrontendAdapterError

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SourceFrontendAdapterError) as caught:
                self._adapter(root)._recovery_action(
                    "recovery_load", {"document_id": "f" * 32})
            self.assertEqual(404, caught.exception.error["http_status"])

    def test_a_malformed_id_refuses_with_400(self) -> None:
        from forge_studio.source_api_adapter import SourceFrontendAdapterError

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SourceFrontendAdapterError) as caught:
                self._adapter(root)._recovery_action(
                    "recovery_begin", {"document_id": "../escape"})
            self.assertEqual(400, caught.exception.error["http_status"])

    def test_discard_through_the_adapter_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            a = self._adapter(root)
            self._write(a)
            a._recovery_action("recovery_discard", {"document_id": DOC})
            self.assertEqual([], a._recovery_action(
                "recovery_list", {})["settings"]["documents"])

    def test_recovery_lands_beside_the_session_not_inside_it(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            from forge_studio.preferences import LastSessionStore

            a = self._adapter(root)
            self._write(a)
            self.assertTrue((Path(root) / "recovery" / DOC).is_dir())
            self.assertTrue(LastSessionStore(root).path.parent
                            == Path(root))


class FrontendOrderingGuards(unittest.TestCase):
    """Mutation guards for the ordering invariant.

        read session -> resolve document recovery -> establish the active
        document -> enable session/recovery writes

    Each of these fails if a step moves. The invariant is not decorative: the
    measured defect was a session write landing BEFORE recovery was
    considered, which replaced the pointer to the owner's work with a blank
    canvas about two seconds after every launch.
    """

    def test_session_writes_are_gated(self) -> None:
        self.assertIn("if (!_sessionWritesEnabled) return;", APP_JS)

    def test_recovery_resolves_after_the_session_read(self) -> None:
        session_at = APP_JS.index("const hadSession = await _loadSession();")
        resolve_at = APP_JS.index("window.StudioRecovery.resolve(")
        self.assertLess(session_at, resolve_at)

    def test_the_resolution_is_awaited(self) -> None:
        """A floating promise would let StudioDocs mint a blank document
        first, which is the defect itself."""

        self.assertIn("await window.StudioRecovery.resolve(", APP_JS)

    def test_the_document_system_waits_for_recovery(self) -> None:
        self.assertIn("R.whenResolved().then(_initWithRecovery)", DOCS_JS)

    def test_writes_are_enabled_only_after_the_document_is_established(self) -> None:
        adopt_at = DOCS_JS.index("_docs = recovered.docs;")
        enable_at = DOCS_JS.index("if (R) R.enableWrites();")
        self.assertLess(adopt_at, enable_at)
        self.assertLess(
            enable_at, DOCS_JS.index('console.log(TAG, "Document system initialized")'))

    def test_every_tab_has_its_own_identity(self) -> None:
        """Before AR4.4 all tabs reported one `document_id`, so per-document
        recovery could not tell two open documents apart."""

        self.assertIn("documentId: (C && C.newDocumentId) ? C.newDocumentId() : \"\"",
                      DOCS_JS)
        self.assertIn("doc.documentId = ident.document_id;", DOCS_JS)

    def test_reopening_a_tab_keeps_its_identity(self) -> None:
        """Minting on switch would orphan that document's recovery on disk."""

        self.assertIn("C.restoreDocumentIdentity({", DOCS_JS)

    def test_capture_hangs_off_the_existing_revision_sites(self) -> None:
        """Not a new trigger. `_bumpRevision` is already the single point
        every generation-affecting mutation passes through, so cosmetic
        changes cannot reach recovery by construction."""

        self.assertIn("onRevisionChange", CORE_JS)
        self.assertIn("C.onRevisionChange(function () { scheduleCapture(); })",
                      RECOVERY_JS)

    def test_capture_never_runs_on_the_synchronous_paint_path(self) -> None:
        """`toDataURL` and `getImageData` encode synchronously on the main
        thread. Under a brush on a large document that is a stutter, which
        would trade one defect for a worse one."""

        code = _code_only(RECOVERY_JS, python=False)
        self.assertIn("canvas.toBlob(", code)
        self.assertIn("readAsDataURL", code)
        self.assertNotIn("toDataURL", code)
        self.assertIn("requestIdleCallback", code)

    def test_only_an_explicit_close_discards(self) -> None:
        """An ordinary image export is not a project save. It must never
        clear recovery, because the owner still has unsaved layers behind
        that flattened picture."""

        self.assertIn("window.StudioRecovery.discard(closingId)", DOCS_JS)
        # The ONLY discard call site in the document system is the close path.
        self.assertEqual(1, DOCS_JS.count("StudioRecovery.discard("))

    def test_undo_history_is_deliberately_not_persisted(self) -> None:
        """A stated product fork, recorded rather than slipped in: the
        recovered document is editable and starts with an empty history."""

        self.assertIn("undoStack: [], redoStack: []", RECOVERY_JS)

    def test_a_capture_failure_is_visible(self) -> None:
        """A recovery system that fails quietly is worse than none, because
        the owner stops saving by hand on the strength of it."""

        self.assertIn("Crash recovery could not save this document", RECOVERY_JS)

    def test_recovery_goes_through_the_shared_request_helper(self) -> None:
        """One `/studio/generate` call site, so the existing guard holds."""

        self.assertIn("window.API.generate(body)", RECOVERY_JS)
        self.assertNotIn('API.post("/studio/generate"', RECOVERY_JS)


class CaptureRunsOnActionCompletionTests(unittest.TestCase):
    """Capture on the END of an action, not on a timer. AR4.6.

    THE DEFECT THIS CLOSES

    AR4.4 hung capture off `onRevisionChange` and then debounced it by 2500 ms.
    Both halves were wrong, and the second hid the first:

    `saveUndo` fires at POINTER-DOWN, because undo has to snapshot the pixels
    BEFORE they are painted over. So the revision edge sees the document as it
    was BEFORE the stroke. The debounce papered over it -- by the time the
    timer fired, the stroke had long finished, so the right thing was captured
    for the wrong reason. Removing the debounce without moving the trigger
    would have started saving pre-stroke canvases.

    And the debounce itself was the data loss. Measured in a real browser: a
    stroke, then a refresh 2.6 s later, and 40000 painted pixels were gone.

    THE OWNER'S DESIGN, WHICH IS THE RIGHT ONE

    Save on the last action -- a stroke released, a fill, a result placed on
    the canvas -- rather than after an arbitrary amount of quiet. That is the
    moment the document is coherent, and it needs no constant.

    Measured after: capture lands 65 ms after `commitStroke`, against 2637 ms
    before. A 40x smaller window, and below the time it takes a hand to leave
    a tablet and reach a key.

    WHAT REMAINS, HONESTLY

    A reload issued in the same tick as the stroke still beats it: encoding is
    asynchronous and no amount of hooking makes it synchronous. The window is
    ~65 ms rather than zero, and that is stated rather than rounded away.
    """

    def test_the_stroke_end_announces_completion(self) -> None:
        """`commitStroke` is where a brush or eraser drag actually finishes --
        every pointer-up and pointer-leave path routes through it."""

        self.assertIn("_notifyActionComplete(\"stroke\")", CORE_JS)
        commit_at = CORE_JS.index("function commitStroke()")
        notify_at = CORE_JS.index("_notifyActionComplete(\"stroke\")", commit_at)
        drawing_false = CORE_JS.index("S.drawing = false;", commit_at)
        self.assertLess(drawing_false, notify_at,
                        "the stroke must be fully committed before listeners "
                        "read the canvas")

    def test_an_instantaneous_action_announces_on_its_revision_bump(self) -> None:
        """A fill, a gradient, a delete or a result dropped on the canvas has
        no separate end -- it is finished when it bumps."""

        self.assertIn('if (!S.drawing) _notifyActionComplete("action");', CORE_JS)

    def test_a_stroke_in_progress_does_not_announce_early(self) -> None:
        """The guard on the whole design: without `!S.drawing` every
        pointer-down would capture the canvas as it was BEFORE the stroke."""

        bump_at = CORE_JS.index("function _bumpRevision()")
        body = CORE_JS[bump_at:bump_at + 1400]
        self.assertIn("if (!S.drawing)", body)

    def test_recovery_captures_on_completion_without_debouncing(self) -> None:
        start = RECOVERY_JS.index("function enableWrites()")
        body = RECOVERY_JS[start:start + 1800]
        self.assertIn("C.onActionComplete(function () { _captureNow(); })", body,
                      "the action trigger must capture immediately")

    def test_the_revision_edge_is_kept_only_as_a_safety_net(self) -> None:
        """Still subscribed -- it catches a mutation with no action boundary --
        but debounced, and the code says why it is the wrong edge."""

        start = RECOVERY_JS.index("function enableWrites()")
        body = RECOVERY_JS[start:start + 1800]
        self.assertIn("C.onRevisionChange(function () { scheduleCapture(); })", body)
        self.assertIn("POINTER-DOWN", body)

    def test_the_safety_net_is_no_longer_a_loss_window(self) -> None:
        """2500 ms was not protecting against cost -- a whole capture measured
        137 ms -- it was the reason a refresh lost a stroke."""

        at = RECOVERY_JS.index("var CAPTURE_IDLE_MS")
        value = int(RECOVERY_JS[at:at + 120].split("=")[1].split(";")[0].strip())
        self.assertLessEqual(value, 1000,
                             "the safety net has grown back into a loss window")

    def test_typing_reaches_recovery_too(self) -> None:
        """A prompt is part of the DOCUMENT -- a tab snapshot carries
        `genPanel` -- and typing has no action boundary to hook, so the signal
        that persists the session tells recovery as well."""

        self.assertIn("window.StudioRecovery.scheduleCapture();", APP_JS)
        at = APP_JS.index("function _scheduleSessionSave()")
        self.assertIn("StudioRecovery", APP_JS[at:at + 900])


class BundleExclusionTests(unittest.TestCase):
    def test_recovery_is_excluded_from_support_bundles(self) -> None:
        """Recovery holds the owner's actual artwork. It must not ship in a
        review or support bundle by default, alongside the existing rule that
        private configuration never does."""

        from forge_studio.canvas_recovery import RECOVERY_DIRNAME

        # AR8 built it, at `scripts/`. This guard was written in AR4.4 and sat
        # SKIPPED until the module existed -- which is the right way round: an
        # exclusion asserted before there is anything to exclude is a guard
        # that can never fire. It fires now.
        bundle = APP_ROOT / "scripts" / "build_support_bundle.py"
        self.assertTrue(bundle.is_file(),
                        "the support bundle module has moved; this guard is "
                        "no longer watching anything")
        self.assertIn(RECOVERY_DIRNAME, bundle.read_text(encoding="utf-8"))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_RECOVERY_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
