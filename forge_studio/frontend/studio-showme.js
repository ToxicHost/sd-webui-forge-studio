/**
 * Forge Studio — Show Me System
 * by ToxicHost & Moritz
 *
 * Interactive tutorials launched from Codex. Each tutorial:
 * 1. Switches to Studio tab
 * 2. Creates a tutorial document via StudioDocs (or reuses canvas)
 * 3. Sets up canvas state (draws shapes, loads images, sets prompts)
 * 4. Runs a walkthrough via StudioTour
 * 5. Closes the tutorial document when the walkthrough ends or is cancelled
 *
 * Tutorials register by Codex entry ID. Codex checks StudioShowMe.has(id)
 * and renders a "Show Me" button on matching entries.
 *
 * CX1 (2026-10-18): the ControlNet, Regional Prompting and Live tutorials were
 * removed with their features, which Studio cannot generate with yet. Do not
 * bring them back until they can.
 */
(function () {
"use strict";

var TAG = "[ShowMe]";
var _tutorials = {};
// The tutorial tab and the tab the tester was on before it. Held as the
// document objects themselves: tab indexes shift as tabs open and close.
var _tutorialDoc = null;
var _returnDoc = null;

function _t(key, fallback) {
  return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback) : fallback;
}

// ========================================================================
// REGISTRATION
// ========================================================================
// Tutorials register with a unique tutorialId and an entryId that maps
// to a Codex entry. Multiple tutorials can share one entryId (e.g.
// inpaint_basics and soft_inpainting both map to "inpaint_basics").

function _register(tutorialId, config) {
  _tutorials[tutorialId] = {
    entryId: config.entryId || tutorialId,
    label: config.label || ((window.I18N && window.I18N.t) ? window.I18N.t("showme.label", "Show Me") : "Show Me"),
    run: config.run
  };
}

function has(entryId) {
  for (var k in _tutorials) {
    if (_tutorials[k].entryId === entryId) return true;
  }
  return false;
}

function forEntry(entryId) {
  var result = [];
  for (var k in _tutorials) {
    if (_tutorials[k].entryId === entryId) {
      result.push({ id: k, label: _tutorials[k].label });
    }
  }
  return result;
}

// ========================================================================
// EXECUTION
// ========================================================================

function run(tutorialId) {
  var tut = _tutorials[tutorialId];
  if (!tut) { console.warn(TAG, "No tutorial for", tutorialId); return; }

  // Switch to Studio tab
  if (window.StudioModules) {
    StudioModules.activateStudio();
  }

  // Cancel any active tour, and take down the previous tutorial's tab:
  // cancelling by force does not run the old tour's own cleanup.
  if (window.StudioTour && StudioTour.active) {
    StudioTour.active._forceCancel();
  }
  _closeTutorialDoc();

  // Small delay for tab switch to render
  setTimeout(function () {
    try {
      tut.run();
    } catch (e) {
      console.error(TAG, "Tutorial error:", tutorialId, e);
    }
  }, 200);
}

// ========================================================================
// HELPERS
// ========================================================================

function _docIndex(doc) {
  if (!doc || !window.StudioDocs) return -1;
  return StudioDocs.docs.indexOf(doc);
}

function _createTutorialDoc(name) {
  if (!window.StudioDocs) { console.warn(TAG, "StudioDocs not available"); return -1; }
  _closeTutorialDoc();
  // Doc title prefix translates; the tutorial name itself stays as-passed.
  var prefix = (window.I18N && window.I18N.t)
    ? window.I18N.t("showme.docPrefix", "Tutorial: " + name, { name: name })
    : "Tutorial: " + name;
  _returnDoc = StudioDocs.activeDoc || null;
  var idx = StudioDocs.newDoc(prefix);
  _tutorialDoc = StudioDocs.docs[idx] || null;
  return idx;
}

// Closes the tutorial tab and goes back to the tab the tester came from.
function _closeTutorialDoc() {
  var doc = _tutorialDoc, back = _returnDoc;
  _tutorialDoc = null; _returnDoc = null;
  var idx = _docIndex(doc);
  if (idx < 0) return;
  var wasActive = StudioDocs.activeIdx === idx;
  // Mark not dirty so close doesn't prompt
  doc._canvasDirty = false;
  StudioDocs.closeDoc(idx);
  if (wasActive) {
    var bi = _docIndex(back);
    if (bi >= 0) StudioDocs.switchDoc(bi);
  }
}

// Every walkthrough starts here: no persistence, no cancel prompt, and the
// tutorial tab is taken down when the walkthrough finishes or is ended.
// `after` runs once at the end, for tutorials that leave the Canvas tab.
function _startTour(cfg, after) {
  cfg.persist = false;
  cfg.confirmCancel = false;
  var finish = function () {
    _closeTutorialDoc();
    if (after) { try { after(); } catch (e) { console.warn(TAG, "after-hook failed:", e); } }
  };
  cfg.onComplete = finish;
  cfg.onCancel = finish;
  StudioTour.create(cfg).start(0);
}

function _click(selector) {
  var el = document.querySelector(selector);
  if (el) el.click();
  return !!el;
}

// beforeShow helper: click something, then give the page a moment to draw.
function _goTo(selector, ms) {
  return function () {
    _click(selector);
    return new Promise(function (r) { setTimeout(r, ms || 250); });
  };
}

function _setPrompt(text) {
  var promptEl = document.getElementById("paramPrompt");
  if (promptEl) promptEl.value = text;
}

function _refreshCanvas() {
  window.StudioCore.markCompositeDirty();
  window.StudioCore.composite();
  window.StudioUI.redraw();
  window.StudioUI.renderLayerPanel();
}

// Draw a simple colored rectangle on a layer
function _drawRect(ctx, x, y, w, h, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
}

// Draw a simple scene on the reference layer (colored shapes for tutorials).
// Fills white first so the scene composites cleanly even when newDoc skipped
// the resizeCanvas white-fill (early-return when dimensions already match).
// Bumps the composite version at the end — direct ctx mutations don't
// invalidate the per-version composite cache, so without this the next
// composite() call returns the pre-drawing pixels and the scene is invisible.
function _drawTutorialScene() {
  var S = window.StudioCore.state;
  var ref = S.layers.find(function (l) { return l.type === "reference"; });
  if (!ref) return;

  var W = S.W, H = S.H;
  var ctx = ref.ctx;

  // White background so colors don't composite against transparent void
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  // Sky gradient
  var grad = ctx.createLinearGradient(0, 0, 0, H * 0.6);
  grad.addColorStop(0, "#2c3e6b");
  grad.addColorStop(1, "#6b88b5");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H * 0.6);

  // Ground
  ctx.fillStyle = "#4a6741";
  ctx.fillRect(0, H * 0.6, W, H * 0.4);

  // Sun
  ctx.fillStyle = "#f0c040";
  ctx.beginPath();
  ctx.arc(W * 0.75, H * 0.2, 40, 0, Math.PI * 2);
  ctx.fill();

  // Tree trunk
  ctx.fillStyle = "#5c3a1e";
  ctx.fillRect(W * 0.2 - 10, H * 0.35, 20, H * 0.25);

  // Tree canopy
  ctx.fillStyle = "#2d5a1e";
  ctx.beginPath();
  ctx.arc(W * 0.2, H * 0.3, 45, 0, Math.PI * 2);
  ctx.fill();

  // House body
  ctx.fillStyle = "#c4956a";
  ctx.fillRect(W * 0.5, H * 0.4, 100, 80);

  // House roof
  ctx.fillStyle = "#8b3a3a";
  ctx.beginPath();
  ctx.moveTo(W * 0.5 - 10, H * 0.4);
  ctx.lineTo(W * 0.5 + 50, H * 0.28);
  ctx.lineTo(W * 0.5 + 110, H * 0.4);
  ctx.closePath();
  ctx.fill();

  // Door
  ctx.fillStyle = "#5c3a1e";
  ctx.fillRect(W * 0.5 + 38, H * 0.48, 24, 32);

  // Window
  ctx.fillStyle = "#a8c8e8";
  ctx.fillRect(W * 0.5 + 15, H * 0.44, 18, 18);
  ctx.fillRect(W * 0.5 + 68, H * 0.44, 18, 18);

  if (window.StudioCore && window.StudioCore.markCompositeDirty) window.StudioCore.markCompositeDirty();
}

