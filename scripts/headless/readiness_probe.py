"""Isolated headless readiness probe.

Not a UI launch and not a generation test. Runs the headless facade in a fresh
subprocess with `gradio` and `gradio_client` blocked at `sys.meta_path`, then
writes one redacted JSON report.

Deliberate non-actions:

    - never imports gradio or gradio_client        (blocked, not merely avoided)
    - never imports torch
    - never calls a device-allocation, memory-probing, or driver-init function
    - never loads or scans a model
    - never generates
    - never binds a socket
    - never touches the network

Reports `cuda_initialized` only when that can be read without causing it: with
Torch absent the honest answer is UNKNOWN, not False.

    python -I -S -B scripts/headless/readiness_probe.py
"""

from __future__ import annotations

import json
import sys
import threading
from importlib.abc import MetaPathFinder
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
REPORT_DIR = WORKSPACE_ROOT / "Evidence" / "studio-headless-forge"
REPORT_JSON = REPORT_DIR / "HEADLESS_READINESS_REPORT.json"

TIMEOUT_SECONDS = 120
FORBIDDEN_ROOTS = ("gradio", "gradio_client")

_REDACTIONS = (
    str(WORKSPACE_ROOT),
    str(APP_ROOT),
    str(Path.home()),
)


def redact(value: str) -> str:
    """Remove absolute paths and the home directory from any reported text."""

    out = str(value)
    for needle in _REDACTIONS:
        if needle:
            out = out.replace(needle, "<REDACTED>")
            out = out.replace(needle.replace("\\", "/"), "<REDACTED>")
    return out


class _Blocker(MetaPathFinder):
    """Refuse forbidden imports before any real finder sees them."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in FORBIDDEN_ROOTS:
            self.attempts.append(fullname)
            raise ImportError(
                f"Forbidden import of {fullname!r} in the headless probe."
            )
        return None


def _loaded(prefix: str) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".") for name in sys.modules
    )


def _cuda_initialized() -> str:
    """Read the CUDA flag only if Torch is already present. Never imports it."""

    torch = sys.modules.get("torch")
    if torch is None:
        return "UNKNOWN"
    try:
        return "true" if torch.cuda.is_initialized() else "false"
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def main() -> int:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)

    record: dict[str, object] = {
        "schema_version": "headless-readiness-report/v1",
        "timeout_seconds": TIMEOUT_SECONDS,
        "verdict": "HEADLESS_READINESS_UNKNOWN",
        "gradio_imported": False,
        "gradio_client_imported": False,
        "torch_imported": False,
        "cuda_initialized": "UNKNOWN",
        "model_loaded": False,
        "generation_performed": False,
        "network_used": False,
        "public_socket_bound": False,
        "forbidden_import_attempts": [],
        "errors": [],
    }

    try:
        from forge_headless import ForgeHeadlessRuntime

        runtime = ForgeHeadlessRuntime.construct(repository_root=APP_ROOT)
        readiness = runtime.probe_readiness()
        identity = runtime.get_runtime_identity()
        capability = runtime.get_runtime_capability()

        record["readiness"] = readiness.to_dict()
        record["identity"] = identity.to_dict()
        record["capability"] = capability.to_dict()
        record["state_after_probe"] = runtime.state.value

        runtime.shutdown()
        record["state_after_shutdown"] = runtime.state.value

        record["gradio_imported"] = _loaded("gradio")
        record["gradio_client_imported"] = _loaded("gradio_client")
        record["torch_imported"] = _loaded("torch")
        record["cuda_initialized"] = _cuda_initialized()

        if record["gradio_imported"] or record["gradio_client_imported"]:
            record["verdict"] = "HEADLESS_READINESS_BLOCKED_BY_GRADIO_IMPORT"
        elif record["cuda_initialized"] == "true":
            record["verdict"] = (
                "HEADLESS_READINESS_BLOCKED_BY_DEVICE_INITIALIZATION"
            )
        elif readiness.ready:
            record["verdict"] = "HEADLESS_READY_NO_MODEL"
        else:
            record["verdict"] = "HEADLESS_READINESS_BLOCKED"

        # Relevant imported modules only -- the retained-Forge and owned
        # packages. Reporting all of sys.modules would be noise.
        record["relevant_modules"] = sorted(
            name
            for name in sys.modules
            if name.split(".", 1)[0]
            in ("backend", "modules", "modules_forge", "forge_headless", "forge_studio")
        )
    except BaseException as exc:  # noqa: BLE001 - the report must always be written
        record["verdict"] = "HEADLESS_READINESS_PROBE_FAILED"
        record["errors"] = [redact(f"{type(exc).__name__}: {exc}")]
    finally:
        try:
            sys.meta_path.remove(blocker)
        except ValueError:
            pass
        record["forbidden_import_attempts"] = [
            redact(name) for name in blocker.attempts
        ]

    rendered = redact(json.dumps(record, indent=2, sort_keys=True))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(rendered + "\n", encoding="utf-8", newline="\n")

    print(f"verdict: {record['verdict']}")
    print(f"report:  Evidence/studio-headless-forge/{REPORT_JSON.name}")
    return 0 if record["verdict"] == "HEADLESS_READY_NO_MODEL" else 1


def _run_with_timeout() -> int:
    """Enforce the timeout without assuming a signal-capable platform."""

    outcome: list[int] = []

    def target() -> None:
        outcome.append(main())

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if worker.is_alive():
        print("verdict: HEADLESS_READINESS_PROBE_TIMEOUT")
        return 2
    return outcome[0] if outcome else 1


if __name__ == "__main__":
    raise SystemExit(_run_with_timeout())
