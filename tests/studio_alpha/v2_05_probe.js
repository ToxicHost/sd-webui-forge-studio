/**
 * V2-05 probe: coverage semantics, Flow versus Opacity, selection at merge.
 *
 *     node tests/studio_alpha/v2_05_probe.js
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
    return { C: g.window.StudioBrushCoverageV2, I: g.window.StudioBrushInputV2,
             S: g.window.StudioBrushSamplerV2 };
}

const { C, I, S } = load();

const W = 200, H = 200;
const toDoc = (x, y) => ({ x: x, y: y });

function rawAt(x, y, t) {
    return { clientX: x, clientY: y, timeStamp: t, pressure: 1,
             tiltX: 0, tiltY: 0, twist: 0, pointerType: "pen", buttons: 1 };
}

/** Fixed vertices, subdivided -- the rule that isolates rate from geometry. */
function walk(vertices, perSegment, hz) {
    const input = new I.StrokeInput();
    const out = [];
    let t = 0;
    out.push(input.normalize(rawAt(vertices[0][0], vertices[0][1], t), toDoc));
    for (let v = 1; v < vertices.length; v++) {
        const [ax, ay] = vertices[v - 1], [bx, by] = vertices[v];
        for (let i = 1; i <= perSegment; i++) {
            const f = i / perSegment;
            t += 1000 / hz;
            out.push(input.normalize(
                rawAt(ax + (bx - ax) * f, ay + (by - ay) * f, t), toDoc));
        }
    }
    return out;
}

const STRAIGHT = [[40, 100], [160, 100]];
const CURVE = (function () {
    const v = [];
    for (let i = 0; i <= 12; i++) {
        const a = (i / 12) * Math.PI;
        v.push([100 + 55 * Math.cos(Math.PI - a), 140 - 45 * Math.sin(a)]);
    }
    return v;
})();
const SHARP = [[50, 60], [140, 60], [140, 150]];

const TIP = { sizePx: 16, hardness: 0.5 };
const SPACING = 0.15;

/** Place marks, then render them with `renderer`. */
function render(vertices, perSegment, opts) {
    const o = opts || {};
    const samples = walk(vertices, perSegment, o.hz || 120);
    const spec = { spacingFraction: o.spacing || SPACING,
                   sizePx: o.sizePx || TIP.sizePx };
    const marks = S.sampleStream(samples, spec).dabs;
    const dep = C.depositionFor({
        hardness: o.hardness === undefined ? TIP.hardness : o.hardness,
        flow: o.flow === undefined ? 1 : o.flow,
        opacity: o.opacity === undefined ? 1 : o.opacity,
        density: o.density === undefined ? 1 : o.density,
        buildup: !!o.buildup,
        // The step the sampler actually used, in tip radii.
        step: (o.spacing || SPACING) * 2,
    });
    const buf = new C.CoverageBuffer(W, H);
    const radius = (o.sizePx || TIP.sizePx) / 2;
    if (o.renderer === C.RENDER_SWEEP) {
        for (let i = 1; i < marks.length; i++) {
            C.sweep(buf, marks[i - 1], marks[i], radius, dep);
        }
    } else {
        for (const m of marks) C.stamp(buf, m, radius, dep);
    }
    return { buffer: buf, marks: marks, dep: dep };
}

function stats(run) {
    return {
        marks: run.marks.length,
        painted: C.paintedPixels(run.buffer),
        total: C.totalCoverage(run.buffer),
    };
}

// ---- reference outputs ---------------------------------------------------

const refStampStraight = stats(render(STRAIGHT, 20));
const refStampCurve = stats(render(CURVE, 4));
const refStampSharp = stats(render(SHARP, 20));
const refSweepStraight = stats(render(STRAIGHT, 20,
    { renderer: C.RENDER_SWEEP, hardness: 1 }));
const refSweepSharp = stats(render(SHARP, 20,
    { renderer: C.RENDER_SWEEP, hardness: 1 }));

// ---- frequency invariance of DARKNESS -------------------------------------

const byRate = {};
for (const [hz, per] of [[30, 5], [60, 10], [120, 20], [240, 40]]) {
    byRate[hz] = stats(render(SHARP, per, { hz: hz, flow: 0.4 }));
}
const rateTotals = Object.keys(byRate).map(k => byRate[k].total);
const rateSpread = (Math.max.apply(null, rateTotals)
                    - Math.min.apply(null, rateTotals))
                   / Math.max(1, Math.max.apply(null, rateTotals));

