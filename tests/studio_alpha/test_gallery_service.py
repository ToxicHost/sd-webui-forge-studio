"""Every Gallery route, called as a method.

No server, no port, no client. The routes return `Reply` objects, so the whole
surface `gallery.js` talks to is testable without an HTTP stack -- and the
tests that matter most here are the refusals, which are exactly the ones a
green "it returned 200" check would miss.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_capability import GalleryProbe  # noqa: E402
from forge_studio.gallery_index import imaging_available  # noqa: E402
from forge_studio.gallery_service import (  # noqa: E402
    ROUTE_PREFIX,
    GalleryService,
)

EXPECTED_GALLERY_SERVICE_TESTS = 80

PARAMETERS = (
    "a knight on a hill\n"
    "Negative prompt: blurry\n"
    "Steps: 30, Sampler: Euler, Seed: 7, Size: 8x6, Model: Anitox"
)


class _Serving(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.pictures = base / "pictures"
        self.pictures.mkdir()
        self.service = GalleryService(base / "state")
        self.addCleanup(self.service.close)

    def png(self, name: str, parameters: str | None = PARAMETERS) -> Path:
        from PIL import Image, PngImagePlugin

        info = None
        if parameters is not None:
            info = PngImagePlugin.PngInfo()
            info.add_text("parameters", parameters)
        path = self.pictures / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (40, 30))
        image.putdata([
            ((x * 3 + len(name)) % 256, (y * 5) % 256, (x + y) % 256)
            for y in range(30) for x in range(40)
        ])
        image.save(path, pnginfo=info)
        return path

    def scan_now(self) -> dict:
        """Index the pictures folder, and let the work behind it settle.

        Request/response, because `gallery.js` treats the response as the
        completion signal and reloads the grid on the next line.

        THE WAIT IS PART OF THIS HELPER, on purpose. `/scan` answers as soon as
        the files are indexed and then starts hashing on a thread it does not
        join, because a fingerprint costs a decode per image. So
        `images.content_hash` -- which every metadata lookup by hash needs --
        is still NULL when the response arrives, and a test that reads metadata
        straight afterwards races that thread.

        Not hypothetical: it made
        `test_a_generated_image_finds_its_prompt_once_scanned` fail 7 times in
        10, measured on an unchanged tree, while passing often enough that a
        green canonical run looked like proof of something.

        Making the safe order the DEFAULT beats remembering to ask for it.
        Nothing needs the pre-enrichment state: the tests that care about
        hashing run their own wait loops and assert the END state, so a wait
        here only makes those loops exit on the first pass.
        """

        self.service.post(f"{ROUTE_PREFIX}/add-folder",
                          {"path": str(self.pictures)})
        outcome = self.service.post(f"{ROUTE_PREFIX}/scan").payload
        self.wait_for_enrichment()
        return outcome

    def get(self, route: str, query: str = ""):
        return self.service.get(f"{ROUTE_PREFIX}{route}{query}")

    def wait_for_enrichment(self) -> None:
        """Enrichment starts on its own after a scan and runs behind it."""

        for _ in range(600):
            if not self.get("/hash-status").payload["hashing"]:
                return
            time.sleep(0.05)
        self.fail("enrichment did not finish")

    def first_image_id(self) -> int:
        return self.get("/images").payload["images"][0]["id"]


class RoutingTests(_Serving):
    def test_a_gallery_path_is_recognised(self) -> None:
        self.assertTrue(self.service.handles(f"{ROUTE_PREFIX}/stats"))

    def test_another_studio_path_is_not_claimed(self) -> None:
        """The service must not swallow routes that belong to the rest of
        Studio, or every one of them starts 404-ing from here instead."""

        self.assertFalse(self.service.handles("/studio/prefs"))
        self.assertFalse(self.service.handles("/api/status"))

    def test_an_unknown_gallery_route_is_not_found(self) -> None:
        self.assertEqual(404, self.get("/no-such-route").status)

    def test_a_query_string_is_parsed(self) -> None:
        self.png("a.png")
        self.scan_now()
        self.assertEqual(1, self.get("/images", "?per_page=1").payload["total"])


class CapabilityTests(_Serving):
    def test_a_working_gallery_reports_available(self) -> None:
        reply = self.get("/capability")
        self.assertTrue(reply.ok)
        self.assertTrue(reply.payload["available"])

    def test_an_unavailable_gallery_refuses_every_route(self) -> None:
        """The rule that matters. An empty list would tell an owner their
        pictures are gone; a refusal tells them Pillow is missing."""

        service = GalleryService(
            Path(self._directory.name) / "other",
            probe=GalleryProbe(imaging=False),
        )
        self.addCleanup(service.close)
        for route in ("/stats", "/images", "/folders", "/scan-folders"):
            with self.subTest(route=route):
                reply = service.get(f"{ROUTE_PREFIX}{route}")
                self.assertEqual(503, reply.status)
                self.assertIn("Pillow", reply.payload["error"])
                self.assertFalse(reply.payload["ok"])

    def test_an_unavailable_gallery_refuses_writes_too(self) -> None:
        service = GalleryService(
            Path(self._directory.name) / "other2",
            probe=GalleryProbe(imaging=False),
        )
        self.addCleanup(service.close)
        self.assertEqual(503,
                         service.post(f"{ROUTE_PREFIX}/scan", {}).status)

    def test_the_hash_status_admits_duplicates_are_off(self) -> None:
        """Otherwise the Duplicates page waits for a worker that never starts.

        Duplicate detection is no longer gated on a missing package -- Studio
        computes its own perceptual hash -- so the only way to reach this state
        now is a Gallery that cannot read images at all."""

        service = GalleryService(
            Path(self._directory.name) / "third",
            probe=GalleryProbe(imaging=True, perceptual_hashing=False),
        )
        self.addCleanup(service.close)
        payload = service.get(f"{ROUTE_PREFIX}/hash-status").payload
        self.assertFalse(payload["available"])
        self.assertIn("read images", payload["reason"])
        # The field names are the PAGE's. It reads `hashing`, `hashed` and
        # `total`; answering `active` left the panel rendering "All {total}
        # images hashed" with the placeholder still in it.
        for name in ("hashing", "hashed", "total", "hash_current", "hash_total"):
            with self.subTest(field=name):
                self.assertIn(name, payload)


class ReadTests(_Serving):
    def test_an_empty_gallery_answers_rather_than_failing(self) -> None:
        self.assertEqual([], self.get("/images").payload["images"])
        self.assertEqual(0, self.get("/stats").payload["images"])
        self.assertEqual([], self.get("/folders").payload)

    def test_images_are_listed_after_a_scan(self) -> None:
        self.png("a.png")
        self.png("b.png")
        self.scan_now()
        payload = self.get("/images").payload
        self.assertEqual(2, payload["total"])
        self.assertEqual({"a.png", "b.png"},
                         {row["filename"] for row in payload["images"]})

    def test_folders_carry_their_counts(self) -> None:
        """`gallery.js` builds its folder tree from exactly these two fields."""

        self.png("a.png")
        self.png("sub/b.png")
        self.scan_now()
        folders = self.get("/folders").payload
        self.assertTrue(folders)
        for entry in folders:
            with self.subTest(entry=entry):
                self.assertIn("folder", entry)
                self.assertIn("image_count", entry)
        self.assertEqual(2, sum(e["image_count"] for e in folders))

    def test_a_scan_folder_is_listed(self) -> None:
        self.png("a.png")
        self.scan_now()
        self.assertEqual(1, len(self.get("/scan-folders").payload))

    def test_one_image_can_be_fetched(self) -> None:
        self.png("a.png")
        self.scan_now()
        reply = self.get(f"/image/{self.first_image_id()}")
        self.assertEqual("a.png", reply.payload["filename"])

    def test_a_missing_image_is_not_found(self) -> None:
        self.assertEqual(404, self.get("/image/9999").status)

    def test_a_non_numeric_image_id_is_not_found(self) -> None:
        """It reaches an int(), and an unhandled ValueError would be a 500."""

        self.assertEqual(404, self.get("/image/nonsense").status)

    def test_a_scanned_image_reports_the_parameters_in_its_file(self) -> None:
        """The defect a live run found while every unit test passed.

        Most images in a library were never generated by this Studio -- they
        were scanned -- so their parameters live in a PNG text chunk and
        nowhere else. The original test inserted a metadata row and then read
        it back, which proved the database worked and proved nothing about the
        route an owner actually hits.
        """

        self.png("a.png")
        self.scan_now()
        payload = self.get(f"/image/{self.first_image_id()}/metadata").payload
        self.assertEqual("a knight on a hill", payload["prompt"])
        self.assertEqual("Euler", payload["sampler"])
        self.assertEqual("embedded", payload["_source"])

    def test_stored_parameters_answer_when_the_file_carries_none(self) -> None:
        """Metadata stripping leaves the database row alone on purpose, so an
        owner who strips a file still sees what it was made with."""

        self.png("bare.png", parameters=None)
        self.scan_now()
        image_id = self.first_image_id()
        with self.service.store().write() as connection:
            connection.execute(
                "INSERT INTO image_metadata(image_id, prompt) "
                "VALUES(?, 'from the database')",
                (image_id,),
            )
        payload = self.get(f"/image/{image_id}/metadata").payload
        self.assertEqual("from the database", payload["prompt"])
        self.assertEqual("stored", payload["_source"])

    def test_the_studio_template_field_survives_the_route(self) -> None:
        """The field the handoff is emphatic about. It has to reach the page,
        not merely be parsed correctly somewhere inside."""

        self.png(
            "t.png",
            "a knight\nTemplate: a __character__\nSteps: 4, Sampler: Euler",
        )
        self.scan_now()
        payload = self.get(f"/image/{self.first_image_id()}/metadata").payload
        self.assertEqual("a __character__", payload["template"])

    def test_metadata_for_an_unknown_image_is_not_found(self) -> None:
        self.assertEqual(404, self.get("/image/4242/metadata").status)

    def test_an_image_with_no_parameters_anywhere_says_so(self) -> None:
        """Answered, not 404: the image exists, it just carries nothing. The
        `_source` marker is what separates "this file has no parameters" from
        "we did not look properly"."""

        self.png("bare.png", parameters=None)
        self.scan_now()
        reply = self.get(f"/image/{self.first_image_id()}/metadata")
        self.assertTrue(reply.ok)
        self.assertEqual("none", reply.payload["_source"])
        self.assertNotIn("prompt", reply.payload)

    def test_suggestions_answer(self) -> None:
        self.assertEqual([], self.get("/suggest", "?q=ar").payload)

    def test_ignore_words_answer(self) -> None:
        self.assertEqual([], self.get("/ignore-words").payload)


class CharacterSidebarTests(_Serving):
    """The route whose absence blanked the whole panel.

    A browser run found it: `gallery.js` calls `.filter()` on this response, so
    a 404 handed it an error object with no `.filter`, the exception escaped
    `init()`, and NOTHING rendered -- while every route the suite knew about
    answered 200.
    """

    def tag(self, name: str, filename: str = "a.png") -> None:
        self.png(filename)
        self.scan_now()
        with self.service.store().write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO characters(name) VALUES(?)", (name,))
            connection.execute(
                "INSERT OR IGNORE INTO image_characters(image_id, character_id) "
                "SELECT i.id, c.id FROM images i, characters c "
                "WHERE i.filename = ? AND c.name = ?",
                (filename, name),
            )

    def test_the_route_answers_a_list(self) -> None:
        """A list, specifically. The page calls `.filter()` on it."""

        reply = self.get("/characters")
        self.assertTrue(reply.ok)
        self.assertIsInstance(reply.payload, list)

    def test_a_tagged_character_is_listed_with_its_count(self) -> None:
        """`Aria` among whatever the scan itself tagged. A file called `a.png`
        names nobody, so the scan files it under "Unknown" -- which is correct
        and is why this asserts membership rather than a list of one."""

        self.tag("Aria")
        found = {row["name"]: row["image_count"]
                 for row in self.get("/characters").payload}
        self.assertEqual(1, found["Aria"])

    def test_unknown_sorts_before_the_named_characters(self) -> None:
        """It is the bucket every untagged image lands in, so it is the one an
        owner reaches for most."""

        self.tag("Zara", "a.png")
        self.tag("unknown", "b.png")
        names = [row["name"] for row in self.get("/characters").payload
                 if row["id"] > 0]
        self.assertEqual("unknown", names[0].lower())

    def test_ratings_ride_along_as_pseudo_tags(self) -> None:
        """One sidebar holds both. Negative ids are how they share it without
        colliding with a real character's id."""

        self.png("a.png")
        self.scan_now()
        with self.service.store().write() as connection:
            connection.execute("UPDATE images SET rating = 4")
        found = self.get("/characters").payload
        stars = [row for row in found if row["id"] < 0]
        self.assertEqual(1, len(stars))
        self.assertEqual(-4, stars[0]["id"])
        self.assertEqual("★★★★", stars[0]["name"])

    def test_the_list_can_be_narrowed_to_a_folder(self) -> None:
        """The sidebar shows the tags present in what is being looked at, not
        every tag in the library."""

        self.tag("Aria", "a.png")
        inside = [row["name"] for row in
                  self.get("/characters", f"?folder={self.pictures.name}").payload]
        self.assertIn("Aria", inside)
        self.assertEqual([], self.get("/characters", "?folder=nowhere").payload)


