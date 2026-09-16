"""Exact-path intake and header-only safetensors preflight.

Two jobs, both before any tensor payload is touched:

1. confirm each supplied path is *exactly* one the owner authorized -- not a
   sibling, not a link to it, not a case-variant of a different file -- without
   ever listing the parent directory;
2. read only the safetensors header of each file and decide, from key names,
   shapes, and dtypes, whether the three files form a combination the retained
   Forge loader actually supports.

A safetensors file begins with an 8-byte little-endian header length followed by
that many bytes of JSON. Reading it means reading a bounded prefix; no tensor
payload is materialized, and the file is never hashed in full.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import HeadlessError
from .load_authorization import (
    ROLE_CHECKPOINT,
    ROLE_TEXT_ENCODER,
    ROLE_VAE,
    AuthorizedFile,
    ControlledLoadAuthorization,
)


#: Refuse a header claiming to be larger than this. A real safetensors header is
#: kilobytes to low megabytes; anything beyond is malformed or hostile.
MAX_HEADER_BYTES = 64 * 1024 * 1024

PREFLIGHT_COMPATIBLE = "MODEL_TRIPLET_PREFLIGHT_COMPATIBLE"
PREFLIGHT_INCOMPATIBLE = "MODEL_TRIPLET_PREFLIGHT_INCOMPATIBLE"
PREFLIGHT_UNSUPPORTED = "MODEL_TRIPLET_PREFLIGHT_UNSUPPORTED"
PREFLIGHT_INCONCLUSIVE = "MODEL_TRIPLET_PREFLIGHT_INCONCLUSIVE"


@dataclass(frozen=True)
class IntakeRecord:
    """One authorized file, validated but not yet read."""

    role: str
    file_id: str
    suffix: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "file_id": self.file_id,
            "suffix": self.suffix,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class HeaderSummary:
    """What the safetensors header says, summarized rather than dumped.

    Raw tensor-key inventories are deliberately not retained: they are large,
    and they can encode a filename or a training path in the metadata. Prefix
    counts and a handful of probe keys are enough to select a loader.
    """

    role: str
    file_id: str
    tensor_count: int
    total_declared_bytes: int
    dtypes: tuple[str, ...]
    top_prefixes: tuple[tuple[str, int], ...]
    metadata_keys: tuple[str, ...]
    probe_shapes: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "file_id": self.file_id,
            "tensor_count": self.tensor_count,
            "total_declared_bytes": self.total_declared_bytes,
            "dtypes": list(self.dtypes),
            "top_prefixes": [
                {"prefix": prefix, "tensors": count}
                for prefix, count in self.top_prefixes
            ],
            "metadata_keys": list(self.metadata_keys),
            "probe_shapes": {
                key: list(shape) for key, shape in sorted(self.probe_shapes.items())
            },
        }


def validate_exact_path(
    authorization: ControlledLoadAuthorization,
    role: str,
) -> IntakeRecord:
    """Validate one authorized file without enumerating its parent directory.

    Uses `lstat` first, so a symlink or reparse point is detected *as a link*
    rather than silently followed to whatever it points at.
    """

    entry: AuthorizedFile = authorization.file_for(role)
    path = entry.path

    # Exact-path re-check: the authorization is the only source of the path, but
    # asserting it again keeps this function honest if it is ever called with a
    # path from elsewhere.
    if authorization.role_for(path) != role:
        raise HeadlessError(
            "CONTROLLED_LOAD_PATH_NOT_AUTHORIZED",
            "That file is not one of the authorized model files.",
        )

    try:
        stat_result = os.lstat(path)
    except FileNotFoundError:
        raise HeadlessError(
            "CONTROLLED_LOAD_FILE_MISSING",
            f"The authorized {role} file is not present.",
        ) from None
    except OSError:
        raise HeadlessError(
            "CONTROLLED_LOAD_FILE_UNREADABLE",
            f"The authorized {role} file could not be inspected.",
        ) from None

    if os.path.islink(path):
        raise HeadlessError(
            "CONTROLLED_LOAD_PATH_IS_LINK",
            f"The authorized {role} path is a link, which is not accepted.",
        )
    # Windows reparse points that are not symlinks (junctions) still set the
    # reparse attribute in st_file_attributes.
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes and attributes & reparse_flag:
        raise HeadlessError(
            "CONTROLLED_LOAD_PATH_IS_REPARSE_POINT",
            f"The authorized {role} path is a reparse point, which is not accepted.",
        )
    if not os.path.isfile(path):
        raise HeadlessError(
            "CONTROLLED_LOAD_NOT_A_REGULAR_FILE",
            f"The authorized {role} path is not a regular file.",
        )

    return IntakeRecord(
        role=role,
        file_id=entry.file_id,
        suffix=path.suffix.lower(),
        size_bytes=int(stat_result.st_size),
    )


def read_safetensors_header(path: Path) -> dict[str, object]:
    """Read only the safetensors JSON header. No tensor payload is materialized."""

    try:
        with open(path, "rb") as handle:
            raw_length = handle.read(8)
            if len(raw_length) != 8:
                raise HeadlessError(
                    "CONTROLLED_LOAD_HEADER_TRUNCATED",
                    "The file is too short to be a safetensors container.",
                )
            (length,) = struct.unpack("<Q", raw_length)
            if length <= 0 or length > MAX_HEADER_BYTES:
                raise HeadlessError(
                    "CONTROLLED_LOAD_HEADER_IMPLAUSIBLE",
                    "The safetensors header length is not plausible.",
                )
            payload = handle.read(length)
    except HeadlessError:
        raise
    except OSError:
        raise HeadlessError(
            "CONTROLLED_LOAD_FILE_UNREADABLE",
            "The authorized file could not be read.",
        ) from None

    if len(payload) != length:
        raise HeadlessError(
            "CONTROLLED_LOAD_HEADER_TRUNCATED",
            "The safetensors header is truncated.",
        )
    try:
        header = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HeadlessError(
            "CONTROLLED_LOAD_HEADER_MALFORMED",
            "The safetensors header is not valid JSON.",
        ) from None
    if not isinstance(header, dict):
        raise HeadlessError(
            "CONTROLLED_LOAD_HEADER_MALFORMED",
            "The safetensors header is not an object.",
        )
    return header


def summarize_header(
    role: str,
    file_id: str,
    header: dict[str, object],
    *,
    probe_keys: tuple[str, ...] = (),
    prefix_depth: int = 2,
    top_n: int = 12,
) -> HeaderSummary:
    """Summarize a header into loader-selection facts, not a key dump."""

    tensors = {
        key: value
        for key, value in header.items()
        if key != "__metadata__" and isinstance(value, dict)
    }
    dtypes: set[str] = set()
    prefixes: dict[str, int] = {}
    declared = 0
    probe_shapes: dict[str, tuple[int, ...]] = {}

    for key, spec in tensors.items():
        dtype = spec.get("dtype")
        if isinstance(dtype, str):
            dtypes.add(dtype)
        offsets = spec.get("data_offsets")
        if isinstance(offsets, list) and len(offsets) == 2:
            try:
                declared += int(offsets[1]) - int(offsets[0])
            except (TypeError, ValueError):
                pass
        parts = key.split(".")
        prefix = ".".join(parts[:prefix_depth]) if len(parts) > 1 else key
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
        if key in probe_keys:
            shape = spec.get("shape")
            if isinstance(shape, list):
                probe_shapes[key] = tuple(int(dim) for dim in shape)

    metadata = header.get("__metadata__")
    metadata_keys: tuple[str, ...] = ()
    if isinstance(metadata, dict):
        metadata_keys = tuple(sorted(str(key) for key in metadata))

    ranked = sorted(prefixes.items(), key=lambda item: (-item[1], item[0]))
    return HeaderSummary(
        role=role,
        file_id=file_id,
        tensor_count=len(tensors),
        total_declared_bytes=declared,
        dtypes=tuple(sorted(dtypes)),
        top_prefixes=tuple(ranked[:top_n]),
        metadata_keys=metadata_keys,
        probe_shapes=probe_shapes,
    )


def first_key_with(header: dict[str, object], needle: str) -> str | None:
    """Find one tensor key containing `needle`, for shape probing."""
    for key in header:
        if key != "__metadata__" and needle in key:
            return key
    return None


def shape_of(header: dict[str, object], key: str) -> tuple[int, ...] | None:
    spec = header.get(key)
    if isinstance(spec, dict):
        shape = spec.get("shape")
        if isinstance(shape, list):
            return tuple(int(dim) for dim in shape)
    return None


def count_keys_with(header: dict[str, object], needle: str) -> int:
    return sum(
        1 for key in header if key != "__metadata__" and needle in key
    )


__all__ = (
    "HeaderSummary",
    "IntakeRecord",
    "MAX_HEADER_BYTES",
    "PREFLIGHT_COMPATIBLE",
    "PREFLIGHT_INCOMPATIBLE",
    "PREFLIGHT_INCONCLUSIVE",
    "PREFLIGHT_UNSUPPORTED",
    "count_keys_with",
    "first_key_with",
    "read_safetensors_header",
    "shape_of",
    "summarize_header",
    "validate_exact_path",
)
