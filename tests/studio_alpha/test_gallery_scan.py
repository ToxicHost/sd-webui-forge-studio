"""Indexing real folders into a real store.

Against real files and a real SQLite database, because every property worth
asserting here is about the two of them meeting: what a second scan skips, what
a cancelled scan must not delete, and which row a generated image's parameters
end up attached to.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import threading
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_index import imaging_available  # noqa: E402
from forge_studio.gallery_scan import (  # noqa: E402
    FOLDER_SEPARATOR,
    GalleryScanner,
    ScanProgress,
    display_folder,
    media_type_of,
)
from forge_studio.gallery_store import GalleryStore  # noqa: E402

EXPECTED_GALLERY_SCAN_TESTS = 30

PARAMETERS = (
    "a knight on a hill\n"
    "Negative prompt: blurry\n"
    "Steps: 30, Sampler: Euler, Seed: 7, Size: 8x6, Model: Anitox"
)


class _Scanning(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.pictures = base / "pictures"
        self.pictures.mkdir()
        self.store = GalleryStore(base / "state").open()
        self.addCleanup(self.store.close)
        self.scanner = GalleryScanner(self.store)

    def png(self, name: str, parameters: str | None = PARAMETERS) -> Path:
        from PIL import Image, PngImagePlugin

        info = None
        if parameters is not None:
            info = PngImagePlugin.PngInfo()
            info.add_text("parameters", parameters)
        path = self.pictures / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (8, 6))
        image.putdata([
            ((x * 7 + len(name)) % 256, (y * 11) % 256, (x + y) % 256)
            for y in range(6) for x in range(8)
        ])
        image.save(path, pnginfo=info)
        return path

    def rows(self) -> list[dict]:
        with self.store.read() as connection:
            return [
                dict(row) for row in connection.execute(
                    "SELECT * FROM images ORDER BY filepath"
                )
            ]


class FolderTests(_Scanning):
    def test_a_folder_can_be_added_and_listed(self) -> None:
        self.scanner.add_folder(self.pictures, "Pictures")
        folders = self.scanner.folders()
        self.assertEqual(1, len(folders))
        self.assertEqual("Pictures", folders[0]["label"])

    def test_adding_the_same_folder_twice_adds_one(self) -> None:
        self.scanner.add_folder(self.pictures)
        self.scanner.add_folder(self.pictures)
        self.assertEqual(1, len(self.scanner.folders()))

    def test_removing_a_folder_removes_its_images(self) -> None:
        """Leaving them behind shows the owner pictures from a folder they
        just removed, with nothing to explain why they are still there."""

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertEqual(1, len(self.rows()))
        self.scanner.remove_folder(self.pictures)
        self.assertEqual([], self.rows())
        self.assertEqual([], self.scanner.folders())

    def test_scanning_with_no_folders_configured_does_nothing(self) -> None:
        result = self.scanner.scan()
        self.assertEqual(0, result.added)
        self.assertEqual(0, result.removed)


class IndexingTests(_Scanning):
    def test_a_scan_indexes_what_is_there(self) -> None:
        self.png("a.png")
        self.png("nested/b.png")
        self.scanner.add_folder(self.pictures)
        result = self.scanner.scan()
        self.assertEqual(2, result.added)
        self.assertEqual({"a.png", "b.png"},
                         {row["filename"] for row in self.rows()})

    def test_the_row_carries_what_the_WALK_knows(self) -> None:
        """Name, folder, date, kind -- everything a directory listing gives.

        NOT the dimensions, the pixel hash or the search text: those need the
        file opened, and a profile showed that single `open()` was half the
        scan. Seventeen thousand images went from twelve minutes to under two
        seconds by leaving them to the enrichment pass.
        """

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        row = self.rows()[0]
        self.assertEqual("a.png", row["filename"])
        self.assertEqual(self.pictures.name, row["folder"])
        self.assertEqual("image", row["media_type"])
        self.assertGreater(row["file_date"], 0)

    def test_the_scan_does_not_open_the_files(self) -> None:
        """The property the speed rests on, asserted rather than assumed.

        A file that cannot be decoded at all still indexes without complaint,
        because nothing tried to decode it."""

        (self.pictures / "broken.png").write_bytes(b"not a png at all")
        self.scanner.add_folder(self.pictures)
        result = self.scanner.scan()
        self.assertEqual(1, result.added)
        self.assertEqual(0, result.unreadable)

    def test_the_negative_prompt_is_not_in_the_search_text(self) -> None:
        """Carried through from the parser, and worth asserting at this level
        too: this is the row a search actually reads."""

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertNotIn("blurry", self.rows()[0]["search_text"])

    def test_a_second_scan_adds_nothing(self) -> None:
        """The property that makes a Gallery refreshable. Without it, every
        refresh re-decodes the whole library."""

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.assertEqual(1, self.scanner.scan().added)
        second = self.scanner.scan()
        self.assertEqual(0, second.added)
        self.assertEqual(0, second.seen)
        self.assertEqual(1, len(self.rows()))

    def test_a_new_file_is_picked_up_by_the_next_scan(self) -> None:
        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.png("b.png")
        self.assertEqual(1, self.scanner.scan().added)
        self.assertEqual(2, len(self.rows()))

    def test_a_vanished_file_is_removed(self) -> None:
        first = self.png("a.png")
        self.png("b.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        first.unlink()
        result = self.scanner.scan()
        self.assertEqual(1, result.removed)
        self.assertEqual(["b.png"], [row["filename"] for row in self.rows()])

    def test_an_unreadable_file_is_still_indexed(self) -> None:
        """It is on disk, so the owner should see it. Hiding a broken file
        makes the Gallery look like it lost one.

        Whether it can be DECODED is discovered later, by enrichment -- the
        scan never opens it."""

        (self.pictures / "broken.png").write_bytes(b"not a png")
        self.scanner.add_folder(self.pictures)
        result = self.scanner.scan()
        self.assertEqual(1, result.added)
        self.assertEqual(["broken.png"],
                         [row["filename"] for row in self.rows()])

    def test_an_unplugged_folder_is_skipped_not_fatal(self) -> None:
        """An owner with an external drive disconnected must still be able to
        browse the rest of their library."""

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.add_folder(self.pictures / "does-not-exist")
        result = self.scanner.scan()
        self.assertEqual(1, result.added)
        self.assertEqual(1, len(result.folders))


class CharacterTaggingTests(_Scanning):
    """A scan tags characters from filenames, as it indexes.

    A browser run found this missing: a freshly scanned library had an empty
    TAGS sidebar, and nothing on screen suggested that a "rescan characters"
    button would fill it. The Extension tags during the scan, and so does this.
    """

    def tags(self, filename: str) -> list[str]:
        with self.store.read() as connection:
            return [
                str(row["name"]) for row in connection.execute(
                    "SELECT c.name FROM characters c "
                    "JOIN image_characters ic ON c.id = ic.character_id "
                    "JOIN images i ON i.id = ic.image_id "
                    "WHERE i.filename = ? ORDER BY ic.position", (filename,))
            ]

    def test_a_scan_tags_from_the_filename(self) -> None:
        self.png("Aria+Bran.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertEqual(["Aria", "Bran"], self.tags("Aria+Bran.png"))

    def test_an_ignored_word_is_honoured_by_the_scan(self) -> None:
        """Read once for the whole scan, so an owner's ignore list applies to
        every file it indexes rather than to none of them."""

        with self.store.write() as connection:
            connection.execute("INSERT INTO ignore_words(word) VALUES('final')")
        self.png("Aria+Final.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertEqual(["Aria"], self.tags("Aria+Final.png"))

    def test_a_nameless_filename_is_filed_under_unknown(self) -> None:
        self.png("00123.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertEqual(["Unknown"], self.tags("00123.png"))


class DeferredWorkTests(_Scanning):
    """What the scan deliberately leaves for enrichment to fill in.

    Linking a generated image's parameters by content hash used to happen
    during the scan, which meant hashing every file inline. It happens in the
    enrichment pass now -- proven at the service level, where that pass runs.
    """

    def test_the_search_text_and_hash_start_empty(self) -> None:
        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        row = self.rows()[0]
        self.assertEqual("", row["search_text"])
        self.assertEqual("", row["content_hash"])

    def test_a_rescan_does_not_undo_enrichment(self) -> None:
        """The rescan skips known paths, so the columns enrichment filled stay
        filled. Re-inserting them empty would undo the expensive pass every
        time the owner pressed refresh."""

        self.png("a.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        with self.store.write() as connection:
            connection.execute(
                "UPDATE images SET search_text='a knight', content_hash='abc'")
        self.scanner.scan()
        row = self.rows()[0]
        self.assertEqual("a knight", row["search_text"])
        self.assertEqual("abc", row["content_hash"])


class CancellationTests(_Scanning):
    def test_a_cancelled_scan_says_so(self) -> None:
        for index in range(4):
            self.png(f"{index}.png")
        self.scanner.add_folder(self.pictures)
        cancel = threading.Event()
        cancel.set()
        scanner = GalleryScanner(self.store, cancel=cancel)
        result = scanner.scan()
        self.assertTrue(result.cancelled)
        self.assertEqual(0, result.added)

    def test_a_cancelled_scan_deletes_nothing(self) -> None:
        """The one that matters. A cancelled scan has not visited the folders
        it never reached, so pruning against its partial results would delete
        an owner's index for them."""

        for index in range(4):
            self.png(f"{index}.png")
        self.scanner.add_folder(self.pictures)
        self.scanner.scan()
        self.assertEqual(4, len(self.rows()))

        cancel = threading.Event()
        cancel.set()
        result = GalleryScanner(self.store, cancel=cancel).scan()
        self.assertTrue(result.cancelled)
        self.assertEqual(0, result.removed)
        self.assertEqual(4, len(self.rows()))

    def test_cancelling_part_way_keeps_what_was_already_indexed(self) -> None:
        """Per-file commits, seen from outside: work already done survives."""

        for index in range(6):
            self.png(f"{index}.png")
        self.scanner.add_folder(self.pictures)

        cancel = threading.Event()
        seen: list[ScanProgress] = []

        def stop_after_a_while(progress: ScanProgress) -> None:
            seen.append(progress)
            cancel.set()

        result = GalleryScanner(
            self.store, cancel=cancel, on_progress=stop_after_a_while
        ).scan()
        self.assertTrue(result.cancelled)
        self.assertTrue(seen)
        self.assertEqual(0, result.removed)