// ---- Flow versus Opacity --------------------------------------------------
//
// A WORKED AREA is the discriminator. On a stroke that never touches itself,
// Flow and Opacity are the same number by construction -- a guard expecting a
// straight line to separate them would be asserting its own misunderstanding.

function workedArea(flow, opacity, buildup) {
    const input = new I.StrokeInput();
    const samples = [];
    let t = 0;
    for (let pass = 0; pass < 8; pass++) {
        const forward = pass % 2 === 0;
        for (let i = 0; i <= 30; i++) {
            const f = forward ? i / 30 : 1 - i / 30;
            t += 8;
            samples.push(input.normalize(rawAt(60 + f * 80, 100, t), toDoc));
        }
    }
    const marks = S.sampleStream(samples,
        { spacingFraction: SPACING, sizePx: TIP.sizePx }).dabs;
    const dep = C.depositionFor({
        hardness: TIP.hardness, flow: flow, opacity: opacity,
        buildup: !!buildup, step: SPACING * 2,
    });
    const buf = new C.CoverageBuffer(W, H);
    for (const m of marks) C.stamp(buf, m, TIP.sizePx / 2, dep);
    return buf;
}

/** Peak coverage anywhere in the corridor. U1: over the dirty region only. */
function peakOf(buf) {
    const view = buf.readRegion();
    let peak = 0;
    for (let i = 0; i < view.data.length; i++) {
        if (view.data[i] > peak) peak = view.data[i];
    }
    return peak;
}

const flowLow = workedArea(0.35, 1.0, false);
const opacityLow = workedArea(1.0, 0.35, false);

// Merged, because Opacity is applied at MERGE and not in coverage.
function mergedPeak(buf, opacity) {
    const target = new Uint8Array(W * H);
    C.merge(buf, target, { opacity: opacity });
    let peak = 0;
    for (let i = 0; i < target.length; i++) if (target[i] > peak) peak = target[i];
    return peak;
}

const flowLowMerged = mergedPeak(flowLow, 1.0);
const opacityLowMerged = mergedPeak(opacityLow, 0.35);

// One pass over the same corridor, for the "a single pass cannot tell them
// apart" half.
function onePass(flow, opacity) {
    const input = new I.StrokeInput();
    const samples = [];
    for (let i = 0; i <= 30; i++) {
        samples.push(input.normalize(rawAt(60 + (i / 30) * 80, 100, i * 8), toDoc));
    }
    const marks = S.sampleStream(samples,
        { spacingFraction: SPACING, sizePx: TIP.sizePx }).dabs;
    const dep = C.depositionFor({ hardness: TIP.hardness, flow: flow,
                                  opacity: opacity, step: SPACING * 2 });
    const buf = new C.CoverageBuffer(W, H);
    for (const m of marks) C.stamp(buf, m, TIP.sizePx / 2, dep);
    return mergedPeak(buf, opacity);
}

// ---- selection at merge ---------------------------------------------------

function selectionRun(perContribution) {
    const buf = workedArea(0.5, 1.0, false);
    const sel = new Uint8Array(W * H);
    sel.fill(128);                       // a uniform 50% selection
    const target = new Uint8Array(W * H);
    if (perContribution) {
        // What applying it per dab would do, modelled at the merge boundary:
        // N overlapping contributions each scaled by 0.5.
        // U1: region-local extraction, mapped back to document indices for the
        // target. A zero-coverage pixel contributed nothing under the old
        // whole-document loop either -- `a` was 0 -- so this is the same result.
        const view = buf.readRegion();
        for (let vy = 0; vy < view.height; vy++) {
            for (let vx = 0; vx < view.width; vx++) {
                const c = view.data[vy * view.width + vx];
                const i = (view.y0 + vy) * W + (view.x0 + vx);
                const passes = Math.max(1, Math.round(c / 32));
                let a = (c / 255) * Math.pow(0.5, passes);
                const prev = target[i] / 255;
                target[i] = Math.round((prev + (1 - prev) * a) * 255);
            }
        }
    } else {
        C.merge(buf, target, { opacity: 1, selection: sel });
    }
    let peak = 0, sum = 0;
    for (let i = 0; i < target.length; i++) {
        if (target[i] > peak) peak = target[i];
        sum += target[i];
    }
    return { peak: peak, sum: sum };
}

const selOnce = selectionRun(false);
const selPerDab = selectionRun(true);

// ---- the peak floor -------------------------------------------------------
//
// A hard tip's SINGLE-PASS cross-section must be unchanged by accumulation.

