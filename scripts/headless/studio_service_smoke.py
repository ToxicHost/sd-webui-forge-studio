"""Tracked runner for the Studio service-path smoke test.

The first live smoke ran from a throwaway scratch script. It proved the product
path -- canonical `/studio/*` request through `StudioPresentation`,
`StudioApplication`, `HeadlessBackendAdapter`, a real Tier-0 generation port,
retained Forge, `ResultRegistry`, opaque retrieval -- and reproduced the
golden-anchor image byte-for-byte with all twelve Gradio counters at zero.

It failed acceptance for one reason: the scratch port stored `self._engine` and
never cleared it, so the engine, VAE component, VAE module and VAE patcher stayed
reachable, one Forge registry entry survived, and 264,719,360 bytes remained
allocated. **No shipped Studio component was an owner** -- the session holds only
a `ResidentModel` description. The defect was in the harness, so the harness is
what this module replaces.

It lives in `scripts/headless/` because that is where every other owned probe
lives -- `first_image_probe`, `readiness_probe`, `model_plumbing_probe`,
`controlled_load_probe`, `first_image_readiness_probe`. It is a diagnostic, not
product API: nothing under `forge_studio/` or `forge_headless/` imports it, and
it never bypasses `StudioApplication`.

Importing this module is free. Torch, the Forge backend, `modules.*` and the
HTTP server are all imported inside functions, so a test can import it, read it,
and assert on it without touching a device or binding a socket.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


LOOPBACK = "127.0.0.1"


def _residual_module():
    """Load the residual-attribution module beside this one, once.

    `scripts/headless/` is not a package, so a plain import will not find it.
    Deferred inside a function so importing this module stays free.
    """

    import sys

    name = "studio_generation_residual"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "generation_residual.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

#: Lifecycle facts the smoke reports. Every one is either observed from an
#: authoritative source or reported `unavailable` -- never inferred.
LIFECYCLE_FIELDS = (
    "request_accepted",
    "model_loading",
    "model_ready",
    "prompt_setup",
    "conditioning",
    "initial_noise",
    "denoiser_entered",
    "first_step_observed",
    "first_completed_sampler_step",
    "first_completed_step_index",
    "first_completed_step_total",
    "final_completed_steps",
    "final_total_steps",
    "completed_sampler_steps",
    "decode",
    "publication",
    "job_terminal",
    "cleanup_started",
    "generation_references_released",
    "model_references_released",
    "result_still_resolves",
    "server_stopped",
)

UNAVAILABLE = "unavailable"
OBSERVED = "observed"
INFERRED = "inferred"


# ---------------------------------------------------------------- lifecycle


class LifecycleRecorder:
    """Lifecycle facts from authoritative sources only.

    The prior harness built its own `HeadlessProgress` for reporting while the
    gateway drove a different one, so it reported step 0 for a job that had
    genuinely completed 12 of 12. This recorder owns **no** progress object. It
    reads Studio job progress through the application, and takes port-side facts
    through explicit callbacks from the port that produced them.

    `assert_consistent_with_job` exists so a recorder field can never quietly
    contradict the authoritative job: if it did, that is a defect and it raises.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.facts: dict[str, Any] = {name: UNAVAILABLE for name in LIFECYCLE_FIELDS}
        self._start = time.monotonic()
        self._steps_from_port = 0
        self._first_step_seen = False
        self.step_truth: dict[str, Any] = {}

    # -- recording ---------------------------------------------------------

    def mark(self, name: str, **detail: Any) -> None:
        self.events.append(
            {
                "event": name,
                "offset_seconds": round(time.monotonic() - self._start, 3),
                **detail,
            }
        )

    def record(self, field_name: str, value: Any, *, source: str = OBSERVED) -> None:
        if field_name not in self.facts:
            raise KeyError(f"unknown lifecycle field: {field_name}")
        self.facts[field_name] = {"value": value, "source": source}
        self.mark(field_name, value=value, source=source)

    def unavailable(self, field_name: str, reason: str) -> None:
        if field_name not in self.facts:
            raise KeyError(f"unknown lifecycle field: {field_name}")
        self.facts[field_name] = {"value": UNAVAILABLE, "reason": reason}

    # -- port callbacks ----------------------------------------------------

    def on_denoiser_entered(self) -> None:
        self.record("denoiser_entered", True)

    def on_sampler_step(self, step: int) -> None:
        self._steps_from_port = max(self._steps_from_port, int(step))
        if not self._first_step_seen:
            self._first_step_seen = True
            self.record("first_completed_sampler_step", int(step))

    def on_decode(self, calls: int) -> None:
        self.record("decode", int(calls))

    def record_step_truth(self, steps: object) -> None:
        """Take the six first-step facts from a `SamplerStepRecorder`.

        Every field is either observed or named unavailable. Nothing is derived
        from the final counter -- that derivation is the defect this replaces.
        """

        truth = steps.to_dict()  # type: ignore[attr-defined]
        self.record("first_step_observed", bool(truth["first_step_observed"]))
        for field_name, key in (
            ("first_completed_step_index", "first_completed_step_index"),
            ("first_completed_step_total", "first_completed_step_total"),
            ("final_completed_steps", "final_completed_steps"),
            ("final_total_steps", "final_total_steps"),
        ):
            value = truth[key]
            if value is None:
                self.unavailable(field_name, "no sampler step event was observed")
            else:
                self.record(field_name, int(value))
        index = truth["first_completed_step_index"]
        if index is not None and not self._first_step_seen:
            # The public lifecycle field keeps its name and now carries the
            # first observed one-based step rather than a post-hoc reading.
            # Normally `on_step` has already recorded it live; this covers a
            # port that observed steps without a step sink attached.
            self._first_step_seen = True
            self.record("first_completed_sampler_step", int(index))
        self.step_truth = truth

    def on_publication(self, byte_length: int) -> None:
        self.record("publication", int(byte_length))

    # -- authoritative reads -----------------------------------------------

    def observe_job(self, application: object, job_id: str) -> dict[str, Any]:
        """Read Studio job progress. The single source of truth for steps."""

        event = application.poll_or_stream_progress(job_id)  # type: ignore[attr-defined]
        snapshot = {
            "state": getattr(getattr(event, "state", None), "value", ""),
            "progress": int(getattr(event, "progress", 0) or 0),
            "step": getattr(event, "step", None),
            "total_steps": getattr(event, "total_steps", None),
            "message": str(getattr(event, "message", "")),
        }
        self.record("completed_sampler_steps", snapshot["step"])
        self.record("job_terminal", snapshot["state"])
        return snapshot

    def assert_consistent_with_job(self, snapshot: dict[str, Any]) -> None:
        """A recorder field must never contradict authoritative job progress."""

        authoritative = snapshot.get("step")
        if authoritative is None:
            return
        recorded = self.facts.get("completed_sampler_steps")
        value = recorded.get("value") if isinstance(recorded, dict) else None
        if value != authoritative:
            raise AssertionError(
                "lifecycle recorder disagrees with authoritative job progress: "
                f"{value!r} != {authoritative!r}"
            )
        if self._steps_from_port and self._steps_from_port > int(authoritative):
            raise AssertionError(
                "port reported more steps than the authoritative job progress"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": dict(self.facts),
            "events": [dict(event) for event in self.events],
            "steps_seen_by_port": self._steps_from_port,
            "step_truth": dict(self.step_truth),
        }


