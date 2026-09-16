"""Durable crash recovery for the Canvas documents currently open. AR4.4.

THE DEFECT THIS CLOSES

`studio-docs.js` holds documents in JavaScript memory and nothing else. Its own
Codex entry says so: "the live canvas state is a window into the active
document, not a separate store." So a plain F5 destroys the owner's work.
Measured before this module existed, on one reload with no restart and no port
change:

    before   document c164f3...   256x320   cyan block   mask painted
    after    document 048efe...   768x768   gone         gone

The 89 session settings survived that reload. The artwork did not. That is data
loss on the most ordinary accidental keystroke in a browser, which is why the
owner's earlier "defer persistence" decision was withdrawn.

WHAT THIS IS, AND IS NOT

Crash recovery for the documents currently open -- enough to survive a refresh,
a renderer failure, or a restart on a different ephemeral port. NOT a document
library, and not a project-save format. An ordinary image export is not a
layered save and does not clear recovery; only an explicit discard or close
does.

WHY THE BLOBS ARE FILES AND NOT JSON

Layer pixels are megabytes. `_Document` in `preferences.py` is the right home
for settings and deliberately carries a 256 KB ceiling; forcing artwork through
it would either break that bound or inline base64 into a settings file. So each
document gets a DIRECTORY: a small JSON manifest describing structure, and one
PNG per pixel layer, mask and region beside it.

NO CEILINGS OF ANY KIND. Owner instruction, and it is load-bearing rather than
decorative: no image-dimension limit, no pixel limit, no document-size ceiling,
no recovery budget, no silent LRU eviction. A 4-layer 2048x2048 document is
roughly 20-30 MB of PNG, which is nothing beside the results already on a
working disk -- and silently dropping the owner's only copy of their work to
respect a number nobody chose would be far worse than using the space.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

logger = logging.getLogger("studio.recovery")

RECOVERY_DIRNAME = "recovery"
MANIFEST_FILENAME = "manifest.json"

RECOVERY_SCHEMA = "studio-canvas-recovery"
RECOVERY_SCHEMA_VERSION = 1

#: A document id is opaque: 32 hex characters, exactly as AR2.1 mints them.
#: Anchored, so a value carrying a separator, a drive letter or `..` cannot
#: match and cannot escape the recovery directory.
DOCUMENT_ID = re.compile(r"\A[0-9a-f]{32}\Z")

#: A blob name is a bare filename this module itself composes. The pattern is
#: what stops a manifest supplied by the page from naming `../../secrets`.
BLOB_NAME = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,63}\.png\Z")


class RecoveryError(Exception):
    """Base for every refusal this store makes."""


class UnknownDocument(RecoveryError):
    """No recovery exists for that document."""


class MalformedRecovery(RecoveryError):
    """The request could not be read as a recovery snapshot."""


class RecoveryNotWritten(RecoveryError):
    """The snapshot could not be stored, and the caller must be told.

    Named rather than swallowed. A recovery system that reports success while
    writing nothing is worse than one that is absent, because the owner stops
    saving manually.
    """


def _referenced_blobs(manifest: Mapping[str, Any]) -> set[str]:
    """Every blob name the manifest says the document is made of."""

    wanted: set[str] = set()
    for layer in manifest.get("layers") or []:
        if isinstance(layer, Mapping) and layer.get("blob"):
            wanted.add(str(layer["blob"]))
    mask = manifest.get("mask")
    if isinstance(mask, Mapping) and mask.get("blob"):
        wanted.add(str(mask["blob"]))
    for region in manifest.get("regions") or []:
        if isinstance(region, Mapping) and region.get("blob"):
            wanted.add(str(region["blob"]))
    return wanted


def _require_referenced_blobs(manifest: Mapping[str, Any],
                              present: Iterable[str]) -> None:
    """Refuse to seal a snapshot that is missing part of itself.

    An upload interrupted between two layers would otherwise commit a
    document missing one of them, REPLACING a good snapshot with a
    plausible-looking corruption of the owner's work -- the worst thing a
    recovery system can do, because it looks like it worked.
    """

    missing = sorted(_referenced_blobs(manifest) - set(present))
    if missing:
        raise MalformedRecovery(
            "The recovery snapshot is incomplete; it references "
            f"{len(missing)} blob(s) that were never written.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CanvasRecoveryStore:
    """One directory per open document, under the Studio state root."""

    def __init__(self, state_root: str | Path | None = None) -> None:
        self._root: Path | None = (
            None if state_root is None
            else Path(state_root) / RECOVERY_DIRNAME
        )
        #: Memory-only when no state root is resolved, matching `_Document`.
        #: A mock host and a real host must refuse the same things.
        self._memory: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @property
    def durable(self) -> bool:
        return self._root is not None

    # -- helpers ----------------------------------------------------------

    def _reclaim_interrupted_swap(self, document_id: str) -> None:
        """Put back a snapshot a crash caught mid-swap.

        `commit` moves the live directory aside before moving the staged one
        into place. A process killed BETWEEN those two renames leaves the last
        coherent snapshot sitting under `.old` and nothing at the real name.
        Reclaiming it is what makes "an interrupted write returns the last
        coherent state" true for the whole write, not just most of it.
        """

        if self._root is None:
            return
        target = self._document_dir(document_id)
        if target.exists():
            return
        for salvage in sorted(target.parent.glob(target.name + ".*.old")):
            if (salvage / MANIFEST_FILENAME).is_file():
                try:
                    salvage.replace(target)
                except OSError:
                    return
                return

    def _document_dir(self, document_id: str) -> Path:
        if not isinstance(document_id, str) or not DOCUMENT_ID.match(document_id):
            raise MalformedRecovery(
                "A document id must be 32 hexadecimal characters.")
        assert self._root is not None
        return self._root / document_id

    @staticmethod
    def _checked_blobs(blobs: Any) -> dict[str, bytes]:
        import base64

        if blobs is None:
            return {}
        if not isinstance(blobs, Mapping):
            raise MalformedRecovery("blobs must be a JSON object.")
        out: dict[str, bytes] = {}
        for name, value in blobs.items():
            if not isinstance(name, str) or not BLOB_NAME.match(name):
                raise MalformedRecovery(f"{name!r} is not a recovery blob name.")
            if not isinstance(value, str):
                raise MalformedRecovery(f"{name} must be a base64 data URL.")
            payload = value.split(",", 1)[-1]
            try:
                out[name] = base64.b64decode(payload, validate=True)
            except Exception as error:  # noqa: BLE001
                raise MalformedRecovery(
                    f"{name} is not valid base64.") from error
        return out

    # -- writes -----------------------------------------------------------

    def begin(self, document_id: str) -> None:
        """Open a fresh staging directory for one document.

        The write is STAGED ACROSS REQUESTS, one blob at a time, because a
        whole document does not fit in one HTTP body and the alternative would
        be a request-size limit -- which is exactly the document-size ceiling
        the owner forbade. A 4-layer 2048x2048 document is 20-30 MB of PNG and
        the source routes are bounded at 16 MB, so a single-request design
        would silently stop recovering the documents most worth recovering.
        """

        with self._lock:
            if self._root is None:
                self._memory.setdefault(
                    document_id, {"envelope": {}, "payloads": {}})
                if not DOCUMENT_ID.match(str(document_id)):
                    raise MalformedRecovery(
                        "A document id must be 32 hexadecimal characters.")
                self._memory[document_id] = {"envelope": {}, "payloads": {}}
                return
            staging = self._staging_dir(document_id)
            try:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise RecoveryNotWritten(
                    f"Recovery staging could not be opened: "
                    f"{type(error).__name__}") from error

    def put_blob(self, document_id: str, name: str, data_url: Any,
                 *, append: bool = False) -> int:
        """Write one blob, or one CHUNK of one, into the open staging area.

        `append` exists because the HTTP layer bounds a single source request
        at 16 MB, and a blob that had to arrive in one request would make that
        bound a document-size ceiling -- exactly what the owner forbade. A
        large layer therefore arrives as consecutive appends and no single
        request has to carry it.

        Nothing here caps the number of chunks, the size of a blob, or the
        size of a document. The only bound is the disk.
        """

        payloads = self._checked_blobs({name: data_url})
        blob = payloads[name]
        with self._lock:
            if self._root is None:
                entry = self._memory.setdefault(
                    document_id, {"envelope": {}, "payloads": {}})
                previous = entry["payloads"].get(name, b"") if append else b""
                entry["payloads"][name] = previous + blob
                return len(entry["payloads"][name])
            staging = self._staging_dir(document_id)
            if not staging.is_dir():
                raise MalformedRecovery(
                    "No recovery write is open for that document.")
            target = staging / name
            try:
                with target.open("ab" if append else "wb") as handle:
                    handle.write(blob)
                return target.stat().st_size
            except OSError as error:
                raise RecoveryNotWritten(
                    f"A recovery blob could not be written: "
                    f"{type(error).__name__}") from error

    def commit(self, document_id: str, manifest: Any) -> dict[str, Any]:
        """Seal the staged write and replace the previous snapshot.

        The swap happens only once every blob is on disk, so an interruption
        leaves the PREVIOUS complete snapshot rather than a document missing
        two of its five layers -- which would restore as a plausible-looking
        corruption of the owner's work, the worst outcome a recovery system
        can produce.
        """

        if not isinstance(manifest, Mapping):
            raise MalformedRecovery("manifest must be a JSON object.")
        with self._lock:
            if self._root is None:
                entry = self._memory.setdefault(
                    document_id, {"envelope": {}, "payloads": {}})
                _require_referenced_blobs(manifest, sorted(entry["payloads"]))
                envelope = self._envelope(document_id, manifest,
                                          sorted(entry["payloads"]))
                entry["envelope"] = envelope
                return envelope

            staging = self._staging_dir(document_id)
            if not staging.is_dir():
                raise MalformedRecovery(
                    "No recovery write is open for that document.")
            names = sorted(p.name for p in staging.iterdir()
                           if p.is_file() and BLOB_NAME.match(p.name))
            _require_referenced_blobs(manifest, names)
            envelope = self._envelope(document_id, manifest, names)
            target = self._document_dir(document_id)
            try:
                (staging / MANIFEST_FILENAME).write_text(
                    json.dumps(envelope, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                if target.exists():
                    doomed = target.with_name(target.name + f".{os.getpid()}.old")
                    shutil.rmtree(doomed, ignore_errors=True)
                    target.replace(doomed)
                    staging.replace(target)
                    shutil.rmtree(doomed, ignore_errors=True)
                else:
                    staging.replace(target)
            except OSError as error:
                shutil.rmtree(staging, ignore_errors=True)
                raise RecoveryNotWritten(
                    f"The recovery snapshot could not be sealed: "
                    f"{type(error).__name__}") from error
            return envelope

    def _staging_dir(self, document_id: str) -> Path:
        target = self._document_dir(document_id)
        return target.with_name(target.name + f".{os.getpid()}.writing")

    def _envelope(self, document_id: str, manifest: Mapping[str, Any],
                  names: list[str]) -> dict[str, Any]:
        return {
            "schema": RECOVERY_SCHEMA,
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "document_id": document_id,
            "updated_at": _utc_now(),
            "manifest": dict(manifest),
            "blobs": list(names),
        }

    def save(self, document_id: str, manifest: Any, blobs: Any) -> dict[str, Any]:
        """Store one document's recovery snapshot, replacing any previous one.

        WRITE-THEN-SWAP, at DIRECTORY granularity. Everything lands in a
        sibling `.writing` directory first and the old one is replaced only
        once every byte is down. An interrupted write therefore leaves the
        previous complete snapshot, never a document with three of its five
        layers -- which would restore as a plausible-looking corruption of the
        owner's work, the worst possible outcome for a recovery system.
        """

        if not isinstance(manifest, Mapping):
            raise MalformedRecovery("manifest must be a JSON object.")
        payloads = self._checked_blobs(blobs)
        envelope = {
            "schema": RECOVERY_SCHEMA,
            "schema_version": RECOVERY_SCHEMA_VERSION,
            "document_id": document_id,
            "updated_at": _utc_now(),
            "manifest": dict(manifest),
            "blobs": sorted(payloads),
        }

        with self._lock:
            if self._root is None:
                if not DOCUMENT_ID.match(str(document_id)):
                    raise MalformedRecovery(
                        "A document id must be 32 hexadecimal characters.")
                self._memory[document_id] = {
                    "envelope": envelope, "payloads": dict(payloads)}
                return envelope

            target = self._document_dir(document_id)
            staging = target.with_name(target.name + f".{os.getpid()}.writing")
            try:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True, exist_ok=True)
                for name, data in payloads.items():
                    (staging / name).write_bytes(data)
                (staging / MANIFEST_FILENAME).write_text(
                    json.dumps(envelope, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                # Replace the old directory only now.
                if target.exists():
                    doomed = target.with_name(target.name + f".{os.getpid()}.old")
                    shutil.rmtree(doomed, ignore_errors=True)
                    target.replace(doomed)
                    staging.replace(target)
                    shutil.rmtree(doomed, ignore_errors=True)
                else:
                    staging.replace(target)
            except OSError as error:
                shutil.rmtree(staging, ignore_errors=True)
                raise RecoveryNotWritten(
                    f"The recovery snapshot could not be written: "
                    f"{type(error).__name__}") from error
            return envelope

    def discard(self, document_id: str) -> None:
        """Remove a document's recovery entirely.

        Only an explicit discard or close reaches here. An ordinary image
        export is not a layered project save and must not clear recovery --
        the owner still has unsaved structure the export did not capture.
        """

        with self._lock:
            if self._root is None:
                self._memory.pop(document_id, None)
                return
            target = self._document_dir(document_id)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    # -- reads ------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        """Every recoverable document, newest first. Never raises.

        A directory that cannot be read is SKIPPED rather than fatal: one
        damaged document must not cost the owner the others, and startup must
        proceed either way.
        """

        if self._root is not None and self._root.is_dir():
            # A crash caught mid-swap left the good snapshot under `.old`.
            # Reclaim before listing, or the owner is told a document they
            # still have is gone -- which is the one lie this module must
            # never tell. `self._root` IS the recovery directory; appending
            # the directory name again looked right and silently globbed a
            # path that does not exist, so nothing was ever reclaimed.
            for stranded in sorted(self._root.glob("*.*.old")):
                name = stranded.name.split(".", 1)[0]
                if DOCUMENT_ID.match(name):
                    self._reclaim_interrupted_swap(name)

        with self._lock:
            if self._root is None:
                return [dict(entry["envelope"]) for entry in self._memory.values()]
            if not self._root.is_dir():
                return []
            found: list[dict[str, Any]] = []
            for child in self._root.iterdir():
                if not child.is_dir() or not DOCUMENT_ID.match(child.name):
                    continue
                try:
                    envelope = json.loads(
                        (child / MANIFEST_FILENAME).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    logger.warning(
                        "Recovery for one document could not be read and was "
                        "skipped; the others are unaffected.")
                    continue
                if (envelope.get("schema") != RECOVERY_SCHEMA
                        or envelope.get("schema_version", 0) > RECOVERY_SCHEMA_VERSION):
                    continue
                found.append(envelope)
            found.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
            return found

    def load(self, document_id: str) -> dict[str, Any]:
        """One document's manifest and its blobs, as base64 data URLs."""

        import base64

        with self._lock:
            self._reclaim_interrupted_swap(document_id)
            if self._root is None:
                entry = self._memory.get(document_id)
                if entry is None:
                    raise UnknownDocument("No recovery exists for that document.")
                return {
                    **entry["envelope"],
                    "blob_data": {
                        name: "data:image/png;base64," + base64.b64encode(data).decode()
                        for name, data in entry["payloads"].items()},
                }

            target = self._document_dir(document_id)
            manifest_path = target / MANIFEST_FILENAME
            if not manifest_path.is_file():
                raise UnknownDocument("No recovery exists for that document.")
            try:
                envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise UnknownDocument(
                    "That recovery snapshot could not be read.") from error
            data: dict[str, str] = {}
            for name in envelope.get("blobs", []):
                if not isinstance(name, str) or not BLOB_NAME.match(name):
                    continue
                blob = target / name
                if not blob.is_file():
                    # A named blob that is missing makes the snapshot
                    # incoherent. Refusing the whole document is right: half a
                    # document restored is a corruption the owner would have to
                    # notice themselves.
                    raise UnknownDocument(
                        "That recovery snapshot is incomplete.")
                data[name] = ("data:image/png;base64,"
                              + base64.b64encode(blob.read_bytes()).decode())
            return {**envelope, "blob_data": data}


__all__ = (
    "CanvasRecoveryStore",
    "MalformedRecovery",
    "RecoveryError",
    "RecoveryNotWritten",
    "UnknownDocument",
    "RECOVERY_SCHEMA",
    "RECOVERY_SCHEMA_VERSION",
)
