"""What this Mac's GPU can do for Studio, measured without loading a model. MAC1.

Run by the Mac tester in stage T1, from the Studio folder, after
`start-studio.sh` has prepared `venv`:

    venv/bin/python scripts/mac_device_probe.py

It runs Studio's real backend start-up (`bootstrap_backend`: options,
samplers, the Anima/heatmap/Apple adapters) exactly as the app does at launch,
then reports:

- the Mac: chip, memory, macOS, Python, Torch, whether Metal (MPS) is usable;
- small tensor operations on the GPU in fp32 / fp16 / bf16, an fp16 attention
  call checked for NaN (a black picture starts as a NaN), and whether Metal
  accepts an fp8 tensor (it should not; Studio converts fp8 models because of
  this);
- the engine's own decisions on this Mac: device, memory mode, the dtypes it
  would pick for SDXL and Anima, where text encoders run, the VAE dtype, the
  attention implementation, and whether Studio's Apple adapter installed.

Loads no model, downloads nothing, sends nothing. Writes one plain-text report
(home folder redacted) and prints where it is. Runs on Windows too, where it
reports CUDA or "no accelerator"; that is how it is tested.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path.home() / "Studio-Mac-Test-Results" / "t1-device-probe.txt"


def _run(command: list[str]) -> str:
    tool = shutil.which(command[0])
    if tool is None:
        return ""
    try:
        done = subprocess.run([tool, *command[1:]], capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip()


def redact(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if home and home != "/" else text


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0

    def section(self, title: str) -> None:
        self.lines += ["", title, "-" * len(title)]

    def add(self, key: str, value: object) -> None:
        self.lines.append(f"{key:<34} {value}")

    def check(self, key: str, func) -> object:
        """Run one probe; a failure is recorded, never fatal."""
        started = time.perf_counter()
        try:
            value = func()
        except Exception as exc:  # noqa: BLE001 - every probe is independent
            self.add(key, f"FAILED {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
            self.failures += 1
            return None
        self.add(key, f"{value}   ({(time.perf_counter() - started) * 1000:.0f} ms)")
        return value

    def text(self) -> str:
        return redact("\n".join(self.lines).strip() + "\n")


def machine(report: Report) -> None:
    report.section("Machine")
    report.add("system", f"{platform.system()} {platform.release()} {platform.machine()}")
    mac = platform.mac_ver()[0]
    report.add("macOS", mac or "not macOS")
    report.add("chip", _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "unknown")
    memory = _run(["sysctl", "-n", "hw.memsize"])
    report.add("memory", f"{int(memory) / 1024**3:.0f} GB" if memory.isdigit() else "unknown")
    report.add("python", f"{platform.python_version()} ({sys.executable})")


def tensors(report: Report) -> str:
    """Small GPU operations. Returns the device kind used."""

    import torch

    report.section("PyTorch and the GPU")
    report.add("torch", torch.__version__)
    mps = getattr(torch.backends, "mps", None)
    built = bool(mps and mps.is_built())
    available = bool(mps and mps.is_available())
    report.add("MPS built / available", f"{built} / {available}")
    report.add("CUDA available", torch.cuda.is_available())
    kind = "mps" if available else "cuda" if torch.cuda.is_available() else "cpu"
    report.add("device used for these checks", kind)
    if kind == "cpu":
        report.add("result", "NO GPU: Studio will refuse to generate on this machine")
        return kind
    device = torch.device(kind)

    def matmul(dtype):
        def run():
            a = torch.randn(512, 512, device=device, dtype=dtype)
            b = (a @ a).float()
            if not torch.isfinite(b).all():
                raise ValueError("non-finite result")
            return "ok"
        return run

    report.check("matmul fp32", matmul(torch.float32))
    report.check("matmul fp16", matmul(torch.float16))
    report.check("matmul bf16", matmul(torch.bfloat16))

    def attention():
        q = torch.randn(2, 10, 1024, 64, device=device, dtype=torch.float16)
        out = torch.nn.functional.scaled_dot_product_attention(q, q, q)
        return "ok, no NaN" if not torch.isnan(out).any() else "NaN PRESENT"

    report.check("attention fp16 (SDPA)", attention)

    def float8():
        if not hasattr(torch, "float8_e4m3fn"):
            return "torch has no float8"
        try:
            torch.zeros(4, device=device).to(torch.float8_e4m3fn)
        except Exception as exc:  # noqa: BLE001 - the expected outcome on a Mac
            return f"refused, as expected on a Mac ({type(exc).__name__})"
        return "accepted"

    report.check("fp8 tensor on the GPU", float8)

    def vae_sized():
        x = torch.randn(1, 4, 128, 128, device=device, dtype=torch.float32)
        conv = torch.nn.Conv2d(4, 128, 3, padding=1).to(device)
        y = torch.nn.functional.interpolate(conv(x), scale_factor=8.0)
        return f"ok {tuple(y.shape)}"

    report.check("1024px decode-sized conv", vae_sized)
    return kind


def engine(report: Report) -> None:
    """Studio's real start-up, then the engine's own decisions."""

    report.section("Studio start-up (no model is loaded)")
    sys.path.insert(0, str(APP))
    from forge_headless.backend_bootstrap import bootstrap_backend

    boot = report.check("bootstrap_backend", lambda: bootstrap_backend(APP).state)
    if boot is None:
        return
    from forge_headless import device_facts, mps_adapter

    for key, value in device_facts.describe().items():
        report.add(f"device_facts.{key}", value)
    report.add("Apple adapter (ADAPTER-003)", dict(mps_adapter.STATE))

    import torch
    from backend import memory_management as mm

    report.section("Engine decisions on this machine")
    device = mm.get_torch_device()
    report.add("engine device", device)
    report.add("memory mode (vram_state)", mm.vram_state.name)
    report.add("text encoders run on", mm.text_encoder_device())
    report.check("should_use_fp16", lambda: mm.should_use_fp16(device))
    report.check("should_use_bf16", lambda: mm.should_use_bf16(device))
    report.check("VAE dtype", lambda: mm.vae_dtype())
    sdxl = [torch.float16, torch.bfloat16, torch.float32]
    anima = [torch.bfloat16, torch.float16, torch.float32]
    for name, supported in (("SDXL", sdxl), ("Anima", anima)):
        for weights in (torch.float16, torch.bfloat16):
            def storage(supported=supported, weights=weights):
                chosen = mm.unet_dtype(device=device, model_params=2_600_000_000,
                                       supported_dtypes=supported, weight_dtype=weights)
                compute = mm.inference_cast(chosen, device, supported)
                return f"store {chosen}, compute {compute}"
            report.check(f"{name} model, {str(weights)[6:]} file", storage)
    report.check("free memory (engine view)",
                 lambda: f"{mm.get_free_memory(device) / 1024**3:.1f} GB")
    report.check("total memory (engine view)",
                 lambda: f"{mm.get_total_memory(device) / 1024**3:.1f} GB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-engine", action="store_true",
                        help="skip Studio's start-up; only the machine and tensor checks")
    args = parser.parse_args()

    report = Report()
    report.lines.append("Forge Studio - Mac device probe (MAC1). No model loaded; nothing sent.")
    machine(report)
    try:
        kind = tensors(report)
    except Exception:  # noqa: BLE001 - torch missing or broken: say so, keep going
        report.add("torch", "FAILED to import or run:")
        report.lines += ["    " + line for line in traceback.format_exc().splitlines()[-6:]]
        report.failures += 1
        kind = "none"
    # Without a GPU the engine cannot start (Neo asks CUDA for its device),
    # and the "no GPU" line above is already the whole answer.
    if not args.no_engine and kind in ("mps", "cuda"):
        try:
            engine(report)
        except Exception:  # noqa: BLE001
            report.add("engine", "FAILED:")
            report.lines += ["    " + line for line in traceback.format_exc().splitlines()[-8:]]
            report.failures += 1

    report.section("Summary")
    report.add("probe failures", report.failures)
    text = report.text()
    print(text)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Saved to {redact(str(args.out))}")
    except OSError as exc:
        print(f"Could not save the report: {exc}", file=sys.stderr)
        return 2
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
