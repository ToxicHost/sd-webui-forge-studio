/**
 * Forge Studio — Brush Engine V2 seam contracts (V2-01b / spec BE1)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §7, plus the guards
 * §22's BE1 gate asks for: field enumeration, the tip support matrix, and the
 * preset leak guard.
 *
 * NOTHING HERE RENDERS, AND NOTHING HERE IS LOADED BY THE PAGE. Spec §19.7
 * builds V2 beside Legacy behind an internal development flag; this file is a
 * static asset `index.html` does not reference. It becomes reachable when the
 * kernel does.
 *
 * WHY THE CONTRACT COMES BEFORE THE KERNEL. The spec orders BE1 ahead of BE2
 * deliberately: the kernel is built against a frozen contract instead of the
 * contract being reverse-engineered from whatever the kernel happened to do.
 * The inherited engine is what that costs -- `noteSample` accepts nineteen
 * measured fields and keeps five, and `twist` is normalised by
 * `canvas-input.js:127` and read by nothing.
 *
 * THREE SPELLINGS OF ONE SAMPLE ALREADY EXIST, which is the immediate reason
 * this is worth landing now:
 *
 *   canvas-input.js:102   normalize()      19 fields, the shipping producer
 *   canvas-core.js:1213   noteSample()      5 fields, the shipping consumer
 *   oracle legacy_adapter penSample()       a third, written against the second
 *
 * `FIELDS` below is the single list all of them should have been reading.
 *
 * Review: `Evidence/source-review/V2-01b-brush-contracts.md`.
 */

