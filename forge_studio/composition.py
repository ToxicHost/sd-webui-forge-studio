"""Product-owned composition root and dual-mode host boundary.

Studio already owns its application service (`StudioApplication`), its backend
port (`BackendAdapter`), its result delivery (`ResultRegistry`), and a
stdlib-only transport (`presentation`). What it did not own was the *seam that
assembles them*: `presentation._create_mock_presentation` hardcoded a mock
backend and an evidence directory, and `backend_selection` returns a backend
*name* but has no factory. Every caller therefore built its own arrangement.

This module is that seam, and nothing more. It adds no business logic: request
validation, job identity, progress semantics, cancellation, result minting and
shutdown all stay in `StudioApplication`, so the two hosts below cannot drift
apart -- there is only one implementation for them to share.

Two hosts, one application:

    StandaloneHost   Studio runs as its own product. Nothing here knows what
                     Neo is.
    ExtensionHost    Studio runs inside a Neo-hosted process. Host facilities
                     arrive by **injection**, never by import.

The injection is not stylistic. `tests/studio_alpha/test_import_boundaries.py`
asserts by AST that no file under `forge_studio/` imports `gradio`, `modules`,
`modules_forge`, or `webui` -- at any nesting level. An extension adapter that
imported Neo would have to weaken that test. Taking the host's facilities as an
injected object keeps the boundary intact and keeps the extension bootstrap,
which may import whatever it likes, outside this package.

Importing this module constructs nothing, reads no environment, touches no
filesystem, and starts no thread. Construction happens only in `build()`.
"""

from __future__ import annotations

import itertools
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping, Protocol, runtime_checkable

from .application import StudioApplication
from .backend import BackendAdapter
from .contracts import StructuredError, StudioError


#: Host names. Reported verbatim in readiness, so a caller can tell which
#: arrangement produced a response without inspecting types.
STANDALONE = "standalone"
EXTENSION = "extension"

HOST_NAMES = (STANDALONE, EXTENSION)

#: Backend kinds. Explicit, observable, and closed: an unrecognised value fails
#: rather than falling back, because a silent fallback to the mock is exactly
#: how `STUDIO_BACKEND=forge-headless` came to look honoured while the mock ran.
MOCK_BACKEND = "mock"
HEADLESS_BACKEND = "headless"

BACKEND_KINDS = (MOCK_BACKEND, HEADLESS_BACKEND)


# --------------------------------------------------------------- injection


@runtime_checkable
class StudioExecutor(Protocol):
    """Where owned background work runs.

    Deliberately one method. Studio's job lifecycle is driven by the backend,
    so the composition root does not need a thread pool -- it needs a seam a
    real adapter can hand its work to and a test can make deterministic.
    """

    def submit(self, work: Callable[[], None]) -> None: ...


class InlineExecutor:
    """Run submitted work immediately on the calling thread.

    The default, and the only one this milestone uses. Deterministic by
    construction: there is no scheduling to be raced. `submitted` is a count,
    not a queue, so holding an executor cannot retain work objects.
    """

    def __init__(self) -> None:
        self.submitted = 0
        self._lock = Lock()

    def submit(self, work: Callable[[], None]) -> None:
        with self._lock:
            self.submitted += 1
        work()


def _default_identifiers() -> Callable[[], str]:
    """Monotonic, process-local, and stable across a composition's life."""

    counter = itertools.count(1)
    return lambda: f"studio-{next(counter):08d}"


