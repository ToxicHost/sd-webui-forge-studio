"""Wildcards on the path the Generate button actually uses.

`test_wildcards.py` proves the ENGINE: tokens, nesting, determinism,
containment, 45 tests of it, all passing. None of that reached an image.

Expansion was invoked in exactly one place -- `SourceFrontendAdapter.
_translate_generation`, reachable only from the legacy `POST /studio/generate`.
`app.js doGenerate` returns before it ever builds that payload, so the live
route carried the prompt verbatim and the sampler received the literal text
`__colour__`. The preview panel resolved correctly the whole time, which is
exactly why it went unnoticed: the owner saw the right answer on screen and
generated the wrong one.

This is the sixth instance of that defect class in this project, and the worst:
the previous five reached Studio's own request object and stopped at the
translation into the headless one. This one never reached Studio's request
either.

SCOPE: that the resolution happens, on the live path, server-side, and that
both halves survive. Not the engine, which is tested next door.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import GenerationRequest, PromptExpansion  # noqa: E402
from forge_studio.preferences import WildcardSettings  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    StudioPresentation,
    _validated_request_payload,
)
from forge_studio.wildcard_service import WildcardService  # noqa: E402


EXPECTED_WILDCARD_GENERATION_TESTS = 37

#: Distinguishes "not supplied" from the meaningful value `None`.
_DEFAULT = object()


class _Wired(unittest.TestCase):
    """A real service over a real folder. No doubles for the thing under test."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        folder = self.root / "wildcards"
        folder.mkdir()
        (folder / "colour.txt").write_text("red\nblue\ngreen\n", encoding="utf-8")
        (folder / "flaw.txt").write_text("lowres\nwatermark\n", encoding="utf-8")
        self.service = WildcardService(
            WildcardSettings(self.root), checkout=self.root)
        self.service.select_folder({"path": str(folder)})
        self.enable(True)

    def enable(self, value: bool) -> None:
        self.service.set_enabled({"studio_dynamic_prompts_enabled": value})

    def build(self, service: object = _DEFAULT, **overrides):
        """The live submission path, minus the queue it hands off to.

        `service` needs three states, not two: this suite's real service, some
        other service, and NO service at all -- and `None` is the third of
        those, so it cannot also mean "the default".
        """

        presentation = StudioPresentation(
            object(), GenerationRequest,
            wildcards=self.service if service is _DEFAULT else service)
        body = {
            "model": "m", "positive_prompt": "a __colour__ coat",
            "negative_prompt": "", "seed": 7, "steps": 20,
            "cfg_scale": 4.0, "width": 64, "height": 64,
        }
        body.update(overrides)
        payload = _validated_request_payload(body)
        payload.update(presentation._server_execution_policy())
        payload.update(presentation._resolve_prompts(payload))
        return GenerationRequest(**payload)


class ReachesInferenceTests(_Wired):
    """The defect, stated as a test."""

    def _sampler_sees(self, request: GenerationRequest) -> tuple[str, str]:
        from forge_headless.studio_generation import translate_request

        headless = translate_request(request, request_id="t").headless_request
        return headless.positive_prompt, headless.negative_prompt

    def test_a_token_is_resolved_before_the_request_is_built(self) -> None:
        self.assertNotIn("__colour__", self.build().positive_prompt)

    def test_the_resolved_prompt_survives_translation(self) -> None:
        """The step the previous five defects died at."""
        positive, _ = self._sampler_sees(self.build())
        self.assertNotIn("__", positive)
        self.assertIn(positive.split()[1], {"red", "blue", "green"})

    def test_the_negative_prompt_is_expanded_too(self) -> None:
        """It was never expanded on either route, legacy included."""
        request = self.build(negative_prompt="__flaw__, blurry")
        _, negative = self._sampler_sees(request)
        self.assertNotIn("__flaw__", negative)
        self.assertTrue(negative.startswith(("lowres", "watermark")), negative)

    def test_a_prompt_with_no_tokens_is_untouched(self) -> None:
        request = self.build(positive_prompt="a plain coat")
        self.assertEqual("a plain coat", request.positive_prompt)


