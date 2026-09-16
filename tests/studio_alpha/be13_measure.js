/**
 * BE13: is the Pixel brush a pixel brush, and does Pixel Perfect remove the
 * corners without disturbing what is under them?
 *
 * THE PATTERNS ARE THE TEST. A corner filter is a claim about specific shapes,
 * so the probes draw specific shapes -- a near-horizontal line that must
 * staircase, a right-angle turn, a 45-degree diagonal -- and report the cells.
 * An aggregate count would pass on a filter that dropped the wrong cells.
 *
 *   node tests/studio_alpha/be13_measure.js <path to canvas-core.js>
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
const W = 256, H = 256;

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
  S.drawing = true;
  C.applyBrushPreset(preset || "Pixel");
  S.smoothing = 0;
  S.brushCurves = [];
  S.brushDynamics = Object.assign({}, S.brushDynamics, {
    sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0,
  });
}

/** Walk a polyline and return the painted cells. */
function draw(points, opts) {
  const o = opts || {};
  reset(o.preset);
  if (o.aliased !== undefined) S.brushAliased = o.aliased;
  if (o.pixelPerfect !== undefined) S.brushPixelPerfect = o.pixelPerfect;
  if (o.size) S.brushSize = o.size;
  if (o.zoom) S.zoom = { scale: o.zoom, ox: 0, oy: 0 };
  C.beginStroke(points[0][0], points[0][1], 1.0);
  for (let i = 1; i < points.length; i++) C.plotTo(points[i][0], points[i][1], 1.0);
  C.finishStroke(points[points.length - 1][0], points[points.length - 1][1], 1.0);
  return S.stroke.alphaMap;
}

function cells(map) {
  const out = [];
  for (let i = 0; i < map.length; i++) {
    if (map[i] > 0) out.push([i % W, (i / W) | 0, map[i]]);
  }
  return out;
}

/** How many cells are painted in one column -- the thickness of a bend. */
function columnRun(map, x) {
  let n = 0;
  for (let y = 0; y < H; y++) if (map[y * W + x] > 0) n++;
  return n;
}

function rowRun(map, y) {
  let n = 0;
  for (let x = 0; x < W; x++) if (map[y * W + x] > 0) n++;
  return n;
}

/** Every distinct alpha value in the mark. Aliased means exactly one. */
function alphas(map) {
  const set = new Set();
  for (let i = 0; i < map.length; i++) if (map[i] > 0) set.add(map[i]);
  return [...set].sort((a, b) => a - b);
}

const R = {};

// ------------------------------------------------ 1. aliased means all or none
{
  // BASIC ROUND, not Pixel. The first version compared two runs of the PIXEL
  // preset, whose hardness is 100 -- an essentially binary dab either way --
  // and reported one alpha level for the antialiased case too. A baseline that
  // cannot exhibit the difference proves nothing about the method.
  const anti = draw([[40, 40], [200, 100]], { preset: "Basic Round", aliased: false, pixelPerfect: false, size: 24 });
  const antiAlphas = alphas(anti);
  const alias = draw([[40, 40], [200, 100]], { preset: "Basic Round", aliased: true, pixelPerfect: false, size: 24 });
  const aliasAlphas = alphas(alias);
  R.aliasedIsAllOrNothing = {
    antialiasedLevels: antiAlphas.length,
    aliasedLevels: aliasAlphas.length,
    aliasedValues: aliasAlphas,
    // Guards the guard: an antialiased dab must have MANY levels, or "one
    // level" would not be evidence of anything.
    antialiasedHasMany: antiAlphas.length > 5,
  };
}

// ---------------------------------- 2. the ordinary brushes are unchanged
{
  const before = draw([[40, 40], [200, 100]], { preset: "Basic Round" });
  let same = true;
  const again = draw([[40, 40], [200, 100]], { preset: "Basic Round" });
  for (let i = 0; i < before.length; i++) if (before[i] !== again[i]) { same = false; break; }
  R.ordinaryBrushesAreUntouched = {
    levels: alphas(before).length,
    aliasedFlag: (() => { reset("Basic Round"); return S.brushAliased; })(),
    pixelPerfectFlag: (() => { reset("Basic Round"); return S.brushPixelPerfect; })(),
    deterministic: same,
  };
}

