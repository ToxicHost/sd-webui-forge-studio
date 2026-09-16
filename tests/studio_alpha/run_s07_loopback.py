"""Focused loopback validation for the canonical Studio S0.7 transport.

This script is intentionally separate from ``run_tests.py`` because that
suite proves that its tests never dial out and never serve.  Here,
standard-library HTTP and RFC 6455 clients exercise the real loopback server.
No request may leave 127.0.0.1 and no third-party WebSocket dependency is
used.
"""

from __future__ import annotations

import base64
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import socket
import struct
import sys
from urllib.parse import quote
from threading import Thread
from time import monotonic, sleep
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
OUTPUT_PATH = (
    WORKSPACE_ROOT
    / "Evidence"
    / "studio-pre-adapter-corrections"
    / "runtime-validation.json"
)
FRONTEND_ROOT = APP_ROOT / "forge_studio" / "frontend"
HOST = "127.0.0.1"
EXPECTED_INDEX_SHA256 = (
    "99455c212869604c8d04ed0fea673f6bf52dad69c4e6d46a69fdd277c6791a7c"
)
EXPECTED_CSS_SHA256 = (
    "14b71c64cf9d8836e6d1a4bce263f5674ca29e034a02d966e8beb431ee6c3201"
)
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

sys.path.insert(0, str(APP_ROOT))

from forge_studio import (  # noqa: E402
    GenerationRequest,
    MockBackend,
    StudioApplication,
)
from forge_studio.presentation import (  # noqa: E402
    StudioPresentation,
    _StudioHTTPServer,
)


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    host: str | None = None,
    origin: str | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    connection = HTTPConnection(HOST, port, timeout=4)
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if host is not None:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        response_headers = {
            name.casefold(): value
            for name, value in response.getheaders()
        }
        return response.status, content, response_headers
    finally:
        connection.close()


def _request_json(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    host: str | None = None,
    origin: str | None = None,
) -> tuple[int, Any]:
    status, content, _headers = _request(
        port,
        path,
        method=method,
        payload=payload,
        host=host,
        origin=origin,
    )
    return status, json.loads(content.decode("utf-8"))


def _generation_payload(
    prompt: str,
    seed: int,
    *,
    negative_prompt: str = "text, watermark",
    width: int = 512,
    height: int = 512,
    extra_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "prompt": prompt,
        "neg_prompt": negative_prompt,
        "seed": seed,
        "steps": 30,
        "cfg_scale": 5.0,
        "width": width,
        "height": height,
    }
    if extra_parameters:
        payload.update(extra_parameters)
    return payload


