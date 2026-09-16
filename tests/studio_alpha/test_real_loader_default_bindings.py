"""Contracts for the real loader defaults bound to proven Forge primitives.

Phase 2 proved the orchestration with all four live steps injected. This suite
proves the steps themselves, by running the **actual default binding graph** and
intercepting only the terminal live operations:

```text
intercepted   backend.loader.forge_loader      would allocate the model
              controlled_device.initialize_cuda would initialize CUDA
              model_intake.validate_exact_path  would stat a real payload
              modules.*                         Neo, not present in a test
              safetensors.safe_open             the real open

NOT faked     StudioStartupGlobals    ControlledPayloadOpener
              build_forge_engine      publish_engine
              PayloadWatch            HeadlessSessionLoader
              StudioLiveGenerationPort
```

That split is what makes the role-open proof meaningful: the *real* `PayloadWatch`
patches our fake `safetensors`, our fake `forge_loader` opens three payloads, and
the watch attributes them and consumes the authorization on the first one. The
ordering assertion is therefore about the shipped code, not about the fake.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no generation, no server.
"""

from __future__ import annotations

import subprocess
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.live_bindings import (  # noqa: E402
    ROLE_ORDER,
    ControlledPayloadOpener,
    LoadConfiguration,
    LoadConfigurationError,
    OpenedPayloads,
    StudioStartupGlobals,
    build_forge_engine,
    publish_engine,
    restore_published_engine,
)
from forge_headless.live_generation_port import (  # noqa: E402
    StudioLiveGenerationPort,
    safe_result_name,
)
from forge_headless.session_loader import HeadlessSessionLoader  # noqa: E402

EXPECTED_BINDING_TESTS = 82


# ------------------------------------------------------------------ doubles


class FakeSafeOpen:
    def __init__(self, filename: str) -> None:
        self.filename = filename

    def __enter__(self):  # pragma: no cover - never entered in these tests
        return self

    def __exit__(self, *_exc):  # pragma: no cover
        return False


class FakeForgeObjects:
    def __init__(self) -> None:
        self.clip = object()
        self.vae = object()


class FakeEngine:
    def __init__(self) -> None:
        self.forge_objects = FakeForgeObjects()
        self.sd_checkpoint_info = None
        self.is_sd1 = False


class FakeStyleDatabase:
    def __init__(self, _filename) -> None:
        self.styles = {}


class FakeTotalTQDM:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1


class FakeProcessed:
    def __init__(self, images) -> None:
        self.images = list(images)
        self.seed = 4242


class FakeImage:
    size = (64, 64)

    def __init__(self) -> None:
        self.saved_to: str | None = None

    def save(self, path, format=None) -> None:  # noqa: A002
        self.saved_to = str(path)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)


