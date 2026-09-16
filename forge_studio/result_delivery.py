"""Contained same-origin delivery of owned generated results.

The browser never receives a filesystem path. A completed result is registered
by owned server code, which mints an opaque handle shaped like a path so the
canonical frontend's display-filename derivation keeps working:

    studio-result/<32 hex characters>.<extension>

Lookup is an exact dictionary match against that registry. No handle is ever
joined onto a directory, so traversal, absolute paths, encoded separators, and
alias tricks cannot reach a file that was never registered. Registration
additionally requires the resolved file to live beneath one injected result
root, and every read re-resolves and re-checks containment so a symlink or
reparse point swapped in after registration fails closed.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from secrets import token_hex
from threading import Lock
import os
import re

from .contracts import StructuredError, StudioError


class CasePolicy(str, Enum):
    """How the volume holding the result root treats filename case.

    This is a property of the **volume**, not of the operating system. macOS
    exposes a POSIX API over a case-insensitive filesystem by default, and a
    case-sensitive APFS volume can be mounted anywhere. Deriving the answer
    from ``os.name`` gets macOS wrong in both directions, so the policy is
    resolved once at construction and then used explicitly.
    """

    CASE_SENSITIVE = "case_sensitive"
    CASE_INSENSITIVE = "case_insensitive"
    AUTO_DETECT = "auto_detect"
    INCONCLUSIVE = "inconclusive"


def detect_case_policy(root: Path) -> CasePolicy:
    """Probe one directory for case behaviour, or fail closed.

    Creates at most one empty file with a random opaque name inside ``root``,
    looks it up under a case variant, and removes it. Never leaves an artifact,
    never clobbers an existing path, never follows a link out, never touches
    an external temp directory, and never needs elevated permissions.

    Any uncertainty -- create, lookup, or cleanup failing, or a name collision
    -- returns ``INCONCLUSIVE`` rather than a guess.
    """

    try:
        resolved = Path(root).resolve(strict=True)
        if not resolved.is_dir():
            return CasePolicy.INCONCLUSIVE
    except OSError:
        return CasePolicy.INCONCLUSIVE

    token = token_hex(16)
    probe = resolved / f".studio-case-probe-{token.lower()}"
    variant = resolved / f".studio-case-probe-{token.upper()}"

    created = False
    try:
        if probe.exists() or variant.exists():
            # Astronomically unlikely; treated as inconclusive rather than
            # risking an existing path.
            return CasePolicy.INCONCLUSIVE

        # x mode refuses to clobber and never follows an existing link.
        with open(probe, "xb"):
            pass
        created = True

        if variant.exists():
            # Confirm the variant is the same file, not a coincidence.
            if not probe.samefile(variant):
                return CasePolicy.INCONCLUSIVE
            return CasePolicy.CASE_INSENSITIVE
        return CasePolicy.CASE_SENSITIVE
    except OSError:
        # Any create, lookup, or comparison failure is uncertainty, not a
        # verdict. Every step above is inside this guard deliberately.
        return CasePolicy.INCONCLUSIVE
    finally:
        if created:
            try:
                probe.unlink()
            except OSError:
                # Cleanup failed. The verdict is already computed and the
                # probe name is random and self-identifying, so this leaves at
                # most one empty, obviously-named file inside the owned root.
                pass


def _resolve_case_policy(root: Path, policy: CasePolicy) -> CasePolicy:
    if policy is CasePolicy.AUTO_DETECT:
        return detect_case_policy(root)
    return policy


HANDLE_PREFIX = "studio-result/"
HANDLE_PATTERN = re.compile(r"\Astudio-result/[0-9a-f]{32}\.[a-z0-9]{1,8}\Z")

# Only types the owned result record can actually produce. Serving eligibility
# never derives from a filename extension.
SUPPORTED_MEDIA_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}

MAX_RESULT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ResultAsset:
    """Opaque browser-facing identity for one owned result file."""

    handle: str
    media_type: str
    byte_length: int


@dataclass(frozen=True)
class ResultPayload:
    """Bytes and type for one resolved result, ready to send."""

    media_type: str
    content: bytes


class ResultRegistry:
    """Bounded opaque-handle registry over exactly one owned result root."""

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 64,
        case_policy: CasePolicy = CasePolicy.AUTO_DETECT,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least one")
        self._root = Path(root).resolve()
        self._max_entries = max_entries
        # Resolved exactly once, at construction. Every later containment
        # decision uses this value rather than re-inspecting the OS.
        self._case_policy = _resolve_case_policy(self._root, case_policy)
        self._entries: OrderedDict[str, tuple[Path, str]] = OrderedDict()
        self._evicted: OrderedDict[str, None] = OrderedDict()
        self._lock = Lock()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def case_policy(self) -> CasePolicy:
        return self._case_policy

    def register(self, path: Path, *, media_type: str) -> ResultAsset:
        """Register one owned file and return its opaque handle."""

        extension = SUPPORTED_MEDIA_TYPES.get(media_type)
        if extension is None:
            self._fail(
                "RESULT_MEDIA_TYPE_UNSUPPORTED",
                "That result type cannot be delivered to the browser.",
            )
        resolved = self._contained(path)
        byte_length = resolved.stat().st_size
        if byte_length > MAX_RESULT_BYTES:
            self._fail(
                "RESULT_TOO_LARGE",
                "That result is too large to deliver.",
            )
        handle = f"{HANDLE_PREFIX}{token_hex(16)}.{extension}"
        with self._lock:
            self._entries[handle] = (resolved, media_type)
            self._entries.move_to_end(handle)
            while len(self._entries) > self._max_entries:
                stale, _ = self._entries.popitem(last=False)
                self._remember_evicted(stale)
        return ResultAsset(
            handle=handle,
            media_type=media_type,
            byte_length=byte_length,
        )

    def read(self, handle: str) -> ResultPayload:
        """Resolve one handle to bytes, or fail closed."""

        if not isinstance(handle, str) or not HANDLE_PATTERN.match(handle):
            # A malformed handle is indistinguishable from an unknown one.
            # Report it the same way so probing learns nothing.
            self._fail("RESULT_NOT_FOUND", "That result is not available.")
        with self._lock:
            entry = self._entries.get(handle)
            evicted = handle in self._evicted
        if entry is None:
            if evicted:
                self._fail(
                    "RESULT_GONE",
                    "That result is no longer retained.",
                )
            self._fail("RESULT_NOT_FOUND", "That result is not available.")
        path, media_type = entry
        # Re-verify containment on every read. Registration proved it once;
        # a symlink or reparse point swapped in afterwards must not escape.
        resolved = self._contained(path, code="RESULT_NOT_FOUND")
        try:
            content = resolved.read_bytes()
        except OSError:
            self._fail("RESULT_NOT_FOUND", "That result is not available.")
        if len(content) > MAX_RESULT_BYTES:
            self._fail("RESULT_TOO_LARGE", "That result is too large to deliver.")
        return ResultPayload(media_type=media_type, content=content)

    def forget(self, handle: str) -> bool:
        with self._lock:
            removed = self._entries.pop(handle, None) is not None
            if removed:
                self._remember_evicted(handle)
        return removed

    def clear(self) -> None:
        with self._lock:
            for handle in list(self._entries):
                self._remember_evicted(handle)
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _remember_evicted(self, handle: str) -> None:
        """Record an evicted handle so it reports gone rather than unknown."""

        self._evicted[handle] = None
        self._evicted.move_to_end(handle)
        while len(self._evicted) > self._max_entries * 4:
            self._evicted.popitem(last=False)

    def _contained(
        self,
        path: Path,
        *,
        code: str = "RESULT_OUTSIDE_ROOT",
    ) -> Path:
        if self._case_policy is CasePolicy.INCONCLUSIVE:
            # Case behaviour could not be established, so containment cannot
            # be decided safely. Fail closed rather than guess.
            self._fail(
                code,
                "Result-root case behaviour is inconclusive.",
            )
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError:
            self._fail(code, "That result is not available.")
        if not resolved.is_file():
            self._fail(code, "That result is not available.")
        if not self._within_root(resolved):
            self._fail(
                code,
                "That result is outside the owned result root.",
            )
        return resolved

    def _within_root(self, resolved: Path) -> bool:
        """Containment under the resolved case policy, never under os.name."""

        try:
            resolved.relative_to(self._root)
            return True
        except ValueError:
            pass
        if self._case_policy is not CasePolicy.CASE_INSENSITIVE:
            return False
        # On a case-insensitive volume two spellings name the same path, so a
        # case-differing prefix is genuine containment rather than an escape.
        root_parts = [part.casefold() for part in self._root.parts]
        candidate_parts = [part.casefold() for part in resolved.parts]
        return (
            len(candidate_parts) > len(root_parts)
            and candidate_parts[: len(root_parts)] == root_parts
        )

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise StudioError(StructuredError(code=code, message=message))
