"""Anima's two emphasis modes: "Studio" and "Neo-compatible". EM1.

Source review: `Evidence/source-review/EM1-anima-emphasis.md`. Research and
measurements: `Evidence/anima-weights-2026-10-11/FINDINGS.md`. The owner's
decisions: 2026-10-11, recorded in both.

WHAT NEO DOES TO A WEIGHT ON ANIMA (measured)
---------------------------------------------
- A weight multiplies the word's context vector after the text adapter
  (`anima_engine.py:117-118`). K's RMSNorm cancels it, so the word attends
  exactly as hard as before; only its value (V) is scaled.
- Each weighted segment is tokenized on its own (`anima_engine.py:39-78`), so
  the brackets alone change the tokens and the image.
- BREAK becomes three junk tokens at weight -1 and repaints about 65 % of the
  image.

WHAT "STUDIO" MODE DOES
-----------------------
1. **Tokenization** (`studio_tokenize`). The whole prompt is tokenized once.
   Each T5 piece takes the weight held by the majority of its non-space
   characters; a tie goes to the weight furthest from 1.0. BREAK becomes a
   space. Negative weights are clamped to 0, with a warning.
2. **V**: Neo's own context scaling, kept.
3. **B**: log(w) is added to the word's cross-attention logits, per batch row,
   as a float additive mask on the SDPA call. w = 0 is -1e4 (the owner's
   choice), which leaves exactly zero attention on the word.

Measured on the owner's blind marks: B+V drew 14 of 21 missing objects,
against 10 for V alone and 9 for B. No artefacts up to 2.0. The mask costs
nothing measurable: cuDNN accepts it (1.18 against 1.19 ms per call).

HOW THE MODE IS CHOSEN
----------------------
Per job, through `job_options.job_scope(emphasis=...)`. Every `opts.emphasis`
read in the process routes through `HeadlessOptions.__getattr__`, which checks
the scope first, so the engine choice (`anima_engine.py:81`), the cond-cache
key (`processing.py:440`) and the recorded "Emphasis" name (`:83`) all agree.
- "Studio" is a registered emphasis option (`EmphasisStudio`, a subclass of
  `EmphasisOriginal`, so any generic path behaves exactly as "Original").
- "Neo-compatible" is no override at all: Neo's own code path, untouched.

WHY RUNTIME HOOKS, NOT A `backend/` EDIT
----------------------------------------
Neo offers no seam for an attention bias: `torch_attention_op` passes no mask
(`backend/nn/anima.py:149-156`). `AGENTS.md` asks why an adapter cannot solve
it, and it can. So `install()` wraps three callables, and each wrapper calls
the original unchanged unless this job runs in Studio mode:
- `AnimaTextProcessingEngine.tokenize_line(self, line)`;
- `SelfCrossAttention.forward(self, x, context, rope_emb, transformer_options)`,
  cross-attention instances only;
- `Sampler.launch_sampling(self, steps, func)`, which every sampling pass
  goes through with its own `p` (base, Hires, img2img, each Auto Detail pass).

The signatures are checked before wrapping. A mismatch means upstream
changed, and the adapter refuses to install rather than guess: Studio mode
then reports itself unavailable and jobs run Neo-compatible. Recorded in
`docs/14_PATCH_INVENTORY.md` as ADAPTER-001.

[+5] The heatmap capture must see the same bias the model used:
`current_pass()` is the one object both read.
"""

from __future__ import annotations

import inspect
import logging
import math
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

LOG = logging.getLogger(__name__)

#: The emphasis option Studio mode runs under. Registered by `install()`.
STUDIO = "Studio"
#: Neo's own option. "Neo-compatible" is this, with no override at all.
NEO_COMPATIBLE = "Original"
#: w = 0: a large negative logit bias, not a dropped key (the owner's
#: decision), so the conditional and unconditional rows stay batched.
NEG_BIAS = -1.0e4
#: BREAK on Anima: there are no 75-token chunks to separate, so it is dropped.
BREAK_SEPARATOR = " "

#: The signatures `install()` wraps. A mismatch refuses installation.
EXPECTED_SIGNATURES = {
    "tokenize_line": ("self", "line"),
    "forward": ("self", "x", "context", "rope_emb", "transformer_options"),
    "launch_sampling": ("self", "steps", "func"),
}


# --------------------------------------------------------------- tokenizer --

