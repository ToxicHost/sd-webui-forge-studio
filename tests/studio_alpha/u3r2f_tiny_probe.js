/**
 * U3-R2F F1 probe — tiny/subpixel round-tip coverage.
 *
 * THE DEFECT, correctly stated. The handoff describes "radius <= 0.5 can paint
 * nothing" via `nd >= 1`. That is the worst phase of a wider problem: `stamp`
 * and `sweep` asked the radial profile for its value at ONE point per pixel, so
 * for a tip about a pixel across the result depended violently on where the tip
 * landed between pixel centres -- 1600% swing in total paint at radius 0.25,
 * 178% at 0.5, 107% at 1.0, and empty at the phase an axis-aligned stroke on
 * integer coordinates always produces.
 *
 * WHY LEGACY LOOKED FINE. It point-samples too, floors the radius at 0.5 too,
 * and rejects `nd >= 1` too. It differs only in where a pixel's sample point is:
 * `dy = py - ccy`, the pixel INDEX as its centre, which its own comment states.
 * So the commonest case lands ON Legacy's samples and BETWEEN V2's. Neither
 * engine touches Canvas2D for the alpha map, so this is arithmetic rather than a
 * browser rasteriser artefact -- and V2's half-pixel convention is the standard
 * one, consistent with its own dirty rects, so flipping it would move every
 * stroke half a pixel to fix a subpixel case.
 *
 * THE REPAIR integrates the profile over the pixel square for tips below radius
 * 2, using a fixed symmetric 4x4 grid. Radius >= 2 is untouched, because §26
 * makes changing ordinary output a stop condition.
 *
 *     node tests/studio_alpha/u3r2f_tiny_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const COVERAGE = path.resolve(
    __dirname, "..", "..", "forge_studio", "frontend", "v2", "coverage.js");

function load() {
    const sb = { console, Math, Object, Array, JSON, Number, String,
                 Uint8Array, Uint16Array };
    sb.window = sb;
    vm.createContext(sb);
    vm.runInContext(fs.readFileSync(COVERAGE, "utf8"), sb, { filename: COVERAGE });
    return sb.window.StudioBrushCoverageV2;
}
const K = load();

const RADII = [0.25, 0.40, 0.49, 0.50, 0.51, 0.60, 0.75, 1.00, 1.50];
const HARDNESSES = [0, 0.25, 0.5, 0.85, 1];
const OFFSETS = [0, 0.25, 0.5, 0.75];
const ANGLES = [0, 22.5, 45, 67.5, 90];

function dep(hardness) {
    return K.depositionFor({ hardness, flow: 1, opacity: 1, density: 1,
                             buildup: false, step: 0.3 });
}

function measure(buf) {
    let painted = 0, total = 0, maxA = 0;
    for (let i = 0; i < buf.acc.length; i++) {
        const b = buf.acc[i] >> 8;
        const v = b > buf.peak[i] ? b : buf.peak[i];
        if (v > 0) { painted += 1; total += v; if (v > maxA) maxA = v; }
    }
    return { painted, total, maxA };
}

/** A dot at a given subpixel phase. */
function dot(radius, hardness, ox, oy) {
    const buf = new K.CoverageBuffer(40, 40);
    K.stamp(buf, { x: 20 + ox, y: 20 + oy }, radius, dep(hardness));
    return measure(buf);
}

/**
 * A short swept line at a given angle and phase.
 *
 * `renderedLength` is the length the KERNEL actually swept: below the radius
 * floor both endpoints are placed on the pixel lattice, and that changes a
 * fixed-length fixture's length by up to ~0.7 px. Reporting total ink without
 * it makes an endpoint-quantisation artefact look like anisotropy.
 */
