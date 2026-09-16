"""Tier-0 generation contract: model identity, failure-path ownership, and
truthful lifecycle boundaries.

The first authorized Tier-0 attempt loaded the model, reached
`modules.processing.process_images_inner`, and died at
`modules/processing.py:894` reading catalogue identity the direct loader never
attached. It then failed to release the engine or serialise its Gradio guard,
because both were harvested only on the success path, and it reported
`denoising_started: true` for a run that never took a sampler step.

These tests pin all three corrections. Runtime cases run in a fresh interpreter
on the workspace venv; no model file is opened, no tensor is allocated, and no
sampling occurs.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PROCESSING = APP_ROOT / "modules" / "processing.py"
UNET = APP_ROOT / "modules" / "sd_unet.py"
LOADER = APP_ROOT / "modules" / "sd_models.py"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_TIER0_CONTRACT_TESTS = 23

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

VENV_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

PRELUDE = """
import os, sys
sys.argv = ["tier0-contract-case"]
sys.path.insert(0, {app!r})
sys.path.insert(0, os.path.join({app!r}, "modules_forge", "packages"))
from pathlib import Path as _Path
from forge_headless.headless_options import headless_options as _headless_options
_options_ctx = _headless_options(_Path({app!r}))
_options_ctx.__enter__()
"""

EPILOGUE = """
_options_ctx.__exit__(None, None, None)
"""


def case_interpreter() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable


def run_case(body: str) -> subprocess.CompletedProcess:
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        script = Path(work) / "case.py"
        script.write_text(
            PRELUDE.format(app=str(APP_ROOT)) + body + EPILOGUE, encoding="utf-8"
        )
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [case_interpreter(), "-B", str(script)],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env={**dict(os.environ)},
        )


def assert_case_ok(case: unittest.TestCase, result, label: str) -> None:
    case.assertEqual(
        result.returncode,
        0,
        f"{label} failed\nstdout:\n{result.stdout[-2000:]}\n"
        f"stderr:\n{result.stderr[-3000:]}",
    )
    case.assertIn("CASE_OK", result.stdout)


def load_probe_module():
    """Import the probe by path without running its CLI."""
    spec = importlib.util.spec_from_file_location("_tier0_probe_under_test", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------- 9.1 identity consumer inventory


class IdentityConsumerInventoryTests(unittest.TestCase):
    """Pin every identity read the Tier-0 closure makes against Forge source.

    If Forge introduces a new unconditional identity access, this fails here --
    cheaply, in a non-live suite -- instead of at the next authorized attempt.
    """

    IDENTITY_ATTRS = {"sd_checkpoint_info", "sd_model_hash", "sd_model_checkpoint"}

    def _inner(self) -> ast.AST:
        tree = ast.parse(PROCESSING.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "process_images_inner":
                return node
        self.fail("process_images_inner not found in modules/processing.py")

    def _model_identity_reads(self, node) -> list[tuple[int, str, str | None]]:
        found = []
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Attribute) or sub.attr not in self.IDENTITY_ATTRS:
                continue
            base = ast.unparse(sub.value)
            if base not in ("shared.sd_model", "p.sd_model", "model"):
                continue
            sub_field = None
            for outer in ast.walk(node):
                if isinstance(outer, ast.Attribute) and outer.value is sub:
                    sub_field = outer.attr
            found.append((sub.lineno, sub.attr, sub_field))
        return sorted(found)

    def test_process_images_inner_reads_exactly_the_known_identity_fields(self) -> None:
        reads = {(attr, sub) for _, attr, sub in self._model_identity_reads(self._inner())}
        self.assertEqual(
            reads,
            {("sd_checkpoint_info", "name_for_extra"), ("sd_model_hash", None)},
            "process_images_inner's model-identity reads changed. Update "
            "forge_headless/model_identity.REQUIRED_IDENTITY_FIELDS and the "
            "attachment to supply the new field truthfully -- do NOT relax "
            "modules/processing.py.",
        )

    def test_sd_unet_identity_read_is_still_model_name(self) -> None:
        tree = ast.parse(UNET.read_text(encoding="utf-8"))
        reads = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "sd_checkpoint_info":
                for outer in ast.walk(tree):
                    if isinstance(outer, ast.Attribute) and outer.value is node:
                        reads.append(outer.attr)
        self.assertEqual(
            sorted(reads),
            ["model_name"],
            "modules/sd_unet.py identity reads changed; it is reachable from "
            "process_images_inner via apply_unet().",
        )

    def test_apply_unet_is_still_reachable_from_the_inner_loop(self) -> None:
        # This is why model_name is required at all: opts.sd_unet defaults to
        # "Automatic", which sends get_unet_option() to the identity object.
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(self._inner())
            if isinstance(node, ast.Call)
        }
        self.assertIn("sd_unet.apply_unet", calls)

    def test_required_fields_constant_matches_the_inventory(self) -> None:
        from forge_headless.model_identity import REQUIRED_IDENTITY_FIELDS

        self.assertEqual(
            set(REQUIRED_IDENTITY_FIELDS),
            {
                "sd_checkpoint_info.name_for_extra",
                "sd_checkpoint_info.model_name",
                "sd_model_hash",
            },
        )

    def test_production_attachment_site_is_still_forge_model_reload(self) -> None:
        # The whole contract defect follows from Tier-0 skipping this function.
        # If Forge moves the attachment, the design note needs revisiting.
        source = LOADER.read_text(encoding="utf-8")
        self.assertIn("sd_model.sd_checkpoint_info = checkpoint_info", source)
        self.assertIn("sd_model.sd_model_hash = checkpoint_info.calculate_shorthash()", source)


# ------------------------------------ 9.3 Forge identity-constructor safety


class ForgeIdentityConstructorSafetyTests(unittest.TestCase):
    """Justify choosing the compatibility identity, from Forge's own source."""

    def _checkpoint_info(self) -> ast.AST:
        tree = ast.parse(LOADER.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "CheckpointInfo":
                return node
        self.fail("CheckpointInfo not found")

    def test_constructor_reads_the_payload(self) -> None:
        # read_metadata_from_safetensors + model_hash both open the file. This
        # is the documented reason Studio does not construct a CheckpointInfo
        # on a path that has already read the payload once.
        init = next(
            n for n in self._checkpoint_info().body
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        calls = {ast.unparse(n.func) for n in ast.walk(init) if isinstance(n, ast.Call)}
        self.assertIn("read_metadata_from_safetensors", calls)
        self.assertIn("model_hash", calls)

    def test_shorthash_performs_a_full_payload_hash(self) -> None:
        method = next(
            n for n in self._checkpoint_info().body
            if isinstance(n, ast.FunctionDef) and n.name == "calculate_shorthash"
        )
        calls = {ast.unparse(n.func) for n in ast.walk(method) if isinstance(n, ast.Call)}
        self.assertIn("hashes.sha256", calls)

    def test_studio_identity_performs_no_io_at_all(self) -> None:
        source = (APP_ROOT / "forge_headless" / "model_identity.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("os", "hashlib", "safetensors", "urllib", "requests", "socket"):
            self.assertNotIn(
                forbidden,
                imported,
                f"model_identity must not import {forbidden}: it performs no IO",
            )


# --------------------------------------------- 9.2 identity attachment proof


class IdentityAttachmentTests(unittest.TestCase):
    """A synthetic engine must satisfy the reads that killed the first attempt."""

    def test_attachment_supplies_the_three_required_fields(self) -> None:
        from forge_headless.model_identity import (
            DEFAULT_RUNTIME_LABEL,
            attach_model_identity,
        )

        class _Engine:
            pass

        engine = _Engine()
        attachment = attach_model_identity(engine)

        self.assertTrue(attachment.attached)
        self.assertEqual(attachment.source, "studio_compatibility_identity")
        self.assertEqual(attachment.hash_status, "unavailable")
        self.assertEqual(engine.sd_checkpoint_info.name_for_extra, DEFAULT_RUNTIME_LABEL)
        self.assertEqual(engine.sd_checkpoint_info.model_name, DEFAULT_RUNTIME_LABEL)
        self.assertIsNone(engine.sd_model_hash)

    def test_a_real_forge_identity_is_never_overwritten(self) -> None:
        from forge_headless.model_identity import attach_model_identity

        class _RealInfo:
            name_for_extra = "already-from-catalogue"
            model_name = "already-from-catalogue"

        class _Engine:
            sd_checkpoint_info = _RealInfo()
            sd_model_hash = "abcdef1234"

        engine = _Engine()
        attachment = attach_model_identity(engine)

        self.assertFalse(attachment.attached)
        self.assertEqual(attachment.source, "forge_checkpoint_info")
        self.assertEqual(attachment.hash_status, "real")
        self.assertIs(engine.sd_checkpoint_info, _Engine.sd_checkpoint_info)

    def test_no_absolute_path_leaks_into_the_identity(self) -> None:
        from forge_headless.model_identity import attach_model_identity

        class _Engine:
            pass

        engine = _Engine()
        attach_model_identity(engine)
        identity = engine.sd_checkpoint_info

        for value in (identity.name_for_extra, identity.model_name, repr(identity)):
            self.assertNotIn(":", value.replace("label=", ""))
            self.assertNotIn("\\", value)
            self.assertNotIn("/", value)
            self.assertNotIn(".safetensors", value)
        self.assertFalse(hasattr(identity, "filename"))

    def test_detach_removes_only_studio_identity(self) -> None:
        from forge_headless.model_identity import (
            attach_model_identity,
            detach_model_identity,
        )

        class _Engine:
            pass

        engine = _Engine()
        attach_model_identity(engine)
        self.assertTrue(detach_model_identity(engine))
        self.assertIsNone(engine.sd_checkpoint_info)
        # A second detach is a no-op rather than an error.
        self.assertFalse(detach_model_identity(engine))

    def test_pre_sampling_metadata_attribution_no_longer_raises(self) -> None:
        # The exact statements from modules/processing.py:894-895 and the
        # sd_unet lookup at modules/sd_unet.py:20, run against a synthetic
        # engine published through shared.sd_model. No model, no sampling.
        result = run_case(
            "import modules.processing\n"
            "from modules import shared, sd_unet\n"
            "from forge_headless.model_identity import attach_model_identity\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "attach_model_identity(engine)\n"
            "shared.sd_model = engine\n"
            "name = shared.sd_model.sd_checkpoint_info.name_for_extra\n"
            "hash_ = shared.sd_model.sd_model_hash\n"
            "unet_name = shared.sd_model.sd_checkpoint_info.model_name\n"
            "assert name == 'studio-tier0-session', name\n"
            "assert hash_ is None, hash_\n"
            "assert unet_name == 'studio-tier0-session', unet_name\n"
            "sd_unet.get_unet_option()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "pre-sampling identity attribution")


# ------------------------------------------ 9.4 / 9.5 / 9.7 failure-path


class FailurePathOwnershipTests(unittest.TestCase):
    """Everything the first attempt lost must survive an exception."""

    def test_release_reports_unowned_cleanup_when_the_engine_is_missing(self) -> None:
        probe = load_probe_module()
        released = probe._release(None)
        self.assertFalse(released["engine_reference_dropped"])
        self.assertFalse(released["owned_cleanup"])

    def test_release_drops_the_engine_and_detaches_identity(self) -> None:
        probe = load_probe_module()
        from forge_headless.model_identity import attach_model_identity

        class _Objects:
            unet = object()
            clip = object()
            vae = object()

        class _Engine:
            forge_objects = _Objects()

        engine = _Engine()
        attach_model_identity(engine)

        released = probe._release(engine)

        self.assertTrue(released["engine_reference_dropped"])
        self.assertTrue(released["owned_cleanup"])
        self.assertTrue(released["identity_detached"])
        self.assertIsNone(engine.forge_objects)

    def test_guard_counters_survive_an_exception_after_installation(self) -> None:
        # 9.5: the synthetic failure the first attempt would have needed.
        #
        # Runs in a subprocess on the workspace venv, not in-process: the
        # canonical runner uses `-I -S`, where Gradio is not importable, and an
        # in-process guard would resolve zero targets and "prove" nothing. A
        # test that passes under only one runner is worse than no test.
        result = run_case(
            "import importlib.util, json\n"
            f"spec = importlib.util.spec_from_file_location('probe', {str(PROBE)!r})\n"
            "probe = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(probe)\n"
            "record = {}\n"
            "guard = probe._GradioRuntimeGuard()\n"
            "guard.install()\n"
            "record['_ui_guard'] = guard\n"
            "try:\n"
            "    raise RuntimeError('synthetic generation failure')\n"
            "except RuntimeError:\n"
            "    pass\n"
            "finally:\n"
            "    harvested = record.pop('_ui_guard', None)\n"
            "    harvested.restore()\n"
            "    record['gradio_runtime'] = harvested.to_dict()\n"
            "report = record['gradio_runtime']\n"
            "assert len(report['instrumented']) == 12, report['instrumented']\n"
            "assert report['uninstrumented'] == [], report['uninstrumented']\n"
            "assert len(report['runtime_call_counts']) == 12\n"
            "for _n, _c in report['runtime_call_counts'].items():\n"
            "    assert isinstance(_c, int), (_n, _c)\n"
            "assert 'object at 0x' not in str(report)\n"
            "json.dumps(report)\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "guard counters after exception")


class LifecycleBoundaryTests(unittest.TestCase):
    """`denoising_started` must mean denoising started."""

    def _boundaries(self, step: int):
        probe = load_probe_module()

        class _Snapshot:
            def __init__(self, value: int) -> None:
                self.step = value

        class _Progress:
            def __init__(self, value: int) -> None:
                self._value = value

            def snapshot(self):
                return _Snapshot(self._value)

        return probe._LifecycleBoundaries(_Progress(step))

    def test_entering_the_inner_loop_is_not_denoising(self) -> None:
        boundaries = self._boundaries(0)
        boundaries.generation_inner_entered = True
        report = boundaries.to_dict()

        self.assertTrue(report["generation_inner_entered"])
        self.assertFalse(report["denoising_started"])
        self.assertEqual(report["sampling_step"], 0)
        self.assertFalse(report["decode_started"])
        self.assertEqual(report["conditioning_started"], "UNKNOWN")

    def test_denoising_becomes_true_only_on_a_real_sampler_step(self) -> None:
        boundaries = self._boundaries(1)
        boundaries.generation_inner_entered = True
        report = boundaries.to_dict()

        self.assertTrue(report["denoising_started"])
        self.assertEqual(report["sampling_step"], 1)

    def test_decode_is_reported_from_the_real_decode_entry_point(self) -> None:
        boundaries = self._boundaries(12)
        self.assertFalse(boundaries.to_dict()["decode_started"])
        boundaries._decode_calls = 1
        self.assertTrue(boundaries.to_dict()["decode_started"])

    def test_probe_no_longer_sets_denoising_before_the_inner_call(self) -> None:
        # The precise defect from the first attempt, pinned structurally.
        source = PROBE.read_text(encoding="utf-8")
        self.assertNotIn('out["denoising_started"] = True', source)
        self.assertIn("boundaries.generation_inner_entered = True", source)


class PartialReportDurabilityTests(unittest.TestCase):
    """9.7: facts recorded before an exception must still be in the report."""

    def test_facts_recorded_before_the_exception_survive(self) -> None:
        record: dict[str, object] = {"stage": "start", "errors": []}
        try:
            record["model_loaded"] = True
            record["engine_class"] = "Anima"
            raise RuntimeError("synthetic failure after model ready")
        except RuntimeError as exc:
            record["errors"].append({"code": "X", "message": str(exc)})
            record["stage"] = "failed"

        self.assertTrue(record["model_loaded"])
        self.assertEqual(record["engine_class"], "Anima")
        self.assertEqual(record["stage"], "failed")

    def test_worker_harvests_ownership_in_a_finally_not_on_success(self) -> None:
        # Structural: the harvest must sit in the cleanup block. The first
        # attempt had it on the success path, which is why nothing survived.
        source = PROBE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        worker = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "worker_main"
        )
        harvest_in_finally = False
        for node in ast.walk(worker):
            if isinstance(node, ast.Try) and node.finalbody:
                block = "\n".join(ast.unparse(stmt) for stmt in node.finalbody)
                if "_engine" in block and "_ui_guard" in block:
                    harvest_in_finally = True
        self.assertTrue(
            harvest_in_finally,
            "worker_main must harvest _engine and _ui_guard in a finally block",
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_TIER0_CONTRACT_TESTS,
            "Tier-0 contract test count changed: update "
            "EXPECTED_TIER0_CONTRACT_TESTS deliberately, or find the test that "
            "stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
