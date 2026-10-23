# Studio Standalone — feature status for testers

Features reviewed 2026-09-13; Git installation verified 2026-09-15; Canvas painting updated 2026-09-30; Workshop, saving and in-app updates added 2026-10-05; Develop fixes, inpaint-aware Auto Detail and a GPU choice added 2026-10-10; ControlNet, the memory warning and crash recovery added 2026-10-20. Regional prompting and Live Painting added 2026-10-21. Core generation rows updated 2026-10-22. This describes the current Standalone source and the
verification performed for this candidate, not the inherited Neo README.

**Available** means there is an implemented Standalone path. **Contract-tested**
means existing automated tests passed with fixtures; it does not mean that a
real GPU or every browser workflow was exercised. Rows that say "used on real
GPUs" were exercised by the owner and testers during the tester alpha, not by
a separate certification run.

## Available, with the stated limits

| Area | Current status | Evidence / limits |
|---|---|---|
| Studio interface and local server | Available | Installed from fresh Git clones by testers through the tester alpha. A fresh Git installation with cached dependencies, source and private-runtime portable extractions passed startup and registry checks. Portable startup also passed with an empty PATH; Windows/NVIDIA is the initial target. |
| Model folders and selection | Available (one list, 0.9.3) | Add any number of folders in Settings > Model folders (or on the welcome screen); every subfolder is scanned and each file sorted by what it is, with an Unrecognized list to correct. Earlier per-type folders are converted on first start. Then choose checkpoint/components; Generate owns loading. |
| Basic image generation | Available | Used on real GPUs by the owner and testers through the tester alpha. Request/lifecycle tests passed. Memory needs depend on the model; the console warns when memory runs short. |
| Img2Img and Inpaint | Available | Used on real GPUs by the owner and testers through the tester alpha. Source image, mask and translation contracts tested. |
| Hires | Available | Used on real GPUs by the owner and testers through the tester alpha. Hires and Img2Img/Hires contracts passed. Requires a suitable upscaler/model and enough memory. |
| Auto Detail | Available | Used on real GPUs by the owner and testers through the tester alpha. Detector/slot contracts passed. Five pinned detectors included and ultralytics declared. A detector finding no target can leave the image unchanged. While inpainting, faces the mask is not on are skipped (2026-10-10; confirmed on a real inpaint). |
| Queue and cancellation | Available | Queue/lifecycle contracts passed. Cancellation is bounded by safe execution points, not necessarily immediate. |
| Batch mode | Available for testing (0.9.4) | Settings > Generation > Generate mode: Queue (default) or Batch. Batch size is Neo's batch (one sampling pass), Batch count the passes; tested through the real server path, and confirmed by the owner on a real GPU. Regions, ControlNet, Klein references, Word Detail, image Hires and outpaint refine make one picture per pass. A large Batch size can run out of video memory (Studio names it). |
| Canvas brushes, layers and masks | Available for testing | Brush Engine V2 is the default painting engine (2026-09-30): automated brush checks passed and every preset was reviewed with a mouse. Aliased/Pixel Perfect brushes, touch input and mask/region painting use the previous engine. No tablet certification. |
| Canvas recovery | Implemented | Recovery-store tests passed. Since 2026-10-15 a capture saves only the layers, mask and regions that changed, and a restored document is byte-identical in tests; closing the last tab or closing a tab mid-save is safe. Undo history is not restored. Keep separate artwork backups. |
| Gallery | Implemented with limitations | Service contract tests passed; fresh browser behavior still needs tester feedback. Image similarity/metadata are not universal format guarantees. |
| Preferences | Available with persistence limits | Server persistence tests passed. The theme and custom themes are saved on the server (0.9.4). An ephemeral port changes browser origin, resetting browser-local tool/layout state; use a stable port when needed. |
| Themes and custom themes | Available for testing (0.9.4) | Seven themes (Liam default, Cream Paper light) with contrast checked by tests; custom themes derived from a few colours, with a readability check, export and import. Checked in the browser on the mock server; Gallery and Workshop in the light theme still need tester eyes. Studio's own colour picker in the editor; New theme always adds a theme (0.9.6). |
| Refreshed screens | Available for testing (0.9.5) | Workspace, Settings, Gallery, Codex, the tour, Workshop, dialogs, Wildcards and Develop restyled to match the Generate panel; no control removed. Each checked in the browser on the mock server or a harness with made-up data, and by automated tests. Needs tester eyes with real folders, models and pictures, and in the Cream Paper light theme. |
| Wildcards / Lexicon | Available | Editor and preference tests passed. Configured local folders are required for user content. Refresh lists newly added wildcards, and they expand without a restart (2026-10-15). Preview picks an entry and shows what it resolves to (0.9.5). |
| LoRAs / embeddings | Available | Used on real GPUs by the owner and testers through the tester alpha. The LoRA and Checkpoint browsers (previews, activation text, Civitai lookup) were added 2026-10-05 with route tests. Refresh lists newly added LoRAs (2026-10-15). |
| Develop | Available for testing | Saved presets work (2026-10-05). Shadows, Blacks, Dehaze, Vignette, Blue Saturation and negative number entry fixed and measured with automated checks (2026-10-10). Sliders render in a background worker (2026-10-21): no pause on the page's thread per drag frame (measured in the browser), saved pictures byte-identical to before; Dehaze and Noise Reduction preview at half size while dragging. A "Develop on" chip in the canvas toolbar shows when Develop is changing the picture (2026-10-21). Float (High Precision) sources are not available. |
| Workshop | Available for testing (2026-10-05) | Weighted Sum, Add Difference and SLERP merges, block weights, LoRA and VAE baking, multi-step chains, History, and a basic Inspector. The arithmetic was tested against the reference formulas on test models, and a real merge on real checkpoints. LoRA baking is proven on test LoRAs only. Results go to the first checkpoint folder; files are never overwritten. |
| Saving and export | Available (2026-10-05) | Save, Save to Gallery, Canvas Export (with watermark stamping) and Export EXR (Standard) write files; route tests passed. |
| Anima prompt weights | Available for testing (2026-10-12) | Studio weights are the default on Anima; reused weighted images made before this update or elsewhere switch to Neo-compatible (the previous behaviour). Checked on a real GPU for text-to-image: prompts without weights unchanged, Neo-compatible byte-identical to the previous version, weighted prompts matching the research images. Weights in Hires, Img2Img/Inpaint and Auto Detail passes are covered by tests, not yet by a GPU check. |
| GPU choice | Available on machines with two or more GPUs (2026-10-10) | Settings > Performance & VRAM > GPU; applied at the next start. Tested in code only; no two-card hardware check yet. |
| Check for Updates | Available for Git installations (2026-10-05) | Fast-forward update of the tracked branch, then a restart. It refuses copies with edited or own-committed files. Tested against real Git repositories, including a shallow clone. |
| Support report | Available | Script is supplied; report is created locally for inspection before sharing. |
| ControlNet | Available for testing (2026-10-20) | Three units for SD 1.5, SDXL and Anima (Control-LLLite), with Forge Neo's own ControlNet code and preprocessors; Klein through "Use as Klein reference". Checked on a real GPU: SDXL Union canny, Anima line art and depth follow their control picture (measured against the same seed without ControlNet), Anima pose runs but is weak, Hires at 1536, map previews, first-use downloads. Preprocessors needing mediapipe or insightface are not installed; Depth Anything V1 is withheld. Covered by tests. |
| Regional prompting | Available for testing (2026-10-21) | Regions from the Regions panel are sent with Generate, any number. Automatic uses Attention Couple on SD 1.5 and SDXL and masked prompts on other models; One pass per region repaints each region of a canvas picture separately. Hires keeps the regions; Auto Detail gives a face its region's prompt. Checked on a real GPU through the page's request on SDXL, Anima and FLUX.2 Klein: regions land on their side and swap, four identical regions stay close to the main prompt's picture, Hires at 1536 keeps them, one pass per region leaves the rest byte-identical. Attention Couple was not checked on SD 1.5 (no SD 1.5 model in the check). Covered by tests. |
| Live Painting | Available for testing (2026-10-21) | ▶ Live repaints the canvas as img2img after each stroke, into the preview pane; Apply to Layer, Strength, seed re-roll; Generate turns Live off. Frames are never saved or added to the Gallery. Checked on a real GPU through the page's routes and socket at 1024: about 3 s a frame on SDXL, 6 s on Anima, 2 s on FLUX.2 Klein after the first; the newest change wins a burst; nothing written to disk; a Generate after Live is byte-identical to one before. Covered by tests. |
| Hires on another model | Available for testing (0.9.9) | Hires Fix > Model runs the Hires pass and Auto Detail on a second model with its own Text Encoder and VAE, any family. One model on the GPU at a time; a switch adds about 4-5 s (Anima) or 7 s (SDXL) on a 16 GB card. Text-to-image only. Checked on a real GPU: Anima to Anima, Anima to SDXL, SDXL to Anima and a batch of 2. |
| Refiner | Available for testing (0.9.10) | A second checkpoint of the same family takes over the sampling at a chosen step; works with Hires and Auto Detail. Its LoRAs are the main prompt's, none, or its own list (0.9.10); on Anima it reads the prompt with its own text adapter. Another family is refused before generating. Text-to-image, .safetensors only. Checked on a real GPU (Anima): the switch and restore take 2-3 s each; its own LoRA reaches its steps; pictures made afterwards, with or without LoRAs, are pixel-identical to those made before. |
| Faster upscaler (half precision) | Available, off by default (0.9.9) | Settings > Performance & VRAM. Remacri's upscale took 2.6 s instead of 4.8 s on an RTX 5060 Ti; fine detail in the final picture can differ slightly. |
| Low-memory warning and crash recovery | Available for testing (2026-10-20) | A console warning, once per job, when RAM use reaches 95 % while a picture is being made; after an unexpected stop the launcher restarts Studio and the page says why. Crash recovery checked with the real launcher and server (a forced stop, restart, the notice once); the warning is covered by tests. A real out-of-memory crash was not reproduced. |

