"""A minimal, Gradio-free stand-in for `modules.shared.opts`.

Why not `modules.shared_init.initialize()`
------------------------------------------

That is Forge's own option bootstrap and it works, but it imports
`modules.shared_options`, which has twelve module-level paths to Gradio because
every option declares the settings-page component that edits it. Calling it from
the Studio headless path would put Gradio back on a path that is currently free
of it, and would also construct a styles database, a shared state object, and a
`total_tqdm` -- none of which a model load needs.

So this module supplies the small set of option values the retained loader
actually reads, and nothing else.

Where the values come from
--------------------------

Not from guesses, and not from a second copy of Forge's defaults that could
drift. `modules/shared_options.py` is **parsed as source** -- never imported, so
Gradio is never touched -- and the first positional argument of each
`OptionInfo(...)` is the default Forge itself would use. `emphasis` resolves to
`"Original"` because `shared_options.py:223` says so.

An option this object is asked for but cannot find in that file is a hard,
named error rather than a silent `None`. A silent `None` is exactly how the
previous attempt failed, and it should be impossible to reproduce quietly.

Lifetime
--------

`headless_options()` is a context manager. It installs the object on
`modules.shared.opts`, yields, and restores the previous value -- on success and
on exception alike. The bridge exists only for the controlled load.

**Ordering matters.** `backend/text_processing/anima_engine.py:11` does
`from modules.shared import opts`, binding the *value* at import time, so the
options must be installed before `backend.loader` is imported. Installing them
afterwards would leave that module holding the old `None`.
"""

from __future__ import annotations

import ast
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import HeadlessError, HeadlessOptionMissing


#: Options the retained Anima load path reads before `MODEL_READY`. Kept
#: explicit so the inventory is reviewable rather than implied.
INFERENCE_OPTIONS: tuple[str, ...] = (
    "emphasis",
)

#: Read after `MODEL_READY`, on generation paths this phase never runs. Listed
#: so the distinction is recorded rather than lost.
GENERATION_ONLY_OPTIONS: tuple[str, ...] = (
    "anima_do_reference",
)

#: Every setting an AST scan finds on the minimal Anima txt2img closure --
#: `modules/processing.py`, `sd_samplers*`, `backend/diffusion_engine/anima.py`,
#: `backend/text_processing/*`, `modules/images.py`. 99 distinct reads.
#:
#: They are listed rather than curated because the boundary answers from Forge's
#: full parsed default map anyway: the value of the list is that a test can
#: assert every one of them resolves, so a missing option is found here instead
#: of part-way through a generation.
GENERATION_OPTIONS: tuple[str, ...] = (
    "CLIP_stop_at_last_layers",
    "always_discard_next_to_last_sigma",
    "anima_do_reference",
    "beta_dist_alpha",
    "beta_dist_beta",
    "emphasis",
    "enable_pnginfo",
    "eta_noise_seed_delta",
    "forge_additional_modules",
    "forge_unet_storage_dtype",
    "live_previews_enable",
    "randn_source",
    "refiner_lora_replacement",
    "rho",
    "s_churn",
    "s_min_uncond",
    "s_noise",
    "s_tmax",
    "s_tmin",
    # Read by the LoRA extra-network handler. `sd_lora` is the one that
    # actually fails a generation when absent; the rest are read on the same
    # path and are listed so a missing one is found here rather than part-way
    # through a job.
    "extra_networks_default_multiplier",
    "forge_preset",
    "lora_add_hashes_to_infotext",
    "lora_preferred_name",
    "lora_preset_filter",
    "sd_lora",
    "samples_format",
    "save_prompt_comments",
    "scaling_factor",
    "sd_noise_schedule",
    "sd_vae_decode_method",
    "sd_vae_encode_method",
    "sgm_noise_multiplier",
    "show_progress_type",
    "sigma_max",
    "sigma_min",
    "tiling",
    "token_merging_ratio",
)

#: The only two generation-path settings Forge computes rather than declares
#: literally (`util.truncate_path(os.path.join(...))`), so they cannot be
#: parsed. Studio owns result delivery through `ResultRegistry`, so these are
#: supplied as explicit, contained overrides -- recorded as `override` in the
#: inventory, never presented as a Forge default.
COMPUTED_PATH_OPTIONS: tuple[str, ...] = (
    "outdir_init_images",
    "outdir_videos",
)


