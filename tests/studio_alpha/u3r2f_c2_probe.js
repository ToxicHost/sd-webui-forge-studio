/**
 * U3-R2F C2 probe — same-contact union semantics.
 *
 * WHAT C2 CHANGED, AND WHAT THIS HAS TO PROVE.
 *
 * At Flow 1 the engine used to skip its accumulator entirely, leaving `peak` --
 * a MAX over the swept polyline -- as the whole deposition. `max(f(d1), f(d2))`
 * is `f(min(d1,d2))`, and the min of two distance fields has a gradient
 * discontinuity exactly on the medial axis between two arms of one stroke. That
 * is the crease the owner rejected. Deposition is now optical depth integrated
 * along the path, which is additive across a segment split and contains no max,
 * so there is no nearest-feature switch left to kink.
 *
 * Every measurement below drives the REAL modules through the REAL adapter. A
 * probe that reimplemented the kernel would certify its own arithmetic.
 *
 *     node tests/studio_alpha/u3r2f_c2_probe.js
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

const DOC = 900;

function makeState(o) {
    return {
        W: DOC, H: DOC,
        layers: [{ type: "paint", canvas: { width: DOC, height: DOC }, ctx: {},
                   visible: true, locked: false, opacity: 1 },
                 { type: "paint", canvas: { width: DOC, height: DOC }, ctx: {},
                   visible: true, locked: false, opacity: 1 }],
        activeLayerIdx: 1, tool: o.tool || "brush",
        editingMask: false, regionMode: false,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSizeMode: "document_pixels", brushSize: o.sizePx,
        brushOpacity: o.opacity === undefined ? 1 : o.opacity,
        brushHardness: o.hardness, brushFlow: o.flow,
        brushBuildup: !!o.buildup, brushSpacing: 0.15, brushPreset: "round",
        brushRatio: 1, brushAngle: 0, brushSpikes: 2, brushGrain: 0,
        brushFalloff: "default", brushDensity: 1, brushAliased: false,
        brushAirbrush: false, smoothing: 0,
        brushDynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                         rotationJitter: 0, followStroke: true, spacing: 0.15 },
        pressureSensitivity: (o.pressureAffects || "none") !== "none",
        pressureAffects: o.pressureAffects || "none",
        stroke: { alphaMap: new Uint8Array(DOC * DOC),
                  dirty: { x0: DOC, y0: DOC, x1: 0, y1: 0 },
                  frameDirty: { x0: DOC, y0: DOC, x1: 0, y1: 0 } },
    };
}

const toDoc = (x, y) => ({ x: x, y: y });
const ev = (x, y, t, p) => ({ clientX: x, clientY: y, timeStamp: t,
                              pressure: p === undefined ? 0.5 : p,
                              pointerType: "mouse", tiltX: 0, tiltY: 0, twist: 0,
                              buttons: 1, isPrimary: true });

/**
 * One contact over a list of waypoints.
 *
 * `perEvent` groups samples into coalesced batches, which is how the SAME
 * geometry is replayed at different event densities -- the invariant that
 * separates "a pass deposited twice" from "the browser reported twice".
 */
function contact(o) {
    const S = o.state || makeState(o);
    const CORE = { brushPx: () => o.sizePx };
    const pts = o.points;
    A._setEnabled(true);
    A.begin(S, CORE, ev(pts[0][0], pts[0][1], 0, pts[0][2]), toDoc);
    const per = o.perEvent || 1;
    let time = 16;
    for (let i = 1; i < pts.length; i += per) {
        const group = pts.slice(i, Math.min(pts.length, i + per));
        const last = group[group.length - 1];
        const e = ev(last[0], last[1], time, last[2]);
        if (group.length > 1) {
            e.getCoalescedEvents = () => group.map((q, k) =>
                ev(q[0], q[1], time - (group.length - 1 - k) * 4, q[2]));
        }
        A.addFromEvent(S, e, toDoc);
        time += 16;
    }
    const tail = pts[pts.length - 1];
    const summary = A.finish(S, ev(tail[0], tail[1], time, tail[2]), toDoc);
    A._setEnabled(false);
    return { map: S.stroke.alphaMap, state: S,
             marks: summary ? summary.marks : 0 };
}

