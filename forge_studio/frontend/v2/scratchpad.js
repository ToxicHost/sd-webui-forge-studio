/**
 * Forge Studio — Brush Engine V2 standalone kernel and scratch pad (V2-06 / BE?)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§6.1, 14, 16.4, and
 * the overnight handoff §4.6.
 *
 * ONE PIPELINE, END TO END, WITH NO CANVAS UNDER IT:
 *
 *     normalize -> causal filter -> arc-length resample
 *               -> dynamics -> coverage -> merge into a test sink
 *
 * THIS IS AN ENGINEERING ACCEPTANCE SURFACE, NOT A SECOND PRODUCT UI (§4.6).
 * It exists so the kernel can be driven, measured and replayed without the
 * shipping compositor, and it depends on nothing from `canvas-core.js`.
 *
 * THE EXECUTION GUARD IS THE POINT OF THE TIMING HALF. §20 Performance 2
 * requires a benchmark to REFUSE to report timing if the real listener was not
 * reached, coordinates were outside the document, counters did not advance, or
 * pixels did not change. This repository has the scar: BE0's own record notes a
 * programme that "once reported a 0.0 ms dispatch for a stroke that never
 * reached the engine". So `run()` returns `timings: null` and a `refusal`
 * whenever it cannot prove work happened, and no caller can coax a number out
 * of it.
 *
 * Review: `Evidence/source-review/V2-06-scratchpad-and-gate2.md`.
 */

