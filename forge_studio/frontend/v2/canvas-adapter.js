/**
 * Forge Studio — Brush Engine V2 → Canvas raster-layer adapter (U3)
 * by ToxicHost & Moritz
 *
 * The first slice where V2 paints on a real document. V2 is the shipping
 * default since SR3-4, with Legacy painting every contact V2 refuses by name;
 * there is no public engine choice anywhere in this file.
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
//: has no storage at all.
//:
//: ON SINCE SR3-4. The owner approved V2 as the default painting engine "after
//: my re-check" (sign-off round 1, 2026-09-29); round 2 passed, its Changes
//: were made, and SR3 closed the four inputs V2 ignored. Legacy still paints
//: every contact V2 refuses by name. Rollback: this line, or `_setEnabled(false)`
//: for one session.
let _enabled = true;

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
//: SR3-3. Aliased coverage is binary and Pixel Perfect is a cell walk with a
//: corner filter; neither is a coverage model, which is the one thing V2 is.
//: Legacy's path is exact, and V2 painting the Pixel Perfect preset was
//: measured breaking all three of its promises (soft edge on every pixel, 1.8x
//: the pixels, 131 doubled corners on one diagonal). So Legacy keeps it.
const REFUSE_ALIASED = "aliased-and-pixel-perfect-stay-on-legacys-pixel-walk";

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
    //: Pixel Perfect does nothing unless the brush is aliased (Legacy's
    //: `pixelPerfectActive`), so the one flag decides both.
    if (S.brushAliased) return REFUSE_ALIASED;
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
    //: S. THE PRESET'S AUTHORED SPACING. `S.brushSpacing` had no producer
    //: anywhere in the shipped frontend -- only the measurement harness wrote
    //: it -- so every preset took the 0.15 fallback whatever it declared
    //: (`Evidence/source-review/U3S-spacing-is-never-forwarded.md`): Fine
    //: Liner asked for 0.03, Pixel Perfect for 1.0. The declared value is now
    //: read; 0.15 remains the default for a brush that declares none, and an
    //: explicit `S.brushSpacing` (the harness) still wins.
    //:
    //: LEGACY'S HARDNESS LAW IS STILL NOT COPIED (U3-R, spec §9.3). Legacy
    //: tightens SOFT tips, `* (0.3 + 0.7 * hardness)`, so separate stamps
    //: blend; the spec asks the opposite -- soft, low-frequency tips may be
    //: sparse -- and the sweep integrates the pass continuously. Measured
    //: after the pass-pricing repair: soft swept presets within 1 level of
    //: the tightened result at a third to a tenth of the cost.
    const dynSpacing = S.brushDynamics && typeof S.brushDynamics.spacing === "number"
        ? S.brushDynamics.spacing : 0;
    const declaredSpacing = dynSpacing > 0 ? Math.max(0.02, dynSpacing) : 0.15;
    const spacing = (typeof S.brushSpacing === "number" && S.brushSpacing > 0)
        ? S.brushSpacing : declaredSpacing;
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
        //: SR3-1. THE PRESET'S AUTHORED DYNAMICS, which V2 never read. Twelve
        //: of the thirteen presets declare curves (BE11) and Legacy applies
        //: them to every dab whatever the pressure toggle says -- so under V2
        //: Marker did not "skip when hurried", Ink Wash did not thin with
        //: speed, and a pen got no pressure response from any preset unless
        //: the toggle was on. Copied, so the contact cannot see a later edit.
        curves: _curvesOf(S),
        //: SR3-1. Taper In, which Ink Wash ships at 0.35 and the Brush
        //: Dynamics flyout offers for every brush. Legacy's `_taperFactor`.
        taperIn: (typeof S.brushTaperIn === "number" && S.brushTaperIn > 0)
            ? Math.min(1, S.brushTaperIn) : 0,
    };
}

//: SR3-1. The owner's curve rules that name an input and a target V2's
//: evaluator knows, copied and frozen; null when there are none, which keeps a
//: curveless brush on exactly the path it always took.
function _curvesOf(S) {
    const src = S && Array.isArray(S.brushCurves) ? S.brushCurves : null;
    if (!src || !src.length) return null;
    const out = [];
    for (let i = 0; i < src.length; i++) {
        const r = src[i];
        if (!r || D.INPUTS.indexOf(r.input) < 0 || D.TARGETS.indexOf(r.target) < 0) continue;
        out.push(Object.freeze(Object.assign({}, r)));
    }
    return out.length ? Object.freeze(out) : null;
}

//: SR3-1. Which targets the curves can move, decided once so the per-mark
//: code asks a boolean rather than scanning the rules.
function _curveTargets(curves) {
    const t = { size: false, flow: false, angle: false, ratio: false };
    if (curves) for (let i = 0; i < curves.length; i++) t[curves[i].target] = true;
    return Object.freeze(t);
}

//: SR3-1. Legacy's `_taperFactor`, on the stroke's arc length at the mark:
//: from 0.12 of the width at the contact point to full width after three
//: brush widths times the setting. The floor keeps the first mark a point
//: rather than a gap.
function _taperAt(st, arcPx) {
    if (!st.taperRamp) return 1;
    const t = arcPx / st.taperRamp;
    return 0.12 + 0.88 * (t < 1 ? t : 1);
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

// ───────────────────────────────────────────────────────── symmetry (SR3-2)

/*
 * SR3-2. SYMMETRY, which V2 ignored: with it switched on, only the stroke under
 * the hand painted.
 *
 * EVERY MARK V2 LAYS GOES THROUGH `_stamp` OR `_sweep`, and each lays its
 * copies there -- so bristle lanes, scatter particles, loose specks, pivot
 * fans and held-airbrush puffs are mirrored by construction rather than one
 * provider at a time. The copies deposit into the one coverage buffer under
 * the same deposition, so where they overlap at an axis they meet exactly as
 * a stroke meets itself.
 *
 * THE AXES ARE THE DRAWN GUIDES, `W / 2` and `H / 2` (`symGuides`). In V2's
 * convention pixel i spans [i, i+1), so `x' = W - x` is the exact mirror about
 * that line; Legacy's index-is-centre convention puts its axis half a pixel off
 * the guide.
 *
 * A MIRRORED COPY'S TIP IS MIRRORED TOO, and that is a deliberate divergence
 * from Legacy and the Extension, which keep the angle for `h` and `v`
 * (Extension `canvas-core.js:814-816`). Kept, the copy of a Calligraphy or
 * chisel stroke is the same nib translated, not a mirror image. Round tips
 * cannot show the difference. Radial is Legacy's: the copy turns with its
 * rotation.
 *
 * Each copy is `x' = a x + b y + c`, `y' = d x + e y + f`, `angle' = s angle + k`.
 */
