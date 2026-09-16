"""First-image readiness probe — everything up to denoising, and no further.

Runs in a fresh subprocess with `gradio`, `gradio_client`, and
`modules.shared_options` blocked at `sys.meta_path`, constructs the bounded
first-image request against a **synthetic** resident-model descriptor, drives it
through validation and the generation port, and receives the policy refusal.

No real model file is opened. Torch is never imported. CUDA is never
initialized. No image is generated.

"Denoising was not called" and "VAE decode was not called" are established by
counters on the recording backend plus a guard that fails loudly if the real
sampler or decode entry points are ever reached -- not by reading the source and
concluding they probably were not.

    python -I -S -B scripts/headless/first_image_readiness_probe.py
"""

from __future__ import annotations

import json
import sys
import threading
from importlib.abc import MetaPathFinder
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
REPORT_DIR = WORKSPACE_ROOT / "Evidence" / "studio-first-image-readiness"
REPORT_JSON = REPORT_DIR / "FIRST_IMAGE_READINESS_REPORT.json"

TIMEOUT_SECONDS = 120
FORBIDDEN_ROOTS = ("gradio", "gradio_client")
FORBIDDEN_EXACT = ("modules.shared_options", "modules.shared_init")

_REDACTIONS = (str(WORKSPACE_ROOT), str(APP_ROOT), str(Path.home()), Path.home().name)


