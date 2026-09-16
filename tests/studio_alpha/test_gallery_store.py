"""The Gallery's durable store — schema, versioning, and surviving a restart.

Gallery is an existing Studio module whose service the standalone never
received. `frontend/gallery.js` makes 35 backend calls and all 35 are backed by
a route in the Extension's `scripts/studio_gallery.py`, so this is a port rather
than a design. The first thing the service needs is somewhere to keep what it
knows, and that somewhere is the state root D1 resolved.

The schema is the Extension's, preserved rather than redesigned: the owner's
existing Gallery databases follow that lineage and a new shape would strand
them. What is added here is what the Extension left implicit -- a recorded
schema version, one transaction per structural change, a backup before any
destructive migration, and a failure that says so.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_store import (  # noqa: E402
    SCHEMA_VERSION,
    GalleryStore,
    GalleryStoreError,
    locate,
)

EXPECTED_GALLERY_STORE_TESTS = 27

#: Every table the Extension's Gallery depends on. Named explicitly so a
#: dropped table is a failure rather than a smaller number.
REQUIRED_TABLES = (
    "config", "scan_folders", "images", "characters", "image_characters",
    "dup_pairs", "ignore_words", "trash", "image_metadata",
)


class _Rooted(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._directory.name) / "ForgeStudio"
        self.addCleanup(self._directory.cleanup)

    def columns(self, store: GalleryStore, table: str) -> set[str]:
        with store.read() as connection:
            return {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }


class LocationTests(_Rooted):
    def test_the_gallery_lives_under_the_state_root(self) -> None:
        """D1 lists `gallery/` in STATE_LAYOUT so an owner who copies the state
        root takes their Gallery with them, and an update cannot delete it."""

        from forge_studio.state_root import STATE_LAYOUT

        self.assertIn("gallery/", STATE_LAYOUT)
        place = locate(self.root)
        self.assertEqual(self.root / "gallery", place.root)
        self.assertEqual(place.root / "gallery.db", place.database)

    def test_locating_creates_nothing(self) -> None:
        locate(self.root)
        self.assertFalse(self.root.exists())


class SchemaTests(_Rooted):
    def test_opening_creates_the_schema_and_its_directories(self) -> None:
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        place = locate(self.root)
        self.assertTrue(place.database.is_file())
        self.assertTrue(place.thumbnails.is_dir())
        self.assertTrue(place.trash.is_dir())

    def test_every_table_the_extension_gallery_needs_exists(self) -> None:
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        tables = set(store.tables())
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        self.assertEqual([], missing)

    def test_the_schema_version_is_recorded_in_the_file(self) -> None:
        """`PRAGMA user_version` rather than a table of our own: SQLite keeps
        it in the file header, so a future migration can read it without
        knowing anything else about the schema."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        self.assertEqual(SCHEMA_VERSION, store.schema_version())

    def test_studio_specific_metadata_columns_are_present(self) -> None:
        """`template` and `negative_template` are Studio's own additions to the
        metadata parser. The handoff is explicit that Studio-specific metadata
        semantics must not be lost to a TrackImage merge."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        columns = self.columns(store, "image_metadata")
        for column in ("prompt", "negative_prompt", "template",
                       "negative_template", "studio_dynamic_prompts", "seed",
                       "sampler", "scheduler"):
            with self.subTest(column=column):
                self.assertIn(column, columns)

    def test_metadata_can_exist_before_its_image_does(self) -> None:
        """The race the Extension migrated away from. Generation writes its
        parameters BEFORE the Gallery has scanned the file it just produced, so
        a metadata row keyed on image_id could never be written in time."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(content_hash, prompt) VALUES(?, ?)",
                ("abc123", "a knight"),
            )
        with store.read() as connection:
            row = connection.execute(
                "SELECT image_id, prompt FROM image_metadata "
                "WHERE content_hash='abc123'"
            ).fetchone()
        self.assertIsNone(row["image_id"])
        self.assertEqual("a knight", row["prompt"])

    def test_an_orphan_row_can_be_linked_once_the_image_is_scanned(self) -> None:
        """The other half: `image_id IS NULL` has to be able to match, which is
        exactly what a rowid-alias primary key made impossible."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(content_hash, prompt) VALUES(?, ?)",
                ("abc123", "a knight"),
            )
            connection.execute(
                "INSERT INTO images(filename, folder, filepath, content_hash) "
                "VALUES(?, ?, ?, ?)",
                ("a.png", "out", "/out/a.png", "abc123"),
            )
            image_id = connection.execute(
                "SELECT id FROM images WHERE filepath='/out/a.png'"
            ).fetchone()["id"]
            linked = connection.execute(
                "UPDATE image_metadata SET image_id=? "
                "WHERE content_hash=? AND image_id IS NULL",
                (image_id, "abc123"),
            ).rowcount
        self.assertEqual(1, linked)

    def test_two_rows_cannot_claim_the_same_content_hash(self) -> None:
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(content_hash) VALUES('dup')")
        with self.assertRaises(GalleryStoreError):
            with store.write() as connection:
                connection.execute(
                    "INSERT INTO image_metadata(content_hash) VALUES('dup')")

    def test_many_rows_may_have_no_content_hash(self) -> None:
        """Why the unique index is partial. A video, or a file that could not
        be decoded, has no pixel hash -- and a plain UNIQUE would let only one
        such row exist in the whole database."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute("INSERT INTO image_metadata(prompt) VALUES('a')")
            connection.execute("INSERT INTO image_metadata(prompt) VALUES('b')")
        with store.read() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM image_metadata WHERE content_hash=''"
            ).fetchone()[0]
        self.assertEqual(2, count)

    def test_the_image_row_carries_its_media_type_and_hashes(self) -> None:
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        columns = self.columns(store, "images")
        for column in ("media_type", "content_hash", "phash", "search_text"):
            with self.subTest(column=column):
                self.assertIn(column, columns)

    def test_opening_twice_is_idempotent(self) -> None:
        store = GalleryStore(self.root)
        store.open()
        self.addCleanup(store.close)
        tables = store.tables()
        store.open()
        self.assertEqual(tables, store.tables())

    def test_a_newer_database_is_refused_rather_than_downgraded(self) -> None:
        """The direction that loses data. A Studio that does not understand a
        newer schema must not touch it."""

        store = GalleryStore(self.root).open()
        with store.write() as connection:
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 5}")
        store.close()

        with self.assertRaises(GalleryStoreError) as raised:
            GalleryStore(self.root).open()
        self.assertIn("newer", str(raised.exception))


