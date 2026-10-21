"""Regional prompting: a prompt per painted region. RG1.

Two methods, chosen per job (`runner_for`):

* **Masked conditioning**, every family. Each region becomes a copy of the
  job's own cond with the region's text and a mask; Neo blends the conds per
  pixel (`sampling_function.calc_cond_uncond_batch`, weighted by
  `mask * strength`). Measured on SDXL, Anima and FLUX.2 Klein in RG0.
* **Attention Couple**, SD 1.5 and SDXL (the Extension's mode there,
  `studio_attention_couple.py`): region tokens appended to cross-attention,
  a spatial bias steering each region's tokens to its area, and self-attention
  isolation between regions. Rebuilt WITHOUT the Extension's module state and
  with a token span per region (its `tokens_per_region` came from region 0, so
  a long region prompt shifted every later region onto the wrong words).

Both are runners on `p.scripts`, installed on the main pass only. Neo resets
`forge_objects` and calls `process_before_every_sampling` before EVERY
sampling call (`processing.py`: txt2img base and Hires, img2img), and each
call here patches a fresh UNet clone: the txt2img Hires pass keeps the
regions (same `p`), Auto Detail and the image-operation Hires pass do not
(their own `p`, no runner), and nothing outlives the job.

THE GUARD. Neo multiplies CFG by the sum of every cond's strength
(`sampling_function_inner`: `edit_strength`), the right rule for AND parts
that cover the whole image and the wrong one for regions: N regions would
multiply guidance by N+1, and at CFG 1 (Klein) the whole prediction. After
adding regions every strength is scaled by one common factor so the sum is
what it was; the per-pixel blend is a ratio and does not move.

Sources and measurements: Evidence/source-review/RG0-regional-prompting.md.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .contracts import HeadlessError

#: How much of the global prompt's own cond remains inside a region (Regional
#: Prompter's default; RG0 measured no difference between 0.0 and 0.5).
BASE_RATIO = 0.2

# Attention Couple's tuned constants, the Extension's v7 values.
BASE_BIAS_STRENGTH = 8.0
NEGATIVE_BIAS_STRENGTH = -10.0
BG_BIAS_STRENGTH = 4.0
SELF_NEGATIVE_BIAS = -8.0
SELF_BIAS_START_FRAC = 0.2
SELF_BIAS_END_FRAC = 0.8
MAX_SELF_ATTN_SPATIAL = 4096
SELF_BG_THRESHOLD = 0.3
MAX_BIAS_SCALE = 12.0

_EXTRA_NETWORK = re.compile(r"<(\w+):([^>]+)>")


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def feather(mask: Any, short_side: int) -> Any:
    """The Extension's feathering: Gaussian, kernel 5% of the short side,
    odd, at least 3, sigma a third of it."""

    import cv2
    import numpy as np

    k = max(3, int(short_side * 0.05))
    if k % 2 == 0:
        k += 1
    return cv2.GaussianBlur(np.asarray(mask, dtype=np.float32), (k, k), k / 3.0)


def region_text(global_prompt: str, region_prompt: str) -> str:
    """The text a region is encoded with: the global prompt, then the
    region's (the Extension prepends it the same way). LoRA and other
    `<name:...>` tags are removed: they act on the whole job through the
    global prompt, and encoded here they would be read as words."""

    text = _EXTRA_NETWORK.sub("", region_prompt or "").strip().strip(",").strip()
    glob = _EXTRA_NETWORK.sub("", global_prompt or "").strip()
    if not text:
        return glob
    if not glob:
        return text
    return glob + (" " if glob.endswith(",") else ", ") + text


def scale_strengths(conds: list[dict], before: float) -> None:
    """The guard: one common factor so the strength sum is `before` again."""

    after = sum(float(c.get("strength", 1.0)) for c in conds)
    if after <= 0 or math.isclose(after, before):
        return
    factor = before / after
    for c in conds:
        c["strength"] = float(c.get("strength", 1.0)) * factor


def _state() -> Any:
    """Neo's `shared.state`, only if the engine already loaded it -- never
    imported from here (importing it parses argv and loads Neo)."""

    import sys

    shared = sys.modules.get("modules.shared")
    return getattr(shared, "state", None) if shared is not None else None


def _current_step() -> int:
    try:
        return int(getattr(_state(), "sampling_step", 0) or 0)
    except Exception:  # noqa: BLE001 - no state, the first schedule entry
        return 0


def _progress() -> float:
    """Where this sampling call is, 0..1, from Neo's own counters (Studio's
    bridge restarts them per pass). The Extension read the same pair through
    `on_cfg_denoiser`."""

    try:
        state = _state()
        total = int(getattr(state, "sampling_steps", 0) or 0)
        step = int(getattr(state, "sampling_step", 0) or 0)
        return min(1.0, max(0.0, step / total)) if total > 0 else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


class _EncodedRegions:
    """Each region's text through Neo's own prompt path, once per job.

    `get_multicond_learned_conditioning` + `reconstruct_multicond_batch` is
    what the main prompt goes through (`processing.py` setup_conds): it
    handles every family's encoder output (a dict for SDXL, a list of
    per-prompt tensors for Anima, Klein and most others), AND parts and
    `[a:b:x]` schedules.
    """

    def __init__(self, p: Any, regions: tuple[Any, ...]) -> None:
        from modules import prompt_parser
        from modules.prompt_parser import SdConditioning

        engine = p.sd_model
        # FLUX.2's encoder rebuilds the reference latents for a positive
        # prompt and consumes the init latent (flux2.py get_learned_conditioning):
        # both are put back, or a Klein edit would lose its own picture.
        from backend.args import dynamic_args

        saved_refs = list(getattr(dynamic_args, "ref_latents", []) or [])
        saved_ini = getattr(engine, "ini_latent", None)
        global_prompt = str(getattr(p, "prompt", "") or "")
        self.multiconds = []
        try:
            for region in regions:
                text = SdConditioning(
                    [region_text(global_prompt, region.prompt)],
                    width=p.width, height=p.height,
                    distilled_cfg_scale=getattr(p, "distilled_cfg_scale", None))
                self.multiconds.append(
                    prompt_parser.get_multicond_learned_conditioning(engine, text, p.steps))
        finally:
            dynamic_args.ref_latents = saved_refs
            if hasattr(engine, "ini_latent"):
                engine.ini_latent = saved_ini

    def at_step(self, index: int, step: int) -> tuple[Any, Any]:
        from modules import prompt_parser

        return prompt_parser.reconstruct_multicond_batch(self.multiconds[index], step)


def _pixel_masks(regions: tuple[Any, ...], width: int, height: int) -> Any:
    """[N, 1, H, W] feathered float masks at the picture's size."""

    import numpy as np
    import torch

    short = min(int(width), int(height))
    return torch.from_numpy(np.stack([feather(r.mask, short) for r in regions])).unsqueeze(1)


