"""Header-only compatibility preflight for one checkpoint / text-encoder / VAE set.

Every rule below is transcribed from retained Forge source, not from filename
convention or outside knowledge:

* `modules_forge/packages/huggingface_guess/detection.py:201-221` -- how Forge
  recognizes an Anima DiT and what it asserts about its shapes;
* `modules_forge/packages/huggingface_guess/model_list.py:464-489` -- what the
  `Anima` guess declares: `latent.Wan21`, `vae_key_prefix = ["vae."]`,
  `clip_target = {"qwen3_06b.transformer": "text_encoder"}`;
* `backend/loader.py:727-738` -- how an additional state dict is recognized as a
  Qwen3 text encoder and which size bucket it falls into;
* `backend/loader.py:527` -- how an additional state dict is recognized as a
  Wan-family VAE;
* `backend/diffusion_engine/anima.py:20-22` -- the engine builds
  `CLIP(model_dict={"qwen3_06b": ...})` and `VAE(..., is_wan=True)`.

Nothing here loads a tensor. Decisions come from key names, declared shapes, and
declared dtypes in the safetensors header.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model_intake import (
    PREFLIGHT_COMPATIBLE,
    PREFLIGHT_INCOMPATIBLE,
    PREFLIGHT_INCONCLUSIVE,
    PREFLIGHT_UNSUPPORTED,
    count_keys_with,
    shape_of,
)


#: Exactly the candidates `detection.py:438-446` tries, in its order, with its
#: ">5 keys" threshold and its `"model."` fallback. Transcribed rather than
#: invented: an independent guess here would answer a different question than
#: the loader will.
UNET_PREFIX_CANDIDATES = ("model.diffusion_model.", "net.")
UNET_PREFIX_FALLBACK = "model."
UNET_PREFIX_MIN_KEYS = 5

ANIMA_FAMILY = "anima"
ANIMA_REPO = "circlestone-labs/Anima"

#: detection.py asserts both of these for Anima.
ANIMA_EXPECTED_IN_CHANNELS = 16
ANIMA_EXPECTED_MODEL_CHANNELS = 2048

#: model_list.Anima -> crossattn_emb_channels, and the Qwen3-0.6B hidden size.
ANIMA_CROSSATTN_EMB_CHANNELS = 1024

#: model_list.Anima -> latent_format = latent.Wan21, a 16-channel latent.
ANIMA_LATENT_CHANNELS = 16

QWEN3_SIZE_BY_HIDDEN = {1024: "06b", 2560: "4b"}
ANIMA_REQUIRED_QWEN3_SIZE = "06b"


@dataclass
class ComponentFinding:
    """One component's verdict, with the evidence that produced it."""

    role: str
    file_id: str
    recognized: bool
    detected_as: str
    expected: str
    evidence: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "file_id": self.file_id,
            "recognized": self.recognized,
            "detected_as": self.detected_as,
            "expected": self.expected,
            "evidence": list(self.evidence),
            "mismatches": list(self.mismatches),
        }


@dataclass
class TripletVerdict:
    verdict: str
    family: str
    selected_loader: str
    findings: list[ComponentFinding]
    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        return self.verdict == PREFLIGHT_COMPATIBLE

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "family": self.family,
            "selected_loader": self.selected_loader,
            "findings": [finding.to_dict() for finding in self.findings],
            "blocking": list(self.blocking),
            "notes": list(self.notes),
        }


def unet_prefix(header: dict[str, object]) -> str:
    """Reproduce `detection.unet_prefix_from_state_dict` exactly."""
    for prefix in UNET_PREFIX_CANDIDATES:
        if sum(1 for key in header if key.startswith(prefix)) > UNET_PREFIX_MIN_KEYS:
            return prefix
    return UNET_PREFIX_FALLBACK


