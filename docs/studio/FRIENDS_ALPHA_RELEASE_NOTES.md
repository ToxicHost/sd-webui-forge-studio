# Studio Standalone — Git tester candidate

Distribution identifier: 0.0.0-internal-alpha. Run `git rev-parse HEAD` for the
exact installed revision. This is an alpha candidate for Windows/NVIDIA testing.

## Whole graphics card, updater fix — 2026-10-18 (second update)

Update with `git pull --ff-only` in your Studio folder, then close Studio and
run Start-Studio.bat again. If Git answers "detected dubious ownership", run
the `git config --global --add safe.directory ...` line it prints, then pull
again. After this update the in-app updater handles that case itself.

- **Studio uses your whole graphics card.** It held itself to 14 GB of video
  memory, so on a 16 GB card it lost 2 GB, and on a 24 GB card 10 GB. Large
  pictures could then fail with "out of memory" where Forge Neo would have
  moved part of the model off the card and carried on. Studio now uses the
  card exactly as Forge Neo does. A `vram_ceiling_gib` left in an old
  configuration file is ignored. Pictures that worked before are unchanged.
- **Check for Updates works on a copy Git calls "dubious ownership"**, which
  is common when Studio was cloned onto a second drive. It used to say the
  copy "wasn't installed with Git". If Git cannot read the copy for another
  reason, the message now says what Git reported.

## Masks, word masks, new canvas tools, folder dialogs — 2026-10-18

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. **The first start after
this update downloads one more model file (MobileSAM, 41 MB) for word
masks.** The generation engine files, installer and requirements are
unchanged.

- **Browse opens your system's folder dialog.** Every folder setting (model
  folders, Save folder, Gallery folders, wildcards, heatmap data) has a
  Browse… button that opens Windows' own "Select Folder" window. Typing a
  path still works.
- **Masks panel** (inside Layers). Besides the Inpaint mask, a document can
  hold named masks: New, rename, show/hide, duplicate, invert, delete; Save
  selection as mask and Load as selection (Shift adds, Alt subtracts,
  Shift+Alt intersects). Masks are kept per tab and come back after a
  reload. The Inpaint mask stays a hard brush; named masks take Opacity and
  Hardness.
- **Masks from words.** Masks > From word… lists the words of the selected
  layer's picture (Studio pictures made with heatmap recording on). Pick one
  or more words, choose Object, Area/light or Everything else, adjust Grow
  and Feather, or step through an object's parts. The first time for a
  picture takes a few seconds on the CPU, with a progress bar.
- **Masks from tones or colour:** highlights, midtones, shadows or a colour
  range.
- **Inpaint with any mask.** The mask selected in the Masks panel is the one
  Generate inpaints with; a soft mask is cut at 40%. With Soft Inpainting
  on, a feathered mask's grey is used.
- **Develop local adjustments:** Exposure, Contrast, Highlights, Shadows,
  Whites, Blacks, Temperature, Tint, Saturation and Vibrance inside a named
  mask.
- **PSD:** named masks are saved as layer masks and come back when the PSD
  is opened in Studio.
- **Word Detail:** an Auto Detail slot (Model: Words…) that details the
  prompt words you name, using the picture's heatmap data. In a batch, only
  the first picture.
- **Canvas tools:** Outpaint (extend a side; about one normal generation's
  time even on large pictures, with an optional "Refine new area"), Canvas
  Size, Smart Patch (remove small things from their surroundings, no model),
  Paint Mixer (a Mix toggle on the Brush that picks up wet colour and mixes
  like paint: blue and yellow make green), Sharpen (in Blur) and Heal (in
  Clone). Fill has Tolerance, Contiguous and real opacity. Selections follow
  one rule in every tool, are undo steps, and are kept per tab and after a
  reload. Transform moves an ellipse or lasso selection's shape.
- **Saving:** the Save folder setting works (it did nothing before).
  Pictures are saved as `studio/<create|edit|upscaled>/<date>/Studio-NNNNN-<seed>`
  under the results folder, or in Forge Neo's layout. A folder outside
  Studio's trusted places falls back to the default and says so.
- **Workflows and saved layouts** save, load and delete (they failed
  before). **Embeddings** have their own folder setting.
- **Network model folders** (opt-in): Settings > Model folders > "Allow
  network model folders" lets a model folder live on another machine. An
  unreachable share shows as unreachable instead of freezing Studio.
- **Generation now matches Forge Neo in three places:** Anima's Hires pass
  uses the same shift as its base pass (it used 3.5 instead of 3.0); Auto
  Detail gives each region its own seed and keeps the detector's order, as
  ADetailer does, and `[SEP]`/`[PROMPT]` work in slot prompts; prompt
  comments are removed before generation; each model family uses its own
  shift. **Anima pictures with Hires, and Auto Detail results, will differ
  slightly from before.**
- **Heatmaps:** an "Only masked" inpaint's heatmap now sits inside the area
  that was inpainted.

