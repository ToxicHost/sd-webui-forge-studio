# Studio Source Snapshot

## Identity

- Source archive name: not available in the supplied reference area
- Extracted reference directory: `Reference/Forge-Studio-main/Forge-Studio-main`
  (outside this Git repository)
- Source file count: 101, including the snapshot `.gitignore`
- Studio version: `v4.10.0 — public beta`
- Studio version metadata:
  `b316a4d87abd69837cd723403a7a41e22f97482d`
- Git metadata in snapshot: none
- Primary license: GNU Affero General Public License v3.0

The Studio implementation was inspected in place and was not copied into the
distribution repository during Phase 0.

## Major frontend areas

- `frontend/`: canvas, layers, workflows, Gallery, Workshop, Lexicon, settings,
  in-app documentation, localization, themes, and brand assets
- `frontend/locales/`: English, German, Spanish, and French strings
- `frontend/fonts/`: Playfair Display font files and SIL Open Font License 1.1
- `frontend/data/`: autocomplete dictionaries and tag datasets
- `javascript/ag-psd.js`: browser-side PSD support
- `css/` and root `style.css`: extension/host styling
- `blueprints/`: shape-CFG blueprint files

## Major backend modules

- `scripts/studio.py`: Gradio tab and Forge `on_app_started` registration
- `scripts/studio_api.py`: main FastAPI routes, file/static serving, model
  listings, request handling, progress, export, and module registration
- `scripts/studio_generation.py`: generation orchestration, Hires paths,
  checkpoint transitions, ADetailer coordination, previews, and timing
- `scripts/studio_bridge_init.py` and `scripts/studio_gradio_stub.py`:
  standalone `--nowebui` ScriptRunner initialization
- `scripts/studio_gallery.py`: Gallery database, scanning, thumbnails,
  metadata, duplicate detection, and event stream
- `scripts/studio_workshop.py`: checkpoint merge, LoRA/VAE baking, recipes, and
  Workshop routes
- `scripts/studio_lexicon.py`: wildcard file management and routes
- `scripts/studio_live.py`: live-paint request coordination
- `scripts/studio_adetailer.py`, `scripts/studio_controlnet.py`,
  `scripts/studio_regional.py`, and `scripts/studio_attention_couple.py`:
  extension and generation integrations
- sampler/scheduler modules: `adams_bashforth.py`, `grimoire.py`,
  `parasite.py`, `samplers_bfs.py`, and `schedulers_beta.py`

## Launch and install files

- `Forge Studio.bat`: Windows standalone launcher using `--nowebui`, port
  7860, xformers, SageAttention, CUDA streams/malloc, FP16 fast path, UV, and
  pinned shared memory
- `Forge_Studio_LowVRAM.bat`: Windows low-VRAM variant with SageAttention
  disabled and a `PYTORCH_CUDA_ALLOC_CONF` setting
- `install.py`: installs `imageio-ffmpeg` and `imagehash`
- No Linux or macOS Studio launcher is present in the snapshot.

These are source observations only. The actual launch arguments used on a
baseline test machine have not been captured.

## Known integration points

- Forge callback registration through `modules.scripts.script_callbacks`
- FastAPI route attachment and frontend serving at `/studio`
- root redirect to `/studio` in standalone mode
- Forge processing through `process_images()`
- model reload paths through `modules.sd_models`
- VAE list refresh through `modules.sd_vae.refresh_vae_list()`
- ScriptRunner argument bridging for extensions in `--nowebui` mode
- optional ADetailer, ControlNet, Dynamic Prompts, and attention-coupling
  extension integration
- opt-in Civitai hash lookup and local metadata cache

## License notes

- Studio and the Neo baseline include AGPL-3.0 license text.
- Playfair Display carries SIL Open Font License 1.1 text.
- `ag-psd.js` contains bundled Apache-2.0, MIT, BSD-3-Clause, and other notice
  blocks that need a formal attribution review before distribution.
- Gallery source credits TrackImage v6.8 by Moritz as integrated with
  permission; no separate TrackImage license record is present.
- In-app documentation notes that some canvas algorithms were ported from
  Krita GPL-3.0 source.

## Missing information

- original source archive filename and checksum;
- repository URL and commit date corresponding to `version.json`;
- signed release tag or other independent snapshot verification;
- complete dependency versions and third-party attribution inventory;
- exact ADetailer Studio-fork URL and license/version evidence;
- baseline runtime environment, logs, fixtures, and output metadata.
