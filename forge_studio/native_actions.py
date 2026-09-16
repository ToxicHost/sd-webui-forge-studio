"""Reveal a folder in whatever file manager this machine actually has.

Capability-driven, which here means something specific: nothing asks what the
operating system is CALLED. It asks whether an artifact exists.

```text
explorer.exe under %SystemRoot%   -> Windows with a shell
/usr/bin/open                     -> macOS with Finder
xdg-open or gio, AND a display    -> a Linux desktop session
none of the above                 -> unavailable, said plainly
```

The last line is the one that matters most. A container and a headless server
have no file manager, and telling the owner "reveal failed" there would be a
lie about a capability rather than an honest absence -- so the capability is
reported false and the control renders disabled instead of failing when
pressed. It also keeps `forge_studio` under its `sys.platform` ban, which is
not a coincidence: the ban exists because a platform NAME is a bad proxy for
what a machine can do, and this is the case that proves it. A Linux box over
SSH and a Linux desktop have the same name and different answers.

**The launch is narrow on purpose.** It is one action -- open this directory --
and the only thing that varies is which of three vetted executables is used.

```text
no shell=True
no shell string assembled from anything
no executable named by the browser
no argument the client supplies except a path the server already resolved
argument vector only
```

The path is REVALIDATED immediately before the call, not trusted from whenever
the handle was minted, because "open this folder" is exactly the operation
where a swapped target matters.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from forge_headless.contracts import HeadlessError

#: Refusals. Distinct codes, because "there is no file manager here" and "that
#: folder is gone" need different words in the UI.
REVEAL_UNAVAILABLE = "STUDIO_REVEAL_UNAVAILABLE"
REVEAL_TARGET_INVALID = "STUDIO_REVEAL_TARGET_INVALID"
REVEAL_FAILED = "STUDIO_REVEAL_FAILED"
REVEAL_RATE_LIMITED = "STUDIO_REVEAL_RATE_LIMITED"

#: Reveals allowed per window. A click opens a window on the owner's desktop;
#: a loop would open hundreds.
MAX_REVEALS_PER_WINDOW = 5
REVEAL_WINDOW_SECONDS = 10.0

#: Finished child processes to remember. Reaped so a long session does not
#: accumulate zombies on POSIX.
MAX_TRACKED_CHILDREN = 16


@dataclass(frozen=True)
class RevealCapability:
    """What this machine can do, and how it was decided."""

    available: bool
    #: A stable name for the mechanism, never an OS name: the page shows it
    #: so an owner on a remote host understands WHERE the window will open.
    mechanism: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"available": self.available}
        if self.mechanism:
            payload["mechanism"] = self.mechanism
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class HostProbe:
    """The facts a capability decision is made from. Injectable, so every
    platform's answer is testable on any machine."""

    is_windows: bool = False
    system_root: str | None = None
    windows_explorer_exists: bool = False
    macos_open_exists: bool = False
    linux_opener: str | None = None
    has_display: bool = False


def probe_host() -> HostProbe:
    """Read the artifacts. The ONLY function here that touches the machine."""

    is_windows = os.name == "nt"
    system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
    explorer = False
    if is_windows and system_root:
        try:
            explorer = (Path(system_root) / "explorer.exe").is_file()
        except OSError:
            explorer = False

    try:
        macos_open = Path("/usr/bin/open").is_file()
    except OSError:
        macos_open = False

    opener = None
    for candidate in ("xdg-open", "gio"):
        found = shutil.which(candidate)
        if found:
            opener = found
            break

    display = bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    return HostProbe(
        is_windows=is_windows,
        system_root=system_root,
        windows_explorer_exists=explorer,
        macos_open_exists=macos_open,
        linux_opener=opener,
        has_display=display,
    )


def detect_reveal(probe: HostProbe) -> RevealCapability:
    """Decide from facts. Pure, so every host's answer is testable here.

    Ordered by how conclusive the artifact is. `explorer.exe` under
    `%SystemRoot%` is decisive; `/usr/bin/open` exists on macOS and not on a
    normal Linux box; a Linux opener is only meaningful WITH a display,
    because `xdg-open` is installed on plenty of headless servers where it has
    nothing to open onto.
    """

    if probe.is_windows:
        if probe.windows_explorer_exists:
            return RevealCapability(available=True, mechanism="explorer")
        return RevealCapability(
            available=False, reason="No file manager was found on this machine."
        )
    if probe.macos_open_exists:
        return RevealCapability(available=True, mechanism="finder")
    if probe.linux_opener:
        if not probe.has_display:
            # An opener with no desktop to open onto: a container, or a
            # server over SSH. Reported as unavailable rather than tried,
            # because trying would fail slowly and confusingly.
            return RevealCapability(
                available=False,
                reason="This machine has no desktop session to open a folder on.",
            )
        return RevealCapability(available=True, mechanism="desktop")
    return RevealCapability(
        available=False, reason="No file manager was found on this machine."
    )