(function () {
"use strict";

//: Bumped only by a BREAKING change to one of the shapes below. Additive
//: fields carry an explicit default and do not move it -- spec §11 of the
//: bundle, and the same discipline `forge_studio/v2_contracts.py` holds on the
//: Canvas/AI side.
const SCHEMA_VERSION = 1;

/** A refusal that never quotes the offending value back. */
function refuse(what) {
    // The value is deliberately absent from the message: a malformed
    // identifier and an unknown one must be indistinguishable, which is the
    // rule `asset_service._entry` already holds on the server.
    throw new Error("brush contract: " + what);
}

//: Anything that could be, or become, a filesystem or network reference.
//: Mirrors `_PATHISH` in `v2_contracts.py` so the two sides refuse the same
//: strings; spec §7.3 says a target reference must never expose a path.
const PATHISH = /[/\\]|\.\.|^[A-Za-z]:|^[A-Za-z][A-Za-z0-9+.-]*:/;

// ─────────────────────────────────────────────────────────── §7.1 the sample

//: Spec §7.1, plus two fields Studio keeps and the spec omits.
//:
//: `pressureAvailable` and `tiltAvailable` are an INTENTIONAL DIVERGENCE, and
//: §8.1 is why: "Missing sensors MUST fall back without disabling an otherwise
//: usable preset." A fallback needs to know a sensor was missing.
//: `canvas-input.js:79-82` records the cost of not knowing -- "a pressure
//: dynamic must not act on a substituted value, or every mouse stroke would be
//: drawn as though the owner pressed exactly half way."
const SAMPLE_FIELDS = Object.freeze([
    "sequence", "timeUs", "x", "y",
    "pressure", "pressureAvailable",
    "tiltXDeg", "tiltYDeg", "tiltAvailable",
    "azimuthRad", "barrelRotationRad",
    "pointer", "buttons", "coalesced",
]);

//: §7.1 names exactly these three. An unknown device is a refusal rather than
//: a silent fourth case, because `pressureIsMeasured` (`canvas-input.js:64`)
//: already switches on precisely this set and a fourth value would fall
//: through it to "not measured" without saying so.
const POINTER_KINDS = Object.freeze(["pen", "mouse", "touch"]);

/**
 * One canonical sample.
 *
 * `sequence` is a per-CONTACT ordinal, not a timestamp. §8.2 requires
 * deterministic replay, and two coalesced samples can share an
 * `event.timeStamp` -- the browser reports the frame's time, not the
 * digitiser's. An ordinal is what orders them.
 *
 * `timeUs` is microseconds, per §7.1. Studio's producer has float
 * milliseconds (`event.timeStamp`), so the conversion happens once, here,
 * rather than in every consumer that wants a rate.
 */
function sample(spec) {
    const s = spec || {};
    const pointer = String(s.pointer || "");
    if (POINTER_KINDS.indexOf(pointer) < 0) refuse("unknown pointer kind");
    const sequence = s.sequence | 0;
    if (sequence < 0) refuse("sequence must not be negative");
    const value = {
        sequence: sequence,
        timeUs: Math.round(Number(s.timeUs) || 0),
        x: Number(s.x) || 0,
        y: Number(s.y) || 0,
        pressure: clamp01(Number(s.pressure)),
        // The FLAG, not the value. A mouse reports a perfectly well-formed
        // 0.5 and it means nothing.
        pressureAvailable: !!s.pressureAvailable,
        tiltXDeg: Number(s.tiltXDeg) || 0,
        tiltYDeg: Number(s.tiltYDeg) || 0,
        tiltAvailable: !!s.tiltAvailable,
        // Absent rather than zero. Studio's normaliser computes no azimuth
        // (it is derivable from tilt, and nothing derives it yet), and a
        // defaulted 0 would tell an azimuth dynamic it had a reading. See the
        // review record §6.
        azimuthRad: s.azimuthRad === undefined ? null : Number(s.azimuthRad),
        barrelRotationRad: s.barrelRotationRad === undefined
            ? null : Number(s.barrelRotationRad),
        pointer: pointer,
        buttons: s.buttons | 0,
        coalesced: !!s.coalesced,
    };
    return Object.freeze(value);
}

function clamp01(n) {
    if (!isFinite(n)) return 0;
    return n < 0 ? 0 : (n > 1 ? 1 : n);
}

// ─────────────────────────────────────────────────────── §7.2 the descriptor

const DESCRIPTOR_FIELDS = Object.freeze([
    "strokeId", "documentId", "baseRevision", "target",
    "presetId", "presetRevision", "resourceHashes",
    "workingSizePx", "color", "blendMode",
    "erase", "preserveAlpha", "deterministicSeed", "performanceMode",
    "schemaVersion",
]);

const PERFORMANCE_MODES = Object.freeze(["auto", "responsive", "quality"]);

/**
 * The behaviour of one contact, frozen.
 *
 * §7.2: "The descriptor MUST freeze behavior for one contact. Preset or
 * setting changes during a stroke apply only to the next contact."
 *
 * FROZEN STRUCTURALLY, not by convention. `Object.freeze` makes a mid-stroke
 * write throw in strict mode instead of silently taking effect on the next
 * dab -- which is how a preset change halfway through a stroke would otherwise
 * show up as a stroke that changes character in the middle.
 */
function descriptor(spec) {
    const s = spec || {};
    const mode = String(s.performanceMode || "auto");
    if (PERFORMANCE_MODES.indexOf(mode) < 0) refuse("unknown performance mode");
    if (!s.target || typeof s.target !== "object") refuse("target is required");
    const value = {
        strokeId: opaque(s.strokeId, "strokeId"),
        documentId: opaque(s.documentId, "documentId"),
        baseRevision: s.baseRevision | 0,
        target: s.target,
        presetId: opaque(s.presetId, "presetId"),
        presetRevision: s.presetRevision | 0,
        // Content identity, never a path -- §13, "Resource identity MUST be
        // content-based and MUST NOT expose absolute paths."
        resourceHashes: Object.freeze((s.resourceHashes || []).map(function (h) {
            if (!/^[0-9a-f]{64}$/.test(String(h))) refuse("resource hash");
            return String(h);
        })),
        workingSizePx: Number(s.workingSizePx) || 0,
        color: s.color === undefined ? null : s.color,
        blendMode: String(s.blendMode || "source-over"),
        erase: !!s.erase,
        preserveAlpha: !!s.preserveAlpha,
        // §13: "Deterministic modes MUST serialize their seed/order." A stroke
        // without one cannot be replayed, so it is required rather than
        // defaulted to something that looks like a seed.
        deterministicSeed: opaque(s.deterministicSeed, "deterministicSeed"),
        performanceMode: mode,
        schemaVersion: SCHEMA_VERSION,
    };
    return Object.freeze(value);
}

function opaque(value, what) {
    const text = String(value === undefined || value === null ? "" : value);
    if (!text) refuse(what + " is required");
    if (PATHISH.test(text)) refuse(what + " must be an opaque id, not a path");
    return text;
}

// ───────────────────────────────────────────────────────── §7.3 the target

//: §7.3, and the same six `v2_contracts.TargetKind` declares on the server.
//: Duplicated deliberately rather than fetched: this file must be readable
//: without a server, and the two are joined by a test that compares them.
const TARGET_KINDS = Object.freeze([
    "raster_layer", "layer_mask", "generation_mask",
    "selection", "region", "censorship",
]);

const TARGET_FIELDS = Object.freeze([
    "kind", "documentId", "layerId", "channelId", "schemaVersion",
]);

function target(spec) {
    const s = spec || {};
    const kind = String(s.kind || "raster_layer");
    if (TARGET_KINDS.indexOf(kind) < 0) refuse("unknown target kind");
    const channel = s.channelId === undefined || s.channelId === null
        ? "" : String(s.channelId);
    if (channel && PATHISH.test(channel)) refuse("channelId must be opaque");
    return Object.freeze({
        kind: kind,
        documentId: opaque(s.documentId, "documentId"),
        layerId: opaque(s.layerId, "layerId"),
        // A raster layer is its own channel, so empty is legal.
        channelId: channel,
        schemaVersion: SCHEMA_VERSION,
    });
}

// ───────────────────────────────────────────── §7.4/§7.5 sink and commit

const COMMIT_FIELDS = Object.freeze([
    "documentId", "revision", "changedBounds", "changedTiles",
    "undoTransactionId", "recoveryDisposition",
    "sampleCount", "dabCount", "timings", "warnings", "schemaVersion",
]);

//: §7.5 requires input/filter/render/merge/preview/commit. Named as a list so
//: a stage that stops being timed fails a test rather than reading as zero.
const TIMING_STAGES = Object.freeze([
    "input", "filter", "render", "merge", "preview", "commit",
]);

//: What a completed transaction did with the recovery journal. §17: "Completed
//: stroke transactions MUST be journaled outside the paint path", so "queued"
//: is the ordinary answer and "written" would be a claim the paint path cannot
//: honestly make.
const RECOVERY_DISPOSITIONS = Object.freeze(["queued", "skipped", "unavailable"]);

function commit(spec) {
    const s = spec || {};
    const disposition = String(s.recoveryDisposition || "queued");
    if (RECOVERY_DISPOSITIONS.indexOf(disposition) < 0) {
        refuse("unknown recovery disposition");
    }
    const timings = {};
    for (let i = 0; i < TIMING_STAGES.length; i++) {
        const stage = TIMING_STAGES[i];
        timings[stage] = Number((s.timings || {})[stage]) || 0;
    }
    return Object.freeze({
        documentId: opaque(s.documentId, "documentId"),
        revision: s.revision | 0,
        changedBounds: s.changedBounds === undefined ? null : s.changedBounds,
        changedTiles: Object.freeze((s.changedTiles || []).slice()),
        undoTransactionId: opaque(s.undoTransactionId, "undoTransactionId"),
        recoveryDisposition: disposition,
        sampleCount: s.sampleCount | 0,
        dabCount: s.dabCount | 0,
        timings: Object.freeze(timings),
        // §7.5: "user-safe warnings". A path in one is a leak that no later
        // redaction reliably catches, because by then it is prose.
        warnings: Object.freeze((s.warnings || []).map(function (w) {
            const text = String(w);
            if (PATHISH.test(text)) refuse("a warning carries a path");
            return text;
        })),
        schemaVersion: SCHEMA_VERSION,
    });
}

//: §7.4's interface, as the method names a sink must provide. An adapter that
//: cannot answer the whole surface is refused before a stroke starts, rather
//: than discovered halfway through one.
const SINK_METHODS = Object.freeze(["begin"]);
const TRANSACTION_METHODS = Object.freeze([
    "addCoverage", "preview", "commit", "cancel",
]);

function validateSink(sink) {
    for (let i = 0; i < SINK_METHODS.length; i++) {
        if (typeof (sink || {})[SINK_METHODS[i]] !== "function") {
            refuse("sink is missing " + SINK_METHODS[i]);
        }
    }
    return sink;
}

function validateTransaction(txn) {
    for (let i = 0; i < TRANSACTION_METHODS.length; i++) {
        if (typeof (txn || {})[TRANSACTION_METHODS[i]] !== "function") {
            refuse("transaction is missing " + TRANSACTION_METHODS[i]);
        }
    }
    return txn;
}

// ─────────────────────────────────────────────── §13 the tip support matrix

//: The controls a tip family may declare. Adding one here without deciding it
//: for every family fails the matrix guard.
const TIP_CONTROLS = Object.freeze([
    "angle", "ratio", "spikes", "density", "falloff", "rotationJitter",
]);

/**
 * Validate one family's declaration.
 *
 * A value is `true`, `false`, or a REASON STRING for conditionally-live --
 * the inherited engine's `"needs-shape"` (`canvas-core.js:2193`) is the
 * pattern, and it is a good one: it tells an owner why a control is inert
 * instead of hiding it.
 *
 * WHAT THIS CANNOT CHECK, AND SAYING SO IS THE POINT. BE20 measured
 * `TIP_CAPABILITIES` against rendered pixels and found four declarations that
 * do nothing -- `ratio` on Airbrush, Ink Wash and Pixel Perfect, `density` on
 * Ink Wash. Worse, it found a control that APPEARS to work and does something
 * else: Rotation Jitter is dead on every round tip, yet moves 35.7% of pixels
 * on Pencil and 89.1% on Scatter Dust, because it consumes `Math.random()` in
 * `stampWet` and shifts the stream feeding the stipple that follows.
 *
 * So "does the mark change?" is NECESSARY AND NOT SUFFICIENT. A verifier that
 * rendered two variants and diffed them would certify Rotation Jitter as live
 * on Charcoal. The honest rule has two halves:
 *
 *   1. a declared-live control MUST change the mark   -- checkable by pixels
 *   2. the renderer MUST READ the declared value      -- structural, and NOT
 *                                                        checkable here
 *
 * Half two is owed by the renderer package (V2-05 / spec BE5). It is named
 * rather than quietly dropped, and `HONESTY_RULE` below is what that package
 * has to satisfy.
 */
function validateTipMatrix(matrix) {
    const families = Object.keys(matrix || {});
    if (!families.length) refuse("the tip matrix declares no families");
    for (let f = 0; f < families.length; f++) {
        const row = matrix[families[f]] || {};
        const declared = Object.keys(row);
        for (let d = 0; d < declared.length; d++) {
            if (TIP_CONTROLS.indexOf(declared[d]) < 0) {
                refuse("family declares an unknown control");
            }
        }
        for (let c = 0; c < TIP_CONTROLS.length; c++) {
            const value = row[TIP_CONTROLS[c]];
            if (value === undefined) refuse("family does not decide a control");
            const ok = value === true || value === false
                || (typeof value === "string" && value.length > 0);
            if (!ok) refuse("a control declaration is not true, false or a reason");
        }
    }
    return matrix;
}

const HONESTY_RULE = Object.freeze({
    pixels: "a control declared live must change the mark",
    structural: "the renderer must read the declared value; a pixel diff "
        + "cannot prove this, and BE20 measured a control that moves pixels "
        + "by consuming the random stream rather than by being read",
    owedBy: "V2-05 (spec BE5, stamp/sweep coverage)",
});

// ───────────────────────────────────────────── §10/§14 preset resolution

//: Where a resolved value may come from, in order. §10, and the ORDER is the
//: contract: calibration is the device, global is the owner's hand, the preset
//: is the artist's intent, the override is this preset's exception, and the
//: session is what the owner is doing right now.
//:
//: THE PREVIOUS PRESET IS NOT ON THIS LIST, and that is the whole mechanism.
const RESOLUTION_ORDER = Object.freeze([
    "calibration", "global", "preset", "override", "session",
]);

/**
 * Every setting V2 resolves, and whether it survives a preset switch.
 *
 * `session: true` means the value is a working value the owner is holding
 * across presets. §14: "Keep Working Size When Switching MUST default on",
 * while "Opacity and Flow normally load from the selected preset."
 *
 * THAT BOUNDARY IS WHERE A LEAK AND A FEATURE LOOK IDENTICAL. Size carrying
 * over is the feature; Ratio carrying over is the defect BE14 closed. Both are
 * "a value from before the switch". Declaring the scope per setting is what
 * lets a guard tell them apart instead of assuming.
 *
 * THE SIX THAT LEAKED are marked. `BE14-preset-rebuild.md` §4: `applyBrushPreset`
 * wrote fourteen fields and not these -- "exactly the controls BE6 proved
 * alive, and exactly the ones no preset sets. That is not a coincidence, it is
 * one defect." Crank Ratio to 4 on Scatter Dust, pick Hard Ink, and Hard Ink is
 * elliptical until the owner notices.
 */
const SETTINGS = Object.freeze({
    size:           { session: true,  fallback: 12,          leakedInLegacy: false },
    opacity:        { session: false, fallback: 1.0,         leakedInLegacy: false },
    flow:           { session: false, fallback: 1.0,         leakedInLegacy: false },
    hardness:       { session: false, fallback: 0.5,         leakedInLegacy: false },
    spacing:        { session: false, fallback: 0.05,        leakedInLegacy: false },
    buildup:        { session: false, fallback: false,       leakedInLegacy: false },
    smoothingMode:  { session: false, fallback: "natural",   leakedInLegacy: false },
    smoothing:      { session: false, fallback: 0,           leakedInLegacy: false },
    ratio:          { session: false, fallback: 1.0,         leakedInLegacy: true  },
    spikes:         { session: false, fallback: 2,           leakedInLegacy: true  },
    density:        { session: false, fallback: 1.0,         leakedInLegacy: true  },
    angle:          { session: false, fallback: 0,           leakedInLegacy: true  },
    taperIn:        { session: false, fallback: 0,           leakedInLegacy: true  },
    falloff:        { session: false, fallback: "default",   leakedInLegacy: true  },
});

//: §5.2. Position smoothing and pressure smoothing are independent, and the
//: three modes are the owner-visible choice.
const SMOOTHING_MODES = Object.freeze(["raw", "natural", "stabilized"]);

/**
 * Resolve EVERY registered setting from the ordered sources.
 *
 * THE LEAK IS UNREPRESENTABLE, not merely absent. `applyBrushPreset` leaked
 * because it MERGED a partial preset over live state, so a field no preset
 * mentioned kept whatever the last one left. BE14 fixed that by making all
 * sixteen presets declare all fifteen fields -- a convention, which fails on
 * the day a sixteenth setting is added and one preset forgets it.
 *
 * This resolves. The result is built from `SETTINGS`, so a setting is present
 * exactly when it is registered, and its value comes only from the sources in
 * `RESOLUTION_ORDER`. `previous` is accepted for ONE purpose -- session-scoped
 * values -- and a setting without `session: true` cannot read it, whatever any
 * caller passes.
 *
 * That is why the signature takes `previous` at all rather than pretending it
 * does not exist: hiding it would push "keep the working size" into the
 * caller, where it would be a second resolution path nobody tests.
 */
function resolvePreset(sources) {
    const s = sources || {};
    const calibration = s.calibration || {};
    const global = s.global || {};
    const preset = s.preset || {};
    const override = s.override || {};
    const previous = s.previous || {};
    const keepWorkingSize = s.keepWorkingSize === undefined
        ? true : !!s.keepWorkingSize;

    const resolved = {};
    const names = Object.keys(SETTINGS);
    for (let i = 0; i < names.length; i++) {
        const name = names[i];
        const rule = SETTINGS[name];
        let value = rule.fallback;
        if (has(calibration, name)) value = calibration[name];
        if (has(global, name)) value = global[name];
        if (has(preset, name)) value = preset[name];
        if (has(override, name)) value = override[name];
        // THE ONLY READ OF `previous`, and it is gated on the DECLARED scope
        // rather than on the caller's intent.
        if (rule.session && keepWorkingSize && has(previous, name)) {
            value = previous[name];
        }
        resolved[name] = value;
    }
    return Object.freeze(resolved);
}

function has(source, name) {
    return Object.prototype.hasOwnProperty.call(source, name)
        && source[name] !== undefined;
}

/** The settings that survive a switch, by declaration. Read by the guard. */
function sessionScopedSettings() {
    return Object.keys(SETTINGS).filter(function (name) {
        return SETTINGS[name].session;
    });
}

// ────────────────────────────────────────────────────────────── the surface

window.StudioBrushV2 = {
    SCHEMA_VERSION: SCHEMA_VERSION,

    // §7 types
    SAMPLE_FIELDS: SAMPLE_FIELDS,
    DESCRIPTOR_FIELDS: DESCRIPTOR_FIELDS,
    TARGET_FIELDS: TARGET_FIELDS,
    COMMIT_FIELDS: COMMIT_FIELDS,
    POINTER_KINDS: POINTER_KINDS,
    TARGET_KINDS: TARGET_KINDS,
    PERFORMANCE_MODES: PERFORMANCE_MODES,
    TIMING_STAGES: TIMING_STAGES,
    RECOVERY_DISPOSITIONS: RECOVERY_DISPOSITIONS,
    SINK_METHODS: SINK_METHODS,
    TRANSACTION_METHODS: TRANSACTION_METHODS,
    sample: sample,
    descriptor: descriptor,
    target: target,
    commit: commit,
    validateSink: validateSink,
    validateTransaction: validateTransaction,

    // §13 tip support matrix
    TIP_CONTROLS: TIP_CONTROLS,
    HONESTY_RULE: HONESTY_RULE,
    validateTipMatrix: validateTipMatrix,

    // §10/§14 preset resolution
    SETTINGS: SETTINGS,
    RESOLUTION_ORDER: RESOLUTION_ORDER,
    SMOOTHING_MODES: SMOOTHING_MODES,
    resolvePreset: resolvePreset,
    sessionScopedSettings: sessionScopedSettings,
};

})();
