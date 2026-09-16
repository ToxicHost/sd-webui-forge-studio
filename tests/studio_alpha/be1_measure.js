/**
 * BE1: measure the REAL brush engine, headless.
 *
 * `canvas-core.js` has zero module-scope browser references, so the shipped
 * file loads in Node behind a two-line `window` shim and stamps into a real
 * Uint8Array. That matters: these guards render through the ACTUAL engine, not
 * through a Python transcription of it.
 *
 * The addendum forbids "a test that compares a renderer against output produced
 * by the same incomplete renderer". A transcription would be exactly that --
 * it would agree with the engine's bugs. Loading the engine and asserting
 * INDEPENDENT required properties of its pixels is the alternative.
 *
 * Emits one JSON object describing everything BE1 needs, so the Python suite
 * spawns Node once rather than once per assertion.
 *
 *   node measure.js <path-to-canvas-core.js>
 */

"use strict";

globalThis.window = globalThis;

// The one browser global the engine needs outside a page. `alphaMapToImageData`
// allocates an ImageData to write coverage into, and Node has none. This is the
// real shape -- a Uint8ClampedArray of w*h*4 -- not a stand-in that would let a
// wrong index pass unnoticed.
if (typeof globalThis.ImageData === "undefined") {
  globalThis.ImageData = class ImageData {
    constructor(w, h) {
      this.width = w; this.height = h;
      this.data = new Uint8ClampedArray(w * h * 4);
    }
  };
}

const fs = require("fs");
const CORE_PATH = process.argv[2];
const source = fs.readFileSync(CORE_PATH, "utf8");

// Silence the module's own load banner so stdout carries only our JSON.
const realLog = console.log;
console.log = () => {};
(0, eval)(source);
console.log = realLog;

const C = window.StudioCore;
const S = C.state;

// 512 square. Small documents are a trap here: Brush Size is RELATIVE to the
// document short side, so at 96px every preset collapses to a 1-4px dab and
// distinct presets produce identical pixels for reasons that have nothing to
// do with the preset. 512 resolves size 12 to a 21px dab.
const W = 512, H = 512;

