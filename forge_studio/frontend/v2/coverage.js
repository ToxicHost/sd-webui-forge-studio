/**
 * Forge Studio — Brush Engine V2 coverage kernel (V2-05 / spec BE5)
 * by ToxicHost & Moritz
 *
 * `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §§7.4, 10, 11, 12.5, 13.
 * Turns placed marks into INTRINSIC coverage, and merges that coverage into a
 * target exactly once.
 *
 * THE BOUNDARY IS THE POINT. §7.4: "The sink applies target selection/mask
 * exactly once at merge. Brush coverage is unselected intrinsic coverage."
 *
 *   stamp / sweep   own coverage in 0..1, and know nothing about opacity,
 *                   selection or target pixels
 *   merge           owns Opacity once, selection once, and the target
 *                   operation, and generates no coverage
 *
 * SELECTION APPLIED PER DAB IS THE SHARPEST FAILURE THIS PREVENTS. Applied per
 * contribution it MULTIPLIES: a 50% selection over N overlapping dabs yields
 * 0.5^N, so a worked area inside a soft selection goes black at the centre and
 * vanishes at the edge.
 *
 * THE DEPOSITION MODEL IS BE17'S, AND §10 MANDATES IT: "Flow: deposition per
 * dab/pass; Opacity: stroke/layer merge ceiling. Ordinary non-Airbrush darkness
 * MUST NOT increase merely because the device reports more events."
 *
 *     K      = max(1, density * (2 / step) * profileMean(hardness))
 *     target = buildup ? Flow * Opacity : Flow
 *     fEff   = 1 - (1 - target)^(1/K)
 *     acc    = acc + (1 - acc) * shape(nd) * fEff        Uint16
 *     peak   = max(peak, shape(nd) * target)             Uint8
 *     cov    = max(acc, peak)
 *
 * Three subtleties, each with the measurement behind it:
 *
 *   * THE PEAK FLOOR is why a hard tip still has a hard edge. Pure accumulation
 *     gives the cross-section the wrong SHAPE, because the number of dabs
 *     sweeping a pixel falls off faster at the rim than the tip's profile does.
 *     Measured at hardness 1.0, Flow 50, across the band: 127 127 127 127
 *     against 62 91 109 116 -- "a soft shoulder on a brush whose whole point is
 *     that it has none, and Pencil's entire mark 19% lighter";
 *   * AT FLOW 100 THERE IS NOTHING TO ACCUMULATE, and accumulating anyway makes
 *     the edge WORSE: the only pixels left to build on are the tip's own
 *     antialiased rim. Measured, Basic Round lost 1,016 of 5,200 painted
 *     pixels' worth of antialiasing, worst case 86/255;
 *   * THE 16-BIT ACCUMULATOR IS NOT OPTIONAL. Normalisation makes the per-dab
 *     contribution small by design; at 55.6 dabs per pixel and Flow 35 one dab
 *     is 0.0077, which truncates to 1 of 255 in eight bits and to ZERO at Flow
 *     15 -- the brush paints nothing at all.
 *
 * DETERMINISM: no `Math.random` anywhere. Randomness arrives as a seeded stream
 * the caller supplies, so a recorded stroke replays byte-identically (§13, §20
 * Correctness 10).
 *
 * U1 — BOUNDED. §16.3: "V2 MUST emit dirty tiles/bounds from the first coverage
 * primitive through commit." Every write records the region it touched, and
 * every read, merge and metric visits ONLY that region. Nothing here allocates
 * or scans in proportion to the document to answer a question about a small
 * stroke.
 *
 * WHY THAT IS A CORRECTNESS MATTER AND NOT ONLY A SPEED ONE. The engine this
 * replaces is already bounded: BE8 gave `alphaMapToImageData` a rect and
 * `commitStroke` walks `S.stroke.dirty`. An unbounded V2 would have been a
 * measurable REGRESSION dressed as an upgrade, and the integration gate found
 * it before the adapter was written rather than after.
 *
 * Review: `Evidence/source-review/V2-05-coverage-semantics.md`,
 * `Evidence/source-review/U1-bounded-coverage.md`.
 */