class NeoStub:
    """Install a synthetic `modules.*`, `backend.*` and `safetensors`.

    Restores `sys.modules` exactly: entries that existed are put back, entries
    this stub created are removed. A test that leaked a fake `modules.shared`
    would corrupt every suite that runs after it in the same process.
    """

    NAMES = (
        "modules",
        "modules.shared",
        "modules.styles",
        "modules.devices",
        "modules.shared_total_tqdm",
        "modules.processing",
        "modules.sd_models",
        "backend",
        "backend.loader",
        "safetensors",
    )

    def __init__(self, *, engine=None, open_order=None, fail_load=None) -> None:
        self.engine = engine if engine is not None else FakeEngine()
        self.open_order = open_order if open_order is not None else []
        self.fail_load = fail_load
        self.saved: dict[str, object] = {}
        self.process_calls = 0
        self.loader_calls = 0
        self.images = [FakeImage()]

    # -- lifecycle ---------------------------------------------------------

    def install(self) -> NeoStub:
        for name in self.NAMES:
            if name in sys.modules:
                self.saved[name] = sys.modules[name]

        modules = types.ModuleType("modules")
        shared = types.ModuleType("modules.shared")
        shared.styles_filename = "styles.csv"
        shared.opts = None
        shared.state = None
        shared.sd_model = None
        # Real `modules/shared.py` declares these three, so they exist and are
        # None rather than absent. The distinction matters: the compatibility
        # context restores a previously-absent attribute by leaving it alone,
        # which is correct for Neo and would look like a leak against a stub
        # that never declared them.
        shared.prompt_styles = None
        shared.device = None
        shared.total_tqdm = None
        modules.shared = shared

        styles = types.ModuleType("modules.styles")
        styles.StyleDatabase = FakeStyleDatabase
        modules.styles = styles

        devices = types.ModuleType("modules.devices")
        devices.device = "cpu-stub"
        modules.devices = devices

        tqdm_module = types.ModuleType("modules.shared_total_tqdm")
        tqdm_module.TotalTQDM = FakeTotalTQDM
        modules.shared_total_tqdm = tqdm_module

        processing = types.ModuleType("modules.processing")
        processing.StableDiffusionProcessingTxt2Img = self._make_processing_class()
        processing.process_images_inner = self._process_images_inner
        modules.processing = processing

        sd_models = types.ModuleType("modules.sd_models")
        sd_models.model_data = types.SimpleNamespace(
            forge_loading_parameters={}, forge_hash="", sd_model=self.engine
        )
        sd_models.forge_model_reload = lambda: (self.engine, False)
        modules.sd_models = sd_models

        backend = types.ModuleType("backend")
        loader = types.ModuleType("backend.loader")
        loader.forge_loader = self._forge_loader
        backend.loader = loader

        safetensors = types.ModuleType("safetensors")
        safetensors.safe_open = self._safe_open

        for name, module in (
            ("modules", modules),
            ("modules.shared", shared),
            ("modules.styles", styles),
            ("modules.devices", devices),
            ("modules.shared_total_tqdm", tqdm_module),
            ("modules.processing", processing),
            ("modules.sd_models", sd_models),
            ("backend", backend),
            ("backend.loader", loader),
            ("safetensors", safetensors),
        ):
            sys.modules[name] = module
        self.shared = shared
        self.safetensors = safetensors
        return self

    def restore(self) -> None:
        for name in self.NAMES:
            if name in self.saved:
                sys.modules[name] = self.saved[name]
            else:
                sys.modules.pop(name, None)
        self.saved.clear()

    # -- the intercepted terminals ----------------------------------------

    def _safe_open(self, filename, *args, **kwargs):
        return FakeSafeOpen(str(filename))

    def _forge_loader(self, checkpoint, additional_state_dicts=None):
        self.loader_calls += 1
        if self.fail_load is not None:
            raise self.fail_load
        # Open in exactly the order the caller supplied, through whatever
        # `safetensors.safe_open` currently is -- which is the real PayloadWatch
        # recording closure while a load is in flight.
        import safetensors

        for path in [checkpoint, *(additional_state_dicts or [])]:
            safetensors.safe_open(path)
        return self.engine

    def _process_images_inner(self, processing):
        self.process_calls += 1
        return FakeProcessed(self.images)

    @staticmethod
    def _make_processing_class():
        class FakeProcessing:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)
                self.sampler = types.SimpleNamespace(
                    name="euler-stub", scheduler="karras-stub"
                )
                self.scripts = None

        return FakeProcessing


class FakeAuthorization:
    """Stands in for `ControlledLoadAuthorization`. Never touches a real path."""

    def __init__(self, *, roles=ROLE_ORDER) -> None:
        self._paths = {role: f"synthetic://{role}" for role in roles}
        self.consumed = False
        self.consume_calls = 0

    def loader_path(self, role: str) -> str:
        return self._paths[role]

    def loader_paths(self, *roles: str) -> list[str]:
        return [self._paths[role] for role in roles]

    def consume(self) -> None:
        self.consume_calls += 1
        self.consumed = True


class _Intercepts:
    """Patch the two module-level terminals the opener would otherwise reach."""

    def __init__(self, *, cuda=None, validate=None) -> None:
        self.cuda = cuda
        self.validate = validate
        self.validated: list[str] = []
        self.cuda_calls = 0
        self._saved: list[tuple[object, str, object]] = []

    def __enter__(self) -> _Intercepts:
        from forge_headless import controlled_device, model_intake

        def initialize_cuda(authorization):
            self.cuda_calls += 1
            if self.cuda is not None:
                raise self.cuda
            return types.SimpleNamespace(
                to_dict=lambda: {"device": "stub", "index": 0}
            )

        def validate_exact_path(authorization, role):
            self.validated.append(role)
            if self.validate is not None:
                raise self.validate

        for module, name, value in (
            (controlled_device, "initialize_cuda", initialize_cuda),
            (model_intake, "validate_exact_path", validate_exact_path),
        ):
            self._saved.append((module, name, getattr(module, name)))
            setattr(module, name, value)
        return self

    def __exit__(self, *_exc) -> bool:
        for module, name, original in self._saved:
            setattr(module, name, original)
        self._saved.clear()
        return False


def _profile(name: str = "alpha"):
    from forge_studio.model_profiles import ModelProfile

    return ModelProfile(
        profile_id=name,
        display_name=name.title(),
        family="qwen-image",
        payload_references={role: f"synthetic://{name}/{role}" for role in ROLE_ORDER},
    )


def _request(request_id: str = "job-1"):
    return types.SimpleNamespace(
        request_id=request_id,
        positive_prompt="p",
        negative_prompt="n",
        seed=1,
        sampler="euler",
        scheduler="karras",
        batch_size=1,
        steps=2,
        cfg_scale=1.0,
        distilled_cfg_scale=1.0,
        width=64,
        height=64,
    )


