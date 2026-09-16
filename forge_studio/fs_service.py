"""The Studio seam for the directory picker, and the only place a path is sent.

Every other Studio response is opaque by construction: a model is three
catalogue ids, a result is a handle, a lifecycle snapshot carries a
`profile_id` and nothing that could name a file. This module is the declared
exception, because a folder picker that cannot say which folder it is showing
is not a picker.

The exception is scoped, not general:

```text
Settings filesystem browser   real paths permitted, here, gated
model catalogue               opaque ids
generation request            opaque ids
load seam                     server-resolved private references
```

Which is why the shaping lives in ONE module. "Where can a path reach a
response body" has one answer, and a test can assert it.

Three controls beyond the route gates:

```text
enabled       an emergency switch, default ON. Not a substitute for
              building the picker safely -- a flag that is the safety
              measure is a feature nobody finished.
concurrency   a bounded number of concurrent walks, so a page that opens
              forty directories cannot occupy every handler thread.
generation    bumped when the configuration changes, which invalidates
              every outstanding handle at once.
```
"""

from __future__ import annotations

import string
import threading
from pathlib import Path
from typing import Any

from forge_headless.contracts import HeadlessError
from forge_headless.fs_browser import (
    MAX_OPEN_HANDLES,
    MAX_PAGE_ENTRIES,
    PURPOSE_MODEL_ROOT,
    DirectoryBrowser,
    Listing,
)

#: Concurrent browse operations. A directory walk is bounded but not free, and
#: the server is a ThreadingHTTPServer with no other admission control.
BROWSE_CONCURRENCY = 4

#: Refused when that bound is reached. A 503 rather than a queue: the owner is
#: clicking through folders, and a slow answer is worse than "try again".
BROWSE_BUSY = "STUDIO_FS_BUSY"
BROWSE_DISABLED = "STUDIO_FS_DISABLED"


def _max_roots_per_role() -> int:
    from forge_headless.model_roots import MAX_ROOTS_PER_ROLE

    return MAX_ROOTS_PER_ROLE

#: How many volume roots `places` will offer. Bounded so the answer cannot
#: become an inventory of the host.
MAX_PLACE_VOLUMES = 32