class EventStreamTests(_Serving):
    def test_the_stream_opens_by_saying_whether_anything_is_watching(
        self,
    ) -> None:
        """`gallery.js` reconnects every five seconds while this fails, so a
        missing route is a request every five seconds for the life of the tab.
        Answering honestly also lets the page draw its watcher indicator."""

        stream = self.service.events()
        first = next(stream)
        stream.close()
        self.assertTrue(first.startswith(b"event: watcher_status"))
        self.assertTrue(first.endswith(b"\n\n"))
        self.assertIn(b'"active": false', first)

    def test_the_reason_reaches_the_page(self) -> None:
        stream = self.service.events()
        first = next(stream)
        stream.close()
        self.assertIn(b"filesystem-notification", first)


class MetadataLinkTests(_Serving):
    def test_parameters_saved_by_hash_are_found_before_the_scan_links_them(
        self,
    ) -> None:
        """An owner asking for the prompt of an image made thirty seconds ago
        should get it, even though the row is still keyed only by hash."""

        from forge_studio.gallery_index import content_hash

        # The file carries nothing -- stripped, or written by a path that puts
        # its parameters only in the database. If it DID carry parameters they
        # would rightly win, and this test would be asserting the fallback
        # while never reaching it.
        path = self.png("a.png", parameters=None)
        # The row below is left with image_id NULL on purpose, so the ONLY
        # join available is `images.content_hash`, which the hashing thread
        # fills. `scan_now()` waits for that thread; without the wait this test
        # carries the same race that made the generation-isolation one fail 7
        # times in 10, and it passed only by happening to.
        self.scan_now()
        image_id = self.first_image_id()

        with self.service.store().write() as connection:
            connection.execute("DELETE FROM image_metadata")
            connection.execute(
                "INSERT INTO image_metadata(content_hash, prompt) "
                "VALUES(?, 'from the canvas')",
                (content_hash(path),),
            )
            # Left UNLINKED on purpose: image_id is still NULL, exactly as
            # generation leaves it before a scan catches up.
            self.assertIsNone(
                connection.execute(
                    "SELECT image_id FROM image_metadata"
                ).fetchone()["image_id"]
            )
        payload = self.get(f"/image/{image_id}/metadata").payload
        self.assertEqual("from the canvas", payload["prompt"])
        self.assertEqual("stored", payload["_source"])


