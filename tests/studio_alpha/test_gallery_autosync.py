"""A generated image reaching an open Gallery without the owner pressing refresh.

The owner's report: "when generating a new image, it ALSO doesn't automatically
update the gallery if the folder is linked in advance of generating. You have to
refresh."

The cause was not subtle once found. `gallery.js` listens for a `sync` event and
turns it into a grid reload, and the server had no way to send one: `events()`
yielded one status frame and then keepalive comments forever. The keepalives
made the connection look healthy the whole time, which is why nothing pointed at
it.

So this suite tests the CHAIN, not the pieces, because every piece already
worked:

    _canonical_result -> note_generation -> AutoSync -> scan -> publish -> events

One test here exists for a specific near-miss. The obvious place to hang the
refresh is `record_generation`, which the adapter already calls -- but that call
was guarded on `metadata["image_sha256"]`, and the only writer of that key in the
whole tree is `mock_backend.py`. Hanging it there would have passed every
mock-shaped test and done nothing at all on the owner's machine.
`AdapterCallsItTests` is that regression, pinned.

AR5.4 has since repaired that guard: it now hashes the delivered pixels with
`gallery_index.content_hash` rather than reading the mock's key, so it does fire
on a real generation. **The pin below still holds and still matters**, for a
reason that outlived the original one: the refresh must not depend on the
image's IDENTITY. An image whose pixels cannot be hashed -- an SVG, a truncated
file -- still appeared in a linked folder and still has to reach an open
Gallery. The assertion is unchanged; only its rationale moved.

No `time.sleep` anywhere: `AutoSync` takes its clock and its wait, so the
debounce is tested by DRIVING time rather than by living through it. A test that
sleeps through a 1.5 second window is a test nobody runs twice.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_autosync import AutoSync  # noqa: E402
from forge_studio.gallery_events import CLOSED, EventBus, frame  # noqa: E402
from forge_studio.gallery_service import ROUTE_PREFIX, GalleryService  # noqa: E402
from forge_studio.gallery_store import GalleryStoreError  # noqa: E402

EXPECTED_GALLERY_AUTOSYNC_TESTS = 21


def _picture(path: Path, shade: tuple[int, int, int]) -> None:
    from PIL import Image

    Image.new("RGB", (32, 32), shade).save(path)


class _Clock:
    """Time an assertion can move, so a debounce is tested in microseconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# -- the bus --------------------------------------------------------------


class EventBusTests(unittest.TestCase):
    def test_a_published_event_reaches_every_subscriber(self) -> None:
        bus = EventBus()
        first, second = bus.subscribe(), bus.subscribe()
        self.assertEqual(2, bus.publish("sync", {"new": 1, "removed": 0}))
        for channel in (first, second):
            self.assertEqual(frame("sync", {"new": 1, "removed": 0}),
                             channel.get_nowait())

    def test_an_unsubscribed_client_is_no_longer_told(self) -> None:
        bus = EventBus()
        channel = bus.subscribe()
        bus.unsubscribe(channel)
        self.assertEqual(0, bus.publish("sync", {"new": 1}))
        self.assertEqual(0, bus.subscriber_count())

    def test_unsubscribing_twice_is_not_an_error(self) -> None:
        # It runs from a stream's `finally`, which is reachable more than one
        # way -- a closed tab, a shutdown, an exception mid-yield.
        bus = EventBus()
        channel = bus.subscribe()
        bus.unsubscribe(channel)
        bus.unsubscribe(channel)

    def test_a_slow_client_loses_a_message_not_its_connection(self) -> None:
        # The Extension REMOVES a client whose queue fills
        # (studio_gallery.py:298-309). A backgrounded tab is not a dead one,
        # and the next sync reloads everything a dropped one would have shown.
        bus = EventBus(depth=2)
        channel = bus.subscribe()
        for index in range(5):
            bus.publish("sync", {"new": index})
        self.assertEqual(1, bus.subscriber_count())
        self.assertEqual(3, bus.dropped())
        self.assertEqual(2, channel.qsize())

    def test_publishing_something_unserialisable_never_raises(self) -> None:
        bus = EventBus()
        bus.subscribe()
        self.assertEqual(0, bus.publish("sync", {"when": object()}))

    def test_close_wakes_a_stream_whose_queue_is_already_full(self) -> None:
        # The subscriber that most needs waking is the one `put_nowait`
        # refuses, so a close that cannot make room never wakes it at all.
        bus = EventBus(depth=2)
        channel = bus.subscribe()
        bus.publish("a")
        bus.publish("b")
        self.assertTrue(channel.full())
        bus.close()
        drained = []
        while not channel.empty():
            drained.append(channel.get_nowait())
        self.assertIn(CLOSED, drained)


# -- the debounce ---------------------------------------------------------


