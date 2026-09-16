import logging
import re
from functools import lru_cache

import torch
from PIL import Image

from backend import memory_management
from backend.memory_management import free_memory, module_size, soft_empty_cache
from modules import devices, errors, modelloader, torch_utils
from modules.shared import opts
from modules.upscaler import Upscaler, UpscalerData
from modules.upscaler_utils import upscale_with_model

logger = logging.getLogger(__name__)

# Only the allocator giving up. A broad `except Exception` here would swallow
# a real upscaler bug and report it as a memory fallback.
_ALLOCATION_ERRORS = tuple(
    candidate
    for candidate in (
        getattr(torch, "OutOfMemoryError", None),
        getattr(torch.cuda, "OutOfMemoryError", None),
    )
    if isinstance(candidate, type)
)

PREFER_HALF = opts.prefer_fp16_upscalers
if PREFER_HALF:
    print("[Upscalers] Prefer Half-Precision:", PREFER_HALF)

MEM_RATIO = {"DRCT": 0.75, "DAT": 0.25}


def _record_decision(*, requested: bool, effective: str, reason: str) -> None:
    """Hand the outcome to the running job, if one is listening.

    The upscale runs three Neo frames below anything Studio owns, so the
    decision cannot be returned upward. It is recorded into the job scope
    instead, and the port reads it back out when the job finishes.
    """
    from forge_headless.job_options import record

    record(
        gpu_tile_composite_requested=bool(requested),
        upscale_composite_effective=str(effective),
        upscale_composite_reason=str(reason),
    )


def _preflight():
    """The composite policy, imported on use rather than at module scope.

    `forge_headless.__init__` eagerly builds the adapter chain, which reaches
    back into `modules`. This module is imported from `modules.modelloader`
    during that same chain, so a module-scope import here would close the
    loop. By the time an upscale runs, everything is already loaded and this
    costs a dict lookup.
    """
    from forge_headless import upscale_preflight

    return upscale_preflight


