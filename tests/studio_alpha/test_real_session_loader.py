"""Contracts for the real session loader and product lifecycle activation.

Phase 1 left the loader as a hole: every test injected a synthetic one and
production had none, so the lifecycle was real but inert. This suite drives the
**real** `HeadlessSessionLoader` -- not a fake of it -- with every step injected,
which is what lets the whole failure matrix run at full fidelity with no payload
opened and no device touched.

The activation half proves the thing Phase 1 could not: a generation refuses
when nothing is warm, leases the session the lifecycle owns when something is,
and never becomes the thing that opens a model.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no generation, no server.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.session_loader import (  # noqa: E402
    LOAD_STEPS,
    MODEL_LOAD_CANCELLED,
    MODEL_LOAD_FAILED,
    MODEL_LOAD_NOT_CONFIGURED,
    ROLE_ORDER,
    HeadlessSessionLoader,
    LoadedStudioSession,
    SessionLoadError,
    StudioSessionLoader,
)
from forge_studio.composition import build_standalone  # noqa: E402
from forge_studio.model_lifecycle import (  # noqa: E402
    MODEL_NOT_READY,
    ModelLifecycleState,
    WarmSessionManager,
)
from forge_studio.model_profiles import ModelProfile, ModelProfileRepository  # noqa: E402
from forge_studio.model_service import ModelLifecycleService  # noqa: E402

EXPECTED_LOADER_TESTS = 58

S = ModelLifecycleState


def _refusal_code(case: unittest.TestCase, call: object) -> str:
    """Return the error code of the studio refusal raised by `call`.

    Matched on shape, never on class identity. `test_import_boundaries` purges
    `forge_studio` from `sys.modules`, so a deferred re-import inside the
    composition root can produce a *second* `StudioError` class object; an
    `assertRaises(StudioError)` bound at import time would then miss the very
    refusal it is asserting, but only under the canonical runner.
    """

    try:
        call()  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 - identity is what we cannot trust
        code = getattr(getattr(exc, "error", None), "code", None)
        if code is None:
            raise
        return str(code)
    case.fail("expected a studio refusal, but the call returned")
    raise AssertionError  # pragma: no cover - fail() always raises

_SECRET = "Z:/private/alpha-checkpoint.safetensors"


def profile(profile_id: str = "a") -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        display_name=f"Alpha {profile_id}",
        family="anima",
        payload_references={
            "checkpoint": f"{_SECRET}#{profile_id}",
            "text_encoder": f"{_SECRET}#te",
            "vae": f"{_SECRET}#vae",
        },
    )


class Recorder:
    """Injected synthetic dependencies that record what the real loader did."""

    def __init__(self, *, fail_at: str | None = None, cancel_after: str | None = None):
        self.fail_at = fail_at
        self.cancel_after = cancel_after
        self.events: list[str] = []
        self.opened_roles: list[str] = []
        self.released: list[str] = []
        self.cancelled = False
        self.engine = None
        self.session = None
        self.port = None

    # -- cancellation probe ------------------------------------------------

    def cancelled_now(self) -> bool:
        return self.cancelled

    def _maybe(self, step: str) -> None:
        self.events.append(step)
        if self.cancel_after == step:
            self.cancelled = True
        if self.fail_at == step:
            raise RuntimeError(f"synthetic failure at {step}")

    # -- injected steps ----------------------------------------------------

    def startup_globals(self):
        self._maybe("startup_globals")

        class Handle:
            def __init__(self, owner):
                self._owner = owner

            def restore(self):
                self._owner.released.append("startup_globals")

        return Handle(self)

    def payload_opener(self, *, profile, references, roles, **_kwargs):
        for role in roles:
            self._maybe(f"open_{role}")
            self.opened_roles.append(role)
        return {role: references[role] for role in roles}

    def engine_builder(self, *, profile, opened):
        self._maybe("engine")
        self.engine = type("SyntheticEngine", (), {"profile_id": profile.profile_id})()
        return self.engine

    def identity_installer(self, engine):
        self._maybe("identity")
        return {"attached": True, "runtime_label": "studio-alpha"}

    def bookkeeping(self, engine):
        self._maybe("bookkeeping")

        class Handle:
            def __init__(self, owner):
                self._owner = owner

            def restore(self):
                self._owner.released.append("bookkeeping")

        return Handle(self)

    def port_factory(self, *, profile, engine, result_root):
        self._maybe("port")
        owner = self

        class Port:
            def release_engine(self):
                owner.released.append("port")
                return {"engine_reference_cleared": True}

        self.port = Port()
        return self.port

    def session_factory(self, *, profile, port, result_root):
        self._maybe("session")
        owner = self

        class Session:
            profile_id = profile.profile_id

            def close(self):
                owner.released.append("session")

        self.session = Session()
        return self.session

    def cleanup(self, *, engine=None):
        self.released.append("engine")

    # -- assembly ----------------------------------------------------------

    def loader(self, **overrides) -> HeadlessSessionLoader:
        kwargs = dict(
            payload_opener=self.payload_opener,
            engine_builder=self.engine_builder,
            identity_installer=self.identity_installer,
            startup_globals=self.startup_globals,
            bookkeeping=self.bookkeeping,
            port_factory=self.port_factory,
            session_factory=self.session_factory,
            cleanup=self.cleanup,
        )
        kwargs.update(overrides)
        return HeadlessSessionLoader(**kwargs)


# ------------------------------------------------------------- loader port


class LoaderPortTests(unittest.TestCase):
    def test_the_port_declares_the_required_surface(self) -> None:
        for name in ("load", "close", "capabilities"):
            self.assertTrue(callable(getattr(StudioSessionLoader, name, None)))

    def test_the_real_loader_implements_the_port(self) -> None:
        self.assertTrue(issubclass(HeadlessSessionLoader, StudioSessionLoader))

    def test_capabilities_opens_nothing(self) -> None:
        recorder = Recorder()
        capabilities = recorder.loader().capabilities(profile())
        self.assertEqual([], recorder.events)
        self.assertEqual(list(ROLE_ORDER), capabilities["roles"])
        self.assertFalse(capabilities["payload_opened"])

    def test_capabilities_carries_no_payload_reference(self) -> None:
        capabilities = Recorder().loader().capabilities(profile())
        self.assertNotIn(_SECRET, json.dumps(capabilities))

    def test_the_declared_step_order_is_fixed(self) -> None:
        self.assertEqual(
            ("cancellation_checked", "startup_globals_installed", "payloads_opened",
             "engine_built", "identity_attached", "bookkeeping_installed",
             "port_built", "session_built"),
            LOAD_STEPS,
        )

    def test_close_is_idempotent(self) -> None:
        recorder = Recorder()
        loader = recorder.loader()
        loaded = loader.load(profile())
        first = loader.close(loaded)
        second = loader.close(loaded)
        self.assertFalse(first["already"])
        self.assertTrue(second["already"])
        self.assertEqual(1, recorder.released.count("session"))

    def test_closing_nothing_is_harmless(self) -> None:
        self.assertTrue(Recorder().loader().close(None)["closed"])

    def test_the_loaded_session_describes_itself_without_a_repr(self) -> None:
        loaded = Recorder().loader().load(profile())
        described = loaded.describe()
        self.assertEqual("a", described["profile_id"])
        self.assertEqual("SyntheticEngine", described["engine_class"])
        self.assertTrue(described["identity_attached"])
        self.assertNotIn(_SECRET, json.dumps(described))

    def test_the_loaded_session_binds_the_profile(self) -> None:
        loaded = Recorder().loader().load(profile("b"))
        self.assertEqual("b", loaded.profile_id)
        self.assertEqual("anima", loaded.family)


# ------------------------------------------------------ loader orchestration


class LoaderOrchestrationTests(unittest.TestCase):
    def test_a_successful_load_runs_every_declared_step_in_order(self) -> None:
        recorder = Recorder()
        loaded = recorder.loader().load(profile())
        self.assertEqual(list(LOAD_STEPS), list(loaded.steps))

    def test_the_three_roles_open_in_a_fixed_order(self) -> None:
        recorder = Recorder()
        recorder.loader().load(profile())
        self.assertEqual(list(ROLE_ORDER), recorder.opened_roles)

    def test_identity_is_attached_before_the_session_is_built(self) -> None:
        recorder = Recorder()
        recorder.loader().load(profile())
        self.assertLess(recorder.events.index("identity"),
                        recorder.events.index("session"))

    def test_identity_is_attached_before_the_port_is_built(self) -> None:
        recorder = Recorder()
        recorder.loader().load(profile())
        self.assertLess(recorder.events.index("identity"),
                        recorder.events.index("port"))

    def test_startup_globals_precede_any_payload_open(self) -> None:
        recorder = Recorder()
        recorder.loader().load(profile())
        self.assertLess(recorder.events.index("startup_globals"),
                        recorder.events.index("open_checkpoint"))

    def test_the_private_references_reach_the_opener_and_go_no_further(self) -> None:
        seen: dict = {}

        def opener(*, profile, references, roles, **_kwargs):
            seen.update(references)
            return dict(references)

        recorder = Recorder()
        loaded = recorder.loader(payload_opener=opener).load(profile())
        self.assertEqual(set(ROLE_ORDER), set(seen))
        self.assertNotIn(_SECRET, json.dumps(loaded.describe()))

    def test_a_profile_without_references_is_refused(self) -> None:
        with self.assertRaises(SessionLoadError) as caught:
            Recorder().loader().load(object())
        self.assertEqual(MODEL_LOAD_FAILED, caught.exception.code)

    def test_a_session_factory_returning_nothing_fails(self) -> None:
        recorder = Recorder()
        with self.assertRaises(SessionLoadError) as caught:
            recorder.loader(session_factory=lambda **_k: None).load(profile())
        self.assertEqual("session_built", caught.exception.step)

    def test_an_engine_builder_returning_nothing_fails(self) -> None:
        recorder = Recorder()
        with self.assertRaises(SessionLoadError) as caught:
            recorder.loader(engine_builder=lambda **_k: None).load(profile())
        self.assertEqual("engine_built", caught.exception.step)

    def test_progress_is_reported_per_step(self) -> None:
        seen: list[str] = []
        Recorder().loader().load(profile(), progress=seen.append)
        self.assertEqual(list(LOAD_STEPS), seen)

    def test_a_raising_progress_sink_cannot_fail_a_load(self) -> None:
        def explode(_step):
            raise RuntimeError("a reporting sink must never fail a load")

        loaded = Recorder().loader().load(profile(), progress=explode)
        self.assertIsNotNone(loaded.session)

    def test_no_retry_after_a_payload_has_been_opened(self) -> None:
        recorder = Recorder()
        loader = recorder.loader()
        loader.load(profile())
        with self.assertRaises(SessionLoadError) as caught:
            loader.load(profile())
        self.assertEqual("single_attempt", caught.exception.step)

    def test_a_failed_load_still_forbids_a_retry(self) -> None:
        recorder = Recorder(fail_at="engine")
        loader = recorder.loader()
        with self.assertRaises(SessionLoadError):
            loader.load(profile())
        with self.assertRaises(SessionLoadError) as caught:
            loader.load(profile())
        self.assertEqual("single_attempt", caught.exception.step)

    def test_an_unconfigured_load_refuses_before_any_payload_access(self) -> None:
        """Phase 2.5 changed what an unconfigured loader reports, not whether.

        Phase 2's four defaults were absent, so an unconfigured load reported
        the generic `MODEL_LOAD_FAILED`. The defaults are now bound, and what
        refuses is the missing controlled access -- a different fact, given a
        code of its own so a caller cannot retry the one thing that can never
        succeed. The refusal, its step, and the untouched payload boundary are
        unchanged.
        """

        loader = HeadlessSessionLoader()
        with self.assertRaises(SessionLoadError) as caught:
            loader.load(profile())
        self.assertEqual(MODEL_LOAD_NOT_CONFIGURED, caught.exception.code)
        self.assertNotEqual(MODEL_LOAD_FAILED, caught.exception.code)
        self.assertEqual("startup_globals_installed", caught.exception.step)
        self.assertFalse(loader.payload_opened)


class CancellationTests(unittest.TestCase):
    def test_cancelling_before_payload_access_opens_nothing(self) -> None:
        recorder = Recorder()
        with self.assertRaises(SessionLoadError) as caught:
            recorder.loader().load(profile(), cancellation=lambda: True)
        self.assertEqual(MODEL_LOAD_CANCELLED, caught.exception.code)
        self.assertEqual("before_payload_access", caught.exception.step)
        self.assertEqual([], recorder.opened_roles)

    def test_a_cancellation_between_steps_stops_the_load(self) -> None:
        recorder = Recorder(cancel_after="startup_globals")
        with self.assertRaises(SessionLoadError) as caught:
            recorder.loader().load(profile(), cancellation=recorder.cancelled_now)
        self.assertEqual(MODEL_LOAD_CANCELLED, caught.exception.code)
        self.assertEqual([], recorder.opened_roles)

    def test_a_cancellation_after_the_engine_releases_what_was_acquired(self) -> None:
        recorder = Recorder(cancel_after="engine")
        with self.assertRaises(SessionLoadError):
            recorder.loader().load(profile(), cancellation=recorder.cancelled_now)
        self.assertIn("engine", recorder.released)
        self.assertIn("startup_globals", recorder.released)

    def test_an_event_style_cancellation_probe_is_accepted(self) -> None:
        class Event:
            @staticmethod
            def is_set():
                return True

        with self.assertRaises(SessionLoadError) as caught:
            Recorder().loader().load(profile(), cancellation=Event())
        self.assertEqual(MODEL_LOAD_CANCELLED, caught.exception.code)

    def test_a_refusing_cancellation_probe_is_not_a_cancellation(self) -> None:
        def explode():
            raise RuntimeError("probe failed")

        loaded = Recorder().loader().load(profile(), cancellation=explode)
        self.assertIsNotNone(loaded.session)


class PartialFailureTests(unittest.TestCase):
    CASES = (
        ("startup_globals", []),
        ("open_checkpoint", ["startup_globals"]),
        ("open_text_encoder", ["startup_globals"]),
        ("open_vae", ["startup_globals"]),
        ("engine", ["startup_globals"]),
        ("identity", ["engine", "startup_globals"]),
        ("bookkeeping", ["engine", "startup_globals"]),
        ("port", ["engine", "startup_globals"]),
        ("session", ["engine", "startup_globals"]),
    )

    def test_every_failure_boundary_releases_what_it_acquired(self) -> None:
        for step, expected in self.CASES:
            with self.subTest(step=step):
                recorder = Recorder(fail_at=step)
                with self.assertRaises(SessionLoadError):
                    recorder.loader().load(profile())
                for released in expected:
                    self.assertIn(released, recorder.released)

    def test_every_failure_carries_a_stable_scalar_error(self) -> None:
        for step, _expected in self.CASES:
            with self.subTest(step=step):
                recorder = Recorder(fail_at=step)
                with self.assertRaises(SessionLoadError) as caught:
                    recorder.loader().load(profile())
                error = caught.exception
                self.assertEqual(MODEL_LOAD_FAILED, error.code)
                self.assertNotIn("Traceback", error.message)
                self.assertNotIn(_SECRET, error.message)
                self.assertIn("RuntimeError", error.message)

    def test_a_failure_after_the_session_still_closes_it(self) -> None:
        recorder = Recorder()

        def failing_session(**kwargs):
            recorder.session_factory(**kwargs)
            raise RuntimeError("publication failed")

        with self.assertRaises(SessionLoadError):
            recorder.loader(session_factory=failing_session).load(profile())
        self.assertIn("engine", recorder.released)

    def test_a_cleanup_that_raises_does_not_mask_the_original_failure(self) -> None:
        def explode(**_kwargs):
            raise RuntimeError("cleanup failed")

        recorder = Recorder(fail_at="port")
        with self.assertRaises(SessionLoadError) as caught:
            recorder.loader(cleanup=explode).load(profile())
        self.assertEqual("port_built", caught.exception.step)

    def test_no_public_path_leaks_from_any_failure(self) -> None:
        for step, _expected in self.CASES:
            recorder = Recorder(fail_at=step)
            with self.assertRaises(SessionLoadError) as caught:
                recorder.loader().load(profile())
            self.assertNotIn(_SECRET, json.dumps(caught.exception.to_dict()))


# ---------------------------------------------------- lifecycle activation


def wired(recorder: Recorder | None = None, *, profiles=("a",)):
    """A composition whose lifecycle owns the session, with a real loader."""

    recorder = recorder or Recorder()
    loader = recorder.loader()
    sessions: list = []

    def load(model_profile):
        loaded = loader.load(model_profile)
        sessions.append(loaded)
        return loaded.session

    def close(session):
        for loaded in sessions:
            if loaded.session is session:
                loaded.close()

    composition = build_standalone(
        backend_kind="headless",
        profiles=[profile(name) for name in profiles],
        session_loader=load,
        session_closer=close,
    )
    return composition, recorder, sessions


class ApplicationActivationTests(unittest.TestCase):
    def test_generation_is_refused_when_no_model_is_loaded(self) -> None:
        composition, _recorder, _sessions = wired()
        code = _refusal_code(
            self, lambda: composition.application.submit_generation(_request())
        )
        self.assertEqual(MODEL_NOT_READY, code)
        composition.shutdown()

    def test_a_refused_generation_opens_nothing(self) -> None:
        composition, recorder, _sessions = wired()
        _refusal_code(
            self, lambda: composition.application.submit_generation(_request())
        )
        self.assertEqual([], recorder.opened_roles)
        composition.shutdown()

    def test_an_explicit_load_publishes_the_session_into_the_backend(self) -> None:
        composition, recorder, sessions = wired()
        composition.model_lifecycle.ensure_loaded(profile("a"))
        backend = composition.application._backend  # noqa: SLF001
        self.assertIs(sessions[0].session, backend._generation)  # noqa: SLF001
        composition.shutdown()

    def test_unload_clears_the_published_session(self) -> None:
        composition, _recorder, _sessions = wired()
        composition.model_lifecycle.ensure_loaded(profile("a"))
        composition.model_lifecycle.unload()
        backend = composition.application._backend  # noqa: SLF001
        self.assertIsNone(backend._generation)  # noqa: SLF001
        self.assertEqual("no_model", composition.model_lifecycle.state()["state"])
        composition.shutdown()

    def test_shutdown_clears_the_published_session(self) -> None:
        composition, _recorder, _sessions = wired()
        composition.model_lifecycle.ensure_loaded(profile("a"))
        composition.shutdown()
        backend = composition.application._backend  # noqa: SLF001
        self.assertIsNone(backend._generation)  # noqa: SLF001

    def test_the_load_opens_exactly_three_roles(self) -> None:
        composition, recorder, _sessions = wired()
        composition.model_lifecycle.ensure_loaded(profile("a"))
        self.assertEqual(list(ROLE_ORDER), recorder.opened_roles)
        composition.shutdown()

    def test_a_lifecycle_with_no_loader_does_not_gate_generation(self) -> None:
        """The mock host: nothing to load, so nothing to refuse."""

        composition = build_standalone(backend_kind="mock")
        self.assertFalse(composition.model_lifecycle.gates_generation)
        composition.shutdown()

    def test_a_lifecycle_with_a_loader_gates_generation(self) -> None:
        composition, _recorder, _sessions = wired()
        self.assertTrue(composition.model_lifecycle.gates_generation)
        composition.shutdown()


def _request():
    from forge_studio.contracts import GenerationRequest

    return GenerationRequest(
        model_id="a", positive_prompt="x", negative_prompt="",
        seed=1, steps=4, cfg_scale=6.0, width=768, height=768,
    )


class WarmReuseTests(unittest.TestCase):
    def manager(self, recorder: Recorder | None = None):
        recorder = recorder or Recorder()
        loader = recorder.loader()
        loaded: list = []

        def load(model_profile):
            handle = loader.load(model_profile)
            loaded.append(handle)
            return handle.session

        def close(session):
            for handle in loaded:
                if handle.session is session:
                    handle.close()

        manager = WarmSessionManager(
            profiles=ModelProfileRepository([profile("a")]),
            loader=load, closer=close,
        )
        return manager, recorder, loaded

    def test_three_jobs_reuse_one_session_with_no_intermediate_unload(self) -> None:
        manager, recorder, loaded = self.manager()
        manager.ensure_loaded(profile("a"))
        first = manager.session
        for index in range(3):
            token = manager.next_job_token()
            manager.lease(token)
            self.assertIs(first, manager.session)
            manager.release(token)
        self.assertEqual(1, len(loaded))
        self.assertEqual([], recorder.released)
        self.assertIs(S.READY, manager.state)
        manager.unload()
        self.assertIs(S.NO_MODEL, manager.state)

    def test_a_queued_job_waits_and_the_session_is_never_reloaded(self) -> None:
        manager, _recorder, loaded = self.manager()
        manager.ensure_loaded(profile("a"))
        manager.lease("j1")
        self.assertFalse(manager.lease("j2"))
        manager.release("j1")
        self.assertEqual("j2", manager.describe()["active_job"])
        manager.release("j2")
        self.assertEqual(1, len(loaded))

    def test_unload_during_a_job_defers_and_then_closes_once(self) -> None:
        manager, recorder, _loaded = self.manager()
        manager.ensure_loaded(profile("a"))
        manager.lease("j1")
        self.assertEqual("unload", manager.unload()["pending_action"])
        self.assertEqual([], recorder.released)
        manager.release("j1")
        self.assertIn("session", recorder.released)
        self.assertIs(S.NO_MODEL, manager.state)

    def test_unload_releases_every_acquired_resource(self) -> None:
        manager, recorder, _loaded = self.manager()
        manager.ensure_loaded(profile("a"))
        manager.unload()
        for name in ("session", "port", "engine", "bookkeeping", "startup_globals"):
            self.assertIn(name, recorder.released, name)


class SwitchReadinessTests(unittest.TestCase):
    def test_synthetic_switching_stays_overlap_free_with_the_real_loader(self) -> None:
        opened: list[str] = []
        closed: list[str] = []
        live: list[str] = []

        def make(model_profile):
            recorder = Recorder()
            handle = recorder.loader().load(model_profile)
            opened.append(model_profile.profile_id)
            live.append(model_profile.profile_id)
            handle.session._profile_id = model_profile.profile_id
            return handle

        handles: dict = {}

        def load(model_profile):
            if live:
                raise AssertionError("a session was still live when another loaded")
            handle = make(model_profile)
            handles[id(handle.session)] = (model_profile.profile_id, handle)
            return handle.session

        def close(session):
            profile_id, handle = handles.pop(id(session))
            handle.close()
            closed.append(profile_id)
            live.remove(profile_id)

        manager = WarmSessionManager(
            profiles=ModelProfileRepository([profile("a"), profile("b")]),
            loader=load, closer=close,
        )
        manager.ensure_loaded(profile("a"))
        manager.ensure_loaded(profile("b"))
        self.assertEqual(["a", "b"], opened)
        self.assertEqual(["a"], closed)
        self.assertEqual(["b"], live)
        manager.unload()
        self.assertEqual(["a", "b"], closed)

    def test_the_real_loader_is_single_use_so_a_switch_needs_a_new_one(self) -> None:
        """Switch capability lives in the caller, not in one loader instance."""

        recorder = Recorder()
        loader = recorder.loader()
        loader.load(profile("a"))
        with self.assertRaises(SessionLoadError) as caught:
            loader.load(profile("b"))
        self.assertEqual("single_attempt", caught.exception.step)


class ServiceContractTests(unittest.TestCase):
    def test_the_service_reports_state_without_opening_anything(self) -> None:
        composition, recorder, _sessions = wired()
        for payload in (composition.model_lifecycle.state(),
                        composition.model_lifecycle.readiness()):
            self.assertNotIn(_SECRET, json.dumps(payload))
        self.assertEqual([], recorder.opened_roles)
        composition.shutdown()

    def test_reading_status_never_loads_a_model(self) -> None:
        composition, recorder, _sessions = wired()
        for _ in range(3):
            composition.model_lifecycle.state()
            composition.model_lifecycle.readiness()
        self.assertEqual([], recorder.opened_roles)
        composition.shutdown()

    def test_nothing_opens_until_a_job_asks_for_a_model(self) -> None:
        # Was "selection is model-free and load is the boundary". Selecting is
        # no longer a server-side act at all, so the boundary moved to the
        # thing that actually needs a model: asking for one.
        composition, recorder, _sessions = wired()
        self.assertEqual([], recorder.opened_roles)
        composition.model_lifecycle.ensure_loaded(profile("a"))
        self.assertEqual(3, len(recorder.opened_roles))
        composition.shutdown()

    def test_settings_never_carry_a_payload_reference(self) -> None:
        # Was "settings record the selection without the references". There is
        # no server-side selection to record now; what survives, and is the
        # part that mattered, is that settings cannot leak a payload path.
        composition, _recorder, _sessions = wired()
        composition.model_lifecycle.ensure_loaded(profile("a"))
        described = composition.settings.describe()
        self.assertNotIn(_SECRET, json.dumps(described))
        composition.shutdown()

    def test_autoload_remains_off_by_default(self) -> None:
        composition, recorder, _sessions = wired()
        self.assertFalse(composition.settings.current.autoload)
        self.assertEqual([], recorder.opened_roles)
        composition.shutdown()


# ------------------------------------------------------------ import safety


PROBE = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import forge_headless.session_loader as loader_module\n"
    "from forge_studio.composition import build_standalone\n"
    "from forge_studio.model_profiles import ModelProfile\n"
    "p = ModelProfile(profile_id='a', display_name='A', family='anima',\n"
    "                 payload_references={'checkpoint': 'x', 'text_encoder': 'y',\n"
    "                                     'vae': 'z'})\n"
    "c = build_standalone(backend_kind='headless', profiles=[p])\n"
    "loader_module.HeadlessSessionLoader().capabilities(p)\n"
    "state = c.model_lifecycle.state()['state']\n"
    "c.shutdown()\n"
    "names = set(sys.modules)\n"
    "print(json.dumps({\n"
    "  'state': state,\n"
    "  'torch': int(any(n == 'torch' or n.startswith('torch.') for n in names)),\n"
    "  'cuda': int(any('cuda' in n for n in names)),\n"
    "  'forge_backend': int(any(n == 'backend' or n.startswith('backend.') for n in names)),\n"
    "  'neo': int(any(n.split('.')[0] in ('modules', 'modules_forge', 'webui') for n in names)),\n"
    "  'gradio': int(any(n.split('.')[0] in ('gradio', 'gradio_client') for n in names)),\n"
    "  'socketserver': int('socketserver' in names),\n"
    "}))\n"
)


class ImportSafetyTests(unittest.TestCase):
    def probe(self) -> dict:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", "-c", PROBE, str(APP_ROOT)],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_headless_construction_pulls_in_nothing_heavy(self) -> None:
        report = self.probe()
        self.assertEqual("no_model", report["state"])
        for key in ("torch", "cuda", "forge_backend", "neo", "gradio", "socketserver"):
            self.assertEqual(0, report[key], f"{key} was imported")

    def test_the_loader_module_defers_every_heavy_import(self) -> None:
        import ast

        path = APP_ROOT / "forge_headless" / "session_loader.py"
        forbidden = {"torch", "backend", "modules", "modules_forge", "webui", "gradio"}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden)

    def test_the_loader_module_embeds_no_private_path(self) -> None:
        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        for leak in ("Private-Local", ".safetensors", "C:\\Users", "/Users/"):
            self.assertNotIn(leak, source)

    def test_the_loader_does_not_import_a_diagnostic(self) -> None:
        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        for diagnostic in ("first_image_probe", "studio_service_smoke",
                           "generation_residual", "run_smoke"):
            self.assertNotIn(diagnostic, source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_LOADER_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("STATIC_IMPORT_SCOPE", __doc__ or "")
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