function sample(t0, t1, n, fn) {
    const out = [];
    for (let i = 0; i <= n; i++) out.push(fn(t0 + (t1 - t0) * (i / n)));
    return out;
}

// ── the seam metric, identical in definition to `Evidence/.../look.py` ──────
const STEP = 4;
function seamOf(map, sizePx, hardness) {
    const r = Math.max(1, sizePx / 2);
    const h = Math.min(0.999, hardness);
    //: The threshold is DERIVED: twice the larger of the 8-bit rounding floor
    //: and the tip profile's own curvature at this hardness and radius. A fixed
    //: threshold reports the profile's curvature as seam at high hardness.
    const curv = 6 / ((1 - h) * (1 - h)) * 255 * STEP * STEP / (r * r);
    const th = Math.max(curv, 2) * 2;
    const lim = 2 * STEP;
    const axes = [[0, STEP], [STEP, 0], [STEP, STEP], [STEP, -STEP]];
    let seam = 0, maxK = 0, painted = 0;
    for (let y = lim; y < DOC - lim; y++) {
        for (let x = lim; x < DOC - lim; x++) {
            const i = y * DOC + x, v = map[i];
            if (!v) continue;
            painted += 1;
            if (v <= 4) continue;
            let k = 0;
            for (let q = 0; q < 4; q++) {
                const dy = axes[q][0], dx = axes[q][1];
                let d = map[(y + dy) * DOC + (x + dx)]
                      + map[(y - dy) * DOC + (x - dx)] - 2 * v;
                if (dy && dx) d /= 2;
                if (d > k) k = d;
            }
            if (k > maxK) maxK = k;
            if (k > th) seam += 1;
        }
    }
    return { seam, maxKink: maxK, painted, threshold: +th.toFixed(3) };
}

function stats(map) {
    let painted = 0, total = 0, peak = 0;
    for (let i = 0; i < map.length; i++) {
        const a = map[i];
        if (!a) continue;
        painted += 1; total += a;
        if (a > peak) peak = a;
    }
    return { painted, total, peak, mean: painted ? +(total / painted).toFixed(3) : 0 };
}

function diff(a, b) {
    let differing = 0, maxDelta = 0;
    for (let i = 0; i < a.length; i++) {
        const d = Math.abs(a[i] - b[i]);
        if (d) { differing += 1; if (d > maxDelta) maxDelta = d; }
    }
    return { differing, maxDelta };
}

const out = {};

// ── 1. crossings at three angles ───────────────────────────────────────────
const SIZE = 160, HARD = 0, R = SIZE / 2;
function crossPoints(deg) {
    const rad = deg * Math.PI / 180;
    const L = 320, cx = DOC / 2, cy = DOC / 2;
    //: Arm 1 horizontal, arm 2 at `deg`, joined by a connector that runs well
    //: outside the measurement window so it cannot contribute to it.
    const a1 = sample(0, 1, 60, (t) => [cx - L + 2 * L * t, cy]);
    const far = [[cx + L, cy], [cx + L + 260, cy - 300]];
    const a2 = sample(0, 1, 60, (t) => [
        cx + L * Math.cos(rad + Math.PI) * (1 - 2 * t) * -1,
        cy + L * Math.sin(rad + Math.PI) * (1 - 2 * t) * -1]);
    return a1.concat(far).concat(a2);
}
out.crossings = [90, 45, 20].map((deg) => {
    const c = contact({ sizePx: SIZE, hardness: HARD, flow: 1, points: crossPoints(deg) });
    return Object.assign({ deg }, seamOf(c.map, SIZE, HARD));
});

