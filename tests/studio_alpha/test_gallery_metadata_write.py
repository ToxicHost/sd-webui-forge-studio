"""The Gallery records what made every picture, whatever the toggle says. AR5.4.

THE DEFECT THIS CLOSES

Owner: "Embed Metadata should apply only to the saved image file. The Gallery
must record metadata locally and link it regardless. The Extension does this
today; Standalone does not."

The owner is right and the toggle is not the cause. `record_generation` had
exactly one caller in the whole tree and it was dead twice over:

    source_api_adapter.py:1370   content_hash = metadata.get("image_sha256", "")
                                 ^ only writer: mock_backend.py:441
                    :1375        self._gallery.record_generation(...)
                                 ^ inside _canonical_result, inside the BLOCKING
                                   SourceFrontendAdapter.generate, which a real
                                   install never enters (AR5.3)

So no real generation has ever written an `image_metadata` row. With Embed
Metadata ON the scan parses the parameters back out of the PNG chunk and nothing
looks wrong. With it OFF the file carries nothing, the row that should have
covered it was never written, and the owner's parameters are gone permanently.

THE ORACLE, stated exactly. `req.embed_metadata` appears only where the
Extension builds `save_kwargs` (`studio_api.py:3227`, `:4542`, `:4558`, `:4568`)
and in no Gallery-write condition; each of its three writes is gated on that
image's infotext (`:3266`, `:3342`, `:4578`). Neo agrees about its half --
`opts.enable_pnginfo` (`modules/images.py:589`, `:607`) reaches only the file --
and keeps no database to disagree about.

NOT "the database always records", which an earlier draft of this file claimed.
The Extension's writes are nested inside its save branches (`:3215`, `:3279`,
`:4513`) and its `else` at `:3353` writes neither file nor row. `save_outputs`
gates both halves; `embed_metadata` gates only the file, and that is the half
the owner asked about. Studio has no `save_outputs` on any request, so its
unconditional write matches the Extension in every state Studio can reach --
`ASaveOutputsToggleWouldChangeThisTests` is the tripwire for the day it does
not.

WHY THESE TESTS DRIVE THE ROUTE

The same reason `test_gallery_autosync_live.py` gives, and it applies harder
here. A test that calls `record_generation` -- or `_record_generation_metadata`
-- directly WOULD HAVE PASSED ON EVERY COMMIT for as long as this defect
existed. `gallery_actions.record_generation` was always correct; nothing on the
live path called it. So `TheLiveRouteRecordsTests` builds a socket-free handler
and drives the real `do_GET` dispatch with a real path string.

AND WHY ONE OF THEM RUNS A REAL SCAN

`image_metadata` rows are keyed by content hash and linked to an image row later,
by `gallery_service.py:1623-1630`, on equality of that hash alone. A generation
that hashes the image one way and a scan that hashes the file another way
produces a row that is written, reports success, and never links -- with no
symptom at all until an owner opens an old picture. Asserting that two functions
agree would not catch it. `TheHashMustMatchTheScanTests` writes a real PNG,
records it through the route, then scans the folder and asserts the row LINKED.

Review: `Evidence/source-review/AR5.4-gallery-metadata-write.md`.
"""

from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import OutputSettings  # noqa: E402
from forge_studio.gallery_index import content_hash, imaging_available  # noqa: E402
from forge_studio.gallery_service import ROUTE_PREFIX, GalleryService  # noqa: E402

EXPECTED_METADATA_WRITE_TESTS = 40

PRESENTATION_PY = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
    encoding="utf-8")
ADAPTER_PY = (APP_ROOT / "forge_studio" / "source_api_adapter.py").read_text(
    encoding="utf-8")

INFOTEXT = (
    "a knight on a hill\n"
    "Negative prompt: blurry\n"
    "Steps: 30, Sampler: Euler, Schedule type: Beta 57, Seed: 7, "
    "Size: 40x30, Model: Anitox"
)


_DOCSTRING_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                     ast.ClassDef)


