"""Writing wildcard files, and the root that writing may not leave.

The read side of this project was found following a directory junction out of
the wildcard folder and expanding a file from outside it into a prompt. That
was proven with a real junction, not argued. The write side has the same hole
and worse consequences -- it would CREATE files out there -- so containment is
the first thing tested here and the thing every other case is checked against.

The donor is not the standard. Its live routes compare paths with
`startswith`, which passes a sibling folder whose name merely extends the
root's; it validates no filename characters, so `CON`, `a<b` and `a.txt:ads`
all reach the disk; it writes non-atomically, so a crash truncates a word list;
and it deletes with `unlink`. Each of those is a case below.

SCOPE: what may be written and where. Not the tree's shape for the page, which
is a wire contract, and not expansion, which is tested next door.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.wildcard_editor import (  # noqa: E402
    MAX_NAME_LENGTH,
    EditorRefused,
    WildcardEditor,
)


EXPECTED_WILDCARD_EDITOR_TESTS = 39


class _Rooted(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.root = self.base / "wildcards"
        self.root.mkdir()
        (self.root / "colour.txt").write_text(
            "red" + chr(10) + "# a comment" + chr(10) + chr(10) + "blue" + chr(10),
            encoding="utf-8")
        (self.root / "people").mkdir()
        (self.root / "people" / "knight.txt").write_text(
            "a knight" + chr(10), encoding="utf-8")
        self.editor = WildcardEditor(self.root)

    def outside(self) -> Path:
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir(exist_ok=True)
        (elsewhere / "secret.txt").write_text("LEAKED" + chr(10), encoding="utf-8")
        return elsewhere


class ContainmentTests(_Rooted):
    """The hard gate. Everything else is a convenience."""

    def test_a_traversal_path_is_refused(self) -> None:
        for escape in ("../secret", "../../secret", "people/../../secret"):
            with self.subTest(path=escape):
                with self.assertRaises(EditorRefused):
                    self.editor.read(escape + ".txt")

    def test_a_backslash_traversal_is_refused(self) -> None:
        """The page sends forward slashes; a caller is not the page."""
        with self.assertRaises(EditorRefused):
            self.editor.read("..\\secret.txt")

    def test_an_absolute_path_cannot_escape(self) -> None:
        """Rebuilt from validated segments, so a drive letter cannot survive."""
        with self.assertRaises(EditorRefused):
            self.editor.read(str(self.outside() / "secret.txt"))

    def test_a_junction_out_of_the_root_is_not_followed(self) -> None:
        """The read-side hole, on the write side. Reproduced, then closed."""
        link = self.root / "sub"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(self.outside())],
            capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            self.skipTest("this host cannot create a directory junction")
        with self.assertRaises(EditorRefused):
            self.editor.read("sub/secret.txt")
        with self.assertRaises(EditorRefused):
            self.editor.create_file("sub", "planted.txt")

    def test_a_junction_is_not_even_listed(self) -> None:
        """Offering a file every write would refuse is its own dishonesty."""
        link = self.root / "sub"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(self.outside())],
            capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            self.skipTest("this host cannot create a directory junction")
        self.assertNotIn("sub", repr(self.editor.tree()))

    def test_a_sibling_root_sharing_a_name_prefix_is_refused(self) -> None:
        """`startswith` passes this. `is_relative_to` does not.

        The donor's live routes compare with a bare string prefix, so a root of
        `.../wildcards` admits `.../wildcards-backup`.

        Reaching that gate needs a JUNCTION, not a `../` path -- a written
        traversal is refused several checks earlier, which is why the first
        version of this test passed against a deliberately lexical
        implementation and proved nothing. The junction gives a path whose
        segments are all clean and whose RESOLVED target is a sibling.
        """
        sibling = self.base / "wildcards-backup"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("LEAKED" + chr(10), encoding="utf-8")
        link = self.root / "sub"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(sibling)],
            capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            self.skipTest("this host cannot create a directory junction")
        with self.assertRaises(EditorRefused):
            self.editor.read("sub/secret.txt")

    def test_the_root_itself_cannot_be_renamed_or_deleted(self) -> None:
        for action in (lambda: self.editor.rename("", "gone"),
                       lambda: self.editor.delete("")):
            with self.subTest(action=action):
                with self.assertRaises(EditorRefused):
                    action()

    def test_no_folder_chosen_refuses_rather_than_guessing(self) -> None:
        with self.assertRaises(EditorRefused):
            WildcardEditor(None).tree()


class NameTests(_Rooted):
    """A name the operating system will give back, or a refusal."""

    def test_a_reserved_device_name_is_refused(self) -> None:
        """`CON.txt` is as unopenable as `CON`. The donor allows both."""
        for name in ("CON", "con.txt", "COM1", "nul", "LPT9.txt"):
            with self.subTest(name=name):
                with self.assertRaises(EditorRefused):
                    self.editor.create_file("", name)

    def test_an_alternate_data_stream_is_refused(self) -> None:
        """`a.txt:ads` writes a stream no listing shows. The donor allows it."""
        with self.assertRaises(EditorRefused):
            self.editor.create_file("", "a.txt:ads")

    def test_shell_and_wildcard_characters_are_refused(self) -> None:
        for name in ("a<b", "a>b", "a|b", "a?b", "a*b", 'a"b'):
            with self.subTest(name=name):
                with self.assertRaises(EditorRefused):
                    self.editor.create_file("", name)

    def test_a_trailing_dot_is_refused(self) -> None:
        """Windows drops it silently, so `moods.` would land as `moods`."""
        with self.assertRaises(EditorRefused):
            self.editor.create_file("", "trail.")

    def test_surrounding_whitespace_is_normalised_not_refused(self) -> None:
        """A typo, not an attack. This caught a real bug the other way round:
        the implementation stripped BEFORE checking for a trailing space, so
        the check could never fire and a padded name was quietly accepted."""
        self.assertEqual("trail.txt",
                         self.editor.create_file("", "  trail  ")["path"])

    def test_a_separator_in_a_name_is_refused(self) -> None:
        for name in ("a/b", "a\\b"):
            with self.subTest(name=name):
                with self.assertRaises(EditorRefused):
                    self.editor.create_file("", name)

    def test_an_overlong_name_is_refused(self) -> None:
        with self.assertRaises(EditorRefused):
            self.editor.create_file("", "x" * (MAX_NAME_LENGTH + 1))

    def test_an_empty_name_is_refused(self) -> None:
        for name in ("", "   "):
            with self.subTest(name=name):
                with self.assertRaises(EditorRefused):
                    self.editor.create_file("", name)

    def test_a_normal_name_with_spaces_and_dashes_is_accepted(self) -> None:
        """The read side resolves `__hair colour__`, so writing one must work."""
        created = self.editor.create_file("", "hair colour-2")
        self.assertEqual("hair colour-2.txt", created["path"])


class ReadTests(_Rooted):
    def test_the_tree_is_one_recursive_folder_node(self) -> None:
        tree = self.editor.tree()
        self.assertEqual("folder", tree["type"])
        self.assertEqual("", tree["path"])
        kinds = {child["name"]: child["type"] for child in tree["children"]}
        self.assertEqual("folder", kinds["people"])
        self.assertEqual("file", kinds["colour.txt"])

    def test_a_file_carries_its_entry_count(self) -> None:
        """Blanks and comments excluded, matching what the sampler draws from.

        A badge that counted raw lines would disagree with the choices.
        """
        tree = self.editor.tree()
        colour = [c for c in tree["children"] if c["name"] == "colour.txt"][0]
        self.assertEqual(2, colour["lines"])

    def test_only_txt_files_are_listed(self) -> None:
        (self.root / "notes.md").write_text("x" + chr(10), encoding="utf-8")
        self.assertNotIn("notes.md", repr(self.editor.tree()))

    def test_reading_returns_the_path_and_content_the_page_reads(self) -> None:
        opened = self.editor.read("people/knight.txt")
        self.assertEqual("people/knight.txt", opened["path"])
        self.assertIn("a knight", opened["content"])

    def test_reading_something_absent_refuses(self) -> None:
        with self.assertRaises(EditorRefused):
            self.editor.read("nosuchfile.txt")


class WriteTests(_Rooted):
    def test_saving_replaces_the_content(self) -> None:
        self.editor.save("colour.txt", "amber" + chr(10))
        self.assertEqual("amber" + chr(10),
                         (self.root / "colour.txt").read_text(encoding="utf-8"))

    def test_saving_leaves_no_temporary_behind(self) -> None:
        """Atomic means temp-then-replace, not temp-then-forget."""
        self.editor.save("colour.txt", "amber" + chr(10))
        self.assertEqual([], list(self.root.glob("*.studio-tmp")))

    def test_an_oversized_save_is_refused(self) -> None:
        from forge_studio.wildcards import MAX_FILE_BYTES

        with self.assertRaises(EditorRefused):
            self.editor.save("colour.txt", "x" * (MAX_FILE_BYTES + 1))

    def test_creating_a_file_adds_the_suffix(self) -> None:
        self.assertEqual("moods.txt", self.editor.create_file("", "moods")["path"])

    def test_creating_inside_a_folder_returns_the_full_path(self) -> None:
        """The page opens exactly what comes back."""
        self.assertEqual("people/rogue.txt",
                         self.editor.create_file("people", "rogue")["path"])

    def test_a_duplicate_name_says_already_exists(self) -> None:
        """The page string-matches this phrase to skip a file on an OS drop."""
        with self.assertRaises(EditorRefused) as raised:
            self.editor.create_file("", "colour.txt")
        self.assertIn("already exists", raised.exception.message)

    def test_creating_a_folder_works_and_nests(self) -> None:
        self.editor.create_folder("people", "knights")
        self.assertTrue((self.root / "people" / "knights").is_dir())

    def test_renaming_a_file_returns_its_new_path(self) -> None:
        renamed = self.editor.rename("people/knight.txt", "paladin")
        self.assertEqual("people/paladin.txt", renamed["new_path"])
        self.assertTrue((self.root / "people" / "paladin.txt").is_file())

    def test_renaming_a_folder_works_through_the_same_route(self) -> None:
        """The page does not say which it is sending."""
        renamed = self.editor.rename("people", "heroes")
        self.assertEqual("heroes", renamed["new_path"])
        self.assertTrue((self.root / "heroes" / "knight.txt").is_file())

    def test_renaming_onto_an_existing_name_is_refused(self) -> None:
        self.editor.create_file("", "taken.txt")
        with self.assertRaises(EditorRefused):
            self.editor.rename("colour.txt", "taken")


class DeleteTests(_Rooted):
    """To the platform's bin. Never straight to nothing."""

    def _spy(self):
        sent = []

        def fake(path):
            sent.append(str(path))
            target = Path(path)
            if target.is_dir():
                import shutil

                shutil.rmtree(target)
            else:
                target.unlink()

        return sent, fake

    def test_a_file_goes_to_the_platform_bin(self) -> None:
        import unittest.mock

        from forge_studio import gallery_actions

        sent, fake = self._spy()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            result = self.editor.delete("colour.txt")
        self.assertEqual(1, len(sent))
        self.assertTrue(result["recycled"])
        self.assertFalse((self.root / "colour.txt").exists())

    def test_a_folder_with_files_refuses_and_says_how_many(self) -> None:
        """The page reads BOTH keys to offer "delete anyway"."""
        with self.assertRaises(EditorRefused) as raised:
            self.editor.delete("people")
        self.assertTrue(raised.exception.extra["not_empty"])
        self.assertEqual(1, raised.exception.extra["file_count"])
        self.assertTrue((self.root / "people").is_dir())

    def test_force_deletes_the_folder(self) -> None:
        import unittest.mock

        from forge_studio import gallery_actions

        sent, fake = self._spy()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            self.editor.delete("people", force=True)
        self.assertEqual(1, len(sent))
        self.assertFalse((self.root / "people").exists())

    def test_an_empty_folder_needs_no_force(self) -> None:
        import unittest.mock

        from forge_studio import gallery_actions

        (self.root / "empty").mkdir()
        _, fake = self._spy()
        with unittest.mock.patch.object(
                gallery_actions, "_send_to_os_trash", fake):
            self.editor.delete("empty")
        self.assertFalse((self.root / "empty").exists())


class DeleteRouteTests(unittest.TestCase):
    """The HTTP verb, not the service beneath it.

    Every unit above passes with the service called directly, and Delete was
    still broken in the browser: `do_DELETE` handed the adapter `self._path()`,
    which strips the query string, so `?path=climate.txt` never arrived. The
    request resolved to the wildcard root and was refused by the guard that
    stops the root being deleted -- a correct refusal to a question nobody
    asked.

    `do_GET` has always passed the full path. Nothing caught the difference
    because the only other DELETE route ignores its query.
    """

    def test_delete_is_handed_the_query_string(self) -> None:
        source = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
            encoding="utf-8")
        body = source.split("def do_DELETE")[1].split("def ")[0]
        self.assertIn("self._source_adapter.delete(self.path)", body)
        self.assertNotIn("self._source_adapter.delete(path)", body)

    def test_the_path_helper_still_strips_the_query(self) -> None:
        """The fix is at the CALL, not in the helper.

        `_path()` is used for routing decisions all over this file and must
        keep answering the bare path; changing it would be the wrong repair.
        """
        source = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
            encoding="utf-8")
        body = source.split("def _path(self)")[1].split("def ")[0]
        self.assertIn("urlsplit(self.path).path", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_WILDCARD_EDITOR_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