def inspect_checkpoint(file_id: str, header: dict[str, object]) -> ComponentFinding:
    """Apply detection.py's Anima rules to a checkpoint header."""

    finding = ComponentFinding(
        role="checkpoint",
        file_id=file_id,
        recognized=False,
        detected_as="unrecognized",
        expected=f"{ANIMA_FAMILY} DiT per detection.py:201",
    )

    prefix = unet_prefix(header)
    finding.evidence.append(
        f"detection.unet_prefix_from_state_dict would select prefix {prefix!r}"
    )
    if f"{prefix}blocks.0.mlp.layer1.weight" not in header:
        finding.mismatches.append(
            f"'{prefix}blocks.0.mlp.layer1.weight' absent, so detection.py:201 "
            "would not classify this as Anima"
        )
        return finding
    finding.evidence.append(
        f"'{prefix}blocks.0.mlp.layer1.weight' present -- detection.py:201 Anima branch"
    )

    adapter_key = f"{prefix}llm_adapter.blocks.0.cross_attn.q_proj.weight"
    if adapter_key not in header:
        finding.mismatches.append(
            "'llm_adapter.blocks.0.cross_attn.q_proj.weight' absent; "
            "detection.py asserts it for Anima"
        )
        return finding
    finding.evidence.append("llm_adapter cross-attention projection present")

    embedder = f"{prefix}x_embedder.proj.1.weight"
    shape = shape_of(header, embedder)
    if shape is None or len(shape) < 2:
        finding.mismatches.append(
            "'x_embedder.proj.1.weight' missing or not 2-D, so in_channels and "
            "model_channels cannot be derived"
        )
        return finding

    model_channels = int(shape[0])
    in_channels = int(shape[1] / 4) - 1
    finding.evidence.append(
        f"x_embedder.proj.1.weight shape {list(shape)} -> "
        f"in_channels={in_channels}, model_channels={model_channels}"
    )
    if in_channels != ANIMA_EXPECTED_IN_CHANNELS:
        finding.mismatches.append(
            f"in_channels {in_channels} != {ANIMA_EXPECTED_IN_CHANNELS}; "
            "detection.py asserts this and would raise"
        )
    if model_channels != ANIMA_EXPECTED_MODEL_CHANNELS:
        finding.mismatches.append(
            f"model_channels {model_channels} != {ANIMA_EXPECTED_MODEL_CHANNELS}; "
            "detection.py asserts this and would raise"
        )

    adapter_tensors = count_keys_with(header, "llm_adapter.")
    finding.evidence.append(f"{adapter_tensors} llm_adapter tensors present")

    # The adapter's cross-attention projection is what the text encoder feeds,
    # so its input width is the checkpoint's own statement of the embedding
    # size it expects. Recorded here and cross-checked against the encoder.
    adapter_shape = shape_of(header, adapter_key)
    if adapter_shape and len(adapter_shape) >= 2:
        finding.evidence.append(
            f"llm_adapter cross_attn.q_proj shape {list(adapter_shape)} -> "
            f"expects {int(adapter_shape[1])}-wide text embeddings"
        )

    if not finding.mismatches:
        finding.recognized = True
        finding.detected_as = ANIMA_FAMILY
    return finding


