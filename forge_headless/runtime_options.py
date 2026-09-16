"""What compute runtime Studio asks the inherited backend for, and why.

Neo reaches these settings through `webui-user.bat` and a command line. Studio
must not: `backend_bootstrap` blanks `sys.argv` before the first Neo import, on
purpose, because Neo's parsers would otherwise consume the launcher's own
arguments. So the settings are Studio-owned values, validated here and
PROJECTED into the inherited modules before those modules read them.

The projection point is exact and it is the whole reason this module exists.
`backend/args.py:122` parses at import; `backend/memory_management.py:37` then
does `from backend.args import args` and reads it AT MODULE SCOPE:

```text
memory_management.py:331   args.fast_fp16         -> allow_fp16_accumulation
memory_management.py:1382  args.cuda_stream       -> NUM_STREAMS
memory_management.py:1456  args.pin_shared_memory -> MAX_PINNED_MEMORY
```

Set them after `backend.args` and before `backend.memory_management`, and the
inherited code configures itself exactly as a command line would have. Set them
afterwards and nothing happens at all, silently -- which is the failure mode
this module is shaped to prevent.

`cuda_malloc` is different and stricter: it is an ENVIRONMENT variable that the
CUDA allocator reads when torch first initialises, so it must be set before
torch is imported. Neo does this in `modules_forge/initialization.py:45-47`,
which Studio does not call.

**No owner ever configures this.** There is no config key and no settings
control: `launch.py` passes nothing, and `resolve()` decides from what the
machine can do. The Extension has no such setting either -- as an extension it
inherits whatever `webui-user.bat` gave Forge, and the owner edits that file.
Studio is standalone, so there is no such file, and section 76 is explicit that
an owner should not need to configure CUDA flags to get fast performance. The
`config` parameter below exists for TESTS and for an engineer pinning a variable
during an A/B. Nothing in the product reads it.

Two rules the owner's books make non-negotiable:

**Nothing is enabled that cannot be verified.** Every setting is gated on a
capability probe -- the package imports, the device is the right kind -- and a
setting whose capability is absent is reported absent rather than requested and
silently dropped. `--sage` without `sageattention` installed does not make
anything faster; it makes the log say something untrue.

**Auto is a decision, not a default.** `PerformanceMode.AUTO` picks what this
machine can actually do. It is not "enable everything": `fast_fp16` changes
numerics, and since no owner-facing control exists to consent to that, it stays
off until MEASUREMENT justifies making it the automatic choice. That is an
engineering decision to be taken with evidence, not a switch to hand over.

Studio-owned: nothing here imports torch at module scope, so importing this
costs nothing and cannot disturb the allocator it exists to configure.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from importlib.util import find_spec
from typing import Any

#: Settings Studio knows how to project. Anything else in a config file is
#: reported as unknown rather than passed through -- an option Studio cannot
#: verify is an option that cannot be part of a measured result.
KNOWN_SETTINGS = (
    "attention",
    "autotune",
    "composite_tiles_on_gpu",
    "cuda_malloc",
    "cuda_stream",
    "pin_shared_memory",
    "fast_fp16",
)

#: Attention implementations an owner may ASK for, fastest first on the
#: hardware where they work. The inherited backend selects by IMPORT SUCCESS
#: (`backend/memory_management.py:209-241`), not by flag, so a request for one
#: that is not installed is a request that cannot be honoured.
ATTENTION_ORDER = ("sage", "xformers", "pytorch")

#: What AUTO picks. NOT the fastest thing that imports.
#:
#: SageAttention QUANTISES Q.K to INT8 -- `backend/attention.py:43-49` selects
#: `sageattn_qk_int8_pv_fp16_triton` and `sageattn_qk_int8_pv_fp8_cuda`. It is
#: an APPROXIMATION, so it belongs with `fast_fp16`: a change to what is
#: computed, not a free speed-up, and not something to consent to on an
#: owner's behalf.
#:
#: It is also not universally available or universally beneficial.
#: `memory_management.sage_enabled()` gates only on NVIDIA plus import
#: success -- no compute-capability check -- so an unsuitable card reaches
#: `attention_sage` and falls back per call with an error logged
#: (`backend/attention.py:266-268`). On small tensors and older cards the
#: quantisation overhead can cost more than the kernel saves.
#:
#: So AUTO stays on stock PyTorch attention, which is exact and works
#: everywhere. Anything else is a measured, owner-made choice.
AUTO_ATTENTION = "pytorch"

#: How a choice of attention is ENFORCED on the inherited backend.
#:
#: The backend picks by import success, in this order
#: (`backend/attention.py:331-360`):
#:
#: ```text
#: sage -> flash -> xformers -> pytorch -> basic
#: ```
#:
#: so there is no positive "use this" flag -- a choice is expressed by
#: disabling everything ahead of it. `use_pytorch_cross_attention` is the one
#: exception and it is a true override: `memory_management.py:276-280` sets
#: `ENABLE_PYTORCH_ATTENTION` and then clears XFORMERS, SAGE and FLASH
#: availability, and it runs AFTER the import probes at :211-232, so it wins.
#:
#: Without this table Studio would REPORT a choice it had not made. That is
#: worse than not choosing: a benchmark would credit the wrong kernel.
ATTENTION_PROJECTION: dict[str, dict[str, bool]] = {
    "pytorch": {"use_pytorch_cross_attention": True},
    # Sage is first in the order, so it needs nothing disabled -- only not
    # disabled itself.
    "sage": {"disable_sage": False},
    # Flash sits BETWEEN sage and xformers, so asking for xformers means
    # disabling both of the things ahead of it.
    "xformers": {"disable_sage": True, "disable_flash": True,
                 "disable_xformers": False},
}

#: The import each attention backend needs.
_ATTENTION_PACKAGE = {
    "sage": "sageattention",
    "xformers": "xformers",
    "pytorch": None,  # always available: it is torch's own
}


class PerformanceMode(str, Enum):
    """How much Studio decides for the owner."""

    AUTO = "auto"
    """The fastest settings this machine is MEASURED to support."""

    COMPATIBILITY = "compatibility"
    """Nothing beyond stock torch. The floor, and the fallback."""

    MANUAL = "manual"
    """Exactly what the config says, minus anything unsupported."""


@dataclass(frozen=True)
class Capability:
    """Whether one setting can be honoured here, and why not when it cannot."""

    name: str
    available: bool
    reason: str = ""
    requires: str = ""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available,
                "reason": self.reason, "requires": self.requires}


@dataclass(frozen=True)
class RuntimeOptions:
    """What Studio asks for. Validated, never raw config."""

    mode: PerformanceMode = PerformanceMode.AUTO
    attention: str = "auto"
    cuda_malloc: bool = False
    cuda_stream: int = 0
    pin_shared_memory: bool = False
    fast_fp16: bool = False
    #: cuDNN algorithm search. NOT bit-identical -- see the resolve() comment.
    autotune: bool = False
    #: Composite upscaler tiles on the device instead of through PIL. NOT
    #: bit-identical: the CPU path quantises each tile to 8 bits BEFORE
    #: blending and composites with `Image.paste`, while this accumulates a
    #: weighted mean in float. Measured difference on real content is at most
    #: 2/255, uniform rather than at seams -- but changed is changed.
    #:
    #: On by default since the working set became preflighted: the path is
    #: chosen per image against real free memory, so asking for it can no
    #: longer put a card into an allocation it cannot afford.
    composite_tiles_on_gpu: bool = True
    #: Settings the config asked for that this machine cannot honour, kept so
    #: the owner is told rather than left to infer it from a benchmark.
    refused: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> dict[str, Any]:
        """The safe projection. No paths, no environment, no argv."""

        return {
            "mode": self.mode.value,
            "attention": self.attention,
            "cuda_malloc": self.cuda_malloc,
            "cuda_stream": self.cuda_stream,
            "pin_shared_memory": self.pin_shared_memory,
            "fast_fp16": self.fast_fp16,
            "autotune": self.autotune,
            "composite_tiles_on_gpu": self.composite_tiles_on_gpu,
            "refused": list(self.refused),
        }


# -- capability -----------------------------------------------------------


def _installed(package: str) -> bool:
    """Whether a package can be imported WITHOUT importing it.

    `find_spec` rather than a try/import: importing sageattention pulls triton
    and can take seconds, and section 53 of the program handoff is explicit
    that capability detection must not add startup latency.
    """

    try:
        return find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def _cuda_present() -> bool:
    """Whether a CUDA device is plausibly here, without initialising torch.

    Deliberately NOT `torch.cuda.is_available()`: that initialises the CUDA
    context, and this module's whole job is to run before that happens so it
    can still set the allocator variable.
    """

    if sys.platform not in ("win32", "linux"):
        return False
    return _installed("torch")


def capabilities() -> tuple[Capability, ...]:
    """What this machine can actually be asked for."""

    cuda = _cuda_present()
    found: list[Capability] = []

    for name in ("sage", "xformers"):
        package = _ATTENTION_PACKAGE[name]
        installed = _installed(str(package))
        found.append(Capability(
            name=f"attention:{name}",
            available=bool(installed and cuda),
            reason=(
                "" if installed and cuda
                else f"{package} is not installed" if not installed
                else "no CUDA device"
            ),
            requires=str(package),
        ))
    found.append(Capability(name="attention:pytorch", available=True,
                            reason="", requires="torch"))

    for name in ("cuda_malloc", "cuda_stream", "pin_shared_memory",
                 "fast_fp16", "autotune"):
        found.append(Capability(
            name=name, available=cuda,
            reason="" if cuda else "no CUDA device", requires="cuda",
        ))
    return tuple(found)


def _capable(found: tuple[Capability, ...], name: str) -> bool:
    return any(c.name == name and c.available for c in found)


# -- resolution -----------------------------------------------------------


def resolve(config: Any = None,
            found: tuple[Capability, ...] | None = None) -> RuntimeOptions:
    """What this machine should run. `config` is for tests, not for owners.

    Called with nothing by the product. See the module docstring: there is no
    owner-facing surface for any of this, by design.

    Never raises. A config that asks for something absent gets it recorded in
    `refused` and left off -- the alternative is a run whose log claims an
    accelerator it did not have.
    """

    found = capabilities() if found is None else found
    document = config if isinstance(config, dict) else {}

    raw_mode = str(document.get("mode", "auto") or "auto").strip().lower()
    try:
        mode = PerformanceMode(raw_mode)
    except ValueError:
        mode = PerformanceMode.AUTO

    if mode is PerformanceMode.COMPATIBILITY:
        return RuntimeOptions(mode=mode, attention="pytorch")

    refused: list[str] = []

    # -- attention ---------------------------------------------------------
    wanted = str(document.get("attention", "auto") or "auto").strip().lower()
    if wanted in ("", "auto"):
        # Not "the fastest that imports". See AUTO_ATTENTION: the alternatives
        # are approximations, and whether they are faster is a property of the
        # card, not of the package being present.
        attention = AUTO_ATTENTION
    elif wanted not in ATTENTION_ORDER:
        attention, _ = "pytorch", refused.append(f"attention:{wanted}")
    elif not _capable(found, f"attention:{wanted}"):
        attention, _ = "pytorch", refused.append(f"attention:{wanted}")
    else:
        attention = wanted

    def flag(name: str, auto_default: bool) -> bool:
        """`refused` means THE OWNER ASKED and this machine could not.

        An AUTO default that turns out to be unsupported is not a refusal --
        nobody asked for it -- and recording one would make the launcher warn
        about a request the owner never made.
        """

        explicit = document.get(name)
        asked = explicit
        if asked is None:
            asked = auto_default if mode is PerformanceMode.AUTO else False
        if not asked:
            return False
        if not _capable(found, name):
            if explicit:
                refused.append(name)
            return False
        return True

    # `fast_fp16` is NOT on under AUTO, and it is now MEASURED rather than
    # assumed -- `Evidence/anima-extension-parity-2026-08-16/`.
    #
    # It does two things, and the second is not in its name. `memory_management`
    # sets `allow_fp16_accumulation` from it (:331) AND `PRIORITIZE_FP16` (:334),
    # and the second makes `unet_dtype` take the fp16 branch instead of walking
    # the architecture's declared order. Anima declares
    # [bfloat16, float16, float32], so the flag is what moves it to fp16; SDXL
    # inherits [float16, ...] from BASE and was fp16 already.
    #
    # Which of the two pays is settled: at equal accumulation, fp16 and bf16
    # matmul at the SAME rate on this card -- 0.986x, i.e. nothing. The whole
    # gain is the accumulator, 1.78x on the matmuls and 1.17-1.28x end to end.
    # The dtype switch is not a speedup; it is what gives the accumulator
    # something to act on. SDXL proves it independently: identical dtype in
    # both arms, still 1.20-1.28x faster.
    #
    # So there is no version of this that is free. It cannot be split into a
    # safe half, and the cost is real -- at a fixed seed only 25.5% of pixels
    # came back identical, mean difference 3.7/255. Section 48 forbids winning
    # a benchmark by altering what is computed, and this alters it. It stays
    # off until an OWNER consents, having seen the two images.
    # `autotune` is OFF under AUTO, for the same reason `fast_fp16` is.
    # `torch.backends.cudnn.benchmark` lets cuDNN pick a different convolution
    # algorithm per shape. It is the SAME mathematics -- no quantisation, no
    # precision trade -- but a different reduction order, so results can differ
    # in the last ULP. Nothing in this tree pins determinism on the generation
    # path already, so non-determinism is permitted; benchmark changes WHICH
    # kernel runs, not whether nondeterminism is allowed. That is still not
    # bit-identical, so it waits for an owner who has A/B'd it.
    #
    # It also costs time on the FIRST occurrence of every new shape, so a cold
    # generation is slower and only repeated work pays it back.
    settings = RuntimeOptions(
        mode=mode,
        attention=attention,
        cuda_malloc=flag("cuda_malloc", True),
        pin_shared_memory=flag("pin_shared_memory", True),
        fast_fp16=flag("fast_fp16", False),
        autotune=flag("autotune", False),
        # Not gated on a CUDA capability: the tile loop runs wherever torch
        # runs, so this is portable in a way the others are not. On by
        # default, because `modules/esrgan_model.py` now preflights the frame
        # against free memory and falls back on its own -- the preference says
        # "prefer this", not "force it".
        composite_tiles_on_gpu=bool(document.get("composite_tiles_on_gpu", True)),
    )

    streams_raw = document.get("cuda_stream")
    if streams_raw is None:
        streams = 2 if mode is PerformanceMode.AUTO else 0
    else:
        try:
            streams = max(0, min(8, int(streams_raw)))
        except (TypeError, ValueError):
            streams, _ = 0, refused.append("cuda_stream")
    if streams and not _capable(found, "cuda_stream"):
        streams = 0
        if streams_raw is not None:
            refused.append("cuda_stream")

    for key in document:
        if key not in KNOWN_SETTINGS and key != "mode":
            refused.append(f"unknown:{key}")

    return replace(settings, cuda_stream=streams, refused=tuple(refused))


# -- projection -----------------------------------------------------------


def apply_environment(settings: RuntimeOptions) -> dict[str, str]:
    """Set what must be set BEFORE torch is imported. Returns what changed.

    Only `cuda_malloc`, and only this way: the CUDA allocator reads
    `PYTORCH_CUDA_ALLOC_CONF` when torch first initialises, so a value set
    after that is inert. Neo does this in
    `modules_forge/initialization.py:45-47`, which Studio does not call.
    """

    changed: dict[str, str] = {}
    if not settings.cuda_malloc:
        return changed
    if "torch" in sys.modules:
        # Too late to matter, and saying so beats setting a variable that
        # will be read by nobody.
        return changed
    existing = os.environ.get("PYTORCH_CUDA_ALLOC_CONF")
    value = ("backend:cudaMallocAsync" if not existing
             else f"{existing},backend:cudaMallocAsync")
    if existing and "cudaMallocAsync" in existing:
        return changed
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = value
    changed["PYTORCH_CUDA_ALLOC_CONF"] = value
    return changed


def project(settings: RuntimeOptions, args: Any) -> dict[str, Any]:
    """Write the settings onto the inherited `backend.args` namespace.

    MUST run after `backend.args` is imported and BEFORE
    `backend.memory_management` is, because that module reads these at module
    scope. Returns what was written, so a caller can report the truth rather
    than the intent.

    Only attributes the namespace ALREADY declares are written. A typo would
    otherwise create a new attribute that nothing reads, and the run would look
    configured while behaving exactly as before.
    """

    written: dict[str, Any] = {}
    projected: list[tuple[str, Any]] = [
        ("cuda_malloc", settings.cuda_malloc),
        ("pin_shared_memory", settings.pin_shared_memory),
        ("fast_fp16", settings.fast_fp16),
        ("cuda_stream", settings.cuda_stream or None),
        ("autotune", settings.autotune),
    ]
    # The attention choice, ENFORCED rather than merely reported. See
    # ATTENTION_PROJECTION: the backend has no positive selector, so a choice
    # is expressed by disabling what sits ahead of it in the order.
    projected.extend(ATTENTION_PROJECTION.get(settings.attention, {}).items())
    for name, value in projected:
        if not hasattr(args, name):
            continue
        try:
            setattr(args, name, value)
        except Exception:  # noqa: BLE001 - a namespace that refuses is not fatal
            continue
        written[name] = value
    return written


def effective(settings: RuntimeOptions) -> dict[str, Any]:
    """What the backend ACTUALLY has on, read back after it imported.

    Section 18: an owner must not have to infer activation from timing. A
    request is not an outcome -- `memory_management.py:338` gates autotune on
    `torch.cuda.is_available() and torch.backends.cudnn.is_available()`, so a
    machine without cuDNN asks and gets nothing, correctly and silently.

    Read from torch itself rather than from what was written, because the
    whole failure mode this guards is a value set too late to be read.
    """

    report: dict[str, Any] = {"autotune_requested": bool(settings.autotune)}
    try:
        import torch

        report["autotune_effective"] = bool(
            torch.backends.cudnn.benchmark)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no torch, no answer, say so
        report["autotune_effective"] = False
    return report


__all__ = (
    "ATTENTION_ORDER",
    "ATTENTION_PROJECTION",
    "AUTO_ATTENTION",
    "KNOWN_SETTINGS",
    "Capability",
    "PerformanceMode",
    "RuntimeOptions",
    "apply_environment",
    "capabilities",
    "effective",
    "project",
    "resolve",
)
