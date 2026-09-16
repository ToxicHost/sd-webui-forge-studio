"""Static source-faithfulness tests for the corrected Studio S0.7 shell.

These tests inspect committed files only. They do not start the presentation
server, execute JavaScript, open sockets, or contact a network.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = APP_ROOT / "forge_studio" / "frontend"
HTML_PATH = FRONTEND_ROOT / "index.html"
CSS_PATH = FRONTEND_ROOT / "app.css"
APP_JS_PATH = FRONTEND_ROOT / "app.js"
MODULE_SYSTEM_PATH = FRONTEND_ROOT / "module-system.js"
DOCS_CSS_PATH = FRONTEND_ROOT / "studio-docs.css"
DOCS_JS_PATH = FRONTEND_ROOT / "studio-docs.js"
LORA_STACK_PATH = FRONTEND_ROOT / "lora-stack.js"


CANONICAL_SHA256 = {
    # P0.6 makes the sampler and scheduler controls real. index.html stops
    # shipping literal option text (DPM++ 2M SDE, Karras) that reached
    # nothing, and app.js populates both from /api/registries and SENDS the
    # choice. The CSP loader hash is unchanged: the loader array did not move.
    # P0.4f adds the folder picker: studio-dir-picker.js is the ONE new
    # frontend file, index.html gains a root list and an Add button per role
    # plus the loader entry, app.css gains the row and overlay rules, and
    # app.js loses the last four /studio/gallery/pick-folder call sites. The
    # server-side dialog opened on the machine running Studio, which is the
    # wrong machine for a remote host, a VM or a container -- it already
    # degraded to a toast, and now there is a picker instead.
    # Session Strip width closure: app.css changes for the first time in
    # this line of work -- one rule inside the documented
    # `@media (max-width: 1600px)` block now hides the strip's collapse
    # control, because below that width the media query owns the rail and
    # the control could not expand anything. index.html and app.js keep
    # their native-integration bytes. Every other canonical hash is the
    # adopted source, untouched.
    # index.html gains the Model Folders settings card: three role fields, a
    # Browse button each reusing the existing sanctioned folder dialog, a
    # per-role status line and one Save. No floating panel, no new category.
    # index.html loses the Profile selector and the Load button. The model is
    # the three role dropdowns, and Generate makes that selection resident.
    # The standalone Upscale panel's upscaler <select> loses its one hardcoded
    # <option>R-ESRGAN 4x+</option> and ships "Loading…" like every other
    # registry-filled dropdown. Markup only, outside the inline <script>, so
    # _SOURCE_LOADER_CSP_HASH is unchanged -- verified by recomputing it.
    # "Model / Session lifecycle (internal alpha)" becomes "Jobs". The old
    # panel showed BUSY, an Unload button and `1 active - 0 queued`: not wrong
    # for exposing a count, wrong for exposing lifecycle CONCEPTS instead of
    # the feature they support. Resident state, leases and cache ownership are
    # diagnostics and stay on /api/model/state. In their place: the running
    # job with its stage and a Cancel, the waiting jobs in execution order
    # with reorder and Remove, and a bounded Recent list. Unload survives as
    # an optional VRAM release, deliberately not presented as queue
    # management. Markup only again, outside the inline <script>, so the CSP
    # hash is once more unchanged and once more verified by recomputing.
    # P0.8 adds the FOURTH model-root row: the Auto Detail detector folder.
    # A root, not a fourth part of the model -- a detector is loaded per slot
    # and released, so it never joins the resident session, which is why
    # MODEL_ROLES gained it and RESIDENT_MODEL_ROLES did not. The save
    # description stops saying "all three". Markup only, outside the inline
    # <script>, so the CSP hash is unchanged and was recomputed to confirm it.
    # Repinned for the GPU tile compositing control. The owner decided the
    # launch flag was not a substitute for product UI, so Settings >
    # Performance & VRAM gains one toggle: the REQUESTED preference, which
    # the runtime preflight may still answer with the CPU path. Markup only,
    # outside the inline <script>, so the CSP hash is unchanged and was
    # recomputed to confirm it.
    # Repinned for the product-truthfulness hotfix. Four inpaint settings
    # in the mask context bar -- Area, Blur, Fill, Padding -- are hidden.
    # They drove a request path that does not exist: Operation has one
    # member, and the payload carrying those fields is assembled below the
    # lifecycle return in app.js, so it never executes on this host. D8
    # hides an absent service rather than labelling it, which is also what
    # test_context_toolbar_uses_source_data_contract asserts. Markup only,
    # outside the inline <script>, so the CSP hash is unchanged and was
    # recomputed to confirm it.
    # BE10 repins it for the Paper section of the brush panel: a surface
    # picker and three sliders -- Depth (SIGNED, -100 to 100), Scale and Tooth.
    # Depth and Scale belong to the DOCUMENT and the section says so in the
    # markup, because the panel around them is the brush panel and a paper
    # that followed the brush would be a filter wearing a paper's name.
    # Markup only, outside the inline <script>, so the CSP hash is unchanged
    # and was recomputed to confirm it.
    # BE13 repins it: two method toggles in the context bar, Alias and PP,
    # shown for the brush AND the eraser. The eraser has no preset system of
    # its own and the acceptance requires it can use the same aliased method,
    # so the bar is the only route it has. Markup only, outside the inline
    # <script>, so the CSP hash is unchanged and was recomputed to confirm it.
    "index.html": "4726fa71afac21913f8d14f74d6807f7cf84b3a75955bcfda5950e4fb09da1e5",
    # Repinned with the product-truthfulness hotfix. `[hidden]` is a UA
    # rule, so an author rule setting display on the same element wins --
    # `.ctx-inline { display: flex }` kept two hidden inpaint controls on
    # screen. One rule makes the attribute mean what it says. Caught in a
    # real browser, not in the markup.
    "app.css": "01a75e86958acdafed5f3714b9f9c2c7cdc022704df937d895b72d8e303addbc",
    # app.js moves for two owner-facing repairs found on one live leg.
    #
    # Generate: the lifecycle path submitted `seed: parseInt(...) || -1`, and
    # the Studio backend refuses a negative seed on purpose
    # (GENERATION_SEED_NOT_FIXED) because it will not substitute one silently.
    # Every click therefore failed. `_resolveSubmittedSeed()` now draws a
    # concrete seed client-side, so "random" still works and the seed reported
    # on the result is the seed that produced it.
    #
    # Send to Canvas: all three failure paths in displayOnCanvas were
    # console-only, so an undecodable source produced no layer and no message.
    # They now raise a toast (toast.canvas.* in locales/en.json).
    # app.js also stops asking the legacy /studio/check_model_te probe on a
    # lifecycle host. That route is keyed by a checkpoint TITLE and cannot
    # resolve the opaque catalogue id `paramModel` now carries, so it answered
    # needs_te:false for everything and #textEncoderRow stayed hidden -- while
    # Load asked the owner to choose a text encoder with no control to do it.
    # app.js again: displayOnCanvas no longer awaits imgEl.decode() as its
    # only gate. Chromium ties decode() to the rendering pipeline, so while
    # the document is hidden it never settles and Send to Canvas hung forever
    # with no layer, no error and no toast -- the original symptom. The load
    # event is awaited instead, and decode is best-effort and bounded.
    # app.js moves for auto-load on Generate: the job now NAMES the model it
    # wants, carrying the three catalogue ids as a `model_selection` object on
    # every generation request, instead of inheriting whatever happens to be
    # resident. This is what makes Generate legal with nothing loaded while
    # still never running on a model nobody chose.
    # P0.4a adds ONE comment line: `// ---- end model folders ----`, closing
    # the section the model-folders contract tests slice out by text. They took
    # a fixed 9000 characters from the opening banner, which reached 63
    # characters past the section's real end, so assertions about what the
    # model-folders code may do were reading gallery code. No behaviour moved.
    # The standalone Upscale panel joins the registry-filled controls: its
    # dropdown was the last one reading /studio/upscalers, and it pre-selected
    # "R-ESRGAN 4x+" -- a name this install does not have. `_loadRegistries`
    # now fills it from /api/registries `image_upscalers`, image kind only,
    # because that panel runs on a finished image and a latent mode has no
    # latent left to resize. Same P0.6/P0.7 repair, one panel over.
    # And now the other half of that repair: filling those four menus once, at
    # DOMContentLoaded, against a route that is EMPTY on a cold server left
    # every one of them showing the disabled "Engine default" placeholder until
    # a manual reload. app.js gains `_refreshRegistries`, a frozen
    # `window.StudioRegistries`, and a listener on `studio:model-state-changed`
    # -- carry, validate, and re-read when the source of truth changes.
    # And Live Preview: the toggle now travels on the generation REQUEST as
    # `preview_enabled`, instead of only on a `preview_config` socket message
    # that the standalone server never reads.
    # R0 truth recovery: guarded DELETE and Gallery onboarding mutations,
    # truthful unknown-architecture handling, retry-aware registry liveness,
    # and persistence-aware defaults success. Every change is pinned here so
    # this remains a deliberate standalone divergence from the adopted source.
    # WP1.6 repins app.js for Soft Inpainting. The page has carried the
    # checkbox and its six sliders all along and only the LEGACY collector
    # read them, so switching the feature on changed nothing and inpaint seams
    # came back with a visible 8px staircase -- the engine rounds the LATENT
    # mask unless this feature clears the flag. `_softInpaintGroup()` joins
    # `_hiresGroup()` and the rest, and the lifecycle body spreads it into the
    # inpaint group. Absent stays absent.
    # The same edit corrects three fallbacks in that group. `mask_blur`,
    # `padding` and `fill` all read `|| 0`, which contradicted the contract's
    # documented 4, 32 and 1 -- so a renamed element id would have sent no
    # feather and a flat fill rather than the defaults, silently. They use
    # `_num` with the contract's own value, which also stops `||` from
    # replacing a deliberate 0.
    "app.js": "0edd614e70dd04ec5bb9ecc11990880b624d360dc458863844d4fe632d6648e9",
    "module-system.js": "a35c269b2196fd4dbe55771b03ac82db396979f91c64e0bd694258145bd72bb2",
    "studio-docs.css": "6ec0d34cb89841f0c1e727803db63fe465f09be4c09f9c2167844db841fa1d4f",
    # BE10 repins it. `_saveDoc` writes `doc.paper` and `_loadDoc` restores
    # it, because the paper is the document's and every stage of that chain --
    # S, _saveDoc, _serialize, _deserialize, _loadDoc -- enumerates its fields
    # by hand. A field added at four of the five is silently dropped on reload.
    # Copied rather than referenced at both ends, or one document's surface
    # would follow the owner into another tab.
    # BE12 repins it. `_saveDoc` stops the airbrush timer, because snapshotting
    # a document is the last moment before it can be swapped out and a timer
    # that survived would write into the layer canvases of a document the owner
    # has left. One of the five exits the brief names.
    # BE19 repins it, and it is the SEAM rather than a cosmetic touch.
    # `_createBlankDoc` now sets `doc.paper` from the engine's exported
    # `DEFAULT_PAPER`. Without that line a new document has no `paper` field,
    # `_loadDoc` falls back to its own literal, and the state default in
    # canvas-core.js reaches NOTHING -- which is exactly how BE10's whole paper
    # package came to touch zero pixels. `_loadDoc`'s fallback deliberately
    # stays "none", so a document that PREDATES the field reopens looking
    # exactly as it did. The ruling was "ship it on", not "repaint what people
    # already made".
    "studio-docs.js": "4d7cbe72b2031ff3ebbe861324f5b88e849bb1d3e31d8b92553e49682c2a8473",
}

# The manifest moves with the app.js change above, the three toast.canvas.*
# strings added to locales/en.json, and studio-model-controls.js revealing
# #textEncoderRow when the lifecycle becomes available. No file was added or
# removed -- the 64/65 counts below are unchanged.
#
# P0.3e moves it again, and again only studio-model-controls.js: the adapter
# now restores the remembered dropdown selection and saves it back on change.
# Still no file added or removed. Repinned deliberately -- the point of this
# hash is that an UNINTENDED frontend edit fails, so an intended one has to
# say what it was.
#
# Moved once more by the live cold-launch rehearsal, same file: the restore
# latched "applied" before the page had finished rebuilding the dependent
# dropdowns, so the text encoder came back showing "None (bundled)" while the
# owner's real choice sat in the config file. It now verifies across polls,
# bounded, instead of trusting one pass.
# P0.4a moves it once more, for that one comment line in app.js. Counts
# unchanged at 64/65 -- no frontend file was added or removed.
#
# Moved again by the standalone Upscale dropdown repair above: index.html and
# app.js both, nothing else. Counts unchanged at 65/66.
#
# And once more by the registry REFRESH, which needed both frontend files:
# app.js re-reads on an event, and studio-model-controls.js -- the only thing
# in Studio that watches the engine come up -- is what raises it. index.html
# did not move, so `_SOURCE_LOADER_CSP_HASH` is deliberately unchanged.
# Counts unchanged at 65/66.
# Repinned for the variation-seed group: app.js gains `_variationGroup()` and
# sends it on the lifecycle payload; workflow-state.js gains the `resize_seed_h`
# registration its width counterpart had been missing. index.html untouched.
#
# Repinned for R1 Tier 3: the GPU Weights slider and the Auto-unload toggle (and
# the minutes row it reveals) are hidden. Markup only, above the inline
# <script>; CSP hash recomputed and confirmed unchanged.
#
# Repinned for R1 Tier 2: the VAE and text-encoder refresh buttons now POST
# /studio/refresh_models before re-reading, so they rescan instead of
# repopulating from cache. app.js only; index.html untouched.
#
# Repinned for the ControlNet and Hires-checkpoint hides: both are markup-only
# and both sit well above the inline <script> at index.html:1960, so
# _SOURCE_LOADER_CSP_HASH is unchanged -- recomputed from the live file and
# confirmed present in presentation.py, not assumed.
#
# Repinned again for the Live entry-point gate: app.js hides #liveToggleBtn when
# /studio/live/status declares `available: false`. index.html is untouched, so
# _SOURCE_LOADER_CSP_HASH in presentation.py still stands.
#
# Repinned for R1 Batch E: `module-system.js` gains an optional capability
# `probe` on register(), and gallery.js / workshop.js / lexicon.js each supply
# one. `module-system.js` is itself a CANONICAL pin, so it moves too. app.js and
# index.html are untouched, so `_SOURCE_LOADER_CSP_HASH` still stands.
#
# Repinned earlier for `locales/en.json`: the three owner-facing strings the R0
# frontend guards introduced (`firstRun.galleryUnavailable`,
# `firstRun.saveFailed`, `toast.defaults.failed`) existed only as inline JS
# fallbacks, so `en.json` -- the canonical key list the other locales
# translate from -- could not carry them. `app.js` did not change, so its pin
# above and `_SOURCE_LOADER_CSP_HASH` both stand; only the two manifests move.
#
# Repinned for the Gallery link-a-folder filter clear: `pickAndAddFolder` in
# gallery.js now empties `G.filter` before rescanning. Linking a folder while a
# tag or another folder was selected scanned the new folder, reported the count
# in a toast, and then displayed none of it, because `loadImagesReset` sends
# whatever filter is active and filters live in `G` alone -- reloading the page
# was the only way to see what had just been linked. gallery.js only; app.js and
# index.html are untouched, so `_SOURCE_LOADER_CSP_HASH` still stands, and the
# canonical/mounted counts hold at 65 / 66.
#
# Repinned for Gallery auto-sync: `connectSSE` now keeps the REASON the server
# sends with `watcher_status`, and `updateWatcherIndicator` shows it as the
# indicator's tooltip. The dot keeps its shipped meaning -- a filesystem
# observer is running -- so it does not turn green for Studio reporting its own
# generations; the sentence is what tells the owner which of the two they have.
# gallery.js only; app.js and index.html are untouched, so
# `_SOURCE_LOADER_CSP_HASH` still stands and the counts hold at 65 / 66.
#
# Repinned for architecture support. `check_model_te` reads the checkpoint
# header instead of answering a constant, so `restoreTextEncoderForModel` can
# do what the Extension's does: hide the Text Encoder row for a checkpoint that
# bundles its own CLIP. The two overrides that forced the row visible -- a
# lifecycle short-circuit in app.js and a 1500 ms re-reveal in
# studio-model-controls.js -- are gone, and a selection now needs only a
# checkpoint. app.js and studio-model-controls.js only; index.html is
# untouched, so `_SOURCE_LOADER_CSP_HASH` still stands and the counts hold at
# 65 / 66.
# Repinned for the GPU tile compositing control: index.html gains the row,
# app.js the handler, settings-page.js the deep-link target. The preference is
# `gpu_tile_compositing`, and the allow-list equality test is what makes those
# four edits one atomic unit -- a key without a control fails, and so does a
# control without a key.
# Repinned for the product-truthfulness hotfix: index.html hides four dead
# inpaint settings, canvas-core.js gives the eraser and clone stamps the
# owner's opacity instead of a flow value nothing assigns, and canvas-ui.js
# plus en.json drop the claim that each region ran as its own generation pass.
# Repinned for the Wildcards editor. lexicon.js only -- and every edit REMOVES
# something, because serving /tree un-gates the tab and the gate was the only
# thing keeping six unbacked controls off the screen. Export and Import
# (buttons and handlers), Duplicate (context menu), the "Aa" content-search
# toggle and its handler, and the Roll listener are gone; drag-to-move now
# says so instead of calling a route that is not there. `loadInfo` went too:
# it fetched /info for a count `_countFilesInTree` overwrites from the tree on
# the next render, so implementing it would have served a number nothing reads
# and omitting it would have 404'd on every activate.
# Repinned again after browser acceptance: the Roll button was still
# rendered with its listener removed, which is a dead control rather
# than a retired one. Found by opening a file in a real browser --
# it lives in the editor pane, so it is invisible until then.
# Repinned again, one line of app.js: the empty-mask comment cited
# `canvas-core.js:3245, :3263` for the `"null"` sentinel. The removal below
# shifted those lines AND deleted one of the two return sites, so the comment
# named two locations of which neither was right. It now cites the single
# remaining site. A citation this project relies on being exact is worth
# keeping exact.
# Repinned for the INPAINT_SKETCH removal. canvas-core.js only, and every
# edit REMOVES something. Owner ruled the mode intentionally superseded by
# Canvas painting (PROJECT_STATE.md, "Resolved scope decisions"), so the two
# residual branches came out: `exportMask`'s painted-layer mask path, and a
# flag in `applyMode` whose expression was constant-true because the flag
# could only ever be false. The state comment stops advertising a mode value
# nothing can set.
#
# No owner-facing behaviour moved. Both branches were UNREACHABLE -- the only
# assignments to that field set "Inpaint" -- so this is residual code, not a
# dead control. Regional compositing and the `"null"` empty-mask sentinel are
# asserted intact by `SupersededScopeTests`.
# Repinned for the blank-canvas guard, one line of app.js. The auto-route to
# txt2img now yields to a painted mask, matching the Extension's own guard at
# `studio_generation.py:2657` (`not _has_mask_data`). Before this, a near-white
# canvas with a painted mask was submitted as txt2img and the mask was dropped
# before admission ever saw it. The blank TEST was already exact parity
# (>= 249 per channel here, `px.min() > 248` there); only the guard was missing.
# Repinned for AR2.1 Canvas document identity. canvas-core.js gains an opaque
# `documentId` and a monotonic `canvasRevision`, bumped at exactly four sites
# (`saveUndo`, `saveStructuralUndo`, `undo`, `redo`); app.js captures the pair
# with the pixels and sends it on every operation. It exists because Studio
# QUEUES -- the owner can repaint between admission and execution, and nothing
# on the request said which Canvas state it captured. The Extension has no
# equivalent because its generate route consumes the payload in the same call.
# Repinned for AR4.3: Remember Last Session becomes SERVER-owned. app.js now
# reads the snapshot from `load_session`, writes through `save_session` with the
# revision it last saw, and deletes via `delete_session` when the owner turns
# the feature off. The legacy `localStorage` write is gone -- it is only read
# once, for migration, then removed, so there is exactly one canonical store.
# The session load is AWAITED after Saved Defaults, which makes the restore
# order deterministic instead of a race that repaints a moment later.
# Repinned for AR4.4: layered Canvas crash recovery. canvas-recovery.js is
# NEW, and index.html loads it before studio-docs.js because the document
# system waits on `StudioRecovery.whenResolved()` at boot. canvas-core.js
# publishes its id minter and a revision-change subscription, so recovery
# hangs off the four existing bump sites rather than inventing a trigger.
# studio-docs.js gives every TAB its own identity -- before this all tabs
# reported the same `document_id`, which per-document recovery cannot use --
# and adopts recovered documents in place of the blank one it used to mint.
# app.js gates session writes until recovery has resolved, which is the
# measured defect: the debounced write replaced the stored `document_id` with
# a fresh blank canvas about two seconds after every launch.
# Repinned for AR3.5. app.js extracts the generation presentation into
# `_beginGenerationPresentation` / `_endGenerationPresentation` and calls the
# setup from the LIFECYCLE branch, which every Studio install takes and which
# returned above it -- so the status word, the button label, the bar reset and
# the inter-pass elapsed timer were dead code on every install. Measured on the
# GPU: 41 seconds in which the bar read 100%, the button read "5 / 5" and the
# status bar read "Ready" while Auto Detail was still running.
# studio-model-controls.js announces the running job from the same `/api/queue`
# view that feeds the stage chip, so the words cannot disagree with the chip.
# Repinned for AR4.5. workflow-state.js marks its field loop as a batch and
# app.js's dimension handler yields to it, so a document switch performs ONE
# resize with the final pair instead of driving the canvas through
# NEW-width x OLD-height first. That intermediate resize resampled every layer:
# a crisp rectangle of exactly 20000 opaque pixels came back as 20400 after one
# switch, and the damage was saved back into the document, so it compounded.
# `_maybeResizeCanvas` takes over the six chrome operations the per-field
# handler used to perform, so suppressing it mid-batch loses nothing.
# The Extension has the identical defect; this divergence is deliberate.
# Repinned for AR5. The High Precision control is REMOVED: it promised, in
# four languages, to save float data that eliminates banding and gives Develop
# ~0.5 stops of headroom, and `grep -rn high_precision --include=*.py` matched
# NOTHING in the whole app, Neo included. A porting gap -- the Extension
# implements it at studio_api.py:1440 -- so the frontend arrived without its
# backend. `develop.js`'s sidecar reader is deliberately KEPT: it never read
# the toggle, and it is the half a future port builds against.
# Repinned for AR4.6. Canvas recovery now captures when an ACTION COMPLETES
# -- a stroke released, a fill, a result placed on the canvas -- instead of
# 2500 ms after the last revision bump. Owner's design, and it corrects a
# defect the debounce was hiding: `saveUndo` fires at POINTER-DOWN, so the
# revision edge sees the canvas BEFORE the stroke. canvas-core.js gains
# `onActionComplete`, fired at the end of `commitStroke` and on a revision
# bump only when no stroke is in progress. Measured: capture lands 65 ms after
# a stroke, against 2637 ms -- a window a person could reach, and did.
# Repinned for AR4.9: the output format setting now reaches the file. It was
# dead at EVERY layer -- assembled below the lifecycle `return`, absent from
# the contract and admission, and hardcoded PNG in three places in the writer.
# app.js sends an `output` group from INSIDE the lifecycle branch, and only
# for jpeg/webp so a default install stays byte-identical.
#
# Repinned for AR4.8: the finished image now REPLACES the live preview.
# `#canvasPreview` is one element shared by the progress thumbnail and the
# result, and the swap -- `_showResultPreview(0)`, commented "replaces live
# preview" -- sat below the lifecycle `return`, so the owner was left looking
# at the last mid-diffusion latent frame and called it what it looked like.
# The result also gains a real drag-out (DownloadURL + uri-list + moz-url),
# matching what gallery.js already does, so a dragged file carries the right
# name and type instead of a browser-synthesised copy.
#
# Repinned for AR4.7 and the tab notification. `_notifyTab` shipped with
# NOTHING calling it, in Studio and in the Extension alike -- a finished
# feature that never fired, and exactly what the owner reported ("the tab does
# not indicate when the image is done"). It is now bound to result DELIVERY,
# not to the end of a job, because a cancelled or failed job also ends and a
# tab flashing a tick for a failure would be the worse lie.
#
# Repinned for AR4.7. Four inpaint controls -- Area, Blur, Fill, Padding --
# lose their `hidden` attribute. They were hidden correctly at 9533093d, when
# their payload sat below the lifecycle `return` and never left the browser;
# WP1.4/WP1.6 made inpaint live and nobody un-hid them. D8 says an ABSENT
# service is hidden; this one is present. Proven on the GPU: mask blur moves
# 17.82% of the image, Area 5.31%, padding 4.59%.
# "Latent Noise" is REMOVED from Fill: measured on the GPU, it fails with
# IndexError while fills 0, 1 and 3 complete. WHY is not known. This comment
# used to assert a cause -- that Studio never populates `p.all_seeds` because
# it calls `process_images_inner` directly -- and that was WRONG:
# `process_images_inner` populates `all_seeds` itself at processing.py:913-925.
# Corrected at 8b2a4705 in the test and the review record; this third copy was
# missed then and is corrected now.
#
# Repinned for AR5.1: Batch Count and Batch Size are REMOVED. Two controls that
# reached nothing -- their only collector sat below the lifecycle `return`, and
# neither id existed in any contract. Not hidden but removed, because unlike
# 9533093d's inpaint controls the backend is not coming: `batch_count` is what
# the queue already does, with per-job cancel and reorder, and `batch_size`
# would need a multi-result contract Studio does not have. index.html, app.js,
# workflow-state.js, codex.js and four locales; the section becomes "Seed" and
# every seed control survives. The edits to index.html are markup only and
# outside the inline <script>, so `_SOURCE_LOADER_CSP_HASH` still stands --
# recomputed to confirm.
# Repinned for AR5.2, generation metadata. app.js `_outputGroup()` gains the
# Embed metadata toggle, and studio-model-controls.js stops hard-coding
# `infotext: ""` on delivery -- the server had been sending the resolved seed in
# `result.metadata` all along with nothing reading it. The seventh thing found
# below the lifecycle `return`, and the sharpest: AR4.9 lifted Format, Quality
# and Lossless out of that dead collector and left the toggle sitting in the
# same settings card. index.html is untouched, so `_SOURCE_LOADER_CSP_HASH`
# still stands -- confirmed by the s06 test that recomputes it from the live
# file. studio-model-controls.js carries no per-file pin, so it moves the two
# manifests only.
# Repinned for AR5.3, Gallery auto-sync. gallery.js only: a `sync` arriving
# while the detail lightbox is open used to be DISCARDED outright, so images
# that landed while the owner was looking at one never appeared until a manual
# scan. It is now held and applied when the lightbox closes, with bursts summed
# rather than replaced. The server half of the same fix -- the missing
# `note_generation()` caller on the live route -- is in presentation.py and
# moves no frontend hash. index.html is untouched, so
# `_SOURCE_LOADER_CSP_HASH` still stands, confirmed by the s06 test that
# recomputes it. gallery.js carries no per-file pin, so this moves the two
# manifests only.
# Repinned for AR6.1, invented limits. index.html only: `data-max="150"` is
# removed from paramSteps and paramHrSteps, because the backend cap they were
# advertising against -- MAX_STEPS = 40, which gated base, Hires and Auto
# Detail steps from one unexplained constant -- is gone. Owner ruling: "we
# should have no restrictions like that." The alignment refusal went with it,
# after the owner verified in the core that 500x500 simply yields 496x496.
# Markup only and outside the inline <script>, so `_SOURCE_LOADER_CSP_HASH`
# still stands -- confirmed by the s06 test that recomputes it.
# Repinned for AR6.2, the seed. app.js only. A seed above 4294967295 fell
# through `_resolveSubmittedSeed`'s guard into the RANDOM branch, so the owner
# got a different image and no message -- the exact thing the comment ten lines
# above it says Studio must not do. The ceiling is gone, and a typed 0 in the
# variation-seed field no longer becomes "random": `|| -1` treated a legal seed
# as falsy, the third instance of that defect here after `_num`'s or/?? and
# `int(quality or 92)`. Both fields now read through one `_seedFieldValue`.
# Repinned for AR6.4, control-vs-admission ranges. index.html only, and only
# ONE control moves: `#paramSoftContrast` gains min="1". That is the single
# genuine constraint in the set -- `inpaint_detail_preservation` is used as
# `pow_(1 / value)` at soft_inpainting.py:81, so 0 divides by zero. The other
# three mismatches were fixed by WIDENING admission instead, because narrowing
# a control takes away range the owner had, which is the thing being fixed.
# Repinned for AR6.5. canvas-ui.js only: the region panel's `regionAdd`
# handler loses `if (S.regions.length >= 8) return;` -- a bare early return
# with no constant, no message and no server-side equivalent, so the ninth
# click on an enabled button did nothing and said nothing. index.html is
# untouched, so `_SOURCE_LOADER_CSP_HASH` still stands.
# Repinned for AR6.7. app.js only. The LoRA stack compiled `<lora:name:weight>`
# tags and trigger words into a prompt that was never sent -- `compilePrompt`
# lived below the lifecycle `return`, and the live branch sent the raw textarea
# value. So an owner could browse LoRAs, add them, weight them, watch a live
# preview of the compiled prompt, and have none of it reach the engine. The
# "Tidy prompt on generate" toggle was dead for the same reason and in the same
# place. Both definitions are now above the branch AND above every helper that
# uses them, so one definition serves both paths.
# Repinned for AR6.8, the Auto Detail LoRA stack. app.js gains
# `_adPromptWithLoras`, which appends a slot's compiled `<lora:...>` tags to
# that slot's prompt; ad-lora-stack.js loses two caps that each claimed a
# backend enforced them, and the header claim that "the backend performs its
# own validated compilation" -- there was no `loras` field in any contract, and
# the only sends were below the lifecycle `return` and to a route Studio does
# not serve. `<lora:...>` is parsed out of prompt text; there is no other
# transport, so the prompt is where it has to go.
# Repinned for AR6.9, the last of the invented ceilings. index.html only:
# `paramHrScale` loses data-max="4" (Forge's slider stop, which the note beside
# the constant already argued against in the same breath as the 2.0 it
# replaced), and `paramWidth`/`paramHeight` drop data-min from 64 to 8 -- the
# VAE factor, and the real floor, where admission had always accepted any
# positive integer and the CONTROL was the narrower of the two.
# Repinned for AR7.2, the standalone Upscale tool. index.html only: the
# "Refine after upscale" row gains `hidden`, and the two rows gated on it go
# with it. The upscaler, the scale and UPSCALE CANVAS stay visible, because
# those now WORK -- the route they POST to has existed only since this commit.
# Built rather than hidden, unlike the Hires checkpoint control, because a pure
# ESRGAN pass needs no resident checkpoint and is the one large-image path a
# 6 GB card can afford.
# Repinned for AR8.1, the LoRA root. index.html gains a fifth model-folder row
# and studio-dir-picker.js gains "lora" in its ROLES list -- which a guard
# asserts must equal `catalogue.MODEL_ROLES`, because the two drifting is how a
# root becomes configurable on one side and invisible on the other. A LoRA is
# named in the prompt and applied per generation, so like the detector folder
# it joins MODEL_ROLES and not RESIDENT_MODEL_ROLES.
# Repinned for the LoRA folder save fix. studio-dir-picker.js only:
# `state.roots` was a FOURTH literal spelling of the role set, beside ROLES,
# ROLE_SUFFIX and the markup. Adding `lora` to the first two and missing this
# one made `addRoot("lora", path)` reach `.indexOf` on undefined, throw, and
# lose the owner's typed folder with no message. It is derived from ROLES now,
# so a role that exists has a slot by construction. Reported from real use --
# the suite had a guard for the markup and none for the save path.
# Repinned for AR8.3, three defects a browser found and 4,318 passing tests did
# not. Two frontend files change.
#
# app.js: `loadSelectedModelComponents` now waits for the lifecycle host to
# answer before choosing between the lifecycle contract and the legacy
# POST /studio/load_model. That flag is affirmed only after the first
# /api/model/state reply, and BOTH boot paths beat it -- the 250 ms component
# restore timer, and the `change` event `restorePreference` dispatches on
# #paramModel on purpose. So a working product raised a red "Model load failed"
# toast on every launch, on 3 of 3 fresh loads. The wait went into the single
# funnel because fixing one caller measurably left the other one firing.
# app.js also gains `lora` and `adetailer` in MODEL_ROOT_FIELDS, the only thing
# that writes #modelRootStatus* or fills #modelRoot* from the server. Both rows
# therefore reported "Not configured" forever; for LoRA that was a plain
# untruth, with the server reporting status "ready", entry_count 73 and the
# picker rendering all 73. `lora` was the FIFTH literal spelling of the role
# set after `1f8d28f3` fixed the fourth, and `adetailer` the sixth -- found by
# the guard written for the fifth rather than by another owner report.
#
# canvas-core.js: `stab()` clamps its window to at least 1. The Smoothing
# control offers 0 (data-min="0"), and at 0 the window was 0, the average loop
# never ran, and the return was 0 / 0 -- NaN for x, y and pressure. Every stamp
# after the opening dab landed nowhere: a dense 61-sample stroke painted 3,740
# pixels instead of 30,990. Inherited from the shipping extension, which has
# the identical expression and the identical data-min, and diverged from
# deliberately: a control whose minimum silently disables the tool is the
# clearest possible violation of "make every visible control truthful".
#
# Repinned again for the stale locale string. locales/en.json only:
# settings.modelFolders.saveDesc said "All three folders are saved
# together" while the card lists FIVE, and i18n.js overwrites the markup
# fallback whenever the key resolves, so the corrected sentence already in
# index.html never reached the screen. Verified in the live DOM before and
# after. The count in that sentence has been wrong since the fourth root
# was added; it is now countless, so it cannot go stale again.
# Behaviour was validated in a live browser BEFORE these pins moved, which is
# the order §13 requires. Evidence/ar8.3-browser-baseline/.
# Repinned for NG-2, Clip Skip. index.html gains #paramClipSkip -- a
# control the PNG-import path in app.js has targeted since before it
# existed, so that path wrote to nothing. Its own row rather than a fourth
# column, so the three sampling controls keep their width and app.css
# needs no new grid class. Range 1..12 default 2 are the ENGINE's, read
# off modules/shared_options.py rather than invented.
# app.js gains _clipSkipValue() and spreads clip_skip into the LIVE
# jobParams, above the lifecycle return -- asserted by a test, because a
# generation field below that return reaches nothing and nine already
# have. Omitted at the engine default, so an owner who never touches it
# sends exactly what they sent before.
# Repinned for AR8.9, two brush defects a sweep found and a browser
# confirmed. canvas-ui.js: the "increase brush size" key was
# Math.min(100, ...) while the Size control offers 500, so a brush set to
# 300 jumped DOWN to 100 on the first press -- measured live, brushPx
# 3991 -> 768. The bound is now read from the control and Math.max
# guarantees the direction. canvas-core.js: cloneStamp applied its
# softness falloff to a 1x1 stamp, whose only pixel sits 0.707 from a
# centre with radius 0.5, so the falloff evaluated outside the dab and
# multiplied it by zero -- Clone Stamp painted NOTHING at minimum Size
# with Hardness under 100%. Measured after: 212 px, identical to the
# full-hardness control. Neither file changes any other tool.
# Repinned for AR8.12, a DATA-LOSS fix. "Save model folders" posted only the
# non-empty text boxes, and the route it posts to replaces every role at once
# with no merge -- so a role whose box was empty was deleted from the live
# registry AND rewritten out of studio-config.json. studio-dir-picker clears
# that box after "Add folder", so add-a-folder-then-Save destroyed the folder
# just added. Save now posts the COMPLETE mapping: the server's configured
# roots, plus anything typed, deduplicated. Blank means "nothing to add";
# removing a root stays explicit through the per-root Remove in the list.
# _readModelRoots also stops filling the box from document_.roots[role],
# which is an ARRAY -- a two-folder role rendered as "C:/a,C:/b" and the save
# posted that as one path. index.html and en.json move together because the
# placeholder promised exactly the behaviour that was losing folders
# ("Leave blank to leave this folder unconfigured"), and the locale wins over
# the markup. Verified live: four configured roots survive a Save with every
# box empty, which under the old code deleted all four.
# Repinned for the Size label. canvas-ui.js only: the Size scrub now shows
# the EFFECTIVE PIXEL WIDTH beside the slider number -- "Size: 100 (768 px)"
# on a 768 document, where 768 is the whole short side. The slider was never
# pixels: brushPx runs it through a power curve, so 100 already covers the
# canvas and the top four fifths of the travel LOOKS inert while it is still
# changing falloff geometry. Showing the number makes the control truthful
# without removing range (which a standing ruling forbids) and without
# changing what any saved brush size means.
# The figure follows the DOCUMENT: resizeCanvas bumps no revision and fires
# no action-complete, and eight call sites resize, so the bar re-checks on
# repaint instead of subscribing to something that does not exist. Measured:
# 768 -> 69 px, 384 -> 34 px, 1024 -> 92 px at slider 20.
# Repinned once, because these now fingerprint NORMALISED content rather

# than the checkout's line endings. Every value below changed for that

# reason alone and for no behaviour reason -- except app.css, which was

# already stored LF on disk and is therefore unchanged, which is itself a

# neat demonstration of the inconsistency this removes.

# BE2 repins it for the Pixel sizing contract. canvas-core.js and canvas-ui.js
# only; no file was added or removed, so the count below is untouched.
#
# Brush Size is RELATIVE to the document's short side through a 1.5-power curve,
# which is right for ordinary brushes and fatal for a pixel brush: the same
# Pixel preset was one document pixel at 512 square and four at 24 MP, and
# Sizes 1-4 collapsed onto the same mark. `brushPx()` gains a second mode where
# Size is a literal document-pixel diameter, and ONLY the Pixel preset declares
# it. The ratified curve is unchanged for everything else and pinned by value in
# test_be2_pixel_sizing.
#
# The `Math.max(2, ...)` stamp floor moves to 1 in the same commit -- not
# because it was the defect, which BE0 disproved by measuring a one-pixel mark
# with the floor in place, but because in literal mode `sz` IS the owner's
# requested diameter and silently doubling a request for 1 makes the mode a lie.
# Ordinary output is byte-identical across that change.
#
# canvas-ui.js carries the mode with the per-tool size memory. A restored size
# without its mode is a different brush: a 5 that meant five pixels coming back
# as a 5 that means eighty.
# BE3 repins it again. canvas-core.js only: dab placement stops depending on
# how often the browser reports the pointer.
#
# `const steps = Math.max(1, Math.ceil(dist / sp))` guaranteed a dab for EVERY
# event however short, and `t = i / steps` then spread those dabs evenly across
# whatever segment arrived -- so the real gap was `dist / steps`, never the
# requested spacing except by coincidence, and the sub-spacing remainder was
# discarded on every event. A 240 Hz pointer laid roughly four times the dabs of
# a 60 Hz one along the same line.
#
# It replaced with a carried DEBT: how much further the stroke must travel
# before the next dab is due, priced at that dab's own pressure. Blender does
# the same in `paint_stroke.cc`. Measured: byte-identical coverage at 1x, 2x,
# 4x, 8x, 17x and 40x the event rate along a fixed polyline.
#
# ONE of five loops changed. smudgeStroke and dodgeBurnStroke are CT10;
# regionPaintMove and regionEraseMove are Regional Prompting. An inventory
# guard now fails if a sixth appears without a named owner.
# BE4 repins it. canvas-core.js only: a selection now BOUNDS a stroke instead
# of metering it.
#
# The weight was multiplied into every dab before accumulation, at all three
# coverage branches. Harmless on a max-blended hard tip, where 50% stays 50%.
# Wrong on an accumulating soft tip, where multiplying before accumulation
# turns an AMOUNT into a RATE -- measured 102/102/102 at 100/50/25 percent,
# the selection reaching nothing at all because the flow ceiling clamped first,
# and 92 at three dabs against 102 at sixty.
#
# Applied once in `alphaMapToImageData`, which already walks only the stroke's
# dirty rectangle, already reads accumulated coverage and already writes output
# alpha -- and which also feeds the live preview, so what the owner sees while
# painting is what lands. `drawGradient` has done it in this exact shape for
# far longer; BE4 restores a pattern the file already used.
#
# Now 102/51/26, and 51 whether the stroke is three dabs or sixty.
#
# CT3d's comment justifying the per-dab approach is corrected in the same
# commit. Source that argues the corrected behaviour is wrong is worse than no
# comment at all.
# BE5 repins it. canvas-core.js only: two hardness cliffs removed, and the flow
# ceiling with them.
#
# `dabAlpha` had two curves either side of hardness 0.01 -- a smoothstep peaking
# at 1.0 above, and a hand-written ramp peaking at 0.4 below. `spacingFraction`
# had a cliff at the SAME threshold, snapping to a flat 0.10. So one hundredth
# of a step on the Hardness control changed core strength two-and-a-half-fold
# and dab count four-fold, with nothing on the control to say so. The research
# went looking for the "Krita Airbrush_Soft" curve the comment credited and did
# not find it in Krita; it was invented here.
#
# The two defects turned out to be one. `stampAlphaMap` FORCED accumulation for
# soft tips with a flow-level ceiling to stop it running away, and the comment
# justifying that said a max-blend "would cap a soft brush at 40% grey" -- true,
# and true only because of the 0.4 curve. With the core reaching full strength a
# max-blended soft dab caps at FLOW, so the ceiling had nothing left to do and
# Buildup became the only accumulation switch. Each of the three quantities now
# means exactly one thing.
#
# Measured across hardness 0, .001, .005, .009, .010, .011, .1, .5, 1: spacing,
# emitted gap, core alpha, edge profile and stroke coverage all continuous, with
# no step at the old threshold.
# BE6 repins it. canvas-core.js only: four tip families stop having four
# different opinions about which controls exist.
#
# shapeDistRound took no angle and folded for spikes AFTER applying ratio, then
# took an isotropic norm -- and rotation preserves an isotropic norm, so Spikes
# was not unwired, it was algebraically incapable of changing a pixel.
# shapeDistFlat hardcoded ry = r*0.3 and shapeDistMarker 0.8/0.35, so brushRatio
# reached neither. Scatter called none of them: a plain circle inline, with no
# density test, so the one preset whose character IS stipple density could not
# see the Density control.
#
# Replaced by ONE tip-local frame -- rotate, fold, THEN anisotropy, then the
# tip's own norm -- and ONE coverage loop that scatter shares. Each tip declares
# only TIP_ASPECT, TIP_EXTENT and TIP_NORM.
#
# Measured: Ratio went from reaching one tip of four to all four; Density from
# three to four; Spikes from none to all four; Angle from two to all four.
# Angle and Spikes are declared "needs-shape" on round and scatter and measured
# at Ratio 0.4, because a circle has no orientation and no implementation can
# give it one.
#
# All ten presets are BYTE-IDENTICAL at neutral control values, which is the
# constraint the brief sets and the one a geometry refactor is most likely to
# break quietly. The first version of TIP_ASPECT was not: it defined aspect as
# a fraction of half-WIDTH and narrowed Bold Marker from 0.35r to 0.28r.
# BE7 repins it. canvas-core.js only: per-dab direction, anisotropic spacing,
# and Density that stops multiplying with Spacing.
#
# Three defects lived in one line -- `if (Math.hypot(dx, dy) > 2)`. A segment of
# two pixels or less did not update the heading at all, so a slowly drawn curve
# kept whatever direction it had. The update ran once per EVENT while plotTo may
# emit many dabs per segment. And the coefficient was a fixed 0.3 per event, so
# the filter's strength depended on the browser's report rate -- BE3's defect,
# living one line above it.
#
# The obvious repair -- a per-DISTANCE coefficient -- was written, measured and
# REJECTED. BE7 also makes the gap depend on the tip's extent along travel, so
# the gap depends on the heading and the heading advances once per gap: a
# feedback loop that left the same quarter turn ending at -59 degrees when
# reported 20 times and -22.7 when reported 240, against a true tangent of -90.
# A first-order filter also lags a turn by about tau x turn-rate, roughly 29
# degrees for a one-dab-width tau -- a chisel held 29 degrees off its own
# direction through every curve is the complaint that opened this programme.
#
# So the heading is the path's TANGENT: a property of the path, not the event
# stream. Measured spread across 1x/2x/5x/12x report rates is 1.3 degrees, and
# the residual is the polyline's own chord error.
#
# Density is normalised against spacing. The skip is per pixel per dab and dabs
# overlap, so coverage is the UNION over every dab touching a pixel: at Density
# 0.35 it ran 0.698 at spacing 0.32 up to 1.000 at 0.02 -- at close spacing the
# control did nothing at all. Spread now 0.071.
# BE8 repins it. canvas-core.js only: the display stops doing full-canvas work
# on every pointer move, and the stamp regression BE1-BE7 introduced is undone.
#
# tipDistance recomputed two trig calls, four table lookups and two divisions
# for EVERY PIXEL of every dab -- and a 170px dab is about 22,000 pixels. It
# cost a measured 4.8x in stamping (154.9ms -> 750.5ms at 4096 square). tipFrame
# resolves all of it once per dab and the per-pixel path is now two multiplies,
# two more and a square root. Back to 158.7ms, measured the same way.
#
# _compositeCache has ONE consumer, the dirty-rect fast path, and that path is
# gated on !imagePreviewActive -- because when the WebGL preview owns the
# display, which is the SHIPPING DEFAULT, a Canvas2D snapshot is not what the
# owner is looking at. The cache was built anyway, every move, with a
# full-canvas getImageData. That is also why BE0 measured the "fast path" as
# barely faster than the full one: on the default path it was never reached.
#
# The Canvas2D path is kept and fixed rather than abandoned. It restored the
# ENTIRE cache and drew the ENTIRE stroke canvas every frame; both are now
# scoped to a per-FRAME dirty rect. putImageData ignores the transform and needs
# DEVICE coordinates; drawImage respects it and needs DOCUMENT ones.
#
# NO overall speed claim. Repeated runs in one page state reproduce to 0.5%, but
# the same configuration swings 4x ACROSS page states -- 28ms as the first
# benchmark after a load, 118ms as the fourth. The harness now records a
# pageStateOrdinal so an invalid comparison cannot look valid.
# BE9 repins it. canvas-core.js and canvas-ui.js: the stabiliser stops
# depending on the browser's report rate, and strokes reach the pointer.
#
# The window was counted in SAMPLES -- `Math.min(S.smoothing, pts.length)` --
# so it covered a quarter of the arc at 240 Hz that it covered at 60. BE3 FOUND
# this and could not fix it: its tests call plotTo directly and bypass the
# stabiliser, so the defect was structurally invisible to them. Through the real
# pointer pipeline it was LARGER than the placement defect BE3 did fix -- at one
# event per segment a stroke covered 7,173 pixels where twenty covered 14,096.
#
# The window is now an ARC LENGTH in SCREEN pixels, and the mean is weighted by
# the path length each sample represents rather than by how many arrived.
# Measured: 0px spread across 1x/3x/8x/20x/50x sampling at every smoothing
# level, and an identical 18px screen lag at 0.25x, 1x and 4x zoom.
#
# Position and pressure now have separate windows. Position jitter is a tremor
# an owner wants removed; pressure lag is felt as a brush that will not respond.
# One window traded one for the other.
#
# finishStroke walks the remainder at pointer-up. A lagging filter ends the mark
# behind the lifted pen -- measured 37 document pixels in the headless fixture,
# 14 through the browser at Smoothing 6. It is NOT an unconditional extra dab:
# it calls plotTo, so BE3's spacing debt still decides, and a stroke already at
# the pointer returns false and paints nothing.
#
# _sampleMean is kept with no caller, deliberately: it is the mutation harness's
# target. A mutation that invents its own replacement tests the mutation rather
# than the code.
# Repinned again for the mechanical deletion of makeStamp + _stampCache.
# 1,811 bytes of dead code with no caller since CT3d -- the addendum required
# the removal be its own change with its own caller search, and it is.
# BE10 repins it: the document gets a paper, and marks land on it.
#
# Everything from BE1 to BE9 changed the SHAPE of a mark. None of them changed
# what the mark lands ON, which is why a stroke still laid down a smooth field
# of colour with a smooth edge -- there was nothing under it.
#
# Four procedural height maps, GENERATED rather than shipped. That is not a
# shortcut around sourcing assets: it means no download, no third-party licence
# to track, and -- the requirement that forced it -- NO FILESYSTEM PATH
# anywhere in document or session state. A paper is a name and two numbers.
#
# APPLIED ONCE, AT MERGE, beside the selection BE4 put there. Not per dab,
# which is what Krita does and what Studio must not: coverage accumulates here,
# so a per-dab multiply turns the reveal from an amount into a rate and the
# texture washes out exactly where the owner pressed hardest. Measured: 1, 4
# and 16 scrubbing passes move the texture by 0.01 of an alpha step.
#
# The curve is Krita's soft-texturing MULTIPLY written out --
# `1 - strength*(1 - height)` -- chosen over the plain multiply because it is
# EXACTLY 1 at strength 0. Four different ways of saying "no paper" all produce
# a byte-identical mark to the unpapered one.
#
# Depth is SIGNED and the sign means something: positive reveals the peaks (a
# pencil on rough paper), negative the valleys (a wash settling). Reveal
# correlation between the two: -1.0000.
#
# Indexed by DOCUMENT coordinate, so two strokes crossing a point find the same
# fibres (1.0000 agreement over 2,914 solidly covered pixels) and zoom moves
# nothing (byte-identical at 0.25x, 1x, 4x).
#
# Per-preset Tooth, written unconditionally for all ten. Pixel is 0 and that is
# BE2's contract, not a taste.
#
# The hash moved once more before it was committed, and for a reason worth
# recording: the CONTACT SHEET found a defect none of the numbers could. Canvas
# and Laid were a clean product of sine ridges, which every measurement scored
# as textured, distinct and correctly seamed -- and which rendered at a 60px
# brush as a printed halftone grid and a barcode. Displacing the ridge
# coordinate by a low-frequency noise gives the threads the irregularity that
# reads as woven. The numbers barely moved (canvas roughness 63.7 -> 60.5); the
# images changed completely.
# BE11 repins it: presets that RESPOND, not just presets that differ.
#
# A small contract -- { input, target, curve, min, max, fallback } -- mapping
# pressure, speed, direction and tilt through a named analytic curve onto size,
# flow, angle or ratio. Nine of the ten shipped presets declare rules; the
# tenth is Pixel and declares none, which is BE2's contract.
#
# THE PART THAT MATTERS IS THE FALLBACK. CT2 has reported `pressureAvailable`
# on every sample since it landed, and wrote it for exactly this case: a mouse
# reports a perfectly well-formed 0.5 while a button is down, so a curve that
# read the VALUE instead of the FLAG would draw every mouse stroke as though
# the owner pressed exactly half way. Every rule declares its own fallback
# INPUT; measured at three, the same curve gives means of 55.89, 145.36 and
# 235.66, and the last is byte-identical to no curve at all.
#
# Every fallback in the SHIPPED table maps to that preset's pre-BE11
# behaviour, so a mouse paints exactly what it painted before. That is a
# curation decision about the table, not a property of the mechanism, and it
# has its own guard.
#
# `opacity` and `grain` are named in the brief and refused here: opacity is
# applied once at commit (BE5) and grain once at merge (BE10), and a per-dab
# curve on either would undo the package that put it there.
#
# One line in canvas-ui.js carries the timestamp and the pen flags from the
# pointer seam into the engine. CT2 has produced both since it landed and
# nothing had ever asked for them.
# BE12 repins it: the Airbrush preset earns its name.
#
# It has been called Airbrush since before this programme started and the name
# was a claim the engine could not support. An airbrush deposits paint WHILE IT
# IS HELD OVER A SPOT; Studio deposited only when the pointer moved, because
# `plotTo` is the only thing that stamps and only a pointermove calls it.
#
# THE RATE MUST NOT DEPEND ON THE CALLBACK, for the third time in this
# programme. BE3 removed that defect from spacing, BE9 from smoothing, and here
# it is again wearing a `setInterval` -- which is a request, not a promise. So
# time carries a DEBT exactly as distance does in `plotTo`. Measured: 400ms
# delivered in 1, 4, 20 and 100 callbacks lays the same dabs and the same
# paint, spread 0. Doubling the time doubles the dabs: 5 / 11 / 23 / 47.
#
# STATIONARY ONLY, and that is a decision. A moving pointer already deposits
# through BE3's spacing debt; adding time-driven dabs on top would make a slow
# drag darker for two independent reasons at once, which is why Krita and
# Photoshop both need a rate control to manage the interaction.
#
# A STALE CALLBACK CANNOT WRITE ANYTHING. Clearing the interval is necessary
# and not sufficient -- a callback can already be queued when `clearInterval`
# runs -- so every tick re-checks a token captured at stroke start. Verified
# after commit, after abort, after an explicit stop and after a new stroke has
# begun.
#
# `abortStroke` covers TWO of the five exits in one line, because CT2 already
# routes both `pointercancel` and window `blur` there.
#
# The clock is injectable and the tick reads nothing else, because a timer
# tested against the real clock is a timer tested against the machine's mood.
# BE13 repins it: the Pixel brush becomes a pixel brush.
#
# BE2 gave it a literal one-pixel width and said in the preset's own comment
# what it was NOT doing -- "aliased coverage and cell-centre placement are BE13
# and are not claimed here". So it was an ANTIALIASED one-pixel brush, which is
# what the owner meant by "just a small thin brush".
#
# THREE THINGS, AND THEY ARE SEPARATE FEATURES.
#
# ALIASED COVERAGE is a method on the ordinary dab pipeline, not a second
# engine: the loop already computed `nd < 1` and already exited on it, so
# aliased is that test without the falloff. The eraser gets it for free.
# Measured: one alpha level against 128.
#
# THE CELL WALK belongs to `aliased`, not to Pixel Perfect, and the first
# version of this package had them as one question. That was wrong twice over:
# the "unfiltered" baseline could not exhibit the defect, and arc-length
# spacing on a diagonal lands at cells that SKIP -- a fast diagonal drag left
# gaps. Measured 12 empty columns before, 0 after.
#
# PIXEL PERFECT defers rather than retracts. `S.stroke.alphaMap` is an
# accumulator with no undo of its own, so unpainting a cell erases whatever an
# earlier part of the same stroke put there. The filter holds one cell
# tentative and writes it only once the next proves it is not a corner.
# Measured on a slow hand: 25 doubled columns -> 0, and exactly 25 cells
# removed, no more.
#
# CELL-CENTRE PLACEMENT flips an expectedFailure BE2 left with this package's
# name on it. A pixel's integer index IS its centre, so an odd diameter is
# symmetric about an integer and an even one about a half-integer. Sizes
# 1/2/3/4/5/8 now paint 1/2/3/4/5/8. It also flipped a SECOND expectedFailure
# in BE1 whose stated reason was wrong -- it blamed the relative size curve, on
# a probe that uses literal pixels.
# BE14 repins it: the preset set rebuilt from ten to sixteen, and the leak that
# kept it flat closed.
#
# THE FINDING. BE6 made Ratio, Spikes, Density, Angle and Falloff work on every
# tip and verified it by rendering -- and not one shipped preset set any of
# them except Scatter Dust's density. That was not two coincidences:
# `applyBrushPreset` wrote nine of the fifteen supported fields and skipped
# exactly those six, so they LEAKED between presets and no preset dared use
# them. BE1's own harness worked around it, clearing "the six leaking fields"
# by hand with a comment saying the harness must not inherit the bug it is
# measuring.
#
# All fifteen are written unconditionally now, and the set spends them: Bristle
# Rake is the first shipped use of Spikes, Calligraphy the first of Angle with
# Follow stroke OFF, Ink Wash the first of Taper in, Charcoal and Pastel the
# first of Density with a gaussian falloff.
#
# SIX NEW PRESETS -- Fine Liner, Ink Wash, Calligraphy, Charcoal, Pastel,
# Bristle Rake -- and four renames, each carrying an ALIAS. A preset name is a
# KEY: the per-tool memory and any recovered document may carry it, and
# `applyBrushPreset` returns false on an unknown name and changes NOTHING, so a
# rename without an alias makes an owner's saved brush stop applying quietly
# rather than loudly.
#
# NEAREST NEIGHBOUR IS THE TEST, because the complaint was that the presets
# felt ALIKE and that is a statement about neighbours. Every preset is compared
# to its most similar sibling on painted area, mean alpha, width and the
# standard deviation of alpha. Closest pair: 0.2057.
#
# MASK HARD, MASK SOFT AND SMUDGE STILL DO NOT SHIP, for the reasons CT3 gave
# and BE10 reinforced.
# Repinned for a defect the BE15 acceptance journey found, which no package's
# own tests could have.
#
# `_saveToolSettings` saved fourteen brush fields and none of the five added
# between BE10 and BE13: brushGrain, brushCurves, brushAirbrush, brushAliased,
# brushPixelPerfect. Switch Brush -> Eraser -> Brush, or reload, and Size,
# Opacity, Flow, Hardness, Smoothing, Angle, Taper, Ratio, Spikes, Falloff and
# Density all come back while the tooth, the curves and the pixel methods do
# not. The brush is a different brush and nothing says so.
#
# Each of BE10 through BE13 tested its field through `applyBrushPreset`,
# through the document chain, or through the engine. All four missed the same
# FIFTH place brush state lives, because none of them had a reason to visit it.
#
# The guard is written to catch the NEXT field rather than these five: it
# derives the `brush*` fields from the state block itself and requires every
# one to be either in the travelling contract or in an exclusion list with a
# stated reason.
# BE16 repins it. canvas-core.js AND canvas-ui.js: the first dab of a stroke
# stops being oriented by the PREVIOUS stroke's direction.
#
# The owner reported it from a picture -- "this is a flat brush, see how the
# first press doesn't match the angle/rotation of the drag?" -- and the cause is
# that `beginStroke` lays dab number one before any direction exists, and took
# its angle from `_saSmooth`, a module global holding the heading the last
# stroke ended on. Measured by intersection-over-union against the dab that
# should have been there: Flat Chisel 0.31, Marker 0.45, Bristle Rake 0.39.
#
# BE7 diagnosed this in a comment, added `_headingKnown = false` sixteen lines
# AFTER the dab it was meant to protect, and never read the flag anywhere. The
# fix gives it a reader and DEFERS the opening dab until a real tangent exists;
# a stroke that never moves flushes it at the end, because a tap must still
# paint. Repainting was not available: the alpha map is an accumulator with no
# undo, so correcting a dab means writing 0 and erasing whatever else landed.
#
# canvas-ui.js is in this repin because the cursor read the same stale global,
# ignored the owner's Angle, and -- for the marker -- was still hardcoded to the
# 0.4 radians CT3 removed from the stamp and left behind here.
#
# TWO MORE MECHANISMS, found by the BE17 audit after this package had shipped
# its own measurements, both inside its own subject:
#
#   Rotation jitter was applied TWICE -- `plotTo` and again in `stampWet` -- so
#   a body dab turned twice as far as the label promised while the opening dab,
#   which reaches `stampWet` directly, turned once. `spacingFor` also priced the
#   gap from one dab's random draw, so an ORIENTATION control was changing dab
#   density. `stampWet`'s application survives, because every dab reaches it.
#
#   `dabIgnoresRotation` held the press dab back whenever Spikes > 2 -- 221
#   inked pixels at press down to zero on twelve presets -- for a spike fold
#   that is a rotation about the origin and therefore cannot change a pixel of
#   a circular tip, which BE6's own guard already asserts.
#
# Measuring that the mechanism you fixed now works is not the same as measuring
# that the complaint is gone. That is the lesson the whole BE programme is
# paying for.
# BE18 repins it. canvas-core.js only: a hard tip keeps one pixel of
# antialiasing instead of painting a binary staircase.
#
# At hardness 1.0 the falloff's inner radius equalled its outer one, so every
# pixel was 0 or full. Measured against an 8x supersampled render, edge RMS was
# 0.29-0.42px on Hard Ink, Fine Liner, Marker and Calligraphy -- against 0.301px
# for PIXEL PERFECT, the preset that declares itself aliased. Those four were
# indistinguishable from the pixel brush on their silhouette, and so was the
# default state before any preset is chosen.
#
# OWNER RULING: fix it, accepting the divergence from the shipping Extension,
# which has the same behaviour. Asked as an explicit choice.
#
# The floor is in PIXELS and capped as a FRACTION. A hardness-based floor
# collapses on a small tip; a flat one-pixel band eats one, taking 33.8% of the
# ink at Size 3 for the same footprint. Mask mode is exempt: `exportMask`
# binarises at alpha > 0, so a rim would widen every inpaint mask by a pixel
# for a softness the binarisation discards.
# BE19 repins it. canvas-core.js and studio-docs.js: the document ships with a
# paper surface, at Depth 0.20 on the `fine` texture.
#
# BE10 built the paper system and it reached ZERO PIXELS -- three neutral gates
# in series and a controls panel that ships `display:none`. Measured at the new
# default: Pencil 71.3% of painted pixels visibly grained, Charcoal 31.2%,
# Pastel 18.1%, while Basic Round and Soft Round stay at 0.0% and Hard Ink and
# Pixel Perfect are byte-identical at ANY depth because they declare no Tooth.
#
# The depth was chosen twice. The first sweep did not reset the seeded RNG
# between the grain-off and grain-on runs, so Charcoal's Density-0.95 stipple
# diverged and reported 72% at Depth 0.10 -- almost all of it RNG rather than
# grain. Reseeded, 0.10 reaches 9.1%, which is BE10's problem again.
# V2-01b: `v2/brush-contracts.js` ADDED, then V2-02: `v2/input.js` ADDED. Both
# manifests move because a file appeared, not because one changed -- no existing
# frontend byte differs, and the per-file table above is untouched for exactly
# that reason.
# U1: `v2/coverage.js` and `v2/scratchpad.js` CHANGED -- the first V2 repin
# caused by an edit rather than an addition. The file COUNT is unchanged (74/75)
# and the per-file table is still untouched, because every shipping module is
# byte-identical: the only files that differ are ones the page does not load.
#
# REPINNED TWICE, recorded because the first attempt was wrong. The first repin
# was taken after editing `coverage.js` and BEFORE editing `scratchpad.js`, so
# it was stale by the time canonical ran and canonical caught it. The pin must
# be taken after the LAST frontend edit, not after the first.
#
# U2: `canvas-core.js` and `canvas-webgl-preview.js` CHANGED. Unlike U1 these
# ARE shipping modules the page loads, so this repin covers a real change to
# what an owner runs -- the dirty presentation path. The file count is still
# 74/75; no file was added or removed.
#
# E0: `canvas-core.js` CHANGED again -- the transient eraser preview no longer
# substitutes the stroke canvas for the active layer. Owner-visible: the layer
# stops vanishing mid-erase. Still 74/75; nothing added or removed.
#
# U3: `v2/canvas-adapter.js` ADDED (75/76), and `index.html`, `canvas-ui.js`
# and `v2/coverage.js` CHANGED. This is the first repin where the page LOADS a
# V2 module -- the kernel is no longer absent from the shipping script graph.
# It is inert: the adapter's flag is off at load and there is no public switch.
# `index.html` moving also moves the CSP loader hash in `presentation.py`, which
# `test_u3_canvas_adapter.py` now pins to the file.
#
# U3-V: `canvas-ui.js` CHANGED. Still 76/76; nothing added or removed. The
# pointer-up release path called Legacy's `finishStroke` during a V2 stroke,
# which walks from LEGACY's last dab -- still the pointer-down point, because
# Legacy never plots while V2 owns the contact -- to the release point, drawing
# a straight line from the start of the stroke to its end. Owner-visible as a
# hand-drawn Z closing into an hourglass; measured at 43,909 extra pixels.
# The two endpoint completions are now mutually exclusive. `index.html` did NOT
# move, so the CSP loader hash in `presentation.py` is unchanged.
#
# U3-R R1: `v2/coverage.js` and `v2/canvas-adapter.js` CHANGED. Still 76/76.
# `rendererFor` no longer withholds its analytic sweep from soft tips -- that
# condition was policy, not mathematics, and it cost 16.6% alpha ripple at
# hardness 0.25 with beads on the centreline at hardness 0. `depositionFor`
# gains the swept overlap weight (1 along travel, not `profileMean`), and the
# adapter now picks the renderer BEFORE building the deposition because the two
# cannot be resolved independently. `index.html` did not move, so the CSP loader
# hash in `presentation.py` is unchanged.
#
# U3-R R2: `v2/canvas-adapter.js` CHANGED. Still 76/76. `describeStroke` read
# only `S.pressureSensitivity` and mapped it to width, so an owner who chose
# `opacity` silently got width -- the one case where a setting does the wrong
# thing rather than nothing. The descriptor now carries the two dimensions
# independently, an unrecognised target normalises to "none" instead of
# becoming width, and a pressure-driven flow gets its own bounded deposition
# cache. `index.html` did not move.
#
# U3-R2F F1: `v2/coverage.js` CHANGED. Still 76/76. Two edits, one unit. The
# tip is INTEGRATED over the pixel square below radius 2 instead of point-
# sampled at its centre, which ends an instability that emptied a 1 px brush at
# the phase an axis-aligned stroke always produces and swung total paint 1600%
# at radius 0.25. And the radius floor now SNAPS as well as clamps: a disc of
# radius 0.5 covers no pixel by more than 78.5%, so integration alone left the
# owner a 44-pixel grey smudge where Legacy paints 21 black -- measured against
# the Legacy oracle, which is the only thing that caught it. Radius >= 2 is
# byte-identical. `index.html` did not move, so the CSP loader hash in
# `presentation.py` is unchanged.
#
# Repinned with `debug.js` PRISTINE: the U3-V evidence instrument appends to
# that file, and a pin taken while it is installed records a tree nobody ships.
#
# U3-R2F F2: `v2/coverage.js` and `v2/canvas-adapter.js` CHANGED. Still 76/76.
# The sweep takes both of a segment's depositions and interpolates flow at each
# pixel's own position along it -- and that alone moved the measured banding
# from 3.79% to 3.84%, which is to say nothing. The banding was never the
# staircase §3.3 describes: `depositionFor` divides flow by the EXPECTED number
# of contributions covering a pixel, the actual number is an integer, and
# 2r/gap is not. Deposition is now weighted by the arc length a segment
# actually contributes, so the shares telescope to `target * cov` however the
# path was chopped -- which also removed a 14% dependence on mark spacing that
# nothing had been looking for. `index.html` did not move, so the CSP loader
# hash in `presentation.py` is unchanged.
#
# Repinned with `debug.js` PRISTINE.
#
# U3-R2F C2: `v2/coverage.js` CHANGED, alone. Still 76/76. Deposition stops
# being two models either side of a Flow threshold. At Flow 1 the accumulator
# was skipped entirely, leaving `peak` -- a MAX over the swept polyline -- as
# the whole deposition; `max(f(d1), f(d2))` is `f(min(d1,d2))`, and the min of
# two distance fields kinks on the medial axis between two arms of ONE stroke.
# That is the light crease the owner rejected. It is now optical depth
# integrated along the path, which is additive across a segment split and has
# no max to switch on. The adapter did NOT move: the opening mark's exposure is
# decided by `dep.swept` inside the kernel, so tip routing stays U3-TF's to
# wire. `index.html` did not move, so the CSP loader hash in `presentation.py`
# is unchanged.
#
# Repinned with `debug.js` PRISTINE and no harness installed.
#
# U3-TF: `v2/coverage.js` AND `v2/canvas-adapter.js` CHANGED. Still 76/76. The
# adapter stops being an eleven-field funnel. It forwarded `sizePx`, spacing,
# hardness, flow, opacity, buildup, erase and the four pressure fields, and
# hard-coded or dropped everything else -- so fifteen of the sixteen shipping
# presets reached the engine as something other than what they declare, and
# every tip rendered round because the KIND was dropped before `rendererFor`
# saw it. Legacy's tip geometry (TIP_ASPECT, TIP_NORM, TIP_EXTENT, the spike
# fold, and the rotate-fold-anisotropy ORDER) is ported into the kernel, which
# had no anisotropic mask at all. A round tip is byte-identical by construction:
# `tipFrame` returns null for a circle and `stamp` takes the closure it always
# took. `index.html` did not move, so the CSP loader hash in `presentation.py`
# is unchanged.
#
# Repinned with `debug.js` PRISTINE and no harness installed.
#
# U3-G then made the sweep's optical-depth table profile-aware, and U3-J
# added the four per-dab jitters to the adapter. Both manifests move for
# the same two files again.
# U3-D moves both manifests, for `coverage.js` and `canvas-adapter.js`.
# Density is a STIPPLE now -- a deterministic per-(pixel, dab) draw in
# `depositWith` -- rather than a factor in `overlapK`, where it made a
# low-density dab DARKER instead of sparser. The dead `unionShare` went in
# the same change; the NEGLOG and EXPN tables it used STAY, because
# `negLogOf` and the sweep's optical-depth weight still read them.
# `index.html` did not move, so the CSP loader hash in `presentation.py`
# is unchanged.
#
# REPINNED 2026-08-27, second time, and the line above was corrected with it.
# The previous pin was derived from a source state that did not survive the
# session: it said "per-(pixel, stroke)", which is U3-D **v1** -- one draw per
# pixel, never re-rolled. The owner rejected that build and U3-D2 replaced it
# with a per-dab draw plus BE7's overlap correction, and neither the pin nor
# this comment was re-derived afterwards. Both manifests are derived here from
# the settled bytes, with `debug.js` PRISTINE and no harness installed.
#
# R1-A THEN MOVED `canvas-core.js`, which is a THIRD file and the first non-V2
# one this run. The live brush preview applied Opacity that the commit does
# not: BE17 put Opacity inside the per-dab target under Buildup and changed
# only `commitStroke` to stop applying it again, E0 later gave the ERASER
# preview the same alpha, and the BRUSH preview's four display sites were never
# given it -- so Ink Wash previewed at 0.35x and Airbrush at 0.50x of what
# would land, which is the owner's "does not appear until release". All four
# now use `S.brushBuildup ? 1 : S.brushOpacity`, which for the thirteen
# non-buildup presets IS `S.brushOpacity` character for character.
# `index.html` did not move, so the CSP loader hash in `presentation.py` is
# unchanged.
#
# And `coverage.js` moved once more, for COMMENT BYTES ONLY. `depositWith`
# still carried U3-D v1's explanation -- "one draw per (pixel, stroke)", "a
# deterministic draw needs no such correction", and a stated product cost
# ("scrubbing within one contact does not fill the holes in") that the
# per-dab draw had already made false. `code_of()` strips comments, so no
# test could have caught a comment describing the opposite of its own code.
# Proven inert by measurement rather than by inspection: the U3-D probe
# returns byte-identical kept fractions, spine unions, tap fractions and
# sweep union across the edit.
FRONTEND_MANIFEST_SHA256 = (
    "6c7d3c5ab366d73e613584cf41cee4caf618840348e698d8c712ca2cfc5509bd"
)
# Same BE16 change, same two files. Both manifests move together because they
# fingerprint the same bytes through different file sets -- one the canonical
# frontend, one everything mounted -- and a repin that updated only one would
# leave the suite red for the honest reason that the other still disagrees.
# U3-V moves it for the same single file, and both manifests move together
# because they fingerprint the same bytes through different file sets.
# U3-R R1 moves it for the same two files.
# U3-R R2 moves it for the adapter alone, and again for the internal
# pressure-width-floor setter -- the lightest-touch width the owner asked to
# choose by painting. Internal like the V2 flag: no settings surface reads it,
# nothing persists it, and the DEFAULT is unchanged at 0.35.
# U3-R2F F1 moves it for `v2/coverage.js` alone -- the tiny-tip integrator and
# the sub-pixel lattice snap, which are one repair in two halves and are pinned
# together because neither is correct without the other.
# U3-R2F F2 moves it for the same two files -- the kernel's arc-length
# deposition and the adapter that hands it both ends of each segment. They are
# pinned together because the gradient reaches nothing without the adapter's
# call, and the adapter's call means nothing without the kernel's.
# U3-R2F C2 moves it for `v2/coverage.js` alone -- the deposition model, with
# no adapter change, because the opening mark now keys off the renderer the
# kernel was already told about.
# U3-TF moves it for the same two files: the kernel gained the shape and the
# adapter gained the wiring, and neither means anything without the other.
MOUNTED_MANIFEST_SHA256 = (
    "6f2e804b681366e4b70673e61536ed2becfba0b994f57eb85de6fb39f018d070"
)

TOOL_ORDER = (
    "brush",
    "eraser",
    "eyedropper",
    "fill",
    "gradient",
    "shape",
    "text",
    "smudge",
    "blur",
    "dodge",
    "clone",
    "liquify",
    "pixelate",
    "select",
    "ellipse",
    "lasso",
    "wand",
    "crop",
    "transform",
)

CORE_SCRIPT_ORDER = (
    "ag-psd.js",
    "png-metadata.js",
    "i18n.js",
    "prefs.js",
    "shortcuts.js",
    "searchable-select.js",
    "canvas-core.js",
    "canvas-input.js",
    "canvas-ui.js",
    "workflow-state.js",
    "canvas-recovery.js",
    "app.js",
    "workflows.js",
    "module-system.js",
    "settings-page.js",
    "studio-tour.js",
    "studio-docs.js",
    "prompt-targets.js",
    "wildcard-preview.js",
    "tag-complete.js",
    "lora-browser.js",
    "checkpoint-browser.js",
    "lora-stack.js",
    "ad-lora-stack.js",
    "wildcard-browser.js",
)

OPTIONAL_SCRIPT_ORDER = (
    "develop.js",
    "workshop.js",
    "lexicon.js",
    "education.js",
    "codex.js",
    "studio-showme.js",
    "gallery.js",
    "module-tours.js",
    "debug.js",
    "canvas-webgl-preview.js",
    "studio-model-controls.js",
    "studio-dir-picker.js",
    # U3. The V2 kernel and its Canvas adapter. Loaded, and INERT until the
    # internal adapter flag is set -- `test_u3_canvas_adapter.py` drives that
    # rather than asserting it.
    "v2/brush-contracts.js",
    "v2/input.js",
    "v2/sampler.js",
    "v2/filters.js",
    "v2/dynamics.js",
    "v2/coverage.js",
    "v2/canvas-adapter.js",
)

# UI gate fixes: the external font-CDN stylesheet is gone (D2). Every
# stylesheet the shell references is served from /studio/static/.
STYLESHEET_ORDER = (
    "/studio/static/app.css?v=4.17.0",
    "/studio/static/searchable-select.css?v=4.17.0",
    "/studio/static/education.css?v=4.17.0",
    "/studio/static/studio-tour.css?v=4.17.0",
    "/studio/static/studio-docs.css?v=4.17.0",
)

SHELL_ELEMENT_TYPES = {
    "appTabs": "div",
    "app-studio": "div",
    "toolstrip": "div",
    "canvasArea": "div",
    "ctxBarWrap": "div",
    "contextBar": "div",
    "studio-viewport": "div",
    "studio-canvas": "canvas",
    "canvasPreviewWrap": "div",
    "canvasPreview": "img",
    "canvasStatus": "div",
    "deckZone": "div",
    "sessionStrip": "div",
    "panelDivider": "div",
    "panelCollapseBtn": "button",
    "panelRight": "div",
    "panelTabs": "div",
    "page-generate": "div",
    "workflowSelect": "select",
    "checkHires": "div",
    "statusDims": "span",
    "statusVRAM": "span",
    "statusModel": "span",
    "statusDot": "span",
    "statusText": "span",
}

SHELL_CLASSES = {
    "titlebar",
    "tab-bar",
    "main",
    "app-page",
    "toolstrip",
    "canvas-area",
    "ctx-bar-wrap",
    "context-bar",
    "brush-presets",
    "deck-zone",
    "session-strip",
    "panel-divider",
    "panel-collapse-btn",
    "panel-right",
    "panel-tabs",
    "panel-content",
    "panel-page",
    "statusbar",
}

TOKEN_VALUES = {
    "--bg-void": "#0e0e11",
    "--bg-surface": "#161619",
    "--bg-raised": "#1c1c20",
    "--bg-input": "#111114",
    "--border": "#2a2a30",
    "--border-subtle": "#222226",
    "--border-hover": "#3a3a42",
    "--text-1": "#e0dfd8",
    "--text-2": "#c2bfb5",
    "--text-3": "#8b8b93",
    "--text-4": "#5a5a62",
    "--accent": "#7b8fff",
    "--control-h": "30px",
    "--control-h-sm": "28px",
    "--control-gap": "6px",
    "--section-pad": "12px",
}


class _HTMLFacts(HTMLParser):
    """Collect tag, ID, class, asset, tool, and theme facts in source order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.ids: dict[str, tuple[str, dict[str, str]]] = {}
        self.classes: set[str] = set()
        self.stylesheets: list[str] = []
        self.asset_references: list[str] = []
        self.tools: list[str] = []
        self.themes: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self.elements.append((tag, attributes))
        self.classes.update(attributes.get("class", "").split())
        if element_id := attributes.get("id"):
            self.ids[element_id] = (tag, attributes)
        for name in ("href", "src"):
            if reference := attributes.get(name):
                self.asset_references.append(reference)
        if (
            tag == "link"
            and attributes.get("rel") == "stylesheet"
            and (href := attributes.get("href"))
        ):
            self.stylesheets.append(href)
        if tool := attributes.get("data-tool"):
            self.tools.append(tool)
        if "theme-btn" in attributes.get("class", "").split():
            self.themes.append(attributes.get("data-theme", ""))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


