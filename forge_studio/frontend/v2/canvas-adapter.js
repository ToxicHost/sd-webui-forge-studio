/**
 * Forge Studio — Brush Engine V2 → Canvas raster-layer adapter (U3)
 * by ToxicHost & Moritz
 *
 * The first slice where V2 paints on a real document. Internal only: the
 * shipping default is Legacy until U3's acceptance is complete, and there is no
 * public engine choice anywhere in this file.
 *
 * THE SEAM IS DELIBERATELY NARROW, and the Canvas integration gate argued for
 * exactly this shape: Legacy's stroke slot ALREADY IS a coverage sink with the
 * properties V2 needs. `S.stroke.alphaMap` is a document-sized coverage buffer;
 * `S.stroke.dirty` and `S.stroke.frameDirty` are its bounds; `commitStroke`
 * applies selection exactly once, writes the layer, takes one undo record,
 * bumps one revision and publishes the dirty region to U2's presentation
 * contract.
 *
 * So V2 does not get its own compositor, document store or transaction. It
 * produces coverage; the Canvas owns everything else. Concretely:
 *
 *     pointer event -> V2 normalize -> V2 filter -> V2 arc-length sampler
 *                   -> V2 dynamics  -> V2 coverage
 *                   -> TRANSFER into S.stroke.alphaMap + Canvas dirty bounds
 *                   -> (unchanged) preview, commit, undo, revision, present
 *
 * WHAT THAT BUYS, and why it is worth stating: every property the earlier units
 * proved keeps holding without being re-implemented. Selection once (BE4).
 * One undo and one revision per contact. E0's layer-isolated eraser preview.
 * U2's bounded presentation. The Canvas2D fallback. None of it is re-derived
 * here, because none of it is re-implemented here.
 *
 * TWO CONVENTIONS MEET IN THIS FILE AND THEY DISAGREE. V2's `DirtyRegion` is
 * HALF-OPEN (`x1` exclusive); Legacy's `S.stroke.dirty` is INCLUSIVE (`x1` is
 * the last painted column). Every conversion is spelled out at its site rather
 * than folded into a helper, because an off-by-one here is a missing rim of
 * pixels that only shows up as a hairline seam at a region edge.
 *
 * Review: `Evidence/source-review/U3-canvas-adapter.md`.
 */

