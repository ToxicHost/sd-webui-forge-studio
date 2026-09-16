/**
 * BE14: are these fifteen brushes, or fifteen labels?
 *
 * NEAREST NEIGHBOUR IS THE TEST. A set where every preset differs from SOME
 * other preset is easy and worthless -- the owner's complaint was that they
 * felt ALIKE, which is a statement about neighbours. So every preset is
 * compared to its most similar sibling, and even that pair has to differ
 * materially.
 *
 * ON FOUR MEASURABLE AXES, not a digest. Painted area, mean alpha, stroke
 * width and the standard deviation of alpha -- which is where paper and
 * density live. A digest difference alone would pass on two presets separated
 * by one pixel of jitter.
 *
 *   node tests/studio_alpha/be14_measure.js <path to canvas-core.js>
 */

"use strict";

globalThis.window = globalThis;
if (typeof globalThis.ImageData === "undefined") {
  globalThis.ImageData = class ImageData {
    constructor(w, h) {
      this.width = w; this.height = h;
      this.data = new Uint8ClampedArray(w * h * 4);
    }
  };
}

const fs = require("fs");
const realLog = console.log;
console.log = () => {};
(0, eval)(fs.readFileSync(process.argv[2], "utf8"));
console.log = realLog;

const C = window.StudioCore;
const S = C.state;
const W = 512, H = 512;