// -------------------------------------------- 3. the Pixel preset declares both
{
  reset("Pixel");
  const pixel = { aliased: S.brushAliased, pp: S.brushPixelPerfect, active: C.pixelPerfectActive() };
  C.applyBrushPreset("Basic Round");
  const after = { aliased: S.brushAliased, pp: S.brushPixelPerfect };
  R.thePixelPresetDeclaresBoth = {
    pixel, after,
    resets: after.aliased === false && after.pp === false,
  };
}

// ---------------------------------- 4. a near-horizontal line has no double bends
//
// THE DEFECT PIXEL PERFECT REMOVES. A line at a shallow angle staircases, and
// at every step the corner cell makes the line two pixels thick in that
// column. With the filter on, no column may hold more than one cell.
{
  // A SLOW HAND, and the first version of this probe used a long straight
  // segment instead -- which cannot exhibit the defect.
  //
  // `_ppWalk`'s Bresenham emits DIAGONAL steps of its own, so a line drawn as
  // one long segment comes out pixel-perfect before the filter ever sees it,
  // and the filter measured as a no-op. That is good output and a useless
  // control case.
  //
  // The corners appear where they actually appear in a browser: on freehand
  // input, where the pointer is sampled finely enough that x advances in one
  // event and y in the NEXT. Six across, one down, repeated -- which is what
  // a hand drawing a shallow line delivers.
  const slowHand = [[20, 60]];
  for (let step = 0; step < 25; step++) {
    const [lx, ly] = slowHand[slowHand.length - 1];
    for (let k = 1; k <= 6; k++) slowHand.push([lx + k, ly]);
    slowHand.push([lx + 6, ly + 1]);
  }
  // Both runs walk cells; only the FILTER differs. See `pixelWalkActive`.
  const off = draw(slowHand, { aliased: true, pixelPerfect: false });
  const on = draw(slowHand, { aliased: true, pixelPerfect: true });
  const thickOff = [], thickOn = [];
  for (let x = 21; x < 190; x++) {
    if (columnRun(off, x) > 1) thickOff.push(x);
    if (columnRun(on, x) > 1) thickOn.push(x);
  }
  R.aShallowLineHasNoDoubleColumns = {
    offThickColumns: thickOff.length,
    onThickColumns: thickOn.length,
    offCells: cells(off).length,
    onCells: cells(on).length,
    // EXACTLY one cell per doubled column, no more. A filter that dropped
    // every second cell of a straight run would also produce zero doubled
    // columns and fewer cells, and would pass a looser pair of guards.
    removedExactlyTheCorners:
      cells(off).length - cells(on).length === thickOff.length,
  };
}

// ------------------------------------------------- 5. a right-angle turn
//
// The classic case, drawn explicitly so the shape can be read rather than
// counted. Right along a row, then down a column.
{
  const off = draw([[100, 100], [120, 100], [120, 120]], { aliased: true, pixelPerfect: false });
  const on = draw([[100, 100], [120, 100], [120, 120]], { aliased: true, pixelPerfect: true });
  R.aRightAngleTurn = {
    // The corner column is where the two legs meet.
    offCorner: columnRun(off, 120),
    onCorner: columnRun(on, 120),
    offRow: rowRun(off, 100),
    onRow: rowRun(on, 100),
    // A DELIBERATE right angle loses its corner too, and that is the
    // documented method rather than a defect: pixel-perfect turns every L
    // into a diagonal step, which is exactly what makes a hand-drawn curve
    // read as one pixel thick. Aseprite behaves the same way. An owner who
    // wants a square corner turns the mode off, which is what the toggle is
    // for.
    offCells: cells(off).length,
    onCells: cells(on).length,
  };
}

