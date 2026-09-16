/**
 * U3-D probe — Density as a stipple, measured in pixels.
 *
 * WHAT THIS HAS TO PROVE. V2 folded Density into `overlapK`, the accumulation
 * count, so a low-density preset deposited fewer and DARKER contributions and
 * removed nothing. Legacy stipples: `canvas-core.js:2604` skips a pixel on
 * `Math.random() > density`. The four shipping presets that declare density
 * below 1.0 diverged from Legacy by a median of 353% of painted pixels while
 * the other twelve sat at 24%.
 *
 * THE PART A NAIVE TEST MISSES is the same one BE7 records. Legacy re-rolls per
 * dab, so what an owner sees is the UNION over every dab covering a pixel, and
 * it climbs as spacing tightens even though the control has not moved --
 * measured at Density 0.35, spacing 0.32 gave 0.698 and spacing 0.02 gave
 * 1.000. A test that stipples one dab and checks the fraction proves nothing
 * about a stroke, which is what the control is for. So §3 sweeps BE7's own
 * spacings.
 *
 *     node tests/studio_alpha/u3d_density_probe.js
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
        hardness: 1, flow: 1, opacity: 1, density: 1,
        step: 0.3, swept: false, seed: 0,
    }, o));
}

/*
 * THE DAB INDEX IS AN ARGUMENT, AND OMITTING IT MEASURED THE WRONG ENGINE.
 *
 * `stamp` and `sweep` take `dabIndex` as their last parameter and the adapter
 * always supplies it (`_placeMarks`: `const j = st.markIndex++`). This probe
 * did not, so `dabIndex` was `undefined` for every dab of every stroke it
 * measured, and in `depositWith` that means BOTH of:
 *
 *     (dabIndex ? stipple.p : stipple.pOpening)   -> always pOpening
 *     (stipple.seed ^ ((dabIndex | 0) * K)) | 0   -> always the same hash
 *
 * which is U3-D **v1** -- one draw per (pixel, stroke), never re-rolled. The
 * owner rejected that build ("real bad": a flat dither with a saturated core).
 * U3-D2 restored the per-dab draw and BE7's correction with it, and the probe
 * went on measuring the implementation that was replaced -- reporting a union
 * of exactly 0.3455 at all four spacings, which is v1's signature, not this
 * engine's. Deleting the correction outright left every test green.
 *
 * Every call below passes an index, so what is measured is what paints.
 */

/** One dab. A tap: the case BE7's spacing correction gets wrong. */
function tap(density, seed) {
    const buf = new K.CoverageBuffer(DOC, DOC);
    //: Index 0 EXPLICITLY. A tap is the opening dab, so `pOpening` is the right
    //: probability -- but it was previously reached by `undefined` being falsy,
    //: which is the same accident that hid the fault above.
    K.stamp(buf, { x: DOC / 2, y: DOC / 2 }, 60,
            dep({ density: density, seed: seed || 0 }), undefined, 0);
    return readAlpha(buf);
}

/**
 * A STROKE, as a train of stamps at a given spacing.
 *
 * `step` is in radii and `2/step` is the overlap, so this is the geometry BE7's
 * correction was written against. Straight, so every SPINE pixel is covered by
 * the same number of dabs -- see `spine` for why that qualifier matters.
 */
function strokeTrain(density, step, seed) {
    const buf = new K.CoverageBuffer(DOC, DOC);
    const d = dep({ density: density, step: step, seed: seed || 0 });
    const r = 30;
    const advance = r * step;
    let j = 0;
    for (let x = 80; x <= DOC - 80; x += advance) {
        K.stamp(buf, { x: x, y: DOC / 2 }, r, d, undefined, j);
        j += 1;
    }
    return readAlpha(buf);
}