#: Files whose bytes are hashed as they are, because they are not text and a
#: newline in them is data rather than a line ending.
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".mp4", ".webm", ".wasm",
})


def _sha256(path: Path) -> str:
    """Fingerprint the CONTENT, not the checkout's line endings.

    These pins hash working-tree bytes, and git only preserves NORMALISED
    content: with `core.autocrlf=true` the object store holds LF and what lands
    on disk depends on the checkout. So the same commit fingerprinted
    differently on Windows and on Linux -- these pins passed here and would
    fail on any Docker, mac or CI checkout, for code git considers identical.

    It also bit repeatedly in ordinary work: `git stash pop` and
    `git checkout <file>` each rewrote frontend files as LF and failed these
    pins while `git diff --numstat` reported no change whatsoever, which is a
    confusing way to lose an afternoon.

    Normalising CRLF to LF before hashing keeps everything the guard is FOR --
    an unexpected edit still changes the hash -- and drops the one property it
    was accidentally asserting, which was "this file was checked out on
    Windows". Binary assets are exempt: a 0x0D0A inside a PNG is pixel data.
    """

    raw = path.read_bytes()
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _manifest_sha256(records: list[str]) -> str:
    serialized = ("\n".join(sorted(records)) + "\n").encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _script_array(source: str, name: str) -> tuple[str, ...]:
    match = re.search(
        rf"\bconst\s+{re.escape(name)}\s*=\s*\[(.*?)\];",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing JavaScript array: {name}")
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _rule_declarations(source: str, selector: str) -> dict[str, str]:
    match = re.search(
        rf"{re.escape(selector)}\s*\{{([^{{}}]*)\}}",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing CSS rule: {selector}")
    return {
        name.strip(): value.strip()
        for name, value in re.findall(
            r"([\-a-zA-Z0-9]+)\s*:\s*([^;]+);?",
            match.group(1),
        )
    }


class StudioS07SourceFaithfulnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.app_js = APP_JS_PATH.read_text(encoding="utf-8")
        cls.module_system = MODULE_SYSTEM_PATH.read_text(encoding="utf-8")
        cls.docs_css = DOCS_CSS_PATH.read_text(encoding="utf-8")
        cls.docs_js = DOCS_JS_PATH.read_text(encoding="utf-8")
        cls.lora_stack = LORA_STACK_PATH.read_text(encoding="utf-8")
        cls.document = _HTMLFacts()
        cls.document.feed(cls.html)

    def test_canonical_source_files_are_byte_identical(self) -> None:
        for relative_path, expected_hash in CANONICAL_SHA256.items():
            with self.subTest(path=relative_path):
                self.assertEqual(
                    expected_hash,
                    _sha256(FRONTEND_ROOT / relative_path),
                )

        mounted_files = tuple(
            path
            for path in FRONTEND_ROOT.rglob("*")
            if path.is_file()
        )
        canonical_files = tuple(
            path
            for path in mounted_files
            if path.name != "ag-psd.js"
        )
        # 65/66 with P0.4f: studio-dir-picker.js is the second Studio-authored
        # addition, alongside studio-model-controls.js. Counted rather than
        # implied, so a file appearing without a decision still fails here.
        #
        # 66/67 with AR4.4: canvas-recovery.js is the third, and the first to
        # be REQUIRED for correctness rather than convenience -- without it an
        # F5 discards the owner's layers, mask and Regional state outright.
        #
        # 67/68 with CT2: canvas-input.js is the fourth. It loads BEFORE
        # canvas-ui.js because `bindCanvas` reads `window.StudioInput` on the
        # first pointer event, and a seam loaded afterwards would leave every
        # stroke on the degraded fallback without saying so.
        #
        # 68/69 with V2-01b: `v2/brush-contracts.js` is the fifth, and the
        # first that `index.html` DOES NOT REFERENCE. Spec §19.7 builds V2
        # beside Legacy behind an internal development flag and §4 forbids a
        # public Legacy/V2 selector, so the file ships as a static asset the
        # page never loads. It is counted here anyway -- this assertion is
        # about what the tree contains, not about what the page pulls in, and
        # a V2 file appearing without a decision must still fail.
        # `test_v2_brush_contracts.ItIsBuiltBesideLegacyTests` is what asserts
        # the page does not load it.
        #
        # 69/70 with V2-02: `v2/input.js` is the sixth, and unloaded for the
        # same reason. Its own guard is
        # `test_v2_input.TheNormalizerMakesNoRoutingDecisionTests
        # .test_the_page_does_not_load_it`, which matches on the PATH rather
        # than the filename -- `canvas-input.js` contains this one's name, and
        # the first version of that test failed on the substring.
        #
        # 70/71 with V2-03: `v2/sampler.js` is the seventh, unloaded for the
        # same reason. The V2 subtree now holds the contract, the input
        # normalizer and the arc-length sampler, and none of them is
        # referenced by `index.html`.
        #
        # 72/73 with V2-04: `v2/filters.js` and `v2/dynamics.js` are the eighth
        # and ninth. §6.1 puts causal filtering before resampling and dynamics
        # evaluation after it, so they are two modules rather than one -- the
        # split is the spec's, not a preference. Neither is referenced by
        # `index.html`.
        #
        # 73/74 with V2-05: `v2/coverage.js` is the tenth. The V2 subtree is now
        # a complete standalone kernel -- contract, input, sampler, filters,
        # dynamics, coverage -- and none of it is referenced by `index.html`.
        #
        # 74/75 with V2-06: `v2/scratchpad.js` is the eleventh, and the last of
        # the standalone kernel. It composes the other six into §6.1's pipeline
        # and depends on nothing from `canvas-core.js`; still unloaded by the
        # page, because §4.6 makes it an engineering acceptance surface rather
        # than a second product UI.
        #
        # 75/76 with U3: `v2/canvas-adapter.js` is the twelfth, and the FIRST
        # V2 module the page actually loads. The kernel modules are now
        # referenced by `index.html` too -- the scratchpad still is not, because
        # it remains an acceptance surface rather than a product module.
        self.assertEqual(75, len(canonical_files))
        self.assertEqual(76, len(mounted_files))

        frontend_records = [
            (
                f"{path.relative_to(FRONTEND_ROOT).as_posix()}"
                f"|{_sha256(path)}"
            )
            for path in canonical_files
        ]
        mounted_records = [
            (
                "javascript/ag-psd.js"
                if path.name == "ag-psd.js"
                else (
                    "frontend/"
                    + path.relative_to(FRONTEND_ROOT).as_posix()
                )
            )
            + f"|{_sha256(path)}"
            for path in mounted_files
        ]
        self.assertEqual(
            FRONTEND_MANIFEST_SHA256,
            _manifest_sha256(frontend_records),
        )
        self.assertEqual(
            MOUNTED_MANIFEST_SHA256,
            _manifest_sha256(mounted_records),
        )

    def test_rejected_handcrafted_frontend_files_are_removed(self) -> None:
        self.assertFalse((FRONTEND_ROOT / "studio.css").exists())
        self.assertFalse((FRONTEND_ROOT / "studio.js").exists())

    def test_repository_preserves_canonical_source_line_endings(self) -> None:
        attributes = (APP_ROOT / ".gitattributes").read_text(encoding="utf-8")
        for rule in (
            "forge_studio/frontend/** text eol=lf",
            "forge_studio/frontend/fonts/OFL.txt -text",
            "forge_studio/frontend/**/*.png -text",
            "forge_studio/frontend/**/*.ttf -text",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, attributes)

    def test_canonical_shell_dom_and_element_types_are_preserved(self) -> None:
        self.assertIn("<title>Forge Studio</title>", self.html)
        self.assertEqual(set(), SHELL_CLASSES - self.document.classes)
        for element_id, expected_tag in SHELL_ELEMENT_TYPES.items():
            with self.subTest(element_id=element_id):
                self.assertIn(element_id, self.document.ids)
                actual_tag, _attributes = self.document.ids[element_id]
                self.assertEqual(expected_tag, actual_tag)

        rejected_classes = {
            "app-bar",
            "app-tabs",
            "tool-rail",
            "canvas-shell",
            "generation-panel",
            "status-bar",
        }
        self.assertEqual(set(), rejected_classes & self.document.classes)
        self.assertNotIn("data-studio-surface", self.html)
        self.assertNotIn("data-studio-milestone", self.html)

    def test_toolstrip_has_exact_19_tool_order_and_grouping(self) -> None:
        self.assertEqual(TOOL_ORDER, tuple(self.document.tools))
        toolstrip_start = self.html.index('<div class="toolstrip" id="toolstrip">')
        toolstrip_end = self.html.index(
            '<div id="hsvPopup"',
            toolstrip_start,
        )
        toolstrip = self.html[toolstrip_start:toolstrip_end]
        self.assertEqual(3, toolstrip.count('class="tool-sep"'))
        self.assertIn('class="tool-spacer"', toolstrip)
        self.assertIn('class="color-swatch" id="colorSwatch"', toolstrip)
        self.assertNotIn("toolLayers", toolstrip)
        self.assertNotIn('data-tool="layers"', toolstrip)

    def test_context_toolbar_uses_source_data_contract(self) -> None:
        context_items = {
            attrs["data-ctx"]: attrs
            for _tag, attrs in self.document.elements
            if "data-ctx" in attrs
        }
        for name in ("size", "opacity", "hardness", "smoothing"):
            self.assertIn(name, context_items)

        self.assertEqual("1", context_items["size"]["data-min"])
        self.assertEqual("500", context_items["size"]["data-max"])
        self.assertEqual("100", context_items["opacity"]["data-max"])
        self.assertEqual("%", context_items["opacity"]["data-suffix"])
        self.assertEqual("20", context_items["smoothing"]["data-max"])

        symmetry_modes = tuple(
            attrs["data-sym"]
            for _tag, attrs in self.document.elements
            if "data-sym" in attrs
        )
        self.assertEqual(("none", "h", "v", "both", "radial"), symmetry_modes)
        self.assertNotIn("segmented-control", self.document.classes)
        self.assertNotIn("mode-switcher", self.document.classes)
        self.assertNotIn("Brush controls unavailable", self.html)
        self.assertFalse(
            any("data-unavailable" in attrs for _tag, attrs in self.document.elements)
        )

    def test_document_strip_is_source_generated_and_26px_high(self) -> None:
        self.assertNotIn('id="docStrip"', self.html)
        self.assertIn('strip.className = "doc-strip"', self.docs_js)
        self.assertIn('strip.id = "docStrip"', self.docs_js)
        self.assertIn(
            "canvasArea.insertBefore(strip, canvasArea.firstChild)",
            self.docs_js,
        )
        self.assertIn('var initial = _createBlankDoc("Untitled")', self.docs_js)
        self.assertEqual("26px", _rule_declarations(
            self.docs_css,
            ".doc-strip",
        )["height"])
        self.assertEqual("22px", _rule_declarations(
            self.docs_css,
            ".doc-tab",
        )["height"])
        self.assertIn(
            ".canvas-area.has-docs #studio-viewport { top: 26px !important; }",
            self.docs_css,
        )

    def test_loader_preserves_exact_stylesheet_and_script_order(self) -> None:
        self.assertEqual(STYLESHEET_ORDER, tuple(self.document.stylesheets))
        self.assertEqual(CORE_SCRIPT_ORDER, _script_array(self.html, "scripts"))
        self.assertEqual(
            OPTIONAL_SCRIPT_ORDER,
            _script_array(self.html, "optionalScripts"),
        )
        self.assertIn('const v = "4.17.0";', self.html)
        self.assertIn("window.__STUDIO_V = v;", self.html)
        self.assertLess(
            self.html.index("for (const s of scripts)"),
            self.html.index("for (const s of optionalScripts)"),
        )

    def test_source_geometry_and_tokens_are_exact(self) -> None:
        root_tokens = dict(
            re.findall(
                r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);",
                _rule_declarations_source(self.css, ":root"),
                flags=re.IGNORECASE,
            )
        )
        for token, expected_value in TOKEN_VALUES.items():
            with self.subTest(token=token):
                self.assertEqual(expected_value, root_tokens[token].strip())

        expected_geometry = {
            (self.css, ".titlebar", "height"): "38px",
            (self.css, ".toolstrip", "width"): "42px",
            (self.css, ".tool-btn", "width"): "30px",
            (self.css, ".tool-btn", "height"): "30px",
            (self.css, ".panel-divider", "width"): "4px",
            (self.css, ".panel-collapse-btn", "width"): "16px",
            (self.css, ".panel-right", "width"): "360px",
            (self.css, ".statusbar", "height"): "24px",
            (self.css, "[data-strip-col] .session-strip", "width"): "192px",
            (
                self.css,
                "[data-strip-col] .session-strip.collapsed",
                "width",
            ): "22px",
        }
        for (source, selector, property_name), expected in expected_geometry.items():
            with self.subTest(selector=selector, property_name=property_name):
                declarations = _rule_declarations(source, selector)
                self.assertEqual(expected, declarations[property_name])

        self.assertIn("@media (max-height: 1100px)", self.css)
        self.assertIn(".panel-right { width: 380px; }", self.css)
        self.assertIn("@media (max-width: 1600px)", self.css)
        self.assertIn(
            "[data-strip-col] .session-strip { width: 22px; }",
            self.css,
        )
        self.assertRegex(self.app_js, r"\bconst\s+MIN_W\s*=\s*320\s*;")
        self.assertRegex(self.app_js, r"\bconst\s+SMALL_SCREEN\s*=\s*900\s*;")
        self.assertIn(
            "Math.min(720, Math.floor(window.innerWidth * 0.5))",
            self.app_js,
        )

    def test_source_copy_and_initial_control_values_are_preserved(self) -> None:
        self.assertIn(
            '<textarea class="prompt-text" id="paramPrompt" rows="3" '
            'placeholder="Describe what to generate..."></textarea>',
            self.html,
        )
        self.assertIn(
            '<textarea class="prompt-text prompt-neg" id="paramNeg" rows="2" '
            'placeholder="Negative prompt..."></textarea>',
            self.html,
        )
        self.assertIn(
            'empty.textContent = _t("loraStack.empty", '
            '"No LoRAs added. Click + LoRAs to browse.")',
            self.lora_stack,
        )
        self.assertIn(">Workflow\u2026</option>", self.html)

        expected_values = {
            "paramSteps": "30",
            "paramCFG": "5.0",
            "paramDenoise": "0.81",
            "paramWidth": "768",
            "paramHeight": "768",
            "paramSeed": "-1",
        }
        for element_id, expected_value in expected_values.items():
            with self.subTest(element_id=element_id):
                _tag, attributes = self.document.ids[element_id]
                self.assertEqual(expected_value, attributes["value"])

        status_order = tuple(
            self.html.index(f'id="{element_id}"')
            for element_id in (
                "statusDims",
                "statusVRAM",
                "statusModel",
                "statusDot",
                "statusText",
            )
        )
        self.assertEqual(tuple(sorted(status_order)), status_order)
        self.assertNotIn("Preview mode", self.html)
        self.assertNotIn("Alpha S0.7", self.html)
        self.assertNotIn("No LoRAs attached", self.html)

    def test_themes_fonts_and_brand_assets_match_source_inventory(self) -> None:
        self.assertEqual(
            ("", "liam", "oliver", "toxic", "neutral", "neon"),
            tuple(self.document.themes),
        )
        self.assertIn(
            "--font: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;",
            self.css,
        )
        self.assertIn(
            "--mono: 'JetBrains Mono', 'Fira Code', monospace;",
            self.css,
        )
        self.assertIn(
            "--font-display: 'Playfair Display', Georgia, "
            "'Times New Roman', serif;",
            self.css,
        )
        # UI gate fixes (D2): zero external asset references of any kind.
        # The CSS font stacks above keep their names purely as local-first
        # preferences; without the CDN stylesheet they resolve to system
        # faces, and the only bundled faces are the OFL files below.
        external_assets = tuple(
            reference
            for reference in self.document.asset_references
            if reference.startswith(("http:", "https:", "//"))
        )
        self.assertEqual((), external_assets)

        for relative_path in (
            "brand/forge-studio-mark.svg",
            "brand/favicon.svg",
            "brand/favicon-32.png",
            "brand/apple-touch-icon.png",
            "brand/logo-lockup-dark.png",
            "brand/logo-lockup-light.png",
            "fonts/PlayfairDisplay-VariableFont_wght.ttf",
            "fonts/PlayfairDisplay-Italic-VariableFont_wght.ttf",
            "fonts/OFL.txt",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((FRONTEND_ROOT / relative_path).is_file())

    def test_shell_has_no_gradio_dom_or_runtime_construction_hooks(self) -> None:
        canonical_shell = "\n".join(
            (self.html, self.css, self.module_system)
        ).casefold()
        self.assertNotIn("gradio", canonical_shell)

        runtime_sources = "\n".join((self.app_js, self.docs_js)).casefold()
        for forbidden in (
            "gradioapp(",
            "window.gradio_config",
            "/gradio_api/",
            "#component-",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, runtime_sources)


def _rule_declarations_source(source: str, selector: str) -> str:
    """Return one non-nested CSS rule body for token extraction."""

    match = re.search(
        rf"{re.escape(selector)}\s*\{{([^{{}}]*)\}}",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing CSS rule: {selector}")
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
