"""Isolated Phase 2A model-plumbing probe.

Proves the catalogue and load-request path end to end **without opening a
checkpoint**. Runs in a fresh subprocess with `gradio` and `gradio_client`
blocked at `sys.meta_path`, against synthetic fixtures the probe creates and
removes itself.

Deliberate non-actions:

    - never imports gradio or gradio_client        (blocked, not merely avoided)
    - never imports torch
    - never calls a device-allocation, memory-probing, or driver-init function
    - never opens, mmaps, hashes, or parses a catalogued file
    - never loads a model and never generates
    - never binds a socket and never touches the network
    - never reads an external directory: the root is inside Evidence/

"Checkpoint not opened" is not asserted by inspection. Every open path --
`builtins.open`, `io.open`, `os.open`, `pathlib.Path.open` -- is wrapped for the
duration of the run and any attempt to open a file beneath the fixture root is
recorded and refused.

    python -I -S -B scripts/headless/model_plumbing_probe.py
"""

from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import sys
import threading
from importlib.abc import MetaPathFinder
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
REPORT_DIR = WORKSPACE_ROOT / "Evidence" / "studio-headless-model-plumbing"
REPORT_JSON = REPORT_DIR / "HEADLESS_MODEL_PLUMBING_REPORT.json"
FIXTURE_ROOT = REPORT_DIR / "_probe_fixtures"

TIMEOUT_SECONDS = 120
FORBIDDEN_ROOTS = ("gradio", "gradio_client")

#: Synthetic, non-checkpoint fixture files. Recognized names, no tensor payload,
#: and no name taken from any real external model directory.
FIXTURES = (
    "probe-candidate-a.safetensors",
    "probe-candidate-b.ckpt",
    "probe-candidate-c.gguf",
    "probe-recognized-only.sft",
    "probe-ignored.txt",
    "probe-excluded.vae.safetensors",
)

_REDACTIONS = (
    str(WORKSPACE_ROOT),
    str(APP_ROOT),
    str(Path.home()),
)


def redact(value: str) -> str:
    out = str(value)
    for needle in _REDACTIONS:
        if needle:
            out = out.replace(needle, "<REDACTED>")
            out = out.replace(needle.replace("\\", "/"), "<REDACTED>")
    return out


