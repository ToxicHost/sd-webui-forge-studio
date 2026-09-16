"""Headless Forge Phase 2A — blocker removal, catalogue, and load plumbing.

Six groups: Gradio blocker removal, catalogue root policy, catalogue contract,
load plumbing, Studio integration, and the subprocess probe.

Proofs are AST-based, import-hook based, or use a patched open guard and a
recording loader port. Nothing here parses a fixture as a checkpoint, imports
Torch, or touches a device.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Sibling helper, not an installed module: put this directory on the path so the
# module imports under both the canonical runner and `unittest discover -t .`.
TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

WORKSPACE_ROOT = APP_ROOT.parent
FIXTURE_PARENT = WORKSPACE_ROOT / "Evidence" / "studio-headless-model-plumbing"
FORBIDDEN = ("gradio", "gradio_client")

from gradio_import_blocker import blocked_gradio_imports  # noqa: E402

from forge_headless.catalogue import (  # noqa: E402
    FORMAT_SUPPORT,
    ModelCatalogue,
    _is_prefix,
    classify_format,
    detect_case_policy_readonly,
    name_is_acceptable,
    sanitize_display_name,
)
from forge_headless.contracts import (  # noqa: E402
    PHASE2A_REACHABLE_STATES,
    HeadlessError,
    LoadSupport,
    ModelAvailability,
    RuntimeState,
)
from forge_headless.facade import ForgeHeadlessRuntime  # noqa: E402
from forge_headless.import_graph import (  # noqa: E402
    module_level_imports,
    paths_to_forbidden,
)
from forge_headless.loader_port import LoadRequest, PolicyGatedLoader  # noqa: E402
from forge_headless.studio_adapter import (  # noqa: E402
    HeadlessBackendAdapter,
    HeadlessBackendError,
)
from forge_studio.backend_selection import configured_model_root  # noqa: E402
from forge_studio.result_delivery import CasePolicy  # noqa: E402


#: Exact number of tests this module must contribute. Phase 1 shipped a module
#: whose sibling import failed under one runner, so unittest reported a single
#: loader error and 68 tests silently vanished. This count makes that
#: impossible: a loader failure changes the number and the assertion fires.
EXPECTED_PHASE2A_TESTS = 76


def _module_imports(module: str) -> set[str]:
    """Module-level imports, excluding `__future__` (a compiler directive)."""
    path = APP_ROOT / (module.replace(".", "/") + ".py")
    found = module_level_imports(ast.parse(path.read_text(encoding="utf-8")))
    return {name for name in found if not name.startswith("__future__")}


@contextmanager
def _restored_sys_modules():
    """Undo any module this block imports, so no residue escapes the test."""
    snapshot = dict(sys.modules)
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n not in snapshot]:
            del sys.modules[name]
        sys.modules.update(snapshot)


@contextmanager
def _as_symlink(name: str):
    """Make one directory entry report itself as a symlink, for one scan."""
    real_scandir = os.scandir

    class _Entry:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def __getattr__(self, attribute: str) -> object:
            return getattr(self._inner, attribute)

        def is_symlink(self) -> bool:
            return self._inner.name == name or self._inner.is_symlink()  # type: ignore[attr-defined]

    class _Scan(list):
        """Iterable AND a context manager, because the real one is both.

        The double used to be a bare list. That was enough while the scanner
        wrote `sorted(os.scandir(...))`, which leaked the iterator's OS handle;
        once the scanner started closing it with `with`, the double stopped
        matching the contract it was standing in for. A test double that
        supports less than the real object turns a correctness fix into a red
        test.
        """

        def __enter__(self):
            return iter(self)

        def __exit__(self, *_exception) -> None:
            return None

    def scandir(path=".", *args, **kwargs):
        return _Scan(_Entry(entry) for entry in real_scandir(path, *args, **kwargs))

    os.scandir = scandir  # type: ignore[assignment]
    try:
        yield
    finally:
        os.scandir = real_scandir  # type: ignore[assignment]


class _FixtureCase(unittest.TestCase):
    """Creates a contained synthetic catalogue and removes it afterwards."""

    files: tuple[str, ...] = (
        "alpha.safetensors",
        "alpha.ckpt",
        "beta.gguf",
        "gamma.sft",
        "notes.txt",
        "excluded.vae.safetensors",
    )

    def setUp(self) -> None:
        FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fx-"))
        for name in self.files:
            (self.root / name).write_bytes(b"synthetic-not-a-checkpoint")
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "deep.safetensors").write_bytes(b"synthetic")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def catalogue(self, **kwargs: object) -> ModelCatalogue:
        return ModelCatalogue(
            self.root,
            workspace_root=WORKSPACE_ROOT,
            **kwargs,  # type: ignore[arg-type]
        )

    def runtime(self, loader: object | None = None) -> ForgeHeadlessRuntime:
        runtime = ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT,
            workspace_root=WORKSPACE_ROOT,
            loader=loader,
        )
        runtime.probe_readiness()
        self.addCleanup(runtime.shutdown)
        return runtime


class _RecordingLoader:
    """Observes what reaches the port. Never opens a file, never imports Torch."""

    def __init__(self, *, refuse: bool = True) -> None:
        self.requests: list[LoadRequest] = []
        self._refuse = refuse

    def load(self, request: LoadRequest) -> None:
        self.requests.append(request)
        if self._refuse:
            PolicyGatedLoader().load(request)


# --------------------------------------------------------------------------
# 1. Gradio blocker removal
# --------------------------------------------------------------------------


class BlockerRemovalTests(unittest.TestCase):
    def test_shared_has_no_module_level_path_to_gradio(self) -> None:
        chains = paths_to_forbidden("modules.shared", APP_ROOT, FORBIDDEN)
        self.assertEqual(
            chains,
            [],
            f"modules.shared still reaches Gradio: {chains[:3]}",
        )

    def test_script_callbacks_core_has_no_path_to_gradio(self) -> None:
        chains = paths_to_forbidden(
            "modules.script_callbacks", APP_ROOT, FORBIDDEN
        )
        self.assertEqual(chains, [], f"still reaches Gradio: {chains[:3]}")

    def test_relocated_rounding_helper_is_pure(self) -> None:
        imports = _module_imports("modules.resolution")
        self.assertEqual(imports, {"math"})
        self.assertEqual(
            paths_to_forbidden("modules.resolution", APP_ROOT, FORBIDDEN), []
        )

    def test_infotext_core_is_pure(self) -> None:
        imports = _module_imports("modules.infotext_core")
        self.assertEqual(imports, {"json"})
        self.assertEqual(
            paths_to_forbidden("modules.infotext_core", APP_ROOT, FORBIDDEN), []
        )

    def test_processing_no_longer_imports_ui_or_infotext_utils(self) -> None:
        imports = _module_imports("modules.processing")
        self.assertNotIn("modules.ui", imports)
        self.assertNotIn("modules.ui.sRound", imports)
        self.assertNotIn("modules.infotext_utils", imports)
        self.assertIn("modules.resolution.sRound", imports)
        self.assertIn("modules.infotext_core", imports)

    def test_shared_does_not_import_gradio_themes_at_module_scope(self) -> None:
        imports = _module_imports("modules.shared")
        self.assertNotIn("modules.shared_gradio_themes", imports)
        self.assertNotIn("gradio", imports)

    def test_no_import_time_theme_construction(self) -> None:
        source = (APP_ROOT / "modules" / "shared.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            ast.unparse(node)
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(isinstance(sub, ast.Call) for sub in ast.walk(node))
        ]
        self.assertFalse(
            [text for text in calls if "themes" in text],
            f"theme constructed at import time: {calls}",
        )

    def test_legacy_compatibility_reexports_survive(self) -> None:
        ui_source = (APP_ROOT / "modules" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("sRound = resolution.sRound", ui_source)
        self.assertIn("_STEP = resolution.resolution_step()", ui_source)
        infotext = (APP_ROOT / "modules" / "infotext_utils.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("quote = infotext_core.quote", infotext)
        self.assertIn("unquote = infotext_core.unquote", infotext)

    def test_rounding_behaviour_is_unchanged(self) -> None:
        import math

        # Import `modules.resolution` for real, but restore `sys.modules`
        # exactly: a pre-existing guarantee is that importing Studio pulls in no
        # `modules`, and residue from this test would silently break it.
        with _restored_sys_modules():
            from modules import resolution

            for step in (8, 16, 64):
                for value in (0, 1, 7.5, 63, 64, 65, 1023.4, 2048):
                    expected = math.floor(value / step + 0.5) * step
                    self.assertEqual(
                        _round_with_step(resolution, value, step), expected
                    )

    def test_no_new_circular_module_scope_import(self) -> None:
        # `modules.extensions` and `modules.scripts` used to import each other at
        # module scope; the cycle is broken on the extensions side.
        self.assertNotIn("modules.scripts", _module_imports("modules.extensions"))
        self.assertIn("modules.extensions", _module_imports("modules.scripts"))

    def test_options_and_shared_items_defer_ui_imports(self) -> None:
        options = _module_imports("modules.options")
        self.assertNotIn("gradio", options)
        self.assertNotIn("modules.ui_components", options)
        shared_items = _module_imports("modules.shared_items")
        for name in ("modules.script_callbacks", "modules.scripts", "modules.ui_components"):
            self.assertNotIn(name, shared_items)

    def test_no_inference_module_still_reaches_gradio(self) -> None:
        """R1.5 emptied `GRADIO_CONTAMINATED_INFERENCE_MODULES`.

        This test used to assert the opposite -- that `modules.processing` was
        named in that tuple AND that the claim was true, because it really did
        still reach Gradio. Naming the contamination honestly was the right
        thing to do while it existed. All five named edges are now cut, so the
        assertion inverts: the tuple is empty, and the modules it named no
        longer have a path to Gradio.

        Both halves matter. An empty tuple with a still-contaminated module
        would be the tuple lying; a clean module absent from
        `GRADIO_CLEARED_MODULES` would be the clearance going unrecorded.
        """

        from forge_headless.facade import (
            GRADIO_CLEARED_MODULES,
            GRADIO_CONTAMINATED_INFERENCE_MODULES,
        )

        self.assertEqual((), GRADIO_CONTAMINATED_INFERENCE_MODULES)
        for module in ("modules.processing", "modules.sd_samplers",
                       "modules.sd_models", "modules.scripts"):
            with self.subTest(module=module):
                self.assertIn(module, GRADIO_CLEARED_MODULES)
                self.assertEqual(
                    [], paths_to_forbidden(module, APP_ROOT, FORBIDDEN)
                )

    def test_shared_device_boundary_is_removed(self) -> None:
        # Phase 2A named `modules/shared.py:7` -> backend.memory_management as
        # the one line keeping `modules.shared` un-importable headlessly.
        # Phase 2B removed it by making `xformers_available` lazy, so the
        # boundary is now gone rather than merely located.
        imports = _module_imports("modules.shared")
        self.assertNotIn("backend.memory_management", imports)
        self.assertEqual(
            paths_to_forbidden("modules.shared", APP_ROOT, ("torch",)), []
        )
        source = (APP_ROOT / "modules" / "shared.py").read_text(encoding="utf-8")
        self.assertIn("def __getattr__", source)
        self.assertIn("xformers_available", source)


def _round_with_step(resolution_module: object, value: float, step: int) -> int:
    """Call sRound with a forced step, without mutating global option state."""
    saved = resolution_module._step  # type: ignore[attr-defined]
    resolution_module._step = step  # type: ignore[attr-defined]
    try:
        return resolution_module.sRound(value)  # type: ignore[attr-defined]
    finally:
        resolution_module._step = saved  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# 2. Catalogue root policy
# --------------------------------------------------------------------------


class CatalogueRootPolicyTests(_FixtureCase):
    def test_explicit_contained_root_is_accepted(self) -> None:
        catalogue = self.catalogue()
        self.assertTrue(catalogue.root_configured)
        self.assertTrue(catalogue.enumerate())

    def test_root_outside_workspace_is_rejected(self) -> None:
        outside = Path(tempfile.gettempdir()).resolve()
        with self.assertRaises(HeadlessError) as caught:
            ModelCatalogue(outside, workspace_root=WORKSPACE_ROOT)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_ROOT_OUTSIDE_WORKSPACE"
        )

    def test_missing_root_is_rejected(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            ModelCatalogue(self.root / "absent", workspace_root=WORKSPACE_ROOT)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE"
        )

    def test_file_as_root_is_rejected(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            ModelCatalogue(
                self.root / "alpha.safetensors", workspace_root=WORKSPACE_ROOT
            )
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_ROOT_NOT_A_DIRECTORY"
        )

    def test_no_recursion_by_default(self) -> None:
        flat = {item.relative_location for item in self.catalogue().enumerate()}
        self.assertNotIn("nested/deep.safetensors", flat)
        deep = {
            item.relative_location
            for item in self.catalogue(recursive=True).enumerate()
        }
        self.assertIn("nested/deep.safetensors", deep)

    def test_inconclusive_case_policy_refuses_a_populated_root(self) -> None:
        # Behaviour change, mandated by the external-model-roots handoff s9:
        # "on an inconclusive probe: do not guess, do not enumerate, do not
        # resolve, return stable catalogue error".
        #
        # This test previously asserted the opposite -- that enumeration still
        # worked, on the reasoning that exact-prefix containment needs no case
        # policy. That reasoning was false on Windows: `Path.relative_to`
        # compares case-insensitively there, so the "exact" branch silently
        # behaved as though the policy had concluded "insensitive".
        with self.assertRaises(HeadlessError) as caught:
            self.catalogue(case_policy=CasePolicy.INCONCLUSIVE)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_CASE_POLICY_INCONCLUSIVE"
        )

    def test_inconclusive_case_policy_tolerates_an_empty_root(self) -> None:
        # Narrow exemption: there is nothing to enumerate and nothing to
        # resolve, so refusing would only turn "I made a folder for my VAEs"
        # into a hard error. resolve() still refuses under the policy.
        empty = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fx-empty-"))
        self.addCleanup(shutil.rmtree, empty, True)
        catalogue = ModelCatalogue(
            empty,
            workspace_root=WORKSPACE_ROOT,
            case_policy=CasePolicy.INCONCLUSIVE,
        )
        self.assertEqual(catalogue.enumerate(), ())
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve("0" * 32)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_CASE_POLICY_INCONCLUSIVE"
        )

    def test_case_sensitive_containment_is_actually_case_sensitive(self) -> None:
        # Real directories, because the case-insensitive branch now asks the
        # filesystem rather than folding strings.
        root = self.root / "Models"
        root.mkdir()
        same = root / "x.safetensors"
        same.write_bytes(b"")
        flipped = self.root / "models" / "x.safetensors"

        self.assertTrue(_is_prefix(root, same, CasePolicy.CASE_SENSITIVE))
        self.assertFalse(_is_prefix(root, flipped, CasePolicy.CASE_SENSITIVE))
        # Under an insensitive policy the answer depends on whether the volume
        # really treats them as one directory -- which is the point.
        expected = os.path.exists(flipped.parent) and os.path.samefile(
            flipped.parent, root
        )
        self.assertEqual(
            _is_prefix(root, flipped, CasePolicy.CASE_INSENSITIVE), expected
        )

    def test_prefix_collision_is_refused_under_both_policies(self) -> None:
        # Models must not contain Models-Evil, textually or otherwise.
        root = self.root / "Models"
        evil_dir = self.root / "Models-Evil"
        root.mkdir()
        evil_dir.mkdir()
        evil = evil_dir / "x.safetensors"
        evil.write_bytes(b"")
        for policy in (CasePolicy.CASE_SENSITIVE, CasePolicy.CASE_INSENSITIVE):
            self.assertFalse(_is_prefix(root, evil, policy), policy)

    def test_containment_does_not_use_full_case_folding(self) -> None:
        # str.casefold() maps U+017F to 's' and 'ss', a WIDER equivalence than
        # any real filesystem's. Using it made two genuinely distinct
        # directories look like one. Guard the property directly.
        root = self.root / "Models"
        twin = self.root / "Modelſ"  # LATIN SMALL LETTER LONG S
        root.mkdir()
        try:
            twin.mkdir()
        except (OSError, UnicodeError):
            self.skipTest("volume cannot represent the twin name")
        if os.path.samefile(root, twin):
            self.skipTest("volume genuinely treats these as one directory")
        self.assertEqual(root.name.casefold(), twin.name.casefold())
        outside = twin / "Secret.safetensors"
        outside.write_bytes(b"")
        for policy in (CasePolicy.CASE_SENSITIVE, CasePolicy.CASE_INSENSITIVE):
            self.assertFalse(
                _is_prefix(root, outside, policy),
                f"{policy}: casefold equivalence leaked into containment",
            )

    def test_case_policy_is_resolved_once_without_writing(self) -> None:
        before = sorted(os.listdir(self.root))
        policy = detect_case_policy_readonly(self.root)
        after = sorted(os.listdir(self.root))
        self.assertEqual(before, after, "case probe wrote into the model root")
        self.assertIsInstance(policy, CasePolicy)

    def test_no_auto_discovery_from_environment(self) -> None:
        self.assertIsNone(configured_model_root({}))
        self.assertIsNone(configured_model_root({"STUDIO_MODEL_ROOT": "  "}))
        self.assertEqual(
            configured_model_root({"STUDIO_MODEL_ROOT": "Evidence/x"}),
            "Evidence/x",
        )

    def test_selector_performs_no_filesystem_access(self) -> None:
        imports = _module_imports("forge_studio.backend_selection")
        self.assertEqual(imports, {"os", "typing", "typing.Mapping"})

    def test_symlinked_entry_is_never_catalogued(self) -> None:
        # Proven on every platform by presenting the scanner an entry that
        # reports itself as a link, then -- where privileges allow -- again with
        # a real symlink. No skip, because the guard is what matters.
        with _as_symlink("alpha.safetensors"):
            locations = {
                item.relative_location for item in self.catalogue().enumerate()
            }
        self.assertNotIn("alpha.safetensors", locations)
        self.assertIn("alpha.ckpt", locations)

    def test_real_symlink_escape_is_not_catalogued(self) -> None:
        target = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, target, True)
        (target / "outside.safetensors").write_bytes(b"synthetic")
        try:
            (self.root / "escape.safetensors").symlink_to(
                target / "outside.safetensors"
            )
        except (OSError, NotImplementedError):
            # Windows without Developer Mode. The guard itself is proven above.
            return
        self.assertNotIn(
            "escape.safetensors",
            {item.relative_location for item in self.catalogue().enumerate()},
        )


# --------------------------------------------------------------------------
# 3. Catalogue entry contract
# --------------------------------------------------------------------------


class CatalogueContractTests(_FixtureCase):
    def test_ids_are_stable_across_reads(self) -> None:
        first = [item.model_id for item in self.catalogue().enumerate()]
        second = [item.model_id for item in self.catalogue().enumerate()]
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_ids_differ_per_root(self) -> None:
        other = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fx2-"))
        self.addCleanup(shutil.rmtree, other, True)
        (other / "alpha.safetensors").write_bytes(b"synthetic-not-a-checkpoint")
        mine = {
            item.relative_location: item.model_id
            for item in self.catalogue().enumerate()
        }
        theirs = {
            item.relative_location: item.model_id
            for item in ModelCatalogue(
                other, workspace_root=WORKSPACE_ROOT
            ).enumerate()
        }
        self.assertNotEqual(
            mine["alpha.safetensors"], theirs["alpha.safetensors"]
        )

    def test_ids_are_opaque_and_not_reversible(self) -> None:
        for item in self.catalogue().enumerate():
            self.assertEqual(len(item.model_id), 32)
            self.assertTrue(all(char in "0123456789abcdef" for char in item.model_id))
            self.assertNotIn(item.display_name, item.model_id)

    def test_no_absolute_path_in_any_field(self) -> None:
        needles = (str(self.root), str(APP_ROOT), str(Path.home()), os.sep + os.sep)
        for item in self.catalogue().enumerate():
            rendered = json.dumps(item.to_dict())
            for needle in needles:
                self.assertNotIn(needle, rendered)
            self.assertFalse(os.path.isabs(item.relative_location))

    def test_recognized_formats_come_from_backend_source(self) -> None:
        sd_models = (APP_ROOT / "modules" / "sd_models.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('ext_filter=[".ckpt", ".safetensors", ".gguf"]', sd_models)
        utils = (APP_ROOT / "backend" / "utils.py").read_text(encoding="utf-8")
        self.assertIn('(".safetensors", ".sft")', utils)
        self.assertEqual(
            {key for key, value in FORMAT_SUPPORT.items() if value is LoadSupport.LOAD_PLUMBED},
            {".safetensors", ".ckpt", ".gguf"},
        )
        self.assertIs(FORMAT_SUPPORT[".sft"], LoadSupport.RECOGNIZED_NOT_PLUMBED)

    def test_unsupported_and_blacklisted_are_excluded(self) -> None:
        locations = {
            item.relative_location for item in self.catalogue().enumerate()
        }
        self.assertNotIn("notes.txt", locations)
        self.assertNotIn("excluded.vae.safetensors", locations)
        self.assertIs(classify_format("x.txt")[1], LoadSupport.UNSUPPORTED)
        self.assertIs(
            classify_format("x.vae.safetensors")[1], LoadSupport.UNSUPPORTED
        )

    def test_duplicate_display_names_stay_distinct(self) -> None:
        items = [
            item for item in self.catalogue().enumerate()
            if item.display_name == "alpha"
        ]
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].model_id, items[1].model_id)

    def test_ordering_is_deterministic(self) -> None:
        order = [item.relative_location for item in self.catalogue().enumerate()]
        self.assertEqual(order, sorted(order))

    def test_size_comes_from_stat_and_content_is_never_read(self) -> None:
        expected = (self.root / "alpha.safetensors").stat().st_size
        item = next(
            entry for entry in self.catalogue().enumerate()
            if entry.relative_location == "alpha.safetensors"
        )
        self.assertEqual(item.size_bytes, expected)

    def test_enumeration_opens_no_catalogued_file(self) -> None:
        with _OpenRecorder(self.root) as recorder:
            self.catalogue().enumerate()
        self.assertEqual(recorder.attempts, [])

    def test_deleted_entry_is_reported_or_dropped_not_crashed(self) -> None:
        catalogue = self.catalogue()
        item = next(
            entry for entry in catalogue.enumerate()
            if entry.relative_location == "beta.gguf"
        )
        (self.root / "beta.gguf").unlink()
        remaining = {entry.relative_location for entry in catalogue.enumerate()}
        self.assertNotIn("beta.gguf", remaining)
        with self.assertRaises(HeadlessError) as caught:
            catalogue.resolve(item.model_id)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_oversized_filename_is_rejected_by_policy(self) -> None:
        # Tested directly: Windows cannot create a name long enough to reach the
        # guard through the filesystem, and a skipped test proves nothing.
        self.assertFalse(name_is_acceptable(("x" * 300) + ".safetensors"))
        self.assertFalse(name_is_acceptable("y" * 256))
        self.assertTrue(name_is_acceptable("y" * 255))
        self.assertTrue(name_is_acceptable("alpha.safetensors"))
        long_name = ("x" * 300) + ".safetensors"
        try:
            (self.root / long_name).write_bytes(b"synthetic")
        except OSError:
            return  # policy already proven above
        self.assertNotIn(
            long_name,
            {item.relative_location for item in self.catalogue().enumerate()},
        )

    def test_display_names_are_sanitized(self) -> None:
        self.assertEqual(sanitize_display_name("a\x00b"), "a b")
        self.assertEqual(sanitize_display_name("  spaced   out "), "spaced out")
        self.assertEqual(sanitize_display_name(""), "unnamed model")
        self.assertEqual(sanitize_display_name("../../etc/passwd"), ".. .. etc passwd")
        self.assertTrue(sanitize_display_name("y" * 400).endswith("..."))

    def test_availability_enum_is_used_not_a_string(self) -> None:
        for item in self.catalogue().enumerate():
            self.assertIsInstance(item.availability, ModelAvailability)
            self.assertIsInstance(item.load_support, LoadSupport)


class _OpenRecorder:
    """Record every attempt to open a file beneath ``root``, without blocking it."""

    def __init__(self, root: Path) -> None:
        self._root = str(root.resolve()).casefold()
        self.attempts: list[str] = []

    def __enter__(self) -> "_OpenRecorder":
        import builtins

        self._real = builtins.open
        recorder = self

        def guarded(file, *args, **kwargs):
            try:
                text = os.fspath(file)
            except TypeError:
                text = ""
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            if str(text).casefold().startswith(recorder._root):
                recorder.attempts.append(str(text))
            return recorder._real(file, *args, **kwargs)

        builtins.open = guarded  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        import builtins

        builtins.open = self._real  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 4. Load plumbing
# --------------------------------------------------------------------------


class LoadPlumbingTests(_FixtureCase):
    def test_request_reaches_the_port_with_contained_source(self) -> None:
        loader = _RecordingLoader()
        runtime = self.runtime(loader)
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.relative_location == "alpha.safetensors"
        )
        with self.assertRaises(HeadlessError):
            runtime.load_model(target.model_id)
        self.assertEqual(len(loader.requests), 1)
        request = loader.requests[0]
        self.assertEqual(request.model_id, target.model_id)
        self.assertEqual(request.format, "safetensors")
        self.assertEqual(request.operation, "load_checkpoint")
        self.assertEqual(request.source.relative_location, "alpha.safetensors")
        self.assertEqual(request.resolved_path, (self.root / "alpha.safetensors").resolve())

    def test_request_is_immutable(self) -> None:
        loader = _RecordingLoader()
        runtime = self.runtime(loader)
        runtime.configure_catalogue_root(self.root)
        target = runtime.list_models()[0]
        with self.assertRaises(HeadlessError):
            runtime.load_model(target.model_id)
        request = loader.requests[0]
        with self.assertRaises(Exception):
            request.model_id = "tampered"  # type: ignore[misc]

    def test_request_view_omits_the_resolved_path(self) -> None:
        loader = _RecordingLoader()
        runtime = self.runtime(loader)
        runtime.configure_catalogue_root(self.root)
        with self.assertRaises(HeadlessError):
            runtime.load_model(runtime.list_models()[0].model_id)
        rendered = json.dumps(loader.requests[0].to_dict())
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("resolved", rendered)

    def test_default_loader_refuses_after_validation(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        with self.assertRaises(HeadlessError) as caught:
            runtime.load_model(target.model_id)
        self.assertEqual(
            caught.exception.code, "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED"
        )

    def test_error_taxonomy_is_distinct(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        models = runtime.list_models()
        plumbed = next(
            item for item in models if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        recognized = next(
            item for item in models
            if item.load_support is LoadSupport.RECOGNIZED_NOT_PLUMBED
        )
        observed = {}
        for label, model_id in (
            ("authorized", plumbed.model_id),
            ("not_plumbed", recognized.model_id),
            ("unknown", "0" * 32),
            ("empty", ""),
        ):
            with self.assertRaises(HeadlessError) as caught:
                runtime.load_model(model_id)
            observed[label] = caught.exception.code
        self.assertEqual(
            observed,
            {
                "authorized": "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED",
                "not_plumbed": "HEADLESS_MODEL_FORMAT_NOT_PLUMBED",
                "unknown": "HEADLESS_MODEL_UNKNOWN",
                "empty": "HEADLESS_MODEL_ID_REQUIRED",
            },
        )
        self.assertEqual(len(set(observed.values())), 4)

    def test_unconfigured_catalogue_is_its_own_error(self) -> None:
        runtime = self.runtime()
        with self.assertRaises(HeadlessError) as caught:
            runtime.load_model("0" * 32)
        self.assertEqual(
            caught.exception.code, "HEADLESS_CATALOGUE_NOT_CONFIGURED"
        )

    def test_stale_entry_is_rejected_at_load_time(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.relative_location == "alpha.ckpt"
        )
        (self.root / "alpha.ckpt").unlink()
        with self.assertRaises(HeadlessError) as caught:
            runtime.load_model(target.model_id)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_root_swap_invalidates_ids(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        target = runtime.list_models()[0].model_id
        other = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fx3-"))
        self.addCleanup(shutil.rmtree, other, True)
        (other / "alpha.safetensors").write_bytes(b"synthetic-not-a-checkpoint")
        runtime.configure_catalogue_root(other)
        with self.assertRaises(HeadlessError) as caught:
            runtime.load_model(target)
        self.assertEqual(caught.exception.code, "HEADLESS_MODEL_UNKNOWN")

    def test_request_ids_are_unique(self) -> None:
        loader = _RecordingLoader()
        runtime = self.runtime(loader)
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        for _ in range(5):
            with self.assertRaises(HeadlessError):
                runtime.load_model(target.model_id)
        ids = [request.request_id for request in loader.requests]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)

    def test_repeated_failures_do_not_mutate_residency_or_state(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        before = runtime.state
        for _ in range(3):
            with self.assertRaises(HeadlessError):
                runtime.load_model(target.model_id)
        self.assertIs(runtime.state, before)
        self.assertIs(runtime.state, RuntimeState.CATALOGUE_READY_NO_MODEL)

    def test_load_opens_no_file_and_imports_no_torch(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        target = next(
            item for item in runtime.list_models()
            if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        torch_before = "torch" in sys.modules
        with _OpenRecorder(self.root) as recorder:
            with self.assertRaises(HeadlessError):
                runtime.load_model(target.model_id)
        self.assertEqual(recorder.attempts, [])
        self.assertEqual("torch" in sys.modules, torch_before)

    def test_loader_port_module_imports_nothing_heavy(self) -> None:
        imports = _module_imports("forge_headless.loader_port")
        for forbidden in ("torch", "gradio", "numpy"):
            self.assertNotIn(forbidden, imports)

    def test_phase2a_never_claims_model_loading(self) -> None:
        runtime = self.runtime()
        runtime.configure_catalogue_root(self.root)
        self.assertIn(runtime.state, PHASE2A_REACHABLE_STATES)
        self.assertNotIn(RuntimeState.MODEL_LOADING, PHASE2A_REACHABLE_STATES)
        self.assertNotIn(RuntimeState.READY, PHASE2A_REACHABLE_STATES)

    def test_wrong_operation_is_refused_by_the_port(self) -> None:
        from forge_headless.catalogue import ContainedSource

        request = LoadRequest(
            model_id="0" * 32,
            model_kind="checkpoint",
            format="safetensors",
            operation="unsupported_operation",
            request_id="load-000000-deadbeefcafe",
            source=ContainedSource(
                root=self.root,
                relative_location="alpha.safetensors",
                resolved=self.root / "alpha.safetensors",
            ),
        )
        with self.assertRaises(HeadlessError) as caught:
            PolicyGatedLoader().load(request)
        self.assertEqual(
            caught.exception.code, "HEADLESS_MODEL_OPERATION_UNSUPPORTED"
        )


# --------------------------------------------------------------------------
# 5. Studio integration
# --------------------------------------------------------------------------


class StudioIntegrationTests(_FixtureCase):
    def adapter(self) -> tuple[ForgeHeadlessRuntime, HeadlessBackendAdapter]:
        runtime = self.runtime()
        return runtime, HeadlessBackendAdapter(runtime)

    def test_model_route_uses_the_existing_wire_contract(self) -> None:
        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        for summary in adapter.list_models():
            self.assertEqual(
                set(summary.to_dict()),
                {"model_id", "name", "description", "is_mock"},
            )
            self.assertFalse(summary.is_mock)

    def test_model_route_leaks_no_path(self) -> None:
        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        rendered = json.dumps([item.to_dict() for item in adapter.list_models()])
        for needle in (str(self.root), str(APP_ROOT), str(Path.home())):
            self.assertNotIn(needle, rendered)
        self.assertNotIn("relative_location", rendered)

    def test_unconfigured_catalogue_is_honestly_empty(self) -> None:
        _, adapter = self.adapter()
        self.assertEqual(adapter.list_models(), ())

    def test_repeated_catalogue_reads_are_pure(self) -> None:
        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        first = [item.to_dict() for item in adapter.list_models()]
        state = runtime.state
        second = [item.to_dict() for item in adapter.list_models()]
        self.assertEqual(first, second)
        self.assertIs(runtime.state, state)
        self.assertFalse(adapter.get_current_model().loaded)

    def test_load_route_returns_distinct_structured_errors(self) -> None:
        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        plumbed = next(
            item for item in runtime.list_models()
            if item.load_support is LoadSupport.LOAD_PLUMBED
        )
        with self.assertRaises(HeadlessBackendError) as authorized:
            adapter.load_model(plumbed.model_id)
        with self.assertRaises(HeadlessBackendError) as unknown:
            adapter.load_model("0" * 32)
        self.assertEqual(
            authorized.exception.error.code, "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED"
        )
        self.assertEqual(authorized.exception.http_status, 409)
        self.assertEqual(unknown.exception.error.code, "HEADLESS_MODEL_UNKNOWN")
        self.assertEqual(unknown.exception.http_status, 404)

    def test_load_failure_leaks_no_path_or_traceback(self) -> None:
        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        target = runtime.list_models()[0]
        with self.assertRaises(HeadlessBackendError) as caught:
            adapter.load_model(target.model_id)
        rendered = json.dumps(caught.exception.error.to_dict())
        for needle in (str(self.root), str(APP_ROOT), "Traceback", os.sep + "app"):
            self.assertNotIn(needle, rendered)

    def test_status_reports_catalogue_facts_without_paths(self) -> None:
        from forge_studio.application import StudioApplication

        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        application = StudioApplication(
            adapter,
            headless_runtime=runtime,
            selected_backend="forge-headless",
        )
        status = application.runtime_status()
        self.assertTrue(status["catalogue_configured"])
        self.assertTrue(status["catalogue_ready"])
        self.assertEqual(status["catalogue_count"], 4)
        self.assertTrue(status["model_load_plumbing_ready"])
        self.assertFalse(status["real_model_load_authorized"])
        self.assertFalse(status["model_loaded"])
        rendered = json.dumps(status)
        for needle in (str(self.root), str(APP_ROOT), str(Path.home())):
            self.assertNotIn(needle, rendered)

    def test_mock_status_gains_the_fields_and_stays_truthful(self) -> None:
        from forge_studio.application import StudioApplication
        from forge_studio.mock_backend import MockBackend

        application = StudioApplication(MockBackend())
        status = application.runtime_status()
        self.assertEqual(status["selected_backend"], "mock")
        self.assertFalse(status["catalogue_configured"])
        self.assertEqual(status["catalogue_count"], 0)
        self.assertFalse(status["real_model_load_authorized"])

    def test_mock_model_route_is_unchanged(self) -> None:
        from forge_studio.mock_backend import MockBackend

        for summary in MockBackend().list_models():
            self.assertEqual(
                set(summary.to_dict()),
                {"model_id", "name", "description", "is_mock"},
            )
            self.assertTrue(summary.is_mock)

    def test_generation_surfaces_still_refuse(self) -> None:
        from forge_studio.contracts import GenerationRequest

        runtime, adapter = self.adapter()
        runtime.configure_catalogue_root(self.root)
        request = GenerationRequest(
            model_id="x",
            positive_prompt="",
            negative_prompt="",
            seed=-1,
            steps=1,
            cfg_scale=1.0,
            width=64,
            height=64,
        )
        for call in (
            lambda: adapter.submit_generation(request),
            lambda: adapter.poll_or_stream_progress("job"),
            lambda: adapter.cancel_generation("job"),
            lambda: adapter.get_result("job"),
        ):
            with self.assertRaises(HeadlessBackendError) as caught:
                call()
            self.assertEqual(
                caught.exception.error.code, "HEADLESS_BACKEND_NOT_READY"
            )

    def test_adapter_implements_the_full_studio_contract(self) -> None:
        from forge_studio.backend import BackendAdapter

        runtime, adapter = self.adapter()
        self.assertIsInstance(adapter, BackendAdapter)
        for name in (
            "get_backend_status",
            "list_models",
            "submit_generation",
            "poll_or_stream_progress",
            "cancel_generation",
            "get_result",
            "load_model",
            "unload_model",
            "get_current_model",
            "get_capability",
            "shutdown",
        ):
            self.assertTrue(callable(getattr(adapter, name)), name)

    def test_studio_path_stays_gradio_free_with_the_catalogue_configured(self) -> None:
        with blocked_gradio_imports():
            runtime = ForgeHeadlessRuntime.construct(
                repository_root=APP_ROOT, workspace_root=WORKSPACE_ROOT
            )
            runtime.probe_readiness()
            runtime.configure_catalogue_root(self.root)
            adapter = HeadlessBackendAdapter(runtime)
            models = adapter.list_models()
            with self.assertRaises(HeadlessBackendError):
                adapter.load_model(models[0].model_id)
            runtime.shutdown()
            leaked = sorted(
                name for name in sys.modules
                if name.split(".", 1)[0] in FORBIDDEN
            )
        self.assertEqual(leaked, [])
        self.assertTrue(models)


# --------------------------------------------------------------------------
# 6. The Phase 2A subprocess probe
# --------------------------------------------------------------------------


class ProbeTests(unittest.TestCase):
    script = APP_ROOT / "scripts" / "headless" / "model_plumbing_probe.py"

    def test_script_exists_and_parses(self) -> None:
        self.assertTrue(self.script.is_file())
        ast.parse(self.script.read_text(encoding="utf-8"))

    def test_script_imports_nothing_forbidden_at_module_scope(self) -> None:
        imports = module_level_imports(
            ast.parse(self.script.read_text(encoding="utf-8"))
        )
        for forbidden in ("gradio", "gradio_client", "torch", "socket", "urllib"):
            self.assertNotIn(forbidden, imports)

    def test_script_declares_a_timeout(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        self.assertIn("TIMEOUT_SECONDS", source)
        self.assertIn("worker.join(TIMEOUT_SECONDS)", source)

    def test_probe_proves_all_ten_phase2a_conditions(self) -> None:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", str(self.script)],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout={completed.stdout}\nstderr={completed.stderr[-2000:]}",
        )
        report = json.loads(
            (
                FIXTURE_PARENT / "HEADLESS_MODEL_PLUMBING_REPORT.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(report["verdict"], "PHASE2A_MODEL_PLUMBING_PROVEN")
        self.assertEqual(
            sorted(report["verdicts"]),
            sorted(
                [
                    "HEADLESS_CATALOGUE_READY",
                    "MODEL_REQUEST_VALIDATED",
                    "MODEL_LOAD_BLOCKED_BY_POLICY",
                    "GRADIO_NOT_IMPORTED",
                    "CHECKPOINT_NOT_OPENED",
                    "TORCH_NOT_IMPORTED",
                    "NO_DEVICE_INITIALIZATION",
                    "NO_MODEL_LOADED",
                    "NO_GENERATION",
                    "NO_EXTERNAL_NETWORK",
                ]
            ),
        )
        self.assertFalse(report["checkpoint_opened"])
        self.assertFalse(report["torch_imported"])
        self.assertFalse(report["gradio_imported"])
        self.assertFalse(report["model_loaded"])
        self.assertFalse(report["generation_performed"])
        self.assertFalse(report["network_used"])
        self.assertFalse(report["public_socket_bound"])
        self.assertTrue(report["fixtures_removed"])

    def test_probe_report_redacts_absolute_paths(self) -> None:
        raw = (
            FIXTURE_PARENT / "HEADLESS_MODEL_PLUMBING_REPORT.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn(str(APP_ROOT), raw)
        self.assertNotIn(str(WORKSPACE_ROOT), raw)
        self.assertNotIn(Path.home().name, raw)

    def test_probe_reaches_the_port_exactly_once_for_one_valid_request(self) -> None:
        report = json.loads(
            (
                FIXTURE_PARENT / "HEADLESS_MODEL_PLUMBING_REPORT.json"
            ).read_text(encoding="utf-8")
        )
        requests = report["requests_reaching_port"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["operation"], "load_checkpoint")
        self.assertNotIn("resolved", json.dumps(requests))

    def test_probe_cleans_up_its_fixtures(self) -> None:
        self.assertFalse((FIXTURE_PARENT / "_probe_fixtures").exists())


# --------------------------------------------------------------------------
# 7. The suite cannot silently vanish
# --------------------------------------------------------------------------


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_phase2a_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__]
        )
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_PHASE2A_TESTS,
            "Phase 2A test count changed: update EXPECTED_PHASE2A_TESTS "
            "deliberately, or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
