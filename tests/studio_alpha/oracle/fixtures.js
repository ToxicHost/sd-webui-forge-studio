/**
 * Deterministic recorded inputs. Geometry, not gestures-in-spirit.
 *
 * EVERY FIXTURE STATES ITS UNITS. Positions are document pixels, timestamps are
 * milliseconds from pen-down, pressure is 0..1. A fixture that left those
 * implicit would be reproducible only by the person who wrote it.
 *
 * THE EVENT-RATE FIXTURES ARE THE SAME POLYLINE, SUBDIVIDED. This is the one
 * trap in the whole corpus and it has caught this programme before: sampling a
 * curve at 15 points and at 120 points does not vary the event rate, it varies
 * the GEOMETRY -- a coarse polyline cuts every corner the fine one follows, so
 * the pixels differ for a reason that has nothing to do with rate. The vertices
 * are fixed first and then subdivided, so every rate walks exactly the same
 * path and only reports its position more often along it.
 */

"use strict";

/** Walk a fixed vertex list, reporting `perSegment` samples inside each leg. */
function subdivide(vertices, perSegment, hz, pressure) {
    const samples = [];
    const dt = 1000 / hz;
    let t = 0;
    for (let v = 1; v < vertices.length; v++) {
        const [ax, ay] = vertices[v - 1];
        const [bx, by] = vertices[v];
        for (let i = 1; i <= perSegment; i++) {
            const f = i / perSegment;
            t += dt;
            samples.push({
                x: ax + (bx - ax) * f,
                y: ay + (by - ay) * f,
                t: +t.toFixed(3),
                pressure: typeof pressure === "function"
                    ? pressure(f, v) : (pressure === undefined ? 1.0 : pressure),
            });
        }
    }
    return samples;
}

/** Back and forth along one corridor without lifting: working an area. */
function scrub(x0, x1, y, passes, perPass, hz) {
    const samples = [];
    const dt = 1000 / hz;
    let t = 0;
    for (let k = 0; k < passes; k++) {
        const forward = k % 2 === 0;
        for (let i = 1; i <= perPass; i++) {
            const f = forward ? i / perPass : 1 - i / perPass;
            t += dt;
            samples.push({ x: x0 + f * (x1 - x0), y, t: +t.toFixed(3), pressure: 1.0 });
        }
    }
    return samples;
}

const TARGET = { width: 320, height: 320 };

//: The straight corridor every deposition fixture uses, so their numbers are
//: comparable with each other and with the BE17 evidence.
const CORRIDOR = { x0: 80, x1: 240, y: 160 };

