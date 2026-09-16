/**
 * BE3: is dab placement a function of DISTANCE or of EVENT RATE?
 *
 * THE TEST THAT DOES NOT WORK, and why it is worth recording.
 *
 * The obvious version samples a sine curve at 15, 30, 62, 120 points and
 * compares the output. It reports "still rate-dependent" on a perfectly
 * correct engine, because those are not the same path: a sine sampled at 15
 * points is a coarse polyline that cuts every curve, and at 120 it hugs them.
 * The geometry differs, so of course the pixels do.
 *
 * A real event-rate test must vary ONLY the number of events. So the path is
 * fixed first -- as an explicit list of vertices -- and then SUBDIVIDED. Every
 * rate walks exactly the same polyline; the fast ones simply report their
 * position more often along it.
 */

"use strict";

globalThis.window = globalThis;
// BE17 needs a LAYER read from this driver -- Buildup's other half is the
// commit-time bound, which `S.stroke.alphaMap` cannot see. `alphaMapToImageData`
// allocates an ImageData, and this driver had never called it, so the shim was
// never needed until now.
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

function reset(preset) {
  _seed = 1;
  S.W = W; S.H = H;
  const noopCtx = {
    clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
    fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
  };
  S.layers = [{
    id: "t", name: "t", visible: true, opacity: 1, blendMode: "source-over",
    canvas: { width: W, height: H }, ctx: noopCtx,
  }];
  S.activeLayerIdx = 0;
  S.stroke = {
    alphaMap: new Uint8Array(W * H),
    dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
    _taperK: 1, lx: 0, ly: 0, lp: 1,
    ctx: noopCtx, canvas: { width: W, height: H },
  };
  S.selection = { active: false, mask: null, rect: null, dragging: false };
  S.editingMask = false; S.symmetry = "none"; S.pressureSensitivity = false;
  S.brushAngle = 0; S.brushRatio = 1; S.brushSpikes = 2;
  S.brushDensity = 1; S.brushFalloff = "default"; S.brushTaperIn = 0;
  C.applyBrushPreset(preset || "Basic Round");
}

/** THE path. Fixed vertices, so subdividing cannot change its geometry. */
const PATH = [
  [60, 200], [180, 200], [260, 320], [380, 120], [450, 300],
];

/** Walk PATH, reporting position `perSegment` times inside each segment. */
function walk(perSegment, preset) {
  reset(preset);
  // NOTE: wrapping C.stampWet does NOT intercept anything. `plotTo` closes
  // over the module-internal `stampWet` (`const fn = stampWet`), so the export
  // is a different reference. Counting dabs that way silently reports zero,
  // which is why coverage is measured instead -- pixels cannot be faked by a
  // wrapper that never fires.
  {
    C.beginStroke(PATH[0][0], PATH[0][1], 1.0);
    for (let v = 1; v < PATH.length; v++) {
      const [ax, ay] = PATH[v - 1], [bx, by] = PATH[v];
      for (let i = 1; i <= perSegment; i++) {
        const t = i / perSegment;
        C.plotTo(ax + (bx - ax) * t, ay + (by - ay) * t, 1.0);
      }
    }
  }
  const m = S.stroke.alphaMap;
  let pixels = 0, sum = 0, digest = 0;
  for (let i = 0; i < m.length; i++) if (m[i]) {
    pixels++; sum += m[i];
    digest = (digest + m[i] * (i + 1) * 2654435761) % 2147483647;
  }
  return { eventsPerSegment: perSegment, pixels, sum, digest };
}

const RATES = { "1x": 1, "2x": 2, "4x": 4, "8x": 8, "17x_irregular": 17, "40x": 40 };
const out = { path: "fixed 5-vertex polyline, subdivided", rates: {} };
for (const [name, n] of Object.entries(RATES)) out.rates[name] = walk(n);

const digests = new Set(Object.values(out.rates).map(r => r.digest));
const pixelCounts = new Set(Object.values(out.rates).map(r => r.pixels));
out.INVARIANT = {
  distinctDigests: digests.size,
  distinctPixelCounts: pixelCounts.size,
  verdict: digests.size === 1 && pixelCounts.size === 1
    ? "EVENT-RATE INVARIANT" : "RATE-DEPENDENT",
};