class SeedTests(_Wired):
    """A random seed must still expand reproducibly."""

    def test_a_random_seed_is_resolved_before_expanding(self) -> None:
        """Expanding against -1 draws from an unseeded RNG.

        The recorded seed would then reproduce the sampling and not the
        prompt -- the one combination that looks reproducible and is not.
        """
        request = self.build(seed=-1)
        self.assertGreaterEqual(request.seed, 0)
        self.assertEqual(request.seed, request.prompt_expansion.seed)

    def test_the_same_seed_expands_the_same_way(self) -> None:
        self.assertEqual(self.build(seed=99).positive_prompt,
                         self.build(seed=99).positive_prompt)

    def test_the_seed_that_expanded_is_the_seed_that_generates(self) -> None:
        """Recording a different seed than the job runs makes it unusable."""
        request = self.build(seed=-1)
        self.assertEqual(request.seed, request.prompt_expansion.seed)

    def test_prompt_and_negative_do_not_always_draw_alike(self) -> None:
        """The same token in both boxes must not lock together.

        Without the decorrelation offset, "__colour__ coat" against
        "__colour__ hat" agrees at every seed, which reads as a bug.
        """
        agree = {
            self.build(seed=s, positive_prompt="__colour__",
                       negative_prompt="__colour__").positive_prompt
            == self.build(seed=s, positive_prompt="__colour__",
                          negative_prompt="__colour__").negative_prompt
            for s in range(1, 15)
        }
        self.assertEqual({True, False}, agree)


class RetentionTests(_Wired):
    """Both halves, or the image is not reproducible."""

    def test_the_original_prompt_is_kept(self) -> None:
        record = self.build().prompt_expansion
        self.assertEqual("a __colour__ coat", record.original_prompt)

    def test_the_resolved_prompt_is_kept(self) -> None:
        request = self.build()
        self.assertEqual(request.positive_prompt,
                         request.prompt_expansion.resolved_prompt)

    def test_both_negative_halves_are_kept(self) -> None:
        record = self.build(negative_prompt="__flaw__").prompt_expansion
        self.assertEqual("__flaw__", record.original_negative_prompt)
        self.assertNotIn("__", record.resolved_negative_prompt)

    def test_the_choices_are_recorded_by_name_not_path(self) -> None:
        record = self.build().prompt_expansion
        self.assertEqual(1, len(record.choices))
        self.assertTrue(record.choices[0].startswith("colour="))
        self.assertNotIn(str(self.root), " ".join(record.choices))

    def test_a_missing_token_stays_legible_and_is_recorded(self) -> None:
        request = self.build(positive_prompt="a __nosuchfile__ coat")
        self.assertIn("__nosuchfile__", request.positive_prompt)
        self.assertIn("nosuchfile", request.prompt_expansion.missing)

    def test_the_record_does_not_leak_into_the_headless_request(self) -> None:
        """Studio-side only. The engine has no use for it."""
        from forge_headless.studio_generation import translate_request

        headless = translate_request(self.build(), request_id="t").headless_request
        self.assertFalse(hasattr(headless, "prompt_expansion"))


class RefusalTests(_Wired):
    """A wildcard is not worth the owner's image."""

    def test_disabled_means_the_prompt_passes_through(self) -> None:
        self.enable(False)
        request = self.build()
        self.assertEqual("a __colour__ coat", request.positive_prompt)
        self.assertFalse(request.prompt_expansion.expanded)

    def test_a_host_with_no_wildcard_service_still_generates(self) -> None:
        """Every mock and most tests. Must behave exactly as before."""
        request = self.build(service=None)
        self.assertEqual("a __colour__ coat", request.positive_prompt)

    def test_a_service_that_raises_does_not_cost_the_image(self) -> None:
        request = self.build(service=_Exploding())
        self.assertEqual("a __colour__ coat", request.positive_prompt)
        self.assertEqual("a __colour__ coat",
                         request.prompt_expansion.original_prompt)