function _symmetryFor(S) {
    const mode = S.symmetry;
    if (mode !== "h" && mode !== "v" && mode !== "both" && mode !== "radial") return null;
    const W = S.W, H = S.H, out = [];
    if (mode === "h" || mode === "both") {
        out.push(Object.freeze({ a: -1, b: 0, c: W, d: 0, e: 1, f: 0, s: -1, k: Math.PI }));
    }
    if (mode === "v" || mode === "both") {
        out.push(Object.freeze({ a: 1, b: 0, c: 0, d: 0, e: -1, f: H, s: -1, k: 0 }));
    }
    if (mode === "both") {
        out.push(Object.freeze({ a: -1, b: 0, c: W, d: 0, e: -1, f: H, s: 1, k: Math.PI }));
    }
    if (mode === "radial") {
        //: Legacy's count, `S.symmetryAxes || 4`, as copies 1..n-1.
        const n = Math.max(1, Math.floor(Number(S.symmetryAxes) || 4));
        const cx = W / 2, cy = H / 2;
        for (let i = 1; i < n; i++) {
            const t = (2 * Math.PI * i) / n, cos = Math.cos(t), sin = Math.sin(t);
            out.push(Object.freeze({ a: cos, b: -sin, c: cx - cx * cos + cy * sin,
                                     d: sin, e: cos, f: cy - cx * sin - cy * cos,
                                     s: 1, k: t }));
        }
    }
    return out.length ? Object.freeze(out) : null;
}

//: A point (and its heading, which a material's strands read) under one copy.
function _mirror(T, p) {
    const q = { x: T.a * p.x + T.b * p.y + T.c, y: T.d * p.x + T.e * p.y + T.f };
    if (typeof p.headingRad === "number") q.headingRad = T.s * p.headingRad + T.k;
    return q;
}

//: `V.stamp` and its copies. An undefined angle means the tip's frozen one,
//: which a copy still has to turn.
function _stamp(st, at, r, dep, angle, j, arc) {
    V.stamp(st.buffer, at, r, dep, angle, j, arc);
    const sym = st.symmetry;
    if (!sym) return;
    const base = typeof angle === "number" ? angle : (dep.tip ? dep.tip.angle : 0);
    for (let i = 0; i < sym.length; i++) {
        V.stamp(st.buffer, _mirror(sym[i], at), r, dep, sym[i].s * base + sym[i].k, j, arc);
    }
}

//: `V.sweep` and its copies. A sweep is round, so there is no angle to turn.
function _sweep(st, from, to, r, dep, depTo, j, arc) {
    V.sweep(st.buffer, from, to, r, dep, depTo, j, arc);
    const sym = st.symmetry;
    if (!sym) return;
    for (let i = 0; i < sym.length; i++) {
        V.sweep(st.buffer, _mirror(sym[i], from), _mirror(sym[i], to), r, dep, depTo, j, arc);
    }
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
    const material = _materialFor(S, spec, strokeSeed);
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
        material: material,
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
            //: SR1-3. Omitted before, so every tip was spaced as a circle.
            extentFor: _extentFor(S, spec, dep) || undefined,
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
        //: G1. Decided ONCE: whether this contact's tip turns with the stroke
        //: in a way that can change what it paints, and how far its ends reach
        //: as a fraction of the radius. Null for a round tip (no frame), a held
        //: nib (does not follow), or rotation jitter (turns are deliberate
        //: randomness, not a path). See `_pivotFill`.
        pivot: _pivotCapability(spec, dep, radius),
        lastStamp: null,
        //: G1. A following tip's opening mark, held until a heading exists --
        //: Legacy BE16 parity. Painting it at once laid a bar at the frozen
        //: angle across the start of every chisel stroke.
        pendingOpening: null,
        pivots: 0,
        //: G2. What the scatter provider laid, for evidence and tests.
        subDabs: 0,
        particles: 0,
        particleDeps: null,
        //: M1a. The dry medium's strands, frozen with the rest, or null; and
        //: the loose specks laid past the edge.
        material: material,
        specks: 0,
        arc: 0,
        //: M1b. Bristle lanes, or null; built just below from the seed.
        lanes: null,
        lanesPlaced: false,
        laneDeps: null,
        laneSegments: 0,
        //: SR1-4. The held airbrush: the hand, the last mark's size and flow,
        //: the puff depositions and how many were laid.
        lastSample: null,
        lastR: null,
        lastFlowMul: null,
        puffDeps: null,
        puffs: 0,
        //: SR1-5. 1 for a pen; the owner's Mouse press setting for a mouse.
        press: _pressFor(S, event),
        //: SR3-1. The preset's curves and what they can move, and whether this
        //: device MEASURES pressure and tilt -- noted from every sample, since
        //: the sampler's marks do not carry it. A mouse measures neither, so
        //: each of its curves takes the rule's own fallback, as in Legacy.
        curves: spec.curves,
        curveTargets: _curveTargets(spec.curves),
        pressureAvailable: false,
        tiltAvailable: false,
        //: SR3-1. Legacy's ramp: three brush widths times the setting.
        taperRamp: spec.taperIn > 0 ? Math.max(1, spec.sizePx * 3 * spec.taperIn) : 0,
        //: SR3-2. The copies every mark is laid again at, frozen: changing
        //: symmetry mid-contact must not split a stroke.
        symmetry: _symmetryFor(S),
    };
    _stroke.lanes = _lanesFor(_stroke, S);
    //: Lanes turn by their own geometry -- each follows the heading across
    //: the tip -- so G1's pivot fan and held opening, which exist for a
    //: stamped flat tip, do not apply.
    if (_stroke.lanes) _stroke.pivot = null;
    _stats.contacts += 1;

    _takeOwnership(S);
    //: P. After `beginStroke` cleared it, so this contact's table is the only
    //: one the merge can see. Null for anything that is not a dry medium on
    //: paper, which leaves Legacy's reveal in charge exactly as before.
    //: The ceiling is the deposition target at a full press: every flow rule
    //: and jitter scales flow DOWN from it, never up.
    const paperTable = S.stroke ? _paperTableFor(S, dep.target, _stroke.press) : null;
    if (S.stroke) S.stroke.paperTable = paperTable;
    _stroke.paper = paperTable ? S.brushMaterial : null;

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
function _depositionForMark(st, flowMultiplier, ratioMultiplier) {
    //: U3-J. OPACITY JITTER IS A SECOND REASON TO VARY FLOW PER MARK.
    //:
    //: This returned the frozen deposition whenever pressure did not drive
    //: flow, which was right when pressure was the only thing that could vary
    //: it. With a jitter it silently discarded the multiplier, and Sketch Light
    //: (0.15), Scatter Dust (0.2), Pencil and Charcoal (0.1) all painted
    //: exactly as if the control were zero -- measured, total alpha identical
    //: to the baseline down to the byte.
    //:
    //: SR3-1. A preset curve on flow or ratio is a third and a fourth.
    const ct = st.curveTargets;
    if (!st.spec.pressureToFlow
        && !(st.spec.jitter && st.spec.jitter.opacity > 0)
        && !(ct && (ct.flow || ct.ratio))) return st.dep;
    const m = flowMultiplier === undefined || flowMultiplier === null
        ? 1 : flowMultiplier;
    const flowBucket = Math.max(0, Math.min(FLOW_BUCKETS,
                                            Math.round(m * FLOW_BUCKETS)));
    //: SR3-1. Legacy clamps the dab's ratio to 0.05..1 (`stampWet`). Keyed
    //: beside flow only when a ratio curve exists, so every other brush keeps
    //: exactly the keys -- and the objects -- it had.
    const ratio = (ct && ct.ratio && typeof ratioMultiplier === "number")
        ? Math.max(0.05, Math.min(1, st.spec.ratio * ratioMultiplier)) : st.spec.ratio;
    const ratioBucket = (ct && ct.ratio) ? Math.round(ratio * RATIO_BUCKETS) : 0;
    const bucket = flowBucket + (FLOW_BUCKETS + 1) * ratioBucket;
    if (!st.depCache) st.depCache = new Map();
    let dep = st.depCache.get(bucket);
    if (dep) return dep;
    const spec = st.spec;
    dep = V.depositionFor({
        hardness: spec.hardness,
        flow: spec.flow * (flowBucket / FLOW_BUCKETS),
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
        tipKind: spec.tipKind, ratio: ratio, spikes: spec.spikes,
        angle: (spec.angleDeg * Math.PI) / 180,
        //: M1a. The same lesson as the tip above: a rebuilt deposition that
        //: omitted the material would fall back to the stipple at every
        //: pressure step.
        material: st.material,
    });
    st.depCache.set(bucket, dep);
    return dep;
}