(function () {
"use strict";

const C = window.StudioBrushV2;
const I = window.StudioBrushInputV2;
const S = window.StudioBrushSamplerV2;
const F = window.StudioBrushFiltersV2;
const D = window.StudioBrushDynamicsV2;
const V = window.StudioBrushCoverageV2;

for (const [name, mod] of [["brush-contracts", C], ["input", I],
                           ["sampler", S], ["filters", F],
                           ["dynamics", D], ["coverage", V]]) {
    if (!mod) throw new Error("v2/scratchpad.js requires v2/" + name + ".js");
}

//: Why a run refused to report timings. Each is a condition §20 Performance 2
//: names, and each is a SEPARATE code so a reader learns which one fired
//: rather than that "something" did.
const REFUSAL_NO_SAMPLES = "no-samples-reached-the-kernel";
const REFUSAL_NO_MARKS = "no-marks-were-placed";
const REFUSAL_NO_PIXELS = "no-pixels-changed";
const REFUSAL_OUTSIDE = "every-sample-fell-outside-the-document";

//: §16.4's three, which "MAY change preview resolution, preview update
//: cadence, cache policy, and optional-GPU use" but "MUST NOT change canonical
//: geometry or final pixels".
const PERFORMANCE_MODES = Object.freeze(["auto", "responsive", "quality"]);

/** A clock the caller supplies, so a test can drive time instead of living it. */
function defaultClock() {
    return (typeof performance === "object" && performance
            && typeof performance.now === "function")
        ? performance.now() : Date.now();
}

/**
 * Run one recorded contact through the whole kernel.
 *
 * `groups` is the dispatch structure `input.js` records: an array of arrays,
 * outer level dispatches, inner level the samples the browser coalesced. Passed
 * through rather than flattened, so a run measures the real grouping.
 *
 * Returns diagnostics ALWAYS and timings only when work is provable.
 */
function run(groups, spec, options) {
    const o = options || {};
    const s = spec || {};
    const clock = typeof o.clock === "function" ? o.clock : defaultClock;
    const width = Math.max(1, s.width | 0 || 200);
    const height = Math.max(1, s.height | 0 || 200);

    const stages = { input: 0, filter: 0, render: 0, merge: 0 };
    let t0 = clock();

    // -- input ------------------------------------------------------------
    const input = new I.StrokeInput(
        { mousePressure: s.mousePressure });
    const toDoc = typeof o.toDoc === "function"
        ? o.toDoc : (x, y) => ({ x: x, y: y });
    const canonical = [];
    let coalescedCount = 0;
    for (let g = 0; g < groups.length; g++) {
        const group = groups[g] || [];
        if (!group.length) continue;
        const last = group[group.length - 1];
        const event = Object.assign({}, last);
        event.getCoalescedEvents = () => group.slice();
        const produced = input.samplesFrom(event, toDoc);
        for (const sample of produced) {
            canonical.push(sample);
            if (sample.coalesced) coalescedCount += 1;
        }
    }
    stages.input = clock() - t0;

    // How many samples actually landed inside the document. §20 Performance 2
    // names "coordinates were outside the document" as its own refusal.
    let inside = 0;
    for (const sample of canonical) {
        if (sample.x >= 0 && sample.y >= 0
            && sample.x < width && sample.y < height) inside += 1;
    }

    // -- filter -----------------------------------------------------------
    t0 = clock();
    const filtered = canonical.length
        ? F.filterStream(canonical, {
            mode: s.smoothingMode || F.MODE_NATURAL,
            strength: s.smoothing,
            pressureStrength: s.pressureSmoothing,
            preserveCorners: !!s.preserveCorners,
            scale: s.scale,
        })
        : [];
    stages.filter = clock() - t0;

    // -- resample + render ------------------------------------------------
    t0 = clock();
    const sizePx = Math.max(1, Number(s.sizePx) || 16);
    const spacingFraction = Number(s.spacingFraction) || 0.15;
    const placed = S.sampleStream(filtered, {
        spacingFraction: spacingFraction,
        sizePx: sizePx,
        extentFor: s.extentFor,
    });
    const marks = placed.dabs;

    const dep = V.depositionFor({
        hardness: s.hardness, flow: s.flow, opacity: s.opacity,
        density: s.density, buildup: s.buildup,
        step: spacingFraction * 2,
    });
    const buffer = new V.CoverageBuffer(width, height);
    const renderer = s.renderer || V.rendererFor({
        hardness: s.hardness === undefined ? 0.5 : s.hardness,
        textured: s.textured, scatter: s.scatter, ratio: s.ratio,
    });
    const radius = sizePx / 2;
    // Dynamics are evaluated PER MARK, which is where §6.1 puts them, and the
    // resolved size feeds the footprint.
    const rules = s.rules || null;
    let dynamicsApplied = 0;
    for (let i = 0; i < marks.length; i++) {
        const mark = marks[i];
        const ctx = {
            pressure: mark.pressure,
            pressureAvailable: filtered.length
                ? filtered[0].pressureAvailable : false,
            tiltXDeg: mark.tiltXDeg, tiltYDeg: mark.tiltYDeg,
            tiltAvailable: filtered.length ? filtered[0].tiltAvailable : false,
            headingRad: mark.headingRad,
            speed: i > 0 ? D.segmentSpeed(marks[i - 1], mark) : null,
        };
        const dyn = D.evaluate(rules, ctx);
        if (dyn !== D.NEUTRAL) dynamicsApplied += 1;
        const r = Math.max(0.5, radius * dyn.size);
        if (renderer === V.RENDER_SWEEP && i > 0) {
            V.sweep(buffer, marks[i - 1], mark, r, dep);
        } else {
            V.stamp(buffer, mark, r, dep);
        }
    }
    stages.render = clock() - t0;

    // -- merge ------------------------------------------------------------
    t0 = clock();
    const target = o.target || new Uint8Array(width * height);
    const before = countNonZero(target);
    V.merge(buffer, target, {
        opacity: s.buildup ? 1 : (s.opacity === undefined ? 1 : s.opacity),
        selection: o.selection || null,
        erase: !!s.erase,
    });
    const after = countNonZero(target);
    stages.merge = clock() - t0;

    // -- the execution guard ----------------------------------------------
    //
    // ORDERED FROM THE EARLIEST FAILURE, so the reported refusal names the
    // first thing that went wrong rather than the last symptom of it.
    let refusal = null;
    if (!canonical.length) refusal = REFUSAL_NO_SAMPLES;
    else if (!inside) refusal = REFUSAL_OUTSIDE;
    else if (!marks.length) refusal = REFUSAL_NO_MARKS;
    else if (V.paintedPixels(buffer) === 0) refusal = REFUSAL_NO_PIXELS;

    return {
        diagnostics: Object.freeze({
            dispatches: groups.length,
            samples: canonical.length,
            coalescedSamples: coalescedCount,
            samplesInsideDocument: inside,
            filtered: filtered.length,
            marks: marks.length,
            contributions: buffer.contributions,
            dynamicsApplied: dynamicsApplied,
            paintedPixels: V.paintedPixels(buffer),
            totalCoverage: V.totalCoverage(buffer),
            // U1. §16.3 requires dirty bounds to travel "from the first
            // coverage primitive THROUGH COMMIT", so the seam that carries the
            // kernel's result has to carry them too. A pipeline that computed
            // exact bounds and then dropped them at its own boundary would
            // satisfy every test inside `coverage.js` and deliver nothing an
            // integrator could use.
            dirty: buffer.dirty.toJSON(),
            // What the run actually had to touch, which is the number that says
            // whether the kernel is bounded end to end rather than only in the
            // module where it is asserted.
            visitedPixels: buffer.stats().visitedPixels,
            targetPixelsBefore: before,
            targetPixelsAfter: after,
            renderer: renderer,
            smoothingMode: s.smoothingMode || F.MODE_NATURAL,
            overlapK: dep.overlapK,
            fEff: dep.fEff,
            stationaryUs: placed.sampler.stationaryUs,
        }),
        // NULL, NOT ZERO, when the run could not prove it did work. A zero
        // reads as "instant" and is exactly the confident wrong number the
        // guard exists to prevent.
        timings: refusal ? null : Object.freeze({
            input: stages.input,
            filter: stages.filter,
            render: stages.render,
            merge: stages.merge,
            total: stages.input + stages.filter + stages.render + stages.merge,
        }),
        refusal: refusal,
        target: target,
        buffer: buffer,
    };
}

function countNonZero(bytes) {
    let n = 0;
    for (let i = 0; i < bytes.length; i++) if (bytes[i]) n += 1;
    return n;
}

/**
 * Two runs of one recording, compared byte for byte.
 *
 * §20 Correctness 10 requires deterministic replay to be pixel-stable, and this
 * is the surface that demonstrates it end to end rather than per module.
 */
function replayMatches(groups, spec, options) {
    const a = run(groups, spec, options);
    const b = run(groups, spec, options);
    if (a.refusal || b.refusal) return { equal: false, refusal: a.refusal || b.refusal };
    const ta = a.target, tb = b.target;
    if (ta.length !== tb.length) return { equal: false, refusal: null };
    for (let i = 0; i < ta.length; i++) {
        if (ta[i] !== tb[i]) return { equal: false, refusal: null, at: i };
    }
    return { equal: true, refusal: null };
}

window.StudioBrushScratchpadV2 = {
    REFUSAL_NO_SAMPLES: REFUSAL_NO_SAMPLES,
    REFUSAL_NO_MARKS: REFUSAL_NO_MARKS,
    REFUSAL_NO_PIXELS: REFUSAL_NO_PIXELS,
    REFUSAL_OUTSIDE: REFUSAL_OUTSIDE,
    PERFORMANCE_MODES: PERFORMANCE_MODES,
    run: run,
    replayMatches: replayMatches,
};

})();