// A tutorial tab with the sample scene already drawn.
function _sceneDoc(nameKey, nameFallback) {
  _createTutorialDoc(_t(nameKey, nameFallback));
  _drawTutorialScene();
  _refreshCanvas();
}

var CENTER = { bottom: "80px", left: "calc(50% - 190px)" };
var MASK_TOOL = '#toolstrip [data-tool="mask"]';

// ========================================================================
// TUTORIAL DEFINITIONS
// ========================================================================

// ── Core Workflow / first image (replay of the first-run walkthrough) ──
// Replays the tester's own path; it never forgets which path they chose.

function _replayWalkthrough() {
  if (window.Education && Education.restartTour) {
    Education.restartTour();
  } else if (window.showToast) {
    showToast(_t("showme.educationUnavailable", "Education system not available"), "error");
  }
}

_register("core_workflow", { label: "Show Me", run: _replayWalkthrough });
_register("first_image_showme", { entryId: "first_image", label: "Show Me", run: _replayWalkthrough });

// ── Setting up models ────────────────────────────────────────────────

_register("model_setup", {
  entryId: "models_and_vae",
  label: _t("showme.tutorialLabel.modelSetup", "Setting Up Models"),
  run: function () {
    _startTour({
      id: "showme-models",
      steps: [
        {
          id: "sm_intro",
          text: _t("showme.models.intro.text", "Studio generates with the models in the folders you choose — there is no Load button. Let’s point it at yours.\n\nThis walkthrough takes you to <em>Settings</em> and back."),
          spotlight: null, advance: "click", btn: _t("showme.models.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "sm_folders",
          text: _t("showme.models.library.text", "<em>Settings → General → Model folders.</em> Add the folders where your models are — one big folder or several, however you keep them. Studio looks through every subfolder and sorts each file by what it is.\n\nNothing in your folders is moved or changed."),
          beforeShow: function () {
            _click('button[data-app="settings"]');
            return new Promise(function (r) {
              setTimeout(function () { _click('[data-settings-nav="general"]'); setTimeout(r, 250); }, 250);
            });
          },
          spotlight: '[data-settings-section="model-folders"]', advance: "click",
          btn: _t("showme.models.folders.btn", "Next")
        },
        {
          id: "sm_add",
          text: _t("showme.models.add.text", "Press <em>Browse…</em> and pick a folder, or type its path and press <em>Add folder</em>. It is saved at once, and its row shows what Studio found there.\n\nA folder on another computer needs <em>Allow network model folders</em> switched on first."),
          spotlight: "#modelLibraryBrowse", advance: "click", btn: _t("showme.models.save.btn", "Next")
        },
        {
          id: "sm_pick",
          text: _t("showme.models.pick.text", "Back on the Canvas, the <em>Model</em> box lists your checkpoints. Models that need them also use a <em>Text encoder</em> and a <em>VAE</em> — leave VAE on Automatic unless the model asks for one.\n\nAdded a file later? Press the ↻ button beside the box."),
          beforeShow: _goTo('button[data-app="studio"]', 300),
          spotlight: '[data-block="model"]', advance: "click", btn: _t("showme.models.pick.btn", "Next")
        },
        {
          id: "sm_generate",
          text: _t("showme.models.generate.text", "Choosing a model does not load it. <em>Generate</em> loads what you chose, so the first image after a change takes longer."),
          spotlight: "#genBtn", advance: "click", btn: _t("showme.models.generate.btn", "Got it")
        },
      ],
    });
  }
});

// ── Inpainting ───────────────────────────────────────────────────────

_register("inpaint_basics", {
  label: _t("showme.tutorialLabel.basicInpainting", "Basic Inpainting"),
  run: function () {
    _sceneDoc("showme.tutorialName.inpainting", "Inpainting");
    // Pre-fill prompt so generation doesn't choke on empty string
    _setPrompt("a full moon in a night sky, stars");

    _startTour({
      id: "showme-inpaint",
      steps: [
        {
          id: "si_intro",
          text: _t("showme.inpaint.intro.text", "Here’s a simple scene. Let’s change part of it using <em>inpainting</em>.\n\nSee the sun in the sky? We’re going to replace it with something else."),
          spotlight: null, advance: "click",
          btn: _t("showme.inpaint.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "si_mask",
          text: _t("showme.inpaint.mask.text", "Press <span class=\"cx-kbd\">Q</span> to pick the <em>Inpaint Mask</em> tool (highlighted). Your brush now paints a <em>red overlay</em> instead of color.\n\nPaint red over the sun. It doesn’t need to be precise — just cover it."),
          spotlight: MASK_TOOL, advance: "click",
          btn: _t("showme.inpaint.mask.btn", "I’ve masked the sun")
        },
        {
          id: "si_area",
          text: _t("showme.inpaint.area.text", "The bar above the canvas has the inpaint settings. <em>Only Masked</em> redraws just the masked area at full detail, with <em>Padding</em> of surrounding context; <em>Whole Picture</em> redraws the full canvas.\n\n<em>Fill</em> chooses what is under the mask: <em>Original</em> keeps it, <em>Fill</em> starts from flat colour (best for removing things)."),
          beforeShow: function () { _click(MASK_TOOL); },
          spotlight: "#inpaintBar", advance: "click",
          btn: _t("showme.inpaint.area.btn", "Next")
        },
        {
          id: "si_prompt",
          text: _t("showme.inpaint.prompt.text", "A prompt has been pre-filled — \"a full moon in a night sky\" — but you can change it to whatever you want. The AI will only change the red area."),
          spotlight: "#paramPrompt", advance: "click",
          btn: _t("showme.inpaint.prompt.btn", "Next"),
          focusTarget: "#paramPrompt"
        },
        {
          id: "si_generate",
          text: _t("showme.inpaint.generate.text", "Hit <em>Generate</em>. Only the masked area changes — the rest stays untouched.\n\nAfterward, press <span class=\"cx-kbd\">Q</span> to go back to painting. Try different denoise values to see how much changes."),
          spotlight: "#genBtn", advance: "click",
          btn: _t("showme.inpaint.generate.btn", "Got it")
        },
      ],
    });
  }
});

// ── Soft Inpainting ──────────────────────────────────────────────────

_register("soft_inpainting", {
  entryId: "inpaint_basics",
  label: _t("showme.tutorialLabel.softInpainting", "Soft Inpainting"),
  run: function () {
    _sceneDoc("showme.tutorialName.softInpainting", "Soft Inpainting");
    _setPrompt("a castle tower, stone walls, medieval, detailed");

    _startTour({
      id: "showme-softinpaint",
      steps: [
        {
          id: "ssi_intro",
          text: _t("showme.softInpaint.intro.text", "Standard inpainting has a hard boundary — inside the mask changes, outside doesn’t. <em>Soft Inpainting</em> blends smoothly across the boundary for more natural transitions.\n\nIt works with a <em>soft mask</em>, and the Inpaint Mask is always a hard brush. So make a <em>named mask</em>: click <em>New mask</em> in the Masks panel."),
          beforeShow: _goTo('[data-panel="generate"]', 250),
          spotlight: "#maskNew", advance: "click",
          btn: _t("showme.softInpaint.intro.btn", "I made a mask")
        },
        {
          id: "ssi_soft",
          text: _t("showme.softInpaint.soft.text", "With the new mask selected, the bar above the canvas has <em>Opacity</em> and <em>Hardness</em>. Turn <em>Hardness</em> down and paint over the house: the edge fades out.\n\nThe selected row says <em>used for inpainting</em> — that is the mask Generate sends."),
          spotlight: "#masksList", advance: "click",
          btn: _t("showme.softInpaint.soft.btn", "I painted it soft")
        },
        {
          id: "ssi_enable",
          text: _t("showme.softInpaint.enable.text", "Find <em>Soft Inpainting</em> in the Generate panel (it appears when the mask tool is active). Enable the checkbox.\n\nSix parameters. The two that matter most:\n\n• <strong>Preservation</strong> — how much of the original is kept. Higher = less change.\n• <strong>Contrast Boost</strong> — how sharp the blend edge is. Lower = softer blend."),
          beforeShow: _goTo(MASK_TOOL, 250),
          spotlight: "#softInpaintSection", advance: "click",
          btn: _t("showme.softInpaint.enable.btn", "Next")
        },
        {
          id: "ssi_generate",
          text: _t("showme.softInpaint.generate.text", "Hit <em>Generate</em> and compare with Soft Inpainting off (same mask, same seed).\n\nWith Soft Inpainting on, the mask’s grey is used; with it off, a soft mask is cut at 40%.\n\nThe other parameters (Schedule Bias, Mask Influence, Diff Threshold/Contrast) are fine at their defaults until you need specific control."),
          spotlight: "#genBtn", advance: "click",
          btn: _t("showme.softInpaint.generate.btn", "Got it")
        },
      ],
    });
  }
});

// ── Masks panel ──────────────────────────────────────────────────────

_register("masks_panel", {
  label: "Show Me",
  run: function () {
    _sceneDoc("showme.tutorialName.masks", "Masks Panel");

    _startTour({
      id: "showme-masks",
      steps: [
        {
          id: "sk_intro",
          text: _t("showme.masks.intro.text", "Besides the Inpaint mask, a picture can hold any number of <em>named masks</em>. They live in the <em>Masks</em> panel, inside Layers.\n\nThe mask <em>selected</em> there is the one Generate inpaints with."),
          beforeShow: _goTo('[data-panel="generate"]', 250),
          spotlight: "#masksList", advance: "click", btn: _t("showme.masks.intro.btn", "Next")
        },
        {
          id: "sk_new",
          text: _t("showme.masks.new.text", "Click <em>New mask</em>. A mask appears in the list and the Inpaint Mask tool is picked. Paint over the house; <em>− Subtract</em> in the bar above the canvas erases.\n\nDouble-click a name to rename it, the eye hides its overlay, the swatch changes its colour."),
          spotlight: "#maskNew", advance: "click", btn: _t("showme.masks.new.btn", "I made a mask")
        },
        {
          id: "sk_selection",
          text: _t("showme.masks.selection.text", "<em>Load as selection</em> turns the selected mask into a selection (Shift adds, Alt subtracts, Shift+Alt intersects); Ctrl+click on a row does the same.\n\nThe button beside it, <em>Save selection as mask</em>, goes the other way: draw a selection with any selection tool, then keep it as a mask."),
          spotlight: "#maskToSelection", advance: "click", btn: _t("showme.masks.selection.btn", "Next")
        },
        {
          id: "sk_range",
          text: _t("showme.masks.range.text", "<em>From range…</em> makes a soft mask from the picture itself: its <em>highlights</em>, <em>midtones</em>, <em>shadows</em>, or a <em>colour range</em>. Try <em>Shadows</em> — no painting needed.\n\n<em>From word…</em> does the same from a word of the prompt, for pictures Studio made with heatmap recording on (see the Codex)."),
          spotlight: "#maskFromRange", advance: "click", btn: _t("showme.masks.range.btn", "Next")
        },
        {
          id: "sk_use",
          text: _t("showme.masks.use.text", "The selected row says <em>used for inpainting</em>. Write a prompt and press Generate: only that mask changes.\n\nMasks are kept per tab, come back after a reload, and are saved as layer masks when you export a PSD."),
          spotlight: "#masksList", advance: "click", btn: _t("showme.masks.use.btn", "Got it")
        },
      ],
    });
  }
});

// ── Outpaint ─────────────────────────────────────────────────────────

_register("outpaint", {
  entryId: "outpaint_canvas_size",
  label: "Show Me",
  run: function () {
    _sceneDoc("showme.tutorialName.outpaint", "Outpaint");
    _setPrompt("a wide mountain valley at dusk, painterly, detailed");

    _startTour({
      id: "showme-outpaint",
      steps: [
        {
          id: "so_intro",
          text: _t("showme.outpaint.intro.text", "<em>Outpaint</em> makes the picture bigger and asks the model to paint the new space so it continues the scene.\n\nThere is no button for it: it lives in the canvas right-click menu."),
          spotlight: null, advance: "click", btn: _t("showme.outpaint.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "so_menu",
          text: _t("showme.outpaint.menu.text", "Right-click the canvas and choose <em>Outpaint…</em>. (<em>Canvas Size…</em>, just above it, grows or trims the canvas without generating.)"),
          spotlight: "#canvasArea", advance: "click", btn: _t("showme.outpaint.menu.btn", "I opened it")
        },
        {
          id: "so_dialog",
          text: _t("showme.outpaint.dialog.text", "Choose how far to extend each side, in pixels (rounded to a multiple of 8). <em>Overlap</em> is how much of the existing edge is repainted so the join is seamless. <em>Refine new area</em> adds a second, light pass for crisper detail — slower, and off unless you choose it.\n\nPress <em>Extend</em>."),
          spotlight: null, advance: "click", btn: _t("showme.outpaint.dialog.btn", "Next"),
          position: { top: "80px", left: "60px" }
        },
        {
          id: "so_generate",
          text: _t("showme.outpaint.generate.text", "Studio fills the new area from the picture’s edge on a layer called <em>Outpaint fill</em>, masks it, and sets up inpainting. Describe what should appear, then press <em>Generate</em> (this needs a model).\n\nIt takes about as long as one normal generation, even on large pictures."),
          spotlight: "#genBtn", advance: "click", btn: _t("showme.outpaint.generate.btn", "Got it")
        },
      ],
    });
  }
});

// ── Canvas right-click menu ──────────────────────────────────────────

_register("canvas_menu", {
  label: "Show Me",
  run: function () {
    _sceneDoc("showme.tutorialName.canvasMenu", "Canvas Menu");

    _startTour({
      id: "showme-canvasmenu",
      steps: [
        {
          id: "scm_intro",
          text: _t("showme.canvasMenu.intro.text", "Right-click anywhere on the canvas. This menu has things that have no toolbar button — start by right-clicking the picture now."),
          spotlight: "#canvasArea", advance: "click", btn: _t("showme.canvasMenu.intro.btn", "Next")
        },
        {
          id: "scm_selection",
          text: _t("showme.canvasMenu.selection.text", "With a <em>selection</em> active (try the rectangle tool, <span class=\"cx-kbd\">M</span>), the menu adds <em>Invert</em>, <em>Feather</em>, <em>Grow</em> and <em>Shrink Selection…</em>, Copy/Cut/Delete, and <em>Smart Patch</em> — which fills the selection from the picture around it, with no model."),
          spotlight: null, advance: "click", btn: _t("showme.canvasMenu.selection.btn", "Next"),
          position: CENTER
        },
        {
          id: "scm_layers",
          text: _t("showme.canvasMenu.layers.text", "Below that: <em>New Layer</em>, <em>Duplicate Layer</em>, <em>Merge Down</em>, <em>Flatten Visible</em>, then <em>Canvas Size…</em> and <em>Outpaint…</em> (only here)."),
          spotlight: null, advance: "click", btn: _t("showme.canvasMenu.layers.btn", "Next"),
          position: CENTER
        },
        {
          id: "scm_save",
          text: _t("showme.canvasMenu.save.text", "At the bottom: <em>Open in New Tab</em> opens the flattened picture as a PNG in a new browser tab (allow pop-ups for Studio), and <em>Save as</em> PNG, JPEG, WebP or a layered PSD."),
          spotlight: null, advance: "click", btn: _t("showme.canvasMenu.save.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ── Layers ───────────────────────────────────────────────────────────

_register("layers_basics", {
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.layers", "Layers"));
    var S = window.StudioCore.state;

    // Draw on reference layer
    var ref = S.layers.find(function (l) { return l.type === "reference"; });
    if (ref) {
      ref.ctx.fillStyle = "#e8dcc8";
      ref.ctx.fillRect(0, 0, S.W, S.H);
      _drawRect(ref.ctx, 100, 100, 200, 200, "#6b88b5");
    }

    // Draw on paint layer
    var paint = S.layers.find(function (l) { return l.type === "paint"; });
    if (paint) {
      _drawRect(paint.ctx, 200, 200, 200, 200, "#c04040");
    }
    _refreshCanvas();

    _startTour({
      id: "showme-layers",
      steps: [
        {
          id: "sl_intro",
          text: _t("showme.layers.intro.text", "This canvas has two layers. The <em>Background</em> layer has a blue square. <em>Layer 1</em> has a red square overlapping it.\n\nLayers are transparent sheets stacked on each other."),
          spotlight: "#layersList", advance: "click", btn: _t("showme.layers.intro.btn", "Next"),
          beforeShow: _goTo('[data-panel="generate"]', 200)
        },
        {
          id: "sl_visibility",
          text: _t("showme.layers.visibility.text", "Try clicking the <em>eye icon</em> on Layer 1 to hide it. The red square disappears, revealing the blue one underneath.\n\nClick it again to show it. This is how you control what’s visible without deleting anything."),
          spotlight: "#layersList", advance: "click", btn: _t("showme.layers.visibility.btn", "Next")
        },
        {
          id: "sl_generation",
          text: _t("showme.layers.generation.text", "When you <em>Generate</em>, the result lands on the reference (bottom) layer. Your painting on upper layers is <strong>never overwritten</strong>.\n\nThis means you can always regenerate without losing your edits."),
          spotlight: null, advance: "click", btn: _t("showme.layers.generation.btn", "Next"),
          position: CENTER
        },
        {
          id: "sl_adjust",
          text: _t("showme.layers.adjust.text", "The buttons above the list add <em>adjustment layers</em> — Brightness/Contrast, Hue/Saturation and Levels — that change everything beneath them without touching the pixels. Delete the layer to undo it.\n\nThe <em>Masks</em> panel below the list is covered in its own Show Me."),
          spotlight: "#layerAddBrightness", advance: "click", btn: _t("showme.layers.adjust.btn", "Next")
        },
        {
          id: "sl_blend",
          text: _t("showme.layers.blend.text", "Each layer has <em>Opacity</em> and <em>Blend Mode</em>. Try changing Layer 1’s blend mode to Multiply or Screen to see how it interacts with the layer below.\n\nThe <em>Save Canvas / PSD</em> button (the disk icon above the list) exports the full stack as a PSD."),
          spotlight: "#layersList", advance: "click", btn: _t("showme.layers.blend.btn", "Got it")
        },
      ],
    });
  }
});

// ── Clone Stamp ──────────────────────────────────────────────────────

_register("tool_clone", {
  run: function () {
    _sceneDoc("showme.tutorialName.cloneStamp", "Clone Stamp");

    // Add an "unwanted" object to remove
    var S = window.StudioCore.state;
    var ref = S.layers.find(function (l) { return l.type === "reference"; });
    if (ref) {
      ref.ctx.fillStyle = "#ff00ff";
      ref.ctx.fillRect(S.W * 0.4, S.H * 0.65, 30, 30);
    }
    _refreshCanvas();

    _startTour({
      id: "showme-clone",
      steps: [
        {
          id: "scl_intro",
          text: _t("showme.clone.intro.text", "See the pink square on the ground? We’ll remove it using the <em>Clone Stamp</em>.\n\nThis tool copies pixels from one area and paints them over another — perfect for removing unwanted objects."),
          spotlight: null, advance: "click", btn: _t("showme.clone.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "scl_select",
          text: _t("showme.clone.select.text", "Press <span class=\"cx-kbd\">K</span> to select the Clone Stamp tool."),
          spotlight: "#toolstrip", advance: "click", btn: _t("showme.clone.select.btn", "Next")
        },
        {
          id: "scl_source",
          text: _t("showme.clone.source.text", "Now hold <span class=\"cx-kbd\">Alt</span> and click on a <em>clean area</em> of green ground near the pink square. This sets your <strong>source point</strong> — where the clone copies from."),
          spotlight: null, advance: "click", btn: _t("showme.clone.source.btn", "I set the source"),
          position: CENTER
        },
        {
          id: "scl_paint",
          text: _t("showme.clone.paint.text", "Now paint directly over the pink square. The tool copies green ground from the source point, covering the pink.\n\nIf the clone starts picking up wrong content, <span class=\"cx-kbd\">Alt+Click</span> a new source and keep going."),
          spotlight: null, advance: "click", btn: _t("showme.clone.paint.btn", "Next"),
          position: CENTER
        },
        {
          id: "scl_modes",
          text: _t("showme.clone.modes.text", "The tool has three modes: <em>Clone</em> copies exactly; <em>Heal</em> copies the source’s detail but keeps the colour and light of where you paint; <em>Patch</em> needs no source at all. There is a separate Show Me for Patch."),
          beforeShow: _goTo('#toolstrip [data-tool="clone"]', 200),
          spotlight: "[data-clonemode=\"heal\"]", advance: "click", btn: _t("showme.clone.modes.btn", "Got it")
        },
      ],
    });
  }
});

// ── Smart Patch ──────────────────────────────────────────────────────

_register("smart_patch", {
  entryId: "tool_smart_patch",
  label: "Show Me",
  run: function () {
    _sceneDoc("showme.tutorialName.smartPatch", "Smart Patch");
    var S = window.StudioCore.state;
    var ref = S.layers.find(function (l) { return l.type === "reference"; });
    if (ref) {
      ref.ctx.fillStyle = "#ff00ff";
      ref.ctx.fillRect(S.W * 0.4, S.H * 0.65, 30, 30);
    }
    _refreshCanvas();

    _startTour({
      id: "showme-smartpatch",
      steps: [
        {
          id: "ssp_intro",
          text: _t("showme.smartPatch.intro.text", "The pink square again. <em>Patch</em> removes small things like this from their surroundings — no source point, no model, no waiting."),
          spotlight: null, advance: "click", btn: _t("showme.smartPatch.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "ssp_mode",
          text: _t("showme.smartPatch.mode.text", "Press <span class=\"cx-kbd\">K</span> for the Clone tool, then click <em>Patch</em> in the bar above the canvas."),
          beforeShow: _goTo('#toolstrip [data-tool="clone"]', 200),
          spotlight: "[data-clonemode=\"patch\"]", advance: "click", btn: _t("showme.smartPatch.mode.btn", "I chose Patch")
        },
        {
          id: "ssp_paint",
          text: _t("showme.smartPatch.paint.text", "Paint over the pink square, covering it fully, then let go. On release it is filled from the picture around it.\n\nIf the result isn’t right, undo (<span class=\"cx-kbd\">Ctrl+Z</span>) and paint a slightly larger area."),
          spotlight: null, advance: "click", btn: _t("showme.smartPatch.paint.btn", "Next"),
          position: CENTER
        },
        {
          id: "ssp_selection",
          text: _t("showme.smartPatch.selection.text", "Same thing with a selection: draw one around a blemish, right-click inside it and choose <em>Smart Patch</em>.\n\nIt works best on small things against a plain-ish background. For anything large, mask it and use inpainting."),
          spotlight: null, advance: "click", btn: _t("showme.smartPatch.selection.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ── Paint Mixer ──────────────────────────────────────────────────────

_register("paint_mixer", {
  entryId: "tool_paint_mixer",
  label: "Show Me",
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.paintMixer", "Paint Mixer"));
    _refreshCanvas();

    _startTour({
      id: "showme-paintmixer",
      steps: [
        {
          id: "spm_intro",
          text: _t("showme.paintMixer.intro.text", "The brush can <em>mix</em> colours like wet paint: it picks up the colour under it and carries it along the stroke, so blue and yellow make green, not just yellow on top."),
          spotlight: null, advance: "click", btn: _t("showme.paintMixer.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "spm_on",
          text: _t("showme.paintMixer.on.text", "Pick the brush (<span class=\"cx-kbd\">B</span>) and switch <em>Mix</em> on in the bar above the canvas. <em>Amount</em> is how much of the picked-up colour the brush carries: 0 is the plain brush colour, 100 only what it picked up."),
          beforeShow: _goTo('#toolstrip [data-tool="brush"]', 200),
          spotlight: "#brushMixBtn", advance: "click", btn: _t("showme.paintMixer.on.btn", "Mix is on")
        },
        {
          id: "spm_paint",
          text: _t("showme.paintMixer.paint.text", "Paint a wide <em>blue</em> stroke. Change the foreground colour to <em>yellow</em> and paint across it. Where they meet you get green, and the colours smear into each other along the stroke.\n\nTurn Mix off to go back to ordinary painting."),
          spotlight: "#toolstrip", advance: "click", btn: _t("showme.paintMixer.paint.btn", "Got it")
        },
      ],
    });
  }
});

// ── Develop: local adjustments ───────────────────────────────────────

_register("develop_local", {
  entryId: "develop",
  label: "Show Me",
  run: function () {
    _sceneDoc("showme.tutorialName.developLocal", "Develop");
    // A mask for the walkthrough to use: the picture's shadows.
    try {
      if (window.StudioCore.createRangeMask) {
        window.StudioCore.createRangeMask({ kind: "luminance", band: "shadows" });
        if (window.StudioUI.renderMaskPanel) window.StudioUI.renderMaskPanel();
      }
    } catch (e) { console.warn(TAG, "could not make the sample mask:", e); }

    _startTour({
      id: "showme-develop",
      steps: [
        {
          id: "sd_intro",
          text: _t("showme.develop.intro.text", "<em>Develop</em> adjusts the whole picture — or just part of it. For a part you need a <em>mask</em>.\n\nWe made one for you: a mask of this picture’s <em>shadows</em>, in the Masks panel."),
          beforeShow: _goTo('[data-panel="generate"]', 250),
          spotlight: "#masksList", advance: "click", btn: _t("showme.develop.intro.btn", "Next")
        },
        {
          id: "sd_local",
          text: _t("showme.develop.local.text", "In the <em>Develop</em> tab, <em>Local adjustments</em> sits right under the global sliders. Press <em>Add local adjustment</em> and choose the mask.\n\nThen move Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Temperature, Tint, Saturation or Vibrance: they change only what the mask covers."),
          beforeShow: _goTo('button[data-module="develop"]', 500),
          spotlight: '[data-section="local"]', advance: "click", btn: _t("showme.develop.local.btn", "Next")
        },
        {
          id: "sd_ba",
          text: _t("showme.develop.ba.text", "<em>B/A</em> flips between before and after, and <em>Reset</em> clears every Develop adjustment. The switch beside the title turns Develop on or off without losing your settings."),
          spotlight: null, advance: "click", btn: _t("showme.develop.ba.btn", "Got it"),
          position: { bottom: "80px", right: "340px" }
        },
      ],
    }, function () { if (window.StudioModules) StudioModules.activateStudio(); });
  }
});

// ── Transform & Warp ─────────────────────────────────────────────────

_register("tool_transform", {
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.transformWarp", "Transform & Warp"));
    var S = window.StudioCore.state;
    var paint = S.layers.find(function (l) { return l.type === "paint"; });
    if (paint) {
      // Draw a star shape to transform
      var cx = S.W / 2, cy = S.H / 2, or = 80, ir = 35;
      paint.ctx.fillStyle = "#e04040";
      paint.ctx.beginPath();
      for (var i = 0; i < 10; i++) {
        var r = i % 2 === 0 ? or : ir;
        var a = (Math.PI * 2 * i / 10) - Math.PI / 2;
        if (i === 0) paint.ctx.moveTo(cx + r * Math.cos(a), cy + r * Math.sin(a));
        else paint.ctx.lineTo(cx + r * Math.cos(a), cy + r * Math.sin(a));
      }
      paint.ctx.closePath();
      paint.ctx.fill();
    }
    _refreshCanvas();

    _startTour({
      id: "showme-transform",
      steps: [
        {
          id: "st_intro",
          text: _t("showme.transform.intro.text", "Here’s a shape on the canvas. Press <span class=\"cx-kbd\">V</span> to activate the <em>Transform</em> tool.\n\nDrag the handles to resize, drag inside to move, and rotate from the corners."),
          spotlight: "#toolstrip", advance: "click", btn: _t("showme.transform.intro.btn", "Next")
        },
        {
          id: "st_basic",
          text: _t("showme.transform.basic.text", "Try the basics: drag a corner handle to resize. Hold <span class=\"cx-kbd\">Shift</span> to constrain proportions. Drag inside to reposition.\n\n<span class=\"cx-kbd\">Enter</span> applies the transform. <span class=\"cx-kbd\">Escape</span> cancels."),
          spotlight: null, advance: "click", btn: _t("showme.transform.basic.btn", "Next"),
          position: CENTER
        },
        {
          id: "st_warp",
          text: _t("showme.transform.warp.text", "The bar above the canvas has more modes:\n\n• <strong>Skew</strong> (<span class=\"cx-kbd\">K</span>) — edge handles shear the shape\n• <strong>Persp</strong> (<span class=\"cx-kbd\">P</span>) — drag the corners freely for perspective\n• <strong>Warp</strong> (<span class=\"cx-kbd\">W</span>) — free deformation with control points; then <em>Aff</em>, <em>Sim</em> or <em>Rig</em> picks how it deforms (affine, angle-preserving, shape-preserving)\n\nThere are also Flip and Rotate buttons. Try each one."),
          spotlight: null, advance: "click", btn: _t("showme.transform.warp.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ── Canvas Routing ───────────────────────────────────────────────────

_register("canvas_routing", {
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.canvasRouting", "Canvas Routing"));
    window.StudioCore.composite();
    window.StudioUI.redraw();

    // Pre-fill prompt so generation doesn't choke on empty string
    _setPrompt("a colorful landscape, digital painting, detailed");

    _startTour({
      id: "showme-routing",
      steps: [
        {
          id: "scr_blank",
          text: _t("showme.routing.blank.text", "This is a <em>blank canvas</em>. If you hit Generate right now, Studio runs <strong>txt2img</strong> — generating from scratch based only on your prompt.\n\nNo mode selection needed. The canvas state IS the mode."),
          spotlight: null, advance: "click",
          btn: _t("showme.routing.blank.btn", "Next"),
          position: CENTER
        },
        {
          id: "scr_content",
          text: _t("showme.routing.content.text", "Now paint something on the canvas — anything. A scribble, a shape, a splash of color.\n\nOnce there are pixels on the canvas, Generate switches to <strong>img2img</strong> automatically. The AI works <em>with</em> what’s there. <em>Denoise</em> controls how much it changes."),
          spotlight: "#toolstrip", advance: "click",
          btn: _t("showme.routing.content.btn", "I painted something")
        },
        {
          id: "scr_mask",
          text: _t("showme.routing.mask.text", "Now press <span class=\"cx-kbd\">Q</span> to pick the <em>Inpaint Mask</em> tool (highlighted) and paint a red overlay over part of your content. A mask selected in the Masks panel counts too.\n\nWith a mask present, Generate switches to <strong>inpainting</strong> — only the masked area changes. Everything else stays untouched.\n\nThree modes, zero toggles. The pixels decide."),
          spotlight: MASK_TOOL, advance: "click",
          btn: _t("showme.routing.mask.btn", "Got it")
        },
      ],
    });
  }
});

// ── Radial Symmetry ──────────────────────────────────────────────────

_register("tool_radial_symmetry", {
  label: "Show Me",
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.radialSymmetry", "Radial Symmetry"));
    window.StudioCore.composite();
    window.StudioUI.redraw();

    _startTour({
      id: "showme-radialsym",
      steps: [
        {
          id: "srs_intro",
          text: _t("showme.radialSym.intro.text", "Radial symmetry mirrors your brush strokes around a center point. Let’s paint a mandala pattern.\n\nFirst, select the <em>Brush</em> tool (<span class=\"cx-kbd\">B</span>)."),
          spotlight: "#toolstrip", advance: "click", btn: _t("showme.radialSym.intro.btn", "Next")
        },
        {
          id: "srs_enable",
          text: _t("showme.radialSym.enable.text", "In the context bar (top of the canvas area), find the symmetry buttons (near the right side). Click the <strong>radial</strong> button (the starburst icon).\n\nSet the <em>axis count</em> — try <strong>6</strong> for a snowflake pattern or <strong>8</strong> for a mandala. More axes = more repetitions."),
          spotlight: null, advance: "click", btn: _t("showme.radialSym.enable.btn", "Next"),
          position: { top: "100px", left: "calc(50% - 190px)" }
        },
        {
          id: "srs_paint",
          text: _t("showme.radialSym.paint.text", "Now paint on the canvas. Every stroke is replicated around the center point.\n\nTry painting outward from the center, or in arcs. Even simple strokes create complex patterns.\n\nThe smudge tool also works with radial symmetry — each segment has its own independent smudge buffer."),
          spotlight: null, advance: "click", btn: _t("showme.radialSym.paint.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ── LoRA Browser ─────────────────────────────────────────────────────

_register("lora_browser", {
  entryId: "loras",
  label: _t("showme.tutorialLabel.loraBrowser", "LoRA Browser"),
  run: function () {
    _startTour({
      id: "showme-loras",
      steps: [
        {
          id: "slb_intro",
          text: _t("showme.lora.intro.text", "The <em>LoRAs</em> block under the prompt box keeps your LoRAs as a list, so you never type the syntax by hand.\n\nPress <strong>+ LoRAs</strong> to open the LoRA browser."),
          beforeShow: _goTo('[data-panel="generate"]', 200),
          spotlight: "#loraStackAddBtn", advance: "click", btn: _t("showme.lora.intro.btn", "Next")
        },
        {
          id: "slb_browse",
          text: _t("showme.lora.browse.text", "The browser shows a card for each LoRA file in your LoRA folder (set in <em>Settings → General → Model folders</em>). Cards show the name and <em>trigger words</em> (read from sidecar <code>.json</code> files).\n\nUse the search bar to filter by name or trigger word."),
          spotlight: null, advance: "click", btn: _t("showme.lora.browse.btn", "Next"),
          position: { bottom: "80px", right: "340px" }
        },
        {
          id: "slb_insert",
          text: _t("showme.lora.insert.text", "Click a card to add it to the list at its preferred weight. When you press Generate, Studio adds the <code>&lt;lora:name:weight&gt;</code> tags and any trigger words to the prompt for you — <em>Preview</em> shows exactly what will be sent.\n\nYou can stack several; lower the weights (0.5–0.7) when you do. <em>Import</em> moves <code>&lt;lora:…&gt;</code> tags you typed into the list."),
          spotlight: "#loraStackPreviewBtn", advance: "click", btn: _t("showme.lora.insert.btn", "Got it")
        },
      ],
    });
  }
});

// ── Brush Dynamics ───────────────────────────────────────────────────

_register("brush_dynamics", {
  entryId: "tool_brush",
  label: _t("showme.tutorialLabel.brushDynamics", "Brush Dynamics"),
  run: function () {
    _createTutorialDoc(_t("showme.tutorialName.brushDynamics", "Brush Dynamics"));
    window.StudioCore.composite();
    window.StudioUI.redraw();

    _startTour({
      id: "showme-dynamics",
      steps: [
        {
          id: "sbd_intro",
          text: _t("showme.dynamics.intro.text", "The brush engine has a <em>dynamics panel</em> that controls how a stroke is built.\n\nSelect the <em>Brush</em> (<span class=\"cx-kbd\">B</span>) and click the <strong>gear icon</strong> at the end of the brush presets (highlighted)."),
          beforeShow: _goTo('#toolstrip [data-tool="brush"]', 200),
          spotlight: "#dynamicsToggle", advance: "click", btn: _t("showme.dynamics.intro.btn", "Next")
        },
        {
          id: "sbd_panel",
          text: _t("showme.dynamics.panel.text", "The panel has three groups:\n\n• <strong>Stroke</strong> — <em>Follow stroke</em>, <em>Buildup</em> (stamps accumulate), <em>Spacing</em>, <em>Size / Opacity / Rotation Jitter</em>, <em>Scatter</em>\n• <strong>Brush Shape</strong> — <em>Ratio</em>, <em>Density</em>, <em>Spikes</em>, <em>Angle</em>, <em>Taper in</em>, <em>Falloff</em> (Std or Gauss)\n• <strong>Paper</strong> — a surface texture (Fine Grain, Canvas, Rough Tooth, Laid) with <em>Depth</em>, <em>Scale</em> and <em>Tooth</em>; it belongs to the document, so every brush paints on the same surface"),
          spotlight: null, advance: "click", btn: _t("showme.dynamics.panel.btn", "Next"),
          position: { top: "120px", left: "60px" }
        },
        {
          id: "sbd_try",
          text: _t("showme.dynamics.try.text", "Try painting with different settings. High scatter + size jitter creates foliage and particle effects. High spacing creates dotted lines. A paper surface makes strokes break up like chalk.\n\nThe <strong>Scatter</strong> preset is pre-configured for textured spray. Each preset stores its own dynamics."),
          spotlight: null, advance: "click", btn: _t("showme.dynamics.try.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ── Wildcards ────────────────────────────────────────────────────────

_register("wildcards_tutorial", {
  entryId: "wildcards",
  label: "Show Me",
  run: function () {
    _startTour({
      id: "showme-wildcards",
      steps: [
        {
          id: "sw_intro",
          text: _t("showme.wildcards.intro.text", "Wildcards insert <em>random text</em> into your prompts for automatic variety. The syntax is <code>__name__</code> (double underscores).\n\nEach generation picks a different line from the matching text file."),
          spotlight: null, advance: "click", btn: _t("showme.wildcards.intro.btn", "Next"),
          position: CENTER
        },
        {
          id: "sw_browse",
          text: _t("showme.wildcards.browse.text", "Click the <strong>WILDCARD</strong> button under the prompt box to open the wildcard browser. It lists your wildcard files.\n\nThey live in the wildcard folder you choose in <em>Settings → Prompts & Metadata → Dynamic Prompts</em>. Each <code>.txt</code> file is one wildcard — the filename becomes the name."),
          spotlight: null, advance: "click", btn: _t("showme.wildcards.browse.btn", "Next"),
          position: { bottom: "80px", right: "340px" }
        },
        {
          id: "sw_syntax",
          text: _t("showme.wildcards.syntax.text", "Try typing in your prompt: <code>a __colors__ cat sitting on a __furniture__</code>\n\nEach generation replaces <code>__colors__</code> with a random line from <code>colors.txt</code> and <code>__furniture__</code> from <code>furniture.txt</code>.\n\n<strong>Folders work too:</strong> a file at <code>animals/cats.txt</code> is used as <code>__animals/cats__</code>."),
          spotlight: "#paramPrompt", advance: "click", btn: _t("showme.wildcards.syntax.btn", "Next"),
          focusTarget: "#paramPrompt"
        },
        {
          id: "sw_lexicon",
          text: _t("showme.wildcards.lexicon.text", "The <em>Wildcards</em> tab (in the main tab bar) is a full wildcard editor — folder tree, file creation, inline editing, preview, sorting and duplicate removal.\n\nWildcards work in the negative prompt too. Common pattern: put your standard negatives in <code>neg4.txt</code> and use <code>__neg4__</code> instead of typing them every time."),
          spotlight: null, advance: "click", btn: _t("showme.wildcards.lexicon.btn", "Got it"),
          position: CENTER
        },
      ],
    });
  }
});

// ========================================================================
// PUBLIC API
// ========================================================================

window.StudioShowMe = {
  has: has,
  forEntry: forEntry,
  run: run
};

console.log(TAG, "Show Me system loaded —", Object.keys(_tutorials).length, "tutorials registered");

})();
