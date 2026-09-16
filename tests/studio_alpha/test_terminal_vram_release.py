"""Contracts for the terminal VRAM release (owner-trial closure).

The owner-trial live smoke released every Studio-owned reference before
its unload -- allocated fell to 9,568,256 bytes -- and still measured
6,111,100,928 bytes RESERVED, because all twelve observed cache clears
ran BEFORE the final reference release. This suite pins the missing step
and, more importantly, its ORDER.

Four areas:

* **The primitive** -- `forge_headless/terminal_release.py` imports
  nothing, no-ops with a stable scalar reason when torch is absent or
  CUDA was never initialized, prefers Forge's `soft_empty_cache` when the
  memory manager is present, falls back to the bare torch primitive, and
  never raises.

* **The seam** -- `LoadedStudioSession.close()` performs the clear after
  dropping its last references; a duplicate close performs none; the
  lifecycle counts terminal clears separately from every other clear;
  explicit unload, switch, and shutdown all reach the one seam; a load
  that failed after the engine step clears once on rollback, and one that
  failed before it clears not at all.

* **The synthetic allocator regression** -- a product-level allocator
  facade tracking allocated/reserved bytes, reference liveness, registry
  count, terminal clear calls, and event order, with the exact required
  success order and four negative orders that must fail.

* **Startup purity** -- the fix adds no import and no clear to a process
  that never loaded.

SCOPE: synthetic throughout. No torch, no CUDA, no payload, no real
generation anywhere in this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import types
import unittest
import weakref
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.session_loader import LoadedStudioSession  # noqa: E402
from forge_headless.terminal_release import (  # noqa: E402
    CLEAR_FAILED,
    CUDA_NOT_INITIALIZED,
    RELEASED,
    STUDIO_RELEASE_CUDA_CACHE,
    TORCH_NOT_IMPORTED,
    release_terminal_cache,
)


class _Owned:
    """A weakref-able stand-in for an owned runtime object."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCuda:
    def __init__(self, *, available=True, initialized=True, fail=False) -> None:
        self._available = available
        self._initialized = initialized
        self._fail = fail
        self.empty_cache_calls = 0

    def is_available(self):
        return self._available

    def is_initialized(self):
        return self._initialized

    def empty_cache(self):
        self.empty_cache_calls += 1
        if self._fail:
            raise RuntimeError("driver said no")


class _ModuleSwap:
    """Install fake `torch` / `backend.memory_management` entries safely."""

    def __init__(self, **modules: object) -> None:
        self._modules = modules
        self._saved: dict[str, object] = {}
        self._created: list[str] = []

    def __enter__(self) -> _ModuleSwap:
        for name, module in self._modules.items():
            if name in sys.modules:
                self._saved[name] = sys.modules[name]
            else:
                self._created.append(name)
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc) -> bool:
        for name in self._modules:
            sys.modules.pop(name, None)
        for name, module in self._saved.items():
            sys.modules[name] = module  # type: ignore[assignment]
        self._saved.clear()
        self._created.clear()
        return False


def _torch(cuda: _FakeCuda) -> types.ModuleType:
    module = types.ModuleType("torch")
    module.cuda = cuda  # type: ignore[attr-defined]
    return module


class _RecordedClear:
    """Patch the ONE sanctioned Studio clear and record its calls.

    The guard suite pins `release_cuda_cache` as the only allowed cache
    clear in Studio-owned code, so the regression observes THAT rather
    than introducing a second primitive to fake.
    """

    def __init__(self, record: list[str], *, fail: bool = False) -> None:
        self.record = record
        self.fail = fail
        self._module = None
        self._original = None

    def __enter__(self) -> _RecordedClear:
        from forge_headless import controlled_device

        self._module = controlled_device
        self._original = controlled_device.release_cuda_cache

        def recorded():
            self.record.append("release_cuda_cache")
            if self.fail:
                raise RuntimeError("driver said no")

        controlled_device.release_cuda_cache = recorded
        return self

    def __exit__(self, *_exc) -> bool:
        self._module.release_cuda_cache = self._original
        return False


