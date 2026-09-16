/**
 * BE11: do dynamics curves reach the dabs, and do they stay honest about what
 * was measured?
 *
 * SYNTHETIC RAMPS, AND THE TESTS SAY SO. There is no pen on this machine. What
 * a synthetic pressure ramp proves is the arithmetic and the plumbing -- that
 * a curve maps an input to a per-dab modifier, that the modifier reaches the
 * stamp, and that an UNAVAILABLE input takes the rule's declared fallback
 * instead. It does not prove anything about hardware and nothing here claims
 * it does.
 *
 * THE MEASUREMENT PROBLEM, and it is the same one BE10 hit one package ago:
 * a dynamics rule changes the dab, and every dab lands in an accumulator that
 * a dozen other things also write to. So each probe varies ONE rule against an
 * otherwise identical stroke and reports the coverage difference, rather than
 * trying to read a modifier out of the pixels.
 *
 *   node tests/studio_alpha/be11_measure.js <path to canvas-core.js>
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
  S.W = W; S.H = H;
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
  C.applyBrushPreset(preset || "Basic Round");
  S.smoothing = 0;
  S.brushCurves = [];
  // Jitter off for every probe below. A dynamics rule and a jitter both change
  // the same numbers, and a probe that left jitter on would be reporting the
  // sum of the two.
  S.brushDynamics = Object.assign({}, S.brushDynamics, {
    sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0,
  });
}

/**
 * A stroke, optionally with a synthetic pen and a pressure ramp.
 *
 * `pen` is what `noteSample` would have been handed by the pointer seam. It is
 * passed BEFORE each plotTo, exactly as canvas-ui does, so the availability
 * flags are live for the dab that follows.
 */
function paint(o) {
  reset(o.preset);
  if (o.curves) S.brushCurves = o.curves.map(r => Object.assign({}, r));
  if (o.size) S.brushSize = o.size;
  const steps = o.steps || 24;
  const y = 256;
  const press = i => (o.ramp ? 0.05 + 0.95 * (i / steps) : 1.0);
  C.beginStroke(80, y, press(0));
  for (let i = 1; i <= steps; i++) {
    if (o.pen) {
      C.noteSample(Object.assign(
        { pressureAvailable: false, tiltAvailable: false, tiltX: 0, tiltY: 0,
          time: o.msPerStep != null ? i * o.msPerStep : undefined },
        o.pen));
    }
    C.plotTo(80 + i * 14, y, press(i));
  }
  return S.stroke.alphaMap;
}

function describe(map) {
  let painted = 0, sum = 0, peak = 0, digest = 0, minY = 1e9, maxY = -1e9;
  for (let i = 0; i < map.length; i++) {
    const a = map[i];
    if (a > 0) {
      painted++; sum += a; if (a > peak) peak = a;
      const py = (i / W) | 0;
      if (py < minY) minY = py;
      if (py > maxY) maxY = py;
    }
    digest = (digest * 31 + a * (i % 977)) >>> 0;
  }
  return {
    painted, peak, digest,
    mean: painted ? +(sum / painted).toFixed(2) : 0,
    // Stroke thickness, which is what a size rule changes and a flow rule
    // does not. Reported so the two can be told apart.
    height: painted ? maxY - minY + 1 : 0,
  };
}

/**
 * Stroke thickness in ONE COLUMN.
 *
 * REPLACED A MAX OVER THE WHOLE STROKE, which reported no change for two
 * probes that were in fact working. Two separate reasons, one metric:
 *
 *   a pressure RAMP reaches full pressure at the end, so the widest point of a
 *   narrowed stroke is exactly as wide as before;
 *
 *   `beginStroke` lays the opening dab BEFORE any `noteSample`, so that dab
 *   always has neutral modifiers -- correctly, nothing has been measured yet --
 *   and a max over the stroke is a max over that dab.
 *
 * A named column answers "how wide is the stroke HERE", which is the question.
 */
function thicknessAt(map, x) {
  let lo = -1, hi = -1;
  for (let y = 0; y < H; y++) {
    if (map[y * W + x] > 0) { if (lo < 0) lo = y; hi = y; }
  }
  return lo < 0 ? 0 : hi - lo + 1;
}

