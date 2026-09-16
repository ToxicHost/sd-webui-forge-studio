/**
 * V2-01b probe: exercise the brush contracts and report what happened.
 *
 * The house pattern (`be17_measure.js` and the rest): a node driver loads the
 * browser module in a `vm` context with a fake `window`, runs probes, and
 * prints ONE JSON object. The Python suite asserts on it, so the assertions
 * live where the rest of the suite's do and the JS side stays a measurement.
 *
 * STRICT MODE IS LOAD-BEARING HERE. Several contracts are `Object.freeze`d and
 * the property this file proves is that a write THROWS rather than being
 * silently ignored -- which is what it would be in sloppy mode, and which is
 * exactly the "preset changed halfway through a stroke" defect §7.2 forbids.
 *
 *     node tests/studio_alpha/v2_01b_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const MODULE = path.resolve(__dirname, "..", "..", "forge_studio", "frontend",
                            "v2", "brush-contracts.js");

function load() {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    vm.createContext(g);
    vm.runInContext('"use strict";' + fs.readFileSync(MODULE, "utf8"), g,
                    { filename: MODULE });
    if (!g.window.StudioBrushV2) throw new Error("module exported nothing");
    return g.window.StudioBrushV2;
}

/** Run `fn`, and report whether it refused rather than what it returned. */
function refusal(fn) {
    try {
        fn();
        return null;
    } catch (error) {
        return String(error && error.message || error);
    }
}

const B = load();

const TARGET = B.target({ kind: "raster_layer", documentId: "d1",
                          layerId: "l1" });

function goodSample(over) {
    return Object.assign({
        sequence: 3, timeUs: 1234, x: 10, y: 20, pressure: 0.5,
        pressureAvailable: true, tiltXDeg: 1, tiltYDeg: 2, tiltAvailable: true,
        azimuthRad: 0.1, barrelRotationRad: 0.2, pointer: "pen", buttons: 1,
        coalesced: true,
    }, over || {});
}

function goodDescriptor(over) {
    return Object.assign({
        strokeId: "s1", documentId: "d1", baseRevision: 4, target: TARGET,
        presetId: "p1", presetRevision: 2, resourceHashes: ["a".repeat(64)],
        workingSizePx: 24, color: "#000000", blendMode: "source-over",
        erase: false, preserveAlpha: false, deterministicSeed: "seed1",
        performanceMode: "quality",
    }, over || {});
}

function goodCommit(over) {
    return Object.assign({
        documentId: "d1", revision: 5, changedBounds: { x0: 0, y0: 0, x1: 1, y1: 1 },
        changedTiles: ["0,0"], undoTransactionId: "u1",
        recoveryDisposition: "queued", sampleCount: 60, dabCount: 400,
        timings: { input: 1, filter: 2, render: 3, merge: 4, preview: 5, commit: 6 },
        warnings: ["a tip resource was missing and a default was used"],
    }, over || {});
}

// ---- field enumeration, both directions --------------------------------

function enumeration(declared, built) {
    const actual = Object.keys(built).sort();
    const expected = declared.slice().sort();
    return {
        declaredNotPresent: expected.filter(n => actual.indexOf(n) < 0),
        presentNotDeclared: actual.filter(n => expected.indexOf(n) < 0),
    };
}

// ---- frozen-ness --------------------------------------------------------

function writeThrows(value, field, next) {
    return refusal(() => { value[field] = next; }) !== null;
}

// ---- the preset leak experiment ----------------------------------------
//
// BE14's, applied to a mechanism instead of to sixteen conventions: poison
// EVERY registered setting with an unusual value, then switch to a preset that
// declares none of them, and see what survives.

const names = Object.keys(B.SETTINGS);
const poisoned = {};
for (const name of names) {
    const fallback = B.SETTINGS[name].fallback;
    poisoned[name] = typeof fallback === "number" ? 9999
        : (typeof fallback === "boolean" ? !fallback : "POISON");
}

const afterEmptyPreset = B.resolvePreset({ previous: poisoned, preset: {} });
const afterEmptyNoKeep = B.resolvePreset({ previous: poisoned, preset: {},
                                           keepWorkingSize: false });
const leaked = names.filter(n => afterEmptyPreset[n] === poisoned[n]
                                 && !B.SETTINGS[n].session);