# ------------------------------------------------------------ cache clears


class CacheClearAccounting:
    """Count every real `torch.cuda.empty_cache`, attributed by owner.

    The prior live run disclosed nine actual calls -- one loader-internal, seven
    from retained Forge during generation, one Studio terminal. That disclosure
    is kept: the counters below never collapse into a single total.
    """

    LOADER = "loader"
    RETAINED = "retained_generation"
    STUDIO_TERMINAL = "studio_terminal"
    OTHER = "other"

    def __init__(self) -> None:
        self.owner = self.OTHER
        self.calls: list[str] = []
        self._original = None
        self._module = None

    def install(self) -> bool:
        try:
            import torch
        except ImportError:
            return False
        if not hasattr(torch.cuda, "empty_cache"):
            return False
        counter = self
        original = torch.cuda.empty_cache

        def counted(*args: Any, **kwargs: Any):
            counter.calls.append(counter.owner)
            return original(*args, **kwargs)

        self._original = original
        self._module = torch.cuda
        torch.cuda.empty_cache = counted
        return True

    def restore(self) -> None:
        if self._module is not None and self._original is not None:
            try:
                self._module.empty_cache = self._original
            except Exception:  # noqa: BLE001
                pass
        self._module = None
        self._original = None

    def note(self, owner: str) -> None:
        """Record a clear the counter cannot observe, e.g. under a double."""
        self.calls.append(owner)

    def to_dict(self) -> dict[str, Any]:
        counts = {
            self.LOADER: 0, self.RETAINED: 0, self.STUDIO_TERMINAL: 0, self.OTHER: 0
        }
        for owner in self.calls:
            counts[owner] = counts.get(owner, 0) + 1
        return {
            "loader_internal": counts[self.LOADER],
            "retained_generation": counts[self.RETAINED],
            "studio_owned_terminal": counts[self.STUDIO_TERMINAL],
            "other": counts[self.OTHER],
            "total_observed": len(self.calls),
            "sequence": list(self.calls),
            "studio_terminal_is_one": counts[self.STUDIO_TERMINAL] == 1,
            "note": (
                "Additional loader and retained-generation clears are disclosed "
                "rather than folded into a single total."
            ),
        }


