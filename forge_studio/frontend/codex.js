/**
 * Forge Studio — Codex Module
 * by ToxicHost & Moritz
 *
 * In-app reference documentation. Searchable, with optional collapsible
 * technical deep-dives per entry. Task-oriented structure.
 *
 * Content model (CX1, 2026-10-18): this file holds only each entry's id,
 * title, category, tags and shortcut. The text lives in locales/en.json:
 *   codex.entry.<id>.content  — main body (clear, accessible to all skill levels)
 *   codex.entry.<id>.deep     — optional collapsible "Under the Hood" (technical detail)
 * The one exception is param_seed_batch, which keeps its text here (the Batch
 * Count removal test reads it). Edit en.json, not this file, to change a body.
 *
 * Registers via StudioModules.register("codex", {...})
 *
 * Additional credits:
 *   VRAM Unload feature suggested by SnekySnek
 *   Save Last Session feature suggested by Railer
 */
(function () {
"use strict";
var TAG = "[Codex]", VERSION = "2.1.5";

// ══════════════════════════════════════════════════════════════════
// CATEGORIES
// ══════════════════════════════════════════════════════════════════

// i18n helper — every dynamically-built string passes its English source
// through _t() so the locale-aware text shows on first paint.
function _t(key, fallback) {
  return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback) : fallback;
}

var CATEGORIES = [
  { id: "getting_started",  label: "Getting Started",     i18nKey: "codex.category.gettingStarted" },
  { id: "how_studio_works", label: "How Studio Works",    i18nKey: "codex.category.howStudioWorks" },
  { id: "canvas_tools",     label: "Canvas Tools",        i18nKey: "codex.category.canvasTools" },
  { id: "generation",       label: "Generation",          i18nKey: "codex.category.generation" },
  { id: "inpainting",       label: "Inpainting",          i18nKey: "codex.category.inpainting" },
  { id: "advanced",         label: "Advanced Features",   i18nKey: "codex.category.advancedFeatures" },
  { id: "layers",           label: "Layers & Export",     i18nKey: "codex.category.layersExport" },
  { id: "gallery",          label: "Gallery",             i18nKey: "codex.category.gallery" },
  { id: "workshop",         label: "Workshop",            i18nKey: "codex.category.workshop" },
  { id: "troubleshooting",  label: "Troubleshooting",     i18nKey: "codex.category.troubleshooting" },
  { id: "shortcuts",        label: "Keyboard Shortcuts",  i18nKey: "codex.category.keyboardShortcuts" },
];

// Translate a category label via its i18nKey, falling back to the
// English label embedded in the CATEGORIES table.
function _catLabel(cat) {
  return cat ? _t(cat.i18nKey || "", cat.label) : "";
}

// Translate an entry title via codex.entry.<id>.title, falling back to
// the English title embedded in the entry definition.
function _entryTitle(e) {
  return e ? _t("codex.entry." + e.id + ".title", e.title) : "";
}

// ══════════════════════════════════════════════════════════════════
// ENTRIES
// ══════════════════════════════════════════════════════════════════

var ENTRIES = [

  // Bodies live in locales/en.json (codex.entry.<id>.content and .deep) - that file
  // is the single source of truth. Entries here are metadata only, EXCEPT
  // param_seed_batch, whose text stays here on purpose: the Batch Count removal test
  // (tests/studio_alpha/test_batch_removal.py) reads it and asserts it has no locale key.

  // ── GETTING STARTED ────────────────────────────────────────

  { id: "core_workflow", title: "The Core Workflow", category: "getting_started", tags: ["workflow", "basics", "generate", "paint", "iterate", "loop"] },
  { id: "tutorials_and_tours", title: "Interactive Tutorials & Tours", category: "getting_started", tags: ["tutorial", "show me", "tour", "walkthrough", "learn", "first run", "beginner", "experienced", "restart", "education"] },
  { id: "first_image", title: "Making Your First Image", category: "getting_started", tags: ["first", "prompt", "generate", "beginner"] },
  { id: "not_in_studio_yet", title: "Not in Studio Yet", category: "getting_started", tags: ["unavailable", "float", "high precision", "batch", "test merge", "custom brush", "missing", "supported"] },
  { id: "ui_layout", title: "UI Layout", category: "getting_started", tags: ["layout", "interface", "panels", "toolbar", "orientation"] },
  { id: "models_and_vae", title: "Models & VAE", category: "getting_started", tags: ["model", "checkpoint", "vae", "sdxl", "sd15", "load", "download", "install", "illustrious", "flux", "gguf", "fp8", "fp16", "architecture"] },
  { id: "output_workflow", title: "Saving & Using Results", category: "getting_started", tags: ["output", "save", "result", "canvas", "gallery", "format", "defaults", "persist", "server", "metadata", "filename", "fingerprint", "hash"] },
  // ── HOW STUDIO WORKS ────────────────────────────────────────

  { id: "layouts", title: "Layouts & Customize Mode", category: "how_studio_works", tags: ["layout", "deck", "classic", "customize", "panel", "blocks", "drag", "hide", "session", "strip", "preset", "share", "json"] },
  // ── GETTING STARTED ────────────────────────────────────────

  { id: "color_picker", title: "Color Picker", category: "getting_started", tags: ["color", "picker", "hsv", "hex", "swatch"] },
  // ── HOW STUDIO WORKS ────────────────────────────────────────

  { id: "canvas_routing", title: "Canvas Routing", category: "how_studio_works", tags: ["routing", "txt2img", "img2img", "auto", "mode", "blank", "zoom", "pixel", "nearest"] },
  { id: "documents", title: "Documents & Tabs", category: "how_studio_works", tags: ["document", "documents", "tab", "tabs", "multi", "multiple", "workspace", "file", "switch", "new", "close", "rename"] },
  { id: "crash_recovery", title: "Crash Recovery", category: "how_studio_works", tags: ["recovery", "crash", "refresh", "reload", "restore", "lost", "undo", "backup", "documents"] },
  { id: "scrub_labels", title: "Scrub Labels", category: "how_studio_works", tags: ["scrub", "slider", "context", "bar", "drag", "adjust"] },
  { id: "per_tool_memory", title: "Per-Tool Settings Memory", category: "how_studio_works", tags: ["tool", "settings", "memory", "remember", "persist", "independent"] },
  { id: "shift_drag_resize", title: "Shift+Drag Brush Resize", category: "how_studio_works", tags: ["shift", "drag", "resize", "brush", "opacity", "krita"], shortcut: "Shift+Drag" },
  { id: "drag_and_drop", title: "Drag & Drop", category: "how_studio_works", tags: ["drag", "drop", "image", "load", "import", "file"] },
  { id: "send_to_canvas", title: "Send to Canvas", category: "how_studio_works", tags: ["result", "canvas", "output", "layer", "send"] },
  { id: "ar_buttons", title: "Aspect Ratio Buttons", category: "how_studio_works", tags: ["aspect", "ratio", "resolution", "base", "swap", "random", "pool"] },
  { id: "wildcards_editor", title: "The Wildcards Tab", category: "how_studio_works", tags: ["wildcards", "editor", "tab", "file", "folder", "tree", "search", "preview", "import", "export", "zip", "auto-save"] },
  { id: "session_vs_defaults", title: "Session Memory vs Workflow Defaults", category: "how_studio_works", tags: ["session", "defaults", "remember", "save", "settings", "persist", "categories", "workflow", "railer"] },
  { id: "settings_map", title: "Settings Map", category: "how_studio_works", tags: ["settings", "options", "preferences", "model folders", "save folder", "file name", "browse", "shortcuts", "updates", "heatmap", "where"] },
  { id: "workflows", title: "Workflows", category: "how_studio_works", tags: ["workflow", "preset", "profile", "apply", "new tab", "save settings", "setup"] },
  // ── CANVAS TOOLS ────────────────────────────────────────

  { id: "tool_brush", title: "Brush", category: "canvas_tools", tags: ["brush", "paint", "draw", "dynamics", "preset"], shortcut: "B" },
  { id: "tool_eraser", title: "Eraser", category: "canvas_tools", tags: ["eraser", "erase", "delete", "transparent"], shortcut: "E" },
  { id: "tool_eyedropper", title: "Eyedropper", category: "canvas_tools", tags: ["eyedropper", "color", "pick", "sample", "average", "radius"], shortcut: "I" },
  { id: "tools_touchup", title: "Touch-Up Tools", category: "canvas_tools", tags: ["smudge", "blur", "dodge", "burn", "pixelate", "touchup", "touch-up", "blend", "soften", "lighten", "darken", "censor"], shortcut: "S / R / J / P" },
  { id: "tool_clone", title: "Clone Stamp", category: "canvas_tools", tags: ["clone", "stamp", "copy", "remove", "fix", "patch"], shortcut: "K" },
  { id: "tool_liquify", title: "Liquify", category: "canvas_tools", tags: ["liquify", "warp", "distort", "push", "anatomy"], shortcut: "Y" },
  { id: "tools_drawing", title: "Fill, Gradient, Shape & Text", category: "canvas_tools", tags: ["fill", "gradient", "shape", "text", "font", "rectangle", "ellipse", "line"], shortcut: "G / U / T" },
  { id: "tools_selection", title: "Selection Tools", category: "canvas_tools", tags: ["select", "marquee", "lasso", "polylasso", "maglasso", "magnetic", "wand", "magic"], shortcut: "M / O / L / W" },
  { id: "tool_crop", title: "Crop", category: "canvas_tools", tags: ["crop", "resize", "trim", "aspect", "preset"], shortcut: "C" },
  { id: "tool_transform", title: "Transform", category: "canvas_tools", tags: ["transform", "move", "scale", "rotate", "perspective", "warp"], shortcut: "V" },
  { id: "tool_radial_symmetry", title: "Symmetry Painting", category: "canvas_tools", tags: ["symmetry", "radial", "mirror", "kaleidoscope", "mandala", "horizontal", "vertical"] },
  { id: "canvas_menu", title: "Canvas Right-Click Menu", category: "canvas_tools", tags: ["right-click", "context menu", "menu", "open in new tab", "canvas size", "outpaint", "smart patch", "feather", "grow", "shrink", "save as", "flatten"] },
  { id: "tool_smart_patch", title: "Smart Patch, Heal & Patch", category: "canvas_tools", tags: ["patch", "smart patch", "heal", "sharpen", "blemish", "remove", "clone", "no model"] },
  { id: "tool_paint_mixer", title: "Paint Mixer", category: "canvas_tools", tags: ["mix", "paint mixer", "wet paint", "pigment", "blend colours", "brush", "amount"] },
  // ── GENERATION ────────────────────────────────────────

  { id: "param_prompt", title: "Writing Effective Prompts", category: "generation", tags: ["prompt", "text", "describe", "token", "emphasis", "weight", "structure", "chunk", "break", "schedule", "and", "alternating"] },
  { id: "anima", title: "Anima", category: "generation", tags: ["anima", "weights", "emphasis", "break", "text encoder", "vae", "heatmap", "family"] },
  { id: "jobs_queue", title: "Jobs & Queue", category: "generation", tags: ["jobs", "queue", "cancel", "interrupt", "skip", "waiting", "batch", "several images", "clear queued"] },
  { id: "param_neg", title: "Negative Prompt", category: "generation", tags: ["negative", "prompt", "exclude", "avoid"] },
  { id: "param_cfg", title: "CFG Scale", category: "generation", tags: ["cfg", "guidance", "creativity", "adherence"] },
  { id: "param_steps", title: "Steps", category: "generation", tags: ["steps", "quality", "speed", "iterations"] },
  { id: "param_denoise", title: "Denoise Strength", category: "generation", tags: ["denoise", "strength", "img2img", "change", "canvas"] },
  { id: "param_sampler", title: "Sampler & Scheduler", category: "generation", tags: ["sampler", "scheduler", "karras", "dpm", "euler", "bfs"] },
  { id: "param_dimensions", title: "Canvas Dimensions", category: "generation", tags: ["width", "height", "resolution", "aspect", "size", "portrait", "landscape"] },
  { id: "param_seed_batch", title: "Seed", category: "generation", tags: ["seed", "random", "variation", "dice", "recycle"], content: "<p><strong>Seed:</strong> -1 = random (dice button). Same seed + same settings = same image. Click the recycle button to lock the seed and make variations by tweaking prompts or other settings.</p><p><em>Extra → Variation Seed</em> lets you blend between two seeds for subtle variations.</p><p>To make several images, submit several jobs — the queue runs them in order and lets you cancel or reorder any one of them.</p>" },
  { id: "loras", title: "LoRAs", category: "generation", tags: ["lora", "style", "character", "finetune", "download", "install", "weight", "civitai", "trigger"] },
  { id: "wildcards", title: "Wildcards", category: "generation", tags: ["wildcard", "random", "dynamic", "lexicon", "template", "variety"] },
  // ── INPAINTING ────────────────────────────────────────

  { id: "inpaint_basics", title: "How to Edit Parts of Your Image", category: "inpainting", tags: ["inpaint", "mask", "edit", "fix", "fill", "soft", "workflow", "partial", "selective"] },
  { id: "masks_panel", title: "Masks Panel", category: "inpainting", tags: ["masks", "mask", "named mask", "inpaint", "selection", "load as selection", "soft mask", "opacity", "hardness", "psd"] },
  { id: "masks_from_words", title: "Masks From Words, Tones & Colour", category: "inpainting", tags: ["word mask", "from word", "from range", "highlights", "shadows", "midtones", "colour range", "heatmap", "mobilesam", "object"] },
  { id: "outpaint_canvas_size", title: "Outpaint & Canvas Size", category: "inpainting", tags: ["outpaint", "extend", "canvas size", "resize canvas", "grow", "trim", "anchor", "right-click"] },
  // ── GENERATION ────────────────────────────────────────

  { id: "hires_fix", title: "HiRes Fix", category: "generation", tags: ["hires", "upscale", "upscaler", "detail", "resolution", "two-pass"] },
  { id: "live_painting", title: "Live Painting", category: "generation", tags: ["live", "painting", "realtime", "real-time", "paint", "loop", "iterate", "img2img", "krita", "apply"] },
  { id: "adetailer", title: "ADetailer", category: "generation", tags: ["adetailer", "face", "detail", "yolo", "hand", "detect", "auto"] },
  // ── ADVANCED FEATURES ────────────────────────────────────────

  { id: "regional_prompting", title: "Regional Prompting", category: "advanced", tags: ["regional", "regions", "spatial", "zone", "area", "multi", "couple", "masked prompts", "one pass per region"] },
  { id: "attention_couple", title: "Attention Couple (Architecture)", category: "advanced", tags: ["attention", "couple", "regional", "bounded", "self-attention", "cross-attention", "architecture", "softmax", "bias"] },
  { id: "controlnet", title: "ControlNet", category: "advanced", tags: ["controlnet", "pose", "depth", "edge", "canny", "openpose", "lineart", "union", "lllite", "preprocessor", "structure"] },
  { id: "word_detail", title: "Auto Detail: Word Detail", category: "advanced", tags: ["word detail", "auto detail", "adetailer", "words", "heatmap", "detail a word", "slot"] },
  { id: "develop", title: "Develop", category: "advanced", tags: ["develop", "exposure", "contrast", "highlights", "shadows", "temperature", "tint", "saturation", "vibrance", "curve", "hsl", "color grading", "local adjustments", "tone"] },
  // ── LAYERS & EXPORT ────────────────────────────────────────

  { id: "layers_basics", title: "Working with Layers", category: "layers", tags: ["layers", "stack", "blend", "opacity", "reference", "paint"] },
  { id: "psd_export", title: "PSD Export", category: "layers", tags: ["psd", "export", "photoshop", "krita", "gimp"] },
  // ── GALLERY ────────────────────────────────────────

  { id: "gallery_overview", title: "Gallery", category: "gallery", tags: ["gallery", "browse", "search", "images", "output", "trackimage", "folder", "tag", "character", "scan", "watcher", "link"] },
  { id: "gallery_organize", title: "Organizing Images", category: "gallery", tags: ["organize", "select", "bulk", "rename", "move", "delete", "trash", "undo", "convert", "metadata", "strip", "drag", "clipboard", "copy", "cut", "paste", "tag"] },
  { id: "gallery_words", title: "Word Heatmaps", category: "gallery", tags: ["heatmap", "words", "attention", "word", "map", "text used", "strength", "record"] },
  { id: "gallery_duplicates", title: "Finding Duplicates", category: "gallery", tags: ["duplicate", "duplicates", "similar", "perceptual", "hash", "cleanup", "imagehash"] },
  // ── WORKSHOP ────────────────────────────────────────

  { id: "workshop_overview", title: "Workshop", category: "workshop", tags: ["workshop", "merge", "bake", "model", "checkpoint", "overview"] },
  { id: "workshop_methods", title: "Merge Methods", category: "workshop", tags: ["weighted sum", "slerp", "add difference", "ties", "dare", "della", "star", "svd", "cosine", "model stock", "method"] },
  { id: "workshop_block_weights", title: "Block Weights", category: "workshop", tags: ["block", "weight", "layer", "input", "middle", "output", "attention"] },
  { id: "workshop_lora_bake", title: "LoRA Baking", category: "workshop", tags: ["lora", "bake", "merge", "checkpoint", "adapter", "loha", "lokr", "oft", "boft", "glora", "dora"] },
  { id: "workshop_vae_bake", title: "VAE Baking", category: "workshop", tags: ["vae", "bake", "checkpoint", "color"] },
  { id: "workshop_journal", title: "Journal & Recipes", category: "workshop", tags: ["journal", "history", "recipe", "rating", "tag", "note", "chain"] },
  // ── TROUBLESHOOTING ────────────────────────────────────────

  { id: "common_problems", title: "Common Problems", category: "troubleshooting", tags: ["problem", "error", "black", "artifact", "broken", "fix", "slow", "load", "seed"] },
  { id: "vram_management", title: "VRAM & Performance", category: "troubleshooting", tags: ["vram", "memory", "gpu", "unload", "low", "snekysnek"] },
  // ── KEYBOARD SHORTCUTS ────────────────────────────────────────

  { id: "shortcuts_tools", title: "Tool Shortcuts", category: "shortcuts", tags: ["shortcut", "keyboard", "hotkey", "tool"] },
  { id: "shortcuts_canvas", title: "Canvas & Editing Shortcuts", category: "shortcuts", tags: ["shortcut", "keyboard", "undo", "zoom", "pan", "clipboard", "delete", "backspace", "reset", "tab", "document"] },

];


// ══════════════════════════════════════════════════════════════════
// ENGINE
// ══════════════════════════════════════════════════════════════════

var _selectedEntry = null, _els = {};

function _search(q) {
  if (!q.trim()) return ENTRIES;
  var t = q.toLowerCase().split(/\s+/);
  return ENTRIES.filter(function (e) {
    // Search corpus: translated title + English title (so users can match
    // either localized or English vocabulary), tags, category, shortcut.
    var s = (_entryTitle(e) + " " + e.title + " " + e.tags.join(" ") + " " + e.category + " " + (e.shortcut || "")).toLowerCase();
    return t.every(function (w) { return s.indexOf(w) >= 0; });
  });
}

function _buildUI(c) {
  c.innerHTML = '<div class="cx-layout"><div class="cx-nav"><div class="cx-search-wrap"><input type="text" class="cx-search" id="cxSearch" data-i18n-placeholder="codex.search.placeholder" placeholder="' + _t("codex.search.placeholder", "Search docs...") + '" autocomplete="off"></div><div class="cx-tree" id="cxTree"></div></div><div class="cx-content" id="cxContent"></div></div>';
  _els.search = c.querySelector("#cxSearch");
  _els.tree = c.querySelector("#cxTree");
  _els.content = c.querySelector("#cxContent");
  _renderTree(ENTRIES);
  _els.search.addEventListener("input", function () {
    var q = _els.search.value;
    _renderTree(_search(q), q.trim().length > 0);
  });
  _renderWelcome();
}

function _renderTree(entries, forceOpen) {
  var html = "";
  var isSearch = forceOpen === true;
  CATEGORIES.forEach(function (cat) {
    var ce = entries.filter(function (e) { return e.category === cat.id; });
    if (!ce.length) return;
    var hasActive = _selectedEntry && ce.some(function (e) { return e.id === _selectedEntry.id; });
    var open = isSearch || hasActive;
    html += '<div class="cx-category' + (open ? ' open' : '') + '" data-cat="' + cat.id + '">';
    // Label gets its own span so applyToDom() can re-translate just the
    // text without clobbering the leading arrow span on locale switch.
    html += '<div class="cx-cat-title" data-cat-toggle="' + cat.id + '"><span class="cx-cat-arrow">\u25b6</span><span data-i18n="' + (cat.i18nKey || "") + '">' + _catLabel(cat) + '</span></div>';
    html += '<div class="cx-cat-entries">';
    ce.forEach(function (e) {
      var a = (_selectedEntry && _selectedEntry.id === e.id) ? " active" : "";
      var sc = e.shortcut ? ' <span style="float:right;color:var(--text-4);font-family:var(--mono);font-size:9px;">' + e.shortcut + '</span>' : "";
      // Title in its own data-i18n span so applyToDom() retranslates on
      // locale switch without clobbering the trailing shortcut hint.
      html += '<button class="cx-entry' + a + '" data-id="' + e.id + '"><span data-i18n="codex.entry.' + e.id + '.title">' + _entryTitle(e) + '</span>' + sc + '</button>';
    });
    html += '</div></div>';
  });
  if (!entries.length) html = '<div class="cx-no-results" data-i18n="codex.popover.empty">' + _t("codex.popover.empty", "No matching entries") + '</div>';
  _els.tree.innerHTML = html;
  // Category toggle
  _els.tree.querySelectorAll(".cx-cat-title").forEach(function (t) {
    t.addEventListener("click", function () {
      var cat = t.closest(".cx-category");
      if (cat) cat.classList.toggle("open");
    });
  });
  // Entry click
  _els.tree.querySelectorAll(".cx-entry").forEach(function (b) {
    b.addEventListener("click", function () {
      var e = ENTRIES.find(function (x) { return x.id === b.dataset.id; });
      if (e) _selectEntry(e);
    });
  });
}

function _selectEntry(e) {
  _selectedEntry = e;
  // Auto-expand the selected entry's category, collapse others
  _els.tree.querySelectorAll(".cx-category").forEach(function (cat) {
    if (cat.dataset.cat === e.category) cat.classList.add("open");
  });
  _els.tree.querySelectorAll(".cx-entry").forEach(function (b) { b.classList.toggle("active", b.dataset.id === e.id); });
  var cat = CATEGORIES.find(function (c) { return c.id === e.category; });
  var content = _t("codex.entry." + e.id + ".content", e.content || "");
  var sc = e.shortcut ? '<p style="margin-bottom:12px;"><span class="cx-kbd">' + e.shortcut + '</span></p>' : "";

  var deepHtml = "";
  // The bodies moved to the locales (CX1), so `e.deep` is unset and the
  // section never showed; the locale decides now (RG4).
  var deep = _t("codex.entry." + e.id + ".deep", e.deep || "");
  if (deep) {
    deepHtml = '<div class="cx-deep"><button class="cx-deep-toggle" onclick="this.parentElement.classList.toggle(\'open\')">' + _t("codex.deepToggle", "Under the Hood") + ' <span class="cx-deep-arrow">\u25b6</span></button><div class="cx-deep-body">' + deep + '</div></div>';
  }

  var showMeHtml = "";
  if (window.StudioShowMe && StudioShowMe.has(e.id)) {
    var variants = StudioShowMe.forEntry(e.id);
    showMeHtml = '<div class="cx-showme">';
    variants.forEach(function (v) {
      showMeHtml += '<button class="cx-showme-btn" data-showme="' + v.id + '">' + v.label + '</button>';
    });
    showMeHtml += '</div>';
  }

  _els.content.innerHTML = '<div class="cx-article"><div class="cx-article-category" data-i18n="' + (cat ? (cat.i18nKey || "") : "") + '">' + _catLabel(cat) + '</div><div class="cx-article-title" data-i18n="codex.entry.' + e.id + '.title">' + _entryTitle(e) + '</div>' + sc + showMeHtml + '<div class="cx-article-body">' + content + '</div>' + deepHtml + '</div>';

  // Wire Show Me buttons
  _els.content.querySelectorAll("[data-showme]").forEach(function (btn) {
    btn.addEventListener("click", function () { StudioShowMe.run(btn.dataset.showme); });
  });
}

function _renderWelcome() {
  var sc = [
    { id: "core_workflow",       label: "The Core Workflow",       desc: "Start here" },
    { id: "tutorials_and_tours", label: "Interactive Tutorials",   desc: "Learn by doing" },
    { id: "canvas_routing",      label: "Canvas Routing",          desc: "How Studio decides what to do" },
    { id: "models_and_vae",      label: "Models & VAE",            desc: "Getting started" },
    { id: "inpaint_basics",      label: "Editing Parts of Images", desc: "Inpainting guide" },
    { id: "loras",               label: "LoRAs",                   desc: "Style & character add-ons" },
    { id: "masks_panel",         label: "Masks Panel",             desc: "Named masks & inpainting" },
    { id: "workshop_overview",   label: "Workshop",                desc: "Merging & baking models" },
    { id: "common_problems",     label: "Common Problems",         desc: "Troubleshooting" },
    { id: "shortcuts_tools",     label: "Keyboard Shortcuts",      desc: "Hotkeys" },
  ];
  var h = '<div class="cx-welcome">' +
    '<div class="cx-welcome-title" data-i18n="codex.welcome.title">' + _t("codex.welcome.title", "Codex") + '</div>' +
    '<div class="cx-welcome-text" data-i18n="codex.welcome.text">' + _t("codex.welcome.text", "Reference documentation for every tool, parameter, and feature in Forge Studio.") + '</div>' +
    '<div class="cx-welcome-shortcuts">';
  sc.forEach(function (s) {
    var labelKey = "codex.welcome.shortcut." + s.id + ".label";
    var descKey = "codex.welcome.shortcut." + s.id + ".desc";
    h += '<div class="cx-welcome-shortcut" data-id="' + s.id + '">' +
      '<span class="cx-welcome-shortcut-title" data-i18n="' + labelKey + '">' + _t(labelKey, s.label) + '</span>' +
      '<span class="cx-welcome-shortcut-desc" data-i18n="' + descKey + '">' + _t(descKey, s.desc) + '</span>' +
      '</div>';
  });
  h += '</div></div>';
  _els.content.innerHTML = h;
  _els.content.querySelectorAll(".cx-welcome-shortcut").forEach(function (el) {
    el.addEventListener("click", function () {
      var e = ENTRIES.find(function (x) { return x.id === el.dataset.id; });
      if (e) _selectEntry(e);
    });
  });
}

// ══════════════════════════════════════════════════════════════════
// POPOVER — floating help panel in Studio tab
// ══════════════════════════════════════════════════════════════════

var _popover = null, _popEls = {}, _popVisible = false;

function _buildPopover() {
  var studio = document.getElementById("app-studio");
  if (!studio || document.getElementById("cxPopover")) return;

  // Ensure CSS is loaded (popover may render before Codex tab is ever opened)
  if (!document.querySelector('link[href*="codex.css"]')) {
    var l = document.createElement("link"); l.rel = "stylesheet";
    l.href = "/studio/static/codex.css?v=" + VERSION; document.head.appendChild(l);
  }

  // Toggle button — inject into toolstrip before the spacer.
  // Defensive ordering: bind the click handler IMMEDIATELY after
  // creating the element, before any other attribute or i18n work, so
  // a runtime failure later in this function can't leave us with a
  // dead toolbar button. Also guard against double-build by checking
  // for an existing #cxPopToggle.
  var toolstrip = document.getElementById("toolstrip");
  if (toolstrip && !document.getElementById("cxPopToggle")) {
    var spacer = toolstrip.querySelector(".tool-spacer");
    var btn = document.createElement("button");
    btn.className = "tool-btn cx-pop-toggle";
    btn.id = "cxPopToggle";
    btn.textContent = "?";
    btn.addEventListener("click", function (ev) { ev.preventDefault(); _togglePopover(); });
    if (spacer) toolstrip.insertBefore(btn, spacer);
    else toolstrip.appendChild(btn);
    btn.dataset.i18nTitle = "codex.popover.tooltip";
    try {
      btn.title = _t("codex.popover.tooltip", "Quick Reference (?)");
    } catch (_) { btn.title = "Quick Reference (?)"; }
  }

  // Popover container
  _popover = document.createElement("div");
  _popover.id = "cxPopover";
  _popover.className = "cx-popover";
  _popover.innerHTML =
    '<div class="cx-pop-header">' +
      '<input type="text" class="cx-pop-search" id="cxPopSearch" data-i18n-placeholder="codex.popover.search.placeholder" placeholder="' + _t("codex.popover.search.placeholder", "Search the Codex...") + '" autocomplete="off">' +
      '<button class="cx-pop-close" id="cxPopClose" data-i18n-title="codex.popover.close" title="' + _t("codex.popover.close", "Close") + '">\u00d7</button>' +
    '</div>' +
    '<div class="cx-pop-body">' +
      '<div class="cx-pop-results" id="cxPopResults" style="display:none;"></div>' +
      '<div class="cx-pop-article" id="cxPopArticle"></div>' +
    '</div>';
  studio.appendChild(_popover);

  _popEls.search = _popover.querySelector("#cxPopSearch");
  _popEls.close = _popover.querySelector("#cxPopClose");
  _popEls.results = _popover.querySelector("#cxPopResults");
  _popEls.article = _popover.querySelector("#cxPopArticle");

  _popEls.close.addEventListener("click", _hidePopover);
  _popEls.search.addEventListener("input", function () {
    var q = _popEls.search.value;
    if (q.trim().length === 0) {
      _popRenderHome();
    } else {
      _popRenderResults(_search(q));
      _popEls.results.style.display = "block";
      _popEls.article.style.display = "none";
    }
  });

  // Re-translate the toolbar button's title on locale switch.
  // applyToDom() handles this via data-i18n-title, but this is a
  // belt-and-suspenders pass that also re-binds the click handler if
  // anything ever stripped it.
  window.addEventListener("i18n:change", function () {
    var btn = document.getElementById("cxPopToggle");
    if (!btn) return;
    try { btn.title = _t("codex.popover.tooltip", "Quick Reference (?)"); } catch (_) {}
  });

  // ? key toggles popover (when not in an input)
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "?" && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      var tag = (ev.target && ev.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      ev.preventDefault();
      _togglePopover();
    } else if (ev.key === "Escape" && _popVisible) {
      _hidePopover();
    }
  });
}

