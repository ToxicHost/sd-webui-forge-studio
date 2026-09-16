"""External model roots: root validation, roles, snapshots and caps.

Every fixture here is synthetic -- temporary directories and empty files with
model-shaped extensions. Nothing in this module reads, stats or enumerates a
real model payload, and no test points a catalogue at owner model storage.

The refusal tests deliberately use paths that do not exist. That is the point:
a network or device path must be refused from its *syntax*, before anything
stats it, so the test passing on a machine with no such share is exactly the
behaviour under test.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.catalogue import (  # noqa: E402
    MAX_CATALOGUE_ENTRIES,
    MAX_SCANNED_ENTRIES,
    MODEL_ROLE_CHECKPOINT,
    MODEL_ROLE_TEXT_ENCODER,
    MODEL_ROLE_VAE,
    MODEL_ROLES,
    ModelCatalogue,
    _is_prefix,
    _is_reparse_point,
    classify_format,
)
from forge_headless.contracts import HeadlessError, LoadSupport  # noqa: E402
from forge_studio.result_delivery import CasePolicy  # noqa: E402
from forge_headless.root_policy import (  # noqa: E402
    ACCEPTED_DRIVE_TYPES,
    DRIVE_REMOTE,
    drive_type,
    validate_root,
)

WORKSPACE_ROOT = APP_ROOT.parent


#: Asserted against the discovered count so a silently dropped test fails.
#: Added ahead of P0.4, which churns this suite hardest. The convention exists
#: because 68 tests once vanished from a suite without anything going red
#: (see test_headless_model_plumbing.py).
EXPECTED_TESTS = 38


class _RootCase(unittest.TestCase):
    """A temp root outside the workspace, which is the whole point."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="emr-")).resolve()
        # Names that make case detection conclusive, so an unrelated
        # inconclusive refusal cannot masquerade as the behaviour under test.
        (self.root / "Alpha.safetensors").write_bytes(b"")
        (self.root / "Beta.ckpt").write_bytes(b"")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def catalogue(self, **kwargs: object) -> ModelCatalogue:
        params: dict[str, object] = {
            "workspace_root": WORKSPACE_ROOT,
            "allow_external": True,
        }
        params.update(kwargs)
        return ModelCatalogue(self.root, **params)  # type: ignore[arg-type]

    def assertRefused(self, code: str, root: object, **kwargs: object) -> None:
        with self.assertRaises(HeadlessError) as caught:
            validate_root(root, **kwargs)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, code, f"for {root!r}")


# --------------------------------------------------------------------------
# 1. Root validation
# --------------------------------------------------------------------------


