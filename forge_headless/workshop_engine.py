"""Workshop: the merge, bake and inspection arithmetic. W1.

The Extension's `scripts/studio_workshop.py`, ported to the owner's scope
(2026-10-03): three merge methods (Weighted Sum, Add Difference, SLERP), block
weights, LoRA baking, VAE baking, and the basic Inspector (model info, A/B
compatibility, RAM preflight). The eleven experimental methods, the health
scan, the cosine-difference map, Model Stock and the in-memory Test Merge are
not ported.

What is kept exactly, because the Extension's own comments explain why:

  * all merge and bake arithmetic in fp32, cast to the output dtype per key;
  * VAE keys always stay fp32, and so do Anima's `llm_adapter` keys;
  * Anima/Cosmos inputs are canonicalised to `net.*` before keys are paired,
    and `llm_adapter` is copied from Model A, never merged;
  * a NaN or a shape mismatch keeps Model A for that key rather than failing;
  * SLERP falls back to LERP when the two tensors are nearly parallel.

Neo agrees on the two A1111 methods (`modules/extras.py:86-93`:
`torch.lerp(A, B, alpha)` and `A + alpha * (B - C)`), and on DoRA
(`backend/patcher/lora.py:35-55`).

Studio-shaped where it must be: torch and safetensors are imported inside the
functions (the launcher must not pull torch in), progress and cancellation go
through a `job` object instead of module globals, and a result is written to a
temporary name and renamed, so an existing model is never overwritten and a
cancelled run leaves nothing half-written in the model folder.

Source review: Evidence/source-review/W1-workshop.md
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .architecture import VAE_PREFIX, detect_architecture

VERSION = "0.6.0-studio"

#: The owner's three. Order is the page's dropdown order.
METHODS = ("weighted_sum", "slerp", "add_difference")
METHODS_NEEDING_C = frozenset({"add_difference"})

LLM_ADAPTER_PREFIX = "llm_adapter."


class Cancelled(Exception):
    """The owner pressed Cancel. Not an error."""


def _is_llm_adapter_key(key: str) -> bool:
    return key.startswith(LLM_ADAPTER_PREFIX) or ".llm_adapter." in key


def _is_protected_fp32_key(key: str, arch: Optional[str] = None) -> bool:
    if VAE_PREFIX in key:
        return True
    return arch == "cosmos" and _is_llm_adapter_key(key)


def _should_copy_from_a_without_merge(key: str, arch: Optional[str]) -> bool:
    return arch == "cosmos" and _is_llm_adapter_key(key)


def resolve_output_dtype(output_dtype: Any, arch: Optional[str], save_fp16: bool = True) -> str:
    """`_resolve_output_dtype`: "auto" is BF16 for Anima, FP16 otherwise."""

    od = str(output_dtype or "").strip().lower()
    if not od:
        od = "auto" if save_fp16 else "fp32"
    if od in ("fp16", "bf16", "fp32"):
        return od
    return "bf16" if arch == "cosmos" else "fp16"


def _cast_output_tensor(t: Any, dtype_policy: str, key: str, arch: Optional[str]) -> Any:
    import torch

    if _is_protected_fp32_key(key, arch) or not torch.is_floating_point(t):
        return t
    if dtype_policy == "fp32":
        return t.float() if t.dtype != torch.float32 else t
    if dtype_policy == "bf16":
        return t.to(torch.bfloat16)
    if dtype_policy == "fp16":
        return t.half()
    return t


# -- Anima / Cosmos namespace canonicalisation (`:159-256`) -----------------

_COSMOS_NET_PREFIX = "net."
_COSMOS_WRAPPED_PREFIX = "model.diffusion_model."


def _cosmos_canonical_key(key: str) -> Optional[str]:
    if "cond_stage_model." in key or "conditioner." in key:
        return None
    if key.startswith(VAE_PREFIX) or ".first_stage_model." in key:
        return None
    if key.startswith(_COSMOS_NET_PREFIX):
        return key
    if key.startswith(_COSMOS_WRAPPED_PREFIX):
        return _COSMOS_NET_PREFIX + key[len(_COSMOS_WRAPPED_PREFIX):]
    return None


def _cosmos_canonicalize_input(physical_keys: set) -> tuple[set, dict[str, str], dict[str, int]]:
    canonical: set = set()
    lookup: dict[str, str] = {}
    dropped = {"te": 0, "vae": 0, "other": 0}
    for key in physical_keys:
        if "cond_stage_model." in key or "conditioner." in key:
            dropped["te"] += 1
            continue
        if key.startswith(VAE_PREFIX) or ".first_stage_model." in key:
            dropped["vae"] += 1
            continue
        canonical_key = _cosmos_canonical_key(key)
        if canonical_key is None:
            dropped["other"] += 1
            continue
        canonical.add(canonical_key)
        if canonical_key not in lookup or key.startswith(_COSMOS_NET_PREFIX):
            lookup[canonical_key] = key
    return canonical, lookup, dropped


# -- Package accounting for the Inspector (`:269-336`) ----------------------

_DTYPE_BYTES = {
    "F64": 8, "F32": 4, "F16": 2, "BF16": 2,
    "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1, "BOOL": 1,
}


def _prefix_family(key: str) -> str:
    if key.startswith("model.diffusion_model."):
        rest = key[len("model.diffusion_model."):]
        return "model.diffusion_model." + rest.split(".", 1)[0]
    return key.split(".", 1)[0]


def _package_for_key(key: str, arch: Optional[str]) -> str:
    if _is_llm_adapter_key(key):
        return "llm_adapter"
    if key.startswith(VAE_PREFIX) or ".first_stage_model." in key:
        return "vae"
    if "cond_stage_model." in key or "conditioner." in key:
        return "text_encoder"
    if arch == "cosmos":
        if (".blocks." in key or key.startswith("blocks.")
                or any(part in key for part in ("adaln_modulation_cross_attn", "patch_embed",
                                                "x_embedder", "t_embedder", "time_embed",
                                                "final_layer"))):
            return "core"
    elif arch in ("flux1", "flux2", "sd3"):
        if ("double_blocks." in key or "single_blocks." in key or "joint_blocks." in key
                or key.startswith(("img_in.", "txt_in.", "time_in.", "vector_in.",
                                   "guidance_in.", "x_embedder.", "y_embedder.",
                                   "t_embedder.", "context_embedder.", "final_layer."))):
            return "core"
    elif arch in ("sdxl", "sd15"):
        if key.startswith(("model.diffusion_model.", "diffusion_model.")):
            return "core"
    return "other"


# -- Blocks (`:373-624`) ----------------------------------------------------

_RE_INP = re.compile(r"\.input_blocks\.(\d+)\.")
_RE_MID = re.compile(r"\.middle_block\.")
_RE_OUT = re.compile(r"\.output_blocks\.(\d+)\.")
_RE_DOUBLE = re.compile(r"double_blocks\.(\d+)\.")
_RE_SINGLE = re.compile(r"single_blocks\.(\d+)\.")
_RE_JOINT = re.compile(r"joint_blocks\.(\d+)\.")
_RE_COSMOS = re.compile(r"\.blocks\.(\d+)\.")


def classify_key(key: str, arch: str) -> str:
    """The block group a key's weight is taken from."""

    if arch in ("sd15", "sdxl"):
        if "cond_stage_model." in key or "conditioner." in key:
            return "BASE"
        found = _RE_INP.search(key)
        if found:
            return f"IN{int(found.group(1)):02d}"
        if _RE_MID.search(key):
            return "M00"
        found = _RE_OUT.search(key)
        if found:
            return f"OUT{int(found.group(1)):02d}"
    elif arch in ("flux1", "flux2"):
        found = _RE_DOUBLE.search(key)
        if found:
            return f"D{int(found.group(1)):02d}"
        found = _RE_SINGLE.search(key)
        if found:
            return f"S{int(found.group(1)):02d}"
    elif arch == "sd3":
        found = _RE_JOINT.search(key)
        if found:
            return f"J{int(found.group(1)):02d}"
    elif arch == "cosmos":
        found = _RE_COSMOS.search(key)
        if found:
            return f"B{int(found.group(1)):02d}"
    if VAE_PREFIX in key:
        return "VAE"
    return "OTHER"


