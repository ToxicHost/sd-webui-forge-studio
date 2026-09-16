"""Owner-authorized Phase 2B controlled model-load probe.

Local invocation only. There is no HTTP path to this, no environment variable
that enables it, and nothing persists the authorization -- a restart begins
unauthorized.

    python scripts/headless/controlled_load_probe.py \
        --checkpoint <path> --text-encoder <path> --vae <path>

That form runs **preflight only**: exact-path validation plus header-only
safetensors inspection. It never opens a tensor payload, never imports Torch,
and never touches a device, so it does not consume the single authorized load
attempt.

Adding `--consume-single-load-attempt` performs the one authorized real load, in
a worker subprocess so the 10-minute timeout can be enforced by killing the
process rather than hoping a thread notices. The worker initializes CUDA, loads,
proves residency, unloads, and reports; the parent enforces the deadline.

No image is generated. Nothing in this file calls a sampler, a denoiser, or a
VAE decode.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
REPORT_DIR = WORKSPACE_ROOT / "Evidence" / "studio-controlled-model-load"

PREFLIGHT_JSON = REPORT_DIR / "MODEL_TRIPLET_PREFLIGHT.json"
LOAD_JSON = REPORT_DIR / "CONTROLLED_LOAD_RETRY_REPORT.json"
WORKER_RESULT = REPORT_DIR / "_worker_result.json"

TIMEOUT_SECONDS = 600
VRAM_CEILING_BYTES = 14 * 1024**3

_REDACTIONS = (
    str(WORKSPACE_ROOT),
    str(APP_ROOT),
    str(Path.home()),
    Path.home().name,
)


def redact(value: object) -> object:
    """Strip absolute paths and the user name from anything reported."""
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


def write_report(path: Path, record: dict[str, object]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(redact(record), indent=2, sort_keys=True)
    path.write_text(rendered + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- preflight


def run_preflight(authorization: object) -> dict[str, object]:
    """Exact-path validation plus header-only inspection. No tensor is read."""

    from forge_headless.contracts import HeadlessError
    from forge_headless.load_authorization import (
        ROLE_CHECKPOINT,
        ROLE_TEXT_ENCODER,
        ROLE_VAE,
    )
    from forge_headless.model_intake import (
        PREFLIGHT_INCONCLUSIVE,
        first_key_with,
        read_safetensors_header,
        summarize_header,
        validate_exact_path,
    )
    from forge_headless.triplet_preflight import (
        evaluate_triplet,
        inconclusive,
        inspect_checkpoint,
        inspect_text_encoder,
        inspect_vae,
    )

    record: dict[str, object] = {
        "schema_version": "controlled-load-preflight/v1",
        "authorization": authorization.to_public_dict(),
        "parent_directory_listed": False,
        "tensor_payload_read": False,
        "torch_imported": "torch" in sys.modules,
        "intake": [],
        "headers": [],
    }

    intakes = {}
    headers = {}
    try:
        for role in (ROLE_CHECKPOINT, ROLE_TEXT_ENCODER, ROLE_VAE):
            intake = validate_exact_path(authorization, role)
            intakes[role] = intake
            record["intake"].append(intake.to_dict())  # type: ignore[union-attr]

        for role in (ROLE_CHECKPOINT, ROLE_TEXT_ENCODER, ROLE_VAE):
            entry = authorization.file_for(role)
            header = read_safetensors_header(entry.path)
            headers[role] = header
            probe_keys = tuple(
                key
                for key in (
                    first_key_with(header, "x_embedder.proj.1.weight"),
                    first_key_with(header, "post_attention_layernorm.weight"),
                    first_key_with(header, "decoder.conv_in.weight"),
                )
                if key
            )
            record["headers"].append(  # type: ignore[union-attr]
                summarize_header(
                    role, entry.file_id, header, probe_keys=probe_keys
                ).to_dict()
            )
    except HeadlessError as exc:
        verdict = inconclusive(f"{exc.code}: {exc.message}", [])
        record["preflight"] = verdict.to_dict()
        record["verdict"] = PREFLIGHT_INCONCLUSIVE
        return record

    record["declared_components"] = _declared_components()
    verdict = evaluate_triplet(
        inspect_checkpoint(intakes["checkpoint"].file_id, headers["checkpoint"]),
        inspect_text_encoder(
            intakes["text_encoder"].file_id, headers["text_encoder"]
        ),
        inspect_vae(intakes["vae"].file_id, headers["vae"]),
    )
    record["preflight"] = verdict.to_dict()
    record["verdict"] = verdict.verdict
    record["torch_imported"] = "torch" in sys.modules
    return record


def _declared_components() -> dict[str, str]:
    """Read the bundled Anima `model_index.json`. Plain JSON: no Torch, no network."""

    index = (
        APP_ROOT
        / "backend"
        / "huggingface"
        / "circlestone-labs"
        / "Anima"
        / "model_index.json"
    )
    try:
        config = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        name: f"{value[0]}.{value[1]}"
        for name, value in config.items()
        if isinstance(value, list) and len(value) == 2
    }


# ------------------------------------------------------------------ parent


def run_worker(authorization: object, *, timeout: int) -> dict[str, object]:
    """Run the one real load in a killable worker and enforce the deadline."""

    from forge_headless.load_authorization import (
        ROLE_CHECKPOINT,
        ROLE_TEXT_ENCODER,
        ROLE_VAE,
    )

    WORKER_RESULT.unlink(missing_ok=True)
    argv = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
        "--checkpoint",
        str(authorization.file_for(ROLE_CHECKPOINT).path),
        "--text-encoder",
        str(authorization.file_for(ROLE_TEXT_ENCODER).path),
        "--vae",
        str(authorization.file_for(ROLE_VAE).path),
        "--authorization-id",
        authorization.authorization_id,
    ]
    environment = dict(os.environ)
    # The worker must not reach a network even by accident.
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["HF_DATASETS_OFFLINE"] = "1"

    timed_out = False
    returncode: int | None = None
    stderr_tail = ""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
        returncode = completed.returncode
        stderr_tail = completed.stderr[-4000:]
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        stderr_tail = (expired.stderr or b"").decode("utf-8", "replace")[-4000:] if isinstance(expired.stderr, bytes) else str(expired.stderr or "")[-4000:]

    worker_record: dict[str, object] = {}
    if WORKER_RESULT.is_file():
        try:
            worker_record = json.loads(WORKER_RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            worker_record = {}
    WORKER_RESULT.unlink(missing_ok=True)

    return {
        "worker_timed_out": timed_out,
        "worker_returncode": returncode,
        "worker_exited": returncode is not None,
        "worker_stderr_tail": stderr_tail,
        "worker": worker_record,
    }


# ------------------------------------------------------------------ worker


def worker_main(args: argparse.Namespace) -> int:
    """The killable half: options bootstrap, CUDA init, one load, unload.

    Every measurement is taken in guaranteed terminal handling, so success,
    exception, and timeout all produce the same telemetry. The first Phase 2B
    attempt recorded peaks only on the success path and lost them to an
    exception; that cannot recur here.
    """

    from forge_headless.contracts import HeadlessError
    from forge_headless.controlled_device import initialize_cuda, release_cuda_cache
    from forge_headless.headless_options import headless_options
    from forge_headless.load_authorization import ControlledLoadAuthorization
    from forge_headless.load_telemetry import (
        PayloadWatch,
        StageTimer,
        VramTelemetry,
        reset_peak,
        sample_current,
        sample_peak,
    )

    timer = StageTimer()
    telemetry = VramTelemetry(ceiling_bytes=VRAM_CEILING_BYTES)
    record: dict[str, object] = {
        "stage": "start",
        "states": [],
        "cuda_initialized": False,
        "torch_imported": False,
        "model_constructed": False,
        "image_generated": False,
        "network_used": False,
        "attempt_consumed": False,
        "options_bootstrap": {},
        "errors": [],
    }

    def note(state: str) -> None:
        record["states"].append(state)  # type: ignore[union-attr]

    engine = None
    watch: PayloadWatch | None = None
    options = None
    try:
        timer.start("authorization_validation")
        authorization = ControlledLoadAuthorization(
            {
                "checkpoint": args.checkpoint,
                "text_encoder": args.text_encoder,
                "vae": args.vae,
            },
            timeout_seconds=TIMEOUT_SECONDS,
            vram_ceiling_bytes=VRAM_CEILING_BYTES,
        )
        for role in ("checkpoint", "text_encoder", "vae"):
            _validate(authorization, role)
        timer.stop("authorization_validation")
        note("CATALOGUE_READY_NO_MODEL")

        note("MODEL_LOAD_PREFLIGHT")
        timer.start("header_preflight")
        record["preflight_reconfirmed"] = _reconfirm_preflight(authorization)
        timer.stop("header_preflight")
        if record["preflight_reconfirmed"] != "MODEL_TRIPLET_PREFLIGHT_COMPATIBLE":
            raise HeadlessError(
                "CONTROLLED_LOAD_PREFLIGHT_NOT_COMPATIBLE",
                "Preflight no longer reports the triplet as compatible.",
            )
        if authorization.consumed:
            raise HeadlessError(
                "CONTROLLED_LOAD_ALREADY_ATTEMPTED",
                "The authorized attempt has already been used.",
            )

        # Forge's vendored packages, exactly as modules_forge/initialization.py:27
        # sets them up. Without this the retained loader is not importable at all.
        packages = str(APP_ROOT / "modules_forge" / "packages")
        if packages not in sys.path:
            sys.path.insert(0, packages)
        sys.argv = [sys.argv[0]]

        # Options must be installed BEFORE backend.loader is imported:
        # backend/text_processing/anima_engine.py:11 does
        # `from modules.shared import opts`, binding the value at import time,
        # so a later assignment would leave that module holding the old None.
        timer.start("options_bootstrap")
        with headless_options(APP_ROOT) as options:
            timer.stop("options_bootstrap")
            record["options_bootstrap"] = {
                "installed": True,
                "source": "modules/shared_options.py (parsed, never imported)",
                "shared_options_imported": "modules.shared_options" in sys.modules,
                "shared_init_imported": "modules.shared_init" in sys.modules,
            }

            timer.start("cuda_initialization")
            device = initialize_cuda(authorization)
            timer.stop("cuda_initialization")
            record["device"] = device.to_dict()
            record["cuda_initialized"] = True
            record["torch_imported"] = True

            telemetry.before_load = sample_current()
            reset_peak()

            watch = PayloadWatch(
                {
                    authorization.loader_path(role): role
                    for role in ("checkpoint", "text_encoder", "vae")
                },
                timer,
            )

            def consume_once() -> None:
                authorization.consume()
                record["attempt_consumed"] = True

            watch.on_first_open = consume_once
            watch.install()

            note("MODEL_LOADING")
            record["stage"] = "loading"
            timer.start("engine_construction")
            from backend.loader import forge_loader

            engine = forge_loader(
                authorization.loader_path("checkpoint"),
                additional_state_dicts=authorization.loader_paths(
                    "text_encoder", "vae"
                ),
            )
            timer.stop("engine_construction")

            record["model_constructed"] = engine is not None
            note("MODEL_READY")
            record["stage"] = "ready"

            objects = getattr(engine, "forge_objects", None)
            record["residency"] = {
                "engine_class": type(engine).__name__,
                "unet_present": getattr(objects, "unet", None) is not None,
                "clip_present": getattr(objects, "clip", None) is not None,
                "vae_present": getattr(objects, "vae", None) is not None,
                "is_wan": bool(getattr(engine, "is_wan", False)),
            }
            record["model_dtype"] = _describe_dtype(engine)
    except HeadlessError as exc:
        record["errors"].append({"code": exc.code, "message": exc.message})  # type: ignore[union-attr]
        record["stage"] = "failed"
        note("MODEL_LOAD_FAILED")
    except BaseException as exc:  # noqa: BLE001 - the report must always be written
        record["errors"].append(  # type: ignore[union-attr]
            {
                "code": "CONTROLLED_LOAD_EXCEPTION",
                "message": f"{type(exc).__name__}: {str(exc)[:400]}",
            }
        )
        record["stage"] = "failed"
        note("MODEL_LOAD_FAILED")
    finally:
        # Guaranteed terminal handling. Order matters: peaks, then owned
        # release, then the sample that distinguishes a leak from a warm cache,
        # then the optional cache clear reported under its own key.
        telemetry.peak = sample_peak()
        timer.start("cleanup")
        note("MODEL_UNLOADING")
        if watch is not None:
            watch.restore()
            record["payload_access"] = watch.to_dict()
        record["cleanup"] = _release(engine)
        engine = None
        telemetry.after_release = sample_current()
        try:
            release_cuda_cache()
        except BaseException:  # noqa: BLE001
            pass
        telemetry.after_cache_clear = sample_current()
        timer.stop("cleanup")
        note("CATALOGUE_READY_NO_MODEL")

        if telemetry.peak is not None:
            telemetry.ceiling_exceeded = (
                telemetry.peak.allocated > VRAM_CEILING_BYTES
                or telemetry.peak.reserved > VRAM_CEILING_BYTES
            )
        else:
            telemetry.notes.append("no CUDA present when telemetry was collected")
        if options is not None:
            record["options_inventory"] = options.inventory()
            record["options_missing"] = list(options.missing)
        record["vram"] = telemetry.to_dict()
        record["stage_timings"] = timer.to_dict()
        record["gradio_imported"] = any(
            name.split(".", 1)[0] in ("gradio", "gradio_client")
            for name in sys.modules
        )
        record["shared_options_imported"] = "modules.shared_options" in sys.modules
        record["shared_init_imported"] = "modules.shared_init" in sys.modules
        record["torch_imported"] = "torch" in sys.modules
        record["options_restored"] = _options_restored()
        WORKER_RESULT.parent.mkdir(parents=True, exist_ok=True)
        WORKER_RESULT.write_text(
            json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0 if record["stage"] == "ready" else 1


def _release(engine: object) -> dict[str, object]:
    """Drop owned references explicitly, before any cache clearing.

    Reported separately from the cache clear on purpose: emptying the cache
    first would make a genuine leak look identical to a clean release.
    """

    import gc

    released: dict[str, object] = {
        "engine_reference_dropped": engine is not None,
        "forge_objects_cleared": False,
        "gc_collected": 0,
    }
    if engine is None:
        released["gc_collected"] = int(gc.collect())
        return released
    objects = getattr(engine, "forge_objects", None)
    for attribute in ("unet", "clip", "vae", "clipvision"):
        if objects is not None and hasattr(objects, attribute):
            try:
                setattr(objects, attribute, None)
            except Exception:  # noqa: BLE001
                pass
    for attribute in (
        "forge_objects",
        "forge_objects_original",
        "forge_objects_after_applying_lora",
        "text_processing_engine_anima",
    ):
        if hasattr(engine, attribute):
            try:
                setattr(engine, attribute, None)
            except Exception:  # noqa: BLE001
                pass
    released["forge_objects_cleared"] = True
    released["gc_collected"] = int(gc.collect())
    return released


def _validate(authorization: object, role: str) -> None:
    from forge_headless.model_intake import validate_exact_path

    validate_exact_path(authorization, role)  # type: ignore[arg-type]


def _reconfirm_preflight(authorization: object) -> str:
    """Re-run the header-only preflight inside the worker, before loading."""

    from forge_headless.model_intake import read_safetensors_header
    from forge_headless.triplet_preflight import (
        evaluate_triplet,
        inspect_checkpoint,
        inspect_text_encoder,
        inspect_vae,
    )

    headers = {
        role: read_safetensors_header(authorization.file_for(role).path)  # type: ignore[attr-defined]
        for role in ("checkpoint", "text_encoder", "vae")
    }
    verdict = evaluate_triplet(
        inspect_checkpoint("c", headers["checkpoint"]),
        inspect_text_encoder("t", headers["text_encoder"]),
        inspect_vae("v", headers["vae"]),
    )
    return verdict.verdict


def _options_restored() -> bool:
    """Whether `modules.shared.opts` is back to a non-headless value."""
    shared = sys.modules.get("modules.shared")
    if shared is None:
        return True
    return type(getattr(shared, "opts", None)).__name__ != "HeadlessOptions"


def _describe_dtype(engine: object) -> str:
    """Report the model dtype without importing Torch in this frame."""
    objects = getattr(engine, "forge_objects", None)
    unet = getattr(objects, "unet", None)
    for attribute in ("dtype", "model_dtype"):
        value = getattr(unet, attribute, None)
        if callable(value):
            try:
                value = value()
            except BaseException:  # noqa: BLE001
                value = None
        if value is not None:
            return str(value)
    dtype = getattr(getattr(unet, "model", None), "dtype", None)
    return str(dtype) if dtype is not None else "UNKNOWN"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--text-encoder", required=True)
    parser.add_argument("--vae", required=True)
    parser.add_argument(
        "--consume-single-load-attempt",
        action="store_true",
        help="Perform the one authorized real load. Preflight-only without it.",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--authorization-id", default="", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str]) -> int:
    if str(APP_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_ROOT))

    args = build_parser().parse_args(argv)
    if args.worker:
        return worker_main(args)

    from forge_headless.contracts import HeadlessError
    from forge_headless.load_authorization import ControlledLoadAuthorization
    from forge_headless.model_intake import PREFLIGHT_COMPATIBLE

    try:
        authorization = ControlledLoadAuthorization(
            {
                "checkpoint": args.checkpoint,
                "text_encoder": args.text_encoder,
                "vae": args.vae,
            },
            timeout_seconds=TIMEOUT_SECONDS,
            vram_ceiling_bytes=VRAM_CEILING_BYTES,
        )
    except HeadlessError as exc:
        print(f"verdict: {exc.code}")
        return 2

    preflight = run_preflight(authorization)
    write_report(PREFLIGHT_JSON, preflight)
    print(f"preflight: {preflight['verdict']}")
    print(f"report:    Evidence/studio-controlled-model-load/{PREFLIGHT_JSON.name}")

    if preflight["verdict"] != PREFLIGHT_COMPATIBLE:
        write_report(
            LOAD_JSON,
            {
                "schema_version": "controlled-load-report/v1",
                "authorization": authorization.to_public_dict(),
                "outcome": "CONTROLLED_MODEL_LOAD_NOT_ATTEMPTED",
                "preflight_verdict": preflight["verdict"],
                "checkpoint_tensor_payload_read": False,
                "cuda_initialized": False,
                "torch_imported": False,
                "model_loaded": False,
                "image_generated": False,
                "network_used": False,
                "single_attempt_consumed": False,
                "states": ["CATALOGUE_READY_NO_MODEL"],
            },
        )
        print("load:      CONTROLLED_MODEL_LOAD_NOT_ATTEMPTED (safe preflight stop)")
        return 0

    if not args.consume_single_load_attempt:
        print(
            "load:      not attempted -- rerun with "
            "--consume-single-load-attempt to spend the one authorized attempt"
        )
        return 0

    outcome = run_worker(authorization, timeout=TIMEOUT_SECONDS)
    worker = outcome.get("worker") or {}
    succeeded = bool(worker.get("stage") == "ready") and not outcome["worker_timed_out"]
    write_report(
        LOAD_JSON,
        {
            "schema_version": "controlled-load-report/v1",
            "authorization": authorization.to_public_dict(),
            "outcome": (
                "CONTROLLED_MODEL_LOAD_SUCCEEDED"
                if succeeded
                else "CONTROLLED_MODEL_LOAD_FAILED"
            ),
            "preflight_verdict": preflight["verdict"],
            "single_attempt_consumed": True,
            "timeout_seconds": TIMEOUT_SECONDS,
            "vram_ceiling_bytes": VRAM_CEILING_BYTES,
            **outcome,
        },
    )
    print(
        "load:      "
        + ("CONTROLLED_MODEL_LOAD_SUCCEEDED" if succeeded else "CONTROLLED_MODEL_LOAD_FAILED")
    )
    print(f"report:    Evidence/studio-controlled-model-load/{LOAD_JSON.name}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