# ---------------------------------------------------------- loopback server


class LoopbackServer:
    """One Studio-owned server on 127.0.0.1, started once and stopped once.

    Wraps the existing `_StudioHTTPServer`. It adds no second server
    implementation -- only start/stop bookkeeping and a joined thread, so a
    caller can prove no socket or thread is left behind.
    """

    def __init__(self, presentation: object) -> None:
        self._presentation = presentation
        self._server = None
        self._thread = None
        self.starts = 0
        self.stops = 0
        self.port = 0

    def start(self) -> int:
        if self.starts:
            raise RuntimeError("the loopback server may be started only once")
        from forge_studio.presentation import _StudioHTTPServer

        self._server = _StudioHTTPServer((LOOPBACK, 0), self._presentation)
        self.port = int(self._server.server_address[1])
        self.starts = 1
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self._thread.start()
        return self.port

    def stop(self, *, join_timeout: float = 15.0) -> None:
        """Idempotent, and safe to call when start() never ran or failed."""

        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
            self.stops = 1
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    @property
    def thread_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": LOOPBACK,
            "port": self.port,
            "ephemeral": True,
            "starts": self.starts,
            "stops": self.stops,
            "thread_alive": self.thread_alive,
            "socket_released": self._server is None and self.stops == 1,
        }


def loopback_request(
    method: str, url: str, payload: object | None = None, *, timeout: float = 300.0
) -> tuple[int, dict[str, str], bytes]:
    """One loopback HTTP call. Never leaves 127.0.0.1."""

    if not url.startswith(f"http://{LOOPBACK}:"):
        raise ValueError("the smoke runner speaks only to the loopback server")
    authority = url.split("//", 1)[1].split("/", 1)[0]
    headers = {"Host": authority}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = f"http://{authority}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status, dict(response.headers), response.read()


# ------------------------------------------------------------- live port