// ── 2. one contact versus two, identical arm geometry ──────────────────────
(function () {
    const cx = DOC / 2, cy = DOC / 2, L = 300;
    const armA = sample(0, 1, 60, (t) => [cx - L + 2 * L * t, cy - L + 2 * L * t]);
    const armB = sample(0, 1, 60, (t) => [cx + L - 2 * L * t, cy - L + 2 * L * t]);
    const connector = [[cx + L, cy + L], [cx + L + 300, cy - L - 300], [cx + L, cy - L]];
    const one = contact({ sizePx: SIZE, hardness: HARD, flow: 1,
                          points: armA.concat(connector).concat(armB) });
    const s1 = contact({ sizePx: SIZE, hardness: HARD, flow: 1, points: armA });
    const s2 = contact({ sizePx: SIZE, hardness: HARD, flow: 1, points: armB });
    //: `commitStroke` composites a finished contact source-over, which for
    //: alpha is `a + b(1-a)`. Two contacts are combined the same way here.
    const two = new Uint8Array(DOC * DOC);
    for (let i = 0; i < two.length; i++) {
        const a = s1.map[i], b = s2.map[i];
        two[i] = a + Math.round(b * (255 - a) / 255);
    }
    //: Only the crossing neighbourhood. The connector and the two caps belong
    //: to the one-contact figure alone and are not part of the claim.
    //: A DISC, AND THE SCAN STOPS SHORT OF ITS EDGE. Zeroing a rectangle and
    //: scanning the whole plane counted the rectangle's own edge as seam -- the
    //: metric cannot tell an artificial cut from a crease, so the cut is kept
    //: outside the scanned region rather than explained away.
    function windowSeam(map) {
        const keep = Math.round(R * 1.4), scan = keep - 2 * STEP - 2;
        const th = seamOf(map, SIZE, HARD).threshold;
        const axes = [[0, STEP], [STEP, 0], [STEP, STEP], [STEP, -STEP]];
        let seam = 0, maxK = 0, painted = 0;
        for (let y = cy - scan; y <= cy + scan; y++) {
            for (let x = cx - scan; x <= cx + scan; x++) {
                if (Math.hypot(x - cx, y - cy) > scan) continue;
                const i = y * DOC + x, v = map[i];
                if (!v) continue;
                painted += 1;
                if (v <= 4) continue;
                let k = 0;
                for (let q = 0; q < 4; q++) {
                    const dy = axes[q][0], dx = axes[q][1];
                    let d = map[(y + dy) * DOC + (x + dx)]
                          + map[(y - dy) * DOC + (x - dx)] - 2 * v;
                    if (dy && dx) d /= 2;
                    if (d > k) k = d;
                }
                if (k > maxK) maxK = k;
                if (k > th) seam += 1;
            }
        }
        return { seam, maxKink: maxK, painted, threshold: th };
    }
    out.oneVsTwo = {
        one: windowSeam(one.map),
        two: windowSeam(two),
        alphaAtCrossing: { one: one.map[cy * DOC + cx], two: two[cy * DOC + cx] },
    };
})();

// ── 3. caps: opening, tap, final endpoint ──────────────────────────────────
out.caps = [];
for (const sizePx of [54, 160]) {
    for (const hardness of [0, 0.25, 0.85]) {
        for (const flow of [1, 0.995, 0.994, 0.5]) {
            const r = sizePx / 2, y = DOC / 2, x0 = 200, x1 = 700;
            const pts = sample(0, 1, 120, (t) => [x0 + (x1 - x0) * t, y]);
            const c = contact({ sizePx, hardness, flow, points: pts });
            const at = (x, yy) => c.map[yy * DOC + x];
            const dy = Math.round(r * 0.52);
            //: The cap's RIM against the body's rim at the same perpendicular
            //: offset. The centreline saturates and hides the defect.
            out.caps.push({
                sizePx, hardness, flow,
                openingRim: at(x0 + 3, y - dy),
                closingRim: at(x1 - 3, y - dy),
                bodyRim: at((x0 + x1) >> 1, y - dy),
                bodyAxis: at((x0 + x1) >> 1, y),
            });
        }
    }
}