// A sub-spacing event must emit NOTHING. The old code guaranteed one dab per
// event, so this is the case it could not express.
function digestOf() {
  const m = S.stroke.alphaMap;
  let d = 0, n = 0;
  for (let i = 0; i < m.length; i++) if (m[i]) { n++; d = (d + m[i] * (i + 1) * 2654435761) % 2147483647; }
  return { d, n };
}
// The movement must be sub-spacing FOR THIS BRUSH, not a fixed 1 px.
//
// It was a fixed 1 px, and BE5 broke it -- tightening soft-tip spacing from a
// flat 0.10 to the ordinary formula made 1 px larger than one gap, so the
// "sub-spacing" events stopped being sub-spacing and the test failed for a
// reason that had nothing to do with what it measures. A fixture that encodes
// a constant from another package's behaviour expires when that package moves.
function subSpacingWalk(preset) {
  reset(preset);
  C.beginStroke(256, 256, 1.0);
  const gap = C.spacingFor(1.0, C.brushPx(), C.spacingFraction());
  const before = digestOf();
  // Twenty events summing to HALF one gap: unambiguously sub-spacing whatever
  // the preset's geometry works out to.
  const step = (gap * 0.5) / 20;
  for (let i = 1; i <= 20; i++) C.plotTo(256 + i * step, 256, 1.0);
  return { gapPx: +gap.toFixed(3), totalTravelPx: +(gap * 0.5).toFixed(3),
           before, after: digestOf() };
}
const hardWalk = subSpacingWalk("Basic Round");
const opening = hardWalk.before;
const afterTiny = hardWalk.after;
out.subSpacing = {
  gapPx: hardWalk.gapPx,
  totalTravelPx: hardWalk.totalTravelPx,
  openingDabPixels: opening.n,
  pixelsAfterTwentyTinyEvents: afterTiny.n,
  coverageUnchanged: opening.d === afterTiny.d,
  verdict: opening.d === afterTiny.d
    ? "twenty sub-spacing events emitted NOTHING, correct"
    : "a sub-spacing event still forced a dab",
};

// The same question on an ACCUMULATING tip.
//
// A hard tip cannot answer it. If the opening debt were zero the extra dab
// lands at travelled = 0, which is exactly the opening dab's own position, and
// max-blend absorbs it -- identical pixels, mutation invisible. A soft tip
// ACCUMULATES, so a duplicate at the origin shows up as a darker blob at the
// start of every stroke, which is what an owner would actually see.
const softWalk = subSpacingWalk("Soft Brush");
const softOpening = softWalk.before;
const softAfter = softWalk.after;
out.subSpacingSoftTip = {
  gapPx: softWalk.gapPx,
  totalTravelPx: softWalk.totalTravelPx,
  openingDabPixels: softOpening.n,
  coverageUnchanged: softOpening.d === softAfter.d,
  verdict: softOpening.d === softAfter.d
    ? "no duplicate dab at the stroke origin, correct"
    : "an extra dab landed on the opening dab and darkened it",
};

// ---- spacing must follow the dab's OWN pressure ------------------------
//
// The mutation that exposed this gap replaced the interpolated pressure with a
// constant 1.0 when pricing the next gap, and every existing test passed --
// because they all ran with pressure sensitivity OFF, where the substitution
// is a no-op. A guard that cannot be reached by the thing it guards is not a
// guard.
//
// Isolated by CONTINUITY. Paint a horizontal line at a low constant pressure
// with pressure driving size. Correct: the gap shrinks with the dab, and the
// line is solid. Broken: the gap is priced for a full-pressure dab while the
// dabs themselves are small, so the line comes out dotted.
function continuityAtPressure(pressure) {
  reset("Basic Round");
  S.pressureSensitivity = true;
  S.pressureAffects = "size";
  // WIDE spacing on purpose. Basic Round ships a ~5% gap, which is so much
  // smaller than the dab that even a 4x mispricing still overlaps and the line
  // stays solid -- the test had no power to fail. At half a dab-width the
  // error becomes visible as gaps, which is the whole point.
  S.brushDynamics.spacing = 0.5;
  const y = 256;
  C.beginStroke(60, y, pressure);
  for (let i = 1; i <= 40; i++) C.plotTo(60 + i * 10, y, pressure);
  const m = S.stroke.alphaMap;
  // Longest run of empty pixels along the stroke's own centre line.
  let longestGap = 0, gap = 0, painted = 0;
  for (let x = 60; x <= 460; x++) {
    if (m[y * W + x]) { painted++; if (gap > longestGap) longestGap = gap; gap = 0; }
    else gap++;
  }
  if (gap > longestGap) longestGap = gap;
  return { pressure, paintedOnCentreLine: painted, longestGapPx: longestGap };
}
out.pressureSpacing = {
  full: continuityAtPressure(1.0),
  light: continuityAtPressure(0.25),
  note: "a light stroke must still be SOLID -- spacing follows the dab it is "
      + "actually painting, not the one it might have painted at full pressure",
};