function _togglePopover() {
  if (_popVisible) _hidePopover();
  else _showPopover();
}

function _showPopover() {
  if (!_popover) return;
  // CSS rule is `.cx-popover.open` — adding only `.visible` (which has
  // no matching CSS) used to leave the popover off-screen at
  // transform: translateX(-360px). Both classes get added so any
  // future stylesheet using either selector still works.
  _popover.classList.add("open");
  _popover.classList.add("visible");
  _popVisible = true;
  _popRenderHome();
  setTimeout(function () { if (_popEls.search) _popEls.search.focus(); }, 50);
}

function _hidePopover() {
  if (!_popover) return;
  _popover.classList.remove("open");
  _popover.classList.remove("visible");
  _popVisible = false;
  if (_popEls.search) _popEls.search.value = "";
}

function _popRenderHome() {
  var cats = [];
  CATEGORIES.forEach(function (cat) {
    var count = ENTRIES.filter(function (e) { return e.category === cat.id; }).length;
    if (count) cats.push('<button class="cx-pop-cat" data-cat="' + cat.id + '"><span data-i18n="' + (cat.i18nKey || "") + '">' + _catLabel(cat) + '</span> <span class="cx-pop-cat-count">' + count + '</span></button>');
  });
  _popEls.article.innerHTML = '<div class="cx-pop-home"><div class="cx-pop-home-title" data-i18n="codex.popover.home.title">' + _t("codex.popover.home.title", "Quick Reference") + '</div><div class="cx-pop-home-text" data-i18n="codex.popover.home.text">' + _t("codex.popover.home.text", "Search above or browse by category.") + '</div><div class="cx-pop-cats">' + cats.join("") + '</div></div>';
  _popEls.article.style.display = "block";
  _popEls.results.style.display = "none";

  _popEls.article.querySelectorAll(".cx-pop-cat").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var ce = ENTRIES.filter(function (e) { return e.category === btn.dataset.cat; });
      _popRenderResults(ce);
      _popEls.results.style.display = "block";
      _popEls.article.style.display = "none";
    });
  });
}