class DebounceTests(unittest.TestCase):
    def _worker(self, clock: _Clock, run) -> AutoSync:
        return AutoSync(run, lambda *_: None, quiet_seconds=1.5,
                        deadline_seconds=10.0, wait=lambda _: None, clock=clock)

    def test_it_waits_while_images_are_still_arriving(self) -> None:
        clock = _Clock()
        worker = self._worker(clock, lambda: (1, 0))
        worker.notify()
        clock.advance(1.0)
        self.assertFalse(worker._settled())

    def test_it_syncs_once_the_arrivals_stop(self) -> None:
        clock = _Clock()
        worker = self._worker(clock, lambda: (1, 0))
        worker.notify()
        clock.advance(1.6)
        self.assertTrue(worker._settled())

    def test_a_steady_stream_cannot_starve_it_forever(self) -> None:
        # The Extension's loop has only the quiet window
        # (studio_gallery.py:458-468), so a batch finishing an image every 1.9
        # seconds resets it indefinitely and the owner watches an empty grid
        # for as long as the queue runs.
        clock = _Clock()
        worker = self._worker(clock, lambda: (1, 0))
        worker.notify()
        for _ in range(30):
            clock.advance(1.0)
            worker.notify()
            if worker._settled():
                break
        self.assertTrue(worker._settled())
        self.assertLessEqual(clock.now, 10.0)

    def test_a_failing_pass_does_not_kill_the_worker(self) -> None:
        def explode() -> tuple[int, int]:
            raise RuntimeError("the disk went away mid-scan")

        worker = self._worker(_Clock(), explode)
        worker._pass()  # must not raise: this is the top of a daemon thread

    def test_nothing_is_published_when_nothing_changed(self) -> None:
        said: list = []
        worker = AutoSync(lambda: (0, 0), lambda n, d: said.append((n, d)),
                          wait=lambda _: None, clock=_Clock())
        worker._pass()
        self.assertEqual([], said)

    def test_a_pass_that_could_not_run_publishes_nothing(self) -> None:
        said: list = []
        worker = AutoSync(lambda: None, lambda n, d: said.append((n, d)),
                          wait=lambda _: None, clock=_Clock())
        worker._pass()
        self.assertEqual([], said)

    def test_what_changed_is_reported_in_the_pages_own_names(self) -> None:
        said: list = []
        worker = AutoSync(lambda: (3, 1), lambda n, d: said.append((n, d)),
                          wait=lambda _: None, clock=_Clock())
        worker._pass()
        self.assertEqual([("sync", {"new": 3, "removed": 1})], said)


# -- the chain ------------------------------------------------------------


class GenerationReachesTheGalleryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.room = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        room = Path(self.room.name)
        self.pictures = room / "pictures"
        self.pictures.mkdir(parents=True)
        _picture(self.pictures / "before.png", (40, 80, 160))
        self.service = GalleryService(room / "state")
        self.addCleanup(self.room.cleanup)
        self.addCleanup(self.service.close)
        self.service.post(f"{ROUTE_PREFIX}/scan-folders",
                          {"path": str(self.pictures)})
        self.service.post(f"{ROUTE_PREFIX}/scan", {})

    def _images(self) -> int:
        reply = self.service.get(f"{ROUTE_PREFIX}/images?page=1&per_page=1")
        return int(reply.payload["total"])

    def test_a_new_file_and_a_pass_publish_a_sync(self) -> None:
        channel = self.service._events.subscribe()
        self.assertEqual(1, self._images())
        _picture(self.pictures / "generated.png", (200, 90, 40))
        # The pass, run directly: the debounce is driven above, and a test
        # that waited on a real thread would be a test that sleeps.
        self.service._autosync._pass()
        self.assertEqual(frame("sync", {"new": 1, "removed": 0}),
                         channel.get_nowait())
        self.assertEqual(2, self._images())

    def test_note_generation_never_raises(self) -> None:
        self.service.close()
        self.service.note_generation()  # after close, deliberately

    def test_the_stream_opens_by_saying_what_is_watching(self) -> None:
        stream = self.service.events()
        opening = next(stream)
        self.assertIn(b"watcher_status", opening)
        self.assertIn(b'"active": false', opening)
        self.assertIn(b'"generation_sync": true', opening)
        stream.close()

    def test_a_refused_gallery_never_claims_generation_sync(self) -> None:
        # `/events` is the one route that bypasses the 503, so it must not be
        # the one place that implies the Gallery is working.
        self.service.close()
        self.assertFalse(self.service.watcher_status()["generation_sync"])

    def test_the_store_is_not_reopened_after_close(self) -> None:
        # A background pass waking after shutdown would otherwise re-create
        # the state tree and, on Windows, re-lock the file close() released.
        self.service.close()
        with self.assertRaises(GalleryStoreError):
            self.service.store()


class AdapterCallsItTests(unittest.TestCase):
    """The near-miss, pinned.

    Originally: `record_generation` was guarded on `metadata["image_sha256"]`,
    and the only writer of that key in the tree is `mock_backend.py`. A result
    WITHOUT it is what every real generation produces, and it must still
    refresh the Gallery.

    AR5.4 repaired that guard to hash the delivered pixels instead. The
    assertion below is deliberately unchanged, because the property it pins is
    now load-bearing for a second reason: an image the Gallery cannot hash --
    the mock's SVG, a truncated file -- still arrived in a linked folder and
    must still reach an open Gallery. The refresh is about ARRIVAL; the
    metadata row is about IDENTITY; neither may be gated on the other.
    """

    def setUp(self) -> None:
        source = (APP_ROOT / "forge_studio" / "source_api_adapter.py")
        self.tree = ast.parse(source.read_text(encoding="utf-8"))

    def _notify_calls(self, node: ast.AST) -> list[ast.Call]:
        return [
            found for found in ast.walk(node)
            if isinstance(found, ast.Call)
            and isinstance(found.func, ast.Attribute)
            and found.func.attr == "note_generation"
        ]

    def test_the_adapter_tells_the_gallery_at_all(self) -> None:
        self.assertTrue(self._notify_calls(self.tree),
                        "nothing in the adapter calls note_generation, so a "
                        "finished generation never reaches an open Gallery")

    def test_it_is_not_inside_the_content_hash_guard(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.If):
                continue
            if "content_hash" not in ast.dump(node.test):
                continue
            self.assertEqual(
                [], self._notify_calls(node),
                "note_generation sits inside the `content_hash` guard, so a "
                "picture whose pixels cannot be hashed never reaches an open "
                "Gallery -- and before AR5.4 that guard read "
                "metadata['image_sha256'], a key only mock_backend.py writes, "
                "so on a real generation it never ran at all")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_AUTOSYNC_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