// ---- BE5 section 8.2: continuity across the old 0.01 threshold ---------
//
// Every quantity the brief names, at every hardness it names. The point is not
// that any one value is right -- it is that NO step jumps merely because
// hardness crossed 0.01, which is where two separate cliffs used to sit.
function hardnessSweep() {
  const rows = {};
  for (const hard of [0, 0.001, 0.005, 0.009, 0.010, 0.011, 0.1, 0.5, 1.0]) {
    reset("Basic Round");
    S.brushHardness = hard;
    S.brushFlow = 1.0;
    S.brushBuildup = false;
    const frac = C.spacingFraction();
    const gap = C.spacingFor(1.0, C.brushPx(), frac);
    // One dab: core alpha and the edge profile along a radius.
    C.beginStroke(256, 256, 1.0);
    const m1 = S.stroke.alphaMap;
    const core = m1[256 * W + 256];
    const profile = [];
    for (let dx = 0; dx <= 12; dx += 2) profile.push(m1[256 * W + 256 + dx]);
    // A full stroke: emitted coverage over a fixed path.
    reset("Basic Round");
    S.brushHardness = hard; S.brushFlow = 1.0; S.brushBuildup = false;
    C.beginStroke(80, 256, 1.0);
    for (let i = 1; i <= 24; i++) C.plotTo(80 + i * 14, 256, 1.0);
    const m2 = S.stroke.alphaMap;
    let painted = 0, peak = 0;
    for (let i = 0; i < m2.length; i++) if (m2[i]) { painted++; if (m2[i] > peak) peak = m2[i]; }
    rows["h" + hard] = {
      spacingFraction: +frac.toFixed(4),
      gapPx: +gap.toFixed(2),
      coreAlpha: core,
      edgeProfile: profile,
      strokePixels: painted,
      strokePeak: peak,
    };
  }
  return rows;
}
out.hardnessSweep = hardnessSweep();

// Flow must be a per-dab contribution bounded by itself when Buildup is off,
// and must keep climbing when Buildup is on.
function flowSemantics() {
  const run = (flow, buildup, dabs) => {
    reset("Basic Round");
    S.brushHardness = 0; S.brushFlow = flow; S.brushBuildup = buildup;
    C.beginStroke(256, 256, 1.0);
    for (let i = 0; i < dabs; i++) C.plotTo(256 + (i % 2) * 3, 256, 1.0);
    const m = S.stroke.alphaMap;
    let peak = 0; for (let i = 0; i < m.length; i++) if (m[i] > peak) peak = m[i];
    return peak;
  };
  // BE17. THE SAME FIXTURE AT AN OPACITY WHERE BUILDUP CAN BE SEEN.
  //
  // Buildup means the Opacity bound does not apply WITHIN the stroke. At
  // Opacity 100 there is no bound to ignore, so the two settings are the same
  // thing and must measure the same -- which is why the old comparison, run at
  // the preset's Opacity 100, now reads 185 against 185 and fails. That is the
  // control being honest, not the control being dead.
  const runAt = (flow, opacity, buildup, dabs) => {
    reset("Basic Round");
    S.brushHardness = 0; S.brushFlow = flow; S.brushBuildup = buildup;
    S.brushOpacity = opacity;
    C.beginStroke(256, 256, 1.0);
    for (let i = 0; i < dabs; i++) C.plotTo(256 + (i % 2) * 3, 256, 1.0);
    S.stroke.dirty = { x0: 0, y0: 0, x1: W - 1, y1: H - 1 };
    S.stroke._cachedImg = null;
    // ON THE LAYER, because the commit-time bound is half of what Buildup
    // changes and the alpha map cannot see it.
    const img = C.alphaMapToImageData("#ffffff");
    const bound = buildup ? 1 : opacity;
    let peak = 0;
    for (let i = 3; i < img.data.length; i += 4) {
      const v = Math.round(img.data[i] * bound);
      if (v > peak) peak = v;
    }
    return peak;
  };

  // ONE PASS, laid out in space rather than scrubbed in one place: what Flow
  // is defined as depositing. The `run` fixture above overlaps 10 to 200 dabs
  // on one spot, which is several passes and therefore cannot measure it.
  const onePass = (flow) => {
    reset("Basic Round");
    S.brushHardness = 0; S.brushFlow = flow; S.brushBuildup = false;
    S.brushOpacity = 1.0;
    C.beginStroke(120, 256, 1.0);
    for (let i = 1; i <= 40; i++) C.plotTo(120 + i * 6, 256, 1.0);
    const m = S.stroke.alphaMap;
    let peak = 0; for (let i = 0; i < m.length; i++) if (m[i] > peak) peak = m[i];
    return peak;
  };

  return {
    flow40_noBuildup_10: run(0.4, false, 10),
    flow40_noBuildup_200: run(0.4, false, 200),
    flow40_buildup_10: run(0.4, true, 10),
    flow40_buildup_200: run(0.4, true, 200),
    flow100_noBuildup: run(1.0, false, 60),
    flow25_noBuildup: run(0.25, false, 60),
    onePass_flow25: onePass(0.25),
    onePass_flow40: onePass(0.4),
    onePass_flow85: onePass(0.85),
    op50_noBuildup_200: runAt(0.4, 0.5, false, 200),
    op50_buildup_200: runAt(0.4, 0.5, true, 200),
    op50_noBuildup_10: runAt(0.4, 0.5, false, 10),
  };
}
out.flowSemantics = flowSemantics();