def _source_generate(
    port: int,
    prompt: str,
    seed: int,
    *,
    negative_prompt: str = "text, watermark",
    width: int = 512,
    height: int = 512,
    extra_parameters: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    status, value = _request_json(
        port,
        "/studio/generate",
        method="POST",
        payload=_generation_payload(
            prompt,
            seed,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            extra_parameters=extra_parameters,
        ),
        origin=f"http://{HOST}:{port}",
    )
    if not isinstance(value, dict):
        raise AssertionError("canonical generation response is not an object")
    return status, value


def _recv_headers(client: socket.socket) -> tuple[str, bytes]:
    received = bytearray()
    while b"\r\n\r\n" not in received:
        chunk = client.recv(4096)
        if not chunk:
            break
        received.extend(chunk)
        if len(received) > 64 * 1024:
            raise AssertionError("WebSocket response headers are too large")
    header_bytes, separator, remainder = bytes(received).partition(b"\r\n\r\n")
    if not separator:
        raise AssertionError("WebSocket response did not contain complete headers")
    return header_bytes.decode("iso-8859-1"), remainder


class _WebSocketClient:
    def __init__(self, client: socket.socket, buffered: bytes = b"") -> None:
        self.client = client
        self.buffered = bytearray(buffered)

    def _recv_exact(self, length: int) -> bytes:
        while len(self.buffered) < length:
            chunk = self.client.recv(max(4096, length - len(self.buffered)))
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            self.buffered.extend(chunk)
        content = bytes(self.buffered[:length])
        del self.buffered[:length]
        return content

    def read_json(self, *, timeout: float = 1.0) -> dict[str, Any] | None:
        self.client.settimeout(timeout)
        try:
            while True:
                header = self._recv_exact(2)
                opcode = header[0] & 0x0F
                length = header[1] & 0x7F
                if length == 126:
                    length = struct.unpack("!H", self._recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._recv_exact(8))[0]
                if header[1] & 0x80:
                    mask = self._recv_exact(4)
                else:
                    mask = None
                payload = self._recv_exact(length)
                if mask is not None:
                    payload = bytes(
                        value ^ mask[index % 4]
                        for index, value in enumerate(payload)
                    )
                if opcode == 0x8:
                    return None
                if opcode == 0x9:
                    self._send_masked(0xA, payload[:125])
                    continue
                if opcode != 0x1:
                    continue
                value = json.loads(payload.decode("utf-8"))
                if not isinstance(value, dict):
                    raise AssertionError("WebSocket JSON payload is not an object")
                return value
        except socket.timeout:
            return None

    def _send_masked(self, opcode: int, payload: bytes = b"") -> None:
        if len(payload) >= 126:
            raise AssertionError("test client only sends small control frames")
        mask = b"\x11\x22\x33\x44"
        masked = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
        self.client.sendall(
            bytes((0x80 | (opcode & 0x0F), 0x80 | len(payload)))
            + mask
            + masked
        )

    def close(self) -> None:
        try:
            self._send_masked(0x8)
            self.client.settimeout(0.5)
            self._recv_exact(2)
        except OSError:
            pass
        except (ConnectionError, socket.timeout):
            pass
        try:
            self.client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.client.close()


def _websocket_handshake(
    port: int,
    *,
    host_header: str | None = None,
    origin: str | None = None,
) -> tuple[int, str, _WebSocketClient | None]:
    client = socket.create_connection((HOST, port), timeout=4)
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    request = (
        "GET /studio/ws HTTP/1.1\r\n"
        f"Host: {host_header or f'{HOST}:{port}'}\r\n"
        f"Origin: {origin or f'http://{HOST}:{port}'}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        client.sendall(request)
        headers, remainder = _recv_headers(client)
        status_line = headers.partition("\r\n")[0]
        status = int(status_line.split()[1])
        if status != 101:
            content_length = 0
            for header_line in headers.split("\r\n")[1:]:
                name, separator, value = header_line.partition(":")
                if separator and name.casefold() == "content-length":
                    content_length = int(value.strip())
                    break
            while len(remainder) < content_length:
                chunk = client.recv(content_length - len(remainder))
                if not chunk:
                    break
                remainder += chunk
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
            return status, headers, None
        expected_accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        if f"Sec-WebSocket-Accept: {expected_accept}" not in headers:
            raise AssertionError("WebSocket accept value does not match RFC 6455")
        return status, headers, _WebSocketClient(client, remainder)
    except Exception:
        client.close()
        raise


def _wait_for_task(port: int, *, timeout: float = 3.0) -> str:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        status, value = _request_json(port, "/studio/task_id")
        if status == 200 and isinstance(value, dict):
            task_id = str(value.get("task_id", ""))
            if task_id:
                return task_id
        sleep(0.01)
    return ""


def _collect_messages(
    client: _WebSocketClient,
    generation: Future[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    deadline = monotonic() + 5.0
    while monotonic() < deadline:
        message = client.read_json(timeout=0.35)
        if message is not None and message.get("type") == "progress":
            messages.append(message)
        if generation.done():
            break
    return messages


def _ordered(messages: list[dict[str, Any]]) -> bool:
    if len(messages) < 2:
        return False
    fractions = [float(message.get("progress", -1)) for message in messages]
    steps = [int(message.get("step", -1)) for message in messages]
    return (
        fractions == sorted(fractions)
        and steps == sorted(steps)
        and all(0 <= value <= 1 for value in fractions)
    )


def _port_released(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main() -> int:
    result_root = (
        WORKSPACE_ROOT
        / "Evidence"
        / "studio-real-adapter-contracts"
        / "loopback-results"
    )
    result_root.mkdir(parents=True, exist_ok=True)
    for stale in result_root.glob("*"):
        if stale.is_file():
            stale.unlink()
    presentation = StudioPresentation(
        StudioApplication(
            MockBackend(
                event_interval_seconds=0.12,
                result_directory=result_root,
            ),
            result_root=result_root,
        ),
        GenerationRequest,
    )
    server = _StudioHTTPServer((HOST, 0), presentation)
    port = int(server.server_address[1])
    thread = Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.02},
        name="studio-s07-loopback-validation",
    )
    thread.start()

    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    error: str | None = None
    try:
        shell_status, shell, shell_headers = _request(port, "/studio/")
        css_status, css, _css_headers = _request(
            port,
            "/studio/static/app.css?v=4.17.0",
        )
        csp = shell_headers.get("content-security-policy", "")
        checks["canonical_static_assets"] = all(
            (
                shell_status == 200,
                css_status == 200,
                hashlib.sha256(shell).hexdigest() == EXPECTED_INDEX_SHA256,
                hashlib.sha256(css).hexdigest() == EXPECTED_CSS_SHA256,
                len(tuple(
                    path
                    for path in FRONTEND_ROOT.rglob("*")
                    if path.is_file()
                )) == 65,
            )
        )
        checks["csp_local_only"] = all(
            (
                "connect-src 'self'" in csp,
                "font-src 'self'" in csp,
                "fonts.googleapis.com" not in csp,
                "fonts.gstatic.com" not in csp,
                "Mw6E3LfyVV2UZb4lR2c68Mtde9nebxdw0TM29Y29r/o=" in csp,
            )
        )

        models_status, models = _request_json(port, "/studio/models")
        current_status, current = _request_json(port, "/studio/current_model")
        checks["model_population"] = all(
            (
                models_status == 200,
                isinstance(models, list),
                bool(models),
            )
        )
        checks["model_reads_are_pure"] = all(
            (
                current_status == 200,
                isinstance(current, dict),
                not current.get("title") if isinstance(current, dict) else False,
            )
        )
        details["source_model_count"] = len(models) if isinstance(models, list) else 0
        selected_title = (
            str(models[0].get("title", ""))
            if isinstance(models, list)
            and models
            and isinstance(models[0], dict)
            else ""
        )
        load_status, load_result = _request_json(
            port,
            "/studio/load_model",
            method="POST",
            payload={"title": selected_title},
            origin=f"http://{HOST}:{port}",
        )
        loaded_current_status, loaded_current = _request_json(
            port,
            "/studio/current_model",
        )
        checks["explicit_model_load_before_generation"] = all(
            (
                bool(selected_title),
                load_status == 200,
                isinstance(load_result, dict),
                load_result.get("ok") is True,
                load_result.get("title") == selected_title,
                loaded_current_status == 200,
                isinstance(loaded_current, dict),
                loaded_current.get("title") == selected_title,
            )
        )
        details["selected_model_title"] = selected_title

        negative_prompt = "low quality, watermark"
        negative_only_status, negative_only_result = _source_generate(
            port,
            "",
            748,
            negative_prompt=negative_prompt,
        )
        negative_only_infotexts = negative_only_result.get("infotexts", [])
        checks["empty_positive_negative_only_generation"] = all(
            (
                negative_only_status == 200,
                negative_only_result.get("error") is None,
                negative_only_result.get("notice") is None,
                bool(negative_only_result.get("images")),
                negative_only_result.get("seed") == 748,
                isinstance(negative_only_infotexts, list),
                bool(negative_only_infotexts),
                f"Negative prompt: {negative_prompt}"
                in str(negative_only_infotexts[0])
                if negative_only_infotexts
                else False,
            )
        )

        recognized_ignored = {
            "sampler": "Euler a",
            "scheduler": "simple",
            "enable_hr": True,
            "unknown_metadata": "must not appear in the notice",
        }
        ignored_status, ignored_result = _source_generate(
            port,
            "recognized ignored parameter notice",
            749,
            extra_parameters=recognized_ignored,
        )
        expected_notice = (
            "Ignored unsupported settings: sampler, scheduler, enable_hr."
        )
        checks["recognized_ignored_parameters_notice"] = all(
            (
                ignored_status == 200,
                ignored_result.get("error") is None,
                bool(ignored_result.get("images")),
                ignored_result.get("notice") == expected_notice,
                "unknown_metadata"
                not in str(ignored_result.get("notice", "")),
            )
        )
        details["recognized_ignored_notice"] = ignored_result.get("notice")

        capability_alignment = 8
        capability_width = 65 * capability_alignment
        capability_height = 97 * capability_alignment
        dimension_status, dimension_result = _source_generate(
            port,
            "capability-derived exact dimensions",
            750,
            width=capability_width,
            height=capability_height,
        )
        dimension_settings = dimension_result.get("settings", {})
        dimension_infotexts = dimension_result.get("infotexts", [])
        dimension_images = dimension_result.get("images", [])
        dimension_svg = ""
        if (
            isinstance(dimension_images, list)
            and dimension_images
            and isinstance(dimension_images[0], str)
            and "," in dimension_images[0]
        ):
            try:
                dimension_svg = base64.b64decode(
                    dimension_images[0].split(",", 1)[1]
                ).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                dimension_svg = ""
        checks["capability_dimensions_preserved_exactly"] = all(
            (
                (capability_width, capability_height) == (520, 776),
                capability_width % capability_alignment == 0,
                capability_height % capability_alignment == 0,
                capability_width % 64 != 0,
                capability_height % 64 != 0,
                dimension_status == 200,
                dimension_result.get("error") is None,
                isinstance(dimension_settings, dict),
                dimension_settings.get("width") == capability_width,
                dimension_settings.get("height") == capability_height,
                dimension_settings.get("seed") == 750,
                dimension_settings.get("model_id")
                == "studio-mock-illustration-v1",
                dimension_settings.get("fixture_kind")
                == "deterministic-programmatic-svg",
                isinstance(dimension_infotexts, list),
                bool(dimension_infotexts),
                f"Size: {capability_width}x{capability_height}"
                in str(dimension_infotexts[0])
                if dimension_infotexts
                else False,
                f'width="{capability_width}"' in dimension_svg,
                f'height="{capability_height}"' in dimension_svg,
            )
        )
        details["capability_dimensions"] = {
            "alignment": capability_alignment,
            "requested": {
                "width": capability_width,
                "height": capability_height,
            },
            "returned_settings": {
                "width": (
                    dimension_settings.get("width")
                    if isinstance(dimension_settings, dict)
                    else None
                ),
                "height": (
                    dimension_settings.get("height")
                    if isinstance(dimension_settings, dict)
                    else None
                ),
            },
        }

        # --- read-only capability route over real HTTP -------------------
        # Residency before the reads. A model was already loaded earlier in
        # this run, so purity means "unchanged", not "nothing loaded".
        current_before_capability = _request_json(
            port,
            "/studio/current_model",
        )[1]
        capability_status, capability_body = _request_json(
            port,
            "/studio/capability?model_id=studio-mock-illustration-v1"
            "&operation=txt2img",
        )
        capability_missing, _missing_body = _request_json(
            port,
            "/studio/capability",
        )
        capability_unknown, _unknown_body = _request_json(
            port,
            "/studio/capability?model_id=absent-model",
        )
        capability_foreign, _foreign_body, _foreign_headers = _request(
            port,
            "/studio/capability?model_id=studio-mock-illustration-v1",
            host="studio.example.invalid",
        )
        current_after_capability = _request_json(
            port,
            "/studio/current_model",
        )[1]
        checks["capability_route_read_only"] = all(
            (
                capability_status == 200,
                isinstance(capability_body, dict),
                capability_body.get("model_id")
                == "studio-mock-illustration-v1",
                capability_body.get("operation") == "txt2img",
                capability_body.get("device_type") == "cpu",
                capability_body.get("dtype_policy") == "fp32",
                capability_body.get("attention_backend") == "backend_default",
                capability_body.get("dimension_alignment") == 1,
                capability_body.get("minimum_dimension") == 1,
                capability_body.get("maximum_dimension") is None,
                capability_body.get("normalizes_dimensions") is False,
                capability_body.get("is_mock") is True,
                capability_missing == 400,
                capability_unknown == 400,
                capability_foreign == 403,
                # A pure read must leave residency exactly as it found it.
                isinstance(current_after_capability, dict),
                current_after_capability == current_before_capability,
            )
        )
        details["capability_route"] = {
            "ok_status": capability_status,
            "missing_parameter_status": capability_missing,
            "unknown_model_status": capability_unknown,
            "foreign_host_status": capability_foreign,
            "device_type": capability_body.get("device_type")
            if isinstance(capability_body, dict)
            else None,
        }

        # --- contained same-origin result delivery -----------------------
        delivery_entries = dimension_result.get("session_entries", [])
        delivery_handle = (
            delivery_entries[0].get("path")
            if isinstance(delivery_entries, list)
            and delivery_entries
            and isinstance(delivery_entries[0], dict)
            else ""
        )
        delivery_status, delivery_bytes, delivery_headers = _request(
            port,
            "/studio/file?path=" + quote(str(delivery_handle), safe=""),
        )
        stored = sorted(result_root.glob("*.svg"))
        expected_bytes = stored[-1].read_bytes() if stored else b""
        checks["result_delivery_same_origin"] = all(
            (
                isinstance(delivery_entries, list),
                len(delivery_entries) == 1,
                delivery_entries[0].get("source") == "scratch",
                bool(
                    re.fullmatch(
                        r"studio-result/[0-9a-f]{32}\.svg",
                        str(delivery_handle),
                    )
                ),
                delivery_status == 200,
                delivery_bytes == expected_bytes,
                delivery_headers.get("content-type") == "image/svg+xml",
                delivery_headers.get("content-length")
                == str(len(expected_bytes)),
                "sandbox" in delivery_headers.get(
                    "content-security-policy", ""
                ),
                "default-src 'none'" in delivery_headers.get(
                    "content-security-policy", ""
                ),
                delivery_headers.get("x-content-type-options") == "nosniff",
                delivery_headers.get("cache-control") == "private, no-store",
            )
        )
        serialized_response = json.dumps(dimension_result)
        checks["result_delivery_no_path_leak"] = all(
            (
                "output_path" not in serialized_response,
                "metadata_path" not in serialized_response,
                str(result_root) not in serialized_response,
                result_root.name not in serialized_response,
                not any(
                    str(result_root) in str(value)
                    for value in delivery_headers.values()
                ),
                not any(
                    stored[-1].name in str(value)
                    for value in delivery_headers.values()
                )
                if stored
                else True,
            )
        )
        hostile_handles = (
            "studio-result/../../secret.svg",
            "studio-result/..%2f..%2fsecret.svg",
            "studio-result/..%252f..%252fsecret.svg",
            "/etc/passwd",
            "C:/Windows/win.ini",
            str(stored[-1]) if stored else "absent",
            "studio-result/" + "0" * 32 + ".svg",
            "",
        )
        hostile_statuses = []
        for candidate in hostile_handles:
            status, _body, _headers = _request(
                port,
                "/studio/file?path=" + quote(candidate, safe=""),
            )
            hostile_statuses.append(status)
        checks["result_delivery_rejects_paths_and_unknown_ids"] = all(
            status in {404, 410} for status in hostile_statuses
        )
        details["result_delivery_hostile_statuses"] = dict(
            zip(hostile_handles, hostile_statuses)
        )
        foreign_delivery_status, _body, _headers = _request(
            port,
            "/studio/file?path=" + quote(str(delivery_handle), safe=""),
            host="studio.example.invalid",
        )
        checks["result_delivery_foreign_host_rejected"] = (
            foreign_delivery_status == 403
        )
        details["result_delivery"] = {
            "handle_shape": "studio-result/<32 hex>.<ext>",
            "status": delivery_status,
            "byte_length": len(delivery_bytes),
            "content_type": delivery_headers.get("content-type"),
        }

        ws_status, _headers, ws_client = _websocket_handshake(port)
        checks["websocket_loopback_connection"] = (
            ws_status == 101 and ws_client is not None
        )
        if ws_client is not None:
            ws_client.close()

        foreign_status, _headers, _client = _websocket_handshake(
            port,
            host_header=f"foreign.example:{port}",
        )
        checks["websocket_foreign_host_rejected"] = foreign_status == 403

        origin_status, _headers, _client = _websocket_handshake(
            port,
            origin="https://foreign.example",
        )
        checks["websocket_foreign_origin_rejected"] = origin_status == 403

        first_status, _headers, first_observer = _websocket_handshake(port)
        second_status, _headers, second_observer = _websocket_handshake(port)
        if first_observer is None or second_observer is None:
            raise AssertionError("progress observers could not connect")
        with ThreadPoolExecutor(max_workers=3) as executor:
            generation = executor.submit(
                _source_generate,
                port,
                "ordered two-observer progress",
                741,
            )
            first_future = executor.submit(
                _collect_messages,
                first_observer,
                generation,
            )
            second_future = executor.submit(
                _collect_messages,
                second_observer,
                generation,
            )
            generation_status, generation_result = generation.result(timeout=6)
            first_messages = first_future.result(timeout=2)
            second_messages = second_future.result(timeout=2)
        first_observer.close()
        second_observer.close()
        checks["websocket_progress_ordered"] = all(
            (
                first_status == 101,
                second_status == 101,
                _ordered(first_messages),
                _ordered(second_messages),
                generation_status == 200,
                generation_result.get("error") is None,
                bool(generation_result.get("images")),
            )
        )
        first_tasks = {
            str(message.get("task_id", ""))
            for message in first_messages
            if message.get("task_id")
        }
        second_tasks = {
            str(message.get("task_id", ""))
            for message in second_messages
            if message.get("task_id")
        }
        checks["websocket_two_observers_non_consuming"] = all(
            (
                len(first_messages) >= 2,
                len(second_messages) >= 2,
                bool(first_tasks),
                first_tasks == second_tasks,
            )
        )
        details["observer_message_counts"] = [
            len(first_messages),
            len(second_messages),
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            cancellation_generation = executor.submit(
                _source_generate,
                port,
                "terminal cancellation",
                742,
            )
            cancellation_task = _wait_for_task(port)
            interrupt_status, interrupt = _request_json(
                port,
                "/studio/interrupt",
                method="POST",
                payload={},
                origin=f"http://{HOST}:{port}",
            )
            cancelled_status, cancelled_result = cancellation_generation.result(
                timeout=6
            )
        task_status, task_after_cancel = _request_json(port, "/studio/task_id")
        checks["cancellation_terminal"] = all(
            (
                bool(cancellation_task),
                interrupt_status == 200,
                isinstance(interrupt, dict),
                interrupt.get("state") == "cancelled",
                cancelled_status == 200,
                isinstance(cancelled_result, dict),
                "cancel" in str(cancelled_result.get("error", "")).casefold(),
                task_status == 200,
                isinstance(task_after_cancel, dict),
                not task_after_cancel.get("task_id"),
            )
        )

        failure_status, failure_result = _source_generate(
            port,
            "__mock_fail__",
            743,
        )
        checks["controlled_failure"] = all(
            (
                failure_status == 200,
                "controlled mock backend failure"
                in str(failure_result.get("error", "")).casefold(),
            )
        )
        recovery_status, recovery_result = _source_generate(
            port,
            "recovery after controlled failure",
            744,
        )
        checks["recovery_second_generation"] = all(
            (
                recovery_status == 200,
                recovery_result.get("error") is None,
                bool(recovery_result.get("images")),
            )
        )

        disconnect_status, _headers, disconnect_client = _websocket_handshake(port)
        if disconnect_client is None:
            raise AssertionError("disconnect observer could not connect")
        with ThreadPoolExecutor(max_workers=1) as executor:
            disconnect_generation = executor.submit(
                _source_generate,
                port,
                "observer disconnect isolation",
                745,
            )
            disconnect_message = disconnect_client.read_json(timeout=2)
            disconnect_client.close()
            disconnect_result_status, disconnect_result = (
                disconnect_generation.result(timeout=6)
            )
        repeat_status, repeat_result = _source_generate(
            port,
            "generation after observer disconnect",
            746,
        )
        checks["disconnect_preserves_job_state"] = all(
            (
                disconnect_status == 101,
                isinstance(disconnect_message, dict),
                disconnect_message.get("type") == "progress",
                disconnect_result_status == 200,
                disconnect_result.get("error") is None,
                bool(disconnect_result.get("images")),
                repeat_status == 200,
                repeat_result.get("error") is None,
                bool(repeat_result.get("images")),
            )
        )

        legacy_submit_status, legacy_submit = _request_json(
            port,
            "/api/generate",
            method="POST",
            payload={
                "model": "studio-mock-illustration-v1",
                "positive_prompt": "legacy rollback polling",
                "negative_prompt": "",
                "seed": 747,
                "steps": 4,
                "cfg_scale": 5.0,
                "width": 512,
                "height": 512,
            },
            origin=f"http://{HOST}:{port}",
        )
        legacy_job_id = (
            str(legacy_submit.get("job_id", ""))
            if isinstance(legacy_submit, dict)
            else ""
        )
        legacy_events: list[dict[str, Any]] = []
        deadline = monotonic() + 4.0
        while legacy_job_id and monotonic() < deadline:
            poll_status, poll_value = _request_json(
                port,
                f"/api/jobs/{legacy_job_id}",
            )
            if poll_status != 200 or not isinstance(poll_value, dict):
                break
            legacy_events.append(poll_value)
            if str(poll_value.get("state", "")).casefold() in TERMINAL_STATES:
                break
            sleep(0.03)
        checks["legacy_polling_surface"] = all(
            (
                legacy_submit_status == 202,
                bool(legacy_job_id),
                bool(legacy_events),
                legacy_events[-1].get("state") == "completed",
            )
        )
        details["legacy_poll_count"] = len(legacy_events)

        foreign_http_status, _value = _request_json(
            port,
            "/studio/models",
            host=f"foreign.example:{port}",
        )
        origin_http_status, _value = _request_json(
            port,
            "/studio/interrupt",
            method="POST",
            payload={},
            origin="https://foreign.example",
        )
        checks["http_host_and_origin_rejection"] = (
            foreign_http_status == 403 and origin_http_status == 403
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        server.shutdown()
        server.server_close()
        presentation.shutdown()
        thread.join(timeout=3)
        checks["clean_shutdown"] = not thread.is_alive()
        checks["port_release"] = _port_released(port)

    report = {
        "schema_version": "studio-pre-adapter-corrections-loopback/v1",
        "bind_host": HOST,
        "external_network_access": False,
        "third_party_websocket_dependency": False,
        "checks": checks,
        "details": details,
        "error": error,
        "passed": error is None and bool(checks) and all(checks.values()),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(OUTPUT_PATH)
    print("PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
