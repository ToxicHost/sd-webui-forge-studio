/**
 * BE17: paint accumulates within a stroke, and Flow stops being Opacity.
 *
 * THE OWNER'S REPORT: "Flow does not do what you say it does. It's literally
 * just opacity in how it currently functions." Measured before the change, on a
 * 300px stroke, (Flow 35, Opacity 100) against (Flow 100, Opacity 35): peak 89
 * against 89, and ZERO pixels differing by more than 8/255 on Basic Round, Soft
 * Round, Hard Ink and Pencil. Painting back and forth over one place inside a
 * single stroke -- 1, 2, 5, 20 passes -- read 89, 89, 89, 89.
 *
 * A SINGLE PASS CANNOT TELL THE TWO APART, AND MUST NOT. Flow is what one pass
 * deposits; Opacity is what the stroke may reach. On a stroke that never
 * touches itself those are the same number by construction, and a driver that
 * expected them to differ there would be measuring its own misunderstanding.
 * The discriminator is OVERLAP: go over the mark again and Flow keeps
 * depositing while Opacity holds the ceiling.
 *
 * EVERYTHING IS READ ON THE LAYER, through the engine's own
 * `alphaMapToImageData` and the single commit-time multiply -- never off
 * `S.stroke.alphaMap`. Half the wrong numbers in this programme came from
 * reading the accumulator and reporting it as what the owner sees.
 *
 *   node be17_measure.js <path to canvas-core.js> [path to a second engine]
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

const W = 320, H = 320;

const noopCtx = {
  clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
  fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
};

/** Load one engine into its own global, so two can be compared in one process. */
function loadEngine(path) {
  const vm = require("vm");
  const g = {};
  g.window = g;
  g.ImageData = globalThis.ImageData;
  g.console = { log() {}, warn() {}, error() {} };
  let seed = 1;
  g.Math = Object.create(Math);
  g.Math.random = function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  g.__reseed = () => { seed = 1; };
  vm.createContext(g);
  const src = fs.readFileSync(path, "utf8");
  vm.runInContext(src, g, { filename: path });
  const C = g.window.StudioCore;
  // WHICH COMMIT-TIME BOUND THIS ENGINE USES, read off the engine rather than
  // passed in. BE17 moved Opacity inside the stroke for Buildup presets and
  // commits those at 1; every engine before it committed at Opacity always.
  // Reading it wrong reports the OLD Airbrush at 246 instead of 123 and turns
  // a correct re-tune into a panic.
  const buildupCommitsAtOne = /S\.brushBuildup \? 1 : S\.brushOpacity/.test(src);
  return { g, C, S: C.state, buildupCommitsAtOne };
}

function newDocument(E, preset) {
  const { C, S, g } = E;
  g.__reseed();
  if (C.stopAirbrush) C.stopAirbrush();
  S.W = W; S.H = H;
  S.tool = "brush";
  S.layers = [{ id: "t", name: "t", visible: true, opacity: 1,
    blendMode: "source-over", canvas: { width: W, height: H }, ctx: noopCtx }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(W * H), accum: null,
    dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    frameDirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    _taperK: 1, lx: 0, ly: 0, lp: 1, points: [],
    ctx: noopCtx, canvas: { width: W, height: H }, _cachedImg: null,
  };
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  S.paper = { texture: "none", scale: 1, depth: 0 };
  S.undoStack = []; S.redoStack = [];
  S.drawing = true;
  if (preset) C.applyBrushPreset(preset);
  S.smoothing = 0;
  S.brushDynamics = Object.assign({}, S.brushDynamics,
    { sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0 });
}

/**
 * THE ALPHA THAT REACHES THE LAYER.
 *
 * The engine's own converter, then the one commit-time multiply. Buildup moves
 * Opacity inside the stroke, so the bound at commit is 1 for those presets --
 * reading it any other way applies Opacity twice and reports a preset as
 * broken when the driver is.
 */
function layerAlpha(E) {
  const { C, S } = E;
  const img = C.alphaMapToImageData("#000000",
    { x0: 0, y0: 0, x1: W - 1, y1: H - 1 });
  const bound = (E.buildupCommitsAtOne && S.brushBuildup) ? 1 : S.brushOpacity;
  const out = new Uint8Array(W * H);
  for (let i = 0; i < W * H; i++) out[i] = Math.round(img.data[i * 4 + 3] * bound);
  return out;
}

