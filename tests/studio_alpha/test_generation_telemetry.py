"""Where the time goes between Generate and a usable image.

This suite exists because of two conclusions this project drew confidently and
wrongly, both from numbers that meant something other than what they looked
like:

* Studio was "70% slower than Neo". The two runs were at different
  resolutions -- 768x960 against 1024x1024, 1.42x the pixels. Correcting for
  it left a 1.4x denoising difference and an overhead difference pointing the
  OTHER way, in Studio's favour.
* Auto Detail was "4.43x slower". It was the global denoising gap multiplied
  by 2.25x of pixels it should never have been denoising, because the detail
  pass was sized from the post-Hires frame instead of the base request.

Both were visible in per-stage effective dimensions and invisible in a
wall-clock total. So the two things this records that a progress bar cannot:

**Effective work.** The size a stage ACTUALLY processed. `detail` reading
1536x1536 next to `base` reading 1024x1024 is the defect, stated.

**Unaccounted time.** Wall clock minus the stages: model moves and device
transfers, which appear in no progress bar. Studio's measured advantage over
Neo lives there -- 5.31 s against 12.29 s -- so it is reported as a
first-class figure rather than left to be derived.

No GPU, no torch: this is about what gets RECORDED, which is what regressed.
"""

from __future__ import annotations

import ast
import sys
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.generation_telemetry import (  # noqa: E402
    COUNTERS,
    GenerationTelemetry,
)

EXPECTED_GENERATION_TELEMETRY_TESTS = 27


class RecordingTests(unittest.TestCase):
    def test_a_stage_records_its_effective_size_not_its_request(self) -> None:
        # THE Auto Detail defect, stated as a measurement. A detail pass
        # sized from the post-Hires frame reads 1536x1536 here while the base
        # reads 1024x1024, and the two sitting next to each other is what
        # makes it visible.
        telemetry = GenerationTelemetry("j")
        telemetry.start("detail")
        telemetry.stop("detail", width=1024, height=1024, steps=10)
        stage = telemetry.describe()["stages"][0]
        self.assertEqual("1024x1024", stage["size"])
        self.assertEqual(10, stage["steps"])

    def test_a_stage_reports_its_own_rate(self) -> None:
        telemetry = GenerationTelemetry("j")
        telemetry.start("base")
        time.sleep(0.02)
        telemetry.stop("base", steps=10)
        self.assertGreater(telemetry.describe()["stages"][0]["it_s"], 0)

    def test_unaccounted_time_is_reported_not_derived(self) -> None:
        # Model moves live here. Nobody puts a stage around them, and they
        # are where Studio currently beats Neo.
        telemetry = GenerationTelemetry("j")
        telemetry.start("base")
        time.sleep(0.01)
        telemetry.stop("base")
        time.sleep(0.02)
        described = telemetry.describe()
        self.assertGreater(described["unaccounted_seconds"], 0.0)
        self.assertAlmostEqual(
            described["total_seconds"],
            described["accounted_seconds"] + described["unaccounted_seconds"],
            places=2)

    def test_a_stage_that_never_ran_is_absent_not_zero(self) -> None:
        # A zero is a measurement. "We did not do this" is not, and reporting
        # it as 0.000 would put a false row in a scorecard.
        telemetry = GenerationTelemetry("j")
        telemetry.start("base")
        telemetry.stop("base")
        names = {stage["stage"] for stage in telemetry.describe()["stages"]}
        self.assertEqual({"base"}, names)

    def test_stopping_a_stage_that_never_started_records_nothing(self) -> None:
        telemetry = GenerationTelemetry("j")
        telemetry.stop("hires")
        self.assertEqual([], telemetry.describe()["stages"])

    def test_counts_answer_was_the_work_redundant(self) -> None:
        # No duration can answer "did this encode the prompt again?".
        telemetry = GenerationTelemetry("j")
        telemetry.count("conditioning_setup_calls")
        telemetry.count("conditioning_setup_calls")
        telemetry.count("component_moves", 3)
        counts = telemetry.describe()["counts"]
        self.assertEqual(2, counts["conditioning_setup_calls"])
        self.assertEqual(3, counts["component_moves"])

    def test_an_unknown_counter_is_ignored_rather_than_invented(self) -> None:
        telemetry = GenerationTelemetry("j")
        telemetry.count("made_up")
        self.assertNotIn("made_up", telemetry.describe()["counts"])

    def test_every_counter_starts_at_zero(self) -> None:
        counts = GenerationTelemetry("j").describe()["counts"]
        self.assertEqual(set(COUNTERS), set(counts))
        self.assertEqual({0}, set(counts.values()))


