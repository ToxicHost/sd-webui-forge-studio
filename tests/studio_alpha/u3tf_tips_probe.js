/**
 * U3-TF probe — tip geometry, and the adapter that finally forwards it.
 *
 * WHAT THIS HAS TO PROVE. `describeStroke` forwarded eleven fields and hard-coded
 * or dropped the rest, so fifteen of the sixteen shipping presets reached the
 * engine as something other than what they declare. The kind was among them, and
 * the kernel had no anisotropic mask at all -- `stamp` measured
 * `sqrt(dx*dx + dy*dy)` for every tip, so `rendererFor` would route a flat tip to
 * the stamp renderer and the stamp renderer would draw it round.
 *
 * THE ORDER IS THE PART A NAIVE TEST MISSES. `canvas-core.js:2262` records it:
 * "Rotation preserves an isotropic norm, so folding the angle and THEN measuring
 * sqrt(dx^2+dy^2) cannot change any pixel: Spikes was not unwired, it was
 * algebraically incapable of doing anything." A test that asserts a frame was
 * built passes on that broken port. These measure PIXELS.
 *
 *     node tests/studio_alpha/u3tf_tips_probe.js
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
        const f = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(f, "utf8"), g, { filename: f });
    }
    return g.window;
}

const W = load();
const K = W.StudioBrushCoverageV2;
const A = W.StudioBrushV2Adapter;
const DOC = 420;

function readAlpha(buf) {
    const out = new Uint8Array(DOC * DOC);
    for (let i = 0; i < out.length; i++) {
        const b = buf.acc[i] >> 8;
        out[i] = b > buf.peak[i] ? b : buf.peak[i];
    }
    return out;
}

function shapeOf(map) {
    let x0 = DOC, x1 = -1, y0 = DOC, y1 = -1, n = 0;
    for (let y = 0; y < DOC; y++) {
        for (let x = 0; x < DOC; x++) {
            if (!map[y * DOC + x]) continue;
            n += 1;
            if (x < x0) x0 = x;
            if (x > x1) x1 = x;
            if (y < y0) y0 = y;
            if (y > y1) y1 = y;
        }
    }
    if (x1 < 0) return { width: 0, height: 0, painted: 0, fill: 0 };
    const w = x1 - x0 + 1, h = y1 - y0 + 1;
    return { width: w, height: h, painted: n,
             fill: +(n / (w * h)).toFixed(4) };
}

/** One dab, straight at the kernel, so nothing but the tip is under test. */
function dab(tip, radius) {
    const buf = new K.CoverageBuffer(DOC, DOC);
    const dep = K.depositionFor({
        hardness: 1, flow: 1, opacity: 1, density: 1, step: 0.3, swept: false,
        tipKind: tip.kind, ratio: tip.ratio, spikes: tip.spikes,
        angle: tip.angle || 0,
    });
    K.stamp(buf, { x: DOC / 2, y: DOC / 2 }, radius === undefined ? 60 : radius, dep);
    return shapeOf(readAlpha(buf));
}

const out = {};

// ── 1. every kind's footprint, measured ────────────────────────────────────
out.shapes = {
    round:        dab({ kind: "round", ratio: 1, spikes: 2 }),
    flat0:        dab({ kind: "flat", ratio: 1, spikes: 2, angle: 0 }),
    flat90:       dab({ kind: "flat", ratio: 1, spikes: 2, angle: Math.PI / 2 }),
    marker:       dab({ kind: "marker", ratio: 1, spikes: 2, angle: 0 }),
    roundRatio06: dab({ kind: "round", ratio: 0.6, spikes: 2, angle: 0 }),
    spikes6:      dab({ kind: "round", ratio: 0.5, spikes: 6, angle: 0 }),
    scatter:      dab({ kind: "scatter", ratio: 1, spikes: 2 }),
};

