/**
 * Forge Studio — Brush Engine V2 causal input filters (V2-04 / spec BE4)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§5.1, 5.2, 6.1.
 * Sits between normalization and arc-length resampling, which is where §6.1
 * puts it. Consumes `NormalizedSample`, produces `NormalizedSample`.
 *
 * CAUSAL, AND THAT IS A HARD PROPERTY. §4 forbids "post-release stroke cleanup
 * or curve replacement" and §5.1 says "No mode may redraw or 'beautify' an
 * entire stroke after release." So there is no method here that returns a
 * revised history: every output is produced from samples already seen, and once
 * emitted it is final.
 *
 * THREE LEGACY DECISIONS CARRIED, each with its measurement:
 *
 *   * THE WINDOW IS AN ARC LENGTH, NOT A SAMPLE COUNT. BE9 found the
 *     sample-count window was the larger of two rate-dependence defects --
 *     "at one event per segment a stroke covered 7,173 pixels where twenty
 *     events covered 14,096" -- because a window of N samples covers a quarter
 *     of the arc at 240 Hz that it covers at 60 Hz;
 *   * PRESSURE GETS A SHORTER WINDOW THAN POSITION, because "position jitter is
 *     a hand tremor an owner wants removed, while pressure lag is felt
 *     immediately as a brush that will not respond". §4.4's "position smoothing
 *     and pressure smoothing are independent" is this, made concrete;
 *   * THE WINDOW IS MEASURED IN SCREEN PIXELS. At 4x zoom the same hand
 *     movement covers a quarter of the document distance, so a document-space
 *     window would smooth four times as hard for no reason the owner asked for.
 *     The scale is INJECTED here rather than read from Canvas state.
 *
 * Review: `Evidence/source-review/V2-04-smoothing-and-dynamics.md`.
 */

