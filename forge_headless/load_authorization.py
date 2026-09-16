"""Single-use authorization for one controlled real model load.

Everything about this type is designed so a real load cannot happen by accident:

* it must be constructed in-process by a local caller -- there is no factory that
  reads an environment variable, a config file, or an HTTP parameter, and
  `forge_studio` never imports this module;
* it is single-use: `consume()` succeeds exactly once, and a consumed
  authorization can never be revived;
* it names the exact files it authorizes, by absolute path, and refuses anything
  else -- including a sibling in the same directory;
* it carries its own timeout and VRAM ceiling, so those limits travel with the
  permission rather than living in whatever code happens to run;
* it does not persist. Nothing writes it to disk and nothing reads it back, so a
  restart begins unauthorized.

Absolute paths live only inside the object. `to_public_dict()` is what may be
reported, and it carries opaque file identifiers and sanitized role names.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .contracts import HeadlessError


#: Sanitized role names. These, not filenames, are what reports contain.
ROLE_CHECKPOINT = "checkpoint"
ROLE_TEXT_ENCODER = "text_encoder"
ROLE_VAE = "vae"

AUTHORIZED_ROLES = (ROLE_CHECKPOINT, ROLE_TEXT_ENCODER, ROLE_VAE)

#: Of those, the ones an authorization must name. A checkpoint that carries
#: its own text encoder and VAE -- SDXL, SD 1.5 -- authorizes ONE file, and an
#: authorization for a file that does not exist is not a safety property, it is
#: a refusal to load a model Forge supports. The unknown-role check below is
#: unchanged: naming something outside AUTHORIZED_ROLES is still refused.
REQUIRED_AUTHORIZED_ROLES = (ROLE_CHECKPOINT,)

DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_VRAM_CEILING_BYTES = 14 * 1024**3


def opaque_file_id(role: str, path: Path) -> str:
    """A stable, non-reversible identifier for one authorized file.

    Derived from the role and the resolved path, so the same file always gets
    the same id, and the id cannot be turned back into a path.
    """
    digest = hashlib.sha256()
    digest.update(role.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(path).encode("utf-8", "surrogatepass"))
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class AuthorizedFile:
    """One exact file the owner named, with its sanitized public view."""

    role: str
    path: Path
    file_id: str

    def to_public_dict(self) -> dict[str, object]:
        return {"role": self.role, "file_id": self.file_id}


class ControlledLoadAuthorization:
    """Permission for exactly one real model load, of exactly these files."""

    def __init__(
        self,
        files: dict[str, str | os.PathLike[str]],
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        vram_ceiling_bytes: int = DEFAULT_VRAM_CEILING_BYTES,
    ) -> None:
        missing = [role for role in REQUIRED_AUTHORIZED_ROLES
                   if role not in files]
        if missing:
            raise HeadlessError(
                "CONTROLLED_LOAD_AUTHORIZATION_INCOMPLETE",
                "The authorization does not name every required role.",
            )
        if set(files) - set(AUTHORIZED_ROLES):
            raise HeadlessError(
                "CONTROLLED_LOAD_AUTHORIZATION_UNKNOWN_ROLE",
                "The authorization names a role that is not recognized.",
            )
        if timeout_seconds < 1 or timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
            raise HeadlessError(
                "CONTROLLED_LOAD_TIMEOUT_INVALID",
                "The authorization timeout is outside the permitted range.",
            )
        if vram_ceiling_bytes < 1 or vram_ceiling_bytes > DEFAULT_VRAM_CEILING_BYTES:
            raise HeadlessError(
                "CONTROLLED_LOAD_CEILING_INVALID",
                "The authorization VRAM ceiling is outside the permitted range.",
            )

        # `os.path.abspath`, not `resolve()`: resolving would follow a link, and
        # link rejection is a separate explicit check at intake time.
        self._files: dict[str, AuthorizedFile] = {}
        for role in AUTHORIZED_ROLES:
            if role not in files:
                continue  # the checkpoint carries this component itself
            absolute = Path(os.path.abspath(os.fspath(files[role])))
            self._files[role] = AuthorizedFile(
                role=role,
                path=absolute,
                file_id=opaque_file_id(role, absolute),
            )

        self.authorization_id = f"auth-{uuid4().hex[:16]}"
        self.timeout_seconds = int(timeout_seconds)
        self.vram_ceiling_bytes = int(vram_ceiling_bytes)
        self._consumed = False
        self._lock = Lock()

    # -- permission --------------------------------------------------------

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed

    def consume(self) -> None:
        """Spend the single attempt. The second call always raises."""
        with self._lock:
            if self._consumed:
                raise HeadlessError(
                    "CONTROLLED_LOAD_ALREADY_ATTEMPTED",
                    "The single authorized load attempt has been used.",
                )
            self._consumed = True

    # -- exact-path matching ----------------------------------------------

    def role_for(self, candidate: str | os.PathLike[str]) -> str:
        """Return the role for an exactly-matching authorized path, or raise.

        Matching is by absolute path, case-insensitively on a case-insensitive
        volume and exactly otherwise -- never by basename, never by directory,
        and never by prefix. A sibling in the same directory does not match.
        """
        absolute = Path(os.path.abspath(os.fspath(candidate)))
        for role, entry in self._files.items():
            if absolute == entry.path:
                return role
            if os.path.normcase(str(absolute)) == os.path.normcase(str(entry.path)):
                return role
        raise HeadlessError(
            "CONTROLLED_LOAD_PATH_NOT_AUTHORIZED",
            "That file is not one of the authorized model files.",
        )

    def has_role(self, role: str) -> bool:
        """Whether this authorization names a file for that role.

        How a caller learns that the checkpoint carries its own component.
        """

        return role in self._files

    def loader_path(self, role: str) -> str:
        """The authorized path in exactly the type retained Forge expects.

        `backend/utils.py::load_torch_file` dispatches on `ckpt.lower()`, so a
        `Path` raises `AttributeError` before a single byte is read -- which is
        precisely how the first Phase 2B attempt died. Normalizing here, once,
        means callers cannot reintroduce it by handing a `Path` to the loader.
        """
        return str(self.file_for(role).path)

    def loader_paths(self, *roles: str) -> list[str]:
        """Several authorized paths as `str`, in the order given."""
        return [self.loader_path(role) for role in roles]

    def file_for(self, role: str) -> AuthorizedFile:
        try:
            return self._files[role]
        except KeyError:
            raise HeadlessError(
                "CONTROLLED_LOAD_AUTHORIZATION_UNKNOWN_ROLE",
                "That role is not part of this authorization.",
            ) from None

    @property
    def roles(self) -> tuple[str, ...]:
        return AUTHORIZED_ROLES

    # -- reporting ---------------------------------------------------------

    def to_public_dict(self) -> dict[str, object]:
        """The only view that may be written to evidence or returned anywhere.

        Contains no path, no filename, no directory, and no user name.
        """
        return {
            "authorization_id": self.authorization_id,
            "timeout_seconds": self.timeout_seconds,
            "vram_ceiling_bytes": self.vram_ceiling_bytes,
            "consumed": self.consumed,
            "files": [
                self._files[role].to_public_dict()
                for role in AUTHORIZED_ROLES if role in self._files
            ],
        }


@dataclass(frozen=True)
class DeviceReport:
    """What the controlled initialization established about the device."""

    initialized: bool
    device_kind: str
    device_name: str
    compute_capability: str
    torch_version: str
    torch_cuda_version: str
    total_vram_bytes: int
    free_vram_bytes: int
    supports_fp16: bool
    supports_bf16: bool
    ceiling_bytes: int
    ceiling_enforced: bool
    ceiling_mechanism: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "initialized": self.initialized,
            "device_kind": self.device_kind,
            "device_name": self.device_name,
            "compute_capability": self.compute_capability,
            "torch_version": self.torch_version,
            "torch_cuda_version": self.torch_cuda_version,
            "total_vram_bytes": self.total_vram_bytes,
            "free_vram_bytes": self.free_vram_bytes,
            "supports_fp16": self.supports_fp16,
            "supports_bf16": self.supports_bf16,
            "ceiling_bytes": self.ceiling_bytes,
            "ceiling_enforced": self.ceiling_enforced,
            "ceiling_mechanism": self.ceiling_mechanism,
            "notes": list(self.notes),
        }
