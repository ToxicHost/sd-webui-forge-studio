/**
 * U3-R2F F2 probe — continuous flow across swept segments.
 *
 * THE DEFECT AS STATED, AND WHAT IT ACTUALLY WAS. §3.3 says "each swept segment
 * receives one deposition value, so a pressure ramp becomes a staircase". That
 * staircase is real and F2 removes it -- and removing it alone moved the
 * measured banding from 3.79% to 3.84%, which is to say nothing at all.
 *
 * The cause was in `depositionFor`. It divides the stroke's flow by `overlapK`,
 * the EXPECTED number of contributions covering a pixel, but the actual number
 * is an integer and `2r/gap` is not. At radius 27 and an 8.1 px gap the count
 * alternates between 7 and 8 against an expectation of 6.67, and in the
 * accumulating branch one extra contribution is one extra multiplication.
 *
 * Three predictions distinguished that from the staircase, and all three held:
 *
 *   1. it must be the BRANCH, not the mode -- at flow 1 the accumulator does
 *      not run, and banding was 0%; at 0.99 it was 0.47%, rising to 6.64% at
 *      flow 0.3;
 *   2. the ripple period must FOLLOW the mark gap -- gap 4 -> period 4, 6 -> 6,
 *      8.1 -> 8, 12 -> 12, 16 -> 16;
 *   3. deposition must therefore depend on how the path was CHOPPED -- the same
 *      geometric stroke at constant flow 0.5 read mean alpha 127.5 at a 4 px
 *      gap and 145.5 at 16 px.
 *
 * (3) is the sample-density dependency §14 names, and §14 authorises the repair
 * by name: "normalise deposition by geometric distance/arc length rather than
 * by the number of browser events".
 *
 * So F2 is two changes: the flow gradient §14 asks for, and arc-length
 * deposition without which the gradient is invisible.
 *
 *     node tests/studio_alpha/u3r2f_flow_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const V2 = path.resolve(__dirname, "..", "..", "forge_studio", "frontend", "v2");
const MODULES = ["brush-contracts.js", "input.js", "sampler.js", "filters.js",
                 "dynamics.js", "coverage.js", "canvas-adapter.js"];

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
const A = W.StudioBrushV2Adapter;
const K = W.StudioBrushCoverageV2;

const DOC_W = 900, DOC_H = 320, MID_Y = 160;
const SIZE_PX = 54;
const X0 = 100, X1 = 800;

function makeState(o) {
    const layers = [];
    for (let i = 0; i < 2; i++) {
        layers.push({ type: "paint", canvas: { width: DOC_W, height: DOC_H },
                      ctx: {}, visible: true, locked: false, opacity: 1 });
    }
    return {
        W: DOC_W, H: DOC_H, layers: layers, activeLayerIdx: 1,
        tool: "brush", editingMask: false, regionMode: false,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSize: 18, brushOpacity: 1,
        brushHardness: o.hardness === undefined ? 0.85 : o.hardness,
        brushFlow: o.flow === undefined ? 1 : o.flow,
        brushBuildup: !!o.buildup, brushSpacing: 0.15,
        pressureSensitivity: (o.pressureAffects || "none") !== "none",
        pressureAffects: o.pressureAffects || "none",
        stroke: {
            alphaMap: new Uint8Array(DOC_W * DOC_H),
            dirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
            frameDirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
        },
    };
}

const CORE = { brushPx: () => SIZE_PX };
const toDoc = (x, y) => ({ x: x, y: y });
const ev = (x, y, t, p) => ({ clientX: x, clientY: y, timeStamp: t, pressure: p,
                              pointerType: "pen", tiltX: 0, tiltY: 0, twist: 0,
                              buttons: 1, isPrimary: true });

const PROFILES = {
    flat: () => 0.8,
    low: () => 0.2,
    high: () => 0.95,
    ramp: (t) => 0.08 + 0.9 * t,
    rampDown: (t) => 0.98 - 0.9 * t,
    hill: (t) => 0.08 + 0.9 * Math.sin(t * Math.PI),
    step: (t) => (t < 0.5 ? 0.15 : 0.9),
};

/** The §15 path shapes, as functions of t in [0,1]. */
const PATHS = {
    straight: (t) => ({ x: X0 + (X1 - X0) * t, y: MID_Y }),
    diagonal: (t) => ({ x: X0 + 400 * t, y: MID_Y - 100 + 200 * t }),
    scurve: (t) => ({ x: X0 + 600 * t, y: MID_Y + 70 * Math.sin(t * 2 * Math.PI) }),
    circle: (t) => ({ x: 450 + 90 * Math.cos(t * 2 * Math.PI),
                      y: MID_Y + 90 * Math.sin(t * 2 * Math.PI) }),
    zigzag: (t) => ({ x: X0 + 600 * t,
                      y: MID_Y + (Math.floor(t * 6) % 2 ? 55 : -55) }),
    figureEight: (t) => ({ x: 450 + 110 * Math.sin(t * 2 * Math.PI),
                           y: MID_Y + 70 * Math.sin(t * 4 * Math.PI) }),
};

