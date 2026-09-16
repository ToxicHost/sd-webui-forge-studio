"""Studio's own backend startup. Explicit, once, and without a model.

R1.5 Phase D. Before this existed, Studio's sampler, scheduler and upscaler
registries were empty until the first generation imported the engine as a SIDE
EFFECT. The owner opened Studio, saw "Engine default" in every menu, generated
once, and only then had real choices. `neo_registries._read` refuses to import
anything itself -- deliberately, after an earlier attempt stood the engine up
under a temporary options object and broke the next generation -- so nothing
ever performed the import on purpose.

This module performs it on purpose.

```text
bootstrap_backend()
  -> vendored package path on sys.path        (k_diffusion lives there)
  -> neutral argv                             (Neo parses it at import)
  -> canonical process-lifetime options       (Phase B; NOT a temporary scope)
  -> import modules.sd_samplers, set_samplers()
  -> import modules.sd_schedulers
  -> read registries, which scans image upscalers
  -> assert no real Gradio arrived
```

What it deliberately does NOT do:

* **load a model.** Not a checkpoint, not a text encoder, not a VAE. The state
  after bootstrap is NO_MODEL. Menus are not worth a checkpoint.
* **import Gradio.** Verified from `sys.modules` afterwards rather than
  assumed; a non-zero count is a TERMINAL failure, because the whole point of
  the R1.5 seam is that this path stays clean.
* **run Neo's `initialize.py` or `load_scripts()`.** Studio boots its own
  backend; it does not boot the legacy application and ride on top.
* **create a second options object.** It calls `process_options`, which is
  idempotent and process-lifetime. Forty modules bind `opts` by value at import
  time, so a temporary scope here would strand every one of them.

Failure is reported truthfully rather than latched as success. That distinction
is the upscaler-latch lesson: a scan that failed must not leave the registry
saying READY with an empty list.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Bootstrap outcomes. Truthful state matters; the exact spelling does not.
NOT_STARTED = "not_started"
READY = "ready"
FAILED_RETRYABLE = "failed_retryable"
FAILED_TERMINAL = "failed_terminal"


@dataclass(frozen=True)
class BootstrapReport:
    """What the bootstrap did, in numbers a test can assert on."""

    state: str
    samplers: int = 0
    schedulers: int = 0
    latent_upscalers: int = 0
    image_upscalers: int = 0
    gradio_modules: int = 0
    model_loaded: bool = False
    plugins_loaded: int = 0
    plugins_failed: tuple = ()
    seconds: float = 0.0
    reason: str = ""
    #: What the compute runtime ACTUALLY became, not what was asked for. A
    #: setting the machine could not honour appears in `runtime["refused"]`,
    #: so a benchmark can never credit an accelerator that was not running.
    runtime: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.state == READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "samplers": self.samplers,
            "schedulers": self.schedulers,
            "latent_upscalers": self.latent_upscalers,
            "image_upscalers": self.image_upscalers,
            "gradio_modules": self.gradio_modules,
            "model_loaded": self.model_loaded,
            "plugins_loaded": self.plugins_loaded,
            "plugins_failed": list(self.plugins_failed),
            "seconds": round(self.seconds, 3),
            "reason": self.reason,
        }


#: The one report for this process, once a bootstrap has succeeded. A list for
#: the same reason `neo_registries._UPSCALER_SCAN_DONE` is one: this module
#: holds no other mutable state and a rebind would need `global`.
_COMPLETED: list[BootstrapReport] = []


def bootstrap_state() -> str:
    """What the bootstrap has achieved so far. Pure read; never initialises."""

    return _COMPLETED[0].state if _COMPLETED else NOT_STARTED


def completed_report() -> BootstrapReport | None:
    """The successful report, or None. Pure read."""

    return _COMPLETED[0] if _COMPLETED else None


def _gradio_modules() -> int:
    return len([n for n in sys.modules
                if n == "gradio" or n.startswith("gradio.")])


def _model_is_loaded() -> bool:
    """Whether a REAL engine is resident, not Forge's import-time sentinel.

    `modules.shared.sd_model` is seeded with a `FakeInitialModel` when
    `modules.shared` is imported, so a not-None check reports a loaded model on
    a process holding no weights at all.
    """

    shared = sys.modules.get("modules.shared")
    if shared is None:
        return False
    model = getattr(shared, "sd_model", None)
    return model is not None and type(model).__name__ != "FakeInitialModel"


def bootstrap_backend(
    repository_root: Path,
    *,
    result_root: Path | None = None,
    runtime_options: dict[str, Any] | None = None,
    force: bool = False,
) -> BootstrapReport:
    """Initialise the Studio backend runtime. Idempotent; never loads a model.

    Returns the cached report on a second call rather than re-registering
    anything. `set_samplers()` rebuilds Neo's list from its own source tables,
    so calling it twice is harmless, but re-running the whole sequence would
    re-scan the upscaler directory on every call for a list that cannot change
    without a restart.

    A FAILURE IS NOT LATCHED. Only a successful report is remembered, so a
    retryable failure stays retryable and a later call can still succeed.
    """

    if _COMPLETED and not force:
        return _COMPLETED[0]

    started = time.monotonic()
    root = Path(repository_root).resolve()

    # Refuse a root that is not an engine BEFORE touching any global.
    #
    # `process_options` is idempotent by design: the first object it builds
    # becomes the canonical one for the process and is never rebuilt. So a
    # bootstrap against a root with no option sources installed an
    # empty-defaults object, and every later bootstrap -- against the real root
    # -- inherited it and died on `hide_schedulers`, an option sitting in a file
    # it had never read.
    #
    # That is the false latch section 13 forbids, arriving through the options
    # rather than through the registry. Checking first means a bad root costs
    # nothing and leaves the process able to succeed afterwards.
    options_source = root / "modules" / "shared_options.py"
    if not options_source.is_file():
        return BootstrapReport(
            state=FAILED_RETRYABLE,
            gradio_modules=_gradio_modules(),
            seconds=time.monotonic() - started,
            reason=(
                "not a Studio backend root: no modules/shared_options.py "
                f"under {root.name}"
            ),
        )

    # The vendored packages directory carries `k_diffusion`, which
    # `modules.sd_samplers_kdiffusion` imports at module scope. Without it the
    # sampler import fails on a missing dependency that is sitting in the tree.
    for entry in (str(root), str(root / "modules_forge" / "packages")):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    # Neo parses `sys.argv` at import time in several places. Studio's own
    # arguments are already parsed by the time this runs, and leaving them
    # visible lets Neo's parsers consume flags meant for the launcher.
    sys.argv = [sys.argv[0] if sys.argv else ""]

    # The compute runtime, projected rather than passed.
    #
    # Blanking argv above is deliberate and stays -- but it also meant Neo's
    # performance flags could never reach it, so Studio ran stock PyTorch
    # attention with no allocator, stream or pinned-memory policy while Neo
    # ran with six flags from `webui-user.bat`. Studio owns these settings
    # instead, and writes them where the inherited modules read them.
    #
    # ORDER IS THE WHOLE THING. `cuda_malloc` is an environment variable the
    # CUDA allocator reads when torch first initialises, so it goes before any
    # torch import. The rest are read AT MODULE SCOPE by
    # `backend/memory_management.py` (:331 fast_fp16, :1382 cuda_stream,
    # :1456 pin_shared_memory) out of the namespace `backend/args.py:122`
    # parsed, so they are written after that namespace exists and before the
    # module that reads it is imported. Written afterwards, they do nothing
    # and say nothing.
    from .runtime_options import apply_environment, effective, project, resolve

    runtime = resolve(runtime_options)
    environment = apply_environment(runtime)
    projected: dict[str, Any] = {}
    try:
        from backend.args import args as _backend_args

        projected = project(runtime, _backend_args)
    except Exception:  # noqa: BLE001 - a backend that cannot be configured
        # still boots; it simply boots at the floor, and says so below.
        projected = {}

    try:
        from .headless_options import output_directory_overrides, process_options

        overrides: dict[str, Any] = {}
        if result_root is not None:
            overrides.update(output_directory_overrides(Path(result_root)))
        # An OPTION, not an arg: `modules/upscaler_utils.py` branches on
        # `shared.opts.composite_tiles_on_gpu`, so it reaches the backend
        # through the options object rather than through `backend.args`. Same
        # Studio-owned decision, different door.
        #
        # Projected either way. While the default was off, projecting only the
        # True case was enough; now that it is on, an owner turning it off has
        # to be able to reach the backend too.
        overrides["composite_tiles_on_gpu"] = runtime.composite_tiles_on_gpu

        # And as a CEILING, separately. The option above is the default a job
        # inherits when it expresses no preference, and a job CAN override it.
        # The launcher saying no is not a default -- it means never in this
        # process -- so it is recorded where a job cannot reach it.
        from .upscale_preflight import disable_for_process

        disable_for_process(not runtime.composite_tiles_on_gpu)
        process_options(root, overrides=overrides)

        # The state bridge must exist BEFORE the samplers import, for the same
        # reason the options must: `modules/sd_samplers_common.py:29` does
        # `from modules.shared import opts, state`, binding both by VALUE at
        # import time.
        #
        # Moving the sampler import to startup moved it in front of the bridge
        # that a load used to install first. The samplers then bound
        # `shared.state` while it was still None, kept that None for the life of
        # the process, and every generation died on `AttributeError` inside the
        # sampler. Measured, not deduced: after bootstrap,
        # `sd_samplers_common.state` was None and identical to `shared.state`.
        #
        # `live_bindings._install_state_bridge` already ADOPTS an existing
        # bridge rather than building a second one -- it looks for exactly this
        # object and re-points its progress at the current job -- so installing
        # one here is the shape that code was written to expect, arriving
        # earlier than it used to.
        from modules import shared as _shared

        from .headless_progress import ForgeStateBridge, HeadlessProgress

        if not isinstance(getattr(_shared, "state", None), ForgeStateBridge):
            _shared.state = ForgeStateBridge(
                HeadlessProgress("studio-bootstrap", preview_enabled=False)
            )

        # THE import the product previously performed only by accident.
        from modules import sd_samplers, sd_schedulers  # noqa: F401

        sd_samplers.set_samplers()

        # Tier-2 compute plugins, AFTER the built-ins exist: these modules
        # extend `sd_samplers.all_samplers`, so the list has to be there to
        # extend. Each import is isolated, so a bad plugin cannot take the
        # registry with it, and one that imports Gradio is refused rather than
        # reported as working.
        from .compute_plugins import load_compute_plugins

        plugins = load_compute_plugins(root)

        from .neo_registries import read_registries

        registries = read_registries()
    except BaseException as error:  # noqa: BLE001 - startup reports, never dies
        # Retryable: Studio still serves, the menus stay honestly empty, and a
        # later read may succeed. Not remembered, so the next call retries.
        return BootstrapReport(
            state=FAILED_RETRYABLE,
            gradio_modules=_gradio_modules(),
            seconds=time.monotonic() - started,
            reason=f"{type(error).__name__}: {error}"[:200],
        )

    gradio = _gradio_modules()
    report = BootstrapReport(
        state=READY if gradio == 0 else FAILED_TERMINAL,
        samplers=len(registries.samplers),
        schedulers=len(registries.schedulers),
        latent_upscalers=len(registries.latent_upscalers),
        image_upscalers=len(registries.image_upscalers),
        gradio_modules=gradio,
        model_loaded=_model_is_loaded(),
        plugins_loaded=sum(1 for p in plugins if p.ok),
        plugins_failed=tuple(
            f"{p.name}: {p.state} {p.reason}" for p in plugins if not p.ok
        ),
        seconds=time.monotonic() - started,
        reason="" if gradio == 0 else (
            f"{gradio} real Gradio modules were imported during backend "
            "bootstrap; the Studio runtime must import none"
        ),
        # Observed, not requested: `projected` is what was actually written
        # onto the inherited namespace and `environment` what was actually
        # set. A benchmark reading this cannot credit an accelerator that
        # never ran.
        runtime={
            **runtime.describe(),
            "projected": dict(projected),
            "environment": dict(environment),
            # Read back from torch AFTER the backend imported, not from what
            # was asked for. A request is not an outcome, and the failure mode
            # this guards is a value set too late to be read.
            **effective(runtime),
        },
    )
    if report.ready:
        _COMPLETED.append(report)
    return report


__all__ = (
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
    "NOT_STARTED",
    "READY",
    "BootstrapReport",
    "bootstrap_backend",
    "bootstrap_state",
    "completed_report",
)
