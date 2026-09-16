# Studio Standalone: portable Windows runtime proposal

**Historical pre-build assessment.** The implemented portable profile and current
verification limits are documented in [PORTABLE_RUNTIME.md](PORTABLE_RUNTIME.md).
The proposal below records the decision before assembly.

Assessment date: 2026-09-13. Application baseline:
`c2853bf50b56423cac59ce683c1789298127a699`, branch `release/tester-alpha-prep`.

**Feasible, with a successful initial compatibility probe. Not ready to distribute
as a self-contained release yet.** This document specifies the proposed contents
before assembly. No portable tester archive has been assembled, and the existing
launcher, dependency pins, application behavior and release ZIPs are unchanged.

The recommended first target is Windows x64 with a supported NVIDIA GPU/driver.
A private Python runtime and prepared libraries would let a tester extract the
folder and run Start-Studio.bat without installing Python or resolving packages.
This does not establish support for AMD, Intel GPUs, Windows ARM, Linux or macOS.

## Proposed contents

| Component | What would be included |
|---|---|
| Launch and help | Start-Studio.bat, start_studio.py and a short START-HERE.md explaining hardware, model placement, first launch and support. |
| Studio application | The current seven runtime trees and required files in packaging/runtime.json. Preserve engine code, frontend, plugins, tokenizer/configuration data and launch.py, which engine imports require. |
| Private interpreter | Official CPython 3.13.15, Windows x64, standard GIL build, in app/runtime. Include its standard library, DLLs, license and an explicit relative import-path configuration. |
| Python libraries | A clean installation of the reviewed, fully pinned dependency set. The 94-package baseline appears below; security review may require a revised list before release. Current Torch pair: 2.11.0+cu130 / TorchVision 0.26.0+cu130. |
| Auxiliary model assets | The same five detectors and one Remacri upscaler already recorded by packaging/assets.json: face_yolov8n.pt, face_yolov8s.pt, hand_yolov8n.pt, person_yolov8n-seg.pt, person_yolov8s-seg.pt, remacri_original.pt. Total 132,628,390 bytes; retain their exact hashes. |
| Empty generation-model folders | app/models/Stable-diffusion/, app/models/VAE/ and app/models/text_encoder/. Users supply their own checkpoint, VAE and text encoders. |
| Tester documentation | Current feature status, release notes, model placement and credits, plus precise runtime/driver requirements once validated. |
| Transparency | Component inventory with versions, origins and SHA-256 hashes; Python/dependency notices; SBOM; Studio source revision; final archive hash. Preserve third-party license files. |
| Source companion | Matching Studio source archive, build recipe, dependency lock and provenance information. Complete redistribution/source requirements must be checked for the actual selected artifacts. |

Proposed main layout:

```text
Studio-Standalone/
  Start-Studio.bat
  start_studio.py
  START-HERE.md
  app/
    runtime/
      python.exe
      python313.dll, python3.dll, standard-library ZIP and native libraries
      python313._pth
      Lib/site-packages/   (prepared dependencies and their notices)
    [current Studio runtime trees and required source files]
    models/
      Stable-diffusion/    (empty)
      VAE/                (empty)
      text_encoder/       (empty)
    [six assets in their existing manifest-defined locations]
    docs/                 (tester documentation and runtime inventory)
```

The launcher creates a fresh studio-config.json and Studio-State on first run,
preserving the current configuration behavior. These contain user data afterward.
The distributed archive would contain no personal configuration, outputs, logs,
private models, development venv or caches. Build-only tools, plans, tests and
legacy WebUI launchers remain in the source companion where applicable.

Bundling does not add missing Studio features. ControlNet, Live Painting generation,
Workshop, regional inference and other limitations in
[the tester status](TESTER_FEATURE_STATUS.md) remain. The baseline dependencies
also omit watchdog: Gallery folder auto-sync is unavailable without that optional
library, while manual rescanning remains available. Adding it should be an explicit
scope decision rather than an accidental consequence of copying a development setup.

## What has actually been checked

- Downloaded the official 3.13.15 embeddable x64 ZIP into isolated workspace Evidence.
  Its SHA-256 matches the published value:
  `d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf`.
  Publisher-signature verification was not performed.
- The private executable starts successfully and imports ssl and sqlite3. Its
  prefix/base prefix point to its own directory, and its initial import search
  contains only that directory and its bundled standard-library ZIP.
- A separate compatibility probe explicitly added the existing workspace library
  directory. NumPy, Pillow, Torch, TorchVision, OpenCV, safetensors, FastAPI,
  Transformers and Diffusers all imported successfully with Python 3.13.15.
  **This used existing libraries; it does not prove a fresh portable installation.**