//: SR3-1. Ratio steps for a curve-driven tip, e.g. Flat Chisel's tilt. Fine
//: enough that a tilting pen does not step visibly; bounded like the flow table
//: by the mark count, since entries are built lazily.
const RATIO_BUCKETS = 64;

//: P. THE DOCUMENT'S PAPER, ANSWERED THE WAY THE APPROVED STUDY ANSWERS IT.
//:
//: Legacy's reveal is linear -- `a * (1 - strength * (1 - h))` -- so pressing
//: harder scales the grain with the paint and never fills it: at the shipped
//: Depth 0.20 it takes at most a fifth off, and the study's own caption for
//: today's Pastel is "the paper never shows". The approved dry media answer
//: with a THRESHOLD that moves with coverage (DEC-BRUSH;
//: `Evidence/brush-material-study-2026-09-28/template.html` `flush`):
//:
//:     th = 1 - r * gain,  r = a / ceiling
//:     a' = a * ((1 - k) + k * smoothstep(th - tooth, th + tooth, h))
//:
//: Light coverage puts the threshold high, so only the peaks take paint;
//: heavy coverage drops it until the grain fills. `gain` below 1 keeps it
//: from ever filling, which is Pastel. `gain` and `tooth` are the study's,
//: per medium.
//:
//: `r` IS COVERAGE AGAINST THE STROKE'S OWN CEILING, not against 255. The
//: study's alpha was a function of PRESSURE reaching 1 at a full press; a
//: Studio preset's coverage stops at its deposition target -- Flow, or Flow x
//: Opacity with buildup -- and the dry presets declare 0.25 to 0.7. Keyed on
//: raw coverage, every one of them stayed "light" at any pressure: measured on
//: a pen ramp, Pastel kept 28% of its ink at BOTH ends and never filled.
//: Keyed on the ceiling, a full press is the study's full press.
//:
//: STILL THE DOCUMENT'S PAPER: the same tile, indexed by document coordinate
//: in the same merge, behind the same gates (a Surface chosen, Depth off zero,
//: a Tooth declared, not a mask). What V2 changes is the RESPONSE, not the
//: surface -- "Pencil and Pastel share the same paper and reveal it
//: differently" (BE10) holds as it did. The study's per-family grain SIZE
//: was its stand-in for a paper and is not reproduced; Scale is the owner's.
//:
//: `k` IS `|Depth| * Tooth * 4`. Fitted, not derived: at the shipped Depth
//: 0.20 it gives Pencil 0.68, Charcoal 0.80 and Pastel 0.76 against the
//: study's approved 0.70, 0.80 and 0.85, and it stays proportional to both
//: controls, so Tooth 0 or Depth 0 is still exactly off.
//:
//: A (coverage, height) TABLE, 64 KiB, built once per medium and amount: the
//: merge already walks only the dirty rectangle, and a lookup keeps it one.
const DRY_PAPER = Object.freeze({
    pencil:   Object.freeze({ gain: 0.95, tooth: 0.17 }),
    charcoal: Object.freeze({ gain: 0.98, tooth: 0.14 }),
    pastel:   Object.freeze({ gain: 0.74, tooth: 0.12 }),
});
const PAPER_AMOUNT_PER_DEPTH = 4;
const _paperTables = new Map();

//: M1a. THE STUDY'S STRANDS, per medium (`template.html` FAMILIES): strand
//: width across and streak length along, in px; `band` is how sharply Density
//: cuts them; `floor` the haze left between strands; `rough` the ragged edge;
//: `loose` = [specks per px of radius, reach in radii, alpha, speck size px]
//: for material landing past the edge. See `materialAt` in coverage.js.
const DRY_MATERIAL = Object.freeze({
    pencil:   Object.freeze({ strand: 0.9, streak: 60, band: 0.18, floor: 0.14, rough: 0,
                              loose: null }),
    charcoal: Object.freeze({ strand: 1.1, streak: 40, band: 0.12, floor: 0.14, rough: 0.16,
                              loose: Object.freeze([0.14, 1.55, 0.3, 0.55]) }),
    pastel:   Object.freeze({ strand: 2.2, streak: 3.2, band: 0.2, floor: 0.45, rough: 0.08,
                              loose: Object.freeze([0.014, 1.35, 0.8, 1.4]) }),
});
const SALT_MATERIAL = 0x2c1b3c6d;
const SALT_LOOSE = 0x632be5ab;
//: The study laid its dry dabs every `size * 0.08`, i.e. 0.16 of a radius, and
//: counted loose specks PER DAB. Per px of travel that is `rate / 0.16` --
//: independent of size -- and that is the rate kept here, so Studio's own
//: spacing (0.05 for Charcoal) does not lay 1.6x the study's dust.
const STUDY_DRY_GAP_RADII = 0.16;

