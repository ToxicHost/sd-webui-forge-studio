"""ST1c. Stage's downloadable models, each fetched only on the owner's request.

ST1c: the Anima 3D-pose adapter. ST7: SAM 3D Body, which reads a pose from a
picture for Stage's "Pose from a picture" (opt-in: 2.8 GB, Meta's SAM License).

Stage renders its figure the way toyxyz's `anima_3dpose_6200` was trained, and on
Anima a Stage unit wants that adapter. It is not bundled: it is a derivative of
Anima under the CircleStone Labs Non-Commercial License (owner's decision,
2026-11-06: download on first use, the licence noted in the Codex's Licensing
entry). Studio goes online only when the owner presses the unit's download
button; nothing here runs on its own.

One file, pinned to an exact revision and checked against its published SHA-256
before it is put in place, so a changed or truncated download never reaches the
ControlNet folder. Review: `Evidence/source-review/ST1-stage.md` (ST1c).
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: Repository revision 37f3a4b6 (2026-11-07). The file's SHA-256 and size are
#: the ones Hugging Face publishes for it (X-Linked-ETag / X-Linked-Size) and
#: match the copy the owner's E0/E1 runs used.
POSE_MODEL_NAME = "anima_3dpose_6200.safetensors"
POSE_MODEL_URL = ("https://huggingface.co/toyxyz/Anima_ControlNet-LLLite/resolve/"
                  "37f3a4b6574f1dce3dfe3b5440285a39b11b3d8d/anima_3dpose_6200.safetensors")
POSE_MODEL_SHA256 = "97542346a658d23ca65368b568aa9aaf2486ccda8942612983aaffdc986cd495"
POSE_MODEL_SIZE = 51_133_576
POSE_MODEL_LICENCE = "https://huggingface.co/circlestone-labs/Anima/blob/main/LICENSE.md"

#: ST7. SAM 3D Body (Meta; SAM License, which allows redistribution under its
#: own terms) as Comfy-Org repackaged it: safetensors, no gate, repository
#: revision 7c974aea (2026-09-23). SHA-256 and size are what Hugging Face
#: publishes (X-Linked-ETag / X-Linked-Size) and match the copy E7 measured.
#: It goes in its own folder beside the ControlNet folder, never inside it (a
#: ControlNet folder's files are listed as ControlNet models).
PICTURE_MODEL_NAME = "sam_3d_body_dinov3_bf16.safetensors"
PICTURE_MODEL_URL = ("https://huggingface.co/Comfy-Org/sam-3d-body/resolve/"
                     "7c974aea58e062252a6cb5b6f9b7f639d545b729/detection/sam_3d_body_dinov3_bf16.safetensors")
PICTURE_MODEL_SHA256 = "59fa45200c504c5b56625004d7d3385daf48c616613e88099e43bf83b3e249cf"
PICTURE_MODEL_SIZE = 2_830_737_652
PICTURE_MODEL_LICENCE = "https://huggingface.co/facebook/sam-3d-body-dinov3/blob/main/LICENSE"
PICTURE_MODEL_FOLDER = "SAM3DBody"

_CHUNK = 1024 * 1024
_TIMEOUT = 60


class StageAssetError(Exception):
    """Named for the owner: why the download cannot start or did not finish."""


@dataclass
class _State:
    state: str = "idle"          # idle | downloading | done | failed
    received: int = 0
    error: str = ""


Opener = Callable[[str], Any]


def _default_opener(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Forge-Studio"})
    return urllib.request.urlopen(request, timeout=_TIMEOUT)


class PoseModelFetcher:
    """One download at a time, in a background thread; the page asks for status.

    The file's name, address, hash, size and licence are read when used (from
    `_spec`), not captured when the fetcher is made.
    """

    def _spec(self) -> tuple[str, str, str, int, str]:
        return POSE_MODEL_NAME, POSE_MODEL_URL, POSE_MODEL_SHA256, POSE_MODEL_SIZE, POSE_MODEL_LICENCE

    def __init__(self, opener: Opener | None = None) -> None:
        self._opener = opener or _default_opener
        self._lock = threading.Lock()
        self._state = _State()
        self._thread: threading.Thread | None = None

    def status(self, folder: Path | None) -> dict[str, Any]:
        name, _url, _sha, size, licence = self._spec()
        with self._lock:
            state = _State(**vars(self._state))
        installed = bool(folder) and (folder / name).is_file()
        return {
            "name": name,
            "installed": installed,
            "state": state.state,
            "received": state.received,
            "total": size,
            "error": state.error,
            "licence": licence,
            "folder_configured": folder is not None,
        }

    def installed_path(self, folder: Path | None) -> Path | None:
        """The file, when it is in place."""
        if folder is None:
            return None
        path = folder / self._spec()[0]
        return path if path.is_file() else None

    def start(self, folder: Path | None, on_done: Callable[[], None] | None = None) -> dict[str, Any]:
        if folder is None:
            raise StageAssetError(
                "No ControlNet folder is configured. Add one in Settings > Model folders, then try again.")
        if (folder / self._spec()[0]).is_file():
            return self.status(folder)
        with self._lock:
            if self._state.state == "downloading":
                return self.status(folder)
            self._state = _State(state="downloading")
            self._thread = threading.Thread(target=self._run, args=(folder, on_done),
                                            name="stage-pose-model", daemon=True)
            self._thread.start()
        return self.status(folder)

    def wait(self, timeout: float | None = None) -> None:
        """For tests: block until the current download ends."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, folder: Path, on_done: Callable[[], None] | None) -> None:
        name, url, sha256, size, _licence = self._spec()
        part = folder / (name + ".part")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            received = 0
            with self._opener(url) as reply, part.open("wb") as out:
                while chunk := reply.read(_CHUNK):
                    received += len(chunk)
                    if received > size:
                        raise StageAssetError("The download is larger than the published file.")
                    digest.update(chunk)
                    out.write(chunk)
                    with self._lock:
                        self._state.received = received
            if received != size or digest.hexdigest() != sha256:
                raise StageAssetError(
                    "The downloaded file does not match the published one, so it was not kept.")
            os.replace(part, folder / name)
            with self._lock:
                self._state.state = "done"
            if on_done is not None:
                try:
                    on_done()
                except Exception:  # noqa: BLE001 - a refresh failure is not a download failure
                    pass
        except Exception as error:  # noqa: BLE001 - named for the owner, below
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            message = str(error) if isinstance(error, StageAssetError) else (
                f"The download failed: {type(error).__name__}: {error}")
            with self._lock:
                self._state.state = "failed"
                self._state.error = message


