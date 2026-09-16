/**
 * V2-04 probe: smoothing modes, corner preservation, and dynamics.
 *
 *     node tests/studio_alpha/v2_04_probe.js
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
                        "filters.js", "dynamics.js"]) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    return {
        F: g.window.StudioBrushFiltersV2,
        D: g.window.StudioBrushDynamicsV2,
        I: g.window.StudioBrushInputV2,
        S: g.window.StudioBrushSamplerV2,
        P: g.window.StudioBrushV2,
    };
}

const { F, D, I, S, P } = load();

const toDoc = (x, y) => ({ x: x, y: y });

function rawAt(x, y, t, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t, pressure: 0.6,
        tiltX: 0, tiltY: 0, twist: 0, pointerType: "pen", buttons: 1,
        isPrimary: true,
    }, over || {});
}

function canonical(records) {
    const input = new I.StrokeInput();
    return records.map(r => input.normalize(r, toDoc, null));
}

/** A straight run with a deterministic zig-zag tremor of +/- `amp` px. */
function tremor(n, amp) {
    const out = [];
    for (let i = 0; i < n; i++) {
        out.push(rawAt(100 + i * 4, 200 + (i % 2 ? amp : -amp), i * 8));
    }
    return canonical(out);
}

/** Distance of each point from the straight line y = 200. */
function deviation(samples) {
    let sum = 0, worst = 0;
    for (const s of samples) {
        const d = Math.abs(s.y - 200);
        sum += d;
        if (d > worst) worst = d;
    }
    return { mean: +(sum / samples.length).toFixed(6), worst: +worst.toFixed(6) };
}

const TREMOR = tremor(40, 3);

const raw = F.filterStream(TREMOR, { mode: F.MODE_RAW });
const natural = F.filterStream(TREMOR, { mode: F.MODE_NATURAL });
const stabilized = F.filterStream(TREMOR, { mode: F.MODE_STABILIZED, strength: 6 });

const sameAsInput = (out) =>
    JSON.stringify(out.map(s => [s.x, s.y, s.pressure]))
    === JSON.stringify(TREMOR.map(s => [s.x, s.y, s.pressure]));

// ---- corner preservation -------------------------------------------------
//
// A right-angle turn at (300, 200). A smoothing filter cuts inside it; corner
// preservation should pass closer to the true vertex.

const VERTEX = [300, 200];
function cornerPath() {
    const out = [];
    for (let x = 200; x <= 300; x += 4) out.push(rawAt(x, 200, out.length * 8));
    for (let y = 204; y <= 300; y += 4) out.push(rawAt(300, y, out.length * 8));
    return canonical(out);
}
const CORNER = cornerPath();

function nearestToVertex(samples) {
    let best = Infinity;
    for (const s of samples) {
        const d = Math.hypot(s.x - VERTEX[0], s.y - VERTEX[1]);
        if (d < best) best = d;
    }
    return +best.toFixed(6);
}

const cornerOff = F.filterStream(CORNER,
    { mode: F.MODE_STABILIZED, strength: 6, preserveCorners: false });
const cornerOn = F.filterStream(CORNER,
    { mode: F.MODE_STABILIZED, strength: 6, preserveCorners: true });

// ---- independence --------------------------------------------------------
//
// Position-only: pressure strength 0. Pressure must come through untouched.
// Pressure-only: position strength 0. Geometry must not move at all.

const varying = canonical(Array.from({ length: 30 }, (_, i) =>
    rawAt(100 + i * 4, 200 + (i % 2 ? 3 : -3), i * 8,
          { pressure: 0.2 + i * 0.02 })));

const positionOnly = F.filterStream(varying,
    { mode: F.MODE_STABILIZED, strength: 6, pressureStrength: 0 });
const pressureOnly = F.filterStream(varying,
    { mode: F.MODE_STABILIZED, strength: 0, pressureStrength: 6 });

const pressuresUnchanged =
    JSON.stringify(positionOnly.map(s => s.pressure))
    === JSON.stringify(varying.map(s => s.pressure));
const geometryUnchanged =
    JSON.stringify(pressureOnly.map(s => [s.x, s.y]))
    === JSON.stringify(varying.map(s => [s.x, s.y]));
