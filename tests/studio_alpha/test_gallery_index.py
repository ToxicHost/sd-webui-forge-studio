"""Reading files off disk: the walk, and one image's facts.

Written against real files rather than mocks. Every claim this module makes is
a claim about Pillow, `os.walk` and `os.stat` behaving a particular way, and a
mock would only assert that the port repeats itself.
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_index import (  # noqa: E402
    MEDIA_SUFFIXES,
    IndexedFile,
    content_hash,
    describe,
    file_date,
    imaging_available,
    iter_media,
    read_metadata,
    walk_follow,
)

EXPECTED_GALLERY_INDEX_TESTS = 24

PARAMETERS = (
    "a knight on a hill\n"
    "Negative prompt: blurry\n"
    "Template: a __character__ on a __place__\n"
    "Steps: 30, Sampler: Euler, Schedule type: Beta 57, Seed: 7, "
    "Size: 8x6, Model: Anitox"
)


def _pillow():
    from PIL import Image, PngImagePlugin

    return Image, PngImagePlugin


class _Files(unittest.TestCase):
    """A real directory with real images in it."""

    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def image(self, size: tuple[int, int] = (8, 6)):
        Image, _ = _pillow()
        # A gradient, not a flat colour: a uniform block survives JPEG
        # compression byte for byte, which would let a broken pixel hash pass.
        image = Image.new("RGB", size)
        image.putdata([
            ((x * 7) % 256, (y * 11) % 256, (x + y) % 256)
            for y in range(size[1]) for x in range(size[0])
        ])
        return image

    def link_directory(self, link: Path, target: Path) -> str:
        """Point `link` at `target`, by whatever means this account has.

        Returns "symlink" or "junction". Creating a directory SYMLINK on
        Windows needs SeCreateSymbolicLinkPrivilege -- admin, or Developer
        Mode -- so an ordinary account cannot, and a test that needs one has to
        say it was skipped rather than quietly pass. A JUNCTION needs no
        privilege, and although Python does not classify it as a link (so it
        proves nothing about `followlinks`) `os.path.realpath` does resolve it,
        which is exactly what the cycle guard is built on.
        """

        try:
            os.symlink(target, link, target_is_directory=True)
            return "symlink"
        except (OSError, NotImplementedError, AttributeError):
            pass
        if sys.platform == "win32":
            import subprocess
            finished = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, text=True,
            )
            if finished.returncode == 0:
                return "junction"
        self.skipTest("this account cannot link directories")
        raise AssertionError("unreachable")  # pragma: no cover

    def png(self, name: str, parameters: str | None = PARAMETERS) -> Path:
        _, PngImagePlugin = _pillow()
        info = None
        if parameters is not None:
            info = PngImagePlugin.PngInfo()
            info.add_text("parameters", parameters)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image().save(path, pnginfo=info)
        return path

    def jpeg(self, name: str, comment: str = PARAMETERS) -> Path:
        Image, _ = _pillow()
        exif = Image.Exif()
        # UserComment lives in the Exif sub-IFD (0x8769), which is exactly
        # where Forge puts the parameters block for formats that have no PNG
        # text chunk to write it into.
        exif.get_ifd(0x8769)[0x9286] = comment
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image().save(path, exif=exif)
        return path


class WalkTests(_Files):
    def test_a_plain_tree_is_walked_whole(self) -> None:
        self.png("top.png")
        self.png("nested/deep/inner.png")
        found = {path.name for path in iter_media(self.root)}
        self.assertEqual({"top.png", "inner.png"}, found)

    def test_only_media_suffixes_are_yielded(self) -> None:
        self.png("keep.png")
        (self.root / "notes.txt").write_text("hello", encoding="utf-8")
        (self.root / "index.db").write_bytes(b"\x00")
        self.assertEqual(["keep.png"],
                         [p.name for p in iter_media(self.root)])

    def test_the_order_is_stable(self) -> None:
        """A scan that reports a different sequence each run cannot be resumed
        or compared against a previous one."""

        for name in ("c.png", "a.png", "b.png"):
            self.png(name)
        first = [p.name for p in iter_media(self.root)]
        self.assertEqual(["a.png", "b.png", "c.png"], first)
        self.assertEqual(first, [p.name for p in iter_media(self.root)])

    def test_suffix_matching_ignores_case(self) -> None:
        self.png("SHOUT.PNG")
        self.assertEqual(1, len(list(iter_media(self.root))))

    def test_a_symlinked_directory_is_descended(self) -> None:
        """The whole reason this is not bare `os.walk`. Owners keep output on
        another drive and link it in; `followlinks=False` skips it silently."""

        target = self.root / "elsewhere"
        target.mkdir()
        self.png("elsewhere/linked.png")
        library = self.root / "library"
        library.mkdir()
        if self.link_directory(library / "link", target) != "symlink":
            self.skipTest("only a true symlink exercises followlinks")
        self.assertEqual(["linked.png"],
                         [p.name for p in iter_media(library)])

    def test_a_link_cycle_terminates(self) -> None:
        """Without the realpath guard this does not fail a test, it hangs one.

        A junction serves here where a symlink is unavailable: bare `os.walk`
        descends junctions too, so a junction pointing back at an ancestor
        loops forever just the same.
        """

        inner = self.root / "inner"
        inner.mkdir()
        self.png("inner/one.png")
        self.link_directory(inner / "back", self.root)
        visited = [dirpath for dirpath, _, _ in walk_follow(self.root)]
        self.assertEqual(len(visited), len(set(map(os.path.realpath, visited))))

    def test_one_directory_reached_two_ways_is_listed_once(self) -> None:
        """The other half of the guard, and the one an owner notices: two links
        to the same folder must not double every picture in it."""

        target = self.root / "shared"
        target.mkdir()
        self.png("shared/once.png")
        for name in ("first", "second"):
            self.link_directory(self.root / name, target)
        names = [path.name for path in iter_media(self.root)]
        self.assertEqual(1, names.count("once.png"))

    def test_the_walk_yields_os_walks_own_dirnames_list(self) -> None:
        """Pruning `dirnames` in the loop must still steer the traversal, or
        every caller that skips a subtree quietly stops skipping it."""

        self.png("skipme/hidden.png")
        self.png("keep/shown.png")
        seen: list[str] = []
        for dirpath, dirnames, filenames in walk_follow(self.root):
            if "skipme" in dirnames:
                dirnames.remove("skipme")
            seen.extend(filenames)
        self.assertIn("shown.png", seen)
        self.assertNotIn("hidden.png", seen)


class MetadataTests(_Files):
    def test_a_png_parameters_chunk_is_read_and_parsed(self) -> None:
        metadata = read_metadata(self.png("a.png"))
        self.assertEqual("a knight on a hill", metadata["prompt"])
        self.assertEqual("blurry", metadata["negative_prompt"])
        self.assertEqual("a __character__ on a __place__", metadata["template"])
        self.assertEqual("Beta 57", metadata["scheduler"])

    def test_the_raw_block_is_kept_verbatim(self) -> None:
        """`gallery.js` sends `raw_parameters` back to the Canvas to rebuild a
        generation, so it must be the text Forge wrote, not a re-rendering."""

        metadata = read_metadata(self.png("a.png"))
        self.assertEqual(PARAMETERS, metadata["raw_parameters"])

    def test_a_jpeg_carries_its_parameters_in_the_exif_sub_ifd(self) -> None:
        """JPEG and WebP have no text chunk. Reading only IFD0 finds nothing,
        and every non-PNG in the Gallery looks metadata-free."""

        metadata = read_metadata(self.jpeg("b.jpg"))
        self.assertEqual("a knight on a hill", metadata["prompt"])
        self.assertEqual("30", metadata["steps"])

    def test_a_png_without_parameters_is_simply_bare(self) -> None:
        metadata = read_metadata(self.png("bare.png", parameters=None))
        self.assertNotIn("prompt", metadata)
        self.assertNotIn("error", metadata)
        self.assertGreater(metadata["file_size"], 0)

    def test_a_broken_file_is_recorded_not_raised(self) -> None:
        """One truncated file in a folder of forty thousand must not end the
        scan, and must not be indistinguishable from an image with no data."""

        broken = self.root / "broken.png"
        broken.write_bytes(b"this is not a png")
        metadata = read_metadata(broken)
        self.assertIn("error", metadata)
        self.assertFalse(describe(broken).readable)

    def test_a_missing_file_is_recorded_not_raised(self) -> None:
        metadata = read_metadata(self.root / "absent.png")
        self.assertIn("error", metadata)


class ContentHashTests(_Files):
    def test_stripping_metadata_does_not_change_the_hash(self) -> None:
        """The point of hashing pixels. It is how a generated image is reunited
        with its parameters after the owner has stripped or renamed it."""

        with_text = self.png("with.png")
        without = self.png("without.png", parameters=None)
        self.assertNotEqual(with_text.read_bytes(), without.read_bytes())
        self.assertEqual(content_hash(with_text), content_hash(without))

    def test_different_pixels_hash_differently(self) -> None:
        """The negative control. A hash that ignores metadata AND ignores the
        picture would pass the test above."""

        Image, _ = _pillow()
        other = self.root / "other.png"
        Image.new("RGB", (8, 6), (255, 0, 0)).save(other)
        self.assertNotEqual(content_hash(self.png("a.png")),
                            content_hash(other))

    def test_an_unreadable_file_hashes_to_empty(self) -> None:
        broken = self.root / "broken.png"
        broken.write_bytes(b"nope")
        self.assertEqual("", content_hash(broken))


class DescribeTests(_Files):
    def test_the_facts_the_index_stores_are_all_present(self) -> None:
        found = describe(self.png("a.png"))
        self.assertIsInstance(found, IndexedFile)
        self.assertEqual((8, 6), (found.width, found.height))
        self.assertGreater(found.size, 0)
        self.assertGreater(found.date, 0)
        self.assertEqual(64, len(found.content_hash))
        self.assertTrue(found.readable)

    def test_the_search_text_holds_the_prompt_but_not_the_negative(self) -> None:
        found = describe(self.png("a.png"))
        self.assertIn("knight", found.search_text)
        self.assertNotIn("blurry", found.search_text)

    def test_hashing_can_be_deferred(self) -> None:
        """Decoding every image to hash its pixels is the expensive half of a
        scan, so the first pass leaves it to a background worker."""

        found = describe(self.png("a.png"), with_hash=False)
        self.assertEqual("", found.content_hash)
        self.assertEqual(8, found.width)

    def test_the_date_is_the_earliest_the_filesystem_admits(self) -> None:
        """`st_ctime` counts only where it means creation. Under POSIX it is
        inode-change time, which a chmod moves and which can postdate mtime."""

        path = self.png("a.png")
        stat = os.stat(path)
        candidates = [stat.st_mtime, getattr(stat, "st_birthtime", None)]
        if os.name == "nt":
            candidates.append(stat.st_ctime)
        self.assertEqual(min(c for c in candidates if c), file_date(path))


class BoundaryTests(unittest.TestCase):
    def test_the_indexer_imports_nothing_from_the_engine(self) -> None:
        """Walking a directory needs no Torch, no Neo and no Gradio."""

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_index.py").read_text(
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

    def test_video_suffixes_are_indexed_too(self) -> None:
        """A gallery of generated output holds video now."""

        for suffix in (".mp4", ".webm", ".png"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, MEDIA_SUFFIXES)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_INDEX_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
