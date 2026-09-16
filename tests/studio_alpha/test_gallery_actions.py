"""Changing an owner's rows, and moving an owner's files.

Real files on disk, because half of what matters here is what happens when the
filesystem says no -- and the other half is that nothing is destroyed when it
does.
"""

from __future__ import annotations

import ast
import sys
import unittest.mock
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_actions import (  # noqa: E402
    ActionRefused,
    add_tag,
    characters_in,
    convert,
    copy_to,
    create_folder,
    delete_folder,
    delete_to_trash,
    empty_trash,
    free_name,
    list_trash,
    move,
    next_number,
    record_generation,
    remove_tag,
    rename,
    rename_folder,
    rescan_characters,
    restore,
    set_ignore_word,
    set_rating,
    strip_metadata,
    tag_from_filenames,
)
from forge_studio.gallery_store import GalleryStore  # noqa: E402
import forge_studio.gallery_actions as gallery_actions  # noqa: E402

EXPECTED_GALLERY_ACTION_TESTS = 85


class _Acting(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.pictures = base / "pictures"
        self.pictures.mkdir()
        self.store = GalleryStore(base / "state").open()
        self.addCleanup(self.store.close)

    def add(self, filename: str = "a.png", *, on_disk: bool = True) -> int:
        path = self.pictures / filename
        if on_disk:
            path.write_bytes(b"pretend this is a picture")
        with self.store.write() as connection:
            cursor = connection.execute(
                "INSERT INTO images(filename, folder, filepath, width, height, "
                "file_date, search_text) VALUES(?, 'pictures', ?, 8, 6, 1.0, 'k')",
                (filename, str(path)),
            )
            return int(cursor.lastrowid)

    def row(self, image_id: int):
        with self.store.read() as connection:
            return connection.execute(
                "SELECT * FROM images WHERE id = ?", (image_id,)
            ).fetchone()

    def tags(self, image_id: int) -> list[str]:
        with self.store.read() as connection:
            return [
                str(row["name"]) for row in connection.execute(
                    "SELECT c.name FROM characters c "
                    "JOIN image_characters ic ON c.id = ic.character_id "
                    "WHERE ic.image_id = ? ORDER BY ic.position",
                    (image_id,),
                )
            ]


class RatingTests(_Acting):
    def test_a_rating_is_recorded(self) -> None:
        image_id = self.add()
        self.assertEqual(4, set_rating(self.store, image_id, 4)["rating"])
        self.assertEqual(4, self.row(image_id)["rating"])

    def test_zero_clears_a_rating(self) -> None:
        image_id = self.add()
        set_rating(self.store, image_id, 5)
        set_rating(self.store, image_id, 0)
        self.assertEqual(0, self.row(image_id)["rating"])

    def test_a_rating_is_clamped_to_the_stars_that_exist(self) -> None:
        image_id = self.add()
        self.assertEqual(5, set_rating(self.store, image_id, 99)["rating"])
        self.assertEqual(0, set_rating(self.store, image_id, -3)["rating"])

    def test_a_rating_that_is_not_a_number_is_refused(self) -> None:
        image_id = self.add()
        with self.assertRaises(ActionRefused):
            set_rating(self.store, image_id, "excellent")

    def test_rating_an_unknown_image_is_refused(self) -> None:
        with self.assertRaises(ActionRefused):
            set_rating(self.store, 9999, 3)


class TagTests(_Acting):
    def test_a_tag_is_added(self) -> None:
        image_id = self.add()
        add_tag(self.store, image_id, "Aria")
        self.assertEqual(["Aria"], self.tags(image_id))

    def test_tagging_twice_is_not_an_error(self) -> None:
        image_id = self.add()
        add_tag(self.store, image_id, "Aria")
        add_tag(self.store, image_id, "Aria")
        self.assertEqual(["Aria"], self.tags(image_id))

    def test_tags_keep_the_order_they_were_added_in(self) -> None:
        image_id = self.add()
        for name in ("Aria", "Bran", "Cade"):
            add_tag(self.store, image_id, name)
        self.assertEqual(["Aria", "Bran", "Cade"], self.tags(image_id))

    def test_an_empty_tag_is_refused(self) -> None:
        image_id = self.add()
        with self.assertRaises(ActionRefused):
            add_tag(self.store, image_id, "   ")

    def test_a_tag_is_removed(self) -> None:
        image_id = self.add()
        add_tag(self.store, image_id, "Aria")
        remove_tag(self.store, image_id, "Aria")
        self.assertEqual([], self.tags(image_id))

    def test_a_character_nobody_wears_is_forgotten(self) -> None:
        """Otherwise it sits in the owner's sidebar forever with a count of
        zero, and nothing they can click will remove it."""

        image_id = self.add()
        add_tag(self.store, image_id, "Aria")
        remove_tag(self.store, image_id, "Aria")
        with self.store.read() as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM characters"
            ).fetchone()[0]
        self.assertEqual(0, remaining)

    def test_a_character_someone_else_wears_survives(self) -> None:
        first, second = self.add("a.png"), self.add("b.png")
        add_tag(self.store, first, "Aria")
        add_tag(self.store, second, "Aria")
        remove_tag(self.store, first, "Aria")
        self.assertEqual(["Aria"], self.tags(second))

    def test_an_ignore_word_can_be_set_and_cleared(self) -> None:
        set_ignore_word(self.store, "final")
        with self.store.read() as connection:
            self.assertEqual(
                1, connection.execute(
                    "SELECT COUNT(*) FROM ignore_words").fetchone()[0])
        set_ignore_word(self.store, "final", ignored=False)
        with self.store.read() as connection:
            self.assertEqual(
                0, connection.execute(
                    "SELECT COUNT(*) FROM ignore_words").fetchone()[0])