// ---- determinism -------------------------------------------------------
//
// THE ENGINE USES Math.random(): scatter placement, size/opacity/rotation
// jitter, and the density skip all call it. Two stamps at IDENTICAL settings
// therefore produce different pixels, and a naive "did the output change?"
// comparison reports EVERY control as working -- including the ones measured
// dead in a browser.
//
// That is not a hypothetical. The first run of this file reported all five
// controls working on all four tips, which is false. A guard that cannot fail
// is worse than no guard, so randomness is replaced with a seeded PRNG reset
// before every stamp, and `selfCheck` below re-runs an identical pair to prove
// the comparison is sound before any result is trusted.
let _seed = 1;
function seededRandom() {
  // mulberry32 -- small, fast, and good enough that a stamp is not accidentally
  // uniform. The exact generator does not matter; reproducibility does.
  _seed |= 0; _seed = (_seed + 0x6D2B79F5) | 0;
  let t = Math.imul(_seed ^ (_seed >>> 15), 1 | _seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
Math.random = seededRandom;

/** A clean stroke buffer and a clean set of every field a preset should own. */
function reset() {
  _seed = 1;   // same random stream for every stamp
  S.W = W; S.H = H;
  S.stroke = S.stroke || {};
  S.stroke.alphaMap = new Uint8Array(W * H);
  S.stroke.dirty = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
  S.stroke._taperK = 1;
  S.stroke.lx = 0; S.stroke.ly = 0; S.stroke.lp = 1;
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = false;
  // BE19 SHIPPED THE PAPER SWITCHED ON, so a fixture that does not pin it now
  // inherits a surface. This harness measures selection bounds, tip geometry
  // and control liveness -- none of which are about the paper -- and BE4's
  // `soft_100` moved the moment the default changed. A fixture must control
  // what it is not testing, or it reports another package's change as its own.
  S.paper = { texture: "none", scale: 1, depth: 0 };
  S.symmetry = "none";
  S.pressureSensitivity = false;
  S.pressureAffects = "none";
  // CLEAR THE SIX LEAKING FIELDS.
  //
  // applyBrushPreset does not reset these -- that is the defect BE1 exists to
  // catch -- so without clearing them here the leak propagates between the
  // measurements below and corrupts them. It already did: a `brushFalloff`
  // left at "gaussian" by an earlier probe silently zeroed every small dab in
  // the size report.
  //
  // The harness must not inherit the bug it is measuring.
  S.brushAngle = 0;
  S.brushRatio = 1.0;
  S.brushSpikes = 2;
  S.brushDensity = 1.0;
  S.brushFalloff = "default";
  S.brushTaperIn = 0;
}

/** Stamp once and describe the coverage. */
function stamp(mutate) {
  reset();
  C.applyBrushPreset("Basic Round");
  S.brushSize = 26;
  if (mutate) mutate();
  C.stampWet(W / 2, H / 2, 1.0);
  const m = S.stroke.alphaMap;
  let peak = 0, count = 0, sum = 0;
  // A position-sensitive digest: two different SHAPES with identical pixel
  // counts must still differ. Summing alpha alone would not catch a rotation.
  let digest = 0;
  for (let i = 0; i < m.length; i++) {
    const a = m[i];
    if (!a) continue;
    count++; sum += a;
    if (a > peak) peak = a;
    digest = (digest + a * (i + 1) * 2654435761) % 2147483647;
  }
  return { peak, count, sum, digest };
}

function differs(a, b) {
  return a.digest !== b.digest || a.count !== b.count || a.sum !== b.sum;
}

/**
 * Prove the comparison itself is trustworthy before believing any result.
 *
 * Stamps the SAME settings twice on every tip. If those differ, the engine is
 * still nondeterministic here and every "works" verdict below is meaningless.
 * Reported rather than silently assumed, because the first version of this
 * file was wrong in exactly that way.
 */
function selfCheck() {
  const report = {};
  for (const tip of ["round", "flat", "marker", "scatter"]) {
    const a = stamp(() => { S.brushPreset = tip; });
    const b = stamp(() => { S.brushPreset = tip; });
    report[tip] = !differs(a, b);
  }
  // And with the jitter-heavy preset, which is where randomness actually lives.
  const j1 = stamp(() => { C.applyBrushPreset("Scatter Dust"); S.brushSize = 26; });
  const j2 = stamp(() => { C.applyBrushPreset("Scatter Dust"); S.brushSize = 26; });
  report.scatterDustPreset = !differs(j1, j2);
  report.allDeterministic = Object.values(report).every(Boolean);
  return report;
}

// ---- 1. preset field reset --------------------------------------------
//
// Which fields does applyBrushPreset actually write? Detected by mutating
// each to a distinctive value, applying a preset, and seeing what survives.
// Detected rather than declared, so the answer cannot go stale.
const CHARACTER_FIELDS = {
  brushAngle: 37,
  brushRatio: 0.37,
  brushSpikes: 7,
  brushDensity: 0.37,
  brushFalloff: "gaussian",
  brushTaperIn: 0.37,
};
const CORE_FIELDS = {
  brushSize: 99, brushHardness: 0.37, brushOpacity: 0.37,
  brushFlow: 0.37, brushBuildup: true, smoothing: 9,
};

function presetResetReport() {
  const survives = {};
  for (const [field, poison] of Object.entries({ ...CHARACTER_FIELDS, ...CORE_FIELDS })) {
    reset();
    S[field] = poison;
    C.applyBrushPreset("Basic Round");
    survives[field] = S[field] === poison;   // true == LEAKED
  }
  return survives;
}

// ---- 2. per-tip support matrix ----------------------------------------
//
// Every control against every tip family, measured separately. The brushAngle
// error that started this program came from proving a control on ONE tip and
// assuming the rest.
const TIPS = ["round", "flat", "marker", "scatter"];
const CONTROLS = {
  brushRatio:   [1.0, 0.35],
  brushSpikes:  [2, 6],
  brushAngle:   [0, 45],
  brushDensity: [1.0, 0.35],
  brushFalloff: ["default", "gaussian"],
};

function supportMatrix() {
  const matrix = {};
  for (const tip of TIPS) {
    matrix[tip] = {};
    for (const [control, [low, high]] of Object.entries(CONTROLS)) {
      const a = stamp(() => { S.brushPreset = tip; S[control] = low; });
      const b = stamp(() => { S.brushPreset = tip; S[control] = high; });
      matrix[tip][control] = differs(a, b) ? "works" : "dead";
    }
  }
  return matrix;
}

/**
 * The `needs-shape` controls, measured where they CAN act.
 *
 * Angle and Spikes cannot change a circle: it has no orientation, and a
 * rotation of a rotationally symmetric shape is the same shape. Reporting them
 * "dead" on a round tip at ratio 1.0 is therefore correct but useless -- it
 * says nothing about whether they are WIRED.
 *
 * So each is measured again with Ratio at 0.4, where the tip has an axis to
 * turn. That is the difference between "mathematically inert here" and "not
 * connected", and BE6 exists because those two were indistinguishable.
 */
function needsShapeMatrix() {
  const matrix = {};
  for (const tip of TIPS) {
    matrix[tip] = {};
    for (const [control, [low, high]] of [["brushAngle", [0, 45]], ["brushSpikes", [2, 6]]]) {
      const a = stamp(() => { S.brushPreset = tip; S.brushRatio = 0.4; S[control] = low; });
      const b = stamp(() => { S.brushPreset = tip; S.brushRatio = 0.4; S[control] = high; });
      matrix[tip][control] = differs(a, b) ? "works" : "dead";
    }
  }
  return matrix;
}

/** The engine's own declared contract, so a test can compare promise to fact. */
function declaredCapabilities() {
  return C.TIP_CAPABILITIES || null;
}

// ---- 3. spikes on a perfect circle ------------------------------------
//
// The one intentional no-difference case, asserted WITH its reason rather than
// waved through: a rotational fold followed by an isotropic norm cannot change
// the norm. Spikes must be neutral at ratio 1.0 and must bite once anisotropy
// is applied after the fold.
function spikesReport() {
  const circle2 = stamp(() => { S.brushPreset = "round"; S.brushRatio = 1.0; S.brushSpikes = 2; });
  const circle6 = stamp(() => { S.brushPreset = "round"; S.brushRatio = 1.0; S.brushSpikes = 6; });
  const ellipse2 = stamp(() => { S.brushPreset = "round"; S.brushRatio = 0.4; S.brushSpikes = 2; });
  const ellipse6 = stamp(() => { S.brushPreset = "round"; S.brushRatio = 0.4; S.brushSpikes = 6; });
  return {
    neutralOnCircle: !differs(circle2, circle6),
    bitesOnEllipse: differs(ellipse2, ellipse6),
  };
}

// ---- 4. selection is an amount, not a rate ----------------------------
function selectionReport() {
  const run = (presetName, pct, dabs) => {
    reset();
    C.applyBrushPreset(presetName);
    S.brushSize = 26;
    if (pct !== null) {
      S.selection = {
        active: true, mask: new Uint8Array(W * H).fill(Math.round(255 * pct / 100)),
        rect: null, dragging: false,
      };
    }
    for (let i = 0; i < dabs; i++) C.stampWet(W / 2 + (i % 2), H / 2, 1.0);
    // BE4 moved selection to merge, so the alpha MAP is deliberately
    // pre-selection now. What the owner gets is what `alphaMapToImageData`
    // writes, so that is what this measures -- reading the map would report
    // the bound as missing when it is merely applied later.
    S.stroke.dirty = { x0: 0, y0: 0, x1: W - 1, y1: H - 1 };
    S.stroke._cachedImg = null;
    const img = C.alphaMapToImageData("#ffffff");
    let peak = 0;
    for (let i = 3; i < img.data.length; i += 4) if (img.data[i] > peak) peak = img.data[i];
    return peak;
  };
  return {
    hard_100: run("Hard Ink", 100, 30),
    hard_50: run("Hard Ink", 50, 30),
    hard_25: run("Hard Ink", 25, 30),
    soft_100: run("Soft Brush", 100, 30),
    soft_50: run("Soft Brush", 50, 30),
    soft_25: run("Soft Brush", 25, 30),
    // DOES THE BOUND RISE WITH DAB COUNT? An amount must not -- but BE17 made
    // the underlying MARK rise with dab count, on purpose, because paint now
    // accumulates within a stroke. So the absolute value at 3 dabs and at 60
    // legitimately differs, and comparing those two numbers stopped being a
    // test of the selection and became a test of the accumulator.
    //
    // What still distinguishes an AMOUNT from a RATE is the RATIO: half the
    // selection must be half the mark, whatever the mark happens to be. A rate
    // fails that, because each dab contributes its share of a reduced value and
    // the total climbs back toward full with every overlap. The unbounded
    // counterpart is measured alongside each bounded one so the test can divide.
    soft_50_few: run("Soft Brush", 50, 3),
    soft_100_few: run("Soft Brush", 100, 3),
    soft_50_many: run("Soft Brush", 50, 60),
    soft_100_many: run("Soft Brush", 100, 60),
    hard_50_few: run("Hard Ink", 50, 3),
    hard_100_few: run("Hard Ink", 100, 3),
    hard_50_many: run("Hard Ink", 50, 60),
    hard_100_many: run("Hard Ink", 100, 60),
  };
}

// ---- 5. preset construction -------------------------------------------
//
// Airbrush's buildup is assigned AFTER the array literal. A parser that reads
// only the literal reports it Buildup-off, which is how "removing the ceiling
// fixes Airbrush" got claimed. Ask the ENGINE, not the text.
function presetConstruction() {
  const built = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    reset();
    C.applyBrushPreset(p.name);
    built[p.name] = {
      tip: S.brushPreset, size: S.brushSize,
      hardness: +S.brushHardness.toFixed(4), opacity: +S.brushOpacity.toFixed(4),
      flow: +S.brushFlow.toFixed(4), buildup: S.brushBuildup,
      smoothing: S.smoothing, spacing: S.brushDynamics.spacing,
    };
  }
  return built;
}

// ---- 6. nearest-neighbour distinctness --------------------------------
function presetDistinctness() {
  const fingerprints = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    reset();
    C.applyBrushPreset(p.name);
    C.stampWet(W / 2, H / 2, 1.0);
    const m = S.stroke.alphaMap;
    let d = 0, n = 0;
    for (let i = 0; i < m.length; i++) if (m[i]) { n++; d = (d + m[i] * (i + 1) * 2654435761) % 2147483647; }
    fingerprints[p.name] = { digest: d, count: n };
  }
  const collisions = [];
  const names = Object.keys(fingerprints);
  for (let i = 0; i < names.length; i++) {
    for (let j = i + 1; j < names.length; j++) {
      const a = fingerprints[names[i]], b = fingerprints[names[j]];
      if (a.digest === b.digest && a.count === b.count) collisions.push([names[i], names[j]]);
    }
  }
  return { fingerprints, collisions };
}

