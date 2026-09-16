/**
 * BE12: does holding still deposit paint, and does the RATE survive a browser
 * that schedules badly?
 *
 * A FAKE CLOCK AND AN INJECTED SCHEDULER, because the acceptance asks for
 * deterministic tests and a timer measured against the real clock is a timer
 * measured against the machine's mood. `setAirbrushClock` and the scheduler
 * argument to `startAirbrush` are the only two seams the browser owns, and
 * everything the browser owns is what a test cannot otherwise reach.
 *
 * THE MEASUREMENT THAT MATTERS is the third probe. One callback carrying 200ms
 * and twenty callbacks carrying 10ms must deposit the same paint. A timer that
 * stamps once per callback cannot make that true, and it is the same
 * event-rate defect BE3 removed from spacing and BE9 removed from smoothing,
 * arriving a third time wearing a timer.
 *
 *   node tests/studio_alpha/be12_measure.js <path to canvas-core.js>
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

// ---- the fake clock ----------------------------------------------------
let _t = 0;
C.setAirbrushClock(() => _t);

const noopCtx = {
  clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
  fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
};

function reset(preset) {
  _seed = 1;
  _t = 0;
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
  S.brushAngle = 0; S.brushRatio = 1; S.brushSpikes = 2;
  S.brushDensity = 1; S.brushFalloff = "default"; S.brushTaperIn = 0;
  S.zoom = { scale: 1, ox: 0, oy: 0 };
  S.paper = { texture: "none", scale: 1, depth: 0 };
  S.undoStack = []; S.redoStack = [];
  C.applyBrushPreset(preset || "Airbrush");
  S.smoothing = 0;
  S.brushCurves = [];
  S.brushDynamics = Object.assign({}, S.brushDynamics, {
    sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0,
  });
}

/** Begin a stroke without letting the real `setInterval` anywhere near it. */
function begin(x, y) {
  // A QUEUED CALLBACK, which is the thing being tested.
  //
  // The first version's canceller set `tick = null`, so every stale-callback
  // probe was testing THE HARNESS: `stopAirbrush` cancelled the handle, the
  // driver had nothing left to call, and the probe reported zero for a token
  // check that had been mutated away. Two mutations escaped on that.
  //
  // `clearInterval` cannot un-queue a callback that has already been
  // scheduled, so neither does this. The function stays callable after
  // cancellation and the ENGINE has to be what refuses to deposit.
  let queued = null;
  const scheduler = fn => { queued = fn; return () => {}; };
  // THE POINTER HANDLER SETS THIS, NOT `beginStroke`, and the first run of
  // this driver forgot it: every tick exited at the `!S.drawing` guard and
  // reported zero dabs for a timer that was working. The guard is right --
  // a timer must not deposit when nothing is being drawn -- so the driver
  // reproduces what canvas-ui does rather than the engine relaxing it.
  S.drawing = true;
  // `beginStroke` calls `startAirbrush()` with no scheduler, which would take
  // the real timer. Stop that one and start our own -- the same call the
  // engine makes, with the seam the acceptance asks for.
  C.beginStroke(x, y, 1.0);
  C.stopAirbrush();
  const token = C.startAirbrush(scheduler);
  return { token, tick: () => (queued ? queued() : 0) };
}

function describe(map) {
  let painted = 0, sum = 0, peak = 0;
  for (let i = 0; i < map.length; i++) {
    const a = map[i];
    if (a > 0) { painted++; sum += a; if (a > peak) peak = a; }
  }
  return { painted, peak, total: sum,
           mean: painted ? +(sum / painted).toFixed(2) : 0 };
}

/**
 * Hold still for `ms`, delivered in `callbacks` equal slices.
 *
 * The whole point: the two arguments are independent, and only the first is
 * allowed to change the result.
 */
function hold(ms, callbacks, preset, flow) {
  reset(preset);
  if (flow != null) S.brushFlow = flow;
  const h = begin(256, 256);
  const slice = callbacks > 0 ? ms / callbacks : 0;
  let laid = 0;
  for (let i = 0; i < callbacks; i++) {
    _t += slice;
    laid += h.tick();
  }
  return { laid, ...describe(S.stroke.alphaMap) };
}

const R = {};

// ------------------------------------------- 1. holding still deposits paint
{
  const still = hold(500, 30);
  // The BASELINE IS A STROKE THAT WAS NEVER HELD, not an empty canvas. The
  // first run of this driver compared against a bare `reset()` -- a map with
  // no stroke on it at all -- so `depositsMore` was true for a timer that had
  // deposited nothing.
  const opening = hold(0, 0);
  R.holdingStillDeposits = {
    afterOpeningDabOnly: opening.total,
    afterHalfASecond: still.total,
    dabs: still.laid,
    depositsMore: still.total > opening.total,
  };
}

// -------------------------------- 2. an ordinary brush does NOT
//
// The acceptance criterion names both halves, and the second is what makes the
// first mean something: if every preset deposited on a timer this would be a
// global behaviour change wearing an airbrush's name.
{
  const air = hold(500, 30, "Airbrush");
  const round = hold(500, 30, "Basic Round");
  const roundOpening = hold(0, 0, "Basic Round");
  R.anOrdinaryBrushDoesNot = {
    airbrushDabs: air.laid,
    basicRoundDabs: round.laid,
    basicRoundUnchanged: round.total === roundOpening.total,
  };
}