def get_block_list(arch: str) -> list[str]:
    if arch == "sd15":
        return ["BASE"] + [f"IN{i:02d}" for i in range(12)] + ["M00"] + [f"OUT{i:02d}" for i in range(12)]
    if arch == "sdxl":
        return ["BASE"] + [f"IN{i:02d}" for i in range(9)] + ["M00"] + [f"OUT{i:02d}" for i in range(9)]
    if arch == "sd3":
        return [f"J{i:02d}" for i in range(38)]
    if arch == "flux1":
        return [f"D{i:02d}" for i in range(19)] + [f"S{i:02d}" for i in range(38)]
    if arch == "flux2":
        return [f"D{i:02d}" for i in range(8)] + [f"S{i:02d}" for i in range(48)]
    if arch == "cosmos":
        return [f"B{i:02d}" for i in range(28)]
    return []


def _unet_presets(inputs: int, outputs: int, style_in: int, style_out: int,
                  vpred_out: int) -> dict[str, Any]:
    ins = [f"IN{i:02d}" for i in range(inputs)]
    outs = [f"OUT{i:02d}" for i in range(outputs)]
    return {
        "ALL 0.5": None,
        "Style from A": {"BASE": 0.0, **{b: 0.0 if i < style_in else 1.0 for i, b in enumerate(ins)},
                         "M00": 1.0, **{b: 1.0 if i < style_out else 0.0 for i, b in enumerate(outs)}},
        "Composition from A": {"BASE": 1.0, **{b: 1.0 if i < style_in else 0.0 for i, b in enumerate(ins)},
                               "M00": 0.0, **{b: 0.0 if i < style_out else 1.0 for i, b in enumerate(outs)}},
        "EPS→V-Pred Safe": {"BASE": 0.0, **{b: 0.5 for b in ins}, "M00": 0.5,
                             **{b: 0.5 if i < vpred_out else 0.0 for i, b in enumerate(outs)}},
        "UNet Only": {"BASE": 0.0},
        "Text Encoder Only": {"BASE": 1.0, **{b: 0.0 for b in ins}, "M00": 0.0,
                              **{b: 0.0 for b in outs}},
    }


#: `PRESETS` (`:528-624`), the same weights, built rather than spelled out.
PRESETS: dict[str, dict[str, Any]] = {
    "sd15": _unet_presets(12, 12, 5, 7, 9),
    "sdxl": _unet_presets(9, 9, 4, 5, 6),
    "flux1": {
        "ALL 0.5": None,
        "Double Blocks Only": {**{f"D{i:02d}": 0.5 for i in range(19)},
                               **{f"S{i:02d}": 0.0 for i in range(38)}},
        "Single Blocks Only": {**{f"D{i:02d}": 0.0 for i in range(19)},
                               **{f"S{i:02d}": 0.5 for i in range(38)}},
    },
    "cosmos": {
        "ALL 0.5": None,
        "Early blocks from B": {**{f"B{i:02d}": 1.0 for i in range(14)},
                                **{f"B{i:02d}": 0.0 for i in range(14, 28)}},
        "Late blocks from B": {**{f"B{i:02d}": 0.0 for i in range(14)},
                               **{f"B{i:02d}": 1.0 for i in range(14, 28)}},
    },
}


# -- Inspector (`:770-1050`) ------------------------------------------------

def estimate_merge_ram(path_a: Path, path_b: Path) -> dict[str, Any]:
    import psutil

    size_a = os.path.getsize(path_a) / (1024 ** 3)
    size_b = os.path.getsize(path_b) / (1024 ** 3)
    output_buffer = max(size_a, size_b)
    overhead = 2.0
    peak = output_buffer + overhead
    memory = psutil.virtual_memory()
    available = memory.available / (1024 ** 3)
    total = memory.total / (1024 ** 3)
    safe = peak < available * 0.8
    warning = None if safe else (
        f"Estimated peak RAM: {peak:.1f} GB. Available: {available:.1f} GB / "
        f"{total:.1f} GB total. This merge may run out of memory.")
    return {
        "model_a_size_gb": round(size_a, 2), "model_b_size_gb": round(size_b, 2),
        "output_buffer_gb": round(output_buffer, 2), "overhead_gb": overhead,
        "peak_gb": round(peak, 2), "available_gb": round(available, 2),
        "total_gb": round(total, 2), "safe": safe, "warning": warning,
    }


