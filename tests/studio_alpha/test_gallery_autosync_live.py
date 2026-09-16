"""The Gallery is told when a generation finishes. AR5.3.

THE DEFECT THIS CLOSES

Owner: "one other issue I noticed with gallery is it doesn't auto update when
new images are added. I have to refresh it for them to appear."

Not a missing feature. A complete, careful, well-built chain with exactly one
wire unconnected -- and both ends already worked:

    gallery.js:186    EventSource("/studio/gallery/events")   listening
    gallery.js:2913   connectSSE()                            called
    gallery_service.py:327  note_generation()                 defined
    gallery_autosync.py     quiet 1.5s / deadline 10s         correct

`note_generation` had exactly ONE caller:

    source_api_adapter.py:1315   inside _canonical_result (:1265)
                                 inside SourceFrontendAdapter.generate (:898)
                                 a BLOCKING generate-and-wait adapter whose own
                                 failure strings say "Mock generation cancelled"

A real install POSTs /api/generate and polls GET /api/jobs/<id>. That adapter
is never entered, so the notification never fired, and `AutoSync.start()` is
lazy -- so the worker thread was never even created.

THE SAME DEFECT CLASS AS THE LIFECYCLE `return`, ONE LAYER DOWN. `_infotext()`
is stranded identically: defined at source_api_adapter.py:1751, called once from
the same dead `_canonical_result`. Two owner-visible features on one dead
function.

WHY THESE TESTS DRIVE THE ROUTE

This is the whole point of the suite, and the reason it is not three lines.

A test that calls `note_generation()` -- or even the new
`_note_generation_to_gallery()` -- and asserts the Gallery was told **would have
passed on every commit for as long as this defect existed**. The service was
always correct. What was broken was that nothing on the live path called it.

So `TheLiveRouteNotifiesTests` builds a socket-free handler and drives the real
`do_GET` dispatch with a real path string. If the call is ever lifted back out
of that route, these fail; a test written against the service could not tell.

Review: `Evidence/source-review/AR5.3-gallery-autosync.md`.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_AUTOSYNC_TESTS = 21

ADAPTER_PY = (APP_ROOT / "forge_studio" / "source_api_adapter.py").read_text(
    encoding="utf-8")
GALLERY_JS = (APP_ROOT / "forge_studio" / "frontend" / "gallery.js").read_text(
    encoding="utf-8")


def presentation():
    return importlib.import_module("forge_studio.presentation")


class _Gallery:
    """A Gallery that only records being told."""

    def __init__(self, raises: bool = False) -> None:
        self.notified = 0
        self._raises = raises

    def note_generation(self) -> None:
        self.notified += 1
        if self._raises:
            raise RuntimeError("the index is on fire")


def build(status, *, gallery=None, no_server: bool = False):
    """A socket-free handler wired to a stub presentation and Gallery.

    The SERVER is a real `_StudioHTTPServer` instance built without running its
    `__init__`, so `_gallery` exercises its actual `isinstance` check rather
    than a property this test replaced.
    """

    module = presentation()
    sent: dict = {}

    server = object.__new__(module._StudioHTTPServer)
    if gallery is not None:
        server.gallery_service = gallery

    class _Surface:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def job_status(self, job_id: str):
            self.asked.append(job_id)
            return status

    surface = _Surface()

    class _Probe(module._StudioRequestHandler):
        def __init__(self) -> None:      # no socket, no BaseHTTPRequestHandler
            self.command = "GET"

        # Everything do_GET consults before the job route.
        def _reject_untrusted_host(self) -> bool:
            return False

        def _serve_static(self, path) -> bool:
            return False

        def _serve_result(self, path) -> bool:
            return False

        def _serve_gallery(self, method, payload=None) -> bool:
            return False

        @property
        def _presentation(self):
            return surface

        @property
        def _settings(self):
            return None

        def _send_json(self, status_code, body) -> None:
            sent["status"] = status_code
            sent["body"] = body

    handler = _Probe()
    if not no_server:
        handler.server = server
    else:
        handler.server = object()      # a host that is not a Studio server
    return handler, sent, surface, server


def completed(job_id: str = "studio-job-000001"):
    return {"job_id": job_id, "state": "completed",
            "result": {"image_handle": "handle-abc", "width": 512,
                       "height": 512}}


class TheLiveRouteNotifiesTests(unittest.TestCase):
    """Driven through `do_GET`, because that is what was broken."""

    def test_a_completed_job_tells_the_gallery(self) -> None:
        gallery = _Gallery()
        handler, sent, surface, _server = build(completed(), gallery=gallery)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(["studio-job-000001"], surface.asked,
                         "the route did not answer at all")
        self.assertEqual(1, gallery.notified,
                         "the live route did not tell the Gallery")

    def test_the_job_response_is_still_served(self) -> None:
        """The notification must not replace or disturb the answer."""

        gallery = _Gallery()
        handler, sent, _surface, _server = build(completed(), gallery=gallery)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(completed(), sent["body"])

    def test_it_notifies_once_and_not_once_per_poll(self) -> None:
        """A completed job can be asked for repeatedly. Notifying each time
        keeps resetting AutoSync's quiet window, which pushes the refresh out
        to the 10 s deadline instead of 1.5 s -- working, and slower for no
        reason."""

        gallery = _Gallery()
        handler, _sent, _surface, _server = build(completed(), gallery=gallery)
        handler.path = "/api/jobs/studio-job-000001"
        for _ in range(5):
            handler.do_GET()
        self.assertEqual(1, gallery.notified)

    def test_two_different_jobs_both_notify(self) -> None:
        """The guard is per job, not a latch that fires once per session."""

        gallery = _Gallery()
        shared = None
        for job in ("studio-job-000001", "studio-job-000002"):
            handler, _s, _p, server = build(completed(job), gallery=gallery)
            # ONE server across both, as a real host has -- otherwise each job
            # gets a fresh guard set and this passes without proving anything.
            shared = shared or server
            handler.server = shared
            handler.path = f"/api/jobs/{job}"
            handler.do_GET()
        self.assertEqual(2, gallery.notified)
        self.assertEqual({"studio-job-000001", "studio-job-000002"},
                         shared._gallery_noted_jobs)


class OnlyAFinishedPictureCountsTests(unittest.TestCase):
    """A scan costs a walk of every linked folder. Do not spend it on nothing."""

    def test_a_running_job_notifies_nothing(self) -> None:
        gallery = _Gallery()
        handler, _sent, _s, _srv = build(
            {"job_id": "j", "state": "running", "progress": {}},
            gallery=gallery)
        handler.path = "/api/jobs/j"
        handler.do_GET()
        self.assertEqual(0, gallery.notified)

    def test_a_cancelled_job_notifies_nothing(self) -> None:
        """A cancelled generation produced no image."""

        gallery = _Gallery()
        handler, _sent, _s, _srv = build(
            {"job_id": "j", "state": "cancelled"}, gallery=gallery)
        handler.path = "/api/jobs/j"
        handler.do_GET()
        self.assertEqual(0, gallery.notified)

    def test_a_failed_job_notifies_nothing(self) -> None:
        gallery = _Gallery()
        handler, _sent, _s, _srv = build(
            {"job_id": "j", "state": "failed", "error": {"code": "X"}},
            gallery=gallery)
        handler.path = "/api/jobs/j"
        handler.do_GET()
        self.assertEqual(0, gallery.notified)

    def test_completed_with_no_result_notifies_nothing(self) -> None:
        """`completed` alone is a state. The result is the picture."""

        gallery = _Gallery()
        handler, _sent, _s, _srv = build(
            {"job_id": "j", "state": "completed"}, gallery=gallery)
        handler.path = "/api/jobs/j"
        handler.do_GET()
        self.assertEqual(0, gallery.notified)


class ItCannotBreakAGenerationTests(unittest.TestCase):
    """The picture was made. Nothing about an index may say otherwise."""

    def test_a_gallery_that_raises_does_not_break_the_response(self) -> None:
        gallery = _Gallery(raises=True)
        handler, sent, _s, _srv = build(completed(), gallery=gallery)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(1, gallery.notified)
        self.assertEqual(completed(), sent["body"])

    def test_a_host_with_no_gallery_serves_the_route_unchanged(self) -> None:
        """No state root means no Gallery -- the demo path and the browser
        harness both build a server that way."""

        handler, sent, surface, _srv = build(completed())   # no gallery set
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(completed(), sent["body"])
        self.assertEqual(["studio-job-000001"], surface.asked)

    def test_a_non_studio_server_serves_the_route_unchanged(self) -> None:
        """`_gallery` guards on isinstance. A handler bound to something else
        must fall through rather than raise."""

        handler, sent, _s, _srv = build(completed(), no_server=True)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(completed(), sent["body"])


class TheGuardIsBoundedTests(unittest.TestCase):
    def test_the_seen_set_does_not_grow_without_limit(self) -> None:
        """Bounded by DISCARDING, not by evicting. An LRU would silently drop
        the oldest id and re-notify that job if it were polled again."""

        gallery = _Gallery()
        handler, _sent, _s, server = build(completed(), gallery=gallery)
        server._gallery_noted_jobs = {f"job-{n}" for n in range(4096)}
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertLessEqual(len(server._gallery_noted_jobs), 4096)
        self.assertIn("studio-job-000001", server._gallery_noted_jobs)

    def test_the_guard_lives_on_the_server_not_the_handler(self) -> None:
        """A handler is built per request. A set on `self` would be empty every
        time, and the notification would fire on every poll."""

        gallery = _Gallery()
        handler, _sent, _s, server = build(completed(), gallery=gallery)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertIn("_gallery_noted_jobs", vars(server))
        self.assertNotIn("_gallery_noted_jobs", vars(handler))


class TheOldPathIsLeftAloneTests(unittest.TestCase):
    """The legacy adapter keeps working for whatever still uses it."""

    def test_the_original_call_site_survives(self) -> None:
        self.assertIn("self._gallery.note_generation()", ADAPTER_PY)

    def test_the_browser_end_was_never_the_problem(self) -> None:
        """Both ends already worked. Recorded so a future reader does not go
        looking for a missing listener that was there all along."""

        self.assertIn('new EventSource(API_BASE + "/events")', GALLERY_JS)
        self.assertIn('_sse.addEventListener("sync"', GALLERY_JS)
        self.assertIn("connectSSE();", GALLERY_JS)


class ASyncArrivingUnderTheLightboxIsHeldTests(unittest.TestCase):
    """The second half of "it does not auto update".

    `onAutoSync` used to `return` outright when the detail lightbox was open.
    That is right about the lightbox -- reloading the grid underneath an open
    image reorders the strip the owner is stepping through -- and wrong about
    the sync: the news was DISCARDED, so those images never appeared at all
    until the owner pressed Scan by hand.

    Found while fixing the server-side trigger, and fixed with it because it is
    the same owner-visible sentence: generate while looking at a picture, and
    the Gallery still does not update.
    """

    def test_the_discard_is_gone(self) -> None:
        self.assertNotIn('if (G.page === "detail") return;', GALLERY_JS)

    def test_it_is_held_instead(self) -> None:
        self.assertIn("_pendingSync = {", GALLERY_JS)
        self.assertIn('if (G.page === "detail") {', GALLERY_JS)

    def test_closing_the_lightbox_applies_it(self) -> None:
        """Held is only better than dropped if something lets it out."""

        at = GALLERY_JS.index("function closeDetail()")
        # To the NEXT top-level declaration, not to the first newline: the
        # function is two lines now, and a one-line slice would have missed
        # the very thing this asserts.
        close = GALLERY_JS[at:GALLERY_JS.index(chr(10) + "function ", at + 5)]
        self.assertIn("_pendingSync", close)
        self.assertIn("applySync(held)", close)

    def test_bursts_are_summed_not_replaced(self) -> None:
        """Several generations can land while one image is open. Keeping only
        the last would report "1 new" for a batch of eight."""

        at = GALLERY_JS.index("_pendingSync = {")
        block = GALLERY_JS[at:at + 260]
        self.assertIn("(_pendingSync?.new || 0) + (d.new || 0)", block)
        self.assertIn("(_pendingSync?.removed || 0) + (d.removed || 0)", block)

    def test_the_ordinary_path_still_applies_immediately(self) -> None:
        """The common case -- grid on screen -- must not have been deferred
        into the lightbox branch by accident."""

        at = GALLERY_JS.index("function onAutoSync(d)")
        self.assertIn("await applySync(d);", GALLERY_JS[at:at + 900])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_AUTOSYNC_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