def code_only(source: str, function: str | None = None) -> str:
    """`source` as an AST dump: code, with docstrings and comments gone.

    EVERY WORD-BAN GUARD IN THIS FILE READS THIS, and the reason is that both
    of them failed on their first run when written as `assertNotIn` over the
    file text. They matched the comment EXPLAINING the ban -- so documenting
    why `image_sha256` must not be read, and why the handler must not consult
    `embed_metadata`, broke the tests asserting exactly that. A guard that
    cannot survive its own rationale being written down is a guard that
    punishes the documentation.

    Comments never reach an AST at all; docstrings do, so they are removed
    from the four node types that can own one. `function` narrows to one
    definition, which matters for `embed_metadata` -- `_validated_output` reads
    it legitimately three hundred lines away.
    """

    tree: ast.AST = ast.parse(source)
    if function is not None:
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == function):
                tree = node
                break
        else:
            raise AssertionError(f"{function!r} is not in this source")
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.dump(tree)


def presentation():
    return importlib.import_module("forge_studio.presentation")


def port():
    return importlib.import_module("forge_headless.live_generation_port")


def png_bytes(tag: int = 0, *, parameters: str | None = None) -> bytes:
    """A real, decodable PNG whose pixels depend on `tag`.

    Distinct pixels per tag, because two images that happen to be identical
    would share a content hash and make a "two jobs both recorded" assertion
    pass for the wrong reason.
    """

    from io import BytesIO

    from PIL import Image, PngImagePlugin

    image = Image.new("RGB", (40, 30))
    image.putdata([
        ((x * 3 + tag) % 256, (y * 5 + tag) % 256, (x + y + tag) % 256)
        for y in range(30) for x in range(40)
    ])
    info = None
    if parameters is not None:
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", parameters)
    buffer = BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


class _Payload:
    """What `read_result_asset` answers with: bytes and a type, never a path."""

    def __init__(self, content: bytes, media_type: str = "image/png") -> None:
        self.content = content
        self.media_type = media_type


class _Gallery:
    """A Gallery that only records being told."""

    def __init__(self, *, record_raises: bool = False,
                 note_raises: bool = False) -> None:
        self.records: list[tuple[str, str, dict]] = []
        self.notified = 0
        self._record_raises = record_raises
        self._note_raises = note_raises

    def record_generation(self, content_hash_value, infotext, settings=None):
        self.records.append((content_hash_value, infotext, dict(settings or {})))
        if self._record_raises:
            raise RuntimeError("the index is on fire")
        return True

    def note_generation(self) -> None:
        self.notified += 1
        if self._note_raises:
            raise RuntimeError("the scanner is on fire")


def build(status, *, gallery=None, asset=None, asset_raises=False,
          no_server=False):
    """A socket-free handler wired to a stub presentation and a Gallery.

    The SERVER is a real `_StudioHTTPServer` built without running `__init__`,
    so `_gallery` exercises its actual `isinstance` check rather than a property
    this test replaced. Copied from `test_gallery_autosync_live.py` and extended
    with `read_result_asset`, which is how the handler reaches the delivered
    bytes.
    """

    module = presentation()
    sent: dict = {}

    server = object.__new__(module._StudioHTTPServer)
    if gallery is not None:
        server.gallery_service = gallery

    class _Surface:
        def __init__(self) -> None:
            self.asked: list[str] = []
            self.read: list[str] = []

        def job_status(self, job_id: str):
            self.asked.append(job_id)
            return status

        def read_result_asset(self, handle: str):
            self.read.append(handle)
            if asset_raises:
                raise RuntimeError("that result is no longer retained")
            return asset

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
    handler.server = object() if no_server else server
    return handler, sent, surface, server


def completed(job_id: str = "studio-job-000001", *, infotext: str = INFOTEXT,
              handle: str = "studio-result/abc.png", **metadata):
    """A completed job status shaped exactly as `poll()` assembles one.

    `metadata` carries the engine's resolved facts and the infotext
    (`studio_generation.py:1228-1243`); `image_handle` is added by
    `presentation.py:1947` and is the only way to the bytes.
    """

    return {
        "job_id": job_id,
        "state": "completed",
        "result": {
            "job_id": job_id,
            "state": "completed",
            "mime_type": "image/png",
            "image_handle": handle,
            "image_media_type": "image/png",
            "image_byte_length": 1234,
            "metadata": {
                "schema_version": "studio-headless-result/v1",
                "width": 40,
                "height": 30,
                "seed": 7,
                "request_id": job_id,
                "infotext": infotext,
                **metadata,
            },
        },
    }


