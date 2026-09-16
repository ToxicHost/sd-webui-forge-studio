/**
 * BE16: does the opening dab point where the stroke is going?
 *
 * THE OWNER'S REPORT: "This is a flat brush. See how the first press doesn't
 * match the angle/rotation of the drag?"
 *
 * THE METRIC, and the first version of it was wrong twice over. It measured
 * the second moment of the dab's pixels and compared it against the second
 * moment of the stroke's midpoint. A ROUND dab has no second moment worth the
 * name -- sxx equals syy and the angle is whatever the noise says -- so round
 * tips reported a confident 90-degree error on a defect they cannot have. And
 * the midpoint of a stroke is a BAND of overlapping dabs whose orientation is
 * the direction of travel, not the tip's.
 *
 * What is measured here is the only thing that matters: HOW DIFFERENT IS THE
 * OPENING DAB FROM THE DAB THAT SHOULD HAVE BEEN THERE. A reference dab is
 * stamped in isolation at the first segment's true tangent, under the same
 * taper, and the two are compared by intersection-over-union. IoU needs no
 * assumption about the tip's shape, which is what lets a round tip report
 * "unaffected" honestly instead of by exemption.
 *
 *   node tests/studio_alpha/be16_measure.js <path to canvas-core.js>
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

/**
 * Reset the DOCUMENT but NOT the module globals.
 *
 * That is the whole point of this driver: `_saSmooth` survives this, exactly as
 * it survives one stroke ending and the next beginning in a real session.
 */
function newDocument(preset) {
  reseed();
  C.stopAirbrush();
  S.W = W; S.H = H;
  S.tool = "brush";
  S.layers = [{ id: "t", name: "t", visible: true, opacity: 1,
    blendMode: "source-over", canvas: { width: W, height: H }, ctx: noopCtx }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(W * H),
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
  S.smoothing = 0;                       // the stabiliser is not the subject
  S.brushDynamics = Object.assign({}, S.brushDynamics,
    { sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0 });
}

function count(map) { let n = 0; for (let i = 0; i < map.length; i++) if (map[i]) n++; return n; }

/** Intersection over union of two coverage maps, counting any nonzero pixel. */
function iou(a, b) {
  let inter = 0, union = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i] > 0, y = b[i] > 0;
    if (x || y) union++;
    if (x && y) inter++;
  }
  return union ? +(inter / union).toFixed(4) : 1;
}

/**
 * The whole stroke, and the first dab isolated from it.
 *
 * The opening dab is captured by painting the SAME stroke twice: once stopping
 * after `beginStroke`, once complete. Before BE16 the first capture holds the
 * dab; after BE16 it is empty until the first move, so the isolated dab is
 * taken from the first move instead. Both are reported, and the test reads
 * whichever the engine produces -- a driver that assumed one shape would break
 * on the other for a reason that is not the defect.
 */
function strokeAndOpening(preset, from, to, opts) {
  const o = opts || {};

  newDocument(preset);
  if (o.leak !== undefined) C.strokeAngle = o.leak;
  C.beginStroke(from[0], from[1], 1.0);
  const afterBegin = new Uint8Array(S.stroke.alphaMap);
  // A MOVE SHORTER THAN ONE SPACING GAP, which is the whole trick.
  //
  // `dist > 0` is all it takes for a tangent to exist and for the held opening
  // dab to land -- but BE3's spacing debt still decides whether a BODY dab is
  // due, and a sub-gap move owes one. So this isolates the opening dab.
  //
  // The first version stepped 2% of the stroke instead. On a 280px stroke that
  // is 5.6px, which for Bristle Rake's 1.04px gap emitted five body dabs on
  // top of the opening one -- and the comparison scored 0.33 on an engine that
  // was placing the opening dab perfectly. A driver that cannot isolate the
  // thing it is measuring reports the wrong verdict with total confidence.
  const len = Math.hypot(to[0] - from[0], to[1] - from[1]);
  const NUDGE = 0.3;
  C.plotTo(from[0] + (to[0] - from[0]) / len * NUDGE,
           from[1] + (to[1] - from[1]) / len * NUDGE, 1.0);
  const afterFirstMove = new Uint8Array(S.stroke.alphaMap);
  C.stopAirbrush();

  return {
    afterBegin,
    afterFirstMove,
    openingIsImmediate: count(afterBegin) > 0,
  };
}