class LiveGenerationPort:
    """Single-use `GenerationPort` over retained `process_images_inner`.

    The engine, its component aliases and every closure that captures it are
    released in a `finally`, so they are gone after success, generation failure,
    publication failure, cancellation, cleanup failure and server failure alike.

    After one run the port is **terminal**: a second `generate` is refused
    explicitly rather than silently reusing a released engine.
    """

    authorized = True

    def __init__(
        self,
        *,
        engine: object,
        bridge: object,
        result_root: Path,
        recorder: LifecycleRecorder,
        result_name: str = "studio-live-smoke.png",
    ) -> None:
        self._engine = engine
        self._bridge = bridge
        self._result_root = Path(result_root)
        self._recorder = recorder
        self._result_name = result_name
        # Component aliases are held only so they can be cleared deliberately;
        # the prior harness never named them and so never released them.
        self._forge_objects = None
        self._vae_component = None
        self.generate_calls = 0
        self.terminal = False
        self.engine_released = False
        self.resolved_sampler = ""
        self.resolved_scheduler = ""
        self.sampler_resolution_source = "unavailable"
        self.processing_request = None
        self.processed = None
        #: Set during generate(); scalars only, so releasing the engine does not
        #: have to reach into them.
        self.step_recorder = None
        self.step_observer_restore: dict[str, Any] | None = None

    # -- ownership ---------------------------------------------------------

    def release_engine(self) -> dict[str, Any]:
        """Drop every reference this port holds. Idempotent, never raises."""

        had_engine = self._engine is not None
        self._engine = None
        self._forge_objects = None
        self._vae_component = None
        self._bridge = None
        self.processing_request = None
        self.processed = None
        self.step_recorder = None
        self.engine_released = True
        self.terminal = True
        return {
            "engine_reference_cleared": True,
            "component_aliases_cleared": True,
            "closures_cleared": True,
            "had_engine": had_engine,
            "terminal": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": type(self).__name__,
            "authorized": self.authorized,
            "generate_calls": self.generate_calls,
            "terminal": self.terminal,
            "engine_released": self.engine_released,
            "holds_engine": self._engine is not None,
            "resolved_sampler": self.resolved_sampler,
            "resolved_scheduler": self.resolved_scheduler,
            "sampler_resolution_source": self.sampler_resolution_source,
        }

    # -- the port ----------------------------------------------------------

    def generate(self, request: object, progress: object) -> object:
        from forge_headless.contracts import HeadlessError
        from forge_headless.generation_port import GenerationOutcome
        from forge_headless.headless_progress import JobState

        if self.terminal or self._engine is None:
            raise HeadlessError(
                "GENERATION_PORT_TERMINAL",
                "This port has already run and cannot generate again.",
            )
        self.generate_calls += 1

        # The bridge must predate the `modules.processing` import, and the
        # per-job progress is created by the gateway afterwards, so the bridge
        # is re-pointed at the authoritative object here.
        if self._bridge is not None:
            object.__setattr__(self._bridge, "_progress", progress)

        from modules.processing import (
            StableDiffusionProcessingTxt2Img,
            process_images_inner,
        )

        engine = self._engine
        objects = getattr(engine, "forge_objects", None)
        self._forge_objects = objects
        self._vae_component = getattr(objects, "vae", None) if objects else None

        progress.set_total_steps(int(request.steps))  # type: ignore[attr-defined]
        progress.advance_to(JobState.SAMPLING)  # type: ignore[attr-defined]
        self._recorder.on_denoiser_entered()

        # Observe the sampler's own step events as they happen. The prior run
        # read `progress.snapshot().step` once, after the loop, and so reported
        # the last zero-based index (11) as the *first* completed step.
        residual = _residual_module()
        steps = residual.SamplerStepRecorder(on_step=self._recorder.on_sampler_step)
        steps.enter_denoiser(int(request.steps))  # type: ignore[attr-defined]
        self.step_recorder = steps
        restore_observers = residual.install_step_observers(
            self._bridge, progress, steps
        )

        try:
            processing = StableDiffusionProcessingTxt2Img(
                sd_model=engine,
                outpath_samples=str(self._result_root),
                outpath_grids=str(self._result_root),
                prompt=request.positive_prompt,  # type: ignore[attr-defined]
                negative_prompt=request.negative_prompt,  # type: ignore[attr-defined]
                seed=int(request.seed),  # type: ignore[attr-defined]
                subseed=-1,
                sampler_name=request.sampler,  # type: ignore[attr-defined]
                scheduler=request.scheduler,  # type: ignore[attr-defined]
                batch_size=int(request.batch_size),  # type: ignore[attr-defined]
                n_iter=1,
                steps=int(request.steps),  # type: ignore[attr-defined]
                cfg_scale=float(request.cfg_scale),  # type: ignore[attr-defined]
                distilled_cfg_scale=float(request.distilled_cfg_scale),  # type: ignore[attr-defined]
                width=int(request.width),  # type: ignore[attr-defined]
                height=int(request.height),  # type: ignore[attr-defined]
                enable_hr=False,
                do_not_save_samples=True,
                do_not_save_grid=True,
                override_settings={},
            )
            processing.scripts = None
            self.processing_request = processing
            processed = process_images_inner(processing)
        finally:
            # The construction is inside the `try` on purpose: a request that
            # fails to build would otherwise leave both observers installed on
            # Studio-owned objects with nothing left to remove them.
            self.step_observer_restore = restore_observers()
            self._recorder.record_step_truth(steps)
        self.processed = processed

        sampler_object = getattr(processing, "sampler", None)
        self.resolved_sampler = str(
            getattr(sampler_object, "name", None)
            or getattr(processing, "sampler_name", "")
            or ""
        )
        self.resolved_scheduler = str(
            getattr(sampler_object, "scheduler", None)
            or getattr(processing, "scheduler", "")
            or ""
        )
        self.sampler_resolution_source = (
            "sampler object" if sampler_object is not None else "request (unavailable)"
        )

        images = list(getattr(processed, "images", []) or [])
        if not images:
            progress.mark_failed("generation produced no image")  # type: ignore[attr-defined]
            raise HeadlessError(
                "GENERATION_NO_RESULT", "The generation produced no image."
            )

        if not progress.terminal:  # type: ignore[attr-defined]
            progress.advance_to(JobState.DECODING)  # type: ignore[attr-defined]
        if not progress.terminal:  # type: ignore[attr-defined]
            progress.advance_to(JobState.PUBLISHING)  # type: ignore[attr-defined]

        self._result_root.mkdir(parents=True, exist_ok=True)
        path = self._result_root / self._result_name
        images[0].save(str(path), format="PNG")
        width, height = images[0].size
        self._recorder.on_publication(path.stat().st_size)

        seed = int(getattr(processed, "seed", request.seed) or request.seed)  # type: ignore[attr-defined]
        progress.mark_completed()  # type: ignore[attr-defined]

        return GenerationOutcome(
            job_id=request.request_id,  # type: ignore[attr-defined]
            request_id=request.request_id,  # type: ignore[attr-defined]
            result_relative_location=self._result_name,
            media_type="image/png",
            width=int(width), height=int(height), seed=seed,
        )