// ---- what the owner actually SEES ---------------------------------------
//
// The peak of a Soft Brush stroke is 102 before and after BE5. The complaint
// was never the peak: it was that the stroke read as a flat band of 50%
// opacity rather than as a soft brush.
//
// Forced accumulation with a flow ceiling filled the whole dab radius up to
// 102 and stopped, so the cross-section was a plateau with a cliff at the
// edge. Max-blending a dab whose falloff reaches full strength leaves the
// falloff intact, scaled by flow. Measured as the cross-section PERPENDICULAR
// to a straight stroke -- the profile an owner sees as the stroke's edge.
function strokeCrossSection(flow, buildup) {
  reset("Soft Brush");
  S.brushFlow = flow; S.brushBuildup = buildup;
  const y = 256;
  C.beginStroke(80, y, 1.0);
  for (let i = 1; i <= 24; i++) C.plotTo(80 + i * 14, y, 1.0);
  const m = S.stroke.alphaMap;
  const x = 256;
  const column = [];
  for (let dy = 0; dy <= 16; dy++) column.push(m[(y + dy) * W + x]);
  // How many DISTINCT alpha levels appear down the edge? A plateau with a
  // cliff has very few; a real falloff has many.
  const levels = new Set(column.filter(v => v > 0)).size;
  return { column, distinctLevels: levels, peak: column[0] };
}
out.crossSection = {
  soft_flow40_noBuildup: strokeCrossSection(0.4, false),
  soft_flow40_buildup: strokeCrossSection(0.4, true),
};

// ---- BE7: per-dab direction and anisotropic spacing ---------------------

// 1. THE GATE. A stroke built from sub-2px steps used to update the heading
//    not at all: `if (Math.hypot(dx,dy) > 2)`. Drawn slowly and carefully --
//    which is when a chisel's angle matters most -- the tip kept whatever
//    heading it had before.
function headingTracksSmallSteps() {
  reset("Flat Shader");
  S.brushDynamics.followStroke = true;
  C.beginStroke(100, 256, 1.0);
  // A quarter turn, walked in 1.5px steps: every one below the old gate.
  const R = 90, steps = 160;
  for (let i = 1; i <= steps; i++) {
    const a = (Math.PI / 2) * (i / steps);
    C.plotTo(100 + R * Math.sin(a), 256 - R * (1 - Math.cos(a)), 1.0);
  }
  return {
    finalHeadingDeg: +(C.strokeAngle * 180 / Math.PI).toFixed(1),
    note: "walking a quarter turn in ~1.5px steps; the heading must follow",
  };
}
out.headingTracksSmallSteps = headingTracksSmallSteps();