class ProgressTests(_Scanning):
    def test_progress_is_reported_with_a_real_total(self) -> None:
        """"412 of 9,000", not a number climbing towards nothing."""

        for index in range(3):
            self.png(f"{index}.png")
        self.scanner.add_folder(self.pictures)
        seen: list[ScanProgress] = []
        GalleryScanner(self.store, on_progress=seen.append).scan()
        self.assertTrue(seen)
        # Every row EXCEPT the opening "Counting" one, which is published
        # before the total is known precisely so the page has something to
        # draw while the tree is walked.
        counted = [p for p in seen if p.phase != "Counting"]
        self.assertTrue(counted)
        self.assertTrue(all(p.total == 3 for p in counted))
        self.assertEqual("Done", seen[-1].phase)

    def test_a_folder_is_announced_before_it_is_counted(self) -> None:
        """The page draws nothing until a folder row exists, and counting
        walks the whole tree first. Without this the owner presses scan and
        watches an unchanged screen for as long as the walk takes."""

        for index in range(3):
            self.png(f"{index}.png")
        self.scanner.add_folder(self.pictures)
        seen: list[ScanProgress] = []
        GalleryScanner(self.store, on_progress=seen.append).scan()
        self.assertEqual("Counting", seen[0].phase)
        self.assertEqual(self.pictures.name, seen[0].folder)
        self.assertEqual("Scanning", seen[1].phase)
        self.assertEqual(3, seen[1].total)

    def test_a_listener_that_throws_does_not_stop_the_scan(self) -> None:
        self.png("a.png")
        self.scanner.add_folder(self.pictures)

        def explode(_progress: ScanProgress) -> None:
            raise RuntimeError("the page went away")

        result = GalleryScanner(self.store, on_progress=explode).scan()
        self.assertEqual(1, result.added)