# -------------------------------------------------------------- the runner


class SmokeReport:
    """Every fact the smoke produces. Scalars, lists and dicts only.

    A plain class rather than a dataclass on purpose: this module is loaded by
    file location (it lives in `scripts/headless/`, which is not a package), and
    `@dataclass` resolves `cls.__module__` through `sys.modules`, which fails
    with `AttributeError: 'NoneType'` unless every loader remembers to register
    the module first. A plain class simply works under any loader.
    """

    def __init__(self) -> None:
        self.outcome: str = "not_run"
        self.errors: list[dict[str, str]] = []
        self.counters: dict[str, int] = {}
        self.service_path: dict[str, Any] = {}
        self.lifecycle: dict[str, Any] = {}
        self.lifecycle_job: dict[str, Any] = {}
        self.pre_cleanup_result: dict[str, Any] = {}
        self.post_cleanup_result: dict[str, Any] = {}
        self.byte_identity: dict[str, Any] = {}
        self.cleanup: dict[str, Any] = {}
        self.server: dict[str, Any] = {}
        self.cache_clears: dict[str, Any] = {}
        self.gradio: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "errors": list(self.errors),
            "counters": dict(self.counters),
            "service_path": dict(self.service_path),
            "lifecycle": dict(self.lifecycle),
            "pre_cleanup_result": dict(self.pre_cleanup_result),
            "post_cleanup_result": dict(self.post_cleanup_result),
            "byte_identity": dict(self.byte_identity),
            "cleanup": dict(self.cleanup),
            "server": dict(self.server),
            "cache_clears": dict(self.cache_clears),
            "gradio": dict(self.gradio),
            "lifecycle_job": dict(self.lifecycle_job),
        }


