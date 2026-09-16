"""Create or repair the standalone environment before launching Studio.

Release launchers are tracked under packaging/windows. This module checks Torch
and installed requirements, repairing interrupted setup without replacing a
working venv. The platform selector supplies the existing Torch/CUDA defaults;
requirements.txt intentionally does not install an unspecified Torch build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

APP_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = APP_ROOT / "venv"
REQUIREMENTS = APP_ROOT / "requirements.txt"

#: The interpreter versions Studio is known to run on.
#:
#: A RANGE rather than a single pin: the vendored core supports several, and
#: refusing a working interpreter because it is not the one this was written on
#: is the same class of invented limit the AR6 sweep spent a day removing.
MINIMUM_PYTHON = (3, 11)
MAXIMUM_PYTHON = (3, 13)


class BootstrapError(RuntimeError):
    """Something the owner has to fix, phrased so they can."""


@dataclass(frozen=True)
class EnvironmentState:
    """What exists right now, before anything is done about it."""

    interpreter: Path | None
    torch_present: bool

    @property
    def complete(self) -> bool:
        return self.interpreter is not None and self.torch_present


def venv_interpreter(venv_dir: Path = VENV_DIR) -> Path:
    """Where the venv's interpreter lives on this platform."""

    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def inspect(venv_dir: Path = VENV_DIR,
            runner: Callable[..., subprocess.CompletedProcess] | None = None
            ) -> EnvironmentState:
    """What is already in place. Cheap, and touches no network.

    Called on EVERY launch, so the healthy answer has to be fast: one
    `exists()` and one in-process import check, and nothing else.
    """

    interpreter = venv_interpreter(venv_dir)
    if not interpreter.is_file():
        return EnvironmentState(interpreter=None, torch_present=False)

    run = runner or subprocess.run
    try:
        probe = run([str(interpreter), "-c", "import torch"],
                    capture_output=True, check=False)
    except OSError:
        # An interpreter that cannot be executed is not an interpreter.
        return EnvironmentState(interpreter=None, torch_present=False)
    return EnvironmentState(interpreter=interpreter,
                            torch_present=probe.returncode == 0)


def torch_install_command() -> list[str]:
    """The pip arguments that install the RIGHT torch for this machine.

    Delegated to the vendored selector rather than reproduced. It knows the
    CUDA index, the ROCm and CPU cases, and that `TORCH_COMMAND` is owner input
    to be used verbatim -- and reproducing any of that here would be a second
    answer to one question.
    """

    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))
    import platform

    from modules.platform_selection import select_torch_command

    selection = select_torch_command(system=platform.system(),
                                     machine=platform.machine())
    if not selection.supported:
        raise BootstrapError(
            "Studio has no Torch build for this platform.\n"
            f"  {selection.reason}\n"
            "Set TORCH_COMMAND to install one yourself.")
    # "pip install a b --extra-index-url c" -> ["install", "a", "b", ...]
    parts = selection.command.split()
    if parts[:1] == ["pip"]:
        parts = parts[1:]
    return parts


def _require_supported_interpreter() -> None:
    version = sys.version_info[:2]
    if not (MINIMUM_PYTHON <= version <= MAXIMUM_PYTHON):
        raise BootstrapError(
            f"Studio needs Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} "
            f"to {MAXIMUM_PYTHON[0]}.{MAXIMUM_PYTHON[1]}. "
            f"This is {version[0]}.{version[1]}, at {sys.executable}.\n"
            "Install a supported Python and launch again, or set PYTHON to "
            "one you already have.")