//: The material a contact carries, or null. Null at Density 0.99 and above --
//: the exact bypass the study and DEC-BRUSH both require -- and for anything
//: that is not a dry medium, which keeps its stipple exactly as before.
function _materialFor(S, spec, strokeSeed) {
    const fam = S.brushMaterial ? DRY_MATERIAL[S.brushMaterial] : null;
    if (!fam || !(spec.density < V.STIPPLE_BELOW)) return null;
    return Object.freeze({
        strand: fam.strand, streak: fam.streak, band: fam.band, floor: fam.floor,
        rough: fam.rough, loose: fam.loose,
        seed: (strokeSeed ^ SALT_MATERIAL) | 0,
    });
}

//: M1b. BRISTLE LANES (DEC-BRUSH; the study's `makeLanes` / `dabRake`).
//:
//: Separate bristles across a flat tip that turns with the stroke. Each lane
//: has its own place across the tip, width, paint load, dry length and contact
//: threshold, all drawn once per stroke from the stroke seed. Density decides
//: which lanes touch (`th < density`); pressure widens the band in contact and
//: loads the paint; a lane dries along its own length of travel and starts
//: skipping. At Density 0.99 and above the flat tip draws exactly as before.
const SALT_LANE = 0x5bd1e995;
const LANE_MIN = 7, LANE_MAX = 42;

function _lanesWanted(S, spec) {
    return S.brushMaterial === "bristle" && spec.density < V.STIPPLE_BELOW;
}

//: The sampler's gap follows the tip's extent ALONG THE TRAVEL (Legacy BE7),
//: or null for a circle, which leaves the sampler's own isotropic default --
//: so every round tip keeps its spacing byte for byte. A following tip's
//: angle is the heading plus the owner's offset; a held nib's is the offset.
//:
//: NOT FOR BRISTLE LANES. Lanes are segments between marks and do not stamp
//: the flat tip at all; denser marks would only chop each lane into shorter
//: skip-dashes and change the look the owner approved.
function _extentFor(S, spec, dep) {
    if (_lanesWanted(S, spec)) return null;
    const tip = dep.tip;
    if (!V.tipFrame(10, tip)) return null;
    const offset = (spec.angleDeg * Math.PI) / 180;
    return spec.tipFollowsStroke
        ? function (heading) { return V.alongExtent(tip, heading, heading + offset); }
        : function (heading) { return V.alongExtent(tip, heading, offset); };
}

function _lanesFor(st, S) {
    const spec = st.spec;
    if (!_lanesWanted(S, spec)) return null;
    const size = spec.sizePx;
    const n = Math.max(LANE_MIN, Math.min(LANE_MAX, Math.round(size / 2.2)));
    const lanes = [];
    for (let i = 0; i < n; i++) {
        lanes.push({
            i: i,
            u: -1 + 2 * (i + 0.5 + (_draw(st, -1, i, 0, SALT_LANE) - 0.5) * 0.7) / n,
            w: (0.7 + _draw(st, -1, i, 1, SALT_LANE) * 0.9) * Math.max(0.9, size / 26),
            load: 0.65 + 0.35 * _draw(st, -1, i, 2, SALT_LANE),
            dry: (12 + _draw(st, -1, i, 3, SALT_LANE) * 14) * size,
            th: _draw(st, -1, i, 4, SALT_LANE),
            px: null, py: null, seg: 0,
        });
    }
    return lanes;
}

//: One lane deposition per alpha bucket, as G2 buckets its particles: a lane
//: segment is one pass of a small round capsule.
function _laneDeposition(st, alpha) {
    const bucket = Math.max(0, Math.min(FLOW_BUCKETS, Math.round(alpha * FLOW_BUCKETS)));
    if (!st.laneDeps) st.laneDeps = new Map();
    let d = st.laneDeps.get(bucket);
    if (d) return d;
    const spec = st.spec;
    d = V.depositionFor({
        hardness: Math.max(PARTICLE_MIN_HARDNESS, spec.hardness),
        flow: spec.flow * (bucket / FLOW_BUCKETS),
        opacity: spec.opacity, density: 1, buildup: spec.buildup,
        seed: st.stippleSeed, falloff: spec.falloff,
        step: 2, swept: true,
        tipKind: "round", ratio: 1, spikes: 2, angle: 0,
    });
    st.laneDeps.set(bucket, d);
    return d;
}

function _laneMark(st, mark, r, flowMul, j) {
    //: The study draws nothing before a heading exists; lanes are laid out
    //: across the travel, and there is no travel yet.
    if (typeof mark.headingRad !== "number") return;
    const p = (flowMul > 1 ? 1 : (flowMul < 0 ? 0 : flowMul)) * st.press;
    const half = r * (0.62 + 0.38 * p);
    const nx = -Math.sin(mark.headingRad), ny = Math.cos(mark.headingRad);
    const contact = 0.3 + 0.7 * p;
    //: The first placement starts from the previous mark, so the stroke
    //: begins where the pointer went down rather than one gap later.
    const origin = st.lanesPlaced ? null : (st.lastMark || mark);
    const density = st.spec.density;
    for (let k = 0; k < st.lanes.length; k++) {
        const L = st.lanes[k];
        const lx = mark.x + nx * L.u * half, ly = mark.y + ny * L.u * half;
        const fx = L.px !== null ? L.px : origin.x + nx * L.u * half;
        const fy = L.py !== null ? L.py : origin.y + ny * L.u * half;
        if (L.th < density && Math.abs(L.u) <= contact + 0.05) {
            const dry = Math.max(0, 1 - st.arc / L.dry);
            L.seg += 1;
            if (_draw(st, L.seg, L.i, 5, SALT_LANE) < 0.25 + 0.75 * dry + 0.15 * p) {
                const tex = 0.72 + 0.28 * V.latticeNoise(st.arc / 6, L.i * 7.3,
                                                         st.stippleSeed ^ SALT_LANE);
                const ldep = _laneDeposition(st, Math.min(1,
                    L.load * (0.55 + 0.45 * p) * (0.55 + 0.45 * dry) * tex));
                _sweep(st, { x: fx, y: fy }, { x: lx, y: ly }, L.w / 2, ldep, ldep, j);
                st.laneSegments += 1;
            }
        }
        L.px = lx; L.py = ly;
    }
    st.lanesPlaced = true;
    st.lastLaneFlow = p;
}