// ---- 7. the size floor, measured not assumed --------------------------
function sizeReport() {
  const extent = (size) => {
    reset();
    C.applyBrushPreset("Pixel");
    S.brushSize = size;
    C.stampWet(W / 2, H / 2, 1.0);
    const m = S.stroke.alphaMap;
    let minx = 1e9, maxx = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (m[y * W + x]) {
      if (x < minx) minx = x; if (x > maxx) maxx = x;
    }
    return { requested: size, resolvedPx: C.brushPx(), widthPx: maxx < 0 ? 0 : maxx - minx + 1 };
  };
  return [1, 2, 3, 4, 5, 6, 8, 12].map(extent);
}

// ---- 8. BE2: the Pixel sizing contract --------------------------------
//
// The whole point is document INDEPENDENCE, so this is the one measurement
// that must be taken at several document sizes. A single-document test would
// pass on the broken engine too.
function sizeContract() {
  const paintAt = (w, h, presetName, size) => {
    S.W = w; S.H = h;
    S.stroke = {
      alphaMap: new Uint8Array(w * h),
      dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
      _taperK: 1, lx: 0, ly: 0, lp: 1,
    };
    S.selection = { active: false, mask: null, rect: null, dragging: false };
    S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
    S.brushAngle = 0; S.brushRatio = 1; S.brushSpikes = 2;
    S.brushDensity = 1; S.brushFalloff = "default"; S.brushTaperIn = 0;
    _seed = 1;
    C.applyBrushPreset(presetName);
    if (size !== undefined) S.brushSize = size;
    const resolved = C.brushPx();
    C.stampWet(Math.floor(w / 2), Math.floor(h / 2), 1.0);
    const m = S.stroke.alphaMap;
    let minx = 1e9, maxx = -1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (m[y * w + x]) {
      if (x < minx) minx = x; if (x > maxx) maxx = x;
    }
    return { mode: S.brushSizeMode, resolved, widthPx: maxx < 0 ? 0 : maxx - minx + 1 };
  };

  const DOCS = [[256, 256], [512, 512], [2048, 2048], [4096, 4096], [6000, 4000]];
  const pixelAcross = {}, ordinaryAcross = {}, modeAfterPreset = {};
  for (const [w, h] of DOCS) pixelAcross[w + "x" + h] = paintAt(w, h, "Pixel");
  for (const [w, h] of DOCS) ordinaryAcross[w + "x" + h] = paintAt(w, h, "Basic Round");
  // Select Pixel, then every other preset: the mode must always come back.
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    paintAt(1024, 1024, "Pixel");
    modeAfterPreset[p.name] = paintAt(1024, 1024, p.name).mode;
  }
  const literalSizes = {};
  for (const s of [1, 2, 3, 4, 5, 8, 12]) literalSizes["size" + s] = paintAt(1024, 1024, "Pixel", s);
  return { pixelAcross, ordinaryAcross, modeAfterPreset, literalSizes };
}