def run_smoke(
    *,
    session: object,
    port: object,
    result_root: Path,
    request_payload: dict[str, Any],
    recorder: LifecycleRecorder | None = None,
    cache_clears: CacheClearAccounting | None = None,
    gradio_guard: object | None = None,
    request_timeout: float = 300.0,
) -> SmokeReport:
    """Drive one canonical Studio request end to end, then prove the teardown.

    The order is the correction this milestone exists for: the result is
    retrieved **before** cleanup, cleanup runs, and the same opaque handle is
    retrieved **again** afterwards. The prior run retrieved once and shut down,
    so post-cleanup durability was never measured.

    Cleanup, result durability and server shutdown are reported independently:
    a failing cleanup does not suppress a successful post-cleanup retrieval, and
    neither hides a server that would not stop.
    """

    from forge_studio import GenerationRequest
    from forge_studio.composition import HEADLESS_BACKEND, build_standalone
    from forge_studio.presentation import StudioPresentation

    recorder = recorder or LifecycleRecorder()
    clears = cache_clears or CacheClearAccounting()
    report = SmokeReport()
    report.counters = {
        "jobs": 0, "sessions": 1, "publications": 0,
        "pre_cleanup_retrievals": 0, "post_cleanup_retrievals": 0,
        "mock_constructions": 0, "external_requests": 0, "retries": 0,
    }

    composition = None
    server = None
    handle = ""
    pre_bytes = b""

    try:
        composition = build_standalone(
            backend_kind=HEADLESS_BACKEND,
            result_root=Path(result_root),
            headless_generation=session,
        )
        readiness = composition.readiness()
        adapter = composition.application._backend  # noqa: SLF001
        report.service_path = {
            "backend_kind": readiness.host_metadata.get("backend_kind"),
            "backend_id": readiness.backend_id,
            "backend_is_mock": readiness.backend_is_mock,
            "adapter_class": type(adapter).__name__,
            "port_class": type(port).__name__,
            "presentation_class": "StudioPresentation",
        }

        presentation = StudioPresentation(composition.application, GenerationRequest)
        server = LoopbackServer(presentation)
        port_number = server.start()
        recorder.mark("server_ready", port=port_number)

        recorder.record("request_accepted", True)
        status, _headers, body = loopback_request(
            "POST",
            f"{server.base_url}/studio/generate",
            {"action": "generate", **request_payload},
            timeout=request_timeout,
        )
        report.counters["jobs"] = 1
        generation = json.loads(body.decode("utf-8"))
        entries = generation.get("session_entries") or []
        handle = str(entries[0].get("path", "")) if entries else ""
        if not handle:
            raise RuntimeError("no opaque result handle was returned")
        report.counters["publications"] = 1

        job_ids = sorted(getattr(session, "_jobs", {}))
        if job_ids:
            snapshot = recorder.observe_job(composition.application, job_ids[-1])
            recorder.assert_consistent_with_job(snapshot)
            report.lifecycle_job = snapshot

        # -- retrieval one, before any cleanup ---------------------------
        status, headers, pre_bytes = loopback_request(
            "GET", f"{server.base_url}/studio/file?path={handle}", timeout=120.0
        )
        report.counters["pre_cleanup_retrievals"] = 1
        report.pre_cleanup_result = _describe_result(status, headers, pre_bytes, handle)

        # -- cleanup, reported independently of durability ----------------
        recorder.record("cleanup_started", True)
        report.cleanup = _cleanup(port, session, composition, recorder, clears)

        # -- retrieval two, after cleanup ---------------------------------
        try:
            status, headers, post_bytes = loopback_request(
                "GET", f"{server.base_url}/studio/file?path={handle}", timeout=120.0
            )
            report.counters["post_cleanup_retrievals"] = 1
            report.post_cleanup_result = _describe_result(
                status, headers, post_bytes, handle
            )
            recorder.record("result_still_resolves", True)
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            report.post_cleanup_result = {"error": type(exc).__name__}
            recorder.record("result_still_resolves", False)
            post_bytes = b""

        report.byte_identity = {
            "same_handle": True,
            "pre_bytes": len(pre_bytes),
            "post_bytes": len(post_bytes),
            "identical": bool(pre_bytes) and pre_bytes == post_bytes,
            "same_media_type": (
                report.pre_cleanup_result.get("content_type")
                == report.post_cleanup_result.get("content_type")
            ),
        }
        report.outcome = (
            "smoke_complete" if report.byte_identity["identical"] else "smoke_incomplete"
        )
    except BaseException as exc:  # noqa: BLE001 - the report must always exist
        report.errors.append(
            {"code": "SMOKE_EXCEPTION", "message": f"{type(exc).__name__}"}
        )
        report.outcome = "smoke_failed"
        if not report.cleanup:
            report.cleanup = _cleanup(port, session, composition, recorder, clears)
    finally:
        # The application is stopped only now: it clears the ResultRegistry, so
        # stopping it earlier would make post-cleanup durability unmeasurable.
        report.cleanup.update(_stop_application(composition))
        if server is not None:
            server.stop()
            report.server = server.to_dict()
        else:
            report.server = {"starts": 0, "stops": 0, "thread_alive": False}
        recorder.record("server_stopped", True)

        for name, reason in (
            ("prompt_setup", "retained code emits no signal the owned bridge observes"),
            ("conditioning", "UNKNOWN by design in every Tier-0 attempt"),
            ("initial_noise", "not separately observable"),
        ):
            if recorder.facts.get(name) == UNAVAILABLE:
                recorder.unavailable(name, reason)

        report.lifecycle = recorder.to_dict()
        report.cache_clears = clears.to_dict()
        if gradio_guard is not None:
            try:
                gradio_guard.restore()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            report.gradio = gradio_guard.to_dict()  # type: ignore[attr-defined]
    return report