class _Imaging(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")


class TheLiveRouteRecordsTests(_Imaging):
    """Driven through `do_GET`, because that is what was broken."""

    def test_a_completed_job_records_the_metadata(self) -> None:
        gallery = _Gallery()
        raw = png_bytes(1)
        handler, _sent, surface, _server = build(
            completed(), gallery=gallery, asset=_Payload(raw))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(["studio-job-000001"], surface.asked,
                         "the route did not answer at all")
        self.assertEqual(1, len(gallery.records),
                         "the live route did not record the metadata")
        self.assertEqual(INFOTEXT, gallery.records[0][1])

    def test_the_hash_is_of_the_delivered_pixels(self) -> None:
        """Not of the file bytes, not of a data URL, not of the mock's key.

        `gallery_service.py:1623-1630` links on this value and nothing else.
        """

        gallery = _Gallery()
        raw = png_bytes(2)
        handler, _sent, _surface, _server = build(
            completed(), gallery=gallery, asset=_Payload(raw))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(content_hash(raw), gallery.records[0][0])
        self.assertEqual(64, len(gallery.records[0][0]),
                         "bare hex, not a `sha256:`-prefixed fixture identity")

    def test_the_job_response_is_still_served(self) -> None:
        """The write must not replace or disturb the answer."""

        gallery = _Gallery()
        expected = completed()
        handler, sent, _surface, _server = build(
            expected, gallery=gallery, asset=_Payload(png_bytes(3)))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(expected, sent["body"])

    def test_it_records_once_and_not_once_per_poll(self) -> None:
        """A completed job can be asked for repeatedly. Each ask would
        otherwise re-read and re-decode the whole image."""

        gallery = _Gallery()
        handler, _sent, surface, _server = build(
            completed(), gallery=gallery, asset=_Payload(png_bytes(4)))
        handler.path = "/api/jobs/studio-job-000001"
        for _ in range(5):
            handler.do_GET()
        self.assertEqual(1, len(gallery.records))
        self.assertEqual(1, len(surface.read),
                         "the result was read again on a later poll")

    def test_two_different_jobs_both_record(self) -> None:
        """The guard is per job, not a latch that fires once per session."""

        gallery = _Gallery()
        shared = None
        for index, job in enumerate(("studio-job-000001", "studio-job-000002")):
            handler, _s, _p, server = build(
                completed(job), gallery=gallery,
                asset=_Payload(png_bytes(10 + index)))
            # ONE server across both, as a real host has -- otherwise each job
            # gets a fresh guard set and this passes without proving anything.
            shared = shared or server
            handler.server = shared
            handler.path = f"/api/jobs/{job}"
            handler.do_GET()
        self.assertEqual(2, len(gallery.records))
        self.assertNotEqual(gallery.records[0][0], gallery.records[1][0])
        self.assertEqual({"studio-job-000001", "studio-job-000002"},
                         shared._gallery_metadata_jobs)

    def test_the_engines_settings_travel_with_the_infotext(self) -> None:
        """Where the two disagree the engine wins -- a seed of -1 in the prompt
        box became a real number by the time the picture existed. That is what
        `gallery_actions._parsed_fields` is built to express, and it can only
        express it if the resolved mapping actually arrives."""

        gallery = _Gallery()
        handler, _sent, _surface, _server = build(
            completed(seed=4242), gallery=gallery, asset=_Payload(png_bytes(5)))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        settings = gallery.records[0][2]
        self.assertEqual(4242, settings["seed"])
        self.assertEqual(40, settings["width"])
        self.assertEqual(30, settings["height"])


class TheToggleGovernsTheFileOnlyTests(_Imaging):
    """The owner's sentence, both halves, on the real functions.

    The file half is `live_generation_port.metadata_save_kwargs`, the real
    decision the real save path makes. The database half is the real route and
    a real `GalleryService`. A test that checked only the file passes today.
    """

    def setUp(self) -> None:
        super().setUp()
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.pictures = self.base / "pictures"
        self.pictures.mkdir()

    def save(self, *, embed: bool) -> Path:
        """Save a PNG exactly as `_publish` would, with the toggle at `embed`."""

        from PIL import Image

        kwargs = port().metadata_save_kwargs(
            INFOTEXT, "PNG", OutputSettings(format="png", embed_metadata=embed))
        target = self.pictures / f"studio-{int(embed)}.png"
        image = Image.new("RGB", (40, 30))
        image.putdata([
            ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)
            for y in range(30) for x in range(40)
        ])
        image.save(target, format="PNG", **kwargs)
        return target

    def chunk_of(self, target: Path):
        from PIL import Image

        with Image.open(target) as opened:
            return opened.info.get("parameters")

    def record_through_the_route(self, target: Path, service: GalleryService):
        handler, _sent, _surface, _server = build(
            completed(), gallery=service,
            asset=_Payload(target.read_bytes()))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()

    def stored_row(self, service: GalleryService, digest: str):
        with service.store().read() as connection:
            return connection.execute(
                "SELECT * FROM image_metadata WHERE content_hash = ?",
                (digest,),
            ).fetchone()

    def test_embed_off_writes_no_parameters_chunk(self) -> None:
        self.assertIsNone(self.chunk_of(self.save(embed=False)))

    def test_embed_on_writes_the_parameters_chunk(self) -> None:
        """The half that already worked, pinned so the fix below cannot be
        mistaken for having changed it."""

        self.assertEqual(INFOTEXT, self.chunk_of(self.save(embed=True)))

    def test_embed_off_still_records_the_gallery_metadata(self) -> None:
        """THE OWNER'S REPORT. The file carries nothing and the database has
        everything -- which is the only arrangement in which turning the toggle
        off is a formatting choice rather than permanent data loss."""

        target = self.save(embed=False)
        service = GalleryService(self.base / "state")
        self.addCleanup(service.close)
        self.record_through_the_route(target, service)
        row = self.stored_row(service, content_hash(target))
        self.assertIsNotNone(row, "the Gallery recorded nothing")
        self.assertEqual(INFOTEXT, row["raw_infotext"])
        self.assertEqual("a knight on a hill", row["prompt"])
        self.assertEqual("Euler", row["sampler"])

    def test_embed_on_records_the_gallery_metadata_too(self) -> None:
        """The database write does not consult the toggle in either direction.
        Recording only when the file does NOT carry it would look correct and
        would lose every row the moment an owner turned the toggle back on."""

        target = self.save(embed=True)
        service = GalleryService(self.base / "state")
        self.addCleanup(service.close)
        self.record_through_the_route(target, service)
        self.assertIsNotNone(self.stored_row(service, content_hash(target)))

    def test_the_handler_never_consults_embed_metadata(self) -> None:
        """Asserted on the source, because a behavioural test can only sample
        the two states this suite happens to drive.

        On the CODE, not the text: the handler's own docstring says the toggle
        governs the file and this write never reads it, and a text search
        matched that sentence."""

        self.assertNotIn(
            "embed_metadata",
            code_only(PRESENTATION_PY, "_record_generation_metadata"))