const PEN = { pressureAvailable: true, tiltAvailable: false };
const PEN_TILTED = { pressureAvailable: true, tiltAvailable: true, tiltX: 60, tiltY: 45 };
const MOUSE = { pressureAvailable: false, tiltAvailable: false };

const R = {};

// -------------------------------------------------- 1. an empty list is neutral
//
// The acceptance criterion "presets using no curves remain identical", and the
// one every other probe here depends on.
{
  const bare = describe(paint({ curves: [] }));
  const undef = describe(paint({}));
  R.emptyIsNeutral = {
    identical: bare.digest === undef.digest,
    digest: bare.digest,
  };
}

// ------------------------------------------------ 2. a size curve changes width
{
  const offMap = paint({ size: 30, ramp: true, pen: PEN });
  const off = describe(offMap);
  const offT = { early: thicknessAt(offMap, 122), late: thicknessAt(offMap, 380) };
  const onMap = paint({
    size: 30, ramp: true, pen: PEN,
    curves: [{ input: "pressure", target: "size", curve: "linear",
               min: 0.2, max: 1, fallback: 1 }],
  });
  const on = describe(onMap);
  const onT = { early: thicknessAt(onMap, 122), late: thicknessAt(onMap, 380) };
  R.sizeCurveActs = {
    off, on,
    // A rule that narrowed the whole stroke equally would be a size CHANGE,
    // not a size CURVE, so both ends are reported as a ratio to the unmodified
    // stroke. The first draft asserted the late end was IDENTICAL, which is
    // not true and should not be: the ramp only reaches 0.90 by that column,
    // so the rule is still asking for 92% of the width. It asks for 33% at the
    // early one, and the gap between those two numbers is the curve.
    earlyOff: offT.early, earlyOn: onT.early,
    lateOff: offT.late, lateOn: onT.late,
    earlyRatio: +(onT.early / offT.early).toFixed(3),
    lateRatio: +(onT.late / offT.late).toFixed(3),
    narrowerEarly: onT.early < offT.early,
    widensAlongTheRamp: (onT.late / offT.late) > (onT.early / offT.early) * 2,
    lessPainted: on.painted < off.painted,
  };
}

// ------------------------------- 3. a flow curve changes weight, not width
//
// The two targets must be distinguishable, or "size" and "flow" are one
// control with two names -- which is the defect BE5 removed one level up.
{
  const offMap = paint({ size: 30, ramp: true, pen: PEN });
  const onMap = paint({
    size: 30, ramp: true, pen: PEN,
    curves: [{ input: "pressure", target: "flow", curve: "linear",
               min: 0.1, max: 1, fallback: 1 }],
  });
  const off = describe(offMap), on = describe(onMap);
  R.flowCurveActs = {
    off, on,
    dimmer: on.mean < off.mean,
    // Weight and width are different targets, and a package that let one do
    // the other's job would be BE5's defect at a different level.
    sameWidthEarly: thicknessAt(onMap, 122) === thicknessAt(offMap, 122),
    sameWidthLate: thicknessAt(onMap, 380) === thicknessAt(offMap, 380),
  };
}

// ---------------------------------------- 4. THE FALLBACK IS THE RULE'S OWN
//
// The heart of the package. The same curve, the same stroke, the same ramp --
// and a device that reports no pressure. Three declared fallbacks must give
// three different marks, and the one declared 1 must be identical to no curve
// at all.
{
  const curve = f => [{ input: "pressure", target: "flow", curve: "linear",
                        min: 0.1, max: 1, fallback: f }];
  const none = describe(paint({ size: 30, ramp: true, pen: MOUSE }));
  const rows = {};
  for (const f of [0, 0.5, 1]) {
    rows["fallback" + f] = describe(paint({
      size: 30, ramp: true, pen: MOUSE, curves: curve(f) }));
  }
  R.unavailableUsesTheDeclaredFallback = {
    noCurve: none.mean,
    at: { f0: rows.fallback0.mean, f05: rows["fallback0.5"].mean, f1: rows.fallback1.mean },
    distinct: new Set(Object.values(rows).map(r => r.digest)).size,
    oneIsNeutral: rows.fallback1.digest === none.digest,
    // A fake maximal value would make all three equal to the top of the range.
    zeroIsNotMaximal: rows.fallback0.mean < rows.fallback1.mean,
  };
}