// 2. THE HEADING FILTER MUST NOT DEPEND ON EVENT RATE. Same arc, same speed,
//    reported at different rates: the tip must end up pointing the same way.
function headingRateInvariance() {
  const walk = (perStep) => {
    reset("Flat Shader");
    S.brushDynamics.followStroke = true;
    C.beginStroke(100, 256, 1.0);
    const R = 90, steps = 20 * perStep;
    for (let i = 1; i <= steps; i++) {
      const a = (Math.PI / 2) * (i / steps);
      C.plotTo(100 + R * Math.sin(a), 256 - R * (1 - Math.cos(a)), 1.0);
    }
    return +(C.strokeAngle * 180 / Math.PI).toFixed(1);
  };
  const at = {};
  for (const n of [1, 2, 5, 12]) at["x" + n] = walk(n);
  const vals = Object.values(at);
  return { at, spreadDeg: +(Math.max(...vals) - Math.min(...vals)).toFixed(2) };
}
out.headingRateInvariance = headingRateInvariance();

// 3. A CHISEL AT SEVERAL TRAVEL ANGLES. Anisotropic spacing exists so a flat
//    tip does not bunch on one heading and gap on another. Measured as the
//    longest empty run along the stroke's own line.
function chiselAtAngles(followStroke) {
  const run = (deg) => {
    reset("Flat Shader");
    // followStroke TRUE aligns the tip with travel, so the tip's extent along
    // travel is always its long axis and the anisotropic gap is a no-op -- a
    // test written that way cannot fail. followStroke FALSE holds the tip at a
    // FIXED angle while the stroke turns, which is exactly when the extent
    // along travel varies and the spacing has to follow it.
    S.brushDynamics.followStroke = followStroke;
    S.brushAngle = 0;
    // WIDE spacing when the tip is held at a fixed angle. At Flat Shader's
    // shipped 0.08 the scalar gap is so much smaller than the dab that it
    // stays continuous even when travelling across the tip's narrow axis --
    // the test could not fail. At 0.45 the difference between "the tip's long
    // axis" and "the tip's extent along travel" is the difference between a
    // solid line and a dotted one.
    if (!followStroke) S.brushDynamics.spacing = 0.45;
    const a = deg * Math.PI / 180;
    const cx = 256, cy = 256, L = 150;
    C.beginStroke(cx - Math.cos(a) * L / 2, cy - Math.sin(a) * L / 2, 1.0);
    for (let i = 1; i <= 30; i++) {
      const t = i / 30;
      C.plotTo(cx + Math.cos(a) * (t - 0.5) * L, cy + Math.sin(a) * (t - 0.5) * L, 1.0);
    }
    const m = S.stroke.alphaMap;
    // Walk the stroke's own line and find the longest gap.
    let longest = 0, gap = 0, hit = 0;
    for (let i = 0; i <= L; i++) {
      const px = Math.round(cx + Math.cos(a) * (i - L / 2));
      const py = Math.round(cy + Math.sin(a) * (i - L / 2));
      if (m[py * W + px]) { hit++; if (gap > longest) longest = gap; gap = 0; }
      else gap++;
    }
    return { deg, longestGapPx: longest, paintedOnLine: hit };
  };
  const rows = {};
  for (const deg of [0, 30, 45, 60, 90, 135]) rows["deg" + deg] = run(deg);
  return rows;
}
out.chiselAtAngles = chiselAtAngles(true);
out.chiselFixedAngle = chiselAtAngles(false);

// The anisotropic gap, measured DIRECTLY rather than inferred from continuity.
//
// Continuity is the wrong instrument for this: it conflates the gap with the
// tip's footprint along travel, and both change together, so a chisel can stay
// solid with the feature disabled and gap with it enabled. Asking spacingFor()
// what gap it returns for a given travel direction is unambiguous.
//
// For a flat tip (aspect 0.3) the gap travelling ALONG the long axis should be
// about 1/0.3 times the gap travelling ACROSS it.
out.anisotropicGap = (function () {
  reset("Flat Shader");
  const frac = C.spacingFraction();
  const px = C.brushPx();
  const along = C.spacingFor(1.0, px, frac, 0, 0);            // travel || long axis
  const across = C.spacingFor(1.0, px, frac, Math.PI / 2, 0); // travel _|_ long axis
  const scalar = C.spacingFor(1.0, px, frac);                 // no direction given
  reset("Basic Round");
  const roundFrac = C.spacingFraction(), roundPx = C.brushPx();
  const roundAlong = C.spacingFor(1.0, roundPx, roundFrac, 0, 0);
  const roundAcross = C.spacingFor(1.0, roundPx, roundFrac, Math.PI / 2, 0);
  return {
    flatAlong: +along.toFixed(3),
    flatAcross: +across.toFixed(3),
    flatScalar: +scalar.toFixed(3),
    ratio: +(along / across).toFixed(2),
    roundAlong: +roundAlong.toFixed(3),
    roundAcross: +roundAcross.toFixed(3),
    roundIsIsotropic: Math.abs(roundAlong - roundAcross) < 1e-6,
  };
})();