const pressureActuallyMoved =
    JSON.stringify(pressureOnly.map(s => s.pressure))
    !== JSON.stringify(varying.map(s => s.pressure));
const geometryActuallyMoved =
    JSON.stringify(positionOnly.map(s => [s.x, s.y]))
    !== JSON.stringify(varying.map(s => [s.x, s.y]));

// ---- rate invariance of the window ---------------------------------------
//
// The same straight path reported at two rates. An arc-length window must
// smooth the same amount; a sample-count window would not.

// THE FIXED-VERTEX RULE AGAIN. The first draft built the two rates as
// alternating +/- 2 px per SAMPLE, which is not one path reported at two rates
// -- it is two different zig-zags, one with a 15 px period and one with 3.75.
// Measured: mean deviation 0.465 against 0.149, a difference entirely
// attributable to the input. The vertices are fixed here and subdivided, so
// both rates walk the identical saw-tooth and only report it more often.
const SAW = (function () {
    const v = [];
    for (let i = 0; i <= 20; i++) v.push([100 + i * 15, 200 + (i % 2 ? 2 : -2)]);
    return v;
})();

function subdivided(perSegment) {
    const out = [];
    let t = 0;
    out.push(rawAt(SAW[0][0], SAW[0][1], t));
    for (let s = 1; s < SAW.length; s++) {
        const [ax, ay] = SAW[s - 1], [bx, by] = SAW[s];
        for (let i = 1; i <= perSegment; i++) {
            const f = i / perSegment;
            t += 8 / perSegment;
            out.push(rawAt(ax + (bx - ax) * f, ay + (by - ay) * f, t));
        }
    }
    return canonical(out);
}
const coarse = F.filterStream(subdivided(1), { mode: F.MODE_STABILIZED, strength: 4 });
const fine = F.filterStream(subdivided(4), { mode: F.MODE_STABILIZED, strength: 4 });

// COMPARED AT CORRESPONDING VERTICES, not by averaging over each stream.
// The two streams contain different sample SETS of the same path, so a mean
// taken over each one weights different positions and would differ for a
// reason that has nothing to do with the filter. Sample `i` of the coarse
// stream and sample `4i` of the fine one are the filter's answers for the same
// input point, and an arc-length centroid must agree there: subdividing a
// segment changes how many terms the sum has, not what it adds up to.
const vertexDrift = (function () {
    let worst = 0;
    for (let i = 0; i < SAW.length; i++) {
        const a = coarse[i], b = fine[i * 4];
        if (!a || !b) continue;
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d > worst) worst = d;
    }
    return +worst.toFixed(9);
})();

// ---- endpoints under a lagging filter ------------------------------------

const lagged = F.filterStream(TREMOR, { mode: F.MODE_STABILIZED, strength: 6 });

// ---- dynamics ------------------------------------------------------------

const penCtx = {
    pressure: 0.5, pressureAvailable: true,
    tiltXDeg: 45, tiltYDeg: 0, tiltAvailable: true,
    headingRad: 0, speed: 1,
};
const mouseCtx = {
    pressure: 0.5, pressureAvailable: false,
    tiltXDeg: 0, tiltYDeg: 0, tiltAvailable: false,
    headingRad: 0, speed: null,
};

const sizeRule = D.ruleFromFeel("size", "pressure", "balanced",
                                { min: 0.2, max: 1.0, fallback: 1 });
const flowRule = D.ruleFromFeel("flow", "pressure", "balanced",
                                { min: 0.1, max: 1.0, fallback: 0.7 });
const angleRule = { input: "direction", target: "angle", curve: "linear",
                    min: 0, max: 90, fallback: 0 };

// A heading that yields a NON-ZERO factor. At heading 0 the direction input is
// 0, the factor is 0, and 0 + 0 is indistinguishable from 0 * 0 -- the first
// draft measured exactly that and the angle mutation survived it.
const turnedCtx = Object.assign({}, penCtx, { headingRad: Math.PI });