/**
 * One contact, through the real adapter, with the real sweep instrumented.
 *
 * The instrumentation records what each segment was ACTUALLY given -- both
 * depositions and both endpoints -- which is how the gradient's presence can be
 * asserted as behaviour rather than as spelling.
 */
function stroke(o) {
    const opts = o || {};
    const calls = [];
    const realSweep = K.sweep;
    const realStamp = K.stamp;
    K.sweep = function (buffer, from, to, radiusPx, dep, depTo) {
        calls.push({ kind: "sweep", fromX: from.x, fromY: from.y,
                     toX: to.x, toY: to.y, radius: radiusPx,
                     target: dep.target,
                     targetTo: depTo ? depTo.target : null,
                     gradient: !!depTo && depTo !== dep
                               && depTo.target !== dep.target });
        return realSweep.call(K, buffer, from, to, radiusPx, dep, depTo);
    };
    K.stamp = function (buffer, mark, radiusPx, dep) {
        calls.push({ kind: "stamp", fromX: mark.x, fromY: mark.y,
                     radius: radiusPx, target: dep.target, gradient: false });
        return realStamp.call(K, buffer, mark, radiusPx, dep);
    };

    A._setEnabled(true);
    const S = makeState(opts);
    const pathFn = PATHS[opts.path || "straight"];
    const profileFn = PROFILES[opts.profile || "ramp"];
    const n = opts.samples === undefined ? 60 : opts.samples;
    const pts = [];
    for (let i = 0; i <= n; i++) {
        const t = i / n;
        const p = pathFn(t);
        pts.push([p.x, p.y, profileFn(t)]);
    }

    A.begin(S, CORE, ev(pts[0][0], pts[0][1], 0, pts[0][2]), toDoc);
    const per = opts.perEvent || 1;
    let time = 16;
    for (let i = 1; i < pts.length; i += per) {
        const group = pts.slice(i, Math.min(pts.length, i + per));
        if (group.length === 1) {
            A.addFromEvent(S, ev(group[0][0], group[0][1], time, group[0][2]),
                           toDoc);
        } else {
            const last = group[group.length - 1];
            const e = ev(last[0], last[1], time, last[2]);
            e.getCoalescedEvents = () => group.map(
                (q, k) => ev(q[0], q[1],
                             time - (group.length - 1 - k) * 4, q[2]));
            A.addFromEvent(S, e, toDoc);
        }
        time += 16;
    }
    const last = pts[pts.length - 1];
    const summary = A.finish(S, ev(last[0], last[1], time, last[2]), toDoc);
    A._setEnabled(false);
    K.sweep = realSweep;
    K.stamp = realStamp;

    return { map: S.stroke.alphaMap, calls: calls,
             marks: summary ? summary.marks : 0,
             samples: summary ? summary.samples : 0,
             finalPressure: last[2],
             dirty: Object.assign({}, S.stroke.dirty) };
}

// ── statistics ─────────────────────────────────────────────────────────────

/**
 * Local QUADRATIC fit rather than a moving average.
 *
 * A moving average cannot follow a curved trend, so on a `hill` pressure
 * profile the curvature leaks into what it calls the residual -- it reported
 * 4.16 alpha of "banding" where the ramp was steepest and 0.94 at the flat
 * peak, which is the shape of a detrending error, not of a segment artefact.
 */