/*
 * THE SPINE, and why the whole footprint cannot carry BE7's claim.
 *
 * BE7's correction makes the union equal the requested density for a pixel
 * covered by the NOMINAL `2/step` dabs. Only the spine is: a pixel at
 * perpendicular offset `dy` is reached by dabs within `sqrt(r^2 - dy^2)`
 * horizontally, so the overlap falls away from the centre line and drops to
 * nothing at the rim, and the ends see half a footprint. Averaged over the
 * whole footprint the kept fraction therefore lands BELOW the request -- 0.285
 * for 0.35, measured -- by a geometric factor that has nothing to do with
 * spacing.
 *
 * So the two claims are measured on the two windows that can carry them:
 *
 *     spine   the union tracks the requested density        (this function)
 *     whole   the union does not move with spacing          (`painted`)
 *
 * +/-3 rows keeps the overlap within 0.5% of nominal (`sqrt(900-9)/30`) and
 * still gives 980 samples, so the binomial noise is about 0.015.
 */
const SPINE_HALF = 3;
const SPINE_X0 = 140;
const SPINE_X1 = DOC - 140;

function spine(map) {
    let on = 0, total = 0;
    const y0 = (DOC / 2) | 0;
    for (let dy = -SPINE_HALF; dy <= SPINE_HALF; dy++) {
        for (let x = SPINE_X0; x < SPINE_X1; x++) {
            total += 1;
            if (map[(y0 + dy) * DOC + x]) on += 1;
        }
    }
    return { on: on, total: total };
}

const out = {};

// ── 1. the control is OFF above the threshold, and off means untouched ──────
(function () {
    //: Twelve of the sixteen shipping presets sit at density 1.0. Not "close
    //: to" the old output -- IDENTICAL, because `depositionFor` returns a null
    //: stipple and the hot loop takes the same branch it always took.
    const a = tap(1.0), b = tap(1.0, 99);
    let differing = 0;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) differing += 1;
    out.offIsOff = {
        paintedAtDensity1: painted(a),
        //: The seed cannot matter when the control is off. If it does, the
        //: threshold is wrong and every solid preset is now stochastic.
        differingAcrossSeeds: differing,
        stippleIsNull: dep({ density: 1.0 }).stipple === null,
        stippleIsNullAt099: dep({ density: 0.99 }).stipple === null,
        stippleExistsAt098: dep({ density: 0.98 }).stipple !== null,
    };
})();

// ── 2. THE TAP. Legacy's own failure case ───────────────────────────────────
(function () {
    //: Bristle Rake declares 0.85 and Legacy paints 132 px of ~1800 for an
    //: isolated dab -- `1 - 0.15^(1/25) = 7.3%` -- because its overlap comes
    //: from the SPACING SETTING rather than from what actually overlaps. A tap
    //: overlaps nothing, so the fraction kept should be the declared density.
    const solid = painted(tap(1.0));
    const rows = {};
    for (const d of [0.85, 0.5, 0.25]) {
        rows[String(d)] = +(painted(tap(d)) / solid).toFixed(4);
    }
    out.tapKeepsItsDeclaredFraction = { solid: solid, kept: rows };
})();

// ── 3. BE7's sweep. The union must not climb with spacing ───────────────────
(function () {
    //: THE MEASUREMENT BE7 MADE, repeated against this implementation. Legacy
    //: at Density 0.35 read 0.698 / 0.902 / 0.999 / 1.000 across these four
    //: spacings before its correction -- a spread of 0.302 over a 16x range.
    //:
    //: The draw DOES re-roll per dab here, so the correction is load-bearing
    //: rather than redundant. What holds it flat is `p`, which pre-shrinks the
    //: per-dab probability by `1/overlap` exactly as BE7 derived.
    const solidAt = {};
    const keptAt = {};
    const spineAt = {};
    for (const sp of [0.32, 0.16, 0.04, 0.02]) {
        const step = sp * 2;   //: `step` is in radii; spacing is the fraction
        const solid = painted(strokeTrain(1.0, step));
        solidAt[String(sp)] = solid;
        keptAt[String(sp)] = +(painted(strokeTrain(0.35, step)) / solid).toFixed(4);
        //: The window where the overlap IS the nominal one. See `spine`.
        const sSolid = spine(strokeTrain(1.0, step));
        const sKept = spine(strokeTrain(0.35, step));
        spineAt[String(sp)] = +(sKept.on / sSolid.on).toFixed(4);
    }
    out.unionAcrossSpacing = {
        requested: 0.35,
        solid: solidAt,
        //: Whole footprint. Carries the SPACING-INVARIANCE claim, not the
        //: absolute one: the rim and the two ends see fewer dabs, so this sits
        //: below the request by a geometric factor at every spacing alike.
        kept: keptAt,
        //: Spine. Carries the ABSOLUTE claim.
        spineKept: spineAt,
        spineSamples: spine(strokeTrain(1.0, 0.64)).total,
    };
})();

