"""The Gallery metadata parser — Studio's fields, not just A1111's.

Ported from the Extension's `parse_sd_parameters`, which is TrackImage-derived
code Studio had already modified. Those modifications are the point of this
suite: a stock parser knows prompt and negative prompt, reads a `Template:` line
as more positive prompt, and therefore glues the unresolved wildcard template
onto the end of every prompt it parses.

The tests are written against the failure each rule prevents, because a parser
that merely "returns a dict" passes any assertion that only checks for keys.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_metadata import (  # noqa: E402
    clean_for_search,
    parse_generation_parameters,
    search_text_for,
)

EXPECTED_GALLERY_METADATA_TESTS = 25

#: One realistic Studio block, carrying every field that matters at once.
STUDIO_BLOCK = """a knight on a hill, (castle:1.4), <lora:crisp:0.8>
Negative prompt: blurry, watermark
Template: a __character__ on a __place__
Negative Template: blurry
Studio dynamic prompts: character=knight, place=hill
Steps: 30, Sampler: Euler, Schedule type: Beta 57, CFG scale: 5.0, Seed: 7, \
Size: 1024x1368, Model: Anitox, Model hash: abc123
SomeExtension: trailing junk that must not become prompt"""


class StudioFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_generation_parameters(STUDIO_BLOCK)

    def test_the_resolved_prompt_is_what_the_model_saw(self) -> None:
        self.assertEqual(
            "a knight on a hill, (castle:1.4), <lora:crisp:0.8>",
            self.parsed["prompt"],
        )

    def test_the_template_is_separate_from_the_prompt(self) -> None:
        """The Studio modification. A stock parser reads `Template:` as more
        positive prompt, so the unresolved wildcards end up glued to every
        prompt and then into the search index."""

        self.assertEqual("a __character__ on a __place__",
                         self.parsed["template"])
        self.assertNotIn("__character__", self.parsed["prompt"])

    def test_the_negative_template_is_its_own_field(self) -> None:
        """`Negative Template:` must be tested before `Template:` or it parses
        as a template whose text begins "Negative"."""

        self.assertEqual("blurry", self.parsed["negative_template"])

    def test_studio_dynamic_prompts_are_captured(self) -> None:
        self.assertEqual("character=knight, place=hill",
                         self.parsed["studio_dynamic_prompts"])

    def test_the_negative_prompt_is_separate(self) -> None:
        self.assertEqual("blurry, watermark", self.parsed["negative_prompt"])


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_generation_parameters(STUDIO_BLOCK)

    def test_the_settings_line_is_read(self) -> None:
        self.assertEqual("30", self.parsed["steps"])
        self.assertEqual("Euler", self.parsed["sampler"])
        self.assertEqual("7", self.parsed["seed"])
        self.assertEqual("1024x1368", self.parsed["size"])
        self.assertEqual("Anitox", self.parsed["model"])

    def test_the_scheduler_is_read(self) -> None:
        """`Schedule type:` is not in the Extension's pattern table. Studio
        dispatches on a scheduler and now records one -- including Beta 57,
        which arrived through the Tier-2 plugin seam."""

        self.assertEqual("Beta 57", self.parsed["scheduler"])

    def test_settings_sharing_a_line_with_a_prompt_tail_are_found(self) -> None:
        """Some writers put the settings block on the same line as the end of
        the prompt. The tail belongs to the prompt; the settings do not."""

        parsed = parse_generation_parameters(
            "a red apple Steps: 6, Sampler: Euler, Seed: 3"
        )
        self.assertEqual("6", parsed["steps"])
        self.assertEqual("a red apple", parsed["prompt"])


class ContinuationTests(unittest.TestCase):
    def test_an_extension_trailer_never_becomes_prompt(self) -> None:
        """The rule that keeps foreign metadata out of the prompt. Once the
        settings tail opens, nothing after it can fall back into a prompt
        bucket.

        Since the 3.94 merge such a trailer is CAPTURED as metadata rather than
        discarded -- which is the point of reading every `Key: Value` -- but it
        still cannot reach the prompt, which is what mattered.
        """

        parsed = parse_generation_parameters(STUDIO_BLOCK)
        self.assertNotIn("SomeExtension", parsed["prompt"])
        self.assertNotIn("trailing junk", parsed["prompt"])
        self.assertEqual("trailing junk that must not become prompt",
                         parsed["someextension"])

    def test_a_wrapped_prompt_keeps_its_later_lines(self) -> None:
        """The other half. Prompts wrap, so an unrecognised line BEFORE the
        settings block belongs to the section that is open."""

        parsed = parse_generation_parameters(
            "first line of prompt\nsecond line of prompt\nSteps: 6"
        )
        self.assertEqual("first line of prompt\nsecond line of prompt",
                         parsed["prompt"])

    def test_a_wrapped_negative_keeps_its_later_lines(self) -> None:
        parsed = parse_generation_parameters(
            "p\nNegative prompt: bad hands\nextra negative text\nSteps: 6"
        )
        self.assertEqual("bad hands\nextra negative text",
                         parsed["negative_prompt"])

    def test_empty_input_is_an_empty_result(self) -> None:
        self.assertEqual({}, parse_generation_parameters(""))
        self.assertEqual({}, parse_generation_parameters("   \n  "))


class TrackImage394MergeTests(unittest.TestCase):
    """What was taken from TrackImage 3.94, and what was deliberately not.

    Per handoff §19: merge its parsing improvements, do NOT revert
    Studio-specific parser behaviour. Both halves are asserted here, because
    the merge that takes the improvements and loses the Studio fields would
    otherwise look like a success.
    """

    RICH = (
        "a knight on a hill\n"
        "Negative prompt: blurry\n"
        "Template: a __character__\n"
        "Steps: 30, Sampler: Euler, Schedule type: Beta 57, Seed: 7,\n"
        "Size: 1024x1368, Model: Anitox, VAE: sdxl_vae.safetensors,\n"
        "Hires upscale: 1.5, ADetailer model: face_yolov8n.pt,\n"
        'ADetailer prompt: "a face, detailed", Lora hashes: "crisp: a1b2",\n'
        "Version: f2.0.1"
    )

    def setUp(self) -> None:
        self.parsed = parse_generation_parameters(self.RICH)

    def test_unknown_keys_are_kept_rather_than_dropped(self) -> None:
        """The single biggest improvement. The Extension matched thirteen
        patterns and silently discarded everything else, so an owner using
        ADetailer saw none of it and could not tell it was in the file."""

        self.assertEqual("face_yolov8n.pt", self.parsed["adetailer_model"])
        self.assertEqual("f2.0.1", self.parsed["version"])

    def test_a_quoted_value_survives_its_commas(self) -> None:
        """How A1111 writes an ADetailer prompt and a Lora hash list. Splitting
        on every comma cuts them in half."""

        self.assertEqual("a face, detailed", self.parsed["adetailer_prompt"])
        self.assertEqual("crisp: a1b2", self.parsed["lora_hashes"])

    def test_newly_recognised_keys_get_canonical_names(self) -> None:
        self.assertEqual("sdxl_vae.safetensors", self.parsed["vae"])

    def test_the_whole_tail_is_read_not_just_its_first_line(self) -> None:
        """An ADetailer block wraps. The Extension kept only the first line, so
        everything past the first wrap was lost."""

        self.assertEqual("1024x1368", self.parsed["size"])
        self.assertEqual("Anitox", self.parsed["model"])

    def test_null_bytes_do_not_break_the_parse(self) -> None:
        """A UTF-16 field decoded as UTF-8. Without stripping them every later
        match fails on a file that otherwise looks perfectly normal."""

        parsed = parse_generation_parameters("a\x00knight\nSteps: 4")
        self.assertEqual("aknight", parsed["prompt"])
        self.assertEqual("4", parsed["steps"])

    def test_the_hires_size_is_derived(self) -> None:
        """An owner sorting by size means the picture they have, not the base
        pass it started from."""

        self.assertEqual("1536x2052", self.parsed["hires_size"])

    def test_an_explicit_hires_resize_wins_over_the_multiplication(self) -> None:
        parsed = parse_generation_parameters(
            "p\nSteps: 4, Size: 100x100, Hires upscale: 2.0, "
            "Hires resize: 512x768"
        )
        self.assertEqual("512x768", parsed["hires_size"])

    def test_the_studio_fields_survive_the_merge(self) -> None:
        """The half that is NOT taken. TrackImage splits the head at
        `Negative prompt:` and calls everything after it negative -- which puts
        a Studio `Template:` line inside the negative prompt."""

        self.assertEqual("a __character__", self.parsed["template"])
        self.assertEqual("blurry", self.parsed["negative_prompt"])
        self.assertNotIn("__character__", self.parsed["negative_prompt"])
        self.assertNotIn("__character__", self.parsed["prompt"])

    def test_a_prompt_line_beginning_with_a_key_name_is_still_prompt(
        self,
    ) -> None:
        """The tail is anchored on `Steps: <digit>`, not on any of a dozen key
        names -- so a prompt that happens to mention a model does not end the
        prompt."""

        parsed = parse_generation_parameters(
            "a knight\nModel: a toy model of a ship\nSteps: 4"
        )
        self.assertIn("toy model", parsed["prompt"])


class SearchTextTests(unittest.TestCase):
    def test_negatives_are_not_searchable(self) -> None:
        """Indexing negatives makes a search for "blurry" return every image
        that asked NOT to be blurry, which is most of them."""

        text = search_text_for(parse_generation_parameters(STUDIO_BLOCK))
        self.assertIn("knight", text)
        self.assertNotIn("watermark", text)

    def test_prompt_syntax_is_reduced_to_words(self) -> None:
        """Weights and LoRA calls are how a prompt is written, not what it is
        about. An owner searching "castle" must find `(castle:1.4)`."""

        self.assertEqual("castle", clean_for_search("(castle:1.4)"))
        self.assertEqual("", clean_for_search("<lora:crisp:0.8>"))
        self.assertEqual("a b", clean_for_search("a,   b"))

    def test_the_template_is_searchable_but_the_negative_template_is_not(
        self,
    ) -> None:
        """Deliberate asymmetry: an owner may well search for the wildcard
        template they used, but a negative template is still a negative."""

        text = search_text_for(parse_generation_parameters(STUDIO_BLOCK))
        self.assertIn("character", text)
        self.assertNotIn("blurry", text)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_METADATA_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