class TheHashMustMatchTheScanTests(_Imaging):
    """The failure mode with no symptom.

    A row written under one hash and a file indexed under another links to
    nothing, reports success, and is discovered months later by an owner
    opening an old picture. So this drives a REAL scan rather than comparing
    two function calls.
    """

    def setUp(self) -> None:
        super().setUp()
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.pictures = self.base / "pictures"
        self.pictures.mkdir()
        self.service = GalleryService(self.base / "state")
        self.addCleanup(self.service.close)
        # No `parameters` chunk: the file must be unable to answer, so that a
        # metadata lookup which succeeds can only have come from the database.
        self.target = self.pictures / "studio-00001-7.png"
        self.target.write_bytes(png_bytes(21))
        self.digest = content_hash(self.target)

    def record(self) -> None:
        handler, _sent, _surface, _server = build(
            completed(), gallery=self.service,
            asset=_Payload(self.target.read_bytes()))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()

    def scan_now(self) -> None:
        self.service.post(f"{ROUTE_PREFIX}/add-folder",
                          {"path": str(self.pictures)})
        self.service.post(f"{ROUTE_PREFIX}/scan")
        # The scan answers as soon as the files are indexed and hashes on a
        # thread it does not join -- and the orphan link happens in that pass
        # (`gallery_service.py:1623`), so reading before it settles races it.
        for _ in range(600):
            if not self.service.get(
                    f"{ROUTE_PREFIX}/hash-status").payload["hashing"]:
                return
            time.sleep(0.05)
        self.fail("enrichment did not finish")

    def image_id(self) -> int:
        return self.service.get(
            f"{ROUTE_PREFIX}/images").payload["images"][0]["id"]

    def linked_id(self):
        with self.service.store().read() as connection:
            row = connection.execute(
                "SELECT image_id FROM image_metadata WHERE content_hash = ?",
                (self.digest,),
            ).fetchone()
        return None if row is None else row["image_id"]

    def test_the_row_is_written_before_any_scan_has_met_the_file(self) -> None:
        """The whole reason `image_metadata.image_id` is nullable."""

        self.record()
        with self.service.store().read() as connection:
            row = connection.execute(
                "SELECT raw_infotext, image_id FROM image_metadata "
                "WHERE content_hash = ?", (self.digest,)).fetchone()
        self.assertIsNotNone(row, "no row was written at all")
        self.assertEqual(INFOTEXT, row["raw_infotext"])
        self.assertIsNone(row["image_id"], "there is no image row to link to yet")

    def test_the_scan_links_the_row_it_finds(self) -> None:
        """THE ASSERTION THAT CATCHES A DIVERGED HASH. Nothing else does."""

        self.record()
        self.scan_now()
        self.assertEqual(self.image_id(), self.linked_id())

    def test_the_gallery_answers_with_the_stored_parameters(self) -> None:
        """End to end, in the owner's terms: a file that carries nothing, and a
        Gallery that still says what made it."""

        self.record()
        self.scan_now()
        payload = self.service.get(
            f"{ROUTE_PREFIX}/image/{self.image_id()}/metadata").payload
        self.assertEqual("stored", payload["_source"])
        self.assertEqual("a knight on a hill", payload["prompt"])
        self.assertEqual(7, payload["seed"])