let _seed = 1;
Math.random = function () {
  _seed |= 0; _seed = (_seed + 0x6D2B79F5) | 0;
  let t = Math.imul(_seed ^ (_seed >>> 15), 1 | _seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const noopCtx = {
  clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
  fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
};

function reset(preset) {
  _seed = 1;
  C.stopAirbrush();
  S.W = W; S.H = H;
  S.tool = "brush";
  S.layers = [{ id: "t", name: "t", visible: true, opacity: 1,
    blendMode: "source-over", canvas: { width: W, height: H }, ctx: noopCtx }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(W * H),
    dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    _taperK: 1, lx: 0, ly: 0, lp: 1, points: [],
    ctx: noopCtx, canvas: { width: W, height: H }, _cachedImg: null,
  };
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  S.drawing = true;
  // NOTHING IS CLEARED BY HAND HERE, and that is the point of this driver.
  //
  // BE1's harness had to clear "the six leaking fields" itself, with a comment
  // saying the harness must not inherit the bug it is measuring. BE14 closed
  // that leak, so this driver deliberately does NOT clear them: if any field
  // still leaks between presets, the nearest-neighbour comparison below will
  // see it as two presets that agree when they should not.
  C.applyBrushPreset(preset);
}

/** The same wave, at the same size, for every preset. */
function paintPreset(name, paper) {
  reset(name);
  S.paper = paper || { texture: "none", scale: 1, depth: 0 };
  const y = h => 256 + Math.sin(h / 55) * 60;
  C.beginStroke(40, y(40), 1.0);
  for (let x = 42; x <= W - 40; x += 2) C.plotTo(x, y(x), 0.35 + 0.65 * (x / W));
  C.finishStroke(W - 40, y(W - 40), 1.0);
  C.stopAirbrush();
  const map = S.stroke.alphaMap;
  let painted = 0, sum = 0, peak = 0;
  const vals = [];
  for (let i = 0; i < map.length; i++) {
    const a = map[i];
    if (a > 0) { painted++; sum += a; vals.push(a); if (a > peak) peak = a; }
  }
  const mean = painted ? sum / painted : 0;
  let varSum = 0;
  for (let i = 0; i < vals.length; i++) varSum += (vals[i] - mean) ** 2;
  // Thickness measured in a fixed column, well past the opening dab.
  let lo = -1, hi = -1;
  for (let yy = 0; yy < H; yy++) {
    if (map[yy * W + 300] > 0) { if (lo < 0) lo = yy; hi = yy; }
  }
  return {
    painted,
    mean: +mean.toFixed(2),
    sd: +Math.sqrt(painted ? varSum / painted : 0).toFixed(2),
    width: lo < 0 ? 0 : hi - lo + 1,
    peak,
  };
}

const NAMES = C.DEFAULT_BRUSH_PRESETS.map(p => p.name);
const PAPER = { texture: "rough", scale: 1, depth: 1 };

const R = {};

// -------------------------------------------------- 1. every preset, measured
{
  const rows = {};
  for (const name of NAMES) rows[name] = paintPreset(name);
  R.presets = rows;
}

// ---------------------------------------- 2. nearest neighbour, on four axes
//
// RELATIVE DIFFERENCE, not difference over the set's range -- and the first
// version used the range, which was wrong in a way that matters.
//
// Dividing by the span of the whole set asks "are these two far apart in the
// set", and at the small end of every axis the answer is always no. Fine Liner
// at 7 pixels wide and Pixel Perfect at 1 differ by SEVEN TIMES and scored
// 0.04, because the set also contains a 147-pixel wash. Two hairlines that
// differ sevenfold are obviously two brushes to anyone holding them.
//
// `|a-b| / (a+b)` asks the right question: how different are these two FROM
// EACH OTHER. It is scale-free, it is 0 for identical values and 1 when one is
// zero and the other is not -- which is exactly right for `sd`, where zero
// means "perfectly flat" and anything else means "textured".
//
// The distance is the LARGEST of the four, not the sum: two presets that
// differ enormously on ONE axis are two different brushes, and a sum would let
// three near-agreements drown it.
{
  const rows = R.presets;
  const axes = ["painted", "mean", "sd", "width"];
  const rel = (a, b) => (a === b) ? 0 : Math.abs(a - b) / (Math.abs(a) + Math.abs(b));
  const dist = (x, y) => Math.max(...axes.map(a => rel(rows[x][a], rows[y][a])));

  const nearest = {};
  for (const n of NAMES) {
    let best = null, bestD = Infinity;
    for (const m of NAMES) {
      if (m === n) continue;
      const d = dist(n, m);
      if (d < bestD) { bestD = d; best = m; }
    }
    nearest[n] = { neighbour: best, distance: +bestD.toFixed(4) };
  }
  const worst = Object.entries(nearest)
    .sort((a, b) => a[1].distance - b[1].distance)[0];
  R.nearestNeighbour = {
    at: nearest,
    closestPair: { preset: worst[0], ...worst[1] },
  };
}

// ------------------------------------------- 3. no two presets render alike
{
  const rows = R.presets;
  const seen = new Map();
  const dupes = [];
  for (const n of NAMES) {
    const key = JSON.stringify(rows[n]);
    if (seen.has(key)) dupes.push([seen.get(key), n]);
    else seen.set(key, n);
  }
  R.noTwoRenderAlike = { duplicates: dupes, count: NAMES.length };
}

// ------------------------------------- 4. every preset writes every field
//
// THE LEAK BE14 CLOSED. Apply a preset that sets every field to something
// unusual, then apply each preset in turn and read the state back. Anything
// that still carries the unusual value is a field that preset did not write.
{
  const FIELDS = ["brushRatio", "brushSpikes", "brushDensity", "brushAngle",
                  "brushTaperIn", "brushFalloff", "brushGrain", "brushBuildup",
                  "brushAirbrush", "brushAliased", "brushPixelPerfect",
                  "brushSizeMode", "brushHardness", "brushOpacity", "brushFlow",
                  "smoothing"];
  const POISON = {
    brushRatio: 0.17, brushSpikes: 11, brushDensity: 0.13, brushAngle: 137,
    brushTaperIn: 0.71, brushFalloff: "gaussian", brushGrain: 0.61,
    brushBuildup: true, brushAirbrush: true, brushAliased: true,
    brushPixelPerfect: true, brushSizeMode: "document_pixels",
    brushHardness: 0.37, brushOpacity: 0.29, brushFlow: 0.19, smoothing: 17,
  };
  const leaks = {};
  for (const name of NAMES) {
    reset("Basic Round");
    for (const f of FIELDS) S[f] = POISON[f];
    C.applyBrushPreset(name);
    const stuck = FIELDS.filter(f => S[f] === POISON[f] && !_legitimate(name, f));
    if (stuck.length) leaks[name] = stuck;
  }
  R.everyFieldIsWritten = { leaks, count: NAMES.length };
}

/**
 * A preset may legitimately WANT the poison value.
 *
 * Checked against the preset's own declaration rather than assumed, so a
 * preset that genuinely asks for `falloff: "gaussian"` is not reported as a
 * leak -- and a preset that does NOT ask for it still is.
 */
function _legitimate(name, field) {
  const p = C.DEFAULT_BRUSH_PRESETS.find(e => e.name === name);
  if (!p) return false;
  const declared = {
    brushRatio: p.ratio, brushSpikes: p.spikes, brushDensity: p.density,
    brushAngle: p.angle, brushTaperIn: p.taperIn, brushFalloff: p.falloff,
    brushGrain: p.grain, brushBuildup: !!p.buildup, brushAirbrush: !!p.airbrush,
    brushAliased: !!p.aliased, brushPixelPerfect: !!p.pixelPerfect,
    brushSizeMode: p.sizeMode === "document_pixels" ? "document_pixels" : "relative",
    brushHardness: p.hardness / 100, brushOpacity: p.opacity / 100,
    brushFlow: Math.max(0.01, p.flow / 100), smoothing: p.smoothing,
  };
  return declared[field] === S[field];
}

// ---------------------------------------- 5. old names still resolve
{
  const rows = {};
  for (const [old, now] of Object.entries(C.BRUSH_PRESET_ALIASES)) {
    reset("Basic Round");
    const ok = C.applyBrushPreset(old);
    const target = C.DEFAULT_BRUSH_PRESETS.find(p => p.name === now);
    rows[old] = {
      resolved: ok,
      to: now,
      matches: ok && target != null && S.brushGrain === target.grain,
    };
  }
  R.oldNamesResolve = {
    at: rows,
    all: Object.values(rows).every(r => r.matches),
    // And an unknown name still changes nothing, which is what makes the
    // alias table necessary rather than decorative.
    unknownIsRefused: (() => { reset("Pencil"); const g = S.brushGrain;
      return C.applyBrushPreset("No Such Brush") === false && S.brushGrain === g; })(),
  };
}

// ------------------------------------ 6. every preset has a truthful description
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    rows[p.name] = {
      has: typeof p.desc === "string" && p.desc.length > 10,
      length: (p.desc || "").length,
    };
  }
  R.descriptions = {
    at: rows,
    all: Object.values(rows).every(r => r.has),
    // Distinct, because fifteen copies of "a brush" would satisfy the above.
    distinct: new Set(C.DEFAULT_BRUSH_PRESETS.map(p => p.desc)).size,
  };
}