// ------------------------------- 5. a pen and a mouse are told apart at all
{
  const curve = [{ input: "pressure", target: "flow", curve: "linear",
                   min: 0.1, max: 1, fallback: 1 }];
  const pen = describe(paint({ size: 30, ramp: true, pen: PEN, curves: curve }));
  const mouse = describe(paint({ size: 30, ramp: true, pen: MOUSE, curves: curve }));
  R.penAndMouseDiffer = {
    pen: pen.mean, mouse: mouse.mean,
    differ: pen.digest !== mouse.digest,
  };
}

// ------------------------------------------------ 6. the five curves differ
{
  const rows = {};
  for (const shape of ["linear", "easeIn", "easeOut", "sShape", "sharp", "flat"]) {
    rows[shape] = describe(paint({
      size: 30, ramp: true, pen: PEN,
      curves: [{ input: "pressure", target: "flow", curve: shape,
                 min: 0.05, max: 1, fallback: 1 }],
    }));
  }
  R.curveShapesDiffer = {
    at: Object.fromEntries(Object.entries(rows).map(([k, v]) => [k, v.mean])),
    distinct: new Set(Object.values(rows).map(r => r.digest)).size,
    count: Object.keys(rows).length,
    // easeIn is below linear is below easeOut for a rising ramp: that is what
    // the three names MEAN, and a table that had them in another order would
    // still pass a distinctness count.
    ordered: rows.easeIn.mean < rows.linear.mean && rows.linear.mean < rows.easeOut.mean,
    // `flat` ignores the input and pins the target at the top of the range.
    flatIsTheTop: Math.abs(rows.flat.mean - describe(paint({ size: 30, ramp: true, pen: PEN })).mean) < 0.01,
  };
}

// --------------------------------------------------- 7. the range is honest
//
// `min === max` must be exactly neutral by arithmetic, not by a special case.
{
  const base = describe(paint({ size: 30, ramp: true, pen: PEN }));
  const pinned = describe(paint({
    size: 30, ramp: true, pen: PEN,
    curves: [{ input: "pressure", target: "flow", curve: "easeIn",
               min: 1, max: 1, fallback: 1 }],
  }));
  const half = describe(paint({
    size: 30, ramp: true, pen: PEN,
    curves: [{ input: "pressure", target: "flow", curve: "easeIn",
               min: 0.5, max: 0.5, fallback: 1 }],
  }));
  R.theRangeIsHonest = {
    unitIsNeutral: pinned.digest === base.digest,
    halfIsHalf: half.mean < base.mean,
    ratio: base.mean ? +(half.mean / base.mean).toFixed(3) : 0,
  };
}

// ---------------------------------------------------------- 8. tilt and ratio
{
  const curve = [{ input: "tilt", target: "ratio", curve: "linear",
                   min: 1, max: 0.3, fallback: 0 }];
  const uprightMap = paint({ preset: "Flat Shader", size: 40, pen: PEN, curves: curve });
  const leanedMap = paint({ preset: "Flat Shader", size: 40, pen: PEN_TILTED, curves: curve });
  const mouseMap = paint({ preset: "Flat Shader", size: 40, pen: MOUSE, curves: curve });
  // Measured in a column PAST the opening dab, which is laid before any sample
  // has been noted and therefore always has neutral modifiers.
  R.tiltDrivesRatio = {
    upright: thicknessAt(uprightMap, 250),
    leaned: thicknessAt(leanedMap, 250),
    mouse: thicknessAt(mouseMap, 250),
    leaningNarrows: thicknessAt(leanedMap, 250) < thicknessAt(uprightMap, 250),
    // fallback 0 lands on `min`, which is 1 -- the mouse gets the shape it had
    // before this package.
    mouseIsUpright: describe(mouseMap).digest === describe(uprightMap).digest,
  };
}

// -------------------------------------------------------------- 9. direction
//
// The one input that is always available, because it is a property of the path
// rather than of the device.
{
  const curve = [{ input: "direction", target: "flow", curve: "linear",
                   min: 0.2, max: 1, fallback: 0 }];
  const marks = {};
  for (const [name, dx, dy] of [["east", 14, 0], ["north", 0, -14], ["diag", 10, 10]]) {
    reset();
    S.brushCurves = curve.map(r => Object.assign({}, r));
    S.brushSize = 30;
    S.brushDynamics = Object.assign({}, S.brushDynamics,
      { sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0 });
    C.beginStroke(256 - dx * 8, 256 - dy * 8, 1.0);
    for (let i = 1; i <= 16; i++) C.plotTo(256 - dx * 8 + dx * i, 256 - dy * 8 + dy * i, 1.0);
    marks[name] = describe(S.stroke.alphaMap).mean;
  }
  R.directionIsAlwaysAvailable = {
    at: marks,
    distinct: new Set(Object.values(marks)).size,
  };
}

