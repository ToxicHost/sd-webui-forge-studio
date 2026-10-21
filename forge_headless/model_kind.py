"""What kind of model file this is, read from its header. ON1.

Source review: Evidence/source-review/ON0-onboarding.md. A model LIBRARY folder
is scanned through its subfolders and every file is sorted by what it IS --
checkpoint, LoRA, VAE, text encoder, ControlNet, embedding, upscaler or Auto
Detail detector -- instead of by which folder the owner put it in.

**A checkpoint is what Neo recognises.** `huggingface_guess.guess` -- the call
`backend/loader.py:_load_unet` makes once a file is loaded -- reads tensor
SHAPES. A safetensors header lists every tensor's shape and dtype, so a
stand-in carrying only those (after the loader's own `preprocess_state_dict`
key rename) lets Neo name the family without reading a single weight. The
scanner and the loader therefore cannot disagree about what a checkpoint is.
ON0 measured it on the dev library: 8/8 checkpoints named (Anima, SDXL,
FLUX.2 Klein), every other kind refused.

**The other kinds are recognised by their key names**, from the formats their
own loaders read: LoRA (`.lora_down`/`.lora_up`, PEFT `lora_A`/`lora_B`,
LyCORIS `hada_`/`lokr_`, kohya `lora_unet_`/`lora_te`), ControlNet
(`control_model.`, `input_hint_block`, `controlnet_cond_embedding`, LLLite),
embeddings (a handful of `emb_params`/`clip_l`/`clip_g`/`string_to_param`
tensors), VAEs (encoder/decoder blocks), text encoders (transformer LM keys).

**Pickle files are never opened** (.ckpt/.pt/.pth/.bin): loading one runs
code. They are sorted by name and by the folder's hint only, at low
confidence. GGUF is read from its metadata (`general.architecture`).

Never raises: anything not recognised is `unknown`, which the library shows
in an "Unrecognized" list the owner can correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

KIND_CHECKPOINT = "checkpoint"
KIND_TEXT_ENCODER = "text_encoder"
KIND_VAE = "vae"
KIND_LORA = "lora"
KIND_CONTROLNET = "controlnet"
KIND_EMBEDDING = "embedding"
KIND_UPSCALER = "upscaler"
KIND_ADETAILER = "adetailer"
KIND_UNKNOWN = "unknown"

#: The kinds a library sorts into -- the catalogue's roles, plus unknown.
KINDS = (KIND_CHECKPOINT, KIND_TEXT_ENCODER, KIND_VAE, KIND_LORA, KIND_CONTROLNET,
         KIND_EMBEDDING, KIND_UPSCALER, KIND_ADETAILER, KIND_UNKNOWN)

SAFETENSORS = (".safetensors", ".sft")
PICKLE = (".ckpt", ".pt", ".pth", ".bin")
GGUF = (".gguf",)
MODEL_EXTENSIONS = SAFETENSORS + PICKLE + GGUF

#: Neo's model-config class names (`huggingface_guess/model_list.py`) -> the
#: family words Studio uses elsewhere. A family Neo knows and this table does
#: not keeps Neo's own name, lower-cased.
_NEO_FAMILY = {
    "SD15": "sd15", "SDXL": "sdxl", "SDXLRefiner": "sdxl_refiner", "Flux": "flux1",
    "FluxSchnell": "flux1", "Flux2K4B": "flux2", "Flux2K9B": "flux2", "Anima": "anima",
    "QwenImage": "qwen", "ZImage": "zimage", "Lumina2": "lumina2", "Chroma": "chroma",
    "ErnieImage": "ernie", "MOSSImage": "moss", "Krea2": "krea2", "Mugen": "mugen",
    "WAN21_T2V": "wan21", "WAN21_I2V": "wan21", "PiD": "pid",
}

#: A LoRA's family from kohya's training metadata (`ss_base_model_version`,
#: `modelspec.architecture`), checked in order; the first match wins.
_LORA_META_FAMILY = (
    ("sdxl", "sdxl"), ("stable-diffusion-xl", "sdxl"), ("anima", "anima"),
    ("flux2", "flux2"), ("flux.2", "flux2"), ("flux", "flux1"),
    ("sd_v1", "sd15"), ("sd1", "sd15"), ("stable-diffusion-v1", "sd15"),
    ("qwen", "qwen"), ("zimage", "zimage"), ("z-image", "zimage"),
    ("lumina", "lumina2"), ("chroma", "chroma"),
)

#: Pickle detectors and upscalers by name -- the file is never opened.
#: Detector markers only: `face_`/`hand_` alone also name OpenPose's
#: `hand_pose_model.pth`, which is not a detector (ON0).
_DETECTOR_NAME = re.compile(r"yolo|_seg\b|-seg\b|detect|mediapipe", re.I)
_UPSCALER_NAME = re.compile(r"esrgan|realesr|remacri|swinir|\bscunet|\bdat\b|\b[248]x[_-]|x[248]\b|upscal|ultrasharp|nmkd", re.I)

#: Key fragments only a diffusion model's own blocks carry (SD/SDXL UNet,
#: FLUX double/single blocks, MMDiT joint blocks, Anima's `net.blocks`).
_DIFFUSION_KEYS = ("model.diffusion_model.", "double_blocks.", "single_blocks.",
                   "joint_blocks.", "net.blocks.")

#: Folder names that say what a pickle file inside is (case-insensitive).
_FOLDER_HINTS = {
    "adetailer": KIND_ADETAILER, "ultralytics": KIND_ADETAILER, "bbox": KIND_ADETAILER,
    "segm": KIND_ADETAILER, "esrgan": KIND_UPSCALER, "realesrgan": KIND_UPSCALER,
    "upscale_models": KIND_UPSCALER, "upscaler": KIND_UPSCALER, "upscalers": KIND_UPSCALER,
    "swinir": KIND_UPSCALER, "dat": KIND_UPSCALER, "lora": KIND_LORA, "loras": KIND_LORA,
    "lycoris": KIND_LORA, "embeddings": KIND_EMBEDDING, "embedding": KIND_EMBEDDING,
    "textual_inversion": KIND_EMBEDDING, "controlnet": KIND_CONTROLNET, "vae": KIND_VAE,
    "stable-diffusion": KIND_CHECKPOINT, "checkpoints": KIND_CHECKPOINT,
}


@dataclass(frozen=True)
class Classification:
    """One file's kind, and how sure the scan is."""

    kind: str
    #: The model family it is or belongs to ("sdxl", "anima", "flux2"...), or "".
    family: str = ""
    #: "high" (Neo or unambiguous keys), "medium" (metadata or a weaker key
    #: rule), "low" (name or folder only).
    confidence: str = "low"
    #: What decided it, for the "Unrecognized" list and the logs.
    evidence: str = ""
    #: Checkpoints: whether a text encoder and a VAE must come from elsewhere.
    needs: dict[str, bool] = field(default_factory=dict)