class BytesTests(_Serving):
    def test_a_thumbnail_is_served_with_an_etag(self) -> None:
        self.png("a.png")
        self.scan_now()
        reply = self.get(f"/thumb/{self.first_image_id()}")
        self.assertTrue(reply.ok)
        self.assertTrue(reply.body)
        self.assertEqual("image/webp", reply.media_type)
        self.assertTrue(reply.headers["ETag"])
        self.assertIn("immutable", reply.headers["Cache-Control"])

    def test_a_thumbnail_for_a_missing_image_is_not_found(self) -> None:
        self.assertEqual(404, self.get("/thumb/9999").status)

    def test_a_thumbnail_of_an_unreadable_file_is_not_found(self) -> None:
        """404, not a 200 with no body: the page draws its broken-image state,
        which is what a broken image should look like."""

        (self.pictures / "broken.png").write_bytes(b"not a png")
        self.scan_now()
        self.assertEqual(404, self.get(f"/thumb/{self.first_image_id()}").status)

    def test_the_full_image_is_served_with_its_real_type(self) -> None:
        self.png("a.png")
        self.scan_now()
        reply = self.get(f"/full/{self.first_image_id()}")
        self.assertEqual("image/png", reply.media_type)
        self.assertEqual(
            (self.pictures / "a.png").read_bytes(), reply.body)

    def test_the_full_response_names_the_original_file(self) -> None:
        """The URL is a numeric id, so without this a drag-out or Save-As
        names the file after the id."""

        self.png("a.png")
        self.scan_now()
        disposition = self.get(
            f"/full/{self.first_image_id()}"
        ).headers["Content-Disposition"]
        self.assertIn('filename="a.png"', disposition)
        # Inline, so a lightbox <img src> and a direct visit both render.
        self.assertTrue(disposition.startswith("inline"))

    def test_a_non_ascii_filename_survives(self) -> None:
        self.png("château.png")
        self.scan_now()
        disposition = self.get(
            f"/full/{self.first_image_id()}"
        ).headers["Content-Disposition"]
        self.assertIn("filename*=UTF-8''", disposition)

    def test_the_media_type_is_never_guessed_from_the_bytes(self) -> None:
        """An indexed file whose extension is not a known image type must not
        be served as something the page might execute."""

        from forge_studio.gallery_service import _MEDIA_TYPES

        for suffix, media_type in _MEDIA_TYPES.items():
            with self.subTest(suffix=suffix):
                self.assertFalse(media_type.startswith("text/"))
                self.assertNotIn("html", media_type)