/** The dab that SHOULD open a stroke travelling `headingRad`. */
function referenceDab(preset, at, headingRad) {
  newDocument(preset);
  C.beginStroke(at[0], at[1], 1.0);
  const openingTaper = S.stroke._taperK;
  C.stopAirbrush();
  S.stroke.alphaMap.fill(0);
  // BE17 GAVE THE STROKE A SECOND BUFFER. Zeroing the map in place leaves
  // the 16-bit accumulator holding the coverage that was just erased, and
  // the next dab then builds on a mark that is no longer there. The engine
  // ties the accumulator to the map's IDENTITY, which a `fill(0)` does not
  // change -- so a caller that clears in place has to say so. Pastel and
  // Scatter Dust measured as opening at the wrong angle until this existed.
  S.stroke.accum = null;
  S.stroke._accumFor = null;
  S.stroke.dirty = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
  S.stroke._taperK = openingTaper;
  const dyn = S.brushDynamics;
  const rot = (dyn.followStroke !== false ? headingRad : 0)
    + (S.brushAngle || 0) * Math.PI / 180;
  reseed();
  C.stampWet(at[0], at[1], 1.0, rot);
  const map = new Uint8Array(S.stroke.alphaMap);
  C.stopAirbrush();
  return map;
}

/** Leak a heading into the module global, the way a finished stroke does. */
function leakHeading(preset, from, to) {
  newDocument(preset);
  C.beginStroke(from[0], from[1], 1.0);
  for (let i = 1; i <= 30; i++) {
    const t = i / 30;
    C.plotTo(from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, 1.0);
  }
  C.stopAirbrush();
  return C.strokeAngle;
}

const R = {};
const NORTH = -Math.PI / 2, EAST = 0, DIAG = -Math.PI / 4;

// ------------------------------------- 1. the opening dab follows the stroke
//
// THE OWNER'S COMPLAINT, as a number. After a stroke that went EAST, a stroke
// that goes NORTH must open with a NORTH dab.
{
  const rows = {};
  for (const preset of ["Flat Chisel", "Marker", "Bristle Rake",
                        "Calligraphy", "Basic Round"]) {
    leakHeading(preset, [120, 256], [400, 256]);          // east
    const s = strokeAndOpening(preset, [256, 400], [256, 120]);
    const should = referenceDab(preset, [256, 400], NORTH);
    // The dab wherever the engine puts it: at beginStroke, or at the first move.
    const opening = s.openingIsImmediate ? s.afterBegin : s.afterFirstMove;
    rows[preset] = {
      openingIsImmediate: s.openingIsImmediate,
      matchesTheStroke: iou(opening, should),
      openingPixels: count(opening),
    };
  }
  R.theOpeningDabFollowsTheStroke = rows;
}

// ------------------------------------------- 2. and on the first stroke ever
{
  const rows = {};
  for (const [name, from, to, heading] of [
    ["east", [120, 256], [400, 256], EAST],
    ["north", [256, 400], [256, 120], NORTH],
    ["diagonal", [140, 380], [380, 140], DIAG],
  ]) {
    const s = strokeAndOpening("Flat Chisel", from, to, { leak: 0 });
    const should = referenceDab("Flat Chisel", from, heading);
    const opening = s.openingIsImmediate ? s.afterBegin : s.afterFirstMove;
    rows[name] = { matchesTheStroke: iou(opening, should) };
  }
  R.firstStrokeOfASession = rows;
}

// -------------------------------------------- 3. every shipped preset agrees
{
  const rows = {};
  for (const p of C.DEFAULT_BRUSH_PRESETS) {
    const s = strokeAndOpening(p.name, [140, 380], [380, 140], { leak: NORTH });
    const should = referenceDab(p.name, [140, 380], DIAG);
    const opening = s.openingIsImmediate ? s.afterBegin : s.afterFirstMove;
    const m = iou(opening, should);
    rows[p.name] = {
      tip: p.preset,
      followStroke: p.dynamics.followStroke !== false,
      match: m,
      wrong: m < 0.999,
    };
  }
  const wrong = Object.entries(rows).filter(([, v]) => v.wrong).map(([k]) => k);
  R.everyPreset = { at: rows, wrong, wrongCount: wrong.length };
}