class _Exploding:
    def enabled(self) -> bool:
        return True

    def expand(self, prompt, seed=None):
        raise RuntimeError("the folder went away mid-generation")


class InlineChoiceTests(_Wired):
    """`{a|b|c}` and `{N$$a|b|c}`, to the Extension's rules.

    Semantics read from `scripts/studio_dynamic_prompts.py`, not recalled:
    section 20 of the handoff is explicit that inventing this from memory is
    not allowed, and the multi-select ordering and separator are exactly the
    kind of detail memory gets wrong.
    """

    def choose(self, prompt: str, seed: int = 3) -> str:
        return self.build(seed=seed, positive_prompt=prompt).positive_prompt

    def test_a_plain_choice_picks_one_option(self) -> None:
        self.assertIn(self.choose("{red|blue|green}"), {"red", "blue", "green"})

    def test_a_multi_select_picks_that_many(self) -> None:
        chosen = self.choose("{2$$a|b|c|d}").split(", ")
        self.assertEqual(2, len(chosen))
        self.assertEqual(len(set(chosen)), len(chosen), "options repeated")

    def test_a_multi_select_keeps_the_owners_order(self) -> None:
        """Indices are sorted after drawing, so `{2$$a|b|c}` never reads 'c, a'."""
        for seed in range(1, 30):
            with self.subTest(seed=seed):
                chosen = self.choose("{3$$a|b|c|d|e}", seed).split(", ")
                self.assertEqual(sorted(chosen), chosen)

    def test_asking_for_more_than_exist_gives_all_of_them(self) -> None:
        self.assertEqual("a, b", self.choose("{9$$a|b}"))

    def test_a_group_without_a_pipe_is_left_alone(self) -> None:
        """`{...}` also carries attention and template syntax."""
        self.assertEqual("a {plain} group", self.choose("a {plain} group"))

    def test_a_range_or_separator_body_is_declined_untouched(self) -> None:
        """Full Dynamic Prompts syntax this does not implement.

        Mangling it would be worse than declining it.
        """
        self.assertEqual("{2$$ and $$a|b}", self.choose("{2$$ and $$a|b}"))

    def test_a_bad_count_warns_once_and_changes_nothing(self) -> None:
        request = self.build(positive_prompt="{0$$a|b}")
        self.assertEqual("{0$$a|b}", request.positive_prompt)
        self.assertEqual(("Invalid multi-select count",),
                         request.prompt_expansion.warnings)

    def test_an_empty_choice_warns_once(self) -> None:
        """Once. It survives several passes and saying it four times helps nobody."""
        record = self.build(positive_prompt="{|}").prompt_expansion
        self.assertEqual(("Empty inline choice",), record.warnings)

    def test_choices_inside_a_wildcard_file_are_resolved(self) -> None:
        """A line pulled from a .txt may itself contain a group."""
        (self.root / "wildcards" / "styled.txt").write_text(
            "{crimson|scarlet} silk" + chr(10), encoding="utf-8")
        self.service.library(reload=True)
        text = self.build(positive_prompt="__styled__").positive_prompt
        self.assertIn(text, {"crimson silk", "scarlet silk"})

    def test_a_token_with_a_space_resolves(self) -> None:
        """The Extension's regex admits a space; this one did not."""
        (self.root / "wildcards" / "hair colour.txt").write_text(
            "auburn" + chr(10), encoding="utf-8")
        self.service.library(reload=True)
        self.assertEqual(
            "auburn hair",
            self.build(positive_prompt="__hair colour__ hair").positive_prompt)


