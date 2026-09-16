"""The only module that turns real operating-system objects into data.

Everything above this file decides; only this file looks. That split is what
makes three-OS behaviour provable on one machine: the browser is handed a
`Filesystem`, and a test can hand it a table of facts describing a macOS
volume or a container's overlay root without owning either.

It is also the containment seam. Every stat, scandir and realpath the browser
performs goes through this protocol, so "what did we actually touch" is a
question with one place to look.

```text
EntryFacts   one directory entry, as a scan saw it
DirFacts     one directory, as a real stat saw it
Filesystem   the four questions the browser is allowed to ask
```

Nothing here interprets. `is_redirecting` reports that an entry is a link or a
reparse point; whether that means "refuse" or "re-anchor" is a policy decision
and lives elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from .path_identity import is_redirecting, name_is_acceptable

#: How many entries a single scan will materialize before giving up. The
#: browser pages below this; the bound is here so a hostile or merely enormous
#: directory cannot exhaust memory even if a caller forgets to page.
MAX_SCAN_ENTRIES = 10_000


@dataclass(frozen=True)
class EntryFacts:
    """One directory entry, as a scan saw it. No path, deliberately.

    The NAME is what the browser needs -- navigation is by name, checked
    against a scan performed in the same request -- and a name cannot be
    turned into a location by a client that receives it.
    """

    name: str
    is_dir: bool
    is_redirecting: bool
    size_bytes: int = 0
    modified: float = 0.0
    #: False when the entry could not be classified at all. Treated as
    #: undescendable rather than skipped, so the owner can still SEE that
    #: something is there.
    readable: bool = True


@dataclass(frozen=True)
class DirFacts:
    """One directory, as a real `stat` saw it.

    ``fingerprint`` is ``(st_dev, st_ino)`` from a REAL stat, never from a
    cached `DirEntry`: on NTFS a cached entry stat reports ``0/0``, so a
    fingerprint taken from one would be identical for every directory on the
    volume and would authenticate nothing.

    None means the filesystem does not supply usable identity. That is a
    reason to re-admit a path from scratch on every use, not a reason to
    refuse it -- some filesystems genuinely have no stable inode.
    """

    exists: bool
    is_dir: bool
    fingerprint: tuple[int, int] | None = None


@runtime_checkable
class Filesystem(Protocol):
    """The four questions the browser may ask. Nothing else is reachable."""

    def stat_dir(self, path: Path) -> DirFacts:
        """Identity and kind of one directory, following links."""

    def scan(self, path: Path) -> Iterable[EntryFacts]:
        """One level of entries. NEVER recursive."""

    def realpath(self, path: Path) -> Path:
        """Resolve links and normalize. May raise OSError."""

    def home(self) -> Path | None:
        """The owner's home directory, if the host has a usable notion of one."""


class RealFilesystem:
    """The one implementation that touches the operating system."""

    def stat_dir(self, path: Path) -> DirFacts:
        try:
            info = os.stat(path)
        except OSError:
            return DirFacts(exists=False, is_dir=False)
        is_dir = os.path.isdir(path)
        # `st_ino == 0` is a filesystem that does not supply identity rather
        # than an error. Reported as absent identity so the caller re-admits
        # from scratch instead of trusting a fingerprint that means nothing.
        fingerprint = (
            (info.st_dev, info.st_ino)
            if info.st_dev or info.st_ino
            else None
        )
        return DirFacts(exists=True, is_dir=is_dir, fingerprint=fingerprint)

    def scan(self, path: Path) -> Iterable[EntryFacts]:
        found: list[EntryFacts] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(found) >= MAX_SCAN_ENTRIES:
                        break
                    found.append(self._entry(entry))
        except OSError:
            # An unlistable directory yields nothing. The caller distinguishes
            # "empty" from "refused" through `stat_dir`, which is the question
            # that can actually tell them apart.
            return ()
        return tuple(found)

    def _entry(self, entry: os.DirEntry[str]) -> EntryFacts:
        name = entry.name
        redirecting = is_redirecting(entry)
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            return EntryFacts(
                name=name,
                is_dir=False,
                is_redirecting=redirecting,
                readable=False,
            )
        size = 0
        modified = 0.0
        if not is_dir:
            try:
                info = entry.stat(follow_symlinks=False)
                size = int(info.st_size)
                modified = float(info.st_mtime)
            except OSError:
                # Listed without metadata rather than hidden: the owner is
                # choosing a FOLDER, and a file whose size cannot be read is
                # still evidence they are in the right place.
                pass
        return EntryFacts(
            name=name,
            is_dir=is_dir,
            is_redirecting=redirecting,
            size_bytes=size,
            modified=modified,
            readable=name_is_acceptable(name),
        )

    def realpath(self, path: Path) -> Path:
        return Path(os.path.realpath(path))

    def home(self) -> Path | None:
        try:
            resolved = Path(os.path.expanduser("~"))
        except (OSError, RuntimeError):
            return None
        if str(resolved) in ("~", ""):
            return None
        return resolved if resolved.is_absolute() else None


__all__ = (
    "MAX_SCAN_ENTRIES",
    "DirFacts",
    "EntryFacts",
    "Filesystem",
    "RealFilesystem",
)
