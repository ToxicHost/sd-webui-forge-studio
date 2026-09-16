"""The Gallery's routes, as answers rather than as HTTP.

`gallery.js` speaks to `/studio/gallery/...`. This module answers it, and does
so without importing an HTTP framework: a route returns a `Reply`, and the
presentation layer turns that into a response. Which means every route in the
Gallery is testable by calling a method, with no server, no port and no client.

**A Gallery that cannot run says so.** Every route checks the capability first
and refuses with a reason and a 503. It never answers `{"images": []}` when the
truth is "Pillow is missing", because an owner shown an empty Gallery concludes
their pictures are gone -- and `gallery.js`'s `api()` sets `.error` on any
non-ok response, so a refusal reaches the page as a refusal.

**Scanning ANSWERS WHEN IT IS DONE.** `/scan` is request/response, because
`gallery.js` treats the response as the completion signal: it reads `new` and
`removed` straight into a toast and reloads the grid on the very next line.
Returning early broke both halves -- "Scan: undefined new, undefined removed",
and a freshly linked folder that stayed empty until the owner pressed refresh.
The page polls `/scan-progress` on its own timer meanwhile, and draws a bar
across the bottom of the window, so the long request is not a silent one.
Asking for a second scan while one runs answers "the one you started is still
going" rather than starting a rival that would fight it for the write lock.

**Bytes are served by handle, not by path.** A thumbnail request names an image
by its row id, and the path behind it never crosses the wire in either
direction. The one place a path is produced is inside this module, from a row
the owner's own scan wrote.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

from . import (
    gallery_actions,
    gallery_index,
    gallery_query,
    gallery_similarity,
)
from .gallery_autosync import AutoSync
from .gallery_events import CLOSED, EventBus, frame
from .gallery_capability import GalleryCapability, GalleryProbe, decide
from .gallery_index import MEDIA_SUFFIXES
from .gallery_query import ImageQuery
from .gallery_scan import GalleryScanner, ScanProgress
from .gallery_store import GalleryStore, GalleryStoreError
from .gallery_thumbnails import ThumbnailCache

logger = logging.getLogger("studio.gallery")

#: Every route this module answers hangs off here.
ROUTE_PREFIX = "/studio/gallery"

#: A rendered thumbnail is immutable for its ETag, so it may be cached hard.
#: The ETag changes when the source file does, which is what makes that safe.
THUMBNAIL_CACHE_CONTROL = "public, max-age=604800, immutable"

#: How often the event stream sends a keepalive comment.
EVENT_KEEPALIVE_SECONDS = 20.0

#: How long to wait on the folder chooser before answering. Long
#: enough that an owner can browse; short enough that a dialog nobody
#: can see does not hold a server thread forever.
PICK_FOLDER_TIMEOUT_SECONDS = 300.0

#: Content types Studio will serve for a full-size image. An extension outside
#: this table is served as a download rather than guessed at, so a file that is
#: not really an image cannot be talked into executing in the page.
_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".tif": "image/tiff", ".avif": "image/avif",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


@dataclass(frozen=True)
class Reply:
    """What a route answers: JSON, or bytes, or a refusal."""

    status: int = int(HTTPStatus.OK)
    payload: Any = None
    body: bytes | None = None
    media_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _event(name: str, data: Any) -> bytes:
    """One Server-Sent Event, framed.

    The blank line at the end is what tells the browser the event is complete;
    without it the page holds the data and never dispatches a listener.
    """

    import json

    body = json.dumps(data)
    return f"event: {name}\ndata: {body}\n\n".encode("utf-8")


def _refusal(reason: str, status: int = int(HTTPStatus.SERVICE_UNAVAILABLE)
             ) -> Reply:
    return Reply(status=status, payload={"ok": False, "error": reason})


def _not_found(what: str = "Not found") -> Reply:
    return Reply(status=int(HTTPStatus.NOT_FOUND),
                 payload={"ok": False, "error": what})


class ScanRunner:
    """One scan at a time, on a thread, with progress anyone may read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._progress: dict[str, ScanProgress] = {}
        self._result: dict[str, Any] | None = None
        self._error: str | None = None
        #: True while a run owns the caller's thread rather than one of ours.
        self._inline = False

    @property
    def running(self) -> bool:
        if self._inline:
            return True
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, scanner_for: Callable[[threading.Event,
                                           Callable[[ScanProgress], None]],
                                          GalleryScanner]) -> bool:
        """Begin a scan. False if one is already going.

        The scanner is built by a callback rather than passed in, because it
        needs the cancel event and the progress sink this runner owns.
        """

        with self._lock:
            if self.running:
                return False
            self._cancel = threading.Event()
            self._progress = {}
            self._result = None
            self._error = None
            scanner = scanner_for(self._cancel, self._record)

            def run() -> None:
                try:
                    self._result = scanner.scan().to_dict()
                except GalleryStoreError as error:
                    self._error = str(error)
                except Exception as error:  # noqa: BLE001
                    # A scan crossing tens of thousands of unknown files will
                    # eventually meet one that surprises it. The thread must
                    # not die silently, leaving the page polling a scan that
                    # will never finish.
                    self._error = f"The scan stopped: {type(error).__name__}"

            self._thread = threading.Thread(
                target=run, name="studio-gallery-scan", daemon=True
            )
            self._thread.start()
            return True

    def begin(self) -> threading.Event:
        """Mark a run as started on the CALLER's thread, and hand it a cancel.

        For work that answers when it finishes rather than being polled to
        completion -- `/scan` is request/response because the page treats the
        response as the completion signal. Progress still publishes, so
        `/scan-progress` describes it while it runs.
        """

        with self._lock:
            self._cancel = threading.Event()
            self._progress = {}
            self._result = None
            self._error = None
            self._inline = True
        return self._cancel

    def finish(self) -> None:
        with self._lock:
            self._inline = False

    def cancel(self) -> None:
        self._cancel.set()

    def record(self, progress: ScanProgress) -> None:
        """Public: an inline run reports through this."""

        self._record(progress)

    def _record(self, progress: ScanProgress) -> None:
        self._progress[progress.folder] = progress

    def status(self) -> dict[str, Any]:
        return {
            "active": self.running,
            "folders": [
                {"name": p.folder, "current": p.current,
                 "total": p.total, "phase": p.phase}
                for p in self._progress.values()
            ],
            "result": self._result,
            "error": self._error,
        }