//: A tap never had a heading, so no lane moved: the study's ticks, a short
//: stroke per lane in contact, across a tip held as the study holds it.
function _laneTap(st) {
    const m = st.lastMark;
    const p = typeof st.lastLaneFlow === "number" ? st.lastLaneFlow : st.press;
    const size = st.spec.sizePx, half = (size / 2) * (0.62 + 0.38 * p);
    const contact = 0.3 + 0.7 * p;
    for (let k = 0; k < st.lanes.length; k++) {
        const L = st.lanes[k];
        if (L.th >= st.spec.density || Math.abs(L.u) > contact + 0.05) continue;
        const ly = m.y + L.u * half;
        const ldep = _laneDeposition(st, Math.min(1, L.load * (0.35 + 0.65 * p)));
        _sweep(st, { x: m.x - size * 0.04, y: ly }, { x: m.x + size * 0.04, y: ly },
                L.w / 2, ldep, ldep, 0);
        st.laneSegments += 1;
    }
}

//: SR1-4. BUILDING WHILE HELD STILL (spec §9.4). The clock is Legacy's
//: `airbrushTick` -- one clock for both engines: rate, time debt, one-second
//: cap, stillness, token cancellation -- and while a V2 contact is active it
//: asks here where the hand is and has each owed puff laid here, so no Legacy
//: dab lands in the buffer V2 owns.
//:
//: A puff is a STAMP at the hand, not a mark in the sampler's row: it is a
//: rate in time on the same pixels, so it is not normalised by spatial
//: overlap. `step: 2` makes the overlap exactly one -- Legacy's
//: `timeDepositStep` -- and a puff deposits its full target.
function _puffDeposition(st, flowMul) {
    const bucket = Math.max(0, Math.min(FLOW_BUCKETS, Math.round(flowMul * FLOW_BUCKETS)));
    if (!st.puffDeps) st.puffDeps = new Map();
    let d = st.puffDeps.get(bucket);
    if (d) return d;
    const spec = st.spec;
    d = V.depositionFor({
        hardness: spec.hardness,
        flow: spec.flow * (bucket / FLOW_BUCKETS),
        opacity: spec.opacity, density: spec.density, buildup: spec.buildup,
        seed: st.stippleSeed, falloff: spec.falloff,
        step: 2, swept: false,
        tipKind: spec.tipKind, ratio: spec.ratio, spikes: spec.spikes,
        angle: (spec.angleDeg * Math.PI) / 180,
        material: st.material,
    });
    st.puffDeps.set(bucket, d);
    return d;
}

function pointerAt() {
    const st = _stroke;
    if (!st) return null;
    const at = st.lastSample || st.lastMark;
    return at ? { x: at.x, y: at.y } : null;
}

function airbrushPuff(S) {
    const st = _stroke;
    if (!st) return 0;
    const at = st.lastSample || st.lastMark;
    if (!at) return 0;
    const flowMul = typeof st.lastFlowMul === "number" ? st.lastFlowMul : 1;
    const r = typeof st.lastR === "number" ? st.lastR : st.radius;
    _stamp(st, { x: at.x, y: at.y }, r, _puffDeposition(st, flowMul), undefined, st.markIndex);
    st.puffs += 1;
    transfer(S);
    return 1;
}

//: M1a. Charcoal's dust and Pastel's crumbs: seeded specks past the edge,
//: more of them the harder the press (the flow multiplier stands in for it,
//: as it does for everything pressure drives). G2's particle deposition, so a
//: speck is one contribution and nothing new is invented to lay it.
function _looseMaterial(st, mark, r, flowMul, j) {
    const loose = st.material && st.material.loose;
    if (!loose) return;
    const rate = loose[0], far = loose[1], alpha = loose[2], size = loose[3];
    const p = (flowMul > 1 ? 1 : (flowMul < 0 ? 0 : flowMul)) * st.press;
    const gap = st.spec.spacingFraction * st.spec.sizePx;
    const count = (rate / STUDY_DRY_GAP_RADII) * gap * (0.4 + 0.6 * p);
    let n = Math.floor(count) + (_draw(st, j, 0, 7, SALT_LOOSE) < count % 1 ? 1 : 0);
    for (let k = 1; k <= n; k++) {
        const ang = _draw(st, j, k, 0, SALT_LOOSE) * Math.PI * 2;
        const dist = r * (0.85 + _draw(st, j, k, 1, SALT_LOOSE) * (far - 0.85));
        const pr = Math.max(0.5, size * (0.6 + _draw(st, j, k, 2, SALT_LOOSE) * 0.8));
        const pdep = _particleDeposition(st, Math.min(1,
            alpha * (0.5 + 0.5 * _draw(st, j, k, 3, SALT_LOOSE)) * (0.5 + 0.5 * p)));
        _stamp(st, { x: mark.x + Math.cos(ang) * dist, y: mark.y + Math.sin(ang) * dist },
                pr, pdep, undefined, j);
    }
    st.specks += n;
}

//: SR1-5. How hard this contact presses, for the responses a pen's pressure
//: would drive: 1 for a pen (its pressure already reaches the coverage), the
//: owner's Mouse press setting for a mouse (`S.mousePress`, the study's 0.5 by
//: default). Decided once at `begin`, like everything else about a contact.
function _pressFor(S, event) {
    if (!event || event.pointerType !== "mouse") return 1;
    const m = Number(S.mousePress);
    return m > 0 && m <= 1 ? m : 1;
}