# ---------------------------------------------------------------------------
# the primitive
# ---------------------------------------------------------------------------

class TerminalReleasePrimitiveTests(unittest.TestCase):
    def test_module_imports_nothing_heavy(self) -> None:
        source = (APP_ROOT / "forge_headless" / "terminal_release.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import torch", "from torch", "import backend",
                          "from backend", "import modules", "from modules"):
            self.assertNotIn(forbidden, source)

    def test_no_op_when_torch_was_never_imported(self) -> None:
        with _ModuleSwap(torch=None):
            report = release_terminal_cache()
        self.assertFalse(report["called"])
        self.assertEqual(TORCH_NOT_IMPORTED, report["reason"])
        self.assertTrue(report["terminal"])

    def test_no_op_on_a_cpu_only_process(self) -> None:
        cuda = _FakeCuda(available=False)
        with _ModuleSwap(torch=_torch(cuda)):
            report = release_terminal_cache()
        self.assertFalse(report["called"])
        self.assertEqual(CUDA_NOT_INITIALIZED, report["reason"])
        self.assertEqual(0, cuda.empty_cache_calls)

    def test_no_op_when_cuda_context_was_never_built(self) -> None:
        cuda = _FakeCuda(available=True, initialized=False)
        with _ModuleSwap(torch=_torch(cuda)):
            report = release_terminal_cache()
        self.assertFalse(report["called"])
        self.assertEqual(CUDA_NOT_INITIALIZED, report["reason"])
        self.assertEqual(0, cuda.empty_cache_calls)

    def test_prefers_the_forge_aware_primitive(self) -> None:
        record: list[str] = []
        cuda = _FakeCuda()
        with _ModuleSwap(torch=_torch(cuda)), _RecordedClear(record):
            report = release_terminal_cache()
        self.assertTrue(report["called"])
        self.assertEqual(STUDIO_RELEASE_CUDA_CACHE, report["primitive"])
        self.assertEqual(RELEASED, report["reason"])
        self.assertEqual(["release_cuda_cache"], record)

    def test_it_adds_no_second_cache_clear_site(self) -> None:
        """ORDER, not a second primitive.

        The Tier-0 boundary guard allows exactly one Studio-owned
        `empty_cache` call site (in `controlled_device.release_cuda_cache`)
        and forbids Studio code from naming Forge memory-manager symbols.
        Checked the same way the guard checks: over the AST, so prose in
        this module's own docstring is not mistaken for a call.
        """

        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_headless" / "terminal_release.py")
            .read_text(encoding="utf-8")
        )
        clear_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("empty_cache", "soft_empty_cache",
                                   "ipc_collect")
        ]
        self.assertEqual([], clear_calls)
        referenced = {n.attr for n in ast.walk(tree)
                      if isinstance(n, ast.Attribute)}
        referenced |= {n.id for n in ast.walk(tree)
                       if isinstance(n, ast.Name)}
        for forbidden in ("soft_empty_cache", "unload_all_models",
                          "unload_model_weights", "free_memory", "torch_gc"):
            self.assertNotIn(forbidden, referenced)
        self.assertIn(
            "release_cuda_cache",
            {alias.name for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom)
             for alias in node.names},
        )

    def test_a_failing_clear_reports_a_scalar_and_never_raises(self) -> None:
        record: list[str] = []
        with _ModuleSwap(torch=_torch(_FakeCuda())),                 _RecordedClear(record, fail=True):
            report = release_terminal_cache()
        self.assertFalse(report["called"])
        self.assertEqual(CLEAR_FAILED, report["reason"])
        self.assertEqual("RuntimeError", report["error"])
        self.assertNotIn("Traceback", json.dumps(report))

    def test_a_refusing_probe_is_a_no_op_not_a_crash(self) -> None:
        class _Hostile:
            def is_available(self):
                raise RuntimeError("probe exploded")

        module = types.ModuleType("torch")
        module.cuda = _Hostile()  # type: ignore[attr-defined]
        with _ModuleSwap(torch=module):
            report = release_terminal_cache()
        self.assertFalse(report["called"])
        self.assertEqual(CUDA_NOT_INITIALIZED, report["reason"])


