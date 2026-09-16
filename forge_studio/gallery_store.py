"""The Gallery's durable store, on the state root D1 resolved.

Gallery is an existing Studio module. `frontend/gallery.js` is a complete client
for it -- all 35 of its backend calls are backed by a route in the Extension's
`scripts/studio_gallery.py`. What the standalone lacks is the service, and the
first thing a service needs is somewhere to keep what it knows.

```text
<state root>/gallery/gallery.db      the index
<state root>/gallery/thumbs/         derived, disposable
<state root>/gallery/trash/          reversible deletes
```

Under the state root, never beside the source checkout: D1 lists `gallery/` in
`STATE_LAYOUT` precisely so an owner who copies that directory between machines
takes their Gallery with them, and so an update or reinstall does not delete it.

The schema is the Extension's, preserved rather than redesigned. Studio Gallery
has a data lineage and the owner's existing databases follow it; inventing a new
shape here would strand them. What this module adds is what the Extension left
implicit:

* **a schema version**, so a future migration knows where it is starting from;
* **one transaction per structural change**, so a crash mid-migration leaves the
  old database rather than half a new one;
* **a backup before any destructive migration**, so "recovered" is a thing an
  owner can actually do;
* **truthful failure** -- a store that cannot open reports that, rather than
  answering an empty list and letting the Gallery look merely empty;
* **adoption**, so an existing Extension database is inspected and brought up
  to the current column list rather than assumed to match it.

THE METADATA TABLE IS KEYED BY CONTENT HASH, not by image id, and this is the
one place where copying the Extension's FIRST shape would have been a bug. The
Extension's own migration note says why:

```text
Old: image_id INTEGER PRIMARY KEY (requires Gallery scan before save -> race)
New: content_hash unique, image_id nullable (generation saves immediately by
     hash, Gallery scan links image_id later)
```

Generation finishes and writes its parameters before the Gallery has scanned
the file it just produced. `image_id INTEGER PRIMARY KEY` is a rowid alias, so
under it a row cannot exist before the image row does -- and the whole
orphan-linking mechanism (`UPDATE ... WHERE content_hash=? AND image_id IS
NULL`) can never match. There is deliberately NO cascade from images: the hash
is the durable identity, so a file that is moved, renamed or re-encoded finds
its parameters again on the next scan instead of losing them.

Studio-owned and Studio-only. This module imports nothing from Forge, Neo or
Torch: an index of files on disk needs none of it, and the Gallery must not be
able to take generation down with it.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

#: Bumped when the tables below change shape. Recorded in the database via
#: `PRAGMA user_version`, which SQLite stores in the file header -- no table of
#: our own, and readable without knowing anything else about the schema.
SCHEMA_VERSION = 1

GALLERY_DIRECTORY = "gallery"
DATABASE_FILENAME = "gallery.db"
THUMBNAIL_DIRECTORY = "thumbs"
TRASH_DIRECTORY = "trash"

#: The Extension's schema, preserved. Table and column names are load-bearing:
#: they are what the owner's existing Gallery databases already contain.
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS scan_folders ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "path TEXT NOT NULL UNIQUE, "
    "label TEXT "
    ")",
    "CREATE TABLE IF NOT EXISTS images ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "filename TEXT NOT NULL, folder TEXT NOT NULL, "
    "filepath TEXT NOT NULL UNIQUE, "
    "width INTEGER, height INTEGER, file_date REAL, "
    "search_text TEXT DEFAULT '', "
    "media_type TEXT DEFAULT 'image', "
    "content_hash TEXT, "
    "phash TEXT, "
    "rating INTEGER DEFAULT 0 "
    ")",
    "CREATE INDEX IF NOT EXISTS idx_images_folder ON images(folder)",
    "CREATE INDEX IF NOT EXISTS idx_images_hash ON images(content_hash)",
    "CREATE TABLE IF NOT EXISTS characters ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "name TEXT NOT NULL UNIQUE COLLATE NOCASE "
    ")",
    "CREATE TABLE IF NOT EXISTS image_characters ( "
    "image_id INTEGER NOT NULL, character_id INTEGER NOT NULL, "
    "position INTEGER DEFAULT 0, "
    "source TEXT DEFAULT 'auto', "
    "PRIMARY KEY (image_id, character_id), "
    "FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE, "
    "FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE "
    ")",
    "CREATE TABLE IF NOT EXISTS dup_pairs ( "
    "image_a INTEGER NOT NULL, "
    "image_b INTEGER NOT NULL, "
    "distance INTEGER NOT NULL, "
    "PRIMARY KEY (image_a, image_b) "
    ")",
    "CREATE INDEX IF NOT EXISTS idx_dup_dist ON dup_pairs(distance)",
    "CREATE INDEX IF NOT EXISTS idx_dup_a ON dup_pairs(image_a)",
    "CREATE INDEX IF NOT EXISTS idx_dup_b ON dup_pairs(image_b)",
    "CREATE TABLE IF NOT EXISTS ignore_words (word TEXT PRIMARY KEY COLLATE NOCASE)",
    "CREATE TABLE IF NOT EXISTS trash ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "original_filepath TEXT NOT NULL, "
    "original_folder TEXT, "
    "original_filename TEXT, "
    "trash_path TEXT NOT NULL, "
    "width INTEGER, height INTEGER, file_date REAL, "
    "search_text TEXT DEFAULT '', "
    "characters_json TEXT DEFAULT '[]', "
    "deleted_at REAL "
    ")",
    "CREATE TABLE IF NOT EXISTS image_metadata ( "
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "content_hash TEXT NOT NULL DEFAULT '', "
    "image_id INTEGER, "
    "prompt TEXT, "
    "negative_prompt TEXT, "
    "template TEXT, "
    "negative_template TEXT, "
    "studio_dynamic_prompts TEXT, "
    "seed INTEGER, "
    "steps INTEGER, "
    "cfg REAL, "
    "sampler TEXT, "
    "scheduler TEXT, "
    "model TEXT, "
    "model_hash TEXT, "
    "denoising REAL, "
    "width INTEGER, "
    "height INTEGER, "
    "clip_skip INTEGER, "
    "hires_upscaler TEXT, "
    "hires_upscale REAL, "
    "raw_infotext TEXT, "
    "created_at REAL, "
    "float_path TEXT DEFAULT '', "
    "blend_mask_path TEXT DEFAULT '' "
    ")",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_meta_hash "
    "ON image_metadata(content_hash) WHERE content_hash != ''",
    "CREATE INDEX IF NOT EXISTS idx_meta_imgid "
    "ON image_metadata(image_id) WHERE image_id IS NOT NULL",
)

#: Applied in three passes, not one: tables, then any columns an adopted
#: database is missing, and only THEN the indexes. An index names columns, and
#: an existing table is not replaced by `CREATE TABLE IF NOT EXISTS` -- so
#: building `idx_images_hash` before the ALTER pass asks an owner's old `images`
#: table for a `content_hash` it does not have yet.
_TABLES = tuple(s for s in _SCHEMA if s.startswith("CREATE TABLE"))
_INDEXES = tuple(s for s in _SCHEMA if "INDEX" in s.split("(")[0])

#: Columns added to the Extension's tables after their first release. An
#: existing database is brought up to this list with ALTER TABLE rather than
#: assumed to have them. `(table, column, declaration)`.
_ADDED_COLUMNS = (
    ("images", "media_type", "TEXT DEFAULT 'image'"),
    ("images", "content_hash", "TEXT"),
    ("images", "phash", "TEXT"),
    ("images", "rating", "INTEGER DEFAULT 0"),
    ("image_metadata", "template", "TEXT"),
    ("image_metadata", "negative_template", "TEXT"),
    ("image_metadata", "studio_dynamic_prompts", "TEXT"),
    ("image_metadata", "float_path", "TEXT DEFAULT ''"),
    ("image_metadata", "blend_mask_path", "TEXT DEFAULT ''"),
)

#: The columns carried across when `image_metadata` is rebuilt from the old
#: image_id-keyed shape. Named explicitly: `SELECT *` would depend on column
#: order, and the old table has no `content_hash` to select.
_METADATA_CARRIED = (
    "image_id", "prompt", "negative_prompt", "seed", "steps", "cfg",
    "sampler", "scheduler", "model", "model_hash", "denoising",
    "width", "height", "clip_skip", "hires_upscaler", "hires_upscale",
    "raw_infotext", "created_at",
)


def natural_key(text: str | None) -> str:
    """Sort key that reads numbers as numbers.

    Plain text ordering puts `image10.png` before `image2.png`, which is wrong
    in the only place an owner ever looks at a sorted list of their own
    filenames. Zero-padding each run of digits to a fixed width makes a plain
    string comparison order them the way a person would.

    Registered on the connection as a SQLite function so ORDER BY can use it,
    exactly as the Extension does -- ordering has to happen in the query, not
    after it, or every sort would have to load the whole library first.
    """

    if not text:
        return ""
    return re.sub(r"(\d+)", lambda found: found.group(1).zfill(10), str(text).lower())


class GalleryStoreError(Exception):
    """The Gallery store could not be opened or changed."""


@dataclass(frozen=True)
class StoreLocation:
    """Where the Gallery keeps things, all under one root."""

    root: Path
    database: Path
    thumbnails: Path
    trash: Path


def locate(state_root: str | Path) -> StoreLocation:
    """Where the Gallery's files belong. Pure: creates nothing."""

    root = Path(state_root) / GALLERY_DIRECTORY
    return StoreLocation(
        root=root,
        database=root / DATABASE_FILENAME,
        thumbnails=root / THUMBNAIL_DIRECTORY,
        trash=root / TRASH_DIRECTORY,
    )