// ---- BE4 section 7.4: what the merge-time bound actually costs -----------
//
// Timed rather than argued. The claim being checked is that applying selection
// here is nearly free because the loop already runs -- so the comparison is
// the SAME function with and without a mask, not against the whole-document
// eraser path that was removed years ago and would flatter it.
function selectionCost() {
  const time = (docW, docH, mask, x0, y0, x1, y1) => {
    S.W = docW; S.H = docH;
    S.stroke = S.stroke || {};
    S.stroke.alphaMap = new Uint8Array(docW * docH).fill(200);
    S.stroke._cachedImg = null;
    S.stroke.dirty = { x0, y0, x1, y1 };
    S.selection = mask
      ? { active: true, mask, rect: null, dragging: false }
      : { active: false, mask: null, rect: null, dragging: false };
    C.alphaMapToImageData("#ffffff");            // warm the cached ImageData
    const t0 = process.hrtime.bigint();
    for (let k = 0; k < 20; k++) C.alphaMapToImageData("#ffffff");
    const t1 = process.hrtime.bigint();
    return +(Number(t1 - t0) / 1e6 / 20).toFixed(3);
  };
  const binary = (n) => { const m = new Uint8Array(n); m.fill(255, 0, n >> 1); return m; };
  const fractional = (n) => new Uint8Array(n).fill(128);

  const small = [200, 200, 260, 260];
  const large = [0, 0, 1023, 1023];
  const n1k = 1024 * 1024;
  const out = {
    small_none:       time(1024, 1024, null, ...small),
    small_binary:     time(1024, 1024, binary(n1k), ...small),
    small_fractional: time(1024, 1024, fractional(n1k), ...small),
    large_none:       time(1024, 1024, null, ...large),
    large_binary:     time(1024, 1024, binary(n1k), ...large),
    large_fractional: time(1024, 1024, fractional(n1k), ...large),
  };
  // 24 MP with a BOUNDED stroke dirty region, which is the realistic case:
  // a stroke touches a fraction of a large canvas, and the loop is scoped to
  // the rectangle it touched -- not to the document.
  const n24 = 6000 * 4000;
  out.mp24_boundedRegion_none = time(6000, 4000, null, 2900, 1900, 3100, 2100);
  out.mp24_boundedRegion_fractional =
    time(6000, 4000, new Uint8Array(n24).fill(128), 2900, 1900, 3100, 2100);
  out.note = "ms per call, mean of 20. Compared against the SAME function "
           + "without a mask -- never against the removed whole-document path.";
  return out;
}