def _describe_result(
    status: int, headers: dict[str, str], body: bytes, handle: str
) -> dict[str, Any]:
    import hashlib

    described: dict[str, Any] = {
        "http_status": status,
        "content_type": headers.get("Content-Type", ""),
        "byte_length": len(body),
        "sha256": hashlib.sha256(body).hexdigest() if body else "",
        "handle_opaque": handle.startswith("studio-result/"),
        "png_signature": body[:8].hex() == "89504e470d0a1a0a",
    }
    if described["png_signature"] and len(body) > 26:
        described["width"] = int.from_bytes(body[16:20], "big")
        described["height"] = int.from_bytes(body[20:24], "big")
    return described


def _cleanup(
    port: object,
    session: object,
    composition: object,
    recorder: LifecycleRecorder,
    clears: CacheClearAccounting,
) -> dict[str, Any]:
    """Release backend, session and port. Never raises; reports instead.

    Deliberately **not** application shutdown. `StudioApplication.shutdown`
    clears the `ResultRegistry` (`application.py:198-207`), which is correct --
    the registry is application-scoped -- but it means a handle stops resolving
    the moment the application stops. Post-cleanup durability is therefore
    measured after backend/session/port teardown and *before* the application
    is stopped, which is the ordering the sequence calls for.

    `composition` is accepted and unused so the signature reads as the full
    ownership set; stopping it is `_stop_application`'s job.
    """

    import gc

    del composition
    cleanup: dict[str, Any] = {}

    # The shipped seam, exactly once per job, and BEFORE the port drops the
    # processing request -- it needs that object to reach the sampler, the
    # denoiser and the class-level conditioning caches. The three-cycle live run
    # measured 4,784,640 bytes freed here on every job.
    cleanup["conditioning_cache_before"] = _conditioning_cache_state()
    cleanup["generation_release"] = _release_generation_references(port)
    cleanup["conditioning_cache_after"] = _conditioning_cache_state()
    cleanup["conditioning_caches_cleared"] = _caches_cleared(
        cleanup["conditioning_cache_before"], cleanup["conditioning_cache_after"]
    )

    try:
        release = getattr(port, "release_engine", None)
        cleanup["port_release"] = release() if callable(release) else {"skipped": True}
    except BaseException as exc:  # noqa: BLE001
        cleanup["port_release"] = {"error": type(exc).__name__}
    recorder.record("generation_references_released", True)

    try:
        close = getattr(session, "close", None)
        if callable(close):
            close()
        cleanup["session_closed"] = True
    except BaseException as exc:  # noqa: BLE001
        cleanup["session_closed"] = False
        cleanup["session_close_error"] = type(exc).__name__

    cleanup["gc_collected"] = int(gc.collect())
    cleanup["application_stopped_here"] = False
    recorder.record("model_references_released", True)
    return cleanup


