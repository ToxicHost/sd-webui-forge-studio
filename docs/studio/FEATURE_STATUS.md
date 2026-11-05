# Forge Studio — feature status

This describes the current Forge Studio source, not the inherited Neo README.
Studio has been installed from fresh Git clones and used to generate on real
GPUs by its makers and early testers.

**Available** means the feature is there and has seen real use. **Newer** means
it is there, but it is recent or has had less real use. "Checked on a real GPU"
names what was checked, not a certification of every model or setting.

## Available, with the stated limits

| Area | Current status | Evidence / limits |
|---|---|---|
| Studio interface and local server | Available | Installed from fresh Git clones. Windows/NVIDIA is the target. |
| Model folders and selection | Available | Add any number of folders in Settings > Model folders (or on the welcome screen); every subfolder is scanned and each file sorted by what it is, with an Unrecognized list to correct. Earlier per-type folders are converted on first start. Then choose checkpoint/components; Generate owns loading. The checkpoint lists group models by subfolder, and each folder folds. |
| Basic image generation | Available | Used on real GPUs. Memory needs depend on the model; the console warns when memory runs short. |
| Samplers and schedulers | Available | Forge Neo's samplers and schedulers, plus Beta 57. |
| Img2Img and Inpaint | Available | Used on real GPUs. |
| Hires | Available | Used on real GPUs. Requires a suitable upscaler/model and enough memory. |
| Auto Detail | Available | Used on real GPUs. Five detectors are prepared by setup. A detector finding no target can leave the image unchanged. While inpainting, faces the mask is not on are skipped (confirmed on a real inpaint). |
| Queue and cancellation | Available | Cancellation is bounded by safe execution points, not necessarily immediate. Skip stops the job in Queue mode. In Batch mode it abandons the current pass and goes on to the next, keeping finished pictures (0.9.13; checked on a real GPU: the pictures left are pixel-identical to an unskipped batch). The base half of a batch using Hires Fix > Model stops the job instead. |
| Batch mode | Newer | Settings > Generation > Generate mode: Queue (default) or Batch. Batch size is Neo's batch (one sampling pass), Batch count the passes; confirmed on a real GPU. Regions, ControlNet, Klein references, Word Detail, image Hires and outpaint refine make one picture per pass. A large Batch size can run out of video memory (Studio names it). |
| Canvas brushes, layers and masks | Available | Brush Engine V2 is the default painting engine: automated brush checks passed and every preset was reviewed with a mouse. Aliased/Pixel Perfect brushes, touch input and mask/region painting use the previous engine. No tablet certification. |
| Canvas recovery | Available | A capture saves only the layers, mask and regions that changed, and a restored document is byte-identical in tests; closing the last tab or closing a tab mid-save is safe. Undo history is not restored. Keep separate artwork backups. |
| Gallery | Available with limitations | Image similarity/metadata are not universal format guarantees. |
| Preferences | Available with persistence limits | The theme and custom themes are saved on the server. Browser-local tool/layout state resets if Studio has to use another port because its usual one is busy. |
| Themes and custom themes | Newer | Seven themes (Liam default, Cream Paper light) with contrast checked by tests; custom themes derived from a few colours, with a readability check, export and import. Studio's own colour picker in the editor. Gallery and Workshop in the light theme have had the least use. |
| Refreshed screens | Newer | Workspace, Settings, Gallery, Codex, the tour, Workshop, dialogs, Wildcards and Develop restyled to match the Generate panel; no control removed. The Cream Paper light theme has had the least use. |
| Wildcards / Lexicon | Available | Configured local folders are required for user content. Refresh lists newly added wildcards, and they expand without a restart. Preview picks an entry and shows what it resolves to. Files can be dragged between folders, and the folder exported and imported as a zip (0.9.13). Import keeps a file that already exists and takes only `.txt` files. Checked in the browser on the mock server and by tests. |
| LoRAs / embeddings | Available | Used on real GPUs. LoRA and Checkpoint browsers with previews, activation text and Civitai lookup. Refresh lists newly added LoRAs. A LoRA added while Studio runs is used without a restart, and one that fails to load says why in the console. |
| Develop | Newer | Saved presets. Sliders render in a background worker; saved pictures are byte-identical to the earlier renderer; Dehaze and Noise Reduction preview at half size while dragging. A "Develop on" chip in the canvas toolbar shows when Develop is changing the picture. Float (High Precision) sources are not available. |
| Workshop | Newer | Weighted Sum, Add Difference and SLERP merges, block weights, LoRA and VAE baking, multi-step chains, History, and a basic Inspector. The arithmetic was tested against the reference formulas on test models, and a real merge on real checkpoints. LoRA baking is proven on test LoRAs only. Results go to the first checkpoint folder; files are never overwritten. Anima models saved with different key prefixes (ComfyUI's included) are recognised as compatible. |
| Saving and export | Available | Save, Save to Gallery, Canvas Export (with watermark stamping) and Export EXR (Standard) write files. |
| Anima prompt weights | Newer | Studio weights are the default on Anima; reused weighted images made before this feature or elsewhere switch to Neo-compatible (the previous behaviour). Checked on a real GPU for text-to-image: prompts without weights unchanged, Neo-compatible byte-identical to the previous version. Weights in Hires, Img2Img/Inpaint and Auto Detail passes are covered by tests, not yet by a GPU check. |
| GPU choice | Newer, on machines with two or more GPUs | Settings > Performance & VRAM > GPU; applied at the next start. Tested in code only; no two-card hardware check yet. |
| Check for Updates | Available for Git installations | Fast-forward update of the tracked branch, then a restart. It refuses copies with edited or own-committed files. Tested against real Git repositories, including a shallow clone. |
| Support report | Available | Script is supplied; report is created locally for inspection before sharing. |
| ControlNet | Newer | Three units for SD 1.5, SDXL and Anima (Control-LLLite), with Forge Neo's own ControlNet code and preprocessors; Klein through "Use as Klein reference". Checked on a real GPU: SDXL Union canny, Anima line art and depth follow their control picture (measured against the same seed without ControlNet), Anima pose runs but is weak, Hires at 1536, map previews, first-use downloads. Preprocessors needing mediapipe or insightface are not installed; Depth Anything V1 is withheld. |
| Regional prompting | Newer | Regions from the Regions panel are sent with Generate, any number. Automatic uses Attention Couple on SD 1.5 and SDXL and masked prompts on other models; One pass per region repaints each region of a canvas picture separately. Hires keeps the regions; Auto Detail gives a face its region's prompt. Checked on a real GPU on SDXL, Anima and FLUX.2 Klein: regions land on their side and swap, Hires at 1536 keeps them, one pass per region leaves the rest byte-identical. Attention Couple was not checked on SD 1.5 (no SD 1.5 model in the check). |
| Live Painting | Newer | ▶ Live repaints the canvas as img2img after each stroke, into the preview pane; Apply to Layer, Strength, seed re-roll; Generate turns Live off. Frames are never saved or added to the Gallery. Checked on a real GPU at 1024: about 3 s a frame on SDXL, 6 s on Anima, 2 s on FLUX.2 Klein after the first. |
| Hires on another model | Newer | Hires Fix > Model runs the Hires pass and Auto Detail on a second model with its own Text Encoder and VAE, any family. One model on the GPU at a time; a switch adds about 4-5 s (Anima) or 7 s (SDXL) on a 16 GB card. Text-to-image only. Checked on a real GPU: Anima to Anima, Anima to SDXL, SDXL to Anima and a batch of 2. |
| Refiner | Newer | A second checkpoint of the same family takes over the sampling at a chosen step; works with Hires and Auto Detail. Its LoRAs are the main prompt's, none, or its own list; on Anima it reads the prompt with its own text adapter. Another family is refused before generating. Text-to-image, .safetensors only. Checked on a real GPU (Anima): the switch and restore take 2-3 s each; its own LoRA reaches its steps; pictures made afterwards are pixel-identical to those made before. |
| Faster upscaler (half precision) | Available, off by default | Settings > Performance & VRAM. Remacri's upscale took 2.6 s instead of 4.8 s on an RTX 5060 Ti; fine detail in the final picture can differ slightly. |
| Low-memory warning and crash recovery | Newer | A console warning, once per job, when RAM use reaches 95 % while a picture is being made; after an unexpected stop the launcher restarts Studio and the page says why. Crash recovery checked with the real launcher and server (a forced stop, restart, the notice once); the warning is covered by tests. A real out-of-memory crash was not reproduced. |
| Masks and word masks | Newer | Named masks in a Masks panel, masks from prompt words (MobileSAM on the CPU) and from tones or colour, inpainting with any mask, Develop local adjustments, PSD layer masks. Word masks need a picture Studio made with heatmap data; they were measured against reference masks on research pictures. |
| Outpaint, Canvas Size, Smart Patch, Paint Mixer | Newer | Outpaint checked on a real GPU (large pictures, Refine new area); the others covered by tests and browser checks. |
| Folder dialogs and network model folders | Newer | Browse opens the system folder dialog for every folder setting; network model folders are opt-in and bounded by timeouts. Covered by tests. |
| Word heatmaps | Newer | Anima and SDXL pictures record each prompt word's attention; the Gallery's Words section shows it. Settings > Output & Files > Heatmap data turns recording off, moves or cleans up its folder. In a batch, only the first picture has word data. Checked on a real GPU for Anima and SDXL: pictures byte-identical with recording on and off. |

## Unavailable or not yet run

| Area | Status |
|---|---|
| Mac (Apple Silicon) | Built and tested on Windows with simulated Mac devices; not yet run on a real Mac. See [MAC_INSTALL.md](MAC_INSTALL.md). |
| Linux/NVIDIA Docker | Experimental [setup supplied](../../packaging/docker/README.md); config and native adapter checks only. Image build, GPU passthrough and container generation have not been run end to end. |
| AMD and Intel GPUs | Not supported yet. |
| Anima 2.9B and 3.8B (40 and 52 blocks) | Not yet run. Since 0.9.13 Studio builds the model with the file's own block count, and a LoRA for a smaller model follows Forge Neo's block tables. No such model has been run here; pictures from 28-block models were checked pixel-identical. Word masks on these models use blocks chosen on 28-block models, and Workshop's block weights name the first 28 blocks. |
| Extensions, scripts and the API | Not supported: Forge and A1111 extensions, X/Y/Z Plot and other scripts, the public `/sdapi` API. |
| Other Forge Neo features | Not supported: img2img resize modes and batch from a folder, Styles, face restoration, video generation. |
| High Precision float output | Not available. Export EXR (Standard) converts the 8-bit image; no float capture is offered. |
| Separate Inpaint Sketch mode | Intentionally superseded by Canvas painting/masks; not an outstanding promised mode. |
| Automatic updates | Not automatic. Git installations update from Settings > About > Check for Updates or with `git pull --ff-only`; see [GIT_INSTALL.md](GIT_INSTALL.md). |
| Multi-user / public network service | Not offered. Studio serves its pages only to the computer it runs on. |