class ScanTests(_Serving):
    def test_a_scan_reports_what_it_did_in_its_own_response(self) -> None:
        """`new` and `removed`, in the page's names, IN the response -- it
        interpolates them straight into a toast. Answering `{ok, started}`
        made that toast read "Scan: undefined new, undefined removed"."""

        self.png("a.png")
        payload = self.scan_now()
        self.assertEqual(1, payload["new"])
        self.assertEqual(0, payload["removed"])
        self.assertTrue(payload["ok"])

    def test_the_images_are_there_the_moment_the_scan_answers(self) -> None:
        """The other half of the same bug. `gallery.js` reloads the grid on
        the line after the await, so a response that arrives before the work
        is done leaves a freshly linked folder looking empty until the owner
        presses refresh."""

        self.png("a.png")
        self.png("b.png")
        self.scan_now()
        self.assertEqual(2, self.get("/images").payload["total"])
        self.assertEqual(2, self.get("/stats").payload["images"])

    def test_a_second_scan_while_one_runs_is_refused_politely(self) -> None:
        """Two scans would fight for the write lock. The honest answer to an
        impatient page is "the one you started is still going"."""

        for index in range(4):
            self.png(f"{index}.png")
        self.service.post(f"{ROUTE_PREFIX}/add-folder",
                          {"path": str(self.pictures)})

        seen: list[dict] = []

        def rival() -> None:
            seen.append(self.service.post(f"{ROUTE_PREFIX}/scan").payload)

        # A scan is running the moment `begin()` marks it, so a rival launched
        # against the same service must see `already_running` rather than
        # starting a second pass over the same folder.
        self.service._scan.begin()  # noqa: SLF001
        try:
            thread = threading.Thread(target=rival)
            thread.start()
            thread.join(30)
        finally:
            self.service._scan.finish()  # noqa: SLF001
        self.assertTrue(seen[0]["already_running"])
        self.assertEqual(0, seen[0]["new"])

    def test_progress_is_published_while_a_scan_runs(self) -> None:
        """The page polls `/scan-progress` on its own timer while the POST is
        still open, so the long request is not a silent one."""

        for index in range(6):
            self.png(f"{index}.png")
        self.scan_now()
        status = self.get("/scan-progress").payload
        self.assertFalse(status["active"])
        self.assertTrue(status["folders"])
        self.assertEqual("Done", status["folders"][0]["phase"])

    def test_a_folder_that_is_not_a_folder_is_refused(self) -> None:
        reply = self.service.post(f"{ROUTE_PREFIX}/add-folder",
                                  {"path": str(self.pictures / "nope")})
        self.assertEqual(400, reply.status)
        self.assertFalse(reply.payload["ok"])

    def test_adding_a_folder_with_no_path_is_refused(self) -> None:
        reply = self.service.post(f"{ROUTE_PREFIX}/add-folder", {})
        self.assertEqual(400, reply.status)

    def test_a_folder_can_be_unlinked(self) -> None:
        self.png("a.png")
        self.scan_now()
        reply = self.service.post(f"{ROUTE_PREFIX}/unlink-folder",
                                  {"path": str(self.pictures)})
        self.assertTrue(reply.payload["ok"])
        self.assertEqual(0, self.get("/stats").payload["images"])

    def test_a_scan_crash_is_reported_rather_than_hanging(self) -> None:
        """A thread that dies silently leaves the page polling a scan that
        will never finish."""

        from forge_studio.gallery_service import ScanRunner

        runner = ScanRunner()

        class Exploding:
            def scan(self):
                raise RuntimeError("something surprising")

        runner.start(lambda cancel, report: Exploding())
        for _ in range(200):
            if not runner.running:
                break
            time.sleep(0.02)
        status = runner.status()
        self.assertFalse(status["active"])
        self.assertIn("RuntimeError", status["error"])

    def test_cancelling_is_answerable_and_destroys_nothing(self) -> None:
        """Cancelling BEFORE a scan starts cannot work and must not pretend
        to: `/scan` mints a fresh cancel each time, so a flag set earlier is
        cleared -- which is how the first version of this test was wrong.

        What is asserted instead is the property that matters and the route
        that carries it. A cancelled scan pruning nothing is proven at the
        scanner level, where the cancel can be set mid-pass."""

        for index in range(4):
            self.png(f"{index}.png")
        self.scan_now()
        self.assertEqual(4, self.get("/stats").payload["images"])

        reply = self.service.post(f"{ROUTE_PREFIX}/scan/cancel", {})
        self.assertTrue(reply.payload["ok"])
        self.assertEqual(4, self.get("/stats").payload["images"])


