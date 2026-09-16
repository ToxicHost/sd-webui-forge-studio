"""The narrow seam between the headless runtime and real model loading.

Phase 2A implements everything up to the seam and then refuses. The refusal is
the *last* step, after the request has been fully validated, so an unknown
model, a deleted model, an unsupported format, and "not authorized" are all
distinguishable rather than collapsing into one generic error.

The port never accepts a filesystem path from the Studio layer. Studio supplies
a ``model_id``; the catalogue turns that into a re-verified ``ContainedSource``;
only then is a request constructed.

No implementation in this module imports Torch or opens a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .catalogue import LOAD_NOT_AUTHORIZED, ContainedSource
from .contracts import HeadlessError, LoadSupport, ModelCandidate


LOAD_OPERATION = "load_checkpoint"


@dataclass(frozen=True)
class LoadRequest:
    """Immutable, fully validated load request.

    ``source`` is contained by construction. ``request_id`` is unique per
    attempt so repeated attempts are distinguishable in a recording loader or a
    log without leaking the path.
    """

    model_id: str
    model_kind: str
    format: str
    operation: str
    request_id: str
    source: ContainedSource

    @property
    def resolved_path(self) -> Path:
        return self.source.resolved

    def to_dict(self) -> dict[str, object]:
        # Deliberately omits ``source.resolved``: this view is safe to log.
        return {
            "model_id": self.model_id,
            "model_kind": self.model_kind,
            "format": self.format,
            "operation": self.operation,
            "request_id": self.request_id,
            "relative_location": self.source.relative_location,
        }


class LoaderPort(Protocol):
    """What a real Phase 2B loader will have to implement."""

    def load(self, request: LoadRequest) -> None:
        """Make the requested checkpoint resident, or raise ``HeadlessError``."""
        ...


class PolicyGatedLoader:
    """The default loader: validates nothing further and refuses.

    It is reached only after catalogue lookup, containment revalidation,
    availability validation, and format validation have all passed, which is why
    it returns a specific "not authorized" code rather than "not implemented".
    """

    #: Set only by an explicitly authorized future phase.
    authorized = False

    def __init__(self) -> None:
        self.refusals = 0

    def load(self, request: LoadRequest) -> None:
        self.refusals += 1
        if request.operation != LOAD_OPERATION:
            raise HeadlessError(
                "HEADLESS_MODEL_OPERATION_UNSUPPORTED",
                "That model operation is not supported.",
            )
        raise HeadlessError(
            LOAD_NOT_AUTHORIZED,
            "Loading a real model is not authorized in this phase.",
        )


def validate_load_support(candidate: ModelCandidate) -> None:
    """Reject a format the retained loader cannot actually take."""
    if candidate.load_support is LoadSupport.LOAD_PLUMBED:
        return
    if candidate.load_support is LoadSupport.RECOGNIZED_NOT_PLUMBED:
        raise HeadlessError(
            "HEADLESS_MODEL_FORMAT_NOT_PLUMBED",
            "That model format is recognized but not yet loadable.",
        )
    raise HeadlessError(
        "HEADLESS_MODEL_FORMAT_UNSUPPORTED",
        "That model format is not supported.",
    )
