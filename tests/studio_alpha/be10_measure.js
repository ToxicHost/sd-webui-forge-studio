/**
 * BE10: does the document's paper actually reach the pixels, and does it hold
 * the four properties that separate a paper from a noise filter?
 *
 * MEASURED AT THE MERGE, NOT AT THE ALPHA MAP. Every driver before this one
 * read `S.stroke.alphaMap` directly, because everything before this one
 * changed coverage. Paper does not touch coverage -- deliberately, that is the
 * whole design -- so a driver that read the alpha map would report that BE10
 * does nothing. What it changes is the OUTPUT ALPHA that
 * `alphaMapToImageData` writes, which is what the layer receives and what the
 * live preview shows.
 *
 *   node tests/studio_alpha/be10_measure.js <path to canvas-core.js>
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
const source = fs.readFileSync(process.argv[2], "utf8");
const realLog = console.log;
console.log = () => {};
(0, eval)(source);
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
  S.W = W; S.H = H;
  S.layers = [{
    id: "t", name: "t", visible: true, opacity: 1, blendMode: "source-over",
    canvas: { width: W, height: H }, ctx: noopCtx,
  }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(W * H),
    dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    _taperK: 1, lx: 0, ly: 0, lp: 1,
    ctx: noopCtx, canvas: { width: W, height: H }, _cachedImg: null,
  };
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
  S.brushAngle = 0; S.brushRatio = 1; S.brushSpikes = 2;
  S.brushDensity = 1; S.brushFalloff = "default"; S.brushTaperIn = 0;
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  S.paper = { texture: "none", scale: 1, depth: 0 };
  C.applyBrushPreset(preset || "Basic Round");
  S.smoothing = 0;   // the stabiliser is BE9's subject, not this one
}

/** A horizontal stroke, then the OUTPUT alpha the layer would receive. */
function paint(opts) {
  const o = opts || {};
  reset(o.preset);
  if (o.paper) S.paper = Object.assign({}, S.paper, o.paper);
  if (o.grain != null) S.brushGrain = o.grain;
  if (o.mask) S.editingMask = true;
  if (o.size) S.brushSize = o.size;
  const y = o.y != null ? o.y : 256;
  const x0 = o.x0 != null ? o.x0 : 80;
  C.beginStroke(x0, y, 1.0);
  for (let i = 1; i <= (o.steps || 24); i++) C.plotTo(x0 + i * 14, y, 1.0);
  // COVERAGE IS CAPTURED TOO, and the first version of this driver did not do
  // that. Two measurements below compare the paper between strokes, and output
  // alpha is `coverage * reveal` -- so comparing outputs compares the coverage
  // as well, which differs between any two strokes that start in different
  // places. The first run reported 92% agreement on a paper that is in fact
  // identical, and a -1.0 sign correlation as +0.11. The reveal has to be
  // divided back out to be looked at directly.
  const cov = Uint8Array.from(S.stroke.alphaMap);
  const img = C.alphaMapToImageData("#ffffff");
  // Copy: the engine reuses one cached ImageData across calls.
  return { data: Uint8Array.from(img.data), cov: cov,
           dirty: Object.assign({}, S.stroke.dirty) };
}

function describe(out) {
  const d = out.data;
  let painted = 0, sum = 0, peak = 0, digest = 0;
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3];
    if (a > 0) { painted++; sum += a; if (a > peak) peak = a; }
    digest = (digest * 31 + a * ((i >> 2) % 977)) >>> 0;
  }
  return { painted, mean: painted ? +(sum / painted).toFixed(2) : 0, peak, digest };
}

/** Standard deviation of the painted alphas -- "is there any texture at all". */
function roughness(out) {
  const d = out.data;
  const vals = [];
  for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0) vals.push(d[i + 3]);
  if (!vals.length) return 0;
  const m = vals.reduce((a, b) => a + b, 0) / vals.length;
  return +Math.sqrt(vals.reduce((a, b) => a + (b - m) ** 2, 0) / vals.length).toFixed(2);
}

