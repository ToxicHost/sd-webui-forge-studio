"""Owner-facing Model Folders surface, and the selector states behind it.

Two kinds of test live here.

The **surface** tests read the shipped `index.html` and `app.js` as committed
files. They prove the section exists natively, is not a floating panel, reuses
the one protected write route, and never writes a filesystem path into browser
state used for generation or load.

The **payload** tests drive the real adapter and the real request handler, so
the states an owner sees -- unconfigured, empty, unavailable, truncated -- are
produced by the same code that will produce them at runtime.

Synthetic fixtures only. No real model payload is created, stat'd or opened.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.model_roots import ModelRootRegistry  # noqa: E402
from forge_studio.model_root_settings import (  # noqa: E402
    CSRF_HEADER,
    ModelRootSettings,
)
from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402

WORKSPACE_ROOT = APP_ROOT.parent
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
LOCALE_EN = json.loads(
    (FRONTEND / "locales" / "en.json").read_text(encoding="utf-8")
)

ROLE_FIELDS = {
    "checkpoint": "modelRootCheckpoint",
    "text_encoder": "modelRootTextEncoder",
    "vae": "modelRootVae",
}


# --------------------------------------------------------------------------
# 1. The native surface
# --------------------------------------------------------------------------


#: Asserted against the discovered count so a silently dropped test fails.
#: Added ahead of P0.4, which churns this suite hardest. The convention exists
#: because 68 tests once vanished from a suite without anything going red
#: (see test_headless_model_plumbing.py).
EXPECTED_TESTS = 26


class SettingsSurfaceTests(unittest.TestCase):
    def test_the_section_lives_in_the_settings_page(self) -> None:
        self.assertIn('data-settings-section="model-folders"', INDEX_HTML)
        # Inside the real Settings page, not appended somewhere else.
        settings_start = INDEX_HTML.index('id="app-settings"')
        self.assertGreater(
            INDEX_HTML.index('data-settings-section="model-folders"'),
            settings_start,
        )

    def test_all_three_role_fields_render(self) -> None:
        for field_id in ROLE_FIELDS.values():
            self.assertIn(f'id="{field_id}"', INDEX_HTML)

    def test_each_role_has_a_status_line_and_a_browse_button(self) -> None:
        for suffix in ("Checkpoint", "TextEncoder", "Vae"):
            self.assertIn(f'id="modelRootStatus{suffix}"', INDEX_HTML)
            self.assertIn(f'id="modelRootBrowse{suffix}"', INDEX_HTML)

    def test_there_is_exactly_one_save_control(self) -> None:
        self.assertEqual(INDEX_HTML.count('id="modelFoldersSave"'), 1)

    def test_no_floating_panel_is_introduced(self) -> None:
        card = INDEX_HTML[
            INDEX_HTML.index('data-settings-section="model-folders"') :
        ]
        card = card[: card.index("</section>")]
        for forbidden in ("position:fixed", "position: fixed", "role=\"dialog\""):
            self.assertNotIn(forbidden, card)

    def test_the_fields_are_editable_by_hand(self) -> None:
        # Section 7: the picker opens on the machine running Studio, so typing
        # a path must remain possible for remote and VM setups.
        card = INDEX_HTML[
            INDEX_HTML.index('data-settings-section="model-folders"') :
        ]
        card = card[: card.index("</section>")]
        # One per role with a configurable ROOT, which P0.8 makes four: the
        # Auto Detail detector folder joined `MODEL_ROLES`. Derived rather
        # than counted, so the next role to gain a root does not need this
        # line edited -- and so the claim stays "every root is typeable"
        # rather than "there are three of them".
        from forge_headless.catalogue import MODEL_ROLES

        self.assertEqual(card.count('type="text"'), len(MODEL_ROLES))
        self.assertNotIn("readonly", card)
        self.assertNotIn("disabled", card)

    def test_every_i18n_key_used_by_the_section_exists(self) -> None:
        card = INDEX_HTML[
            INDEX_HTML.index('data-settings-section="model-folders"') :
        ]
        card = card[: card.index("</section>")]
        keys = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', card))
        missing = sorted(key for key in keys if key not in LOCALE_EN)
        self.assertEqual(missing, [], f"untranslated keys: {missing}")


# --------------------------------------------------------------------------
# 2. What the browser code is allowed to do
# --------------------------------------------------------------------------


class BrowserBehaviourTests(unittest.TestCase):
    def _model_folders_block(self) -> str:
        """The model-folders section, bounded by markers rather than by count.

        This used to be `start : start + 9000`, which overran the section by 63
        characters and pulled `galleryFolderTrust` into every assertion below.
        A character count cannot tell whether it is still inside the code it
        claims to be describing; two markers can.
        """

        start = APP_JS.index("// ---- Model folders ---")
        end = APP_JS.index("// ---- end model folders ----", start)
        return APP_JS[start:end]

    def test_save_posts_only_to_the_protected_route(self) -> None:
        """Stated as an allowlist plus a presence check, not an exact set.

        The exact-set form failed for the wrong reasons: any POST added
        anywhere in the window broke it, including one that was never part of
        this section. What must hold is that the save goes to the protected
        route and that nothing here reaches an UNSANCTIONED one.
        """

        block = self._model_folders_block()
        posts = set(
            re.findall(r'fetch\(API\.base \+ "([^"]+)"[^)]*method: "POST"', block)
        )
        self.assertIn("/studio/settings/model_roots", posts)
        allowed = {
            # The privileged write. The reason this test exists.
            "/studio/settings/model_roots",
            # The legacy server-side folder dialog. Retired by the P0.4
            # directory picker, which lives outside this section entirely.
            "/studio/gallery/pick-folder",
            # The P0.4 picker's own routes, if a future edit moves a call in
            # here. Read-only, and gated by the same token as the write above.
            "/studio/fs/capabilities",
            "/studio/fs/places",
            "/studio/fs/resolve",
            "/studio/fs/list",
        }
        self.assertEqual(set(), posts - allowed, "unsanctioned POST in this section")

    def test_save_sends_the_process_token(self) -> None:
        self.assertIn(CSRF_HEADER, self._model_folders_block())

    def test_browse_fills_a_field_and_does_not_save(self) -> None:
        block = self._model_folders_block()
        start = block.index("// Browse fills the field")
        browse = block[start : block.index('getElementById("modelFoldersSave")?', start)]
        self.assertIn("input.value", browse)
        self.assertNotIn("/studio/settings/model_roots", browse)

    def test_no_autosave_on_keystroke(self) -> None:
        block = self._model_folders_block()
        self.assertNotIn('addEventListener("input"', block)
        self.assertNotIn('addEventListener("keyup"', block)

    def test_the_browser_does_not_validate_the_filesystem(self) -> None:
        block = self._model_folders_block()
        for forbidden in ("existsSync", "statSync", "showDirectoryPicker"):
            self.assertNotIn(forbidden, block)

    def test_a_root_change_refreshes_the_selectors(self) -> None:
        self.assertIn("window.populateDropdowns", self._model_folders_block())

    def test_selectors_read_ids_not_titles(self) -> None:
        # The defect this guards: the checkpoint dropdown once read m.title
        # unconditionally, so every catalogued model rendered as "undefined".
        self.assertIn("m.model_id || m.title || m.name", APP_JS)
        self.assertNotIn("`<option value=\"${m.title}\">", APP_JS)

    def test_selector_states_are_distinct(self) -> None:
        for key in (
            "selector.unconfigured",
            "selector.unavailable",
            "selector.empty",
            "selector.truncated",
        ):
            self.assertIn(key, APP_JS)
            self.assertIn(key, LOCALE_EN)

    def test_raw_backend_codes_are_mapped_to_owner_wording(self) -> None:
        block = self._model_folders_block()
        # The code appears as a lookup key, and never as displayed text.
        self.assertIn("HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION", block)
        self.assertNotIn("textContent = entry.reason_code", block)
        self.assertNotIn("+ entry.reason_code", block)


# --------------------------------------------------------------------------
# 3. The states an owner actually sees
# --------------------------------------------------------------------------


class _StubPresentation:
    lifecycle_available = False

    def models(self) -> dict[str, object]:
        return {"models": []}


class RoleStatusPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="mfux-")).resolve()
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
        self.adapter = SourceFrontendAdapter(
            _StubPresentation(), model_roots=self.registry
        )

    def _root(self, name: str, *files: str) -> Path:
        root = self.temp / name
        root.mkdir()
        for entry in files:
            (root / entry).write_bytes(b"")
        return root

    def test_unconfigured_is_distinguishable_from_empty(self) -> None:
        empty = self._root("empty-vae")
        self.registry.configure({"vae": str(empty)})
        roles = {r["role"]: r for r in self.adapter.model_roots()["roles"]}
        self.assertEqual(roles["vae"]["status"], "ready")
        self.assertEqual(roles["vae"]["entry_count"], 0)
        self.assertEqual(roles["checkpoint"]["status"], "not_configured")

    def test_an_unavailable_root_is_reported_after_tolerant_startup(self) -> None:
        good = self._root("ckpt", "Alpha.safetensors")
        self.registry.configure_tolerantly(
            {"checkpoint": str(good), "vae": str(self.temp / "gone")}
        )
        roles = {r["role"]: r for r in self.adapter.model_roots()["roles"]}
        self.assertEqual(roles["checkpoint"]["status"], "ready")
        self.assertEqual(roles["vae"]["status"], "refused")
        self.assertEqual(
            roles["vae"]["reason_code"], "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE"
        )

    def test_truncation_is_carried_to_the_browser(self) -> None:
        import forge_headless.catalogue as catalogue_module

        crowded = self._root("many", *[f"m{i}.safetensors" for i in range(9)], "Aa.safetensors")
        original = catalogue_module.MAX_CATALOGUE_ENTRIES
        catalogue_module.MAX_CATALOGUE_ENTRIES = 4
        try:
            self.registry.configure({"checkpoint": str(crowded)})
            payload = self.adapter.get("/studio/models")
            roles = {r["role"]: r for r in self.adapter.model_roots()["roles"]}
        finally:
            catalogue_module.MAX_CATALOGUE_ENTRIES = original
        self.assertTrue(roles["checkpoint"]["truncated"])
        self.assertTrue(all(entry["truncated"] for entry in payload))

    def test_no_role_status_carries_a_path(self) -> None:
        root = self._root("ckpt2", "Alpha.safetensors")
        self.registry.configure({"checkpoint": str(root)})
        rendered = json.dumps(self.adapter.model_roots())
        self.assertNotIn(str(root), rendered)
        self.assertNotIn(str(self.temp), rendered)

    def test_the_model_list_payload_is_renderable_without_title(self) -> None:
        # Guards the same defect from the payload side: a catalogued entry must
        # carry something the dropdown can show and something it can submit.
        root = self._root("ckpt3", "RealVis.safetensors")
        self.registry.configure({"checkpoint": str(root)})
        entries = self.adapter.get("/studio/models")
        self.assertTrue(entries)
        for entry in entries:
            self.assertTrue(entry.get("model_id"))
            self.assertTrue(entry.get("name"))
            self.assertNotIn("relative_location", entry)
            self.assertNotIn(str(root), json.dumps(entry))


# --------------------------------------------------------------------------
# 4. Save transaction, through the real handler
# --------------------------------------------------------------------------


class _KeptBytesIO(io.BytesIO):
    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, request_bytes: bytes) -> None:
        self._request = io.BytesIO(request_bytes)
        self.response = _KeptBytesIO()

    def makefile(self, mode: str = "rb", *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        return self._request if "r" in mode else self.response

    def close(self) -> None:
        pass


class SaveTransactionTests(unittest.TestCase):
    PORT = 45211

    def setUp(self) -> None:
        from forge_studio.presentation import _StudioHTTPServer

        self.temp = Path(tempfile.mkdtemp(prefix="mfux-save-")).resolve()
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.good = self.temp / "ckpt"
        self.good.mkdir()
        (self.good / "Alpha.safetensors").write_bytes(b"")
        self.config = self.temp / "studio-config.json"
        self.config.write_text(json.dumps({"backend": "mock"}), encoding="utf-8")

        self.registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
        self.settings = ModelRootSettings(self.registry, config_path=self.config)
        self.server = object.__new__(_StudioHTTPServer)
        self.server.server_address = ("127.0.0.1", self.PORT)  # type: ignore[attr-defined]
        self.server.presentation = _StubPresentation()  # type: ignore[attr-defined]
        self.server.model_root_settings = self.settings  # type: ignore[attr-defined]
        self.server.source_adapter = SourceFrontendAdapter(  # type: ignore[attr-defined]
            self.server.presentation, model_roots=self.registry  # type: ignore[attr-defined]
        )

    def _post(self, roots: dict[str, str]) -> tuple[int, dict[str, object]]:
        from forge_studio.presentation import _StudioRequestHandler

        body = json.dumps({"model_roots": roots}).encode("utf-8")
        raw = (
            "POST /studio/settings/model_roots HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.PORT}\r\n"
            f"Origin: http://127.0.0.1:{self.PORT}\r\n"
            f"{CSRF_HEADER}: {self.settings.token}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1") + body
        connection = _FakeConnection(raw)
        handler = object.__new__(_StudioRequestHandler)
        handler.server = self.server  # type: ignore[assignment]
        handler.client_address = ("127.0.0.1", 5000)  # type: ignore[attr-defined]
        handler.connection = connection  # type: ignore[attr-defined]
        handler.request = connection  # type: ignore[attr-defined]
        handler.rfile = connection.makefile("rb")  # type: ignore[attr-defined]
        handler.wfile = connection.response  # type: ignore[attr-defined]
        handler.close_connection = True  # type: ignore[attr-defined]
        handler.handle_one_request()
        head, _, payload = connection.response.getvalue().partition(b"\r\n\r\n")
        status = int(head.decode("latin-1").split("\r\n")[0].split(" ")[1])
        return status, json.loads(payload.decode("utf-8") or "{}")

    def test_a_valid_save_applies_and_reports_every_role(self) -> None:
        status, body = self._post({"checkpoint": str(self.good)})
        self.assertEqual(status, 200)
        roles = {entry["role"]: entry for entry in body["roles"]}
        self.assertEqual(roles["checkpoint"]["status"], "ready")
        # Every role with a configurable root, which P0.8 makes four: the
        # detector root joined `MODEL_ROLES`. Derived rather than counted, so
        # the next role to gain a root does not need this line edited again.
        from forge_headless.catalogue import MODEL_ROLES

        self.assertEqual(set(MODEL_ROLES), set(roles))

    def test_one_invalid_root_changes_nothing_and_names_a_code(self) -> None:
        self._post({"checkpoint": str(self.good)})
        before = json.loads(self.config.read_text(encoding="utf-8"))
        status, body = self._post(
            {"checkpoint": str(self.good), "vae": r"\\server\share\vae"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION")
        # No path in the message the owner is shown.
        self.assertNotIn("server", body["error"])
        self.assertEqual(
            json.loads(self.config.read_text(encoding="utf-8")), before
        )
        self.assertEqual(
            self.registry.describe()["checkpoint"].status, "ready"
        )

    def test_a_successful_save_replaces_the_snapshot(self) -> None:
        self._post({"checkpoint": str(self.good)})
        first = [e.model_id for e in self.registry.entries("checkpoint")]
        other = self.temp / "ckpt2"
        other.mkdir()
        (other / "Beta.safetensors").write_bytes(b"")
        self._post({"checkpoint": str(other)})
        second = [e.model_id for e in self.registry.entries("checkpoint")]
        self.assertTrue(second)
        self.assertFalse(set(first) & set(second), "stale ids survived a root change")

    def test_a_stale_id_stops_resolving_after_a_root_change(self) -> None:
        self._post({"checkpoint": str(self.good)})
        stale = self.registry.entries("checkpoint")[0].model_id
        other = self.temp / "ckpt3"
        other.mkdir()
        (other / "Gamma.safetensors").write_bytes(b"")
        self._post({"checkpoint": str(other)})
        with self.assertRaises(HeadlessError) as caught:
            self.registry.resolve("checkpoint", stale)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