#: The class-level conditioning caches. Named here only to read their populated
#: counts -- this module never assigns or clears them. That is the seam's job,
#: and doing it here would prove nothing about the shipped cleanup path.
_CONDITIONING_CACHES = (
    ("StableDiffusionProcessing", "cached_c"),
    ("StableDiffusionProcessing", "cached_uc"),
    ("StableDiffusionProcessingTxt2Img", "cached_hr_c"),
    ("StableDiffusionProcessingTxt2Img", "cached_hr_uc"),
)


def _conditioning_cache_state() -> dict[str, Any]:
    """Populated counts for the class-level conditioning caches.

    Counts only -- never contents, never a tensor, never a repr. Reports
    `available: False` with a reason when `modules.processing` cannot be
    imported, which is the normal case for a non-live test.
    """

    import sys

    # Read only what is ALREADY imported. Reporting must never trigger a heavy
    # import as a side effect: under a runner whose argv Neo's argparse rejects,
    # importing `modules.processing` raises SystemExit and dumps the entire CLI
    # help to stderr -- which it did, ~700 KB of it, before this check existed.
    # On the live path the port has already imported it, so this is a dict hit.
    _processing = sys.modules.get("modules.processing")
    if _processing is None:
        return {
            "available": False,
            "reason": "modules.processing is not imported in this context",
        }

    state: dict[str, Any] = {"available": True}
    for class_name, attribute in _CONDITIONING_CACHES:
        klass = getattr(_processing, class_name, None)
        cache = getattr(klass, attribute, None) if klass is not None else None
        if cache is None:
            state[attribute] = {"present": False}
            continue
        state[attribute] = {
            "present": True,
            "slots": len(cache),
            "populated": len([item for item in cache if item is not None]),
        }
    return state


def _caches_cleared(before: dict[str, Any], after: dict[str, Any]) -> Any:
    """True only when something was populated and is now empty.

    Returns `unavailable` rather than a verdict when the caches could not be
    read: a cleanup that cannot see the caches has not proven anything about
    them, and reporting `True` there would be a lie of omission.
    """

    if not before.get("available") or not after.get("available"):
        return UNAVAILABLE
    populated_before = sum(
        int((before.get(name) or {}).get("populated", 0) or 0)
        for _, name in _CONDITIONING_CACHES
    )
    populated_after = sum(
        int((after.get(name) or {}).get("populated", 0) or 0)
        for _, name in _CONDITIONING_CACHES
    )
    if populated_before == 0:
        return UNAVAILABLE
    return populated_after == 0


def _release_generation_references(port: object) -> dict[str, Any]:
    """Call the shipped release seam exactly once. Never raises; reports instead.

    `forge_headless.failure_cleanup.release_generation_references` already
    clears the sampler, the CFG denoiser, the instance fields, the post-sampling
    accumulators, the published PIL images, the state bridge's latent and the
    class-level conditioning caches -- and it is covered by 71 existing tests.
    Re-implementing any of that here would create a second thing to keep
    correct, so this only calls it.
    """

    try:
        from forge_headless.failure_cleanup import release_generation_references
    except BaseException as exc:  # noqa: BLE001
        return {"called": False, "reason": type(exc).__name__}

    try:
        report = release_generation_references(
            processing=getattr(port, "processing_request", None),
            processed=getattr(port, "processed", None),
            state_bridge=getattr(port, "_bridge", None),
            exception=None,
            outcome=None,
        )
    except BaseException as exc:  # noqa: BLE001 - cleanup must never raise
        return {"called": True, "error": type(exc).__name__}
    return {"called": True, "report": report.to_dict()}


def _stop_application(composition: object) -> dict[str, Any]:
    """Stop the Studio application. Last, because it clears the registry."""

    stopped: dict[str, Any] = {}
    try:
        if composition is not None:
            composition.shutdown()  # type: ignore[attr-defined]
            stopped["composition_shutdown"] = True
        else:
            stopped["composition_shutdown"] = False
    except BaseException as exc:  # noqa: BLE001
        stopped["composition_shutdown"] = False
        stopped["composition_shutdown_error"] = type(exc).__name__
    return stopped


__all__ = (
    "CacheClearAccounting",
    "LIFECYCLE_FIELDS",
    "LifecycleRecorder",
    "LiveGenerationPort",
    "LoopbackServer",
    "SmokeReport",
    "loopback_request",
    "run_smoke",
)
