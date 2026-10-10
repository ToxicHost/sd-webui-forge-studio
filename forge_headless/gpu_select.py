"""Which GPU Studio runs on. G1.

Neo takes `--gpu-device-id N` and turns it into `CUDA_VISIBLE_DEVICES=N`
before torch loads (`modules_forge/initialization.py:31-33`). Studio never
runs that code and never passes the flag, so it always used the first card.
This is the same mechanism, driven by a Settings choice instead of a command
line:

  * `list_gpus()` asks `nvidia-smi`, which sees every card without starting
    CUDA in this process (torch, once loaded, sees only the visible ones);
  * `apply_choice(choice)` runs at start-up, BEFORE the first torch import,
    exactly where `runtime_options.apply_environment` sets the allocator.
    It also sets `CUDA_DEVICE_ORDER=PCI_BUS_ID`: CUDA otherwise orders cards
    fastest-first while `nvidia-smi` numbers them by bus, so "card 1" in the
    list could have meant a different card to CUDA.

A choice takes effect at the next start; a running engine cannot move its
models to another card. `CUDA_VISIBLE_DEVICES` already set in the environment
(Start-Studio.bat, the escape hatch) wins over Settings, and says so.

Source review: Evidence/source-review/G1-gpu-selector.md
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Callable

#: What `apply_choice` did at this start, for Settings to show.
STARTUP: dict[str, Any] = {"requested": "auto", "applied": None, "source": "default"}


def list_gpus(run: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]]:
    """`[{"index", "name", "memory_gb"}]`, in nvidia-smi's (bus) order. Never raises."""

    tool = shutil.which("nvidia-smi")
    if tool is None:
        return []
    try:
        done = run([tool, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                   capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(done, "returncode", 1) != 0:
        return []
    return parse_nvidia_smi(getattr(done, "stdout", "") or "")


def parse_nvidia_smi(text: str) -> list[dict[str, Any]]:
    gpus = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        try:
            memory_gb = round(float(parts[-1]) / 1024, 1)
        except ValueError:
            memory_gb = None
        gpus.append({"index": int(parts[0]), "name": ", ".join(parts[1:-1]), "memory_gb": memory_gb})
    return gpus


def apply_choice(choice: Any, environ: Any = None, modules: Any = None) -> dict[str, Any]:
    """Make the chosen card the only visible one. Before torch, or not at all."""

    environ = os.environ if environ is None else environ
    modules = sys.modules if modules is None else modules
    requested = str(choice or "auto").strip() or "auto"
    state = {"requested": requested, "applied": None, "source": "default"}
    if environ.get("CUDA_VISIBLE_DEVICES") not in (None, ""):
        state.update(applied=environ["CUDA_VISIBLE_DEVICES"], source="environment")
    elif requested != "auto" and requested.isdigit():
        if "torch" in modules:
            state["source"] = "too_late"
        else:
            environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
            environ["CUDA_VISIBLE_DEVICES"] = requested
            state.update(applied=requested, source="settings")
    STARTUP.clear()
    STARTUP.update(state)
    return dict(state)


def describe(selected: Any) -> dict[str, Any]:
    """The Settings view: the cards, the saved choice, and what is in use."""

    gpus = list_gpus()
    choice = str(selected or "auto").strip() or "auto"
    applied = STARTUP.get("applied")
    # Named only when Settings chose it. On Automatic, CUDA picks the card it
    # ranks fastest, which need not be nvidia-smi's index 0, so naming one
    # here would be a guess.
    in_use = None
    if STARTUP.get("source") == "settings" and applied is not None:
        in_use = next((g for g in gpus if str(g["index"]) == str(applied)), None)
    return {
        "gpus": gpus,
        "selected": choice,
        "in_use": in_use,
        "source": STARTUP.get("source", "default"),
        "restart_required": choice != STARTUP.get("requested", "auto"),
    }
