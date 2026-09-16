/**
 * BE20: which brush controls actually change the mark, on which preset.
 *
 * THE CENSUS IS THE INDEPENDENT OBSERVATION. `TIP_CAPABILITIES` is what the
 * engine DECLARES; this file is what the engine DOES. A truthfulness guard
 * generated from the declaration it is meant to verify would pass on any
 * engine, including one where every control is dead, so the two sides have to
 * come from different places.
 *
 * The audit that opened this package reported four controls inert on twelve of
 * sixteen presets. That is the claim being checked, not assumed.
 *
 * WHY A CONTROL IS DEAD ON A CIRCULAR TIP, so the result is not mistaken for a
 * wiring fault:
 *
 *   `tipFrame` computes `rotates = !!ang && !(circular && !folds)`. On a tip
 *   where rx === ry the frame transform is the identity, so Angle, Follow
 *   stroke and Rotation Jitter have nothing to turn.
 *
 *   `_applySpikeRotation` folds the sample angle into a wedge, and a fold is a
 *   rotation about the origin. A rotation preserves radius, so on a circular
 *   tip the normalised distance is unchanged and the spike count cannot alter
 *   one pixel.
 *
 * Neither is a bug to fix in the renderer. They are honest geometry, and the
 * defect is a UI that offers the control anyway.
 *
 *   node be20_measure.js <path to canvas-core.js>
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
const N = 320;

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

/** Every preset the picker offers, read off the engine rather than listed. */
const PRESETS = C.DEFAULT_BRUSH_PRESETS.map(p => p.name);

function setUp(preset) {
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
  S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  // THE PAPER IS PINNED OFF. This census is about brush controls; a surface
  // would add its own texture to every comparison and is not the subject.
  S.paper = { texture: "none", scale: 1, depth: 0 };
  S.undoStack = []; S.redoStack = []; S.drawing = true;
  C.applyBrushPreset(preset);
  S.smoothing = 0;
  return S;
}

/** A curved stroke, so a control that only shows on a turn still shows. */
function stroke(preset, apply) {
  setUp(preset);
  if (apply) apply();
  C.beginStroke(60, 140, 1.0);
  for (let i = 1; i <= 60; i++) {
    const t = i / 60;
    C.plotTo(60 + t * 200, 140 + Math.sin(t * Math.PI) * 70, 1.0);
  }
  C.finishStroke(260, 140, 1.0);
  const img = C.alphaMapToImageData("#000000",
    { x0: 0, y0: 0, x1: N - 1, y1: N - 1 });
  const out = new Uint8Array(N * N);
  for (let i = 0; i < N * N; i++) {
    out[i] = Math.round(img.data[i * 4 + 3] * S.brushOpacity);
  }
  C.stopAirbrush();
  return out;
}

function changed(a, b) {
  let any = 0, diff = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] || b[i]) any += 1;
    if (Math.abs(a[i] - b[i]) > 8) diff += 1;
  }
  return any ? +(diff / any).toFixed(4) : 0;
}

const dyn = (patch) => {
  S.brushDynamics = Object.assign({}, S.brushDynamics, patch);
};

/**
 * The controls a painter can see, and the two settings each is swept between.
 *
 * Each pair is chosen to be the LARGEST honest move the control allows, so a
 * control reported dead is dead at its own extremes rather than merely subtle.
 */
const CONTROLS = {
  angle: [() => { S.brushAngle = 0; dyn({ followStroke: false }); },
          () => { S.brushAngle = 60; dyn({ followStroke: false }); }],
  spikes: [() => { S.brushSpikes = 2; }, () => { S.brushSpikes = 9; }],
  followStroke: [() => { dyn({ followStroke: false }); },
                 () => { dyn({ followStroke: true }); }],
  rotationJitter: [() => { dyn({ rotationJitter: 0 }); },
                   () => { dyn({ rotationJitter: 0.5 }); }],
  ratio: [() => { S.brushRatio = 1.0; }, () => { S.brushRatio = 0.35; }],
  density: [() => { S.brushDensity = 1.0; }, () => { S.brushDensity = 0.4; }],
};

const R = { presets: PRESETS.length, controls: Object.keys(CONTROLS) };

// ------------------------------------------- 1. the census, preset by control
{
  const rows = {};
  for (const preset of PRESETS) {
    const tip = (() => { setUp(preset); return S.brushPreset; })();
    const declared = (C.TIP_CAPABILITIES || {})[tip] || {};
    const row = { tip, declared: {}, observed: {}, live: {} };
    for (const [name, [a, b]] of Object.entries(CONTROLS)) {
      const A = stroke(preset, a);
      const B = stroke(preset, b);
      const f = changed(A, B);
      row.observed[name] = f;
      row.live[name] = f > 0.01;
      row.declared[name] = declared[name] !== undefined ? declared[name] : null;
    }
    rows[preset] = row;
  }
  R.census = rows;
}

// -------------------------- 2. where the declaration and the renderer disagree
//
// TWO DIRECTIONS, because only one of them is a lie. A control DECLARED
// supported that changes nothing is the defect. A control declared
// conditional that turns out live is the condition being met, which is
// correct and is recorded rather than flagged.
{
  const lying = [];
  const conditionalAndLive = [];
  for (const [preset, row] of Object.entries(R.census)) {
    for (const name of Object.keys(CONTROLS)) {
      const declared = row.declared[name];
      const live = row.live[name];
      if (declared === true && !live) {
        lying.push({ preset, control: name, observed: row.observed[name] });
      }
      if (typeof declared === "string" && live) {
        conditionalAndLive.push({ preset, control: name });
      }
    }
  }
  R.disagreements = { lying, conditionalAndLive };
}

// ------------------------- 3. the four the audit named, on the default brush
{
  const row = R.census["Basic Round"];
  R.theDefaultBrush = {
    tip: row.tip,
    angle: row.observed.angle,
    spikes: row.observed.spikes,
    followStroke: row.observed.followStroke,
    rotationJitter: row.observed.rotationJitter,
    deadControls: ["angle", "spikes", "followStroke", "rotationJitter"]
      .filter(k => !row.live[k]),
  };
}

// ------- 4. and that they come alive when the tip stops being circular
//
// Guards the census in the other direction: a control reported dead everywhere
// might simply be unreachable by this fixture. Ratio 0.35 makes a round tip
// elliptical, and an elliptical tip has an orientation to turn.
{
  const rows = {};
  for (const name of ["angle", "spikes"]) {
    const [a, b] = CONTROLS[name];
    const A = stroke("Basic Round", () => { a(); S.brushRatio = 0.35; });
    const B = stroke("Basic Round", () => { b(); S.brushRatio = 0.35; });
    rows[name] = { observedAtRatio35: changed(A, B) };
  }
  R.aliveOnceTheTipIsNotCircular = rows;
}

realLog(JSON.stringify(R, null, 2));
