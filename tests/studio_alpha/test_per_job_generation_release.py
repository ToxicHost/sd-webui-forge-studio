"""Contracts for per-job generation release and engine-weakref ownership.

The three-cycle live run proved the tracked Studio path omitted the shipped
`release_generation_references` seam from per-job cleanup, and measured what
that cost: 4,784,640 bytes retained per job, cleared by one call to a function
that already ships and is already tested.

This suite pins the integration:

* the seam is called exactly once per job, from one call site, on every path;
* the class-level conditioning caches are cleared **only** through that seam;
* results stay resolvable and the warm session stays reusable afterwards;
* owned weakrefs are sampled only after every release is marked, so a report
  can no longer be taken from a moment when a diagnostic local was still alive.

No socket is bound here, so the suite runs identically under the canonical
runner and under discovery.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no generation, no server.
"""

from __future__ import annotations

import ast
import gc
import importlib.util
import sys
import unittest
import weakref
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SMOKE_PATH = APP_ROOT / "scripts" / "headless" / "studio_service_smoke.py"
RESIDUAL_PATH = APP_ROOT / "scripts" / "headless" / "generation_residual.py"

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_RELEASE_TESTS = 50


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = load("studio_service_smoke_for_release_tests", SMOKE_PATH)
residual = load("studio_generation_residual", RESIDUAL_PATH)


class Sentinel:
    """Weak-referenceable stand-in for an engine or a component."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeAllocator:
    """Bytes fall only when the object they were charged to is collected."""

    def __init__(self) -> None:
        self._live: dict[int, int] = {}
        self._keep: list = []

    def charge(self, obj, nbytes):
        key = id(obj)
        self._live[key] = self._live.get(key, 0) + nbytes
        self._keep.append(weakref.finalize(obj, self._live.pop, key, None))
        return obj

    def allocated(self) -> int:
        gc.collect()
        return sum(self._live.values())


class RecordingSeam:
    """Stands in for the shipped seam so calls can be counted."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("a cleanup seam that raises must be reported")

        class _Report:
            @staticmethod
            def to_dict():
                return {"class_level_caches_cleared": ["cached_c", "cached_uc"]}

        return _Report()


class SyntheticPort:
    """A tracked-port-shaped double. Holds what the seam needs to reach."""

    def __init__(self, engine=None, *, release_raises: bool = False) -> None:
        self._engine = engine
        self._bridge = Sentinel("bridge")
        self.processing_request = Sentinel("processing")
        self.processed = Sentinel("processed")
        self.terminal = False
        self.engine_released = False
        self._release_raises = release_raises

    def release_engine(self):
        if self._release_raises:
            raise RuntimeError("release failed")
        self._engine = None
        self._bridge = None
        self.processing_request = None
        self.processed = None
        self.engine_released = True
        self.terminal = True
        return {"engine_reference_cleared": True, "terminal": True}


class SyntheticSession:
    def __init__(self, *, close_raises: bool = False) -> None:
        self.closed = False
        self._close_raises = close_raises

    def close(self):
        if self._close_raises:
            raise RuntimeError("close failed")
        self.closed = True


def run_cleanup(port, session=None, *, seam=None):
    """Drive the tracked `_cleanup` with the seam swapped for a recorder."""

    import forge_headless.failure_cleanup as failure_cleanup

    seam = seam or RecordingSeam()
    original = failure_cleanup.release_generation_references
    failure_cleanup.release_generation_references = seam
    try:
        report = smoke._cleanup(  # noqa: SLF001
            port, session or SyntheticSession(), None,
            smoke.LifecycleRecorder(), smoke.CacheClearAccounting(),
        )
    finally:
        failure_cleanup.release_generation_references = original
    return report, seam


# ------------------------------------------------------------ the seam call


