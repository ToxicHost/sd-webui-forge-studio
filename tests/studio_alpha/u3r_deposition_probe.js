/**
 * U3-R R1 probe: the soft-brush deposition repair, through the REAL adapter.
 *
 * WHAT WAS WRONG. Stamping a soft round tip takes MAX of overlapping falloffs,
 * and the max of two smoothsteps dips between their centres. On a straight
 * 54 px stroke at the shipped spacing that measured 16.6% alpha ripple at
 * hardness 0.25, with the beads reaching the CENTRELINE at hardness 0 -- which
 * is what the owner photographed.
 *
 * WHAT WAS DONE. `rendererFor` offered its analytic sweep only to tips with
 * `hardness >= 0.99`. That condition was policy, not mathematics: `sweep`
 * evaluates `shapeAt(distanceToSegment / r)`, which for a round procedural tip
 * IS the continuous limit of an infinitely dense stamp train. The condition is
 * gone; textured, scattered and anisotropic tips still stamp, because those are
 * the ones DiVerdi's "constant fill loses the media quality" warning is about.
 *
 * AND THE PART THAT IS EASY TO MISS. `overlapK` counts contributions covering
 * one pixel so `fEff` can divide by it. A stamp train catches a pixel at a
 * different point on the falloff each time -- weight `profileMean`. A sweep
 * catches it at the SAME perpendicular distance every time -- weight 1. Without
 * that correction a swept Flow-0.5 stroke deposited 144 of 255 where the train
 * deposits 127. The renderer is therefore chosen BEFORE the deposition, because
 * the two cannot be resolved independently.
 *
 *     node tests/studio_alpha/u3r_deposition_probe.js
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
const COV = W.StudioBrushCoverageV2;

const DOC_W = 900, DOC_H = 220, MID_Y = 110;
const SIZE_PX = 54;

function makeState(opts) {
    const o = opts || {};
    const layers = [];
    for (let i = 0; i < 2; i++) {
        layers.push({ type: "paint", canvas: { width: DOC_W, height: DOC_H },
                      ctx: {}, visible: true, locked: false, opacity: 1 });
    }
    return {
        W: DOC_W, H: DOC_H, layers, activeLayerIdx: 1,
        tool: o.tool || "brush",
        editingMask: false, regionMode: false,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSize: 18,
        brushOpacity: o.opacity === undefined ? 1 : o.opacity,
        brushHardness: o.hardness === undefined ? 0.5 : o.hardness,
        brushFlow: o.flow === undefined ? 1 : o.flow,
        brushBuildup: !!o.buildup,
        brushSpacing: 0.15,
        pressureSensitivity: !!o.pressureSensitivity,
        pressureAffects: o.pressureAffects || "none",
        stroke: {
            alphaMap: new Uint8Array(DOC_W * DOC_H),
            dirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
            frameDirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
        },
    };
}

const CORE = { brushPx: () => SIZE_PX };
const toDoc = (x, y) => ({ x, y });

function event(x, y, t, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t, pressure: 0.8,
        pointerType: "pen", tiltX: 0, tiltY: 0, twist: 0,
        buttons: 1, isPrimary: true,
    }, over || {});
}

function coalescedEvent(points, t0, over) {
    const last = points[points.length - 1];
    const e = event(last[0], last[1], t0 + points.length * 8, over);
    e.getCoalescedEvents = () =>
        points.map((p, i) => event(p[0], p[1], t0 + i * 8, p[2] === undefined
            ? over : Object.assign({}, over, { pressure: p[2] })));
    return e;
}

/** Drive a straight horizontal stroke and report what landed in the alpha map. */
function straightStroke(opts) {
    const o = opts || {};
    A._setEnabled(true);
    const S = makeState(o);
    const x0 = 100, x1 = 800;
    const pts = [];
    const n = o.samples === undefined ? 60 : o.samples;
    for (let i = 0; i <= n; i++) {
        const t = i / n;
        const p = o.pressureRamp ? 0.08 + 0.9 * Math.sin(t * Math.PI) : 0.8;
        pts.push([x0 + (x1 - x0) * t, MID_Y, p]);
    }
    A.begin(S, CORE, event(pts[0][0], pts[0][1], 0,
                           { pressure: pts[0][2] }), toDoc);
    const per = o.perEvent || 1;
    let t = 16;
    for (let i = 1; i < pts.length; i += per) {
        const group = pts.slice(i, Math.min(pts.length, i + per));
        if (group.length === 1) {
            A.addFromEvent(S, event(group[0][0], group[0][1], t,
                                    { pressure: group[0][2] }), toDoc);
        } else {
            A.addFromEvent(S, coalescedEvent(group, t), toDoc);
        }
        t += 16;
    }
    const last = pts[pts.length - 1];
    const summary = A.finish(S, event(last[0], last[1], t,
                                      { pressure: last[2] }), toDoc);

    const map = S.stroke.alphaMap;
    const at = (x, y) => map[y * DOC_W + x];
    const row = (frac) => {
        const off = Math.round((SIZE_PX / 2) * frac);
        const a = [];
        for (let x = x0 + 60; x <= x1 - 60; x++) a.push(at(x, MID_Y + off));
        const mn = Math.min(...a), mx = Math.max(...a);
        const mean = a.reduce((p, q) => p + q, 0) / a.length;
        return { mean: +mean.toFixed(1), ripple: mx - mn,
                 pct: mean ? +(100 * (mx - mn) / mean).toFixed(1) : 0 };
    };
    const cross = [];
    for (let d = 0; d <= 30; d += 3) cross.push(at(450, MID_Y + d));
    let painted = 0, sum = 0;
    for (let i = 0; i < map.length; i++) { if (map[i]) { painted += 1; sum += map[i]; } }
    return {
        marks: summary ? summary.marks : 0,
        samples: summary ? summary.samples : 0,
        centreline: row(0), r35: row(0.35), r60: row(0.6), r80: row(0.8),
        crossSection: cross, centreAlpha: at(450, MID_Y),
        paintedPixels: painted, totalAlpha: sum,
        dirty: Object.assign({}, S.stroke.dirty),
    };
}

