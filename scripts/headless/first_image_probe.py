"""Owner-authorized first controlled image — one attempt, no UI.

Runs the whole owned Studio path: the Gradio-free options boundary, the owned
progress source and its `shared.state` bridge, the proven Anima load, the real
`modules.processing` generation loop, and publication through Studio's own
`ResultRegistry` to an opaque handle.

Local invocation only. The generation happens in a worker subprocess so the
10-minute deadline can be enforced by killing the process rather than hoping a
thread notices.

Two ordering facts are load-bearing, both learned the hard way:

* `modules/processing.py:33` does `from modules.shared import cmd_opts, opts,
  state`, binding **values** at import time. Both the headless options and the
  state bridge must be installed before that import, or the module holds `None`.
* `process_images` would call `forge_model_reload()` and re-resolve a checkpoint
  from Forge's own catalogue. The model here comes from three exact authorized
  files, so the entry point is `process_images_inner`, which is the real
  generation loop without the model-swapping wrapper.

    python -B scripts/headless/first_image_probe.py --checkpoint ... \
        --text-encoder ... --vae ... --consume-single-generation-attempt
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
REPORT_DIR = WORKSPACE_ROOT / "Evidence" / "studio-first-image"
RESULT_ROOT = REPORT_DIR / "results"
REPORT_JSON = REPORT_DIR / "FIRST_IMAGE_REPORT.json"
WORKER_RESULT = REPORT_DIR / "_worker_result.json"

TIMEOUT_SECONDS = 600
VRAM_CEILING_BYTES = 14 * 1024**3

# The owner-authorized execution profile, verbatim.
PROMPT = (
    "A quiet glass greenhouse at sunrise, lush green plants, warm golden light, "
    "soft mist, detailed cinematic photography, natural colors"
)
NEGATIVE_PROMPT = ""
SEED = 123456789
STEPS = 12
WIDTH = 768
HEIGHT = 768
CFG_SCALE = 6.0
DISTILLED_CFG_SCALE = 3.0
SAMPLER = "Euler"
SCHEDULER = "Automatic"

_REDACTIONS = (str(WORKSPACE_ROOT), str(APP_ROOT), str(Path.home()), Path.home().name)


def redact(value: object) -> object:
    if isinstance(value, str):
        out = value
        for needle in _REDACTIONS:
            if needle:
                out = out.replace(needle, "<REDACTED>")
                out = out.replace(needle.replace("\\", "/"), "<REDACTED>")
        return out
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


# ------------------------------------------------------------------ worker


def worker_main(args: argparse.Namespace) -> int:
    from forge_headless.contracts import HeadlessError
    from forge_headless.controlled_device import initialize_cuda, release_cuda_cache
    from forge_headless.headless_options import (
        headless_options,
        output_directory_overrides,
    )
    from forge_headless.headless_compat import (
        COMPAT_OPTION_OVERRIDES,
        HeadlessCompatibilityContext,
    )
    from forge_headless.headless_progress import (
        ForgeStateBridge,
        HeadlessProgress,
        JobState,
    )
    from forge_headless.load_authorization import ControlledLoadAuthorization
    from forge_headless.load_telemetry import (
        PayloadWatch,
        StageTimer,
        VramTelemetry,
        reset_peak,
        sample_current,
        sample_peak,
    )

    timer = StageTimer()
    telemetry = VramTelemetry(ceiling_bytes=VRAM_CEILING_BYTES)
    record: dict[str, object] = {
        "stage": "start",
        "used_owned_studio_path": True,
        "states": [],
        "cuda_initialized": False,
        "model_loaded": False,
        "text_encoder_loaded": False,
        "vae_loaded": False,
        "denoising_started": False,
        "image_generated": False,
        "result_published": False,
        "network_used": False,
        "errors": [],
    }

    def note(state: str) -> None:
        record["states"].append(state)  # type: ignore[union-attr]

    engine = None
    failed_exception: BaseException | None = None
    bridge = None
    watch: PayloadWatch | None = None
    progress = HeadlessProgress("first-image", preview_enabled=False)
    try:
        timer.start("authorization_validation")
        authorization = ControlledLoadAuthorization(
            {
                "checkpoint": args.checkpoint,
                "text_encoder": args.text_encoder,
                "vae": args.vae,
            },
            timeout_seconds=TIMEOUT_SECONDS,
            vram_ceiling_bytes=VRAM_CEILING_BYTES,
        )
        from forge_headless.model_intake import validate_exact_path

        for role in ("checkpoint", "text_encoder", "vae"):
            validate_exact_path(authorization, role)
        timer.stop("authorization_validation")
        note("CATALOGUE_READY_NO_MODEL")

        packages = str(APP_ROOT / "modules_forge" / "packages")
        if packages not in sys.path:
            sys.path.insert(0, packages)
        sys.argv = [sys.argv[0]]

        RESULT_ROOT.mkdir(parents=True, exist_ok=True)

        timer.start("options_bootstrap")
        option_overrides = dict(output_directory_overrides(RESULT_ROOT))
        # `multiple_tqdm=False` keeps the production TotalTQDM silent: update()
        # and updateTotal() return before constructing a bar, so the owned
        # progress bridge stays the only progress authority.
        option_overrides.update(COMPAT_OPTION_OVERRIDES)
        with headless_options(APP_ROOT, overrides=option_overrides) as options:
            timer.stop("options_bootstrap")

            # Both `opts` and `state` must exist on modules.shared before
            # modules.processing imports them by value.
            from modules import shared

            previous_state = getattr(shared, "state", None)
            bridge = ForgeStateBridge(progress)  # noqa: F841 - released in finally
            shared.state = bridge

            # The retained closure also reads prompt_styles, device, and
            # total_tqdm off modules.shared. shared_init assigns them and this
            # path never runs it; two authorized attempts were each spent
            # discovering one. Supply the whole reachable set here.
            timer.start("compatibility_bootstrap")
            compat = HeadlessCompatibilityContext()
            compat.install()
            record["startup_compatibility"] = compat.to_dict()
            timer.stop("compatibility_bootstrap")
            try:
                # `_generate` mutates `record` in place rather than returning a
                # dict: the first attempt lost every partial fact -- model
                # loaded, device, payload access -- because an exception meant
                # the returned dict was never merged.
                _generate(
                    args, authorization, options, progress, timer, telemetry, record
                )
            finally:
                compat.restore()
                record["startup_compatibility"] = compat.to_dict()
                shared.state = previous_state
                record["state_restored"] = shared.state is previous_state
    except HeadlessError as exc:
        record["errors"].append({"code": exc.code, "message": exc.message})  # type: ignore[union-attr]
        failed_exception = exc
        record["stage"] = "failed"
        note("FAILED")
    except BaseException as exc:  # noqa: BLE001 - the report must always be written
        record["errors"].append(  # type: ignore[union-attr]
            {
                "code": "FIRST_IMAGE_EXCEPTION",
                "message": f"{type(exc).__name__}: {str(exc)[:400]}",
            }
        )
        # Scalars only. The exception itself is kept just long enough for the
        # finally to clear its traceback frames -- those frames hold every local
        # of CFGDenoiser.forward, including conditioning tensors.
        failed_exception = exc
        record["stage"] = "failed"
        note("FAILED")
    finally:
        telemetry.peak = sample_peak()
        timer.start("cleanup")
        note("MODEL_UNLOADING")
        if watch is not None:
            watch.restore()

        # Harvest ownership here, not on the success path. Every exception --
        # generation, timeout, cancellation, guard refusal -- reaches this
        # block, so this is the only place the engine and the guard are
        # guaranteed to be recoverable.
        engine = record.pop("_engine", None)
        guard = record.pop("_ui_guard", None)
        if guard is not None:
            try:
                guard.restore()
            except BaseException:  # noqa: BLE001 - reporting must still happen
                pass
            record["gradio_runtime"] = guard.to_dict()
            record["gradio_guard_serialized"] = True
        else:
            record["gradio_guard_serialized"] = False

        reload_state = record.pop("_reload_state", None)
        if reload_state is not None:
            try:
                reload_state.restore()
            except BaseException:  # noqa: BLE001 - reporting must still happen
                pass
            record["reload_bookkeeping"] = reload_state.to_dict()

        boundaries = record.pop("_boundaries", None)
        if boundaries is not None:
            try:
                boundaries.restore()
            except BaseException:  # noqa: BLE001
                pass
            record["lifecycle_boundaries"] = boundaries.to_dict()
        # Keep the legacy top-level flag, but derive it from the real signal so
        # it can no longer disagree with the boundary record.
        record["denoising_started"] = bool(
            (record.get("lifecycle_boundaries") or {}).get("denoising_started", False)
        )

        # Drop the strong references generation may have left behind before
        # VRAM is measured, or the proof reports tensors the run no longer
        # needs but still holds.
        processing_request = record.pop("_processing_request", None)
        processed_result = record.pop("_processed", None)
        record.pop("_image", None)

        # Attempt 04 left 13.1 MiB allocated across empty_cache(): p.close() is
        # never called on this path, and the conditioning caches are class
        # attributes that persistent_cond_cache keeps alive anyway.
        try:
            from forge_headless.failure_cleanup import release_generation_references

            released = release_generation_references(
                processing=processing_request,
                state_bridge=bridge,
                exception=failed_exception,
                processed=processed_result,
                outcome=None,
            )
            released.exception_serialized_scalar_only = True
            record["failure_release"] = released.to_dict()
        except BaseException:  # noqa: BLE001 - reporting must still happen
            record["failure_release"] = {"error": "release_generation_references failed"}
        processing_request = None
        processed_result = None
        failed_exception = None

        record["cleanup"] = _release(engine)
        engine = None
        telemetry.after_release = sample_current()
        try:
            release_cuda_cache()
        except BaseException:  # noqa: BLE001
            pass
        telemetry.after_cache_clear = sample_current()
        timer.stop("cleanup")
        note("CATALOGUE_READY_NO_MODEL")

        if telemetry.peak is not None:
            telemetry.ceiling_exceeded = (
                telemetry.peak.allocated > VRAM_CEILING_BYTES
                or telemetry.peak.reserved > VRAM_CEILING_BYTES
            )
        record["vram"] = telemetry.to_dict()
        record["stage_timings"] = timer.to_dict()
        record["progress_snapshot"] = progress.snapshot().to_dict()
        record["gradio_imported"] = any(
            name.split(".", 1)[0] in ("gradio", "gradio_client") for name in sys.modules
        )
        record["shared_options_imported"] = "modules.shared_options" in sys.modules
        WORKER_RESULT.parent.mkdir(parents=True, exist_ok=True)
        WORKER_RESULT.write_text(
            json.dumps(redact(record), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0 if record["stage"] == "published" else 1


def _generate(args, authorization, options, progress, timer, telemetry, record) -> None:
    """Load, generate, publish, recording each fact into `record` as it lands."""

    from forge_headless.controlled_device import initialize_cuda
    from forge_headless.headless_progress import JobState
    from forge_headless.load_telemetry import (
        PayloadWatch,
        reset_peak,
        sample_current,
    )

    out = record  # every assignment below is visible even if this raises

    progress.advance_to(JobState.LOADING)
    timer.start("cuda_initialization")
    device = initialize_cuda(authorization)
    timer.stop("cuda_initialization")
    out["device"] = device.to_dict()
    out["cuda_initialized"] = True

    telemetry.before_load = sample_current()
    reset_peak()

    watch = PayloadWatch(
        {
            authorization.loader_path(role): role
            for role in ("checkpoint", "text_encoder", "vae")
        },
        timer,
    )
    watch.on_first_open = authorization.consume
    watch.install()

    timer.start("model_load")
    from backend.loader import forge_loader

    engine = forge_loader(
        authorization.loader_path("checkpoint"),
        additional_state_dicts=authorization.loader_paths("text_encoder", "vae"),
    )
    timer.stop("model_load")
    watch.restore()

    # Ownership first: the cleanup path must be able to release this engine no
    # matter how the rest of the function exits. The first attempt lost the
    # reference because it was harvested only on success.
    out["_engine"] = engine

    objects = getattr(engine, "forge_objects", None)
    out["model_loaded"] = engine is not None
    out["text_encoder_loaded"] = getattr(objects, "clip", None) is not None
    out["vae_loaded"] = getattr(objects, "vae", None) is not None
    out["payload_access"] = watch.to_dict()
    out["engine_class"] = type(engine).__name__

    # `process_images_inner` reads catalogue identity off the model
    # unconditionally, and the Tier-0 path skips the `forge_model_reload()`
    # that normally attaches it. Attach before publication, not after.
    from forge_headless.model_identity import attach_model_identity

    attachment = attach_model_identity(engine)
    out["model_identity"] = attachment.to_dict()

    # `modules.sd_models:12` imports `processing`, and `modules/processing.py:32`
    # does `from modules.sd_models import apply_token_merging, ...` -- a genuine
    # mutual cycle. It resolves only when `processing` is imported first: then
    # `sd_models` sees a partially initialised `processing` module object, which
    # is fine, and finishes before `processing` pulls the name it needs.
    #
    # Importing `sd_models` first is fatal, and `shared.sd_model = ...` does
    # exactly that through the `Shared` property setter
    # (`modules/shared_items.py:175`). So processing is imported *before* the
    # engine is published, not after.
    from modules.processing import StableDiffusionProcessingTxt2Img, process_images_inner

    from modules import shared

    shared.sd_model = engine

    # process_images_inner calls forge_model_reload() itself at
    # modules/processing.py:945. Entering at the inner loop avoids only the
    # outer wrapper's call at :785, not this one -- live attempt 03 proved it.
    # Establish the bookkeeping Forge's own early return checks, so the call is
    # a truthful no-op rather than a catalogue lookup.
    from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping
    from forge_headless.model_identity import DEFAULT_RUNTIME_LABEL

    timer.start("reload_bookkeeping")
    reload_state = DirectLoadReloadBookkeeping()
    reload_state.install(engine, runtime_label=DEFAULT_RUNTIME_LABEL)
    out["_reload_state"] = reload_state
    # Prove the no-op before generation rather than discovering it mid-run. The
    # predicate is checked first, so this cannot fall through into a real load.
    reload_state.verify_fast_path(engine)
    out["reload_bookkeeping"] = reload_state.to_dict()
    timer.stop("reload_bookkeeping")

    processing = StableDiffusionProcessingTxt2Img(
        sd_model=engine,
        outpath_samples=str(RESULT_ROOT),
        outpath_grids=str(RESULT_ROOT),
        prompt=PROMPT,
        negative_prompt=NEGATIVE_PROMPT,
        seed=SEED,
        subseed=-1,
        sampler_name=SAMPLER,
        scheduler=SCHEDULER,
        batch_size=1,
        n_iter=1,
        steps=STEPS,
        cfg_scale=CFG_SCALE,
        distilled_cfg_scale=DISTILLED_CFG_SCALE,
        width=WIDTH,
        height=HEIGHT,
        enable_hr=False,
        do_not_save_samples=True,
        do_not_save_grid=True,
        override_settings={},
    )
    processing.scripts = None
    # Held so the failure path can call close(), clear the class-level
    # conditioning caches, and drop the sampler/denoiser before VRAM is read.
    out["_processing_request"] = processing
    out["request"] = {
        "operation": "txt2img",
        "width": WIDTH,
        "height": HEIGHT,
        "steps": STEPS,
        "batch_size": 1,
        "seed": SEED,
        "sampler": SAMPLER,
        "scheduler": SCHEDULER,
        "cfg_scale": CFG_SCALE,
        "distilled_cfg_scale": DISTILLED_CFG_SCALE,
        "enable_hr": False,
        "prompt_length": len(PROMPT),
        "negative_prompt_length": len(NEGATIVE_PROMPT),
    }

    # Prove no Gradio UI is constructed, now that the library is loaded.
    ui_guard = _GradioRuntimeGuard()
    ui_guard.install()
    out["_ui_guard"] = ui_guard

    progress.advance_to(JobState.CONDITIONING)
    progress.set_total_steps(STEPS)
    progress.advance_to(JobState.SAMPLING)

    boundaries = _LifecycleBoundaries(progress)
    boundaries.install()
    out["_boundaries"] = boundaries

    # The only honest thing this line can assert: the inner loop is about to be
    # called. Whether denoising happens is decided by the sampler, and is
    # reported by the state bridge when a real step lands.
    boundaries.generation_inner_entered = True
    out["lifecycle_boundaries"] = boundaries.to_dict()

    timer.start("generation")
    processed = process_images_inner(processing)
    timer.stop("generation")

    # Resolved names come from the sampler Forge actually built, not from
    # Processed metadata -- four attempts died before Processed existed, so
    # reading them here means they survive a failure after sampler creation.
    sampler_obj = getattr(processing, "sampler", None)
    out["resolved_sampler"] = str(
        getattr(sampler_obj, "name", None) or getattr(processing, "sampler_name", "") or ""
    )
    out["resolved_scheduler"] = str(
        getattr(sampler_obj, "scheduler", None)
        or getattr(processing, "scheduler", "")
        or ""
    )
    out["sampler_resolution_source"] = (
        "sampler object" if sampler_obj is not None else "request (sampler unavailable)"
    )
    out["scheduler_resolution_source"] = out["sampler_resolution_source"]
    out["_processed"] = processed
    out["lifecycle_boundaries"] = boundaries.to_dict()

    images = list(getattr(processed, "images", []) or [])
    out["image_generated"] = bool(images)
    if not images:
        raise RuntimeError("the generation loop returned no image")

    progress.advance_to(JobState.DECODING)
    progress.advance_to(JobState.PUBLISHING)

    image = images[0]
    out["_image"] = image
    out["image_size"] = list(getattr(image, "size", ()))
    out["image_mode"] = str(getattr(image, "mode", "UNKNOWN"))
    out["resolved_seed"] = int(getattr(processed, "seed", SEED))

    timer.start("result_publication")
    target = RESULT_ROOT / "first-controlled-image.png"
    image.save(target, format="PNG")
    out["png_bytes"] = target.stat().st_size

    from forge_studio.result_delivery import ResultRegistry

    registry = ResultRegistry(RESULT_ROOT)
    asset = registry.register(target, media_type="image/png")
    payload = registry.read(asset.handle)
    timer.stop("result_publication")

    out["result_published"] = True
    out["result_handle"] = asset.handle
    out["result_media_type"] = asset.media_type
    out["result_byte_length"] = asset.byte_length
    out["handle_roundtrip_bytes"] = len(payload.content)
    out["handle_is_opaque"] = (
        target.name not in asset.handle and str(RESULT_ROOT) not in asset.handle
    )

    # The guard, the boundaries, and the engine are released by `worker_main`'s
    # `finally`, which runs on success and failure alike. Restoring here too
    # would mean the success path and the failure path release differently --
    # which is exactly how the first attempt lost its telemetry.

    progress.mark_completed()
    out["stage"] = "published"
    out["options_read"] = options.inventory()
    out["options_missing"] = list(options.missing)


class _GradioRuntimeGuard:
    """Separate "Gradio was imported" from "Gradio was used".

    `modules.processing` imports Gradio through three module-level edges
    documented since Phase 2A, so the library is unavoidably in `sys.modules`.
    That says nothing about whether a UI, queue, server, route, or delivery path
    was constructed -- and only the second question matters for this milestone.

    Every entry point below is wrapped. Construction and launch **fail closed**;
    the rest are counted. Anything that cannot be reached on this Gradio build is
    reported `UNINSTRUMENTED` rather than silently assumed absent, because an
    unwrapped API and an unused one look identical in a report otherwise.

    SCOPE: MINIMAL_RUNTIME_SCOPE. Static-import facts are reported separately.
    """

    #: name -> (module path, attribute path, "refuse" or "count")
    TARGETS = (
        ("blocks_init", "gradio", "Blocks.__init__", "refuse"),
        ("blocks_launch", "gradio", "Blocks.launch", "refuse"),
        ("blocks_queue", "gradio", "Blocks.queue", "refuse"),
        ("interface_init", "gradio", "Interface.__init__", "refuse"),
        ("interface_launch", "gradio", "Interface.launch", "refuse"),
        ("mount_gradio_app", "gradio", "mount_gradio_app", "refuse"),
        ("routes_app_create", "gradio.routes", "App.create_app", "refuse"),
        ("queue_init", "gradio.queueing", "Queue.__init__", "count"),
        ("save_pil_to_cache", "gradio.processing_utils", "save_pil_to_cache", "count"),
        ("move_files_to_cache", "gradio.processing_utils", "move_files_to_cache", "count"),
        ("get_upload_folder", "gradio.utils", "get_upload_folder", "count"),
        ("abspath", "gradio.utils", "abspath", "count"),
    )

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.status: dict[str, str] = {}
        self._restore: list = []

    # -- installation ------------------------------------------------------

    def _resolve(self, module_path: str, attribute_path: str):
        import importlib

        try:
            module = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001 - absent is a fact, not a failure
            return None, None, None
        owner = module
        parts = attribute_path.split(".")
        for part in parts[:-1]:
            owner = getattr(owner, part, None)
            if owner is None:
                return None, None, None
        name = parts[-1]
        if not hasattr(owner, name):
            return None, None, None
        return owner, name, getattr(owner, name)

    def install(self) -> None:
        for key, module_path, attribute_path, mode in self.TARGETS:
            owner, name, original = self._resolve(module_path, attribute_path)
            if owner is None:
                # Never claim absence for something that was never wrapped.
                self.status[key] = "UNINSTRUMENTED"
                self.counts[key] = 0
                continue
            self.counts[key] = 0
            self.status[key] = "INSTRUMENTED"
            self._restore.append((owner, name, original))
            self._wrap(owner, name, original, key, mode)

    def _wrap(self, owner, name, original, key: str, mode: str) -> None:
        guard = self

        if mode == "refuse":
            def replacement(*args, **kwargs):
                guard.counts[key] = guard.counts.get(key, 0) + 1
                raise AssertionError("PROBE_REFUSED_GRADIO_" + key.upper())
        else:
            def replacement(*args, **kwargs):
                guard.counts[key] = guard.counts.get(key, 0) + 1
                return original(*args, **kwargs)

        try:
            setattr(owner, name, replacement)
        except Exception:  # noqa: BLE001 - read-only slot on some builds
            self.status[key] = "UNINSTRUMENTED"

    def restore(self) -> None:
        for owner, name, original in reversed(self._restore):
            try:
                setattr(owner, name, original)
            except Exception:  # noqa: BLE001
                pass
        self._restore.clear()

    # -- reporting ---------------------------------------------------------

    @property
    def instrumented(self) -> list[str]:
        return sorted(k for k, v in self.status.items() if v == "INSTRUMENTED")

    @property
    def uninstrumented(self) -> list[str]:
        return sorted(k for k, v in self.status.items() if v == "UNINSTRUMENTED")

    @property
    def all_clear(self) -> bool:
        """True only when every instrumented counter is zero."""
        return all(self.counts.get(k, 0) == 0 for k in self.instrumented)

    def to_dict(self) -> dict[str, object]:
        import sys as _sys

        return {
            "scope": "MINIMAL_RUNTIME_SCOPE",
            "static_import_facts": {
                "gradio_in_sys_modules": "gradio" in _sys.modules,
                "gradio_client_in_sys_modules": "gradio_client" in _sys.modules,
                "note": (
                    "modules.processing imports Gradio through three "
                    "module-level edges documented since Phase 2A; presence in "
                    "sys.modules is expected and is not a runtime-use claim"
                ),
            },
            "runtime_call_counts": dict(self.counts),
            "instrumented": self.instrumented,
            "uninstrumented": self.uninstrumented,
            "all_runtime_counters_zero": self.all_clear,
            "permitted_claim": (
                "Gradio may be imported as a compatibility library, but no "
                "Gradio UI, queue, server, route, or delivery path was "
                "constructed or launched."
                if self.all_clear
                else "NOT PERMITTED - a Gradio runtime counter is non-zero"
            ),
        }


class _LifecycleBoundaries:
    """Report crossed boundaries from real runtime signals, not from intent.

    The first attempt reported `denoising_started: true` for a run that never
    reached a sampler step, because the flag was set immediately before calling
    `process_images_inner`. Entering the inner loop is not denoising -- the run
    died ~100 lines later at metadata attribution, and the report said otherwise.

    Each boundary here is therefore backed by something the runtime actually
    did:

    * `generation_inner_entered` -- set explicitly, and named so it cannot be
      mistaken for denoising;
    * `denoising_started` / `sampling_step` -- read from the owned progress
      source, which the `ForgeStateBridge` writes when Forge assigns
      `shared.state.sampling_step`. That is the real sampler callback, so no
      sampler is edited and nothing is monkey-patched to get it;
    * `decode_started` -- a single wrapper around
      `modules.processing.decode_latent_batch`, the exact VAE entry point at
      `modules/processing.py:1011`. One function, restored afterwards;
    * `conditioning_started` -- **UNKNOWN**. Retained conditioning emits no
      signal the owned bridge observes, and manufacturing one would mean broad
      edits inside `processing`. An honest unknown beats an optimistic true.
    """

    def __init__(self, progress: object) -> None:
        self._progress = progress
        self.generation_inner_entered = False
        self._decode_calls = 0
        self._decode_original = None
        self._decode_owner = None
        # Survives restore(), so the report can still distinguish "instrumented
        # and never called" from "never instrumented".
        self._decode_instrumented = False

    def install(self) -> None:
        try:
            from modules import processing as _processing

            original = getattr(_processing, "decode_latent_batch", None)
            if original is None:
                return

            def counted(*args, **kwargs):
                self._decode_calls += 1
                return original(*args, **kwargs)

            self._decode_original = original
            self._decode_owner = _processing
            self._decode_instrumented = True
            _processing.decode_latent_batch = counted
        except Exception:  # noqa: BLE001 - instrumentation must never break the run
            self._decode_original = None

    def restore(self) -> None:
        if self._decode_owner is not None and self._decode_original is not None:
            try:
                self._decode_owner.decode_latent_batch = self._decode_original
            except Exception:  # noqa: BLE001
                pass
        self._decode_owner = None
        self._decode_original = None

    @property
    def sampling_step(self) -> int:
        try:
            return int(self._progress.snapshot().step)
        except Exception:  # noqa: BLE001
            return 0

    def to_dict(self) -> dict[str, object]:
        step = self.sampling_step
        return {
            "generation_inner_entered": self.generation_inner_entered,
            "conditioning_started": "UNKNOWN",
            "denoising_started": step >= 1,
            "sampling_step": step,
            "decode_started": self._decode_calls > 0,
            "decode_calls": self._decode_calls,
            "decode_instrumented": self._decode_instrumented,
            "note": (
                "denoising_started reflects a real sampler step reported through "
                "the owned state bridge; entering process_images_inner alone does "
                "not set it. conditioning_started is UNKNOWN by design."
            ),
        }


def _release(engine: object) -> dict[str, object]:
    import gc

    released: dict[str, object] = {
        "engine_reference_dropped": engine is not None,
        "forge_objects_cleared": False,
        "identity_detached": False,
        "shared_sd_model_restored": False,
        "owned_cleanup": engine is not None,
        "gc_collected": 0,
    }
    if engine is None:
        # Reaching here after a load means the engine was never handed over.
        # Say so plainly: process exit reclaiming VRAM is not owned cleanup.
        released["gc_collected"] = int(gc.collect())
        return released

    try:
        from forge_headless.model_identity import detach_model_identity

        released["identity_detached"] = detach_model_identity(engine)
    except Exception:  # noqa: BLE001 - cleanup must not raise
        pass
    objects = getattr(engine, "forge_objects", None)
    for attribute in ("unet", "clip", "vae", "clipvision"):
        if objects is not None and hasattr(objects, attribute):
            try:
                setattr(objects, attribute, None)
            except Exception:  # noqa: BLE001
                pass
    for attribute in (
        "forge_objects",
        "forge_objects_original",
        "forge_objects_after_applying_lora",
        "text_processing_engine_anima",
    ):
        if hasattr(engine, attribute):
            try:
                setattr(engine, attribute, None)
            except Exception:  # noqa: BLE001
                pass
    try:
        from modules import sd_models

        sd_models.model_data.set_sd_model(None)
        # `shared.sd_model` is a property over `model_data`
        # (`modules/shared_items.py:168-177`), so clearing the holder is what
        # restores it. Read it back rather than assuming.
        from modules import shared

        released["shared_sd_model_restored"] = shared.sd_model is None
    except Exception:  # noqa: BLE001
        pass
    released["forge_objects_cleared"] = True
    released["gc_collected"] = int(gc.collect())
    return released


# ------------------------------------------------------------------ parent


def main(argv: list[str]) -> int:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--text-encoder", required=True)
    parser.add_argument("--vae", required=True)
    parser.add_argument("--consume-single-generation-attempt", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        return worker_main(args)

    if not args.consume_single_generation_attempt:
        print("refusing: pass --consume-single-generation-attempt to spend the attempt")
        return 2

    WORKER_RESULT.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["HF_DATASETS_OFFLINE"] = "1"

    timed_out = False
    returncode: int | None = None
    stderr_tail = ""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                "--checkpoint", args.checkpoint,
                "--text-encoder", args.text_encoder,
                "--vae", args.vae,
            ],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=environment,
        )
        returncode = completed.returncode
        stderr_tail = completed.stderr[-4000:]
    except subprocess.TimeoutExpired:
        timed_out = True

    worker: dict = {}
    if WORKER_RESULT.is_file():
        try:
            worker = json.loads(WORKER_RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            worker = {}
    WORKER_RESULT.unlink(missing_ok=True)

    succeeded = bool(worker.get("stage") == "published") and not timed_out
    record = {
        "schema_version": "first-controlled-image/v1",
        "outcome": "FIRST_IMAGE_SUCCEEDED" if succeeded else "FIRST_IMAGE_FAILED",
        "worker_timed_out": timed_out,
        "worker_returncode": returncode,
        "worker_exited": returncode is not None,
        "worker_stderr_tail": stderr_tail,
        "timeout_seconds": TIMEOUT_SECONDS,
        "vram_ceiling_bytes": VRAM_CEILING_BYTES,
        "worker": worker,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(redact(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"outcome: {record['outcome']}")
    print(f"report:  Evidence/studio-first-image/{REPORT_JSON.name}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