class NeverCostsAPictureTests(unittest.TestCase):
    """Telemetry must not take a generation down. Same contract as
    `record_generation` and `note_generation`."""

    def test_recording_something_unserialisable_does_not_raise(self) -> None:
        telemetry = GenerationTelemetry("j")
        telemetry.start("base")
        telemetry.stop("base", weird=object())
        telemetry.describe()

    def test_a_summary_of_nothing_does_not_raise(self) -> None:
        self.assertIn("total=", GenerationTelemetry().summary())

    def test_unaccounted_never_goes_negative(self) -> None:
        telemetry = GenerationTelemetry("j")
        telemetry.start("base")
        telemetry.stop("base")
        self.assertGreaterEqual(telemetry.unaccounted(), 0.0)


class WiredIntoGenerationTests(unittest.TestCase):
    """The port must actually record, or none of the above matters."""

    SOURCE = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
        encoding="utf-8")

    def _calls(self, method: str) -> list[str]:
        tree = ast.parse(self.SOURCE)
        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == method
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "telemetry"):
                if node.args and isinstance(node.args[0], ast.Constant):
                    found.append(str(node.args[0].value))
        return found

    def test_the_owned_stages_are_bracketed(self) -> None:
        started, stopped = set(self._calls("start")), set(self._calls("stop"))
        self.assertLessEqual(
            {"primary_pipeline", "prepare_detail", "detail", "publish"}, started)
        self.assertEqual(started, stopped, "a stage is started and never stopped")

    def test_the_pipeline_row_is_not_called_base(self) -> None:
        """It brackets base + decode + upscale + Hires as one inherited call.

        Naming it `base` invites a comparison against Neo's base-only `it/s`
        that would find a deficit which is mostly a Hires pass -- the exact
        class of false conclusion this instrumentation exists to prevent.
        """

        self.assertNotIn("base", set(self._calls("start")))

    def test_the_pipeline_row_records_enough_to_interpret_it(self) -> None:
        # It is not a per-stage denoise timer and must not be read as one, so
        # the Hires settings that make up its time are recorded beside it.
        marker = 'telemetry.stop(' + chr(10) + ' ' * 16 + '"primary_pipeline"'
        body = self.SOURCE[self.SOURCE.index(marker):][:900]
        for field in ("hires", "hires_target", "hires_steps", "upscaler"):
            self.assertIn(field, body)

    def test_the_detail_pass_records_the_size_it_denoises(self) -> None:
        start = self.SOURCE.index("def _detail_pass(")
        body = self.SOURCE[start:][:6000]
        self.assertIn("_detail_width", body)
        self.assertIn('telemetry.count("detail_regions")', body)

    def test_telemetry_failure_cannot_reach_the_generation(self) -> None:
        # Every public method swallows. Verified on the class, not the
        # comment: this runs inside the try that owns the owner's picture.
        from forge_headless import generation_telemetry as module

        source = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        guarded = {
            node.name for node in ast.walk(source)
            if isinstance(node, ast.FunctionDef)
            and any(isinstance(child, ast.Try) for child in ast.walk(node))
        }
        self.assertLessEqual({"start", "stop", "count"}, guarded)