(function () {
"use strict";

//: §5.2's three, and the only three.
const MODE_RAW = "raw";
const MODE_NATURAL = "natural";
const MODE_STABILIZED = "stabilized";
const MODES = Object.freeze([MODE_RAW, MODE_NATURAL, MODE_STABILIZED]);

//: Screen pixels of averaging per strength step.
//:
//: Legacy's value, and admitted as chosen rather than derived in exactly its
//: terms: "6 px per step puts the shipped presets (1 to 6) between 6 and 36
//: screen pixels of averaging... Chosen to land near the old feel, not
//: derived -- and stated as such rather than dressed up."
const SCREEN_PX_PER_STEP = 6;

//: Pressure's window as a fraction of position's. Legacy's 0.4, for the reason
//: in the module docstring: the two are different complaints and averaging
//: them over one window trades one for the other.
const PRESSURE_WINDOW_FRACTION = 0.4;

//: NATURAL IS ONE STEP. §5.1 requires the default to be "limited to microscopic
//: jitter correction" and §4.4 requires it to feel "immediate and clean, not
//: delayed or rubber-banded". Six screen pixels of averaging removes tremor and
//: is below the threshold at which lag is felt.
const NATURAL_STRENGTH = 1;

//: How sharp a turn has to be before corner preservation acts, in radians.
//: ~60 degrees: shallower than that is a curve the owner drew and wants
//: smoothed, sharper is a corner they meant.
//:
//: Chosen, not derived, and said so.
const CORNER_TURN_RAD = Math.PI / 3;

/**
 * @param spec {mode, strength, pressureStrength?, preserveCorners?, scale?}
 *
 * `strength` is in steps; `pressureStrength` defaults to `strength` and is
 * scaled by `PRESSURE_WINDOW_FRACTION` at use, so the two are independently
 * settable AND independently defaulted.
 *
 * `preserveCorners` DEFAULTS FALSE. §5.1: "Sharp-corner preservation MUST be a
 * separate toggle and MUST default off."
 *
 * `scale` is screen pixels per document pixel. Injected; the filter does not
 * read Canvas zoom.
 */
function StrokeFilter(spec) {
    const s = spec || {};
    const mode = String(s.mode || MODE_NATURAL);
    if (MODES.indexOf(mode) < 0) {
        throw new Error("brush filter: unknown smoothing mode");
    }
    this.mode = mode;
    this.strength = (s.strength === undefined)
        ? (mode === MODE_STABILIZED ? 4 : NATURAL_STRENGTH)
        : Math.max(0, Number(s.strength) || 0);
    this.pressureStrength = (s.pressureStrength === undefined)
        ? this.strength : Math.max(0, Number(s.pressureStrength) || 0);
    this.preserveCorners = !!s.preserveCorners;
    this.scale = Math.max(1e-6, Number(s.scale) || 1);
    this.points = [];
    this.emitted = 0;
}

/** The averaging window in DOCUMENT units, from a strength in steps. */
StrokeFilter.prototype.windowFor = function (strength) {
    return Math.max(1e-6, (strength * SCREEN_PX_PER_STEP) / this.scale);
};

/**
 * One canonical sample in, one filtered sample out.
 *
 * RAW IS IDENTITY, and returns the input object itself. §20 Correctness 3 asks
 * the three modes to be "measurably distinct", and a Raw that filtered even
 * slightly would make every measurement of the other two relative to an
 * unstated baseline.
 */
StrokeFilter.prototype.push = function (sample) {
    if (this.mode === MODE_RAW) {
        this.points.push(sample);
        this.emitted += 1;
        return sample;
    }
    this.points.push(sample);
    // The first sample of a contact is the contact point. §5.1 requires it
    // represented exactly, and there is nothing to average it with.
    if (this.points.length < 2) {
        this.emitted += 1;
        return sample;
    }

    // STRENGTH 0 IS OFF, NOT "an infinitesimally small window". §4.4 requires
    // position and pressure smoothing to be independent, and independence has
    // to include turning one of them fully off -- an infinitesimal window
    // returns the newest value to within floating-point epsilon, which is not
    // the same as returning it. The first draft did the latter and the
    // independence guard failed on rounding rather than on filtering.
    let x = sample.x, y = sample.y, pressure = sample.pressure;

    if (this.strength > 0) {
        let posWindow = this.windowFor(this.strength);
        // CORNER PRESERVATION shortens the position window at a detected turn,
        // so the filtered path passes closer to the vertex instead of cutting
        // inside it. It does not touch pressure: a corner is a geometric event.
        if (this.preserveCorners) {
            const turn = this._turnAt(this.points.length - 1);
            if (turn !== null && turn >= CORNER_TURN_RAD) posWindow = 0;
        }
        if (posWindow > 0) {
            x = this._centroid(posWindow, "x", sample.x);
            y = this._centroid(posWindow, "y", sample.y);
        }
    }

    if (this.pressureStrength > 0) {
        pressure = this._centroid(
            this.windowFor(this.pressureStrength * PRESSURE_WINDOW_FRACTION),
            "pressure", sample.pressure);
    }

    this.emitted += 1;
    return withPosition(sample, x, y, pressure);
};

/**
 * Pen up: the TRUE final sample, unfiltered.
 *
 * §5.1: "A stabilized stroke MUST flush real buffered samples to the actual
 * endpoint; it MUST NOT invent predicted continuation." A causal filter lags by
 * up to its window, and the fix is not to extrapolate -- that is invented
 * continuation -- but to emit what actually arrived. The sampler's `finish`
 * then places it exactly, and between them both halves of §5.1 hold.
 */
StrokeFilter.prototype.flush = function (finalSample) {
    if (!finalSample) return null;
    this.points.push(finalSample);
    this.emitted += 1;
    return finalSample;
};

/** The turn angle at point `i`, in radians, or null when undefined. */
StrokeFilter.prototype._turnAt = function (i) {
    const pts = this.points;
    if (i < 2) return null;
    const a = pts[i - 2], b = pts[i - 1], c = pts[i];
    const ax = b.x - a.x, ay = b.y - a.y;
    const bx = c.x - b.x, by = c.y - b.y;
    const la = Math.hypot(ax, ay), lb = Math.hypot(bx, by);
    if (la < 1e-9 || lb < 1e-9) return null;
    const cos = (ax * bx + ay * by) / (la * lb);
    return Math.acos(Math.max(-1, Math.min(1, cos)));
};

/**
 * The arc-length-weighted mean of one component over the last `windowDoc`
 * units of path, walking backwards from the newest sample.
 *
 * Each segment contributes its own midpoint weighted by the length taken from
 * it, and a partial segment contributes the midpoint of the part taken. That is
 * what makes it exact rather than approximate: subdividing a segment changes
 * how many terms the sum has and not what it adds up to -- which is precisely
 * why the result does not depend on the event rate.
 */
StrokeFilter.prototype._centroid = function (windowDoc, key, fallback) {
    const pts = this.points;
    let need = windowDoc, sum = 0, total = 0;
    for (let i = pts.length - 1; i > 0 && need > 0; i--) {
        const a = pts[i], b = pts[i - 1];
        const seg = Math.hypot(a.x - b.x, a.y - b.y);
        if (seg <= 1e-9) continue;
        const take = Math.min(seg, need);
        const t = take / seg;
        const mid = a[key] + (b[key] - a[key]) * (t / 2);
        sum += mid * take;
        total += take;
        need -= take;
    }
    return total > 0 ? sum / total : fallback;
};

/** A sample with new geometry, every other field carried unchanged. */
function withPosition(sample, x, y, pressure) {
    return Object.freeze({
        sequence: sample.sequence,
        timeUs: sample.timeUs,
        x: x, y: y,
        pressure: pressure,
        pressureAvailable: sample.pressureAvailable,
        tiltXDeg: sample.tiltXDeg,
        tiltYDeg: sample.tiltYDeg,
        tiltAvailable: sample.tiltAvailable,
        azimuthRad: sample.azimuthRad,
        barrelRotationRad: sample.barrelRotationRad,
        pointer: sample.pointer,
        buttons: sample.buttons,
        coalesced: sample.coalesced,
    });
}

/** Run a whole stream through one filter, flushing the true endpoint. */
function filterStream(samples, spec) {
    const filter = new StrokeFilter(spec);
    const out = [];
    for (let i = 0; i < samples.length - 1; i++) {
        out.push(filter.push(samples[i]));
    }
    if (samples.length) {
        // The last real sample is flushed rather than filtered, so the stroke
        // ends where the pen lifted.
        out.push(filter.flush(samples[samples.length - 1]));
    }
    return out;
}

window.StudioBrushFiltersV2 = {
    MODE_RAW: MODE_RAW,
    MODE_NATURAL: MODE_NATURAL,
    MODE_STABILIZED: MODE_STABILIZED,
    MODES: MODES,
    SCREEN_PX_PER_STEP: SCREEN_PX_PER_STEP,
    PRESSURE_WINDOW_FRACTION: PRESSURE_WINDOW_FRACTION,
    NATURAL_STRENGTH: NATURAL_STRENGTH,
    CORNER_TURN_RAD: CORNER_TURN_RAD,
    StrokeFilter: StrokeFilter,
    filterStream: filterStream,
};

})();
