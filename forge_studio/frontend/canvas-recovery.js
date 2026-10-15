/**
 * Forge Studio — Canvas crash recovery (AR4.4)
 * by ToxicHost & Moritz
 *
 * THE DEFECT THIS CLOSES
 *
 * `studio-docs.js` holds every open document in JavaScript memory and nowhere
 * else. Its own header says so: "working state is a window into the active
 * document". So F5, a renderer crash, or an OS kill discarded the owner's
 * layers, mask and Regional state outright. Measured before implementing:
 *
 *     before   document c164f3...  256x320  cyan paint  mask painted
 *     after    document 048efe...  768x768  gone        gone
 *
 * 89 session parameters survived that same refresh, because AR4.3 had already
 * made them server-owned. The artwork did not, and that asymmetry is the whole
 * argument for this file.
 *
 * WHAT THIS IS AND IS NOT
 *
 * It is crash recovery for the documents currently open. It is NOT a document
 * library, and an ordinary image export is NOT a project save -- exporting a
 * PNG must never clear recovery, because the owner still has unsaved layers
 * behind that flattened picture. Recovery is removed only when the owner
 * explicitly closes or discards a document.
 *
 * NO CEILINGS. There is no image-dimension limit, no pixel limit, no
 * document-size limit, no recovery budget and no silent eviction anywhere in
 * this file or its server side. Blobs are chunked across requests precisely so
 * the 16 MB HTTP request bound cannot become one by the back door.
 *
 * OFF THE PAINT PATH. Capture uses `toBlob` + `FileReader`, both asynchronous,
 * scheduled on idle and debounced. `getImageData`/`toDataURL` would encode
 * synchronously on the main thread and make a large document stutter under the
 * brush, which would trade one defect for a worse one.
 *
 * ONLY WHAT CHANGED. OB2. A stroke changes one layer, and a capture used to
 * read back every layer, mask and region of the active document, keep that
 * copy, and re-encode and re-upload all of it. Now the active document's
 * canvases are encoded where they stand -- no readback, no copy kept -- and
 * only the ones written since the last sealed capture; the server carries the
 * rest forward from that capture. Which canvases were written is OBSERVED,
 * not inferred: see SOURCE TRACKING.
 *
 * Review: `Evidence/source-review/AR4.4-canvas-crash-recovery.md`,
 * `Evidence/source-review/OB2-recovery-capture.md`.
 */
