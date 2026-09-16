"""Contracts for the three usable-alpha lifecycle seams.

Every prior suite tested a component. All three defects this suite exists for
lived *between* components, and all three shipped through 1259 passing tests:

```text
the lifecycle published LoadedStudioSession where the adapter needs the inner
HeadlessGenerationSession -- both objects correct, the seam wrong

begin_job() recorded a queue position and ran anyway -- lease() correct, the
caller wrong, and two generations reached one warm engine

result_root defaulted to None and the port did Path(result_root) -- both
reasonable alone, and the load failed at step 7 of 8 after every payload was
open
```

So this suite drives the real objects **together**: the real composition, the
real loader defaults, the real adapter, the real queue. Only terminal live
operations are intercepted. A test that exercised any of these in isolation
would have passed before the fixes, which is exactly the point.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no image, no server.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.live_bindings import LoadConfiguration  # noqa: E402
from forge_headless.session_loader import (  # noqa: E402
    RESULT_ROOT_NOT_CONFIGURED,
    HeadlessSessionLoader,
)
from forge_studio import GenerationRequest  # noqa: E402
from forge_studio.composition import (  # noqa: E402
    _adapter_session,
    _looks_like_generation_session,
    build_standalone,
)
from forge_studio.model_lifecycle import MODEL_JOB_CANCELLED  # noqa: E402
from forge_studio.presentation import StudioPresentation  # noqa: E402

EXPECTED_SEAM_TESTS = 50


# ------------------------------------------------------------------ helpers


class GatedNeo(B.NeoStub):
    """A NeoStub whose generation can be held open inside the port."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()
        self.concurrent = 0
        self.max_concurrent = 0
        self.order: list[int] = []
        self._lk = threading.Lock()

    def _process_images_inner(self, processing):
        with self._lk:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.order.append(int(getattr(processing, "seed", -1)))
        self.entered.set()
        try:
            self.gate.wait(timeout=30)
            return super()._process_images_inner(processing)
        finally:
            with self._lk:
                self.concurrent -= 1


def payload(seed: int) -> dict[str, object]:
    """Exactly the eight allowlisted transport fields."""

    return {
        "model": "alpha", "positive_prompt": "p", "negative_prompt": "",
        "width": 768, "height": 768, "steps": 12, "cfg_scale": 6.0, "seed": seed,
    }


class Wired:
    """The real composition, wired the way production wires it."""

    def __init__(self, *, result_root: object = "auto", profiles=("alpha",)) -> None:
        self.neo = GatedNeo().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self._tmp = None
        if result_root == "auto":
            import tempfile

            self._tmp = tempfile.TemporaryDirectory()
            result_root = Path(self._tmp.name)
        self.composition = build_standalone(
            backend_kind="headless",
            profiles=[B._profile(name) for name in profiles],
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=B.FakeAuthorization()
            ),
            result_root=result_root,
        )
        self.lifecycle = self.composition.model_lifecycle
        self.presentation = StudioPresentation(
            self.composition.application, GenerationRequest
        )

    @property
    def adapter(self):
        return self.composition.application._backend  # noqa: SLF001

    def load(self, name: str = "alpha") -> None:
        self.lifecycle.ensure_loaded(B._profile(name))

    def submit_on_thread(self, seed: int, sink: dict) -> threading.Thread:
        def run() -> None:
            try:
                sink["out"] = self.presentation.submit(payload(seed))
            except BaseException as exc:  # noqa: BLE001
                sink["error"] = exc
                sink["code"] = getattr(getattr(exc, "error", None), "code", "")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        try:
            self.composition.shutdown()
        except BaseException:  # noqa: BLE001
            pass
        self.intercepts.__exit__(None, None, None)
        self.neo.restore()
        if self._tmp is not None:
            self._tmp.cleanup()


# ------------------------------------------------------- Fix A: the seam


class AdapterSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = Wired()
        self.addCleanup(self.wired.close)

    def test_the_adapter_receives_the_inner_session(self) -> None:
        self.wired.load()
        self.assertIs(
            self.wired.lifecycle.session.session, self.wired.adapter._generation
        )

    def test_the_lifecycle_retains_the_wrapper(self) -> None:
        self.wired.load()
        wrapper = self.wired.lifecycle.session
        self.assertTrue(hasattr(wrapper, "close"))
        self.assertTrue(hasattr(wrapper, "port"))
        self.assertIsNot(wrapper, self.wired.adapter._generation)

    def test_the_published_session_answers_what_the_adapter_asks(self) -> None:
        self.wired.load()
        published = self.wired.adapter._generation
        for name in ("resident_model", "submit", "close"):
            self.assertTrue(hasattr(published, name), name)

    def test_an_ordinary_generation_reaches_the_inner_session(self) -> None:
        self.wired.load()
        out = self.wired.presentation.submit(payload(1))
        self.assertEqual("completed", out["state"])
        self.assertEqual(1, self.wired.neo.process_calls)

    def test_unload_clears_the_adapter_attachment(self) -> None:
        self.wired.load()
        self.wired.lifecycle.unload()
        self.assertIsNone(self.wired.adapter._generation)

    def test_the_wrapper_closes_exactly_once(self) -> None:
        self.wired.load()
        wrapper = self.wired.lifecycle.session
        self.wired.lifecycle.unload()
        self.assertTrue(wrapper.closed)
        # A second close must be a no-op, not a second release.
        self.assertTrue(wrapper.close()["already"])

    def test_shutdown_clears_the_adapter_attachment(self) -> None:
        self.wired.load()
        self.wired.composition.shutdown()
        self.assertIsNone(self.wired.adapter._generation)

    # -- the structural guard itself ---------------------------------------

    def test_publishing_the_wrapper_fails_the_seam_check(self) -> None:
        """The regression: the wrapper must not satisfy the adapter surface."""

        self.wired.load()
        wrapper = self.wired.lifecycle.session
        self.assertFalse(_looks_like_generation_session(wrapper))

    def test_the_inner_session_satisfies_the_seam_check(self) -> None:
        self.wired.load()
        self.assertTrue(
            _looks_like_generation_session(self.wired.lifecycle.session.session)
        )

    def test_unwrapping_is_by_surface_not_by_class(self) -> None:
        class Wrapper:
            def __init__(self, session) -> None:
                self.session = session

        class Inner:
            resident_model = object()

            def submit(self, request):  # pragma: no cover - never called
                return None

            def close(self):  # pragma: no cover
                return None

        inner = Inner()
        self.assertIs(inner, _adapter_session(Wrapper(inner)))

    def test_an_already_inner_session_passes_through(self) -> None:
        class Inner:
            resident_model = object()
            session = None

            def submit(self, request):  # pragma: no cover
                return None

            def close(self):  # pragma: no cover
                return None

        inner = Inner()
        self.assertIs(inner, _adapter_session(inner))

    def test_none_stays_none(self) -> None:
        self.assertIsNone(_adapter_session(None))
        self.assertFalse(_looks_like_generation_session(None))


# ------------------------------------------------------ Fix B: the queue


class QueueGatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = Wired()
        self.addCleanup(self.wired.close)
        self.wired.load()
        self.neo = self.wired.neo

    def _block_job_one(self) -> tuple[threading.Thread, dict]:
        self.neo.gate.clear()
        self.neo.entered.clear()
        sink: dict = {}
        thread = self.wired.submit_on_thread(1, sink)
        self.assertTrue(self.neo.entered.wait(timeout=20), "job 1 never began")
        return thread, sink

    def test_a_second_job_does_not_reach_the_port(self) -> None:
        t1, s1 = self._block_job_one()
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(1.0)
        # `max_concurrent` counts ENTRIES into the port, which is the question:
        # `process_calls` only increments after the gate releases, so it would
        # read zero here whether or not the second job had entered.
        self.assertEqual(1, self.neo.max_concurrent)
        self.assertEqual(1, self.wired.lifecycle.state()["queued_jobs"])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_only_one_generation_is_ever_concurrent(self) -> None:
        t1, _s1 = self._block_job_one()
        threads = [t1]
        sinks = []
        for seed in (2, 3):
            sink: dict = {}
            sinks.append(sink)
            threads.append(self.wired.submit_on_thread(seed, sink))
            time.sleep(0.3)
        self.neo.gate.set()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(1, self.neo.max_concurrent)
        self.assertEqual(3, self.neo.process_calls)

    def test_promotion_is_fifo(self) -> None:
        t1, _s1 = self._block_job_one()
        threads = [t1]
        for seed in (2, 3):
            sink: dict = {}
            threads.append(self.wired.submit_on_thread(seed, sink))
            time.sleep(0.4)
        self.assertEqual(2, self.wired.lifecycle.state()["queued_jobs"])
        self.neo.gate.set()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual([1, 2, 3], self.neo.order)

    def test_a_queued_job_is_visible_as_a_token(self) -> None:
        t1, _s1 = self._block_job_one()
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(1.0)
        tokens = self.wired.lifecycle.queued_jobs()
        self.assertEqual(1, len(tokens))
        self.assertTrue(all(isinstance(token, str) for token in tokens))
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_queued_tokens_carry_no_path_or_prompt(self) -> None:
        t1, _s1 = self._block_job_one()
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(1.0)
        rendered = repr(self.wired.lifecycle.queued_jobs())
        self.assertNotIn("synthetic://", rendered)
        self.assertNotIn(str(APP_ROOT), rendered)
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_release_runs_only_for_jobs_that_reached_the_port(self) -> None:
        t1, _s1 = self._block_job_one()
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(0.8)
        tokens = self.wired.lifecycle.queued_jobs()
        self.wired.lifecycle.cancel_queued_job(tokens[-1])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        port = self.wired.lifecycle.session.port
        self.assertEqual(1, port.generate_calls)
        self.assertEqual(1, port.release_calls)

    def test_the_state_reports_busy_while_a_job_runs(self) -> None:
        t1, _s1 = self._block_job_one()
        self.assertEqual("busy", self.wired.lifecycle.state()["state"])
        self.neo.gate.set()
        t1.join(timeout=30)
        self.assertEqual("ready", self.wired.lifecycle.state()["state"])

    def test_the_queue_drains_to_ready(self) -> None:
        t1, _s1 = self._block_job_one()
        threads = [t1]
        for seed in (2, 3):
            threads.append(self.wired.submit_on_thread(seed, {}))
            time.sleep(0.3)
        self.neo.gate.set()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual("ready", self.wired.lifecycle.state()["state"])
        self.assertEqual(0, self.wired.lifecycle.state()["queued_jobs"])


class QueuedCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = Wired()
        self.addCleanup(self.wired.close)
        self.wired.load()
        self.neo = self.wired.neo

    def _running_plus_queued(self):
        self.neo.gate.clear()
        self.neo.entered.clear()
        s1: dict = {}
        t1 = self.wired.submit_on_thread(1, s1)
        self.assertTrue(self.neo.entered.wait(timeout=20))
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(1.0)
        return t1, s1, t2, s2

    def test_a_queued_job_can_be_cancelled(self) -> None:
        t1, _s1, t2, _s2 = self._running_plus_queued()
        tokens = self.wired.lifecycle.queued_jobs()
        self.assertTrue(self.wired.lifecycle.cancel_queued_job(tokens[0]))
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_a_cancelled_queued_job_never_reaches_the_port(self) -> None:
        t1, _s1, t2, _s2 = self._running_plus_queued()
        tokens = self.wired.lifecycle.queued_jobs()
        self.wired.lifecycle.cancel_queued_job(tokens[0])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        self.assertEqual(1, self.neo.process_calls)

    def test_a_cancelled_queued_job_reports_a_stable_code(self) -> None:
        t1, _s1, t2, s2 = self._running_plus_queued()
        tokens = self.wired.lifecycle.queued_jobs()
        self.wired.lifecycle.cancel_queued_job(tokens[0])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        self.assertEqual(MODEL_JOB_CANCELLED, s2.get("code"))

    def test_cancelling_an_unknown_token_is_false(self) -> None:
        self.assertFalse(self.wired.lifecycle.cancel_queued_job("studio-job-999999"))

    def test_cancelling_an_active_job_is_refused_here(self) -> None:
        """Active cancellation is a different operation and is not done here."""

        t1, _s1, t2, _s2 = self._running_plus_queued()
        active = self.wired.lifecycle.state().get("active_job")
        self.assertIsNotNone(active)
        self.assertFalse(self.wired.lifecycle.cancel_queued_job(str(active)))
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_a_cancelled_job_does_not_hold_the_session(self) -> None:
        t1, _s1, t2, _s2 = self._running_plus_queued()
        tokens = self.wired.lifecycle.queued_jobs()
        self.wired.lifecycle.cancel_queued_job(tokens[0])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        self.assertEqual("ready", self.wired.lifecycle.state()["state"])
        self.assertEqual(0, self.wired.lifecycle.state()["queued_jobs"])

    def test_the_counter_records_the_queued_cancellation(self) -> None:
        t1, _s1, t2, _s2 = self._running_plus_queued()
        tokens = self.wired.lifecycle.queued_jobs()
        self.wired.lifecycle.cancel_queued_job(tokens[0])
        self.neo.gate.set()
        t1.join(timeout=30)
        t2.join(timeout=30)
        counters = self.wired.lifecycle.state()["counters"]
        self.assertEqual(1, counters["queued_cancellations"])


class QueueTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = Wired()
        self.addCleanup(self.wired.close)
        self.wired.load()
        self.neo = self.wired.neo

    def test_shutdown_with_an_active_and_a_queued_job_is_bounded(self) -> None:
        self.neo.gate.clear()
        self.neo.entered.clear()
        t1 = self.wired.submit_on_thread(1, {})
        self.assertTrue(self.neo.entered.wait(timeout=20))
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(0.8)

        started = time.monotonic()
        shutdown = threading.Thread(
            target=lambda: self.wired.composition.shutdown(), daemon=True
        )
        shutdown.start()
        self.neo.gate.set()
        shutdown.join(timeout=30)
        self.assertFalse(shutdown.is_alive(), "shutdown did not return")
        self.assertLess(time.monotonic() - started, 30.0)
        t1.join(timeout=30)
        t2.join(timeout=30)

    def test_a_waiting_job_is_woken_by_shutdown(self) -> None:
        self.neo.gate.clear()
        self.neo.entered.clear()
        t1 = self.wired.submit_on_thread(1, {})
        self.assertTrue(self.neo.entered.wait(timeout=20))
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        time.sleep(0.8)
        shutdown = threading.Thread(
            target=lambda: self.wired.composition.shutdown(), daemon=True
        )
        shutdown.start()
        self.neo.gate.set()
        shutdown.join(timeout=30)
        t1.join(timeout=30)
        t2.join(timeout=30)
        self.assertFalse(t2.is_alive(), "the queued job was left blocked")
        self.assertIn("error", s2)

    def test_unload_while_busy_is_deferred_not_forced(self) -> None:
        self.neo.gate.clear()
        self.neo.entered.clear()
        t1 = self.wired.submit_on_thread(1, {})
        self.assertTrue(self.neo.entered.wait(timeout=20))
        self.wired.lifecycle.unload()
        self.assertEqual("unload", self.wired.lifecycle.state()["pending_action"])
        self.neo.gate.set()
        t1.join(timeout=30)
        time.sleep(0.5)
        self.assertEqual("no_model", self.wired.lifecycle.state()["state"])

    def test_a_job_is_refused_while_an_unload_is_pending(self) -> None:
        self.neo.gate.clear()
        self.neo.entered.clear()
        t1 = self.wired.submit_on_thread(1, {})
        self.assertTrue(self.neo.entered.wait(timeout=20))
        self.wired.lifecycle.unload()
        s2: dict = {}
        t2 = self.wired.submit_on_thread(2, s2)
        t2.join(timeout=20)
        self.assertIn("error", s2)
        self.neo.gate.set()
        t1.join(timeout=30)


