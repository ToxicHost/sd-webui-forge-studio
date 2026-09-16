/**
 * Forge Studio — Brush Engine V2 dynamics evaluation (V2-04 / spec BE4)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§5.3, 10. Runs AFTER
 * arc-length resampling, which is where §6.1 puts it: a rule reads what a
 * placed mark measured and returns the factors the renderer applies.
 *
 * THE EVALUATOR IS LEGACY'S (`canvas-core.js:1268-1293`) and three of its
 * decisions are carried verbatim:
 *
 *   * A MISSING MEASUREMENT RETURNS null, and the rule's OWN declared fallback
 *     applies -- "rather than a number this function picked on the rule's
 *     behalf". §8.1 requires a missing sensor to fall back without disabling an
 *     otherwise usable preset, and a fallback the rule declares is the only
 *     kind that can be right for that rule;
 *   * ANGLE ADDS, IN DEGREES; EVERY OTHER TARGET MULTIPLIES. "A multiplicative
 *     angle is meaningless -- zero degrees times anything is zero degrees";
 *   * SPEED IS MEASURED PER SEGMENT, NOT PER MARK. "The dabs along one segment
 *     are interpolated positions between two real samples; they share the hand
 *     movement that produced them, and a per-dab speed would be an invention
 *     with a plausible shape."
 *
 * TWO OF THE BRIEF'S FIVE TARGETS ARE DELIBERATELY ABSENT, with Legacy's
 * reasons intact. `opacity` is applied once at commit and bounds the whole
 * stroke, so a per-mark curve on it would have to choose which mark it was
 * right about -- Flow is the per-mark quantity and is offered instead. `grain`
 * is applied once to accumulated coverage precisely so overlap cannot wash the
 * texture out, and a per-mark grain factor would restore the amount-versus-rate
 * defect that removed.
 *
 * Review: `Evidence/source-review/V2-04-smoothing-and-dynamics.md`.
 */

