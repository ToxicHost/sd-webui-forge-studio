/**
 * V2-03 probe: exercise the arc-length sampler and report what happened.
 *
 * THE FIXTURES FIX THEIR VERTICES FIRST AND SUBDIVIDE BETWEEN THEM, which is
 * `oracle/fixtures.js:8-14`'s rule and the one trap in this whole measurement:
 * sampling a curve at 15 points and at 120 points does not vary the event rate,
 * it varies the GEOMETRY, because a coarse polyline cuts every corner the fine
 * one follows. With the vertices fixed, every rate walks exactly the same path
 * and only reports its position more often along it -- so the frequency
 * comparison can be EXACT rather than toleranced.
 *
 *     node tests/studio_alpha/v2_03_probe.js
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
    for (const name of ["brush-contracts.js", "input.js", "sampler.js"]) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    return { S: g.window.StudioBrushSamplerV2, I: g.window.StudioBrushInputV2,
             C: g.window.StudioBrushV2 };
}

const { S, I, C } = load();

const toDoc = (x, y) => ({ x: x, y: y });

//: 20 px tip at 0.15 -> a 3.0 px gap, and the value is CALIBRATED rather than
//: chosen for roundness.
//:
//: At 0.25 the gap is 5.0, and every per-sample distance in these fixtures
//: (40, 20, 10, 5 px at 30/60/120/240 Hz) is an exact multiple of it. There is
//: then never a remainder to carry, so severing the residual carry changes
//: NOTHING and the frequency guard reports a pass. Measured: severed totals by
//: rate were [80, 80, 80, 80] at gap 5.0 and [80, 80, 80, 80] at 4.6, against
//: [130, 120, 120, 80] at 3.0.
//:
//: A metric that cannot separate the two cases it is about to call equal is
//: not a metric, and the mutation harness is what caught it.
const SPEC = { spacingFraction: 0.15, sizePx: 20 };

/** Raw device records along a fixed vertex list, `perSegment` per leg. */
function walk(vertices, perSegment, hz) {
    const out = [];
    const dt = 1000 / hz;
    let t = 0;
    out.push(rawAt(vertices[0][0], vertices[0][1], t));
    for (let v = 1; v < vertices.length; v++) {
        const [ax, ay] = vertices[v - 1];
        const [bx, by] = vertices[v];
        for (let i = 1; i <= perSegment; i++) {
            const f = i / perSegment;
            t += dt;
            out.push(rawAt(ax + (bx - ax) * f, ay + (by - ay) * f, t));
        }
    }
    return out;
}

function rawAt(x, y, t, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t, pressure: 0.6,
        tiltX: 0, tiltY: 0, twist: 0, pointerType: "pen", buttons: 1,
        isPrimary: true,
    }, over || {});
}

/** Raw records to canonical samples, one dispatch each. */
function canonical(records) {
    const input = new I.StrokeInput();
    return records.map(r => input.normalize(r, toDoc, null));
}

/** Raw records grouped into dispatches of `size`, through samplesFrom. */
function canonicalGrouped(records, size) {
    const input = new I.StrokeInput();
    const out = [];
    for (let i = 0; i < records.length; i += size) {
        const group = records.slice(i, i + size);
        const last = group[group.length - 1];
        const event = Object.assign({}, last);
        event.getCoalescedEvents = () => group.slice();
        const samples = input.samplesFrom(event, toDoc);
        for (const s of samples) out.push(s);
    }
    return out;
}

const LINE = [[100, 100], [400, 100]];
const CORNER = [[100, 100], [300, 100], [300, 300]];

function place(samples, spec) {
    return S.sampleStream(samples, spec || SPEC).dabs;
}

/** Positions only, rounded, for comparison. */
function positions(dabs) {
    return dabs.map(d => [+d.x.toFixed(6), +d.y.toFixed(6)]);
}

// ---- frequency invariance, exact by construction -------------------------

const rates = [30, 60, 120, 240];
const perSegment = { 30: 5, 60: 10, 120: 20, 240: 40 };
const byRate = {};
for (const hz of rates) {
    byRate[hz] = place(canonical(walk(CORNER, perSegment[hz], hz)));
}
const ratePositions = rates.map(hz => JSON.stringify(positions(byRate[hz])));
const rateCounts = rates.map(hz => byRate[hz].length);