class GalleryService:
    """Everything `gallery.js` can ask for."""

    def __init__(self, state_root: str | Path,
                 probe: GalleryProbe | None = None,
                 result_root: str | Path | None = None) -> None:
        self._state_root = Path(state_root)
        self._probe = probe
        #: Studio's own output directory, adopted as a scan folder the first
        #: time this Gallery opens its store. None for a host that does not
        #: own one, which is every host that is not the launcher.
        self._result_root = Path(result_root) if result_root else None
        self._result_root_warned = False
        self._lock = threading.RLock()
        self._store: GalleryStore | None = None
        self._capability: GalleryCapability | None = None
        self._thumbnails: ThumbnailCache | None = None
        self._scan = ScanRunner()
        self._scan_lock = threading.Lock()
        self._hashing = ScanRunner()
        self._closed = False
        self._events = EventBus()
        #: Not started here. A GalleryService is constructed for every host
        #: with a state root, including ones that never open the Gallery, and
        #: a thread per construction is a thread per test.
        self._autosync = AutoSync(self._sync_pass, self._events.publish)

    # -- capability and store ---------------------------------------------

    def capability(self) -> GalleryCapability:
        """What this Gallery can do. Measured once, then remembered.

        Remembered because it is read on every request, and `find_spec` walks
        the import path each time. Nothing it depends on changes without a
        restart -- installing a package into a running Studio does not make it
        importable in an already-warm process anyway.
        """

        with self._lock:
            if self._capability is None:
                probe = self._probe
                if probe is None:
                    writable, reason = self._storage_state()
                    probe = GalleryProbe.measure(
                        storage_writable=writable, storage_reason=reason
                    )
                self._capability = decide(probe)
            return self._capability

    def _storage_state(self) -> tuple[bool, str | None]:
        """Whether the Gallery can write where it needs to."""

        try:
            location = self._state_root / "gallery"
            location.mkdir(parents=True, exist_ok=True)
            probe = location / ".writable"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as error:
            return False, (
                "The Gallery cannot write to the Studio state folder "
                f"({type(error).__name__}), so it has nowhere to keep its "
                "index."
            )
        return True, None

    def store(self) -> GalleryStore:
        with self._lock:
            if self._closed:
                # Background work outlives a request, so a worker that wakes
                # after shutdown would otherwise RE-OPEN the database this
                # service just let go of -- re-creating the state tree and, on
                # Windows, re-locking the file `close()` exists to release.
                raise GalleryStoreError("The Gallery has been closed.")
            if self._store is None:
                self._store = GalleryStore(self._state_root).open()
                self._adopt_result_root(self._store)
            return self._store

    def _adopt_result_root(self, store: GalleryStore) -> None:
        """Make Studio's own output directory a scan folder, once.

        NG-1. Nothing registered it, so a fresh install generated an image and
        showed an empty Gallery with no hint that a folder needed linking.
        Measured on the owner's own install before this existed: both
        `/scan-folders` and `/folders` answered `[]`.

        WHY HERE. `store()` is the single funnel -- the Gallery tab and
        `note_generation()` both reach the database through it -- so one hook
        covers "the owner opened the Gallery" and "the owner generated
        something" without either path knowing about the other. It runs inside
        the same lock that creates the store, so it cannot race a second
        opener, and it is handed the store it was built with rather than
        calling back into `store()`.

        THE EXTENSION DOES THE SAME THING FROM ITS SAVE PIPELINE.
        `studio_gallery.py register_scan_folder` is `INSERT OR IGNORE`, returns
        whether a row was actually inserted, and is documented never to raise
        into the save path. Studio registers from the store instead of from the
        save because Studio's autosync is already call-driven rather than
        watcher-driven, but the contract is the Extension's: idempotent, and
        never able to cost the owner a picture.

        IDEMPOTENT TWICE OVER: `scan_folders.path` is `NOT NULL UNIQUE` and
        `add_folder` is `INSERT OR IGNORE`, so repeated launches cannot
        duplicate the row, and a root the owner has already linked by hand is
        left exactly as it was.

        NEVER RAISES. A Gallery that cannot adopt a folder must not stop the
        Gallery opening, and must not stop a generation being delivered.
        """

        root = self._result_root
        if root is None:
            return
        try:
            if not root.is_dir():
                # Once. This runs on every store open, and a missing output
                # directory would otherwise fill the log with the same line.
                if not self._result_root_warned:
                    self._result_root_warned = True
                    logger.warning(
                        "The Studio results folder is not available, so new "
                        "images will not appear in the Gallery until it is. "
                        "Check the result_root setting."
                    )
                return
            GalleryScanner(store).add_folder(root, root.name)
        except Exception:  # noqa: BLE001 - see the docstring
            return

    def thumbnails(self) -> ThumbnailCache:
        with self._lock:
            if self._thumbnails is None:
                self._thumbnails = ThumbnailCache(
                    self.store().location.thumbnails
                )
            return self._thumbnails

    def record_generation(self, content_hash: str, infotext: str,
                          settings: dict[str, Any] | None = None) -> bool:
        """Remember what a just-generated image was made with.

        NEVER RAISES, and that is the point. This is called from the
        generation path, and the Gallery is not allowed to take generation
        down with it -- an owner whose picture was made successfully must not
        be told it failed because an index could not be written.

        Returns whether it was recorded, so a caller that cares can tell.
        """

        if not content_hash:
            return False
        if not self.capability().available:
            return False
        try:
            gallery_actions.record_generation(
                self.store(), content_hash, infotext, settings
            )
        except Exception:  # noqa: BLE001
            return False
        return True

    def note_generation(self) -> None:
        """Studio just made a picture. Look for it, once the batch settles.

        NEVER RAISES, for the same reason `record_generation` does not: this is
        called from the generation path, and an owner whose picture was made
        must not be told it failed because a Gallery index could not be
        refreshed.

        No path is passed, and none is needed. `record_generation` is keyed by
        content hash because metadata is written before any scan sees the file,
        and the adapter's delivery handle is not a filesystem path either. What
        the Gallery does have is the folders the owner linked, and an
        incremental scan of those is 0.25 s on a 17,000 image library -- so the
        cheap, honest answer is to look where the owner said to look.
        """

        try:
            if not self.capability().available:
                return
            self._autosync.notify("generation")
        except Exception:  # noqa: BLE001
            return

    def _sync_pass(self) -> tuple[int, int] | None:
        """One background scan. Returns `(added, removed)`, or None.

        None means the pass could not run and nothing should be published:
        the service is closed, the Gallery is unavailable, or a scan the owner
        started is already going -- in which case `/scan` answers that owner
        directly and a second reload would only fight it.
        """

        with self._scan_lock:
            if self._scan.running:
                return None
            try:
                scanner = GalleryScanner(
                    self.store(),
                    cancel=self._scan.begin(),
                    on_progress=self._scan.record,
                )
            except GalleryStoreError:
                self._scan.finish()
                return None
        try:
            result = scanner.scan()
        except GalleryStoreError:
            return None
        finally:
            self._scan.finish()
        if result.cancelled:
            return None
        if result.added:
            # The same promise `/scan` keeps: an image that has just appeared
            # gets its dimensions, its metadata text and its fingerprint, so
            # the grid is not a row of tiles with nothing behind them.
            self._start_hashing()
        return result.added, result.removed

    def watcher_status(self) -> dict[str, Any]:
        """What the page's auto-sync indicator is told, and why.

        `active` keeps the meaning the Extension gives it and the meaning the
        page renders: a filesystem observer is running right now
        (`studio_gallery.py:2485`). Studio updating the Gallery because Studio
        itself made an image is a different thing, and claiming the same green
        dot for it would tell an owner their folders are being watched when
        nothing is watching them.

        So the dot stays honest and the REASON carries the rest. The page shows
        it as the indicator's tooltip.
        """

        watching = self.capability().feature("auto_sync")
        with self._lock:
            closed = self._closed
        # `capability()` is measured once and remembered, so it goes on
        # reporting an available Gallery after `close()` has let go of the
        # database. A stream still open across shutdown must not be told that
        # images will keep arriving.
        if closed or not self.capability().available:
            # Every other Gallery route is answering 503 right now. `/events`
            # is the one that bypasses the refusal, so it must not be the one
            # place that implies the Gallery is working.
            return {"active": False, "reason": watching.reason,
                    "generation_sync": False}
        return {
            "active": False,
            "reason": (
                "New images Studio makes appear here on their own. "
                + str(watching.reason or "")
            ).strip(),
            "generation_sync": True,
        }

    def close(self) -> None:
        """Release the database handle and stop any scan.

        Never raises. This runs from `server_close`, so a store that cannot
        close cleanly must not stop Studio shutting down -- and the handle is
        dropped either way, which is what actually matters on Windows where a
        live one keeps the file locked.
        """

        # Outside the lock, and joined. The worker takes `self._lock` itself
        # through `store()` and `capability()`, so joining while holding it
        # would deadlock -- an RLock is reentrant per thread, not across them.
        self._autosync.stop()
        self._events.close()
        with self._lock:
            self._closed = True
            self._scan.cancel()
            store, self._store = self._store, None
            if store is not None:
                try:
                    store.close()
                except Exception:  # noqa: BLE001
                    pass

    # -- dispatch ----------------------------------------------------------

    def handles(self, path: str) -> bool:
        return _route(path)[0] is not None

    def get(self, path: str) -> Reply:
        route, query = _route(path)
        if route is None:
            return _not_found()
        refusal = self._unavailable()
        if refusal is not None:
            return refusal
        try:
            return self._get(route, query)
        except GalleryStoreError as error:
            return _refusal(str(error))

    def post(self, path: str, payload: Any = None) -> Reply:
        route, _ = _route(path)
        if route is None:
            return _not_found()
        refusal = self._unavailable()
        if refusal is not None:
            return refusal
        try:
            return self._post(route, payload if isinstance(payload, dict) else {})
        except GalleryStoreError as error:
            return _refusal(str(error))

    def _unavailable(self) -> Reply | None:
        capability = self.capability()
        if capability.available:
            return None
        return _refusal(
            capability.reason or "The Gallery is unavailable."
        )

    # -- reads -------------------------------------------------------------

    def _get(self, route: str, query: dict[str, str]) -> Reply:
        if route == "/capability":
            return Reply(payload=self.capability().to_dict())
        if route == "/stats":
            return Reply(payload=gallery_query.statistics(self.store()))
        if route == "/scan-folders":
            return Reply(payload=self._scan_folders_for_browser())
        if route == "/folders":
            return Reply(payload=self._folders())
        if route == "/images":
            return Reply(payload=gallery_query.list_images(
                self.store(), ImageQuery.from_request(query)))
        if route == "/suggest":
            return Reply(payload=gallery_query.suggest(
                self.store(), query.get("q", "")))
        if route == "/characters":
            return Reply(payload=self._characters(query.get("folder", "")))
        if route == "/ignore-words":
            return Reply(payload=self._ignore_words())
        if route == "/trash":
            return Reply(payload=gallery_actions.list_trash(self.store()))
        if route == "/scan-progress":
            return Reply(payload=self._scan.status())
        if route == "/hash-status":
            return Reply(payload=self._hash_status())
        if route == "/duplicates":
            return self._duplicates(query)

        parts = [part for part in route.split("/") if part]
        if parts[:1] == ["similar"] and len(parts) == 2:
            return self._similar(parts[1], query)
        if parts[:1] == ["image"] and len(parts) >= 2:
            return self._image_route(parts)
        if parts[:1] == ["thumb"] and len(parts) == 2:
            return self._thumbnail(parts[1])
        if parts[:1] == ["full"] and len(parts) == 2:
            return self._full(parts[1])
        return _not_found()

    def _image_route(self, parts: list[str]) -> Reply:
        try:
            image_id = int(parts[1])
        except ValueError:
            return _not_found()
        if len(parts) == 2:
            found = gallery_query.image(self.store(), image_id)
            return Reply(payload=found) if found else _not_found()
        if parts[2] == "metadata":
            return self._metadata(image_id)
        return _not_found()

    def _metadata(self, image_id: int) -> Reply:
        """The parameters an image was made with.

        THE FILE FIRST, then the database. Most images in a library were never
        generated by this Studio -- they were scanned -- so their parameters
        live in a PNG text chunk and in nothing else. A database-only lookup
        answers `{}` for every one of them, which is what a live run of this
        exact route showed while every unit test passed: the tests inserted the
        row they then read back.

        The database is the fallback rather than the authority because it holds
        what the file cannot. Metadata stripping deliberately leaves those rows
        alone, so an owner who strips a file still sees its prompt here; and a
        row saved by generation is keyed by content hash before any scan has
        linked it to an id, so it is looked up both ways.

        `_source` says which answered. `gallery.js` already skips it when
        listing "other" metadata, and it is the difference between "this file
        carries no parameters" and "we did not look properly".
        """

        with self.store().read() as connection:
            row = connection.execute(
                "SELECT filepath, content_hash FROM images WHERE id = ?",
                (image_id,),
            ).fetchone()
        if row is None:
            return _not_found()

        from .gallery_index import read_metadata

        embedded = read_metadata(str(row["filepath"]))
        if embedded.get("prompt") or embedded.get("raw_parameters"):
            return Reply(payload={**embedded, "_source": "embedded"})

        stored = self._stored_metadata(image_id, str(row["content_hash"] or ""))
        if stored is not None:
            return Reply(payload={**stored, "_source": "stored"})
        return Reply(payload={**embedded, "_source": "none"})

    def _stored_metadata(self, image_id: int,
                         content_hash: str) -> dict[str, Any] | None:
        with self.store().read() as connection:
            row = connection.execute(
                "SELECT * FROM image_metadata WHERE image_id = ?", (image_id,)
            ).fetchone()
            if row is None and content_hash:
                # Still keyed only by hash: generation saved it before the scan
                # reached the file, or the owner renamed the file since.
                row = connection.execute(
                    "SELECT * FROM image_metadata WHERE content_hash = ?",
                    (content_hash,),
                ).fetchone()
        if row is None:
            return None
        return {
            key: row[key] for key in row.keys()
            if row[key] is not None
            and key not in ("id", "image_id", "content_hash")
        }

    def _scan_folders_for_browser(self) -> list[dict[str, Any]]:
        """The linked folders, named but not located.

        The module contract above is that bytes are served by handle and a path
        never crosses the wire. This route was the exception: it answered with
        the absolute path of every linked folder, which describes the owner's
        directory layout to anything that can read the response.

        Nothing wanted it. `gallery.js` reads this payload in exactly two
        places -- it assigns it, and it takes `.length` to decide whether the
        Gallery has been set up. The path was never rendered and never sent
        back. Adding Studio's own result root (NG-1) would have put the
        install directory on the wire for the first time, so the projection is
        narrowed here rather than after someone notices.

        The id stays, because it is the durable handle a future
        remove-by-handle flow needs, and the label stays because it is what a
        human reads.
        """

        return [
            {"id": row["id"], "label": row["label"]}
            for row in GalleryScanner(self.store()).folders()
        ]

    def _folders(self) -> list[dict[str, Any]]:
        with self.store().read() as connection:
            rows = connection.execute(
                "SELECT folder, COUNT(*) AS image_count FROM images "
                "GROUP BY folder ORDER BY natural_key(folder)"
            ).fetchall()
        return [{"folder": str(row["folder"]),
                 "image_count": int(row["image_count"])} for row in rows]

    def _characters(self, folder: str) -> list[dict[str, Any]]:
        """The tag sidebar: character names, and the star ratings beside them.

        `gallery.js` calls `.filter()` on whatever comes back, so a 404 here
        does not fail politely -- the error object it receives instead has no
        `.filter`, the exception escapes `init()`, and the ENTIRE panel renders
        blank. That is what a browser run showed while every route this suite
        knew about answered 200.

        Ratings ride in the same list as pseudo-tags with NEGATIVE ids, which
        is the Extension's trick for putting them in one sidebar without
        colliding with a real character's id.
        """

        conditions = ""
        parameters: list[Any] = []
        if folder:
            # GLOB, matching the image query: LIKE would read an underscore in
            # the owner's folder name as a wildcard.
            conditions = " WHERE i.folder = ? OR i.folder GLOB ?"
            parameters = [folder, f"{folder}\\*"]

        with self.store().read() as connection:
            rows = connection.execute(
                "SELECT c.id, c.name, COUNT(DISTINCT ic.image_id) AS image_count "
                "FROM characters c "
                "JOIN image_characters ic ON c.id = ic.character_id "
                "JOIN images i ON ic.image_id = i.id"  # noqa: S608
                f"{conditions} GROUP BY c.id "
                "ORDER BY c.name COLLATE NOCASE ASC",
                parameters,
            ).fetchall()
            rated = connection.execute(
                "SELECT rating, COUNT(*) AS image_count FROM images i "  # noqa: S608
                + ("WHERE (i.folder = ? OR i.folder GLOB ?) AND rating > 0"
                   if folder else "WHERE rating > 0")
                + " GROUP BY rating ORDER BY rating",
                parameters,
            ).fetchall()

        characters = [dict(row) for row in rows]
        # "Unknown" first: it is the bucket every untagged image falls into, so
        # it is the one an owner reaches for most.
        unknown = [c for c in characters if str(c["name"]).lower() == "unknown"]
        rest = [c for c in characters if str(c["name"]).lower() != "unknown"]
        stars = [
            {"id": -int(row["rating"]), "name": "★" * int(row["rating"]),
             "image_count": int(row["image_count"]), "rating": int(row["rating"])}
            for row in rated
        ]
        return stars + unknown + rest

    def events(self) -> Iterator[bytes]:
        """The auto-sync stream, as Server-Sent Events.

        `gallery.js` opens an `EventSource` here and reconnects every five
        seconds for as long as it fails, so a missing route is not a quiet
        gap -- it is a request every five seconds for the life of the tab.

        This used to be the whole story: one status frame, then keepalive
        comments forever. The page listens for `sync` and turns it into a grid
        reload, so with nothing able to speak into this stream a generated
        image sat on disk -- correctly written, inside a folder the owner had
        linked -- until the owner pressed refresh. The keepalives made the
        connection look healthy the entire time.

        Now the stream drains a subscriber queue. The blocking `get` is safe
        and already precedented here: the server is a `ThreadingHTTPServer`
        with daemon threads, so this owns one connection thread and shutdown
        does not wait for it. `EventBus.close()` pushes a sentinel so a parked
        stream still wakes when Studio stops.
        """

        yield frame("watcher_status", self.watcher_status())
        channel = self._events.subscribe()
        try:
            while True:
                try:
                    message = channel.get(timeout=EVENT_KEEPALIVE_SECONDS)
                except queue.Empty:
                    # A comment, not an event: it keeps proxies and idle
                    # timeouts from closing the stream without waking any
                    # listener on the page. It is also how a closed tab is
                    # noticed -- nothing reads the socket, so a dead client
                    # surfaces only when a write finally fails.
                    yield b": keepalive\n\n"
                    continue
                if message is CLOSED:
                    return
                yield message
        finally:
            self._events.unsubscribe(channel)

    def _ignore_words(self) -> list[str]:
        with self.store().read() as connection:
            return [
                str(row["word"]) for row in connection.execute(
                    "SELECT word FROM ignore_words ORDER BY word"
                )
            ]

    # -- bytes -------------------------------------------------------------

    def _path_of(self, image_id_text: str) -> str | None:
        try:
            image_id = int(image_id_text)
        except ValueError:
            return None
        with self.store().read() as connection:
            row = connection.execute(
                "SELECT filepath FROM images WHERE id = ?", (image_id,)
            ).fetchone()
        return str(row["filepath"]) if row else None

    def _thumbnail(self, image_id_text: str) -> Reply:
        path = self._path_of(image_id_text)
        if path is None:
            return _not_found()
        found = self.thumbnails().thumbnail_for(path)
        if found is None:
            # The file is indexed but cannot be turned into a picture. A 404
            # is the honest answer: the page draws its broken-image state,
            # which is what a broken image should look like.
            return _not_found("This image could not be read.")
        headers = {"ETag": found.etag}
        headers["Cache-Control"] = (
            THUMBNAIL_CACHE_CONTROL if found.cacheable else "no-cache"
        )
        return Reply(body=found.data, media_type=found.media_type,
                     headers=headers)

    def _full(self, image_id_text: str) -> Reply:
        path = self._path_of(image_id_text)
        if path is None:
            return _not_found()
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            return _not_found()
        try:
            data = source.read_bytes()
        except OSError:
            return _not_found("This image could not be read.")
        return Reply(
            body=data,
            # Never guessed from the bytes. An unknown extension is served as
            # a download rather than as something the page might execute.
            media_type=_MEDIA_TYPES.get(suffix, "application/octet-stream"),
            headers={
                "Content-Disposition": _disposition(source.name),
                "Cache-Control": "private, max-age=3600",
            },
        )

    # -- writes ------------------------------------------------------------

    def _post(self, route: str, payload: dict[str, Any]) -> Reply:
        if route == "/scan":
            return self._start_scan()
        if route == "/compute-hashes":
            return self._start_hashing()
        if route == "/scan/cancel":
            self._scan.cancel()
            return Reply(payload={"ok": True})
        if route == "/pick-folder":
            return self._pick_folder()
        if route == "/unlink-folder":
            return self._unlink(payload)
        # `/scan-folders` is what `gallery.js` POSTs to register a folder --
        # `/add-folder` was this module's own invention and nothing calls it.
        # Both answer, because the live harness and any owner script that has
        # already learned the second one should keep working.
        if route in ("/scan-folders", "/add-folder"):
            path = str(payload.get("path") or "")
            if not path:
                return _refusal("No folder was named.",
                                int(HTTPStatus.BAD_REQUEST))
            if not Path(path).is_dir():
                return _refusal("There is no folder there.",
                                int(HTTPStatus.BAD_REQUEST))
            GalleryScanner(self.store()).add_folder(
                path, str(payload.get("label") or "") or None)
            return Reply(payload={"ok": True})
        if route == "/ignore-words":
            return self._act(gallery_actions.set_ignore_word,
                             str(payload.get("word") or ""), True)
        if route == "/trash/empty":
            return self._act(gallery_actions.empty_trash)

        if route == "/rescan-characters":
            return self._act(gallery_actions.rescan_characters)
        if route == "/next-number":
            return self._act(gallery_actions.next_number,
                             str(payload.get("base_name") or ""),
                             payload.get("exclude_ids") or [],
                             str(payload.get("media_type") or ""))
        if route == "/create-folder":
            return self._act(gallery_actions.create_folder,
                             str(payload.get("parent") or ""),
                             str(payload.get("name") or ""))
        if route == "/delete-folder":
            return self._act(gallery_actions.delete_folder,
                             str(payload.get("folder") or ""))
        if route == "/rename-folder":
            return self._act(gallery_actions.rename_folder,
                             str(payload.get("folder") or ""),
                             str(payload.get("new_name") or ""))
        if route == "/folders/bulk-delete":
            return self._over(payload.get("folders") or [],
                              gallery_actions.delete_folder)
        if route == "/folders/bulk-move":
            return self._move_folders(payload)
        if route == "/bulk-restore":
            return self._over(payload.get("trash_ids") or [],
                              gallery_actions.restore)
        if route.startswith("/images/"):
            return self._bulk(route[len("/images/"):], payload)

        parts = [part for part in route.split("/") if part]
        if parts[:1] == ["restore"] and len(parts) == 2:
            return self._act_on_id(gallery_actions.restore, parts[1])
        if parts[:1] == ["image"] and len(parts) == 3:
            return self._image_action(parts[1], parts[2], payload)
        return _not_found()

    def _image_action(self, image_id_text: str, action: str,
                      payload: dict[str, Any]) -> Reply:
        try:
            image_id = int(image_id_text)
        except ValueError:
            return _not_found()
        if action == "rating":
            return self._act(gallery_actions.set_rating, image_id,
                             payload.get("rating", 0))
        if action == "add-tag":
            return self._act(gallery_actions.add_tag, image_id,
                             str(payload.get("name") or ""))
        if action == "remove-tag":
            return self._act(gallery_actions.remove_tag, image_id,
                             str(payload.get("name") or ""))
        if action == "rename":
            return self._act(gallery_actions.rename, image_id,
                             str(payload.get("filename")
                                 or payload.get("name") or ""))
        if action == "delete":
            return self._act(gallery_actions.delete_to_trash, image_id)
        if action == "move":
            return self._act(gallery_actions.move, image_id,
                             str(payload.get("folder") or ""))
        if action == "remove-metadata":
            return self._act(gallery_actions.strip_metadata, image_id)
        if action in ("open-explorer", "open-file"):
            return self._reveal(image_id, action == "open-file")
        return _not_found()

    def _reveal(self, image_id: int, open_it: bool) -> Reply:
        """Show a file in the system's own file manager, or open it.

        Through `native_actions`, which already probes what this machine can do
        and refuses with a reason where there is no desktop. Growing a second
        way to launch a program from a path would mean two places to get the
        containment wrong.
        """

        with self.store().read() as connection:
            row = connection.execute(
                "SELECT filepath FROM images WHERE id = ?", (image_id,)
            ).fetchone()
        if row is None:
            return _not_found()

        from .native_actions import NativeActions

        path = Path(str(row["filepath"]))
        actions = NativeActions()
        if not actions.capability.available:
            return _refusal(
                actions.capability.reason
                or "This machine cannot open a file manager."
            )
        try:
            if open_it:
                # Only the file types the Gallery indexes. Handing a file to
                # whatever program is registered for it is the one place an
                # executable must never be able to arrive.
                actions.open_file(path, MEDIA_SUFFIXES)
            else:
                # `reveal` points a file manager at a DIRECTORY, so "show me
                # this image" means its folder.
                actions.reveal(path.parent)
        except Exception as error:  # noqa: BLE001
            reason = getattr(error, "message", None) or str(error)
            return _refusal(reason or "That could not be opened.")
        return Reply(payload={"ok": True})

    # -- many at once ------------------------------------------------------

    def _bulk(self, action: str, payload: dict[str, Any]) -> Reply:
        identifiers = payload.get("ids") or []
        if action == "tag-info":
            return self._tag_info(identifiers)
        if action == "bulk-add-tag":
            return self._over(identifiers, gallery_actions.add_tag,
                              str(payload.get("tag") or ""))
        if action == "bulk-remove-tag":
            return self._over(identifiers, gallery_actions.remove_tag,
                              str(payload.get("tag") or ""))
        if action == "bulk-delete":
            return self._over(identifiers, gallery_actions.delete_to_trash)
        if action == "bulk-move":
            return self._over(identifiers, gallery_actions.move,
                              str(payload.get("folder") or ""))
        if action == "bulk-copy":
            return self._over(identifiers, gallery_actions.copy_to,
                              str(payload.get("folder") or ""))
        if action == "bulk-remove-metadata":
            return self._over(identifiers, gallery_actions.strip_metadata)
        if action == "bulk-convert":
            return self._over(
                identifiers, gallery_actions.convert,
                str(payload.get("target_format") or ""),
                payload.get("quality", 90),
                bool(payload.get("lossless")),
                bool(payload.get("keep_original")),
            )
        if action == "bulk-rename":
            return self._bulk_rename(payload)
        return _not_found()

    def _over(self, items: Any, operation: Any, *arguments: Any) -> Reply:
        """Run one operation over many targets, and report each outcome.

        NOT all-or-nothing. Forty files are selected and one of them is open in
        another program; refusing the whole batch for that leaves the owner to
        find which one and try again. Each failure is named instead, so the
        page can say what did not happen and to which picture.
        """

        done = 0
        failures: list[dict[str, Any]] = []
        for item in items:
            try:
                operation(self.store(), int(item), *arguments)
                done += 1
            except (gallery_actions.ActionRefused, GalleryStoreError) as error:
                failures.append({"id": item, "error": str(error)})
            except (TypeError, ValueError):
                failures.append({"id": item, "error": "That is not an image."})
        return Reply(payload={"ok": not failures, "done": done,
                              "failed": len(failures), "failures": failures})

    def _bulk_rename(self, payload: dict[str, Any]) -> Reply:
        """Number a set of pictures under one name: `Aria 001`, `Aria 002`."""

        base = str(payload.get("base_name") or "").strip()
        identifiers = payload.get("ids") or []
        if not base:
            return _refusal("A series needs a name.",
                            int(HTTPStatus.BAD_REQUEST))
        start = 1
        if payload.get("continue_numbering"):
            try:
                start = int(gallery_actions.next_number(
                    self.store(), base, identifiers)["next"])
            except gallery_actions.ActionRefused:
                start = 1

        done = 0
        failures: list[dict[str, Any]] = []
        for offset, item in enumerate(identifiers):
            try:
                image_id = int(item)
                suffix = Path(_filename_of(self.store(), image_id)).suffix
                gallery_actions.rename(
                    self.store(), image_id,
                    f"{base} {start + offset:03d}{suffix}")
                done += 1
            except (gallery_actions.ActionRefused, GalleryStoreError,
                    TypeError, ValueError) as error:
                failures.append({"id": item, "error": str(error)})
        return Reply(payload={"ok": not failures, "done": done,
                              "failed": len(failures), "failures": failures,
                              "next": start + done})

    def _move_folders(self, payload: dict[str, Any]) -> Reply:
        """Move whole folders' images into one destination."""

        target = str(payload.get("target") or "")
        if not target:
            return _refusal("No destination folder was named.",
                            int(HTTPStatus.BAD_REQUEST))
        identifiers: list[int] = []
        with self.store().read() as connection:
            for folder in payload.get("folders") or []:
                identifiers.extend(
                    int(row["id"]) for row in connection.execute(
                        "SELECT id FROM images WHERE folder = ? "
                        "OR folder LIKE ?", (folder, f"{folder}\\%"))
                )
        return self._over(identifiers, gallery_actions.move, target)

    def _tag_info(self, identifiers: Any) -> Reply:
        """Every tag on the selection, with how many of it wears each one.

        `{"tags": [{"name", "count"}], "total": n}` -- the page's shape. It
        does the common/partial split itself, comparing each count against the
        total, so it needs the COUNTS and not a verdict.

        A first version answered `{"common": [...], "partial": [...]}` and the
        tag editor showed "No tags on any selected image" over two images that
        were both tagged: it read `info.tags`, found nothing there, and said so
        truthfully about a field that was never sent.
        """

        wanted = [int(item) for item in identifiers if str(item).isdigit()]
        if not wanted:
            return Reply(payload={"tags": [], "total": 0})
        placeholders = ", ".join("?" for _ in wanted)
        with self.store().read() as connection:
            rows = connection.execute(
                "SELECT c.name AS name, COUNT(DISTINCT ic.image_id) AS held "
                "FROM characters c "
                "JOIN image_characters ic ON c.id = ic.character_id "
                f"WHERE ic.image_id IN ({placeholders}) "  # noqa: S608
                "GROUP BY c.id ORDER BY c.name COLLATE NOCASE",
                wanted,
            ).fetchall()
        return Reply(payload={
            "tags": [{"name": str(row["name"]), "count": int(row["held"])}
                     for row in rows],
            "total": len(wanted),
        })

    # -- duplicates --------------------------------------------------------

    def _unhashed_count(self) -> int:
        with self.store().read() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM images WHERE media_type != 'video' "
                "AND (phash IS NULL OR phash = '')"
            ).fetchone()[0])

    def _hash_status(self) -> dict[str, Any]:
        """What the Duplicates panel needs to describe itself.

        The field NAMES here are the page's, not this module's. A browser run
        showed the panel reading `All {total} images hashed` with the
        placeholder still in it, because this route answered `active` and
        `unhashed` while `gallery.js` reads `hashing`, `hashed` and `total`.
        Every undefined field it touches becomes a sentence with a hole in it.
        """

        state = self.capability().feature("duplicates")
        if not state.available:
            return {"available": False, "reason": state.reason,
                    "hashing": False, "hashed": 0, "total": 0,
                    "hash_current": 0, "hash_total": 0}

        with self.store().read() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) FROM images WHERE media_type != 'video'"
            ).fetchone()[0])
            hashed = int(connection.execute(
                "SELECT COUNT(*) FROM images WHERE media_type != 'video' "
                "AND phash IS NOT NULL AND phash != ''"
            ).fetchone()[0])

        progress = self._hashing.status()
        current = progress["folders"][0] if progress["folders"] else {}
        return {
            "available": True,
            "reason": None,
            "hashing": bool(progress["active"]),
            "hashed": hashed,
            "total": total,
            "hash_current": int(current.get("current", hashed)),
            "hash_total": int(current.get("total", total)),
            "result": progress["result"],
            "error": progress["error"],
        }

    def _start_hashing(self) -> Reply:
        """Fingerprint every image that has not been fingerprinted yet.

        On a thread and incremental, because a fingerprint costs one decode and
        a library has as many images as it has. Pairs are computed as each
        image is hashed, so the quadratic comparison happens once per image
        against what is already known rather than over the whole library every
        time the Duplicates page is opened.
        """

        state = self.capability().feature("duplicates")
        if not state.available:
            return _refusal(state.reason or "Duplicate detection is off.")
        store = self.store()

        def work(cancel: threading.Event,
                 report: Callable[[ScanProgress], None]) -> Any:
            return _Hashing(store, cancel, report)

        started = self._hashing.start(work)
        return Reply(payload={"ok": True, "started": started,
                              "already_running": not started})

    def _duplicates(self, query: dict[str, str]) -> Reply:
        """Groups of images that are the same picture."""

        state = self.capability().feature("duplicates")
        if not state.available:
            return _refusal(state.reason or "Duplicate detection is off.")
        threshold = _threshold(query)
        with self.store().read() as connection:
            pairs = [
                (int(row["image_a"]), int(row["image_b"]))
                for row in connection.execute(
                    "SELECT image_a, image_b FROM dup_pairs WHERE distance <= ?",
                    (threshold,),
                )
            ]
            # How far apart each pair actually is, so a group can report how
            # alike its members are rather than only that they are "close".
            gaps = {
                (int(row["image_a"]), int(row["image_b"])): int(row["distance"])
                for row in connection.execute(
                    "SELECT image_a, image_b, distance FROM dup_pairs "
                    "WHERE distance <= ?", (threshold,))
            }
            groups = []
            for members in gallery_similarity.group(pairs):
                rows = [
                    gallery_query.image(self.store(), member)
                    for member in members
                ]
                rows = [row for row in rows if row]
                if len(rows) > 1:
                    groups.append({
                        "images": rows,
                        "count": len(rows),
                        "similarity": _similarity_of(members, gaps),
                    })

        # `total_groups` and `total_duplicates` are the PAGE's field names. It
        # interpolates them into its summary line, and without them a browser
        # run showed "undefined group - undefined file (keeping one per group
        # would free NaN files)".
        return Reply(payload={
            "groups": groups,
            "total_groups": len(groups),
            "total_duplicates": sum(group["count"] for group in groups),
            "threshold": threshold,
            "unhashed": self._unhashed_count(),
        })

    def _similar(self, image_id_text: str, query: dict[str, str]) -> Reply:
        """Everything that looks like one image."""

        state = self.capability().feature("duplicates")
        if not state.available:
            return _refusal(state.reason or "Duplicate detection is off.")
        try:
            image_id = int(image_id_text)
        except ValueError:
            return _not_found()
        threshold = _threshold(query)
        with self.store().read() as connection:
            row = connection.execute(
                "SELECT phash FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            if row is None:
                return _not_found()
            target = str(row["phash"] or "")
            if not target:
                return Reply(payload={"images": [], "hashed": False})
            candidates = [
                (int(other["id"]), str(other["phash"] or ""))
                for other in connection.execute(
                    "SELECT id, phash FROM images WHERE id != ? AND phash != ''",
                    (image_id,),
                )
            ]
        found = []
        for other_id, gap in gallery_similarity.near(target, candidates,
                                                     threshold):
            row = gallery_query.image(self.store(), other_id)
            if row:
                found.append({**row, "distance": gap})
        return Reply(payload={"images": found, "hashed": True,
                              "threshold": threshold})

    def _unlink(self, payload: dict[str, Any]) -> Reply:
        """Stop watching a folder, named however the caller knows it.

        `gallery.js` sends `{"folder": "pictures"}` -- the DISPLAY name from
        the sidebar tree, not a path, because a path is the one thing the page
        never has. The first version of this route accepted only `{"path":
        ...}`, so unlinking silently did nothing: the request succeeded, the
        folder stayed, and there was no way to remove it from the UI at all.
        """

        scanner = GalleryScanner(self.store())
        path = str(payload.get("path") or "").strip()
        folder = str(payload.get("folder") or "").strip()
        if not path and folder:
            path = self._path_for_display_folder(folder, scanner)
        if not path:
            return _refusal("No folder was named.",
                            int(HTTPStatus.BAD_REQUEST))
        removed = scanner.remove_folder(path)
        # Rows whose file has gone with the folder, including a scan folder
        # that no longer exists on disk at all.
        with self.store().write() as connection:
            removed += connection.execute(
                "DELETE FROM images WHERE folder = ? OR folder LIKE ?",
                (folder or Path(path).name, f"{folder or Path(path).name}\\%"),
            ).rowcount
        return Reply(payload={"ok": True, "removed": removed})

    def _path_for_display_folder(self, folder: str,
                                 scanner: GalleryScanner) -> str:
        """The configured root behind a sidebar label.

        The label is the scan folder's own name, then its subtree, so the root
        is whatever configured folder that first segment belongs to.
        """

        head = folder.replace("/", "\\").split("\\")[0]
        for configured in scanner.folders():
            if Path(str(configured["path"])).name == head:
                return str(configured["path"])
        return ""

    def _pick_folder(self) -> Reply:
        """Ask the owner for a folder, with the system's own dialog.

        The same choice the Extension makes, and the right one: the owner
        physically selects the folder, which is a stronger guarantee than any
        path they could type. Studio runs on their machine, so the dialog is
        on their screen.

        Refused with a reason where there is no desktop to put a dialog on --
        a container or a remote host -- rather than hanging on a window nobody
        can see.
        """

        result: dict[str, Any] = {}

        def ask() -> None:
            try:
                import tkinter
                from tkinter import filedialog
            except ImportError:
                result["error"] = (
                    "This Studio cannot open a folder chooser because tkinter "
                    "is not installed."
                )
                return
            try:
                window = tkinter.Tk()
                window.withdraw()
                window.wm_attributes("-topmost", 1)
                chosen = filedialog.askdirectory(title="Select image folder")
                window.destroy()
            except Exception as error:  # noqa: BLE001
                result["error"] = (
                    "The folder chooser could not be opened "
                    f"({type(error).__name__}). There may be no desktop "
                    "available on this machine."
                )
                return
            # Cancelled is an empty path and NOT an error: `gallery.js` returns
            # quietly on a blank path and shows a toast on an error, so
            # reporting a cancel as a failure would scold the owner for
            # changing their mind.
            result["path"] = str(chosen or "").replace("/", os.sep)

        # Its own thread, joined here. Tk wants to own whatever thread it is
        # created on, and a server worker thread is reused for the next
        # request.
        worker = threading.Thread(target=ask, name="studio-gallery-pick",
                                  daemon=True)
        worker.start()
        worker.join(timeout=PICK_FOLDER_TIMEOUT_SECONDS)
        if worker.is_alive():
            return _refusal(
                "The folder chooser is still open. Finish choosing there, "
                "then try again.",
                int(HTTPStatus.SERVICE_UNAVAILABLE),
            )
        if "error" in result:
            return _refusal(str(result["error"]),
                            int(HTTPStatus.SERVICE_UNAVAILABLE))
        return Reply(payload={"path": result.get("path", "")})

    def _act_on_id(self, operation: Any, identifier: str) -> Reply:
        try:
            return self._act(operation, int(identifier))
        except ValueError:
            return _not_found()

    def _act(self, operation: Any, *arguments: Any) -> Reply:
        """Run a write and turn its refusal into an answer.

        A refusal is a 400 with the sentence the action wrote, never a 500:
        "there is already a file called that" is something the owner can act
        on, and a stack trace is not.
        """

        try:
            return Reply(payload=operation(self.store(), *arguments))
        except gallery_actions.ActionRefused as error:
            return _refusal(str(error), int(HTTPStatus.BAD_REQUEST))

    def _start_scan(self) -> Reply:
        """Scan, and answer when it is FINISHED.

        Request/response, not start-and-poll. `gallery.js` does this:

        ```js
        const r = await api("/scan", { method: "POST" });
        toast("Scan: " + r.new + " new, " + r.removed + " removed");
        await Promise.all([loadStats(), loadCharacters(), loadFolders(),
                           loadImagesReset()]);
        ```

        so the response IS the completion signal. Returning immediately broke
        both halves at once: the toast read "Scan: undefined new, undefined
        removed", and the reload on the next line ran before anything had been
        indexed -- which is why a freshly linked folder stayed empty until the
        owner pressed refresh. It polls `/scan-progress` on its own timer for
        the progress bar, so the long request is not a silent one.

        The Extension is synchronous here too. This is not a place to be
        cleverer than the client.
        """

        with self._scan_lock:
            if self._scan.running:
                # Asking twice is what an impatient page does. The honest
                # answer is "the one you started is still going".
                return Reply(payload={"ok": True, "already_running": True,
                                      "new": 0, "removed": 0, "total": 0})
            scanner = GalleryScanner(
                self.store(),
                cancel=self._scan.begin(),
                on_progress=self._scan.record,
            )

        try:
            result = scanner.scan()
        except GalleryStoreError as error:
            return _refusal(str(error))
        finally:
            self._scan.finish()

        if not result.cancelled:
            # What the Duplicates panel promises: "Hashing starts
            # automatically after a scan." Started AFTER the answer is
            # computed, so fingerprinting never delays the owner's toast.
            self._start_hashing()

        # `new` and `removed` are the page's names for these.
        return Reply(payload={
            "ok": True,
            "new": result.added,
            "removed": result.removed,
            "total": result.seen,
            "cancelled": result.cancelled,
            "unreadable": result.unreadable,
            "already_running": False,
        })


def _similarity_of(members: list[int], gaps: dict[tuple[int, int], int]) -> int:
    """How alike a duplicate group is, as a percentage.

    The WORST pair in the group, not the average: a group is only as tight as
    its loosest member, and an owner about to delete files should be told the
    weakest link rather than a flattering mean.
    """

    worst = 0
    for index, left in enumerate(members):
        for right in members[index + 1:]:
            gap = gaps.get((min(left, right), max(left, right)))
            if gap is not None:
                worst = max(worst, gap)
    return int(round(100.0 * (1.0 - worst / gallery_similarity.MAX_DISTANCE)))


def _filename_of(store: GalleryStore, image_id: int) -> str:
    with store.read() as connection:
        row = connection.execute(
            "SELECT filename FROM images WHERE id = ?", (image_id,)
        ).fetchone()
    return str(row["filename"]) if row else ""


def _threshold(query: dict[str, str]) -> int:
    """How many differing bits still counts as the same picture."""

    try:
        wanted = int(query.get("threshold", gallery_similarity.DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        wanted = gallery_similarity.DEFAULT_THRESHOLD
    return max(0, min(gallery_similarity.MAX_DISTANCE, wanted))


#: How many images one worker takes at a time. Bigger batches amortise the
#: cost of handing work across a process boundary; smaller ones keep progress
#: moving and cancellation responsive.
ENRICH_CHUNK = 8

#: Below this many pending images, enrichment stays on one core. Starting a
#: pool of processes to read thirty files costs more than it saves.
ENRICH_PARALLEL_THRESHOLD = 64


def enrichment_workers(cores: int | None = None) -> int:
    """How many processes to read images with.

    HALF the machine, capped. Measured on a 28-core host: 8 processes gave
    5.1x, 16 gave 5.8x, 24 dropped back to 4.9x as the hand-off overhead
    overtook the work. Threads managed only 2.5x however many were used,
    because the perceptual hash is pure Python and holds the GIL -- which is
    the whole reason this uses processes at all.

    Half rather than all, because Studio may be making a picture while this
    runs, and an owner would rather their generation kept its cores than have
    their library indexed a minute sooner.
    """

    available = cores if cores is not None else (os.cpu_count() or 1)
    return max(1, min(16, available // 2))


def _read_once(filepath: str) -> tuple[Any, str | None]:
    """One `Image.open`, both the enrichment facts and the perceptual hash."""

    try:
        from PIL import Image
    except ImportError:
        return gallery_index.enrich(filepath), None
    try:
        with Image.open(filepath) as image:
            facts = gallery_index.enrich(filepath, image=image)
            found = gallery_similarity.fingerprint(image)
        return facts, found
    except (OSError, ValueError, TypeError):
        return gallery_index.enrich(filepath), None


class _Hashing:
    """Fills in everything the scan deliberately skipped, ONE decode each.

    The scan reads headers so an owner sees seventeen thousand pictures in
    seconds. This is where the twenty-six milliseconds a file actually costs
    get spent: the embedded parameters, the pixel hash and the perceptual hash,
    all derived from a single `Image.open` rather than the three the first
    version used.

    Shaped like a scanner so `ScanRunner` can drive it -- same `scan()` entry,
    same progress, same cancel, same crash reporting.
    """

    def __init__(self, store: GalleryStore, cancel: threading.Event,
                 report: Callable[[ScanProgress], None]) -> None:
        self._store = store
        self._cancel = cancel
        self._report = report
        #: How this pass actually read the images. Reported rather than
        #: assumed: the pool falls back to one core on any failure, and a
        #: silent fallback is indistinguishable from a pool that never
        #: started -- which is exactly how a test can pass while proving
        #: nothing about the thing it claims to test.
        self.parallel = False
        self.workers = 0
        #: Images genuinely read in a worker process, and images the pool
        #: handed back as failures and this process had to read itself.
        self.in_pool = 0
        self.recovered = 0

    def scan(self) -> Any:
        with self._store.read() as connection:
            pending = [
                (int(row["id"]), str(row["filepath"]))
                for row in connection.execute(
                    "SELECT id, filepath FROM images "
                    "WHERE media_type != 'video' "
                    "AND (phash IS NULL OR phash = '') ORDER BY id"
                )
            ]
            known = [
                (int(row["id"]), str(row["phash"]))
                for row in connection.execute(
                    "SELECT id, phash FROM images WHERE phash IS NOT NULL "
                    "AND phash != ''"
                )
            ]

        total = len(pending)
        done = failed = paired = linked = 0
        self._report(ScanProgress("details", 0, total, "Reading"))
        for image_id, filepath, facts, found in self._read_all(pending):
            if self._cancel.is_set():
                return _HashResult(done, failed, paired, total, True, linked,
                                   self.in_pool > 0, self.workers,
                                   self.in_pool, self.recovered)
            done += 1
            if found is None:
                failed += 1

            matches = (gallery_similarity.near(found, known)
                       if found is not None else [])
            try:
                with self._store.write() as connection:
                    connection.execute(
                        "UPDATE images SET phash = ?, content_hash = ?, "
                        "search_text = ?, width = ?, height = ? WHERE id = ?",
                        (
                            # A single space for a file that could not be
                            # hashed: not valid hex, so it never matches
                            # anything, and never retried for ever either.
                            found if found is not None else " ",
                            facts.content_hash,
                            facts.search_text or " ",
                            facts.width or None,
                            facts.height or None,
                            image_id,
                        ),
                    )
                    if facts.content_hash:
                        # The row generation left behind before this file had
                        # ever been scanned.
                        linked += connection.execute(
                            "UPDATE image_metadata SET image_id = ? "
                            "WHERE content_hash = ? AND image_id IS NULL",
                            (image_id, facts.content_hash),
                        ).rowcount
                    for other_id, gap in matches:
                        low, high = sorted((image_id, other_id))
                        connection.execute(
                            "INSERT OR REPLACE INTO dup_pairs(image_a, "
                            "image_b, distance) VALUES(?, ?, ?)",
                            (low, high, gap))
                        paired += 1
            except GalleryStoreError:
                failed += 1
                continue

            if found is not None:
                known.append((image_id, found))
            if done % 10 == 0 or done == total:
                self._report(ScanProgress("details", done, total, "Reading"))
        self._report(ScanProgress("details", done, total, "Done"))
        return _HashResult(done, failed, paired, total, False, linked,
                           self.in_pool > 0, self.workers,
                           self.in_pool, self.recovered)

    def _read_all(self, pending: list[tuple[int, str]]) -> Iterator[
        tuple[int, str, Any, str | None]
    ]:
        """Decode the pending images, across cores where that is worth it.

        Yields in COMPLETION order, not submission order. Nothing downstream
        depends on the sequence -- each row is written by id -- and waiting for
        a slow image to keep the order would idle every other worker.

        Falls back to one core on any pool failure. A machine that refuses to
        fork must end up with a slower Gallery, never a broken one.
        """

        if len(pending) < ENRICH_PARALLEL_THRESHOLD:
            for image_id, filepath in pending:
                if self._cancel.is_set():
                    return
                facts, found = _read_once(filepath)
                yield image_id, filepath, facts, found
            return

        from concurrent.futures import ProcessPoolExecutor, as_completed

        workers = enrichment_workers()
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                waiting = {
                    pool.submit(_read_once, filepath): (image_id, filepath)
                    for image_id, filepath in pending
                }
                self.workers = workers
                for future in as_completed(waiting):
                    image_id, filepath = waiting[future]
                    if self._cancel.is_set():
                        # Drop everything still queued. `cancel()` only works
                        # on work that has not started, which is exactly the
                        # backlog worth abandoning.
                        for pending_future in waiting:
                            pending_future.cancel()
                        return
                    try:
                        facts, found = future.result()
                        self.in_pool += 1
                    except Exception:  # noqa: BLE001
                        # That worker could not do it. Read it here rather
                        # than lose the image -- and COUNT it, so a pool whose
                        # every task failed cannot report itself as parallel.
                        self.recovered += 1
                        facts, found = _read_once(filepath)
                    yield image_id, filepath, facts, found
        except Exception:  # noqa: BLE001
            # A pool that cannot start -- no fork, a sandbox, an exhausted
            # handle table -- must not stop the library being read.
            self.parallel = False
            self.workers = 0
            for image_id, filepath in pending:
                if self._cancel.is_set():
                    return
                facts, found = _read_once(filepath)
                yield image_id, filepath, facts, found


@dataclass
class _HashResult:
    hashed: int
    failed: int
    pairs: int
    total: int
    cancelled: bool
    linked: int = 0
    parallel: bool = False
    workers: int = 0
    in_pool: int = 0
    recovered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"hashed": self.hashed, "failed": self.failed,
                "pairs": self.pairs, "total": self.total,
                "cancelled": self.cancelled, "linked": self.linked,
                "parallel": self.parallel, "workers": self.workers,
                "in_pool": self.in_pool, "recovered": self.recovered}


def _disposition(filename: str) -> str:
    """`inline`, with the original name for Save-As and drag-out.

    Inline rather than attachment: a lightbox `<img src>` and a direct visit
    should both render. The filename is a hint for the drop target, which
    otherwise names the file after the numeric id in the URL.
    """

    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
    return (
        f'inline; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _route(path: str) -> tuple[str | None, dict[str, str]]:
    """Split a request path into a Gallery route and its query."""

    split = urlsplit(path)
    if not split.path.startswith(ROUTE_PREFIX):
        return None, {}
    route = unquote(split.path[len(ROUTE_PREFIX):]) or "/"
    values = {
        key: found[0]
        for key, found in parse_qs(split.query, keep_blank_values=True).items()
    }
    return route, values


__all__ = (
    "ROUTE_PREFIX",
    "THUMBNAIL_CACHE_CONTROL",
    "EVENT_KEEPALIVE_SECONDS",
    "GalleryService",
    "Reply",
    "ScanRunner",
)
