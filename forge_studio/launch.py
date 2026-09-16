"""Internal-alpha launcher: one configured, contained, NO_MODEL start.

One documented command:

```text
venv\\Scripts\\python.exe launch_studio.py --config <path-to-config.json>
```

The launcher owns exactly the bounded configuration contract Internal Alpha
Phase 1 declared -- backend kind, bind host, port, result root, autoload,
selected profile id, profiles -- validates all of it BEFORE constructing
anything, starts the stdlib Studio server in `NO_MODEL`, and performs no
model, payload, or CUDA work at startup. Loading is an explicit act in the
frontend, exactly as it is everywhere else in the product.

Explicit load access is granted per load through an opaque access factory
owned by the headless layer (`forge_headless/explicit_access.py`): a fresh
controlled authorization per explicit load, built from the SELECTED
profile's own role references at that moment. This module never names the
authorization machinery -- the no-public-bypass guard pins that -- and
load -> unload -> load works across one app session without a restart.

Import-safe: importing this module reaches no torch, no backend, no modules.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .state_root import (
    WINDOWS,
    refuse_conflicting_root,
    resolve_state_root,
)

APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = APP_ROOT.parent

_LOOPBACK = "127.0.0.1"
_MAX_TIMEOUT_SECONDS = 600
_DEFAULT_VRAM_CEILING_GIB = 14

logger = logging.getLogger("studio.internal_alpha")


class LaunchConfigurationError(Exception):
    """A configuration problem, named plainly enough to fix from the message."""


def _fail(message: str) -> LaunchConfigurationError:
    return LaunchConfigurationError(message)


def load_config(path: Path) -> dict[str, Any]:
    """Read and validate the bounded launch contract. No side effects."""

    if not path.exists():
        raise _fail(
            f"Configuration file not found: {path}. Copy the template from "
            "docs/studio/internal-alpha/studio-config.template.json and fill "
            "in your profiles."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"Configuration is not readable JSON: {type(exc).__name__}")

    if not isinstance(raw, dict):
        raise _fail("Configuration must be a JSON object.")

    backend = raw.get("backend", "headless")
    if backend not in ("headless", "mock"):
        raise _fail("backend must be 'headless' or 'mock'.")

    host = raw.get("host", _LOOPBACK)
    if host != _LOOPBACK:
        raise _fail(
            "Internal alpha binds 127.0.0.1 only; set host to '127.0.0.1'."
        )

    port = raw.get("port", 0)
    if not isinstance(port, int) or isinstance(port, bool) or not (
        0 <= port <= 65535
    ):
        raise _fail("port must be an integer between 0 and 65535 (0=ephemeral).")

    onboarding = raw.get("onboarding", "default")
    if onboarding not in ("default", "suppressed"):
        raise _fail(
            "onboarding must be 'default' (owner mode: first-run overlays "
            "show once) or 'suppressed' (smoke mode: the announced URL "
            "carries ?onboarding=off and nonessential overlays stay away)."
        )

    result_root_raw = raw.get("result_root")
    if not isinstance(result_root_raw, str) or not result_root_raw.strip():
        raise _fail("result_root is required (a directory for Studio results).")
    result_root = Path(result_root_raw).expanduser()
    if not result_root.is_absolute():
        result_root = WORKSPACE_ROOT / result_root
    result_root = result_root.resolve()
    try:
        result_root.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise _fail(
            "result_root must live inside the Studio workspace, so results "
            f"stay contained. Workspace: {WORKSPACE_ROOT}"
        )

    # -- legacy keys ------------------------------------------------------
    #
    # `profiles`, `selected_profile_id` and `autoload` described a model the
    # owner configured by absolute path, selected by id, and then explicitly
    # loaded. None of those steps exist: the model is three catalogue ids
    # carried on the generation request. The keys are READ but not honoured,
    # and deliberately not validated -- a legacy profile still holding a
    # template placeholder, or naming a checkpoint that has since been moved,
    # must not stop Studio from starting. The owner needs the app running in
    # order to correct their configuration.
    legacy_keys = tuple(
        key for key in ("profiles", "selected_profile_id", "autoload")
        if key in raw
    )

    last_model_selection = _read_remembered_selection(raw.get("last_model_selection"))

    # Shape only. Whether a directory exists, is local, and is enumerable is
    # decided by the catalogue at construction time, not here: load_config is
    # documented as having no side effects, and a model directory that has been
    # renamed since last launch must not stop Studio from starting.
    from forge_headless.contracts import HeadlessError
    from forge_headless.model_roots import normalize_roots

    try:
        model_roots = normalize_roots(raw.get("model_roots"))
        model_roots_refusal = None
    except HeadlessError as error:
        # NOT fatal. This was the last path by which the config file could
        # stop Studio from starting, and it is the one an owner is least able
        # to recover from: the app is where model directories are edited, so
        # refusing to launch over a malformed `model_roots` locks them out of
        # the only tool that fixes it. It is also how a DOWNGRADE would have
        # bitten -- an older build reading a roots list it does not understand
        # would have exited 2 with no way back.
        #
        # Startup is already tolerant of a directory that has been renamed
        # (`configure_tolerantly`); this makes it equally tolerant of a
        # directory list it cannot parse. The reason is carried through and
        # logged, and the roles simply come up unconfigured, which the
        # Settings page already renders and the owner can already fix.
        model_roots = {}
        model_roots_refusal = getattr(error, "code", None) or "unknown"

    # The detectors Studio SHIPS live in `app/models/adetailer`, and their
    # sha256s are recorded in `detector_catalogue.BUNDLED_DETECTORS`. With the
    # role left unconfigured, `scan_configured_detectors` yielded nothing, so
    # Auto Detail offered an empty Model dropdown on a fresh install -- and the
    # only way out was for the owner to open Settings and type a path INSIDE
    # Studio's own install directory, to reach files Studio put there.
    #
    # That is the chore the standing ruling forbids: Studio decides where its
    # own assets are, and an owner never edits configuration to reach what
    # Studio shipped.
    #
    # Seeded into the ROOTS rather than hidden behind the catalogue, so
    # `configured_roots()` -- and therefore the Settings page -- says where
    # detectors are actually being read from. A catalogue-level fallback would
    # have made those two disagree about the same directory, which is the exact
    # failure `detector_catalogue` argues against having two enumerators for.
    #
    # It fills an EMPTY role only. An owner who names their own detector
    # directory replaces this outright, and can point at the bundled folder
    # again by name if they want both.
    if not model_roots.get("adetailer"):
        _bundled_detectors = APP_ROOT / "models" / "adetailer"
        if _bundled_detectors.is_dir():
            model_roots = dict(model_roots)
            model_roots["adetailer"] = (str(_bundled_detectors),)

    browser = raw.get("filesystem_browser", {})
    if not isinstance(browser, dict):
        browser = {}
    # Default ON. A directory picker the owner cannot use is not safer than
    # one they can; the switch is here for the machine where browsing should
    # not be possible at all, not as the safety measure itself.
    filesystem_browser_enabled = browser.get("enabled", True) is not False

    access = raw.get("load_access", {})
    if not isinstance(access, dict):
        raise _fail("load_access must be an object when present.")
    timeout = access.get("timeout_seconds", _MAX_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or not (0 < timeout <= _MAX_TIMEOUT_SECONDS):
        raise _fail(
            f"load_access.timeout_seconds must be 1..{_MAX_TIMEOUT_SECONDS}."
        )
    ceiling_gib = access.get("vram_ceiling_gib", _DEFAULT_VRAM_CEILING_GIB)
    if not isinstance(ceiling_gib, int) or not (1 <= ceiling_gib <= 48):
        raise _fail("load_access.vram_ceiling_gib must be 1..48.")

    # -- the Studio state root (owner decision D1) ------------------------
    #
    # ONE configurable root. Native installs get the platform-conventional
    # per-user location; portable and image-based deployments override it
    # explicitly, through config or the environment.
    #
    # The wording is constrained: a purity test asserts this module never
    # mentions packaging vocabulary, guarding the native launcher against
    # acquiring packaging-specific behaviour. This comment adds none -- but that
    # guard is a text check and cannot tell describing from doing, so the guard
    # wins and the prose moves around it. The comments-trip-checks trap this
    # codebase records, arriving from the other direction.
    #
    # Deliberately NOT contained inside WORKSPACE_ROOT the way `result_root`
    # is. The whole point of a per-user application-data directory is that it
    # survives the install being replaced, and containing it here would put the
    # owner's settings back inside the directory an update deletes.
    import os
    import platform as _platform

    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        # Raises when no home can be determined -- a service account, or an
        # image with no passwd entry. That is precisely what the override
        # exists for, so it is passed through as None rather than guessed at.
        home = None

    state = resolve_state_root(
        system=_platform.system(),
        environ=os.environ,
        home=home,
        config_value=raw.get("studio_state_root"),
    )
    refuse_conflicting_root(
        state.root,
        install_root=str(APP_ROOT),
        result_root=str(result_root),
        # `normalize_roots` yields {role: (path, ...)}, so every configured
        # directory across every role is checked -- not just the first.
        model_roots=tuple(
            path for paths in model_roots.values() for path in paths
        ),
        windows=state.platform == WINDOWS,
    )

    return {
        "backend": backend,
        "host": host,
        "port": port,
        "studio_state_root": state.root,
        "studio_state_root_source": state.source,
        "onboarding": onboarding,
        "result_root": result_root,
        "model_roots": model_roots,
        "model_roots_refusal": model_roots_refusal,
        "filesystem_browser_enabled": filesystem_browser_enabled,
        "last_model_selection": last_model_selection,
        "legacy_keys": legacy_keys,
        "timeout_seconds": timeout,
        "vram_ceiling_bytes": ceiling_gib * 1024**3,
    }


def _read_remembered_selection(raw: Any) -> dict[str, str]:
    """The dropdown choices to restore, or nothing. NOT a load instruction.

    This is the one piece of "what model" that survives a restart, and it
    survives as a *preference*: the frontend uses it to pre-select three
    dropdowns, and nothing else may read it. Studio still starts in NO_MODEL
    and still loads nothing until a job asks. If this value could reach the
    lifecycle it would recreate `selected_profile_id` under a new name, and a
    cold launch would stop being cold.

    Malformed or unrecognised entries are dropped individually rather than
    refused, because the worst consequence of a bad preference is a dropdown
    that opens unselected. Ids are checked for SHAPE only -- 32 lowercase hex,
    as the catalogue mints them. Whether one still resolves is the
    catalogue's business at request time, and a model deleted since last
    launch must not stop this one.
    """

    if not isinstance(raw, dict):
        return {}
    remembered: dict[str, str] = {}
    for role in ("checkpoint", "text_encoder", "vae"):
        value = raw.get(role)
        if not isinstance(value, str):
            continue
        candidate = value.strip().lower()
        if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate):
            remembered[role] = candidate
    return remembered


def _configure_logging(timeline: bool = False) -> Path:
    log_directory = WORKSPACE_ROOT / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "studio-internal-alpha.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    if timeline:
        # ONE logger, not the root. The generation timeline is per-stage
        # timings, effective sizes and per-move evictions -- useful when a
        # performance question is open, noise otherwise, and the handoff is
        # explicit that the normal log stays concise. Raising the root to
        # DEBUG here would also turn on every inherited library's debug
        # output, which is not what anyone asked for.
        logging.getLogger("studio.generation").setLevel(logging.DEBUG)
    return log_path


def run(config_path: Path, runtime: dict[str, Any] | None = None,
        timeline: bool = False) -> int:
    """Validate, construct, serve, and shut down cooperatively."""

    config = load_config(config_path)
    log_path = _configure_logging(timeline=timeline)

    from forge_headless.explicit_access import ExplicitLoadAccess
    from forge_headless.live_bindings import LoadConfiguration
    from forge_studio import GenerationRequest
    from forge_studio.composition import build_standalone
    from forge_studio.presentation import StudioPresentation, _StudioHTTPServer

    load_configuration = None
    if config["backend"] == "headless":
        load_configuration = LoadConfiguration(
            repository_root=APP_ROOT,
            payload_opener=ExplicitLoadAccess(
                timeout_seconds=config["timeout_seconds"],
                vram_ceiling_bytes=config["vram_ceiling_bytes"],
            ),
        )

    config["result_root"].mkdir(parents=True, exist_ok=True)
    # No profiles. The catalogue built from `model_roots` is the only source
    # of models, and a job names its three ids against it.
    composition = build_standalone(
        backend_kind=config["backend"],
        load_configuration=load_configuration,
        result_root=config["result_root"],
    )
    lifecycle = composition.model_lifecycle
    # A remembered `selected_profile_id` no longer touches the lifecycle at
    # startup. It was the last path by which configuration could decide what
    # is resident, and reading it here would make a cold launch differ from a
    # cold launch -- which is the whole point of starting in NO_MODEL. The key
    # is still parsed and still tolerated; P0.3e replaces it with a remembered
    # UI preference that the frontend applies to its dropdowns and that
    # nothing may treat as a load instruction.

    state = lifecycle.state()["state"]
    logger.info("Studio internal alpha starting in state: %s", state)
    if config["legacy_keys"]:
        logger.info(
            "Ignoring retired configuration keys: %s. They are kept in the "
            "file and do nothing; the model is chosen in the app.",
            ", ".join(config["legacy_keys"]),
        )
    if config["last_model_selection"]:
        logger.info(
            "Remembered dropdown selection for %s. Nothing is loaded until a "
            "generation asks.",
            ", ".join(sorted(config["last_model_selection"])),
        )
    logger.info(
        "Studio state root: %s (%s)",
        config["studio_state_root"],
        config["studio_state_root_source"],
    )
    if config["port"] == 0:
        # Stated because it is otherwise undetectable. A browser scopes its
        # own storage to an origin, and an origin includes the port -- so a
        # launch on a fresh ephemeral port reads none of the settings the last
        # one wrote, and then rewrites several of them from defaults. The next
        # session does not look damaged; it looks new. Proven in a browser:
        # Evidence/r1-d1-origin-scope/.
        #
        # Preferences and saved defaults are unaffected. They live on the state
        # root above and are served to whatever origin asks.
        logger.info(
            "This launch uses an ephemeral port, so the browser treats it as a "
            "new site: theme, layout, panel sizes, tool settings and tour "
            "progress start from defaults. Settings kept on the state root are "
            "not affected. Set a fixed port to keep the rest between launches."
        )
    logger.info("Log file: %s", log_path)

    # R1.5 Phase D: Studio initialises its own backend, on purpose, here.
    #
    # The sampler, scheduler and upscaler registries used to be empty until the
    # first generation imported the engine as a side effect, so an owner who
    # opened Studio saw "Engine default" in every menu until they had generated
    # once. This is the intentional replacement for that accident.
    #
    # It loads NO MODEL. Studio stays in NO_MODEL until a generation asks, and
    # the report below says so. It imports no real Gradio, and verifies that
    # from `sys.modules` rather than assuming it.
    #
    # Only for the headless backend: the mock has no engine to initialise, and
    # standing one up for it would be a second initialisation path.
    if config["backend"] == "headless":
        from forge_headless.backend_bootstrap import READY, bootstrap_backend

        # `runtime` is whatever the launcher was given, which for an owner
        # who has not touched Start-Studio.bat is nothing at all. Studio then
        # decides from what the machine can verify -- no config file, and no
        # need to know what cudaMallocAsync is.
        bootstrap = bootstrap_backend(
            APP_ROOT, result_root=config["result_root"],
            runtime_options=runtime,
        )
        if bootstrap.state == READY:
            logger.info(
                "Studio backend ready in %.1fs: %d samplers, %d schedulers, "
                "%d latent and %d image upscalers. No model is loaded.",
                bootstrap.seconds, bootstrap.samplers, bootstrap.schedulers,
                bootstrap.latent_upscalers, bootstrap.image_upscalers,
            )
            effective = bootstrap.runtime or {}
            logger.info(
                "Compute runtime: attention=%s allocator=%s streams=%s "
                "pinned=%s fast-fp16=%s autotune=%s",
                effective.get("attention", "?"),
                "cuda-malloc" if effective.get("cuda_malloc") else "default",
                effective.get("cuda_stream", 0),
                "on" if effective.get("pin_shared_memory") else "off",
                "on" if effective.get("fast_fp16") else "off",
                # REQUESTED and EFFECTIVE, separately. A machine without cuDNN
                # asks and gets nothing; an owner must not have to infer that
                # from a stopwatch.
                "{}/{}".format(
                    "on" if effective.get("autotune_requested") else "off",
                    "on" if effective.get("autotune_effective") else "off"),
            )
            for refusal in effective.get("refused", ()):
                # Said out loud. A setting asked for and not honoured is the
                # difference between a benchmark that means something and one
                # that credits an accelerator which never ran.
                logger.warning(
                    "Compute runtime: %s was requested but is not available "
                    "on this machine.", refusal,
                )
        else:
            # Studio still serves. The menus stay honestly empty rather than
            # showing names the engine cannot dispatch, which is the same rule
            # the cold registry answer has always followed.
            logger.warning(
                "Studio backend bootstrap did not complete (%s): %s. Sampler "
                "and scheduler menus will be empty until it succeeds.",
                bootstrap.state, bootstrap.reason,
            )

    from forge_headless.model_roots import ModelRootRegistry
    from forge_studio.model_root_settings import ModelRootSettings

    registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
    # Tolerant at startup only: a model directory renamed since last launch must
    # not stop Studio from starting, because the owner needs the app running in
    # order to correct it. The Settings write is all-or-nothing instead.
    if config["model_roots_refusal"]:
        logger.warning(
            "The model_roots setting could not be read (%s); every model type "
            "starts unconfigured. Set the directories in Settings -- Studio "
            "is running so that you can.",
            config["model_roots_refusal"],
        )
    root_status = registry.configure_tolerantly(config["model_roots"])
    for role in sorted(root_status):
        entry = root_status[role]
        if entry.status == "refused":
            logger.warning(
                "Model directory for %s was refused (%s); configure it in "
                "Settings.",
                role,
                entry.reason_code,
            )
        elif entry.status == "partial":
            # Named separately from `ready`: some of this role's directories
            # are serving and some are not, and reporting only the total would
            # let a dead directory hide behind a live one.
            logger.warning(
                "Model directories for %s: %d found, but %d of %d could not "
                "be used; check them in Settings.",
                role,
                entry.entry_count,
                sum(1 for root in entry.roots if root.status == "refused"),
                len(entry.roots),
            )
        elif entry.status == "ready":
            logger.info("Model directory for %s: %d found.", role, entry.entry_count)
    settings = ModelRootSettings(
        registry,
        config_path=config_path,
        # Handed in, not read from disk a second time: `load_config` already
        # decided which remembered ids are well-formed, and two readers of one
        # key eventually disagree about what it means.
        last_model_selection=config["last_model_selection"],
    )

    # Auto-load on Generate. This is the ONLY registry in the product, and the
    # resolver is bound to that instance rather than building its own: a second
    # registry means a second case-policy probe and a second containment
    # snapshot, which can disagree about what is inside a root while both
    # believe themselves authoritative.
    #
    # Attached here rather than in the composition root because the
    # application is built before the roots are configured -- configuring them
    # tolerantly must not be able to stop Studio from starting, since the owner
    # needs the app running in order to fix a bad root.
    from forge_studio.model_selection import make_selection_resolver

    composition.application.use_selection_resolver(make_selection_resolver(registry))

    # The detector catalogue, bound to that SAME registry and for the same
    # reason. Read through a callable rather than snapshotted, so an owner who
    # points at a detector folder mid-session gets validated against what is
    # there now -- the roots write already re-reads /api/detectors, and a
    # cached tuple here would let the two disagree.
    from forge_studio.detector_catalogue import scan_configured_detectors

    composition.application.use_detector_catalogue(
        lambda: scan_configured_detectors(registry)
    )

    # And the resolver the RUNTIME needs. `detect` requires a path, the
    # catalogue refuses to carry one, and the generation runtime has no
    # registry -- so the turn from an admitted name into a file happens here,
    # against the same registry that answers /api/detectors, and travels down
    # with the request.
    from forge_studio.detector_catalogue import resolve_detector_path

    composition.application.use_detector_resolver(
        lambda name: resolve_detector_path(name, registry)
    )

    # Built before the presentation, because the presentation reads it at
    # request assembly for the server-owned execution policy. The state root
    # matters: a bare `PreferenceStore()` would resolve a different file than
    # the one the owner's Settings write to.
    from forge_studio.preferences import DefaultsStore, PreferenceStore

    preferences = PreferenceStore(config["studio_state_root"])
    defaults = DefaultsStore(config["studio_state_root"])

    # A SECOND service, rooted at the same state root as the adapter's. That
    # is deliberate and it is safe, which is worth writing down because the
    # obvious reading is that it should be shared.
    #
    # The two cannot drift on the two things that matter. The enabled flag is
    # read through `_Document`, which re-reads the file on every call and holds
    # no copy. The folder is re-read by `library()` on every call too, and the
    # cached walk is rebuilt whenever the resolved root differs from the one in
    # hand -- so the folder the owner picks in Settings is the folder Generate
    # expands against, on the very next generation, with no plumbing between
    # them.
    #
    # What they DO share is the staleness of an edited wildcard FILE: neither
    # reloads until something asks it to. That is pre-existing and equal on
    # both sides, not a consequence of there being two.
    from forge_studio.preferences import WildcardSettings
    from forge_studio.wildcard_service import WildcardService

    wildcards = WildcardService(
        WildcardSettings(config["studio_state_root"]),
        checkout=APP_ROOT,
    )

    presentation = StudioPresentation(
        composition.application, GenerationRequest,
        preferences=preferences, wildcards=wildcards,
    )

    from forge_studio.fs_service import FilesystemService

    # Enabled by default. The switch exists so an owner who does not want a
    # directory browser on this machine can turn it off; it is not a stand-in
    # for building the picker safely, and Studio ships with it on.
    filesystem_service = FilesystemService(
        registry=registry, enabled=config["filesystem_browser_enabled"]
    )
    settings.on_change(filesystem_service.invalidate)

    # D1 stage 2: the state root stops being a path that was merely resolved.
    #
    # Neither call touches the disk. Nothing is created until the owner's first
    # save, which is what keeps "the preferences file does not exist" a usable
    # signal for the migration that follows this stage.
    server = None
    try:
        server = _StudioHTTPServer(
            (config["host"], config["port"]),
            presentation,
            model_root_settings=settings,
            filesystem_service=filesystem_service,
            preferences=preferences,
            defaults=defaults,
            state_root=config["studio_state_root"],
            result_root=config["result_root"],
        )
        actual_port = int(server.server_address[1])
        # The machine-readable ready line. Emitted exactly once, only AFTER
        # the bind above succeeded (a bind failure raises before this line),
        # and flushed so a piped consumer reads it promptly without -u.
        # Scalars only -- never a path.
        print(
            f"STUDIO_READY host={config['host']} port={actual_port}",
            flush=True,
        )
        onboarding_suffix = (
            "?onboarding=off" if config["onboarding"] == "suppressed" else ""
        )
        print(
            f"Forge Studio internal alpha: "
            f"http://{config['host']}:{actual_port}/studio/{onboarding_suffix}",
            flush=True,
        )
        print(
            "Loopback only. Choose a model and press Generate. Ctrl+C to stop.",
            flush=True,
        )
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nStudio internal alpha stopping.", flush=True)
    finally:
        if server is not None:
            server.server_close()
        composition.shutdown()
        logger.info("Studio internal alpha stopped cooperatively.")
    return 0


def _runtime_from(arguments: Any) -> dict[str, Any]:
    """The compute-runtime request the launcher was given, if any.

    An empty dict means "decide for me", which is what an owner who has not
    touched `Start-Studio.bat` gets. Every value here is still validated
    against what the machine can actually do.
    """

    request: dict[str, Any] = {}
    if getattr(arguments, "compatibility", False):
        return {"mode": "compatibility"}
    if getattr(arguments, "pytorch_attention", False):
        request["attention"] = "pytorch"
    elif getattr(arguments, "sage", False):
        request["attention"] = "sage"
    elif getattr(arguments, "xformers", False):
        request["attention"] = "xformers"
    for name in ("cuda_malloc", "pin_shared_memory", "fast_fp16", "autotune",
                 "composite_tiles_on_gpu"):
        if getattr(arguments, name, False):
            request[name] = True
        elif getattr(arguments, f"no_{name}", False):
            request[name] = False
    streams = getattr(arguments, "cuda_stream", None)
    if streams is not None:
        request["cuda_stream"] = streams
    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch Forge Studio internal alpha from a configuration file."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=WORKSPACE_ROOT / "studio-config.json",
        help="path to the launch configuration "
             "(default: <workspace>/studio-config.json)",
    )
    # NEO'S OWN FLAG NAMES, on purpose. An owner coming from Forge already has
    # a line like this in `webui-user.bat`, and the useful property is that it
    # can be pasted into `Start-Studio.bat` unchanged. Studio parses them here
    # and PROJECTS them; Neo's own parsers still see a blank argv, so the
    # isolation `backend_bootstrap` exists for is untouched.
    speed = parser.add_argument_group(
        "compute runtime",
        "Optional. Studio picks the best it can verify on this machine "
        "without any of these.",
    )
    # ONE DELIBERATE DIVERGENCE. In Neo these mean "pip install this"
    # (`backend/args.py:67,69`) and the attention backend is then chosen by
    # import success. Studio does not install packages on an owner's behalf,
    # so here they mean "use it" -- and if the package is absent, Studio says
    # so at startup instead of quietly running something else.
    speed.add_argument("--sage", action="store_true",
                       help="use SageAttention (needs the sageattention package)")
    speed.add_argument("--xformers", action="store_true",
                       help="use xFormers attention (needs the xformers package)")
    speed.add_argument("--use-pytorch-cross-attention", dest="pytorch_attention",
                       action="store_true", help="force stock PyTorch attention")
    speed.add_argument("--cuda-malloc", dest="cuda_malloc",
                       action="store_true", help="use the async CUDA allocator")
    speed.add_argument("--no-cuda-malloc", dest="no_cuda_malloc",
                       action="store_true", help="do not use it")
    # `nargs="?"` with `const=2`, exactly as `backend/args.py:94` declares it,
    # so a pasted line carrying a bare `--cuda-stream` means what it meant.
    speed.add_argument("--cuda-stream", dest="cuda_stream", type=int,
                       nargs="?", const=2, metavar="N",
                       help="asynchronous weight-offload streams (default 2)")
    speed.add_argument("--pin-shared-memory", dest="pin_shared_memory",
                       action="store_true", help="pin shared memory")
    speed.add_argument("--no-pin-shared-memory", dest="no_pin_shared_memory",
                       action="store_true", help="do not pin it")
    speed.add_argument("--autotune", dest="autotune", action="store_true",
                       help="let cuDNN search for the fastest convolution per "
                            "shape. Same mathematics, possibly a different "
                            "reduction order, so images can differ in the last "
                            "bit. Costs time on the first use of each new "
                            "shape. CUDA/ROCm only; inert elsewhere.")
    speed.add_argument("--fast-fp16", dest="fast_fp16", action="store_true",
                       help="accumulate fp16 matmuls in fp16, and prefer fp16 "
                            "storage for models that offer it. About 1.2x "
                            "faster on NVIDIA. CHANGES THE IMAGE -- not "
                            "slightly: same composition, different fine "
                            "detail. Off unless asked for.")
    speed.add_argument("--gpu-tile-composite", dest="composite_tiles_on_gpu",
                       action="store_true",
                       help="blend upscaler tiles on the device instead of "
                            "through PIL. Faster, and CHANGES OUTPUT: the CPU "
                            "path rounds each tile to 8 bits before blending, "
                            "so results differ by up to about 2/255.")
    speed.add_argument("--no-gpu-tile-composite",
                       dest="no_composite_tiles_on_gpu",
                       action="store_true",
                       help="always blend upscaler tiles through PIL. Slower, "
                            "but its memory cost does not grow with the "
                            "image, and it is the path Studio falls back to "
                            "on its own when a frame will not fit.")
    speed.add_argument("--compatibility", action="store_true",
                       help="stock PyTorch only. The floor, for diagnosis.")
    parser.add_argument("--timeline", action="store_true",
                        help="log a per-generation timing breakdown: stage "
                             "durations, the size each stage actually "
                             "processed, and every model move with what it "
                             "evicted. For answering a performance question; "
                             "off by default because it is noise otherwise.")

    # Neo accepts these and Studio does not need them. Swallowed rather than
    # refused so a pasted `webui-user.bat` line does not stop Studio booting
    # over a flag that was only ever about installing packages.
    parser.add_argument("--uv", action="store_true", help=argparse.SUPPRESS)

    arguments, unknown = parser.parse_known_args(argv)
    for flag in unknown:
        print(f"Ignoring unrecognised option: {flag}", file=sys.stderr,
              flush=True)
    try:
        return run(arguments.config, runtime=_runtime_from(arguments),
                   timeline=bool(getattr(arguments, "timeline", False)))
    except LaunchConfigurationError as exc:
        # A refused configuration reaches this line without ever printing
        # STUDIO_READY: the ready line lives after the successful bind.
        print(f"Configuration error: {exc}", file=sys.stderr, flush=True)
        return 2


__all__ = (
    "LaunchConfigurationError",
    "load_config",
    "main",
    "run",
)