// ── 4. an exact tap, and a tap that becomes a stroke ───────────────────────
(function () {
    const sizePx = 160, y = DOC / 2, x = DOC / 2;
    const tap = contact({ sizePx, hardness: 0, flow: 1, points: [[x, y]] });
    const moved = contact({ sizePx, hardness: 0, flow: 1,
                            points: sample(0, 1, 40, (t) => [x + 400 * t, y]) });
    //: The tap's own footprint must survive inside the moving stroke: a tap
    //: that shrinks once the pointer moves is the failure §7 forbids.
    let shrunk = 0, worst = 0;
    for (let i = 0; i < tap.map.length; i++) {
        if (!tap.map[i]) continue;
        const d = tap.map[i] - moved.map[i];
        if (d > 0) { shrunk += 1; if (d > worst) worst = d; }
    }
    out.tap = { tapStats: stats(tap.map), tapMarks: tap.marks,
                pixelsThatShrankOnMove: shrunk, worstShrink: worst };
})();

// ── 5. non-overlap fidelity, and the exact retrace ─────────────────────────
(function () {
    const sizePx = 160, y = DOC / 2;
    const straight = sample(0, 1, 120, (t) => [200 + 500 * t, y]);
    //: A retrace walks back over its own path, which IS a second geometric
    //: pass and must darken; a straight line is one pass and must not.
    const retrace = straight.concat(straight.slice().reverse());
    const one = contact({ sizePx, hardness: 0, flow: 1, points: straight });
    const back = contact({ sizePx, hardness: 0, flow: 1, points: retrace });
    out.retrace = {
        straight: Object.assign(stats(one.map), seamOf(one.map, sizePx, 0)),
        retraced: Object.assign(stats(back.map), seamOf(back.map, sizePx, 0)),
    };
})();

// ── 6. event-density invariance ────────────────────────────────────────────
(function () {
    const sizePx = 160, y = DOC / 2;
    const pts = sample(0, 1, 240, (t) => [
        200 + 500 * t, y + 60 * Math.sin(t * Math.PI * 2)]);
    const rows = [1, 2, 4, 8].map((per) => {
        const c = contact({ sizePx, hardness: 0, flow: 1, points: pts, perEvent: per });
        return { perEvent: per, stats: stats(c.map), map: c.map };
    });
    const base = rows[0].map;
    out.density = rows.map((r) => ({
        perEvent: r.perEvent, mean: r.stats.mean, painted: r.stats.painted,
        vsOnePerEvent: diff(base, r.map),
    }));
})();

// ── 7. flow continuity across the retired threshold ────────────────────────
(function () {
    const sizePx = 160, y = DOC / 2;
    const pts = sample(0, 1, 120, (t) => [200 + 500 * t, y]);
    out.flowContinuity = [0.99, 0.994, 0.995, 0.999, 1].map((flow) => {
        const c = contact({ sizePx, hardness: 0, flow, points: pts });
        const s = stats(c.map);
        return { flow, axis: c.map[y * DOC + 450], mean: s.mean, painted: s.painted };
    });
})();

// ── 8. opacity is applied once, and only at merge ──────────────────────────
(function () {
    const sizePx = 160, y = DOC / 2;
    const pts = sample(0, 1, 120, (t) => [200 + 500 * t, y]);
    //: Coverage is intrinsic: the stroke buffer must not carry Opacity at all,
    //: or the merge applies it a second time.
    out.opacity = [1, 0.5, 0.25].map((opacity) => {
        const c = contact({ sizePx, hardness: 0, flow: 1, opacity, points: pts });
        return { opacity, axis: c.map[y * DOC + 450], mean: stats(c.map).mean };
    });
})();

// ── 9. the accumulator does not survive a contact ──────────────────────────
(function () {
    const sizePx = 160, y = DOC / 2;
    const pts = sample(0, 1, 60, (t) => [200 + 300 * t, y]);
    const first = contact({ sizePx, hardness: 0, flow: 1, points: pts });
    const second = contact({ sizePx, hardness: 0, flow: 1, points: pts });
    out.reset = { identical: diff(first.map, second.map) };
})();