class LabellingTests(unittest.TestCase):
    def test_a_folders_own_files_are_filed_under_its_name(self) -> None:
        root = Path("/pictures/output")
        self.assertEqual("output", display_folder(root, root))

    def test_a_subtree_is_filed_under_the_joined_path(self) -> None:
        root = Path("/pictures/output")
        self.assertEqual(
            f"output{FOLDER_SEPARATOR}2026{FOLDER_SEPARATOR}may",
            display_folder(root, root / "2026" / "may"),
        )

    def test_the_separator_is_the_one_an_adopted_database_uses(self) -> None:
        """Not `os.sep`. The column is matched by equality when filtering, and
        an Extension-written database is full of backslashes -- mixing the two
        would file the same folder twice in one library."""

        self.assertEqual("\\", FOLDER_SEPARATOR)

    def test_media_types_are_told_apart(self) -> None:
        self.assertEqual("image", media_type_of(Path("a.png")))
        self.assertEqual("video", media_type_of(Path("a.mp4")))
        # Neither: shown inline like an image, but not a still to thumbnail.
        self.assertEqual("gif", media_type_of(Path("a.gif")))


class BoundaryTests(unittest.TestCase):
    def test_the_scanner_imports_nothing_from_the_engine(self) -> None:
        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_scan.py").read_text(
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
        self.assertEqual(EXPECTED_GALLERY_SCAN_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
