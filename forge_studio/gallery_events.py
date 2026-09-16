"""Telling every open Gallery tab that something changed.

`gallery.js` opens an `EventSource` on `/studio/gallery/events` and listens for
three named events. Until this module existed the server could answer that
connection but never speak into it: `events()` yielded one status frame and then
keepalive comments forever, so `sync` -- the event the page turns into a grid
reload -- was a listener with no speaker. A generated image therefore sat on
disk, correctly written and inside a linked folder, until the owner pressed
refresh.

The bus is deliberately small.

**Publishing never raises.** Its callers are a generation finishing and a
background sync thread. Neither may be taken down because a page went away
mid-write, so every failure here is swallowed at the boundary.

**A slow subscriber is dropped, not evicted.** Each subscriber gets a bounded
queue and a full queue costs that subscriber THAT MESSAGE, counted, rather than
its connection. The Extension removes a client on a full queue
(`studio_gallery.py:298-309`), which reads as liveness detection but is not: a
tab that merely paused -- backgrounded, or blocked on a slow reload -- is
disconnected after fifty messages it would have caught up on. Dropping one
`sync` is harmless because the next one reloads the whole grid anyway.

**Framing happens once, outside the lock.** A publish serialises to bytes before
it takes the lock, so the time the lock is held is a handful of `put_nowait`
calls and nothing else.

Studio-owned: nothing here imports Forge, Neo or Torch.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

#: How many unread messages one tab may fall behind by. Small on purpose: the
#: page's own handler reloads everything it needs, so a backlog has no value --
#: only the most recent event carries information the earlier ones do not.
EVENT_QUEUE_DEPTH = 32

#: Pushed at shutdown to wake a stream parked on `get()`. A sentinel rather
#: than a flag because the reader is blocked in the queue, not on a condition
#: it could be asked to re-check.
CLOSED = None


def frame(name: str, data: Any = None) -> bytes:
    """One Server-Sent Event, framed.

    The blank line at the end is what tells the browser the event is complete;
    without it the page holds the data and never dispatches a listener.
    """

    body = json.dumps(data if data is not None else {})
    return f"event: {name}\ndata: {body}\n\n".encode("utf-8")


class EventBus:
    """Every open Gallery tab, and a way to say something to all of them."""

    def __init__(self, depth: int = EVENT_QUEUE_DEPTH) -> None:
        self._depth = max(1, int(depth))
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._dropped = 0
        self._closed = False

    def subscribe(self) -> queue.Queue:
        """A queue of framed events for one connection."""

        channel: queue.Queue = queue.Queue(maxsize=self._depth)
        with self._lock:
            if self._closed:
                channel.put_nowait(CLOSED)
                return channel
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        """Called from the stream's `finally`, so it must tolerate anything."""

        with self._lock:
            try:
                self._subscribers.remove(channel)
            except ValueError:
                pass  # already gone, or never ours

    def publish(self, name: str, data: Any = None) -> int:
        """Say something to every tab. Returns how many were told.

        NEVER RAISES. A generation that finished successfully must not be
        reported as failed because a Gallery tab closed while this was writing
        to it.
        """

        try:
            message = frame(name, data)
        except (TypeError, ValueError):
            return 0
        told = 0
        with self._lock:
            for channel in self._subscribers:
                try:
                    channel.put_nowait(message)
                    told += 1
                except queue.Full:
                    # That tab is behind. The next event supersedes this one.
                    self._dropped += 1
                except Exception:  # noqa: BLE001
                    continue
        return told

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def dropped(self) -> int:
        """Messages a slow subscriber missed. Reported, never guessed at."""

        with self._lock:
            return self._dropped

    def close(self) -> None:
        """Wake every parked stream so shutdown is not held up by a live tab."""

        with self._lock:
            self._closed = True
            subscribers, self._subscribers = self._subscribers, []
        for channel in subscribers:
            # A FULL queue is exactly the case that needs waking, and
            # `put_nowait` into one raises -- which would swallow the sentinel
            # and leave that stream parked until its own timeout. Make room:
            # the backlog is superseded by the shutdown anyway.
            while True:
                try:
                    channel.put_nowait(CLOSED)
                    break
                except queue.Full:
                    try:
                        channel.get_nowait()
                    except queue.Empty:
                        break
                except Exception:  # noqa: BLE001
                    break


__all__ = ("CLOSED", "EVENT_QUEUE_DEPTH", "EventBus", "frame")
