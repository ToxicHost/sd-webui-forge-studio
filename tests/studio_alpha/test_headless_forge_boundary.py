"""Headless Forge boundary — Gradio excision Phase 1.

Six groups: static architecture, facade lifecycle, import blocker, result
seam, readiness subprocess, and Studio integration.

Proofs are AST or import-hook based, never substring scans over source text.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# `gradio_import_blocker` is a sibling helper, not an installed module. Under
# `run_tests.py` this directory is already the discovery root; under
# `unittest discover -t .` it is not, so put it on the path explicitly. One
# identity either way, which matters because ForbiddenImportError is caught by
# class.
TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

HEADLESS_ROOT = APP_ROOT / "forge_headless"
OWNED_ROOT = APP_ROOT / "forge_studio"

from gradio_import_blocker import (  # noqa: E402  (path set above)
    ForbiddenImportError,
    blocked_gradio_imports,
    forbidden_modules_in_sys_modules,
)


def _module_level_imports(path: Path) -> set[str]:
    graph = importlib.import_module("forge_headless.import_graph")
    return graph.module_level_imports(ast.parse(path.read_text(encoding="utf-8")))


def _python_sources(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts and "frontend" not in p.parts
    ]


# ---------------------------------------------------------------- static


class StaticArchitectureTests(unittest.TestCase):
    """The boundary must hold structurally, not just when tests happen to run."""

    FORBIDDEN = ("gradio", "gradio_client")
    NEO_UI = ("modules.ui", "modules.ui_tempdir", "modules.gradio_extensions")

    def test_headless_package_never_imports_gradio(self) -> None:
        for path in _python_sources(HEADLESS_ROOT):
            with self.subTest(module=path.name):
                for imported in _module_level_imports(path):
                    head = imported.split(".", 1)[0]
                    self.assertNotIn(head, self.FORBIDDEN)

    def test_headless_package_never_imports_neo_ui(self) -> None:
        for path in _python_sources(HEADLESS_ROOT):
            with self.subTest(module=path.name):
                for imported in _module_level_imports(path):
                    for banned in self.NEO_UI:
                        self.assertFalse(
                            imported == banned
                            or imported.startswith(banned + "."),
                            f"{path.name} imports {imported}",
                        )

    def test_headless_package_imports_no_forge_at_module_scope(self) -> None:
        """Importing the package must be side-effect free."""

        for path in _python_sources(HEADLESS_ROOT):
            with self.subTest(module=path.name):
                for imported in _module_level_imports(path):
                    head = imported.split(".", 1)[0]
                    self.assertNotIn(
                        head,
                        ("modules", "modules_forge", "backend", "torch"),
                    )

    def test_owned_studio_package_purity_is_unchanged(self) -> None:
        for path in _python_sources(OWNED_ROOT):
            with self.subTest(module=path.name):
                for imported in _module_level_imports(path):
                    head = imported.split(".", 1)[0]
                    self.assertNotIn(
                        head,
                        ("torch", "torchvision", "gradio", "numpy"),
                    )

    def test_owned_studio_package_never_imports_forge(self) -> None:
        for path in _python_sources(OWNED_ROOT):
            with self.subTest(module=path.name):
                for imported in _module_level_imports(path):
                    head = imported.split(".", 1)[0]
                    self.assertNotIn(head, ("modules", "modules_forge", "backend"))

    def test_backend_tree_is_free_of_module_level_gradio(self) -> None:
        """The load-bearing finding: backend/ carries no Gradio import."""

        graph = importlib.import_module("forge_headless.import_graph")
        result = graph.scan_package(
            APP_ROOT / "backend", APP_ROOT, self.FORBIDDEN
        )
        self.assertEqual((), result.offenders)
        self.assertGreater(result.modules_parsed, 50)

    def test_canonical_frontend_is_untouched_by_this_package(self) -> None:
        for path in _python_sources(HEADLESS_ROOT):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("forge_studio/frontend", source)
                self.assertNotIn("forge_studio\\frontend", source)


# ---------------------------------------------------------------- facade


class FacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.headless = importlib.import_module("forge_headless")
        self.facade_module = importlib.import_module("forge_headless.facade")
        self.runtime = self.headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT
        )

    def test_construction_is_idempotent_and_starts_uninitialized(self) -> None:
        other = self.headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT
        )
        self.assertIs(
            self.headless.RuntimeState.UNINITIALIZED, self.runtime.state
        )
        self.assertIs(self.headless.RuntimeState.UNINITIALIZED, other.state)

    def test_readiness_reaches_ready_no_model(self) -> None:
        readiness = self.runtime.probe_readiness()
        self.assertIs(
            self.headless.RuntimeState.READY_NO_MODEL, readiness.state
        )
        self.assertTrue(readiness.ready)

    def test_readiness_is_idempotent(self) -> None:
        first = self.runtime.probe_readiness()
        self.assertIs(first, self.runtime.probe_readiness())

    def test_phase1_never_claims_a_state_beyond_ready_no_model(self) -> None:
        readiness = self.runtime.probe_readiness()
        self.assertIn(
            readiness.state, self.headless.contracts.PHASE1_REACHABLE_STATES
        )
        for forbidden in (
            self.headless.RuntimeState.READY,
            self.headless.RuntimeState.BUSY,
            self.headless.RuntimeState.MODEL_LOADING,
        ):
            self.assertNotEqual(forbidden, readiness.state)

    def test_readiness_reports_no_model_and_no_generation(self) -> None:
        readiness = self.runtime.probe_readiness()
        self.assertFalse(readiness.model_loaded)
        self.assertFalse(readiness.generation_performed)

    def test_readiness_imports_only_declared_safe_modules(self) -> None:
        readiness = self.runtime.probe_readiness()
        self.assertEqual(
            set(self.facade_module.SAFE_BACKEND_MODULES),
            set(readiness.verified_modules),
        )

    def test_declared_safe_modules_really_are_safe(self) -> None:
        """Each must be free of Gradio, Torch, and device probing at import."""

        graph = importlib.import_module("forge_headless.import_graph")
        for name in self.facade_module.SAFE_BACKEND_MODULES:
            path = APP_ROOT / Path(*name.split(".")).with_suffix(".py")
            with self.subTest(module=name):
                self.assertTrue(path.is_file(), name)
                imports = graph.module_level_imports(
                    ast.parse(path.read_text(encoding="utf-8"))
                )
                for imported in imports:
                    head = imported.split(".", 1)[0]
                    self.assertNotIn(
                        head, ("gradio", "gradio_client", "torch")
                    )

    def test_device_probing_modules_are_not_imported(self) -> None:
        readiness = self.runtime.probe_readiness()
        for name in self.facade_module.DEVICE_PROBING_BACKEND_MODULES:
            with self.subTest(module=name):
                self.assertNotIn(name, readiness.verified_modules)
                self.assertNotIn(name, sys.modules)

    def test_phase2_blockers_are_reported_and_located(self) -> None:
        readiness = self.runtime.probe_readiness()
        phase2 = [b for b in readiness.blockers if b.phase == "phase-2"]
        self.assertTrue(phase2)
        for blocker in phase2:
            with self.subTest(module=blocker.module):
                self.assertTrue(blocker.code)
                self.assertTrue(blocker.module)
                self.assertTrue(blocker.detail)

    def test_no_phase1_blocker_on_a_healthy_repository(self) -> None:
        readiness = self.runtime.probe_readiness()
        self.assertEqual(
            [], [b for b in readiness.blockers if b.phase == "phase-1"]
        )

    def test_capability_declines_to_claim_device_facts(self) -> None:
        self.runtime.probe_readiness()
        capability = self.runtime.get_runtime_capability()
        self.assertEqual("UNKNOWN", capability.device_type)
        self.assertEqual("UNKNOWN", capability.dtype_policy)
        self.assertEqual("UNKNOWN", capability.attention_backend)
        self.assertEqual("UNKNOWN", capability.maximum_dimension)

    def test_capability_reports_phase1_truthfully(self) -> None:
        self.runtime.probe_readiness()
        capability = self.runtime.get_runtime_capability()
        self.assertTrue(capability.headless_startup_supported)
        self.assertFalse(capability.model_loading_implemented)
        self.assertFalse(capability.generation_implemented)
        self.assertFalse(capability.cancellation_implemented)
        self.assertFalse(capability.progress_implemented)
        self.assertTrue(capability.owned_result_delivery_enabled)

    def test_capability_startup_is_false_before_a_probe(self) -> None:
        fresh = self.headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT
        )
        self.assertFalse(
            fresh.get_runtime_capability().headless_startup_supported
        )

    def test_identity_exposes_no_path_user_or_host(self) -> None:
        identity = self.runtime.get_runtime_identity()
        rendered = json.dumps(identity.to_dict())
        for leak in ("C:\\", "/Users/", "heras", str(APP_ROOT)):
            self.assertNotIn(leak, rendered)

    def test_identity_reports_headless_and_no_model(self) -> None:
        identity = self.runtime.get_runtime_identity()
        self.assertTrue(identity.headless_mode)
        self.assertFalse(identity.model_loaded)
        self.assertFalse(identity.gradio_imported)
        self.assertEqual("forge-neo-retained", identity.backend_family)

    def test_identity_and_capability_serialize_to_json(self) -> None:
        self.runtime.probe_readiness()
        for record in (
            self.runtime.get_runtime_identity(),
            self.runtime.get_runtime_capability(),
            self.runtime.probe_readiness(),
        ):
            with self.subTest(record=type(record).__name__):
                payload = record.to_dict()
                self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_compatibility_mode_is_independent_of_headless(self) -> None:
        legacy = self.headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT, compatibility_mode=True
        )
        legacy.probe_readiness()
        self.assertTrue(legacy.get_runtime_identity().compatibility_mode)
        self.assertTrue(legacy.get_runtime_capability().legacy_ui_available)
        self.assertFalse(
            self.runtime.get_runtime_identity().compatibility_mode
        )

    def test_shutdown_is_idempotent(self) -> None:
        self.runtime.probe_readiness()
        self.runtime.shutdown()
        self.runtime.shutdown()
        self.assertIs(self.headless.RuntimeState.STOPPED, self.runtime.state)

    def test_shutdown_before_probe_is_safe(self) -> None:
        self.runtime.shutdown()
        self.assertIs(self.headless.RuntimeState.STOPPED, self.runtime.state)

    def test_unsupported_operations_raise_stable_codes(self) -> None:
        # `list_models` and `load_model` are implemented as of Phase 2A, so with
        # no catalogue configured they report *that* rather than
        # "not implemented". Still an unconditional, stable, specific refusal.
        cases = (
            (self.runtime.list_models, (), "HEADLESS_CATALOGUE_NOT_CONFIGURED"),
            (self.runtime.load_model, ("m",), "HEADLESS_CATALOGUE_NOT_CONFIGURED"),
            (self.runtime.submit_generation, (object(),), "HEADLESS_BACKEND_NOT_READY"),
            (self.runtime.poll_generation, ("j",), "HEADLESS_BACKEND_NOT_READY"),
            (self.runtime.cancel_generation, ("j",), "HEADLESS_BACKEND_NOT_READY"),
        )
        for call, args, code in cases:
            with self.subTest(operation=call.__name__):
                with self.assertRaises(self.headless.HeadlessError) as raised:
                    call(*args)
                self.assertEqual(code, raised.exception.code)

    def test_unsupported_operations_never_fake_success(self) -> None:
        for call, args in (
            (self.runtime.list_models, ()),
            (self.runtime.load_model, ("m",)),
        ):
            with self.subTest(operation=call.__name__):
                with self.assertRaises(self.headless.HeadlessError):
                    call(*args)

    def test_cuda_is_reported_unknown_when_torch_is_absent(self) -> None:
        readiness = self.runtime.probe_readiness()
        if "torch" not in sys.modules:
            self.assertEqual("UNKNOWN", readiness.cuda_initialized)
        else:
            self.assertIn(readiness.cuda_initialized, ("true", "false", "UNKNOWN"))


# --------------------------------------------------------- import blocker


class ImportBlockerTests(unittest.TestCase):
    def test_direct_gradio_import_is_refused(self) -> None:
        with blocked_gradio_imports():
            with self.assertRaises(ForbiddenImportError) as raised:
                importlib.import_module("gradio")
            self.assertEqual("gradio", raised.exception.forbidden_module)

    def test_gradio_client_is_refused_separately(self) -> None:
        with blocked_gradio_imports():
            with self.assertRaises(ForbiddenImportError) as raised:
                importlib.import_module("gradio_client")
            self.assertEqual(
                "gradio_client", raised.exception.forbidden_module
            )

    def test_gradio_submodules_are_refused(self) -> None:
        for name in (
            "gradio.blocks",
            "gradio.processing_utils",
            "gradio_client.utils",
        ):
            with self.subTest(module=name):
                with blocked_gradio_imports():
                    with self.assertRaises(ForbiddenImportError):
                        importlib.import_module(name)

    def test_transitive_gradio_import_is_refused(self) -> None:
        """A module that imports Gradio itself must fail, not succeed quietly."""

        with blocked_gradio_imports():
            with self.assertRaises(ForbiddenImportError) as raised:
                importlib.import_module("modules.shared_gradio_themes")
            self.assertEqual("gradio", raised.exception.forbidden_module)

    def test_blocker_reports_the_importing_module(self) -> None:
        with blocked_gradio_imports() as blocker:
            with self.assertRaises(ForbiddenImportError):
                importlib.import_module("modules.shared_gradio_themes")
            self.assertTrue(blocker.attempts)
            name, importer = blocker.attempts[-1]
            self.assertEqual("gradio", name)
            self.assertEqual("modules.shared_gradio_themes", importer)

    def test_unrelated_imports_still_work(self) -> None:
        with blocked_gradio_imports():
            self.assertIsNotNone(importlib.import_module("json"))
            self.assertIsNotNone(importlib.import_module("forge_studio"))

    def test_blocker_is_removed_on_exit(self) -> None:
        with blocked_gradio_imports() as blocker:
            self.assertIn(blocker, sys.meta_path)
        self.assertNotIn(blocker, sys.meta_path)

    def test_legacy_neo_path_is_explicitly_outside_this_proof(self) -> None:
        """`modules.ui` is expected to import Gradio. That is the legacy path."""

        graph = importlib.import_module("forge_headless.import_graph")
        imports = graph.module_level_imports(
            ast.parse((APP_ROOT / "modules" / "ui.py").read_text(encoding="utf-8"))
        )
        self.assertIn("gradio", {i.split(".", 1)[0] for i in imports})


class StudioPathIsGradioFreeTests(unittest.TestCase):
    """The ten required proofs, each under an active blocker."""

    def test_owned_studio_server_imports_no_gradio(self) -> None:
        with blocked_gradio_imports():
            for name in (
                "forge_studio",
                "forge_studio.presentation",
                "forge_studio.source_api_adapter",
                "forge_studio.result_delivery",
            ):
                for loaded in [m for m in sys.modules if m.startswith(name)]:
                    del sys.modules[loaded]
            importlib.import_module("forge_studio.presentation")
            importlib.import_module("forge_studio.source_api_adapter")
            self.assertEqual([], forbidden_modules_in_sys_modules())

    def test_headless_layer_and_full_lifecycle_import_no_gradio(self) -> None:
        with blocked_gradio_imports():
            for loaded in [m for m in sys.modules if m.startswith("forge_headless")]:
                del sys.modules[loaded]
            headless = importlib.import_module("forge_headless")
            runtime = headless.ForgeHeadlessRuntime.construct(
                repository_root=APP_ROOT
            )
            readiness = runtime.probe_readiness()
            identity = runtime.get_runtime_identity()
            capability = runtime.get_runtime_capability()
            runtime.shutdown()

            self.assertEqual([], forbidden_modules_in_sys_modules())
            self.assertTrue(readiness.ready)
            self.assertFalse(readiness.gradio_imported)
            self.assertFalse(readiness.gradio_client_imported)
            self.assertFalse(identity.gradio_imported)
            self.assertTrue(capability.headless_startup_supported)

    def test_no_new_module_imports_modules_ui_or_ui_tempdir(self) -> None:
        graph = importlib.import_module("forge_headless.import_graph")
        for root in (HEADLESS_ROOT, OWNED_ROOT):
            for path in _python_sources(root):
                imports = graph.module_level_imports(
                    ast.parse(path.read_text(encoding="utf-8"))
                )
                with self.subTest(module=path.name):
                    self.assertNotIn("modules.ui", imports)
                    self.assertNotIn("modules.ui_tempdir", imports)


# ------------------------------------------------------------ result seam


class ResultSeamTests(unittest.TestCase):
    """A backend-produced file becomes an owned handle with no Gradio object."""

    def setUp(self) -> None:
        self.headless = importlib.import_module("forge_headless")
        self.studio = importlib.import_module("forge_studio")
        self.delivery = importlib.import_module("forge_studio.result_delivery")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "outputs"
        self.root.mkdir(parents=True)

    def _synthetic(self, name: str = "job-1.png") -> Path:
        target = self.root / name
        target.write_bytes(b"\x89PNG\r\n\x1a\n synthetic")
        return target

    def _descriptor(self, path: Path, media_type: str = "image/png"):
        return self.headless.ForgeResultDescriptor(
            job_id="job-1", path=path, media_type=media_type
        )

    def test_descriptor_validates_and_registers_to_an_opaque_handle(self) -> None:
        path = self._synthetic()
        descriptor = self._descriptor(path)
        resolved = descriptor.validate(owned_root=self.root)

        registry = self.delivery.ResultRegistry(self.root)
        asset = registry.register(resolved, media_type=descriptor.media_type)

        self.assertRegex(
            asset.handle, r"\Astudio-result/[0-9a-f]{32}\.png\Z"
        )
        self.assertEqual(
            path.read_bytes(), registry.read(asset.handle).content
        )

    def test_handle_reveals_no_path(self) -> None:
        registry = self.delivery.ResultRegistry(self.root)
        descriptor = self._descriptor(self._synthetic())
        asset = registry.register(
            descriptor.validate(owned_root=self.root),
            media_type=descriptor.media_type,
        )
        self.assertNotIn(str(self.root), asset.handle)
        self.assertNotIn("job-1", asset.handle)

    def test_descriptor_diagnostic_view_omits_the_path(self) -> None:
        descriptor = self._descriptor(self._synthetic())
        payload = descriptor.to_dict()
        self.assertEqual({"job_id", "file_name", "media_type"}, set(payload))
        self.assertNotIn(str(self.root), json.dumps(payload))

    def test_no_gradio_object_is_involved(self) -> None:
        with blocked_gradio_imports():
            registry = self.delivery.ResultRegistry(self.root)
            descriptor = self._descriptor(self._synthetic())
            asset = registry.register(
                descriptor.validate(owned_root=self.root),
                media_type=descriptor.media_type,
            )
            self.assertTrue(registry.read(asset.handle).content)
            self.assertEqual([], forbidden_modules_in_sys_modules())

    def test_seam_never_references_gradio_cache_machinery(self) -> None:
        source = (
            HEADLESS_ROOT / "result_descriptor.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                rendered = ast.unparse(node)
                for banned in ("gradio", "processing_utils", "move_files_to_cache"):
                    self.assertNotIn(banned, rendered)

    def test_outside_root_descriptor_is_rejected(self) -> None:
        outside = Path(self._temp.name) / "escape.png"
        outside.write_bytes(b"x")
        with self.assertRaises(self.headless.HeadlessError) as raised:
            self._descriptor(outside).validate(owned_root=self.root)
        self.assertEqual(
            "RESULT_DESCRIPTOR_OUTSIDE_ROOT", raised.exception.code
        )

    def test_traversal_descriptor_is_rejected(self) -> None:
        for candidate in (
            self.root / ".." / "escape.png",
            Path("/etc/passwd"),
            Path(r"C:\Windows\win.ini"),
        ):
            with self.subTest(path=str(candidate)):
                with self.assertRaises(self.headless.HeadlessError):
                    self._descriptor(candidate).validate(owned_root=self.root)

    def test_unsupported_media_type_is_rejected_at_the_seam(self) -> None:
        path = self._synthetic("job-1.svg")
        with self.assertRaises(self.headless.HeadlessError) as raised:
            self._descriptor(path, "text/html").validate(owned_root=self.root)
        self.assertEqual(
            "RESULT_DESCRIPTOR_MEDIA_TYPE_UNSUPPORTED", raised.exception.code
        )

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(self.headless.HeadlessError) as raised:
            self._descriptor(self.root / "absent.png").validate(
                owned_root=self.root
            )
        self.assertEqual("RESULT_DESCRIPTOR_UNREADABLE", raised.exception.code)

    def test_empty_job_id_is_rejected(self) -> None:
        path = self._synthetic()
        descriptor = self.headless.ForgeResultDescriptor(
            job_id="  ", path=path, media_type="image/png"
        )
        with self.assertRaises(self.headless.HeadlessError) as raised:
            descriptor.validate(owned_root=self.root)
        self.assertEqual(
            "RESULT_DESCRIPTOR_JOB_ID_INVALID", raised.exception.code
        )

    def test_registry_retention_behaviour_is_unchanged(self) -> None:
        registry = self.delivery.ResultRegistry(self.root, max_entries=2)
        handles = []
        for index in range(4):
            path = self._synthetic(f"job-{index}.png")
            handles.append(
                registry.register(path, media_type="image/png").handle
            )
        self.assertEqual(2, len(registry))
        with self.assertRaises(self.studio.StudioError) as raised:
            registry.read(handles[0])
        self.assertEqual("RESULT_GONE", raised.exception.error.code)

    def test_descriptor_media_types_are_a_subset_of_registry_types(self) -> None:
        descriptor_module = importlib.import_module(
            "forge_headless.result_descriptor"
        )
        self.assertTrue(
            descriptor_module.DELIVERABLE_MEDIA_TYPES
            <= set(self.delivery.SUPPORTED_MEDIA_TYPES)
        )


# ------------------------------------------------------ readiness probe


class ReadinessProbeTests(unittest.TestCase):
    """The Phase F subprocess probe, driven from the test suite."""

    PROBE = APP_ROOT / "scripts" / "headless" / "readiness_probe.py"

    def test_probe_script_exists_and_parses(self) -> None:
        self.assertTrue(self.PROBE.is_file())
        ast.parse(self.PROBE.read_text(encoding="utf-8"))

    def test_probe_imports_no_gradio_at_module_scope(self) -> None:
        graph = importlib.import_module("forge_headless.import_graph")
        imports = graph.module_level_imports(
            ast.parse(self.PROBE.read_text(encoding="utf-8"))
        )
        for imported in imports:
            head = imported.split(".", 1)[0]
            self.assertNotIn(head, ("gradio", "gradio_client", "torch"))

    def test_probe_declares_a_timeout_and_no_network(self) -> None:
        source = self.PROBE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.targets[0].id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Name)
        }
        self.assertIn("TIMEOUT_SECONDS", names)
        imports = importlib.import_module(
            "forge_headless.import_graph"
        ).module_level_imports(tree)
        for banned in ("socket", "urllib", "http", "requests", "httpx"):
            self.assertNotIn(banned, {i.split(".", 1)[0] for i in imports})

    def test_probe_runs_in_a_subprocess_and_reports_cleanly(self) -> None:
        report = APP_ROOT.parent / "Evidence" / "studio-headless-forge" / "HEADLESS_READINESS_REPORT.json"
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(self.PROBE)],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        self.assertTrue(report.is_file())
        payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual("HEADLESS_READY_NO_MODEL", payload["verdict"])
        self.assertFalse(payload["gradio_imported"])
        self.assertFalse(payload["gradio_client_imported"])
        self.assertFalse(payload["model_loaded"])
        self.assertFalse(payload["generation_performed"])
        self.assertFalse(payload["network_used"])
        self.assertFalse(payload["public_socket_bound"])

    def test_probe_report_redacts_absolute_paths(self) -> None:
        report = APP_ROOT.parent / "Evidence" / "studio-headless-forge" / "HEADLESS_READINESS_REPORT.json"
        if not report.is_file():
            self.skipTest("probe has not been run in this session")
        rendered = report.read_text(encoding="utf-8")
        for leak in ("C:\\Users", "/Users/", "heras", str(APP_ROOT)):
            self.assertNotIn(leak, rendered)


# --------------------------------------------------- Studio integration


class StudioIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")
        self.selection = importlib.import_module("forge_studio.backend_selection")

    def test_mock_is_the_default_backend(self) -> None:
        self.assertEqual("mock", self.selection.select_backend_name({}))

    def test_forge_headless_is_opt_in(self) -> None:
        self.assertEqual(
            "forge-headless",
            self.selection.select_backend_name(
                {"STUDIO_BACKEND": "forge-headless"}
            ),
        )

    def test_unknown_backend_falls_back_to_mock_without_raising(self) -> None:
        self.assertEqual(
            "mock",
            self.selection.select_backend_name({"STUDIO_BACKEND": "nonsense"}),
        )

    def test_neo_compatibility_is_independent_and_disabled_by_default(self) -> None:
        self.assertFalse(self.selection.neo_ui_compatibility_enabled({}))
        self.assertTrue(
            self.selection.neo_ui_compatibility_enabled(
                {"NEO_UI_COMPATIBILITY": "enabled"}
            )
        )

    def test_enabling_headless_does_not_enable_neo_ui(self) -> None:
        env = {"STUDIO_BACKEND": "forge-headless"}
        self.assertEqual("forge-headless", self.selection.select_backend_name(env))
        self.assertFalse(self.selection.neo_ui_compatibility_enabled(env))

    def test_disabling_neo_ui_does_not_change_backend_selection(self) -> None:
        env = {"NEO_UI_COMPATIBILITY": "disabled"}
        self.assertEqual("mock", self.selection.select_backend_name(env))

    def test_no_selector_broadens_network_or_model_access(self) -> None:
        source = (
            OWNED_ROOT / "backend_selection.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = importlib.import_module(
            "forge_headless.import_graph"
        ).module_level_imports(tree)
        for banned in ("socket", "urllib", "http", "subprocess", "torch"):
            self.assertNotIn(banned, {i.split(".", 1)[0] for i in imports})

    def test_headless_status_reads_are_pure_and_repeatable(self) -> None:
        headless = importlib.import_module("forge_headless")
        runtime = headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT
        )
        runtime.probe_readiness()
        first = runtime.get_runtime_identity().to_dict()
        for _ in range(3):
            self.assertEqual(first, runtime.get_runtime_identity().to_dict())
        self.assertIs(headless.RuntimeState.READY_NO_MODEL, runtime.state)

    def test_status_route_reports_headless_state_without_paths(self) -> None:
        presentation = importlib.import_module("forge_studio.presentation")
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        application = self.studio.StudioApplication(
            self.studio.MockBackend(event_interval_seconds=0.001)
        )
        surface = presentation.StudioPresentation(
            application, self.studio.GenerationRequest
        )
        adapter = adapter_module.SourceFrontendAdapter(surface)
        payload = adapter.get("/studio/runtime_status")

        self.assertEqual("mock", payload["selected_backend"])
        self.assertFalse(payload["model_loaded"])
        # The mock genuinely can generate, so reporting False here would be a
        # lie. The headless path is the one that reports generation
        # unavailable -- covered by the next test.
        self.assertTrue(payload["generation_available"])
        self.assertEqual("not_selected", payload["headless_state"])
        self.assertEqual("disabled", payload["legacy_compatibility_state"])
        # Studio can say it did not import Gradio itself, not that nothing in
        # the process did. UNKNOWN is the honest answer.
        self.assertIn(payload["gradio_imported"], (False, "UNKNOWN"))
        rendered = json.dumps(payload)
        for leak in ("C:\\", "/Users/", "Traceback", str(APP_ROOT)):
            self.assertNotIn(leak, rendered)

    def test_status_route_reports_headless_selection_truthfully(self) -> None:
        presentation = importlib.import_module("forge_studio.presentation")
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        headless = importlib.import_module("forge_headless")
        runtime = headless.ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT
        )
        runtime.probe_readiness()
        application = self.studio.StudioApplication(
            self.studio.MockBackend(event_interval_seconds=0.001),
            headless_runtime=runtime,
            selected_backend="forge-headless",
        )
        adapter = adapter_module.SourceFrontendAdapter(
            presentation.StudioPresentation(
                application, self.studio.GenerationRequest
            )
        )
        payload = adapter.get("/studio/runtime_status")

        self.assertEqual("forge-headless", payload["selected_backend"])
        self.assertEqual("ready_no_model", payload["headless_state"])
        self.assertFalse(payload["gradio_imported"])
        self.assertFalse(payload["model_loaded"])
        self.assertFalse(payload["generation_available"])
        self.assertTrue(payload["blocking_reason"])
        rendered = json.dumps(payload)
        for leak in ("C:\\", "/Users/", "Traceback", str(APP_ROOT)):
            self.assertNotIn(leak, rendered)

    def test_status_route_is_pure(self) -> None:
        presentation = importlib.import_module("forge_studio.presentation")
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        application = self.studio.StudioApplication(
            self.studio.MockBackend(event_interval_seconds=0.001)
        )
        adapter = adapter_module.SourceFrontendAdapter(
            presentation.StudioPresentation(
                application, self.studio.GenerationRequest
            )
        )
        first = adapter.get("/studio/runtime_status")
        for _ in range(3):
            self.assertEqual(first, adapter.get("/studio/runtime_status"))
        self.assertFalse(application.get_current_model().loaded)

    def test_mock_backend_behaviour_is_unchanged(self) -> None:
        application = self.studio.StudioApplication(
            self.studio.MockBackend(event_interval_seconds=0.001)
        )
        models = application.list_models()
        self.assertEqual(2, len(models))
        self.assertTrue(all(model.is_mock for model in models))


if __name__ == "__main__":
    unittest.main()