// ---------------------------------------------- 6. a 45-degree diagonal
//
// Every step is diagonal, so there are no corners at all and the filter must
// leave the line exactly as it found it.
{
  const off = draw([[40, 40], [140, 140]], { aliased: true, pixelPerfect: false });
  const on = draw([[40, 40], [140, 140]], { aliased: true, pixelPerfect: true });
  let identical = true;
  for (let i = 0; i < off.length; i++) if (off[i] !== on[i]) { identical = false; break; }
  R.aPerfectDiagonalIsUntouched = {
    offCells: cells(off).length,
    onCells: cells(on).length,
    identical,
  };
}

// ------------------------------------- 7. nothing beneath a dropped cell is lost
//
// "existing artwork beneath tentative pixels is preserved". Painted twice:
// once along a shallow line, then a second stroke crossing it. The crossing
// cells the first stroke painted must survive the second stroke's filtering.
{
  // WITHIN ONE STROKE, because that is the only place the question exists.
  //
  // The first version wrote a bar into the alpha map and then called
  // `beginStroke`, which ALLOCATES A FRESH MAP -- so it measured the
  // allocation, not the filter, and reported 151 of 160 cells "lost". Across
  // strokes the property is trivially true: the alpha map is per-stroke and
  // the layer beneath is never read.
  //
  // The real hazard is a stroke that crosses ITSELF: a cell painted early,
  // then a corner dropped later at the same place. So the map is watched for
  // any cell that ever goes back down.
  reset("Pixel");
  S.brushAliased = true; S.brushPixelPerfect = true;
  const path = [[60, 60], [180, 80], [180, 140], [60, 120], [60, 60], [140, 100]];
  C.beginStroke(path[0][0], path[0][1], 1.0);
  const high = new Uint8Array(W * H);
  let regressions = 0, everPainted = 0;
  const watch = () => {
    for (let i = 0; i < high.length; i++) {
      const v = S.stroke.alphaMap[i];
      if (v < high[i]) regressions++;
      if (v > high[i]) high[i] = v;
    }
  };
  watch();
  for (let i = 1; i < path.length; i++) { C.plotTo(path[i][0], path[i][1], 1.0); watch(); }
  C.finishStroke(path[path.length - 1][0], path[path.length - 1][1], 1.0);
  watch();
  for (let i = 0; i < high.length; i++) if (high[i] > 0) everPainted++;
  R.nothingBeneathIsLost = { underlyingCells: everPainted, lost: regressions };
}

// ------------------------- 6b. an orthogonal step followed by a DIAGONAL one
//
// ADDED AFTER TWO MUTATIONS ESCAPED. Both broke `_ppIsCorner` and both were
// invisible to every probe above, because a pure diagonal is rejected at the
// FIRST leg check and a slow hand only ever produces two orthogonal legs.
//
// The case that discriminates is a vertical step followed by a diagonal one:
// with the second leg's length unchecked, `(abx !== 0) !== (bcx !== 0)` reads
// 0-versus-1 and drops a cell that is not a corner at all.
{
  const zig = [[60, 60]];
  for (let i = 0; i < 20; i++) {
    const [lx, ly] = zig[zig.length - 1];
    zig.push([lx, ly + 1]);          // vertical
    zig.push([lx + 1, ly + 2]);      // diagonal
  }
  const off = draw(zig, { aliased: true, pixelPerfect: false });
  const on = draw(zig, { aliased: true, pixelPerfect: true });
  let identical = true;
  for (let i = 0; i < off.length; i++) if (off[i] !== on[i]) { identical = false; break; }
  R.aDiagonalAfterAnOrthogonalIsNotACorner = {
    offCells: cells(off).length,
    onCells: cells(on).length,
    identical,
  };
}

// -------------------------------- 7b. the cell walk leaves no gaps
//
// ITS OWN FEATURE, and a defect nobody had named. BE3's spacing debt places
// dabs every `spacing` document pixels ALONG THE PATH, and on a diagonal that
// lands at cells which skip -- so a fast diagonal drag with the Pixel brush
// left gaps. The walk belongs to `aliased`, not to Pixel Perfect.
{
  const walked = draw([[40, 40], [200, 130]], { aliased: true, pixelPerfect: false });
  // No column between the ends may be empty.
  let gaps = 0;
  for (let x = 41; x < 200; x++) if (columnRun(walked, x) === 0) gaps++;
  // And the same path with the walk disabled, so the guard has a control.
  const arc = draw([[40, 40], [200, 130]], { aliased: false, pixelPerfect: false });
  let arcGaps = 0;
  for (let x = 41; x < 200; x++) if (columnRun(arc, x) === 0) arcGaps++;
  R.theWalkLeavesNoGaps = { gaps, arcGaps, cells: cells(walked).length };
}