// ── 2. THE ORDER. Spikes must change pixels on a shaped tip ────────────────
(function () {
    //: The whole point of Legacy's note. If the fold were applied before an
    //: isotropic norm, these two would be identical and a frame-exists test
    //: would still pass.
    const noSpikes = dab({ kind: "round", ratio: 0.5, spikes: 2 });
    const withSpikes = dab({ kind: "round", ratio: 0.5, spikes: 6 });
    //: And the fact Legacy asserts rather than promises: on a CIRCLE, spikes
    //: and angle cannot do anything, because a circle has no orientation.
    const circle = dab({ kind: "round", ratio: 1, spikes: 2 });
    const circleSpiked = dab({ kind: "round", ratio: 1, spikes: 6 });
    const circleAngled = dab({ kind: "round", ratio: 1, spikes: 2, angle: 0.7 });
    out.order = {
        shapedChangesWithSpikes: noSpikes.painted !== withSpikes.painted,
        shapedPaintedNoSpikes: noSpikes.painted,
        shapedPaintedWithSpikes: withSpikes.painted,
        circleIgnoresSpikes: circle.painted === circleSpiked.painted,
        circleIgnoresAngle: circle.painted === circleAngled.painted,
    };
})();

// ── 3. a round tip must not have moved at all ──────────────────────────────
(function () {
    //: `tipFrame` returns null for a circle and `stamp` takes the closure it
    //: always took, so this is byte identity by construction rather than by
    //: tolerance. Compared against a deposition carrying NO tip at all, which
    //: is what every caller passed before U3-TF.
    const buf = new K.CoverageBuffer(DOC, DOC);
    const withTip = K.depositionFor({
        hardness: 0.6, flow: 1, opacity: 1, density: 1, step: 0.3, swept: false,
        tipKind: "round", ratio: 1, spikes: 2, angle: 0.9,
    });
    K.stamp(buf, { x: DOC / 2, y: DOC / 2 }, 47, withTip);

    const buf2 = new K.CoverageBuffer(DOC, DOC);
    const bare = K.depositionFor({
        hardness: 0.6, flow: 1, opacity: 1, density: 1, step: 0.3, swept: false,
    });
    K.stamp(buf2, { x: DOC / 2, y: DOC / 2 }, 47, bare);

    const a = readAlpha(buf), b = readAlpha(buf2);
    let differing = 0, maxDelta = 0;
    for (let i = 0; i < a.length; i++) {
        const d = Math.abs(a[i] - b[i]);
        if (d) { differing += 1; if (d > maxDelta) maxDelta = d; }
    }
    out.roundUnchanged = { differing, maxDelta, painted: shapeOf(a).painted };
})();

// ── 4. routing ─────────────────────────────────────────────────────────────
out.routing = {
    round:   K.rendererFor({ kind: "round", ratio: 1, spikes: 2 }),
    flat:    K.rendererFor({ kind: "flat", ratio: 1, spikes: 2 }),
    marker:  K.rendererFor({ kind: "marker", ratio: 1, spikes: 2 }),
    ratio:   K.rendererFor({ kind: "round", ratio: 0.6, spikes: 2 }),
    spiked:  K.rendererFor({ kind: "round", ratio: 1, spikes: 6 }),
    scatter: K.rendererFor({ scatter: true }),
    bare:    K.rendererFor({}),
    SWEEP: K.RENDER_SWEEP, STAMP: K.RENDER_STAMP,
};

