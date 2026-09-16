"""Contracts for the headless Forge boundary.

Standard library only. These types are what the Studio-side adapter consumes;
they deliberately do not expose Forge objects, module handles, or globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


UNKNOWN = "UNKNOWN"


class RuntimeState(str, Enum):
    """Headless runtime lifecycle.

    Phase 1 may legitimately reach at most ``READY_NO_MODEL``; Phase 2A adds
    ``CATALOGUE_READY_NO_MODEL`` and the transient
    ``MODEL_LOAD_VALIDATING``. Any state beyond those would assert a resident
    model or a generation, neither of which these phases perform.

    ``MODEL_LOADING`` deliberately stays unreachable: it must mean a loader has
    begun opening a checkpoint, and no loader does.
    """

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY_NO_MODEL = "ready_no_model"
    CATALOGUE_READY_NO_MODEL = "catalogue_ready_no_model"
    MODEL_LOAD_VALIDATING = "model_load_validating"
    MODEL_LOADING = "model_loading"
    READY = "ready"
    BUSY = "busy"
    DEGRADED = "degraded"
    FAILED = "failed"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


#: States Phase 1 is permitted to reach. Asserted by test.
PHASE1_REACHABLE_STATES = frozenset(
    {
        RuntimeState.UNINITIALIZED,
        RuntimeState.INITIALIZING,
        RuntimeState.READY_NO_MODEL,
        RuntimeState.DEGRADED,
        RuntimeState.FAILED,
        RuntimeState.SHUTTING_DOWN,
        RuntimeState.STOPPED,
    }
)

#: States Phase 2A is permitted to reach. Asserted by test.
PHASE2A_REACHABLE_STATES = PHASE1_REACHABLE_STATES | {
    RuntimeState.CATALOGUE_READY_NO_MODEL,
    RuntimeState.MODEL_LOAD_VALIDATING,
}


class ModelAvailability(str, Enum):
    """Whether the catalogued file is still there and readable."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"


class LoadSupport(str, Enum):
    """How far the retained backend's own loader can take this format.

    Derived from source, not from Stable Diffusion filename conventions:
    ``modules/sd_models.py`` enumerates ``.ckpt``, ``.safetensors``, ``.gguf``,
    and ``backend/utils.py::load_torch_file`` dispatches on ``.safetensors`` /
    ``.sft``, ``.gguf``, and a ``torch.load`` fallback.
    """

    LOAD_PLUMBED = "load_plumbed"
    RECOGNIZED_NOT_PLUMBED = "recognized_not_plumbed"
    UNSUPPORTED = "unsupported"