(function () {
"use strict";

//: Above this, one contribution already deposits everything the pass may.
//: See the module docstring.
//: U3-R2F C2. WAS 0.995, AND THE THRESHOLD IS WHY THE CREASE EXISTED.
//: Below it the accumulator ran; at Flow 1 it did not, leaving `peak` --
//: a max over the swept polyline -- as the whole deposition, and
//: `max(f(d1), f(d2)) = f(min(d1,d2))` kinks on the medial axis between
//: two arms. Above 1 the branch is unreachable, so deposition is ONE
//: model at every flow instead of two either side of a threshold.
//:
//: Kept as a named constant rather than deleted, so the flow-continuity
//: tests around 0.994/0.995/0.999/1.0 have something to name and a
//: mutation that reinstates the branch has something to move.
const NO_ACCUMULATE_ABOVE = 1.0001;

//: The two renderers §11.1 assigns.
const RENDER_STAMP = "stamp";
const RENDER_SWEEP = "sweep";

//: This module's own API generation, independent of `brush-contracts.js`'s
//: SCHEMA_VERSION -- that one stamps serialised contract OBJECTS (sample,
//: preset, target) and coverage is not one of them. 1 was the unbounded API;
//: 2 is the bounded one. Declared so a caller can assert what it is holding
//: rather than discover the difference by misreading an array.
const COVERAGE_API_VERSION = 2;

/**
 * The tip's radial profile at normalised distance `nd` (0 centre, 1 rim).
 *
 * 1 out to `hardness`, then a smoothstep down over the rest. A hardness of 1
 * is a flat disc; 0 is a full smoothstep.
 */
function shapeAt(nd, hardness) {
    if (nd >= 1) return 0;
    const h = hardness;
    if (nd <= h) return 1;
    const t = (nd - h) / (1 - h || 1e-6);
    const s = 1 - t;
    return s * s * (3 - 2 * s);
}

/*
 * U3-G. THE GAUSSIAN FALLOFF.
 *
 * Airbrush, Ink Wash and Charcoal declare it (`canvas-core.js:741, 756, 814`)
 * and V2 rendered all three with the smoothstep above.
 *
 * PORTED VERBATIM from `canvas-core.js:2178`, which is Krita's
 * `KisGaussCircleMaskGenerator`. The constants are not tuning and are not
 * rounded: `6761` and `12500` are Krita's, and rewriting them as a tidier
 * bell would be a different brush wearing the same name.
 */

/** Abramowitz & Stegun 7.1.26. Legacy's, digit for digit. */
function erf(x) {
    const sign = x >= 0 ? 1 : -1;
    const ax = Math.abs(x);
    const t = 1.0 / (1.0 + 0.3275911 * ax);
    const y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                      - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
    return sign * y;
}

/**
 * The gaussian profile, in NORMALISED distance like `shapeAt`.
 *
 * Legacy's `distfactor` carries `1 / radius`, so its `dabAlphaGauss` is already
 * a function of `dist / radius` alone -- which is why this needs no radius
 * argument and composes with the supersampler and the tip frames unchanged.
 */
function shapeAtGauss(nd, hardness) {
    if (nd >= 1) return 0;
    const fade = Math.max(0.01, 1.0 - hardness * 0.85);
    const center = (2.5 * (6761.0 * fade - 10000.0)) / (1.41421356 * 6761.0 * fade);
    const alphafactor = 1.0 / (2.0 * erf(center));
    const d = nd * 1.41421356 * 12500.0 / (6761.0 * fade);
    return alphafactor * (erf(d + center) - erf(d - center));
}

//: Below this radius the tip is POINT-SAMPLED NO LONGER. See `coverageAt`.
//:
//: 2 is chosen so radius >= 2 stays byte-identical: §26 makes "changes ordinary
//: radius >= 2 output materially" a stop condition, and a higher threshold
//: would buy a smaller seam by changing brushes that were never broken.
const SUPERSAMPLE_BELOW_RADIUS = 2;

//: 4x4, fixed, symmetric, no jitter. §7.B is explicit about the last part, and
//: a seeded jitter would make the same stroke differ between runs. 16 samples
//: over at most ~13 pixels is a few hundred evaluations for a whole tiny tip.
const SUPERSAMPLE_N = 4;

/**
 * The smallest tip the raster can hold, and the point below which POSITION
 * stops being meaningful as well as size.
 *
 * `stamp` and `sweep` have always floored the radius here. What was missing is
 * the consequence: a disc of radius 0.5 has an area of 0.785 px, so no
 * placement of it covers any pixel by more than 78.5%, and a tip landing on a
 * pixel CORNER splits that four ways. Integrating the profile over the pixel
 * square -- which is what `coverageAt` does, and which is right -- therefore
 * produced a mathematically correct and visually useless result:
 *
 *                       Legacy        V2 point-sampled   V2 integrated only
 *     1 px soft tap     1/255/255     0/0/0              4/56/14
 *     1 px hard tap     1/255/255     0/0/0              4/188/47
 *     1 px hard stroke  21/5355/255   1/255/255          44/4628/111
 *                       (painted px / total alpha / PEAK alpha)
 *
 * §9 names that outcome and forbids it: a 1 px-class tip must not "peak near
 * alpha 18 when the equivalent Legacy result is about 222", and must be "an
 * intentional crisp antialiased line rather than nothing or a two-pixel blur".
 * 44 pixels of grey where Legacy paints 21 of black IS the two-pixel blur.
 *
 * SO THE FLOOR SNAPS AS WELL AS CLAMPS. Below one pixel there is no sub-pixel
 * shape left to place, so the tip is placed on the pixel lattice and the
 * integration then lands it in ONE row instead of straddling two. Legacy
 * arrives at the same place by accident -- it point-samples with the pixel
 * INDEX as the centre -- and pays for it by losing hardness entirely down
 * there, which is why its soft and hard 1 px tips are the same solid dot.
 * Snapping keeps the profile, so V2's soft 1 px tip is half the ink of its
 * hard one, which is what the owner asked the hardness slider for.
 *
 * NO FADE, AND THAT WAS MEASURED RATHER THAN ASSUMED. Blending the snap out
 * between radius 0.5 and 0.75 or 1.0 looks tidier and is worse: a partly
 * snapped tip is neither aligned nor uniformly distributed, and phase swing in
 * total paint at radius 0.6 went from 1.2% to 27.6%. The floor is already a
 * plateau -- every brush of 1 px or less renders at exactly this radius -- so
 * there is nothing to fade across.
 *
 * Above the floor nothing is snapped, and radius >= 2 is untouched entirely.
 */
const SUBPIXEL_FLOOR_RADIUS = 0.5;

/** The centre of the pixel containing `v`. Pixel i spans [i, i+1). */
function snapToPixelCentre(v) {
    return Math.floor(v) + 0.5;
}

/**
 * One pixel's coverage, integrated rather than sampled when the tip is tiny.
 *
 * WHY POINT SAMPLING FAILS, and it is not only the empty case §3.1 names.
 * `stamp` and `sweep` asked the profile for its value at ONE point -- the pixel
 * centre. For a tip whose radius is around a pixel, the answer depends violently
 * on where the tip happens to land between pixel centres. Measured, total paint
 * across sixteen subpixel phases:
 *
 *     radius 0.25    1600% swing, empty at some phases
 *     radius 0.50     178% swing (hardness 1), 420% (hardness 0), empty at 0.00
 *     radius 1.00     107% swing (hardness 1)
 *     radius 2.00      41% swing (hardness 1)
 *
 * The empty case is the worst phase of that instability, not a separate bug: an
 * axis-aligned stroke on integer coordinates puts every pixel centre exactly
 * 0.5 away, and with the radius floored at 0.5 that is `nd == 1`, which
 * `nd >= 1` rejects. Every candidate pixel, every time.
 *
 * LEGACY IS NOT MORE CORRECT HERE, IT IS MORE LUCKILY ALIGNED. It point-samples
 * too, and floors the radius at 0.5 too, and rejects `nd >= 1` too. It differs
 * only in where a pixel's sample point is: `dy = py - ccy`, the pixel INDEX as
 * its centre, stated outright in its own comment. So the commonest case -- an
 * axis-aligned stroke on integer coordinates -- lands exactly ON Legacy's
 * sample points and exactly BETWEEN V2's. Neither engine touches Canvas2D for
 * the alpha map, so this is arithmetic, not a browser rasteriser artefact.
 *
 * V2's half-pixel convention is the standard one and is consistent with its own
 * dirty rectangles and transfer, so the repair is NOT to flip it -- that would
 * move every stroke in the engine by half a pixel to fix a subpixel case. The
 * repair is to stop asking a single point what a whole pixel is worth.
 *
 * A CENTROID-WEIGHTED AREA MODEL WAS TRIED AND REJECTED. Disc/square overlap
 * times the profile at the overlap centroid is the cheaper classic, and it is
 * fine at hardness 1 -- but it swings 81% to 118% at hardness 0, because a
 * single centroid cannot stand in for a profile that varies steeply across the
 * pixel. Supersampling holds at 5.2% worst case across every radius and both
 * hardnesses.
 *
 * `distance` is injected so the stamp (distance to a point) and the sweep
 * (distance to a segment) share one integrator without either knowing about the
 * other's geometry.
 */
/*
 * U3-G. A PROFILE IS ITS SHAPE, ITS SUPPORT AND ITS MEAN.
 *
 * Three things travel together, and the second is the one that makes this an
 * abstraction rather than a function pointer.
 *
 *   at     the radial profile at normalised distance
 *   floor  THE LOWER BOUND OF ITS SUPPORT. `buildPassTable` confines its grids
 *          to `[floor, 1]`, on the stated grounds that "inside the plateau the
 *          tip is opaque ... and `rho` is never sampled there anyway". True of
 *          the smoothstep, which is exactly 1 for `nd <= hardness`. FALSE of a
 *          gaussian, which is 1 only at the centre -- measured, `gauss(0.5)` at
 *          hardness 0.5 is 0.7629 and `gauss(0.85)` at hardness 0.85 is 0.3081,
 *          where the smoothstep gives 1.0000 for both. A gaussian table built
 *          on the shoulder would read a domain the profile does not live on.
 *   mean   the along-travel weight for a STAMP train. Legacy carries a
 *          gaussian-aware one too (`canvas-core.js:3318`) and it is easy to
 *          miss, because the smoothstep's is a closed form and the gaussian's
 *          is a numeric integral.
 *
 * THE SAFETY PROPERTY IS STRUCTURAL. `SMOOTHSTEP.floor` returns `hardness`, so
 * every expression in `buildPassTable` reduces to the arithmetic that was
 * already there and the thirteen non-gaussian presets are byte-identical BY
 * CONSTRUCTION. Nothing about this change is reachable unless a tip asks for
 * gaussian -- which is what makes it safe to touch C2's core at all.
 */

const _GAUSS_MEAN = new Map();

/** Legacy's, including its 512 samples and its 0.05 floor. */
function gaussMean(hardness) {
    const h = Math.min(1, Math.max(0, hardness));
    const key = Math.round(h * 1000);
    let mean = _GAUSS_MEAN.get(key);
    if (mean === undefined) {
        const N = 512;
        let sum = 0;
        for (let i = 0; i < N; i++) sum += shapeAtGauss((i + 0.5) / N, h);
        mean = Math.max(0.05, sum / N);
        _GAUSS_MEAN.set(key, mean);
    }
    return mean;
}

const SMOOTHSTEP = Object.freeze({
    name: "default",
    at: shapeAt,
    floor: function (hardness) { return hardness; },
    mean: function (hardness) {
        const h = Math.min(1, Math.max(0, hardness));
        return (1 + h) / 2;
    },
});

const GAUSSIAN = Object.freeze({
    name: "gaussian",
    at: shapeAtGauss,
    //: No plateau, so the support is the whole disc.
    floor: function () { return 0; },
    mean: gaussMean,
});

/** Unknown names fall back to the smoothstep rather than throwing at paint time. */
function profileFor(falloff) {
    return falloff === "gaussian" ? GAUSSIAN : SMOOTHSTEP;
}

function coverageAt(px, py, radius, hardness, distance, profile) {
    const shape = (profile || SMOOTHSTEP).at;
    if (radius >= SUPERSAMPLE_BELOW_RADIUS) {
        return shape(distance(px + 0.5, py + 0.5) / radius, hardness);
    }
    const n = SUPERSAMPLE_N, step = 1 / n, half = step / 2;
    const invR = 1 / radius;
    let sum = 0;
    for (let sy = 0; sy < n; sy++) {
        const y = py + half + sy * step;
        for (let sx = 0; sx < n; sx++) {
            sum += shape(distance(px + half + sx * step, y) * invR, hardness);
        }
    }
    return sum / (n * n);
}

/**
 * The mean of the tip's radial profile: the integral of shape(u) du over 0..1.
 *
 * Dabs overlap by GEOMETRY but deposit by PROFILE: a pixel under the middle of
 * the swept band is touched by `2 * radius / gap` dabs, and each touches it at
 * a different point on the falloff. The effective count of full-strength dabs
 * is the geometric count weighted by this mean, and that is what Flow must be
 * divided by.
 *
 * Closed form for the default falloff: a smoothstep integrates to half its
 * interval, so the mean is `h + (1 - h) / 2`. That gives 1.0 at hardness 1 -- a
 * flat disc -- and 0.5 at hardness 0, which is the sanity check for the whole
 * derivation.
 */
function profileMean(hardness, profile) {
    return (profile || SMOOTHSTEP).mean(hardness);
}

/**
 * §7.4's `DirtyRegion`, given a shape. The spec NAMES it -- `preview():
 * DirtyRegion` -- and never defines it, so this is the definition, and it is
 * deliberately the only one in the codebase.
 *
 * HALF-OPEN, `x0`/`y0` INCLUSIVE, `x1`/`y1` EXCLUSIVE. That is the same
 * convention as every JavaScript slice, it makes width `x1 - x0` with no
 * off-by-one, and it lets EMPTY be a real state rather than a sentinel:
 * `x1 <= x0` is empty, and the empty region is `(0,0,0,0)`.
 *
 * WHY EMPTY MATTERS ENOUGH TO SAY TWICE. A stroke entirely outside the document
 * must report "nothing", and the tempting shortcut -- clamping to a 1x1 rect at
 * the origin -- reports a painted pixel that does not exist. Consumers of a
 * dirty region upload it, composite it or journal it; a fake 1x1 is a lie that
 * costs a texture upload and hides a bug. `isEmpty()` is derived from the
 * numbers, so it cannot disagree with them.
 *
 * Bounds only, no tiles. §16.3 asks for "tiles/bounds" and `StrokeCommit` for
 * "changed bounds and/or tiles", so bounds alone satisfies the contract. Tile
 * keys can be derived from a region later without changing this shape, which is
 * why no second model is invented here.
 */
function DirtyRegion(x0, y0, x1, y1) {
    this.x0 = x0 | 0;
    this.y0 = y0 | 0;
    this.x1 = x1 | 0;
    this.y1 = y1 | 0;
}

DirtyRegion.empty = function () { return new DirtyRegion(0, 0, 0, 0); };

DirtyRegion.prototype.isEmpty = function () {
    return this.x1 <= this.x0 || this.y1 <= this.y0;
};
DirtyRegion.prototype.width = function () {
    return this.isEmpty() ? 0 : this.x1 - this.x0;
};
DirtyRegion.prototype.height = function () {
    return this.isEmpty() ? 0 : this.y1 - this.y0;
};
DirtyRegion.prototype.area = function () {
    return this.width() * this.height();
};
DirtyRegion.prototype.clone = function () {
    return new DirtyRegion(this.x0, this.y0, this.x1, this.y1);
};
DirtyRegion.prototype.reset = function () {
    this.x0 = 0; this.y0 = 0; this.x1 = 0; this.y1 = 0;
    return this;
};

/** Grow to include one pixel. Half-open in, half-open out. */
DirtyRegion.prototype.expandPixel = function (x, y) {
    return this.expand(x, y, x + 1, y + 1);
};

/** Grow to include a half-open box. An empty box is a no-op, not a collapse. */
DirtyRegion.prototype.expand = function (x0, y0, x1, y1) {
    if (x1 <= x0 || y1 <= y0) return this;
    if (this.isEmpty()) {
        this.x0 = x0 | 0; this.y0 = y0 | 0;
        this.x1 = x1 | 0; this.y1 = y1 | 0;
        return this;
    }
    if (x0 < this.x0) this.x0 = x0 | 0;
    if (y0 < this.y0) this.y0 = y0 | 0;
    if (x1 > this.x1) this.x1 = x1 | 0;
    if (y1 > this.y1) this.y1 = y1 | 0;
    return this;
};

DirtyRegion.prototype.unionWith = function (other) {
    if (!other) return this;
    return this.expand(other.x0, other.y0, other.x1, other.y1);
};

/** Clip into a document. A region clipped away entirely becomes empty. */
DirtyRegion.prototype.clipTo = function (width, height) {
    const w = width | 0, h = height | 0;
    if (this.x0 < 0) this.x0 = 0;
    if (this.y0 < 0) this.y0 = 0;
    if (this.x1 > w) this.x1 = w;
    if (this.y1 > h) this.y1 = h;
    if (this.isEmpty()) this.reset();
    return this;
};

DirtyRegion.prototype.toJSON = function () {
    return {
        x0: this.x0, y0: this.y0, x1: this.x1, y1: this.y1,
        width: this.width(), height: this.height(),
        area: this.area(), empty: this.isEmpty(),
    };
};

/**
 * A coverage surface. Uint16 accumulator, Uint8 result.
 *
 * The accumulator's extra precision is needed to ADD small contributions, not
 * to store the answer.
 *
 * `dirty` is the union of the pixels whose coverage actually ended NON-ZERO --
 * not the tips' bounding boxes. A dab whose Flow resolves to nothing writes no
 * coverage and must not dirty anything, or "the stroke changed pixels" stops
 * meaning what it says.
 */
function CoverageBuffer(width, height) {
    this.width = width | 0;
    this.height = height | 0;
    const n = this.width * this.height;
    this.acc = new Uint16Array(n);
    this.peak = new Uint8Array(n);
    this.contributions = 0;
    this.dirty = DirtyRegion.empty();
    //: Instrumentation, so a test can prove an algorithm is bounded instead of
    //: timing it and hoping. Wall clock cannot distinguish "bounded" from
    //: "unbounded but the allocator was warm".
    this.extractions = 0;
    this.growths = 0;
    this.visitedPixels = 0;
    this._scratch = null;
}

CoverageBuffer.prototype.at = function (x, y) {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return 0;
    const i = y * this.width + x;
    const built = this.acc[i] >> 8;
    return built > this.peak[i] ? built : this.peak[i];
};

/**
 * The region a caller means: the one it passed, clipped, or the dirty one.
 *
 * Always a COPY, so a caller cannot mutate the buffer's own bounds by keeping
 * the returned object.
 */
CoverageBuffer.prototype.regionFor = function (region) {
    const r = region
        ? new DirtyRegion(region.x0, region.y0, region.x1, region.y1)
        : this.dirty.clone();
    return r.clipTo(this.width, this.height);
};

/**
 * Coverage over one region, 0..255, row-major within the region.
 *
 * THE RETURNED `data` IS OWNED BY THE BUFFER AND IS REUSED. The next
 * `readRegion` on the same buffer overwrites it. Copy it if it must outlive
 * that call. This is the "one explicitly owned compact extraction" the bounded
 * contract asks for: a fresh array per call is what made the old `read()`
 * allocate a document-sized buffer four times over for one merge.
 *
 * Indexing is REGION-LOCAL: `data[(y - r.y0) * r.width + (x - r.x0)]`. It is
 * not document-indexed, which is why the old document-indexed `read()` was
 * removed outright rather than quietly redefined -- a caller that missed the
 * change gets a TypeError instead of silently reading the wrong pixels.
 */
CoverageBuffer.prototype.readRegion = function (region) {
    const r = this.regionFor(region);
    const rw = r.width(), rh = r.height();
    const need = rw * rh;
    if (need === 0) {
        this.extractions += 1;
        return { x0: r.x0, y0: r.y0, width: 0, height: 0, region: r,
                 data: new Uint8Array(0) };
    }
    if (!this._scratch || this._scratch.length < need) {
        this._scratch = new Uint8Array(need);
        this.growths += 1;
    }
    const scratch = this._scratch;
    const w = this.width;
    let k = 0;
    for (let y = r.y0; y < r.y1; y++) {
        const row = y * w;
        for (let x = r.x0; x < r.x1; x++) {
            const i = row + x;
            const built = this.acc[i] >> 8;
            scratch[k++] = built > this.peak[i] ? built : this.peak[i];
        }
    }
    this.extractions += 1;
    this.visitedPixels += need;
    // A subarray, not the whole scratch: the length is exactly the region's, so
    // a caller cannot walk off the end into a previous, larger extraction.
    return { x0: r.x0, y0: r.y0, width: rw, height: rh, region: r,
             data: scratch.subarray(0, need) };
};

/**
 * Zero the coverage and forget the bounds.
 *
 * BOUNDED, like everything else here: only the dirty region is zeroed, because
 * only the dirty region can be non-zero. A stroke on a 6000x4000 document
 * clears the few thousand pixels it touched, not 24 million.
 */
CoverageBuffer.prototype.clear = function () {
    const r = this.dirty;
    if (!r.isEmpty()) {
        const w = this.width;
        for (let y = r.y0; y < r.y1; y++) {
            const row = y * w;
            this.acc.fill(0, row + r.x0, row + r.x1);
            this.peak.fill(0, row + r.x0, row + r.x1);
        }
        this.visitedPixels += r.area();
    }
    this.dirty = DirtyRegion.empty();
    this.contributions = 0;
    return this;
};

/**
 * The region that has changed since the last call, and reset it.
 *
 * U3 needs the FRAME's rectangle, not the stroke's: an adapter transfers what
 * changed since the previous frame into the Canvas's own buffer, and taking the
 * accumulated region every time would re-transfer the whole stroke on every
 * move -- the exact cost U2 removed from the Legacy path.
 *
 * COVERAGE IS UNTOUCHED. Only the bookkeeping is reset, which is what separates
 * this from `clear()`. It is the same shape as `S.stroke.frameDirty` on the
 * Canvas side, and for the same reason.
 */
CoverageBuffer.prototype.takeDirty = function () {
    const taken = this.dirty.clone();
    this.dirty = DirtyRegion.empty();
    return taken;
};

/** What this buffer has allocated and visited. For evidence, not for logic. */
CoverageBuffer.prototype.stats = function () {
    return {
        width: this.width, height: this.height,
        bufferBytes: this.acc.byteLength + this.peak.byteLength,
        scratchBytes: this._scratch ? this._scratch.byteLength : 0,
        extractions: this.extractions,
        growths: this.growths,
        visitedPixels: this.visitedPixels,
        contributions: this.contributions,
        dirty: this.dirty.toJSON(),
    };
};

/*
 * ONE MINUS (1 - tc) TO THE SHARE, without a `Math.pow` per pixel.
 *
 * U3-R2F F2's arc-length deposition needs that quantity for every painted pixel
 * of every swept segment whenever the accumulator runs, and `Math.pow` there
 * measured 21% of the branch's cost -- enough to put a 272 px brush on a 4096
 * document at 30.3 ms per frame against §17's 33 ms gate, from 11.2 ms before.
 *
 * Rewritten as `1 - exp(-share * g)` with `g = -ln(1 - tc)`, which splits one
 * two-argument transcendental into two one-argument ones, each tabulated over a
 * bounded domain and read with linear interpolation.
 *
 * THE TAILS ARE HANDLED RATHER THAN TABULATED, because that is where a table
 * silently lies. `g` has curvature `1/(1-tc)^2`, so above tc 0.999 linear
 * interpolation would err by a quarter of an alpha level and climbing; that
 * range falls back to the exact `Math.pow` and is rare. Beyond `z = 12`,
 * `exp(-z)` is under 1e-5 and the answer is 1 to well past 8-bit precision.
 *
 * The tables are module-level and built once: 4096 + 2049 floats, 24 KB, not
 * per stroke and not per segment.
 */
const NEGLOG_N = 4096;
const NEGLOG_MAX_TC = 0.999;
const NEGLOG = new Float64Array(NEGLOG_N + 1);
for (let i = 0; i <= NEGLOG_N; i++) {
    NEGLOG[i] = -Math.log(1 - (i / NEGLOG_N) * NEGLOG_MAX_TC);
}
const EXPN_N = 2048;
const EXPN_MAX = 12;
const EXPN = new Float64Array(EXPN_N + 1);
for (let i = 0; i <= EXPN_N; i++) {
    EXPN[i] = Math.exp(-(i / EXPN_N) * EXPN_MAX);
}



/*
 * OPTICAL-DEPTH TABLES. U3-R2F C2.
 *
 * The ACCUMULATOR THIS REPLACED distributed a pass among segments as
 * `span / (2e)` and
 * multiplies that share by the coverage at the segment's CLAMPED distance. Those
 * two describe different geometry -- the window comes from the infinite line,
 * the coverage from the clamped segment -- so only the one segment containing a
 * pixel's projection reads the coverage at closest approach and the rest read
 * lower. Measured on a straight stroke, where the shares sum to exactly 1.0000,
 * the `peak` floor still won on 96.76% of pixels by a mean of 29.5 alpha: the
 * accumulator has never governed a single pass.
 *
 * The repair is to stop partitioning a pass and integrate along it instead.
 * `PASS_DENSITY` is the radial density whose LINE INTEGRAL is the tip's own
 * profile, so the integral over a whole straight pass reproduces `shapeAt`
 * exactly and the integral over part of one is that part's honest share.
 *
 * It is the inverse Abel transform of `G(p) = -ln(1 - target*shapeAt(p))`:
 *
 *     rho(r) = -(1/pi) INTEGRAL_r^1 G'(p) / sqrt(p^2 - r^2) dp
 *
 * evaluated with the substitution `p = sqrt(r^2 + v^2)`, which cancels the
 * endpoint singularity analytically -- the direct form has a 1/sqrt(0) at the
 * lower limit and a naive quadrature returns 1e7 there.
 */
const PASS_TAU_MAX = 12;
//: Resolutions chosen by a measured sweep, not by taste. Build time against the
//: two gates that constrain them, worst hardness of {0, 0.25, 0.5, 0.85}:
//:
//:   N   M   K   RHO   build    profile delta   cap overshoot
//:   512 96  256 1024   90.6ms   1 byte          1.000
//:   512 64  128  512   18.4ms   1 byte          1.000
//:   256 48   96  384   12.1ms   1 byte          1.000   <- chosen
//:   256 32   64  256    6.5ms   1 byte          1.008, 1.016  REJECTED
//:
//: One step cheaper and the cap starts to overshoot again, which is the defect
//: this candidate exists to remove. These cost build time only; At 512/192/128 the
//: hardness-0.85 cross-section differed from the shipping profile by 4 of 255 --
//: outside the one-byte tolerance -- because a hardness-0.85 tip is a fully
//: opaque plateau over most of its radius and `G` is correspondingly steep at
//: its edge. These cost build time only; the per-pixel lookup count is
//: unchanged.
const PASS_N = 256;
const PASS_M = 48;
const PASS_K = 96;
//: `rho` gets its OWN, much finer grid than the 2-D table it feeds. A hardness
//: plateau makes `G` a near-step at `nd == hardness`, so `rho` inherits an
//: integrable 1/sqrt singularity there; sampling it at the 2-D grid's rate left
//: the soft rim 4 of 255 heavy at hardness 0.85. It is a 1-D array, so the
//: extra resolution is nearly free.
const PASS_RHO_K = 384;

/** `-ln(1 - a)`, bounded, so a fully opaque core is a large number not an Infinity. */
function passG(a) {
    const cap = 1 - Math.exp(-PASS_TAU_MAX);
    const v = a > cap ? cap : (a < 0 ? 0 : a);
    return -Math.log(1 - v);
}

/**
 * The 2-D antiderivative `P(p, t) = INTEGRAL_0^t rho(sqrt(p^2+tau^2)) dtau`,
 * in units of the tip radius, on a PASS_K x PASS_K grid.
 *
 * Built once per (hardness, target) and cached. The build is ~230k flops and
 * runs at `depositionFor` time, not per mark and not per pixel.
 */
function buildPassTable(hardness, target, profile) {
    const prof = profile || SMOOTHSTEP;
    const shape = prof.at;
    //: U3-G. THE DOMAIN IS THE PROFILE'S SUPPORT, not the hardness. For
    //: the smoothstep this IS `hardness`, so every expression below is
    //: the arithmetic that was already here and non-gaussian tips are
    //: byte-identical by construction rather than by tolerance.
    const floor = prof.floor(hardness);
    //: EVERYTHING LIVES ON THE SHOULDER [hardness, 1]. Inside the plateau the
    //: tip is opaque, every row of the table would be identical, and `rho` is
    //: never sampled there anyway -- `P(p,t)` reads `rho(sqrt(p^2+t^2))` with
    //: `p >= hardness`, so the radius argument never falls below it. Confining
    //: all three grids to the shoulder sharpened the hardness-0.85 rim from
    //: 3 of 255 to 1 and cut the build by most of its cost at the same time.
    const span = 1 - floor;
    const g = new Float64Array(PASS_N + 1);
    for (let i = 0; i <= PASS_N; i++) {
        g[i] = passG(target * shape(floor + (i / PASS_N) * span, hardness));
    }
    //: Central differences, one-sided at the ends.
    const gp = new Float64Array(PASS_N + 1);
    const dx = (span > 1e-9 ? span : 1e-9) / PASS_N;
    for (let i = 0; i <= PASS_N; i++) {
        if (i === 0) gp[i] = (g[1] - g[0]) / dx;
        else if (i === PASS_N) gp[i] = (g[PASS_N] - g[PASS_N - 1]) / dx;
        else gp[i] = (g[i + 1] - g[i - 1]) / (2 * dx);
    }
    const invSpan = span > 1e-9 ? 1 / span : 0;
    const sampleGp = function (p) {
        const rel = (p - floor) * invSpan;
        if (rel <= 0) return gp[0];
        if (rel >= 1) return gp[PASS_N];
        const f = rel * PASS_N, i = f | 0;
        return gp[i] + (gp[i + 1] - gp[i]) * (f - i);
    };
    const rho = new Float64Array(PASS_RHO_K + 1);
    for (let k = 0; k <= PASS_RHO_K; k++) {
        const r = floor + (k / PASS_RHO_K) * span;
        const vmax = Math.sqrt(Math.max(1 - r * r, 0));
        if (vmax <= 0) { rho[k] = 0; continue; }
        let sum = 0;
        const h = vmax / PASS_M;
        const rr2 = r * r;
        for (let m = 0; m <= PASS_M; m++) {
            const v = m * h;
            const pq = Math.sqrt(rr2 + v * v);
            //: `/ pq` is the whole point of the substitution: it is what the
            //: singular `1/sqrt(p^2-r^2)` becomes, and it is bounded.
            let gpv;
            const rel = (pq - floor) * invSpan;
            if (rel <= 0) gpv = gp[0];
            else if (rel >= 1) gpv = gp[PASS_N];
            else {
                const f = rel * PASS_N, i = f | 0;
                gpv = gp[i] + (gp[i + 1] - gp[i]) * (f - i);
            }
            const term = pq > 0 ? gpv / pq : 0;
            sum += (m === 0 || m === PASS_M) ? term * 0.5 : term;
        }
        rho[k] = -(1 / Math.PI) * sum * h;
        //: Measured non-negative for every hardness; clamped so a quadrature
        //: wobble can never subtract paint.
        if (rho[k] < 0) rho[k] = 0;
    }
    const sampleRho = function (r) {
        if (r >= 1) return 0;
        const rel = (r - floor) * invSpan;
        if (rel <= 0) return rho[0];
        const f = rel * PASS_RHO_K, i = f | 0;
        return i >= PASS_RHO_K ? rho[PASS_RHO_K]
                               : rho[i] + (rho[i + 1] - rho[i]) * (f - i);
    };
    //: P[p][t], cumulative trapezoid in t. Odd in t, so only t >= 0 is stored.
    const P = new Float64Array((PASS_K + 1) * (PASS_K + 1));
    //: The row's whole-pass optical depth. `passTau` divides by it to return a
    //: dimensionless FRACTION of a pass, so the magnitude can come from the
    //: coverage the floor actually used rather than from this table.
    const full = new Float64Array(PASS_K + 1);
    const dt = 1 / PASS_K;
    //: Row `a` is the perpendicular distance `hardness + a/PASS_K * (1-hardness)`.
    //: See `passTau` for the matching read.
    const pSpan = 1 - floor;
    for (let a = 0; a <= PASS_K; a++) {
        const p = floor + (a / PASS_K) * pSpan;
        //: SUBSTEPPED, AND INLINED. One trapezoid per stored point steps
        //: straight over the rho singularity a hardness plateau creates.
        //:
        //: The interpolation is written out rather than called: this loop runs
        //: PASS_K * PASS_K * SUB times per table, and through a closure it cost
        //: 120 ms per contact -- a visible stall at pointer-down. Inlined it is
        //: arithmetic on a typed array.
        const SUB = 4, ds = dt / SUB;
        const pp = p * p;
        let acc = 0;
        let prev;
        {
            const rel0 = (p - floor) * invSpan;
            const f0 = (rel0 <= 0 ? 0 : rel0) * PASS_RHO_K;
            const i0 = f0 | 0;
            prev = i0 >= PASS_RHO_K ? rho[PASS_RHO_K]
                                    : rho[i0] + (rho[i0 + 1] - rho[i0]) * (f0 - i0);
        }
        P[a * (PASS_K + 1)] = 0;
        for (let b = 1; b <= PASS_K; b++) {
            for (let q = 1; q <= SUB; q++) {
                const t = (b - 1) * dt + q * ds;
                const rr2 = Math.sqrt(pp + t * t);
                let cur;
                if (rr2 >= 1) {
                    cur = 0;
                } else {
                    const rel = (rr2 - floor) * invSpan;
                    if (rel <= 0) {
                        cur = rho[0];
                    } else {
                        const f = rel * PASS_RHO_K;
                        const i = f | 0;
                        cur = i >= PASS_RHO_K ? rho[PASS_RHO_K]
                                              : rho[i] + (rho[i + 1] - rho[i]) * (f - i);
                    }
                }
                acc += (prev + cur) * 0.5 * ds;
                prev = cur;
            }
            P[a * (PASS_K + 1) + b] = acc;
        }
        //: PIN THE TOTAL. The inversion decides HOW a pass's optical depth is
        //: distributed along the traversal; it must not decide HOW MUCH. A full
        //: pass at perpendicular `p` is `2 * P(p, sqrt(1-p^2))` and must equal
        //: `G(p)` exactly, or the tip's own cross-section moves -- measured at
        //: 3 of 255 in the narrow rim of a hardness-0.85 tip, where `G` is a
        //: near-step and the inversion is at its worst.
        //:
        //: Scaling the row makes a single pass exact BY CONSTRUCTION at every
        //: distance, for every hardness, independent of quadrature accuracy.
        //: Truncated integrals stay proportionally truncated, so caps still
        //: fall short of a pass and the `peak` floor still draws them.
        const tmax = Math.sqrt(Math.max(1 - p * p, 0));
        if (tmax > 0) {
            const fb = tmax * PASS_K;
            let bi = fb | 0;
            if (bi >= PASS_K) bi = PASS_K - 1;
            const bfrac = fb - bi;
            const row = a * (PASS_K + 1);
            const raw = 2 * (P[row + bi] + (P[row + bi + 1] - P[row + bi]) * bfrac);
            const want = passG(target * shape(p, hardness));
            if (raw > 1e-12 && want > 0) {
                const k = want / raw;
                for (let b = 0; b <= PASS_K; b++) P[row + b] *= k;
            }
            full[a] = want;
        }
    }
    //: U3-G. THE FLOOR TRAVELS WITH THE TABLE. `passTau` has to map a
    //: perpendicular distance onto a row, and it used `hardness` -- which
    //: is the same number ONLY for the smoothstep. Recomputing it there
    //: would be a second definition of the domain, and the two would
    //: silently disagree for a gaussian: every pixel would read the wrong
    //: row, which is a crease, which is the thing this table exists to fix.
    return { P: P, full: full, floor: floor };
}

const PASS_CACHE = new Map();
function passTableFor(hardness, target, profile) {
    //: Bucketed so a pressure-driven flow reuses tables instead of building one
    //: per mark. 256 matches FLOW_BUCKETS in the adapter.
    //:
    //: U3-G. THE PROFILE IS PART OF THE KEY. Without it a gaussian tip at
    //: the same hardness and flow as a smoothstep one would be handed the
    //: other's table -- a cache hit that paints the wrong brush, and the
    //: kind of bug that only appears when two presets are used in one
    //: session.
    const prof = profile || SMOOTHSTEP;
    const key = ((prof === GAUSSIAN ? 1 : 0) << 20)
              | ((Math.round(hardness * 256) & 511) << 10)
              | (Math.round(target * 256) & 511);
    let t = PASS_CACHE.get(key);
    if (!t) {
        t = buildPassTable(Math.round(hardness * 256) / 256,
                           Math.round(target * 256) / 256, prof);
        //: Bounded. A contact cannot pin unbounded memory through this cache.
        if (PASS_CACHE.size > 64) PASS_CACHE.clear();
        PASS_CACHE.set(key, t);
    }
    return t;
}

/**
 * Optical depth the tip lays on a pixel while travelling `t0 -> t1` along its
 * own axis, with `p` the perpendicular distance. All three in tip radii.
 *
 * Two interpolations along one shared row, not two full bilinear lookups: the
 * perpendicular distance is the same for both ends of the interval.
 */
function passTau(tbl, p, t0, t1, hardness) {
    if (p >= 1) return 0;
    const P = tbl.P, FULL = tbl.full;
    //: Rows span the shoulder, so the plateau maps to row 0 -- where the tip is
    //: opaque and every row would be the same anyway.
    //:
    //: U3-G. READ OFF THE TABLE, not recomputed from `hardness`. They are
    //: the same number for the smoothstep and they are not for a gaussian,
    //: whose support has no plateau to fold into row 0.
    const floor = tbl.floor === undefined ? hardness : tbl.floor;
    const span = 1 - floor;
    const rel = span > 1e-9 ? (p - floor) / span : 0;
    const fa = (rel < 0 ? 0 : rel) * PASS_K;
    let a0 = fa | 0;
    if (a0 >= PASS_K) a0 = PASS_K - 1;
    const af = fa - a0;
    const row0 = a0 * (PASS_K + 1), row1 = (a0 + 1) * (PASS_K + 1);

    //: BOTH ENDS INLINED. This ran as a closure called twice per pixel per
    //: segment; the branch is the hot loop of the whole engine and a call there
    //: is not free. The row weights are shared because both ends sit at the same
    //: perpendicular distance.
    let s1 = 1, u1 = t1;
    if (u1 < 0) { s1 = -1; u1 = -u1; }
    u1 *= PASS_K;
    if (u1 > PASS_K) u1 = PASS_K;
    let b1 = u1 | 0;
    if (b1 >= PASS_K) b1 = PASS_K - 1;
    const f1 = u1 - b1;
    const p1a = P[row0 + b1], p1b = P[row1 + b1];
    const w1 = (p1a + (P[row0 + b1 + 1] - p1a) * f1)
             + ((p1b + (P[row1 + b1 + 1] - p1b) * f1)
                - (p1a + (P[row0 + b1 + 1] - p1a) * f1)) * af;

    let s0 = 1, u0 = t0;
    if (u0 < 0) { s0 = -1; u0 = -u0; }
    u0 *= PASS_K;
    if (u0 > PASS_K) u0 = PASS_K;
    let b0 = u0 | 0;
    if (b0 >= PASS_K) b0 = PASS_K - 1;
    const f0 = u0 - b0;
    const p0a = P[row0 + b0], p0b = P[row1 + b0];
    const w0 = (p0a + (P[row0 + b0 + 1] - p0a) * f0)
             + ((p0b + (P[row1 + b0 + 1] - p0b) * f0)
                - (p0a + (P[row0 + b0 + 1] - p0a) * f0)) * af;

    const tau = s1 * w1 - s0 * w0;
    if (tau <= 0) return 0;
    //: THE FRACTION OF A PASS, not the pass itself. The table decides only HOW
    //: a pass's exposure is spread along the traversal; how MUCH is decided by
    //: `cov`, the same supersampled coverage the `peak` floor uses. Sampling the
    //: density at the pixel centre instead cost F1's tiny-tip isotropy -- 21.8%
    //: directional spread at radius 0.75 against a 3% gate -- because below
    //: radius 2 `coverageAt` integrates over the pixel SQUARE and a centre
    //: sample is a different quantity.
    const f0v = FULL[a0], f1v = FULL[a0 + 1];
    const den = f0v + (f1v - f0v) * af;
    return den > 1e-12 ? tau / den : 0;
}

/*
 * U3-D. THE STIPPLE DRAW.
 *
 * Legacy's threshold, kept: below this a dab stipples, at or above it the
 * control is off and not one pixel may move. Twelve of the sixteen shipping
 * presets sit at 1.0 and must be byte-identical.
 */
const STIPPLE_BELOW = 0.99;

/**
 * A deterministic pseudo-random draw in [0, 1) from two integers.
 *
 * TWO USERS, ONE IMPLEMENTATION. The density stipple draws per (pixel, stroke);
 * U3-J's per-dab jitter draws per (mark, stroke-and-channel). Two copies of a
 * hash is two things to get subtly different, and a jitter that correlated with
 * the stipple would put visible structure in both.
 *
 * Murmur3's finalizer. Integer-only, no allocation, and it avalanches -- which
 * matters because the argument is a raster index, so neighbouring pixels differ
 * by 1 and a weak mix would put visible structure in the mask.
 *
 * `i` IS THE POSITION. It is `y * width + x`, a bijection of the pixel for a
 * given buffer, so hashing it is hashing the coordinate. The buffer does not
 * resize mid-stroke, which is the only thing that could make one pixel take two
 * draws.
 *
 * Not `Math.random()`, deliberately. Legacy's global stream makes a stroke
 * unreproducible between renders and perturbs every other consumer of
 * randomness -- `brushGrain`'s own comment records a grain comparison that
 * "diverged the seeded RNG between the runs" and reported 72% of pixels
 * changed, "almost all of it RNG divergence rather than grain", off which a
 * shipped default was then chosen. Legacy also draws BEFORE its
 * inside-the-tip test, so it burns a number per bounding-box pixel and its
 * stream position depends on the box. Neither is reproduced.
 */
function hash01(i, seed) {
    let h = (i ^ seed) >>> 0;
    h = Math.imul(h ^ (h >>> 16), 2246822507) >>> 0;
    h = Math.imul(h ^ (h >>> 13), 3266489909) >>> 0;
    h = (h ^ (h >>> 16)) >>> 0;
    return h / 4294967296;
}

/*
 * M1a. DRY-MEDIA STRANDS: THE APPROVED MATERIAL, NOT A PIXEL MASK.
 *
 * DEC-BRUSH (owner, 2026-09-28) rejected every universal per-pixel mask -- the
 * stipple reads as one-pixel spray at every size -- and approved the study's
 * family materials (`Evidence/brush-material-study-2026-09-28/template.html`
 * `dabDry`). For the dry media that material is STRANDS: a noise field indexed
 * by where a pixel sits ACROSS the tip (`u`, signed px from the path) and how
 * far ALONG the stroke it is (`s`, arc length in px). Because `s` is the
 * stroke's own arc length, the strands move with the tip and run continuous
 * along travel; `streak` against `strand` is what makes Charcoal scratch and
 * Pastel powder.
 *
 *     m    = smooth(0.2, 0.8, 0.6 n(u/su, s/ss) + 0.4 n(2u/su, s/(0.35 ss)))
 *     mult = floor + (1 - floor) smooth(th - band, th + band, m),  th = 1 - density
 *
 * The multiplier scales a contribution's coverage -- the floor AND the pass
 * price -- so a full pass lands on `target * shape * mult` and the stroke's
 * accumulation model is untouched. It REPLACES the stipple for these media;
 * running both would spend Density twice, U3-D's own argument.
 *
 * `n` is the study's value noise over its own lattice hash -- the same hash,
 * read from a 256-cell tile (`noiseTileFor`) so the approved look is the one
 * built and the hash is paid once per seed rather than per pixel.
 */
function latticeHash(x, y, z) {
    let h = Math.imul(x | 0, 0x27d4eb2d) ^ Math.imul(y | 0, 0x165667b1) ^ Math.imul(z | 0, 0x3c6ef372);
    h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function latticeNoise(x, y, seed) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    const a = latticeHash(xi, yi, seed), b = latticeHash(xi + 1, yi, seed);
    const c = latticeHash(xi, yi + 1, seed), d = latticeHash(xi + 1, yi + 1, seed);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

//: THE SAME NOISE FROM A TILE, because the hash was the cost. A sweep visits
//: every pixel once per covering segment -- twenty or more at the dry presets'
//: spacing -- and hashing twelve lattice points each time made a Charcoal
//: stroke 2.4x slower than its stipple. The tile holds the SAME `latticeHash`
//: values for one 256-cell period; beyond it the lattice repeats, 256 strands
//: across (280 px at Charcoal's width) and 256 streaks along (10,240 px).
//: Built once per seed, about 65k hashes, and kept for the few seeds a stroke
//: and its neighbours use.
const NOISE_TILE = 256;
const _noiseTiles = new Map();
function noiseTileFor(seed) {
    const key = seed | 0;
    let t = _noiseTiles.get(key);
    if (t) return t;
    t = new Float32Array(NOISE_TILE * NOISE_TILE);
    for (let y = 0; y < NOISE_TILE; y++) {
        for (let x = 0; x < NOISE_TILE; x++) t[(y << 8) | x] = latticeHash(x, y, key);
    }
    if (_noiseTiles.size >= 12) _noiseTiles.clear();
    _noiseTiles.set(key, t);
    return t;
}

function tileNoise(t, x, y) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    const x0 = xi & 255, x1 = (xi + 1) & 255, y0 = (yi & 255) << 8, y1 = ((yi + 1) & 255) << 8;
    const a = t[y0 | x0], b = t[y0 | x1], c = t[y1 | x0], d = t[y1 | x1];
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

function smoothBand(e0, e1, x) {
    let t = (x - e0) / (e1 - e0);
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return t * t * (3 - 2 * t);
}

/** The strand multiplier at tip-local (`u` across, `s` along), in [floor, 1]. */
function materialAt(mat, u, s) {
    const n = 0.6 * tileNoise(mat.n0, u / mat.strand, s / mat.streak)
            + 0.4 * tileNoise(mat.n1, u / (mat.strand * 0.5), s / (mat.streak * 0.35));
    const m = smoothBand(0.2, 0.8, n);
    return mat.floor + (1 - mat.floor) * smoothBand(mat.th - mat.band, mat.th + mat.band, m);
}

//: The study's edge noise period, in px.
const MATERIAL_EDGE_PERIOD = 1.7;

/**
 * A ragged edge: the tip's radius at this PIXEL, as a factor of the nominal.
 * Indexed by document position, not tip position, so overlapping marks agree
 * on where the edge is and a stroke's rim cannot shimmer mark to mark.
 */
function materialEdge(mat, x, y) {
    return 1 - mat.rough + 2 * mat.rough
        * tileNoise(mat.ne, x / MATERIAL_EDGE_PERIOD, y / MATERIAL_EDGE_PERIOD);
}

/** `-ln(1 - tc)`, from the `NEGLOG` table built above. */
function negLogOf(tc) {
    if (tc <= 0) return 0;
    if (tc >= 1) return EXPN_MAX;
    if (tc > NEGLOG_MAX_TC) return -Math.log(1 - tc);
    const gi = (tc / NEGLOG_MAX_TC) * NEGLOG_N;
    const g0 = gi | 0;
    return NEGLOG[g0] + (NEGLOG[g0 + 1] - NEGLOG[g0]) * (gi - g0);
}

/**
 * The deposition parameters for one stroke, resolved once.
 *
 * `step` is the gap divided by the tip's radius along travel, threaded from the
 * sampler so the two cannot disagree about how far apart the marks are.
 */
function depositionFor(spec) {
    const s = spec || {};
    const hardness = Math.min(1, Math.max(0, Number(s.hardness) || 0));
    const flow = Math.min(1, Math.max(0, s.flow === undefined ? 1 : s.flow));
    const opacity = Math.min(1, Math.max(0,
        s.opacity === undefined ? 1 : s.opacity));
    const density = Math.min(1, Math.max(0,
        s.density === undefined ? 1 : s.density));
    const step = Math.max(1e-6, Number(s.step) || 1);
    // U3-R. THE ALONG-TRAVEL PROFILE WEIGHT DEPENDS ON WHICH RENDERER RUNS.
    //
    // `overlapK` counts how many contributions cover one pixel. For a STAMP
    // train each dab catches the pixel at a different point on its falloff, so
    // the mean profile value is the right weight -- that is `profileMean`.
    //
    // A SWEEP is not that. A pixel at perpendicular distance d from the path is
    // at distance d from EVERY segment that brackets it, so every covering
    // segment contributes the SAME `shapeAt(d/r)`. The along-travel weight is
    // therefore 1, and K is simply `2/step`.
    //
    // Measured, on the straight fixture: with the stamp train's weight a swept
    // stroke at Flow 0.5 reached 144 of 255 where the train reaches 127 -- 13%
    // too much paint. With this weight the two agree to within 5 of 255 across
    // hardness 0..0.85 and Flow 0.2..1.
    const swept = !!s.swept;
    //: U3-G. THE FALLOFF THE PRESET DECLARES. The adapter did not forward
    //: this field at all -- the same dropped-field class as the eleven
    //: U3-TF found -- so Airbrush, Ink Wash and Charcoal all rendered with
    //: the smoothstep whatever they asked for.
    const profile = profileFor(s.falloff);
    // BUILDUP decides whether the Opacity bound applies WITHIN the stroke.
    // Off: target is Flow, merge multiplies by Opacity once, and the stroke can
    // never exceed Opacity however long it is worked. On: target is Flow x
    // Opacity, merge multiplies by 1, and one scrubbed stroke can reach full
    // black with Opacity acting as a rate. That is the wash/airbrush semantic.
    const target = Math.min(1, Math.max(0,
        s.buildup ? flow * opacity : flow));
    //: U3-D. DENSITY IS NOT IN HERE ANY MORE, and it never should have been.
    //:
    //: `overlapK` counts how many contributions cover a pixel. Density does not
    //: change that count -- it changes WHICH PIXELS are covered at all. Folding
    //: it in here made a low-density preset deposit FEWER, DARKER contributions
    //: (a smaller K raises `fEff`), which is the opposite of a stipple, and is
    //: why the four presets that declare density below 1.0 diverged from Legacy
    //: by a median of 353% of painted pixels while the other twelve sat at 24%.
    //:
    //: Leaving it here AND stippling would spend it twice: once thinning
    //: coverage, once thickening what survives.
    const overlapK = Math.max(
        1, (2 / step) * (swept ? 1 : profileMean(hardness, profile)));
    const accumulating = target < NO_ACCUMULATE_ABOVE;
    const fEff = accumulating
        ? 1 - Math.pow(1 - target, 1 / overlapK)
        : target;
    //: M1a. A dry medium below the bypass carries STRANDS instead of the
    //: stipple. At Density 0.99 and above there is neither: the plain brush.
    const ms = s.material;
    const material = (ms && density < STIPPLE_BELOW)
        ? (function () {
            const seed = (Number(ms.seed) || 0) | 0;
            return Object.freeze({
                strand: ms.strand, streak: ms.streak, band: ms.band, floor: ms.floor,
                rough: ms.rough || 0, seed: seed, th: 1 - density,
                //: The study's three noise fields: two strand octaves and the
                //: edge, each its own seed as `dabDry` seeds them.
                n0: noiseTileFor(seed), n1: noiseTileFor(seed + 7),
                ne: (ms.rough || 0) > 0 ? noiseTileFor(seed + 3) : null,
            });
        })()
        : null;
    return Object.freeze({
        hardness: hardness, flow: flow, opacity: opacity, density: density,
        step: step, buildup: !!s.buildup, swept: swept,
        profile: profile, falloff: profile.name,
        //: null when the control is off, so the hot loop tests one identity
        //: rather than recomputing a threshold per pixel.
        //: U3-D2. TWO PROBABILITIES, AND A PER-DAB DRAW.
        //:
        //: The first version drew ONCE per (pixel, stroke), which made the
        //: union exactly `density` at any spacing -- mathematically clean and
        //: visually wrong. Re-covering a pixel could not fill it in, so the
        //: stroke had the SAME hole fraction at its spine as at its rim: a flat
        //: dither with a saturated core where the survivors reached full alpha
        //: while their neighbours stayed at zero. The owner's word for it was
        //: "real bad", and the artefact they named -- "a denser line in the
        //: middle" -- is exactly that core.
        //:
        //: Re-rolling per dab is not an accident of `Math.random()`. It is what
        //: gives a stroke its density GRADIENT: a spine pixel gets many draws
        //: and fills in, a rim pixel gets one or two and does not. BE7's
        //: correction exists to make the resulting union equal the requested
        //: density, and with the draw restored the correction is needed again.
        //:
        //: `pOpening` keeps the tap honest. BE7 reads its overlap off the
        //: SPACING SETTING, which is not the overlap an isolated dab has, so
        //: Legacy stipples a lone Bristle Rake tap to 7.3%. A dab that is the
        //: only dab overlaps nothing, so it draws against the declared density.
        material: material,
        stipple: (density < STIPPLE_BELOW && !material)
            ? Object.freeze({
                p: 1 - Math.pow(1 - density, 1 / Math.max(1, 2 / step)),
                pOpening: density,
                seed: (Number(s.seed) || 0) | 0,
            })
            : null,
        target: target, overlapK: overlapK,
        accumulating: accumulating, fEff: fEff,
        //: Built once per (hardness, target) and shared by every mark of the
        //: contact. Cached at module level, so a steady hand builds one.
        //: U3-TF. The tip's SHAPE, frozen with everything else at `begin`, so a
        //: preset change mid-contact cannot alter it. `stamp` builds the frame
        //: per mark because the radius moves with pressure.
        tip: Object.freeze({
            kind: typeof s.tipKind === "string" ? s.tipKind : "round",
            ratio: s.ratio === undefined ? 1 : s.ratio,
            angle: s.angle === undefined ? 0 : s.angle,
            spikes: s.spikes === undefined ? 2 : s.spikes,
        }),
        //: SWEPT ONLY. `passTau` is reached from `sweep`'s accumulating
        //: branch and nowhere else, so a stamp train would build a table
        //: it never reads -- 12 ms of it, per hardness and flow.
        passTable: (accumulating && swept)
            ? passTableFor(hardness, target, profile) : null,
    });
}

/**
 * Deposit one contribution at one pixel. BE17's `_depositAt`.
 *
 * RETURNS THE RESULTING COVERAGE, which is what makes the dirty bounds exact.
 * A contribution at Flow 0, or one whose accumulated share has not yet reached
 * one part in 256, leaves the pixel reading zero -- and a pixel reading zero is
 * not dirty. Expanding on the ATTEMPT instead of the RESULT would make the
 * bounds the tip's bounding box, which is the thing this unit exists to stop.
 *
 * Self-correcting for the accumulating case: if a later contribution pushes the
 * same pixel over the threshold, THAT call returns non-zero and dirties it.
 */
function deposit(buffer, i, cov, dep, dabIndex) {
    //: U3-R2F C2. THE OPENING MARK CONTRIBUTES NO EXPOSURE, and that is the cap
    //: repair rather than a special case.
    //:
    //: `_placeMarks` stamps the first mark of every contact and sweeps the rest
    //: (`canvas-adapter.js:452`). At full flow `fEff = 1 - (1-1)^(1/K) = 1`, so
    //: the stamp used to deposit a WHOLE pass and the sweeps then added their
    //: own on top -- measured as a 23% cap over-deposit, 121 -> 149 at hardness
    //: 0, which shipping shows at no flow.
    //:
    //: A stamp has zero arc length, so under the optical-depth model its
    //: exposure is exactly zero. The `peak` floor is idempotent and already
    //: carries the full tip footprint, so an exact TAP is unchanged and a tap
    //: that becomes a stroke gains only the sweeps' exposure. No after-release
    //: correction, and nothing to undo.
    //: `dep.swept` is the discriminator, NOT "this is a stamp". Today
    //: `rendererFor` hard-codes round/untextured (`canvas-adapter.js:307`) so
    //: `stamp` only ever draws a swept stroke's opening mark -- but U3-TF wires
    //: real tip routing, and a genuine stamp TRAIN still needs its accumulator
    //: or flow stops building for every textured and scattered tip.
    return depositWith(buffer, i, cov, dep.target,
                       dep.swept ? 0 : cov * dep.fEff, dep.accumulating, dep.stipple,
                       dabIndex);
}

/**
 * The same deposit, with the decided quantities passed explicitly.
 *
 * U3-R2F F2 needs them per PIXEL rather than per contribution: a swept segment
 * whose two ends were laid down at different pressures has a different `target`
 * at each end, and `deposit` above can only be told one.
 *
 * `cov` AND `accWeight` ARE SEPARATE ARGUMENTS, and that is the load-bearing
 * part rather than an untidiness. The MAX floor is `cov * target` and must keep
 * the tip's profile whatever the accumulator is doing; the accumulator's weight
 * is `cov * fEff` in the ordinary case but is a whole telescoping share in
 * F2's arc-length case, where `cov` is already inside the power and applying it
 * again would square the falloff. Collapsing the two back into one argument
 * would flatten the rim of every swept soft tip.
 *
 * `deposit` is this function with the frozen values, so there is one
 * implementation of the accumulator and not two that can drift.
 */
function depositWith(buffer, i, cov, target, accWeight, accumulating, stipple, dabIndex) {
    //: U3-D. DENSITY, AS ONE DRAW PER (PIXEL, DAB).
    //:
    //: Legacy re-rolls `Math.random()` for every pixel of every dab
    //: (`canvas-core.js:2604`), so the coverage an owner sees is the UNION over
    //: every dab touching a pixel, and it climbs as spacing tightens even
    //: though the Density control has not moved. BE7 measured exactly that at
    //: Density 0.35 -- spacing 0.32 gave 0.698 coverage, spacing 0.02 gave
    //: 1.000, "at close spacing the control does nothing at all" -- and
    //: corrected it by pre-shrinking the per-dab probability to
    //: `1 - (1 - density)^(1/overlap)`.
    //:
    //: THE DAB INDEX IS IN THE KEY, and the first version of this left it out.
    //: One draw per (pixel, stroke) made the union exactly `density` at any
    //: spacing with no correction at all -- mathematically clean, and the owner
    //: rejected it on sight. Re-covering a pixel could not re-roll it, so the
    //: hole fraction at the spine equalled the hole fraction at the rim: a flat
    //: dither with a saturated core, not a stroke. The re-roll is what gives a
    //: stroke its density GRADIENT -- a spine pixel gets many draws and fills
    //: in, a rim pixel gets one or two and does not -- so BE7's correction is
    //: load-bearing here rather than redundant, and it lives on `stipple.p`.
    //:
    //: THE TAP IS THE EXCEPTION, through `stipple.pOpening`. BE7's `overlap`
    //: comes from the SPACING SETTING, which is not the overlap an isolated dab
    //: has -- so Legacy stipples a lone Bristle Rake tap down to
    //: `1 - 0.15^(1/25) = 7.3%`, measured at 132 px of ~1800. Dab 0 overlaps
    //: nothing, so it draws against the declared density instead.
    //:
    //: Deterministic, which Legacy's global stream is not: the same seed and
    //: the same path replay to the same pixels, so the feature can be measured
    //: at all. See `hash01` for what that costs Legacy and why.
    if (stipple !== null && stipple !== undefined
        && hash01(i, (stipple.seed ^ ((dabIndex | 0) * 0x9E3779B1)) | 0)
           >= (dabIndex ? stipple.p : stipple.pOpening)) {
        //: SKIPPED, not zeroed. The pixel keeps whatever earlier strokes put
        //: there, and returning it unchanged means the caller's `> 0` dirty
        //: test does not mark a pixel this dab did not touch.
        const heldBuilt = buffer.acc[i] >> 8;
        const heldPeak = buffer.peak[i];
        return heldBuilt > heldPeak ? heldBuilt : heldPeak;
    }
    const peak = (cov * target * 255) | 0;
    if (peak > buffer.peak[i]) buffer.peak[i] = peak;
    if (accumulating) {
        const prev = buffer.acc[i];
        const next = prev + (65535 - prev) * accWeight;
        buffer.acc[i] = next > 65535 ? 65535 : next;
    }
    // Read the STORED peak, not the local one: it is a Uint8 and clamps.
    const built = buffer.acc[i] >> 8;
    const stored = buffer.peak[i];
    return built > stored ? built : stored;
}


/*
 * TIP GEOMETRY. U3-TF.
 *
 * PORTED FROM `canvas-core.js:2073-2330`, which is Studio's own parity oracle
 * and is itself derived from Krita's `KisCircleMaskGenerator` (`fixRotation`
 * plus the anisotropic xcoef/ycoef). Not paraphrased: the constants, the norm
 * and above all the ORDER are Legacy's, because Legacy already paid for getting
 * them wrong once and recorded the receipts.
 *
 * V2 had none of this. `stamp` measured `sqrt(dx*dx + dy*dy)` for every tip, so
 * `rendererFor` would route a flat or spiked tip to the stamp renderer and the
 * stamp renderer would draw it round. Fifteen of the sixteen shipping presets
 * declare a property the engine could not express.
 *
 * THE ORDER IS THE REPAIR, and Legacy states why better than a new comment
 * could: "Rotation preserves an isotropic norm, so folding the angle and THEN
 * measuring sqrt(dx^2 + dy^2) cannot change any pixel: Spikes was not unwired,
 * it was algebraically incapable of doing anything."
 *
 *     translate -> ROTATE by -angle -> FOLD (spikes) -> ANISOTROPY (ratio)
 *                                                    -> the tip's own norm
 */

//: Half-HEIGHT as a fraction of the dab RADIUS, before the owner's Ratio.
//: A fraction of the RADIUS, not of the half-width -- Legacy's table records
//: that the half-width reading quietly narrowed Bold Marker from 0.35r to
//: 0.28r, 4,420 painted pixels down to 3,836, and only the tenth of ten
//: presets caught it.
const TIP_ASPECT = { round: 1, scatter: 1, flat: 0.3, marker: 0.35 };

//: Marker is a rectangle, so it measures with a Chebyshev norm rather than a
//: Euclidean one. That is the tip's identity, not a setting.
const TIP_NORM = { marker: "chebyshev" };

//: Marker's long axis is 0.8r. A scale on the tip, not an aspect.
const TIP_EXTENT = { marker: 0.8 };

/** Krita's `fixRotation`: fold a point's angle into the first spike sector. */
function foldSpikes(xr, yr, spikes) {
    const spikeAngle = Math.PI / spikes;
    const cs = Math.cos(-2 * spikeAngle);
    const ss = Math.sin(-2 * spikeAngle);
    let angle = Math.atan2(yr < 0 ? -yr : yr, xr);
    let sx = xr, sy = yr < 0 ? -yr : yr;
    //: Bounded: each turn removes 2*spikeAngle from an angle that starts at or
    //: below PI, so at the minimum 3 spikes this runs at most twice.
    while (angle > spikeAngle) {
        const nx = cs * sx - ss * sy;
        const ny = ss * sx + cs * sy;
        sx = nx; sy = ny;
        angle -= 2 * spikeAngle;
    }
    return { x: sx, y: sy };
}

/**
 * The tip's radius ALONG A DIRECTION OF TRAVEL, as a fraction of the nominal.
 *
 * Legacy BE7's `alongExtentFor` (`canvas-core.js`), ported: same table, same
 * ellipse, same answer of 1.0 for a circle or for a caller with no heading.
 * The sampler scales its gap by it, so a flat tip travelling across its thin
 * side is spaced by the width it PRESENTS. V2 never passed it -- the sampler
 * has taken `extentFor` since V2-03 and defaulted to a circle -- so a Flat
 * Chisel with its broad face across the travel was stamped about 3.3x too far
 * apart, and the owner's first V2 sign-off saw it: "Has bumpy sides, seems
 * like it's making a stamp every stroke?"
 */
function alongExtent(tip, travelAngle, tipAngle) {
    if (typeof travelAngle !== "number" || typeof tipAngle !== "number") return 1.0;
    const t = tip || {};
    const kind = TIP_ASPECT[t.kind] !== undefined ? t.kind : "round";
    const ratio = Math.min(1, Math.max(0.05, Number(t.ratio) || 1));
    const aspect = TIP_ASPECT[kind] * ratio;
    if (Math.abs(aspect - 1) <= 1e-6) return 1.0;
    const extent = TIP_EXTENT[kind] || 1.0;
    //: Travel expressed in the tip's own frame.
    const phi = travelAngle - tipAngle;
    const c = Math.cos(phi), s = Math.sin(phi);
    const rx = extent, ry = aspect;
    const denom = Math.sqrt((ry * c) * (ry * c) + (rx * s) * (rx * s));
    return denom > 1e-9 ? (rx * ry) / denom : rx;
}

/**
 * Everything about a dab's shape that does not vary per pixel, resolved once.
 *
 * RETURNS null FOR A TIP THAT IS ALREADY A CIRCLE, and that is load-bearing
 * rather than an optimisation: a null frame makes `stamp` take the exact
 * Euclidean closure it has always taken, so every round tip stays
 * BYTE-IDENTICAL. §26's stop condition is that ordinary radius >= 2 output must
 * not move, and this is how that is guaranteed by construction instead of by
 * measurement.
 *
 * `at` returns a NORMALISED distance -- 0 at the core, 1 at the edge -- which
 * `stamp` scales back by `r` because `coverageAt` divides by `r` again.
 */
function tipFrame(r, tip) {
    const t = tip || {};
    const kind = TIP_ASPECT[t.kind] !== undefined ? t.kind : "round";
    const ratio = Math.min(1, Math.max(0.05, Number(t.ratio) || 1));
    const spikes = Math.max(2, Math.min(12, Math.round(Number(t.spikes) || 2)));
    const ang = Number(t.angle) || 0;
    const extent = TIP_EXTENT[kind] || 1;
    const aspect = TIP_ASPECT[kind] * ratio;
    const rx = Math.max(0.5, r * extent);
    const ry = Math.max(0.5, r * aspect);
    const folds = spikes > 2;
    const circular = Math.abs(rx - ry) < 1e-9;
    const chebyshev = TIP_NORM[kind] === "chebyshev";
    //: A circle has no orientation, so on a round tip at Ratio 1 and Spikes 2
    //: an Angle cannot do anything. `TIP_CAPABILITIES` calls that
    //: "needs-shape"; here it means there is no frame to build at all.
    if (circular && !folds && !chebyshev) return null;
    const rotates = !!ang && !(circular && !folds);
    const cosA = rotates ? Math.cos(-ang) : 1;
    const sinA = rotates ? Math.sin(-ang) : 0;
    const invRx = 1 / rx, invRy = 1 / ry;
    return {
        rx: rx, ry: ry, rotates: rotates, folds: folds, chebyshev: chebyshev,
        at: function (dx, dy) {
            let lx = dx, ly = dy;
            if (rotates) {
                lx = dx * cosA - dy * sinA;
                ly = dx * sinA + dy * cosA;
            }
            if (folds) {
                const f = foldSpikes(lx, ly, spikes);
                lx = f.x; ly = f.y;
            }
            const nx = lx * invRx, ny = ly * invRy;
            if (!chebyshev) return Math.sqrt(nx * nx + ny * ny);
            const ax = nx < 0 ? -nx : nx, ay = ny < 0 ? -ny : ny;
            return ax > ay ? ax : ay;
        },
    };
}

/**
 * One stamped mark. §11.1's renderer for textured paint, grain, scatter,
 * airbrush and custom tips.
 */
function stamp(buffer, mark, radiusPx, dep, tipAngle, dabIndex, arcPx) {
    const r = Math.max(SUBPIXEL_FLOOR_RADIUS, radiusPx);
    //: M1a. The strand frame is read BEFORE the lattice snap replaces the mark.
    //: `arcPx` is the stroke's arc length at this mark, from the caller: NOT
    //: `mark.travelPx`, which is the travel of the INPUT SAMPLE the mark was
    //: placed from and is shared by every mark that sample produced -- the
    //: strands jumped 8.5 px at some boundaries and not at others. Omitted, it
    //: is 0, which is exact for the one stamp a dry stroke lays: its opening.
    const mat = dep.material || null;
    const matS = mat && typeof arcPx === "number" ? arcPx : 0;
    const matHeading = mat && typeof mark.headingRad === "number" ? mark.headingRad : 0;
    const matTx = Math.cos(matHeading), matTy = Math.sin(matHeading);
    //: Sub-pixel tips are placed on the lattice. See `SUBPIXEL_FLOOR_RADIUS`.
    if (radiusPx <= SUBPIXEL_FLOOR_RADIUS) {
        mark = { x: snapToPixelCentre(mark.x), y: snapToPixelCentre(mark.y) };
    }
    //: A ragged edge can reach past the nominal radius.
    const reachR = mat && mat.rough ? r * (1 + mat.rough) : r;
    const x0 = Math.max(0, Math.floor(mark.x - reachR));
    const y0 = Math.max(0, Math.floor(mark.y - reachR));
    const x1 = Math.min(buffer.width - 1, Math.ceil(mark.x + reachR));
    const y1 = Math.min(buffer.height - 1, Math.ceil(mark.y + reachR));
    let touched = 0;
    // Accumulated locally and unioned ONCE. A method call per painted pixel
    // would put the bookkeeping inside the hot loop of a performance unit.
    let minX = 0, minY = 0, maxX = -1, maxY = -1;
    //: Distance from an arbitrary sample point to the mark. Hoisted so the
    //: integrator can call it 16 times for a tiny tip and once otherwise.
    const mx = mark.x, my = mark.y;
    //: NULL FRAME MEANS THE OLD CLOSURE, byte for byte. Only a tip that is
    //: genuinely not a circle builds one, so a round tip cannot move.
    //:
    //: A PER-MARK ANGLE OVERRIDES THE CONTACT'S, because a flat tip follows the
    //: stroke: `canvas-core.js:2381` resolves a flat's angle as the stroke
    //: heading plus the owner's offset, and the heading changes along a curve.
    //: The rest of the shape is frozen at `begin` with everything else.
    //:
    //: AN ARGUMENT, NOT A FIELD ON THE MARK. The sampler FREEZES its marks --
    //: §19.9's contract that nothing can alter a contact mid-flight -- so the
    //: first version of this, which set `mark.tipAngle`, threw
    //: "object is not extensible" the moment a flat tip was drawn. The freeze
    //: is right and the write was wrong.
    const tipAtMark = (typeof tipAngle === "number" && dep.tip)
        ? { kind: dep.tip.kind, ratio: dep.tip.ratio, spikes: dep.tip.spikes,
            angle: tipAngle }
        : dep.tip;
    const frame = tipFrame(r, tipAtMark);
    const distance = frame
        ? function (sx, sy) {
            //: `at` is normalised to the tip's own extent; `coverageAt` divides
            //: by `r` again, so it is scaled back here rather than teaching the
            //: integrator about two kinds of distance.
            return frame.at(sx - mx, sy - my) * r;
        }
        : function (sx, sy) {
            const dx = sx - mx, dy = sy - my;
            return Math.sqrt(dx * dx + dy * dy);
        };
    for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
            //: M1a. A ragged edge moves the radius per pixel; without one it
            //: is `r` and the call is the one it always was.
            const re = (mat && mat.rough) ? r * materialEdge(mat, x, y) : r;
            let cov = coverageAt(x, y, re, dep.hardness, distance, dep.profile);
            if (cov <= 0) continue;
            if (mat) {
                const dx = x + 0.5 - mx, dy = y + 0.5 - my;
                //: `u` has the sweep's sign (`d x t`), so an opening stamp and
                //: the segment after it read the same strand.
                cov *= materialAt(mat, dx * matTy - dy * matTx, matS + dx * matTx + dy * matTy);
            }
            if (deposit(buffer, y * buffer.width + x, cov, dep, dabIndex) > 0) {
                if (maxX < minX) { minX = maxX = x; minY = maxY = y; }
                else {
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }
            touched += 1;
        }
    }
    if (maxX >= minX) buffer.dirty.expand(minX, minY, maxX + 1, maxY + 1);
    buffer.contributions += 1;
    return touched;
}

/**
 * One analytically swept band between two points. §11.1's renderer for clean
 * hard line art, Pixel-adjacent geometry and geometric erasers.
 *
 * BOUNDED, NOT GENERAL, and `rendererFor` is where that is decided. DiVerdi
 * §2.6.2 warns that a swept contour with a constant fill "loses the natural
 * media quality" -- which is a description of what a max-blend produces, from
 * the stamping side. A sweep is right for a hard round tip at constant width
 * and wrong for anything whose character comes from its texture.
 *
 * Deposits ONCE per pixel for the whole segment, which is what makes it a sweep
 * rather than a very dense stamp train: the coverage is the distance to the
 * segment, not the sum of overlapping discs.
 *
 * `depTo` IS U3-R2F F2, and it is optional on purpose.
 *
 * Depositing once per pixel is what made the sweep right, and it is also what
 * made pressure-driven flow band: one deposition served the whole capsule, so a
 * pressure ramp became a staircase that stepped at every mark. Measured on the
 * fixture in `Evidence/u3r2f-tiny/f2_banding_probe.js`, the detrended residual
 * on an opacity ramp had its dominant period at 8.0 px against a measured
 * segment gap of 8.1 px, and sat 2.81 higher at boundaries than at midpoints.
 * The period WAS the segment rate; there is no interpretation left to do.
 *
 * So when the segment's two ends were laid down at different flows, the caller
 * passes both depositions and each pixel is deposited at its OWN position along
 * the segment -- the same `t` the coverage already computes, so the flow and
 * the geometry cannot disagree about where a pixel is.
 *
 * `target` is linear in flow in both buildup modes (`flow` or `flow * opacity`),
 * so interpolating it IS interpolating flow -- not an approximation of it.
 * `overlapK` is identical for both ends because only flow differs between them,
 * so `fEff` is recovered exactly from the interpolated target rather than
 * lerped, and the accumulating branch is decided per pixel from that target.
 *
 * WITHOUT `depTo`, OR WITH THE SAME OBJECT, NOTHING CHANGES. Constant-flow
 * strokes take the identical path they took before and pay nothing: no `pow`,
 * no second projection, no branch inside the pixel loop beyond one hoisted
 * boolean. That is what keeps §14's "constant-flow strokes remain
 * byte-identical" true by construction rather than by tolerance.
 */
function sweep(buffer, from, to, radiusPx, dep, depTo, dabIndex, arcFromPx) {
    const r = Math.max(SUBPIXEL_FLOOR_RADIUS, radiusPx);
    //: M1a. The stroke's arc length at `from`, from the caller; the strand
    //: coordinate along the stroke starts here. Not `from.travelPx` -- see
    //: `stamp` for why that field cannot be used.
    const mat = dep.material || null;
    const matS = mat && typeof arcFromPx === "number" ? arcFromPx : 0;
    //: BOTH ends, and before the bounds and the direction vector, so the whole
    //: segment moves together rather than changing angle. Consecutive marks
    //: that snap to the same lattice point give a zero-length segment, which
    //: sweeps a disc and leaves no gap.
    if (radiusPx <= SUBPIXEL_FLOOR_RADIUS) {
        from = { x: snapToPixelCentre(from.x), y: snapToPixelCentre(from.y) };
        to = { x: snapToPixelCentre(to.x), y: snapToPixelCentre(to.y) };
    }
    //: A ragged edge can reach past the nominal radius.
    const reachR = mat && mat.rough ? r * (1 + mat.rough) : r;
    const x0 = Math.max(0, Math.floor(Math.min(from.x, to.x) - reachR));
    const y0 = Math.max(0, Math.floor(Math.min(from.y, to.y) - reachR));
    const x1 = Math.min(buffer.width - 1, Math.ceil(Math.max(from.x, to.x) + reachR));
    const y1 = Math.min(buffer.height - 1, Math.ceil(Math.max(from.y, to.y) + reachR));
    const vx = to.x - from.x, vy = to.y - from.y;
    const len2 = vx * vx + vy * vy;
    let touched = 0;
    let minX = 0, minY = 0, maxX = -1, maxY = -1;
    //: Distance from an arbitrary sample point to the SEGMENT. Same shape as
    //: the stamp's, so one integrator serves both and neither renderer has to
    //: know about the other's geometry.
    const fx = from.x, fy = from.y;
    const distance = function (sx, sy) {
        const px = sx - fx, py = sy - fy;
        let t = len2 > 0 ? (px * vx + py * vy) / len2 : 0;
        t = t < 0 ? 0 : (t > 1 ? 1 : t);
        const dx = px - vx * t, dy = py - vy * t;
        return Math.sqrt(dx * dx + dy * dy);
    };
    //: U3-R2F F2. Hoisted out of the pixel loop entirely: a constant-flow
    //: stroke at full flow reads two booleans and takes the path it always took.
    //: S. The distance to the segment's LINE, for pricing a pass (below). Only
    //: tiny tips call it; above the supersampling radius `perp` already is it.
    const lineDistance = function (sx, sy) {
        const px = sx - fx, py = sy - fy;
        if (!(len2 > 0)) return Math.sqrt(px * px + py * py);
        const cr = px * vy - py * vx;
        return (cr < 0 ? -cr : cr) / Math.sqrt(len2);
    };
    const shapeAtFn = (dep.profile || SMOOTHSTEP).at;
    const gradient = !!depTo && depTo !== dep && depTo.target !== dep.target;
    const t0 = dep.target, tSpan = gradient ? depTo.target - t0 : 0;
    //: Arc-length deposition runs whenever the accumulator can run at all. At
    //: full flow `accumulating` is false at both ends, only the MAX floor
    //: applies, and none of this is reached.
    const byArcLength = dep.accumulating
        || (gradient && depTo.accumulating);
    const segLen = Math.sqrt(len2);
    const invLen = segLen > 0 ? 1 / segLen : 0;
    const rr = r * r;
    //: Everything the density is indexed by is in TIP RADII, so the table is
    //: independent of brush size and a pressure-driven radius needs no rebuild.
    const invR = 1 / r;
    //: `sweep` CANNOT ASSUME ITS CALLER SET `swept`. `depositionFor` builds the
    //: pass table only for a swept deposition, because a stamp train would pay
    //: 12 ms for a table it never reads -- but `sweep` is reachable with ANY
    //: deposition, and two probe suites called it with `swept: false` and hit a
    //: null table. Resolved once per segment rather than per pixel, and the
    //: module-level cache makes the fallback a map lookup after the first
    //: stroke at that hardness and flow.
    const passTbl = byArcLength
        ? (dep.passTable || passTableFor(dep.hardness, dep.target, dep.profile))
        : null;
    /*
     * THE PER-PIXEL BRANCH, and it is one block rather than two helpers on
     * purpose: computing the projection twice through two closures was 2.3x of
     * this branch's whole cost, measured by ablation against a build with the
     * transcendentals removed.
     *
     * ARC-LENGTH SHARE -- U3-R2F F2's second half, and the one the banding
     * actually turned on. `depositionFor` divides the stroke's flow by
     * `overlapK`, the EXPECTED number of contributions covering a pixel -- but
     * the actual number is an integer and `2r/gap` is not. At radius 27 and an
     * 8.1 px gap the count alternates between 7 and 8 against an expectation of
     * 6.67, and in the accumulating branch one extra contribution is one extra
     * multiplication. That is a ripple at exactly the segment rate, and it is
     * what the fixture measured: dominant period 8.0 px against a measured
     * 8.1 px gap, present at CONSTANT pressure where no staircase exists,
     * absent entirely at full flow where the accumulator does not run, and
     * growing from 0.84% at a 4 px gap to 9.38% at 16 px as the quantisation
     * coarsens.
     *
     * The same arithmetic made deposition depend on how the path was chopped:
     * the same geometric stroke at constant flow 0.5 read mean alpha 127.5 at a
     * 4 px gap and 145.5 at 16 px. §14 authorises the repair by name --
     * "normalise deposition by geometric distance/arc length rather than by the
     * number of browser events".
     *
     * So a segment is weighted by the arc length it actually contributes. A
     * pixel `dperp` from the stroke's axis is under a tip of radius `r` for a
     * travel of `2*sqrt(r^2 - dperp^2)`; this segment covers whatever part of
     * that window it overlaps. The shares of the segments covering a pixel sum
     * to 1 by construction, so the accumulator telescopes to `target * cov`
     * exactly -- which is what the MAX floor already says a full pass is worth,
     * and independent of where the boundaries fell.
     *
     * STRAIGHT-SEGMENT EXACT, CURVE-APPROXIMATE. Consecutive segments of a
     * curve are not collinear, so their windows do not tile the pass perfectly.
     * The residual is second order in the turn angle per segment and is far
     * smaller than the count quantisation it replaces.
     */
    const perPixel = gradient || byArcLength;
    for (let y = y0; y <= y1; y++) {
        const py = y + 0.5 - fy;
        for (let x = x0; x <= x1; x++) {
            //: M1a. Without a material every one of these is its constant and
            //: `mult` is exactly 1, so the plain brush is byte-identical.
            let rX = r, invRX = invR, rrX = rr, mult = 1;
            if (mat && mat.rough) {
                rX = r * materialEdge(mat, x, y);
                invRX = 1 / rX;
                rrX = rX * rX;
            }
            let cov = coverageAt(x, y, rX, dep.hardness, distance, dep.profile);
            if (cov <= 0) continue;
            if (mat) {
                //: The segment's own frame: `across` is the signed offset from
                //: its line, `along` the travel past `from`. Unclamped, as the
                //: study's disc is, so a cap continues the strands it ends.
                const mpx = x + 0.5 - fx;
                const along = segLen > 0 ? (mpx * vx + py * vy) * invLen : 0;
                const across = segLen > 0 ? (mpx * vy - py * vx) * invLen : 0;
                mult = materialAt(mat, across, matS + along);
                cov *= mult;
            }
            let target = dep.target;
            let accumulating = dep.accumulating;
            let weight = cov * dep.fEff;
            if (perPixel) {
                const px = x + 0.5 - fx;
                //: ONE projection, shared by the gradient and the share.
                const dot = px * vx + py * vy;
                if (gradient) {
                    //: Clamped exactly as the coverage clamps it, so a pixel
                    //: beyond an endpoint gets that endpoint's flow rather than
                    //: an extrapolated one, and the flow and the geometry
                    //: cannot disagree about where a pixel is.
                    let t = len2 > 0 ? dot / len2 : 0;
                    t = t < 0 ? 0 : (t > 1 ? 1 : t);
                    target = t0 + tSpan * t;
                    accumulating = target < NO_ACCUMULATE_ABOVE;
                }
                if (accumulating) {
                    const u = dot * invLen;
                    //: Distance to the INFINITE line. It is the perpendicular
                    //: offset the tip passes the pixel at, which is the first
                    //: argument the density needs.
                    const perp2 = px * px + py * py - u * u;
                    const perp = Math.sqrt(perp2 > 0 ? perp2 : 0);
                    //: How far along its own axis the tip is still within reach
                    //: of this pixel. Beyond it the density is zero, so the
                    //: integral is naturally bounded and no window floor is
                    //: needed -- the half-pixel fudge the share form required
                    //: is gone with it.
                    const reach = Math.sqrt(rrX - (perp2 > 0 ? perp2 : 0));
                    //: The interval of THIS segment, expressed as travel
                    //: relative to closest approach, clipped to reach.
                    let t0 = -u, t1 = segLen - u;
                    if (t0 < -reach) t0 = -reach;
                    if (t1 > reach) t1 = reach;
                    if (t1 > t0) {
                        const frac = passTau(passTbl, perp * invRX,
                                             t0 * invRX, t1 * invRX, dep.hardness);
                        //: Magnitude from the coverage at the PERPENDICULAR
                        //: distance, distribution from the table. A whole pass
                        //: is `frac == 1` summed over the segments that carry
                        //: it, and lands exactly on `target * shape(p)`.
                        //:
                        //: S. IT WAS `cov`, the coverage at the distance to
                        //: THIS SEGMENT -- clamped to its ends, so every
                        //: segment that did not contain the closest point
                        //: priced its share of the pass lower than the pass is.
                        //: The shares summed to one, the amounts did not: on a
                        //: straight soft stroke the accumulator fell 16 levels
                        //: short of the floor at spacing 0.15 and 30 at 0.015,
                        //: so the `peak` MAX decided the body -- and where a
                        //: stroke crossed itself that max of two arms was C2's
                        //: medial-axis crease again, worse the tighter the
                        //: spacing. Caps stay honest: past the end the chord is
                        //: truncated in `frac`, not in the magnitude.
                        const covPass = rX >= SUPERSAMPLE_BELOW_RADIUS
                            ? shapeAtFn(perp * invRX, dep.hardness)
                            : coverageAt(x, y, rX, dep.hardness, lineDistance, dep.profile);
                        //: M1a. `mult` prices the pass at the strand, exactly
                        //: as it scaled the floor above.
                        const tau = frac * negLogOf(target * covPass * mult);
                        //: Optical depth becomes a union weight. The product of
                        //: (1 - weight) over contributions is exp(-sum tau), so
                        //: the accumulator is integrating tau even though it
                        //: stores a union -- which is why no read site moves.
                        //: `EXPN` already tabulates exp(-z) over [0, EXPN_MAX]
                        //: with the same cap, so the transcendental this needs
                        //: is one the module has paid for since F2.
                        if (tau >= EXPN_MAX) {
                            weight = 1;
                        } else {
                            const zi = (tau / EXPN_MAX) * EXPN_N;
                            const z0 = zi | 0;
                            const zf = zi - z0;
                            weight = 1 - (EXPN[z0] + (EXPN[z0 + 1] - EXPN[z0]) * zf);
                        }
                    } else {
                        weight = 0;
                    }
                }
            }
            if (depositWith(buffer, y * buffer.width + x, cov,
                            target, weight, accumulating, dep.stipple,
                            dabIndex) > 0) {
                if (maxX < minX) { minX = maxX = x; minY = maxY = y; }
                else {
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }
            touched += 1;
        }
    }
    if (maxX >= minX) buffer.dirty.expand(minX, minY, maxX + 1, maxY + 1);
    buffer.contributions += 1;
    return touched;
}

/**
 * Which renderer a tip should use. §11.1, decided here rather than by a caller.
 *
 * A sweep is offered to any ROUND, UNTEXTURED, UNSCATTERED tip. Everything whose
 * character comes from its texture, scatter or anisotropy stamps, because that
 * is what produces the media quality a constant fill loses.
 *
 * U3-R REMOVED THE HARDNESS CONDITION, and that was the whole soft-brush
 * repair. It read `hardness >= 0.99`, quoting DiVerdi §2.6.2 on a swept contour
 * losing "the natural media quality" -- but that warning is about a swept
 * contour with a CONSTANT FILL, which is why textured and scattered tips are
 * excluded on their own line. `sweep` does not fill: it evaluates
 * `shapeAt(distanceToSegment / r)`, which for a round procedural tip IS the
 * continuous limit of an infinitely dense stamp train. There is no texture for
 * a soft round tip to lose.
 *
 * WHAT THE HARDNESS CONDITION COST. Stamping a soft tip at the shipped spacing
 * takes MAX of overlapping falloffs, and the max of two smoothsteps dips between
 * their centres. Measured on a straight 54 px stroke: 16.6% alpha ripple at
 * hardness 0.25 and beads reaching the CENTRELINE at hardness 0 -- owner-visible
 * as a beaded stroke. The swept form measures 0%, and its cross-section matches
 * the stamp train's to within 5 of 255, so the fix costs no fidelity.
 *
 * TWO REJECTED ALTERNATIVES, both measured on the same fixture:
 *
 *   enabling the union at Flow 1  fixed the ripple and DISTORTED the profile by
 *                                 up to 62 of 255 at hardness 0, flattening soft
 *                                 tips toward hard. Legacy carries the same
 *                                 short-circuit and its comment records the same
 *                                 finding from its own measurement.
 *   Legacy's spacing law          exact, and costs 2.5x to 8.3x the marks.
 *                                 Legacy pays it; V2 does not have to, because
 *                                 the sweep gets the same answer analytically.
 */
function rendererFor(tip) {
    const t = tip || {};
    if (t.textured || t.scatter) return RENDER_STAMP;
    //: U3-TF. ONE DEFINITION OF "IS THIS A CIRCLE", and it is the frame
    //: builder's. `tipFrame` returns null exactly when a tip needs no frame,
    //: so asking it here means kind, ratio and spikes cannot disagree with the
    //: renderer about what shape is being drawn. The nominal radius is large
    //: enough that the half-pixel floor inside `tipFrame` cannot round a genuine
    //: aspect back to circular -- at r = 1 a flat tip's 0.3 aspect floors to 0.5
    //: and reads round, which is the sort of thing that ships.
    if (tipFrame(1024, t) !== null) return RENDER_STAMP;
    //: Kept explicitly as well as implied, because the existing contract is
    //: stated in terms of `ratio` and a caller may pass it without a kind.
    if (t.ratio !== undefined && Math.abs(t.ratio - 1) >= 1e-6) return RENDER_STAMP;
    return RENDER_SWEEP;
}

/**
 * Merge intrinsic coverage into a target. Opacity ONCE, selection ONCE.
 *
 * `selection` is an optional Uint8Array of the same size, 0..255. It is
 * sampled here and NOWHERE ELSE: applying it per contribution multiplies, so a
 * 50% selection over N overlapping dabs yields 0.5^N and a worked area goes
 * black at the centre and vanishes at the edge.
 *
 * `erase` is the same footprint and the same coverage with a different target
 * operation (§12.5). Not a second renderer -- one flag, which is what makes
 * "every brush usable as an eraser" true by construction.
 *
 * BOUNDED, AND BYTE-IDENTICAL FOR IT. Outside the dirty region coverage is zero
 * by construction, and the unbounded loop's first act on a zero pixel was to
 * `continue`. So walking only the region changes what is VISITED and nothing
 * that is WRITTEN -- which is a property worth a test rather than a comment,
 * and has one.
 *
 * `targetAlpha` stays DOCUMENT-indexed: it belongs to the Canvas, not to this
 * buffer, and re-indexing someone else's buffer would be how the two drift.
 */
function merge(buffer, targetAlpha, options) {
    const o = options || {};
    const opacity = Math.min(1, Math.max(0,
        o.opacity === undefined ? 1 : o.opacity));
    const selection = o.selection || null;
    const erase = !!o.erase;
    const r = buffer.regionFor(o.region);
    if (r.isEmpty()) return targetAlpha;
    const w = buffer.width;
    const acc = buffer.acc, peak = buffer.peak;
    for (let y = r.y0; y < r.y1; y++) {
        const row = y * w;
        for (let x = r.x0; x < r.x1; x++) {
            const i = row + x;
            const built = acc[i] >> 8;
            const c = built > peak[i] ? built : peak[i];
            if (!c) continue;
            let a = c / 255;
            // ONCE. Both of them.
            a *= opacity;
            if (selection) a *= selection[i] / 255;
            if (a <= 0) continue;
            const prev = targetAlpha[i] / 255;
            const next = erase ? prev * (1 - a) : prev + (1 - prev) * a;
            targetAlpha[i] = Math.round(Math.min(1, Math.max(0, next)) * 255);
        }
    }
    buffer.visitedPixels += r.area();
    return targetAlpha;
}

/**
 * Total coverage over a buffer, for comparisons that need one number.
 *
 * One bounded pass and NO extraction. It used to call `read()`, which allocated
 * a document-sized array to add up a few thousand pixels -- and `paintedPixels`
 * did it again, and `merge` a third time.
 */
function totalCoverage(buffer, region) {
    const r = buffer.regionFor(region);
    if (r.isEmpty()) return 0;
    const w = buffer.width;
    const acc = buffer.acc, peak = buffer.peak;
    let sum = 0;
    for (let y = r.y0; y < r.y1; y++) {
        const row = y * w;
        for (let x = r.x0; x < r.x1; x++) {
            const i = row + x;
            const built = acc[i] >> 8;
            sum += built > peak[i] ? built : peak[i];
        }
    }
    buffer.visitedPixels += r.area();
    return sum;
}

/** How many pixels carry any coverage at all. One bounded pass, no extraction. */
function paintedPixels(buffer, region) {
    const r = buffer.regionFor(region);
    if (r.isEmpty()) return 0;
    const w = buffer.width;
    const acc = buffer.acc, peak = buffer.peak;
    let n = 0;
    for (let y = r.y0; y < r.y1; y++) {
        const row = y * w;
        for (let x = r.x0; x < r.x1; x++) {
            const i = row + x;
            const built = acc[i] >> 8;
            if (built > peak[i] ? built : peak[i]) n += 1;
        }
    }
    buffer.visitedPixels += r.area();
    return n;
}

window.StudioBrushCoverageV2 = {
    COVERAGE_API_VERSION: COVERAGE_API_VERSION,
    NO_ACCUMULATE_ABOVE: NO_ACCUMULATE_ABOVE,
    RENDER_STAMP: RENDER_STAMP,
    RENDER_SWEEP: RENDER_SWEEP,
    shapeAt: shapeAt,
    //: U3-G. Exported so a probe can compare the ported bell against
    //: Legacy's `dabAlphaGauss` directly, rather than inferring the
    //: profile from painted pixels.
    shapeAtGauss: shapeAtGauss,
    //: U3-J. The adapter draws its per-dab jitter from the same hash.
    hash01: hash01,
    //: M1a. The adapter decides the material bypass at the same threshold the
    //: stipple uses, read from here rather than restated.
    STIPPLE_BELOW: STIPPLE_BELOW,
    //: M1a. Exposed for evidence and tests; the renderers call them directly.
    materialAt: materialAt,
    materialEdge: materialEdge,
    latticeNoise: latticeNoise,
    profileFor: profileFor,
    profileMean: profileMean,
    DirtyRegion: DirtyRegion,
    CoverageBuffer: CoverageBuffer,
    depositionFor: depositionFor,
    stamp: stamp,
    sweep: sweep,
    rendererFor: rendererFor,
    TIP_ASPECT: TIP_ASPECT,
    TIP_NORM: TIP_NORM,
    TIP_EXTENT: TIP_EXTENT,
    tipFrame: tipFrame,
    alongExtent: alongExtent,
    foldSpikes: foldSpikes,
    merge: merge,
    totalCoverage: totalCoverage,
    paintedPixels: paintedPixels,
};

})();
