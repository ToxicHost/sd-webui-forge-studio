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

The prompt is not re-encoded, as in Neo's fast path (D5). The base model's
weights go back at the end of the job, so no later job runs on the Refiner's.

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
                 switch_at: float, cfg: float, label: str) -> None:
        self.engine = engine
        self.path = str(path)
        self.base_path = str(base_path)
        self.switch_at = float(switch_at)
        self.cfg = float(cfg or 0.0)
        self.label = label or label_for(self.path)
        self.switched = False
        self.seconds: dict[str, float] = {}

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
        self._load(self.path, "switch")
        self.switched = True
        self._reactivate_loras(p)
        self._prepare(x)
        self.engine.current_lora_hash = str([])
        return True

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
        self.switched = False
        # The next job re-applies its own LoRAs from a clean hash.
        self.engine.current_lora_hash = str([])

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

    def _reactivate_loras(self, p: Any) -> None:
        """Neo's fast path, step 2: the LoRAs again, onto the new weights."""

        self.engine.current_lora_hash = str([])
        if getattr(p, "disable_extra_networks", False):
            return
        data = getattr(p, "extra_network_data", None)
        if not data:
            return
        from modules import extra_networks

        extra_networks.activate(p, data)


def _unet_prefix(state_dict: Any) -> str:
    """Neo's own rule (`huggingface_guess.detection.unet_prefix_from_state_dict`,
    :438): key names only. Imported here, at the one place it is needed,
    because the package imports torch."""

    from huggingface_guess.detection import unet_prefix_from_state_dict

    return unet_prefix_from_state_dict(state_dict)


def _diffusion_shapes(path: str) -> dict[str, tuple[int, ...]]:
    """A checkpoint's diffusion weights as {name: shape}, from its header.

    The names Neo's fast path would load: `preprocess_state_dict`'s prefix
    rule (`loader.py` :756), then `unet_prefix_from_state_dict`
    (`detection.py` :438, key names only) stripped, `llm*` left out.
    """

    import json
    import struct

    # The format's own header: an 8-byte little-endian length, then JSON of
    # {name: {"dtype", "shape", "data_offsets"}}. `safe_open` took 3 s to give
    # the same answer for one 3.9 GiB file; this reads a few hundred KB.
    with open(path, "rb") as handle:
        (length,) = struct.unpack("<Q", handle.read(8))
        if length <= 0 or length > 100 * 1024 * 1024:
            raise _refused(f"{Path(path).name} is not a readable .safetensors file.")
        header = json.loads(handle.read(length))
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
