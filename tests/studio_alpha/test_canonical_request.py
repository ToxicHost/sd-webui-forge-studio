"""The sampler and scheduler controls, and whether they do anything.

They did not. `index.html` shipped `DPM++ 2M SDE`, `Euler a`, `Karras` and
`Simple` as literal `<option>` text; the Studio request had no sampler field;
`studio_generation` listed sampler and scheduler in `BACKEND_DEFAULTED_FIELDS`
and passed `DEFAULT_SAMPLER` every time. So every image came out of the engine
default while the page showed the owner a menu and the translation report said
the field had been defaulted.

A control that does nothing is worse than a missing one: a missing control is
a gap, and a dead control is a claim.

These tests hold the repair end to end -- the value survives the contract, the
presentation, the translation, and arrives at the processing object -- and
they hold the honesty rule at the other end: when the engine cannot be
reached, the list is EMPTY rather than plausible, because an invented sampler
name is the same lie in a different place.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model is loaded and
no image is generated; the translation is inspected directly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import GenerationRequest  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_request_payload,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_CANONICAL_TESTS = 51

BASE = {
    "model": "",
    "positive_prompt": "a cube",
    "negative_prompt": "",
    "seed": 1,
    "steps": 8,
    "cfg_scale": 4.0,
    "width": 512,
    "height": 512,
    "model_selection": {
        "checkpoint_model_id": "a" * 32,
        "text_encoder_model_id": "b" * 32,
        "vae_model_id": "c" * 32,
    },
}


class PayloadTests(unittest.TestCase):
    def test_a_sampler_is_accepted(self) -> None:
        payload = _validated_request_payload({**BASE, "sampler": "Euler a"})
        self.assertEqual("Euler a", payload["sampler"])

    def test_a_scheduler_is_accepted(self) -> None:
        payload = _validated_request_payload({**BASE, "scheduler": "Karras"})
        self.assertEqual("Karras", payload["scheduler"])

    def test_both_are_optional(self) -> None:
        """A host that does not offer the controls still generates."""

        payload = _validated_request_payload(dict(BASE))
        self.assertEqual("", payload["sampler"])
        self.assertEqual("", payload["scheduler"])

    def test_whitespace_is_trimmed(self) -> None:
        payload = _validated_request_payload({**BASE, "sampler": "  Euler a  "})
        self.assertEqual("Euler a", payload["sampler"])

    def test_a_non_string_sampler_is_refused_by_name(self) -> None:
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload({**BASE, "sampler": 7})
        self.assertIn("sampler", str(caught.exception))

    def test_a_non_string_scheduler_is_refused_by_name(self) -> None:
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload({**BASE, "scheduler": []})
        self.assertIn("scheduler", str(caught.exception))

    def test_an_unknown_field_is_still_refused(self) -> None:
        """The allowlist did not become permissive."""

        with self.assertRaises(PresentationError):
            _validated_request_payload({**BASE, "enable_hr": True})

    def test_the_selection_shape_is_unchanged(self) -> None:
        payload = _validated_request_payload(dict(BASE))
        self.assertEqual(BASE["model_selection"], payload["model_selection"])


class CanonicalBodyTests(unittest.TestCase):
    """One wire shape, and the four ways of getting it wrong.

    The book's submission nests the request under `generation` and keeps the
    three catalogue ids in `model_selection` BESIDE it, not inside it. The
    older flat body is still accepted and rewritten into that shape at one
    point, which is what keeps it an input dialect rather than a second schema.

    The specific thing P0.6 forbids is a second PERMANENT flat-ID schema: the
    three ids acquiring a top-level spelling alongside the one they already
    have. That is refused by name below, and it is the assertion that matters
    most here -- the others protect a half-finished migration from silently
    dropping a parameter.
    """

    GENERATION = {
        "positive_prompt": "a cube",
        "negative_prompt": "",
        "seed": 1,
        "steps": 8,
        "cfg_scale": 4.0,
        "width": 512,
        "height": 512,
    }

    def nested(self, **overrides):
        body = {
            "model": "",
            "model_selection": dict(BASE["model_selection"]),
            "generation": dict(self.GENERATION),
        }
        body.update(overrides)
        return body

    def test_the_nested_body_and_the_flat_body_agree_exactly(self) -> None:
        """Not merely 'both work' -- the same kwargs, field for field.

        If they could differ, the transitional path would BE a second schema.
        """

        self.assertEqual(
            _validated_request_payload(dict(BASE)),
            _validated_request_payload(self.nested()),
        )

    def test_the_flat_body_is_still_accepted(self) -> None:
        payload = _validated_request_payload(dict(BASE))
        self.assertEqual("a cube", payload["positive_prompt"])

    def test_a_catalogue_id_at_the_top_level_is_refused(self) -> None:
        """THE trap. Two spellings of one id is the defect this item names."""

        for name in (
            "checkpoint_model_id",
            "text_encoder_model_id",
            "vae_model_id",
        ):
            with self.subTest(field=name):
                with self.assertRaises(PresentationError) as caught:
                    _validated_request_payload(self.nested(**{name: "x" * 32}))
                self.assertIn("model_selection", str(caught.exception))

    def test_the_selection_may_not_hide_inside_generation(self) -> None:
        """It is a SIBLING of `generation`, per the book's shape."""

        body = self.nested()
        body["generation"]["model_selection"] = body.pop("model_selection")
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload(body)
        self.assertIn("model_selection", str(caught.exception))

    def test_a_mixed_body_is_refused_rather_than_half_read(self) -> None:
        """Nesting some fields and leaving others flat drops the strays.

        Refusing is the whole point: a silently ignored `steps` is a generation
        that quietly used a different number.
        """

        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload(self.nested(steps=99))
        self.assertIn("generation", str(caught.exception))

    def test_an_unknown_field_inside_generation_names_its_position(self) -> None:
        body = self.nested()
        body["generation"]["enable_hr"] = True
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload(body)
        self.assertIn("generation.enable_hr", str(caught.exception))

    def test_generation_must_be_an_object(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_request_payload(self.nested(generation="steps=8"))


class ContractTests(unittest.TestCase):
    def request(self, **overrides) -> GenerationRequest:
        fields = {
            "model_id": "",
            "positive_prompt": "a cube",
            "negative_prompt": "",
            "seed": 1,
            "steps": 8,
            "cfg_scale": 4.0,
            "width": 512,
            "height": 512,
        }
        fields.update(overrides)
        return GenerationRequest(**fields)

    def test_the_contract_carries_a_sampler(self) -> None:
        self.assertEqual("Euler a", self.request(sampler="Euler a").sampler)

    def test_the_contract_defaults_to_empty_not_to_a_name(self) -> None:
        """Empty means 'whatever the engine defaults to'. A default NAME here
        would be Studio inventing a choice and reporting it as the owner's."""

        self.assertEqual("", self.request().sampler)
        self.assertEqual("", self.request().scheduler)

    def test_the_request_is_still_frozen(self) -> None:
        request = self.request(sampler="Euler a")
        with self.assertRaises(Exception):
            request.sampler = "DPM++ 2M"  # type: ignore[misc]


class TranslationTests(unittest.TestCase):
    """The half that was actually broken: does the value reach the engine?"""

    def translate(self, **overrides):
        from forge_headless.studio_generation import translate_request

        fields = {
            "model_id": "m",
            "positive_prompt": "a cube",
            "negative_prompt": "",
            "seed": 1,
            "steps": 8,
            "cfg_scale": 4.0,
            "width": 512,
            "height": 512,
        }
        fields.update(overrides)
        return translate_request(GenerationRequest(**fields), request_id="r1")

    def test_a_chosen_sampler_reaches_the_headless_request(self) -> None:
        translation = self.translate(sampler="Euler a")
        self.assertEqual("Euler a", translation.headless_request.sampler)

    def test_a_chosen_scheduler_reaches_the_headless_request(self) -> None:
        translation = self.translate(scheduler="Karras")
        self.assertEqual("Karras", translation.headless_request.scheduler)

    def test_no_choice_still_uses_the_engine_default(self) -> None:
        from forge_headless.generation_request import (
            DEFAULT_SAMPLER,
            DEFAULT_SCHEDULER,
        )

        translation = self.translate()
        self.assertEqual(DEFAULT_SAMPLER, translation.headless_request.sampler)
        self.assertEqual(DEFAULT_SCHEDULER, translation.headless_request.scheduler)

    def test_sampler_is_no_longer_reported_as_backend_defaulted(self) -> None:
        """The report was TRUE and the product was wrong: the field really was
        being defaulted, because nothing carried the owner's choice. Both had
        to change together."""

        translation = self.translate(sampler="Euler a")
        self.assertNotIn("sampler", translation.backend_defaulted)
        self.assertNotIn("scheduler", translation.backend_defaulted)

    def test_sampler_is_reported_as_carried(self) -> None:
        translation = self.translate(sampler="Euler a")
        self.assertIn("sampler", translation.carried)
        self.assertIn("scheduler", translation.carried)

    def test_hires_is_no_longer_pinned_off(self) -> None:
        """P0.7 gave the owner a field, so the flag follows the REQUEST.

        This asserted the opposite through P0.6, correctly: a flag with no
        owner-facing field is better pinned than left to a default that might
        move. The pin was never the goal, it was the honest answer while the
        field did not exist.
        """

        self.assertNotIn("enable_hr", self.translate().pinned_off)

    def test_the_remaining_flags_are_still_pinned_off(self) -> None:
        """Extensions and reference images have no field, so they keep the pin.

        `enable_adetailer` LEFT this list in P0.8, the same way `enable_hr`
        left it in P0.7 and for the same reason: the owner has a field now, so
        the flag follows `auto_detail.enabled` instead of a constant.

        It is still REFUSED downstream -- the slot settings do not travel yet
        and no detail pass runs -- but refused is not pinned. Reporting it as
        pinned would misdescribe a flag that carries the owner's choice, and
        the whole point of `pinned_off` is to say truthfully which choices the
        request could not express.
        """

        translation = self.translate()
        self.assertNotIn("enable_adetailer", translation.pinned_off)
        for flag in ("enable_extensions", "reference_image_enabled"):
            self.assertIn(flag, translation.pinned_off)

    def test_no_hires_group_leaves_the_base_pass_untouched(self) -> None:
        """The phase's first acceptance criterion, at the translation seam:
        Hires OFF must be indistinguishable from Hires ABSENT."""

        headless = self.translate().headless_request
        self.assertFalse(headless.enable_hr)


class RegistryHonestyTests(unittest.TestCase):
    def test_an_unreachable_engine_yields_an_empty_list(self) -> None:
        """NOT a plausible-looking one. An invented sampler name is the exact
        lie this phase removes; a page that receives nothing can say so.

        The failure is INJECTED rather than provoked by really calling
        through. Calling `read_registries()` for real imports `modules` into
        this process even when it then fails, and `test_import_boundaries`
        later asserts that importing `forge_studio` pulled in no Neo package
        -- so the honest version of this test poisoned an unrelated one two
        suites away. Injecting keeps both true.
        """

        from unittest import mock

        from forge_headless import neo_registries

        with mock.patch.object(
            neo_registries, "_read", side_effect=RuntimeError("no engine")
        ):
            registries = neo_registries.read_registries()
        self.assertFalse(registries.available)
        self.assertEqual((), registries.samplers)
        self.assertEqual((), registries.schedulers)
        self.assertEqual("RuntimeError", registries.reason)

    def test_a_reachable_engine_reports_what_it_found(self) -> None:
        from unittest import mock

        from forge_headless import neo_registries

        with mock.patch.object(
            neo_registries, "_read", return_value=(("Euler",), ("Karras",))
        ):
            registries = neo_registries.read_registries()
        self.assertTrue(registries.available)
        self.assertEqual(("Euler",), registries.samplers)

    def test_membership_is_case_insensitive(self) -> None:
        from forge_headless.neo_registries import Registries, is_known_sampler

        registries = Registries(
            samplers=("Euler a", "DPM++ 2M"), schedulers=(), available=True
        )
        self.assertTrue(is_known_sampler("euler A", registries))
        self.assertFalse(is_known_sampler("Nope", registries))


class EngineChoiceRefusalTests(unittest.TestCase):
    """An unknown sampler or scheduler must be refused, not silently dropped.

    Before this, an unknown SCHEDULER was accepted, reported as a completed
    job, and discarded: Neo's `schedulers_map.get(name)` misses and falls
    through to the model's default sigmas with no exception and no log line.
    Proven live -- `"Karrass"` and `"ZZZNonsense"` produced one byte-identical
    image with `error: null`.
    """

    @staticmethod
    def _application():
        from forge_studio.application import StudioApplication

        return StudioApplication(object())

    @staticmethod
    def _registries(available: bool):
        from forge_headless.neo_registries import Registries

        return Registries(
            samplers=("Euler", "Euler a"),
            schedulers=("Automatic", "Karras"),
            available=available,
        )

    def _check(self, *, sampler: str, scheduler: str, available: bool = True):
        import types
        from unittest import mock

        from forge_headless import neo_registries

        request = types.SimpleNamespace(sampler=sampler, scheduler=scheduler)
        with mock.patch.object(
            neo_registries,
            "read_registries",
            return_value=self._registries(available),
        ):
            self._application()._validate_engine_choices(request)

    @staticmethod
    def _studio_error():
        from forge_studio.contracts import StudioError

        return StudioError

    def test_an_unknown_scheduler_is_refused_by_name(self) -> None:
        with self.assertRaises(self._studio_error()) as caught:
            self._check(sampler="Euler", scheduler="Karrass")
        self.assertEqual("scheduler", caught.exception.error.field)
        self.assertEqual(
            "INVALID_GENERATION_REQUEST", caught.exception.error.code
        )
        self.assertIn("Karrass", caught.exception.error.message)

    def test_an_unknown_sampler_is_refused_by_name(self) -> None:
        with self.assertRaises(self._studio_error()) as caught:
            self._check(sampler="NotASampler", scheduler="")
        self.assertEqual("sampler", caught.exception.error.field)

    def test_a_known_choice_and_an_empty_choice_are_both_accepted(self) -> None:
        """Empty is the owner declining to choose, which is always valid."""

        self._check(sampler="Euler", scheduler="Karras")
        self._check(sampler="", scheduler="")
        # Case is not a mismatch of intent.
        self._check(sampler="euler a", scheduler="karras")

    def test_nothing_is_refused_when_the_engine_is_not_standing(self) -> None:
        """A mock host, or any process where Neo was never stood up.

        The registry is legitimately EMPTY there, and refusing a valid value
        because the list is unavailable would be worse than the gap this closes.
        """

        self._check(sampler="Karrass", scheduler="ZZZNonsense", available=False)

    def test_nothing_is_refused_when_the_registry_read_RAISES(self) -> None:
        """`read_registries` does not report unavailability -- it RE-RAISES.

        `_read` raises REGISTRY_UNAVAILABLE whenever `modules.sd_samplers` is
        absent from `sys.modules`, and `read_registries` lets HeadlessError
        through rather than returning `available=False`. A caller that only
        inspects `.available` therefore refuses EVERY generation on a host with
        no engine. That regression reached the canonical suite once; this test
        is why it will not reach it twice.
        """

        import types
        from unittest import mock

        from forge_headless import neo_registries

        request = types.SimpleNamespace(sampler="Karrass", scheduler="Nonsense")
        with mock.patch.object(
            neo_registries,
            "read_registries",
            side_effect=RuntimeError("the engine is not running yet"),
        ):
            self._application()._validate_engine_choices(request)

    def test_the_registry_module_imports_no_engine_at_module_scope(self) -> None:
        """Naming this module must not drag torch into a Studio process."""

        import ast

        source = (
            APP_ROOT / "forge_headless" / "neo_registries.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"modules", "torch"})


class NoDeadControlsTests(unittest.TestCase):
    HTML = (
        APP_ROOT / "forge_studio" / "frontend" / "index.html"
    ).read_text(encoding="utf-8")
    APP_JS = (
        APP_ROOT / "forge_studio" / "frontend" / "app.js"
    ).read_text(encoding="utf-8")

    def test_the_page_no_longer_ships_hardcoded_sampler_names(self) -> None:
        import re

        for control in ("paramSampler", "paramScheduler"):
            match = re.search(
                rf'<select class="param-select" id="{control}">(.*?)</select>',
                self.HTML,
                re.S,
            )
            self.assertIsNotNone(match, control)
            body = match.group(1)
            for invented in ("DPM++", "Euler", "Karras", "Simple"):
                self.assertNotIn(invented, body, f"{control} still hardcodes {invented}")

    def test_the_page_populates_them_from_the_engine(self) -> None:
        self.assertIn("/api/registries", self.APP_JS)

    def test_the_generation_request_sends_the_choice(self) -> None:
        """The half that makes it not decoration."""

        self.assertIn('sampler: document.getElementById("paramSampler")', self.APP_JS)
        self.assertIn(
            'scheduler: document.getElementById("paramScheduler")', self.APP_JS
        )

    def test_registry_names_render_as_text_not_markup(self) -> None:
        block = self.APP_JS[self.APP_JS.index("async function _loadRegistries"):]
        block = block[: block.index("async function doGenerate")]
        self.assertIn("textContent", block)
        self.assertNotIn("innerHTML", block)


class RegistryRefreshTests(unittest.TestCase):
    """A menu whose source of truth arrives late must read it again.

    `_loadRegistries` ran ONCE, at DOMContentLoaded, against a route that is
    empty on a cold server by design. The first generation stood Neo up and
    made all four lists real; nothing asked a second time, so the page kept
    showing the disabled "Engine default" placeholder until a manual reload.
    The owner selected an upscaler from a menu that had never held one and got
    the latent default while believing otherwise.

    Every assertion here fails against that one-shot implementation.
    """

    APP_JS = (
        APP_ROOT / "forge_studio" / "frontend" / "app.js"
    ).read_text(encoding="utf-8")
    CONTROLS_JS = (
        APP_ROOT / "forge_studio" / "frontend" / "studio-model-controls.js"
    ).read_text(encoding="utf-8")

    EVENT = "studio:model-state-changed"

    def test_the_lifecycle_poll_announces_a_transition(self) -> None:
        """The only thing in Studio that watches the engine arrive."""

        self.assertIn(self.EVENT, self.CONTROLS_JS)
        self.assertIn("function announceLifecycle", self.CONTROLS_JS)
        self.assertIn("announceLifecycle();", self.CONTROLS_JS)

    def test_the_announcement_is_made_once_per_change(self) -> None:
        """A 1500 ms poll must not rebuild every <select> 40 times a minute."""

        block = self.CONTROLS_JS[
            self.CONTROLS_JS.index("function announceLifecycle"):
        ]
        block = block[: block.index("\n  }")]
        self.assertIn("state.announced.state", block)
        self.assertIn("state.announced.loads", block)
        self.assertIn("return;", block)

    def test_the_announcement_watches_the_load_counter_too(self) -> None:
        """A switch can land between two polls without the state string ever
        being observed as anything but `ready`."""

        self.assertIn("counters.loads", self.CONTROLS_JS)

    def test_the_announcement_carries_no_lifecycle_vocabulary(self) -> None:
        """The page is being told the engine moved, not how sessions work."""

        block = self.CONTROLS_JS[
            self.CONTROLS_JS.index("function announceLifecycle"):
        ]
        block = block[: block.index("\n  }")]
        for internal in ("lease", "cache", "profile", "session"):
            self.assertNotIn(internal, block.casefold())

    def test_the_page_listens_and_re_reads(self) -> None:
        self.assertIn(f'addEventListener("{self.EVENT}"', self.APP_JS)
        self.assertIn("_refreshRegistries", self.APP_JS)

    def test_the_refresh_stops_once_the_lists_are_live(self) -> None:
        """Otherwise one busy -> ready transition per job rebuilds the menus
        forever, under an owner who may have one open."""

        block = self.APP_JS[self.APP_JS.index("function _refreshRegistries"):]
        block = block[: block.index("\nwindow.StudioRegistries")]
        self.assertIn("if (_registriesLive) return", block)

    def test_liveness_means_every_registry_scan_finished(self) -> None:
        """Samplers may be readable before image-upscaler discovery succeeds.
        The frontend must keep retrying that partial state rather than latch it
        as live for the rest of the process."""

        block = self.APP_JS[self.APP_JS.index("async function _loadRegistries"):]
        block = block[: block.index("function _refreshRegistries")]
        self.assertIn("_registriesLive = !!(", block)
        self.assertIn("document_.available", block)
        self.assertIn("document_.samplers || []).length", block)
        self.assertIn("document_.upscaler_scan_complete === true", block)

    def test_the_refresh_covers_every_registry_backed_control(self) -> None:
        """Not a Hires-only patch. One read fills all four, so one refresh
        repairs all four -- sampler and scheduler carried the same defect."""

        block = self.APP_JS[self.APP_JS.index("async function _loadRegistries"):]
        block = block[: block.index("function _refreshRegistries")]
        for control in (
            "paramSampler",
            "paramScheduler",
            "paramHrUpscaler",
            "paramUpscaleModel",
        ):
            self.assertIn(control, block)

    def test_the_transport_is_frozen(self) -> None:
        """`StudioModelControls` and `StudioDirPicker` are frozen for the same
        reason: a truthful menu is not something another script may rebind."""

        self.assertIn(
            "window.StudioRegistries = Object.freeze(", self.APP_JS
        )

    def test_a_concurrent_refresh_is_not_started_twice(self) -> None:
        block = self.APP_JS[self.APP_JS.index("function _refreshRegistries"):]
        block = block[: block.index("\nwindow.StudioRegistries")]
        self.assertIn("if (_registriesInFlight) return _registriesInFlight;", block)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_CANONICAL_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
