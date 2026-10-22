/**
 * Forge Studio — Education System
 * by ToxicHost & Moritz
 *
 * The first run, the orientation tour, the first-picture checklist and the
 * first-encounter tips. Zero VRAM, zero network beyond Studio's own routes.
 *
 * Onboarding redo (ON2-ON4, owner decisions 2026-10-21):
 *   - ONE first-run screen. It asks about the owner's MODELS, not their skill:
 *     "I already have models" adds folders right there (the same list as
 *     Settings > Model folders, counts as each one is scanned); "I'm starting
 *     fresh" explains where models come from -- guidance, never a download.
 *   - Then a choice, never a requirement: "Show me around" (the orientation
 *     tour plus the checklist) or "Skip". Both stay in Help and Settings.
 *   - The first-picture checklist ticks itself from what the owner actually
 *     does. It replaces the beginner walkthrough, which hid the UI until the
 *     tour revealed it.
 *   - Existing installs never see the first run.
 */
"use strict";

var Education = (function () {
  // i18n helper. Step arrays are built lazily via get*() so _t() resolves
  // against the live locale at tour-start time (after I18N.ready).
  function _t(key, fallback, params) {
    return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback, params) : fallback;
  }

  var STORAGE_PREFIX = "studio-edu-";
  // The first-run card's own key (app.js used it before ON2); a browser that
  // already has it is an existing install.
  var ONBOARD_KEY = "studio-onboarded";
  var _state = {
    tier: null, enabled: true, guidedStep: 0, guidedActive: false,
    generationCount: 0, flags: {}, dismissedHints: []
  };
  var _tour = null;

  // ========================================================================
  // STATE PERSISTENCE
  // ========================================================================

  // ED1. The tutorial's progress is ALSO kept in Studio's own preferences
  // (the state root), under `education.state`. The browser keeps localStorage
  // per origin, and the origin includes the port: a launch on another port
  // (the random port every tester had until P2, or 7866 when 7865 is busy) or
  // in another browser read nothing back, so the tutorial picker came back.
  // localStorage still wins when it has a choice; the mirror fills it in.
  function _prefsEducation() {
    try {
      var edu = (window.Prefs && window.Prefs.get) ? window.Prefs.get("education", null) : null;
      return (edu && typeof edu === "object") ? edu : {};
    } catch (e) { return {}; }
  }

  function _setPrefsEducation(patch) {
    try {
      if (!(window.Prefs && window.Prefs.set)) return;
      window.Prefs.set("education", Object.assign({}, _prefsEducation(), patch));
    } catch (e) { /* the browser copy still holds it */ }
  }

  function _mirrorToPrefs(state) {
    _setPrefsEducation({ state: state });
  }

  function _load() {
    var mirrored = _prefsEducation().state;
    if (!localStorage.getItem(STORAGE_PREFIX + "tier") && mirrored && typeof mirrored === "object" && mirrored.tier) {
      _state.tier = String(mirrored.tier);
      _state.enabled = mirrored.enabled !== false;
      _state.guidedStep = parseInt(mirrored.guidedStep) || 0;
      _state.guidedActive = !!mirrored.guidedActive;
      _state.generationCount = parseInt(mirrored.generationCount) || 0;
      _state.flags = (mirrored.flags && typeof mirrored.flags === "object") ? mirrored.flags : {};
      _state.dismissedHints = Array.isArray(mirrored.dismissedHints) ? mirrored.dismissedHints : [];
      _save();  // back into this origin's localStorage
      return;
    }
    _state.tier = localStorage.getItem(STORAGE_PREFIX + "tier") || null;
    _state.enabled = localStorage.getItem(STORAGE_PREFIX + "enabled") !== "0";
    _state.guidedStep = parseInt(localStorage.getItem(STORAGE_PREFIX + "guided-step")) || 0;
    _state.guidedActive = localStorage.getItem(STORAGE_PREFIX + "guided-active") === "1";
    _state.generationCount = parseInt(localStorage.getItem(STORAGE_PREFIX + "gen-count")) || 0;
    try { _state.flags = JSON.parse(localStorage.getItem(STORAGE_PREFIX + "flags") || "{}"); } catch (e) { _state.flags = {}; }
    try { _state.dismissedHints = JSON.parse(localStorage.getItem(STORAGE_PREFIX + "dismissed") || "[]"); } catch (e) { _state.dismissedHints = []; }
  }

  function _save() {
    if (_state.tier) localStorage.setItem(STORAGE_PREFIX + "tier", _state.tier);
    localStorage.setItem(STORAGE_PREFIX + "enabled", _state.enabled ? "1" : "0");
    localStorage.setItem(STORAGE_PREFIX + "guided-step", _state.guidedStep);
    localStorage.setItem(STORAGE_PREFIX + "guided-active", _state.guidedActive ? "1" : "0");
    localStorage.setItem(STORAGE_PREFIX + "gen-count", _state.generationCount);
    localStorage.setItem(STORAGE_PREFIX + "flags", JSON.stringify(_state.flags));
    localStorage.setItem(STORAGE_PREFIX + "dismissed", JSON.stringify(_state.dismissedHints));
    if (_state.tier) {
      _mirrorToPrefs({
        tier: _state.tier, enabled: _state.enabled, guidedStep: _state.guidedStep,
        guidedActive: _state.guidedActive, generationCount: _state.generationCount,
        flags: _state.flags, dismissedHints: _state.dismissedHints
      });
    }
  }

  function _setFlag(n, v) { if (v === undefined) v = true; _state.flags[n] = v; _save(); }

  function _reset() {
    Object.keys(localStorage).filter(function (k) { return k.startsWith(STORAGE_PREFIX) || k.startsWith("st-tour-"); }).forEach(function (k) { localStorage.removeItem(k); });
    _state.tier = null; _state.enabled = true; _state.guidedStep = 0; _state.guidedActive = false;
    _state.generationCount = 0; _state.flags = {}; _state.dismissedHints = [];
    // ED1: a reset is a reset everywhere, or the mirror would undo it.
    if (_prefsEducation().state) _mirrorToPrefs(null);
  }

  // ========================================================================
  // SHOW ME AROUND — ORIENTATION TOUR
  // ========================================================================

  function getTourSteps() {
    return [
      {
        id: "exp_layout",
        title: _t("education.tour.experienced.layout.title", "Layout"),
        text: _t("education.tour.experienced.layout.text", "Quick orientation. <em>Toolstrip</em> on the left (19 painting tools plus the Inpaint Mask). <em>Canvas</em> in the center — scroll to zoom, Space+drag to pan, right-click for the canvas menu. <em>Panel</em> on the right (Generate and Extensions tabs). <span class=\"cx-kbd\">\\</span> collapses it. <em>Settings</em> is its own tab at the top.\n\nContext bar above the canvas shows controls for the active tool."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.layout.btn", "Next"),
        position: { bottom: "80px", left: "calc(50% - 190px)" }
      },
      {
        id: "exp_canvas_routing",
        title: _t("education.tour.experienced.canvasRouting.title", "Canvas routing"),
        text: _t("education.tour.experienced.canvasRouting.text", "No separate txt2img/img2img tabs. Studio checks the canvas and routes automatically:\n\n• <strong>Blank canvas</strong> → txt2img\n• <strong>Content on canvas</strong> → img2img\n• <strong>Mask painted</strong> → inpainting\n\nThe pixels are the source of truth. Paint something, erase it all, generate — that’s txt2img because the canvas is blank. No mode toggles."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.canvasRouting.btn", "Next"),
        position: { bottom: "80px", left: "calc(50% - 190px)" }
      },
      {
        id: "exp_canvas_ux",
        title: _t("education.tour.experienced.canvasUx.title", "Canvas tips"),
        text: _t("education.tour.experienced.canvasUx.text", "A few things that work differently here:\n\n• Context bar values are <em>scrub labels</em> — drag horizontally to adjust, click to type a number.\n• Each tool <em>remembers its own settings</em> independently. Switch tools and back — settings preserved.\n• <span class=\"cx-kbd\">Shift+Drag</span> on the canvas changes the brush size: drag right for bigger, left for smaller.\n• <span class=\"cx-kbd\">Ctrl+Click</span> eyedrops from any tool without switching.\n• Zoomed in, pixels show crisp, not blurry: from 100% with the GPU canvas preview, from 800% without it."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.canvasUx.btn", "Next"),
        position: { bottom: "80px", left: "calc(50% - 190px)" }
      },
      {
        id: "exp_mask_regions",
        title: _t("education.tour.experienced.maskRegions.title", "Masks"),
        text: _t("education.tour.experienced.maskRegions.text", "The <em>Inpaint Mask</em> tool (<span class=\"cx-kbd\">Q</span>) paints a hard-edged generation mask. Beside it, the <em>Masks panel</em> (inside Layers) holds named masks — from a brush, a selection, tones, a colour or a prompt word — with their own opacity and hardness. The mask selected there is the one Generate inpaints with.\n\nThere are no separate txt2img/img2img/inpaint tabs: the canvas and the selected mask decide."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.maskRegions.btn", "Next"),
        position: { bottom: "80px", left: "calc(50% - 190px)" }
      },
      {
        id: "exp_layers",
        title: _t("education.tour.experienced.layers.title", "Layers"),
        text: _t("education.tour.experienced.layers.text", "Real layer stack with blend modes, opacity, and reordering. Sending results to canvas creates a <em>new layer</em> — never overwrites your work.\n\nGeneration results go to a reusable \"Gen Result\" layer. Your painted edits on other layers are always safe. Export the full stack as PSD."),
        spotlight: "#layersList", reveal: [], advance: "click",
        btn: _t("education.tour.experienced.layers.btn", "Next"),
        beforeShow: function () {
          var genTab = document.querySelector('[data-panel="generate"]');
          if (genTab) genTab.click();
          return new Promise(function (resolve) {
            setTimeout(function () {
              var el = document.getElementById("layersList");
              if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
              setTimeout(resolve, 300);
            }, 100);
          });
        }
      },
      {
        id: "exp_extras",
        title: _t("education.tour.experienced.extras.title", "Extras"),
        text: _t("education.tour.experienced.extras.text", "<em>Auto Detail</em> (3 slots, including a Words detector) runs automatically after generation.\n\n<em>Drag and drop</em> an image onto the canvas to load it — resizes canvas, imports metadata, creates a new layer.\n\nSave current settings as <em>workflow defaults</em> — persists server-side across sessions — or as named <em>workflows</em>. Model folders, save folders and the file-name pattern are in <em>Settings</em>."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.extras.btn", "Next"),
        position: { bottom: "80px", right: "340px" }
      },
      {
        id: "exp_shortcuts",
        title: _t("education.tour.experienced.shortcuts.title", "Shortcuts"),
        text: _t("education.tour.experienced.shortcuts.text", "Key shortcuts to know:\n\n<span class=\"cx-kbd\">Q</span> Mask mode • <span class=\"cx-kbd\">B</span> Brush • <span class=\"cx-kbd\">E</span> Eraser • <span class=\"cx-kbd\">V</span> Transform\n<span class=\"cx-kbd\">[</span>/<span class=\"cx-kbd\">]</span> Brush size • <span class=\"cx-kbd\">Shift+Drag</span> Brush size (drag sideways)\n<span class=\"cx-kbd\">Ctrl+Enter</span> Generate • <span class=\"cx-kbd\">\\</span> Toggle panel\n<span class=\"cx-kbd\">Ctrl+Z</span> Undo • <span class=\"cx-kbd\">Space+Drag</span> Pan • <span class=\"cx-kbd\">Scroll</span> Zoom\n\nFull list in the <em>Codex</em> tab (highlighted above) under Keyboard Shortcuts.\n\nYou’re set. Go make something."),
        spotlight: null, reveal: [], advance: "click",
        btn: _t("education.tour.experienced.shortcuts.btn", "Let’s go"),
        position: { bottom: "80px", left: "calc(50% - 190px)" },
        beforeShow: function () {
          var codexTab = document.querySelector('button[data-module="codex"]');
          if (codexTab) {
            codexTab.style.outline = "2px solid var(--accent-bright)";
            codexTab.style.outlineOffset = "2px";
            codexTab.style.borderRadius = "4px";
          }
        }
      },
    ];
  }

  function _clearCodexHighlight() {
    var codexTab = document.querySelector('button[data-module="codex"]');
    if (codexTab) { codexTab.style.outline = ""; codexTab.style.outlineOffset = ""; codexTab.style.borderRadius = ""; }
  }

  function _startTour() {
    if (!window.StudioTour) return;
    if (_tour && _tour.isActive) _tour._forceCancel();
    var canvasTab = document.querySelector('button[data-app="studio"]');
    if (canvasTab) canvasTab.click();
    _tour = StudioTour.create({
      id: "experienced-tour",
      title: _t("education.tour.name", "Show me around"),
      steps: getTourSteps(),
      persist: false,
      bodyClass: null,
      confirmCancel: true,
      cancelPrompt: _t("tour.endConfirm", "End the tour? You can restart it anytime from Settings."),
      onComplete: function () { _clearCodexHighlight(); _tour = null; },
      onCancel: function () { _clearCodexHighlight(); _tour = null; },
    });
    _tour.start(0);
  }

  // ========================================================================
  // FIRST RUN (ON2) — one screen: your models, then an optional look around
  // ========================================================================

  function _el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Line icons for the choice cards (they were text glyphs).
  var _CARD_ICONS = {
    have: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    fresh: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    tour: '<circle cx="12" cy="12" r="9"/><polygon points="16 8 10.5 10.5 8 16 13.5 13.5 16 8"/>',
    skip: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
  };

  function _svgEl(inner, viewBox, cls) {
    var span = _el("span", cls || "");
    span.innerHTML = '<svg viewBox="' + (viewBox || "0 0 24 24") + '" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + "</svg>";
    return span;
  }

  function _card(choice, icon, title, desc) {
    var card = _el("button", "edu-card");
    card.type = "button";
    card.dataset.choice = choice;
    card.appendChild(_CARD_ICONS[choice] ? _svgEl(_CARD_ICONS[choice], null, "edu-card-icon") : _el("div", "edu-card-icon", icon));
    var body = _el("div", "edu-card-body");
    body.appendChild(_el("div", "edu-card-title", title));
    body.appendChild(_el("div", "edu-card-desc", desc));
    card.appendChild(body);
    return card;
  }

  function _paragraphs(host, text) {
    String(text).split("\n\n").forEach(function (block) {
      host.appendChild(_el("p", "edu-welcome-text", block));
    });
  }

  function _foot(buttons) {
    var foot = _el("div", "edu-welcome-foot");
    buttons.forEach(function (b) {
      var button = _el("button", b.primary ? "edu-welcome-btn primary" : "edu-welcome-btn", b.label);
      button.type = "button";
      button.addEventListener("click", b.onClick);
      foot.appendChild(button);
    });
    return foot;
  }

  function _showFirstRun() {
    if (document.getElementById("eduWelcome")) return;
    var overlay = _el("div", "edu-modal-overlay");
    overlay.id = "eduWelcome";
    var modal = _el("div", "edu-modal edu-welcome");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // The title bar's wordmark: the flame in the accent, FORGE STUDIO.
    function brand() {
      var b = _el("div", "edu-modal-brand");
      b.appendChild(_svgEl('<path d="M278 37C340 101 371 172 371 246C371 357 302 436 207 436C132 436 76 384 76 312C76 258 108 224 139 192C165 165 172 137 181 105C192 68 225 46 278 37Z" fill="currentColor"/>', "0 0 512 512", "edu-brand-flame"));
      b.appendChild(_el("em", "", "FORGE"));
      b.appendChild(_el("span", "edu-brand-studio", "STUDIO"));
      return b;
    }

    function show(step) {
      modal.textContent = "";
      modal.dataset.step = step;
      modal.appendChild(brand());
      STEPS[step]();
    }

    function finish(tier) {
      _state.tier = tier; _state.guidedStep = 0; _state.guidedActive = false; _save();
      try { localStorage.setItem(ONBOARD_KEY, "1"); } catch (e) {}
      _setPrefsEducation({ onboarded: true });
      overlay.remove();
      if (tier === "tour") {
        _setChecklistOpen(true);
        _startTour();
      }
    }

    var STEPS = {
      models: function () {
        modal.appendChild(_el("div", "edu-modal-subtitle", _t("education.welcome.title", "Welcome to Forge Studio")));
        modal.appendChild(_el("p", "edu-welcome-text", _t("education.welcome.models", "First, your models — the files Studio makes pictures with.")));
        var cards = _el("div", "edu-cards");
        var have = _card("have", "✦", _t("education.welcome.have.title", "I already have models"),
          _t("education.welcome.have.desc", "Point Studio at the folders they’re in. A Forge, A1111 or ComfyUI install works too."));
        var fresh = _card("fresh", "✧", _t("education.welcome.fresh.title", "I’m starting fresh"),
          _t("education.welcome.fresh.desc", "I don’t have any models yet."));
        have.addEventListener("click", function () { show("have"); });
        fresh.addEventListener("click", function () { show("fresh"); });
        cards.appendChild(have);
        cards.appendChild(fresh);
        modal.appendChild(cards);
        var later = _el("button", "edu-skip-link", _t("education.welcome.later", "I’ll set this up later"));
        later.type = "button";
        later.addEventListener("click", function () { show("tour"); });
        modal.appendChild(later);
      },
      have: function () {
        modal.appendChild(_el("div", "edu-modal-subtitle", _t("education.welcome.add.title", "Add your model folders")));
        modal.appendChild(_el("p", "edu-welcome-text", _t("education.welcome.add.desc",
          "Add as many as you like. Studio looks through every subfolder and sorts what it finds; nothing in them is moved or changed.")));
        var list = _el("div", "settings-model-library edu-welcome-library");
        list.setAttribute("data-model-library-list", "");
        modal.appendChild(list);
        var row = _el("div", "edu-welcome-add");
        var input = _el("input", "param-val settings-text-input");
        input.type = "text";
        input.spellcheck = false;
        input.autocomplete = "off";
        input.placeholder = _t("settings.modelFolders.placeholder", "Type a folder path to add, or use Browse");
        var add = _el("button", "defaults-btn", _t("settings.modelFolders.add", "Add folder"));
        add.type = "button";
        var browse = _el("button", "defaults-btn primary", _t("settings.modelFolders.browse", "Browse…"));
        browse.type = "button";
        row.appendChild(input);
        row.appendChild(add);
        row.appendChild(browse);
        modal.appendChild(row);
        var picker = window.StudioDirPicker;
        function addPath(path) {
          if (picker && picker.addFolder) picker.addFolder(path);
          input.value = "";
        }
        add.addEventListener("click", function () { addPath(input.value); });
        input.addEventListener("keydown", function (e) { if (e.key === "Enter") addPath(input.value); });
        browse.addEventListener("click", function () {
          if (!window.studioPickFolder) return;
          window.studioPickFolder({ purpose: "library", start: input.value }).then(function (path) {
            if (path) addPath(path);
          });
        });
        if (picker && picker.refresh) picker.refresh().catch(function () {});
        modal.appendChild(_foot([
          { label: _t("education.welcome.back", "Back"), onClick: function () { show("models"); } },
          { label: _t("education.welcome.continue", "Continue"), primary: true, onClick: function () { show("tour"); } },
        ]));
      },
      fresh: function () {
        modal.appendChild(_el("div", "edu-modal-subtitle", _t("education.welcome.freshGuide.title", "Getting your first model")));
        _paragraphs(modal, _t("education.welcome.freshGuide.text",
          "Studio makes pictures with a model called a checkpoint. Many are free on sites such as Civitai and Hugging Face — pick one made for the kind of pictures you want.\n\nPrefer .safetensors files. Older .ckpt and .pt files can carry code, and Studio never opens them to look inside.\n\nSome checkpoints also need a text encoder and a VAE. Their page says so; put those in the same folder."));
        var note = _el("p", "edu-welcome-text edu-welcome-note", "");
        var useStudio = _el("button", "defaults-btn primary", _t("education.welcome.freshGuide.useStudio", "Use Studio’s models folder"));
        useStudio.type = "button";
        useStudio.addEventListener("click", function () {
          var picker = window.StudioDirPicker;
          if (!(picker && picker.useStudioFolder)) return;
          useStudio.disabled = true;
          picker.useStudioFolder().then(function (answer) {
            note.textContent = _t("education.welcome.freshGuide.ready",
              "Done — Studio opened {folder}. Put your checkpoint there; it shows up in the Model box by itself after you press ↻.",
              { folder: (answer && answer.checkpoints) || "" });
          }).catch(function () {
            useStudio.disabled = false;
            note.textContent = _t("education.welcome.freshGuide.failed", "Studio’s models folder could not be set up. Add a folder of your own instead.");
          });
        });
        var mine = _el("button", "defaults-btn", _t("education.welcome.freshGuide.mine", "Use a folder of my own"));
        mine.type = "button";
        mine.addEventListener("click", function () { show("have"); });
        var row = _el("div", "edu-welcome-add");
        row.appendChild(useStudio);
        row.appendChild(mine);
        modal.appendChild(row);
        modal.appendChild(note);
        modal.appendChild(_foot([
          { label: _t("education.welcome.back", "Back"), onClick: function () { show("models"); } },
          { label: _t("education.welcome.continue", "Continue"), primary: true, onClick: function () { show("tour"); } },
        ]));
      },
      tour: function () {
        modal.appendChild(_el("div", "edu-modal-subtitle", _t("education.welcome.tour.title", "Want a quick look around?")));
        var cards = _el("div", "edu-cards");
        var around = _card("tour", "➤", _t("education.welcome.tour.yes.title", "Show me around"),
          _t("education.welcome.tour.yes.desc", "A short tour of where things are, and a checklist for your first picture."));
        var skip = _card("skip", "✕", _t("education.welcome.tour.no.title", "Skip"),
          _t("education.welcome.tour.no.desc", "I’ll explore on my own. The tour and the checklist stay in Settings and the Codex."));
        around.addEventListener("click", function () { finish("tour"); });
        skip.addEventListener("click", function () { finish("skip"); });
        cards.appendChild(around);
        cards.appendChild(skip);
        modal.appendChild(cards);
      },
    };

    show("models");
    if (window.I18N && typeof window.I18N.applyToDom === "function") { try { window.I18N.applyToDom(overlay); } catch (e) {} }
  }

  // An existing install never sees the first run: a choice already made, the
  // old card's mark, saved defaults or a session, or model folders already set.
  function _isExistingInstall() {
    if (_state.tier) return Promise.resolve(true);
    try { if (localStorage.getItem(ONBOARD_KEY)) return Promise.resolve(true); } catch (e) {}
    if (_prefsEducation().onboarded) return Promise.resolve(true);
    if (window.__studioHadStoredConfig) return Promise.resolve(true);
    return fetch("/studio/model_roots").then(function (r) { return r.ok ? r.json() : null; }).then(function (doc) {
      return !!(doc && Number(doc.library_folders) > 0);
    }).catch(function () { return false; });
  }

  // ========================================================================
  // FIRST-PICTURE CHECKLIST (ON4)
  // ========================================================================
  // Non-modal and never in the way: a small card in the corner that ticks
  // itself from what the owner actually does. Each item, clicked, goes to
  // the place it is about. A ticked item stays ticked.

  var CHECKLIST = [
    { id: "folder", key: "education.checklist.folder", fallback: "Add a model folder",
      done: function () { return !!(window.State && window.State.models && window.State.models.length); },
      go: function () {
        var s = document.querySelector('button[data-app="settings"]'); if (s) s.click();
        setTimeout(function () {
          var g = document.querySelector('[data-settings-nav="general"]'); if (g) g.click();
          var c = document.querySelector('[data-settings-section="model-folders"]'); if (c) c.scrollIntoView({ block: "start" });
        }, 200);
      } },
    { id: "model", key: "education.checklist.model", fallback: "Choose a model in the Model box",
      done: function () { var m = document.getElementById("paramModel"); return !!(m && m.value && m.value !== "None"); },
      go: function () { _toCanvas(); _focus("paramModel"); } },
    { id: "prompt", key: "education.checklist.prompt", fallback: "Describe your picture",
      done: function () { var p = document.getElementById("paramPrompt"); return !!(p && p.value.trim()); },
      go: function () { _toCanvas(); _focus("paramPrompt"); } },
    { id: "generate", key: "education.checklist.generate", fallback: "Press Generate",
      done: function () { return _state.generationCount > 0; },
      go: function () { _toCanvas(); _focus("genBtn"); } },
    { id: "canvas", key: "education.checklist.canvas", fallback: "Send it to the canvas and paint over it",
      done: function () { return !!_state.flags.used_result_to_canvas; },
      go: function () { _toCanvas(); _focus("outputToCanvas"); } },
  ];
  var _checklistEl = null;
  var _checklistTimer = null;

  function _toCanvas() {
    var c = document.querySelector('button[data-app="studio"]'); if (c) c.click();
  }

  function _focus(id) {
    setTimeout(function () {
      var el = document.getElementById(id);
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      if (typeof el.focus === "function") el.focus();
      el.classList.add("edu-checklist-flash");
      setTimeout(function () { el.classList.remove("edu-checklist-flash"); }, 1600);
    }, 200);
  }

  function _setChecklistOpen(open) {
    _setFlag("checklist_open", !!open);
    if (open) _renderChecklist(); else _removeChecklist();
  }

  function _removeChecklist() {
    if (_checklistEl) { _checklistEl.remove(); _checklistEl = null; }
    if (_checklistTimer) { clearInterval(_checklistTimer); _checklistTimer = null; }
  }

  function _checklistDone() {
    var done = _state.flags.checklist_done || {};
    var changed = false;
    CHECKLIST.forEach(function (item) {
      if (!done[item.id]) {
        var ok = false;
        try { ok = item.done(); } catch (e) { ok = false; }
        if (ok) { done[item.id] = true; changed = true; }
      }
    });
    if (changed) _setFlag("checklist_done", done);
    return done;
  }

  function _tickChecklist(id, value) {
    var done = Object.assign({}, _state.flags.checklist_done || {});
    if (value) done[id] = true; else delete done[id];
    _setFlag("checklist_done", done);
    _renderChecklist();
  }

  function _renderChecklist() {
    var done = _checklistDone();
    if (!_checklistEl) {
      _checklistEl = _el("aside", "edu-checklist");
      _checklistEl.setAttribute("aria-label", _t("education.checklist.title", "Your first picture"));
      document.body.appendChild(_checklistEl);
      _checklistTimer = setInterval(function () { if (_checklistEl) _renderChecklist(); }, 1000);
    }
    var count = CHECKLIST.filter(function (item) { return done[item.id]; }).length;
    var all = count === CHECKLIST.length;
    var signature = JSON.stringify(done);
    if (_checklistEl.dataset.signature === signature) return;
    _checklistEl.dataset.signature = signature;
    _checklistEl.textContent = "";
    var head = _el("div", "edu-checklist-head");
    head.appendChild(_el("span", "edu-checklist-title", all
      ? _t("education.checklist.allDone", "That’s your first picture. Enjoy!")
      : _t("education.checklist.title", "Your first picture")));
    head.appendChild(_el("span", "edu-checklist-count", _t("education.checklist.count", "{n} of {total}")
      .replace("{n}", count).replace("{total}", CHECKLIST.length)));
    var close = _el("button", "edu-checklist-close", _t("education.checklist.hideLabel", "Hide"));
    close.type = "button";
    close.title = _t("education.checklist.hide", "Hide the checklist (it stays in Settings)");
    close.addEventListener("click", function () { _setChecklistOpen(false); });
    head.appendChild(close);
    _checklistEl.appendChild(head);
    var bar = _el("div", "edu-checklist-progress");
    var fill = _el("span", "");
    fill.style.width = Math.round((count / CHECKLIST.length) * 100) + "%";
    bar.appendChild(fill);
    _checklistEl.appendChild(bar);
    var list = _el("ul", "edu-checklist-items");
    CHECKLIST.forEach(function (item) {
      var li = _el("li", done[item.id] ? "edu-checklist-item done" : "edu-checklist-item");
      // The box ticks by hand too (owner, 2026-10-21): the checklist is a
      // guide, not a test -- someone who did a step another way is not stuck.
      var mark = _el("button", "edu-checklist-mark", "");
      mark.type = "button";
      mark.setAttribute("role", "checkbox");
      mark.setAttribute("aria-checked", done[item.id] ? "true" : "false");
      mark.title = done[item.id]
        ? _t("education.checklist.untick", "Mark as not done")
        : _t("education.checklist.tick", "Mark as done");
      mark.addEventListener("click", function () { _tickChecklist(item.id, !done[item.id]); });
      li.appendChild(mark);
      var button = _el("button", "edu-checklist-go");
      button.type = "button";
      button.appendChild(_el("span", "edu-checklist-text", _t(item.key, item.fallback)));
      button.appendChild(_el("span", "edu-checklist-chev", ""));
      button.addEventListener("click", item.go);
      li.appendChild(button);
      list.appendChild(li);
    });
    _checklistEl.appendChild(list);
    if (!all) _checklistEl.appendChild(_el("div", "edu-checklist-hint", _t("education.checklist.hint", "Ticks itself as you go. Click a step to go there.")));
    if (all) setTimeout(function () { if (_checklistEl) _setChecklistOpen(false); }, 6000);
  }

  // ========================================================================
  // THE FIRST SAVED PICTURE — one note, once (owner decision 4)
  // ========================================================================

  // `path` is the saved file when the page knows it (the legacy path); a
  // queued job's result names no path, and then the note names the save
  // folder and offers to open it (`/studio/open_folder`, which opens Studio's
  // own output folder for a blank setting).
  function notifySaved(path) {
    var text = String(path || "");
    if (_prefsEducation().savedNoteShown) return;
    _setPrefsEducation({ savedNoteShown: true });
    var folder = text.replace(/[\\/][^\\/]*$/, "");
    var el = _el("div", "edu-tip edu-saved-note");
    el.setAttribute("role", "note");
    el.appendChild(_el("div", "edu-tip-text", folder
      ? _t("education.saved.text", "Your picture was saved to {folder}", { folder: folder })
      : _t("education.saved.textFolder", "Your picture was saved to your save folder.")));
    var row = _el("div", "edu-tip-actions");
    var open = _el("button", "edu-tip-btn", _t("education.saved.open", "Open folder"));
    open.type = "button";
    open.addEventListener("click", function () {
      var dir = (window.State && window.State.saveDir) || "";
      fetch("/studio/open_folder", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dir }),
      }).catch(function () {});
    });
    row.appendChild(open);
    var change = _el("button", "edu-tip-btn edu-tip-learn", _t("education.saved.change", "Change where pictures save"));
    change.type = "button";
    change.addEventListener("click", function () {
      el.remove();
      var s = document.querySelector('button[data-app="settings"]'); if (s) s.click();
      setTimeout(function () { var o = document.querySelector('[data-settings-nav="output-files"]'); if (o) o.click(); }, 200);
    });
    var ok = _el("button", "edu-tip-btn edu-tip-dismiss", _t("education.tip.gotIt", "Got it"));
    ok.type = "button";
    ok.addEventListener("click", function () { el.remove(); });
    row.appendChild(change);
    row.appendChild(ok);
    el.appendChild(row);
    document.body.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.remove(); }, 20000);
  }

  // ========================================================================
  // SETTINGS
  // ========================================================================

  // Settings > Appearance & Layout > Accessibility, and the Codex "Show Me"
  // buttons: the tour, the checklist and the welcome screen are all
  // replayable, and none is ever required.
  function _restartTour() {
    _setChecklistOpen(true);
    setTimeout(_startTour, 200);
  }

  function _injectSettings() {
    if (document.getElementById("eduRestartTour")) return; // already injected

    // Preferred: stable anchor inside the Accessibility card (redesigned page).
    var anchor = document.querySelector('[data-settings-inject-point="education"]');
    var host = anchor ? anchor.parentNode : null;
    // Fallback for any layout without the anchor.
    if (!host) host = document.querySelector('#app-settings [data-settings-section="accessibility"] .settings-card-body')
      || document.querySelector("#app-settings .settings-content")
      || document.querySelector("#app-settings");
    if (!host) return;

    var g = _el("div", "settings-edu-inject");
    g.appendChild(_el("p", "settings-eyebrow", _t("education.settings.title", "Getting started")));
    function item(titleKey, titleFallback, descKey, descFallback, id, labelKey, labelFallback, onClick) {
      var row = _el("div", "settings-item");
      var copy = _el("div", "settings-item-copy");
      copy.appendChild(_el("span", "settings-item-title", _t(titleKey, titleFallback)));
      copy.appendChild(_el("p", "settings-item-description", _t(descKey, descFallback)));
      row.appendChild(copy);
      var control = _el("div", "settings-item-control");
      var button = _el("button", "defaults-btn", _t(labelKey, labelFallback));
      button.type = "button";
      button.id = id;
      button.addEventListener("click", onClick);
      control.appendChild(button);
      row.appendChild(control);
      g.appendChild(row);
    }
    item("education.settings.tour.title", "Show me around", "education.settings.tour.desc", "A short tour of where things are.",
      "eduRestartTour", "education.settings.tour.button", "Start the tour", function () { _restartTour(); });
    item("education.settings.checklist.title", "First-picture checklist", "education.settings.checklist.desc", "Ticks itself off as you make your first picture.",
      "eduShowChecklist", "education.settings.checklist.button", "Show the checklist", function () { _setChecklistOpen(true); });
    // The Gallery and Workshop tours run once, on a first visit; these bring
    // them back. A module that is not available here says so instead.
    function replay(id) {
      if (window.StudioModuleTours && StudioModuleTours.replay(id)) return;
      if (window.showToast) window.showToast(_t("education.settings.replay.unavailable", "That tab isn’t available here."), "info");
    }
    item("education.settings.galleryTour.title", "Gallery tour", "education.settings.galleryTour.desc", "Three steps around the Gallery.",
      "eduReplayGalleryTour", "education.settings.replay.button", "Replay", function () { replay("gallery"); });
    item("education.settings.workshopTour.title", "Workshop tour", "education.settings.workshopTour.desc", "Three steps around the Workshop.",
      "eduReplayWorkshopTour", "education.settings.replay.button", "Replay", function () { replay("workshop"); });
    item("education.settings.welcome.title", "Welcome screen", "education.settings.welcome.desc", "The first-run screen, with model folders and the tour.",
      "eduShowWelcome", "education.settings.welcome.button", "Show it again", function () { _showFirstRun(); });
    if (anchor && anchor.parentNode === host) host.insertBefore(g, anchor); else host.appendChild(g);
  }

  // ========================================================================
  // GENERATION COUNTER
  // ========================================================================

  function _hookGeneration() {
    var was = false;
    setInterval(function () {
      if (window.State) {
        if (was && !window.State.generating && window.State.sessionEntries && window.State.sessionEntries.length > 0) {
          _state.generationCount++;
          _save();
        }
        was = window.State.generating;
      }
    }, 1000);
    // The job queue, which is how Generate runs on every real install, never
    // sets `State.generating` (app.js `handleProgress` says why), so the poll
    // above only ever saw the legacy path and "Press Generate" never ticked.
    // A queued picture is counted when it is delivered.
    document.addEventListener("studio:result-delivered", function () {
      _state.generationCount++;
      _save();
      if (_checklistEl) _renderChecklist();
      // Queue results carry no file path (the server keeps paths out of the
      // browser), so the note names the save folder rather than the file.
      if (window.State && window.State.saveOutputs) notifySaved("");
    });
    // The checklist's last item: Result -> Canvas, however it is reached.
    document.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest("#outputToCanvas") && !_state.flags.used_result_to_canvas) {
        _setFlag("used_result_to_canvas");
      }
    }, true);
  }

  // ========================================================================
  // FIRST-ENCOUNTER TIPS
  // ========================================================================
  // Static callouts, each shown once on the first meaningful interaction and
  // never again after dismissal. `regions` and `live` were removed with their
  // features (CX1) and came back when those features could generate (RG1-RG3,
  // LV1; the Extension's tips). Seen-state persists server-side via prefs key
  // "education" ({seen:{layers:true,...}}), so it survives reloads and Clear
  // Storage. Deliberately not a tour engine. Copy teaches workflow, not UI
  // labels.

  var TIPS = {
    layers: {
      i18n: "education.tip.layers",
      fallback: "Generate, send to canvas, mask, and regenerate to composite elements — layers are for building images in passes, like a paint program.",
      codexId: "layers_basics"
    },
    regions: {
      i18n: "education.tip.regions",
      fallback: "Each region carries its own prompt inside a single generation — draw where something should go, describe it in that region's prompt, and generate once.",
      codexId: "regional_prompting"
    },
    live: {
      i18n: "education.tip.live",
      fallback: "Live is img2img over your canvas, not a scribble-to-image guide — low strength follows your sketch closely, higher strength takes liberties. Apply puts the result on the canvas as a new layer.",
      codexId: "live_painting"
    },
  };
  var _tipEl = null;

  function _tipSeen() {
    var edu = (window.Prefs && window.Prefs.get) ? (window.Prefs.get("education", null) || {}) : {};
    return (edu && typeof edu.seen === "object" && edu.seen) ? edu.seen : {};
  }

  function _markTipSeen(id) {
    if (!(window.Prefs && window.Prefs.set)) return;
    var edu = window.Prefs.get("education", null);
    if (!edu || typeof edu !== "object") edu = {};
    edu.seen = Object.assign({}, _tipSeen());
    edu.seen[id] = true;
    window.Prefs.set("education", edu);
  }

  function _dismissTip() {
    if (_tipEl) { _tipEl.remove(); _tipEl = null; }
  }

  function _showTip(spec) {
    _dismissTip();
    var el = document.createElement("div");
    el.className = "edu-tip";
    el.setAttribute("role", "note");
    var text = document.createElement("div");
    text.className = "edu-tip-text";
    text.textContent = _t(spec.i18n, spec.fallback);
    el.appendChild(text);
    var row = document.createElement("div");
    row.className = "edu-tip-actions";
    var learn = document.createElement("button");
    learn.className = "edu-tip-btn edu-tip-learn";
    learn.textContent = _t("education.tip.learnMore", "Learn more in Codex");
    learn.addEventListener("click", function () {
      var codexId = spec.codexId;
      _dismissTip();
      if (window.Codex && window.Codex.openEntry) window.Codex.openEntry(codexId);
    });
    row.appendChild(learn);
    var ok = document.createElement("button");
    ok.className = "edu-tip-btn edu-tip-dismiss";
    ok.textContent = _t("education.tip.gotIt", "Got it");
    ok.addEventListener("click", _dismissTip);
    row.appendChild(ok);
    el.appendChild(row);
    document.body.appendChild(el);
    _tipEl = el;
  }

  function maybeShowTip(id) {
    var spec = TIPS[id];
    if (!spec) return;
    if (_tipSeen()[id]) return;
    _markTipSeen(id); // fires exactly once per install, dismissed or not
    _showTip(spec);
  }

  // ========================================================================
  // INIT
  // ========================================================================

  // Internal-alpha smoke mode: `?onboarding=off` in the launch URL keeps the
  // first-run screen away for this visit. Per-visit only -- nothing is
  // written, so normal owner mode still shows it once.
  function _onboardingSuppressedByUrl() {
    try {
      return new URLSearchParams(window.location.search).get("onboarding") === "off";
    } catch (e) { return false; }
  }

  function init() {
    // ED1. Wait for Studio's preferences (a cached promise app.js also
    // awaits at boot) before deciding whether to show the first run: the
    // saved choice may live only there. A failed load decides from the
    // browser alone, as before.
    var loading = null;
    try { if (window.Prefs && window.Prefs.load) loading = window.Prefs.load(); } catch (e) {}
    Promise.resolve(loading).catch(function () {}).then(_start);
  }

  function _start() {
    _load();
    _hookGeneration();
    _injectSettings();

    // The beginner walkthrough is gone (ON4). Someone in the middle of it
    // gets the checklist, which covers the same first picture.
    if (_state.guidedActive) {
      _state.guidedActive = false;
      _save();
      _setChecklistOpen(true);
      return;
    }
    if (_state.flags.checklist_open) _renderChecklist();

    if (_onboardingSuppressedByUrl()) return;
    _isExistingInstall().then(function (existing) {
      if (existing || _onboardingSuppressedByUrl()) return;
      // Asked again when it would show: app.js learns about saved defaults
      // during its own start, which may finish after this one.
      setTimeout(function () { if (!window.__studioHadStoredConfig) _showFirstRun(); }, 800);
    });
  }

  // ========================================================================
  // PUBLIC API
  // ========================================================================

  return {
    init: init,
    get state() {
      return {
        tier: _state.tier, enabled: _state.enabled, guidedStep: _state.guidedStep,
        guidedActive: _state.guidedActive, generationCount: _state.generationCount,
        flags: Object.assign({}, _state.flags)
      };
    },
    setFlag: _setFlag,
    reset: _reset,
    showFirstRun: _showFirstRun,
    startTour: _startTour,
    restartTour: _restartTour,
    showChecklist: function () { _setChecklistOpen(true); },
    notifySaved: notifySaved,
    maybeShowTip: maybeShowTip,
    get tour() { return _tour; },
  };
})();

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", function () { setTimeout(function () { Education.init(); }, 500); });
} else {
  setTimeout(function () { Education.init(); }, 500);
}
window.Education = Education;