@dataclass(frozen=True)
class StudioRuntimeServices:
    """Everything the composition root injects, in one value.

    Frozen so a host cannot rewrite the services it was handed, and so the two
    hosts provably receive the same shape.

    `clock_injected` is not cosmetic. `MockBackend.__init__` reads
    `clock is None` into `_wait_for_progress` (mock_backend.py:73): with no
    clock it *sleeps* to the next transition, which is what paces the loopback
    UI, and with any clock it becomes fully synchronous. Handing it
    `time.monotonic` as a "default" would therefore silently change the served
    product's progress behaviour. So the flag records whether a caller really
    chose a clock, and only then is one passed down.
    """

    clock: Callable[[], float]
    identifiers: Callable[[], str]
    executor: StudioExecutor
    result_root: Path | None
    environ: Mapping[str, str]
    clock_injected: bool = False

    def backend_clock(self) -> Callable[[], float] | None:
        """The clock to hand a backend: `None` unless one was really chosen."""

        return self.clock if self.clock_injected else None

    def describe(self) -> dict[str, object]:
        """Scalar description. Never a path, a callable, or an object repr."""

        return {
            "clock_injected": self.clock_injected,
            "identifiers_injected": True,
            "executor_kind": type(self.executor).__name__,
            "result_root_configured": self.result_root is not None,
            "environ_injected": self.environ is not os.environ,
        }


def _implements_backend_port(candidate: object) -> bool:
    """Whether an object satisfies the backend port, by surface not identity.

    `isinstance(candidate, BackendAdapter)` is the obvious check and it is
    wrong here. `tests/studio_alpha/test_import_boundaries.py:47` deliberately
    purges `forge_studio` and every submodule from `sys.modules` to prove a
    fresh import stays clean. A later deferred `from .mock_backend import
    MockBackend` then re-imports the package, producing a *second*
    `BackendAdapter` class object -- and a backend built from it fails an
    identity check against the one this module bound at its own import.

    The port is defined by its method surface, so that is what is checked. The
    names come from the ABC itself rather than a literal list, so adding an
    abstract method cannot leave this behind.
    """

    required = set(getattr(BackendAdapter, "__abstractmethods__", ()))
    if not required:  # pragma: no cover - the ABC always declares some
        required = {"submit_generation", "poll_or_stream_progress", "shutdown"}
    return all(callable(getattr(candidate, name, None)) for name in required)


# ----------------------------------------------------------- job execution


#: The three job states no further observation can change.
TERMINAL_STATES = frozenset({"completed", "cancelled", "failed"})

#: Returned when the deadline expired before any terminal state was observed.
TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class TerminalWaitPolicy:
    """How long to wait for a submitted job, and how often to look.

    The values are the ones `SourceFrontendAdapter.generate` has always used;
    they are named here so both hosts share one policy instead of each carrying
    a copy of the numbers.
    """

    timeout_seconds: float = 120.0
    poll_interval_seconds: float = 0.02


DEFAULT_TERMINAL_WAIT = TerminalWaitPolicy()


def run_to_terminal(
    *,
    poll: Callable[[], object],
    observe: Callable[[object], str],
    cancel: Callable[[], object],
    policy: TerminalWaitPolicy = DEFAULT_TERMINAL_WAIT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, object | None]:
    """Drive one submitted job to a terminal state, or cancel it on timeout.

    This is the only piece of job-lifecycle policy that was living inside the
    canonical-frontend adapter rather than in a shared place: a single-flight
    wait loop with a deadline, which on expiry cancels rather than abandoning.
    A second host that did not reuse that adapter wholesale would otherwise
    have had to reimplement it, which is exactly the fork this milestone must
    not create -- so it lives here, and the adapter calls it.

    `observe` receives each polled event and returns its state string, which
    lets a caller record progress without this function knowing anything about
    how progress is shaped. Returns `(state, last_event)`; on timeout the state
    is `TIMED_OUT` and the event is `None`.
    """

    deadline = clock() + policy.timeout_seconds
    while clock() < deadline:
        event = poll()
        state = observe(event)
        if state in TERMINAL_STATES:
            return state, event
        sleep(policy.poll_interval_seconds)
    cancel()
    return TIMED_OUT, None


# ------------------------------------------------------------------- hosts


