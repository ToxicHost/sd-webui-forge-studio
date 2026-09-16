"""What the grid asks for, and what it is told.

Rows are inserted directly rather than scanned: this suite is about the query,
and going through the scanner would make every assertion depend on Pillow
decoding a file correctly as well.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_query import (  # noqa: E402
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    ImageQuery,
    handle_for,
    image,
    image_by_hash,
    list_images,
    resolve_handle,
    statistics,
    suggest,
)
from forge_studio.gallery_store import GalleryStore  # noqa: E402

EXPECTED_GALLERY_QUERY_TESTS = 40


class _Indexed(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.store = GalleryStore(Path(self._directory.name) / "state").open()
        self.addCleanup(self.store.close)

    def add(self, filename: str, folder: str = "output", *, search: str = "",
            rating: int = 0, date: float = 1000.0, media: str = "image",
            digest: str = "", characters: tuple[str, ...] = ()) -> int:
        with self.store.write() as connection:
            cursor = connection.execute(
                "INSERT INTO images(filename, folder, filepath, width, height, "
                "file_date, search_text, media_type, content_hash, rating) "
                "VALUES(?, ?, ?, 8, 6, ?, ?, ?, ?, ?)",
                (filename, folder, f"/pictures/{folder}/{filename}", date,
                 search, media, digest, rating),
            )
            image_id = cursor.lastrowid
            for position, name in enumerate(characters):
                connection.execute(
                    "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
                character_id = connection.execute(
                    "SELECT id FROM characters WHERE name=? COLLATE NOCASE",
                    (name,),
                ).fetchone()["id"]
                connection.execute(
                    "INSERT INTO image_characters(image_id, character_id, "
                    "position) VALUES(?, ?, ?)",
                    (image_id, character_id, position),
                )
        return int(image_id)

    def names(self, **values: object) -> list[str]:
        found = list_images(self.store, ImageQuery.from_request(values))
        return [row["filename"] for row in found["images"]]


class ParameterTests(unittest.TestCase):
    def test_defaults_are_the_grids_defaults(self) -> None:
        query = ImageQuery.from_request({})
        self.assertEqual(1, query.page)
        self.assertEqual(DEFAULT_PER_PAGE, query.per_page)
        self.assertEqual("filename", query.sort)
        self.assertEqual("asc", query.order)

    def test_a_page_size_cannot_be_unbounded(self) -> None:
        """An unclamped per_page is a request for the whole library in one
        response, which is a denial of service dressed as a query string."""

        self.assertEqual(MAX_PER_PAGE,
                         ImageQuery.from_request({"per_page": 10_000}).per_page)

    def test_a_page_cannot_be_negative(self) -> None:
        """A negative page is a negative OFFSET, which SQLite rejects at
        runtime -- so the grid would show an error instead of a first page."""

        self.assertEqual(1, ImageQuery.from_request({"page": -5}).page)

    def test_nonsense_numbers_fall_back(self) -> None:
        query = ImageQuery.from_request({"page": "abc", "per_page": None})
        self.assertEqual(1, query.page)
        self.assertEqual(DEFAULT_PER_PAGE, query.per_page)

    def test_an_unknown_sort_falls_back_to_filename(self) -> None:
        """A sort name reaches ORDER BY, where it cannot be a parameter. The
        allow-list is what stops it being SQL."""

        self.assertEqual(
            "filename",
            ImageQuery.from_request({"sort": "filename); DROP TABLE images--"}).sort,
        )

    def test_ratings_are_clamped_to_the_five_stars_that_exist(self) -> None:
        self.assertEqual(5, ImageQuery.from_request({"rating": 99}).rating)
        self.assertEqual(0, ImageQuery.from_request({"rating": -1}).rating)


class ListingTests(_Indexed):
    def test_an_empty_index_lists_nothing_without_failing(self) -> None:
        found = list_images(self.store, ImageQuery())
        self.assertEqual([], found["images"])
        self.assertEqual(0, found["total"])
        self.assertEqual(0, found["pages"])

    def test_filenames_sort_the_way_a_person_reads_them(self) -> None:
        """`image10` after `image2`. Plain text ordering gets this backwards,
        and filenames are numbered, so it gets it backwards constantly."""

        for name in ("image10.png", "image2.png", "image1.png"):
            self.add(name)
        self.assertEqual(["image1.png", "image2.png", "image10.png"],
                         self.names())

    def test_the_order_can_be_reversed(self) -> None:
        for name in ("a.png", "b.png"):
            self.add(name)
        self.assertEqual(["b.png", "a.png"], self.names(order="desc"))

    def test_newest_sorts_by_date(self) -> None:
        self.add("old.png", date=100.0)
        self.add("new.png", date=900.0)
        self.assertEqual(["new.png", "old.png"],
                         self.names(sort="newest", order="desc"))

    def test_pages_do_not_overlap_or_skip(self) -> None:
        for index in range(5):
            self.add(f"{index}.png")
        first = self.names(per_page=2, page=1)
        second = self.names(per_page=2, page=2)
        third = self.names(per_page=2, page=3)
        self.assertEqual(2, len(first))
        self.assertEqual(1, len(third))
        self.assertEqual(5, len(set(first + second + third)))

    def test_the_total_counts_every_match_not_the_page(self) -> None:
        """The grid uses it to decide whether to keep loading. A total equal to
        the page size means it stops after one page, forever."""

        for index in range(5):
            self.add(f"{index}.png")
        found = list_images(self.store, ImageQuery(per_page=2))
        self.assertEqual(5, found["total"])
        self.assertEqual(3, found["pages"])
        self.assertEqual(2, len(found["images"]))


class FilterTests(_Indexed):
    def test_a_folder_filter_includes_its_subtree(self) -> None:
        """Selecting a folder in a tree means "and everything under it"."""

        self.add("top.png", folder="output")
        self.add("deep.png", folder="output\\2026\\may")
        self.add("other.png", folder="elsewhere")
        self.assertEqual({"top.png", "deep.png"}, set(self.names(folder="output")))

    def test_a_folder_filter_does_not_match_a_sibling_by_prefix(self) -> None:
        """`output` must not select `output-old`."""

        self.add("mine.png", folder="output")
        self.add("theirs.png", folder="output-old")
        self.assertEqual(["mine.png"], self.names(folder="output"))

    def test_an_underscore_in_a_folder_name_is_not_a_wildcard(self) -> None:
        """Why the subtree test is GLOB and not LIKE. Under LIKE, `_` matches
        any character, so an owner with `my_pictures` would also see
        `myXpictures` -- and would have no way to tell why.

        Both folders need a SUBTREE for this to bite. The pattern ends in a
        separator, so `myXpictures` alone fails to match under LIKE too, and a
        version of this test without the subdirectories passes whichever
        operator is used -- which is exactly how it read before a mutation run
        showed it catching nothing.
        """

        self.add("mine.png", folder="my_pictures\\2026")
        self.add("theirs.png", folder="myXpictures\\2026")
        self.assertEqual(["mine.png"], self.names(folder="my_pictures"))

    def test_search_matches_the_indexed_prompt(self) -> None:
        self.add("a.png", search="a knight on a hill")
        self.add("b.png", search="a castle")
        self.assertEqual(["a.png"], self.names(search="knight"))

    def test_search_matches_a_filename(self) -> None:
        self.add("knight-01.png", search="")
        self.add("other.png", search="")
        self.assertEqual(["knight-01.png"], self.names(search="knight"))

    def test_search_matches_a_character_tag(self) -> None:
        self.add("a.png", characters=("Aria",))
        self.add("b.png")
        self.assertEqual(["a.png"], self.names(search="aria"))

    def test_two_search_terms_narrow_rather_than_widen(self) -> None:
        """AND across terms. OR would make every extra word return MORE
        results, which is the opposite of what typing more words means."""

        self.add("a.png", search="a knight on a hill")
        self.add("b.png", search="a knight in a castle")
        self.assertEqual(["a.png"], self.names(search="knight hill"))

    def test_a_comma_separates_search_terms(self) -> None:
        self.add("a.png", search="a knight on a hill")
        self.add("b.png", search="a knight in a castle")
        self.assertEqual(["a.png"], self.names(search="knight, hill"))

    def test_a_character_filter_selects_its_images(self) -> None:
        self.add("a.png", characters=("Aria", "Bran"))
        self.add("b.png", characters=("Bran",))
        self.assertEqual(["a.png"], self.names(character="Aria"))

    def test_a_character_filter_ignores_case(self) -> None:
        self.add("a.png", characters=("Aria",))
        self.assertEqual(["a.png"], self.names(character="aria"))

    def test_a_character_filter_does_not_duplicate_an_image(self) -> None:
        """The join multiplies rows. Without DISTINCT an image with three tags
        appears three times in the grid, and the total is wrong too."""

        self.add("a.png", characters=("Aria", "Bran", "Cade"))
        found = list_images(self.store, ImageQuery(search="a"))
        self.assertEqual(1, found["total"])
        self.assertEqual(1, len(found["images"]))

    def test_a_rating_filter_selects_that_rating(self) -> None:
        self.add("three.png", rating=3)
        self.add("five.png", rating=5)
        self.add("none.png")
        self.assertEqual(["three.png"], self.names(rating=3))

    def test_filters_combine(self) -> None:
        self.add("a.png", folder="output", search="knight", rating=5)
        self.add("b.png", folder="output", search="knight", rating=1)
        self.add("c.png", folder="elsewhere", search="knight", rating=5)
        self.assertEqual(["a.png"],
                         self.names(folder="output", search="knight", rating=5))


class HandleTests(_Indexed):
    def test_a_row_never_carries_a_filesystem_path(self) -> None:
        """The containment rule. A page that cannot see a path cannot leak the
        shape of the owner's disk, and cannot ask for a file by naming one."""

        self.add("a.png")
        row = list_images(self.store, ImageQuery())["images"][0]
        self.assertNotIn("filepath", row)
        for value in row.values():
            with self.subTest(value=value):
                self.assertNotIn("/pictures/", str(value))

    def test_a_handle_is_stable_for_the_same_path(self) -> None:
        """It has to survive a restart: the page holds handles in open tabs."""

        self.assertEqual(handle_for("/a/b.png"), handle_for("/a/b.png"))

    def test_different_paths_get_different_handles(self) -> None:
        self.assertNotEqual(handle_for("/a/b.png"), handle_for("/a/c.png"))

    def test_a_handle_resolves_only_for_an_indexed_file(self) -> None:
        """A handle is not a capability. Hashing the path of something the
        owner never indexed buys nothing."""

        self.add("a.png")
        row = list_images(self.store, ImageQuery())["images"][0]
        self.assertEqual("/pictures/output/a.png",
                         resolve_handle(self.store, row["fphash"]))
        self.assertIsNone(
            resolve_handle(self.store, handle_for("/etc/passwd")))
        self.assertIsNone(resolve_handle(self.store, ""))


