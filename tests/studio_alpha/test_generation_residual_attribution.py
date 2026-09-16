"""Contracts for post-generation CUDA residual attribution.

Three things are pinned here, none of which need a device:

* the telemetry classifier consumes the canonical `VramSample` schema and fails
  closed on anything else -- proven against the **real** producer, so a schema
  drift breaks a test rather than a live run;
* the first completed sampler step is taken from the first authoritative event,
  with the prior post-hoc `11` reproduced beside it;
* a liveness-driven allocator distinguishes a fixed reusable plateau from
  cumulative per-job growth across three cycles.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no generation, no server.
"""

from __future__ import annotations

import gc
import importlib.util
import json
import subprocess
import sys
import unittest
import weakref
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

RESIDUAL_PATH = APP_ROOT / "scripts" / "headless" / "generation_residual.py"
SMOKE_PATH = APP_ROOT / "scripts" / "headless" / "studio_service_smoke.py"

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_RESIDUAL_TESTS = 73

TOTAL_STEPS = 12


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


residual = load("studio_generation_residual", RESIDUAL_PATH)
smoke = load("studio_service_smoke_for_residual_tests", SMOKE_PATH)


def sample(allocated: int, reserved: int) -> dict[str, int]:
    """Build a sample through the REAL producer, never by hand."""

    from forge_headless.load_telemetry import VramSample

    return VramSample(allocated=allocated, reserved=reserved).to_dict()


CLEAN_OWNERSHIP = {
    "all_owned_weakrefs_dead": True,
    "registry_at_pre_load_count": True,
    "shared_sd_model_no_model": True,
    "model_data_sd_model_no_model": True,
}


# ------------------------------------------------------------------ schema


class TelemetrySchemaTests(unittest.TestCase):
    def test_the_real_producer_and_the_classifier_agree_on_field_names(self) -> None:
        from forge_headless.load_telemetry import VramSample

        produced = set(VramSample(allocated=1, reserved=2).to_dict())
        self.assertEqual(set(residual.VRAM_SAMPLE_FIELDS), produced)

    def test_a_real_sample_reads_back_exactly(self) -> None:
        self.assertEqual((1, 2), residual.read_sample(sample(1, 2)))

    def test_the_legacy_spelling_is_refused_not_translated(self) -> None:
        with self.assertRaises(residual.TelemetrySchemaError) as caught:
            residual.read_sample({"allocated": 1, "reserved": 2})
        self.assertIn("legacy spelling", str(caught.exception))

    def test_a_missing_field_is_refused(self) -> None:
        with self.assertRaises(residual.TelemetrySchemaError):
            residual.read_sample({"allocated_bytes": 1})

    def test_a_non_integer_is_refused(self) -> None:
        with self.assertRaises(residual.TelemetrySchemaError):
            residual.read_sample({"allocated_bytes": "1", "reserved_bytes": 2})

    def test_a_bool_is_refused_because_true_is_an_int(self) -> None:
        with self.assertRaises(residual.TelemetrySchemaError):
            residual.read_sample({"allocated_bytes": True, "reserved_bytes": 2})

    def test_a_negative_byte_count_is_refused(self) -> None:
        with self.assertRaises(residual.TelemetrySchemaError):
            residual.read_sample({"allocated_bytes": -1, "reserved_bytes": 2})

    def test_a_non_mapping_is_refused(self) -> None:
        for value in (None, [], "0", 0):
            with self.assertRaises(residual.TelemetrySchemaError):
                residual.read_sample(value)

    def test_there_is_no_minus_one_fallback_anywhere_in_the_module(self) -> None:
        source = RESIDUAL_PATH.read_text(encoding="utf-8")
        self.assertNotIn('.get("allocated"', source)
        self.assertNotIn('.get("reserved"', source)
        self.assertNotIn(", -1)", source)


# ---------------------------------------------------------- classification