class _RegionalRunner:
    """Shared runner plumbing: every hook not used is a no-op, as Neo's base
    `Script` does, and failures reach the owner by name."""

    def __init__(self, regions: tuple[Any, ...]) -> None:
        self.regions = tuple(regions)

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _noop

    @staticmethod
    def _named(error: Exception) -> HeadlessError:
        if isinstance(error, HeadlessError):
            return error
        return HeadlessError("REGIONS_FAILED",
                             f"Regional prompting failed: {type(error).__name__}: {error}")


class MaskedRegionsRunner(_RegionalRunner):
    """Masked conditioning: one masked cond per region, every family."""

    def __init__(self, regions: tuple[Any, ...], *, base_ratio: float = BASE_RATIO) -> None:
        super().__init__(regions)
        self.base_ratio = float(base_ratio)
        self._encoded: _EncodedRegions | None = None
        self._pixels: Any = None
        self._cache: dict[tuple, Any] = {}

    def process(self, p: Any, *_args: Any, **_kwargs: Any) -> None:
        try:
            self._encoded = _EncodedRegions(p, self.regions)
            self._pixels = _pixel_masks(self.regions, p.width, p.height)
        except Exception as error:  # noqa: BLE001 - named, not swallowed
            raise self._named(error) from error

    def process_before_every_sampling(self, p: Any, **_kwargs: Any) -> None:
        unet = p.sd_model.forge_objects.unet.clone()
        unet.add_conditioning_modifier(self.modifier)
        p.sd_model.forge_objects.unet = unet

    def _masks_for(self, x: Any) -> Any:
        """[N, h, w] at the latent's spatial size: its last two axes."""

        import torch.nn.functional as F

        key = (tuple(x.shape[-2:]), x.device, x.dtype)
        if key not in self._cache:
            down = F.interpolate(self._pixels.to(device=x.device, dtype=x.dtype),
                                 size=tuple(x.shape[-2:]), mode="bilinear", align_corners=False)
            self._cache[key] = down[:, 0].clamp(0, 1)
        return self._cache[key]

    @staticmethod
    def cond_mask(mask_hw: Any, x: Any) -> Any:
        """[1, h, w] for an image latent; [1, T, h, w] for a video-shaped one
        ([B, C, T, h, w]: Anima). `get_area_and_mult` asserts mask dims 1-2
        against latent dims 2-3 (PATCH-006 lets it repeat a 4-D mask)."""

        if x.ndim == 5:
            return mask_hw[None, None].expand(1, x.shape[2], *mask_hw.shape).contiguous()
        return mask_hw[None]

    def modifier(self, model, x, timestep, uncond, cond, cond_scale, model_options, seed):
        try:
            return model, x, timestep, uncond, self.regional_conds(x, cond), cond_scale, model_options, seed
        except Exception as error:  # noqa: BLE001
            raise self._named(error) from error

    def regional_conds(self, x: Any, cond: list[dict]) -> list[dict]:
        from backend.sampling.condition import compile_weighted_conditions

        masks = self._masks_for(x)
        union = masks.sum(dim=0).clamp(0, 1)
        before = sum(float(c.get("strength", 1.0)) for c in cond)
        out = []
        base_mask = self.cond_mask(1.0 - (1.0 - self.base_ratio) * union, x)
        for c in cond:
            base = dict(c)
            base["mask"] = base_mask
            out.append(base)
        template = cond[0]
        step = _current_step()
        for index, region in enumerate(self.regions):
            conds_list, stacked = self._encoded.at_step(index, step)
            for compiled in compile_weighted_conditions(stacked, conds_list):
                # A COPY of the job's cond with only the text swapped, so
                # `c_concat` (inpaint models), `control` (ControlNet) and
                # `guidance` carry over and the conds still batch together.
                part = dict(template)
                model_conds = dict(template["model_conds"])
                for key in ("c_crossattn", "y"):
                    if key in compiled["model_conds"]:
                        model_conds[key] = compiled["model_conds"][key]
                part["model_conds"] = model_conds
                part["cross_attn"] = compiled.get("cross_attn")
                if "pooled_output" in compiled:
                    part["pooled_output"] = compiled["pooled_output"]
                part["mask"] = self.cond_mask(masks[index], x)
                part["strength"] = float(compiled.get("strength", 1.0)) * float(region.weight)
                out.append(part)
        scale_strengths(out, before)
        return out


