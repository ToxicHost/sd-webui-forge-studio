/**
 * U3-G probe — the gaussian falloff, and the thirteen presets it must not touch.
 *
 * THE RISK THIS UNIT CARRIES. Airbrush, Ink Wash and Charcoal are round with
 * ratio 1, so they take RENDER_SWEEP -- and the sweep is where C2's crease
 * repair lives. Making the sweep's optical-depth table profile-aware means
 * editing the most delicate code in the kernel, and getting it wrong
 * reintroduces the owner's originating complaint on three presets.
 *
 * So §3 measures the crease directly rather than trusting that the table still
 * works, and §1 proves the smoothstep path is untouched by BYTE COMPARISON
 * rather than by the argument that it ought to be.
 *
 *     node tests/studio_alpha/u3g_gaussian_probe.js
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
const DOC = 420;

function readAlpha(buf) {
    const out = new Uint8Array(DOC * DOC);
    for (let i = 0; i < out.length; i++) {
        const b = buf.acc[i] >> 8;
        out[i] = b > buf.peak[i] ? b : buf.peak[i];
    }
    return out;
}

function painted(map) {
    let n = 0;
    for (let i = 0; i < map.length; i++) if (map[i]) n += 1;
    return n;
}

function dep(o) {
    return K.depositionFor(Object.assign({
        hardness: 0.5, flow: 1, opacity: 1, density: 1,
        step: 0.3, swept: true,
    }, o));
}

/** A straight swept stroke, the C2 fixture. */
function sweepStroke(o) {
    const buf = new K.CoverageBuffer(DOC, DOC);
    const d = dep(o);
    K.sweep(buf, { x: 70, y: DOC / 2 }, { x: DOC - 70, y: DOC / 2 }, 40, d);
    return readAlpha(buf);
}

/** A stroke that crosses itself -- the shape the crease showed up on. */
function crossStroke(o) {
    const buf = new K.CoverageBuffer(DOC, DOC);
    const d = dep(o);
    const pts = [];
    for (let i = 0; i <= 48; i++) {
        const t = i / 48;
        pts.push({ x: 90 + t * (DOC - 180), y: DOC / 2 + Math.sin(t * Math.PI * 2) * 70 });
    }
    for (let i = 1; i < pts.length; i++) K.sweep(buf, pts[i - 1], pts[i], 26, d);
    return readAlpha(buf);
}

/**
 * THE SEAM METRIC. A crease is a local DIP along the stroke's spine: the
 * centre line should not get darker and lighter as it travels.
 *
 * Reported as the worst single-step drop, in alpha, scanning the spine. C2 took
 * the owner's stroke from 115,319 to 1,044 on the equivalent measure.
 */
function spineDip(map, y) {
    let worst = 0, prev = -1;
    for (let x = 1; x < DOC - 1; x++) {
        const v = map[y * DOC + x];
        if (v === 0) { prev = -1; continue; }
        if (prev >= 0 && prev - v > worst) worst = prev - v;
        prev = v;
    }
    return worst;
}

const out = {};

// ── 1. THE THIRTEEN. Byte identity, not tolerance ──────────────────────────
(function () {
    //: `SMOOTHSTEP.floor` returns `hardness`, so every expression in
    //: `buildPassTable` reduces to what was already there. That is an argument;
    //: this is the measurement. Compared against a deposition that names no
    //: falloff at all, which is what every caller passed before U3-G.
    const cases = [];
    for (const hardness of [0, 0.25, 0.5, 0.85, 1]) {
        for (const flow of [0.3, 1]) {
            const bare = sweepStroke({ hardness, flow });
            const named = sweepStroke({ hardness, flow, falloff: "default" });
            const unknown = sweepStroke({ hardness, flow, falloff: "wobble" });
            let d1 = 0, d2 = 0, maxDelta = 0;
            for (let i = 0; i < bare.length; i++) {
                if (bare[i] !== named[i]) d1 += 1;
                if (bare[i] !== unknown[i]) d2 += 1;
                const dd = Math.abs(bare[i] - named[i]);
                if (dd > maxDelta) maxDelta = dd;
            }
            cases.push({ hardness, flow, painted: painted(bare),
                         differingNamed: d1, differingUnknown: d2,
                         maxDelta });
        }
    }
    out.smoothstepUnchanged = cases;
})();