class ReleaseSeamCallTests(unittest.TestCase):
    def test_the_seam_is_called_exactly_once_on_a_successful_job(self) -> None:
        report, seam = run_cleanup(SyntheticPort(Sentinel("engine")))
        self.assertEqual(1, len(seam.calls))
        self.assertTrue(report["generation_release"]["called"])

    def test_the_seam_receives_the_processing_request_and_processed_result(self) -> None:
        port = SyntheticPort(Sentinel("engine"))
        processing, processed = port.processing_request, port.processed
        _report, seam = run_cleanup(port)
        self.assertIs(processing, seam.calls[0]["processing"])
        self.assertIs(processed, seam.calls[0]["processed"])

    def test_the_seam_receives_the_state_bridge(self) -> None:
        port = SyntheticPort(Sentinel("engine"))
        bridge = port._bridge  # noqa: SLF001
        _report, seam = run_cleanup(port)
        self.assertIs(bridge, seam.calls[0]["state_bridge"])

    def test_the_seam_is_called_before_the_port_drops_the_request(self) -> None:
        """It needs the processing object; releasing the engine first loses it."""

        port = SyntheticPort(Sentinel("engine"))
        _report, seam = run_cleanup(port)
        self.assertIsNotNone(seam.calls[0]["processing"])
        self.assertTrue(port.engine_released)

    def test_no_exception_is_passed_because_cleanup_is_not_a_failure_path(self) -> None:
        _report, seam = run_cleanup(SyntheticPort(Sentinel("engine")))
        self.assertIsNone(seam.calls[0]["exception"])
        self.assertIsNone(seam.calls[0]["outcome"])

    def test_a_raising_seam_is_reported_not_propagated(self) -> None:
        report, seam = run_cleanup(
            SyntheticPort(Sentinel("engine")), seam=RecordingSeam(raises=True)
        )
        self.assertEqual(1, len(seam.calls))
        self.assertEqual("RuntimeError", report["generation_release"]["error"])
        self.assertIn("port_release", report)

    def test_the_seam_still_runs_when_the_port_release_raises(self) -> None:
        port = SyntheticPort(Sentinel("engine"), release_raises=True)
        report, seam = run_cleanup(port)
        self.assertEqual(1, len(seam.calls))
        self.assertEqual("RuntimeError", report["port_release"]["error"])

    def test_the_seam_still_runs_when_the_session_close_raises(self) -> None:
        report, seam = run_cleanup(
            SyntheticPort(Sentinel("engine")), SyntheticSession(close_raises=True)
        )
        self.assertEqual(1, len(seam.calls))
        self.assertFalse(report["session_closed"])

    def test_a_port_with_nothing_to_release_still_calls_the_seam_once(self) -> None:
        port = SyntheticPort(None)
        port.processing_request = None
        port.processed = None
        _report, seam = run_cleanup(port)
        self.assertEqual(1, len(seam.calls))
        self.assertIsNone(seam.calls[0]["processing"])

    def test_cleanup_never_raises_whatever_fails(self) -> None:
        port = SyntheticPort(Sentinel("engine"), release_raises=True)
        report, _seam = run_cleanup(
            port, SyntheticSession(close_raises=True), seam=RecordingSeam(raises=True)
        )
        self.assertIn("gc_collected", report)


