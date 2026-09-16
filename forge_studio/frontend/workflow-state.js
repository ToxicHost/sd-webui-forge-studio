/**
 * Forge Studio — Shared Generation State Snapshot
 * by ToxicHost & Moritz
 *
 * Single source of truth for "the user's current Generate-panel setup":
 *   - StudioDocs uses this for per-tab snapshot/restore.
 *   - StudioWorkflows uses this for save/apply of named profiles.
 *
 * Stable schema keys are used for storage so a future rename of a DOM id
 * does not invalidate saved profiles.
 *
 * Loaded BEFORE studio-docs.js. Depends only on basic DOM and the optional
 * StudioSearchableSelect + ExtensionBridge globals (gracefully degrades
 * when those aren't present).
 *
 * Tab-switch applies (loadModel omitted/false) never trigger a model/VAE/TE
 * load: we set `.value` directly and refresh the searchable-select label so
 * the dropdown reads correctly without kicking off an async load — the
 * paramModel/VAE/TE change-listeners only fire on real `change` events, not
 * programmatic `.value` writes. Explicit workflow apply (loadModel: true)
 * DOES load the selected components via the shared helper
 * window.loadSelectedModelComponents("workflow-apply"). Either way, the
 * generation preflight in app.js reconciles UI vs. Forge's loaded state
 * before each Generate, so any remaining mismatch self-corrects by then.
 */
