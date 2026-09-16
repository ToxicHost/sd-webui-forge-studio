/**
 * BE19: the document has a surface, and it ships switched on.
 *
 * BE10 built the paper system and it reached ZERO PIXELS. Every preset was
 * bit-identical with grain declared versus grain forced to zero, because three
 * gates ship neutral -- a Surface must be chosen, Depth must move off zero, and
 * the preset must declare a Tooth -- and the controls live inside a panel that
 * ships `display:none`.
 *
 * The properties BE10 built and nobody could check, because nobody could see
 * the grain at all, are checkable now and are what most of this file measures:
 * the surface is anchored to DOCUMENT coordinates, so it does not swim when the
 * view moves, and it is not re-rolled per dab, because a surface is a surface
 * and not jitter.
 *
 *   node be19_measure.js <path to canvas-core.js>
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
const N = 384;

let _seed = 1;
function reseed() { _seed = 1; }
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

function setUp(preset, opts) {
  const o = opts || {};
  reseed();
  C.stopAirbrush();
  S.W = N; S.H = N; S.tool = "brush";
  S.layers = [{ id: "t", visible: true, opacity: 1, blendMode: "source-over",
                canvas: { width: N, height: N }, ctx: noopCtx }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(N * N), accum: null, _accumFor: null,
    dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    frameDirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    _taperK: 1, lx: 0, ly: 0, lp: 1, points: [],
    ctx: noopCtx, canvas: { width: N, height: N }, _cachedImg: null,
  };
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = !!o.mask;
  S.symmetry = "none"; S.pressureSensitivity = false;
  S.zoom = { scale: o.zoom || 1, ox: o.ox || 0, oy: o.oy || 0 };
  S.undoStack = []; S.redoStack = []; S.drawing = true;
  C.applyBrushPreset(preset);
  S.smoothing = 0;
  S.brushDynamics = Object.assign({}, S.brushDynamics,
    { sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0 });
  // `undefined` means "whatever the engine ships", which is the whole subject.
  S.paper = o.paper !== undefined
    ? o.paper : Object.assign({}, C.DEFAULT_PAPER);
  return S;
}

/** What reaches the layer. Grain is applied at merge, so the map cannot see it. */
function layer() {
  const img = C.alphaMapToImageData("#000000",
    { x0: 0, y0: 0, x1: N - 1, y1: N - 1 });
  const out = new Uint8Array(N * N);
  for (let i = 0; i < N * N; i++) {
    out[i] = Math.round(img.data[i * 4 + 3] * S.brushOpacity);
  }
  return out;
}

/** A straight stroke across the middle. `steps` lets the same path be split. */
function stroke(preset, opts) {
  setUp(preset, opts);
  const o = opts || {};
  const y = 192, x0 = 60, x1 = 320;
  C.beginStroke(x0, y, 1.0);
  const n = o.steps || 40;
  for (let i = 1; i <= n; i++) C.plotTo(x0 + (i / n) * (x1 - x0), y, 1.0);
  C.finishStroke(x1, y, 1.0);
  const out = layer();
  C.stopAirbrush();
  return out;
}

function compare(a, b) {
  let any = 0, diff = 0, sa = 0, sb = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] || b[i]) any += 1;
    if (Math.abs(a[i] - b[i]) > 8) diff += 1;
    sa += a[i]; sb += b[i];
  }
  return {
    fractionDiffering: any ? +(diff / any).toFixed(4) : 0,
    inkChangePercent: sa ? +(((sb - sa) / sa) * 100).toFixed(2) : 0,
    identical: diff === 0,
  };
}

const OFF = { texture: "none", scale: 1, depth: 0 };
const R = {};

R.shippedDefault = C.DEFAULT_PAPER;

// ------------------------------- 1. the grain reaches pixels as shipped
//
// THE BE10 FAILURE, AS A NUMBER. Every preset used to be bit-identical with
// grain declared versus forced to zero. This is the same comparison, with the
// paper left at whatever the engine ships rather than forced on by the fixture.
{
  const rows = {};
  for (const preset of ["Charcoal", "Pastel", "Pencil", "Sketch Light",
                        "Basic Round", "Soft Round", "Hard Ink",
                        "Pixel Perfect"]) {
    const off = stroke(preset, { paper: OFF });
    const shipped = stroke(preset, {});
    rows[preset] = Object.assign({ grain: S.brushGrain }, compare(off, shipped));
  }
  R.grainReachesPixels = rows;
}

// --------------------------- 2. a preset that declares no Tooth is untouched
//
// `paperReveal` multiplies by `S.brushGrain`, so a preset declaring 0.0 is
// unaffected at any depth. An ink pen does not find the tooth. Measured across
// the whole slider rather than at the default, because "untouched" is a
// property of the preset and not of the setting.
{
  const rows = {};
  for (const preset of ["Hard Ink", "Pixel Perfect"]) {
    const off = stroke(preset, { paper: OFF });
    const at = {};
    for (const depth of [0.10, 0.30, 0.60, 1.0]) {
      const on = stroke(preset, { paper: { texture: "rough", scale: 1, depth } });
      at["depth" + Math.round(depth * 100)] = compare(off, on).identical;
    }
    rows[preset] = { grain: S.brushGrain, identicalAtEveryDepth: at };
  }
  R.noToothMeansNoGrain = rows;
}

