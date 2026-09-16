"""Per-job option overrides that do not become global state.

An owner preference has to reach `modules/esrgan_model.py`, which sits three
Neo-owned frames below anything Studio owns. The two obvious routes are both
closed:

    write it onto `shared.opts`     about forty modules bind `opts` once at
                                    import, so the write is visible to every
                                    job in the process. A queued job would see
                                    the preference the NEXT job was enqueued
                                    with.

    thread an argument down         would mean editing `resize_image`,
                                    `Upscaler.upscale` and four subclasses to
                                    carry a Studio preference through Neo.

So it travels in a `ContextVar` and is read where Studio already owns the
lookup -- `HeadlessOptions.__getattr__`, which every `opts.X` read routes
through.

This suite is the isolation proof that makes that defensible. The mechanism is
only correct while a scope cannot outlive its job or be seen by another one,
and "admission is serial today" is not a proof of that -- it is the exact
assumption `_in_flight_job_id` made before it started reporting the previous
job's work.

SCOPE: the scope mechanism and the options seam. Not the preference's journey
from the Settings panel, which is not built.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

from forge_studio.presentation import (
    PresentationError,
    StudioPresentation,
    _validated_request_payload,
)

from forge_headless.headless_options import HeadlessOptions
from forge_headless.job_options import (
    has_option,
    job_scope,
    option,
    record,
    recorded,
)


EXPECTED_JOB_OPTIONS_TESTS = 40

APP_ROOT = Path(__file__).resolve().parents[2]


class OutsideAScopeTests(unittest.TestCase):
    """A process that never started a job has no job state."""

    def test_no_option_is_set(self) -> None:
        self.assertFalse(has_option("composite_tiles_on_gpu"))
        self.assertIsNone(option("composite_tiles_on_gpu"))

    def test_the_default_is_returned(self) -> None:
        self.assertEqual("fallback", option("anything", "fallback"))

    def test_recording_is_a_quiet_no_op(self) -> None:
        """The upscaler is reachable from paths that opened no scope.

        A preflight that raised because nobody was listening would turn a
        diagnostic into an outage.
        """
        record(upscale_composite_effective="gpu")
        self.assertEqual({}, recorded())


class InsideAScopeTests(unittest.TestCase):
    def test_an_option_the_job_named_is_visible(self) -> None:
        with job_scope(composite_tiles_on_gpu=True):
            self.assertTrue(option("composite_tiles_on_gpu"))

    def test_false_is_a_real_override_and_not_an_absence(self) -> None:
        """The case the whole thing exists for.

        With the default ON, refusing is what has to travel. If False read as
        "unset" the owner's OFF would fall through to the default and turn
        itself back on.
        """
        with job_scope(composite_tiles_on_gpu=False):
            self.assertTrue(has_option("composite_tiles_on_gpu"))
            self.assertFalse(option("composite_tiles_on_gpu"))

    def test_an_option_the_job_did_not_name_still_falls_through(self) -> None:
        with job_scope(composite_tiles_on_gpu=False):
            self.assertFalse(has_option("ESRGAN_tile"))

    def test_the_decision_record_is_visible_inside(self) -> None:
        with job_scope():
            record(upscale_composite_effective="cpu", upscale_composite_reason="fit")
            self.assertEqual(
                {"upscale_composite_effective": "cpu",
                 "upscale_composite_reason": "fit"},
                recorded())

    def test_the_record_is_yielded_so_it_can_be_read_at_the_boundary(self) -> None:
        with job_scope() as decisions:
            record(upscale_composite_effective="gpu")
        self.assertEqual({"upscale_composite_effective": "gpu"}, decisions)


class RestorationTests(unittest.TestCase):
    """Nothing survives the job that set it."""

    def test_the_option_is_gone_afterwards(self) -> None:
        with job_scope(composite_tiles_on_gpu=True):
            pass
        self.assertFalse(has_option("composite_tiles_on_gpu"))

    def test_the_record_is_gone_afterwards(self) -> None:
        with job_scope():
            record(upscale_composite_effective="gpu")
        self.assertEqual({}, recorded())

    def test_a_failure_inside_still_restores(self) -> None:
        with self.assertRaises(RuntimeError):
            with job_scope(composite_tiles_on_gpu=True):
                raise RuntimeError("the job failed")
        self.assertFalse(has_option("composite_tiles_on_gpu"))

    def test_a_nested_scope_puts_back_what_it_found(self) -> None:
        # Restored by token rather than by deletion, which is the difference
        # between "put back the outer value" and "clear everything".
        with job_scope(composite_tiles_on_gpu=True):
            with job_scope(composite_tiles_on_gpu=False):
                self.assertFalse(option("composite_tiles_on_gpu"))
            self.assertTrue(option("composite_tiles_on_gpu"))

    def test_three_jobs_in_a_row_do_not_leak(self) -> None:
        """Job A ON, Job B OFF, Job C ON, on one thread. Section 54."""
        seen = []
        for requested in (True, False, True):
            with job_scope(composite_tiles_on_gpu=requested):
                seen.append(option("composite_tiles_on_gpu"))
        self.assertEqual([True, False, True], seen)
        self.assertFalse(has_option("composite_tiles_on_gpu"))


class ThreadIsolationTests(unittest.TestCase):
    """The property that makes serial admission an implementation detail."""

    def test_two_threads_do_not_see_each_other(self) -> None:
        seen: dict[str, object] = {}
        started = threading.Barrier(2)

        def run(label: str, requested: bool) -> None:
            with job_scope(composite_tiles_on_gpu=requested):
                started.wait(timeout=5)
                # Both scopes are open at once here. If the value were global
                # rather than per-thread, one would be reading the other's.
                seen[label] = option("composite_tiles_on_gpu")

        threads = [
            threading.Thread(target=run, args=("a", True)),
            threading.Thread(target=run, args=("b", False)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual({"a": True, "b": False}, seen)

    def test_a_thread_that_opened_no_scope_sees_nothing(self) -> None:
        seen: dict[str, bool] = {}

        def run() -> None:
            seen["has"] = has_option("composite_tiles_on_gpu")

        with job_scope(composite_tiles_on_gpu=True):
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=5)

        self.assertFalse(seen["has"])


class OptionsSeamTests(unittest.TestCase):
    """`opts.X` honours the running job without the object being mutated."""

    def test_the_job_wins_over_the_projected_default(self) -> None:
        opts = HeadlessOptions({"composite_tiles_on_gpu": True})
        with job_scope(composite_tiles_on_gpu=False):
            self.assertFalse(opts.composite_tiles_on_gpu)

    def test_the_projected_default_applies_outside_a_job(self) -> None:
        opts = HeadlessOptions({"composite_tiles_on_gpu": True})
        self.assertTrue(opts.composite_tiles_on_gpu)

    def test_the_object_itself_is_not_mutated(self) -> None:
        """The whole reason for the ContextVar.

        `HeadlessOptions` defines no `__setattr__`, so a write would create a
        real instance attribute that permanently shadows `__getattr__` -- a
        per-job preference silently converted into a process-wide one.
        """
        opts = HeadlessOptions({"composite_tiles_on_gpu": True})
        with job_scope(composite_tiles_on_gpu=False):
            self.assertFalse(opts.composite_tiles_on_gpu)
        self.assertNotIn("composite_tiles_on_gpu", vars(opts))
        self.assertTrue(opts.composite_tiles_on_gpu)

    def test_options_the_job_did_not_name_are_untouched(self) -> None:
        opts = HeadlessOptions({"ESRGAN_tile": 256})
        with job_scope(composite_tiles_on_gpu=False):
            self.assertEqual(256, opts.ESRGAN_tile)


class PortContractTests(unittest.TestCase):
    """Where the scope is opened, asserted at the source."""

    def test_the_port_opens_and_closes_the_scope_around_the_generation(self) -> None:
        source = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
            encoding="utf-8")
        self.assertIn("with job_scope(**overrides):", source)
        # Read INSIDE the scope. Outside it the record is gone, and reading it
        # off the port instead would be the `_in_flight_job_id` mistake again:
        # correct while admission is serial, wrong the day it is not.
        body = source.split("with job_scope(**overrides):")[1]
        self.assertIn("decision = recorded()", body.split(chr(10) + chr(10))[0])

    def test_the_outcome_carries_requested_and_effective_separately(self) -> None:
        source = (APP_ROOT / "forge_headless" / "generation_port.py").read_text(
            encoding="utf-8")
        for field in ("gpu_tile_composite_requested",
                      "upscale_composite_effective",
                      "upscale_composite_reason"):
            self.assertIn(field, source)

    def test_the_upscaler_records_its_decision(self) -> None:
        source = (APP_ROOT / "modules" / "esrgan_model.py").read_text(
            encoding="utf-8")
        self.assertIn("_record_decision(", source)
        self.assertIn("REASON_ALLOCATION_FALLBACK", source)


class _Store:
    """A PreferenceStore stand-in. `read` may also refuse, like the real one."""

    def __init__(self, document, *, broken=False):
        self.document = dict(document)
        self.broken = broken

    def read(self):
        if self.broken:
            raise OSError("preferences.json is unreadable")
        return dict(self.document)


def _presentation(store):
    return StudioPresentation(object(), dict, preferences=store)


class ServerSnapshotTests(unittest.TestCase):
    """The server owns the preference. The browser does not.

    A page loaded an hour ago holds an hour-old preference, and several pages
    can be open at once, so a browser-submitted value can diverge from the
    durable state that Settings actually writes.
    """

    def test_the_store_saying_yes_admits_yes(self) -> None:
        policy = _presentation(_Store({"gpu_tile_compositing": True}))._server_execution_policy()
        self.assertTrue(policy["gpu_tile_compositing_requested"])

    def test_the_store_saying_no_admits_no(self) -> None:
        policy = _presentation(_Store({"gpu_tile_compositing": False}))._server_execution_policy()
        self.assertFalse(policy["gpu_tile_compositing_requested"])

    def test_an_absent_key_takes_the_default(self) -> None:
        policy = _presentation(_Store({}))._server_execution_policy()
        self.assertTrue(policy["gpu_tile_compositing_requested"])

    def test_an_unreadable_store_takes_the_default(self) -> None:
        """A setting is not worth an owner's image.

        Failing the generation because a preference file could not be read
        would trade the thing they asked for against the thing they configured.
        """
        policy = _presentation(_Store({}, broken=True))._server_execution_policy()
        self.assertTrue(policy["gpu_tile_compositing_requested"])

    def test_no_store_at_all_takes_the_default(self) -> None:
        policy = _presentation(None)._server_execution_policy()
        self.assertTrue(policy["gpu_tile_compositing_requested"])

    def test_the_browser_cannot_supply_the_preference(self) -> None:
        """Section 25/27: the stale-client contract.

        The validated payload is what the browser is allowed to say. The
        preference is not in it, so a page cannot assert one -- which is why
        a stale page needs no special handling rather than careful handling.
        """
        # Stronger than being ignored: the validator refuses fields it does
        # not know, so a page cannot even transport a preference.
        for field in ("gpu_tile_compositing", "gpu_tile_compositing_requested"):
            with self.subTest(field=field):
                with self.assertRaises(PresentationError):
                    _validated_request_payload({field: True})


    def test_server_truth_beats_a_stale_page(self) -> None:
        # The page was loaded while the preference was on; the store now says
        # off. The admitted job must be off.
        presentation = _presentation(_Store({"gpu_tile_compositing": False}))
        # Whatever the page sent, the server composes its own policy over it.
        payload = {"gpu_tile_compositing_requested": True}
        payload.update(presentation._server_execution_policy())
        self.assertFalse(payload["gpu_tile_compositing_requested"])

    def test_a_later_settings_change_does_not_reach_an_admitted_job(self) -> None:
        """Section 24. Admission freezes the preference."""
        store = _Store({"gpu_tile_compositing": True})
        presentation = _presentation(store)

        job_a = presentation._server_execution_policy()
        store.document["gpu_tile_compositing"] = False
        job_b = presentation._server_execution_policy()

        self.assertTrue(job_a["gpu_tile_compositing_requested"])
        self.assertFalse(job_b["gpu_tile_compositing_requested"])

    def test_the_port_seeds_the_scope_from_the_admitted_job(self) -> None:
        source = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
            encoding="utf-8")
        self.assertIn('getattr(request, "gpu_tile_compositing_requested", None)',
                      source)
        # Not from the store, during execution. A preference changed mid-job
        # belongs to the next job.
        self.assertNotIn("PreferenceStore", source)

    def test_the_request_carries_it_as_an_immutable_field(self) -> None:
        from forge_studio.contracts import GenerationRequest

        field = GenerationRequest.__dataclass_fields__["gpu_tile_compositing_requested"]
        self.assertTrue(field.default)


class TranslationBoundaryTests(unittest.TestCase):
    """The preference has to CROSS the boundary, not just reach it.

    The port reads the TRANSLATED request, not Studio's own. A field that
    stops at `forge_studio.contracts.GenerationRequest` is a field the
    upscaler cannot see, and every job silently takes the default -- which is
    exactly what happened: the Settings toggle wrote to the store, the store
    was read at assembly, and the job still composited on the device.

    `studio_generation.py` documents four earlier instances of this same
    defect -- sampler, hires, preview_enabled, auto_detail -- and warns that
    asserting on the carried-fields tuple pins nothing, because
    `translation.carried` is assigned FROM that tuple. So these assert the
    VALUE survives, which is the thing that was broken.
    """

    @staticmethod
    def _request(flag):
        import types

        return types.SimpleNamespace(
            gpu_tile_compositing_requested=flag, preview_enabled=False,
            positive_prompt="x", negative_prompt="", seed=1, steps=4,
            cfg_scale=4.0, width=512, height=512, sampler="Euler",
            scheduler="", hires=None, auto_detail=None, variation=None,
            model_id="m")

    def test_an_owner_who_asked_for_it_is_carried_across(self) -> None:
        from forge_headless.studio_generation import translate_request

        translated = translate_request(self._request(True), request_id="r")
        self.assertTrue(
            translated.headless_request.gpu_tile_compositing_requested)

    def test_an_owner_who_refused_is_carried_across(self) -> None:
        """The case that was broken, and the one that matters.

        With the compositor on by default, refusing is the only instruction
        that has to travel -- a dropped True is invisible.
        """
        from forge_headless.studio_generation import translate_request

        translated = translate_request(self._request(False), request_id="r")
        self.assertFalse(
            translated.headless_request.gpu_tile_compositing_requested)

    #: Distinctive, non-default values. A default that survives by accident
    #: proves nothing -- the field this suite exists for defaulted to True and
    #: looked correct in every arm except the one that asked for False.
    HIGH_VALUE_CONTROLS = (
        ("seed", "seed", 987654321),
        ("steps", "steps", 23),
        ("cfg_scale", "cfg_scale", 6.5),
        ("width", "width", 768),
        ("height", "height", 1152),
        ("sampler", "sampler", "DPM++ 2M"),
        ("scheduler", "scheduler", "Exponential"),
        ("positive_prompt", "positive_prompt", "a distinctive prompt"),
        ("negative_prompt", "negative_prompt", "a distinctive negative"),
        ("preview_enabled", "preview_enabled", True),
        ("gpu_tile_compositing_requested",
         "gpu_tile_compositing_requested", False),
    )

    def test_every_high_value_control_survives_translation(self) -> None:
        """Section 25's audit, as a standing check.

        One field silently stopped at this boundary and made an owner-facing
        control inert. These assert the TRANSLATED value for each control that
        would do the same damage.
        """
        from forge_headless.studio_generation import translate_request

        for source, target, value in self.HIGH_VALUE_CONTROLS:
            with self.subTest(control=source):
                request = self._request(True)
                setattr(request, source, value)
                translated = translate_request(
                    request, request_id="r").headless_request
                self.assertEqual(value, getattr(translated, target, None))

    def test_the_hires_group_survives_translation(self) -> None:
        import types

        from forge_headless.studio_generation import translate_request

        request = self._request(True)
        request.hires = types.SimpleNamespace(
            enabled=True, scale=1.75, upscaler="distinctive_upscaler",
            second_pass_steps=13, denoising_strength=0.42,
            sampler="", scheduler="", prompt="", negative_prompt="", cfg=0.0)
        translated = translate_request(request, request_id="r").headless_request

        for attribute, value in (
            ("enable_hr", True),
            ("hr_scale", 1.75),
            ("hr_upscaler", "distinctive_upscaler"),
            ("hr_second_pass_steps", 13),
            ("hr_denoising_strength", 0.42),
        ):
            with self.subTest(field=attribute):
                self.assertEqual(value, getattr(translated, attribute, None))

    def test_the_auto_detail_slot_survives_translation(self) -> None:
        """Read the slot back out of `ad_slots`, not out of guessed flat names.

        The section 25 audit first reported five gaps that were all wrong
        attribute names on the reader's side. An absent attribute means the
        name was wrong; it does not mean the value was dropped, and reporting
        one as the other would have invented five defects in working code.
        """
        import types

        from forge_headless.studio_generation import translate_request

        request = self._request(True)
        request.auto_detail = types.SimpleNamespace(
            enabled=True,
            slots=(types.SimpleNamespace(
                enabled=True, detector="distinctive_detector.pt",
                confidence=0.55, top_k=2, min_ratio=0.05, max_ratio=0.85,
                dilate_erode=7, mask_blur=9, denoising_strength=0.37,
                prompt="", negative_prompt="", inpaint_padding=41,
                steps=0, cfg=0.0),))
        translated = translate_request(
            request, request_id="r",
            resolve_detector=lambda name: "/fake/" + name).headless_request

        self.assertTrue(getattr(translated, "enable_adetailer", False))
        slots = getattr(translated, "ad_slots", None)
        self.assertTrue(slots, "the slot did not cross the boundary at all")

        carried = slots[0]
        for key, value in (
            ("confidence", 0.55),
            ("denoising_strength", 0.37),
            ("mask_blur", 9),
            ("inpaint_padding", 41),
            ("top_k", 2),
            ("dilate_erode", 7),
        ):
            with self.subTest(field=key):
                got = (carried.get(key) if isinstance(carried, dict)
                       else getattr(carried, key, None))
                self.assertEqual(value, got)

    def test_the_headless_request_defaults_it_on(self) -> None:
        from forge_headless.generation_request import FirstImageRequest

        field = FirstImageRequest.__dataclass_fields__[
            "gpu_tile_compositing_requested"]
        self.assertTrue(field.default)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_JOB_OPTIONS_TESTS, suite.countTestCases())

    def test_the_mechanism_imports_no_accelerator_library(self) -> None:
        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_headless" / "job_options.py").read_text(
                encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots.add((node.module or "").split(".")[0])
        self.assertEqual(set(), roots & {"torch", "numpy", "PIL"})


if __name__ == "__main__":
    unittest.main()