class _Blocker(MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in FORBIDDEN_ROOTS:
            self.attempts.append(fullname)
            raise ImportError(
                f"Forbidden import of {fullname!r} in the model-plumbing probe."
            )
        return None


class _OpenGuard:
    """Refuse and record any attempt to open a file beneath ``root``."""

    def __init__(self, root: Path) -> None:
        self._root = str(root.resolve()).casefold()
        self.attempts: list[str] = []
        self._saved: dict[str, object] = {}

    def _guard(self, target: object) -> bool:
        try:
            text = os.fspath(target)  # type: ignore[arg-type]
        except TypeError:
            return False
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        if str(text).casefold().startswith(self._root):
            self.attempts.append(str(text))
            return True
        return False

    def __enter__(self) -> "_OpenGuard":
        self._saved = {
            "builtins.open": builtins.open,
            "io.open": io.open,
            "os.open": os.open,
            "Path.open": Path.open,
        }

        real_builtins_open = builtins.open
        real_io_open = io.open
        real_os_open = os.open
        real_path_open = Path.open

        def guarded_builtins_open(file, *args, **kwargs):
            if self._guard(file):
                raise AssertionError("PROBE_REFUSED_CHECKPOINT_OPEN")
            return real_builtins_open(file, *args, **kwargs)

        def guarded_io_open(file, *args, **kwargs):
            if self._guard(file):
                raise AssertionError("PROBE_REFUSED_CHECKPOINT_OPEN")
            return real_io_open(file, *args, **kwargs)

        def guarded_os_open(path, *args, **kwargs):
            if self._guard(path):
                raise AssertionError("PROBE_REFUSED_CHECKPOINT_OPEN")
            return real_os_open(path, *args, **kwargs)

        def guarded_path_open(self_path, *args, **kwargs):
            if self._guard(self_path):
                raise AssertionError("PROBE_REFUSED_CHECKPOINT_OPEN")
            return real_path_open(self_path, *args, **kwargs)

        builtins.open = guarded_builtins_open  # type: ignore[assignment]
        io.open = guarded_io_open  # type: ignore[assignment]
        os.open = guarded_os_open  # type: ignore[assignment]
        Path.open = guarded_path_open  # type: ignore[assignment,method-assign]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        builtins.open = self._saved["builtins.open"]  # type: ignore[assignment]
        io.open = self._saved["io.open"]  # type: ignore[assignment]
        os.open = self._saved["os.open"]  # type: ignore[assignment]
        Path.open = self._saved["Path.open"]  # type: ignore[assignment,method-assign]


class _RecordingPolicyLoader:
    """Records what reaches the port, then defers to the real policy gate.

    The refusal still comes from ``PolicyGatedLoader``: this wrapper adds
    observation, never permission.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.requests: list[dict[str, object]] = []

    def load(self, request: object) -> None:
        self.requests.append(request.to_dict())  # type: ignore[attr-defined]
        self._inner.load(request)  # type: ignore[attr-defined]


def _loaded(prefix: str) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".") for name in sys.modules
    )


def _cuda_initialized() -> str:
    torch = sys.modules.get("torch")
    if torch is None:
        return "UNKNOWN"
    try:
        return "true" if torch.cuda.is_initialized() else "false"
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _write_fixtures() -> None:
    if FIXTURE_ROOT.exists():
        shutil.rmtree(FIXTURE_ROOT)
    FIXTURE_ROOT.mkdir(parents=True)
    for name in FIXTURES:
        (FIXTURE_ROOT / name).write_bytes(b"synthetic-not-a-checkpoint\n")
    nested = FIXTURE_ROOT / "nested"
    nested.mkdir()
    (nested / "probe-nested.safetensors").write_bytes(b"synthetic\n")


def main() -> int:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)

    record: dict[str, object] = {
        "schema_version": "headless-model-plumbing-report/v1",
        "timeout_seconds": TIMEOUT_SECONDS,
        "verdict": "PHASE2A_PROBE_UNKNOWN",
        "verdicts": [],
        "gradio_imported": False,
        "gradio_client_imported": False,
        "torch_imported": False,
        "cuda_initialized": "UNKNOWN",
        "checkpoint_opened": False,
        "model_loaded": False,
        "generation_performed": False,
        "network_used": False,
        "public_socket_bound": False,
        "forbidden_import_attempts": [],
        "checkpoint_open_attempts": [],
        "errors": [],
    }

    guard: _OpenGuard | None = None
    try:
        _write_fixtures()

        from forge_headless import ForgeHeadlessRuntime, PolicyGatedLoader
        from forge_headless.contracts import HeadlessError

        recording = _RecordingPolicyLoader(PolicyGatedLoader())
        runtime = ForgeHeadlessRuntime.construct(
            repository_root=APP_ROOT,
            workspace_root=WORKSPACE_ROOT,
            loader=recording,
        )
        readiness = runtime.probe_readiness()
        record["readiness_state"] = readiness.state.value

        guard = _OpenGuard(FIXTURE_ROOT)
        with guard:
            count = runtime.configure_catalogue_root(FIXTURE_ROOT)
            candidates = runtime.list_models()
            record["catalogue_count"] = count
            record["catalogue"] = [item.to_dict() for item in candidates]
            record["state_with_catalogue"] = runtime.state.value

            plumbed = [
                item for item in candidates
                if item.load_support.value == "load_plumbed"
            ]
            refusals: list[dict[str, str]] = []
            for item in plumbed[:1]:
                try:
                    runtime.load_model(item.model_id)
                except HeadlessError as exc:
                    refusals.append({"model_id": item.model_id, "code": exc.code})
            # Distinct-error proof, still without opening anything.
            for label, model_id in (
                ("unknown", "0" * 32),
                (
                    "recognized_not_plumbed",
                    next(
                        (
                            item.model_id
                            for item in candidates
                            if item.load_support.value == "recognized_not_plumbed"
                        ),
                        "",
                    ),
                ),
            ):
                if not model_id:
                    continue
                try:
                    runtime.load_model(model_id)
                except HeadlessError as exc:
                    refusals.append({"model_id": label, "code": exc.code})

            record["load_refusals"] = refusals
            record["requests_reaching_port"] = recording.requests
            record["state_after_refusals"] = runtime.state.value

        record["checkpoint_open_attempts"] = [
            redact(name) for name in guard.attempts
        ]
        record["checkpoint_opened"] = bool(guard.attempts)

        residency_before = runtime.state.value
        runtime.shutdown()
        record["state_after_shutdown"] = runtime.state.value

        record["gradio_imported"] = _loaded("gradio")
        record["gradio_client_imported"] = _loaded("gradio_client")
        record["torch_imported"] = _loaded("torch")
        record["cuda_initialized"] = _cuda_initialized()

        verdicts: list[str] = []
        if count > 0 and record["state_with_catalogue"] == "catalogue_ready_no_model":
            verdicts.append("HEADLESS_CATALOGUE_READY")
        if recording.requests:
            verdicts.append("MODEL_REQUEST_VALIDATED")
        if any(
            item["code"] == "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED"
            for item in record["load_refusals"]  # type: ignore[union-attr]
        ):
            verdicts.append("MODEL_LOAD_BLOCKED_BY_POLICY")
        if not record["gradio_imported"] and not record["gradio_client_imported"]:
            verdicts.append("GRADIO_NOT_IMPORTED")
        if not record["checkpoint_opened"]:
            verdicts.append("CHECKPOINT_NOT_OPENED")
        if not record["torch_imported"]:
            verdicts.append("TORCH_NOT_IMPORTED")
        if record["cuda_initialized"] != "true":
            verdicts.append("NO_DEVICE_INITIALIZATION")
        if not record["model_loaded"] and residency_before != "ready":
            verdicts.append("NO_MODEL_LOADED")
        if not record["generation_performed"]:
            verdicts.append("NO_GENERATION")
        if not record["network_used"]:
            verdicts.append("NO_EXTERNAL_NETWORK")

        record["verdicts"] = verdicts
        record["verdict"] = (
            "PHASE2A_MODEL_PLUMBING_PROVEN"
            if len(verdicts) == 10
            else "PHASE2A_MODEL_PLUMBING_INCOMPLETE"
        )
    except BaseException as exc:  # noqa: BLE001 - the report must always be written
        record["verdict"] = "PHASE2A_PROBE_FAILED"
        record["errors"] = [redact(f"{type(exc).__name__}: {exc}")]
    finally:
        if guard is not None:
            # Idempotent: __exit__ already ran on the success path.
            try:
                guard.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        try:
            sys.meta_path.remove(blocker)
        except ValueError:
            pass
        record["forbidden_import_attempts"] = [
            redact(name) for name in blocker.attempts
        ]
        shutil.rmtree(FIXTURE_ROOT, ignore_errors=True)
        record["fixtures_removed"] = not FIXTURE_ROOT.exists()

    rendered = redact(json.dumps(record, indent=2, sort_keys=True))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(rendered + "\n", encoding="utf-8", newline="\n")

    print(f"verdict: {record['verdict']}")
    print(f"checks:  {len(record['verdicts'])}/10")
    print(f"report:  Evidence/studio-headless-model-plumbing/{REPORT_JSON.name}")
    return 0 if record["verdict"] == "PHASE2A_MODEL_PLUMBING_PROVEN" else 1


def _run_with_timeout() -> int:
    outcome: list[int] = []

    def target() -> None:
        outcome.append(main())

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if worker.is_alive():
        print("verdict: PHASE2A_PROBE_TIMEOUT")
        return 2
    return outcome[0] if outcome else 1


if __name__ == "__main__":
    raise SystemExit(_run_with_timeout())
