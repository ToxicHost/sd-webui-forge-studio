/**
 * U3-V — the evidence harness's own gates, driven headlessly.
 *
 * WHY THIS EXISTS. §14 lists twelve failures the U3-V harness must catch, and
 * requires the checks to be BEHAVIOURAL: "do not merely assert that a function
 * or identifier exists; U3 and E0 both proved that such guards can stay green
 * when the call/use is removed."
 *
 * A gate buried inside an async browser routine cannot be tested without a
 * browser, and an untested gate is a comfortable assumption in a guard's
 * uniform. So the harness keeps its refusals in one pure function over an
 * observation object, and this probe drives that function with fabricated
 * observations — one per failure, each differing from a passing observation in
 * exactly one field.
 *
 * The harness file itself is loaded from `Evidence/`, unmodified. `boot()`
 * returns immediately because `window.StudioCore` is absent, so nothing here
 * touches a Canvas.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HARNESS = path.resolve(
    __dirname, "..", "..", "..", "Evidence", "u3v-foreground", "_u3v_harness.js");

// The smallest stub that lets the harness's IIFE finish. `boot()` finds no
// StudioCore and reschedules itself; `setTimeout` is a no-op, so it stops there
// and never reaches anything that needs a document body or a GL context.
function loadHarness() {
    const listeners = { document: 0, window: 0 };
    const sandbox = {
        console: console,
        setTimeout: function () { return 0; },
        Promise: Promise,
        Math: Math,
        Object: Object,
        Array: Array,
        JSON: JSON,
        String: String,
        Number: Number,
        Uint8Array: Uint8Array,
        Date: Date,
        performance: { now: () => 0 },
        document: {
            addEventListener: () => { listeners.document += 1; },
            visibilityState: "visible",
            getElementById: () => null,
            createElement: () => ({ style: {}, addEventListener() {} }),
            scripts: { length: 0 },
        },
        fetch: () => Promise.reject(new Error("no network in the probe")),
        requestAnimationFrame: () => 0,
        location: { origin: "http://127.0.0.1:0" },
        navigator: { userAgent: "probe", hardwareConcurrency: 1 },
        URL: { createObjectURL: () => "", revokeObjectURL() {} },
        Blob: function () {},
        PointerEvent: function () {},
    };
    sandbox.window = sandbox;
    sandbox.window.addEventListener = () => { listeners.window += 1; };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(HARNESS, "utf8"), sandbox, { filename: HARNESS });
    if (!sandbox.window.U3V || typeof sandbox.window.U3V.gates !== "function") {
        throw new Error("harness did not export its gates");
    }
    return sandbox.window.U3V;
}

const U3V = loadHarness();

/** An observation of a perfectly ordinary, valid V2 row. */
function cleanV2() {
    return {
        useV2: true,
        freshnessMatched: true,
        visible: true,
        everBlurred: false,
        frames: 40,
        canonicalChanged: true,
        displayedHashNull: false,
        displayedChanged: true,
        readbackMethod: "gl-readpixels",
        readbackNonZero: 250000,
        pendingAtEnd: false,
        flagEnabled: true,
        contacts: 1,
        samples: 60,
        marks: 480,
        lastRefusal: null,
        paintedPixels: 8060,
        localStroke: false,
        meanTransferShare: 0.9,
        growthRatio: 1.01,
        growthBelowClockResolution: false,
        expectCanvas2D: false,
        lastInputInsidePainted: true,
        firstInputInsidePainted: true,
    };
}

function cleanLegacy() {
    const o = cleanV2();
    o.useV2 = false;
    o.flagEnabled = false;
    o.contacts = 0;
    o.samples = 0;
    o.marks = 0;
    return o;
}

/** A clean observation with one field changed. */
function withField(base, key, value) {
    const o = base();
    o[key] = value;
    return o;
}

const cases = [];
function scenario(name, obs, expected) {
    cases.push({ name, refusals: U3V.gates(obs), expected });
}

// A clean row is refused for nothing. Everything below is measured against
// this: if the baseline itself refused, every "detected" result would be
// meaningless.
scenario("clean-v2", cleanV2(), []);
scenario("clean-legacy", cleanLegacy(), []);

// §14, item by item.
scenario("1-flag-off-in-a-v2-row",
         withField(cleanV2, "flagEnabled", false),
         ["v2-flag-off-in-a-v2-row"]);

scenario("2-contacts-but-no-marks",
         withField(cleanV2, "marks", 0),
         ["v2-placed-no-marks", "legacy-produced-the-pixels-in-a-v2-row"]);

// The U3 defect precisely: Legacy's dab painted, V2 did not.
scenario("3-legacy-produced-the-pixels", (function () {
    const o = cleanV2();
    o.marks = 0;
    o.paintedPixels = 7115;
    return o;
})(), ["v2-placed-no-marks", "legacy-produced-the-pixels-in-a-v2-row"]);

scenario("4-stale-module",
         withField(cleanV2, "freshnessMatched", false),
         ["runtime-does-not-match-served-source"]);

scenario("5-gl-region-read-back-empty",
         withField(cleanV2, "readbackNonZero", 0),
         ["gl-readback-was-empty-but-reported-as-gl"]);