// ------------------------------------------------ 3. a mask has no surface
//
// `stampWet` passes `S.editingMask ? null : paperReveal()`. A mask is a
// decision boundary, and `exportMask` binarises it anyway.
{
  const off = stroke("Charcoal", { paper: OFF, mask: true });
  const on = stroke("Charcoal", { mask: true });
  R.aMaskHasNoSurface = Object.assign(compare(off, on), {
    paintedPixels: (() => { let n = 0; for (const v of on) if (v) n += 1; return n; })(),
  });
}

// ------------------------------- 4. the surface is anchored to the document
//
// It must not swim when the VIEW moves. Same document coordinates, same
// stroke, three different zooms and pans: the grain has to land identically,
// because it is a property of the paper rather than of the window onto it.
{
  const base = stroke("Charcoal", {});
  const rows = {};
  for (const [label, opts] of [
    ["zoom4", { zoom: 4 }],
    ["zoomQuarter", { zoom: 0.25 }],
    ["panned", { zoom: 1, ox: 137, oy: -89 }],
  ]) {
    rows[label] = compare(base, stroke("Charcoal", opts));
  }
  R.theSurfaceIsAnchoredToTheDocument = rows;
}

// ---------------------------- 5. the surface is not re-rolled per dab
//
// A surface is a surface, not jitter. The SAME path reported at three sample
// densities lays a different number of dabs over the same pixels, so a grain
// re-rolled per dab would differ between them. Distinct from the event-rate
// contract elsewhere: this is about the TEXTURE being stable, not the
// placement.
{
  // THE CONTROL IS THE SAME SWEEP WITH THE PAPER OFF. Dab placement itself has
  // a small residue across sample densities -- measured elsewhere at about 4%
  // of pixels at the antialiased rim -- so a bare "the marks differ by 3%"
  // cannot tell an unstable GRAIN from ordinary placement noise. What the
  // surface owes is that turning it ON does not make that residue worse.
  const rows = {};
  const withPaper = stroke("Charcoal", { steps: 40 });
  const withoutPaper = stroke("Charcoal", { steps: 40, paper: OFF });
  for (const steps of [10, 80, 160]) {
    const on = compare(withPaper, stroke("Charcoal", { steps }));
    const off = compare(withoutPaper,
      stroke("Charcoal", { steps, paper: OFF }));
    rows["steps" + steps] = {
      withPaper: on.fractionDiffering,
      placementOnly: off.fractionDiffering,
      grainContribution: +(on.fractionDiffering - off.fractionDiffering).toFixed(4),
    };
  }
  R.theSurfaceIsNotReRolledPerDab = rows;
}

// -------------------------- 6. a stroke drawn in two halves is continuous
//
// The grain must not restart at the seam. Two strokes covering the same span,
// against one stroke covering it: where they overlap the texture must agree.
{
  setUp("Charcoal", {});
  C.beginStroke(60, 192, 1.0);
  for (let i = 1; i <= 20; i++) C.plotTo(60 + (i / 20) * 130, 192, 1.0);
  C.finishStroke(190, 192, 1.0);
  const firstHalf = layer();
  C.stopAirbrush();

  const whole = stroke("Charcoal", {});
  // Compare only the span the half-stroke actually covered.
  let differing = 0, counted = 0;
  for (let x = 70; x <= 180; x++) {
    for (let y = 172; y <= 212; y++) {
      const i = y * N + x;
      if (!firstHalf[i] && !whole[i]) continue;
      counted += 1;
      if (Math.abs(firstHalf[i] - whole[i]) > 8) differing += 1;
    }
  }
  R.aSplitStrokeKeepsOneSurface = {
    comparedPixels: counted,
    differing,
    fraction: counted ? +(differing / counted).toFixed(4) : 0,
  };
}

// ------------------------------------- 7. the depth sweep the default came from
//
// RESEEDED, WHICH THE FIRST VERSION WAS NOT. Charcoal ships Density 0.95, so
// its stipple consumes the random stream; a sweep that did not reset the seed
// between the grain-off and grain-on runs compared two different stipples and
// reported 72% of pixels changed at depth 0.10, nearly all of it RNG
// divergence rather than grain. The default was chosen off that number.
{
  const rows = {};
  for (const depth of [0.10, 0.20, 0.30, 0.45, 0.60]) {
    const at = {};
    for (const preset of ["Charcoal", "Pastel", "Pencil", "Sketch Light",
                          "Basic Round"]) {
      const off = stroke(preset, { paper: OFF });
      const on = stroke(preset, { paper: { texture: "fine", scale: 1, depth } });
      const c = compare(off, on);
      at[preset] = { visible: c.fractionDiffering, ink: c.inkChangePercent };
    }
    rows["depth" + Math.round(depth * 100)] = at;
  }
  R.depthSweep = rows;
}

realLog(JSON.stringify(R, null, 2));