class RenameTests(_Acting):
    def test_a_file_is_renamed_on_disk_and_in_the_index(self) -> None:
        image_id = self.add("before.png")
        rename(self.store, image_id, "after.png")
        self.assertTrue((self.pictures / "after.png").exists())
        self.assertFalse((self.pictures / "before.png").exists())
        self.assertEqual("after.png", self.row(image_id)["filename"])
        self.assertTrue(
            str(self.row(image_id)["filepath"]).endswith("after.png"))

    def test_renaming_onto_an_existing_file_is_refused(self) -> None:
        """Never silently. The other file is an owner's picture too."""

        image_id = self.add("a.png")
        self.add("b.png")
        with self.assertRaises(ActionRefused):
            rename(self.store, image_id, "b.png")
        self.assertTrue((self.pictures / "a.png").exists())

    def test_a_name_containing_a_path_is_refused(self) -> None:
        """The containment rule. Without it a rename walks out of the folder
        the file lives in and lands anywhere the process can write."""

        image_id = self.add()
        for attempt in ("../escape.png", "sub/deeper.png", "..",
                        "C:\\Windows\\evil.png"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(ActionRefused):
                    rename(self.store, image_id, attempt)

    def test_an_empty_name_is_refused(self) -> None:
        image_id = self.add()
        with self.assertRaises(ActionRefused):
            rename(self.store, image_id, "  ")

    def test_renaming_a_file_that_has_gone_is_refused(self) -> None:
        image_id = self.add("ghost.png", on_disk=False)
        with self.assertRaises(ActionRefused):
            rename(self.store, image_id, "new.png")


class FreeNameTests(_Acting):
    def test_an_unused_name_is_returned_unchanged(self) -> None:
        self.assertEqual(self.pictures / "a.png",
                         free_name(self.pictures, "a.png"))

    def test_a_taken_name_gets_a_number(self) -> None:
        (self.pictures / "a.png").write_bytes(b"x")
        self.assertEqual(self.pictures / "a (2).png",
                         free_name(self.pictures, "a.png"))

    def test_the_number_climbs_past_what_is_taken(self) -> None:
        for name in ("a.png", "a (2).png", "a (3).png"):
            (self.pictures / name).write_bytes(b"x")
        self.assertEqual(self.pictures / "a (4).png",
                         free_name(self.pictures, "a.png"))


class TrashTests(_Acting):
    def test_deleting_moves_the_file_rather_than_destroying_it(self) -> None:
        image_id = self.add("a.png")
        result = delete_to_trash(self.store, image_id)
        self.assertTrue(result["ok"])
        self.assertFalse((self.pictures / "a.png").exists())
        survivors = list(self.store.location.trash.iterdir())
        self.assertEqual(1, len(survivors))
        self.assertEqual(b"pretend this is a picture",
                         survivors[0].read_bytes())

    def test_the_index_forgets_a_deleted_image(self) -> None:
        image_id = self.add("a.png")
        delete_to_trash(self.store, image_id)
        self.assertIsNone(self.row(image_id))

    def test_the_trash_remembers_where_the_file_came_from(self) -> None:
        image_id = self.add("a.png")
        add_tag(self.store, image_id, "Aria")
        delete_to_trash(self.store, image_id)
        entries = list_trash(self.store)
        self.assertEqual(1, len(entries))
        self.assertEqual("a.png", entries[0]["filename"])
        self.assertEqual("pictures", entries[0]["folder"])

    def test_the_trash_listing_carries_no_path(self) -> None:
        image_id = self.add("a.png")
        delete_to_trash(self.store, image_id)
        for value in list_trash(self.store)[0].values():
            with self.subTest(value=value):
                self.assertNotIn(str(self.pictures), str(value))

    def test_deleting_an_image_whose_file_has_gone_still_clears_the_row(
        self,
    ) -> None:
        """The index is out of date and the delete is what fixes it. Refusing
        would leave a row the owner can see and cannot remove."""

        image_id = self.add("ghost.png", on_disk=False)
        delete_to_trash(self.store, image_id)
        self.assertIsNone(self.row(image_id))

    def test_two_files_with_one_name_do_not_collide_in_the_trash(self) -> None:
        first = self.add("a.png")
        (self.pictures / "sub").mkdir()
        second_path = self.pictures / "sub" / "a.png"
        second_path.write_bytes(b"a different picture")
        with self.store.write() as connection:
            cursor = connection.execute(
                "INSERT INTO images(filename, folder, filepath) "
                "VALUES('a.png', 'pictures\\sub', ?)", (str(second_path),))
            second = int(cursor.lastrowid)
        delete_to_trash(self.store, first)
        delete_to_trash(self.store, second)
        self.assertEqual(2, len(list(self.store.location.trash.iterdir())))
        self.assertEqual(2, len(list_trash(self.store)))


class RestoreTests(_Acting):
    def test_a_deleted_file_comes_back(self) -> None:
        image_id = self.add("a.png")
        trash_id = delete_to_trash(self.store, image_id)["trash_id"]
        result = restore(self.store, trash_id)
        self.assertTrue(result["ok"])
        self.assertTrue((self.pictures / "a.png").exists())
        self.assertEqual(b"pretend this is a picture",
                         (self.pictures / "a.png").read_bytes())
        self.assertEqual([], list_trash(self.store))

    def test_a_restored_file_is_indexed_again(self) -> None:
        image_id = self.add("a.png")
        trash_id = delete_to_trash(self.store, image_id)["trash_id"]
        restore(self.store, trash_id)
        with self.store.read() as connection:
            self.assertEqual(
                1, connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])

    def test_its_tags_come_back_too(self) -> None:
        image_id = self.add("a.png")
        add_tag(self.store, image_id, "Aria")
        trash_id = delete_to_trash(self.store, image_id)["trash_id"]
        restore(self.store, trash_id)
        with self.store.read() as connection:
            restored = connection.execute(
                "SELECT id FROM images").fetchone()["id"]
        self.assertEqual(["Aria"], self.tags(restored))

    def test_restoring_never_overwrites_what_is_there_now(self) -> None:
        """A data-loss bug inside the feature whose whole purpose is undoing
        one. The owner made a new file with that name; it is not ours to
        replace."""

        image_id = self.add("a.png")
        trash_id = delete_to_trash(self.store, image_id)["trash_id"]
        (self.pictures / "a.png").write_bytes(b"something else entirely")

        result = restore(self.store, trash_id)
        self.assertTrue(result["renamed"])
        self.assertEqual(b"something else entirely",
                         (self.pictures / "a.png").read_bytes())
        self.assertEqual(b"pretend this is a picture",
                         (self.pictures / "a (2).png").read_bytes())

    def test_restoring_an_unknown_item_is_refused(self) -> None:
        with self.assertRaises(ActionRefused):
            restore(self.store, 9999)

    def test_restoring_a_file_missing_from_the_trash_is_refused(self) -> None:
        image_id = self.add("a.png")
        trash_id = delete_to_trash(self.store, image_id)["trash_id"]
        for entry in self.store.location.trash.iterdir():
            entry.unlink()
        with self.assertRaises(ActionRefused):
            restore(self.store, trash_id)

    def test_emptying_the_trash_destroys_it(self) -> None:
        image_id = self.add("a.png")
        delete_to_trash(self.store, image_id)
        self.assertEqual(1, empty_trash(self.store)["removed"])
        self.assertEqual([], list_trash(self.store))
        self.assertEqual([], list(self.store.location.trash.iterdir()))


