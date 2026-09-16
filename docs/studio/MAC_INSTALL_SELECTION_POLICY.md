# Mac Install Selection Policy — B1

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

Selecting an install command is not a compatibility claim. This boundary
decides *what would be installed*. It does not assert that the result runs,
that a wheel exists for the platform, or that an acceleration device is
present. **No macOS install has been performed or verified.**

---

## 1. What changed

Before, `prepare_environment()` in `modules/launch_utils.py` unconditionally
defaulted to `torch==2.11.0+cu130` from a CUDA 13.0 index — a build with no
macOS wheel — and could tell a machine with no NVIDIA hardware to update its
NVIDIA driver.

Now a pure helper, `modules/platform_selection.py`, answers:

```text
platform + machine + environment overrides + optional flags
    -> Torch command, index policy, and which accelerators exist here
```

`prepare_environment()` consumes the result. Selection logic moved; the
Windows and Linux values did not.

---

## 2. The selection matrix

| Platform | Machine | Acceleration family | Command | Index |
|---|---|---|---|---|
| Windows | any | `cuda` | `pip install torch==2.11.0+cu130 torchvision==0.26.0+cu130 --extra-index-url <cuda index>` | CUDA 13.0 |
| Linux | any | `cuda` | same as Windows | CUDA 13.0 |
| macOS | `arm64` / `aarch64` | `mps` | `pip install torch torchvision` | none |
| macOS | anything else | `cpu` | `pip install torch torchvision` | none |
| unrecognised | any | `unknown` | **none selected** | none |

Windows and Linux are byte-identical to the retained defaults. The retained
source never differentiated CUDA from non-CUDA Linux, so neither does this —
the Linux matrix is preserved, not expanded.

### Why the macOS command is unpinned

The repository pins `2.11.0+cu130`, which has no macOS wheel. No macOS wheel
availability has been verified from this workspace — that would require
network access, which is prohibited. Pinning an unverified version would
invent a fact, so the default is unpinned and an owner who wants a pin sets
`TORCH_COMMAND`.

### Why Apple Silicon reports `mps`

`acceleration_family` is an **install** family: which build to fetch. An ARM64
Mac reports `mps` whether or not Torch can later reach an MPS device. Runtime
capability is reported separately, by the backend, through `ModelCapability`.
The two must not be conflated — see
[`STUDIO_PLATFORM_CAPABILITY_CONTRACT.md`](STUDIO_PLATFORM_CAPABILITY_CONTRACT.md).

### Intel macOS

Classified `cpu`, never `mps`. Intel Macs have no Metal Performance Shaders
backend. The command is the same unpinned default; whether a wheel exists for
that architecture is unverified and deliberately not asserted either way.

### Unrecognised platform

Returns `supported=False` with `command=None`, and `prepare_environment()`
raises a clear `SystemError`. **CUDA is never chosen as a fallback** — that was
the old failure mode, and it is now impossible by construction.

---

## 3. Environment overrides

`TORCH_COMMAND` is trusted owner input. When set it is used **verbatim**: never
rewritten, never re-indexed, never validated against the platform. The
selection records `source: environment_override` so an override is always
distinguishable from a platform default.

`TORCH_INDEX_URL` is honoured for CUDA families and reported for macOS, but a
macOS default never synthesises an `--extra-index-url` argument from it.

---

## 4. NVIDIA driver guidance

The `"Please update your GPU driver to support cu130"` message is now gated on
`cuda_driver_guidance_applies(selection)`, which is true only when the selected
acceleration family is `cuda`.

A macOS, CPU, or unknown selection that fails the Torch GPU test gets the
generic `"PyTorch is not able to access GPU"` instead. Real CUDA checks on
Windows and Linux are untouched.

---

## 5. Optional accelerators

Every retained optional accelerator is a CUDA build, a Windows wheel, or a
`linux_x86_64` wheel:

| Accelerator | Windows | Linux | macOS |
|---|---|---|---|
| xformers | CUDA index build | CUDA index build | **unsupported** |
| SageAttention | `win_amd64` wheel | PyPI (CUDA) | **unsupported** |
| FlashAttention | `win_amd64` wheel | `linux_x86_64` wheel | **unsupported** |
| Triton | `triton-windows` | `triton` | **unsupported** |
| nunchaku | `win_amd64` wheel | `linux_x86_64` wheel | **unsupported** |
| CUDA allocator | supported | supported | **unsupported** |

Before this change, macOS fell into the `else` branch and would have been
offered the **Linux** packages — a `linux_x86_64` flash wheel on an ARM Mac.
Now macOS sets each package to `None`, and `_require_accelerator` raises a
clear `SystemError` naming the reason if a flag requests one.

An override does not make an unsupported platform supported. The override is
recorded as seen and declined, so the owner can tell their value was read
rather than ignored.

**No alternative macOS acceleration package is substituted.** None is offered
by the retained source, and inventing one is out of scope.

---

## 5.5 xformers is selected with the others

xformers was previously assigned before the platform branch, building a CUDA
index string unconditionally, and was kept from use on macOS by a conditional
112 lines away at the install site. The value was dead there, but its safety
depended on a distant guard that no test could exercise -- running
`prepare_environment()` invokes pip.

It now lives in the platform branch with sage, flash, triton, and nunchaku:
`None` on macOS, the unchanged `XFORMERS_PACKAGE` default with the CUDA index
on Windows and Linux. No CUDA index string is constructed for macOS at all, so
the value is **absent rather than present-but-guarded**, and `is_cuda` no
longer appears anywhere in `launch_utils.py`.

Verified by 11 tests that parse `prepare_environment` instead of running it:
the macOS branch assigns literal `None` for all five accelerators and contains
no f-string, index URL, or `os.environ` read; both Windows and Linux keep their
default; every accelerator install site is guarded by `_require_accelerator`;
and no test in the suite calls `prepare_environment` or `run_pip`.

`bitsandbytes` is deliberately unchanged and remains outside this boundary.

---

## 6. Purity guarantees

`modules/platform_selection.py` imports only `__future__`, `os`,
`dataclasses`, and `typing`. It does not import Torch, invoke pip, spawn a
subprocess, touch the network, read the filesystem, or mutate `os.environ`.

These are asserted by AST analysis of the module source rather than by
observing one call, so the guarantee holds for every code path — including
ones no test exercises. See
`tests/studio_alpha/test_platform_selection.py::HelperPurityTests`.

---

## 7. Test coverage

26 tests in `tests/studio_alpha/test_platform_selection.py`, covering the
15 required cases: Windows default and override, Apple Silicon default and
override, Intel macOS, Linux, unknown platform, no `+cu` suffix on macOS, no
CUDA index on macOS, no NVIDIA guidance on macOS, unchanged Windows CUDA
behaviour, incompatible accelerators, and the three purity properties.

Every case injects platform, machine, and environment, so the whole matrix
runs on one Windows machine.

---

## 8. What is still unverified

- **No macOS install was attempted.** Whether `pip install torch torchvision`
  succeeds on Apple Silicon with Python 3.13.5 is unknown from here.
- **No macOS wheel availability was checked** for any package, including the
  Intel macOS case.
- **No MPS device was initialised.** Reporting the `mps` install family says
  nothing about runtime availability.
- **`prepare_environment()` itself was not executed.** Only the pure helper is
  under test; the integration is by inspection.