Checked on a real GPU (2026-10-18, the previous build against this one on
the same prompts and seeds): plain Anima, SDXL and Outpaint with Refine are
byte-identical; Anima with Hires and Auto Detail change slightly, as
intended. Also checked on a GPU: Word Detail, Outpaint on a large picture,
heatmap recording on SDXL and on "Only masked" inpaints (byte-identical with
recording on and off).

## Word heatmaps, crash recovery, fixes — 2026-10-15

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Only Studio's own interface
and local server change; the generation engine files, launcher, installer,
requirements and models are unchanged.

- **Word heatmaps in the Gallery.** Every Anima or SDXL picture made from now
  on records where each word of its prompt landed. Open the picture in the
  Gallery: a **Words** section sits above Metadata.
  - Click a word to lay its heat map over the picture; click it again to
    clear it. Zoom and pan carry the overlay along.
  - The bar beside each word is its strength compared with the picture's
    typical word. Regions and adjectives read short by nature.
  - **Text used here** (Anima only) shows where the picture drew on the
    prompt at all.
  - Pictures made before this update have no Words section.
- **Settings > Heatmap data:** turn recording off (for big batches), see how
  much space it uses, move its folder (type the full path, then Move here),
  clean up leftovers, or open the folder. Recording costs about 0.3 seconds
  and 250-300 KB per picture. A picture's data is deleted when the Gallery
  trash is emptied.
- **The first start after this update upgrades the Gallery database.** Studio
  backs it up first, as `gallery.v1.<time>.bak` in the gallery folder.