## Unavailable, incomplete or outside this candidate

| Area | Status |
|---|---|
| High Precision float output | Not available. Export EXR (Standard) converts the 8-bit image; no float capture is offered. |
| Separate Inpaint Sketch mode | Intentionally superseded by Canvas painting/masks; not an outstanding promised mode. |
| Automatic updates | Not automatic. Git installations update from Settings > About > Check for Updates or with `git pull --ff-only`; see [GIT_INSTALL.md](GIT_INSTALL.md). ZIP updates use a separate candidate folder and backups. |
| Multi-user / public network service | Outside the Windows local-use candidate. |
| Linux/NVIDIA Docker tester | Experimental [setup supplied](../../packaging/docker/README.md); config and native adapter checks only. Image build, GPU passthrough and container generation await the Linux tester. |
| Masks and word masks | Available for testing (2026-10-18) | Named masks in a Masks panel, masks from prompt words (MobileSAM on the CPU) and from tones or colour, inpainting with any mask, Develop local adjustments, PSD layer masks. Store, undo, recovery and word-mask methods covered by tests; checked in the browser; word masks measured against reference masks on research pictures. |
| Outpaint, Canvas Size, Smart Patch, Paint Mixer | Available for testing (2026-10-18) | Outpaint checked on a real GPU (large pictures, Refine new area); the others covered by tests and browser checks. |
| Folder dialogs and network model folders | Available for testing (2026-10-18) | Browse opens the system folder dialog for every folder setting; network model folders are opt-in and bounded by timeouts. Covered by tests. |
| Word heatmaps | Available for testing (2026-10-15) | Anima and SDXL pictures record each prompt word's attention; the Gallery's Words section shows it. Settings > Heatmap data turns recording off, moves or cleans up its folder. Checked on a real GPU for Anima: pictures byte-identical with recording on and off, maps matching the research capture. SDXL checked on a real GPU too: byte-identical with recording on and off (2026-10-16). |
| macOS, Linux, Docker, AMD/Intel GPU certification | Not established by this review. Some platform/source support exists, but is not a tested release promise. |