class StudioHost(ABC):
    """One arrangement of the same Studio application.

    A host answers two questions and nothing else: which backend to construct,
    and what to say about itself. It must not implement request, job, progress,
    cancellation, result, or shutdown behaviour -- those live in
    `StudioApplication`, once.
    """

    #: Set by subclasses. One of `HOST_NAMES`.
    name: str = ""

    @abstractmethod
    def create_backend(self, services: StudioRuntimeServices) -> BackendAdapter:
        """Construct the backend for this host. Called exactly once."""

    def describe(self) -> dict[str, object]:
        """Host-specific metadata. Documented as permitted to differ."""

        return {"host": self.name}

    def close(self) -> None:
        """Release host-owned resources. Idempotent. Default: nothing owned."""


class StandaloneHost(StudioHost):
    """Studio as its own product: no Neo, no Gradio, no host process.

    The backend factory is injected rather than selected here, because
    selecting `forge-headless` would otherwise mean importing the live loader
    at composition time. `backend_selection.select_backend_name` still decides
    the *name*; wiring a real adapter to that name is a later milestone, and
    until then an unwired name fails with a structured error instead of
    silently falling back to the mock.
    """

    name = STANDALONE

    def __init__(
        self,
        *,
        backend_factory: Callable[[StudioRuntimeServices], BackendAdapter] | None = None,
        backend_kind: str = MOCK_BACKEND,
        backend_name: str | None = None,
        headless_runtime: object | None = None,
        headless_generation: object | None = None,
    ) -> None:
        # `backend_name` is the Phase 1 spelling. Accepted so existing callers
        # keep working, and folded into the one selector rather than kept as a
        # second source of truth.
        chosen = backend_kind if backend_name is None else backend_name
        self._backend_kind = str(chosen or MOCK_BACKEND)
        self._backend_factory = backend_factory
        self._headless_runtime = headless_runtime
        self._headless_generation = headless_generation

    @property
    def backend_kind(self) -> str:
        return self._backend_kind

    def create_backend(self, services: StudioRuntimeServices) -> BackendAdapter:
        if self._backend_factory is not None:
            return self._backend_factory(services)
        if self._backend_kind == MOCK_BACKEND:
            # Deferred, so selecting the mock imports no headless module.
            from .mock_backend import MockBackend

            return MockBackend(
                result_directory=services.result_root,
                clock=services.backend_clock(),
            )
        if self._backend_kind == HEADLESS_BACKEND:
            # Deferred for the same reason in the other direction: the headless
            # adapter is only imported when it is actually selected. It imports
            # no Torch, Forge, Neo, or Gradio module, and constructing it opens
            # no model and initializes no device.
            from forge_headless.studio_adapter import HeadlessBackendAdapter

            return HeadlessBackendAdapter(
                self._headless_runtime,
                generation=self._headless_generation,
            )
        raise StudioError(
            StructuredError(
                code="BACKEND_NOT_WIRED",
                message="That Studio backend is not available in this build.",
                field="backend",
            )
        )

    def describe(self) -> dict[str, object]:
        return {
            "host": self.name,
            "backend_kind": self._backend_kind,
            "backend_name": self._backend_kind,
            "backend_factory_injected": self._backend_factory is not None,
            "headless_generation_injected": self._headless_generation is not None,
            "owns_process": True,
            "neo_host_facilities": False,
        }