- **Fixes:**
  - Copy image works on a picture you just generated (it failed with "Failed
    to fetch"). Copying text works when Studio is opened by a network address;
    copying an image there needs Studio opened at 127.0.0.1 or localhost, and
    Studio now says so.
  - Refresh in the LoRA and wildcard browsers shows files added since Studio
    started, and a new wildcard works in the next generation without a
    restart.
  - Dragging an image onto the canvas keeps your seed; its prompt and other
    settings are still used.
  - With Remember Last Session on, Studio reopens with your last session's
    model instead of an older choice, and the session no longer stops saving
    without telling you.
  - Crash recovery saves only what changed and uses less memory on large
    documents. Closing the last tab no longer risks the tab that takes its
    place, and a tab closed while it was being saved stays closed.

Checked on a real GPU for Anima: ten pictures byte-identical with heatmap
recording on and off, and the maps matched the research capture exactly.

## Prompt weights on Anima — 2026-10-12

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Only the Studio interface and
its local server change; the generation engine, launcher, installer,
requirements and models are unchanged.

- **Prompt weights now work on Anima models.** `(word:1.3)` makes a word count
  more and `(word:0.5)` less, and a weight now changes how strongly the word is
  used, not only how much of it is mixed in.
  - Brackets on their own no longer change the picture: `(word:1.0001)` gives
    almost exactly the image without brackets.
  - 1.6 often brings a missing object into the picture while keeping the scene;
    2.0 often reshapes the scene.
  - `(word:0)` silences a word, but it cannot remove something the rest of the
    prompt implies ("in a white dress" still draws a woman). Use the negative
    prompt or delete the word instead.
  - BREAK has no effect on Anima, which has no 75-token chunks to separate. The
    bar under the prompt says so.
- **Your earlier Anima images still reproduce.** Reusing the parameters of an
  Anima image whose prompt has weights or BREAK, made before this update or in
  another program, switches its weights to "Neo-compatible" (the previous
  behaviour). A note under the prompt shows it, and "Use Studio weights"
  switches back. A prompt without weights gives the same image either way.
- A prompt that uses prompt editing (`[a:b:N]`), `AND`, or a separate Hires
  prompt runs the previous behaviour for that image, and Studio tells you why.
- SDXL and the other model families are unchanged.

Checked on a real GPU: eight prompts without weights gave byte-identical images
to before, Neo-compatible gave byte-identical images to the previous version on
weighted and BREAK prompts, and weighted prompts matched the research images,
with no measurable speed cost.

## Develop fixes, smarter Auto Detail, GPU choice — 2026-10-10

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Only the Studio interface and
its local server change; the generation engine, launcher, installer,
requirements and models are unchanged.

- **Develop (the edit tab) — fixed:**
  - Shadows lifts dark areas and keeps their colour (it used to wash them out to
    grey or white).
  - Blacks moves the black point gently and keeps colour (it used to crush or
    wash out the whole image).
  - Dehaze now removes haze (positive values used to add it), with no light
    rims around objects; -100 no longer replaces the picture with one colour.
  - Vignette darkens or lightens the corners softly instead of turning them
    black or white.
  - Calibration > Blue Saturation works above +33.
  - A minus sign can be typed in the number boxes (it used to turn into 0).
  Images you already edited in Develop will look different with these fixes.
- **Auto Detail while inpainting** skips faces your mask is not on. A face is
  detailed when the mask covers at least half of it, or at least half of the
  mask is on it — so masking one of two characters details only that one, and
  a small mask on an eye still details that face. Skipped faces show as
  `outside_mask=` in the console, and the job is faster.
- **Settings > Performance & VRAM > GPU** (only on machines with two or more
  graphics cards): choose which card Studio runs on. Takes effect at the next
  start.

The Develop fixes were measured with automated checks and a visual comparison;
the Auto Detail change was confirmed on a real inpaint. The GPU choice has only
been tested in code — please report how it behaves on a two-card machine.

## Workshop, saving and in-app updates — 2026-10-05

Update once by hand with `git pull --ff-only`, then close Studio and run
Start-Studio.bat again. From then on, Settings > About > Check for Updates can do
this for you. Only the Studio interface and its local server change; the
generation engine, launcher, installer, requirements and models are unchanged.

- **Workshop is available.** Merge two checkpoints with Weighted Sum, Add
  Difference or SLERP, optionally per block (with presets); bake LoRAs or a VAE
  into a checkpoint; chain several steps on one board; and look back through
  merges in History. The Inspector shows model info, whether two models can be
  merged, and a memory estimate. Results are saved into your first checkpoint
  folder and appear in the model lists straight away; an existing file is never
  overwritten. Merging needs free system RAM of roughly the larger model's size
  plus 2 GB. Experimental merge methods and Test Merge are not included.
- **Check for Updates works** for Git installations. It lists what is new and
  updates with one click (fast-forward only), then asks you to restart. It
  refuses, and says why, if Studio's own files have been edited.
- **Saving works:** Save, Save to Gallery and Canvas Export now write files, and
  Export EXR (Standard) is available from the output menu.
- **Model browsers:** the Checkpoint and LoRA browsers show previews, LoRA activation
  text and preferred weight, and can look a model up on Civitai.
- **Lexicon:** duplicate and move wildcard files, and search inside them.
- **Develop:** saved presets work.
- **Gallery and session strip:** opening an output in the Gallery finds it by
  content, and the session strip shows thumbnails.
- **Watermarks:** Settings > Watermark lists your watermark images (with an Open
  folder button) and stamps them on Export. The older "stamp every generated
  image" mode works too.
- **Generation metadata** now names the real checkpoint file.
- **Mask brush:** Shift+drag resizes it, as with the other brushes.

Workshop and the new routes have automated tests; a real merge was also checked
on real checkpoints. LoRA baking is proven on test files only, so please report
any LoRA that bakes wrongly. Keep backups of models you care about.

## Canvas painting update — 2026-09-30

Update with `git pull --ff-only`, then restart Studio. Only the Studio interface
and its local page server change; the generation engine, launcher, installer,
requirements and models are unchanged.

- A new painting engine (Brush V2) is now the default for the brush and eraser.
  Presets have their own materials: Pencil, Charcoal and Pastel lay strands that
  follow the paper's grain, Bristle Rake lays separate bristles, Scatter Dust
  scatters particles, and Airbrush builds while held still and shows the build
  as it happens. Flat tips turn cleanly through corners.
- Hard Ink, Fine Liner and Sketch Light were removed from the preset list:
  Basic Round covers the first two, and Pencil at a lower Density the third.
- The Flow control is gone: Opacity is the only strength setting, and saved
  tool settings are converted.
- Density is on the brush's context bar. Pencil starts at 70%; use 100% for a
  plain line.
- Settings > Canvas > Mouse press: a mouse paints at medium pressure by default,
  so paper grain shows. Switch it off for full pressure.
- Symmetry, Taper In and each preset's pressure, speed and tilt response work in
  the new engine. A mirrored calligraphy or chisel stroke is a true mirror image.
- Aliased brushes and Pixel Perfect, touch input, and Inpaint Mask or region
  painting still use the previous engine.
- Inpaint has a dedicated Mask tool with its own settings and history. Undo or
  redo pressed mid-stroke cancels only that stroke, and crash recovery waits
  until a held stroke ends.

The presets were reviewed with a mouse. Pen pressure and tilt have automated
checks only; there is no tablet certification yet. Keep backups of artwork.

## Git delivery cleanup — 2026-09-15

The tester branch now contains runtime source, installation helpers, user guides
and licenses. Project plans, agent instructions, Docker/deployment files, tests,
legacy WebUI entry points and release-build tools are removed from this branch.
The retained engine, Studio frontend, launchers, installers and requirements are
unchanged. Existing installations can receive this cleanup with `git pull --ff-only`.

Start-Studio.bat creates or repairs the local Python environment, verifies the
six auxiliary models, and opens Studio. Python 3.11–3.13 is accepted; use 3.13 for
this candidate. Existing settings are preserved. The launcher creates empty
models/Stable-diffusion, models/VAE and models/text_encoder folders; users supply
their own generation models and choose model directories in Settings.

The five Auto Detail detectors and Remacri are downloaded from studio-assets-v1
only when missing, with pinned size/hash verification. This cleanup does not
change that release or any generation, sampling or painting behavior.

See [installation](GIT_INSTALL.md), [tester notes](FRIENDS_ALPHA_TESTER_GUIDE.md)
and [feature status](TESTER_FEATURE_STATUS.md). ControlNet, Live generation,
Workshop and regional inference are unavailable in this Standalone candidate.
Updates are manual. Real generation, interactive browser/tablet acceptance and
cross-platform certification remain separate from these installation checks.