// -------------------------- 7. the paper is revealed differently by neighbours
//
// BE10's acceptance criterion, shipped as presets: Charcoal and Pastel share
// the same paper at nearly the same tooth and must not produce the same mark.
{
  const rows = {};
  for (const name of NAMES) rows[name] = paintPreset(name, PAPER);
  const pairs = [["Charcoal", "Pastel"], ["Pencil", "Sketch Light"],
                 ["Hard Ink", "Fine Liner"], ["Marker", "Flat Chisel"]];
  R.onPaper = {
    at: rows,
    pairsDiffer: pairs.map(([a, b]) => ({
      pair: a + " vs " + b,
      meanGap: +Math.abs(rows[a].mean - rows[b].mean).toFixed(2),
      widthGap: Math.abs(rows[a].width - rows[b].width),
      sdGap: +Math.abs(rows[a].sd - rows[b].sd).toFixed(2),
    })),
  };
}

// -------------------------------- 8. the engine's capabilities are all used
//
// BE6 proved Ratio, Spikes, Density, Angle and Falloff alive on every tip, and
// not one shipped preset set any of them except Scatter Dust's density. A
// brush set that never uses half its own engine is a brush set that feels
// alike.
{
  const used = { ratio: [], spikes: [], density: [], angle: [], taperIn: [],
                 falloffGauss: [], buildup: [], airbrush: [], aliased: [],
                 pixelPerfect: [], curves: [], grain: [] };
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    if (p.ratio !== 1) used.ratio.push(p.name);
    if (p.spikes !== 2) used.spikes.push(p.name);
    if (p.density !== 1) used.density.push(p.name);
    if (p.angle !== 0) used.angle.push(p.name);
    if (p.taperIn !== 0) used.taperIn.push(p.name);
    if (p.falloff === "gaussian") used.falloffGauss.push(p.name);
    if (p.buildup) used.buildup.push(p.name);
    if (p.airbrush) used.airbrush.push(p.name);
    if (p.aliased) used.aliased.push(p.name);
    if (p.pixelPerfect) used.pixelPerfect.push(p.name);
    if ((p.curves || []).length) used.curves.push(p.name);
    if (p.grain > 0) used.grain.push(p.name);
  }
  R.capabilitiesAreUsed = {
    at: Object.fromEntries(Object.entries(used).map(([k, v]) => [k, v.length])),
    unused: Object.entries(used).filter(([, v]) => !v.length).map(([k]) => k),
    detail: used,
  };
}

console.log(JSON.stringify(R, null, 2));