const R = {};

// ---------------------------------------------------------------- 1. it acts
//
// The first question, and the one the owner's complaint is really about: does
// turning it on change the mark at all?
{
  const off = paint({ paper: { texture: "none" }, grain: 1 });
  const on = paint({ paper: { texture: "rough", depth: 1 }, grain: 1 });
  R.paperChangesTheMark = {
    off: describe(off), on: describe(on),
    offRoughness: roughness(off), onRoughness: roughness(on),
    identical: describe(off).digest === describe(on).digest,
  };
}

// ------------------------------------------------------- 2. neutral is neutral
//
// Three ways to mean "no paper", and all three must be BYTE-IDENTICAL to the
// unpapered mark. A control that is nearly neutral at zero is a control the
// owner cannot turn off.
{
  const base = describe(paint({ paper: { texture: "none" }, grain: 1 }));
  R.neutralIsExact = {
    base: base.digest,
    depthZero: describe(paint({ paper: { texture: "rough", depth: 0 }, grain: 1 })).digest === base.digest,
    grainZero: describe(paint({ paper: { texture: "rough", depth: 1 }, grain: 0 })).digest === base.digest,
    textureNone: describe(paint({ paper: { texture: "none", depth: 1 }, grain: 1 })).digest === base.digest,
    unknownTexture: describe(paint({ paper: { texture: "nosuchpaper", depth: 1 }, grain: 1 })).digest === base.digest,
  };
}

// ------------------------------------------------- 3. the paper is the canvas
//
// Two strokes crossing the same place must find the same fibres. Painted at
// two different y values, the SAME stroke must differ; painted twice at the
// same y it must be identical; and a stroke drawn 512 pixels lower must repeat
// exactly, which is what "tiled, canvas-locked" means.
{
  const a = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, y: 200 });
  const b = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, y: 200 });
  const c = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, y: 260 });
  // Same fibres, reached from a different starting x: the overlap region must
  // agree pixel for pixel even though the strokes are not the same stroke.
  const long = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, x0: 80, steps: 24, y: 320 });
  const short = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, x0: 220, steps: 10, y: 320 });
  // Compared as REVEAL, not as output. See the note in `paint`.
  let overlap = 0, agree = 0, worst = 0;
  for (let i = 0; i < long.data.length; i += 4) {
    const k = i >> 2;
    // Only pixels both strokes covered SOLIDLY: at a partially covered pixel
    // the quantisation of `coverage * reveal` to 8 bits is coarse enough to
    // differ by one for two different coverages under the same reveal.
    if (long.cov[k] < 250 || short.cov[k] < 250) continue;
    overlap++;
    const a = long.data[i + 3] / long.cov[k];
    const b = short.data[i + 3] / short.cov[k];
    const d = Math.abs(a - b);
    if (d > worst) worst = d;
    if (d < 0.004) agree++;
  }
  R.paperIsLockedToTheCanvas = {
    repeatable: describe(a).digest === describe(b).digest,
    positionMatters: describe(a).digest !== describe(c).digest,
    overlapPixels: overlap,
    overlapAgreement: overlap ? +(agree / overlap).toFixed(4) : 0,
    worstRevealDifference: +worst.toFixed(4),
  };
}

// -------------------------------------------------------- 4. zoom moves nothing
//
// The height map is indexed in document space, so zoom must be irrelevant to
// the output. Asserted because a screen-space texture is the easy mistake and
// it looks fine until the owner zooms.
{
  const marks = {};
  for (const scale of [0.25, 1, 4]) {
    reset();
    S.zoom = { scale: scale, ox: 0, oy: 0 };
    S.paper = { texture: "canvas", scale: 1, depth: 1 };
    S.brushGrain = 1;
    S.smoothing = 0;
    C.beginStroke(80, 256, 1.0);
    for (let i = 1; i <= 24; i++) C.plotTo(80 + i * 14, 256, 1.0);
    const img = C.alphaMapToImageData("#ffffff");
    marks["zoom" + scale] = describe({ data: Uint8Array.from(img.data) }).digest;
  }
  R.zoomDoesNotMoveTheGrain = {
    at: marks,
    identical: new Set(Object.values(marks)).size === 1,
  };
}

