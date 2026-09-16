/**
 * The owner-visible contracts. Engine-neutral: nothing here knows what engine
 * it is judging, and nothing here may import an engine.
 *
 * WHY THESE CONTRACTS AND NOT OTHERS. Sixteen work packages passed their own
 * tests while the assembled brush felt like a fill tool, because every one of
 * them set acceptance on a quantity INSIDE the engine -- dab count, peak alpha
 * in the accumulator, resolved spacing fraction. All of those moved, so all of
 * them passed. Not one asserted what a painter would see. Every contract below
 * is phrased as something a person can do with a pen and then look at.
 *
 * STATUS VALUES.
 *
 *   PASS      the metric separated a known-good case from a known-different
 *             one, and this engine is on the good side.
 *   FAIL      same, and this engine is not.
 *   EVIDENCE  measured and recorded, but no honest threshold has been
 *             established yet. NOT a pass. The V2 handoff is explicit that an
 *             uncalibrated row must be reported as evidence collected rather
 *             than quietly counted as green.
 *
 * A contract may only report PASS if its `calibration` field names the
 * controlled case that proves the metric can tell the difference.
 */

"use strict";

const { FIXTURES, RATE_FIXTURES, TARGET, CORRIDOR } = require("./fixtures.js");

//: Levels of 255 below which a difference is not visible on a monitor.
const VISIBLE = 8;

// ───────────────────────────────────────────────────────────── self-validation

class VacuousRun extends Error {}

/**
 * Paint one fixture and REFUSE TO RETURN if the run proves nothing.
 *
 * The programme this oracle exists because of once reported a 0.0 ms dispatch
 * for a stroke that never reached the engine, and a spacing sweep with a spread
 * of exactly 0.00 produced by a sweep that swept nothing. Both would be caught
 * here.
 */
function paint(adapter, brush, fixture, opts) {
    const o = opts || {};
    const t = adapter.createTarget(TARGET);
    adapter.selectBrush(t, brush);
    adapter.beginStroke(t, fixture.down);
    for (const s of fixture.samples) adapter.addSample(t, s);
    let timeTicks = 0;
    if (fixture.holdMs) timeTicks = adapter.advanceTime(t, fixture.holdMs);
    if (o.holdMs) timeTicks += adapter.advanceTime(t, o.holdMs);
    adapter.endStroke(t, fixture.lift);

    const coverage = adapter.readCoverage(t);
    const diag = adapter.diagnostics(t);

    const expectedSamples = 1 + fixture.samples.length;
    if (diag.acceptedSamples !== expectedSamples) {
        throw new VacuousRun(
            `${fixture.id}: adapter accepted ${diag.acceptedSamples} samples, `
            + `fixture supplied ${expectedSamples}`);
    }
    if (!o.mayPaintNothing && diag.changedPixels === 0) {
        throw new VacuousRun(
            `${fixture.id}: the stroke changed no pixels at all`);
    }
    if (diag.bounds) {
        const b = diag.bounds, e = fixture.expectBoundsWithin;
        if (b.x0 < 0 || b.y0 < 0 || b.x1 >= TARGET.width || b.y1 >= TARGET.height) {
            throw new VacuousRun(`${fixture.id}: the mark left the target: ${JSON.stringify(b)}`);
        }
        if (e && (b.x0 < e.x0 || b.y0 < e.y0 || b.x1 > e.x1 || b.y1 > e.y1)) {
            throw new VacuousRun(
                `${fixture.id}: the mark landed outside where the fixture says `
                + `it should: got ${JSON.stringify(b)}, expected within `
                + `${JSON.stringify(e)}`);
        }
    }
    return { coverage, diag, timeTicks, target: t };
}

/** Two fixtures that turn out to be the same fixture prove nothing either. */
function assertActuallyDifferent(label, a, b) {
    if (a.length !== b.length) return;
    let same = true;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) { same = false; break; }
    if (same) {
        throw new VacuousRun(
            `${label}: the two cases being compared are pixel-identical, so the `
            + "comparison cannot mean anything");
    }
}

// ──────────────────────────────────────────────────────────────────── metrics

function coreMean(cov) {
    let sum = 0, n = 0;
    for (let x = CORRIDOR.x0 + 40; x <= CORRIDOR.x1 - 40; x++) {
        sum += cov[CORRIDOR.y * TARGET.width + x];
        n += 1;
    }
    return +(sum / n).toFixed(2);
}