// ------------------- 3. THE RATE IS INDEPENDENT OF THE CALLBACK FREQUENCY
//
// The probe this package exists for. Same elapsed time, wildly different
// scheduling.
{
  const rows = {};
  for (const callbacks of [1, 4, 20, 100]) {
    rows["cb" + callbacks] = hold(400, callbacks);
  }
  const dabs = Object.values(rows).map(r => r.laid);
  const totals = Object.values(rows).map(r => r.total);
  R.rateIsIndependentOfTheCallback = {
    at: Object.fromEntries(Object.entries(rows).map(
      ([k, v]) => [k, { dabs: v.laid, total: v.total }])),
    dabSpread: Math.max(...dabs) - Math.min(...dabs),
    totalSpread: Math.max(...totals) - Math.min(...totals),
  };
}

// ------------------------------------------ 4. and it is proportional to time
//
// Guards the guard. A timer that deposited NOTHING would have a spread of zero
// above and would satisfy every invariance test ever written.
{
  // AT FULL FLOW, so the counts are large enough to be a measurement. At the
  // Airbrush preset's own Flow of 0.15 the interval is 111ms and 100ms lays
  // nothing, so the doubling test would have been comparing zeros.
  const rows = {};
  for (const ms of [100, 200, 400, 800]) rows["ms" + ms] = hold(ms, 10, "Airbrush", 1).laid;
  R.depositionFollowsElapsedTime = {
    at: rows,
    // Doubling the time doubles the dabs, within one for the truncation the
    // debt carries.
    doubles: Math.abs(rows.ms200 - rows.ms100 * 2) <= 1
      && Math.abs(rows.ms400 - rows.ms200 * 2) <= 1
      && Math.abs(rows.ms800 - rows.ms400 * 2) <= 1,
  };
}

// --------------------------------------------------- 5. Flow sets the rate
{
  const rows = {};
  for (const flow of [0.15, 0.5, 1.0]) {
    reset();
    S.brushFlow = flow;
    const h = begin(256, 256);
    let laid = 0;
    for (let i = 0; i < 20; i++) { _t += 25; laid += h.tick(); }
    rows["flow" + flow] = { laid, interval: +C.airbrushInterval().toFixed(2) };
  }
  R.flowSetsTheRate = {
    at: rows,
    faster: rows.flow1.laid > rows["flow0.5"].laid
      && rows["flow0.5"].laid > rows["flow0.15"].laid,
  };
}

// ------------------------------------------- 6. moving deposits nothing extra
//
// The stationary-only decision, asserted rather than described.
{
  reset();
  const h = begin(256, 256);
  let laid = 0;
  for (let i = 1; i <= 20; i++) {
    _t += 25;
    S.stroke.lx = 256 + i * 10;      // the pointer has moved between ticks
    laid += h.tick();
  }
  R.movingDepositsNothingExtra = { laid, none: laid === 0 };
}

// ------------------------------- 7. a stale callback cannot write anything
//
// Clearing the interval is NECESSARY AND NOT SUFFICIENT: a callback can
// already be queued when `clearInterval` runs. Each of these captures a live
// tick, ends the stroke the way a browser would, and then fires it.
{
  const rows = {};

  // (a) commit
  reset();
  {
    const h = begin(256, 256);
    _t += 200;
    C.commitStroke();
    S.stroke.alphaMap = new Uint8Array(W * H);   // a fresh map to catch a write
    S.drawing = true;
    _t += 500;
    rows.afterCommit = h.tick();
  }

  // (b) abort
  reset();
  {
    const h = begin(256, 256);
    _t += 200;
    C.abortStroke();
    S.stroke.alphaMap = new Uint8Array(W * H);
    S.drawing = true;
    _t += 500;
    rows.afterAbort = h.tick();
  }

  // (c) an explicit stop, which is what setTool and switchDoc call
  reset();
  {
    const h = begin(256, 256);
    _t += 200;
    C.stopAirbrush();
    _t += 500;
    rows.afterStop = h.tick();
  }

  // (d) a NEW stroke started in between -- the token must not be reusable
  reset();
  {
    const h = begin(256, 256);
    _t += 200;
    C.stopAirbrush();
    begin(100, 100);
    _t += 500;
    rows.afterANewStroke = h.tick();
  }

  R.staleCallbacksWriteNothing = {
    at: rows,
    allZero: Object.values(rows).every(v => v === 0),
  };
}

// ------------------------------------------- 8. one stroke, one undo entry
{
  reset();
  S.undoStack = [];
  const h = begin(256, 256);
  for (let i = 0; i < 20; i++) { _t += 25; h.tick(); }
  const during = S.undoStack.length;
  C.commitStroke();
  R.oneStrokeOneUndo = {
    during, after: S.undoStack.length,
    // `beginStroke` is not what pushes the snapshot -- the pointer handler
    // does -- so the count here is whatever the harness set. What matters is
    // that a hundred timer dabs do not each add one.
    timerAddsNoEntries: during <= 1,
  };
}

// ------------------------------------ 9. a backgrounded tab cannot dump
//
// A tab throttled for a minute returns with one enormous elapsed time. The
// debt is capped at a second of deposition, which is the most a hand could
// have meant to lay down while away.
{
  reset();
  const h = begin(256, 256);
  _t += 60000;
  const laid = h.tick();
  R.aBackgroundedTabCannotDump = {
    laid,
    bounded: laid <= Math.ceil(1000 / C.airbrushInterval()) + 1,
  };
}

// ------------------------------------- 10. only the Airbrush preset declares it
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) rows[p.name] = !!p.airbrush;
  reset("Airbrush");
  const afterAir = S.brushAirbrush;
  C.applyBrushPreset("Basic Round");
  const afterRound = S.brushAirbrush;
  R.onlyAirbrushDeclaresIt = {
    at: rows,
    count: Object.values(rows).filter(Boolean).length,
    // The state-leak rule, for the fifth field in this programme.
    resets: afterAir === true && afterRound === false,
  };
}

console.log(JSON.stringify(R, null, 2));
