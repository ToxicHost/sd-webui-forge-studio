"""Independently launchable Forge Studio application surface."""

from .application import StudioApplication
from .backend import BackendAdapter
from .composition import (
    BACKEND_KINDS,
    EXTENSION,
    HEADLESS_BACKEND,
    HOST_NAMES,
    MOCK_BACKEND,
    STANDALONE,
    ExtensionHost,
    InlineExecutor,
    StandaloneHost,
    StudioComposition,
    StudioExecutor,
    StudioHost,
    StudioReadiness,
    StudioRuntimeServices,
    build_extension,
    build_standalone,
)
from .contracts import (
    AttentionBackend,
    BackendStatus,
    CancellationResult,
    DeviceType,
    DtypePolicy,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    JobState,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
    StructuredError,
    StudioError,
)
from .mock_backend import MockBackend
from .result_delivery import (
    CasePolicy,
    ResultAsset,
    ResultPayload,
    ResultRegistry,
    detect_case_policy,
)

__all__ = (
    "AttentionBackend",
    "BACKEND_KINDS",
    "BackendAdapter",
    "BackendStatus",
    "CancellationResult",
    "CasePolicy",
    "DeviceType",
    "DtypePolicy",
    "EXTENSION",
    "ExtensionHost",
    "HEADLESS_BACKEND",
    "GeneratedResult",
    "GenerationJobIdentity",
    "GenerationRequest",
    "HOST_NAMES",
    "InlineExecutor",
    "JobState",
    "MOCK_BACKEND",
    "MockBackend",
    "ModelCapability",
    "ModelResidency",
    "ModelSummary",
    "ProgressEvent",
    "ResultAsset",
    "ResultPayload",
    "ResultRegistry",
    "STANDALONE",
    "StandaloneHost",
    "StructuredError",
    "StudioApplication",
    "StudioComposition",
    "StudioError",
    "StudioExecutor",
    "StudioHost",
    "StudioReadiness",
    "StudioRuntimeServices",
    "build_extension",
    "build_standalone",
    "detect_case_policy",
)

#: Studio's version, the ONE place it is written (owner, 2026-10-20: 0.9.x
#: until 1.0, which comes with Regional and Live). Settings > About shows it
#: through /api/version. Raise the last number with every tester update. The
#: page's `?v=4.17.0` (index.html) is only a cache token inherited from the
#: Extension, pinned by the loader's CSP hash; it is not shown anywhere.
__version__ = "0.9.6"