function line(radius, hardness, angleDeg, ox, oy) {
    const buf = new K.CoverageBuffer(60, 60);
    const rad = angleDeg * Math.PI / 180;
    const cx = 30 + ox, cy = 30 + oy, L = 10;
    let from = { x: cx - Math.cos(rad) * L, y: cy - Math.sin(rad) * L };
    let to = { x: cx + Math.cos(rad) * L, y: cy + Math.sin(rad) * L };
    K.sweep(buf, from, to, radius, dep(hardness));
    if (radius <= 0.5) {
        from = { x: Math.floor(from.x) + 0.5, y: Math.floor(from.y) + 0.5 };
        to = { x: Math.floor(to.x) + 0.5, y: Math.floor(to.y) + 0.5 };
    }
    const out = measure(buf);
    out.renderedLength = Math.sqrt((to.x - from.x) * (to.x - from.x)
                                   + (to.y - from.y) * (to.y - from.y));
    return out;
}

const out = {};

// ── §9 non-empty: every accepted radius, hardness, alignment and angle ─────
(function () {
    const empties = [];
    let checked = 0;
    for (const r of RADII) {
        for (const h of HARDNESSES) {
            for (const ox of OFFSETS) {
                for (const oy of OFFSETS) {
                    checked += 1;
                    if (dot(r, h, ox, oy).painted === 0) {
                        empties.push({ kind: "dot", r, h, ox, oy });
                    }
                }
            }
            for (const a of ANGLES) {
                for (const ox of OFFSETS) {
                    checked += 1;
                    if (line(r, h, a, ox, ox).painted === 0) {
                        empties.push({ kind: "line", r, h, a, ox });
                    }
                }
            }
        }
    }
    out.nonEmpty = { checked, empties, allPaint: empties.length === 0 };
})();

// ── §9 spatial stability: phase must redistribute, never delete ───────────
(function () {
    const rows = [];
    for (const r of RADII) {
        for (const h of [0, 0.5, 1]) {
            const totals = [];
            for (const ox of OFFSETS) {
                for (const oy of OFFSETS) totals.push(dot(r, h, ox, oy).total);
            }
            const mn = Math.min(...totals), mx = Math.max(...totals);
            const mean = totals.reduce((a, b) => a + b, 0) / totals.length;
            rows.push({ radius: r, hardness: h, minTotal: mn, maxTotal: mx,
                        swingPct: mean ? +(100 * (mx - mn) / mean).toFixed(1) : 0 });
        }
    }
    out.phaseStability = rows;
})();

// ── §9 directional parity: a diagonal must weigh like a horizontal ────────
(function () {
    const rows = [];
    const spread = (v) => {
        const mn = Math.min(...v), mx = Math.max(...v);
        const mean = v.reduce((a, b) => a + b, 0) / v.length;
        return mean ? +(100 * (mx - mn) / mean).toFixed(1) : 0;
    };
    for (const r of RADII) {
        for (const h of [0, 1]) {
            const runs = ANGLES.map(a => line(r, h, a, 0, 0));
            const byAngle = runs.map(v => v.total);
            //: Ink PER PIXEL OF TRAVEL, which is what "comparable apparent
            //: weight" means. The raw total also carries the fixture's own
            //: length, and below the floor the lattice snap changes that.
            const perLength = runs.map(v => +(v.total / v.renderedLength).toFixed(1));
            rows.push({ radius: r, hardness: h, byAngle, perLength,
                        renderedLengths: runs.map(v => +v.renderedLength.toFixed(3)),
                        spreadPct: spread(byAngle),
                        spreadPerLengthPct: spread(perLength) });
        }
    }
    out.directionalParity = rows;
})();

// ── §9 continuity and monotonicity across the old empty threshold ─────────
(function () {
    const rows = [];
    for (const h of [0, 0.5, 1]) {
        const totals = RADII.map(r => {
            // Mean over phases, so the series measures the tip and not the grid.
            let s = 0, n = 0;
            for (const ox of OFFSETS) for (const oy of OFFSETS) { s += dot(r, h, ox, oy).total; n += 1; }
            return Math.round(s / n);
        });
        let monotonic = true;
        for (let i = 1; i < totals.length; i++) if (totals[i] < totals[i - 1]) monotonic = false;
        // The jump across 0.49 -> 0.51, where the engine used to fall empty.
        const i49 = RADII.indexOf(0.49), i51 = RADII.indexOf(0.51);
        const jump = totals[i49] ? 100 * Math.abs(totals[i51] - totals[i49]) / totals[i49] : 0;
        rows.push({ hardness: h, radii: RADII, meanTotals: totals, monotonic,
                    jumpAcrossHalfPct: +jump.toFixed(1) });
    }
    out.continuity = rows;
})();

