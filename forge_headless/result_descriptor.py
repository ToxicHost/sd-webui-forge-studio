"""The seam by which a Forge-produced file becomes an owned Studio handle.

The point of this module is what it *does not* touch. A generated file reaches
the browser through Studio's own `ResultRegistry` -- opaque handles, containment
re-verified on every read, MIME allowlist, sandboxed response CSP -- and never
through:

    gradio.processing_utils          gradio_client.utils
    async_move_files_to_cache        the Gradio file cache
    Gradio upload directories        Gradio download routes

A descriptor is a plain value object: a path the backend owns, plus a declared
media type. Validation happens here; registration happens in Studio. Neither
step imports Gradio.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import HeadlessError


#: Media types a Forge result may declare. Kept in step with the Studio
#: registry's own allowlist; a descriptor the registry would refuse must be
#: refused here too, so the failure is located at the boundary.
DELIVERABLE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)

MAX_DESCRIPTOR_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ForgeResultDescriptor:
    """An internal reference to a file the backend produced and owns.

    ``path`` is a real filesystem path and is **never** sent to a browser.
    Studio converts it into an opaque handle; the path itself stays inside the
    process.
    """

    job_id: str
    path: Path
    media_type: str

    def validate(self, *, owned_root: Path) -> Path:
        """Check the descriptor is deliverable, or fail closed with a code.

        Mirrors the registry's own rules so a bad descriptor is rejected at the
        seam rather than deeper in. Does not register anything.
        """

        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise HeadlessError(
                "RESULT_DESCRIPTOR_JOB_ID_INVALID",
                "A result descriptor requires a non-empty job id.",
            )
        if self.media_type not in DELIVERABLE_MEDIA_TYPES:
            raise HeadlessError(
                "RESULT_DESCRIPTOR_MEDIA_TYPE_UNSUPPORTED",
                f"{self.media_type!r} is not a deliverable result type.",
            )
        try:
            resolved = Path(self.path).resolve(strict=True)
        except OSError as exc:
            raise HeadlessError(
                "RESULT_DESCRIPTOR_UNREADABLE",
                "The described result file could not be resolved.",
            ) from exc
        if not resolved.is_file():
            raise HeadlessError(
                "RESULT_DESCRIPTOR_NOT_A_FILE",
                "The described result is not a regular file.",
            )
        root = Path(owned_root).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise HeadlessError(
                "RESULT_DESCRIPTOR_OUTSIDE_ROOT",
                "The described result is outside the owned result root.",
            ) from exc
        if resolved.stat().st_size > MAX_DESCRIPTOR_BYTES:
            raise HeadlessError(
                "RESULT_DESCRIPTOR_TOO_LARGE",
                "The described result is too large to deliver.",
            )
        return resolved

    def to_dict(self) -> dict[str, str]:
        """Diagnostic view. Emits the file *name* only -- never the full path.

        Used in evidence and logs. The browser receives none of this; it sees
        only the opaque handle Studio mints.
        """

        return {
            "job_id": self.job_id,
            "file_name": Path(self.path).name,
            "media_type": self.media_type,
        }