(function () {
"use strict";

//: What a rule may read. Each is honest about being unavailable.
const INPUTS = Object.freeze(["pressure", "speed", "direction", "tilt"]);

//: What a rule may drive. See the module docstring for the two that are not
//: here and why.
const TARGETS = Object.freeze(["size", "flow", "angle", "ratio"]);

//: How fast a hand must move for `speed` to read 1, in DOCUMENT pixels per
//: millisecond. 2 px/ms is a quick flick across a 1024 document in half a
//: second. Legacy's value, and admitted there as "a feel constant, and dressing
//: it up as a measurement would be worse than saying so".
const SPEED_REF = 2;

//: Five shapes and a flat. Each is monotonic on 0..1 and each maps 0 to 0 and
//: 1 to 1 except `flat`.
const CURVES = Object.freeze({
    linear: v => v,
    easeIn: v => v * v,
    easeOut: v => 1 - (1 - v) * (1 - v),
    sShape: v => v * v * (3 - 2 * v),
    //: Late and sudden: nothing until most of the way, then quickly. The one
    //: that makes a light sketch pass read as a sketch.
    sharp: v => v * v * v * v,
    //: Ignores the input and returns the top of the range. Its use is to say
    //: "this target is pinned" in a preset without deleting the rule.
    flat: () => 1,
});

//: §5.3: "The ordinary UI MUST present: Soft; Balanced; Firm; Custom."
//: Named shapes over the same curves, so the owner picks a feel and the
//: advanced editor stays optional rather than required for ordinary use.
const FEEL_PRESETS = Object.freeze({
    soft: "easeOut",
    balanced: "linear",
    firm: "easeIn",
});

//: Neutral, and shared. Never handed out where a caller could keep it.
const NEUTRAL = Object.freeze({ size: 1, flow: 1, angle: 0, ratio: 1 });

/**
 * One input's measurement, or null.
 *
 * NULL MEANS "NOT MEASURED", never "zero". The distinction is the whole reason
 * `pressureAvailable` exists: a mouse reports a perfectly well-formed 0.5 and
 * it means nothing.
 */
function inputValue(name, ctx) {
    switch (name) {
        case "pressure":
            // The AVAILABILITY flag, not the value.
            if (!ctx || !ctx.pressureAvailable) return null;
            return ctx.pressure;
        case "speed":
            // Requires a timestamp. Legacy's `plotTo` never had one, so every
            // headless driver reported no speed at all; V2's samples always
            // carry one, and an absent speed here means the caller did not
            // compute a segment.
            if (!ctx || typeof ctx.speed !== "number") return null;
            return Math.max(0, Math.min(1, ctx.speed / SPEED_REF));
        case "direction":
            if (!ctx || typeof ctx.headingRad !== "number") return null;
            // Normalised to 0..1 over a full turn.
            return ((ctx.headingRad % (Math.PI * 2)) + Math.PI * 2)
                % (Math.PI * 2) / (Math.PI * 2);
        case "tilt":
            if (!ctx || !ctx.tiltAvailable) return null;
            // Magnitude of the tilt vector, normalised over 90 degrees.
            return Math.min(1, Math.hypot(ctx.tiltXDeg || 0, ctx.tiltYDeg || 0)
                / 90);
        default:
            return null;
    }
}

/**
 * Resolve every rule against one mark's context.
 *
 * Returns the shared NEUTRAL object when there are no rules -- the
 * overwhelmingly common case, and it costs one length check.
 */
function evaluate(rules, ctx) {
    if (!rules || !rules.length) return NEUTRAL;
    const out = { size: 1, flow: 1, angle: 0, ratio: 1 };
    for (let i = 0; i < rules.length; i++) {
        const r = rules[i];
        if (!r || out[r.target] === undefined) continue;
        if (INPUTS.indexOf(r.input) < 0) continue;
        const measured = inputValue(r.input, ctx);
        const v = measured === null
            ? (typeof r.fallback === "number" ? r.fallback : 0)
            : measured;
        const shape = CURVES[r.curve] || CURVES.linear;
        const clamped = v < 0 ? 0 : (v > 1 ? 1 : v);
        const min = typeof r.min === "number" ? r.min : 0;
        const max = typeof r.max === "number" ? r.max : 1;
        const factor = min + (max - min) * shape(clamped);
        // ANGLE ADDS. See the module docstring.
        if (r.target === "angle") out.angle += factor;
        else out[r.target] *= factor;
    }
    return Object.freeze(out);
}

/** A rule from a named feel, so the ordinary UI needs no curve editor. */
function ruleFromFeel(target, input, feel, options) {
    const o = options || {};
    return Object.freeze({
        input: input,
        target: target,
        curve: FEEL_PRESETS[feel] || "linear",
        min: typeof o.min === "number" ? o.min : 0,
        max: typeof o.max === "number" ? o.max : 1,
        // Declared per rule, never chosen by the evaluator.
        fallback: typeof o.fallback === "number" ? o.fallback : 1,
    });
}

/**
 * Segment speed in document pixels per millisecond, or null.
 *
 * PER SEGMENT, not per mark, and null rather than a guess when the interval is
 * not positive -- two samples sharing a frame timestamp have no measurable
 * speed between them, and dividing by zero to produce Infinity would feed a
 * curve a value it was never defined over.
 */
function segmentSpeed(fromSample, toSample) {
    if (!fromSample || !toSample) return null;
    const dtUs = toSample.timeUs - fromSample.timeUs;
    if (!(dtUs > 0)) return null;
    const dist = Math.hypot(toSample.x - fromSample.x, toSample.y - fromSample.y);
    return dist / (dtUs / 1000);
}

window.StudioBrushDynamicsV2 = {
    INPUTS: INPUTS,
    TARGETS: TARGETS,
    CURVES: CURVES,
    FEEL_PRESETS: FEEL_PRESETS,
    SPEED_REF: SPEED_REF,
    NEUTRAL: NEUTRAL,
    inputValue: inputValue,
    evaluate: evaluate,
    ruleFromFeel: ruleFromFeel,
    segmentSpeed: segmentSpeed,
};

})();
