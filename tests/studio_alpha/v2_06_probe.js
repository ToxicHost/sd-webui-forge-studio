/**
 * V2-06 probe: the standalone kernel end to end, and the execution guard.
 *
 *     node tests/studio_alpha/v2_06_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const V2 = path.resolve(__dirname, "..", "..", "forge_studio", "frontend", "v2");
const MODULES = ["brush-contracts.js", "input.js", "sampler.js", "filters.js",
                 "dynamics.js", "coverage.js", "scratchpad.js"];

function load() {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    vm.createContext(g);
    for (const name of MODULES) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    return g.window;
}

const W = load();
const K = W.StudioBrushScratchpadV2;
const F = W.StudioBrushFiltersV2;
const V = W.StudioBrushCoverageV2;
const D = W.StudioBrushDynamicsV2;

//: A clock the harness drives, so timings are deterministic and a test never
//: depends on how fast this machine happens to be.
let tick = 0;
const clock = () => (tick += 0.25);

function rawAt(x, y, t, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t, pressure: 0.8,
        tiltX: 0, tiltY: 0, twist: 0, pointerType: "pen", buttons: 1,
        isPrimary: true,
    }, over || {});
}

/** A recorded contact as dispatch groups of `size`. */
function recording(vertices, perSegment, size, over) {
    const flat = [];
    let t = 0;
    flat.push(rawAt(vertices[0][0], vertices[0][1], t, over));
    for (let v = 1; v < vertices.length; v++) {
        const [ax, ay] = vertices[v - 1], [bx, by] = vertices[v];
        for (let i = 1; i <= perSegment; i++) {
            const f = i / perSegment;
            t += 8;
            flat.push(rawAt(ax + (bx - ax) * f, ay + (by - ay) * f, t, over));
        }
    }
    const groups = [];
    for (let i = 0; i < flat.length; i += size) groups.push(flat.slice(i, i + size));
    return groups;
}

const CORNER = [[40, 40], [150, 40], [150, 150]];
const SPEC = { width: 200, height: 200, sizePx: 16, hardness: 0.5,
               flow: 0.5, opacity: 1, spacingFraction: 0.15 };

const ok = K.run(recording(CORNER, 20, 4), SPEC, { clock: clock });

// ---- the execution guard --------------------------------------------------

const noSamples = K.run([], SPEC, { clock: clock });
const outside = K.run(recording([[900, 900], [980, 900]], 10, 4), SPEC,
                      { clock: clock });
const noPixels = K.run(recording(CORNER, 20, 4),
                       Object.assign({}, SPEC, { flow: 0 }), { clock: clock });
// A single-point contact still places its opening mark, so it must NOT refuse.
const tap = K.run([[rawAt(100, 100, 0)]], SPEC, { clock: clock });

// ---- the modes reach the kernel -------------------------------------------

const byMode = {};
for (const mode of F.MODES) {
    byMode[mode] = K.run(recording(CORNER, 20, 4),
                         Object.assign({}, SPEC, { smoothingMode: mode }),
                         { clock: clock }).diagnostics;
}

// ---- dispatch grouping is invisible to the result -------------------------

function coverageFor(size) {
    return K.run(recording(CORNER, 20, size), SPEC, { clock: clock })
        .diagnostics.totalCoverage;
}
const groupingTotals = [1, 3, 7, 200].map(coverageFor);

// ---- deterministic replay -------------------------------------------------

const replay = K.replayMatches(recording(CORNER, 20, 4), SPEC, { clock: clock });

// ---- dynamics reach the footprint -----------------------------------------

const sizeRule = D.ruleFromFeel("size", "pressure", "balanced",
                                { min: 0.2, max: 1.0, fallback: 1 });
const withRule = K.run(recording(CORNER, 20, 4, { pressure: 0.3 }),
                       Object.assign({}, SPEC, { rules: [sizeRule] }),
                       { clock: clock }).diagnostics;
const withoutRule = K.run(recording(CORNER, 20, 4, { pressure: 0.3 }), SPEC,
                          { clock: clock }).diagnostics;

// ---- selection and erase reach the merge ----------------------------------