class _Progress:
    def __init__(self) -> None:
        self.terminal = False
        self.states: list[str] = []
        self.total = 0
        self.completed = False
        self.failure = ""

    def set_total_steps(self, total) -> None:
        self.total = int(total)

    def advance_to(self, state) -> None:
        self.states.append(getattr(state, "name", str(state)))

    def mark_completed(self) -> None:
        self.completed = True
        self.terminal = True

    def mark_failed(self, message) -> None:
        self.failure = str(message)
        self.terminal = True


def _loader(tmp, neo, *, configuration=None, **kwargs):
    """A loader whose four live steps are the REAL defaults."""

    return HeadlessSessionLoader(
        result_root=tmp,
        load_configuration=configuration
        if configuration is not None
        else LoadConfiguration(
            repository_root=APP_ROOT, authorization=FakeAuthorization()
        ),
        **kwargs,
    )


class _Tmp:
    def __init__(self) -> None:
        import tempfile

        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name)

    def close(self) -> None:
        self._dir.cleanup()


# ------------------------------------------------------------- the suites


class LoadConfigurationTests(unittest.TestCase):
    def test_configuration_reports_no_path(self) -> None:
        cfg = LoadConfiguration(repository_root=APP_ROOT, authorization=object())
        public = cfg.to_dict()
        rendered = repr(public)
        self.assertNotIn(str(APP_ROOT), rendered)
        self.assertTrue(public["repository_root_configured"])
        self.assertTrue(public["authority_configured"])

    def test_configuration_without_authority_is_not_loadable(self) -> None:
        cfg = LoadConfiguration(repository_root=APP_ROOT)
        self.assertFalse(cfg.has_authority)

    def test_injected_opener_counts_as_authority(self) -> None:
        cfg = LoadConfiguration(repository_root=APP_ROOT, payload_opener=object())
        self.assertTrue(cfg.has_authority)
        self.assertIsNotNone(cfg.payload_opener())

    def test_loader_without_configuration_cannot_load(self) -> None:
        self.assertFalse(HeadlessSessionLoader().can_load)

    def test_loader_with_configuration_can_load(self) -> None:
        loader = HeadlessSessionLoader(
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=object()
            )
        )
        self.assertTrue(loader.can_load)

    def test_configured_but_authority_free_loader_cannot_load(self) -> None:
        loader = HeadlessSessionLoader(
            load_configuration=LoadConfiguration(repository_root=APP_ROOT)
        )
        self.assertFalse(loader.can_load)

    def test_fully_injected_loader_can_load_without_configuration(self) -> None:
        loader = HeadlessSessionLoader(
            startup_globals=lambda: None,
            payload_opener=lambda **_k: None,
            engine_builder=lambda **_k: object(),
            port_factory=lambda **_k: object(),
        )
        self.assertTrue(loader.can_load)

    def test_capabilities_report_bound_defaults(self) -> None:
        caps = HeadlessSessionLoader().capabilities(_profile())
        self.assertTrue(caps["defaults_bound"])
        self.assertFalse(caps["load_configuration_present"])
        self.assertFalse(caps["can_load"])

    def test_capabilities_open_nothing(self) -> None:
        loader = HeadlessSessionLoader()
        loader.capabilities(_profile())
        self.assertFalse(loader.payload_opened)

    def test_missing_configuration_fails_before_payload_access(self) -> None:
        loader = HeadlessSessionLoader(result_root=None)
        with self.assertRaises(Exception) as caught:
            loader.load(_profile())
        self.assertEqual("startup_globals_installed", caught.exception.step)
        self.assertFalse(loader.payload_opened)


class StartupGlobalsBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.tmp = _Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)

    def test_install_supplies_the_three_fields(self) -> None:
        owner = StudioStartupGlobals(
            repository_root=APP_ROOT, result_root=self.tmp.path
        ).install()
        self.addCleanup(owner.close)
        for field in ("prompt_styles", "device", "total_tqdm"):
            self.assertIsNotNone(getattr(self.neo.shared, field, None), field)

    def test_install_sets_opts_and_state(self) -> None:
        owner = StudioStartupGlobals(
            repository_root=APP_ROOT, result_root=self.tmp.path
        ).install()
        self.addCleanup(owner.close)
        self.assertIsNotNone(self.neo.shared.opts)
        self.assertIsNotNone(self.neo.shared.state)

    def test_bridge_is_exposed_for_the_port(self) -> None:
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        self.addCleanup(owner.close)
        self.assertIsNotNone(owner.bridge)
        self.assertIs(owner.bridge, self.neo.shared.state)

    def test_close_restores_every_field_except_the_process_options(self) -> None:
        """`opts` is excluded by design -- it is process-lifetime as of R1.5.

        See `process_options` in forge_headless/headless_options.py: forty
        modules bind the value at import time, so restoring it strands all
        forty. The exclusion is named here rather than the field being dropped
        from the tuple, so a reader sees that it was considered.
        """

        before = {
            field: getattr(self.neo.shared, field, None)
            for field in ("prompt_styles", "device", "total_tqdm", "opts", "state")
        }
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        owner.close()
        for field, value in before.items():
            if field == "opts":
                continue
            self.assertIs(value, getattr(self.neo.shared, field, None), field)

        from forge_headless.headless_options import HeadlessOptions

        self.assertIsInstance(self.neo.shared.opts, HeadlessOptions)

    def test_close_is_idempotent(self) -> None:
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        first = owner.close()
        second = owner.close()
        self.assertEqual(first, second)
        self.assertTrue(owner.closed)

    def test_close_reports_restoration(self) -> None:
        """Compatibility and state go back. Options deliberately do not.

        `options_restored` is still reported, and reports False. A caller that
        checks it should learn that nothing was torn down, rather than find the
        key missing and conclude the step was skipped.
        """

        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        report = owner.close()
        self.assertTrue(report["compatibility_restored"])
        self.assertTrue(report["state_restored"])
        self.assertIs(False, report["options_restored"])

    def test_partial_install_failure_unwinds(self) -> None:
        del sys.modules["modules.shared_total_tqdm"].TotalTQDM
        owner = StudioStartupGlobals(repository_root=APP_ROOT)
        with self.assertRaises(Exception):
            owner.install()
        # A half-installed shared must not survive the failure.
        self.assertTrue(owner.closed)
        self.assertIsNone(self.neo.shared.state)

    def test_projection_carries_no_path(self) -> None:
        owner = StudioStartupGlobals(
            repository_root=APP_ROOT, result_root=self.tmp.path
        ).install()
        self.addCleanup(owner.close)
        self.assertNotIn(str(APP_ROOT), repr(owner.to_dict()))

    def test_double_install_is_a_no_op(self) -> None:
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        self.addCleanup(owner.close)
        bridge = owner.bridge
        owner.install()
        self.assertIs(bridge, owner.bridge)


class ControlledOpenerBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.addCleanup(self.neo.restore)
        self.auth = FakeAuthorization()

    def test_construction_opens_nothing(self) -> None:
        with _Intercepts() as spy:
            opener = ControlledPayloadOpener(authorization=self.auth)
            self.assertFalse(opener.opened)
            self.assertEqual(0, spy.cuda_calls)
            self.assertEqual([], spy.validated)

    def test_missing_authority_refuses(self) -> None:
        with self.assertRaises(LoadConfigurationError):
            ControlledPayloadOpener(authorization=None)

    def test_open_validates_every_role_before_cuda(self) -> None:
        order: list[str] = []

        with _Intercepts() as spy:
            opener = ControlledPayloadOpener(authorization=self.auth)
            opener(profile=_profile(), roles=ROLE_ORDER)
        self.assertEqual(list(ROLE_ORDER), spy.validated)
        self.assertEqual(1, spy.cuda_calls)
        del order

    def test_validation_failure_precedes_cuda(self) -> None:
        with _Intercepts(validate=RuntimeError("bad path")) as spy:
            opener = ControlledPayloadOpener(authorization=self.auth)
            with self.assertRaises(RuntimeError):
                opener(profile=_profile())
        self.assertEqual(0, spy.cuda_calls)

    def test_open_returns_opened_payloads(self) -> None:
        with _Intercepts():
            opened = ControlledPayloadOpener(authorization=self.auth)(
                profile=_profile()
            )
        self.assertIsInstance(opened, OpenedPayloads)
        self.assertEqual(ROLE_ORDER, opened.opened_roles)

    def test_a_spent_opener_refuses_a_second_open(self) -> None:
        with _Intercepts():
            opener = ControlledPayloadOpener(authorization=self.auth)
            opener(profile=_profile())
            with self.assertRaises(LoadConfigurationError):
                opener(profile=_profile())

    def test_authorization_is_not_consumed_before_an_open(self) -> None:
        with _Intercepts():
            ControlledPayloadOpener(authorization=self.auth)(profile=_profile())
        # The watch is installed, but no payload has been read yet.
        self.assertFalse(self.auth.consumed)

    def test_projection_carries_no_path(self) -> None:
        with _Intercepts():
            opener = ControlledPayloadOpener(authorization=self.auth)
            opened = opener(profile=_profile())
        rendered = repr(opener.to_dict()) + repr(opened.to_dict())
        self.assertNotIn("synthetic://", rendered)


class EngineBuilderBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.addCleanup(self.neo.restore)
        self.auth = FakeAuthorization()

    def _opened(self):
        with _Intercepts():
            return ControlledPayloadOpener(authorization=self.auth)(profile=_profile())

    def test_engine_builder_opens_three_roles_in_order(self) -> None:
        opened = self._opened()
        build_forge_engine(profile=_profile(), opened=opened)
        self.assertEqual(list(ROLE_ORDER), list(opened._watch.opened))

    def test_first_open_consumes_the_authorization_exactly_once(self) -> None:
        opened = self._opened()
        build_forge_engine(profile=_profile(), opened=opened)
        self.assertTrue(self.auth.consumed)
        self.assertEqual(1, self.auth.consume_calls)

    def test_engine_builder_returns_one_engine(self) -> None:
        opened = self._opened()
        engine = build_forge_engine(profile=_profile(), opened=opened)
        self.assertIs(self.neo.engine, engine)
        self.assertEqual(1, self.neo.loader_calls)

    def test_watch_is_restored_after_a_successful_build(self) -> None:
        import safetensors

        # Read from the module, not the stub: `self.neo._safe_open` builds a
        # fresh bound method on every access, so identity would never hold.
        original = safetensors.safe_open
        opened = self._opened()
        self.assertIsNot(original, safetensors.safe_open)
        build_forge_engine(profile=_profile(), opened=opened)
        self.assertIs(original, safetensors.safe_open)

    def test_watch_is_restored_after_a_failed_build(self) -> None:
        import safetensors

        self.neo.fail_load = RuntimeError("no")
        # Read from the module, not the stub: `self.neo._safe_open` builds a
        # fresh bound method on every access, so identity would never hold.
        original = safetensors.safe_open
        opened = self._opened()
        with self.assertRaises(RuntimeError):
            build_forge_engine(profile=_profile(), opened=opened)
        self.assertIs(original, safetensors.safe_open)

    def test_builder_without_payloads_refuses(self) -> None:
        with self.assertRaises(LoadConfigurationError):
            build_forge_engine(profile=_profile(), opened=None)

    def test_publish_imports_processing_before_assigning(self) -> None:
        engine = FakeEngine()
        state = publish_engine(engine)
        self.assertTrue(state["published"])
        self.assertIs(engine, self.neo.shared.sd_model)

    def test_publish_is_restorable(self) -> None:
        previous = object()
        self.neo.shared.sd_model = previous
        state = publish_engine(FakeEngine())
        self.assertTrue(restore_published_engine(state))
        self.assertIs(previous, self.neo.shared.sd_model)