## Verification scope

- 2026-10-20 update: the full development suite passed. On a real GPU the
  previous update and this one gave byte-identical Anima, Anima Hires, Auto
  Detail, Outpaint and SDXL pictures; ControlNet was checked on a real GPU
  for SDXL and Anima (see the ControlNet row).
- 2026-10-18 update: the full development suite passed apart from the same
  development-guide text checks as before. On a real GPU the previous build
  and this one gave byte-identical plain Anima, SDXL and Outpaint pictures;
  Anima Hires and Auto Detail changed slightly, as intended (Neo parity).
- 2026-10-15 update: the full development suite ran 6,336 tests with the GPU
  after the heatmap work; the 11 failures are the same development-guide text
  checks as before. The later fixes were tested with the GPU hidden: beyond
  those 11, only engine start-up tests that need a GPU failed, and the engine
  did not change. Ten real Anima images were generated on a GPU for heatmaps
  (see the Word heatmaps row).
- 2026-10-12 update: the full development suite ran 6,240 tests. The 11
  failures are the same development-guide text checks as before; none are in the
  runtime. Anima weights have 62 tests of their own, and 23 real Anima images
  were generated on a GPU for this update (the results are in the Anima prompt
  weights row above).
- 2026-10-10 update: the full development suite ran 6,178 tests. The 11
  failures are the same development-guide text checks as before; none are in the
  runtime. Develop's tones are measured by running the real Develop code; the
  inpaint Auto Detail skip was confirmed on a real inpaint.
