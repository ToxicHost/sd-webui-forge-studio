"""Where captured attention lives: the sidecar store's names. HM1.

The owner's decision (mask design 7, 2026-10-11): captured data lives in
sidecar files in a folder of their own, away from the images, named by the
picture's `content_hash` and sharded by its first characters, and indexed
from the Gallery's `image_metadata` row. No PNG chunks.

Two writers, one convention, so it lives here:
- the generation port writes `<root>/pending/<request id>.attn.npz` as soon
  as the main pass ends (it cannot know the final hash: JPEG and WebP are
  hashed from the re-read file, `presentation._record_generation_metadata`);
- presentation, holding the hash of the delivered bytes, moves it to
  `<root>/attention/<h[:2]>/<h>.attn.npz` and records that relative name.

Two roots (HM2). `pending/` is Studio's own and transient, always under the
state root (`sidecar_root`), which is where the port was told to write at
load. The permanent `attention/` tree is the OWNER's: the folder named in
`<state>/sidecars/location.json`, or the same default (`store_root`). A
recorded name is relative, so the folder can move without rewriting a row.
Housekeeping is `forge_studio.attention_housekeeping`.

Why a file of its own and not a preference: the location may only change
together with the files, and only the move route does that (copy, verify,
then name the new folder). A page-writable preference would let any page
point the store at a folder that does not hold it.

Pure standard library: `forge_studio` is the purity-locked package. Every
name is validated, so neither a request id nor a stored row can steer a path
outside the root.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

SIDECARS = "sidecars"
PENDING = "pending"
ATTENTION = "attention"
SUFFIX = ".attn.npz"
LOCATION = "location.json"

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE = re.compile(r"^attention/[0-9a-f]{2}/[0-9a-f]{64}\.attn\.npz$")


def sidecar_root(state_root: Path | str) -> Path:
    """The default store, and always the pending area: inside Studio's own
    data area."""
    return Path(state_root) / SIDECARS


def read_location(state_root: Path | str) -> str:
    """The owner's folder as last moved to, or "" for the default. An
    unreadable file is the default: the store is never lost over it, only
    looked for where it started."""
    try:
        document = json.loads((sidecar_root(state_root) / LOCATION).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    folder = document.get("folder") if isinstance(document, dict) else None
    return folder if isinstance(folder, str) else ""


def write_location(state_root: Path | str, folder: str) -> None:
    """Name the store's folder ("" = the default). Atomic: a crash leaves
    either the old name or the new one, never half of either."""
    target = sidecar_root(state_root) / LOCATION
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"folder": folder}), encoding="utf-8")
    os.replace(temporary, target)


def store_root(state_root: Path | str, configured: object = None) -> Path:
    """Where adopted sidecars live: the owner's folder, or the default.

    `configured` is `read_location`'s answer. Anything but a non-empty
    absolute path means the default -- a relative path would resolve
    against whatever directory Studio happened to start in.
    """
    if isinstance(configured, str) and configured.strip():
        folder = Path(configured.strip())
        if folder.is_absolute():
            return folder
    return sidecar_root(state_root)


def pending_path(root: Path | str, request_id: str) -> Path | None:
    """Where the port leaves a job's capture, or None for an unusable id."""
    if not isinstance(request_id, str) or not _REQUEST_ID.match(request_id):
        return None
    return Path(root) / PENDING / f"{request_id}{SUFFIX}"


def relative_for(digest: str) -> str | None:
    """The store-relative name of a picture's sidecar, or None for a bad hash."""
    if not isinstance(digest, str) or not _DIGEST.match(digest):
        return None
    return f"{ATTENTION}/{digest[:2]}/{digest}{SUFFIX}"


def resolve(root: Path | str, relative: str) -> Path | None:
    """A recorded relative name back to a path under `root`, or None."""
    if not isinstance(relative, str) or not _RELATIVE.match(relative):
        return None
    return Path(root) / relative


def adopt(root: Path | str, request_id: str, digest: str,
          store: Path | str | None = None) -> str | None:
    """Move a job's pending capture (under `root`) into the store (`store`,
    default `root`), under its picture's identity.

    Returns the relative name to record, or None when there is nothing to
    adopt. A later picture with the same pixels replaces the earlier sidecar:
    same pixels, same seed and prompt in practice, and the newest capture is
    the one that described them. `shutil.move`, because the owner's folder
    may be on another drive, where a rename raises.
    """
    source = pending_path(root, request_id)
    relative = relative_for(digest)
    if source is None or relative is None or not source.is_file():
        return None
    target = Path(store if store is not None else root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    return relative


__all__ = (
    "ATTENTION",
    "LOCATION",
    "PENDING",
    "SIDECARS",
    "SUFFIX",
    "adopt",
    "pending_path",
    "read_location",
    "relative_for",
    "resolve",
    "sidecar_root",
    "store_root",
    "write_location",
)