class GenerationIsolationTests(_Serving):
    """The Gallery is not allowed to take generation down with it.

    `record_generation` is called from the generation path, so an owner whose
    picture was made successfully must never be told it failed because an
    index could not be written.
    """

    INFOTEXT = "a knight\nSteps: 30, Sampler: Euler, Seed: 7"

    def test_a_generation_is_recorded(self) -> None:
        self.assertTrue(
            self.service.record_generation("abc123", self.INFOTEXT))
        with self.service.store().read() as connection:
            row = connection.execute(
                "SELECT prompt FROM image_metadata WHERE content_hash='abc123'"
            ).fetchone()
        self.assertEqual("a knight", row["prompt"])

    def test_a_broken_store_does_not_raise_into_generation(self) -> None:
        """The isolation property, proven by breaking the thing it protects
        against rather than by trusting the try/except is there."""

        self.service.close()

        class Exploding:
            location = None

            def write(self, *_a, **_k):
                raise RuntimeError("the disk went away")

            def read(self, *_a, **_k):
                raise RuntimeError("the disk went away")

            def close(self):
                # It cannot close either. `close()` runs from `server_close`,
                # so a store that fails here must not stop Studio shutting
                # down -- the first version of this fake had no `close` at all
                # and the cleanup raised, which is how that was found.
                raise RuntimeError("the disk went away")

        self.service._store = Exploding()  # noqa: SLF001
        self.assertFalse(
            self.service.record_generation("abc123", self.INFOTEXT))
        self.service.close()  # must not raise

    def test_a_gallery_that_cannot_run_records_nothing_and_says_so(
        self,
    ) -> None:
        service = GalleryService(
            Path(self._directory.name) / "no-pillow",
            probe=GalleryProbe(imaging=False),
        )
        self.addCleanup(service.close)
        self.assertFalse(service.record_generation("abc123", self.INFOTEXT))

    def test_a_result_with_no_hash_is_not_recorded(self) -> None:
        self.assertFalse(self.service.record_generation("", self.INFOTEXT))

    def test_a_generated_image_finds_its_prompt_once_scanned(self) -> None:
        """End to end, in the order it really happens: the picture is made and
        recorded, and only later does a scan meet the file.

        WAIT FOR ENRICHMENT, and this test is the reason that helper exists.

        The stored record is keyed by CONTENT HASH, and `images.content_hash`
        is filled by `_start_hashing()` -- a thread the scan kicks off and does
        not join, because a fingerprint costs a decode per image. `POST /scan`
        answers as soon as the files are indexed, so reading the metadata
        immediately afterwards races that thread: with the hash still NULL the
        join finds nothing, the answer falls back to the file (which carries no
        parameters here, deliberately), and `payload["prompt"]` raises
        KeyError.

        Measured at 3 passes in 10 without this wait, on an unchanged tree at
        `221bee89`. It had been flaky since long before the session that found
        it, and it passed often enough that a green canonical run looked like
        proof. See `Evidence/ar8.3-browser-baseline/` and PROJECT_STATE.

        The product is not racy in the way this test was: a real generated
        image carries its parameters in the file, so the embedded path answers
        it without the hash. This test uses `parameters=None` precisely to
        force the STORED path, and the stored path has an asynchronous
        precondition that the test has to honour.
        """

        from forge_studio.gallery_index import content_hash

        path = self.png("made.png", parameters=None)
        self.assertTrue(
            self.service.record_generation(content_hash(path), self.INFOTEXT))
        self.scan_now()
        payload = self.get(f"/image/{self.first_image_id()}/metadata").payload
        self.assertEqual("a knight", payload["prompt"])
        self.assertEqual("stored", payload["_source"])


