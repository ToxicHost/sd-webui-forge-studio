/**
 * U1 probe: bounded coverage. Dirty regions, bounded reads, and the
 * instrumentation that proves the algorithm is bounded rather than merely fast.
 *
 *     node tests/studio_alpha/v2_u1_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const V2 = path.resolve(__dirname, "..", "..", "forge_studio", "frontend", "v2");

function load() {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    vm.createContext(g);
    for (const name of ["brush-contracts.js", "input.js", "sampler.js",
                        "filters.js", "dynamics.js", "coverage.js"]) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    return g.window.StudioBrushCoverageV2;
}

const C = load();

const SOFT = C.depositionFor({ hardness: 0.2, flow: 1, opacity: 1, step: 0.3 });
const HARD = C.depositionFor({ hardness: 1.0, flow: 1, opacity: 1, step: 0.3 });

/** The bounding box of every non-zero pixel, found the slow honest way. */
function trueBbox(buf) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let y = 0; y < buf.height; y++) {
        for (let x = 0; x < buf.width; x++) {
            if (buf.at(x, y) > 0) {
                if (x < x0) x0 = x;
                if (y < y0) y0 = y;
                if (x > x1) x1 = x;
                if (y > y1) y1 = y;
            }
        }
    }
    if (x1 < x0) return { x0: 0, y0: 0, x1: 0, y1: 0, empty: true };
    return { x0: x0, y0: y0, x1: x1 + 1, y1: y1 + 1, empty: false };
}

function sameBox(a, b) {
    if (a.empty || b.empty) return !!a.empty === !!b.empty;
    return a.x0 === b.x0 && a.y0 === b.y0 && a.x1 === b.x1 && a.y1 === b.y1;
}

// ── 1. A one-pixel tap is exactly 1x1, and empty is empty ────────────────────

const tapBuf = new C.CoverageBuffer(64, 64);
C.stamp(tapBuf, { x: 32.5, y: 32.5 }, 0.5, HARD);
const tap = tapBuf.dirty.toJSON();

// Empty must be observably empty, NOT a 1x1 at the origin.
const emptyBuf = new C.CoverageBuffer(64, 64);
const emptyRegion = emptyBuf.dirty.toJSON();

// ── 2. Bounds equal the true non-zero bbox, stamp and sweep ──────────────────

const stampBuf = new C.CoverageBuffer(128, 128);
C.stamp(stampBuf, { x: 60.3, y: 70.7 }, 9, SOFT);
const stampAgrees = sameBox(stampBuf.dirty.toJSON(), trueBbox(stampBuf));

const sweepBuf = new C.CoverageBuffer(128, 128);
C.sweep(sweepBuf, { x: 20, y: 30 }, { x: 90, y: 100 }, 6, HARD);
const sweepAgrees = sameBox(sweepBuf.dirty.toJSON(), trueBbox(sweepBuf));

const softSweepBuf = new C.CoverageBuffer(128, 128);
C.sweep(softSweepBuf, { x: 15.4, y: 15.4 }, { x: 40.9, y: 88.2 }, 7, SOFT);
const softSweepAgrees = sameBox(softSweepBuf.dirty.toJSON(),
                                trueBbox(softSweepBuf));

// ── 3. Every document edge, and off-document entirely ────────────────────────

const EDGE = 40;
function edgeCase(x, y) {
    const b = new C.CoverageBuffer(EDGE, EDGE);
    C.stamp(b, { x: x, y: y }, 5, SOFT);
    return {
        dirty: b.dirty.toJSON(),
        agrees: sameBox(b.dirty.toJSON(), trueBbox(b)),
        withinDoc: b.dirty.x0 >= 0 && b.dirty.y0 >= 0
            && b.dirty.x1 <= EDGE && b.dirty.y1 <= EDGE,
    };
}
const edges = {
    left: edgeCase(0.5, 20),
    right: edgeCase(EDGE - 0.5, 20),
    top: edgeCase(20, 0.5),
    bottom: edgeCase(20, EDGE - 0.5),
    corner: edgeCase(0.5, 0.5),
};