const sel = new Uint8Array(200 * 200);
sel.fill(128);
const selected = K.run(recording(CORNER, 20, 4), SPEC,
                       { clock: clock, selection: sel });

const prePainted = new Uint8Array(200 * 200);
prePainted.fill(255);
const erased = K.run(recording(CORNER, 20, 4),
                     Object.assign({}, SPEC, { erase: true }),
                     { clock: clock, target: prePainted });

function peakOf(bytes) {
    let peak = 0;
    for (let i = 0; i < bytes.length; i++) if (bytes[i] > peak) peak = bytes[i];
    return peak;
}
function minOf(bytes) {
    let low = 255;
    for (let i = 0; i < bytes.length; i++) if (bytes[i] < low) low = bytes[i];
    return low;
}

const SOURCE = fs.readFileSync(path.join(V2, "scratchpad.js"), "utf8");

const report = {
    endToEnd: {
        dispatches: ok.diagnostics.dispatches,
        samples: ok.diagnostics.samples,
        coalescedSamples: ok.diagnostics.coalescedSamples,
        marks: ok.diagnostics.marks,
        contributions: ok.diagnostics.contributions,
        paintedPixels: ok.diagnostics.paintedPixels,
        // U1: the dirty bounds have to survive the kernel's own seam.
        dirty: ok.diagnostics.dirty,
        visitedPixels: ok.diagnostics.visitedPixels,
        documentPixels: SPEC.width * SPEC.height,
        targetPixelsBefore: ok.diagnostics.targetPixelsBefore,
        targetPixelsAfter: ok.diagnostics.targetPixelsAfter,
        renderer: ok.diagnostics.renderer,
        refusal: ok.refusal,
        hasTimings: ok.timings !== null,
        timingStages: ok.timings ? Object.keys(ok.timings).sort() : null,
        totalIsSumOfStages: ok.timings
            ? Math.abs(ok.timings.total
                       - (ok.timings.input + ok.timings.filter
                          + ok.timings.render + ok.timings.merge)) < 1e-9
            : null,
    },

    guard: {
        noSamplesRefusal: noSamples.refusal,
        noSamplesTimings: noSamples.timings,
        outsideRefusal: outside.refusal,
        outsideTimings: outside.timings,
        noPixelsRefusal: noPixels.refusal,
        noPixelsTimings: noPixels.timings,
        // A refused run still reports diagnostics -- the numbers that say WHY.
        noPixelsStillDiagnoses: noPixels.diagnostics.samples > 0,
        // A tap is a real contact and must not be refused.
        tapRefusal: tap.refusal,
        tapMarks: tap.diagnostics.marks,
        tapHasTimings: tap.timings !== null,
    },

    modes: {
        raw: byMode.raw.smoothingMode,
        natural: byMode.natural.smoothingMode,
        stabilized: byMode.stabilized.smoothingMode,
        // Every mode must still paint.
        painted: F.MODES.map(m => byMode[m].paintedPixels),
        coverage: F.MODES.map(m => byMode[m].totalCoverage),
    },

    grouping: {
        totals: groupingTotals,
        allEqual: groupingTotals.every(t => t === groupingTotals[0]),
        nonZero: groupingTotals[0] > 0,
    },

    replay: replay,

    dynamics: {
        withRuleMarks: withRule.marks,
        withoutRuleMarks: withoutRule.marks,
        withRuleApplied: withRule.dynamicsApplied,
        withoutRuleApplied: withoutRule.dynamicsApplied,
        withRuleCoverage: withRule.totalCoverage,
        withoutRuleCoverage: withoutRule.totalCoverage,
    },

    merge: {
        plainPeak: peakOf(ok.target),
        selectedPeak: peakOf(selected.target),
        erasedMin: minOf(erased.target),
        erasedRefusal: erased.refusal,
    },

    model: {
        overlapK: +ok.diagnostics.overlapK.toFixed(6),
        fEff: +ok.diagnostics.fEff.toFixed(6),
        performanceModes: K.PERFORMANCE_MODES.slice(),
    },

    independence: {
        // §4.6: "no dependency on the shipping Canvas compositor".
        modulesLoaded: MODULES.length,
        sourceLength: SOURCE.length,
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