class HashStatusTests(_Serving):
    """The shape the Duplicates panel reads, not the shape this module likes.

    Found in a browser: the panel showed `All {total} images hashed` with the
    placeholder unreplaced, because every field it touched was undefined.
    """

    def test_the_counts_the_panel_interpolates_are_all_present(self) -> None:
        self.png("a.png")
        self.png("b.png")
        self.scan_now()
        payload = self.get("/hash-status").payload
        for name in ("available", "hashing", "hashed", "total",
                     "hash_current", "hash_total"):
            with self.subTest(field=name):
                self.assertIn(name, payload)
        self.assertEqual(2, payload["total"])

    def test_hashing_starts_by_itself_after_a_scan(self) -> None:
        """`gallery.js` tells the owner "Hashing starts automatically after a
        scan". Until this worked, that sentence was false -- hashing only ever
        began if they found the button behind the Find-duplicates dialog."""

        self.png("a.png")
        self.png("b.png")
        self.scan_now()
        for _ in range(200):
            payload = self.get("/hash-status").payload
            if not payload["hashing"] and payload["hashed"] == payload["total"]:
                break
            time.sleep(0.05)
        payload = self.get("/hash-status").payload
        self.assertEqual(payload["total"], payload["hashed"])
        self.assertGreater(payload["total"], 0)

    def test_a_video_is_not_counted_as_unhashed_for_ever(self) -> None:
        """A clip has no perceptual hash and never will. Counting it as
        outstanding leaves the panel permanently reporting work to do."""

        (self.pictures / "clip.mp4").write_bytes(bytes([0, 0, 0, 24]) + b"ftypmp42")
        self.png("a.png")
        self.scan_now()
        for _ in range(200):
            if not self.get("/hash-status").payload["hashing"]:
                break
            time.sleep(0.05)
        payload = self.get("/hash-status").payload
        self.assertEqual(1, payload["total"])
        self.assertEqual(1, payload["hashed"])