class ModelMoveObserverTests(unittest.TestCase):
    """Counting the moves, through the one seam they all pass through.

    `backend.memory_management.load_models_gpu` (:616) is where every move to
    the device happens and where `Moving model(s) has taken N` is logged
    (:702). A wrapper answers "how many, and when" without forking it.

    "The text encoder moved twice instead of four times" is a claim that
    survives a noisy machine. "It was 0.4 s faster" is not.
    """

    def _fake_backend(self):
        import types

        module = types.ModuleType("backend.memory_management")
        module.get_free_memory = lambda *a, **k: 9_000 * 1024 ** 2
        module.load_models_gpu = lambda models, **kw: "loaded"
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)
        return module

    def test_moves_are_counted_and_attributed_to_the_open_stage(self) -> None:
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._fake_backend()
        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            telemetry.start("primary_pipeline")
            module.load_models_gpu([])
            telemetry.stop("primary_pipeline")
            telemetry.start("prepare_detail")
            module.load_models_gpu([])
            module.load_models_gpu([])
            telemetry.stop("prepare_detail")
        described = telemetry.describe()
        self.assertEqual(3, described["counts"]["component_moves"])
        self.assertEqual({"primary_pipeline": 1, "prepare_detail": 2},
                         described["model_moves_by_stage"])

    def test_the_module_is_restored_afterwards(self) -> None:
        # A patch left behind would attribute the NEXT generation's moves to a
        # telemetry object nobody is reading.
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._fake_backend()
        original = module.load_models_gpu
        with ModelMoveObserver(GenerationTelemetry("j")):
            self.assertIsNot(original, module.load_models_gpu)
        self.assertIs(original, module.load_models_gpu)

    def test_the_module_is_restored_even_when_the_body_raises(self) -> None:
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._fake_backend()
        original = module.load_models_gpu
        observer = ModelMoveObserver(GenerationTelemetry("j"))
        observer.__enter__()
        try:
            raise RuntimeError("the generation failed")
        except RuntimeError:
            pass
        finally:
            observer.__exit__(None, None, None)
        self.assertIs(original, module.load_models_gpu)

    def test_a_backend_without_the_seam_is_not_an_error(self) -> None:
        """A backend that cannot be observed is observed as nothing.

        Deliberately a module WITHOUT `load_models_gpu` rather than no module
        at all: removing `backend` from `sys.modules` would send the
        observer's import to the REAL package, which pulls torch into this
        process and breaks the import-boundary suite that asserts
        `forge_studio.launch` reaches no CUDA. The canonical suite runs in one
        process, so a test that imports torch changes what every later test
        sees.
        """

        import types

        from forge_headless.generation_telemetry import ModelMoveObserver

        module = types.ModuleType("backend.memory_management")  # no seam
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)
        with ModelMoveObserver(GenerationTelemetry("j")):
            pass


class MoveTraceTests(unittest.TestCase):
    """What section 3 of the churn-trace handoff requires per move.

    A count says churn happened. It does not say WHICH component moved, how
    much was asked for, or whether memory was tight enough to force an unload
    -- and those decide whether a move is required or avoidable.
    """

    def _backend(self):
        import types

        module = types.ModuleType("backend.memory_management")
        module.get_free_memory = lambda *a, **k: 9_000 * 1024 ** 2
        module.load_models_gpu = lambda models, memory_required=0, **kw: None
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)
        return module

    def test_a_move_names_the_component_by_class_not_by_path(self) -> None:
        # This reaches a log and an Evidence file. `JointTextEncoder`
        # identifies the component; a checkpoint path is the owner's private
        # business.
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._backend()

        class JointTextEncoder:
            pass

        class Patcher:
            model = JointTextEncoder()

        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            telemetry.start("detail")
            module.load_models_gpu([Patcher()], memory_required=2526 * 1024 ** 2)
            telemetry.stop("detail")
        move = telemetry.describe()["moves"][0]
        self.assertEqual(["JointTextEncoder"], move["components"])
        self.assertEqual(["detail"], move["stages"])

    def test_a_move_records_the_pressure_it_was_made_under(self) -> None:
        # "Was this unload necessary?" is answered by free-vs-requested, not
        # by a story about memory management.
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._backend()
        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            module.load_models_gpu([], memory_required=2526 * 1024 ** 2)
        move = telemetry.describe()["moves"][0]
        self.assertAlmostEqual(2526.0, move["requested_mb"], places=1)
        self.assertGreater(move["free_before_mb"], 0)

    def test_a_backend_that_cannot_report_memory_still_records_the_move(self) -> None:
        from forge_headless.generation_telemetry import ModelMoveObserver

        module = self._backend()
        del module.get_free_memory
        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            module.load_models_gpu([], memory_required=1)
        self.assertEqual(1, len(telemetry.describe()["moves"]))


