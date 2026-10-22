/*
 * The Settings model-folder list (ON1b), and the folder picker behind it.
 *
 * Model folders are ONE list, any number (the owner's decision, 2026-10-21).
 * Studio walks every subfolder and files each model by what its header says
 * it is, so a row here is a folder and what it holds -- "Checkpoints 8 ·
 * LoRAs 2" -- not a role. A file Studio cannot place is listed under that
 * folder's Unrecognized panel, where the owner says what it is. Add and Remove
 * save at once; there is no Save button to forget.
 *
 * Browse opens the operating system's own folder dialog (FP1,
 * `_studioPickFolder` in app.js over `/studio/pick_folder`): Studio binds
 * 127.0.0.1, so the server's desktop is the owner's. The overlay below is the
 * FALLBACK for a host with no desktop -- a container, a headless server --
 * where a native dialog cannot appear. It browses the SERVER's filesystem, in
 * the page, and says so. The Browse button is bound by app.js alone;
 * `choose()` is how it reaches this overlay.
 *
 * Directory and file names are attacker-influenced strings. Everything
 * rendered here goes through textContent; there is no innerHTML anywhere in
 * this file and a test asserts that, because the page CSP allows
 * 'unsafe-inline' for styles and a name is a place someone would try to put
 * markup.
 */