class DuplicateReportTests(_Serving):
    """The duplicates payload, in the page's own field names."""

    def duplicates(self):
        from PIL import Image

        # Bigger than the 64x64 the hash resizes to. The suite's usual 40x30
        # is SMALLER, so both the original and its copy get upscaled -- from
        # different starting resolutions -- and the two hashes drift apart.
        # A perceptual hash compares pictures, not thumbnails of thumbnails.
        # And with LOW-frequency content. `(x * 3) % 256` is a sawtooth that
        # repeats every 85 pixels; halving the resolution aliases it into a
        # different pattern and the two hashes land 18 bits apart -- outside
        # the threshold, correctly. A perceptual hash describes pictures, and
        # a test that feeds it a moire pattern is testing the wrong thing.
        original = self.pictures / "original.png"
        image = Image.new("RGB", (240, 180))
        image.putdata([
            ((x + y * 2) % 256, (y + 40) % 256, (x // 2) % 256)
            for y in range(180) for x in range(240)
        ])
        image.save(original)
        image.resize((120, 90)).save(self.pictures / "copy.png")
        self.png("different.png", parameters=None)
        self.scan_now()
        for _ in range(300):
            if not self.get("/hash-status").payload["hashing"]:
                break
            time.sleep(0.05)
        return self.get("/duplicates").payload

    def test_the_summary_fields_the_page_interpolates_are_present(self) -> None:
        """A browser run rendered "undefined group - undefined file (keeping
        one per group would free NaN files)" because these were absent."""

        payload = self.duplicates()
        for name in ("groups", "total_groups", "total_duplicates"):
            with self.subTest(field=name):
                self.assertIn(name, payload)
        self.assertEqual(len(payload["groups"]), payload["total_groups"])

    def test_a_rescaled_copy_is_grouped_with_its_original(self) -> None:
        payload = self.duplicates()
        self.assertEqual(1, payload["total_groups"])
        names = {row["filename"] for row in payload["groups"][0]["images"]}
        self.assertEqual({"original.png", "copy.png"}, names)

    def test_a_group_reports_how_alike_it_is(self) -> None:
        payload = self.duplicates()
        similarity = payload["groups"][0]["similarity"]
        self.assertGreaterEqual(similarity, 90)
        self.assertLessEqual(similarity, 100)


class ParallelEnrichmentTests(_Serving):
    """Reading the library across cores, and the ways that must not go wrong.

    The scan reads headers; enrichment decodes. Decoding is pure arithmetic
    and the machine has more than one core, so it runs in a process pool --
    measured at 7.7x on a 28-core host, which turns seven minutes into one for
    a seventeen-thousand-image library.

    Processes rather than threads because the perceptual hash is pure Python
    and holds the GIL: threads plateaued at 2.5x however many were used.
    """

    def test_the_worker_count_leaves_the_machine_room(self) -> None:
        """Half the cores, capped. Studio may be making a picture while this
        runs, and an owner would rather their generation kept its cores."""

        from forge_studio.gallery_service import enrichment_workers

        self.assertEqual(1, enrichment_workers(1))
        self.assertEqual(1, enrichment_workers(2))
        self.assertEqual(2, enrichment_workers(4))
        self.assertEqual(8, enrichment_workers(16))
        self.assertEqual(16, enrichment_workers(64))

    def test_a_small_library_stays_on_one_core(self) -> None:
        """Starting a pool of processes to read thirty files costs more than
        it saves."""

        from forge_studio.gallery_service import ENRICH_PARALLEL_THRESHOLD

        self.assertGreater(ENRICH_PARALLEL_THRESHOLD, 8)
        self.png("a.png")
        self.scan_now()
        self.wait_for_enrichment()
        with self.service.store().read() as connection:
            row = connection.execute(
                "SELECT search_text, content_hash, phash FROM images"
            ).fetchone()
        self.assertIn("knight", row["search_text"])
        self.assertEqual(64, len(row["content_hash"]))
        self.assertEqual(64, len(row["phash"]))

    def test_parallel_and_serial_agree_exactly(self) -> None:
        """The assertion the whole change rests on. A faster pass that
        produced different hashes would be a silent corruption of the index,
        and duplicate detection would stop matching anything already stored.
        """

        import forge_studio.gallery_service as service

        for index in range(6):
            self.png(f"{index}.png")
        self.scan_now()
        self.wait_for_enrichment()
        serial = self.fingerprints()

        # Same files, same service, forced through the pool.
        with self.service.store().write() as connection:
            connection.execute(
                "UPDATE images SET phash='', content_hash='', search_text=''")
        original = service.ENRICH_PARALLEL_THRESHOLD
        service.ENRICH_PARALLEL_THRESHOLD = 1
        try:
            self.service.post(f"{ROUTE_PREFIX}/compute-hashes", {})
            self.wait_for_enrichment()
        finally:
            service.ENRICH_PARALLEL_THRESHOLD = original

        # The pass REPORTS how it read. Without this the test compares serial
        # with serial whenever the pool fails to start, and passes while
        # proving nothing -- which is what it did before the run said so.
        result = self.get("/hash-status").payload["result"]
        self.assertTrue(result["parallel"],
                        "the pool did not start; this compared serial to serial")
        self.assertGreater(result["in_pool"], 0,
                           "every task failed and was recovered in-process")
        self.assertEqual(0, result["recovered"])
        self.assertEqual(serial, self.fingerprints())

    def test_a_pool_that_cannot_start_falls_back(self) -> None:
        """A machine that refuses to fork must end up with a SLOWER Gallery,
        never a broken one."""

        import forge_studio.gallery_service as service

        for index in range(3):
            self.png(f"{index}.png")
        self.scan_now()
        self.wait_for_enrichment()
        expected = self.fingerprints()

        with self.service.store().write() as connection:
            connection.execute(
                "UPDATE images SET phash='', content_hash='', search_text=''")

        class Refuses:
            def __init__(self, *_a, **_k):
                raise OSError("this machine will not fork")

        original_pool = service.ProcessPoolExecutor if hasattr(
            service, "ProcessPoolExecutor") else None
        threshold = service.ENRICH_PARALLEL_THRESHOLD
        service.ENRICH_PARALLEL_THRESHOLD = 1
        import concurrent.futures as futures
        real = futures.ProcessPoolExecutor
        futures.ProcessPoolExecutor = Refuses
        try:
            self.service.post(f"{ROUTE_PREFIX}/compute-hashes", {})
            self.wait_for_enrichment()
        finally:
            futures.ProcessPoolExecutor = real
            service.ENRICH_PARALLEL_THRESHOLD = threshold
        result = self.get("/hash-status").payload["result"]
        self.assertFalse(result["parallel"], "it should have fallen back")
        self.assertEqual(expected, self.fingerprints())

    def fingerprints(self) -> list[tuple]:
        with self.service.store().read() as connection:
            return [
                (str(row["filename"]), str(row["phash"]),
                 str(row["content_hash"]), str(row["search_text"]))
                for row in connection.execute(
                    "SELECT filename, phash, content_hash, search_text "
                    "FROM images ORDER BY filename")
            ]


class BulkTests(_Serving):
    """Many at once, and what happens when one of them fails."""

    def several(self, count: int = 3) -> list[int]:
        for index in range(count):
            self.png(f"{index}.png")
        self.scan_now()
        return [row["id"] for row in self.get("/images").payload["images"]]

    def post(self, route: str, payload: dict) -> Any:
        return self.service.post(f"{ROUTE_PREFIX}{route}", payload)

    def test_a_tag_is_added_to_every_selected_image(self) -> None:
        identifiers = self.several()
        reply = self.post("/images/bulk-add-tag",
                          {"ids": identifiers, "tag": "Aria"})
        self.assertTrue(reply.payload["ok"])
        self.assertEqual(3, reply.payload["done"])
        self.assertEqual(3, self.get("/images",
                                     "?character=Aria").payload["total"])

    def test_a_tag_is_removed_from_every_selected_image(self) -> None:
        identifiers = self.several()
        self.post("/images/bulk-add-tag", {"ids": identifiers, "tag": "Aria"})
        self.post("/images/bulk-remove-tag",
                  {"ids": identifiers, "tag": "Aria"})
        self.assertEqual(0, self.get("/images",
                                     "?character=Aria").payload["total"])

    def test_many_images_are_deleted_to_the_trash(self) -> None:
        identifiers = self.several()
        reply = self.post("/images/bulk-delete", {"ids": identifiers})
        self.assertEqual(3, reply.payload["done"])
        self.assertEqual(0, self.get("/stats").payload["images"])
        self.assertEqual(3, len(self.get("/trash").payload))

    def test_everything_can_be_restored_at_once(self) -> None:
        identifiers = self.several()
        self.post("/images/bulk-delete", {"ids": identifiers})
        trash_ids = [row["id"] for row in self.get("/trash").payload]
        reply = self.post("/bulk-restore", {"trash_ids": trash_ids})
        self.assertEqual(3, reply.payload["done"])
        self.assertEqual(3, self.get("/stats").payload["images"])

    def test_one_failure_does_not_abandon_the_rest(self) -> None:
        """NOT all-or-nothing. Forty files selected and one open in another
        program should not mean the owner has to find which one and start
        again -- so each failure is named and the rest still happen."""

        identifiers = self.several()
        reply = self.post("/images/bulk-add-tag",
                          {"ids": identifiers + [999999], "tag": "Aria"})
        self.assertFalse(reply.payload["ok"])
        self.assertEqual(3, reply.payload["done"])
        self.assertEqual(1, reply.payload["failed"])
        self.assertEqual(999999, reply.payload["failures"][0]["id"])
        self.assertIn("not in the Gallery",
                      reply.payload["failures"][0]["error"])

    def test_a_series_is_renamed_and_numbered(self) -> None:
        identifiers = self.several()
        reply = self.post("/images/bulk-rename",
                          {"ids": identifiers, "base_name": "Aria"})
        self.assertEqual(3, reply.payload["done"])
        names = sorted(row["filename"]
                       for row in self.get("/images").payload["images"])
        self.assertEqual(["Aria 001.png", "Aria 002.png", "Aria 003.png"],
                         names)

    def test_a_rename_without_a_name_is_refused(self) -> None:
        reply = self.post("/images/bulk-rename", {"ids": [], "base_name": ""})
        self.assertEqual(400, reply.status)

    def test_tag_info_separates_shared_tags_from_partial_ones(self) -> None:
        """The page renders a tag differently when only some of the selection
        has it, and removing a partial tag is a different act."""

        identifiers = self.several()
        self.post("/images/bulk-add-tag", {"ids": identifiers, "tag": "Aria"})
        self.post("/images/bulk-add-tag",
                  {"ids": identifiers[:1], "tag": "Bran"})
        payload = self.post("/images/tag-info",
                            {"ids": identifiers}).payload
        # The page's shape: counts, and a total to compare them against. It
        # does the common/partial split itself, so sending a verdict instead
        # left the tag editor reporting "No tags on any selected image" over
        # images that were tagged.
        self.assertEqual(3, payload["total"])
        counts = {tag["name"]: tag["count"] for tag in payload["tags"]}
        self.assertEqual(3, counts["Aria"])   # shared by all three
        self.assertEqual(1, counts["Bran"])   # only the first

    def test_tag_info_on_nothing_is_empty_rather_than_an_error(self) -> None:
        payload = self.post("/images/tag-info", {"ids": []}).payload
        self.assertEqual({"tags": [], "total": 0}, payload)

    def test_images_are_converted_in_bulk(self) -> None:
        identifiers = self.several(2)
        reply = self.post("/images/bulk-convert",
                          {"ids": identifiers, "target_format": "webp"})
        self.assertEqual(2, reply.payload["done"])
        names = {row["filename"]
                 for row in self.get("/images").payload["images"]}
        self.assertTrue(all(name.endswith(".webp") for name in names), names)

    def test_metadata_is_stripped_in_bulk(self) -> None:
        identifiers = self.several(2)
        reply = self.post("/images/bulk-remove-metadata", {"ids": identifiers})
        self.assertEqual(2, reply.payload["done"])
        payload = self.get(f"/image/{identifiers[0]}/metadata").payload
        self.assertNotIn("prompt", payload)


class FolderRouteTests(_Serving):
    def test_a_folder_is_created_and_then_used(self) -> None:
        self.png("a.png")
        self.scan_now()
        reply = self.service.post(f"{ROUTE_PREFIX}/create-folder",
                                  {"parent": self.pictures.name,
                                   "name": "2026"})
        self.assertTrue(reply.payload["ok"])
        self.assertTrue((self.pictures / "2026").is_dir())

    def test_a_folder_is_renamed(self) -> None:
        self.png("sub/a.png")
        self.scan_now()
        reply = self.service.post(
            f"{ROUTE_PREFIX}/rename-folder",
            {"folder": f"{self.pictures.name}\\sub", "new_name": "renamed"})
        self.assertTrue(reply.payload["ok"])
        self.assertTrue((self.pictures / "renamed").is_dir())

    def test_a_folder_is_deleted_through_the_trash(self) -> None:
        self.png("sub/a.png")
        self.scan_now()
        reply = self.service.post(
            f"{ROUTE_PREFIX}/delete-folder",
            {"folder": f"{self.pictures.name}\\sub"})
        self.assertEqual(1, reply.payload["images"])
        self.assertEqual(1, len(self.get("/trash").payload))

    def test_characters_are_rescanned_from_filenames(self) -> None:
        self.png("Aria+Bran.png")
        self.scan_now()
        reply = self.service.post(f"{ROUTE_PREFIX}/rescan-characters", {})
        self.assertTrue(reply.payload["ok"])
        names = [row["name"] for row in self.get("/characters").payload]
        self.assertIn("Aria", names)
        self.assertIn("Bran", names)

    def test_the_next_number_in_a_series_is_reported(self) -> None:
        self.png("Aria 004.png")
        self.scan_now()
        reply = self.service.post(f"{ROUTE_PREFIX}/next-number",
                                  {"base_name": "Aria"})
        self.assertEqual(5, reply.payload["next"])


class ThreadSafetyTests(_Serving):
    def test_concurrent_reads_do_not_collide(self) -> None:
        """Studio serves from a thread pool, and one SQLite connection is
        shared. The store's lock is what makes that safe."""

        self.png("a.png")
        self.scan_now()
        failures: list[BaseException] = []

        def read() -> None:
            try:
                for _ in range(20):
                    self.get("/images")
                    self.get("/stats")
            except BaseException as error:  # noqa: BLE001
                failures.append(error)

        threads = [threading.Thread(target=read) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual([], failures)


class BoundaryTests(unittest.TestCase):
    def test_the_service_imports_nothing_from_the_engine(self) -> None:
        """The Gallery must never be able to take generation down with it."""

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_service.py").read_text(
                encoding="utf-8"
            )
        )
        forbidden = ("torch", "modules", "modules_forge", "backend", "gradio",
                     "forge_headless")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                with self.subTest(name=name):
                    self.assertNotIn(name.split(".")[0], forbidden)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_SERVICE_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
