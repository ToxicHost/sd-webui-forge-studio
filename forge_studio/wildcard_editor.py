"""Changing wildcard files: the write half of the Wildcards module.

`wildcards.py` reads. This writes, and everything here touches an owner's real
files, so the rules are stricter than on the reading side and are mostly about
one question: is this path still inside the folder the owner chose.

**Containment is resolved, never spelled.** `relative_to` is lexical, and a
directory junction inside the root is relative to it by SPELLING while pointing
anywhere on the disk. That was proven on the read side of this project -- a
junction in a real wildcard folder read a file outside it into a prompt -- and
the write side has the same hole with worse consequences, because it would
CREATE files out there. Every target is resolved and checked with
`is_relative_to` against a resolved root, exactly as `WildcardLibrary.load`
does, and the check happens AFTER any directory is created rather than before,
which closes the window the donor leaves open between deciding and writing.

**Only `.txt`, and only names an operating system will give back.** The donor
validates neither: `CON`, `COM1`, `a<b`, `a.txt:ads` (an NTFS alternate data
stream), trailing dots and 300-character names all pass its check. A name that
cannot be reopened is a file the owner has lost.

**Saving is atomic.** The donor writes straight over the target, so a crash or
a full disk truncates a word list to nothing. This writes a temporary beside it
and replaces, the same shape `preferences.py` already uses.

**Deleting goes to the operating system's bin**, not to oblivion and not to
Studio's Gallery trash -- that one exists to power Ctrl+Z on images and this
module has no undo stack to feed. Owner decision: wildcard files are the
platform's to hold, and an owner who deletes one should find it where they
find everything else they delete.

**A refusal raises.** It does not return `{"ok": False}` with a 200, because
the adapter renders a returned body at HTTPStatus.OK and the page then toasts a
refusal in green -- which this project has already shipped once, for layouts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .wildcards import MAX_FILE_BYTES, WILDCARD_SUFFIX

#: Names Windows will not give back, whatever the extension. Checked against
#: the STEM, because `CON.txt` is as unopenable as `CON`.
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)

#: Deliberately narrow. Letters, digits, space, and the three punctuation marks
#: a wildcard library actually uses. Everything else -- `:` opening an
#: alternate data stream, `<` and `|` and `?` and `*`, control characters, and
#: the path separators -- is refused rather than substituted, so the owner is
#: told rather than surprised by a name they did not choose.
_ALLOWED_NAME = re.compile(r"^[A-Za-z0-9 _.\-]+$")

#: One folder should not be able to hide a filesystem inside it.
MAX_NAME_LENGTH = 120
MAX_TREE_ENTRIES = 20_000


class EditorRefused(Exception):
    """A write Studio will not perform, with a reason for the owner."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        #: Additional keys the page reads. `deleteItem` needs `not_empty` and
        #: `file_count` to offer its force-delete confirmation, so a refusal
        #: carrying only a message would make a non-empty folder undeletable
        #: rather than merely guarded.
        self.extra = extra


@dataclass(frozen=True)
class _Target:
    """A validated place on disk, and the relative path that named it."""

    path: Path
    relative: str


def _clean_name(name: str, *, kind: str = "file") -> str:
    """One path SEGMENT the owner typed, or a refusal explaining why not."""

    cleaned = str(name or "").strip()
    if not cleaned:
        raise EditorRefused(f"A {kind} needs a name.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise EditorRefused(
            f"That name is too long -- {MAX_NAME_LENGTH} characters at most.")
    if "/" in cleaned or "\\" in cleaned:
        raise EditorRefused("A name cannot contain a path separator.")
    if not _ALLOWED_NAME.match(cleaned):
        raise EditorRefused(
            "A name may use letters, digits, spaces, dots, dashes and "
            "underscores.")
    # A trailing DOT is silently dropped by Windows, so `moods.` would become
    # `moods` on disk and the owner would have a file they did not name.
    # Surrounding whitespace is different: it is almost always a typo, it was
    # already stripped above, and refusing it would be pedantry rather than
    # protection.
    if cleaned.endswith("."):
        raise EditorRefused("A name cannot end with a dot.")
    stem = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    if stem.lower() in _RESERVED:
        raise EditorRefused(f"{cleaned!r} is a name the system reserves.")
    return cleaned


