"""Model library folders: any number, scanned through, sorted by content. ON1b.

Source review: Evidence/source-review/ON0-onboarding.md. The owner's decision
(2026-10-21): model folders are a LIST, and each file is filed by what it IS
(`model_kind.classify_file`), not by which box its folder was typed into. An
owner who keeps LoRAs, checkpoints and wildcards in separate folders adds
each one; an owner with one big folder adds that.

A library folder is served as one `ModelCatalogue` PER ROLE over the same
directory, each keeping only the files of its own kind. Everything that reads
a role -- the dropdowns, `resolve`, the id digest -- is unchanged, and a file
keeps the id it had in a role folder (role, resolved root and relative
location are the same).

The eight role folders convert ONCE into list entries carrying the role as a
HINT. A hint decides only what the content cannot: a pickle file is never
opened, so `old_style.pt` in what was the LoRA folder stays a LoRA.

Studio never writes inside a library folder. What a scan learned is kept in
Studio's own state folder (`KindCache`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .catalogue import MODEL_ROLES
from .contracts import HeadlessError
from .model_kind import KIND_UNKNOWN, Classification, classify_file, folder_hint

#: Config key holding the folder list.
LIBRARY_KEY = "model_library"
#: Config key holding the owner's corrections: absolute path -> kind.
CORRECTIONS_KEY = "model_library_corrections"

#: How many library folders one Studio enumerates. An implementation limit,
#: like `model_roots.MAX_ROOTS_PER_ROLE`, with its own refusal code.
MAX_LIBRARY_FOLDERS = 32
LIBRARY_TOO_MANY = "HEADLESS_MODEL_LIBRARY_TOO_MANY"
LIBRARY_MALFORMED = "HEADLESS_MODEL_LIBRARY_MALFORMED"

#: Subfolders a library scan does not enter (case-insensitive). A library
#: folder may be a whole Forge or A1111 `models` folder: these hold
#: preprocessor annotators, face restorers, interrogators and Hugging Face
#: caches, none of which is a model the owner picks, and every one of which
#: would otherwise fill the Unrecognized list.
SKIPPED_DIRECTORIES = frozenset({
    "controlnetpreprocessor", "annotator", "annotators", "diffusers", "insightface",
    "codeformer", "gfpgan", "facerestore_models", "blip", "deepbooru",
    "torch_deepdanbooru", "clip_interrogator", "interrogate", "ldsr", "vae-approx",
    "karlo", "sam", "huggingface", "hub", ".cache", ".git", "__pycache__", ".trash",
})


@dataclass(frozen=True)
class LibraryFolder:
    """One folder in the owner's list."""

    path: str
    #: A role, or "": what this folder held when it was a role folder.
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"path": self.path}
        if self.hint:
            payload["hint"] = self.hint
        return payload


def normalize_library(raw: object) -> tuple[LibraryFolder, ...]:
    """Validate a ``model_library`` list. No filesystem access.

    Each entry is a path, or ``{"path": ..., "hint": role}``. Textual
    duplicates are dropped (the first keeps its hint); two spellings of one
    folder are caught after resolution, as for role folders.
    """

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise HeadlessError(LIBRARY_MALFORMED, "model_library must be a list of folders.")
    folders: list[LibraryFolder] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            text, hint = entry, ""
        elif isinstance(entry, dict):
            text, hint = entry.get("path"), entry.get("hint") or ""
            if not isinstance(text, str) or not isinstance(hint, str):
                raise HeadlessError(LIBRARY_MALFORMED, "A model_library entry needs a path.")
        else:
            raise HeadlessError(LIBRARY_MALFORMED, "model_library must contain only folders.")
        text = text.strip()
        hint = hint.strip()
        if hint and hint not in MODEL_ROLES:
            raise HeadlessError(LIBRARY_MALFORMED, f"Unknown model role hint: {hint}.")
        if not text or text in seen:
            continue
        seen.add(text)
        folders.append(LibraryFolder(text, hint))
    if len(folders) > MAX_LIBRARY_FOLDERS:
        raise HeadlessError(LIBRARY_TOO_MANY,
                            f"The model library accepts at most {MAX_LIBRARY_FOLDERS} folders.")
    return tuple(folders)