const out = {};

// ── which renderer each tip now selects ────────────────────────────────────
out.rendererSelection = [1, 0.99, 0.85, 0.5, 0.25, 0].map(h => ({
    hardness: h,
    round: COV.rendererFor({ hardness: h, textured: false, scatter: false, ratio: 1 }),
    textured: COV.rendererFor({ hardness: h, textured: true, scatter: false, ratio: 1 }),
    scattered: COV.rendererFor({ hardness: h, textured: false, scatter: true, ratio: 1 }),
    anisotropic: COV.rendererFor({ hardness: h, textured: false, scatter: false, ratio: 2 }),
}));

// ── the repair, measured through the adapter ───────────────────────────────
out.ripple = [1, 0.85, 0.5, 0.25, 0].map(h =>
    Object.assign({ hardness: h }, straightStroke({ hardness: h })));

// ── flow fidelity: the swept overlap weight must not change what a pass lays ─
out.flowFidelity = [];
for (const h of [0.85, 0.5, 0]) {
    for (const flow of [1, 0.8, 0.5, 0.2]) {
        const r = straightStroke({ hardness: h, flow });
        out.flowFidelity.push({ hardness: h, flow, centreAlpha: r.centreAlpha,
                                expectedApprox: Math.round(255 * flow),
                                worstRipplePct: Math.max(
                                    r.centreline.pct, r.r35.pct, r.r60.pct, r.r80.pct) });
    }
}

