"""Real defaults for `HeadlessSessionLoader`, bound to proven primitives.

Phase 2 left four loader steps refusing rather than guessing. This module binds
them. Almost nothing here is new logic: three of the four compose owners that
already exist in this package, and the fourth calls Forge's own loader entry.
The value is in the *wiring* and in the ordering, which was paid for live.

```text
StudioStartupGlobals    headless_options + ForgeStateBridge + HeadlessCompatibilityContext
ControlledPayloadOpener ControlledLoadAuthorization + validate_exact_path
                        + initialize_cuda + PayloadWatch
build_forge_engine      backend.loader.forge_loader
publish_engine          the import-order constraint, then shared.sd_model
```

Every import that touches Forge, Neo or torch is deferred into a function body.
Importing this module reaches none of them.

SCOPE: construction is free. Only `open()` and `build()` cross the live
boundary, and only an explicit product load calls them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: The roles a Studio profile must supply, in the order they are opened.
ROLE_ORDER: tuple[str, ...] = ("checkpoint", "text_encoder", "vae")

def _authorized(authorization: Any, role: str) -> bool:
    """Whether this load carries a payload for `role`.

    Tolerant on purpose. The real `ControlledLoadAuthorization` answers
    `has_role`, but this seam is also handed doubles and older authorization
    objects that predate it, and a missing method must not read as "the role
    is absent" -- that would silently drop the text encoder from an Anima load.
    So: ask if it can answer, otherwise assume the role is carried, which is
    what every caller meant before components became optional.
    """

    asked = getattr(authorization, "has_role", None)
    if callable(asked):
        try:
            return bool(asked(role))
        except Exception:  # noqa: BLE001
            return True
    return True


#: What may be handed to `forge_loader` ALONGSIDE the checkpoint, in the order
#: Forge expects to meet them. The checkpoint itself is the first positional
#: argument and is never in this list.
ADDITIONAL_STATE_DICT_ROLES: tuple[str, ...] = ("text_encoder", "vae")


class LoadConfigurationError(Exception):
    """The live load configuration is absent or unusable.

    Raised *before* any payload access, so a missing authority costs nothing.
    """

    def __init__(self, message: str, *, step: str = "load_configuration") -> None:
        super().__init__(message)
        self.message = message
        self.step = step


class LoadConfiguration:
    """The minimum a real load needs beyond the profile itself.

    Deliberately a plain object the caller must construct and hand in. There is
    no environment fallback and no default path: a Studio that was never given
    an authority cannot load a model, and says so before touching anything.

    ```text
    repository_root   where the Gradio-free option defaults are read from
    authorization     the controlled access authority; carries the role paths,
                      the timeout and the VRAM ceiling
    payload_opener    optional, for a caller that owns its own opener
    progress_sink     optional; the load-phase progress object
    ```

    No authorization prose is stored here. This object holds the *mechanism*
    the owner's decision authorizes, not the decision.
    """

    def __init__(
        self,
        *,
        repository_root: Path | str,
        authorization: object = None,
        payload_opener: object = None,
        progress_sink: object = None,
    ) -> None:
        self.repository_root = Path(repository_root)
        self.authorization = authorization
        self._payload_opener = payload_opener
        self.progress_sink = progress_sink

    def payload_opener(self) -> object | None:
        """The caller's own opener, or `None` to build the controlled default."""

        return self._payload_opener

    @property
    def has_authority(self) -> bool:
        return self.authorization is not None or self._payload_opener is not None

    def to_dict(self) -> dict[str, Any]:
        """Scalars only. Never reports the repository or any role path."""

        return {
            "configuration": type(self).__name__,
            "repository_root_configured": True,
            "authority_configured": self.authorization is not None,
            "payload_opener_injected": self._payload_opener is not None,
            "progress_sink_configured": self.progress_sink is not None,
        }


# --------------------------------------------------------------- 1. globals


