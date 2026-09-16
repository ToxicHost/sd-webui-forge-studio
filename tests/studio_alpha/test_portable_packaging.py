"""Portable input trust and damaged-installation regressions."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import build_portable as builder
from scripts import portable_runtime

APP = Path(__file__).resolve().parents[2]


class ArchiveBoundaryTests(unittest.TestCase):
    def test_rejects_absolute_parent_drive_and_backslash_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("../escape", "/absolute", "C:/drive", "safe/../../escape", "safe\\escape"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    builder.checked_path(Path(tmp), name)

    def test_rejects_duplicate_windows_names_and_symlinks_before_extraction(self):
        for kind in ("duplicate", "link"):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as bundle:
                bundle.writestr("safe/file.txt", b"one")
                if kind == "duplicate":
                    bundle.writestr("SAFE/FILE.TXT", b"two")
                else:
                    info = zipfile.ZipInfo("safe/link")
                    info.external_attr = 0o120777 << 16
                    bundle.writestr(info, b"../../outside")
            data.seek(0)
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(data) as bundle:
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    builder.check_zip(bundle, Path(tmp))
                self.assertEqual([], list(Path(tmp).iterdir()))

    def test_a_tampered_cached_input_is_refused_without_network_or_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dependency.whl"
            path.write_bytes(b"changed")
            spec = {"filename":path.name, "url":"https://example.invalid/dependency.whl",
                    "sha256":hashlib.sha256(b"approved").hexdigest()}
            with patch.object(builder.urllib.request, "urlopen") as network:
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    builder.input_file(Path(tmp), spec, True)
                network.assert_not_called()
            self.assertEqual(b"changed", path.read_bytes())

    def test_verified_cached_input_can_be_used_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dependency.whl"
            path.write_bytes(b"approved")
            spec = {"filename":path.name, "sha256":hashlib.sha256(b"approved").hexdigest()}
            self.assertEqual(path, builder.input_file(Path(tmp), spec, False))


class PrivateRuntimeTests(unittest.TestCase):
    def load_launcher(self):
        spec = importlib.util.spec_from_file_location("portable_test_launcher", APP / "packaging/windows/start_studio.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_damaged_portable_does_not_fall_back_to_existing_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            venv = app / "venv/Scripts/python.exe"
            venv.parent.mkdir(parents=True)
            venv.write_bytes(b"different-python")
            (app / "portable-runtime.json").write_text("{}")
            selected = self.load_launcher().interpreter_for(app)
            self.assertEqual(app / "runtime/python.exe", selected)
            self.assertFalse(selected.exists())

    def test_launcher_passes_private_isolation_and_optional_git_to_child(self):
        import contextlib
        import os
        for portable in (True, False):
            with self.subTest(portable=portable), tempfile.TemporaryDirectory() as tmp:
                app = Path(tmp)
                python = app / ("runtime/python.exe" if portable else "venv/Scripts/python.exe")
                python.parent.mkdir(parents=True)
                python.touch()
                launcher = app / "launch_studio.py"
                launcher.touch()
                if portable:
                    (app / "portable-runtime.json").write_text("{}")
                module = self.load_launcher()
                with patch.multiple(module, APP=app, PYTHON=python, LAUNCHER=launcher), \
                        patch.object(module, "bootstrap_config", return_value=0), \
                        patch.object(module, "open_browser_when_ready"), \
                        patch.object(module.sys, "argv", ["start_studio.py", "--fast-fp16"]), \
                        patch.object(module.sys, "path", list(sys.path)), \
                        patch.dict(os.environ, {"GIT_PYTHON_REFRESH":"error"}), \
                        patch.object(portable_runtime, "check", return_value=[]), \
                        patch.object(module.subprocess, "Popen") as spawn, \
                        contextlib.redirect_stdout(io.StringIO()):
                    spawn.return_value.wait.return_value = 0
                    self.assertEqual(0, module.main())
                    command = spawn.call_args.args[0]
                    self.assertEqual(str(python), command[0])
                    self.assertEqual(portable, "-I" in command)
                    self.assertEqual(portable, "-B" in command)
                    self.assertEqual("--fast-fp16", command[-1])
                    self.assertEqual("quiet" if portable else "error", os.environ["GIT_PYTHON_REFRESH"])

    def test_missing_manifest_and_missing_libraries_report_failure_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            self.assertTrue(portable_runtime.check(app))
            self.assertEqual([], list(app.iterdir()))
            spec = {"python":{"version":".".join(map(str,sys.version_info[:3]))},
                    "packages":[{"name":"deliberately-absent", "version":"1.0"}]}
            (app / "portable-runtime.json").write_text(json.dumps(spec))
            errors = portable_runtime.check(app)
            self.assertTrue(any("Missing or changed library" in e for e in errors))
            self.assertEqual(["portable-runtime.json"], [p.name for p in app.iterdir()])

    def test_integrity_check_detects_modified_runtime_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            runtime = app / "runtime"
            runtime.mkdir()
            (runtime / "python.exe").write_bytes(b"modified")
            (app / "portable-runtime.json").write_text(json.dumps({
                "python":{"version":".".join(map(str,sys.version_info[:3]))}, "packages":[]}))
            (app / "runtime-files.json").write_text(json.dumps({"files":[{
                "path":"python.exe", "sha256":hashlib.sha256(b"original").hexdigest()}]}))
            with patch.object(portable_runtime.sys, "executable", str(runtime / "python.exe")):
                errors = portable_runtime.check(app, verify=True)
            self.assertEqual(["Changed runtime file: python.exe"], errors)

    def test_manifest_cannot_hash_files_outside_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            (app / "portable-runtime.json").write_text(json.dumps({
                "python":{"version":".".join(map(str,sys.version_info[:3]))}, "packages":[]}))
            (app / "runtime-files.json").write_text(json.dumps({"files":[{
                "path":"../outside", "sha256":"0"*64}]}))
            errors = portable_runtime.check(app, verify=True)
            self.assertTrue(any("Invalid runtime manifest path" in e for e in errors))

    def test_pip_local_wrappers_and_urls_are_excluded_but_native_libraries_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ("Lib/site-packages/bin/tool.exe", "Lib/site-packages/p.dist-info/direct_url.json",
                             "Lib/site-packages/p/__pycache__/x.pyc", "Lib/site-packages/p/native.pyd",
                             "Lib/site-packages/p.dist-info/LICENSE"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"content")
            self.assertEqual({"native.pyd","LICENSE"}, {p.name for p in builder.runtime_files(root)})


if __name__ == "__main__":
    unittest.main()