// ── 4. deterministic, and different per stroke ──────────────────────────────
(function () {
    const a = strokeTrain(0.5, 0.3, 7);
    const b = strokeTrain(0.5, 0.3, 7);
    const c = strokeTrain(0.5, 0.3, 8);
    let same = 0, diff = 0;
    for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) same += 1;
        if (a[i] !== c[i]) diff += 1;
    }
    out.determinism = {
        //: Same seed, same pixels. Legacy's `Math.random()` cannot say this,
        //: and `brushGrain`'s comment records what that cost: a comparison
        //: that reported 72% of pixels changed, almost all of it RNG drift.
        differingAcrossTwoRunsOfOneSeed: same,
        //: A second stroke must punch different holes or painting twice would
        //: never fill in.
        differingAcrossTwoSeeds: diff,
    };
})();

// ── 5. density is no longer spent on the accumulation count ────────────────
(function () {
    //: Folding it into `overlapK` made a low-density dab DARKER, which is the
    //: opposite of a stipple and would double-count against §2.
    out.overlapK = {
        atDensity1: dep({ density: 1.0 }).overlapK,
        atDensity025: dep({ density: 0.25 }).overlapK,
        //: `fEff` follows `overlapK`; if density still moved K it would move
        //: this too, and the surviving pixels would be the wrong alpha.
        fEffAtDensity1: +dep({ density: 1.0 }).fEff.toFixed(6),
        fEffAtDensity025: +dep({ density: 0.25 }).fEff.toFixed(6),
    };
})();

// ── 6. the sweep renderer stipples too ──────────────────────────────────────
(function () {
    //: Charcoal and Pastel are round, ratio 1, and `textured` is hard-coded
    //: false, so they take RENDER_SWEEP -- while Scatter Dust and Bristle Rake
    //: stamp. Stippling only the stamp path would fix two of the four presets
    //: and silently leave the other two, which is exactly the sort of half-port
    //: this programme keeps finding.
    //: A TRAIN OF SEGMENTS WITH REAL INDICES, not one segment with none. The
    //: adapter sweeps between consecutive marks and passes `j` to each
    //: (`V.sweep(..., st.lastDep || dep, dep, j)`), so a single indexless
    //: segment would exercise `pOpening` and one fixed hash -- the same blind
    //: spot the stamp path had.
    function swept(density) {
        const buf = new K.CoverageBuffer(DOC, DOC);
        const d = dep({ density: density, swept: true, step: 0.3, seed: 3 });
        const advance = 30 * 0.3;
        let j = 0;
        let prev = { x: 80, y: DOC / 2 };
        for (let x = 80 + advance; x <= DOC - 80; x += advance) {
            const next = { x: x, y: DOC / 2 };
            K.sweep(buf, prev, next, 30, d, d, j);
            prev = next;
            j += 1;
        }
        return { map: readAlpha(buf), segments: j };
    }
    const solid = swept(1.0);
    const half = swept(0.5);
    out.sweepStipples = {
        solid: painted(solid.map),
        segments: solid.segments,
        keptAt05: +(painted(half.map) / painted(solid.map)).toFixed(4),
        //: Same spine window as the stamp train, so the two renderers are
        //: compared on the quantity that carries the absolute claim.
        spineKeptAt05: +(spine(half.map).on / spine(solid.map).on).toFixed(4),
        renderer: K.rendererFor({ kind: "round", ratio: 1, textured: false }),
    };
})();

process.stdout.write(JSON.stringify(out, null, 1));