# ------------------------------------------------ Fix C: the result root


class ResultRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self.addCleanup(self.neo.restore)
        self.addCleanup(lambda: self.intercepts.__exit__(None, None, None))
        self.configuration = LoadConfiguration(
            repository_root=APP_ROOT, authorization=B.FakeAuthorization()
        )

    def _loader(self, root):
        return HeadlessSessionLoader(
            result_root=root, load_configuration=self.configuration
        )

    def test_a_missing_root_refuses_before_any_payload(self) -> None:
        loader = self._loader(None)
        with self.assertRaises(Exception) as caught:
            loader.load(B._profile())
        self.assertEqual(RESULT_ROOT_NOT_CONFIGURED, caught.exception.code)
        self.assertEqual("result_root_configured", caught.exception.step)
        self.assertFalse(loader.payload_opened)
        self.assertEqual(0, self.neo.loader_calls)

    def test_an_empty_root_refuses(self) -> None:
        loader = self._loader("   ")
        with self.assertRaises(Exception) as caught:
            loader.load(B._profile())
        self.assertEqual(RESULT_ROOT_NOT_CONFIGURED, caught.exception.code)
        self.assertFalse(loader.payload_opened)

    def test_an_unusable_root_refuses(self) -> None:
        loader = self._loader(object())
        with self.assertRaises(Exception) as caught:
            loader.load(B._profile())
        self.assertEqual(RESULT_ROOT_NOT_CONFIGURED, caught.exception.code)
        self.assertFalse(loader.payload_opened)

    def test_a_valid_contained_root_loads(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            loaded = self._loader(Path(tmp)).load(B._profile())
            self.addCleanup(loaded.close)
            self.assertTrue(loaded.describe()["session_configured"])

    def test_the_refusal_carries_no_path(self) -> None:
        loader = self._loader(None)
        with self.assertRaises(Exception) as caught:
            loader.load(B._profile())
        self.assertNotIn(str(APP_ROOT), str(caught.exception))
        self.assertNotIn("synthetic://", str(caught.exception))

    def test_an_injected_port_needs_no_result_root(self) -> None:
        """A caller with its own port owns its own publication."""

        loader = HeadlessSessionLoader(
            result_root=None,
            load_configuration=self.configuration,
            port_factory=lambda **_k: object(),
        )
        loaded = loader.load(B._profile())
        self.addCleanup(loaded.close)
        self.assertTrue(loaded.describe()["port_configured"])

    def test_a_missing_authority_is_reported_before_the_result_root(self) -> None:
        """The more fundamental refusal wins, and still precedes any payload."""

        loader = HeadlessSessionLoader(result_root=None)
        with self.assertRaises(Exception) as caught:
            loader.load(B._profile())
        self.assertNotEqual(RESULT_ROOT_NOT_CONFIGURED, caught.exception.code)
        self.assertFalse(loader.payload_opened)

    def test_no_system_temp_or_environment_fallback_is_used(self) -> None:
        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        for banned in ("tempfile", "gettempdir", "os.environ", "getenv"):
            self.assertNotIn(banned, source, banned)


# -------------------------------------------------- ownership invariants


class OwnershipInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = Wired()
        self.addCleanup(self.wired.close)
        self.wired.load()

    def test_one_session_manager(self) -> None:
        service = self.wired.lifecycle
        self.assertIs(service._manager, self.wired.composition._model_lifecycle._manager)

    def test_one_generation_session(self) -> None:
        self.assertIs(
            self.wired.lifecycle.session.session, self.wired.adapter._generation
        )

    def test_one_engine_owner(self) -> None:
        wrapper = self.wired.lifecycle.session
        self.assertIs(wrapper.engine, wrapper.port._engine)  # noqa: SLF001

    def test_the_adapter_does_not_close_the_lifecycle_session(self) -> None:
        """The wrapper owns the release; the adapter must not duplicate it."""

        self.wired.load.__self__  # noqa: B018 - readability only
        wrapper = self.wired.lifecycle.session
        port = wrapper.port
        self.wired.adapter.shutdown()
        self.assertFalse(port.engine_released)
        self.assertFalse(wrapper.closed)

    def test_unload_releases_the_engine_exactly_once(self) -> None:
        wrapper = self.wired.lifecycle.session
        port = wrapper.port
        self.wired.lifecycle.unload()
        self.assertTrue(port.engine_released)
        self.assertTrue(wrapper.closed)

    def test_the_profile_repository_is_shared(self) -> None:
        self.assertIs(
            self.wired.composition.profiles,
            self.wired.lifecycle._profiles,
        )


# ----------------------------------------------------------- import safety


class SeamImportSafetyTests(unittest.TestCase):
    PROBE = """
import sys, os
sys.path.insert(0, os.getcwd())
from forge_studio.composition import build_standalone
c = build_standalone(backend_kind="headless")
c.model_lifecycle.readiness()
c.model_lifecycle.state()
c.shutdown()
heavy = sorted({m.split('.')[0] for m in sys.modules
                if m.split('.')[0] in ('torch','backend','modules','gradio',
                                       'safetensors','socketserver')})
print("HEAVY=" + ",".join(heavy))
"""

    def test_construction_imports_nothing_heavy(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", self.PROBE],
            capture_output=True, text=True, cwd=str(APP_ROOT), timeout=180,
        )
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        for line in result.stdout.splitlines():
            if line.startswith("HEAVY="):
                self.assertEqual("", line[len("HEAVY="):])
                return
        self.fail("probe reported no HEAVY line")

    def test_no_diagnostic_reaches_the_changed_product_modules(self) -> None:
        forbidden = (
            "first_image_probe", "studio_service_smoke", "generation_residual",
        )
        for name in (
            APP_ROOT / "forge_studio" / "composition.py",
            APP_ROOT / "forge_studio" / "model_lifecycle.py",
            APP_ROOT / "forge_studio" / "model_service.py",
            APP_ROOT / "forge_headless" / "session_loader.py",
        ):
            source = name.read_text(encoding="utf-8")
            for banned in forbidden:
                self.assertNotIn(banned, source, f"{name.name} -> {banned}")


class CompleteRehearsalTests(unittest.TestCase):
    """The whole §6 path, in one test, against the real objects.

    This is the test whose absence let all three defects ship. It is
    deliberately one long scenario rather than several short ones: the defects
    were interactions, and an interaction is only visible when the steps run in
    order against the same objects.
    """

    def test_the_complete_three_job_lifecycle(self) -> None:
        wired = Wired()
        self.addCleanup(wired.close)
        neo = wired.neo
        counts: dict[str, int] = {}

        # -- no model, then a selection that makes itself resident
        # Was: no model -> select -> assert nothing opened -> explicit load.
        # There is no select and no explicit load; asking for a model is the
        # one act, so the sequence is cold -> ask -> ready, and "nothing opens
        # until asked" is asserted BEFORE the ask rather than between two.
        self.assertEqual("no_model", wired.lifecycle.state()["state"])
        self.assertEqual(0, neo.loader_calls)
        wired.lifecycle.ensure_loaded(B._profile("alpha"))
        self.assertEqual("ready", wired.lifecycle.state()["state"])

        counts["profiles"] = len(wired.composition.profiles)
        counts["loads"] = wired.lifecycle.state()["counters"]["loads"]
        counts["engines"] = neo.loader_calls
        wrapper = wired.lifecycle.session

        # the seam
        self.assertIs(wrapper.session, wired.adapter._generation)

        # -- Job 1 blocks inside the port; Jobs 2 and 3 queue behind it
        neo.gate.clear()
        neo.entered.clear()
        s1: dict = {}
        t1 = wired.submit_on_thread(1, s1)
        self.assertTrue(neo.entered.wait(timeout=20))
        s2: dict = {}
        t2 = wired.submit_on_thread(2, s2)
        time.sleep(0.5)
        s3: dict = {}
        t3 = wired.submit_on_thread(3, s3)
        time.sleep(0.8)

        self.assertEqual(2, wired.lifecycle.state()["queued_jobs"])
        self.assertEqual(1, neo.max_concurrent)

        # -- cancel Job 3 while it waits
        tokens = wired.lifecycle.queued_jobs()
        self.assertEqual(2, len(tokens))
        self.assertTrue(wired.lifecycle.cancel_queued_job(tokens[-1]))

        # -- release Job 1; Job 2 is promoted and completes
        neo.gate.set()
        for thread in (t1, t2, t3):
            thread.join(timeout=30)

        counts["jobs_submitted"] = 3
        counts["jobs_reaching_port"] = neo.process_calls
        counts["jobs_completed"] = sum(1 for s in (s1, s2, s3) if "out" in s)
        counts["jobs_cancelled_queued"] = sum(
            1 for s in (s1, s2, s3) if s.get("code") == MODEL_JOB_CANCELLED
        )
        counts["max_concurrent_port_calls"] = neo.max_concurrent
        counts["per_job_releases"] = wrapper.port.release_calls
        counts["publications"] = wrapper.port.generate_calls

        self.assertEqual(2, counts["jobs_reaching_port"])
        self.assertEqual(2, counts["jobs_completed"])
        self.assertEqual(1, counts["jobs_cancelled_queued"])
        self.assertEqual(1, counts["max_concurrent_port_calls"])
        self.assertEqual(2, counts["per_job_releases"])
        self.assertEqual(2, counts["publications"])
        self.assertEqual([1, 2], neo.order)

        # -- results are durable BEFORE unload
        # The handle comes from a poll, not from submit: submit returns the job
        # identity, and the result is minted when the job reaches completed.
        polls = [wired.presentation.poll(s["out"]["job_id"]) for s in (s1, s2)]
        self.assertTrue(all(p["state"] == "completed" for p in polls))
        handles = [p["result"]["image_handle"] for p in polls]
        before = [wired.presentation.read_result_asset(h).content for h in handles]
        self.assertTrue(all(b[:8].hex() == "89504e470d0a1a0a" for b in before))

        # -- one warm session throughout: no intermediate unload
        self.assertEqual(0, wired.lifecycle.state()["counters"]["unloads"])
        self.assertIs(wrapper, wired.lifecycle.session)

        # -- explicit unload
        wired.lifecycle.unload()
        self.assertEqual("no_model", wired.lifecycle.state()["state"])
        self.assertIsNone(wired.adapter._generation)
        self.assertTrue(wrapper.closed)
        counts["explicit_unloads"] = wired.lifecycle.state()["counters"]["unloads"]

        # -- results are STILL durable after unload
        after = [wired.presentation.read_result_asset(h).content for h in handles]
        self.assertEqual(before, after)

        # -- generation in NO_MODEL is refused, and opens nothing
        opens_before = neo.loader_calls
        with self.assertRaises(Exception):
            wired.presentation.submit(payload(9))
        self.assertEqual(opens_before, neo.loader_calls)

        self.assertEqual(
            {
                "profiles": 1, "loads": 1, "engines": 1,
                "jobs_submitted": 3, "jobs_reaching_port": 2,
                "jobs_completed": 2, "jobs_cancelled_queued": 1,
                "max_concurrent_port_calls": 1, "per_job_releases": 2,
                "publications": 2, "explicit_unloads": 1,
            },
            counts,
        )

    def test_the_rehearsal_fails_if_the_seam_defect_returns(self) -> None:
        """Publishing the wrapper must not silently work."""

        wired = Wired()
        self.addCleanup(wired.close)
        wired.load()
        wired.adapter._generation = wired.lifecycle.session  # the old defect
        with self.assertRaises(Exception):
            wired.presentation.submit(payload(1))


class SuiteIntegrityTests(unittest.TestCase):
    def test_discovered_count_matches_the_declared_constant(self) -> None:
        loaded = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_SEAM_TESTS, loaded.countTestCases())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