# ---------------------------------------------------------------------------
# the seam
# ---------------------------------------------------------------------------

class CloseSeamTests(unittest.TestCase):
    def _session(self, closers=()) -> LoadedStudioSession:
        return LoadedStudioSession(
            profile_id="alpha", family="qwen-image",
            session=object(), engine=object(), port=object(),
            identity={}, steps=("engine_built",), _closers=tuple(closers),
        )

    def test_close_runs_exactly_one_terminal_release(self) -> None:
        record: list[str] = []
        session = self._session()
        with _ModuleSwap(torch=_torch(_FakeCuda())), _RecordedClear(record):
            report = session.close()
        self.assertTrue(report["terminal_release"]["called"])
        self.assertEqual(["release_cuda_cache"], record)

    def test_duplicate_close_does_not_clear_again(self) -> None:
        record: list[str] = []
        session = self._session()
        with _ModuleSwap(torch=_torch(_FakeCuda())), _RecordedClear(record):
            session.close()
            second = session.close()
        self.assertTrue(second["already"])
        self.assertNotIn("terminal_release", second)
        self.assertEqual(1, len(record))

    def test_clear_runs_after_every_reference_is_dropped(self) -> None:
        order: list[str] = []
        engine = _Owned("engine")
        probe = weakref.ref(engine)
        session = LoadedStudioSession(
            profile_id="alpha", family="qwen-image",
            session=object(), engine=engine, port=object(), identity={},
            steps=("engine_built",),
            _closers=(lambda: order.append("closer"),),
        )
        del engine
        from forge_headless import controlled_device

        original = controlled_device.release_cuda_cache

        def recorded():
            order.append("terminal_clear")
            # The session must no longer hold anything by now.
            order.append(f"engine_field={session.engine is None}")

        controlled_device.release_cuda_cache = recorded
        self.addCleanup(setattr, controlled_device, "release_cuda_cache",
                        original)
        with _ModuleSwap(torch=_torch(_FakeCuda())):
            session.close()
        self.assertEqual(
            ["closer", "terminal_clear", "engine_field=True"], order
        )
        self.assertIsNone(probe())

    def test_clear_failure_leaves_the_session_released(self) -> None:
        record: list[str] = []
        session = self._session()
        with _ModuleSwap(torch=_torch(_FakeCuda())),                 _RecordedClear(record, fail=True):
            report = session.close()
        self.assertTrue(report["closed"])
        self.assertFalse(report["terminal_release"]["called"])
        self.assertEqual(CLEAR_FAILED, report["terminal_release"]["reason"])
        # Ownership stays released regardless of the clear outcome.
        self.assertIsNone(session.session)
        self.assertIsNone(session.engine)
        self.assertIsNone(session.port)
        self.assertTrue(session.closed)