function paintPasses(E, preset, opts) {
  const { C, S } = E;
  const o = opts || {};
  newDocument(E, preset);
  if (o.flow !== undefined) S.brushFlow = o.flow;
  if (o.opacity !== undefined) S.brushOpacity = o.opacity;
  if (o.hardness !== undefined) S.brushHardness = o.hardness;
  if (o.size !== undefined) S.brushSize = o.size;
  // SPACING LIVES ON `brushDynamics`, not on a `brushSpacing` field. The first
  // version of this driver wrote `S.brushSpacing` and reported a spread of
  // EXACTLY 0.00 across six spacings at three hardnesses -- a perfect result
  // produced by a sweep that swept nothing. Too clean is the tell.
  if (o.spacing !== undefined) {
    S.brushDynamics = Object.assign({}, S.brushDynamics, { spacing: o.spacing });
  }
  if (o.buildup !== undefined) S.brushBuildup = o.buildup;
  const passes = o.passes || 1;
  const y = 160, x0 = 80, x1 = 240;
  C.beginStroke(x0, y, 1.0);
  for (let k = 0; k < passes; k++) {
    const fwd = k % 2 === 0;
    for (let i = 1; i <= 40; i++) {
      const t = fwd ? i / 40 : 1 - i / 40;
      C.plotTo(x0 + t * (x1 - x0), y, 1.0);
    }
  }
  C.finishStroke(passes % 2 === 1 ? x1 : x0, y, 1.0);
  const out = layerAlpha(E);
  if (C.stopAirbrush) C.stopAirbrush();
  return out;
}

/** The mark's core value: the middle of the band, averaged along it. */
function coreMean(a) {
  let sum = 0, n = 0;
  for (let x = 120; x <= 200; x++) { sum += a[160 * W + x]; n++; }
  return +(sum / n).toFixed(2);
}

function painted(a) { let n = 0; for (let i = 0; i < a.length; i++) if (a[i]) n++; return n; }

function visiblyDifferent(a, b) {
  let any = 0, diff = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] || b[i]) any++;
    if (Math.abs(a[i] - b[i]) > 8) diff++;
  }
  return any ? +(diff / any).toFixed(4) : 0;
}

const NOW = loadEngine(process.argv[2]);
const WAS = process.argv[3] ? loadEngine(process.argv[3]) : null;

const R = {};
const PRESETS = NOW.C.state.brushPresets
  ? null : null;

// ------------------------------- 0. the instrument, before any number it makes
//
// A metric that cannot separate two things it is about to call equal is not
// evidence. Known-same and known-different, both stated.
{
  const a = paintPasses(NOW, "Basic Round", { flow: 0.35, opacity: 1.0 });
  const b = paintPasses(NOW, "Basic Round", { flow: 0.35, opacity: 1.0 });
  const c = paintPasses(NOW, "Basic Round", { flow: 0.40, opacity: 1.0 });
  R.instrument = {
    deterministic: visiblyDifferent(a, b),
    flow35VsFlow40: visiblyDifferent(a, c),
    coreAt35: coreMean(a),
    coreAt40: coreMean(c),
  };
}

// -------------------------------------- 1. Flow is no longer a second Opacity
//
// A SINGLE PASS cannot tell them apart and must not. The discriminator is
// overlap: eight passes over the same corridor inside one stroke.
{
  const rows = {};
  for (const preset of ["Basic Round", "Soft Round", "Hard Ink", "Pencil", "Sketch Light"]) {
    const onePassFlow = paintPasses(NOW, preset, { flow: 0.35, opacity: 1.0 });
    const onePassOp = paintPasses(NOW, preset, { flow: 1.0, opacity: 0.35 });
    const workedFlow = paintPasses(NOW, preset, { flow: 0.35, opacity: 1.0, passes: 8 });
    const workedOp = paintPasses(NOW, preset, { flow: 1.0, opacity: 0.35, passes: 8 });
    rows[preset] = {
      onePassFlow: coreMean(onePassFlow),
      onePassOpacity: coreMean(onePassOp),
      workedFlow: coreMean(workedFlow),
      workedOpacity: coreMean(workedOp),
      differAfterWorking: visiblyDifferent(workedFlow, workedOp),
    };
  }
  R.flowIsNotOpacity = rows;
}

