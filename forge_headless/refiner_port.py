"""MH2. The Refiner: a second checkpoint of the SAME family takes over the
sampling at step k of n, in the same latent, as Neo's Refiner does.

Source review: `Evidence/source-review/MH-model-handoff.md`, "Stage 2". Owner,
2026-10-22: "Let's focus on the regular refiner first".

Neo's Refiner (`modules/sd_samplers_common.py` `apply_refiner`) is called by
`CFGDenoiser.forward` on every step and, at the switch, either reloads the whole
model through Neo's checkpoint catalogue (which Studio's directly loaded
sessions do not carry) or -- `refiner_fast_sd` -- swaps the diffusion weights in
place. Studio takes the second, Neo's own steps, with three differences:

* the file is one of Studio's model folders, resolved from its catalogue id
  through the model-root registry, as ControlNet models are (`controlnet_port`);
* the switch is a step fraction (k / n), which is what "45 of 60" means; Neo's
  default compares raw sigma, a fraction only for flow models;
* it composes with Hires, which then runs on the Refiner model (D1). Neo prints
  "[Error]Refiner does not support Hires. fix" and runs without the refiner.

D5, REVERSED (owner, 2026-10-23). Neo's fast path keeps the prompt as the base
model encoded it. On Anima that is not the Refiner's reading of the prompt: the
LLM adapter that turns the text encoder's output into the model's conditioning
ships IN the checkpoint, and Neo's loader moves it to the text encoder
(`loader.process_anima`), where the diffusion-weight swap never reaches it
(`ignore_start="llm"`). AnimaTox and Hicks differ in 116 of its 118 tensors; a
prospective user's ComfyUI two-sampler workflow, where each model applies its
own adapter, did not match Studio. So at the switch the Refiner's adapter is
loaded too and the prompt re-encoded with it, for the remaining steps, Hires and
Auto Detail. Regional prompts keep the base model's encoding.

The Refiner's own LoRAs (owner, 2026-10-23). `loras` None keeps Neo's fast path,
which re-applies the main prompt's LoRAs onto the Refiner's weights; a string of
`<lora:name:weight>` tags ("" for none) REPLACES them for the Refiner's steps,
Hires and Auto Detail, as the second sampler of a ComfyUI two-sampler workflow
has its own LoRA stack. Neo's `apply_lora_for_refiner` (a name-replacement rule
in the settings) is not used: Studio has no settings file for the owner to edit.

The base model's weights, and its adapter, go back at the end of the job, so no
later job runs on the Refiner's.

`current_lora_hash` is reset to `_RELOAD`, never Neo's "[]": "[]" is also the
hash `load_networks` computes for an EMPTY LoRA list, so after Neo's reset a
Refiner with no LoRAs, or the next job without any, returned early and kept the
LoRA-patched objects (`networks.py` :134-136, `processing.py` :983).

Installed as an ADAPTER: `install()` puts `dispatch` on
`modules.sd_samplers_common.apply_refiner`, which `CFGDenoiser.forward` looks
up on the module each call. A processing object without a `studio_refiner`
falls through to Neo's own function unchanged. No Neo file is edited.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .contracts import HeadlessError

log = logging.getLogger("studio.generation")

_REGISTRY: Any = None
_NEO_APPLY_REFINER: Any = None

#: A `current_lora_hash` no LoRA list compiles to (`load_networks` compares
#: `str(compiled_lora_targets)`, a list's repr), so the next activation always
#: rebuilds the patched objects from the originals.
_RELOAD = "studio: weights changed, re-apply the LoRAs"


def set_registry(registry: Any) -> None:
    """The model-root registry, from `model_roots` (as `controlnet_port`)."""

    global _REGISTRY
    _REGISTRY = registry


def resolve_checkpoint(model_id: str) -> tuple[str, str]:
    """A checkpoint catalogue id into (absolute path, display name)."""

    registry = _REGISTRY
    if registry is None:
        raise HeadlessError(
            "REFINER_MODEL_UNKNOWN",
            "No checkpoint folder is configured. Add one in Settings > Model folders.")
    candidate, source = registry.resolve("checkpoint", str(model_id))
    return str(source.resolved), str(getattr(candidate, "display_name", "") or "")


def label_for(path: str) -> str:
    """The name the Refiner's pictures record, as MI1 names a checkpoint."""

    from .model_identity import checkpoint_label

    return checkpoint_label(path) or Path(path).stem


def _refused(message: str) -> HeadlessError:
    return HeadlessError("REFINER_MODEL_INCOMPATIBLE", message)