class DurabilityTests(_Rooted):
    def test_written_rows_survive_a_new_store_on_the_same_root(self) -> None:
        """The whole reason this is on the state root."""

        first = GalleryStore(self.root).open()
        with first.write() as connection:
            connection.execute(
                "INSERT INTO scan_folders(path, label) VALUES(?, ?)",
                ("/pictures", "Pictures"),
            )
        first.close()

        second = GalleryStore(self.root).open()
        self.addCleanup(second.close)
        self.assertEqual(1, second.statistics()["folders"])

    def test_a_failed_write_leaves_nothing_behind(self) -> None:
        """One transaction per change. A half-applied structural edit is the
        thing a schema version cannot rescue."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        # GalleryStoreError, not sqlite3.Error: the store converts database
        # failures into its own named refusal so callers never have to catch a
        # driver exception to know a write did not happen.
        with self.assertRaises(GalleryStoreError):
            with store.write() as connection:
                connection.execute(
                    "INSERT INTO scan_folders(path, label) VALUES(?, ?)",
                    ("/a", "A"),
                )
                connection.execute("INSERT INTO no_such_table VALUES(1)")
        self.assertEqual(0, store.statistics()["folders"])

    def test_foreign_keys_are_enforced(self) -> None:
        """SQLite defaults them OFF, and the schema's CASCADE deletes are what
        stop a character tag outliving the image it describes.

        Tested on `image_characters` rather than `image_metadata`, because
        metadata deliberately has no such key: it is content-hash-keyed so a
        moved or re-encoded file finds its parameters again."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute("INSERT INTO characters(name) VALUES('knight')")
        with self.assertRaises(GalleryStoreError):
            with store.write() as connection:
                connection.execute(
                    "INSERT INTO image_characters(image_id, character_id) "
                    "VALUES(?, ?)",
                    (9999, 1),
                )

    def test_using_a_closed_store_is_a_named_refusal(self) -> None:
        """Not an empty answer. A Gallery that cannot read its index must not
        look like a Gallery with no images."""

        store = GalleryStore(self.root)
        with self.assertRaises(GalleryStoreError):
            store.schema_version()