// Wholly outside: negative and beyond, both must be empty and allocate nothing
// proportional to the document.
const outsideBuf = new C.CoverageBuffer(2048, 2048);
C.stamp(outsideBuf, { x: -500, y: -500 }, 8, SOFT);
C.stamp(outsideBuf, { x: 5000, y: 5000 }, 8, SOFT);
const outside = {
    dirty: outsideBuf.dirty.toJSON(),
    contributions: outsideBuf.contributions,
    merged: (function () {
        const t = new Uint8Array(2048 * 2048);
        C.merge(outsideBuf, t, {});
        let n = 0;
        for (let i = 0; i < t.length; i++) if (t[i]) n += 1;
        return n;
    })(),
    stats: outsideBuf.stats(),
    painted: C.paintedPixels(outsideBuf),
    total: C.totalCoverage(outsideBuf),
};

// Partially outside — the visible part is dirty, and only the visible part.
const partialBuf = new C.CoverageBuffer(64, 64);
C.stamp(partialBuf, { x: -3, y: 32 }, 10, SOFT);
const partial = {
    dirty: partialBuf.dirty.toJSON(),
    agrees: sameBox(partialBuf.dirty.toJSON(), trueBbox(partialBuf)),
};

// ── 4. A zero-coverage write dirties nothing ─────────────────────────────────

const ZERO_FLOW = C.depositionFor({ hardness: 0.5, flow: 0, opacity: 1,
                                    step: 0.3 });
const zeroBuf = new C.CoverageBuffer(64, 64);
const zeroTouched = C.stamp(zeroBuf, { x: 32, y: 32 }, 8, ZERO_FLOW);
const zeroFlow = {
    touched: zeroTouched,
    contributions: zeroBuf.contributions,
    dirty: zeroBuf.dirty.toJSON(),
    painted: C.paintedPixels(zeroBuf),
};

// ── 5. Disconnected islands union into one region that covers both ───────────

const islandBuf = new C.CoverageBuffer(128, 128);
C.stamp(islandBuf, { x: 15, y: 15 }, 5, HARD);
C.stamp(islandBuf, { x: 110, y: 100 }, 5, HARD);
const islands = {
    dirty: islandBuf.dirty.toJSON(),
    agrees: sameBox(islandBuf.dirty.toJSON(), trueBbox(islandBuf)),
};

// ── 6. Bounded work does not scale with document area ────────────────────────
//
// The SAME stroke geometry on three documents. Visited pixels and extracted
// bytes must be identical; only the base buffer allocation may differ, because
// a full-document coverage surface is inherent to the design and is what
// Legacy's alphaMap costs too.

function sizedRun(w, h) {
    const b = new C.CoverageBuffer(w, h);
    C.sweep(b, { x: 100, y: 100 }, { x: 160, y: 140 }, 8, SOFT);
    const target = new Uint8Array(w * h);
    C.merge(b, target, {});
    const painted = C.paintedPixels(b);
    const total = C.totalCoverage(b);
    const view = b.readRegion();
    return {
        document: w + "x" + h,
        megapixels: +(w * h / 1e6).toFixed(1),
        dirty: b.dirty.toJSON(),
        painted: painted,
        total: total,
        extractedPixels: view.width * view.height,
        stats: b.stats(),
    };
}
const sizes = [sizedRun(1024, 1024), sizedRun(4096, 4096), sizedRun(6000, 4000)];

// ── 7. Metrics do not each materialise a full copy ───────────────────────────
//
// Three metric calls plus a merge. Under the old API each of the four called
// read(), allocating a document-sized array every time. Extractions must stay
// at exactly the number of readRegion calls -- zero for the metrics.

const metricBuf = new C.CoverageBuffer(1024, 1024);
C.stamp(metricBuf, { x: 500, y: 500 }, 10, SOFT);
const beforeMetrics = metricBuf.stats();
C.paintedPixels(metricBuf);
C.totalCoverage(metricBuf);
C.merge(metricBuf, new Uint8Array(1024 * 1024), {});
const afterMetrics = metricBuf.stats();
const metrics = {
    extractionsBefore: beforeMetrics.extractions,
    extractionsAfter: afterMetrics.extractions,
    scratchBytes: afterMetrics.scratchBytes,
    visitedDelta: afterMetrics.visitedPixels - beforeMetrics.visitedPixels,
    dirtyArea: metricBuf.dirty.area(),
};