// ── 5. the adapter forwards what the owner set ─────────────────────────────
function makeState(o) {
    return {
        W: DOC, H: DOC,
        layers: [{ type: "paint", canvas: { width: DOC, height: DOC }, ctx: {},
                   visible: true, locked: false, opacity: 1 },
                 { type: "paint", canvas: { width: DOC, height: DOC }, ctx: {},
                   visible: true, locked: false, opacity: 1 }],
        activeLayerIdx: 1, tool: "brush", editingMask: false, regionMode: false,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSizeMode: "document_pixels", brushSize: 40,
        brushOpacity: 1, brushHardness: 0.8, brushFlow: 1, brushBuildup: false,
        brushSpacing: 0.15,
        brushPreset: o.preset, brushRatio: o.ratio, brushAngle: o.angleDeg,
        brushSpikes: o.spikes, brushDensity: o.density,
        brushGrain: 0, brushFalloff: "default", brushAliased: false,
        brushAirbrush: false, smoothing: o.smoothing,
        brushDynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                         rotationJitter: 0, followStroke: true, spacing: 0.15 },
        pressureSensitivity: false, pressureAffects: "none",
        zoom: { scale: o.zoom === undefined ? 1 : o.zoom, ox: 0, oy: 0 },
        stroke: { alphaMap: new Uint8Array(DOC * DOC),
                  dirty: { x0: DOC, y0: DOC, x1: 0, y1: 0 },
                  frameDirty: { x0: DOC, y0: DOC, x1: 0, y1: 0 } },
    };
}

// ── 5b. follow-stroke is a DYNAMIC, not a kind ─────────────────────────────
(function () {
    //: THE OWNER SAW THIS ONE. Reading follow-stroke off the tip KIND rotated
    //: Calligraphy at every stamp -- it declares `followStroke: false` and its
    //: own description is "A held nib. Angle is fixed". `dabRotation`
    //: (`canvas-core.js:3438`) reads `S.brushDynamics.followStroke`, and that is
    //: the live path; `canvas-core.js:2381` is not.
    //:
    //: Measured as the SPREAD of angles the kernel was actually handed, which is
    //: the quantity "rotates at every stamp" names.
    const angles = { held: [], following: [] };
    const realStamp = K.stamp;
    K.stamp = function (buffer, mark, r, dep, tipAngle) {
        angles._sink.push(tipAngle === undefined ? null : +tipAngle.toFixed(6));
        return realStamp.call(K, buffer, mark, r, dep, tipAngle);
    };
    function run(followStroke, sink) {
        angles._sink = sink;
        const S = makeState({ preset: "flat", ratio: 0.6, angleDeg: 45,
                              spikes: 2, density: 1, smoothing: 0 });
        S.brushDynamics.followStroke = followStroke;
        const CORE = { brushPx: () => S.brushSize };
        const toDoc = (x, y) => ({ x, y });
        const ev = (x, y, t) => ({ clientX: x, clientY: y, timeStamp: t,
                                   pressure: 0.5, pointerType: "mouse",
                                   tiltX: 0, tiltY: 0, twist: 0, buttons: 1,
                                   isPrimary: true });
        A._setEnabled(true);
        A.begin(S, CORE, ev(60, 210, 0), toDoc);
        //: An ARC, so a following tip genuinely has a changing heading and a
        //: held one genuinely must not move.
        for (let i = 1; i <= 40; i++) {
            const a = (i / 40) * Math.PI * 0.8;
            A.addFromEvent(S, ev(60 + 140 * Math.sin(a), 210 + 90 * (1 - Math.cos(a)),
                                 i * 16), toDoc);
        }
        A.finish(S, ev(200, 300, 700), toDoc);
        A._setEnabled(false);
    }
    run(false, angles.held);
    run(true, angles.following);
    K.stamp = realStamp;
    delete angles._sink;
    const spread = (xs) => {
        const v = xs.filter((x) => typeof x === "number");
        return v.length ? +(Math.max.apply(null, v) - Math.min.apply(null, v)).toFixed(4) : 0;
    };
    out.followStroke = {
        heldMarks: angles.held.length,
        heldOverrides: angles.held.filter((x) => x !== null).length,
        heldSpreadRad: spread(angles.held),
        followingMarks: angles.following.length,
        followingOverrides: angles.following.filter((x) => x !== null).length,
        followingSpreadRad: spread(angles.following),
        //: The opening mark has a null heading (`sampler.js:124`), so a following
        //: tip must leave it undefined rather than substitute 0.
        followingFirstIsUndefined: angles.following[0] === null,
    };
})();

