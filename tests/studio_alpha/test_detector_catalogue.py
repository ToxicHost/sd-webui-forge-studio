"""Which detectors may be offered, and where the root that holds them lives.

`detector_catalogue.py` landed in P0.8(2) with no tests. This is that suite,
plus the role that gives it a configurable root.

THREE LISTS, NOT TWO
====================

```text
forge_headless/catalogue.py         MODEL_ROLES           roles with a ROOT
forge_studio/model_selection.py     RESIDENT_MODEL_ROLES  roles a job LOADS
forge_studio/model_root_settings.py RESIDENT_MODEL_ROLES  the same three
```

Both of the latter were called `SELECTION_ROLES` and both held exactly the
three strings `MODEL_ROLES` held, so nothing distinguished a role that has a
directory from a role that is part of the resident session. They were renamed
BEFORE `adetailer` was added rather than after -- the moment the names would
otherwise have started lying, since a detector has a root and is never
resident.

WHY THE CATALOGUE EXISTS AT ALL
===============================

Upstream's `get_models` welds three policies together: scan a directory, reach
HuggingFace, and inject four MediaPipe entries whether or not MediaPipe is
installed. Studio refuses two of those, so it owns the rule instead, and the
rule is small enough to state: local only, names only, bundled bytes
hash-checked, an owner's own file admitted but marked.

SCOPE: MINIMAL_RUNTIME_SCOPE. Real files in a temporary directory; no
detector is ever loaded, and nothing here imports a runtime.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.catalogue import (  # noqa: E402
    DETECTOR_FORMAT_SUPPORT,
    MODEL_ROLE_ADETAILER,
    MODEL_ROLES,
    ROLE_BLACKLISTED_SUFFIXES,
    ROLE_FORMAT_SUPPORT,
)
from forge_studio.detector_catalogue import (  # noqa: E402
    BUNDLED_DETECTORS,
    Detector,
    describe_catalogue,
    is_known_detector,
    scan_configured_detectors,
    scan_detectors,
)
from forge_studio.model_root_settings import (  # noqa: E402
    RESIDENT_MODEL_ROLES as SETTINGS_ROLES,
)
from forge_studio.model_selection import (  # noqa: E402
    RESIDENT_MODEL_ROLES as SELECTION_ROLES_,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_CATALOGUE_TESTS = 42


class _Roots(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name: str, payload: bytes, *, under: str = "") -> Path:
        target = self.root / under / name if under else self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    def bundle(self, name: str, payload: bytes) -> dict[str, str]:
        """A `bundled` table whose recorded hash matches `payload`."""

        return {name: hashlib.sha256(payload).hexdigest()}


class ScanTests(_Roots):
    def test_a_bundled_detector_with_matching_bytes_is_verified(self) -> None:
        self.write("face_yolov8n.pt", b"weights")
        found = scan_detectors(self.root, bundled=self.bundle("face_yolov8n.pt", b"weights"))
        self.assertEqual(1, len(found))
        self.assertTrue(found[0].verified)
        self.assertFalse(found[0].owner_supplied)

    def test_a_bundled_name_with_wrong_bytes_is_refused(self) -> None:
        """Not offered-and-then-failed. A name Studio ships carrying bytes
        Studio did not is corruption or substitution, and it is the one case
        where silence would be dangerous rather than merely unhelpful."""

        self.write("face_yolov8n.pt", b"tampered")
        found = scan_detectors(
            self.root, bundled=self.bundle("face_yolov8n.pt", b"original")
        )
        self.assertEqual((), found)

    def test_an_owner_supplied_detector_is_offered_and_marked(self) -> None:
        """Refusing a detector because Studio did not ship it would be a
        different product."""

        self.write("my_own.pt", b"whatever")
        found = scan_detectors(self.root, bundled={})
        self.assertEqual(1, len(found))
        self.assertTrue(found[0].owner_supplied)
        self.assertFalse(found[0].verified)

    def test_a_missing_root_yields_nothing(self) -> None:
        """An ordinary state before the owner points at a folder, reported by
        an empty catalogue rather than an exception."""

        self.assertEqual((), scan_detectors(self.root / "absent"))
        self.assertEqual((), scan_detectors(None))

    def test_a_file_that_is_not_a_root_yields_nothing(self) -> None:
        target = self.write("notadir.pt", b"x")
        self.assertEqual((), scan_detectors(target))

    def test_non_pt_files_are_ignored(self) -> None:
        self.write("model.safetensors", b"x")
        self.write("notes.txt", b"x")
        self.assertEqual((), scan_detectors(self.root, bundled={}))

    def test_the_scan_recurses(self) -> None:
        """Matching upstream's rglob, so a directory organised into
        subfolders works unchanged."""

        self.write("deep.pt", b"x", under="a/b")
        self.assertEqual(1, len(scan_detectors(self.root, bundled={})))

    def test_a_duplicate_name_resolves_to_one_entry(self) -> None:
        """Two files of the same name are one ambiguous choice, and picking
        by directory order would make the answer depend on something the
        owner cannot see."""

        self.write("dup.pt", b"one", under="a")
        self.write("dup.pt", b"two", under="b")
        found = scan_detectors(self.root, bundled={})
        self.assertEqual(1, len(found))

    def test_the_order_is_stable(self) -> None:
        for name in ("c.pt", "a.pt", "b.pt"):
            self.write(name, b"x")
        names = [d.name for d in scan_detectors(self.root, bundled={})]
        self.assertEqual(sorted(names), names)

    def test_a_detector_carries_its_size(self) -> None:
        self.write("sized.pt", b"0123456789")
        self.assertEqual(10, scan_detectors(self.root, bundled={})[0].size)


class NoPathEscapesTests(_Roots):
    """Names only, exactly as no model path reaches a response."""

    def test_a_detector_has_no_path_attribute(self) -> None:
        self.assertNotIn("path", Detector.__dataclass_fields__)

    def test_the_projection_carries_no_path(self) -> None:
        self.write("face.pt", b"x", under="secret_folder")
        described = describe_catalogue(scan_detectors(self.root, bundled={}))
        rendered = repr(described)
        self.assertNotIn("secret_folder", rendered)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("\\\\", rendered)

    def test_the_projection_counts_what_it_offers(self) -> None:
        self.write("a.pt", b"x")
        self.write("b.pt", b"y")
        described = describe_catalogue(scan_detectors(self.root, bundled={}))
        self.assertEqual(2, described["count"])
        self.assertEqual(0, described["verified_count"])


class MembershipTests(unittest.TestCase):
    """`is_known_detector`, the guard whose three predecessors shipped with
    no callers. It is wired in the same commit as the field it guards."""

    def detectors(self) -> tuple[Detector, ...]:
        return (
            Detector(name="face_yolov8n.pt", size=1, verified=True, owner_supplied=False),
        )

    def test_an_offered_name_is_known(self) -> None:
        self.assertTrue(is_known_detector("face_yolov8n.pt", self.detectors()))

    def test_an_unoffered_name_is_not(self) -> None:
        self.assertFalse(is_known_detector("nope.pt", self.detectors()))

    def test_a_refused_detector_is_not_known(self) -> None:
        """The hash-mismatch case must not merely be absent from the UI: it
        must fail the membership check the request is validated against."""

        self.assertFalse(is_known_detector("face_yolov8n.pt", ()))

    def test_whitespace_is_trimmed_not_significant(self) -> None:
        self.assertTrue(is_known_detector("  face_yolov8n.pt  ", self.detectors()))

    def test_an_empty_name_is_not_known(self) -> None:
        self.assertFalse(is_known_detector("", self.detectors()))


class RoleTableTests(unittest.TestCase):
    """The three lists, and why only one of them gained a role."""

    def test_the_detector_role_has_a_root(self) -> None:
        self.assertIn(MODEL_ROLE_ADETAILER, MODEL_ROLES)

    def test_the_detector_role_is_not_part_of_the_resident_model(self) -> None:
        """It is loaded per Auto Detail slot and released. Adding it to the
        resident set would make a generation refuse to start without a
        detector chosen."""

        self.assertNotIn(MODEL_ROLE_ADETAILER, SELECTION_ROLES_)
        self.assertNotIn(MODEL_ROLE_ADETAILER, SETTINGS_ROLES)

    def test_the_two_resident_lists_still_agree(self) -> None:
        self.assertEqual(tuple(SELECTION_ROLES_), tuple(SETTINGS_ROLES))

    def test_the_lists_are_no_longer_the_same_shape(self) -> None:
        """The rename's whole purpose. While both were three-long and
        identically spelled, nothing marked them as different concepts."""

        self.assertNotEqual(len(MODEL_ROLES), len(SELECTION_ROLES_))

    def test_the_old_name_is_gone_from_both_files(self) -> None:
        for module in ("model_selection.py", "model_root_settings.py"):
            source = (APP_ROOT / "forge_studio" / module).read_text(encoding="utf-8")
            code = "\n".join(
                line for line in source.splitlines()
                if not line.lstrip().startswith("#")
            )
            with self.subTest(module=module):
                self.assertNotIn("SELECTION_ROLES", code)

    def test_a_detector_root_admits_only_pt(self) -> None:
        """`MODULE_FORMAT_SUPPORT` would also admit .safetensors, .ckpt, .bin
        and .gguf. Those are real model formats and none is a detector, so
        offering one would be a control presenting a choice that cannot work."""

        self.assertEqual({".pt"}, set(DETECTOR_FORMAT_SUPPORT))
        self.assertIs(
            DETECTOR_FORMAT_SUPPORT, ROLE_FORMAT_SUPPORT[MODEL_ROLE_ADETAILER]
        )

    def test_every_role_has_both_tables(self) -> None:
        """A role in MODEL_ROLES with no format table is a role that
        enumerates nothing while looking configured."""

        for role in MODEL_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, ROLE_FORMAT_SUPPORT)
                self.assertIn(role, ROLE_BLACKLISTED_SUFFIXES)


class ConfiguredRootBridgeTests(_Roots):
    """One enumerator over the directory, not two."""

    class _Registry:
        def __init__(self, roots: dict[str, tuple[str, ...]]) -> None:
            self._roots = roots

        def configured_roots(self) -> dict[str, tuple[str, ...]]:
            return dict(self._roots)

    def test_it_reads_the_configured_root(self) -> None:
        self.write("mine.pt", b"x")
        registry = self._Registry({"adetailer": (str(self.root),)})
        self.assertEqual(1, len(scan_configured_detectors(registry)))

    def test_an_unconfigured_role_yields_nothing(self) -> None:
        registry = self._Registry({"checkpoint": (str(self.root),)})
        self.assertEqual((), scan_configured_detectors(registry))

    def test_a_registry_that_refuses_yields_nothing(self) -> None:
        """A missing catalogue is an empty one. A detector list is never
        worth failing a page over."""

        class _Broken:
            def configured_roots(self):
                raise RuntimeError("no catalogue")

        self.assertEqual((), scan_configured_detectors(_Broken()))

    def test_the_first_root_wins_a_duplicate_name(self) -> None:
        """The same rule the single-root scan applies, for the same reason:
        resolving by configuration order would make the answer depend on
        something invisible."""

        other = self.root / "second"
        other.mkdir()
        self.write("dup.pt", b"first")
        (other / "dup.pt").write_bytes(b"second")
        registry = self._Registry({"adetailer": (str(self.root), str(other))})
        self.assertEqual(1, len(scan_configured_detectors(registry)))

    def test_the_bundled_names_are_the_recorded_ones(self) -> None:
        """`BUNDLED_MODEL_ASSETS.md` is a distribution obligation, and this
        dict is its machine-readable half."""

        self.assertEqual(5, len(BUNDLED_DETECTORS))
        for name, digest in BUNDLED_DETECTORS.items():
            with self.subTest(name=name):
                self.assertTrue(name.endswith(".pt"))
                self.assertEqual(64, len(digest))


class RouteTests(unittest.TestCase):
    SOURCE = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
        encoding="utf-8"
    )

    def test_the_catalogue_is_reachable(self) -> None:
        self.assertIn('"/api/detectors"', self.SOURCE)

    def test_the_route_takes_the_registry_from_the_server(self) -> None:
        """It lives on the server via `model_root_settings`, because a host
        may serve Studio without configuring model roots at all."""

        self.assertIn(
            'self._presentation.detectors(\n'
            '                        getattr(self._settings, "registry", None)',
            self.SOURCE,
        )


class BrowseRowTests(unittest.TestCase):
    """The owner needs a way to point at the folder.

    A backend root with no control is the mirror image of the defect this
    phase keeps finding: instead of a control that reaches nothing, a
    capability nothing can reach.
    """

    HTML = (
        APP_ROOT / "forge_studio" / "frontend" / "index.html"
    ).read_text(encoding="utf-8")
    PICKER = (
        APP_ROOT / "forge_studio" / "frontend" / "studio-dir-picker.js"
    ).read_text(encoding="utf-8")

    def test_the_row_exists_with_the_same_controls_as_the_others(self) -> None:
        for element in (
            "modelRootAdetailer",
            "modelRootStatusAdetailer",
            "modelRootListAdetailer",
            "modelRootAddAdetailer",
            "modelRootBrowseAdetailer",
        ):
            with self.subTest(element=element):
                self.assertIn(f'id="{element}"', self.HTML)

    def test_the_picker_knows_the_role(self) -> None:
        """One list drives Browse, Add and the row rendering, so the role has
        to be in it rather than special-cased anywhere."""

        self.assertIn('"adetailer"', self.PICKER)
        self.assertIn('adetailer: "Adetailer"', self.PICKER)

    def test_the_picker_roles_match_the_server_roles(self) -> None:
        """The two lists drifting is how a root becomes configurable on one
        side and invisible on the other."""

        import re

        match = re.search(r"const ROLES = \[(.*?)\];", self.PICKER, re.S)
        self.assertIsNotNone(match)
        roles = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        self.assertEqual(set(MODEL_ROLES), roles)

    def test_the_picker_suffixes_cover_every_role(self) -> None:
        """The second list. `ROLE_SUFFIX` maps a role to its element-id suffix,
        so a role missing here has no input, no Add button and no Browse
        button -- and nothing says so."""

        import re

        match = re.search(r"const ROLE_SUFFIX = " + chr(92) + r"{(.*?)" + chr(92) + r"};",
                          self.PICKER, re.S)
        self.assertIsNotNone(match)
        mapped = set(re.findall(r"(" + chr(92) + r"w+):", match.group(1)))
        self.assertEqual(set(MODEL_ROLES), mapped)

    def test_the_picker_state_is_derived_not_listed(self) -> None:
        """The third list, and the one that actually broke.

        `state.roots` was a fourth literal spelling of the role set. When
        `lora` was added to ROLES and ROLE_SUFFIX it was missed here, so
        `addRoot("lora", path)` reached `state.roots["lora"].indexOf(...)` on
        undefined, threw, and the owner's typed folder silently did not save.
        Found by the owner using it, not by this suite.

        Asserted on DERIVATION rather than on membership: a fourth literal that
        happens to be correct today is the same defect waiting.
        """

        self.assertIn("ROLES.map((role) => [role, []])", self.PICKER)
        self.assertNotIn("roots: { checkpoint:", self.PICKER)

    def test_saving_roots_re_reads_the_detector_catalogue(self) -> None:
        """REFRESH, the third of the six obligations. Without it the owner
        points at a folder and the Auto Detail list keeps showing what was
        there before."""

        block = self.PICKER[self.PICKER.index("async function save()"):]
        block = block[: block.index("window.StudioDirPicker")]
        self.assertIn("await refreshDetectors();", block)

    def test_the_summary_reports_an_empty_folder_honestly(self) -> None:
        """A configured folder holding nothing usable says so, rather than
        leaving the previous count on screen."""

        self.assertIn("No usable detectors in that folder.", self.PICKER)

    def test_no_detector_path_reaches_the_page(self) -> None:
        block = self.PICKER[self.PICKER.index("async function refreshDetectors"):]
        block = block[: block.index("window.StudioDirPicker")]
        self.assertIn("d.name", block)
        for leak in ("d.path", "location", "absolute"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, block)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_CATALOGUE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