// ---------------------------------------------- 4. an absolute angle is safe
//
// Calligraphy IS a held nib: followStroke off, Angle 45. A fix that gave it the
// travel direction would break the one preset that is deliberately fixed.
{
  const rows = {};
  for (const [name, to, heading] of [
    ["north", [256, 120], NORTH], ["east", [400, 400], EAST]]) {
    const s = strokeAndOpening("Calligraphy", [256, 400], to, { leak: Math.PI / 3 });
    const should = referenceDab("Calligraphy", [256, 400], heading);
    const opening = s.openingIsImmediate ? s.afterBegin : s.afterFirstMove;
    rows[name] = iou(opening, should);
  }
  // followStroke is off, so the reference ignores the heading entirely and both
  // directions must give the same, correct dab.
  R.anAbsoluteAngleIsUntouched = { at: rows, both: rows.north === 1 && rows.east === 1 };
}

// ------------------------------------------------------- 5. A TAP STILL PAINTS
//
// The hazard a deferral introduces. Press and release without moving: there is
// no tangent and there never will be, and the mark must still exist.
{
  const rows = {};
  for (const preset of ["Flat Chisel", "Basic Round", "Pixel Perfect"]) {
    newDocument(preset);
    C.beginStroke(256, 256, 1.0);
    const beforeEnd = count(S.stroke.alphaMap);
    C.finishStroke(256, 256, 1.0);
    const afterFinish = count(S.stroke.alphaMap);
    C.stopAirbrush();
    rows[preset] = { beforeEnd, afterFinish, painted: afterFinish > 0 };
  }
  R.aTapStillPaints = { at: rows, all: Object.values(rows).every(r => r.painted) };
}

// ------------------------------- 5b. and a commit without a finish still paints
//
// The backstop. A caller that begins and commits without a move -- the headless
// drivers do exactly this -- must not commit an empty stroke.
{
  newDocument("Flat Chisel");
  C.beginStroke(256, 256, 1.0);
  const beforeCommit = count(S.stroke.alphaMap);

  // WHAT THE COMMIT ACTUALLY CONVERTS. The alpha map is nulled by
  // `commitStroke` on its way out, so counting it afterwards answers nothing.
  // The commit's own call to `alphaMapToImageData` is the last moment the
  // coverage exists, so that is where it is read.
  //
  // The wrapper is installed on the EXPORT, which `commitStroke` does not call
  // -- it closes over the module-internal function. So the map is read from a
  // one-shot hook on the layer instead: `drawImage` runs immediately after the
  // conversion and before the map is cleared.
  let atCommitTime = -1;
  const target = S.layers[S.activeLayerIdx];
  const realDrawImage = target.ctx.drawImage;
  target.ctx.drawImage = function () {
    if (atCommitTime < 0) atCommitTime = count(S.stroke.alphaMap);
  };
  C.commitStroke();
  target.ctx.drawImage = realDrawImage;
  C.stopAirbrush();

  R.aCommitWithoutAFinishStillPaints = {
    beforeCommit,
    atCommitTime,
    painted: atCommitTime > 0,
  };
}

// ---------------------------- 6. the airbrush and the opening dab agree
//
// A HELD brush has no direction. Whatever angle its timer deposits take, the
// opening dab must take the same one, or a held flat brush builds up at two
// angles at once.
{
  const LEAK = Math.PI / 3;
  newDocument("Flat Chisel");
  C.strokeAngle = LEAK;
  S.brushAirbrush = true;
  S.brushFlow = 1.0;
  let tick = null;
  C.beginStroke(256, 256, 1.0);
  C.stopAirbrush();
  C.startAirbrush(fn => { tick = fn; return () => {}; });
  S.stroke.alphaMap.fill(0);               // isolate the timer's own deposit
  // BE17 GAVE THE STROKE A SECOND BUFFER. Zeroing the map in place leaves
  // the 16-bit accumulator holding the coverage that was just erased, and
  // the next dab then builds on a mark that is no longer there. The engine
  // ties the accumulator to the map's IDENTITY, which a `fill(0)` does not
  // change -- so a caller that clears in place has to say so. Pastel and
  // Scatter Dust measured as opening at the wrong angle until this existed.
  S.stroke.accum = null;
  S.stroke._accumFor = null;
  if (C.setAirbrushClock) C.setAirbrushClock(() => 1000);
  const laid = tick ? tick() : 0;
  const held = new Uint8Array(S.stroke.alphaMap);
  C.stopAirbrush();

  const atLeak = referenceDab("Flat Chisel", [256, 256], LEAK);
  const atNone = referenceDab("Flat Chisel", [256, 256], 0);
  R.theAirbrushHasNoDirectionEither = {
    dabsLaid: laid,
    matchesTheLeakedHeading: iou(held, atLeak),
    matchesNoHeading: iou(held, atNone),
  };
}