class WildcardEditor:
    """Create, read, rename and delete files under ONE folder.

    Holds no state beyond the root resolver, so a folder the owner changes in
    Settings is picked up on the next call rather than cached into staleness.
    """

    def __init__(self, root: Path | None) -> None:
        self._root = Path(root) if root else None

    # -- containment -------------------------------------------------------

    def _real_root(self) -> Path:
        if self._root is None:
            raise EditorRefused("No wildcard folder is chosen.")
        try:
            return self._root.resolve(strict=True)
        except OSError as error:
            raise EditorRefused("The wildcard folder is not reachable.") from error

    def _resolve(self, relative: str, *, must_exist: bool = True) -> _Target:
        """Turn a caller's relative path into a place inside the root.

        The caller is the browser, so nothing it sends is trusted: the path is
        rebuilt from validated segments rather than accepted and checked, which
        means a drive letter or a UNC prefix cannot survive the trip.
        """

        real_root = self._real_root()
        raw = str(relative or "").replace("\\", "/")
        segments = [part for part in raw.split("/") if part]
        if any(part in (".", "..") for part in segments):
            raise EditorRefused("That path is not inside the wildcard folder.")
        for part in segments:
            _clean_name(part, kind="folder")

        target = real_root.joinpath(*segments) if segments else real_root
        try:
            resolved = target.resolve(strict=must_exist)
        except OSError as error:
            raise EditorRefused("There is nothing there.") from error
        # THE GATE. Resolved on both sides, and `is_relative_to` rather than a
        # string prefix -- `startswith` lets a sibling folder whose name merely
        # extends the root's pass, which is the hole the donor's live routes
        # still have.
        if resolved != real_root and not resolved.is_relative_to(real_root):
            raise EditorRefused("That path is not inside the wildcard folder.")
        return _Target(resolved, "/".join(segments))

    # -- reading -----------------------------------------------------------

    def tree(self) -> dict[str, Any]:
        """The whole folder, as one recursive node.

        Shape is the page's, not this module's preference: the root is itself a
        `folder` node with an empty path, folders carry `children`, and files
        carry a `lines` count that renders as the grey badge.
        """

        real_root = self._real_root()
        budget = [MAX_TREE_ENTRIES]

        def walk(directory: Path, relative: str) -> dict[str, Any]:
            children: list[dict[str, Any]] = []
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda item: (item.is_file(), item.name.lower()))
            except OSError:
                entries = []
            for entry in entries:
                if budget[0] <= 0:
                    break
                child_relative = f"{relative}/{entry.name}" if relative else entry.name
                try:
                    resolved = entry.resolve(strict=True)
                    if not resolved.is_relative_to(real_root):
                        # A junction pointing out of the root. Skipped rather
                        # than shown: listing it would offer the owner a file
                        # every write here would then refuse.
                        continue
                    if entry.is_dir():
                        budget[0] -= 1
                        children.append(walk(entry, child_relative))
                    elif entry.suffix.lower() == WILDCARD_SUFFIX:
                        budget[0] -= 1
                        children.append({
                            "type": "file",
                            "name": entry.name,
                            "path": child_relative,
                            "lines": _count_lines(entry),
                        })
                except OSError:
                    continue
            return {"type": "folder", "name": directory.name,
                    "path": relative, "children": children}

        node = walk(real_root, "")
        node["path"] = ""
        return node

    def read(self, relative: str) -> dict[str, Any]:
        target = self._resolve(relative)
        if not target.path.is_file():
            raise EditorRefused("There is no wildcard file there.")
        try:
            content = target.path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            raise EditorRefused("That file could not be read.") from error
        return {"path": target.relative, "content": content,
                "lines": len(content.splitlines()), "size": len(content)}

    # -- writing -----------------------------------------------------------

    def save(self, relative: str, content: str) -> dict[str, Any]:
        target = self._resolve(relative)
        if not target.path.is_file():
            raise EditorRefused("There is no wildcard file there.")
        body = str(content or "")
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise EditorRefused("That file is too large to save.")
        _atomic_write(target.path, body)
        return {"ok": True, "path": target.relative,
                "lines": len(body.splitlines()), "size": len(body)}

    def create_file(self, parent: str, name: str) -> dict[str, Any]:
        clean = _clean_name(name)
        if not clean.lower().endswith(WILDCARD_SUFFIX):
            clean += WILDCARD_SUFFIX
        holder = self._resolve(parent)
        if not holder.path.is_dir():
            raise EditorRefused("There is no folder there.")
        target = self._resolve(
            f"{holder.relative}/{clean}" if holder.relative else clean,
            must_exist=False)
        if target.path.exists():
            # The page string-matches this phrase to let an OS file-drop skip a
            # file it already has, so the wording is load-bearing.
            raise EditorRefused(f"{clean!r} already exists.")
        _atomic_write(target.path, "")
        return {"ok": True, "path": target.relative}

    def create_folder(self, parent: str, name: str) -> dict[str, Any]:
        clean = _clean_name(name, kind="folder")
        holder = self._resolve(parent)
        if not holder.path.is_dir():
            raise EditorRefused("There is no folder there.")
        target = self._resolve(
            f"{holder.relative}/{clean}" if holder.relative else clean,
            must_exist=False)
        if target.path.exists():
            raise EditorRefused(f"{clean!r} already exists.")
        try:
            target.path.mkdir(parents=False)
        except OSError as error:
            raise EditorRefused("That folder could not be created.") from error
        # Re-checked AFTER creation. Between deciding and making, the shape of
        # the disk can change.
        self._resolve(target.relative)
        return {"ok": True, "path": target.relative}

    def rename(self, relative: str, new_name: str) -> dict[str, Any]:
        """One route for files and folders both -- the page does not say which."""

        target = self._resolve(relative)
        if target.path == self._real_root():
            raise EditorRefused("The wildcard folder itself cannot be renamed.")
        clean = _clean_name(
            new_name, kind="folder" if target.path.is_dir() else "file")
        if target.path.is_file() and not clean.lower().endswith(WILDCARD_SUFFIX):
            clean += WILDCARD_SUFFIX
        parent_relative = "/".join(target.relative.split("/")[:-1])
        destination = self._resolve(
            f"{parent_relative}/{clean}" if parent_relative else clean,
            must_exist=False)
        if destination.path.exists():
            raise EditorRefused(f"{clean!r} already exists.")
        try:
            target.path.rename(destination.path)
        except OSError as error:
            raise EditorRefused("That could not be renamed.") from error
        return {"ok": True, "new_path": destination.relative}

    def duplicate(self, relative: str) -> dict[str, Any]:
        """P7. The Extension's `file/duplicate` (`studio_lexicon.py:399-425`):
        `<stem>_copy.txt`, then `_copy2`, `_copy3`... beside the original."""

        import shutil

        target = self._resolve(relative)
        if not target.path.is_file():
            raise EditorRefused("File not found")
        if target.path.suffix.lower() != WILDCARD_SUFFIX:
            raise EditorRefused("Only .txt files")
        stem = target.path.name[: -len(WILDCARD_SUFFIX)]
        parent = "/".join(target.relative.split("/")[:-1])
        counter = 1
        while True:
            name = f"{stem}_copy{WILDCARD_SUFFIX}" if counter == 1 \
                else f"{stem}_copy{counter}{WILDCARD_SUFFIX}"
            copy = self._resolve(f"{parent}/{name}" if parent else name, must_exist=False)
            if not copy.path.exists():
                break
            counter += 1
        try:
            shutil.copy2(target.path, copy.path)
        except OSError as error:
            raise EditorRefused(f"Duplicate error: {error.strerror or error}") from error
        return {"ok": True, "path": copy.relative}

    def move(self, relative: str, destination: str) -> dict[str, Any]:
        """P7. The Extension's `file/move` (`studio_lexicon.py:431-483`): a file
        or folder into another folder ("" is the root). Refuses moving a folder
        into itself and overwriting; the same place is a no-op."""

        import shutil

        source = self._resolve(relative)
        if source.path == self._real_root():
            raise EditorRefused("The wildcard folder itself cannot be moved.")
        holder = self._resolve(destination)
        if not holder.path.is_dir():
            raise EditorRefused("Destination is not a folder")
        if source.path.is_dir() and (holder.path == source.path
                                     or holder.path.is_relative_to(source.path)):
            raise EditorRefused("Cannot move a folder into itself")
        if source.path.parent == holder.path:
            return {"ok": True, "new_path": source.relative}
        name = source.path.name
        moved = self._resolve(f"{holder.relative}/{name}" if holder.relative else name,
                              must_exist=False)
        if moved.path.exists():
            raise EditorRefused(f'"{name}" already exists in destination')
        try:
            shutil.move(str(source.path), str(moved.path))
        except OSError as error:
            raise EditorRefused(f"Move error: {error.strerror or error}") from error
        return {"ok": True, "new_path": moved.relative}

    def search_content(self, query: str) -> list[dict[str, Any]]:
        """P7. The Extension's `search_content` (`studio_lexicon.py:522-550`):
        files whose text contains `query` (2+ characters), one hit per file,
        at most 100, the matching line trimmed to 120 characters."""

        needle = str(query or "").lower()
        if len(needle) < 2:
            return []
        real_root = self._real_root()
        results: list[dict[str, Any]] = []
        for path in sorted(real_root.rglob(f"*{WILDCARD_SUFFIX}")):
            try:
                if not path.is_file() or not path.resolve().is_relative_to(real_root):
                    continue
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    results.append({"name": path.name,
                                    "path": path.relative_to(real_root).as_posix(),
                                    "line": number, "text": line.strip()[:120]})
                    break
            if len(results) >= 100:
                break
        return results

    def delete(self, relative: str, *, force: bool = False) -> dict[str, Any]:
        """To the operating system's bin, never straight to nothing."""

        from .gallery_actions import RECYCLED, _discard

        target = self._resolve(relative)
        if target.path == self._real_root():
            raise EditorRefused("The wildcard folder itself cannot be deleted.")
        if target.path.is_dir():
            contained = [
                item for item in target.path.rglob(f"*{WILDCARD_SUFFIX}")
                if item.is_file()
            ]
            if contained and not force:
                # The page reads BOTH extra keys to offer "delete anyway", so a
                # bare message would make a non-empty folder undeletable rather
                # than guarded.
                raise EditorRefused(
                    f"That folder holds {len(contained)} wildcard files.",
                    not_empty=True, file_count=len(contained))
        outcome = _discard(target.path)
        return {"ok": True, "path": target.relative, "disposal": outcome,
                "recycled": outcome == RECYCLED}


def _count_lines(path: Path) -> int:
    """Entries in a wildcard file, ignoring blanks and comments.

    The same reading `WildcardLibrary.lines` uses, so the badge in the tree and
    the choices the sampler draws from cannot disagree.
    """

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return 0
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines()
               if line.strip() and not line.lstrip().startswith("#"))


def _atomic_write(path: Path, body: str) -> None:
    """Temporary beside the target, then replace.

    `replace` is atomic on POSIX and on Windows, so a reader either sees the
    old file or the new one. Writing in place risks a truncated word list on a
    crash or a full disk -- which is what the donor does.
    """

    temporary = path.with_name(f"{path.name}.studio-tmp")
    try:
        temporary.write_text(body, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise EditorRefused("That file could not be written.") from error


__all__ = ("EditorRefused", "MAX_NAME_LENGTH", "WildcardEditor")
