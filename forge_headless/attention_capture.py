"""Where each prompt word landed: cross-attention captured during generation. HM1.

Design and source review: `Evidence/source-review/HM1-attention-capture.md`.
The research behind every number: `Evidence/develop-knowledge-2026-10-10`,
`prompt-masks-2026-10-10` and `anima-attention-2026-10-11` (FINDINGS.md).

WHAT IS KEPT (the owner's decisions, 2026-10-11)
------------------------------------------------
For the MAIN sampling pass of a job (not Neo's Hires pass, not Studio's
img2img Hires pass, not Auto Detail), for the conditional (positive) row
only, the head-mean attention probability of every prompt token on the
image grid, as running sums -- nothing per step:

- **Anima** (28 DiT blocks; the grid is (H/16) x (W/16)):
  - blocks 2-8 over all steps (the mask source);
  - all 28 blocks over all steps (the "possibly missing" warning);
  - the padding slots' share over all blocks ("text used here" is 1 - it).

  Measured cost: about +0.34 s per image.
- **SDXL** (U-Net cross-attention, the two finest levels, (H/16) and (H/32)):
  steps 5-15 only. That subset still flags an absent word (measured,
  `Evidence/heatmap-capture-2026-10-12/h1-sdxl-subset-face.txt`). Measured
  cost: about +0.48 s per image.

Probabilities are computed ON THE SIDE from q and k; nothing feeds back into
the model, so images are pixel-identical with or without capture (measured in
the research). On Anima in Studio emphasis mode the capture adds the same
log(w) bias the model used ([+5], `anima_emphasis.current_pass()`), or it
would report attention the model never used.

HOOKS (ADAPTER-002 in docs/14_PATCH_INVENTORY.md; no Neo file is edited)
--------------------------------------------------------------------------
- `Sampler.launch_sampling`: opens a session for a pass the port MARKED
  (`p._studio_attention_capture`) and closes it when the pass ends.
- `Sampler.callback_state`: counts steps (SDXL's subset).
- Anima `SelfCrossAttention.compute_qkv` / `forward` (cross-attention only):
  keep q and k, then compute after the forward. Installed after
  `anima_emphasis`, so this `forward` wraps its wrapper.
- SDXL `CrossAttention.forward` (cross-attention calls only): q and k
  recomputed for the conditional rows at the captured steps and levels.

Every wrapper calls the original unchanged when no session is open. A failure
inside a capture drops that capture and logs it; it never reaches generation.
"""

from __future__ import annotations

import inspect
import logging
import math
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

LOG = logging.getLogger(__name__)

FORMAT_VERSION = 1
#: Anima blocks for masks (the research's best; 0-based, inclusive 2..8).
ANIMA_MASK_BLOCKS = frozenset(range(2, 9))
#: SDXL evaluations captured: 0-based 4..14 = steps 5-15.
SDXL_STEPS = frozenset(range(4, 15))
#: The family of each engine class the capture understands.
FAMILIES = {"Anima": "anima", "StableDiffusionXL": "sdxl"}

EXPECTED_SIGNATURES = {
    "launch_sampling": ("self", "steps", "func"),
    "callback_state": ("self", "d"),
    "anima_compute_qkv": ("self", "x", "context", "rope_emb"),
    "anima_forward": ("self", "x", "context", "rope_emb", "transformer_options"),
    "sdxl_forward": ("self", "x", "context", "value", "mask", "transformer_options"),
}

#: The attribute the port sets on the processing object of the pass it wants.
MARK = "_studio_attention_capture"


# ------------------------------------------------------------------ results --

@dataclass
class CaptureResult:
    """One job's capture, on the CPU, ready to be written as a sidecar."""

    family: str
    width: int
    height: int
    grid: tuple[int, int]
    #: name -> float16 array (positions, gh, gw) or (gh, gw).
    arrays: dict[str, Any]
    #: The token pieces each map row belongs to, in order.
    pieces: list[str]
    prompt: str
    meta: dict[str, Any] = field(default_factory=dict)


_RESULT: ContextVar[CaptureResult | None] = ContextVar("studio_attention_capture_result", default=None)


def take() -> CaptureResult | None:
    """The capture of the last marked pass on this thread, once; None if none."""
    result = _RESULT.get()
    _RESULT.set(None)
    return result


