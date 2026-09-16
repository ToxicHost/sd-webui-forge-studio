"""Changing things: ratings, tags, names, and deletion that can be undone.

The write half of the Gallery. Everything here touches either an owner's
database rows or an owner's actual files, so the rules are stricter than on the
reading side.

**A path is never taken from the caller.** Every operation names its target by
row id and reads the path out of the index. A request cannot ask Studio to
rename or delete a file the owner never indexed, whatever it puts in the body.

**Deleting moves, it does not destroy.** A delete takes the file to the
Gallery's own trash folder and records where it came from, so restoring is a
move back rather than a recovery. `shutil.move`, not `os.rename`: the state
root is frequently on a different drive from the pictures, and `os.rename`
across volumes raises -- which in the Extension surfaced as a delete that
failed with an OSError for exactly the owners whose library is on a second
disk.

**Restoring never overwrites.** If something now occupies the original name,
the restored file is given a free one beside it. Silently replacing whatever is
there would be a data-loss bug inside the feature whose entire purpose is
undoing one.

**Studio's trash is a holding pen, not the grave.** It exists to make Ctrl+Z
work: undo restores by row id from `original_filepath`, which the operating
system's own trash cannot be asked for portably. But once the owner empties it,
disposal belongs to the platform. `empty_trash` used to call `unlink`, so an
owner's pictures were destroyed outright and never touched the Recycle Bin at
any point in their life -- against every expectation Windows sets. The same was
true of a format conversion that did not keep its original.

**The row goes only when the file has moved.** Every operation moves the file
first and writes the database second, so a failed move leaves an index that
still matches the disk. The other order produces a Gallery that has forgotten a
file which is still there.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .gallery_store import GalleryStore, GalleryStoreError

# `Send2Trash` is a deliberate dependency, and it is worth saying why against
# the opposite decision next door. `gallery_similarity` REMOVED `imagehash`,
# because its job was arithmetic with a bit-exact specification that could be
# reimplemented and verified against the original. This job is a platform shell
# API whose failure mode is silent permanent deletion: on Windows,
# `SHFileOperation` with `FOF_ALLOWUNDO` destroys the file outright when the
# volume has no recycle bin, and getting that wrong loses an owner's pictures
# while reporting success. Pure Python, no runtime dependencies of its own.
try:
    from send2trash import send2trash as _send_to_os_trash
except ImportError:  # pragma: no cover - an offline install, reported not hidden
    _send_to_os_trash = None


#: Where a discarded file actually went. Reported rather than assumed: a volume
#: with no recycle bin is a real configuration, and quietly hard-deleting there
#: is the exact bug this helper exists to prevent.
RECYCLED = "recycled"
DESTROYED = "destroyed"
ALREADY_GONE = "already_gone"


def _discard(path: Path) -> str:
    """Hand one file to the operating system's trash, if the host has one."""

    if not path.exists():
        return ALREADY_GONE
    if _send_to_os_trash is not None:
        try:
            _send_to_os_trash(str(path))
            return RECYCLED
        except Exception:  # noqa: BLE001
            # Network shares, some removable volumes, and a locked file all
            # land here. Falling through is right; pretending it was recycled
            # is not, which is why the caller is told which happened.
            pass
    try:
        path.unlink()
        return DESTROYED
    except OSError:
        return ALREADY_GONE


#: Ratings are stars, and there are five of them. Zero means unrated.
MAX_RATING = 5

#: How many times a free name is looked for before giving up. A folder with
#: this many collisions on one name is not a case worth grinding over.
MAX_NAME_ATTEMPTS = 1000


#: How long to keep retrying a file operation another process is blocking.
#: Windows refuses to rename or delete a file while anything holds a handle to
#: it, and plenty of things briefly do: an antivirus scanner, the search
#: indexer, a thumbnail provider -- and Studio's own hashing worker, which
#: starts on its own after a scan and reads every new image. Without this, an
#: owner who renames a picture seconds after linking a folder gets a
#: PermissionError for no reason they could ever guess.
FILE_LOCK_PATIENCE_SECONDS = 2.0
FILE_LOCK_RETRY_SECONDS = 0.05


class ActionRefused(Exception):
    """The operation did not happen, and this is why."""