- 2026-10-05 update: the full development suite ran 6,139 tests. 11 failed, all
  in development-guide text checks that also failed before this update; none
  are in the runtime. Workshop, saving, Check for Updates and the Gallery
  additions have their own route and arithmetic tests.
- Git delivery: 49 focused checks passed, one Windows symlink test skipped.
  Fresh venv setup from cached dependencies/assets, `git pull --ff-only`, offline
  reuse, settings preservation and `pip check` passed. The non-mock backend
  exposed 23 samplers, 18 schedulers, six latent/four image upscalers and five
  verified detectors. Public internet setup and real generation remain untested.
- Existing Canvas set: 148 tests; 147 initially passed and one aggregate
  fingerprint failed. Individual file pins already matched. Only the two
  aggregate fingerprints were regenerated; the failing test then passed.
- Initial packaging/bootstrap/privacy/archive set: 53 tests passed.
- Tester cleanup: 76 packaging/setup/privacy/diagnostics checks passed; the
  extracted app exposed the expected sampler/upscaler registries and 5 detectors.
- Fixture-based feature set: 464 tests passed across generation, Inpaint,
  Hires, Auto Detail, jobs, recovery, Gallery, Wildcards, preferences,
  lifecycle and capability gating.
- The [historical preparation review](https://github.com/ToxicHost/sd-webui-forge-studio/blob/7543faf5cf11cabbb8648afecdbe2c1bf76cb4b4/docs/studio/RELEASE_PREPARATION_REVIEW.md) records the earlier fixture/archive checks.

Apart from the 2026-10-12 Anima weights check, no fresh real-GPU image was
generated in these preparations. Historical release notes are not evidence that
this exact candidate passed those live scenarios.