// -------------------------------------------------- 8. zoom changes nothing
{
  const marks = {};
  for (const zoom of [0.25, 1, 4]) {
    const map = draw([[20, 60], [200, 80]], { pixelPerfect: true, zoom });
    let digest = 0;
    for (let i = 0; i < map.length; i++) digest = (digest * 31 + map[i] * (i % 977)) >>> 0;
    marks["zoom" + zoom] = digest;
  }
  R.zoomChangesNothing = {
    at: marks,
    identical: new Set(Object.values(marks)).size === 1,
  };
}

// ------------------------------- 9. it is restricted to the regime it supports
{
  reset("Pixel");
  const atOne = C.pixelPerfectActive();
  S.brushSizeMode = "document_pixels"; S.brushSize = 40;
  const atForty = C.pixelPerfectActive();
  S.brushSize = 1; S.brushAliased = false;
  const withoutAlias = C.pixelPerfectActive();
  S.brushAliased = true; S.tool = "smudge";
  const wrongTool = C.pixelPerfectActive();
  R.restrictedToItsRegime = {
    atOne, atForty, withoutAlias, wrongTool,
    correct: atOne === true && atForty === false
      && withoutAlias === false && wrongTool === false,
  };
}

// ------------------------------------- 10. the last cell of a stroke is painted
//
// The filter holds one cell tentative. Without a flush every stroke would end
// one cell short -- which would look exactly like the filter being too
// aggressive and would be very hard to tell apart from it.
{
  const map = draw([[100, 100], [110, 100]], { pixelPerfect: true });
  const painted = cells(map);
  const maxX = Math.max(...painted.map(c => c[0]));
  R.theLastCellIsPainted = { maxX, reachesTheEnd: maxX >= 110 };
}

// ------------------------ 11. an eraser can use the same aliased method
//
// "Eraser can use the same aliased method" -- and it needs no eraser code,
// because the eraser has always used this same stamp. The two differ at commit
// and nowhere else.
{
  reset("Pixel");
  S.tool = "eraser";
  S.brushAliased = true;
  C.beginStroke(40, 40, 1.0);
  C.plotTo(120, 60, 1.0);
  C.finishStroke(120, 60, 1.0);
  R.theEraserUsesTheSameMethod = {
    levels: alphas(S.stroke.alphaMap).length,
    cells: cells(S.stroke.alphaMap).length,
  };
}

// ------------------------------------- 12. even diameters paint even widths
//
// BE2 left this as an expectedFailure with BE13's name on it: at an integer
// centre a diameter-d dab covers 2*ceil(d/2)-1 cells, so Size 2 painted 1 and
// Size 4 painted 3. An even diameter has to straddle a cell boundary.
{
  const rows = {};
  for (const size of [1, 2, 3, 4, 5, 8]) {
    reset("Pixel");
    S.brushAliased = true;
    S.brushPixelPerfect = false;
    S.brushSizeMode = "document_pixels";
    S.brushSize = size;
    C.beginStroke(128, 128, 1.0);
    // Measured across the WIDEST row of the dab: an even-diameter dab
    // straddles two rows and neither of them alone is its width.
    let widest = 0;
    for (let y = 0; y < H; y++) { const n = rowRun(S.stroke.alphaMap, y); if (n > widest) widest = n; }
    rows["size" + size] = widest;
  }
  R.evenDiametersPaintEvenWidths = {
    at: rows,
    exact: Object.entries(rows).every(([k, v]) => v === Number(k.slice(4))),
  };
}

console.log(JSON.stringify(R, null, 2));