class GenerationRecordTests(_Acting):
    """What a finished generation leaves behind.

    Recorded the moment the picture exists -- before any scan, and possibly
    before the owner has ever opened the Gallery. This is what `image_metadata`
    being keyed by content hash with a nullable image_id is FOR.
    """

    INFOTEXT = (
        "a knight on a hill\n"
        "Negative prompt: blurry\n"
        "Template: a __character__\n"
        "Steps: 30, Sampler: Euler, Schedule type: Beta 57, Seed: 7, "
        "Size: 8x6, Model: Anitox"
    )

    def stored(self, content_hash: str = "abc123"):
        with self.store.read() as connection:
            return connection.execute(
                "SELECT * FROM image_metadata WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()

    def test_the_parameters_are_recorded_before_any_scan(self) -> None:
        record_generation(self.store, "abc123", self.INFOTEXT)
        row = self.stored()
        self.assertEqual("a knight on a hill", row["prompt"])
        self.assertEqual("Euler", row["sampler"])
        self.assertEqual("Beta 57", row["scheduler"])
        self.assertIsNone(row["image_id"])

    def test_the_studio_template_is_recorded(self) -> None:
        record_generation(self.store, "abc123", self.INFOTEXT)
        self.assertEqual("a __character__", self.stored()["template"])

    def test_the_raw_infotext_is_kept_verbatim(self) -> None:
        """The Canvas rebuilds a generation from it, so it has to be the text
        that was written, not a re-rendering of the parsed fields."""

        record_generation(self.store, "abc123", self.INFOTEXT)
        self.assertEqual(self.INFOTEXT, self.stored()["raw_infotext"])

    def test_the_engines_settings_win_over_the_infotext(self) -> None:
        """A seed of -1 in the prompt box is a real number by the time the
        picture exists, and the engine is the one that knows it."""

        record_generation(self.store, "abc123", self.INFOTEXT,
                          {"seed": 999, "sampler": "DPM++ 2M"})
        row = self.stored()
        self.assertEqual(999, row["seed"])
        self.assertEqual("DPM++ 2M", row["sampler"])

    def test_a_settings_key_that_is_not_a_column_is_ignored(self) -> None:
        """The dictionary comes from the engine, and a column list rather than
        a loop over its keys is what stops it inventing one."""

        record_generation(self.store, "abc123", self.INFOTEXT,
                          {"nonsense": 1, "'; DROP TABLE images--": 2})
        self.assertIsNotNone(self.stored())

    def test_recording_twice_replaces_rather_than_collides(self) -> None:
        """The unique index on content_hash would otherwise refuse a rerun of
        the same image."""

        record_generation(self.store, "abc123", self.INFOTEXT)
        record_generation(self.store, "abc123", "a different prompt\nSteps: 4")
        with self.store.read() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM image_metadata").fetchone()[0]
        self.assertEqual(1, count)
        self.assertEqual("a different prompt", self.stored()["prompt"])

    def test_an_already_indexed_image_is_linked_at_once(self) -> None:
        """No waiting for the next scan when the file is already known."""

        image_id = self.add("a.png")
        with self.store.write() as connection:
            connection.execute(
                "UPDATE images SET content_hash = 'abc123' WHERE id = ?",
                (image_id,))
        result = record_generation(self.store, "abc123", self.INFOTEXT)
        self.assertTrue(result["linked"])
        self.assertEqual(image_id, self.stored()["image_id"])

    def test_a_recording_without_a_hash_is_refused(self) -> None:
        """An empty hash would collide with every other empty one under the
        partial unique index, and identifies nothing anyway."""

        with self.assertRaises(ActionRefused):
            record_generation(self.store, "", self.INFOTEXT)


class CharacterNameTests(unittest.TestCase):
    """Who a filename says is in the picture.

    The Extension's conventions, because an owner's existing library is
    already named to them.
    """

    def test_a_plain_name_is_one_character(self) -> None:
        self.assertEqual(["Aria"], characters_in("Aria.png"))

    def test_plus_and_comma_separate_people(self) -> None:
        self.assertEqual(["Luca", "Oliver"], characters_in("Oliver+Luca.png"))
        self.assertEqual(["Luca", "Oliver"], characters_in("Oliver, Luca.png"))

    def test_underscores_and_dashes_join_one_name(self) -> None:
        """The other half of the same rule, and the one that goes wrong if a
        parser treats every separator alike: `Shoyo_Hinata` is one person."""

        self.assertEqual(["Shoyo Hinata"], characters_in("Shoyo_Hinata.png"))
        self.assertEqual(["Mary Jane"], characters_in("mary-jane.png"))

    def test_a_trailing_counter_is_not_part_of_the_name(self) -> None:
        self.assertEqual(["Aria"], characters_in("Aria 007.png"))

    def test_bracketed_asides_come_off(self) -> None:
        self.assertEqual(["Aria"], characters_in("Aria (final) [v2].png"))

    def test_a_possessive_comes_off(self) -> None:
        self.assertEqual(["Aria"], characters_in("Aria's.png"))

    def test_serial_numbers_and_hashes_are_not_names(self) -> None:
        self.assertEqual(["Aria"], characters_in("Aria_00123_a1b2c3d4e5.png"))

    def test_an_ignored_word_is_dropped(self) -> None:
        self.assertEqual(["Aria"],
                         characters_in("Aria+Final", ignore={"final"}))

    def test_a_filename_with_no_name_in_it_is_unknown(self) -> None:
        """Never empty. "Unknown" is a bucket an owner can click; an empty
        list is a picture that vanishes from the sidebar."""

        self.assertEqual(["Unknown"], characters_in("00123.png"))
        self.assertEqual(["Unknown"], characters_in("(2).png"))


class TaggingFromFilenamesTests(_Acting):
    def test_an_image_is_tagged_from_its_name(self) -> None:
        image_id = self.add("Aria+Bran.png")
        self.assertEqual(["Aria", "Bran"],
                         tag_from_filenames(self.store, image_id))
        self.assertEqual(["Aria", "Bran"], self.tags(image_id))

    def test_a_rescan_reapplies_the_current_ignore_list(self) -> None:
        """What an owner presses after editing the ignore words. The old tags
        were parsed under the old list, so they have to be redone."""

        image_id = self.add("Aria+Final.png")
        tag_from_filenames(self.store, image_id)
        self.assertIn("Final", self.tags(image_id))

        set_ignore_word(self.store, "final")
        rescan_characters(self.store)
        self.assertEqual(["Aria"], self.tags(image_id))

    def test_a_rescan_leaves_a_hand_made_tag_alone(self) -> None:
        """A tag the owner typed is not the parser's to remove."""

        image_id = self.add("Aria.png")
        add_tag(self.store, image_id, "Someone Else")
        rescan_characters(self.store)
        self.assertIn("Someone Else", self.tags(image_id))


class MoveAndCopyTests(_Acting):
    def setUp(self) -> None:
        super().setUp()
        (self.pictures / "sub").mkdir()
        with self.store.write() as connection:
            connection.execute(
                "INSERT INTO scan_folders(path, label) VALUES(?, 'pictures')",
                (str(self.pictures),))

    def test_a_file_moves_into_another_folder(self) -> None:
        image_id = self.add("a.png")
        move(self.store, image_id, "pictures\\sub")
        self.assertTrue((self.pictures / "sub" / "a.png").exists())
        self.assertFalse((self.pictures / "a.png").exists())
        self.assertEqual("pictures\\sub", self.row(image_id)["folder"])

    def test_a_move_onto_a_taken_name_gets_a_free_one(self) -> None:
        """Never overwrites. The file already there is an owner's picture."""

        image_id = self.add("a.png")
        (self.pictures / "sub" / "a.png").write_bytes(b"someone else")
        move(self.store, image_id, "pictures\\sub")
        self.assertEqual(b"someone else",
                         (self.pictures / "sub" / "a.png").read_bytes())
        self.assertTrue((self.pictures / "sub" / "a (2).png").exists())

    def test_a_copy_leaves_the_original_and_indexes_the_copy(self) -> None:
        image_id = self.add("a.png")
        copy_to(self.store, image_id, "pictures\\sub")
        self.assertTrue((self.pictures / "a.png").exists())
        self.assertTrue((self.pictures / "sub" / "a.png").exists())
        with self.store.read() as connection:
            self.assertEqual(2, connection.execute(
                "SELECT COUNT(*) FROM images").fetchone()[0])

    def test_moving_to_a_folder_the_gallery_does_not_know_is_refused(
        self,
    ) -> None:
        """The containment rule for folders. A destination is resolved through
        the indexed scan folders, so it cannot name somewhere else on disk."""

        image_id = self.add("a.png")
        with self.assertRaises(ActionRefused):
            move(self.store, image_id, "somewhere-else")


class NextNumberTests(_Acting):
    def test_an_unused_series_starts_at_one(self) -> None:
        self.assertEqual(1, next_number(self.store, "Aria")["next"])

    def test_the_counter_continues_past_what_exists(self) -> None:
        self.add("Aria 001.png")
        self.add("Aria 007.png")
        self.assertEqual(8, next_number(self.store, "Aria")["next"])

    def test_the_images_being_renamed_do_not_count(self) -> None:
        """Otherwise renaming a series pushes its own numbers up every time."""

        first = self.add("Aria 001.png")
        self.assertEqual(1, next_number(self.store, "Aria",
                                        exclude=[first])["next"])

    def test_a_series_needs_a_name(self) -> None:
        with self.assertRaises(ActionRefused):
            next_number(self.store, "  ")


class FolderTests(_Acting):
    def setUp(self) -> None:
        super().setUp()
        with self.store.write() as connection:
            connection.execute(
                "INSERT INTO scan_folders(path, label) VALUES(?, 'pictures')",
                (str(self.pictures),))

    def test_a_folder_is_created_inside_an_indexed_one(self) -> None:
        create_folder(self.store, "pictures", "2026")
        self.assertTrue((self.pictures / "2026").is_dir())

    def test_a_folder_name_containing_a_path_is_refused(self) -> None:
        for attempt in ("../escape", "a/b", ".."):
            with self.subTest(attempt=attempt):
                with self.assertRaises(ActionRefused):
                    create_folder(self.store, "pictures", attempt)

    def test_creating_over_an_existing_folder_is_refused(self) -> None:
        create_folder(self.store, "pictures", "2026")
        with self.assertRaises(ActionRefused):
            create_folder(self.store, "pictures", "2026")

    def test_deleting_a_folder_sends_its_images_to_the_trash(self) -> None:
        """Through the trash, one at a time, rather than a recursive delete.
        A folder delete an owner cannot undo is not something they should meet
        by accident."""

        (self.pictures / "old").mkdir()
        path = self.pictures / "old" / "a.png"
        path.write_bytes(b"a picture")
        with self.store.write() as connection:
            connection.execute(
                "INSERT INTO images(filename, folder, filepath) "
                "VALUES('a.png', 'pictures\\old', ?)", (str(path),))
        result = delete_folder(self.store, "pictures\\old")
        self.assertEqual(1, result["images"])
        self.assertEqual(1, len(list_trash(self.store)))
        self.assertEqual([], self.rows_in("pictures\\old"))

    def test_a_folder_is_renamed_on_disk_and_in_every_row(self) -> None:
        """A rename that updated only the label would leave the index pointing
        at a directory that no longer exists."""

        (self.pictures / "old").mkdir()
        path = self.pictures / "old" / "a.png"
        path.write_bytes(b"a picture")
        with self.store.write() as connection:
            connection.execute(
                "INSERT INTO images(filename, folder, filepath) "
                "VALUES('a.png', 'pictures\\old', ?)", (str(path),))

        rename_folder(self.store, "pictures\\old", "new")
        self.assertTrue((self.pictures / "new").is_dir())
        self.assertFalse((self.pictures / "old").exists())
        with self.store.read() as connection:
            row = connection.execute(
                "SELECT folder, filepath FROM images").fetchone()
        self.assertEqual("pictures\\new", row["folder"])
        self.assertTrue(Path(str(row["filepath"])).exists())

    def test_renaming_onto_an_existing_folder_is_refused(self) -> None:
        (self.pictures / "old").mkdir()
        (self.pictures / "taken").mkdir()
        with self.assertRaises(ActionRefused):
            rename_folder(self.store, "pictures\\old", "taken")

    def rows_in(self, folder: str) -> list:
        with self.store.read() as connection:
            return connection.execute(
                "SELECT id FROM images WHERE folder = ?", (folder,)
            ).fetchall()


class MetadataStrippingTests(_Acting):
    def setUp(self) -> None:
        super().setUp()
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")

    def add_png(self, name: str = "a.png") -> int:
        from PIL import Image, PngImagePlugin

        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "a knight\nSteps: 30, Sampler: Euler")
        path = self.pictures / name
        Image.new("RGB", (20, 15), (10, 20, 30)).save(path, pnginfo=info)
        with self.store.write() as connection:
            cursor = connection.execute(
                "INSERT INTO images(filename, folder, filepath, search_text) "
                "VALUES(?, 'pictures', ?, 'a knight')", (name, str(path)))
            return int(cursor.lastrowid)

    def test_the_parameters_leave_the_file(self) -> None:
        from forge_studio.gallery_index import read_metadata

        image_id = self.add_png()
        path = Path(str(self.row(image_id)["filepath"]))
        self.assertEqual("a knight", read_metadata(path)["prompt"])
        strip_metadata(self.store, image_id)
        self.assertNotIn("prompt", read_metadata(path))

    def test_the_picture_itself_survives(self) -> None:
        from PIL import Image

        image_id = self.add_png()
        path = Path(str(self.row(image_id)["filepath"]))
        strip_metadata(self.store, image_id)
        with Image.open(path) as image:
            self.assertEqual((20, 15), image.size)
            self.assertEqual((10, 20, 30), image.getpixel((0, 0)))

    def test_the_database_row_is_left_alone(self) -> None:
        """The point of stripping: remove the prompt from the FILE an owner is
        about to share, without losing it from their own library."""

        image_id = self.add_png()
        with self.store.write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(image_id, prompt) "
                "VALUES(?, 'a knight')", (image_id,))
        strip_metadata(self.store, image_id)
        with self.store.read() as connection:
            self.assertEqual("a knight", connection.execute(
                "SELECT prompt FROM image_metadata WHERE image_id = ?",
                (image_id,)).fetchone()["prompt"])


