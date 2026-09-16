"""Pure platform-aware install-command selection.

This module answers one question and performs no side effects:

    platform + machine + environment overrides + optional flags
        -> which Torch command and index to use, and which optional
           accelerators are even installable here

It deliberately imports nothing beyond the standard library. It does not
import Torch, invoke pip, spawn a subprocess, touch the network, read outside
the repository, or mutate ``os.environ``. Every input is injected, so the whole
matrix is testable on one machine.

Selecting a command is not a compatibility claim. This module says what would
be installed; it does not assert that the result runs, that a wheel exists for
the platform, or that an acceleration device is present. Runtime capability is
reported separately by the backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


# Platform families
WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"
UNKNOWN_PLATFORM = "unknown"

# Acceleration families. This is an *install* family, not a runtime claim.
CUDA = "cuda"
MPS = "mps"
CPU = "cpu"
UNKNOWN_ACCELERATION = "unknown"

# Where a selection came from
ENVIRONMENT_OVERRIDE = "environment_override"
PLATFORM_DEFAULT = "platform_default"

# Retained CUDA defaults. Unchanged from the historical Windows/Linux values;
# moving them here does not alter them.
CUDA_INDEX_URL = "https://download.pytorch.org/whl/cu130"
CUDA_TORCH_SPEC = "torch==2.11.0+cu130 torchvision==0.26.0+cu130"
CUDA_XFORMERS_SPEC = "xformers==0.0.35"

# Apple Silicon default. Deliberately unpinned: the repository pins a
# ``+cu130`` build that has no macOS wheel, and no macOS wheel availability has
# been verified from this workspace. Pinning an unverified version would invent
# a fact. Owners who want a pin can set TORCH_COMMAND.
MACOS_TORCH_SPEC = "torch torchvision"

ARM64_MACHINES = frozenset({"arm64", "aarch64"})


@dataclass(frozen=True)
class TorchSelection:
    """The Torch install command selected for one platform."""

    platform_family: str
    machine: str
    acceleration_family: str
    source: str
    command: str | None
    index_url: str | None
    supported: bool
    reason: str

    @property
    def is_cuda(self) -> bool:
        """Whether CUDA-specific handling -- driver errors -- applies."""

        return self.acceleration_family == CUDA


@dataclass(frozen=True)
class AcceleratorSelection:
    """Whether one optional accelerator is installable on this platform."""

    name: str
    platform_family: str
    supported: bool
    package: str | None
    source: str
    reason: str


def platform_family(system: str) -> str:
    """Map ``platform.system()`` onto a known family."""

    normalized = str(system or "").strip().casefold()
    if normalized == "windows":
        return WINDOWS
    if normalized == "darwin":
        return MACOS
    if normalized == "linux":
        return LINUX
    return UNKNOWN_PLATFORM


def acceleration_family(family: str, machine: str) -> str:
    """Map platform and architecture onto an *install* acceleration family.

    Apple Silicon selects the MPS family because that is the build to install.
    It is not a statement that an MPS device is available at runtime -- an
    ARM64 Mac reports MPS here whether or not Torch can later use it.
    """

    normalized_machine = str(machine or "").strip().casefold()
    if family == WINDOWS:
        return CUDA
    if family == LINUX:
        # The retained source does not differentiate CUDA from non-CUDA Linux.
        # Preserved rather than expanded.
        return CUDA
    if family == MACOS:
        if normalized_machine in ARM64_MACHINES:
            return MPS
        # Intel Macs have no MPS. Never classify them as MPS-capable.
        return CPU
    return UNKNOWN_ACCELERATION


def select_torch_command(
    *,
    system: str,
    machine: str,
    environ: Mapping[str, str] | None = None,
) -> TorchSelection:
    """Select the Torch install command without side effects."""

    env = os.environ if environ is None else environ
    family = platform_family(system)
    acceleration = acceleration_family(family, machine)

    override_command = env.get("TORCH_COMMAND")
    override_index = env.get("TORCH_INDEX_URL")

    if override_command:
        # An explicit override is trusted owner input. It is never rewritten,
        # re-indexed, or validated against the platform.
        return TorchSelection(
            platform_family=family,
            machine=str(machine or ""),
            acceleration_family=acceleration,
            source=ENVIRONMENT_OVERRIDE,
            command=override_command,
            index_url=override_index,
            supported=True,
            reason="TORCH_COMMAND supplied by the owner; used verbatim.",
        )

    if acceleration == CUDA:
        index_url = override_index or CUDA_INDEX_URL
        return TorchSelection(
            platform_family=family,
            machine=str(machine or ""),
            acceleration_family=CUDA,
            source=PLATFORM_DEFAULT,
            command=(
                f"pip install {CUDA_TORCH_SPEC} --extra-index-url {index_url}"
            ),
            index_url=index_url,
            supported=True,
            reason="Retained CUDA default for this platform.",
        )

    if family == MACOS:
        # No CUDA local-version suffix and no CUDA wheel index on macOS.
        # TORCH_INDEX_URL is honoured only if the owner set it explicitly.
        return TorchSelection(
            platform_family=MACOS,
            machine=str(machine or ""),
            acceleration_family=acceleration,
            source=PLATFORM_DEFAULT,
            command=f"pip install {MACOS_TORCH_SPEC}",
            index_url=override_index,
            supported=True,
            reason=(
                "Apple Silicon: default PyPI build."
                if acceleration == MPS
                else "Intel macOS: default PyPI build, no MPS."
            ),
        )

    return TorchSelection(
        platform_family=UNKNOWN_PLATFORM,
        machine=str(machine or ""),
        acceleration_family=UNKNOWN_ACCELERATION,
        source=PLATFORM_DEFAULT,
        command=None,
        index_url=None,
        supported=False,
        reason=(
            "Unrecognized platform. No default is selected; set TORCH_COMMAND "
            "explicitly. CUDA is never chosen as a fallback."
        ),
    )


# Optional accelerators, and the platform families on which the retained source
# has a package to offer. macOS appears in none of them: every retained package
# is a CUDA build, a Windows wheel, or a linux_x86_64 wheel.
_ACCELERATOR_PLATFORMS: dict[str, frozenset[str]] = {
    "xformers": frozenset({WINDOWS, LINUX}),
    "sage": frozenset({WINDOWS, LINUX}),
    "flash": frozenset({WINDOWS, LINUX}),
    "triton": frozenset({WINDOWS, LINUX}),
    "nunchaku": frozenset({WINDOWS, LINUX}),
    "cuda_malloc": frozenset({WINDOWS, LINUX}),
}

_ACCELERATOR_ENV = {
    "xformers": "XFORMERS_PACKAGE",
    "sage": "SAGE_PACKAGE",
    "flash": "FLASH_PACKAGE",
    "triton": "TRITION_PACKAGE",
    "nunchaku": "NUNCHAKU_PACKAGE",
}


def select_accelerator(
    name: str,
    *,
    system: str,
    machine: str = "",
    environ: Mapping[str, str] | None = None,
    package: str | None = None,
) -> AcceleratorSelection:
    """Report whether one optional accelerator is installable here.

    Returns a structured unsupported result rather than letting a Windows or
    CUDA package be attempted on a platform that has none. No alternative
    macOS acceleration package is substituted -- none is offered by the
    retained source, and inventing one is out of scope.
    """

    env = os.environ if environ is None else environ
    family = platform_family(system)
    known = _ACCELERATOR_PLATFORMS.get(name)

    if known is None:
        return AcceleratorSelection(
            name=name,
            platform_family=family,
            supported=False,
            package=None,
            source=PLATFORM_DEFAULT,
            reason=f"Unknown accelerator {name!r}.",
        )

    override_key = _ACCELERATOR_ENV.get(name)
    override = env.get(override_key) if override_key else None

    if family not in known:
        # An override does not make an unavailable platform supported, but it
        # is reported so the owner can see their value was seen and declined.
        return AcceleratorSelection(
            name=name,
            platform_family=family,
            supported=False,
            package=None,
            source=(
                ENVIRONMENT_OVERRIDE if override else PLATFORM_DEFAULT
            ),
            reason=(
                f"{name} is not available on {family}: every retained package "
                "is a CUDA, Windows, or linux_x86_64 build."
            ),
        )

    if override:
        return AcceleratorSelection(
            name=name,
            platform_family=family,
            supported=True,
            package=override,
            source=ENVIRONMENT_OVERRIDE,
            reason=f"{override_key} supplied by the owner; used verbatim.",
        )

    return AcceleratorSelection(
        name=name,
        platform_family=family,
        supported=True,
        package=package,
        source=PLATFORM_DEFAULT,
        reason=f"Retained {family} default for {name}.",
    )


def cuda_driver_guidance_applies(selection: TorchSelection) -> bool:
    """Whether an NVIDIA driver message is appropriate for this selection.

    A macOS or CPU selection must never be told to update an NVIDIA driver.
    """

    return selection.is_cuda
