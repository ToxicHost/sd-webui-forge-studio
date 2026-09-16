"""Filesystem boundary enforcement for the preflight orchestrator.

All tool-owned I/O goes through :class:`WorkspaceFS`. Paths are checked
lexically before any filesystem call, then each existing in-workspace path
segment is checked for a symlink or Windows reparse point before traversal.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable, NoReturn


class BoundaryViolation(RuntimeError):
    """Raised before an unapproved filesystem path is accessed."""

    def __init__(self, reason_code: str, report_path: str) -> None:
        self.reason_code = reason_code
        self.report_path = report_path
        super().__init__(f"{reason_code}: {report_path}")


@dataclass(frozen=True)
class AccessEvent:
    """Sanitized record of an attempted tool-owned filesystem operation."""

    operation: str
    path: str
    allowed: bool
    reason_code: str | None = None


class WorkspaceBoundary:
    """Authorize paths under app, Reference, and Evidence only."""

    _ALLOWED_ROOT_NAMES = ("app", "Reference", "Evidence")
    _WINDOWS_RESERVED_NAMES = {
        "aux",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"com{number}" for number in ("¹", "²", "³")),
        *(f"lpt{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in ("¹", "²", "³")),
    }

    def __init__(self, workspace_root: str | os.PathLike[str]) -> None:
        self.workspace_root = Path(
            os.path.normpath(os.path.abspath(os.fspath(workspace_root)))
        )
        self.allowed_roots = {
            name: self.workspace_root / name for name in self._ALLOWED_ROOT_NAMES
        }
        self.private_local_root = self.workspace_root / "Private-Local"
        self._events: list[AccessEvent] = []

    @property
    def events(self) -> tuple[AccessEvent, ...]:
        return tuple(self._events)

    def _lexical_absolute(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(os.fspath(path))
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return Path(os.path.normpath(os.path.abspath(os.fspath(candidate))))

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            common = os.path.commonpath(
                (os.path.normcase(os.fspath(candidate)), os.path.normcase(os.fspath(root)))
            )
        except ValueError:
            return False
        return common == os.path.normcase(os.fspath(root))

    def _safe_report_path(self, candidate: Path) -> str:
        if self._is_within(candidate, self.workspace_root):
            relative = os.path.relpath(candidate, self.workspace_root)
            return relative.replace("\\", "/")
        return "<outside-workspace>"

    @staticmethod
    def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
        if stat.S_ISLNK(path_stat.st_mode):
            return True
        attributes = getattr(path_stat, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        return bool(attributes & reparse_flag)

    def _check_existing_segments(self, candidate: Path, allowed_root: Path) -> None:
        """Reject links/junctions before traversing through them."""

        relative = candidate.relative_to(allowed_root)
        current = allowed_root
        segments: Iterable[str] = (os.fspath(part) for part in relative.parts)

        for segment in ("", *segments):
            if segment:
                current = current / segment
            try:
                current_stat = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError as exc:
                raise BoundaryViolation(
                    "WORKSPACE_PATH_UNREADABLE", self._safe_report_path(current)
                ) from exc
            if self._is_reparse_or_symlink(current_stat):
                raise BoundaryViolation(
                    "WORKSPACE_REPARSE_POINT_REJECTED",
                    self._safe_report_path(current),
                )
            if (
                os.path.normcase(os.fspath(current))
                == os.path.normcase(os.fspath(candidate))
                and stat.S_ISREG(current_stat.st_mode)
                and current_stat.st_nlink > 1
            ):
                raise BoundaryViolation(
                    "WORKSPACE_HARD_LINK_REJECTED",
                    self._safe_report_path(current),
                )

    def _reject(
        self,
        *,
        operation: str,
        report_path: str,
        reason_code: str,
    ) -> NoReturn:
        self._events.append(
            AccessEvent(
                operation=operation,
                path=report_path,
                allowed=False,
                reason_code=reason_code,
            )
        )
        raise BoundaryViolation(reason_code, report_path)

    def _authorize_lexically(
        self,
        path: str | os.PathLike[str],
        operation: str,
    ) -> tuple[Path, Path, str]:
        """Validate path spelling and scope without any target filesystem I/O."""

        raw_path = os.fspath(path)
        if raw_path.startswith(("\\\\", "//")):
            self._reject(
                operation=operation,
                report_path="<outside-workspace>",
                reason_code="FILESYSTEM_SPECIAL_PATH_REJECTED",
            )

        candidate = self._lexical_absolute(path)
        report_path = self._safe_report_path(candidate)

        if self._is_within(candidate, self.private_local_root):
            self._reject(
                operation=operation,
                report_path=report_path,
                reason_code="PRIVATE_LOCAL_OWNER_AUTHORIZATION_REQUIRED",
            )

        allowed_root = next(
            (
                root
                for root in self.allowed_roots.values()
                if self._is_within(candidate, root)
            ),
            None,
        )
        if allowed_root is None:
            self._reject(
                operation=operation,
                report_path=report_path,
                reason_code="FILESYSTEM_PATH_OUTSIDE_WORKSPACE_BOUNDARY",
            )

        relative = candidate.relative_to(allowed_root)
        for part in relative.parts:
            trimmed = part.rstrip(" .")
            device_stem = trimmed.split(".", 1)[0].casefold()
            if (
                ":" in part
                or trimmed != part
                or device_stem in self._WINDOWS_RESERVED_NAMES
            ):
                self._reject(
                    operation=operation,
                    report_path=report_path,
                    reason_code="FILESYSTEM_SPECIAL_PATH_REJECTED",
                )
        return candidate, allowed_root, report_path

    def authorize_lexically(
        self,
        path: str | os.PathLike[str],
        operation: str,
    ) -> Path:
        """Authorize scope and spelling without inspecting the target path."""

        candidate, _, report_path = self._authorize_lexically(path, operation)
        self._events.append(
            AccessEvent(operation=operation, path=report_path, allowed=True)
        )
        return candidate

    def authorize(
        self,
        path: str | os.PathLike[str],
        operation: str,
    ) -> Path:
        """Return a checked absolute path or reject it before target I/O."""

        candidate, allowed_root, report_path = self._authorize_lexically(
            path, operation
        )
        self._check_existing_segments(candidate, allowed_root)
        self._events.append(
            AccessEvent(operation=operation, path=report_path, allowed=True)
        )
        return candidate

    def report_path(self, path: str | os.PathLike[str]) -> str:
        """Return a sanitized relative path after boundary validation."""

        candidate = self.authorize(path, "report-path")
        return self._safe_report_path(candidate)


class WorkspaceFS:
    """Small I/O facade that applies :class:`WorkspaceBoundary` first."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def exists(self, path: str | os.PathLike[str]) -> bool:
        target = self.boundary.authorize(path, "exists")
        return target.exists()

    def is_file(self, path: str | os.PathLike[str]) -> bool:
        target = self.boundary.authorize(path, "is-file")
        return target.is_file()

    def is_dir(self, path: str | os.PathLike[str]) -> bool:
        target = self.boundary.authorize(path, "is-directory")
        return target.is_dir()

    def read_text(
        self,
        path: str | os.PathLike[str],
        encoding: str = "utf-8",
        *,
        max_chars: int | None = None,
    ) -> str:
        if max_chars is not None and (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or max_chars < 0
        ):
            raise ValueError("TEXT_SIZE_LIMIT_INVALID")
        target = self.boundary.authorize(path, "read-text")
        if max_chars is None:
            return target.read_text(encoding=encoding)
        with target.open("r", encoding=encoding) as source:
            content = source.read(max_chars + 1)
        if len(content) > max_chars:
            raise ValueError("TEXT_INPUT_EXCEEDS_SIZE_LIMIT")
        return content

    def read_bytes(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read exact binary content, optionally enforcing a pre-allocation bound."""

        if max_bytes is not None and (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 0
        ):
            raise ValueError("BINARY_SIZE_LIMIT_INVALID")

        target = self.boundary.authorize(path, "read-bytes")
        if max_bytes is None:
            return target.read_bytes()
        with target.open("rb") as source:
            content = source.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("BINARY_INPUT_EXCEEDS_SIZE_LIMIT")
        return content

    def read_json(
        self,
        path: str | os.PathLike[str],
        *,
        max_chars: int = 16_000_000,
    ) -> Any:
        try:
            content = self.read_text(path, max_chars=max_chars)
        except ValueError as exc:
            if str(exc) == "TEXT_INPUT_EXCEEDS_SIZE_LIMIT":
                raise ValueError("JSON_INPUT_EXCEEDS_SIZE_LIMIT") from exc
            raise

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, child in pairs:
                if key in value:
                    raise ValueError(f"JSON_DUPLICATE_KEY_REJECTED:{key}")
                value[key] = child
            return value

        def reject_nonfinite_constant(value: str) -> None:
            raise ValueError(f"JSON_NONFINITE_NUMBER_REJECTED:{value}")

        return json.loads(
            content,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite_constant,
        )

    def mkdir(self, path: str | os.PathLike[str]) -> Path:
        target = self.boundary.authorize(path, "mkdir")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(
        self,
        path: str | os.PathLike[str],
        content: str,
        encoding: str = "utf-8",
    ) -> Path:
        target = self.boundary.authorize(path, "write-text")
        self.mkdir(target.parent)
        target.write_text(content, encoding=encoding, newline="\n")
        return target

    def write_json(self, path: str | os.PathLike[str], value: Any) -> Path:
        content = json.dumps(
            value,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        return self.write_text(path, content)

    def create_text_exclusive(
        self,
        path: str | os.PathLike[str],
        content: str,
        encoding: str = "utf-8",
    ) -> Path:
        """Create a new file with O_EXCL; an existing target is never replaced."""

        payload = content.encode(encoding)
        target = self.boundary.authorize(path, "create-text-exclusive")
        parent = self.boundary.authorize(
            target.parent, "create-text-exclusive-parent"
        )
        self.mkdir(parent)
        target = self.boundary.authorize(target, "create-text-exclusive-recheck")
        self.boundary.authorize(parent, "create-text-exclusive-parent-recheck")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(target, flags, 0o600)
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(file_descriptor, remaining)
                if written <= 0:
                    raise OSError("EXCLUSIVE_FILE_WRITE_FAILED")
                remaining = remaining[written:]
        finally:
            os.close(file_descriptor)
        return target

    def create_json_exclusive(
        self,
        path: str | os.PathLike[str],
        value: Any,
    ) -> Path:
        """Serialize deterministic JSON into a newly created exclusive file."""

        content = json.dumps(
            value,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        return self.create_text_exclusive(path, content)

    def replace(
        self,
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
    ) -> Path:
        self.boundary.authorize_lexically(source, "replace-source-lexical")
        self.boundary.authorize_lexically(
            destination, "replace-destination-lexical"
        )
        checked_source = self.boundary.authorize(source, "replace-source")
        checked_destination = self.boundary.authorize(
            destination, "replace-destination"
        )
        self.boundary.authorize(checked_source.parent, "replace-source-parent")
        self.boundary.authorize(
            checked_destination.parent, "replace-destination-parent"
        )
        os.replace(checked_source, checked_destination)
        return checked_destination

    def list_children(self, path: str | os.PathLike[str]) -> tuple[Path, ...]:
        directory = self.boundary.authorize(path, "list-directory")
        children: list[Path] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                child = self.boundary.authorize(entry.path, "list-entry")
                children.append(child)
        return tuple(sorted(children, key=lambda item: item.name.casefold()))

    def find_child(
        self,
        directory: str | os.PathLike[str],
        *,
        prefix: str,
        suffix: str,
    ) -> Path:
        matches = [
            child
            for child in self.list_children(directory)
            if child.name.casefold().startswith(prefix.casefold())
            and child.name.casefold().endswith(suffix.casefold())
        ]
        if len(matches) != 1:
            location = self.boundary.report_path(directory)
            raise FileNotFoundError(
                f"Expected one {prefix}*{suffix} entry under {location}; "
                f"found {len(matches)}"
            )
        return matches[0]