function detrend(values, window) {
    const w = window % 2 === 0 ? window + 1 : window;
    const half = (w - 1) / 2;
    const residual = [];
    for (let i = 0; i < values.length; i++) {
        let n = 0, sx = 0, sx2 = 0, sx3 = 0, sx4 = 0;
        let sy = 0, sxy = 0, sx2y = 0;
        for (let k = -half; k <= half; k++) {
            const j = i + k;
            if (j < 0 || j >= values.length) continue;
            const x = k, y = values[j], x2 = x * x;
            n += 1; sx += x; sx2 += x2; sx3 += x2 * x; sx4 += x2 * x2;
            sy += y; sxy += x * y; sx2y += x2 * y;
        }
        const m = [[n, sx, sx2], [sx, sx2, sx3], [sx2, sx3, sx4]];
        const v = [sy, sxy, sx2y];
        let fitted = sy / n;
        const det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                  - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                  + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
        if (Math.abs(det) > 1e-9) {
            const d0 = v[0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                     - m[0][1] * (v[1] * m[2][2] - m[1][2] * v[2])
                     + m[0][2] * (v[1] * m[2][1] - m[1][1] * v[2]);
            fitted = d0 / det;
        }
        residual.push(values[i] - fitted);
    }
    return residual;
}

function dominantPeriod(residual) {
    let best = { period: 0, magnitude: 0 };
    for (let p = 3; p <= 40; p += 0.25) {
        const w = 2 * Math.PI / p;
        let re = 0, im = 0;
        for (let i = 0; i < residual.length; i++) {
            re += residual[i] * Math.cos(w * i);
            im += residual[i] * Math.sin(w * i);
        }
        const mag = 2 * Math.sqrt(re * re + im * im) / residual.length;
        if (mag > best.magnitude) best = { period: p, magnitude: mag };
    }
    return { period: +best.period.toFixed(2),
             magnitude: +best.magnitude.toFixed(3) };
}

/** The centreline of a straight stroke, with its banding statistics. */
function centrelineStats(run, x0, x1) {
    const row = [];
    for (let x = x0; x <= x1; x++) row.push(run.map[MID_Y * DOC_W + x]);
    const mean = row.reduce((a, b) => a + b, 0) / row.length;

    const bs = run.calls.filter(c => c.kind === "sweep")
        .map(c => c.fromX).filter(b => b >= x0 && b <= x1).sort((a, b) => a - b);
    const gaps = [];
    for (let i = 1; i < bs.length; i++) gaps.push(bs[i] - bs[i - 1]);
    const meanGap = gaps.length
        ? gaps.reduce((a, b) => a + b, 0) / gaps.length : 8.1;

    const residual = detrend(row, Math.max(5, Math.round(meanGap * 2)));
    const mn = Math.min(...residual), mx = Math.max(...residual);

    //: THE BOUNDARY STATISTIC. Split by distance to the nearest recorded
    //: segment endpoint. If the segment rate is the cause, this is it stated
    //: directly rather than inferred from a spectrum.
    let atB = 0, atBn = 0, atM = 0, atMn = 0;
    for (let i = 0; i < row.length; i++) {
        const x = x0 + i;
        let nearest = Infinity;
        for (const b of bs) {
            const d = Math.abs(x - b);
            if (d < nearest) nearest = d;
        }
        if (nearest <= meanGap * 0.25) { atB += residual[i]; atBn += 1; }
        else if (nearest >= meanGap * 0.35) { atM += residual[i]; atMn += 1; }
    }

    const dom = dominantPeriod(residual);
    return {
        meanAlpha: +mean.toFixed(1),
        meanGapPx: +meanGap.toFixed(2),
        bandingPct: mean ? +(100 * (mx - mn) / mean).toFixed(2) : 0,
        boundaryMinusMidpoint: +((atBn ? atB / atBn : 0)
                                 - (atMn ? atM / atMn : 0)).toFixed(3),
        dominantPeriodPx: dom.period,
        row: row,
    };
}

/** Geometric width of a straight stroke, measured column by column. */
function widths(run, x0, x1) {
    const out = [];
    for (let x = x0; x <= x1; x += 10) {
        let n = 0;
        for (let y = 0; y < DOC_H; y++) if (run.map[y * DOC_W + x] > 0) n += 1;
        out.push(n);
    }
    return out;
}

function totals(run) {
    let painted = 0, total = 0, peak = 0;
    for (let i = 0; i < run.map.length; i++) {
        const v = run.map[i];
        if (v > 0) { painted += 1; total += v; if (v > peak) peak = v; }
    }
    return { painted, total, peak };
}

const out = {};

// ── §16 the banding gate, per pressure mode ───────────────────────────────
out.modes = ["none", "size", "opacity", "both"].map(mode => {
    const s = centrelineStats(
        stroke({ pressureAffects: mode, profile: "ramp" }), 200, 700);
    return { mode: mode, meanAlpha: s.meanAlpha,
             bandingPct: s.bandingPct, meanGapPx: s.meanGapPx,
             boundaryMinusMidpoint: s.boundaryMinusMidpoint,
             dominantPeriodPx: s.dominantPeriodPx };
});

// ── §15 the pressure profiles ─────────────────────────────────────────────
out.profiles = ["flat", "low", "high", "ramp", "rampDown", "hill", "step"]
    .map(profile => {
        const s = centrelineStats(
            stroke({ pressureAffects: "opacity", profile: profile }), 200, 700);
        return { profile: profile, meanAlpha: s.meanAlpha,
                 bandingPct: s.bandingPct,
                 boundaryMinusMidpoint: s.boundaryMinusMidpoint,
                 dominantPeriodPx: s.dominantPeriodPx };
    });

// ── §16 monotonicity with the requested ramp ──────────────────────────────
(function () {
    const s = centrelineStats(
        stroke({ pressureAffects: "opacity", profile: "ramp" }), 220, 680);
    //: Averaged in 20 px blocks, because 8-bit rounding makes a strict
    //: per-pixel monotonicity test a test of the rounding.
    const blocks = [];
    for (let i = 0; i + 20 <= s.row.length; i += 20) {
        const seg = s.row.slice(i, i + 20);
        blocks.push(+(seg.reduce((a, b) => a + b, 0) / seg.length).toFixed(2));
    }
    let rising = true;
    for (let i = 1; i < blocks.length; i++) {
        if (blocks[i] < blocks[i - 1]) rising = false;
    }
    const down = centrelineStats(
        stroke({ pressureAffects: "opacity", profile: "rampDown" }), 220, 680);
    const dblocks = [];
    for (let i = 0; i + 20 <= down.row.length; i += 20) {
        const seg = down.row.slice(i, i + 20);
        dblocks.push(+(seg.reduce((a, b) => a + b, 0) / seg.length).toFixed(2));
    }
    let falling = true;
    for (let i = 1; i < dblocks.length; i++) {
        if (dblocks[i] > dblocks[i - 1]) falling = false;
    }
    out.monotonic = { rampBlocks: blocks, rampRises: rising,
                      rampDownBlocks: dblocks, rampDownFalls: falling };
})();

// ── §14 the gradient is actually used, and only where it should be ────────
out.gradientUse = ["none", "size", "opacity", "both"].map(mode => {
    const run = stroke({ pressureAffects: mode, profile: "ramp" });
    const sweeps = run.calls.filter(c => c.kind === "sweep");
    const withGradient = sweeps.filter(c => c.gradient).length;
    //: §14: "continuity at shared segment endpoints" -- each segment's start
    //: value must equal the previous segment's end value.
    let discontinuities = 0;
    for (let i = 1; i < sweeps.length; i++) {
        const prevEnd = sweeps[i - 1].targetTo === null
            ? sweeps[i - 1].target : sweeps[i - 1].targetTo;
        if (Math.abs(sweeps[i].target - prevEnd) > 1e-9) discontinuities += 1;
    }
    return { mode: mode, sweeps: sweeps.length, withGradient: withGradient,
             endpointDiscontinuities: discontinuities };
});

// ── §14 the final endpoint carries its actual pressure ────────────────────
//
// NOT "the last target equals the last pressure". `describeStroke` builds a
// pressure->flow RULE with a feel curve, so the target is a function of the
// pressure and not the pressure itself; an equality test there asserts the
// curve is the identity, which it is not, and fails for the wrong reason.
//
// The behaviour §14 actually asks for is that the final endpoint's OWN flow
// reaches the canvas. Two strokes identical except for the pressure of the last
// sample must differ at the last segment's end, in the right direction, and by
// the amount the curve implies rather than by nothing.
(function () {
    const withFinal = (finalPressure) => {
        const saved = PROFILES.ramp;
        PROFILES.ramp = (t) => (t >= 1 ? finalPressure : 0.08 + 0.5 * t);
        const run = stroke({ pressureAffects: "opacity", profile: "ramp" });
        PROFILES.ramp = saved;
        const sweeps = run.calls.filter(c => c.kind === "sweep");
        const lastEnd = sweeps.length
            ? (sweeps[sweeps.length - 1].targetTo !== null
                ? sweeps[sweeps.length - 1].targetTo
                : sweeps[sweeps.length - 1].target)
            : 0;
        //: The alpha actually committed at the very end of the painted run,
        //: which is what the owner sees whatever the internals did.
        let lastPaintedX = 0;
        for (let x = DOC_W - 1; x >= 0; x--) {
            if (run.map[MID_Y * DOC_W + x] > 0) { lastPaintedX = x; break; }
        }
        return { finalPressure: finalPressure,
                 lastSegmentEndTarget: +lastEnd.toFixed(4),
                 endAlpha: run.map[MID_Y * DOC_W + (lastPaintedX - 2)],
                 lastPaintedX: lastPaintedX };
    };
    const light = withFinal(0.15);
    const heavy = withFinal(0.95);
    out.finalEndpoint = {
        light: light, heavy: heavy,
        //: Ordered, and separated by far more than one flow bucket (1/256).
        ordered: heavy.lastSegmentEndTarget > light.lastSegmentEndTarget,
        targetSeparation: +(heavy.lastSegmentEndTarget
                            - light.lastSegmentEndTarget).toFixed(4),
        alphaSeparation: heavy.endAlpha - light.endAlpha,
        //: Both strokes travel the same path, so the paint must stop in the
        //: same place -- a lighter final touch must not shorten the stroke.
        sameExtent: light.lastPaintedX === heavy.lastPaintedX,
    };
})();

// ── §16 width and flow stay independent ───────────────────────────────────
(function () {
    const w = (mode) => widths(stroke({ pressureAffects: mode,
                                        profile: "ramp" }), 220, 680);
    const flowOf = (mode) => centrelineStats(
        stroke({ pressureAffects: mode, profile: "ramp" }), 220, 680);
    const none = w("none"), size = w("size");
    const opacity = w("opacity"), both = w("both");
    const spread = (v) => Math.max(...v) - Math.min(...v);
    out.independence = {
        widthsNone: none, widthsSize: size,
        widthsOpacity: opacity, widthsBoth: both,
        opacityWidthSpread: spread(opacity),
        noneWidthSpread: spread(none),
        sizeWidthSpread: spread(size),
        bothWidthSpread: spread(both),
        //: `size` must vary width and NOT the flow metric; `opacity` the
        //: reverse. Compared against `none`, which varies neither.
        flowNone: flowOf("none").meanAlpha,
        flowSize: flowOf("size").meanAlpha,
        flowOpacity: flowOf("opacity").meanAlpha,
        flowBoth: flowOf("both").meanAlpha,
    };
})();

// ── §14 "no single-value-per-segment staircase", measured directly ────────
//
// THE GATE THAT WAS MISSING, and five mutations proved it. Banding statistics
// cannot see this: once the arc-length normalisation is in place, a segment
// that is FLAT along its length still bands only 2.6 alpha per 8 px step, and a
// detrend window two gaps wide absorbs it. Reverting the gradient entirely --
// or averaging it to the segment midpoint -- therefore passed every gate this
// file had.
//
// So ask the question directly: of all the alpha change along the stroke, how
// much of it happens AT a segment boundary? A staircase puts essentially all of
// it there. A gradient spreads it over the whole segment, leaving a boundary
// share near one pixel in `gap`.
(function () {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp" });
    const x0 = 220, x1 = 680;
    const row = [];
    for (let x = x0; x <= x1; x++) row.push(run.map[MID_Y * DOC_W + x]);
    const bs = run.calls.filter(c => c.kind === "sweep")
        .map(c => c.fromX).filter(b => b > x0 + 1 && b < x1 - 1)
        .sort((a, b) => a - b);
    let atBoundary = 0, total = 0;
    for (let i = 1; i < row.length; i++) {
        const step = Math.abs(row[i] - row[i - 1]);
        total += step;
        const x = x0 + i;
        let nearest = Infinity;
        for (const b of bs) {
            const d = Math.abs(x - b);
            if (d < nearest) nearest = d;
        }
        if (nearest <= 1) atBoundary += step;
    }
    const gaps = [];
    for (let i = 1; i < bs.length; i++) gaps.push(bs[i] - bs[i - 1]);
    const meanGap = gaps.length
        ? gaps.reduce((a, b) => a + b, 0) / gaps.length : 8.1;
    out.staircase = {
        segments: bs.length,
        meanGapPx: +meanGap.toFixed(2),
        totalChange: +total.toFixed(1),
        changeAtBoundaries: +atBoundary.toFixed(1),
        boundaryShare: total ? +(atBoundary / total).toFixed(4) : 0,
        //: What a perfectly even gradient would put within +/-1 px of a
        //: boundary: three of every `gap` pixels.
        evenShare: +(3 / meanGap).toFixed(4),
    };
})();

// ── §14 the stroke reaches where the pen actually stopped ─────────────────
//
// Also missing, and also proved by a mutation: dropping the closing sample
// shortened every stroke equally, so a light-versus-heavy comparison could not
// see it. This asks the absolute question instead.
(function () {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp" });
    let lastPainted = -1;
    for (let x = DOC_W - 1; x >= 0; x--) {
        if (run.map[MID_Y * DOC_W + x] > 0) { lastPainted = x; break; }
    }
    out.reachesFinalPosition = {
        finalSampleX: X1,
        radiusPx: SIZE_PX / 2,
        lastPaintedX: lastPainted,
        //: The tip's own radius past the final sample, less a pixel for the
        //: antialiased rim.
        expectedAtLeast: X1 + SIZE_PX / 2 - 2,
    };
})();

// ── §17 cost must not grow with what the stroke has already painted ───────
//
// A RATIO WITHIN ONE RUN, which is what makes it usable as a gate: a clock
// reading would be a measurement of the machine.
//
// ALTERNATED AND TAKEN AS MEDIANS. A first-batch-versus-last-batch reading of
// the same buffer measured 1.199 on a quiet machine, which is JIT warm-up and
// GC rather than growth -- and 1.199 would have been reported against §17's
// 1.15 as if it meant something. Alternating the two populations and taking
// medians removes the warm-up, because both halves then contain early and late
// batches alike.
(function () {
    const r = 27, gap = 8.1;
    const buf = new K.CoverageBuffer(1024, 1024);
    const dep = K.depositionFor({ hardness: 0.85, flow: 0.5, opacity: 1,
                                  density: 1, buildup: false,
                                  step: gap / r, swept: true });
    const depTo = K.depositionFor({ hardness: 0.85, flow: 0.3, opacity: 1,
                                    density: 1, buildup: false,
                                    step: gap / r, swept: true });
    let placed = 0;
    const batch = (n) => {
        const t0 = process.hrtime.bigint();
        for (let i = 0; i < n; i++) {
            const y = 60 + (placed % 900);
            placed += 1;
            K.sweep(buf, { x: 100, y: y }, { x: 108, y: y }, r, dep, depTo);
        }
        return Number(process.hrtime.bigint() - t0) / 1e6 / n;
    };
    const median = (xs) => {
        const v = xs.slice().sort((a, b) => a - b);
        const h = v.length >> 1;
        return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2;
    };
    //: Warm the JIT before either population is sampled.
    for (let k = 0; k < 4; k++) batch(200);
    const early = [], late = [];
    for (let k = 0; k < 12; k++) {
        //: Alternate, so warm-up and drift land in both populations equally.
        (k % 2 === 0 ? early : late).push(batch(200));
    }
    out.historyGrowth = {
        earlyMsPerSegment: +median(early).toFixed(4),
        lateMsPerSegment: +median(late).toFixed(4),
        ratio: +(median(late) / median(early)).toFixed(3),
        batches: early.length + late.length,
        //: Recorded so a threshold is read against the noise it sits above: a
        //: scan of what the stroke has already painted measures 3x and up.
        note: "alternated batches, medians; JIT warmed before sampling",
    };
})();

// ── §14's contract on ONE segment, where it is not averaged away ──────────
//
// THE GRADIENT IS NEARLY INVISIBLE IN A REAL STROKE, and pretending otherwise
// would make this suite assert something it cannot see. At the shipping spacing
// a pixel is inside about 2r/gap = 6.7 capsules, so its final alpha is already
// a union over a 54 px window of the ramp; interpolating WITHIN the one segment
// that currently contains it changes that union by about 15% of one segment's
// step, which is under half an alpha level. Two mutations -- reverting the
// gradient entirely, and averaging it to each segment's midpoint -- produced
// byte-identical fixtures at spacing 0.15 for exactly that reason.
//
// So the contract is measured where it is not averaged: ONE segment, long
// enough that its interior pixels are covered by it alone. There the flow at a
// pixel is the flow the gradient assigns it, and nothing else.
//
// This is also where §14's requirement actually bites in practice -- the ends
// of a stroke, an abrupt pressure step, and any brush whose spacing approaches
// its own diameter.
(function () {
    const r = 27, y = 110;
    const buf = new K.CoverageBuffer(500, 220);
    const low = K.depositionFor({ hardness: 1, flow: 0.2, opacity: 1,
                                  density: 1, buildup: false,
                                  step: 0.3, swept: true });
    const high = K.depositionFor({ hardness: 1, flow: 0.9, opacity: 1,
                                   density: 1, buildup: false,
                                   step: 0.3, swept: true });
    K.sweep(buf, { x: 100, y: y }, { x: 400, y: y }, r, low, high);
    const at = (x) => {
        const i = y * 500 + x;
        const b = buf.acc[i] >> 8;
        return b > buf.peak[i] ? b : buf.peak[i];
    };
    const profile = [];
    for (let x = 110; x <= 390; x += 10) profile.push(at(x));
    //: STRICTLY rising, and the strictness is the point: a FLAT profile is
    //: non-decreasing, so a `<` test calls a removed gradient "rising" and
    //: cannot reject the one thing it exists to reject. A mutation proved it.
    let rising = true;
    for (let i = 1; i < profile.length; i++) {
        if (profile[i] <= profile[i - 1]) rising = false;
    }
    //: A flat segment -- gradient removed, or averaged to one value -- has a
    //: span of zero here. A working gradient spans most of 0.2..0.9 of 255.
    out.singleSegmentGradient = {
        profile: profile,
        startAlpha: profile[0],
        endAlpha: profile[profile.length - 1],
        span: profile[profile.length - 1] - profile[0],
        rising: rising,
        lowTarget: Math.round(0.2 * 255),
        highTarget: Math.round(0.9 * 255),
        //: The midpoint, which is what an averaged segment would read
        //: everywhere. Recorded so "not the average" is checkable.
        midAlpha: profile[(profile.length / 2) | 0],
    };
})();

// ── §17 "no full-document work", as a cost that must not scale ────────────
//
// The other measurement a mutation proved was missing. A per-segment scan of
// the whole document is CONSTANT per segment, so neither a visited-pixel count
// nor a growth-over-time ratio can see it -- the first does not count it and
// the second sees no growth. What it does do is scale with the DOCUMENT, which
// is the thing §17 forbids and U1 was built around.
//
// Same stroke geometry, three document sizes. The per-segment cost must be flat.
(function () {
    const r = 27;
    const median = (xs) => {
        const v = xs.slice().sort((a, b) => a - b);
        const h = v.length >> 1;
        return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2;
    };
    const costAt = (side) => {
        const buf = new K.CoverageBuffer(side, side);
        const dep = K.depositionFor({ hardness: 0.85, flow: 0.5, opacity: 1,
                                      density: 1, buildup: false,
                                      step: 0.3, swept: true });
        const depTo = K.depositionFor({ hardness: 0.85, flow: 0.3, opacity: 1,
                                        density: 1, buildup: false,
                                        step: 0.3, swept: true });
        const batch = (n) => {
            const t0 = process.hrtime.bigint();
            for (let i = 0; i < n; i++) {
                const y = 60 + (i % 300);
                K.sweep(buf, { x: 100, y: y }, { x: 108, y: y }, r, dep, depTo);
            }
            return Number(process.hrtime.bigint() - t0) / 1e6 / n;
        };
        batch(100);
        const runs = [];
        for (let k = 0; k < 5; k++) runs.push(batch(150));
        return median(runs);
    };
    const small = costAt(512), mid = costAt(1024), large = costAt(2048);
    out.documentScaleCost = {
        ms512: +small.toFixed(4), ms1024: +mid.toFixed(4),
        ms2048: +large.toFixed(4),
        //: Area grows 16x from 512 to 2048. Bounded work does not.
        ratio2048over512: +(large / small).toFixed(3),
        areaRatio: 16,
    };
})();

// ── §16 grouping and density invariance ───────────────────────────────────
//: ON A CURVE, not a straight line. Dropping the intermediate samples of a
//: coalesced group changes nothing on a straight path, because the dropped
//: points are colinear with the ones that survive -- so a straight fixture
//: cannot tell an engine that honours coalescing from one that discards it.
out.grouping = [1, 2, 3, 6, 12].map(per => {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp",
                         path: "scurve", perEvent: per });
    const t = totals(run);
    return { perEvent: per, marks: run.marks, samples: run.samples,
             painted: t.painted, total: t.total, peak: t.peak };
});