def inspect_text_encoder(
    file_id: str, header: dict[str, object]
) -> ComponentFinding:
    """Apply loader.py's additional-state-dict rules to a text-encoder header."""

    finding = ComponentFinding(
        role="text_encoder",
        file_id=file_id,
        recognized=False,
        detected_as="unrecognized",
        expected="Qwen3-0.6B per loader.py:734 and Anima.clip_target",
    )

    norm_key = "model.layers.0.post_attention_layernorm.weight"
    q_norm_key = "model.layers.0.self_attn.q_norm.weight"

    if norm_key not in header:
        finding.mismatches.append(
            f"'{norm_key}' absent; loader.py would not route this to a qwen3 bucket"
        )
        return finding
    if q_norm_key not in header:
        finding.mismatches.append(
            f"'{q_norm_key}' absent; loader.py routes such a file to "
            "ministral3_3b or gemma2_2b, not qwen3"
        )
        return finding

    shape = shape_of(header, norm_key)
    if shape is None or not shape:
        finding.mismatches.append(
            "post_attention_layernorm has no usable shape, so the qwen3 size "
            "bucket cannot be derived"
        )
        return finding

    hidden = int(shape[0])
    size = QWEN3_SIZE_BY_HIDDEN.get(hidden, "8b")
    finding.evidence.append(
        f"post_attention_layernorm hidden size {hidden} -> qwen3_{size}"
    )
    finding.evidence.append("self_attn.q_norm present, confirming the qwen3 branch")

    if size != ANIMA_REQUIRED_QWEN3_SIZE:
        finding.mismatches.append(
            f"loader.py would prefix these tensors 'text_encoders.qwen3_{size}.', "
            f"but Anima.clip_target expects 'qwen3_06b.transformer'"
        )
        return finding
    if hidden != ANIMA_CROSSATTN_EMB_CHANNELS:
        finding.mismatches.append(
            f"hidden size {hidden} != Anima crossattn_emb_channels "
            f"{ANIMA_CROSSATTN_EMB_CHANNELS}"
        )
        return finding

    finding.evidence.append(
        f"hidden size matches Anima crossattn_emb_channels "
        f"{ANIMA_CROSSATTN_EMB_CHANNELS}"
    )
    layers = len(
        {
            key.split(".")[2]
            for key in header
            if key.startswith("model.layers.") and len(key.split(".")) > 2
        }
    )
    if layers:
        finding.evidence.append(f"{layers} transformer layers declared")
    finding.recognized = True
    finding.detected_as = f"qwen3_{size}"
    return finding


def inspect_vae(file_id: str, header: dict[str, object]) -> ComponentFinding:
    """Apply loader.py:527's VAE rules, and check the Wan family Anima needs."""

    finding = ComponentFinding(
        role="vae",
        file_id=file_id,
        recognized=False,
        detected_as="unrecognized",
        expected="Wan-family VAE (Anima uses latent.Wan21 and VAE(is_wan=True))",
    )

    wan_marker = "decoder.middle.0.residual.0.gamma"
    generic_marker = "decoder.conv_in.weight"
    diffusers_marker = "decoder.up_blocks.0.resnets.0.norm1.weight"

    has_wan = wan_marker in header
    has_generic = generic_marker in header
    has_diffusers = diffusers_marker in header

    if has_wan:
        finding.evidence.append(
            f"'{wan_marker}' present -- loader.py:527 recognizes this as the "
            "Wan VAE layout"
        )
    if has_generic:
        finding.evidence.append(f"'{generic_marker}' present")
    if has_diffusers:
        finding.evidence.append(
            f"'{diffusers_marker}' present -- diffusers layout, converted by "
            "convert_vae_state_dict before classification"
        )

    if not (has_wan or has_generic or has_diffusers):
        finding.mismatches.append(
            "none of the VAE marker keys loader.py:521-527 tests for are present, "
            "so this file would not replace the checkpoint's VAE at all"
        )
        return finding

    # Latent channel count: Anima is Wan21, which is 16-channel.
    latent_channels: int | None = None
    for key in ("decoder.conv1.weight", "decoder.conv_in.weight"):
        shape = shape_of(header, key)
        if shape and len(shape) >= 2:
            latent_channels = int(shape[1])
            finding.evidence.append(
                f"{key} shape {list(shape)} -> {latent_channels} latent channels"
            )
            break

    if has_wan:
        if latent_channels is not None and latent_channels != ANIMA_LATENT_CHANNELS:
            finding.mismatches.append(
                f"{latent_channels} latent channels, but Anima declares "
                f"latent.Wan21 with {ANIMA_LATENT_CHANNELS}"
            )
            finding.detected_as = "wan_vae_wrong_latent_width"
            return finding
        finding.evidence.append(
            f"latent width matches Anima's latent.Wan21 ({ANIMA_LATENT_CHANNELS})"
        )
        finding.recognized = True
        finding.detected_as = "wan_vae"
        return finding

    finding.mismatches.append(
        "recognized as a VAE, but not by the Wan marker "
        f"'{wan_marker}'. Anima declares latent.Wan21 and constructs "
        "VAE(is_wan=True), so a non-Wan VAE layout would not match the engine"
    )
    finding.detected_as = "non_wan_vae"
    return finding