def storable_library(folders: tuple[LibraryFolder, ...]) -> list[object]:
    """The config shape: a bare path when there is no hint."""

    return [f.to_dict() if f.hint else f.path for f in folders]


def library_from_role_folders(roots: dict[str, tuple[str, ...]]) -> tuple[LibraryFolder, ...]:
    """The one-time conversion of the eight role folders into list entries.

    In `MODEL_ROLES` order, so a folder named for two roles keeps the first
    one's hint. Never touches the disk.
    """

    folders: list[LibraryFolder] = []
    seen: set[str] = set()
    for role in MODEL_ROLES:
        for text in roots.get(role, ()) or ():
            text = str(text).strip()
            if text and text not in seen:
                seen.add(text)
                folders.append(LibraryFolder(text, role))
    return tuple(folders[:MAX_LIBRARY_FOLDERS])


def install_models_folder(path: os.PathLike[str] | str) -> tuple[str, str] | None:
    """When a folder the owner PICKED is a Forge / A1111 or ComfyUI install
    root, ``(kind, its models folder)``; otherwise None. ON2.

    Owner decision 2: Studio never looks for installs by itself. This looks
    only inside the one folder the owner chose (and its `webui` subfolder,
    where Forge's one-click package keeps the install), at a handful of
    names. Scanning a whole install would walk its venv and repositories.
    """

    base = Path(path)
    for root in (base, base / "webui"):
        models = root / "models"
        try:
            if not models.is_dir():
                continue
            if (models / "checkpoints").is_dir() and ((root / "comfy").is_dir() or (root / "main.py").is_file()):
                return "comfyui", str(models)
            if (models / "Stable-diffusion").is_dir() and any(
                (root / marker).exists() for marker in ("webui.py", "launch.py", "webui-user.bat", "modules")
            ):
                return "forge", str(models)
        except OSError:
            continue
    return None


def file_hint(relative: str, entry_hint: str, root_name: str = "") -> str:
    """The hint for one file: its own subfolders' names first (innermost
    wins), then what the library entry was, then the library folder's name."""

    parents = tuple(relative.split("/")[:-1])
    return folder_hint(parents) or entry_hint or folder_hint((root_name,))