class StudioStartupGlobals:
    """Own the Neo globals the retained closure reads, and restore them.

    Composes three existing owners rather than reimplementing any of them:

    ```text
    headless_options              shared.opts, Gradio-free
    ForgeStateBridge              shared.state, the progress authority
    HeadlessCompatibilityContext  prompt_styles, device, total_tqdm
    ```

    Both `opts` and `state` must exist on `modules.shared` **before**
    `modules.processing` imports them by value, which is why this is the first
    step of a load and not a neighbour of the engine build.

    `close()` is idempotent and never raises: restoration runs on unload and on
    every partial failure, and a restore that raised would mask the original
    fault.
    """

    def __init__(
        self,
        *,
        repository_root: Path | str,
        result_root: Path | str | None = None,
        progress: object | None = None,
    ) -> None:
        self._repository_root = Path(repository_root)
        self._result_root = Path(result_root) if result_root is not None else None
        self._progress = progress

        #: The process options object once this loader has installed it. Held
        #: for diagnostics only -- it is not this loader's to dispose of.
        self._options: Any = None
        self._compat: Any = None
        self._previous_state: Any = None
        self._had_state = False
        #: True when this load ADOPTED a bridge a previous load installed,
        #: rather than building one. Restoring then puts the same object back,
        #: which is the point: the samplers hold it by value and must keep
        #: holding the one thing the port re-points per job.
        self._adopted_state = False
        self.bridge: Any = None

        #: Runtime-environment ownership: what was added to sys.path (removed
        #: on close, exactly once each) and the argv object that was replaced.
        self._path_entries_added: list[str] = []
        self._previous_argv: list[str] | None = None
        self._previous_argv_values: tuple[str, ...] = ()

        self.installed = False
        self.closed = False
        self._report: dict[str, Any] = {}

    # -- install -----------------------------------------------------------

    def install(self) -> StudioStartupGlobals:
        """Install every global. On partial failure, unwinds what it installed."""

        if self.installed:
            return self
        try:
            # The runtime environment comes FIRST, before anything imports
            # modules.* -- shared_cmd_options parses sys.argv at import, and
            # the strict-parse branch dies on foreign arguments.
            self._install_runtime_environment()
            self._install_options()
            self._install_state_bridge()
            self._install_compatibility()
        except BaseException:
            # A half-installed `modules.shared` must not outlive this call.
            self.close()
            raise
        self.installed = True
        return self

    def _install_runtime_environment(self) -> None:
        """Own the process-global import preconditions the retained code reads.

        Three of them, in a deliberate order:

        ```text
        sys.argv    modules/shared_cmd_options.py:13 parses it STRICTLY at
                    import, so it is normalized before any modules.* import.
                    The parse happens once, so the window only needs to cover
                    the load; close() puts the original back.
        packages    backend/loader.py reaches huggingface_guess through anima,
                    and that package exists only under modules_forge/packages.
                    This is the line whose absence stopped the second live
                    attempt at the engine-builder import.
        repository  modules and backend resolve from the repository root when
                    the process was launched from somewhere else.
        ```

        Entries are inserted only when absent, recorded, and removed exactly
        once each on close, so a path the environment already had is never
        touched and sys.path order is preserved for everything this object
        did not add. No environment variable is consulted.
        """

        # The ORIGINAL argv object is saved by reference, never copied and
        # never mutated; a dedicated temporary list replaces it for the load
        # window. The final live run restored a value-equal COPY, which is a
        # different object -- close() now puts back the exact object, and the
        # captured tuple proves nothing mutated it in the meantime.
        self._previous_argv = sys.argv
        self._previous_argv_values = tuple(sys.argv)
        sys.argv = [sys.argv[0] if sys.argv else ""]

        root = str(self._repository_root.resolve())
        packages = str(
            (self._repository_root / "modules_forge" / "packages").resolve()
        )
        for entry in (root, packages):
            if entry not in sys.path:
                sys.path.insert(0, entry)
                self._path_entries_added.append(entry)

    def _install_options(self) -> None:
        """Install the PROCESS options object. Once, and not undone.

        This used to open a context manager per load and restore the previous
        `shared.opts` on close. Forty modules bind that name by value at import
        time, so the second load installed an object the first load's imports
        would never see -- the same class of defect
        `_install_state_bridge` documents for `shared.state`, and the reason
        `neo_registries` refuses to stand the engine up at all.

        `process_options` constructs once and updates overrides in place, so a
        second load with a different result root moves the output without
        handing the engine a second options object.
        """

        from .headless_options import output_directory_overrides, process_options

        overrides: dict[str, Any] = {}
        if self._result_root is not None:
            overrides.update(output_directory_overrides(self._result_root))
        # `multiple_tqdm=False` keeps the production TotalTQDM silent, so the
        # owned progress bridge stays the only progress authority.
        overrides.update(self._compat_option_overrides())

        self._options = process_options(
            self._repository_root, overrides=overrides
        )

    @staticmethod
    def _compat_option_overrides() -> dict[str, Any]:
        from .headless_compat import COMPAT_OPTION_OVERRIDES

        return dict(COMPAT_OPTION_OVERRIDES)

    def _install_state_bridge(self) -> None:
        """Own `shared.state`, REUSING the bridge already bound if there is one.

        The samplers bind it by VALUE:

        ```text
        modules/sd_samplers_common.py:29      from modules.shared import state
        modules/sd_samplers_cfg_denoiser.py:6 from modules.shared import state
        ```

        That name is resolved once, at first import, and never re-read. So the
        FIRST bridge installed in a process is the one `store_latent` writes
        every latent into, for the life of that process, no matter what
        `shared.state` is reassigned to afterwards.

        Building a second bridge on the second load therefore left the port
        listening to an object the sampler never touches -- the exact hazard
        `session_loader` warns about at its call site ("a second bridge would
        leave the sampler reporting into an object nobody reads"), which turns
        out to happen ACROSS loads rather than within one.

        Live symptom, found by the owner: Live Preview worked, then died the
        moment a different checkpoint was selected, and stayed dead. The
        socket was open and progress messages flowed; the frames were being
        decoded into the previous session's progress object.

        Reusing it is safe because the bridge holds no session state of its
        own: `live_generation_port` re-points its `_progress` at the current
        job on every generate, which is what makes one long-lived bridge the
        correct shape rather than merely the working one.
        """

        from modules import shared

        from .headless_progress import ForgeStateBridge, HeadlessProgress

        progress = self._progress
        if progress is None:
            progress = HeadlessProgress("studio-load", preview_enabled=False)
        self._had_state = hasattr(shared, "state")
        self._previous_state = getattr(shared, "state", None)

        # What the SAMPLERS hold, not what `shared.state` currently is.
        #
        # `close()` restores the original `shared.state` between loads, so by
        # the time a second load runs, `shared.state` is Neo's own object
        # again -- while the samplers still hold the first bridge, because
        # they bound it by value and never re-read the name. Checking
        # `shared.state` here would therefore miss every time and build the
        # second bridge anyway.
        existing = self._bound_bridge()
        if isinstance(existing, ForgeStateBridge):
            # A previous load's bridge, still the one the samplers hold.
            # Adopt it and re-point it; do NOT replace it.
            object.__setattr__(existing, "_progress", progress)
            self.bridge = existing
            self._adopted_state = True
        else:
            self.bridge = ForgeStateBridge(progress)
            self._adopted_state = False
        shared.state = self.bridge

    @staticmethod
    def _bound_bridge() -> Any:
        """The bridge the retained samplers actually write into, or None.

        `from modules.shared import state` resolves once, at first import.
        These two modules are where the latent and the step counter are
        published, so whichever object THEY hold is the one a preview frame
        can come from -- regardless of what `shared.state` has been
        reassigned to since.
        """

        import sys

        from .headless_progress import ForgeStateBridge

        for name in (
            "modules.sd_samplers_common",
            "modules.sd_samplers_cfg_denoiser",
        ):
            module = sys.modules.get(name)
            if module is None:
                continue
            candidate = getattr(module, "state", None)
            if isinstance(candidate, ForgeStateBridge):
                return candidate
        return None

    def _install_compatibility(self) -> None:
        from .headless_compat import HeadlessCompatibilityContext

        self._compat = HeadlessCompatibilityContext()
        self._compat.install()

    # -- restore -----------------------------------------------------------

    def close(self) -> dict[str, Any]:
        """Restore in reverse order. Idempotent; never raises."""

        if self.closed:
            return dict(self._report)

        report: dict[str, Any] = {
            "compatibility_restored": False,
            "state_restored": False,
            "options_restored": False,
            "sys_path_restored": False,
            "argv_restored": False,
        }

        if self._compat is not None:
            try:
                self._compat.restore()
                report["compatibility_restored"] = True
                to_dict = getattr(self._compat, "to_dict", None)
                if callable(to_dict):
                    report["compatibility"] = to_dict()
            except BaseException:  # noqa: BLE001 - restoration never raises
                pass
            self._compat = None

        try:
            from modules import shared

            if self._had_state:
                shared.state = self._previous_state
                report["state_restored"] = shared.state is self._previous_state
            else:
                report["state_restored"] = True
        except BaseException:  # noqa: BLE001
            pass
        self._previous_state = None
        self.bridge = None

        # Options are NOT restored. They belong to the process, not to this
        # load -- see `_install_options`. `options_restored` stays in the
        # report and reads False, because a caller that checks it should learn
        # that nothing was torn down rather than find the key missing.
        report["options_restored"] = False

        # The runtime environment goes back LAST -- it was installed first.
        try:
            for entry in self._path_entries_added:
                if entry in sys.path:
                    sys.path.remove(entry)
            report["sys_path_restored"] = all(
                entry not in sys.path for entry in self._path_entries_added
            )
        except BaseException:  # noqa: BLE001
            pass
        self._path_entries_added = []

        try:
            if self._previous_argv is not None:
                sys.argv = self._previous_argv
                report["argv_restored"] = (
                    sys.argv is self._previous_argv
                    and tuple(sys.argv) == self._previous_argv_values
                )
            else:
                report["argv_restored"] = True
        except BaseException:  # noqa: BLE001
            pass
        self._previous_argv = None
        self._previous_argv_values = ()

        self.closed = True
        self.installed = False
        self._report = report
        return dict(report)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": type(self).__name__,
            "installed": self.installed,
            "closed": self.closed,
            "has_bridge": self.bridge is not None,
            **({"restoration": dict(self._report)} if self._report else {}),
        }