// ------------------------------------------------------------------ 10. speed
//
// Available only when a timestamp reached the engine. `msPerStep` is what the
// pointer seam would have carried; without it the rule falls back, which is
// the same treatment the pen inputs get rather than a special case for tests.
{
  const curve = [{ input: "speed", target: "flow", curve: "linear",
                   min: 1, max: 0.3, fallback: 0 }];
  const slow = describe(paint({ size: 30, pen: PEN, msPerStep: 60, curves: curve }));
  const fast = describe(paint({ size: 30, pen: PEN, msPerStep: 4, curves: curve }));
  const untimed = describe(paint({ size: 30, pen: PEN, curves: curve }));
  const bare = describe(paint({ size: 30, pen: PEN }));
  R.speedNeedsATimestamp = {
    slow: slow.mean, fast: fast.mean, untimed: untimed.mean, bare: bare.mean,
    fasterIsLighter: fast.mean < slow.mean,
    // fallback 0 lands on `min` = 1, so an untimed stroke is exactly the
    // unmodified one. That is the honest answer, not a guess at a speed.
    untimedIsNeutral: untimed.digest === bare.digest,
  };
}

// ------------------------------------- 11. curve state cannot leak presets
{
  reset();
  C.applyBrushPreset("Pencil");
  const pencil = S.brushCurves.length;
  C.applyBrushPreset("Pixel");
  const pixel = S.brushCurves.length;
  C.applyBrushPreset("Pencil");
  const back = S.brushCurves.length;
  // Mutating the live rules must not reach the preset table.
  if (S.brushCurves.length) S.brushCurves[0].min = -99;
  C.applyBrushPreset("Pencil");
  R.curvesDoNotLeak = {
    pencil, pixel, back,
    resets: pixel === 0 && back === pencil,
    tableIsIntact: !S.brushCurves.length || S.brushCurves[0].min !== -99,
  };
}

// --------------------------------------- 12. the shipped presets declare them
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    rows[p.name] = {
      rules: (p.curves || []).length,
      // Every rule must name a real input, a real target and a real curve, and
      // must declare its own fallback. A rule that omitted one would inherit a
      // default someone else picked, which is what this package exists to stop.
      wellFormed: (p.curves || []).every(r =>
        C.DYN_INPUTS.indexOf(r.input) >= 0
        && C.DYN_TARGETS.indexOf(r.target) >= 0
        && Object.prototype.hasOwnProperty.call(C.DYN_CURVES, r.curve)
        && typeof r.fallback === "number"
        && typeof r.min === "number" && typeof r.max === "number"),
    };
  }
  R.shippedPresetsDeclareCurves = {
    at: rows,
    withCurves: Object.values(rows).filter(r => r.rules > 0).length,
    allWellFormed: Object.values(rows).every(r => r.wellFormed),
    // BE14 renamed it, and the rename is a KEY change rather than a label
    // change -- the alias table exists for exactly that reason.
    pixelHasNone: rows["Pixel Perfect"].rules === 0,
  };
}

// ------------------- 13. and on a mouse they change nothing they used to do
//
// Every fallback in the shipped table maps to the preset's pre-BE11 behaviour,
// so a mouse paints what it painted yesterday. That is a curation decision
// about the table rather than a property of the mechanism -- probe 4 shows the
// mechanism takes any fallback -- and this is the probe that holds the table
// to it.
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    const withCurves = describe(paint({ preset: p.name, ramp: true, pen: MOUSE }));
    const stripped = describe(paint({ preset: p.name, ramp: true, pen: MOUSE, curves: [] }));
    rows[p.name] = withCurves.digest === stripped.digest;
  }
  R.aMouseSeesNoChange = {
    at: rows,
    all: Object.values(rows).every(Boolean),
  };
}

console.log(JSON.stringify(R, null, 2));