class LifecycleAccountingTests(unittest.TestCase):
    """The one seam, reached by unload, switch, and shutdown."""

    def setUp(self) -> None:
        from forge_studio.model_lifecycle import WarmSessionManager
        from forge_studio.model_profiles import ModelProfileRepository

        self.closed: list[str] = []
        self.reports: list[dict] = []

        def closer(session):
            self.closed.append(getattr(session, "profile_id", "?"))
            return {"closed": True, "already": False, "errors": [],
                    "terminal_release": {"terminal": True, "called": True,
                                         "primitive": STUDIO_RELEASE_CUDA_CACHE,
                                         "reason": RELEASED}}

        def loader(profile):
            return types.SimpleNamespace(
                profile_id=profile.profile_id, close=lambda: None)

        self.manager = WarmSessionManager(
            profiles=ModelProfileRepository([B._profile("alpha"),
                                             B._profile("beta")]),
            loader=loader, closer=closer,
        )

    def test_explicit_unload_counts_one_terminal_clear(self) -> None:
        self.manager.ensure_loaded(B._profile("alpha"))
        self.manager.unload()
        self.assertEqual(1, self.manager.counters["terminal_cache_clears"])
        self.assertEqual(RELEASED,
                         self.manager.last_terminal_release["reason"])

    def test_duplicate_unload_does_not_duplicate_the_clear(self) -> None:
        self.manager.ensure_loaded(B._profile("alpha"))
        self.manager.unload()
        self.manager.unload()
        self.assertEqual(1, self.manager.counters["terminal_cache_clears"])
        self.assertEqual("no_model", self.manager.state.value)

    def test_shutdown_while_ready_reuses_the_same_seam(self) -> None:
        self.manager.ensure_loaded(B._profile("alpha"))
        self.manager.shutdown()
        self.assertEqual(1, self.manager.counters["terminal_cache_clears"])

    def test_shutdown_after_unload_does_not_clear_twice(self) -> None:
        self.manager.ensure_loaded(B._profile("alpha"))
        self.manager.unload()
        self.manager.shutdown()
        self.assertEqual(1, self.manager.counters["terminal_cache_clears"])

    def test_switch_clears_once_for_the_replaced_session(self) -> None:
        self.manager.ensure_loaded(B._profile("alpha"))
        self.manager.ensure_loaded(B._profile("beta"))
        self.assertEqual(1, self.manager.counters["terminal_cache_clears"])
        self.assertEqual(["alpha"], self.closed)

    def test_no_model_unload_request_clears_nothing(self) -> None:
        # Nothing is resident: the old select() loaded nothing, and there is
        # no select now, so the arrangement is simply an unload on a cold
        # manager -- which is what this always meant.
        self.manager.unload()
        self.assertEqual(0, self.manager.counters["terminal_cache_clears"])
        self.assertIsNone(self.manager.last_terminal_release)

    def test_a_no_op_release_is_not_counted_as_a_clear(self) -> None:
        from forge_studio.model_lifecycle import WarmSessionManager
        from forge_studio.model_profiles import ModelProfileRepository

        def closer(session):
            return {"closed": True, "terminal_release": {
                "terminal": True, "called": False,
                "reason": CUDA_NOT_INITIALIZED, "primitive": None}}

        manager = WarmSessionManager(
            profiles=ModelProfileRepository([B._profile("alpha")]),
            loader=lambda profile: types.SimpleNamespace(close=lambda: None),
            closer=closer,
        )
        manager.ensure_loaded(B._profile("alpha"))
        manager.unload()
        self.assertEqual(0, manager.counters["terminal_cache_clears"])
        self.assertEqual(CUDA_NOT_INITIALIZED,
                         manager.last_terminal_release["reason"])


class LoadRollbackTests(unittest.TestCase):
    """A failed load clears only when an engine had been allocated.

    Runs over the seam suite's synthetic world, because reaching the
    engine step at all requires configured access and the retained
    modules -- a loader without them refuses earlier, which is itself the
    "before the engine" case below.
    """

    def setUp(self) -> None:
        import tempfile

        self.neo = B.NeoStub().install()
        self.addCleanup(self.neo.restore)
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self.addCleanup(self.intercepts.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _loader(self, **kwargs):
        from forge_headless.live_bindings import LoadConfiguration
        from forge_headless.session_loader import HeadlessSessionLoader

        return HeadlessSessionLoader(
            result_root=Path(self.tmp.name),
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT,
                authorization=B.FakeAuthorization(),
            ),
            **kwargs,
        )

    def test_failure_before_the_engine_step_clears_nothing(self) -> None:
        record: list[str] = []
        loader = self._loader(
            payload_opener=lambda **_kw: (_ for _ in ()).throw(
                RuntimeError("payload refused")),
        )
        with _ModuleSwap(torch=_torch(_FakeCuda())), _RecordedClear(record):
            with self.assertRaises(Exception):
                loader.load(B._profile("alpha"))
        self.assertEqual([], record)
        self.assertIsNone(getattr(loader, "terminal_release_report", None))

    def test_failure_after_the_engine_step_clears_once(self) -> None:
        record: list[str] = []
        loader = self._loader(
            engine_builder=lambda **_kw: B.FakeEngine(),
            identity_installer=lambda engine: (_ for _ in ()).throw(
                RuntimeError("identity refused")),
        )
        with _ModuleSwap(torch=_torch(_FakeCuda())), _RecordedClear(record):
            with self.assertRaises(Exception):
                loader.load(B._profile("alpha"))
        self.assertEqual(["release_cuda_cache"], record)
        self.assertTrue(loader.terminal_release_report["called"])