class OnlyAFinishedPictureCountsTests(_Imaging):
    """Reading and decoding a result is real work. Do not spend it on nothing."""

    def drive(self, status, **kwargs):
        gallery = _Gallery()
        handler, _sent, surface, _server = build(
            status, gallery=gallery, asset=_Payload(png_bytes(9)), **kwargs)
        handler.path = f"/api/jobs/{status.get('job_id', 'j')}"
        handler.do_GET()
        return gallery, surface

    def unfinished(self, state: str, **extra):
        """A non-terminal status that DOES carry a full result payload.

        Deliberately not a bare `{"job_id": ..., "state": ...}`. Production
        checks the state first and the result second, so a status with no
        `result` key is stopped by the second guard and the STATE guard is
        pinned by nothing at all -- three tests that look like coverage and
        assert one thing between them. Giving them a result the handler could
        act on is what makes the state the only thing saying no.
        """

        status = completed(job_id="j")
        status["state"] = state
        status["result"]["state"] = state
        status.update(extra)
        return status

    def test_a_running_job_records_nothing(self) -> None:
        gallery, _s = self.drive(self.unfinished("running", progress={}))
        self.assertEqual([], gallery.records)

    def test_a_cancelled_job_records_nothing(self) -> None:
        gallery, _s = self.drive(self.unfinished("cancelled"))
        self.assertEqual([], gallery.records)

    def test_a_failed_job_records_nothing(self) -> None:
        gallery, _s = self.drive(
            self.unfinished("failed", error={"code": "X"}))
        self.assertEqual([], gallery.records)

    def test_completed_with_no_result_records_nothing(self) -> None:
        gallery, _s = self.drive({"job_id": "j", "state": "completed"})
        self.assertEqual([], gallery.records)

    def test_a_result_with_no_infotext_records_nothing(self) -> None:
        """The Extension's own gate. A backend that produces no parameter
        string has nothing to record, and an empty row keyed by a real hash
        would shadow a later real one under `INSERT OR REPLACE`."""

        gallery, surface = self.drive(completed(infotext=""))
        self.assertEqual([], gallery.records)
        self.assertEqual([], surface.read,
                         "the result was read before the infotext was checked")

    def test_a_result_with_no_handle_records_nothing(self) -> None:
        """No delivered file means no pixels to key on, and no file for a scan
        to meet either."""

        gallery, _s = self.drive(completed(handle=""))
        self.assertEqual([], gallery.records)