const report = {
    modes: {
        list: F.MODES.slice(),
        rawIsIdentity: sameAsInput(raw),
        naturalIsNotIdentity: !sameAsInput(natural),
        stabilizedIsNotIdentity: !sameAsInput(stabilized),
        deviation: {
            input: deviation(TREMOR),
            raw: deviation(raw),
            natural: deviation(natural),
            stabilized: deviation(stabilized),
        },
        unknownModeRefused: (() => {
            try { new F.StrokeFilter({ mode: "magic" }); return false; }
            catch (e) { return true; }
        })(),
    },

    endpoints: {
        // §5.1 -- the first sample is the contact point and the last is what
        // actually arrived, not a filtered lag.
        firstMatches: lagged[0].x === TREMOR[0].x && lagged[0].y === TREMOR[0].y,
        lastMatches:
            lagged[lagged.length - 1].x === TREMOR[TREMOR.length - 1].x
            && lagged[lagged.length - 1].y === TREMOR[TREMOR.length - 1].y,
        count: lagged.length,
        inputCount: TREMOR.length,
    },

    corners: {
        vertex: VERTEX,
        nearestOff: nearestToVertex(cornerOff),
        nearestOn: nearestToVertex(cornerOn),
        defaultIsOff: !(new F.StrokeFilter({ mode: F.MODE_NATURAL })
                        .preserveCorners),
    },

    independence: {
        pressuresUnchangedByPositionFilter: pressuresUnchanged,
        geometryUnchangedByPressureFilter: geometryUnchanged,
        // The calibration half: both filters must actually do something, or
        // "unchanged" is true of a filter that does nothing at all.
        pressureFilterMovedPressure: pressureActuallyMoved,
        positionFilterMovedGeometry: geometryActuallyMoved,
        pressureWindowFraction: F.PRESSURE_WINDOW_FRACTION,
    },

    rateInvariance: {
        vertexDrift: vertexDrift,
        coarseDeviation: deviation(coarse),
        fineDeviation: deviation(fine),
        // Calibration: the streams really are different lengths, so the
        // agreement above is between two genuinely different samplings.
        coarseCount: coarse.length,
        fineCount: fine.length,
    },

    dynamics: {
        inputs: D.INPUTS.slice(),
        targets: D.TARGETS.slice(),
        curves: Object.keys(D.CURVES).sort(),
        feels: Object.keys(D.FEEL_PRESETS).sort(),
        noRulesIsNeutral: D.evaluate([], penCtx) === D.NEUTRAL,
        penSize: +D.evaluate([sizeRule], penCtx).size.toFixed(6),
        // A mouse has no measured pressure, so the rule's OWN fallback applies.
        mouseSize: +D.evaluate([sizeRule], mouseCtx).size.toFixed(6),
        mouseFlow: +D.evaluate([flowRule], mouseCtx).flow.toFixed(6),
        // ANGLE ADDS, in degrees; everything else multiplies.
        angleAdds: +D.evaluate([angleRule, angleRule], turnedCtx).angle.toFixed(6),
        angleFromOneRule: +D.evaluate([angleRule], turnedCtx).angle.toFixed(6),
        sizeMultiplies: +D.evaluate([sizeRule, sizeRule], penCtx).size.toFixed(6),
        unknownTargetIgnored:
            D.evaluate([{ input: "pressure", target: "grain", curve: "linear" }],
                       penCtx).size,
        unknownInputIgnored:
            D.evaluate([{ input: "humidity", target: "size", curve: "linear" }],
                       penCtx).size,
        tiltUnavailableUsesFallback:
            +D.evaluate([{ input: "tilt", target: "size", curve: "linear",
                           min: 0, max: 1, fallback: 0.42 }],
                        mouseCtx).size.toFixed(6),
        speedUnavailableUsesFallback:
            +D.evaluate([{ input: "speed", target: "flow", curve: "linear",
                           min: 0, max: 1, fallback: 0.33 }],
                        mouseCtx).flow.toFixed(6),
        segmentSpeed: D.segmentSpeed(
            { x: 0, y: 0, timeUs: 0 }, { x: 100, y: 0, timeUs: 100000 }),
        sharedTimestampSpeedIsNull: D.segmentSpeed(
            { x: 0, y: 0, timeUs: 5 }, { x: 100, y: 0, timeUs: 5 }),
        // Two of the brief's five targets are deliberately absent.
        opacityIsNotATarget: D.TARGETS.indexOf("opacity") < 0,
        grainIsNotATarget: D.TARGETS.indexOf("grain") < 0,
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