class FilesystemService:
    """Purpose-scoped directory browsing for the Settings picker."""

    def __init__(
        self,
        *,
        registry=None,
        browser: DirectoryBrowser | None = None,
        enabled: bool = True,
        native=None,
    ) -> None:
        self._registry = registry
        self._browser = browser or DirectoryBrowser()
        self._enabled = bool(enabled)
        # Constructed here rather than injected everywhere, but injectable, so
        # no test starts a real file-manager process.
        if native is None:
            from forge_studio.native_actions import NativeActions

            native = NativeActions()
        self._native = native
        self._slots = threading.BoundedSemaphore(BROWSE_CONCURRENCY)

    # -- lifecycle ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def invalidate(self) -> None:
        """Called when the configuration changes.

        Turning the browser off, or repointing the roots, must not leave live
        handles behind: a handle IS authority over a directory, and authority
        granted under one configuration has no business surviving into the
        next.
        """

        self._browser.invalidate()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.invalidate()

    # -- routes ------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        """What this host can do, as facts rather than an OS name."""

        return {
            "enabled": self._enabled,
            "browse": self._enabled,
            "max_entries": MAX_PAGE_ENTRIES,
            "max_handles": MAX_OPEN_HANDLES,
            "generation": self._browser.generation,
            # The real answer, from artifact probes. False on a container
            # or a headless server, and the page renders the control disabled
            # rather than offering an action that cannot work.
            "reveal": self._native.capability.available,
            "reveal_detail": self._native.capability.to_dict(),
            "purposes": [PURPOSE_MODEL_ROOT],
        }

    def places(self, *, filesystem=None) -> dict[str, Any]:
        """A conservative starting set. NOT an inventory of the host.

        Configured roots first, because "the folder I already use" is the
        answer most of the time; then home; then the local volume roots that
        the root policy would actually admit. Nothing enumerates shares,
        removable media that is not present, or every mount on the machine.
        """

        self._require_enabled()
        places: list[dict[str, str]] = []
        seen: set[str] = set()

        def offer(kind: str, label: str, path: Path) -> None:
            text = str(path)
            if text in seen or len(places) >= MAX_PLACE_VOLUMES:
                return
            seen.add(text)
            places.append({"kind": kind, "label": label, "path": text})

        for role, roots in self._configured_roots().items():
            for ordinal, text in enumerate(roots):
                label = role.replace("_", " ")
                if len(roots) > 1:
                    label = f"{label} {ordinal + 1}"
                offer("configured", label, Path(text))

        home = self._home(filesystem)
        if home is not None:
            offer("home", "Home", home)

        for root in self._admissible_volume_roots():
            offer("volume", str(root), root)

        return {"places": places, "generation": self._browser.generation}

    def resolve(self, payload: Any) -> dict[str, Any]:
        """The ONE text -> path entry point. Everything else navigates."""

        self._require_enabled()
        text = self._text(payload, "path")
        with self._slot():
            return self._document(self._browser.open_path(text))

    def list(self, payload: Any) -> dict[str, Any]:
        """Navigate: stay, descend by name, or go up. Never by path."""

        self._require_enabled()
        if not isinstance(payload, dict):
            raise HeadlessError("STUDIO_FS_MALFORMED", "Expected a JSON object.")
        handle = str(payload.get("handle") or "").strip()
        if not handle:
            raise HeadlessError("STUDIO_FS_MALFORMED", "A handle is required.")
        child = payload.get("child")
        parent = bool(payload.get("parent"))
        if child is not None and not isinstance(child, str):
            raise HeadlessError("STUDIO_FS_MALFORMED", "child must be a name.")
        if child and parent:
            raise HeadlessError(
                "STUDIO_FS_MALFORMED", "Ask for a child or the parent, not both."
            )

        with self._slot():
            if parent:
                return self._document(self._browser.ascend(handle))
            if child:
                return self._document(self._browser.descend(handle, child))
            return self._document(self._browser.listing(handle))

    def reveal(self, payload: Any) -> dict[str, Any]:
        """Open a directory the owner is LOOKING AT in their file manager.

        Takes a handle, never a path. The client cannot name what gets opened:
        it can only name a directory the server already admitted, and the
        server re-checks that directory immediately before the call.
        """

        self._require_enabled()
        if not isinstance(payload, dict):
            raise HeadlessError("STUDIO_FS_MALFORMED", "Expected a JSON object.")
        handle = str(payload.get("handle") or "").strip()
        if not handle:
            raise HeadlessError("STUDIO_FS_MALFORMED", "A handle is required.")
        with self._slot():
            listing = self._browser.listing(handle)
            return self._native.reveal(Path(listing.path))

    def diagnostics(self) -> dict[str, Any]:
        """Why a refusal happened, in terms an owner can act on.

        Deliberately a separate, opt-in route rather than fields bolted onto
        every listing: it exists to explain a problem, and a surface that
        explains problems all the time is a surface that discloses host detail
        all the time. It carries codes and counts -- never a path Studio was
        not already showing.
        """

        self._require_enabled()
        roles: list[dict[str, Any]] = []
        describe = getattr(self._registry, "describe", None)
        if describe is not None:
            try:
                for role, status in describe().items():
                    roles.append(status.to_dict() if hasattr(status, "to_dict")
                                 else {"role": role})
            except Exception:  # noqa: BLE001 - diagnostics must not fail hard
                roles = []
        return {
            "roles": roles,
            "reveal": self._native.capability.to_dict(),
            "limits": {
                "max_entries": MAX_PAGE_ENTRIES,
                "max_handles": MAX_OPEN_HANDLES,
                "max_roots_per_role": _max_roots_per_role(),
                "concurrency": BROWSE_CONCURRENCY,
            },
            "generation": self._browser.generation,
        }

    # -- shaping -----------------------------------------------------------

    def _document(self, listing: Listing) -> dict[str, Any]:
        """The one place a Listing becomes a response body.

        `path` and `breadcrumb` carry real filesystem text. They are the
        reason this module is the declared exception to path privacy, and
        they appear nowhere else.
        """

        return {
            "handle": listing.handle,
            "path": listing.path,
            "display_name": listing.display_name,
            "breadcrumb": [
                {"label": label, "path": text} for label, text in listing.breadcrumb
            ],
            "entries": [
                {
                    "name": entry.name,
                    "kind": "directory" if entry.is_dir else "file",
                    "redirecting": entry.is_redirecting,
                    "size_bytes": entry.size_bytes,
                    "modified": entry.modified,
                }
                for entry in listing.entries
            ],
            "truncated": listing.truncated,
            "total_seen": listing.total_seen,
            "has_parent": listing.has_parent,
            "usable": listing.usable,
            "usable_reason": listing.usable_reason,
            "generation": self._browser.generation,
        }

    # -- internals ---------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise HeadlessError(
                BROWSE_DISABLED, "Folder browsing is switched off."
            )

    def _slot(self):
        service = self

        class _Slot:
            def __enter__(self):
                if not service._slots.acquire(blocking=False):
                    raise HeadlessError(
                        BROWSE_BUSY, "Too many folders are being read; try again."
                    )
                return self

            def __exit__(self, *_exception):
                service._slots.release()
                return None

        return _Slot()

    def _text(self, payload: Any, key: str) -> str:
        if not isinstance(payload, dict):
            raise HeadlessError("STUDIO_FS_MALFORMED", "Expected a JSON object.")
        value = payload.get(key)
        if not isinstance(value, str):
            raise HeadlessError("STUDIO_FS_MALFORMED", f"{key} must be text.")
        return value

    def _configured_roots(self) -> dict[str, tuple[str, ...]]:
        getter = getattr(self._registry, "configured_roots", None)
        if getter is None:
            return {}
        try:
            configured = getter()
        except Exception:  # noqa: BLE001 - a place list must never fail hard
            return {}
        return {
            role: (paths,) if isinstance(paths, str) else tuple(paths)
            for role, paths in dict(configured).items()
        }

    def _home(self, filesystem=None) -> Path | None:
        source = filesystem or getattr(self._browser, "_fs", None)
        getter = getattr(source, "home", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001
            return None

    def _admissible_volume_roots(self) -> tuple[Path, ...]:
        """Volume roots the policy would accept, and no more.

        Windows is enumerated by letter because that is the only way to ask;
        POSIX offers `/`, because everything hangs beneath it and offering
        every mount would be the inventory this is supposed to avoid. Each
        candidate is run through the SAME volume admission a typed path gets,
        so a network drive never appears in the list at all.
        """

        import os

        from forge_headless.root_policy import _refuse_by_volume

        candidates: list[Path] = []
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                candidates.append(Path(f"{letter}:\\"))
        else:
            candidates.append(Path("/"))

        admitted: list[Path] = []
        for candidate in candidates:
            try:
                if not candidate.exists():
                    continue
                _refuse_by_volume(candidate)
            except (HeadlessError, OSError):
                continue
            admitted.append(candidate)
            if len(admitted) >= MAX_PLACE_VOLUMES:
                break
        return tuple(admitted)


__all__ = (
    "BROWSE_BUSY",
    "BROWSE_CONCURRENCY",
    "BROWSE_DISABLED",
    "MAX_PLACE_VOLUMES",
    "FilesystemService",
)