process.stdout.write(JSON.stringify({
  selectionCost: selectionCost(),
  sizeContract: sizeContract(),
  engine: { exports: Object.keys(C).length, presets: C.DEFAULT_BRUSH_PRESETS.length },
  selfCheck: selfCheck(),
  presetLeaks: presetResetReport(),
  supportMatrix: supportMatrix(),
  tipShapes: (function tipShapes() {
    // Isolates each tip's NORM, which comparing two presets cannot: Flat
    // Shader and Bold Marker differ in size, aspect and extent as well, so
    // they would look different even if both were ellipses.
    //
    // FILL RATIO is the discriminator, and it is norm-specific by
    // construction: a Chebyshev norm fills its bounding box, so the ratio is
    // ~1.0; a Euclidean one inscribes an ellipse, so it is ~pi/4 = 0.785.
    // Independent of the tip's size, aspect and extent, which is exactly what
    // a corner probe was not -- the first version sampled at 0.7r on both
    // axes and fell outside Marker's 0.35 aspect entirely, reporting every tip
    // identical.
    const probe = {};
    for (const tip of TIPS) {
      reset();
      C.applyBrushPreset("Basic Round");
      S.brushSize = 40;
      S.brushPreset = tip;
      S.brushHardness = 1.0;
      C.stampWet(W / 2, H / 2, 1.0);
      const m = S.stroke.alphaMap;
      let n = 0, minx = 1e9, maxx = -1, miny = 1e9, maxy = -1;
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (m[y * W + x]) {
        n++;
        if (x < minx) minx = x; if (x > maxx) maxx = x;
        if (y < miny) miny = y; if (y > maxy) maxy = y;
      }
      const boxArea = (maxx - minx + 1) * (maxy - miny + 1);
      probe[tip] = {
        paintedPixels: n,
        boxArea,
        fillRatio: boxArea > 0 ? +(n / boxArea).toFixed(3) : 0,
      };
    }
    return probe;
  })(),
  needsShapeMatrix: needsShapeMatrix(),
  declaredCapabilities: declaredCapabilities(),
  spikes: spikesReport(),
  selection: selectionReport(),
  construction: presetConstruction(),
  distinctness: presetDistinctness(),
  size: sizeReport(),
}, null, 1));
