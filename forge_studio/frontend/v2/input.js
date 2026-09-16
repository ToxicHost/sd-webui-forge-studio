/**
 * Forge Studio — Brush Engine V2 input normalization (V2-02 / spec BE2)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§7.1, 8.1, 8.2.
 * One canonical stream of `NormalizedSample`, independent of how the browser
 * chose to group its dispatches.
 *
 * NOT LOADED BY THE PAGE. §19.7 builds V2 beside Legacy behind an internal
 * development flag; this is a static asset `index.html` does not reference.
 *
 * WHAT THIS OWNS: classification, ordering, coalesced expansion, sensor
 * fallbacks, and deterministic record/replay.
 *
 * WHAT IT DELIBERATELY DOES NOT OWN, and the guard test enforces it: whether a
 * touch paints or pans, which tool receives the stroke, pointer capture, pan,
 * zoom, and every other routing decision. Those are BE8's
 * (`OVERNIGHT_HANDOFF_V2_02_AND_GPU_2026-08-25.md` §1.1). The honest verdict
 * this module supports is CLASSIFICATION PROVEN, ROUTING DEFERRED.
 *
 * WHAT IS INHERITED FROM CT2 AND KEPT. `canvas-input.js` got three things
 * right and they are carried across unchanged, named here so V2 cannot quietly
 * regress them:
 *
 *   * `pressureAvailable` travels beside the value, because "a pressure
 *     dynamic must not act on a substituted value, or every mouse stroke would
 *     be drawn as though the owner pressed exactly half way";
 *   * touch pressure counts as measured only STRICTLY between 0 and 1, since
 *     many digitisers report a constant 1 for contact and 0 for none;
 *   * coalesced samples dedupe on RAW client coordinates, before the document
 *     transform, because at high zoom the transform separates two samples that
 *     were the same sample.
 *
 * Review: `Evidence/source-review/V2-02-normalized-input.md`.
 */