// ------------------------------------- 7. an aborted stroke leaves nothing
{
  newDocument("Flat Chisel");
  C.beginStroke(256, 256, 1.0);
  C.abortStroke();
  const survived = S.stroke && S.stroke._openingDab ? 1 : 0;
  // And the NEXT stroke must not inherit it.
  newDocument("Flat Chisel");
  C.beginStroke(100, 100, 1.0);
  C.plotTo(180, 100, 1.0);
  C.finishStroke(180, 100, 1.0);
  let strayAt256 = 0;
  for (let y = 230; y < 285; y++) for (let x = 230; x < 285; x++) {
    if (S.stroke.alphaMap[y * W + x]) strayAt256++;
  }
  C.stopAirbrush();
  R.anAbortedStrokeLeavesNothing = { pendingSurvived: survived, strayAt256 };
}

// ------------------------ 7b. a zero-length move must not settle the heading
//
// `Math.atan2(0, 0)` is 0 in JavaScript, and `plotTo` computes the tangent
// unconditionally. A duplicate-coordinate pointermove -- which browsers do
// deliver -- would therefore flush the held dab at zero radians: the same wrong
// answer in a different disguise. The flush is gated on `dist > 0`.
{
  newDocument("Flat Chisel");
  C.strokeAngle = 0;
  C.beginStroke(256, 400, 1.0);
  C.plotTo(256, 400, 1.0);                 // the duplicate the browser sends
  const afterDuplicate = count(S.stroke.alphaMap);
  C.plotTo(256, 399.7, 1.0);               // now a real, sub-gap move north
  const opening = new Uint8Array(S.stroke.alphaMap);
  C.stopAirbrush();
  const should = referenceDab("Flat Chisel", [256, 400], NORTH);
  R.aZeroLengthMoveSettlesNothing = {
    paintedAfterTheDuplicate: afterDuplicate,
    matchesTheStroke: iou(opening, should),
  };
}

// ---------------------------------- 7c. the deferred dab keeps its own taper
//
// `_taperK` belongs to distance travelled and the opening dab's is zero, but
// `stampWet` reads one mutable slot that `plotTo` overwrites per dab. A flush
// that re-read it would lay the point of the stroke at full width.
//
// None of the three deferred presets ships Taper In, so this is only reachable
// through the dynamics panel -- which is exactly why it needs a test rather
// than an assumption.
{
  newDocument("Flat Chisel");
  C.strokeAngle = 0;
  S.brushTaperIn = 1.0;
  C.beginStroke(256, 400, 1.0);
  const taperAtPress = S.stroke._taperK;
  // Several real segments, so `_taperK` has climbed well away from its start.
  for (let i = 1; i <= 6; i++) C.plotTo(256, 400 - i * 8, 1.0);
  const taperAfter = S.stroke._taperK;
  C.stopAirbrush();

  // The opening dab alone, at the same taper the press had.
  newDocument("Flat Chisel");
  S.brushTaperIn = 1.0;
  C.strokeAngle = 0;
  C.beginStroke(256, 400, 1.0);
  C.plotTo(256, 399.7, 1.0);
  let openWidth = 0;
  for (let x = 0; x < W; x++) if (S.stroke.alphaMap[400 * W + x]) openWidth++;
  C.stopAirbrush();
  R.theDeferredDabKeepsItsOwnTaper = {
    taperAtPress: +taperAtPress.toFixed(4),
    taperAfterSixSegments: +taperAfter.toFixed(4),
    openingDabWidth: openWidth,
  };
}

// ------------------------------- 7d. a mask toggle cannot reshape a held dab
//
// `stampWet` reads `S.editingMask` LIVE and forces a round, hardness-1 tip from
// it. The toggle is a keystroke, so it can land between the press and the first
// move. `beginStroke` already locks `_commitTarget` and `_commitMask` for the
// same reason; the held dab captures the same thing.
{
  const shape = (toggleTo) => {
    newDocument("Flat Chisel");
    C.strokeAngle = 0;
    S.editingMask = false;
    C.beginStroke(256, 400, 1.0);
    if (toggleTo !== null) S.editingMask = toggleTo;   // q, between the events
    C.plotTo(256, 399.7, 1.0);
    const map = new Uint8Array(S.stroke.alphaMap);
    S.editingMask = false;
    C.stopAirbrush();
    return map;
  };
  const untouched = shape(null);
  const toggled = shape(true);
  R.aMaskToggleCannotReshapeAHeldDab = {
    pixelsUntouched: count(untouched),
    pixelsAfterToggle: count(toggled),
    identical: iou(untouched, toggled),
  };
}

