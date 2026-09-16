/**
 * Forge Studio — Brush Engine V2 arc-length sampler (V2-03 / spec BE3)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§5.1, 9.1-9.4.
 * Takes the canonical `NormalizedSample` stream and decides WHERE marks are
 * placed, by distance travelled through the document and never by how often
 * the browser reported a position.
 *
 * NOT LOADED BY THE PAGE, and renders nothing. It emits placement records; a
 * renderer turns them into coverage (V2-05).
 *
 * THE MODEL IS LEGACY'S, THE CODE IS NOT. BE3, BE7 and BE9 solved this once and
 * four of their decisions are carried because re-deriving them would mean
 * re-discovering their defects:
 *
 *   * the carried quantity is a DEBT -- "how much further the stroke must
 *     travel before the next dab is due" -- so a segment shorter than the debt
 *     can emit nothing and still pay down what it can. A residual cannot
 *     express that;
 *   * the anchor stays on the POINTER, not on the last emitted dab. Moving it
 *     would cut the corner at every event boundary;
 *   * only a MISSING debt is refilled, never a zero one. Legacy learned this
 *     from a mutation: the looser test silently rescued a zeroed seed, and "a
 *     defence that makes a defect invisible is not a defence". Here the
 *     distinction is in the type -- `null` means unseeded -- rather than in a
 *     numeric predicate;
 *   * the gap is priced at the tip's extent ALONG TRAVEL, because a chisel
 *     presents several times the width along its axis that it presents across
 *     it. That extent is INJECTED here rather than computed: tip geometry
 *     belongs to the renderer, and a sampler that owned a tip table would be
 *     the second place tips are described.
 *
 * ONE DELIBERATE STRENGTHENING. Legacy's endpoint flush is conditional -- "BE3's
 * spacing debt still governs whether any dab is due at all". The V2 spec is
 * not: §5.1 requires pen-down and pen-up to be represented EXACTLY, and §20
 * Input 5 requires a pen-up with backlog to reach the exact endpoint. So
 * `finish` places the final point whenever a mark is not already there, and the
 * resulting short last gap is correct -- a stroke ends where the pen lifted.
 *
 * Review: `Evidence/source-review/V2-03-arc-length-sampling.md`.
 */

