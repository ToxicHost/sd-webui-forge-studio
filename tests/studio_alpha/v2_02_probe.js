/**
 * V2-02 probe: exercise the input normalizer and report what happened.
 *
 * House pattern: a node driver loads the browser modules in a `vm` context
 * with a fake `window`, runs probes, and prints ONE JSON object for the Python
 * suite to assert on.
 *
 * NO DOM, NO REAL POINTEREVENT. Every record here is a duck-typed object with
 * the fields the normalizer reads. That is not a shortcut -- it is the same
 * shape a recorded fixture replays through, so the replay tests exercise the
 * production path rather than a parallel one.
 *
 *     node tests/studio_alpha/v2_02_probe.js
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
    for (const name of ["brush-contracts.js", "input.js"]) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    if (!g.window.StudioBrushInputV2) throw new Error("input.js exported nothing");
    return { I: g.window.StudioBrushInputV2, C: g.window.StudioBrushV2 };
}

const { I, C } = load();

function refusal(fn) {
    try { fn(); return null; } catch (e) { return String(e && e.message || e); }
}

/** Identity transform, so document space equals client space in the probe. */
const toDoc = (x, y) => ({ x: x, y: y });

/** One raw device record. `over` supplies whatever the case is about. */
function raw(over) {
    return Object.assign({
        clientX: 10, clientY: 20, timeStamp: 100, pressure: 0.5,
        tiltX: 0, tiltY: 0, twist: 0, pointerType: "pen", buttons: 1,
        isPrimary: true,
    }, over || {});
}

function fakeEvent(group, extra) {
    const last = group[group.length - 1];
    const event = Object.assign({}, last, extra || {});
    event.getCoalescedEvents = () => group.slice();
    return event;
}

/** A recorded contact: 12 samples along a line, grouped 4 + 5 + 3. */
function recordedGroups() {
    const all = [];
    for (let i = 0; i < 12; i++) {
        all.push(raw({
            clientX: 10 + i * 7, clientY: 20 + i * 3,
            timeStamp: 100 + i * 8, pressure: 0.3 + i * 0.05,
        }));
    }
    return [all.slice(0, 4), all.slice(4, 9), all.slice(9, 12)];
}

const GROUPS = recordedGroups();
const FLAT = I.flatten(GROUPS);

/** Canonical stream as plain JSON, for equality comparison. */
function streamOf(groups) {
    return I.replay(groups, toDoc).map(s => JSON.parse(JSON.stringify(s)));
}

const asRecorded = streamOf(GROUPS);
const asOneDispatch = streamOf([FLAT]);
const asSingles = streamOf(I.regroup(FLAT, 1));
const asThrees = streamOf(I.regroup(FLAT, 3));

function sameStream(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}

// ---- classification ------------------------------------------------------

const penSample = new I.StrokeInput().normalize(
    raw({ pointerType: "pen", pressure: 0.7, tiltX: 12, tiltY: -3 }), toDoc);
const mouseSample = new I.StrokeInput().normalize(
    raw({ pointerType: "mouse", pressure: 0.5 }), toDoc);
const touchMeasured = new I.StrokeInput().normalize(
    raw({ pointerType: "touch", pressure: 0.42 }), toDoc);
const touchConstant = new I.StrokeInput().normalize(
    raw({ pointerType: "touch", pressure: 1 }), toDoc);
const unknownDevice = new I.StrokeInput().normalize(
    raw({ pointerType: "spatial-controller", pressure: 0.9 }), toDoc);
const emptyType = new I.StrokeInput().normalize(
    raw({ pointerType: "", pressure: 0.9 }), toDoc);

// §3.3 #1 -- a primary pen and a primary touch at the same moment.
const bothPrimary = new I.StrokeInput();
const penWhileTouching = bothPrimary.normalize(
    raw({ pointerType: "pen", isPrimary: true, pressure: 0.8 }), toDoc);