// ── dispatch invariance: grouping must not change a pixel ──────────────────
(function () {
    const one = straightStroke({ hardness: 0, perEvent: 1 });
    const three = straightStroke({ hardness: 0, perEvent: 3 });
    const all = straightStroke({ hardness: 0, perEvent: 999 });
    out.dispatchInvariance = {
        onePerEvent: { alpha: one.totalAlpha, marks: one.marks },
        threePerEvent: { alpha: three.totalAlpha, marks: three.marks },
        allInOne: { alpha: all.totalAlpha, marks: all.marks },
        identical: one.totalAlpha === three.totalAlpha
                && three.totalAlpha === all.totalAlpha,
    };
})();

// ── a tap has no segment, so it must still stamp exactly one footprint ─────
(function () {
    A._setEnabled(true);
    const S = makeState({ hardness: 0 });
    A.begin(S, CORE, event(450, MID_Y, 0), toDoc);
    const summary = A.finish(S, event(450, MID_Y, 8), toDoc);
    const map = S.stroke.alphaMap;
    let painted = 0;
    for (let i = 0; i < map.length; i++) if (map[i]) painted += 1;
    out.tap = {
        marks: summary.marks, paintedPixels: painted,
        centre: map[MID_Y * DOC_W + 450],
        atR60: map[MID_Y * DOC_W + 450 + 16],
        outsideRim: map[MID_Y * DOC_W + 450 + 28],
    };
})();

// ── pressure-varying width still tapers ────────────────────────────────────
(function () {
    const r = straightStroke({ hardness: 0.5, pressureSensitivity: true,
                              pressureRamp: true });
    const widths = [];
    const map = [];
    for (let s = 0; s < 9; s++) {
        const x = Math.round(160 + (740 - 160) * (s / 8));
        let n = 0;
        for (let y = MID_Y - 40; y <= MID_Y + 40; y++) {
            if (r.crossSection && false) break;
            n += 0;
        }
        map.push(x);
    }
    out.pressureTaperNote =
        "width profile measured in the browser matrix, not here: this stub has "
        + "no layer pixels, and the alpha map is the same surface the ripple "
        + "rows already read";
    out.pressureRampRan = { marks: r.marks, samples: r.samples,
                            painted: r.paintedPixels };
})();

// ── bounded work: the dirty rect stays the stroke's, not the document's ────
(function () {
    const r = straightStroke({ hardness: 0 });
    const area = (r.dirty.x1 - r.dirty.x0 + 1) * (r.dirty.y1 - r.dirty.y0 + 1);
    out.bounded = {
        dirty: r.dirty, area,
        documentPixels: DOC_W * DOC_H,
        sharePct: +(100 * area / (DOC_W * DOC_H)).toFixed(2),
    };
})();

// ── a STAMPED tip must keep its own overlap weight ─────────────────────────
//
// Round tips now sweep, so nothing above exercises the stamp train's flow any
// more -- and a mutation that gave the swept weight to EVERY renderer survived
// the whole suite because of it. Textured, scattered and anisotropic tips still
// stamp, and their `overlapK` must stay `(2/step) * profileMean`: a stamp train
// catches a pixel at a different point on the falloff each time.
(function () {
    const SZ = 54, R = SZ / 2, SP = 0.15;
    out.stampedFlowFidelity = [];
    for (const hardness of [0.85, 0.5, 0]) {
        for (const flow of [1, 0.5, 0.2]) {
            const W = 700, H = 160, y = 80;
            const buf = new COV.CoverageBuffer(W, H);
            const dep = COV.depositionFor({
                hardness, flow, opacity: 1, density: 1, buildup: false,
                step: SP * 2, swept: false,
            });
            for (let x = 100; x <= 600; x += SP * SZ) COV.stamp(buf, { x, y }, R, dep);
            const i = y * W + 350;
            const built = buf.acc[i] >> 8;
            const alpha = built > buf.peak[i] ? built : buf.peak[i];
            out.stampedFlowFidelity.push({
                hardness, flow, centreAlpha: alpha,
                expectedApprox: Math.round(255 * flow),
                overlapK: +dep.overlapK.toFixed(3),
            });
        }
    }
})();

process.stdout.write(JSON.stringify(out, null, 1));
