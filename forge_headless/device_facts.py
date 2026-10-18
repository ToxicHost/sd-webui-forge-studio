"""The inference device as the engine chose it, in the contract's vocabulary. MAC1.

`docs/studio/STUDIO_PLATFORM_CAPABILITY_CONTRACT.md` section 5 specifies the
translation and says it lives in the adapter, never in `forge_studio`:

    device_type        <- memory_management.get_torch_device()
    dtype_policy       <- the backend's own per-model decision
    attention_backend  <- sage / flash / xformers / pytorch, in Neo's order

Everything is read from `backend.memory_management` as found in
`sys.modules`. It is never imported here: importing it initialises the device,
and asking a question must not do that. Before the engine has booted, every
field is `UNKNOWN`, which the contract defines as "did not say".

The host operating system is never consulted. A Mac whose PyTorch cannot reach
Metal reports `cpu`, because that is the device Neo would use.

Source review: Evidence/source-review/MAC1-mps-device.md.
"""

from __future__ import annotations

import sys
from typing import Any

#: Neo device types the contract has a member for. Anything else is UNKNOWN.
_DEVICE_TYPES = ("cuda", "mps", "cpu", "xpu")


def _engine() -> Any:
    return sys.modules.get("backend.memory_management")


def device_type_value() -> str:
    """`cuda`, `mps`, `cpu`, `xpu`, `directml` or `unknown`."""

    memory_management = _engine()
    if memory_management is None:
        return "unknown"
    try:
        if memory_management.is_directml_enabled():
            return "directml"
        kind = str(memory_management.get_torch_device().type)
    except Exception:  # noqa: BLE001 - an engine that cannot answer is silent
        return "unknown"
    return kind if kind in _DEVICE_TYPES else "unknown"


def dtype_policy_value() -> str:
    """`fp32` when the engine is forced to it, else `backend_default`.

    Neo picks storage and compute dtypes PER MODEL from the architecture's
    declared order (`memory_management.unet_dtype` / `inference_cast`): SDXL
    computes in fp16 where Anima prefers bf16, on the same device. One
    device-wide value would be wrong for one of them, so the contract's
    `backend_default` is the true answer.
    """

    memory_management = _engine()
    if memory_management is None:
        return "unknown"
    if getattr(memory_management, "FORCE_FP32", False):
        return "fp32"
    return "backend_default"


def attention_backend_value() -> str:
    """The implementation `backend/attention.py:331-361` selects, in its order."""

    memory_management = _engine()
    if memory_management is None:
        return "unknown"
    try:
        if memory_management.sage_enabled():
            return "sage"
        if memory_management.flash_enabled():
            return "flash"
        if memory_management.xformers_enabled():
            return "xformers"
        if memory_management.pytorch_attention_enabled():
            return "pytorch_sdpa"
    except Exception:  # noqa: BLE001
        return "unknown"
    # Neo's `attention_basic`: no member names it, and it is the backend's own
    # fallback rather than an unknown.
    return "backend_default"


def describe() -> dict[str, str]:
    """Scalars only, for the startup log and the support bundle."""

    facts = {
        "device_type": device_type_value(),
        "dtype_policy": dtype_policy_value(),
        "attention_backend": attention_backend_value(),
    }
    memory_management = _engine()
    if facts["device_type"] == "mps" and memory_management is not None:
        try:
            version = memory_management.mac_version()
            facts["macos"] = ".".join(str(n) for n in version) if version else "unknown"
            facts["bf16"] = "yes" if memory_management.should_use_bf16(
                memory_management.get_torch_device()) else "no"
        except Exception:  # noqa: BLE001 - optional detail
            pass
    return facts


def contract_fields() -> dict[str, Any]:
    """The three `ModelCapability` fields as enum members."""

    from forge_studio.contracts import AttentionBackend, DeviceType, DtypePolicy

    return {
        "device_type": DeviceType(device_type_value()),
        "dtype_policy": DtypePolicy(dtype_policy_value()),
        "attention_backend": AttentionBackend(attention_backend_value()),
    }


__all__ = (
    "attention_backend_value",
    "contract_fields",
    "describe",
    "device_type_value",
    "dtype_policy_value",
)
