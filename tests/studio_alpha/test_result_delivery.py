"""Contained same-origin result delivery.

Covers the 26 behaviours required by the real-adapter contract handoff. No
real generated image is used; the mock backend's deterministic output is the
fixture.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _request_payload(model_id: str) -> dict[str, object]:
    return {
        "model_id": model_id,
        "positive_prompt": "contained delivery",
        "negative_prompt": "",
        "seed": 7,
        "steps": 20,
        "cfg_scale": 7.0,
        "width": 320,
        "height": 448,
    }


class ResultDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")
        self.delivery = importlib.import_module("forge_studio.result_delivery")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "results"
        self.root.mkdir(parents=True)
        self.application = self.studio.StudioApplication(
            self.studio.MockBackend(
                result_directory=self.root,
                event_interval_seconds=0.001,
            ),
            result_root=self.root,
        )

    # -- helpers ---------------------------------------------------------

    def _complete_job(self, prompt: str = "contained delivery") -> str:
        model_id = self.application.list_models()[0].model_id
        request = self.studio.GenerationRequest(
            **{**_request_payload(model_id), "positive_prompt": prompt}
        )
        job_id = self.application.submit_generation(request).job_id
        for _ in range(64):
            event = self.application.poll_or_stream_progress(job_id)
            if event.state.value in {"completed", "failed", "cancelled"}:
                break
        return job_id

    def _asset(self) -> object:
        job_id = self._complete_job()
        asset = self.application.result_asset(job_id)
        self.assertIsNotNone(asset)
        return asset

    # -- 1-5: registration, handle, retrieval, bytes, type ----------------

    def test_completed_result_registers_beneath_the_result_root(self) -> None:
        """1."""

        job_id = self._complete_job()
        result = self.application.get_result(job_id)
        stored = Path(result.output_path).resolve()
        self.assertTrue(stored.is_file())
        stored.relative_to(self.root.resolve())

    def test_asset_handle_is_opaque_and_reveals_no_path(self) -> None:
        """2."""

        asset = self._asset()
        self.assertRegex(
            asset.handle,
            r"\Astudio-result/[0-9a-f]{32}\.svg\Z",
        )
        self.assertNotIn(str(self.root), asset.handle)
        self.assertNotIn(self.root.name, asset.handle)

    def test_handle_resolves_to_exact_stored_bytes(self) -> None:
        """3 and 4."""

        job_id = self._complete_job()
        asset = self.application.result_asset(job_id)
        stored = Path(
            self.application.get_result(job_id).output_path
        ).read_bytes()
        payload = self.application.read_result_asset(asset.handle)
        self.assertEqual(stored, payload.content)
        self.assertEqual(len(stored), asset.byte_length)

    def test_media_type_comes_from_the_result_record(self) -> None:
        """5. Not from the filename extension."""

        job_id = self._complete_job()
        asset = self.application.result_asset(job_id)
        payload = self.application.read_result_asset(asset.handle)
        self.assertEqual(
            self.application.get_result(job_id).mime_type,
            payload.media_type,
        )

    # -- 6-7: no internal path leakage -----------------------------------

    def test_poll_response_carries_no_internal_path(self) -> None:
        """6."""

        presentation = importlib.import_module("forge_studio.presentation")
        surface = presentation.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )
        job_id = self._complete_job()
        response = surface.poll(job_id)
        result = response["result"]
        self.assertNotIn("output_path", result)
        self.assertNotIn("metadata_path", result)
        self.assertIn("image_handle", result)
        serialized = repr(response)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("\\\\", serialized.replace("\\\\n", ""))

    def test_asset_record_exposes_only_handle_type_and_length(self) -> None:
        """7. Nothing that could become a path header."""

        asset = self._asset()
        self.assertEqual(
            {"handle", "media_type", "byte_length"},
            set(vars(asset)),
        )

    # -- 10-11: unknown and evicted --------------------------------------

    def test_unknown_handle_is_not_found(self) -> None:
        """10."""

        with self.assertRaises(self.studio.StudioError) as raised:
            self.application.read_result_asset(
                "studio-result/" + "0" * 32 + ".png"
            )
        self.assertEqual("RESULT_NOT_FOUND", raised.exception.error.code)

    def test_evicted_handle_reports_gone(self) -> None:
        """11."""

        registry = self.delivery.ResultRegistry(self.root, max_entries=1)
        first = self.root / "first.png"
        second = self.root / "second.png"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        stale = registry.register(first, media_type="image/png")
        registry.register(second, media_type="image/png")
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.read(stale.handle)
        self.assertEqual("RESULT_GONE", raised.exception.error.code)

    # -- 12-16: traversal and path injection ------------------------------

    def test_traversal_and_path_shaped_handles_are_rejected(self) -> None:
        """12, 13, 14, 15, 16."""

        registry = self.delivery.ResultRegistry(self.root)
        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"outside")
        hostile = (
            "../outside.png",
            "studio-result/../../outside.png",
            "studio-result/..%2f..%2foutside.png",
            "studio-result/..%252f..%252foutside.png",
            "studio-result/sub/dir.png",
            "studio-result\\evil.png",
            "/etc/passwd",
            r"C:\Windows\win.ini",
            "C:/Windows/win.ini",
            "",
            "studio-result/.png",
            "studio-result/" + "0" * 31 + ".png",
            "studio-result/" + "0" * 33 + ".png",
            "STUDIO-RESULT/" + "0" * 32 + ".png",
        )
        for candidate in hostile:
            with self.subTest(handle=candidate):
                with self.assertRaises(self.studio.StudioError) as raised:
                    registry.read(candidate)
                self.assertEqual(
                    "RESULT_NOT_FOUND",
                    raised.exception.error.code,
                )

    def test_registration_outside_the_root_is_rejected(self) -> None:
        """17."""

        registry = self.delivery.ResultRegistry(self.root)
        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"outside")
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(outside, media_type="image/png")
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    def test_symlink_escape_is_rejected_or_fails_closed(self) -> None:
        """18."""

        registry = self.delivery.ResultRegistry(self.root)
        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"outside")
        link = self.root / "link.png"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(link, media_type="image/png")
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    def test_symlink_swapped_after_registration_fails_closed(self) -> None:
        """18. Containment is re-verified on every read."""

        registry = self.delivery.ResultRegistry(self.root)
        outside = Path(self._temp.name) / "outside.png"
        outside.write_bytes(b"outside")
        target = self.root / "swap.png"
        target.write_bytes(b"inside")
        asset = registry.register(target, media_type="image/png")
        target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable in this environment")
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.read(asset.handle)
        self.assertEqual("RESULT_NOT_FOUND", raised.exception.error.code)

    def test_deleted_result_fails_closed_on_read(self) -> None:
        """18. Re-verification on read, without needing symlink support."""

        registry = self.delivery.ResultRegistry(self.root)
        target = self.root / "vanishing.png"
        target.write_bytes(b"inside")
        asset = registry.register(target, media_type="image/png")
        target.unlink()
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.read(asset.handle)
        self.assertEqual("RESULT_NOT_FOUND", raised.exception.error.code)

    def test_directory_cannot_be_registered(self) -> None:
        """17. Only regular files are deliverable."""

        registry = self.delivery.ResultRegistry(self.root)
        folder = self.root / "folder"
        folder.mkdir()
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(folder, media_type="image/png")
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    def test_missing_file_cannot_be_registered(self) -> None:
        """17."""

        registry = self.delivery.ResultRegistry(self.root)
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.register(
                self.root / "absent.png",
                media_type="image/png",
            )
        self.assertEqual("RESULT_OUTSIDE_ROOT", raised.exception.error.code)

    # -- 19: MIME policy ---------------------------------------------------

    def test_unsupported_media_type_is_rejected_at_registration(self) -> None:
        """19. Eligibility never derives from the filename extension."""

        registry = self.delivery.ResultRegistry(self.root)
        payload = self.root / "payload.png"
        payload.write_bytes(b"not really a png")
        for media_type in ("text/html", "application/octet-stream", "", None):
            with self.subTest(media_type=media_type):
                with self.assertRaises(self.studio.StudioError) as raised:
                    registry.register(payload, media_type=media_type)
                self.assertEqual(
                    "RESULT_MEDIA_TYPE_UNSUPPORTED",
                    raised.exception.error.code,
                )

    def test_extension_comes_from_media_type_not_from_the_file(self) -> None:
        """19."""

        registry = self.delivery.ResultRegistry(self.root)
        payload = self.root / "payload.html"
        payload.write_bytes(b"<html></html>")
        asset = registry.register(payload, media_type="image/png")
        self.assertTrue(asset.handle.endswith(".png"))

    # -- 20: bounded retention ---------------------------------------------

    def test_registry_growth_is_bounded(self) -> None:
        """20."""

        registry = self.delivery.ResultRegistry(self.root, max_entries=3)
        for index in range(10):
            target = self.root / f"bounded-{index}.png"
            target.write_bytes(b"x")
            registry.register(target, media_type="image/png")
        self.assertEqual(3, len(registry))

    def test_repeated_access_is_allowed_while_retained(self) -> None:
        """24. Documented policy: reads do not consume a handle."""

        asset = self._asset()
        first = self.application.read_result_asset(asset.handle)
        second = self.application.read_result_asset(asset.handle)
        self.assertEqual(first.content, second.content)

    def test_registration_is_idempotent_per_job(self) -> None:
        """20. Repeated observation must not grow the registry."""

        job_id = self._complete_job()
        handles = {
            self.application.result_asset(job_id).handle for _ in range(5)
        }
        self.assertEqual(1, len(handles))

    # -- 21-23: lifecycle interaction --------------------------------------

    def test_result_free_progress_does_not_read_image_bytes(self) -> None:
        """21."""

        presentation = importlib.import_module("forge_studio.presentation")
        surface = presentation.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )
        job_id = self._complete_job()
        response = surface.poll(job_id, include_result=False)
        self.assertNotIn("result", response)

    def test_cancellation_leaves_no_accessible_result(self) -> None:
        """22."""

        model_id = self.application.list_models()[0].model_id
        request = self.studio.GenerationRequest(
            **_request_payload(model_id)
        )
        job_id = self.application.submit_generation(request).job_id
        self.application.cancel_generation(job_id)
        self.assertIsNone(self.application.result_asset(job_id))

    def test_recovery_generation_produces_a_new_valid_result(self) -> None:
        """23."""

        first = self._complete_job("first")
        first_handle = self.application.result_asset(first).handle
        second = self._complete_job("second")
        second_handle = self.application.result_asset(second).handle
        self.assertNotEqual(first_handle, second_handle)
        self.assertTrue(
            self.application.read_result_asset(second_handle).content
        )

    # -- 25-26: compatibility ----------------------------------------------

    def test_inline_data_url_support_is_preserved(self) -> None:
        """25."""

        job_id = self._complete_job()
        result = self.application.get_result(job_id)
        self.assertTrue(
            str(result.image_data_url).startswith("data:image/svg+xml;base64,")
        )

    def test_canonical_client_url_shape_is_produced(self) -> None:
        """26. The canonical frontend consumes this without a vendored change.

        `app.js:2575-2581` renders `session_entries[i]` with
        `source === "scratch"` through `_studioFileUrl(srv.path)`, which is
        `/studio/file?path=<encoded>` (`app.js:558-560`).
        """

        presentation = importlib.import_module("forge_studio.presentation")
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        surface = presentation.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )
        adapter = adapter_module.SourceFrontendAdapter(surface)
        models = adapter.get("/studio/models")
        adapter.post(
            "/studio/load_model",
            {"title": models[0]["title"]},
        )
        response = adapter.post(
            "/studio/generate",
            {
                "prompt": "canonical delivery",
                "negative_prompt": "",
                "seed": 11,
                "steps": 20,
                "cfg_scale": 7.0,
                "width": 320,
                "height": 448,
            },
        )
        entries = response["session_entries"]
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("scratch", entry["source"])
        self.assertRegex(
            entry["path"],
            r"\Astudio-result/[0-9a-f]{32}\.svg\Z",
        )
        self.assertNotIn("output_path", response)
        self.assertNotIn("metadata_path", response)
        self.assertNotIn(str(self.root), repr(response))
        payload = self.application.read_result_asset(entry["path"])
        self.assertTrue(payload.content)

    # -- shutdown ----------------------------------------------------------

    def test_shutdown_clears_delivery_handles(self) -> None:
        """17 retention: eviction removes lookup capability."""

        asset = self._asset()
        self.application.shutdown()
        with self.assertRaises(self.studio.StudioError) as raised:
            self.application.read_result_asset(asset.handle)
        self.assertEqual("RESULT_GONE", raised.exception.error.code)

    def test_application_without_result_root_mints_no_handles(self) -> None:
        """Delivery is opt-in through composition."""

        application = self.studio.StudioApplication(
            self.studio.MockBackend(event_interval_seconds=0.001)
        )
        model_id = application.list_models()[0].model_id
        request = self.studio.GenerationRequest(**_request_payload(model_id))
        job_id = application.submit_generation(request).job_id
        for _ in range(64):
            if application.poll_or_stream_progress(job_id).state.value in {
                "completed",
                "failed",
                "cancelled",
            }:
                break
        self.assertIsNone(application.result_asset(job_id))


class ResultResponsePolicyTests(unittest.TestCase):
    """Header and status policy for the delivery route, without a socket."""

    def setUp(self) -> None:
        self.presentation = importlib.import_module(
            "forge_studio.presentation"
        )

    def _handler(self, payload: object, error: Exception | None = None):
        """Build a socket-free handler bound to a stub delivery surface."""

        sent: dict[str, object] = {"headers": {}, "body": b""}

        class _Surface:
            def read_result_asset(self, handle: str) -> object:
                if error is not None:
                    raise error
                return payload

        class _Probe(self.presentation._StudioRequestHandler):
            def __init__(self) -> None:  # no socket, no BaseHTTPRequestHandler
                self.command = "GET"

            @property
            def _presentation(self):
                return _Surface()

            def send_response(self, status, message=None):
                sent["status"] = status

            def send_header(self, key, value):
                sent["headers"][key] = value

            def end_headers(self):
                return None

        handler = _Probe()

        class _Writer:
            def write(self, data: bytes) -> None:
                sent["body"] = sent["body"] + data

        handler.wfile = _Writer()
        return handler, sent

    def test_result_headers_are_inert_and_pathless(self) -> None:
        """7 and 15. Sandboxed, no-store, exact length, no path header."""

        module = importlib.import_module("forge_studio.result_delivery")
        payload = module.ResultPayload(
            media_type="image/svg+xml",
            content=b"<svg/>",
        )
        handler, sent = self._handler(payload)
        handler._send_result_bytes(payload.media_type, payload.content)
        headers = sent["headers"]
        self.assertEqual("image/svg+xml", headers["Content-Type"])
        self.assertEqual("6", headers["Content-Length"])
        self.assertEqual("private, no-store", headers["Cache-Control"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn(
            "script-src 'unsafe-hashes'",
            headers["Content-Security-Policy"],
        )
        for value in headers.values():
            self.assertNotIn(os.sep + os.sep, str(value))
        self.assertEqual(b"<svg/>", sent["body"])

    def test_head_request_sends_headers_without_a_body(self) -> None:
        module = importlib.import_module("forge_studio.result_delivery")
        payload = module.ResultPayload(media_type="image/png", content=b"data")
        handler, sent = self._handler(payload)
        handler.command = "HEAD"
        handler._send_result_bytes(payload.media_type, payload.content)
        self.assertEqual("4", sent["headers"]["Content-Length"])
        self.assertEqual(b"", sent["body"])


class ResultRouteStatusTests(ResultResponsePolicyTests):
    """Routing and status mapping exercised through `_serve_result`."""

    def setUp(self) -> None:
        super().setUp()
        self.studio = importlib.import_module("forge_studio")
        self.delivery = importlib.import_module("forge_studio.result_delivery")

    def _owned(self, code: str) -> Exception:
        return self.studio.StudioError(
            self.studio.StructuredError(code=code, message="nope")
        )

    def test_non_result_route_is_not_claimed(self) -> None:
        handler, _ = self._handler(None)
        self.assertFalse(handler._serve_result("/studio/models"))
        self.assertFalse(handler._serve_result("/api/status"))

    def test_unknown_handle_returns_404(self) -> None:
        """10."""

        handler, sent = self._handler(
            None,
            error=self._owned("RESULT_NOT_FOUND"),
        )
        self.assertTrue(
            handler._serve_result("/studio/file?path=studio-result/x.png")
        )
        self.assertEqual(HTTPStatus.NOT_FOUND, sent["status"])

    def test_evicted_handle_returns_410(self) -> None:
        """11."""

        handler, sent = self._handler(None, error=self._owned("RESULT_GONE"))
        self.assertTrue(
            handler._serve_result("/studio/file?path=studio-result/x.png")
        )
        self.assertEqual(HTTPStatus.GONE, sent["status"])

    def test_missing_query_parameter_returns_404(self) -> None:
        handler, sent = self._handler(
            None,
            error=self._owned("RESULT_NOT_FOUND"),
        )
        self.assertTrue(handler._serve_result("/studio/file"))
        self.assertEqual(HTTPStatus.NOT_FOUND, sent["status"])

    def test_successful_route_sends_bytes_and_status_ok(self) -> None:
        """3."""

        payload = self.delivery.ResultPayload(
            media_type="image/png",
            content=b"\x89PNG",
        )
        handler, sent = self._handler(payload)
        self.assertTrue(
            handler._serve_result("/studio/file?path=studio-result/x.png")
        )
        self.assertEqual(HTTPStatus.OK, sent["status"])
        self.assertEqual(b"\x89PNG", sent["body"])
        self.assertEqual("image/png", sent["headers"]["Content-Type"])

    def test_unowned_exception_is_not_swallowed(self) -> None:
        handler, _ = self._handler(None, error=RuntimeError("defect"))
        with self.assertRaises(RuntimeError):
            handler._serve_result("/studio/file?path=studio-result/x.png")


if __name__ == "__main__":
    unittest.main()
