"""D1 stage 2 -- the owner's settings survive the process that took them.

Stage 1 resolved a state root and wired it into `launch.py`. Nothing read or
wrote it, and the evidence said so. This suite covers the first thing that
does: two JSON documents on that root, replacing the process-local dictionaries
the frontend adapter kept.

What was actually broken, in owner terms: custom shortcuts, per-checkpoint text
encoder and VAE memory, the session limit, panel layout, folder settings and
saved generation defaults were accepted, answered with the stored document, and
discarded at exit. Nothing reported a failure, because nothing had failed --
the write went exactly where it was told to go.

Three kinds of test here, and the middle one is the point:

* the store on its own -- merge semantics, refusals, corruption, locking;
* the store WIRED -- that the real server hands its documents to the real
  adapter, and that the real launcher builds them on the resolved root. A
  module passing its own tests proves nothing about being called, and this
  codebase has now recorded four occasions where something was written,
  exported, and never reached;
* durability across a real process boundary, in subprocesses, because "it
  survives a restart" is the claim and re-reading a file in the same
  interpreter does not test it.

Migration of legacy `localStorage` values is stage 3 and is deliberately absent
here. `test_nothing_is_created_before_the_first_write` is the one that keeps
stage 3 possible: an absent preferences file is the only remaining signal that
tells a never-migrated install from one that migrated to nothing.

No network, no model, no GPU. The subprocesses talk to a temporary directory.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.preferences import (  # noqa: E402
    DEFAULTS_FILENAME,
    MAX_DOCUMENT_BYTES,
    PREFERENCE_KEYS,
    PREFERENCES_FILENAME,
    DefaultsStore,
    DocumentTooLarge,
    DocumentUnreadable,
    MalformedDocument,
    PreferenceStore,
    UnknownPreferenceKeys,
)
from forge_studio.source_api_adapter import (  # noqa: E402
    SourceFrontendAdapter,
    SourceFrontendAdapterError,
)

EXPECTED_PREFERENCE_TESTS = 46

FRONTEND_ROOT = APP_ROOT / "forge_studio" / "frontend"
LAUNCH_SOURCE = APP_ROOT / "forge_studio" / "launch.py"


def _well_shaped(key: str):
    """A value each preference key will actually accept.

    `PREFERENCE_SHAPES` pins what every recognised key may hold, so fixtures
    can no longer use the key name as a stand-in value.
    """
    from forge_studio.preferences import PREFERENCE_SHAPES

    shape = PREFERENCE_SHAPES[key]
    if isinstance(shape, tuple) and shape and isinstance(shape[0], str):
        return shape[0]
    if shape is dict:
        return {"fixture": key}
    if shape is bool:
        return True
    if shape is str:
        return key
    return 12


class _Presentation:
    """The smallest object the adapter and the server will accept.

    Deliberately not the mock backend: nothing here generates, and a real
    presentation would start threads this suite would then have to stop.
    """

    def runtime_status(self) -> dict[str, object]:
        return {}


def _adapter(root: Path | None) -> SourceFrontendAdapter:
    return SourceFrontendAdapter(
        _Presentation(),
        preferences=PreferenceStore(root),
        defaults=DefaultsStore(root),
    )


class _RootedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.root = Path(self._directory.name) / "ForgeStudio"
        self.addCleanup(self._directory.cleanup)

    @property
    def document(self) -> Path:
        return self.root / PREFERENCES_FILENAME

    def _stored(self) -> dict[str, object]:
        return json.loads(self.document.read_text(encoding="utf-8"))


class StoredPreferenceTests(_RootedTest):
    def test_nothing_is_created_before_the_first_write(self) -> None:
        """The invariant stage 3 depends on, asserted where it can regress.

        A store that made its directory at construction -- the obvious,
        tidy-looking thing to do -- would destroy the only signal left that
        distinguishes an install which has never been migrated from one that
        migrated and found nothing. `prefs.js` sets its migration marker after
        the first successful POST, so on every existing install that marker is
        already set against a store that never persisted a byte.
        """

        store = PreferenceStore(self.root)
        self.assertEqual({}, store.read())
        self.assertFalse(self.root.exists())

    def test_a_write_creates_the_document_under_the_state_root(self) -> None:
        store = PreferenceStore(self.root)
        store.merge({"layout_preset": "classic"})
        self.assertTrue(self.document.is_file())
        self.assertEqual({"layout_preset": "classic"}, self._stored())

    def test_a_write_returns_the_whole_document(self) -> None:
        """The contract `prefs.js` is written against: POST answers with the
        merged state, not with an acknowledgement."""

        store = PreferenceStore(self.root)
        store.merge({"layout_preset": "classic"})
        self.assertEqual(
            {"layout_preset": "classic", "session_limit": 15},
            store.merge({"session_limit": 15}),
        )

    def test_unposted_keys_survive_a_write(self) -> None:
        store = PreferenceStore(self.root)
        store.merge({"layout_preset": "classic", "save_dir": "D:/pictures"})
        store.merge({"session_limit": 30})
        self.assertEqual("D:/pictures", store.read()["save_dir"])

    def test_a_posted_key_replaces_that_key_whole(self) -> None:
        """Shallow by top-level key, and it has to be.

        The page owns complete objects -- `component_memory` and `shortcuts`
        are posted entire -- so a deep merge would restore an entry the owner
        had just deleted, and the deletion would look like it had worked until
        the next restart.
        """

        store = PreferenceStore(self.root)
        store.merge({"component_memory": {"by_model": {"a": {"te": "x"}}}})
        store.merge({"component_memory": {"by_model": {}}})
        self.assertEqual({"by_model": {}}, store.read()["component_memory"])

    def test_values_survive_a_new_store_on_the_same_root(self) -> None:
        PreferenceStore(self.root).merge({"session_limit": 42})
        self.assertEqual({"session_limit": 42}, PreferenceStore(self.root).read())

    def test_clear_removes_the_document(self) -> None:
        store = PreferenceStore(self.root)
        store.merge({"session_limit": 42})
        store.clear()
        self.assertFalse(self.document.exists())
        self.assertEqual({}, store.read())

    def test_clear_is_not_an_error_when_nothing_was_written(self) -> None:
        """`?reset` runs before anything has ever been saved, routinely."""

        PreferenceStore(self.root).clear()

    def test_a_write_leaves_no_temporary_file_behind(self) -> None:
        store = PreferenceStore(self.root)
        store.merge({"session_limit": 42})
        store.merge({"layout_preset": "classic"})
        self.assertEqual(
            [PREFERENCES_FILENAME],
            sorted(path.name for path in self.root.iterdir()),
        )


class RefusalTests(_RootedTest):
    def test_an_unknown_key_is_refused(self) -> None:
        with self.assertRaises(UnknownPreferenceKeys):
            PreferenceStore(self.root).merge({"not_a_preference": 1})

    def test_the_refusal_names_the_keys(self) -> None:
        """So the message is something a person can act on."""

        with self.assertRaises(UnknownPreferenceKeys) as raised:
            PreferenceStore(self.root).merge({"beta": 1, "alpha": 2})
        self.assertEqual(("alpha", "beta"), raised.exception.keys)
        self.assertIn("alpha", str(raised.exception))

    def test_a_refused_write_changes_nothing(self) -> None:
        """The discriminating half. Refusing the request but keeping the half
        of it that was recognised would answer an error over a partial save --
        and the recognised half would be on disk, contradicting the refusal.
        """

        store = PreferenceStore(self.root)
        store.merge({"session_limit": 15})
        with self.assertRaises(UnknownPreferenceKeys):
            store.merge({"layout_preset": "compact", "smuggled": True})
        self.assertEqual({"session_limit": 15}, store.read())

    def test_a_document_over_the_ceiling_is_refused(self) -> None:
        store = PreferenceStore(self.root)
        with self.assertRaises(DocumentTooLarge):
            store.merge({"save_dir": "x" * (MAX_DOCUMENT_BYTES + 1)})
        self.assertFalse(self.document.exists())

    def test_the_ceiling_counts_the_whole_document_not_one_write(self) -> None:
        """A per-request cap -- what the reference implementation applies --
        bounds one POST and lets the stored document grow without limit across
        many small ones. What is worth bounding is what ends up on the disk.
        """

        store = PreferenceStore(self.root)
        half = "x" * (MAX_DOCUMENT_BYTES // 2)
        store.merge({"save_dir": half})
        with self.assertRaises(DocumentTooLarge):
            store.merge({"gallery_folder": half})
        self.assertEqual({"save_dir": half}, store.read())

    def test_a_value_json_cannot_carry_is_refused(self) -> None:
        with self.assertRaises(MalformedDocument):
            PreferenceStore(self.root).merge({"education": {1, 2}})
        self.assertFalse(self.document.exists())

    def test_a_document_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaises(MalformedDocument):
            PreferenceStore(self.root).merge([("session_limit", 1)])


class DamagedDocumentTests(_RootedTest):
    def test_a_corrupt_document_reads_empty_and_the_bytes_are_kept(self) -> None:
        """Corrupt is not the same as absent, and the difference is the
        owner's data. Reading it as empty is the only way forward, so the old
        bytes move aside BEFORE the next write replaces them.
        """

        self.root.mkdir(parents=True)
        self.document.write_text("{not json", encoding="utf-8")
        store = PreferenceStore(self.root)
        with self.assertLogs("studio.preferences", level="WARNING"):
            self.assertEqual({}, store.read())
        kept = self.root / (PREFERENCES_FILENAME + ".unreadable")
        self.assertEqual("{not json", kept.read_text(encoding="utf-8"))

    def test_a_json_array_is_treated_as_corrupt(self) -> None:
        """A valid JSON document of the wrong shape is still not a document
        this store can merge into."""

        self.root.mkdir(parents=True)
        self.document.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertLogs("studio.preferences", level="WARNING"):
            self.assertEqual({}, PreferenceStore(self.root).read())

    def test_an_unreadable_document_refuses_rather_than_reporting_empty(
        self,
    ) -> None:
        """The dangerous failure, and the reason it is not folded in with
        corruption. If a permission fault reported `{}`, the page would be
        told the owner has no preferences, and the next `set()` would write one
        key over everything they had.
        """

        store = PreferenceStore(self.root)
        store.merge({"session_limit": 15})
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("denied")):
            with self.assertRaises(DocumentUnreadable):
                store.read()
        self.assertEqual({"session_limit": 15}, store.read())


class ConcurrencyTests(_RootedTest):
    def test_concurrent_writes_do_not_lose_keys(self) -> None:
        """Two browser tabs posting different keys is the ordinary case.

        An atomic replace alone does not prevent this: both writers would read
        the same document, add their own key, and the second replace would
        drop the first key entirely. The lock covers the whole read-merge-write
        transaction, which is what makes the outcome complete rather than
        merely uncorrupted.
        """

        store = PreferenceStore(self.root)
        keys = sorted(PREFERENCE_KEYS)
        # One well-shaped value per key. The fixture used to write the key's
        # own NAME as its value, which no longer passes: recognised keys are
        # now shape-checked, and most of them are not strings. The concurrency
        # this proves is unchanged -- one distinct write per thread.
        expected = {key: _well_shaped(key) for key in keys}
        barrier = Barrier(len(keys))

        def write(key: str) -> None:
            barrier.wait()
            store.merge({key: expected[key]})

        threads = [Thread(target=write, args=(key,)) for key in keys]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(expected, store.read())


class MemoryDocumentTests(unittest.TestCase):
    """A host without a state root gets the SAME contract, not a lenient one.

    Two implementations that drift is how this project acquired a frontend
    whose backend was missing. The mock server must refuse what the real server
    refuses, or the browser-truth run proves something the product does not do.
    """

    def test_a_memory_store_is_not_durable_and_has_no_path(self) -> None:
        store = PreferenceStore()
        self.assertFalse(store.durable)
        self.assertIsNone(store.path)

    def test_a_memory_store_enforces_the_same_allow_list(self) -> None:
        with self.assertRaises(UnknownPreferenceKeys):
            PreferenceStore().merge({"not_a_preference": 1})

    def test_a_memory_store_enforces_the_same_ceiling(self) -> None:
        with self.assertRaises(DocumentTooLarge):
            PreferenceStore().merge({"save_dir": "x" * (MAX_DOCUMENT_BYTES + 1)})

    def test_a_memory_store_still_merges_and_clears(self) -> None:
        store = PreferenceStore()
        store.merge({"session_limit": 15})
        self.assertEqual({"session_limit": 15}, store.merge({"session_limit": 15}))
        store.clear()
        self.assertEqual({}, store.read())


class AllowListTests(unittest.TestCase):
    def test_the_allow_list_is_exactly_what_the_frontend_uses(self) -> None:
        """Derived, not declared twice.

        A key added to the page and forgotten here would be refused at runtime
        with a 400 the owner would experience as "that setting will not save".
        A key removed from the page and left here is a permission nothing
        needs. Both directions are asserted.

        Comments are stripped first. This codebase has tripped that trap eight
        times: `index.html` discusses `Prefs` in prose, and a raw scan would
        collect key names out of the explanation.
        """

        found: set[str] = set()
        pattern = re.compile(
            r'Prefs\??\.(?:get|set|has)\(\s*"([A-Za-z_][A-Za-z0-9_]*)"'
        )
        sources = sorted(FRONTEND_ROOT.rglob("*.js")) + sorted(
            FRONTEND_ROOT.rglob("*.html")
        )
        for path in sources:
            code = re.sub(
                r"//.*$", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE
            )
            found.update(pattern.findall(code))

        self.assertEqual(
            set(PREFERENCE_KEYS),
            found,
            "the allow-list and the frontend's preference keys have drifted",
        )

    def test_the_migration_table_can_only_post_allow_listed_keys(self) -> None:
        """The second thing that POSTs, and the one stage 3 depends on.

        `prefs.js` does not only send keys that went through `Prefs.set()`. Its
        legacy table posts under the `server:` name of each entry, in ONE
        request. Since an unrecognised key refuses the whole write, a single
        entry outside the allow-list would fail the entire migration -- and
        `_migrateLegacyIfNeeded` treats a failed POST as "retry next boot", so
        it would fail identically forever, silently, on every launch.
        """

        source = re.sub(
            r"//.*$",
            "",
            (FRONTEND_ROOT / "prefs.js").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        migrated = set(
            re.findall(r'server:\s*"([A-Za-z_][A-Za-z0-9_]*)"', source)
        )
        self.assertTrue(migrated, "the legacy table was not found to check")
        self.assertEqual(set(), migrated - set(PREFERENCE_KEYS))

    def test_saved_defaults_are_not_allow_listed(self) -> None:
        """Deliberate. That document holds whatever generation parameters the
        page saved, so enumerating them would mean a new control silently stops
        being saveable. The size ceiling is the bound that matters for
        something written from a browser request, and it still applies.
        """

        store = DefaultsStore()
        self.assertEqual(
            {"an_unnamed_future_parameter": 1},
            store.replace({"an_unnamed_future_parameter": 1}),
        )
        with self.assertRaises(DocumentTooLarge):
            store.replace({"steps": "x" * (MAX_DOCUMENT_BYTES + 1)})


class SeparateDocumentTests(_RootedTest):
    def test_defaults_are_kept_beside_preferences_not_inside_them(self) -> None:
        PreferenceStore(self.root).merge({"session_limit": 15})
        DefaultsStore(self.root).replace({"steps": 30})
        self.assertEqual(
            [DEFAULTS_FILENAME, PREFERENCES_FILENAME],
            sorted(path.name for path in self.root.iterdir()),
        )

    def test_clearing_preferences_leaves_saved_defaults_alone(self) -> None:
        """The whole reason they are two documents.

        `?reset` DELETEs `/studio/prefs`. If defaults were a fourteenth
        preference key, one emergency button would take the owner's saved
        generation settings with it -- and an emergency reset is exactly when
        someone is least able to afford a second, unannounced loss.
        """

        preferences = PreferenceStore(self.root)
        defaults = DefaultsStore(self.root)
        preferences.merge({"session_limit": 15})
        defaults.replace({"steps": 30, "cfg_scale": 5.0})
        preferences.clear()
        self.assertEqual({}, preferences.read())
        self.assertEqual({"steps": 30, "cfg_scale": 5.0}, defaults.read())


class AdapterContractTests(_RootedTest):
    def test_the_prefs_route_contract_is_unchanged(self) -> None:
        """Byte-for-byte the shapes `prefs.js` already expects. The frontend is
        an immutable consumer, and this stage moves storage, not the API."""

        adapter = _adapter(self.root)
        self.assertEqual({}, adapter.get("/studio/prefs"))
        self.assertEqual(
            {"layout_preset": "classic", "session_limit": 15},
            adapter.post(
                "/studio/prefs",
                {"layout_preset": "classic", "session_limit": 15},
            ),
        )
        self.assertEqual(
            {"layout_preset": "classic", "session_limit": 15},
            adapter.get("/studio/prefs"),
        )
        self.assertEqual({"ok": True}, adapter.delete("/studio/prefs"))
        self.assertEqual({}, adapter.get("/studio/prefs"))

    def test_an_adapter_with_no_store_still_answers(self) -> None:
        """Every existing caller constructs one this way."""

        adapter = SourceFrontendAdapter(_Presentation())
        self.assertEqual({}, adapter.get("/studio/prefs"))
        self.assertEqual(
            {"save_dir": "D:/pictures"},
            adapter.post("/studio/prefs", {"save_dir": "D:/pictures"}),
        )

    def test_preferences_outlive_the_adapter_that_wrote_them(self) -> None:
        _adapter(self.root).post("/studio/prefs", {"session_limit": 42})
        self.assertEqual(
            {"session_limit": 42}, _adapter(self.root).get("/studio/prefs")
        )

    def test_saved_defaults_outlive_the_adapter_that_wrote_them(self) -> None:
        """`Settings -> Defaults`, which reset on every launch."""

        saved = _adapter(self.root).post(
            "/studio/generate",
            {"action": "save_defaults", "defaults_data": {"steps": 30}},
        )
        self.assertTrue(saved["settings"]["defaults_saved"])
        loaded = _adapter(self.root).post(
            "/studio/generate", {"action": "load_defaults"}
        )
        self.assertEqual({"steps": 30}, loaded["settings"])

    def test_saving_defaults_replaces_rather_than_merges(self) -> None:
        """Settings posts the complete document, so a parameter the owner
        removed must not come back."""

        adapter = _adapter(self.root)
        adapter.post(
            "/studio/generate",
            {"action": "save_defaults", "defaults_data": {"steps": 30, "hr": True}},
        )
        adapter.post(
            "/studio/generate",
            {"action": "save_defaults", "defaults_data": {"steps": 30}},
        )
        loaded = adapter.post("/studio/generate", {"action": "load_defaults"})
        self.assertEqual({"steps": 30}, loaded["settings"])

    def test_deleting_defaults_empties_them(self) -> None:
        adapter = _adapter(self.root)
        adapter.post(
            "/studio/generate",
            {"action": "save_defaults", "defaults_data": {"steps": 30}},
        )
        deleted = adapter.post("/studio/generate", {"action": "delete_defaults"})
        self.assertTrue(deleted["settings"]["defaults_deleted"])
        self.assertEqual(
            {},
            _adapter(self.root).post(
                "/studio/generate", {"action": "load_defaults"}
            )["settings"],
        )

    def test_an_unknown_key_reaches_the_caller_as_a_bad_request(self) -> None:
        with self.assertRaises(SourceFrontendAdapterError) as raised:
            _adapter(self.root).post("/studio/prefs", {"smuggled": True})
        self.assertEqual(400, raised.exception.error["http_status"])
        self.assertIn("smuggled", raised.exception.error["message"])

    def test_an_oversized_write_reaches_the_caller_as_too_large(self) -> None:
        with self.assertRaises(SourceFrontendAdapterError) as raised:
            _adapter(self.root).post(
                "/studio/prefs", {"save_dir": "x" * (MAX_DOCUMENT_BYTES + 1)}
            )
        self.assertEqual(413, raised.exception.error["http_status"])

    def test_a_storage_fault_is_reported_as_a_fault(self) -> None:
        """Not a 400. An unreadable file is Studio's problem, and telling the
        caller their request was bad sends them looking for a mistake they did
        not make.
        """

        adapter = _adapter(self.root)
        adapter.post("/studio/prefs", {"session_limit": 15})
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("denied")):
            with self.assertRaises(SourceFrontendAdapterError) as raised:
                adapter.get("/studio/prefs")
        self.assertEqual(500, raised.exception.error["http_status"])


class WiringTests(_RootedTest):
    """Written, exported, and never called is this codebase's signature bug.

    It has happened with a chooser, twice more with other seams, and once
    already inside D1 -- stage 1's first attempt patched `launch.py` with a
    `str.replace` whose target did not match, failed silently, and left a
    module that passed all of its own tests and was reached by nothing.
    """

    def test_the_server_hands_its_documents_to_the_adapter(self) -> None:
        """The real server constructor, not a stand-in for it.

        `server_bind` and `server_activate` are suppressed so no socket is
        listened on: this suite runs under a guard that refuses `listen()`, and
        the wiring under test happens before either would be called anyway.
        """

        from forge_studio.presentation import _StudioHTTPServer

        preferences = PreferenceStore(self.root)
        defaults = DefaultsStore(self.root)
        with mock.patch.object(_StudioHTTPServer, "server_bind"), mock.patch.object(
            _StudioHTTPServer, "server_activate"
        ):
            server = _StudioHTTPServer(
                ("127.0.0.1", 0),
                _Presentation(),
                preferences=preferences,
                defaults=defaults,
            )
        try:
            self.assertIs(preferences, server.source_adapter._preferences)
            self.assertIs(defaults, server.source_adapter._defaults)
        finally:
            server.socket.close()

    def test_a_server_given_no_documents_still_builds_an_adapter(self) -> None:
        """The demo path and the browser-truth harness construct it this way."""

        from forge_studio.presentation import _StudioHTTPServer

        with mock.patch.object(_StudioHTTPServer, "server_bind"), mock.patch.object(
            _StudioHTTPServer, "server_activate"
        ):
            server = _StudioHTTPServer(("127.0.0.1", 0), _Presentation())
        try:
            self.assertFalse(server.source_adapter._preferences.durable)
        finally:
            server.socket.close()

    def _launch_calls(self, name: str) -> list[ast.Call]:
        tree = ast.parse(LAUNCH_SOURCE.read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]

    def test_the_launcher_builds_the_documents_on_the_resolved_root(self) -> None:
        """Asserted through the syntax tree rather than a text search. A
        comment mentioning `studio_state_root` would satisfy a raw `assertIn`
        while the call itself passed nothing -- which is the same class of
        false pass that let stage 1's silent patch failure through.
        """

        for name in ("PreferenceStore", "DefaultsStore"):
            with self.subTest(store=name):
                calls = self._launch_calls(name)
                self.assertEqual(1, len(calls))
                self.assertIn("studio_state_root", ast.unparse(calls[0]))

    def test_the_launcher_hands_the_documents_to_the_server(self) -> None:
        calls = self._launch_calls("_StudioHTTPServer")
        self.assertEqual(1, len(calls))
        keywords = {keyword.arg for keyword in calls[0].keywords}
        self.assertIn("preferences", keywords)
        self.assertIn("defaults", keywords)


class SeparateProcessTests(_RootedTest):
    """The claim is that settings survive a restart. Re-reading a file in the
    interpreter that wrote it does not test a restart.

    These children run with `-I -S -B`, so they see no site-packages and no
    cached bytecode -- which also proves the store needs nothing beyond the
    standard library to work.
    """

    def _child(self, body: str) -> str:
        source = (
            f"import sys\nsys.path.insert(0, {str(APP_ROOT)!r})\n"
            f"from forge_studio.preferences import DefaultsStore, PreferenceStore\n"
            f"ROOT = {str(self.root)!r}\n"
        ) + body
        finished = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        self.assertEqual(
            0,
            finished.returncode,
            finished.stderr.decode("utf-8", "replace"),
        )
        return finished.stdout.decode("utf-8").strip()

    def test_a_second_process_reads_what_the_first_wrote(self) -> None:
        self._child(
            "PreferenceStore(ROOT).merge({'session_limit': 42, "
            "'layout_preset': 'compact'})\n"
            "DefaultsStore(ROOT).replace({'steps': 30})\n"
        )
        self.assertEqual(
            '{"layout_preset": "compact", "session_limit": 42}|{"steps": 30}',
            self._child(
                "import json\n"
                "print(json.dumps(PreferenceStore(ROOT).read(), sort_keys=True)"
                " + '|' + json.dumps(DefaultsStore(ROOT).read(), sort_keys=True))\n"
            ),
        )

    def test_a_second_process_sees_a_document_that_was_cleared(self) -> None:
        """A reset has to survive the restart too, or the settings the owner
        cleared come back."""

        self._child("PreferenceStore(ROOT).merge({'session_limit': 42})\n")
        self._child("PreferenceStore(ROOT).clear()\n")
        self.assertEqual(
            "{}",
            self._child(
                "import json\n"
                "print(json.dumps(PreferenceStore(ROOT).read()))\n"
            ),
        )

    def test_a_second_process_starts_from_empty_when_the_first_wrote_nothing(
        self,
    ) -> None:
        """The discriminating case: a test that only ever reads back its own
        write passes just as well against a store that ignores its argument
        and answers from a module-level dictionary."""

        self.assertEqual(
            "{}|False",
            self._child(
                "import json\n"
                "store = PreferenceStore(ROOT)\n"
                "print(json.dumps(store.read()) + '|' + str(store.path.exists()))\n"
            ),
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PREFERENCE_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