def find_supported_python(
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> str | None:
    """Ask Windows' launcher for an installed supported Python; never install it."""
    run = runner or subprocess.run
    env = os.environ.copy()
    env.pop("PYLAUNCHER_ALLOW_INSTALL", None)
    env.pop("PYLAUNCHER_ALWAYS_INSTALL", None)
    env["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false"
    probe = (
        "import json,sys; "
        "print(json.dumps({'version':list(sys.version_info[:2]),'executable':sys.executable}))"
    )
    for minor in range(MAXIMUM_PYTHON[1], MINIMUM_PYTHON[1] - 1, -1):
        try:
            result = run(["py", f"-3.{minor}", "-c", probe],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", check=False, timeout=10, env=env)
            if result.returncode != 0:
                continue
            found = json.loads(result.stdout)
            version = tuple(found["version"])
            executable = found["executable"]
            if (MINIMUM_PYTHON <= version <= MAXIMUM_PYTHON
                    and isinstance(executable, str) and executable.strip()):
                return executable
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            continue
    return None


def select_interpreter(argv: list[str]) -> int | None:
    """Re-execute setup when PATH picked an unsupported Python on Windows.

    None means the current interpreter is supported. An integer is the exit
    code of setup in the selected interpreter. Explicit choices and existing
    app environments are never silently replaced.
    """
    try:
        _require_supported_interpreter()
        return None
    except BootstrapError as original:
        if os.environ.get("PYTHON"):
            raise BootstrapError(
                f"{original}\nPYTHON is explicitly set. Update it to a supported "
                "executable, or clear it to enable automatic selection.") from original
        if venv_interpreter(VENV_DIR).is_file():
            raise BootstrapError(
                f"{original}\nThe existing app/venv has been preserved. "
                "Use a fresh extraction with supported Python to rebuild setup.") from original
        if sys.platform != "win32" or "--no-python-search" in argv:
            raise
        selected = find_supported_python()
        if selected is None:
            raise BootstrapError(
                f"{original}\nNo supported Python was found through the Windows py launcher. "
                "Install Python 3.13 with its launcher, then run Start-Studio.bat again.") from original
        print(f"PATH selected Python {sys.version_info[0]}.{sys.version_info[1]}; "
              f"using compatible Python at {selected}.", flush=True)
        try:
            result = subprocess.run(
                [selected, str(Path(__file__).resolve()), *argv, "--no-python-search"],
                check=False)
        except OSError as error:
            raise BootstrapError(f"Could not start selected Python: {error}") from error
        return result.returncode


def _run(command: Sequence[str], what: str,
         runner: Callable[..., subprocess.CompletedProcess]) -> None:
    print(f"  {what} ...", flush=True)
    result = runner(list(command), check=False)
    if result.returncode != 0:
        raise BootstrapError(
            f"{what} failed (exit {result.returncode}).\n"
            f"  {' '.join(command)}")


def requirements_satisfied(interpreter: Path, requirements: Path,
                           runner: Callable[..., subprocess.CompletedProcess]) -> bool:
    """Inspect installed metadata; never import detector libraries or use pip."""
    probe = """
import sys
from importlib import metadata
from pathlib import Path
from packaging.requirements import Requirement
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    requirement = Requirement(line)
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        version = metadata.version(requirement.name)
    except metadata.PackageNotFoundError:
        sys.exit(1)
    if version not in requirement.specifier:
        sys.exit(1)
"""
    try:
        result = runner([str(interpreter), "-c", probe, str(requirements)],
                        capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def ensure(venv_dir: Path = VENV_DIR,
           requirements: Path = REQUIREMENTS,
           runner: Callable[..., subprocess.CompletedProcess] | None = None
           ) -> bool:
    """Make the environment usable. Returns True if anything was installed.

    Idempotent by design: a complete environment costs one file check and one
    import probe, because this runs before every launch.
    """

    run = runner or subprocess.run
    state = inspect(venv_dir, runner=run)
    if not requirements.is_file():
        raise BootstrapError(f"Requirements file is missing: {requirements}")
    if state.complete and requirements_satisfied(state.interpreter, requirements, run):
        return False

    _require_supported_interpreter()
    print("Preparing the Studio environment. This happens once.", flush=True)

    interpreter = state.interpreter
    if interpreter is None:
        _run([sys.executable, "-m", "venv", str(venv_dir)],
             f"Creating {venv_dir.name}", run)
        interpreter = venv_interpreter(venv_dir)
        if not interpreter.is_file():
            raise BootstrapError(
                f"The environment was created but {interpreter} is not there.")
        # Best effort. An old pip still installs; it just warns more.
        try:
            _run([str(interpreter), "-m", "pip", "install", "--upgrade", "pip"],
                 "Updating pip", run)
        except BootstrapError:
            print("  (pip could not be updated; continuing)", flush=True)

    if not state.torch_present:
        _run([str(interpreter), "-m", "pip"] + torch_install_command(),
             "Installing Torch for your GPU", run)

    if requirements.is_file():
        _run([str(interpreter), "-m", "pip", "install", "-r", str(requirements)],
             "Installing the remaining packages", run)

    print("Environment ready.", flush=True)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        selected_exit = select_interpreter(argv)
        if selected_exit is not None:
            return selected_exit
        if "--check-python" in argv:
            print(f"Supported Python {sys.version_info[0]}.{sys.version_info[1]}: {sys.executable}")
            return 0
        changed = ensure()
    except BootstrapError as error:
        print(file=sys.stderr)
        print("Studio cannot start.", file=sys.stderr)
        print(file=sys.stderr)
        for line in str(error).splitlines():
            print(f"  {line}", file=sys.stderr)
        print(file=sys.stderr)
        return 1
    if "--quiet" not in argv and not changed:
        print("Environment already in place.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