def inspect_model(path: Path, display_name: str) -> dict[str, Any]:
    from safetensors import safe_open

    result: dict[str, Any] = {
        "filename": display_name,
        "size_gb": round(os.path.getsize(path) / (1024 ** 3), 2),
        "key_count": 0, "architecture": None,
        "metadata": {}, "model_info": {}, "dtypes": {}, "block_groups": {},
    }
    with safe_open(str(path), framework="pt", device="cpu") as f:
        keys = set(f.keys())
        result["key_count"] = len(keys)
        arch_info = detect_architecture(keys)
        result["architecture"] = arch_info
        meta = f.metadata()
        if meta:
            result["metadata"] = dict(meta)
            info: dict[str, Any] = {}
            for key in ("modelspec.prediction_type", "ss_v_parameterization",
                        "prediction_type", "modelspec.predict_key"):
                value = meta.get(key)
                if value:
                    if value in ("true", "True", "1"):
                        info["prediction"] = "v-prediction"
                    elif value in ("false", "False", "0"):
                        info["prediction"] = "eps"
                    else:
                        info["prediction"] = value
                    break
            for key in ("modelspec.title", "ss_sd_model_name", "modelspec.base_model"):
                if meta.get(key):
                    info["base_model"] = meta[key][:100]
                    break
            for key in ("modelspec.resolution", "ss_resolution"):
                if meta.get(key):
                    info["resolution"] = meta[key]
                    break
            recipe = meta.get("sd_merge_recipe") or meta.get("merge_recipe")
            if recipe:
                try:
                    info["merge_recipe"] = json.loads(recipe) if isinstance(recipe, str) else recipe
                except (json.JSONDecodeError, TypeError):
                    info["merge_recipe"] = str(recipe)[:200]
            result["model_info"] = info
        dtype_counts: dict[str, int] = {}
        block_counts: dict[str, int] = {}
        pkg_keys: dict[str, int] = {}
        pkg_bytes: dict[str, int] = {}
        pkg_prefixes: dict[str, dict[str, int]] = {}
        for key in keys:
            tensor = f.get_slice(key)
            dtype = str(tensor.get_dtype())
            dtype_counts[dtype] = dtype_counts.get(dtype, 0) + 1
            group = classify_key(key, arch_info["arch"])
            block_counts[group] = block_counts.get(group, 0) + 1
            numel = 1
            for dim in tensor.get_shape():
                numel *= dim
            package = _package_for_key(key, arch_info["arch"])
            pkg_keys[package] = pkg_keys.get(package, 0) + 1
            pkg_bytes[package] = pkg_bytes.get(package, 0) + numel * _DTYPE_BYTES.get(dtype, 0)
            family = _prefix_family(key)
            pkg_prefixes.setdefault(package, {})
            pkg_prefixes[package][family] = pkg_prefixes[package].get(family, 0) + 1
        result["dtypes"] = dtype_counts
        result["block_groups"] = dict(sorted(block_counts.items()))
        result["packages"] = {
            name: {
                "keys": pkg_keys.get(name, 0),
                "bytes": pkg_bytes.get(name, 0),
                "gb": round(pkg_bytes.get(name, 0) / (1024 ** 3), 3),
                "prefixes": dict(sorted(pkg_prefixes.get(name, {}).items(), key=lambda kv: -kv[1])),
            }
            for name in ("core", "text_encoder", "vae", "llm_adapter", "other")
        }
    return result


def check_compatibility(path_a: Path, path_b: Path) -> dict[str, Any]:
    """`check_compatibility` (`:895-1050`): issues block, warnings caution."""

    from safetensors import safe_open

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    info: list[dict[str, str]] = []
    with safe_open(str(path_a), framework="pt", device="cpu") as f_a, \
            safe_open(str(path_b), framework="pt", device="cpu") as f_b:
        keys_a = set(f_a.keys())
        keys_b = set(f_b.keys())
        arch_a = detect_architecture(keys_a)
        arch_b = detect_architecture(keys_b)
        if arch_a["arch"] != arch_b["arch"]:
            issues.append({"type": "arch_mismatch",
                           "text": f"Architecture mismatch: {arch_a['details']} vs {arch_b['details']}",
                           "detail": "These models cannot be merged — they have different network structures."})
        else:
            info.append({"type": "arch_match", "text": f"Architecture: {arch_a['details']}"})
            if arch_a["arch"] == "cosmos":
                info.append({"type": "cosmos_llm_adapter",
                             "text": "Cosmos/Anima model detected; llm_adapter keys will be copied from Model A, not merged."})
                warnings.append({"type": "cosmos_preview",
                                 "text": "Anima preview models may not transfer cleanly to future final releases."})
                te_a = arch_a.get("text_encoder_key_count", 0)
                te_b = arch_b.get("text_encoder_key_count", 0)
                if (te_a > 0) != (te_b > 0):
                    warnings.append({"type": "cosmos_te_asymmetry",
                                     "text": f"Bundled text encoder: {te_a} keys in A, {te_b} keys in B",
                                     "detail": ("One Cosmos model carries a bundled text encoder and the "
                                                "other doesn't. Workshop writes the canonical layout, so "
                                                "the bundled encoder is dropped from the output.")})
        shared = keys_a & keys_b
        only_a = keys_a - keys_b
        only_b = keys_b - keys_a
        overlap = len(shared) / max(len(keys_a | keys_b), 1) * 100
        if overlap < 50:
            issues.append({"type": "low_overlap",
                           "text": f"Key overlap: {overlap:.0f}% ({len(shared)} shared, {len(only_a)} only in A, {len(only_b)} only in B)",
                           "detail": "Less than 50% shared keys — these models may be incompatible."})
        elif overlap < 90:
            warnings.append({"type": "partial_overlap",
                             "text": f"Key overlap: {overlap:.0f}% ({len(only_a)} unique to A, {len(only_b)} unique to B)"})
        else:
            info.append({"type": "good_overlap", "text": f"Key overlap: {overlap:.0f}% ({len(shared)} shared keys)"})
        clip_a = any("conditioner." in k or "cond_stage_model." in k for k in keys_a)
        clip_b = any("conditioner." in k or "cond_stage_model." in k for k in keys_b)
        if clip_a != clip_b:
            warnings.append({"type": "clip_mismatch",
                             "text": f"CLIP text encoder: {'present' if clip_a else 'absent'} in A, {'present' if clip_b else 'absent'} in B",
                             "detail": "One model has a baked-in text encoder and the other doesn't. The result will inherit whichever is present."})
        vae_a = any(k.startswith(VAE_PREFIX) for k in keys_a)
        vae_b = any(k.startswith(VAE_PREFIX) for k in keys_b)
        if vae_a != vae_b:
            warnings.append({"type": "vae_mismatch",
                             "text": f"VAE: {'baked' if vae_a else 'absent'} in A, {'baked' if vae_b else 'absent'} in B"})
        elif vae_a and vae_b:
            info.append({"type": "vae_both", "text": "Both models have baked-in VAE"})
        sample = list(shared)[:200]
        mismatched = sum(1 for key in sample
                         if str(f_a.get_slice(key).get_dtype()) != str(f_b.get_slice(key).get_dtype()))
        if mismatched:
            warnings.append({"type": "dtype_mismatch",
                             "text": f"Dtype mismatches: {mismatched / max(len(sample), 1) * 100:.0f}% of sampled keys differ (merge will upcast to fp32)"})
    verdict = "incompatible" if issues else ("caution" if warnings else "compatible")
    return {"verdict": verdict, "issues": issues, "warnings": warnings, "info": info}