function fractionDiffering(a, b) {
    let any = 0, diff = 0;
    for (let i = 0; i < a.length; i++) {
        if (a[i] || b[i]) any += 1;
        if (Math.abs(a[i] - b[i]) > VISIBLE) diff += 1;
    }
    return any ? +(diff / any).toFixed(4) : 0;
}

function totalCoverage(cov) {
    let s = 0;
    for (let i = 0; i < cov.length; i++) s += cov[i];
    return s;
}

function coveredAt(cov, x, y) {
    const xi = Math.round(x), yi = Math.round(y);
    if (xi < 0 || yi < 0 || xi >= TARGET.width || yi >= TARGET.height) return 0;
    return cov[yi * TARGET.width + xi];
}

/** How far coverage reaches beyond a point, along a direction, in px. */
function reachBeyond(cov, x, y, dx, dy) {
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    let last = 0;
    for (let d = 1; d <= 60; d++) {
        if (coveredAt(cov, x + ux * d, y + uy * d) > 0) last = d;
    }
    return last;
}

/**
 * The sharpest KINK along a scan line, in levels per pixel.
 *
 * A second difference, deliberately: `max()` of two smooth bumps is continuous
 * but has a gradient discontinuity where they are equal, and that corner is
 * what the eye reads as a hard edge inside a soft stroke. Measuring darkness
 * instead would let a stroke that merely got lighter pass.
 */
function sharpestKink(cov, y, x0, x1) {
    let worst = 0;
    for (let x = x0 + 1; x < x1 - 1; x++) {
        const i = y * TARGET.width + x;
        const d2 = Math.abs(cov[i - 1] - 2 * cov[i] + cov[i + 1]);
        if (d2 > worst) worst = d2;
    }
    return worst;
}

/**
 * Where a turn's crease would be, from the fixture's own declared vertices.
 *
 * Returns `{ vx, vy, bx, by }`: the vertex, and the unit INTERIOR bisector --
 * the direction into the wedge between the two arms, which is the line a
 * `max()` seam runs along.
 *
 * A STRAIGHT STROKE IS A 180-DEGREE TURN and is handled here rather than
 * special-cased by the caller. Its arms are antiparallel so their sum is zero
 * and there is no interior wedge; the bisector is then the left normal of the
 * outgoing arm, which puts the control measurement at the same offset from the
 * centreline that a real corner's scan sits at from its vertex. That is what
 * makes the floor comparable with the thing it is the floor of.
 */
function bisectorFrame(vertices) {
    const n = vertices.length;
    if (n < 2) throw new Error("a corner frame needs at least two vertices");
    let vx, vy, ax, ay, bx0, by0;
    if (n >= 3) {
        const mid = (n / 2) | 0;
        [vx, vy] = vertices[mid];
        [ax, ay] = vertices[mid - 1];
        [bx0, by0] = vertices[mid + 1];
    } else {
        [ax, ay] = vertices[0];
        [bx0, by0] = vertices[1];
        vx = (ax + bx0) / 2; vy = (ay + by0) / 2;
    }
    const la = Math.hypot(ax - vx, ay - vy) || 1;
    const lb = Math.hypot(bx0 - vx, by0 - vy) || 1;
    const uax = (ax - vx) / la, uay = (ay - vy) / la;
    const ubx = (bx0 - vx) / lb, uby = (by0 - vy) / lb;
    let sx = uax + ubx, sy = uay + uby;
    const ls = Math.hypot(sx, sy);
    if (ls < 1e-6) { sx = uby; sy = -ubx; }          // antiparallel: left normal
    else { sx /= ls; sy /= ls; }
    return { vx, vy, bx: sx, by: sy };
}

/**
 * The sharpest kink along a line PERPENDICULAR to a turn's bisector.
 *
 * THE SCAN LINE USED TO BE HARDCODED -- row 184, columns 100 to 240 -- and it
 * suited exactly one of the two fixtures it was applied to. `acuteCorner`'s
 * vertex is at (160, 210) and that row crosses its bisector 26px out, inside
 * the mark, which is why the metric worked at all. `rightAngle`'s vertex is at
 * (220, 120) and its bisector runs down-left, so row 184 crosses it at
 * x = 156 -- 64px from either arm, on empty canvas with a 20px tip. The floor
 * was worse: it read row 184 of a stroke painted at y = 160, entirely outside
 * the mark. So the contract passed a restored max-blend seam because two of
 * its three measurements were of nothing.
 *
 * Placed relative to the geometry instead: `outRadii` tip radii along the
 * bisector from the vertex, then across it for `spanRadii` radii either side.
 * The offset matters -- at the vertex both arms are saturated and a seam has no
 * gradient to break, so the measurement has to sit out in the falloff where the
 * two coverage fields actually meet.
 */