// ── the seam at the floor, measured on both sides ────────────────────────
//
// §9 forbids "a discontinuous jump at radius 0.5/0.6". The lattice snap stops
// exactly at the floor, so this is where a jump would be if there is one.
//
// The AMOUNT of paint is continuous across it. What changes is CONCENTRATION:
// at 0.5 one pixel's worth of ink is in one pixel, and at 0.51 the same ink is
// spread over up to four. That is the seam, it is the price of the snap, and
// it is recorded here rather than left for someone to find by painting a
// pressure taper. Reported as means over the sixteen phases, because a
// single-phase reading of the unsnapped side would be measuring the phase.
(function () {
    const side = (r, h) => {
        let total = 0, painted = 0, peak = 0, n = 0;
        for (const ox of OFFSETS) {
            for (const oy of OFFSETS) {
                const d = dot(r, h, ox, oy);
                total += d.total; painted += d.painted; peak += d.maxA; n += 1;
            }
        }
        return { meanTotal: +(total / n).toFixed(1),
                 meanPainted: +(painted / n).toFixed(2),
                 meanPeak: +(peak / n).toFixed(1) };
    };
    const pct = (a, b) => (a ? +(100 * (b - a) / a).toFixed(1) : 0);
    out.floorSeam = [0, 0.5, 1].map(h => {
        const below = side(0.5, h), above = side(0.51, h);
        return { hardness: h, below, above,
                 totalDeltaPct: pct(below.meanTotal, above.meanTotal),
                 paintedDeltaPct: pct(below.meanPainted, above.meanPainted),
                 peakDeltaPct: pct(below.meanPeak, above.meanPeak) };
    });
})();

// ── §9 useful visibility ──────────────────────────────────────────────────
(function () {
    const rows = [];
    for (const r of [0.5, 0.75, 1.0]) {
        for (const h of [0, 1]) {
            const maxes = [];
            for (const ox of OFFSETS) for (const oy of OFFSETS) maxes.push(dot(r, h, ox, oy).maxA);
            rows.push({ radius: r, hardness: h,
                        minMaxAlpha: Math.min(...maxes),
                        maxMaxAlpha: Math.max(...maxes) });
        }
    }
    out.visibility = rows;
})();

// ── §9 the 1 px tip must be USEFUL, against the Legacy oracle ─────────────
//
// §9 states this gate as a number rather than a shape: a 1 px-class tip must
// not "peak near alpha 18 when the equivalent Legacy result is about 222", and
// must be "an intentional crisp antialiased line rather than nothing or a
// two-pixel blur". Every other measurement in this file is V2 against V2 and
// none of them can answer it.
//
// The Legacy column is MEASURED, in `Evidence/u3r2f-tiny/f1_legacy_oracle_probe
// .js`, through the shipping input path (`beginStroke` -> `noteSample` ->
// `stab` -> `plotTo` -> `finishStroke`, then `alphaMapToImageData` and the
// commit bound) at opacity 1 and flow 1 so both engines report the same
// quantity. Legacy loses hardness entirely at this size -- its soft and hard
// 1 px tips are the same solid dot -- so it is a floor to clear, not a target
// to match.
(function () {
    const line1px = (h) => {
        const buf = new K.CoverageBuffer(60, 60);
        K.sweep(buf, { x: 20, y: 30 }, { x: 40, y: 30 }, 0.5, dep(h));
        return measure(buf);
    };
    const rows = [];
    for (const h of [0, 1]) {
        const peaks = [], paints = [];
        for (const ox of OFFSETS) {
            for (const oy of OFFSETS) {
                const d = dot(0.5, h, ox, oy);
                peaks.push(d.maxA); paints.push(d.painted);
            }
        }
        const l = line1px(h);
        rows.push({
            hardness: h,
            tapPeakMin: Math.min(...peaks), tapPeakMax: Math.max(...peaks),
            tapPaintedMax: Math.max(...paints),
            strokePainted: l.painted, strokeTotal: l.total, strokePeak: l.maxA,
        });
    }
    out.onePixelUsable = {
        rows,
        legacyOracle: {
            note: "f1_legacy_oracle_probe.js, shipping input path, opacity 1",
            tapPeak: 255, strokePainted: 21, strokeTotal: 5355, strokePeak: 255,
        },
        //: What the integration alone produced, before the lattice snap. Kept
        //: so "peak 255" is read against the 111 it replaced and against the
        //: 18 §9 names.
        integrationOnly: {
            tapPeakH0: 14, tapPeakH1: 47,
            strokePaintedH1: 44, strokeTotalH1: 4628, strokePeakH1: 111,
        },
    };
})();