class RefinerSwap:
    """One job's Refiner: check, switch at step k of n, restore.

    `switch_at` is the fraction of the main pass's denoiser calls after which
    the Refiner samples (`CFGDenoiser.step / total_steps`, Neo's own test in
    its steps mode). It switches ONCE: the Hires pass and Auto Detail that
    follow run on the Refiner's weights, which is the owner's D1.
    """

    def __init__(self, *, engine: Any, path: str, base_path: str,
                 switch_at: float, cfg: float, label: str,
                 loras: str | None = None) -> None:
        self.engine = engine
        self.path = str(path)
        self.base_path = str(base_path)
        self.switch_at = float(switch_at)
        self.cfg = float(cfg or 0.0)
        self.label = label or label_for(self.path)
        #: None: the main prompt's LoRAs (Neo's fast path). Otherwise the
        #: Refiner's own `<lora:...>` tags, "" for none, which replace them.
        self.loras = None if loras is None else str(loras)
        self.switched = False
        self.seconds: dict[str, float] = {}
        #: Anima: the Refiner's LLM adapter is swapped in at the switch (set by
        #: `check`), and the base adapter's own tensors kept here to go back.
        self.swap_adapter = False
        self._adapter_backup: dict[str, Any] | None = None

    # -- before sampling ---------------------------------------------------

    def check(self) -> None:
        """Refuse a file that cannot replace this model's weights.

        From the safetensors HEADERS only -- key names and shapes, no tensor
        read -- so a checkpoint of another family is refused before a single
        step is paid for. The same family means: every diffusion weight the
        base model's file has, under the same name and shape (Neo's
        `strict=False` load would otherwise leave the rest at the base
        model's values and say so only in the console). `llm*` is left out,
        as Neo's own load leaves it out (`ignore_start="llm"`).
        """

        for path in (self.path, self.base_path):
            if not path.lower().endswith((".safetensors", ".sft")):
                raise _refused(
                    "The Refiner works with .safetensors checkpoints; "
                    f"{Path(path).name} is not one.")
        wanted = _diffusion_shapes(self.base_path)
        # Only what a load can reach. Anima's `pos_embedder.dim_spatial_range`
        # and `dim_temporal_range` are `persistent=False` buffers the model
        # computes for itself, and `pos_embedder.seq` is not on Neo's model at
        # all: some files carry the three, merges often drop them, and no
        # state-dict load ever reads them. Counting them refused
        # two Anima finetunes of the same family (owner, 2026-10-22).
        loadable = self._loadable_names()
        if loadable is not None:
            wanted = {name: shape for name, shape in wanted.items() if name in loadable}
        shapes = _diffusion_shapes(self.path)
        missing = [name for name in wanted if name not in shapes]
        different = [name for name in wanted if name in shapes and shapes[name] != wanted[name]]
        if not wanted or missing or different:
            raise _refused(
                f"{self.label} is not the same kind of model as the one loaded "
                f"({len(missing)} weights missing, {len(different)} of another "
                "shape). The Refiner needs a checkpoint of the same family; for "
                "another family, use Hires Fix > Model.")
        self._check_adapter()

    def _check_adapter(self) -> None:
        """Anima only: whether the Refiner's own LLM adapter can replace the
        loaded one. Same names and shapes, or the job is refused; a file that
        carries none keeps the base adapter, and says so."""

        adapter = self._adapter()
        self.swap_adapter = False
        if adapter is None:
            return
        live = {name: tuple(int(n) for n in tensor.shape)
                for name, tensor in adapter.state_dict().items()}
        theirs = _adapter_shapes(self.path)
        if not theirs:
            log.info("REFINER %s carries no LLM adapter; its steps keep the main "
                     "model's prompt encoding", Path(self.path).name)
            return
        if theirs != live:
            raise _refused(
                f"{self.label}'s prompt adapter does not match the loaded model's "
                f"({len(set(live) - set(theirs))} tensors missing, "
                f"{sum(1 for k in live if k in theirs and theirs[k] != live[k])} "
                "of another shape), so the Refiner cannot read the prompt as this "
                "model does.")
        self.swap_adapter = True

    def _adapter(self) -> Any:
        """Anima's LLM adapter on the loaded text encoder (`Qwen3_06B.llm_adapter`,
        `backend/nn/llm/llama.py`), or None for every other family."""

        try:
            encoder = self.engine.forge_objects.clip.cond_stage_model.qwen3_06b
        except AttributeError:
            return None
        return getattr(encoder, "llm_adapter", None)

    # -- the switch --------------------------------------------------------

    def apply(self, cfg_denoiser: Any, x: Any, sigma: Any) -> bool:
        """`apply_refiner` for this job's main pass. True once, at the switch."""

        del sigma
        if self.switched or getattr(cfg_denoiser.p, "is_hr_pass", False):
            return False
        total = int(getattr(cfg_denoiser, "total_steps", 0) or 0)
        if total <= 0 or self.switch_at > cfg_denoiser.step / total:
            return False
        p = cfg_denoiser.p
        p.extra_generation_params["Refiner"] = self.label
        p.extra_generation_params["Refiner switch at"] = round(self.switch_at, 4)
        if self.loras is not None:
            p.extra_generation_params["Refiner LoRAs"] = self.loras or "None"
        self._load(self.path, "switch")
        self.switched = True
        if self.swap_adapter:
            self._swap_adapter(self.path)
        self._reactivate_loras(p)
        # Its own LoRAs may patch the text encoder differently from the main
        # prompt's, so the prompt is read again with them, as with its adapter.
        if self.swap_adapter or self.loras is not None:
            self._reencode(cfg_denoiser)
        self._prepare(x)
        self.engine.current_lora_hash = _RELOAD
        return True

    def _reencode(self, cfg_denoiser: Any) -> None:
        """The prompt again, through the Refiner's adapter: Neo's full-reload
        Refiner steps (`p.setup_conds()`, then `CFGDenoiser.update_inner_model`'s
        `get_conds` into the sampler's arguments), which `forward` reads back
        right after this switch. The cond caches are emptied first, or
        `get_conds_with_caching` would hand back the base model's encoding --
        its key names the prompt and settings, not the adapter."""

        from modules import devices

        p = cfg_denoiser.p
        clear_cond_caches(p)
        with devices.autocast():
            p.setup_conds()
        c, uc = p.get_conds()
        cfg_denoiser.sampler.sampler_extra_args["cond"] = c
        cfg_denoiser.sampler.sampler_extra_args["uncond"] = uc

    def _prepare(self, x: Any) -> None:
        """Neo's fast path, last step: the new weights onto the card,
        LoRA-patched (`sampling_function.sampling_prepare`)."""

        from backend.sampling.sampling_function import sampling_prepare

        sampling_prepare(self.engine.forge_objects.unet, x=x)

    def restore(self) -> None:
        """The base model's weights back, after the job. Raises on failure."""

        if not self.switched:
            return
        self._load(self.base_path, "restore")
        if self._adapter_backup is not None:
            self._swap_adapter(None)
        # The base model's next prompt is encoded by its own adapter, never
        # served from a cache the Refiner's filled.
        clear_cond_caches(None)
        self.switched = False
        # The next job re-applies its own LoRAs -- none included -- onto the
        # base model's weights, never the patched objects this job left.
        self.engine.current_lora_hash = _RELOAD

    # -- Neo's fast-path steps --------------------------------------------

    def _module(self) -> Any:
        return self.engine.forge_objects.unet.model.diffusion_model

    def _loadable_names(self) -> set[str] | None:
        """The live diffusion model's state-dict names (parameters and
        persistent buffers), or None when there is no live model to ask."""

        try:
            return set(self._module().state_dict().keys())
        except Exception:  # noqa: BLE001 - no engine: compare the files alone
            return None

    def _load(self, path: str, stage: str) -> None:
        """Neo's fast-path load, under `torch.inference_mode()` as Neo's own
        restore is (`ScriptRefiner.postprocess` is decorated with it).

        The switch runs inside `process_images_inner`'s inference mode
        (`processing.py` :921), where `model_unload` moves the weights to the
        CPU as inference tensors; the restore runs after it, and an in-place
        copy into an inference tensor outside that mode raises ("Inplace update
        to inference tensor outside InferenceMode") -- which is how the first
        live run failed every restore.
        """

        import torch

        with torch.inference_mode():
            self._load_weights(path, stage)

    def _load_weights(self, path: str, stage: str) -> None:
        import time

        from backend.loader import preprocess_state_dict
        from backend.memory_management import current_loaded_models
        from backend.patcher.unet import UnetPatcher
        from backend.state_dict import (
            convert_quantization,
            load_state_dict,
            try_filter_state_dict,
        )
        from backend.utils import load_torch_file

        started = time.monotonic()
        for index, loaded in enumerate(list(current_loaded_models)):
            if isinstance(loaded.model, UnetPatcher):
                current_loaded_models.pop(index).model_unload()
                break
        sd, metadata = load_torch_file(path, return_metadata=True)
        sd, metadata = convert_quantization(sd, metadata)
        sd = preprocess_state_dict(sd)
        sd = try_filter_state_dict(sd, [_unet_prefix(sd)])
        load_state_dict(self._module(), sd, ignore_start="llm")
        del sd
        self.seconds[stage] = round(time.monotonic() - started, 3)
        log.info("REFINER %s %s in %.2f s", stage, Path(path).name, self.seconds[stage])

    def _swap_adapter(self, path: str | None) -> None:
        """The Refiner's LLM adapter in (`path`), or the base one's kept
        tensors back (None). The text encoder is unloaded first, as the UNet
        is above, so a LoRA's patch is lifted before the weights change and
        re-applied on the next encode rather than restored over them."""

        import time

        import torch

        started = time.monotonic()
        with torch.inference_mode():
            adapter = self._adapter()
            self._unload_text_encoder()
            if path is None:
                adapter.load_state_dict(self._adapter_backup, strict=True)
                self._adapter_backup = None
            else:
                if self._adapter_backup is None:
                    self._adapter_backup = {
                        name: tensor.detach().to("cpu", copy=True)
                        for name, tensor in adapter.state_dict().items()}
                adapter.load_state_dict(_adapter_tensors(path), strict=True)
        stage = "adapter restore" if path is None else "adapter switch"
        self.seconds[stage] = round(time.monotonic() - started, 3)
        log.info("REFINER %s in %.2f s", stage, self.seconds[stage])

    def _unload_text_encoder(self) -> None:
        from backend.memory_management import current_loaded_models

        patcher = self.engine.forge_objects.clip.patcher
        for index, loaded in enumerate(list(current_loaded_models)):
            if loaded.model is patcher:
                current_loaded_models.pop(index).model_unload()
                break

    def _reactivate_loras(self, p: Any) -> None:
        """Neo's fast path, step 2: the LoRAs again, onto the new weights --
        the main prompt's, or the Refiner's own in their place."""

        self.engine.current_lora_hash = _RELOAD
        if getattr(p, "disable_extra_networks", False):
            return
        if self.loras is None:
            data = getattr(p, "extra_network_data", None)
            if not data:
                return
            from modules import extra_networks

            extra_networks.activate(p, data)
            return
        from modules import extra_networks

        own = list(extra_networks.parse_prompt(self.loras)[1].get("lora", []))
        # In place, as Neo's own fast path replaces `extra_network_data["lora"]`:
        # the cond cache's key and the end-of-job deactivate read this dict,
        # and the Hires pass activates `hr_extra_network_data` on the Refiner.
        for name in ("extra_network_data", "hr_extra_network_data"):
            data = getattr(p, name, None)
            if data is None and name == "extra_network_data":
                data = {}
                setattr(p, name, data)
            if data is not None:
                data["lora"] = list(own)
        kept = dict(getattr(p, "lora_hashes", None) or {})
        extra_networks.activate(p, p.extra_network_data)
        _merge_lora_hashes(p, kept)