(function () {
"use strict";

const I = window.StudioBrushInputV2;
const F = window.StudioBrushFiltersV2;
const SAMP = window.StudioBrushSamplerV2;
const D = window.StudioBrushDynamicsV2;
const V = window.StudioBrushCoverageV2;

for (const [name, mod] of [["input", I], ["filters", F], ["sampler", SAMP],
                           ["dynamics", D], ["coverage", V]]) {
    if (!mod) throw new Error("v2/canvas-adapter.js requires v2/" + name + ".js");
}

//: INTERNAL. Not persisted, not in settings, not in the DOM. §14: the flag must
//: be reversible without touching saved brush preferences, so it deliberately
//: has no storage at all -- it resets to Legacy on reload, which is the safe
//: direction for a migration switch.
let _enabled = false;

//: Named refusals. A combination this slice does not support must FAIL LOUDLY
//: to Legacy rather than silently lose the stroke, and the reason has to be
//: readable afterwards.
const REFUSE_FLAG_OFF = "v2-flag-off";
const REFUSE_TOOL = "tool-is-not-brush-or-eraser";
const REFUSE_NO_LAYER = "no-active-layer";
const REFUSE_NOT_RASTER = "target-is-not-a-raster-layer";
const REFUSE_HIDDEN = "target-layer-is-hidden";
const REFUSE_LOCKED = "target-layer-is-locked";
const REFUSE_MASK_MODE = "mask-and-region-targets-are-not-in-this-slice";
const REFUSE_TOUCH = "touch-is-not-a-paint-contact";

//: One contact's frozen state. Everything the stroke needs is captured at
//: `begin` and never re-read, because re-resolving mid-stroke is how a stroke
//: ends up half on one layer and half on another.
let _stroke = null;

const _stats = {
    contacts: 0, refusals: [], samples: 0, marks: 0,
    transfers: 0, transferredPixels: 0, lastRefusal: null,
};

function _note(reason) {
    _stats.lastRefusal = reason;
    _stats.refusals.push(reason);
    if (_stats.refusals.length > 24) _stats.refusals.shift();
    return reason;
}

// ───────────────────────────────────────────────────────── target resolution

/**
 * May V2 take this contact?
 *
 * Resolved ONCE, at `begin`. Returns a reason string when it refuses so the
 * caller can fall back to Legacy and the refusal can be read back in evidence.
 */
function refusalFor(S, event) {
    if (!_enabled) return REFUSE_FLAG_OFF;
    if (S.tool !== "brush" && S.tool !== "eraser") return REFUSE_TOOL;
    // §17: a touch must not paint merely because nobody made a routing
    // decision. Pen and mouse only in this slice; the finger/pen product
    // decision is BE8's and is not pre-empted here.
    if (event && event.pointerType === "touch") return REFUSE_TOUCH;
    if (S.editingMask || S.regionMode) return REFUSE_MASK_MODE;
    const layers = S.layers || [];
    const L = layers[S.activeLayerIdx];
    if (!L) return REFUSE_NO_LAYER;
    if (L.type && L.type !== "paint") return REFUSE_NOT_RASTER;
    if (!L.canvas || !L.ctx) return REFUSE_NOT_RASTER;
    if (L.visible === false) return REFUSE_HIDDEN;
    if (L.locked) return REFUSE_LOCKED;
    return null;
}

// ───────────────────────────────────────────────────────── settings mapping

/**
 * The Legacy controls whose V2 meaning is PROVEN, and nothing else.
 *
 * §18 is explicit: a field without a proven mapping is omitted or refused, not
 * guessed. So Size, Opacity, Flow, Hardness and Spacing map across because
 * their semantics were established in V2-04/V2-05 against the same
 * definitions; everything V2-only takes a recorded internal default.
 */
//: The four values Legacy's `pressureAffects` is documented to take. An
//: unrecognised one must not become "width" by accident -- that is the exact
//: failure R2 exists to remove -- so it is named and falls back to "none",
//: which changes nothing rather than changing the wrong thing.
const PRESSURE_TARGETS = ["none", "size", "opacity", "both"];

//: How thin the lightest touch goes, as a fraction of full width.
//:
//: 0.35 is V2-04/05's choice. Legacy's is 0.10 (`pSz` clamps pressure at
//: `Math.max(0.1, p)` and scales linearly), so V2's lightest stroke is more
//: than three times as wide as the engine it is compared against -- which is
//: what the owner reported as "not thin enough at the lightest pressure".
//:
//: Adjustable ONLY through the internal setter below, which exists so the
//: number can be chosen by painting rather than argued about. It is not a
//: settings surface: no UI reads it, nothing persists it, and a reload returns
//: to this default. Same category as the V2 flag itself, and tested the same
//: way.
const DEFAULT_PRESSURE_WIDTH_FLOOR = 0.35;
let _pressureWidthFloor = DEFAULT_PRESSURE_WIDTH_FLOOR;

function normalisedPressureTarget(S) {
    const raw = S && typeof S.pressureAffects === "string"
        ? S.pressureAffects : "none";
    return PRESSURE_TARGETS.indexOf(raw) >= 0 ? raw : "none";
}

/**
 * Which dimensions pressure drives, resolved once from the owner's two
 * settings. `pressureSensitivity` is the master switch and `pressureAffects`
 * chooses the target; neither alone is enough to answer the question.
 */
function pressureTargets(S) {
    if (!S || !S.pressureSensitivity) return { width: false, flow: false };
    const target = normalisedPressureTarget(S);
    return {
        width: target === "size" || target === "both",
        flow: target === "opacity" || target === "both",
    };
}

/** One dynamics number, defaulting to 0 rather than to "absent". */
function _dyn(S, name) {
    const d = S && S.brushDynamics;
    const v = d && d[name];
    return typeof v === "number" && v > 0 ? v : 0;
}

function describeStroke(S, C) {
    const sizePx = Math.max(1, C.brushPx ? C.brushPx() : 16);
    const spacing = (typeof S.brushSpacing === "number" && S.brushSpacing > 0)
        ? S.brushSpacing : 0.15;
    return {
        sizePx: sizePx,
        spacingFraction: spacing,
        hardness: typeof S.brushHardness === "number" ? S.brushHardness : 0.5,
        flow: typeof S.brushFlow === "number" ? S.brushFlow : 1,
        opacity: typeof S.brushOpacity === "number" ? S.brushOpacity : 1,
        buildup: !!S.brushBuildup,
        erase: S.tool === "eraser",
        //: U3-TF. THE TIP'S SHAPE, read from the owner's state instead of
        //: assumed. `describeStroke` used to forward eleven fields and hard-code
        //: the rest, so fifteen of the sixteen shipping presets reached the
        //: engine as something other than what they declare -- and every tip
        //: rendered round, because the KIND was dropped. Flat Chisel declares
        //: `ratio: 1.0`; its flatness is entirely `TIP_ASPECT.flat`, reached
        //: through the kind.
        tipKind: typeof S.brushPreset === "string" ? S.brushPreset : "round",
        ratio: typeof S.brushRatio === "number" ? S.brushRatio : 1,
        //: DEGREES here, radians at the kernel. `canvas-core.js:205` calls it
        //: "0-360 degrees, offset from the stroke direction" and `_brushAngleRad`
        //: is the only place Legacy converts.
        angleDeg: typeof S.brushAngle === "number" ? S.brushAngle : 0,
        spikes: typeof S.brushSpikes === "number" ? S.brushSpikes : 2,
        //: FOLLOW-STROKE IS A DYNAMIC, NOT A KIND, and reading it off the kind
        //: was wrong in a way the owner saw immediately: flat tips rotated at
        //: every stamp.
        //:
        //: The first version took `canvas-core.js:2381`, which resolves
        //: `(preset === "flat" ? strokeHeading() : 0)`. That is not the live
        //: path. `dabRotation` (`canvas-core.js:3438`) is, and it reads
        //: `S.brushDynamics.followStroke` -- so CALLIGRAPHY, whose own
        //: description is "A held nib. Angle is fixed, so width follows
        //: direction", declares `followStroke: false` and must be held at a
        //: fixed 45 degrees. Keying off `preset === "flat"` rotated it with
        //: every mark instead. Scatter Dust and Pixel Perfect declare it false
        //: too; they are round, so it could not show there.
        tipFollowsStroke: !!(S.brushDynamics && S.brushDynamics.followStroke),
        //: WAS THE LITERAL 1, so four presets that ask for a lighter dab train
        //: got a full one. U3-D: density no longer scales `overlapK` at all --
        //: it is a per-pixel stipple, which is what Legacy spends it on.
        density: typeof S.brushDensity === "number" ? S.brushDensity : 1,
        //: U3-G. NOT FORWARDED AT ALL until now, so Airbrush, Ink Wash and
        //: Charcoal asked for a gaussian bell and every one of them was drawn
        //: with the smoothstep. The kernel resolves the name; an unknown one
        //: falls back to the smoothstep rather than throwing at paint time.
        falloff: typeof S.brushFalloff === "string" ? S.brushFalloff : "default",
        //: U3-J. THE FOUR PER-DAB JITTERS, none of which V2 forwarded or
        //: applied. `dynamics.js` models curve-driven rules -- pressure to
        //: size, speed to flow -- which is a different thing from randomness
        //: per dab, so there was nowhere for these to land.
        jitter: {
            size: _dyn(S, "sizeJitter"),
            opacity: _dyn(S, "opacityJitter"),
            rotation: _dyn(S, "rotationJitter"),
            scatter: _dyn(S, "scatter"),
        },
        //: WAS HARD-CODED `MODE_NATURAL`, so a preset asking for NO smoothing
        //: got smoothed. Pixel Perfect declares `smoothing: 0` and is the one
        //: preset for which that is the whole point. The bundle's product
        //: principles say smoothing is user-controlled and Studio must never
        //: secretly reshape a stroke; this is the line that makes that true.
        //:
        //: MODE_STABILIZED has no owner control to reach it. Recorded as a
        //: BR-03 gap rather than invented: Legacy exposes one stabiliser with a
        //: STRENGTH, not a three-way mode, and choosing a level at which
        //: "Natural" becomes "Stabilized" would be a product decision.
        smoothingLevel: typeof S.smoothing === "number" ? S.smoothing : 0,
        smoothingMode: (typeof S.smoothing === "number" && S.smoothing <= 0)
            ? F.MODE_RAW : F.MODE_NATURAL,
        //: Screen pixels per document pixel. `filters.js:102` computes the
        //: window as `strength * SCREEN_PX_PER_STEP / scale`, the same form as
        //: Legacy's stabiliser -- and the adapter was passing the literal 1, so
        //: the window was wrong at every zoom but 100%.
        zoomScale: (S.zoom && typeof S.zoom.scale === "number" && S.zoom.scale > 0)
            ? S.zoom.scale : 1,
        //: U3-R R2. TWO INDEPENDENT DIMENSIONS, NOT ONE BOOLEAN.
        //:
        //: This read only `S.pressureSensitivity` and mapped it to width, which
        //: meant an owner who chose `opacity` silently got width instead -- the
        //: one case where the setting does the wrong thing rather than nothing.
        //:
        //: Legacy resolves the same choice in `pSz` and `pOp`, and `pOp`'s own
        //: comment settles what the `opacity` target MEANS for a brush: the
        //: per-stamp value is FLOW, an accumulation rate, because the stroke is
        //: composited at `S.brushOpacity` once at commit and using opacity per
        //: stamp would apply it twice. V2's merge applies opacity once for the
        //: same reason, so the same mapping holds here.
        pressureToWidth: pressureTargets(S).width,
        pressureToFlow: pressureTargets(S).flow,
        //: Recorded so a reader can see which owner setting produced the two
        //: flags above, rather than having to reconstruct it.
        pressureSensitivity: !!S.pressureSensitivity,
        pressureAffects: normalisedPressureTarget(S),
        //: Frozen with everything else, so a mid-stroke change cannot alter
        //: the contact in flight.
        pressureWidthFloor: _pressureWidthFloor,
    };
}

// ───────────────────────────────────────────────────────── the transfer

/**
 * Move V2's newly-covered pixels into the Canvas's own stroke buffer.
 *
 * BOUNDED BY CONSTRUCTION: only the region V2 reports as changed since the last
 * transfer is touched, and `takeDirty` resets it so the next frame starts from
 * nothing. Transferring the accumulated region instead would re-copy the whole
 * stroke on every move, which is precisely the cost U2 removed from Legacy.
 *
 * ASSIGNMENT, NOT MAX. V2's buffer is authoritative for the entire stroke -- it
 * is the only producer -- so its value IS the coverage. Blending with whatever
 * happened to be in the alpha map would mix two accumulation models.
 */
function transfer(S) {
    const st = _stroke;
    if (!st || !S.stroke || !S.stroke.alphaMap) return 0;
    const region = st.buffer.takeDirty();
    if (region.isEmpty()) return 0;

    const view = st.buffer.readRegion(region);
    const map = S.stroke.alphaMap;
    const W = S.W;
    for (let vy = 0; vy < view.height; vy++) {
        const row = (view.y0 + vy) * W;
        const vrow = vy * view.width;
        for (let vx = 0; vx < view.width; vx++) {
            map[row + view.x0 + vx] = view.data[vrow + vx];
        }
    }

    // HALF-OPEN IN, INCLUSIVE OUT. V2's `x1` is one past the last column;
    // Legacy's is the last column. Spelled out at the site.
    const ix0 = view.x0, iy0 = view.y0;
    const ix1 = view.x0 + view.width - 1, iy1 = view.y0 + view.height - 1;
    for (const d of [S.stroke.dirty, S.stroke.frameDirty]) {
        if (!d) continue;
        if (ix0 < d.x0) d.x0 = ix0;
        if (iy0 < d.y0) d.y0 = iy0;
        if (ix1 > d.x1) d.x1 = ix1;
        if (iy1 > d.y1) d.y1 = iy1;
    }
    _stats.transfers += 1;
    _stats.transferredPixels += view.width * view.height;
    return view.width * view.height;
}

// ───────────────────────────────────────────────────────── the contact

/**
 * V2 TAKES SOLE OWNERSHIP OF THE STROKE BUFFER.
 *
 * `beginStroke` has already run by the time the adapter is offered the contact
 * -- deliberately, so the Canvas's transaction, undo record and locked commit
 * target are all in place. But it also leaves Legacy coverage behind: either a
 * dab already stamped into the alpha map, or a DEFERRED one in `_openingDab`
 * that `commitStroke` flushes at the end (BE16), plus a possible pixel-perfect
 * cell in `_ppPrev`.
 *
 * With two producers writing one alpha map the result is a V2 stroke with a
 * Legacy dab welded to its start -- and the deferred case lands AFTER every V2
 * mark, so it survives the transfer and is the harder one to see.
 *
 * Clearing is bounded: only the rectangle Legacy reported dirty, which is one
 * dab at most.
 */
function _takeOwnership(S) {
    const st = S.stroke;
    if (!st) return;
    // The deferred opening dab and the pixel-perfect cell, before they can be
    // flushed into a stroke that is no longer Legacy's.
    st._openingDab = null;
    st._ppPrev = null;
    st._ppPrev2 = null;
    const d = st.dirty;
    if (st.alphaMap && d && d.x1 >= d.x0 && d.y1 >= d.y0) {
        const x0 = Math.max(0, d.x0), y0 = Math.max(0, d.y0);
        const x1 = Math.min(S.W - 1, d.x1), y1 = Math.min(S.H - 1, d.y1);
        for (let y = y0; y <= y1; y++) {
            st.alphaMap.fill(0, y * S.W + x0, y * S.W + x1 + 1);
        }
    }
    st.dirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
    st.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
}

function begin(S, C, event, toDoc) {
    const refusal = refusalFor(S, event);
    if (refusal) { _stroke = null; return _note(refusal); }

    const spec = describeStroke(S, C);
    const radius = spec.sizePx / 2;
    // U3-R. THE RENDERER IS CHOSEN FIRST, because the deposition depends on it.
    //
    // `overlapK` counts contributions covering one pixel, and a swept segment
    // covers a pixel at the same profile value every time where a stamp train
    // covers it at a different one each time. Building the deposition without
    // knowing which renderer will run made a swept Flow-0.5 stroke deposit 13%
    // too much paint. The two cannot be resolved independently.
    //: U3-TF. THE REAL TIP, not three literals. `rendererFor` asks `tipFrame`
    //: whether the shape is a circle, so a flat, marker or spiked tip routes to
    //: the stamp renderer that can express it. A round tip is unchanged and
    //: keeps the analytic sweep, which is what C2's repair lives in.
    //:
    //: `textured` stays false: grain is absent from the V2 kernel entirely and
    //: claiming it here would route tips to a renderer that cannot deliver it.
    //: U3-D. ONE SEED PER STROKE, and the same seed for every mark of it -- the
    //: stipple mask must not change under a brush that is still down. A counter
    //: rather than `Math.random()` so a session replays identically and a test
    //: painting the same stroke from a fresh module gets the same pixels.
    const strokeSeed = _nextStippleSeed();
    const renderer = V.rendererFor({
        hardness: spec.hardness, textured: false,
        scatter: spec.tipKind === "scatter",
        kind: spec.tipKind, ratio: spec.ratio, spikes: spec.spikes,
    });
    const dep = V.depositionFor({
        hardness: spec.hardness, flow: spec.flow, opacity: spec.opacity,
        density: spec.density, buildup: spec.buildup, seed: strokeSeed,
        falloff: spec.falloff,
        step: spec.spacingFraction * 2,
        swept: renderer === V.RENDER_SWEEP,
        tipKind: spec.tipKind, ratio: spec.ratio, spikes: spec.spikes,
        angle: (spec.angleDeg * Math.PI) / 180,
    });

    _stroke = {
        // FROZEN AT BEGIN. §19.9/§19.10: changing size, colour or preset during
        // a contact must not alter it, and the target cannot be redirected.
        spec: spec,
        dep: dep,
        //: Read by `_depositionForMark`, which rebuilds a deposition per flow
        //: bucket. Without this every pressure step would re-seed the mask and
        //: a pressure-varying stroke would stipple through a different mask at
        //: each bucket -- a shimmer, not a texture.
        stippleSeed: strokeSeed,
        //: U3-J. Counted across the whole contact, not per batch.
        markIndex: 0,
        radius: radius,
        targetIndex: S.activeLayerIdx,
        targetLayer: S.layers[S.activeLayerIdx],
        input: new I.StrokeInput({ mousePressure: undefined }),
        filter: new F.StrokeFilter({
            mode: spec.smoothingMode,
            //: The owner's level, which the filter turns into an ARC LENGTH.
            //: Omitted before, so every preset smoothed at the same default
            //: whatever it declared -- Fine Liner asks for 8 and Scatter Dust
            //: for 1, and both got the same window.
            strength: spec.smoothingLevel,
            scale: spec.zoomScale,
            //: No owner control reaches this. Left false and recorded as the
            //: BR-04 gap rather than invented.
            preserveCorners: false,
        }),
        sampler: new SAMP.ArcSampler({
            spacingFraction: spec.spacingFraction, sizePx: spec.sizePx,
        }),
        buffer: new V.CoverageBuffer(S.W, S.H),
        //: U3-R R2. One rule per dimension the owner actually selected, so
        //: `both` drives both and `opacity` drives flow ALONE rather than
        //: quietly driving width.
        rules: (function () {
            const rules = [];
            if (spec.pressureToWidth) {
                rules.push(D.ruleFromFeel("size", "pressure", "balanced",
                                          { min: spec.pressureWidthFloor,
                                            max: 1.0, fallback: 1 }));
            }
            if (spec.pressureToFlow) {
                //: Same shape and same floor as the width rule. Legacy clamps
                //: pressure at 0.1 and scales linearly; V2-04/05 chose 0.35 as
                //: the floor so a light touch still marks, and §16 says to keep
                //: the existing curve semantics unless source requires
                //: otherwise. Using one floor for both dimensions also means
                //: `both` cannot drift into two different feels.
                rules.push(D.ruleFromFeel("flow", "pressure", "balanced",
                                          { min: 0.35, max: 1.0, fallback: 1 }));
            }
            return rules.length ? rules : null;
        })(),
        //: Depositions keyed by quantised flow multiplier. `deposit` needs
        //: `target` and `fEff`, both of which move with flow, so a
        //: pressure-driven flow needs a deposition per distinct value -- and
        //: building one per MARK would allocate on the hot path. 64 buckets is
        //: finer than an 8-bit result can show and bounds the table.
        depCache: null,
        renderer: renderer,
        lastMark: null,
        //: U3-R2F F2. The deposition the PREVIOUS mark was laid down with, so
        //: a swept segment can be given both of its ends. Null until the first
        //: mark, which is why the opening dab stamps rather than sweeps.
        lastDep: null,
        began: false,
        samples: 0,
        marks: 0,
    };
    _stats.contacts += 1;

    _takeOwnership(S);

    // THE CONTACT POINT IS A SAMPLE, and §17 requires it represented exactly.
    // Feeding it here is what makes the stroke start where the pointer went
    // down: without it V2's first mark is the first MOVE, the opening dab is
    // missing, and the only reason the canvas looks nearly right is Legacy's
    // own opening dab -- which this adapter has just removed.
    if (event && toDoc) addFromEvent(S, event, toDoc);
    return null;
}

//: Buckets across 0..1.
//:
//: U3-R2F F2 RAISED THIS FROM 64, and the old comment's claim -- "finer than an
//: 8-bit coverage result can express, so quantising here cannot be seen" -- was
//: wrong. 64 buckets is a target resolution of 1/64, which is 4 of 255 in
//: alpha, and R2 could not see it only because every segment was flat anyway.
//: Once the segment carries a gradient the buckets ARE the remaining staircase:
//: measured on the ramp fixture, detrended banding was 2.26% at 64 and 1.11% at
//: 256, and flat beyond that.
//:
//: 256 rather than 1024, also measured: at 1024 a nominally constant-pressure
//: stroke went from 0% to 0.54%, because the sampler's own interpolation makes
//: `dyn.flow` vary in the last few digits and the coarser bucket was absorbing
//: it. 256 is the point where the ramp is fixed and the flat stroke is still
//: flat.
//:
//: The table is bounded by the MARK COUNT, not by this number -- entries are
//: built lazily and a stroke can only ask for as many as it has marks.
const FLOW_BUCKETS = 256;

/*
 * U3-D. THE STIPPLE SEED.
 *
 * Each stroke gets its own, so two strokes over the same pixels do not punch
 * the same holes and a second pass genuinely darkens. The odd multiplier keeps
 * consecutive seeds far apart in the hash's input space; consecutive integers
 * would be mixed well anyway by Murmur's finalizer, but a stroke counter is the
 * one input an owner can trivially make sequential.
 */
let _stippleSeedCounter = 0;
function _nextStippleSeed() {
    _stippleSeedCounter = (_stippleSeedCounter + 0x9e3779b1) | 0;
    return _stippleSeedCounter;
}

/*
 * U3-J. PER-DAB JITTER.
 *
 * Four controls, eight presets, and V2 had none of them: `dynamics.js` models
 * curve-driven rules (pressure to size, speed to flow), which is a different
 * thing from per-dab randomness.
 *
 *     sizeJitter      5 presets   Scatter Dust 0.3, Charcoal 0.12, ...
 *     opacityJitter   6 presets   Scatter Dust 0.2, Sketch Light 0.15, ...
 *     rotationJitter  1 preset    Scatter Dust 0.5
 *     scatter         3 presets   Scatter Dust 0.5, Airbrush 0.1, Pastel 0.05
 *
 * Legacy's semantics, ported exactly (`canvas-core.js:2914, 2915, 2921, 3115`):
 *
 *     size      sz *= 1 + u * sizeJitter,        floored at 1 px
 *     opacity   op *= 1 + u * opacityJitter,     clamped to [0.01, 1]
 *     rotation  ang += u * PI * rotationJitter
 *     scatter   offset PERPENDICULAR to travel by u * brushPx * scatter
 *
 * where `u` is uniform on [-1, 1]. Legacy draws all four from `Math.random()`;
 * these are drawn from the shared hash, for the reasons U3-D records -- a
 * stroke that cannot be painted twice cannot be measured, and `brushGrain`'s
 * comment has the receipt for what that costs.
 *
 * FOUR INDEPENDENT CHANNELS. One draw per dab reused across all four would
 * correlate them: every big dab would also be the most opaque and the most
 * displaced, which reads as a pulse rather than as noise. The salts are
 * arbitrary odd constants; only their distinctness matters.
 */
const NO_JITTER = Object.freeze({ size: 0, opacity: 0, rotation: 0, scatter: 0 });
const JITTER_SIZE = 0x2545f491;
const JITTER_OPACITY = 0x9e3779b9;
const JITTER_ROTATION = 0x85ebca6b;
const JITTER_SCATTER = 0xc2b2ae35;

/** Uniform on [-1, 1] for one dab of one stroke on one channel. */
function _jitter(seed, markIndex, salt) {
    return V.hash01(markIndex, (seed ^ salt) | 0) * 2 - 1;
}

/**
 * The deposition for one mark, given the flow multiplier pressure produced.
 *
 * WITHOUT PRESSURE ON FLOW THIS IS THE FROZEN ONE, unchanged and unallocated --
 * which keeps every existing stroke byte-identical and keeps the common path
 * free of the cache entirely.
 *
 * With it, `target` and `fEff` both move with flow, so `deposit` genuinely
 * needs a different deposition per distinct value. Building one per mark would
 * allocate on the hot path, so they are cached by bucket: at most 64 objects
 * per contact, built lazily, and a steady hand reuses one.
 */
function _depositionForMark(st, flowMultiplier) {
    //: U3-J. OPACITY JITTER IS A SECOND REASON TO VARY FLOW PER MARK.
    //:
    //: This returned the frozen deposition whenever pressure did not drive
    //: flow, which was right when pressure was the only thing that could vary
    //: it. With a jitter it silently discarded the multiplier, and Sketch Light
    //: (0.15), Scatter Dust (0.2), Pencil and Charcoal (0.1) all painted
    //: exactly as if the control were zero -- measured, total alpha identical
    //: to the baseline down to the byte.
    if (!st.spec.pressureToFlow
        && !(st.spec.jitter && st.spec.jitter.opacity > 0)) return st.dep;
    const m = flowMultiplier === undefined || flowMultiplier === null
        ? 1 : flowMultiplier;
    const bucket = Math.max(0, Math.min(FLOW_BUCKETS,
                                        Math.round(m * FLOW_BUCKETS)));
    if (!st.depCache) st.depCache = new Map();
    let dep = st.depCache.get(bucket);
    if (dep) return dep;
    const spec = st.spec;
    dep = V.depositionFor({
        hardness: spec.hardness,
        flow: spec.flow * (bucket / FLOW_BUCKETS),
        opacity: spec.opacity, density: spec.density, buildup: spec.buildup,
        seed: st.stippleSeed, falloff: spec.falloff,
        step: spec.spacingFraction * 2,
        swept: st.renderer === V.RENDER_SWEEP,
        //: THE TIP, AND IT WAS NOT HERE. `depositionFor` builds
        //: `dep.tip` from these four and DEFAULTS THEM TO A ROUND DAB
        //: when they are absent (`coverage.js`: kind "round", ratio 1,
        //: spikes 2) -- so a rebuilt deposition silently discarded the
        //: geometry `begin` had resolved, and `_placeMarks` hands this
        //: object straight to `stamp`.
        //:
        //: U3-TF added the four fields to `begin` and not to this
        //: rebuild, which made it reachable for any preset driving
        //: pressure to flow. U3-J then widened the gate above to
        //: `|| jitter.opacity > 0`, and THAT is what reaches Bristle
        //: Rake on a mouse: `pressureTargets` returns flow:false whenever
        //: pressure sensitivity is off, so with a mouse the jitter clause
        //: is the only way in, and Bristle Rake is the one preset that
        //: declares both an opacity jitter and a shaped tip.
        //:
        //: Measured on the shipped declaration (flat, ratio 0.55, spikes
        //: 6) at radius 60: with the tip 4,528 px at aspect 0.881,
        //: without it 11,112 px at aspect 1.000 -- 2.45x the ink, as a
        //: plain disc. The owner's own 2026-08-28 gallery run recorded
        //: the same thing from the other side: a V2 Bristle Rake tap at
        //: aspect 1.0000 for a preset whose whole identity is a splayed
        //: flat nib.
        tipKind: spec.tipKind, ratio: spec.ratio, spikes: spec.spikes,
        angle: (spec.angleDeg * Math.PI) / 180,
    });
    st.depCache.set(bucket, dep);
    return dep;
}

function _placeMarks(S, dabs) {
    const st = _stroke;
    for (let i = 0; i < dabs.length; i++) {
        const mark = dabs[i];
        const ctx = {
            pressure: mark.pressure,
            pressureAvailable: true,
            tiltXDeg: mark.tiltXDeg, tiltYDeg: mark.tiltYDeg,
            tiltAvailable: false,
            headingRad: mark.headingRad,
            speed: st.lastMark ? D.segmentSpeed(st.lastMark, mark) : null,
        };
        const dyn = st.rules ? D.evaluate(st.rules, ctx) : D.NEUTRAL;
        //: U3-J. THE DAB INDEX IS THE DRAW'S ADDRESS. Counted on the
        //: stroke rather than taken from `i`, because `_placeMarks` is
        //: called once per batch of dabs and `i` restarts at zero every
        //: time -- which would make every batch jitter identically and
        //: read as a repeating pattern at the pointer-event rate.
        const j = st.markIndex++;
        //: A spec built by a caller that predates U3-J has no `jitter`
        //: at all. Absent must mean OFF, not a throw at paint time.
        const jit = st.spec.jitter || NO_JITTER;
        let r = Math.max(0.5, st.radius * dyn.size);
        if (jit.size > 0) {
            //: Legacy floors at 1 PIXEL, not at a fraction of the
            //: radius (`canvas-core.js:2914`).
            r = Math.max(1, r * (1 + _jitter(st.stippleSeed, j,
                                             JITTER_SIZE) * jit.size));
        }
        //: U3-TF. A FOLLOWING TIP GETS ITS ANGLE PER MARK. Only a flat follows
        //: the stroke -- every other kind uses the owner's offset alone, which
        //: is already frozen on the deposition -- so this stays undefined for
        //: them and `stamp` takes the frozen shape unchanged.
        //:
        //: Passed as an ARGUMENT because the sampler's marks are frozen.
        //:
        //: NO HEADING MEANS NO OVERRIDE. The sampler places the opening mark
        //: with a null heading (`sampler.js:124`) because a stroke has no
        //: direction until the pointer moves. Legacy DEFERS that dab entirely
        //: rather than guess -- `canvas-core.js:3444` is a full essay on why a
        //: dab cannot be painted and corrected. V2 paints it immediately, so the
        //: honest fallback is the contact's own frozen angle: the owner's offset
        //: alone, which is what a held nib would have used anyway. Substituting
        //: 0 for the heading was a silent guess that pointed the first dab of
        //: every following tip the wrong way.
        let tipAngle;
        if (st.spec.tipFollowsStroke && typeof mark.headingRad === "number") {
            tipAngle = mark.headingRad + (st.spec.angleDeg * Math.PI) / 180;
        }
        //: U3-J. ROTATION JITTER IS A FOURTH TERM, added the way Legacy adds it
        //: (`canvas-core.js:2921`): `+= u * PI * rotationJitter`. It has to
        //: start from the frozen angle when the tip does NOT follow the stroke,
        //: or a held nib would jitter around zero instead of around its own
        //: 45 degrees.
        if (jit.rotation > 0) {
            const base = tipAngle === undefined
                ? (st.spec.angleDeg * Math.PI) / 180 : tipAngle;
            tipAngle = base + _jitter(st.stippleSeed, j, JITTER_ROTATION)
                              * Math.PI * jit.rotation;
        }
        //: U3-J. OPACITY JITTER RIDES THE FLOW MULTIPLIER, which the
        //: deposition cache already buckets, so a jittered dab costs a
        //: cache lookup rather than a table build. Legacy clamps to
        //: [0.01, 1] (`canvas-core.js:2915`); the bucketing floors at 0
        //: anyway, so only the upper clamp has to be written here.
        let flowMul = dyn.flow;
        if (jit.opacity > 0) {
            const f = flowMul * (1 + _jitter(st.stippleSeed, j,
                                             JITTER_OPACITY) * jit.opacity);
            flowMul = f < 0.01 ? 0.01 : (f > 1 ? 1 : f);
        }
        const dep = _depositionForMark(st, flowMul);
        if (st.renderer === V.RENDER_SWEEP && st.lastMark) {
            // U3-R2F F2. BOTH ENDS, so the segment carries a flow gradient
            // instead of one value. Without `st.lastDep` the sweep was flat
            // along its length and stepped at every boundary, and the
            // detrended residual on a pressure ramp had its dominant period at
            // the segment rate -- 8.0 px against a measured 8.1 px gap.
            //
            // `_depositionForMark` returns the SAME frozen object whenever
            // pressure does not drive flow, so `dep === st.lastDep` there and
            // the sweep takes its constant-flow path untouched. That is how
            // `none` and `size` are kept from accidentally gaining a gradient,
            // and it is a property of the object rather than a mode check that
            // could fall out of step with the descriptor.
            V.sweep(st.buffer, st.lastMark, mark, r, st.lastDep || dep, dep, j);
        } else {
            //: U3-J. SCATTER DISPLACES THE DAB PERPENDICULAR TO TRAVEL,
            //: which is Legacy's geometry (`canvas-core.js:3115`).
            //:
            //: STAMP ONLY, and that is a real limit rather than an oversight. A
            //: sweep draws a continuous segment between two marks; displacing
            //: its endpoints would bend the path rather than scatter dabs along
            //: it. Scatter Dust -- the preset whose whole character is this
            //: control, at 0.5 -- takes the stamp renderer, so it is covered.
            //: Airbrush (0.1) and Pastel (0.05) are round and therefore sweep,
            //: and do not scatter. Recorded as a gap with its numbers rather
            //: than papered over by routing them to a renderer that would cost
            //: them C2's crease repair.
            //:
            //: A NEW OBJECT, because the sampler freezes its marks.
            let placed = mark;
            if (jit.scatter > 0 && typeof mark.headingRad === "number") {
                const d = _jitter(st.stippleSeed, j, JITTER_SCATTER)
                          * st.radius * 2 * jit.scatter;
                placed = { x: mark.x - Math.sin(mark.headingRad) * d,
                           y: mark.y + Math.cos(mark.headingRad) * d };
            }
            V.stamp(st.buffer, placed, r, dep, tipAngle, j);
        }
        st.lastMark = mark;
        st.lastDep = dep;
        st.marks += 1;
        _stats.marks += 1;
    }
}

/** One browser dispatch, with every sample the browser coalesced into it. */
function addFromEvent(S, event, toDoc) {
    const st = _stroke;
    if (!st) return 0;
    const samples = st.input.samplesFrom(event, toDoc);
    st.samples += samples.length;
    _stats.samples += samples.length;
    for (let i = 0; i < samples.length; i++) {
        // ONE SAMPLE IN, ONE SAMPLE OUT. `StrokeFilter.push` returns a sample,
        // not a list -- the filter is causal and never fans out. Treating it as
        // a list silently iterated the sample's own KEYS and placed nothing:
        // the adapter reported samples consumed and zero marks, and Legacy's
        // opening dab made the canvas look almost right.
        const s = st.filter.push(samples[i]);
        if (!s) continue;
        const dabs = st.began ? st.sampler.push(s) : st.sampler.begin(s);
        st.began = true;
        if (dabs && dabs.length) _placeMarks(S, dabs);
    }
    return transfer(S);
}

/** The final endpoint, which §17 requires to be exact. */
function finish(S, event, toDoc) {
    const st = _stroke;
    if (!st) return 0;
    let last = null;
    if (event) {
        const samples = st.input.samplesFrom(event, toDoc);
        if (samples.length) last = samples[samples.length - 1];
    }
    // `flush` returns the TRUE final sample unfiltered, or null when there is
    // none. §5.1: emit what actually arrived rather than extrapolating, and let
    // the sampler place it exactly.
    const tail = st.filter.flush(last);
    const endSample = tail || last;
    const finalDabs = st.sampler.finish(endSample);
    if (finalDabs && finalDabs.length) _placeMarks(S, finalDabs);
    const moved = transfer(S);
    const summary = { samples: st.samples, marks: st.marks, moved: moved };
    _stroke = null;
    return summary;
}

/**
 * Drop everything. No canonical pixels, no undo record, no revision: this
 * function deliberately touches nothing but its own state, because the Canvas
 * owns the transaction and its cancel path already does the rest.
 */
function cancel() {
    _stroke = null;
}

window.StudioBrushV2Adapter = {
    REFUSE_FLAG_OFF, REFUSE_TOOL, REFUSE_NO_LAYER, REFUSE_NOT_RASTER,
    REFUSE_HIDDEN, REFUSE_LOCKED, REFUSE_MASK_MODE, REFUSE_TOUCH,

    isEnabled: function () { return _enabled; },
    //: Internal. Named with a leading underscore and never referenced from any
    //: settings surface, so the absence is testable.
    _setEnabled: function (on) { _enabled = !!on; if (!on) cancel(); return _enabled; },
    //: Internal, like the flag above. Exists so the lightest-touch width can be
    //: chosen by painting rather than argued about; no settings surface reads
    //: it and nothing persists it, so a reload returns to the default.
    DEFAULT_PRESSURE_WIDTH_FLOOR: DEFAULT_PRESSURE_WIDTH_FLOOR,
    _pressureWidthFloor: function () { return _pressureWidthFloor; },
    _setPressureWidthFloor: function (v) {
        const n = Number(v);
        _pressureWidthFloor = (isFinite(n) && n >= 0 && n < 1)
            ? n : DEFAULT_PRESSURE_WIDTH_FLOOR;
        return _pressureWidthFloor;
    },

    //: Internal, like the flag and the width floor above. Two strokes
    //: cannot be compared unless they draw the same seed, and every
    //: stroke deliberately takes a fresh one -- so a probe that wants to
    //: isolate ONE variable has to pin it. Nothing in the product calls
    //: this and nothing persists it.
    _resetStippleSeed: function (v) {
        _stippleSeedCounter = (Number(v) || 0) | 0;
        return _stippleSeedCounter;
    },

    isActive: function () { return _stroke !== null; },
    refusalFor: refusalFor,
    describeStroke: describeStroke,

    begin: begin,
    addFromEvent: addFromEvent,
    finish: finish,
    cancel: cancel,

    stats: function () {
        return {
            contacts: _stats.contacts, samples: _stats.samples,
            marks: _stats.marks, transfers: _stats.transfers,
            transferredPixels: _stats.transferredPixels,
            lastRefusal: _stats.lastRefusal,
            refusals: _stats.refusals.slice(-8),
            active: _stroke !== null,
            enabled: _enabled,
        };
    },
    resetStats: function () {
        _stats.contacts = 0; _stats.samples = 0; _stats.marks = 0;
        _stats.transfers = 0; _stats.transferredPixels = 0;
        _stats.refusals.length = 0; _stats.lastRefusal = null;
    },
};

})();
