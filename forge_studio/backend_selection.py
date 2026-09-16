"""Backend and legacy-UI selection for Studio.

Two independent selectors, both read-only over an injected environment:

    STUDIO_BACKEND        mock | forge-headless        default: mock
    NEO_UI_COMPATIBILITY  enabled | disabled          default: disabled
    STUDIO_MODEL_ROOT     one explicit directory      default: none

They are independent by design. Choosing the headless Forge backend does not
enable the legacy Neo UI, and disabling the legacy UI does not remove or
change the inference backend.

Neither selector broadens network or model access: they choose which already
present code path runs. This module imports only the standard library --
asserted by test -- so selecting a backend can never itself pull in Torch,
Gradio, or a socket.

``STUDIO_MODEL_ROOT`` is returned verbatim and is **not** validated, expanded,
resolved, or touched here: this module performs no filesystem access at all.
Containment, symlink rejection, case policy, and the workspace restriction are
enforced by ``forge_headless.catalogue.ModelCatalogue``, which is the single
place that decides whether a root is acceptable. There is no default root, so
nothing is ever discovered automatically.
"""

from __future__ import annotations

import os
from typing import Mapping


MOCK = "mock"
FORGE_HEADLESS = "forge-headless"

#: The only accepted backend names. An unrecognised value falls back to the
#: mock rather than raising: a typo in an environment variable should not stop
#: Studio from starting, and the status route reports what was actually
#: selected so the fallback is visible rather than silent.
KNOWN_BACKENDS = (MOCK, FORGE_HEADLESS)

DEFAULT_BACKEND = MOCK

_ENABLED = frozenset({"enabled", "1", "true", "yes", "on"})


def select_backend_name(environ: Mapping[str, str] | None = None) -> str:
    """Which backend Studio should construct. Defaults to the mock."""

    env = os.environ if environ is None else environ
    requested = str(env.get("STUDIO_BACKEND", "") or "").strip().casefold()
    if requested in KNOWN_BACKENDS:
        return requested
    return DEFAULT_BACKEND


def backend_selection_was_honoured(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Whether an explicit request was accepted, so a fallback is reportable."""

    env = os.environ if environ is None else environ
    requested = str(env.get("STUDIO_BACKEND", "") or "").strip()
    if not requested:
        return True
    return requested.casefold() in KNOWN_BACKENDS


def neo_ui_compatibility_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Whether the legacy Neo/Gradio shell is permitted. Disabled by default."""

    env = os.environ if environ is None else environ
    value = str(env.get("NEO_UI_COMPATIBILITY", "") or "").strip().casefold()
    return value in _ENABLED


def configured_model_root(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The one explicitly configured model root, verbatim, or ``None``.

    No default, no expansion, no filesystem access. An empty or whitespace-only
    value means "not configured" rather than "current directory".
    """

    env = os.environ if environ is None else environ
    value = str(env.get("STUDIO_MODEL_ROOT", "") or "").strip()
    return value or None