// ---- dispatch grouping ---------------------------------------------------

const records = walk(CORNER, 20, 120);
const oneDispatch = place(canonicalGrouped(records, records.length));
const manyDispatch = place(canonicalGrouped(records, 3));
const perEvent = place(canonicalGrouped(records, 1));

// ---- endpoints -----------------------------------------------------------

function endpointsFor(vertices, perSeg) {
    const samples = canonical(walk(vertices, perSeg, 120));
    const dabs = place(samples);
    const first = dabs[0], last = dabs[dabs.length - 1];
    return {
        count: dabs.length,
        firstAt: [first.x, first.y],
        expectedFirst: [samples[0].x, samples[0].y],
        lastAt: [last.x, last.y],
        expectedLast: [samples[samples.length - 1].x,
                       samples[samples.length - 1].y],
        firstSource: first.source,
        lastSource: last.source,
    };
}

// A flick shorter than one gap: the endpoint rule is the only thing that can
// place its far end.
const flickSamples = canonical([rawAt(100, 100, 0), rawAt(102, 100, 8)]);
const flickDabs = place(flickSamples);

// A tap: down and up at one place, no movement at all.
const tapSamples = canonical([rawAt(160, 160, 0), rawAt(160, 160, 12)]);
const tapDabs = place(tapSamples);

// ---- sub-spacing moves keep their distance -------------------------------
//
// Twenty moves of 1px each, against one move of 20px. Same distance, same
// spacing: the debt must produce the same number of marks.

const crawl = canonical(Array.from({ length: 21 },
    (_, i) => rawAt(100 + i, 100, i * 4)));
const stride = canonical([rawAt(100, 100, 0), rawAt(120, 100, 80)]);
const crawlDabs = place(crawl);
const strideDabs = place(stride);

// ---- degenerate input ----------------------------------------------------

const repeated = canonical([
    rawAt(100, 100, 0), rawAt(100, 100, 5), rawAt(100, 100, 10),
    rawAt(100, 100, 15),
]);
const repeatedDabs = place(repeated);
const repeatedSampler = S.sampleStream(repeated, SPEC).sampler;

const sharedTime = canonical([
    rawAt(100, 100, 0), rawAt(150, 100, 0), rawAt(200, 100, 0),
]);
const sharedTimeDabs = place(sharedTime);

const allFinite = (dabs) => dabs.every(d =>
    isFinite(d.x) && isFinite(d.y) && isFinite(d.pressure)
    && isFinite(d.timeUs) && isFinite(d.gapPx));

// ---- interpolation -------------------------------------------------------
//
// Pressure rises 0.2 -> 1.0 over one long segment. A mark in the middle must
// carry a pressure BETWEEN the ends, not the newest sample's.

const ramp = canonical([
    rawAt(100, 100, 0, { pressure: 0.2 }),
    rawAt(400, 100, 100, { pressure: 1.0 }),
]);
const rampDabs = place(ramp);
const rampPressures = rampDabs.map(d => +d.pressure.toFixed(4));
const rampMonotonic = rampDabs.every(
    (d, i) => i === 0 || d.pressure >= rampDabs[i - 1].pressure - 1e-9);
const rampCopiedLatest = rampDabs.slice(1, -1)
    .every(d => Math.abs(d.pressure - 1.0) < 1e-9);

// ---- anisotropic spacing -------------------------------------------------
//
// A tip twice as long along x as across it. Travelling along x must earn a
// gap twice as wide as travelling along y, so half as many marks.

const chisel = {
    spacingFraction: 0.25, sizePx: 20,
    extentFor: (heading) => {
        const c = Math.cos(heading), s = Math.sin(heading);
        return Math.sqrt((2 * c) * (2 * c) + (1 * s) * (1 * s));
    },
};
const alongAxis = place(canonical(walk([[100, 100], [400, 100]], 30, 120)), chisel);
const acrossAxis = place(canonical(walk([[100, 100], [100, 400]], 30, 120)), chisel);

