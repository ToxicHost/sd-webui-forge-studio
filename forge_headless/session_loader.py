"""The real product session loader: explicit load, behind deferred imports.

Phase 1 gave Studio a lifecycle whose loader was a hole -- every test injected a
synthetic one, and production had none, so `load()` would have failed with
`MODEL_LOAD_FAILED` for want of anything to call. This module fills that hole
with the primitives four live milestones already proved, and nothing else.

The shape that matters is **every step is injectable**. The defaults are the
real, deferred, Forge-touching implementations; a test supplies synthetic ones
and drives the *real* loader rather than a fake of it. That is why the failure
matrix below can be exercised at full fidelity without a payload:

    payload_opener      opens the three authorized role references
    engine_builder      builds the engine from what was opened
    identity_installer  attaches compatibility identity -- BEFORE ready
    startup_globals     installs the retained-code globals and the state bridge
    bookkeeping         installs direct-load reload bookkeeping
    port_factory        builds the generation port
    session_factory     builds the HeadlessGenerationSession
    cleanup             releases whatever was acquired, on any partial failure

Ordering is the contract. Identity is attached before the session is exposed,
because `process_images_inner` reads catalogue identity off the model
unconditionally and a session published without it is a session that fails at
first generation rather than at load.

Importing this module is free: no torch, no `backend`, no `modules`, no gradio.
Every heavy import is inside a function, and none of them runs until an explicit
`load()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
MODEL_LOAD_CANCELLED = "MODEL_LOAD_CANCELLED"
MODEL_UNLOAD_FAILED = "MODEL_UNLOAD_FAILED"
#: Distinct from MODEL_LOAD_FAILED on purpose: nothing was attempted, so
#: nothing failed. A caller that conflates the two retries forever.
MODEL_LOAD_NOT_CONFIGURED = "MODEL_LOAD_NOT_CONFIGURED"
#: No result root is configured, so a loaded model would have nowhere to
#: publish. Raised before any payload is opened.
RESULT_ROOT_NOT_CONFIGURED = "RESULT_ROOT_NOT_CONFIGURED"

#: The three roles a Tier-0 profile carries, in the order they are opened. The
#: order is fixed so a partial failure names the same boundary every time.
ROLE_ORDER = ("checkpoint", "text_encoder", "vae")

#: Every step, in order. Reported in evidence and asserted by tests, so a step
#: cannot be added, removed or reordered without saying so.
LOAD_STEPS = (
    "cancellation_checked",
    "startup_globals_installed",
    "payloads_opened",
    "engine_built",
    "identity_attached",
    "bookkeeping_installed",
    "port_built",
    "session_built",
)


class SessionLoadError(Exception):
    """A load failure carrying a stable code and a scalar message.

    Never a traceback, never a repr, never a path. The originating exception's
    **type name** is the most that travels, which is what every prior milestone
    settled on after a traceback nearly reached a report.
    """

    def __init__(self, code: str, message: str, *, step: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.step = step

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "step": self.step}


@dataclass
class LoadedStudioSession:
    """What a successful load hands back. Scalars and owned objects only.

    `describe()` is the only projection a response may carry: it names the
    profile and the engine class, and carries no path, no filename and no repr
    of a Forge object.
    """

    profile_id: str
    family: str
    session: Any
    engine: Any = None
    port: Any = None
    identity: Mapping[str, Any] = field(default_factory=dict)
    steps: tuple[str, ...] = ()
    _closed: bool = False
    _closers: tuple[Callable[[], None], ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "family": self.family,
            "engine_class": type(self.engine).__name__ if self.engine is not None else "",
            "session_configured": self.session is not None,
            "port_configured": self.port is not None,
            "identity_attached": bool(self.identity),
            "steps_completed": list(self.steps),
            "closed": self._closed,
        }

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> dict[str, Any]:
        """Idempotent. Releases in reverse acquisition order and never raises.

        The terminal allocator clear runs HERE, at the end, and nowhere
        else: this is the point where the last Studio-owned references to
        the session, engine, and port are gone, and it is still inside the
        lifecycle's UNLOADING window. A duplicate close returns early
        above, so the clear happens exactly once per session that was
        actually released.
        """

        if self._closed:
            return {"closed": True, "already": True}
        self._closed = True
        report: dict[str, Any] = {"closed": True, "already": False, "errors": []}
        for closer in reversed(self._closers):
            try:
                closer()
            except BaseException as exc:  # noqa: BLE001 - reported as a scalar
                report["errors"].append(type(exc).__name__)
        self.session = None
        self.engine = None
        self.port = None
        self._closers = ()
        # Deferred import: a process that never loaded must not gain a
        # module here, and the helper itself imports nothing.
        from .terminal_release import release_terminal_cache

        report["terminal_release"] = release_terminal_cache()
        return report


class StudioSessionLoader:
    """The port. A product-owned loader implements these three."""

    def load(self, profile: Any, *, cancellation: Any = None,
             progress: Any = None) -> LoadedStudioSession:
        raise NotImplementedError

    def close(self, session: LoadedStudioSession) -> dict[str, Any]:
        raise NotImplementedError

    def capabilities(self, profile: Any) -> dict[str, Any]:
        raise NotImplementedError


def _cancelled(cancellation: Any) -> bool:
    """A cancellation probe may be a flag, a callable, or an event."""

    if cancellation is None:
        return False
    if callable(cancellation):
        try:
            return bool(cancellation())
        except BaseException:  # noqa: BLE001 - a refusing probe is not a cancel
            return False
    is_set = getattr(cancellation, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return bool(cancellation)


class _PublishedBookkeeping:
    """One restore handle covering both halves of the bookkeeping step.

    The step does two things -- publishes the engine onto `modules.shared` and
    installs the direct-load reload state -- so it must undo both, in reverse,
    as a single unit. Returning only the reload state would leave a released
    engine published as the retained code's current model.
    """

    def __init__(self, state: Any, published: dict, restore_published: Any) -> None:
        self.state = state
        self.published = dict(published)
        self._restore_published = restore_published
        self.restored = False

    def restore(self) -> None:
        if self.restored:
            return
        self.restored = True
        from .unload_events import (
            RELOAD_BOOKKEEPING_RESTORED,
            SENTINEL_RESTORED,
            record,
        )

        # Named for what each half actually puts back. `_restore_state` returns
        # the direct-load reload bookkeeping; `_restore_published` returns
        # `shared.sd_model` to the retained Forge sentinel. The order is the
        # one this class documents above and must not be swapped: republishing
        # the sentinel last is what stops a released engine from being left as
        # the retained code's current model.
        for undo, step in (
            (lambda: self._restore_state(), RELOAD_BOOKKEEPING_RESTORED),
            (lambda: self._restore_published(self.published), SENTINEL_RESTORED),
        ):
            try:
                undo()
            except BaseException:  # noqa: BLE001 - restoration never raises
                pass
            else:
                record(step)
        # The previous engine is dropped only after it has been put back, so a
        # failed restore does not also lose the reference.
        self.published.pop("previous", None)

    def _restore_state(self) -> None:
        for name in ("restore", "close"):
            method = getattr(self.state, name, None)
            if callable(method):
                method()
                return

    def to_dict(self) -> dict[str, Any]:
        state_dict = getattr(self.state, "to_dict", None)
        return {
            "published": bool(self.published.get("published")),
            "restored": self.restored,
            **(state_dict() if callable(state_dict) else {}),
        }


class HeadlessSessionLoader(StudioSessionLoader):
    """Builds one warm `HeadlessGenerationSession` from one profile.

    Nothing here is called by profile selection, by a status read, or by a
    generation request. It runs only from an explicit lifecycle `load()`, which
    is what keeps payload access an act rather than a side effect.
    """

    def __init__(
        self,
        *,
        result_root: Any = None,
        payload_opener: Callable[..., Any] | None = None,
        engine_builder: Callable[..., Any] | None = None,
        identity_installer: Callable[[Any], Any] | None = None,
        startup_globals: Callable[..., Any] | None = None,
        bookkeeping: Callable[[Any], Any] | None = None,
        port_factory: Callable[..., Any] | None = None,
        session_factory: Callable[..., Any] | None = None,
        cleanup: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
        timeout_seconds: int = 600,
        vram_ceiling_bytes: int = 14 * 1024**3,
        load_configuration: Any = None,
    ) -> None:
        #: The live load configuration: the controlled access authority and the
        #: repository root the real defaults need. Absent for every non-live
        #: caller, which is why the defaults check it and refuse *before* any
        #: payload access rather than failing partway through one.
        self._load_configuration = load_configuration
        self._result_root = result_root
        self._payload_opener = payload_opener
        self._engine_builder = engine_builder
        self._identity_installer = identity_installer
        self._startup_globals = startup_globals
        self._bookkeeping = bookkeeping
        self._port_factory = port_factory
        self._session_factory = session_factory
        self._cleanup = cleanup
        self._clock = clock
        self._timeout_seconds = int(timeout_seconds)
        self._vram_ceiling_bytes = int(vram_ceiling_bytes)
        #: A loader is single-attempt per payload access: once a payload has
        #: been opened, a failure is reported and not retried.
        self.payload_opened = False
        self.load_attempts = 0
        #: The state bridge the startup-globals step installed, handed to the
        #: port so a job's progress reaches the retained sampler. Set during a
        #: load and cleared when the load unwinds.
        self._startup_bridge: Any = None

    # -- reporting ---------------------------------------------------------

    def capabilities(self, profile: Any) -> dict[str, Any]:
        """Scalar metadata about what this loader would do. Opens nothing."""

        return {
            "loader": type(self).__name__,
            "profile_id": getattr(profile, "profile_id", ""),
            "family": getattr(profile, "family", ""),
            "roles": list(ROLE_ORDER),
            "steps": list(LOAD_STEPS),
            "timeout_seconds": self._timeout_seconds,
            "vram_ceiling_bytes": self._vram_ceiling_bytes,
            "result_root_configured": self._result_root is not None,
            "single_attempt_after_payload_access": True,
            "payload_opened": self.payload_opened,
            "defaults_bound": True,
            "load_configuration_present": self._load_configuration is not None,
            "can_load": self.can_load,
        }

    @property
    def can_load(self) -> bool:
        """True when a real load could proceed without injected steps.

        Bound defaults are not readiness. A loader whose steps all exist but
        which has no controlled access authority still cannot open anything, and
        reporting it as ready would move the failure from selection time to
        payload time -- the opposite of the point.
        """

        if self._load_configuration is None:
            return all(
                step is not None
                for step in (
                    self._startup_globals,
                    self._payload_opener,
                    self._engine_builder,
                    self._port_factory,
                )
            )
        return bool(getattr(self._load_configuration, "has_authority", False))

    def describe(self) -> dict[str, Any]:
        return {
            "loader": type(self).__name__,
            "payload_opener_injected": self._payload_opener is not None,
            "engine_builder_injected": self._engine_builder is not None,
            "identity_installer_injected": self._identity_installer is not None,
            "startup_globals_injected": self._startup_globals is not None,
            "bookkeeping_injected": self._bookkeeping is not None,
            "port_factory_injected": self._port_factory is not None,
            "session_factory_injected": self._session_factory is not None,
            "cleanup_injected": self._cleanup is not None,
            "load_attempts": self.load_attempts,
            "payload_opened": self.payload_opened,
        }

    # -- the load ----------------------------------------------------------

    def load(self, profile: Any, *, cancellation: Any = None,
             progress: Any = None) -> LoadedStudioSession:
        if self.payload_opened:
            raise SessionLoadError(
                MODEL_LOAD_FAILED,
                "This loader has already opened a payload and will not retry.",
                step="single_attempt",
            )
        self.load_attempts += 1
        acquired: list[Callable[[], None]] = []
        steps: list[str] = []
        engine = None
        opened: dict[str, Any] = {}
        identity: Mapping[str, Any] = {}

        def note(step: str) -> None:
            steps.append(step)
            if progress is not None:
                try:
                    progress(step)
                except BaseException:  # noqa: BLE001 - reporting never fails a load
                    pass

        def check_cancelled(step: str) -> None:
            if _cancelled(cancellation):
                raise SessionLoadError(
                    MODEL_LOAD_CANCELLED,
                    "The model load was cancelled.",
                    step=step,
                )

        try:
            # 0. Preconditions that cost nothing to check and everything to
            #    discover late. A missing result root used to surface at
            #    `port_built` -- step 7 of 8, after every payload was open and
            #    the engine was built -- as an unattributed TypeError from
            #    `Path(None)`. On a single-use authorization that means the
            #    authorization is spent before the failure is known.
            self._require_result_root()

            # 1. Cancellation is checked BEFORE any payload access, so a
            #    cancelled load costs nothing and opens nothing.
            check_cancelled("before_payload_access")
            note("cancellation_checked")

            # 2. Retained-code globals and the state bridge must exist before
            #    `modules.processing` binds them by value.
            globals_handle = self._call(
                self._startup_globals, self._default_startup_globals,
                step="startup_globals_installed",
            )
            if globals_handle is not None:
                acquired.append(lambda h=globals_handle: self._restore(h))
                # The port needs the same bridge the retained code reads, so it
                # is carried here rather than rebuilt: a second bridge would
                # leave the sampler reporting into an object nobody reads.
                self._startup_bridge = getattr(globals_handle, "bridge", None)
                acquired.append(lambda: setattr(self, "_startup_bridge", None))
            note("startup_globals_installed")

            # 3. The payload boundary. Exactly three roles, in a fixed order.
            check_cancelled("payloads")
            references = self._references(profile)
            opener = self._payload_opener or self._default_payload_opener
            self.payload_opened = True
            opened = self._guard(
                lambda: opener(profile=profile, references=references,
                               roles=ROLE_ORDER,
                               timeout_seconds=self._timeout_seconds,
                               vram_ceiling_bytes=self._vram_ceiling_bytes),
                step="payloads_opened",
            )
            # The opener may have patched `safetensors.safe_open` through its
            # watch. Register the restore with THIS unwind, not only with the
            # engine builder's finally: a cancellation or failure between this
            # step and that finally would otherwise leave the patch installed
            # with no owner left to remove it. The restore is idempotent, so
            # both running is harmless.
            watch_restore = getattr(
                getattr(opened, "_watch", None), "restore", None
            )
            if callable(watch_restore):
                acquired.append(lambda r=watch_restore: r())
            note("payloads_opened")

            # 4. The engine.
            check_cancelled("engine")
            builder = self._engine_builder or self._default_engine_builder
            engine = self._guard(
                lambda: builder(profile=profile, opened=opened), step="engine_built"
            )
            if engine is None:
                raise SessionLoadError(
                    MODEL_LOAD_FAILED, "The engine builder returned nothing.",
                    step="engine_built",
                )
            acquired.append(lambda: self._release_engine(engine))
            note("engine_built")

            # 5. Identity, BEFORE the session is exposed. A session published
            #    without it fails at first generation instead of at load.
            installer = self._identity_installer or self._default_identity_installer
            identity = self._guard(
                lambda: installer(engine), step="identity_attached"
            ) or {}
            note("identity_attached")

            # 6. Direct-load reload bookkeeping, so the inner reload is a
            #    truthful no-op rather than a catalogue lookup.
            book = self._bookkeeping or self._default_bookkeeping
            bookkeeping_handle = self._guard(
                lambda: book(engine), step="bookkeeping_installed"
            )
            if bookkeeping_handle is not None:
                acquired.append(lambda h=bookkeeping_handle: self._restore(h))
            note("bookkeeping_installed")

            # 7. The generation port.
            check_cancelled("port")
            make_port = self._port_factory or self._default_port_factory
            port = self._guard(
                lambda: make_port(profile=profile, engine=engine,
                                  result_root=self._result_root),
                step="port_built",
            )
            note("port_built")

            # 8. The session the application will actually use.
            make_session = self._session_factory or self._default_session_factory
            session = self._guard(
                lambda: make_session(profile=profile, port=port,
                                     result_root=self._result_root),
                step="session_built",
            )
            if session is None:
                raise SessionLoadError(
                    MODEL_LOAD_FAILED, "The session factory returned nothing.",
                    step="session_built",
                )
            acquired.append(lambda: self._close_session(session, port))
            note("session_built")
        except SessionLoadError:
            self._release_acquired(acquired)
            self._terminal_release_after_rollback(steps)
            raise
        except BaseException as exc:  # noqa: BLE001 - never leaks a traceback
            self._release_acquired(acquired)
            self._terminal_release_after_rollback(steps)
            raise SessionLoadError(
                MODEL_LOAD_FAILED,
                f"The model failed to load ({type(exc).__name__}).",
            ) from None

        return LoadedStudioSession(
            profile_id=str(getattr(profile, "profile_id", "")),
            family=str(getattr(profile, "family", "")),
            session=session, engine=engine, port=port,
            identity=dict(identity) if isinstance(identity, Mapping) else {},
            steps=tuple(steps), _closers=tuple(acquired),
        )

    def close(self, session: LoadedStudioSession) -> dict[str, Any]:
        if session is None:
            return {"closed": True, "already": True}
        return session.close()

    # -- internals ---------------------------------------------------------

    def _references(self, profile: Any) -> dict[str, str]:
        """The private role references to open. Never logged, never reported.

        The parameter is named `profile` for history. What this actually
        requires is narrower and is now stated: an object exposing
        `reference_for(role)` for each role in `ROLE_ORDER`. A
        `forge_studio.model_selection.ResolvedSelection` satisfies it, and is
        what the product supplies; a `ModelProfile` also satisfies it while the
        transitional path still exists.

        The loader has never needed a profile. It needed a checkpoint, any
        components that checkpoint does not carry itself, and an identity
        string -- and taking `Any` let that go unsaid.
        """

        reference_for = getattr(profile, "reference_for", None)
        if not callable(reference_for):
            raise SessionLoadError(
                MODEL_LOAD_FAILED,
                "That model selection carries no payload references.",
                step="payloads_opened",
            )
        references: dict[str, str] = {}
        for role in ROLE_ORDER:
            try:
                value = reference_for(role)
            except (KeyError, LookupError):
                # A bundled component has no reference. Absent, not empty:
                # what is not here is not opened and not authorized.
                continue
            if value:
                references[role] = value
        return references

    def _call(self, injected, default, *, step: str):
        return self._guard(lambda: (injected or default)(), step=step)

    def _guard(self, work: Callable[[], Any], *, step: str):
        try:
            return work()
        except SessionLoadError:
            raise
        except BaseException as exc:  # noqa: BLE001
            raise SessionLoadError(
                MODEL_LOAD_FAILED,
                f"The model failed to load ({type(exc).__name__}).",
                step=step,
            ) from None

    def _release_acquired(self, acquired: list[Callable[[], None]]) -> None:
        """Reverse-order release of everything this attempt acquired."""

        for release in reversed(acquired):
            try:
                release()
            except BaseException:  # noqa: BLE001 - cleanup never raises
                pass
        acquired.clear()

    def _terminal_release_after_rollback(
        self, steps: list[str] | tuple[str, ...]
    ) -> dict[str, Any] | None:
        """One terminal clear on a failed load that had reached the engine.

        A load that failed BEFORE the engine step allocated nothing to
        clear, and its report says so by absence. Once the engine existed,
        the rollback above has just released the last reference to it, so
        this is the same ordering the successful path uses.
        """

        if "engine_built" not in tuple(steps):
            self.terminal_release_report = None
            return None
        from .terminal_release import release_terminal_cache

        self.terminal_release_report = release_terminal_cache()
        return self.terminal_release_report

    @staticmethod
    def _restore(handle: Any) -> None:
        for name in ("restore", "close", "__exit__"):
            method = getattr(handle, name, None)
            if callable(method):
                method() if name != "__exit__" else method(None, None, None)
                return

    def _release_engine(self, engine: Any) -> None:
        released = self._cleanup
        if callable(released):
            released(engine=engine)
            return
        from .failure_cleanup import release_generation_references

        release_generation_references(processing=None, processed=None)

    @staticmethod
    def _close_session(session: Any, port: Any) -> None:
        release = getattr(port, "release_engine", None)
        if callable(release):
            release()
        close = getattr(session, "close", None)
        if callable(close):
            close()

    # -- real defaults, all deferred --------------------------------------

    def _require_result_root(self) -> Any:
        """Refuse a load that has nowhere to publish. Opens nothing.

        Deliberately a refusal rather than a resolved default. Inventing a root
        here would mean choosing a directory the owner never named -- and the
        alternatives are all worse: system temp is outside the product, an
        environment variable is a hidden fallback, and creating one under the
        repository writes where a load was not asked to write.
        """

        if self._port_factory is not None:
            # An injected port owns its own publication. The requirement exists
            # because the DEFAULT port calls `Path(result_root)`; imposing it on
            # a caller that supplied its own port would refuse loads that were
            # never going to touch a result root.
            return None

        configuration = self._load_configuration
        if configuration is None or not getattr(configuration, "has_authority", False):
            # No controlled access at all is the more fundamental thing to be
            # told, and the step that reports it still runs before any payload
            # opens. Reporting a missing result root first would answer a
            # narrower question than the caller has.
            return None

        root = self._result_root
        if root is None:
            raise SessionLoadError(
                RESULT_ROOT_NOT_CONFIGURED,
                "No result root is configured, so a loaded model would have "
                "nowhere to publish. Configure one before loading.",
                step="result_root_configured",
            )
        try:
            resolved = Path(root)
        except TypeError:
            raise SessionLoadError(
                RESULT_ROOT_NOT_CONFIGURED,
                "The configured result root is not a usable location.",
                step="result_root_configured",
            ) from None
        if not str(resolved).strip():
            raise SessionLoadError(
                RESULT_ROOT_NOT_CONFIGURED,
                "The configured result root is empty.",
                step="result_root_configured",
            )
        return resolved

    def _configuration(self, step: str) -> Any:
        """The live load configuration, or a refusal naming what is missing.

        Checked by every real default. A load with no configured authority fails
        here -- before CUDA, before any open -- rather than partway through one.
        """

        configuration = self._load_configuration
        if configuration is None or not getattr(
            configuration, "has_authority", False
        ):
            # Deliberately NOT MODEL_LOAD_FAILED. "You never configured
            # controlled access" and "the load was attempted and failed" are
            # different facts, and a caller that cannot tell them apart will
            # retry the one that can never succeed.
            raise SessionLoadError(
                MODEL_LOAD_NOT_CONFIGURED,
                "No controlled access is configured, so no model can be "
                "loaded. Configure explicit load access first.",
                step=step,
            )
        return configuration

    def _default_startup_globals(self):
        configuration = self._configuration("startup_globals_installed")
        from .live_bindings import StudioStartupGlobals

        return StudioStartupGlobals(
            repository_root=configuration.repository_root,
            result_root=self._result_root,
            progress=getattr(configuration, "progress_sink", None),
        ).install()

    def _default_payload_opener(self, **kwargs):
        configuration = self._configuration("payloads_opened")
        from .live_bindings import ControlledPayloadOpener

        opener = configuration.payload_opener()
        if opener is None:
            opener = ControlledPayloadOpener(
                authorization=configuration.authorization,
                timeout_seconds=self._timeout_seconds,
                vram_ceiling_bytes=self._vram_ceiling_bytes,
            )
        return opener(**kwargs)

    def _default_engine_builder(self, **kwargs):
        self._configuration("engine_built")
        from .live_bindings import build_forge_engine

        return build_forge_engine(**kwargs)

    @staticmethod
    def _default_identity_installer(engine: Any):
        from .model_identity import attach_model_identity

        return attach_model_identity(engine).to_dict()

    @staticmethod
    def _default_bookkeeping(engine: Any):
        """Publish the engine, then make the retained reload a truthful no-op.

        Publication lives here rather than in the engine builder because the
        proven order is engine -> identity -> publish -> bookkeeping: identity
        must be attached before the engine is published, and `process_images_inner`
        calls `forge_model_reload()` itself, so the bookkeeping it checks must
        exist before the first job rather than be discovered mid-run.
        """

        from .direct_load_reload import DirectLoadReloadBookkeeping
        from .live_bindings import publish_engine, restore_published_engine
        from .model_identity import DEFAULT_RUNTIME_LABEL

        published = publish_engine(engine)

        state = DirectLoadReloadBookkeeping()
        state.install(engine, runtime_label=DEFAULT_RUNTIME_LABEL)
        # Prove the no-op now rather than discovering it mid-run. The predicate
        # is checked first, so this cannot fall through into a real load.
        state.verify_fast_path(engine)

        return _PublishedBookkeeping(state, published, restore_published_engine)

    def _default_port_factory(self, *, profile: Any, engine: Any, result_root: Any):
        self._configuration("port_built")
        from .live_generation_port import StudioLiveGenerationPort

        del profile
        return StudioLiveGenerationPort(
            engine=engine,
            bridge=self._startup_bridge,
            result_root=result_root,
        )

    @staticmethod
    def _default_session_factory(*, profile: Any, port: Any, result_root: Any):
        from .generation_port import GenerationGateway
        from .generation_request import Operation, ResidentModel
        from .studio_generation import HeadlessGenerationSession

        return HeadlessGenerationSession(
            gateway=GenerationGateway(port),
            resident_model=ResidentModel(
                model_id=str(getattr(profile, "profile_id", "")),
                family=str(getattr(profile, "family", "")),
                resident=True,
                # STATED, not inherited. This is the only place a real session
                # is built, and it previously said nothing -- so the dataclass
                # default decided what the whole product could do, and it said
                # txt2img. Every live inpaint job was refused as unsupported.
                # A capability this load-bearing is declared where the session
                # is made, so changing it is a visible edit rather than a
                # default drifting out from under the caller.
                supported_operations=tuple(
                    member.value for member in Operation),
            ),
            result_root=result_root,
            generation_authorized=True,
        )


__all__ = (
    "LOAD_STEPS",
    "MODEL_LOAD_CANCELLED",
    "MODEL_LOAD_FAILED",
    "MODEL_LOAD_NOT_CONFIGURED",
    "RESULT_ROOT_NOT_CONFIGURED",
    "MODEL_UNLOAD_FAILED",
    "ROLE_ORDER",
    "HeadlessSessionLoader",
    "LoadedStudioSession",
    "SessionLoadError",
    "StudioSessionLoader",
)
