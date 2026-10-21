# Studio Standalone — Git tester candidate

Studio's version is shown in Settings > About. Run `git rev-parse HEAD` for the
exact installed revision. This is a test version for Windows/NVIDIA; 1.0 comes
when Regional prompting and Live are in.

## 0.9.1: Regional prompting, smooth Develop — 2026-10-21

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Nothing new is installed.

- **Regional prompting.** The Regions section of the Generate panel now
  reaches the picture. Click **+** to add a region, click its row and paint
  where it applies, and give it its own prompt (and negative). The main
  prompt still covers the whole picture and goes in front of every region's
  prompt, so keep lighting and style there. Leave unpainted gaps between
  regions: they keep the scene one scene. There is no limit on the number of
  regions; each one adds sampling time (about 3.5 s on SDXL, 7.5 s on Anima,
  3 s on FLUX.2 Klein at 1024, on a 16 GB card).
  - **Method**, shown once there is a region. *Automatic* uses Attention
    Couple on SD 1.5 and SDXL models and masked prompts on every other
    model. *Attention Couple* (SD 1.5 and SDXL) steers each region's words to
    its area inside the model, in one pass; *Masked prompts* works on every
    model and blends one prediction per region.
  - **One pass per region** is the fourth method, for a picture already on
    the canvas: each region is repainted separately with its own prompt and
    its own Denoise (the row's slider becomes Denoise), and the results are
    put together; the parameters end "Regions: N, one pass each".
  - The slider on each row is the region's **Weight** (0.25–2).
  - A hidden region (the eye), or one without a prompt or paint, is not sent.
  - **Hires keeps the regions.** In **Auto Detail**, a face inside a region is
    fixed with that region's prompt when the slot has no prompt of its own.
  - Checked on a real GPU through the same path the page uses, on SDXL, Anima
    and FLUX.2 Klein: red-left/blue-right regions land on their side and
    swap when swapped; four regions that all say the same thing give nearly
    the picture that word gives in the main prompt (the guidance does not
    add up region by region, which would burn the picture); Hires at 1536 keeps the
    regions; one pass per region leaves the area outside the regions
    byte-identical.
- **Develop is smooth.** The sliders no longer jump and the picture keeps up:
  the adjustments render in the background (a worker) instead of on the
  page's own thread, once per change instead of two or three times. With
  Dehaze or Noise Reduction on, the picture is drawn at half size while you
  drag and sharpens a moment after you let go. Saved pictures are exactly as
  before (checked byte for byte). Also fixed: with Highlights/Shadows on, the
  shading could come from an older version of the picture after painting or
  generating.
- **Help Center.** Regional Prompting, Attention Couple and ControlNet have
  their articles back; "Not in Studio Yet" no longer lists them; the "Under
  the Hood" sections show again (they had stopped appearing).

## 0.9.0: ControlNet, a memory warning, crash recovery, licence notices — 2026-10-20

**Studio has its own version number now: 0.9.0.** Settings > About used to
say v4.17.0, the Studio Extension's number. Each update counts up from here
(0.9.1, 0.9.2 ...) until 1.0.

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. **The first start after
this update installs five small Python packages** (addict, ftfy, fvcore,
timm, yapf) that ControlNet's preprocessors use; it needs the internet once.
Pictures from the models you already use are unchanged: checked on a real
GPU against the previous update, five jobs (Anima, Anima with Hires, Auto
Detail, Outpaint with Refine, SDXL), all byte-identical.

- **ControlNet.** The Generate panel has a ControlNet section with three
  units, for SD 1.5, SDXL and Anima models. Each unit takes the canvas, the
  active layer or an uploaded picture, a preprocessor and a ControlNet model,
  with Weight, Start/End, Control mode, Resize mode, Resolution and which
  passes it applies to when Hires is on. Put your ControlNet models in a
  folder and choose it in Settings > Model folders > ControlNet. Union models
  (one model, many control types) pick their type from the preprocessor.
  - **Anima** uses Control-LLLite models (line art, depth, pose). For line
    art, give it black lines on white with the preprocessor set to None;
    for depth, use depth_anything_v2. Pose is weak on Anima: treat it as a
    hint.
  - **Preview map** shows what the preprocessor makes of your picture
    before you generate.
  - **FLUX.2 Klein has no ControlNet.** With a Klein model selected, "Use as
    Klein reference" adds a unit's map to Reference Images; then say in the
    prompt what to follow ("match the pose from image 2").
  - A preprocessor's own model is **downloaded the first time you use it**;
    the Generate button says "Downloading <file>…" while it does.
  - A few preprocessors are greyed out: the ones that need mediapipe or
    insightface (not installed), and Depth Anything V1, which downloads and
    runs code from the internet. Depth Anything V2 works.
- **A warning when memory runs low.** If your computer's RAM is nearly full
  (95 %) while a picture is being made, the console window says so once for
  that job, with what to do about it. Nothing is asked before Generate.
- **If Studio crashes, it comes back and says why.** When the engine stops
  unexpectedly in the middle of a job (for example when the computer runs
  out of memory), the launcher restarts it on the same address and the page
  tells you what happened: out of memory or the error code, and what was
  being made. Your canvas is untouched. It stops restarting after three
  crashes in ten minutes.
- **The Wildcards tab no longer disappears.** It could vanish until the page
  was reloaded, even with a working wildcard folder. It is always there
  now; without a folder it says to choose one in Settings > Dynamic Prompts.
  Gallery and Workshop could disappear the same way and now come back by
  themselves.
- **Inpainting after an Outpaint is a normal inpaint again.** Once a picture
  had been outpainted, every later inpaint on it (a face, a hand) was quietly
  made as another outpaint: the whole picture at about 1 megapixel, without
  your padding or Hires, so it came out softer. Only a re-roll of the
  outpaint itself is made that way now.
- **Dropdowns show what is selected.** After switching to a different kind
  of model, Text Encoder, VAE, Sampler and Scheduler could keep showing the
  previous choice, and picking the right one again did nothing. Fixed.
- **Progress you can believe.** The Generate button counts every pass from
  the start, Auto Detail included; before, a later pass kept the previous
  pass's last step (for example "29 / 11"). The console's progress bars
  redraw in place as normal bars instead of printing a new line of digits
  and # signs at every step, and two harmless warnings no longer print
  at every start.
- **How long it took.** Saved pictures note it in their parameters, as
  "Time taken: 2 min. 17.3 sec." (from the start of the job to the saved
  file, not counting time in the queue), and the Gallery's details show it.
- **Licence notices.** Studio is AGPL-3.0-or-later. A new NOTICE.md says
  what Studio adds to Forge Neo and where the source is, and the third-party
  notices now cover every component that ships.

## Klein reference images, MOSS-Image, Close all tabs — 2026-10-19

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Pictures from the models you
already use are unchanged: checked on a real GPU against the previous update,
five jobs (Anima, Anima with Hires, Auto Detail, Outpaint with Refine, SDXL),
all byte-identical.

- **FLUX.2 Klein: reference images.** With a Klein model selected, the
  Generate panel shows a Reference Images section. Add up to four pictures
  from a file, from a canvas layer, or by dragging them in (files, results or
  Gallery pictures), then name them in the prompt: "the character from image
  1 wearing the dress from image 2". In an image edit the canvas is image 1
  and the references follow it. "Max side" sets how large each reference is
  read (1024 by default; larger keeps more detail and costs more memory and
  time). Saved pictures note how many references they used; the reference
  pictures themselves are not stored, so add them again when you reuse one.
- **FLUX.2 Klein, ERNIE-Image: right size.** Pictures from these models came
  out at twice the size you asked for, and some image-edit sizes (for
  example 1024x1368) failed. Both are fixed.
- **No "ghost" after stopping a Klein edit.** Stopping a Klein image edit
  early could leave that picture behind, and the next Klein job would quietly
  use it as a reference. Every job now starts clean.
- **MOSS-Image.** The new OpenMOSS model (good at writing text in pictures)
  runs in Studio, using Comfy-Org's single files: the MOSS-Image model, the
  Qwen3.5 9B text encoder and the FLUX.2 VAE. It needs a large card (about
  11 GB while sampling).
- **Close all tabs.** Right-click a Canvas tab for Close other tabs and Close
  all tabs (or right-click the empty tab strip). Closing everything leaves one
  blank document, and if any of the tabs have unsaved changes, Studio asks
  once and says how many.

## Help Center, translations, raw prompts, fixes — 2026-10-18 (fourth update)

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again. Pictures are unchanged:
checked on a real GPU against the previous update, five jobs (Anima, Anima
with Hires, Auto Detail, Outpaint with Refine, SDXL), all byte-identical.

- **Help Center refreshed.** It describes what Studio does now: new articles
  for the Masks panel, masks from words, tones and colour, word heatmaps,
  Word Detail, Outpaint and Canvas Size, the canvas right-click menu, Smart
  Patch, Paint Mixer, Develop local adjustments, crash recovery, model setup,
  the Settings map and a "Not in Studio yet" list. Show Me has new
  walkthroughs, and its tutorial tabs now close when they finish.
- **German, Spanish and French.** The whole interface is translated: menus,
  dialogs, panels, tooltips, Settings, the Help Center and Show Me. These are
  first drafts; corrections from native speakers are welcome.
- **The raw prompt is saved.** When wildcards change your prompt, the picture
  keeps what you typed as well (a `Template:` line), in the file and in the
  Gallery, so sending it back with the raw prompt works. Pictures made before
  this update cannot get it back.
- **Gallery shows a just-made picture's real size.** It showed the size before
  Hires.
- **The tutorial and the welcome card are remembered** in Studio's own
  settings, not only in the browser, so they do not come back after a change
  of port or browser.
- If reading your upscaler folders ever fails, Studio keeps the list it had
  and stops retrying, instead of retrying at every menu refresh.
- Groundwork for a Mac version (Apple Silicon). Not supported yet: it is being
  tested.

## Settings kept between launches, upscaler folder, file names, Open in New Tab — 2026-10-18 (third update)

Update from Settings > About > Check for Updates, or with `git pull --ff-only`,
then close Studio and run Start-Studio.bat again.

- **Studio keeps your settings between launches.** It used to start on a new
  port each time, and the browser keeps theme, layout, panel sizes, tool
  settings and tour progress per port, so every start looked like a first
  start. Studio now uses port 7865 every time (or the next free one if
  something else has 7865). Settings lost on earlier launches cannot be
  brought back. Choose them once more and they stay.
- **Upscaler folder** (Settings > Model folders): point Studio at the folder
  where you keep upscalers (ESRGAN, Remacri, 4x-UltraSharp and the like),
  for example Forge's `models/ESRGAN`, with Browse… and Save folders. You can
  add several folders. Studio's own bundled upscaler stays listed, and the
  Hires and Upscale menus update when you save.
- **File name** (Settings > Save folder): name new images with a pattern,
  for example `[date]_[model_name]_[number]`. The tokens are `[number]`
  `[seed]` `[date]` `[datetime]` `[model_name]` `[prompt_words]` `[steps]`
  `[cfg]` `[sampler]` `[width]` `[height]` `[mode]` and more, with a preview
  as you type. Leave it blank for the usual `Studio-[number]-[seed]`.
  Existing files are never overwritten.
- **Open in New Tab** on the Canvas right-click menu shows the picture in a
  browser tab without saving it.

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