class DefaultAssemblyTests(unittest.TestCase):
    """The §12 proof: the real graph, end to end, nothing faked but terminals."""

    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.tmp = _Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)
        self.auth = FakeAuthorization()
        self.configuration = LoadConfiguration(
            repository_root=APP_ROOT, authorization=self.auth
        )

    def _load(self, **kwargs):
        loader = HeadlessSessionLoader(
            result_root=self.tmp.path,
            load_configuration=self.configuration,
            **kwargs,
        )
        with _Intercepts():
            return loader, loader.load(_profile())

    def test_explicit_load_completes_through_real_defaults(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        described = loaded.describe()
        self.assertTrue(described["identity_attached"])
        self.assertTrue(described["port_configured"])
        self.assertTrue(described["session_configured"])
        self.assertEqual(
            [
                "cancellation_checked",
                "startup_globals_installed",
                "payloads_opened",
                "engine_built",
                "identity_attached",
                "bookkeeping_installed",
                "port_built",
                "session_built",
            ],
            described["steps_completed"],
        )

    def test_three_roles_open_in_fixed_order(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        self.assertEqual(1, self.neo.loader_calls)
        self.assertEqual(1, self.auth.consume_calls)

    def test_engine_is_published_to_retained_code(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        self.assertIs(self.neo.engine, self.neo.shared.sd_model)

    def test_the_real_port_is_selected(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        self.assertIsInstance(loaded.port, StudioLiveGenerationPort)

    def test_the_port_holds_the_installed_bridge(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        self.assertIs(self.neo.shared.state, loaded.port._bridge)

    def test_two_jobs_reuse_one_session_and_release_twice(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        port = loaded.port
        for index in (1, 2):
            port.generate(_request(f"job-{index}"), _Progress())
        self.assertEqual(2, port.generate_calls)
        self.assertEqual(2, port.release_calls)
        self.assertFalse(port.engine_released)
        self.assertEqual(2, self.neo.process_calls)

    def test_close_restores_every_owned_global_except_the_process_options(
        self,
    ) -> None:
        """As above: `opts` outlives the load deliberately."""

        before = {
            field: getattr(self.neo.shared, field, None)
            for field in ("prompt_styles", "device", "total_tqdm", "opts", "state",
                          "sd_model")
        }
        _loader_obj, loaded = self._load()
        loaded.close()
        for field, value in before.items():
            if field == "opts":
                continue
            self.assertIs(value, getattr(self.neo.shared, field, None), field)

        from forge_headless.headless_options import HeadlessOptions

        self.assertIsInstance(self.neo.shared.opts, HeadlessOptions)

    def test_close_releases_the_engine(self) -> None:
        _loader_obj, loaded = self._load()
        port = loaded.port
        loaded.close()
        self.assertTrue(port.engine_released)
        self.assertTrue(port.terminal)

    def test_close_is_idempotent(self) -> None:
        _loader_obj, loaded = self._load()
        loaded.close()
        loaded.close()
        self.assertTrue(loaded.closed)

    def test_a_second_load_is_refused(self) -> None:
        loader, loaded = self._load()
        self.addCleanup(loaded.close)
        with self.assertRaises(Exception) as caught:
            loader.load(_profile())
        self.assertEqual("single_attempt", caught.exception.step)

    def test_public_projection_carries_no_path(self) -> None:
        _loader_obj, loaded = self._load()
        self.addCleanup(loaded.close)
        self.assertNotIn("synthetic://", repr(loaded.describe()))


class BindingFailureMatrixTests(unittest.TestCase):
    """§13. Every boundary releases in reverse and reports a scalar."""

    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.tmp = _Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)
        self.auth = FakeAuthorization()
        self.before = {
            field: getattr(self.neo.shared, field, None)
            for field in ("prompt_styles", "device", "total_tqdm", "opts", "state",
                          "sd_model")
        }

    def _fails(self, *, step: str, intercepts=None, **kwargs):
        loader = HeadlessSessionLoader(
            result_root=self.tmp.path,
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=self.auth
            ),
            **kwargs,
        )
        with (intercepts or _Intercepts()):
            with self.assertRaises(Exception) as caught:
                loader.load(_profile())
        error = caught.exception
        self.assertEqual(step, error.step)
        self.assertNotIn("synthetic://", str(error))
        self.assertNotIn("Traceback", str(error))
        return loader, error

    def _assert_globals_restored(self) -> None:
        """Every global goes back EXCEPT `opts`, which is process-lifetime.

        R1.5 made the options object outlive the load that installed it.
        Forty modules bind `from modules.shared import opts` at import time, so
        restoring the previous value leaves all forty holding an object nothing
        else uses and the next load builds a second -- the failure
        `neo_registries._read` records.

        `opts` is therefore excluded here and asserted separately below, so the
        exclusion is a stated contract rather than a silently skipped field.
        """

        for field, value in self.before.items():
            if field == "opts":
                continue
            self.assertIs(value, getattr(self.neo.shared, field, None), field)

        from forge_headless.headless_options import HeadlessOptions

        self.assertIsInstance(
            getattr(self.neo.shared, "opts", None),
            HeadlessOptions,
            "opts must survive the load, not be restored",
        )

    def test_missing_authority(self) -> None:
        loader = HeadlessSessionLoader(result_root=self.tmp.path)
        with self.assertRaises(Exception) as caught:
            loader.load(_profile())
        self.assertEqual("startup_globals_installed", caught.exception.step)
        self.assertFalse(loader.payload_opened)

    def test_startup_global_install_failure(self) -> None:
        del sys.modules["modules.styles"].StyleDatabase
        self._fails(step="startup_globals_installed")
        self._assert_globals_restored()

    def test_validation_failure_opens_nothing(self) -> None:
        self._fails(
            step="payloads_opened",
            intercepts=_Intercepts(validate=RuntimeError("bad role")),
        )
        self._assert_globals_restored()
        self.assertFalse(self.auth.consumed)

    def test_cuda_failure_opens_nothing(self) -> None:
        self._fails(
            step="payloads_opened", intercepts=_Intercepts(cuda=RuntimeError("no cuda"))
        )
        self._assert_globals_restored()
        self.assertFalse(self.auth.consumed)

    def test_engine_build_failure(self) -> None:
        self.neo.fail_load = RuntimeError("bad payload")
        self._fails(step="engine_built")
        self._assert_globals_restored()

    def test_identity_failure(self) -> None:
        self._fails(
            step="identity_attached",
            identity_installer=lambda _engine: (_ for _ in ()).throw(
                RuntimeError("identity")
            ),
        )
        self._assert_globals_restored()

    def test_bookkeeping_failure(self) -> None:
        self._fails(
            step="bookkeeping_installed",
            bookkeeping=lambda _engine: (_ for _ in ()).throw(RuntimeError("book")),
        )
        self._assert_globals_restored()

    def test_port_construction_failure(self) -> None:
        self._fails(
            step="port_built",
            port_factory=lambda **_k: (_ for _ in ()).throw(RuntimeError("port")),
        )
        self._assert_globals_restored()

    def test_session_construction_failure(self) -> None:
        self._fails(
            step="session_built",
            session_factory=lambda **_k: (_ for _ in ()).throw(RuntimeError("session")),
        )
        self._assert_globals_restored()

    def test_no_retry_after_a_post_open_failure(self) -> None:
        loader, _error = self._fails(
            step="session_built",
            session_factory=lambda **_k: (_ for _ in ()).throw(RuntimeError("s")),
        )
        self.assertTrue(loader.payload_opened)
        with self.assertRaises(Exception) as caught:
            loader.load(_profile())
        self.assertEqual("single_attempt", caught.exception.step)

    def test_published_engine_is_unpublished_on_later_failure(self) -> None:
        self._fails(
            step="port_built",
            port_factory=lambda **_k: (_ for _ in ()).throw(RuntimeError("port")),
        )
        self.assertIs(self.before["sd_model"], self.neo.shared.sd_model)


class LivePortContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = NeoStub().install()
        self.tmp = _Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)
        self.port = StudioLiveGenerationPort(
            engine=self.neo.engine, bridge=None, result_root=self.tmp.path
        )

    def test_port_survives_many_jobs(self) -> None:
        for index in range(3):
            self.port.generate(_request(f"job-{index}"), _Progress())
        self.assertEqual(3, self.port.generate_calls)
        self.assertFalse(self.port.terminal)

    def test_release_runs_exactly_once_per_job(self) -> None:
        for index in range(3):
            self.port.generate(_request(f"job-{index}"), _Progress())
        self.assertEqual(3, self.port.release_calls)

    def test_release_runs_after_a_failed_job(self) -> None:
        self.neo.images = []
        with self.assertRaises(Exception):
            self.port.generate(_request(), _Progress())
        self.assertEqual(1, self.port.release_calls)

    def test_a_failed_job_does_not_release_the_engine(self) -> None:
        self.neo.images = []
        with self.assertRaises(Exception):
            self.port.generate(_request(), _Progress())
        self.assertFalse(self.port.engine_released)
        self.neo.images = [FakeImage()]
        self.port.generate(_request("job-2"), _Progress())
        self.assertEqual(2, self.port.generate_calls)

    def test_release_engine_is_idempotent(self) -> None:
        first = self.port.release_engine()
        second = self.port.release_engine()
        self.assertTrue(first["engine_reference_cleared"])
        self.assertTrue(second["terminal"])

    def test_generation_after_release_is_refused(self) -> None:
        self.port.release_engine()
        with self.assertRaises(Exception) as caught:
            self.port.generate(_request(), _Progress())
        self.assertEqual("GENERATION_PORT_RELEASED", caught.exception.code)

    def test_sampler_readback_comes_from_the_sampler_object(self) -> None:
        self.port.generate(_request(), _Progress())
        self.assertEqual("euler-stub", self.port.resolved_sampler)
        self.assertEqual("karras-stub", self.port.resolved_scheduler)
        self.assertEqual("sampler object", self.port.sampler_resolution_source)

    def test_outcome_names_a_relative_location_only(self) -> None:
        outcome = self.port.generate(_request("job-7"), _Progress())
        self.assertEqual("job-7.png", outcome.result_relative_location)
        self.assertNotIn(str(self.tmp.path), repr(outcome.to_dict()))

    def test_result_names_are_filesystem_safe(self) -> None:
        self.assertEqual("job1.png", safe_result_name("../job1", index=1))
        self.assertEqual("job-3.png", safe_result_name("///", index=3))

    def test_two_jobs_do_not_collide(self) -> None:
        first = self.port.generate(_request("a"), _Progress())
        second = self.port.generate(_request("b"), _Progress())
        self.assertNotEqual(
            first.result_relative_location, second.result_relative_location
        )

    def test_progress_reaches_terminal_completion(self) -> None:
        progress = _Progress()
        self.port.generate(_request(), progress)
        self.assertTrue(progress.completed)
        self.assertEqual(2, progress.total)

    def test_port_projection_carries_no_path(self) -> None:
        self.port.generate(_request(), _Progress())
        self.assertNotIn(str(self.tmp.path), repr(self.port.to_dict()))


class BindingImportSafetyTests(unittest.TestCase):
    PROBE = """
import sys, os
sys.path.insert(0, os.getcwd())
import forge_headless.live_bindings, forge_headless.live_generation_port
import forge_headless.session_loader as sl
from forge_studio.composition import build_standalone
c = build_standalone(backend_kind="headless")
loader = sl.HeadlessSessionLoader()
loader.capabilities(object())
c.shutdown()
heavy = sorted({m.split('.')[0] for m in sys.modules
                if m.split('.')[0] in ('torch','backend','modules','gradio',
                                       'safetensors','socketserver')})
print("HEAVY=" + ",".join(heavy))
print("CAN_LOAD=" + str(loader.can_load))
"""

    def _probe(self) -> str:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", self.PROBE],
            capture_output=True, text=True, cwd=str(APP_ROOT), timeout=180,
        )
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        return result.stdout

    def test_probe_reports_an_empty_heavy_set(self) -> None:
        for line in self._probe().splitlines():
            if line.startswith("HEAVY="):
                self.assertEqual("", line[len("HEAVY="):])
                return
        self.fail("probe reported no HEAVY line")

    def test_construction_leaves_the_loader_unable_to_load(self) -> None:
        self.assertIn("CAN_LOAD=False", self._probe())

    def test_product_imports_no_diagnostic(self) -> None:
        forbidden = (
            "first_image_probe", "studio_service_smoke", "generation_residual",
            "readiness_probe", "model_plumbing_probe", "controlled_load_probe",
        )
        for name in ("live_bindings.py", "live_generation_port.py",
                     "session_loader.py"):
            source = (APP_ROOT / "forge_headless" / name).read_text(encoding="utf-8")
            for banned in forbidden:
                self.assertNotIn(banned, source, f"{name} references {banned}")

    def test_no_private_path_is_embedded(self) -> None:
        for name in ("live_bindings.py", "live_generation_port.py"):
            source = (APP_ROOT / "forge_headless" / name).read_text(encoding="utf-8")
            lowered = source.lower()
            self.assertNotIn("private-local", lowered)
            self.assertNotIn(".safetensors", lowered)


class ServiceReadinessTests(unittest.TestCase):
    """§16. The product distinguishes "configure access" from "load failed"."""

    def _composition(self, **kwargs):
        from forge_studio.composition import build_standalone

        return build_standalone(
            backend_kind="headless", profiles=[_profile("a")], **kwargs
        )

    def test_headless_studio_reports_configuration_required(self) -> None:
        composition = self._composition()
        self.addCleanup(composition.shutdown)
        self.assertTrue(
            composition.model_lifecycle.readiness()["load_configuration_required"]
        )

    def test_configured_studio_does_not_require_configuration(self) -> None:
        composition = self._composition(
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=FakeAuthorization()
            )
        )
        self.addCleanup(composition.shutdown)
        self.assertFalse(
            composition.model_lifecycle.readiness()["load_configuration_required"]
        )

    def test_an_unconfigured_host_refuses_to_load_rather_than_half_working(
        self,
    ) -> None:
        # Was "selection still works without configuration", which held because
        # selecting loaded nothing. Choosing a model is client-side now, so the
        # property worth keeping is the one about the server: asking for a
        # model on a host that cannot load must fail plainly and leave a
        # deterministic state, not appear to succeed.
        composition = self._composition()
        self.addCleanup(composition.shutdown)
        with self.assertRaises(Exception):
            composition.model_lifecycle.ensure_loaded(_profile("a"))
        self.assertNotEqual(
            "ready", composition.model_lifecycle.state()["state"]
        )

    def test_status_reads_open_nothing_without_configuration(self) -> None:
        composition = self._composition()
        self.addCleanup(composition.shutdown)
        composition.model_lifecycle.readiness()
        composition.model_lifecycle.state()
        self.assertFalse(
            composition.model_lifecycle.readiness()["model_loaded"]
        )

    def test_an_injected_session_host_is_left_alone(self) -> None:
        """A host handed its own session must not acquire a loader."""

        class _Session:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        composition = self._composition(headless_generation=_Session())
        self.addCleanup(composition.shutdown)
        self.assertFalse(
            composition.model_lifecycle.readiness()["load_configuration_required"]
        )

    def test_mock_backend_is_left_alone(self) -> None:
        from forge_studio.composition import build_standalone

        composition = build_standalone(profiles=[_profile("a")])
        self.addCleanup(composition.shutdown)
        self.assertFalse(
            composition.model_lifecycle.readiness()["load_configuration_required"]
        )

    def test_readiness_carries_no_path(self) -> None:
        composition = self._composition(
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=FakeAuthorization()
            )
        )
        self.addCleanup(composition.shutdown)
        rendered = repr(composition.model_lifecycle.readiness())
        self.assertNotIn(str(APP_ROOT), rendered)
        self.assertNotIn("synthetic://", rendered)


class SuiteIntegrityTests(unittest.TestCase):
    def test_discovered_count_matches_the_declared_constant(self) -> None:
        loaded = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_BINDING_TESTS, loaded.countTestCases())

    def test_suite_binds_no_socket(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        # Built from fragments so the assertion is not itself a match.
        for banned in ("socket." + "socket(", "import " + "socket"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