class ExtensionHost(StudioHost):
    """Studio inside a Neo-hosted process, with host facilities injected.

    `host_services` is an opaque object supplied by the extension bootstrap --
    which lives outside this package and may import whatever it needs. Only a
    declared, duck-typed surface is read, and every member is optional:

        create_backend(services) -> BackendAdapter
        result_root()            -> Path | None
        describe()               -> Mapping[str, object]   (scalars only)

    Nothing about Neo is imported, named, or assumed here. An extension host
    with no facilities behaves exactly like the standalone host, which is what
    makes host equivalence testable rather than asserted.
    """

    name = EXTENSION

    def __init__(
        self,
        host_services: object | None = None,
        *,
        backend_factory: Callable[[StudioRuntimeServices], BackendAdapter] | None = None,
    ) -> None:
        self._host_services = host_services
        self._backend_factory = backend_factory

    def create_backend(self, services: StudioRuntimeServices) -> BackendAdapter:
        if self._backend_factory is not None:
            return self._backend_factory(services)
        factory = getattr(self._host_services, "create_backend", None)
        if callable(factory):
            return factory(services)
        from .mock_backend import MockBackend

        return MockBackend(
            result_directory=services.result_root,
            clock=services.backend_clock(),
        )

    def host_result_root(self) -> Path | None:
        """A result root the host prefers, or None. Never invented."""

        getter = getattr(self._host_services, "result_root", None)
        if not callable(getter):
            return None
        root = getter()
        return None if root is None else Path(root)

    def describe(self) -> dict[str, object]:
        described: dict[str, object] = {
            "host": self.name,
            "host_services_injected": self._host_services is not None,
            "backend_factory_injected": self._backend_factory is not None,
            "owns_process": False,
            "neo_host_facilities": self._host_services is not None,
        }
        describer = getattr(self._host_services, "describe", None)
        if callable(describer):
            reported = describer()
            if isinstance(reported, Mapping):
                # Scalars only, and never allowed to overwrite the fields above:
                # a host must not be able to misreport which host it is.
                for key, value in reported.items():
                    if key in described:
                        continue
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        described[str(key)] = value
        return described

    def close(self) -> None:
        closer = getattr(self._host_services, "close", None)
        if callable(closer):
            closer()


# -------------------------------------------------------- composition root


def _looks_like_generation_session(candidate: object) -> bool:
    """True when `candidate` is what the backend adapter actually consumes.

    Checked by surface, never by class: `test_import_boundaries` purges
    `forge_studio` from `sys.modules`, so an identity test would be unreliable
    under the canonical runner. `resident_model` is the field the adapter
    reaches for first, and reaching it on the wrong object is precisely the
    defect this guards.
    """

    if candidate is None:
        return False
    return all(
        hasattr(candidate, name) for name in ("resident_model", "submit", "close")
    )


def _adapter_session(loaded: object) -> object:
    """The session the adapter consumes, unwrapped from what the loader owns.

    The lifecycle owns a `LoadedStudioSession` because unload must release the
    engine, the port, the startup globals and the bookkeeping. The adapter owns
    none of that and must never see the wrapper -- it calls `resident_model`,
    which the wrapper does not have, so publishing the wrapper fails on the
    first generation, after a real load has already opened every payload.

    Injected loaders that return the inner session directly are unchanged: the
    unwrap is by surface and falls through when there is nothing to unwrap.
    """

    if loaded is None:
        return None
    inner = getattr(loaded, "session", None)
    if _looks_like_generation_session(inner):
        return inner
    return loaded


def _real_session_loader(
    *,
    result_root: object,
    load_configuration: object | None,
    closer: Callable[[object], None] | None,
):
    """Build the real loader and adapt it to the manager's callable seam.

    Returns `(loader_callable, closer)`. The callable carries `can_load` from
    the loader it wraps, so the lifecycle can report that controlled access is
    still required *without* attempting a load to find out.

    Deferred: importing this module must not import the headless loader, and
    building the loader must not import Forge.
    """

    from forge_headless.session_loader import HeadlessSessionLoader

    def build(profile: object) -> object:
        # One loader instance per load, which is what makes the single-attempt
        # rule a per-load fact rather than a per-process one.
        return HeadlessSessionLoader(
            result_root=result_root, load_configuration=load_configuration
        ).load(profile)

    # A probe instance, never used to load. It answers "could a load proceed"
    # from exactly the same inputs a real one would get.
    probe = HeadlessSessionLoader(
        result_root=result_root, load_configuration=load_configuration
    )
    build.can_load = probe.can_load  # type: ignore[attr-defined]

    def close(session: object) -> object | None:
        """Close the session and **return its report**.

        The return value is the terminal-release evidence. `LoadedStudioSession.
        close()` builds a report carrying `terminal_release`, and
        `WarmSessionManager._close_session` increments
        `counters["terminal_cache_clears"]` only when that mapping comes back
        with `called` true.

        This wrapper used to be annotated `-> None` and discarded it, so a live
        unload performed the terminal clear and then reported
        `terminal_cache_clears = 0`. Memory returned to baseline while the
        counter said the clear never happened -- telemetry blind to the seam it
        exists to measure, which is exactly the ambiguity that blocked
        acceptance.
        """
        close_method = getattr(session, "close", None)
        if callable(close_method):
            return close_method()
        return None

    return build, (closer if closer is not None else close)


