"""Keeping the attention sidecar store tidy: size, orphans, deletion, moving. HM2.

Design and source review: `Evidence/source-review/HM2-sidecar-settings.md`.
The owner's decision (mask design 7): "The folder location is a setting, with
a size readout. Deleting an image in Studio deletes its sidecars, and a
'clean up orphans' action handles the rest."

WHAT COUNTS AS AN ORPHAN, and why so little does. The Gallery indexes only the
folders the owner linked, so "no indexed picture has this hash" is not proof a
picture is gone -- a picture in an unlinked save folder would look exactly
like that while it sits on the disk. A sidecar is an orphan only when Studio
KNOWS its picture is gone:

- a pending capture older than a day (its job never delivered a picture);
- a stored sidecar no Gallery record mentions at all;
- a stored sidecar whose picture the Gallery once indexed (`image_id` set on
  its record) and whose pixels are now in neither `images` nor `trash`.

Nothing modified in the last day is ever an orphan, and the clean-up refuses
while the Gallery is scanning or still hashing pictures, because then "not in
`images`" is not yet a fact. A stored sidecar whose record exists but no
longer names it is relinked, not deleted.

Deleting with the image happens when the TRASH is emptied, not when a picture
is moved to it: Studio's trash is an undo window, and a restored picture keeps
its heatmap. A sidecar whose pixels another indexed picture still has is kept.

Pure standard library, like `attention_store`.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .attention_store import ATTENTION, PENDING, SUFFIX, relative_for, resolve

#: Nothing younger than this is an orphan: a generation in flight, or one
#: whose picture the Gallery has not met yet.
GRACE_SECONDS = 24 * 60 * 60

#: An interrupted `write_sidecar` leaves this behind in `pending/`.
_TEMP_SUFFIX = ".attn.tmp.npz"


class MoveRefused(Exception):
    """The new folder cannot take the store; nothing was changed."""


def stored(root: Path | str) -> Iterator[tuple[str, Path]]:
    """`(content hash, path)` of every validly named sidecar in the store."""
    base = Path(root) / ATTENTION
    if not base.is_dir():
        return
    for shard in sorted(base.iterdir()):
        if not shard.is_dir():
            continue
        for path in sorted(shard.iterdir()):
            if not path.name.endswith(SUFFIX):
                continue
            digest = path.name[: -len(SUFFIX)]
            if relative_for(digest) == f"{ATTENTION}/{shard.name}/{path.name}":
                yield digest, path


def pending(root: Path | str) -> Iterator[Path]:
    """Every capture still waiting in `pending/`, and any interrupted write."""
    base = Path(root) / PENDING
    if not base.is_dir():
        return
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name.endswith((SUFFIX, _TEMP_SUFFIX)):
            yield path


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _age(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class Survey:
    """What the store holds and what a clean-up would do. Paths stay here;
    `to_dict` is what a page sees."""

    files: int = 0
    bytes: int = 0
    pending_files: int = 0
    orphans: list[Path] = field(default_factory=list)
    orphan_bytes: int = 0
    #: Records whose picture is gone, so their name is cleared with the file.
    gone: list[str] = field(default_factory=list)
    #: Records that exist but no longer name their sidecar.
    relink: list[str] = field(default_factory=list)
    #: Why a clean-up cannot run now, or "".
    refused: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"files": self.files, "bytes": self.bytes, "pending_files": self.pending_files,
                "orphan_files": len(self.orphans), "orphan_bytes": self.orphan_bytes,
                "refused": self.refused}


def _facts(store: Any) -> dict[str, Any]:
    with store.read() as connection:
        rows = connection.execute(
            "SELECT content_hash, attention_path, image_id FROM image_metadata "
            "WHERE content_hash != ''").fetchall()
        indexed = {str(r[0]) for r in connection.execute(
            "SELECT content_hash FROM images WHERE content_hash IS NOT NULL "
            "AND TRIM(content_hash) != ''")}
        trashed = {str(r[0]) for r in connection.execute(
            "SELECT content_hash FROM trash WHERE content_hash IS NOT NULL "
            "AND content_hash != ''")}
        unhashed = int(connection.execute(
            "SELECT COUNT(*) FROM images WHERE content_hash IS NULL "
            "OR content_hash = ''").fetchone()[0])
    return {"named": {str(r[0]): str(r[1] or "") for r in rows},
            "linked": {str(r[0]) for r in rows if r[2] is not None},
            "indexed": indexed, "trashed": trashed, "unhashed": unhashed}


def survey(store: Any, store_root: Path | str, pending_root: Path | str, *,
           scanning: bool = False, now: float | None = None) -> Survey:
    """The size readout, and the orphans by the rule in the module docstring."""
    now = time.time() if now is None else now
    result = Survey()
    facts = _facts(store)
    if scanning:
        result.refused = "The Gallery is scanning. Try again when it has finished."
    elif facts["unhashed"]:
        result.refused = "The Gallery is still indexing pictures. Try again when it has finished."
    for digest, path in stored(store_root):
        size = _size(path)
        result.files += 1
        result.bytes += size
        if _age(path, now) < GRACE_SECONDS:
            continue
        relative = relative_for(digest)
        if digest not in facts["named"]:
            result.orphans.append(path)
            result.orphan_bytes += size
        elif digest in facts["linked"] and digest not in facts["indexed"] \
                and digest not in facts["trashed"]:
            result.orphans.append(path)
            result.orphan_bytes += size
            result.gone.append(digest)
        elif facts["named"][digest] != relative:
            result.relink.append(digest)
    for path in pending(pending_root):
        result.pending_files += 1
        if _age(path, now) >= GRACE_SECONDS:
            result.orphans.append(path)
            result.orphan_bytes += _size(path)
    return result


def _prune_empty_shards(root: Path | str) -> None:
    base = Path(root) / ATTENTION
    if not base.is_dir():
        return
    for shard in base.iterdir():
        try:
            if shard.is_dir() and not any(shard.iterdir()):
                shard.rmdir()
        except OSError:
            pass


def clean(store: Any, store_root: Path | str, pending_root: Path | str, *,
          scanning: bool = False, now: float | None = None) -> dict[str, Any]:
    """Delete the orphans `survey` finds, clear the records of pictures that
    are gone, and relink records that lost their sidecar's name."""
    found = survey(store, store_root, pending_root, scanning=scanning, now=now)
    if found.refused:
        return {"ok": False, "error": found.refused}
    removed, freed = 0, 0
    for path in found.orphans:
        size = _size(path)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed += 1
        freed += size
    with store.write() as connection:
        for digest in found.gone:
            connection.execute("UPDATE image_metadata SET attention_path = '' WHERE content_hash = ?",
                               (digest,))
        for digest in found.relink:
            connection.execute("UPDATE image_metadata SET attention_path = ? WHERE content_hash = ?",
                               (relative_for(digest), digest))
    _prune_empty_shards(store_root)
    return {"ok": True, "removed": removed, "freed_bytes": freed, "relinked": len(found.relink)}