class ClassificationMatrixTests(unittest.TestCase):
    def classify(self, allocated: int, reserved: int, ownership=CLEAN_OWNERSHIP):
        return residual.classify(sample(allocated, reserved), ownership)

    def test_absolute_zero(self) -> None:
        self.assertEqual(residual.ABSOLUTE_ZERO, self.classify(0, 0)["classification"])

    def test_the_known_generation_residual_matches_exactly(self) -> None:
        result = self.classify(9_568_256, 23_068_672)
        self.assertEqual(residual.KNOWN_GENERATION_RESIDUAL_MATCH, result["classification"])
        self.assertEqual(0, result["allocated_delta_vs_known"])

    def test_one_byte_off_the_known_residual_is_not_a_match(self) -> None:
        self.assertNotEqual(
            residual.KNOWN_GENERATION_RESIDUAL_MATCH,
            self.classify(9_568_257, 23_068_672)["classification"],
        )

    def test_the_rerun_figures_classify_as_new_or_greater(self) -> None:
        result = self.classify(14_352_384, 25_165_824)
        self.assertEqual(residual.NEW_OR_GREATER_RESIDUAL, result["classification"])
        self.assertEqual(4_784_128, result["allocated_delta_vs_known"])
        self.assertEqual(2_097_152, result["reserved_delta_vs_known"])

    def test_a_smaller_nonzero_residual_has_its_own_classification(self) -> None:
        result = self.classify(1_048_576, 2_097_152)
        self.assertEqual(residual.LOWER_BUT_NONZERO_RESIDUAL, result["classification"])

    def test_malformed_telemetry_cannot_be_classified(self) -> None:
        result = residual.classify({"allocated": 0, "reserved": 0}, CLEAN_OWNERSHIP)
        self.assertEqual(residual.TELEMETRY_SCHEMA_INVALID, result["classification"])
        self.assertFalse(result["classified"])
        self.assertNotIn("allocated_bytes", result)

    def test_ownership_inconsistency_outranks_a_zero_byte_count(self) -> None:
        dirty = dict(CLEAN_OWNERSHIP, all_owned_weakrefs_dead=False)
        result = self.classify(0, 0, dirty)
        self.assertEqual(residual.OWNERSHIP_STATE_INCONSISTENT, result["classification"])

    def test_ownership_inconsistency_outranks_the_known_residual(self) -> None:
        dirty = dict(CLEAN_OWNERSHIP, registry_at_pre_load_count=False)
        result = self.classify(9_568_256, 23_068_672, dirty)
        self.assertEqual(residual.OWNERSHIP_STATE_INCONSISTENT, result["classification"])

    def test_a_missing_ownership_fact_is_not_cleanliness(self) -> None:
        for name in sorted(CLEAN_OWNERSHIP):
            partial = {k: v for k, v in CLEAN_OWNERSHIP.items() if k != name}
            self.assertFalse(residual.ownership_is_clean(partial), name)

    def test_a_truthy_non_true_ownership_value_is_not_accepted(self) -> None:
        self.assertFalse(
            residual.ownership_is_clean(dict(CLEAN_OWNERSHIP, all_owned_weakrefs_dead=1))
        )

    def test_the_first_live_failure_classifies_as_inconsistent_not_by_bytes(self) -> None:
        # 264,719,360 with a live engine weakref: the shape of the first run.
        dirty = dict(CLEAN_OWNERSHIP, all_owned_weakrefs_dead=False,
                     registry_at_pre_load_count=False)
        result = self.classify(264_719_360, 333_447_168, dirty)
        self.assertEqual(residual.OWNERSHIP_STATE_INCONSISTENT, result["classification"])

    def test_every_declared_classification_is_reachable(self) -> None:
        reached = {
            self.classify(0, 0)["classification"],
            self.classify(9_568_256, 23_068_672)["classification"],
            self.classify(14_352_384, 25_165_824)["classification"],
            self.classify(1024, 2048)["classification"],
            residual.classify({"nope": 1}, CLEAN_OWNERSHIP)["classification"],
            self.classify(0, 0, {})["classification"],
        }
        self.assertEqual(set(residual.CLASSIFICATIONS), reached)


# ------------------------------------------------------------- first step


def drive_forge_steps(bridge, total: int = TOTAL_STEPS) -> None:
    """Replay exactly what retained Forge writes, in order.

    `launch_sampling` (`sd_samplers_common.py:435-440`) then `callback_state`
    (`:431-432`) per step, with the zero-based index and its one-based twin.
    """

    bridge.sampling_steps = total
    bridge.sampling_step = 0
    bridge.preview_step = 0
    for index in range(total):
        bridge.sampling_step = index
        bridge.preview_step = index + 1


