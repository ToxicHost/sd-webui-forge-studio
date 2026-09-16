"""Keep the reason a load failed, without leaking what it was loading.

The loader already knows exactly which stage failed: ``SessionLoadError``
carries a stable ``code``, a scalar ``message`` and a ``step``. The lifecycle
used to catch ``BaseException`` and keep only ``type(exc).__name__``, so a
failure that knew it happened at ``engine_built`` was reported as

    "The model failed to load (SessionLoadError)."

That is not a cosmetic problem. It is why a live confirmation could not say
which stage failed, and why an owner whose model will not load is told only
that it did not.

What may travel, and what may not:

```text
travels    stable code, load stage, component role, safe reason,
           inner exception CLASS NAME, recoverability
never      absolute paths, filenames, repr(exc), tracebacks
```

The inner exception's *message* is deliberately not forwarded. A message from
Forge or Torch can contain a path, and there is no way to know in advance that
it does not. The class name plus the stage is what carries diagnostic value
without that risk.
"""

from __future__ import annotations

from typing import Any

from .contracts import StructuredError

MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"

#: Stage -> the role the owner would recognise, and owner-facing wording.
#: Stages come from `forge_headless.session_loader`; anything unlisted falls
#: back to a generic but still stage-named reason.
_STAGE_WORDING: dict[str, tuple[str, str]] = {
    "before_payload_access": ("", "Load was cancelled before reading anything."),
    "cancellation_checked": ("", "Load was cancelled."),
    "startup_globals_installed": ("", "Studio could not prepare the model runtime."),
    "payloads_opened": ("", "One of the selected model files could not be opened."),
    "engine_built": ("checkpoint", "The model could not be built from the selected files."),
    "identity_attached": ("", "The model loaded but could not be identified."),
    "bookkeeping_installed": ("", "The model loaded but its bookkeeping failed."),
    "port_built": ("", "The generation port could not be created."),
    "session_built": ("", "The model session could not be created."),
}

#: Stages after which a retry with the same selection is pointless.
_UNRECOVERABLE = {"engine_built", "payloads_opened"}


def _safe_class_name(exc: BaseException) -> str:
    """The exception's class name, and only that.

    A class name is a Python identifier, so it cannot carry a path. Anything
    unexpected is reduced to a placeholder rather than trusted.
    """
    name = type(exc).__name__
    return name if name.isidentifier() else "Exception"


def load_failure_from(exc: BaseException) -> StructuredError:
    """Build the failure the lifecycle reports, preserving what is safe."""

    code = getattr(exc, "code", None) or MODEL_LOAD_FAILED
    stage = str(getattr(exc, "step", "") or "")
    inner = _safe_class_name(exc)

    role, reason = _STAGE_WORDING.get(stage, ("", ""))
    if not reason:
        reason = (
            f"The model failed to load at the {stage} stage."
            if stage
            else "The model failed to load."
        )

    # `message` stays a scalar sentence for surfaces that show only a string.
    # The structured fields carry the rest.
    message = reason if stage else f"{reason} ({inner})"

    detail: dict[str, Any] = {
        "load_stage": stage,
        "inner_error_class": inner,
        "recoverability": (
            "not_recoverable" if stage in _UNRECOVERABLE else "retryable"
        ),
    }
    if role:
        detail["component_role"] = role

    return StructuredError(
        code=str(code),
        message=message,
        field="profile_id",
        detail=detail,
    )


__all__ = ("MODEL_LOAD_FAILED", "load_failure_from")