// ------------------------------------------ 2. working an area darkens it
{
  const rows = {};
  for (const preset of ["Basic Round", "Soft Round", "Sketch Light", "Pencil", "Flat Chisel"]) {
    rows[preset] = {};
    for (const n of [1, 2, 4, 8]) {
      rows[preset]["passes" + n] =
        coreMean(paintPasses(NOW, preset, { flow: 0.35, opacity: 1.0, passes: n }));
    }
  }
  R.workingAnAreaDarkensIt = rows;
}

// ---------------------------------- 3. Opacity is the bound, within the stroke
//
// It must REACH the bound and STOP. A model that keeps climbing past it has
// made Opacity a rate, which is what Buildup is for and what Off must not do.
{
  const rows = {};
  for (const op of [1.0, 0.5, 0.35]) {
    const at = {};
    for (const n of [1, 2, 4, 8, 16, 32]) {
      at["passes" + n] =
        coreMean(paintPasses(NOW, "Basic Round", { flow: 0.35, opacity: op, passes: n }));
    }
    rows["opacity" + Math.round(op * 100)] = at;
  }
  R.opacityBoundsTheStroke = rows;
}

// ---------------------------------- 4. one pass deposits exactly Flow
//
// Across hardness and spacing, because the whole point of normalising against
// overlap is that neither may change what a pass deposits.
{
  const rows = {};
  for (const hardness of [0, 0.5, 1.0]) {
    for (const spacing of [0.02, 0.08, 0.32]) {
      const at = {};
      for (const flow of [0.1, 0.25, 0.4, 0.6, 0.85]) {
        const a = paintPasses(NOW, "Basic Round",
          { flow, opacity: 1.0, hardness, spacing });
        at["flow" + Math.round(flow * 100)] =
          +(coreMean(a) - flow * 255).toFixed(1);
      }
      rows[`hard${hardness}_spacing${spacing}`] = at;
    }
  }
  R.onePassErrorAgainstFlow = rows;
}

// ------------------------------- 5. Spacing is not a darkness control
//
// MEASURED AT THE DAB, NOT AVERAGED ALONG THE BAND. The mean along a stroke
// legitimately falls as spacing widens, because at Spacing 50 the dabs stop
// touching and the pixels BETWEEN them have less paint on them for the honest
// reason that no tip was ever there. That is a SMOOTHNESS consequence -- it is
// what DiVerdi Fig 2.6 calls the un-smooth silhouette -- and rolling it into
// this number would report a scalloped stroke as a darkness bug.
//
// The first version of this measurement did exactly that and read a spread of
// 23 / 67 / 91. What Flow owes is that where the brush actually LANDED, the
// amount of paint does not depend on how often it landed.
{
  const rows = {};
  for (const hardness of [0, 0.5, 1.0]) {
    const peaks = [], means = [];
    for (const spacing of [0.02, 0.05, 0.10, 0.20, 0.35, 0.50]) {
      const a = paintPasses(NOW, "Basic Round",
        { flow: 0.4, opacity: 1.0, hardness, spacing, passes: 4 });
      let peak = 0;
      for (let x = 120; x <= 200; x++) {
        const v = a[160 * W + x];
        if (v > peak) peak = v;
      }
      peaks.push(peak);
      means.push(coreMean(a));
    }
    rows["hard" + hardness] = {
      peakAt: peaks,
      peakSpread: Math.max(...peaks) - Math.min(...peaks),
      meanAt: means,
    };
  }
  R.spacingIsNotADarknessControl = rows;
}