const FIXTURES = {
    straightPass: {
        id: "straightPass",
        description: "One clean pass, left to right, no overlap anywhere.",
        units: "document px, ms from pen-down, pressure 0..1",
        // DECLARED, not recovered from the samples. A contract that has to
        // place a measurement relative to the turn -- `corner-leaves-no-crease`
        // does -- must read the geometry rather than re-derive it from a
        // subdivided polyline, which is how a scan line ends up hardcoded at a
        // row that suited one fixture and missed the other.
        vertices: [[80, 160], [240, 160]],
        down: { x: 80, y: 160, t: 0, pressure: 1.0 },
        samples: subdivide([[80, 160], [240, 160]], 40, 120, 1.0),
        lift: { x: 240, y: 160, t: 333.333, pressure: 1.0 },
        expectBoundsWithin: { x0: 40, y0: 120, x1: 280, y1: 200 },
    },

    crossingPasses: {
        id: "crossingPasses",
        description: "A stroke that crosses itself at a right angle, one contact.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 80, y: 160, t: 0, pressure: 1.0 },
        samples: subdivide([[80, 160], [240, 160], [160, 160], [160, 80],
                            [160, 240]], 30, 120, 1.0),
        lift: { x: 160, y: 240, t: 1000, pressure: 1.0 },
        expectBoundsWithin: { x0: 40, y0: 40, x1: 280, y1: 280 },
    },

    workedArea: {
        id: "workedArea",
        description: "Eight passes over one corridor without lifting.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 80, y: 160, t: 0, pressure: 1.0 },
        samples: scrub(80, 240, 160, 8, 40, 120),
        lift: null,
        expectBoundsWithin: { x0: 40, y0: 120, x1: 280, y1: 200 },
    },

    singlePassForComparison: {
        id: "singlePassForComparison",
        description: "One pass over the SAME corridor as workedArea, so the "
            + "two differ only in how many times the brush went over it.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 80, y: 160, t: 0, pressure: 1.0 },
        samples: scrub(80, 240, 160, 1, 40, 120),
        lift: null,
        expectBoundsWithin: { x0: 40, y0: 120, x1: 280, y1: 200 },
    },

    tap: {
        id: "tap",
        description: "Pen down and up with no movement at all.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 160, y: 160, t: 0, pressure: 1.0 },
        samples: [],
        lift: { x: 160, y: 160, t: 12, pressure: 1.0 },
        expectBoundsWithin: { x0: 100, y0: 100, x1: 220, y1: 220 },
    },

    shortMark: {
        id: "shortMark",
        description: "A deliberate flick shorter than most brushes are wide. "
            + "Long-line fixtures pass endpoint tests that this one fails.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 150, y: 160, t: 0, pressure: 1.0 },
        samples: subdivide([[150, 160], [170, 160]], 4, 120, 1.0),
        lift: { x: 170, y: 160, t: 33.333, pressure: 1.0 },
        expectBoundsWithin: { x0: 100, y0: 110, x1: 220, y1: 210 },
    },

    fastLongLine: {
        id: "fastLongLine",
        description: "A fast drag: few samples over a long distance, which is "
            + "what a real quick stroke delivers.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 40, y: 60, t: 0, pressure: 1.0 },
        samples: subdivide([[40, 60], [280, 260]], 5, 60, 1.0),
        lift: { x: 280, y: 260, t: 83.333, pressure: 1.0 },
        expectBoundsWithin: { x0: 0, y0: 20, x1: 319, y1: 300 },
    },

    acuteCorner: {
        id: "acuteCorner",
        description: "A 150-degree turn: the geometry the crease appears on.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 70, y: 210, t: 0, pressure: 1.0 },
        // Out along +x, then back at 30 degrees above the return path: the
        // two arms sit close together, which is where max() cut a seam.
        vertices: [[70, 210], [160, 210],
                   [160 + 90 * Math.cos(Math.PI * 30 / 180),
                    210 - 90 * Math.sin(Math.PI * 30 / 180)]],
        samples: subdivide(
            [[70, 210], [160, 210],
             [160 + 90 * Math.cos(Math.PI * 30 / 180),
              210 - 90 * Math.sin(Math.PI * 30 / 180)]],
            40, 120, 1.0),
        lift: null,
        expectBoundsWithin: { x0: 10, y0: 80, x1: 300, y1: 280 },
    },

    rightAngle: {
        id: "rightAngle",
        description: "A 90-degree corner, the ordinary case.",
        units: "document px, ms from pen-down, pressure 0..1",
        vertices: [[80, 120], [220, 120], [220, 260]],
        down: { x: 80, y: 120, t: 0, pressure: 1.0 },
        samples: subdivide([[80, 120], [220, 120], [220, 260]], 40, 120, 1.0),
        lift: null,
        expectBoundsWithin: { x0: 30, y0: 70, x1: 280, y1: 310 },
    },

    stationaryHold: {
        id: "stationaryHold",
        description: "Pen down, held perfectly still. Airbrush deposition must "
            + "come from elapsed time, never from repeated pointer samples.",
        units: "document px, ms from pen-down, pressure 0..1",
        down: { x: 160, y: 160, t: 0, pressure: 1.0 },
        samples: [],
        holdMs: 500,
        lift: null,
        expectBoundsWithin: { x0: 60, y0: 60, x1: 260, y1: 260 },
    },
};

/**
 * THE SAME POLYLINE AT FOUR REPORTING RATES.
 *
 * Vertices fixed, then subdivided. Timestamps advance at the stated rate so an
 * engine that integrates over time sees an honest clock, and the total elapsed
 * time is identical across rates because the path and the speed are.
 */
const RATE_VERTICES = [[60, 200], [180, 200], [260, 120], [280, 240]];

function rateFixture(hz, perSegment) {
    return {
        id: `rate${hz}`,
        description: `The same fixed polyline reported at ${hz} Hz.`,
        units: "document px, ms from pen-down, pressure 0..1",
        hz,
        down: { x: 60, y: 200, t: 0, pressure: 1.0 },
        samples: subdivide(RATE_VERTICES, perSegment, hz, 1.0),
        lift: { x: 280, y: 240, t: null, pressure: 1.0 },
        expectBoundsWithin: { x0: 10, y0: 60, x1: 319, y1: 300 },
    };
}

//: One second of travel at every rate: 30 Hz reports 10 samples per leg,
//: 240 Hz reports 80, and the polyline is identical.
const RATE_FIXTURES = [
    rateFixture(30, 10),
    rateFixture(60, 20),
    rateFixture(120, 40),
    rateFixture(240, 80),
];

for (const f of RATE_FIXTURES) {
    f.lift.t = f.samples[f.samples.length - 1].t;
}

module.exports = { FIXTURES, RATE_FIXTURES, TARGET, CORRIDOR, subdivide, scrub };