const palmWhileDrawing = bothPrimary.normalize(
    raw({ pointerType: "touch", isPrimary: true, pressure: 1 }), toDoc);

// ---- coalesced expansion and ordering ------------------------------------

const ordering = new I.StrokeInput();
const orderGroup = [
    raw({ clientX: 1, timeStamp: 10 }),
    raw({ clientX: 2, timeStamp: 11 }),
    raw({ clientX: 3, timeStamp: 12 }),
];
const orderOut = ordering.samplesFrom(fakeEvent(orderGroup), toDoc);

// A null entry mid-list. CT2 would throw at `raw.clientX` and kill the stroke.
const withNull = new I.StrokeInput();
const nullResult = refusal(() => withNull.samplesFrom(
    fakeEvent([raw({ clientX: 1 }), null, raw({ clientX: 3, timeStamp: 101 })]),
    toDoc));
const nullSurvivors = withNull.sequence;

// getCoalescedEvents that throws.
const throwing = new I.StrokeInput();
const throwEvent = raw({ clientX: 55 });
throwEvent.getCoalescedEvents = () => { throw new Error("browser said no"); };
const throwOut = throwing.samplesFrom(throwEvent, toDoc);

// An empty coalesced list still paints. Built directly rather than through
// `fakeEvent`, which always installs its own `getCoalescedEvents` and would
// silently overwrite the empty one -- the first version of this probe did
// exactly that and reported the fallback as coalesced.
const emptyList = new I.StrokeInput();
const emptyEvent = raw({ clientX: 9 });
emptyEvent.getCoalescedEvents = () => [];
const emptyOut = emptyList.samplesFrom(emptyEvent, toDoc);

// §3.3 #5 -- predicted events, offered and ignored.
const predicted = new I.StrokeInput();
const predictedEvent = fakeEvent([raw({ clientX: 1, timeStamp: 10 })]);
predictedEvent.getPredictedEvents = () => [
    raw({ clientX: 999, clientY: 999, timeStamp: 20 }),
    raw({ clientX: 1000, clientY: 1000, timeStamp: 30 }),
];
const predictedOut = predicted.samplesFrom(predictedEvent, toDoc);

// ---- time and sequence ---------------------------------------------------

const backwards = new I.StrokeInput();
const backwardsOut = [
    backwards.normalize(raw({ timeStamp: 100 }), toDoc),
    backwards.normalize(raw({ timeStamp: 90, clientX: 11 }), toDoc),
    backwards.normalize(raw({ timeStamp: 120, clientX: 12 }), toDoc),
];

const shared = new I.StrokeInput();
const sharedTimeOut = [
    shared.normalize(raw({ timeStamp: 50, clientX: 1 }), toDoc),
    shared.normalize(raw({ timeStamp: 50, clientX: 2 }), toDoc),
];

// ---- field enumeration ---------------------------------------------------

const declared = C.SAMPLE_FIELDS.slice().sort();
const produced = Object.keys(penSample).sort();

const SOURCE = fs.readFileSync(path.join(V2, "input.js"), "utf8");

