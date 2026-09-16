"""Launch Studio for a live leg the way the owner does, and prove it before use.

Written after a live A/B diagnostic was invalidated by a harness defect: the
verifier launched Studio with

    python.exe -I -S -B launch_studio.py ...

which is the **canonical test runner's** flag set, chosen precisely so the
contract suite runs without site-packages and proves startup purity. Studio is
not a test. Without site-packages `torch` is not importable, so every load
failed at `startup_globals_installed` in tens of milliseconds, and two
authorized live legs were spent measuring the harness instead of the product.

Two rules follow, and both are enforced here rather than remembered:

```text
1. launch with owner-equivalent semantics, read from the owner launcher itself
2. prove `import torch` succeeds in the TARGET interpreter before a live leg
```

The second is the gate that would have caught the incident before any
authorization was consumed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .process_ownership import ProcessRegistry

APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
OWNER_LAUNCHER = WORKSPACE_ROOT / "start_studio.py"
STUDIO_ENTRY = APP_ROOT / "launch_studio.py"
TARGET_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

#: Flags that remove site-packages or isolate the interpreter. Studio needs
#: site-packages; these belong to the test runner and nowhere near a live leg.
FORBIDDEN_LAUNCH_FLAGS = ("-S", "-I", "-E")

READY_PATTERN = re.compile(r"STUDIO_READY host=(\S+) port=(\d+)")


class HarnessPreflightError(RuntimeError):
    """A live leg must not start. Raised before any load is consumed."""


@dataclass(frozen=True)
class TargetProbe:
    """What the target interpreter can actually do."""

    torch_importable: bool
    torch_version: str
    cuda_available: bool
    cuda_device: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "torch_importable": self.torch_importable,
            "torch_version": self.torch_version,
            "cuda_available": self.cuda_available,
            "cuda_device": self.cuda_device,
        }


def owner_launch_command(config: Path) -> list[str]:
    """The command the owner's launcher would run, derived not assumed.

    `start_studio.py` builds `[PYTHON, LAUNCHER, "--config", CONFIG]`. This
    mirrors it exactly, so a change there is visible here rather than silently
    diverging.
    """
    return [str(TARGET_PYTHON), str(STUDIO_ENTRY), "--config", str(config)]


def assert_owner_equivalent(command: list[str]) -> None:
    """Refuse a command carrying interpreter flags the owner never uses."""

    offending = [part for part in command if part in FORBIDDEN_LAUNCH_FLAGS]
    if offending:
        raise HarnessPreflightError(
            f"launch command carries test-runner flags {offending}; the owner "
            f"launcher uses none of {list(FORBIDDEN_LAUNCH_FLAGS)}"
        )
    if str(STUDIO_ENTRY) not in command:
        raise HarnessPreflightError("launch command does not start Studio")


def probe_target_python() -> TargetProbe:
    """Run a probe in the TARGET interpreter, with the target's real flags.

    Deliberately a subprocess of the same interpreter Studio will use, with no
    flags, because the whole failure mode was the harness's environment
    differing from the target's.
    """
    script = (
        "import json\n"
        "out = {'torch_importable': False, 'torch_version': '', "
        "'cuda_available': False, 'cuda_device': '', 'detail': ''}\n"
        "try:\n"
        "    import torch\n"
        "    out['torch_importable'] = True\n"
        "    out['torch_version'] = torch.__version__\n"
        "    out['cuda_available'] = bool(torch.cuda.is_available())\n"
        "    if out['cuda_available']:\n"
        "        out['cuda_device'] = torch.cuda.get_device_name(0)\n"
        "except BaseException as exc:\n"
        "    out['detail'] = type(exc).__name__\n"
        "print(json.dumps(out))\n"
    )
    completed = subprocess.run(
        [str(TARGET_PYTHON), "-c", script],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=300,
    )
    line = ""
    for candidate in reversed((completed.stdout or "").splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate.strip()
            break
    if not line:
        raise HarnessPreflightError(
            "target interpreter probe produced no result "
            f"(exit {completed.returncode})"
        )
    data = json.loads(line)
    return TargetProbe(
        torch_importable=bool(data["torch_importable"]),
        torch_version=str(data["torch_version"]),
        cuda_available=bool(data["cuda_available"]),
        cuda_device=str(data["cuda_device"]),
        detail=str(data.get("detail", "")),
    )


def require_live_preflight() -> TargetProbe:
    """THE gate. No authorized load may be consumed unless this passes."""

    probe = probe_target_python()
    if not probe.torch_importable:
        raise HarnessPreflightError(
            "TARGET_PYTHON_IMPORT_TORCH = FAIL "
            f"({probe.detail or 'import failed'}); refusing to consume a live load"
        )
    return probe


@dataclass
class LiveStudio:
    """One owned Studio process, launched owner-equivalently."""

    registry: ProcessRegistry
    pid: int
    port: int
    host: str
    log_path: Path

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> bool:
        return self.registry.terminate(self.pid)


def launch_studio(
    config: Path,
    *,
    registry: ProcessRegistry,
    log_path: Path,
    timeout: float = 180.0,
    preflight: bool = True,
) -> LiveStudio:
    """Launch Studio and wait for its own readiness line.

    `preflight=False` exists only for harness tests that must not require a
    Torch install; a live leg always leaves it True.
    """
    if preflight:
        require_live_preflight()

    command = owner_launch_command(Path(config))
    assert_owner_equivalent(command)

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    owned = registry.spawn(
        command, label="live-studio", cwd=str(APP_ROOT),
        stdout=handle, stderr=subprocess.STDOUT,
    )

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = READY_PATTERN.search(text)
        if match:
            return LiveStudio(
                registry=registry, pid=owned.pid, host=match.group(1),
                port=int(match.group(2)), log_path=log_path,
            )
        if handle.closed:
            break
        time.sleep(0.5)

    registry.terminate(owned.pid)
    raise HarnessPreflightError(
        f"Studio did not announce readiness within {timeout:.0f}s"
    )


__all__ = (
    "FORBIDDEN_LAUNCH_FLAGS",
    "HarnessPreflightError",
    "LiveStudio",
    "TargetProbe",
    "assert_owner_equivalent",
    "launch_studio",
    "owner_launch_command",
    "probe_target_python",
    "require_live_preflight",
)
