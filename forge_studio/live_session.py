"""Live Painting: the session that turns canvas changes into frames (LV1).

Source review: Evidence/source-review/LV0-live-painting.md. The Extension's
`scripts/studio_live.py` keeps one running frame and ONE pending slot -- the
newest change overwrites it -- and never interrupts a running frame
(interrupting gave NaN/black frames on Anima/Cosmos). This is that rule over
Studio's own job path: every frame is an ordinary job through the canonical
coordinator (one GPU lease per frame, the owner's decision), built exactly as
`/api/generate` builds one, with `output.purpose = "live"` so the port returns
the picture inline and writes nothing (no file, no Gallery row, no heatmap).

Events go to the page over the existing `/studio/ws` as the Extension's
`live_started` / `live_stopped` / `live_busy` / `live_result` / `live_error`
messages, which the page's `Live` object already handles. The socket loop
reads them with `events_since(cursor)`.

Dropped from the Extension, on purpose: `LiveScheduler`. Its `submit` called
`should_generate` and ignored the answer (`studio_live.py:189`), so it was
bookkeeping; the page's debounce and this pending slot are the real rate
limit. Its average frame time survives as `status()["avg_gen_time"]`.

No torch, no engine import: the coordinator is reached through three
callables, so this module is tested with plain functions.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Mapping

#: One console line per frame, instead of the five an ordinary job prints.
_LOG = logging.getLogger("studio.job")

TERMINAL = ("completed", "failed", "cancelled")
#: Events the socket can still catch up on. A tab that falls further behind
#: skips ahead rather than replaying stale frames.
EVENT_LIMIT = 16
#: Frame pictures kept in the event log. Older `live_result` events keep their
#: place but lose the picture (megabytes each).
PICTURES_KEPT = 2
#: How often a running frame's job is looked at.
POLL_SECONDS = 0.05
#: A frame's job that has not finished by then is given up on (the first
#: frame after a model change includes the load, so this is generous).
FRAME_DEADLINE = 600.0


class LiveError(ValueError):
    """A Live request Studio refuses, with the reason the page shows."""


def _number(spec: Mapping[str, Any], key: str, kind: type, default: Any, low: float, high: float) -> Any:
    raw = spec.get(key, default)
    if isinstance(raw, bool):
        raise LiveError(f"Live {key} must be a number.")
    try:
        value = kind(raw)
    except (TypeError, ValueError):
        raise LiveError(f"Live {key} must be a number.") from None
    if not low <= value <= high:
        raise LiveError(f"Live {key} must be between {low:g} and {high:g}.")
    return value


def frame_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The `/api/generate` body for one frame, from the page's Live spec.

    The page sends what `Live._buildSpec` builds (`app.js`): the canvas as a
    PNG data URL at the panel's size, both prompts, the Live seed and
    strength, the main panel's sampler, scheduler, CFG and steps -- uncapped,
    the owner's decision after LV0 -- and the model selection Generate sends.
    """

    if not isinstance(spec, Mapping):
        raise LiveError("A Live frame must be a JSON object.")
    image = spec.get("image")
    if not isinstance(image, str) or not image.startswith("data:image/"):
        raise LiveError("A Live frame needs the canvas picture.")
    selection = spec.get("model_selection")
    if not isinstance(selection, Mapping) or not str(selection.get("checkpoint_model_id") or "").strip():
        raise LiveError("Choose a checkpoint first: Live paints with the model selected on the Generate panel.")
    seed = _number(spec, "seed", int, -1, -1, 2**32 - 1)
    generation = {
        "operation": "img2img",
        "source_image": {"data_url": image},
        "positive_prompt": str(spec.get("prompt") or ""),
        "negative_prompt": str(spec.get("negative_prompt") or ""),
        "seed": seed,
        "denoising_strength": _number(spec, "strength", float, 0.3, 0.01, 1.0),
        "width": _number(spec, "width", int, 512, 64, 4096),
        "height": _number(spec, "height", int, 512, 64, 4096),
        "steps": _number(spec, "steps", int, 20, 1, 150),
        "cfg_scale": _number(spec, "cfg_scale", float, 7.0, 0.0, 30.0),
        "sampler": str(spec.get("sampler_name") or ""),
        "scheduler": str(spec.get("scheduler") or ""),
        "preview_enabled": False,
        "output": {"purpose": "live"},
    }
    return {"model": "", "model_selection": dict(selection), "generation": generation}