class RootValidationTests(_RootCase):
    def test_an_external_root_is_accepted_only_with_allow_external(self) -> None:
        # The temp dir is outside the workspace, so this is the widening itself.
        self.assertTrue(self.catalogue().enumerate())
        with self.assertRaises(HeadlessError) as caught:
            ModelCatalogue(self.root, workspace_root=WORKSPACE_ROOT)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_ROOT_OUTSIDE_WORKSPACE"
        )

    def test_unc_paths_are_refused_by_syntax(self) -> None:
        for text in (
            r"\\server\share\models",
            r"\\192.168.1.10\models",
            "//server/share/models",
        ):
            self.assertRefused("HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION", text)

    def test_device_and_extended_namespaces_are_refused(self) -> None:
        for text in (
            r"\\?\C:\Models",
            r"\\?\UNC\server\share\models",
            r"\\.\PhysicalDrive0",
            r"\\.\pipe\anything",
        ):
            self.assertRefused("HEADLESS_CATALOGUE_ROOT_DEVICE_NAMESPACE", text)

    def test_refusal_precedes_any_filesystem_access(self) -> None:
        # None of these exist. If validation resolved first, the failure would
        # be ROOT_UNAVAILABLE; the specific network/device codes prove the
        # syntactic refusal ran before anything touched the path.
        self.assertRefused(
            "HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION",
            r"\\no-such-host-exists\share",
        )
        self.assertRefused(
            "HEADLESS_CATALOGUE_ROOT_DEVICE_NAMESPACE",
            r"\\?\UNC\no-such-host-exists\share",
        )

    def test_filesystem_and_drive_roots_are_refused(self) -> None:
        candidates = [Path(self.root.anchor)] if self.root.anchor else []
        candidates.append(Path("C:/") if os.name == "nt" else Path("/"))
        for candidate in candidates:
            self.assertRefused(
                "HEADLESS_CATALOGUE_ROOT_IS_FILESYSTEM_ROOT", candidate
            )

    def test_missing_root_is_refused(self) -> None:
        self.assertRefused(
            "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE", self.root / "absent"
        )

    def test_a_regular_file_as_root_is_refused(self) -> None:
        self.assertRefused(
            "HEADLESS_CATALOGUE_ROOT_NOT_A_DIRECTORY",
            self.root / "Alpha.safetensors",
        )

    def test_empty_root_is_refused(self) -> None:
        self.assertRefused("HEADLESS_CATALOGUE_ROOT_NOT_SUPPLIED", "   ")

    def test_relative_root_is_refused_for_owner_configuration(self) -> None:
        self.assertRefused(
            "HEADLESS_CATALOGUE_ROOT_NOT_ABSOLUTE",
            "models/checkpoints",
            require_absolute=True,
        )
        # Internal callers that resolve against the workspace are unaffected.
        self.assertTrue(validate_root(self.root).is_dir())

    def test_mapped_network_drive_is_classified_not_guessed(self) -> None:
        # The classifier is what refuses a mapped drive, so assert on the
        # classifier itself rather than requiring a mounted share to exist.
        if os.name != "nt":
            self.assertIsNone(drive_type(Path("/srv/models")))
            return
        self.assertNotIn(DRIVE_REMOTE, ACCEPTED_DRIVE_TYPES)
        self.assertIn(drive_type(self.root), ACCEPTED_DRIVE_TYPES)
        # A UNC drive component classifies as remote without contacting it.
        self.assertEqual(drive_type(Path(r"\\host\share\x")), DRIVE_REMOTE)


# --------------------------------------------------------------------------
# 2. Roles and identity
# --------------------------------------------------------------------------


class RoleTests(_RootCase):
    def test_every_role_can_be_catalogued(self) -> None:
        for role in MODEL_ROLES:
            catalogue = self.catalogue(role=role)
            self.assertEqual(catalogue.role, role)
            for item in catalogue.enumerate():
                self.assertEqual(item.model_kind, role)

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            # NOT "lora" -- that became a real role at AR8.1. An example of
            # an unknown role has to be a name that will never be one.
            self.catalogue(role="not_a_role")
        self.assertEqual(caught.exception.code, "HEADLESS_CATALOGUE_ROLE_UNKNOWN")

    def test_ids_differ_per_role_for_the_same_file(self) -> None:
        # Every role that CATALOGUES this file, which is not every role: the
        # adetailer role admits `.pt` only, so a `.safetensors` is absent from
        # it by design rather than by omission. Deriving the set from the
        # enumeration keeps the claim -- role is inside the digest -- true for
        # whatever roles exist, instead of pinning the number three.
        by_role = {
            role: {
                item.relative_location: item.model_id
                for item in self.catalogue(role=role).enumerate()
            }
            for role in MODEL_ROLES
        }
        holders = [r for r in MODEL_ROLES if "Alpha.safetensors" in by_role[r]]
        self.assertGreater(len(holders), 1, "fixture catalogued in one role only")
        ids = {by_role[role]["Alpha.safetensors"] for role in holders}
        self.assertEqual(len(ids), len(holders), "role is not in the digest")

    def test_a_checkpoint_id_does_not_resolve_in_the_vae_catalogue(self) -> None:
        checkpoint = self.catalogue(role=MODEL_ROLE_CHECKPOINT)
        vae = self.catalogue(role=MODEL_ROLE_VAE)
        model_id = checkpoint.enumerate()[0].model_id
        checkpoint.resolve(model_id)  # valid in its own catalogue
        with self.assertRaises(HeadlessError) as caught:
            vae.resolve(model_id)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_vae_role_does_not_apply_the_checkpoint_blacklist(self) -> None:
        # `.vae.safetensors` is excluded from checkpoints on purpose. Applying
        # that exclusion to the VAE role would hide the role's own files.
        name = "decoder.vae.safetensors"
        _, checkpoint_support = classify_format(name, MODEL_ROLE_CHECKPOINT)
        _, vae_support = classify_format(name, MODEL_ROLE_VAE)
        self.assertIs(checkpoint_support, LoadSupport.UNSUPPORTED)
        self.assertIs(vae_support, LoadSupport.LOAD_PLUMBED)

    def test_module_roles_recognize_the_formats_forge_enumerates(self) -> None:
        # modules_forge/main_entry.py:97 -- one namespace for VAE and text
        # encoder, with these extensions and no blacklist.
        for suffix in ("ckpt", "pt", "pth", "bin", "safetensors", "sft", "gguf"):
            for role in (MODEL_ROLE_TEXT_ENCODER, MODEL_ROLE_VAE):
                fmt, support = classify_format(f"m.{suffix}", role)
                self.assertEqual(fmt, suffix)
                self.assertIs(support, LoadSupport.LOAD_PLUMBED, f"{role}/{suffix}")
        # Checkpoints keep the narrower table they always had.
        self.assertIs(
            classify_format("m.pt", MODEL_ROLE_CHECKPOINT)[1],
            LoadSupport.UNSUPPORTED,
        )