class HeadlessError(Exception):
    """Structured headless failure. Carries a stable code, never a traceback."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class HeadlessOptionMissing(HeadlessError, AttributeError):
    """A read for an option the boundary does not supply.

    Also an `AttributeError`, deliberately. Retained Forge code asks for
    optional settings with `getattr(shared.opts, name, default)` -- see
    `modules/script_callbacks.py:192`, which supplies `[]` because the
    dynamically generated `prioritized_callbacks_<category>` options may simply
    not exist. `getattr` honours its default only on `AttributeError`, so a
    plain `HeadlessError` sails straight past a fallback Forge already wrote.
    Tier-0 live attempt 04 died exactly there, inside `CFGDenoiser.forward`.

    Being both keeps every existing `except HeadlessError` and
    `HEADLESS_OPTION_NOT_AVAILABLE` assertion working, while `getattr(opts, x,
    d) == d` and `hasattr(opts, x) is False` behave the way Python promises.

    Strictness is preserved where it belongs: a bare `opts.name` still raises,
    and `opts[name]` / `opts.require(name)` raise a plain `HeadlessError` that
    no `getattr` default can swallow.
    """


@dataclass(frozen=True)
class HeadlessBlocker:
    """One precisely located reason a capability is not available.

    ``module`` and ``symbol`` are repository-relative identifiers, never
    absolute paths.
    """

    code: str
    module: str
    symbol: str
    detail: str
    phase: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "module": self.module,
            "symbol": self.symbol,
            "detail": self.detail,
            "phase": self.phase,
        }


@dataclass(frozen=True)
class HeadlessIdentity:
    """Non-sensitive, locally derived facts about the runtime.

    Every field is either read from the repository or observed in-process. No
    absolute path, user name, host name, or device serial appears here.
    """

    backend_family: str
    backend_source_revision: str
    integration_revision: str
    headless_mode: bool
    compatibility_mode: bool
    gradio_imported: bool
    model_loaded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_family": self.backend_family,
            "backend_source_revision": self.backend_source_revision,
            "integration_revision": self.integration_revision,
            "headless_mode": self.headless_mode,
            "compatibility_mode": self.compatibility_mode,
            "gradio_imported": self.gradio_imported,
            "model_loaded": self.model_loaded,
        }


@dataclass(frozen=True)
class HeadlessCapability:
    """What this integration can actually do, and what it declines to claim.

    Device, dtype, attention backend, and model limits are ``UNKNOWN`` in
    Phase 1 and must stay that way: establishing them requires importing
    ``backend.memory_management``, which probes device memory at import time.
    Reporting a guess here would be worse than reporting ignorance.
    """

    headless_startup_supported: bool
    model_loading_implemented: bool
    generation_implemented: bool
    cancellation_implemented: bool
    progress_implemented: bool
    owned_result_delivery_enabled: bool
    legacy_ui_available: bool
    device_type: str = UNKNOWN
    dtype_policy: str = UNKNOWN
    attention_backend: str = UNKNOWN
    maximum_dimension: str = UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "headless_startup_supported": self.headless_startup_supported,
            "model_loading_implemented": self.model_loading_implemented,
            "generation_implemented": self.generation_implemented,
            "cancellation_implemented": self.cancellation_implemented,
            "progress_implemented": self.progress_implemented,
            "owned_result_delivery_enabled": self.owned_result_delivery_enabled,
            "legacy_ui_available": self.legacy_ui_available,
            "device_type": self.device_type,
            "dtype_policy": self.dtype_policy,
            "attention_backend": self.attention_backend,
            "maximum_dimension": self.maximum_dimension,
        }


@dataclass(frozen=True)
class ModelCandidate:
    """One catalogue entry.

    ``model_id`` is an opaque digest of the resolved root plus the root-relative
    location: stable for the same file under the same root, and one-way, so it
    cannot be turned back into a path. ``relative_location`` is root-relative by
    construction and never absolute; the Studio HTTP projection drops it
    entirely and carries only ``model_id`` and ``display_name``.

    No checkpoint content is read to build this record. ``size_bytes`` comes
    from a directory-entry ``stat``, never from opening the file.
    """

    model_id: str
    display_name: str
    model_kind: str
    format: str
    relative_location: str
    size_bytes: int
    availability: ModelAvailability
    load_support: LoadSupport
    load_blocking_reason: str
    is_mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "model_kind": self.model_kind,
            "format": self.format,
            "relative_location": self.relative_location,
            "size_bytes": self.size_bytes,
            "availability": self.availability.value,
            "load_support": self.load_support.value,
            "load_blocking_reason": self.load_blocking_reason,
            "is_mock": self.is_mock,
        }


@dataclass(frozen=True)
class HeadlessReadiness:
    """Result of a readiness probe.

    ``verified_modules`` lists retained Forge modules this probe actually
    imported. ``statically_verified_packages`` lists package trees whose
    module-level import graph was parsed and found free of Gradio *without*
    importing them -- a weaker but safe claim, and labelled as such.
    """

    state: RuntimeState
    gradio_imported: bool
    gradio_client_imported: bool
    torch_imported: bool
    cuda_initialized: str
    model_loaded: bool
    generation_performed: bool
    verified_modules: tuple[str, ...] = ()
    statically_verified_packages: tuple[str, ...] = ()
    blockers: tuple[HeadlessBlocker, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.state is RuntimeState.READY_NO_MODEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "gradio_imported": self.gradio_imported,
            "gradio_client_imported": self.gradio_client_imported,
            "torch_imported": self.torch_imported,
            "cuda_initialized": self.cuda_initialized,
            "model_loaded": self.model_loaded,
            "generation_performed": self.generation_performed,
            "verified_modules": list(self.verified_modules),
            "statically_verified_packages": list(
                self.statically_verified_packages
            ),
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }
