/**
 * The brush adapter boundary. Engine-neutral, by construction.
 *
 * WHY THIS FILE HAS NO ENGINE IN IT. The contracts in `contracts.js` must be
 * able to judge Brush Engine V2 without V2 having to implement any of Legacy's
 * private state. So the contracts talk only to the surface described here, and
 * every engine supplies its own adapter. `legacy_adapter.js` is the first one;
 * a V2 adapter is the second, and writing it is the whole point.
 *
 * WHAT IS DELIBERATELY ABSENT, and this is the load-bearing design decision:
 *
 *   THERE IS NO DAB COUNT.
 *
 * A dab is a STAMPING concept. DiVerdi 2.6.2 describes sweeping as the other
 * half of the design space -- Illustrator's calligraphic brush computes the
 * swept area analytically and never stamps anything -- and the V2 handoff
 * commits to a hybrid renderer that sweeps simple hard line art and stamps
 * textured paint. An oracle that counted dabs would declare a correct swept
 * renderer broken, and would be measuring the implementation rather than the
 * product.
 *
 * The same reasoning removes: alpha maps, spacing fractions, tip frames,
 * accumulators, and anything named after a Legacy function. What survives is
 * what a painter can see and what an engine of any construction must be able
 * to answer.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * THE SURFACE
 * ─────────────────────────────────────────────────────────────────────────
 *
 *   name                                  string, for reports
 *   capabilities()                        what this engine offers, see below
 *   createTarget({width, height})         a fresh transparent surface
 *   selectBrush(target, BrushSpec)        the brush the owner picked
 *   beginStroke(target, Sample)           pen down
 *   addSample(target, Sample)             one pointer sample
 *   advanceTime(target, ms)               wall time passes with the pen still
 *   endStroke(target, Sample|null)        pen up, at a position or wherever
 *   cancelStroke(target)                  the stroke is abandoned
 *   readCoverage(target)                  Uint8Array, 0..255, WHAT REACHES THE
 *                                         LAYER -- after every bound the engine
 *                                         applies at commit, never a private
 *                                         accumulator
 *   diagnostics(target)                   proof of execution, see below
 *
 * Sample:    { x, y, t, pressure }   document pixels, milliseconds, 0..1
 * BrushSpec: { id, sizePx, flow, opacity, hardness?, spacing?, buildup? }
 *
 * capabilities() -> {
 *   brushes: [ { id, label, tip, supports: { angle, spikes, followStroke,
 *                                            rotationJitter, ... } } ],
 *   canAdvanceTime: boolean,
 * }
 *
 * `supports` values are `true`, `false`, or a string reason such as
 * "needs-shape". A string means the control is conditionally live and the
 * condition is not currently met. The control-honesty contract checks this
 * DECLARATION against what the renderer actually does, so an engine that
 * declares everything true fails rather than passes.
 *
 * diagnostics() -> { acceptedSamples, changedPixels, totalCoverage, bounds }
 *
 * These exist for one reason: to make a vacuous run impossible to report as a
 * result. The programme this oracle exists because of once reported a 0.0 ms
 * dispatch for a stroke that never reached the engine.
 */

"use strict";

/** Every method an adapter must provide, checked before a run is trusted. */
const REQUIRED = [
    "name", "capabilities", "createTarget", "selectBrush", "beginStroke",
    "addSample", "advanceTime", "endStroke", "cancelStroke", "readCoverage",
    "diagnostics",
];

/**
 * Refuse an adapter that cannot answer the whole surface.
 *
 * A partially implemented adapter would produce contract rows that look like
 * results and are actually holes, which is exactly the failure mode this
 * oracle was built to prevent.
 */
function validateAdapter(adapter) {
    const missing = REQUIRED.filter(k => typeof adapter[k] === "undefined");
    if (missing.length) {
        throw new Error(
            `adapter "${adapter && adapter.name}" is missing: ${missing.join(", ")}`);
    }
    for (const k of REQUIRED) {
        if (k === "name") continue;
        if (typeof adapter[k] !== "function") {
            throw new Error(`adapter.${k} must be a function`);
        }
    }
    return adapter;
}

module.exports = { REQUIRED, validateAdapter };