- Recorded dependency resolution contains 94 packages for Windows CPython 3.13.
  Archive sizes are available for all 94. Two arrive as source distributions:
  filterpy 1.4.5 and antlr4-python3-runtime 4.9.3. Build and verify their wheels
  during release preparation so testers do not need build tools.
- Existing extracted-application startup evidence belongs to the previous setup
  and interpreter. No Studio server startup, GPU generation or fresh-machine
  test was performed with this new private runtime.

The official [Python embedding documentation](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)
supports distributing a private interpreter with an application and managing its
third-party libraries as part of that application. It does not include pip as a
normal end-user package manager. The runtime version and download are recorded
on the [Python 3.13.15 release page](https://www.python.org/downloads/release/python-31315/).

## Size and practical trade-offs

| Measurement | Bytes | Meaning |
|---|---:|---|
| Official private Python ZIP | 11,009,825 | Downloaded and hash checked; about 10.5 MiB. |
| Private Python extracted | 21,337,150 | Sum of official ZIP member sizes. |
| All 94 dependency source/wheel archives | 2,202,427,243 | About 2.20 GB / 2.05 GiB; public metadata, not downloaded as a set. |
| Torch CUDA wheel alone | 1,915,220,980 | Most of the download; exact recorded artifact URL. |
| Current Studio tester ZIP | 164,189,261 | Includes the six auxiliary weights; no Python/dependencies. |
| Planning download total | 2,377,626,329 | Sum of the three compressed inputs: about 2.38 GB / 2.21 GiB. Not a measured final ZIP. |

A prepared-library ZIP recompresses extracted files, so the final archive will
not exactly equal those input totals. Security-driven dependency revisions can
also change it. Installed-library RECORD metadata for the baseline package names
in the existing development environment totals 3,701,222,025 bytes. Of those 94
packages, 21 installed versions differ from the resolver baseline; this is only
a rough disk-size indication. Allow roughly 4 GB extracted and provisionally
8 GB free to hold download plus extraction, **before generation models and
outputs**. Measure both figures from the actual candidate before publishing them
as requirements.

For this tester release, one ZIP containing the prepared runtime is the simplest
experience. A small launcher that downloads libraries on first use would keep the
initial download small but retain network failures and package-resolution/setup
complexity. It is not the proposed portable build.

## Security findings and maintenance

A private interpreter is a supported deployment choice; it does not isolate Studio
from the operating system or reduce its permissions. The proposed launcher needs
no Python installation, PATH change or admin elevation. Native Windows runtime
and NVIDIA driver requirements still need clean-machine verification; any missing
system prerequisite must be clearly reported.

The current dependency versions have known-advisory metadata for **ten packages**:

- accelerate 1.14.0: [version metadata](https://pypi.org/pypi/accelerate/1.14.0/json).
- diffusers 0.37.1: [version metadata](https://pypi.org/pypi/diffusers/0.37.1/json).
- diskcache 5.6.3: [version metadata](https://pypi.org/pypi/diskcache/5.6.3/json).
- GitPython 3.1.52: [version metadata](https://pypi.org/pypi/GitPython/3.1.52/json).
- h11 0.14.0: [version metadata](https://pypi.org/pypi/h11/0.14.0/json).
- protobuf 4.25.9: [version metadata](https://pypi.org/pypi/protobuf/4.25.9/json).
- setuptools 69.5.1: [version metadata](https://pypi.org/pypi/setuptools/69.5.1/json).
- starlette 0.50.0: [version metadata](https://pypi.org/pypi/starlette/0.50.0/json).
- torch 2.11.0+cu130: [version metadata](https://pypi.org/pypi/torch/2.11.0/json).
- transformers 4.57.6: [version metadata](https://pypi.org/pypi/transformers/4.57.6/json).

These are package/version matches, not ten proven Studio vulnerabilities. The raw
inventory retains advisory IDs, aliases, links and reported fixes. Duplicate
GHSA/PYSEC entries should be deduplicated during triage. Torch's advisory lookup
uses public version 2.11.0; its exact CUDA artifact remains separately identified.
This check does not cover every bundled DLL, JavaScript component, model asset,
unknown vulnerability or malicious behavior.

One concrete example is h11 0.14.0. Its upstream
[chunked-encoding advisory](https://github.com/python-hyper/h11/security/advisories/GHSA-vqfr-h8mv-ghfj)
describes request smuggling in a particular proxy/parser combination and lists
0.16.0 as a patched version. Studio's local presentation server uses Python's
ThreadingHTTPServer, so the presence of h11 does not establish that its local
server is affected. Client and other transitive uses still need review. Updating
h11 alone may conflict with the older httpx/httpcore constraints.

The upstream [PyTorch issue](https://github.com/pytorch/pytorch/issues/149623)
linked from Torch's advisory concerns a torch.jit.script crash. It is not evidence
that ordinary Studio model loading is remotely exploitable. Determine reachable
code and applicable fixes before deciding to upgrade the engine stack.

Before distributing a fixed runtime:

1. Triage each advisory against Studio's actual use. Apply necessary updates with
   regression checks, or document the evidence for non-applicability. Do not
   silently upgrade inference libraries under a packaging-only change.
2. Build from exact, reviewed sources/wheels, with all transitive versions and
   hashes locked. The existing bootstrap's live pip resolution and unpinned pip
   upgrade are not a reproducible release recipe. Use a controlled build step;
   first launch should perform no package installation.
3. Preserve original licenses and complete a component/DLL redistribution review.
   Remacri's canonical license remains unverified as already recorded in
   [the asset notes](BUNDLED_MODEL_ASSETS.md); owner approval of its attribution
   does not resolve that provenance gap.
4. Publish component/source identity, checksums and release changes. Verify available
   publisher signatures in the build; assess signing the release launcher. A hash
   shows identity/integrity, not a security audit or trustworthiness by itself.
5. Own runtime updates: review advisories before each tester release and rebuild
   when an applicable fix is needed. Provide replacement-folder instructions that
   preserve user state/models. Automatic updating is outside current functionality.

Experienced users would reasonably look for those details and for evidence the
bundle does not modify their Python installations. Bundling is not itself a reason
to distrust the app; an opaque or stale dependency set would be a concern.

## Implementation and acceptance work remaining

Both Start-Studio.bat and start_studio.py currently select app/venv/Scripts/python.exe.
A portable profile needs explicit app/runtime/python.exe launch paths and a
validated relative import path for Studio and its prepared libraries. Keep user
site packages and arbitrary PYTHONPATH content out of that profile. Do not rely
on copying the development venv: Python documents virtual environments as
[not portable](https://docs.python.org/3/library/venv.html).

The build should retain the current source/asset allowlist and add a separate
runtime manifest with archive and per-file hashes. Prepare dependencies in an
isolated build location; include native .pyd/.dll files, package data and notices.
Audit any .pth startup hooks and subprocess runtime paths. Use the techniques
in pip's [repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/)
and [secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/) guidance.

Acceptance requires the assembled candidate, copied to a different short path
including spaces, running under a standard Windows account with no installed
Python, offline and with a usable GPU/driver. Check native runtime prerequisites;
model-folder setup; populated engine registries; a real generation; Img2Img/Inpaint,
Hires and Auto Detail; Gallery/Canvas; cancellation; output/state persistence;
restart and repair instructions. Compare functional results to the baseline and
update tester feature status with precise evidence. Verify no developer-library
path or undeclared first-run download is needed. A clean non-GPU machine can prove
setup behavior but cannot substitute for real GPU generation testing.

**Recommended next step:** assess the ten advisory-flagged dependency versions and
settle the exact runtime lock, then build one isolated Windows/NVIDIA prototype
using this inventory. Re-present any material inventory changes before assembly.
The packaging concept has passed its initial feasibility check; release readiness
has not.

## Dependency baseline for review

This is the recorded resolver selection, not an approved security-cleared runtime
lock. “None returned” means PyPI returned no advisory for that version at the
assessment time. All 94 archive URLs/hashes, metadata links and size sources are
preserved in the workspace evidence envelope
`Evidence/portable-runtime-feasibility-2026-09-13/dependency-inventory.json`.

| Package | Version | Build input | Advisory metadata |
|---|---|---|---|
| accelerate | 1.14.0 | Wheel | Review |
| annotated-doc | 0.0.5 | Wheel | None returned |
| annotated-types | 0.8.0 | Wheel | None returned |
| antlr4-python3-runtime | 4.9.3 | Build wheel first | None returned |
| anyio | 4.15.1 | Wheel | None returned |
| audioop-lts | 0.2.2 | Wheel | None returned |
| av | 17.1.0 | Wheel | None returned |
| certifi | 2026.7.22 | Wheel | None returned |
| charset-normalizer | 3.5.1 | Wheel | None returned |
| colorama | 0.4.6 | Wheel | None returned |
| comfy-kitchen | 0.2.22 | Wheel | None returned |
| contourpy | 1.4.0 | Wheel | None returned |
| cycler | 0.12.1 | Wheel | None returned |
| diffusers | 0.37.1 | Wheel | Review |
| diskcache | 5.6.3 | Wheel | Review |
| einops | 0.8.2 | Wheel | None returned |
| facexlib | 0.3.0 | Wheel | None returned |
| fastapi | 0.127.1 | Wheel | None returned |
| filelock | 3.32.6 | Wheel | None returned |
| filterpy | 1.4.5 | Build wheel first | None returned |
| fonttools | 4.65.0 | Wheel | None returned |
| fsspec | 2026.7.0 | Wheel | None returned |
| gitdb | 4.0.12 | Wheel | None returned |
| GitPython | 3.1.52 | Wheel | Review |
| h11 | 0.14.0 | Wheel | Review |
| httpcore | 0.17.3 | Wheel | None returned |
| httpx | 0.24.1 | Wheel | None returned |
| huggingface_hub | 0.36.2 | Wheel | None returned |
| idna | 3.19 | Wheel | None returned |
| ImageIO | 2.37.4 | Wheel | None returned |
| importlib_metadata | 9.0.1 | Wheel | None returned |
| inflection | 0.5.1 | Wheel | None returned |
| Jinja2 | 3.1.6 | Wheel | None returned |
| joblib | 1.5.3 | Wheel | None returned |
| kiwisolver | 1.5.1 | Wheel | None returned |
| kornia | 0.6.12 | Wheel | None returned |
| lark | 1.2.2 | Wheel | None returned |
| lazy-loader | 0.5 | Wheel | None returned |
| llvmlite | 0.49.0 | Wheel | None returned |
| markdown-it-py | 4.2.0 | Wheel | None returned |
| MarkupSafe | 3.0.3 | Wheel | None returned |
| matplotlib | 3.11.2 | Wheel | None returned |
| mdurl | 0.1.2 | Wheel | None returned |
| mpmath | 1.3.0 | Wheel | None returned |
| networkx | 3.6.1 | Wheel | None returned |
| numba | 0.67.0 | Wheel | None returned |
| numpy | 2.3.5 | Wheel | None returned |
| omegaconf | 2.2.3 | Wheel | None returned |
| opencv-python | 4.11.0.86 | Wheel | None returned |
| packaging | 26.3 | Wheel | None returned |
| pandas | 3.0.5 | Wheel | None returned |
| piexif | 1.1.3 | Wheel | None returned |
| pillow | 12.3.0 | Wheel | None returned |
| pillow-jxl-plugin | 1.3.8 | Wheel | None returned |
| pillow_heif | 1.4.0 | Wheel | None returned |
| protobuf | 4.25.9 | Wheel | Review |
| psutil | 6.1.1 | Wheel | None returned |
| py-cpuinfo | 9.0.0 | Wheel | None returned |
| pydantic | 2.10.6 | Wheel | None returned |
| pydantic_core | 2.27.2 | Wheel | None returned |
| Pygments | 2.21.0 | Wheel | None returned |
| pyparsing | 3.3.2 | Wheel | None returned |
| python-dateutil | 2.9.0.post0 | Wheel | None returned |
| PyYAML | 6.0.3 | Wheel | None returned |
| regex | 2026.9.10 | Wheel | None returned |
| requests | 2.34.2 | Wheel | None returned |
| rich | 14.3.4 | Wheel | None returned |
| safetensors | 0.8.0 | Wheel | None returned |
| scikit-image | 0.25.2 | Wheel | None returned |
| scipy | 1.18.1 | Wheel | None returned |
| seaborn | 0.13.2 | Wheel | None returned |
| Send2Trash | 2.1.0 | Wheel | None returned |
| setuptools | 69.5.1 | Wheel | Review |
| six | 1.17.0 | Wheel | None returned |
| smmap | 5.0.3 | Wheel | None returned |
| sniffio | 1.3.1 | Wheel | None returned |
| spandrel | 0.4.2 | Wheel | None returned |
| spandrel_extra_arches | 0.2.0 | Wheel | None returned |
| starlette | 0.50.0 | Wheel | Review |
| sympy | 1.14.0 | Wheel | None returned |
| tifffile | 2026.9.9 | Wheel | None returned |
| tokenizers | 0.22.2 | Wheel | None returned |
| torch | 2.11.0+cu130 | Wheel | Review |
| torchsde | 0.2.6 | Wheel | None returned |
| torchvision | 0.26.0+cu130 | Wheel | None returned |
| tqdm | 4.67.3 | Wheel | None returned |
| trampoline | 0.1.2 | Wheel | None returned |
| transformers | 4.57.6 | Wheel | Review |
| typing_extensions | 4.16.0 | Wheel | None returned |
| tzdata | 2026.4 | Wheel | None returned |
| ultralytics | 8.3.119 | Wheel | None returned |
| ultralytics-thop | 2.1.6 | Wheel | None returned |
| urllib3 | 2.7.0 | Wheel | None returned |
| zipp | 4.1.0 | Wheel | None returned |