class UpscalerESRGAN(Upscaler):
    def __init__(self, dirname: str):
        self.user_path = dirname
        self.model_path = dirname
        super().__init__(True)

        self.name = "ESRGAN"
        self.model_url = "https://github.com/cszn/KAIR/releases/download/v1.0/ESRGAN.pth"
        self.model_name = "ESRGAN"
        self.scalers = []

        model_paths = self.find_models(ext_filter=[".pt", ".pth", ".safetensors"])
        if len(model_paths) == 0:
            scaler_data = UpscalerData(self.model_name, self.model_url, self, 4)
            self.scalers.append(scaler_data)

        for file in model_paths:
            if file.startswith("http"):
                name = self.model_name
            else:
                name = modelloader.friendly_name(file)

            if match := re.search(r"(\d)[xX]|[xX](\d)", name):
                scale = int(match.group(1) or match.group(2))
            else:
                scale = 4

            scaler_data = UpscalerData(name, file, self, scale)
            self.scalers.append(scaler_data)

    def do_upscale(self, img: Image.Image, selected_model: str):
        soft_empty_cache()

        try:
            model = self.load_model(selected_model)
        except Exception:
            errors.report(f"Unable to load {selected_model}", exc_info=True)
            return img

        # Tile geometry and weights only. This reservation covers the per-tile
        # inference workspace, which BOTH composite paths pay. It carried a
        # 1.1 factor for GPU compositing that was measured at +15.4 MiB
        # against a real cost of +271 MiB at 1024 base, so the factor is gone
        # and the real cost is preflighted below instead.
        free_memory(
            #       (W * H)       * C *          dtype            *    scale    *                  ratio                       *  MB
            (opts.ESRGAN_tile**2) * 3 * (2 if PREFER_HALF else 4) * model.scale * MEM_RATIO.get(model.architecture.name, 0.05) * 1024 + module_size(model.model),
            device=devices.device_esrgan,
        )

        composite_on_gpu = self.composite_on_gpu_for(img, model)

        if composite_on_gpu:
            try:
                return upscale_with_model(
                    model=model,
                    img=img,
                    tile_size=opts.ESRGAN_tile,
                    tile_overlap=opts.ESRGAN_tile_overlap,
                    composite_on_gpu=True,
                )
            except _ALLOCATION_ERRORS:
                # The preflight passed and the allocator still refused. That is
                # runtime variance, not the normal path -- the estimate is the
                # gate, this is the residual. Release whatever was reached
                # before retrying, or the CPU path inherits the pressure.
                soft_empty_cache()
                logger.warning(
                    "UPSCALE COMPOSITE %s",
                    _preflight().describe_decision(
                        requested="gpu",
                        effective="cpu",
                        reason=_preflight().REASON_ALLOCATION_FALLBACK,
                    ),
                )
                _record_decision(
                    requested=True,
                    effective=_preflight().MODE_CPU,
                    reason=_preflight().REASON_ALLOCATION_FALLBACK,
                )

        return upscale_with_model(
            model=model,
            img=img,
            tile_size=opts.ESRGAN_tile,
            tile_overlap=opts.ESRGAN_tile_overlap,
            composite_on_gpu=False,
        )

    def composite_on_gpu_for(self, img: Image.Image, model) -> bool:
        """Whether this image can afford to composite on the device.

        Every value is read at the moment of the upscale. The scale comes from
        the loaded model rather than `UpscalerData.scale`, which is parsed from
        the filename and defaults to 4 when the name says nothing; the element
        size comes from a real parameter rather than `PREFER_HALF`, which is a
        preference that spandrel may not have honoured.
        """
        preflight = _preflight()
        requested = bool(opts.composite_tiles_on_gpu)

        working_set = preflight.composite_working_set(
            width=img.width,
            height=img.height,
            scale=model.scale,
            itemsize=torch_utils.get_param(model).element_size(),
            tiles=preflight.tile_count(
                img.width, img.height, opts.ESRGAN_tile, opts.ESRGAN_tile_overlap
            ),
        )

        # `get_free_memory` answers for every device, but only some of those
        # answers are about VRAM: it returns host RAM on CPU and MPS and a
        # hardcoded 1 GiB on DirectML.
        answerable = (
            memory_management.cpu_state == memory_management.CPUState.GPU
            and not memory_management.directml_enabled
        )
        free_bytes = (
            memory_management.get_free_memory(devices.device_esrgan) if answerable else 0
        )
        reserve_bytes = memory_management.minimum_inference_memory()

        mode, reason = preflight.choose_composite_mode(
            requested_gpu=requested,
            device_reports_free_memory=answerable,
            free_bytes=free_bytes,
            reserve_bytes=reserve_bytes,
            working_set_bytes=working_set["peak"],
            # Read from the module, not from `opts`: the running job is
            # consulted before `opts` and could otherwise lift the ceiling.
            process_disabled=preflight.process_disabled(),
        )

        logger.info(
            "UPSCALE COMPOSITE %s",
            preflight.describe_decision(
                requested="gpu" if requested else "cpu",
                effective=mode,
                reason=reason,
                working_set_bytes=working_set["peak"],
                free_bytes=free_bytes if answerable else None,
                reserve_bytes=reserve_bytes,
            ),
        )
        _record_decision(requested=requested, effective=mode, reason=reason)
        return mode == preflight.MODE_GPU

    @lru_cache(maxsize=4, typed=False)
    def load_model(self, path: str):
        if not path.startswith("http"):
            filename = path
        else:
            filename = modelloader.load_file_from_url(
                url=path,
                model_dir=self.model_download_path,
                file_name=path.rsplit("/", 1)[-1],
            )

        model = modelloader.load_spandrel_model(filename, device=devices.cpu, prefer_half=PREFER_HALF)
        model.to(devices.device_esrgan)
        return model