function crossSection(hardness, flow) {
    const dep = C.depositionFor({ hardness: hardness, flow: flow,
                                  step: SPACING * 2 });
    const buf = new C.CoverageBuffer(W, H);
    const input = new I.StrokeInput();
    const samples = [];
    for (let i = 0; i <= 40; i++) {
        samples.push(input.normalize(rawAt(40 + i * 3, 100, i * 8), toDoc));
    }
    const marks = S.sampleStream(samples,
        { spacingFraction: SPACING, sizePx: TIP.sizePx }).dabs;
    for (const m of marks) C.stamp(buf, m, TIP.sizePx / 2, dep);
    const out = [];
    for (let dy = -3; dy <= 3; dy++) out.push(buf.at(100, 100 + dy));
    return out;
}

// ---- erase is the same coverage, a different operation --------------------

const eraseBuf = workedArea(1.0, 1.0, false);
const painted = new Uint8Array(W * H);
painted.fill(255);
C.merge(eraseBuf, painted, { opacity: 1, erase: true });
let erasedToZero = 0;
for (let i = 0; i < painted.length; i++) if (painted[i] === 0) erasedToZero += 1;

// ---- determinism ----------------------------------------------------------

const detA = C.totalCoverage(render(SHARP, 20).buffer);
const detB = C.totalCoverage(render(SHARP, 20).buffer);

const SOURCE = fs.readFileSync(path.join(V2, "coverage.js"), "utf8");

const report = {
    reference: {
        stampStraight: refStampStraight,
        stampCurve: refStampCurve,
        stampSharp: refStampSharp,
        sweepStraight: refSweepStraight,
        sweepSharp: refSweepSharp,
    },

    renderer: {
        hardRoundSweeps: C.rendererFor({ hardness: 1 }),
        softStamps: C.rendererFor({ hardness: 0.5 }),
        texturedStamps: C.rendererFor({ hardness: 1, textured: true }),
        scatterStamps: C.rendererFor({ hardness: 1, scatter: 0.5 }),
        anisotropicStamps: C.rendererFor({ hardness: 1, ratio: 0.4 }),
    },

    frequency: {
        totals: rateTotals,
        spread: +rateSpread.toFixed(6),
        painted: Object.keys(byRate).map(k => byRate[k].painted),
    },

    flowVsOpacity: {
        // Coverage is INTRINSIC: Opacity is not in it at all.
        flowLowCoverage: peakOf(flowLow),
        opacityLowCoverage: peakOf(opacityLow),
        // After merge, where Opacity is applied once.
        flowLowMerged: flowLowMerged,
        opacityLowMerged: opacityLowMerged,
        workedAreaSeparates: flowLowMerged !== opacityLowMerged,
        // A single pass CANNOT tell them apart, and must not.
        onePassFlowLow: onePass(0.35, 1.0),
        onePassOpacityLow: onePass(1.0, 0.35),
    },

    selection: {
        onceAtMergePeak: selOnce.peak,
        perContributionPeak: selPerDab.peak,
        onceAtMergeSum: selOnce.sum,
        perContributionSum: selPerDab.sum,
    },

    peakFloor: {
        hard: crossSection(1.0, 0.5),
        soft: crossSection(0.0, 0.5),
    },

    erase: {
        erasedToZero: erasedToZero,
        painted: C.paintedPixels(eraseBuf),
    },

    determinism: {
        runA: detA,
        runB: detB,
        identical: detA === detB,
        // The §13 "no ambient randomness" source check is NOT here: the module
        // names `Math.random` in a comment saying it never calls one, so a text
        // scan reports a hit. Eighth time in this repository. The Python suite
        // runs it through `_js_source.code_only`.
    },

    model: {
        profileMeanFlat: C.profileMean(1),
        profileMeanSoft: C.profileMean(0),
        noAccumulateAbove: C.NO_ACCUMULATE_ABOVE,
        // Flow 100 short-circuits; Flow 35 accumulates.
        flow100Accumulates: C.depositionFor({ flow: 1, step: 0.3 }).accumulating,
        flow35Accumulates: C.depositionFor({ flow: 0.35, step: 0.3 }).accumulating,
        overlapKAtTightSpacing:
            +C.depositionFor({ flow: 0.35, hardness: 0.5, step: 0.3 })
                .overlapK.toFixed(4),
        fEffAtTightSpacing:
            +C.depositionFor({ flow: 0.35, hardness: 0.5, step: 0.3 })
                .fEff.toFixed(6),
        buildupChangesTarget: [
            C.depositionFor({ flow: 0.5, opacity: 0.5, buildup: false }).target,
            C.depositionFor({ flow: 0.5, opacity: 0.5, buildup: true }).target,
        ],
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