// ------------------------------------------- 5. overlap cannot erase the grain
//
// THE REASON PAPER IS APPLIED AT MERGE. A per-dab grain washes out under
// overlap: each pass takes its share of the reduction and the total climbs
// back toward full. Scrubbing the same span 1, 4 and 16 times must not reduce
// the texture.
{
  const rows = {};
  for (const passes of [1, 4, 16]) {
    reset();
    S.paper = { texture: "rough", scale: 1, depth: 1 };
    S.brushGrain = 1;
    S.smoothing = 0;
    C.beginStroke(80, 256, 1.0);
    for (let p = 0; p < passes; p++) {
      for (let i = 1; i <= 20; i++) C.plotTo(80 + i * 14, 256, 1.0);
      for (let i = 19; i >= 0; i--) C.plotTo(80 + i * 14, 256, 1.0);
    }
    const img = C.alphaMapToImageData("#ffffff");
    const out = { data: Uint8Array.from(img.data) };
    rows["passes" + passes] = { rough: roughness(out), mean: describe(out).mean };
  }
  const rs = Object.values(rows).map(r => r.rough);
  R.overlapDoesNotEraseTheGrain = {
    at: rows,
    spread: +(Math.max(...rs) - Math.min(...rs)).toFixed(2),
  };
}

// ------------------------------------------- 6. the sign reverses the reveal
{
  const pos = paint({ paper: { texture: "rough", depth: 1 }, grain: 1 });
  const neg = paint({ paper: { texture: "rough", depth: -1 }, grain: 1 });
  // Where one is high the other must be low. Correlation over painted pixels.
  // Correlated as REVEAL, not as output alpha. Both marks share one coverage
  // field, and coverage dominates the variance -- the first run of this driver
  // reported +0.11 for two signs that are exact opposites, because the stroke
  // edges are dim in both.
  let n = 0, sx = 0, sy = 0, sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < pos.data.length; i += 4) {
    const k = i >> 2;
    if (pos.cov[k] < 250) continue;
    const x = pos.data[i + 3] / pos.cov[k], y = neg.data[i + 3] / neg.cov[k];
    n++; sx += x; sy += y; sxy += x * y; sxx += x * x; syy += y * y;
  }
  const num = n * sxy - sx * sy;
  const den = Math.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy));
  R.theSignReversesTheReveal = {
    revealCorrelation: den > 0 ? +(num / den).toFixed(4) : 0,
    solidPixels: n,
    positiveMean: describe(pos).mean, negativeMean: describe(neg).mean,
    differ: describe(pos).digest !== describe(neg).digest,
  };
}

// ------------------------------------- 7. presets reveal the same paper differently
{
  const marks = {};
  for (const g of [0, 0.25, 0.6, 1]) {
    const out = paint({ paper: { texture: "fine", depth: 1 }, grain: g });
    marks["grain" + g] = { rough: roughness(out), mean: describe(out).mean };
  }
  R.presetStrengthMatters = {
    at: marks,
    monotonic: marks.grain0.mean > marks["grain0.25"].mean
      && marks["grain0.25"].mean > marks["grain0.6"].mean
      && marks["grain0.6"].mean > marks.grain1.mean,
  };
}

// --------------------------------------------------- 8. the papers are distinct
{
  const marks = {};
  for (const name of Object.keys(C.PAPER_LIBRARY)) {
    const out = paint({ paper: { texture: name, depth: 1 }, grain: 1 });
    marks[name] = { digest: describe(out).digest, rough: roughness(out), mean: describe(out).mean };
  }
  R.papersAreDistinct = {
    at: marks,
    distinct: new Set(Object.values(marks).map(m => m.digest)).size,
    count: Object.keys(marks).length,
  };
}

