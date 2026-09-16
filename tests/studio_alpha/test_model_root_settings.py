"""Durable model-root configuration and the one privileged write.

The settings route is the only place in Studio where the browser supplies a
filesystem path. These tests are mostly negatives, because that is where the
value is: the route must refuse a cross-origin POST, a missing Origin, a wrong
or absent token, a GET attempting mutation, and a non-JSON body.

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
from http import HTTPStatus
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.model_roots import (  # noqa: E402
    ModelRootRegistry,
    normalize_roots,
    write_config_atomically,
)
from forge_studio.model_root_settings import (  # noqa: E402
    CSRF_HEADER,
    ModelRootSettings,
)

WORKSPACE_ROOT = APP_ROOT.parent


def _make_root(prefix: str, names: tuple[str, ...]) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    for name in names:
        (root / name).write_bytes(b"")
    return root


#: Asserted against the discovered count so a silently dropped test fails.
#: Added ahead of P0.4, which churns this suite hardest. The convention exists
#: because 68 tests once vanished from a suite without anything going red
#: (see test_headless_model_plumbing.py).
EXPECTED_TESTS = 47


class _SettingsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.checkpoints = _make_root("mrs-ckpt-", ("Alpha.safetensors",))
        self.encoders = _make_root("mrs-te-", ("Enc.safetensors", "Enc2.pt"))
        self.vaes = _make_root("mrs-vae-", ("Dec.vae.safetensors",))
        for root in (self.checkpoints, self.encoders, self.vaes):
            self.addCleanup(shutil.rmtree, root, True)
        self.config_dir = Path(tempfile.mkdtemp(prefix="mrs-cfg-")).resolve()
        self.addCleanup(shutil.rmtree, self.config_dir, True)
        self.config_path = self.config_dir / "studio-config.json"
        self.config_path.write_text(
            json.dumps({"backend": "mock", "port": 0, "result_root": "Results"}),
            encoding="utf-8",
        )
        self.registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
        self.settings = ModelRootSettings(
            self.registry, config_path=self.config_path
        )

    def all_roots(self) -> dict[str, str]:
        return {
            "checkpoint": str(self.checkpoints),
            "text_encoder": str(self.encoders),
            "vae": str(self.vaes),
        }


# --------------------------------------------------------------------------
# 1. Configuration is a transaction
# --------------------------------------------------------------------------


class TransactionTests(_SettingsCase):
    def test_every_named_role_applies_together(self) -> None:
        # Named, not "all three". P0.8 adds the `adetailer` root, which this
        # save does not mention -- and a role with no configured root reports
        # `not_configured`, which is the truthful answer rather than a broken
        # transaction. The claim under test is that the roles the save DID
        # name all land, atomically.
        named = self.all_roots()
        self.settings.apply({"model_roots": named})
        statuses = self.registry.describe()
        self.assertEqual(
            {role: statuses[role].status for role in named},
            {role: "ready" for role in named},
        )
        for role, status in statuses.items():
            if role not in named:
                with self.subTest(unnamed=role):
                    self.assertEqual("not_configured", status.status)
        self.assertEqual(statuses["text_encoder"].entry_count, 2)

    def test_one_bad_root_changes_nothing(self) -> None:
        self.settings.apply({"model_roots": self.all_roots()})
        before = {r: s.entry_count for r, s in self.registry.describe().items()}
        broken = self.all_roots()
        broken["vae"] = r"\\server\share\vae"
        with self.assertRaises(HeadlessError) as caught:
            self.settings.apply({"model_roots": broken})
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION"
        )
        after = {r: s.entry_count for r, s in self.registry.describe().items()}
        self.assertEqual(before, after, "a refused write mutated the registry")

    def test_a_refused_write_does_not_touch_the_config_file(self) -> None:
        original = self.config_path.read_text(encoding="utf-8")
        broken = dict(self.all_roots())
        broken["checkpoint"] = str(self.checkpoints / "Alpha.safetensors")
        with self.assertRaises(HeadlessError):
            self.settings.apply({"model_roots": broken})
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), original)

    def test_startup_is_tolerant_where_the_write_is_not(self) -> None:
        roots = self.all_roots()
        roots["vae"] = str(self.config_dir / "gone")
        statuses = self.registry.configure_tolerantly(roots)
        self.assertEqual(statuses["checkpoint"].status, "ready")
        self.assertEqual(statuses["vae"].status, "refused")
        self.assertEqual(
            statuses["vae"].reason_code, "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE"
        )
        # The same input through the privileged write refuses outright.
        with self.assertRaises(HeadlessError):
            self.settings.apply({"model_roots": roots})


# --------------------------------------------------------------------------
# 2. Persistence
# --------------------------------------------------------------------------


class PersistenceTests(_SettingsCase):
    def test_roots_survive_into_the_config_file(self) -> None:
        result = self.settings.apply({"model_roots": self.all_roots()})
        self.assertTrue(result["persisted"])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["model_roots"], self.all_roots())

    def test_unrelated_configuration_is_preserved(self) -> None:
        self.settings.apply({"model_roots": self.all_roots()})
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["backend"], "mock")
        self.assertEqual(saved["result_root"], "Results")

    def test_an_unreadable_config_is_not_silently_replaced(self) -> None:
        self.config_path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(HeadlessError) as caught:
            self.settings.apply({"model_roots": self.all_roots()})
        self.assertEqual(
            caught.exception.code, "STUDIO_SETTINGS_CONFIG_UNREADABLE"
        )
        # The live registry was still updated: the app works, the file is stale.
        self.assertEqual(self.registry.describe()["checkpoint"].status, "ready")

    def test_the_write_leaves_no_partial_file(self) -> None:
        self.settings.apply({"model_roots": self.all_roots()})
        names = sorted(p.name for p in self.config_dir.iterdir())
        self.assertEqual(names, ["studio-config.json"])

    def test_atomic_write_replaces_rather_than_truncates(self) -> None:
        target = self.config_dir / "atomic.json"
        write_config_atomically(target, {"first": True})
        write_config_atomically(target, {"second": True})
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8")), {"second": True}
        )
        self.assertEqual(
            sorted(p.name for p in self.config_dir.iterdir()),
            ["atomic.json", "studio-config.json"],
        )


# --------------------------------------------------------------------------
# 3. Shape validation
# --------------------------------------------------------------------------


class ShapeTests(_SettingsCase):
    def test_unknown_roles_are_named_not_ignored(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            # NOT "lora" -- a real role since AR8.1.
            normalize_roots({"not_a_role": "C:/x"})
        self.assertEqual(
            caught.exception.code, "HEADLESS_MODEL_ROOTS_UNKNOWN_ROLE"
        )

    def test_blank_and_absent_mean_not_configured(self) -> None:
        self.assertEqual(normalize_roots({"checkpoint": "   "}), {})
        self.assertEqual(normalize_roots({"checkpoint": None}), {})
        self.assertEqual(normalize_roots(None), {})

    def test_non_string_root_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            normalize_roots({"checkpoint": 17})
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_ROOTS_MALFORMED")

    def test_a_null_byte_is_refused_before_the_filesystem(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            self.settings.apply({"model_roots": {"checkpoint": "C:/x\x00y"}})
        self.assertEqual(caught.exception.code, "STUDIO_SETTINGS_ROOT_MALFORMED")

    def test_an_absurdly_long_root_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            self.settings.apply({"model_roots": {"checkpoint": "C:/" + "a" * 9000}})
        self.assertEqual(caught.exception.code, "STUDIO_SETTINGS_ROOT_TOO_LONG")

    def test_a_non_object_payload_is_refused(self) -> None:
        for payload in ([], "roots", 3, None):
            with self.assertRaises(HeadlessError):
                self.settings.apply(payload)


# --------------------------------------------------------------------------
# 4. Token
# --------------------------------------------------------------------------


class TokenTests(_SettingsCase):
    def test_token_is_per_process_and_not_persisted(self) -> None:
        other = ModelRootSettings(
            ModelRootRegistry(workspace_root=WORKSPACE_ROOT),
            config_path=self.config_path,
        )
        self.assertNotEqual(self.settings.token, other.token)
        self.settings.apply({"model_roots": self.all_roots()})
        saved = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn(self.settings.token, saved)

    def test_token_comparison_rejects_prefixes_and_blanks(self) -> None:
        token = self.settings.token
        self.assertTrue(self.settings.token_matches(token))
        self.assertFalse(self.settings.token_matches(token[:-1]))
        self.assertFalse(self.settings.token_matches(token + "x"))
        self.assertFalse(self.settings.token_matches(""))
        self.assertFalse(self.settings.token_matches(None))

    def test_token_is_long_enough_to_be_unguessable(self) -> None:
        self.assertGreaterEqual(len(self.settings.token), 32)


# --------------------------------------------------------------------------
# 5. The route, over a real loopback server
# --------------------------------------------------------------------------


class _StubPresentation:
    """Enough surface for the handler to construct. Never exercised here."""

    lifecycle_available = False

    def backend_status(self) -> dict[str, object]:
        return {"state": "ready"}


class _KeptBytesIO(io.BytesIO):
    """A response buffer that survives the handler closing it."""

    def close(self) -> None:  # noqa: D102
        pass


class _FakeConnection:
    """Stands in for a socket, entirely in memory.

    The canonical runner forbids ``listen``, ``connect`` and ``urlopen``, so
    these tests drive the real request handler over fake file objects instead of
    binding a port. That is not a workaround -- the guarantee being protected is
    that the contract suite serves nothing and talks to nothing, and the
    security properties under test are in the handler, not in the transport.
    """

    def __init__(self, request_bytes: bytes) -> None:
        self._request = io.BytesIO(request_bytes)
        self.response = _KeptBytesIO()

    def makefile(self, mode: str = "rb", *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        return self._request if "r" in mode else self.response

    def close(self) -> None:
        pass


class _RouteCase(_SettingsCase):
    """The in-memory request harness. Cases live in the classes below it.

    Split out so a second route can reuse the transport without inheriting
    the first route's assertions -- a subclass of a TestCase re-runs every
    test it inherits, which reads as coverage and is not.
    """

    PORT = 45123

    def setUp(self) -> None:
        super().setUp()
        from forge_studio.presentation import _StudioHTTPServer
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        # Constructed without __init__, which would bind a real socket. The
        # handler asserts isinstance, so a plain stub object will not do.
        self.server = object.__new__(_StudioHTTPServer)
        self.server.server_address = ("127.0.0.1", self.PORT)  # type: ignore[attr-defined]
        self.server.presentation = _StubPresentation()  # type: ignore[attr-defined]
        self.server.model_root_settings = self.settings  # type: ignore[attr-defined]
        self.server.source_adapter = SourceFrontendAdapter(  # type: ignore[attr-defined]
            self.server.presentation, model_roots=self.registry  # type: ignore[attr-defined]
        )
        self.origin = f"http://127.0.0.1:{self.PORT}"

    def _exchange(self, raw: bytes) -> tuple[int, dict[str, object], dict[str, str]]:
        from forge_studio.presentation import _StudioRequestHandler

        connection = _FakeConnection(raw)
        handler = object.__new__(_StudioRequestHandler)
        handler.server = self.server  # type: ignore[assignment]
        handler.client_address = ("127.0.0.1", 54321)  # type: ignore[attr-defined]
        handler.connection = connection  # type: ignore[attr-defined]
        handler.request = connection  # type: ignore[attr-defined]
        handler.rfile = connection.makefile("rb")  # type: ignore[attr-defined]
        handler.wfile = connection.response  # type: ignore[attr-defined]
        handler.close_connection = True  # type: ignore[attr-defined]
        handler.handle_one_request()

        raw_response = connection.response.getvalue()
        head, _, body = raw_response.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ")[1])
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        return status, payload, headers

    def _request_bytes(
        self,
        method: str,
        *,
        body: bytes = b"",
        origin: str | None = "same",
        token: str | None = "valid",
        content_type: str = "application/json",
        path: str = "/studio/settings/model_roots",
    ) -> bytes:
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: 127.0.0.1:{self.PORT}",
        ]
        if origin == "same":
            lines.append(f"Origin: {self.origin}")
        elif origin is not None:
            lines.append(f"Origin: {origin}")
        if token == "valid":
            lines.append(f"{CSRF_HEADER}: {self.settings.token}")
        elif token is not None:
            lines.append(f"{CSRF_HEADER}: {token}")
        if body:
            lines.append(f"Content-Type: {content_type}")
            lines.append(f"Content-Length: {len(body)}")
        lines.append("Connection: close")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

    def post(
        self,
        body: dict[str, object],
        *,
        origin: str | None = "same",
        token: str | None = "valid",
        content_type: str = "application/json",
        path: str = "/studio/settings/model_roots",
    ) -> tuple[int, dict[str, object]]:
        raw = self._request_bytes(
            "POST",
            body=json.dumps(body).encode("utf-8"),
            origin=origin,
            token=token,
            content_type=content_type,
            path=path,
        )
        status, payload, _ = self._exchange(raw)
        return status, payload


class SettingsRouteTests(_RouteCase):
    def test_a_correct_request_applies_and_persists(self) -> None:
        status, body = self.post({"model_roots": self.all_roots()})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["persisted"])
        self.assertEqual(self.registry.describe()["checkpoint"].status, "ready")

    def test_a_cross_origin_post_is_refused(self) -> None:
        status, _ = self.post(
            {"model_roots": self.all_roots()}, origin="http://evil.example"
        )
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(
            self.registry.describe()["checkpoint"].status, "not_configured"
        )

    def test_a_missing_origin_is_refused_on_this_route(self) -> None:
        # Deliberately stricter than the general POST rule, which tolerates a
        # missing Origin for same-origin and non-browser callers.
        status, _ = self.post({"model_roots": self.all_roots()}, origin=None)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_missing_token_is_refused(self) -> None:
        status, _ = self.post({"model_roots": self.all_roots()}, token=None)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_wrong_token_is_refused(self) -> None:
        status, _ = self.post({"model_roots": self.all_roots()}, token="nope")
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_form_content_type_is_refused(self) -> None:
        # The shape a simple cross-origin form POST would take.
        status, _ = self.post(
            {"model_roots": self.all_roots()},
            content_type="application/x-www-form-urlencoded",
        )
        self.assertNotEqual(status, HTTPStatus.OK)

    def test_get_cannot_mutate(self) -> None:
        status, body, _ = self._exchange(self._request_bytes("GET"))
        self.assertEqual(status, HTTPStatus.OK)
        # A read, and only a read.
        self.assertEqual(
            self.registry.describe()["checkpoint"].status, "not_configured"
        )
        self.assertIn("roles", body)

    def test_no_permissive_cors_header_is_ever_sent(self) -> None:
        _, _, headers = self._exchange(self._request_bytes("GET"))
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotIn("access-control-allow-credentials", headers)

    def test_an_untrusted_host_header_is_refused(self) -> None:
        raw = (
            "POST /studio/settings/model_roots HTTP/1.1\r\n"
            "Host: evil.example\r\n"
            f"Origin: {self.origin}\r\n"
            f"{CSRF_HEADER}: {self.settings.token}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        status, _, _ = self._exchange(raw)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_the_refused_paths_never_reach_the_filesystem(self) -> None:
        # A refused request must not leave the config file altered either.
        original = self.config_path.read_text(encoding="utf-8")
        self.post({"model_roots": self.all_roots()}, token="nope")
        self.post({"model_roots": self.all_roots()}, origin="http://evil.example")
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), original)


# --------------------------------------------------------------------------
# 6. Load routing
# --------------------------------------------------------------------------


class SelectionRouteTests(_RouteCase):
    """`POST /studio/settings/model_selection` -- remembering the dropdowns.

    It carries no path and triggers no load, so it is not privileged in the
    way the roots write is. It is gated identically anyway: it still writes
    the owner's configuration file, and two different answers to "may this
    page change the config" is one answer too many.
    """

    ROUTE = "/studio/settings/model_selection"
    ID = "0123456789abcdef0123456789abcdef"

    def remember(self, selection, **kwargs):  # type: ignore[no-untyped-def]
        return self.post(
            {"last_model_selection": selection}, path=self.ROUTE, **kwargs
        )

    def test_a_correct_request_remembers_and_persists(self) -> None:
        status, body = self.remember({"checkpoint": self.ID})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(body["persisted"])
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual({"checkpoint": self.ID}, saved["last_model_selection"])

    def test_a_cross_origin_post_is_refused(self) -> None:
        status, _ = self.remember(
            {"checkpoint": self.ID}, origin="http://evil.example"
        )
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertNotIn(
            "last_model_selection",
            json.loads(self.config_path.read_text(encoding="utf-8")),
        )

    def test_a_missing_origin_is_refused_on_this_route(self) -> None:
        status, _ = self.remember({"checkpoint": self.ID}, origin=None)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_missing_token_is_refused(self) -> None:
        status, _ = self.remember({"checkpoint": self.ID}, token=None)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_wrong_token_is_refused(self) -> None:
        status, _ = self.remember({"checkpoint": self.ID}, token="nope")
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_a_path_is_refused_rather_than_stored(self) -> None:
        """The one thing this route must never become is a second way to put
        a filesystem path into the config file -- that is the whole reason
        the roots route carries the controls it does."""

        status, body = self.remember({"checkpoint": "Z:/private/x.safetensors"})
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual("STUDIO_SETTINGS_MALFORMED", body["code"])
        self.assertNotIn(
            "Z:/private", self.config_path.read_text(encoding="utf-8")
        )

    def test_remembering_loads_nothing(self) -> None:
        """The route answers OK without the lifecycle existing at all. A
        preference that needed a lifecycle would be an instruction."""

        self.assertFalse(hasattr(self.server.presentation, "model_lifecycle"))
        status, _ = self.remember({"checkpoint": self.ID})
        self.assertEqual(status, HTTPStatus.OK)

    def test_the_remembered_value_is_readable_back_with_the_token(self) -> None:
        self.remember({"checkpoint": self.ID})
        raw = self._request_bytes("GET", path="/studio/settings/model_roots")
        status, payload, _ = self._exchange(raw)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual({"checkpoint": self.ID}, payload["last_model_selection"])


class KeepAliveDrainTests(_RouteCase):
    """A refused POST must not poison the NEXT request on the connection.

    Found in a live browser, invisible to every route test that came before
    it, because each of those sent exactly one request per connection. A
    refusal that answers without reading the body leaves those bytes in the
    socket; the next request is then parsed starting inside them, and the
    failure lands on whatever the page asks for next. The symptom was a
    perfectly correct 404 on a retired route followed by
    `501 Unsupported method ('{}GET')` on an unrelated GET.
    """

    def _pipelined(self, first: bytes, second: bytes):  # type: ignore[no-untyped-def]
        """Two requests down one connection, the way a browser sends them."""

        from forge_studio.presentation import _StudioRequestHandler

        connection = _FakeConnection(first + second)
        handler = object.__new__(_StudioRequestHandler)
        handler.server = self.server  # type: ignore[assignment]
        handler.client_address = ("127.0.0.1", 54321)  # type: ignore[attr-defined]
        handler.connection = connection  # type: ignore[attr-defined]
        handler.request = connection  # type: ignore[attr-defined]
        handler.rfile = connection.makefile("rb")  # type: ignore[attr-defined]
        handler.wfile = connection.response  # type: ignore[attr-defined]
        handler.close_connection = False  # type: ignore[attr-defined]
        handler.handle_one_request()
        handler.close_connection = True  # type: ignore[attr-defined]
        handler.handle_one_request()
        # Scanned rather than split on line starts: a JSON body carries no
        # trailing CRLF, so the second response's status line begins in the
        # middle of the first response's last line.
        raw = connection.response.getvalue().decode("latin-1")
        return [int(code) for code in re.findall(r"HTTP/1\.1 (\d{3}) ", raw)]

    def test_a_retired_route_does_not_break_the_next_request(self) -> None:
        retired = self._request_bytes(
            "POST",
            body=json.dumps({"profile_id": "alpha"}).encode("utf-8"),
            path="/api/profiles/select",
        )
        follow = self._request_bytes("GET", path="/api/status")
        self.assertEqual(
            [HTTPStatus.NOT_FOUND, HTTPStatus.OK], self._pipelined(retired, follow)
        )

    def test_a_token_refusal_does_not_break_the_next_request(self) -> None:
        refused = self._request_bytes(
            "POST",
            body=json.dumps({"model_roots": self.all_roots()}).encode("utf-8"),
            token="nope",
        )
        follow = self._request_bytes("GET", path="/api/status")
        self.assertEqual(
            [HTTPStatus.FORBIDDEN, HTTPStatus.OK], self._pipelined(refused, follow)
        )

    def test_a_cross_origin_refusal_does_not_break_the_next_request(self) -> None:
        refused = self._request_bytes(
            "POST",
            body=json.dumps({"model_roots": self.all_roots()}).encode("utf-8"),
            origin="http://evil.example",
        )
        follow = self._request_bytes("GET", path="/api/status")
        self.assertEqual(
            [HTTPStatus.FORBIDDEN, HTTPStatus.OK], self._pipelined(refused, follow)
        )

    def test_an_accepted_write_does_not_over_read_the_next_request(self) -> None:
        """The other direction: a handler that DID read its body must not have
        the drain read again, or it eats the following request instead."""

        accepted = self._request_bytes(
            "POST",
            body=json.dumps({"model_roots": self.all_roots()}).encode("utf-8"),
        )
        follow = self._request_bytes("GET", path="/api/status")
        self.assertEqual(
            [HTTPStatus.OK, HTTPStatus.OK], self._pipelined(accepted, follow)
        )


class LoadRoutingTests(_SettingsCase):
    def _select(self) -> dict[str, str]:
        self.registry.configure(self.all_roots())
        return {
            role: self.registry.entries(role)[0].model_id
            for role in ("checkpoint", "text_encoder", "vae")
        }

    def test_ids_become_the_existing_payload_references(self) -> None:
        references = self.registry.build_payload_references(self._select())
        self.assertEqual(
            sorted(references), ["checkpoint", "text_encoder", "vae"]
        )
        for role, reference in references.items():
            self.assertTrue(Path(reference).is_file(), role)
            self.assertTrue(Path(reference).is_absolute(), role)

    def test_the_result_feeds_the_existing_profile_contract(self) -> None:
        from forge_studio.model_profiles import ModelProfile

        profile = ModelProfile(
            profile_id="catalogue-selection",
            display_name="Catalogue selection",
            family="test",
            payload_references=self.registry.build_payload_references(
                self._select()
            ),
        )
        described = profile.describe()
        self.assertTrue(described["complete"])
        # The profile projection must not carry the resolved paths.
        rendered = json.dumps(described)
        for root in (self.checkpoints, self.encoders, self.vaes):
            self.assertNotIn(str(root), rendered)

    def test_a_cross_role_id_is_refused_at_the_load_seam(self) -> None:
        selection = self._select()
        selection["vae"] = selection["checkpoint"]
        with self.assertRaises(HeadlessError) as caught:
            self.registry.build_payload_references(selection)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_an_unconfigured_role_refuses_rather_than_guessing(self) -> None:
        self.registry.configure({"checkpoint": str(self.checkpoints)})
        entry = self.registry.entries("checkpoint")[0]
        with self.assertRaises(HeadlessError) as caught:
            self.registry.build_payload_references({"vae": entry.model_id})
        self.assertEqual(
            caught.exception.code, "HEADLESS_MODEL_ROOT_NOT_CONFIGURED"
        )

    def test_a_deleted_file_is_caught_at_resolve_not_at_load(self) -> None:
        selection = self._select()
        (self.checkpoints / "Alpha.safetensors").unlink()
        with self.assertRaises(HeadlessError):
            self.registry.build_payload_references(selection)

    def test_an_empty_id_is_named_not_silently_skipped(self) -> None:
        self.registry.configure(self.all_roots())
        with self.assertRaises(HeadlessError) as caught:
            self.registry.build_payload_references({"checkpoint": "  "})
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_ID_REQUIRED")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
