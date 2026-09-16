"""NG-1: Studio's own result root is a Gallery scan folder without being asked.

Nothing registered it. A fresh tester generated an image and met an empty
Gallery with no hint that a folder needed linking, while the first-run panel
told them "The default output folder is already watched." Measured on the
owner's own install before this existed: `/scan-folders` and `/folders` both
answered `[]`.

The Extension answers this from its save pipeline -- `studio_gallery.py`
`register_scan_folder`, `INSERT OR IGNORE`, documented never to raise into the
save path. Studio adopts from the store instead, because `store()` is the one
funnel both the Gallery tab and `note_generation()` already pass through, but
the contract is the Extension's: idempotent, and never able to cost a picture.

No server, no port, no client, matching `test_gallery_service.py`.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio import gallery_service as gallery_service_module  # noqa: E402
from forge_studio.gallery_index import imaging_available  # noqa: E402
from forge_studio.gallery_service import (  # noqa: E402
    ROUTE_PREFIX,
    GalleryService,
)

EXPECTED_NG1_TESTS = 9


class _Bootstrap(unittest.TestCase):
    def setUp(self) -> None:
        if not imaging_available():
            self.skipTest("Pillow is not installed")
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.state = self.base / "state"
        self.results = self.base / "Studio-Results"
        self.results.mkdir()

    def service(self, *, result_root: Path | None = "default") -> GalleryService:
        root = self.results if result_root == "default" else result_root
        made = GalleryService(self.state, result_root=root)
        self.addCleanup(made.close)
        return made

    def folders(self, service: GalleryService) -> list[dict]:
        return service.get(f"{ROUTE_PREFIX}/scan-folders").payload

    def png(self, folder: Path, name: str) -> Path:
        from PIL import Image

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        Image.new("RGB", (16, 12), (7, 90, 160)).save(path)
        return path


class AdoptionTests(_Bootstrap):
    def test_the_result_root_is_adopted_on_first_store_open(self) -> None:
        service = self.service()
        rows = self.folders(service)
        self.assertEqual(1, len(rows))
        self.assertEqual(self.results.name, rows[0]["label"])

    def test_a_second_service_on_the_same_state_does_not_duplicate(self) -> None:
        """A restart is exactly this: the same state root, opened again.

        `scan_folders.path` is NOT NULL UNIQUE and `add_folder` is
        INSERT OR IGNORE, so this is belt and braces -- which is the point,
        because a duplicated root would scan the owner's output twice on every
        launch and the schema is the only thing currently stopping it.
        """

        first = self.service()
        self.folders(first)
        first.close()

        second = self.service()
        rows = self.folders(second)
        self.assertEqual(1, len(rows))

    def test_a_folder_the_owner_linked_by_hand_survives(self) -> None:
        first = self.service()
        theirs = self.base / "their-pictures"
        theirs.mkdir()
        first.post(f"{ROUTE_PREFIX}/scan-folders", {"path": str(theirs)})
        first.close()

        second = self.service()
        labels = sorted(row["label"] for row in self.folders(second))
        self.assertEqual([self.results.name, "their-pictures"], labels)

    def test_a_host_with_no_result_root_adopts_nothing(self) -> None:
        """Every host that is not the launcher. Absent must mean absent."""

        service = self.service(result_root=None)
        self.assertEqual([], self.folders(service))


class UnavailableResultRootTests(_Bootstrap):
    def test_a_missing_result_root_warns_once_and_the_gallery_still_opens(
        self,
    ) -> None:
        missing = self.base / "not-here"
        service = self.service(result_root=missing)

        with self.assertLogs("studio.gallery", level=logging.WARNING) as caught:
            store = service.store()
        self.assertIsNotNone(store)
        self.assertEqual(1, len(caught.records))
        message = caught.records[0].getMessage()
        self.assertIn("Gallery", message)
        # Useful means it names what to check, not that something went wrong.
        self.assertIn("result_root", message)

        # Opening again says nothing further: the store is cached, and the
        # warned flag holds the line even if that ever changes.
        with self.assertRaises(AssertionError):
            with self.assertLogs("studio.gallery", level=logging.WARNING):
                service.store()

        self.assertEqual([], self.folders(service))

    def test_adoption_can_never_stop_the_gallery_opening(self) -> None:
        """The Extension's rule: this must not cost the owner a picture.

        `store()` is on the path that delivers a finished generation, so an
        exception here would turn a made image into a failed request.
        """

        class Exploding:
            def __init__(self, _store) -> None:
                pass

            def add_folder(self, *_a, **_k):
                raise RuntimeError("the disk went away")

        original = gallery_service_module.GalleryScanner
        gallery_service_module.GalleryScanner = Exploding
        self.addCleanup(
            setattr, gallery_service_module, "GalleryScanner", original
        )

        service = GalleryService(self.state, result_root=self.results)
        self.addCleanup(service.close)
        self.assertIsNotNone(service.store())


class BrowserProjectionTests(_Bootstrap):
    def test_the_browser_is_told_the_name_and_never_the_location(self) -> None:
        """NG-1 requires that no absolute path reaches the browser.

        Adopting Studio's own root would otherwise have put the install
        directory on the wire for the first time. Nothing wanted the path:
        `gallery.js` assigns this payload and takes `.length` from it, and
        reads no other field.
        """

        service = self.service()
        rows = self.folders(service)
        self.assertEqual(1, len(rows))
        self.assertEqual({"id", "label"}, set(rows[0]))

        serialized = repr(rows)
        self.assertNotIn(str(self.results), serialized)
        self.assertNotIn(str(self.base), serialized)
        self.assertNotIn(os.sep, serialized)

    def test_an_image_written_to_the_result_root_needs_no_registration(
        self,
    ) -> None:
        """The whole point, end to end.

        Studio writes into its own result root and the owner never links a
        folder; the picture must still be findable.
        """

        service = self.service()
        self.png(self.results, "made.png")

        outcome = service.post(f"{ROUTE_PREFIX}/scan").payload
        self.assertTrue(outcome["ok"])
        self.assertEqual(1, outcome["new"])

        images = service.get(f"{ROUTE_PREFIX}/images").payload
        self.assertEqual(1, images["total"])
        self.assertEqual("made.png", images["images"][0]["filename"])
        self.assertEqual(self.results.name, images["images"][0]["folder"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self) -> None:
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_NG1_TESTS, found)


if __name__ == "__main__":
    unittest.main()