// -------------------------------------------------------- 9. scale is a control
{
  const marks = {};
  for (const scale of [0.5, 1, 4]) {
    const out = paint({ paper: { texture: "rough", scale: scale, depth: 1 }, grain: 1 });
    marks["scale" + scale] = describe(out).digest;
  }
  R.scaleIsAControl = {
    at: marks,
    distinct: new Set(Object.values(marks)).size,
  };
}

// ------------------------------------------------------ 10. not in mask mode
{
  const plain = paint({ paper: { texture: "rough", depth: 1 }, grain: 1, mask: true });
  const none = paint({ paper: { texture: "none" }, grain: 1, mask: true });
  R.masksAreNotPapered = {
    identical: describe(plain).digest === describe(none).digest,
  };
}

// ------------------------------------------------- 11. the tile itself is sane
{
  const t = C.paperTile("rough", 1);
  const again = C.paperTile("rough", 1);
  let lo = 255, hi = 0, sum = 0;
  for (let i = 0; i < t.length; i++) { sum += t[i]; if (t[i] < lo) lo = t[i]; if (t[i] > hi) hi = t[i]; }
  // The wrap seam must be no worse than an ordinary interior column, or the
  // tile shows as a ruled line every 512 document pixels.
  let seam = 0, interior = 0;
  for (let y = 0; y < C.PAPER_TILE; y++) {
    seam += Math.abs(t[y * C.PAPER_TILE + C.PAPER_TILE - 1] - t[y * C.PAPER_TILE]);
    interior += Math.abs(t[y * C.PAPER_TILE + 255] - t[y * C.PAPER_TILE + 256]);
  }
  R.theTileIsSane = {
    size: t.length, min: lo, max: hi, mean: +(sum / t.length).toFixed(1),
    cached: t === again,
    colSeam: +(seam / C.PAPER_TILE).toFixed(2),
    colInterior: +(interior / C.PAPER_TILE).toFixed(2),
  };
}

// ---------------------- 11b. what tooth each shipped preset declares
//
// READ FROM THE TABLE rather than matched as a source string. The guards that
// used this asserted `'name: "Pencil", grain: 0.85'` -- one field order on one
// line -- and BE14 reformatted the table onto several lines per preset. A
// guard that reads a value survives a reformat; one that matches a line does
// not, and the claim was always about the value.
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) rows[p.name] = p.grain;
  R.shippedTooth = rows;
}

// ------------------------------------------------- 12. the curve, exactly
//
// ADDED AFTER A MUTATION ESCAPED. `lut[i] = h + (1-h)*(1-strength)*0.995` --
// the same curve, 0.5% short -- passed every guard here. Depth 0 was safe
// because `paperReveal` returns null before it builds a table at all, and at
// every other strength the error was below the 8-bit quantisation of
// `coverage * reveal` for most coverages.
//
// So the table is read directly. The curve has two exact endpoints and they
// are the contract: a peak is untouched at ANY strength, and a pit is reduced
// to exactly `1 - strength`. An implementation that is nearly right fails
// both.
{
  const rows = {};
  for (const strength of [0.25, 0.5, 1]) {
    reset();
    S.paper = { texture: "rough", scale: 1, depth: strength };
    S.brushGrain = 1;
    const rev = C.paperReveal();
    rows["strength" + strength] = {
      peak: rev.lut[255],
      pit: rev.lut[0],
      expectedPit: 1 - strength,
      monotonic: rev.lut.every((v, i) => i === 0 || v >= rev.lut[i - 1]),
    };
  }
  R.theCurveIsExact = rows;
}

// -------------------------------- 13. presets do not inherit the last one's tooth
{
  reset();
  C.applyBrushPreset("Pencil");
  const afterPencil = S.brushGrain;
  C.applyBrushPreset("Hard Ink");
  const afterInk = S.brushGrain;
  C.applyBrushPreset("Pencil");
  R.presetsResetTheirGrain = {
    afterPencil, afterInk, backToPencil: S.brushGrain,
    resets: afterInk !== afterPencil || afterPencil === 0,
  };
}

console.log(JSON.stringify(R, null, 2));