class AttentionCoupleRunner(_RegionalRunner):
    """The Extension's Attention Couple (v7) on SD 1.5 and SDXL UNets."""

    def __init__(self, regions: tuple[Any, ...]) -> None:
        super().__init__(regions)
        self._context: Any = None
        self._spans: list[tuple[int, int]] = []
        self._pixels: Any = None
        self._scales: list[float] = []

    def process(self, p: Any, *_args: Any, **_kwargs: Any) -> None:
        import torch

        try:
            encoded = _EncodedRegions(p, self.regions)
            contexts = []
            for index in range(len(self.regions)):
                _conds_list, stacked = encoded.at_step(index, 0)
                tensor = stacked["crossattn"] if isinstance(stacked, dict) else stacked
                contexts.append(tensor[:1])
            # A SPAN PER REGION. The Extension assumed every region had
            # region 0's token count; a prompt over 75 tokens is two chunks.
            start = 0
            self._spans = []
            for tensor in contexts:
                self._spans.append((start, start + int(tensor.shape[1])))
                start += int(tensor.shape[1])
            self._context = torch.cat(contexts, dim=1)
            self._pixels = _pixel_masks(self.regions, p.width, p.height)
            total = float(self._pixels.shape[2] * self._pixels.shape[3])
            self._scales = []
            for index, region in enumerate(self.regions):
                area = float((self._pixels[index] > 0.5).sum().item())
                fraction = max(area / total, 0.001)
                self._scales.append(min(1.0 / math.sqrt(fraction) * float(region.weight), MAX_BIAS_SCALE))
        except Exception as error:  # noqa: BLE001
            raise self._named(error) from error

    def process_before_every_sampling(self, p: Any, **_kwargs: Any) -> None:
        unet = p.sd_model.forge_objects.unet.clone()
        patch, cross, self_attn = self._patches()
        unet.set_model_attn2_patch(patch)
        for block_name, number in self._blocks(unet):
            unet.set_model_attn2_replace(cross, block_name, number)
            unet.set_model_attn1_replace(self_attn, block_name, number)
        p.sd_model.forge_objects.unet = unet

    @staticmethod
    def _blocks(unet: Any) -> list[tuple[str, int]]:
        """Every (block, index) with a SpatialTransformer, from the UNet itself."""

        model = unet.model.diffusion_model
        found = []
        for index, block in enumerate(model.input_blocks):
            if any(type(layer).__name__ == "SpatialTransformer" for layer in block):
                found.append(("input", index))
        if any(type(layer).__name__ == "SpatialTransformer" for layer in model.middle_block):
            found.append(("middle", 0))
        for index, block in enumerate(model.output_blocks):
            if any(type(layer).__name__ == "SpatialTransformer" for layer in block):
                found.append(("output", index))
        return found

    @staticmethod
    def _sdp(q, k, v, heads, dim_head):
        import torch.nn.functional as F

        bs, seq_q, _ = q.shape
        seq_k = k.shape[1]
        q = q.view(bs, seq_q, heads, dim_head).permute(0, 2, 1, 3)
        k = k.view(bs, seq_k, heads, dim_head).permute(0, 2, 1, 3)
        v = v.view(bs, seq_k, heads, dim_head).permute(0, 2, 1, 3)
        out = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0, is_causal=False)
        return out.permute(0, 2, 1, 3).reshape(bs, seq_q, heads * dim_head)

    def _masks_at(self, num_spatial: int, original_shape: list[int], device, dtype, cache: dict):
        """[N, S] masks at one attention layer's resolution, cached per S."""

        import torch.nn.functional as F

        if num_spatial not in cache:
            lat_h, lat_w = int(original_shape[2]), int(original_shape[3])
            scale = math.ceil(math.log2(math.sqrt(lat_h * lat_w / num_spatial)))
            h, w = lat_h, lat_w
            for _ in range(max(scale, 0)):
                h, w = math.ceil(h / 2), math.ceil(w / 2)
            down = F.interpolate(self._pixels.to(device=device, dtype=dtype), size=(h, w),
                                 mode="bilinear", align_corners=False)
            cache[num_spatial] = down.view(down.shape[0], -1)
        return cache[num_spatial]

    def _patches(self):
        import torch

        context = self._context
        spans = list(self._spans)
        scales = list(self._scales)
        cross_cache: dict = {}
        self_cache: dict = {}
        base_tokens = {"n": 0}

        @torch.inference_mode()
        def attn2_patch(q, k, v, extra_options):
            chunks = len(extra_options.get("cond_or_uncond", [0, 1]))
            bs = q.shape[0] // max(chunks, 1)
            base_tokens["n"] = k.shape[1]
            region_ctx = context.to(device=k.device, dtype=k.dtype)
            if region_ctx.shape[0] != bs:
                region_ctx = region_ctx.expand(bs, -1, -1)
            ks, vs = k.chunk(chunks, dim=0), v.chunk(chunks, dim=0)
            return (q, torch.cat([torch.cat([part, region_ctx], dim=1) for part in ks], dim=0),
                    torch.cat([torch.cat([part, region_ctx], dim=1) for part in vs], dim=0))

        @torch.inference_mode()
        def attn2_replace(q, k, v, extra_options):
            heads, dim_head = extra_options["n_heads"], extra_options["dim_head"]
            kinds = extra_options.get("cond_or_uncond", [0, 1])
            bs = q.shape[0] // max(len(kinds), 1)
            base = base_tokens["n"]
            outputs = []
            for index, kind in enumerate(kinds):
                q_c, k_c, v_c = (t[index * bs:(index + 1) * bs] for t in (q, k, v))
                if kind == 1:
                    # Uncond: region tokens stripped, or CFG cancels the split.
                    outputs.append(self._sdp(q_c, k_c[:, :base], v_c[:, :base], heads, dim_head))
                else:
                    outputs.append(self._biased_cross(q_c, k_c, v_c, heads, dim_head, base,
                                                      extra_options["original_shape"], spans,
                                                      scales, cross_cache))
            return torch.cat(outputs, dim=0)

        @torch.inference_mode()
        def attn1_replace(q, k, v, extra_options):
            heads, dim_head = extra_options["n_heads"], extra_options["dim_head"]
            num_spatial = q.shape[1]
            progress = _progress()
            if (progress < SELF_BIAS_START_FRAC or progress > SELF_BIAS_END_FRAC
                    or num_spatial > MAX_SELF_ATTN_SPATIAL):
                return self._sdp(q, k, v, heads, dim_head)
            if num_spatial not in self_cache:
                self_cache[num_spatial] = self._self_bias(num_spatial, extra_options["original_shape"],
                                                          q.device, q.dtype)
            bias = self_cache[num_spatial]
            if bias is None:
                return self._sdp(q, k, v, heads, dim_head)
            bs = q.shape[0]
            q_mh = q.view(bs, num_spatial, heads, dim_head).permute(0, 2, 1, 3)
            k_mh = k.view(bs, num_spatial, heads, dim_head).permute(0, 2, 1, 3)
            v_mh = v.view(bs, num_spatial, heads, dim_head).permute(0, 2, 1, 3)
            logits = torch.matmul(q_mh, k_mh.transpose(-2, -1)) * dim_head ** -0.5 + bias[None, None]
            out = torch.matmul(torch.softmax(logits, dim=-1), v_mh)
            return out.permute(0, 2, 1, 3).reshape(bs, num_spatial, heads * dim_head)

        return attn2_patch, attn2_replace, attn1_replace

    def _self_bias(self, num_spatial, original_shape, device, dtype):
        """Different regions may not attend to each other; background is the
        bridge that carries light and perspective between them."""

        masks = self._masks_at(num_spatial, original_shape, device, dtype, {})
        if masks.shape[0] < 2:
            return None
        values, ids = masks.max(dim=0)
        ids = ids.clone()
        ids[values < SELF_BG_THRESHOLD] = -1
        if int((ids >= 0).sum().item()) < 2:
            return None
        background = ids == -1
        suppress = (~background[:, None]) & (~background[None, :]) & (ids[:, None] != ids[None, :])
        return suppress.to(dtype=dtype) * SELF_NEGATIVE_BIAS

    def _biased_cross(self, q, k, v, heads, dim_head, base, original_shape, spans, scales, cache):
        import torch

        mult = 1.0 - _progress() ** 2
        if mult < 0.01:
            return self._sdp(q, k, v, heads, dim_head)
        num_spatial = q.shape[1]
        masks = self._masks_at(num_spatial, original_shape, q.device, q.dtype, cache)
        bias = torch.zeros(1, num_spatial, k.shape[1], device=q.device, dtype=q.dtype)
        background = 1.0 - masks.sum(dim=0).clamp(0.0, 1.0)
        bias[0, :, :base] += (background * BG_BIAS_STRENGTH * mult)[:, None]
        for index, (start, end) in enumerate(spans):
            column = masks[index]
            region = column * scales[index] * BASE_BIAS_STRENGTH * mult
            outside = (1.0 - column) * NEGATIVE_BIAS_STRENGTH * mult
            bias[0, :, base + start: base + end] += (region + outside)[:, None]
        bs, seq_k = q.shape[0], k.shape[1]
        q_mh = q.view(bs, num_spatial, heads, dim_head).permute(0, 2, 1, 3)
        k_mh = k.view(bs, seq_k, heads, dim_head).permute(0, 2, 1, 3)
        v_mh = v.view(bs, seq_k, heads, dim_head).permute(0, 2, 1, 3)
        logits = torch.matmul(q_mh, k_mh.transpose(-2, -1)) * dim_head ** -0.5 + bias[:, None]
        out = torch.matmul(torch.softmax(logits, dim=-1), v_mh)
        return out.permute(0, 2, 1, 3).reshape(bs, num_spatial, heads * dim_head)


