"""Linux container adapter; Studio's native launch and network policy stay intact."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
from urllib.request import ProxyHandler, build_opener

APP = Path(__file__).resolve().parents[2]
DATA = Path("/studio/data")
MODELS = Path("/studio/models")
sys.path.insert(0, str(APP))


def ensure_config(data: Path = DATA, models: Path = MODELS) -> Path:
    """Create defaults once; settings written by Studio remain authoritative."""
    config_path = data / "config" / "studio-config.json"
    if not config_path.exists():
        port = int(os.environ.get("STUDIO_PORT", "7865"))
        if not 1 <= port <= 65535:
            raise ValueError("STUDIO_PORT must be between 1 and 65535")
        config = {
            "backend": "headless", "host": "127.0.0.1", "port": port,
            "result_root": str(data / "results"),
            "studio_state_root": str(data / "state"),
            "model_roots": {
                "checkpoint": [str(models / "Stable-diffusion")],
                "vae": [str(models / "VAE")],
                "text_encoder": [str(models / "text_encoder")],
                "lora": [str(models / "Lora")],
            },
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with config_path.open("x", encoding="utf-8") as stream:
                json.dump(config, stream, indent=2)
                stream.write("\n")
        except FileExistsError:
            pass
    return config_path


def validate_config(config_path: Path) -> dict:
    from forge_studio.launch import load_config
    config = load_config(config_path)
    if config["backend"] != "headless" or config["port"] == 0:
        raise ValueError("Docker profile requires backend=headless and a fixed nonzero port")
    return config


def check_gpu() -> None:
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Check the host NVIDIA driver, Container Toolkit and Compose GPU reservation.")
    # Diagnostic only: validates a CUDA kernel without loading any model.
    value = (torch.ones(1, device="cuda") + 1).item()
    if value != 2:
        raise RuntimeError("CUDA computation failed")
    print(f"GPU OK: {torch.cuda.get_device_name(0)}; torch {torch.__version__}; CUDA {torch.version.cuda}", flush=True)


def healthcheck(config_path: Path) -> None:
    config = validate_config(config_path)
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{config['port']}/api/status", timeout=5) as response:
        if response.status != 200 or not isinstance(json.load(response), dict):
            raise RuntimeError("Studio status endpoint is not healthy")


def stop(_signum, _frame) -> None:
    # Studio already handles KeyboardInterrupt with server/composition cleanup.
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Create/validate persistent configuration only")
    modes.add_argument("--check-gpu", action="store_true", help="Run a small CUDA computation, then exit")
    modes.add_argument("--healthcheck", action="store_true", help="Check the running server, then exit")
    args = parser.parse_args(argv)
    try:
        if args.healthcheck:
            healthcheck(DATA / "config" / "studio-config.json")
            return 0
        if args.check_gpu:
            check_gpu()
            return 0
        config_path = ensure_config(DATA, MODELS)
        config = validate_config(config_path)
        for name in ("home", "cache", "logs", "results", "state"):
            (DATA / name).mkdir(parents=True, exist_ok=True)
        print(f"Studio Docker config OK; URL http://127.0.0.1:{config['port']}/studio/", flush=True)
        if args.check:
            return 0
        # Bootstrap must set allocator policy before the first torch import.
        # The GPU diagnostic runs only in its own --check-gpu process.
        signal.signal(signal.SIGTERM, stop)
        from forge_studio.launch import main as studio_main
        return studio_main(["--config", str(config_path), "--fast-fp16"])
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Studio Docker cannot start/check: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
