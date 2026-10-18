"""Safe dtypes and honest failures on Apple Silicon (MPS). MAC1, ADAPTER-003.

Neo already runs on MPS: it selects the device, keeps text encoders on the CPU
(`vram_state = SHARED`), stores and computes in bf16 only from macOS 14
(`should_use_bf16`), and keeps the VAE in bf16 or fp32 (`vae_dtype`). Three
gaps remain, read from Neo `97ff3a40` (Evidence/source-review/MAC1-mps-device.md):

1. **fp8 weights reach Metal.** `backend/loader.py:440` keeps an fp8 state
   dict in fp8 (`storage_dtype = state_dict_dtype`) whatever the device, and
   the text-encoder branches (:171, :205, :239, :280, :334) do the same. Metal
   has no float8 type, so the first move to the device fails. Fix: on MPS,
   plain fp8 tensors are upcast as they are read, to bf16 where Neo would
   compute in bf16 (macOS 14+) and fp16 otherwise -- the dtype Neo then picks
   for the model anyway.
2. **Scaled fp8 cannot be upcast correctly.** A `scaled_fp8` / `comfy_quant`
   file stores weights that are only meaningful with their scales, which
   Neo's mixed-precision ops apply at run time (`backend/state_dict.py:105-160`).
   Those ops are fp8 kernels. Upcasting the raw weights would load a model
   that generates noise, so the load is refused with a sentence instead.
3. **The out-of-memory check calls CUDA.** `memory_management.is_oom`, given
   an error that is an OOM by message but not by type, calls
   `discard_cuda_async_error`, which runs `torch.cuda.synchronize()`. On a Mac
   that raises "Torch not compiled with CUDA enabled" from inside the VAE's
   OOM handler, replacing the real error and skipping Neo's tiled-decode
   retry. Fix: on MPS that one function synchronizes Metal instead.

And one guard: `vae_dtype` never returns fp16 on MPS unless a command-line
flag forces it; Studio passes none. The SDXL VAE overflows in fp16 (black
images), so if fp16 ever comes back, it is replaced by bf16 or fp32.

Installed once by `bootstrap_backend`, and only when Neo's memory manager is
in MPS mode. On CUDA nothing is wrapped, so the CUDA path is Neo's.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable

LOG = logging.getLogger("studio.mps")

#: Marks a wrapper so a second install never stacks another one.
_WRAPPED = "_studio_mps_adapter"

#: Every float8 dtype name Neo knows (`memory_management.py:75`).
FLOAT8_NAMES = ("float8_e4m3fn", "float8_e4m3fnuz", "float8_e5m2",
                "float8_e5m2fnuz", "float8_e8m0fnu")

QUANTIZED_FP8_MESSAGE = (
    "This model file is a scaled fp8 build ({marker}). Apple Silicon GPUs "
    "cannot run fp8, and this kind of file cannot be converted safely while "
    "loading. Download the fp16 or bf16 version of the same model and choose "
    "that instead."
)

STATE: dict[str, Any] = {"installed": False, "reason": "not_installed"}


def float8_dtypes(torch: Any) -> tuple[Any, ...]:
    return tuple(getattr(torch, name) for name in FLOAT8_NAMES if hasattr(torch, name))


def quantization_marker(state_dict: Any, metadata: Any = None) -> str:
    """The first sign that a state dict is scaled/quantized fp8, or ""."""

    if isinstance(metadata, dict) and "_quantization_metadata" in metadata:
        return "_quantization_metadata"
    for key in state_dict:
        text = str(key)
        if text.endswith("scaled_fp8"):
            return "scaled_fp8"
        if text.endswith(".comfy_quant"):
            return "comfy_quant"
        if text.endswith(".scale_weight"):
            return "scale_weight"
    return ""


def upcast_target(memory_management: Any, torch: Any) -> Any:
    """bf16 where Neo computes in bf16 on this Mac (macOS 14+), else fp16."""

    try:
        if memory_management.should_use_bf16(torch.device("mps")):
            return torch.bfloat16
    except Exception:  # noqa: BLE001 - an unreadable macOS version is not bf16
        pass
    return torch.float16


def upcast_float8(state_dict: dict, target: Any, torch: Any) -> int:
    """Replace plain fp8 tensors with `target` copies, in place. Returns how many."""

    kinds = float8_dtypes(torch)
    converted = 0
    for key in list(state_dict):
        value = state_dict[key]
        if getattr(value, "dtype", None) in kinds:
            state_dict[key] = value.to(dtype=target)
            converted += 1
    return converted


def wrap_load_torch_file(original: Callable[..., Any], memory_management: Any,
                         torch: Any) -> Callable[..., Any]:
    """`backend.loader.load_torch_file`, made safe for Metal."""

    if getattr(original, _WRAPPED, False):
        return original

    def load_torch_file(ckpt: str, *args: Any, **kwargs: Any) -> Any:
        result = original(ckpt, *args, **kwargs)
        if isinstance(result, tuple):
            state_dict, metadata = result[0], (result[1] if len(result) > 1 else None)
        else:
            state_dict, metadata = result, None
        if not isinstance(state_dict, dict):
            return result
        kinds = float8_dtypes(torch)
        if not any(getattr(v, "dtype", None) in kinds for v in state_dict.values()):
            return result
        marker = quantization_marker(state_dict, metadata)
        if marker:
            from .contracts import HeadlessError

            raise HeadlessError("MPS_SCALED_FP8_UNSUPPORTED",
                                QUANTIZED_FP8_MESSAGE.format(marker=marker))
        target = upcast_target(memory_management, torch)
        count = upcast_float8(state_dict, target, torch)
        # The file name only, never the folder it lives in.
        LOG.warning("Apple GPU: %d fp8 tensors in %s were converted to %s while "
                    "loading (Metal has no fp8). This uses more memory than on "
                    "NVIDIA.", count, os.path.basename(str(ckpt)),
                    str(target).replace("torch.", ""))
        return result

    setattr(load_torch_file, _WRAPPED, True)
    load_torch_file.__wrapped__ = original  # type: ignore[attr-defined]
    return load_torch_file


def wrap_vae_dtype(original: Callable[..., Any], memory_management: Any,
                   torch: Any) -> Callable[..., Any]:
    """`memory_management.vae_dtype`, never fp16 on MPS."""

    if getattr(original, _WRAPPED, False):
        return original

    def vae_dtype(*args: Any, **kwargs: Any) -> Any:
        chosen = original(*args, **kwargs)
        if chosen != torch.float16:
            return chosen
        safe = torch.float32
        try:
            if memory_management.should_use_bf16(torch.device("mps")):
                safe = torch.bfloat16
        except Exception:  # noqa: BLE001
            pass
        return safe

    setattr(vae_dtype, _WRAPPED, True)
    vae_dtype.__wrapped__ = original  # type: ignore[attr-defined]
    return vae_dtype


def metal_discard_async_error(torch: Any) -> Callable[[], None]:
    """Neo's `discard_cuda_async_error`, for a Mac: sync Metal, never raise."""

    def discard_cuda_async_error() -> None:
        try:
            torch.mps.synchronize()
        except Exception:  # noqa: BLE001 - the caller is already handling an error
            pass

    setattr(discard_cuda_async_error, _WRAPPED, True)
    return discard_cuda_async_error