def is_unet(engine: Any) -> bool:
    """SD 1.5 or SDXL: the families Attention Couple's UNet patches fit."""

    return bool(getattr(engine, "is_sd1", False) or getattr(engine, "is_sdxl", False))


def runner_for(engine: Any, regions: tuple[Any, ...], method: str) -> Any:
    """The runner for this job's regions, or None when it has none.

    "auto": Attention Couple on SD 1.5 / SDXL, masked conditioning elsewhere.
    "attention_couple" on any other family is refused by name, not silently
    run another way."""

    if not regions:
        return None
    if method == "per_region":
        # RG3: the port runs one inpaint pass per region instead of the main
        # pass; nothing rides the main pass.
        return None
    if method == "masked":
        return MaskedRegionsRunner(regions)
    if method == "attention_couple" and not is_unet(engine):
        raise HeadlessError(
            "REGIONS_METHOD_UNSUPPORTED",
            "Attention Couple works with SD 1.5 and SDXL models; choose masked regions for this model.")
    if is_unet(engine):
        return AttentionCoupleRunner(regions)
    return MaskedRegionsRunner(regions)


__all__ = (
    "AttentionCoupleRunner",
    "BASE_RATIO",
    "MaskedRegionsRunner",
    "feather",
    "is_unet",
    "region_text",
    "runner_for",
    "scale_strengths",
)