// -------------------------- 6. the crease at a corner, which max() cut
//
// max(a, b) of two smooth bumps has a GRADIENT discontinuity where they are
// equal, and the eye reads that as a hard edge. Measured as the sharpest
// second difference along a scan line crossing the bisector of a turn.
function cornerSharpness(E, turnDeg) {
  const { C, S } = E;
  newDocument(E, "Soft Round");
  S.brushHardness = 0; S.brushFlow = 0.4; S.brushOpacity = 1.0;
  const phi = (180 - turnDeg) * Math.PI / 180;
  const L = 90, vx = 160, vy = 210;
  C.beginStroke(vx - L, vy, 1.0);
  for (let i = 1; i <= 40; i++) C.plotTo(vx - L + (L * i) / 40, vy, 1.0);
  for (let i = 1; i <= 40; i++) {
    const t = (L * i) / 40;
    C.plotTo(vx + t * Math.cos(phi), vy - t * Math.sin(phi), 1.0);
  }
  C.finishStroke(vx + L * Math.cos(phi), vy - L * Math.sin(phi), 1.0);
  const a = layerAlpha(E);
  if (C.stopAirbrush) C.stopAirbrush();
  // Scan across the bisector, a little above the vertex.
  const y = vy - 26;
  let worst = 0;
  for (let x = vx - 40; x <= vx + 40; x++) {
    const d2 = Math.abs(a[y * W + x - 1] - 2 * a[y * W + x] + a[y * W + x + 1]);
    if (d2 > worst) worst = d2;
  }
  return worst;
}
{
  const rows = {};
  for (const deg of [150, 120, 90]) rows["turn" + deg] = cornerSharpness(NOW, deg);
  R.cornerSharpness = rows;
}

// ------------------------------ 7. low Flow on a DENSE preset still paints
//
// The 8-bit store could not hold a normalised contribution: Airbrush lays 55.6
// dabs per pixel, so at Flow 15 one dab rounded to ZERO and the brush painted
// nothing at all. This is the guard on the 16-bit accumulator.
{
  const rows = {};
  for (const preset of ["Airbrush", "Charcoal"]) {
    rows[preset] = {};
    for (const flow of [0.05, 0.10, 0.15, 0.35]) {
      const a = paintPasses(NOW, preset, { flow, opacity: 1.0, buildup: false });
      rows[preset]["flow" + Math.round(flow * 100)] =
        { core: coreMean(a), painted: painted(a) };
    }
  }
  R.lowFlowOnADensePresetStillPaints = rows;
}

// ------------- 7a. low flow on a dense preset must BUILD, not merely paint
//
// THE PEAK FLOOR MASKS THE PRECISION LOSS ON A SINGLE PASS, which is what an
// earlier version of this driver missed: at Airbrush Flow 15 an 8-bit
// accumulator rounds every dab to zero, but `max(acc, peak)` still returns the
// single-dab peak, so the mark is there and the brush looks fine. What is gone
// is the ACCUMULATION -- working the area adds nothing, because nothing ever
// gets into the accumulator to add.
//
// So the 16-bit store has to be guarded on building, not on painting.
{
  const rows = {};
  for (const preset of ["Airbrush", "Charcoal"]) {
    rows[preset] = {};
    for (const flow of [0.10, 0.15]) {
      const one = coreMean(paintPasses(NOW, preset,
        { flow, opacity: 1.0, buildup: false, passes: 1 }));
      const eight = coreMean(paintPasses(NOW, preset,
        { flow, opacity: 1.0, buildup: false, passes: 8 }));
      rows[preset]["flow" + Math.round(flow * 100)] =
        { onePass: one, eightPasses: eight, gained: +(eight - one).toFixed(2) };
    }
  }
  R.lowFlowOnADensePresetStillBuilds = rows;
}

// ------- 7a2. the threaded step matters where re-deriving it would be wrong
//
// `2 * spacingFraction()` IS the correct step for a ROUND tip at no pressure --
// the algebra collapses to it -- so a round-tip fixture cannot tell a threaded
// step from a re-derived one, and an earlier version of this driver used only
// Basic Round and let that mutation walk straight through.
//
// Two cases separate them. An ANISOTROPIC tip, where `spacingFor` multiplies
// the gap by the tip's extent along travel and the re-derivation does not; and
// a gap small enough for `spacingFor`'s half-pixel clamp to bind, where the
// fraction stops describing the distance actually advanced.
{
  const rows = {};
  for (const preset of ["Flat Chisel", "Marker"]) {
    // Along the tip's long axis and across it: the extent term differs most
    // between the two, so if threading is broken the two disagree.
    const along = coreMean(paintPasses(NOW, preset, { flow: 0.4, opacity: 1.0 }));
    rows[preset] = { alongTravel: along, errorVsFlow: +(along - 0.4 * 255).toFixed(1) };
  }
  // The clamp case: a small tip at the tightest shipped spacing.
  const clamped = coreMean(paintPasses(NOW, "Basic Round",
    { flow: 0.4, opacity: 1.0, hardness: 0, spacing: 0.02, size: 8 }));
  rows.smallTipTightSpacing = {
    core: clamped, errorVsFlow: +(clamped - 0.4 * 255).toFixed(1),
  };
  R.theThreadedStepMatters = rows;
}

