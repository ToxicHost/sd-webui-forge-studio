"""Making the small version, and not making it twice."""

from __future__ import annotations

import ast
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_index import imaging_available  # noqa: E402
from forge_studio.gallery_thumbnails import (  # noqa: E402
    THUMBNAIL_SIZE,
    VIDEO_PLACEHOLDER,
    ThumbnailCache,
    ffmpeg_path,
    identity,
    render,
)

EXPECTED_GALLERY_THUMBNAIL_TESTS = 23


class _Images(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self.pictures = base / "pictures"
        self.pictures.mkdir()
        self.cache = ThumbnailCache(base / "thumbs")

    def png(self, name: str = "a.png", size: tuple[int, int] = (1200, 800)):
        from PIL import Image

        image = Image.new("RGB", size)
        image.putdata([
            ((x * 3) % 256, (y * 5) % 256, (x + y) % 256)
            for y in range(size[1]) for x in range(size[0])
        ])
        path = self.pictures / name
        image.save(path)
        return path

    def opened(self, data: bytes):
        from io import BytesIO

        from PIL import Image

        return Image.open(BytesIO(data))


class RenderTests(_Images):
    def test_a_thumbnail_is_smaller_than_the_source(self) -> None:
        data = render(self.png())
        self.assertIsNotNone(data)
        image = self.opened(data)
        self.assertLessEqual(max(image.size), THUMBNAIL_SIZE)

    def test_the_aspect_ratio_survives(self) -> None:
        """A squashed thumbnail misrepresents the picture it stands for."""

        image = self.opened(render(self.png(size=(1200, 600))))
        self.assertAlmostEqual(2.0, image.width / image.height, places=1)

    def test_the_output_is_webp(self) -> None:
        self.assertEqual("WEBP", self.opened(render(self.png())).format)

    def test_a_small_image_is_not_enlarged(self) -> None:
        """`thumbnail()` only shrinks, and upscaling would make a 32-pixel icon
        into a blurry 640-pixel one for no gain."""

        image = self.opened(render(self.png(size=(32, 24))))
        self.assertEqual((32, 24), image.size)

    def test_transparency_is_flattened_rather_than_failing(self) -> None:
        """WEBP can hold alpha, but a P-mode PNG cannot be saved as one
        directly -- and an exception here would mean no thumbnail at all."""

        from PIL import Image

        path = self.pictures / "alpha.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 128)).save(path)
        self.assertIsNotNone(render(path))

    def test_orientation_is_applied(self) -> None:
        """A photograph carries its rotation in EXIF rather than in its pixels.
        Without `exif_transpose` the thumbnail is sideways while the full image
        is upright, and only the thumbnail looks wrong."""

        from PIL import Image

        path = self.pictures / "rotated.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 clockwise
        Image.new("RGB", (200, 100), (10, 20, 30)).save(path, exif=exif)

        image = self.opened(render(path))
        # Stored 200x100; orientation 6 means it should be shown as 100x200.
        self.assertGreater(image.height, image.width)

    def test_an_unreadable_file_renders_to_none(self) -> None:
        path = self.pictures / "broken.png"
        path.write_bytes(b"not a png")
        self.assertIsNone(render(path))

    def test_a_missing_file_renders_to_none(self) -> None:
        self.assertIsNone(render(self.pictures / "absent.png"))


class IdentityTests(_Images):
    def test_the_same_file_has_the_same_identity(self) -> None:
        path = self.png()
        self.assertEqual(identity(path), identity(path))

    def test_editing_the_file_changes_its_identity(self) -> None:
        """The whole invalidation story. The disk cache and the browser ETag
        use this one value, so they cannot disagree about staleness."""

        path = self.png()
        before = identity(path)
        time.sleep(0.01)
        self.png()  # rewrite, new mtime
        self.assertNotEqual(before, identity(path))

    def test_a_different_size_has_a_different_identity(self) -> None:
        path = self.png()
        self.assertNotEqual(identity(path, 640), identity(path, 200))

    def test_a_missing_file_still_has_an_identity(self) -> None:
        """It may have been deleted between the query and the request, and
        raising there would be a worse answer than a 404."""

        self.assertTrue(identity(self.pictures / "absent.png"))


class CacheTests(_Images):
    def test_a_thumbnail_is_kept_and_reused(self) -> None:
        path = self.png()
        first = self.cache.thumbnail_for(path)
        self.assertIsNotNone(first)
        self.assertIsNotNone(self.cache.get(path))
        second = self.cache.thumbnail_for(path)
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.etag, second.etag)

    def test_the_second_request_reads_the_stored_bytes(self) -> None:
        """Proven by making the stored bytes distinguishable from anything a
        renderer would produce, then asking again.

        Not by deleting the source: a missing file has a DIFFERENT identity by
        design -- the stat fails and the timestamp falls back -- so a deleted
        source misses the cache rather than proving it was used.
        """

        path = self.png()
        first = self.cache.thumbnail_for(path)
        entry = self.cache.entry(first.etag)
        self.assertTrue(entry.is_file())

        entry.write_bytes(b"these-exact-bytes-were-stored")
        again = self.cache.thumbnail_for(path)
        self.assertEqual(b"these-exact-bytes-were-stored", again.data)
        self.assertEqual(first.etag, again.etag)

    def test_editing_the_source_produces_a_new_thumbnail(self) -> None:
        path = self.png(size=(1200, 800))
        first = self.cache.thumbnail_for(path)
        time.sleep(0.01)
        self.png(size=(600, 600))
        second = self.cache.thumbnail_for(path)
        self.assertNotEqual(first.etag, second.etag)
        self.assertAlmostEqual(
            1.0,
            self.opened(second.data).width / self.opened(second.data).height,
            places=1,
        )

    def test_entries_are_spread_across_subdirectories(self) -> None:
        """One directory holding forty thousand files is slow to open on every
        filesystem that has an opinion about it."""

        self.cache.thumbnail_for(self.png())
        entries = list(self.cache.directory.rglob("*.webp"))
        self.assertEqual(1, len(entries))
        self.assertNotEqual(self.cache.directory, entries[0].parent)

    def test_an_unreadable_source_yields_nothing(self) -> None:
        path = self.pictures / "broken.png"
        path.write_bytes(b"not a png")
        self.assertIsNone(self.cache.thumbnail_for(path))

    def test_a_cache_that_cannot_write_still_answers(self) -> None:
        """Derived data. A read-only cache directory should make the Gallery
        slow, not blank."""

        blocked = ThumbnailCache(self.pictures / "a.png")  # a file, not a dir
        self.png()
        found = blocked.thumbnail_for(self.pictures / "a.png")
        self.assertIsNotNone(found)
        self.assertTrue(found.data)

    def test_sweeping_removes_only_what_is_old(self) -> None:
        path = self.png()
        self.cache.thumbnail_for(path)
        self.assertEqual(0, self.cache.sweep())
        self.assertEqual(1, self.cache.sweep(max_age=-1))
        self.assertIsNone(self.cache.get(path))