@dataclass(frozen=True)
class StudioReadiness:
    """What the application can say about itself without loading a model."""

    host: str
    session_id: str
    ready: bool
    backend_id: str
    backend_state: str
    backend_is_mock: bool
    model_loaded: bool
    result_delivery_configured: bool
    model_count: int
    host_metadata: dict[str, object] = field(default_factory=dict)
    services: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "session_id": self.session_id,
            "ready": self.ready,
            "backend_id": self.backend_id,
            "backend_state": self.backend_state,
            "backend_is_mock": self.backend_is_mock,
            "model_loaded": self.model_loaded,
            "result_delivery_configured": self.result_delivery_configured,
            "model_count": self.model_count,
            "host_metadata": dict(self.host_metadata),
            "services": dict(self.services),
        }


class StudioComposition:
    """The one place Studio's services are assembled.

    Construct with `build()`. The constructor is private in spirit: it takes
    already-built collaborators so tests can substitute any of them.
    """

    def __init__(
        self,
        *,
        host: StudioHost,
        application: StudioApplication,
        services: StudioRuntimeServices,
        session_id: str,
        profiles: object | None = None,
        settings: object | None = None,
        model_lifecycle: object | None = None,
    ) -> None:
        self._host = host
        self._application = application
        self._services = services
        self._session_id = session_id
        # Lifecycle services are assembled here rather than by the transport or
        # a diagnostic: the composition root is the only place that may decide
        # which profiles exist and who owns the warm session.
        self._profiles = profiles
        self._settings = settings
        self._model_lifecycle = model_lifecycle
        self._closed = False
        self._lock = Lock()

    # -- construction ------------------------------------------------------

    @classmethod
    def build(
        cls,
        host: StudioHost,
        *,
        clock: Callable[[], float] | None = None,
        identifiers: Callable[[], str] | None = None,
        executor: StudioExecutor | None = None,
        result_root: Path | None = None,
        environ: Mapping[str, str] | None = None,
        profiles: object | None = None,
        settings_path: Path | None = None,
        session_loader: Callable[[object], object] | None = None,
        session_closer: Callable[[object], None] | None = None,
        load_configuration: object | None = None,
    ) -> "StudioComposition":
        """Assemble one Studio application for one host.

        Every collaborator is injectable and every default is explicit. No
        environment is read unless one is passed; no directory is created.

        The lifecycle services are assembled here too, and assembling them costs
        nothing: no profile is loaded, no settings file is required, and the
        warm-session manager holds no session until someone calls `load()`.
        """

        if host.name not in HOST_NAMES:
            raise StudioError(
                StructuredError(
                    code="UNKNOWN_STUDIO_HOST",
                    message="That Studio host is not recognised.",
                    field="host",
                )
            )

        chosen_root = result_root
        if chosen_root is None:
            preferred = getattr(host, "host_result_root", None)
            if callable(preferred):
                chosen_root = preferred()

        root = Path(chosen_root) if chosen_root is not None else None
        if root is not None:
            # The registry resolves its case policy at construction by writing
            # one probe file inside the root (`result_delivery.detect_case_policy`),
            # and an absent root makes that INCONCLUSIVE, which fails closed --
            # every later `register` would raise RESULT_OUTSIDE_ROOT. Creating
            # the configured root is assembly, which is this seam's job; the
            # previous inline arrangements only worked because the directory
            # happened to survive from an earlier run.
            root.mkdir(parents=True, exist_ok=True)

        services = StudioRuntimeServices(
            clock=clock or time.monotonic,
            identifiers=identifiers or _default_identifiers(),
            executor=executor or InlineExecutor(),
            result_root=root,
            environ=os.environ if environ is None else environ,
            clock_injected=clock is not None,
        )

        backend = host.create_backend(services)
        if not _implements_backend_port(backend):
            raise StudioError(
                StructuredError(
                    code="INVALID_STUDIO_BACKEND",
                    message="The host produced an unusable Studio backend.",
                    field="backend",
                )
            )

        # The lifecycle is assembled before the application so the application
        # can be handed its gate at construction, and so the manager can publish
        # its session straight into the backend adapter. Without that, the
        # adapter would keep whatever session it was built with and the two
        # would drift -- which is the failure this milestone exists to prevent.
        from .model_lifecycle import WarmSessionManager
        from .model_profiles import (
            ModelProfileRepository,
            looks_like_profile_repository,
        )
        from .model_service import ModelLifecycleService
        from .settings import SettingsService

        repository = (
            profiles
            if looks_like_profile_repository(profiles)
            else ModelProfileRepository(profiles or ())
        )
        settings_service = SettingsService(settings_path)
        settings_service.load()

        # A real session loader is built only for a host that would otherwise
        # have no session at all. Three things disqualify a host, and each is
        # a case where something else already owns the session:
        #
        #   an injected session_loader   the caller owns the loading
        #   the mock backend             it owns its own session
        #   an injected generation seam  the adapter was handed a session
        #
        # Building the loader is free: it opens nothing until an explicit load
        # calls it.
        if (
            session_loader is None
            and str(getattr(host, "backend_kind", MOCK_BACKEND)) != MOCK_BACKEND
            and not host.describe().get("headless_generation_injected")
        ):
            session_loader, session_closer = _real_session_loader(
                result_root=services.result_root,
                load_configuration=load_configuration,
                closer=session_closer,
            )

        def publish_session(session: object) -> None:
            """Point the backend adapter at the lifecycle-owned session.

            Set to `None` on unload, switch failure and shutdown, so a backend
            can never keep serving a session the product has released.

            Scoped to the same condition as the generation gate: the product
            publishes only what it owns. With no loader the adapter owns the
            session it was handed, and publishing `None` at shutdown would
            steal it before the adapter could close it.
            """

            if session_loader is None:
                return
            if hasattr(backend, "_generation"):
                backend._generation = _adapter_session(session)  # noqa: SLF001

        manager = WarmSessionManager(
            profiles=repository,
            loader=session_loader,
            closer=session_closer,
            clock=services.clock,
            on_session_changed=publish_session,
        )
        lifecycle_service = ModelLifecycleService(
            profiles=repository, manager=manager, settings=settings_service
        )

        application = StudioApplication(
            backend,
            result_root=services.result_root,
            model_lifecycle=lifecycle_service,
            selected_backend=str(host.describe().get("backend_name", host.name)),
        )
        return cls(
            host=host,
            application=application,
            services=services,
            session_id=services.identifiers(),
            profiles=repository,
            settings=settings_service,
            model_lifecycle=lifecycle_service,
        )

    # -- accessors ---------------------------------------------------------

    @property
    def application(self) -> StudioApplication:
        return self._application

    @property
    def services(self) -> StudioRuntimeServices:
        return self._services

    @property
    def profiles(self) -> object | None:
        """The configured model profiles, or `None` when none were supplied."""

        return self._profiles

    @property
    def settings(self) -> object | None:
        return self._settings

    @property
    def model_lifecycle(self) -> object | None:
        """The lifecycle service: select, load, unload, switch, report."""

        return self._model_lifecycle

    @property
    def host_name(self) -> str:
        return self._host.name

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    # -- readiness ---------------------------------------------------------

    def readiness(self) -> StudioReadiness:
        """Application readiness, established without loading a model.

        Calls only backend reads. A shut-down composition reports `ready=False`
        rather than raising, so a caller can always ask.
        """

        if self.closed:
            return StudioReadiness(
                host=self._host.name,
                session_id=self._session_id,
                ready=False,
                backend_id="",
                backend_state="stopped",
                backend_is_mock=False,
                model_loaded=False,
                result_delivery_configured=self._services.result_root is not None,
                model_count=0,
                host_metadata=self._host.describe(),
                services=self._services.describe(),
            )

        status = self._application.get_backend_status()
        residency = self._application.get_current_model()
        return StudioReadiness(
            host=self._host.name,
            session_id=self._session_id,
            ready=bool(getattr(status, "ready", False)),
            backend_id=str(getattr(status, "backend_id", "")),
            backend_state=str(getattr(status, "state", "")),
            backend_is_mock=bool(getattr(status, "is_mock", False)),
            model_loaded=bool(getattr(residency, "loaded", False)),
            result_delivery_configured=self._services.result_root is not None,
            model_count=len(self._application.list_models()),
            host_metadata=self._host.describe(),
            services=self._services.describe(),
        )

    # -- shutdown ----------------------------------------------------------

    def shutdown(self) -> None:
        """Deterministic and idempotent.

        The application is shut down first and the host second, so a host that
        raises cannot leave the backend running. The closed flag is set before
        either, so a second call is a no-op even if the first one raised.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            # The warm session closes first: it owns a model, and shutting the
            # application down around a still-loaded session would leave the
            # heaviest resource to a finalizer. A refusing lifecycle must not
            # block the rest of shutdown.
            lifecycle = self._model_lifecycle
            if lifecycle is not None:
                try:
                    lifecycle.shutdown()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - reported by its own state
                    pass
            self._application.shutdown()
        finally:
            self._host.close()

    def __enter__(self) -> "StudioComposition":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()


def build_standalone(
    *,
    result_root: Path | None = None,
    backend_factory: Callable[[StudioRuntimeServices], BackendAdapter] | None = None,
    backend_kind: str = MOCK_BACKEND,
    backend_name: str | None = None,
    headless_runtime: object | None = None,
    headless_generation: object | None = None,
    **kwargs: object,
) -> StudioComposition:
    """Convenience for the standalone product path.

    `backend_kind` is the one selector: `"mock"` or `"headless"`. Anything else
    fails with `BACKEND_NOT_WIRED` rather than falling back, and nothing reads
    an environment variable to decide -- `backend_selection.select_backend_name`
    can still *report* a preference, but a caller has to pass it in.
    """

    return StudioComposition.build(
        StandaloneHost(
            backend_factory=backend_factory,
            backend_kind=backend_kind,
            backend_name=backend_name,
            headless_runtime=headless_runtime,
            headless_generation=headless_generation,
        ),
        result_root=result_root,
        **kwargs,  # type: ignore[arg-type]
    )


def build_extension(
    host_services: object | None = None,
    *,
    result_root: Path | None = None,
    backend_factory: Callable[[StudioRuntimeServices], BackendAdapter] | None = None,
    **kwargs: object,
) -> StudioComposition:
    """Convenience for the extension-hosted path."""

    return StudioComposition.build(
        ExtensionHost(host_services, backend_factory=backend_factory),
        result_root=result_root,
        **kwargs,  # type: ignore[arg-type]
    )