class FirstStepTruthTests(unittest.TestCase):
    def build(self):
        from forge_headless.headless_progress import ForgeStateBridge, HeadlessProgress

        progress = HeadlessProgress("residual-test", preview_enabled=False)
        bridge = ForgeStateBridge(progress)
        recorder = residual.SamplerStepRecorder()
        recorder.enter_denoiser(TOTAL_STEPS)
        restore = residual.install_step_observers(bridge, progress, recorder)
        return progress, bridge, recorder, restore

    def test_the_first_completed_step_is_one_not_eleven(self) -> None:
        progress, bridge, recorder, restore = self.build()
        drive_forge_steps(bridge)
        restore()
        truth = recorder.to_dict()
        self.assertTrue(truth["first_step_observed"])
        self.assertEqual(1, truth["first_completed_step_index"])
        self.assertEqual(TOTAL_STEPS, truth["first_completed_step_total"])
        self.assertEqual(TOTAL_STEPS, truth["final_completed_steps"])
        self.assertEqual(TOTAL_STEPS, truth["final_total_steps"])

    def test_regression_the_prior_post_hoc_reading_was_eleven(self) -> None:
        progress, bridge, recorder, restore = self.build()
        drive_forge_steps(bridge)
        restore()
        # The defect, reproduced: one read of the counter after the loop.
        post_hoc = int(progress.snapshot().step or 0)
        self.assertEqual(11, post_hoc)
        # The correction, from the same run.
        self.assertEqual(1, recorder.to_dict()["first_completed_step_index"])

    def test_the_zero_based_signal_is_kept_only_as_a_cross_check(self) -> None:
        progress, bridge, recorder, restore = self.build()
        drive_forge_steps(bridge)
        restore()
        truth = recorder.to_dict()
        self.assertEqual(0, truth["zero_based_first_index"])
        self.assertEqual(11, truth["zero_based_last_index"])
        self.assertEqual("one-based, public", truth["index_base"])
        self.assertTrue(truth["consistent"])

    def test_the_pre_loop_reset_is_not_counted_as_a_completed_step(self) -> None:
        progress, bridge, recorder, restore = self.build()
        bridge.sampling_steps = TOTAL_STEPS
        bridge.sampling_step = 0
        bridge.preview_step = 0
        restore()
        truth = recorder.to_dict()
        self.assertFalse(truth["first_step_observed"])
        self.assertIsNone(truth["first_completed_step_index"])

    def test_one_completed_step_is_observed_immediately(self) -> None:
        progress, bridge, recorder, restore = self.build()
        bridge.sampling_steps = TOTAL_STEPS
        bridge.sampling_step = 0
        bridge.preview_step = 0
        bridge.sampling_step = 0
        bridge.preview_step = 1
        self.assertTrue(recorder.first_step_observed)
        self.assertEqual(1, recorder.first_completed_step_index)
        restore()

    def test_the_step_count_is_never_derived_from_the_final_counter(self) -> None:
        truth = residual.SamplerStepRecorder().to_dict()
        self.assertFalse(truth["derived_from_final_counter"])
        self.assertIn("sd_samplers_common.py", truth["translation_source"])

    def test_an_interrupted_run_reports_what_it_saw_and_no_more(self) -> None:
        progress, bridge, recorder, restore = self.build()
        drive_forge_steps(bridge, total=TOTAL_STEPS)
        recorder_total = recorder.to_dict()
        restore()
        partial_progress, partial_bridge, partial, partial_restore = self.build()
        partial_bridge.sampling_steps = TOTAL_STEPS
        for index in range(4):
            partial_bridge.sampling_step = index
            partial_bridge.preview_step = index + 1
        partial_restore()
        truth = partial.to_dict()
        self.assertEqual(1, truth["first_completed_step_index"])
        self.assertEqual(4, truth["final_completed_steps"])
        self.assertEqual(TOTAL_STEPS, truth["final_total_steps"])
        self.assertFalse(truth["consistent"])
        self.assertTrue(recorder_total["consistent"])

    def test_a_step_sink_is_notified_live_for_every_step(self) -> None:
        seen: list[int] = []
        from forge_headless.headless_progress import ForgeStateBridge, HeadlessProgress

        progress = HeadlessProgress("residual-sink", preview_enabled=False)
        bridge = ForgeStateBridge(progress)
        recorder = residual.SamplerStepRecorder(on_step=seen.append)
        recorder.enter_denoiser(TOTAL_STEPS)
        restore = residual.install_step_observers(bridge, progress, recorder)
        drive_forge_steps(bridge)
        restore()
        self.assertEqual(list(range(1, TOTAL_STEPS + 1)), seen)

    def test_a_raising_sink_cannot_break_sampling(self) -> None:
        from forge_headless.headless_progress import ForgeStateBridge, HeadlessProgress

        def explode(_step: int) -> None:
            raise RuntimeError("a reporting sink must never reach the sampler")

        progress = HeadlessProgress("residual-boom", preview_enabled=False)
        bridge = ForgeStateBridge(progress)
        recorder = residual.SamplerStepRecorder(on_step=explode)
        restore = residual.install_step_observers(bridge, progress, recorder)
        drive_forge_steps(bridge)
        restore()
        self.assertEqual(TOTAL_STEPS, recorder.to_dict()["final_completed_steps"])

    def test_restore_puts_both_objects_back(self) -> None:
        from forge_headless.headless_progress import ForgeStateBridge, HeadlessProgress

        progress = HeadlessProgress("residual-restore", preview_enabled=False)
        bridge = ForgeStateBridge(progress)
        original_values_type = type(object.__getattribute__(bridge, "_values"))
        recorder = residual.SamplerStepRecorder()
        restore = residual.install_step_observers(bridge, progress, recorder)
        self.assertNotEqual(
            original_values_type, type(object.__getattribute__(bridge, "_values"))
        )
        report = restore()
        self.assertTrue(report["restored"])
        self.assertEqual(
            original_values_type, type(object.__getattribute__(bridge, "_values"))
        )
        self.assertNotIn("report_step", vars(progress))

    def test_restore_is_idempotent(self) -> None:
        progress, bridge, recorder, restore = self.build()
        restore()
        self.assertTrue(restore()["already"])

    def test_observers_survive_a_bridge_that_cannot_be_watched(self) -> None:
        recorder = residual.SamplerStepRecorder()
        restore = residual.install_step_observers(None, None, recorder)
        self.assertTrue(restore()["restored"])

    def test_the_tracked_runner_no_longer_reads_the_counter_after_the_loop(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("int(progress.snapshot().step or 0)", source)
        self.assertIn("install_step_observers", source)

    def test_the_observers_are_restored_even_if_the_request_fails_to_build(self) -> None:
        """The construction must be inside the try, not before it."""

        import ast

        tree = ast.parse(SMOKE_PATH.read_text(encoding="utf-8"))
        generate = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "generate"
        )
        tries = [node for node in generate.body if isinstance(node, ast.Try)]
        self.assertEqual(1, len(tries))
        guarded = ast.dump(ast.Module(body=tries[0].body, type_ignores=[]))
        self.assertIn("StableDiffusionProcessingTxt2Img", guarded)
        self.assertIn("process_images_inner", guarded)
        finalizer = ast.dump(ast.Module(body=tries[0].finalbody, type_ignores=[]))
        self.assertIn("restore_observers", finalizer)
        self.assertIn("record_step_truth", finalizer)

    def test_the_lifecycle_recorder_carries_all_six_required_fields(self) -> None:
        for name in (
            "denoiser_entered",
            "first_step_observed",
            "first_completed_step_index",
            "first_completed_step_total",
            "final_completed_steps",
            "final_total_steps",
        ):
            self.assertIn(name, smoke.LIFECYCLE_FIELDS, name)

    def test_the_lifecycle_recorder_takes_the_six_facts_from_the_step_recorder(self) -> None:
        progress, bridge, recorder, restore = self.build()
        drive_forge_steps(bridge)
        restore()
        lifecycle = smoke.LifecycleRecorder()
        lifecycle.record_step_truth(recorder)
        facts = lifecycle.to_dict()["facts"]
        self.assertEqual(1, facts["first_completed_step_index"]["value"])
        self.assertEqual(1, facts["first_completed_sampler_step"]["value"])
        self.assertEqual(TOTAL_STEPS, facts["final_completed_steps"]["value"])
        self.assertEqual(TOTAL_STEPS, facts["final_total_steps"]["value"])
        self.assertTrue(facts["first_step_observed"]["value"])

    def test_unobserved_steps_are_named_unavailable_not_zero(self) -> None:
        lifecycle = smoke.LifecycleRecorder()
        lifecycle.record_step_truth(residual.SamplerStepRecorder())
        facts = lifecycle.to_dict()["facts"]
        for name in ("first_completed_step_index", "final_completed_steps"):
            self.assertEqual("unavailable", facts[name]["value"])
            self.assertIn("reason", facts[name])


