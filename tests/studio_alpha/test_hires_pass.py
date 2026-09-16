"""The Hires second pass: carried, bounded, and honest about the machine.

`enable_hr` was hardcoded False in the live port AND pinned off in translation
AND refused in validation AND used as the example "unknown field" in another
suite. Four independent statements that Hires did not exist. P0.7 replaces all
four with one request field and the checks that make it dispatchable.

Two design decisions are asserted here because they are the ones most likely to
be quietly undone later:

* OFF is byte-identical to ABSENT. With no `hires` group the port passes no
  `hr_*` argument at all -- not inert values, nothing -- so the base pass is
  the same object it was before this phase. That is the book's first
  acceptance criterion and it is cheaper to guarantee by omission than to
  prove downstream.
* The product range is FORGE'S, not this machine's. A 1.5-2.0 bound briefly
  lived in the code, sized to one 16 GB card. It would have capped every
  larger install at the smallest one anybody tested on. What varies with the
  machine is answered by measuring the machine.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model is loaded and no
image is generated.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.generation_request import (  # noqa: E402
    HIRES_SCALE_MAX,
    HIRES_SCALE_MIN,
    MAX_HIRES_DIMENSION,
    FirstImageRequest,
    ResidentModel,
    validate_request,
)
from forge_headless.live_generation_port import (  # noqa: E402
    _hires_kwargs,
    _looks_like_out_of_memory,
)
from forge_studio.contracts import GenerationRequest, HiresSettings  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_request_payload,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_HIRES_TESTS = 44

SELECTION = {
    "checkpoint_model_id": "a" * 32,
    "text_encoder_model_id": "b" * 32,
    "vae_model_id": "c" * 32,
}
GENERATION = {
    "positive_prompt": "a cube",
    "negative_prompt": "",
    "seed": 1,
    "steps": 8,
    "cfg_scale": 4.0,
    "width": 512,
    "height": 512,
}


def wire(hires=None):
    generation = dict(GENERATION)
    if hires is not None:
        generation["hires"] = hires
    return {"model": "", "model_selection": dict(SELECTION), "generation": generation}


def headless(**overrides):
    fields = {
        "request_id": "r1",
        "model_id": "m",
        "width": 512,
        "height": 512,
        "seed": 1,
        "steps": 8,
    }
    fields.update(overrides)
    return FirstImageRequest(**fields)


def problems_for(**overrides):
    result = validate_request(
        headless(**overrides),
        ResidentModel(model_id="m", family="anima", resident=True),
        options_available=True,
        progress_installed=True,
        result_root_writable=True,
        generation_authorized=True,
    )
    return [item["code"] for item in result.problems]


class OffIsAbsentTests(unittest.TestCase):
    """The book's first acceptance criterion, held at three seams."""

    def test_no_group_means_no_contract_field(self) -> None:
        self.assertIsNone(_validated_request_payload(wire())["hires"])

    def test_no_group_means_the_port_passes_nothing_at_all(self) -> None:
        """Not `enable_hr=False` plus inert values -- NOTHING.

        An inert value is still a value, and the next reader has to decide
        whether it matters. An absent kwarg cannot be misread.
        """

        self.assertEqual({}, _hires_kwargs(headless(enable_hr=False)))

    def test_a_disabled_group_still_dispatches_nothing(self) -> None:
        settings = _validated_request_payload(
            wire({"enabled": False, "scale": 2.0})
        )["hires"]
        self.assertFalse(settings.enabled)
        self.assertEqual({}, _hires_kwargs(headless(enable_hr=False)))