def _unblocked(operation: Any, description: str) -> Any:
    """Run a file operation, waiting out a transient lock.

    Only PermissionError is retried. A missing file or a bad path fails at
    once, because waiting two seconds to report something that will never
    change is worse than reporting it immediately.
    """

    deadline = time.monotonic() + FILE_LOCK_PATIENCE_SECONDS
    while True:
        try:
            return operation()
        except PermissionError as error:
            if time.monotonic() >= deadline:
                raise ActionRefused(
                    f"{description} because another program is using it. "
                    "Close it and try again."
                ) from error
            time.sleep(FILE_LOCK_RETRY_SECONDS)
        except (OSError, shutil.Error) as error:
            raise ActionRefused(
                f"{description}: {type(error).__name__}"
            ) from error


@dataclass(frozen=True)
class Target:
    """One indexed image, as the actions need it."""

    image_id: int
    path: Path
    filename: str
    folder: str


def _target(store: GalleryStore, image_id: int) -> Target:
    with store.read() as connection:
        row = connection.execute(
            "SELECT id, filepath, filename, folder FROM images WHERE id = ?",
            (image_id,),
        ).fetchone()
    if row is None:
        raise ActionRefused("That image is not in the Gallery.")
    return Target(int(row["id"]), Path(str(row["filepath"])),
                  str(row["filename"]), str(row["folder"]))


def free_name(directory: Path, desired: str) -> Path:
    """A name in `directory` that nothing is using yet.

    `picture.png`, then `picture (2).png`, and so on -- the convention an owner
    already recognises from their file manager.
    """

    candidate = directory / desired
    if not candidate.exists():
        return candidate
    stem, suffix = Path(desired).stem, Path(desired).suffix
    for attempt in range(2, MAX_NAME_ATTEMPTS + 2):
        candidate = directory / f"{stem} ({attempt}){suffix}"
        if not candidate.exists():
            return candidate
    raise ActionRefused(
        f"There are too many files called {desired} in that folder."
    )


# -- ratings and tags -----------------------------------------------------


def set_rating(store: GalleryStore, image_id: int, rating: int) -> dict[str, Any]:
    """Nought to five stars. Zero clears it."""

    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ActionRefused("A rating must be a number.") from None
    rating = max(0, min(MAX_RATING, rating))
    target = _target(store, image_id)
    with store.write() as connection:
        connection.execute(
            "UPDATE images SET rating = ? WHERE id = ?", (rating, target.image_id)
        )
    return {"ok": True, "rating": rating}