const report = {
    fixtureVersion: I.FIXTURE_VERSION,

    classification: {
        pen: penSample.pointer,
        mouse: mouseSample.pointer,
        touchMeasured: touchMeasured.pointer,
        touchConstant: touchConstant.pointer,
        unknownDevice: unknownDevice.pointer,
        emptyType: emptyType.pointer,
        // Both are produced, both keep their own kind, neither is dropped.
        simultaneous: {
            pen: penWhileTouching.pointer,
            touch: palmWhileDrawing.pointer,
            bothProduced: !!(penWhileTouching && palmWhileDrawing),
            sequences: [penWhileTouching.sequence, palmWhileDrawing.sequence],
        },
    },

    sensors: {
        penPressureMeasured: penSample.pressureAvailable,
        penPressureValue: penSample.pressure,
        mousePressureMeasured: mouseSample.pressureAvailable,
        mousePressureValue: mouseSample.pressure,
        // Strictly between 0 and 1 is a measurement; a constant 1 is contact.
        touchMeasuredFlag: touchMeasured.pressureAvailable,
        touchConstantFlag: touchConstant.pressureAvailable,
        touchConstantValue: touchConstant.pressure,
        unknownPressureMeasured: unknownDevice.pressureAvailable,
        unknownPressureValue: unknownDevice.pressure,
        configuredMousePressure: new I.StrokeInput({ mousePressure: 0.25 })
            .normalize(raw({ pointerType: "mouse" }), toDoc).pressure,
        penTiltAvailable: penSample.tiltAvailable,
        mouseTiltAvailable: mouseSample.tiltAvailable,
        penTilt: [penSample.tiltXDeg, penSample.tiltYDeg],
        // Absent rather than fabricated.
        azimuthAbsent: penSample.azimuthRad,
        // `twist` reaches the contract in RADIANS. Legacy normalises it and
        // reads it nowhere.
        barrelFromTwist: new I.StrokeInput()
            .normalize(raw({ twist: 180 }), toDoc).barrelRotationRad,
        // A sample with no usable pressure is still a sample.
        missingPressureStillProduces:
            !!new I.StrokeInput().normalize(
                raw({ pointerType: "mouse", pressure: undefined }), toDoc),
    },

    coalesced: {
        orderPreserved: orderOut.map(s => s.x),
        sequences: orderOut.map(s => s.sequence),
        allMarkedCoalesced: orderOut.every(s => s.coalesced === true),
        // A degraded path is distinguishable from a genuine one-sample list.
        throwFallbackCount: throwOut.length,
        throwFallbackCoalesced: throwOut[0].coalesced,
        emptyFallbackCount: emptyOut.length,
        emptyFallbackCoalesced: emptyOut[0].coalesced,
        nullEntryThrew: nullResult,
        nullEntrySurvivors: nullSurvivors,
    },

    predicted: {
        // Offered two predicted samples at (999,999) and (1000,1000).
        producedCount: predictedOut.length,
        maxX: Math.max.apply(null, predictedOut.map(s => s.x)),
        // The SOURCE check is deliberately not here. The module names
        // `getPredictedEvents` in a comment explaining why it never calls it,
        // so a text scan reports a hit and would fail the guard that documents
        // itself -- the seventh time this repository has hit that trap. The
        // Python suite runs it through `_js_source.code_only` instead.
    },

    ordering: {
        sequencesStrictlyIncrease: backwardsOut.every(
            (s, i) => i === 0 || s.sequence === backwardsOut[i - 1].sequence + 1),
        timesNeverGoBackwards: backwardsOut.every(
            (s, i) => i === 0 || s.timeUs >= backwardsOut[i - 1].timeUs),
        backwardsClampedTo: backwardsOut[1].timeUs,
        // Two samples sharing a timestamp still order.
        sharedTimeSequences: sharedTimeOut.map(s => s.sequence),
        sharedTimeTimes: sharedTimeOut.map(s => s.timeUs),
        microseconds: backwardsOut[0].timeUs,
    },

    grouping: {
        recordedCount: asRecorded.length,
        oneDispatchMatches: sameStream(asRecorded, asOneDispatch),
        singlesMatch: sameStream(asRecorded, asSingles),
        threesMatch: sameStream(asRecorded, asThrees),
        // The measurement that makes the three above meaningful: the streams
        // are not trivially empty.
        sampleCounts: [asRecorded.length, asOneDispatch.length,
                       asSingles.length, asThrees.length],
    },

    enumeration: {
        declaredNotProduced: declared.filter(n => produced.indexOf(n) < 0),
        producedNotDeclared: produced.filter(n => declared.indexOf(n) < 0),
    },

    boundaries: {
        // §3.2 -- the normalizer owns no routing. Checked on CODE in the
        // Python suite; reported here so a reader of the JSON sees it too.
        sourceLength: SOURCE.length,
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