class AdoptionTests(_Rooted):
    """Opening a database the Extension wrote.

    An owner's existing `gallery.db` is the case that cannot be re-created if
    it goes wrong, and it arrives with no `user_version` at all -- so it looks
    exactly like a fresh file until you look at its tables.
    """

    #: The Extension's ORIGINAL shape: no content_hash anywhere, no media_type,
    #: and metadata keyed on the image's rowid.
    LEGACY = """
    CREATE TABLE images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL, folder TEXT NOT NULL,
        filepath TEXT NOT NULL UNIQUE,
        width INTEGER, height INTEGER, file_date REAL,
        search_text TEXT DEFAULT ''
    );
    CREATE TABLE image_metadata (
        image_id INTEGER PRIMARY KEY,
        prompt TEXT, negative_prompt TEXT, seed INTEGER, steps INTEGER,
        cfg REAL, sampler TEXT, scheduler TEXT, model TEXT, model_hash TEXT,
        denoising REAL, width INTEGER, height INTEGER, clip_skip INTEGER,
        hires_upscaler TEXT, hires_upscale REAL, raw_infotext TEXT,
        created_at REAL
    );
    CREATE TABLE characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );
    """

    def legacy_database(self) -> Path:
        place = locate(self.root)
        place.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(place.database)
        try:
            connection.executescript(self.LEGACY)
            connection.execute(
                "INSERT INTO images(filename, folder, filepath, search_text) "
                "VALUES('old.png', 'out', '/out/old.png', 'a knight')"
            )
            image_id = connection.execute(
                "SELECT id FROM images"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO image_metadata(image_id, prompt, seed) "
                "VALUES(?, 'a knight', 42)",
                (image_id,),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(0, self._version(place.database))
        return place.database

    @staticmethod
    def _version(database: Path) -> int:
        connection = sqlite3.connect(database)
        try:
            return int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
        finally:
            connection.close()

    def test_an_owners_rows_survive_adoption(self) -> None:
        """The assertion that matters. Everything else here is detail."""

        self.legacy_database()
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.read() as connection:
            image = connection.execute(
                "SELECT filename, search_text FROM images"
            ).fetchone()
            meta = connection.execute(
                "SELECT prompt, seed, image_id FROM image_metadata"
            ).fetchone()
        self.assertEqual("old.png", image["filename"])
        self.assertEqual("a knight", image["search_text"])
        self.assertEqual(42, meta["seed"])
        self.assertIsNotNone(meta["image_id"])

    def test_adoption_adds_the_columns_the_old_shape_lacks(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` does NOTHING to a table that already
        exists, so without an explicit ALTER pass an adopted database keeps its
        old columns and every write against a new one fails."""

        self.legacy_database()
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        for column in ("media_type", "content_hash", "phash"):
            with self.subTest(column=column):
                self.assertIn(column, self.columns(store, "images"))
        for column in ("content_hash", "template", "negative_template"):
            with self.subTest(column=column):
                self.assertIn(column, self.columns(store, "image_metadata"))

    def test_adoption_frees_the_metadata_table_from_its_image_key(self) -> None:
        """Proven by doing the thing the old shape made impossible."""

        self.legacy_database()
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        with store.write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(content_hash, prompt) "
                "VALUES('newhash', 'unscanned')"
            )
        with store.read() as connection:
            row = connection.execute(
                "SELECT image_id FROM image_metadata WHERE content_hash='newhash'"
            ).fetchone()
        self.assertIsNone(row["image_id"])

    def test_adoption_backs_the_database_up_first(self) -> None:
        """A database reporting version 0 is not necessarily empty -- the
        Extension never wrote a version at all. Backing up only when the
        version is above zero would skip every real owner's library."""

        self.legacy_database()
        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        backups = list(locate(self.root).root.glob("gallery.v*.bak"))
        self.assertEqual(1, len(backups))

    def test_a_fresh_database_is_not_backed_up(self) -> None:
        """The negative control: nothing to lose, nothing copied."""

        store = GalleryStore(self.root).open()
        self.addCleanup(store.close)
        self.assertEqual([], list(locate(self.root).root.glob("*.bak")))

    def test_adopting_twice_changes_nothing_further(self) -> None:
        self.legacy_database()
        first = GalleryStore(self.root).open()
        tables = first.tables()
        first.close()
        second = GalleryStore(self.root).open()
        self.addCleanup(second.close)
        self.assertEqual(tables, second.tables())
        self.assertEqual(1, second.statistics()["images"])
        self.assertEqual(
            1, len(list(locate(self.root).root.glob("gallery.v*.bak")))
        )


class SchemaPartitionTests(unittest.TestCase):
    def test_every_statement_is_either_a_table_or_an_index(self) -> None:
        """The schema is applied in two passes with an ALTER between them, so a
        statement matching neither filter would be dropped in silence and its
        table would simply never appear."""

        from forge_studio.gallery_store import _INDEXES, _SCHEMA, _TABLES

        self.assertEqual(len(_SCHEMA), len(_TABLES) + len(_INDEXES))
        self.assertEqual(set(_SCHEMA), set(_TABLES) | set(_INDEXES))

    def test_no_index_is_created_before_the_columns_it_names(self) -> None:
        """Stated as a property of the constants, because the failure it
        prevents only shows up on an adopted database."""

        from forge_studio.gallery_store import _INDEXES, _TABLES

        self.assertTrue(all(s.startswith("CREATE TABLE") for s in _TABLES))
        self.assertTrue(all("INDEX" in s for s in _INDEXES))


class BoundaryTests(unittest.TestCase):
    def test_the_store_imports_nothing_from_the_engine(self) -> None:
        """An index of files on disk needs no Torch, no Neo and no Gradio, and
        the Gallery must never be able to take generation down with it."""

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_store.py").read_text(
                encoding="utf-8"
            )
        )
        forbidden = ("torch", "modules", "modules_forge", "backend", "gradio",
                     "forge_headless")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                with self.subTest(name=name):
                    self.assertNotIn(name.split(".")[0], forbidden)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_STORE_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