class KindCache:
    """Every library file's kind, remembered by (size, mtime) per path.

    Classification reads a header, and a checkpoint header is the biggest
    one; a library of a few hundred files is read once, and a rescan reads
    only what changed. Persisted to ``path`` -- inside Studio's state folder,
    never beside the models -- so a restart does not read them all again.

    ``overrides`` are the owner's corrections from the Unrecognized list,
    keyed like the cache. They win over any classification.
    """

    VERSION = 1
    MAX_REMEMBERED = 50_000

    def __init__(self, path: os.PathLike[str] | str | None = None, *,
                 classify: Callable[..., Classification] = classify_file) -> None:
        self._path = Path(path) if path is not None else None
        self._classify = classify
        self._lock = Lock()
        self._entries: dict[str, tuple[int, int, str, Classification]] = {}
        self._overrides: dict[str, str] = {}
        self._dirty = False
        self._load()

    @staticmethod
    def key(path: Path | str) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def skips(self, directory_name: str) -> bool:
        return directory_name.strip().lower() in SKIPPED_DIRECTORIES

    def set_overrides(self, overrides: dict[str, str]) -> None:
        with self._lock:
            self._overrides = {self.key(p): k for p, k in (overrides or {}).items()
                               if k in MODEL_ROLES or k == KIND_UNKNOWN}

    def corrected_under(self, root: Path | str) -> int:
        """How many owner corrections name a file inside ``root``."""

        prefix = self.key(root).rstrip("\\/") + os.sep
        with self._lock:
            return sum(1 for key in self._overrides if key.startswith(prefix))

    def kind_of(self, path: Path, *, relative: str = "", hint: str = "",
                size: int, mtime_ns: int, root_name: str = "") -> Classification:
        key = self.key(path)
        hint = file_hint(relative or Path(path).name, hint, root_name)
        with self._lock:
            corrected = self._overrides.get(key)
            if corrected:
                return Classification(corrected, "", "high", "corrected by the owner")
            hit = self._entries.get(key)
            if hit is not None and hit[:3] == (size, mtime_ns, hint):
                return hit[3]
        # Outside the lock: a checkpoint header read must not stall the
        # other roles' scans of the same folder.
        result = self._classify(Path(path), hint=hint)
        with self._lock:
            if len(self._entries) < self.MAX_REMEMBERED or key in self._entries:
                self._entries[key] = (size, mtime_ns, hint, result)
                self._dirty = True
        return result

    def save(self) -> bool:
        """Write what changed. Never raises: a cache is not worth a failure."""

        if self._path is None:
            return False
        with self._lock:
            if not self._dirty:
                return False
            document = {
                "version": self.VERSION,
                "entries": {
                    key: [size, mtime, hint, c.kind, c.family, c.confidence, c.evidence, c.needs]
                    for key, (size, mtime, hint, c) in self._entries.items()
                },
            }
            self._dirty = False
        try:
            from .model_roots import write_config_atomically

            write_config_atomically(self._path, document)
            return True
        except Exception:  # noqa: BLE001
            with self._lock:
                self._dirty = True
            return False

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(document, dict) or document.get("version") != self.VERSION:
            return
        for key, row in (document.get("entries") or {}).items():
            try:
                size, mtime, hint, kind, family, confidence, evidence, needs = row
                self._entries[str(key)] = (int(size), int(mtime), str(hint), Classification(
                    str(kind), str(family), str(confidence), str(evidence), dict(needs or {})))
            except (TypeError, ValueError):
                continue


def unrecognized_files(root: Path, cache: KindCache, *, hint: str = "",
                       limit: int = 500) -> list[dict[str, Any]]:
    """The model files under one library folder that no role took.

    For the Settings page's Unrecognized list, so the owner can correct them.
    Walks with the catalogue's rules (no links, the skipped folders), and
    reads only what the cache does not already know.
    """

    from .catalogue import _is_reparse_point
    from .model_kind import MODEL_EXTENSIONS

    found: list[dict[str, Any]] = []
    stack = [(Path(root), "")]
    while stack and len(found) < limit:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if _is_reparse_point(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not cache.skips(entry.name):
                        stack.append((Path(entry.path), f"{prefix}{entry.name}/"))
                    continue
                if Path(entry.name).suffix.lower() not in MODEL_EXTENSIONS:
                    continue
                facts = entry.stat(follow_symlinks=False)
            except (OSError, AttributeError):
                continue
            relative = f"{prefix}{entry.name}"
            result = cache.kind_of(Path(entry.path), relative=relative, hint=hint,
                                   size=int(facts.st_size), mtime_ns=int(facts.st_mtime_ns),
                                   root_name=Path(root).name)
            if result.kind == KIND_UNKNOWN:
                found.append({"relative": relative, "evidence": result.evidence,
                              "size_bytes": int(facts.st_size)})
                if len(found) >= limit:
                    break
    found.sort(key=lambda item: item["relative"].lower())
    return found


__all__ = (
    "CORRECTIONS_KEY", "KindCache", "LIBRARY_KEY", "LIBRARY_MALFORMED", "LIBRARY_TOO_MANY", "LibraryFolder",
    "MAX_LIBRARY_FOLDERS", "SKIPPED_DIRECTORIES", "file_hint", "install_models_folder", "library_from_role_folders",
    "normalize_library", "storable_library", "unrecognized_files",
)