// ── 8. Bounded merge is byte-identical to an unbounded one ───────────────────

const mergeBuf = new C.CoverageBuffer(256, 256);
C.stamp(mergeBuf, { x: 100, y: 120 }, 12, SOFT);
C.stamp(mergeBuf, { x: 140, y: 130 }, 9, SOFT);

const boundedTarget = new Uint8Array(256 * 256);
C.merge(mergeBuf, boundedTarget, { opacity: 0.7 });

// The whole document, forced, as the oracle.
const wholeTarget = new Uint8Array(256 * 256);
C.merge(mergeBuf, wholeTarget, {
    opacity: 0.7,
    region: new C.DirtyRegion(0, 0, 256, 256),
});

let mergeDiffers = 0;
for (let i = 0; i < boundedTarget.length; i++) {
    if (boundedTarget[i] !== wholeTarget[i]) mergeDiffers += 1;
}

// With a selection too, because selection is sampled document-indexed.
const sel = new Uint8Array(256 * 256);
sel.fill(96);
const selBounded = new Uint8Array(256 * 256);
const selWhole = new Uint8Array(256 * 256);
C.merge(mergeBuf, selBounded, { selection: sel });
C.merge(mergeBuf, selWhole, {
    selection: sel, region: new C.DirtyRegion(0, 0, 256, 256),
});
let selDiffers = 0;
for (let i = 0; i < selBounded.length; i++) {
    if (selBounded[i] !== selWhole[i]) selDiffers += 1;
}

// ── 9. Erase reports the same footprint as paint ─────────────────────────────

function footprint(erase) {
    const b = new C.CoverageBuffer(128, 128);
    C.stamp(b, { x: 64.5, y: 64.5 }, 11, SOFT);
    const t = new Uint8Array(128 * 128);
    t.fill(255);
    C.merge(b, t, { erase: erase });
    return b.dirty.toJSON();
}
const eraseFootprint = {
    paint: footprint(false),
    erase: footprint(true),
};

// ── 10. Clear cannot leak the previous bounds ────────────────────────────────

const reuseBuf = new C.CoverageBuffer(128, 128);
C.stamp(reuseBuf, { x: 20, y: 20 }, 6, HARD);
const beforeClear = reuseBuf.dirty.toJSON();
reuseBuf.clear();
const afterClear = reuseBuf.dirty.toJSON();
const afterClearPainted = C.paintedPixels(reuseBuf, new C.DirtyRegion(0, 0, 128, 128));
C.stamp(reuseBuf, { x: 100, y: 100 }, 6, HARD);
const afterReuse = reuseBuf.dirty.toJSON();
const reuseAgrees = sameBox(afterReuse, trueBbox(reuseBuf));

// ── 11. Replay and regrouping give identical bounds ──────────────────────────

function strokeRun(groups) {
    const b = new C.CoverageBuffer(256, 256);
    for (const g of groups) {
        for (const m of g) C.stamp(b, m, 7, SOFT);
    }
    return b;
}
const MARKS = [];
for (let i = 0; i < 24; i++) {
    MARKS.push({ x: 40 + i * 6.3, y: 60 + Math.sin(i / 3) * 25 });
}
function regroup(n) {
    const out = [];
    for (let i = 0; i < MARKS.length; i += n) out.push(MARKS.slice(i, i + n));
    return out;
}
const runA = strokeRun(regroup(1));
const runB = strokeRun(regroup(5));
const runC = strokeRun([MARKS]);
const viewA = runA.readRegion(), viewB = runB.readRegion();
let replayDiffers = 0;
if (viewA.data.length !== viewB.data.length) replayDiffers = -1;
else {
    for (let i = 0; i < viewA.data.length; i++) {
        if (viewA.data[i] !== viewB.data[i]) replayDiffers += 1;
    }
}
const regrouping = {
    boundsEqual: sameBox(runA.dirty.toJSON(), runB.dirty.toJSON())
        && sameBox(runB.dirty.toJSON(), runC.dirty.toJSON()),
    pixelDiffs: replayDiffers,
    totalA: C.totalCoverage(runA),
    totalB: C.totalCoverage(runB),
    totalC: C.totalCoverage(runC),
};