class VideoTests(_Images):
    def test_a_video_without_ffmpeg_gets_a_placeholder(self) -> None:
        """The file is real and belongs in the grid; the owner just cannot
        have a frame from it. A broken image would say the opposite."""

        path = self.pictures / "clip.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        found = self.cache.thumbnail_for(path)
        self.assertIsNotNone(found)
        if ffmpeg_path() is None:
            self.assertEqual(VIDEO_PLACEHOLDER, found.data)
            self.assertEqual("image/svg+xml", found.media_type)
            # Not cacheable: installing ffmpeg later must change what is shown.
            self.assertFalse(found.cacheable)

    def test_the_placeholder_needs_no_network(self) -> None:
        """It is drawn, not fetched. A placeholder that loads a remote asset
        fails in exactly the offline case it exists for."""

        text = VIDEO_PLACEHOLDER.decode("ascii")
        # The `xmlns` value is an XML namespace NAME, not an address anything
        # fetches -- so the assertion is about the constructs that would
        # actually load something, not about the string "http".
        for fetching in ("<image", "href", "url(", "<script", "@import",
                         "<use", "<foreignObject"):
            with self.subTest(fetching=fetching):
                self.assertNotIn(fetching, text)
        self.assertTrue(text.startswith("<svg"))


class BoundaryTests(unittest.TestCase):
    def test_thumbnails_import_nothing_from_the_engine(self) -> None:
        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_thumbnails.py").read_text(
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
        self.assertEqual(EXPECTED_GALLERY_THUMBNAIL_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
