"""What architecture a checkpoint is, and which components it already carries.

Ported from the shipping Studio extension, not reasoned out here. The oracle is
`Reference/Forge-Studio-main/Forge-Studio-main/scripts/`:

* `studio_workshop.py:399-470` -- `detect_architecture`, transcribed branch for
  branch and constant for constant;
* `studio_api.py:4769-4822` -- the `/studio/check_model_te` header scan, whose
  two prefix tests decide whether the owner is shown a Text Encoder dropdown at
  all.

Several of the extension's choices look like slips and are NOT slips, so they
are kept exactly:

**The gates use substrings, the counters use anchored patterns.** Each branch is
entered on `any("input_blocks." in k ...)` and then measured with
`_RE_INP`, which requires a LEADING DOT. A key that mentions `input_blocks.` at
the very start of the string enters the branch and contributes nothing to the
maximum, so the count is 0 and the model reads as SDXL. That asymmetry is the
extension's, and a checkpoint classified by the extension must classify the same
way here.

**`_count_extras` matches text-encoder keys by substring; the needs-TE test
matches by prefix.** `cond_stage_model.` anywhere in a key counts toward
`text_encoder_key_count`, but only a key that STARTS with it means the
checkpoint bundles an encoder. So a model can report a non-zero count and still
be told it needs one. That is deliberate in the extension -- the count is a
diagnostic, the prefix test is the decision.

**`blocks` is hardcoded for the UNet families.** 20 for SDXL and 26 for SD 1.5,
rather than the number just counted. It is a label, not a measurement.

**The fallback is permissive.** Anything unrecognised is `"unknown"` with zero
blocks, never an error. A detector that refuses is a detector that hides a
model the loader would have taken.

Studio-shaped where it must be: this reads a header through
`model_intake.read_safetensors_header`, so the bounded-length and truncation
guards already in the headless boundary apply, and it never returns a path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model_intake import read_safetensors_header

#: `studio_workshop.py:67`.
VAE_PREFIX = "first_stage_model."

#: `studio_workshop.py:77`. The Cosmos-Predict2 conditioning adapter appears
#: both as a top-level key and wrapped under `model.diffusion_model.`, which is
#: why the test below is a substring rather than a prefix.
LLM_ADAPTER_PREFIX = "llm_adapter."

#: `studio_workshop.py:373-379`. Transcribed, including the leading dots.
_RE_INP = re.compile(r"\.input_blocks\.(\d+)\.")
_RE_MID = re.compile(r"\.middle_block\.")
_RE_OUT = re.compile(r"\.output_blocks\.(\d+)\.")
_RE_DOUBLE = re.compile(r"double_blocks\.(\d+)\.")
_RE_SINGLE = re.compile(r"single_blocks\.(\d+)\.")
_RE_JOINT = re.compile(r"joint_blocks\.(\d+)\.")
_RE_COSMOS = re.compile(r"\.blocks\.(\d+)\.")

#: `studio_api.py:4795-4806`. The two tests that decide whether the owner sees a
#: Text Encoder dropdown. A checkpoint carrying either prefix bundles its own
#: encoder and must not be asked for one.
_TEXT_ENCODER_PREFIXES = ("cond_stage_model.", "conditioner.")
_VAE_PREFIXES = (VAE_PREFIX,)

#: What `check_model_te` answers when it cannot tell. FAIL OPEN, matching
#: `studio_api.py:4780-4785` and `:4820-4822`: three of its five return paths
#: are this. Hiding the row costs an owner a control they did not need; showing
#: an unsatisfiable one costs them a generation that refuses for no stated
#: reason.
NEUTRAL: dict[str, Any] = {
    "needs_te": False, "needs_vae": False, "arch": "unknown",
}

#: `studio_api.py:4791-4792`. A GGUF header cannot be scanned this way, so the
#: extension assumes both are external and SAYS SO rather than guessing quietly.
GGUF: dict[str, Any] = {
    "needs_te": True, "needs_vae": True, "arch": "unknown",
    "note": "GGUF — cannot detect, assuming external TE+VAE",
}


def _is_llm_adapter_key(key: str) -> bool:
    """`studio_workshop.py:80-83`. True for any wrapping of the adapter."""

    return key.startswith(LLM_ADAPTER_PREFIX) or ".llm_adapter." in key


def _count_extras(keys: set[str]) -> dict[str, int]:
    """`studio_workshop.py:382-396`. One pass, three counters.

    Substring tests, not prefixes -- see the module docstring.
    """

    text_encoder = vae = adapter = 0
    for key in keys:
        if "cond_stage_model." in key or "conditioner." in key:
            text_encoder += 1
        if key.startswith(VAE_PREFIX) or ".first_stage_model." in key:
            vae += 1
        if _is_llm_adapter_key(key):
            adapter += 1
    return {"text_encoder": text_encoder, "vae": vae, "llm_adapter": adapter}


def detect_architecture(keys: set[str]) -> dict[str, Any]:
    """`studio_workshop.py:399-470`. Branch order is part of the contract.

    FLUX before SD3 before UNet before Cosmos, because the tests are not
    mutually exclusive and the first match wins.
    """

    extras = _count_extras(keys)
    common = {
        "has_text_encoder_keys": extras["text_encoder"] > 0,
        "text_encoder_key_count": extras["text_encoder"],
        "vae_key_count": extras["vae"],
        "llm_adapter_key_count": extras["llm_adapter"],
    }

    if any("double_blocks." in key for key in keys):
        double_max = single_max = -1
        for key in keys:
            found = _RE_DOUBLE.search(key)
            if found:
                double_max = max(double_max, int(found.group(1)))
            found = _RE_SINGLE.search(key)
            if found:
                single_max = max(single_max, int(found.group(1)))
        n_double = double_max + 1 if double_max >= 0 else 0
        n_single = single_max + 1 if single_max >= 0 else 0
        total = n_double + n_single
        if n_double <= 8 and n_single >= 40:
            return {"arch": "flux2", "blocks": total,
                    "details": f"FLUX.2 ({n_double} double + {n_single} single blocks)",
                    **common}
        return {"arch": "flux1", "blocks": total,
                "details": f"FLUX.1 ({n_double} double + {n_single} single blocks)",
                **common}

    if any("joint_blocks." in key for key in keys):
        joint_max = -1
        for key in keys:
            found = _RE_JOINT.search(key)
            if found:
                joint_max = max(joint_max, int(found.group(1)))
        n_joints = joint_max + 1 if joint_max >= 0 else 0
        return {"arch": "sd3", "blocks": n_joints,
                "details": f"SD3 MMDiT ({n_joints} joint blocks)",
                **common}

    if any("input_blocks." in key for key in keys):
        inp_max = -1
        for key in keys:
            found = _RE_INP.search(key)
            if found:
                inp_max = max(inp_max, int(found.group(1)))
        n_inp = inp_max + 1 if inp_max >= 0 else 0
        if n_inp <= 9:
            return {"arch": "sdxl", "blocks": 20,
                    "details": "SDXL (9+1+9 blocks, dual CLIP)",
                    **common}
        return {"arch": "sd15", "blocks": 26,
                "details": "SD 1.5 (12+1+12 blocks)",
                **common}

    # Cosmos-Predict2 (Anima, etc.) -- adaln_modulation_cross_attn is unique.
    if any("adaln_modulation_cross_attn" in key for key in keys):
        block_max = -1
        for key in keys:
            found = _RE_COSMOS.search(key)
            if found:
                block_max = max(block_max, int(found.group(1)))
        n_blocks = block_max + 1 if block_max >= 0 else 0
        te_count = extras["text_encoder"]
        if te_count > 0:
            # Surface the actual count so the owner can tell a 12-key residue
            # apart from a full 1190-key bundled text encoder at a glance.
            te_note = f"bundled TE: {te_count} keys"
        else:
            te_note = "external TE+VAE"
        return {"arch": "cosmos", "blocks": n_blocks,
                "details": f"Cosmos-Predict2 ({n_blocks} blocks, {te_note})",
                **common}

    return {"arch": "unknown", "blocks": 0,
            "details": "Unknown architecture", **common}


def component_needs(keys: set[str]) -> dict[str, Any]:
    """`studio_api.py:4795-4819`, given the keys already read.

    Split out from the file read so the decision can be tested against a set of
    key names without a safetensors file existing.
    """

    has_te = any(key.startswith(_TEXT_ENCODER_PREFIXES) for key in keys)
    has_vae = any(key.startswith(_VAE_PREFIXES) for key in keys)
    try:
        arch = str(detect_architecture(keys).get("arch") or "unknown")
    except Exception:  # noqa: BLE001 - detection is a nicety, never a blocker
        arch = "unknown"
    return {"needs_te": not has_te, "needs_vae": not has_vae, "arch": arch}


def inspect_checkpoint_file(path: Path, *, is_gguf: bool = False
                            ) -> dict[str, Any]:
    """What one checkpoint on disk needs, read from its header alone.

    Never raises. Every failure answers `NEUTRAL`, which is what the extension
    does on a missing model, an unreadable file, and an unexpected exception
    alike -- three of its five return paths.
    """

    if is_gguf or str(path).lower().endswith(".gguf"):
        return dict(GGUF)
    try:
        header = read_safetensors_header(Path(path))
    except Exception:  # noqa: BLE001 - see the docstring
        return dict(NEUTRAL)
    keys = {key for key in header if key != "__metadata__"}
    if not keys:
        return dict(NEUTRAL)
    return component_needs(keys)


__all__ = (
    "GGUF",
    "LLM_ADAPTER_PREFIX",
    "NEUTRAL",
    "VAE_PREFIX",
    "component_needs",
    "detect_architecture",
    "inspect_checkpoint_file",
)