def evaluate_triplet(
    checkpoint: ComponentFinding,
    text_encoder: ComponentFinding,
    vae: ComponentFinding,
) -> TripletVerdict:
    """Combine three component findings into one honest verdict."""

    findings = [checkpoint, text_encoder, vae]
    blocking: list[str] = []
    notes: list[str] = []

    if not checkpoint.recognized:
        # Without a recognized architecture there is no loader to select, and
        # the other two cannot be judged against anything.
        return TripletVerdict(
            verdict=PREFLIGHT_UNSUPPORTED,
            family="unknown",
            selected_loader="none",
            findings=findings,
            blocking=[
                "checkpoint architecture not recognized by "
                "huggingface_guess.detection",
                *checkpoint.mismatches,
            ],
            notes=[
                "No retained Forge diffusion engine matches this checkpoint, so "
                "no loader could be selected."
            ],
        )

    selected_loader = "backend.diffusion_engine.anima.Anima via backend.loader.forge_loader"

    for finding in (text_encoder, vae):
        if not finding.recognized:
            blocking.extend(f"{finding.role}: {reason}" for reason in finding.mismatches)

    # Cross-component check: the checkpoint's llm_adapter states the embedding
    # width it consumes, and the encoder's hidden size states what it produces.
    # Either alone can look fine while the pair does not fit.
    adapter_width = _stated_width(checkpoint, "expects ")
    encoder_width = _stated_width(text_encoder, "hidden size ")
    if adapter_width and encoder_width and adapter_width != encoder_width:
        blocking.append(
            f"checkpoint expects {adapter_width}-wide text embeddings but the "
            f"supplied encoder produces {encoder_width}"
        )
    elif adapter_width and encoder_width:
        notes.append(
            f"checkpoint and text encoder agree on a {adapter_width}-wide "
            "embedding"
        )

    if blocking:
        return TripletVerdict(
            verdict=PREFLIGHT_INCOMPATIBLE,
            family=ANIMA_FAMILY,
            selected_loader=selected_loader,
            findings=findings,
            blocking=blocking,
            notes=[
                "The checkpoint is a recognized Anima DiT, so the loader is "
                "known; one or more supplied components do not match what that "
                "loader requires."
            ],
        )

    notes.append(
        "All three components match the rules the retained loader itself "
        "applies. Configs and tokenizers for "
        f"{ANIMA_REPO} are bundled locally under backend/huggingface/, so no "
        "download is required."
    )
    return TripletVerdict(
        verdict=PREFLIGHT_COMPATIBLE,
        family=ANIMA_FAMILY,
        selected_loader=selected_loader,
        findings=findings,
        notes=notes,
    )


def _stated_width(finding: ComponentFinding, marker: str) -> int | None:
    """Recover a width this component's own evidence already recorded."""
    for line in finding.evidence:
        index = line.find(marker)
        if index < 0:
            continue
        digits = ""
        for char in line[index + len(marker):]:
            if char.isdigit():
                digits += char
            elif digits:
                break
        if digits:
            return int(digits)
    return None


def inconclusive(reason: str, findings: list[ComponentFinding]) -> TripletVerdict:
    return TripletVerdict(
        verdict=PREFLIGHT_INCONCLUSIVE,
        family="unknown",
        selected_loader="none",
        findings=findings,
        blocking=[reason],
        notes=["Preflight could not establish compatibility truthfully."],
    )