# --------------------------------------------------------------- 2. payloads


class OpenedPayloads:
    """What the payload step hands the engine builder. Scalars and paths only.

    The paths are the authorized loader paths; they never reach a public
    projection, and `to_dict()` deliberately reports opaque role facts instead.
    """

    def __init__(
        self,
        *,
        loader_paths: dict[str, str],
        opened_roles: tuple[str, ...],
        device: dict[str, Any],
        watch: object | None = None,
    ) -> None:
        self.loader_paths = dict(loader_paths)
        self.opened_roles = tuple(opened_roles)
        self.device = dict(device)
        #: The live `PayloadWatch`. Held so the engine builder can restore the
        #: `safetensors.safe_open` patch in a `finally`; never projected.
        self._watch = watch

    def path(self, role: str) -> str:
        """The opened path for a role, or "" if the load did not carry one.

        Empty rather than a KeyError: a checkpoint that bundles its own text
        encoder never opens one, and asking is how the caller finds that out.
        """

        return self.loader_paths.get(role, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": list(self.opened_roles),
            "role_count": len(self.opened_roles),
            "device": dict(self.device),
            "watch_installed": self._watch is not None,
            "opened_roles_observed": list(getattr(self._watch, "opened", []) or []),
        }


class ControlledPayloadOpener:
    """The real payload boundary: authorization, CUDA, then the watched opens.

    Construction opens nothing and initializes nothing. Only `__call__` crosses
    the boundary, and only an explicit product load calls it.

    The single-attempt rule is enforced where it was proven, not re-invented:
    `PayloadWatch.on_first_open = authorization.consume` spends the
    authorization at the *observed* open event rather than at a hopeful moment
    beforehand.
    """

    def __init__(
        self,
        *,
        authorization: object,
        timeout_seconds: int | None = None,
        vram_ceiling_bytes: int | None = None,
    ) -> None:
        if authorization is None:
            raise LoadConfigurationError(
                "No controlled load authority is configured for this load.",
                step="payloads_opened",
            )
        self._authorization = authorization
        self._timeout_seconds = timeout_seconds
        self._vram_ceiling_bytes = vram_ceiling_bytes
        self.opened = False

    @property
    def authorization(self) -> object:
        return self._authorization

    def __call__(
        self,
        *,
        profile: object,
        references: object = None,
        roles: tuple[str, ...] = ROLE_ORDER,
        timeout_seconds: int | None = None,
        vram_ceiling_bytes: int | None = None,
        **_ignored: Any,
    ) -> OpenedPayloads:
        del profile, references, timeout_seconds, vram_ceiling_bytes

        if self.opened:
            raise LoadConfigurationError(
                "This payload authority has already been spent.",
                step="payloads_opened",
            )

        from .controlled_device import initialize_cuda
        from .load_telemetry import PayloadWatch, StageTimer
        from .model_intake import validate_exact_path

        authorization = self._authorization
        # Only the roles this load actually carries. A component the
        # checkpoint bundles was never authorized, so it is not opened and not
        # validated -- there is no file to validate.
        ordered = tuple(
            role for role in (tuple(roles) or ROLE_ORDER)
            if _authorized(authorization, role)
        )

        # Exact-path validation first: a wrong or missing role fails here,
        # before CUDA is touched and before anything is opened.
        for role in ordered:
            validate_exact_path(authorization, role)

        device = initialize_cuda(authorization)

        loader_paths = {
            role: authorization.loader_path(role)  # type: ignore[attr-defined]
            for role in ordered
        }
        watch = PayloadWatch(
            {path: role for role, path in loader_paths.items()}, StageTimer()
        )
        watch.on_first_open = authorization.consume  # type: ignore[attr-defined]

        self.opened = True
        watch.install()
        device_report = device.to_dict() if hasattr(device, "to_dict") else {}
        return OpenedPayloads(
            loader_paths=loader_paths,
            opened_roles=ordered,
            device=device_report,
            watch=watch,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "opener": type(self).__name__,
            "roles": list(ROLE_ORDER),
            "opened": self.opened,
            "authority_configured": self._authorization is not None,
        }