class GalleryStore:
    """One SQLite database, opened once, guarded by one lock.

    `check_same_thread=False` with an explicit lock rather than a connection
    per thread: Studio serves from a thread pool, and a connection-per-request
    would open and close the database several times a second while a scan is
    running. The lock is held for the whole of each transaction, which is what
    makes a read-modify-write safe rather than merely atomic.
    """

    def __init__(self, state_root: str | Path) -> None:
        self.location = locate(state_root)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    # -- lifecycle --------------------------------------------------------

    def open(self) -> "GalleryStore":
        """Create the directories and the schema. Idempotent.

        Creating IS the point here, unlike the preference store: a Gallery with
        nowhere to write is not a Gallery. The state root itself is still only
        created when something actually needs it.
        """

        with self._lock:
            if self._connection is not None:
                return self
            try:
                for directory in (
                    self.location.root,
                    self.location.thumbnails,
                    self.location.trash,
                ):
                    directory.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(
                    self.location.database, check_same_thread=False
                )
                connection.row_factory = sqlite3.Row
                # WAL: a scan writing does not block the page reading. Foreign
                # keys are OFF by default in SQLite and the schema depends on
                # them for its CASCADE deletes.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                # Ordering happens in the query, so the function it orders by
                # has to live on the connection. `deterministic` lets SQLite
                # use it in an index later without re-evaluating per row.
                connection.create_function(
                    "natural_key", 1, natural_key, deterministic=True
                )
                self._connection = connection
                self._migrate(connection)
            except BaseException as error:
                # ANY failure closes the handle. The first version caught only
                # OSError and sqlite3.Error, so a refused migration -- which
                # raises GalleryStoreError -- escaped with the connection still
                # open and the store half-alive: `opened` said True, the file
                # stayed locked, and on Windows the directory could not even be
                # removed. A store that failed to open must hold nothing.
                if self._connection is not None:
                    try:
                        self._connection.close()
                    except sqlite3.Error:
                        pass
                self._connection = None
                if isinstance(error, GalleryStoreError):
                    raise
                raise GalleryStoreError(
                    f"The Gallery database could not be opened: "
                    f"{type(error).__name__}"
                ) from error
        return self

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @property
    def opened(self) -> bool:
        return self._connection is not None

    # -- schema -----------------------------------------------------------

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Bring the database to `SCHEMA_VERSION`, transactionally.

        A fresh file reports version 0 and gets the schema. An existing one at
        the current version is left alone. A future migration backs the file up
        FIRST -- a migration that fails after mangling the only copy is not a
        migration, it is a data-loss event with good intentions.
        """

        found = connection.execute("PRAGMA user_version").fetchone()[0]
        if found == SCHEMA_VERSION:
            return
        if found > SCHEMA_VERSION:
            raise GalleryStoreError(
                f"This Gallery database is version {found}, newer than this "
                f"Studio understands ({SCHEMA_VERSION}). It has not been "
                "changed. Use a newer Studio, or move the file aside."
            )
        # Back up whenever there is something to lose, which is NOT the same as
        # `found > 0`. A database written by the Extension carries no
        # `user_version` at all, so it arrives here reporting 0 with an owner's
        # entire library in it.
        if self._has_data(connection):
            self._backup(found)
        with connection:  # one transaction; rolls back on any error
            self._rebuild_metadata_if_image_keyed(connection)
            self._apply(connection, _TABLES)
            self._add_missing_columns(connection)
            self._apply(connection, _INDEXES)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _apply(connection: sqlite3.Connection,
               statements: tuple[str, ...]) -> None:
        """Run the schema one statement at a time.

        NOT `executescript`, which issues a COMMIT before it runs anything.
        That would end the transaction this migration is wrapped in and leave a
        half-applied schema behind on failure -- exactly the outcome the single
        transaction exists to prevent.
        """

        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _has_data(connection: sqlite3.Connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images'"
        ).fetchone() is not None

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }

    def _rebuild_metadata_if_image_keyed(
        self, connection: sqlite3.Connection
    ) -> None:
        """Move an old `image_metadata` off its image_id primary key.

        The Extension's first shape was `image_id INTEGER PRIMARY KEY`, which
        is a rowid alias -- so a row cannot exist before the image does, and
        `image_id IS NULL` can never match. Generation finishes and saves its
        parameters BEFORE the Gallery has scanned the file it just wrote, so
        that shape loses the parameters of every image until a scan runs, and
        loses them permanently if one never does.

        Rebuilt rather than altered: SQLite cannot drop a primary key in place.
        Inside the caller's transaction, so an interrupted rebuild rolls back
        to the old table rather than leaving neither.
        """

        if "image_metadata" not in {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }:
            return
        if "content_hash" in self._columns(connection, "image_metadata"):
            return

        carried = [
            name for name in _METADATA_CARRIED
            if name in self._columns(connection, "image_metadata")
        ]
        columns = ", ".join(carried)
        connection.execute(
            "ALTER TABLE image_metadata RENAME TO image_metadata_old"
        )
        self._apply(connection, _TABLES)
        if carried:
            connection.execute(
                f"INSERT INTO image_metadata ({columns}) "  # noqa: S608 - fixed names
                f"SELECT {columns} FROM image_metadata_old"
            )
        connection.execute("DROP TABLE image_metadata_old")

    def _add_missing_columns(self, connection: sqlite3.Connection) -> None:
        """Bring an existing table up to the current column list.

        `CREATE TABLE IF NOT EXISTS` does nothing to a table that already
        exists, so an adopted database keeps whatever columns it was written
        with. Every addition here is additive and safe to re-run.
        """

        for table, column, declaration in _ADDED_COLUMNS:
            if column in self._columns(connection, table):
                continue
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def _backup(self, from_version: int) -> None:
        """Copy the database aside before a structural change."""

        stamp = int(time.time())
        target = self.location.database.with_name(
            f"{self.location.database.stem}.v{from_version}.{stamp}.bak"
        )
        try:
            shutil.copy2(self.location.database, target)
        except OSError as error:
            raise GalleryStoreError(
                "The Gallery database could not be backed up before "
                f"migration, so nothing was changed: {type(error).__name__}"
            ) from error

    # -- access -----------------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """A transaction. Commits on success, rolls back on any exception."""

        with self._lock:
            connection = self._require()
            try:
                with connection:
                    yield connection
            except sqlite3.Error as error:
                raise GalleryStoreError(
                    f"The Gallery database write failed: {type(error).__name__}"
                ) from error

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._require()

    def _require(self) -> sqlite3.Connection:
        if self._connection is None:
            raise GalleryStoreError(
                "The Gallery database is not open."
            )
        return self._connection

    def schema_version(self) -> int:
        with self.read() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def tables(self) -> tuple[str, ...]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            ).fetchall()
        return tuple(str(row["name"]) for row in rows)

    def statistics(self) -> dict[str, Any]:
        """Counts only. Never a path, which is what `/stats` may report on."""

        with self.read() as connection:
            def count(table: str) -> int:
                try:
                    return int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed names
                        ).fetchone()[0]
                    )
                except sqlite3.Error:
                    return 0

            return {
                "images": count("images"),
                "folders": count("scan_folders"),
                "characters": count("characters"),
                "trash": count("trash"),
                "schema_version": self.schema_version(),
            }


__all__ = (
    "DATABASE_FILENAME",
    "GALLERY_DIRECTORY",
    "SCHEMA_VERSION",
    "GalleryStore",
    "GalleryStoreError",
    "StoreLocation",
    "locate",
    "natural_key",
)
