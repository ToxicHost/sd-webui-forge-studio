"""The headless Forge runtime facade.

Phase 1 implemented ``construct``, ``probe_readiness``, ``get_runtime_identity``,
``get_runtime_capability``, and ``shutdown``. Phase 2A adds
``configure_catalogue_root``, ``list_models``, and ``load_model`` -- where
``load_model`` validates the request all the way to the loader port and is then
refused by policy, before any checkpoint is opened or any device is touched.
Generation, progress, and cancellation still raise a stable structured error.
Nothing here fakes a model or a generation.

Two deliberate design choices worth stating plainly.

**No Forge import at module scope.** Importing this module must be free of side
effects, so every retained-Forge import happens inside ``probe_readiness`` and
is individually guarded.

**Only genuinely safe modules are imported.** Three backend modules --
``memory_management``, ``operations``, and ``operations_mixed_precision`` --
call device-property and memory-probing functions at module scope. Importing
them would initialise a device, which this phase must not do. They are
therefore reported as an explicit Phase 2 gate rather than imported and
reported as "working".
"""

from __future__ import annotations

import sys
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .contracts import (
    UNKNOWN,
    HeadlessBlocker,
    HeadlessCapability,
    HeadlessError,
    HeadlessIdentity,
    HeadlessReadiness,
    RuntimeState,
)
from .import_graph import scan_package
from .loader_port import LOAD_OPERATION, PolicyGatedLoader


#: Retained Forge modules imported by the readiness probe.
#:
#: Each is free of Gradio, free of Torch, performs no device probing at module
#: scope, and needs nothing outside the standard library -- so the probe can run
#: under `-I -S` (isolated, no site-packages) and the proof does not depend on
#: which third-party packages happen to be installed.
#:
#: `backend.logging` is a deliberate omission: it is Gradio-free and
#: device-free, but imports `rich`, so it cannot be reached under `-S`. Adding
#: it would couple this proof to site-packages without establishing anything
#: further about the Gradio boundary. Same reasoning excludes
#: `backend.patcher.clip`, which needs `psutil`.
SAFE_BACKEND_MODULES = (
    "backend.args",
    "backend.shared",
    "backend.misc.eps",
    "backend.text_processing.parsing",
)

#: Backend modules that probe a device at import time. Importing any of them is
#: a Phase 2 decision, not a Phase 1 side effect.
DEVICE_PROBING_BACKEND_MODULES = (
    "backend.memory_management",
    "backend.operations",
    "backend.operations_mixed_precision",
)

#: `modules.*` packages inference routes through that reach a module-level
#: Gradio import.
#:
#: EMPTY as of R1.5 (2026-08-11). This tuple named five edges, measured rather
#: than assumed, and all five have been cut -- each by deferring one
#: module-scope import into the function that actually uses it:
#:
#:     modules.processing  -> modules.scripts            scripts.py:8
#:     modules.processing  -> modules_forge.main_entry   processing.py:36
#:     modules.processing  -> modules.profiling          profiling.py:3
#:     modules.sd_models   -> modules.processing         (transitive; cleared with it)
#:     modules.sd_samplers -> modules.sd_samplers_common sd_samplers_common.py:30
#:
#: A sixth surfaced once the first five were gone -- `modules.scripts` ->
#: `modules.scripts_postprocessing` -- which had been masked behind
#: `scripts.py:8`. It is cut too. A static walk over the 121 modules reachable
#: from `modules.processing` by module-scope import finds no seventh.
#:
#: Kept as an empty tuple rather than deleted: `describe_blockers` iterates it,
#: and the divergence ledger's removal condition is that it stays empty. An
#: upstream sync that reintroduces a UI import here should repopulate it.
GRADIO_CONTAMINATED_INFERENCE_MODULES = ()

#: Inference-path modules proved free of any module-level Gradio import.
#: The first seven by Phase 2A; the rest by R1.5, which cleared the whole
#: generation path rather than naming what it could not reach.
GRADIO_CLEARED_MODULES = (
    "modules.shared",
    "modules.script_callbacks",
    "modules.extensions",
    "modules.options",
    "modules.shared_items",
    "modules.resolution",
    "modules.infotext_core",
    "modules.processing",
    "modules.sd_models",
    "modules.sd_samplers",
    "modules.sd_samplers_common",
    "modules.scripts",
    "modules.scripts_postprocessing",
    "modules.profiling",
)