# ---------------------------------------------------------------- 3. engine


def build_forge_engine(*, profile: object = None, opened: object = None, **_ignored: Any):
    """Build one engine through Forge's own loader entry point.

    `forge_loader` takes all three payloads in a single call, so the fixed role
    order is expressed in the paths handed to it, and the individual opens are
    observed and attributed by the `PayloadWatch` the opener installed.

    The watch is restored here, in a `finally`: it patches
    `safetensors.safe_open` globally, and leaving it installed would make every
    later open in the interpreter route through a dead closure.
    """

    del profile
    if opened is None:
        raise LoadConfigurationError(
            "The engine builder received no opened payloads.", step="engine_built"
        )

    # The watch handle is acquired BEFORE the terminal import, and the import
    # sits INSIDE the try. The second live attempt failed on exactly this
    # ordering: `from backend.loader import forge_loader` raised outside the
    # try, the finally never ran, and `safetensors.safe_open` stayed patched
    # by a watch whose load was already dead.
    watch = getattr(opened, "_watch", None)
    try:
        from backend.loader import forge_loader

        # Built from the roles the owner actually supplied, in role order.
        #
        # This was a literal two-element list, and that is what confined Studio
        # to checkpoints needing an external text encoder and VAE -- an SDXL or
        # SD 1.5 file carries both inside itself and has nothing to put here.
        # Neo was never the constraint: `backend/loader.py:822` declares
        # `additional_state_dicts: list = None` and `:843` merges only when it
        # IS a list, so an empty one skips the loop. Studio's seam is what
        # removed the capability, and this is the seam.
        additional: list[Any] = []
        for role in ADDITIONAL_STATE_DICT_ROLES:
            path = opened.path(role)  # type: ignore[attr-defined]
            if path:
                additional.append(path)
        return forge_loader(
            opened.path("checkpoint"),  # type: ignore[attr-defined]
            additional_state_dicts=additional,
        )
    finally:
        restore = getattr(watch, "restore", None)
        if callable(restore):
            try:
                restore()
            except BaseException:  # noqa: BLE001 - restoration never fails a load
                pass