//: §15's "different geometric segment lengths over the same continuous path":
//: the same path described by more or fewer input samples.
out.sampleDensity = [20, 40, 60, 120].map(n => {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp",
                         samples: n });
    const s = centrelineStats(run, 250, 650);
    return { samples: n, marks: run.marks, meanAlpha: s.meanAlpha,
             bandingPct: s.bandingPct };
});

//: The arc-length property at the kernel, where the mark gap can be set
//: directly instead of inferred: deposition must not depend on the chopping.
out.gapInvariance = [4, 6, 8.1, 12, 16, 20].map(gap => {
    const r = 27, buf = new K.CoverageBuffer(900, 220);
    const dep = K.depositionFor({ hardness: 0.85, flow: 0.5, opacity: 1,
                                  density: 1, buildup: false,
                                  step: gap / r, swept: true });
    let prev = 100;
    for (let x = 100 + gap; x <= 800; x += gap) {
        K.sweep(buf, { x: prev, y: 110 }, { x: x, y: 110 }, r, dep);
        prev = x;
    }
    const at = (px, py) => {
        const i = py * 900 + px;
        const b = buf.acc[i] >> 8;
        return b > buf.peak[i] ? b : buf.peak[i];
    };
    const row = [];
    for (let x = 200; x <= 700; x++) row.push(at(x, 110));
    const mean = row.reduce((a, b) => a + b, 0) / row.length;
    const res = detrend(row, Math.max(5, Math.round(gap * 2)));
    return { gap: gap, meanAlpha: +mean.toFixed(1),
             bandingAbs: +(Math.max(...res) - Math.min(...res)).toFixed(2),
             centreAlpha: at(450, 110),
             //: What a full pass is worth: `target * cov` at the centreline.
             expected: Math.round(0.5 * 255) };
});