class LiveSession:
    """One running frame, one pending slot, newest wins, never interrupted.

    Every event names the page that caused it (`client`): the socket reaches
    every open tab, and a tab that is not painting must not show another
    tab's frames (code review, 2026-10-21). A page that sends no client id is
    treated as the only one.
    """

    def __init__(
        self,
        submit: Callable[[dict[str, Any]], Mapping[str, Any]],
        poll: Callable[[str], Mapping[str, Any]],
        picture: Callable[[str], tuple[str, Mapping[str, Any]]],
        forget: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._submit = submit          # payload -> {"job_id": ...}
        self._poll = poll              # job id -> {"state": ..., "error": ...}
        self._picture = picture        # job id -> (data URL, metadata)
        self._forget = forget          # job id -> drop the finished job's records
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._active = False
        #: Bumped by every Start and Stop. A frame shows only in the session
        #: it was asked for: Stop then Start while a frame runs must not put
        #: the old canvas's frame into the new session.
        self._session = 0
        self._running: str | None = None       # the frame's job id while it runs
        self._worker: threading.Thread | None = None
        self._pending: _Frame | None = None
        self._events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=EVENT_LIMIT)
        self._sequence = 0
        self._times: deque[float] = deque(maxlen=10)

    # -- the page's four routes ----------------------------------------------

    def start(self, client: str = "") -> dict[str, Any]:
        with self._lock:
            self._active = True
            self._session += 1
            self._times.clear()
            self._emit({"type": "live_started"}, client)
        return {"status": "started", "active": True}

    def stop(self, client: str = "") -> dict[str, Any]:
        """Stop scheduling. A frame already running finishes on its own and
        is not followed by another; it is never interrupted."""
        with self._lock:
            self._active = False
            self._session += 1
            self._pending = None
            self._emit({"type": "live_stopped"}, client)
        return {"status": "stopped", "active": False}

    def submit(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        client = _client_of(spec)
        if not self._active:
            # Said on the socket too: after Studio restarts under a page that
            # still has Live on, this is the only thing the page hears.
            with self._lock:
                self._emit({"type": "live_error", "message": "Live painting is not active.",
                            "inactive": True}, client)
            raise LiveError("Live painting is not active.")
        try:
            payload = frame_payload(spec)
        except LiveError as refusal:
            # The page only logs a failed submit; the socket is where it shows
            # a message (`Live._onError`).
            with self._lock:
                self._emit({"type": "live_error", "message": str(refusal)}, client)
            raise
        with self._lock:
            self._pending = _Frame(payload, int(payload["generation"]["seed"]), client, self._session)
            if self._worker is not None and self._worker.is_alive():
                self._emit({"type": "live_busy", "pending": True}, client)
                return {"status": "submitted", "queued": True}
            self._worker = threading.Thread(target=self._run, daemon=True, name="studio-live-frames")
            self._worker.start()
        return {"status": "submitted", "queued": False}

    @property
    def active(self) -> bool:
        return self._active

    def status(self) -> dict[str, Any]:
        with self._lock:
            average = sum(self._times) / len(self._times) if self._times else 0.0
            return {
                "available": True,
                "active": self._active,
                "generating": self._running is not None,
                "pending": self._pending is not None,
                "avg_gen_time": round(average, 3),
            }

    # -- the socket ------------------------------------------------------------

    def events_since(self, cursor: int) -> tuple[int, list[dict[str, Any]]]:
        """Events after `cursor`, and the new cursor. A new socket passes -1
        and gets only what happens from now on."""
        with self._lock:
            if cursor < 0:
                return self._sequence, []
            fresh = [event for number, event in self._events if number > cursor]
            return self._sequence, fresh

    # -- frames ----------------------------------------------------------------

    def _emit(self, event: dict[str, Any], client: str = "") -> None:
        """Called with the lock held."""
        if client:
            event["client"] = client
        self._sequence += 1
        self._events.append((self._sequence, event))
        if event.get("type") == "live_result":
            kept = 0
            for _number_, older in reversed(self._events):
                if older.get("type") == "live_result" and "image" in older:
                    kept += 1
                    if kept > PICTURES_KEPT:
                        older.pop("image", None)

    def _run(self) -> None:
        first = True
        while True:
            with self._lock:
                frame = self._pending
                self._pending = None
                if frame is None or not self._active:
                    self._worker = None
                    return
                if not first:
                    # The change that waited is running now (the Extension's
                    # `_on_generation_complete`).
                    self._emit({"type": "live_busy", "pending": False}, frame.client)
            first = False
            self._frame(frame)

    def _frame(self, frame: "_Frame") -> None:
        began = self._clock()
        payload, seed, client = frame.payload, frame.seed, frame.client
        job_id = ""
        outcome = "failed"
        try:
            submitted = self._submit(payload)
            job_id = str(submitted.get("job_id") or "")
            if not job_id:
                raise LiveError(str(submitted.get("error") or "Studio did not accept the frame."))
            with self._lock:
                self._running = job_id
            view: Mapping[str, Any] = {}
            while True:
                view = self._poll(job_id)
                if str(view.get("state") or "") in TERMINAL:
                    break
                # A job that never finishes would hold the one Live worker,
                # and every later stroke would only replace the waiting one.
                if self._clock() - began > FRAME_DEADLINE:
                    raise LiveError("The frame did not finish in %d minutes." % (FRAME_DEADLINE // 60))
                self._sleep(POLL_SECONDS)
            state = str(view.get("state") or "")
            if state != "completed":
                error = view.get("error")
                message = (error.get("message") if isinstance(error, Mapping) else error) or f"The frame {state}."
                raise LiveError(str(message))
            image, metadata = self._picture(job_id)
            with self._lock:
                self._times.append(self._clock() - began)
                # Shown only in the session that asked for it: not after
                # Stop, and not after Stop and Start (the canvas moved on).
                if self._active and self._session == frame.session:
                    self._emit({"type": "live_result", "image": image,
                                "seed": int(metadata.get("seed", seed) if isinstance(metadata, Mapping) else seed)},
                               client)
                    outcome = "shown"
                else:
                    outcome = "not shown (Live stopped)"
        except Exception as exc:  # noqa: BLE001 - a frame's failure is the page's message, never the loop's end
            message = str(exc) or type(exc).__name__
            outcome = "failed: " + message
            with self._lock:
                self._emit({"type": "live_error", "message": message}, client)
        finally:
            with self._lock:
                self._running = None
            # Read once, then dropped: the job's records hold the canvas and
            # the frame (`JobCoordinator.forget`, the backend's `forget`).
            if job_id and self._forget is not None:
                try:
                    self._forget(job_id)
                except Exception:  # noqa: BLE001 - forgetting never fails a frame
                    pass
            generation = payload.get("generation", {})
            _LOG.info("LIVE frame %sx%s steps=%s strength=%s seed=%s %.2fs %s",
                      generation.get("width"), generation.get("height"), generation.get("steps"),
                      generation.get("denoising_strength"), seed, self._clock() - began, outcome)


class _Frame:
    """One frame waiting or running: its job body, seed, page and session."""

    __slots__ = ("payload", "seed", "client", "session")

    def __init__(self, payload: dict[str, Any], seed: int, client: str, session: int) -> None:
        self.payload, self.seed, self.client, self.session = payload, seed, client, session


def _client_of(spec: Any) -> str:
    """The page's id for itself (`Live._clientId`), short and printable, or ""."""
    if not isinstance(spec, Mapping):
        return ""
    raw = str(spec.get("client") or "")
    return raw[:64] if raw.isascii() and raw.isprintable() else ""


__all__ = ("EVENT_LIMIT", "FRAME_DEADLINE", "LiveError", "LiveSession", "PICTURES_KEPT", "frame_payload")