function _popRenderResults(entries) {
  if (!entries.length) {
    _popEls.results.innerHTML = '<div class="cx-pop-empty" data-i18n="codex.popover.empty">' + _t("codex.popover.empty", "No matching entries") + '</div>';
    return;
  }
  var html = "";
  entries.forEach(function (e) {
    var cat = CATEGORIES.find(function (c) { return c.id === e.category; });
    html += '<button class="cx-pop-result" data-id="' + e.id + '"><span class="cx-pop-result-title" data-i18n="codex.entry.' + e.id + '.title">' + _entryTitle(e) + '</span><span class="cx-pop-result-cat" data-i18n="' + (cat ? (cat.i18nKey || "") : "") + '">' + _catLabel(cat) + '</span></button>';
  });
  _popEls.results.innerHTML = html;
  _popEls.results.querySelectorAll(".cx-pop-result").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var e = ENTRIES.find(function (x) { return x.id === btn.dataset.id; });
      if (e) _popSelectEntry(e);
    });
  });
}

function _popSelectEntry(e) {
  var cat = CATEGORIES.find(function (c) { return c.id === e.category; });
  var sc = e.shortcut ? '<p style="margin-bottom:8px;"><span class="cx-kbd">' + e.shortcut + '</span></p>' : "";
  var deepHtml = "";
  // The bodies moved to the locales (CX1), so `e.deep` is unset and the
  // section never showed; the locale decides now (RG4).
  var deep = _t("codex.entry." + e.id + ".deep", e.deep || "");
  if (deep) {
    deepHtml = '<div class="cx-deep"><button class="cx-deep-toggle" onclick="this.parentElement.classList.toggle(\'open\')">' + _t("codex.deepToggle", "Under the Hood") + ' <span class="cx-deep-arrow">\u25b6</span></button><div class="cx-deep-body">' + deep + '</div></div>';
  }
  var showMeHtml = "";
  if (window.StudioShowMe && StudioShowMe.has(e.id)) {
    var variants = StudioShowMe.forEntry(e.id);
    showMeHtml = '<div class="cx-showme">';
    variants.forEach(function (v) {
      showMeHtml += '<button class="cx-showme-btn" data-showme="' + v.id + '">' + v.label + '</button>';
    });
    showMeHtml += '</div>';
  }
  _popEls.article.innerHTML = '<div class="cx-pop-back-wrap"><button class="cx-pop-back" data-i18n="codex.popover.back">' + _t("codex.popover.back", "\u2190 Back") + '</button></div>' +
    '<div class="cx-article"><div class="cx-article-category" data-i18n="' + (cat ? (cat.i18nKey || "") : "") + '">' + _catLabel(cat) + '</div><div class="cx-article-title" data-i18n="codex.entry.' + e.id + '.title">' + _entryTitle(e) + '</div>' + sc + showMeHtml + '<div class="cx-article-body">' + _t("codex.entry." + e.id + ".content", e.content || "") + '</div>' + deepHtml + '</div>';
  _popEls.results.style.display = "none";
  _popEls.article.style.display = "block";
  _popEls.article.scrollTop = 0;

  // Wire Show Me buttons
  _popEls.article.querySelectorAll("[data-showme]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      _hidePopover();
      StudioShowMe.run(btn.dataset.showme);
    });
  });

  _popEls.article.querySelector(".cx-pop-back").addEventListener("click", function () {
    var q = _popEls.search.value;
    if (q.trim().length > 0) {
      _popRenderResults(_search(q));
      _popEls.results.style.display = "block";
      _popEls.article.style.display = "none";
    } else {
      _popRenderHome();
    }
  });
}