// ── 9b. sweep survives a deposition that was not declared swept ────────────
(function () {
    //: THE BUG THIS GUARDS. `depositionFor` builds the pass table only when
    //: `swept` is true, so a stamp train does not pay for one it never reads --
    //: but `sweep` is reachable with ANY deposition, and two probe suites
    //: called it with `swept: false` and crashed on a null table. `sweep`
    //: resolves the table itself now; this proves it runs and covers the same
    //: ground either way.
    const buf = new K.CoverageBuffer(DOC, DOC);
    const depNotSwept = K.depositionFor({
        hardness: 0, flow: 1, opacity: 1, density: 1, step: 0.3, swept: false,
    });
    let threw = null, touched = 0;
    try {
        touched = K.sweep(buf, { x: 200, y: 450 }, { x: 500, y: 450 }, 80,
                          depNotSwept, depNotSwept);
    } catch (e) { threw = String(e); }

    const buf2 = new K.CoverageBuffer(DOC, DOC);
    const depSwept = K.depositionFor({
        hardness: 0, flow: 1, opacity: 1, density: 1, step: 0.3, swept: true,
    });
    K.sweep(buf2, { x: 200, y: 450 }, { x: 500, y: 450 }, 80, depSwept, depSwept);

    const read = function (b) {
        const o = new Uint8Array(DOC * DOC);
        for (let i = 0; i < o.length; i++) {
            const built = b.acc[i] >> 8;
            o[i] = built > b.peak[i] ? built : b.peak[i];
        }
        return o;
    };
    out.notSwept = {
        threw: threw, touched: touched,
        //: `overlapK` differs between the two (`profileMean` applies only to a
        //: stamp train), so deposited VALUES may differ -- the footprint, and
        //: whether it runs at all, may not.
        sameFootprint: stats(read(buf)).painted === stats(read(buf2)).painted,
    };
})();

// ── 9c. the cross-section IS the tip's profile, in absolute levels ─────────
(function () {
    //: EVERY OTHER MEASUREMENT HERE IS AN INVARIANCE OR A RATIO, and a mutation
    //: campaign found the hole: halving the `peak` floor moved 85,596 pixels by
    //: up to 126 levels and not one test noticed, because ratios and
    //: invariances are all preserved by a uniform scale. This compares the body
    //: cross-section against `shapeAt` itself, in levels.
    const sizePx = 160, r = sizePx / 2, y = DOC / 2, midX = 450;
    out.profile = [];
    for (const hardness of [0, 0.25, 0.85]) {
        for (const flow of [1, 0.5]) {
            const pts = sample(0, 1, 120, (t) => [200 + 500 * t, y]);
            const c = contact({ sizePx, hardness, flow, points: pts });
            let worst = 0, worstAt = -1;
            for (let dy = 0; dy <= r; dy++) {
                const measured = c.map[(y + dy) * DOC + midX];
                //: The mark axis sits on y exactly, so a pixel centre `dy` rows
                //: below it is `dy + 0.5` away.
                const expected = Math.round(
                    K.shapeAt((dy + 0.5) / r, hardness) * flow * 255);
                const d = Math.abs(measured - expected);
                if (d > worst) { worst = d; worstAt = dy; }
            }
            out.profile.push({ hardness, flow, worstDeviation: worst,
                               worstAtDy: worstAt });
        }
    }
})();

// ── 10. the model's own shape, read from the module ────────────────────────
out.model = {
    noAccumulateAbove: K.NO_ACCUMULATE_ABOVE,
    flow100Accumulates: K.depositionFor({
        hardness: 0, flow: 1, opacity: 1, density: 1, step: 0.3, swept: true,
    }).accumulating,
    flow050Accumulates: K.depositionFor({
        hardness: 0, flow: 0.5, opacity: 1, density: 1, step: 0.3, swept: true,
    }).accumulating,
    //: A swept contact's opening mark must contribute NO exposure -- the peak
    //: floor already carries its footprint -- while a genuine stamp train still
    //: needs its accumulator or flow stops building for textured tips.
    sweptHasPassTable: !!K.depositionFor({
        hardness: 0, flow: 1, opacity: 1, density: 1, step: 0.3, swept: true,
    }).passTable,
    stampedHasPassTable: !!K.depositionFor({
        hardness: 0, flow: 1, opacity: 1, density: 1, step: 0.3, swept: false,
    }).passTable,
};

process.stdout.write(JSON.stringify(out));