def add_tag(store: GalleryStore, image_id: int, name: str) -> dict[str, Any]:
    """Tag an image with a character. Tagging twice is not an error."""

    name = (name or "").strip()
    if not name:
        raise ActionRefused("A tag needs a name.")
    target = _target(store, image_id)
    with store.write() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
        character = connection.execute(
            "SELECT id FROM characters WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM image_characters "
            "WHERE image_id = ?",
            (target.image_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT OR IGNORE INTO image_characters"
            "(image_id, character_id, position, source) VALUES(?, ?, ?, 'manual')",
            (target.image_id, character["id"], position),
        )
    return {"ok": True, "name": name}


def remove_tag(store: GalleryStore, image_id: int, name: str) -> dict[str, Any]:
    """Untag an image, and forget the character if nothing else wears it."""

    name = (name or "").strip()
    if not name:
        raise ActionRefused("A tag needs a name.")
    target = _target(store, image_id)
    with store.write() as connection:
        connection.execute(
            "DELETE FROM image_characters WHERE image_id = ? AND character_id IN "
            "(SELECT id FROM characters WHERE name = ? COLLATE NOCASE)",
            (target.image_id, name),
        )
        # A character nobody is tagged with is not a character, and leaving it
        # puts an empty entry in the owner's sidebar forever.
        connection.execute(
            "DELETE FROM characters WHERE id NOT IN "
            "(SELECT DISTINCT character_id FROM image_characters)"
        )
    return {"ok": True, "name": name}


def set_ignore_word(store: GalleryStore, word: str,
                    ignored: bool = True) -> dict[str, Any]:
    """Words the character parser should not read as a name."""

    word = (word or "").strip()
    if not word:
        raise ActionRefused("An ignore word needs to be a word.")
    with store.write() as connection:
        if ignored:
            connection.execute(
                "INSERT OR IGNORE INTO ignore_words(word) VALUES(?)", (word,))
        else:
            connection.execute(
                "DELETE FROM ignore_words WHERE word = ? COLLATE NOCASE",
                (word,),
            )
    return {"ok": True, "word": word, "ignored": ignored}


# -- renaming -------------------------------------------------------------


def rename(store: GalleryStore, image_id: int, filename: str) -> dict[str, Any]:
    """Give a file a new name, in the folder it is already in."""

    filename = (filename or "").strip()
    if not filename:
        raise ActionRefused("A file needs a name.")
    # A NAME, not a path. Without this a rename could walk out of the folder
    # the file lives in and land anywhere the process can write.
    if filename != Path(filename).name or filename in (".", ".."):
        raise ActionRefused("A file name cannot contain a path.")

    target = _target(store, image_id)
    if not target.path.exists():
        raise ActionRefused("That file is no longer where the Gallery left it.")
    destination = target.path.parent / filename
    if destination.exists() and destination != target.path:
        raise ActionRefused(f"There is already a file called {filename} there.")

    _unblocked(lambda: target.path.replace(destination),
               "The file could not be renamed")

    with store.write() as connection:
        connection.execute(
            "UPDATE images SET filename = ?, filepath = ? WHERE id = ?",
            (filename, str(destination), target.image_id),
        )
    return {"ok": True, "filename": filename}


# -- deletion, and undoing it ---------------------------------------------


def delete_to_trash(store: GalleryStore, image_id: int) -> dict[str, Any]:
    """Move a file to the Gallery's trash and remember where it came from."""

    target = _target(store, image_id)
    trash_directory = store.location.trash
    trash_directory.mkdir(parents=True, exist_ok=True)

    with store.read() as connection:
        row = connection.execute(
            "SELECT width, height, file_date, search_text FROM images "
            "WHERE id = ?", (image_id,)
        ).fetchone()
        characters = [
            str(found["name"]) for found in connection.execute(
                "SELECT c.name FROM characters c "
                "JOIN image_characters ic ON c.id = ic.character_id "
                "WHERE ic.image_id = ? ORDER BY ic.position",
                (image_id,),
            )
        ]

    # Stamped, so two files with the same name deleted from different folders
    # do not collide in one flat trash directory.
    stamped = f"{int(time.time() * 1000)}_{target.filename}"
    trash_path = ""
    if target.path.exists():
        destination = free_name(trash_directory, stamped)
        # `shutil.move`, not `os.rename`: the state root is often on a
        # different drive from the pictures, and a cross-volume rename raises
        # rather than copying.
        _unblocked(lambda: shutil.move(str(target.path), str(destination)),
                   "The file could not be moved to the trash")
        trash_path = str(destination)

    # Only now. A row removed before the move would leave the Gallery having
    # forgotten a file that is still sitting on the disk.
    with store.write() as connection:
        cursor = connection.execute(
            "INSERT INTO trash(original_filepath, original_folder, "
            "original_filename, trash_path, width, height, file_date, "
            "search_text, characters_json, deleted_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(target.path), target.folder, target.filename, trash_path,
             row["width"] if row else None, row["height"] if row else None,
             row["file_date"] if row else None,
             (row["search_text"] if row else "") or "",
             json.dumps(characters), time.time()),
        )
        trash_id = cursor.lastrowid
        connection.execute(
            "DELETE FROM dup_pairs WHERE image_a = ? OR image_b = ?",
            (image_id, image_id),
        )
        connection.execute("DELETE FROM images WHERE id = ?", (image_id,))
        connection.execute(
            "DELETE FROM characters WHERE id NOT IN "
            "(SELECT DISTINCT character_id FROM image_characters)"
        )
    return {"ok": True, "trash_id": trash_id}


def list_trash(store: GalleryStore) -> list[dict[str, Any]]:
    """What is in the trash, newest first. Never a path."""

    with store.read() as connection:
        rows = connection.execute(
            "SELECT id, original_filename, original_folder, width, height, "
            "deleted_at FROM trash ORDER BY deleted_at DESC"
        ).fetchall()
    return [
        {"id": int(row["id"]),
         "filename": str(row["original_filename"] or ""),
         "folder": str(row["original_folder"] or ""),
         "width": row["width"], "height": row["height"],
         "deleted_at": row["deleted_at"]}
        for row in rows
    ]