# ---------------------------------------------------------------------------
# the synthetic allocator regression
# ---------------------------------------------------------------------------

REQUIRED_UNLOAD_ORDER = (
    "unloading_entered",
    "wrapper_closed",
    "adapter_cleared",
    "engine_released",
    "sentinel_restored",
    "registry_restored",
    "owned_weakrefs_dead",
    "terminal_cache_clear",
    "memory_sampled",
    "no_model_published",
)


class SyntheticAllocator:
    """A product-level allocator facade with observable ordering.

    Reserved bytes behave like the CUDA caching allocator: freeing an
    allocation returns it to the reserve, and ONLY a terminal clear
    returns the reserve to the device -- and only when nothing is still
    allocated.
    """

    def __init__(self) -> None:
        self.allocated = 0
        self.reserved = 0
        self.terminal_clears = 0
        self.events: list[str] = []
        self.registry: list[object] = []

    def allocate(self, size: int) -> None:
        self.allocated += size
        self.reserved = max(self.reserved, self.allocated)

    def free(self, size: int) -> None:
        self.allocated = max(0, self.allocated - size)

    def terminal_clear(self) -> dict:
        self.terminal_clears += 1
        self.events.append("terminal_cache_clear")
        if self.allocated == 0:
            self.reserved = 0
            return {"called": True, "reason": RELEASED, "terminal": True}
        # Live blocks pin the reserve: clearing early cannot return it.
        return {"called": True, "reason": "blocks_still_live",
                "terminal": True}

    def sample(self) -> dict:
        self.events.append("memory_sampled")
        return {"status": "ok", "allocated_bytes": self.allocated,
                "reserved_bytes": self.reserved}


class _SyntheticUnload:
    """One scripted unload over the synthetic allocator."""

    ENGINE_BYTES = 6_000_000_000

    def __init__(self, allocator: SyntheticAllocator) -> None:
        self.allocator = allocator
        self.engine: object | None = _Owned("engine")
        self.wrapper: object | None = _Owned("wrapper")
        self.probe_engine = weakref.ref(self.engine)
        self.probe_wrapper = weakref.ref(self.wrapper)
        self.allocator.allocate(self.ENGINE_BYTES)
        self.allocator.registry.append(self.engine)
        self.sentinel_restored = False
        self.adapter_attached = True
        self.state = "ready"

    # -- the scripted steps ------------------------------------------------

    def enter_unloading(self) -> None:
        self.state = "unloading"
        self.allocator.events.append("unloading_entered")

    def close_wrapper(self) -> None:
        self.wrapper = None
        self.allocator.events.append("wrapper_closed")

    def clear_adapter(self) -> None:
        self.adapter_attached = False
        self.allocator.events.append("adapter_cleared")

    def release_engine(self) -> None:
        self.allocator.free(self.ENGINE_BYTES)
        self.engine = None
        self.allocator.events.append("engine_released")

    def restore_sentinel(self) -> None:
        self.sentinel_restored = True
        self.allocator.events.append("sentinel_restored")

    def restore_registry(self) -> None:
        self.allocator.registry.clear()
        self.allocator.events.append("registry_restored")

    def assert_weakrefs_dead(self) -> bool:
        import gc

        gc.collect()
        dead = self.probe_engine() is None and self.probe_wrapper() is None
        if dead:
            self.allocator.events.append("owned_weakrefs_dead")
        return dead

    def publish_no_model(self) -> None:
        self.state = "no_model"
        self.allocator.events.append("no_model_published")

    def ownership_clean(self) -> bool:
        return (self.engine is None and self.wrapper is None
                and self.sentinel_restored and not self.adapter_attached
                and not self.allocator.registry)