class ConversionTests(_Acting):
    def setUp(self) -> None:
        super().setUp()
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")

    def add_png(self, mode: str = "RGB") -> int:
        from PIL import Image

        path = self.pictures / "a.png"
        Image.new(mode, (20, 15), (10, 20, 30, 255)[:len(mode)]).save(path)
        with self.store.write() as connection:
            cursor = connection.execute(
                "INSERT INTO images(filename, folder, filepath) "
                "VALUES('a.png', 'pictures', ?)", (str(path),))
            return int(cursor.lastrowid)

    def test_a_png_becomes_a_jpeg(self) -> None:
        from PIL import Image

        image_id = self.add_png()
        result = convert(self.store, image_id, "jpg")
        self.assertEqual("a.jpg", result["filename"])
        with Image.open(self.pictures / "a.jpg") as image:
            self.assertEqual("JPEG", image.format)

    def test_the_index_follows_the_converted_file(self) -> None:
        image_id = self.add_png()
        convert(self.store, image_id, "webp")
        self.assertEqual("a.webp", self.row(image_id)["filename"])
        self.assertFalse((self.pictures / "a.png").exists())

    def test_the_original_can_be_kept(self) -> None:
        image_id = self.add_png()
        convert(self.store, image_id, "webp", keep_original=True)
        self.assertTrue((self.pictures / "a.png").exists())
        self.assertTrue((self.pictures / "a.webp").exists())

    def test_transparency_is_flattened_for_jpeg_rather_than_failing(
        self,
    ) -> None:
        """JPEG has no alpha. An owner converting a transparent PNG knows they
        are losing it; an exception would just look broken."""

        image_id = self.add_png("RGBA")
        convert(self.store, image_id, "jpg")
        self.assertTrue((self.pictures / "a.jpg").exists())

    def test_an_unknown_format_is_refused(self) -> None:
        image_id = self.add_png()
        with self.assertRaises(ActionRefused):
            convert(self.store, image_id, "tiff-but-not-really")