(function () {
"use strict";

//: The floor on a gap, in document pixels. Legacy clamps at half a pixel and
//: the reason is arithmetic rather than taste: a gap below one pixel cannot
//: separate two marks, and a gap approaching zero makes the emit loop
//: unbounded on a finite segment.
const MIN_GAP_PX = 0.5;

//: What a placement came from. Carried so a consumer can tell an ordinary
//: path mark from the two that are placed by rule.
const SOURCE_BEGIN = "begin";
const SOURCE_PATH = "path";
const SOURCE_FINISH = "finish";

const DAB_FIELDS = Object.freeze([
    "index", "x", "y", "pressure", "tiltXDeg", "tiltYDeg",
    "timeUs", "travelPx", "gapPx", "headingRad", "source",
]);

/** A circle: the same extent in every direction. */
function unitExtent() { return 1.0; }

/**
 * @param spec {spacingFraction, sizePx, extentFor?, pressureToSize?}
 *
 * `extentFor(travelAngleRad)` returns the tip's radius along that heading as a
 * fraction of the nominal. Defaulting to a circle is what lets V2-03 be correct
 * for anisotropic tips without owning a tip table -- V2-05 supplies the real
 * one.
 *
 * `pressureToSize(pressure)` returns the diameter at that pressure, or is
 * absent when size does not follow pressure. Legacy prices the NEXT gap at
 * THIS dab's pressure, so a stroke that thins also tightens its spacing, and
 * that behaviour is kept.
 */
function ArcSampler(spec) {
    const s = spec || {};
    this.spacingFraction = Math.max(1e-6, Number(s.spacingFraction) || 0.05);
    this.sizePx = Math.max(0.5, Number(s.sizePx) || 12);
    this.extentFor = typeof s.extentFor === "function" ? s.extentFor : unitExtent;
    this.pressureToSize = typeof s.pressureToSize === "function"
        ? s.pressureToSize : null;

    // null means UNSEEDED. A debt of zero is a real value that something set,
    // and refilling it would hide whatever set it.
    this.debt = null;
    this.last = null;
    this.travelPx = 0;
    this.index = 0;
    this.stationaryUs = 0;
    this.lastEmitted = null;
}

/** The gap this pressure and heading earn, in document pixels. */
ArcSampler.prototype.gapFor = function (pressure, headingRad) {
    const px = this.pressureToSize
        ? Math.max(1, this.pressureToSize(pressure))
        : this.sizePx;
    const extent = (headingRad === null || headingRad === undefined)
        ? 1.0 : this.extentFor(headingRad);
    return Math.max(MIN_GAP_PX, px * extent * this.spacingFraction);
};

/**
 * Pen down. The opening mark is placed EXACTLY at the contact point.
 *
 * §5.1: "Pen-down and pen-up positions MUST be represented exactly in canonical
 * geometry." No heading exists yet, so the first gap is priced isotropically --
 * the same thing Legacy's `beginStroke` seed does, and the reason
 * `alongExtentFor` answers 1.0 for a caller with no heading.
 */
ArcSampler.prototype.begin = function (sample) {
    this.last = sample;
    this.travelPx = 0;
    this.index = 0;
    this.stationaryUs = 0;
    this.debt = this.gapFor(sample.pressure, null);
    const dab = this._place(sample.x, sample.y, sample.pressure,
                            sample.tiltXDeg, sample.tiltYDeg, sample.timeUs,
                            null, SOURCE_BEGIN);
    return [dab];
};

/**
 * One more canonical sample. Returns zero or more placements.
 *
 * A SEGMENT SHORTER THAN THE DEBT EMITS NOTHING AND KEEPS ITS DISTANCE, which
 * is §4.3's "allow a short sub-spacing move to emit no intermediate dab without
 * losing its distance" and the case a residual formulation cannot express.
 */
ArcSampler.prototype.push = function (sample) {
    if (!this.last) return this.begin(sample);
    const x0 = this.last.x, y0 = this.last.y;
    const dx = sample.x - x0, dy = sample.y - y0;
    const dist = Math.hypot(dx, dy);

    // STATIONARY: no distance, so no mark is due. §4.3 requires this stated
    // explicitly rather than manufactured as motion -- Airbrush buildup is
    // time-driven and is §9.4's, a different unit. The elapsed time is
    // accumulated so that unit can read it.
    if (!(dist > 0)) {
        const dt = sample.timeUs - this.last.timeUs;
        if (dt > 0) this.stationaryUs += dt;
        this.last = sample;
        return [];
    }

    const heading = Math.atan2(dy, dx);
    const out = [];
    let travelled = 0;
    let debt = this.debt;
    // Only a MISSING debt is refilled. See the module docstring.
    if (debt === null || !isFinite(debt)) {
        debt = this.gapFor(this.last.pressure, heading);
    }

    while (travelled + debt <= dist) {
        travelled += debt;
        const t = travelled / dist;
        // INTERPOLATED AT THE EMITTED POSITION, not copied from the latest
        // event. §9.2 lists position, pressure, tilt and time; copying the
        // newest sample would make a dab halfway along a segment claim the
        // pressure measured at its far end.
        const pressure = lerp(this.last.pressure, sample.pressure, t);
        const tiltX = lerp(this.last.tiltXDeg, sample.tiltXDeg, t);
        const tiltY = lerp(this.last.tiltYDeg, sample.tiltYDeg, t);
        const timeUs = Math.round(lerp(this.last.timeUs, sample.timeUs, t));
        out.push(this._place(x0 + dx * t, y0 + dy * t, pressure, tiltX, tiltY,
                             timeUs, heading, SOURCE_PATH));
        // The NEXT gap is priced at THIS mark's pressure and heading, so a
        // stroke that thins also tightens, and a chisel spaces itself by the
        // width it is presenting.
        debt = this.gapFor(pressure, heading);
    }

    // Whatever is left of this segment pays down the next mark's debt. This is
    // the line that makes the sampler independent of event rate AND of
    // dispatch grouping: the remainder survives the boundary.
    this.debt = debt - (dist - travelled);
    this.travelPx += dist;
    this.last = sample;
    return out;
};

/**
 * Pen up. Walks any remaining distance, then places the exact final point.
 *
 * UNCONDITIONAL, and that is the one place this diverges from Legacy. §5.1 and
 * §20 Input 5 require the endpoint exactly; Legacy lets the spacing debt veto
 * it. The short final gap that results is correct: a stroke ends where the pen
 * lifted, not where the arithmetic last landed.
 *
 * A mark already at the final position is not duplicated -- "represented
 * exactly" is satisfied, and a second mark there would deposit twice.
 */
ArcSampler.prototype.finish = function (sample) {
    const out = sample ? this.push(sample) : [];
    const end = sample || this.last;
    if (!end) return out;
    const at = this.lastEmitted;
    if (at && Math.hypot(at.x - end.x, at.y - end.y) < 1e-9) return out;
    const heading = at
        ? Math.atan2(end.y - at.y, end.x - at.x)
        : null;
    out.push(this._place(end.x, end.y, end.pressure, end.tiltXDeg,
                         end.tiltYDeg, end.timeUs, heading, SOURCE_FINISH));
    return out;
};

/**
 * `gapPx` is MEASURED FROM THE PREVIOUS MARK, not taken from the debt.
 *
 * The debt at emit time is only the REMAINDER of the gap whenever the previous
 * mark fell in an earlier segment -- the rest was paid there. Reporting it
 * under-states the real spacing, and the first version of this file did exactly
 * that: a chisel whose gap should have doubled along its axis reported an
 * unchanged 5 while placing half as many marks. The counts were right and the
 * field was wrong, which is the worst combination because the number looks
 * measured.
 *
 * Legacy has the same construction (`canvas-core.js:3053` passes `debt` to the
 * stamp), so its value carries the same under-statement into BE17's overlap
 * divisor. Not repaired there -- Legacy is frozen -- but not reproduced here.
 */
ArcSampler.prototype._place = function (x, y, pressure, tiltX, tiltY, timeUs,
                                        headingRad, source) {
    const at = this.lastEmitted;
    const dab = Object.freeze({
        index: this.index,
        x: x, y: y,
        pressure: pressure,
        tiltXDeg: tiltX, tiltYDeg: tiltY,
        timeUs: timeUs,
        travelPx: this.travelPx,
        gapPx: at ? Math.hypot(x - at.x, y - at.y) : 0,
        headingRad: headingRad,
        source: source,
    });
    this.index += 1;
    this.lastEmitted = dab;
    return dab;
};

function lerp(a, b, t) { return a + (b - a) * t; }

/**
 * Run a whole canonical stream through one sampler.
 *
 * `groups` is the dispatch structure from `input.js`; flattening it here would
 * defeat the test that the debt survives a dispatch boundary, so the grouping
 * is preserved and the sampler simply does not care.
 */
function sampleStream(samples, spec, options) {
    const sampler = new ArcSampler(spec);
    const out = [];
    const opts = options || {};
    for (let i = 0; i < samples.length; i++) {
        const s = samples[i];
        const dabs = (i === 0) ? sampler.begin(s) : sampler.push(s);
        for (let d = 0; d < dabs.length; d++) out.push(dabs[d]);
    }
    if (opts.finish !== false && samples.length) {
        const tail = sampler.finish(null);
        for (let d = 0; d < tail.length; d++) out.push(tail[d]);
    }
    return { dabs: out, sampler: sampler };
}

window.StudioBrushSamplerV2 = {
    MIN_GAP_PX: MIN_GAP_PX,
    DAB_FIELDS: DAB_FIELDS,
    SOURCE_BEGIN: SOURCE_BEGIN,
    SOURCE_PATH: SOURCE_PATH,
    SOURCE_FINISH: SOURCE_FINISH,
    ArcSampler: ArcSampler,
    sampleStream: sampleStream,
};

})();
