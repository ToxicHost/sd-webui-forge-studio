"""The tracked Studio service-smoke harness: ownership, durability, truth.

The first live smoke proved the product path and reproduced the golden-anchor
image, then failed acceptance for one reason: the throwaway scratch port stored
`self._engine` and never cleared it, so the engine, VAE component, VAE module and
VAE patcher stayed reachable and one Forge registry entry survived. **No shipped
Studio component was an owner.**

Two proof gaps came with it: the result was retrieved once, before cleanup, so
post-cleanup durability was never measured; and the lifecycle recorder observed a
placeholder progress object rather than the one the gateway drove, so it reported
step 0 for a job that had genuinely completed 12 of 12.

This suite covers the tracked replacement. The harness is real; only the
generation port beneath it is synthetic.

No model is opened. No CUDA is initialized. No image is generated: the only bytes
written are a 70-byte synthetic PNG literal. The loopback server is started and
stopped inside the tests -- short-lived, never persistent.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import gc
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
import weakref
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
APP_ROOT = TEST_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

HARNESS_SOURCE = APP_ROOT / "scripts" / "headless" / "studio_service_smoke.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_SMOKE_HARNESS_TESTS = 41

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

import importlib.util  # noqa: E402


def _load_harness():
    """`scripts/headless/` is not a package, so load by file location.

    The module must be registered in `sys.modules` **before** it executes:
    `@dataclass` resolves `cls.__module__` through `sys.modules`, and an
    unregistered module makes that lookup return `None`.
    """

    spec = importlib.util.spec_from_file_location(
        "studio_service_smoke", str(HARNESS_SOURCE)
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["studio_service_smoke"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


harness = _load_harness()

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.generation_port import GenerationGateway, GenerationOutcome  # noqa: E402
from forge_headless.generation_request import ResidentModel  # noqa: E402
from forge_headless.headless_progress import (  # noqa: E402
    HeadlessProgress,
    JobState as HeadlessJobState,
)
from forge_headless.studio_generation import HeadlessGenerationSession  # noqa: E402


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea735cd900000000049454e"
    "44ae426082"
)

MODEL_ID = "smoke-session-model"
RESULT_NAME = "smoke.png"
TOTAL_STEPS = 12


class Sentinel:
    """Weak-referenceable stand-in for a device-owning object."""

    def __init__(self, label: str) -> None:
        self.label = label


class FakeAllocator:
    """Liveness-driven byte counter. Never zeroed by anything but collection."""

    def __init__(self) -> None:
        self._live: dict[int, int] = {}
        self._keep: list = []

    def charge(self, obj: object, nbytes: int) -> None:
        key = id(obj)
        self._live[key] = self._live.get(key, 0) + nbytes
        self._keep.append(weakref.finalize(obj, self._live.pop, key, None))

    def allocated(self) -> int:
        gc.collect()
        return sum(value for value in self._live.values() if value)


class SyntheticEngine:
    """The ownership graph the live harness actually holds."""

    def __init__(self, allocator: FakeAllocator | None = None) -> None:
        self.unet_module = Sentinel("unet_module")
        self.unet_patcher = Sentinel("unet_patcher")
        self.vae_module = Sentinel("vae_module")
        self.vae_patcher = Sentinel("vae_patcher")
        self.vae_component = Sentinel("vae_component")
        self.text_encoder = Sentinel("text_encoder")
        self.forge_objects = Sentinel("forge_objects")
        # Mirror the real shape: forge_objects.vae is the component.
        self.forge_objects.vae = self.vae_component  # type: ignore[attr-defined]
        self.forge_objects.unet = self.unet_patcher  # type: ignore[attr-defined]
        if allocator is not None:
            for part in (
                self.unet_module, self.unet_patcher, self.vae_module,
                self.vae_patcher, self.vae_component, self.text_encoder,
                self.forge_objects, self,
            ):
                allocator.charge(part, 8_000_000)

    def weak_manifest(self) -> dict[str, object]:
        return {
            "engine": weakref.ref(self),
            "forge_objects": weakref.ref(self.forge_objects),
            "unet_patcher": weakref.ref(self.unet_patcher),
            "unet_module": weakref.ref(self.unet_module),
            "vae_component": weakref.ref(self.vae_component),
            "vae_module": weakref.ref(self.vae_module),
            "vae_patcher": weakref.ref(self.vae_patcher),
            "text_encoder": weakref.ref(self.text_encoder),
        }


class SyntheticOwningPort:
    """A port with the live port's ownership shape and none of its device work."""

    authorized = True

    def __init__(
        self,
        *,
        engine: SyntheticEngine,
        result_root: Path,
        recorder,
        fail_at_step: int | None = None,
        fail_publication: bool = False,
        cancel_at_step: int | None = None,
        release_raises: bool = False,
        retain_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._forge_objects = engine.forge_objects
        self._vae_component = engine.vae_component
        self._result_root = Path(result_root)
        self._recorder = recorder
        self._fail_at_step = fail_at_step
        self._fail_publication = fail_publication
        self._cancel_at_step = cancel_at_step
        self._release_raises = release_raises
        self._retain_engine = retain_engine
        self.generate_calls = 0
        self.terminal = False
        self.engine_released = False

    def release_engine(self) -> dict[str, object]:
        if self._release_raises:
            raise RuntimeError("release exploded")
        if self._retain_engine:
            # The negative control: exactly the prior defect, kept on purpose.
            self.terminal = True
            return {"engine_reference_cleared": False, "retained_on_purpose": True}
        self._engine = None  # type: ignore[assignment]
        self._forge_objects = None
        self._vae_component = None
        self.engine_released = True
        self.terminal = True
        return {"engine_reference_cleared": True, "component_aliases_cleared": True}

    def generate(self, request, progress) -> GenerationOutcome:
        if self.terminal or self._engine is None:
            raise HeadlessError(
                "GENERATION_PORT_TERMINAL", "This port has already run."
            )
        self.generate_calls += 1
        progress.set_total_steps(TOTAL_STEPS)
        progress.advance_to(HeadlessJobState.SAMPLING)
        self._recorder.on_denoiser_entered()

        for step in range(1, TOTAL_STEPS + 1):
            if progress.cancellation_requested:
                progress.mark_cancelled()
                raise HeadlessError("GENERATION_CANCELLED", "cancelled during sampling")
            progress.report_step(step)
            self._recorder.on_sampler_step(step)
            if self._cancel_at_step is not None and step == self._cancel_at_step:
                progress.request_cancellation()
            if self._fail_at_step is not None and step == self._fail_at_step:
                progress.mark_failed("synthetic sampler failure")
                raise HeadlessError("GENERATION_BACKEND_FAILED", "sampler failed")

        progress.advance_to(HeadlessJobState.DECODING)
        self._recorder.on_decode(1)
        progress.advance_to(HeadlessJobState.PUBLISHING)
        if self._fail_publication:
            progress.mark_failed("synthetic publication failure")
            raise HeadlessError("GENERATION_NO_RESULT", "publication failed")

        self._result_root.mkdir(parents=True, exist_ok=True)
        path = self._result_root / RESULT_NAME
        path.write_bytes(TINY_PNG)
        self._recorder.on_publication(len(TINY_PNG))
        progress.mark_completed()
        return GenerationOutcome(
            job_id=request.request_id, request_id=request.request_id,
            result_relative_location=RESULT_NAME, media_type="image/png",
            width=1, height=1, seed=int(request.seed),
        )


REQUEST_PAYLOAD = {
    "prompt": "a contained synthetic subject",
    "neg_prompt": "",
    "seed": 123456789,
    "steps": TOTAL_STEPS,
    "cfg_scale": 6.0,
    "width": 768,
    "height": 768,
}


# ------------------------------------------------------- subprocess driver
#
# `tests/studio_alpha/run_tests.py` patches `connect`, `connect_ex`, `listen`,
# `accept`, `sendto`, `create_connection`, name resolution, `urlopen` and
# `urlretrieve` to raise, and fails the whole run if any of them is touched.
# That guard makes no exception for loopback, and it should not -- weakening it
# to let this suite serve a port would trade a real protection for a
# convenience.
#
# So every scenario that starts the Studio loopback server runs in a fresh
# subprocess with its own unpatched socket module, exactly as the other runtime
# suites in this directory already do. The scenario is driven by this same file,
# so there is one implementation of the synthetic engine and port rather than a
# copy embedded in a string.


def drive_scenario(options: dict) -> dict:
    """Run one smoke scenario and return only JSON-able facts."""

    import tempfile as _tempfile

    directory = _tempfile.mkdtemp()
    root = Path(directory) / "results"
    recorder = harness.LifecycleRecorder()
    allocator = FakeAllocator()
    engine = SyntheticEngine(allocator)
    manifest = engine.weak_manifest()
    port = SyntheticOwningPort(
        engine=engine, result_root=root, recorder=recorder,
        fail_at_step=options.get("fail_at_step"),
        fail_publication=bool(options.get("fail_publication")),
        cancel_at_step=options.get("cancel_at_step"),
        release_raises=bool(options.get("release_raises")),
        retain_engine=bool(options.get("retain_engine")),
    )
    session = HeadlessGenerationSession(
        gateway=GenerationGateway(port),
        resident_model=ResidentModel(
            model_id=MODEL_ID, family="synthetic", resident=True
        ),
        result_root=root, result_writer=None,
    )
    del engine

    report = harness.run_smoke(
        session=session, port=port, result_root=root,
        request_payload=dict(REQUEST_PAYLOAD), recorder=recorder,
        request_timeout=60.0,
    )
    if options.get("clear_field_afterwards"):
        port._engine = None  # noqa: SLF001 - the exact field the defect was in
        port._forge_objects = None  # noqa: SLF001
        port._vae_component = None  # noqa: SLF001

    gc.collect()
    alive = sorted(name for name, ref in manifest.items() if ref() is not None)
    rendered = json.dumps(report.to_dict())
    return {
        "report": report.to_dict(),
        "alive": alive,
        "allocated": allocator.allocated(),
        "port": {
            "generate_calls": port.generate_calls,
            "terminal": port.terminal,
            "engine_released": port.engine_released,
        },
        "rendered_has_root": str(root) in rendered,
        "rendered_has_result_name": RESULT_NAME in rendered,
        "rendered_has_traceback": "Traceback" in rendered,
        "torch_imported": "torch" in sys.modules,
    }


def _scenario(**options) -> dict:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
         "--scenario", json.dumps(options)],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])



