"""Remember Last Session is server-owned. AR4.3.

THE DEFECT THIS CLOSES

The feature stored its snapshot in `localStorage`, which is scoped to an ORIGIN
-- and the port is part of the origin. Studio takes an EPHEMERAL PORT, so every
restart handed the browser a new origin and an empty store. The feature worked
perfectly and appeared broken: what it wrote sat at an address nothing would
visit again.

Saved Defaults survived the identical restart because they were already
server-side. That contrast is the whole argument, and it is why the owner's
decision was to move the state rather than freeze the port.

The Extension has the SAME code and does not have this defect, because it runs
inside Gradio on a fixed port. So this is not a parity repair -- it is a
Studio-owned divergence forced by a different deployment model.

Review: `Evidence/source-review/AR4.3-server-owned-session.md`.

WHAT THESE TESTS REFUSE TO ACCEPT

A snapshot that cannot be understood must never stop Studio launching. Every
read failure below returns "no session" and falls back to Saved Defaults, and
one of them asserts the store is still writable afterwards -- because a
corruption path that leaves the store poisoned has only moved the failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_SESSION_TESTS = 29

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")


def store(root):
    from forge_studio.preferences import LastSessionStore

    return LastSessionStore(root)


def refusals():
    import forge_studio.preferences as prefs

    return prefs


class RoundTripTests(unittest.TestCase):
    def test_a_snapshot_survives_a_write_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.write_snapshot({"paramSteps": "31", "paramCFG": "9.5"})
            self.assertEqual({"paramSteps": "31", "paramCFG": "9.5"},
                             s.read_snapshot()["settings"])

    def test_nothing_written_reads_as_no_session(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual({}, store(root).read_snapshot())

    def test_the_revision_is_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            first = s.write_snapshot({"a": "1"})["session_revision"]
            second = s.write_snapshot({"a": "2"}, base_revision=first)
            self.assertEqual(first + 1, second["session_revision"])

    def test_it_lands_beside_defaults_not_inside_them(self) -> None:
        """Catches: an afternoon of experiments quietly rewriting the
        baseline the owner deliberately saved."""

        from forge_studio.preferences import DefaultsStore

        with tempfile.TemporaryDirectory() as root:
            defaults, session = DefaultsStore(root), store(root)
            self.assertEqual(defaults.path.parent, session.path.parent)
            self.assertNotEqual(defaults.path, session.path)
            self.assertEqual("last-session.json", session.path.name)

    def test_the_document_identity_travels(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            written = store(root).write_snapshot(
                {"a": "1"}, document_id="b" * 32, canvas_revision=7)
            self.assertEqual("b" * 32, written["document_id"])
            self.assertEqual(7, written["canvas_revision"])


class FallbackTests(unittest.TestCase):
    """Every one of these must leave Studio able to launch."""

    def _corrupt(self, root, text):
        (Path(root) / "last-session.json").write_text(text, encoding="utf-8")

    def test_a_future_schema_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._corrupt(root, json.dumps({
                "schema": "studio-last-session", "schema_version": 99,
                "settings": {"a": 1}}))
            self.assertEqual({}, store(root).read_snapshot())

    def test_a_foreign_schema_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._corrupt(root, json.dumps({
                "schema": "something-else", "schema_version": 1,
                "settings": {}}))
            self.assertEqual({}, store(root).read_snapshot())

    def test_a_missing_settings_object_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._corrupt(root, json.dumps({
                "schema": "studio-last-session", "schema_version": 1}))
            self.assertEqual({}, store(root).read_snapshot())

    def test_invalid_json_falls_back_and_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._corrupt(root, "{not json at all")
            self.assertEqual({}, store(root).read_snapshot())
            self.assertTrue(
                (Path(root) / "last-session.json.unreadable").is_file(),
                "the old bytes must be kept, not silently destroyed")

    def test_the_store_still_works_after_corruption(self) -> None:
        """Catches: a corruption path that leaves the store poisoned, which
        has only moved the failure rather than recovered from it."""

        with tempfile.TemporaryDirectory() as root:
            self._corrupt(root, "{not json at all")
            s = store(root)
            s.read_snapshot()
            self.assertEqual(1, s.write_snapshot({"a": "1"})["session_revision"])
            self.assertEqual({"a": "1"}, s.read_snapshot()["settings"])


class StaleWriteTests(unittest.TestCase):
    def test_a_stale_revision_is_refused_by_name(self) -> None:
        """Two views, or one that slept. The older must not win by arriving
        last."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.write_snapshot({"a": "1"})
            s.write_snapshot({"a": "2"}, base_revision=1)
            with self.assertRaises(refusals().SessionRevisionStale):
                s.write_snapshot({"a": "3"}, base_revision=1)

    def test_a_write_without_a_base_revision_is_allowed(self) -> None:
        """A first write, and the migration path, have no revision to cite."""

        with tempfile.TemporaryDirectory() as root:
            s = store(root)
            s.write_snapshot({"a": "1"})
            self.assertEqual(2, s.write_snapshot({"a": "2"})["session_revision"])

    def test_a_non_integer_revision_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for bad in ("1", 1.5, True):
                with self.subTest(base_revision=bad):
                    with self.assertRaises(refusals().MalformedDocument):
                        store(root).write_snapshot({"a": "1"}, base_revision=bad)