// ------------------- 7e. an aliased stroke advances the heading like any other
//
// The pixel-walk branch of `plotTo` returns before the dab loop, so
// `_advanceHeading` never ran for an aliased or pixel-perfect stroke: the
// global held the previous stroke's direction for the WHOLE of it.
{
  newDocument("Pixel Perfect");
  C.strokeAngle = Math.PI / 2;             // a leaked heading, south
  C.beginStroke(100, 100, 1.0);
  for (let i = 1; i <= 12; i++) C.plotTo(100 + i * 6, 100, 1.0);   // east
  const headingAfter = C.strokeAngle;
  const known = !!(S.stroke && S.stroke._headingKnown);
  C.stopAirbrush();
  R.anAliasedStrokeAdvancesTheHeading = {
    headingDeg: +(((headingAfter * 180 / Math.PI) % 360 + 360) % 360).toFixed(1),
    headingKnown: known,
  };
}

// ------------------------------------------ 8. the flag finally has a reader
{
  const src = fs.readFileSync(process.argv[2], "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
  const writes = (src.match(/_headingKnown\s*=/g) || []).length;
  const mentions = (src.match(/_headingKnown/g) || []).length;
  R.headingKnownFlag = {
    writesInCode: writes,
    mentionsInCode: mentions,
    readsOtherThanWrites: mentions - writes,
  };
}

// -------------------------- 9. rotation jitter is applied ONCE, in stampWet
//
// FOUND BY THE BE17 AUDIT, inside this package's own subject. `plotTo` applied
// the jitter and `stampWet` applied it again, so a BODY dab turned twice as far
// as the label promised while the OPENING dab, which reaches `stampWet`
// directly, turned once. The first press disagreeing with the drag, from a
// second direction, one function away from the fix this package wrote.
{
  const src = fs.readFileSync(process.argv[2], "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
  R.rotationJitterApplications = {
    inCode: (src.match(/Math\.PI \* dyn\.rotationJitter/g) || []).length,
    inStampWet: /ang \+= \(Math\.random\(\) \* 2 - 1\) \* Math\.PI \* dyn\.rotationJitter/
      .test(src) ? 1 : 0,
  };
}

// ------------- 10. the jitter no longer moves the dabs whose gap it prices
//
// `spacingFor` prices the gap from the same angle the dab is drawn at. While
// the jitter was applied in `plotTo`, that angle was one dab's random draw, so
// Rotation Jitter silently changed how MANY dabs a stroke laid as well as how
// they were turned. Painted pixels is the visible proxy for that.
{
  const rows = {};
  for (const preset of ["Flat Chisel", "Marker"]) {
    rows[preset] = {};
    for (const j of [0, 0.13, 0.5]) {
      newDocument(preset);
      S.brushDynamics = Object.assign({}, S.brushDynamics, { rotationJitter: j });
      C.beginStroke(80, 200, 1.0);
      for (let i = 1; i <= 60; i++) C.plotTo(80 + i * 4, 200, 1.0);
      C.finishStroke(320, 200, 1.0);
      rows[preset]["jitter" + Math.round(j * 100)] = count(S.stroke.alphaMap);
      C.stopAirbrush();
    }
  }
  R.paintedPixelsByJitter = rows;
}

// ------------------- 11. a tap inks at press whatever Spikes is set to
//
// This package's own fast path used to defer the press dab whenever Spikes > 2,
// on a tip where a spike fold provably cannot change a pixel: measured 221 px
// at press with Spikes 2 and ZERO with Spikes 3, for a finished stroke that is
// byte-identical either way. Twelve presets paid a pointer event of missing
// feedback for a control that could not help them.
{
  const rows = {};
  for (const spikes of [2, 3, 6, 12]) {
    newDocument("Basic Round");
    S.brushSpikes = spikes;
    C.beginStroke(200, 200, 1.0);
    const atPress = count(S.stroke.alphaMap);
    for (let i = 1; i <= 30; i++) C.plotTo(200 + i * 4, 200, 1.0);
    C.finishStroke(320, 200, 1.0);
    rows["spikes" + spikes] = { inkedAtPress: atPress, finished: count(S.stroke.alphaMap) };
    C.stopAirbrush();
  }
  R.aTapInksAtPressAtEverySpikes = rows;
}

realLog(JSON.stringify(R, null, 2));
