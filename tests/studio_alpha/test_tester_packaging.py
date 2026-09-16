"""Release setup regressions derived from the packaging source review."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap_environment as bootstrap
from scripts import build_distributable as builder

APP = Path(__file__).resolve().parents[2]


def launcher():
    spec = importlib.util.spec_from_file_location(
        "release_launcher", APP / "packaging/windows/start_studio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FirstRunTests(unittest.TestCase):
    def test_real_template_creates_config_without_private_model_discovery(self):
        module = launcher()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module.ROOT = root
            module.CONFIG = root / "studio-config.json"
            module.TEMPLATE = APP / "docs/studio/internal-alpha/studio-config.template.json"
            self.assertEqual(0, module.bootstrap_config())
            data = json.loads(module.CONFIG.read_text(encoding="utf-8"))
            self.assertEqual({}, data["model_roots"])
            self.assertNotIn("profiles", data)
            self.assertEqual(str(root / "Studio-State"), data["studio_state_root"])
            self.assertEqual(0, data["port"])
            self.assertEqual(["studio-config.json"], [p.name for p in root.iterdir()])

    def test_existing_config_is_preserved_byte_for_byte(self):
        module = launcher()
        with tempfile.TemporaryDirectory() as tmp:
            module.CONFIG = Path(tmp) / "studio-config.json"
            original = b'{"custom": "keep this", "port": 8123}\r\n'
            module.CONFIG.write_bytes(original)
            self.assertEqual(0, module.bootstrap_config())
            self.assertEqual(original, module.CONFIG.read_bytes())

    def test_invalid_template_reports_failure_without_creating_config(self):
        module = launcher()
        with tempfile.TemporaryDirectory() as tmp:
            module.CONFIG = Path(tmp) / "studio-config.json"
            module.TEMPLATE = Path(tmp) / "template.json"
            module.TEMPLATE.write_text("not JSON")
            self.assertEqual(2, module.bootstrap_config())
            self.assertFalse(module.CONFIG.exists())


class AssetTests(unittest.TestCase):
    def test_exact_pinned_assets_are_included_and_no_other_models_are_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "models/adetailer/face.pt"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"approved")
            (model.parent / "owner-private.pt").write_bytes(b"exclude")
            manifest = root / "assets.json"
            manifest.write_text(json.dumps({"assets": [{
                "path": "models/adetailer/face.pt", "bytes": 8,
                "sha256": hashlib.sha256(b"approved").hexdigest()
            }]}))
            with patch.object(builder, "APP", root), patch.object(builder, "ASSET_MANIFEST", manifest):
                members = builder._asset_members("candidate")
                self.assertEqual([(model, "candidate/app/models/adetailer/face.pt")], members)
                model.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    builder._asset_members("candidate")

    def test_missing_asset_refuses_before_writing_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            with patch.object(builder, "_tree_is_clean", return_value=True), \
                 patch.object(builder, "_asset_members", side_effect=ValueError("Required asset missing")):
                self.assertEqual(4, builder.build(output))
            self.assertFalse(output.exists())

    def test_path_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "assets.json"
            manifest.write_text(json.dumps({"assets": [{
                "path": "../outside.pt", "bytes": 1, "sha256": "0" * 64
            }]}))
            with patch.object(builder, "APP", root), patch.object(builder, "ASSET_MANIFEST", manifest):
                with self.assertRaisesRegex(ValueError, "outside models"):
                    builder._asset_members("candidate")


class RepairTests(unittest.TestCase):
    def test_installed_torch_does_not_hide_missing_other_dependencies(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            # Metadata probe sees an incomplete requirements installation.
            code = 1 if "from importlib import metadata" in " ".join(command) else 0
            return subprocess.CompletedProcess(command, code)
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            exe = bootstrap.venv_interpreter(venv)
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            self.assertTrue(bootstrap.ensure(venv_dir=venv, runner=runner))
        installs = [c for c in calls if "pip" in c]
        self.assertEqual(1, len(installs))
        self.assertIn("-r", installs[0])

    def test_missing_requirements_cannot_be_reported_as_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.ensure(
                    venv_dir=Path(tmp) / "venv",
                    requirements=Path(tmp) / "missing.txt",
                    runner=lambda *a, **k: subprocess.CompletedProcess(a, 0))


if __name__ == "__main__":
    unittest.main()