// ── 2. the ported bell IS Legacy's ─────────────────────────────────────────
(function () {
    //: Legacy's `dabAlphaGauss`, re-implemented here from `canvas-core.js:2178`
    //: independently of the kernel's copy, so this compares two readings of the
    //: same source rather than a function against itself.
    function erf(x) {
        const sign = x >= 0 ? 1 : -1;
        const ax = Math.abs(x);
        const t = 1 / (1 + 0.3275911 * ax);
        const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                        - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
        return sign * y;
    }
    function legacyGauss(dist, radius, hardness) {
        if (dist >= radius) return 0;
        const fade = Math.max(0.01, 1.0 - hardness * 0.85);
        const center = (2.5 * (6761.0 * fade - 10000.0)) / (1.41421356 * 6761.0 * fade);
        const alphafactor = 1.0 / (2.0 * erf(center));
        const distfactor = 1.41421356 * 12500.0 / (6761.0 * fade * radius);
        const d = dist * distfactor;
        return alphafactor * (erf(d + center) - erf(d - center));
    }
    let worst = 0, at = null;
    for (const h of [0, 0.1, 0.25, 0.5, 0.75, 0.85, 1]) {
        for (let i = 0; i <= 100; i++) {
            const nd = i / 100;
            //: Legacy takes a radius; ours is normalised. Radius 100 so
            //: `dist / radius` is exactly `nd`.
            const a = K.shapeAtGauss(nd, h);
            const b = legacyGauss(nd * 100, 100, h);
            const e = Math.abs(a - b);
            if (e > worst) { worst = e; at = { hardness: h, nd }; }
        }
    }
    out.matchesLegacy = { worstAbsoluteError: +worst.toExponential(3), at };
    //: The property that decided the design: a gaussian has NO plateau, so the
    //: pass table's shoulder-only domain would be the wrong domain for it.
    out.noPlateau = {
        gaussAtHardness050: +K.shapeAtGauss(0.5, 0.5).toFixed(4),
        gaussAtHardness085: +K.shapeAtGauss(0.85, 0.85).toFixed(4),
        smoothstepAtHardness050: +K.shapeAt(0.5, 0.5).toFixed(4),
        floorSmoothstep: K.profileFor("default").floor(0.5),
        floorGaussian: K.profileFor("gaussian").floor(0.5),
    };
})();

// ── 3. THE CREASE, on the renderer this unit had to modify ─────────────────
(function () {
    //: The whole risk of U3-G. If the profile-aware table is wrong, a swept
    //: gaussian stroke creases -- and Airbrush, Ink Wash and Charcoal all sweep.
    //: Measured against the same stroke with the smoothstep, which C2 already
    //: proved clean, so the comparison is like for like.
    const rows = [];
    for (const hardness of [0, 0.5, 0.85]) {
        const s = crossStroke({ hardness, flow: 1 });
        const g = crossStroke({ hardness, flow: 1, falloff: "gaussian" });
        rows.push({
            hardness,
            smoothstepWorstDip: spineDip(s, DOC / 2),
            gaussianWorstDip: spineDip(g, DOC / 2),
            smoothstepPainted: painted(s),
            gaussianPainted: painted(g),
        });
    }
    out.crease = rows;
})();

// ── 4. the bell actually reaches the pixels ────────────────────────────────
(function () {
    //: A softer profile at the same hardness deposits less total alpha and a
    //: wider, fainter rim. If the two were identical the wiring did not land --
    //: which is exactly what "V2 rendered every tip with the smoothstep" was.
    function total(map) { let t = 0; for (let i = 0; i < map.length; i++) t += map[i]; return t; }
    const rows = [];
    for (const hardness of [0.25, 0.5, 0.85]) {
        const s = sweepStroke({ hardness, flow: 1 });
        const g = sweepStroke({ hardness, flow: 1, falloff: "gaussian" });
        rows.push({ hardness,
                    smoothstepTotal: total(s), gaussianTotal: total(g),
                    ratio: +(total(g) / total(s)).toFixed(4) });
    }
    out.bellIsSofter = rows;
    //: The stamp train's along-travel weight. Legacy computes a gaussian one by
    //: numeric integration; a smoothstep mean here would over-deposit.
    out.profileMean = {
        smoothstepAt050: K.depositionFor({ hardness: 0.5, flow: 1, opacity: 1,
                                           density: 1, step: 0.3, swept: false }).overlapK,
        gaussianAt050: K.depositionFor({ hardness: 0.5, flow: 1, opacity: 1,
                                         density: 1, step: 0.3, swept: false,
                                         falloff: "gaussian" }).overlapK,
    };
})();

// ── 5. the table cache cannot serve one profile's table to the other ───────
(function () {
    //: Two tips at the same hardness and flow, differing only in falloff. A
    //: cache keyed without the profile would hand the second the first's table
    //: -- a hit that paints the wrong brush, and only in a session where both
    //: presets are used.
    const a = K.depositionFor({ hardness: 0.5, flow: 0.6, opacity: 1, density: 1,
                                step: 0.3, swept: true });
    const b = K.depositionFor({ hardness: 0.5, flow: 0.6, opacity: 1, density: 1,
                                step: 0.3, swept: true, falloff: "gaussian" });
    out.tablesAreDistinct = {
        sameObject: a.passTable === b.passTable,
        smoothstepFloor: a.passTable ? a.passTable.floor : null,
        gaussianFloor: b.passTable ? b.passTable.floor : null,
    };
})();

process.stdout.write(JSON.stringify(out, null, 1));
