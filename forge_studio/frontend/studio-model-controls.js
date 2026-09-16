// Studio Jobs controls — a NATIVE Canvas Strip adapter.
//
// Studio-authored (not part of the adopted canonical source set). This
// module creates no surface of its own: the floating Model and Results
// boxes are gone, and what remains is an adapter that
//
//   * drives the JOBS parameter group that lives in the Canvas Strip
//     alongside every other generation parameter, and
//   * routes completed results into Studio's existing session registry
//     (`State.sessionEntries`) so the Session Strip renders them and the
//     existing Canvas open path shows them.
//
// The group was "Model / Session lifecycle (internal alpha)" and showed a
// BUSY chip, an Unload button and `1 active - 0 queued`. It was not wrong
// for exposing a count; it was wrong for exposing lifecycle CONCEPTS --
// resident state, leases, cache ownership -- to someone who should never
// have to reason about them, instead of the feature those concepts support.
// Those readings are diagnostics and remain on /api/model/state.
//
// It speaks only the canonical Studio API:
//
//   GET  /api/model/state       GET  /api/jobs
//   POST /api/model/unload      GET  /api/jobs/{id}
//   POST /api/generate          POST /api/jobs/{id}/cancel
//   GET  /api/queue             POST /api/jobs/{id}/remove
//   POST /api/queue/reorder     POST /api/queue/clear
//   GET  /studio/file?path={opaque handle}
//
// There is no profile route and no load route, because there is no owner
// concept of either. The model this client wants travels on the generation
// request as three opaque catalogue ids, and the server makes that selection
// resident before the job leases a session -- Forge Neo's behaviour, where
// choosing a model updates desired state and generating reconciles it.
// `unload` survives as an optional VRAM action; nothing here is a
// prerequisite for generating.
//
// There is no second lifecycle state machine and no parallel result
// store: lifecycle state comes from the product, job state from the
// coordinator records, and results are appended to the one session
// registry the rest of Studio already uses. Nothing here ever sees or
// renders a filesystem path -- role NAMES only, and every image travels
// through its opaque handle under a safe generated name.
(() => {
  "use strict";

  const POLL_MS = 1500;
  // No profile routes and no load route. The model this client wants travels
  // on the generation request; `unload` survives as the optional VRAM action
  // and `state` as a status read, which is the whole model-lifecycle surface.
  const API = {
    unload: "/api/model/unload",
    state: "/api/model/state",
    generate: "/api/generate",
    jobs: "/api/jobs",
    job: (id) => `/api/jobs/${encodeURIComponent(id)}`,
    cancel: (id) => `/api/jobs/${encodeURIComponent(id)}/cancel`,
    remove: (id) => `/api/jobs/${encodeURIComponent(id)}/remove`,
    queue: "/api/queue",
    reorderQueue: "/api/queue/reorder",
    clearQueue: "/api/queue/clear",
    file: (handle) => `/studio/file?path=${encodeURIComponent(handle)}`,
    settings: "/studio/settings/model_roots",
    rememberSelection: "/studio/settings/model_selection",
  };

  // The three Canvas Strip selectors, in the order a selection names them.
  const SELECTORS = {
    checkpoint: "paramModel",
    text_encoder: "paramTextEncoder",
    vae: "paramVAE",
  };

  // studio-job-000001 -> studio-result-000001 (safe public name; derived
  // from the public job id alone, never from a backing path).
  const safeName = (jobId) =>
    String(jobId).replace("studio-job-", "studio-result-");

  const state = {
    model: { state: "unknown" },
    jobs: [],
    //: The owner-facing queue, straight from /api/queue. Kept separate from
    //: `jobs` -- that flat list is every job this process has seen and is the
    //: diagnostic read; this one is what the panel renders.
    queue: { running: null, queued: [], recent: [], queue_depth: 0 },
    // Affirmed only by a successful state poll. Starting false means a
    // Generate click can never route to the coordinator before this host
    // has actually proven it HAS one.
    lifecycleAvailable: false,
    lastError: "",
    // Set the instant Unload is clicked, cleared when the server's
    // authoritative state arrives: guarantees at least one rendered
    // UNLOADING frame and blocks a duplicate unload request.
    optimisticUnloading: false,
    // The remembered dropdown choices, once fetched. null means "not asked
    // yet", {} means "asked, nothing remembered" -- the difference decides
    // whether restoring should keep retrying while the catalogue loads.
    preference: null,
    preferenceApplied: false,
    restoreAttempts: 0,
    // True while restorePreference() is driving a <select>, so the change it
    // dispatches is not saved back as an owner decision.
    restoring: false,
    // Set by the first real owner change. After that the remembered value is
    // never re-applied, so a slow catalogue can never overwrite a choice the
    // owner has already made.
    ownerChangedSelection: false,
    settingsToken: "",
    // job_id -> true once its result has been added to the session
    // registry, so the poll never adds the same result twice.
    delivered: new Map(),
    // The last lifecycle state and load count ANNOUNCED to the page, so a
    // transition is announced once rather than every 1500 ms. `loads` starts
    // at -1 because 0 is a real value on a cold server.
    // `running` starts null, not false, so the FIRST poll of a page that
    // loaded mid-job still announces rather than matching a default.
    announced: { state: null, loads: -1, running: null },
  };

  // The native group's own markup lives in index.html and uses existing
  // classes. Only the two Studio-authored children -- the lifecycle chip
  // and the queue rows -- need styles, and they are written against the
  // existing design tokens so app.css stays byte-identical.
  const css = `
  .studio-stage-chip {
    display: inline-block; padding: 1px 8px; border-radius: 9px;
    font-size: 10px; font-weight: 600; letter-spacing: .02em;
    background: var(--bg-raised); color: var(--text-3);
    border: 1px solid var(--border);
  }
  .studio-stage-chip[data-stage="completed"] { color: #bbf7d0; border-color: #14532d; }
  .studio-stage-chip[data-stage="generating"],
  .studio-stage-chip[data-stage="starting"],
  .studio-stage-chip[data-stage="hires"],
  .studio-stage-chip[data-stage="publishing"] { color: #fde68a; border-color: #713f12; }
  .studio-stage-chip[data-stage="failed"] { color: #fecaca; border-color: #7f1d1d; }
  .studio-session-note, .studio-session-error {
    font-size: 10px; margin-top: 4px; color: var(--text-3);
  }
  .studio-session-error { color: var(--danger, #e06666); }
  .studio-job-running { margin-top: 6px; }
  .studio-queue-section { margin-top: 8px; }
  .studio-queue-heading {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 10px; color: var(--text-3); margin-bottom: 4px;
  }
  .studio-queue-action { font-size: 9px; padding: 1px 6px; }
  .studio-queue-list, .studio-recent-list { display: grid; gap: 3px; }
  .studio-recent { margin-top: 8px; font-size: 10px; color: var(--text-3); }
  .studio-recent summary { cursor: pointer; }
  .studio-recent-list { margin-top: 4px; }
  .studio-job-row {
    display: flex; align-items: center; gap: 6px;
    padding: 3px 5px; background: var(--bg-raised);
    border: 1px solid var(--border); border-radius: var(--radius);
    font-size: 10px;
  }
  .studio-job-row[data-running="true"] { border-color: #713f12; }
  .studio-job-stage { font-weight: 600; font-size: 9px; white-space: nowrap; }
  .studio-job-summary { flex: 1; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; opacity: .85; }
  .studio-job-position { opacity: .6; font-variant-numeric: tabular-nums; }
  .studio-job-progress { opacity: .75; font-variant-numeric: tabular-nums; }
  .studio-job-code { opacity: .8; color: var(--danger, #e06666); }
  .studio-job-move {
    background: none; border: none; cursor: pointer; padding: 0 2px;
    color: var(--text-3); font-size: 10px; line-height: 1;
  }
  .studio-job-move[disabled] { opacity: .3; cursor: default; }
  `;

  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      // null/undefined means ABSENT, not the string "null". `disabled="null"`
      // is a disabled button, so a conditional attribute written the obvious
      // way would have disabled every reorder control it was meant to enable.
      if (value === null || value === undefined) continue;
      if (name === "text") node.textContent = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value);
    }
    for (const child of children) node.appendChild(child);
    return node;
  };

  const jsonFetch = async (url, options = {}) => {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = null;
    try { body = await response.json(); } catch (_) { /* non-JSON */ }
    if (!response.ok) {
      const message = body && body.error ? body.error : `HTTP ${response.status}`;
      throw new Error(message);
    }
    return body;
  };

  // ---- the native Canvas Strip group ---------------------------------------

  const refs = {};

  function bind() {
    refs.group = document.getElementById("studioSessionBlock");
    if (!refs.group) return false;
    refs.chip = document.getElementById("studioStageChip");
    refs.unloadButton = document.getElementById("studioUnloadBtn");
    refs.note = document.getElementById("studioSessionNote");
    refs.error = document.getElementById("studioSessionError");
    refs.running = document.getElementById("studioRunningJob");
    refs.queuedSection = document.getElementById("studioQueuedSection");
    refs.queuedHeading = document.getElementById("studioQueuedHeading");
    refs.clearQueueButton = document.getElementById("studioClearQueueBtn");
    refs.queueList = document.getElementById("studioQueueList");
    refs.recentDisclosure = document.getElementById("studioRecentDisclosure");
    refs.recentSummary = document.getElementById("studioRecentSummary");
    refs.recentList = document.getElementById("studioRecentList");
    // No Profile selector and no Load button in this list, deliberately. They
    // used to be binding prerequisites, so removing the markup first would
    // have made bind() fail, lifecycleAvailable() go false, and doGenerate
    // stop taking the lifecycle branch -- silently undoing auto-load while
    // looking like progress. The adapter stops requiring them BEFORE the
    // markup goes.
    return !!(refs.chip && refs.unloadButton && refs.queueList && refs.running);
  }

  // A compact, human summary of one job. Dimensions and seed, because those
  // are what tells two otherwise identical queue rows apart. Never a prompt
  // (the request carries only lengths past `to_dict`), never a path, and
  // never a lease, a cache owner or a session id -- those are diagnostics and
  // they live on /api/model/state, which is where an owner is not asked to
  // look.
  const jobSummary = (job) => {
    const bits = [];
    const width = job.width || (job.settings && job.settings.width);
    const height = job.height || (job.settings && job.settings.height);
    if (width && height) bits.push(`${width}x${height}`);
    if (job.seed !== undefined && job.seed !== null) bits.push(`seed ${job.seed}`);
    return bits.length ? bits.join(" · ") : safeName(job.job_id);
  };

  const progressText = (job) => {
    if (typeof job.step === "number" && job.total_steps) {
      return `${job.step}/${job.total_steps}`;
    }
    if (typeof job.progress === "number") return `${job.progress}%`;
    return "";
  };

  // One queue row. `kind` decides which controls it earns: the running job
  // gets Cancel, a waiting job gets reorder plus Remove, and a finished one
  // gets neither.
  function jobRow(job, kind, index, total) {
    const row = el("div", {
      class: "studio-job-row", "data-running": String(kind === "running"),
    });
    if (kind === "queued") {
      row.appendChild(el("span", {
        class: "studio-job-position", text: `${job.queue_position || index + 1}.`,
      }));
    }
    row.appendChild(el("span", {
      class: "studio-job-stage", text: job.stage || "",
    }));
    row.appendChild(el("span", {
      class: "studio-job-summary", text: jobSummary(job),
    }));
    if (kind === "running") {
      const text = progressText(job);
      if (text) {
        row.appendChild(el("span", { class: "studio-job-progress", text }));
      }
      row.appendChild(el("button", {
        class: "cn-upload-btn studio-queue-action", type: "button",
        text: "Cancel", onclick: () => cancelJob(job.job_id),
      }));
    }
    if (kind === "queued") {
      // Up/down rather than drag: the order has to be unambiguous and
      // keyboard-reachable, and a dropped row that lands one place off is a
      // queue running in an order the owner did not choose.
      row.appendChild(el("button", {
        class: "studio-job-move", type: "button", text: "▲",
        title: "Move up", disabled: index === 0 ? "disabled" : null,
        onclick: () => moveQueued(index, index - 1),
      }));
      row.appendChild(el("button", {
        class: "studio-job-move", type: "button", text: "▼",
        title: "Move down", disabled: index >= total - 1 ? "disabled" : null,
        onclick: () => moveQueued(index, index + 1),
      }));
      row.appendChild(el("button", {
        class: "cn-upload-btn studio-queue-action", type: "button",
        text: "Remove", onclick: () => removeJob(job.job_id),
      }));
    }
    if (job.error && job.error.code) {
      row.appendChild(el("span", {
        class: "studio-job-code", text: job.error.code,
      }));
      row.title = job.error.message || job.error.code;
    }
    return row;
  }

  function renderQueue() {
    const view = state.queue;
    const running = view.running;
    const queued = view.queued || [];
    const recent = view.recent || [];

    // The chip says what is HAPPENING, not what the session is. It read
    // BUSY / READY / UNLOADING -- lifecycle vocabulary an owner should never
    // have to learn -- and now reads the stage of their picture.
    const stage = state.optimisticUnloading
      ? "Unloading"
      : (running ? (running.stage || "Generating") : "Idle");
    refs.chip.dataset.stage = stage.toLowerCase();
    refs.chip.textContent = stage;

    refs.running.textContent = "";
    refs.running.hidden = !running;
    if (running) refs.running.appendChild(jobRow(running, "running", 0, 1));

    refs.queuedSection.hidden = queued.length === 0;
    refs.queuedHeading.textContent =
      queued.length === 1 ? "1 waiting" : `${queued.length} waiting`;
    refs.queueList.textContent = "";
    queued.forEach((job, index) => {
      refs.queueList.appendChild(jobRow(job, "queued", index, queued.length));
    });

    refs.recentDisclosure.hidden = recent.length === 0;
    refs.recentSummary.textContent = `Recent (${recent.length})`;
    refs.recentList.textContent = "";
    recent.forEach((job) => {
      refs.recentList.appendChild(jobRow(job, "recent", 0, 0));
    });
  }

  function render() {
    if (!refs.group) return;
    renderQueue();

    const modelState = state.model.state;
    const readiness = state.model.readiness || {};
    // There is no Load button to enable. The model is the three dropdowns and
    // Generate makes that selection resident by itself, so the only control
    // left here is the optional VRAM release.
    refs.unloadButton.disabled = state.optimisticUnloading || !(
      modelState === "ready" || modelState === "busy"
    );
    // Section 10: say which selection is missing rather than leaving a dead
    // button with no explanation. UX only -- the backend re-validates.
    // Says what is missing before Generate is pressed, rather than letting the
    // owner discover it from a failed job. UX only -- the backend re-validates
    // every id through its own role catalogue regardless.
    const selection = catalogueSelection();
    refs.note.textContent = readiness.load_configuration_required
      ? "Load access is not configured for this launch."
      : (!selection.ready && modelState === "no_model"
          ? "Choose a " + selection.missing.join(", a ") + " to generate."
          : "");
    refs.error.textContent = state.lastError;
  }

  // ---- actions --------------------------------------------------------------

  const action = async (work) => {
    state.lastError = "";
    try { await work(); }
    catch (error) { state.lastError = String(error.message || error); }
    await refresh();
  };

  // Reads the three Canvas Strip selectors. The CHECKPOINT is the model; the
  // text encoder and VAE are components it may already carry. An SDXL or
  // SD 1.5 file bundles both, so demanding all three refused a model Forge
  // loads happily -- and the Text Encoder row is hidden for exactly those
  // checkpoints, so the owner was asked for something with no control to
  // supply it. Frontend validation is UX only -- the backend re-validates
  // every id through its own role catalogue regardless.
  const catalogueSelection = () => {
    const value = (id) => (document.getElementById(id)?.value || "").trim();
    const checkpoint = value("paramModel");
    const textEncoder = value("paramTextEncoder");
    const vae = value("paramVAE");
    // "None" and "Automatic" are sentinels, not catalogue ids. For a
    // component that is the owner saying the checkpoint needs nothing
    // external, which is an answer rather than a gap.
    const real = (v) => v && v !== "None" && v !== "Automatic";
    if (!real(checkpoint)) {
      return { ready: false, missing: ["checkpoint"] };
    }
    return {
      ready: true,
      body: {
        checkpoint_model_id: checkpoint,
        text_encoder_model_id: real(textEncoder) ? textEncoder : "",
        vae_model_id: real(vae) ? vae : "",
      },
    };
  };

  // There is no load action. The three selections travel on the generation
  // request and the server makes them resident before the job leases a
  // session, which is Forge Neo's behaviour: selecting a model updates desired
  // state, and generating is what reconciles it.
  const cancelJob = (jobId) => action(() => jsonFetch(API.cancel(jobId), {
    method: "POST", body: "{}",
  }));

  // Remove is a DIFFERENT verb from cancel, all the way down: the server
  // refuses to remove a job that has already started, so a mis-aimed click on
  // a row that just began cannot stop the picture being made. It answers
  // JOB_NOT_QUEUED and the row is still there at the next poll.
  const removeJob = (jobId) => action(() => jsonFetch(API.remove(jobId), {
    method: "POST", body: "{}",
  }));

  const clearQueue = () => action(() => jsonFetch(API.clearQueue, {
    method: "POST", body: "{}",
  }));

  // The order is sent WHOLE, as the complete list of waiting ids, and the
  // server refuses it if the queue moved underneath. A "swap these two"
  // message would silently apply to whichever jobs happened to occupy those
  // positions by the time it arrived.
  const moveQueued = (from, to) => action(async () => {
    const ids = (state.queue.queued || []).map((job) => job.job_id);
    if (to < 0 || to >= ids.length) return;
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    await jsonFetch(API.reorderQueue, {
      method: "POST", body: JSON.stringify({ order: ids }),
    });
  });

  // Unload renders UNLOADING immediately -- one synchronous frame before
  // any network round trip -- disables both lifecycle buttons, and
  // refuses a duplicate request on double-click. The server stays
  // authoritative: the optimistic flag clears as soon as its response
  // (or the next poll) arrives, settling at NO_MODEL or FAILED.
  const unloadModel = async () => {
    if (state.optimisticUnloading) return; // no duplicate unload
    state.optimisticUnloading = true;
    state.lastError = "";
    render();
    try {
      await jsonFetch(API.unload, { method: "POST", body: "{}" });
    } catch (error) {
      state.lastError = String(error.message || error);
    } finally {
      state.optimisticUnloading = false;
    }
    await refresh();
  };

  // The coordinator transport for the main Generate action. One POST to
  // /api/generate answers 202 with the public job id -- minted before any
  // backend work -- and that same id is what the queue list, the queued
  // Cancel control, and the delivered result all use afterwards. This
  // module never calls the legacy blocking route and never falls back to
  // it: on a lifecycle host an error here is an error the owner sees.
  const submitGenerate = async (params) => {
    if (state.lifecycleAvailable !== true) {
      throw new Error("The model lifecycle is not available on this host.");
    }
    state.lastError = "";
    let submitted;
    try {
      submitted = await jsonFetch(API.generate, {
        method: "POST", body: JSON.stringify(params),
      });
    } catch (error) {
      state.lastError = String(error.message || error);
      render();
      throw error;
    }
    await refresh(); // the queue row renders now, not at the next tick
    return submitted; // {job_id, state: "queued"}
  };

  // ---- results -> the ONE session registry ---------------------------------

  // Append a completed lifecycle result to Studio's existing session
  // entries, in the same shape the rest of the app uses, and let the
  // existing renderer draw the Session Strip and gallery. No parallel
  // store, no second thumbnail surface, and Canvas opening comes from
  // the existing double-click path for free.
  async function deliverResult(job) {
    if (state.delivered.has(job.job_id)) return false;
    const detail = await jsonFetch(API.job(job.job_id));
    const result = (detail && detail.result) || {};
    if (!result.image_handle) return false;
    state.delivered.set(job.job_id, true);
    const url = API.file(result.image_handle);
    const name = safeName(job.job_id);
    const entry = {
      sessionId: (window.SESSION_ID || ""),
      entryId: job.job_id,          // the public job id IS the linkage
      source: "studio-result",
      canDeleteFile: false,
      url,
      thumbUrl: url,
      b64: null,
      // The engine's parameter string, and it now ARRIVES. This was hard-coded
      // to "" -- the sole occurrence of the word in this file -- while the
      // server had been sending the resolved seed in `result.metadata` all
      // along and nothing in the frontend read it. Four surfaces depend on
      // this one string: the output info bar, Copy Seed, and both Recycle
      // buttons, all of which parse it and all of which were therefore dead.
      infotext: (result.metadata && result.metadata.infotext) || "",
      filename: name,
      contentHash: "",
      floatPath: "",
      maskPath: "",
      floatStats: null,
      width: (detail.settings && detail.settings.width) || result.width || 0,
      height: (detail.settings && detail.settings.height) || result.height || 0,
      ts: Date.now(),
    };
    const S = window.State;
    if (!S || !Array.isArray(S.sessionEntries)) return false;
    // The RESOLVED seed, taken from the metadata rather than parsed back out
    // of the infotext. Both would work today, but a regex over prose is the
    // fragile one, and -1 ("random") is exactly the case that matters: Recycle
    // exists to recover the number the engine actually picked.
    const resolvedSeed = result.metadata && result.metadata.seed;
    if (Number.isFinite(resolvedSeed)) {
      entry.seed = resolvedSeed;
      S.lastSeed = resolvedSeed;
    }
    S.sessionEntries.unshift(entry);
    S.selectedOutputIdx = 0;
    if (typeof window.renderOutputGallery === "function") {
      window.renderOutputGallery();
    } else if (window.SessionStrip && window.SessionStrip.render) {
      window.SessionStrip.render();
    }
    // AN IMAGE ARRIVED. Announced because the page cannot otherwise tell a
    // finished job from a cancelled or failed one -- the queue only reports
    // that nothing is running any more, and a tab that flashed "done" for a
    // failure would be worse than one that stayed quiet.
    //
    // Announced HERE rather than from the poll, because this is the point at
    // which the result is actually on screen; anywhere earlier would promise
    // a picture the owner cannot yet look at.
    document.dispatchEvent(new CustomEvent("studio:result-delivered", {
      detail: { jobId: job.job_id },
    }));
    return true;
  }

  // ---- polling ---------------------------------------------------------------

  // The text encoder row is CONTEXTUAL, as it is in the Extension: shown
  // for a checkpoint that needs an external encoder, hidden for one that
  // bundles its own. `restoreTextEncoderForModel` in app.js owns that
  // decision from /studio/check_model_te, which reads the header.
  //
  // This file used to re-reveal the row on every 1500 ms poll, because the
  // probe was a constant that answered needs_te:false for every model and
  // hid a control Load then demanded. Re-revealing it on a timer would now
  // fight the header's own answer and put the dropdown back a second and a
  // half after an SDXL checkpoint correctly hid it.

  // ---- remembered dropdown selection -----------------------------------------
  //
  // A PREFERENCE, not a load instruction. Restoring it puts three dropdowns
  // back where the owner left them and does nothing else: the app stays in
  // NO_MODEL until a generation asks. This is what replaced
  // `selected_profile_id`, and the difference is the whole point -- that key
  // decided what was resident at startup, this one decides what a <select>
  // displays.

  async function loadPreference() {
    try {
      const document_ = await jsonFetch(API.settings);
      state.settingsToken = document_.token || "";
      state.preference = document_.last_model_selection || {};
    } catch (_) {
      // No settings host, or it refused. The dropdowns simply open unselected.
      state.preference = {};
    }
  }

  //: How many polls the restore may spend converging before it gives up.
  //: Bounded because choosing a checkpoint makes the page REBUILD the
  //: dependent dropdowns and reset them, so the restore and that rebuild can
  //: legitimately take a turn each -- but an unbounded retry would fight any
  //: page that resets a value on purpose, forever.
  const RESTORE_ATTEMPTS = 8;

  function restorePreference() {
    if (state.preferenceApplied || state.ownerChangedSelection) return;
    const preference = state.preference;
    if (!preference) return;                    // not fetched yet
    if (state.restoreAttempts >= RESTORE_ATTEMPTS) {
      state.preferenceApplied = true;           // stop trying; leave it be
      return;
    }
    state.restoreAttempts += 1;
    let settled = true;
    let matched = false;
    for (const [role, id] of Object.entries(SELECTORS)) {
      const wanted = preference[role];
      if (!wanted) continue;
      const select = document.getElementById(id);
      if (!select) { settled = false; continue; }
      if (select.value === wanted) { matched = true; continue; }
      const available = Array.from(select.options).some((o) => o.value === wanted);
      if (!available) { settled = false; continue; }  // catalogue still loading
      state.restoring = true;
      try {
        select.value = wanted;
        // Dispatched so the rest of the page reacts exactly as it would to a
        // real choice -- the text-encoder row, the Generate gate, everything.
        // `state.restoring` keeps this from being saved back as if the owner
        // had picked it.
        select.dispatchEvent(new Event("change", { bubbles: true }));
      } finally {
        state.restoring = false;
      }
      // NOT settled: setting the checkpoint makes the page rebuild the text
      // encoder and VAE lists and reset them, and that rebuild lands AFTER
      // this pass. Latching here is what left the text encoder showing
      // "None (bundled)" with the owner's real choice sitting in the config
      // file -- a restore that reported success and had already been undone.
      settled = false;
    }
    if (settled && matched) state.preferenceApplied = true;
  }

  async function rememberSelection() {
    if (!state.settingsToken) return;           // nothing to authorize with
    const selection = {};
    for (const [role, id] of Object.entries(SELECTORS)) {
      const value = document.getElementById(id)?.value || "";
      if (value) selection[role] = value;
    }
    try {
      await jsonFetch(API.rememberSelection, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Studio-Settings-Token": state.settingsToken,
        },
        body: JSON.stringify({ last_model_selection: selection }),
      });
      state.preference = selection;
    } catch (_) {
      // Never surfaced and never blocking. A preference that cannot be saved
      // costs the owner one re-pick at the next launch; failing a generation
      // over it would cost them the thing they actually asked for.
    }
  }

  async function refresh() {
    try {
      const modelState = await jsonFetch(API.state);
      state.model = modelState;
      state.lifecycleAvailable = true;
    } catch (error) {
      state.lifecycleAvailable = false;
      state.model = { state: "offline" };
    }
    if (state.lifecycleAvailable) {
      try {
        state.queue = await jsonFetch(API.queue);
      } catch (_) { /* the panel keeps its last view rather than blanking */ }
      try {
        const jobs = await jsonFetch(API.jobs);
        state.jobs = jobs.jobs || [];
        for (const job of state.jobs) {
          // Only completed jobs carry an image. Cancelled and failed jobs
          // are queue rows and never become session entries.
          if (job.state === "completed") {
            try { await deliverResult(job); } catch (_) { /* retry next tick */ }
          }
        }
      } catch (_) { /* jobs listing is best-effort */ }
    }
    announceLifecycle();
    announceRunning();
    // Retried on the poll because the catalogue populates the selects
    // asynchronously: the first tick often finds them empty. It stops as soon
    // as every remembered role has found its option, or the owner picks.
    restorePreference();
    render();
  }

  // Tell the page whether a generation is RUNNING. AR3.5.
  //
  // `state.queue` is the same view that feeds the stage chip, and during the
  // 41-second freeze this work measured, that chip was the ONE indicator
  // telling the truth while the bar, the button and the status bar all said
  // finished. So the words are driven from the same source as the chip; they
  // cannot disagree about whether a job is running.
  //
  // Announced on CHANGE only, like `announceLifecycle` beneath it, and it
  // carries a boolean rather than queue vocabulary -- app.js is being told
  // "something is running", not how the queue is shaped.
  function announceRunning() {
    const running = !!(state.queue && state.queue.running);
    if (running === state.announced.running) return;
    state.announced.running = running;
    document.dispatchEvent(new CustomEvent("studio:generation-running-changed", {
      detail: { running },
    }));
  }

  // Tell the page when the lifecycle actually moved.
  //
  // This poll is the only thing in Studio that watches the engine come up, and
  // several owner-facing catalogues are EMPTY until it does -- `/api/registries`
  // answers nothing on a cold server because `neo_registries` will not stand Neo
  // up merely to list samplers. Whoever fills those menus has to be told that
  // their source of truth changed; app.js listens for this and re-reads.
  //
  // Announced on CHANGE only, and it carries no lifecycle vocabulary: the detail
  // is a state name and a load count, not a lease, a cache owner or a session.
  // This module keeps owning the lifecycle; it just stops being the only thing
  // that knows the engine arrived.
  function announceLifecycle() {
    const modelState = state.model.state || "unknown";
    const counters = state.model.counters || {};
    const loads = Number(counters.loads) || 0;
    if (modelState === state.announced.state && loads === state.announced.loads) {
      return;
    }
    state.announced.state = modelState;
    state.announced.loads = loads;
    document.dispatchEvent(new CustomEvent("studio:model-state-changed", {
      detail: { state: modelState, loads },
    }));
  }

  // ---- mount ------------------------------------------------------------------

  function mount() {
    if (!bind()) return;   // no native group on this host: adapt nothing
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    refs.unloadButton.addEventListener("click", unloadModel);
    if (refs.clearQueueButton) {
      refs.clearQueueButton.addEventListener("click", clearQueue);
    }
    // The three Canvas Strip selectors decide whether Load is possible, so a
    // change in any of them has to re-evaluate the button. Render only -- no
    // request, and nothing is applied until Load is actually pressed.
    ["paramModel", "paramTextEncoder", "paramVAE"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => {
        render();
        if (state.restoring) return;
        state.ownerChangedSelection = true;
        rememberSelection();
      });
    });
    render();
    loadPreference().then(restorePreference);
    refresh();
    setInterval(refresh, POLL_MS);
  }

  // The surface app.js routes through: truthful availability plus the
  // coordinator submission. Frozen so nothing can rebind the transport.
  window.StudioModelControls = Object.freeze({
    lifecycleAvailable: () => state.lifecycleAvailable === true,
    loadedModelId: () => state.model.profile_id || null,
    submitGenerate,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