def controlnet_folder(registry: Any) -> Path | None:
    """The first ControlNet folder that exists, or None.

    A role folder (`model_roots`) first; then, since Settings > Model folders
    turned role folders into library folders (the launcher migrates them), a
    library folder that holds ControlNet models, then one that was the
    ControlNet role folder (its hint). ST7 found the role-only lookup answered
    "No ControlNet folder" on a migrated config, for both downloads.
    """
    if registry is None:
        return None
    candidates: list[str] = []
    try:
        candidates += list(registry.configured_roots().get("controlnet", ()))
    except Exception:  # noqa: BLE001 - an unreadable registry means none configured
        pass
    try:
        candidates += list(registry.library_roots("controlnet"))
    except Exception:  # noqa: BLE001 - a registry without a library has none
        pass
    try:
        candidates += [folder.path for folder in registry.configured_library()
                       if getattr(folder, "hint", "") == "controlnet"]
    except Exception:  # noqa: BLE001 - as above
        pass
    for root in candidates:
        path = Path(root)
        if path.is_dir():
            return path
    return None


class PictureModelFetcher(PoseModelFetcher):
    """ST7. The same download, for SAM 3D Body."""

    def _spec(self) -> tuple[str, str, str, int, str]:
        return (PICTURE_MODEL_NAME, PICTURE_MODEL_URL, PICTURE_MODEL_SHA256, PICTURE_MODEL_SIZE,
                PICTURE_MODEL_LICENCE)


def picture_model_folder(registry: Any) -> Path | None:
    """ST7. Where SAM 3D Body lives: `SAM3DBody` beside the first ControlNet
    folder (Forge's layout puts model kinds side by side: models/ControlNet,
    models/SAM3DBody), or None when no ControlNet folder is configured."""
    folder = controlnet_folder(registry)
    return None if folder is None else folder.parent / PICTURE_MODEL_FOLDER


FETCHER = PoseModelFetcher()
PICTURE_FETCHER = PictureModelFetcher()