// ══════════════════════════════════════════════════════════════════
// MODULE REGISTRATION
// ══════════════════════════════════════════════════════════════════

if (window.StudioModules) {
  StudioModules.register("codex", {
    label: "Codex", icon: "\u229e",
    init: function (container) {
      console.log(TAG, "Initializing Codex v" + VERSION);
      if (!document.querySelector('link[href*="codex.css"]')) {
        var l = document.createElement("link"); l.rel = "stylesheet";
        l.href = "/studio/static/codex.css?v=" + VERSION; document.head.appendChild(l);
      }
      _buildUI(container);
    },
    activate: function () {
      setTimeout(function () { if (_els.search) _els.search.focus(); }, 100);
    },
    deactivate: function () {},
  });
} else console.warn(TAG, "StudioModules not available");

// Build popover on load — needs to be in Studio tab before Codex tab is ever opened
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", function () { setTimeout(_buildPopover, 100); });
} else {
  setTimeout(_buildPopover, 100);
}

// Public deep-link API — the education first-encounter tips use it for
// their "Learn more" links. Activates the Codex tab and selects the entry
// by id; unknown ids are a safe no-op. Selection is deferred a tick since
// the module's init builds the UI on first activation.
window.Codex = {
  openEntry: function (id) {
    var e = ENTRIES.find(function (x) { return x.id === id; });
    if (!e) return false;
    try { if (window.StudioModules) StudioModules.activate("codex"); } catch (err) {}
    setTimeout(function () { try { _selectEntry(e); } catch (err) {} }, 150);
    return true;
  },
};

})();