def install(memory_management: Any = None, loader: Any = None,
            torch: Any = None) -> dict[str, Any]:
    """Install on MPS only. Never raises; returns what it did."""

    STATE.clear()
    memory_management = memory_management or sys.modules.get("backend.memory_management")
    if memory_management is None:
        STATE.update(installed=False, reason="engine_not_loaded")
        return dict(STATE)
    try:
        on_mps = bool(memory_management.mps_mode())
    except Exception:  # noqa: BLE001
        on_mps = False
    if not on_mps:
        STATE.update(installed=False, reason="not_mps")
        return dict(STATE)
    try:
        if torch is None:
            import torch  # noqa: PLC0415
        if loader is None:
            import backend.loader as loader  # noqa: PLC0415

        loader.load_torch_file = wrap_load_torch_file(
            loader.load_torch_file, memory_management, torch)
        memory_management.vae_dtype = wrap_vae_dtype(
            memory_management.vae_dtype, memory_management, torch)
        if not getattr(memory_management.discard_cuda_async_error, _WRAPPED, False):
            memory_management.discard_cuda_async_error = metal_discard_async_error(torch)
        target = upcast_target(memory_management, torch)
    except Exception as exc:  # noqa: BLE001 - startup reports, never dies
        STATE.update(installed=False, reason=f"failed: {type(exc).__name__}: {exc}"[:200])
        LOG.warning("Apple GPU adapter not installed: %s", STATE["reason"])
        return dict(STATE)
    STATE.update(installed=True, reason="mps",
                 fp8_upcast_to=str(target).replace("torch.", ""))
    return dict(STATE)


__all__ = (
    "FLOAT8_NAMES",
    "QUANTIZED_FP8_MESSAGE",
    "STATE",
    "install",
    "metal_discard_async_error",
    "quantization_marker",
    "upcast_float8",
    "upcast_target",
    "wrap_load_torch_file",
    "wrap_vae_dtype",
)