class DurableIdentityTests(unittest.TestCase):
    def test_an_ephemeral_handle_cannot_be_durable_identity(self) -> None:
        """Catches: a snapshot that looks restorable and resolves to nothing
        next launch, because the registry it points into died with the
        process."""

        with tempfile.TemporaryDirectory() as root:
            for bad in ("studio-asset/" + "a" * 32,
                        "studio-result/" + "b" * 32 + ".png"):
                with self.subTest(handle=bad):
                    with self.assertRaises(refusals().SessionIdentityNotDurable):
                        store(root).write_snapshot({"a": "1"}, document_id=bad)

    def test_a_path_cannot_be_durable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for bad in ("C:/Users/x/doc.png", "/home/x/doc", "..\\\\x"):
                with self.subTest(path=bad):
                    with self.assertRaises(refusals().SessionIdentityNotDurable):
                        store(root).write_snapshot({"a": "1"}, document_id=bad)

    def test_no_response_carries_a_filesystem_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            written = store(root).write_snapshot({"a": "1"})
            self.assertNotIn("path", json.dumps(written).lower())
            self.assertNotIn(root.replace("\\\\", "/").lower(),
                             json.dumps(written).lower())


class AdapterActionTests(unittest.TestCase):
    """The three operations the page actually calls."""

    def _adapter(self, root):
        from forge_studio.preferences import DefaultsStore, LastSessionStore
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        adapter = SourceFrontendAdapter.__new__(SourceFrontendAdapter)
        adapter._defaults = DefaultsStore(root)
        adapter._session = LastSessionStore(root)
        return adapter

    def test_save_then_load_returns_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            a = self._adapter(root)
            a._defaults_action("save_session", {"session_data": {"s": "31"}})
            loaded = a._defaults_action("load_session", {})["settings"]
            self.assertTrue(loaded["session_present"])
            self.assertEqual({"s": "31"}, loaded["session"])

    def test_load_with_nothing_stored_is_an_explicit_absence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            loaded = self._adapter(root)._defaults_action("load_session", {})["settings"]
            self.assertFalse(loaded["session_present"])
            self.assertEqual({}, loaded["session"])

    def test_delete_removes_the_snapshot(self) -> None:
        """Disabling must not merely stop restoring: a file left behind comes
        back the moment the feature is re-enabled."""

        with tempfile.TemporaryDirectory() as root:
            a = self._adapter(root)
            a._defaults_action("save_session", {"session_data": {"s": "31"}})
            a._defaults_action("delete_session", {})
            self.assertFalse(a._session.path.is_file())
            self.assertFalse(
                a._defaults_action("load_session", {})["settings"]["session_present"])

    def test_a_non_object_session_is_refused(self) -> None:
        from forge_studio.source_api_adapter import SourceFrontendRequestError

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SourceFrontendRequestError):
                self._adapter(root)._defaults_action(
                    "save_session", {"session_data": "not an object"})

    def test_defaults_are_untouched_by_session_writes(self) -> None:
        """Save Defaults is the baseline; the session is an overlay. One must
        never rewrite the other."""

        with tempfile.TemporaryDirectory() as root:
            a = self._adapter(root)
            a._defaults_action("save_defaults", {"defaults_data": {"s": "30"}})
            a._defaults_action("save_session", {"session_data": {"s": "18"}})
            self.assertEqual(
                {"s": "30"},
                a._defaults_action("load_defaults", {})["settings"])


class FrontendOwnershipGuards(unittest.TestCase):
    """Mutation guards: these fail if the server stops being canonical."""

    def test_the_session_is_read_from_the_server(self) -> None:
        self.assertIn('action: "load_session"', APP_JS)

    def test_the_session_is_written_to_the_server(self) -> None:
        self.assertIn('action: "save_session"', APP_JS)

    def test_disabling_deletes_server_side(self) -> None:
        self.assertIn('action: "delete_session"', APP_JS)

    def test_localstorage_is_no_longer_the_owner(self) -> None:
        """Catches the whole defect returning. The legacy key may only be READ
        (for one-time migration) or REMOVED -- never written as the store."""

        self.assertNotIn('localStorage.setItem("studio-session-data"', APP_JS)

    def test_the_session_load_is_awaited_before_the_ui_settles(self) -> None:
        """A floating promise would let the page settle on Saved Defaults and
        repaint from the session a moment later -- the flicker-then-change the
        deterministic order exists to prevent."""

        self.assertIn("const hadSession = await _loadSession();", APP_JS)

    def test_defaults_are_loaded_before_the_session_overlay(self) -> None:
        defaults_at = APP_JS.index("const hadDefaults = await loadDefaults();")
        session_at = APP_JS.index("const hadSession = await _loadSession();")
        self.assertLess(defaults_at, session_at)

    def test_a_stale_write_is_detected_rather_than_ignored(self) -> None:
        self.assertIn("base_revision: _sessionRevision", APP_JS)
        self.assertIn("409", APP_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_SESSION_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