function _paperTableFor(S, ceiling, press) {
    const resp = S.brushMaterial ? DRY_PAPER[S.brushMaterial] : null;
    const P = S.paper;
    if (!resp || S.editingMask || !P || !P.texture || P.texture === "none") return null;
    const depth = P.depth || 0;
    const grain = S.brushGrain != null ? S.brushGrain : 1;
    const k = Math.min(1, Math.abs(depth) * grain * PAPER_AMOUNT_PER_DEPTH);
    if (!(k > 0)) return null;
    //: Negative Depth reveals the valleys, as in Legacy: same paper, inverted.
    const invert = depth < 0;
    //: In coverage bytes, as the merge sees it. Never below 1, so a
    //: vanishing Flow cannot divide by zero.
    const ceil = Math.max(1, Math.round(255 * Math.min(1, ceiling > 0 ? ceiling : 1)));
    //: SR1-5. A mouse at medium press reads its coverage as half of a full
    //: press, as the study's mouse did -- so the grain shows instead of filling.
    const pr = press > 0 && press <= 1 ? press : 1;
    const key = S.brushMaterial + "|" + k + "|" + invert + "|" + ceil + "|" + pr;
    const hit = _paperTables.get(key);
    if (hit) return hit;
    const tbl = new Uint8Array(65536);
    for (let a = 1; a < 256; a++) {
        const th = 1 - Math.min(1, a / ceil) * pr * resp.gain;
        const e0 = th - resp.tooth, span = 2 * resp.tooth;
        for (let h = 0; h < 256; h++) {
            let t = ((invert ? 255 - h : h) / 255 - e0) / span;
            t = t < 0 ? 0 : (t > 1 ? 1 : t);
            //: Same rounding as Legacy's merge, so the two differ only in the
            //: response and never by a rounding step.
            tbl[(a << 8) | h] = (a * ((1 - k) + k * t * t * (3 - 2 * t)) + 0.5) | 0;
        }
    }
    if (_paperTables.size >= 16) _paperTables.clear();
    _paperTables.set(key, tbl);
    return tbl;
}

//: G1. A SHARP TURN IS FILLED BY PIVOTING THE TIP, NOT BY LAGGING THE HEADING.
//:
//: The sampler's heading is the path's tangent (Legacy BE7), which snaps at a
//: vertex: measured on Flat Chisel, 34-55 degrees in ONE mark at a right-angle
//: corner and 51-78 at a zig-zag point, with marks 3.5 px apart -- so the
//: tip's far end jumped 13-30 px and left a bow-tie. Legacy `_pivotFill` is
//: the same rule: pivot stamps between two marks, interpolating position and
//: angle, until neither end moves more than one gap per stamp.
//:
//: A PIVOT DEPOSITS ONCE. The fan is a rotational sweep, so it takes the
//: sweep's semantic -- accumulation weight 0, the MAX floor `cov * target` --
//: and a corner reaches the one-pass level instead of darkening.
const PIVOT_MAX = 64;
const _PIVOT_DEPS = new WeakMap();

function _pivotCapability(spec, dep, radius) {
    if (!spec.tipFollowsStroke) return null;
    if (spec.jitter && spec.jitter.rotation > 0) return null;
    const frame = V.tipFrame(Math.max(0.5, radius), dep.tip);
    if (!frame) return null;
    return { reach: Math.max(frame.rx, frame.ry) / Math.max(0.5, radius) };
}

function _pivotDeposition(dep) {
    let p = _PIVOT_DEPS.get(dep);
    if (!p) {
        p = Object.freeze(Object.assign({}, dep, { swept: true }));
        _PIVOT_DEPS.set(dep, p);
    }
    return p;
}

//: Test seam, as Legacy's `setPivotFill`: the same stroke with and without the
//: fill, so "a pivot adds no darkness" is measured directly. Product code
//: never turns it off.
let _pivotFillOn = true;

function _pivotFill(st, placed, r, dep, tipAngle, j) {
    const from = st.lastStamp;
    if (!_pivotFillOn || !st.pivot || !from || typeof tipAngle !== "number") return;
    const turn = Math.atan2(Math.sin(tipAngle - from.angle), Math.cos(tipAngle - from.angle));
    const gap = Math.max(1, Math.hypot(placed.x - from.x, placed.y - from.y));
    const reach = Math.max(r, from.r) * st.pivot.reach;
    const n = Math.min(PIVOT_MAX, Math.ceil(reach * Math.abs(turn) / gap) - 1);
    if (!(n > 0)) return;
    const pdep = _pivotDeposition(dep);
    for (let k = 1; k <= n; k++) {
        const t = k / (n + 1);
        _stamp(st, { x: from.x + (placed.x - from.x) * t, y: from.y + (placed.y - from.y) * t },
                from.r + (r - from.r) * t, pdep, from.angle + turn * t, j);
    }
    st.pivots += n;
}

//: G2. THE SCATTER PROVIDER.
//:
//: V2 drew Scatter Dust as ONE round dab per mark. Both oracles draw a CLUSTER
//: (Extension `canvas-core.js` stampAlphaMap scatter branch; Legacy's copy
//: routes it through `cover`): `max(3, size/3)` round sub-dabs at
//: `(u - 0.5) * size * 0.8` per axis, radius `u * r * 0.3 + 1`. That cluster is
//: the plain brush, and at Density 1 it is drawn here -- seeded from `hash01`
//: per (stroke, mark, sub-dab), never `Math.random`, so a stroke replays.
//:
//: BELOW DENSITY 1 IT IS THE APPROVED PARTICLE MATERIAL (DEC-BRUSH, owner
//: 2026-09-28), not the per-pixel stipple the owner rejected as static:
//: small seeded particles scattered round the mark, Scatter setting the
//: cloud's reach and Density the COUNT, derived from the occupancy it should
//: reach once the marks either side have landed. Each particle is one
//: contribution (`overlapK` 1) with no stipple -- Density already chose which
//: particles exist, and thinning them per pixel as well would spend it twice.
const SCATTER_PLAIN_DENSITY = 0.99;
const SALT_SUBDAB = 0x7f4a7c15;
const SALT_PARTICLE = 0x94d049bb;
const PARTICLE_MIN_HARDNESS = 0.75;
const PARTICLE_MAX_OCCUPANCY = 0.97;

function _draw(st, j, k, channel, salt) {
    return V.hash01((k * 8 + channel) | 0,
                    (st.stippleSeed ^ salt ^ Math.imul(j + 1, 0x9E3779B1)) | 0);
}

function _particleDeposition(st, flowMul) {
    const bucket = Math.max(0, Math.min(FLOW_BUCKETS, Math.round(flowMul * FLOW_BUCKETS)));
    if (!st.particleDeps) st.particleDeps = new Map();
    let d = st.particleDeps.get(bucket);
    if (d) return d;
    const spec = st.spec;
    d = V.depositionFor({
        hardness: Math.max(PARTICLE_MIN_HARDNESS, spec.hardness),
        flow: spec.flow * (bucket / FLOW_BUCKETS),
        opacity: spec.opacity, density: 1, buildup: spec.buildup,
        seed: st.stippleSeed, falloff: spec.falloff,
        //: `2 / step` contributions cover a pixel; a particle is one.
        step: 2, swept: false,
        tipKind: "round", ratio: 1, spikes: 2, angle: 0,
    });
    st.particleDeps.set(bucket, d);
    return d;
}

