"""The EXR encoder behind Export EXR (Standard). P12.

In `forge_headless` because it needs numpy, which Studio's owned package does
not import (`test_device_capability.OwnedPackagePurityTests`). The route and
its folder rules are `forge_studio/exr_export.py`.

Source review: Evidence/source-review/P12-exr-export.md
"""

from __future__ import annotations

import struct
from typing import Any


def image_to_rgb_float(image: Any) -> Any:
    """HxWx3 float32 in [0, 1]. `convert("RGB")` is the Extension's grey
    broadcast and alpha drop, and also reads a palette image as colours."""

    import numpy as np

    return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def encode_exr(rgb: Any) -> bytes:
    """An uncompressed single-part scanline OpenEXR, float32 B, G, R.

    The Extension's `_write_exr_minimal` (`studio_api.py:1316-1381`),
    returning the bytes so the caller can create the file exclusively.
    """

    import numpy as np

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("rgb must be HxWx3")
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    planes = [np.ascontiguousarray(rgb[:, :, c], dtype="<f4") for c in (2, 1, 0)]

    def attribute(name: str, kind: str, payload: bytes) -> bytes:
        return (name.encode("ascii") + b"\x00" + kind.encode("ascii") + b"\x00"
                + struct.pack("<I", len(payload)) + payload)

    channels = bytearray()
    for name in ("B", "G", "R"):          # alphabetical, as EXR requires
        channels += name.encode("ascii") + b"\x00"
        channels += struct.pack("<i", 2)  # FLOAT
        channels += struct.pack("<I", 0)  # pLinear + reserved
        channels += struct.pack("<ii", 1, 1)
    channels += b"\x00"
    box = struct.pack("<iiii", 0, 0, width - 1, height - 1)

    out = bytearray(b"\x76\x2f\x31\x01" + struct.pack("<I", 2))
    out += attribute("channels", "chlist", bytes(channels))
    out += attribute("compression", "compression", b"\x00")
    out += attribute("dataWindow", "box2i", box)
    out += attribute("displayWindow", "box2i", box)
    out += attribute("lineOrder", "lineOrder", b"\x00")
    out += attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    out += attribute("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0))
    out += attribute("screenWindowWidth", "float", struct.pack("<f", 1.0))
    out += b"\x00"

    line_bytes = 3 * width * 4
    first = len(out) + height * 8
    for y in range(height):
        out += struct.pack("<Q", first + y * (8 + line_bytes))
    for y in range(height):
        out += struct.pack("<iI", y, line_bytes)
        for plane in planes:
            out += plane[y].tobytes()
    return bytes(out)