// ── §15 the path shapes ───────────────────────────────────────────────────
out.paths = Object.keys(PATHS).map(name => {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp",
                         path: name });
    const t = totals(run);
    const sweeps = run.calls.filter(c => c.kind === "sweep");
    return { path: name, marks: run.marks, sweeps: sweeps.length,
             painted: t.painted, total: t.total, peak: t.peak,
             withGradient: sweeps.filter(c => c.gradient).length };
});

// ── §16 constant-flow rows must not regress R1 ────────────────────────────
//
// The R1 fixture: a straight stroke at each hardness, no pressure, flow 1.
// Nothing in F2 may touch it, because at flow 1 the accumulator does not run.
out.r1Constant = [1, 0.85, 0.5, 0.25, 0].map(hardness => {
    const run = stroke({ pressureAffects: "none", profile: "flat",
                         hardness: hardness });
    const s = centrelineStats(run, 200, 700);
    const cross = [];
    for (let d = 0; d <= 30; d += 3) cross.push(run.map[(MID_Y + d) * DOC_W + 450]);
    return { hardness: hardness, meanAlpha: s.meanAlpha,
             bandingPct: s.bandingPct, crossSection: cross,
             centreAlpha: run.map[MID_Y * DOC_W + 450] };
});

// ── §17 no extra marks were added to smooth anything ──────────────────────
out.markCount = ["none", "size", "opacity", "both"].map(mode => ({
    mode: mode,
    marks: stroke({ pressureAffects: mode, profile: "ramp" }).marks,
}));