(function () {
"use strict";

const C = (typeof window !== "undefined" && window.StudioBrushV2) || null;
if (!C) throw new Error("v2/input.js requires v2/brush-contracts.js first");

//: §8.2: "Mouse MUST use a fixed configurable pressure". Configurable by the
//: CALLER, not by a Settings control -- exposing it in the UI is a product
//: surface V2-02 does not own. 0.5 preserves CT2's `FALLBACK_PRESSURE`, so
//: nothing about an existing mouse stroke moves.
const DEFAULT_MOUSE_PRESSURE = 0.5;

//: The same conservative value for a device Studio cannot identify. Painting a
//: stroke at zero opacity is unrecoverable; a known-safe half is not.
const DEFAULT_UNKNOWN_PRESSURE = 0.5;

/**
 * Which of the spec's three kinds this device is.
 *
 * AN UNRECOGNISED TYPE BECOMES `mouse` RATHER THAN A REFUSAL. The contract
 * refuses an unknown kind outright, so something has to map -- and `mouse` is
 * what is actually true of a device Studio cannot identify: no measured
 * pressure, no tilt. Refusing here would satisfy the contract and violate §8.1,
 * "Missing sensors MUST fall back without disabling an otherwise usable
 * preset", by disabling the device entirely.
 */
function classify(pointerType) {
    const kind = String(pointerType || "").toLowerCase();
    if (kind === "pen" || kind === "mouse" || kind === "touch") return kind;
    return "mouse";
}

/**
 * Is this device's pressure a MEASUREMENT or a placeholder?
 *
 * CT2's rule, kept: a pen always measures; a touch digitiser measures only when
 * it reports strictly between the contact/no-contact constants; a mouse never
 * does.
 */
function pressureIsMeasured(kind, rawPressure) {
    if (kind === "pen") return true;
    if (kind === "touch") {
        return typeof rawPressure === "number"
            && rawPressure > 0 && rawPressure < 1;
    }
    return false;
}

/** The pressure to use, and whether it was measured. */
function resolvePressure(kind, rawPressure, options) {
    const fallback = (options && typeof options.mousePressure === "number")
        ? options.mousePressure
        : (kind === "mouse" ? DEFAULT_MOUSE_PRESSURE : DEFAULT_UNKNOWN_PRESSURE);
    if (!pressureIsMeasured(kind, rawPressure)) {
        return { pressure: fallback, available: false };
    }
    const clamped = rawPressure < 0 ? 0 : (rawPressure > 1 ? 1 : rawPressure);
    return { pressure: clamped, available: true };
}

/**
 * One contact's canonical stream. Created at pen-down, discarded at pen-up.
 *
 * SEQUENCE IS PER CONTACT AND COUNTS ACCEPTED SAMPLES -- not dispatches, not
 * milliseconds. That is precisely what makes the stream independent of browser
 * dispatch grouping: the same physical samples produce the same sequence
 * numbers whether the browser delivered them as one coalesced event or as five
 * separate ones, because the counter advances per sample admitted.
 */
function StrokeInput(options) {
    this.options = options || {};
    this.sequence = 0;
    this.lastTimeUs = null;
    // Dedupe state, on RAW client coordinates. Reset per contact.
    this._lastRaw = null;
}

/**
 * Normalise one raw pointer-like record into a `NormalizedSample`.
 *
 * `raw` is anything with the PointerEvent shape this module reads. Taking a
 * duck-typed record rather than a real event is what lets a recorded fixture
 * replay through the identical code path -- a replay that went down a second
 * path would be testing the replay.
 *
 * `toDoc` converts client coordinates to document space and is injected, so
 * this file has no opinion about zoom, pan or device pixel ratio.
 */
StrokeInput.prototype.normalize = function (raw, toDoc, extra) {
    const kind = classify(raw.pointerType);
    const rawPressure = typeof raw.pressure === "number" ? raw.pressure : 0;
    const resolved = resolvePressure(kind, rawPressure, this.options);
    const doc = toDoc
        ? toDoc(raw.clientX, raw.clientY)
        : { x: raw.clientX || 0, y: raw.clientY || 0 };

    // Microseconds, per §7.1. The producer has float milliseconds, so the
    // conversion happens once, here, instead of in every consumer wanting a
    // rate.
    let timeUs = Math.round((Number(raw.timeStamp) || 0) * 1000);
    // CLAMPED NON-DECREASING, and `sequence` is the ordering authority.
    // Coalesced samples can share a timestamp and a dispatch boundary can
    // report one backwards; a negative interval reaching a speed dynamic is a
    // worse outcome than a repeated one. Recorded in the review record §6.3
    // because it does discard information.
    if (this.lastTimeUs !== null && timeUs < this.lastTimeUs) {
        timeUs = this.lastTimeUs;
    }
    this.lastTimeUs = timeUs;

    // Tilt is only a measurement on a pen that reports it. Zero rather than
    // absent for the VALUES, so a consumer need not branch; the FLAG is what
    // says whether the zero means anything.
    const tiltAvailable = kind === "pen"
        && (typeof raw.tiltX === "number" || typeof raw.tiltY === "number");

    const sample = C.sample({
        sequence: this.sequence,
        timeUs: timeUs,
        x: doc.x,
        y: doc.y,
        pressure: resolved.pressure,
        pressureAvailable: resolved.available,
        tiltXDeg: typeof raw.tiltX === "number" ? raw.tiltX : 0,
        tiltYDeg: typeof raw.tiltY === "number" ? raw.tiltY : 0,
        tiltAvailable: tiltAvailable,
        // Absent rather than fabricated. Nothing derives azimuth today, and a
        // defaulted 0 would tell an azimuth dynamic it had a reading. V2-04
        // owns deriving it from tilt if it wants one.
        azimuthRad: undefined,
        // MEASURED AND, IN LEGACY, READ BY NOTHING. `canvas-input.js:127`
        // normalises `twist` and no consumer exists. It reaches the contract
        // here so V2's does.
        barrelRotationRad: typeof raw.twist === "number"
            ? (raw.twist * Math.PI / 180) : undefined,
        pointer: kind,
        buttons: raw.buttons | 0,
        coalesced: !!(extra && extra.coalesced),
    });
    this.sequence += 1;
    return sample;
};

/**
 * Every canonical sample one dispatched event carries, in source order.
 *
 * PREDICTED EVENTS ARE NOT READ, and their absence is the mechanism rather
 * than an oversight. `getPredictedEvents` has zero occurrences in any
 * executable source in this repository; §8.2 requires that predicted samples
 * never affect committed geometry, replay, undo or recovery, and the way to
 * guarantee it is to never ask for them. `NormalizedSample` has no `predicted`
 * flag for the same reason: a predicted sample is not a canonical sample with
 * a flag set, it is one that must not exist in the stream at all.
 */
StrokeInput.prototype.samplesFrom = function (event, toDoc) {
    let list = null;
    if (event && typeof event.getCoalescedEvents === "function") {
        try { list = event.getCoalescedEvents(); } catch (e) { list = null; }
    }
    if (!list || !list.length) {
        // The three degraded paths -- method missing, method threw, empty list
        // -- all resolve to the dispatched event itself, marked NOT coalesced
        // so a consumer can tell a fallback from a genuine one-sample list.
        return [this.normalize(event, toDoc, null)];
    }

    const out = [];
    for (let i = 0; i < list.length; i++) {
        const raw = list[i];
        // A NULL ENTRY IS SKIPPED, NOT FATAL. CT2 wraps only the
        // `getCoalescedEvents()` call, so a null in the returned array throws
        // at `raw.clientX`, escapes the handler and kills the stroke mid-way.
        // §8.2 says input must not be dropped; losing one malformed entry is a
        // smaller loss than losing the rest of the contact.
        if (!raw || typeof raw !== "object") continue;
        // Deduped on the RAW client coordinates and timestamp, BEFORE the
        // document transform -- at high zoom the transform separates two
        // samples that were the same sample. Adjacent-only, because an
        // A->B->A path is three real samples.
        const key = raw.clientX + "|" + raw.clientY + "|" + raw.timeStamp;
        if (this._lastRaw === key) continue;
        this._lastRaw = key;
        out.push(this.normalize(raw, toDoc, { coalesced: true }));
    }
    if (!out.length) return [this.normalize(event, toDoc, null)];
    return out;
};

// ─────────────────────────────────────────────── deterministic record/replay

//: A recorded contact: the raw device records, in order, with nothing derived.
//:
//: RAW RATHER THAN NORMALISED, deliberately. A fixture of normalised samples
//: would replay through nothing -- it would BE the answer. Recording the device
//: input and replaying it through the same `StrokeInput` is what makes a replay
//: test able to fail.
const FIXTURE_VERSION = 1;

/** Record one dispatched event's raw payload, preserving grouping. */
function recordEvent(event) {
    const group = [];
    let list = null;
    if (event && typeof event.getCoalescedEvents === "function") {
        try { list = event.getCoalescedEvents(); } catch (e) { list = null; }
    }
    const source = (list && list.length) ? list : [event];
    for (let i = 0; i < source.length; i++) {
        const raw = source[i];
        if (!raw || typeof raw !== "object") continue;
        group.push({
            clientX: raw.clientX, clientY: raw.clientY,
            timeStamp: raw.timeStamp, pressure: raw.pressure,
            tiltX: raw.tiltX, tiltY: raw.tiltY, twist: raw.twist,
            pointerType: raw.pointerType, buttons: raw.buttons,
            isPrimary: raw.isPrimary,
        });
    }
    return group;
}

/**
 * Replay a recorded contact into a canonical stream.
 *
 * `groups` is an array of arrays: the outer level is dispatches, the inner
 * level is the samples the browser coalesced into each. **Regrouping the same
 * samples must not change the output**, which is what
 * `test_the_same_samples_regrouped_produce_the_same_stream` asserts and what
 * makes the fixtures independent of browser dispatch grouping.
 */
function replay(groups, toDoc, options) {
    const input = new StrokeInput(options);
    const out = [];
    for (let g = 0; g < groups.length; g++) {
        const group = groups[g] || [];
        const event = fakeDispatch(group);
        const samples = input.samplesFrom(event, toDoc);
        for (let s = 0; s < samples.length; s++) out.push(samples[s]);
    }
    return out;
}

/**
 * One recorded group as something `samplesFrom` can read.
 *
 * The dispatched event is the LAST of its coalesced samples, which is what the
 * Pointer Events specification says: "the coalesced list ends at the dispatched
 * event". A fake that put the first there would make the degraded path -- which
 * falls back to the dispatched event -- disagree with the coalesced path for a
 * reason the browser never produces.
 */
function fakeDispatch(group) {
    const last = group[group.length - 1] || {};
    const event = {};
    const keys = Object.keys(last);
    for (let i = 0; i < keys.length; i++) event[keys[i]] = last[keys[i]];
    event.getCoalescedEvents = function () { return group.slice(); };
    return event;
}

/** All samples flattened, losing the grouping. For the regrouping test. */
function flatten(groups) {
    const out = [];
    for (let g = 0; g < groups.length; g++) {
        const group = groups[g] || [];
        for (let i = 0; i < group.length; i++) out.push(group[i]);
    }
    return out;
}

/** Re-cut a flat sample list into groups of `size`. */
function regroup(flatSamples, size) {
    const out = [];
    const n = Math.max(1, size | 0);
    for (let i = 0; i < flatSamples.length; i += n) {
        out.push(flatSamples.slice(i, i + n));
    }
    return out;
}

window.StudioBrushInputV2 = {
    DEFAULT_MOUSE_PRESSURE: DEFAULT_MOUSE_PRESSURE,
    DEFAULT_UNKNOWN_PRESSURE: DEFAULT_UNKNOWN_PRESSURE,
    FIXTURE_VERSION: FIXTURE_VERSION,
    classify: classify,
    pressureIsMeasured: pressureIsMeasured,
    resolvePressure: resolvePressure,
    StrokeInput: StrokeInput,
    recordEvent: recordEvent,
    replay: replay,
    flatten: flatten,
    regroup: regroup,
};

})();