def restore(store: GalleryStore, trash_id: int) -> dict[str, Any]:
    """Put a deleted file back where it was.

    Never over the top of anything. If the original name is taken now, the
    file comes back beside it under a free one -- overwriting would be a
    data-loss bug inside the feature that exists to undo one.
    """

    with store.read() as connection:
        row = connection.execute(
            "SELECT * FROM trash WHERE id = ?", (trash_id,)
        ).fetchone()
    if row is None:
        raise ActionRefused("That item is not in the trash.")

    source = Path(str(row["trash_path"] or ""))
    if not row["trash_path"] or not source.exists():
        raise ActionRefused(
            "The deleted file is no longer in the Gallery's trash folder."
        )

    original = Path(str(row["original_filepath"]))
    try:
        original.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ActionRefused(
            f"The folder could not be recreated: {type(error).__name__}"
        ) from error
    destination = free_name(original.parent, original.name)
    _unblocked(lambda: shutil.move(str(source), str(destination)),
               "The file could not be restored")

    with store.write() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO images(filename, folder, filepath, width, "
            "height, file_date, search_text) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (destination.name, str(row["original_folder"] or ""),
             str(destination), row["width"], row["height"],
             row["file_date"], row["search_text"] or ""),
        )
        image_id = cursor.lastrowid
        if image_id:
            for position, name in enumerate(
                _characters_of(row["characters_json"])
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
                character = connection.execute(
                    "SELECT id FROM characters WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                connection.execute(
                    "INSERT OR IGNORE INTO image_characters"
                    "(image_id, character_id, position) VALUES(?, ?, ?)",
                    (image_id, character["id"], position),
                )
        connection.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
    return {"ok": True, "filename": destination.name,
            "renamed": destination.name != original.name}


def _characters_of(raw: Any) -> list[str]:
    try:
        found = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(name) for name in found] if isinstance(found, list) else []


#: Metadata columns a generation may fill. Named, so a settings dictionary
#: carrying anything else cannot invent a column or reach one it should not.
_RECORDABLE = (
    "prompt", "negative_prompt", "template", "negative_template",
    "studio_dynamic_prompts", "seed", "steps", "cfg", "sampler", "scheduler",
    "model", "model_hash", "denoising", "width", "height", "clip_skip",
    "hires_upscaler", "hires_upscale",
)

#: What the parser calls a field, where that differs from the column.
_PARSED_NAMES = {"cfg": "cfg_scale", "denoising": "denoising"}