// ── §17 work stays bounded to the stroke ──────────────────────────────────
(function () {
    const run = stroke({ pressureAffects: "opacity", profile: "ramp" });
    const d = run.dirty;
    out.bounded = {
        dirty: { x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1 },
        area: Math.max(0, d.x1 - d.x0) * Math.max(0, d.y1 - d.y0),
        documentPixels: DOC_W * DOC_H,
    };
})();

// ── the accumulator telescopes to what a full pass is worth ───────────────
//
// The property the whole repair rests on: however a pass is chopped, the shares
// sum to 1 and the pixel lands on `target * cov`.
out.telescoping = [0.2, 0.35, 0.5, 0.7, 0.9].map(flow => {
    const r = 27, gap = 8.1;
    const buf = new K.CoverageBuffer(900, 220);
    const dep = K.depositionFor({ hardness: 1, flow: flow, opacity: 1,
                                  density: 1, buildup: false,
                                  step: gap / r, swept: true });
    let prev = 100;
    for (let x = 100 + gap; x <= 800; x += gap) {
        K.sweep(buf, { x: prev, y: 110 }, { x: x, y: 110 }, r, dep);
        prev = x;
    }
    const i = 110 * 900 + 450;
    const built = buf.acc[i] >> 8;
    const got = built > buf.peak[i] ? built : buf.peak[i];
    return { flow: flow, centreAlpha: got, expected: Math.round(flow * 255),
             delta: got - Math.round(flow * 255) };
});

process.stdout.write(JSON.stringify(out, null, 1));
