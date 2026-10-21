"""Where the owner's textual-inversion embeddings are, told to the engine. EMB.

THE DEFECT. Neo's CLIP text engines read embeddings from one folder,
`dynamic_args.embedding_dir`, when a model is built
(`backend/diffusion_engine/sd15.py:28`, `sdxl.py:39` / `:52`, `flux.py:37`
-> `backend/text_processing/classic_engine.py:58-60`). Only
`modules/sd_models.py:358` (`forge_model_reload`) sets it, from
`cmd_opts.embeddings_dir` (`backend/args.py:152` says so). Studio builds its
engine directly (`live_bindings.build_forge_engine` -> `forge_loader`) and
never calls `forge_model_reload`, so the folder was None: an embedding named
in a prompt was tokenised as plain words and did nothing, silently. Studio
also had no embeddings folder to point at, and `/studio/embeddings` answered
`[]`, so the prompt's autocomplete offered none.

WHAT THIS DOES. `model_roots` hands the configured embedding roots here
(no Neo import -- a registry built in a test must not drag the engine in).
The engine builder then (Neo imported):

- `apply_before_load()` sets `dynamic_args.embedding_dir` to the first root,
  so the text engines load it themselves, exactly as under Forge;
- `extend_engine(engine)` adds every further root to each CLIP text engine's
  own `EmbeddingDatabase` and reloads it (Neo takes one folder; Studio's
  roles take several, as LoRA's do);
- `refresh(engine)` reloads a database whose folder changed since it was read
  (Neo's own `DirWithTextualInversionEmbeddings.has_changed`, the top
  folder's mtime). Called at the start of a generation, on the generation's
  own thread, so a rescan never races a prompt being encoded -- and an
  embedding the owner just added is used without reloading the model.

Families without a CLIP encoder (Anima, Qwen, Lumina...) have no
`EmbeddingDatabase`; nothing here touches them.

ON1b. Embeddings from the owner's library folders arrive as FILES
(`set_files`), never as a folder: Neo's `load_from_dir` loads every file it
walks in full -- a checkpoint in a mixed folder included. Each is loaded with
Neo's own `load_from_file`, after the folders, and reloaded when one changes.
"""

from __future__ import annotations

import os
import re
from typing import Any

_ROOTS: tuple[str, ...] = ()
_FILES: tuple[str, ...] = ()

#: The CLIP text engines a diffusion engine may carry (`backend/
#: diffusion_engine/*.py`: sd15 `text_processing_engine`, sdxl / flux / mugen
#: `_l` and `_g`).
_TEXT_ENGINES = ("text_processing_engine", "text_processing_engine_l", "text_processing_engine_g")
_LISTED = (".safetensors", ".pt", ".bin")


def set_roots(roots: Any) -> None:
    """The configured embedding roots, in order (from `model_roots`)."""

    global _ROOTS
    _ROOTS = tuple(str(r) for r in (roots or ()) if str(r))


def roots() -> tuple[str, ...]:
    return _ROOTS


def set_files(files: Any) -> None:
    """The library's embedding files, in order (from `model_roots`)."""

    global _FILES
    _FILES = tuple(str(f) for f in (files or ()) if str(f))


def files() -> tuple[str, ...]:
    return _FILES


def _files_signature() -> tuple:
    signature = []
    for path in _FILES:
        try:
            signature.append((path, os.stat(path).st_mtime_ns))
        except OSError:
            continue
    return tuple(signature)


def _load_files(database: Any) -> None:
    """Neo's `load_from_dir` loop body, per library file."""

    for path in _FILES:
        try:
            if os.stat(path).st_size == 0:
                continue
            database.load_from_file(path, os.path.basename(path))
        except Exception:  # noqa: BLE001 - as Neo: one bad file is skipped
            continue
    database._studio_files = _files_signature()


def apply_before_load() -> None:
    """Point Neo at the first root before `forge_loader` builds the engine."""

    from backend.args import dynamic_args

    dynamic_args.embedding_dir = _ROOTS[0] if _ROOTS else None


def _databases(engine: Any):
    for name in _TEXT_ENGINES:
        database = getattr(getattr(engine, name, None), "embeddings", None)
        if database is not None and hasattr(database, "embedding_dirs"):
            yield database


def extend_engine(engine: Any) -> int:
    """Add the further roots to every CLIP text engine; returns how many
    databases were extended."""

    if len(_ROOTS) < 2 and not _FILES:
        return 0
    extended = 0
    for database in _databases(engine):
        for root in _ROOTS[1:]:
            if root not in database.embedding_dirs:
                database.add_embedding_dir(root)
        database.load_textual_inversion_embeddings()
        _load_files(database)
        extended += 1
    return extended


def refresh(engine: Any) -> int:
    """Reload every database with a changed folder; returns how many."""

    reloaded = 0
    for database in _databases(engine):
        try:
            changed = any(d.has_changed() for d in database.embedding_dirs.values())
        except OSError:
            changed = False
        if getattr(database, "_studio_files", ()) != _files_signature():
            changed = True
        if changed:
            database.load_textual_inversion_embeddings()
            _load_files(database)
            reloaded += 1
    return reloaded


def _natural(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def list_embeddings() -> list[dict]:
    """`[{name, file}]` for the prompt's autocomplete, the Extension's shape
    (`studio_api.py:5339`). Every root, walked as Neo walks it (`os.walk`);
    the name is the file's stem, which is what Neo registers. A name found
    again later replaces the earlier one, as Neo's `register_embedding_by_name`
    does. No paths."""

    seen: dict[str, dict] = {}
    for root in _ROOTS:
        if not os.path.isdir(root):
            continue
        for _base, _dirs, files in os.walk(root, followlinks=True):
            for filename in files:
                stem, ext = os.path.splitext(filename)
                if ext.lower() in _LISTED:
                    seen[stem] = {"name": stem, "file": filename}
    for path in _FILES:
        filename = os.path.basename(path)
        stem, ext = os.path.splitext(filename)
        if ext.lower() in _LISTED:
            seen[stem] = {"name": stem, "file": filename}
    return sorted(seen.values(), key=lambda e: _natural(e["name"]))


def list_catalogued(entries: Any) -> list[dict]:
    """The same list from Studio's cached catalogue scan, walking nothing.

    For an embedding folder on another machine: `list_embeddings` walks every
    root on the request thread with no timeout, and a share that stopped
    answering would hang the prompt's autocomplete. The catalogue scanned the
    same files under the folder's bound, and a name is the file's stem either
    way, so the answer is the same list.
    """

    seen: dict[str, dict] = {}
    for entry in entries or ():
        filename = str(getattr(entry, "relative_location", "") or "").rsplit("/", 1)[-1]
        stem, ext = os.path.splitext(filename)
        if stem and ext.lower() in _LISTED:
            seen[stem] = {"name": stem, "file": filename}
    return sorted(seen.values(), key=lambda e: _natural(e["name"]))


__all__ = ("set_roots", "roots", "set_files", "files", "apply_before_load", "extend_engine",
           "refresh", "list_embeddings", "list_catalogued")
