"""Noticing that the library changed, without asking twenty times.

Studio writes the images it generates. It does not have to watch a folder to
learn that a picture appeared -- it made the picture. So the trigger here is a
call, not a filesystem event, and the Gallery updates itself on a machine with
no filesystem-notification library installed at all.

What this adds over calling the scan directly is patience. A batch of eight
images finishes eight times in a few seconds, and a scan per image would walk
every linked folder eight times to report the same eight files. So a `notify`
sets a flag, and the worker waits for the noise to stop before it looks.

Two bounds, because one is not enough:

```text
quiet_seconds     wait for this long with no further notify, then sync
deadline_seconds  ...but never wait longer than this since the FIRST notify
```

The Extension has only the first (`studio_gallery.py:458-468`: wait, clear,
sleep 2, and if it went dirty again in those two seconds, loop instead of
syncing). A generation queue that finishes an image every 1.9 seconds keeps
resetting that window, so the owner watches an empty grid for as long as the
queue runs -- the starvation is invisible in testing because it needs a steady
stream, which is exactly what a batch is. The deadline makes the wait bounded:
quiet if it can be, punctual regardless.

`wait` and `clock` are injected so the debounce can be tested by driving time
rather than by sleeping through it.

Studio-owned: nothing here imports Forge, Neo or Torch.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

#: Long enough for a batch to settle, short enough that one image does not feel
#: delayed. The Extension uses 2.0; a generation is a known event rather than a
#: guess about a file being finished, so it can be tighter.
QUIET_SECONDS = 1.5

#: The longest an owner waits while generations keep arriving.
DEADLINE_SECONDS = 10.0


class AutoSync:
    """A background pass that coalesces bursts and publishes what changed."""

    def __init__(self,
                 run: Callable[[], tuple[int, int] | None],
                 publish: Callable[[str, Any], Any],
                 *,
                 quiet_seconds: float = QUIET_SECONDS,
                 deadline_seconds: float = DEADLINE_SECONDS,
                 wait: Callable[[float], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._run = run
        self._publish = publish
        self._quiet = float(quiet_seconds)
        self._deadline = float(deadline_seconds)
        self._clock = clock
        self._sleep = wait if wait is not None else time.sleep
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self._wake = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None
        #: Counted, not a set of paths. The Extension suppresses per path
        #: (`studio_gallery.py:102`) and clears the WHOLE set after a sync
        #: (`:454-455`), so a long action begun during a pass loses its
        #: protection halfway through. A depth counter cannot be cleared out
        #: from under a caller that is still inside its own action.
        self._deferred = 0
        self._first_notify: float | None = None
        self._last_notify: float | None = None

    # -- the owner-facing edge ---------------------------------------------

    def notify(self, _reason: str = "") -> None:
        """Something changed. Look, once the changing has stopped."""

        with self._lock:
            now = self._clock()
            if self._first_notify is None:
                self._first_notify = now
            self._last_notify = now
        self._dirty.set()
        self._wake.set()
        self.start()

    def defer(self) -> "AutoSync":
        """A context manager: hold the pass off while Studio edits files itself.

        A rename is a delete and a create to anything watching, and a pass that
        lands between the two indexes a file the Gallery is halfway through
        moving.
        """

        return self

    def __enter__(self) -> "AutoSync":
        with self._lock:
            self._deferred += 1
        return self

    def __exit__(self, *_exception: Any) -> None:
        with self._lock:
            self._deferred = max(0, self._deferred - 1)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the worker if it is not already running. Idempotent.

        Lazy rather than started in a constructor: a `GalleryService` is built
        for every host with a state root, including ones that never open the
        Gallery, and a thread per construction is a thread per test.
        """

        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._loop, name="studio-gallery-autosync", daemon=True)
            thread = self._thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop, and WAIT for the pass to finish.

        Joined on purpose. The pass holds the Gallery's database handle, and
        `GalleryService.close()` exists to let go of that file -- on Windows a
        live handle keeps it locked. Returning from `close()` while a worker is
        still mid-scan would leave the handle open behind it.
        """

        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None
        self._wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # -- the worker --------------------------------------------------------

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            self._wake.wait(timeout=self._quiet)
            self._wake.clear()
            with self._lock:
                if not self._running:
                    return
                if self._deferred:
                    continue  # Studio is editing files; not now
            if not self._dirty.is_set():
                continue
            if not self._settled():
                continue
            self._dirty.clear()
            with self._lock:
                self._first_notify = self._last_notify = None
            self._pass()

    def _settled(self) -> bool:
        """Whether the burst has stopped, or has waited long enough regardless.

        Quiet since the LAST notify -- a debounce measured from the first one
        is not a debounce, it is a fixed delay. The deadline is measured from
        the first, and is what stops a steady stream of generations from
        resetting the window forever.
        """

        with self._lock:
            first, last = self._first_notify, self._last_notify
        if first is None or last is None:
            return True
        now = self._clock()
        if (now - last) >= self._quiet:
            return True
        return (now - first) >= self._deadline

    def _pass(self) -> None:
        """One sync. Never raises: this is the top of a daemon thread."""

        try:
            changed = self._run()
        except Exception:  # noqa: BLE001
            # A pass that fails must not kill the worker, or the Gallery stops
            # updating for the rest of the session with nothing said.
            return
        if not changed:
            return
        added, removed = changed
        if not added and not removed:
            return  # nothing to tell a page about
        try:
            self._publish("sync", {"new": int(added), "removed": int(removed)})
        except Exception:  # noqa: BLE001
            return


__all__ = ("DEADLINE_SECONDS", "QUIET_SECONDS", "AutoSync")