// ── the lattice snap is confined to the floor ────────────────────────────
//
// The snap quantises POSITION, which is right below one pixel and wrong above
// it. This records where it stops, so a later widening cannot happen quietly.
(function () {
    const displacement = (r) => {
        //: Draw the same tip at two sub-pixel positions inside one pixel and
        //: ask whether the result moved. If it did not, the tip was snapped.
        const a = new K.CoverageBuffer(20, 20);
        const b = new K.CoverageBuffer(20, 20);
        K.stamp(a, { x: 10.1, y: 10.1 }, r, dep(1));
        K.stamp(b, { x: 10.9, y: 10.9 }, r, dep(1));
        let same = true;
        for (let i = 0; i < a.acc.length; i++) {
            const av = Math.max(a.acc[i] >> 8, a.peak[i]);
            const bv = Math.max(b.acc[i] >> 8, b.peak[i]);
            if (av !== bv) { same = false; break; }
        }
        return same;
    };
    out.snapExtent = {
        floorRadius: 0.5,
        snappedAt: [0.25, 0.4, 0.5].map(r => ({ radius: r, quantised: displacement(r) })),
        freeAt: [0.51, 0.6, 0.75, 1, 1.5, 2, 3].map(r => ({ radius: r, quantised: displacement(r) })),
    };
})();

// ── §9 hardness profiles stay distinct: no hidden solid core ──────────────
(function () {
    const sigs = {};
    for (const h of HARDNESSES) {
        const d = dot(1.5, h, 0.5, 0.5);
        sigs[h] = { painted: d.painted, total: d.total, maxA: d.maxA };
    }
    const totals = HARDNESSES.map(h => sigs[h].total);
    let monotonic = true;
    for (let i = 1; i < totals.length; i++) if (totals[i] < totals[i - 1]) monotonic = false;
    out.hardnessDistinct = {
        signatures: sigs,
        allDifferent: new Set(totals).size === totals.length,
        risesWithHardness: monotonic,
    };
})();

// ── §26 ordinary radii must be untouched ─────────────────────────────────
//
// MEASURED from the pre-repair engine (`git show HEAD:.../coverage.js` run
// side by side), at phase 0, hardness 1. Not estimated -- an earlier draft of
// this probe carried three GUESSED values here and two of them were wrong,
// which would have made this guard assert a number nothing ever produced.
(function () {
    out.ordinaryUnchanged = {
        threshold: 2,
        recordedBeforeRepair: { r2: 3060, r3: 8160, r4: 13260,
                                r6: 28560, r27: 406980 },
        now: {
            r2: dot(2, 1, 0, 0).total,
            r3: dot(3, 1, 0, 0).total,
            r4: dot(4, 1, 0, 0).total,
            r6: dot(6, 1, 0, 0).total,
            r27: dot(27, 1, 0, 0).total,
        },
    };
})();

// ── directional parity, against the pre-repair baseline ──────────────────
//
// §9 asks for comparable weight across angles. The residual at hardness 1 is
// the inherent aliasing of a hard-edged disc narrower than a pixel; removing it
// means antialiasing the rim, which changes ordinary output and is a §26 stop
// condition. Recorded so "21%" is read against the 89% it replaced.
(function () {
    out.parityBaseline = {
        note: "measured from the pre-repair engine, same fixture",
        beforeH1: { "0.25": 189.7, "0.5": 189.7, "0.6": 89.0, "0.75": 29.7,
                    "1": 4.5, "1.5": 56.3 },
        beforeH0: { "0.25": 202.0, "0.5": 202.0, "0.6": 125.5, "0.75": 39.1,
                    "1": 1.1, "1.5": 1.1 },
    };
})();