def output_directory_overrides(result_root: Path) -> dict[str, str]:
    """Contained values for the two options Forge computes at import time."""
    return {name: str(result_root) for name in COMPUTED_PATH_OPTIONS}

_OPTION_FACTORIES = ("OptionInfo", "OptionHTML", "OptionDiv", "OptionRow")

_lock = threading.Lock()
#: Parsed option defaults, keyed by resolved repository root.
_cached_defaults: dict[str, dict[str, Any]] = {}


def _unwrap_factory_call(node: ast.expr) -> ast.Call | None:
    """Find the `OptionInfo(...)` at the base of a method chain.

    Most options are declared as `OptionInfo(default, ...).info(...).html(...)`,
    so the outermost call is `.html`, not the factory. Walking down the chain is
    what makes `emphasis` -- the option this whole boundary exists for -- visible
    at all.
    """
    current = node
    while isinstance(current, ast.Call):
        func = current.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in _OPTION_FACTORIES:
            return current
        if isinstance(func, ast.Attribute):
            current = func.value
            continue
        return None
    return None


def parse_legacy_defaults(source_path: Path) -> dict[str, Any]:
    """Extract `name -> default` from `shared_options.py` without importing it.

    Walks the AST for dictionary entries whose key is a string literal and whose
    value is a call to one of Forge's option factories, then takes the first
    positional argument when it is a literal. Non-literal defaults (lambdas,
    computed values) are skipped: reporting "unknown" is better than evaluating
    arbitrary code to find out.
    """

    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise HeadlessError(
            "HEADLESS_OPTIONS_SOURCE_UNREADABLE",
            "Forge's option definitions could not be parsed.",
        ) from exc

    defaults: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                continue
            call = _unwrap_factory_call(value)
            if call is None:
                continue
            # Most declarations pass the default positionally; a few use
            # `OptionInfo(default=...)`. Missing the keyword form loses real
            # options, so both are read.
            node_default = call.args[0] if call.args else None
            if node_default is None:
                for keyword in call.keywords:
                    if keyword.arg == "default":
                        node_default = keyword.value
                        break
            if node_default is None:
                continue
            try:
                defaults[key.value] = ast.literal_eval(node_default)
            except (ValueError, TypeError):
                # A computed default (a path built at import time, a lambda).
                # Recording "unknown" beats evaluating arbitrary code to find
                # out; callers that need one supply an explicit override.
                continue
    return defaults


#: Every file where Forge declares an `OptionInfo`. `shared_options.py` holds
#: most of them, but not all: `modules_forge/shared_options.py` adds the Forge
#: UNet/storage options, and built-in processing scripts declare their own.
#: Parsing only the first file silently loses six options the generation path
#: reads, which would surface as failures mid-generation.
def option_sources(repository_root: Path) -> list[Path]:
    sources = [
        repository_root / "modules" / "shared_options.py",
        repository_root / "modules_forge" / "shared_options.py",
        # The LoRA extension declares four options of its own, and the handler
        # READS them: `extra_networks_lora.py:23` opens with
        # `shared.opts.sd_lora`. Studio never imports that script -- it uses
        # `gr.Dropdown` at module scope and a Gradio import is a terminal
        # bootstrap failure -- so without this entry the handler dies on
        # HEADLESS_OPTION_NOT_AVAILABLE the first time a <lora:...> tag is
        # activated. Measured, before this line existed.
        #
        # Safe because `parse_legacy_defaults` walks the AST "without
        # importing it": the defaults come from the engine's own declaration
        # and no Gradio is touched.
        repository_root / "extensions-builtin" / "sd_forge_lora" / "scripts"
        / "lora_script.py",
    ]
    scripts = repository_root / "modules" / "processing_scripts"
    if scripts.is_dir():
        sources.extend(sorted(scripts.glob("*.py")))
    return [path for path in sources if path.is_file()]