def record_generation(store: GalleryStore, content_hash: str, infotext: str,
                      settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Remember what an image was generated with, keyed by its pixels.

    Called the moment a generation finishes -- BEFORE the Gallery has scanned
    the file, and possibly before the owner has ever opened the Gallery. That
    is the whole reason `image_metadata` is keyed by content hash with a
    nullable image_id: there is no row to attach to yet. A later scan links it,
    and `_metadata` on the service already looks both ways.

    Worth keeping even for an image the Gallery will index anyway, because the
    database row survives metadata stripping and the file's own chunk does not.
    """

    content_hash = (content_hash or "").strip()
    if not content_hash:
        # Without a hash there is no identity to key on, and a row with an
        # empty hash would collide with every other one under the partial
        # unique index.
        raise ActionRefused("Generation metadata needs a content hash.")

    parsed = _parsed_fields(infotext, settings or {})
    columns = ["content_hash", "raw_infotext", "created_at"]
    values: list[Any] = [content_hash, infotext or "", time.time()]
    for column, value in parsed.items():
        columns.append(column)
        values.append(value)

    placeholders = ", ".join("?" for _ in columns)
    with store.write() as connection:
        connection.execute(
            f"INSERT OR REPLACE INTO image_metadata({', '.join(columns)}) "  # noqa: S608
            f"VALUES({placeholders})",
            values,
        )
        # If the scan already met this file, attach at once rather than waiting
        # for the next one.
        linked = connection.execute(
            "UPDATE image_metadata SET image_id = "
            "(SELECT id FROM images WHERE content_hash = ?) "
            "WHERE content_hash = ? AND image_id IS NULL "
            "AND EXISTS(SELECT 1 FROM images WHERE content_hash = ?)",
            (content_hash, content_hash, content_hash),
        ).rowcount
    return {"ok": True, "content_hash": content_hash, "linked": bool(linked)}


def _parsed_fields(infotext: str, settings: dict[str, Any]) -> dict[str, Any]:
    """The columns to write, from the infotext with settings on top.

    The infotext is what the image will carry and what an owner sees; the
    settings dictionary is what the engine actually resolved. Where they
    disagree the engine wins, because a seed of -1 in the prompt box became a
    real number by the time the picture existed.
    """

    from .gallery_metadata import parse_generation_parameters

    parsed = parse_generation_parameters(infotext or "")
    found: dict[str, Any] = {}
    for column in _RECORDABLE:
        name = _PARSED_NAMES.get(column, column)
        value = settings.get(column, settings.get(name))
        if value is None:
            value = parsed.get(column, parsed.get(name))
        if value not in (None, ""):
            found[column] = value
    return found


# -- characters from filenames -------------------------------------------


def _is_code(word: str) -> bool:
    """A serial number or a hash, not somebody's name."""

    return bool(re.fullmatch(r"\d+", word)
                or re.fullmatch(r"[0-9a-fA-F]{8,}", word))


def characters_in(filename: str,
                  ignore: Iterable[str] = ()) -> list[str]:
    """Who a filename says is in the picture.

    Ported from the Extension, whose conventions are what an owner's existing
    library is already named to. `+` and `,` SEPARATE characters, while spaces,
    underscores and dashes JOIN the parts of one name -- so `Oliver+Luca` is
    two people and `Shoyo_Hinata` is one. Trailing counters, bracketed asides
    and possessives come off first.

    Never empty: a file nothing can be read from is filed under "Unknown",
    which is a bucket an owner can click rather than a picture that vanishes
    from the sidebar.
    """

    ignored = {word.lower() for word in ignore}
    stem = Path(filename).stem
    stem = re.sub(r"\s*\d+\s*$", "", stem).strip()          # trailing counter
    stem = re.sub(r"\s*\([^)]*\)\s*", " ", stem).strip()    # (asides)
    stem = re.sub(r"\s*\[[^\]]*\]\s*", " ", stem).strip()   # [asides]
    stem = re.sub(r"['’]s\b", "", stem)                # possessives
    stem = re.sub(r"['’`]", " ", stem)
    if not stem.strip():
        return ["Unknown"]

    found: list[str] = []
    for part in re.split(r"[,+]+", stem.strip()):
        words = [
            word.strip().title()
            for word in re.split(r"[\s_\-]+", part.strip())
            if word.strip() and not _is_code(word.strip())
            and re.search(r"[a-zA-Z]", word)
        ]
        name = " ".join(words)
        if len(name) < 2 or name.lower() in ignored or name in found:
            continue
        found.append(name)
    found.sort(key=str.lower)
    return found or ["Unknown"]


def ignore_words(store: GalleryStore) -> set[str]:
    with store.read() as connection:
        return {
            str(row["word"]).lower()
            for row in connection.execute("SELECT word FROM ignore_words")
        }


def tag_from_filenames(store: GalleryStore, image_id: int,
                       ignore: Iterable[str] | None = None) -> list[str]:
    """Give one image the characters its filename names."""

    target = _target(store, image_id)
    names = characters_in(target.filename,
                          ignore if ignore is not None else ignore_words(store))
    with store.write() as connection:
        connection.execute(
            "DELETE FROM image_characters WHERE image_id = ? "
            "AND source = 'auto'", (image_id,))
        for position, name in enumerate(names):
            connection.execute(
                "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
            character = connection.execute(
                "SELECT id FROM characters WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO image_characters"
                "(image_id, character_id, position, source) "
                "VALUES(?, ?, ?, 'auto')",
                (image_id, character["id"], position),
            )
    return names


def rescan_characters(store: GalleryStore) -> dict[str, Any]:
    """Re-read every filename. What an owner presses after editing the
    ignore-word list, because the old tags were parsed under the old list."""

    ignore = ignore_words(store)
    with store.read() as connection:
        library = [
            (int(row["id"]), str(row["filename"] or ""))
            for row in connection.execute("SELECT id, filename FROM images")
        ]
    tagged = 0
    #: `tag_from_filenames` is the per-image door, and it opens a read and a
    #: write transaction each time. The page calls this route straight after
    #: every scan, so on a 17,000 image library that was 34,000 transactions
    #: an owner waited through BEFORE the grid would load -- `rescan()` in
    #: `gallery.js` awaits this route between the scan and `loadImagesReset`.
    #: One transaction, and one name -> id cache, does the same work.
    known: dict[str, int] = {}
    with store.write() as connection:
        for image_id, filename in library:
            try:
                names = characters_in(filename, ignore)
            except (ActionRefused, GalleryStoreError):
                continue
            connection.execute(
                "DELETE FROM image_characters WHERE image_id = ? "
                "AND source = 'auto'", (image_id,))
            for position, name in enumerate(names):
                character_id = known.get(name.lower())
                if character_id is None:
                    connection.execute(
                        "INSERT OR IGNORE INTO characters(name) VALUES(?)",
                        (name,))
                    row = connection.execute(
                        "SELECT id FROM characters WHERE name = ? "
                        "COLLATE NOCASE", (name,)).fetchone()
                    if row is None:
                        continue
                    character_id = int(row["id"])
                    known[name.lower()] = character_id
                connection.execute(
                    "INSERT OR IGNORE INTO image_characters"
                    "(image_id, character_id, position, source) "
                    "VALUES(?, ?, ?, 'auto')",
                    (image_id, character_id, position),
                )
            tagged += 1
    with store.write() as connection:
        connection.execute(
            "DELETE FROM characters WHERE id NOT IN "
            "(SELECT DISTINCT character_id FROM image_characters)"
        )
    return {"ok": True, "images": tagged}


# -- moving, copying, converting -----------------------------------------


def _folder_root(store: GalleryStore, folder: str) -> Path:
    """The directory behind a display folder like `pictures\\2026`."""

    head, _, tail = folder.replace("/", "\\").partition("\\")
    with store.read() as connection:
        for row in connection.execute("SELECT path FROM scan_folders"):
            root = Path(str(row["path"]))
            if root.name == head:
                return root / tail.replace("\\", "/") if tail else root
    raise ActionRefused(f"The Gallery does not know a folder called {folder}.")


def move(store: GalleryStore, image_id: int, folder: str) -> dict[str, Any]:
    """Move a file into another indexed folder."""

    folder = (folder or "").strip()
    if not folder:
        raise ActionRefused("No destination folder was named.")
    target = _target(store, image_id)
    if not target.path.exists():
        raise ActionRefused("That file is no longer where the Gallery left it.")
    directory = _folder_root(store, folder)
    directory.mkdir(parents=True, exist_ok=True)
    destination = free_name(directory, target.filename)
    _unblocked(lambda: shutil.move(str(target.path), str(destination)),
               "The file could not be moved")
    with store.write() as connection:
        connection.execute(
            "UPDATE images SET filepath = ?, filename = ?, folder = ? "
            "WHERE id = ?",
            (str(destination), destination.name, folder, image_id),
        )
    return {"ok": True, "filename": destination.name, "folder": folder}


def copy_to(store: GalleryStore, image_id: int, folder: str) -> dict[str, Any]:
    """Copy a file into another indexed folder. The copy is indexed too."""

    folder = (folder or "").strip()
    if not folder:
        raise ActionRefused("No destination folder was named.")
    target = _target(store, image_id)
    if not target.path.exists():
        raise ActionRefused("That file is no longer where the Gallery left it.")
    directory = _folder_root(store, folder)
    directory.mkdir(parents=True, exist_ok=True)
    destination = free_name(directory, target.filename)
    _unblocked(lambda: shutil.copy2(str(target.path), str(destination)),
               "The file could not be copied")

    with store.read() as connection:
        row = connection.execute(
            "SELECT width, height, search_text, media_type, content_hash "
            "FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    with store.write() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO images(filename, folder, filepath, width, "
            "height, file_date, search_text, media_type, content_hash) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (destination.name, folder, str(destination),
             row["width"] if row else None, row["height"] if row else None,
             time.time(), (row["search_text"] if row else "") or "",
             (row["media_type"] if row else "image") or "image",
             (row["content_hash"] if row else "") or ""),
        )
    return {"ok": True, "filename": destination.name, "folder": folder}


def strip_metadata(store: GalleryStore, image_id: int) -> dict[str, Any]:
    """Rewrite a file without its embedded parameters.

    The DATABASE row is deliberately left alone, so the detail panel still
    shows what the picture was made with. That is the Extension's behaviour and
    the reason `image_metadata` is worth having at all: an owner sharing an
    image should be able to remove the prompt from the FILE without losing it
    from their own library.
    """

    target = _target(store, image_id)
    if not target.path.exists():
        raise ActionRefused("That file is no longer where the Gallery left it.")
    try:
        from PIL import Image
    except ImportError as error:
        raise ActionRefused(
            "Metadata cannot be removed because Pillow is not installed."
        ) from error

    try:
        with Image.open(target.path) as image:
            # A NEW image carrying only the pixels. Copying the object would
            # bring its `info` dictionary -- and the parameters chunk with it,
            # which is the one thing this is supposed to remove.
            clean = Image.new(image.mode, image.size)
            clean.frombytes(image.tobytes())
            # Written beside and renamed, so an interrupted strip cannot leave
            # the owner with a truncated picture where their image used to be.
            temporary = target.path.with_suffix(
                f"{target.path.suffix}.stripping")
            clean.save(temporary, format=image.format)
    except (OSError, ValueError, TypeError) as error:
        raise ActionRefused(
            f"The metadata could not be removed: {type(error).__name__}"
        ) from error
    _unblocked(lambda: temporary.replace(target.path),
               "The metadata could not be removed")

    with store.write() as connection:
        # The search text came from the parameters that are now gone. A single
        # space marks it as looked at, so a rescan does not keep retrying.
        connection.execute(
            "UPDATE images SET search_text = ' ' WHERE id = ?", (image_id,))
    return {"ok": True, "filename": target.filename}


def convert(store: GalleryStore, image_id: int, target_format: str,
            quality: int = 90, lossless: bool = False,
            keep_original: bool = False) -> dict[str, Any]:
    """Save a picture in another format, beside the original."""

    wanted = (target_format or "").strip().lower().lstrip(".")
    formats = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
    if wanted not in formats:
        raise ActionRefused(f"Studio cannot convert to {target_format!r}.")
    target = _target(store, image_id)
    if not target.path.exists():
        raise ActionRefused("That file is no longer where the Gallery left it.")
    try:
        from PIL import Image
    except ImportError as error:
        raise ActionRefused(
            "Images cannot be converted because Pillow is not installed."
        ) from error

    destination = free_name(target.path.parent, f"{target.path.stem}.{wanted}")
    try:
        with Image.open(target.path) as image:
            options: dict[str, Any] = {}
            if formats[wanted] == "JPEG":
                # JPEG has no alpha. Flattening rather than failing, because an
                # owner converting a PNG to JPEG knows they are losing it.
                image = image.convert("RGB")
                options["quality"] = max(1, min(100, int(quality)))
            elif formats[wanted] == "WEBP":
                options["quality"] = max(1, min(100, int(quality)))
                options["lossless"] = bool(lossless)
            image.save(destination, formats[wanted], **options)
    except (OSError, ValueError, TypeError) as error:
        raise ActionRefused(
            f"The image could not be converted: {type(error).__name__}"
        ) from error

    if not keep_original:
        # The owner asked for the original to go, not to be unrecoverable.
        _discard(target.path)
        with store.write() as connection:
            connection.execute(
                "UPDATE images SET filepath = ?, filename = ? WHERE id = ?",
                (str(destination), destination.name, image_id))
    return {"ok": True, "filename": destination.name}


def next_number(store: GalleryStore, base_name: str,
                exclude: Iterable[int] = (),
                media_type: str = "") -> dict[str, Any]:
    """The next free counter for a `name 007`-style series.

    The Gallery's renaming numbers a set of pictures under one name. Asking
    the index rather than the directory means a series numbered across several
    folders keeps counting.
    """

    base = (base_name or "").strip()
    if not base:
        raise ActionRefused("A series needs a name.")
    excluded = {int(item) for item in exclude}
    pattern = re.compile(rf"^{re.escape(base)}\s*(\d+)$", re.IGNORECASE)
    highest = 0
    with store.read() as connection:
        rows = connection.execute(
            "SELECT id, filename, media_type FROM images"
        ).fetchall()
    for row in rows:
        if int(row["id"]) in excluded:
            continue
        if media_type and str(row["media_type"] or "") != media_type:
            continue
        found = pattern.match(Path(str(row["filename"])).stem)
        if found:
            highest = max(highest, int(found.group(1)))
    return {"ok": True, "next": highest + 1, "base_name": base}


# -- folders --------------------------------------------------------------


def create_folder(store: GalleryStore, parent: str, name: str) -> dict[str, Any]:
    """Make a directory inside an indexed folder."""

    name = (name or "").strip()
    if not name:
        raise ActionRefused("A folder needs a name.")
    if name != Path(name).name or name in (".", ".."):
        raise ActionRefused("A folder name cannot contain a path.")
    if not (parent or "").strip():
        raise ActionRefused("No parent folder was named.")
    directory = _folder_root(store, parent) / name
    if directory.exists():
        raise ActionRefused(f"There is already a folder called {name} there.")
    try:
        directory.mkdir(parents=True)
    except OSError as error:
        raise ActionRefused(
            f"The folder could not be created: {type(error).__name__}"
        ) from error
    return {"ok": True, "folder": f"{parent}\\{name}"}


def delete_folder(store: GalleryStore, folder: str) -> dict[str, Any]:
    """Send a folder's images to the trash, then remove the directory.

    Through the trash, one image at a time, rather than `rmtree`. A folder
    delete that cannot be undone is not a feature an owner should meet by
    accident, and the trash is what makes it undoable.
    """

    folder = (folder or "").strip()
    if not folder:
        raise ActionRefused("No folder was named.")
    directory = _folder_root(store, folder)
    with store.read() as connection:
        identifiers = [
            int(row["id"]) for row in connection.execute(
                "SELECT id FROM images WHERE folder = ? OR folder LIKE ?",
                (folder, f"{folder}\\%"),
            )
        ]
    for image_id in identifiers:
        delete_to_trash(store, image_id)

    removed = False
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            _unblocked(directory.rmdir, "The folder could not be removed")
            removed = True
    except (OSError, ActionRefused):
        # An empty folder that will not go is not worth failing the delete
        # over: the images -- the thing the owner asked to remove -- are gone.
        pass
    # An owner's own files that the Gallery never indexed are left where they
    # are: this deletes PICTURES, not everything that happened to be nearby.
    return {"ok": True, "images": len(identifiers), "folder_removed": removed}


def rename_folder(store: GalleryStore, folder: str,
                  new_name: str) -> dict[str, Any]:
    """Rename a directory, and re-file every image the index has under it."""

    folder = (folder or "").strip()
    new_name = (new_name or "").strip()
    if not folder or not new_name:
        raise ActionRefused("A folder and a new name are both needed.")
    if new_name != Path(new_name).name or new_name in (".", ".."):
        raise ActionRefused("A folder name cannot contain a path.")

    directory = _folder_root(store, folder)
    destination = directory.parent / new_name
    if destination.exists():
        raise ActionRefused(
            f"There is already a folder called {new_name} there.")
    # A DIRECTORY cannot be renamed while anything holds a file inside it
    # open, and the enrichment worker started by the last scan is reading
    # exactly those files. Same wait as the file operations.
    _unblocked(lambda: directory.rename(destination),
               "The folder could not be renamed")

    parts = folder.replace("/", "\\").split("\\")
    renamed = "\\".join(parts[:-1] + [new_name]) if len(parts) > 1 else new_name
    with store.write() as connection:
        # Every row under it, path and display folder together. A rename that
        # updated only the label would leave the index pointing at a directory
        # that no longer exists.
        for row in connection.execute(
            "SELECT id, filepath, folder FROM images "
            "WHERE folder = ? OR folder LIKE ?", (folder, f"{folder}\\%")
        ).fetchall():
            old = str(row["filepath"])
            fresh = old.replace(str(directory), str(destination), 1)
            label = str(row["folder"]).replace(folder, renamed, 1)
            connection.execute(
                "UPDATE images SET filepath = ?, folder = ? WHERE id = ?",
                (fresh, label, int(row["id"])))
        connection.execute(
            "UPDATE scan_folders SET path = ?, label = ? WHERE path = ?",
            (str(destination), new_name, str(directory)))
    return {"ok": True, "folder": renamed}


def empty_trash(store: GalleryStore) -> dict[str, Any]:
    """Let go of everything in the trash, through the platform's own bin.

    No longer the irreversible operation it was. Studio's trash is the undo
    window; emptying it hands each file to the Recycle Bin (or the desktop
    equivalent), which is where an owner will look for it. Only a host with no
    bin, or a file the shell refuses, is destroyed outright -- and the counts
    say which, so the caller never has to assume.
    """

    with store.read() as connection:
        rows = connection.execute(
            "SELECT id, trash_path FROM trash"
        ).fetchall()
    tally = {RECYCLED: 0, DESTROYED: 0, ALREADY_GONE: 0}
    removed = 0
    for row in rows:
        path = str(row["trash_path"] or "")
        if path:
            tally[_discard(Path(path))] += 1
        removed += 1
    with store.write() as connection:
        connection.execute("DELETE FROM trash")
    return {"ok": True, "removed": removed,
            "recycled": tally[RECYCLED], "destroyed": tally[DESTROYED]}


__all__ = (
    "MAX_RATING",
    "ActionRefused",
    "Target",
    "add_tag",
    "characters_in",
    "convert",
    "copy_to",
    "create_folder",
    "delete_folder",
    "delete_to_trash",
    "empty_trash",
    "free_name",
    "ignore_words",
    "list_trash",
    "move",
    "next_number",
    "record_generation",
    "remove_tag",
    "rename",
    "rename_folder",
    "rescan_characters",
    "restore",
    "set_ignore_word",
    "set_rating",
    "strip_metadata",
    "tag_from_filenames",
)
