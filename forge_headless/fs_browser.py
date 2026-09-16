"""Directory navigation the client cannot forge its way through.

The client never sends a path except once, at `open_path`, and that one text
goes through the same admission a model root does. Everything after it is
navigation by NAME against a scan performed in the SAME request:

```text
open_path(text)        -> handle   the one text -> path entry point
descend(handle, name)  -> handle   name must be in this directory, now
ascend(handle)         -> handle   parent, re-admitted from scratch
listing(handle)                    bounded, non-recursive, revalidated
```

Descend-by-name is what makes a whole class of attacks not exist rather than
be defended against. `..`, `C:foo`, `\\\\?\\`, `%2f`, a decomposed spelling, a
reserved device name, a trailing dot -- none of them are names that a scandir
of this directory returned a moment ago, so one equality test refuses all of
them without a per-platform parser to get wrong.

**A handle is not a capability the client can mint.** It is a random token
naming a server-side record, so a fabricated path-shaped string carries no
authority. The record is revalidated on every use: the directory is re-stat'd
and its `(st_dev, st_ino)` compared, so a directory swapped for a link between
two requests is caught rather than followed.

Handles are purpose-scoped, generation-scoped and bounded. Purpose, so a
handle minted for choosing a model root cannot be replayed against a surface
added later. Generation, so changing the configuration invalidates every
outstanding handle at once. Bounded, so a client cannot make the table grow.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from .contracts import HeadlessError
from .fs_facts import DirFacts, EntryFacts, Filesystem, RealFilesystem
from .path_identity import name_is_acceptable, nfc
from .root_policy import (
    ROOT_DEVICE_NAMESPACE,
    ROOT_NETWORK_LOCATION,
    ROOT_NOT_A_DIRECTORY,
    ROOT_NOT_ABSOLUTE,
    ROOT_UNAVAILABLE,
    _refuse_by_syntax,
    _refuse_by_volume,
)

#: Entries returned in one page. Bounded so a directory of a hundred thousand
#: files answers, rather than timing out or exhausting memory.
MAX_PAGE_ENTRIES = 500

#: Outstanding handles. Bounded so navigation cannot grow the table without
#: limit; the oldest is evicted, which costs a stale client one re-open.
MAX_OPEN_HANDLES = 256

#: How long a handle stays usable without being touched.
HANDLE_TTL_SECONDS = 30 * 60

#: The only purpose in P0.4. Named rather than implied so a second surface has
#: to declare itself instead of inheriting this one's authority.
PURPOSE_MODEL_ROOT = "model_root"

BROWSE_HANDLE_UNKNOWN = "STUDIO_FS_HANDLE_UNKNOWN"
BROWSE_HANDLE_EXPIRED = "STUDIO_FS_HANDLE_EXPIRED"
BROWSE_HANDLE_WRONG_PURPOSE = "STUDIO_FS_HANDLE_WRONG_PURPOSE"
BROWSE_DIRECTORY_CHANGED = "STUDIO_FS_DIRECTORY_CHANGED"
BROWSE_ENTRY_UNKNOWN = "STUDIO_FS_ENTRY_UNKNOWN"
BROWSE_NOT_A_DIRECTORY = "STUDIO_FS_NOT_A_DIRECTORY"
BROWSE_NO_PARENT = "STUDIO_FS_NO_PARENT"
BROWSE_REDIRECT_REFUSED = "STUDIO_FS_REDIRECT_REFUSED"


@dataclass
class _Handle:
    """A server-owned record. The token names it; it names the directory."""

    token: str
    path: Path
    purpose: str
    generation: int
    fingerprint: tuple[int, int] | None
    touched: float


@dataclass(frozen=True)
class Listing:
    """One directory, bounded, with everything the page needs to render it."""

    handle: str
    path: str
    display_name: str
    breadcrumb: tuple[tuple[str, str], ...]
    entries: tuple[EntryFacts, ...]
    truncated: bool
    total_seen: int
    has_parent: bool
    #: Whether this directory could be USED for the handle's purpose. Folded
    #: into the listing rather than offered as a separate probe route, so
    #: navigation and admissibility come from the one resolution navigation
    #: already performed.
    usable: bool
    usable_reason: str | None = field(default=None)


def _refuse(code: str, message: str) -> HeadlessError:
    return HeadlessError(code, message)


class DirectoryBrowser:
    """Bounded, non-recursive navigation under server-owned handles."""

    def __init__(
        self,
        *,
        filesystem: Filesystem | None = None,
        clock=time.monotonic,
        max_entries: int = MAX_PAGE_ENTRIES,
    ) -> None:
        self._fs = filesystem or RealFilesystem()
        self._clock = clock
        self._max_entries = max_entries
        # The path FLAVOUR to parse with, taken from the filesystem rather
        # than from the host. `PurePath` on Windows is `PureWindowsPath`, and
        # `PureWindowsPath("/srv/models").is_absolute()` is False -- correctly,
        # because on Windows that path is drive-relative and depends on the
        # current drive, which is exactly what the absolute-path rule exists
        # to refuse.
        #
        # So a simulated POSIX filesystem has to be navigated under POSIX
        # rules, or the simulation tests the host's rules instead of the ones
        # it is standing in for. The real filesystem supplies nothing here and
        # gets the host flavour, unchanged.
        self._pure = getattr(self._fs, "pure_path", PurePath)
        self._handles: OrderedDict[str, _Handle] = OrderedDict()
        self._generation = 0

    # -- lifecycle ---------------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> None:
        """Drop every outstanding handle.

        Called when the configuration changes. A handle is authority over a
        directory, and authority granted under one configuration must not
        survive into the next -- otherwise disabling the browser, or
        repointing the roots, leaves live capability behind.
        """

        self._generation += 1
        self._handles.clear()

    # -- the one text entry point -----------------------------------------

    def open_path(self, text: str, *, purpose: str = PURPOSE_MODEL_ROOT) -> Listing:
        """Admit a path the owner typed, and return its listing.

        The ONLY place a client-supplied path becomes a location. It runs the
        same admission a model root does -- syntax first, volume second, both
        before any stat, and both again on the resolved answer -- because the
        hazard is identical: stat'ing a network location authenticates to a
        remote host and can hang.
        """

        candidate = (text or "").strip()
        if not candidate:
            raise _refuse(ROOT_NOT_ABSOLUTE, "No folder was supplied.")
        _refuse_by_syntax(candidate)
        supplied = self._pure(candidate)
        if not supplied.is_absolute():
            raise _refuse(ROOT_NOT_ABSOLUTE, "Use a full path.")
        _refuse_by_volume(supplied)

        try:
            # Parsed with the filesystem's own flavour, so a simulated
            # POSIX host round-trips as POSIX rather than through this box's
            # separator rules.
            resolved = self._fs.realpath(self._pure(candidate))
        except OSError:
            raise _refuse(ROOT_UNAVAILABLE, "That folder is not available.") from None

        return self._admit(resolved, purpose=purpose)

    # -- navigation --------------------------------------------------------

    def listing(self, token: str, *, purpose: str = PURPOSE_MODEL_ROOT) -> Listing:
        handle = self._resolve_handle(token, purpose)
        return self._render(handle)

    def descend(
        self, token: str, name: str, *, purpose: str = PURPOSE_MODEL_ROOT
    ) -> Listing:
        """Enter a child BY NAME, verified against a scan taken right now.

        The name is compared under NFC, because a macOS scan can return a
        decomposed spelling of the name the page was shown -- and refusing the
        owner's own folder because the bytes round-tripped differently would
        be a bug that only appears on one platform.
        """

        handle = self._resolve_handle(token, purpose)
        wanted = nfc((name or "").strip())
        if not wanted or "/" in wanted or "\\" in wanted:
            raise _refuse(BROWSE_ENTRY_UNKNOWN, "That folder is not here.")

        match = None
        for entry in self._fs.scan(handle.path):
            if nfc(entry.name) == wanted:
                match = entry
                break
        if match is None:
            raise _refuse(BROWSE_ENTRY_UNKNOWN, "That folder is not here.")
        if not match.is_dir:
            raise _refuse(BROWSE_NOT_A_DIRECTORY, "That is a file, not a folder.")
        if match.is_redirecting:
            # A link or reparse point. Refused rather than followed: entering
            # one is precisely how a caller reaches somewhere the admission
            # checks never saw, and the target may be on a volume this policy
            # refuses.
            raise _refuse(
                BROWSE_REDIRECT_REFUSED,
                "That folder is a shortcut, and shortcuts are not followed.",
            )

        # The real name, not the requested spelling: the child is opened under
        # what the filesystem actually calls it.
        return self._admit_child(handle, match.name, purpose=purpose)

    def ascend(self, token: str, *, purpose: str = PURPOSE_MODEL_ROOT) -> Listing:
        """Go to the parent, re-admitted from scratch.

        Not a climb: the parent goes through the same volume admission as a
        typed path, so walking up out of a local disk onto a network mount is
        refused at the boundary rather than at the point of use.
        """

        handle = self._resolve_handle(token, purpose)
        parent = handle.path.parent
        if parent == handle.path:
            raise _refuse(BROWSE_NO_PARENT, "That is already the top.")
        _refuse_by_volume(self._pure(parent))
        return self._admit(parent, purpose=purpose)

    # -- internals ---------------------------------------------------------

    def _admit_child(self, handle: _Handle, name: str, *, purpose: str) -> Listing:
        target = handle.path / name
        # Re-resolved, then required to still be a child of the directory we
        # were in. A link was already refused above; this catches the case
        # where the entry changed underneath between the scan and here.
        try:
            resolved = self._fs.realpath(target)
        except OSError:
            raise _refuse(ROOT_UNAVAILABLE, "That folder is not available.") from None
        if resolved.parent != handle.path and resolved != target:
            raise _refuse(
                BROWSE_REDIRECT_REFUSED,
                "That folder moved while it was being opened.",
            )
        return self._admit(resolved, purpose=purpose)

    def _admit(self, resolved: Path, *, purpose: str) -> Listing:
        """Every path that becomes a handle passes through here."""

        _refuse_by_syntax(str(resolved))
        _refuse_by_volume(self._pure(resolved))
        facts = self._fs.stat_dir(resolved)
        if not facts.exists:
            raise _refuse(ROOT_UNAVAILABLE, "That folder is not available.")
        if not facts.is_dir:
            raise _refuse(ROOT_NOT_A_DIRECTORY, "That is a file, not a folder.")

        handle = _Handle(
            token=secrets.token_urlsafe(24),
            path=resolved,
            purpose=purpose,
            generation=self._generation,
            fingerprint=facts.fingerprint,
            touched=self._clock(),
        )
        self._store(handle)
        return self._render(handle)

    def _store(self, handle: _Handle) -> None:
        self._handles[handle.token] = handle
        self._handles.move_to_end(handle.token)
        while len(self._handles) > MAX_OPEN_HANDLES:
            # Oldest first. A client that navigates past the bound loses its
            # earliest handle and re-opens; a client trying to grow the table
            # cannot.
            self._handles.popitem(last=False)

    def _resolve_handle(self, token: str, purpose: str) -> _Handle:
        handle = self._handles.get(str(token or ""))
        if handle is None:
            # Unknown and forged are the same answer on purpose: a different
            # message for "expired" than for "never existed" tells a caller
            # whether a token they guessed was ever real.
            raise _refuse(BROWSE_HANDLE_UNKNOWN, "Start browsing again.")
        if handle.generation != self._generation:
            self._handles.pop(handle.token, None)
            raise _refuse(BROWSE_HANDLE_EXPIRED, "Start browsing again.")
        now = self._clock()
        if now - handle.touched > HANDLE_TTL_SECONDS:
            self._handles.pop(handle.token, None)
            raise _refuse(BROWSE_HANDLE_EXPIRED, "Start browsing again.")
        if handle.purpose != purpose:
            raise _refuse(BROWSE_HANDLE_WRONG_PURPOSE, "Start browsing again.")

        # Revalidated at USE, not merely at mint. Between two requests the
        # directory can be replaced by a link to somewhere else entirely, and
        # a handle that trusted its own record would follow it.
        facts = self._fs.stat_dir(handle.path)
        if not facts.exists or not facts.is_dir:
            self._handles.pop(handle.token, None)
            raise _refuse(BROWSE_DIRECTORY_CHANGED, "That folder is gone.")
        if (
            handle.fingerprint is not None
            and facts.fingerprint is not None
            and facts.fingerprint != handle.fingerprint
        ):
            self._handles.pop(handle.token, None)
            raise _refuse(BROWSE_DIRECTORY_CHANGED, "That folder changed.")

        handle.touched = now
        self._handles.move_to_end(handle.token)
        return handle

    def _render(self, handle: _Handle) -> Listing:
        seen = 0
        kept: list[EntryFacts] = []
        truncated = False
        for entry in self._fs.scan(handle.path):
            seen += 1
            if not name_is_acceptable(entry.name):
                continue
            if len(kept) >= self._max_entries:
                truncated = True
                continue
            kept.append(entry)

        # Directories first, then by name. Case-folded for ordering only --
        # this decides presentation, never identity.
        kept.sort(key=lambda item: (not item.is_dir, nfc(item.name).lower()))

        usable, reason = self._usable(handle)
        return Listing(
            handle=handle.token,
            path=str(handle.path),
            display_name=handle.path.name or str(handle.path),
            breadcrumb=self._breadcrumb(handle.path),
            entries=tuple(kept),
            truncated=truncated,
            total_seen=seen,
            has_parent=handle.path.parent != handle.path,
            usable=usable,
            usable_reason=reason,
        )

    def _usable(self, handle: _Handle) -> tuple[bool, str | None]:
        """Could this directory be used for the handle's purpose?

        Answered from the resolution navigation already performed, rather than
        from a separate probe route -- a probe would mean a second
        `realpath` per navigated directory, on an unbounded thread pool, for
        an answer this call already has.
        """

        if handle.purpose != PURPOSE_MODEL_ROOT:
            return False, BROWSE_HANDLE_WRONG_PURPOSE
        if handle.path.parent == handle.path:
            # A whole drive or `/`. Navigable, so the owner can reach what is
            # inside it, but not choosable: a typo must not become a
            # filesystem-wide scan.
            return False, "HEADLESS_CATALOGUE_ROOT_IS_FILESYSTEM_ROOT"
        return True, None

    def _breadcrumb(self, path: Path) -> tuple[tuple[str, str], ...]:
        """(label, path) from the top down.

        Carries real paths, and is allowed to: this is the privileged Settings
        surface, and a picker that cannot say where it is is not a picker.
        Nothing else in the product may render one.
        """

        crumbs: list[tuple[str, str]] = []
        walked = path
        while True:
            label = walked.name or str(walked)
            crumbs.append((label, str(walked)))
            if walked.parent == walked:
                break
            walked = walked.parent
        return tuple(reversed(crumbs))


__all__ = (
    "BROWSE_DIRECTORY_CHANGED",
    "BROWSE_ENTRY_UNKNOWN",
    "BROWSE_HANDLE_EXPIRED",
    "BROWSE_HANDLE_UNKNOWN",
    "BROWSE_HANDLE_WRONG_PURPOSE",
    "BROWSE_NOT_A_DIRECTORY",
    "BROWSE_NO_PARENT",
    "BROWSE_REDIRECT_REFUSED",
    "HANDLE_TTL_SECONDS",
    "MAX_OPEN_HANDLES",
    "MAX_PAGE_ENTRIES",
    "PURPOSE_MODEL_ROOT",
    "DirectoryBrowser",
    "Listing",
)
