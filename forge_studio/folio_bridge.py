"""Send to Folio: Gallery images into Folio's Images tray. F1.

Folio is the owner's page-layout app (comic assembly). Its launcher accepts
images from local programs over HTTP; the contract is Folio's, agreed with
the Folio session on 2026-10-04 (`docs/STUDIO_HANDSHAKE.md` in the Folio
folder, protocol 1):

  * discovery: `launcher.json` in Folio's data folder names a port and a
    per-start token; it can be stale, so `GET /__folio` must answer
    `{"app": "folio", "protocol": 1}` before anything is sent;
  * sending: one `POST /__folio/inbox?name=` per image, in selection order,
    waiting for each reply, the original bytes unmodified, `X-Folio-Token`,
    an image `Content-Type`, and NO `Origin` header -- Folio refuses browser
    origins, which is why Studio's backend sends and the page does not.

Studio-side rules, the Gallery's own: the page names images by row id only,
and the path is read from the index here (`gallery_actions`' rule). Only
127.0.0.1 is ever contacted; the file supplies a port and a token, never a
host. Folio is never started from Studio.

Source review: Evidence/source-review/F1-send-to-folio.md
"""

from __future__ import annotations

import http.client
import json
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

HOST = "127.0.0.1"
PROTOCOL = 1
#: Folio refuses larger; checked here so a huge file fails without a transfer.
MAX_BYTES = 100 * 1024 * 1024
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif"}
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_DISCOVERY_TIMEOUT = 3.0
_SEND_TIMEOUT = 60.0

NOT_RUNNING = "Folio isn't running. Start Folio, then try again."
SENDER = "Studio"


class FolioUnavailable(Exception):
    """Folio cannot take anything right now; the reason is owner-facing."""


def data_folder(env: Any = None, *, os_name: str | None = None, home: Any = None) -> Path:
    """The contract's data folder: FOLIO_DATA, else the system's own.

    Decided by what the machine HAS, not by its name (Studio's owned code
    does not branch on the platform): `os.name` separates Windows' APPDATA
    from POSIX, and a home with `Library/Application Support` is a Mac.
    """

    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    if env.get("FOLIO_DATA"):
        return Path(env["FOLIO_DATA"])
    if (os_name or os.name) == "nt":
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "Folio"
    support = home / "Library" / "Application Support"
    if support.is_dir():
        return support / "Folio"
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "Folio"


def installed(folder: Path | None = None) -> bool:
    """Folio creates its data folder the first time it runs. A machine that
    has never run Folio has none, and is never offered Send to Folio."""

    try:
        return (folder or data_folder()).is_dir()
    except OSError:
        return False


def _read_launcher(folder: Path) -> tuple[int, str]:
    target = folder / "launcher.json"
    try:
        if not target.is_file() or target.stat().st_size > 4096:
            raise FolioUnavailable(NOT_RUNNING)
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FolioUnavailable(NOT_RUNNING) from None
    if not isinstance(record, dict) or record.get("app") != "folio":
        raise FolioUnavailable(NOT_RUNNING)
    if record.get("protocol") != PROTOCOL:
        raise FolioUnavailable("This version of Folio uses a different hand-off. "
                               "Update Studio or Folio so they match.")
    port, token = record.get("port"), record.get("token")
    if (isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535
            or not isinstance(token, str) or not _TOKEN.match(token)):
        raise FolioUnavailable(NOT_RUNNING)
    return port, token


def _request(port: int, method: str, path: str, *, body: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: float) -> tuple[int, Any]:
    """One HTTP exchange with 127.0.0.1. `http.client` adds no Origin."""

    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read(64 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            payload = {}
        return response.status, payload
    finally:
        connection.close()


class FolioClient:
    def __init__(self, folder: Path | None = None,
                 request: Callable[..., tuple[int, Any]] = _request) -> None:
        self._folder = folder or data_folder()
        self._request = request
        self._target: tuple[int, str] | None = None

    def connect(self) -> tuple[int, str]:
        """Read the hint, then ask the port who it is."""

        port, token = _read_launcher(self._folder)
        try:
            status, reply = self._request(port, "GET", "/__folio", timeout=_DISCOVERY_TIMEOUT)
        except (OSError, http.client.HTTPException):
            raise FolioUnavailable(NOT_RUNNING) from None
        if status != 200 or not isinstance(reply, dict) or reply.get("app") != "folio" \
                or reply.get("protocol") != PROTOCOL:
            raise FolioUnavailable(NOT_RUNNING)
        self._target = (port, token)
        return self._target

    def send(self, data: bytes, name: str, content_type: str) -> None:
        """One image. A 401 means a restarted Folio: re-read once, retry once."""

        for attempt in (1, 2):
            port, token = self._target or self.connect()
            try:
                # `from=` names the sender in Folio's note ("N pictures from
                # Studio"); optional in the contract, 40 characters at most.
                status, _ = self._request(
                    port, "POST", "/__folio/inbox?name=" + quote(name, safe="") + "&from=" + SENDER,
                    body=data,
                    headers={"X-Folio-Token": token, "Content-Type": content_type,
                             "Content-Length": str(len(data))},
                    timeout=_SEND_TIMEOUT)
            except (OSError, http.client.HTTPException):
                raise FolioUnavailable(NOT_RUNNING) from None
            if status == 201:
                return
            if status == 401 and attempt == 1:
                self._target = None
                continue
            raise _refusal_for(status)


class _ItemRefused(Exception):
    """This one image was not taken; the rest may still go."""


def _refusal_for(status: int) -> Exception:
    if status == 507:
        # Folio says 507 at 200 pictures OR 500 MB waiting, so no number here.
        return FolioUnavailable("Folio's Images tray is full. Place some pictures, "
                                "then send the rest.")
    if status == 413:
        return _ItemRefused("Larger than Folio accepts (100 MB).")
    if status == 400:
        return _ItemRefused("Folio couldn't read this image.")
    if status == 401:
        return FolioUnavailable("Folio didn't accept Studio's key. Restart Folio, "
                                "then try again.")
    return FolioUnavailable(f"Folio refused the image ({status}).")


def send_images(targets: list[tuple[Any, Path, str]], client: FolioClient | None = None) -> dict[str, Any]:
    """Send `[(id, path, filename)]` in order; report each outcome.

    Not all-or-nothing, like the Gallery's other bulk actions: an image Folio
    cannot take is named and the rest still go. Folio becoming unreachable or
    full stops the batch, and every image not yet sent is reported with why.
    """

    client = client or FolioClient()
    sent = 0
    failures: list[dict[str, Any]] = []
    stopped: str | None = None
    for image_id, path, filename in targets:
        if stopped is not None:
            failures.append({"id": image_id, "error": stopped})
            continue
        content_type = CONTENT_TYPES.get(path.suffix.lower())
        if content_type is None:
            failures.append({"id": image_id, "error": "Folio takes PNG, JPEG, WebP or GIF images."})
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                failures.append({"id": image_id, "error": "Larger than Folio accepts (100 MB)."})
                continue
            data = path.read_bytes()
        except OSError:
            failures.append({"id": image_id, "error": "The file could not be read."})
            continue
        try:
            client.send(data, filename, content_type)
            sent += 1
        except _ItemRefused as refusal:
            failures.append({"id": image_id, "error": str(refusal)})
        except FolioUnavailable as unavailable:
            stopped = str(unavailable)
            failures.append({"id": image_id, "error": stopped})
    reply: dict[str, Any] = {"ok": not failures, "sent": sent, "failed": len(failures),
                             "failures": failures}
    if stopped is not None:
        reply["error"] = stopped
    return reply