function _scatterMark(st, mark, r, dep, flowMul, j, jit) {
    const size = r * 2;
    if (!(st.spec.density < SCATTER_PLAIN_DENSITY)) {
        let cx = mark.x, cy = mark.y;
        //: The whole cluster takes U3-J's perpendicular scatter, as Legacy's
        //: dab does before its sub-dabs are drawn.
        if (jit.scatter > 0 && typeof mark.headingRad === "number") {
            const d = _jitter(st.stippleSeed, j, JITTER_SCATTER) * st.radius * 2 * jit.scatter;
            cx -= Math.sin(mark.headingRad) * d;
            cy += Math.cos(mark.headingRad) * d;
        }
        const n = Math.max(3, Math.floor(size / 3));
        for (let k = 0; k < n; k++) {
            _stamp(st,
                    { x: cx + (_draw(st, j, k, 0, SALT_SUBDAB) - 0.5) * size * 0.8,
                      y: cy + (_draw(st, j, k, 1, SALT_SUBDAB) - 0.5) * size * 0.8 },
                    _draw(st, j, k, 2, SALT_SUBDAB) * r * 0.3 + 1, dep, undefined, j);
        }
        st.subDabs += n;
        return;
    }
    const reach = r * (1 + 2 * jit.scatter);
    const pr0 = Math.max(0.55, size * 0.035);
    //: How many marks overlap one point of the cloud, at the nominal gap.
    const gap = Math.max(1, st.spec.spacingFraction * st.spec.sizePx);
    const hits = Math.max(1, (2 * reach) / gap);
    const occupancy = Math.min(PARTICLE_MAX_OCCUPANCY, Math.max(0, st.spec.density));
    const n = Math.max(1, Math.round(-Math.log(1 - occupancy) * (reach * reach)
                                     / (pr0 * pr0 * 1.3) / hits));
    const sigma = reach / 2.2;
    for (let k = 0; k < n; k++) {
        const ang = _draw(st, j, k, 0, SALT_PARTICLE) * Math.PI * 2;
        const rad = sigma * Math.sqrt(-2 * Math.log(1 - _draw(st, j, k, 1, SALT_PARTICLE) * 0.999));
        const pr = pr0 * (0.5 + _draw(st, j, k, 2, SALT_PARTICLE) * 1.1);
        const pdep = _particleDeposition(st, flowMul * (0.55 + 0.45 * _draw(st, j, k, 3, SALT_PARTICLE)));
        _stamp(st, { x: mark.x + Math.cos(ang) * rad, y: mark.y + Math.sin(ang) * rad },
                pr, pdep, undefined, j);
    }
    st.particles += n;
}

/** Lay the held opening mark, at `angle` or at the frozen angle for a tap. */
function _flushOpening(st, angle) {
    const o = st.pendingOpening;
    if (!o) return;
    st.pendingOpening = null;
    const a = typeof angle === "number" ? angle : (st.spec.angleDeg * Math.PI) / 180;
    _stamp(st, o, o.r, o.dep, a, o.j);
    _noteStamp(st, o, o.r, a);
    st.marks += 1;
    _stats.marks += 1;
}