#: States in which a load request may be validated.
_CATALOGUE_STATES = frozenset(
    {RuntimeState.READY_NO_MODEL, RuntimeState.CATALOGUE_READY_NO_MODEL}
)

FORBIDDEN_TOP_LEVEL = ("gradio", "gradio_client")

_NOT_IMPLEMENTED = "HEADLESS_OPERATION_NOT_IMPLEMENTED"
_NOT_READY = "HEADLESS_BACKEND_NOT_READY"


class ForgeHeadlessRuntime:
    """Owned facade over the retained Forge backend, with no Gradio on the path."""

    def __init__(
        self,
        *,
        repository_root: Path | None = None,
        workspace_root: Path | None = None,
        compatibility_mode: bool = False,
        loader: object | None = None,
    ) -> None:
        self._root = (
            Path(repository_root).resolve()
            if repository_root is not None
            else Path(__file__).resolve().parents[1]
        )
        # The workspace is the boundary the owner authorized, and it is the
        # *parent* of the repository: `Evidence/` -- where contained test
        # fixtures live -- is a sibling of `app/`, not inside it. A catalogue
        # root outside this is rejected.
        self._workspace = (
            Path(workspace_root).resolve()
            if workspace_root is not None
            else self._root.parent
        )
        self._compatibility_mode = bool(compatibility_mode)
        self._state = RuntimeState.UNINITIALIZED
        self._readiness: HeadlessReadiness | None = None
        self._catalogue: object | None = None
        self._loader: object = loader if loader is not None else PolicyGatedLoader()
        self._request_counter = 0
        self._lock = RLock()

    # -- lifecycle -------------------------------------------------------

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    @classmethod
    def construct(
        cls,
        *,
        repository_root: Path | None = None,
        workspace_root: Path | None = None,
        compatibility_mode: bool = False,
        loader: object | None = None,
    ) -> "ForgeHeadlessRuntime":
        """Build the facade. Imports nothing and touches no device.

        ``loader`` defaults to ``PolicyGatedLoader``, which refuses every real
        load. Tests inject a recording loader to observe what reaches the port.
        """

        return cls(
            repository_root=repository_root,
            workspace_root=workspace_root,
            compatibility_mode=compatibility_mode,
            loader=loader,
        )

    def probe_readiness(self) -> HeadlessReadiness:
        """Establish what can be claimed, importing only what is safe.

        Idempotent: the first call computes, later calls return the same
        record. Never raises for a blocked backend -- it fails closed into a
        readiness record naming the blocker.
        """

        with self._lock:
            if self._readiness is not None:
                return self._readiness
            self._state = RuntimeState.INITIALIZING

        blockers: list[HeadlessBlocker] = []
        verified: list[str] = []

        # 1. Import the safe retained-Forge subset. A forbidden import raised
        #    by a blocker hook surfaces here as a located blocker.
        for name in SAFE_BACKEND_MODULES:
            try:
                __import__(name)
                verified.append(name)
            except BaseException as exc:  # noqa: BLE001 - blocker hooks may raise anything
                blockers.append(
                    HeadlessBlocker(
                        code="HEADLESS_SAFE_MODULE_IMPORT_FAILED",
                        module=name,
                        symbol="<module>",
                        detail=f"{type(exc).__name__}: {exc}",
                        phase="phase-1",
                    )
                )

        # 2. Statically verify the backend tree is Gradio-free without
        #    importing it. Weaker than an import, and labelled as such.
        static_packages: list[str] = []
        backend_root = self._root / "backend"
        if backend_root.is_dir():
            result = scan_package(backend_root, self._root, FORBIDDEN_TOP_LEVEL)
            if result.clean:
                static_packages.append(
                    f"backend ({result.modules_parsed} modules)"
                )
            else:
                for module, imported in result.offenders[:5]:
                    blockers.append(
                        HeadlessBlocker(
                            code="HEADLESS_BACKEND_IMPORTS_GRADIO",
                            module=module,
                            symbol=imported,
                            detail="module-level Gradio import inside backend/",
                            phase="phase-1",
                        )
                    )
        else:
            blockers.append(
                HeadlessBlocker(
                    code="HEADLESS_BACKEND_TREE_MISSING",
                    module="backend",
                    symbol="<package>",
                    detail="backend/ not found beneath the repository root",
                    phase="phase-1",
                )
            )

        # 3. Record the known Phase 2 gates. These are not Phase 1 failures:
        #    they are the reason model catalogue, load, and generation stay
        #    unimplemented.
        for name in DEVICE_PROBING_BACKEND_MODULES:
            blockers.append(
                HeadlessBlocker(
                    code="HEADLESS_MODULE_PROBES_DEVICE_AT_IMPORT",
                    module=name,
                    symbol="<module scope>",
                    detail=(
                        "calls device-property or memory-probing functions at "
                        "import time; importing it would initialise a device"
                    ),
                    phase="phase-2",
                )
            )
        for name, via, detail in GRADIO_CONTAMINATED_INFERENCE_MODULES:
            blockers.append(
                HeadlessBlocker(
                    code="HEADLESS_INFERENCE_MODULE_IMPORTS_GRADIO",
                    module=name,
                    symbol=via,
                    detail=detail,
                    phase="phase-2",
                )
            )

        readiness = HeadlessReadiness(
            state=self._resolve_state(blockers, verified),
            gradio_imported=self._module_present("gradio"),
            gradio_client_imported=self._module_present("gradio_client"),
            torch_imported=self._module_present("torch"),
            cuda_initialized=self._cuda_initialized_without_initializing(),
            model_loaded=False,
            generation_performed=False,
            verified_modules=tuple(verified),
            statically_verified_packages=tuple(static_packages),
            blockers=tuple(blockers),
        )

        with self._lock:
            self._readiness = readiness
            self._state = readiness.state
        return readiness

    def _resolve_state(
        self,
        blockers: list[HeadlessBlocker],
        verified: list[str],
    ) -> RuntimeState:
        """Phase 1 reaches READY_NO_MODEL only on positive evidence."""

        phase1 = [b for b in blockers if b.phase == "phase-1"]
        if phase1:
            return RuntimeState.FAILED
        if self._module_present("gradio") or self._module_present(
            "gradio_client"
        ):
            return RuntimeState.DEGRADED
        if len(verified) != len(SAFE_BACKEND_MODULES):
            return RuntimeState.DEGRADED
        return RuntimeState.READY_NO_MODEL

    @staticmethod
    def _module_present(name: str) -> bool:
        """Whether a module is already in sys.modules. Never imports it."""

        return name in sys.modules

    @staticmethod
    def _cuda_initialized_without_initializing() -> str:
        """Report CUDA initialisation only if it can be read without causing it.

        ``torch.cuda.is_initialized()`` is safe -- it reads a flag. But asking
        for it requires Torch to be imported, and this phase does not import
        Torch. So when Torch is absent the honest answer is UNKNOWN, not False.
        """

        torch = sys.modules.get("torch")
        if torch is None:
            return UNKNOWN
        try:
            return "true" if torch.cuda.is_initialized() else "false"
        except Exception:  # noqa: BLE001 - any failure means we cannot tell
            return UNKNOWN

    def get_runtime_identity(self) -> HeadlessIdentity:
        """Non-sensitive locally derived facts. No path, user, or host name."""

        return HeadlessIdentity(
            backend_family="forge-neo-retained",
            backend_source_revision=self._backend_source_revision(),
            integration_revision="headless-forge/phase1",
            headless_mode=True,
            compatibility_mode=self._compatibility_mode,
            gradio_imported=self._module_present("gradio"),
            model_loaded=False,
        )

    def _backend_source_revision(self) -> str:
        """Read the retained Forge version from the repository, or UNKNOWN.

        Deliberately reads the tracked version module rather than shelling out
        to git: no subprocess, no absolute path in the result.
        """

        candidate = self._root / "modules_forge" / "forge_version.py"
        if not candidate.is_file():
            return UNKNOWN
        version = release = None
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    version = stripped.split("=", 1)[1].strip().strip("\"'")
                elif stripped.startswith("release") and "=" in stripped:
                    release = stripped.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            return UNKNOWN
        if version and release:
            return f"{version} {release}"
        return version or release or UNKNOWN

    def get_runtime_capability(self) -> HeadlessCapability:
        """What this integration can do. Device facts stay UNKNOWN in Phase 1."""

        readiness = self._readiness
        startup = bool(readiness and readiness.ready)
        return HeadlessCapability(
            headless_startup_supported=startup,
            model_loading_implemented=False,
            generation_implemented=False,
            cancellation_implemented=False,
            progress_implemented=False,
            owned_result_delivery_enabled=True,
            legacy_ui_available=self._compatibility_mode,
        )

    def shutdown(self) -> None:
        """Release the facade. Idempotent, and safe before any probe."""

        with self._lock:
            if self._state is RuntimeState.STOPPED:
                return
            self._state = RuntimeState.SHUTTING_DOWN
            self._readiness = None
            self._catalogue = None
            self._state = RuntimeState.STOPPED

    # -- Phase 2A: catalogue and load-request plumbing --------------------

    def configure_catalogue_root(
        self,
        root: object,
        *,
        recursive: bool = False,
        role: str | None = None,
        allow_external: bool = False,
    ) -> int:
        """Point the catalogue at one explicitly supplied root.

        Returns the number of candidates found. Raises ``HeadlessError`` for a
        root that is missing, is not a directory, is a network or device
        location, is a whole drive, or -- unless ``allow_external`` is set --
        is outside the workspace. Nothing is auto-discovered and no default
        root exists.
        """

        from .catalogue import MODEL_ROLE_CHECKPOINT, ModelCatalogue

        catalogue = ModelCatalogue(
            root,  # type: ignore[arg-type]
            workspace_root=self._workspace,
            recursive=recursive,
            role=role or MODEL_ROLE_CHECKPOINT,
            allow_external=allow_external,
        )
        count = len(catalogue.enumerate())
        with self._lock:
            self._catalogue = catalogue
            if self._state is RuntimeState.READY_NO_MODEL:
                self._state = RuntimeState.CATALOGUE_READY_NO_MODEL
        return count

    @property
    def catalogue_configured(self) -> bool:
        return self._catalogue is not None

    def list_models(self) -> tuple[object, ...]:
        """Enumerate the configured catalogue. Pure: no load, no device, no open."""

        catalogue = self._catalogue
        if catalogue is None:
            raise HeadlessError(
                "HEADLESS_CATALOGUE_NOT_CONFIGURED",
                "No model root has been configured.",
            )
        return catalogue.enumerate()

    def load_model(self, model_id: str) -> None:
        """Validate a load request all the way to the loader port, then refuse.

        The pipeline is: catalogue lookup, containment revalidation,
        availability validation, format/support validation, immutable request,
        loader port. Only the port refuses, so callers can tell "unknown model"
        from "not authorized".
        """

        from .loader_port import LoadRequest, validate_load_support

        catalogue = self._catalogue
        if catalogue is None:
            raise HeadlessError(
                "HEADLESS_CATALOGUE_NOT_CONFIGURED",
                "No model root has been configured.",
            )
        requested = str(model_id or "").strip()
        if not requested:
            raise HeadlessError(
                "HEADLESS_MODEL_ID_REQUIRED",
                "A model id is required.",
            )

        # Lookup + containment revalidation + availability, in the catalogue.
        candidate, source = catalogue.resolve(requested)
        validate_load_support(candidate)

        request = LoadRequest(
            model_id=candidate.model_id,
            model_kind=candidate.model_kind,
            format=candidate.format,
            operation=LOAD_OPERATION,
            request_id=self._next_request_id(),
            source=source,
        )

        previous = self.state
        with self._lock:
            if self._state in _CATALOGUE_STATES:
                self._state = RuntimeState.MODEL_LOAD_VALIDATING
        try:
            self._loader.load(request)
        finally:
            # Residency never changes and the runtime never fails: a refused
            # load must leave the catalogue ready and the runtime healthy.
            with self._lock:
                if self._state is RuntimeState.MODEL_LOAD_VALIDATING:
                    self._state = previous

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_counter += 1
            counter = self._request_counter
        return f"load-{counter:06d}-{uuid4().hex[:12]}"

    # -- deliberately unimplemented in Phase 2A --------------------------

    def submit_generation(self, request: object) -> None:
        del request
        raise HeadlessError(
            _NOT_READY,
            "Generation requires a resident model. No model can be loaded in "
            "Phase 1.",
        )

    def poll_generation(self, job_id: str) -> None:
        del job_id
        raise HeadlessError(
            _NOT_READY,
            "No generation can be in flight in Phase 1.",
        )

    def cancel_generation(self, job_id: str) -> None:
        del job_id
        raise HeadlessError(
            _NOT_READY,
            "No generation can be in flight in Phase 1.",
        )