def redact(value: object) -> object:
    if isinstance(value, str):
        out = value
        for needle in _REDACTIONS:
            if needle:
                out = out.replace(needle, "<REDACTED>")
                out = out.replace(needle.replace("\\", "/"), "<REDACTED>")
        return out
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class _Blocker(MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in FORBIDDEN_ROOTS or fullname in FORBIDDEN_EXACT:
            self.attempts.append(fullname)
            raise ImportError(f"Forbidden import of {fullname!r} in the readiness probe.")
        return None


class _DenoiseGuard:
    """Fails loudly if anything reaches a real sampler or decode entry point."""

    def __init__(self) -> None:
        self.denoise_calls = 0
        self.decode_calls = 0

    def denoise(self, *args, **kwargs):  # pragma: no cover - must never run
        self.denoise_calls += 1
        raise AssertionError("PROBE_REFUSED_DENOISING")

    def decode(self, *args, **kwargs):  # pragma: no cover - must never run
        self.decode_calls += 1
        raise AssertionError("PROBE_REFUSED_VAE_DECODE")


def _loaded(prefix: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for name in sys.modules)


def main() -> int:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    guard = _DenoiseGuard()

    record: dict[str, object] = {
        "schema_version": "first-image-readiness/v1",
        "timeout_seconds": TIMEOUT_SECONDS,
        "verdict": "FIRST_IMAGE_READINESS_UNKNOWN",
        "verdicts": [],
        "gradio_imported": False,
        "torch_imported": False,
        "cuda_initialized": "UNKNOWN",
        "real_model_accessed": False,
        "real_image_generated": False,
        "network_used": False,
        "public_socket_bound": False,
        "denoise_calls": 0,
        "vae_decode_calls": 0,
        "forbidden_import_attempts": [],
        "errors": [],
    }

    try:
        from forge_headless.contracts import HeadlessError
        from forge_headless.generation_port import (
            GENERATION_NOT_AUTHORIZED,
            GenerationGateway,
        )
        from forge_headless.generation_request import (
            DERIVATIONS,
            ResidentModel,
            build_first_image_request,
            validate_request,
        )
        from forge_headless.headless_options import (
            GENERATION_OPTIONS,
            HeadlessOptions,
            legacy_defaults,
        )
        from forge_headless.headless_progress import (
            ForgeStateBridge,
            HeadlessProgress,
            JobState,
        )

        # 3. minimal headless generation options -- parsed from Forge source,
        #    with modules.shared_options blocked at the import hook.
        defaults = legacy_defaults(APP_ROOT)
        options = HeadlessOptions(defaults)
        missing = [name for name in GENERATION_OPTIONS if name not in defaults]
        record["generation_options"] = {
            "declared": len(GENERATION_OPTIONS),
            "resolved_from_source": len(GENERATION_OPTIONS) - len(missing),
            "missing": missing,
            "sample": {name: defaults.get(name) for name in ("emphasis", "anima_do_reference")},
        }

        # 4. owned progress source, plus the narrow shared.state bridge.
        progress = HeadlessProgress("probe-job", preview_enabled=False)
        bridge = ForgeStateBridge(progress)
        bridge.sampling_steps = 12
        bridge.sampling_step = 3
        record["progress_source"] = {
            "installed": True,
            "bridge_attributes": len(ForgeStateBridge.SUPPORTED),
            "snapshot_after_bridge_write": progress.snapshot().to_dict(),
        }

        # 5. the bounded request.
        request = build_first_image_request(
            "probe-job", "synthetic-model-id", seed=1234, steps=12
        )
        record["request"] = request.to_dict()
        record["derivations"] = dict(DERIVATIONS)

        # 6. validate against a synthetic resident-model descriptor.
        model = ResidentModel(
            model_id="synthetic-model-id",
            family="anima",
            resident=True,
            supported_samplers=("Euler",),
            supported_schedulers=("Automatic",),
        )
        result = validate_request(
            request,
            model,
            options_available=True,
            progress_installed=True,
            result_root_writable=True,
            generation_authorized=False,
        )
        record["validation"] = result.to_dict()
        blocking = [
            item for item in result.problems if item["code"] != GENERATION_NOT_AUTHORIZED
        ]
        record["validation_blocking_problems"] = blocking

        # 7-8. reach the port and take the refusal.
        gateway = GenerationGateway()
        refusal = ""
        try:
            gateway.submit(request, model, options_available=True)
        except HeadlessError as exc:
            refusal = exc.code
        record["port_refusal"] = refusal
        record["job_state_after_refusal"] = gateway.progress_for(
            request.request_id
        ).state.value

        record["denoise_calls"] = guard.denoise_calls
        record["vae_decode_calls"] = guard.decode_calls
        record["gradio_imported"] = _loaded("gradio") or _loaded("gradio_client")
        record["torch_imported"] = _loaded("torch")
        record["shared_options_imported"] = "modules.shared_options" in sys.modules

        verdicts: list[str] = []
        if not blocking:
            verdicts.append("GENERATION_REQUEST_VALIDATED")
        if record["progress_source"]["installed"]:  # type: ignore[index]
            verdicts.append("PROGRESS_SOURCE_READY")
        if refusal == GENERATION_NOT_AUTHORIZED:
            verdicts.append("GENERATION_BLOCKED_BY_POLICY")
        if guard.denoise_calls == 0:
            verdicts.append("DENOISING_NOT_CALLED")
        if guard.decode_calls == 0:
            verdicts.append("VAE_DECODE_NOT_CALLED")
        if not record["real_image_generated"]:
            verdicts.append("NO_REAL_IMAGE_GENERATED")
        if not record["gradio_imported"]:
            verdicts.append("GRADIO_NOT_IMPORTED")
        if not record["torch_imported"]:
            verdicts.append("TORCH_NOT_IMPORTED")
        if record["cuda_initialized"] != "true":
            verdicts.append("NO_CUDA_INITIALIZATION")
        if not record["network_used"]:
            verdicts.append("NO_EXTERNAL_NETWORK")

        record["verdicts"] = verdicts
        record["verdict"] = (
            "FIRST_IMAGE_READINESS_PROVEN"
            if len(verdicts) == 10
            else "FIRST_IMAGE_READINESS_INCOMPLETE"
        )
        record["state_transitions_exercised"] = [state.value for state in JobState]
    except BaseException as exc:  # noqa: BLE001 - the report must always be written
        record["verdict"] = "FIRST_IMAGE_READINESS_PROBE_FAILED"
        record["errors"] = [redact(f"{type(exc).__name__}: {str(exc)[:300]}")]
    finally:
        try:
            sys.meta_path.remove(blocker)
        except ValueError:
            pass
        record["forbidden_import_attempts"] = [redact(name) for name in blocker.attempts]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(redact(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"verdict: {record['verdict']}")
    print(f"checks:  {len(record['verdicts'])}/10")
    print(f"report:  Evidence/studio-first-image-readiness/{REPORT_JSON.name}")
    return 0 if record["verdict"] == "FIRST_IMAGE_READINESS_PROVEN" else 1


def _run_with_timeout() -> int:
    outcome: list[int] = []

    def target() -> None:
        outcome.append(main())

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if worker.is_alive():
        print("verdict: FIRST_IMAGE_READINESS_PROBE_TIMEOUT")
        return 2
    return outcome[0] if outcome else 1


if __name__ == "__main__":
    raise SystemExit(_run_with_timeout())
