"""Telling two pictures apart, and two copies of one picture together.

The load-bearing test here is bit-identity with `imagehash.phash(hash_size=16)`.
Studio computes its own hash so that duplicate detection needs no numeric
library at all, but an owner's EXISTING hashes were written by the other
implementation -- so "the same idea" is not good enough; it has to be the same
bits.

`imagehash` is not installed, so the comparison is made against a numpy
transcription of the same algorithm, which is what TrackImage 3.94 uses and
what it in turn verified against `imagehash`. numpy is available to tests; it
is banned inside `forge_studio`, which is the whole reason the shipped version
is pure Python.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_index import imaging_available  # noqa: E402
from forge_studio.gallery_similarity import (  # noqa: E402
    DEFAULT_THRESHOLD,
    MAX_DISTANCE,
    distance,
    fingerprint,
    group,
    near,
)

EXPECTED_GALLERY_SIMILARITY_TESTS = 24


def numpy_reference(image, hash_size: int = 16, factor: int = 4) -> str:
    """TrackImage 3.94's phash, transcribed. The oracle for bit-identity."""

    import numpy as np
    from PIL import Image, ImageOps

    size = hash_size * factor
    prepared = ImageOps.exif_transpose(image).convert("L").resize(
        (size, size), Image.LANCZOS)
    pixels = np.asarray(prepared, dtype=np.float64)
    k = np.arange(size).reshape(-1, 1)
    i = np.arange(size).reshape(1, -1)
    matrix = 2.0 * np.cos(np.pi * k * (2 * i + 1) / (2 * size))
    low = (matrix @ pixels @ matrix.T)[:hash_size, :hash_size]
    median = np.median(low)
    value = 0
    for bit in (low > median).flatten():
        value = (value << 1) | int(bit)
    return "%0*x" % (hash_size * hash_size // 4, value)


class _Pictures(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")

    def picture(self, width: int = 160, height: int = 120, seed: int = 0,
                bright: int = 0):
        from PIL import Image

        image = Image.new("RGB", (width, height))
        image.putdata([
            (min(255, (x * 7 + y * 3 + seed * 29) % 256 + bright),
             min(255, (y * 5 + seed * 11) % 256 + bright),
             min(255, (x + y) % 256 + bright))
            for y in range(height) for x in range(width)
        ])
        return image


class BitIdentityTests(_Pictures):
    def setUp(self) -> None:
        super().setUp()
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy is not installed, so the oracle is missing")

    def test_the_hash_matches_the_reference_implementation(self) -> None:
        """An owner's existing hashes were written by the other one. Matching
        the idea is not enough; it has to be the same bits or every stored
        hash silently stops matching anything."""

        for width, height, seed in ((160, 120, 0), (64, 64, 1), (301, 200, 2),
                                    (512, 384, 3)):
            with self.subTest(size=(width, height)):
                image = self.picture(width, height, seed)
                self.assertEqual(numpy_reference(image),
                                 fingerprint(image))

    def test_a_non_square_image_matches_too(self) -> None:
        """The resize is to a square, so a wide image is squashed. Both
        implementations have to squash it the same way."""

        image = self.picture(400, 100, 5)
        self.assertEqual(numpy_reference(image), fingerprint(image))


class HashTests(_Pictures):
    def test_the_hash_is_256_bits_of_hex(self) -> None:
        found = fingerprint(self.picture())
        self.assertEqual(64, len(found))
        int(found, 16)  # must parse

    def test_the_same_picture_hashes_the_same(self) -> None:
        self.assertEqual(fingerprint(self.picture(seed=1)),
                         fingerprint(self.picture(seed=1)))

    def test_different_pictures_hash_differently(self) -> None:
        first = fingerprint(self.picture(seed=1))
        second = fingerprint(self.picture(seed=9))
        self.assertNotEqual(first, second)
        self.assertGreater(distance(first, second), DEFAULT_THRESHOLD)

    def test_a_rescaled_copy_is_still_the_same_picture(self) -> None:
        """The whole point of a perceptual hash. A thumbnail and its original
        are one picture, and an exact hash would call them different."""

        original = self.picture(320, 240, seed=3)
        smaller = original.resize((160, 120))
        self.assertLessEqual(
            distance(fingerprint(original), fingerprint(smaller)),
            DEFAULT_THRESHOLD,
        )

    def test_an_unreadable_file_fingerprints_to_none(self) -> None:
        import tempfile

        broken = Path(tempfile.mkdtemp()) / "broken.png"
        broken.write_bytes(b"not a png")
        self.assertIsNone(fingerprint(broken))

    def test_orientation_is_applied_before_hashing(self) -> None:
        """A photograph rotated by EXIF is the same picture as itself. Without
        `exif_transpose` two copies tagged differently never match."""

        import tempfile

        from PIL import Image

        directory = Path(tempfile.mkdtemp())
        upright = self.picture(200, 100, seed=4)
        plain = directory / "plain.png"
        upright.save(plain)

        # Orientation 6 means "rotate 90 clockwise to display", so what is
        # STORED must be the upright picture turned 90 counter-clockwise --
        # PIL's ROTATE_90. Storing the clockwise turn instead leaves the two
        # 180 degrees apart, which is how this test failed first time round.
        rotated = upright.transpose(Image.ROTATE_90)
        exif = Image.Exif()
        exif[0x0112] = 6
        tagged = directory / "tagged.jpg"
        rotated.save(tagged, exif=exif, quality=95)

        self.assertLessEqual(
            distance(fingerprint(plain), fingerprint(tagged)),
            MAX_DISTANCE // 4,
        )


class DistanceTests(unittest.TestCase):
    def test_identical_hashes_are_zero_apart(self) -> None:
        self.assertEqual(0, distance("ff00", "ff00"))

    def test_one_differing_bit_is_one_apart(self) -> None:
        self.assertEqual(1, distance("00", "01"))

    def test_a_missing_hash_is_maximally_far(self) -> None:
        """Never zero. An unhashed image must not read as a duplicate of every
        other unhashed image."""

        self.assertEqual(MAX_DISTANCE, distance("", "ff"))
        self.assertEqual(MAX_DISTANCE, distance("ff", ""))

    def test_a_hash_that_is_not_hex_is_maximally_far(self) -> None:
        """The worker writes a single space for a file it could not read."""

        self.assertEqual(MAX_DISTANCE, distance(" ", "ff"))


class NoTileSignatureTests(unittest.TestCase):
    """The region score is absent on purpose, and this records why.

    TrackImage added an 8x8 tile signature in v3.73 to separate "the same image
    with a watermark" from "a different image with a similar composition", and
    its code comment argues the case well. Its AUTHOR removed it in v3.93:

        They promised something the method could not deliver -- a watermark
        scored around 95% and never showed up, while genuinely different
        images scored low.

    3.94 still computes and stores one, and nothing reads it. A first pass
    here copied that faithfully, on the strength of the v3.73 comment, without
    reading the v3.93 entry that retracted it. These assertions exist so a
    future session meeting the same persuasive comment finds the retraction
    with it.
    """

    def test_no_tile_signature_is_computed_or_exported(self) -> None:
        import forge_studio.gallery_similarity as similarity

        for name in ("TILE_GRID", "TILE_CELLS", "tile_difference",
                     "_tile_signature", "Fingerprint"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(similarity, name))

    def test_the_schema_carries_no_tile_column(self) -> None:
        """Storing 64 bytes a row for something nothing reads is the exact
        vestigial state 3.94 is in. Studio does not need to inherit it."""

        from forge_studio.gallery_store import _ADDED_COLUMNS, _SCHEMA

        self.assertNotIn("tile_sig", " ".join(_SCHEMA))
        self.assertNotIn("tile_sig", str(_ADDED_COLUMNS))

    def test_the_retraction_is_recorded_where_it_would_be_re_added(self) -> None:
        """In the module itself, not only in a commit message. A commit message
        is not where somebody about to add a feature looks."""

        source = (APP_ROOT / "forge_studio" / "gallery_similarity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("v3.93", source)
        self.assertIn("could not deliver", source)


class NeighbourTests(unittest.TestCase):
    def test_only_candidates_within_the_threshold_are_returned(self) -> None:
        found = near("00", [(1, "00"), (2, "01"), (3, "ff")], threshold=1)
        self.assertEqual([(1, 0), (2, 1)], found)

    def test_the_closest_comes_first(self) -> None:
        found = near("00", [(1, "0f"), (2, "01")], threshold=8)
        self.assertEqual([2, 1], [image_id for image_id, _ in found])

    def test_a_candidate_with_no_hash_is_skipped(self) -> None:
        self.assertEqual([], near("00", [(1, "")], threshold=MAX_DISTANCE))


class GroupingTests(unittest.TestCase):
    def test_pairs_collapse_into_one_group(self) -> None:
        """Similarity is not transitive at a fixed threshold, so A~B and B~C
        must still be ONE group of three rather than two pairs -- an owner
        clearing duplicates wants one group per picture."""

        self.assertEqual([[1, 2, 3]], group([(1, 2), (2, 3)]))

    def test_separate_pairs_stay_separate(self) -> None:
        self.assertEqual([[1, 2], [3, 4]],
                         sorted(group([(1, 2), (3, 4)])))

    def test_nothing_groups_to_nothing(self) -> None:
        self.assertEqual([], group([]))


class BoundaryTests(unittest.TestCase):
    def test_similarity_imports_no_numeric_library(self) -> None:
        """The reason this module is written the way it is. `forge_studio` may
        not import numpy -- it sits beside torch and gradio in the owned-package
        purity rule -- so the DCT is pure Python and the Hamming distance is
        `int.bit_count()`."""

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "gallery_similarity.py").read_text(
                encoding="utf-8"
            )
        )
        forbidden = ("numpy", "scipy", "imagehash", "pywt", "torch",
                     "modules", "gradio")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                with self.subTest(name=name):
                    self.assertNotIn(name.split(".")[0], forbidden)

    def test_hashing_one_image_is_fast_enough_to_be_worth_it(self) -> None:
        """The pure-Python DCT is single-digit milliseconds because only the
        top-left 16x16 is ever read -- against 50-200ms to decode the file.
        A version that transformed the whole 64x64 would be sixteen times
        slower for no extra information."""

        if not imaging_available():
            self.skipTest("Pillow is not installed")
        from time import perf_counter

        from PIL import Image

        image = Image.new("RGB", (64, 64))
        started = perf_counter()
        fingerprint(image)
        self.assertLess(perf_counter() - started, 1.0)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_SIMILARITY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