class SingleImageTests(_Indexed):
    def test_an_image_can_be_fetched_by_id(self) -> None:
        image_id = self.add("a.png", characters=("Aria",))
        found = image(self.store, image_id)
        self.assertEqual("a.png", found["filename"])
        self.assertEqual(["Aria"], found["characters"])

    def test_a_missing_id_is_none_rather_than_an_error(self) -> None:
        self.assertIsNone(image(self.store, 9999))

    def test_an_image_can_be_fetched_by_content_hash(self) -> None:
        """How the Canvas hands over an image it just made: it knows the hash
        of what it produced, not the row id a scan will give it later."""

        self.add("a.png", digest="abc123")
        found = image_by_hash(self.store, "abc123")
        self.assertEqual("a.png", found["filename"])

    def test_an_unknown_hash_is_none(self) -> None:
        self.assertIsNone(image_by_hash(self.store, "nope"))
        self.assertIsNone(image_by_hash(self.store, ""))

    def test_a_video_row_says_it_is_a_video(self) -> None:
        self.add("clip.mp4", media="video")
        row = list_images(self.store, ImageQuery())["images"][0]
        self.assertTrue(row["is_video"])
        self.assertEqual("video", row["media_type"])


class SummaryTests(_Indexed):
    def test_statistics_count_what_the_header_shows(self) -> None:
        self.add("a.png", rating=4, characters=("Aria",))
        self.add("b.png")
        found = statistics(self.store)
        self.assertEqual(2, found["images"])
        self.assertEqual(1, found["rated"])
        self.assertEqual(1, found["characters"])

    def test_statistics_never_include_a_path(self) -> None:
        self.add("a.png")
        for value in statistics(self.store).values():
            with self.subTest(value=value):
                self.assertNotIn("/pictures", str(value))

    def test_suggestions_complete_a_character_name(self) -> None:
        self.add("a.png", characters=("Aria", "Arthur", "Bran"))
        self.assertEqual(["Aria", "Arthur"], suggest(self.store, "ar"))

    def test_an_empty_prefix_suggests_nothing(self) -> None:
        """Not everything. An empty box means the owner has typed nothing."""

        self.add("a.png", characters=("Aria",))
        self.assertEqual([], suggest(self.store, ""))


class BoundaryTests(unittest.TestCase):
    def test_the_query_imports_nothing_from_the_engine(self) -> None:
        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_query.py").read_text(
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
        self.assertEqual(EXPECTED_GALLERY_QUERY_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