class ItCannotBreakAGenerationTests(_Imaging):
    """The picture was made. Nothing about an index may say otherwise."""

    def test_a_gallery_that_raises_does_not_break_the_response(self) -> None:
        gallery = _Gallery(record_raises=True)
        expected = completed()
        handler, sent, _s, _srv = build(
            expected, gallery=gallery, asset=_Payload(png_bytes(6)))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(1, len(gallery.records))
        self.assertEqual(expected, sent["body"])

    def test_a_result_that_cannot_be_read_does_not_break_the_response(self) -> None:
        """`RESULT_GONE` is a real answer -- the registry is bounded and evicts.
        A picture whose bytes have been released still has to serve its job."""

        gallery = _Gallery()
        expected = completed()
        handler, sent, _s, _srv = build(
            expected, gallery=gallery, asset=None, asset_raises=True)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual([], gallery.records)
        self.assertEqual(expected, sent["body"])

    def test_an_unreadable_result_is_attempted_only_once(self) -> None:
        """MARKED SEEN BEFORE THE WORK, pinned.

        `RESULT_GONE` is a real answer: the registry is bounded and evicts
        (`result_delivery.py:209-214`). If the job were marked seen only after
        a read that succeeded, every later poll of that job would read and
        decode the result again, for ever, on the response path of a route the
        page polls in a loop -- and the job that most needs the bound is
        exactly the one whose read keeps failing.

        Nothing else in the tree says so. Moving `seen.add(job_id)` below the
        read leaves the whole suite green apart from this test, which is why
        it exists as its own case rather than as an extra assertion on the
        success path.
        """

        gallery = _Gallery()
        handler, _sent, surface, _server = build(
            completed(), gallery=gallery, asset=None, asset_raises=True)
        handler.path = "/api/jobs/studio-job-000001"
        for _ in range(4):
            handler.do_GET()
        self.assertEqual([], gallery.records)
        self.assertEqual(1, len(surface.read),
                         "an unreadable result was read again on every poll")

    def test_an_undecodable_result_records_nothing_and_does_not_raise(self) -> None:
        """The mock renders an SVG. Pillow cannot open one, so it has no pixel
        identity -- and a scan could not index it either. Recording nothing is
        the honest answer, not a degraded one."""

        gallery = _Gallery()
        expected = completed()
        handler, sent, _s, _srv = build(
            expected, gallery=gallery,
            asset=_Payload(b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                           "image/svg+xml"))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual([], gallery.records)
        self.assertEqual(expected, sent["body"])

    def test_a_host_with_no_gallery_serves_the_route_unchanged(self) -> None:
        """No state root means no Gallery -- the demo path and the browser
        harness both build a server that way."""

        expected = completed()
        handler, sent, surface, _srv = build(
            expected, asset=_Payload(png_bytes(7)))     # no gallery set
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(expected, sent["body"])
        self.assertEqual(["studio-job-000001"], surface.asked)

    def test_a_non_studio_server_serves_the_route_unchanged(self) -> None:
        """`_gallery` guards on isinstance. A handler bound to something else
        must fall through rather than raise."""

        expected = completed()
        handler, sent, _s, _srv = build(
            expected, asset=_Payload(png_bytes(8)), no_server=True)
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        self.assertEqual(expected, sent["body"])


class TheTwoHooksAreIndependentTests(_Imaging):
    """One route, two Gallery concerns. Neither may swallow the other.

    ARRIVAL (AR5.3, `note_generation`) is about a file appearing in a linked
    folder. IDENTITY (AR5.4, `record_generation`) is about what made it. An
    image whose pixels cannot be hashed still arrived; a row written for an
    image the owner never scrolls to is still what saves their parameters.
    """

    def drive(self, gallery, asset=None):
        handler, sent, _s, server = build(
            completed(), gallery=gallery,
            asset=asset if asset is not None else _Payload(png_bytes(11)))
        handler.path = "/api/jobs/studio-job-000001"
        handler.do_GET()
        return sent, server

    def test_a_failing_record_still_lets_the_notification_through(self) -> None:
        gallery = _Gallery(record_raises=True)
        self.drive(gallery)
        self.assertEqual(1, gallery.notified)

    def test_a_failing_notification_does_not_undo_the_record(self) -> None:
        gallery = _Gallery(note_raises=True)
        self.drive(gallery)
        self.assertEqual(1, len(gallery.records))

    def test_an_unhashable_image_still_refreshes_the_gallery(self) -> None:
        gallery = _Gallery()
        self.drive(gallery, _Payload(b"not an image at all", "image/svg+xml"))
        self.assertEqual([], gallery.records)
        self.assertEqual(1, gallery.notified)

    def test_the_two_guards_are_separate_sets(self) -> None:
        """A shared set would let whichever ran first silence the other."""

        gallery = _Gallery()
        _sent, server = self.drive(gallery)
        self.assertIn("_gallery_metadata_jobs", vars(server))
        self.assertIn("_gallery_noted_jobs", vars(server))
        self.assertIsNot(server._gallery_metadata_jobs,
                         server._gallery_noted_jobs)

    def test_the_record_happens_before_the_notification(self) -> None:
        """Order with a reason: the notification starts a scan, and a row that
        already exists is linked by THAT scan (`gallery_service.py:1623`)
        instead of waiting for the next one."""

        dispatch = code_only(PRESENTATION_PY, "do_GET")
        self.assertLess(dispatch.index("_record_generation_metadata"),
                        dispatch.index("_note_generation_to_gallery"))


