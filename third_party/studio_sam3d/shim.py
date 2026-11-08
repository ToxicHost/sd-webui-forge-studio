"""Plain-PyTorch stand-ins for the few ComfyUI internals SAM 3D Body's code uses.

ComfyUI's SAM 3D Body implementation (GPL-3.0, Comfy-Org/ComfyUI at the commit
in SOURCE.txt) runs here with no ComfyUI: every ComfyUI import in the copied
files points at this module instead. Written for Studio (ST7, Stage's "Pose
from a picture"); the copy is made by
`Evidence/controlnet-expansion-2026-11-06/e7/build_port.py`, which is the only
change made to ComfyUI's files (their import lines).
"""
import math
from types import SimpleNamespace

import torch
import torch.nn.functional as F
from torch import nn


# -- comfy.model_management -------------------------------------------------

def get_torch_device():
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def cast_to(weight, dtype=None, device=None, non_blocking=False, copy=False, **_):
    if (device is None or weight.device == device) and (dtype is None or weight.dtype == dtype) and not copy:
        return weight
    return weight.to(device=device, dtype=dtype, non_blocking=non_blocking, copy=copy)


def cast_to_device(tensor, device, dtype, copy=False):
    return cast_to(tensor, dtype=dtype, device=device, copy=copy)


# -- comfy.ops ---------------------------------------------------------------

def cast_to_input(weight, input, non_blocking=False, copy=True):
    return cast_to(weight, input.dtype, input.device, non_blocking=non_blocking, copy=copy)


def _cast(module, x):
    w = module.weight.to(x.dtype) if module.weight is not None else None
    b = module.bias.to(x.dtype) if getattr(module, "bias", None) is not None else None
    return w, b


class Linear(nn.Linear):
    def forward(self, x):
        w, b = _cast(self, x)
        return F.linear(x, w, b)


class Conv2d(nn.Conv2d):
    def forward(self, x):
        w, b = _cast(self, x)
        return self._conv_forward(x, w, b)


class LayerNorm(nn.LayerNorm):
    def forward(self, x):
        w, b = _cast(self, x) if self.weight is not None else (None, None)
        return F.layer_norm(x, self.normalized_shape, w, b, self.eps)


class Embedding(nn.Embedding):
    pass


#: what ComfyUI passes as `operations`: weights stay in the file's dtype and
#: are cast to the activation's dtype at use, like comfy's manual-cast ops.
operations = SimpleNamespace(Linear=Linear, Conv2d=Conv2d, LayerNorm=LayerNorm, Embedding=Embedding)


def scaled_dot_product_attention(q, k, v, *args, **kwargs):
    return F.scaled_dot_product_attention(q, k, v, *args, **kwargs)


# -- comfy.ldm.modules.attention ---------------------------------------------

def optimized_attention(q, k, v, heads, mask=None, attn_precision=None, skip_reshape=False,
                        skip_output_reshape=False, **kwargs):
    if skip_reshape:
        b, _, _, dim_head = q.shape
    else:
        b, _, dim_head = q.shape
        dim_head //= heads
        q, k, v = (t.view(b, -1, heads, dim_head).transpose(1, 2) for t in (q, k, v))
    if mask is not None:
        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        if mask.ndim == 3:
            mask = mask.unsqueeze(1)
    extra = {key: val for key, val in kwargs.items() if key in ("scale",)}
    out = F.scaled_dot_product_attention(q, k, v, attn_mask=mask, dropout_p=0.0, is_causal=False, **extra)
    if not skip_output_reshape:
        out = out.transpose(1, 2).reshape(b, -1, heads * dim_head)
    return out


def optimized_attention_for_device(device, mask=False, small_input=False):
    return optimized_attention


# -- comfy.ldm.cascade.common ----------------------------------------------

def LayerNorm2d_op(operations):
    class LayerNorm2d(operations.LayerNorm):
        def forward(self, x):
            return super().forward(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)
    return LayerNorm2d


# -- comfy.image_encoders.dino2 ----------------------------------------------

class LayerScale(nn.Module):
    def __init__(self, dim, dtype, device, operations):
        super().__init__()
        self.lambda1 = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x):
        return x * cast_to_device(self.lambda1, x.device, x.dtype)


# -- comfy.ldm.sam3.sam ------------------------------------------------------

class PositionEmbeddingRandom(nn.Module):
    """Fourier feature positional encoding with random gaussian projection."""
    def __init__(self, num_pos_feats=64, scale=None):
        super().__init__()
        self.register_buffer("positional_encoding_gaussian_matrix", (scale or 1.0) * torch.randn(2, num_pos_feats))

    def _encode(self, normalized_coords):
        orig_dtype = normalized_coords.dtype
        proj_matrix = self.positional_encoding_gaussian_matrix.to(device=normalized_coords.device, dtype=torch.float32)
        projected = 2 * math.pi * (2 * normalized_coords.float() - 1) @ proj_matrix
        return torch.cat([projected.sin(), projected.cos()], dim=-1).to(orig_dtype)

    def forward(self, size, device=None):
        h, w = size
        dev = device if device is not None else self.positional_encoding_gaussian_matrix.device
        ones = torch.ones((h, w), device=dev, dtype=torch.float32)
        norm_xy = torch.stack([(ones.cumsum(1) - 0.5) / w, (ones.cumsum(0) - 0.5) / h], dim=-1)
        return self._encode(norm_xy).permute(2, 0, 1).unsqueeze(0)

    def forward_with_coords(self, pixel_coords, image_size):
        norm = pixel_coords.clone()
        norm[:, :, 0] /= image_size[1]
        norm[:, :, 1] /= image_size[0]
        return self._encode(norm)


# -- comfy_extras.mediapipe.face_landmarker: face expressions, not needed --------

class FaceLandmarker(nn.Module):
    def __init__(self, *args, **kwargs):
        super().__init__()


# -- comfy.utils / comfy.ldm.sam3.tracker ------------------------------------------

class ProgressBar:
    def __init__(self, total):
        self.total = total

    def update(self, n):
        pass


def unpack_masks(*args, **kwargs):
    raise NotImplementedError("SAM3 tracking is not part of the port")