// 4. DENSITY must not change meaning when SPACING changes. The skip is per
//    pixel per dab and dabs overlap, so coverage is the UNION over every dab
//    touching a pixel -- and it used to rise to 1.0 as spacing tightened while
//    the Density control sat still.
function densityAgainstSpacing() {
  const run = (spacing) => {
    reset("Basic Round");
    S.brushDensity = 0.35;
    S.brushDynamics.spacing = spacing;
    const y = 256;
    C.beginStroke(80, y, 1.0);
    for (let i = 1; i <= 24; i++) C.plotTo(80 + i * 14, y, 1.0);
    const m = S.stroke.alphaMap;
    const r = Math.round(C.brushPx() / 2);
    let painted = 0, total = 0;
    for (let py = y - r + 2; py <= y + r - 2; py++)
      for (let px = 140; px <= 340; px++) { total++; if (m[py * W + px]) painted++; }
    return +(painted / total).toFixed(3);
  };
  const rows = {};
  for (const s of [0.02, 0.04, 0.08, 0.16, 0.32]) rows["sp" + s] = run(s);
  const cov = Object.values(rows);
  return { rows, spread: +(Math.max(...cov) - Math.min(...cov)).toFixed(3),
           requested: 0.35 };
}
out.densityAgainstSpacing = densityAgainstSpacing();

// ---- BE9: the stabiliser is a property of the PATH -----------------------
//
// The old window was counted in SAMPLES, so it covered a quarter of the arc at
// 240 Hz that it covered at 60. BE3 found this and could not fix it there --
// its own tests call plotTo directly and bypass the stabiliser entirely.
//
// Measured here by feeding the SAME polyline at different sampling densities
// and comparing the stabilised output at matched arc positions.
function stabiliserRateInvariance() {
  const PATH = [[60, 200], [180, 200], [260, 320], [380, 120], [450, 300]];
  const feed = (perSegment, smoothing) => {
    reset("Basic Round");
    S.smoothing = smoothing;
    S.zoom = S.zoom || {}; S.zoom.scale = 1;
    S.stroke.points = [{ x: PATH[0][0], y: PATH[0][1], p: 1 }];
    let out = null;
    for (let v = 1; v < PATH.length; v++) {
      const [ax, ay] = PATH[v - 1], [bx, by] = PATH[v];
      for (let i = 1; i <= perSegment; i++) {
        const t = i / perSegment;
        out = C.stab(ax + (bx - ax) * t, ay + (by - ay) * t, 1);
      }
    }
    return { x: +out.x.toFixed(2), y: +out.y.toFixed(2) };
  };
  const rows = {};
  for (const sm of [0, 3, 6]) {
    const at = {};
    for (const n of [1, 3, 8, 20, 50]) at["x" + n] = feed(n, sm);
    const xs = Object.values(at).map(v => v.x);
    const ys = Object.values(at).map(v => v.y);
    rows["smoothing" + sm] = {
      at,
      spreadPx: +Math.max(Math.max(...xs) - Math.min(...xs),
                          Math.max(...ys) - Math.min(...ys)).toFixed(2),
    };
  }
  return rows;
}
out.stabiliserRateInvariance = stabiliserRateInvariance();