class SyntheticAllocatorRegressionTests(unittest.TestCase):
    def _run_correct_order(self) -> tuple[_SyntheticUnload, dict]:
        allocator = SyntheticAllocator()
        run = _SyntheticUnload(allocator)
        run.enter_unloading()
        run.close_wrapper()
        run.clear_adapter()
        run.release_engine()
        run.restore_sentinel()
        run.restore_registry()
        self.assertTrue(run.assert_weakrefs_dead())
        allocator.terminal_clear()
        sample = allocator.sample()
        run.publish_no_model()
        return run, sample

    def test_the_required_order_releases_reserved_memory(self) -> None:
        run, sample = self._run_correct_order()
        self.assertEqual(list(REQUIRED_UNLOAD_ORDER), run.allocator.events)
        self.assertEqual(1, run.allocator.terminal_clears)
        self.assertEqual(0, sample["allocated_bytes"])
        self.assertEqual(0, sample["reserved_bytes"])
        self.assertEqual("no_model", run.state)
        self.assertTrue(run.ownership_clean())

    def test_the_released_sample_passes_the_central_classifier(self) -> None:
        from forge_headless.memory_report import (
            MEMORY_ACCEPTED,
            classify_memory,
        )

        run, sample = self._run_correct_order()
        verdict = classify_memory(
            job1_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            job2_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            post_unload=sample,
            warm_tolerance_bytes=1_048_576,
            allocated_ceiling_bytes=11_206_656,
            reserved_ceiling_bytes=26_214_400,
            ownership={"owned_weakrefs_dead": True,
                       "registry_restored": True,
                       "global_state_restored": True},
        )
        self.assertEqual(MEMORY_ACCEPTED, verdict["outcome"])

    # -- the four negative orders -----------------------------------------

    def test_clearing_before_engine_death_fails_to_release(self) -> None:
        allocator = SyntheticAllocator()
        run = _SyntheticUnload(allocator)
        run.enter_unloading()
        run.close_wrapper()
        run.clear_adapter()
        # The V2/owner-trial mistake: clear while the engine is still live.
        result = allocator.terminal_clear()
        self.assertEqual("blocks_still_live", result["reason"])
        run.release_engine()
        sample = allocator.sample()
        self.assertEqual(0, sample["allocated_bytes"])
        self.assertGreater(sample["reserved_bytes"], 26_214_400)
        self.assertNotEqual(list(REQUIRED_UNLOAD_ORDER), allocator.events)

    def test_dirty_ownership_with_low_reserved_is_never_accepted(self) -> None:
        from forge_headless.memory_report import (
            MEMORY_ACCEPTED,
            OWNERSHIP_STATE_INCONSISTENT,
            classify_memory,
        )

        verdict = classify_memory(
            job1_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            job2_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            post_unload={"status": "ok", "allocated_bytes": 0,
                         "reserved_bytes": 0},
            warm_tolerance_bytes=1_048_576,
            allocated_ceiling_bytes=11_206_656,
            reserved_ceiling_bytes=26_214_400,
            ownership={"owned_weakrefs_dead": False,
                       "registry_restored": True,
                       "global_state_restored": True},
        )
        self.assertNotEqual(MEMORY_ACCEPTED, verdict["outcome"])
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])

    def test_a_missing_terminal_clear_fails_the_reserved_threshold(self) -> None:
        from forge_headless.memory_report import (
            MEMORY_THRESHOLD_EXCEEDED,
            classify_memory,
        )

        allocator = SyntheticAllocator()
        run = _SyntheticUnload(allocator)
        run.enter_unloading()
        run.close_wrapper()
        run.clear_adapter()
        run.release_engine()
        run.restore_sentinel()
        run.restore_registry()
        self.assertTrue(run.assert_weakrefs_dead())
        sample = allocator.sample()  # no terminal clear at all
        self.assertEqual(0, allocator.terminal_clears)
        self.assertNotIn("terminal_cache_clear", allocator.events)
        verdict = classify_memory(
            job1_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            job2_post_release={"status": "ok", "allocated_bytes": 1_000,
                               "reserved_bytes": 2_000},
            post_unload=sample,
            warm_tolerance_bytes=1_048_576,
            allocated_ceiling_bytes=11_206_656,
            reserved_ceiling_bytes=26_214_400,
            ownership={"owned_weakrefs_dead": True,
                       "registry_restored": True,
                       "global_state_restored": True},
        )
        # Exactly the owner-trial outcome this milestone closes.
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])
        self.assertEqual(["post_unload_reserved"],
                         verdict["thresholds_exceeded"])

    def test_a_duplicated_terminal_clear_is_detectable_and_fails(self) -> None:
        run, _sample = self._run_correct_order()
        run.allocator.terminal_clear()
        self.assertEqual(2, run.allocator.terminal_clears)
        self.assertNotEqual(list(REQUIRED_UNLOAD_ORDER),
                            run.allocator.events)
        self.assertEqual(
            2, run.allocator.events.count("terminal_cache_clear")
        )