function sharpestKinkAcrossBisector(cov, frame, radius,
                                    outRadii = 1.3, spanRadii = 2.0) {
    const cx = frame.vx + frame.bx * radius * outRadii;
    const cy = frame.vy + frame.by * radius * outRadii;
    const px = -frame.by, py = frame.bx;            // perpendicular, unit
    const half = radius * spanRadii;
    let worst = 0;
    for (let d = -half + 1; d <= half - 1; d += 1) {
        const a = coveredAt(cov, cx + px * (d - 1), cy + py * (d - 1));
        const b = coveredAt(cov, cx + px * d, cy + py * d);
        const c = coveredAt(cov, cx + px * (d + 1), cy + py * (d + 1));
        const d2 = Math.abs(a - 2 * b + c);
        if (d2 > worst) worst = d2;
    }
    return worst;
}

// ──────────────────────────────────────────────────────────────── the contracts

function row(id, status, opts) {
    return Object.assign({ id, status }, opts);
}

const CONTRACTS = [
    {
        id: "flow-is-not-opacity",
        title: "Flow and Opacity are behaviourally distinct",
        run(adapter) {
            const brushes = ["round-basic", "round-soft", "round-hard", "pencil"];
            const detail = {};
            let worst = 1;
            for (const id of brushes) {
                const base = { id, sizePx: 30, hardness: undefined };
                const flowLow = paint(adapter,
                    Object.assign({}, base, { flow: 0.35, opacity: 1.0 }),
                    FIXTURES.workedArea);
                const opLow = paint(adapter,
                    Object.assign({}, base, { flow: 1.0, opacity: 0.35 }),
                    FIXTURES.workedArea);
                const f = fractionDiffering(flowLow.coverage, opLow.coverage);
                detail[id] = {
                    workedAtFlow35: coreMean(flowLow.coverage),
                    workedAtOpacity35: coreMean(opLow.coverage),
                    fractionDiffering: f,
                };
                worst = Math.min(worst, f);
            }
            return row("flow-is-not-opacity", worst >= 0.5 ? "PASS" : "FAIL", {
                metric: "fraction of painted pixels differing by more than "
                    + `${VISIBLE}/255, after eight passes over one corridor`,
                units: "fraction 0..1",
                threshold: 0.5,
                thresholdKind: "calibrated",
                calibration: "The engine before BE17 scored exactly 0.0000 on "
                    + "this comparison for Basic Round, Soft Round, Hard Ink "
                    + "and Pencil -- Flow and Opacity were the same control. "
                    + "0.5 sits between that and the 0.87-1.00 measured after.",
                observed: worst,
                detail,
            });
        },
    },

    {
        id: "one-pass-cannot-separate-them",
        title: "A single non-overlapping pass deliberately CANNOT separate them",
        run(adapter) {
            const detail = {};
            let worst = 0;
            for (const id of ["round-basic", "round-soft", "round-hard"]) {
                const a = paint(adapter,
                    { id, sizePx: 30, flow: 0.35, opacity: 1.0 },
                    FIXTURES.singlePassForComparison);
                const b = paint(adapter,
                    { id, sizePx: 30, flow: 1.0, opacity: 0.35 },
                    FIXTURES.singlePassForComparison);
                const gap = Math.abs(coreMean(a.coverage) - coreMean(b.coverage));
                detail[id] = { atFlow35: coreMean(a.coverage),
                               atOpacity35: coreMean(b.coverage), gap };
                worst = Math.max(worst, gap);
            }
            return row("one-pass-cannot-separate-them",
                worst <= 3 ? "PASS" : "FAIL", {
                metric: "difference in core coverage between (Flow 35, Opacity "
                    + "100) and (Flow 100, Opacity 35) on ONE clean pass",
                units: "levels of 255",
                threshold: 3,
                thresholdKind: "derived",
                calibration: "Exact by definition rather than calibrated: one "
                    + "pass deposits Flow and the stroke is then bounded by "
                    + "Opacity, so the product is the same either way. This row "
                    + "exists to stop a future engine 'fixing' the contract "
                    + "above by making a straight line depend on the split.",
                observed: worst,
                detail,
            });
        },
    },

    {
        id: "working-an-area-builds",
        title: "Going over your own mark inside one contact adds paint",
        run(adapter) {
            const detail = {};
            let worstGain = 1e9;
            for (const id of ["round-basic", "round-soft", "pencil"]) {
                const brush = { id, sizePx: 30, flow: 0.35, opacity: 1.0 };
                const one = paint(adapter, brush, FIXTURES.singlePassForComparison);
                const eight = paint(adapter, brush, FIXTURES.workedArea);
                assertActuallyDifferent(`working-an-area/${id}`,
                    one.coverage, eight.coverage);
                const gain = +(coreMean(eight.coverage) - coreMean(one.coverage))
                    .toFixed(2);
                detail[id] = { onePass: coreMean(one.coverage),
                               eightPasses: coreMean(eight.coverage), gain };
                worstGain = Math.min(worstGain, gain);
            }
            return row("working-an-area-builds",
                worstGain > VISIBLE ? "PASS" : "FAIL", {
                metric: "levels gained in the corridor between one pass and "
                    + "eight, within a single pen contact",
                units: "levels of 255",
                threshold: VISIBLE,
                thresholdKind: "derived",
                calibration: "The engine before BE17 gained 0.05 levels over "
                    + "eight passes on Soft Round -- scrubbing was inert. The "
                    + "bound is the visibility floor, so any pass is a gain a "
                    + "person can actually see.",
                observed: worstGain,
                detail,
            });
        },
    },

    {
        id: "opacity-bounds-the-contact",
        title: "Opacity is a ceiling the contact reaches and stops at",
        run(adapter) {
            const detail = {};
            let ok = true;
            for (const opacity of [1.0, 0.5, 0.35]) {
                const brush = { id: "round-basic", sizePx: 30, flow: 0.35, opacity };
                const worked = paint(adapter, brush, FIXTURES.workedArea);
                const core = coreMean(worked.coverage);
                const ceiling = Math.round(255 * opacity);
                detail[`opacity${Math.round(opacity * 100)}`] = {
                    core, ceiling, over: +(core - ceiling).toFixed(2),
                };
                if (core > ceiling + 2) ok = false;
            }
            return row("opacity-bounds-the-contact", ok ? "PASS" : "FAIL", {
                metric: "worked coverage against `round(255 * opacity)`",
                units: "levels of 255",
                threshold: 2,
                thresholdKind: "exact",
                calibration: "Exact: the ceiling is the arithmetic product. The "
                    + "tolerance is 8-bit rounding, not a fudge. A model that "
                    + "made Opacity a rate rather than a bound overshoots it.",
                observed: Math.max(...Object.values(detail).map(d => d.over)),
                detail,
            });
        },
    },

    {
        id: "airbrush-builds-from-time",
        title: "Stationary buildup comes from elapsed time, not pointer spam",
        run(adapter) {
            const brush = { id: "airbrush", sizePx: 30, flow: 0.15, opacity: 0.5 };
            const pressOnly = paint(adapter, brush,
                Object.assign({}, FIXTURES.stationaryHold, { holdMs: 0 }));
            const held = paint(adapter, brush, FIXTURES.stationaryHold);
            assertActuallyDifferent("airbrush-builds-from-time",
                pressOnly.coverage, held.coverage);
            const gained = +(totalCoverage(held.coverage)
                - totalCoverage(pressOnly.coverage)).toFixed(0);
            const ticks = held.timeTicks;
            return row("airbrush-builds-from-time",
                (gained > 0 && ticks > 0) ? "PASS" : "FAIL", {
                metric: "total coverage gained over 500 ms of stillness, with "
                    + "ZERO additional pointer samples",
                units: "summed levels of 255",
                threshold: 0,
                thresholdKind: "derived",
                calibration: "The comparison case is the same fixture with the "
                    + "clock not advanced: identical samples, no time. Any gain "
                    + "is therefore attributable to time alone, which is the "
                    + "whole claim. Charging timed deposits the SPATIAL overlap "
                    + "divisor made this gain zero, which is how the defect was "
                    + "found.",
                observed: gained,
                detail: { pressOnly: totalCoverage(pressOnly.coverage),
                          afterHold: totalCoverage(held.coverage),
                          timerTicks: ticks },
            });
        },
    },

    {
        id: "spacing-is-not-a-darkness-control",
        title: "Spacing changes continuity, not how dark one pass is",
        run(adapter) {
            // MEASURED AT THE DAB, NOT AVERAGED ALONG THE MARK. The mean along
            // a stroke legitimately falls as spacing widens, because the dabs
            // stop touching and the pixels between them have less paint for the
            // honest reason that no tip was ever there. That is DiVerdi Fig
            // 2.6's un-smooth silhouette -- a continuity consequence -- and
            // folding it into this number would report a scalloped stroke as a
            // darkness bug.
            //
            // AND MEASURED ON ONE PASS, which is what the calibration below is
            // a calibration OF. The first version of this contract asserted the
            // EIGHT-pass spread against a threshold derived from the old
            // engine's ONE-pass spread: two different quantities, compared as
            // though they were one. That is the exact mistake this whole oracle
            // exists to stop, and it very nearly shipped inside it.
            const band = [0.02, 0.05, 0.10, 0.20];
            const detail = {};
            let previous = null, anyPlacementChange = false;

            const peakOf = (cov) => {
                let peak = 0;
                for (let x = CORRIDOR.x0 + 40; x <= CORRIDOR.x1 - 40; x++) {
                    const v = cov[CORRIDOR.y * TARGET.width + x];
                    if (v > peak) peak = v;
                }
                return peak;
            };

            const onePassPeaks = [], workedPeaks = [];
            for (const spacing of band) {
                const brush = { id: "round-basic", sizePx: 30, flow: 0.4,
                                opacity: 1.0, spacing };
                const one = paint(adapter, brush, FIXTURES.singlePassForComparison);
                const worked = paint(adapter, brush, FIXTURES.workedArea);
                onePassPeaks.push(peakOf(one.coverage));
                workedPeaks.push(peakOf(worked.coverage));
                detail[`spacing${Math.round(spacing * 100)}`] = {
                    onePassPeak: peakOf(one.coverage),
                    workedPeak: peakOf(worked.coverage),
                    onePassMean: coreMean(one.coverage),
                };
                if (previous && fractionDiffering(previous, one.coverage) > 0) {
                    anyPlacementChange = true;
                }
                previous = one.coverage;
            }

            // A sweep that changed nothing would pass a spread test trivially,
            // which is how a driver that wrote the wrong field once reported a
            // spread of exactly 0.00 across six spacings at three hardnesses.
            if (!anyPlacementChange) {
                throw new VacuousRun(
                    "spacing-is-not-a-darkness-control: moving the spacing "
                    + "changed no pixels at all, so the sweep swept nothing");
            }

            const onePassSpread = Math.max(...onePassPeaks) - Math.min(...onePassPeaks);
            const workedSpread = Math.max(...workedPeaks) - Math.min(...workedPeaks);
            detail.onePassPeakSpread = onePassSpread;
            detail.workedPeakSpread = workedSpread;
            detail.spacingActuallyChangedTheMark = anyPlacementChange;
            detail.knownLimit = "The worked spread is recorded, not asserted. "
                + "The model prices a dab by how many dabs cover a pixel, and "
                + "that count is an over-estimate of at most one dab -- "
                + "negligible when a pixel sees fifty, proportionally large "
                + "when it sees two. Repeated passes compound the relative "
                + "error, so working an area at Spacing 20 reaches "
                + `${workedPeaks[workedPeaks.length - 1]} where Spacing 2 `
                + `reaches ${workedPeaks[0]}. That residual is real, it is `
                + "outside the calibrated claim, and it is the first thing a "
                + "V2 coverage model should improve on.";

            return row("spacing-is-not-a-darkness-control",
                onePassSpread <= 30 ? "PASS" : "FAIL", {
                metric: "spread in PEAK coverage of ONE pass, where the brush "
                    + "actually landed, across the shipped spacing band",
                units: "levels of 255",
                threshold: 30,
                thresholdKind: "calibrated",
                calibration: "Like for like: the engine's own accumulate branch "
                    + "before BE17 spread 53.3 / 80.6 / 94.5 of 255 across this "
                    + "slider ON ONE PASS at hardness 0 / 0.5 / 1.0 -- spacing "
                    + "was the strongest darkness control on those presets. 30 "
                    + "sits below the smallest of those and above the 4-24 "
                    + "measured after. The sensitivity of the metric itself is "
                    + "guarded by `spacingActuallyChangedTheMark`, which "
                    + "refuses the row outright if the sweep is inert.",
                observed: onePassSpread,
                detail,
            });
        },
    },

    {
        id: "event-rate-invariance",
        title: "The same path at 30, 60, 120 and 240 Hz paints the same mark",
        run(adapter) {
            const brush = { id: "round-soft", sizePx: 30, flow: 0.4, opacity: 1.0 };
            const runs = RATE_FIXTURES.map(f => ({
                hz: f.hz, fixture: f, out: paint(adapter, brush, f),
            }));
            const ref = runs[0];
            const detail = {};
            let worstFraction = 0, worstCoverage = 0;
            for (const r of runs) {
                const frac = fractionDiffering(ref.out.coverage, r.out.coverage);
                const cov = totalCoverage(r.out.coverage);
                const refCov = totalCoverage(ref.out.coverage);
                const covRatio = refCov ? +(cov / refCov).toFixed(4) : 0;
                detail[`hz${r.hz}`] = {
                    samples: r.fixture.samples.length,
                    totalCoverage: cov,
                    coverageRatioVs30Hz: covRatio,
                    fractionDifferingVs30Hz: frac,
                    bounds: r.out.diag.bounds,
                };
                if (r.hz !== ref.hz) {
                    worstFraction = Math.max(worstFraction, frac);
                    worstCoverage = Math.max(worstCoverage, Math.abs(1 - covRatio));
                }
            }
            // The rates must actually differ, or the whole row is vacuous.
            const counts = new Set(runs.map(r => r.fixture.samples.length));
            if (counts.size !== runs.length) {
                throw new VacuousRun(
                    "event-rate-invariance: the rate fixtures do not differ in "
                    + "sample count");
            }
            // WHERE the difference lives decides what it means. A rate
            // dependence in the BODY is the BE3 defect -- the same stroke
            // coming out darker because the browser reported more often. A
            // difference confined to the antialiased RIM at direction changes
            // is sub-pixel rounding, which is a different and much smaller
            // claim. Reporting one number for both would conflate them.
            let bodyDiffer = 0, bodyAny = 0;
            const ref30 = ref.out.coverage;
            const fastest = runs[runs.length - 1].out.coverage;
            for (let i = 0; i < ref30.length; i++) {
                const hi = Math.max(ref30[i], fastest[i]);
                if (hi <= 200) continue;
                bodyAny += 1;
                if (Math.abs(ref30[i] - fastest[i]) > VISIBLE) bodyDiffer += 1;
            }
            const bodyFraction = bodyAny ? +(bodyDiffer / bodyAny).toFixed(4) : 0;

            return row("event-rate-invariance", "EVIDENCE", {
                metric: "fraction of painted pixels differing from the 30 Hz "
                    + `run by more than ${VISIBLE}/255, split into the solid `
                    + "body and the antialiased rim",
                units: "fraction 0..1",
                threshold: null,
                thresholdKind: "uncalibrated",
                calibration: "NOT ESTABLISHED, and deliberately not invented. "
                    + "Three known-different candidates were tried and none "
                    + "separated: (a) a no-debt-carry mutation on a soft tip "
                    + "scored 0.038 against the shipped 0.042; (b) the same "
                    + "mutation on a hard tip at wide spacing scored 0.000 on "
                    + "both, because overlapping solid dabs union to the same "
                    + "band wherever they land; (c) the mutation turns out to "
                    + "be rate-INVARIANT while badly wrong -- it suppresses "
                    + "nearly every dab and the endpoint catch-up then draws "
                    + "the mark, which is rate-independent. The engine before "
                    + "BE3 cannot be used directly: it predates "
                    + "`alphaMapToImageData`, so the neutral coverage read does "
                    + "not exist on it. Until a mutation is found that "
                    + "reproduces rate-dependent DARKNESS, this row is evidence "
                    + "and not a pass.",
                observed: { allPixels: worstFraction, solidBody: bodyFraction },
                detail: Object.assign(detail, {
                    worstCoverageRatioDrift: +worstCoverage.toFixed(4),
                    solidBodyFractionDiffering: bodyFraction,
                    note: "Measured on the shipped engine: bounds identical at "
                        + "every rate, ZERO differing pixels in the solid body, "
                        + "total coverage within 1.7%, and 793 of 798 differing "
                        + "pixels within 40px of a direction change, worst case "
                        + "31/255. That is consistent with BE3 holding and with "
                        + "sub-pixel rim rounding at corners -- but consistency "
                        + "is not a calibrated pass.",
                }),
            });
        },
    },

    {
        id: "exact-endpoints",
        title: "The mark starts where the pen went down and ends where it lifted",
        run(adapter) {
            const base = { id: "round-hard", sizePx: 16, flow: 1.0, opacity: 1.0 };
            const brush = base;
            const detail = {};
            let ok = true;
            // A STABILISED PASS IS IN THE LIST ON PURPOSE. A filter that lags
            // ends the mark behind the lifted pen, and the owner ruling is
            // explicit that a stabilised stroke "consumes its real pending
            // samples and ends at the actual pen-up position". Without this
            // case the contract passes on an engine that quietly stops short
            // whenever smoothing is on -- which is most of the time for line
            // work, and is exactly the defect BE9 was opened for.
            const CASES = [
                ["straightPass", base],
                ["shortMark", base],
                ["tap", base],
                ["fastLongLine", base],
                ["straightPassStabilised",
                 Object.assign({}, base, { smoothing: 6 })],
            ];
            for (const [key, spec] of CASES) {
                const f = FIXTURES[key.replace("Stabilised", "")];
                const out = paint(adapter, spec, f);
                const atDown = coveredAt(out.coverage, f.down.x, f.down.y);
                const end = f.lift || f.samples[f.samples.length - 1] || f.down;
                const atUp = coveredAt(out.coverage, end.x, end.y);
                // How far past the lift the mark keeps going, along travel.
                const prev = f.samples.length
                    ? f.samples[Math.max(0, f.samples.length - 2)] : f.down;
                const dx = end.x - prev.x, dy = end.y - prev.y;
                // A TAP HAS NO DIRECTION OF TRAVEL, so it has no tail. The
                // first version of this contract measured one anyway: with
                // dx = dy = 0 the ray degenerates to the same pixel sixty
                // times, every one of them covered, and the tap reported a
                // 60px tail on an engine that had done nothing wrong. The
                // harness was the defect, which is exactly what the
                // self-validation elsewhere in this file exists to catch.
                const hasDirection = (dx * dx + dy * dy) > 1e-9;
                const tail = hasDirection
                    ? reachBeyond(out.coverage, end.x, end.y, dx, dy) : null;
                detail[key] = { atDown, atUp, tailBeyondLiftPx: tail,
                                measuredTail: hasDirection };
                if (atDown === 0 || atUp === 0) ok = false;
                // The tip legitimately extends its own radius past the lift.
                if (hasDirection && tail > spec.sizePx) ok = false;
            }
            return row("exact-endpoints", ok ? "PASS" : "FAIL", {
                metric: "coverage at the pen-down and pen-up positions, and how "
                    + "far the mark continues past the lift",
                units: "levels of 255; px",
                threshold: `> 0 at both ends; tail <= tip radius (${16})`,
                thresholdKind: "derived",
                calibration: "The tap and short-mark fixtures are the "
                    + "known-different cases: a long-line-only fixture passes "
                    + "this row on an engine that drops short strokes entirely, "
                    + "and a stabiliser that lags ends the mark behind the "
                    + "pointer, which shows as zero coverage at the lift.",
                observed: detail,
                detail,
            });
        },
    },

    {
        id: "corner-leaves-no-crease",
        title: "A soft stroke that turns sharply has no hard edge inside it",
        run(adapter) {
            const brush = { id: "round-soft", sizePx: 40, flow: 0.4,
                            opacity: 1.0, hardness: 0 };
            const detail = {};
            let worst = 0;
            const radius = brush.sizePx / 2;
            for (const key of ["acuteCorner", "rightAngle"]) {
                const out = paint(adapter, brush, FIXTURES[key]);
                // Across the bisector, out in the falloff where the two arms'
                // coverage fields meet -- placed from the fixture's own
                // declared vertices rather than at a row that suited one of
                // the two. See `sharpestKinkAcrossBisector`.
                const frame = bisectorFrame(FIXTURES[key].vertices);
                const kink = sharpestKinkAcrossBisector(
                    out.coverage, frame, radius);
                detail[key] = { sharpestKink: kink, scanAt: frame };
                worst = Math.max(worst, kink);
            }
            // A straight stroke is the floor: whatever a smooth soft edge
            // produces on this metric is not a crease. Measured by the SAME
            // function at the SAME offset -- a straight stroke is a
            // 180-degree turn -- so the two numbers are comparable rather
            // than merely both present.
            const straight = paint(adapter, brush, FIXTURES.straightPass);
            const floor = sharpestKinkAcrossBisector(
                straight.coverage,
                bisectorFrame(FIXTURES.straightPass.vertices), radius);
            detail.straightStrokeFloor = floor;
            // EVIDENCE, NOT PASS, and the demotion is the honest result of
            // measuring the row rather than trusting it. See `calibration`.
            return row("corner-leaves-no-crease", "EVIDENCE", {
                metric: "sharpest second difference of coverage along a scan "
                    + "line crossing the turn's bisector",
                units: "levels of 255 per pixel",
                threshold: null,
                thresholdKind: "uncalibrated",
                calibration: "THIS ROW CANNOT CURRENTLY BE QUOTED AS A RESULT, "
                    + "and the reason is measured rather than suspected. "
                    + "Evidence/oracle-repair/. "
                    + "(1) THE SCAN WAS IN THE WRONG PLACE and now is not: it "
                    + "was hardcoded at row 184, which crosses acuteCorner's "
                    + "bisector 26px out but crosses rightAngle's 64px from "
                    + "either arm -- on empty canvas with a 20px tip -- and "
                    + "read row 184 of a straight stroke painted at y=160 for "
                    + "its floor. Two of three measurements were of nothing. "
                    + "It is placed from each fixture's own declared vertices "
                    + "now. (2) THAT DID NOT MAKE THE MUTATION DETECTABLE, and "
                    + "the reason is the fixture: restoring the max blend "
                    + "changes acuteCorner by ZERO pixels of 18,301. A 30-degree "
                    + "turn does not overlap itself, and below 45 degrees the "
                    + "two engines are bit-identical (corner_angle_sweep.json). "
                    + "(3) NEITHER DOES A BETTER FIXTURE, because the METRIC "
                    + "does not discriminate: swept over offsets 0.8-2.0 radii "
                    + "the shipped engine reaches 6 and the max-blend engine "
                    + "reaches 9 (rightAngle 6/8, a 120-degree turn 5/9, "
                    + "acuteCorner 5/5). Two populations that overlap at 6 "
                    + "cannot carry a threshold. The old '11.36 vs 0.93' "
                    + "calibration was measured on the pre-BE17 engine, which "
                    + "is a different thing from BE17 with _depositAt reverted. "
                    + "WHAT THE NEXT ATTEMPT MUST BEAT: the image-level "
                    + "difference IS large and well-behaved -- 400-500 pixels "
                    + "differing with a worst delta near 20 at a 115-125 degree "
                    + "turn -- so a metric aimed at the inside of the turn "
                    + "rather than at a second difference along one line has "
                    + "something real to find.",
                observed: worst,
                detail,
            });
        },
    },

    {
        id: "controls-do-not-lie",
        title: "Every declared-supported control changes the mark",
        run(adapter) {
            // The independent observation the declaration is checked against:
            // two renders, not a second reading of the same table.
            const probes = [
                { control: "angle",
                  a: { angleDeg: 0 }, b: { angleDeg: 60 } },
                { control: "spikes",
                  a: { spikes: 2 }, b: { spikes: 8 } },
            ];
            const caps = adapter.capabilities();
            const detail = {};
            const lying = [];
            const understated = [];
            for (const brush of caps.brushes) {
                if (brush.id === "pixel") continue;   // aliased by contract
                detail[brush.id] = {};
                for (const probe of probes) {
                    const base = { id: brush.id, sizePx: 40, flow: 1.0,
                                   opacity: 1.0, followStroke: false };
                    const A = paint(adapter, Object.assign({}, base, probe.a),
                        FIXTURES.straightPass);
                    const B = paint(adapter, Object.assign({}, base, probe.b),
                        FIXTURES.straightPass);
                    const frac = fractionDiffering(A.coverage, B.coverage);
                    const declared = brush.supports[probe.control];
                    const live = frac > 0.01;
                    detail[brush.id][probe.control] = {
                        declared, fractionDiffering: frac, observedLive: live,
                    };
                    if (declared === true && !live) {
                        lying.push(`${brush.id}.${probe.control}`);
                    }
                    if (declared !== true && live) {
                        understated.push(`${brush.id}.${probe.control}`);
                    }
                }
            }
            return row("controls-do-not-lie",
                lying.length === 0 ? "PASS" : "FAIL", {
                metric: "for every control the engine declares supported, the "
                    + "fraction of pixels that change when it moves",
                units: "fraction 0..1",
                threshold: 0.01,
                thresholdKind: "calibrated",
                calibration: "The declaration and the observation come from "
                    + "different places on purpose: `capabilities()` is what "
                    + "the engine claims, and the two renders are what it does. "
                    + "A table generated from the declaration it is meant to "
                    + "verify would pass on any engine. Known-different: on a "
                    + "flat tip, Angle 0 against 60 moves most of the mark.",
                observed: { lying, understated },
                detail,
            });
        },
    },
];

module.exports = { CONTRACTS, VacuousRun, paint, VISIBLE,
                   coreMean, fractionDiffering, totalCoverage, sharpestKink };