def _merge_lora_hashes(p: Any, kept: dict[str, str]) -> None:
    """The picture's "Lora hashes" name the main prompt's LoRAs AND the
    Refiner's: `ExtraNetworkLora.activate` starts the dict afresh outside the
    Hires pass, so the switch's activation would drop the main prompt's."""

    if not kept:
        return
    merged = {**kept, **(getattr(p, "lora_hashes", None) or {})}
    p.lora_hashes = merged
    p.extra_generation_params["Lora hashes"] = ", ".join(f"{k}: {v}" for k, v in merged.items())


def _unet_prefix(state_dict: Any) -> str:
    """Neo's own rule (`huggingface_guess.detection.unet_prefix_from_state_dict`,
    :438): key names only. Imported here, at the one place it is needed,
    because the package imports torch."""

    from huggingface_guess.detection import unet_prefix_from_state_dict

    return unet_prefix_from_state_dict(state_dict)


def _read_header(path: str) -> tuple[dict[str, Any], int]:
    """A .safetensors file's header and the offset its tensor data starts at.

    The format's own header: an 8-byte little-endian length, then JSON of
    {name: {"dtype", "shape", "data_offsets"}}. `safe_open` took 3 s to give
    the same answer for one 3.9 GiB file; this reads a few hundred KB.
    """

    import json
    import struct

    with open(path, "rb") as handle:
        (length,) = struct.unpack("<Q", handle.read(8))
        if length <= 0 or length > 100 * 1024 * 1024:
            raise _refused(f"{Path(path).name} is not a readable .safetensors file.")
        return json.loads(handle.read(length)), 8 + length


