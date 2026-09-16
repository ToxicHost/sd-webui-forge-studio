"""Regression coverage for unsupported PATH Python during first-run setup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap_environment as bootstrap


def found(command, version=(3, 13), executable="selected-python.exe"):
    return subprocess.CompletedProcess(command, 0, json.dumps({
        "version": version, "executable": executable,
    }))


class LauncherProbeTests(unittest.TestCase):
    def test_prefers_supported_313_and_disables_automatic_installation(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            self.assertNotIn("PYLAUNCHER_ALLOW_INSTALL", kwargs["env"])
            self.assertNotIn("PYLAUNCHER_ALWAYS_INSTALL", kwargs["env"])
            self.assertEqual("false", kwargs["env"]["PYTHON_MANAGER_AUTOMATIC_INSTALL"])
            self.assertEqual(10, kwargs["timeout"])
            self.assertNotIn("shell", kwargs)
            return found(command)
        with patch.dict(os.environ, {"PYLAUNCHER_ALLOW_INSTALL":"1", "PYLAUNCHER_ALWAYS_INSTALL":"1"}):
            self.assertEqual("selected-python.exe", bootstrap.find_supported_python(run))
        self.assertEqual(["py", "-3.13"], calls[0][:2])
        self.assertEqual(1, len(calls))

    def test_an_unsupported_probe_result_does_not_hide_supported_312(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return found(command, (3, 14) if len(calls) == 1 else (3, 12))
        self.assertEqual("selected-python.exe", bootstrap.find_supported_python(run))
        self.assertEqual(["-3.13", "-3.12"], [c[1] for c in calls])

    def test_missing_launcher_reports_no_candidate(self):
        with patch.object(bootstrap.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(bootstrap.find_supported_python())

    def test_timeout_and_bad_output_allow_fallback_to_311(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(command, 10)
            if len(calls) == 2:
                return subprocess.CompletedProcess(command, 0, "not JSON")
            return found(command, (3, 11))
        self.assertEqual("selected-python.exe", bootstrap.find_supported_python(run))
        self.assertEqual("-3.11", calls[-1][1])

    def test_failed_probe_and_empty_executable_are_rejected(self):
        def run(command, **kwargs):
            if command[1] == "-3.13":
                return subprocess.CompletedProcess(command, 1, "")
            return found(command, executable="")
        self.assertIsNone(bootstrap.find_supported_python(run))


class InterpreterSelectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for context in (
            patch.object(bootstrap, "VENV_DIR", self.root / "venv"),
            patch.object(bootstrap.sys, "version_info", (3, 10, 0)),
            patch.object(bootstrap.sys, "platform", "win32"),
            patch.dict(os.environ, {"PYTHON":""}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_path_310_hands_setup_to_supported_runtime_without_installing_in_310(self):
        selected = str(self.root / "Selected Python" / "python.exe")
        with patch.object(bootstrap, "find_supported_python", return_value=selected), \
             patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             patch.object(bootstrap, "ensure") as ensure:
            self.assertEqual(0, bootstrap.main(["--quiet"]))
        ensure.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(selected, command[0])
        self.assertEqual(str(Path(bootstrap.__file__).resolve()), command[1])
        self.assertIn("--quiet", command)
        self.assertIn("--no-python-search", command)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_child_setup_failure_is_returned_without_trying_another_environment(self):
        with patch.object(bootstrap, "find_supported_python", return_value="selected.exe") as find, \
             patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)), \
             patch.object(bootstrap, "ensure") as ensure:
            self.assertEqual(7, bootstrap.main([]))
        find.assert_called_once()
        ensure.assert_not_called()

    def test_explicit_unsupported_python_is_respected_and_explained(self):
        with patch.dict(os.environ, {"PYTHON":"owner-selected.exe"}), \
             patch.object(bootstrap, "find_supported_python") as find:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "PYTHON is explicitly set"):
                bootstrap.select_interpreter([])
        find.assert_not_called()

    def test_existing_incompatible_environment_is_preserved(self):
        exe = bootstrap.venv_interpreter(bootstrap.VENV_DIR)
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"existing environment")
        with patch.object(bootstrap, "find_supported_python") as find:
            with self.assertRaisesRegex(bootstrap.BootstrapError, "existing app/venv has been preserved"):
                bootstrap.select_interpreter([])
        self.assertEqual(b"existing environment", exe.read_bytes())
        find.assert_not_called()

    def test_no_supported_runtime_stops_before_any_install(self):
        with patch.object(bootstrap, "find_supported_python", return_value=None), \
             patch.object(bootstrap, "ensure") as ensure:
            self.assertEqual(1, bootstrap.main([]))
        ensure.assert_not_called()
        self.assertFalse(bootstrap.VENV_DIR.exists())

    def test_reexecution_cannot_loop(self):
        with patch.object(bootstrap, "find_supported_python") as find:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.select_interpreter(["--no-python-search"])
        find.assert_not_called()

    def test_supported_python_needs_no_discovery_and_check_mode_never_installs(self):
        with patch.object(bootstrap.sys, "version_info", (3, 13, 5)), \
             patch.object(bootstrap, "find_supported_python") as find, \
             patch.object(bootstrap, "ensure") as ensure:
            self.assertEqual(0, bootstrap.main(["--check-python"]))
        find.assert_not_called()
        ensure.assert_not_called()

    def test_selected_runtime_that_cannot_start_reports_failure(self):
        with patch.object(bootstrap, "find_supported_python", return_value="selected.exe"), \
             patch.object(bootstrap.subprocess, "run", side_effect=OSError("cannot execute")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "Could not start selected Python"):
                bootstrap.select_interpreter([])


if __name__ == "__main__":
    unittest.main()