// The window must scale with ZOOM, so the control feels the same at any
// magnification rather than smoothing four times as hard at 4x.
function stabiliserZoomStability() {
  const lagAt = (scale) => {
    reset("Basic Round");
    S.smoothing = 6;
    S.zoom = S.zoom || {}; S.zoom.scale = scale;
    S.stroke.points = [{ x: 100, y: 256, p: 1 }];
    let out = null;
    // A straight run, sampled finely; the lag is the gap between the true
    // position and the stabilised one, expressed in SCREEN pixels.
    for (let i = 1; i <= 200; i++) out = C.stab(100 + i * 1.5, 256, 1);
    return +((400 - out.x) * scale).toFixed(2);
  };
  const rows = {};
  for (const s of [0.25, 1, 4]) rows["zoom" + s] = lagAt(s);
  const v = Object.values(rows);
  return { screenLagPx: rows, spread: +(Math.max(...v) - Math.min(...v)).toFixed(2) };
}
out.stabiliserZoomStability = stabiliserZoomStability();

// Position and pressure are separate concerns with separate windows.
function pressureWindowIsShorter() {
  // A step placed BETWEEN the two windows discriminates them.
  //
  // At Smoothing 6 the position window is 36 document pixels (6 steps x 6px,
  // zoom 1) and the pressure window is 40% of that, 14.4. So a step 20px back
  // is fully outside the pressure window and still inside the position one:
  // pressure must have caught up and position must not have.
  //
  // The first version of this measurement put the step 60px back, where BOTH
  // windows had caught up, and reported a confident 1.0 that would have been
  // 1.0 with the windows identical.
  reset("Basic Round");
  S.smoothing = 6;
  S.zoom = S.zoom || {}; S.zoom.scale = 1;
  const STEP_BACK_PX = 20;
  S.stroke.points = [{ x: 100, y: 256, p: 0 }];
  let out = null;
  const total = 120, stepAt = total - Math.round(STEP_BACK_PX / 2);
  for (let i = 1; i <= total; i++) out = C.stab(100 + i * 2, 256, i > stepAt ? 1 : 0);
  const trueX = 100 + total * 2;
  return {
    stepBackPx: STEP_BACK_PX,
    pressure: +out.p.toFixed(3),
    positionLagPx: +(trueX - out.x).toFixed(2),
    note: "pressure should be near 1 (its window has passed the step) while "
        + "position still lags (its window has not)",
  };
}
out.pressureWindowIsShorter = pressureWindowIsShorter();

// The endpoint catch-up, measured rather than inferred from source order.
//
// A source guard that only checks the CALL comes before the commit is
// satisfied by `if (false) C.finishStroke(...)` -- a mutation proved exactly
// that. What the catch-up does has to be measured.
function endpointCatchUp() {
  const stroke = (useFlush) => {
    reset("Hard Ink");
    S.brushSize = 6;
    const y = 256, stopAt = 420;
    C.beginStroke(80, y, 1.0);
    // Deliberately stop the plotted path SHORT of where the pointer stopped,
    // which is what a lagging stabiliser leaves behind.
    for (let i = 1; i <= 30; i++) C.plotTo(80 + i * 10, y, 1.0);
    const before = (() => {
      const m = S.stroke.alphaMap; let last = -1;
      for (let x = 0; x < W; x++) if (m[y * W + x]) last = x;
      return last;
    })();
    if (useFlush) C.finishStroke(stopAt, y, 1.0);
    const after = (() => {
      const m = S.stroke.alphaMap; let last = -1;
      for (let x = 0; x < W; x++) if (m[y * W + x]) last = x;
      return last;
    })();
    return { stopAt, before, after, shortfallBefore: stopAt - before,
             shortfallAfter: stopAt - after };
  };
  const withFlush = stroke(true);
  const without = stroke(false);
  // And the constraint: a catch-up with nothing to catch up on must do nothing.
  reset("Hard Ink");
  S.brushSize = 6;
  C.beginStroke(200, 256, 1.0);
  for (let i = 1; i <= 10; i++) C.plotTo(200 + i * 10, 256, 1.0);
  const atRest = { x: S.stroke.lx, y: S.stroke.ly };
  const digestOf = () => {
    const m = S.stroke.alphaMap; let d = 0;
    for (let i = 0; i < m.length; i++) if (m[i]) d = (d + m[i] * (i + 1) * 2654435761) % 2147483647;
    return d;
  };
  const beforeNoop = digestOf();
  const returned = C.finishStroke(atRest.x, atRest.y, 1.0);
  const afterNoop = digestOf();
  return {
    withFlush, without,
    noop: { returned, unchanged: beforeNoop === afterNoop },
  };
}
out.endpointCatchUp = endpointCatchUp();

process.stdout.write(JSON.stringify(out, null, 1));