_ADAPTER = "llm_adapter."
_DTYPES = {"BF16": "bfloat16", "F16": "float16", "F32": "float32"}


def _adapter_shapes(path: str) -> dict[str, tuple[int, ...]]:
    """Anima's LLM adapter in a checkpoint file, as {name under the adapter:
    shape}; empty when the file carries none."""

    header, _start = _read_header(path)
    return {key.split(_ADAPTER, 1)[1]: tuple(int(n) for n in info.get("shape", ()))
            for key, info in header.items()
            if key != "__metadata__" and _ADAPTER in key}


def _adapter_tensors(path: str) -> dict[str, Any]:
    """The adapter's tensors, read from their byte ranges (about 270 MB in
    BF16) rather than through `load_torch_file`, which maps the whole file."""

    import torch

    header, start = _read_header(path)
    tensors: dict[str, Any] = {}
    with open(path, "rb") as handle:
        for key, info in header.items():
            if key == "__metadata__" or _ADAPTER not in key:
                continue
            name = _DTYPES.get(str(info.get("dtype")))
            if name is None:
                raise _refused(
                    f"{Path(path).name}'s prompt adapter is stored as "
                    f"{info.get('dtype')}, which the Refiner cannot read.")
            first, last = info["data_offsets"]
            handle.seek(start + int(first))
            raw = bytearray(handle.read(int(last) - int(first)))
            tensors[key.split(_ADAPTER, 1)[1]] = torch.frombuffer(
                raw, dtype=getattr(torch, name)).reshape(info["shape"])
    return tensors