class _Base(unittest.TestCase):
    """Scenarios run in a subprocess; only pure assertions run in-process."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "results"

    def scenario(self, **options) -> dict:
        return _scenario(**options)


# ------------------------------------------------------------ import safety


def _probe(body: str) -> dict[str, object]:
    prologue = f"import json, sys\nsys.path.insert(0, {str(APP_ROOT)!r})\n"
    epilogue = (
        "names = sorted(sys.modules)\n"
        "print(json.dumps({\n"
        '    "torch": [n for n in names if n == "torch" or n.startswith("torch.")],\n'
        '    "cuda": [n for n in names if "cuda" in n.casefold()],\n'
        '    "forge_backend": [n for n in names if n == "backend" or n.startswith("backend.")],\n'
        '    "neo": [n for n in names if n in ("modules", "modules_forge", "webui")\n'
        '            or n.startswith(("modules.", "modules_forge.", "webui."))],\n'
        '    "gradio": [n for n in names if n == "gradio" or n.startswith("gradio.")],\n'
        '    "socketserver": [n for n in names if n == "socketserver"],\n'
        "}))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-I", "-S", "-B", "-c",
         prologue + textwrap.dedent(body).strip() + "\n" + epilogue],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


class ImportSafetyTests(unittest.TestCase):
    LOAD = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('s', {str(HARNESS_SOURCE)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
    )

    def test_importing_the_harness_touches_no_runtime_stack(self) -> None:
        report = _probe(self.LOAD)
        for key in ("torch", "cuda", "forge_backend", "neo", "gradio"):
            self.assertEqual([], report[key], key)

    def test_importing_the_harness_binds_no_server(self) -> None:
        report = _probe(self.LOAD)
        self.assertEqual([], report["socketserver"])

    def test_constructing_the_recorder_and_counters_is_free(self) -> None:
        report = _probe(
            self.LOAD
            + "r = m.LifecycleRecorder()\nc = m.CacheClearAccounting()\nr.to_dict()\nc.to_dict()\n"
        )
        for key in ("torch", "cuda", "forge_backend", "neo", "gradio"):
            self.assertEqual([], report[key], key)

    def test_the_harness_defers_every_heavy_import(self) -> None:
        tree = ast.parse(HARNESS_SOURCE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        for forbidden in (
            "torch", "backend", "modules", "modules_forge", "webui", "gradio",
            "forge_studio", "forge_headless",
        ):
            self.assertNotIn(forbidden, roots, forbidden)

    def test_the_harness_embeds_no_private_model_path(self) -> None:
        text = HARNESS_SOURCE.read_text(encoding="utf-8")
        for leak in ("Private-Local", ".safetensors", "C:\\Users", "/Users/"):
            self.assertNotIn(leak, text)


# --------------------------------------------------- complete non-live run


class NonLiveRehearsalTests(_Base):
    def test_the_full_service_path_runs_without_model_or_cuda(self) -> None:
        outcome = self.scenario()
        report = outcome["report"]
        self.assertEqual("smoke_complete", report["outcome"])
        self.assertEqual([], report["errors"])
        self.assertEqual("headless", report["service_path"]["backend_kind"])
        self.assertFalse(report["service_path"]["backend_is_mock"])
        self.assertEqual(
            "HeadlessBackendAdapter", report["service_path"]["adapter_class"]
        )
        self.assertEqual("SyntheticOwningPort", report["service_path"]["port_class"])
        self.assertFalse(outcome["torch_imported"])

    def test_the_counters_report_one_of_everything(self) -> None:
        counters = self.scenario()["report"]["counters"]
        self.assertEqual(1, counters["jobs"])
        self.assertEqual(1, counters["publications"])
        self.assertEqual(1, counters["pre_cleanup_retrievals"])
        self.assertEqual(1, counters["post_cleanup_retrievals"])
        self.assertEqual(0, counters["mock_constructions"])
        self.assertEqual(0, counters["retries"])

    def test_the_request_reaches_the_port_through_the_canonical_route(self) -> None:
        outcome = self.scenario()
        self.assertEqual(1, outcome["port"]["generate_calls"])
        self.assertEqual("smoke_complete", outcome["report"]["outcome"])


# --------------------------------------------------------- weakref release


class WeakrefReleaseTests(_Base):
    def test_every_owned_object_dies_after_cleanup(self) -> None:
        outcome = self.scenario()
        self.assertEqual("smoke_complete", outcome["report"]["outcome"])
        self.assertEqual([], outcome["alive"])

    def test_the_fake_allocator_reaches_zero_only_after_release(self) -> None:
        outcome = self.scenario()
        self.assertEqual("smoke_complete", outcome["report"]["outcome"])
        self.assertEqual(0, outcome["allocated"])

    def test_the_allocator_is_liveness_driven_not_hardcoded(self) -> None:
        allocator = FakeAllocator()
        held = Sentinel("held")
        allocator.charge(held, 264_719_360)
        self.assertEqual(264_719_360, allocator.allocated())
        del held
        self.assertEqual(0, allocator.allocated())

    def test_the_port_reports_that_it_released_the_engine(self) -> None:
        outcome = self.scenario()
        self.assertTrue(outcome["port"]["engine_released"])
        self.assertTrue(outcome["port"]["terminal"])
        self.assertTrue(
            outcome["report"]["cleanup"]["port_release"]["engine_reference_cleared"]
        )

    def test_a_terminal_port_refuses_a_second_run(self) -> None:
        recorder = harness.LifecycleRecorder()
        port = SyntheticOwningPort(
            engine=SyntheticEngine(), result_root=self.root, recorder=recorder
        )
        port.release_engine()
        with self.assertRaises(HeadlessError) as raised:
            port.generate(object(), object())
        self.assertEqual("GENERATION_PORT_TERMINAL", raised.exception.code)


class NegativeRetentionTests(_Base):
    """The prior defect, kept on purpose, so the proof cannot be vacuous."""

    def test_retaining_the_engine_keeps_every_component_alive(self) -> None:
        outcome = self.scenario(retain_engine=True)
        self.assertEqual("smoke_complete", outcome["report"]["outcome"])
        for expected in ("engine", "vae_component", "vae_module", "vae_patcher"):
            self.assertIn(expected, outcome["alive"])
        self.assertGreater(
            outcome["allocated"], 0, "the allocator must still report bytes"
        )
        self.assertFalse(
            outcome["report"]["cleanup"]["port_release"]["engine_reference_cleared"]
        )

    def test_clearing_the_field_afterwards_permits_collection(self) -> None:
        retained = self.scenario(retain_engine=True)
        self.assertGreater(retained["allocated"], 0)
        cleared = self.scenario(retain_engine=True, clear_field_afterwards=True)
        self.assertEqual([], cleared["alive"])
        self.assertEqual(0, cleared["allocated"])

    def test_only_the_engine_field_stands_between_alive_and_dead(self) -> None:
        clean = self.scenario()
        dirty = self.scenario(retain_engine=True)
        self.assertEqual(clean["report"]["outcome"], dirty["report"]["outcome"])
        self.assertEqual(0, clean["allocated"])
        self.assertGreater(dirty["allocated"], 0)


# ----------------------------------------------------- result durability


class ResultDurabilityTests(_Base):
    def test_the_result_is_retrieved_before_and_after_cleanup(self) -> None:
        report = self.scenario()["report"]
        self.assertEqual(200, report["pre_cleanup_result"]["http_status"])
        self.assertEqual(200, report["post_cleanup_result"]["http_status"])

    def test_bytes_are_identical_across_cleanup(self) -> None:
        report = self.scenario()["report"]
        self.assertTrue(report["byte_identity"]["identical"])
        self.assertEqual(len(TINY_PNG), report["byte_identity"]["pre_bytes"])
        self.assertEqual(len(TINY_PNG), report["byte_identity"]["post_bytes"])
        self.assertEqual(
            report["pre_cleanup_result"]["sha256"],
            report["post_cleanup_result"]["sha256"],
        )

    def test_media_type_and_dimensions_survive(self) -> None:
        report = self.scenario()["report"]
        self.assertTrue(report["byte_identity"]["same_media_type"])
        self.assertEqual("image/png", report["post_cleanup_result"]["content_type"])
        self.assertEqual(
            report["pre_cleanup_result"]["width"],
            report["post_cleanup_result"]["width"],
        )

    def test_the_handle_stays_opaque_and_leaks_no_path(self) -> None:
        outcome = self.scenario()
        self.assertTrue(outcome["report"]["pre_cleanup_result"]["handle_opaque"])
        self.assertFalse(outcome["rendered_has_root"])
        self.assertFalse(outcome["rendered_has_result_name"])

    def test_no_generation_object_is_needed_for_retrieval(self) -> None:
        outcome = self.scenario()
        self.assertTrue(outcome["port"]["engine_released"])
        self.assertEqual([], outcome["alive"])
        self.assertEqual(
            200, outcome["report"]["post_cleanup_result"]["http_status"]
        )

    def test_durability_is_reported_independently_of_cleanup(self) -> None:
        report = self.scenario(release_raises=True)["report"]
        self.assertIn("error", report["cleanup"]["port_release"])
        self.assertEqual(200, report["post_cleanup_result"]["http_status"])
        self.assertTrue(report["byte_identity"]["identical"])


# ------------------------------------------------------- lifecycle truth


class LifecycleTruthTests(_Base):
    def test_the_recorder_reports_all_twelve_steps(self) -> None:
        facts = self.scenario()["report"]["lifecycle"]["facts"]
        self.assertEqual(TOTAL_STEPS, facts["completed_sampler_steps"]["value"])
        self.assertEqual(1, facts["first_completed_sampler_step"]["value"])
        self.assertTrue(facts["denoiser_entered"]["value"])
        self.assertEqual("completed", facts["job_terminal"]["value"])

    def test_unavailable_stages_are_named_not_guessed(self) -> None:
        facts = self.scenario()["report"]["lifecycle"]["facts"]
        for name in ("prompt_setup", "conditioning", "initial_noise"):
            self.assertEqual(harness.UNAVAILABLE, facts[name]["value"])
            self.assertIn("reason", facts[name])

    def test_the_recorder_owns_no_progress_object(self) -> None:
        """The prior defect was a second progress object. There is none now."""

        tree = ast.parse(HARNESS_SOURCE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LifecycleRecorder":
                names = {
                    child.func.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                }
                self.assertNotIn("HeadlessProgress", names)

    def test_a_contradiction_with_authoritative_progress_raises(self) -> None:
        recorder = harness.LifecycleRecorder()
        recorder.record("completed_sampler_steps", 0)
        with self.assertRaises(AssertionError):
            recorder.assert_consistent_with_job({"step": TOTAL_STEPS})

    def test_regression_the_placeholder_defect_is_reproduced_and_fixed(self) -> None:
        """Old recorder reads 0; the new one reads 12, from the real object."""

        placeholder = HeadlessProgress("placeholder", preview_enabled=False)
        real = HeadlessProgress("real", preview_enabled=False)
        real.set_total_steps(TOTAL_STEPS)
        real.advance_to(HeadlessJobState.LOADING)
        real.advance_to(HeadlessJobState.CONDITIONING)
        real.advance_to(HeadlessJobState.SAMPLING)
        for step in range(1, TOTAL_STEPS + 1):
            real.report_step(step)

        # The old shape: read the object nothing wrote to.
        self.assertEqual(0, placeholder.snapshot().step)
        self.assertIsNone(placeholder.snapshot().total_steps)
        # The authoritative object.
        self.assertEqual(TOTAL_STEPS, real.snapshot().step)

        class _Application:
            def poll_or_stream_progress(self, job_id: str):
                snapshot = real.snapshot()

                class _Event:
                    state = type("S", (), {"value": snapshot.state.value})()
                    progress = 100
                    step = snapshot.step
                    total_steps = snapshot.total_steps
                    message = snapshot.stage_label

                return _Event()

        recorder = harness.LifecycleRecorder()
        observed = recorder.observe_job(_Application(), "real")
        self.assertEqual(TOTAL_STEPS, observed["step"])
        recorder.assert_consistent_with_job(observed)


# --------------------------------------------------------- failure matrix


class FailureMatrixTests(_Base):
    def _assert_scalar_errors(self, outcome: dict) -> None:
        self.assertFalse(outcome["rendered_has_traceback"])
        self.assertFalse(outcome["rendered_has_root"])
        for error in outcome["report"]["errors"]:
            self.assertIsInstance(error["code"], str)
            self.assertIsInstance(error["message"], str)

    def test_generation_failure(self) -> None:
        outcome = self.scenario(fail_at_step=5)
        self.assertEqual(0, outcome["report"]["counters"]["retries"])
        self.assertTrue(outcome["port"]["terminal"])
        self.assertEqual([], outcome["alive"])
        self.assertEqual(1, outcome["report"]["server"]["stops"])
        self._assert_scalar_errors(outcome)

    def test_publication_failure(self) -> None:
        outcome = self.scenario(fail_publication=True)
        self.assertTrue(outcome["port"]["terminal"])
        self.assertEqual([], outcome["alive"])
        self.assertEqual(1, outcome["report"]["server"]["stops"])
        self._assert_scalar_errors(outcome)

    def test_cancellation_during_sampling(self) -> None:
        outcome = self.scenario(cancel_at_step=3)
        self.assertTrue(outcome["port"]["terminal"])
        self.assertEqual([], outcome["alive"])
        self._assert_scalar_errors(outcome)

    def test_cleanup_failure_still_stops_the_server(self) -> None:
        report = self.scenario(release_raises=True)["report"]
        self.assertIn("error", report["cleanup"]["port_release"])
        self.assertEqual(1, report["server"]["stops"])
        self.assertFalse(report["server"]["thread_alive"])

    def test_failure_before_the_engine_is_assigned(self) -> None:
        recorder = harness.LifecycleRecorder()
        report = harness.run_smoke(
            session=object(), port=object(), result_root=self.root,
            request_payload=dict(REQUEST_PAYLOAD), recorder=recorder,
        )
        self.assertEqual("smoke_failed", report.outcome)
        self.assertEqual(1, len(report.errors))
        self.assertEqual(0, report.server["starts"])
        rendered = json.dumps(report.to_dict())
        self.assertNotIn("Traceback", rendered)

    def test_no_retry_is_ever_attempted(self) -> None:
        for kwargs in ({"fail_at_step": 2}, {"fail_publication": True}, {}):
            outcome = self.scenario(**kwargs)
            self.assertEqual(0, outcome["report"]["counters"]["retries"])
            self.assertLessEqual(outcome["port"]["generate_calls"], 1)


# ------------------------------------------------ server and cache clears


class LoopbackShutdownTests(_Base):
    def test_the_server_starts_once_and_stops_once(self) -> None:
        server = self.scenario()["report"]["server"]
        self.assertEqual(1, server["starts"])
        self.assertEqual(1, server["stops"])
        self.assertFalse(server["thread_alive"])
        self.assertTrue(server["socket_released"])
        self.assertEqual("127.0.0.1", server["host"])
        self.assertTrue(server["ephemeral"])

    def test_a_second_start_is_refused(self) -> None:
        server = harness.LoopbackServer(object())
        server.starts = 1
        with self.assertRaises(RuntimeError):
            server.start()

    def test_stop_is_safe_when_start_never_ran(self) -> None:
        server = harness.LoopbackServer(object())
        server.stop()
        self.assertEqual(0, server.starts)
        self.assertFalse(server.thread_alive)


class CacheClearAccountingTests(unittest.TestCase):
    def test_owners_are_counted_separately(self) -> None:
        clears = harness.CacheClearAccounting()
        clears.note(clears.LOADER)
        for _ in range(7):
            clears.note(clears.RETAINED)
        clears.note(clears.STUDIO_TERMINAL)
        report = clears.to_dict()
        self.assertEqual(1, report["loader_internal"])
        self.assertEqual(7, report["retained_generation"])
        self.assertEqual(1, report["studio_owned_terminal"])
        self.assertEqual(9, report["total_observed"])
        self.assertTrue(report["studio_terminal_is_one"])

    def test_a_second_studio_terminal_clear_fails_the_check(self) -> None:
        clears = harness.CacheClearAccounting()
        clears.note(clears.STUDIO_TERMINAL)
        clears.note(clears.STUDIO_TERMINAL)
        self.assertFalse(clears.to_dict()["studio_terminal_is_one"])

    def test_the_total_is_never_collapsed_to_one(self) -> None:
        clears = harness.CacheClearAccounting()
        clears.note(clears.LOADER)
        clears.note(clears.STUDIO_TERMINAL)
        report = clears.to_dict()
        self.assertEqual(2, report["total_observed"])
        self.assertIn("disclosed", report["note"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(), EXPECTED_SMOKE_HARNESS_TESTS,
            "Smoke harness test count changed: update "
            "EXPECTED_SMOKE_HARNESS_TESTS deliberately, never to match",
        )

    def test_scope_is_declared(self) -> None:
        doc = sys.modules[__name__].__doc__ or ""
        for label in SCOPE_LABELS:
            self.assertIn(label, doc)


if __name__ == "__main__":
    if "--scenario" in sys.argv:
        print(json.dumps(drive_scenario(json.loads(sys.argv[-1]))))
        raise SystemExit(0)
    unittest.main()