// ── 12. The extraction is owned and reused ───────────────────────────────────

const ownedBuf = new C.CoverageBuffer(128, 128);
C.stamp(ownedBuf, { x: 40, y: 40 }, 8, SOFT);
const firstView = ownedBuf.readRegion();
const firstByte = firstView.data[Math.floor(firstView.data.length / 2)];
const growthsAfterFirst = ownedBuf.stats().growths;
const secondView = ownedBuf.readRegion();
const growthsAfterSecond = ownedBuf.stats().growths;
const owned = {
    sameBacking: firstView.data.buffer === secondView.data.buffer,
    grewOnce: growthsAfterFirst === 1 && growthsAfterSecond === 1,
    // Mutating the extraction must not corrupt the coverage it came from.
    mutationIsolated: (function () {
        const before = ownedBuf.at(40, 40);
        secondView.data[0] = 7;
        return ownedBuf.at(40, 40) === before;
    })(),
    firstByte: firstByte,
};

// ── 13. Region arithmetic ────────────────────────────────────────────────────

const regionMath = (function () {
    const a = new C.DirtyRegion(10, 10, 20, 20);
    const b = new C.DirtyRegion(30, 5, 40, 12);
    const u = a.clone().unionWith(b);
    const clipped = new C.DirtyRegion(-5, -5, 15, 15).clipTo(10, 10);
    const clippedAway = new C.DirtyRegion(100, 100, 110, 110).clipTo(10, 10);
    const emptyExpand = new C.DirtyRegion(5, 5, 5, 5);
    return {
        union: u.toJSON(),
        halfOpenWidth: a.width(),
        clipped: clipped.toJSON(),
        clippedAway: clippedAway.toJSON(),
        emptyIsEmpty: emptyExpand.isEmpty(),
        emptyHasZeroArea: emptyExpand.area() === 0,
        expandingByEmptyIsNoop: (function () {
            const r = new C.DirtyRegion(1, 2, 3, 4);
            r.expand(9, 9, 9, 9);
            return r.x0 === 1 && r.y0 === 2 && r.x1 === 3 && r.y1 === 4;
        })(),
        regionForIsACopy: (function () {
            const buf = new C.CoverageBuffer(32, 32);
            C.stamp(buf, { x: 16, y: 16 }, 4, HARD);
            const got = buf.regionFor();
            got.x0 = 999;
            return buf.dirty.x0 !== 999;
        })(),
    };
})();

// ── 14. Flow 100 short circuit still bounds correctly ────────────────────────

const FLOW100 = C.depositionFor({ hardness: 0.5, flow: 1, opacity: 1,
                                  step: 0.3 });
const flow100Buf = new C.CoverageBuffer(128, 128);
C.stamp(flow100Buf, { x: 64, y: 64 }, 10, FLOW100);
const flow100 = {
    accumulating: FLOW100.accumulating,
    agrees: sameBox(flow100Buf.dirty.toJSON(), trueBbox(flow100Buf)),
    dirty: flow100Buf.dirty.toJSON(),
};

process.stdout.write(JSON.stringify({
    apiVersion: C.COVERAGE_API_VERSION,
    readRemoved: typeof C.CoverageBuffer.prototype.read !== "function",
    tap: tap,
    emptyRegion: emptyRegion,
    stampAgrees: stampAgrees,
    sweepAgrees: sweepAgrees,
    softSweepAgrees: softSweepAgrees,
    edges: edges,
    outside: outside,
    partial: partial,
    zeroFlow: zeroFlow,
    islands: islands,
    sizes: sizes,
    metrics: metrics,
    mergeDiffers: mergeDiffers,
    selDiffers: selDiffers,
    eraseFootprint: eraseFootprint,
    reuse: {
        beforeClear: beforeClear, afterClear: afterClear,
        afterClearPainted: afterClearPainted,
        afterReuse: afterReuse, agrees: reuseAgrees,
    },
    regrouping: regrouping,
    owned: owned,
    regionMath: regionMath,
    flow100: flow100,
}, null, 1));