// ── the footprint must be the TIP's, not an inflated one ─────────────────
//
// §11 names "inflating the radius rather than integrating coverage" as a
// regression the guards must catch, and none of the gates above would: growing
// the tip fills the empty phases, steadies the swing and keeps every angle
// weighted, while making a 1 px brush paint a 3 px blob.
//
// The invariant that separates the two: paint may only land on a pixel whose
// SQUARE intersects the disc. A pixel whose CENTRE is beyond the radius is
// legitimate -- that is exactly what integrating a partly-covered pixel
// produces, and demanding otherwise would forbid the repair. A pixel that does
// not touch the disc at all is inflation, with nothing else it can be.
//
// Measured against the RENDERED tip, not the requested one: `stamp` and
// `sweep` floor the radius at 0.5 and, at that floor, place the tip on the
// pixel lattice. Measuring against the requested radius and the raw centre
// would report the engine's own floor and snap as inflation.
(function () {
    const N = 40;
    let worst = -Infinity, worstCase = null;
    for (const r of RADII) {
        for (const h of [0, 0.5, 1]) {
            for (const ox of OFFSETS) {
                for (const oy of OFFSETS) {
                    const buf = new K.CoverageBuffer(N, N);
                    let cx = 20 + ox, cy = 20 + oy;
                    K.stamp(buf, { x: cx, y: cy }, r, dep(h));
                    if (r <= 0.5) {
                        cx = Math.floor(cx) + 0.5;
                        cy = Math.floor(cy) + 0.5;
                    }
                    const eff = Math.max(0.5, r);
                    for (let y = 0; y < N; y++) {
                        for (let x = 0; x < N; x++) {
                            const i = y * N + x;
                            const b = buf.acc[i] >> 8;
                            const v = b > buf.peak[i] ? b : buf.peak[i];
                            if (v <= 0) continue;
                            //: nearest point of the pixel square [x,x+1]x[y,y+1]
                            const nx = cx < x ? x : (cx > x + 1 ? x + 1 : cx);
                            const ny = cy < y ? y : (cy > y + 1 ? y + 1 : cy);
                            const d = Math.sqrt((cx - nx) * (cx - nx)
                                                + (cy - ny) * (cy - ny));
                            if (d - eff > worst) {
                                worst = d - eff;
                                worstCase = { radius: r, hardness: h, ox, oy,
                                              px: x, py: y,
                                              nearestDist: +d.toFixed(4) };
                            }
                        }
                    }
                }
            }
        }
    }
    out.footprint = { maxOverextentPx: +worst.toFixed(4), worstCase };
})();

// ── §10 bounded: the dirty rect stays the tip's ──────────────────────────
(function () {
    const buf = new K.CoverageBuffer(400, 400);
    K.stamp(buf, { x: 200.5, y: 200.5 }, 0.5, dep(0));
    const d = buf.takeDirty();
    out.bounded = {
        dirty: { x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1 },
        area: d.width() * d.height(),
        documentPixels: 160000,
    };
})();

// ── §10 cost: profile evaluations per affected pixel ─────────────────────
(function () {
    const rows = [];
    for (const r of [0.5, 1, 1.99, 2, 3, 27]) {
        const t0 = process.hrtime.bigint();
        const N = r > 10 ? 200 : 4000;
        for (let i = 0; i < N; i++) {
            const buf = new K.CoverageBuffer(80, 80);
            K.stamp(buf, { x: 40.3, y: 40.7 }, r, dep(0.85));
        }
        const t1 = process.hrtime.bigint();
        const d = dot(r, 0.85, 0.3, 0.7);
        rows.push({ radius: r, paintedPixels: d.painted,
                    usPerStamp: +(Number(t1 - t0) / 1000 / N).toFixed(2),
                    supersampled: r < 2 });
    }
    out.cost = rows;
})();

process.stdout.write(JSON.stringify(out, null, 1));
