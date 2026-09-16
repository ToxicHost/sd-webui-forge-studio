/*
 * The Settings folder picker, and the ordered-root rows behind it.
 *
 * The whole file exists because a native folder dialog opens on the machine
 * running Studio, which is the wrong machine for a remote host, a VM or a
 * container -- which is why the old Browse button already degraded to "type
 * the path instead". This browses the SERVER's filesystem, in the page, and
 * says so.
 *
 * Directory names are attacker-influenced strings. Everything rendered here
 * goes through textContent; there is no innerHTML anywhere in this file and a
 * test asserts that, because the page CSP allows 'unsafe-inline' for styles
 * and a name is a place someone would try to put markup.
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
    detectors: "/api/detectors",
  };

  // Every role with a configurable ROOT, which is not the same set as the
  // roles that make up the resident model. `adetailer` has a folder the owner
  // points at, but a detector is loaded per Auto Detail slot and released, so
  // it never becomes part of the session. The server draws that same
  // distinction between `catalogue.MODEL_ROLES` and
  // `model_selection.RESIDENT_MODEL_ROLES`.
  // Must stay equal to `catalogue.MODEL_ROLES` -- a guard asserts it,
  // because the two drifting is how a root becomes configurable on one
  // side and invisible on the other.
  const ROLES = ["checkpoint", "text_encoder", "vae", "adetailer", "lora"];
  const ROLE_SUFFIX = {
    checkpoint: "Checkpoint", text_encoder: "TextEncoder", vae: "Vae",
    adetailer: "Adetailer", lora: "Lora",
  };

  const state = {
    token: "",
    capabilities: null,
    // DERIVED from ROLES, never listed again. This was a fourth literal
    // spelling of the role set, and when `lora` was added to ROLES and
    // ROLE_SUFFIX it was missed here -- so `addRoot("lora", path)` reached
    // `state.roots["lora"].indexOf(...)` on undefined, threw, and the owner's
    // typed folder silently did not save. Reported from real use.
    //
    // One source now: a role that exists in ROLES has a slot by construction.
    roots: Object.fromEntries(ROLES.map((role) => [role, []])),
    open: null,          // { role, handle, path, entries, breadcrumb, usable }
    busy: false,
  };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;   // never innerHTML
    return node;
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

  // ---- the token, and the roots the server already holds ---------------------

  async function refreshSettings() {
    const response = await fetch(API.settings);
    if (!response.ok) return null;
    const document_ = await response.json();
    state.token = document_.token || "";
    const configured = document_.roots || {};
    ROLES.forEach((role) => {
      const value = configured[role];
      // One root persists as a string and several as a list. The page reads
      // both rather than requiring the server to have normalised, because
      // that narrowing is what keeps an older build able to read the file.
      state.roots[role] = value === undefined || value === null
        ? []
        : (typeof value === "string" ? [value] : value.slice());
    });
    renderRows(document_);
    return document_;
  }

  // ---- ordered root rows ----------------------------------------------------

  function renderRows(document_) {
    const byRole = {};
    (document_ && document_.roles ? document_.roles : []).forEach((entry) => {
      byRole[entry.role] = entry;
    });
    ROLES.forEach((role) => {
      const host = document.getElementById("modelRootList" + ROLE_SUFFIX[role]);
      if (!host) return;
      host.textContent = "";
      const detail = (byRole[role] && byRole[role].roots) || [];
      state.roots[role].forEach((path, index) => {
        host.appendChild(rootRow(role, path, index, detail[index]));
      });
    });
  }

  function rootRow(role, path, index, detail) {
    const row = el("div", "studio-root-row");
    row.appendChild(el("span", "studio-root-handle", "⋮⋮"));

    const label = el("span", "studio-root-path", path);
    label.title = path;                       // full text on hover, ellipsised in CSS
    row.appendChild(label);

    const status = el("span", "studio-root-status", statusText(detail));
    status.dataset.state = (detail && detail.status) || "unknown";
    row.appendChild(status);

    const up = el("button", "studio-root-btn", "▲");
    up.type = "button";
    up.title = "Move up";
    up.disabled = index === 0;
    up.addEventListener("click", () => move(role, index, -1));
    row.appendChild(up);

    const down = el("button", "studio-root-btn", "▼");
    down.type = "button";
    down.title = "Move down";
    down.disabled = index === state.roots[role].length - 1;
    down.addEventListener("click", () => move(role, index, 1));
    row.appendChild(down);

    const remove = el("button", "studio-root-btn", "×");
    remove.type = "button";
    remove.title = "Remove";
    remove.addEventListener("click", () => {
      state.roots[role].splice(index, 1);
      renderRows(null);
      commitRoots();
    });
    row.appendChild(remove);
    return row;
  }

  // EVERY root mutation ends here. `save()` existed, was exported, and was
  // called by nothing at all -- so Add, Remove and reorder each changed
  // `state.roots`, drew a row, and persisted nothing. The row appeared, the
  // owner restarted, and the folder was gone.
  //
  // Reported twice from real use before it was found: once as "it doesn't
  // save" (that was a separate TypeError) and once as "it did not save between
  // restarts", which was this.
  //
  // Failure is SHOWN, not swallowed. A row that is on screen and not on disk
  // is the thing being fixed, so a refused write must not leave the row
  // looking accepted.
  function commitRoots() {
    save().catch((error) => {
      renderRows(null);
      if (window.showToast) {
        window.showToast(
          "That folder could not be saved: " + (error && error.message
            ? error.message : "the server refused it"), "error");
      }
    });
  }

  function statusText(detail) {
    if (!detail) return "";
    if (detail.status === "refused") return "unavailable";
    if (detail.truncated) return detail.entry_count + "+ models";
    return (detail.entry_count || 0) + " models";
  }

  function move(role, index, delta) {
    const list = state.roots[role];
    const target = index + delta;
    if (target < 0 || target >= list.length) return;
    const [item] = list.splice(index, 1);
    list.splice(target, 0, item);
    renderRows(null);
    commitRoots();
  }

  function addRoot(role, path) {
    const text = String(path || "").trim();
    if (!text) return;
    if (state.roots[role].indexOf(text) >= 0) return;
    state.roots[role].push(text);
    renderRows(null);
    commitRoots();
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
    addRoot(state.open.role, state.open.path);
    close();
  }

  // ---- save -----------------------------------------------------------------

  async function save() {
    const roots = {};
    // Sent as LISTS, always. The server narrows one entry back to a string on
    // disk; the wire shape does not have to carry that, and encoding it here
    // would put the persistence rule in two places.
    ROLES.forEach((role) => { roots[role] = state.roots[role].slice(); });
    try {
      const document_ = await post(API.settings, { model_roots: roots });
      renderRows(document_);
      if (typeof window.populateDropdowns === "function") await window.populateDropdowns();
      // The source of truth for detectors just changed, so the catalogue is
      // re-read. Same rule the registry menus follow after the engine comes
      // up: carry, validate, and REFRESH when the source of truth changes.
      // Without this the owner points at a detector folder and the Auto
      // Detail list keeps showing what was there before -- the exact shape of
      // the defect that started this whole line of work.
      await refreshDetectors();
      return document_;
    } catch (error) {
      throw error;
    }
  }

  // The detector catalogue, as NAMES. `/api/detectors` hashes bundled files
  // and never opens a weight, so this is safe to call on a cold server and
  // cannot be the thing that drags a detector runtime into the process.
  //
  // Reported honestly: a configured folder holding nothing usable says so,
  // rather than leaving the previous count on screen. A bundled name whose
  // bytes do not match is REFUSED by the server and simply will not appear,
  // which is why the count can be lower than the file count.
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
      summary.textContent = state.roots.adetailer && state.roots.adetailer.length
        ? "No usable detectors in that folder."
        : "";
      return;
    }
    const names = (document_.detectors || []).map((d) => d.name).join(", ");
    summary.textContent =
      `${count} detector${count === 1 ? "" : "s"} (${verified} verified): ${names}`;
  }

  window.StudioDirPicker = Object.freeze({
    open: open,
    save: save,
    refresh: refreshSettings,
    addRoot: addRoot,
    roots: () => JSON.parse(JSON.stringify(state.roots)),
  });

  function mount() {
    ROLES.forEach((role) => {
      const button = document.getElementById("modelRootBrowse" + ROLE_SUFFIX[role]);
      if (button) {
        button.addEventListener("click", (event) => {
          event.preventDefault();
          open(role);
        });
      }
      const input = document.getElementById("modelRoot" + ROLE_SUFFIX[role]);
      const add = document.getElementById("modelRootAdd" + ROLE_SUFFIX[role]);
      if (add && input) {
        add.addEventListener("click", () => {
          addRoot(role, input.value);
          input.value = "";
        });
      }
    });
    refreshSettings().catch(() => {});
    refreshDetectors().catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
