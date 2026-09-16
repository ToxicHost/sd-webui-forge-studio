"""Studio builds its own environment. AR7.1.

THE DEFECT THIS CLOSES

Owner: "Just need users to be able to install it without issue."

A tester extracted the archive, double-clicked, and got "Missing interpreter" --
which the guide described as "the expected behaviour, not a fault" before
telling them to "ask for the environment bundle separately, or build one
yourself". There is no environment bundle, and `build_distributable.py` is right
to refuse to make one: "a 10 GB folder with someone's venv in it" is not a
clean package.

And these testers had never built one. They run Forge Neo, the Studio extension
or ComfyUI -- and every one of those environments was made FOR them by a
launcher. Studio's launcher was the only one that did not.

THE SECOND HALF, WHICH IS WORSE THAN THE FIRST

`requirements.txt` carried a bare `torch`. No pin, no index URL. A plain
`pip install -r requirements.txt` resolves that against PyPI and installs the
CPU build -- SUCCESSFULLY -- after which Studio cannot generate and nothing
says why. Anyone who followed the guide's own "build one yourself" got that.

WHAT THESE TESTS REFUSE TO ACCEPT

**A healthy launch that touches the network.** This runs before EVERY start, so
the cost of "already fine" has to be a file check. Driven with a runner that
records calls, asserting it is never invoked -- a timing assertion would be
flaky and would not say what it meant.

**A torch install without an index URL.** Asserted on the COMMAND rather than
on the constant, because resolving from PyPI is the failure itself.

**Interpreter selection happens before installation.** The versioned launcher
prefers Studio's existing environment, respects explicit PYTHON, and can enter
setup through python or the Windows py launcher. The tracked bootstrap selects
an installed supported interpreter when PATH supplies an unsupported version.
Tests read the shipped launcher template, not the old workspace wrapper.

Review: `Evidence/source-review/AR7.1-environment-bootstrap.md`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_BOOTSTRAP_TESTS = 19

REQUIREMENTS = (APP_ROOT / "requirements.txt").read_text(encoding="utf-8")
LAUNCHER = (APP_ROOT / "packaging/windows/Start-Studio.bat").read_text(
    encoding="utf-8", errors="surrogateescape")


def bootstrap():
    import importlib

    return importlib.import_module("scripts.bootstrap_environment")


class _Runner:
    """A stand-in for `subprocess.run` that records instead of executing."""

    def __init__(self, torch_present: bool = True, fail: str | None = None):
        self.calls: list[list[str]] = []
        self._torch_present = torch_present
        self._fail = fail

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        text = " ".join(str(part) for part in command)
        if "import torch" in text:
            return subprocess.CompletedProcess(
                command, 0 if self._torch_present else 1)
        if self._fail and self._fail in text:
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    @property
    def pip_calls(self) -> list[list[str]]:
        return [c for c in self.calls
                if any("pip" in str(part) for part in c)]


def fake_venv(directory: Path) -> Path:
    """A venv-shaped directory whose interpreter exists."""

    venv = Path(directory) / "venv"
    interpreter = bootstrap().venv_interpreter(venv)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("", encoding="utf-8")
    return venv


class AHealthyInstallDoesNothingTests(unittest.TestCase):
    """The property that lets this run on every launch."""

    def test_a_complete_environment_installs_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = fake_venv(directory)
            runner = _Runner(torch_present=True)
            changed = bootstrap().ensure(venv_dir=venv, runner=runner)
        self.assertFalse(changed)
        self.assertEqual([], runner.pip_calls,
                         "a healthy launch reached for pip")

    def test_the_check_is_a_file_test_and_one_probe(self) -> None:
        """Asserted on the CALLS, not on elapsed time. A timing assertion
        would be flaky and would not say what it meant."""

        with tempfile.TemporaryDirectory() as directory:
            venv = fake_venv(directory)
            runner = _Runner(torch_present=True)
            bootstrap().inspect(venv, runner=runner)
        self.assertEqual(1, len(runner.calls))
        self.assertIn("import torch", " ".join(runner.calls[0]))

    def test_a_missing_venv_is_reported_without_running_anything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = _Runner()
            state = bootstrap().inspect(Path(directory) / "absent",
                                        runner=runner)
        self.assertIsNone(state.interpreter)
        self.assertFalse(state.complete)
        self.assertEqual([], runner.calls)


class APartialEnvironmentIsRepairedTests(unittest.TestCase):
    """What a failed or interrupted first run leaves behind."""

    def test_an_interpreter_without_torch_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = fake_venv(directory)
            state = bootstrap().inspect(venv, runner=_Runner(torch_present=False))
        self.assertIsNotNone(state.interpreter)
        self.assertFalse(state.torch_present)
        self.assertFalse(state.complete)

    def test_torch_is_installed_without_recreating_the_venv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = fake_venv(directory)
            runner = _Runner(torch_present=False)
            changed = bootstrap().ensure(venv_dir=venv, runner=runner)
        self.assertTrue(changed)
        joined = [" ".join(str(p) for p in c) for c in runner.calls]
        self.assertFalse(any("-m venv" in text for text in joined),
                         "an existing interpreter was thrown away")
        self.assertTrue(any("torch==" in text for text in joined))


class TheTorchTrapIsClosedTests(unittest.TestCase):
    """The half that fails silently rather than loudly."""

    def test_requirements_no_longer_carries_a_bare_torch(self) -> None:
        code = [line.strip() for line in REQUIREMENTS.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
        self.assertNotIn("torch", code,
                         "a bare `torch` resolves to the CPU build from PyPI")

    def test_requirements_says_why_torch_is_absent(self) -> None:
        """Otherwise the next person adds it back."""

        self.assertIn("bootstrap_environment", REQUIREMENTS)

    def test_torchsde_is_still_pinned(self) -> None:
        """The neighbouring line, so the edit is known to have removed one
        entry and not a block."""

        self.assertIn("torchsde==0.2.6", REQUIREMENTS)

    def test_the_install_command_carries_an_index_url(self) -> None:
        """Asserted on the COMMAND, because installing from PyPI IS the
        defect -- a constant could be right while the command dropped it."""

        command = " ".join(bootstrap().torch_install_command())
        self.assertIn("index-url", command)
        self.assertIn("download.pytorch.org", command)

    def test_the_command_is_pip_arguments_not_a_pip_invocation(self) -> None:
        """It is passed to `python -m pip`, so a leading "pip" would become
        `python -m pip pip install ...`."""

        self.assertNotIn("pip", bootstrap().torch_install_command()[:1])
        self.assertEqual("install", bootstrap().torch_install_command()[0])

    def test_an_owner_override_is_used_verbatim(self) -> None:
        """`TORCH_COMMAND` is trusted input. The vendored selector promises
        never to rewrite it, and this asserts Studio does not either."""

        import os

        original = os.environ.get("TORCH_COMMAND")
        os.environ["TORCH_COMMAND"] = "pip install torch==1.2.3 --custom"
        try:
            command = " ".join(bootstrap().torch_install_command())
        finally:
            if original is None:
                os.environ.pop("TORCH_COMMAND", None)
            else:
                os.environ["TORCH_COMMAND"] = original
        self.assertIn("torch==1.2.3", command)
        self.assertIn("--custom", command)


class FailuresAreLegibleTests(unittest.TestCase):
    def test_a_failed_step_names_the_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            venv = fake_venv(directory)
            runner = _Runner(torch_present=False, fail="torch==")
            with self.assertRaises(bootstrap().BootstrapError) as caught:
                bootstrap().ensure(venv_dir=venv, runner=runner)
        self.assertIn("torch", str(caught.exception).lower())

    def test_main_exits_zero_on_a_healthy_environment(self) -> None:
        """The launcher checks `errorlevel`, so the exit code IS the contract
        between these two files. Named for what it asserts -- an earlier
        version of this said "non_zero" and asserted 0."""

        self.assertEqual(0, bootstrap().main(["--quiet"]))

    def test_main_exits_non_zero_when_the_environment_cannot_be_built(self) -> None:
        """The half the launcher actually branches on."""

        module = bootstrap()
        original = module.ensure
        module.ensure = lambda *a, **k: (_ for _ in ()).throw(
            module.BootstrapError("no interpreter"))
        try:
            self.assertEqual(1, module.main([]))
        finally:
            module.ensure = original


class ReleaseLauncherTests(unittest.TestCase):
    """The versioned Windows entrypoint must reach the tracked bootstrap."""

    def test_it_calls_the_tracked_bootstrap(self) -> None:
        self.assertIn("bootstrap_environment.py", LAUNCHER)

    def test_it_preserves_the_existing_environment(self) -> None:
        self.assertIn('if exist "app\\venv\\Scripts\\python.exe" goto existing_environment', LAUNCHER)

    def test_it_names_the_python_versions_when_there_is_none(self) -> None:
        """A tester with no Python at all gets a sentence, not a stack."""

        self.assertIn("3.11 to 3.13", LAUNCHER)
        self.assertIn("py -3", LAUNCHER)
        self.assertIn("PATH", LAUNCHER)

    def test_it_does_not_decide_which_torch_to_install(self) -> None:
        """That decision belongs to the vendored selector, which knows the
        non-CUDA platforms and the override."""

        self.assertNotIn("download.pytorch.org", LAUNCHER)
        self.assertNotIn("index-url", LAUNCHER)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_BOOTSTRAP_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