(function () {
"use strict";

var TAG = "[Recovery]";

// A SAFETY NET, not the trigger. AR4.6.
//
// Capture now runs when an ACTION COMPLETES -- a stroke released, a fill, a
// result placed on the canvas -- which is the moment the document is coherent
// and the moment the owner would expect their work to be safe.
//
// This timer only covers changes that arrive without an action boundary. It is
// short because the measured cost of a capture is small: for a 1024x1280
// document with three layers the whole encode-and-upload took about 137 ms.
// The old 2500 ms value was not protecting against that cost -- it was the
// reason a refresh within 2.6 seconds of a stroke lost it. Measured: 40000
// painted pixels, gone.
var CAPTURE_IDLE_MS = 700;

// Base64 chars per request. A multiple of 4, so every chunk decodes on its own
// and the server can simply append. 6 MB of base64 sits well inside the 16 MB
// request bound with the surrounding JSON, and the COUNT of chunks is
// unbounded -- that is what keeps a large document possible.
var CHUNK_CHARS = 6 * 1024 * 1024;

var _resolved = false;          // has recovery been considered yet?
var _resolveWaiters = [];
var _writesEnabled = false;     // THE ORDERING GATE, see `resolve` below
var _restored = null;           // { docs: [...], activeIdx: n } or null
var _timer = null;
var _inFlight = false;
var _pendingAgain = false;
var _failures = 0;

// What the server last SEALED for each open document: the commit to name as
// `base`, the manifest, and for each blob the state of the canvas it was
// encoded from. Numbers and strings only -- a canvas or an `ImageData` held
// here would keep alive exactly the copy this work removes.
var _committed = Object.create(null);

// Documents a capture was asked for while another was in flight. Kept by id,
// so a document closed in the meantime is simply not found.
var _pendingIds = [];

// Documents the owner closed on this page. OB2c. A capture of one may be part
// way through its requests when the close happens; the server refuses the
// rest of that write (410) so it cannot seal the document back, and here the
// capture stops sending it and neither records nor reports what came back.
var _closed = Object.create(null);

function _api(body) {
  // One call site, through the shared helper, so the request guard that
  // asserts a single `/studio/generate` caller still holds.
  return window.API.generate(body);
}

// ========================================================================
// SOURCE TRACKING — which canvases changed since the last sealed capture
// ========================================================================
//
// OB2. Calling a layer unchanged is what lets a capture skip it, so that call
// has to be exact: a layer wrongly called unchanged is a layer the owner
// loses. Inferring it from the undo system would not be exact -- a tutorial
// scene, a canvas resize, or a path nobody has written yet changes pixels
// without naming a layer -- so it is OBSERVED instead. The first time a
// capture reads a canvas, its context's drawing calls and the canvas's
// `width`/`height` setters (assigning either clears it) are wrapped to count
// writes. Every pixel a 2D canvas holds arrives through one of those,
// whoever makes the call.
//
// A canvas is carried forward only when it is the SAME context, with the
// same count and the same size, as the one its sealed blob was encoded from.
// Anything else -- the new canvases a structural undo builds, a resize, a
// canvas whose setters could not be wrapped -- is encoded again. The cost on
// the paint path is one increment per drawing call.
var WRITES = ["clearRect", "fillRect", "strokeRect", "fill", "stroke",
              "fillText", "strokeText", "drawImage", "putImageData",
              "drawFocusIfNeeded", "reset"];
var _sources = new WeakMap();   // context -> { id, writes, exact }
var _nextSource = 1;

function _descriptor(obj, prop) {
  for (var o = obj; o; o = Object.getPrototypeOf(o)) {
    var d = Object.getOwnPropertyDescriptor(o, prop);
    if (d) return d;
  }
  return null;
}

function _countCalls(ctx, name, t) {
  var original = ctx[name];
  if (typeof original !== "function") return true;   // nothing can call it
  var counted = function () {
    t.writes++;
    return original.apply(this, arguments);
  };
  try { ctx[name] = counted; } catch (_) { return false; }
  return ctx[name] === counted;
}

function _countSets(canvas, prop, t) {
  var d = _descriptor(canvas, prop);
  if (!d || typeof d.get !== "function" || typeof d.set !== "function") return false;
  try {
    Object.defineProperty(canvas, prop, {
      configurable: true, enumerable: d.enumerable,
      get: function () { return d.get.call(this); },
      set: function (value) { t.writes++; d.set.call(this, value); },
    });
  } catch (_) { return false; }
  return true;
}

function _track(ctx) {
  var t = _sources.get(ctx);
  if (t) return t;
  t = { id: _nextSource++, writes: 0, exact: true };
  for (var i = 0; i < WRITES.length; i++) {
    if (!_countCalls(ctx, WRITES[i], t)) t.exact = false;
  }
  var canvas = ctx.canvas;
  if (!canvas || !_countSets(canvas, "width", t) || !_countSets(canvas, "height", t)) {
    t.exact = false;                 // never carried forward
  }
  _sources.set(ctx, t);
  return t;
}

// ========================================================================
// SERIALISATION
// ========================================================================
//
// The shape mirrors `studio-docs.js::_saveDoc` field for field, with every
// pixel source replaced by a named PNG blob. Undo and redo are deliberately
// absent: each entry carries a full-canvas `ImageData` and `S.maxUndo` bounds
// the COUNT rather than the bytes, so persisting them would multiply the
// payload by the undo depth and put the largest, least valuable data on the
// critical write path. The recovered document is editable and starts with an
// empty history. That is a stated product fork, not an oversight.

function _blobName(kind, id) {
  return kind + "-" + String(id).replace(/[^0-9a-z]/gi, "").toLowerCase() + ".png";
}

// A canvas to a PNG `Blob`. `toBlob` copies the canvas when it is CALLED and
// encodes off the main thread, so the call is the snapshot: what is drawn
// afterwards is not in it.
function _encode(canvas) {
  return new Promise(function (resolve) {
    if (!canvas) return resolve(null);
    canvas.toBlob(function (blob) { resolve(blob || null); }, "image/png");
  });
}

// A `Blob` to a base64 data URL, made when it is sent and not before: every
// layer's base64 held at once was most of a whole capture's memory.
function _dataUrl(blob) {
  return new Promise(function (resolve) {
    if (!blob) return resolve(null);
    var reader = new FileReader();
    reader.onload = function () { resolve(String(reader.result || "")); };
    reader.onerror = function () { resolve(null); };
    reader.readAsDataURL(blob);
  });
}

// `ImageData` -> canvas, for the doc-object form held by inactive tabs.
function _canvasFor(imageData, w, h) {
  if (!imageData) return null;
  var c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d", { colorSpace: "srgb" }).putImageData(imageData, 0, 0);
  return c;
}

// One item's PNG. A layer that will not encode FAILS the capture: sealing the
// document without it would restore that layer empty and look like it
// worked. The last good snapshot stays, and the owner is told.
async function _blobOf(item, shot) {
  var blob = item.blob ? await item.blob
    : await _encode(_canvasFor(item.imageData, shot.W, shot.H));
  if (!blob) throw new Error(item.name + " could not be encoded");
  return blob;
}

// ONE StudioDocs document, as it stands now: a manifest plus the items its
// blobs come from. SYNCHRONOUS on purpose -- every live canvas that changed is
// handed to `toBlob` before this returns, so the snapshot is one instant even
// though encoding and upload take many.
//
// A live canvas exactly as it was when this document's last sealed blob was
// encoded from it is carried forward instead. An inactive tab's stored
// `ImageData` is always encoded: it is captured when it is left, and then it
// does not change. `fresh` ignores what was sealed, for a whole document.
function _snapshot(doc, fresh) {
  if (!doc || !doc.documentId) return null;
  var prior = fresh ? null : (_committed[doc.documentId] || null);
  var items = [];
  var named = Object.create(null);

  function pixels(name, ctx, imageData) {
    if (!ctx && !imageData) return null;
    // Ids are unique by construction; should two ever collide, they share
    // one blob as they always did, rather than failing every capture.
    if (named[name]) return name;
    named[name] = true;
    if (ctx) {
      var t = _track(ctx), c = ctx.canvas;
      var seen = { source: t.exact ? t.id : 0, writes: t.writes,
                   w: c.width, h: c.height };
      var was = prior && prior.commitId && prior.blobs[name];
      if (was && seen.source && was.source === seen.source
          && was.writes === seen.writes && was.w === seen.w && was.h === seen.h) {
        items.push({ name: name, seen: seen, reuse: true });
      } else {
        items.push({ name: name, seen: seen, blob: _encode(c) });
      }
      return name;
    }
    // Encoded later, one at a time; this reference keeps it until then
    // even if the tab is switched to and lets its own copy go.
    items.push({ name: name, seen: null, imageData: imageData });
    return name;
  }

  var layers = [];
  for (var i = 0; i < (doc.layers || []).length; i++) {
    var L = doc.layers[i];
    var entry = {
      id: L.id, name: L.name, type: L.type,
      visible: L.visible, opacity: L.opacity,
      blendMode: L.blendMode, locked: L.locked, blob: null,
    };
    if (L.type === "adjustment") {
      // No pixels at all -- an adjustment layer is its parameters.
      entry.adjustType = L.adjustType;
      entry.adjustParams = JSON.parse(JSON.stringify(L.adjustParams || {}));
    } else {
      entry.blob = pixels(_blobName("layer", L.id), L.ctx, L.imageData);
    }
    layers.push(entry);
  }

  var mask = { blob: null, visible: doc.maskVisible, opacity: doc.maskOpacity };
  mask.blob = pixels("mask.png", doc.maskCtx, doc.maskData);

  var regions = [];
  for (var r = 0; r < (doc.regions || []).length; r++) {
    var R = doc.regions[r];
    regions.push({
      id: R.id, color: R.color, prompt: R.prompt || "",
      negPrompt: R.negPrompt || "", weight: R.weight,
      denoise: R.denoise, blob: pixels(_blobName("region", R.id), R.ctx, R.imageData),
    });
  }

  var manifest = _manifest(doc, layers, mask, regions);
  var json = JSON.stringify(manifest);
  var reusing = items.some(function (it) { return it.reuse; });
  return {
    documentId: doc.documentId, W: doc.W, H: doc.H,
    manifest: manifest, json: json, items: items,
    base: reusing ? prior.commitId : null,
    // Nothing to send: the server already holds exactly this.
    unchanged: !!prior && json === prior.manifest
      && items.every(function (it) { return it.reuse; }),
  };
}

// The whole document with every blob encoded, as `{ manifest, blobs }` with
// the blobs as data URLs. The Canvas browser harness reads it.
async function _serialize(doc) {
  var shot = _snapshot(doc, true);
  var blobs = {};
  for (var i = 0; i < shot.items.length; i++) {
    blobs[shot.items[i].name] = await _dataUrl(await _blobOf(shot.items[i], shot));
  }
  return { manifest: shot.manifest, blobs: blobs };
}

function _manifest(doc, layers, mask, regions) {
  return {
    name: doc.name,
    document_id: doc.documentId,
    canvas_revision: doc.canvasRevision || 0,
    W: doc.W, H: doc.H,
    genPanel: doc.genPanel || null,
    layers: layers,
    activeLayerIdx: doc.activeLayerIdx,
    nextLayerId: doc.nextLayerId,
    mask: mask,
    regions: regions,
    activeRegionId: doc.activeRegionId,
    regionMode: !!doc.regionMode,
    nextRegionId: doc._nextRegionId || 1,
    editingMask: !!doc.editingMask,
    canvasTool: doc.canvasTool || null, maskReturnTool: doc.maskReturnTool || "brush",
    userMaskMode: !!doc._userMaskMode,
    canvasDirty: !!doc._canvasDirty,
    zoom: doc.zoom ? { scale: doc.zoom.scale, ox: doc.zoom.ox, oy: doc.zoom.oy } : null,
    developParams: doc.developParams
      ? JSON.parse(JSON.stringify(doc.developParams)) : null,
    // BE10. Three numbers and a name -- no blob, and deliberately NO PATH.
    // The papers are generated from their names, so there is nothing here
    // that could leak where a file lives on this machine.
    paper: doc.paper ? JSON.parse(JSON.stringify(doc.paper)) : null,
  };
}

// A data URL back to `ImageData`, at the document's own dimensions.
function _decode(dataUrl, w, h) {
  return new Promise(function (resolve) {
    if (!dataUrl) return resolve(null);
    var img = new Image();
    img.onload = function () {
      var c = document.createElement("canvas");
      c.width = w; c.height = h;
      var ctx = c.getContext("2d", { colorSpace: "srgb" });
      ctx.drawImage(img, 0, 0);
      resolve(ctx.getImageData(0, 0, w, h));
    };
    img.onerror = function () { resolve(null); };
    img.src = dataUrl;
  });
}

// Manifest plus blob data back into the StudioDocs document shape.
async function _deserialize(manifest, blobData, docId) {
  var W = manifest.W | 0, H = manifest.H | 0;
  if (!(W > 0 && H > 0)) throw new Error("recovered document has no dimensions");

  var layers = [];
  for (var i = 0; i < (manifest.layers || []).length; i++) {
    var L = manifest.layers[i];
    layers.push({
      id: L.id, name: L.name, type: L.type,
      visible: L.visible, opacity: L.opacity,
      blendMode: L.blendMode, locked: L.locked,
      adjustType: L.adjustType,
      adjustParams: L.adjustParams,
      imageData: L.blob ? await _decode(blobData[L.blob], W, H) : null,
    });
  }

  var regions = [];
  for (var r = 0; r < (manifest.regions || []).length; r++) {
    var R = manifest.regions[r];
    regions.push({
      id: R.id, color: R.color, prompt: R.prompt || "",
      negPrompt: R.negPrompt || "", weight: R.weight, denoise: R.denoise,
      imageData: R.blob ? await _decode(blobData[R.blob], W, H) : null,
    });
  }

  // THE DOCUMENT'S OWN GEOMETRY IS AUTHORITATIVE.
  //
  // `_loadDoc` sets the canvas size from the document and THEN applies the
  // saved Generate panel, whose `applyDimensions` can resize the canvas right
  // back. If the two ever disagree the panel wins and the restored pixels are
  // destroyed by the resize -- measured in a real browser: a 256x320 document
  // came back as 768x768 with every layer hash changed, while its identity,
  // layer order and Regional state all restored correctly. That is the worst
  // shape of failure, because it looks like recovery worked.
  //
  // Stamping the real geometry into the restored panel makes the two agree by
  // construction, so recovery does not depend on them having been in sync at
  // the moment of capture.
  var gen = manifest.genPanel || null;
  if (gen && gen.settings) {
    gen.settings.width = W;
    gen.settings.height = H;
  }

  var mask = manifest.mask || {};
  return {
    id: 0,                              // re-numbered by StudioDocs on adopt
    name: manifest.name || "Recovered",
    documentId: docId,
    canvasRevision: manifest.canvas_revision | 0,
    W: W, H: H,
    layers: layers,
    activeLayerIdx: manifest.activeLayerIdx | 0,
    nextLayerId: manifest.nextLayerId | 0,
    maskData: mask.blob ? await _decode(blobData[mask.blob], W, H) : null,
    maskVisible: mask.visible !== undefined ? mask.visible : true,
    maskOpacity: mask.opacity !== undefined ? mask.opacity : 0.5,
    regions: regions,
    activeRegionId: manifest.activeRegionId,
    regionMode: !!manifest.regionMode,
    _nextRegionId: manifest.nextRegionId || 1,
    editingMask: !!manifest.editingMask,
    canvasTool: manifest.canvasTool || null, maskReturnTool: manifest.maskReturnTool || "brush",
    _userMaskMode: !!manifest.userMaskMode,
    _canvasDirty: !!manifest.canvasDirty,
    zoom: manifest.zoom || { scale: 1, ox: 0, oy: 0 },
    // Stated fork: the pixels come back, the history does not.
    undoStack: [], redoStack: [],
    developParams: manifest.developParams || null,
    paper: manifest.paper || null,
    genPanel: gen,
  };
}

// ========================================================================
// WRITE
// ========================================================================

async function _sendBlob(documentId, name, url) {
  // Chunked. One blob may span any number of requests, so a large layer
  // is not bounded by the per-request limit.
  var comma = url.indexOf(",");
  var head = url.slice(0, comma + 1);
  var b64 = url.slice(comma + 1);
  var first = true;
  for (var off = 0; off < b64.length; off += CHUNK_CHARS) {
    await _api({
      action: "recovery_blob", document_id: documentId, name: name,
      data: (first ? head : "") + b64.slice(off, off + CHUNK_CHARS),
      append: !first,
    });
    first = false;
  }
  if (first) {
    // An empty payload still needs the blob to exist, or the manifest
    // would reference a file the commit refuses.
    await _api({ action: "recovery_blob", document_id: documentId,
                 name: name, data: head, append: false });
  }
}

// Null when the document was closed part way through: nothing more of it is
// sent.
async function _upload(shot) {
  var reuse = [];
  await _api({ action: "recovery_begin", document_id: shot.documentId });
  for (var i = 0; i < shot.items.length; i++) {
    var item = shot.items[i];
    if (item.reuse) { reuse.push(item.name); continue; }
    // One layer's base64 at a time, let go as soon as it is sent.
    var url = await _dataUrl(await _blobOf(item, shot));
    if (_isClosed(shot.documentId)) return null;
    if (!url) throw new Error(item.name + " could not be encoded");
    await _sendBlob(shot.documentId, item.name, url);
    item.blob = null;
    item.imageData = null;
  }
  var commit = { action: "recovery_commit", document_id: shot.documentId,
                 manifest: shot.manifest };
  if (reuse.length) {
    // Carried forward from the snapshot these canvases were checked
    // against, and refused by the server if that is no longer the one it
    // holds.
    commit.reuse = reuse;
    commit.base = shot.base;
  }
  if (_isClosed(shot.documentId)) return null;
  return _api(commit);
}

// Closed by the owner here, and in no open tab. A tab that still carries the
// id did not finish closing, and a refusal for it is a failure the owner is
// told about like any other -- never swallowed as "closed".
function _isClosed(documentId) {
  return !!_closed[documentId]
    && !(window.StudioDocs && _openDoc(window.StudioDocs, documentId));
}

// Seal one snapshot and remember what was sealed. False when the server's
// snapshot had moved on and the document is owed a whole capture instead.
// A document closed before or during its capture is done with: true, and
// nothing recorded.
async function _store(shot) {
  if (shot.unchanged || _isClosed(shot.documentId)) return true;
  var response;
  try {
    response = await _upload(shot);
  } catch (e) {
    // Most likely the server's 410 for a write that reached it after the
    // close. Whatever it was, the close is what stands.
    if (_isClosed(shot.documentId)) return true;
    if (shot.base && String(e).indexOf("409") >= 0) {
      // Another page wrote this document, or our last commit never came
      // back. Splicing these layers into that snapshot would be a document
      // nobody painted; send every blob on the next pass instead.
      console.warn(TAG, "the stored snapshot moved on; sending the whole document");
      delete _committed[shot.documentId];
      _remember(shot.documentId);
      _pendingAgain = true;
      return false;
    }
    throw e;
  }
  // Closed while it was being sent: stopped short, or sealed before the close
  // reached the server, which has removed it since. Nothing to record.
  if (_isClosed(shot.documentId)) return true;
  var sealed = (response && response.settings) || {};
  var blobs = {};
  for (var i = 0; i < shot.items.length; i++) {
    if (shot.items[i].seen) blobs[shot.items[i].name] = shot.items[i].seen;
  }
  _committed[shot.documentId] = {
    commitId: sealed.commit_id || null, manifest: shot.json, blobs: blobs,
  };
  return true;
}

function _remember(documentId) {
  if (documentId && _pendingIds.indexOf(documentId) < 0) _pendingIds.push(documentId);
}

function _openDoc(Docs, documentId) {
  var docs = Docs.docs || [];
  for (var i = 0; i < docs.length; i++) {
    if (docs[i] && docs[i].documentId === documentId) return docs[i];
  }
  return null;
}

async function _captureNow() {
  if (!_writesEnabled) return;              // the ordering gate
  var Docs = window.StudioDocs;
  if (_inFlight) {
    // Remember WHICH document asked. A tab switch asks for the one it is
    // leaving, and nothing moves that document's revision again while it sits
    // in the background -- dropped here, it was not written until the owner
    // came back to it.
    var asked = Docs && Docs.activeDoc;
    if (asked) _remember(asked.documentId);
    _pendingAgain = true;
    return;
  }
  // A gesture in progress is not a document state. A capture that landed
  // mid-stroke once resolved the stroke by aborting it, erasing it under the
  // owner's pen. Wait instead; the stroke's own completion captures it.
  var Core = window.StudioCore;
  if (Core && Core.state && Core.state.drawing) { scheduleCapture(); return; }
  if (!Docs || typeof Docs.docForRecovery !== "function") return;

  _inFlight = true;
  var shots = [];
  try {
    // The active document first, from its live canvases, snapshotted here and
    // synchronously so nothing can move under it. Then any document left
    // while the last capture was running, from what it stored on the way out.
    var active = _snapshot(Docs.docForRecovery());
    if (active) shots.push(active);
    var waiting = _pendingIds.splice(0);
    for (var i = 0; i < waiting.length; i++) {
      if (active && waiting[i] === active.documentId) continue;
      var left = _openDoc(Docs, waiting[i]);
      var shot = left ? _snapshot(Docs.docForRecovery(left)) : null;
      if (shot) shots.push(shot);
    }
    var sealedAll = true;
    while (shots.length) {
      if (!(await _store(shots[0]))) sealedAll = false;
      shots.shift();
    }
    if (_failures && sealedAll) {
      _failures = 0;
      if (window.showToast) window.showToast("Crash recovery is working again.", "info");
    }
  } catch (e) {
    // A document whose capture did not land is still owed one -- not retried
    // on a timer, but the next action or flush includes it.
    for (var j = 0; j < shots.length; j++) _remember(shots[j].documentId);
    // VISIBLE. A recovery system that fails quietly is worse than none,
    // because the owner stops saving by hand on the strength of it. Warned
    // once per run of failures rather than on every attempt.
    _failures++;
    console.warn(TAG, "capture failed:", e);
    if (_failures === 1 && window.showToast) {
      window.showToast("Crash recovery could not save this document. Save your work manually.", "error");
    }
  } finally {
    _inFlight = false;
    if (_pendingAgain) { _pendingAgain = false; scheduleCapture(); }
  }
}

function scheduleCapture() {
  if (!_writesEnabled) return;
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(function () {
    _timer = null;
    // Idle, so encoding never competes with a frame the owner is watching.
    if (window.requestIdleCallback) {
      window.requestIdleCallback(function () { _captureNow(); }, { timeout: 4000 });
    } else {
      _captureNow();
    }
  }, CAPTURE_IDLE_MS);
}

function discard(documentId) {
  // ONLY from an explicit close or discard. Never from an image export.
  if (!documentId) return Promise.resolve();
  // Before the request, so a capture of it already under way stops at its
  // next step instead of sending more.
  _closed[documentId] = true;
  delete _committed[documentId];
  var waiting = _pendingIds.indexOf(documentId);
  if (waiting >= 0) _pendingIds.splice(waiting, 1);
  return _api({ action: "recovery_discard", document_id: documentId })
    .catch(function (e) { console.warn(TAG, "discard failed:", e); });
}

// ========================================================================
// RESOLVE — the ordering invariant
// ========================================================================
//
//     read session
//       -> resolve and restore document recovery
//       -> establish the active document
//       -> enable session/recovery writes
//
// Writes stay DISABLED until this finishes. That is not caution, it is the
// measured defect: a fresh document was minted at boot and the debounced
// session write replaced the stored `document_id` before anything could have
// used it, so the identity pointing at the owner's work was overwritten by an
// empty canvas roughly two seconds after every launch.

// A skipped recovery told the owner NOTHING.
//
// Three paths in `resolve` below give up quietly, and each one costs the owner
// work they believe is safe:
//
//   * one document fails to load, and they get the other three back without
//     ever learning a fourth existed;
//   * every document fails, and they get a blank canvas;
//   * the whole resolution throws, and they get a blank canvas.
//
// All three logged to the console and stopped. The console is not where
// somebody who just lost a painting looks.
//
// A toast, not a card: this is information, and a card over the canvas at boot
// would be worse than the silence for the far more common case where nothing
// was lost. The detail stays in the console for whoever asks.
function _tellTheOwner(message, kind) {
  try {
    if (typeof window.showToast === "function") {
      window.showToast(message, kind || "error");
      return true;
    }
  } catch (_) { /* a notice must never cost the recovery */ }
  // Recovery resolves inside app.js's awaited boot chain, so `showToast`
  // normally exists by now. If it does not, the notice waits rather than
  // vanishing -- `app.js` drains this on first paint.
  _pendingNotices.push({ message: message, kind: kind || "error" });
  return false;
}

var _pendingNotices = [];

/** Notices that had nowhere to go yet. Drained by the page once it is up. */
function takeNotices() {
  return _pendingNotices.splice(0);
}

async function resolve(sessionIdentity) {
  var skipped = 0;
  try {
    var listed = await _api({ action: "recovery_list" });
    var docs = ((listed && listed.settings) || {}).documents || [];
    if (!docs.length) return null;

    var out = [];
    for (var i = 0; i < docs.length; i++) {
      var id = docs[i].document_id;
      try {
        var loaded = await _api({ action: "recovery_load", document_id: id });
        var s = (loaded && loaded.settings) || {};
        out.push(await _deserialize(s.manifest || {}, s.blob_data || {}, id));
      } catch (e) {
        // One unreadable document must not cost the owner the others -- but it
        // must not pass unremarked either.
        skipped++;
        console.warn(TAG, "could not restore", id, e);
      }
    }
    if (!out.length) {
      _tellTheOwner(
        docs.length === 1
          ? "A recovered document could not be restored, so the canvas has "
            + "started empty. Nothing was deleted — it is still saved."
          : docs.length + " recovered documents could not be restored, so the "
            + "canvas has started empty. Nothing was deleted — they are "
            + "still saved.");
      return null;
    }
    if (skipped > 0) {
      _tellTheOwner(
        "Restored " + out.length + " document" + (out.length === 1 ? "" : "s")
        + ", but " + skipped + " could not be read and "
        + (skipped === 1 ? "was" : "were") + " left out. "
        + "Nothing was deleted.");
    }

    // The session's document is the one the owner was last looking at.
    var activeIdx = 0;
    if (sessionIdentity && sessionIdentity.document_id) {
      for (var j = 0; j < out.length; j++) {
        if (out[j].documentId === sessionIdentity.document_id) { activeIdx = j; break; }
      }
    }
    _restored = { docs: out, activeIdx: activeIdx };
    console.log(TAG, "recovered", out.length, "document(s)");
    return _restored;
  } catch (e) {
    console.warn(TAG, "recovery resolution failed:", e);
    _tellTheOwner(
      "Crash recovery could not run, so the canvas has started empty. "
      + "Any saved work is still on disk — nothing was deleted.");
    return null;
  } finally {
    _resolved = true;
    var waiters = _resolveWaiters.splice(0);
    for (var w = 0; w < waiters.length; w++) { try { waiters[w](); } catch (_) {} }
  }
}

function whenResolved() {
  if (_resolved) return Promise.resolve();
  return new Promise(function (done) { _resolveWaiters.push(done); });
}

// Called by StudioDocs once the restored documents are actually installed and
// the active document is established. Only then may anything write.
function enableWrites() {
  _writesEnabled = true;
  var C = window.StudioCore;
  if (C && typeof C.onActionComplete === "function") {
    // THE TRIGGER. Not debounced: an action that has finished is exactly
    // when the document should be written. Concurrent captures coalesce --
    // `_captureNow` marks a pending re-run rather than queueing another --
    // so a fast painter is rate-limited by how quickly a capture completes
    // rather than by a fixed timer, which is self-tuning in a way a constant
    // cannot be.
    C.onActionComplete(function () { _captureNow(); });
  }
  if (C && typeof C.onRevisionChange === "function") {
    // Still subscribed, but only as the safety net: `saveUndo` fires at
    // POINTER-DOWN, so this edge sees the document as it was BEFORE the
    // stroke. A debounce does NOT guarantee the stroke has finished -- any
    // stroke longer than it is still down when it fires -- so `_captureNow`
    // defers while a gesture is in progress. It is what catches a mutation
    // that never reaches an action boundary.
    C.onRevisionChange(function () { scheduleCapture(); });
  }

  // FLUSH ON THE WAY OUT, the same three events the session uses, and for the
  // same reason: each misses in different scenarios.
  //
  //   beforeunload  desktop close and navigation; suppressed on tab kill
  //   pagehide      fires across bfcache and most close paths
  //   hidden        fires when the tab is backgrounded, catching the
  //                 memory-manager kills that never fire the other two
  //
  // This is not belt-and-braces. A BACKGROUNDED tab has its timers throttled,
  // so the 2.5s debounce stops being 2.5s -- measured in a real hidden tab,
  // one edit took 13.9 seconds to reach the server. Without a flush, every one
  // of those seconds is work an F5 would lose.
  //
  // Best-effort by nature: encoding and upload are asynchronous and an unload
  // will not wait for them. The debounce remains the real safety net; this
  // narrows the window rather than closing it.
  var flush = function () { if (_writesEnabled) { _captureNow(); } };
  window.addEventListener("beforeunload", flush);
  window.addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", function () {
    // Straight to the capture, deliberately skipping the debounce: the tab is
    // going away and there may be no later timer to wait for.
    if (document.visibilityState === "hidden") flush();
  });
}

window.StudioRecovery = {
  resolve: resolve,
  takeNotices: takeNotices,
  whenResolved: whenResolved,
  restored: function () { return _restored; },
  enableWrites: enableWrites,
  scheduleCapture: scheduleCapture,
  captureNow: _captureNow,
  discard: discard,
  get writesEnabled() { return _writesEnabled; },
  get resolved() { return _resolved; },
};

})();