class WireValidationTests(unittest.TestCase):
    def test_an_enabled_group_survives_the_wire(self) -> None:
        settings = _validated_request_payload(
            wire({"enabled": True, "scale": 1.5, "upscaler": "Latent",
                  "second_pass_steps": 6, "denoising_strength": 0.5})
        )["hires"]
        self.assertTrue(settings.enabled)
        self.assertEqual(1.5, settings.scale)
        self.assertEqual("Latent", settings.upscaler)
        self.assertEqual(6, settings.second_pass_steps)

    def test_there_is_no_scale_ceiling_at_all(self) -> None:
        """AR6.9. This asserted 4.0 and explained it as "Forge's, not one
        machine's" -- but the note beside the constant argues that a number
        here "caps every install... because of hardware it has never met",
        and that applies to Forge's slider stop exactly as it applied to the
        2.0 it replaced. A scale multiplies output resolution, so the real
        bound is VRAM, answered honestly at dispatch.

        The FLOOR stays: below 1.0 is a downscale.
        """

        self.assertEqual(1.0, HIRES_SCALE_MIN)
        self.assertEqual(float("inf"), HIRES_SCALE_MAX)

    def test_a_scale_below_the_floor_is_refused(self) -> None:
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload(wire({"enabled": True, "scale": 0.5}))
        self.assertIn("hires.scale", str(caught.exception))

    def test_a_large_scale_is_accepted(self) -> None:
        """4.5 and 16.0 used to be refused here."""

        for scale in (4.5, 16.0):
            with self.subTest(scale=scale):
                settings = _validated_request_payload(
                    wire({"enabled": True, "scale": scale}))["hires"]
                self.assertEqual(scale, settings.scale)

    def test_four_times_is_accepted_now(self) -> None:
        """The case the owner widened the range for: 512 x 4.0 -> 2048."""

        settings = _validated_request_payload(
            wire({"enabled": True, "scale": 4.0})
        )["hires"]
        self.assertEqual(4.0, settings.scale)

    def test_a_disabled_group_is_not_range_checked(self) -> None:
        """Nothing will be dispatched, so nothing needs to fit."""

        settings = _validated_request_payload(
            wire({"enabled": False, "scale": 99.0})
        )["hires"]
        self.assertFalse(settings.enabled)

    def test_an_unknown_hires_field_names_its_position(self) -> None:
        with self.assertRaises(PresentationError) as caught:
            _validated_request_payload(wire({"enabled": True, "hr_scale": 2.0}))
        self.assertIn("hires.hr_scale", str(caught.exception))

    def test_out_of_range_numbers_are_refused_by_name(self) -> None:
        for field, value in (
            # 999 used to be refused here. The steps ceiling was removed
            # 2026-08-20 by owner ruling, so the surviving refusal is the
            # FLOOR -- 0 means "inherit the base pass" and below that is not a
            # number of steps. Denoising strength stays 0.0-1.0 because it
            # indexes a schedule; that is arithmetic, not a policy.
            ("second_pass_steps", -1),
            ("denoising_strength", 1.5),
            ("denoising_strength", -0.1),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(PresentationError) as caught:
                    _validated_request_payload(
                        wire({"enabled": True, "scale": 2.0, field: value})
                    )
                self.assertIn(field, str(caught.exception))

    def test_hires_must_be_an_object(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_request_payload(wire("yes please"))

    def test_a_non_boolean_enabled_is_refused(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_request_payload(wire({"enabled": "true"}))


class TargetGeometryTests(unittest.TestCase):
    """The target is SNAPPED, not refused. The ceiling is still a refusal."""

    def test_a_scale_that_does_not_divide_evenly_is_accepted(self) -> None:
        """It used to be refused whole, and the reason given was false.

        The docstring here said "a misaligned target fails INSIDE the second
        pass". It does not: `calculate_target_resolution`
        (modules/processing.py:1271) runs `sRound` on both axes in both of its
        branches before the target is ever used. An owner lost a real job to
        this -- 1368 x 1.5 = 2052, refused -- for arithmetic the engine was
        going to do anyway.
        """

        codes = problems_for(enable_hr=True, hr_scale=1.5, width=1368, height=1368)
        self.assertEqual([], [c for c in codes if "HIRES_TARGET" in c])

    def test_the_snapped_target_is_what_neo_would_use(self) -> None:
        from forge_headless.generation_request import _snap_to_alignment

        self.assertEqual(2056, _snap_to_alignment(1368 * 1.5))
        self.assertEqual(560, _snap_to_alignment(512 * 1.1))

    def test_snapping_always_lands_on_the_alignment(self) -> None:
        from forge_headless.generation_request import (
            DIMENSION_ALIGNMENT,
            _snap_to_alignment,
        )

        for width in range(64, 2048, 8):
            for scale in (1.1, 1.25, 1.5, 1.75, 2.0, 2.3):
                with self.subTest(width=width, scale=scale):
                    snapped = _snap_to_alignment(width * scale)
                    self.assertEqual(0, snapped % DIMENSION_ALIGNMENT)

    def test_the_base_dimensions_are_no_longer_refused(self) -> None:
        """The inversion of this test, and the reason it was wrong.

        It used to assert a refusal and justified it as protecting "what the
        OWNER typed" -- while being the thing that refused what the owner
        typed. Owner ruling, 2026-08-20, after testing it in the core himself:
        500x500 yields a 496x496 image, silently, and "if it needs to silently
        adjust that's FINE."

        The derived Hires target still snaps, because that is a CALCULATION.
        The base dimensions are the owner's and are passed through.
        """

        codes = problems_for(enable_hr=False, width=1001, height=512)
        self.assertNotIn("GENERATION_DIMENSION_MISALIGNED", codes)
        self.assertEqual([], codes)

    def test_there_is_no_size_ceiling_at_all(self) -> None:
        """Owner decision, 2026-08-16: Studio does not restrict size. Ever.

        Both ceilings were Tier-0 profile bounds that outlived their phase and
        capped every install from one card's measurements. A 2304 base was
        refused on hardware that had never been consulted.
        """

        self.assertIsNone(MAX_HIRES_DIMENSION)
        for enormous in (2304, 4096, 8192, 16384):
            with self.subTest(size=enormous):
                codes = problems_for(
                    enable_hr=True, hr_scale=2.0,
                    width=enormous, height=enormous)
                self.assertEqual([], [c for c in codes if "DIMENSION" in c
                                      or "TARGET" in c])

    def test_a_size_that_is_not_a_size_is_still_refused(self) -> None:
        """Not a ceiling -- a validity check. Zero is not a small picture."""
        codes = problems_for(enable_hr=False, width=0, height=512)
        self.assertIn("GENERATION_DIMENSION_OUT_OF_RANGE", codes)

    def test_an_enormous_target_is_accepted(self) -> None:
        """6144 wide. The card decides, not a constant written in advance."""
        codes = problems_for(enable_hr=True, hr_scale=4.0, width=1536, height=1536)
        self.assertEqual([], [c for c in codes if "TARGET" in c])

    def test_hires_off_is_never_geometry_checked(self) -> None:
        codes = problems_for(enable_hr=False, hr_scale=99.0)
        self.assertNotIn("GENERATION_HIRES_TARGET_TOO_LARGE", codes)
        self.assertNotIn("GENERATION_HIRES_SCALE_OUT_OF_RANGE", codes)

    def test_hires_is_no_longer_refused_wholesale(self) -> None:
        self.assertNotIn(
            "GENERATION_HIRES_NOT_PERMITTED", problems_for(enable_hr=True, hr_scale=1.5)
        )


class DispatchKwargTests(unittest.TestCase):
    def test_additional_modules_is_a_LIST_not_a_string(self) -> None:
        """The trap that costs a whole generation to discover.

        `hr_additional_modules` is declared `list = field(default=None)`, and
        processing does `"Use same choices" not in self.hr_additional_modules`
        -- so the DEFAULT raises TypeError, and raises it AFTER the base pass
        has burned its steps.

        A bare string survives that membership test by substring luck, but
        processing also does `isinstance(..., list)` before recording
        `Hires Module 1`, so a string yields a working generation whose recipe
        is silently incomplete. The Forge-Studio extension's comment insisting
        it MUST be a string is true of an older Forge, not of this build.
        """

        kwargs = _hires_kwargs(headless(enable_hr=True))
        self.assertIsInstance(kwargs["hr_additional_modules"], list)
        self.assertEqual(["Use same choices"], kwargs["hr_additional_modules"])

    def test_never_None_which_is_the_actual_crash(self) -> None:
        self.assertIsNotNone(
            _hires_kwargs(headless(enable_hr=True))["hr_additional_modules"]
        )

    def test_empty_choices_become_None_for_the_engine_default(self) -> None:
        """Neo reads None, not "", as "use the default"."""

        kwargs = _hires_kwargs(headless(enable_hr=True, hr_upscaler="", hr_sampler_name=""))
        self.assertIsNone(kwargs["hr_upscaler"])
        self.assertIsNone(kwargs["hr_sampler_name"])

    def test_a_chosen_upscaler_is_carried_verbatim(self) -> None:
        kwargs = _hires_kwargs(headless(enable_hr=True, hr_upscaler="Latent (bicubic)"))
        self.assertEqual("Latent (bicubic)", kwargs["hr_upscaler"])

    def test_studio_denoise_maps_onto_neos_field_name(self) -> None:
        """Studio says `denoising_strength` inside the Hires group; Neo spells
        the second pass's denoise as the top-level `denoising_strength`."""

        kwargs = _hires_kwargs(headless(enable_hr=True, hr_denoising_strength=0.42))
        self.assertEqual(0.42, kwargs["denoising_strength"])


class OutOfMemoryIsNamedTests(unittest.TestCase):
    """The machine's answer, measured rather than projected.

    No projection is attempted anywhere: there is no validated model of what a
    Hires pass costs on an arbitrary card, and a confident-looking guess would
    refuse passes that would have worked on hardware nobody here has seen.
    """

    def test_an_allocator_failure_is_recognised_by_message(self) -> None:
        self.assertTrue(_looks_like_out_of_memory(RuntimeError("CUDA out of memory.")))

    def test_an_unrelated_failure_is_not(self) -> None:
        self.assertFalse(_looks_like_out_of_memory(ValueError("bad sampler name")))

    def test_asking_the_question_does_not_import_torch(self) -> None:
        """The check must not drag torch -- and CUDA -- into a clean process.

        It did. This helper imported torch to identify the exception type, so
        merely CALLING it from a test polluted `sys.modules` and failed
        `test_import_boundaries.test_launch_import_does_not_initialize_cuda`
        in the full run while passing in isolation. Third instance of the same
        shape this phase, so it gets its own assertion.
        """

        import ast

        source = (
            APP_ROOT / "forge_headless" / "live_generation_port.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_looks_like_out_of_memory"
        )
        # AST, not a raw-text search: this file documents its own rule in a
        # comment, and a text search would match the prose describing it.
        imports = [
            alias.name
            for node in ast.walk(function)
            for alias in getattr(node, "names", [])
            if isinstance(node, ast.Import)
        ]
        self.assertNotIn("torch", imports)

    def test_out_of_memory_is_a_failure_not_a_bad_request(self) -> None:
        """Mapping it onto REQUEST_UNSUPPORTED would tell the owner to change
        their request when the honest answer is that the card ran out."""

        from forge_headless.studio_generation import GENERATION_FAILED, studio_code_for

        self.assertEqual(
            GENERATION_FAILED, studio_code_for("GENERATION_HIRES_OUT_OF_MEMORY")
        )


class UnknownUpscalerIsRefusedTests(unittest.TestCase):
    """Found the same way the scheduler gap was: by sending a bad name.

    Unchecked, an unknown upscaler reached dispatch and died there as a bare
    `ValueError`, AFTER the base pass had been paid for, reaching the owner as
    "GENERATION_FAILED: ValueError". Loud but uninformative, and it costs a
    whole generation to discover.

    `is_known_upscaler` was written alongside `is_known_sampler` and had zero
    production callers -- the same shape of unfinished wiring, in the same
    module, found again one phase later.
    """

    @staticmethod
    def _application():
        from forge_studio.application import StudioApplication

        return StudioApplication(object())

    @staticmethod
    def _registries():
        from forge_headless.neo_registries import Registries

        return Registries(
            samplers=("Euler",),
            schedulers=("Automatic",),
            available=True,
            latent_upscalers=("Latent", "Latent (bicubic)"),
            image_upscalers=("remacri_original",),
        )

    def _check(self, upscaler: str):
        from unittest import mock

        from forge_headless import neo_registries

        request = GenerationRequest(
            model_id="m", positive_prompt="p", negative_prompt="",
            seed=1, steps=8, cfg_scale=4.0, width=512, height=512,
            hires=HiresSettings(enabled=True, scale=1.5, upscaler=upscaler),
        )
        with mock.patch.object(
            neo_registries, "read_registries", return_value=self._registries()
        ):
            self._application()._validate_engine_choices(request)

    def test_an_unknown_upscaler_is_refused_before_any_work(self) -> None:
        from forge_studio.contracts import StudioError

        with self.assertRaises(StudioError) as caught:
            self._check("NotAnUpscaler")
        self.assertEqual("hires.upscaler", caught.exception.error.field)

    def test_the_refusal_names_what_is_actually_offered(self) -> None:
        """An upscaler name is not guessable -- it is whatever file happens to
        be in the model directory."""

        from forge_studio.contracts import StudioError

        with self.assertRaises(StudioError) as caught:
            self._check("NotAnUpscaler")
        self.assertIn("remacri_original", caught.exception.error.message)

    def test_both_kinds_are_accepted(self) -> None:
        self._check("Latent (bicubic)")
        self._check("remacri_original")

    def test_empty_means_engine_default_and_is_not_refused(self) -> None:
        self._check("")


class SecondPassProgressTests(unittest.TestCase):
    """One public job, two sampling passes, an honest bar.

    The book is explicit that Hires is NOT a second public job, so both new
    states project onto RUNNING and the owner sees one job whose stage label
    changes.
    """

    @staticmethod
    def _progress():
        from forge_headless.headless_progress import HeadlessProgress, JobState

        progress = HeadlessProgress(job_id="j", preview_enabled=False)
        progress.advance_to(JobState.LOADING)
        progress.advance_to(JobState.CONDITIONING)
        progress.advance_to(JobState.SAMPLING)
        return progress

    def test_every_table_stays_total(self) -> None:
        """A JobState missing from STATE_PROJECTION is a KeyError on the FIRST
        poll, not a default. The dict is total by design."""

        from forge_headless.headless_progress import (
            ALLOWED_TRANSITIONS,
            STAGE_LABELS,
            JobState,
        )
        from forge_headless.studio_generation import STATE_PROJECTION

        for state in JobState:
            with self.subTest(state=state.value):
                self.assertIn(state, STATE_PROJECTION)
                self.assertIn(state, STAGE_LABELS)
                self.assertIn(state, ALLOWED_TRANSITIONS)

    def test_hires_is_not_a_second_public_job(self) -> None:
        from forge_headless.headless_progress import JobState
        from forge_studio.contracts import JobState as StudioJobState
        from forge_headless.studio_generation import project_state

        for state in (JobState.HIRES_PREPARING, JobState.HIRES_SAMPLING):
            with self.subTest(state=state.value):
                self.assertEqual(StudioJobState.RUNNING, project_state(state))

    def test_a_base_only_job_never_enters_the_hires_states(self) -> None:
        from forge_headless.headless_progress import JobState

        progress = self._progress()
        progress.set_total_steps(6)
        for step in range(1, 7):
            progress.report_step(step)
        self.assertIs(JobState.SAMPLING, progress.snapshot().state)

    def test_the_gap_before_the_second_pass_is_named(self) -> None:
        """The upscale runs with NO step callbacks at all. Unnamed, that gap is
        where a Hires job looks hung."""

        from forge_headless.headless_progress import JobState

        progress = self._progress()
        progress.expect_hires_pass()
        progress.set_total_steps(6)
        for step in range(1, 7):
            progress.report_step(step)
        snapshot = progress.snapshot()
        self.assertIs(JobState.HIRES_PREPARING, snapshot.state)
        self.assertEqual("Preparing Hires pass", snapshot.stage_label)

    def test_the_second_pass_rewinds_the_counter(self) -> None:
        """Without this the bar sits at 100% for the whole second pass.

        `report_step` is monotonic on purpose, so the Hires pass's steps 0..N
        are all below the base pass's final step and all discarded. And the
        budget SHRINKS -- launch_sampling(t_enc + 1) where t_enc is
        denoise * steps -- which is what makes it read as "already done".
        """

        from forge_headless.headless_progress import JobState

        progress = self._progress()
        progress.expect_hires_pass()
        progress.set_total_steps(6)
        for step in range(1, 7):
            progress.report_step(step)
        self.assertEqual(1.0, progress.snapshot().fraction)

        progress.set_total_steps(4)  # the SMALLER second-pass budget
        snapshot = progress.snapshot()
        self.assertIs(JobState.HIRES_SAMPLING, snapshot.state)
        self.assertEqual(0, snapshot.step)
        self.assertEqual(4, snapshot.total_steps)
        self.assertEqual(0.0, snapshot.fraction)

    def test_the_second_pass_then_advances_normally(self) -> None:
        progress = self._progress()
        progress.expect_hires_pass()
        progress.set_total_steps(6)
        for step in range(1, 7):
            progress.report_step(step)
        progress.set_total_steps(4)
        for step in range(1, 5):
            progress.report_step(step)
        self.assertEqual(1.0, progress.snapshot().fraction)

    def test_the_second_pass_still_reaches_decoding(self) -> None:
        from forge_headless.headless_progress import JobState

        progress = self._progress()
        progress.expect_hires_pass()
        progress.set_total_steps(6)
        for step in range(1, 7):
            progress.report_step(step)
        progress.set_total_steps(4)
        progress.advance_to(JobState.DECODING)
        self.assertIs(JobState.DECODING, progress.snapshot().state)

    def test_cancellation_is_reachable_from_both_hires_states(self) -> None:
        """The book requires cancellation before AND within the second pass."""

        from forge_headless.headless_progress import ALLOWED_TRANSITIONS, JobState

        for state in (JobState.HIRES_PREPARING, JobState.HIRES_SAMPLING):
            with self.subTest(state=state.value):
                self.assertIn(JobState.CANCELLED, ALLOWED_TRANSITIONS[state])
                self.assertIn(JobState.FAILED, ALLOWED_TRANSITIONS[state])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_HIRES_TESTS, loaded.countTestCases())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