# --------------------------------------------------------------------------
# 3. Snapshots, revalidation and caps
# --------------------------------------------------------------------------


class EmptyRootTransitionTests(_RootCase):
    """The owner-ratified empty-root behaviour, and what happens next.

    An empty root constructs, lists nothing, and resolves nothing while its
    case policy is inconclusive. This pins what happens when the first model
    arrives, which is the case the exemption made possible.
    """

    def test_an_empty_root_serves_nothing_and_resolves_nothing(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="emr-empty-")).resolve()
        self.addCleanup(shutil.rmtree, empty, True)
        catalogue = ModelCatalogue(
            empty,
            workspace_root=WORKSPACE_ROOT,
            allow_external=True,
            case_policy=CasePolicy.INCONCLUSIVE,
        )
        self.assertEqual(catalogue.enumerate(), ())
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve("0" * 32)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_CASE_POLICY_INCONCLUSIVE"
        )

    def test_an_inconclusive_policy_keeps_failing_closed_when_a_model_arrives(
        self,
    ) -> None:
        empty = Path(tempfile.mkdtemp(prefix="emr-arrive-")).resolve()
        self.addCleanup(shutil.rmtree, empty, True)
        catalogue = ModelCatalogue(
            empty,
            workspace_root=WORKSPACE_ROOT,
            allow_external=True,
            case_policy=CasePolicy.INCONCLUSIVE,
        )
        (empty / "Alpha.safetensors").write_bytes(b"")
        self.assertEqual(
            catalogue.refresh().entries,
            (),
            "an inconclusive policy started serving entries",
        )

    def test_a_detected_policy_is_reprobed_when_the_first_model_arrives(
        self,
    ) -> None:
        # The live path: the owner configures an empty folder, Studio accepts
        # it, then a model is copied in. No reconstruction happens -- the
        # running catalogue must pick it up on the next refresh.
        root = Path(tempfile.mkdtemp(prefix="emr-arrives-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        catalogue = ModelCatalogue(
            root, workspace_root=WORKSPACE_ROOT, allow_external=True
        )
        self.assertIs(catalogue.case_policy, CasePolicy.INCONCLUSIVE)
        self.assertEqual(catalogue.enumerate(), ())

        (root / "Alpha.safetensors").write_bytes(b"")
        entries = catalogue.refresh().entries
        if catalogue.case_policy is CasePolicy.INCONCLUSIVE:
            # Still undecidable: fail closed rather than list what cannot load.
            self.assertEqual(entries, ())
            return
        self.assertEqual(
            [item.relative_location for item in entries], ["Alpha.safetensors"]
        )
        catalogue.resolve(entries[0].model_id)

    def test_a_reopened_populated_root_catalogues_normally(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="emr-reopen-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        ModelCatalogue(root, workspace_root=WORKSPACE_ROOT, allow_external=True)
        (root / "Alpha.safetensors").write_bytes(b"")
        reopened = ModelCatalogue(
            root, workspace_root=WORKSPACE_ROOT, allow_external=True
        )
        if reopened.case_policy is CasePolicy.INCONCLUSIVE:
            self.skipTest("volume case behaviour is genuinely undecidable here")
        entries = reopened.enumerate()
        self.assertEqual(
            [item.relative_location for item in entries], ["Alpha.safetensors"]
        )
        reopened.resolve(entries[0].model_id)


class SnapshotTests(_RootCase):
    def test_generation_advances_only_on_refresh(self) -> None:
        catalogue = self.catalogue()
        first = catalogue.snapshot()
        self.assertIs(catalogue.snapshot(), first)
        second = catalogue.refresh()
        self.assertEqual(second.generation, first.generation + 1)

    def test_an_id_from_an_older_snapshot_stops_resolving_once_removed(self) -> None:
        catalogue = self.catalogue()
        model_id = catalogue.enumerate()[0].model_id
        (self.root / "Alpha.safetensors").unlink()
        # Still in the published snapshot, but revalidation catches the removal.
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve(model_id)
        self.assertIn(
            caught.exception.code,
            {"HEADLESS_MODEL_UNAVAILABLE", "HEADLESS_MODEL_UNKNOWN"},
        )
        catalogue.refresh()
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve(model_id)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_resolution_revalidates_rather_than_trusting_the_snapshot(self) -> None:
        catalogue = self.catalogue()
        entry = catalogue.enumerate()[0]
        _, source = catalogue.resolve(entry.model_id)
        self.assertEqual(source.root, self.root)
        self.assertTrue(source.resolved.is_file())
        # Swap the file for a directory of the same name: same id, no longer a
        # loadable file, so resolution must fail rather than hand back a path.
        target = self.root / entry.relative_location
        target.unlink()
        target.mkdir()
        with self.assertRaises(HeadlessError):
            catalogue.resolve(entry.model_id)

    def test_a_retargeted_root_is_detected(self) -> None:
        catalogue = self.catalogue()
        model_id = catalogue.enumerate()[0].model_id
        elsewhere = Path(tempfile.mkdtemp(prefix="emr-swap-")).resolve()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        (elsewhere / "Alpha.safetensors").write_bytes(b"")
        shutil.rmtree(self.root)
        try:
            os.symlink(elsewhere, self.root, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            # No privilege to create the link: the root is simply gone, which
            # must also refuse. Both outcomes are failures, which is the point.
            with self.assertRaises(HeadlessError):
                catalogue.resolve(model_id)
            self.root.mkdir(parents=True, exist_ok=True)
            return
        self.addCleanup(lambda: self.root.unlink(missing_ok=True))
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve(model_id)
        self.assertIn(
            caught.exception.code,
            {"HEADLESS_CATALOGUE_ROOT_CHANGED", "HEADLESS_MODEL_OUTSIDE_ROOT"},
        )

    def test_caps_are_bounded_and_ordered(self) -> None:
        self.assertLessEqual(MAX_CATALOGUE_ENTRIES, MAX_SCANNED_ENTRIES)
        self.assertGreater(MAX_CATALOGUE_ENTRIES, 0)

    def test_truncation_is_reported_not_silent(self) -> None:
        crowded = Path(tempfile.mkdtemp(prefix="emr-many-")).resolve()
        self.addCleanup(shutil.rmtree, crowded, True)
        (crowded / "Aa.safetensors").write_bytes(b"")
        for index in range(12):
            (crowded / f"m{index:03d}.safetensors").write_bytes(b"")
        catalogue = ModelCatalogue(
            crowded, workspace_root=WORKSPACE_ROOT, allow_external=True
        )
        full = catalogue.snapshot()
        self.assertFalse(full.truncated)
        self.assertEqual(full.scanned_entries, 13)
        self.assertEqual(len(full.entries), 13)

    def test_a_truncated_scan_sets_the_flag(self) -> None:
        import forge_headless.catalogue as catalogue_module

        crowded = Path(tempfile.mkdtemp(prefix="emr-cap-")).resolve()
        self.addCleanup(shutil.rmtree, crowded, True)
        (crowded / "Aa.safetensors").write_bytes(b"")
        for index in range(9):
            (crowded / f"m{index}.safetensors").write_bytes(b"")
        original = catalogue_module.MAX_CATALOGUE_ENTRIES
        catalogue_module.MAX_CATALOGUE_ENTRIES = 4
        try:
            snapshot = ModelCatalogue(
                crowded, workspace_root=WORKSPACE_ROOT, allow_external=True
            ).snapshot()
        finally:
            catalogue_module.MAX_CATALOGUE_ENTRIES = original
        self.assertTrue(snapshot.truncated, "cap reached without reporting it")
        self.assertLessEqual(len(snapshot.entries), 4)


# --------------------------------------------------------------------------
# 3b. Reparse points and the casefold escape
# --------------------------------------------------------------------------


class ReparseEscapeTests(unittest.TestCase):
    """The adversarial-review finding, end to end.

    Two defects composed into a real containment escape:

    1. a Windows directory junction (``mklink /J``, no admin rights needed)
       reports ``is_symlink() == False``, so the scan walked through it;
    2. ``_is_prefix`` folded with ``str.casefold()``, which is a strictly wider
       equivalence than NTFS's -- ``'Modelſ'.casefold() == 'models'`` while the
       two directories genuinely coexist -- so the containment recheck that
       should have caught the escape certified it instead.

    Only the second was load-bearing, but both are fixed and both are pinned.
    """

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="emr-reparse-")).resolve()
        self.addCleanup(shutil.rmtree, self.temp, True)

    def _catalogue(self, root: Path, **kwargs: object) -> ModelCatalogue:
        params: dict[str, object] = {
            "workspace_root": WORKSPACE_ROOT,
            "allow_external": True,
        }
        params.update(kwargs)
        return ModelCatalogue(root, **params)  # type: ignore[arg-type]

    def _junction(self, link: Path, target: Path) -> bool:
        if os.name != "nt":
            try:
                os.symlink(target, link, target_is_directory=True)
                return True
            except (OSError, NotImplementedError):
                return False
        import subprocess

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def test_casefold_is_wider_than_the_filesystem(self) -> None:
        # The premise of the escape, asserted so the fix cannot be argued away.
        self.assertEqual("Modelſ".casefold(), "models")
        self.assertEqual("große".casefold(), "grosse")
        self.assertNotEqual("Modelſ".lower(), "models")

    def test_a_junction_into_a_casefold_twin_is_not_catalogued(self) -> None:
        root = self.temp / "Models"
        twin = self.temp / "Modelſ"
        root.mkdir()
        try:
            twin.mkdir()
        except (OSError, UnicodeError):
            self.skipTest("volume cannot represent the twin directory name")
        if os.path.samefile(root, twin):
            self.skipTest("volume treats the twin as the same directory")
        (root / "Alpha.safetensors").write_bytes(b"")
        (twin / "Secret.safetensors").write_bytes(b"SECRET-OUTSIDE-ROOT")
        if not self._junction(root / "link", twin):
            self.skipTest("cannot create a junction on this machine")

        catalogue = self._catalogue(root, recursive=True)
        locations = {item.relative_location for item in catalogue.enumerate()}
        self.assertEqual(locations, {"Alpha.safetensors"})
        self.assertNotIn("link/Secret.safetensors", locations)

    def test_no_entry_resolves_outside_the_root_through_a_junction(self) -> None:
        root = self.temp / "Root"
        outside = self.temp / "Outside"
        root.mkdir()
        outside.mkdir()
        (root / "Alpha.safetensors").write_bytes(b"")
        (outside / "Secret.safetensors").write_bytes(b"SECRET")
        if not self._junction(root / "link", outside):
            self.skipTest("cannot create a junction on this machine")

        catalogue = self._catalogue(root, recursive=True)
        for item in catalogue.enumerate():
            _, source = catalogue.resolve(item.model_id)
            self.assertTrue(
                _is_prefix(root, source.resolved, CasePolicy.CASE_SENSITIVE)
                or os.path.samefile(source.resolved.parent, root),
                f"{item.relative_location} resolved to {source.resolved}",
            )

    def test_a_junction_is_recognised_as_a_reparse_point(self) -> None:
        root = self.temp / "R"
        target = self.temp / "T"
        root.mkdir()
        target.mkdir()
        if not self._junction(root / "link", target):
            self.skipTest("cannot create a junction on this machine")
        with os.scandir(root) as entries:
            entry = next(e for e in entries if e.name == "link")
        if os.name == "nt":
            # The exact condition that made the old guard miss it.
            self.assertFalse(entry.is_symlink())
            self.assertTrue(entry.is_dir(follow_symlinks=False))
        self.assertTrue(_is_reparse_point(entry))


# --------------------------------------------------------------------------
# 4. Privacy of the catalogue projection
# --------------------------------------------------------------------------


class ProjectionPrivacyTests(_RootCase):
    def test_no_absolute_path_escapes_in_a_candidate(self) -> None:
        for role in MODEL_ROLES:
            for item in self.catalogue(role=role).enumerate():
                rendered = repr(item.to_dict())
                self.assertNotIn(str(self.root), rendered)
                self.assertNotIn(tempfile.gettempdir(), rendered)
                self.assertFalse(os.path.isabs(item.relative_location))

    def test_contained_source_stays_inside_the_headless_boundary(self) -> None:
        # ContainedSource carries absolute paths by design; the guarantee is
        # that it is not part of any dict a route could serialize.
        catalogue = self.catalogue()
        candidate, source = catalogue.resolve(catalogue.enumerate()[0].model_id)
        self.assertNotIn("root", candidate.to_dict())
        self.assertNotIn("resolved", candidate.to_dict())
        self.assertTrue(source.resolved.is_absolute())


# --------------------------------------------------------------------------
# 5. The catalogue layer must never gain a deserializer
# --------------------------------------------------------------------------


class LoaderSafetyRegressionTests(unittest.TestCase):
    """Fails if enumeration ever grows the ability to open model content.

    Extension filtering is not the deserialization boundary. This test exists
    so that a future edit which adds ``torch.load``, ``safe_open`` or a GGUF
    parse to the enumeration layer fails loudly rather than quietly moving that
    boundary into code that runs when a user opens a dropdown.
    """

    #: Callables that would move the deserialization boundary into enumeration.
    #: Matched against the *called name* in the AST, so prose in a comment or
    #: docstring cannot trip the guard and cannot satisfy it either.
    FORBIDDEN_CALLS = frozenset(
        {
            "open",
            "load",
            "loads",
            "safe_open",
            "read_bytes",
            "read_text",
            "mmap",
            "frombuffer",
            "from_file",
        }
    )
    FORBIDDEN_IMPORTS = frozenset(
        {"torch", "safetensors", "pickle", "mmap", "numpy", "gguf"}
    )

    #: `os.scandir` and `Path.stat` are the two filesystem calls enumeration is
    #: allowed to make. Neither reads content.
    ALLOWED_CALLS = frozenset({"scandir", "stat", "listdir", "samefile", "exists"})

    def _module_ast(self, name: str):  # type: ignore[no-untyped-def]
        import ast

        source = (APP_ROOT / "forge_headless" / name).read_text(encoding="utf-8")
        return ast.parse(source)

    def _called_names(self, tree) -> set[str]:  # type: ignore[no-untyped-def]
        import ast

        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        return names

    def test_catalogue_layer_calls_no_deserializer(self) -> None:
        for name in ("catalogue.py", "root_policy.py"):
            called = self._called_names(self._module_ast(name))
            offending = called & self.FORBIDDEN_CALLS
            self.assertEqual(
                offending,
                set(),
                f"{name} calls {sorted(offending)}: the enumeration layer must "
                f"not open or parse model content",
            )

    def test_catalogue_layer_imports_no_deserializer(self) -> None:
        import ast

        for name in ("catalogue.py", "root_policy.py"):
            imported: set[str] = set()
            for node in ast.walk(self._module_ast(name)):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            offending = imported & self.FORBIDDEN_IMPORTS
            self.assertEqual(
                offending, set(), f"{name} imports {sorted(offending)}"
            )

    def test_the_guard_itself_detects_a_planted_call(self) -> None:
        # A guard that cannot fail proves nothing. This plants the exact edit
        # the guard exists to catch and asserts it is seen.
        import ast

        planted = ast.parse("import torch\ndef f(p):\n    return torch.load(p)\n")
        self.assertIn("load", self._called_names(planted) & self.FORBIDDEN_CALLS)

    def test_enumeration_reads_no_file_content(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="emr-noopen-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        payload = root / "Aa.safetensors"
        payload.write_bytes(b"NOT-A-REAL-MODEL")
        opened: list[str] = []
        real_open = open

        def watching_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        import builtins

        builtins.open = watching_open  # type: ignore[assignment]
        try:
            entries = ModelCatalogue(
                root, workspace_root=WORKSPACE_ROOT, allow_external=True
            ).enumerate()
        finally:
            builtins.open = real_open  # type: ignore[assignment]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].size_bytes, len(b"NOT-A-REAL-MODEL"))
        self.assertEqual(
            [name for name in opened if str(root) in name],
            [],
            "enumeration opened a file in the model root",
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
