"""First-image readiness — options, progress, request, port, delivery, probe.

No real model file is touched, no Torch is imported, and nothing generates.
Synthetic PNG fixtures are written by the tests and are explicitly not
generated images.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

WORKSPACE_ROOT = APP_ROOT.parent
EVIDENCE = WORKSPACE_ROOT / "Evidence" / "studio-first-image-readiness"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_readiness_probe.py"

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.generation_port import (  # noqa: E402
    GENERATION_NOT_AUTHORIZED,
    GenerationGateway,
    GenerationOutcome,
    PolicyGatedGenerator,
    RecordingGenerator,
)
from forge_headless.generation_request import (  # noqa: E402
    DEFAULT_CFG_SCALE,
    DEFAULT_DISTILLED_CFG_SCALE,
    DEFAULT_SAMPLER,
    DEFAULT_SCHEDULER,
    DERIVATIONS,
    FirstImageRequest,
    ResidentModel,
    build_first_image_request,
    validate_request,
)
from forge_headless.headless_options import (  # noqa: E402
    COMPUTED_PATH_OPTIONS,
    GENERATION_OPTIONS,
    HeadlessOptions,
    legacy_defaults,
    output_directory_overrides,
)
from forge_headless.headless_progress import (  # noqa: E402
    ForgeStateBridge,
    HeadlessProgress,
    JobState,
    ProgressError,
)
from forge_headless.import_graph import module_level_imports, paths_to_forbidden  # noqa: E402


EXPECTED_READINESS_TESTS = 56

#: A 1x1 PNG. Written by tests as a delivery fixture; not a generated image.
SYNTHETIC_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def a_model(**overrides: object) -> ResidentModel:
    values: dict = {
        "model_id": "m1",
        "family": "anima",
        "resident": True,
        "supported_samplers": ("Euler",),
        "supported_schedulers": ("Automatic",),
    }
    values.update(overrides)
    return ResidentModel(**values)  # type: ignore[arg-type]


def a_request(**overrides: object) -> FirstImageRequest:
    request = build_first_image_request("r1", "m1", seed=7, steps=12)
    if overrides:
        from dataclasses import replace

        request = replace(request, **overrides)  # type: ignore[arg-type]
    return request


def valid(request: FirstImageRequest, model: ResidentModel, **kwargs: object):
    defaults = {
        "options_available": True,
        "progress_installed": True,
        "result_root_writable": True,
        "generation_authorized": False,
    }
    defaults.update(kwargs)
    return validate_request(request, model, **defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------ options


class GenerationOptionTests(unittest.TestCase):
    def test_every_generation_option_resolves_from_forge_source(self) -> None:
        defaults = legacy_defaults(APP_ROOT)
        missing = [name for name in GENERATION_OPTIONS if name not in defaults]
        self.assertEqual(missing, [], f"unresolvable options: {missing}")
        self.assertGreaterEqual(len(GENERATION_OPTIONS), 30)

    def test_key_defaults_match_their_declaration_sites(self) -> None:
        defaults = legacy_defaults(APP_ROOT)
        self.assertEqual(defaults["emphasis"], "Original")
        self.assertEqual(defaults["anima_do_reference"], False)
        self.assertEqual(defaults["forge_unet_storage_dtype"], "Automatic")
        self.assertEqual(defaults["save_prompt_comments"], False)
        forge_options = (
            APP_ROOT / "modules_forge" / "shared_options.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"forge_unet_storage_dtype": OptionInfo("Automatic")', forge_options)

    def test_options_outside_shared_options_are_still_found(self) -> None:
        # Six generation-path options live in modules_forge/shared_options.py
        # and modules/processing_scripts/*. Parsing only the main file lost them.
        defaults = legacy_defaults(APP_ROOT)
        for name in (
            "forge_additional_modules",
            "forge_unet_storage_dtype",
            "refiner_lora_replacement",
            "save_prompt_comments",
        ):
            self.assertIn(name, defaults, name)

    def test_keyword_form_defaults_are_read(self) -> None:
        # `shared.OptionInfo(default="...")` -- positional-only parsing missed it.
        self.assertIn("refiner_lora_replacement", legacy_defaults(APP_ROOT))

    def test_computed_path_options_are_explicit_overrides(self) -> None:
        defaults = legacy_defaults(APP_ROOT)
        for name in COMPUTED_PATH_OPTIONS:
            self.assertNotIn(name, defaults, "a computed path must not be parsed")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        overrides = output_directory_overrides(root)
        self.assertEqual(set(overrides), set(COMPUTED_PATH_OPTIONS))
        options = HeadlessOptions(defaults, overrides=overrides)
        self.assertEqual(options.outdir_videos, str(root))
        self.assertEqual(options.inventory()[0]["default_source"], "override")

    def test_missing_option_names_itself(self) -> None:
        options = HeadlessOptions({"emphasis": "Original"})
        with self.assertRaises(HeadlessError) as caught:
            _ = options.not_a_real_option
        self.assertEqual(caught.exception.code, "HEADLESS_OPTION_NOT_AVAILABLE")
        self.assertIn("not_a_real_option", caught.exception.message)

    def test_options_module_imports_nothing_forbidden(self) -> None:
        self.assertEqual(
            paths_to_forbidden(
                "forge_headless.headless_options",
                APP_ROOT,
                ("gradio", "gradio_client", "torch"),
            ),
            [],
        )


# ----------------------------------------------------------------- progress


class ProgressTests(unittest.TestCase):
    def test_full_lifecycle_transitions(self) -> None:
        progress = HeadlessProgress("j")
        for state in (
            JobState.LOADING,
            JobState.CONDITIONING,
            JobState.SAMPLING,
            JobState.DECODING,
            JobState.PUBLISHING,
        ):
            progress.advance_to(state)
            self.assertIs(progress.state, state)
        progress.mark_completed()
        self.assertIs(progress.state, JobState.COMPLETED)

    def test_illegal_transition_is_refused(self) -> None:
        progress = HeadlessProgress("j")
        with self.assertRaises(ProgressError):
            progress.advance_to(JobState.DECODING)

    def test_terminal_state_cannot_regress(self) -> None:
        progress = HeadlessProgress("j")
        progress.mark_cancelled()
        self.assertTrue(progress.terminal)
        with self.assertRaises(ProgressError):
            progress.advance_to(JobState.SAMPLING)
        progress.mark_completed()
        self.assertIs(progress.state, JobState.CANCELLED)

    def test_fraction_is_none_until_a_real_total_arrives(self) -> None:
        progress = HeadlessProgress("j")
        self.assertIsNone(progress.snapshot().fraction)
        progress.report_step(3)
        self.assertIsNone(progress.snapshot().fraction, "no fabricated percentage")
        progress.set_total_steps(12)
        self.assertAlmostEqual(progress.snapshot().fraction, 0.25)

    def test_step_reporting_is_monotonic_and_clamped(self) -> None:
        progress = HeadlessProgress("j")
        progress.set_total_steps(10)
        progress.report_step(5)
        progress.report_step(3)
        self.assertEqual(progress.snapshot().step, 5, "out-of-order callback ignored")
        progress.report_step(99)
        self.assertEqual(progress.snapshot().step, 10, "clamped to the real total")

    def test_cancellation_is_cooperative_and_idempotent(self) -> None:
        progress = HeadlessProgress("j")
        self.assertTrue(progress.request_cancellation())
        self.assertTrue(progress.cancellation_requested)
        self.assertIsNot(progress.state, JobState.CANCELLED, "not cancelled until observed")
        progress.mark_cancelled()
        self.assertFalse(progress.request_cancellation(), "terminal job refuses")

    def test_failure_message_is_truncated_and_sanitized(self) -> None:
        progress = HeadlessProgress("j")
        progress.mark_failed("x" * 500)
        snapshot = progress.snapshot()
        self.assertIs(snapshot.state, JobState.FAILED)
        self.assertLessEqual(len(snapshot.error or ""), 200)

    def test_preview_only_when_enabled_and_sampling(self) -> None:
        off = HeadlessProgress("j", preview_enabled=False)
        off.advance_to(JobState.LOADING)
        off.advance_to(JobState.CONDITIONING)
        off.advance_to(JobState.SAMPLING)
        off.report_step(1)
        self.assertFalse(off.snapshot().preview_available)

    def test_snapshot_is_non_consuming(self) -> None:
        progress = HeadlessProgress("j")
        progress.set_total_steps(4)
        progress.report_step(2)
        first = progress.snapshot()
        second = progress.snapshot()
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_progress_module_imports_nothing_forbidden(self) -> None:
        imports = module_level_imports(
            ast.parse(
                (APP_ROOT / "forge_headless" / "headless_progress.py").read_text(
                    encoding="utf-8"
                )
            )
        )
        for forbidden in ("gradio", "gradio_client", "torch", "modules.shared"):
            self.assertNotIn(forbidden, imports)


class StateBridgeTests(unittest.TestCase):
    def test_bridge_writes_through_to_owned_progress(self) -> None:
        progress = HeadlessProgress("j")
        bridge = ForgeStateBridge(progress)
        bridge.sampling_steps = 20
        bridge.sampling_step = 7
        snapshot = progress.snapshot()
        self.assertEqual(snapshot.total_steps, 20)
        self.assertEqual(snapshot.step, 7)

    def test_bridge_interrupt_requests_cancellation(self) -> None:
        progress = HeadlessProgress("j")
        bridge = ForgeStateBridge(progress)
        self.assertFalse(bridge.interrupted)
        bridge.interrupt()
        self.assertTrue(bridge.interrupted)
        self.assertTrue(progress.cancellation_requested)

    def test_bridge_exposes_exactly_the_scanned_attributes(self) -> None:
        progress = HeadlessProgress("j")
        bridge = ForgeStateBridge(progress)
        for name in ForgeStateBridge.SUPPORTED:
            getattr(bridge, name)

    def test_bridge_names_an_unsupported_field(self) -> None:
        bridge = ForgeStateBridge(HeadlessProgress("j"))
        with self.assertRaises(AttributeError) as caught:
            _ = bridge.current_image
        self.assertIn("current_image", str(caught.exception))

    def test_legacy_state_class_is_untouched(self) -> None:
        source = (APP_ROOT / "modules" / "shared_state.py").read_text(encoding="utf-8")
        self.assertIn("class State:", source)
        self.assertNotIn("ForgeStateBridge", source)


# ------------------------------------------------------------------ request


class RequestProfileTests(unittest.TestCase):
    def test_profile_is_immutable(self) -> None:
        request = a_request()
        with self.assertRaises(Exception):
            request.steps = 30  # type: ignore[misc]

    def test_defaults_are_derived_and_documented(self) -> None:
        request = a_request()
        self.assertEqual(request.sampler, DEFAULT_SAMPLER)
        self.assertEqual(request.scheduler, DEFAULT_SCHEDULER)
        self.assertEqual(request.cfg_scale, DEFAULT_CFG_SCALE)
        self.assertEqual(request.distilled_cfg_scale, DEFAULT_DISTILLED_CFG_SCALE)
        for key in ("sampler", "scheduler", "cfg_scale", "distilled_cfg_scale"):
            self.assertIn(key, DERIVATIONS)

    def test_sampler_and_scheduler_exist_in_forge_source(self) -> None:
        samplers = (APP_ROOT / "modules" / "sd_samplers_kdiffusion.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('("Euler", "sample_euler"', samplers)
        schedulers = (APP_ROOT / "modules" / "sd_schedulers.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('Scheduler("automatic", "Automatic", None)', schedulers)

    def test_distilled_cfg_matches_the_anima_shift(self) -> None:
        model_list = (
            APP_ROOT / "modules_forge" / "packages" / "huggingface_guess" / "model_list.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"shift": 3.0,', model_list)
        self.assertEqual(DEFAULT_DISTILLED_CFG_SCALE, 3.0)
        processing = (APP_ROOT / "modules" / "processing.py").read_text(encoding="utf-8")
        self.assertIn("set_shift(shift=self.distilled_cfg_scale)", processing)

    def test_valid_profile_has_only_the_authorization_problem(self) -> None:
        result = valid(a_request(), a_model())
        self.assertEqual(
            [item["code"] for item in result.problems], [GENERATION_NOT_AUTHORIZED]
        )

    def test_misaligned_dimensions_are_accepted(self) -> None:
        """Inverted 2026-08-20 by owner ruling.

        Studio's core needs no alignment: `modules/processing.py` computes the
        latent with floor division and asserts nothing. Verified by the owner
        end to end -- 500x500 produces 496x496 with no error.

        The values asserted are the ones the owner actually tested.
        """

        for width in (770, 500, 900, 777):
            with self.subTest(width=width):
                codes = [item["code"]
                         for item in valid(a_request(width=width),
                                           a_model()).problems]
                self.assertNotIn("GENERATION_DIMENSION_MISALIGNED", codes)

    def test_a_multiple_of_eight_is_accepted(self) -> None:
        """The regression. 1368 is 8 x 171 -- valid for the VAE, odd for the
        patch grid, and refused for a year by an alignment of 16 that came
        from multiplying the two together.

        The model does not need it: backend/nn/anima.py:478 pads the latent up
        to the patch size and :510 crops the output back. Reported by an owner
        who could generate 1024x1368 in the Forge Studio extension -- the same
        engine, without this precheck in front of it.

        Both axes, because the check runs per axis and passing one proves
        nothing about the other.
        """

        codes = [
            item["code"]
            for item in valid(
                a_request(width=1024, height=1368), a_model()
            ).problems
        ]
        self.assertNotIn("GENERATION_DIMENSION_MISALIGNED", codes)
        codes = [
            item["code"]
            for item in valid(
                a_request(width=1368, height=1024), a_model()
            ).problems
        ]
        self.assertNotIn("GENERATION_DIMENSION_MISALIGNED", codes)

    def test_batch_size_and_output_count_are_enforced(self) -> None:
        codes = [
            item["code"]
            for item in valid(a_request(batch_size=2, output_count=2), a_model()).problems
        ]
        self.assertIn("GENERATION_BATCH_SIZE_UNSUPPORTED", codes)
        self.assertIn("GENERATION_OUTPUT_COUNT_UNSUPPORTED", codes)

    def test_random_seed_is_rejected(self) -> None:
        codes = [item["code"] for item in valid(a_request(seed=-1), a_model()).problems]
        self.assertIn("GENERATION_SEED_NOT_FIXED", codes)

    def test_unsupported_sampler_and_scheduler_are_rejected(self) -> None:
        codes = [
            item["code"]
            for item in valid(
                a_request(sampler="Euler a", scheduler="Karras"), a_model()
            ).problems
        ]
        self.assertIn("GENERATION_SAMPLER_UNSUPPORTED", codes)
        self.assertIn("GENERATION_SCHEDULER_UNSUPPORTED", codes)

    def test_extras_must_be_disabled(self) -> None:
        codes = [
            item["code"]
            for item in valid(
                a_request(
                    enable_hr=True,
                    enable_adetailer=True,
                    enable_extensions=True,
                    reference_image_enabled=True,
                ),
                a_model(),
            ).problems
        ]
        for expected in (
            # GENERATION_ADETAILER_NOT_PERMITTED left this list in P0.8, for
            # exactly the reason the Hires note below records. Auto Detail is
            # no longer an "extra" refused wholesale -- the slots travel and
            # the port runs them, so the question became whether the pass
            # asked for is dispatchable. This request turns the flag on and
            # names no slot, which is its own incoherence and its own code.
            "GENERATION_ADETAILER_NO_SLOTS",
            "GENERATION_EXTENSIONS_NOT_PERMITTED",
            "GENERATION_REFERENCE_NOT_PERMITTED",
        ):
            self.assertIn(expected, codes)
        # GENERATION_HIRES_NOT_PERMITTED is gone from this list in P0.7. Hires
        # is no longer an "extra" refused wholesale -- it is a request the
        # owner can make, and what replaced the blanket refusal is a set of
        # checks on whether the pass they asked for is dispatchable.
        self.assertNotIn("GENERATION_HIRES_NOT_PERMITTED", codes)

    def test_absent_model_and_mismatch_are_distinct(self) -> None:
        codes = [
            item["code"]
            for item in valid(a_request(), a_model(resident=False, model_id="other")).problems
        ]
        self.assertIn("GENERATION_MODEL_NOT_RESIDENT", codes)
        self.assertIn("GENERATION_MODEL_MISMATCH", codes)

    def test_validation_reports_every_problem_not_the_first(self) -> None:
        """Named rather than counted.

        This asserted a COUNT, so removing the size ceiling silently broke it
        without saying which problem had gone. The point was always that four
        independent faults each get reported.
        """

        # `width=770` used to be the first fault here. The alignment refusal
        # was removed 2026-08-20, so the fault is now a steps FLOOR violation
        # -- still four independent problems, none of them an invented cap.
        result = valid(
            a_request(steps=0, batch_size=3, seed=-1, enable_hr=True), a_model()
        )
        self.assertEqual(
            {
                "GENERATION_STEPS_OUT_OF_RANGE",
                "GENERATION_BATCH_SIZE_UNSUPPORTED",
                "GENERATION_SEED_NOT_FIXED",
                "HEADLESS_GENERATION_NOT_AUTHORIZED",
            },
            {problem["code"] for problem in result.problems},
        )

    def test_request_view_omits_prompt_text(self) -> None:
        rendered = json.dumps(a_request(positive_prompt="a secret phrase").to_dict())
        self.assertNotIn("a secret phrase", rendered)
        self.assertIn("positive_prompt_length", rendered)

    def test_request_accepts_no_filesystem_path(self) -> None:
        for name in FirstImageRequest.__dataclass_fields__:
            self.assertNotIn("path", name)


# --------------------------------------------------------------------- port


class GenerationPortTests(unittest.TestCase):
    def test_default_gate_refuses_after_validation(self) -> None:
        gateway = GenerationGateway()
        with self.assertRaises(HeadlessError) as caught:
            gateway.submit(a_request(), a_model(), options_available=True)
        self.assertEqual(caught.exception.code, GENERATION_NOT_AUTHORIZED)
        self.assertIsInstance(gateway.port, PolicyGatedGenerator)

    def test_validation_failure_never_reaches_the_port(self) -> None:
        recorder = RecordingGenerator()
        gateway = GenerationGateway(recorder)
        with self.assertRaises(HeadlessError) as caught:
            gateway.submit(a_request(steps=0), a_model(), options_available=True)
        self.assertEqual(caught.exception.code, "GENERATION_STEPS_OUT_OF_RANGE")
        self.assertEqual(recorder.requests, [], "port must not see an invalid request")

    def test_recording_backend_receives_the_exact_request(self) -> None:
        outcome = GenerationOutcome(
            job_id="r1",
            request_id="r1",
            result_relative_location="first-image.png",
            media_type="image/png",
            width=768,
            height=768,
            seed=7,
        )
        recorder = RecordingGenerator(outcome=outcome)
        gateway = GenerationGateway(recorder)
        request = a_request()
        returned = gateway.submit(request, a_model(), options_available=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIs(recorder.requests[0], request)
        self.assertEqual(returned.result_relative_location, "first-image.png")
        self.assertIs(gateway.progress_for("r1").state, JobState.COMPLETED)

    def test_no_denoise_or_decode_is_ever_invoked(self) -> None:
        recorder = RecordingGenerator()
        gateway = GenerationGateway(recorder)
        with self.assertRaises(HeadlessError):
            gateway.submit(a_request(), a_model(), options_available=True)
        self.assertEqual(recorder.denoise_calls, 0)
        self.assertEqual(recorder.decode_calls, 0)

    def test_cancellation_stops_sampling_and_publishes_nothing(self) -> None:
        outcome = GenerationOutcome("r1", "r1", "x.png", "image/png", 768, 768, 7)
        recorder = RecordingGenerator(outcome=outcome, cancel_at_step=3, total_steps=12)
        gateway = GenerationGateway(recorder)
        with self.assertRaises(HeadlessError) as caught:
            gateway.submit(a_request(), a_model(), options_available=True)
        self.assertEqual(caught.exception.code, "GENERATION_CANCELLED")
        progress = gateway.progress_for("r1")
        self.assertIs(progress.state, JobState.CANCELLED)
        self.assertLess(progress.snapshot().step, 12)

    def test_failed_job_reaches_a_terminal_state(self) -> None:
        recorder = RecordingGenerator(fail_with="synthetic backend failure")
        gateway = GenerationGateway(recorder)
        with self.assertRaises(HeadlessError) as caught:
            gateway.submit(a_request(), a_model(), options_available=True)
        self.assertEqual(caught.exception.code, "GENERATION_BACKEND_FAILED")
        self.assertIs(gateway.progress_for("r1").state, JobState.FAILED)

    def test_repeated_requests_are_isolated(self) -> None:
        outcome = GenerationOutcome("a", "a", "a.png", "image/png", 768, 768, 7)
        gateway = GenerationGateway(RecordingGenerator(outcome=outcome, total_steps=2))
        from dataclasses import replace

        first = a_request()
        second = replace(first, request_id="r2")
        gateway.submit(first, a_model(), options_available=True)
        gateway.submit(second, a_model(), options_available=True)
        self.assertIsNot(gateway.progress_for("r1"), gateway.progress_for("r2"))

    def test_port_module_imports_nothing_forbidden(self) -> None:
        imports = module_level_imports(
            ast.parse(
                (APP_ROOT / "forge_headless" / "generation_port.py").read_text(
                    encoding="utf-8"
                )
            )
        )
        for forbidden in ("torch", "gradio", "gradio_client", "modules"):
            self.assertNotIn(forbidden, imports)


# ---------------------------------------------------------- result delivery


class ResultDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=EVIDENCE, prefix="fi-"))
        # Explicitly a fixture, not a generated image.
        self.png = self.root / "SYNTHETIC-NOT-GENERATED.png"
        self.png.write_bytes(SYNTHETIC_PNG)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def registry(self):
        from forge_studio.result_delivery import ResultRegistry

        return ResultRegistry(self.root)

    def test_synthetic_png_registers_and_yields_an_opaque_handle(self) -> None:
        asset = self.registry().register(self.png, media_type="image/png")
        self.assertTrue(asset.handle.startswith("studio-result/"))
        self.assertNotIn(self.png.name, asset.handle)
        self.assertNotIn(str(self.root), asset.handle)
        self.assertEqual(asset.media_type, "image/png")
        self.assertEqual(asset.byte_length, len(SYNTHETIC_PNG))

    def test_handle_resolves_back_to_the_contained_file(self) -> None:
        registry = self.registry()
        asset = registry.register(self.png, media_type="image/png")
        payload = registry.read(asset.handle)
        self.assertEqual(payload.content, SYNTHETIC_PNG)
        self.assertEqual(payload.media_type, "image/png")

    def test_unknown_handle_is_refused(self) -> None:
        registry = self.registry()
        registry.register(self.png, media_type="image/png")
        with self.assertRaises(Exception):
            registry.read("studio-result/" + "0" * 32 + ".png")

    def test_unsupported_media_type_is_refused(self) -> None:
        with self.assertRaises(Exception):
            self.registry().register(self.png, media_type="application/zip")

    def test_no_result_is_published_for_a_cancelled_job(self) -> None:
        outcome = GenerationOutcome("r1", "r1", "x.png", "image/png", 768, 768, 7)
        gateway = GenerationGateway(
            RecordingGenerator(outcome=outcome, cancel_at_step=1, total_steps=4)
        )
        registry = self.registry()
        with self.assertRaises(HeadlessError) as caught:
            gateway.submit(a_request(), a_model(), options_available=True)
        self.assertEqual(caught.exception.code, "GENERATION_CANCELLED")
        # Nothing was handed to the registry, so no handle can resolve.
        with self.assertRaises(Exception):
            registry.read("studio-result/" + "1" * 32 + ".png")


# -------------------------------------------------------------------- probe


class ProbeTests(unittest.TestCase):
    def test_probe_parses_and_blocks_the_right_modules(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn('FORBIDDEN_EXACT = ("modules.shared_options"', source)
        self.assertIn('FORBIDDEN_ROOTS = ("gradio", "gradio_client")', source)

    def test_probe_imports_nothing_forbidden_at_module_scope(self) -> None:
        imports = module_level_imports(ast.parse(PROBE.read_text(encoding="utf-8")))
        for forbidden in ("torch", "gradio", "socket", "urllib", "http"):
            self.assertNotIn(forbidden, imports)

    def test_probe_declares_a_timeout(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn("TIMEOUT_SECONDS", source)
        self.assertIn("worker.join(TIMEOUT_SECONDS)", source)

    def test_probe_proves_all_ten_conditions(self) -> None:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", str(PROBE)],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(
            completed.returncode, 0, f"{completed.stdout}\n{completed.stderr[-1500:]}"
        )
        report = json.loads(
            (EVIDENCE / "FIRST_IMAGE_READINESS_REPORT.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["verdict"], "FIRST_IMAGE_READINESS_PROVEN")
        self.assertEqual(
            sorted(report["verdicts"]),
            sorted(
                [
                    "GENERATION_REQUEST_VALIDATED",
                    "PROGRESS_SOURCE_READY",
                    "GENERATION_BLOCKED_BY_POLICY",
                    "DENOISING_NOT_CALLED",
                    "VAE_DECODE_NOT_CALLED",
                    "NO_REAL_IMAGE_GENERATED",
                    "GRADIO_NOT_IMPORTED",
                    "TORCH_NOT_IMPORTED",
                    "NO_CUDA_INITIALIZATION",
                    "NO_EXTERNAL_NETWORK",
                ]
            ),
        )
        self.assertEqual(report["denoise_calls"], 0)
        self.assertEqual(report["vae_decode_calls"], 0)
        self.assertFalse(report["real_model_accessed"])
        self.assertFalse(report["torch_imported"])
        self.assertFalse(report["gradio_imported"])
        self.assertFalse(report["shared_options_imported"])

    def test_probe_report_is_redacted(self) -> None:
        raw = (EVIDENCE / "FIRST_IMAGE_READINESS_REPORT.json").read_text(encoding="utf-8")
        self.assertNotIn(str(APP_ROOT), raw)
        self.assertNotIn(Path.home().name, raw)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_READINESS_TESTS,
            "Readiness test count changed: update EXPECTED_READINESS_TESTS "
            "deliberately, or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