scenario("6a-page-hidden",
         withField(cleanV2, "visible", false),
         ["page-was-not-visible"]);
scenario("6b-window-blurred",
         withField(cleanV2, "everBlurred", true),
         ["window-lost-focus-after-start"]);

scenario("7-final-endpoint-dropped",
         withField(cleanV2, "lastInputInsidePainted", false),
         ["final-endpoint-missing-from-the-painted-bounds"]);
scenario("7b-opening-contact-dropped",
         withField(cleanV2, "firstInputInsidePainted", false),
         ["opening-contact-missing-from-the-painted-bounds"]);

scenario("8-generation-left-pending",
         withField(cleanV2, "pendingAtEnd", true),
         ["presentation-work-left-pending"]);

scenario("9-full-document-work-for-a-local-stroke", (function () {
    const o = cleanV2();
    o.localStroke = true;
    o.meanTransferShare = 100;
    return o;
})(), ["full-document-work-for-a-local-stroke"]);

// A long stroke may legitimately dirty a large rectangle. §11 says so, and the
// gate must not fire when the caller has not claimed the stroke is local.
scenario("9b-large-share-on-a-non-local-stroke",
         withField(cleanV2, "meanTransferShare", 100),
         []);

scenario("10-work-grew-with-history",
         withField(cleanV2, "growthRatio", 1.9),
         ["per-move-work-grew-with-stroke-history"]);

// Growth measured entirely below the clock's resolution is noise, not evidence.
scenario("10b-growth-below-clock-resolution", (function () {
    const o = cleanV2();
    o.growthRatio = 2.0;
    o.growthBelowClockResolution = true;
    return o;
})(), []);

scenario("11-canvas2d-fallback-skipped",
         withField(cleanV2, "expectCanvas2D", true),
         ["canvas2d-fallback-was-not-used"]);

scenario("11b-canvas2d-fallback-actually-used", (function () {
    const o = cleanV2();
    o.expectCanvas2D = true;
    o.readbackMethod = "canvas2d-overlay";
    return o;
})(), []);

scenario("12-legacy-control-routed-through-v2", (function () {
    const o = cleanLegacy();
    o.contacts = 1;
    o.marks = 480;
    return o;
})(), ["legacy-control-was-routed-through-v2"]);

// The remaining refusals, so the whole surface is covered rather than only the
// numbered twelve.
scenario("no-canonical-change",
         withField(cleanV2, "canonicalChanged", false),
         ["no-canonical-pixel-change"]);
scenario("no-displayed-change",
         withField(cleanV2, "displayedChanged", false),
         ["no-displayed-pixel-change"]);
scenario("no-displayed-readback",
         withField(cleanV2, "displayedHashNull", true),
         ["no-displayed-readback"]);
scenario("too-few-frames",
         withField(cleanV2, "frames", 2),
         ["too-few-frames-executed"]);

// A tap is one pointerdown and one pointerup. Holding every row to five frames
// would refuse a correct dot for being brief, so the floor travels with the row.
scenario("a-tap-declares-its-own-frame-floor", (function () {
    const o = cleanV2();
    o.frames = 2;
    o.minFrames = 2;
    return o;
})(), []);
scenario("v2-refused-the-contact",
         withField(cleanV2, "lastRefusal", "target-is-not-a-raster-layer"),
         ["v2-refused:target-is-not-a-raster-layer"]);
scenario("v2-consumed-no-samples", (function () {
    const o = cleanV2();
    o.samples = 0;
    return o;
})(), ["v2-consumed-no-samples"]);
scenario("v2-accepted-no-contact", (function () {
    const o = cleanV2();
    o.contacts = 0;
    return o;
})(), ["v2-accepted-no-contact"]);

// ── the freshness comparison, driven directly ───────────────────────────────
//
// A stale module is exactly the asymmetry `inServedSource && !inRuntime`.

const freshness = (function () {
    const markers = [
        { file: "a.js", token: "NEW_THING", runtime: () => true },
        { file: "b.js", token: "OTHER", runtime: () => true },
    ];
    const agreeing = U3V.compareMarkers(markers, { "a.js": "NEW_THING", "b.js": "OTHER" });
    // The server has the new code; the page does not.
    const staleMarkers = [
        { file: "a.js", token: "NEW_THING", runtime: () => false },
    ];
    const stale = U3V.compareMarkers(staleMarkers, { "a.js": "NEW_THING here" });
    // The page has it; the server does not. Equally a mismatch.
    const backwards = U3V.compareMarkers(
        [{ file: "a.js", token: "NEW_THING", runtime: () => true }], { "a.js": "old" });
    // A runtime probe that throws is a mismatch, never an accidental pass.
    const throwing = U3V.compareMarkers(
        [{ file: "a.js", token: "NEW_THING", runtime: () => { throw new Error("x"); } }],
        { "a.js": "NEW_THING" });
    return {
        agreeingIsFresh: agreeing.fresh,
        agreeingChecks: agreeing.checks.length,
        staleIsRefused: !stale.fresh,
        backwardsIsRefused: !backwards.fresh,
        throwingIsRefused: !throwing.fresh,
    };
})();

process.stdout.write(JSON.stringify({ cases, freshness }, null, 1));