/** Remember the stamp just laid, reusing one object for the contact. */
function _noteStamp(st, placed, r, angle) {
    if (!st.pivot) return;
    const v = st.lastStamp || (st.lastStamp = { x: 0, y: 0, r: 0, angle: 0 });
    v.x = placed.x; v.y = placed.y; v.r = r; v.angle = angle;
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
        //: SR3-1. THE PRESET'S CURVES, on what this device actually measures.
        //: Separate from the toggle's rules above, which keep the inputs they
        //: were built and measured against; Legacy likewise multiplies BE11's
        //: modifiers onto what `pSz`/`pOp` already returned.
        const pre = st.curves ? D.evaluate(st.curves, {
            pressure: mark.pressure,
            pressureAvailable: st.pressureAvailable,
            tiltXDeg: mark.tiltXDeg, tiltYDeg: mark.tiltYDeg,
            tiltAvailable: st.tiltAvailable,
            headingRad: mark.headingRad,
            speed: ctx.speed,
        }) : D.NEUTRAL;
        //: U3-J. THE DAB INDEX IS THE DRAW'S ADDRESS. Counted on the
        //: stroke rather than taken from `i`, because `_placeMarks` is
        //: called once per batch of dabs and `i` restarts at zero every
        //: time -- which would make every batch jitter identically and
        //: read as a repeating pattern at the pointer-event rate.
        const j = st.markIndex++;
        //: A spec built by a caller that predates U3-J has no `jitter`
        //: at all. Absent must mean OFF, not a throw at paint time.
        const jit = st.spec.jitter || NO_JITTER;
        let r = Math.max(0.5, st.radius * dyn.size * pre.size);
        if (jit.size > 0) {
            //: Legacy floors at 1 PIXEL, not at a fraction of the
            //: radius (`canvas-core.js:2914`).
            r = Math.max(1, r * (1 + _jitter(st.stippleSeed, j,
                                             JITTER_SIZE) * jit.size));
        }
        //: SR3-1. Taper after the jitter, as `stampWet` orders it, at this
        //: mark's arc length -- the stroke's so far plus the step to here.
        if (st.taperRamp) {
            const arcHere = st.lastMark
                ? st.arc + Math.hypot(mark.x - st.lastMark.x, mark.y - st.lastMark.y)
                : st.arc;
            r = Math.max(0.5, r * _taperAt(st, arcHere));
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
        //: SR3-1. A curve on angle ADDS DEGREES, as `stampWet` adds `M.angle`:
        //: to the heading for a following tip, to the frozen angle for a held
        //: one.
        if (pre.angle) {
            const base = tipAngle === undefined
                ? (st.spec.angleDeg * Math.PI) / 180 : tipAngle;
            tipAngle = base + (pre.angle * Math.PI) / 180;
        }
        //: U3-J. OPACITY JITTER RIDES THE FLOW MULTIPLIER, which the
        //: deposition cache already buckets, so a jittered dab costs a
        //: cache lookup rather than a table build. Legacy clamps to
        //: [0.01, 1] (`canvas-core.js:2915`); the bucketing floors at 0
        //: anyway, so only the upper clamp has to be written here.
        let flowMul = dyn.flow * pre.flow;
        if (jit.opacity > 0) {
            const f = flowMul * (1 + _jitter(st.stippleSeed, j,
                                             JITTER_OPACITY) * jit.opacity);
            flowMul = f < 0.01 ? 0.01 : (f > 1 ? 1 : f);
        }
        const dep = _depositionForMark(st, flowMul, pre.ratio);
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
            _sweep(st, st.lastMark, mark, r, st.lastDep || dep, dep, j, st.arc);
        } else if (st.spec.tipKind === "scatter") {
            //: G2. A SCATTER TIP IS A PROVIDER, not one round dab. See
            //: `_scatterMark` for the cluster (Density 1) and the particles.
            _scatterMark(st, mark, r, dep, flowMul, j, jit);
        } else if (st.lanes) {
            //: M1b. Bristle lanes below the bypass; see `_laneMark`.
            _laneMark(st, mark, r, flowMul, j);
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
            if (st.pivot && typeof mark.headingRad !== "number" && !st.lastStamp) {
                //: G1. THE OPENING MARK WAITS FOR A DIRECTION. Legacy BE16
                //: defers it rather than guess; stamping it now at the frozen
                //: angle laid a bar across the start of every chisel stroke.
                //: `_flushOpening` lays it with the first heading, or at the
                //: frozen angle if the stroke ends without moving (a tap).
                st.pendingOpening = { x: placed.x, y: placed.y, r: r, dep: dep, j: j };
                st.lastMark = mark;
                st.lastDep = dep;
                continue;
            }
            if (st.pendingOpening) _flushOpening(st, tipAngle);
            _pivotFill(st, placed, r, dep, tipAngle, j);
            _stamp(st, placed, r, dep, tipAngle, j);
            _noteStamp(st, placed, r, typeof tipAngle === "number" ? tipAngle
                : (st.spec.angleDeg * Math.PI) / 180);
        }
        if (st.material) _looseMaterial(st, mark, r, flowMul, j);
        //: SR1-4. What an airbrush puff held here would lay: this mark's size
        //: and flow, so pressure still sets the amount while the hand rests.
        st.lastR = r;
        st.lastFlowMul = flowMul;
        //: M1a. The arc length along the chain of marks the sweep draws, so the
        //: next segment's strands start exactly where this one's ended.
        if (st.lastMark) st.arc += Math.hypot(mark.x - st.lastMark.x, mark.y - st.lastMark.y);
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
        //: SR3-1. What the device measures, for the preset's curves.
        _noteDevice(st, samples[i]);
        // ONE SAMPLE IN, ONE SAMPLE OUT. `StrokeFilter.push` returns a sample,
        // not a list -- the filter is causal and never fans out. Treating it as
        // a list silently iterated the sample's own KEYS and placed nothing:
        // the adapter reported samples consumed and zero marks, and Legacy's
        // opening dab made the canvas look almost right.
        const s = st.filter.push(samples[i]);
        if (!s) continue;
        //: SR1-4. Where the hand IS, for the airbrush clock's stillness test and
        //: its puffs -- the sampler's last mark can lag it by up to one gap.
        st.lastSample = s;
        const dabs = st.began ? st.sampler.push(s) : st.sampler.begin(s);
        st.began = true;
        if (dabs && dabs.length) _placeMarks(S, dabs);
    }
    return transfer(S);
}

//: SR3-1. `input.js` decides per sample whether pressure and tilt were
//: MEASURED (a pen) or are placeholders (a mouse); the sampler's marks drop the
//: flags, so the contact keeps them here for the curves.
function _noteDevice(st, sample) {
    st.pressureAvailable = !!sample.pressureAvailable;
    st.tiltAvailable = !!sample.tiltAvailable;
}

/** The final endpoint, which §17 requires to be exact. */
function finish(S, event, toDoc) {
    const st = _stroke;
    if (!st) return 0;
    let last = null;
    if (event) {
        const samples = st.input.samplesFrom(event, toDoc);
        if (samples.length) last = samples[samples.length - 1];
        if (last) _noteDevice(st, last);
    }
    // `flush` returns the TRUE final sample unfiltered, or null when there is
    // none. §5.1: emit what actually arrived rather than extrapolating, and let
    // the sampler place it exactly.
    const tail = st.filter.flush(last);
    const endSample = tail || last;
    const finalDabs = st.sampler.finish(endSample);
    if (finalDabs && finalDabs.length) _placeMarks(S, finalDabs);
    //: G1. A tap never produced a heading: its held opening mark lands now,
    //: at the frozen angle, so it still leaves a mark.
    if (st.pendingOpening) _flushOpening(st, undefined);
    //: M1b. A tap with lanes leaves the study's ticks.
    if (st.lanes && st.laneSegments === 0 && st.lastMark) _laneTap(st);
    const moved = transfer(S);
    const summary = { samples: st.samples, marks: st.marks, moved: moved, pivots: st.pivots,
                      subDabs: st.subDabs, particles: st.particles, paper: st.paper,
                      material: st.material ? S.brushMaterial : null, specks: st.specks,
                      lanes: st.lanes ? st.lanes.length : 0, laneSegments: st.laneSegments,
                      lanesInContact: st.lanes ? st.lanes.filter(L => L.th < st.spec.density).length : 0,
                      puffs: st.puffs };
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
    REFUSE_HIDDEN, REFUSE_LOCKED, REFUSE_MASK_MODE, REFUSE_TOUCH, REFUSE_ALIASED,

    isEnabled: function () { return _enabled; },
    //: Internal. Named with a leading underscore and never referenced from any
    //: settings surface, so the absence is testable.
    _setEnabled: function (on) { _enabled = !!on; if (!on) cancel(); return _enabled; },
    _setPivotFill: function (on) { _pivotFillOn = !!on; return _pivotFillOn; },
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
    //: P. Read-only views for evidence and tests; the merge reads the table
    //: from `S.stroke.paperTable`, never from here.
    DRY_PAPER: DRY_PAPER,
    DRY_MATERIAL: DRY_MATERIAL,
    PAPER_AMOUNT_PER_DEPTH: PAPER_AMOUNT_PER_DEPTH,
    _paperTableFor: _paperTableFor,

    begin: begin,
    addFromEvent: addFromEvent,
    finish: finish,
    cancel: cancel,
    //: SR1-4. Called by Legacy's airbrush clock while a V2 contact is active.
    pointerAt: pointerAt,
    airbrushPuff: airbrushPuff,

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