class SeamCallSiteTests(unittest.TestCase):
    """Structural proof of "exactly once per job", not just per _cleanup call."""

    def tree(self):
        return ast.parse(SMOKE_PATH.read_text(encoding="utf-8"))

    def test_the_seam_is_called_from_exactly_one_place(self) -> None:
        calls = [
            node for node in ast.walk(self.tree())
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "release_generation_references"
        ]
        self.assertEqual(1, len(calls))

    def test_that_call_lives_in_the_cleanup_helper(self) -> None:
        holder = [
            node.name for node in ast.walk(self.tree())
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Call)
                and getattr(inner.func, "id", "") == "release_generation_references"
                for inner in ast.walk(node)
            )
        ]
        self.assertEqual(["_release_generation_references"], holder)

    def test_cleanup_is_invoked_from_exactly_two_guarded_places(self) -> None:
        """Success path and exception path, the second guarded so it cannot double."""

        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertEqual(2, source.count("report.cleanup = _cleanup("))
        self.assertIn("if not report.cleanup:", source)

    def test_the_helper_is_called_once_from_cleanup(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertEqual(1, source.count("_release_generation_references(port)"))


# ------------------------------------------------------------ cache clearing


class ConditioningCacheTests(unittest.TestCase):
    def test_the_runner_never_clears_the_caches_itself(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("clear_prompt_cache", source)
        for attribute in ("cached_c", "cached_uc", "cached_hr_c", "cached_hr_uc"):
            self.assertNotIn(f"{attribute} = [", source)
            self.assertNotIn(f"setattr(klass, \"{attribute}\"", source)

    def test_the_runner_only_reads_populated_counts(self) -> None:
        state = smoke._conditioning_cache_state()  # noqa: SLF001
        if state.get("available"):
            for _, name in smoke._CONDITIONING_CACHES:  # noqa: SLF001
                entry = state[name]
                self.assertLessEqual(set(entry), {"present", "slots", "populated"})
        else:
            self.assertIn("reason", state)

    def test_an_unreadable_cache_reports_unavailable_not_success(self) -> None:
        verdict = smoke._caches_cleared(  # noqa: SLF001
            {"available": False, "reason": "no modules"}, {"available": False}
        )
        self.assertEqual("unavailable", verdict)

    def test_cleared_is_true_only_when_something_was_populated_and_is_now_empty(self) -> None:
        before = {"available": True, "cached_c": {"populated": 3},
                  "cached_uc": {"populated": 3}, "cached_hr_c": {"populated": 0},
                  "cached_hr_uc": {"populated": 0}}
        after = {"available": True, "cached_c": {"populated": 0},
                 "cached_uc": {"populated": 0}, "cached_hr_c": {"populated": 0},
                 "cached_hr_uc": {"populated": 0}}
        self.assertTrue(smoke._caches_cleared(before, after))  # noqa: SLF001

    def test_still_populated_afterwards_is_false_not_unavailable(self) -> None:
        before = {"available": True, "cached_c": {"populated": 3},
                  "cached_uc": {"populated": 3}, "cached_hr_c": {"populated": 0},
                  "cached_hr_uc": {"populated": 0}}
        self.assertFalse(smoke._caches_cleared(before, before))  # noqa: SLF001

    def test_nothing_populated_beforehand_is_unavailable_not_true(self) -> None:
        empty = {"available": True, "cached_c": {"populated": 0},
                 "cached_uc": {"populated": 0}, "cached_hr_c": {"populated": 0},
                 "cached_hr_uc": {"populated": 0}}
        self.assertEqual("unavailable", smoke._caches_cleared(empty, empty))  # noqa: SLF001

    def test_cleanup_reports_the_cache_state_on_both_sides(self) -> None:
        report, _seam = run_cleanup(SyntheticPort(Sentinel("engine")))
        self.assertIn("conditioning_cache_before", report)
        self.assertIn("conditioning_cache_after", report)
        self.assertIn("conditioning_caches_cleared", report)

    def test_reading_the_cache_state_never_triggers_an_import(self) -> None:
        """Reporting must not import `modules.processing` as a side effect.

        Under a runner whose argv Neo's argparse rejects, that import raises
        SystemExit and dumps the whole CLI help to stderr. An earlier version of
        this integration did exactly that, once per call, and produced about
        700 KB of noise in one discovery run before the check below existed.
        """

        source = SMOKE_PATH.read_text(encoding="utf-8")
        body = source.split("def _conditioning_cache_state(")[1].split("\ndef ")[0]
        self.assertNotIn("from modules import", body)
        self.assertNotIn("import modules", body)
        self.assertIn('sys.modules.get("modules.processing")', body)

    def test_an_unimported_processing_module_reports_unavailable(self) -> None:
        previous = sys.modules.pop("modules.processing", None)
        try:
            state = smoke._conditioning_cache_state()  # noqa: SLF001
            self.assertFalse(state["available"])
            self.assertIn("not imported", state["reason"])
        finally:
            if previous is not None:
                sys.modules["modules.processing"] = previous

    def test_the_shipped_seam_owns_all_four_cache_names(self) -> None:
        from forge_headless.failure_cleanup import _CLASS_LIST_CACHES

        for _, name in smoke._CONDITIONING_CACHES:  # noqa: SLF001
            self.assertIn(name, _CLASS_LIST_CACHES)


# ---------------------------------------------------- durability and warmth


class DurabilityAndWarmSessionTests(unittest.TestCase):
    def test_a_published_result_survives_the_release(self) -> None:
        """The registry holds a path and a media type, not generation objects."""

        import tempfile

        from forge_studio.result_delivery import ResultRegistry

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "result.png"
            target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
            registry = ResultRegistry(root)
            asset = registry.register(target, media_type="image/png")
            port = SyntheticPort(Sentinel("engine"))
            run_cleanup(port)
            gc.collect()
            self.assertEqual(target.read_bytes(), registry.read(asset.handle).content)

    def test_the_warm_engine_is_not_released_by_the_seam(self) -> None:
        engine = Sentinel("engine")
        reference = weakref.ref(engine)
        port = SyntheticPort(engine)
        run_cleanup(port)
        gc.collect()
        self.assertIsNotNone(reference(), "per-job cleanup must not unload the model")

    def test_no_model_unload_is_attempted_per_job(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        for forbidden in ("unload_all_models", "set_sd_model(None)",
                          "detach_model_identity", "forge_model_unload"):
            self.assertNotIn(forbidden, source)

    def test_a_second_job_can_reuse_the_same_engine_after_cleanup(self) -> None:
        engine = Sentinel("engine")
        for _ in range(3):
            port = SyntheticPort(engine)
            report, seam = run_cleanup(port)
            self.assertEqual(1, len(seam.calls))
            self.assertTrue(port.engine_released)
        gc.collect()
        self.assertIsNotNone(weakref.ref(engine)())

    def test_per_job_generation_objects_die_while_the_engine_lives(self) -> None:
        allocator = FakeAllocator()
        engine = allocator.charge(Sentinel("engine"), 4_000_000_000)
        port = SyntheticPort(engine)
        allocator.charge(port.processing_request, 4_784_640)
        references = [weakref.ref(port.processing_request), weakref.ref(port.processed)]
        run_cleanup(port)
        del port
        gc.collect()
        self.assertEqual([None, None], [reference() for reference in references])
        self.assertEqual(4_000_000_000, allocator.allocated())


# ------------------------------------------------------- ownership sampling


class OwnershipSamplingTests(unittest.TestCase):
    def sampler_with_engine(self):
        engine = Sentinel("engine")
        sampler = residual.OwnershipSampler()
        sampler.track("engine", engine)
        return sampler, engine

    def test_sampling_is_refused_until_every_release_is_marked(self) -> None:
        sampler, _engine = self.sampler_with_engine()
        result = sampler.sample()
        self.assertFalse(result["sampled"])
        self.assertEqual(residual.NOT_READY, result["status"])
        self.assertEqual(list(residual.REQUIRED_RELEASES), result["outstanding"])

    def test_a_partially_marked_sampler_still_refuses(self) -> None:
        sampler, _engine = self.sampler_with_engine()
        sampler.mark("port_engine_cleared")
        sampler.mark("session_closed")
        self.assertFalse(sampler.ready)
        self.assertEqual(["gateway_released", "diagnostic_locals_released"],
                         sampler.outstanding)

    def test_an_unknown_release_name_is_refused(self) -> None:
        sampler, _engine = self.sampler_with_engine()
        with self.assertRaises(KeyError):
            sampler.mark("something_else")

    def test_the_engine_weakref_is_alive_while_a_diagnostic_local_holds_it(self) -> None:
        """The live run's exact shape, reproduced."""

        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        result = sampler.sample()
        self.assertTrue(result["sampled"])
        self.assertEqual(["engine"], result["alive"])
        self.assertFalse(result["all_owned_weakrefs_dead"])
        del engine

    def test_the_engine_weakref_dies_once_the_local_is_dropped(self) -> None:
        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        del engine
        result = sampler.sample()
        self.assertEqual([], result["alive"])
        self.assertEqual(["engine"], result["dead"])
        self.assertTrue(result["all_owned_weakrefs_dead"])

    def test_a_report_building_closure_keeps_it_alive_too(self) -> None:
        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)

        def build_report(_captured=engine):
            return {"engine_class": type(_captured).__name__}

        del engine
        self.assertFalse(sampler.sample()["all_owned_weakrefs_dead"])
        del build_report
        self.assertTrue(sampler.sample()["all_owned_weakrefs_dead"])

    def test_a_retained_local_classifies_as_ownership_inconsistent(self) -> None:
        """The negative control: strict classification is not weakened."""

        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        facts = sampler.ownership_facts(
            registry_at_pre_load_count=True,
            shared_sd_model_no_model=True,
            model_data_sd_model_no_model=True,
        )
        verdict = residual.classify(
            {"allocated_bytes": 0, "reserved_bytes": 0}, facts
        )
        self.assertEqual(residual.OWNERSHIP_STATE_INCONSISTENT,
                         verdict["classification"])
        del engine

    def test_the_same_facts_classify_cleanly_once_the_local_is_gone(self) -> None:
        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        del engine
        facts = sampler.ownership_facts(
            registry_at_pre_load_count=True,
            shared_sd_model_no_model=True,
            model_data_sd_model_no_model=True,
        )
        verdict = residual.classify(
            {"allocated_bytes": 0, "reserved_bytes": 0}, facts
        )
        self.assertEqual(residual.ABSOLUTE_ZERO, verdict["classification"])

    def test_the_caller_cannot_assert_weakref_deadness_itself(self) -> None:
        """`ownership_facts` overwrites the caller's claim with the measurement."""

        sampler, engine = self.sampler_with_engine()
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        facts = sampler.ownership_facts(all_owned_weakrefs_dead=True)
        self.assertFalse(facts["all_owned_weakrefs_dead"])
        del engine

    def test_an_unready_sampler_yields_facts_that_cannot_be_clean(self) -> None:
        sampler, engine = self.sampler_with_engine()
        del engine
        facts = sampler.ownership_facts(
            registry_at_pre_load_count=True,
            shared_sd_model_no_model=True,
            model_data_sd_model_no_model=True,
        )
        self.assertFalse(facts["all_owned_weakrefs_dead"])
        self.assertFalse(residual.ownership_is_clean(facts))

    def test_absent_and_non_weakrefable_objects_are_named_not_counted(self) -> None:
        sampler = residual.OwnershipSampler()
        sampler.track("missing", None)
        sampler.track("primitive", 42)
        for release in residual.REQUIRED_RELEASES:
            sampler.mark(release)
        result = sampler.sample()
        self.assertEqual(0, result["observed"])
        self.assertEqual(2, len(result["skipped"]))
        self.assertTrue(result["all_owned_weakrefs_dead"])


# ------------------------------------------------------------- regressions


class NoRegressionTests(unittest.TestCase):
    def test_malformed_telemetry_still_fails_closed(self) -> None:
        verdict = residual.classify({"allocated": 0, "reserved": 0},
                                    {"all_owned_weakrefs_dead": True})
        self.assertEqual(residual.TELEMETRY_SCHEMA_INVALID, verdict["classification"])

    def test_the_stage_contract_is_unchanged(self) -> None:
        self.assertEqual(16, len(residual.STAGES))
        self.assertEqual(residual.S0, residual.STAGES[0])
        self.assertEqual(residual.S15, residual.STAGES[-1])

    def test_first_step_reporting_is_unchanged(self) -> None:
        recorder = residual.SamplerStepRecorder()
        recorder.enter_denoiser(12)
        recorder.observe_one_based(0)
        recorder.observe_one_based(1)
        truth = recorder.to_dict()
        self.assertEqual(1, truth["first_completed_step_index"])
        self.assertFalse(truth["derived_from_final_counter"])

    def test_cleanup_adds_no_cuda_cache_clear(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("empty_cache", source.split("def _cleanup(")[1])
        self.assertNotIn("release_cuda_cache", source)

    def test_cleanup_records_no_extra_studio_terminal_clear(self) -> None:
        clears = smoke.CacheClearAccounting()
        smoke._cleanup(  # noqa: SLF001
            SyntheticPort(Sentinel("engine")), SyntheticSession(), None,
            smoke.LifecycleRecorder(), clears,
        )
        self.assertEqual(0, clears.to_dict()["studio_owned_terminal"])
        self.assertEqual(0, clears.to_dict()["total_observed"])

    def test_the_release_helper_imports_no_gradio(self) -> None:
        source = SMOKE_PATH.read_text(encoding="utf-8")
        helper = source.split("def _release_generation_references(")[1].split("\ndef ")[0]
        self.assertNotIn("gradio", helper)

    def test_the_lifecycle_fields_are_unchanged(self) -> None:
        for name in ("generation_references_released", "model_references_released",
                     "first_completed_step_index", "final_completed_steps"):
            self.assertIn(name, smoke.LIFECYCLE_FIELDS)

    def test_cleanup_still_reports_the_fields_the_prior_suites_read(self) -> None:
        report, _seam = run_cleanup(SyntheticPort(Sentinel("engine")))
        for key in ("port_release", "session_closed", "gc_collected",
                    "application_stopped_here"):
            self.assertIn(key, report)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_RELEASE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("STATIC_IMPORT_SCOPE", __doc__ or "")
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