class EveryOutputFormatAgreesTests(_Imaging):
    """The join, checked for every format Studio can actually emit.

    `OUTPUT_FORMATS` (`live_generation_port.py:50-55`) is png, jpeg and webp,
    and the owner picks between them. The Extension warns about exactly this in
    its own comments -- it hashes the PIL image for PNG but RE-READS THE FILE
    for the lossy pair, because "post-encode pixels differ from the original"
    (`studio_api.py:3335-3341`). Studio takes the general branch for all three
    by hashing the delivered file's bytes, and this is where that claim is
    measured rather than asserted.

    Three values must agree per format: the bytes the handler hashes, the path
    the scan would hash, and the digest `enrich` actually produces from its
    single decode.
    """

    FORMATS = (("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp"))

    def source_image(self):
        from PIL import Image

        image = Image.new("RGB", (40, 30))
        image.putdata([
            ((x * 7) % 256, (y * 11) % 256, (x * y) % 256)
            for y in range(30) for x in range(40)
        ])
        return image

    def test_the_bytes_the_path_and_the_scan_agree(self) -> None:
        from forge_studio.gallery_index import enrich

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            for encoder, extension in self.FORMATS:
                with self.subTest(format=encoder):
                    target = Path(folder) / f"a.{extension}"
                    self.source_image().save(target, format=encoder)
                    raw = target.read_bytes()
                    from_bytes = content_hash(raw)
                    self.assertNotEqual("", from_bytes)
                    self.assertEqual(content_hash(target), from_bytes)
                    self.assertEqual(enrich(target).content_hash, from_bytes)

    def test_a_lossy_save_really_does_move_the_pixels(self) -> None:
        """Without this the test above could be passing vacuously.

        If JPEG happened to round-trip these pixels exactly, "hash the file
        rather than the pre-encode image" would be a distinction with no
        difference and the guard would prove nothing. It does not round-trip:
        this is the measurement behind the Extension's comment, and behind
        Studio's choice to hash the delivered bytes for every format instead of
        copying the PNG shortcut.
        """

        from io import BytesIO

        source = self.source_image()
        buffer = BytesIO()
        source.save(buffer, format="JPEG")
        self.assertNotEqual(content_hash(source),
                            content_hash(buffer.getvalue()))


class ASaveOutputsToggleWouldChangeThisTests(unittest.TestCase):
    """A tripwire, not a behaviour test, and it says so.

    The Extension gates its Gallery writes on TWO things, not one. Every one of
    them is nested inside a save branch -- `if req.save_outputs and
    req.save_format == "png":` (`studio_api.py:3215`), the lossy `elif`
    (`:3279`), `if req.save_outputs:` (`:4513`) -- and the `else` at `:3353`
    returns base64 with no file, no hash and no row. `embed_metadata` gates only
    the file; `save_outputs` gates both halves.

    Studio's write is unconditional, and today that matches: no request Studio
    can build carries a `save_outputs` field, so there is no state in which the
    two behave differently. The page has the toggle (`index.html:1628`) but it
    sits below the lifecycle `return` and reaches nothing.

    THIS TEST FAILS THE DAY SOMEONE WIRES IT, which is the point. A write that
    silently keeps recording rows for images the owner asked not to save is
    exactly the kind of divergence that has no symptom until it is the whole
    problem.
    """

    def test_no_request_contract_carries_a_save_outputs_field(self) -> None:
        from dataclasses import fields

        from forge_headless.generation_request import (
            FirstImageRequest,
            OutputOptions,
        )
        from forge_studio.contracts import GenerationRequest as StudioRequest
        from forge_studio.contracts import OutputSettings

        # Both sides of the seam: what the page sends Studio, and what Studio
        # hands the engine. A toggle wired into either one reaches this write.
        for contract in (OutputSettings, StudioRequest,
                         OutputOptions, FirstImageRequest):
            with self.subTest(contract=contract.__name__):
                self.assertNotIn(
                    "save_outputs", {f.name for f in fields(contract)},
                    "Studio now has a save-outputs control. The Extension "
                    "gates its Gallery write on that toggle as well as on the "
                    "infotext (studio_api.py:3215, :3279, :4513, and the else "
                    "at :3353) -- so `_record_generation_metadata` has to "
                    "honour it, or Studio records rows for images the owner "
                    "asked not to keep.")


