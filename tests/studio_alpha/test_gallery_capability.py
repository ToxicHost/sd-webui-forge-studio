"""What the Gallery can do here, and what it says when it cannot.

Every test drives an injected `GalleryProbe`, which is the point of having one:
the answer this build gives on a machine with no Pillow has to be checkable on
a machine that has it.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_capability import (  # noqa: E402
    PILLOW_PACKAGE,
    WATCHDOG_PACKAGE,
    GalleryProbe,
    decide,
)

EXPECTED_GALLERY_CAPABILITY_TESTS = 19

#: A machine with everything.
COMPLETE = GalleryProbe(
    imaging=True, perceptual_hashing=True, filesystem_watching=True,
    storage_writable=True,
)


class WholeFeatureTests(unittest.TestCase):
    def test_a_complete_machine_runs_everything(self) -> None:
        capability = decide(COMPLETE)
        self.assertTrue(capability.available)
        self.assertIsNone(capability.reason)
        for name in ("browse", "duplicates", "auto_sync"):
            with self.subTest(feature=name):
                self.assertTrue(capability.feature(name).available)

    def test_without_pillow_the_gallery_is_unavailable_with_a_reason(
        self,
    ) -> None:
        """DISABLE WITH REASON, never an empty Gallery. An owner shown zero
        images concludes their pictures are gone."""

        capability = decide(
            GalleryProbe(imaging=False, perceptual_hashing=True,
                         filesystem_watching=True)
        )
        self.assertFalse(capability.available)
        self.assertIn("Pillow", capability.reason or "")

    def test_the_reason_names_what_to_install(self) -> None:
        capability = decide(GalleryProbe(imaging=False))
        self.assertEqual(PILLOW_PACKAGE, capability.feature("browse").requires)

    def test_unwritable_storage_disables_the_gallery(self) -> None:
        """An index that cannot be written is not a Gallery. Read-only state
        roots happen -- a revoked permission, a full disk, a mounted share."""

        capability = decide(
            GalleryProbe(imaging=True, storage_writable=False)
        )
        self.assertFalse(capability.available)
        self.assertIn("state folder", capability.reason or "")

    def test_a_storage_reason_is_carried_through_verbatim(self) -> None:
        """Whoever discovered the failure knows more about it than this module
        does, so its sentence wins over the generic one."""

        capability = decide(
            GalleryProbe(imaging=True, storage_writable=False,
                         storage_reason="The state folder is on a read-only "
                                        "volume.")
        )
        self.assertEqual("The state folder is on a read-only volume.",
                         capability.reason)

    def test_storage_is_checked_before_imaging(self) -> None:
        """Both broken means the owner should hear the one they can act on
        first, and a missing library is not why the disk is read-only."""

        capability = decide(
            GalleryProbe(imaging=False, storage_writable=False)
        )
        self.assertIn("state folder", capability.reason or "")


class OptionalFeatureTests(unittest.TestCase):
    def test_missing_perceptual_hashing_disables_only_duplicates(self) -> None:
        capability = decide(
            GalleryProbe(imaging=True, perceptual_hashing=False,
                         filesystem_watching=True)
        )
        self.assertTrue(capability.available)
        self.assertTrue(capability.feature("browse").available)
        self.assertTrue(capability.feature("auto_sync").available)
        self.assertFalse(capability.feature("duplicates").available)
        self.assertEqual(PILLOW_PACKAGE,
                         capability.feature("duplicates").requires)

    def test_missing_filesystem_watching_disables_only_auto_sync(self) -> None:
        capability = decide(
            GalleryProbe(imaging=True, perceptual_hashing=True,
                         filesystem_watching=False)
        )
        self.assertTrue(capability.available)
        self.assertFalse(capability.feature("auto_sync").available)
        self.assertEqual(WATCHDOG_PACKAGE,
                         capability.feature("auto_sync").requires)

    def test_an_optional_reason_says_what_still_works(self) -> None:
        """The difference between a disabled control and a broken product."""

        capability = decide(GalleryProbe(imaging=True))
        self.assertIn("rest of the Gallery works",
                      capability.feature("duplicates").reason or "")
        self.assertIn("Rescan by hand",
                      capability.feature("auto_sync").reason or "")

    def test_optional_features_inherit_the_reason_the_gallery_is_off(
        self,
    ) -> None:
        """Not their own reason. Blaming duplicate detection's own dependency
        on a machine with no Pillow sends the owner after the wrong thing."""

        capability = decide(
            GalleryProbe(imaging=False, perceptual_hashing=False)
        )
        self.assertIn("Pillow", capability.feature("duplicates").reason or "")

    def test_an_unknown_feature_is_unavailable_rather_than_an_error(
        self,
    ) -> None:
        state = decide(COMPLETE).feature("time_travel")
        self.assertFalse(state.available)
        self.assertIn("time_travel", state.reason or "")


class WireFormatTests(unittest.TestCase):
    def test_the_payload_carries_every_feature(self) -> None:
        payload = decide(COMPLETE).to_dict()
        self.assertTrue(payload["available"])
        self.assertEqual({"browse", "duplicates", "auto_sync"},
                         set(payload["features"]))

    def test_an_available_feature_carries_no_apology(self) -> None:
        payload = decide(COMPLETE).to_dict()
        self.assertEqual({"available": True}, payload["features"]["browse"])

    def test_an_unavailable_feature_carries_reason_and_requirement(
        self,
    ) -> None:
        payload = decide(GalleryProbe(imaging=True)).to_dict()
        duplicates = payload["features"]["duplicates"]
        self.assertFalse(duplicates["available"])
        self.assertIn("reason", duplicates)
        self.assertEqual(PILLOW_PACKAGE, duplicates["requires"])


class MeasurementTests(unittest.TestCase):
    def test_measuring_reads_the_real_environment(self) -> None:
        """Pillow is a hard dependency of this checkout, so this is a real
        assertion rather than a tautology."""

        self.assertTrue(GalleryProbe.measure().imaging)

    def test_measuring_imports_nothing(self) -> None:
        """`find_spec`, not `import`. Importing `watchdog` starts its threads
        inside a process that
        may never open the Gallery."""

        # PIL is the one that makes this a real assertion here: the other two
        # are not installed, so nothing could import them anyway, and a
        # `find_spec` swapped back to `import` would still pass on them.
        #
        # Everything removed is put back. The canonical suite runs in ONE
        # process, and leaving a half-unloaded PIL behind would break whichever
        # suite happened to run next rather than this one.
        removed = {
            name: module for name, module in sys.modules.items()
            if name.split(".")[0] in ("PIL", "watchdog", "imagehash")
        }
        for name in removed:
            del sys.modules[name]
        self.addCleanup(sys.modules.update, removed)

        before = set(sys.modules)
        GalleryProbe.measure()
        new = {module.split(".")[0] for module in set(sys.modules) - before}
        for name in ("PIL", "watchdog", "imagehash"):
            with self.subTest(name=name):
                self.assertNotIn(name, new)


class NoInstallTests(unittest.TestCase):
    def test_the_capability_never_installs_anything(self) -> None:
        """The Extension's Gallery pip-installs `watchdog` at import. Studio
        reports a missing dependency and leaves the decision to the owner:
        that line reaches the network during startup, mutates an environment
        the owner assembled, and turns a missing optional feature into a
        failed launch."""

        source = (APP_ROOT / "forge_studio" / "gallery_capability.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for banned in ("subprocess", "pip", "urllib", "socket", "requests"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, imported)

    def test_the_gallery_modules_import_nothing_from_the_engine(self) -> None:
        forbidden = ("torch", "modules", "modules_forge", "backend", "gradio",
                     "forge_headless")
        for name in ("gallery_capability", "gallery_index", "gallery_metadata",
                     "gallery_store"):
            tree = ast.parse(
                (APP_ROOT / "forge_studio" / f"{name}.py").read_text(
                    encoding="utf-8"
                )
            )
            for node in ast.walk(tree):
                found: list[str] = []
                if isinstance(node, ast.Import):
                    found = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found = [node.module]
                for module in found:
                    with self.subTest(name=name, module=module):
                        self.assertNotIn(module.split(".")[0], forbidden)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GALLERY_CAPABILITY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
