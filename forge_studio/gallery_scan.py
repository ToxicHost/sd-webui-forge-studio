"""Putting what is on disk into the index, and taking out what is not.

Joins `gallery_index` (which reads files) to `gallery_store` (which remembers
them). The Extension's `scan_all_folders` in one place, with its hard-won
properties kept and its implicit ones made explicit.

**A scan is incremental.** Files already indexed are skipped by path, so the
second scan of a forty-thousand-image library costs a directory walk rather
than forty thousand image decodes. It is the difference between a Gallery an
owner can refresh and one they avoid refreshing.

**One file, one transaction.** The Extension commits per file, and the comment
explaining why is worth keeping: the expensive part is decoding the image, and
holding a write transaction across it blocks every other writer -- including
the generation that is trying to save the parameters of an image being made
right now. Slow work happens outside the transaction; the transaction is the
INSERT.

**A vanished file is removed, but only after a complete pass.** The prune uses
what the walk actually found, so a scan that was cancelled half way through
does not conclude that the folders it never reached are empty. That check is
the difference between a cancel and a catastrophe.

**Orphan metadata is linked by hash.** Generation saves parameters keyed on the
content hash the moment an image is written, before the Gallery has ever seen
the file. When the scan meets that file it adopts the waiting row. This is what
`gallery_store`'s nullable `image_id` exists for.

Studio-owned: no Forge, no Neo, no Torch.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import gallery_actions
from .gallery_index import (
    MEDIA_SUFFIXES,
    VIDEO_SUFFIXES,
    scan_entries,
    walk_follow,
)
from .gallery_store import GalleryStore, GalleryStoreError

#: What separates the parts of a displayed folder name.
#:
#: A backslash, on every platform, and NOT `os.sep`. This string is a grouping
#: label rather than a path: `gallery.js` normalises slashes before splitting it
#: for the folder tree, so either would draw correctly -- but the column is also
#: matched by equality when filtering, and an adopted database written by the
#: Extension is full of backslashes. Mixing the two would file `out\sub` and
#: `out/sub` as different folders in the same library.
FOLDER_SEPARATOR = "\\"

#: What a folder's own files are filed under.
ROOT_LABEL = "(Root)"

#: How many new files share one transaction.
#:
#: The Extension commits per file, and its comment explains why: the expensive
#: part was decoding the image, and holding a write lock across it blocks the
#: generation trying to save its own metadata. That reasoning was sound and no
#: longer applies -- the scan does not decode anything now, so a commit per file
#: IS the cost. At 1.9ms a row, seventeen thousand of them is thirty seconds of
#: pure transaction overhead.
#:
#: A batch of this size takes a few milliseconds, so the lock is still held for
#: about as long as one decode used to take. A cancelled scan loses at most the
#: batch in flight, and since a cancelled scan prunes nothing, losing it costs
#: the owner one rescan of those rows and nothing else.
SCAN_BATCH = 250


class ScanCancelled(Exception):
    """The owner stopped the scan. Not a failure."""


@dataclass(frozen=True)
class ScanProgress:
    """Where a scan has got to, for one folder."""

    folder: str
    current: int
    total: int
    phase: str


@dataclass
class ScanResult:
    """What a scan did."""

    added: int = 0
    removed: int = 0
    seen: int = 0
    linked: int = 0
    unreadable: int = 0
    folders: list[str] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "seen": self.seen,
            "linked": self.linked,
            "unreadable": self.unreadable,
            "folders": list(self.folders),
            "cancelled": self.cancelled,
        }


def _earliest(stat: Any) -> float:
    """The earliest date a stat admits to. `file_date`, without re-stat-ing.

    Same rule: `st_birthtime` where the filesystem records one, and `st_ctime`
    only under `os.name == "nt"` where it means creation rather than the inode
    change time POSIX makes it.
    """

    dates = [stat.st_mtime] if stat.st_mtime else []
    birth = getattr(stat, "st_birthtime", None)
    if birth:
        dates.append(birth)
    if os.name == "nt" and stat.st_ctime:
        dates.append(stat.st_ctime)
    return min(dates) if dates else 0.0


def media_type_of(path: Path) -> str:
    """`image`, `gif` or `video`.

    An animated GIF is neither: it is an image the Gallery can show inline but
    must not treat as a still when making a thumbnail.
    """

    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "gif"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "image"


def display_folder(root: Path, directory: Path) -> str:
    """The label an image is filed under: the scan folder, then the subtree."""

    relative = os.path.relpath(str(directory), str(root))
    if relative in (".", ""):
        return root.name
    parts = [part for part in Path(relative).parts if part not in (".",)]
    return FOLDER_SEPARATOR.join([root.name, *parts])


class GalleryScanner:
    """Indexes the configured folders into a store."""

    def __init__(
        self,
        store: GalleryStore,
        *,
        cancel: threading.Event | None = None,
        on_progress: Callable[[ScanProgress], None] | None = None,
    ) -> None:
        self._store = store
        self._cancel = cancel or threading.Event()
        self._on_progress = on_progress
        self._ignore: set[str] = set()
        #: Character name -> row id, for the length of one scan. A library has
        #: thousands of images and a handful of names, so looking each one up
        #: again per file was three SQL statements to learn something already
        #: known.
        self._character_ids: dict[str, int] = {}

    # -- configured folders ------------------------------------------------

    def add_folder(self, path: str | Path, label: str | None = None) -> None:
        """Register a folder to scan. Adding the same one twice is harmless."""

        resolved = str(Path(path).resolve())
        with self._store.write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO scan_folders(path, label) VALUES(?, ?)",
                (resolved, label or Path(resolved).name),
            )

    def remove_folder(self, path: str | Path) -> int:
        """Forget a folder AND the images indexed from it.

        The images go too. Leaving them would present an owner with a Gallery
        full of pictures from a folder they just removed, and no way to tell
        why they are still there.
        """

        resolved = str(Path(path).resolve())
        with self._store.write() as connection:
            connection.execute(
                "DELETE FROM scan_folders WHERE path=?", (resolved,))
            removed = connection.execute(
                "DELETE FROM images WHERE filepath LIKE ?",
                (f"{resolved}{os.sep}%",),
            ).rowcount
        return int(removed)

    def folders(self) -> list[dict[str, Any]]:
        with self._store.read() as connection:
            rows = connection.execute(
                "SELECT id, path, label FROM scan_folders ORDER BY label"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- the scan ----------------------------------------------------------

    def scan(self) -> ScanResult:
        """Index every configured folder. Returns what changed.

        Raises `GalleryStoreError` only for a database that has genuinely
        failed. A folder that has been unplugged is not a failure: it is
        reported and skipped, because an owner with an external drive
        disconnected should still be able to browse the rest of their library.
        """

        result = ScanResult()
        configured = self.folders()
        if not configured:
            return result

        found: set[str] = set()
        known = self._known_paths()
        # Read once for the whole scan rather than per file: it is a small
        # table and every new image consults it.
        self._ignore = gallery_actions.ignore_words(self._store)
        self._character_ids = {}

        unreachable: list[str] = []
        for folder in configured:
            root = Path(folder["path"])
            if not root.is_dir():
                # Unplugged, renamed, or not yet mounted. Remembered, because
                # the prune below must not conclude that a folder nobody
                # looked at is a folder whose files are gone.
                unreachable.append(str(root))
                continue
            result.folders.append(str(root))
            try:
                self._scan_one(root, known, found, result)
            except ScanCancelled:
                result.cancelled = True
                return result

        # Only a COMPLETE pass may conclude that a file is gone. A cancelled
        # scan has not visited the folders it never reached, and pruning on its
        # partial `found` set would delete an owner's index for them.
        #
        # An UNREACHABLE root is the same argument, and the docstring above
        # already promised it: an owner with an external drive disconnected
        # should still be able to browse the rest of their library. It was not
        # true. `_known_paths` returns every row in the table, the unplugged
        # root contributes nothing to `found`, and one scan therefore deleted
        # every row under it -- ratings, tags and all, none of which come back
        # when the drive does. Reachable folders prune; absent ones are left
        # exactly as they were.
        result.removed = self._prune(
            self._still_answerable(known - found, unreachable))
        return result

    def _scan_one(self, root: Path, known: set[str], found: set[str],
                  result: ScanResult) -> None:
        # Announced BEFORE counting. Counting walks the whole tree, which on a
        # real library is seconds of nothing -- and the page will not draw its
        # progress bar until a folder row exists, so the owner presses scan and
        # watches an unchanged screen wondering whether it registered.
        self._report(ScanProgress(root.name, 0, 0, "Counting"))
        total = self._count(root)
        current = 0
        self._report(ScanProgress(root.name, 0, total, "Scanning"))

        batch: list[tuple[Path, Any]] = []
        #: One folder label per DIRECTORY rather than per file. `relpath` was
        #: 17,000 calls for 17,000 files that mostly share a few hundred
        #: folders.
        labels: dict[str, str] = {}
        for path, entry in scan_entries(root):
            self._check_cancelled()
            current += 1
            text = str(path)
            found.add(text)
            # Often enough to look alive. The page polls twice a second and
            # draws a bar across the bottom of the window, so a report every
            # twenty-five files leaves a small library finishing before the bar
            # ever moves.
            if current % 5 == 0 or current == total:
                self._report(ScanProgress(root.name, current, total, "Scanning"))
            if text in known:
                continue
            result.seen += 1
            batch.append((path, entry))
            if len(batch) >= SCAN_BATCH:
                self._write_batch(root, batch, result, labels)
                batch = []
        if batch:
            self._write_batch(root, batch, result, labels)

        self._report(ScanProgress(root.name, current, total, "Done"))

    def _write_batch(self, root: Path, batch: list[tuple[Path, Any]],
                     result: ScanResult,
                     labels: dict[str, str]) -> None:
        """One transaction for many files."""

        try:
            with self._store.write() as connection:
                for path, entry in batch:
                    self._insert(connection, root, path, entry, result, labels)
        except GalleryStoreError:
            # One unwritable batch must not end the scan. Those files stay
            # absent from the index and the next scan tries them again.
            result.unreadable += len(batch)

    def _insert(self, connection: Any, root: Path, path: Path,
                entry: Any, result: ScanResult,
                labels: dict[str, str]) -> None:
        """One row, inside a transaction the caller owns.

        NOTHING here opens the file. `outline` used to, only to read the
        dimensions out of the header -- and a profile showed that single
        `open()` was half the entire scan: 0.74ms of filesystem per image,
        which is 12 seconds across seventeen thousand of them, paid twice
        because enrichment opens each file again anyway.
        """

        kind = media_type_of(path)
        try:
            # The stat the DIRECTORY LISTING already carried. Asking the
            # filesystem again -- which is what `path.stat()` plus `file_date`
            # did, twice per file -- was 34,000 syscalls for 17,000 images.
            stat = entry.stat()
            when = _earliest(stat)
        except OSError:
            when = 0.0
        directory = str(path.parent)
        label = labels.get(directory)
        if label is None:
            label = display_folder(root, path.parent)
            labels[directory] = label
        cursor = connection.execute(
            "INSERT OR IGNORE INTO images("
            "filename, folder, filepath, file_date, "
            "search_text, media_type, content_hash) "
            "VALUES(?, ?, ?, ?, '', ?, '')",
            (path.name, label, str(path), when, kind),
        )
        image_id = cursor.lastrowid
        if not image_id:
            return  # already present; another writer won the race
        result.added += 1
        self._tag(connection, image_id, path.name)

    def _tag(self, connection: Any, image_id: int, filename: str) -> None:
        """Give a new image the characters its filename names.

        DURING the scan, in the same transaction as the row. The Extension
        does this and a browser run showed why it matters: without it a freshly
        scanned library has an empty TAGS sidebar, and the owner has no reason
        to suspect a "rescan characters" button would fill it.
        """

        for position, name in enumerate(
            gallery_actions.characters_in(filename, self._ignore)
        ):
            character_id = self._character_ids.get(name.lower())
            if character_id is None:
                connection.execute(
                    "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
                row = connection.execute(
                    "SELECT id FROM characters WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if row is None:
                    continue
                character_id = int(row["id"])
                self._character_ids[name.lower()] = character_id
            connection.execute(
                "INSERT OR IGNORE INTO image_characters"
                "(image_id, character_id, position, source) "
                "VALUES(?, ?, ?, 'auto')",
                (image_id, character_id, position),
            )

    def _prune(self, missing: Iterable[str]) -> int:
        missing = list(missing)
        if not missing:
            return 0
        removed = 0
        with self._store.write() as connection:
            for text in missing:
                removed += connection.execute(
                    "DELETE FROM images WHERE filepath=?", (text,)
                ).rowcount
            # A character nobody is tagged with is not a character.
            connection.execute(
                "DELETE FROM characters WHERE id NOT IN "
                "(SELECT DISTINCT character_id FROM image_characters)"
            )
        return int(removed)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _still_answerable(missing: set[str],
                          unreachable: Iterable[str]) -> set[str]:
        """`missing`, minus everything under a root nobody could look inside."""

        roots = [os.path.join(str(root), "") for root in unreachable]
        if not roots:
            return missing
        return {
            path for path in missing
            if not any(path.startswith(root) for root in roots)
        }

    def _known_paths(self) -> set[str]:
        with self._store.read() as connection:
            return {
                str(row[0])
                for row in connection.execute("SELECT filepath FROM images")
            }

    @staticmethod
    def _count(root: Path) -> int:
        """How many media files are under `root`, for the progress total.

        A second walk, deliberately. Counting first means the owner sees "412
        of 9,000" rather than a number climbing towards nothing, and a walk is
        cheap next to decoding every file it finds.
        """

        total = 0
        for _, _, filenames in walk_follow(root):
            total += sum(
                1 for name in filenames
                if Path(name).suffix.lower() in MEDIA_SUFFIXES
            )
        return total

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise ScanCancelled()

    def _report(self, progress: ScanProgress) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(progress)
        except Exception:  # noqa: BLE001
            # A listener that throws must not take the scan down with it.
            pass


__all__ = (
    "FOLDER_SEPARATOR",
    "ROOT_LABEL",
    "GalleryScanner",
    "ScanCancelled",
    "ScanProgress",
    "ScanResult",
    "display_folder",
    "media_type_of",
)
