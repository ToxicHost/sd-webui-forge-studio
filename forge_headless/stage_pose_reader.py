"""ST7. Stage's "Pose from a picture": a person's pose, read from a picture.

SAM 3D Body (Meta, SAM License) reads one person's body and hand pose from a
picture in MHR's terms, which is the body model Stage poses. The weights are
an opt-in download (`forge_studio.stage_assets.PICTURE_MODEL`, 2.8 GB); the
model code is ComfyUI's implementation (GPL-3.0) in `third_party/studio_sam3d`,
with plain PyTorch in place of ComfyUI's internals. Measured on the owner's
card (E7, `Evidence/controlnet-expansion-2026-11-06/e7/RESULTS.md`): 2.8 GiB
peak, about half a second a picture once loaded.

The model stays in system memory between reads and is on the GPU only while it
reads; the caller holds the job coordinator's one-job gate around `read`
(`gpu_side_task`), as the ControlNet preview does. Nothing here imports torch
at module scope (`test_headless_forge_boundary`).

What a read returns, per person, is every MHR joint's rotation in the
picture's camera frame (x right, y up, z toward the camera: three.js's camera
space), as quaternions [x, y, z, w] in MHR's joint order, which is Stage's bone
order. Stage turns them into its own bone rotations. Review:
`Evidence/source-review/ST7-pose-from-picture.md`.
"""

from __future__ import annotations

import base64
import binascii
import io
import sys
import threading
from pathlib import Path
from typing import Any

_APP = Path(__file__).resolve().parents[1]
_VENDOR = _APP / "third_party"

#: A picture is scaled down to this on its longest side before reading; the
#: model sees a 512 px crop of the person, so more only costs decode time.
MAX_SIDE = 1536
#: Weights the reader leaves out: the MediaPipe face landmarker ComfyUI ships
#: in the same file (face expressions, which Stage does not use), and a hand
#: classifier ComfyUI's own loader also drops.
_SKIPPED_KEYS = ("face_landmarker.", "hand_cls_embed.")


class PoseReadError(Exception):
    """Named for the owner. `code` is stable; the message is the sentence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def decode_picture(data_url: Any) -> Any:
    """A data URL (or bare base64) to an RGB PIL image no larger than MAX_SIDE."""

    if not isinstance(data_url, str) or not data_url.strip():
        raise PoseReadError("NO_PICTURE", "Choose a picture first.")
    text = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise PoseReadError("NOT_A_PICTURE", "That file is not a picture Studio can read.") from None
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise PoseReadError("NOT_A_PICTURE", "That file is not a picture Studio can read.") from None
    image = image.convert("RGB")
    longest = max(image.size)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.LANCZOS)
    return image


def rotation_to_quaternion(matrix: Any) -> list[float]:
    """A 3x3 rotation (nested lists or array) to [x, y, z, w], w >= 0."""

    m = [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = (trace + 1.0) ** 0.5 * 2
        w, x, y, z = 0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = (1.0 + m[0][0] - m[1][1] - m[2][2]) ** 0.5 * 2
        w, x, y, z = (m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = (1.0 + m[1][1] - m[0][0] - m[2][2]) ** 0.5 * 2
        w, x, y, z = (m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s
    else:
        s = (1.0 + m[2][2] - m[0][0] - m[1][1]) ** 0.5 * 2
        w, x, y, z = (m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s
    norm = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    sign = -1.0 if w < 0 else 1.0
    return [round(sign * v / norm, 6) for v in (x, y, z, w)]


class PoseReader:
    """Loads the model once (on first read) and reads one picture at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._path: Path | None = None

    def loaded(self) -> bool:
        return self._model is not None

    def read(self, model_path: Path | None, data_url: Any) -> dict[str, Any]:
        if model_path is None or not Path(model_path).is_file():
            raise PoseReadError("NO_MODEL", "The pose-reading model is not installed. Get it in Stage first.")
        image = decode_picture(data_url)
        with self._lock:
            model = self._load(Path(model_path))
            import numpy as np
            import torch

            from studio_sam3d.run_utils import run_batched_frames
            from studio_sam3d.shim import get_torch_device

            device = get_torch_device()
            frame = torch.from_numpy(np.asarray(image).copy())
            box = torch.tensor([[0.0, 0.0, float(image.width), float(image.height)]])
            try:
                model.to(device)
                with torch.inference_mode():
                    frames = run_batched_frames(model, [frame], [box], None, model.image_size, "full")
            except torch.cuda.OutOfMemoryError:
                raise PoseReadError("OUT_OF_MEMORY",
                                    "The card ran out of memory reading the pose. Unload a model and try again.") from None
            finally:
                model.to("cpu")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
        people = []
        for person in frames[0] if frames else []:
            rotations = person.get("pred_global_rots")
            if rotations is None:
                continue
            if hasattr(rotations, "detach"):
                rotations = rotations.detach().float().cpu().numpy()
            matrices = np.asarray(rotations, dtype=np.float64).reshape(-1, 3, 3)
            people.append({
                "world": [rotation_to_quaternion(m) for m in matrices],
                "box": [round(float(v), 1) for v in np.asarray(person["bbox"]).reshape(-1)[:4]],
            })
        if not people:
            raise PoseReadError("NO_PERSON", "No person was found in that picture.")
        return {"people": people, "width": image.width, "height": image.height}

    # -- loading ---------------------------------------------------------------

    def _load(self, path: Path) -> Any:
        if self._model is not None and self._path == path:
            return self._model
        self._model = None
        import torch
        from safetensors.torch import load_file

        if str(_VENDOR) not in sys.path:
            sys.path.insert(0, str(_VENDOR))
        from studio_sam3d.dino3 import DINOv3ViTRopePositionEmbedding
        from studio_sam3d.sam3d_body.model import model as model_module
        from studio_sam3d.shim import operations

        bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
        dtype = torch.bfloat16 if bf16 else torch.float32
        state = load_file(str(path), device="cpu")
        state = {k.replace(".layers.0.0.", ".layers.0."): v for k, v in state.items()
                 if not k.startswith(_SKIPPED_KEYS)}
        with torch.device("meta"):
            model = model_module.SAM3DBody(dtype=dtype, operations=operations)
        missing, unexpected = model.load_state_dict(state, strict=False, assign=True)
        if missing or unexpected:
            raise PoseReadError("WRONG_MODEL", "That model file is not the SAM 3D Body file Studio expects.")
        # Buffers the file does not carry are made in __init__, on meta here.
        for name, buffer in list(model.named_buffers()):
            if not buffer.is_meta:
                continue
            owner_name, _, leaf = name.rpartition(".")
            owner = model.get_submodule(owner_name) if owner_name else model
            if leaf == "image_mean":
                value = torch.tensor(model_module.IMAGE_MEAN).view(-1, 1, 1)
            elif leaf == "image_std":
                value = torch.tensor(model_module.IMAGE_STD).view(-1, 1, 1)
            elif leaf == "inv_freq" and isinstance(owner, DINOv3ViTRopePositionEmbedding):
                value = 1 / owner.base ** torch.arange(0, 1, 4 / owner.head_dim, dtype=torch.float32)
            else:
                raise PoseReadError("WRONG_MODEL", f"The model has a part Studio cannot set up ({name}).")
            owner._buffers[leaf] = value
        model.backbone_dtype = dtype
        model.eval()
        self._model, self._path = model, path
        return model


READER = PoseReader()