def clear_cond_caches(p: Any) -> None:
    """Empty Neo's prompt-conditioning caches, in place: the class-level lists
    every processing object shares (`processing.py` `cached_c`/`cached_uc`,
    Txt2Img's `cached_hr_c`/`cached_hr_uc`) and `p`'s own, which are normally
    the same lists. Their key is the prompt and settings, not the adapter."""

    import sys

    # Only if Neo's processing is already standing: importing it here would
    # bring the engine up, and with no engine there is no cache to empty.
    processing = sys.modules.get("modules.processing")
    lists: list[Any] = []
    if processing is not None:
        lists += [getattr(getattr(processing, "StableDiffusionProcessing", None), name, None)
                  for name in ("cached_c", "cached_uc")]
        lists += [getattr(getattr(processing, "StableDiffusionProcessingTxt2Img", None), name, None)
                  for name in ("cached_hr_c", "cached_hr_uc")]
    if p is not None:
        lists += [getattr(p, name, None)
                  for name in ("cached_c", "cached_uc", "cached_hr_c", "cached_hr_uc")]
    for cache in lists:
        if isinstance(cache, list):
            cache[:] = [None] * len(cache)


def _diffusion_shapes(path: str) -> dict[str, tuple[int, ...]]:
    """A checkpoint's diffusion weights as {name: shape}, from its header.

    The names Neo's fast path would load: `preprocess_state_dict`'s prefix
    rule (`loader.py` :756), then `unet_prefix_from_state_dict`
    (`detection.py` :438, key names only) stripped, `llm*` left out.
    """

    header, _start = _read_header(path)
    keys = [key for key in header if key != "__metadata__"]
    bare = not any(k.startswith(("model.diffusion_model.", "net.")) for k in keys)
    named = {(f"model.diffusion_model.{k}" if bare else k): k for k in keys}
    prefix = _unet_prefix(dict.fromkeys(named))
    shapes = {
        name[len(prefix):]: tuple(int(v) for v in header[key].get("shape", ()))
        for name, key in named.items() if name.startswith(prefix)
    }
    return {name: shape for name, shape in shapes.items() if not name.startswith("llm")}


def dispatch(cfg_denoiser: Any, x: Any, sigma: Any) -> bool:
    """The `apply_refiner` Studio installs: this job's Refiner, or Neo's."""

    swap = getattr(getattr(cfg_denoiser, "p", None), "studio_refiner", None)
    if swap is not None:
        return swap.apply(cfg_denoiser, x, sigma)
    return _NEO_APPLY_REFINER(cfg_denoiser, x, sigma)


def install() -> None:
    """Put `dispatch` on `modules.sd_samplers_common`. Idempotent."""

    global _NEO_APPLY_REFINER
    from modules import sd_samplers_common

    current = sd_samplers_common.apply_refiner
    if current is dispatch:
        return
    _NEO_APPLY_REFINER = current
    sd_samplers_common.apply_refiner = dispatch


__all__ = ("RefinerSwap", "dispatch", "install", "label_for", "resolve_checkpoint",
           "set_registry")