def architecture_of(path: Path) -> dict[str, Any]:
    from safetensors import safe_open

    with safe_open(str(path), framework="pt", device="cpu") as f:
        return detect_architecture(set(f.keys()))


# -- Writing ----------------------------------------------------------------

def _save(output: dict[str, Any], output_path: Path, recipe: dict[str, Any]) -> None:
    """Write under a temporary name, then rename: never overwrite, never
    leave a half-written model where the catalogue would list it."""

    from safetensors.torch import save_file

    if output_path.exists():
        raise FileExistsError(f"Output file already exists: {output_path.name}")
    partial = output_path.with_name(output_path.name + ".partial")
    try:
        save_file(output, str(partial), metadata={"studio_workshop_recipe": json.dumps(recipe)})
        os.rename(partial, output_path)
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass


def _stamp(recipe: dict[str, Any]) -> dict[str, Any]:
    recipe["timestamp"] = datetime.now(timezone.utc).isoformat()
    recipe["workshop_version"] = VERSION
    return recipe


def _tick(job: Any, index: int, total: int, key: str, start: float) -> None:
    if job.cancelled():
        raise Cancelled()
    if index % 50 == 0:
        job.update(progress=index / max(total, 1), keys_done=index, keys_total=total,
                   current_key=key, elapsed=round(time.time() - start, 1))


# -- Merge (`:1674-2180`) ---------------------------------------------------

def _merge_pair(t_a: Any, t_b: Any, alpha: float, method: str, stats: dict[str, int]) -> Any:
    import torch

    if method == "slerp":
        a_n = torch.nn.functional.normalize(t_a.flatten().float(), dim=0)
        b_n = torch.nn.functional.normalize(t_b.flatten().float(), dim=0)
        dot = torch.clamp(torch.dot(a_n, b_n), -1.0, 1.0).item()
        del a_n, b_n
        if abs(dot) > 0.9995:
            stats["lerp_fallback"] = stats.get("lerp_fallback", 0) + 1
            return torch.lerp(t_a, t_b, alpha)
        theta = math.acos(dot)
        sin_theta = math.sin(theta)
        stats["slerp_count"] = stats.get("slerp_count", 0) + 1
        return (math.sin((1.0 - alpha) * theta) / sin_theta) * t_a + (math.sin(alpha * theta) / sin_theta) * t_b
    return torch.lerp(t_a, t_b, alpha)