class ContainmentTests(_Wired):
    """The wildcard root is a boundary, not a suggestion."""

    def test_a_link_out_of_the_root_is_not_followed(self) -> None:
        """Name-based containment was not enough, and this was reproduced.

        `_name_of` uses `relative_to`, which is LEXICAL: a junction inside the
        root is relative to it by spelling while pointing anywhere on disk. A
        junction was created in a real wildcard folder and read a file outside
        it into a prompt.
        """
        import subprocess

        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "secret.txt").write_text("LEAKED" + chr(10), encoding="utf-8")
        link = self.root / "wildcards" / "sub"
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            self.skipTest("this host cannot create a directory junction")
        self.service.library(reload=True)
        request = self.build(positive_prompt="__sub/secret__")
        self.assertNotIn("LEAKED", request.positive_prompt)
        self.assertIn("__sub/secret__", request.positive_prompt)

    def test_a_traversal_token_still_resolves_to_nothing(self) -> None:
        for token in ("__../secret__", "__../../secret__"):
            with self.subTest(token=token):
                self.assertIn(
                    token, self.build(positive_prompt=token).positive_prompt)


class TruncationTests(_Wired):
    """`truncated` means a cycle, not a typo."""

    def test_a_missing_token_is_not_truncation(self) -> None:
        """It used to be. A single typo forced all twelve passes and reported
        truncation for a prompt that never recursed."""
        record = self.build(positive_prompt="__nosuchthing__").prompt_expansion
        self.assertFalse(record.truncated)
        self.assertIn("nosuchthing", record.missing)

    def test_a_cycle_is_truncation(self) -> None:
        (self.root / "wildcards" / "loop.txt").write_text(
            "__loop__" + chr(10), encoding="utf-8")
        self.service.library(reload=True)
        self.assertTrue(
            self.build(positive_prompt="__loop__").prompt_expansion.truncated)

    def test_a_declined_group_is_not_truncation(self) -> None:
        self.assertFalse(
            self.build(positive_prompt="a {plain} group").prompt_expansion.truncated)


class DefaultStateTests(unittest.TestCase):
    """What an owner gets before they touch anything.

    This shipped as a flat OFF while the settings panel drew the toggle ON, so
    the page said the feature was running and the server skipped expansion.
    Every control looked correct and a prompt full of tokens went to the
    sampler verbatim -- found by the owner testing it, not by any test here.

    A flat ON is the same lie reversed, and `test_r1_refusal_seam` guards it:
    with no folder resolved there is nothing to expand against, so reporting
    "on" describes a service that cannot run.

    The default follows CAPABILITY, and stays a default rather than becoming an
    AND -- `WildcardService.enabled` records why. Note what WP0.4 changed about
    these cases: nothing. Until the duplicate POST handler was removed nothing
    could be stored, so this default was the whole setting; the cases below
    were the only ones that existed. An owner's explicit choice now reaches
    them at all.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def service(self, *, with_folder: bool = False) -> WildcardService:
        service = WildcardService(
            WildcardSettings(self.root), checkout=self.root)
        if with_folder:
            folder = self.root / "wildcards"
            folder.mkdir(exist_ok=True)
            (folder / "colour.txt").write_text("red" + chr(10), encoding="utf-8")
            service.select_folder({"path": str(folder)})
        return service

    def test_it_is_on_when_there_is_a_folder_to_expand_against(self) -> None:
        """The Extension's behaviour, for an owner who has a library."""
        self.assertTrue(self.service(with_folder=True).enabled())

    def test_it_is_off_when_there_is_nowhere_to_look(self) -> None:
        """Honest rather than eager. Nothing can expand, so nothing claims to."""
        self.assertFalse(self.service().enabled())

    def test_the_panel_is_told_the_same_thing(self) -> None:
        """`config()` feeds the toggle. It disagreeing with `enabled()` IS the
        defect -- the page drew on while the server read off."""
        for with_folder in (False, True):
            with self.subTest(folder=with_folder):
                service = self.service(with_folder=with_folder)
                self.assertEqual(
                    service.enabled(),
                    service.config()["studio_dynamic_prompts_enabled"])

    def test_an_explicit_off_beats_the_default(self) -> None:
        """A folder makes the default ON, so this proves the stored value wins."""
        service = self.service(with_folder=True)
        service.set_enabled({"studio_dynamic_prompts_enabled": False})
        self.assertFalse(
            WildcardService(WildcardSettings(self.root),
                            checkout=self.root).enabled(),
            "a fresh service forgot the owner's choice")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            EXPECTED_WILDCARD_GENERATION_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
