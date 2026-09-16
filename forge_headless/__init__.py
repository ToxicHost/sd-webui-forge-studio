"""Owned headless integration boundary between Studio and retained Forge.

This package exists so Forge Studio can reach the retained inference backend
without importing or constructing the Gradio UI.

Why it lives here and not in ``forge_studio``:

    ``forge_studio`` is purity-locked. An AST test asserts no module under it
    imports ``torch``, ``torchvision``, ``gradio``, or ``numpy``, and none reads
    ``sys.platform`` or ``platform.system``. That rule is load-bearing -- it is
    what keeps the Studio contracts portable and testable without a GPU. This
    package is allowed to import retained Forge modules, so it must sit outside
    that boundary.

Why it is not in ``modules`` or ``modules_forge``:

    Those are the Neo/A1111-derived application layers, and every module-level
    Gradio import in the repository lives there. Placing the headless boundary
    inside them would defeat its purpose.

Hard rules for everything in this package:

    - never import ``gradio`` or ``gradio_client``, directly or transitively;
    - never import ``modules.ui`` or ``modules.ui_tempdir``;
    - never construct HTTP presentation;
    - never own Studio contracts;
    - never expose raw Forge globals to Studio;
    - never claim a runtime fact it has not established.
"""

from __future__ import annotations

from .catalogue import ModelCatalogue
from .contracts import (
    HeadlessBlocker,
    HeadlessCapability,
    HeadlessError,
    HeadlessIdentity,
    HeadlessReadiness,
    LoadSupport,
    ModelAvailability,
    ModelCandidate,
    RuntimeState,
)
from .facade import ForgeHeadlessRuntime
from .loader_port import LoaderPort, LoadRequest, PolicyGatedLoader
from .result_descriptor import ForgeResultDescriptor
from .studio_adapter import HeadlessBackendAdapter, HeadlessBackendError

__all__ = (
    "ForgeHeadlessRuntime",
    "ForgeResultDescriptor",
    "HeadlessBackendAdapter",
    "HeadlessBackendError",
    "HeadlessBlocker",
    "HeadlessCapability",
    "HeadlessError",
    "HeadlessIdentity",
    "HeadlessReadiness",
    "LoadRequest",
    "LoadSupport",
    "LoaderPort",
    "ModelAvailability",
    "ModelCandidate",
    "ModelCatalogue",
    "PolicyGatedLoader",
    "RuntimeState",
)

INTEGRATION_REVISION = "headless-forge/phase2a"