def write_sidecar(result: CaptureResult, path: Path) -> Path:
    """One compressed .npz: the arrays plus a JSON header (no pickles)."""
    import json

    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"version": FORMAT_VERSION, "family": result.family, "width": result.width, "height": result.height,
              "grid": list(result.grid), "pieces": result.pieces, "prompt": result.prompt, **result.meta}
    tmp = path.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, header=np.array(json.dumps(header)), **result.arrays)
    tmp.replace(path)
    return path


def read_sidecar(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(header, arrays)` of a sidecar written by `write_sidecar`."""
    import json

    import numpy as np

    with np.load(Path(path), allow_pickle=False) as data:
        header = json.loads(str(data["header"]))
        arrays = {k: data[k] for k in data.files if k != "header"}
    return header, arrays


# ----------------------------------------------------------------- sessions --

@dataclass
class _Session:
    family: str
    width: int
    height: int
    grid: tuple[int, int]
    block_of: dict[int, int] = field(default_factory=dict)
    step: int = 0
    sums: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    length: int | None = None            # Anima: the prompt's real positions (pieces + </s>)
    stash: dict[int, tuple[Any, Any]] = field(default_factory=dict)
    failed: str | None = None


_SESSION: ContextVar[_Session | None] = ContextVar("studio_attention_capture_session", default=None)


def family_of(sd_model: Any) -> str | None:
    return FAMILIES.get(type(sd_model).__name__)


def _half(n: int) -> int:
    return -(-int(n) // 2)


def _grid(family: str, width: int, height: int) -> tuple[int, int]:
    """The token grid of the finest captured level. Ceiling halves, because
    Studio sizes are multiples of 8, not 16: Anima pads an odd latent to its
    patch size, and SDXL's U-Net rounds an odd size up at each downsample."""
    return (_half(-(-int(height) // 8)), _half(-(-int(width) // 8)))


def _anima_blocks(sd_model: Any) -> dict[int, int]:
    """id(cross_attn) -> block index, from the engine's own DiT."""
    dm = sd_model.forge_objects.unet.model.diffusion_model
    return {id(block.cross_attn): i for i, block in enumerate(dm.blocks)}


def _open(p: Any) -> _Session | None:
    family = family_of(getattr(p, "sd_model", None))
    if family is None:
        return None
    width, height = int(getattr(p, "width", 0) or 0), int(getattr(p, "height", 0) or 0)
    session = _Session(family=family, width=width, height=height, grid=_grid(family, width, height))
    if family == "anima":
        session.block_of = _anima_blocks(p.sd_model)
    return session


def _add(session: _Session, name: str, value: Any) -> None:
    current = session.sums.get(name)
    session.sums[name] = value.clone() if current is None else current.add_(value)
    session.counts[name] = session.counts.get(name, 0) + 1


def _cond_rows(transformer_options: Mapping, batch: int) -> list[int]:
    coul = list(transformer_options.get("cond_or_uncond") or [0])
    per = batch // max(1, len(coul))
    return [j * per + r for j, c in enumerate(coul) if c == 0 for r in range(per)]


# ------------------------------------------------------------ the arithmetic --

def anima_side_probs(q_r: Any, k_r: Any, length: int, total: int, bias_r: Any = None) -> tuple[Any, Any]:
    """Head-mean probabilities of the `length` real keys, and of the padding,
    for one batch row. q_r (N, H, D), k_r (T, H, D). The T - length padded
    keys are exact zeros (logit 0, e^0 each; `anima_engine.py:120-121`), so
    they enter the softmax as one term, log(T - length). `bias_r` (T,) is
    Studio mode's log(w) for this row, or None."""
    import torch

    scale = q_r.shape[-1] ** -0.5
    qh = q_r.float().permute(1, 0, 2)                  # H, N, D
    kh = k_r[:length].float().permute(1, 2, 0)         # H, D, L
    logits = torch.bmm(qh, kh) * scale                 # H, N, L
    if bias_r is not None:
        logits = logits + bias_r[:length].float()
    if length < total:
        log_pad = math.log(total - length)
        lse = torch.logsumexp(torch.cat([logits, logits.new_full((*logits.shape[:2], 1), log_pad)], -1),
                              -1, keepdim=True)
        return torch.exp(logits - lse).mean(0), torch.exp(log_pad - lse)[..., 0].mean(0)
    probs = torch.softmax(logits, dim=-1)
    return probs.mean(0), torch.zeros_like(probs[0, :, 0])


def sdxl_probs(module: Any, x_r: Any, context_r: Any) -> Any:
    """Head-mean probabilities (N, T) for the conditional rows of one SDXL
    cross-attention call, q and k recomputed exactly as the layer makes them
    (`unet.py:141-143`), in float32."""
    import torch

    q = module.to_q(x_r).float()
    k = module.to_k(context_r).float()
    b, n, _ = q.shape
    h = module.heads
    d = q.shape[-1] // h
    q = q.view(b, n, h, d).transpose(1, 2)
    k = k.view(b, k.shape[1], h, d).transpose(1, 2)
    probs = torch.softmax(q @ k.transpose(-1, -2) / math.sqrt(d), dim=-1)
    return probs.mean(dim=1)[0]                         # N, T (the first conditional row)


def _capture_anima(session: _Session, module: Any, context: Any, transformer_options: Mapping) -> None:
    stash = session.stash.pop(id(module), None)
    block = session.block_of.get(id(module))
    if stash is None or block is None:
        return
    q, k = stash
    import torch

    with torch.no_grad():
        q = q.reshape(q.shape[0], -1, q.shape[-2], q.shape[-1])
        k = k.reshape(k.shape[0], -1, k.shape[-2], k.shape[-1])
        rows = _cond_rows(transformer_options, q.shape[0])
        if not rows:
            return
        r = rows[0]
        total = int(k.shape[1])
        if session.length is None:
            ctx = context.reshape(context.shape[0], -1, context.shape[-1])
            live = (ctx[r].abs().amax(dim=-1) > 0).nonzero()
            session.length = int(live.max()) + 1 if live.numel() else 1
        bias = None
        from .anima_emphasis import current_pass

        weights = current_pass()
        if weights is not None:
            coul = list(transformer_options.get("cond_or_uncond") or [0])
            bias = weights.bias(coul, q.shape[0], total, q.device, torch.float32)[r, 0, 0]
        probs, pad = anima_side_probs(q[r], k[r], session.length, total, bias)
        _add(session, "anima_all", probs)
        _add(session, "anima_pad", pad)
        if block in ANIMA_MASK_BLOCKS:
            _add(session, "anima_b2_8", probs)


def _capture_sdxl(session: _Session, module: Any, x: Any, context: Any, transformer_options: Mapping) -> None:
    if session.step not in SDXL_STEPS:
        return
    gh, gw = session.grid
    n = int(x.shape[1])
    level = "sdxl_16" if n == gh * gw else "sdxl_32" if n == _half(gh) * _half(gw) else None
    if level is None:
        return
    rows = _cond_rows(transformer_options, x.shape[0])
    if not rows:
        return
    import torch

    with torch.no_grad():
        _add(session, level, sdxl_probs(module, x[rows], context[rows]))


# ------------------------------------------------------------------- finish --

def _conditioning_text(p: Any) -> str:
    from modules import prompt_parser

    from .anima_emphasis import _conditioning_text as text_of

    prompts = getattr(p, "prompts", None) or [""]
    return text_of(prompts[0], positive=True, steps=int(getattr(p, "steps", 20) or 20), prompt_parser=prompt_parser)


def _anima_pieces(p: Any, text: str) -> list[str]:
    """The T5 pieces the conditioning used, without side effects: Studio mode
    runs the pure Studio tokenizer, any other mode Neo's own `tokenize_line`
    (unwrapped, so nothing touches the infotext)."""
    from modules.shared import opts

    from . import anima_emphasis

    engine = p.sd_model.text_processing_engine_anima
    if getattr(opts, "emphasis", None) == anima_emphasis.STUDIO and anima_emphasis.available():
        from backend.text_processing import parsing

        ids = anima_emphasis.studio_tokenize(text, parsing.parse_prompt_attention, engine.t5_tokenizer,
                                             engine.qwen_tokenizer, id_pad=engine.id_pad,
                                             id_end=engine.id_end)["t5_ids"]
    else:
        tokenize_line = type(engine).tokenize_line
        tokenize_line = getattr(tokenize_line, "__wrapped__", tokenize_line)
        ids = [int(t) for t in tokenize_line(engine, text)[0].t5_tokens]
    return list(engine.t5_tokenizer.convert_ids_to_tokens(ids))


def _sdxl_tokens(p: Any, text: str) -> tuple[list[str], list[int]]:
    """CLIP pieces and their column in the conditioning (77 per chunk): each
    chunk's real tokens, between its start token and its first end token."""
    engine = p.sd_model.text_processing_engine_l
    pieces, columns = [], []
    chunks, _count = engine.tokenize_line(text)          # `classic_engine.py:125`: (chunks, token count)
    for c, chunk in enumerate(chunks):
        tokens = list(chunk.tokens)
        for i in range(1, len(tokens)):
            if tokens[i] == engine.id_end:
                break
            columns.append(c * len(tokens) + i)
            pieces.append(engine.tokenizer.convert_ids_to_tokens([int(tokens[i])])[0])
    return pieces, columns


def _finish(session: _Session, p: Any) -> CaptureResult | None:
    if session.failed or not session.sums:
        return None
    gh, gw = session.grid
    text = _conditioning_text(p)
    arrays: dict[str, Any] = {}
    if session.family == "anima":
        pieces = _anima_pieces(p, text)
        length = int(session.length or 0)
        tokens = int(session.sums["anima_all"].shape[0])
        if tokens != gh * gw:
            raise ValueError(f"Anima grid {gh}x{gw} does not match {tokens} image tokens")
        for name in ("anima_all", "anima_b2_8"):
            if name in session.sums:
                mean = session.sums[name] / max(1, session.counts[name])            # N, L
                arrays[name] = mean.t().reshape(length, gh, gw).to("cpu").numpy().astype("float16")
        if "anima_pad" in session.sums:
            pad = session.sums["anima_pad"] / max(1, session.counts["anima_pad"])
            arrays["anima_pad"] = pad.reshape(gh, gw).to("cpu").numpy().astype("float16")
        pieces = pieces[:length] + [""] * max(0, length - len(pieces))
    else:
        import torch.nn.functional as F

        pieces, columns = _sdxl_tokens(p, text)
        fine = None
        for name, (h, w) in (("sdxl_16", (gh, gw)), ("sdxl_32", (_half(gh), _half(gw)))):
            if name not in session.sums:
                continue
            mean = session.sums[name] / max(1, session.counts[name])                # N, T
            maps = mean.t()[columns].reshape(len(columns), h, w)
            if (h, w) != (gh, gw):
                maps = F.interpolate(maps[None], size=(gh, gw), mode="bilinear", align_corners=False)[0]
            fine = maps if fine is None else fine + maps
        if fine is None:
            return None
        arrays["sdxl"] = (fine / sum(1 for n in ("sdxl_16", "sdxl_32") if n in session.sums)) \
            .to("cpu").numpy().astype("float16")
    meta = {"calls": dict(session.counts)}
    if session.family == "anima":
        from modules.shared import opts

        meta["emphasis"] = str(getattr(opts, "emphasis", "") or "")
    return CaptureResult(family=session.family, width=session.width, height=session.height, grid=(gh, gw),
                         arrays=arrays, pieces=pieces, prompt=text, meta=meta)


# -------------------------------------------------------------------- hooks --

_INSTALLED: dict[str, Any] = {}


def available() -> bool:
    return _INSTALLED.get("state") == "installed"


def status() -> dict[str, Any]:
    return dict(_INSTALLED)


def _parameters(fn: Any) -> tuple[str, ...]:
    return tuple(inspect.signature(fn).parameters)


def install() -> dict[str, Any]:
    """Wrap the five callables after checking their signatures. Idempotent;
    never raises; a failure is not latched and leaves Neo untouched."""
    if available():
        return dict(_INSTALLED)
    try:
        from backend.nn import anima, unet
        from modules import sd_samplers_common

        targets = {
            "launch_sampling": (sd_samplers_common.Sampler, "launch_sampling"),
            "callback_state": (sd_samplers_common.Sampler, "callback_state"),
            "anima_compute_qkv": (anima.SelfCrossAttention, "compute_qkv"),
            "anima_forward": (anima.SelfCrossAttention, "forward"),
            "sdxl_forward": (unet.CrossAttention, "forward"),
        }
        originals = {name: getattr(owner, attr) for name, (owner, attr) in targets.items()}
        for name, fn in originals.items():
            if _parameters(fn) != EXPECTED_SIGNATURES[name]:
                raise RuntimeError(f"{name} signature changed upstream: {_parameters(fn)}")

        o_launch, o_callback = originals["launch_sampling"], originals["callback_state"]
        o_qkv, o_aforward, o_sforward = originals["anima_compute_qkv"], originals["anima_forward"], \
            originals["sdxl_forward"]

        def launch_sampling(self, steps, func):
            p = getattr(self, "p", None)
            if p is None or not getattr(p, MARK, False) or getattr(p, "is_hr_pass", False):
                return o_launch(self, steps, func)
            try:
                session = _open(p)
            except Exception as exc:  # noqa: BLE001 -- capture is optional; the image is not
                LOG.warning("attention capture not started: %r", exc)
                session = None
            if session is None:
                return o_launch(self, steps, func)
            token = _SESSION.set(session)
            try:
                out = o_launch(self, steps, func)
            finally:
                _SESSION.reset(token)
            try:
                _RESULT.set(_finish(session, p))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("attention capture dropped at finish: %r", exc)
                _RESULT.set(None)
            return out

        def callback_state(self, d):
            session = _SESSION.get()
            if session is not None:
                session.step = int(d["i"]) + 1          # the NEXT model evaluation's 0-based step
            return o_callback(self, d)

        def compute_qkv(self, x, context=None, rope_emb=None):
            q, k, v = o_qkv(self, x, context, rope_emb=rope_emb)
            session = _SESSION.get()
            if session is not None and session.family == "anima" and not self.is_SelfAttn and not session.failed:
                session.stash[id(self)] = (q, k)
            return q, k, v

        def anima_forward(self, x, context=None, rope_emb=None, transformer_options={}):
            out = o_aforward(self, x, context, rope_emb=rope_emb, transformer_options=transformer_options)
            session = _SESSION.get()
            if session is None or session.family != "anima" or self.is_SelfAttn or session.failed:
                return out
            try:
                _capture_anima(session, self, context, transformer_options)
            except Exception as exc:  # noqa: BLE001
                session.failed = repr(exc)
                LOG.warning("attention capture dropped: %r", exc)
            return out

        def sdxl_forward(self, x, context=None, value=None, mask=None, transformer_options={}):
            out = o_sforward(self, x, context=context, value=value, mask=mask, transformer_options=transformer_options)
            session = _SESSION.get()
            if session is None or session.family != "sdxl" or session.failed or context is None \
                    or context.shape[1] == x.shape[1]:
                return out
            try:
                _capture_sdxl(session, self, x, context, transformer_options)
            except Exception as exc:  # noqa: BLE001
                session.failed = repr(exc)
                LOG.warning("attention capture dropped: %r", exc)
            return out

        for fn, original in ((launch_sampling, o_launch), (callback_state, o_callback), (compute_qkv, o_qkv),
                             (anima_forward, o_aforward), (sdxl_forward, o_sforward)):
            fn.__wrapped__ = original
        sd_samplers_common.Sampler.launch_sampling = launch_sampling
        sd_samplers_common.Sampler.callback_state = callback_state
        anima.SelfCrossAttention.compute_qkv = compute_qkv
        anima.SelfCrossAttention.forward = anima_forward
        unet.CrossAttention.forward = sdxl_forward
        _INSTALLED.clear()
        _INSTALLED.update(state="installed", wrapped=sorted(targets))
    except Exception as exc:  # noqa: BLE001 -- startup reports, never dies
        _INSTALLED.clear()
        _INSTALLED.update(state="unavailable", reason=f"{type(exc).__name__}: {exc}")
        LOG.warning("attention capture unavailable: %s", _INSTALLED["reason"])
    return dict(_INSTALLED)


__all__ = (
    "ANIMA_MASK_BLOCKS",
    "CaptureResult",
    "EXPECTED_SIGNATURES",
    "FORMAT_VERSION",
    "MARK",
    "SDXL_STEPS",
    "anima_side_probs",
    "available",
    "family_of",
    "install",
    "read_sidecar",
    "sdxl_probs",
    "status",
    "take",
    "write_sidecar",
)