class BoundaryTests(unittest.TestCase):
    def test_the_actions_import_nothing_from_the_engine(self) -> None:
        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_actions.py").read_text(
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

    def test_deleting_uses_a_move_that_crosses_volumes(self) -> None:
        """`os.rename` raises across drives, and an owner's pictures are very
        often on a different disk from the Studio state folder -- so the
        Extension's delete failed for exactly those owners."""

        # Over the AST, not the text. The first version grepped the source and
        # failed on the DOCSTRING, which names `os.rename` to explain why it is
        # not used -- a test that cannot tell an explanation from a call.
        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_actions.py").read_text(
                encoding="utf-8"
            )
        )
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                value = node.func.value
                if isinstance(value, ast.Name):
                    called.add(f"{value.id}.{node.func.attr}")
        self.assertIn("shutil.move", called)
        self.assertNotIn("os.rename", called)


class PlatformDisposalTests(_Acting):
    """Letting go of a file, versus destroying it.

    Studio's own trash is the undo window and must stay a plain folder --
    Ctrl+Z restores by row id from `original_filepath`, and the operating
    system's bin cannot be asked that portably. But EMPTYING it used to call
    `unlink`, so an owner's pictures were destroyed outright and never touched
    the Recycle Bin at any point. These fix the destination, not the holding
    pen, and the spy keeps the suite out of the real bin.
    """

    def _spy(self):
        sent = []

        def fake(path):
            sent.append(str(path))
            Path(path).unlink()

        return sent, fake

    def _trashed(self) -> None:
        """One file sitting in Studio's trash, ready to be let go of."""
        delete_to_trash(self.store, self.add("gone.png"))

    def test_emptying_hands_each_file_to_the_platform(self) -> None:
        self._trashed()
        sent, fake = self._spy()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            result = empty_trash(self.store)
        self.assertEqual(1, len(sent))
        self.assertEqual(1, result["recycled"])
        self.assertEqual(0, result["destroyed"])
        self.assertEqual([], list(self.store.location.trash.iterdir()))

    def test_a_host_with_no_bin_still_empties_and_says_so(self) -> None:
        """Absent is not a reason to leave the file sitting there."""
        self._trashed()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", None):
            result = empty_trash(self.store)
        self.assertEqual(0, result["recycled"])
        self.assertEqual(1, result["destroyed"])
        self.assertEqual([], list(self.store.location.trash.iterdir()))

    def test_a_shell_that_refuses_falls_back_rather_than_stalling(self) -> None:
        """Network shares and locked files land here, and must not accumulate."""
        self._trashed()

        def refuse(path):
            raise OSError("this volume has no recycle bin")

        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", refuse):
            result = empty_trash(self.store)
        self.assertEqual(0, result["recycled"])
        self.assertEqual(1, result["destroyed"])
        self.assertEqual([], list(self.store.location.trash.iterdir()))

    def test_a_conversion_that_drops_its_original_recycles_it(self) -> None:
        """The owner asked for it to go, not to be unrecoverable."""
        from PIL import Image

        path = self.pictures / "shot.png"
        Image.new("RGB", (20, 15), (10, 20, 30)).save(path)
        with self.store.write() as connection:
            image_id = int(connection.execute(
                "INSERT INTO images(filename, folder, filepath) "
                "VALUES('shot.png', 'pictures', ?)", (str(path),)).lastrowid)
        original = self.row(image_id)["filepath"]
        sent, fake = self._spy()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            convert(self.store, image_id, "webp", keep_original=False)
        self.assertEqual([original], sent)

    def test_studios_own_trash_is_still_a_plain_folder(self) -> None:
        """The guard on the obvious "simplification".

        Routing `delete_to_trash` at the OS bin too would read as tidier and
        would silently break Ctrl+Z, because restore needs a path it owns.
        """
        sent, fake = self._spy()
        image_id = self.add("keep.png")
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            delete_to_trash(self.store, image_id)
        self.assertEqual([], sent)
        self.assertEqual(1, len(list(self.store.location.trash.iterdir())))
        self.assertEqual(1, len(list_trash(self.store)))

    def test_the_real_recycler_is_wired_in(self) -> None:
        """A silently failed import would make every case above fall back."""
        self.assertIsNotNone(
            gallery_actions._send_to_os_trash,
            "Send2Trash is pinned in requirements.txt but did not import")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_ACTION_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