# --------------------------------------------------------- stage recorder


class FakeAllocator:
    """Bytes fall only when the object they were charged to is collected."""

    def __init__(self) -> None:
        self._live: dict[int, int] = {}
        self._keep: list = []

    def charge(self, obj: object, nbytes: int) -> object:
        key = id(obj)
        self._live[key] = self._live.get(key, 0) + nbytes
        self._keep.append(weakref.finalize(obj, self._live.pop, key, None))
        return obj

    def allocated(self) -> int:
        gc.collect()
        return sum(self._live.values())

    def snapshot(self):
        total = self.allocated()
        return {"allocated_bytes": total, "reserved_bytes": total}


class Tensorish:
    """A stand-in for a device tensor. Weak-referenceable, like the real ones."""

    def __init__(self, name: str) -> None:
        self.name = name


class StageRecorderTests(unittest.TestCase):
    def test_it_is_inert_without_a_sampler(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        self.assertFalse(recorder.active)
        snapshot = recorder.record(residual.S0)
        self.assertFalse(snapshot["available"])
        self.assertNotIn("allocated_bytes", snapshot)

    def test_all_sixteen_stages_are_declared(self) -> None:
        self.assertEqual(16, len(residual.STAGES))
        self.assertEqual(residual.S0, residual.STAGES[0])
        self.assertEqual(residual.S15, residual.STAGES[-1])

    def test_snapshots_are_ordered_and_sequenced(self) -> None:
        allocator = FakeAllocator()
        recorder = residual.StageSnapshotRecorder(sampler=allocator.snapshot)
        for stage in residual.STAGES:
            recorder.record(stage)
        report = recorder.to_dict()
        self.assertEqual(list(residual.STAGES), report["recorded"])
        self.assertEqual(
            list(range(1, 17)), [s["sequence"] for s in report["stages"]]
        )
        self.assertTrue(report["sequence_is_monotonic"])
        self.assertEqual([], report["missing"])

    def test_a_stage_cannot_be_recorded_twice(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        recorder.record(residual.S0)
        with self.assertRaises(KeyError):
            recorder.record(residual.S0)

    def test_an_unknown_stage_is_refused(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        with self.assertRaises(KeyError):
            recorder.record("S99_invented")

    def test_an_unavailable_stage_carries_a_reason_and_no_bytes(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        recorder.unavailable(residual.S2, "retained code emits no signal")
        entry = recorder.to_dict()["stages"][0]
        self.assertFalse(entry["available"])
        self.assertIn("reason", entry)
        self.assertNotIn("allocated_bytes", entry)

    def test_missing_stages_are_listed_rather_than_invented(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        recorder.record(residual.S0)
        report = recorder.to_dict()
        self.assertEqual([residual.S0], report["recorded"])
        self.assertEqual(15, len(report["missing"]))

    def test_the_job_id_is_an_opaque_scalar(self) -> None:
        recorder = residual.StageSnapshotRecorder(job_id="headless-000001")
        entry = recorder.record(residual.S0)
        self.assertNotIn("headless", entry["job"])
        self.assertEqual(12, len(entry["job"]))

    def test_ownership_facts_are_scalars_only(self) -> None:
        recorder = residual.StageSnapshotRecorder()
        entry = recorder.record(
            residual.S12,
            ownership={"registry": 0, "engine": Tensorish("engine"), "clean": True},
        )
        self.assertEqual({"clean": True, "registry": 0}, entry["ownership"])

    def test_a_malformed_sampler_reading_is_refused_not_stored(self) -> None:
        recorder = residual.StageSnapshotRecorder(
            sampler=lambda: {"allocated": 1, "reserved": 2}
        )
        with self.assertRaises(residual.TelemetrySchemaError):
            recorder.record(residual.S0)

    def test_deltas_are_between_observed_stages_only(self) -> None:
        allocator = FakeAllocator()
        recorder = residual.StageSnapshotRecorder(sampler=allocator.snapshot)
        recorder.record(residual.S0)
        recorder.unavailable(residual.S1, "not observed")
        held = allocator.charge(Tensorish("latent"), 1024)
        recorder.record(residual.S5)
        deltas = recorder.to_dict()["deltas"]
        self.assertEqual(1, len(deltas))
        self.assertEqual(residual.S0, deltas[0]["from"])
        self.assertEqual(residual.S5, deltas[0]["to"])
        self.assertEqual(1024, deltas[0]["allocated_delta"])
        del held

    def test_the_first_retaining_stage_is_identified(self) -> None:
        allocator = FakeAllocator()
        recorder = residual.StageSnapshotRecorder(sampler=allocator.snapshot)
        recorder.record(residual.S0)
        recorder.record(residual.S1)
        keep = allocator.charge(Tensorish("conditioning"), 4096)
        recorder.record(residual.S3)
        recorder.record(residual.S5)
        self.assertEqual(residual.S3, recorder.to_dict()["first_retaining_stage"])
        del keep

    def test_no_retaining_stage_when_nothing_rises(self) -> None:
        allocator = FakeAllocator()
        recorder = residual.StageSnapshotRecorder(sampler=allocator.snapshot)
        recorder.record(residual.S0)
        recorder.record(residual.S14)
        self.assertIsNone(recorder.to_dict()["first_retaining_stage"])


# ---------------------------------------------------------- liveness model


class GenerationOwners:
    """The six synthetic generation-only owners the census must tell apart."""

    def __init__(self, allocator: FakeAllocator) -> None:
        self.conditioning_cache: list = []
        self.sampler_closure = None
        self.callback_parameter = None
        self.latent_holder = None
        self.decode_intermediate = None
        self.result_metadata: dict = {}
        self._allocator = allocator

    def run_job(self, index: int, *, leak: bool) -> dict:
        allocator = self._allocator
        # A fixed reusable cache: charged once, reused every cycle.
        if not self.conditioning_cache:
            self.conditioning_cache.append(
                allocator.charge(Tensorish("conditioning"), 8_000_000)
            )
        latent = allocator.charge(Tensorish(f"latent-{index}"), 2_000_000)
        self.latent_holder = latent
        decode = allocator.charge(Tensorish(f"decode-{index}"), 1_000_000)
        self.decode_intermediate = decode
        parameter = allocator.charge(Tensorish(f"callback-{index}"), 500_000)
        self.callback_parameter = parameter

        def closure(_x=latent):
            return None

        self.sampler_closure = closure
        self.result_metadata = {"job": index, "bytes": 754_452, "mime": "image/png"}
        if leak:
            # A per-job holder nothing ever clears.
            self.conditioning_cache.append(
                allocator.charge(Tensorish(f"leaked-{index}"), 3_000_000)
            )
        return self.result_metadata

    def release_generation_objects(self) -> None:
        self.latent_holder = None
        self.decode_intermediate = None
        self.callback_parameter = None
        self.sampler_closure = None

    def release_model_and_session_only(self) -> None:
        """What model/session teardown reaches. Deliberately not the caches."""


class LivenessModelTests(unittest.TestCase):
    def cycles(self, *, leak: bool) -> list[int]:
        allocator = FakeAllocator()
        owners = GenerationOwners(allocator)
        after = []
        for index in range(3):
            owners.run_job(index, leak=leak)
            owners.release_generation_objects()
            owners.release_model_and_session_only()
            after.append(allocator.allocated())
        return after

    def test_a_fixed_reusable_cache_produces_a_stable_plateau(self) -> None:
        after = self.cycles(leak=False)
        self.assertEqual(8_000_000, after[0])
        self.assertEqual([after[0]] * 3, after)

    def test_a_leaked_per_job_holder_produces_monotonic_growth(self) -> None:
        after = self.cycles(leak=True)
        self.assertEqual(11_000_000, after[0])
        self.assertLess(after[0], after[1])
        self.assertLess(after[1], after[2])
        self.assertEqual(3_000_000, after[1] - after[0])

    def test_the_two_shapes_are_distinguishable_from_the_deltas_alone(self) -> None:
        flat = self.cycles(leak=False)
        growing = self.cycles(leak=True)
        self.assertEqual({0}, {b - a for a, b in zip(flat, flat[1:])})
        self.assertEqual({3_000_000}, {b - a for a, b in zip(growing, growing[1:])})

    def test_clearing_model_and_session_cannot_clear_a_generation_holder(self) -> None:
        allocator = FakeAllocator()
        owners = GenerationOwners(allocator)
        owners.run_job(0, leak=False)
        owners.release_generation_objects()
        owners.release_model_and_session_only()
        self.assertEqual(8_000_000, allocator.allocated())
        # Only clearing the generation-only cache itself moves it.
        owners.conditioning_cache.clear()
        self.assertEqual(0, allocator.allocated())

    def test_the_allocator_is_driven_by_liveness_not_a_constant(self) -> None:
        allocator = FakeAllocator()
        held = allocator.charge(Tensorish("residual"), 14_352_384)
        self.assertEqual(14_352_384, allocator.allocated())
        del held
        self.assertEqual(0, allocator.allocated())

    def test_the_census_names_the_stage_where_bytes_first_appear(self) -> None:
        allocator = FakeAllocator()
        owners = GenerationOwners(allocator)
        recorder = residual.StageSnapshotRecorder(sampler=allocator.snapshot)
        recorder.record(residual.S0)
        recorder.record(residual.S1)
        recorder.record(residual.S2)
        owners.run_job(0, leak=False)
        recorder.record(residual.S3)
        recorder.record(residual.S7)
        owners.release_generation_objects()
        recorder.record(residual.S10)
        self.assertEqual(residual.S3, recorder.to_dict()["first_retaining_stage"])

    def test_three_cycles_of_growth_classify_differently_each_time(self) -> None:
        after = self.cycles(leak=True)
        verdicts = [
            residual.classify(
                {"allocated_bytes": value, "reserved_bytes": value},
                CLEAN_OWNERSHIP,
            )["classification"]
            for value in after
        ]
        self.assertEqual([residual.NEW_OR_GREATER_RESIDUAL] * 3, verdicts)
        self.assertEqual(3, len(set(after)))


class ResultDurabilityTests(unittest.TestCase):
    def test_result_metadata_outlives_every_generation_tensor(self) -> None:
        allocator = FakeAllocator()
        owners = GenerationOwners(allocator)
        result = owners.run_job(0, leak=False)
        refs = [
            weakref.ref(owners.latent_holder),
            weakref.ref(owners.decode_intermediate),
            weakref.ref(owners.callback_parameter),
        ]
        owners.release_generation_objects()
        owners.conditioning_cache.clear()
        gc.collect()
        self.assertEqual([None, None, None], [ref() for ref in refs])
        self.assertEqual(0, allocator.allocated())
        self.assertEqual(754_452, result["bytes"])
        self.assertEqual("image/png", result["mime"])

    def test_the_result_holds_no_tensor_reference(self) -> None:
        allocator = FakeAllocator()
        owners = GenerationOwners(allocator)
        result = owners.run_job(0, leak=False)
        self.assertTrue(all(
            isinstance(value, (int, str)) for value in result.values()
        ))


# ------------------------------------------------------------ import safety


PROBE = (
    "import importlib.util, json, sys\n"
    "spec = importlib.util.spec_from_file_location('r', r'{path}')\n"
    "m = importlib.util.module_from_spec(spec)\n"
    "sys.modules['r'] = m\n"
    "spec.loader.exec_module(m)\n"
    "m.classify({{'allocated_bytes': 0, 'reserved_bytes': 0}}, {{}})\n"
    "m.StageSnapshotRecorder().record(m.S0)\n"
    "m.SamplerStepRecorder().to_dict()\n"
    "names = set(sys.modules)\n"
    "print(json.dumps({{\n"
    "  'torch': int(any(n == 'torch' or n.startswith('torch.') for n in names)),\n"
    "  'cuda': int(any('cuda' in n for n in names)),\n"
    "  'forge_backend': int(any(n == 'backend' or n.startswith('backend.') for n in names)),\n"
    "  'neo': int(any(n.split('.')[0] in ('modules', 'modules_forge', 'webui') for n in names)),\n"
    "  'gradio': int(any(n.split('.')[0] in ('gradio', 'gradio_client') for n in names)),\n"
    "  'socketserver': int('socketserver' in names),\n"
    "}}))\n"
)


class ImportSafetyTests(unittest.TestCase):
    def probe(self) -> dict:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", "-c", PROBE.format(path=RESIDUAL_PATH)],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_a_fresh_import_and_use_pulls_in_nothing_heavy(self) -> None:
        report = self.probe()
        for key in ("torch", "cuda", "forge_backend", "neo", "gradio", "socketserver"):
            self.assertEqual(0, report[key], f"{key} was imported")

    def test_the_module_defers_every_heavy_import(self) -> None:
        import ast

        tree = ast.parse(RESIDUAL_PATH.read_text(encoding="utf-8"))
        forbidden = {
            "torch", "backend", "modules", "modules_forge", "webui", "gradio",
            "forge_studio", "forge_headless",
        }
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden)

    def test_the_module_embeds_no_private_path(self) -> None:
        source = RESIDUAL_PATH.read_text(encoding="utf-8")
        for leak in ("Private-Local", ".safetensors", "C:\\Users", "/Users/"):
            self.assertNotIn(leak, source)

    def test_no_product_module_imports_the_diagnostic(self) -> None:
        for package in ("forge_studio", "forge_headless"):
            for path in (APP_ROOT / package).rglob("*.py"):
                self.assertNotIn(
                    "generation_residual", path.read_text(encoding="utf-8"), str(path)
                )


PROBE_PATH = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"
PROCESSING_PATH = APP_ROOT / "modules" / "processing.py"


class TrackedRunnerReleaseGapTests(unittest.TestCase):
    """The leading candidate for the residual delta, pinned as source facts.

    Attempts 05/06 and the cleanup-only diagnostic ran through
    `first_image_probe`, which calls the shipped
    `release_generation_references`. The live-smoke rerun ran through the
    tracked runner, which does not. The two runs ended at different residuals.
    That is a difference in cleanup, not a proven owner -- these tests pin the
    difference so it cannot drift unnoticed while it is being investigated.
    """

    def test_the_shipped_release_clears_the_class_level_conditioning_caches(self) -> None:
        from forge_headless.failure_cleanup import _CLASS_LIST_CACHES

        for name in ("cached_c", "cached_uc", "cached_hr_c", "cached_hr_uc"):
            self.assertIn(name, _CLASS_LIST_CACHES)

    def test_the_conditioning_caches_are_class_attributes_in_retained_source(self) -> None:
        source = PROCESSING_PATH.read_text(encoding="utf-8")
        self.assertIn("    cached_uc = [None, None, None]", source)
        self.assertIn("    cached_c = [None, None, None]", source)

    def test_close_skips_the_prompt_cache_while_the_option_is_on(self) -> None:
        source = PROCESSING_PATH.read_text(encoding="utf-8")
        close_at = source.index("    def close(self):")
        body = source[close_at:close_at + 400]
        self.assertIn("if not opts.persistent_cond_cache:", body)
        self.assertIn("clear_prompt_cache()", body)

    def test_both_runners_now_call_the_shipped_generation_release(self) -> None:
        """The gap this test used to pin open is closed.

        It previously asserted the tracked runner did NOT call the seam, and
        said to re-measure before editing it. The three-cycle live run did
        exactly that: the seam freed 4,784,640 bytes on every one of three jobs,
        against 4,784,128 predicted from source. So the assertion is inverted
        rather than deleted -- the runner must now call it.
        """

        probe = PROBE_PATH.read_text(encoding="utf-8")
        runner = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertIn("release_generation_references(", probe)
        self.assertIn("release_generation_references(", runner)

    def test_the_tracked_runner_calls_the_seam_from_exactly_one_place(self) -> None:
        """One call site, so "exactly once per job" is structural."""

        import ast

        tree = ast.parse(SMOKE_PATH.read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "release_generation_references"
        ]
        self.assertEqual(1, len(calls))

    def test_the_tracked_runner_never_clears_the_caches_itself(self) -> None:
        """Only the shipped seam may clear them."""

        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("clear_prompt_cache", source)
        for attribute in ("cached_c", "cached_uc", "cached_hr_c", "cached_hr_uc"):
            self.assertNotIn(f"{attribute} =", source)
            self.assertNotIn(f'setattr(klass, "{attribute}"', source)

    def test_a_class_level_cache_survives_dropping_every_instance(self) -> None:
        allocator = FakeAllocator()

        class Processingish:
            cached_c: list = []

            def __init__(self) -> None:
                # The shape of `__post_init__`: the instance points at the class list.
                self.cached_c = Processingish.cached_c

            def close(self, persistent: bool = True) -> None:
                if not persistent:
                    Processingish.cached_c = []
                    self.cached_c = []

        instance = Processingish()
        instance.cached_c.append(allocator.charge(Tensorish("conditioning"), 4_784_128))
        instance.close(persistent=True)
        del instance
        gc.collect()
        self.assertEqual(
            4_784_128, allocator.allocated(),
            "dropping the instance cannot release a class-level cache",
        )
        Processingish.cached_c.clear()
        self.assertEqual(0, allocator.allocated())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_RESIDUAL_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("STATIC_IMPORT_SCOPE", __doc__ or "")
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