// ---- stationary ----------------------------------------------------------

const hold = canonical([
    rawAt(200, 200, 0), rawAt(200, 200, 100), rawAt(200, 200, 200),
]);
const holdRun = S.sampleStream(hold, SPEC);

const report = {
    frequency: {
        rates: rates,
        counts: rateCounts,
        allPositionsIdentical: ratePositions.every(p => p === ratePositions[0]),
        // "Too clean is the tell": a sampler emitting nothing would satisfy the
        // line above.
        markCount: rateCounts[0],
    },

    grouping: {
        oneDispatchCount: oneDispatch.length,
        manyDispatchCount: manyDispatch.length,
        perEventCount: perEvent.length,
        oneMatchesMany:
            JSON.stringify(positions(oneDispatch)) === JSON.stringify(positions(manyDispatch)),
        oneMatchesPerEvent:
            JSON.stringify(positions(oneDispatch)) === JSON.stringify(positions(perEvent)),
    },

    endpoints: {
        line: endpointsFor(LINE, 20),
        corner: endpointsFor(CORNER, 20),
        flick: {
            count: flickDabs.length,
            firstAt: [flickDabs[0].x, flickDabs[0].y],
            lastAt: [flickDabs[flickDabs.length - 1].x,
                     flickDabs[flickDabs.length - 1].y],
            lastSource: flickDabs[flickDabs.length - 1].source,
        },
        tap: {
            count: tapDabs.length,
            at: [tapDabs[0].x, tapDabs[0].y],
            source: tapDabs[0].source,
        },
    },

    subSpacing: {
        crawlCount: crawlDabs.length,
        strideCount: strideDabs.length,
        samePlacements:
            JSON.stringify(positions(crawlDabs)) === JSON.stringify(positions(strideDabs)),
    },

    degenerate: {
        repeatedCount: repeatedDabs.length,
        repeatedFinite: allFinite(repeatedDabs),
        repeatedStationaryUs: repeatedSampler.stationaryUs,
        sharedTimeCount: sharedTimeDabs.length,
        sharedTimeFinite: allFinite(sharedTimeDabs),
        sharedTimeAdvances: sharedTimeDabs.length > 1,
    },

    interpolation: {
        pressures: rampPressures,
        monotonic: rampMonotonic,
        // If the sampler copied the newest sample, every interior mark would
        // read 1.0.
        copiedLatestSample: rampCopiedLatest,
        firstPressure: rampPressures[0],
        lastPressure: rampPressures[rampPressures.length - 1],
    },

    anisotropy: {
        alongAxisCount: alongAxis.length,
        acrossAxisCount: acrossAxis.length,
        // Measured on a mark well clear of the seeded first gap, which has no
        // heading yet and is priced isotropically on purpose.
        alongGap: +alongAxis[5].gapPx.toFixed(6),
        acrossGap: +acrossAxis[5].gapPx.toFixed(6),
    },

    // A stroke whose length is NOT a whole number of gaps, so the endpoint can
    // only be placed by the finish rule. The fixtures above divide exactly and
    // would let a severed flush pass.
    unevenEndpoint: (function () {
        // 118 px of travel against a 3.0 px gap: 39 whole gaps and a
        // remainder, so the end can only be placed by the finish rule. At the
        // previous 5.0 px gap this fixture measured 117 px and divided exactly,
        // which would have let a severed flush pass.
        const samples = canonical([rawAt(100, 100, 0), rawAt(218, 100, 60)]);
        const dabs = place(samples);
        const last = dabs[dabs.length - 1];
        return {
            count: dabs.length,
            lastAt: [+last.x.toFixed(6), last.y],
            expectedLast: [218, 100],
            lastSource: last.source,
            lastGapPx: +last.gapPx.toFixed(6),
        };
    })(),

    stationary: {
        dabCount: holdRun.dabs.length,
        stationaryUs: holdRun.sampler.stationaryUs,
    },

    enumeration: {
        declared: S.DAB_FIELDS.slice().sort(),
        produced: Object.keys(byRate[120][1]).sort(),
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
