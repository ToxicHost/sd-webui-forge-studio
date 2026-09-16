/**
 * BE18: a hard tip keeps one pixel of antialiasing.
 *
 * At `hardness === 1.0` the falloff's inner radius equalled the outer one, so
 * every pixel inside the tip returned exactly 1 and every pixel outside
 * returned 0. Coverage was binary and the silhouette was a staircase. Measured
 * against an 8x supersampled render: edge RMS 0.29-0.42px on Hard Ink, Fine
 * Liner, Marker and Calligraphy, against 0.301px for PIXEL PERFECT -- the
 * preset that declares itself aliased. On their silhouettes those four were
 * indistinguishable from the pixel brush, and so was the default state before
 * any preset is chosen.
 *
 *   node be18_measure.js <path to canvas-core.js> [path to a second engine]
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
const vm = require("vm");

const N = 512;

const noopCtx = {
  clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
  fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
};

function loadEngine(path) {
  const g = {};
  g.window = g;
  g.console = { log() {}, warn() {}, error() {} };
  g.ImageData = globalThis.ImageData;
  let seed = 1;
  g.Math = Object.create(Math);
  g.Math.random = function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  vm.createContext(g);
  vm.runInContext(fs.readFileSync(path, "utf8"), g, { filename: path });
  return g.window.StudioCore;
}

function setUp(C, preset, opts) {
  const S = C.state;
  const o = opts || {};
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
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  S.paper = { texture: "none", scale: 1, depth: 0 };
  S.undoStack = []; S.redoStack = []; S.drawing = true;
  C.applyBrushPreset(preset);
  S.smoothing = 0;
  if (o.size !== undefined) S.brushSize = o.size;
  if (o.hardness !== undefined) S.brushHardness = o.hardness;
  return S;
}

/** A straight stroke, and what its coverage looks like. */
function stroke(C, preset, opts) {
  const S = setUp(C, preset, opts);
  C.beginStroke(100, 256, 1.0);
  for (let i = 1; i <= 40; i++) C.plotTo(100 + i * 7, 256, 1.0);
  if (C.finishStroke) C.finishStroke(380, 256, 1.0);
  C.stopAirbrush();
  const m = S.stroke.alphaMap;
  let ink = 0, painted = 0, partial = 0, solid = 0;
  for (let i = 0; i < m.length; i++) {
    if (!m[i]) continue;
    painted += 1; ink += m[i];
    if (m[i] < 250) partial += 1; else solid += 1;
  }
  return { radius: +(C.brushPx() / 2).toFixed(2), ink, painted, partial, solid };
}

const NOW = loadEngine(process.argv[2]);
const WAS = process.argv[3] ? loadEngine(process.argv[3]) : null;

const R = {};

// ------------------------------- 1. a hard tip has an antialiased rim at all
//
// PARTIAL-COVERAGE PIXELS ARE THE WHOLE CLAIM. A staircase silhouette has
// exactly none: every pixel is 0 or full. One is enough to prove a ramp
// exists; the count is reported so the size sweep below can be read.
{
  const rows = {};
  for (const preset of ["Hard Ink", "Fine Liner", "Marker", "Calligraphy"]) {
    const s = stroke(NOW, preset, {});
    rows[preset] = {
      radius: s.radius, partialPixels: s.partial, solidPixels: s.solid,
      partialFraction: s.painted ? +(s.partial / s.painted).toFixed(4) : 0,
    };
  }
  R.hardTipsHaveARim = rows;
}

// ------------------------------------- 2. the rim does not eat a small tip
//
// THE RISK THE FLOOR INTRODUCES. A flat one-pixel band is right for an
// ordinary tip and ruinous for a tiny one: at radius 1.5 it leaves an inner
// core of half a pixel, so the dab is MOSTLY ramp. Measured before the
// fraction cap, Hard Ink at Size 3 lost 33.8% of its ink -- the same
// footprint, painted a third paler -- while every other size moved by under
// half a percent.
{
  const rows = {};
  for (const size of [1, 2, 3, 4, 6, 8, 12, 20, 40]) {
    const now = stroke(NOW, "Hard Ink", { size });
    const row = {
      radius: now.radius, ink: now.ink, partialPixels: now.partial,
      // ABSOLUTE, so the guard does not depend on a second engine being
      // supplied. The delta below is better evidence and is only available
      // when one is -- and a guard written on the delta alone asserted over an
      // EMPTY SET when the module ran the driver with one argument, which is
      // how the fraction-cap mutation walked straight through it.
      partialFraction: now.painted ? +(now.partial / now.painted).toFixed(4) : 0,
      solidFraction: now.painted ? +(now.solid / now.painted).toFixed(4) : 0,
    };
    if (WAS) {
      const was = stroke(WAS, "Hard Ink", { size });
      row.inkBefore = was.ink;
      row.inkChangePercent = was.ink
        ? +(((now.ink - was.ink) / was.ink) * 100).toFixed(2) : 0;
    }
    rows["size" + size] = row;
  }
  R.theRimDoesNotEatASmallTip = rows;
}

// ------------------------------------------- 3. a mask edge stays a decision
//
// `stampWet` forces round/hardness-1 in mask mode, and `exportMask` binarises
// at alpha > 0 -- so a rim would expand every inpaint mask by a pixel in every
// direction, changing what the model is asked to repaint. The softness would
// be thrown away by the binarisation anyway.
{
  const rows = {};
  for (const preset of ["Hard Ink", "Marker"]) {
    const s = stroke(NOW, preset, { mask: true });
    rows[preset] = { partialPixels: s.partial, solidPixels: s.solid };
  }
  R.aMaskEdgeStaysBinary = rows;
}

// ------------------------------- 4. a brush already softer is untouched
{
  const rows = {};
  for (const preset of ["Basic Round", "Soft Round", "Pixel Perfect"]) {
    const now = stroke(NOW, preset, {});
    const row = { radius: now.radius, ink: now.ink, partialPixels: now.partial };
    if (WAS) {
      const was = stroke(WAS, preset, {});
      row.inkChangePercent = was.ink
        ? +(((now.ink - was.ink) / was.ink) * 100).toFixed(2) : 0;
    }
    rows[preset] = row;
  }
  R.alreadySoftIsUntouched = rows;
}

console.log(JSON.stringify(R, null, 2));