const sessionCarried = names.filter(n => afterEmptyPreset[n] === poisoned[n]
                                         && B.SETTINGS[n].session);
const carriedWithKeepOff = names.filter(n => afterEmptyNoKeep[n] === poisoned[n]);

// Precedence: each later source must win over the one before it.
const precedence = B.resolvePreset({
    calibration: { hardness: 0.1 },
    global: { hardness: 0.2, flow: 0.3 },
    preset: { hardness: 0.4 },
    override: { hardness: 0.6 },
});
const globalOnly = B.resolvePreset({ global: { hardness: 0.2 } });
const presetOverGlobal = B.resolvePreset({ global: { hardness: 0.2 },
                                           preset: { hardness: 0.4 } });
const calibrationOnly = B.resolvePreset({ calibration: { hardness: 0.1 } });

// ---- the tip matrix ------------------------------------------------------

function fullRow(over) {
    const row = {};
    for (const control of B.TIP_CONTROLS) row[control] = true;
    return Object.assign(row, over || {});
}

const report = {
    schemaVersion: B.SCHEMA_VERSION,

    enumeration: {
        sample: enumeration(B.SAMPLE_FIELDS, B.sample(goodSample())),
        descriptor: enumeration(B.DESCRIPTOR_FIELDS,
                                B.descriptor(goodDescriptor())),
        target: enumeration(B.TARGET_FIELDS, TARGET),
        commit: enumeration(B.COMMIT_FIELDS, B.commit(goodCommit())),
    },

    sample: {
        unknownPointerRefused: refusal(() =>
            B.sample(goodSample({ pointer: "eraser-tip" }))) !== null,
        emptyPointerRefused: refusal(() =>
            B.sample(goodSample({ pointer: "" }))) !== null,
        negativeSequenceRefused: refusal(() =>
            B.sample(goodSample({ sequence: -1 }))) !== null,
        // The FLAG is independent of the VALUE: a mouse reports a well-formed
        // 0.5 and it means nothing.
        pressureKeptWhenUnavailable:
            B.sample(goodSample({ pressure: 0.5, pressureAvailable: false })).pressure,
        availabilityKept:
            B.sample(goodSample({ pressureAvailable: false })).pressureAvailable,
        pressureClamped: [
            B.sample(goodSample({ pressure: -3 })).pressure,
            B.sample(goodSample({ pressure: 4 })).pressure,
        ],
        // Absent, not zero -- a defaulted 0 tells an azimuth dynamic it has a
        // reading it does not have.
        absentAzimuthIsNull:
            B.sample(goodSample({ azimuthRad: undefined })).azimuthRad,
        absentBarrelIsNull:
            B.sample(goodSample({ barrelRotationRad: undefined })).barrelRotationRad,
        frozen: writeThrows(B.sample(goodSample()), "x", 999),
        everyPointerKindAccepted: B.POINTER_KINDS.every(kind =>
            refusal(() => B.sample(goodSample({ pointer: kind }))) === null),
    },

    descriptor: {
        frozen: writeThrows(B.descriptor(goodDescriptor()), "presetId", "other"),
        resourceHashesFrozen:
            writeThrows(B.descriptor(goodDescriptor()).resourceHashes, 0, "x"),
        badResourceHashRefused: refusal(() =>
            B.descriptor(goodDescriptor({ resourceHashes: ["not-a-hash"] }))) !== null,
        pathStrokeIdRefused: refusal(() =>
            B.descriptor(goodDescriptor({ strokeId: "../../etc" }))) !== null,
        missingSeedRefused: refusal(() =>
            B.descriptor(goodDescriptor({ deterministicSeed: "" }))) !== null,
        missingTargetRefused: refusal(() =>
            B.descriptor(goodDescriptor({ target: null }))) !== null,
        unknownModeRefused: refusal(() =>
            B.descriptor(goodDescriptor({ performanceMode: "turbo" }))) !== null,
        everyModeAccepted: B.PERFORMANCE_MODES.every(mode =>
            refusal(() => B.descriptor(goodDescriptor({ performanceMode: mode })))
                === null),
        refusalDoesNotQuoteTheValue:
            (refusal(() => B.descriptor(goodDescriptor({ strokeId: "/etc/passwd" })))
                || "").indexOf("passwd") < 0,
    },

    target: {
        kinds: B.TARGET_KINDS.slice(),
        unknownKindRefused: refusal(() =>
            B.target({ kind: "everything", documentId: "d", layerId: "l" })) !== null,
        pathDocumentRefused: refusal(() =>
            B.target({ documentId: "../d", layerId: "l" })) !== null,
        pathChannelRefused: refusal(() =>
            B.target({ documentId: "d", layerId: "l", channelId: "a/b" })) !== null,
        emptyChannelLegal:
            B.target({ documentId: "d", layerId: "l" }).channelId === "",
        everyKindAccepted: B.TARGET_KINDS.every(kind =>
            refusal(() => B.target({ kind, documentId: "d", layerId: "l" })) === null),
    },

    commit: {
        everyTimingStagePresent: B.TIMING_STAGES.every(stage =>
            Object.prototype.hasOwnProperty.call(
                B.commit(goodCommit()).timings, stage)),
        missingStageBecomesZero:
            B.commit(goodCommit({ timings: { input: 1 } })).timings.render,
        pathWarningRefused: refusal(() =>
            B.commit(goodCommit({ warnings: ["failed to read C:\\models\\x"] }))) !== null,
        unknownDispositionRefused: refusal(() =>
            B.commit(goodCommit({ recoveryDisposition: "written" }))) !== null,
        frozen: writeThrows(B.commit(goodCommit()), "revision", 99),
    },

    sink: {
        completeSinkAccepted:
            refusal(() => B.validateSink({ begin() {} })) === null,
        incompleteSinkRefused: refusal(() => B.validateSink({})) !== null,
        completeTransactionAccepted: refusal(() => B.validateTransaction({
            addCoverage() {}, preview() {}, commit() {}, cancel() {},
        })) === null,
        // A partially implemented sink produces rows that look like results
        // and are holes -- the failure the oracle's `validateAdapter` exists
        // for, one layer down.
        partialTransactionRefused: refusal(() => B.validateTransaction({
            addCoverage() {}, preview() {}, commit() {},
        })) !== null,
    },

    tipMatrix: {
        controls: B.TIP_CONTROLS.slice(),
        completeRowAccepted:
            refusal(() => B.validateTipMatrix({ round: fullRow() })) === null,
        reasonStringAccepted: refusal(() => B.validateTipMatrix({
            round: fullRow({ angle: "needs-shape" }) })) === null,
        undecidedControlRefused: refusal(() => {
            const row = fullRow();
            delete row.density;
            B.validateTipMatrix({ round: row });
        }) !== null,
        unknownControlRefused: refusal(() =>
            B.validateTipMatrix({ round: fullRow({ wetness: true }) })) !== null,
        nonBooleanRefused: refusal(() =>
            B.validateTipMatrix({ round: fullRow({ ratio: 1 }) })) !== null,
        emptyMatrixRefused: refusal(() => B.validateTipMatrix({})) !== null,
        honestyRuleNamesItsOwner: B.HONESTY_RULE.owedBy,
    },

    preset: {
        registered: names.slice().sort(),
        sessionScoped: B.sessionScopedSettings().slice().sort(),
        resolutionOrder: B.RESOLUTION_ORDER.slice(),
        leakedFromPoisonedState: leaked,
        sessionCarriedFromPoisonedState: sessionCarried,
        carriedWithKeepWorkingSizeOff: carriedWithKeepOff,
        // Every non-session setting must be back at its declared fallback.
        allNonSessionAtFallback: names
            .filter(n => !B.SETTINGS[n].session)
            .every(n => afterEmptyPreset[n] === B.SETTINGS[n].fallback),
        precedence: {
            calibrationOnly: calibrationOnly.hardness,
            globalOverCalibration: globalOnly.hardness,
            presetOverGlobal: presetOverGlobal.hardness,
            overrideWinsAll: precedence.hardness,
            globalReachesAnUntouchedSetting: precedence.flow,
        },
        frozen: writeThrows(afterEmptyPreset, "size", 1),
        // The six BE14 named, so a future edit to SETTINGS cannot quietly drop
        // one of them from the guard's subject.
        leakedInLegacy: names.filter(n => B.SETTINGS[n].leakedInLegacy).sort(),
    },
};

process.stdout.write(JSON.stringify(report, null, 2));