(function () {
    //: Intercept the frozen deposition and the filter the adapter builds, so
    //: what is asserted is what the adapter actually handed the kernel -- not a
    //: re-reading of Studio state.
    const seen = [];
    const realDep = K.depositionFor;
    K.depositionFor = function (spec) {
        const d = realDep.call(K, spec);
        seen.push({ spec: spec, dep: d });
        return d;
    };
    const cases = [
        { label: "flat", preset: "flat", ratio: 1, angleDeg: 0, spikes: 2,
          density: 1, smoothing: 3 },
        { label: "calligraphy", preset: "flat", ratio: 0.6, angleDeg: 45,
          spikes: 2, density: 1, smoothing: 3 },
        { label: "bristle", preset: "flat", ratio: 0.55, angleDeg: 0, spikes: 6,
          density: 0.85, smoothing: 2 },
        { label: "pixel-perfect", preset: "round", ratio: 1, angleDeg: 0,
          spikes: 2, density: 1, smoothing: 0 },
        { label: "zoomed", preset: "round", ratio: 1, angleDeg: 0, spikes: 2,
          density: 1, smoothing: 4, zoom: 2.5 },
    ];
    out.adapter = cases.map(function (c) {
        seen.length = 0;
        const S = makeState(c);
        const CORE = { brushPx: () => S.brushSize };
        const toDoc = (x, y) => ({ x, y });
        const ev = (x, y, t) => ({ clientX: x, clientY: y, timeStamp: t,
                                   pressure: 0.5, pointerType: "mouse",
                                   tiltX: 0, tiltY: 0, twist: 0, buttons: 1,
                                   isPrimary: true });
        A._setEnabled(true);
        A.begin(S, CORE, ev(100, 200, 0), toDoc);
        for (let i = 1; i <= 8; i++) A.addFromEvent(S, ev(100 + i * 14, 200, i * 16), toDoc);
        A.finish(S, ev(220, 200, 160), toDoc);
        A._setEnabled(false);
        const first = seen[0] || { spec: {}, dep: {} };
        return {
            label: c.label,
            tipKind: first.dep.tip ? first.dep.tip.kind : null,
            ratio: first.dep.tip ? first.dep.tip.ratio : null,
            angleRad: first.dep.tip ? +first.dep.tip.angle.toFixed(6) : null,
            spikes: first.dep.tip ? first.dep.tip.spikes : null,
            density: first.dep.density,
            swept: first.spec.swept,
            painted: shapeOf(S.stroke.alphaMap).painted,
        };
    });
    K.depositionFor = realDep;
})();

// ── 6. smoothing reaches the filter ────────────────────────────────────────
(function () {
    const F = W.StudioBrushFiltersV2 || W.StudioBrushV2Filters;
    const seen = [];
    const Real = F && F.StrokeFilter;
    if (!Real) { out.smoothing = { error: "filters module not found" }; return; }
    F.StrokeFilter = function (spec) {
        seen.push({ mode: spec.mode, strength: spec.strength, scale: spec.scale });
        return new Real(spec);
    };
    F.StrokeFilter.prototype = Real.prototype;
    const rows = [];
    for (const c of [{ smoothing: 0, zoom: 1 }, { smoothing: 3, zoom: 1 },
                     { smoothing: 8, zoom: 1 }, { smoothing: 4, zoom: 2.5 }]) {
        seen.length = 0;
        const S = makeState({ preset: "round", ratio: 1, angleDeg: 0, spikes: 2,
                              density: 1, smoothing: c.smoothing, zoom: c.zoom });
        const CORE = { brushPx: () => 40 };
        A._setEnabled(true);
        A.begin(S, CORE, { clientX: 50, clientY: 50, timeStamp: 0, pressure: 0.5,
                           pointerType: "mouse", buttons: 1, isPrimary: true },
                (x, y) => ({ x, y }));
        A.cancel();
        A._setEnabled(false);
        rows.push(Object.assign({ asked: c.smoothing, zoom: c.zoom },
                                seen[0] || {}));
    }
    F.StrokeFilter = Real;
    out.smoothing = rows;
})();

process.stdout.write(JSON.stringify(out));