class _Shape:
    """A tensor's shape and dtype from the header -- nothing else."""

    __slots__ = ("shape", "dtype")

    def __init__(self, shape: Any, dtype: Any) -> None:
        self.shape = shape
        self.dtype = dtype

    @property
    def ndim(self) -> int:
        return len(self.shape)


def _neo_guess(header: dict[str, Any]) -> str | None:
    """Neo's model-config class name for a header, or None when Neo does not
    recognise it as a diffusion model. Imports torch and Neo's guess lazily:
    only a scan needs them. Raises only if Neo itself cannot be imported."""

    import torch

    import huggingface_guess

    sd = {key: _Shape(torch.Size(value.get("shape") or []), torch.float16)
          for key, value in header.items() if key != "__metadata__" and isinstance(value, dict)}
    # `backend/loader.py:preprocess_state_dict`, exactly: a key rename.
    if not any(k.startswith(("model.diffusion_model.", "net.")) for k in sd):
        sd = {f"model.diffusion_model.{k}": v for k, v in sd.items()}
    try:
        config = huggingface_guess.guess(sd)
    except ModuleNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a value-reading branch (Lumina's std) on a stand-in
        return None
    return type(config).__name__


def _has(keys: set[str], *parts: str) -> bool:
    return any(part in key for key in keys for part in parts)


def lora_family(keys: set[str], metadata: dict[str, Any]) -> str:
    """Which family a LoRA was trained for: kohya metadata first, key names
    second, "" when neither says."""

    for field_name in ("ss_base_model_version", "modelspec.architecture", "ss_sd_model_name"):
        value = str(metadata.get(field_name) or "").lower()
        for needle, family in _LORA_META_FAMILY:
            if value and needle in value:
                return family
    if _has(keys, "lora_te2_", "te2."):
        return "sdxl"
    if _has(keys, "double_blocks", "single_blocks"):
        return "flux1"
    if _has(keys, "lora_unet_blocks_", "diffusion_model.blocks.", "net.blocks."):
        return "anima"
    if _has(keys, "lora_unet_input_blocks_", "lora_unet_down_blocks_"):
        return "sd15"
    return ""


def classify_header(header: dict[str, Any], *, name: str = "", hint: str = "",
                    neo_guess: Callable[[dict[str, Any]], str | None] | None = None,
                    needs: Callable[[set[str]], dict[str, Any]] | None = None) -> Classification:
    """A safetensors header (`model_intake.read_safetensors_header`) -> kind.

    `neo_guess` and `needs` are injectable so a test needs neither torch nor
    Neo; by default they are Neo's guess and `architecture.component_needs`.
    """

    metadata = header.get("__metadata__") if isinstance(header.get("__metadata__"), dict) else {}
    keys = {key for key in header if key != "__metadata__"}
    if not keys:
        return Classification(KIND_UNKNOWN, evidence="no tensors in the header")

    # Adapters first: a LoRA or ControlNet also carries UNet-looking names.
    if _has(keys, ".lora_down.", ".lora_up.", ".lora_A.", ".lora_B.", ".hada_w1", ".lokr_w1",
            "lora_unet_", "lora_te_", "lora_te1_", "lora_te2_") \
            or str(metadata.get("ss_network_module") or ""):
        return Classification(KIND_LORA, lora_family(keys, metadata), "high", "LoRA keys")
    if _has(keys, "control_model.", "input_hint_block", "controlnet_cond_embedding",
            "controlnet_blocks.", "lllite_", "_lllite"):
        return Classification(KIND_CONTROLNET, "", "high", "ControlNet keys")
    if len(keys) <= 4 and all(k.startswith(("emb_params", "clip_l", "clip_g", "string_to_param", "<"))
                              for k in keys):
        return Classification(KIND_EMBEDDING, "", "high", "embedding vectors")

    guess = neo_guess or _neo_guess
    try:
        neo = guess(header)
    except Exception:  # noqa: BLE001 - Neo unavailable: the key rules below decide
        neo = None
    if neo:
        family = _NEO_FAMILY.get(neo, neo.lower())
        try:
            if needs is None:
                from .architecture import component_needs as needs
            facts = needs(keys)
            required = {"text_encoder": bool(facts.get("needs_te")), "vae": bool(facts.get("needs_vae"))}
        except Exception:  # noqa: BLE001 - needs are a nicety, never a blocker
            required = {}
        return Classification(KIND_CHECKPOINT, family, "high", f"Neo recognises it ({neo})", required)

    # BEFORE the VAE and text-encoder rules: a full checkpoint carries its
    # VAE's decoder and its CLIP's layers too, so without Neo (not importable
    # yet, or a family it does not name) those rules would claim it.
    if _has(keys, *_DIFFUSION_KEYS):
        return Classification(KIND_CHECKPOINT, "", "medium", "diffusion-model keys Neo did not name")

    if _has(keys, "decoder.up", "decoder.mid", "decoder.conv_in", "decoder.upsamples", "decoder.middle",
            "encoder.down_blocks", "decoder.up_blocks"):
        return Classification(KIND_VAE, "", "medium", "encoder/decoder blocks")
    if _has(keys, "text_model.encoder.layers", "encoder.block.", "model.layers.", "transformer.resblocks",
            "layers.0.self_attn", "model.embed_tokens", "shared.weight"):
        return Classification(KIND_TEXT_ENCODER, "", "medium", "transformer language-model keys")
    if _has(keys, "model.0.weight", "conv_first.weight", "body.0.", "RRDB", "upconv1"):
        return Classification(KIND_UPSCALER, "", "medium", "upscaler network keys")
    return _by_name(name, hint, f"{len(keys)} tensors, none recognised")


def _by_name(name: str, hint: str, why: str) -> Classification:
    """Pickle files, and anything the keys did not decide: name, then the
    folder's hint. Always low confidence -- the owner can correct it."""

    stem = Path(name).stem
    if _DETECTOR_NAME.search(stem):
        return Classification(KIND_ADETAILER, "", "low", "detector-like name")
    if _UPSCALER_NAME.search(stem):
        return Classification(KIND_UPSCALER, "", "low", "upscaler-like name")
    if hint in KINDS and hint != KIND_UNKNOWN:
        return Classification(hint, "", "low", "the folder's hint")
    return Classification(KIND_UNKNOWN, "", "low", why)


def folder_hint(parts: tuple[str, ...]) -> str:
    """The kind a folder's NAME suggests (innermost wins), or ""."""

    for part in reversed(parts):
        kind = _FOLDER_HINTS.get(part.strip().lower())
        if kind:
            return kind
    return ""


def classify_file(path: Path, *, hint: str = "",
                  neo_guess: Callable[[dict[str, Any]], str | None] | None = None) -> Classification:
    """One file on disk -> kind. NEVER RAISES and never opens a pickle."""

    suffix = path.suffix.lower()
    name = path.name
    hint = hint or folder_hint(path.parent.parts[-3:])
    if suffix in PICKLE:
        return _by_name(name, hint, "a pickle file (never opened)")
    if suffix in GGUF:
        return _classify_gguf(path, hint)
    if suffix not in SAFETENSORS:
        return Classification(KIND_UNKNOWN, evidence="not a model file")
    try:
        from .model_intake import read_safetensors_header

        header = read_safetensors_header(path)
    except Exception as exc:  # noqa: BLE001 - unreadable is "unknown", not a crash
        return Classification(KIND_UNKNOWN, evidence=f"unreadable header ({getattr(exc, 'code', type(exc).__name__)})")
    return classify_header(header, name=name, hint=hint, neo_guess=neo_guess)


#: GGUF `general.architecture` values (city96's diffusion GGUFs, llama.cpp's
#: text-encoder GGUFs) -> (kind, family).
_GGUF_ARCH = {
    "flux": (KIND_CHECKPOINT, "flux1"), "sdxl": (KIND_CHECKPOINT, "sdxl"), "sd1": (KIND_CHECKPOINT, "sd15"),
    "sd3": (KIND_CHECKPOINT, "sd3"), "aura": (KIND_CHECKPOINT, "auraflow"), "ltxv": (KIND_CHECKPOINT, "ltxv"),
    "hyvid": (KIND_CHECKPOINT, "hunyuan"), "wan": (KIND_CHECKPOINT, "wan21"), "lumina2": (KIND_CHECKPOINT, "lumina2"),
    "qwen_image": (KIND_CHECKPOINT, "qwen"), "chroma": (KIND_CHECKPOINT, "chroma"), "cosmos": (KIND_CHECKPOINT, "anima"),
    "t5": (KIND_TEXT_ENCODER, ""), "t5encoder": (KIND_TEXT_ENCODER, ""), "llama": (KIND_TEXT_ENCODER, ""),
    "qwen2": (KIND_TEXT_ENCODER, ""), "qwen2vl": (KIND_TEXT_ENCODER, ""), "qwen3": (KIND_TEXT_ENCODER, ""),
    "gemma2": (KIND_TEXT_ENCODER, ""), "gemma3": (KIND_TEXT_ENCODER, ""), "clip": (KIND_TEXT_ENCODER, ""),
}


def _classify_gguf(path: Path, hint: str) -> Classification:
    """GGUF: `general.architecture` from the metadata, read by the GGUF
    package Neo already carries (`modules_forge/packages/gguf`)."""

    try:
        import gguf

        reader = gguf.GGUFReader(str(path), "r")
        arch_field = reader.fields.get("general.architecture")
        arch = ""
        if arch_field is not None and arch_field.data:
            arch = bytes(arch_field.parts[arch_field.data[0]]).decode("utf-8", "replace").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable GGUF is unknown, not a crash
        return _by_name(path.name, hint, "GGUF metadata unreadable")
    kind, family = _GGUF_ARCH.get(arch, (None, ""))
    if kind is None:
        return _by_name(path.name, hint, f"GGUF architecture {arch!r} not recognised")
    needs = {"text_encoder": True, "vae": True} if kind == KIND_CHECKPOINT else {}
    return Classification(kind, family, "high", f"GGUF architecture {arch!r}", needs)


__all__ = (
    "Classification", "KINDS", "KIND_ADETAILER", "KIND_CHECKPOINT", "KIND_CONTROLNET",
    "KIND_EMBEDDING", "KIND_LORA", "KIND_TEXT_ENCODER", "KIND_UNKNOWN", "KIND_UPSCALER",
    "KIND_VAE", "MODEL_EXTENSIONS", "classify_file", "classify_header", "folder_hint", "lora_family",
)