def plain_segments(parsed: Iterable[Sequence[Any]]) -> list[tuple[str, float]]:
    """Neo's parsed `[text, weight]` list, with BREAK replaced by a space.

    `parse_prompt_attention` emits BREAK as `["BREAK", -1]` and swallows the
    whitespace around it (`parsing.py:22, 58`), so a separator has to be put
    back or `beach, BREAK backlit` would read `beach,backlit`.
    """
    out: list[tuple[str, float]] = []
    for text, weight in parsed:
        if text == "BREAK" and weight == -1:
            out.append((BREAK_SEPARATOR, 1.0))
        else:
            out.append((text, float(weight)))
    return out


def piece_weight(span: tuple[int, int], char_weight: Sequence[float], text: str) -> float | None:
    """The weight of the majority of the non-space characters in `[a, b)`.

    A tie goes to the weight furthest from 1.0: the user's explicit emphasis
    wins. None when the span holds no non-space character (a bare U+2581 piece
    shares the span of the character after it, so it takes the next piece's
    weight in `studio_tokenize`).
    """
    a, b = span
    counts: dict[float, int] = {}
    for i in range(a, b):
        if i < len(text) and not text[i].isspace():
            w = char_weight[i]
            counts[w] = counts.get(w, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    tied = [w for w, c in counts.items() if c == best]
    return max(tied, key=lambda w: (abs(w - 1.0), w))


def studio_tokenize(line: str, parse: Callable[[str, str], list], t5: Any, qwen: Any, *,
                    emphasis: str = STUDIO, id_pad: int = 151643, id_end: int = 1) -> dict[str, Any]:
    """The Studio-mode tokenization of one prompt line. Pure.

    `parse` is Neo's `parse_prompt_attention`; `t5` and `qwen` are the
    tokenizers the engine holds. Returns `t5_ids`, `t5_multipliers` (one per
    id, `</s>` last at 1.0), `qwen_ids`, the `plain` text, `warnings`, and
    `weighted`: whether any weight differs from 1.0 or the line held BREAK.

    An unweighted line without BREAK gives exactly Neo's ids and multipliers
    (measured: 29 of 29 prompts, `w2-tokenize-checks.txt` A).
    """
    warnings: list[str] = []
    parsed = parse(line, emphasis)
    had_break = any(text == "BREAK" and weight == -1 for text, weight in parsed)
    plain, char_weight = "", []
    for text, w in plain_segments(parsed):
        if w < 0:
            warnings.append(f"negative weight {w:g} on {text!r} clamped to 0")
            w = 0.0
        plain += text
        char_weight += [w] * len(text)
    if emphasis == "Ignore":
        char_weight = [1.0] * len(char_weight)
    enc = t5(plain, add_special_tokens=False, return_offsets_mapping=True)
    ids = [int(t) for t in enc["input_ids"]]
    spans = [tuple(o) for o in enc["offset_mapping"]]
    mult: list[float | None] = [piece_weight(s, char_weight, plain) for s in spans]
    for i in range(len(mult) - 1, -1, -1):
        if mult[i] is None:
            mult[i] = mult[i + 1] if i + 1 < len(mult) and mult[i + 1] is not None else 1.0
    ids.append(id_end)
    mult.append(1.0)
    qwen_ids = [int(t) for t in qwen(plain, add_special_tokens=False)["input_ids"]] if plain else []
    if not qwen_ids:
        qwen_ids = [id_pad]
    multipliers = [float(m) for m in mult]
    return {"t5_ids": ids, "t5_multipliers": multipliers, "qwen_ids": qwen_ids, "plain": plain,
            "warnings": warnings, "weighted": had_break or any(m != 1.0 for m in multipliers)}


# ---------------------------------------------------------------- fallback --

def fallback_reason(prompts: Sequence[tuple[str, str, bool]], *, steps: int,
                    hires: tuple[str, str, str, str] | None,
                    schedules: Callable[..., list], multicond: Callable[..., tuple]) -> str | None:
    """Why this job must run Neo-compatible instead of Studio, or None.

    The owner's decision 3: fall back, with a notice, for prompt editing,
    `AND`, a separate Hires prompt, or different prompts per image in a batch
    (Studio's batch size is 1, so that one cannot occur).

    `prompts` is `(role, text, is_positive)` for every prompt the job will
    condition on. `AND` matters only on a positive prompt: Neo splits
    positives with `get_multicond_prompt_list` and conditions negatives
    whole. `hires` is `(hr_prompt, prompt, hr_negative, negative)`; an empty
    Hires prompt means "same as the main one" (`processing.py:1602-1606`).
    `schedules` and `multicond` are `modules.prompt_parser`'s own functions.
    """
    if hires is not None:
        hr_prompt, prompt, hr_negative, negative = hires
        if (hr_prompt and hr_prompt != prompt) or (hr_negative and hr_negative != negative):
            return "a separate Hires prompt"
    for role, text, positive in prompts:
        if not text:
            continue
        subprompts = [text]
        if positive:
            _, flat, _ = multicond([text])
            if len(flat) > 1:
                return f"AND in the {role}"
            subprompts = list(flat)
        for sub in subprompts:
            if len(schedules([sub], int(steps))[0]) > 1:
                return f"prompt editing in the {role}"
    return None


#: The two values a request can name. Absent means "studio": the owner's
#: default on Anima (2026-10-11).
REQUEST_VALUES = ("studio", "neo")


def decide(requested: str | None, *, is_anima: bool, prompts: Sequence[tuple[str, str, bool]],
           hires: tuple[str, str, str, str] | None, steps: int, schedules: Callable[..., list],
           multicond: Callable[..., tuple], adapter_available: bool,
           adapter_reason: str = "") -> tuple[str, str, str]:
    """`(requested, effective, reason)` for one job, as the result records them.

    All three are "" for a model that is not Anima: the mode does not exist
    there, and a result must not claim a decision it never made. `effective`
    is "studio" or "neo". `reason` says why they differ, and is "" when they
    agree. A notice is owed whenever `requested` is "studio" and `effective`
    is not.
    """
    if not is_anima:
        return "", "", ""
    want = requested or "studio"
    if want == "neo":
        return want, "neo", ""
    if not adapter_available:
        return want, "neo", f"Studio mode is unavailable in this process ({adapter_reason or 'not installed'})"
    reason = fallback_reason(prompts, steps=steps, hires=hires, schedules=schedules, multicond=multicond)
    if reason:
        return want, "neo", reason
    return want, "studio", ""


# --------------------------------------------------------- per-pass weights --

@dataclass
class PassWeights:
    """The weights of one sampling pass: the positive prompt's for the
    conditional rows, the negative prompt's for the unconditional rows.
    `</s>` is last at 1.0; slots beyond a prompt (padding) are 1.0."""

    cond: tuple[float, ...]
    uncond: tuple[float, ...]
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def weighted(self) -> bool:
        return any(w != 1.0 for w in self.cond + self.uncond)

    def bias(self, cond_or_uncond: Sequence[int], rows: int, length: int, device: Any, dtype: Any) -> Any:
        """The (rows, 1, 1, length) additive mask: log(w), or NEG_BIAS where
        w = 0, per row in `cond_or_uncond` order (0 = conditional,
        `sampling_function.py:253`), each entry repeated for its rows."""
        coul = list(cond_or_uncond) or [0]
        key = (tuple(coul), int(rows), int(length), str(device), str(dtype))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        import torch

        per = max(1, rows // len(coul))
        table = torch.zeros(len(coul) * per, length, dtype=torch.float32)
        for j, c in enumerate(coul):
            weights = self.cond if c == 0 else self.uncond
            row = [NEG_BIAS if w <= 0 else math.log(w) for w in weights[:length]]
            for r in range(per):
                table[j * per + r, :len(row)] = torch.tensor(row, dtype=torch.float32)
        mask = table[:, None, None, :].to(device=device, dtype=dtype)
        self._cache[key] = mask
        return mask


_PASS: ContextVar[PassWeights | None] = ContextVar("studio_anima_emphasis_pass", default=None)


def current_pass() -> PassWeights | None:
    """The B weights in force for the sampling pass running on this thread,
    or None (Neo's own attention). [+5] The heatmap capture reads this, so it
    reports the attention the model actually used."""
    return _PASS.get()


def _conditioning_text(prompt: str, *, positive: bool, steps: int, prompt_parser: Any) -> str:
    """The exact text Neo hands the engine for one prompt with no editing or
    AND (`processing.py:483-498` -> `prompt_parser.get_multicond_learned_conditioning`
    for positives, `get_learned_conditioning` for negatives)."""
    text = prompt
    if positive:
        _, flat, _ = prompt_parser.get_multicond_prompt_list([prompt])
        text = flat[0]
    return prompt_parser.get_learned_conditioning_prompt_schedules([text], int(steps))[0][-1][1]


def weights_for_pass(p: Any) -> PassWeights | None:
    """This pass's weights in Studio mode, or None.

    None when: the job is not in Studio mode; the model is not Anima; or no
    weight differs from 1.0 (then Neo's own attention runs, which is how an
    unweighted prompt stays pixel-identical).
    """
    if p is None:
        return None
    from modules.shared import opts

    if getattr(opts, "emphasis", None) != STUDIO:
        return None
    engine = getattr(getattr(p, "sd_model", None), "text_processing_engine_anima", None)
    if engine is None:
        return None
    from backend.text_processing import parsing
    from modules import prompt_parser

    hr = bool(getattr(p, "is_hr_pass", False))
    prompts = getattr(p, "hr_prompts" if hr else "prompts", None) or [""]
    negatives = getattr(p, "hr_negative_prompts" if hr else "negative_prompts", None) or [""]
    steps = int(getattr(p, "steps", 20) or 20)

    def weights(prompt: str, positive: bool) -> tuple[float, ...]:
        text = _conditioning_text(prompt, positive=positive, steps=steps, prompt_parser=prompt_parser)
        r = studio_tokenize(text, parsing.parse_prompt_attention, engine.t5_tokenizer, engine.qwen_tokenizer,
                            emphasis=STUDIO, id_pad=engine.id_pad, id_end=engine.id_end)
        return tuple(r["t5_multipliers"])

    found = PassWeights(cond=weights(prompts[0], True), uncond=weights(negatives[0], False))
    return found if found.weighted else None


# ------------------------------------------------------------------- hooks --

_INSTALLED: dict[str, Any] = {}


def available() -> bool:
    """Whether Studio mode can run in this process (the hooks are in)."""
    return _INSTALLED.get("state") == "installed"


def status() -> dict[str, Any]:
    return dict(_INSTALLED)


def _parameters(fn: Callable) -> tuple[str, ...]:
    return tuple(inspect.signature(fn).parameters)


def _masked_cross_attention(module: Any, x: Any, context: Any, rope_emb: Any,
                            transformer_options: Mapping, weights: PassWeights) -> Any:
    """`SelfCrossAttention.forward` with B's mask. The same reshape as
    `torch_attention_op` (`anima.py:151-155`) and the same tail as
    `compute_attention` (:158-160)."""
    from einops import rearrange

    q, k, v = module.compute_qkv(x, context, rope_emb=rope_emb)
    qs, ks = q.shape, k.shape
    qh = rearrange(q, "b ... h k -> b h ... k").view(qs[0], qs[-2], -1, qs[-1])
    kh = rearrange(k, "b ... h v -> b h ... v").view(ks[0], ks[-2], -1, ks[-1])
    vh = rearrange(v, "b ... h v -> b h ... v").view(ks[0], ks[-2], -1, ks[-1])
    mask = weights.bias(transformer_options.get("cond_or_uncond") or [0], qh.shape[0], kh.shape[2],
                        qh.device, qh.dtype)
    from backend import attention as neo_attention

    if neo_attention.attention_function is neo_attention.attention_pytorch:
        out = neo_attention.attention_pytorch(qh, kh, vh, qs[-2], mask=mask, skip_reshape=True)
    else:
        from backend import operations

        out = operations.scaled_dot_product_attention(qh, kh, vh, attn_mask=mask, dropout_p=0.0, is_causal=False)
        out = out.transpose(1, 2).reshape(qs[0], -1, qs[-2] * qs[-1])
    return module.output_dropout(module.output_proj(out))


def install() -> dict[str, Any]:
    """Register the "Studio" emphasis option and wrap the three callables.
    Idempotent. Never raises: a failure leaves Neo untouched and Studio mode
    unavailable, and says why. A failure is not latched (as `bootstrap_backend`):
    a later call tries again."""
    if available():
        return dict(_INSTALLED)
    try:
        from backend.nn import anima
        from backend.text_processing import anima_engine, emphasis
        from modules import sd_samplers_common

        targets = {
            "tokenize_line": (anima_engine.AnimaTextProcessingEngine, "tokenize_line"),
            "forward": (anima.SelfCrossAttention, "forward"),
            "launch_sampling": (sd_samplers_common.Sampler, "launch_sampling"),
        }
        originals = {name: getattr(owner, attr) for name, (owner, attr) in targets.items()}
        for name, fn in originals.items():
            if _parameters(fn) != EXPECTED_SIGNATURES[name]:
                raise RuntimeError(f"{name} signature changed upstream: {_parameters(fn)}")

        class EmphasisStudio(emphasis.EmphasisOriginal):
            name = STUDIO
            description = ("Anima: the prompt tokenized once, BREAK dropped, and weights that change how "
                           "strongly a word attends (Studio). Elsewhere the same as Original.")

        if not any(getattr(o, "name", None) == STUDIO for o in emphasis.options):
            emphasis.options.append(EmphasisStudio)

        original_tokenize_line = originals["tokenize_line"]
        original_forward = originals["forward"]
        original_launch_sampling = originals["launch_sampling"]

        def tokenize_line(self, line):
            if getattr(getattr(self, "emphasis", None), "name", None) != STUDIO:
                return original_tokenize_line(self, line)
            from backend.text_processing import parsing

            r = studio_tokenize(line, parsing.parse_prompt_attention, self.t5_tokenizer, self.qwen_tokenizer,
                                emphasis=STUDIO, id_pad=self.id_pad, id_end=self.id_end)
            if r["warnings"]:
                LOG.warning("Studio emphasis: %s", "; ".join(r["warnings"]))
                from .job_options import record

                record(emphasis_warnings=list(r["warnings"]))
            if r["weighted"]:
                # Neo records the name only for weights (`uses_emphasis`, which
                # ignores BREAK). Studio mode also changes a BREAK prompt, so
                # it has to say so or that image cannot be reproduced.
                from backend.args import dynamic_args

                dynamic_args.last_extra_generation_params["Emphasis"] = STUDIO
            chunk = anima_engine.PromptChunk()
            chunk.qwen_tokens = list(r["qwen_ids"])
            chunk.qwen_multipliers = [1.0] * len(chunk.qwen_tokens)
            chunk.t5_tokens = list(r["t5_ids"])
            chunk.t5_multipliers = list(r["t5_multipliers"])          # V: Neo's context scaling
            return [chunk]

        def forward(self, x, context=None, rope_emb=None, transformer_options={}):
            weights = _PASS.get()
            if weights is None or self.is_SelfAttn or context is None:
                return original_forward(self, x, context, rope_emb=rope_emb, transformer_options=transformer_options)
            return _masked_cross_attention(self, x, context, rope_emb, transformer_options, weights)

        def launch_sampling(self, steps, func):
            try:
                weights = weights_for_pass(getattr(self, "p", None))
            except Exception as exc:  # noqa: BLE001 -- weights are a refinement; sampling must run
                LOG.warning("Studio emphasis: no weights for this pass (%r); Neo's attention runs", exc)
                weights = None
            token = _PASS.set(weights)
            try:
                return original_launch_sampling(self, steps, func)
            finally:
                _PASS.reset(token)

        tokenize_line.__wrapped__ = original_tokenize_line
        forward.__wrapped__ = original_forward
        launch_sampling.__wrapped__ = original_launch_sampling
        anima_engine.AnimaTextProcessingEngine.tokenize_line = tokenize_line
        anima.SelfCrossAttention.forward = forward
        sd_samplers_common.Sampler.launch_sampling = launch_sampling
        _INSTALLED.update(state="installed", wrapped=sorted(targets))
    except Exception as exc:  # noqa: BLE001 -- startup reports, never dies
        _INSTALLED.clear()
        _INSTALLED.update(state="unavailable", reason=f"{type(exc).__name__}: {exc}")
        LOG.warning("Studio emphasis unavailable: %s", _INSTALLED["reason"])
    return dict(_INSTALLED)


__all__ = (
    "BREAK_SEPARATOR",
    "EXPECTED_SIGNATURES",
    "NEG_BIAS",
    "NEO_COMPATIBLE",
    "PassWeights",
    "REQUEST_VALUES",
    "STUDIO",
    "available",
    "current_pass",
    "decide",
    "fallback_reason",
    "install",
    "piece_weight",
    "plain_segments",
    "status",
    "studio_tokenize",
    "weights_for_pass",
)
