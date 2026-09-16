"""Wildcards: the files behind `__name__`, and what a prompt becomes.

Real files in real folders. Every property worth asserting here is about a
prompt meeting a directory, and a mock of the filesystem would only assert
that the module repeats itself.
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

from forge_studio.preferences import (  # noqa: E402
    PREFERENCE_KEYS,
    WildcardSettings,
)
from forge_studio.wildcard_service import WildcardService  # noqa: E402
from forge_studio.wildcards import (  # noqa: E402
    MAX_DEPTH,
    WildcardLibrary,
    resolve_root,
)

EXPECTED_WILDCARD_TESTS = 45


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.root = self.base / "wildcards"
        self.root.mkdir()

    def write(self, name: str, *lines: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def library(self) -> WildcardLibrary:
        return WildcardLibrary(self.root).load(force=True)


class DiscoveryTests(_Folder):
    def test_a_text_file_becomes_a_wildcard(self) -> None:
        self.write("colour.txt", "red", "blue")
        self.assertEqual(["colour"], self.library().names())

    def test_a_folder_becomes_part_of_the_name(self) -> None:
        """`people/knight.txt` is `__people/knight__`, with forward slashes on
        every platform -- that is what the page shows and what an owner types
        between the underscores."""

        self.write("people/knight.txt", "a knight")
        self.assertEqual(["people/knight"], self.library().names())

    def test_names_are_lowercased(self) -> None:
        """So `__Colour__` and `__colour__` are the same wildcard. An owner
        does not think of a filename as case-sensitive."""

        self.write("Colour.txt", "red")
        self.assertEqual(["colour"], self.library().names())
        self.assertEqual(["red"], self.library().lines("COLOUR"))

    def test_a_file_that_is_not_text_is_ignored(self) -> None:
        self.write("colour.txt", "red")
        (self.root / "notes.md").write_text("hello", encoding="utf-8")
        (self.root / "image.png").write_bytes(b"\x89PNG")
        self.assertEqual(["colour"], self.library().names())

    def test_an_entry_carries_the_size_the_browser_shows(self) -> None:
        self.write("colour.txt", "red", "blue", "green")
        entry = self.library().entries()[0]
        self.assertEqual("colour", entry["name"])
        self.assertIn("kb", entry)

    def test_a_missing_folder_is_empty_rather_than_an_error(self) -> None:
        library = WildcardLibrary(self.base / "nowhere").load()
        self.assertEqual([], library.names())
        self.assertFalse(library.available)

    def test_no_folder_at_all_is_empty_rather_than_an_error(self) -> None:
        library = WildcardLibrary(None).load()
        self.assertEqual([], library.names())
        self.assertFalse(library.available)


class ContentTests(_Folder):
    def test_the_lines_are_the_choices(self) -> None:
        self.write("colour.txt", "red", "blue")
        self.assertEqual(["red", "blue"], self.library().lines("colour"))

    def test_blank_lines_and_comments_are_dropped(self) -> None:
        """A word list an owner maintains by hand collects both, and neither
        is a thing to put in a prompt."""

        self.write("colour.txt", "red", "", "# a note", "blue", "   ")
        self.assertEqual(["red", "blue"], self.library().lines("colour"))

    def test_an_unknown_name_has_no_lines(self) -> None:
        self.assertEqual([], self.library().lines("nope"))


class ExpansionTests(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write("colour.txt", "red", "blue", "green")
        self.write("people/knight.txt", "a knight", "a paladin")

    def test_a_token_is_replaced_by_one_of_its_lines(self) -> None:
        found = self.library().expand("a __colour__ hall", 1)
        self.assertIn(found.text, ("a red hall", "a blue hall", "a green hall"))

    def test_the_same_seed_gives_the_same_prompt(self) -> None:
        """The entire contract of a seed. Without it, reusing a seed to
        reproduce a picture reproduces a different one."""

        first = self.library().expand("__colour__ __people/knight__", 42)
        second = self.library().expand("__colour__ __people/knight__", 42)
        self.assertEqual(first.text, second.text)

    def test_different_seeds_generally_differ(self) -> None:
        seen = {self.library().expand("__colour__", seed).text
                for seed in range(12)}
        self.assertGreater(len(seen), 1)

    def test_the_choices_are_recorded(self) -> None:
        """So the infotext can say which wildcard produced which word. The
        Gallery parser reads that line straight back out of a PNG."""

        found = self.library().expand("a __colour__ hall", 5)
        self.assertEqual(1, len(found.choices))
        self.assertEqual("colour", found.choices[0][0])
        self.assertIn(found.choices[0][1], ("red", "blue", "green"))
        self.assertTrue(found.summary.startswith("colour="))

    def test_a_nested_wildcard_resolves(self) -> None:
        """A chosen line may itself name a wildcard."""

        self.write("scene.txt", "__people/knight__ in a __colour__ hall")
        found = self.library().expand("__scene__", 3)
        self.assertNotIn("__", found.text)
        self.assertIn("hall", found.text)

    def test_an_unknown_wildcard_is_left_visible(self) -> None:
        """Replacing it with nothing silently changes the picture. Leaving the
        token lets the owner see which one Studio could not find."""

        found = self.library().expand("__nope__ and __colour__", 1)
        self.assertIn("__nope__", found.text)
        self.assertEqual(["nope"], found.missing)

    def test_a_wildcard_that_names_itself_terminates(self) -> None:
        """Otherwise it expands forever and takes the generation with it."""

        self.write("loop.txt", "__loop__")
        found = self.library().expand("__loop__", 1)
        self.assertTrue(found.truncated)

    def test_the_depth_bound_is_reported_not_hidden(self) -> None:
        for step in range(MAX_DEPTH + 3):
            self.write(f"step{step}.txt", f"__step{step + 1}__")
        found = self.library().expand("__step0__", 1)
        self.assertTrue(found.truncated)

    def test_a_prompt_with_no_wildcards_is_unchanged(self) -> None:
        found = self.library().expand("a plain prompt", 1)
        self.assertEqual("a plain prompt", found.text)
        self.assertEqual([], found.choices)

    def test_an_empty_prompt_is_unchanged(self) -> None:
        self.assertEqual("", self.library().expand("", 1).text)

    def test_a_name_cannot_climb_out_of_the_folder(self) -> None:
        """The containment rule. Every lookup goes through a dictionary built
        by walking the owner's folder, so a prompt cannot name a file the walk
        never found."""

        secret = self.base / "secret.txt"
        secret.write_text("the secret", encoding="utf-8")
        for attempt in ("__../secret__", "__../../secret__",
                        "__/etc/passwd__"):
            with self.subTest(attempt=attempt):
                found = self.library().expand(attempt, 1)
                self.assertNotIn("the secret", found.text)

    def test_expansion_without_a_folder_leaves_the_prompt_alone(self) -> None:
        library = WildcardLibrary(None).load()
        found = library.expand("a __colour__ hall", 1)
        self.assertEqual("a __colour__ hall", found.text)
        self.assertEqual(["colour"], found.missing)


class PreviewTests(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write("colour.txt", "red", "blue", "green", "gold", "silver")

    def test_several_samples_come_back(self) -> None:
        found = self.library().preview("__colour__", 3, samples=4)
        self.assertEqual(4, len(found["prompts"]))

    def test_the_samples_differ_from_each_other(self) -> None:
        """An owner asking for five previews wants five prompts, not one
        prompt five times."""

        found = self.library().preview("__colour__", 3, samples=8)
        self.assertGreater(len(set(found["prompts"])), 1)

    def test_a_fixed_seed_previews_the_same_set_every_time(self) -> None:
        first = self.library().preview("__colour__", 11, samples=5)
        second = self.library().preview("__colour__", 11, samples=5)
        self.assertEqual(first["prompts"], second["prompts"])

    def test_the_sample_count_is_bounded(self) -> None:
        found = self.library().preview("__colour__", 1, samples=10_000)
        self.assertLessEqual(len(found["prompts"]), 25)


class RootResolutionTests(_Folder):
    def test_a_chosen_folder_is_custom(self) -> None:
        path, mode = resolve_root(self.root)
        self.assertEqual(self.root, path)
        self.assertEqual("custom", mode)

    def test_a_chosen_folder_that_has_gone_is_still_custom(self) -> None:
        """Reporting `default` would hide from the owner that the folder they
        chose is missing."""

        _, mode = resolve_root(self.base / "gone")
        self.assertEqual("custom", mode)

    def test_a_fallback_is_default(self) -> None:
        path, mode = resolve_root("", [self.root])
        self.assertEqual(self.root, path)
        self.assertEqual("default", mode)

    def test_nowhere_to_look_is_disabled(self) -> None:
        path, mode = resolve_root("", [self.base / "gone"])
        self.assertIsNone(path)
        self.assertEqual("disabled", mode)


class ServiceTests(_Folder):
    def setUp(self) -> None:
        super().setUp()
        self.write("colour.txt", "red", "blue")
        self.service = WildcardService(WildcardSettings(self.base / "state"))

    def test_the_config_uses_the_names_the_page_reads(self) -> None:
        """`wildcard_folder_mode`, `wildcard_folder`, `wildcard_folder_display`
        -- app.js:5879 reads exactly those. The stub before this answered
        `folder_mode` and `folder`, so the panel said "Default folders"
        whatever the owner had chosen."""

        config = self.service.config()
        for name in ("wildcard_folder_mode", "wildcard_folder",
                     "wildcard_folder_display",
                     "studio_dynamic_prompts_enabled"):
            with self.subTest(field=name):
                self.assertIn(name, config)

    def test_choosing_a_folder_is_reported_back(self) -> None:
        found = self.service.select_folder({"folder": str(self.root)})
        self.assertTrue(found["ok"])
        self.assertEqual("custom", found["wildcard_folder_mode"])
        self.assertEqual("wildcards", found["wildcard_folder_display"])
        self.assertEqual(1, found["wildcard_count"])

    def test_a_folder_that_is_not_there_is_refused(self) -> None:
        found = self.service.select_folder({"folder": str(self.base / "gone")})
        self.assertFalse(found["ok"])
        self.assertIn("no folder", found["error"].lower())

    def test_an_empty_folder_resets_to_default(self) -> None:
        self.service.select_folder({"folder": str(self.root)})
        found = self.service.select_folder({"folder": ""})
        self.assertTrue(found["ok"])
        self.assertEqual("disabled", found["wildcard_folder_mode"])

    def test_the_choice_survives_a_new_service(self) -> None:
        """Stored in the preferences document on the state root. A choice held
        in memory is a setting that quietly forgets itself."""

        self.service.select_folder({"folder": str(self.root)})
        fresh = WildcardService(WildcardSettings(self.base / "state"))
        self.assertEqual(str(self.root), fresh.config()["wildcard_folder"])
        self.assertEqual("custom", fresh.config()["wildcard_folder_mode"])

    def test_the_wildcard_keys_stay_out_of_the_frontend_preferences(
        self,
    ) -> None:
        """`preferences.json` is defined as what the FRONTEND stores through
        /studio/prefs, and a test derives its allow-list by reading the
        frontend source. Two keys the page never posts do not belong in it,
        however convenient the file would be -- putting them there broke that
        test at once, which is the check working."""

        self.assertNotIn("wildcard_folder", PREFERENCE_KEYS)
        self.assertEqual(
            {"wildcard_folder", "studio_dynamic_prompts_enabled"},
            set(WildcardSettings.ALLOWED_KEYS),
        )

    def test_the_settings_document_is_its_own_file(self) -> None:
        settings = WildcardSettings(self.base / "state")
        self.assertEqual("wildcards.json", settings.path.name)

    def test_the_toggle_is_stored_and_reported(self) -> None:
        self.service.set_enabled({"studio_dynamic_prompts_enabled": True})
        self.assertTrue(self.service.enabled())
        self.assertTrue(
            self.service.config()["studio_dynamic_prompts_enabled"])

    def test_the_status_reports_what_is_there(self) -> None:
        self.service.select_folder({"folder": str(self.root)})
        status = self.service.status()
        self.assertTrue(status["available"])
        self.assertEqual(1, status["wildcard_count"])

    def test_the_listing_is_what_the_browser_renders(self) -> None:
        self.service.select_folder({"folder": str(self.root)})
        listing = self.service.listing()
        self.assertEqual(1, len(listing))
        self.assertEqual("colour", listing[0]["name"])
        self.assertIn("kb", listing[0])

    def test_expansion_never_raises_into_generation(self) -> None:
        """Called from the generation path, so the Gallery's rule holds here
        too: a wildcard folder that cannot be read must not turn a picture
        that would have been made into a failure."""

        class Exploding:
            def read(self):
                raise RuntimeError("the disk went away")

            def merge(self, *_a, **_k):
                raise RuntimeError("the disk went away")

        broken = WildcardService(Exploding())
        self.assertEqual("a __colour__ hall",
                         broken.expand("a __colour__ hall", 1).text)


class InfotextTests(unittest.TestCase):
    """What a generated image carries, and what the Gallery reads back."""

    def test_the_studio_fields_round_trip(self) -> None:
        from forge_studio.gallery_metadata import parse_generation_parameters
        from forge_studio.source_api_adapter import _infotext

        # The template and the choices are passed BESIDE the request, not in
        # it: the generation payload has a strict allow-list and neither is a
        # parameter of the picture.
        text = _infotext(
            {
                "positive_prompt": "a knight in a blue hall",
                "negative_prompt": "blurry",
                "steps": 30, "cfg_scale": 5.0, "width": 1024, "height": 1368,
            },
            "Anitox", 7,
            template="__people/knight__ in a __colour__ hall",
            choices="people/knight=a knight, colour=blue",
        )
        parsed = parse_generation_parameters(text)
        self.assertEqual("a knight in a blue hall", parsed["prompt"])
        self.assertEqual("__people/knight__ in a __colour__ hall",
                         parsed["template"])
        self.assertEqual("people/knight=a knight, colour=blue",
                         parsed["studio_dynamic_prompts"])
        self.assertEqual("blurry", parsed["negative_prompt"])

    def test_no_template_line_when_nothing_was_expanded(self) -> None:
        """A `Template:` identical to the prompt is noise in every infotext of
        every image made without wildcards."""

        from forge_studio.source_api_adapter import _infotext

        text = _infotext(
            {"positive_prompt": "a plain prompt",
             "steps": 30, "cfg_scale": 5.0},
            "Anitox", 1, template="a plain prompt", choices="",
        )
        self.assertNotIn("Template:", text)
        self.assertNotIn("Studio dynamic prompts:", text)


class BoundaryTests(unittest.TestCase):
    def test_wildcards_import_nothing_from_the_engine(self) -> None:
        for name in ("wildcards", "wildcard_service"):
            tree = ast.parse(
                (APP_ROOT / "forge_studio" / f"{name}.py").read_text(
                    encoding="utf-8"))
            forbidden = ("torch", "modules", "modules_forge", "backend",
                         "gradio", "numpy", "forge_headless")
            for node in ast.walk(tree):
                found: list[str] = []
                if isinstance(node, ast.Import):
                    found = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found = [node.module]
                for module in found:
                    with self.subTest(name=name, module=module):
                        self.assertNotIn(module.split(".")[0], forbidden)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_WILDCARD_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