def merge_models(job: Any, path_a: Path, path_b: Path, output_path: Path, *,
                 method: str = "weighted_sum", alpha: float = 0.5,
                 block_weights: Optional[dict[str, float]] = None, save_fp16: bool = True,
                 path_c: Optional[Path] = None, output_dtype: str = "auto",
                 names: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Key-iterative merge. `names` are the display names for the recipe."""

    import contextlib

    import torch
    from safetensors import safe_open

    if method not in METHODS:
        raise ValueError(f"Method '{method}' is not available.")
    names = names or {}
    start = time.time()
    output: dict[str, Any] = {}
    stats: dict[str, int] = {}
    with contextlib.ExitStack() as stack:
        f_a = stack.enter_context(safe_open(str(path_a), framework="pt", device="cpu"))
        f_b = stack.enter_context(safe_open(str(path_b), framework="pt", device="cpu"))
        f_c = (stack.enter_context(safe_open(str(path_c), framework="pt", device="cpu"))
               if path_c is not None and method == "add_difference" else None)
        keys_a, keys_b = set(f_a.keys()), set(f_b.keys())
        keys_c = set(f_c.keys()) if f_c is not None else set()
        arch = detect_architecture(keys_a)["arch"]
        dtype_policy = resolve_output_dtype(output_dtype, arch, save_fp16)
        if arch == "cosmos":
            canon_a, lookup_a, _ = _cosmos_canonicalize_input(keys_a)
            canon_b, lookup_b, _ = _cosmos_canonicalize_input(keys_b)
            canon_c, lookup_c, _ = (_cosmos_canonicalize_input(keys_c) if f_c is not None
                                    else (set(), {}, {}))
            all_keys = sorted(canon_a | canon_b | canon_c)
        else:
            lookup_a = {k: k for k in keys_a}
            lookup_b = {k: k for k in keys_b}
            lookup_c = {k: k for k in keys_c}
            all_keys = sorted(keys_a | keys_b | keys_c)
        total = len(all_keys)
        job.update(keys_total=total)
        for i, key in enumerate(all_keys):
            _tick(job, i, total, key, start)
            phys_a, phys_b = lookup_a.get(key), lookup_b.get(key)
            phys_c = lookup_c.get(key) if f_c is not None else None
            if _should_copy_from_a_without_merge(key, arch):
                source = (f_a, phys_a) if phys_a else (f_b, phys_b) if phys_b else (f_c, phys_c)
                if source[1] is None:
                    continue
                output[key] = source[0].get_tensor(source[1])
                continue
            key_alpha = alpha
            if block_weights:
                key_alpha = block_weights.get(classify_key(key, arch), alpha)
            if f_c is not None:
                if phys_a and phys_b and phys_c:
                    t_a = f_a.get_tensor(phys_a).float()
                    t_b = f_b.get_tensor(phys_b).float()
                    t_c = f_c.get_tensor(phys_c).float()
                    if t_a.shape != t_b.shape or t_a.shape != t_c.shape:
                        t_out = t_a
                    elif not (torch.isfinite(t_a).all() and torch.isfinite(t_b).all()
                              and torch.isfinite(t_c).all()):
                        t_out = t_a
                    else:
                        # Add Difference: A + alpha * (B - C). The subtraction in
                        # fp32 -- catastrophic cancellation in fp16.
                        t_out = t_a + key_alpha * (t_b - t_c)
                    del t_b, t_c
                elif phys_a:
                    t_out = f_a.get_tensor(phys_a)
                elif phys_b:
                    t_out = f_b.get_tensor(phys_b)
                else:
                    t_out = f_c.get_tensor(phys_c)
            elif phys_a and phys_b:
                t_a = f_a.get_tensor(phys_a).float()
                t_b = f_b.get_tensor(phys_b).float()
                if t_a.shape != t_b.shape:
                    t_out = t_a
                elif not torch.isfinite(t_a).all() or not torch.isfinite(t_b).all():
                    # Keep whichever side is intact; A when both are broken.
                    t_out = t_b if (not torch.isfinite(t_a).all() and torch.isfinite(t_b).all()) else t_a
                else:
                    t_out = _merge_pair(t_a, t_b, key_alpha, method, stats)
                del t_b
            elif phys_a:
                t_out = f_a.get_tensor(phys_a)
            else:
                t_out = f_b.get_tensor(phys_b)
            output[key] = _cast_output_tensor(t_out, dtype_policy, key, arch)
    recipe = _stamp({
        "method": method,
        "model_a": names.get("a", path_a.name), "model_b": names.get("b", path_b.name),
        "alpha": alpha, "block_weights": block_weights, "fp16": save_fp16,
        "output_dtype": dtype_policy,
        "output_dtype_requested": output_dtype or ("auto" if save_fp16 else "fp32"),
        "architecture": arch,
    })
    if path_c is not None:
        recipe["model_c"] = names.get("c", path_c.name)
    if arch == "cosmos":
        recipe["llm_adapter_policy"] = "Copied from Model A; not merged"
        recipe["license_note"] = "Inherits CircleStone Labs Non-Commercial + NVIDIA Open Model License"
    job.update(current_key="(saving...)", progress=0.95)
    _save(output, output_path, recipe)
    del output
    return {"filename": output_path.name, "elapsed": round(time.time() - start, 1),
            "recipe": recipe, "stats": stats}


# -- LoRA baking (`:2555-3466`) ---------------------------------------------

_LORA_SUFFIXES = (
    ".lora_up.weight", ".lora_down.weight", ".lora_mid.weight",
    "_lora.up.weight", "_lora.down.weight",
    ".lora_A.weight", ".lora_B.weight",
    ".lora.up.weight", ".lora.down.weight",
    ".lora_A", ".lora_B",
    ".lora_linear_layer.up.weight", ".lora_linear_layer.down.weight",
    ".lora_A.default.weight", ".lora_B.default.weight",
    ".hada_w1_a", ".hada_w1_b", ".hada_w2_a", ".hada_w2_b",
    ".hada_t1", ".hada_t2",
    ".lokr_w1", ".lokr_w2", ".lokr_w1_a", ".lokr_w1_b",
    ".lokr_w2_a", ".lokr_w2_b", ".lokr_t2",
    ".oft_blocks", ".rescale",
    ".a1.weight", ".a2.weight", ".b1.weight", ".b2.weight",
    ".alpha", ".dora_scale",
    ".reshape_weight", ".w_norm", ".b_norm",
    ".diff", ".diff_b", ".set_weight",
)

_CKPT_PREFIXES = {
    "unet_sd15": "model.diffusion_model.",
    "te_sd15": "cond_stage_model.model.transformer.text_model.",
    "te_sd15_alt": "cond_stage_model.transformer.text_model.",
    "te1_sdxl": "conditioner.embedders.0.transformer.",
    "te2_sdxl": "conditioner.embedders.1.model.transformer.",
    "vae": "first_stage_model.",
}

_LORA_PREFIXES = {
    "lora_unet_": ["unet_sd15"],
    "lora_te_": ["te_sd15", "te_sd15_alt"],
    "lora_te1_": ["te1_sdxl"],
    "lora_te2_": ["te2_sdxl"],
}

#: `_SDXL_COMPVIS_TO_DIFFUSERS` (`:2689-2739`).
_SDXL_COMPVIS_TO_DIFFUSERS = {
    "input_blocks.0.0": "conv_in",
    "input_blocks.1.0": "down_blocks.0.resnets.0",
    "input_blocks.2.0": "down_blocks.0.resnets.1",
    "input_blocks.3.0.op": "down_blocks.0.downsamplers.0.conv",
    "input_blocks.4.0": "down_blocks.1.resnets.0",
    "input_blocks.4.1": "down_blocks.1.attentions.0",
    "input_blocks.5.0": "down_blocks.1.resnets.1",
    "input_blocks.5.1": "down_blocks.1.attentions.1",
    "input_blocks.6.0.op": "down_blocks.1.downsamplers.0.conv",
    "input_blocks.7.0": "down_blocks.2.resnets.0",
    "input_blocks.7.1": "down_blocks.2.attentions.0",
    "input_blocks.8.0": "down_blocks.2.resnets.1",
    "input_blocks.8.1": "down_blocks.2.attentions.1",
    "middle_block.0": "mid_block.resnets.0",
    "middle_block.1": "mid_block.attentions.0",
    "middle_block.2": "mid_block.resnets.1",
    "output_blocks.0.0": "up_blocks.0.resnets.0",
    "output_blocks.0.1": "up_blocks.0.attentions.0",
    "output_blocks.1.0": "up_blocks.0.resnets.1",
    "output_blocks.1.1": "up_blocks.0.attentions.1",
    "output_blocks.2.0": "up_blocks.0.resnets.2",
    "output_blocks.2.1": "up_blocks.0.attentions.2",
    "output_blocks.2.2": "up_blocks.0.upsamplers.0",
    "output_blocks.3.0": "up_blocks.1.resnets.0",
    "output_blocks.3.1": "up_blocks.1.attentions.0",
    "output_blocks.4.0": "up_blocks.1.resnets.1",
    "output_blocks.4.1": "up_blocks.1.attentions.1",
    "output_blocks.5.0": "up_blocks.1.resnets.2",
    "output_blocks.5.1": "up_blocks.1.attentions.2",
    "output_blocks.5.2": "up_blocks.1.upsamplers.0",
    "output_blocks.6.0": "up_blocks.2.resnets.0",
    "output_blocks.7.0": "up_blocks.2.resnets.1",
    "output_blocks.8.0": "up_blocks.2.resnets.2",
    "out.0": "conv_norm_out",
    "out.2": "conv_out",
    "time_embed.0": "time_embedding.linear_1",
    "time_embed.2": "time_embedding.linear_2",
    "label_emb.0.0": "add_embedding.linear_1",
    "label_emb.0.2": "add_embedding.linear_2",
}
_SDXL_CV_PREFIXES_SORTED = sorted(_SDXL_COMPVIS_TO_DIFFUSERS, key=len, reverse=True)


def _lora_bases(lora_keys: set) -> set:
    bases = set()
    for key in lora_keys:
        for suffix in _LORA_SUFFIXES:
            if key.endswith(suffix):
                bases.add(key[:-len(suffix)])
                break
    return bases


def _detect_adapter_type(lora_keys: set, base: str) -> str:
    def has(suffix: str) -> bool:
        return f"{base}{suffix}" in lora_keys

    if has(".hada_w1_a"):
        return "loha"
    if has(".lokr_w1") or has(".lokr_w1_a"):
        return "lokr"
    if has(".oft_blocks"):
        return "oft"
    if has(".a1.weight"):
        return "glora"
    if (has(".lora_up.weight") or has("_lora.up.weight") or has(".lora_B.weight")
            or has(".lora.up.weight") or has(".lora_B") or has(".lora_B.default.weight")
            or has(".lora_linear_layer.up.weight")):
        return "lora"
    if has(".diff"):
        return "diff"
    if has(".set_weight"):
        return "set"
    if has(".w_norm"):
        return "norm"
    return "unknown"


def build_key_map(ckpt_keys: set, lora_keys: set, arch: str) -> dict[str, str]:
    """`_build_key_map` (`:2747-2905`): LoRA base name -> checkpoint key."""

    key_map: dict[str, str] = {}
    bases = _lora_bases(lora_keys)

    # Kohya / A1111: lora_unet_*, lora_te_*, lora_te1_*, lora_te2_*.
    for ckpt_key in ckpt_keys:
        if not ckpt_key.endswith(".weight"):
            continue
        for lora_prefix, prefix_names in _LORA_PREFIXES.items():
            for prefix_name in prefix_names:
                ckpt_prefix = _CKPT_PREFIXES[prefix_name]
                if ckpt_key.startswith(ckpt_prefix):
                    inner = ckpt_key[len(ckpt_prefix):-len(".weight")]
                    base = lora_prefix + inner.replace(".", "_")
                    if base in bases:
                        key_map[base] = ckpt_key
                        break

    if arch in ("flux1", "flux2", "sd3"):
        for ckpt_key in ckpt_keys:
            if ckpt_key.endswith(".weight") and ckpt_key.startswith("model.diffusion_model."):
                inner = ckpt_key[len("model.diffusion_model."):-len(".weight")]
                if inner in bases:
                    key_map[inner] = ckpt_key
                if f"transformer.{inner}" in bases:
                    key_map[f"transformer.{inner}"] = ckpt_key
                lycoris = f"lycoris_{inner.replace('.', '_')}"
                if lycoris in bases:
                    key_map[lycoris] = ckpt_key

    if arch == "cosmos":
        prefixes = ("model.diffusion_model.", "diffusion_model.", "pipe.dit.", "dit.", "net.")
        for ckpt_key in ckpt_keys:
            if not ckpt_key.endswith(".weight") or _is_llm_adapter_key(ckpt_key):
                continue
            inner = next((ckpt_key[len(p):-len(".weight")] for p in prefixes
                          if ckpt_key.startswith(p)), ckpt_key[:-len(".weight")])
            underscore = inner.replace(".", "_")
            candidates = [inner, *(f"{p}{inner}".rstrip(".") for p in prefixes),
                          f"lycoris_{underscore}", f"lora_unet_{underscore}"]
            for candidate in candidates:
                if candidate and candidate in bases and candidate not in key_map:
                    key_map[candidate] = ckpt_key
                    break

    for base in bases:
        if base in key_map:
            continue
        if f"{base}.weight" in ckpt_keys:
            key_map[base] = f"{base}.weight"
        if f"model.diffusion_model.{base}.weight" in ckpt_keys:
            key_map[base] = f"model.diffusion_model.{base}.weight"
        if base.startswith("diffusion_model.") and f"model.{base}.weight" in ckpt_keys:
            key_map[base] = f"model.{base}.weight"
        if base.startswith("text_encoders.") and f"{base[len('text_encoders.'):]}.weight" in ckpt_keys:
            key_map[base] = f"{base[len('text_encoders.'):]}.weight"

    if arch == "sdxl" and bases - set(key_map):
        prefix = "model.diffusion_model."
        for ckpt_key in ckpt_keys:
            if not ckpt_key.startswith(prefix) or not ckpt_key.endswith(".weight"):
                continue
            inner = ckpt_key[len(prefix):-len(".weight")]
            for cv_prefix in _SDXL_CV_PREFIXES_SORTED:
                if inner == cv_prefix or inner.startswith(cv_prefix + "."):
                    mapped = _SDXL_COMPVIS_TO_DIFFUSERS[cv_prefix] + inner[len(cv_prefix):]
                    base = "lora_unet_" + mapped.replace(".", "_")
                    if base in bases and base not in key_map:
                        key_map[base] = ckpt_key
                    break
    return key_map


def compute_lora_delta(weight: Any, base: str, adapter_type: str, lora: dict,
                       strength: float = 1.0) -> Any:
    """`_compute_lora_delta` (`:2908-3177`): the patched weight, or None."""

    import torch

    def get(suffix: str) -> Any:
        return lora.get(f"{base}{suffix}")

    alpha_tensor = get(".alpha")
    alpha = alpha_tensor.item() if alpha_tensor is not None else None
    dora_scale = get(".dora_scale")
    try:
        if adapter_type == "lora":
            up = down = None
            for up_s, down_s in ((".lora_up.weight", ".lora_down.weight"),
                                 ("_lora.up.weight", "_lora.down.weight"),
                                 (".lora_B.weight", ".lora_A.weight"),
                                 (".lora.up.weight", ".lora.down.weight"),
                                 (".lora_B", ".lora_A"),
                                 (".lora_B.default.weight", ".lora_A.default.weight"),
                                 (".lora_linear_layer.up.weight", ".lora_linear_layer.down.weight")):
                up, down = get(up_s), get(down_s)
                if up is not None and down is not None:
                    break
            if up is None or down is None:
                return None
            up, down = up.float(), down.float()
            mid = get(".lora_mid.weight")
            scale = (alpha / down.shape[0]) if alpha is not None else 1.0
            if mid is not None:
                mid = mid.float()
                final_shape = [down.shape[1], down.shape[0], mid.shape[2], mid.shape[3]]
                down = torch.mm(down.transpose(0, 1).flatten(start_dim=1),
                                mid.transpose(0, 1).flatten(start_dim=1)
                                ).reshape(final_shape).transpose(0, 1)
            lora_diff = torch.mm(up.flatten(start_dim=1), down.flatten(start_dim=1)
                                 ).reshape(weight.shape) * scale
        elif adapter_type == "loha":
            w1a, w1b, w2a, w2b = (get(".hada_w1_a"), get(".hada_w1_b"),
                                  get(".hada_w2_a"), get(".hada_w2_b"))
            if any(x is None for x in (w1a, w1b, w2a, w2b)):
                return None
            w1a, w1b, w2a, w2b = w1a.float(), w1b.float(), w2a.float(), w2b.float()
            scale = (alpha / w1b.shape[0]) if alpha is not None else 1.0
            t1, t2 = get(".hada_t1"), get(".hada_t2")
            if t1 is not None and t2 is not None:
                m1 = torch.einsum("i j k l, j r, i p -> p r k l", t1.float(), w1b, w1a)
                m2 = torch.einsum("i j k l, j r, i p -> p r k l", t2.float(), w2b, w2a)
            else:
                m1, m2 = torch.mm(w1a, w1b), torch.mm(w2a, w2b)
            lora_diff = (m1 * m2).reshape(weight.shape) * scale
        elif adapter_type == "lokr":
            w1, w2 = get(".lokr_w1"), get(".lokr_w2")
            w1_a, w1_b, w2_a, w2_b = (get(".lokr_w1_a"), get(".lokr_w1_b"),
                                      get(".lokr_w2_a"), get(".lokr_w2_b"))
            t2 = get(".lokr_t2")
            dim = None
            if w1 is None and w1_a is not None and w1_b is not None:
                dim = w1_b.shape[0]
                w1 = torch.mm(w1_a.float(), w1_b.float())
            elif w1 is not None:
                w1 = w1.float()
            if w2 is None and w2_a is not None and w2_b is not None:
                dim = w2_b.shape[0]
                w2 = (torch.einsum("i j k l, j r, i p -> p r k l", t2.float(), w2_b.float(), w2_a.float())
                      if t2 is not None else torch.mm(w2_a.float(), w2_b.float()))
            elif w2 is not None:
                w2 = w2.float()
            if w1 is None or w2 is None:
                return None
            scale = (alpha / dim) if (alpha is not None and dim is not None) else 1.0
            if len(w2.shape) == 4:
                w1 = w1.unsqueeze(2).unsqueeze(2)
            lora_diff = torch.kron(w1, w2).reshape(weight.shape) * scale
        elif adapter_type == "oft":
            blocks = get(".oft_blocks")
            if blocks is None:
                return None
            blocks = blocks.float()
            rescale = get(".rescale")
            if blocks.ndim == 4:
                boft_m, _, boft_b, _ = blocks.shape
                eye = torch.eye(boft_b, device=blocks.device, dtype=blocks.dtype)
                q = blocks - blocks.transpose(-1, -2)
                if alpha is not None and alpha > 0:
                    norm = torch.norm(q) + 1e-8
                    if norm > alpha:
                        q = q * alpha / norm
                r = (eye + q) @ (eye - q).float().inverse()
                inp = weight.float()
                r_b = boft_b // 2
                for i in range(boft_m):
                    k = 2 ** i * r_b
                    inp = inp.unflatten(0, (-1, 2, k)).transpose(1, 2).flatten(0, 2).unflatten(0, (-1, boft_b))
                    inp = torch.einsum("b i j, b j ...-> b i ...", r[i], inp)
                    inp = inp.flatten(0, 1).unflatten(0, (-1, k, 2)).transpose(1, 2).flatten(0, 2)
                if rescale is not None:
                    inp = inp * rescale.float()
                lora_diff = inp - weight.float()
            else:
                block_num, block_size, _ = blocks.shape
                eye = torch.eye(block_size, device=blocks.device, dtype=blocks.dtype)
                q = blocks - blocks.transpose(1, 2)
                if alpha is not None and alpha > 0:
                    norm = torch.norm(q) + 1e-8
                    if norm > alpha:
                        q = q * alpha / norm
                r = (eye + q) @ (eye - q).float().inverse()
                _, *shape = weight.shape
                rotated = torch.einsum("k n m, k n ... -> k m ...", r,
                                       weight.float().view(block_num, block_size, *shape)).flatten(0, 1)
                if rescale is not None:
                    rotated = rotated * rescale.float()
                lora_diff = rotated - weight.float()
        elif adapter_type == "glora":
            a1, a2, b1, b2 = get(".a1.weight"), get(".a2.weight"), get(".b1.weight"), get(".b2.weight")
            if any(x is None for x in (a1, a2, b1, b2)):
                return None
            a1, a2 = a1.float().flatten(start_dim=1), a2.float().flatten(start_dim=1)
            b1, b2 = b1.float().flatten(start_dim=1), b2.float().flatten(start_dim=1)
            old = b2.shape[1] == b1.shape[0] == a1.shape[0] == a2.shape[1]
            rank = a1.shape[0] if old else a2.shape[0]
            scale = (alpha / rank) if alpha is not None else 1.0
            flat = weight.float().flatten(start_dim=1)
            lora_diff = (torch.mm(b2, b1) + torch.mm(torch.mm(flat, a2), a1) if old
                         else torch.mm(torch.mm(flat, a1), a2) + torch.mm(b1, b2))
            lora_diff = lora_diff.reshape(weight.shape) * scale
        elif adapter_type == "diff":
            diff = get(".diff")
            if diff is None or diff.shape != weight.shape:
                return None
            lora_diff = diff.float()
        elif adapter_type == "set":
            value = get(".set_weight")
            return None if value is None else value.float().to(dtype=weight.dtype)
        elif adapter_type == "norm":
            value = get(".w_norm")
            if value is None or value.shape != weight.shape:
                return None
            lora_diff = value.float()
        else:
            return None

        if dora_scale is not None and adapter_type not in ("diff", "set", "norm"):
            # Neo's `weight_decompose` (`backend/patcher/lora.py:35-55`).
            scale_f = dora_scale.float().to(device=weight.device)
            calc = weight.float() + lora_diff
            if scale_f.shape[0] == calc.shape[0]:
                norm = (weight.float().reshape(weight.shape[0], -1).norm(dim=1, keepdim=True)
                        .reshape(weight.shape[0], *[1] * (weight.dim() - 1)))
            else:
                norm = (calc.transpose(0, 1).reshape(calc.shape[1], -1).norm(dim=1, keepdim=True)
                        .reshape(calc.shape[1], *[1] * (calc.dim() - 1)).transpose(0, 1))
            calc *= scale_f / (norm + torch.finfo(weight.dtype).eps)
            result = (weight.float() + strength * (calc - weight.float())) if strength != 1.0 else calc
            return result.to(dtype=weight.dtype)
        return (weight.float() + strength * lora_diff).to(dtype=weight.dtype)
    except Exception:  # noqa: BLE001 - one adapter failing skips that key
        return None


def bake_lora(job: Any, ckpt_path: Path, loras: list[tuple[Path, float, str]],
              output_path: Path, *, save_fp16: bool = True, output_dtype: str = "auto",
              checkpoint_name: str = "") -> dict[str, Any]:
    """`bake_lora` (`:3239-3466`). `loras` is `[(path, strength, display name)]`."""

    from safetensors import safe_open
    from safetensors.torch import load_file

    start = time.time()
    with safe_open(str(ckpt_path), framework="pt", device="cpu") as f:
        ckpt_keys = set(f.keys())
    arch = detect_architecture(ckpt_keys)["arch"]
    dtype_policy = resolve_output_dtype(output_dtype, arch, save_fp16)
    prepared = []
    for lora_path, strength, _name in loras:
        lora = load_file(str(lora_path), device="cpu")
        lora_keys = set(lora)
        key_map = build_key_map(ckpt_keys, lora_keys, arch)
        if key_map:
            prepared.append((lora, {ckpt: base for base, ckpt in key_map.items()}, lora_keys, strength))
    if not prepared:
        raise ValueError("No LoRA matched this checkpoint's keys — check that the LoRA "
                         "is for the same model family.")
    if arch == "cosmos":
        _, lookup, _ = _cosmos_canonicalize_input(ckpt_keys)
        all_keys = sorted(lookup)
    else:
        lookup = {k: k for k in ckpt_keys}
        all_keys = sorted(ckpt_keys)
    output: dict[str, Any] = {}
    applied = errors = 0
    total = len(all_keys)
    job.update(keys_total=total)
    with safe_open(str(ckpt_path), framework="pt", device="cpu") as f:
        for i, key in enumerate(all_keys):
            _tick(job, i, total, key, start)
            phys = lookup[key]
            weight = f.get_tensor(phys)
            if _should_copy_from_a_without_merge(key, arch):
                output[key] = weight
                continue
            for lora, ckpt_to_lora, lora_keys, strength in prepared:
                base = ckpt_to_lora.get(phys)
                if base is None:
                    continue
                result = compute_lora_delta(weight, base, _detect_adapter_type(lora_keys, base),
                                            lora, strength)
                if result is None:
                    errors += 1
                else:
                    weight = result
                    applied += 1
            output[key] = _cast_output_tensor(weight, dtype_policy, key, arch)
    recipe = _stamp({
        "operation": "lora_bake",
        "checkpoint": checkpoint_name or ckpt_path.name,
        "loras": [{"filename": name, "strength": s} for _p, s, name in loras],
        "adapters_applied": applied, "fp16": save_fp16, "output_dtype": dtype_policy,
        "output_dtype_requested": output_dtype or ("auto" if save_fp16 else "fp32"),
    })
    if arch == "cosmos":
        recipe["architecture"] = "cosmos"
        recipe["llm_adapter_policy"] = "Pass-through; LoRA targets on llm_adapter ignored"
        recipe["license_note"] = "Inherits CircleStone Labs Non-Commercial + NVIDIA Open Model License"
    job.update(current_key="(saving...)", progress=0.95)
    _save(output, output_path, recipe)
    return {"filename": output_path.name, "elapsed": round(time.time() - start, 1),
            "recipe": recipe, "applied": applied, "errors": errors}


# -- VAE baking (`:3518-3663`) ----------------------------------------------

def bake_vae(job: Any, ckpt_path: Path, vae_path: Path, output_path: Path, *,
             save_fp16: bool = True, output_dtype: str = "auto",
             checkpoint_name: str = "", vae_name: str = "") -> dict[str, Any]:
    from safetensors import safe_open
    from safetensors.torch import load_file

    start = time.time()
    with safe_open(str(ckpt_path), framework="pt", device="cpu") as f:
        all_keys = sorted(f.keys())
    arch = detect_architecture(set(all_keys))["arch"]
    if arch == "cosmos":
        raise ValueError("VAE baking is not supported for Anima/Cosmos checkpoints. "
                         "Anima loads its VAE separately.")
    dtype_policy = resolve_output_dtype(output_dtype, arch, save_fp16)
    vae = load_file(str(vae_path), device="cpu")
    vae_map: dict[str, str] = {}
    for vae_key in vae:
        vae_map[VAE_PREFIX + vae_key] = vae_key
        if vae_key.startswith(VAE_PREFIX):
            vae_map[vae_key] = vae_key
    output: dict[str, Any] = {}
    replaced = 0
    total = len(all_keys)
    job.update(keys_total=total)
    with safe_open(str(ckpt_path), framework="pt", device="cpu") as f:
        for i, key in enumerate(all_keys):
            _tick(job, i, total, key, start)
            if key in vae_map:
                # Always fp32: an fp16 VAE decode produces NaN.
                output[key] = vae[vae_map[key]].float()
                replaced += 1
            else:
                output[key] = _cast_output_tensor(f.get_tensor(key), dtype_policy, key, arch)
    recipe = _stamp({
        "operation": "vae_bake", "checkpoint": checkpoint_name or ckpt_path.name,
        "vae": vae_name or vae_path.name, "keys_replaced": replaced, "fp16": save_fp16,
        "output_dtype": dtype_policy,
        "output_dtype_requested": output_dtype or ("auto" if save_fp16 else "fp32"),
    })
    job.update(current_key="(saving...)", progress=0.95)
    _save(output, output_path, recipe)
    return {"filename": output_path.name, "elapsed": round(time.time() - start, 1),
            "recipe": recipe, "replaced": replaced}