(function () {
"use strict";

var SCHEMA_VERSION = 1;

function _t(key, fallback, params) {
  return (window.I18N && window.I18N.t)
    ? window.I18N.t(key, fallback, params)
    : fallback;
}

// Type semantics for FIELD_MAP entries:
//   "value"          — <input>/<select>, set via .value
//   "textarea"       — <textarea>, set via .value
//   "number"         — numeric .value, JSON-stored as a float
//   "int"            — numeric .value, JSON-stored as an integer
//   "collapseCheck"  — div.collapse-check: .classList toggles "checked"
//   "onClass"        — div.toggle-track:   .classList toggles "on"
//   "checkbox"       — real <input type=checkbox>: .checked property
//   "hidden"         — hidden <input>: .value (e.g. AR pool JSON strings)
//
// Optional flags:
//   dimension: true  — entry is skipped when applyDimensions === false
//   model:     true  — entry is a checkpoint/VAE/TE select. Apply writes
//                      the value silently (no change dispatch), then at
//                      the end of the apply pass dispatches `change`
//                      exactly once on whichever model field actually
//                      changed — paramModel cascades to VAE+TE inside the
//                      existing load listener (app.js:2905), so we don't
//                      double-load. Only fires when options.loadModel.
//   tabExclude: true — entry is skipped during captureWorkflowState when
//                      mode === "tab". Model fields use this so tab
//                      switches never reload the global model.
//   prompt:    true  — entry is the positive prompt textarea
//   negPrompt: true  — entry is the negative prompt textarea

var FIELD_MAP = {
  // Prompts ----------------------------------------------------------------
  prompt:          { id: "paramPrompt",   type: "textarea", prompt: true },
  negative_prompt: { id: "paramNeg",      type: "textarea", negPrompt: true },
  // LoRA stack is a JSON-serialized [{name, weight, enabled}] array in a
  // hidden input. Flagged `prompt: true` so it follows the positive prompt's
  // include/exclude rules in workflow save dialogs.
  lora_stack:      { id: "paramLoraStack", type: "hidden", prompt: true },

  // Model / VAE / TE -------------------------------------------------------
  // Excluded from tab snapshots: tab switching never reloads the global
  // model. They are still captured/applied via workflow profiles.
  model:           { id: "paramModel",       type: "value", model: true, tabExclude: true },
  vae:             { id: "paramVAE",         type: "value", model: true, tabExclude: true },
  text_encoder:    { id: "paramTextEncoder", type: "value", model: true, tabExclude: true },

  // Sampling ---------------------------------------------------------------
  sampler:    { id: "paramSampler",   type: "value" },
  scheduler:  { id: "paramScheduler", type: "value" },
  steps:      { id: "paramSteps",     type: "int" },
  cfg:        { id: "paramCFG",       type: "number" },
  denoise:    { id: "paramDenoise",   type: "number" },

  // Dimensions -------------------------------------------------------------
  width:      { id: "paramWidth",  type: "int", dimension: true },
  height:     { id: "paramHeight", type: "int", dimension: true },

  // Seed / batch -----------------------------------------------------------
  seed:                    { id: "paramSeed",            type: "int" },
  variation_seed:          { id: "paramVarSeed",         type: "int" },
  variation_strength:      { id: "paramVarStrength",     type: "number" },
  variation_strength_val:  { id: "paramVarStrengthVal",  type: "number" },
  resize_seed_w:           { id: "paramResizeSeedW",     type: "int" },
  // resize_seed_h was missing. Neo ignores the resize-from pair unless BOTH
  // dimensions are > 0, so a workflow that restored only the width restored a
  // half-configuration that silently did nothing -- and the owner would have
  // seen their width come back and reasonably assumed the height had too.
  resize_seed_h:           { id: "paramResizeSeedH",     type: "int" },

  // Hires ------------------------------------------------------------------
  hires_enabled:    { id: "checkHires",        type: "collapseCheck" },
  hires_upscaler:   { id: "paramHrUpscaler",   type: "value" },
  hires_scale:      { id: "paramHrScale",      type: "number" },
  hires_steps:      { id: "paramHrSteps",      type: "int" },
  hires_denoise:    { id: "paramHrDenoise",    type: "number" },
  hires_cfg:        { id: "paramHrCFG",        type: "number" },
  hires_checkpoint: { id: "paramHrCheckpoint", type: "value" },

  // ADetailer master + 3 slots --------------------------------------------
  ad_enabled:        { id: "checkAD",         type: "collapseCheck" },
  ad1_enabled:       { id: "checkAD1",        type: "collapseCheck" },
  ad1_model:         { id: "paramAD1Model",   type: "value" },
  ad1_conf:          { id: "paramAD1Conf",    type: "number" },
  ad1_denoise:       { id: "paramAD1Denoise", type: "number" },
  ad1_blur:          { id: "paramAD1Blur",    type: "number" },
  ad1_prompt:        { id: "paramAD1Prompt",  type: "textarea" },
  ad1_loras:         { id: "adLoraStack1",    type: "hidden" },
  ad2_enabled:       { id: "checkAD2",        type: "collapseCheck" },
  ad2_model:         { id: "paramAD2Model",   type: "value" },
  ad2_conf:          { id: "paramAD2Conf",    type: "number" },
  ad2_denoise:       { id: "paramAD2Denoise", type: "number" },
  ad2_blur:          { id: "paramAD2Blur",    type: "number" },
  ad2_prompt:        { id: "paramAD2Prompt",  type: "textarea" },
  ad2_loras:         { id: "adLoraStack2",    type: "hidden" },
  ad3_enabled:       { id: "checkAD3",        type: "collapseCheck" },
  ad3_model:         { id: "paramAD3Model",   type: "value" },
  ad3_conf:          { id: "paramAD3Conf",    type: "number" },
  ad3_denoise:       { id: "paramAD3Denoise", type: "number" },
  ad3_blur:          { id: "paramAD3Blur",    type: "number" },
  ad3_prompt:        { id: "paramAD3Prompt",  type: "textarea" },
  ad3_loras:         { id: "adLoraStack3",    type: "hidden" },

  // ControlNet master + 2 units (image bytes intentionally omitted) -------
  cn_enabled:        { id: "checkCN",         type: "collapseCheck" },
  cn1_enabled:       { id: "checkCN1",        type: "collapseCheck" },
  cn1_module:        { id: "paramCN1Module",  type: "value" },
  cn1_model:         { id: "paramCN1Model",   type: "value" },
  cn1_source:        { id: "paramCN1Source",  type: "value" },
  cn1_weight:        { id: "paramCN1Weight",  type: "number" },
  cn1_start:         { id: "paramCN1Start",   type: "number" },
  cn1_end:           { id: "paramCN1End",     type: "number" },
  cn1_mode:          { id: "paramCN1Mode",    type: "value" },
  cn2_enabled:       { id: "checkCN2",        type: "collapseCheck" },
  cn2_module:        { id: "paramCN2Module",  type: "value" },
  cn2_model:         { id: "paramCN2Model",   type: "value" },
  cn2_source:        { id: "paramCN2Source",  type: "value" },
  cn2_weight:        { id: "paramCN2Weight",  type: "number" },
  cn2_start:         { id: "paramCN2Start",   type: "number" },
  cn2_end:           { id: "paramCN2End",     type: "number" },
  cn2_mode:          { id: "paramCN2Mode",    type: "value" },

  // Inpaint ----------------------------------------------------------------
  inpaint_area:      { id: "paramInpaintArea", type: "value" },
  inpaint_fill:      { id: "paramFill",        type: "value" },
  mask_blur:         { id: "paramMaskBlur",    type: "int" },
  inpaint_padding:   { id: "paramPadding",     type: "int" },

  // Soft inpaint -----------------------------------------------------------
  soft_inpaint_enabled: { id: "checkSoftInpaint",      type: "collapseCheck" },
  soft_bias:            { id: "paramSoftBias",         type: "number" },
  soft_preserve:        { id: "paramSoftPreserve",     type: "number" },
  soft_contrast:        { id: "paramSoftContrast",     type: "number" },
  soft_mask_inf:        { id: "paramSoftMaskInf",      type: "number" },
  soft_diff_thresh:     { id: "paramSoftDiffThresh",   type: "number" },
  soft_diff_contrast:   { id: "paramSoftDiffContrast", type: "number" },

  // AR randomizer (hidden state) ------------------------------------------
  ar_rand_base:        { id: "arRandBase",        type: "checkbox" },
  ar_rand_ratio:       { id: "arRandRatio",       type: "checkbox" },
  ar_rand_orientation: { id: "arRandOrientation", type: "checkbox" },
  ar_base_pool:        { id: "arBasePoolData",    type: "hidden" },
  ar_ratio_pool:       { id: "arRatioPoolData",   type: "hidden" },

  // Output settings (live in Settings panel but rebuilt per workflow) -----
  save_outputs:    { id: "toggleSaveOutputs",   type: "onClass" },
  live_preview:    { id: "toggleLivePreview",   type: "onClass" },
  embed_metadata:  { id: "toggleMetadata",      type: "onClass" },
  save_format:     { id: "settingSaveFormat",   type: "value" },
  jpeg_quality:    { id: "settingJpegQuality",  type: "int" },
  webp_quality:    { id: "settingWebpQuality",  type: "int" },
  webp_lossless:   { id: "toggleWebpLossless",  type: "onClass" },

  // Extra-args expand toggle (real checkbox) ------------------------------
  extra_open:      { id: "checkExtra",          type: "checkbox" },
};

// ---------------------------------------------------------------------------
// Capture
// ---------------------------------------------------------------------------

function _readField(spec) {
  var el = document.getElementById(spec.id);
  if (!el) return undefined;
  switch (spec.type) {
    case "value":
    case "hidden":
    case "textarea":
      return el.value;
    case "number": {
      var n = parseFloat(el.value);
      return Number.isFinite(n) ? n : el.value;
    }
    case "int": {
      var i = parseInt(el.value, 10);
      return Number.isFinite(i) ? i : el.value;
    }
    case "collapseCheck":
      return el.classList.contains("checked");
    case "onClass":
      return el.classList.contains("on");
    case "checkbox":
      return !!el.checked;
  }
  return undefined;
}

function captureWorkflowState(options) {
  options = options || {};
  var includePrompt = options.includePrompt !== false;
  var includeNegativePrompt = options.includeNegativePrompt !== false;
  var includeDimensions = options.includeDimensions !== false;
  var isTab = options.mode === "tab";

  var settings = {};
  for (var key in FIELD_MAP) {
    if (!Object.prototype.hasOwnProperty.call(FIELD_MAP, key)) continue;
    var spec = FIELD_MAP[key];
    if (spec.prompt && !includePrompt) continue;
    if (spec.negPrompt && !includeNegativePrompt) continue;
    if (spec.dimension && !includeDimensions) continue;
    if (spec.tabExclude && isTab) continue;
    var v = _readField(spec);
    if (v !== undefined) settings[key] = v;
  }

  var dynamic = {};
  try {
    var EB = window.ExtensionBridge;
    if (EB && typeof EB.collectArgs === "function") {
      dynamic.extension_args = EB.collectArgs() || {};
    }
  } catch (e) { /* ignore */ }

  return {
    version: SCHEMA_VERSION,
    settings: settings,
    dynamic: dynamic,
  };
}

// ---------------------------------------------------------------------------
// Apply
// ---------------------------------------------------------------------------

function _normalize(workflowOrSnapshot) {
  if (!workflowOrSnapshot || typeof workflowOrSnapshot !== "object") return null;
  // Workflow shape: { settings: {...} }
  if (workflowOrSnapshot.settings && typeof workflowOrSnapshot.settings === "object") {
    return {
      settings: workflowOrSnapshot.settings,
      dynamic: workflowOrSnapshot.dynamic || {},
    };
  }
  // Bare snapshot from older StudioDocs payloads: flat { paramSteps: 20, ... }
  // Convert by reverse-lookup: any key matching a known DOM id maps to that
  // field's schema name.
  var idToKey = {};
  for (var key in FIELD_MAP) {
    if (!Object.prototype.hasOwnProperty.call(FIELD_MAP, key)) continue;
    idToKey[FIELD_MAP[key].id] = key;
  }
  var converted = {};
  for (var k in workflowOrSnapshot) {
    if (!Object.prototype.hasOwnProperty.call(workflowOrSnapshot, k)) continue;
    if (idToKey[k]) converted[idToKey[k]] = workflowOrSnapshot[k];
  }
  return { settings: converted, dynamic: {} };
}

function _refreshSearchable(el) {
  try {
    var SS = window.StudioSearchableSelect;
    if (SS && typeof SS.attach === "function") {
      var handle = SS.attach(el);
      if (handle && typeof handle.refresh === "function") handle.refresh();
    }
  } catch (e) { /* ignore */ }
}

function _writeField(spec, value) {
  var el = document.getElementById(spec.id);
  if (!el) return { skipped: true };
  switch (spec.type) {
    case "value": {
      var prev = el.value;
      el.value = value;
      if (spec.model) {
        // Write silently — apply pass dispatches `change` exactly once
        // at the end on whichever model field actually changed, so we
        // never trigger duplicate reloads from the listeners at
        // app.js:2905 (paramModel) / :2979 (VAE) / :3018 (TE).
        _refreshSearchable(el);
        return { applied: true, model: true, changed: prev !== el.value };
      }
      if (el.tagName === "SELECT") {
        _refreshSearchable(el);
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return { applied: true };
    }
    case "hidden":
      el.value = value == null ? "" : String(value);
      // Notify listeners (e.g. LoraStack) that the hidden value changed.
      // Pre-existing hidden inputs (arBasePoolData, arRatioPoolData) have
      // no input listeners so this is a no-op for them.
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return { applied: true };
    case "textarea":
      el.value = value == null ? "" : String(value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return { applied: true };
    case "number":
    case "int":
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { applied: true };
    case "collapseCheck":
      el.classList.toggle("checked", !!value);
      return { applied: true };
    case "onClass":
      el.classList.toggle("on", !!value);
      return { applied: true };
    case "checkbox":
      el.checked = !!value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { applied: true };
  }
  return { skipped: true };
}

function _syncStateFlags() {
  // App-level generation flags live on window.State (defined in app.js).
  // StudioCore.state holds engine state — notably `livePreview` there is
  // an object { canvas, ctx, active }, NOT a boolean. Writing the toggle
  // value there clobbered the engine object and crashed resetCanvasState.
  var AppState = window.State;
  if (!AppState) return;
  var saveOutputs = document.getElementById("toggleSaveOutputs");
  var livePreview = document.getElementById("toggleLivePreview");
  var embedMeta   = document.getElementById("toggleMetadata");
  if (saveOutputs) AppState.saveOutputs   = saveOutputs.classList.contains("on");
  if (livePreview) AppState.livePreview   = livePreview.classList.contains("on");
  if (embedMeta)   AppState.embedMetadata = embedMeta.classList.contains("on");
  // Defense in depth: authoritatively re-read output format + independent
  // JPEG/WebP quality from the DOM after a workflow/tab restore, so event
  // order among the slider writes can never leave stale/crossed quality.
  try {
    window.StudioOutputSettings?.syncFromDOM?.();
  } catch (e) {
    console.warn("[WorkflowState] Failed to sync output settings", e);
  }
}

// THE SINGLE AUTHORITATIVE RESIZE for a batch of field writes.
//
// It always intended to be that. What it was not, until AR4.5, was the ONLY
// one: `_writeField` dispatches `change` per field, and app.js binds a handler
// to `paramWidth`/`paramHeight` that resizes the canvas immediately from
// whatever the two inputs currently hold. Width and height are separate
// fields, so switching between documents of different sizes drove the canvas
// through NEW-width x OLD-height first:
//
//     resizeCanvas(192, 320)  from 192x448   <- the intermediate, destructive
//     resizeCanvas(192, 448)  from 192x320
//     resizeCanvas(192, 448)  from 192x448   <- this function, now a no-op
//
// Two resamples, one of them to a size that is neither document's. Measured in
// a real browser: a crisp fillRect of exactly 20000 opaque pixels came back as
// 20400 after ONE ordinary tab switch, feathered at the edges -- and the
// damage was saved back into the document, so it compounded with every switch.
//
// The Extension has the identical code and degrades documents the same way.
// Studio diverges deliberately: parity is the target for behaviour, not for
// data loss. See `Evidence/source-review/AR4.5-tab-switch-resample.md`.
//
// This now performs the whole sync the per-field handler used to do on the
// last write, so suppressing that handler mid-batch loses nothing. Same
// operations, same order, once instead of twice.
function _maybeResizeCanvas(width, height) {
  if (!Number.isFinite(width) || !Number.isFinite(height)) return;
  if (window.StudioCore && typeof window.StudioCore.resizeCanvas === "function") {
    try {
      window.StudioCore.resizeCanvas(width, height);
      _syncCanvasChrome(width, height);
      return;
    } catch (e) { /* fall through */ }
  }
  var UI = window.StudioUI;
  if (UI && typeof UI.syncCanvasToViewport === "function") {
    var S = window.StudioCore && window.StudioCore.state;
    if (S) { S.W = width; S.H = height; }
    try { UI.syncCanvasToViewport(); } catch (e) { /* ignore */ }
  }
}

// The chrome that follows a resize: the status bar, the viewport, and the
// canvas readout. Lifted out of app.js's per-field handler so the batch tail
// can perform it exactly once.
//
// `zoomFit` is included deliberately. Today's handler already calls it on the
// last field write, so leaving it out would be a second behaviour change
// smuggled in beside this one. That the document's saved `zoom` does not
// survive a tab switch is a real but SEPARATE defect, recorded rather than
// quietly altered here.
function _syncCanvasChrome(width, height) {
  try {
    if (window.StatusBar && typeof window.StatusBar.setDimensions === "function") {
      window.StatusBar.setDimensions(width, height);
    }
    var UI = window.StudioUI;
    if (UI) {
      if (typeof UI.syncCanvasToViewport === "function") UI.syncCanvasToViewport();
      if (window.StudioCore && typeof window.StudioCore.zoomFit === "function") {
        window.StudioCore.zoomFit();
      }
      if (typeof UI.updateStatus === "function") UI.updateStatus();
      if (typeof UI.redraw === "function") UI.redraw();
    }
    var status = document.getElementById("canvasStatus");
    if (status) {
      var scale = Math.round(
        ((window.StudioCore && window.StudioCore.state
          && window.StudioCore.state.zoom && window.StudioCore.state.zoom.scale) || 1) * 100);
      status.innerHTML = width + " &times; " + height + " &ensp; " + scale + "%";
    }
  } catch (e) {
    console.warn("[WorkflowState] canvas chrome sync failed", e);
  }
}

// True while `applyWorkflowState` is writing its fields.
//
// app.js's dimension handler consults this and skips the resize, because the
// batch resizes ONCE at its tail with the final pair. Without it, writing
// width fires a resize against the OLD height. See `_maybeResizeCanvas`.
var _applyingBatch = false;

function applyWorkflowState(workflowOrSnapshot, options) {
  options = options || {};
  var silent = options.silent === true;
  var applyDimensions = options.applyDimensions !== false;
  var loadModel = options.loadModel === true;

  var norm = _normalize(workflowOrSnapshot);
  if (!norm) return { applied: 0, skipped: 0 };

  var applied = 0;
  var skipped = 0;
  var dimensionsApplied = false;
  // Track which model-class fields actually changed value so we can fire
  // a single change event at the end to trigger Forge's load pipeline.
  var modelChanged = { paramModel: false, paramVAE: false, paramTextEncoder: false };

  // `finally`, not a trailing assignment: a field write that throws must not
  // leave the flag latched, or every later hand-typed dimension would stop
  // resizing the canvas and the owner would have no way to get it back.
  _applyingBatch = true;
  try {
    for (var key in norm.settings) {
      if (!Object.prototype.hasOwnProperty.call(norm.settings, key)) continue;
      var spec = FIELD_MAP[key];
      if (!spec) continue; // unknown schema key — ignore
      if (spec.dimension && !applyDimensions) continue;
      var res = _writeField(spec, norm.settings[key]);
      if (res && res.applied) {
        applied++;
        if (spec.dimension) dimensionsApplied = true;
        if (res.model && res.changed && modelChanged[spec.id] === false) {
          modelChanged[spec.id] = true;
        }
      } else {
        skipped++;
      }
    }
  } finally {
    _applyingBatch = false;
  }

  // Side-effect tail: mirror what _applyDefaults does after writing fields.
  _syncStateFlags();
  if (dimensionsApplied) {
    var w = parseInt(norm.settings.width, 10);
    var h = parseInt(norm.settings.height, 10);
    if (Number.isFinite(w) && Number.isFinite(h)) _maybeResizeCanvas(w, h);
  }

  // Trigger model load via the shared helper rather than dispatching
  // `change` on the dropdowns. Going through the helper directly means
  // the paramModel listener's per-model TE memory restore can't clobber
  // the value the workflow just wrote — the helper's "workflow-apply"
  // reason keeps paramTextEncoder as-is. paramModel's load also handles
  // VAE + TE in a single backend call, so any of the three changing
  // funnels into one request.
  var anyModelChanged =
    modelChanged.paramModel || modelChanged.paramVAE || modelChanged.paramTextEncoder;
  if (loadModel && anyModelChanged) {
    if (typeof window.loadSelectedModelComponents === "function") {
      // Fire and forget — generation can wait on State.generating-aware
      // pending queue if it overlaps.
      try { window.loadSelectedModelComponents("workflow-apply"); }
      catch (e) { /* helper handles its own errors via toasts */ }
    } else {
      // Fallback for older builds without the helper: dispatch change
      // on whichever field changed. paramModel cascades to VAE+TE.
      var targetId = null;
      if (modelChanged.paramModel) targetId = "paramModel";
      else if (modelChanged.paramVAE) targetId = "paramVAE";
      else if (modelChanged.paramTextEncoder) targetId = "paramTextEncoder";
      if (targetId) {
        var el = document.getElementById(targetId);
        if (el) el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }

  if (!silent && typeof window.showToast === "function") {
    if (skipped > 0) {
      window.showToast(
        _t(
          "workflows.toast.appliedPartial",
          "Workflow applied with {count} unavailable settings",
          { count: skipped },
        ),
        "info",
      );
    } else {
      window.showToast(_t("workflows.toast.applied", "Workflow applied"), "success");
    }
  }

  return { applied: applied, skipped: skipped };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getCurrentDimensions() {
  var w = parseInt((document.getElementById("paramWidth") || {}).value, 10);
  var h = parseInt((document.getElementById("paramHeight") || {}).value, 10);
  return {
    width:  Number.isFinite(w) ? w : null,
    height: Number.isFinite(h) ? h : null,
  };
}

function workflowHasDimensions(wf) {
  var norm = _normalize(wf);
  if (!norm) return false;
  var s = norm.settings || {};
  return s.width != null && s.height != null;
}

window.StudioWorkflowState = {
  captureWorkflowState: captureWorkflowState,
  applyWorkflowState: applyWorkflowState,
  getCurrentDimensions: getCurrentDimensions,
  workflowHasDimensions: workflowHasDimensions,
  // Consulted by app.js's dimension handler so it yields to the batch's
  // single tail resize. AR4.5.
  isApplyingBatch: function () { return _applyingBatch; },
  _FIELD_MAP: FIELD_MAP, // exposed for diagnostics; do not rely on shape
};

})();