def legacy_defaults(repository_root: Path) -> dict[str, Any]:
    """Parsed defaults from every Forge option source. Cached per ROOT.

    The cache used to ignore `repository_root` entirely: the first call filled
    one module global and every later call returned it, whatever root it was
    asked about. Production never noticed, because production only ever asks
    about the install it is running from.

    A bootstrap that FAILS against one root and then succeeds against another
    does notice. The failing call parsed a directory with no option sources,
    cached the empty result, and every subsequent call inherited it -- so the
    real root came back with no `hide_schedulers` and the sampler import died
    on an option that is sitting in the file it did not read.

    Keyed by resolved root, so a cache entry answers only for the root it was
    built from. An empty parse is still cached, because "this root has no
    option sources" is an answer; it is now an answer about THAT root.
    """

    key = str(Path(repository_root).resolve())
    with _lock:
        if key not in _cached_defaults:
            merged: dict[str, Any] = {}
            for source in option_sources(repository_root):
                merged.update(parse_legacy_defaults(source))
            _cached_defaults[key] = merged
        return dict(_cached_defaults[key])


class HeadlessOptions:
    """Answers option reads from Forge's own defaults, and records every read.

    The recording is the point as much as the answering: it turns "which options
    does a real Anima load touch" from a static guess into an observation the
    evidence can carry.
    """

    def __init__(self, defaults: dict[str, Any], *, overrides: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_defaults", dict(defaults))
        object.__setattr__(self, "_overrides", dict(overrides or {}))
        object.__setattr__(self, "_reads", [])
        object.__setattr__(self, "_missing", [])

    # -- reads -------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        overrides = object.__getattribute__(self, "_overrides")
        defaults = object.__getattribute__(self, "_defaults")
        reads = object.__getattribute__(self, "_reads")

        # The running job wins, when it named this option. Read here rather
        # than written onto `_overrides`, because about forty modules bind
        # `opts` once at import and a write would be visible to every job in
        # the process -- a queued job would see the preference the NEXT one
        # was enqueued with. This is per-thread and leaves nothing behind.
        from .job_options import has_option, option
        if has_option(name):
            reads.append(name)
            return option(name)

        if name in overrides:
            reads.append(name)
            return overrides[name]
        if name in defaults:
            reads.append(name)
            return defaults[name]
        object.__getattribute__(self, "_missing").append(name)
        # AttributeError-compatible, so retained `getattr(opts, name, default)`
        # gets its default -- Forge writes those deliberately, and attempt 04
        # died because a plain HeadlessError bypassed one. A bare `opts.name`
        # still raises; `opts[name]` and `require()` stay strictly non-catchable
        # by a getattr default. See HeadlessOptionMissing.
        raise HeadlessOptionMissing(
            "HEADLESS_OPTION_NOT_AVAILABLE",
            f"The retained loader read option {name!r}, which the headless "
            "options boundary does not supply.",
        )

    def __getitem__(self, name: str) -> Any:
        """Strict read. Never satisfied by a `getattr` default."""
        return self.require(name)

    def require(self, name: str) -> Any:
        """Strict read for code that must not silently accept a default."""
        overrides = object.__getattribute__(self, "_overrides")
        defaults = object.__getattribute__(self, "_defaults")
        reads = object.__getattribute__(self, "_reads")

        # The running job wins, when it named this option. Read here rather
        # than written onto `_overrides`, because about forty modules bind
        # `opts` once at import and a write would be visible to every job in
        # the process -- a queued job would see the preference the NEXT one
        # was enqueued with. This is per-thread and leaves nothing behind.
        from .job_options import has_option, option
        if has_option(name):
            reads.append(name)
            return option(name)

        if name in overrides:
            reads.append(name)
            return overrides[name]
        if name in defaults:
            reads.append(name)
            return defaults[name]
        object.__getattribute__(self, "_missing").append(name)
        raise HeadlessError(
            "HEADLESS_OPTION_REQUIRED_NOT_AVAILABLE",
            f"Option {name!r} was required explicitly, and the headless "
            "options boundary does not supply it.",
        )

    # A few call-shaped members Forge code occasionally touches. Answering them
    # is safer than letting `__getattr__` raise on something that is not an
    # inference option at all.
    def get(self, name: str, default: Any = None) -> Any:
        try:
            return getattr(self, name)
        except (AttributeError, HeadlessError):
            return default

    @property
    def data(self) -> dict[str, Any]:
        return dict(object.__getattribute__(self, "_overrides"))

    @property
    def reads(self) -> tuple[str, ...]:
        return tuple(object.__getattribute__(self, "_reads"))

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Change values on THIS object rather than building a second one.

        A process-lifetime options object still has to follow a load whose
        result root differs from the last one's. Mutating in place is what
        keeps the forty modules that bound `opts` at import time pointing at
        the object the product is actually configured by.
        """

        object.__getattribute__(self, "_overrides").update(dict(overrides))

    @property
    def distinct_reads(self) -> tuple[str, ...]:
        return tuple(sorted(set(object.__getattribute__(self, "_reads"))))

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(sorted(set(object.__getattribute__(self, "_missing"))))

    def inventory(self) -> list[dict[str, Any]]:
        """What was actually read, with where each value came from."""
        overrides = object.__getattribute__(self, "_overrides")
        defaults = object.__getattribute__(self, "_defaults")
        rows: list[dict[str, Any]] = []
        for name in self.distinct_reads:
            source = "override" if name in overrides else "modules/shared_options.py"
            value = overrides.get(name, defaults.get(name))
            rows.append(
                {
                    "option": name,
                    "value": value,
                    "type": type(value).__name__,
                    "default_source": source,
                    "read_before_model_ready": name in INFERENCE_OPTIONS,
                }
            )
        return rows


@contextmanager
def headless_options(
    repository_root: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> Iterator[HeadlessOptions]:
    """Install a Gradio-free `opts` for the load, then restore what was there.

    Restores on success and on exception. The previous value is captured with
    `getattr(..., None)` rather than assumed to be `None`, so this composes with
    a real Forge session instead of clobbering it.
    """

    from modules import shared

    options = HeadlessOptions(
        legacy_defaults(repository_root), overrides=overrides
    )
    had_previous = hasattr(shared, "opts")
    previous = getattr(shared, "opts", None)
    shared.opts = options
    try:
        yield options
    finally:
        if had_previous:
            shared.opts = previous
        else:  # pragma: no cover - `opts` is always declared in shared.py
            try:
                del shared.opts
            except AttributeError:
                pass


#: The one options object this process uses, once something has asked for it.
#:
#: A list rather than a rebindable name because this module holds no other
#: mutable state and a rebind would need `global` -- the same reason
#: `neo_registries._UPSCALER_SCAN_DONE` is a list.
_PROCESS_OPTIONS: list[HeadlessOptions] = []


def process_options(
    repository_root: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> HeadlessOptions:
    """Install ONE options object for the life of the process. Idempotent.

    Why this exists alongside `headless_options()`, rather than replacing it.

    Forty modules bind the VALUE at import time -- `from modules.shared import
    opts` -- across `modules/` (21), `backend/` (18) and `modules_forge/` (1),
    including `backend/diffusion_engine/anima.py:11`,
    `modules/sd_samplers_kdiffusion.py:12` and `modules/processing.py:34`.
    Measured, not estimated. A name bound that way is resolved once and never
    re-read, so whatever object is installed when a module is first imported is
    the object that module uses forever.

    The context-manager form installs an object for one load and then RESTORES
    the previous value. The second load builds a new object and installs it --
    while all forty modules still hold the first. That is not a hypothetical:
    `neo_registries._read` records a generation failing for exactly this
    reason, and `live_bindings._install_state_bridge` carries the same lesson
    for `shared.state`, where the fix was to adopt the already-bound bridge
    instead of building a second one. This is that fix, for `opts`.

    So: construct once, install once, never restore, and let later loads update
    the overrides ON the same object rather than replace it.

    `headless_options()` is kept for tests that want an isolated, restoring
    scope. Production takes this path.
    """

    from modules import shared

    if not _PROCESS_OPTIONS:
        _PROCESS_OPTIONS.append(
            HeadlessOptions(legacy_defaults(repository_root), overrides=overrides)
        )
    elif overrides:
        # Same object, new values. A load with a different result root must
        # change where output goes without handing the engine a second opts.
        _PROCESS_OPTIONS[0].apply_overrides(overrides)
    shared.opts = _PROCESS_OPTIONS[0]
    return _PROCESS_OPTIONS[0]


def installed_process_options() -> HeadlessOptions | None:
    """The process options object, or None if nothing has installed one yet.

    A read, for diagnostics and tests. Never installs.
    """

    return _PROCESS_OPTIONS[0] if _PROCESS_OPTIONS else None


__all__ = (
    "COMPUTED_PATH_OPTIONS",
    "GENERATION_ONLY_OPTIONS",
    "GENERATION_OPTIONS",
    "INFERENCE_OPTIONS",
    "HeadlessOptions",
    "headless_options",
    "installed_process_options",
    "legacy_defaults",
    "output_directory_overrides",
    "parse_legacy_defaults",
    "process_options",
)