def forget(store: Any, store_root: Path | str, hashes: Iterable[str]) -> int:
    """The trash was emptied: delete each picture's sidecar, unless another
    indexed picture still has the same pixels. Returns how many went."""
    wanted = {h for h in hashes if relative_for(h or "")}
    if not wanted:
        return 0
    facts = _facts(store)
    removed = 0
    for digest in sorted(wanted - facts["indexed"]):
        path = resolve(store_root, relative_for(digest))
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            continue
        with store.write() as connection:
            connection.execute("UPDATE image_metadata SET attention_path = '' WHERE content_hash = ?",
                               (digest,))
    _prune_empty_shards(store_root)
    return removed


# -- moving the store -------------------------------------------------------

def validate_target(folder: object, current: Path | str, pending_root: Path | str) -> Path:
    """The new store folder, or `MoveRefused` naming what is wrong. Creates it
    and proves it writable; changes nothing else."""
    text = folder.strip() if isinstance(folder, str) else ""
    if not text:
        raise MoveRefused("Name a folder.")
    target = Path(text)
    if not target.is_absolute():
        raise MoveRefused("Use a full path, starting with the drive.")
    if target.anchor and Path(target.anchor) == target:
        raise MoveRefused("Pick a folder, not a whole drive.")
    target = Path(os.path.abspath(target))
    old = Path(os.path.abspath(current))
    working = Path(os.path.abspath(pending_root)) / PENDING
    if target == old:
        raise MoveRefused("That is already the folder in use.")
    if old in target.parents:
        raise MoveRefused("That folder is inside the current one.")
    if target == working or working in target.parents:
        raise MoveRefused("That folder is Studio's own working area.")
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".studio-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as error:
        raise MoveRefused(f"Studio cannot write there ({type(error).__name__}).") from None
    return target


def copy_store(old_root: Path | str, new_root: Path | str) -> list[tuple[Path, Path]]:
    """Copy every sidecar into the new folder, each verified by size. On any
    failure the copies made are deleted and `MoveRefused` raised: the old
    folder is still the whole store."""
    pairs: list[tuple[Path, Path]] = []
    try:
        for digest, source in stored(old_root):
            target = Path(new_root) / relative_for(digest)
            target.parent.mkdir(parents=True, exist_ok=True)
            pairs.append((source, target))
            shutil.copy2(source, target)
            if _size(target) != _size(source):
                raise OSError(f"{target.name} was copied short")
    except OSError as error:
        for _source, target in pairs:
            try:
                target.unlink()
            except OSError:
                pass
        _prune_empty_shards(new_root)
        raise MoveRefused(f"The heatmap data could not be copied ({type(error).__name__}). "
                          "Nothing was changed.") from None
    return pairs


def remove_originals(pairs: Iterable[tuple[Path, Path]], old_root: Path | str) -> int:
    """After the switch: delete the old copies. Returns how many could not go
    (stray files in the old folder, harmless and reported)."""
    left = 0
    for source, _target in pairs:
        try:
            source.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            left += 1
    _prune_empty_shards(old_root)
    return left


__all__ = (
    "GRACE_SECONDS",
    "MoveRefused",
    "Survey",
    "clean",
    "copy_store",
    "forget",
    "pending",
    "remove_originals",
    "stored",
    "survey",
    "validate_target",
)