class EvictionBindingTests(unittest.TestCase):
    """Which component lost residency, and to satisfy WHICH request.

    Section 14: "Do not leave the unload as an unowned log line." The
    backend's own message names bytes -- `Unloaded partially: 631.50 MB
    freed, 4265.56 MB remains loaded` -- but not which component lost them
    and never which incoming request forced the choice. That binding is what
    decides whether the eviction was necessary, and it is the whole question
    this package turns on.
    """

    def _backend_where_loading_evicts(self):
        import types

        class KModel:
            pass

        class Patcher:
            def __init__(self, inner, size):
                self.model, self._size = inner, size

            def loaded_size(self):
                return self._size

        class Loaded:
            def __init__(self, patcher):
                self.model = patcher

        resident = Patcher(KModel(), 4897 * 1024 ** 2)
        module = types.ModuleType("backend.memory_management")
        module.current_loaded_models = [Loaded(resident)]
        module.get_free_memory = lambda *a, **k: 1500 * 1024 ** 2

        def load_models_gpu(models, memory_required=0, **kw):
            resident._size -= 631 * 1024 ** 2   # the partial unload
            module.current_loaded_models.append(Loaded(models[0]))

        module.load_models_gpu = load_models_gpu
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)
        return module, Patcher

    def test_an_eviction_names_the_component_and_the_bytes(self) -> None:
        from forge_headless.generation_telemetry import ModelMoveObserver

        module, Patcher = self._backend_where_loading_evicts()

        class JointTextEncoder:
            pass

        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            telemetry.start("detail")
            module.load_models_gpu(
                [Patcher(JointTextEncoder(), 2526 * 1024 ** 2)],
                memory_required=2526 * 1024 ** 2)
            telemetry.stop("detail")
        move = telemetry.describe()["moves"][0]
        self.assertEqual(["JointTextEncoder"], move["components"])
        self.assertEqual(1, len(move["evicted"]))
        loss = move["evicted"][0]
        self.assertEqual("KModel", loss["component"])
        self.assertAlmostEqual(631.0, loss["freed_mb"], places=0)
        self.assertFalse(loss["fully"], "this was a partial unload")

    def test_a_move_that_evicts_nothing_records_nothing(self) -> None:
        # Clear headroom is the decision that permits retention, so "no
        # eviction" must be recordable as a fact rather than an absence.
        import types

        from forge_headless.generation_telemetry import ModelMoveObserver

        module = types.ModuleType("backend.memory_management")
        module.current_loaded_models = []
        module.get_free_memory = lambda *a, **k: 9_000 * 1024 ** 2
        module.load_models_gpu = lambda models, **kw: None
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)

        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            module.load_models_gpu([])
        self.assertEqual([], telemetry.describe()["moves"][0]["evicted"])

    def test_unreadable_backend_state_is_not_fatal(self) -> None:
        import types

        from forge_headless.generation_telemetry import ModelMoveObserver

        module = types.ModuleType("backend.memory_management")
        module.current_loaded_models = ["not a LoadedModel"]
        module.load_models_gpu = lambda models, **kw: None
        backend = types.ModuleType("backend")
        backend.memory_management = module
        sys.modules["backend"] = backend
        sys.modules["backend.memory_management"] = module
        self.addCleanup(sys.modules.pop, "backend", None)
        self.addCleanup(sys.modules.pop, "backend.memory_management", None)

        telemetry = GenerationTelemetry("j")
        with ModelMoveObserver(telemetry):
            module.load_models_gpu([])
        self.assertEqual(1, len(telemetry.describe()["moves"]))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GENERATION_TELEMETRY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