// ------------------- 7b. a hard tip keeps its hard cross-section
//
// THE PEAK FLOOR, as a number a mutation can break. Pure accumulation puts the
// centreline in the right place and gets the SHAPE wrong: dab count falls off
// faster at the rim of the swept band than the tip's own profile does, so the
// band acquires a soft shoulder. Measured at hardness 1.0, Flow 50:
//
//     with the floor    127 127 127 127 127 127 127
//     without it         62  91 109 116 124 124 124
//
// A soft shoulder on a brush whose whole point is that it has none.
{
  const a = paintPasses(NOW, "Basic Round",
    { flow: 0.5, opacity: 1.0, hardness: 1.0 });
  // Across the band at its middle, inward from the edge.
  const profile = [];
  for (let dy = -6; dy <= 6; dy++) profile.push(a[(160 + dy) * W + 160]);
  // THE INTERIOR IS WHAT IS INSIDE THE ANTIALIASING BAND, not merely what is
  // non-zero. Those were the same thing while the band had ZERO width, and the
  // first version of this metric took every non-zero sample -- which was
  // accidentally correct until BE18 gave a hard tip a one-pixel rim, and then
  // reported an interior gradient of 66 for a profile reading
  // `61 127 127 127 127 127 127 127 127 127 127 127 61`. The plateau was
  // perfect; the metric was counting the rim as part of it.
  const nonZero = profile.map((v, i) => [v, i]).filter(([v]) => v > 0);
  const inner = nonZero.length > 2
    ? nonZero.slice(1, -1).map(([v]) => v)
    : nonZero.map(([v]) => v);
  const rim = nonZero.length > 2
    ? [nonZero[0][0], nonZero[nonZero.length - 1][0]] : [];
  R.aHardTipKeepsItsShape = {
    profile,
    interiorSpread: inner.length ? Math.max(...inner) - Math.min(...inner) : 0,
    interiorSamples: inner.length,
    rim,
    peak: Math.max(...profile),
  };
}

// ------------------- 7c. Flow 100 keeps the tip's own antialiased rim
//
// At Flow 100 one dab already deposits everything the pass may deposit, so the
// only pixels left for an accumulator to build on are the tip's antialiased
// rim -- and building on those makes the edge HARDER, which is the wrong
// direction. The short-circuit above target 0.995 is what stops it. Measured
// without it: Basic Round lost 1016 of 5200 painted pixels' worth of
// antialiasing, worst case 86/255.
{
  const a = paintPasses(NOW, "Basic Round",
    { flow: 1.0, opacity: 1.0, hardness: 0.5 });
  let rim = 0, solid = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] > 0 && a[i] < 250) rim++;
    else if (a[i] >= 250) solid++;
  }
  R.flow100KeepsItsRim = { rimPixels: rim, solidPixels: solid };
}

// --------------------------- 8. a single pass is what it was, preset by preset
{
  if (WAS) {
    const rows = {};
    for (const preset of ["Basic Round", "Soft Round", "Hard Ink", "Fine Liner",
                          "Pencil", "Sketch Light", "Flat Chisel", "Marker",
                          "Calligraphy", "Charcoal", "Pastel", "Scatter Dust",
                          "Bristle Rake", "Pixel Perfect", "Ink Wash", "Airbrush"]) {
      const now = paintPasses(NOW, preset, {});
      const was = paintPasses(WAS, preset, {});
      rows[preset] = {
        differs: visiblyDifferent(was, now),
        coreWas: coreMean(was),
        coreNow: coreMean(now),
      };
    }
    R.aSinglePassIsWhatItWas = rows;
  }
}

console.log(JSON.stringify(R, null, 2));