class ExactlyOneContentHashTests(unittest.TestCase):
    """Two implementations of this hash is how the orphan stops linking.

    The tree already holds four different image digests -- pixels, file bytes,
    the mock's SVG source, and Neo's truncated filename hash. Only one of them
    is a Gallery key, and it must live in exactly one FILE.

    One file, not one line: `gallery_index.enrich` spells the same algorithm
    out a second time on purpose, so the scan derives parameters, dimensions
    and the hash from ONE decode instead of three. That copy is three lines
    from the function it mirrors, where a change to either is visible against
    the other -- and `TheHashMustMatchTheScanTests` proves the two still agree
    by running a real scan rather than by reading them.
    """

    ROOTS = ("forge_studio", "forge_headless")

    def hashing_files(self) -> set[str]:
        found: set[str] = set()
        for root in self.ROOTS:
            for path in (APP_ROOT / root).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                # The ALGORITHM, not the word: a comment naming it must not
                # count, and a second copy under another name must.
                if 'convert("RGB").tobytes()' in text:
                    found.add(path.name)
        return found

    def test_only_gallery_index_hashes_decoded_pixels(self) -> None:
        self.assertEqual({"gallery_index.py"}, self.hashing_files())

    def test_the_adapter_no_longer_reads_the_mock_key(self) -> None:
        """`metadata["image_sha256"]` is a digest of SVG source text,
        `sha256:`-prefixed. It was never a content hash, so the row it keyed
        could not have linked even on the mock.

        On the CODE, not the text: the helper that replaced it names the key in
        its docstring so the next reader knows what was wrong with it, and a
        text search matched that."""

        self.assertNotIn("image_sha256", code_only(ADAPTER_PY))

    def test_the_adapter_gates_the_record_on_the_infotext(self) -> None:
        """The Extension's gate (`studio_api.py:3262`, `:3339`, `:4578`), not
        on `embed_metadata` and not on the image list."""

        at = ADAPTER_PY.index("self._gallery.record_generation(")
        # Back to the `if` that guards it, not to the previous line: the
        # comment block between them is several lines long.
        guard = ADAPTER_PY.rindex("if self._gallery is not None", 0, at)
        self.assertIn("infotext", ADAPTER_PY[guard:at])

    def test_bytes_and_a_path_agree_on_the_same_image(self) -> None:
        """The property the whole design rests on, asserted directly as well as
        through the scan: the delivery layer holds bytes, the scan holds a path,
        and both must produce the same key."""

        if not imaging_available():
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            target = Path(folder) / "a.png"
            raw = png_bytes(33)
            target.write_bytes(raw)
            self.assertEqual(content_hash(target), content_hash(raw))
            self.assertNotEqual("", content_hash(raw))

    def test_something_that_is_not_an_image_hashes_to_nothing(self) -> None:
        """Guarded, because `content_hash` RAISES without Pillow rather than
        answering "no metadata" -- `_require_imaging()` sits one line above the
        `try` at `gallery_index.py:261-262` and `ImagingUnavailable` is a
        `RuntimeError`, so the `except (OSError, ValueError)` never sees it.

        That is deliberate in production (an absent capability is not the same
        fact as an unreadable file) and it means this test needs the guard the
        source scans around it do not. Without it the module errors under the
        documented discovery command in `docs/studio/OPERATIONS.md:326`, which
        runs `-I -S -B` and therefore has no site-packages and no Pillow --
        while `run_tests.py`, the canonical gate, stays green. Found that way.
        """

        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self.assertEqual("", content_hash(b"not an image at all"))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_METADATA_WRITE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