def publish_engine(engine: object) -> dict[str, Any]:
    """Make `engine` the retained code's current model, in the only safe order.

    `modules/sd_models.py:12` imports `processing`, and `modules/processing.py:32`
    imports back from `sd_models` -- a genuine mutual cycle that resolves only
    when `processing` is imported first. `shared.sd_model = engine` reaches
    `sd_models` through the property setter at `modules/shared_items.py:175`,
    so assigning before importing `processing` is fatal.

    Two authorized live attempts were spent discovering this. It is not
    reordering-safe.
    """

    import modules.processing  # noqa: F401 - imported FIRST, deliberately

    from modules import shared

    previous = getattr(shared, "sd_model", None)
    shared.sd_model = engine
    return {
        "published": getattr(shared, "sd_model", None) is engine,
        "had_previous": previous is not None,
        "previous": previous,
    }


def restore_published_engine(state: dict[str, Any]) -> bool:
    """Undo `publish_engine`. Never raises."""

    try:
        from modules import shared

        shared.sd_model = state.get("previous")
        return True
    except BaseException:  # noqa: BLE001
        return False


__all__ = (
    "ROLE_ORDER",
    "ControlledPayloadOpener",
    "LoadConfiguration",
    "LoadConfigurationError",
    "OpenedPayloads",
    "StudioStartupGlobals",
    "build_forge_engine",
    "publish_engine",
    "restore_published_engine",
)