def _argv(capability: RevealCapability, probe: HostProbe, target: Path) -> list[str]:
    """The exact vector. Every element is chosen HERE, never supplied.

    The only value that comes from outside is `target`, and it arrives as an
    already-resolved directory that has just been re-checked -- and it is
    passed as one argument, not interpolated into anything.
    """

    if capability.mechanism == "explorer":
        root = probe.system_root or ""
        return [str(Path(root) / "explorer.exe"), str(target)]
    if capability.mechanism == "finder":
        return ["/usr/bin/open", str(target)]
    if capability.mechanism == "desktop":
        opener = probe.linux_opener or ""
        if opener.endswith("gio"):
            return [opener, "open", str(target)]
        return [opener, str(target)]
    raise HeadlessError(REVEAL_UNAVAILABLE, "There is no file manager here.")


class NativeActions:
    """Reveal, rate-limited, revalidated, and narrow."""

    def __init__(self, *, probe: HostProbe | None = None, spawn=None, clock=time.monotonic) -> None:
        self._probe = probe if probe is not None else probe_host()
        self._capability = detect_reveal(self._probe)
        # Injected so no test ever starts a real process.
        self._spawn = spawn or self._default_spawn
        self._clock = clock
        self._recent: deque[float] = deque(maxlen=MAX_REVEALS_PER_WINDOW)
        self._children: deque = deque(maxlen=MAX_TRACKED_CHILDREN)
        self._lock = threading.Lock()

    @property
    def capability(self) -> RevealCapability:
        return self._capability

    def open_file(self, target: Path, allowed_suffixes: frozenset[str]
                  ) -> dict[str, object]:
        """Open one file with whatever the system opens it with.

        Deliberately narrower than `reveal`. Revealing points a file manager at
        a directory; this hands a file to whichever program is registered for
        its type, so the ONE thing that must never be possible is doing it to
        an executable. The caller passes the suffixes it will vouch for -- the
        Gallery passes its media list -- and anything else is refused before a
        process is spawned.

        Everything else is `reveal`'s: the same capability, the same rate
        limit, the same revalidation immediately before the call.
        """

        resolved = Path(target)
        if resolved.suffix.lower() not in allowed_suffixes:
            raise HeadlessError(
                REVEAL_TARGET_INVALID,
                "Studio only opens the kinds of file it recognises.",
            )
        if not resolved.is_absolute() or not resolved.is_file():
            raise HeadlessError(
                REVEAL_TARGET_INVALID, "That file is no longer available."
            )
        return self._launch(resolved, {"opened": True})

    def reveal(self, target: Path) -> dict[str, object]:
        # REVALIDATED here, immediately before the call. A handle minted a
        # minute ago is not evidence about what this path is now, and "open
        # this folder" is precisely the operation where a swapped target
        # matters.
        resolved = Path(target)
        if not resolved.is_absolute() or not resolved.is_dir():
            raise HeadlessError(
                REVEAL_TARGET_INVALID, "That folder is no longer available."
            )
        return self._launch(resolved, {"revealed": True})

    def _launch(self, resolved: Path, answer: dict[str, object]
                ) -> dict[str, object]:
        """The spawn both actions share: capability, rate limit, one vetted
        argv, no shell.

        The gate lives HERE rather than in each caller, so an action added
        later cannot reach a spawn without passing it. `open_file` originally
        did its own checks and silently had neither.
        """

        if not self._capability.available:
            raise HeadlessError(
                REVEAL_UNAVAILABLE,
                self._capability.reason or "There is no file manager here.",
            )
        with self._lock:
            now = self._clock()
            while self._recent and now - self._recent[0] > REVEAL_WINDOW_SECONDS:
                self._recent.popleft()
            if len(self._recent) >= MAX_REVEALS_PER_WINDOW:
                raise HeadlessError(
                    REVEAL_RATE_LIMITED,
                    "Too many things were opened at once; try again shortly.",
                )
            self._recent.append(now)

        argv = _argv(self._capability, self._probe, resolved)
        try:
            child = self._spawn(argv)
        except OSError:
            raise HeadlessError(
                REVEAL_FAILED, "That could not be opened."
            ) from None
        if child is not None:
            self._track(child)
        return {**answer, "mechanism": self._capability.mechanism}

    def _track(self, child) -> None:
        with self._lock:
            self._children.append(child)
            for tracked in tuple(self._children):
                poll = getattr(tracked, "poll", None)
                if poll is not None and poll() is not None:
                    try:
                        self._children.remove(tracked)
                    except ValueError:
                        pass

    @staticmethod
    def _default_spawn(argv: list[str]):
        """One vetted executable, one argument vector, no shell anywhere.

        `shell=False` is the default and is stated anyway, because this is the
        line where a future edit would be tempted to accept a command string.
        stdin/stdout/stderr are closed so a file manager cannot hold the
        server's console open.
        """

        return subprocess.Popen(  # noqa: S603 - vetted argv, shell=False
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )


__all__ = (
    "MAX_REVEALS_PER_WINDOW",
    "REVEAL_FAILED",
    "REVEAL_RATE_LIMITED",
    "REVEAL_TARGET_INVALID",
    "REVEAL_UNAVAILABLE",
    "HostProbe",
    "NativeActions",
    "RevealCapability",
    "detect_reveal",
    "probe_host",
)