(function () {
  "use strict";

  const API = {
    capabilities: "/studio/fs/capabilities",
    places: "/studio/fs/places",
    resolve: "/studio/fs/resolve",
    list: "/studio/fs/list",
    reveal: "/studio/fs/reveal",
    settings: "/studio/settings/model_roots",
    unrecognized: "/studio/settings/model_library/unrecognized",
    correct: "/studio/settings/model_library/correct",
    inspect: "/studio/settings/model_library/inspect",
    useStudio: "/studio/settings/model_library/use_studio_folder",
    detectors: "/api/detectors",
  };

  // Every kind a library file can be filed as -- the server's
  // `catalogue.MODEL_ROLES`, which a guard asserts this equals. It drives the
  // summary on each row and the choices in the Unrecognized panel, so a kind
  // missing here is one the owner could never correct a file to.
  const ROLES = ["checkpoint", "text_encoder", "vae", "adetailer", "lora", "embedding", "upscaler", "controlnet"];
  // Display order for a row's summary: what an owner looks for first.
  const SUMMARY_ORDER = ["checkpoint", "lora", "vae", "text_encoder", "controlnet", "embedding", "upscaler", "adetailer"];
  const PLURAL = {
    checkpoint: ["settings.modelFolders.kinds.checkpoint", "Checkpoints"],
    text_encoder: ["settings.modelFolders.kinds.textEncoder", "Text encoders"],
    vae: ["settings.modelFolders.kinds.vae", "VAEs"],
    adetailer: ["settings.modelFolders.kinds.adetailer", "Detectors"],
    lora: ["settings.modelFolders.kinds.lora", "LoRAs"],
    embedding: ["settings.modelFolders.kinds.embedding", "Embeddings"],
    upscaler: ["settings.modelFolders.kinds.upscaler", "Upscalers"],
    controlnet: ["settings.modelFolders.kinds.controlnet", "ControlNets"],
  };
  const SINGULAR = {
    checkpoint: ["settings.modelFolders.kind.checkpoint", "Checkpoint"],
    text_encoder: ["settings.modelFolders.kind.textEncoder", "Text encoder"],
    vae: ["settings.modelFolders.kind.vae", "VAE"],
    adetailer: ["settings.modelFolders.kind.adetailer", "Auto Detail detector"],
    lora: ["settings.modelFolders.kind.lora", "LoRA"],
    embedding: ["settings.modelFolders.kind.embedding", "Embedding"],
    upscaler: ["settings.modelFolders.kind.upscaler", "Upscaler"],
    controlnet: ["settings.modelFolders.kind.controlnet", "ControlNet"],
  };

  const state = {
    token: "",
    capabilities: null,
    // The owner's library folders, as the server holds them: [{path, hint?}].
    library: [],
    // Per-folder detail from the server, by ordinal (status, counts...).
    status: [],
    // Which folders' Unrecognized panels are open, by path.
    expanded: new Set(),
    // ON2: a picked folder that is a Forge / ComfyUI install, waiting for
    // the owner to say "use its models folder": {path, models, kind}.
    pendingInstall: null,
    open: null,          // { handle, path, usable } while the overlay is up
    chooser: null,       // OF1: resolve(path|null) for a choose() caller
    busy: false,
  };

  const t = (key, fallback, params) =>
    (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback, params) : fallback;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;   // never innerHTML
    return node;
  };
  // ST1. A stroke icon, built node by node (the rule above holds for markup
  // too). `shapes` is a list of [tag, attributes].
  const icon = (size, width, shapes) => {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill: "none",
      stroke: "currentColor", "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true" })) svg.setAttribute(k, String(v));
    for (const [tag, attrs] of shapes) {
      const shape = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) shape.setAttribute(k, v);
      svg.appendChild(shape);
    }
    return svg;
  };

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Studio-Settings-Token": state.token,
      },
      body: JSON.stringify(body || {}),
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* non-JSON */ }
    if (!response.ok) {
      const error = new Error((payload && payload.error) || `HTTP ${response.status}`);
      error.code = payload && payload.code;
      throw error;
    }
    return payload;
  }

  // ---- the token, and the folders the server already holds ------------------

  async function refreshSettings() {
    const response = await fetch(API.settings);
    if (!response.ok) return null;
    const document_ = await response.json();
    state.token = document_.token || "";
    adopt(document_);
    return document_;
  }

  function adopt(document_) {
    if (!document_) return;
    state.library = (document_.model_library || []).map((folder) =>
      typeof folder === "string" ? { path: folder } : { path: folder.path, hint: folder.hint });
    state.status = document_.library_status || [];
    renderRows();
  }

  // ---- folder rows ----------------------------------------------------------

  // Every list on the page: Settings' and, during the first run, the
  // welcome screen's (ON2) -- one state, drawn wherever it is shown.
  function renderRows() {
    document.querySelectorAll("#modelLibraryList, [data-model-library-list]").forEach(renderInto);
  }

  function renderInto(host) {
    host.textContent = "";
    if (state.pendingInstall) host.appendChild(installNotice(state.pendingInstall));
    if (!state.library.length) {
      host.appendChild(el("p", "settings-item-description studio-library-empty",
        t("settings.modelFolders.none", "No model folders yet. Add the folder where your models are.")));
      return;
    }
    state.library.forEach((folder, index) => {
      host.appendChild(folderRow(folder, index, state.status[index]));
    });
  }

  // ON2. Recognising a folder the owner PICKED is allowed (owner decision
  // 2); looking for installs is not. A whole install would be walked
  // through its venv and repositories, so Studio asks to use its models
  // folder instead.
  function installNotice(pending) {
    const box = el("div", "studio-library-install");
    box.appendChild(el("p", "settings-item-description", pending.kind === "comfyui"
      ? t("settings.modelFolders.install.comfyui", "That folder is a ComfyUI install. Use its models folder?")
      : t("settings.modelFolders.install.forge", "That folder is a Forge or A1111 install. Use its models folder?")));
    const use = el("button", "defaults-btn primary", t("settings.modelFolders.install.use", "Use its models folder"));
    use.type = "button";
    use.addEventListener("click", () => {
      state.pendingInstall = null;
      push(pending.models);
    });
    const cancel = el("button", "defaults-btn", t("settings.modelFolders.install.cancel", "Cancel"));
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      state.pendingInstall = null;
      renderRows();
    });
    const row = el("div", "settings-btn-group");
    row.appendChild(use);
    row.appendChild(cancel);
    box.appendChild(row);
    return box;
  }

  function folderRow(folder, index, detail) {
    const block = el("div", "studio-library-folder");
    const row = el("div", "studio-root-row");

    // ST1. A folder icon, then the path; a ready folder's counts become chips
    // ("8 Checkpoints"), every other state keeps its sentence.
    const folderIcon = el("span", "studio-root-folder-icon");
    folderIcon.appendChild(icon(16, 2, [["path", { d: "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" }]]));
    row.appendChild(folderIcon);

    const label = el("span", "studio-root-path", folder.path);
    label.title = folder.path;                // full text on hover, ellipsised in CSS
    row.appendChild(label);

    const chips = countChips(detail);
    if (chips) {
      row.appendChild(chips);
    } else {
      const status = el("span", "studio-root-status", statusText(detail));
      status.dataset.state = (detail && detail.status) || "unknown";
      row.appendChild(status);
    }

    const served = detail && detail.status === "ready" && detail.inside === undefined;
    // Only when there is something in it (owner, 2026-10-21: an
    // "Unrecognized" link on every folder read as "this folder was not
    // recognised"). A network folder has no count -- reading one would walk
    // the share on every Settings read -- so it keeps the plain link.
    const unknown = detail ? detail.unrecognized : undefined;
    const corrected = (detail && detail.corrected) || 0;
    const counted = typeof unknown === "number";
    const toggleLabel = !counted
      ? t("settings.modelFolders.unrecognized.button", "Unrecognized")
      : unknown > 0
        ? t("settings.modelFolders.unrecognized.count", "{n} unrecognized", { n: unknown })
        : t("settings.modelFolders.unrecognized.corrected", "Files you set");
    const offered = served && (!counted || unknown > 0 || corrected > 0);
    if (offered) {
      const open = state.expanded.has(folder.path);
      const toggle = el("button", "studio-root-btn studio-library-toggle", toggleLabel);
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.addEventListener("click", () => {
        if (state.expanded.has(folder.path)) state.expanded.delete(folder.path);
        else state.expanded.add(folder.path);
        renderRows();
      });
      row.appendChild(toggle);
    }

    const remove = el("button", "studio-root-btn studio-root-remove");
    remove.appendChild(icon(12, 2.4, [["line", { x1: "6", y1: "6", x2: "18", y2: "18" }], ["line", { x1: "18", y1: "6", x2: "6", y2: "18" }]]));
    remove.type = "button";
    remove.title = t("settings.modelFolders.remove", "Remove this folder");
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => {
      state.library.splice(index, 1);
      state.status.splice(index, 1);
      state.expanded.delete(folder.path);
      renderRows();
      commit();
    });
    row.appendChild(remove);
    block.appendChild(row);

    if (offered && state.expanded.has(folder.path)) {
      const panel = el("div", "studio-library-unrecognized");
      panel.appendChild(el("p", "settings-item-description",
        t("settings.modelFolders.unrecognized.loading", "Looking for files Studio could not place…")));
      block.appendChild(panel);
      loadUnrecognized(index, panel);
    }
    return block;
  }

  // ST1. A ready folder's counts as chips, in SUMMARY_ORDER; null when the
  // folder is in any other state (or holds nothing), which keeps its sentence.
  function countChips(detail) {
    if (!detail || detail.status !== "ready" || detail.inside !== undefined) return null;
    const counts = detail.counts || {};
    const kinds = SUMMARY_ORDER.filter((kind) => counts[kind]);
    if (!kinds.length) return null;
    const box = el("span", "studio-library-chips");
    kinds.forEach((kind) => {
      const chip = el("span", "studio-library-chip");
      chip.appendChild(el("b", "", String(counts[kind])));
      chip.appendChild(document.createTextNode(t(PLURAL[kind][0], PLURAL[kind][1])));
      box.appendChild(chip);
    });
    box.setAttribute("aria-label", statusText(detail));
    return box;
  }

  function statusText(detail) {
    if (!detail) return "";
    if (detail.status === "refused" || detail.status === "unreachable") {
      // The same owner wording the selectors use (app.js), from a stable code.
      return typeof window.studioModelFolderStatus === "function"
        ? window.studioModelFolderStatus(detail)
        : detail.status;
    }
    if (detail.inside !== undefined) {
      return t("settings.modelFolders.inside", "Already included in model folder {n}", { n: detail.inside + 1 });
    }
    const counts = detail.counts || {};
    const parts = SUMMARY_ORDER.filter((kind) => counts[kind]).map((kind) =>
      t(PLURAL[kind][0], PLURAL[kind][1]) + " " + counts[kind]);
    if (!parts.length) return t("settings.modelFolders.ready.none", "Ready · no models found in this folder");
    const text = parts.join(" · ");
    return detail.truncated
      ? text + " · " + t("settings.modelFolders.truncated", "list truncated, folder contains more")
      : text;
  }

  // ---- the Unrecognized panel -----------------------------------------------

  async function loadUnrecognized(index, panel) {
    let answer = null;
    try {
      if (!state.token) await refreshSettings();
      answer = await post(API.unrecognized, { folder: index });
    } catch (error) {
      panel.textContent = "";
      panel.appendChild(el("p", "settings-item-description",
        t("settings.modelFolders.unrecognized.failed", "That folder could not be read.")));
      return;
    }
    panel.textContent = "";
    const files = answer.files || [];
    const corrected = answer.corrected || [];
    if (!files.length && !corrected.length) {
      panel.appendChild(el("p", "settings-item-description",
        t("settings.modelFolders.unrecognized.none", "Every model file in this folder was recognised.")));
      return;
    }
    if (files.length) {
      panel.appendChild(el("p", "settings-item-description",
        t("settings.modelFolders.unrecognized.intro",
          "Studio could not tell what these are. Older .ckpt, .pt and .pth files are never opened, so their names are all it has. Say what each one is:")));
    }
    files.forEach((file) => panel.appendChild(fileRow(index, file.relative, "", file.evidence)));
    corrected.forEach((file) => panel.appendChild(fileRow(index, file.relative, file.kind, "")));
  }

  function fileRow(index, relative, kind, evidence) {
    const row = el("div", "studio-root-row studio-library-file");
    const name = el("span", "studio-root-path", relative);
    name.title = evidence ? relative + " — " + evidence : relative;
    row.appendChild(name);

    const select = el("select", "param-val studio-library-kind");
    const automatic = el("option", "", kind
      ? t("settings.modelFolders.unrecognized.automatic", "Let Studio decide")
      : t("settings.modelFolders.unrecognized.choose", "What is it?"));
    automatic.value = "";
    select.appendChild(automatic);
    ROLES.forEach((role) => {
      const option = el("option", "", t(SINGULAR[role][0], SINGULAR[role][1]));
      option.value = role;
      select.appendChild(option);
    });
    select.value = kind || "";
    select.addEventListener("change", () => correct(index, relative, select.value));
    row.appendChild(select);
    return row;
  }

  async function correct(index, relative, kind) {
    try {
      adopt(await post(API.correct, { folder: index, relative: relative, kind: kind }));
      await afterChange();
    } catch (error) {
      if (window.showToast) {
        window.showToast(t("settings.modelFolders.unrecognized.saveFailed", "That file could not be changed."), "error");
      }
      renderRows();
    }
  }

  // EVERY folder change ends here, and a refused write is SHOWN: a row that
  // is on screen and not on disk is the defect the per-role picker shipped
  // twice ("it did not save between restarts").
  function commit() {
    save().catch((error) => {
      refreshSettings().catch(() => {});
      if (window.showToast) {
        window.showToast(t("settings.modelFolders.saveFailed", "Model folders not changed") + ": " +
          (error && error.message ? error.message : ""), "error");
      }
    });
  }

  async function addFolder(path) {
    const text = String(path || "").trim();
    if (!text) return;
    if (state.library.some((folder) => folder.path === text)) return;
    try {
      if (!state.token) await refreshSettings();
      const answer = await post(API.inspect, { path: text });
      if (answer && answer.install && answer.models) {
        state.pendingInstall = { path: text, models: answer.models, kind: answer.install };
        renderRows();
        return;
      }
    } catch (_) { /* not an install, or no answer: add what was picked */ }
    push(text);
  }

  function push(path) {
    if (state.library.some((folder) => folder.path === path)) { renderRows(); return; }
    state.library.push({ path: path });
    renderRows();
    commit();
  }

  // ON2, starting fresh: Studio's own models folder joins the library, and
  // the server opens its checkpoint folder for the owner to drop a model in.
  async function useStudioFolder() {
    if (!state.token) await refreshSettings();
    const answer = await post(API.useStudio, {});
    adopt(answer);
    await afterChange();
    return answer;
  }

  // ---- the picker overlay ---------------------------------------------------
  //
  // Constructed in JS and appended to document.body, following the
  // lora-browser idiom, so the Settings CARD stays free of a fixed-position
  // dialog -- which is a shape the model-folders surface tests forbid.

  function overlay() {
    let node = document.getElementById("studioDirPicker");
    if (node) return node;
    node = el("div", "studio-dir-picker");
    node.id = "studioDirPicker";
    node.style.display = "none";

    const panel = el("div", "studio-dir-panel");
    panel.appendChild(el("div", "studio-dir-title", "Choose a folder on the Studio machine"));
    const note = el("div", "studio-dir-note",
      "This browses the machine running Studio, not this computer.");
    panel.appendChild(note);

    const crumbs = el("div", "studio-dir-crumbs");
    crumbs.id = "studioDirCrumbs";
    panel.appendChild(crumbs);

    const body = el("div", "studio-dir-body");
    const places = el("div", "studio-dir-places");
    places.id = "studioDirPlaces";
    body.appendChild(places);
    const listing = el("div", "studio-dir-listing");
    listing.id = "studioDirListing";
    body.appendChild(listing);
    panel.appendChild(body);

    const footer = el("div", "studio-dir-footer");
    const message = el("div", "studio-dir-message", "");
    message.id = "studioDirMessage";
    footer.appendChild(message);

    const reveal = el("button", "defaults-btn", "Open in file manager");
    reveal.id = "studioDirReveal";
    reveal.type = "button";
    reveal.addEventListener("click", doReveal);
    footer.appendChild(reveal);

    const cancel = el("button", "defaults-btn", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", close);
    footer.appendChild(cancel);

    const choose = el("button", "defaults-btn primary", "Use this folder");
    choose.id = "studioDirChoose";
    choose.type = "button";
    choose.addEventListener("click", chooseCurrent);
    footer.appendChild(choose);

    panel.appendChild(footer);
    node.appendChild(panel);
    node.addEventListener("click", (event) => { if (event.target === node) close(); });
    document.body.appendChild(node);
    return node;
  }

  function close() {
    const node = document.getElementById("studioDirPicker");
    if (node) node.style.display = "none";
    state.open = null;
    finishChoosing(null);
  }

  // OF1. A caller other than a model-root row -- Settings > Save folder's
  // Browse, the Gallery folder's -- waiting for one folder. Kept OUT of
  // `state.open`, which `render` rebuilds on every navigation.
  function finishChoosing(path) {
    const resolve = state.chooser;
    state.chooser = null;
    if (resolve) resolve(path || null);
  }

  // Resolves with the chosen folder's path, or null when cancelled.
  function choose() {
    finishChoosing(null);
    return new Promise((resolve) => {
      state.chooser = resolve;
      open(null);
    });
  }

  function message(text) {
    const node = document.getElementById("studioDirMessage");
    if (node) node.textContent = text || "";
  }

  async function open(role) {
    if (!state.token) await refreshSettings();
    overlay().style.display = "flex";
    state.open = { role: role };
    message("");
    try {
      state.capabilities = await post(API.capabilities, {});
      const reveal = document.getElementById("studioDirReveal");
      if (reveal) reveal.disabled = !(state.capabilities && state.capabilities.reveal);
      const places = await post(API.places, {});
      renderPlaces(places.places || []);
      const first = (places.places || [])[0];
      if (first) await openPath(first.path);
    } catch (error) {
      message(error.message || "Folder browsing is unavailable.");
    }
  }

  function renderPlaces(places) {
    const host = document.getElementById("studioDirPlaces");
    if (!host) return;
    host.textContent = "";
    places.forEach((place) => {
      const button = el("button", "studio-dir-place", place.label);
      button.type = "button";
      button.title = place.path;
      button.addEventListener("click", () => openPath(place.path));
      host.appendChild(button);
    });
  }

  async function openPath(path) {
    try {
      render(await post(API.resolve, { path: path }));
    } catch (error) {
      message(error.message || "That folder could not be opened.");
    }
  }

  async function navigate(body) {
    try {
      render(await post(API.list, body));
    } catch (error) {
      message(error.message || "That folder could not be opened.");
    }
  }

  function render(document_) {
    const role = state.open ? state.open.role : null;
    state.open = {
      role: role,
      handle: document_.handle,
      path: document_.path,
      usable: document_.usable,
    };
    message(document_.truncated
      ? "Showing the first " + document_.entries.length + " of " + document_.total_seen + " items."
      : "");

    const crumbs = document.getElementById("studioDirCrumbs");
    if (crumbs) {
      crumbs.textContent = "";
      (document_.breadcrumb || []).forEach((crumb) => {
        const button = el("button", "studio-dir-crumb", crumb.label);
        button.type = "button";
        button.addEventListener("click", () => openPath(crumb.path));
        crumbs.appendChild(button);
      });
    }

    const listing = document.getElementById("studioDirListing");
    if (listing) {
      listing.textContent = "";
      if (document_.has_parent) {
        const up = el("button", "studio-dir-entry is-dir", "↑ Up");
        up.type = "button";
        up.addEventListener("click", () => navigate({ handle: state.open.handle, parent: true }));
        listing.appendChild(up);
      }
      (document_.entries || []).forEach((entry) => {
        const isDir = entry.kind === "directory";
        const button = el(
          "button",
          "studio-dir-entry" + (isDir ? " is-dir" : " is-file") +
            (entry.redirecting ? " is-link" : ""),
          entry.name
        );
        button.type = "button";
        button.disabled = !isDir;
        if (entry.redirecting) button.title = "Shortcuts are not followed.";
        button.addEventListener("click", () =>
          navigate({ handle: state.open.handle, child: entry.name })
        );
        listing.appendChild(button);
      });
    }

    const choose = document.getElementById("studioDirChoose");
    if (choose) choose.disabled = !document_.usable;
  }

  async function doReveal() {
    if (!state.open || !state.open.handle) return;
    try {
      await post(API.reveal, { handle: state.open.handle });
    } catch (error) {
      message(error.message || "That folder could not be opened here.");
    }
  }

  function chooseCurrent() {
    if (!state.open || !state.open.usable) return;
    if (state.chooser) {
      const path = state.open.path;
      finishChoosing(path);
      close();
      return;
    }
    addFolder(state.open.path);
    close();
  }

  // ---- save -----------------------------------------------------------------

  async function save() {
    // The complete list, every time: the server replaces it as one
    // transaction, and a folder that cannot be used refuses the save and
    // leaves the previous list serving.
    const library = state.library.map((folder) =>
      folder.hint ? { path: folder.path, hint: folder.hint } : folder.path);
    const document_ = await post(API.settings, { model_library: library });
    adopt(document_);
    await afterChange();
    return document_;
  }

  async function afterChange() {
    if (typeof window.populateDropdowns === "function") await window.populateDropdowns();
    // The source of truth for detectors just changed, so the catalogue is
    // re-read. Without this the owner adds a folder of detectors and the
    // Auto Detail list keeps showing what was there before -- the exact
    // shape of the defect that started this whole line of work.
    await refreshDetectors();
    // UP1: the server rescanned the upscalers; refill the Hires and Upscale
    // menus.
    if (typeof window.loadRegistries === "function") window.loadRegistries().catch(() => {});
  }

  // The detector catalogue, as NAMES. `/api/detectors` hashes bundled files
  // and never opens a weight, so this is safe to call on a cold server and
  // cannot be the thing that drags a detector runtime into the process.
  //
  // A bundled name whose bytes do not match is REFUSED by the server and
  // simply will not appear, which is why the count can be lower than the
  // file count.
  async function refreshDetectors() {
    const summary = document.getElementById("modelRootDetectorSummary");
    if (!summary) return;
    let document_ = null;
    try {
      const response = await fetch(API.detectors);
      if (response.ok) document_ = await response.json();
    } catch (_) { /* a host without the route simply has no list */ }
    if (!document_) { summary.textContent = ""; return; }
    const count = Number(document_.count) || 0;
    const verified = Number(document_.verified_count) || 0;
    if (!count) {
      summary.textContent = "";
      return;
    }
    const names = (document_.detectors || []).map((d) => d.name).join(", ");
    summary.textContent = t("settings.modelFolders.detectors",
      "Auto Detail detectors: {count} ({verified} shipped with Studio): {names}",
      { count: count, verified: verified, names: names });
  }

  window.StudioDirPicker = Object.freeze({
    open: open,
    choose: choose,
    save: save,
    refresh: refreshSettings,
    addFolder: addFolder,
    useStudioFolder: useStudioFolder,
    render: renderRows,
    library: () => state.library.map((folder) => ({ ...folder })),
  });

  function mount() {
    // Browse is NOT bound here: app.js owns it and opens the system's folder
    // dialog, reaching this overlay through `choose()` only when the server
    // has no desktop.
    const input = document.getElementById("modelLibraryInput");
    const add = document.getElementById("modelLibraryAdd");
    if (add && input) {
      add.addEventListener("click", () => {
        addFolder(input.value);
        input.value = "";
      });
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        addFolder(input.value);
        input.value = "";
      });
    }
    refreshSettings().catch(() => {});
    refreshDetectors().catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
