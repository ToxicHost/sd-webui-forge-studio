"""Own the processes you started, and only those.

Written because a live leg killed the owner's running Studio. The cleanup at the
time matched *every* `launch_studio` process, which is indistinguishable from
"kill anything that looks like the thing I started". A second failure came from
the same root: a rehearsal process survived a broad `pkill` and was still
running during a live attempt, so the "no other Studio" gate was not actually
clean.

The rule here is narrow and boring on purpose:

```text
spawn        record the PID and the run id that spawned it
terminate    only PIDs this registry spawned, one at a time
verify       confirm each exited
never        match by image name, command-line substring, or port
```

Nothing in this module can express "kill every launch_studio", which is the
point: the unsafe operation is not available rather than merely discouraged.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field


class ProcessOwnershipError(RuntimeError):
    """Refused because the process is not ours, or cannot be proven ours."""


@dataclass(frozen=True)
class OwnedProcess:
    """One process this registry started."""

    pid: int
    run_id: str
    #: What it was launched for, for evidence. Never used to select a target.
    label: str = ""


@dataclass
class ProcessRegistry:
    """Every process spawned under one run id, and the only things killable."""

    run_id: str
    _owned: dict[int, OwnedProcess] = field(default_factory=dict)
    _handles: dict[int, subprocess.Popen] = field(default_factory=dict)

    # -- spawn -------------------------------------------------------------

    def spawn(self, command: list[str], *, label: str = "", **kwargs) -> OwnedProcess:
        """Start a process and take ownership of exactly that PID."""

        handle = subprocess.Popen(command, **kwargs)
        owned = OwnedProcess(pid=handle.pid, run_id=self.run_id, label=label)
        self._owned[handle.pid] = owned
        self._handles[handle.pid] = handle
        return owned

    def adopt(self, handle: subprocess.Popen, *, label: str = "") -> OwnedProcess:
        """Take ownership of a handle this run created by other means."""

        owned = OwnedProcess(pid=handle.pid, run_id=self.run_id, label=label)
        self._owned[handle.pid] = owned
        self._handles[handle.pid] = handle
        return owned

    # -- reads -------------------------------------------------------------

    @property
    def owned(self) -> tuple[OwnedProcess, ...]:
        return tuple(self._owned.values())

    def owns(self, pid: int) -> bool:
        return pid in self._owned

    def live_owned(self) -> tuple[OwnedProcess, ...]:
        """Owned processes that are still running."""

        alive = []
        for pid, owned in self._owned.items():
            handle = self._handles.get(pid)
            if handle is not None and handle.poll() is None:
                alive.append(owned)
        return tuple(alive)

    # -- terminate ---------------------------------------------------------

    def terminate(self, pid: int, *, timeout: float = 10.0) -> bool:
        """Stop one owned process. Refuses anything this registry did not start.

        Returns True once the process is confirmed gone.
        """
        if pid not in self._owned:
            raise ProcessOwnershipError(
                f"pid {pid} was not started by run {self.run_id!r}; refusing"
            )
        handle = self._handles.get(pid)
        if handle is None:
            raise ProcessOwnershipError(f"no handle retained for pid {pid}")
        if handle.poll() is not None:
            return True

        handle.terminate()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if handle.poll() is not None:
                return True
            time.sleep(0.1)
        handle.kill()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if handle.poll() is not None:
                return True
            time.sleep(0.1)
        return False

    def terminate_all(self, *, timeout: float = 10.0) -> dict[int, bool]:
        """Stop every owned process. Touches nothing else."""

        return {
            pid: self.terminate(pid, timeout=timeout)
            for pid in list(self._owned)
        }

    def assert_none_running(self) -> None:
        """Fail loudly if anything this run started is still alive."""

        alive = self.live_owned()
        if alive:
            raise ProcessOwnershipError(
                "run "
                f"{self.run_id!r} still owns running processes: "
                f"{sorted(p.pid for p in alive)}"
            )


__all__ = (
    "OwnedProcess",
    "ProcessOwnershipError",
    "ProcessRegistry",
)
