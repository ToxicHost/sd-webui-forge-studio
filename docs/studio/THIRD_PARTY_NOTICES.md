# Forge Studio — third-party notices

Forge Studio is licensed under the GNU Affero General Public License v3 or
later ([LICENSE](../../LICENSE)); [NOTICE.md](../../NOTICE.md) gives its copyright,
lineage and modification statement. The components below come from other
projects and keep their own copyright notices and licences. Inventoried
2026-10-20; anything not yet established is marked **(to confirm)** rather
than guessed.

## How the licences combine

- **GPL-3.0** code (ComfyUI, ControlNet extension, Krita AI Diffusion) combines
  with AGPL-3.0 under section 13 of both licences; the combined work is under
  AGPL-3.0 terms, including its network clause.
- **GPL-2.0-or-later** code (Krita) is taken under GPL-3.0, then as above.
- **LGPL-2.x** code (Krita colour adjustments) may be applied under the GPL
  (LGPL section 3), then as above.
- **Apache-2.0, MIT, BSD, zlib, ISC, PSF, MPL-2.0** components are compatible
  and keep their notices.
- No GPL-2.0-only code is known to be included.

## The engine lineage (AGPL-3.0)

- **Stable Diffusion WebUI** — Copyright (c) 2023 AUTOMATIC1111
- **Stable Diffusion WebUI Forge** — Copyright (c) 2024 lllyasviel
- **Forge Neo** — Copyright (c) 2025 Haoming02
  (`modules/`, `modules_forge/`, `backend/`, `extensions-builtin/`; revision
  in `UPSTREAM_BASE`; Studio's changes in `docs/14_PATCH_INVENTORY.md`)

## Code derived from other projects

| Project | Licence | Where |
|---|---|---|
| **ComfyUI** (Comfy Org) | GPL-3.0 | About 54 engine files whose headers cite `github.com/Comfy-Org/ComfyUI` or `comfyanonymous/ComfyUI` (`backend/` model code, memory management, operations, text engines; `modules_forge/packages/comfy`, `huggingface_guess`, `k_diffusion/sampling.py`, `cuda_malloc.py`); `modules_forge/packages/comfy/LICENSE` |
| **huggingface_guess** (lllyasviel) | GPL-3.0 | `modules_forge/packages/huggingface_guess/` (its own `LICENSE`) |
| **gguf** (Georgi Gerganov, from llama.cpp) | MIT | `modules_forge/packages/gguf/` (its own `LICENSE`) |
| **k-diffusion** (Katherine Crowson) | MIT upstream **(to confirm: no LICENSE file in the vendored copy)**; `sampling.py` via ComfyUI (GPL-3.0); `deis.py` Apache-2.0 per its header | `modules_forge/packages/k_diffusion/` |
| **MOSS-Image** (OpenMOSS) | Apache-2.0 | `backend/nn/moss.py`, `backend/text_processing/moss_engine.py` (from `pipeline_moss_image.py`); tests' `_moss_reference.py` |
| **FLUX** (Black Forest Labs), **Qwen-Image** (Qwen), **Wan 2.1** (Wan team), **Lumina-Image 2.0** (Alpha-VLLM), **Cosmos-Predict2** (NVIDIA, Anima), **ControlNet** (lllyasviel), **ToMe for SD** (dbolya), **TAESD** (madebyollin), **ComfyUI-GGUF** (city96), **ComfyUI-nunchaku**, **ComfyUI-INT8-Fast** (MIT per header), **ComfyUI-ZImage-Triton**, **anzhc-qwen2d-comfyui** | as published by each upstream **(to confirm: most headers cite the repository but state no licence)** | `backend/nn/*`, `backend/misc/tomesd.py`, `modules/sd_vae_taesd.py`, `backend/loader_gguf.py`, `backend/quant_rotation.py`, `backend/operations_triton.py`, `backend/nn/_qwen_lora.py`, `backend/nn/wan_vae_2d.py` |
| **Depth Anything V2** (DepthAnything; packaged by MackinationsAi) | Apache-2.0 (its `LICENSE` beside it) | `third_party/depth_anything_v2/`, verbatim from the wheel Forge installs (`third_party/README.md` has the source and hash). Its weights are not distributed: Neo downloads them on first use, and Depth Anything V2 Large is CC BY-NC 4.0 |
| **Krita** (KDE) | GPL-2.0-or-later; LGPL-2.0-only / LGPL-2.0-or-later (colour adjustments) | Brush falloff, masking curve, colour smudge, Smart Patch and Develop adjustments in `forge_studio/frontend/canvas-core.js`, `v2/coverage.js`, `develop.js`; each file's header lists the Krita sources with their SPDX copyright lines (Cyrille Berger, Lukáš Tvrdý, Geoffry Song, Sven Langkamp, Dmitry Kazakov, Deif Lou, Peter Schatz, Eugene Ingerman, Sahil Nagpal, Boudewijn Rempt) |
| **Krita AI Diffusion** (Acly) | GPL-3.0 upstream **(to confirm)** | the completion-triggered loop in `forge_studio/frontend/app.js` ("adapted from Krita AI Diffusion") |
| **TrackImage** (Moritz) | open source; a separate project by one of Forge Studio's creators, credited in Settings › About › Credits | the Gallery: `forge_studio/frontend/gallery.js`, `gallery.css`, `forge_studio/gallery_metadata.py`, `gallery_similarity.py` |

## Built-in extensions that ship

| Extension | Licence |
|---|---|
| `sd_forge_controlnet`, `sd_forge_ipadapter` | GPL-3.0 (own `LICENSE`). Not used by Studio. IP-Adapter code can download insightface models, which are non-commercial; Studio never calls it. |
| `sd_forge_lora`, `soft-inpainting`, `sd_forge_image_stitch` (used by Studio) and the other Forge Neo extensions | the repository's AGPL-3.0 (no licence file of their own) |
| `forge_legacy_preprocessors` (Forge's ControlNet preprocessors), shipped as Forge Neo ships it | each annotator keeps the licence file in its folder. Among them, some are not permissive: **OpenPose** (CMU: non-commercial research use), **LeReS / pix2pix** (academic use) and **PiDiNet** (research use; commercial use by arrangement). Those terms apply to your use of them. |

## Frontend assets

| Asset | Licence |
|---|---|
| **ag-psd** (Agamnentzar), `forge_studio/frontend/ag-psd.js` | MIT upstream **(to confirm: the bundle carries no ag-psd notice)**. The bundle includes **jpeg-js** (Copyright 2011 notmasteryet, Apache-2.0), **buffer** (feross, MIT), **base64-js** (MIT), **ieee754** (BSD-3-Clause), **pako** (Vitaly Puzrin, Andrei Tuputcyn; MIT and zlib, from zlib by Jean-loup Gailly and Mark Adler) and **Node.js** helpers (Joyent, MIT). |
| **Playfair Display** fonts, `forge_studio/frontend/fonts/` | SIL Open Font License 1.1 — Copyright 2017 The Playfair Display Project Authors; Reserved Font Name "Playfair Display" (`fonts/OFL.txt`) |
| Tag autocomplete lists, `forge_studio/frontend/data/*.csv` (danbooru, e621, derpibooru, an English dictionary, quality tags) | publicly available tag lists from those communities; no licence terms attach to them |
| Studio's own branding, `forge_studio/frontend/brand/` | the Forge Studio authors |

## Model configuration and tokenizer files

`backend/huggingface/` holds small configuration and tokenizer files copied
from the model repositories Forge Neo supports (Stable Diffusion 1.5/XL,
FLUX.1/FLUX.2 Klein, Chroma, Qwen-Image, Z-Image, Wan 2.1, ERNIE-Image,
Krea 2, Anima, Neta Lumina, PiD, Mugen, MOSS-Image) and five compressed
tokenizers (`*.tokenizer.json.xz`). No model weights. Each tokenizer is under
its model family's terms, for example Qwen (Apache-2.0), T5/umT5 (Apache-2.0),
CLIP (MIT), Mistral (Apache-2.0), Gemma 2 (Gemma Terms of Use, for Neta
Lumina) **(to confirm per file)**.

## Downloaded at setup (not in the source tree)

`scripts/setup_assets.py` downloads seven files from this project's release
`studio-assets-v1` and checks each one's size and SHA-256. Details and hashes:
[BUNDLED_MODEL_ASSETS.md](BUNDLED_MODEL_ASSETS.md).

- **Five Auto Detail detectors** (`face_yolov8n/s`, `hand_yolov8n`,
  `person_yolov8n/s-seg`) — Bingsu's ADetailer repository, Apache-2.0 per its
  metadata. They are YOLOv8 fine-tunes; Ultralytics treats YOLOv8 derivatives
  as AGPL-3.0, which is also Studio's licence.
- **MobileSAM** (word masks) — Chaoning Zhang et al., Apache-2.0; builds on
  Meta's Segment Anything (Apache-2.0) and TinyViT (MIT).
- **Remacri** (upscaler) — by FoolhardyVEVO. Its source repository states no
  licence; OpenModelDB lists it as CC BY-NC-SA 4.0 (attribution,
  non-commercial, share-alike), and Studio follows those terms: it is
  credited, shipped unmodified, and Forge Studio is free software that is
  never sold. Anyone redistributing Studio commercially must replace it.

## Python packages

Installed by setup from `requirements.txt` (and redistributed inside the
portable ZIP). Nearly all are permissive (MIT, BSD, Apache-2.0, PSF, ISC).
Those with other terms:

| Package | Licence |
|---|---|
| ultralytics, ultralytics-thop | AGPL-3.0 (used only through `forge_headless/ultralytics_boundary.py`) |
| pillow-jxl-plugin | GPL-3.0-or-later |
| pillow_heif | BSD-3-Clause; its wheels bundle libheif, libde265 and x265 (LGPL/GPL) |
| certifi, tqdm, orjson | MPL-2.0 (in part) |
| torch, torchvision (CUDA builds) | BSD-3-Clause; the CUDA wheels include NVIDIA runtime libraries under NVIDIA's licence terms |
| av, opencv-python | BSD-3-Clause / Apache-2.0; their wheels bundle FFmpeg (LGPL) |

## Corrections

If you hold rights in something listed here and the attribution or licence is
wrong, please open an issue on the project repository.
