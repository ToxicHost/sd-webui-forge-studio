"""Runtime-boundary tests for the source-faithful Studio frontend.

The canonical HTML/CSS/JavaScript remains an immutable consumer. These tests
exercise the owned static server and mock API adapter without constructing a
socket. They never contact a network or import Forge, Torch, CUDA, or Gradio.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hashlib
from http import HTTPStatus
import importlib
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep
from types import MethodType
from typing import Any
import unittest


APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = APP_ROOT / "forge_studio" / "frontend"


def _presentation(*, interval: float = 0.001) -> Any:
    studio = importlib.import_module("forge_studio")
    presentation = importlib.import_module("forge_studio.presentation")
    return presentation.StudioPresentation(
        studio.StudioApplication(
            studio.MockBackend(event_interval_seconds=interval)
        ),
        studio.GenerationRequest,
    )


class SourceFrontendAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        self.adapter_module = adapter_module
        self.presentation = _presentation()
        self.adapter = adapter_module.SourceFrontendAdapter(self.presentation)

    def tearDown(self) -> None:
        self.presentation.shutdown()

    def _load_first_model(self) -> dict[str, str]:
        model = self.adapter.get("/studio/models")[0]
        loaded = self.adapter.post(
            "/studio/load_model",
            {"title": model["title"]},
        )
        self.assertTrue(loaded["ok"])
        return model

    def test_boot_resources_use_canonical_shapes_and_honest_mock_values(
        self,
    ) -> None:
        models = self.adapter.get("/studio/models")
        self.assertGreaterEqual(len(models), 1)
        self.assertEqual(
            {"title", "name", "hash", "filename"},
            set(models[0]),
        )
        self.assertEqual("", self.adapter.get("/studio/current_model")["title"])
        fresh_status = self.adapter.get("/studio/model_status")
        self.assertFalse(fresh_status["loaded"])
        self.assertEqual("", fresh_status["title"])
        self.assertTrue(fresh_status["is_mock"])
        self.assertEqual(models, self.adapter.get("/studio/models"))
        self.assertFalse(self.adapter.get("/studio/model_status")["loaded"])
        self._load_first_model()
        loaded_status = self.adapter.get("/studio/model_status")
        self.assertTrue(loaded_status["loaded"])
        self.assertEqual(models[0]["title"], loaded_status["title"])

        for path in (
            "/studio/samplers",
            "/studio/schedulers",
            "/studio/upscalers",
            "/studio/vaes",
            "/studio/text_encoders",
            "/studio/ad_models",
            "/studio/cn_models",
            "/studio/cn_preprocessors",
            "/studio/loras",
            "/studio/embeddings",
            "/studio/wildcards",
            "/studio/extensions",
            "/studio/workflows",
            "/studio/layouts",
        ):
            with self.subTest(path=path):
                self.assertIsInstance(self.adapter.get(path), list)

        vram = self.adapter.get("/studio/vram")
        self.assertFalse(vram["available"])
        self.assertEqual("", vram["gpu_name"])
        self.assertEqual(
            {"sd_model_checkpoint"},
            set(self.adapter.get("/sdapi/v1/options")),
        )

    def test_preferences_and_defaults_round_trip_without_a_state_root(
        self,
    ) -> None:
        """This adapter is built with no documents, so its preferences are
        memory-backed and process-local -- which is the FALLBACK, not the
        product. D1 stage 2 gave the served adapter durable documents on the
        resolved state root; `test_preferences.py` covers that path, and this
        one pins the contract a host without a state root still honours.
        """

        self.assertEqual({}, self.adapter.get("/studio/prefs"))
        self.assertEqual(
            {"layout_preset": "classic", "session_limit": 15},
            self.adapter.post(
                "/studio/prefs",
                {"layout_preset": "classic", "session_limit": 15},
            ),
        )
        self.assertEqual(
            {"layout_preset": "classic", "session_limit": 15},
            self.adapter.get("/studio/prefs"),
        )

        saved = self.adapter.post(
            "/studio/generate",
            {
                "action": "save_defaults",
                "defaults_data": {"steps": 30, "cfg_scale": 5.0},
            },
        )
        self.assertTrue(saved["settings"]["defaults_saved"])
        loaded = self.adapter.post(
            "/studio/generate",
            {"action": "load_defaults"},
        )
        self.assertEqual(
            {"steps": 30, "cfg_scale": 5.0},
            loaded["settings"],
        )
        self.adapter.delete("/studio/prefs")
        self.assertEqual({}, self.adapter.get("/studio/prefs"))

        auto_unload = self.adapter.post(
            "/studio/auto_unload",
            {"enabled": True, "minutes": 12},
        )
        self.assertFalse(auto_unload["ok"])
        self.assertFalse(auto_unload["available"])
        self.assertFalse(auto_unload["enabled"])
        self.assertIn("unavailable", auto_unload["error"].casefold())

        session_clear = self.adapter.post("/studio/session_clear", {})
        self.assertTrue(session_clear["ok"])
        self.assertFalse(session_clear["persisted"])

        vram_reserve = self.adapter.post(
            "/studio/vram_reserve",
            {"gb": 2},
        )
        self.assertFalse(vram_reserve["ok"])
        self.assertFalse(vram_reserve["available"])
        self.assertIn("unavailable", vram_reserve["error"].casefold())

    def test_generation_translates_to_owned_mock_and_back(self) -> None:
        self._load_first_model()
        result = self.adapter.post(
            "/studio/generate",
            {
                "prompt": "source-faithful adapter test",
                "neg_prompt": "text, watermark",
                "seed": 707,
                "steps": 4,
                "cfg_scale": 5.0,
                "width": 512,
                "height": 512,
            },
        )
        self.assertIsNone(result["error"])
        self.assertEqual(707, result["seed"])
        self.assertEqual(512, result["settings"]["width"])
        self.assertEqual(512, result["settings"]["height"])
        self.assertTrue(result["settings"]["is_mock"])
        self.assertEqual(1, len(result["images"]))
        self.assertTrue(result["images"][0].startswith("data:image/"))
        self.assertEqual(1, len(result["infotexts"]))
        self.assertIn("source-faithful adapter test", result["infotexts"][0])
        self.assertTrue(result["task_id"])

    def test_generation_requires_an_explicit_valid_model_selection(self) -> None:
        with self.assertRaises(
            self.adapter_module.SourceFrontendRequestError
        ) as context:
            self.adapter.post(
                "/studio/generate",
                {
                    "prompt": "no implicit model residency",
                    "neg_prompt": "",
                    "seed": 710,
                    "steps": 4,
                    "cfg_scale": 5.0,
                    "width": 512,
                    "height": 512,
                },
            )
        self.assertIn("Select an available mock model", str(context.exception))

    def test_infotext_uses_only_owned_fields_and_resolved_seed(self) -> None:
        request = {
            "positive_prompt": "honest metadata",
            "negative_prompt": "watermark",
            "steps": 30,
            "cfg_scale": 5.0,
            "width": 768,
            "height": 768,
        }
        unresolved = self.adapter_module._infotext(
            request,
            "Studio Mock Illustration",
            None,
        )
        self.assertNotIn("Sampler:", unresolved)
        self.assertNotIn("Schedule type:", unresolved)
        self.assertNotIn("Seed:", unresolved)
        self.assertNotIn("Seed: -1", unresolved)

        resolved = self.adapter_module._infotext(
            request,
            "Studio Mock Illustration",
            42,
        )
        self.assertIn("Seed: 42", resolved)
        self.assertIn("Steps: 30", resolved)
        self.assertIn("CFG scale: 5.0", resolved)
        self.assertIn("Size: 768x768", resolved)
        self.assertIn("Model: Studio Mock Illustration", resolved)

    def test_progress_fraction_uses_explicit_contract_scale(self) -> None:
        for progress, expected in (
            (0, 0.00),
            (1, 0.01),
            (2, 0.02),
            (50, 0.50),
            (99, 0.99),
            (100, 1.00),
        ):
            with self.subTest(progress=progress):
                self.assertEqual(
                    expected,
                    self.adapter_module._fraction_of(
                        {"progress": progress}
                    ),
                )
        self.assertEqual(
            0.5,
            self.adapter_module._fraction_of(
                {"progress_fraction": 0.5}
            ),
        )

    def test_generation_notices_only_recognized_ignored_settings(self) -> None:
        self._load_first_model()
        supported = {
            "prompt": "supported request",
            "neg_prompt": "",
            "seed": 711,
            "steps": 4,
            "cfg_scale": 5.0,
            "width": 512,
            "height": 512,
        }
        supported_result = self.adapter.post(
            "/studio/generate",
            supported,
        )
        self.assertIsNone(supported_result["notice"])

        ignored_result = self.adapter.post(
            "/studio/generate",
            {
                **supported,
                "seed": 712,
                "sampler": "Euler a",
                "scheduler": "simple",
                "enable_hr": True,
                "hr_scale": 2.0,
                "hr_upscaler": "Latent",
                "adetailer": [{"enabled": True}],
                "loras": [{"name": "example"}],
                "regions": [{"prompt": "detail"}],
                "controlnet": [{"enabled": True}],
                "watermark": {"enabled": True},
                "extension_args": {"example": True},
                "disabled_extensions": ["example"],
                "unknown_metadata": "does not create noise",
            },
        )
        notice = ignored_result["notice"]
        self.assertIsInstance(notice, str)
        for key in (
            "sampler",
            "scheduler",
            "enable_hr",
            "hr_scale",
            "hr_upscaler",
            "adetailer",
            "loras",
            "regions",
            "controlnet",
            "watermark",
            "extension_args",
            "disabled_extensions",
        ):
            with self.subTest(key=key):
                self.assertIn(key, notice)
        self.assertNotIn("unknown_metadata", notice)

    def test_http_and_websocket_observers_request_result_free_polling(
        self,
    ) -> None:
        class RecordingPresentation:
            def __init__(self) -> None:
                self.include_result_values: list[bool] = []

            def poll(
                self,
                _job_id: str,
                *,
                include_result: bool = True,
            ) -> dict[str, Any]:
                self.include_result_values.append(include_result)
                return {
                    "state": "running",
                    "progress": 1,
                    "message": "one percent",
                }

        presentation = RecordingPresentation()
        adapter = self.adapter_module.SourceFrontendAdapter(presentation)
        adapter._active = self.adapter_module._ActiveGeneration(
            job_id="result-free-observer",
            steps=100,
            model_title="Test model",
            request={},
            ignored_parameters=(),
        )
        self.assertEqual(0.01, adapter.progress()["progress"])
        self.assertEqual(0.01, adapter.websocket_status()["progress"])
        self.assertEqual([False, False], presentation.include_result_values)

    def test_active_generation_can_be_interrupted_concurrently(self) -> None:
        self.presentation.shutdown()
        self.presentation = _presentation(interval=0.05)
        self.adapter = self.adapter_module.SourceFrontendAdapter(
            self.presentation
        )
        self._load_first_model()
        started = Event()

        def generate() -> dict[str, Any]:
            started.set()
            return self.adapter.post(
                "/studio/generate",
                {
                    "prompt": "interrupt source adapter",
                    "neg_prompt": "",
                    "seed": 708,
                    "steps": 30,
                    "cfg_scale": 5.0,
                    "width": 512,
                    "height": 512,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(generate)
            self.assertTrue(started.wait(1))
            deadline = monotonic() + 1
            while self.adapter.active_job_id is None and monotonic() < deadline:
                sleep(0.005)
            self.assertIsNotNone(self.adapter.active_job_id)
            cancellation = self.adapter.interrupt()
            result = future.result(timeout=2)

        self.assertTrue(cancellation["cancelled"])
        self.assertIn("cancel", result["error"].casefold())
        self.assertIsNone(self.adapter.active_job_id)

    def test_unsupported_source_routes_are_explicit_404s(self) -> None:
        with self.assertRaises(
            self.adapter_module.SourceFrontendRouteError
        ) as context:
            self.adapter.get("/studio/not-a-real-route")
        self.assertEqual(404, context.exception.error["http_status"])
        self.assertIn(
            "GET /studio/not-a-real-route",
            context.exception.error["message"],
        )


class StaticPresentationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )

    def _handler(self) -> Any:
        return object.__new__(
            self.presentation_module._StudioRequestHandler
        )

    def test_static_paths_resolve_only_inside_the_copied_frontend(self) -> None:
        handler = self._handler()
        self.assertEqual("index.html", handler._static_filename("/studio"))
        self.assertEqual(
            "app.css",
            handler._static_filename("/studio/static/app.css"),
        )
        self.assertEqual(
            "brand/favicon.svg",
            handler._static_filename(
                "/studio/static/brand/favicon.svg"
            ),
        )
        for path in (
            "/studio/static/",
            "/studio/static/../presentation.py",
            "/studio/static/%2e%2e/presentation.py",
            r"/studio/static/..\presentation.py",
            "/other/static/app.css",
        ):
            with self.subTest(path=path):
                self.assertIsNone(handler._static_filename(path))

    def test_source_actions_may_send_an_explicit_empty_body(self) -> None:
        handler = self._handler()
        handler.headers = Message()
        handler.headers["Content-Length"] = "0"
        self.assertEqual({}, handler._read_source_json())

    def test_static_server_reads_nested_source_assets_with_correct_types(
        self,
    ) -> None:
        handler = self._handler()
        responses: list[tuple[Any, str, bytes, bool]] = []

        def send_bytes(
            _handler: Any,
            status: Any,
            content_type: str,
            content: bytes,
            *,
            cache: bool = False,
        ) -> None:
            responses.append((status, content_type, content, cache))

        handler._send_bytes = MethodType(send_bytes, handler)
        self.assertTrue(
            handler._serve_static("/studio/static/locales/en.json")
        )
        status, content_type, content, cache = responses.pop()
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("application/json; charset=utf-8", content_type)
        self.assertTrue(cache)
        self.assertEqual(
            (FRONTEND_ROOT / "locales" / "en.json").read_bytes(),
            content,
        )

        self.assertTrue(
            handler._serve_static("/studio/static/brand/favicon-32.png")
        )
        _status, content_type, _content, cache = responses.pop()
        self.assertEqual("image/png", content_type)
        self.assertTrue(cache)

    def test_csp_allows_only_the_pinned_source_requirements(self) -> None:
        handler = self._handler()
        headers: dict[str, str] = {}

        def send_header(
            _handler: Any,
            name: str,
            value: str,
        ) -> None:
            headers[name] = value

        handler.send_header = MethodType(send_header, handler)
        handler._security_headers()
        csp = headers["Content-Security-Policy"]
        html = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
        loader = re.search(r"<script>([\s\S]*?)</script>", html)
        self.assertIsNotNone(loader)
        loader_hash = base64.b64encode(
            hashlib.sha256(loader.group(1).encode("utf-8")).digest()
        ).decode("ascii")
        handler = re.search(r'\bonclick="([^"]+)"', html)
        self.assertIsNotNone(handler)
        handler_hash = base64.b64encode(
            hashlib.sha256(handler.group(1).encode("utf-8")).digest()
        ).decode("ascii")

        directives = {
            fields[0]: tuple(fields[1:])
            for directive in csp.split(";")
            if (fields := directive.strip().split())
        }
        self.assertEqual(
            {
                "default-src": ("'self'",),
                "img-src": ("'self'", "data:", "blob:"),
                "style-src": ("'self'", "'unsafe-inline'"),
                "script-src": (
                    "'self'",
                    "'unsafe-hashes'",
                    f"'sha256-{loader_hash}'",
                    f"'sha256-{handler_hash}'",
                ),
                "font-src": ("'self'",),
                "connect-src": ("'self'",),
                "base-uri": ("'none'",),
                "form-action": ("'self'",),
                "frame-ancestors": ("'none'",),
                "object-src": ("'none'",),
            },
            directives,
        )

    def test_websocket_frame_encoding_is_rfc6455_compatible(self) -> None:
        handler = self._handler()
        self.assertEqual(b"\x81\x02{}", handler._websocket_frame(0x1, b"{}"))
        medium = handler._websocket_frame(0x1, b"x" * 126)
        self.assertEqual(b"\x81\x7e\x00\x7e", medium[:4])
        self.assertEqual(130, len(medium))


class StaticAssetsAreRevalidatedTests(unittest.TestCase):
    """A static file edited on disk must reach a browser that already has it.

    THE DEFECT, as the owner met it: `harness.py install` appends ~163 KB to
    `forge_studio/frontend/debug.js`, Studio is reloaded, and the harness panel
    is not there. The instruction that grew around it was "start Studio on a
    port it has not used before", which treats a new ORIGIN as the remedy.

    Measured over loopback against this handler, before the repair:

        GET /studio/static/debug.js?v=4.17.0
          Cache-Control: public, max-age=300
          ETag:          (absent)
          Last-Modified: (absent)

    The server was never at fault -- `_serve_static` re-reads from disk on every
    request and served the edited bytes immediately. The browser was: `max-age`
    means a stored response is reused WITHOUT ASKING until it lapses, and with
    no validator there was nothing to ask WITH even after it did.

    THE FRESHNESS DIRECTIVE IS THE BINDING ONE, and that ordering is the whole
    design. Adding an ETag while leaving `max-age=300` would have changed
    nothing at all, because a validator is only consulted once freshness has
    already expired. `no-cache` is what makes the browser ask; the ETag is only
    what makes asking cost a 304 instead of the file.

    SOCKET-FREE, because it has to be. `run_tests.py`'s `NetworkGuard` refuses
    `socket.listen` and `socket.accept`, so a collected test cannot start a
    loopback server. The wire-level proof lives in
    `Evidence/u3r2f-owner/_static_cache_headers.py`, which drives the real
    server and whose fields must move
    `cache-control: public, max-age=300 -> no-cache`, `etag: null -> "<64 hex>"`
    and `conditionalFetch.is304: absent -> true`.
    """

    def setUp(self) -> None:
        self.presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )

    def _wire(self, request_headers: dict[str, str] | None = None,
              *, command: str = "GET") -> tuple[Any, dict[str, Any]]:
        """A handler that records exactly what would reach the wire."""

        sent: dict[str, Any] = {"headers": {}, "body": b"", "status": None}
        request = Message()
        for name, value in (request_headers or {}).items():
            request[name] = value

        handler = object.__new__(self.presentation_module._StudioRequestHandler)
        handler.command = command
        handler.headers = request

        def send_response(_h: Any, status: Any, message: Any = None) -> None:
            sent["status"] = status

        def send_header(_h: Any, key: str, value: str) -> None:
            sent["headers"][key] = value

        def end_headers(_h: Any) -> None:
            return None

        handler.send_response = MethodType(send_response, handler)
        handler.send_header = MethodType(send_header, handler)
        handler.end_headers = MethodType(end_headers, handler)

        class _Writer:
            def write(self, data: bytes) -> None:
                sent["body"] = sent["body"] + data

        handler.wfile = _Writer()
        return handler, sent

    @staticmethod
    def _tag(name: str) -> str:
        return '"' + hashlib.sha256(
            (FRONTEND_ROOT / name).read_bytes()
        ).hexdigest() + '"'

    def test_a_cacheable_static_asset_must_be_revalidated_before_reuse(
        self,
    ) -> None:
        """The defect, written down. This asserted nothing before because the
        value was pinned nowhere in the whole suite."""

        handler, sent = self._wire()
        self.assertTrue(handler._serve_static("/studio/static/app.js"))
        self.assertEqual("no-cache", sent["headers"]["Cache-Control"])
        #: The binding directive. Not a spelling preference: any `max-age`
        #: without `no-cache` restores the silent-reuse window.
        self.assertNotIn("max-age", sent["headers"]["Cache-Control"])

    def test_a_cacheable_static_asset_carries_a_content_derived_etag(
        self,
    ) -> None:
        """Derived from the RESPONSE BYTES, so it cannot disagree with what was
        sent. An mtime or size tag can repeat across two different contents --
        `harness.py restore` rewinds `debug.js` to bytes it held before -- and a
        repeated validator on changed content is a permanent 304."""

        handler, sent = self._wire()
        self.assertTrue(handler._serve_static("/studio/static/app.js"))
        self.assertEqual(self._tag("app.js"), sent["headers"]["ETag"])
        #: NO `Last-Modified`. HTTP-date is second-resolution and installing the
        #: harness then reloading is a sub-second edit, so a date validator
        #: would answer 304 for a file that had changed.
        self.assertNotIn("Last-Modified", sent["headers"])

    def test_a_matching_validator_gets_a_bodyless_304(self) -> None:
        handler, sent = self._wire(
            {"If-None-Match": self._tag("app.js")}
        )
        self.assertTrue(handler._serve_static("/studio/static/app.js"))
        self.assertEqual(HTTPStatus.NOT_MODIFIED, sent["status"])
        self.assertEqual(b"", sent["body"])
        #: A 304 has no body, so it must declare no length and no type. The
        #: connection is HTTP/1.1 keep-alive, and a declared length with no
        #: bytes behind it desynchronises the NEXT request on it -- the failure
        #: then lands on an unrelated asset and is diagnosed there.
        self.assertNotIn("Content-Length", sent["headers"])
        self.assertNotIn("Content-Type", sent["headers"])
        self.assertIn("Cache-Control", sent["headers"])

    def test_a_changed_file_defeats_a_held_validator(self) -> None:
        """THE OWNER'S CASE. A browser holding a validator that no longer
        describes the file on disk gets the file, on the same origin and URL.

        The byte count is READ, never written down: `harness.py install`
        changes this file's size, so a hard-coded length would make this test
        pass or fail on whether the harness happened to be installed."""

        handler, sent = self._wire({"If-None-Match": '"' + "0" * 64 + '"'})
        self.assertTrue(handler._serve_static("/studio/static/debug.js"))
        self.assertEqual(HTTPStatus.OK, sent["status"])
        self.assertEqual((FRONTEND_ROOT / "debug.js").read_bytes(),
                         sent["body"])
        self.assertEqual(self._tag("debug.js"), sent["headers"]["ETag"])

    def test_weak_and_listed_and_wildcard_validators_all_match(self) -> None:
        """`If-None-Match` is a LIST, `*` matches anything, and `W/` marks a
        weak tag which a conditional GET compares weakly. A bare `==` against
        the raw header is the silent mutation: every one of these would get a
        correct 200 and revalidation would simply stop happening."""

        tag = self._tag("app.js")
        for header in (tag, "W/" + tag, f'"other", {tag}', "*"):
            with self.subTest(header=header):
                handler, sent = self._wire({"If-None-Match": header})
                self.assertTrue(handler._serve_static("/studio/static/app.js"))
                self.assertEqual(HTTPStatus.NOT_MODIFIED, sent["status"])

    def test_the_shell_offers_no_validator_and_no_storage(self) -> None:
        """`/studio/` is in `_UNCACHED_STATIC_PATHS`, so the shell is `no-store`
        and the loader re-runs on every load. That is what makes the rest of
        this work, and it must not acquire an ETag."""

        handler, sent = self._wire()
        self.assertTrue(handler._serve_static("/studio/"))
        self.assertEqual("no-store", sent["headers"]["Cache-Control"])
        self.assertNotIn("ETag", sent["headers"])

    def test_a_caller_supplied_cache_control_and_etag_still_win(self) -> None:
        """The gallery thumbnail contract. It is immutable for its own ETag and
        asks for a week; this must not overwrite either.

        `cache=True` DELIBERATELY, and that is the whole test. Written first
        with the default `cache=False`, it looked right and guarded nothing: the
        `if cache and ... and "ETag" not in extra` line short-circuits on the
        FIRST operand, so deleting the third one -- the guard this test is named
        for -- left it green. Caught by the mutation campaign, not by review."""

        handler, sent = self._wire()
        handler._send_bytes(
            HTTPStatus.OK, "image/webp", b"x",
            cache=True,
            extra_headers={
                "ETag": "abc",
                "Cache-Control": "public, max-age=604800, immutable",
            },
        )
        self.assertEqual("public, max-age=604800, immutable",
                         sent["headers"]["Cache-Control"])
        self.assertEqual("abc", sent["headers"]["ETag"])

    def test_an_uncached_response_with_its_own_etag_never_gets_a_304(
        self,
    ) -> None:
        """THE GALLERY SHAPE, which is the reason the 304 is gated on `cache`
        and not merely on having a tag.

        A thumbnail arrives with `cache=False` and its own ETag from
        `gallery_service.py`, which is a bare unquoted 64-hex string rather than
        a syntactically valid entity-tag. It is served `immutable` for a week
        and is never meant to revalidate, so comparing a client's echo of it
        against that tag would be a contract change with no symptom. Dropping
        `cache and` from the gate is invisible to every other test here."""

        handler, sent = self._wire({"If-None-Match": "abc"})
        handler._send_bytes(
            HTTPStatus.OK, "image/webp", b"x",
            extra_headers={
                "ETag": "abc",
                "Cache-Control": "public, max-age=604800, immutable",
            },
        )
        self.assertEqual(HTTPStatus.OK, sent["status"])
        self.assertEqual(b"x", sent["body"])

    def test_an_uncached_response_gains_no_validator(self) -> None:
        """`cache=False` is every caller except `_serve_static` -- JSON, the
        404, the favicon 204 and the gallery. None of them may start emitting a
        validator, and the 304 branch must be unreachable for them."""

        handler, sent = self._wire({"If-None-Match": "*"})
        handler._send_bytes(HTTPStatus.OK, "application/json", b"{}")
        self.assertEqual(HTTPStatus.OK, sent["status"])
        self.assertEqual("no-store", sent["headers"]["Cache-Control"])
        self.assertNotIn("ETag", sent["headers"])

    def test_a_conditional_head_is_a_304_and_an_unconditional_one_is_not(
        self,
    ) -> None:
        handler, sent = self._wire({"If-None-Match": self._tag("app.js")},
                                   command="HEAD")
        self.assertTrue(handler._serve_static("/studio/static/app.js"))
        self.assertEqual(HTTPStatus.NOT_MODIFIED, sent["status"])
        self.assertEqual(b"", sent["body"])

        handler2, sent2 = self._wire(command="HEAD")
        self.assertTrue(handler2._serve_static("/studio/static/app.js"))
        self.assertEqual(HTTPStatus.OK, sent2["status"])
        #: A HEAD still declares the length it would have sent, and sends none.
        self.assertIn("Content-Length", sent2["headers"])
        self.assertEqual(b"", sent2["body"])

    def test_the_validator_depends_on_content_alone(self) -> None:
        """CONTROL, and it must not fire.

        Two independently constructed handlers reaching the same bytes by
        different paths produce the SAME tag, and different files produce
        different ones. If this ever goes red, per-process, per-path, per-port
        or per-clock state has been smuggled into the validator -- which is the
        mtime tag arriving by another door."""

        a, sent_a = self._wire()
        self.assertTrue(a._serve_static("/studio/static/app.js"))
        b, sent_b = self._wire()
        self.assertTrue(b._serve_static("/studio/static/app.js"))
        self.assertEqual(sent_a["headers"]["ETag"], sent_b["headers"]["ETag"])

        c, sent_c = self._wire()
        self.assertTrue(c._serve_static("/studio/static/debug.js"))
        self.assertNotEqual(sent_a["headers"]["ETag"],
                            sent_c["headers"]["ETag"])


if __name__ == "__main__":
    unittest.main()