# ---------------------------------------------------------------------------
# startup purity
# ---------------------------------------------------------------------------

class StartupPurityTests(unittest.TestCase):
    def test_startup_imports_nothing_and_clears_nothing(self) -> None:
        script = r"""
import json, sys
from pathlib import Path
APP_ROOT = Path(sys.argv[1])
sys.path.insert(0, str(APP_ROOT))
import launch_studio
from forge_headless.terminal_release import release_terminal_cache
from forge_studio.composition import build_standalone
from forge_studio.model_profiles import ModelProfile
references = {"checkpoint": "synthetic://c", "text_encoder": "synthetic://t",
              "vae": "synthetic://v"}
composition = build_standalone(
    backend_kind="mock",
    profiles=[ModelProfile(profile_id="alpha", display_name="Alpha",
                            family="qwen-image",
                            payload_references=references)],
    result_root=Path(sys.argv[2]),
)
state = composition.model_lifecycle.state()["state"]
report = release_terminal_cache()
composition.shutdown()
forbidden = sorted(
    name for name in sys.modules
    if name == "torch" or name.startswith("torch.")
    or name == "backend" or name.startswith("backend.")
    or name == "modules" or name.startswith("modules.")
    or name == "gradio" or name.startswith("gradio.")
)
print(json.dumps({"state": state, "forbidden": forbidden,
                  "release": report}))
"""
        scratch = APP_ROOT / "outputs" / f"tvr-purity-{os.getpid()}"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(APP_ROOT / "venv" / "Scripts" / "python.exe"),
                 "-I", "-S", "-B", "-c", script, str(APP_ROOT), str(scratch)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=str(APP_ROOT),
            )
            self.assertEqual(0, completed.returncode, completed.stderr[-800:])
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual("no_model", report["state"])
        self.assertEqual([], report["forbidden"])
        # Importing and CALLING the helper on a clean process must remain a
        # reported no-op -- it is what keeps startup purity intact.
        self.assertFalse(report["release"]["called"])
        self.assertEqual(TORCH_NOT_IMPORTED, report["release"]["reason"])


if __name__ == "__main__":
    unittest.main()
