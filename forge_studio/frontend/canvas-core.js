/**
 * Forge Studio — Canvas Core (Standalone Clean Engine)
 * by ToxicHost & Moritz
 *
 * Phase 1: Pure algorithm module. No DOM queries, no event listeners,
 * no panel rendering. Functions take data, return data.
 *
 * Browser canvas APIs (createElement("canvas"), getContext, ImageData) are used
 * for offscreen buffers — that's unavoidable. But nothing here calls
 * getElementById, querySelector, addEventListener, or manipulates layout.
 *
 * Exposes window.StudioCore — the contract Phase 2 (canvas-ui.js) binds to.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * COLORSPACE CONTRACT
 * ─────────────────────────────────────────────────────────────────────────
 * Studio's use case is sRGB end-to-end: generative models (SD, Flux, WAN)
 * output sRGB, exports go to disk tagged sRGB, and monitors display sRGB
 * via the OS color manager. The pipeline is built around that and nothing
 * fancier — no wide-gamut working space, no per-image ICC awareness, no
 * OCIO. Don't add those without a concrete use case that needs them.
 *
 *   1. EVERY 2D canvas context in the codebase MUST be created with
 *      `getContext("2d", { colorSpace: "srgb" })` — including the display
 *      canvas. Layer canvases, mask, stroke buffer, region overlays,
 *      temp/cache canvases, the export offscreen, the on-screen canvas:
 *      all sRGB. The OS color manager handles sRGB→display conversion via
 *      the monitor ICC profile, which works correctly on every display
 *      including wide-gamut. We tried `display-p3` on the display canvas;
 *      on Firefox mode-2 calibrated setups the browser re-tagged the buffer
 *      as P3 without converting sRGB values, so sRGB pixel values rendered
 *      through P3 primaries and the canvas no longer matched the
 *      (correctly sRGB-tagged) export. Don't reintroduce.
 *
 *   2. EXPORT is lossless to the backend. exportFlattened() always returns
 *      `data:image/png;...` regardless of the user's chosen output format.
 *      The backend does the single, intentional lossy encode (JPEG/WebP)
 *      with the appropriate ICC profile. Encoding lossy on the frontend
 *      causes a double-encode and visibly degrades color (this was
 *      Moritz's "duller and yellow" bug; do not reintroduce).
 *
 *      For Send-to-Canvas of saved Studio outputs, displayOnCanvas uses
 *      backend raw RGBA + putImageData (POST /studio/image_pixels) so
 *      the canvas receives the same pixels Pillow reads from disk. The
 *      `<img>`/ImageBitmap browser decode paths color-manage pixels
 *      during decode on calibrated Firefox setups (chromatically
 *      desaturating reds/oranges before drawImage); raw-pixel import
 *      bypasses that. `<img>` is kept as a fallback for non-file-backed
 *      sources (Live Painting, drag/drop, unsaved data URLs) and when
 *      /studio/image_pixels is unreachable. Diagnosed via
 *      window.StudioDebug.sampleColorPipelineGrid; don't change the
 *      import path without re-running it.
 *
 *   3. BACKEND tags every save with sRGB ICC (_SRGB_ICC in studio_api.py).
 *      It does NOT currently convert non-sRGB inputs — if a user ever
 *      drags a P3-tagged file in, that needs profileToProfile conversion
 *      before tagging. Until then, this is fine because all canvas-side
 *      paths are sRGB by construction.
 * ─────────────────────────────────────────────────────────────────────────
 */
(function () {
"use strict";

// ========================================================================
// OFFSCREEN CANVAS FACTORY
// ========================================================================
// Abstracted so we never call document.createElement directly in algorithm code.
function _createCanvas(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    return c;
}

// Reusable temp canvases — prevents per-frame allocation / GC pressure
const _tempCanvases = {};
function getTempCanvas(key, w, h) {
    let tc = _tempCanvases[key];
    if (!tc || tc.width !== w || tc.height !== h) {
        tc = _createCanvas(w, h);
        _tempCanvases[key] = tc;
    }
    const ctx = tc.getContext("2d", { colorSpace: "srgb" });
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.filter = "none";
    ctx.clearRect(0, 0, w, h);
    return tc;
}

// Apply the document → display-canvas transform on `ctx`. Use this in place
// of `ctx.setTransform(z.scale, 0, 0, z.scale, z.ox, z.oy)` when drawing to
// the on-screen display canvas, so HiDPI backing buffers (CSS × devicePixel
// Ratio) are correctly scaled.
//
// zoom.scale / zoom.ox / zoom.oy are stored in CSS pixels (DPR-independent);
// the buffer is sized CSS × DPR by syncCanvasToViewport, so the multiplier
// here turns the CSS-unit transform into one that fills the larger buffer.
function applyDisplayTransform(ctx) {
    const z = S.zoom;
    const dpr = S.displayDpr || 1;
    ctx.setTransform(z.scale * dpr, 0, 0, z.scale * dpr, z.ox * dpr, z.oy * dpr);
}

/**
 * THE DOCUMENT'S SURFACE, AS SHIPPED. One definition, three consumers.
 *
 * BE19. BE10 built the paper system and it reached ZERO PIXELS: every preset
 * was bit-identical with grain declared versus grain forced to zero, because
 * three gates ship neutral -- a Surface must be chosen, Depth must move off
 * zero, and the preset must declare a Tooth -- and the controls live inside a
 * panel that ships `display:none`.
 *
 * DEPTH 0.20 IS MEASURED. Swept against grain-off, fraction of painted pixels
 * differing by more than 8/255, and total ink change:
 *
 *     depth   Charcoal      Pastel        Pencil        Basic Round
 *     0.10     9.1% / -5%    1.5% / -5%    1.1% / -4%    0.0% / -1%
 *     0.20    31.2% /-10%   18.1% /-10%   71.3% / -9%    0.0% / -2%
 *     0.30    40.4% /-16%   34.0% /-14%   92.7% /-13%    5.4% / -2%
 *     0.45    48.3% /-23%   48.4% /-22%   97.7% /-20%   56.5% / -4%
 *
 * 0.20 is where the textured presets first show real grain while the smooth
 * ones are still untouched. Above it the ink cost climbs faster than the
 * texture does, and by 0.45 the surface has started eating Basic Round, which
 * declares a Tooth of only 0.15 and should stay clean at a modest setting.
 *
 * THE FIRST VERSION OF THAT SWEEP WAS WRONG AND CHOSE 0.10. It did not reset
 * the seeded RNG between the grain-off and grain-on runs, so Charcoal's
 * stipple -- Density 0.95, consuming the random stream -- diverged between
 * them and 72% of pixels "changed". Almost all of it was RNG divergence rather
 * than grain, and the default was picked off that number. A measurement that
 * compares two runs of a stochastic preset has to hold the stream still.
 *
 * A preset that declares no Tooth is untouched at ANY depth: `paperReveal`
 * multiplies by `S.brushGrain`, and Pixel Perfect declares 0.0 (Hard Ink did
 * too, until the owner cut it at the first V2 sign-off).
 * Sketch Light shows no visible grain until about 0.45 for an honest reason --
 * its stroke sits near alpha 25, so a 20% modulation is under the visibility
 * floor. That is recorded rather than chased.
 */
const DEFAULT_PAPER = { texture: "fine", scale: 1, depth: 0.20 };

// ========================================================================
// STATE
// ========================================================================
const S = {
    // Display canvas — set by boot(), not owned by core
    canvas: null, ctx: null,

    // Document dimensions (independent of viewport)
    W: 768, H: 768,

    // Layer stack: {id, name, type, canvas, ctx, visible, opacity, blendMode, locked}
    // type: "reference" | "paint" | "adjustment"
    layers: [],
    activeLayerIdx: 1,
    nextLayerId: 0,

    // Inpaint mask — separate from layer stack
    mask: { canvas: null, ctx: null, visible: true, opacity: 0.5 },

    // Live painting — AI preview, separate from layer stack and undo
    livePreview: { canvas: null, ctx: null, active: false },

    // Current tool state
    tool: "brush",
    brushSize: 5,        // 1-100 (percentage slider)
    // What `brushSize` MEANS. "relative" is the owner-ratified curve against
    // the document's short side and is the default for everything; only the
    // Pixel family opts into "document_pixels", where Size is a literal
    // document-pixel diameter. See `brushPx()`.
    brushSizeMode: "relative",
    brushOpacity: 1,
    // CT3 made Flow a control; the owner SCRAPPED it at the first V2 sign-off
    // (2026-09-29): "Opacity is the only strength setting." It is back to
    // what the Extension always had -- 1, with no control -- and survives only
    // as the internal per-dab channel that pressure, speed and opacity jitter
    // move (`pOp()`, V2's flow buckets).
    brushFlow: 1,
    // CT3. Whether stamps ACCUMULATE within a single stroke.
    //
    // Off, a stamp can only raise a pixel to its own alpha, so going back over
    // your own wet stroke changes nothing -- which is what a hard brush has
    // always done here.
    // On, each stamp adds to what is already there, so a slow hand or a
    // scrubbing motion darkens. That is the airbrush behaviour, and it did not
    // exist in any form: no field, no control, no code.
    //
    // Default false, because false is what shipped.
    brushBuildup: false,
    brushHardness: 1.0,
    brushPreset: "round", // "round" | "flat" | "marker" | "scatter" | "custom"
    brushRatio: 1.0,      // 0.1-1.0 — ellipse ratio (1.0 = circle)
    // CT3. Tip ANGLE, in degrees, and the retirement of a magic number.
    //
    // The stamp angle used to be `followStroke ? _saSmooth : 0.4` -- and with
    // Follow stroke off, every flat and marker stamp was rotated by a
    // hardcoded 0.4 radians, about 23 degrees, that nothing explained and
    // nothing could change. A flat brush is defined by the angle you hold it
    // at; there was no way to hold it.
    //
    // Added to the stroke angle rather than replacing it, so Follow stroke
    // still means "align to the direction of travel" and Angle offsets it --
    // which is how a real chisel nib behaves. With Follow stroke off it is the
    // absolute angle. Default 0, which is what a round tip has always drawn at
    // and what the 0.4 should have been.
    brushAngle: 0,        // 0-360 degrees, offset from the stroke direction
    // CT3. TAPER IN: the stroke starts narrow and reaches full width over the
    // first stretch of travel.
    //
    // IN ONLY, and the control says so. Taper OUT needs the END of the stroke,
    // which is not known until pointer-up -- by which time every stamp is
    // already in the alpha map. Faking it would mean re-rendering the whole
    // stroke at commit, so the live stroke and the committed one would differ,
    // and a brush that changes when you let go is worse than one that does not
    // taper out.
    brushTaperIn: 0,      // 0-1, fraction; 0 = off
    brushSpikes: 2,       // 2-12 — star/spike count (2 = normal circle)
    brushFalloff: "default", // "default" | "gaussian" | "soft"
    brushDensity: 1.0,    // 0.05-1.0 — stipple density (1.0 = solid)
    // BE10. How strongly THIS brush finds the paper, 0-1. Set by the
    // preset, because "Pencil and Pastel share the same paper and reveal it
    // differently" is the whole acceptance criterion.
    brushGrain: 0,
    // P. Which material family THIS brush is, or null: "pencil", "charcoal"
    // or "pastel" for the dry media, whose paper RESPONSE and strands (M1a)
    // the approved study gives (DEC-BRUSH) -- light pressure catches only the
    // grain's high points, heavier pressure fills it, Pastel never fully --
    // and "bristle" for Bristle Rake's lanes (M1b). Brush V2 reads it; Legacy
    // ignores it and paints exactly as before.
    brushMaterial: null,
    // SR1-5. How hard a MOUSE stroke presses, for the responses a pen's
    // pressure would drive in Brush V2: the paper's grain threshold, dry-media
    // dust and crumbs, bristle lanes. The approved material study read a mouse
    // as p = 0.5; V2 read it as a full press, so the grain filled in and Pencil
    // "looked nothing like the tests". The owner asked for it to be a setting
    // (Settings > Canvas). 0.5 is the study's medium press; 1 is a full press.
    mousePress: 0.5,
    // BE10. The DOCUMENT's paper. Travels with the document through recovery,
    // not with the brush and not with the session.
    //
    //   texture  a name in PAPER_LIBRARY, or "none"
    //   scale    0.25-8, feature size
    //   depth    -1 to 1, SIGNED: + reveals peaks, - reveals valleys, 0 is off
    paper: Object.assign({}, DEFAULT_PAPER),
    // BE11. The brush's dynamics rules. EMPTY IS NEUTRAL and is the default:
    // a preset that declares none behaves exactly as it did before this
    // package, which is an acceptance criterion rather than a nicety.
    brushCurves: [],
    // BE12. Whether THIS brush deposits on a timer while held still.
    // Declared by the preset, like `buildup` and `grain` and `curves`
    // before it, and written unconditionally for the same reason.
    brushAirbrush: false,
    // BE13. ALIASED coverage: a cell is in or out, no falloff. A method
    // on the ordinary dab pipeline rather than a second engine, and
    // available to the eraser for free because the eraser uses the same
    // stamp.
    brushAliased: false,
    // BE13. PIXEL PERFECT: drop the corner cell of an L so a bend is one
    // pixel thick. Only in effect inside the regime it supports -- see
    // `pixelPerfectActive` -- and the control says so rather than
    // silently doing nothing.
    brushPixelPerfect: false,
    color: "#000000",
    bgColor: "#ffffff",
    maskColor: "#ff0000",

    // Pressure
    pressureSensitivity: false,
    pressureAffects: "none", // "size" | "opacity" | "both" | "none"

    // Brush dynamics
    brushDynamics: {
        sizeJitter: 0, opacityJitter: 0, scatter: 0,
        rotationJitter: 0, followStroke: true, spacing: 0.08
    },

    // Custom brush tip (grayscale Uint8Array)
    customTip: { data: null, width: 0, height: 0 },

    // Stabilizer (smoothing)
    smoothing: 4,

    // Tool strength (smudge, blur, dodge/burn, magic wand tolerance)
    toolStrength: 0.5,

    // Liquify sub-modes and spacing
    liquifyMode: "move",    // "move" | "pinch" | "bloat" | "twirl_cw" | "twirl_ccw"
    liquifySpacing: 0.2,    // fraction of brush diameter between dabs (0.2 = 20%)

    // Symmetry
    symmetry: "none", // "none" | "h" | "v" | "both" | "radial"
    symmetryAxes: 4,  // N axes for radial mode (2-16)

    // Eyedropper
    sampleRadius: 1,   // 1-11 px
    sampleMerged: false,

    // Drawing state
    drawing: false,
    lastResult: null,
    lastSettings: null,
    ready: false,

    // Stroke buffers
    stroke: {
        canvas: null, ctx: null, alphaMap: null,
        dirty: { x0: 0, y0: 0, x1: 0, y1: 0 },
        points: [], stampPoints: [],
        lx: 0, ly: 0, lp: 0.5,
        _cachedImg: null
    },

    // Undo/redo
    undoStack: [], redoStack: [], maxUndo: 100,

    // Color history
    colorHistory: ["#000000", "#ffffff"],

    // Smudge
    smudgeBuffer: null,

    // Zoom/pan
    zoom: {
        scale: 1, ox: 0, oy: 0,
        panning: false, panStartX: 0, panStartY: 0,
        panOxStart: 0, panOyStart: 0
    },

    refLoaded: false,
    studioMode: "Create",  // "Create" | "Edit" | "img2img"
    inpaintMode: "Inpaint", // "Inpaint" | "Regional"
    // WHICH DOCUMENT, AND WHICH VERSION OF IT. AR2.1.
    //
    // Studio QUEUES: /api/generate returns 202 and the job runs later, so the
    // owner can paint, undo, resize or open something else while it waits.
    // Without these two fields nothing on the request says which Canvas state
    // it captured, and a queued job can execute against pixels nobody chose.
    // The Extension has no equivalent because its generate route consumes the
    // payload in the same call and has no such interval.
    //
    // OPAQUE, and never a filename or path -- identity must not become a
    // channel for the filesystem.
    documentId: "",
    // Strictly increasing for the life of the document. Undo does NOT rewind
    // it: going back to earlier pixels is still a new state of the document,
    // and reusing a number would let two different states share an identity.
    canvasRevision: 0,
    editingMask: false,
    maskOperation: "add",
    _maskReturnTool: "brush",
    generating: false,
    _canvasDirty: false, // Set true on any user edit — tracks whether canvas has been touched

    // Selection
    selection: {
        active: false, rect: null, mask: null,
        dragging: false, startX: 0, startY: 0,
        marchOffset: 0, animId: null,
        lassoPoints: null,
        _isLasso: false, _isEllipse: false,
        _isMaskBased: false, _contour: null
    },

    // Clipboard
    clipboard: null,

    // Transform
    transform: {
        active: false, bounds: null, originalData: null, layerIdx: -1,
        dragMode: null, dragStart: null, origBounds: null,
        canvas: null, ctx: null, rotation: 0,
        flipH: false, flipV: false, aspectLock: false
    },

    // Regions (v3.0 Regional Inpainting)
    regions: [],
    activeRegionId: null,
    regionMode: false,
    _nextRegionId: 1,

    // AR state
    arLocked: false, arRatio: null,

    // Dodge/burn sub-mode
    _dodgeMode: "dodge",

    // Gradient/shape sub-modes
    _gradientMode: "linear",
    _shapeMode: "rect",
    _shapeFilled: false,

    // Clone stamp
    _cloneSource: null,
    _cloneOffset: null,

    // Poly lasso points
    _polyPoints: null,

    // Internal flags
    _resizeSuppressed: false,

    // Display options
    showGrid: false
};

// ========================================================================
// COMPOSITOR CACHE
// ========================================================================
let _compositeCache = null;
let _compBuffer = null, _compCtx = null;

// E0. Scratch for the transient eraser preview, sized to the STROKE, never to
// the document. Grows to fit the stroke's accumulated rectangle and is capped
// at the document; it is never shrunk, because a stroke's rectangle only grows
// and reallocating per move is the cost this avoids. Declared here rather than
// hidden inside the helper so its lifetime is visible next to the other
// compositor buffers.
let _eraseScratch = null, _eraseScratchCtx = null;

// Developed-buffer cache. Stores the post-layer-composite + post-develop
// pixel buffer keyed on _compositeVersion. Cursor mousemoves don't bump
// the version, so they hit this cache and skip the entire layer loop +
// develop pipeline — that's what kept the cursor responsive when develop
// was non-identity. Bumped by markCompositeDirty() from any path that
// mutates layers, develop params, regions, masks, or canvas dims.
let _compBufCache = null;
let _compBufCacheVer = -1;
let _compositeVersion = 0;

// ========================================================================
// U2 — PRESENTATION DIRTY, OWNED BY CANVAS
// ========================================================================
//
// What the DISPLAY still owes, as opposed to what the document has done.
// `_compositeVersion` answers "did anything change"; this answers "where",
// which is the question a bounded upload has to ask.
//
// FULL IS THE SAFE DEFAULT AND THE DEFAULT IS WHAT `markCompositeDirty` DOES.
// Every existing caller keeps its exact meaning -- "I changed something,
// somewhere" -- and gets a full invalidation, so no site that was correct
// before becomes wrong by omission. Only a caller that can NAME its region
// opts into the bounded path, by calling `markCompositeDirtyRegion`.
//
// THE GENERATION IS WHAT STOPS AN UPDATE BEING LOST. A consumer takes the
// pending region, uploads it, and acknowledges the generation it took. If the
// document changed while that upload was in flight, the generation has moved
// and the acknowledgement is REFUSED, so the region stays pending for the next
// frame. Without it, an edit that lands mid-upload is silently dropped and the
// display is stale until something unrelated happens to invalidate it.
let _presentFull = true;
let _presentX0 = 0, _presentY0 = 0, _presentX1 = 0, _presentY1 = 0;
let _presentGeneration = 0;

function _presentIsEmpty() {
    return !_presentFull && _presentX1 <= _presentX0;
}

/**
 * Anything changed, somewhere. The whole display is owed.
 *
 * The pending rectangle is CLEARED rather than left behind. A full
 * invalidation that still reported the last region's coordinates would hand a
 * consumer a box that looks meaningful and is not -- and a consumer that read
 * the box without checking the flag would upload precisely the wrong thing.
 * Found because a mutation of the full-dominance shortcut changed nothing
 * observable, which is what a shortcut that is only an optimisation looks like.
 */
function markCompositeDirty() {
    _compositeVersion++;
    _presentFull = true;
    _presentX0 = 0; _presentY0 = 0; _presentX1 = 0; _presentY1 = 0;
    _presentGeneration++;
}

/**
 * Something changed HERE. Half-open, document space, unioned with what is
 * already pending.
 *
 * A region that arrives while a full invalidation is pending is absorbed by it
 * -- full dominates, because a consumer that owes the whole display already
 * owes this rectangle.
 */
function markCompositeDirtyRegion(x0, y0, x1, y1) {
    _compositeVersion++;
    _presentGeneration++;
    // An OPTIMISATION, not a correctness guard, and labelled so because a
    // mutation proved it: with it removed the union still happens, `_presentFull`
    // stays true, and the reported state is unchanged. It skips pointless work
    // for a consumer that already owes everything.
    if (_presentFull) return;
    x0 = Math.max(0, Math.floor(x0));
    y0 = Math.max(0, Math.floor(y0));
    x1 = Math.min(S.W, Math.ceil(x1));
    y1 = Math.min(S.H, Math.ceil(y1));
    if (x1 <= x0 || y1 <= y0) return;
    if (_presentX1 <= _presentX0) {
        _presentX0 = x0; _presentY0 = y0; _presentX1 = x1; _presentY1 = y1;
        return;
    }
    if (x0 < _presentX0) _presentX0 = x0;
    if (y0 < _presentY0) _presentY0 = y0;
    if (x1 > _presentX1) _presentX1 = x1;
    if (y1 > _presentY1) _presentY1 = y1;
}

/** What the display owes. Does NOT clear -- see `acknowledgePresentation`. */
function takePresentationDirty() {
    return {
        full: _presentFull,
        empty: _presentIsEmpty(),
        x0: _presentX0, y0: _presentY0, x1: _presentX1, y1: _presentY1,
        generation: _presentGeneration,
    };
}

/**
 * "I have presented everything that was pending as of `generation`."
 *
 * Refused, and the pending region kept, if anything has changed since. Returns
 * whether the acknowledgement was accepted, so a consumer can tell the
 * difference between "done" and "go round again".
 */
function acknowledgePresentation(generation) {
    if (generation !== _presentGeneration) return false;
    _presentFull = false;
    _presentX0 = 0; _presentY0 = 0; _presentX1 = 0; _presentY1 = 0;
    return true;
}

/**
 * Force a full invalidation without pretending a document edit happened.
 *
 * CLEARS THE PENDING RECTANGLE, for the same reason `markCompositeDirty` does:
 * a full invalidation that still carried the previous region's coordinates
 * hands a consumer a box that looks meaningful and is not.
 *
 * FOUND IN THE BROWSER, not by a unit test. U2's headless suite covered this
 * invariant for `markCompositeDirty` and not for this function, so it shipped
 * half-closed -- and a half-closed invariant is worse than an absent one,
 * because a reader who checks one site reasonably assumes the other. The U2-V
 * journeys hit it twice: once when a region flatten was refused under a
 * non-local Develop setting, and once after a WebGL context restore, both
 * reporting `full: true` alongside a live-looking rectangle.
 */
function invalidatePresentation(reason) {
    _presentFull = true;
    _presentX0 = 0; _presentY0 = 0; _presentX1 = 0; _presentY1 = 0;
    _presentGeneration++;
    return reason || "unspecified";
}

function getCompositeVersion() { return _compositeVersion; }

// Stroke angle tracking (for follow-stroke rotation)
let _sa = 0, _saSmooth = 0;

// Cursor position in document space (for cursor rendering by UI layer)
let _cx = -1, _cy = -1;

// ========================================================================
// PSD BLEND MODE MAPS
// ========================================================================
const _blendToPS = {
    "source-over": "normal", "multiply": "multiply", "screen": "screen",
    "overlay": "overlay", "darken": "darken", "lighten": "lighten",
    "color-dodge": "color dodge", "color-burn": "color burn",
    "hard-light": "hard light", "soft-light": "soft light",
    "difference": "difference", "exclusion": "exclusion",
    "hue": "hue", "saturation": "saturation", "color": "color",
    "luminosity": "luminosity"
};
const _blendFromPS = {};
Object.keys(_blendToPS).forEach(k => _blendFromPS[_blendToPS[k]] = k);

const ALL_BLEND_MODES = [
    ["source-over", "Normal"], ["multiply", "Multiply"], ["screen", "Screen"],
    ["overlay", "Overlay"], ["darken", "Darken"], ["lighten", "Lighten"],
    ["color-dodge", "Color Dodge"], ["color-burn", "Color Burn"],
    ["hard-light", "Hard Light"], ["soft-light", "Soft Light"],
    ["difference", "Difference"], ["exclusion", "Exclusion"],
    ["hue", "Hue"], ["saturation", "Saturation"], ["color", "Color"],
    ["luminosity", "Luminosity"]
];

// ========================================================================
// REGION COLORS
// ========================================================================
const REGION_COLORS = [
    "#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6",
    "#a855f7", "#ec4899", "#14b8a6", "#f59e0b", "#8b5cf6"
];

// ========================================================================
// ADJUSTMENT LAYER DEFAULTS
// ========================================================================
// New v2 param shapes — see _migrateAdjustParams below for v1 → v2 conversion.
// brightness/contrast/saturation/lightness are integer percent (-100..100).
// levels black/white are 0..1 floats (UI shows 0..255), gamma is 0.1..9.99.
const _adjustDefaults = {
    "brightness": { _version: 2, brightness: 0, contrast: 0 },
    "hue":        { _version: 2, hue: 0, saturation: 0, lightness: 0, model: "HSL", colorize: false },
    "levels":     { _version: 2, levInBlack: 0, levInWhite: 1, levGamma: 1, levOutBlack: 0, levOutWhite: 1 }
};

// Idempotent migration from legacy adjustParam shapes to v2.
// Legacy: brightness/contrast/saturation/lightness stored as -1..1 floats; HSL had no
// `model` or `colorize` fields. v2 stamps `_version: 2` and rescales the affected fields.
function _migrateAdjustParams(adjustType, params) {
    const def = _adjustDefaults[adjustType];
    if (!def) return params || {};
    const p = Object.assign({}, def, params || {});
    if (p._version === 2) return p;
    if (adjustType === "brightness") {
        p.brightness = Math.round((p.brightness || 0) * 100);
        p.contrast   = Math.round((p.contrast   || 0) * 100);
    } else if (adjustType === "hue") {
        p.saturation = Math.round((p.saturation || 0) * 100);
        p.lightness  = Math.round((p.lightness  || 0) * 100);
        if (typeof p.model    !== "string")  p.model    = "HSL";
        if (typeof p.colorize !== "boolean") p.colorize = false;
    }
    p._version = 2;
    return p;
}
//: BE14. FIFTEEN PRESETS, AND EVERY FIELD ON EVERY ONE.
//:
//: The brief requires each preset to "explicitly reset every supported field",
//: and until this package `applyBrushPreset` wrote nine of the fifteen. The six
//: it skipped -- ratio, spikes, density, angle, taperIn, falloff -- are exactly
//: the six BE6 proved alive by rendering, and exactly the six no preset had
//: ever set. That was ONE defect, not two coincidences: they leaked between
//: presets, so no preset dared use them.
//:
//: BE1's own harness worked around it. `reset()` in be1_measure.js clears "the
//: six leaking fields" by hand, with a comment saying the harness must not
//: inherit the bug it is measuring.
//:
//: WHAT IS NEW, AND WHY EACH ONE EARNS ITS PLACE:
//:
//:   Fine Liner     Smoothing 8 against Hard Ink's 1 -- the stabiliser BE9
//:                  rebuilt, used as a brush property rather than a setting
//:   Ink Wash       buildup + gaussian falloff + a speed curve: pools where
//:                  the hand lingers, which BE12's Airbrush does in time and
//:                  this does in travel
//:   Calligraphy    Follow stroke OFF with an absolute Angle. That IS what a
//:                  held nib is, and it is the first shipped use of BE6's
//:                  Angle control
//:   Charcoal       BE10's paper at full tooth, with Density and a gaussian
//:                  falloff. "Only after BE10" was the brief's condition
//:   Pastel         the same paper at nearly full tooth, revealed completely
//:                  differently -- soft, buildup, dense. The pair is the
//:                  acceptance criterion for BE10, shipped as two presets
//:   Bristle Rake   the first shipped use of Spikes, on a flat tip
//:
//: MASK HARD AND MASK SOFT DO NOT SHIP, and the reason is unchanged from CT3:
//: mask mode forces the tip round and the hardness to 1, and `exportMask`
//: binarises at alpha > 0. A soft mask preset could not paint a soft mask, and
//: Mask Hard alone would be a preset for the only behaviour there is. BE10
//: reinforces it: paper is deliberately not applied in mask mode, because a
//: binarised grainy mask is a mask full of holes.
//:
//: SMUDGE DOES NOT SHIP EITHER. Its consumer does not exist -- smudge reads
//: `toolStrength`, not this contract -- and CT10 owns that migration.
const DEFAULT_BRUSH_PRESETS = [
    {
        name: "Basic Round",
        desc: "An everyday round brush. Soft edge, full strength.",
        preset: "round", size: 12, hardness: 85, opacity: 100,
        smoothing: 3, grain: 0.15, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 1.0, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.06 },
        curves: [
            { input: "pressure", target: "size", curve: "easeOut", min: 0.35, max: 1, fallback: 1 },
            { input: "pressure", target: "flow", curve: "linear", min: 0.6, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Soft Round",
        desc: "A wide feathered tip for soft, light shading.",
        preset: "round", size: 24, hardness: 0, opacity: 40,
        smoothing: 4, grain: 0.1, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 1.0, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.06 },
        curves: [
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.15, max: 1, fallback: 1 },
        ],
    },
    //: HARD INK AND FINE LINER WERE CUT by the owner at the first Brush V2
    //: sign-off (2026-09-29): "Reads like Basic Round but hard. Cut." and
    //: "Reads like basic round but smaller. Cut." Their names resolve to Basic
    //: Round below; engine harnesses that measured a hard round tip through
    //: them use `tests/studio_alpha/removed_presets_fixture.js`.
    {
        name: "Pencil",
        desc: "Graphite. Catches the paper's ridges and skips the pits.",
        preset: "round", size: 3, hardness: 90, opacity: 48,
        smoothing: 2, grain: 0.85, buildup: false, material: "pencil",
        airbrush: false, aliased: false, pixelPerfect: false,
        // SR3-4. 0.70, the approved study's Pencil, is what the round-2 panel
        // showed and the owner approved; it ships with V2 as the default.
        // Density 1 is the plain round, and the bar's Density scrub gets it.
        ratio: 1.0, spikes: 2, density: 0.7, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0.05, opacityJitter: 0.1, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.08 },
        curves: [
            { input: "pressure", target: "size", curve: "linear", min: 0.5, max: 1, fallback: 1 },
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.25, max: 1, fallback: 1 },
            { input: "speed", target: "flow", curve: "linear", min: 1, max: 0.6, fallback: 0 },
        ],
    },
    //: SKETCH LIGHT WAS CUT at the owner's second V2 sign-off (2026-09-29):
    //: "Too transparent ... make density a visible setting and cut this cuz
    //: Pencil can do it." Density is on the brush's context bar now; the name
    //: resolves to Pencil below.
    {
        name: "Airbrush",
        desc: "Deposits while held still. Hold in place to build up.",
        preset: "round", size: 30, hardness: 0, opacity: 7.5,
        smoothing: 6, grain: 0.05, buildup: true,
        airbrush: true, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 1.0, angle: 0, taperIn: 0,
        falloff: "gaussian",
        dynamics: { sizeJitter: 0, opacityJitter: 0.05, scatter: 0.1,
                    rotationJitter: 0, followStroke: true, spacing: 0.06 },
        curves: [
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.1, max: 1, fallback: 1 },
            { input: "speed", target: "flow", curve: "linear", min: 1, max: 0.45, fallback: 0 },
        ],
    },
    {
        name: "Ink Wash",
        desc: "A broad dilute wash. Settles slowly and pools where you linger.",
        preset: "round", size: 48, hardness: 0, opacity: 4.55,
        smoothing: 5, grain: 0.35, buildup: true,
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 1.0, angle: 0, taperIn: 0.35,
        falloff: "gaussian",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.05 },
        curves: [
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.3, max: 1, fallback: 1 },
            { input: "speed", target: "flow", curve: "linear", min: 1, max: 0.5, fallback: 0 },
        ],
    },
    {
        name: "Flat Chisel",
        desc: "A chisel nib. Wide across its travel, thin along it.",
        preset: "flat", size: 22, hardness: 92, opacity: 59.5,
        smoothing: 3, grain: 0.5, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        // G1 (owner decision 2026-09-28, option B). A following tip's Angle is
        // an OFFSET from the heading, and a flat tip's long axis is its local
        // x -- so at 0 this chisel was dragged lengthwise, drawing a line 0.3x
        // its size, contrary to the description above, and sweeping its full
        // length round every sharp corner. 90 presents the broad face across
        // the travel, which is what the description has always said.
        ratio: 1.0, spikes: 2, density: 1.0, angle: 90, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.08 },
        curves: [
            { input: "tilt", target: "ratio", curve: "linear", min: 1, max: 0.35, fallback: 0 },
            { input: "pressure", target: "flow", curve: "linear", min: 0.5, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Marker",
        desc: "Solvent ink from a squared nib. Skips when hurried.",
        preset: "marker", size: 30, hardness: 100, opacity: 90,
        smoothing: 2, grain: 0.15, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        // G1. Same reason as Flat Chisel: at 20 the squared nib was dragged
        // nearly lengthwise, a 30 px marker drawing a 10 px line whose stamp
        // ends showed down its length. 110 keeps the declared 20-degree slant
        // but presents the broad face across the travel.
        ratio: 1.0, spikes: 2, density: 1.0, angle: 110, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.06 },
        curves: [
            { input: "speed", target: "flow", curve: "linear", min: 1, max: 0.72, fallback: 0 },
        ],
    },
    {
        name: "Calligraphy",
        desc: "A held nib. Angle is fixed, so width follows direction.",
        preset: "flat", size: 22, hardness: 100, opacity: 100,
        smoothing: 3, grain: 0.2, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 0.6, spikes: 2, density: 1.0, angle: 45, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: false, spacing: 0.04 },
        curves: [
            { input: "pressure", target: "size", curve: "linear", min: 0.55, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Charcoal",
        desc: "Broad and broken. Finds every ridge in the paper.",
        preset: "round", size: 22, hardness: 35, opacity: 63,
        smoothing: 2, grain: 1.0, buildup: false, material: "charcoal",
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 0.55, angle: 0, taperIn: 0,
        falloff: "gaussian",
        dynamics: { sizeJitter: 0.12, opacityJitter: 0.1, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.05 },
        curves: [
            { input: "pressure", target: "size", curve: "linear", min: 0.45, max: 1, fallback: 1 },
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.3, max: 1, fallback: 1 },
            { input: "speed", target: "flow", curve: "linear", min: 1, max: 0.7, fallback: 0 },
        ],
    },
    {
        name: "Pastel",
        desc: "Soft chalk. Lays colour into the tooth and builds.",
        preset: "round", size: 30, hardness: 15, opacity: 38.25,
        smoothing: 3, grain: 0.95, buildup: true, material: "pastel",
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 0.8, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0.06, opacityJitter: 0, scatter: 0.05,
                    rotationJitter: 0, followStroke: true, spacing: 0.05 },
        curves: [
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.25, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Scatter Dust",
        desc: "Flung particles. Density and spacing decide what lands.",
        preset: "scatter", size: 25, hardness: 50, opacity: 48,
        smoothing: 1, grain: 0.6, buildup: false,
        airbrush: false, aliased: false, pixelPerfect: false,
        ratio: 1.0, spikes: 2, density: 0.5, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0.3, opacityJitter: 0.2, scatter: 0.5,
                    rotationJitter: 0.5, followStroke: false, spacing: 0.15 },
        curves: [
            { input: "speed", target: "flow", curve: "easeOut", min: 1, max: 0.5, fallback: 0 },
            { input: "pressure", target: "size", curve: "linear", min: 0.6, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Bristle Rake",
        desc: "A splayed tip that leaves separated strokes.",
        preset: "flat", size: 26, hardness: 80, opacity: 72,
        smoothing: 2, grain: 0.55, buildup: false, material: "bristle",
        airbrush: false, aliased: false, pixelPerfect: false,
        // G1. "Separated strokes" need the bristles side by side ACROSS the
        // travel, which is also how the approved material direction draws its
        // lanes (DEC-BRUSH). At 0 the splay was dragged lengthwise.
        ratio: 0.55, spikes: 6, density: 0.85, angle: 90, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0.05, scatter: 0,
                    rotationJitter: 0, followStroke: true, spacing: 0.04 },
        curves: [
            { input: "pressure", target: "flow", curve: "easeIn", min: 0.35, max: 1, fallback: 1 },
        ],
    },
    {
        name: "Pixel Perfect",
        //: Sign-off round 1: "Unsure what this actually does." Kept by the
        //: owner; the description now says what it is FOR.
        desc: "For pixel art. Exactly one document pixel, no soft edge, no doubled corners.",
        preset: "round", size: 1, hardness: 100, opacity: 100,
        smoothing: 0, grain: 0.0, buildup: false,
        airbrush: false, aliased: true, pixelPerfect: true,
        sizeMode: "document_pixels",
        ratio: 1.0, spikes: 2, density: 1.0, angle: 0, taperIn: 0,
        falloff: "default",
        dynamics: { sizeJitter: 0, opacityJitter: 0, scatter: 0,
                    rotationJitter: 0, followStroke: false, spacing: 1.0 },
        curves: [],
    },
];

//: Renamed presets keep their old names, because a preset name is a KEY.
//: The per-tool memory and any recovered document may carry it, and
//: `applyBrushPreset` returns false on an unknown name and changes
//: NOTHING -- so an owner's saved choice would stop resolving quietly
//: rather than loudly, which is the worse of the two failures.
const BRUSH_PRESET_ALIASES = {
    "Soft Brush": "Soft Round",
    "Flat Shader": "Flat Chisel",
    "Bold Marker": "Marker",
    "Pixel": "Pixel Perfect",
    //: Cut, not renamed (sign-off round 1): the nearest shipped brush, so a
    //: saved choice still resolves instead of silently changing nothing.
    "Hard Ink": "Basic Round",
    "Fine Liner": "Basic Round",
    //: Cut at sign-off round 2: the same medium, and Density is now on the bar.
    "Sketch Light": "Pencil",
};

//: BE10. `grain` is how strongly this preset finds the document's paper,
//: 0 to 1, multiplied into the document's own Depth. It is what makes the
//: paper a brush property rather than a document filter -- the acceptance
//: criterion is that two presets on the SAME paper at the SAME place reveal
//: it differently, which a single document-wide strength cannot do.
//:
//:   Basic Round  0.15  a plain round brush lays down ink; it finds the tooth a little and no more
//:   Pencil       0.85  the archetype. Graphite only touches what stands up
//:   Soft Brush   0.1   a soft edge floods the pits before it dries
//:   Hard Ink     0.0   ink floods the tooth completely. Zero is a statement, not an omission
//:   Airbrush     0.05  atomised paint settles into the pits as readily as onto the peaks
//:   Flat Shader  0.5   a broad dry edge skips across the surface
//:   Scatter Dust 0.6   already a broken mark; the paper decides where the pieces land
//:   Bold Marker  0.15  solvent ink floods, but a fast marker still skips
//:   Sketch Light 0.9   the lightest touch in the set, so it reaches only the crests
//:   Pixel        0.0   BE2 CONTRACT. One literal document pixel at full alpha on every document. A height map would dim it by position
//:
//: Airbrush is the one definition that means BUILDUP, and it is the reason
//: buildup exists: a low flow that accumulates the longer you hold. Set here
//: rather than in the literal above so the diff says which preset changed
//: behaviour and why.

//: BE12. And it is the one that deposits ON A TIMER, which is what an airbrush
//: is and what this preset has been named for without doing since long before
//: this programme started. Set here for the same reason `buildup` is: the diff
//: says which preset changed behaviour and why.
//:
//: It DEPENDS on the line above. Buildup is what lets a held brush accumulate;
//: without it the max-blend path clamps every additional dab to the same value
//: and a timer would deposit invisibly.

/**
 * Apply one preset by name. Returns true if it was found.
 *
 * Writes S directly. Every field below has a consumer in the stroke engine --
 * that is the whole point of the package this arrived in, and a preset that
 * set something inert would be the same defect one level up.
 *
 * `buildup` defaults to false rather than being left alone: a preset that did
 * not mention it would otherwise inherit whatever the last one set, and
 * "Hard Ink behaves differently depending on what you picked before it" is not
 * a preset system.
 */
function applyBrushPreset(name) {
    // BE14. An old name resolves to its current one. A preset name is a KEY --
    // the per-tool memory and any recovered document may carry it -- and the
    // failure without this is QUIET: `applyBrushPreset` returns false and
    // changes nothing, so an owner's saved brush would silently stop applying
    // rather than raise anything.
    const resolved = (typeof BRUSH_PRESET_ALIASES !== "undefined"
        && BRUSH_PRESET_ALIASES[name]) || name;
    const p = DEFAULT_BRUSH_PRESETS.find(entry => entry.name === resolved);
    if (!p) return false;
    S.brushPreset = p.preset;
    // Mode BEFORE size, and written for EVERY preset rather than only the one
    // that declares it. A preset that left this alone would inherit the last
    // one's mode, and Size 12 would mean 12 literal pixels instead of 205 --
    // the same state-leak defect BE1's guards exist to catch, reintroduced by
    // the package that was supposed to know better.
    S.brushSizeMode = p.sizeMode === "document_pixels" ? "document_pixels" : "relative";
    S.brushSize = p.size;
    S.brushHardness = p.hardness / 100;
    S.brushOpacity = p.opacity / 100;
    // FLOW WAS SCRAPPED at the owner's first V2 sign-off (2026-09-29):
    // "Opacity is the only strength setting." Each preset's flow was folded
    // into its opacity, which keeps a single pass exactly and keeps the
    // build-up presets' per-pass target (`flow * opacity`) exactly. Flow stays
    // only as the INTERNAL per-dab channel that pressure, speed and opacity
    // jitter move, so every preset starts it at 1.
    S.brushFlow = 1;
    S.brushBuildup = !!p.buildup;
    // BE10. Written unconditionally, like `brushSizeMode` above and for the
    // same reason: a preset that left this alone would inherit the last one's
    // tooth, and "Hard Ink is grainy if you picked Pencil first" is the exact
    // state-leak defect BE1 exists to catch.
    S.brushGrain = p.grain != null ? p.grain : 0;
    // P. Unconditional for the same reason: Hard Ink must not answer the paper
    // like charcoal because the owner picked Charcoal first.
    S.brushMaterial = p.material || null;
    // BE11. Written for EVERY preset and DEEP COPIED, and both halves matter.
    //
    // Unconditional, for the third time in this programme: BE2 added
    // `brushSizeMode` that way, BE10 added `brushGrain` that way, and BE1's
    // entire guard set exists because a control that silently inherits the
    // previous preset's value is indistinguishable from one that does not
    // work.
    //
    // Copied, because a shared array would let a later edit to the live rules
    // edit the PRESET TABLE -- a leak that survives a document reload and that
    // nothing in the UI would ever explain.
    S.brushCurves = (p.curves || []).map(r => Object.assign({}, r));
    // BE12. Unconditional, for the same reason as the four fields above
    // it. A preset that left this alone would keep depositing on a timer
    // because the owner happened to pick Airbrush first.
    S.brushAirbrush = !!p.airbrush;
    // BE13. The sixth and seventh fields to need the unconditional-write
    // rule. A preset that left these alone would paint aliased because
    // the owner picked Pixel earlier in the session.
    S.brushAliased = !!p.aliased;
    S.brushPixelPerfect = !!p.pixelPerfect;
    S.smoothing = p.smoothing;
    const d = p.dynamics || {};
    S.brushDynamics = {
        sizeJitter: d.sizeJitter || 0,
        opacityJitter: d.opacityJitter || 0,
        scatter: d.scatter || 0,
        rotationJitter: d.rotationJitter || 0,
        followStroke: d.followStroke !== false,
        spacing: d.spacing || 0.08,
    };
    // BE14. THE SIX THAT LEAKED.
    //
    // `ratio`, `spikes`, `density`, `angle`, `taperIn` and `falloff` were the
    // only supported fields this function did not write -- and they are
    // exactly the six BE6 proved alive by rendering, and exactly the six no
    // shipped preset had ever set. That is ONE defect rather than two
    // coincidences: they leaked between presets, so no preset dared use them.
    //
    // Crank Ratio to 4 on Scatter Dust, pick Hard Ink, and Hard Ink was
    // elliptical until the owner noticed. BE1's whole guard set exists for
    // this class, and its own harness had to work around it: `reset()` in
    // be1_measure.js clears "the six leaking fields" by hand, with a comment
    // saying the harness must not inherit the bug it is measuring.
    //
    // Written with the same explicit defaults the state block declares, so a
    // preset that omits one is reset rather than left alone.
    S.brushRatio = p.ratio != null ? p.ratio : 1.0;
    S.brushSpikes = p.spikes != null ? p.spikes : 2;
    S.brushDensity = p.density != null ? p.density : 1.0;
    S.brushAngle = p.angle != null ? p.angle : 0;
    S.brushTaperIn = p.taperIn != null ? p.taperIn : 0;
    S.brushFalloff = p.falloff || "default";
    return true;
}

// ========================================================================
// BE10 — DOCUMENT PAPER
// ========================================================================
//
// The largest remaining difference between "a transparent round stamp" and a
// material brush. Everything before this package changed the SHAPE of a mark;
// paper changes what the mark lands ON.
//
// THREE PROPERTIES, and each is a decision rather than an accident.
//
// THE PAPER IS THE DOCUMENT'S, NOT THE BRUSH'S. Real paper does not move when
// you change pencils. Two strokes crossing the same point reveal the same
// fibres because the height map is indexed by DOCUMENT coordinates -- so it
// also cannot slide under a pan or breathe under a zoom, which a screen-space
// texture would.
//
// IT IS APPLIED ONCE, AT MERGE. Not per dab. A per-dab grain multiplies with
// every overlap: at Spacing 0.04 a slow hand crosses the same fibre thirty
// times, each pass takes its share of the reduction, and the grain washes out
// exactly where the owner pressed hardest. Applied to ACCUMULATED coverage the
// bound is absolute -- no number of dabs can climb past `a * reveal` -- which
// is the same argument BE4 made for the selection, in the same loop.
//
// THE HEIGHT MAPS ARE GENERATED, NOT SHIPPED. Four procedural papers, built
// from a seeded hash. That is not a shortcut around finding image assets: it
// means there is no asset to download, no licence to track, and -- the
// requirement that actually forced it -- NO FILESYSTEM PATH anywhere in
// document or session state. A paper is a name and two numbers.
//
// WHAT THIS IS NOT. There is no fluid paint, no pigment mixing, no wet edge
// and no impasto. A height map gates coverage; it does not move pigment.

const PAPER_TILE = 512;

//: Integer hash. Deterministic across machines and runs -- `Math.random` here
//: would mean the same document showed different fibres after a reload.
function _paperHash(x, y, seed) {
    let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(seed | 0, 1274126177);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function _paperSmooth(t) { return t * t * (3 - 2 * t); }

/**
 * Value noise on a lattice whose period DIVIDES the tile.
 *
 * That is the whole reason the tile wraps. A lattice that does not divide the
 * tile leaves a discontinuity at the edge, and since the tile repeats every
 * 512 document pixels the discontinuity is not a subtle artefact -- it is a
 * ruled line across the canvas every 512 pixels, in both directions.
 */
function _paperNoise(x, y, period, seed) {
    const cell = PAPER_TILE / period;
    const fx = x / cell, fy = y / cell;
    const ix = Math.floor(fx), iy = Math.floor(fy);
    const tx = _paperSmooth(fx - ix), ty = _paperSmooth(fy - iy);
    const x0 = ((ix % period) + period) % period, x1 = (x0 + 1) % period;
    const y0 = ((iy % period) + period) % period, y1 = (y0 + 1) % period;
    const a = _paperHash(x0, y0, seed), b = _paperHash(x1, y0, seed);
    const c = _paperHash(x0, y1, seed), d = _paperHash(x1, y1, seed);
    const top = a + (b - a) * tx;
    return top + ((c + (d - c) * tx) - top) * ty;
}

function _paperFbm(x, y, basePeriod, octaves, seed) {
    let sum = 0, amp = 1, total = 0, period = basePeriod;
    for (let o = 0; o < octaves; o++) {
        if (period < 1) break;
        sum += _paperNoise(x, y, Math.max(1, Math.round(period)), seed + o * 101) * amp;
        total += amp;
        amp *= 0.5;
        period *= 2;
    }
    return total > 0 ? sum / total : 0.5;
}

//: A whole number of cycles across the tile, for the same wrapping reason.
function _paperRidge(v, cycles) {
    return 0.5 + 0.5 * Math.sin(v * (2 * Math.PI) * Math.max(1, Math.round(cycles)));
}

//: Four papers, each a different KIND of surface rather than the same noise at
//: four amplitudes -- which is the mistake this whole programme exists to
//: stop. Measured tile standard deviations: 36 / 56 / 34 / 51.
const PAPER_LIBRARY = {
    fine: {
        label: "Fine Grain",
        build(x, y, s) {
            const n = _paperFbm(x, y, 64 / s, 3, 11);
            const speck = _paperNoise(x, y, Math.max(1, Math.round(256 / s)), 77);
            return n * 0.55 + speck * 0.45;
        },
    },
    canvas: {
        label: "Canvas",
        build(x, y, s) {
            // THE THREADS WANDER, and the first version's did not. A clean
            // product of two sines renders as a printed halftone grid rather
            // than as cloth -- visible immediately in the contact sheet at a
            // 60px brush, and not at all in any of the numbers. Displacing the
            // ridge coordinate by a low-frequency noise gives the threads the
            // slight irregularity that reads as woven.
            //
            // The displacement wraps because `_paperNoise` does and the ridge
            // takes a whole number of cycles, so the tile still tiles.
            const wob = 6 / s;
            const wx = x + (_paperNoise(x, y, Math.max(1, Math.round(32 / s)), 91) - 0.5) * wob;
            const wy = y + (_paperNoise(x, y, Math.max(1, Math.round(32 / s)), 97) - 0.5) * wob;
            // `max` of two ridges, not a sum: a weave is threads crossing OVER
            // one another, so the high point where they meet belongs to
            // whichever is on top, and the low points are the gaps between.
            const warp = _paperRidge(wx / PAPER_TILE, 64 / s);
            const weft = _paperRidge(wy / PAPER_TILE, 64 / s);
            return Math.max(warp, weft) * 0.62 + _paperFbm(x, y, 24 / s, 3, 23) * 0.38;
        },
    },
    rough: {
        label: "Rough Tooth",
        build(x, y, s) {
            const big = _paperFbm(x, y, 8 / s, 4, 37);
            const tooth = _paperNoise(x, y, Math.max(1, Math.round(128 / s)), 53);
            return big * 0.65 + tooth * 0.35;
        },
    },
    laid: {
        label: "Laid",
        build(x, y, s) {
            // Laid lines are genuinely regular -- they are wire impressions --
            // but perfectly straight ones read as a barcode. The same wander
            // as the weave, at a third of the amplitude.
            const wy = y + (_paperNoise(x, y, Math.max(1, Math.round(24 / s)), 103) - 0.5) * (2 / s);
            const lines = _paperRidge(wy / PAPER_TILE, 96 / s);
            const chain = _paperRidge(x / PAPER_TILE, 16 / s);
            return lines * 0.42 + chain * 0.16 + _paperFbm(x, y, 48 / s, 3, 59) * 0.42;
        },
    },
};

//: One tile at a time. Rebuilt only when the paper or its scale changes --
//: about 20ms, which is why it is not rebuilt per stroke and certainly not per
//: dab.
let _paperCache = { key: "", tile: null };

/**
 * The height map for the current paper, or null when there is none.
 *
 * Normalised to the full 0-255 range so that Depth means the same thing
 * whichever paper is chosen. Without it a low-contrast paper would need Depth
 * 1 to show at all while a high-contrast one blew out at 0.3, and the control
 * would be reading the paper rather than the owner.
 */
function paperTile(name, scale) {
    const p = PAPER_LIBRARY[name];
    if (!p) return null;
    const s = Math.max(0.25, Math.min(8, scale || 1));
    const key = name + "@" + s;
    if (_paperCache.key === key && _paperCache.tile) return _paperCache.tile;

    const raw = new Float32Array(PAPER_TILE * PAPER_TILE);
    let lo = Infinity, hi = -Infinity;
    for (let y = 0; y < PAPER_TILE; y++) {
        for (let x = 0; x < PAPER_TILE; x++) {
            const v = p.build(x, y, s);
            raw[y * PAPER_TILE + x] = v;
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
    }
    const span = hi - lo > 1e-6 ? hi - lo : 1;
    const tile = new Uint8Array(PAPER_TILE * PAPER_TILE);
    for (let i = 0; i < raw.length; i++) {
        const t = Math.round(((raw[i] - lo) / span) * 255);
        tile[i] = t < 0 ? 0 : (t > 255 ? 255 : t);
    }
    _paperCache = { key: key, tile: tile };
    return tile;
}

/**
 * The reveal multiplier table for the current paper and this brush, or null
 * when paper is off.
 *
 * A 256-entry LOOKUP rather than arithmetic per pixel: the reveal depends only
 * on the height byte, so the whole curve can be evaluated 256 times instead of
 * once per pixel in the dirty rectangle. BE8 spent this package's budget
 * getting the merge loop down; paper is not allowed to give it back.
 *
 * THE CURVE. `reveal = 1 - strength * (1 - h)`, which is Krita's "soft
 * texturing" MULTIPLY -- `mul(unionShapeOpacity(src, inv(strength)), dst)` in
 * `KisMaskingBrushCompositeOp.h` -- written out. It has the property the
 * plain multiply lacks: at strength 0 it is exactly 1, so a neutral control is
 * neutral by construction rather than by a rounding accident.
 *
 * THE SIGN. Depth is signed and the sign is not decoration. Positive reveals
 * the PEAKS -- paint catches the raised fibres and skips the pits, which is a
 * pencil on rough paper. Negative reveals the VALLEYS -- paint sinks into the
 * tooth and misses the crests, which is a wash settling. Same paper, same
 * location, opposite marks.
 */
function paperReveal() {
    const P = S.paper;
    if (!P || !P.texture || P.texture === "none") return null;
    // Per-preset strength. A preset that declares 0 is unaffected by paper --
    // an ink pen does not find the tooth -- and that has to be reachable, or
    // "the presets differ" would be a claim about names again.
    const strength = Math.abs(P.depth || 0) * (S.brushGrain != null ? S.brushGrain : 1);
    if (strength <= 0) return null;
    const tile = paperTile(P.texture, P.scale);
    if (!tile) return null;
    const invert = (P.depth || 0) < 0;
    const lut = new Float32Array(256);
    for (let i = 0; i < 256; i++) {
        const h = (invert ? 255 - i : i) / 255;
        lut[i] = 1 - strength * (1 - h);
    }
    return { tile: tile, lut: lut };
}

// ========================================================================
// BE11 — DYNAMICS CURVES
// ========================================================================
//
// What makes two presets behave differently rather than merely look different
// in a list. Everything before this package gave a preset a fixed shape and a
// fixed weight; this lets a preset say how it RESPONDS.
//
// THE SMALLEST CONTRACT THAT IS STILL A CONTRACT:
//
//     { input, target, curve, min, max, fallback }
//
//     v      = available ? input : fallback
//     shaped = CURVE[curve](v)             // 0..1, analytic
//     factor = min + (max - min) * shaped  // the classic range
//
// ANALYTIC CURVES RATHER THAN A CONTROL-POINT TABLE. "Deterministic
// interpolation" is the requirement, and a named closed-form function is
// deterministic by construction: there is no interpolation code to get wrong,
// it serialises as a string, and it cannot drift between two implementations.
// The curve EDITOR is explicitly out of scope for V1, so a point list would be
// a serialisation format with no producer.
//
// EVERY RULE CARRIES ITS OWN FALLBACK, and that is the whole reason this is
// not three lines shorter. CT2 wrote `pressureAvailable` for exactly this:
//
//     "a pressure dynamic must not act on a substituted value, or every mouse
//      stroke would be drawn as though the owner pressed exactly half way."
//
// The substituted value IS 0.5 -- a mouse reports 0.5 while a button is down.
// So a naive pressure curve on a mouse does not merely guess, it guesses the
// middle of the range, and a size rule mapped 0.2 to 1.0 would draw every
// mouse stroke at 60% width for a reason the owner cannot see anywhere.
//
// A rule that wants full width on a mouse says `fallback: 1`. A rule that
// wants the light end says `fallback: 0`. Neither is assumed.

//: The inputs, and how honest each one is about being available.
//:
//: DIRECTION IS FREE: BE7 already advances a smoothed heading per dab so the
//: tip can point where the stroke is going, and this reads it.
//:
//: SPEED needs a timestamp, which `plotTo` has never been given. The pointer
//: handler has one on every sample and drops it; `noteSample` carries it in.
//: Where no timestamp arrives -- every headless driver, and any caller that
//: has not been taught -- speed reports unavailable and the rule's own
//: fallback applies, which is the same rule the pen inputs follow rather than
//: a special case for the test harness.
const DYN_INPUTS = ["pressure", "speed", "direction", "tilt"];

//: The targets.
//:
//: TWO OF THE BRIEF'S FIVE ARE ABSENT, and both for reasons that come from
//: earlier packages rather than from taste.
//:
//: `opacity` -- `S.brushOpacity` is applied ONCE when the stroke commits and
//: bounds the whole stroke. That is BE5's contract and the reason Flow exists
//: as a separate control. A per-dab curve on it would have to choose which dab
//: it was right about. Flow IS the per-dab quantity and is offered instead.
//:
//: `grain` -- the brief says "and grain reveal WHERE JUSTIFIED", and here it is
//: not. BE10 applies the paper once, to accumulated coverage, specifically so
//: that overlap cannot wash the texture out; a per-dab grain factor would put
//: back the amount-versus-rate defect that package removed, in the package
//: immediately after it. A per-STROKE grain curve would be defensible, but it
//: is a second evaluation timing and would need its own contract.
const DYN_TARGETS = ["size", "flow", "angle", "ratio"];

//: How fast a hand has to move for the speed input to read 1, in DOCUMENT
//: pixels per millisecond.
//:
//: 2 px/ms is about 2,000 px/s, which is a quick flick across a 1024 document
//: in half a second. Admitted as chosen rather than derived, in the same terms
//: as BE9's STAB_SCREEN_PX_PER_STEP: it is a feel constant, and dressing it up
//: as a measurement would be worse than saying so.
const DYN_SPEED_REF = 2;

//: Five shapes and a flat. Each is monotonic on 0..1, each maps 0 to 0 and 1
//: to 1 except `flat`, and none of them allocates.
const DYN_CURVES = {
    linear:  v => v,
    easeIn:  v => v * v,
    easeOut: v => 1 - (1 - v) * (1 - v),
    sShape:  v => v * v * (3 - 2 * v),
    //: Late and sudden: nothing until most of the way, then quickly. This is
    //: the one that makes a light sketch pass read as a sketch.
    sharp:   v => v * v * v * v,
    //: Ignores the input entirely and returns the top of the range. Its use is
    //: to say "this target is pinned" in a preset without deleting the rule.
    flat:    () => 1,
};

//: Neutral, and shared. Never handed out where a caller could keep it: every
//: consumer reads the fields immediately.
const DYN_NEUTRAL = { size: 1, flow: 1, angle: 0, ratio: 1 };

/**
 * The live pen sample, carried from the pointer handler.
 *
 * NOT the event: this file has no opinion about PointerEvent and CT2 owns
 * that. It takes the normalised sample CT2 already produces.
 *
 * Reset by `beginStroke`, so a stroke that starts without one behaves as
 * though nothing is available -- which is exactly what it is.
 */
function noteSample(s) {
    if (!s) return;
    const st = S.stroke;
    if (!st) return;
    st._pen = {
        pressureAvailable: !!s.pressureAvailable,
        tiltAvailable: !!s.tiltAvailable,
        tiltX: s.tiltX || 0,
        tiltY: s.tiltY || 0,
        time: typeof s.time === "number" ? s.time : null,
    };
}

/**
 * One input's value, and whether it was measured.
 *
 * Returns a NUMBER or null. Null means "no measurement", and every caller
 * turns that into the rule's own declared fallback rather than into a value of
 * its own choosing.
 */
function _dynInput(name, ctx) {
    const pen = (S.stroke && S.stroke._pen) || null;
    switch (name) {
        case "pressure":
            // The AVAILABILITY flag, not the value. A mouse reports a
            // perfectly well-formed 0.5 and it means nothing.
            if (!pen || !pen.pressureAvailable) return null;
            return ctx.pressure;
        case "tilt":
            if (!pen || !pen.tiltAvailable) return null;
            // Magnitude, 0 (upright) to 1 (flat at 90 degrees). Direction of
            // lean is a separate question and no target asks it.
            return Math.min(1, Math.hypot(pen.tiltX, pen.tiltY) / 90);
        case "direction":
            // Always available: it is a property of the path, not of the
            // device. Mapped over a full turn so a rule can favour one axis.
            return ((ctx.heading % (Math.PI * 2)) + Math.PI * 2)
                % (Math.PI * 2) / (Math.PI * 2);
        case "speed":
            if (ctx.speed === null || ctx.speed === undefined) return null;
            return Math.min(1, ctx.speed / DYN_SPEED_REF);
        default:
            return null;
    }
}

/**
 * The per-dab modifiers for the current brush.
 *
 * `ctx` carries what only the dab loop knows: this dab's pressure, the heading
 * BE7 smoothed for it, and the segment speed if a timestamp reached the
 * engine.
 *
 * See DYN_TARGETS for why `opacity` and `grain` are not among them.
 */
function applyDynamics(ctx) {
    const rules = S.brushCurves;
    // The overwhelmingly common case, and it costs one length check.
    if (!rules || !rules.length) return DYN_NEUTRAL;
    const out = { size: 1, flow: 1, angle: 0, ratio: 1 };
    for (let i = 0; i < rules.length; i++) {
        const r = rules[i];
        if (!r || out[r.target] === undefined) continue;
        const measured = _dynInput(r.input, ctx);
        const v = measured === null
            ? (typeof r.fallback === "number" ? r.fallback : 0)
            : measured;
        const shape = DYN_CURVES[r.curve] || DYN_CURVES.linear;
        const clamped = v < 0 ? 0 : (v > 1 ? 1 : v);
        const min = typeof r.min === "number" ? r.min : 0;
        const max = typeof r.max === "number" ? r.max : 1;
        const factor = min + (max - min) * shape(clamped);
        // ANGLE IS ADDED, IN DEGREES. A multiplicative angle is meaningless --
        // zero degrees times anything is zero degrees -- and BE7 already
        // treats the tip angle as a sum of three separate terms for the same
        // reason. Everything else multiplies.
        if (r.target === "angle") out.angle += factor;
        else out[r.target] *= factor;
    }
    return out;
}

//: Reused rather than allocated per dab. A 4096-dab stroke would otherwise
//: allocate 4096 short-lived objects inside the loop BE8 spent its whole
//: budget on. Written immediately before every read and never retained.
const _dynCtx = { pressure: 1, heading: 0, speed: null };

/**
 * This segment's speed, in DOCUMENT pixels per millisecond, or null.
 *
 * NULL IS THE HONEST ANSWER and not a failure: `plotTo` has never taken a
 * timestamp, so every headless driver and every caller that has not been
 * taught reports no time at all. A speed rule then applies its own declared
 * fallback -- the same treatment pressure and tilt get on a mouse -- rather
 * than a number this function picked on the rule's behalf.
 *
 * Measured per SEGMENT and not per dab. The dabs along one segment are
 * interpolated positions between two real samples; they share the hand
 * movement that produced them, and a per-dab speed would be an invention with
 * a plausible shape.
 */
function _segmentSpeed(dist) {
    const st = S.stroke;
    if (!st) return null;
    const now = (st._pen && typeof st._pen.time === "number") ? st._pen.time : null;
    const prev = st._lastTime;
    st._lastTime = now;
    if (now === null || typeof prev !== "number") return null;
    const dt = now - prev;
    if (!(dt > 0)) return null;
    return dist / dt;
}

// ========================================================================
// BE12 — AIRBRUSH, IN TIME
// ========================================================================
//
// The preset has been called Airbrush since before this programme started, and
// until now the name was a claim the engine could not support. An airbrush
// deposits paint WHILE IT IS HELD OVER A SPOT. Studio deposited when the
// pointer moved, because `plotTo` is the only thing that stamps and only a
// pointermove calls it -- so holding perfectly still did nothing at all.
//
// THE RATE MUST NOT DEPEND ON THE CALLBACK, and this is the third time that
// sentence has been written in this programme. BE3 removed it from spacing,
// BE9 removed it from smoothing, and here it is again wearing a timer:
//
//     setInterval(() => stamp(), 16);      // WRONG
//
// `setInterval` is a request, not a promise. A busy frame, a background tab, a
// throttled window -- the callbacks arrive when they arrive, and a stroke held
// for one second would deposit however many the browser felt like delivering.
//
// So time carries a DEBT, exactly as distance does in `plotTo`. A callback
// that arrives late deposits everything it owes rather than losing it, and the
// total is `floor(elapsed / interval)` however the callbacks were spaced.
//
// STATIONARY ONLY, and that is a decision rather than a limitation. A moving
// pointer already deposits through BE3's spacing debt; adding time-driven dabs
// on top would make a slow drag darker for two independent reasons at once.
// Krita and Photoshop couple the two and both need a rate control to manage
// the interaction. V1 takes the narrow honest version: holding still deposits,
// and moving deposits exactly what it did before this package.

//: Dabs per second at Flow 1, and the floor the interval cannot go below.
//:
//: 60/s is one dab per frame at 60Hz, which is as fast as anything on screen
//: can be seen to change. Scaled DOWN by Flow so that the control the owner
//: already has for "how much paint" also governs "how fast it arrives" -- one
//: control doing one thing, rather than two that interact.
//:
//: Admitted as chosen, in the same terms as BE9's STAB_SCREEN_PX_PER_STEP and
//: BE11's DYN_SPEED_REF.
const AIR_MAX_RATE = 60;
const AIR_MIN_RATE = 4;
//: FLOW WAS SCRAPPED (owner, V2 sign-off round 1), so the owner-facing
//: control this rate was scaled by is gone and every preset starts Flow at 1.
//: Left at 60/s, the Airbrush would puff 6.7x faster than it did. The base is
//: therefore the Airbrush preset's own rate while Flow existed -- 60/s times
//: its Flow of 0.15 -- so it builds exactly as it did; Opacity is now how much
//: each puff lays, and pressure (the internal flow channel) still slows it.
const AIR_BASE_RATE = AIR_MAX_RATE * 0.15;

//: How far the pointer may drift and still count as held still, in DOCUMENT
//: pixels. A hand resting on a tablet is never perfectly still.
const AIR_STILL_PX = 0.75;

//: Injectable, because the acceptance requires deterministic FAKE-CLOCK tests
//: and a timer tested against the real clock is a timer tested against the
//: machine's mood. `setAirbrushClock` is the only writer.
let _airClock = () => (typeof performance !== "undefined" && performance.now
    ? performance.now() : Date.now());

//: A timer that outlives its stroke must be unable to write anything, and
//: clearing the interval is necessary rather than sufficient: a callback can
//: already be queued when `clearInterval` runs. Every tick re-checks that it
//: still belongs to the stroke that started it.
let _airTimer = null;
let _airToken = 0;
let _airDebt = 0;
let _airLast = 0;
let _airHandle = null;

function setAirbrushClock(fn) { _airClock = fn || _airClock; }

/** Dab interval in milliseconds for the current Flow. */
function airbrushInterval() {
    const flow = Math.max(0.01, Math.min(1, S.brushFlow || 1));
    const rate = Math.max(AIR_MIN_RATE, AIR_BASE_RATE * flow);
    return 1000 / rate;
}

/**
 * Start depositing on a timer, if this brush is an airbrush.
 *
 * Called by `beginStroke`. Returns the token, so a test can assert a timer was
 * or was not started without reaching into module state.
 */
function startAirbrush(scheduler) {
    stopAirbrush();
    if (!S.brushAirbrush) return 0;
    if (S.tool !== "brush") return 0;
    if (!S.stroke || !S.stroke.alphaMap) return 0;
    _airToken++;
    _airDebt = 0;
    _airLast = _airClock();
    const token = _airToken;
    const tick = () => airbrushTick(token);
    if (scheduler) {
        // A test injects its own scheduler and drives the ticks by hand.
        _airHandle = scheduler(tick);
        _airTimer = "injected";
    } else if (typeof setInterval === "function") {
        _airTimer = setInterval(tick, Math.max(4, airbrushInterval() / 2));
    }
    return token;
}

/**
 * Stop, and make any queued callback harmless.
 *
 * The token bump is the half that matters. Clearing the interval leaves a
 * callback that was already scheduled, and that callback would otherwise stamp
 * into a stroke that has been committed -- or into the alpha map of a document
 * the owner has since switched away from.
 */
function stopAirbrush() {
    if (_airTimer === "injected") {
        if (typeof _airHandle === "function") { try { _airHandle(); } catch (e) {} }
    } else if (_airTimer !== null && typeof clearInterval === "function") {
        clearInterval(_airTimer);
    }
    _airTimer = null;
    _airHandle = null;
    _airToken++;
    _airDebt = 0;
}

/**
 * One deposition tick. Returns the number of dabs it laid down.
 *
 * Exported so the fake-clock tests can drive it directly. That is not a test
 * hook bolted on: the scheduler is the only part of this that a browser owns,
 * and everything a browser owns is the part a test cannot reach.
 */
function airbrushTick(token) {
    if (token !== _airToken) return 0;             // a stale callback
    if (!S.drawing || !S.stroke || !S.stroke.alphaMap) { stopAirbrush(); return 0; }
    if (S.tool !== "brush") { stopAirbrush(); return 0; }

    const now = _airClock();
    const elapsed = now - _airLast;
    _airLast = now;
    if (!(elapsed > 0)) return 0;

    // SR1-4. UNDER BRUSH V2 THE CLOCK IS THE SAME AND THE HAND IS V2'S. The
    // pointer's moves go to V2, not `plotTo`, so `S.stroke.lx/ly` stay at the
    // contact point; and a Legacy dab here would land in the buffer V2
    // overwrites. So V2 says where the pointer is and lays each owed puff.
    const v2 = (typeof window !== "undefined") ? window.StudioBrushV2Adapter : null;
    const v2At = (v2 && v2.isActive && v2.isActive() && v2.pointerAt) ? v2.pointerAt() : null;
    // MOVED means the pointer is depositing through `plotTo` already, so the
    // time debt is discarded rather than banked -- banking it would pay out a
    // burst the moment the hand stopped.
    const x = v2At ? v2At.x : S.stroke.lx, y = v2At ? v2At.y : S.stroke.ly;
    const px = S.stroke._airX, py = S.stroke._airY;
    S.stroke._airX = x; S.stroke._airY = y;
    if (px !== undefined && Math.hypot(x - px, y - py) > AIR_STILL_PX) {
        _airDebt = 0;
        return 0;
    }

    const interval = airbrushInterval();
    _airDebt += elapsed;
    // Bounded, so a tab that was backgrounded for a minute does not return and
    // dump 3,600 dabs into one spot. The cap is one second of deposition,
    // which is the most a hand could have meant to lay down while away.
    if (_airDebt > 1000) _airDebt = 1000;
    let laid = 0;
    while (_airDebt >= interval) {
        _airDebt -= interval;
        // BE16. A HELD airbrush has no direction, and used to deposit at the
        // previous stroke's -- measured at IoU 1.0000 against the leaked
        // heading and 0.2560 against none. `dabRotation` answers 0 when
        // nothing is known, so the held deposit and the opening dab agree.
        // BE17. A TIMED DEPOSIT IS NOT A DAB IN A ROW OF DABS.
        //
        // `plotTo`'s dabs are one pass being laid out in SPACE, so each is
        // worth a fraction of the pass and is normalised by how many of them
        // cover a pixel. The timer's deposits land on the SAME pixel, one
        // after another, and are a rate in TIME -- `airbrushInterval` already
        // derives that rate from Flow. Dividing them by the spatial overlap as
        // well charges them twice: measured, a held Airbrush deposited at
        // 1/17th of its rate and half a second of stillness moved the mark by
        // nothing at all.
        //
        // `timeDepositStep()` is the step at which exactly ONE dab covers a
        // pixel, so the normalisation collapses to identity and a tick
        // deposits Flow. Written as a step rather than as a flag because the
        // units then say what it means.
        if (v2At) v2.airbrushPuff(S);
        else stampWet(x, y, S.stroke.lp, dabRotation(),
            applyDynamics(_airDynCtx(S.stroke.lp)), timeDepositStep());
        laid++;
    }
    // SR2-1. Show it now: a held pointer sends no events to redraw on.
    if (laid && _onAirbrushDeposit) _onAirbrushDeposit();
    return laid;
}

//: The dynamics context for a stationary dab. Speed is explicitly null and not
//: zero: the pointer is not moving, so there is no measured speed, and a rule
//: that wanted one gets its own declared fallback rather than a value this
//: function invented. Direction is the last heading, which is the direction the
//: nozzle is still pointing.
function _airDynCtx(pressure) {
    _dynCtx.pressure = pressure;
    // BE16. Null becomes 0 here rather than the previous stroke's heading. A
    // direction rule on a brush being held still gets "no direction", which is
    // the honest answer and the same one BE11 gives an unavailable pen input.
    _dynCtx.heading = strokeHeading() || 0;
    _dynCtx.speed = null;
    return _dynCtx;
}

// ========================================================================
// BE13 — ALIASED COVERAGE AND PIXEL PERFECT
// ========================================================================
//
// BE2 made the Pixel brush one literal document pixel wide on every document,
// and said in the preset's own comment what it was NOT doing:
//
//     "This is literal SIZING only. Pixel-perfect corner handling, aliased
//      coverage and cell-centre placement are BE13 and are not claimed here."
//
// So today's Pixel brush is an ANTIALIASED one-pixel brush. `dabAlpha` returns
// a smoothstep, the dab carries a fractional alpha at the edge of its own
// cell, and a diagonal run leaves soft shoulders. That is a small thin brush,
// which is exactly what the owner called it.
//
// TWO FEATURES, AND ONLY ONE IS ABOUT THE PIXEL PRESET.
//
// ALIASED COVERAGE is a METHOD on the ordinary dab pipeline -- the brief says
// "not a second engine" and the code makes that easy. `stampAlphaMap`'s loop
// already computes `nd`, the normalised distance in the tip's own frame, and
// already exits on `nd >= 1`. `nd < 1` IS "this cell is inside the tip".
// Aliased coverage is that test without the falloff: one branch, hoisted out
// of the pixel loop like everything else BE8 hoisted.
//
// It belongs to the eraser for free, because the eraser has always used this
// same function -- BE4's note in `commitStroke` records that the two differ at
// commit and nowhere else.
//
// PIXEL PERFECT is a stroke-level filter and has nothing to do with coverage.
// It decides which CELLS get a dab, not what a dab looks like.

//: How wide a brush may be and still be in the regime Pixel Perfect supports.
//:
//: A corner filter on a 40px brush is meaningless: its "cells" are dab centres
//: of a wide tip and dropping one leaves a bite out of the stroke. The brief
//: says "restrict Pixel Perfect to the supported small/one-pixel regime", and
//: the toggle SAYS SO when it is out of regime rather than silently doing
//: nothing -- which is the defect class BE1 exists to catch, and it would be a
//: poor package that reintroduced it while fixing a different one.
const PP_MAX_WIDTH = 1.5;

/**
 * Does this brush walk CELLS rather than arc length?
 *
 * SEPARATED FROM THE CORNER FILTER, and the first version of this package had
 * them as one question. That was wrong in a way the tests caught: with the
 * filter off the engine fell back to BE3's arc-length spacing, so the
 * "unfiltered" baseline had no doubled corners either -- and a comparison
 * where the control case cannot exhibit the defect proves nothing.
 *
 * It is also wrong on its own terms. An aliased one-pixel brush should walk
 * cells whatever the filter is doing: arc-length spacing on a diagonal lands
 * at cells that SKIP, so a fast diagonal drag leaves gaps. That is a defect
 * nobody had named and it belongs to `aliased`, not to Pixel Perfect.
 */
function pixelWalkActive() {
    return !!S.brushAliased
        && (S.tool === "brush" || S.tool === "eraser")
        && brushPx() <= PP_MAX_WIDTH;
}

/** Is Pixel Perfect actually in effect, as opposed to merely switched on? */
function pixelPerfectActive() {
    return !!S.brushPixelPerfect && pixelWalkActive();
}

/**
 * The classic corner test.
 *
 * Where a run goes right-then-down, the corner cell makes the line two pixels
 * thick at the bend. B is that corner when A and C are each orthogonally
 * adjacent to it and A-to-C is a diagonal step.
 */
function _ppIsCorner(a, b, c) {
    const abx = b.x - a.x, aby = b.y - a.y;
    const bcx = c.x - b.x, bcy = c.y - b.y;
    // Each leg exactly one cell, and each along a different axis.
    if (Math.abs(abx) + Math.abs(aby) !== 1) return false;
    if (Math.abs(bcx) + Math.abs(bcy) !== 1) return false;
    return (abx !== 0) !== (bcx !== 0);
}

/**
 * Offer one cell to the filter, and stamp whatever it confirms.
 *
 * DEFERRED, NOT RETRACTED, and this is the constraint the whole design is
 * shaped around. `S.stroke.alphaMap` is an accumulator with no undo of its
 * own, so "paint it and then unpaint it" means writing 0 -- which erases
 * whatever an EARLIER part of the same stroke put there, and cannot tell
 * "this stroke painted it" from "it was already 255".
 *
 * The brief names it -- "avoiding destructive retraction from the coverage
 * buffer" -- and the acceptance says "existing artwork beneath tentative
 * pixels is preserved". A cell that is dropped here was never painted, so
 * there is nothing beneath it to preserve it FROM.
 */
function _ppOffer(cell, stamp) {
    const st = S.stroke;
    // WITHOUT the filter this is a plain cell walk: every cell is committed as
    // it arrives. The walk and the filter are separate features and the
    // separation is what makes the filter measurable -- see `pixelWalkActive`.
    if (!S.brushPixelPerfect) {
        const last = st._ppPrev;
        if (last && last.x === cell.x && last.y === cell.y) return;
        st._ppPrev2 = last;
        st._ppPrev = cell;
        stamp(cell.x, cell.y);
        return;
    }
    const prev = st._ppPrev, prev2 = st._ppPrev2;
    if (prev && prev.x === cell.x && prev.y === cell.y) return;   // no move
    if (prev && prev2 && _ppIsCorner(prev2, prev, cell)) {
        // The corner is dropped. `prev2` stays as the anchor, because the run
        // now goes prev2 -> cell diagonally.
        st._ppPrev = cell;
        return;
    }
    if (prev) { stamp(prev.x, prev.y); st._ppPrev2 = prev; }
    st._ppPrev = cell;
}

/**
 * Stamp the cell still held tentative, if any.
 *
 * Called at the end of a stroke. Without it the last cell of every stroke
 * would be missing -- a defect that would look exactly like the corner filter
 * being too aggressive and would be hard to tell apart from one.
 */
function flushPixelPerfect() {
    const st = S.stroke;
    if (!st || !st._ppPrev) return false;
    // The unfiltered walk stamps as it goes, so its `_ppPrev` is a record of
    // where it has been rather than a cell that is owed. Flushing it would
    // stamp the last cell twice.
    if (!S.brushPixelPerfect) { st._ppPrev = null; st._ppPrev2 = null; return false; }
    const cell = st._ppPrev;
    st._ppPrev = null;
    st._ppPrev2 = null;
    if (!st.alphaMap) return false;
    stampWet(cell.x, cell.y, st.lp, _brushAngleRad(), DYN_NEUTRAL);
    return true;
}

/**
 * Walk from one cell to another, 8-connected, offering every cell.
 *
 * BRESENHAM, because BE3's spacing debt places dabs every `spacing` document
 * pixels ALONG THE PATH -- which on a diagonal lands at cells that skip. A
 * corner filter needs a connected run or it has no corners to find.
 *
 * This also fixes a defect nobody had named: a fast diagonal drag with the
 * Pixel brush leaves gaps today, because arc-length spacing does not
 * guarantee cell adjacency.
 */
function _ppWalk(x0, y0, x1, y1, stamp) {
    let x = x0, y = y0;
    const dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx - dy;
    // Bounded, so a corrupt coordinate cannot spin here. The longest possible
    // run is the document diagonal.
    let budget = dx + dy + 2;
    for (;;) {
        _ppOffer({ x: x, y: y }, stamp);
        if ((x === x1 && y === y1) || budget-- <= 0) return;
        const e2 = 2 * err;
        if (e2 > -dy) { err -= dy; x += sx; }
        if (e2 < dx) { err += dx; y += sy; }
    }
}

// ========================================================================
// COLOR MATH
// ========================================================================
function hexRgb(h) {
    return {
        r: parseInt(h.slice(1, 3), 16),
        g: parseInt(h.slice(3, 5), 16),
        b: parseInt(h.slice(5, 7), 16)
    };
}

function rgbHex(r, g, b) {
    return "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
}

function hsvToRgb(h, s, v) {
    s /= 100; v /= 100;
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    let r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return {
        r: Math.round((r + m) * 255),
        g: Math.round((g + m) * 255),
        b: Math.round((b + m) * 255)
    };
}

function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0, s = max === 0 ? 0 : d / max, v = max;
    if (d !== 0) {
        if (max === r) h = 60 * (((g - b) / d) % 6);
        else if (max === g) h = 60 * ((b - r) / d + 2);
        else h = 60 * ((r - g) / d + 4);
    }
    if (h < 0) h += 360;
    return { h, s: s * 100, v: v * 100 };
}

// ========================================================================
// LAYER MODEL
// ========================================================================
function createLayerCanvas() {
    return _createCanvas(S.W, S.H);
}

function makeLayer(name, type, opts) {
    const c = createLayerCanvas();
    const ctx = c.getContext("2d", { colorSpace: "srgb" });
    return {
        id: S.nextLayerId++, name: name, type: type || "paint",
        canvas: c, ctx: ctx,
        visible: true, opacity: 1, blendMode: "source-over", locked: false,
        ...(opts || {})
    };
}

function makeAdjustLayer(name, adjustType, params) {
    return {
        id: S.nextLayerId++, name: name, type: "adjustment",
        adjustType: adjustType,
        adjustParams: _migrateAdjustParams(adjustType, params),
        visible: true, opacity: 1, blendMode: "source-over", locked: false,
        canvas: null, ctx: null,
        _lutCache: null
    };
}

function activeLayer() {
    // Self-healing: if activeLayerIdx is invalid, reset to last paint layer
    if (S.activeLayerIdx == null || S.activeLayerIdx < 0 || S.activeLayerIdx >= S.layers.length) {
        let fixed = S.layers.findIndex(l => l.type === "paint");
        if (fixed < 0) fixed = 0;
        S.activeLayerIdx = fixed;
        console.warn("[StudioCore] activeLayerIdx was invalid, reset to", fixed);
    }
    return S.layers[S.activeLayerIdx] || S.layers[0];
}
function findLayerIdx(id) { return S.layers.findIndex(l => l.id === id); }

function drawTarget() {
    if (_strokeTransaction && S.drawing) return _strokeTransaction.target;
    if (S.editingMask) return { canvas: S.mask.canvas, ctx: S.mask.ctx };
    const L = activeLayer();
    return { canvas: L.canvas, ctx: L.ctx };
}

function strokeTool() {
    return S.tool === "mask" ? (S.maskOperation === "subtract" ? "eraser" : "brush") : S.tool;
}

function drawColor() { return S.editingMask ? S.maskColor : S.color; }

// ========================================================================
// BRUSH SIZE CONVERSION
// ========================================================================
// Slider 1-100 → pixel radius. Power curve for fine control at small sizes.
/**
 * The Size control's value, resolved to a document-pixel diameter.
 *
 * TWO MODES, because one number cannot mean both things.
 *
 *   relative          the owner-ratified curve. Size is a position on a
 *                     1.5-power scale against the document's SHORT SIDE, so
 *                     the same preset is proportionally the same mark on any
 *                     document. This is what every ordinary brush uses and it
 *                     is deliberately unchanged.
 *
 *   document_pixels   Size IS the diameter, in document pixels. Size 1 is one
 *                     pixel on a 256 square and one pixel on a 24 MP canvas.
 *
 * The second mode exists because the first cannot express a pixel brush. Under
 * the relative curve a 512-square document resolves Sizes 1 and 2 to the same
 * single pixel while a 6000x4000 document resolves Size 1 to four -- so "Pixel"
 * was a brush whose width depended on the canvas it was used on.
 *
 * BE0 measured the thing that made the earlier plan wrong: the `Math.max(2,...)`
 * stamp floor was blamed for this and is innocent. Size 1 already painted
 * exactly one pixel with the floor in place. The curve was the cause.
 *
 * The mode is a property of the PRESET, written explicitly by
 * `applyBrushPreset` for every preset, and remembered per tool alongside the
 * size it belongs to -- a literal 5 and a relative 5 are different marks, and
 * handing one to the other would resize the owner's brush behind their back.
 */
/*
 * WHAT "SIZE N" MEANS IN document_pixels MODE, EXACTLY.
 *
 * N is the dab DIAMETER in document pixels. It is not a promise that the
 * rasterised mark is N pixels wide, and the difference is not hand-waving:
 *
 *     size 1 -> 1 px      size 2 -> 1 px      size 3 -> 3 px
 *     size 4 -> 3 px      size 5 -> 5 px      size 8 -> 7 px
 *
 * At an integer centre a round dab of diameter d covers 2*ceil(d/2)-1 pixels,
 * because the falloff excludes anything at exactly the radius. Only ODD widths
 * are reachable; an even one needs the dab centred on a half pixel, which is
 * cell-centre placement and belongs to BE13 along with aliased coverage and
 * pixel-perfect corners.
 *
 * So even Size steps still produce the same mark as the odd one below them.
 * That is a real remaining limitation, it is covered by an expectedFailure
 * guard naming BE13, and it is stated here rather than left for someone to
 * rediscover by painting.
 *
 * The ordinary relative mode has always had the same property -- Basic Round
 * resolves to 170 and paints 169 -- so the Size label reports the DIAMETER in
 * both modes and stays consistent with what it has always meant.
 */
function brushPx() {
    const v = S.brushSize;
    if (S.brushSizeMode === "document_pixels") {
        // No document term, deliberately: that is the whole contract.
        return Math.max(1, Math.round(v));
    }
    const shortSide = Math.min(S.W, S.H);
    const pct = (Math.pow(v, 1.5) / Math.pow(100, 0.5)) / 100;
    return Math.max(1, Math.round(pct * shortSide));
}

// Pressure-adjusted size/opacity
function pSz(p) {
    const base = brushPx();
    if (!S.pressureSensitivity) return base;
    const v = Math.max(0.1, p);
    return (S.pressureAffects === "size" || S.pressureAffects === "both") ? base * v : base;
}

// The BRUSH's per-stamp value, which is FLOW.
//
// The name is historical and the distinction is the one CT3 turns on:
// `S.brushOpacity` is applied ONCE, when the stroke commits, and bounds the
// whole stroke; this is applied to every stamp. Two different quantities, and
// treating them as interchangeable is the defect this package removes.
//
// `pressureAffects` still says "opacity", and for the brush that means this --
// the per-stamp contribution. Renaming the setting would silently invalidate
// every saved default and every recovered document that carries it, which is a
// worse trade than a name that needs one sentence of explanation.
/** The owner's tip angle in radians. Degrees in the UI, radians in the maths. */
function _brushAngleRad() {
    return ((S.brushAngle || 0) * Math.PI) / 180;
}

/**
 * How wide the tip is, this far into the stroke. 1 means full width.
 *
 * The ramp is measured in brush WIDTHS, not pixels, so a taper looks the same
 * on a 3 px pencil and a 300 px wash -- a pixel-based ramp would be invisible
 * on one and endless on the other.
 *
 * THREE widths at full setting, and that number was measured rather than
 * chosen. At six, a size-24 brush on a 512 px document has a 60 px tip and a
 * 360 px ramp, so a 240 px stroke reached only 25 of its 59 px -- "Taper in
 * 100%" that never finishes is really "permanently thin", and a control whose
 * maximum is unreachable is the sort of thing this package exists to remove.
 * At three it completes inside an ordinary stroke.
 *
 * Floors at 0.12 rather than 0: a stamp scaled to nothing is a gap at the
 * start of every stroke, which reads as a dropped sample rather than a taper.
 */
function _taperFactor(travelled, widthPx) {
    const taper = S.brushTaperIn || 0;
    if (taper <= 0) return 1;
    const ramp = Math.max(1, widthPx * 3 * taper);
    const t = Math.min(1, travelled / ramp);
    return 0.12 + 0.88 * t;
}

function pOp(p) {
    if (!S.pressureSensitivity) return S.brushFlow;
    const v = Math.max(0.1, p);
    return (S.pressureAffects === "opacity" || S.pressureAffects === "both") ? S.brushFlow * v : S.brushFlow;
}

// Per-stamp opacity for tools that have NO commit-time alpha.
//
// The brush composites its whole stroke at S.brushOpacity when the stroke
// commits (commitStroke, "globalAlpha = wasMask ? 1 : S.brushOpacity"), so its
// per-stamp value is FLOW -- an accumulation rate -- and must keep reading
// pOp(). Using the owner's opacity there too would apply it twice.
//
// The eraser replaces its target destructively and the clone draws straight to
// the layer, so neither ever sees a commit-time alpha. For those two the
// per-stamp value IS the opacity the owner set. They read S.brushFlow, which
// the context bar never writes and nothing else assigns, so both sliders moved
// and neither did anything.
function pOpacity(p) {
    if (!S.pressureSensitivity) return S.brushOpacity;
    const v = Math.max(0.1, p);
    return (S.pressureAffects === "opacity" || S.pressureAffects === "both") ? S.brushOpacity * v : S.brushOpacity;
}

// ========================================================================
// BRUSH STAMP ENGINE
// ========================================================================

// Dab alpha falloff with hardness
// Hard/medium brushes (hardness > 0): opaque core + smoothstep fade, uses max-blend
// Soft brush (hardness ≈ 0): Krita Airbrush_Soft curve, low per-dab alpha, uses source-over accumulation
// Error function approximation (Abramowitz & Stegun 7.1.26, max error 1.5e-7)
function _erf(x) {
    const sign = x >= 0 ? 1 : -1;
    x = Math.abs(x);
    const t = 1.0 / (1.0 + 0.3275911 * x);
    const y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return sign * y;
}

// Spike rotation — folds point angle into first sector (Krita's fixRotation)
function _applySpikeRotation(xr, yr, spikes) {
    if (spikes <= 2) return { x: xr, y: yr };
    const spikeAngle = Math.PI / spikes;
    const cs = Math.cos(-2 * spikeAngle);
    const ss = Math.sin(-2 * spikeAngle);
    let angle = Math.atan2(Math.abs(yr), xr);
    let sx = xr, sy = Math.abs(yr);
    while (angle > spikeAngle) {
        const nx = cs * sx - ss * sy;
        const ny = ss * sx + cs * sy;
        sx = nx; sy = ny;
        angle -= 2 * spikeAngle;
    }
    return { x: sx, y: sy };
}

/**
 * A dab's coverage at `dist` from its centre. ONE curve, all hardnesses.
 *
 * There used to be two, with a cliff between them at hardness 0.01:
 *
 *     hardness >= 0.01   smoothstep from a solid core, peaking at 1.0
 *     hardness <  0.01   a hand-written ramp peaking at 0.4
 *
 * So a brush at hardness 0.011 painted a full-strength core and one at 0.009
 * painted a 40% one. Nothing in the control's travel said so, and the research
 * that went looking for the "Krita Airbrush_Soft" curve the comment credited
 * could not find it in Krita. It was invented here.
 *
 * The general branch already handles hardness 0 correctly: `innerR` becomes 0,
 * the solid core shrinks to the centre point, and the smoothstep runs the whole
 * radius. That is a soft brush -- a smooth falloff that still reaches full
 * strength where the dab is thickest. So the special case is deleted rather
 * than replaced.
 *
 * WHY THIS MATTERS BEYOND CONTINUITY, and why it is what let the flow ceiling
 * go: `stampAlphaMap` used to FORCE accumulation for soft tips, with a comment
 * explaining that a max-blend "would cap a soft brush at 40% grey no matter how
 * long you painted". That was true of the 0.4 curve and is the only reason the
 * ceiling existed. With the core reaching 1.0, a max-blended soft dab caps at
 * FLOW, which is what Flow is supposed to mean. The two defects were one.
 */
//: The antialiasing band never narrows below this, in DOCUMENT PIXELS.
//:
//: BE18. In pixels rather than in hardness, because a fraction collapses on a
//: small tip: Fine Liner's whole radius is 3px, and on a 6px tip the band is
//: already effectively gone by hardness 0.99. A floor expressed as "never
//: harder than 0.95" would leave the small tips exactly as aliased as before
//: and soften the big ones for no reason.
const MIN_AA_PX = 1.0;

//: ...and never more than this fraction of the radius, so a small tip
//: keeps a dominant solid core. Binds only below radius 4.
const MAX_AA_FRACTION = 0.25;

/**
 * The hardness a dab actually paints with: never so hard the rim vanishes.
 *
 * BE18. At `hardness === 1.0`, `dabAlpha`'s `innerR` equals `radius`, so every
 * pixel inside the tip returns exactly 1 and every pixel outside returns 0.
 * Coverage is binary and the silhouette is a staircase -- measured at 0.29 to
 * 0.42px of edge RMS on Hard Ink, Fine Liner, Marker and Calligraphy, against
 * 0.301px for PIXEL PERFECT, the preset that declares itself aliased. On their
 * silhouettes those four were indistinguishable from the pixel brush, and so
 * was the default state before any preset is chosen.
 *
 * A `min`, so a brush already softer than the floor is untouched. That is most
 * of the set: Basic Round ships hardness 0.85 on a 10.5px radius and comes
 * through byte-identical.
 *
 * MASK MODE IS EXEMPT, and this is the load-bearing exemption. `stampWet`
 * forces round/hardness-1 when `S.editingMask`, and `exportMask` binarises at
 * `alpha > 0` -- so a 1px antialiased rim would expand every inpaint mask by a
 * pixel in every direction, changing what the model is asked to repaint. The
 * softness would be discarded by the binarisation anyway. A mask edge is a
 * decision boundary, not a mark.
 */
function effectiveHardness(hardness, radius) {
    const h = Math.min(1, Math.max(0, hardness));
    if (S.editingMask) return h;
    if (!(radius > 0)) return h;
    // THE BAND IS BOUNDED AS A FRACTION TOO, and this is not belt-and-braces.
    //
    // A flat one-pixel band is right for an ordinary tip and ruinous for a
    // tiny one: at radius 1.5 it leaves an inner core of 0.5px, so the dab is
    // MOSTLY ramp. Measured before this cap, Hard Ink at Size 3 lost 33.8% of
    // its ink -- the same footprint, painted a third paler -- while every
    // other size moved by under half a percent. Antialiasing a mark is worth a
    // little of its edge; it is not worth a third of the mark.
    //
    // A quarter of the radius keeps the core dominant at every size, and binds
    // only below radius 4, where one pixel was already too much.
    const band = Math.min(MIN_AA_PX, radius * MAX_AA_FRACTION);
    return Math.min(h, (radius - band) / radius);
}

function dabAlpha(dist, radius, hardness) {
    if (dist >= radius) return 0;
    const innerR = radius * hardness;
    if (dist <= innerR) return 1;
    const t = (dist - innerR) / (radius - innerR);
    return 1 - (t * t * (3 - 2 * t));
}

function dabAlphaGauss(dist, radius, hardness) {
    if (dist >= radius) return 0;
    // Gaussian bell curve via erf (from Krita's KisGaussCircleMaskGenerator)
    // Map hardness to fade width — high hardness = narrow bell, low = wide soft
    const fade = Math.max(0.01, 1.0 - hardness * 0.85);
    const center = (2.5 * (6761.0 * fade - 10000.0)) / (1.41421356 * 6761.0 * fade);
    const alphafactor = 1.0 / (2.0 * _erf(center));
    const distfactor = 1.41421356 * 12500.0 / (6761.0 * fade * radius);
    const d = dist * distfactor;
    return alphafactor * (_erf(d + center) - _erf(d - center));
}

// Dispatch to active falloff mode
function _dabFalloff(dist, radius, hardness) {
    switch (S.brushFalloff) {
        case "gaussian": return dabAlphaGauss(dist, radius, hardness);
        default: return dabAlpha(dist, radius, hardness);
    }
}

/**
 * TIP GEOMETRY. One frame, one order of operations, every tip family.
 *
 * Each tip declares only what makes it that tip -- its aspect, its extent and
 * its norm -- and shares everything else. The three functions this replaced
 * each did their own thing and disagreed about which controls exist:
 *
 *     shapeDistRound   took NO angle. Applied ratio, THEN folded for spikes,
 *                      THEN took an isotropic norm.
 *     shapeDistFlat    took an angle. Hardcoded `ry = r * 0.3`, so brushRatio
 *                      could not reach it.
 *     shapeDistMarker  took an angle. Hardcoded `0.8 / 0.35`, same.
 *     scatter          called none of them -- a plain circle, inline, so four
 *                      controls could not reach it at all.
 *
 * THE ORDER IS THE FIX. Rotation preserves an isotropic norm, so folding the
 * angle and THEN measuring `sqrt(dx^2 + dy^2)` cannot change any pixel: Spikes
 * was not unwired, it was algebraically incapable of doing anything. Folding
 * first and applying anisotropy after leaves the fold on a shape rotation does
 * not preserve, and the spikes appear.
 *
 *     translate -> ROTATE by -angle -> FOLD (spikes) -> ANISOTROPY (ratio)
 *                                                    -> the tip's own norm
 *
 * WHAT IS HONESTLY IMPOSSIBLE, asserted rather than promised: a circle has no
 * orientation. On a round tip at Ratio 1 and Spikes 2, Angle changes nothing
 * and cannot. Both become meaningful the moment the tip stops being circular.
 */

//: Half-HEIGHT as a fraction of the dab radius, before the owner's Ratio. This
//: is what makes a flat a flat, and Ratio modulates it, so at Ratio 1.0 every
//: tip is byte-identical to what it was before this package.
//:
//: A fraction of the RADIUS, not of the half-width. The first version of this
//: table said half-width and multiplied `extent` into both axes, which quietly
//: narrowed Bold Marker from 0.35r to 0.28r -- 4,420 painted pixels became
//: 3,836. Nine presets were byte-identical and the tenth was not, which is the
//: only reason it was caught.
const TIP_ASPECT = { round: 1.0, scatter: 1.0, flat: 0.3, marker: 0.35 };

//: Marker is a rectangle, so it measures with a Chebyshev norm rather than a
//: Euclidean one. That is the tip's identity, not a setting.
const TIP_NORM = { marker: "chebyshev" };

//: Marker's long axis was 0.8r rather than r. Preserved, so its footprint does
//: not change: a scale on the tip, not an aspect.
const TIP_EXTENT = { marker: 0.8 };

/** Normalised distance from a dab's centre: 0 at the core, 1 at the edge. */
function tipDistance(px, py, cx, cy, r, ang, kind) {
    return tipFrame(r, ang, kind).at(px - cx, py - cy);
}

/**
 * Everything about a dab's geometry that does NOT vary per pixel, resolved once.
 *
 * BE8. `tipDistance` was called for every pixel of every dab and recomputed all
 * of this each time: two trig calls, four table lookups, two divisions. A
 * 170 px dab is about 22,000 pixels, and BE0's harness measured the cost --
 * stamping went from 2.8% of a pointer-move dispatch to 31.2% across BE1-BE7,
 * a 4.8x regression that this package owns.
 *
 * The per-pixel work is now two multiplies, two more, and a square root.
 *
 * THREE SHORTCUTS, and each is a fact about the geometry rather than a guess:
 *
 *   `rotates`   false when the tip is circular OR the angle is zero. A rotation
 *               of a rotationally symmetric shape is the same shape, which is
 *               exactly what `TIP_CAPABILITIES` already calls `needs-shape`.
 *   `folds`     false at 2 spikes, which is the default and the neutral value.
 *   `chebyshev` resolved once instead of a string compare per pixel.
 *
 * Reciprocals are precomputed so the inner loop multiplies rather than divides.
 */
function tipFrame(r, ang, kind) {
    const ratio = S.brushRatio || 1.0;
    const extent = TIP_EXTENT[kind] || 1.0;
    const aspect = (TIP_ASPECT[kind] ?? 1.0) * ratio;
    const rx = Math.max(0.5, r * extent);
    const ry = Math.max(0.5, r * aspect);
    const spikes = S.brushSpikes || 2;
    const folds = spikes > 2;
    // A circular tip has no orientation to rotate, so the frame transform is
    // the identity and can be skipped outright.
    const circular = Math.abs(rx - ry) < 1e-9;
    const rotates = !!ang && !(circular && !folds);
    const cosA = rotates ? Math.cos(-ang) : 1;
    const sinA = rotates ? Math.sin(-ang) : 0;
    const invRx = 1 / rx, invRy = 1 / ry;
    const chebyshev = TIP_NORM[kind] === "chebyshev";
    return {
        rx, ry, rotates, folds, chebyshev,
        at(dx, dy) {
            let lx = dx, ly = dy;
            if (rotates) {
                lx = dx * cosA - dy * sinA;
                ly = dx * sinA + dy * cosA;
            }
            if (folds) {
                const f = _applySpikeRotation(lx, ly, spikes);
                lx = f.x; ly = f.y;
            }
            const nx = lx * invRx, ny = ly * invRy;
            return chebyshev
                ? (nx < 0 ? -nx : nx) > (ny < 0 ? -ny : ny)
                    ? (nx < 0 ? -nx : nx) : (ny < 0 ? -ny : ny)
                : Math.sqrt(nx * nx + ny * ny);
        },
    };
}

/**
 * The capability matrix, declared rather than discovered.
 *
 * The addendum requires each tip method to state what it supports, and
 * requires that no visible control silently do nothing. `true` here is a
 * PROMISE that BE6's guards verify by rendering; a control absent from a tip's
 * row must be disabled or removed rather than left clickable.
 *
 * `angle` and `spikes` on a round tip are marked `"needs-shape"`: they work,
 * but only once Ratio has given the tip an orientation to rotate. That is a
 * mathematical fact about circles, and it is recorded here so the UI can say
 * so instead of an owner discovering it by painting.
 */
const TIP_CAPABILITIES = {
    round:   { angle: "needs-shape", ratio: true, spikes: "needs-shape", density: true, falloff: true },
    scatter: { angle: "needs-shape", ratio: true, spikes: "needs-shape", density: true, falloff: true },
    flat:    { angle: true, ratio: true, spikes: true, density: true, falloff: true },
    marker:  { angle: true, ratio: true, spikes: true, density: true, falloff: true },
};

// Stamp onto the alpha map
// Hard brushes: per-pixel max (no accumulation within stroke)
// Coverage write: source-over accumulation when Buildup is on, max-blend when
// it is off. No ceiling -- see the BE5 note inside stampAlphaMap.
function stampAlphaMap(cx, cy, sz, opacity, stampAngle, dabStep, pivot) {
    const map = S.stroke.alphaMap;
    if (!map) return;
    // BE13. CELL-CENTRE PLACEMENT, and BE2 left an expectedFailure with
    // this package's name on it:
    //
    //     "At an integer centre a diameter-d round dab covers
    //      2*ceil(d/2)-1 pixels, so Size 2 paints 1 px and Size 4 paints
    //      3. Even widths need the dab centred on a half pixel."
    //
    // The engine's convention is that a pixel's integer index IS its
    // centre. An ODD diameter is therefore symmetric about an integer
    // index and an EVEN one must straddle a cell boundary -- at a
    // half-integer. Snapping to the wrong one of the two is exactly how
    // Size 2 came to paint one pixel.
    //
    // ALIASED ONLY. An antialiased dab spreads across the boundary
    // anyway, and snapping it would quantise every stroke to the pixel
    // grid -- which is a visible change to nine presets in service of a
    // property only the tenth needs.
    if (S.brushAliased) {
        const even = (Math.round(sz) % 2) === 0;
        cx = even ? Math.round(cx - 0.5) + 0.5 : Math.round(cx);
        cy = even ? Math.round(cy - 0.5) + 0.5 : Math.round(cy);
    }
    const r = sz / 2;
    // BE18. THE ONE CALL SITE, and it is one because everything downstream
    // already reads this local: the falloff dispatch, the custom-tip path, and
    // BE17's `profileMean`.
    //
    // That last one is why the floor belongs HERE rather than inside
    // `dabAlpha`. BE17 normalises each dab's deposit by `I`, the mean of the
    // tip's radial profile, using the closed form `(1 + hardness) / 2` derived
    // from `dabAlpha`'s exact shape. Widening the band changes that integral.
    // Handing `profileMean` the EFFECTIVE hardness keeps the closed form exactly
    // right with no second edit -- whereas flooring inside `dabAlpha` would have
    // left `I` describing a profile the engine no longer draws, and quietly
    // broken "one pass deposits exactly Flow".
    const hard = effectiveHardness(S.brushHardness, r);
    const preset = S.brushPreset;
    // The last of the hardcoded 0.4 radians. Every caller inside the stroke
    // engine passes an angle, so this fallback only fires for a direct call,
    // and it now answers with the owner's Angle rather than 23 degrees nobody
    // chose.
    // BE16. The fallback for a direct call with no angle. It read the module
    // global, which can hold a finished stroke's heading.
    const ang = stampAngle !== undefined
        ? stampAngle
        : ((preset === "flat" ? (strokeHeading() || 0) : 0) + _brushAngleRad());
    const w = S.W, h = S.H;
    // CT3. THE THREE QUANTITIES, AND WHY THEY ARE NOT THE SAME ONE.
    //
    //   opacity   S.brushOpacity, applied ONCE at commitStroke as a
    //             globalAlpha. It bounds what the whole stroke can reach on
    //             the layer, whatever happens inside it.
    //   flow      the `opacity` ARGUMENT of this function -- badly named, and
    //             renamed nowhere because `pOp` has carried that name since
    //             the file was inherited. It is what ONE STAMP contributes.
    //   buildup   whether stamps accumulate within the stroke.
    //
    // BE5. BUILDUP IS NOW THE ONLY THING THAT DECIDES WHETHER DABS ACCUMULATE.
    //
    // It used to be `softTip || S.brushBuildup`, with a flow-level ceiling
    // bolted on to stop the forced accumulation running away:
    //
    //     const softTip = hard < 0.01;
    //     const useSoftBlend = softTip || S.brushBuildup;
    //     const ceiling = (useSoftBlend && !S.brushBuildup)
    //         ? Math.min(255, (opacity * 255) | 0) : 255;
    //
    // The comment that justified it said a max-blend "would cap a soft brush at
    // 40% grey no matter how long you painted". That was TRUE, and it was true
    // because `dabAlpha`'s soft branch peaked at 0.4 -- an invented curve, not
    // the Krita one it credited. BE5 removed that branch, so a soft dab now
    // reaches full strength at its core and a max-blend caps it at FLOW.
    //
    // Which means the ceiling has nothing left to do. It was a patch over a
    // falloff bug, and with the falloff fixed the three quantities finally each
    // mean one thing:
    //
    //     flow      what ONE dab contributes            -- the argument here
    //     buildup   whether dabs add to each other      -- this line
    //     opacity   what the whole stroke may reach     -- globalAlpha at commit
    //
    // So a non-buildup stroke reaches exactly Flow however long you paint, and
    // its soft edge stays soft instead of filling in to a flat band -- which is
    // what "Soft Brush looks like it is just 50% opacity" actually was.
    // A buildup stroke keeps adding, bounded only by Opacity at merge.
    const useSoftBlend = S.brushBuildup;

    const x0 = Math.max(0, Math.floor(cx - r));
    const y0 = Math.max(0, Math.floor(cy - r));
    const x1 = Math.min(w - 1, Math.ceil(cx + r));
    const y1 = Math.min(h - 1, Math.ceil(cy + r));

    // Expand dirty rect
    const d = S.stroke.dirty;
    if (x0 < d.x0) d.x0 = x0;
    if (y0 < d.y0) d.y0 = y0;
    if (x1 > d.x1) d.x1 = x1;
    if (y1 > d.y1) d.y1 = y1;

    // ONE coverage loop, shared by every geometric tip.
    //
    // It used to be written out twice with different bodies -- once for
    // scatter's sub-dabs and once for the standard shapes -- and the copies had
    // drifted. Scatter's measured its own inline circular distance and skipped
    // the density test, so brushRatio, brushSpikes, brushAngle and brushDensity
    // all reached the standard branch and none of them reached Scatter Dust:
    // the one preset whose entire character is stipple density could not see
    // the Density control.
    // BE7. DENSITY IS NORMALISED AGAINST SPACING.
    //
    // The skip is per PIXEL PER DAB, and dabs overlap. So the coverage an owner
    // actually sees is the UNION over every dab that touches a pixel, and it
    // rises as spacing tightens even though the Density control has not moved.
    // Measured at Density 0.35:
    //
    //     spacing 0.32 -> 0.698 coverage      spacing 0.04 -> 0.999
    //     spacing 0.16 -> 0.902               spacing 0.02 -> 1.000
    //
    // At close spacing the control does nothing at all: everything fills in.
    // Density and Spacing are supposed to be independent, and they were
    // multiplying.
    //
    // A pixel inside a stroke is covered by roughly `1 / spacingFraction` dabs
    // -- the dab spans 2r and the gap is 2r x fraction -- so the per-dab
    // probability that makes the union equal the requested density is
    //
    //     p = 1 - (1 - density) ^ (1 / overlap)
    //
    // A single isolated stamp is therefore sparser than its Density number
    // suggests. That is the correct trade: Density is a control over how a
    // STROKE reads, and a stroke is what it is used on.
    const requestedDensity = S.brushDensity || 1.0;
    let density = requestedDensity;
    if (requestedDensity < 0.99) {
        const overlap = Math.max(1, 1 / Math.max(0.001, spacingFraction()));
        density = 1 - Math.pow(1 - requestedDensity, 1 / overlap);
    }
    // BE8. Everything invariant across the dab is hoisted out of the pixel
    // loop: the tip frame (see `tipFrame`), the density test, and the falloff
    // dispatch. What remains per pixel is the transform, the falloff and the
    // blend -- and nothing that could have been decided once.
    const stipple = density < 0.99;
    const gaussian = S.brushFalloff === "gaussian";
    // BE13. ALIASED. Hoisted with the rest, because whether a dab is
    // aliased is a property of the brush and not of the pixel.
    //
    // The loop below already computes `nd`, the normalised distance in
    // the tip's own frame, and already exits on `nd >= 1`. So `nd < 1`
    // IS "this cell is inside the tip", and aliased coverage is that
    // test with the falloff removed. Nothing else changes: the same tip
    // frame, the same blend, the same dirty rect.
    const aliased = !!S.brushAliased;
    // BE17. PAINT ACCUMULATES WITHIN A STROKE, AND FLOW STOPS BEING OPACITY.
    //
    // What was here took a MAX, so a stroke capped at exactly Flow and Opacity
    // multiplied once at commit: the rendered mark was the PRODUCT of two
    // interchangeable numbers. Measured, (Flow 35, Opacity 100) and (Flow 100,
    // Opacity 35) differed by ZERO pixels on Basic Round, Soft Round, Hard Ink
    // and Pencil, and painting back and forth over one spot inside a single
    // stroke -- 1, 2, 5, 20 passes -- read 89, 89, 89, 89.
    //
    // FLOW IS WHAT ONE PASS DEPOSITS, not what one dab deposits. If it were the
    // latter, Spacing would silently multiply it -- measured spread across the
    // shipped Spacing slider of 53.3 / 80.6 / 94.5 of 255 at hardness 0 / 0.5 /
    // 1.0 on the old accumulate branch. That is DiVerdi 2.6.1's warning
    // verbatim: "the number of overlapping stamps at a single pixel is
    // determined by a combination of the footprint and the spacing rate".
    //
    // So the per-dab contribution is normalised against how many dabs cover a
    // pixel, using the same algebra the Density block above uses three lines
    // away, and for the same reason: complements multiply.
    //
    //     K      = dabs covering one pixel, weighted by the falloff
    //     f_eff  = 1 - (1 - target) ^ (1/K)
    //
    // `step` is threaded in from the emitter and NOT re-derived here.
    // `spacingFor` clamps at 0.5px, and below that clamp `spacingFraction()`
    // stops describing the gap actually advanced -- re-deriving it costs
    // 46/255 at hardness 0, Spacing 2. The fallback is the isotropic identity,
    // for the callers that have no heading: the opening dab and the airbrush.
    const stepAlong = (typeof dabStep === "number" && dabStep > 0)
        ? dabStep
        : 2 * spacingFraction();
    // Density thins the dab train, so a pixel is touched by fewer than the
    // geometry says. `density` is ALREADY normalised against spacing above and
    // must not be normalised a second time -- it is multiplied in, not re-cooked.
    const overlapK = Math.max(
        1, density * (2 / stepAlong) * profileMean(hard, gaussian));
    // BUILDUP is the only thing left worth a checkbox: whether the Opacity
    // bound applies WITHIN the stroke. Off, the target is Flow and commit
    // multiplies by Opacity once, so the stroke can never exceed Opacity
    // however long it is worked. On, the target is Flow x Opacity and commit
    // multiplies by 1, so one held or scrubbed stroke can reach full black and
    // Opacity acts as a rate. That is the wash/airbrush semantic, and it is
    // what the three presets that set it already claim in their descriptions.
    const target = Math.min(1, Math.max(0,
        useSoftBlend ? opacity * (S.brushOpacity ?? 1) : opacity));
    // AT FLOW 100 THERE IS NOTHING TO ACCUMULATE, and accumulating anyway makes
    // the edge WORSE. One dab already deposits everything the pass may deposit,
    // so the only pixels left to build on are the tip's own antialiased rim --
    // measured, Basic Round lost 1016 of 5200 painted pixels' worth of
    // antialiasing, worst case 86/255. Short-circuited, the six Flow-100
    // presets are byte-identical to what they were.
    const accumulating = target < 0.995;
    const fEff = accumulating
        ? 1 - Math.pow(1 - target, 1 / overlapK)
        : target;
    // THE 16-BIT ACCUMULATOR IS NOT OPTIONAL. Normalisation makes the per-dab
    // contribution small BY DESIGN, and the 8-bit store cannot hold it: at the
    // shipped spacings Airbrush lays 55.6 dabs per pixel and Charcoal 36.7, so
    // at Flow 35 one dab is 0.0077 -- which truncates to 1 of 255, losing 86%
    // -- and at Flow 15 it truncates to ZERO and the brush paints nothing at
    // all. Measured over 16 passes against a target of 206: u8 gives 0 to 192,
    // u16 gives 206.4 to 207.6, f32 gives the same as u16. So u16 it is, two
    // bytes rather than four.
    //
    // Allocated LAZILY and only when something can accumulate, so a Flow-100
    // preset -- six of the sixteen -- never pays for it. BE8's rule about
    // full-document transients is why.
    // TIED TO THE MAP IT ACCUMULATES FOR, not merely to the stroke. A caller
    // that swaps `alphaMap` without clearing this -- which every test harness
    // does, and which `beginStroke` is only one of the ways to do -- would
    // otherwise inherit the previous mark's accumulated coverage and paint
    // something that depends on what was painted before it. BE1's
    // `test_identical_settings_produce_identical_pixels` caught exactly that.
    //
    // Checking the map's IDENTITY makes the stale case unreachable rather than
    // merely discouraged, which is the difference between a rule and a habit.
    // G1. A PIVOT STAMP DEPOSITS ONCE. It fills the fan a following tip sweeps
    // through a sharp turn (`_pivotFill`), and that fan is a rotational SWEEP:
    // accumulating it like a train of dabs would darken every corner. With no
    // accumulator `_depositAt` returns the MAX floor `cov * target`, which is
    // exactly the level one pass reaches on the spine.
    let acc = null;
    if (accumulating && !pivot) {
        if (S.stroke._accumFor !== map || !S.stroke.accum
            || S.stroke.accum.length !== w * h) {
            S.stroke.accum = new Uint16Array(w * h);
            S.stroke._accumFor = map;
        }
        acc = S.stroke.accum;
    }
    const cover = (ccx, ccy, cr) => {
        const bx0 = Math.max(0, Math.floor(ccx - cr));
        const by0 = Math.max(0, Math.floor(ccy - cr));
        const bx1 = Math.min(w - 1, Math.ceil(ccx + cr));
        const by1 = Math.min(h - 1, Math.ceil(ccy + cr));
        if (bx0 < d.x0) d.x0 = bx0;
        if (by0 < d.y0) d.y0 = by0;
        if (bx1 > d.x1) d.x1 = bx1;
        if (by1 > d.y1) d.y1 = by1;
        // BE8. A SECOND rect, covering only what has changed since the last
        // time the display was composited.
        //
        // `S.stroke.dirty` accumulates for the whole stroke, because commit
        // needs the total. Using it to drive the live preview meant that by
        // the end of a long stroke every frame re-composited the stroke's
        // entire bounding box -- which on a large canvas is most of the
        // document, every pointer move.
        const fd = S.stroke.frameDirty;
        if (fd) {
            if (bx0 < fd.x0) fd.x0 = bx0;
            if (by0 < fd.y0) fd.y0 = by0;
            if (bx1 > fd.x1) fd.x1 = bx1;
            if (by1 > fd.y1) fd.y1 = by1;
        }
        const frame = tipFrame(cr, ang, preset);
        const falloff = gaussian ? dabAlphaGauss : dabAlpha;
        for (let py = by0; py <= by1; py++) {
            const rowBase = py * w;
            const dy = py - ccy;
            for (let px = bx0; px <= bx1; px++) {
                // Density skip -- randomly omit pixels for stipple/charcoal.
                if (stipple && Math.random() > density) continue;
                const nd = frame.at(px - ccx, dy);
                // The falloff returns 0 outside the tip, so the commonest exit
                // is taken before any multiply.
                if (nd >= 1) continue;
                // CELL CENTRE DECIDES, and it decides all or nothing. The
                // engine's convention is that a pixel's integer index IS
                // its centre -- the dirty rect, the tip frame and
                // `alphaMapToImageData` all use it -- so aliased follows
                // it rather than introducing a half-pixel offset that
                // would make the two methods disagree about where the
                // tip is.
                // COVERAGE, without Flow. The two are separated because they
                // now enter at different points: coverage shapes the dab,
                // `fEff` says how much of a pass one dab is worth.
                const cov = aliased ? 1 : falloff(nd * cr, cr, hard);
                if (cov <= 0) continue;
                const idx = rowBase + px;
                let out = _depositAt(acc, idx, cov, fEff, target);
                if (out > map[idx]) map[idx] = out;
            }
        }
    };

    // Scatter preset: the same tip, stamped many times at jittered offsets.
    if (preset === "scatter") {
        const n = Math.max(3, ~~(sz / 3));
        for (let i = 0; i < n; i++) {
            cover(cx + (Math.random() - 0.5) * sz * 0.8,
                  cy + (Math.random() - 0.5) * sz * 0.8,
                  Math.random() * r * 0.3 + 1);
        }
        return;
    }

    // Custom tip: sample from grayscale image
    if (preset === "custom" && S.customTip.data) {
        const tipW = S.customTip.width, tipH = S.customTip.height;
        const tipData = S.customTip.data;
        const cosA = Math.cos(-ang), sinA = Math.sin(-ang);
        const scaleX = tipW / sz, scaleY = tipH / sz;
        for (let py = y0; py <= y1; py++) {
            for (let px = x0; px <= x1; px++) {
                const dx = px - cx, dy = py - cy;
                const lx = dx * cosA - dy * sinA;
                const ly = dx * sinA + dy * cosA;
                const tx = (lx * scaleX + tipW / 2) | 0;
                const ty = (ly * scaleY + tipH / 2) | 0;
                if (tx < 0 || tx >= tipW || ty < 0 || ty >= tipH) continue;
                const tipAlpha = tipData[ty * tipW + tx] / 255;
                let cov = tipAlpha;
                if (hard < 1.0 && tipAlpha > 0) {
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    cov *= _dabFalloff(dist, r, hard);
                }
                if (cov <= 0) continue;
                const idx = py * w + px;
                const out = _depositAt(acc, idx, cov, fEff, target);
                if (out > map[idx]) map[idx] = out;
            }
        }
        return;
    }

    // Standard shapes (round, flat, marker) -- one dab through the same loop.
    cover(cx, cy, r);
}

// Convert alpha map → ImageData with a flat color (only processes dirty region)
function alphaMapToImageData(color, rect) {
    const map = S.stroke.alphaMap;
    const w = S.W, h = S.H;
    // BE8. `rect` lets the live preview convert only the region that changed
    // since the last frame. Commit passes nothing and gets the whole stroke,
    // which is what it needs.
    const d = rect || S.stroke.dirty;
    const img = S.stroke._cachedImg || (S.stroke._cachedImg = new ImageData(w, h));
    if (img.width !== w || img.height !== h) {
        S.stroke._cachedImg = new ImageData(w, h);
        return alphaMapToImageData(color);
    }
    const data = img.data;
    const rgb = hexRgb(color);
    // BE4. SELECTION IS APPLIED ONCE, HERE.
    //
    // It used to be multiplied into every dab inside `stampAlphaMap`, at all
    // three coverage branches. That works for a max-blended hard tip, where
    // 50% stays 50% however many dabs overlap. It is wrong for an accumulating
    // soft tip, because multiplying before accumulation turns the selection
    // from an AMOUNT into a RATE: each dab contributes its share of a reduced
    // value, and the total climbs back toward full with every overlap.
    //
    // Measured before the change:
    //
    //     hard tip, selection 100 / 50 / 25   ->  255 / 128 / 64   correct
    //     soft tip, selection 100 / 50 / 25   ->  102 / 102 / 102  ignored
    //
    // The soft numbers are identical because the flow ceiling clamped first --
    // the selection was not merely wrong there, it reached nothing at all.
    //
    // This is the right place for three reasons that are properties of the
    // existing code rather than of this change:
    //
    //   it already walks ONLY the dirty rectangle, so there is no new pass;
    //   it already reads accumulated coverage and writes output alpha, so the
    //     bound costs one multiply inside a loop that was running anyway;
    //   it serves the live preview in `composite()` as well as the commit, so
    //     what the owner sees while painting is what lands.
    //
    // `drawGradient` has applied selection at merge in this exact shape since
    // long before this package. BE4 is not inventing a pattern; it is
    // restoring one the file already uses.
    const sel = (S.selection.active && S.selection.mask) ? S.selection.mask : null;
    // BE10. PAPER IS APPLIED HERE, for the reasons BE4 gives above and one
    // more that is specific to grain.
    //
    // Coverage has already accumulated. Whatever a slow hand did -- thirty
    // overlapping dabs at Spacing 0.04 -- the result is one number per pixel,
    // and the paper multiplies it once. A per-dab grain cannot say that: each
    // pass takes its share of the reduction and the total climbs back, so the
    // fibres vanish exactly where the owner pressed hardest. That is the same
    // amount-versus-rate error BE4 found in the selection, and this is the
    // same fix in the same loop.
    //
    // NOT IN MASK MODE. `exportMask` binarises at alpha > 0, so a grainy mask
    // would not export as soft tooth -- it would export as a mask full of
    // holes wherever the pits fell. The commit already treats mask strokes
    // differently for the same kind of reason.
    const grain = S.editingMask ? null : paperReveal();
    const gTile = grain ? grain.tile : null;
    const gLut = grain ? grain.lut : null;
    // P. A Brush V2 dry-media stroke answers the paper through its own table,
    // indexed by (coverage, height), because the approved response depends on
    // how much paint is there: light coverage catches only the peaks, heavy
    // coverage fills the grain. Only where paper is on at all -- the gates
    // above still decide that -- and only for the stroke that built it.
    const gTbl = gTile && S.stroke.paperTable ? S.stroke.paperTable : null;
    const dx0 = Math.max(0, d.x0), dy0 = Math.max(0, d.y0);
    const dx1 = Math.min(w - 1, d.x1), dy1 = Math.min(h - 1, d.y1);
    for (let py = dy0; py <= dy1; py++) {
        for (let px = dx0; px <= dx1; px++) {
            const i = py * w + px;
            let a = map[i];
            const j = i * 4;
            if (a === 0) { data[j + 3] = 0; continue; }
            // Indexed by DOCUMENT coordinate, so the fibres are a property of
            // the canvas. Two strokes crossing here find the same ones, and
            // neither pan nor zoom moves them. `& 511` because the tile is a
            // power of two -- one AND, one lookup, no division.
            if (gTile) {
                const gh = gTile[((py & 511) << 9) | (px & 511)];
                a = gTbl ? gTbl[(a << 8) | gh] : (a * gLut[gh] + 0.5) | 0;
                if (a === 0) { data[j + 3] = 0; continue; }
            }
            data[j]     = rgb.r;
            data[j + 1] = rgb.g;
            data[j + 2] = rgb.b;
            // Same rounding as the accumulator above, so a fully selected
            // pixel is exactly unchanged rather than one short.
            data[j + 3] = sel ? (((a * sel[i] + 127) / 255) | 0) : a;
        }
    }
    return img;
}

// ========================================================================
// STABILIZER
// ========================================================================
//: How much path one step of the Smoothing control is worth, in SCREEN pixels.
//:
//: SCREEN and not document pixels, because that is what makes the control
//: zoom-stable: at 4x zoom the same hand movement covers a quarter of the
//: document distance, so a document-space window would smooth four times as
//: hard for no reason the owner asked for.
//:
//: 6 px per step puts the shipped presets (1 to 6) between 6 and 36 screen
//: pixels of averaging, which brackets the sample-count windows they replaced
//: at a typical 60 Hz hand speed. Chosen to land near the old feel, not
//: derived -- and stated as such rather than dressed up.
const STAB_SCREEN_PX_PER_STEP = 6;

//: Pressure gets a SHORTER window than position.
//:
//: The brief requires the two to be separate concerns, and they genuinely are:
//: position jitter is a hand tremor an owner wants removed, while pressure lag
//: is felt immediately as a brush that will not respond. Averaging them over
//: the same window trades one for the other.
const STAB_PRESSURE_FRACTION = 0.4;

/**
 * The stabiliser: the centroid of the last N units of PATH, not of samples.
 *
 * WHAT THIS REPLACED, and why it was the larger of two defects:
 *
 *     const w = Math.max(1, Math.min(S.smoothing, pts.length));
 *     ...mean of the last w SAMPLES...
 *
 * A window counted in samples covers a quarter of the arc at 240 Hz that it
 * covers at 60 Hz. So the filter's real strength -- how much of the path it
 * averages over -- depended on how often the browser reported the pointer.
 *
 * BE3 found this and could not fix it there: its own tests call `plotTo`
 * directly and bypass the stabiliser entirely, so the defect was invisible to
 * them. Measured through the real pointer pipeline it was LARGER than the
 * placement defect BE3 did fix -- at one event per segment a stroke covered
 * 7,173 pixels where twenty events covered 14,096.
 *
 * The window is now an arc length, and the average is weighted by the path
 * length each sample represents rather than by how many samples arrived. That
 * makes the result a property of the polyline: exactly the same answer however
 * finely it is sampled.
 */
function stab(rx, ry, rp) {
    const pts = S.stroke.points;
    pts.push({ x: rx, y: ry, p: rp });
    const level = S.smoothing || 0;
    // 0 means no stabilisation, and says so directly rather than relying on a
    // window of 1 to be a no-op. (It is one -- the mean of a single sample is
    // that sample -- but an explicit exit is cheaper and cannot be broken by a
    // later change to the weighting.)
    if (level <= 0 || pts.length < 2) return { x: rx, y: ry, p: rp };

    const scale = (S.zoom && S.zoom.scale) || 1;
    const windowDoc = Math.max(0.001, (level * STAB_SCREEN_PX_PER_STEP) / scale);
    return {
        x: _arcCentroid(pts, windowDoc, "x", rx),
        y: _arcCentroid(pts, windowDoc, "y", ry),
        p: _arcCentroid(pts, windowDoc * STAB_PRESSURE_FRACTION, "p", rp),
    };
}

/**
 * The arc-length-weighted mean of one component over the last `windowDoc`
 * units of path, walking backwards from the newest sample.
 *
 * Each segment contributes its own midpoint weighted by the length taken from
 * it, and a partial segment contributes the midpoint of the part taken. That is
 * what makes this exact rather than approximate: subdividing a segment changes
 * how many terms the sum has and not what it adds up to.
 */
//: The SAMPLE-COUNT mean the arc-length window replaced. Kept ONLY so the
//: mutation harness can put the original defect back and prove a guard catches
//: it -- a mutation that has to invent its own replacement is testing the
//: mutation, not the code. Nothing in the engine calls this.
function _sampleMean(pts, key) {
    const w = Math.max(1, Math.min(S.smoothing, pts.length));
    let sum = 0;
    for (let i = pts.length - w; i < pts.length; i++) sum += pts[i][key];
    return sum / w;
}

function _arcCentroid(pts, windowDoc, key, fallback) {
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
}

//: SMOOTHING 0 USED TO KILL THE BRUSH, and the record is kept because the
//: shipping Extension still has the defect.
//:
//: The old window was `Math.max(1, Math.min(S.smoothing, pts.length))`. Without
//: that `Math.max(1, ...)` -- which Studio added and the Extension lacks -- a
//: Smoothing of 0 gave a window of 0, every component became 0/0, and every
//: stamp after the opening dab was placed at NaN. A dense 61-sample stroke
//: painted 3,740 pixels instead of 30,990
//: (Evidence/ar8.3-browser-baseline/canvas-baseline.md).
//:
//: The arc-length stabiliser above exits at level 0 before any arithmetic, so
//: the class of bug is gone rather than guarded against. Extension reference:
//: Reference/Forge-Studio-main/Forge-Studio-main/frontend/canvas-core.js:789
//: and its index.html:107.

// ========================================================================
// STROKE ENGINE
// ========================================================================

function stampWet(x, y, p, stampRot, mods, dabStep, pivot) {
    const dyn = S.brushDynamics;
    const M = mods || DYN_NEUTRAL;
    // Floor of ONE, not two.
    //
    // The old floor of 2 was blamed for making a pixel brush impossible. BE0
    // measured that it was innocent: a diameter-2 hard dab at an integer centre
    // rasterises to a single pixel anyway, so Size 1 already painted one pixel.
    // The cause was the relative size curve, and `brushPx()` now owns that.
    //
    // It is corrected here because it CONFLICTS with the literal-pixel
    // contract rather than because it was the defect: in `document_pixels`
    // mode `sz` IS the owner's requested diameter, and silently doubling a
    // request for 1 makes the mode a lie even when the pixels happen to agree.
    // Ordinary brushes are unaffected -- `brushPx()` already floors at 1, and
    // the rasterised output is byte-identical either way, which is measured
    // rather than asserted.
    //
    // NOT changed: `smudgeStroke`'s `Math.max(2, brushPx() * 0.25)` is a dab
    // SPACING floor owned by CT10. The second floor this comment used
    // to name lived in `makeStamp`, which is now deleted.
    // BE11. Dynamics multiply what the existing controls return; they do not
    // replace them. The Pen toggle is a coarser control that predates this
    // package and that owners have saved into presets and recovered documents,
    // so replacing it would silently change every one of those.
    //
    // BEFORE the jitters, deliberately: jitter is meant to vary the dab the
    // brush actually intends to lay down, so it multiplies the dynamic size
    // rather than a base the dynamics then overrule.
    let sz = Math.max(1, pSz(p) * M.size), op = pOp(p) * M.flow;
    if (!S.stroke.alphaMap) return;
    if (dyn.sizeJitter > 0) sz = Math.max(1, sz * (1 + (Math.random() * 2 - 1) * dyn.sizeJitter));
    if (dyn.opacityJitter > 0) op = Math.max(0.01, op * (1 + (Math.random() * 2 - 1) * dyn.opacityJitter));
    op = op < 0.01 ? 0.01 : (op > 1 ? 1 : op);
    // BE16. Same fallback, same reason. Every caller inside the stroke engine
    // passes an angle; this is for a direct call, and it used to answer with
    // whatever the last stroke ended on.
    let ang = stampRot !== undefined ? stampRot : dabRotation();
    if (dyn.rotationJitter > 0) ang += (Math.random() * 2 - 1) * Math.PI * dyn.rotationJitter;
    // ADDED, in degrees converted to radians. A multiplicative angle is
    // meaningless -- zero degrees times anything is zero degrees -- and BE7
    // already treats the tip angle as a sum of three separate terms.
    if (M.angle) ang += (M.angle * Math.PI) / 180;
    // CT3. Taper in. `_taperK` is set per stamp by `plotTo` from the distance
    // travelled so far; `beginStroke` leaves it at the narrowest so the very
    // first dab is the point of the stroke rather than its full width.
    const taperK = S.stroke._taperK;
    if (typeof taperK === "number" && taperK < 1) sz = Math.max(1, sz * taperK);
    // RATIO is applied by save-and-restore, because `tipFrame` reads
    // `S.brushRatio` directly and threading a per-dab ratio through it would
    // mean a parameter on the hottest function in the engine. The idiom is
    // already here: the mask branch two lines down does exactly this.
    const savedRatio = S.brushRatio;
    if (M.ratio !== 1) S.brushRatio = Math.max(0.05, Math.min(1, savedRatio * M.ratio));
    // In Edit > Inpaint: force hard round mask brush
    const savedPreset = S.brushPreset, savedHard = S.brushHardness;
    if (S.editingMask) { S.brushPreset = "round"; S.brushHardness = 1.0; }
    const finalOp = S.editingMask ? 1 : op;
    stampAlphaMap(x, y, sz, finalOp, ang, dabStep, pivot);
    if (S.symmetry === "h" || S.symmetry === "both") stampAlphaMap(S.W - x, y, sz, finalOp, ang, dabStep, pivot);
    if (S.symmetry === "v" || S.symmetry === "both") stampAlphaMap(x, S.H - y, sz, finalOp, ang, dabStep, pivot);
    if (S.symmetry === "both") stampAlphaMap(S.W - x, S.H - y, sz, finalOp, ang, dabStep, pivot);
    if (S.symmetry === "radial") {
        const n = S.symmetryAxes || 4;
        const ccx = S.W / 2, ccy = S.H / 2;
        for (let k = 1; k < n; k++) {
            const a = (2 * Math.PI * k) / n;
            const cos = Math.cos(a), sin = Math.sin(a);
            const rx = ccx + (x - ccx) * cos - (y - ccy) * sin;
            const ry = ccy + (x - ccx) * sin + (y - ccy) * cos;
            stampAlphaMap(rx, ry, sz, finalOp, ang + a, dabStep, pivot);
        }
    }
    if (S.editingMask) { S.brushPreset = savedPreset; S.brushHardness = savedHard; }
    S.brushRatio = savedRatio;
}

// `stampWetErase` used to live here: a second stamp function that drew
// `ctx.arc()` or a radial gradient straight onto the stroke canvas with
// `destination-out`.
//
// CT3 DELETED IT, which is what §8.4 asks for in as many words -- "Do not
// retain a duplicated generic-circle Eraser path after parity is proven". It
// was generic-circle in the literal sense: it knew nothing about tip shape,
// roundness, spikes, density or falloff mode, so a flat or marker eraser
// erased a circle and the Ratio and Spikes controls did nothing while the
// eraser was selected. It also had no commit-time alpha, which is the whole
// reason `pOpacity` had to exist as a second per-stamp function.
//
// The eraser now runs `stampWet` like everything else and differs in exactly
// one place: `commitStroke` composites the stroke's alpha map with
// `destination-out` instead of `source-over`. One stamp engine, one commit,
// and every tip control reaches both tools.

function plotTo(x, y, p) {
    const x0 = S.stroke.lx, y0 = S.stroke.ly, p0 = S.stroke.lp;
    const dx = x - x0, dy = y - y0;
    // BE7. THE STALE-ANGLE GATE IS GONE.
    //
    //     if (Math.hypot(dx, dy) > 2) { ...update the direction... }
    //
    // Three defects in that line. A segment of two pixels or less did not
    // update the direction AT ALL, so a slowly drawn curve -- the case where a
    // chisel's angle matters most -- kept whatever heading it had before. The
    // update happened once per EVENT while `plotTo` may emit many dabs along
    // the segment, so every dab in a fast stroke shared one angle and tight
    // curves came out faceted. And the smoothing was a fixed 0.3 per event,
    // which makes the filter's strength depend on how often the browser
    // reports the pointer -- the same event-rate defect BE3 removed from
    // spacing, still living one line above it.
    //
    // The direction is now computed PER DAB and smoothed over DISTANCE, below.
    const dyn = S.brushDynamics;
    const bpx = brushPx();
    const spacingFrac = spacingFraction();
    const dist = Math.hypot(dx, dy);
    // BE11. Segment speed, in document pixels per millisecond, or null.
    //
    // NULL IS THE HONEST ANSWER when no timestamp reached the engine, and it
    // is what every headless driver gets. A speed rule then uses its own
    // declared fallback -- the same rule the pen inputs follow -- rather than
    // a value this function chose on its behalf.
    //
    // Measured per SEGMENT and not per dab: the dabs along one segment are
    // interpolated positions, so they share the hand movement that produced
    // them and a per-dab speed would be an invention.
    const segSpeed = _segmentSpeed(dist);
    if (dist > 0) _sa = Math.atan2(dy, dx);
    // ONE stamp function. The eraser differs at commit, not here.
    const fn = stampWet;
    const strokeAngle = Math.atan2(dy, dx);
    const perpX = -Math.sin(strokeAngle), perpY = Math.cos(strokeAngle);
    // BE16. A HEADING EXISTS AS SOON AS THE POINTER HAS MOVED, and the held
    // opening dab lands the moment it does.
    //
    // `_advanceHeading` used to run only inside the dab loop, which has two
    // consequences that were never intended:
    //
    //   the loop runs only once BE3's spacing debt is paid, so a slow start
    //     left the stroke with no heading -- and the opening dab pending --
    //     across several sub-gap moves while a perfectly good direction was
    //     already known;
    //
    //   the pixel-walk branch below RETURNS before the loop, so an aliased or
    //     pixel-perfect stroke never advanced the heading AT ALL. `_saSmooth`
    //     stayed on the previous stroke's direction for the whole of it.
    //
    // Hoisting the call fixes both and costs one assignment per segment. The
    // loop still calls it per dab, which is the same value written again.
    //
    // The flush is here rather than in the loop for the same reason, and it
    // keeps the mark in drawing order: the first press, then the drag.
    if (dist > 0) {
        _advanceHeading(strokeAngle);
        flushOpeningDab(strokeAngle);
    }

    // BE3. DAB PLACEMENT IS A FUNCTION OF DISTANCE, NOT OF EVENT RATE.
    //
    // What this replaced:
    //
    //     const steps = Math.max(1, Math.ceil(dist / sp));
    //     for (let i = 1; i <= steps; i++) { const t = i / steps; ... }
    //
    // Two defects in three lines. `Math.max(1, ...)` guaranteed a dab for
    // EVERY pointer event however short, so a browser delivering 240 events a
    // second laid four times the dabs of one delivering 60 along the same
    // path. And `t = i / steps` spread those dabs evenly across whatever
    // segment arrived, so the actual gap was `dist / steps` -- never the
    // requested spacing except by coincidence.
    //
    // Nothing carried between events, so the sub-spacing remainder was
    // discarded on every one. Blender solves this in `paint_stroke.cc` by
    // carrying the leftover, and its comment says why in as many words:
    // "copy last position -before- jittering, or space fill code will create
    // too many dabs".
    //
    // The carried quantity here is a DEBT: how much further the stroke must
    // travel before the next dab is due. A segment shorter than the debt emits
    // nothing and pays down what it can, which is the case the old code could
    // not express at all.
    //
    // DELIBERATE DIVERGENCE from the package brief, which asks both to "carry
    // residual distance between events" AND to "advance the anchor to the last
    // emitted dab, not the pointer". Those are two formulations of one fix and
    // doing both would double-count the remainder. The anchor stays on the
    // pointer so the dabs follow the polyline the owner actually traced; moving
    // it to the last dab would cut the corner at every event boundary.
    // BE13. PIXEL PERFECT TAKES THE SEGMENT WHOLE.
    //
    // BE3's spacing debt places dabs every `spacing` document pixels
    // ALONG THE PATH, which on a diagonal lands at cells that skip. A
    // corner filter needs an 8-connected run or it has no corners to
    // find, so the segment is walked with Bresenham instead.
    //
    // The debt is left untouched and unread on this path. That is not an
    // oversight: a cell walk emits one dab per cell by definition, and a
    // spacing debt would be a second, contradictory answer to "how far
    // apart are the dabs".
    if (pixelWalkActive()) {
        const stamp = (sx, sy) => {
            S.stroke._travelled = (S.stroke._travelled || 0) + 1;
            S.stroke._taperK = _taperFactor(S.stroke._travelled, bpx);
            _dynCtx.pressure = p;
            _dynCtx.heading = strokeAngle;
            _dynCtx.speed = segSpeed;
            fn(sx, sy, p, _brushAngleRad(), applyDynamics(_dynCtx));
        };
        _ppWalk(Math.round(x0), Math.round(y0),
                Math.round(x), Math.round(y), stamp);
        S.stroke.lx = x; S.stroke.ly = y; S.stroke.lp = p;
        return;
    }

    let debt = S.stroke._spacingDebt;
    // Only a MISSING debt is refilled. A debt of zero is not missing -- it
    // means something set it to zero, and charging a fresh gap there would
    // paper over the bug rather than surface it.
    //
    // The looser `if (!(debt > 0))` was written first and a mutation caught it:
    // zeroing the seed in `beginStroke` changed no behaviour, because this line
    // silently rescued it. A defence that makes a defect invisible is not a
    // defence.
    if (debt === undefined || !Number.isFinite(debt)) {
        debt = spacingFor(p0, bpx, spacingFrac);
    }
    let travelled = 0;
    while (dist > 0 && travelled + debt <= dist) {
        travelled += debt;
        const t = travelled / dist;
        let sx = x0 + dx * t, sy = y0 + dy * t;
        if (dyn.scatter > 0 && S.tool !== "eraser") {
            const scatterDist = (Math.random() * 2 - 1) * bpx * dyn.scatter;
            sx += perpX * scatterDist;
            sy += perpY * scatterDist;
        }
        const stampP = p0 + (p - p0) * t;
        // BE7. The heading is advanced PER DAB and smoothed over DISTANCE.
        //
        // `_advanceHeading` moves the smoothed direction toward this segment's
        // tangent by an amount that depends on how far the stroke travelled to
        // reach this dab -- so the filter has the same strength per millimetre
        // whatever rate the browser reports at, and a slow curve turns the tip
        // exactly as much as a fast one over the same arc.
        _advanceHeading(strokeAngle);
        // THREE SEPARATE TERMS, deliberately not collapsed:
        //   the heading      -- where the stroke is going, if followStroke
        //   the base Angle   -- what the owner set, always
        //   rotation jitter  -- seeded randomness, never mixed into the filter
        let stampRot = (dyn.followStroke ? _saSmooth : 0) + _brushAngleRad();
        // BE16. THE JITTER IS NOT APPLIED HERE, and that is the fix rather than
        // an omission. `stampWet` applies it too, so a body dab used to turn
        // TWICE as far as the label promises -- measured spread sd 17.8 degrees
        // against 13.0 for one application at a 13% setting -- while the
        // opening dab, which reaches `stampWet` directly, took exactly one.
        // The first press turning half as far as the drag is this package's own
        // subject, and it was one function away from the fix.
        //
        // `stampWet` is the survivor because EVERY dab passes through it: the
        // body, the deferred opening dab, the airbrush's timed deposits. Keeping
        // this one instead would leave those last two unjittered.
        //
        // It also uncouples Rotation Jitter from dab DENSITY. `spacingFor`
        // prices the gap from `stampRot`, and pricing it from one dab's random
        // draw made the control silently change how many dabs a stroke lays --
        // Flat Chisel 60 at 0% and about 130 at 50%. The gap is now priced from
        // the angle the STROKE has, which is the quantity a gap is about.
        // Distance travelled BEFORE this stamp, so the taper is a function of
        // how far the stroke has come rather than of how many samples the
        // browser happened to deliver.
        S.stroke._travelled = (S.stroke._travelled || 0) + debt;
        S.stroke._taperK = _taperFactor(S.stroke._travelled, bpx);
        // BE11. The dab loop is the only place that knows all four inputs at
        // once -- this dab's pressure, the heading BE7 just advanced for it,
        // and the segment speed if a timestamp reached the engine. Computed
        // here and handed down, so the stamp reads four numbers rather than
        // deriving anything.
        //
        // `_dynCtx` is reused rather than allocated per dab. A 4096-dab stroke
        // would otherwise allocate 4096 short-lived objects inside the loop
        // BE8 spent its whole budget on.
        _dynCtx.pressure = stampP;
        _dynCtx.heading = stampRot;
        _dynCtx.speed = segSpeed;
        // BE17. HOW FAR THIS DAB IS FROM THE ONE BEFORE IT, in units of the
        // tip's own radius along the direction of travel. That ratio is what
        // says how many dabs cover a pixel, and therefore how much of a pass
        // each dab is worth.
        //
        // `debt` IS this dab's gap: the loop has just added it to `travelled`
        // to arrive here. It is passed rather than recomputed because
        // `spacingFor` clamps at half a pixel, and under that clamp the
        // fraction no longer describes the distance actually advanced.
        const dabPx = S.pressureSensitivity ? Math.max(1, pSz(stampP)) : bpx;
        const alongRadius = Math.max(
            0.5, dabPx * 0.5 * alongExtentFor(strokeAngle, stampRot));
        const mods = applyDynamics(_dynCtx);
        _pivotFill(sx, sy, stampP, stampRot, dabPx, mods, debt / alongRadius);
        fn(sx, sy, stampP, stampRot, mods, debt / alongRadius);
        _notePivot(sx, sy, stampP, stampRot);
        // The NEXT gap is measured at THIS dab's pressure AND at the tip's
        // extent along the direction of travel, so a chisel spaces itself by
        // the width it is actually presenting rather than by one scalar.
        debt = spacingFor(stampP, bpx, spacingFrac, strokeAngle, stampRot);
    }
    // Whatever is left of this segment pays down the next dab's debt.
    S.stroke._spacingDebt = debt - (dist - travelled);
    S.stroke._travelled = (S.stroke._travelled || 0) + (dist - travelled);
    S.stroke.lx = x; S.stroke.ly = y; S.stroke.lp = p;
}

/**
 * The gap between two dabs, in document pixels, at a given pressure.
 *
 * Split out of `plotTo` because BE3 needs it TWICE per dab -- once to decide
 * where this dab lands and once to price the next gap -- and because a spacing
 * rule that lives in one place can be tested in one place.
 *
 * Pressure enters through `pSz`, so when pressure drives size the spacing
 * follows the dab that is actually being painted. When it does not, `pSz`
 * returns the base width and this is exactly the old expression.
 */
function spacingFor(pressure, basePx, spacingFrac, travelAngle, tipAngle) {
    const px = S.pressureSensitivity ? Math.max(1, pSz(pressure)) : basePx;
    // BE7. The gap follows the tip's extent ALONG THE DIRECTION OF TRAVEL.
    //
    // A scalar width is right for a circle and wrong for everything else. A
    // chisel travelling along its long axis presents several times the width
    // it presents travelling across it, so one number either bunches the dabs
    // on one heading or leaves gaps on the other. Both were visible: it is
    // most of why "the flat brushes behave weirdly when doing turns".
    //
    // Skipped entirely when the caller has no heading -- `beginStroke` seeds
    // the first debt before any direction exists -- and when the tip is
    // circular, where the extent is the same in every direction and the whole
    // calculation is a no-op that would only cost time.
    return Math.max(0.5, px * alongExtentFor(travelAngle, tipAngle) * spacingFrac);
}

/**
 * The tip's radius along a direction of travel, as a fraction of the nominal.
 *
 * Lifted out of `spacingFor` by BE17, which needs the SAME number for a second
 * purpose: to say how many dabs cover one pixel, and therefore how much each
 * dab may deposit. Two copies of an ellipse would be two chances to disagree
 * about the tip -- which is the defect BE6 spent a package removing when three
 * tip functions had each grown their own geometry.
 *
 * Answers 1.0 for a circle and for a caller with no heading, so both of
 * `spacingFor`'s old branches collapse into one expression.
 */
function alongExtentFor(travelAngle, tipAngle) {
    if (travelAngle === undefined || tipAngle === undefined) return 1.0;
    const kind = S.brushPreset;
    const aspect = (TIP_ASPECT[kind] ?? 1.0) * (S.brushRatio || 1.0);
    if (Math.abs(aspect - 1) <= 1e-6) return 1.0;
    const extent = TIP_EXTENT[kind] || 1.0;
    // Travel direction expressed in the tip's own frame.
    const phi = travelAngle - tipAngle;
    const c = Math.cos(phi), s = Math.sin(phi);
    const rx = extent, ry = aspect;
    // Radius of the tip's ellipse in that direction, as a fraction of the
    // nominal radius. Chebyshev tips use the same measure: it is the right
    // order of magnitude and the alternative is a norm-specific special case
    // for a value that only scales a gap.
    const denom = Math.sqrt((ry * c) * (ry * c) + (rx * s) * (rx * s));
    return denom > 1e-9 ? (rx * ry) / denom : rx;
}

/**
 * The mean of the tip's radial profile, integral of shape(u) du over [0, 1].
 *
 * BE17 needs it because dabs overlap by GEOMETRY but deposit by PROFILE: a
 * pixel under the middle of the swept band is touched by `2 * radius / gap`
 * dabs, and each of those touches it at a different point on the falloff. The
 * effective number of full-strength dabs is therefore the geometric count
 * weighted by this mean, and that is the number Flow has to be divided by.
 *
 * The default falloff has a closed form and is not integrated. `dabAlpha` is
 * 1 out to `hardness` and a smoothstep down over the rest, and a smoothstep
 * integrates to half its interval, so the mean is `h + (1 - h)/2`. That gives
 * 1.0 at hardness 1 -- a flat disc -- and 0.5 at hardness 0, which is the
 * sanity check for the whole derivation.
 *
 * The gaussian falloff has no closed form worth writing, so it is integrated
 * once per hardness by the midpoint rule and cached. 512 points costs about
 * two microseconds and the cache means a stroke pays it once.
 */
/**
 * What one dab deposits at one pixel, as coverage in 0..255.
 *
 * THE PEAK FLOOR IS WHY A HARD TIP STILL HAS A HARD EDGE. Pure accumulation
 * puts the centreline in the right place and gives the cross-section the wrong
 * SHAPE, because the number of dabs sweeping over a pixel falls off faster at
 * the rim of the band than the tip's own profile does. Measured at hardness
 * 1.0, Flow 50, across the band:
 *
 *     today            127 127 127 127 127 127 127
 *     pure accumulate   62  91 109 116 124 124 124
 *
 * -- a soft shoulder on a brush whose whole point is that it has none, and
 * Pencil's entire mark 19% lighter. Flooring with the old max-blend value
 * keeps the single-pass profile IDENTICAL at hardness 1.0, 0.85 and 0.5 and
 * within 5/255 at hardness 0, and costs one byte per pixel. The accumulator
 * then only ever ADDS to what a single pass would have laid, which is exactly
 * what "going over it again makes it darker" should mean.
 *
 * The accumulator is 16-bit and the answer is 8-bit: the extra precision is
 * needed to ADD small contributions, not to store the result.
 */
function _depositAt(acc, idx, cov, fEff, target) {
    const peak = (cov * target * 255) | 0;
    if (!acc) return peak;
    const prev = acc[idx];
    const next = prev + (65535 - prev) * (cov * fEff);
    acc[idx] = next > 65535 ? 65535 : next;
    const built = acc[idx] >> 8;
    return built > peak ? built : peak;
}

/**
 * The step at which exactly ONE dab covers a pixel.
 *
 * `stampAlphaMap` divides a dab's contribution by `(2 / step) * profileMean`,
 * which is the number of dabs a pixel sees as a stroke sweeps past. A deposit
 * that is not sweeping -- the airbrush's timer, landing repeatedly on the same
 * pixel -- must not pay that divisor, and this is the step at which it is one.
 *
 * Expressed as a step rather than as a flag so the caller reads in the same
 * units as every other caller, and so the identity is checkable: substituting
 * it gives `(2 / (2 * I)) * I`, which is 1.
 */
function timeDepositStep() {
    return 2 * profileMean(S.brushHardness, S.brushFalloff === "gaussian");
}

const _PROFILE_MEAN = new Map();

function profileMean(hardness, gaussian) {
    const h = Math.min(1, Math.max(0, hardness));
    if (!gaussian) return (1 + h) / 2;
    const key = Math.round(h * 1000);
    let mean = _PROFILE_MEAN.get(key);
    if (mean === undefined) {
        const N = 512;
        let sum = 0;
        for (let i = 0; i < N; i++) sum += dabAlphaGauss((i + 0.5) / N, 1, h);
        mean = Math.max(0.05, sum / N);
        _PROFILE_MEAN.set(key, mean);
    }
    return mean;
}

/**
 * Set the dab's heading to the path's TANGENT at this dab. No filter.
 *
 * WHAT WAS THERE, AND WHY IT WENT. The original advanced a smoothed heading by
 * a fixed 0.3 per pointer EVENT, so the filter's strength depended on how often
 * the browser reported the pointer -- the same event-rate defect BE3 removed
 * from spacing, living one line above it.
 *
 * The obvious repair is to make the coefficient per-DISTANCE instead, and that
 * was written and measured before this. It does not work, for a reason worth
 * recording: BE7 also makes the dab gap depend on the tip's extent ALONG the
 * direction of travel, so the gap depends on the heading and the heading
 * advances once per gap. That is a feedback loop, and it made the trajectory
 * sensitive to the sampling after all -- the same quarter-turn ended at -59
 * degrees when reported 20 times and -22.7 degrees when reported 240 times,
 * against a true final tangent of -90.
 *
 * A first-order filter also lags a turn by roughly `tau x turn-rate` whatever
 * the sampling, which for a one-dab-width tau is about 29 degrees on a
 * reference arc. A chisel held 29 degrees off its direction of travel through
 * every curve is precisely the complaint that opened this program.
 *
 * So the heading is the tangent, which is a property of the PATH rather than of
 * the event stream: exactly rate-invariant, no lag, no feedback. Input noise is
 * a separate concern and belongs to the stabiliser -- BE9 smooths POSITION, and
 * a smoothed path yields a smooth tangent for free.
 *
 * `_saSmooth` keeps its name because it is exported as `strokeAngle` and read
 * in three other places; renaming it is a separate, mechanical change.
 */
function _advanceHeading(target) {
    if (!Number.isFinite(target)) return;
    _saSmooth = target;
    S.stroke._headingKnown = true;
}

/**
 * Can this dab's rotation change what it paints?
 *
 * BE16. THE DEFERRAL IS ONLY PAID FOR WHERE IT BUYS SOMETHING. Holding the
 * opening dab back costs one pointer event of feedback at press -- and for a
 * TAP, the dot appears on release rather than on press, which is a change in
 * feel on every preset. Three presets have the angle defect. Making the other
 * thirteen, the eraser and mask mode pay for it would be a bad trade.
 *
 * So a dab whose rotation cannot matter is stamped immediately, exactly as
 * before. Three independent reasons it cannot matter, and each is a fact about
 * the engine rather than a guess:
 *
 *   MASK MODE forces a round tip and hardness 1 inside `stampWet` itself, so
 *     nothing it lays can be rotated;
 *   A CIRCULAR TIP discards the angle in `tipFrame` -- `rotates` is false when
 *     the frame is circular and unfolded, which is the same test used here;
 *   FOLLOW STROKE OFF means the rotation is the owner's Angle alone, which is
 *     known at press. Calligraphy is the case that matters: a nib deliberately
 *     held at 45 degrees, and the one preset a heading must never reach.
 *
 * The custom tip is excluded from the fast path deliberately: its branch
 * rotates unconditionally, and although `S.customTip.data` is null on every
 * build today, a predicate that is wrong the moment an importer lands is worse
 * than one extra pointer event.
 */
function dabIgnoresRotation() {
    if (S.editingMask) return true;
    if (!S.brushDynamics.followStroke) return true;
    const kind = S.brushPreset;
    if (kind === "custom") return false;
    // SPIKES ARE NOT A REASON TO DEFER, and the clause that said they were
    // cost 221 inked pixels at press on twelve presets for nothing. Measured:
    // Spikes 2 inks 221 px at press, Spikes 3 and 12 ink zero, and the FINISHED
    // stroke is byte-identical in all three.
    //
    // `_applySpikeRotation` folds the sample angle into a wedge, and a fold is
    // a rotation about the origin. A rotation preserves radius, so on a tip
    // where rx === ry the normalised distance cannot change and the spike count
    // cannot alter one pixel -- which is what BE6's own
    // `test_spikes_is_neutral_on_a_perfect_circle` asserts. On a tip where
    // rx !== ry spikes DO make lobes and rotation does matter, but such a tip
    // already fails the anisotropy test below and defers on that.
    //
    // So the clause had no case in which it answered correctly and one in which
    // it answered wrongly. Deleted rather than repaired.
    const extent = TIP_EXTENT[kind] || 1.0;
    const aspect = (TIP_ASPECT[kind] ?? 1.0) * (S.brushRatio || 1.0);
    return Math.abs(extent - aspect) < 1e-9;
}

/**
 * The heading a dab should use, or NULL when this stroke has none yet.
 *
 * BE16. `_headingKnown` has existed since BE7, whose comment diagnoses the bug
 * exactly -- "`_saSmooth` is a module global, so without this the first dabs of
 * every stroke inherit the direction the previous stroke ended on" -- and which
 * then never read the flag anywhere. A field with no consumer is the defect
 * class BE1's whole guard set exists to catch, and it was sitting inside the
 * package that wrote the sentence describing it.
 *
 * This is its reader. Null means "no direction exists", which is a different
 * statement from "the direction is zero", and every caller turns it into 0
 * rather than into the last thing the owner drew.
 */
function strokeHeading() {
    return (S.stroke && S.stroke._headingKnown) ? _saSmooth : null;
}

/** The rotation a dab should take, given the owner's Angle and the heading. */
function dabRotation() {
    const h = strokeHeading();
    return (S.brushDynamics.followStroke && h !== null ? h : 0) + _brushAngleRad();
}

/**
 * G1. A SHARP TURN IS FILLED BY PIVOTING THE TIP, NOT BY LAGGING THE HEADING.
 *
 * BE7 made the heading the path's tangent, which removed the Extension's
 * event-rate filter and its lag -- and with it the only thing that had been
 * hiding corners. At a zig-zag vertex the tangent snaps 50-80 degrees between
 * two dabs 3.5 px apart, so a 22 px chisel's far end jumps 20-30 px and every
 * vertex left a bow-tie of separate stamps: the owner's "rotates the tip at
 * direction changes, giving strange stamping".
 *
 * Spacing is measured at the tip's CENTRE; a turning tip also moves its ENDS.
 * So when the far end would travel further than the gap, pivot stamps are laid
 * between the two dabs, interpolating position and angle, until neither end
 * advances more than one gap per stamp. A straight line or a gentle curve turns
 * too little to need any and is byte-identical. Pivot stamps deposit ONCE (see
 * `stampAlphaMap`), so a corner reaches the one-pass level and no darker.
 *
 * Only where rotation can change what is painted, and only for a tip that
 * follows the stroke: a held nib (Calligraphy) does not turn, a round tip has
 * no ends, and rotation jitter is deliberate randomness rather than a turn.
 */
const PIVOT_MAX = 64;

// A test seam, like `setAirbrushClock`: lets a measurement compare the same
// stroke with and without the fill, so "a pivot adds no darkness" can be
// asserted directly rather than inferred from a peak that self-overlap also
// moves. Never switched off by product code.
let _pivotFillOn = true;
function setPivotFill(on) { _pivotFillOn = !!on; }

function _pivotFill(x, y, p, rot, dabPx, mods, dabStep) {
    const from = S.stroke && S.stroke._pivot;
    if (!_pivotFillOn || !from || !from.set) return 0;
    const dyn = S.brushDynamics;
    if (!dyn.followStroke || dyn.rotationJitter > 0 || dabIgnoresRotation()) return 0;
    const turn = Math.atan2(Math.sin(rot - from.rot), Math.cos(rot - from.rot));
    const kind = S.brushPreset;
    const reach = dabPx * 0.5 * Math.max(TIP_EXTENT[kind] || 1.0,
        (TIP_ASPECT[kind] ?? 1.0) * (S.brushRatio || 1.0));
    const gap = Math.max(1, Math.hypot(x - from.x, y - from.y));
    const n = Math.min(PIVOT_MAX, Math.ceil(reach * Math.abs(turn) / gap) - 1);
    for (let k = 1; k <= n; k++) {
        const t = k / (n + 1);
        stampWet(from.x + (x - from.x) * t, from.y + (y - from.y) * t,
                 from.p + (p - from.p) * t, from.rot + turn * t, mods, dabStep, true);
    }
    if (n > 0) S.stroke._pivots = (S.stroke._pivots || 0) + n;
    return n > 0 ? n : 0;
}

/** Remember the dab just laid, without allocating one object per dab (BE8). */
function _notePivot(x, y, p, rot) {
    const st = S.stroke;
    if (!st) return;
    const v = st._pivot || (st._pivot = { x: 0, y: 0, p: 0, rot: 0, set: false });
    v.x = x; v.y = y; v.p = p; v.rot = rot; v.set = true;
}

/**
 * Lay the opening dab that `beginStroke` held back, now that a heading exists.
 *
 * DEFERRED, NOT GUESSED, AND NOT REPAINTED. A stroke has no direction until the
 * pointer moves, so dab number one either waits or is wrong. It cannot be
 * painted and corrected: `S.stroke.alphaMap` is an accumulator with no undo of
 * its own, so correcting a dab means writing 0 -- which erases whatever else
 * has landed there and cannot tell "this dab painted it" from "it was already
 * 255". BE13 refuses that for a pixel-perfect corner and BE4 refuses the same
 * shape for the selection. A dab that is never painted needs no unpainting.
 *
 * `heading` is the first segment's tangent, or null for a stroke that ended
 * without ever moving -- a tap, which still has to leave a mark.
 *
 * TWO THINGS ARE CAPTURED AT PRESS TIME, and both are corrections to a naive
 * flush rather than decoration.
 *
 *   THE TAPER. `_taperK` belongs to distance travelled, and the opening dab's
 *     distance is zero however long it waited to be laid. `stampWet` reads the
 *     one mutable slot live, and `plotTo` overwrites it per dab -- so a flush
 *     that re-read it would lay the point of the stroke at up to eight times
 *     its intended width, deleting Taper In entirely.
 *
 *   MASK MODE. `stampWet` reads `S.editingMask` LIVE and forces a round,
 *     hardness-1, alpha-1 tip from it. The mask toggle is a keystroke, so it
 *     can land between the press and the first move: the same opening dab is
 *     65x19 as a pixel dab and 65x65 as a mask dab. `beginStroke` already locks
 *     the other half of this into `_commitTarget` and `_commitMask` precisely
 *     so a mid-stroke toggle cannot reach the wrong canvas, and a dab SHAPED at
 *     flush time would reintroduce exactly the hazard those two fields exist to
 *     prevent.
 *
 * THE MODIFIERS ARE NOT CAPTURED, and that is also deliberate. The opening dab
 * has always been stamped with no `mods` argument, so it takes DYN_NEUTRAL --
 * and `beginStroke`'s own comment says why: "the pen state for the OPENING dab
 * arrives after this line. That dab uses the neutral modifiers, which is
 * correct rather than convenient: nothing has been measured yet." By flush time
 * `noteSample` HAS populated the pen, so passing live dynamics would quietly
 * make that comment false and change the dab's ratio and flow.
 */
function flushOpeningDab(heading) {
    const st = S.stroke;
    if (!st || !st._openingDab) return false;
    const o = st._openingDab;
    st._openingDab = null;
    if (!st.alphaMap) return false;
    const dyn = S.brushDynamics;
    const rot = (dyn.followStroke && Number.isFinite(heading) ? heading : 0)
        + _brushAngleRad();
    const savedTaper = st._taperK;
    const savedMask = S.editingMask;
    st._taperK = o.taperK;
    S.editingMask = o.mask;
    try {
        stampWet(o.x, o.y, o.p, rot);
    } finally {
        st._taperK = savedTaper;
        S.editingMask = savedMask;
    }
    _notePivot(o.x, o.y, o.p, rot);
    return true;
}

/**
 * Dab spacing as a fraction of the dab's own width.
 *
 * Lifted out of `plotTo` when BE3 gave `beginStroke` a reason to need the same
 * number: the opening dab has to know what gap follows it, or the first
 * interval of every stroke is priced differently from the rest.
 *
 * BE5 REMOVED THE SECOND HARDNESS CLIFF, which lived here:
 *
 *     return hardness < 0.01 ? 0.10 : baseSpacing * (0.3 + 0.7 * hardness);
 *
 * Below 0.01 the gap snapped to a flat 10% of the dab and above it dropped
 * back to the ordinary formula -- for Soft Brush, 0.100 against 0.024, a
 * four-fold change in dab count for a hundredth of a step on a control that
 * says nothing about it. The falloff curve had a cliff at the SAME threshold,
 * and fixing one without the other would have left the acceptance test failing
 * on the half nobody looked at.
 *
 * The 10% was there to serve the forced accumulation soft tips used to get. A
 * non-buildup soft tip now max-blends, where overlapping identical dabs are
 * idempotent, so wider spacing bought nothing and the ordinary formula applies
 * throughout.
 */
function spacingFraction() {
    const dyn = S.brushDynamics;
    const baseSpacing = Math.max(0.02, dyn.spacing || 0.08);
    const hardness = S.brushHardness ?? 1;
    // Softer tips blend into each other, so they are spaced tighter to keep the
    // stroke smooth; a hard tip can afford the preset's full gap. Continuous in
    // hardness, with no special case anywhere on the control's travel.
    return baseSpacing * (0.3 + 0.7 * hardness);
}

function beginStroke(x, y, p) {
    S.stroke.ctx.clearRect(0, 0, S.W, S.H);
    S.stroke.stampPoints = [];
    S.stroke.alphaMap = new Uint8Array(S.W * S.H);
    // BE17. The accumulator is stroke-scoped and allocated lazily, so it
    // is cleared on every path that ends a stroke -- a buffer left behind
    // is both a wrong answer for the next stroke and 48MB held on a
    // 6000x4000 document for nothing.
    S.stroke.accum = null;
    S.stroke._accumFor = null;
    // P. Brush V2's paper response for THIS stroke, set by its adapter after
    // this function returns. Cleared here so a Legacy stroke can never merge
    // through the table a V2 stroke left behind.
    S.stroke.paperTable = null;
    S.stroke.dirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
    // BE8. What changed since the last COMPOSITE, as opposed to since the start
    // of the stroke. `dirty` accumulates because commit needs the total; using
    // it to drive the live preview meant that by the end of a long stroke every
    // frame re-composited the stroke's whole bounding box.
    S.stroke.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
    S.stroke._cachedImg = null;
    // Lock draw target at stroke start so commitStroke can't hit the wrong
    // canvas if editingMask toggles mid-stroke (e.g. Q key, mode switch).
    S.stroke._commitTarget = drawTarget();
    S.stroke._commitMask = S.editingMask;
    // The eraser used to pre-fill the stroke canvas with a copy of the target
    // here, because it erased FROM that copy and `commitStroke` then replaced
    // the target with it. It accumulates into the alpha map like the brush
    // now, so there is nothing to copy -- and a full-canvas `drawImage` per
    // stroke goes with it.
    // Prime stabilizer so first dab uses same pressure pipeline as the rest
    S.stroke.points = [{ x, y, p }];
    S.stroke.lx = x; S.stroke.ly = y; S.stroke.lp = p;
    S.stroke._travelled = 0;
    // G1. A new stroke pivots from nothing: its first dab must not fill a turn
    // from wherever the previous stroke ended.
    if (S.stroke._pivot) S.stroke._pivot.set = false;
    S.stroke._pivots = 0;
    S.stroke._taperK = _taperFactor(0, brushPx());
    // BE11. Cleared, not left. A mouse stroke following a pen stroke would
    // otherwise inherit the pen's availability flags and act on a pressure
    // nothing measured -- the state leak this programme has now paid for
    // four times, one level down.
    //
    // `noteSample` runs on pointermove, so the pen state for the OPENING
    // dab arrives after this line. That dab uses the neutral modifiers,
    // which is correct rather than convenient: nothing has been measured
    // yet.
    S.stroke._pen = null;
    S.stroke._lastTime = null;
    S.stroke._airX = undefined;
    S.stroke._airY = undefined;
    // BE13. The filter's two-cell history. Seeded with the opening cell
    // as TENTATIVE rather than committed, so the very first bend of a
    // stroke is filtered like every other one -- a first cell that was
    // committed immediately would leave a permanent nub whenever the
    // stroke turned straight away.
    S.stroke._ppPrev = null;
    S.stroke._ppPrev2 = null;
    // BE16. THE OPENING DAB IS HELD, not stamped.
    //
    // It used to be laid here with `_saSmooth`, a MODULE global holding the
    // heading the PREVIOUS stroke ended on -- so the first press of every
    // flat-tipped stroke was oriented by the last thing the owner drew.
    // Measured on the shipped presets: Flat Chisel matched the dab it should
    // have been at IoU 0.31, Marker at 0.45, Bristle Rake at 0.39.
    //
    // The flag two lines below is BE7's, and its comment diagnoses exactly this
    // -- it was simply set AFTER the dab it was meant to protect, and never
    // read. `_headingKnown` is now cleared BEFORE anything can lay a dab.
    //
    // ONLY WHEN THE ANGLE CAN BE WRONG. Deferring costs one pointer event of
    // feedback at press, and for a TAP the dot appears on release rather than
    // on press -- a change in feel. Three presets have this defect; the other
    // thirteen, the eraser's round tip and mask mode should not pay for it.
    // See `dabIgnoresRotation`.
    S.stroke._headingKnown = false;
    if (dabIgnoresRotation()) {
        stampWet(x, y, p, dabRotation());
    } else {
        S.stroke._openingDab = {
            x: x, y: y, p: p,
            taperK: S.stroke._taperK,
            mask: !!S.editingMask,
        };
    }
    // BE3. The opening dab is dab number one, so the stroke owes a full gap
    // before the next. Seeded here rather than defaulted inside `plotTo`,
    // because a debt that started at zero would fire a second dab on the very
    // first pointermove however small -- reintroducing the event-rate
    // dependence at the one place it is most visible, the start of the mark.
    // No heading exists yet, so the opening gap is the scalar one. The
    // first dab's own tangent is unknown until the pointer moves.
    S.stroke._spacingDebt = spacingFor(p, brushPx(), spacingFraction());
    // BE12. LAST in beginStroke, after the opening dab and after the
    // spacing debt is seeded: a tick that fired into a half-built stroke
    // would deposit at coordinates nothing had set yet.
    startAirbrush();
}

// Ends a stroke. Everything that finishes a brush or eraser drag routes
// through here -- pointer-up, pointer-leave -- which makes it the one place
// that can honestly say "the owner just finished painting something".
/**
 * BE9. Walk the stroke out to where the pointer actually stopped.
 *
 * The stabiliser lags by design, so the last dab sits behind the lifted
 * pointer -- by up to the smoothing window, which at Smoothing 6 is 36 screen
 * pixels. Without this every mark ends short by an amount governed by a
 * setting that is supposed to be about steadiness, not length.
 *
 * NOT an unconditional extra dab. The brief forbids that, and nothing here
 * forces one: this calls `plotTo`, so BE3's spacing debt still decides whether
 * a dab is due. A stroke that already ended on the pointer emits nothing, and
 * a stroke that ended half a gap short still emits nothing -- it simply pays
 * down the debt, exactly as a sub-spacing pointer move does.
 *
 * The stabiliser is BYPASSED here on purpose. Feeding the endpoint through it
 * would produce another lagged position, which is the thing being corrected;
 * the owner's last real sample is the truth about where the stroke ends.
 */
function finishStroke(x, y, p) {
    if (!S.stroke || !S.stroke.alphaMap) return false;
    // BE16. A stroke that never moved has no tangent and never will, so its
    // held opening dab is laid now with no heading. This is the TAP: press and
    // release without dragging, which must still leave a mark.
    const openedTap = flushOpeningDab(null);
    // BE13. The tentative cell lands here, BEFORE the catch-up, so the
    // stroke ends in the order it was drawn.
    const flushed = flushPixelPerfect() || openedTap;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    const dx = x - S.stroke.lx, dy = y - S.stroke.ly;
    // Nothing to catch up on. Distinguished from "a dab is not due" because
    // only this case can be answered without consulting the spacing debt.
    if (dx * dx + dy * dy < 1e-12) return flushed;
    plotTo(x, y, Number.isFinite(p) ? p : S.stroke.lp);
    return true;
}

function commitStroke() {
    // BE12. FIRST, before anything is torn down. A deposition tick that
    // landed between the teardown and the stop would stamp into an alpha
    // map that is about to be composited -- or, worse, one that already
    // has been.
    stopAirbrush();
    // BE13. A backstop for callers that commit without finishing -- the
    // headless drivers, and any future caller that has not been taught.
    // `flushPixelPerfect` is a no-op when there is nothing pending, so
    // the ordinary path pays one null check.
    //
    // BE16 adds the opening dab to the same backstop, and it matters more here
    // than the cell does: a caller that begins a stroke and commits it without
    // a single move would otherwise commit NOTHING.
    flushOpeningDab(strokeHeading());
    flushPixelPerfect();
    // BE16. THE HEADING DIES WITH THE STROKE THAT PRODUCED IT.
    //
    // `_saSmooth` has never had a lifecycle: one live writer, no reader that
    // clears it, so its value outlives its stroke by construction. That is the
    // root cause behind every stale read, and it is why the flat-tip CURSOR in
    // canvas-ui.js previews the previous stroke's angle while merely hovering.
    //
    // The global itself is left alone -- it is exported as `strokeAngle` and
    // read outside this file -- and the FLAG is what says whether it means
    // anything. Cleared here, so between strokes there is honestly no heading.
    S.stroke._headingKnown = false;
    // Use draw target locked at beginStroke time to prevent layer wipe
    // if editingMask changed mid-stroke.
    const T = S.stroke._commitTarget || drawTarget();
    const wasMask = S.stroke._commitMask != null ? S.stroke._commitMask : S.editingMask;
    // ONE commit for both tools. They differ in a single argument.
    //
    // The eraser's branch used to read the WHOLE target and the whole stroke
    // canvas back as ImageData and blend them by hand whenever a selection was
    // active -- two full-canvas `getImageData` calls and a per-pixel loop over
    // every pixel in the document, painted or not. CT3d removed that, and the
    // comment here used to justify the replacement by saying `stampAlphaMap`
    // multiplied each stamp by the selection as it went.
    //
    // BE4 CORRECTS THAT. Multiplying per dab was never right: before
    // accumulation it turns the selection from an amount into a rate, and a
    // soft tip ignored it completely (102/102/102 at 100/50/25 percent).
    //
    // The performance argument stands and does not apply to the fix. It was
    // about a WHOLE-DOCUMENT pass that read the destination back;
    // `alphaMapToImageData` walks only the stroke's dirty rectangle and reads
    // nothing back, so the bound costs one multiply in a loop that already
    // runs. Selection is applied there, exactly once.
    if (S.stroke.alphaMap) {
        const img = alphaMapToImageData(drawColor());
        const d2 = S.stroke.dirty;
        const dx = Math.max(0, d2.x0), dy = Math.max(0, d2.y0);
        const dw = Math.min(S.W, d2.x1 + 1) - dx, dh = Math.min(S.H, d2.y1 + 1) - dy;
        if (dw > 0 && dh > 0) {
            S.stroke.ctx.clearRect(0, 0, S.W, S.H);
            S.stroke.ctx.putImageData(img, 0, 0, dx, dy, dw, dh);
        }
        T.ctx.save();
        // A mask stroke is binarised on export whatever alpha it carries, so
        // compositing it at anything but 1 would only change what the owner
        // sees and not what the engine receives.
        // BE17. Buildup moved Opacity INSIDE the stroke: its per-dab
        // target is Flow x Opacity, so applying Opacity again here would
        // square it. Off -- which is thirteen of sixteen presets -- this
        // is byte-for-byte the line it replaced.
        T.ctx.globalAlpha = wasMask ? 1 : (S.brushBuildup ? 1 : S.brushOpacity);
        // THE ONE DIFFERENCE between painting and erasing.
        T.ctx.globalCompositeOperation =
            strokeTool() === "eraser" ? "destination-out" : "source-over";
        // U2. BOUNDED TO THE STROKE. Outside its dirty rectangle the stroke
        // canvas is fully transparent, and neither `source-over` nor
        // `destination-out` changes a destination pixel under a zero-alpha
        // source -- so this is the same result over a smaller rectangle. At
        // 4096 square a full-canvas commit blit is 16.8 million pixels to lay
        // down a stroke that may cover a few thousand.
        if (dw > 0 && dh > 0) {
            T.ctx.drawImage(S.stroke.canvas, dx, dy, dw, dh, dx, dy, dw, dh);
        } else {
            T.ctx.drawImage(S.stroke.canvas, 0, 0);
        }
        T.ctx.restore();
    }
    // U2. The display now owes exactly this rectangle. Published BEFORE the
    // stroke state is torn down, because `S.stroke.dirty` is about to be reset
    // and this is the last moment the committed region is known.
    //
    // A stroke that painted nothing publishes nothing: `markCompositeDirty`
    // would have invalidated the entire display for a stroke that changed no
    // pixel, which is the same confident-wrong-number failure the V2 execution
    // guard exists to prevent, wearing a different hat.
    if (S.stroke.dirty && S.stroke.dirty.x1 >= S.stroke.dirty.x0
        && S.stroke.dirty.y1 >= S.stroke.dirty.y0) {
        markCompositeDirtyRegion(S.stroke.dirty.x0, S.stroke.dirty.y0,
                                 S.stroke.dirty.x1 + 1, S.stroke.dirty.y1 + 1);
    }
    S.stroke.alphaMap = null;
    S.stroke.accum = null;
    S.stroke._accumFor = null;
    S.stroke._cachedImg = null;
    S.stroke._commitTarget = null;
    S.stroke._commitMask = null;
    S.stroke.ctx.clearRect(0, 0, S.W, S.H);
    S.drawing = false;
    const registeredStroke = !!_strokeTransaction;
    clearStrokeUndo();
    // The stroke is now ON the layer and the document is coherent. Last line
    // deliberately: a listener that reads the canvas must see the finished
    // stroke, not the buffer mid-merge.
    if (!registeredStroke) _notifyActionComplete("stroke");
}

// ========================================================================
// SMUDGE
// ========================================================================
function _symPoints(x, y) {
    // Returns array of {x, y} for all symmetry-derived points (including original)
    const pts = [{ x, y }];
    if (S.symmetry === "h" || S.symmetry === "both") pts.push({ x: S.W - x, y });
    if (S.symmetry === "v" || S.symmetry === "both") pts.push({ x, y: S.H - y });
    if (S.symmetry === "both") pts.push({ x: S.W - x, y: S.H - y });
    if (S.symmetry === "radial") {
        const n = S.symmetryAxes || 4;
        const ccx = S.W / 2, ccy = S.H / 2;
        for (let k = 1; k < n; k++) {
            const a = (2 * Math.PI * k) / n;
            pts.push({
                x: ccx + (x - ccx) * Math.cos(a) - (y - ccy) * Math.sin(a),
                y: ccy + (x - ccx) * Math.sin(a) + (y - ccy) * Math.cos(a)
            });
        }
    }
    return pts;
}

function _smudgeInitAt(ctx, x, y) {
    const sz = ~~Math.max(6, brushPx()), r = sz / 2;
    const ix = ~~Math.max(0, x - r), iy = ~~Math.max(0, y - r);
    const ex = ~~Math.min(S.W, x + r), ey = ~~Math.min(S.H, y + r);
    const w = ex - ix, h = ey - iy;
    if (w < 2 || h < 2) return null;
    const s = ctx.getImageData(ix, iy, w, h), d = s.data;
    const cl = x - ix, ct = y - iy;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const dist = Math.hypot(px - cl, py - ct) / r, i = (py * w + px) * 4;
        if (dist > 1) { d[i] = 0; d[i+1] = 0; d[i+2] = 0; d[i + 3] = 0; }
        else if (dist > 0.6) {
            const fade = 1 - (dist - 0.6) / 0.4;
            d[i + 3] = ~~(d[i + 3] * fade);
        }
    }
    return { imageData: s, w, h };
}

function smudgeInit(ctx, x, y) {
    const pts = _symPoints(x, y);
    S.smudgeBuffer = _smudgeInitAt(ctx, pts[0].x, pts[0].y);
    S._smudgeSymBuffers = pts.length > 1 ? pts.slice(1).map(p => _smudgeInitAt(ctx, p.x, p.y)) : null;
}

function _smudgeDragAt(ctx, x, y, p, buffer) {
    if (!buffer) return null;
    const sz = ~~Math.max(6, brushPx()), r = sz / 2;
    const str = S.toolStrength * (S.pressureSensitivity ? Math.max(0.1, p) : 1);
    const ix = ~~Math.max(0, x - r), iy = ~~Math.max(0, y - r);
    const ex = ~~Math.min(S.W, x + r), ey = ~~Math.min(S.H, y + r);
    const w = ex - ix, h = ey - iy;
    if (w < 2 || h < 2) return buffer;
    const under = ctx.getImageData(ix, iy, w, h);
    const bufW = buffer.w, bufH = buffer.h;
    const bd = buffer.imageData.data;
    const ud = under.data;
    const cl = x - ix, ct = y - iy;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const dist = Math.hypot(px - cl, py - ct) / r;
        if (dist > 1) continue;
        const bx = ~~(px * bufW / w), by = ~~(py * bufH / h);
        if (bx < 0 || bx >= bufW || by < 0 || by >= bufH) continue;
        const bi = (by * bufW + bx) * 4;
        if (bd[bi + 3] < 2) continue;
        const i = (py * w + px) * 4;
        const blend = (1 - dist) * str;
        ud[i]     = ud[i] + (bd[bi] - ud[i]) * blend;
        ud[i + 1] = ud[i + 1] + (bd[bi + 1] - ud[i + 1]) * blend;
        ud[i + 2] = ud[i + 2] + (bd[bi + 2] - ud[i + 2]) * blend;
        const blendedA = ud[i + 3] + (bd[bi + 3] - ud[i + 3]) * blend;
        if (blendedA > ud[i + 3]) ud[i + 3] = blendedA;
    }
    ctx.putImageData(under, ix, iy);
    // Refresh buffer
    const ns = ctx.getImageData(ix, iy, w, h), nd = ns.data;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const dist = Math.hypot(px - cl, py - ct) / r, i = (py * w + px) * 4;
        if (dist > 1) { nd[i] = 0; nd[i+1] = 0; nd[i+2] = 0; nd[i + 3] = 0; }
        else if (dist > 0.6) {
            const fade = 1 - (dist - 0.6) / 0.4;
            nd[i + 3] = ~~(nd[i + 3] * fade);
        }
    }
    return { imageData: ns, w, h };
}

function smudgeDrag(ctx, x, y, p) {
    if (!S.smudgeBuffer) return;
    const pts = _symPoints(x, y);
    S.smudgeBuffer = _smudgeDragAt(ctx, pts[0].x, pts[0].y, p, S.smudgeBuffer);
    if (S._smudgeSymBuffers) {
        for (let k = 0; k < S._smudgeSymBuffers.length; k++) {
            const sp = pts[k + 1];
            if (sp) S._smudgeSymBuffers[k] = _smudgeDragAt(ctx, sp.x, sp.y, p, S._smudgeSymBuffers[k]);
        }
    }
}

function smudgeStroke(ctx, x1, y1, x2, y2, p1, p2) {
    const dx = x2 - x1, dy = y2 - y1, dist = Math.hypot(dx, dy);
    const sp = Math.max(2, brushPx() * 0.25);
    const steps = Math.max(1, Math.ceil(dist / sp));
    for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        smudgeDrag(ctx, x1 + dx * t, y1 + dy * t, p1 + (p2 - p1) * t);
    }
}

// ========================================================================
// BLUR TOOL
// ========================================================================
function blurAt(ctx, x, y, p) {
    const sz = Math.max(6, pSz(p)), r = ~~(sz / 2);
    const ix = ~~Math.max(0, x - r), iy = ~~Math.max(0, y - r);
    const ex = ~~Math.min(S.W, x + r), ey = ~~Math.min(S.H, y + r);
    const w = ex - ix, h = ey - iy;
    if (w < 3 || h < 3) return;
    const img = ctx.getImageData(ix, iy, w, h);
    const d = img.data;
    const out = new Uint8ClampedArray(d.length);
    const kR = Math.max(1, ~~(S.toolStrength * 4));
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        let rr = 0, gg = 0, bb = 0, aa = 0, cnt = 0;
        for (let ky = -kR; ky <= kR; ky++) for (let kx = -kR; kx <= kR; kx++) {
            const sx2 = px + kx, sy2 = py + ky;
            if (sx2 >= 0 && sx2 < w && sy2 >= 0 && sy2 < h) {
                const si = (sy2 * w + sx2) * 4;
                rr += d[si]; gg += d[si + 1]; bb += d[si + 2]; aa += d[si + 3]; cnt++;
            }
        }
        const di = (py * w + px) * 4;
        out[di] = rr / cnt; out[di + 1] = gg / cnt; out[di + 2] = bb / cnt; out[di + 3] = aa / cnt;
    }
    const cl = w / 2, ct = h / 2;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const dist = Math.hypot(px - cl, py - ct) / r;
        if (dist > 1) continue;
        const b = 1 - dist, di = (py * w + px) * 4;
        d[di] += (out[di] - d[di]) * b;
        d[di + 1] += (out[di + 1] - d[di + 1]) * b;
        d[di + 2] += (out[di + 2] - d[di + 2]) * b;
        d[di + 3] += (out[di + 3] - d[di + 3]) * b;
    }
    ctx.putImageData(img, ix, iy);
}

// ========================================================================
// PIXELATE / CENSOR
// ========================================================================
function pixelateAt(ctx, x, y, p) {
    const sz = Math.max(8, pSz(p)), r = ~~(sz / 2);
    const blockSize = Math.max(4, ~~(sz * Math.max(0.1, S.toolStrength)));
    const ix = ~~Math.max(0, x - r), iy = ~~Math.max(0, y - r);
    const ex = ~~Math.min(S.W, x + r), ey = ~~Math.min(S.H, y + r);
    const w = ex - ix, h = ey - iy;
    if (w < 4 || h < 4) return;
    // Align to global grid so overlapping strokes produce consistent blocks
    const gx0 = ix - (((ix % blockSize) + blockSize) % blockSize);
    const gy0 = iy - (((iy % blockSize) + blockSize) % blockSize);
    const img = ctx.getImageData(ix, iy, w, h);
    const d = img.data;
    const cl = x - ix, ct = y - iy;
    for (let by = gy0; by < ey; by += blockSize) {
        for (let bx = gx0; bx < ex; bx += blockSize) {
            // Check if block center is within brush circle
            const bcx = bx + blockSize / 2, bcy = by + blockSize / 2;
            const dist = Math.hypot(bcx - x, bcy - y) / r;
            if (dist > 1) continue;
            // Average colors in this block
            let rr = 0, gg = 0, bb = 0, aa = 0, cnt = 0;
            for (let py = Math.max(by, iy); py < Math.min(by + blockSize, ey); py++) {
                for (let px = Math.max(bx, ix); px < Math.min(bx + blockSize, ex); px++) {
                    const i = ((py - iy) * w + (px - ix)) * 4;
                    rr += d[i]; gg += d[i + 1]; bb += d[i + 2]; aa += d[i + 3]; cnt++;
                }
            }
            if (cnt === 0) continue;
            rr = ~~(rr / cnt); gg = ~~(gg / cnt); bb = ~~(bb / cnt); aa = ~~(aa / cnt);
            // Fill block with average
            for (let py = Math.max(by, iy); py < Math.min(by + blockSize, ey); py++) {
                for (let px = Math.max(bx, ix); px < Math.min(bx + blockSize, ex); px++) {
                    const i = ((py - iy) * w + (px - ix)) * 4;
                    d[i] = rr; d[i + 1] = gg; d[i + 2] = bb; d[i + 3] = aa;
                }
            }
        }
    }
    ctx.putImageData(img, ix, iy);
}

// ========================================================================
// DODGE / BURN
// ========================================================================
function dodgeBurnAt(ctx, x, y, p) {
    const sz = Math.max(6, pSz(p)), r = ~~(sz / 2);
    const str = S.toolStrength * (S.pressureSensitivity ? Math.max(0.1, p) : 1);
    const ix = ~~Math.max(0, x - r), iy = ~~Math.max(0, y - r);
    const ex = ~~Math.min(S.W, x + r), ey = ~~Math.min(S.H, y + r);
    const w = ex - ix, h = ey - iy;
    if (w < 2 || h < 2) return;
    const img = ctx.getImageData(ix, iy, w, h), d = img.data;
    const isDodge = S._dodgeMode === "dodge";
    const cl = x - ix, ct = y - iy, hard = S.brushHardness;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        const dist = Math.hypot(px - cl, py - ct) / r;
        if (dist > 1) continue;
        const innerR = hard;
        let falloff = 1;
        if (dist > innerR && innerR < 1) falloff = 1 - (dist - innerR) / (1 - innerR);
        const amount = str * falloff * 0.012;
        const i = (py * w + px) * 4;
        if (d[i + 3] < 2) continue;
        if (isDodge) {
            d[i]     = Math.min(255, d[i] + amount * (255 - d[i]));
            d[i + 1] = Math.min(255, d[i + 1] + amount * (255 - d[i + 1]));
            d[i + 2] = Math.min(255, d[i + 2] + amount * (255 - d[i + 2]));
        } else {
            d[i]     = Math.max(0, d[i] - amount * d[i]);
            d[i + 1] = Math.max(0, d[i + 1] - amount * d[i + 1]);
            d[i + 2] = Math.max(0, d[i + 2] - amount * d[i + 2]);
        }
    }
    ctx.putImageData(img, ix, iy);
}

function dodgeBurnStroke(ctx, x1, y1, x2, y2, p) {
    const dx = x2 - x1, dy = y2 - y1, dist = Math.hypot(dx, dy);
    const sp = Math.max(1, brushPx() * 0.08);
    const steps = Math.max(1, Math.ceil(dist / sp));
    for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        dodgeBurnAt(ctx, x1 + dx * t, y1 + dy * t, p);
    }
}

// ========================================================================
// LIQUIFY
// ========================================================================
function liquifyPush(ctx, cx, cy, dx, dy, pressure) {
    const sz = Math.max(8, pSz(pressure));
    const r = sz / 2;
    const str = S.toolStrength * 0.25 * (S.pressureSensitivity ? Math.max(0.1, pressure) : 1);
    const hard = S.brushHardness;
    const pad = Math.ceil(Math.hypot(dx, dy) * str) + 4;
    const ix = Math.max(0, Math.floor(cx - r - pad));
    const iy = Math.max(0, Math.floor(cy - r - pad));
    const ex = Math.min(S.W, Math.ceil(cx + r + pad));
    const ey = Math.min(S.H, Math.ceil(cy + r + pad));
    const w = ex - ix, h = ey - iy;
    if (w < 4 || h < 4) return;
    const src = ctx.getImageData(ix, iy, w, h);
    const dst = new ImageData(new Uint8ClampedArray(src.data), w, h);
    const sd = src.data, dd = dst.data;
    const mode = S.liquifyMode || "move";

    for (let py = 0; py < h; py++) {
        for (let px = 0; px < w; px++) {
            const worldX = px + ix, worldY = py + iy;
            const distFromCenter = Math.hypot(worldX - cx, worldY - cy);
            if (distFromCenter >= r) continue;
            const innerR = r * hard;
            let falloff = 1;
            if (distFromCenter > innerR && innerR < r) {
                falloff = 1 - (distFromCenter - innerR) / (r - innerR);
            }
            const weight = falloff * str;

            // Compute displacement based on mode
            let dispX, dispY;
            if (mode === "move") {
                dispX = dx * weight;
                dispY = dy * weight;
            } else if (mode === "pinch") {
                // Pull toward brush center
                const toX = cx - worldX, toY = cy - worldY;
                dispX = toX * weight * 0.15;
                dispY = toY * weight * 0.15;
            } else if (mode === "bloat") {
                // Push away from brush center
                const awayX = worldX - cx, awayY = worldY - cy;
                const d = Math.max(distFromCenter, 0.001);
                dispX = (awayX / d) * weight * r * 0.08;
                dispY = (awayY / d) * weight * r * 0.08;
            } else if (mode === "twirl_cw" || mode === "twirl_ccw") {
                // Rotate around brush center
                const relX = worldX - cx, relY = worldY - cy;
                const angle = weight * 0.3 * (mode === "twirl_ccw" ? -1 : 1);
                const cos = Math.cos(angle), sin = Math.sin(angle);
                dispX = (relX * cos - relY * sin) - relX;
                dispY = (relX * sin + relY * cos) - relY;
            } else {
                dispX = dx * weight;
                dispY = dy * weight;
            }

            // Bilinear sample from displaced source position
            const srcPxF = px - dispX;
            const srcPyF = py - dispY;
            const sx0 = Math.floor(srcPxF), sy0 = Math.floor(srcPyF);
            const fx = srcPxF - sx0, fy = srcPyF - sy0;
            const sx1 = sx0 + 1, sy1 = sy0 + 1;
            if (sx0 < 0 || sy0 < 0 || sx1 >= w || sy1 >= h) continue;
            const i00 = (sy0 * w + sx0) * 4;
            const i10 = (sy0 * w + sx1) * 4;
            const i01 = (sy1 * w + sx0) * 4;
            const i11 = (sy1 * w + sx1) * 4;
            const di = (py * w + px) * 4;
            for (let ch = 0; ch < 4; ch++) {
                const v = sd[i00 + ch] * (1 - fx) * (1 - fy) + sd[i10 + ch] * fx * (1 - fy) +
                          sd[i01 + ch] * (1 - fx) * fy + sd[i11 + ch] * fx * fy;
                dd[di + ch] = Math.round(v);
            }
        }
    }
    ctx.putImageData(dst, ix, iy);
}

// ========================================================================
// CLONE STAMP
// ========================================================================
function cloneStamp(x, y, p) {
    if (!S._cloneSource || !S._cloneOffset) return;
    const T = drawTarget();
    const sz = pSz(p), r = sz / 2;
    const srcX = x + S._cloneOffset.dx, srcY = y + S._cloneOffset.dy;
    // Composite visible layers for source sampling
    const tc = getTempCanvas("cloneSrc", S.W, S.H);
    const tctx = tc.getContext("2d", { colorSpace: "srgb" });
    for (const L of S.layers) {
        if (L.visible && L.canvas) { tctx.globalAlpha = L.opacity; tctx.drawImage(L.canvas, 0, 0); }
    }
    const stamp = getTempCanvas("cloneStamp", Math.ceil(sz), Math.ceil(sz));
    const sctx = stamp.getContext("2d", { colorSpace: "srgb" });
    sctx.save();
    sctx.beginPath(); sctx.arc(r, r, r, 0, Math.PI * 2); sctx.clip();
    sctx.drawImage(tc, srcX - r, srcY - r, sz, sz, 0, 0, sz, sz);
    sctx.restore();
    // `sz >= 2` because below that there is no edge to soften, and trying
    // erased the dab entirely. A 1x1 stamp has one pixel, whose centre sits
    // hypot(0.5, 0.5) = 0.707 from the middle while the radius is 0.5 -- so
    // the falloff is evaluated OUTSIDE the dab and multiplies the only pixel
    // there is by zero. Measured: at minimum Size with Hardness under 100%,
    // Clone Stamp painted nothing at all, silently. At Hardness 100% this
    // block is skipped and the tool always worked, which is why it survived.
    if (S.brushHardness < 1 && sz >= 2) {
        const sd = sctx.getImageData(0, 0, Math.ceil(sz), Math.ceil(sz));
        for (let py = 0; py < sd.height; py++) for (let px = 0; px < sd.width; px++) {
            const dist = Math.hypot(px - r, py - r);
            const a = _dabFalloff(dist, r, S.brushHardness);
            sd.data[(py * sd.width + px) * 4 + 3] = Math.round(sd.data[(py * sd.width + px) * 4 + 3] * a);
        }
        sctx.putImageData(sd, 0, 0);
    }
    T.ctx.save(); T.ctx.globalAlpha = pOpacity(p);
    T.ctx.drawImage(stamp, x - r, y - r);
    T.ctx.restore();
}

// ========================================================================
// FLOOD FILL
// ========================================================================
function floodFill(pt) {
    const T = drawTarget(), ctx = T.ctx, w = S.W, h = S.H;
    const sx = ~~pt.x, sy = ~~pt.y;
    if (sx < 0 || sx >= w || sy < 0 || sy >= h) return;
    if (S.selection.active && S.selection.mask && S.selection.mask[sy * w + sx] === 0) return;
    const img = ctx.getImageData(0, 0, w, h), d = img.data;
    const idx = (sy * w + sx) * 4;
    const tR = d[idx], tG = d[idx + 1], tB = d[idx + 2], tA = d[idx + 3];
    const fc = hexRgb(drawColor()), fA = ~~(S.brushOpacity * 255);
    if (tR === fc.r && tG === fc.g && tB === fc.b && tA === fA) return;
    const tol = 32, stack = [sx, sy], vis = new Uint8Array(w * h);
    const sel = S.selection.active ? S.selection.mask : null;
    while (stack.length) {
        const cy2 = stack.pop(), cx2 = stack.pop();
        const ci = cy2 * w + cx2;
        if (vis[ci]) continue;
        if (sel && sel[ci] === 0) continue;
        const pi = ci * 4;
        if (Math.abs(d[pi] - tR) > tol || Math.abs(d[pi + 1] - tG) > tol ||
            Math.abs(d[pi + 2] - tB) > tol || Math.abs(d[pi + 3] - tA) > tol) continue;
        vis[ci] = 1;
        d[pi] = fc.r; d[pi + 1] = fc.g; d[pi + 2] = fc.b; d[pi + 3] = fA;
        if (cx2 > 0) stack.push(cx2 - 1, cy2);
        if (cx2 < w - 1) stack.push(cx2 + 1, cy2);
        if (cy2 > 0) stack.push(cx2, cy2 - 1);
        if (cy2 < h - 1) stack.push(cx2, cy2 + 1);
    }
    ctx.putImageData(img, 0, 0);
}

// ========================================================================
// GRADIENT
// ========================================================================
function drawGradient(start, end, mode) {
    const T = drawTarget();
    const col = drawColor(), rgb = hexRgb(col);
    T.ctx.save();
    let grad;
    if (mode === "radial") {
        const r = Math.hypot(end.x - start.x, end.y - start.y);
        grad = T.ctx.createRadialGradient(start.x, start.y, 0, start.x, start.y, r);
    } else {
        grad = T.ctx.createLinearGradient(start.x, start.y, end.x, end.y);
    }
    grad.addColorStop(0, `rgba(${rgb.r},${rgb.g},${rgb.b},${S.brushOpacity})`);
    grad.addColorStop(1, `rgba(${rgb.r},${rgb.g},${rgb.b},0)`);
    if (S.selection.active && S.selection.mask) {
        const tc = getTempCanvas("gradientMask", S.W, S.H);
        const tctx = tc.getContext("2d", { colorSpace: "srgb" });
        tctx.fillStyle = grad;
        tctx.fillRect(0, 0, S.W, S.H);
        const gd = tctx.getImageData(0, 0, S.W, S.H);
        for (let i = 0; i < S.selection.mask.length; i++) {
            if (S.selection.mask[i] === 0) gd.data[i * 4 + 3] = 0;
            else gd.data[i * 4 + 3] = Math.round(gd.data[i * 4 + 3] * S.selection.mask[i] / 255);
        }
        tctx.putImageData(gd, 0, 0);
        T.ctx.drawImage(tc, 0, 0);
    } else {
        T.ctx.fillStyle = grad;
        T.ctx.fillRect(0, 0, S.W, S.H);
    }
    T.ctx.restore();
}

// ========================================================================
// SHAPE DRAWING
// ========================================================================
function drawShapePath(ctx, mode, x1, y1, x2, y2, filled) {
    if (mode === "line") {
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    } else if (mode === "ellipse") {
        const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
        const rx = Math.abs(x2 - x1) / 2, ry = Math.abs(y2 - y1) / 2;
        ctx.beginPath(); ctx.ellipse(cx, cy, Math.max(1, rx), Math.max(1, ry), 0, 0, Math.PI * 2);
        if (filled) ctx.fill(); else ctx.stroke();
    } else {
        const x = Math.min(x1, x2), y = Math.min(y1, y2);
        const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
        if (filled) ctx.fillRect(x, y, w, h); else ctx.strokeRect(x, y, w, h);
    }
}

function commitShape(start, end, mode, filled) {
    const T = drawTarget(), col = drawColor();
    T.ctx.save();
    T.ctx.strokeStyle = col; T.ctx.fillStyle = col;
    T.ctx.lineWidth = brushPx();
    T.ctx.globalAlpha = S.brushOpacity;
    drawShapePath(T.ctx, mode, start.x, start.y, end.x, end.y, filled);
    T.ctx.restore();
}

// ========================================================================
// EYEDROPPER
// ========================================================================
function pickColor(pt) {
    const radius = S.sampleRadius || 1;
    const merged = S.sampleMerged || false;

    if (merged) {
        // Sample from composited visible layers
        const tmp = _createCanvas(S.W, S.H);
        const tc = tmp.getContext("2d", { colorSpace: "srgb" });
        for (let i = 0; i < S.layers.length; i++) {
            const L = S.layers[i];
            if (!L.visible || !L.canvas) continue;
            tc.globalAlpha = L.opacity ?? 1;
            tc.globalCompositeOperation = L.blendMode && _blendToPS[L.blendMode] ? L.blendMode : "source-over";
            tc.drawImage(L.canvas, 0, 0);
        }
        tc.globalAlpha = 1; tc.globalCompositeOperation = "source-over";
        const avg = _sampleAverage(tc, pt.x, pt.y, radius);
        S.color = rgbHex(avg[0], avg[1], avg[2]);
    } else {
        // Sample from topmost visible layer with content
        let src = null;
        for (let i = S.layers.length - 1; i >= 0; i--) {
            const L = S.layers[i];
            if (!L.visible || !L.canvas) continue;
            const px = L.ctx.getImageData(~~pt.x, ~~pt.y, 1, 1).data;
            if (px[3] > 10) {
                src = _sampleAverage(L.ctx, pt.x, pt.y, radius);
                break;
            }
        }
        if (!src) src = _sampleAverage(S.layers[0].ctx, pt.x, pt.y, radius);
        S.color = rgbHex(src[0], src[1], src[2]);
    }
    addColor(S.color);
}

function _sampleAverage(ctx, cx, cy, radius) {
    if (radius <= 1) {
        const d = ctx.getImageData(~~cx, ~~cy, 1, 1).data;
        return [d[0], d[1], d[2]];
    }
    const r = Math.floor(radius / 2);
    const x0 = Math.max(0, ~~cx - r), y0 = Math.max(0, ~~cy - r);
    const sz = radius;
    const data = ctx.getImageData(x0, y0, sz, sz).data;
    let rr = 0, gg = 0, bb = 0, count = 0;
    const cr = sz / 2;
    for (let py = 0; py < sz; py++) {
        for (let px = 0; px < sz; px++) {
            // Circular sample area
            if (Math.hypot(px - cr + 0.5, py - cr + 0.5) > cr) continue;
            const idx = (py * sz + px) * 4;
            if (data[idx + 3] < 10) continue; // skip transparent
            rr += data[idx]; gg += data[idx + 1]; bb += data[idx + 2];
            count++;
        }
    }
    if (count === 0) return [0, 0, 0];
    return [Math.round(rr / count), Math.round(gg / count), Math.round(bb / count)];
}

// ========================================================================
// COLOR HISTORY
// ========================================================================
function addColor(hex) {
    hex = hex.toLowerCase();
    S.colorHistory = S.colorHistory.filter(c => c !== hex);
    S.colorHistory.unshift(hex);
    if (S.colorHistory.length > 10) S.colorHistory.pop();
}

// ========================================================================
// ZOOM / PAN
// ========================================================================
function zoomAt(screenX, screenY, factor) {
    const z = S.zoom;
    if (!S.canvas) return;
    // CSS-pixel coords on the canvas (zoom is stored in CSS units; the DPR
    // multiplier is applied at draw time by applyDisplayTransform).
    const r = S.canvas.getBoundingClientRect();
    const ex = screenX - r.left;
    const ey = screenY - r.top;
    const newScale = Math.min(16, Math.max(0.1, z.scale * factor));
    z.ox = ex - (ex - z.ox) / z.scale * newScale;
    z.oy = ey - (ey - z.oy) / z.scale * newScale;
    z.scale = newScale;
}

function zoomFit() {
    if (!S.canvas) return;
    // Fit math runs in CSS dims so the visual layout doesn't change with
    // HiDPI buffers. viewportCssW/H are stamped by syncCanvasToViewport;
    // fall back to the canvas style box if they aren't yet.
    const cw = S.viewportCssW || parseFloat(S.canvas.style.width) || S.canvas.width;
    const ch = S.viewportCssH || parseFloat(S.canvas.style.height) || S.canvas.height;
    if (!cw || !ch || !S.W || !S.H) {
        S.zoom.scale = 1; S.zoom.ox = 0; S.zoom.oy = 0; return;
    }
    const sx = cw / S.W, sy = ch / S.H;
    const scale = Math.min(sx, sy) * 0.9;
    S.zoom.scale = scale;
    S.zoom.ox = (cw - S.W * scale) / 2;
    S.zoom.oy = (ch - S.H * scale) / 2;
}

// Screen coordinates → document coordinates. Pointer events deliver CSS
// pixels; zoom is stored in CSS pixels; no DPR multiplication needed here.
function screenToDoc(screenX, screenY) {
    const z = S.zoom;
    if (!S.canvas) return { x: 0, y: 0 };
    const r = S.canvas.getBoundingClientRect();
    const canvasX = screenX - r.left;
    const canvasY = screenY - r.top;
    return {
        x: (canvasX - z.ox) / z.scale,
        y: (canvasY - z.oy) / z.scale
    };
}

// ========================================================================
// UNDO / REDO
// ========================================================================
function _undoTarget() {
    if (S.editingMask) return { ctx: S.mask.ctx, id: "mask" };
    const L = activeLayer();
    return { ctx: L.ctx, id: L.id };
}

function _undoResolve(id) {
    if (id === "mask") return S.mask.ctx;
    const L = S.layers.find(l => l.id === id);
    return L ? L.ctx : null;
}

function _inferActionLabel() {
    const labels = {
        mask: S.maskOperation === "subtract" ? "Subtract generation mask" : "Add generation mask",
        brush: "Brush stroke", eraser: "Erase", smudge: "Smudge", blur: "Blur",
        fill: "Fill", gradient: "Gradient", shape: "Shape", text: "Text",
        clone: "Clone stamp", dodge: "Dodge/Burn", liquify: "Liquify",
        eyedropper: "Eyedropper", select: "Select", lasso: "Lasso",
        transform: "Transform", crop: "Crop"
    };
    return labels[S.tool] || "Paint";
}

const _revisionListeners = [];
const _actionListeners = [];

function _notifyActionComplete(reason) {
    // Listener faults are contained: a broken recovery hook must never break
    // painting. Same rule as the revision listeners below.
    for (const fn of _actionListeners) {
        try { fn(reason); } catch (e) {
            console.warn("[Canvas] action listener failed:", e);
        }
    }
}

function _newDocumentId() {
    // `crypto.getRandomValues` rather than Math.random: this is an identity,
    // and identities that collide stop being identities.
    const bytes = new Uint8Array(16);
    (self.crypto || window.crypto).getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

function _startNewDocument() {
    S.documentId = _newDocumentId();
    S.canvasRevision = 0;
}

function _bumpRevision() {
    // Called from the two save functions and from undo/redo -- four sites, not
    // six. The three other `undoStack.push` calls live INSIDE undo/redo and
    // restore rather than mutate; bumping there would count one owner action
    // twice.
    //
    // Nothing else needs to call this, and that is the point: selection, tool
    // changes, zoom and panel state never push an undo step, so "cosmetic
    // changes must not bump" holds by construction instead of by remembering
    // to exclude each one.
    if (!S.documentId) _startNewDocument();
    S.canvasRevision += 1;
    // Notify AFTER the bump so a listener reading the identity sees the new
    // revision. Listener faults are contained: a broken recovery hook must
    // never break painting.
    for (const fn of _revisionListeners) {
        try { fn(S.documentId, S.canvasRevision); } catch (e) {
            console.warn("[Canvas] revision listener failed:", e);
        }
    }

    // For an INSTANTANEOUS action the bump is also the completion: a flood
    // fill, a gradient, a shape, a delete, a layer operation or a result
    // dropped on the canvas is finished the moment it bumps. A stroke is
    // not -- `S.drawing` is true for its whole length, and it completes at
    // `commitStroke` instead.
    if (!S.drawing) _notifyActionComplete("action");
}

// The snapshot must be of the canvas the caller is about to change. Region
// painting names its region and a layer command names its layer; otherwise it
// is what `drawTarget()` would write to. Guessing a region from `regionMode`
// made undo after Fill, Gradient, Shape or Delete in Regional mode restore the
// region and leave the edited layer as it was.
function saveUndo(label, target) {
    if (_strokeTransaction) abortStroke();
    S._canvasDirty = true;
    _bumpRevision();
    markCompositeDirty();
    if (target && target.region) {
        const r = target.region;
        S.undoStack.push({
            type: "region", regionId: r.id,
            data: r.ctx.getImageData(0, 0, S.W, S.H),
            label: label || "Region paint"
        });
        if (S.undoStack.length > S.maxUndo) S.undoStack.shift();
        S.redoStack = [];
        return;
    }
    const t = target && target.layer ? { ctx: target.layer.ctx, id: target.layer.id } : _undoTarget();
    S.undoStack.push({
        type: "pixel", layerId: t.id,
        userMaskMode: t.id === "mask" ? !!S._userMaskMode : undefined,
        data: t.ctx.getImageData(0, 0, S.W, S.H),
        label: label || _inferActionLabel()
    });
    if (S.undoStack.length > S.maxUndo) S.undoStack.shift();
    S.redoStack = [];
}

function saveStructuralUndo(label) {
    if (_strokeTransaction) abortStroke();
    S._canvasDirty = true;
    _bumpRevision();
    markCompositeDirty();
    const snapshot = {
        type: "structural",
        label: label || "Layer change",
        layers: S.layers.map(L => {
            if (L.type === "adjustment") {
                return {
                    id: L.id, name: L.name, type: L.type,
                    adjustType: L.adjustType,
                    adjustParams: JSON.parse(JSON.stringify(L.adjustParams || {})),
                    visible: L.visible, opacity: L.opacity,
                    blendMode: L.blendMode, locked: L.locked, data: null
                };
            }
            return {
                id: L.id, name: L.name, type: L.type,
                visible: L.visible, opacity: L.opacity,
                blendMode: L.blendMode, locked: L.locked,
                data: L.ctx.getImageData(0, 0, S.W, S.H)
            };
        }),
        activeIdx: S.activeLayerIdx, editingMask: S.editingMask, userMaskMode: !!S._userMaskMode,
        maskData: S.mask.ctx.getImageData(0, 0, S.W, S.H),
        canvasW: S.W, canvasH: S.H
    };
    S.undoStack.push(snapshot);
    if (S.undoStack.length > S.maxUndo) S.undoStack.shift();
    S.redoStack = [];
}

// onUndoRedo: callback for UI layer to re-render panels
// Set by canvas-ui.js via StudioCore.onUndoRedo = fn
let _onUndoRedo = null;
//: SR2-1. Called after an airbrush tick lays paint. A held pointer sends no
//: events, so nothing else asks the canvas to redraw: the owner's second V2
//: sign-off saw the build "only after releasing the click". The UI wires its
//: redraw here, the same way it wires `onUndoRedo`.
let _onAirbrushDeposit = null;

function _restoreStructural(entry) {
    if (entry.canvasW && entry.canvasH && (entry.canvasW !== S.W || entry.canvasH !== S.H)) {
        S.W = entry.canvasW; S.H = entry.canvasH;
        S.stroke.canvas.width = S.W; S.stroke.canvas.height = S.H;
        S.mask.canvas.width = S.W; S.mask.canvas.height = S.H;
        S.mask.ctx = S.mask.canvas.getContext("2d", { colorSpace: "srgb" });
    }
    if (entry.maskData) S.mask.ctx.putImageData(entry.maskData, 0, 0);
    S.layers = [];
    for (const ld of entry.layers) {
        if (ld.type === "adjustment") {
            const L = makeAdjustLayer(ld.name, ld.adjustType, JSON.parse(JSON.stringify(ld.adjustParams || {})));
            L.id = ld.id; L.visible = ld.visible; L.opacity = ld.opacity;
            L.blendMode = ld.blendMode; L.locked = ld.locked;
            L._lutCache = null;
            S.layers.push(L);
        } else {
            const c = createLayerCanvas(); const ctx = c.getContext("2d", { colorSpace: "srgb" });
            if (ld.data) ctx.putImageData(ld.data, 0, 0);
            S.layers.push({
                id: ld.id, name: ld.name, type: ld.type, canvas: c, ctx: ctx,
                visible: ld.visible, opacity: ld.opacity, blendMode: ld.blendMode, locked: ld.locked
            });
        }
    }
    S.nextLayerId = Math.max(...S.layers.map(l => l.id)) + 1;
    S.activeLayerIdx = entry.activeIdx;
    // History restores content; the selected tool remains the visible target.
    S.editingMask = S.tool === "mask";
    if (entry.userMaskMode !== undefined) S._userMaskMode = entry.userMaskMode;
}

function _captureStructural() {
    return {
        type: "structural",
        layers: S.layers.map(L => {
            if (L.type === "adjustment") {
                return {
                    id: L.id, name: L.name, type: L.type,
                    adjustType: L.adjustType,
                    adjustParams: JSON.parse(JSON.stringify(L.adjustParams || {})),
                    visible: L.visible, opacity: L.opacity,
                    blendMode: L.blendMode, locked: L.locked, data: null
                };
            }
            return {
                id: L.id, name: L.name, type: L.type,
                visible: L.visible, opacity: L.opacity,
                blendMode: L.blendMode, locked: L.locked,
                data: L.ctx.getImageData(0, 0, S.W, S.H)
            };
        }),
        activeIdx: S.activeLayerIdx, editingMask: S.editingMask, userMaskMode: !!S._userMaskMode,
        maskData: S.mask.ctx.getImageData(0, 0, S.W, S.H),
        canvasW: S.W, canvasH: S.H
    };
}

// ========================================================================
// STROKE TRANSACTION (CT2)
// ========================================================================
//
// CT2-R1. Preserve complete pre-gesture history, including an evicted oldest
// entry and redo. Revision numbers are invalidation tokens and never rewind:
// opening reserves a revision; cancellation invalidates that provisional state.
let _strokeTransaction = null;

// Region painting is Brush/Eraser with a region selected -- canvas-ui's region
// branch, as in the Extension. Every other tool edits the image in Regional
// mode too; routing them to the region made Smudge/Clone/Liquify paint the
// prompt map instead of the picture.
function _regionPaintTarget() {
    return S.regionMode && (S.tool === "brush" || S.tool === "eraser") ? activeRegion() : null;
}

/** Open BEFORE the first write. Owns saveUndo so callers cannot lose redo first. */
function noteStrokeUndo(label) {
    if (_strokeTransaction) abortStroke();
    const region = _regionPaintTarget();
    const target = region || drawTarget();
    if (!target || !target.ctx) return false;
    const transaction = {
        documentId: S.documentId, width: S.W, height: S.H,
        tool: S.tool, editingMask: S.editingMask, regionMode: S.regionMode,
        region: !!region, regionId: S.activeRegionId, layer: activeLayer(), target,
        undo: S.undoStack.slice(), redo: S.redoStack.slice(), dirty: S._canvasDirty,
        cloneOffset: S._cloneOffset ? {...S._cloneOffset} : null
    };
    S.drawing = true;
    saveUndo(label, region ? { region } : undefined);
    transaction.documentId = S.documentId;
    transaction.entry = S.undoStack[S.undoStack.length - 1];
    _strokeTransaction = transaction;
    S.stroke._pen = null; S.stroke._lastTime = null;
    S.stroke.points = [];
    return true;
}

function strokeTargetIsCurrent() {
    const t = _strokeTransaction;
    return !t || (t.documentId === S.documentId && t.width === S.W && t.height === S.H
        && t.tool === S.tool && t.editingMask === S.editingMask && t.regionMode === S.regionMode
        && (t.region ? t.regionId === S.activeRegionId
            && S.regions.some(r => r === t.target)
            : t.editingMask ? t.target.ctx === S.mask.ctx : t.layer === activeLayer()));
}

function _strokePixelsChanged(t) {
    const before = t.entry.data.data;
    const after = t.target.ctx.getImageData(0, 0, t.width, t.height).data;
    for (let i = 0; i < before.length; i++) if (before[i] !== after[i]) return true;
    return false;
}

function _restoreStrokeHistory(t) {
    S.undoStack = t.undo;
    S.redoStack = t.redo;
    S._canvasDirty = t.dirty;
}

function _clearStrokeBuffers() {
    stopAirbrush();
    S.drawing = false;
    S.stroke._openingDab = null;
    S.stroke._headingKnown = false;
    S.stroke.alphaMap = null;
    S.stroke.accum = null;
    S.stroke._accumFor = null;
    S.stroke.points = [];
    S.stroke.stampPoints = [];
    S.stroke._cachedImg = null;
    S.stroke._commitTarget = null;
    S.stroke._commitMask = null;
    S.stroke._pen = null; S.stroke._lastTime = null;
    S.stroke._ppPrev = null; S.stroke._ppPrev2 = null;
    S.stroke.dirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
    S.stroke.frameDirty = {...S.stroke.dirty};
    if (S.stroke.ctx) S.stroke.ctx.clearRect(0, 0, S.W, S.H);
    S.smudgeBuffer = null;
    S._smudgeSymBuffers = null;
    S._liquifySnapshot = null;
    S.stroke._liquifyDist = 0;
    if (S.stroke.pointerId != null && S.canvas) {
        try { S.canvas.releasePointerCapture(S.stroke.pointerId); } catch (_) {}
    }
    S.stroke.pointerId = null;
    const preview = window.StudioCanvasWebGLPreview;
    if (preview && preview.endLiveCanvasFallback) {
        try { preview.endLiveCanvasFallback(S.tool); } catch (_) {}
    }
}

/** Close after pixels are merged; unchanged gestures preserve history/dirty. */
function clearStrokeUndo() {
    const t = _strokeTransaction;
    if (!t) return false;
    const changed = _strokePixelsChanged(t);
    _strokeTransaction = null;
    if (!changed) _restoreStrokeHistory(t);
    if (changed && t.tool === "mask") S._userMaskMode = true;
    _clearStrokeBuffers();
    if (changed) _notifyActionComplete("stroke");
    return changed;
}

/** Restore the captured target, never resolve a different layer/document. */
function abortStroke() {
    stopAirbrush();
    const t = _strokeTransaction;
    _strokeTransaction = null;
    if (window.StudioBrushV2Adapter) window.StudioBrushV2Adapter.cancel();
    if (t) {
        t.target.ctx.putImageData(t.entry.data, 0, 0);
        // Transitions resolve before swapping documents. A foreign caller
        // that already replaced the document must not receive old history.
        if (t.documentId === S.documentId) {
            _restoreStrokeHistory(t);
            S._cloneOffset = t.cloneOffset;
        }
    }
    _clearStrokeBuffers();
    if (t && t.documentId === S.documentId) _bumpRevision();
    markCompositeDirty();
    composite();
    return !!t;
}

// A history key while a stroke is held cancels that stroke and nothing more.
// Carrying on after the abort also undid the stroke before it -- one Ctrl+Z,
// two strokes gone. The Extension never touched the earlier stroke either.
function undo() {
    if (_strokeTransaction) { abortStroke(); if (_onUndoRedo) _onUndoRedo(); return; }
    // A NEW revision, never an old one reused.
    _bumpRevision();
    if (!S.undoStack.length) return;
    if (S.transform.active) { S.transform.active = false; S.transform.canvas = null; S.transform.flipH = false; S.transform.flipV = false; }
    markCompositeDirty();
    const e = S.undoStack.pop();
    if (e.type === "structural") {
        S.redoStack.push(_captureStructural());
        _restoreStructural(e);
    } else if (e.type === "region") {
        const r = S.regions.find(rr => rr.id === e.regionId);
        if (r) {
            S.redoStack.push({ type: "region", regionId: e.regionId, data: r.ctx.getImageData(0, 0, S.W, S.H), label: e.label });
            r.ctx.putImageData(e.data, 0, 0);
        }
    } else {
        const ctx = _undoResolve(e.layerId);
        if (!ctx) return;
        S.redoStack.push({ type: "pixel", layerId: e.layerId, data: ctx.getImageData(0, 0, S.W, S.H), label: e.label, userMaskMode: e.layerId === "mask" ? !!S._userMaskMode : undefined });
        ctx.putImageData(e.data, 0, 0);
        if (e.layerId === "mask" && e.userMaskMode !== undefined) S._userMaskMode = e.userMaskMode;
    }
    if (_onUndoRedo) _onUndoRedo();
}

function redo() {
    if (_strokeTransaction) { abortStroke(); if (_onUndoRedo) _onUndoRedo(); return; }
    // A NEW revision, never an old one reused.
    _bumpRevision();
    if (!S.redoStack.length) return;
    markCompositeDirty();
    const e = S.redoStack.pop();
    if (e.type === "structural") {
        S.undoStack.push(_captureStructural());
        _restoreStructural(e);
    } else if (e.type === "region") {
        const r = S.regions.find(rr => rr.id === e.regionId);
        if (r) {
            S.undoStack.push({ type: "region", regionId: e.regionId, data: r.ctx.getImageData(0, 0, S.W, S.H), label: e.label });
            r.ctx.putImageData(e.data, 0, 0);
        }
    } else {
        const ctx = _undoResolve(e.layerId);
        if (!ctx) return;
        S.undoStack.push({ type: "pixel", layerId: e.layerId, data: ctx.getImageData(0, 0, S.W, S.H), label: e.label, userMaskMode: e.layerId === "mask" ? !!S._userMaskMode : undefined });
        ctx.putImageData(e.data, 0, 0);
        if (e.layerId === "mask" && e.userMaskMode !== undefined) S._userMaskMode = e.userMaskMode;
    }
    if (_onUndoRedo) _onUndoRedo();
}

// ========================================================================
// SELECTION ALGORITHMS
// ========================================================================

// Scanline polygon fill into mask buffer
function fillPolygonMask(pts, mask, w, h) {
    let yMin = h, yMax = 0;
    for (const p of pts) { if (p.y < yMin) yMin = p.y; if (p.y > yMax) yMax = p.y; }
    yMin = Math.max(0, Math.floor(yMin));
    yMax = Math.min(h - 1, Math.ceil(yMax));
    for (let y = yMin; y <= yMax; y++) {
        const intersections = [];
        for (let i = 0; i < pts.length; i++) {
            const j = (i + 1) % pts.length;
            const y0 = pts[i].y, y1 = pts[j].y;
            if ((y0 <= y && y1 > y) || (y1 <= y && y0 > y)) {
                const t = (y - y0) / (y1 - y0);
                intersections.push(pts[i].x + t * (pts[j].x - pts[i].x));
            }
        }
        intersections.sort((a, b) => a - b);
        for (let i = 0; i < intersections.length - 1; i += 2) {
            const xStart = Math.max(0, Math.ceil(intersections[i]));
            const xEnd = Math.min(w - 1, Math.floor(intersections[i + 1]));
            for (let x = xStart; x <= xEnd; x++) mask[y * w + x] = 255;
        }
    }
}

// Magic wand — flood-fill selection by color similarity
function magicWandSelect(pt) {
    const tolerance = S.toolStrength * 50;
    const tc = _createCanvas(S.W, S.H);
    const tctx = tc.getContext("2d", { colorSpace: "srgb" });
    for (const L of S.layers) {
        if (L.visible && L.canvas) { tctx.globalAlpha = L.opacity; tctx.drawImage(L.canvas, 0, 0); }
    }
    const img = tctx.getImageData(0, 0, S.W, S.H);
    const d = img.data, w = S.W, h = S.H;
    const sx = ~~pt.x, sy = ~~pt.y;
    if (sx < 0 || sx >= w || sy < 0 || sy >= h) return null;
    const idx = (sy * w + sx) * 4;
    const tR = d[idx], tG = d[idx + 1], tB = d[idx + 2], tA = d[idx + 3];
    const mask = new Uint8Array(w * h);
    const visited = new Uint8Array(w * h);
    const stack = [sx, sy];
    const tolSq = tolerance * tolerance * 3;
    while (stack.length) {
        const cy2 = stack.pop(), cx2 = stack.pop();
        if (cx2 < 0 || cx2 >= w || cy2 < 0 || cy2 >= h) continue;
        const ci = cy2 * w + cx2;
        if (visited[ci]) continue;
        visited[ci] = 1;
        const pi = ci * 4;
        const dr = d[pi] - tR, dg = d[pi + 1] - tG, db = d[pi + 2] - tB;
        if (dr * dr + dg * dg + db * db > tolSq) continue;
        if (Math.abs(d[pi + 3] - tA) > tolerance) continue;
        mask[ci] = 255;
        stack.push(cx2 + 1, cy2); stack.push(cx2 - 1, cy2);
        stack.push(cx2, cy2 + 1); stack.push(cx2, cy2 - 1);
    }
    // Compute bounding rect
    let x0 = w, y0 = h, x1 = 0, y1 = 0;
    let hasSelection = false;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (mask[y * w + x] > 0) {
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
            hasSelection = true;
        }
    }
    if (!hasSelection) return null;
    return { mask, rect: { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 } };
}

// Selection modification (add/subtract)
function selectionModify(newMask, mode) {
    if (mode === "add" && S.selection.mask) {
        for (let i = 0; i < newMask.length; i++) {
            if (newMask[i] > S.selection.mask[i]) S.selection.mask[i] = newMask[i];
        }
    } else if (mode === "subtract" && S.selection.mask) {
        for (let i = 0; i < S.selection.mask.length; i++) {
            if (newMask[i] > 0) S.selection.mask[i] = 0;
        }
    } else {
        S.selection.mask = newMask;
    }
    // Recalculate bounding rect
    const w = S.W, h = S.H, m = S.selection.mask;
    let x0 = w, y0 = h, x1 = 0, y1 = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (m[y * w + x] > 0) {
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
    }
    S.selection.rect = { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
    S.selection.active = true;
}

// Feather selection (3-pass box blur approximation of Gaussian)
function featherSelection(radius) {
    if (!S.selection.active || !S.selection.mask || !radius || radius < 1) return;
    const w = S.W, h = S.H;
    const src = S.selection.mask;
    const dst = new Uint8Array(w * h);
    const tmp = new Uint8Array(w * h);
    for (let pass = 0; pass < 3; pass++) {
        const input = pass === 0 ? src : (pass % 2 === 1 ? dst : tmp);
        const output = pass % 2 === 1 ? tmp : dst;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                let sum = 0, cnt = 0;
                for (let kx = -radius; kx <= radius; kx++) {
                    const sx2 = x + kx;
                    if (sx2 >= 0 && sx2 < w) { sum += input[y * w + sx2]; cnt++; }
                }
                output[y * w + x] = (sum / cnt) | 0;
            }
        }
        const buf = new Uint8Array(w * h);
        for (let x2 = 0; x2 < w; x2++) {
            for (let y2 = 0; y2 < h; y2++) {
                let sum = 0, cnt = 0;
                for (let ky = -radius; ky <= radius; ky++) {
                    const sy2 = y2 + ky;
                    if (sy2 >= 0 && sy2 < h) { sum += output[sy2 * w + x2]; cnt++; }
                }
                buf[y2 * w + x2] = (sum / cnt) | 0;
            }
        }
        if (pass < 2) { for (let i = 0; i < w * h; i++) dst[i] = buf[i]; }
        else { S.selection.mask = buf; }
    }
    S.selection._isMaskBased = true;
}

// Selection operations
function selectionFill() {
    if (!S.selection.active || !S.selection.mask) return;
    const T = drawTarget(), ctx = T.ctx;
    const rgb = hexRgb(drawColor());
    const img = ctx.getImageData(0, 0, S.W, S.H);
    const d = img.data, mask = S.selection.mask;
    const fA = Math.round(S.brushOpacity * 255);
    for (let i = 0; i < mask.length; i++) {
        if (mask[i] === 0) continue;
        const j = i * 4, blend = mask[i] / 255;
        d[j]     = Math.round(rgb.r * blend + d[j] * (1 - blend));
        d[j + 1] = Math.round(rgb.g * blend + d[j + 1] * (1 - blend));
        d[j + 2] = Math.round(rgb.b * blend + d[j + 2] * (1 - blend));
        d[j + 3] = Math.max(d[j + 3], Math.round(fA * blend));
    }
    ctx.putImageData(img, 0, 0);
}

function selectionDelete() {
    if (!S.selection.active || !S.selection.mask) return;
    const T = drawTarget(), ctx = T.ctx;
    const img = ctx.getImageData(0, 0, S.W, S.H);
    const d = img.data, mask = S.selection.mask;
    for (let i = 0; i < mask.length; i++) {
        if (mask[i] === 0) continue;
        d[i * 4 + 3] = Math.round(d[i * 4 + 3] * (1 - mask[i] / 255));
    }
    ctx.putImageData(img, 0, 0);
}

function selectionInvert() {
    if (!S.selection.active || !S.selection.mask) return;
    for (let i = 0; i < S.selection.mask.length; i++) S.selection.mask[i] = 255 - S.selection.mask[i];
    S.selection.rect = { x: 0, y: 0, w: S.W, h: S.H };
}

function selectionAll() {
    S.selection.rect = { x: 0, y: 0, w: S.W, h: S.H };
    S.selection.mask = new Uint8Array(S.W * S.H);
    S.selection.mask.fill(255);
    S.selection.active = true;
}

function selectionClear() {
    S.selection.active = false;
    S.selection.rect = null;
    S.selection.mask = null;
    S.selection.dragging = false;
    S.selection._isLasso = false;
    S.selection._isEllipse = false;
    S.selection._isMaskBased = false;
    S.selection._contour = null;
    S.selection.lassoPoints = null;
}

function selectionToMask() {
    if (!S.selection.active || !S.selection.mask) return;
    const ctx = S.mask.ctx;
    const img = ctx.getImageData(0, 0, S.W, S.H);
    const d = img.data, mask = S.selection.mask;
    for (let i = 0; i < mask.length; i++) {
        if (mask[i] === 0) continue;
        const j = i * 4;
        d[j] = 255; d[j + 1] = 0; d[j + 2] = 0;
        d[j + 3] = Math.max(d[j + 3], mask[i]);
    }
    ctx.putImageData(img, 0, 0);
    S.mask.visible = true;
}

// Clipboard
function selectionCopy() {
    if (!S.selection.active || !S.selection.mask) return;
    const T = drawTarget(), ctx = T.ctx;
    const img = ctx.getImageData(0, 0, S.W, S.H);
    const d = img.data, mask = S.selection.mask;
    const copy = new ImageData(S.W, S.H);
    const cd = copy.data;
    for (let i = 0; i < mask.length; i++) {
        const j = i * 4;
        if (mask[i] > 0) {
            cd[j] = d[j]; cd[j + 1] = d[j + 1]; cd[j + 2] = d[j + 2];
            cd[j + 3] = Math.round(d[j + 3] * mask[i] / 255);
        }
    }
    S.clipboard = { data: copy, rect: S.selection.rect ? { ...S.selection.rect } : { x: 0, y: 0, w: S.W, h: S.H } };
}

function selectionCut() {
    if (!S.selection.active || !S.selection.mask) return;
    selectionCopy();
    selectionDelete();
}

function selectionPaste() {
    if (!S.clipboard) return;
    const newL = makeLayer("Pasted", "paint");
    newL.ctx.putImageData(S.clipboard.data, 0, 0);
    S.layers.splice(S.activeLayerIdx + 1, 0, newL);
    S.activeLayerIdx = S.activeLayerIdx + 1;
    if (S.tool === "mask" && window.StudioUI) window.StudioUI.setTool(S._maskReturnTool || "brush");
    S.editingMask = S.tool === "mask";
    selectionClear();
}

// ========================================================================
// TRANSFORM DATA MODEL
// ========================================================================
const HANDLE_SIZE = 8;

function getLayerContentBounds(L) {
    const img = L.ctx.getImageData(0, 0, S.W, S.H);
    const d = img.data;
    let x0 = S.W, y0 = S.H, x1 = 0, y1 = 0, hasContent = false;
    for (let y = 0; y < S.H; y++) for (let x = 0; x < S.W; x++) {
        if (d[(y * S.W + x) * 4 + 3] > 0) {
            if (x < x0) x0 = x; if (x > x1) x1 = x;
            if (y < y0) y0 = y; if (y > y1) y1 = y;
            hasContent = true;
        }
    }
    return hasContent ? { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 } : null;
}

function transformHitTest(px, py) {
    if (!S.transform.active || !S.transform.bounds) return null;
    const b = S.transform.bounds;
    const hs = HANDLE_SIZE / S.zoom.scale;
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    const rot = S.transform.rotation || 0;
    const skX = S.transform.skewX || 0, skY = S.transform.skewY || 0;

    // Transform mouse point into local (un-rotated, un-skewed) space
    let lx = px - cx, ly = py - cy;
    // Inverse rotate
    const cosR = Math.cos(-rot), sinR = Math.sin(-rot);
    const rx = lx * cosR - ly * sinR;
    const ry = lx * sinR + ly * cosR;
    // Inverse skew: inverse of [1, skX; skY, 1] = [1, -skX; -skY, 1] / det
    const det = 1 - skX * skY;
    lx = (rx - skX * ry) / det;
    ly = (-skY * rx + ry) / det;

    // Rotation handle — above top edge in local space
    const rhY = -b.h / 2 - 25 / S.zoom.scale;
    if (Math.abs(lx) <= hs * 1.5 && Math.abs(ly - rhY) <= hs * 1.5) return "rotate";

    // Corner handles (in local space, centered)
    const corners = [
        { name: "nw", x: -b.w / 2, y: -b.h / 2 }, { name: "ne", x: b.w / 2, y: -b.h / 2 },
        { name: "sw", x: -b.w / 2, y: b.h / 2 },  { name: "se", x: b.w / 2, y: b.h / 2 }
    ];
    const edges = [
        { name: "n", x: 0, y: -b.h / 2 }, { name: "s", x: 0, y: b.h / 2 },
        { name: "w", x: -b.w / 2, y: 0 }, { name: "e", x: b.w / 2, y: 0 }
    ];
    for (const c of corners) { if (Math.abs(lx - c.x) <= hs && Math.abs(ly - c.y) <= hs) return c.name; }
    for (const e of edges) { if (Math.abs(lx - e.x) <= hs && Math.abs(ly - e.y) <= hs) return e.name; }
    if (lx >= -b.w / 2 && lx <= b.w / 2 && ly >= -b.h / 2 && ly <= b.h / 2) return "move";
    return null;
}

// ========================================================================
// GRID SUBDIVISION RENDERER
// Shared backbone for non-affine transforms (perspective, mesh, cage).
// Renders a source image through an arbitrary grid of control points
// using textured-triangle rendering via Canvas 2D clip+drawImage.
// ========================================================================

/**
 * Subdivide a 2D grid of {x,y} points via bilinear interpolation.
 * Each level doubles resolution: R×C points → (2R-1)×(2C-1) points.
 * Shared edges are computed once (no seams).
 */
function _subdivideGrid(grid, levels) {
    let g = grid;
    for (let lvl = 0; lvl < levels; lvl++) {
        const rows = g.length, cols = g[0].length;
        const nr = rows * 2 - 1, nc = cols * 2 - 1;
        const ng = new Array(nr);
        for (let i = 0; i < nr; i++) {
            ng[i] = new Array(nc);
            for (let j = 0; j < nc; j++) {
                const ei = i % 2 === 0, ej = j % 2 === 0;
                if (ei && ej) {
                    // Original control point
                    ng[i][j] = g[i >> 1][j >> 1];
                } else if (ei) {
                    // Horizontal edge midpoint
                    const a = g[i >> 1][(j - 1) >> 1], b = g[i >> 1][(j + 1) >> 1];
                    ng[i][j] = { x: (a.x + b.x) * 0.5, y: (a.y + b.y) * 0.5 };
                } else if (ej) {
                    // Vertical edge midpoint
                    const a = g[(i - 1) >> 1][j >> 1], b = g[(i + 1) >> 1][j >> 1];
                    ng[i][j] = { x: (a.x + b.x) * 0.5, y: (a.y + b.y) * 0.5 };
                } else {
                    // Cell center — average of 4 corners
                    const tl = g[(i - 1) >> 1][(j - 1) >> 1], tr = g[(i - 1) >> 1][(j + 1) >> 1];
                    const bl = g[(i + 1) >> 1][(j - 1) >> 1], br = g[(i + 1) >> 1][(j + 1) >> 1];
                    ng[i][j] = { x: (tl.x + tr.x + bl.x + br.x) * 0.25, y: (tl.y + tr.y + bl.y + br.y) * 0.25 };
                }
            }
        }
        g = ng;
    }
    return g;
}

/**
 * Render one textured triangle.
 * Maps source triangle (s0,s1,s2) in image pixel coords
 * to destination triangle (d0,d1,d2) in canvas coords.
 * Uses affine transform + clip — exact for planar patches.
 */
function _renderTriangle(ctx, src, s0, s1, s2, d0, d1, d2) {
    const du1 = s1.x - s0.x, du2 = s2.x - s0.x;
    const dv1 = s1.y - s0.y, dv2 = s2.y - s0.y;
    const det = du1 * dv2 - du2 * dv1;
    if (Math.abs(det) < 1e-10) return; // degenerate triangle

    const dx1 = d1.x - d0.x, dx2 = d2.x - d0.x;
    const dy1 = d1.y - d0.y, dy2 = d2.y - d0.y;

    // Affine coefficients: maps source pixel (u,v) → document pixel (x,y)
    const a = (dx1 * dv2 - dx2 * dv1) / det;
    const c = (du1 * dx2 - du2 * dx1) / det;
    const e = d0.x - a * s0.x - c * s0.y;
    const b = (dy1 * dv2 - dy2 * dv1) / det;
    const d = (du1 * dy2 - du2 * dy1) / det;
    const f = d0.y - b * s0.x - d * s0.y;

    ctx.save();
    // Clip path in document space (current transform maps to screen)
    ctx.beginPath();
    ctx.moveTo(d0.x, d0.y);
    ctx.lineTo(d1.x, d1.y);
    ctx.lineTo(d2.x, d2.y);
    ctx.closePath();
    ctx.clip();
    // Compose source→document affine with existing context transform (zoom)
    // Result: source pixel → document coord → screen coord
    ctx.transform(a, b, c, d, e, f);
    ctx.drawImage(src, 0, 0);
    ctx.restore();
}

/**
 * Render a source image through a grid of control points.
 *
 * @param {CanvasRenderingContext2D} ctx  - destination context
 * @param {HTMLCanvasElement|Image} srcCanvas - source image
 * @param {{x,y,w,h}} srcRect - region of source to map through grid
 * @param {{x,y}[][]} gridPts - 2D array [row][col] of destination points.
 *        Minimum 2×2. gridPts[0][0] = where srcRect top-left lands.
 * @param {number} [subdivisions=3] - subdivision levels (each level 4× triangles)
 * @param {number} [opacity=1] - global alpha for the rendered output
 */
function gridRender(ctx, srcCanvas, srcRect, gridPts, subdivisions, opacity) {
    if (!gridPts || gridPts.length < 2 || gridPts[0].length < 2) return;
    const grid = _subdivideGrid(gridPts, subdivisions ?? 3);
    const rows = grid.length - 1;
    const cols = grid[0].length - 1;

    const prevAlpha = ctx.globalAlpha;
    if (opacity !== undefined && opacity !== 1) ctx.globalAlpha = opacity;

    for (let i = 0; i < rows; i++) {
        for (let j = 0; j < cols; j++) {
            // Source UVs — uniform subdivision of srcRect
            const u0 = srcRect.x + (j / cols) * srcRect.w;
            const v0 = srcRect.y + (i / rows) * srcRect.h;
            const u1 = srcRect.x + ((j + 1) / cols) * srcRect.w;
            const v1 = srcRect.y + ((i + 1) / rows) * srcRect.h;

            const s_tl = { x: u0, y: v0 }, s_tr = { x: u1, y: v0 };
            const s_bl = { x: u0, y: v1 }, s_br = { x: u1, y: v1 };

            const d_tl = grid[i][j],     d_tr = grid[i][j + 1];
            const d_bl = grid[i + 1][j], d_br = grid[i + 1][j + 1];

            // Two triangles per quad (consistent diagonal: TL→BR)
            _renderTriangle(ctx, srcCanvas, s_tl, s_tr, s_br, d_tl, d_tr, d_br);
            _renderTriangle(ctx, srcCanvas, s_tl, s_br, s_bl, d_tl, d_br, d_bl);
        }
    }

    ctx.globalAlpha = prevAlpha;
}

/**
 * Draw grid wireframe — for debug/UI overlay during non-affine transforms.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {{x,y}[][]} gridPts - control grid (pre-subdivision)
 * @param {number} [subdivisions=0] - subdivide before drawing (0 = control grid only)
 * @param {string} [color="#4af"] - stroke color
 * @param {number} [lineWidth=1] - stroke width
 */
function gridDrawWireframe(ctx, gridPts, subdivisions, color, lineWidth) {
    if (!gridPts || gridPts.length < 2 || gridPts[0].length < 2) return;
    const grid = subdivisions ? _subdivideGrid(gridPts, subdivisions) : gridPts;
    const rows = grid.length, cols = grid[0].length;

    ctx.save();
    ctx.strokeStyle = color || "#4af";
    ctx.lineWidth = lineWidth || 1;
    ctx.setLineDash([]);

    // Horizontal lines
    for (let i = 0; i < rows; i++) {
        ctx.beginPath();
        ctx.moveTo(grid[i][0].x, grid[i][0].y);
        for (let j = 1; j < cols; j++) ctx.lineTo(grid[i][j].x, grid[i][j].y);
        ctx.stroke();
    }
    // Vertical lines
    for (let j = 0; j < cols; j++) {
        ctx.beginPath();
        ctx.moveTo(grid[0][j].x, grid[0][j].y);
        for (let i = 1; i < rows; i++) ctx.lineTo(grid[i][j].x, grid[i][j].y);
        ctx.stroke();
    }
    ctx.restore();
}

/**
 * Build initial grid from a bounding rect.
 * Returns a (rows+1) × (cols+1) array of {x,y} points.
 * Default 2×2 (4 corners) — suitable for perspective.
 */
function gridFromRect(rect, rows, cols) {
    rows = rows || 1;
    cols = cols || 1;
    const pts = [];
    for (let i = 0; i <= rows; i++) {
        pts[i] = [];
        for (let j = 0; j <= cols; j++) {
            pts[i][j] = {
                x: rect.x + (j / cols) * rect.w,
                y: rect.y + (i / rows) * rect.h
            };
        }
    }
    return pts;
}

/**
 * Hit-test a grid's control points. Returns {row, col} or null.
 * Tests in un-zoomed document space.
 */
function gridHitTest(gridPts, px, py, tolerance) {
    tolerance = tolerance || 8;
    for (let i = 0; i < gridPts.length; i++) {
        for (let j = 0; j < gridPts[i].length; j++) {
            const pt = gridPts[i][j];
            if (Math.abs(px - pt.x) <= tolerance && Math.abs(py - pt.y) <= tolerance) {
                return { row: i, col: j };
            }
        }
    }
    return null;
}

// ========================================================================
// MAGNETIC LASSO
// Edge-snapping selection via gradient-based shortest path.
// Computes Sobel edge map, then Dijkstra between anchor points.
// ========================================================================

/**
 * Compute gradient magnitude map from a canvas context.
 * Returns Float32Array (W×H) with values 0-1.
 */
function _computeEdgeMap(ctx, w, h) {
    const img = ctx.getImageData(0, 0, w, h).data;
    const gray = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) {
        gray[i] = (img[i*4] * 0.299 + img[i*4+1] * 0.587 + img[i*4+2] * 0.114) / 255;
    }
    const grad = new Float32Array(w * h);
    let maxG = 0;
    for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
            // Sobel 3x3
            const gx = -gray[(y-1)*w+x-1] + gray[(y-1)*w+x+1]
                      -2*gray[y*w+x-1]    + 2*gray[y*w+x+1]
                      -gray[(y+1)*w+x-1]  + gray[(y+1)*w+x+1];
            const gy = -gray[(y-1)*w+x-1] - 2*gray[(y-1)*w+x] - gray[(y-1)*w+x+1]
                      +gray[(y+1)*w+x-1]  + 2*gray[(y+1)*w+x] + gray[(y+1)*w+x+1];
            const g = Math.sqrt(gx * gx + gy * gy);
            grad[y * w + x] = g;
            if (g > maxG) maxG = g;
        }
    }
    // Normalize
    if (maxG > 0) for (let i = 0; i < grad.length; i++) grad[i] /= maxG;
    return grad;
}

/**
 * Build composite edge map from all visible layers.
 */
function magneticEdgeMap() {
    const tmp = _createCanvas(S.W, S.H);
    const tc = tmp.getContext("2d", { colorSpace: "srgb" });
    for (const L of S.layers) {
        if (!L.visible || !L.canvas) continue;
        tc.globalAlpha = L.opacity ?? 1;
        tc.drawImage(L.canvas, 0, 0);
    }
    tc.globalAlpha = 1;
    return _computeEdgeMap(tc, S.W, S.H);
}

/**
 * Find shortest path between two points on the edge map.
 * Cost = 1 - gradient (low gradient = high cost = paths avoid flat areas).
 * Uses Dijkstra with 8-connected neighbors, bounded to a search region.
 *
 * @param {Float32Array} edgeMap - gradient magnitude (0-1), W×H
 * @param {number} w - image width
 * @param {number} h - image height
 * @param {number} x0 - start x
 * @param {number} y0 - start y
 * @param {number} x1 - end x
 * @param {number} y1 - end y
 * @param {number} [margin=40] - search region padding around bounding box
 * @returns {{x,y}[]} path from start to end
 */
function magneticPath(edgeMap, w, h, x0, y0, x1, y1, margin) {
    x0 = ~~x0; y0 = ~~y0; x1 = ~~x1; y1 = ~~y1;
    margin = margin || 40;

    // Bounded search region
    const bx0 = Math.max(0, Math.min(x0, x1) - margin);
    const by0 = Math.max(0, Math.min(y0, y1) - margin);
    const bx1 = Math.min(w - 1, Math.max(x0, x1) + margin);
    const by1 = Math.min(h - 1, Math.max(y0, y1) + margin);
    const bw = bx1 - bx0 + 1, bh = by1 - by0 + 1;

    const INF = 1e9;
    const dist = new Float32Array(bw * bh).fill(INF);
    const prev = new Int32Array(bw * bh).fill(-1);
    const visited = new Uint8Array(bw * bh);

    // Local coords
    const lx0 = x0 - bx0, ly0 = y0 - by0;
    const lx1 = x1 - bx0, ly1 = y1 - by0;
    dist[ly0 * bw + lx0] = 0;

    // Simple priority queue via sorted array (fast enough for bounded regions)
    // For regions up to ~100x100 = 10K pixels this is fine
    const queue = [[0, lx0, ly0]];

    const dx8 = [-1, 0, 1, -1, 1, -1, 0, 1];
    const dy8 = [-1, -1, -1, 0, 0, 1, 1, 1];
    const dc8 = [1.414, 1, 1.414, 1, 1, 1.414, 1, 1.414]; // diagonal costs

    while (queue.length > 0) {
        // Pop min
        let minIdx = 0;
        for (let i = 1; i < queue.length; i++) {
            if (queue[i][0] < queue[minIdx][0]) minIdx = i;
        }
        const [d, cx, cy] = queue[minIdx];
        queue[minIdx] = queue[queue.length - 1];
        queue.pop();

        const ci = cy * bw + cx;
        if (visited[ci]) continue;
        visited[ci] = 1;

        if (cx === lx1 && cy === ly1) break;

        for (let k = 0; k < 8; k++) {
            const nx = cx + dx8[k], ny = cy + dy8[k];
            if (nx < 0 || nx >= bw || ny < 0 || ny >= bh) continue;
            const ni = ny * bw + nx;
            if (visited[ni]) continue;

            // Cost: inverse of edge strength. Strong edges = cheap to traverse
            const gx = nx + bx0, gy = ny + by0;
            const edgeVal = edgeMap[gy * w + gx];
            const cost = (1 - edgeVal * 0.9) * dc8[k]; // keep 0.1 min cost
            const nd = d + cost;

            if (nd < dist[ni]) {
                dist[ni] = nd;
                prev[ni] = ci;
                queue.push([nd, nx, ny]);
            }
        }
    }

    // Backtrace
    const path = [];
    let ci = ly1 * bw + lx1;
    if (dist[ci] >= INF) {
        // No path found — straight line fallback
        path.push({ x: x1, y: y1 });
        path.push({ x: x0, y: y0 });
        return path;
    }
    while (ci !== -1) {
        const lx = ci % bw, ly = (ci / bw) | 0;
        path.push({ x: lx + bx0, y: ly + by0 });
        ci = prev[ci];
    }
    return path; // reversed (end→start), caller can reverse if needed
}

// ========================================================================
// MLS (Moving Least Squares) WARP
// Implements Schaefer et al. 2006 — three deformation modes.
// Affine: general, allows shear. Similitude: preserves angles.
// Rigid: preserves local shape and area.
// ========================================================================

/**
 * Warp a single point via MLS interpolation.
 * @param {{x,y}[]} origPts - original control point positions (flat array)
 * @param {{x,y}[]} curPts  - current (dragged) positions (flat array)
 * @param {{x,y}} v         - point to warp
 * @param {string} mode     - "affine", "similitude", or "rigid"
 * @returns {{x,y}}
 */
function _mlsWarp(origPts, curPts, v, mode) {
    const n = origPts.length;
    if (n === 0) return { x: v.x, y: v.y };

    // Weights: w_i = 1 / |p_i - v|²
    const w = new Array(n);
    let wSum = 0;
    for (let i = 0; i < n; i++) {
        const dx = origPts[i].x - v.x, dy = origPts[i].y - v.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 1e-10) return { x: curPts[i].x, y: curPts[i].y };
        w[i] = 1 / d2;
        wSum += w[i];
    }

    // Weighted centroids p*, q*
    let psx = 0, psy = 0, qsx = 0, qsy = 0;
    for (let i = 0; i < n; i++) {
        psx += w[i] * origPts[i].x; psy += w[i] * origPts[i].y;
        qsx += w[i] * curPts[i].x;  qsy += w[i] * curPts[i].y;
    }
    psx /= wSum; psy /= wSum;
    qsx /= wSum; qsy /= wSum;

    const vx = v.x - psx, vy = v.y - psy;

    if (mode === "affine") {
        // M = (Σ w_i p̂ᵀp̂)⁻¹ · (Σ w_i p̂ᵀq̂)
        let a = 0, b = 0, c = 0;
        let d = 0, e = 0, f = 0, g = 0;
        for (let i = 0; i < n; i++) {
            const phx = origPts[i].x - psx, phy = origPts[i].y - psy;
            const qhx = curPts[i].x - qsx, qhy = curPts[i].y - qsy;
            a += w[i] * phx * phx; b += w[i] * phx * phy; c += w[i] * phy * phy;
            d += w[i] * phx * qhx; e += w[i] * phx * qhy;
            f += w[i] * phy * qhx; g += w[i] * phy * qhy;
        }
        const det = a * c - b * b;
        if (Math.abs(det) < 1e-10) return { x: v.x + qsx - psx, y: v.y + qsy - psy };
        const m00 = (c * d - b * f) / det, m01 = (c * e - b * g) / det;
        const m10 = (-b * d + a * f) / det, m11 = (-b * e + a * g) / det;
        return { x: vx * m00 + vy * m10 + qsx, y: vx * m01 + vy * m11 + qsy };
    }

    // Similitude & Rigid share the conformal accumulation
    let mu = 0;
    for (let i = 0; i < n; i++) {
        const phx = origPts[i].x - psx, phy = origPts[i].y - psy;
        mu += w[i] * (phx * phx + phy * phy);
    }
    if (mu < 1e-10) return { x: v.x + qsx - psx, y: v.y + qsy - psy };

    let fx = 0, fy = 0;
    for (let i = 0; i < n; i++) {
        const phx = origPts[i].x - psx, phy = origPts[i].y - psy;
        const qhx = curPts[i].x - qsx, qhy = curPts[i].y - qsy;
        const dot = phx * vx + phy * vy;
        const cross = phx * vy - phy * vx;
        fx += w[i] * (qhx * dot - qhy * cross);
        fy += w[i] * (qhy * dot + qhx * cross);
    }
    fx /= mu; fy /= mu;

    if (mode === "rigid") {
        // Normalize to preserve distance from centroid
        const vlen = Math.sqrt(vx * vx + vy * vy);
        const flen = Math.sqrt(fx * fx + fy * fy);
        if (flen > 1e-10) { fx = fx * vlen / flen; fy = fy * vlen / flen; }
    }

    return { x: fx + qsx, y: fy + qsy };
}

/**
 * Evaluate MLS warp across a regular grid.
 * Takes N×N control grids (original + current), produces a dense
 * evaluation grid suitable for gridRender().
 *
 * @param {{x,y}[][]} origCtrl - original control grid positions
 * @param {{x,y}[][]} curCtrl  - current (dragged) control positions
 * @param {{x,y,w,h}} srcRect  - source bounds (for eval grid spacing)
 * @param {number} evalSize     - eval grid dimension (e.g. 12 → 13×13 points)
 * @param {string} mode         - "affine", "similitude", or "rigid"
 * @returns {{x,y}[][]}         - evaluation grid for gridRender
 */
function mlsEvalGrid(origCtrl, curCtrl, srcRect, evalSize, mode) {
    // Flatten control grids
    const origFlat = [], curFlat = [];
    for (const row of origCtrl) for (const pt of row) origFlat.push(pt);
    for (const row of curCtrl) for (const pt of row) curFlat.push(pt);

    const grid = [];
    for (let i = 0; i <= evalSize; i++) {
        grid[i] = [];
        for (let j = 0; j <= evalSize; j++) {
            const v = {
                x: srcRect.x + (j / evalSize) * srcRect.w,
                y: srcRect.y + (i / evalSize) * srcRect.h
            };
            grid[i][j] = _mlsWarp(origFlat, curFlat, v, mode);
        }
    }
    return grid;
}

// ========================================================================
// REGIONS
// ========================================================================
function addRegion(name) {
    if (_strokeTransaction) abortStroke();
    const id = S._nextRegionId++;
    const colorIdx = (id - 1) % REGION_COLORS.length;
    const c = _createCanvas(S.W, S.H);
    const region = {
        id, name: name || ("Region " + id),
        prompt: "", negPrompt: "", denoising: 0.55, weight: 1.0,
        color: REGION_COLORS[colorIdx],
        canvas: c, ctx: c.getContext("2d", { colorSpace: "srgb" }), visible: true
    };
    S.regions.push(region);
    S.activeRegionId = id;
    S.regionMode = true;
    return region;
}

function deleteRegion(id) {
    if (_strokeTransaction) abortStroke();
    S.regions = S.regions.filter(r => r.id !== id);
    if (S.activeRegionId === id) S.activeRegionId = S.regions.length ? S.regions[S.regions.length - 1].id : null;
    if (!S.regions.length) S.regionMode = false;
}

function clearRegion(id) {
    const r = S.regions.find(r => r.id === id);
    if (r) r.ctx.clearRect(0, 0, S.W, S.H);
}

function activeRegion() { return S.regions.find(r => r.id === S.activeRegionId) || null; }

function serializeRegions() {
    if (!S.regions.length) return "";
    return JSON.stringify({
        regions: S.regions.map(r => ({
            name: r.name, prompt: r.prompt, negPrompt: r.negPrompt,
            denoising: r.denoising,
            weight: r.weight,
            color: r.color,
            mask_b64: r.canvas.toDataURL("image/png")
        }))
    });
}

function regionPaintAt(x, y) {
    const r = activeRegion();
    if (!r) return;
    r.ctx.fillStyle = "#fff";
    r.ctx.beginPath(); r.ctx.arc(x, y, Math.max(1, brushPx() / 2), 0, Math.PI * 2); r.ctx.fill();
}

function regionPaintMove(x1, y1, x2, y2) {
    const r = activeRegion();
    if (!r) return;
    const rad = Math.max(1, brushPx() / 2);
    const steps = Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / (rad * 0.4)));
    for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        r.ctx.fillStyle = "#fff";
        r.ctx.beginPath();
        r.ctx.arc(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, rad, 0, Math.PI * 2);
        r.ctx.fill();
    }
}

function regionEraseMove(x1, y1, x2, y2) {
    const r = activeRegion();
    if (!r) return;
    const rad = Math.max(1, brushPx() / 2);
    const steps = Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / (rad * 0.4)));
    for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        r.ctx.save(); r.ctx.globalCompositeOperation = "destination-out";
        r.ctx.fillStyle = "#fff";
        r.ctx.beginPath();
        r.ctx.arc(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, rad, 0, Math.PI * 2);
        r.ctx.fill(); r.ctx.restore();
    }
}

// ========================================================================
// ADJUSTMENT LAYER RENDERING
// ========================================================================
// Per-pixel transforms — replace the legacy ctx.filter pipeline. Algorithm
// references follow Krita's filter implementations (KisFilter, GPL-3.0,
// compatible with this project's license). The functions mutate `ctx` in
// place and are opacity-agnostic; _applyAdjustment handles `L.opacity` by
// snapshot-and-lerp around the dispatch.

const _LUT_IDENTITY_BC = new Uint8Array(256);
for (let i = 0; i < 256; i++) _LUT_IDENTITY_BC[i] = i;

function _isBrightnessIdentity(ap) {
    return (ap.brightness | 0) === 0 && (ap.contrast | 0) === 0;
}
function _isHSLIdentity(ap) {
    if (ap.colorize) return false;
    return (ap.hue | 0) === 0 && (ap.saturation | 0) === 0 && (ap.lightness | 0) === 0;
}
function _isLevelsIdentity(ap) {
    return (ap.levInBlack || 0) === 0
        && (ap.levInWhite !== undefined ? ap.levInWhite : 1) === 1
        && (ap.levGamma !== undefined ? ap.levGamma : 1) === 1
        && (ap.levOutBlack || 0) === 0
        && (ap.levOutWhite !== undefined ? ap.levOutWhite : 1) === 1;
}

function _getCachedLut(L, type, sig, build) {
    const cache = L._lutCache;
    if (cache && cache.type === type && cache.sig === sig) return cache.lut;
    const lut = build();
    L._lutCache = { type, sig, lut };
    return lut;
}

// Brightness offset + Krita-style sigmoid contrast centered at 0.5.
// contrast slope = tan((c+1) * π/4): c=0 → 1 (identity), c=1 → ∞ (binarize), c=-1 → 0 (flat).
function _buildBrightnessContrastLut(ap) {
    const b = (ap.brightness || 0) / 100;
    const c = Math.max(-1, Math.min(1, (ap.contrast || 0) / 100));
    const slope = Math.tan((c + 1) * Math.PI / 4);
    const lut = new Uint8Array(256);
    for (let v = 0; v < 256; v++) {
        let t = v / 255;
        t = (t - 0.5) * slope + 0.5 + b;
        if (t < 0) t = 0; else if (t > 1) t = 1;
        lut[v] = (t * 255 + 0.5) | 0;
    }
    return lut;
}

function _applyBrightnessContrastToCtx(ctx, w, h, ap, L) {
    const sig = (ap.brightness | 0) + ":" + (ap.contrast | 0);
    const lut = _getCachedLut(L, "brightness", sig, () => _buildBrightnessContrastLut(ap));
    const imgData = ctx.getImageData(0, 0, w, h);
    const d = imgData.data;
    for (let p = 0, len = d.length; p < len; p += 4) {
        d[p] = lut[d[p]]; d[p + 1] = lut[d[p + 1]]; d[p + 2] = lut[d[p + 2]];
    }
    ctx.putImageData(imgData, 0, 0);
}

// HSL/HSV adjustment. Relative shifts use Krita's "linear toward edge" rule
// for saturation/lightness so extremes don't clip prematurely. `colorize`
// replaces hue and saturation with absolute targets.
function _applyHSLToCtx(ctx, w, h, ap) {
    const hShift   = ap.hue || 0;
    const sShift   = (ap.saturation || 0) / 100;
    const lShift   = (ap.lightness  || 0) / 100;
    const useHSV   = ap.model === "HSV";
    const colorize = !!ap.colorize;
    const cHue = ((ap.hue || 0) % 360 + 360) % 360;
    const cSat = useHSV ? Math.max(0, Math.min(1, (ap.saturation || 0) / 100))
                        : Math.max(0, Math.min(1, ((ap.saturation || 0) + 100) / 200));

    const imgData = ctx.getImageData(0, 0, w, h);
    const d = imgData.data;
    for (let p = 0, len = d.length; p < len; p += 4) {
        if (d[p + 3] === 0) continue;
        const r = d[p] / 255, g = d[p + 1] / 255, b = d[p + 2] / 255;
        const max = r > g ? (r > b ? r : b) : (g > b ? g : b);
        const min = r < g ? (r < b ? r : b) : (g < b ? g : b);
        const delta = max - min;

        let H = 0;
        if (delta > 0) {
            if (max === r)      H = ((g - b) / delta) % 6;
            else if (max === g) H = (b - r) / delta + 2;
            else                H = (r - g) / delta + 4;
            H *= 60; if (H < 0) H += 360;
        }

        let S, axis;
        if (useHSV) {
            axis = max;                                   // V
            S = max === 0 ? 0 : delta / max;
        } else {
            axis = (max + min) * 0.5;                      // L
            S = delta === 0 ? 0 : delta / (1 - Math.abs(2 * axis - 1));
        }

        if (colorize) {
            H = cHue;
            S = cSat;
            axis = Math.max(0, Math.min(1, axis + lShift));
        } else {
            H = (H + hShift) % 360; if (H < 0) H += 360;
            S = sShift >= 0 ? S + (1 - S) * sShift : S + S * sShift;
            if (S < 0) S = 0; else if (S > 1) S = 1;
            axis = lShift >= 0 ? axis + (1 - axis) * lShift : axis + axis * lShift;
            if (axis < 0) axis = 0; else if (axis > 1) axis = 1;
        }

        let C, m;
        if (useHSV) { C = axis * S; m = axis - C; }
        else        { C = (1 - Math.abs(2 * axis - 1)) * S; m = axis - C * 0.5; }
        const X = C * (1 - Math.abs((H / 60) % 2 - 1));
        let r2, g2, b2;
        const seg = (H / 60) | 0;
        if      (seg === 0) { r2 = C; g2 = X; b2 = 0; }
        else if (seg === 1) { r2 = X; g2 = C; b2 = 0; }
        else if (seg === 2) { r2 = 0; g2 = C; b2 = X; }
        else if (seg === 3) { r2 = 0; g2 = X; b2 = C; }
        else if (seg === 4) { r2 = X; g2 = 0; b2 = C; }
        else                { r2 = C; g2 = 0; b2 = X; }
        d[p]     = ((r2 + m) * 255 + 0.5) | 0;
        d[p + 1] = ((g2 + m) * 255 + 0.5) | 0;
        d[p + 2] = ((b2 + m) * 255 + 0.5) | 0;
    }
    ctx.putImageData(imgData, 0, 0);
}

// Levels: input black/white normalize → gamma → output black/white remap.
function _buildLevelsLut(ap) {
    const iBlk = ap.levInBlack || 0;
    const iWht = ap.levInWhite !== undefined ? ap.levInWhite : 1;
    const gamma = ap.levGamma !== undefined ? ap.levGamma : 1;
    const oBlk = ap.levOutBlack || 0;
    const oWht = ap.levOutWhite !== undefined ? ap.levOutWhite : 1;
    const iRange = Math.max(0.001, iWht - iBlk);
    const oRange = oWht - oBlk;
    const invGamma = 1 / Math.max(0.01, gamma);
    const lut = new Uint8Array(256);
    for (let v = 0; v < 256; v++) {
        let t = (v / 255 - iBlk) / iRange;
        if (t < 0) t = 0; else if (t > 1) t = 1;
        t = Math.pow(t, invGamma);
        let o = oBlk + t * oRange;
        if (o < 0) o = 0; else if (o > 1) o = 1;
        lut[v] = (o * 255 + 0.5) | 0;
    }
    return lut;
}

function _applyLevelsToCtx(ctx, w, h, ap, L) {
    const sig = (ap.levInBlack || 0) + "|" + (ap.levInWhite || 1) + "|"
              + (ap.levGamma || 1) + "|" + (ap.levOutBlack || 0) + "|" + (ap.levOutWhite || 1);
    const lut = _getCachedLut(L, "levels", sig, () => _buildLevelsLut(ap));
    const imgData = ctx.getImageData(0, 0, w, h);
    const d = imgData.data;
    for (let p = 0, len = d.length; p < len; p += 4) {
        d[p] = lut[d[p]]; d[p + 1] = lut[d[p + 1]]; d[p + 2] = lut[d[p + 2]];
    }
    ctx.putImageData(imgData, 0, 0);
}

// ========================================================================
// DEVELOP — global non-destructive post-processing pass.
// The pipeline lives in develop.js (window.StudioDevelop). The compositor
// invokes this hook on the document-resolution buffer, *after* the layer
// stack composite, *before* UI overlays (grid, regions, marching ants).
// ctx is at S.W × S.H pixel coords (no zoom transform applied here).
// ========================================================================
function _applyDevelop(ctx, w, h, params) {
    if (!params || !params.enabled) return;
    const SD = window.StudioDevelop;
    if (!SD || typeof SD.applyToContext !== "function") return;
    try { SD.applyToContext(ctx, w, h, params); }
    catch (e) { console.error("[Develop] pipeline error:", e); }
}

function _applyAdjustment(ctx, w, h, L) {
    const ap = L.adjustParams || {};
    let identity = false;
    if      (L.adjustType === "brightness") identity = _isBrightnessIdentity(ap);
    else if (L.adjustType === "hue")        identity = _isHSLIdentity(ap);
    else if (L.adjustType === "levels")     identity = _isLevelsIdentity(ap);
    else return;
    if (identity) return;

    const opacity = L.opacity != null ? L.opacity : 1;
    let snap = null;
    if (opacity < 1) snap = ctx.getImageData(0, 0, w, h);

    if      (L.adjustType === "brightness") _applyBrightnessContrastToCtx(ctx, w, h, ap, L);
    else if (L.adjustType === "hue")        _applyHSLToCtx(ctx, w, h, ap);
    else if (L.adjustType === "levels")     _applyLevelsToCtx(ctx, w, h, ap, L);

    if (snap) {
        const out = ctx.getImageData(0, 0, w, h);
        const od = out.data, sd = snap.data;
        const a = opacity, ia = 1 - opacity;
        for (let p = 0, len = od.length; p < len; p += 4) {
            od[p]     = (od[p]     * a + sd[p]     * ia + 0.5) | 0;
            od[p + 1] = (od[p + 1] * a + sd[p + 1] * ia + 0.5) | 0;
            od[p + 2] = (od[p + 2] * a + sd[p + 2] * ia + 0.5) | 0;
        }
        ctx.putImageData(out, 0, 0);
    }
}

// ========================================================================
// COMPOSITOR
// ========================================================================
function checker(ctx, w, h) {
    const s = 10;
    ctx.fillStyle = "#3a3a3a"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#444";
    for (let y = 0; y < h; y += s) for (let x = 0; x < w; x += s) {
        if ((~~(x / s) + ~~(y / s)) & 1) ctx.fillRect(x, y, s, s);
    }
}

function _drawGrid(c, w, h, z) {
    if (!S.showGrid) return;
    const gStep = 64;
    const lw = 1 / z.scale;
    c.save();
    c.globalCompositeOperation = "difference";
    c.strokeStyle = "rgba(255,255,255,0.18)";
    c.lineWidth = lw;
    c.beginPath();
    for (let gx = gStep; gx < w; gx += gStep) {
        c.moveTo(gx, 0); c.lineTo(gx, h);
    }
    for (let gy = gStep; gy < h; gy += gStep) {
        c.moveTo(0, gy); c.lineTo(w, gy);
    }
    c.stroke();
    c.restore();
}

function symGuides(c) {
    c.save();
    c.strokeStyle = "rgba(100,200,255,0.3)";
    c.lineWidth = 1 / S.zoom.scale;
    c.setLineDash([4 / S.zoom.scale, 4 / S.zoom.scale]);
    if (S.symmetry === "h" || S.symmetry === "both") {
        c.beginPath(); c.moveTo(S.W / 2, 0); c.lineTo(S.W / 2, S.H); c.stroke();
    }
    if (S.symmetry === "v" || S.symmetry === "both") {
        c.beginPath(); c.moveTo(0, S.H / 2); c.lineTo(S.W, S.H / 2); c.stroke();
    }
    if (S.symmetry === "radial") {
        const n = S.symmetryAxes || 4;
        const ccx = S.W / 2, ccy = S.H / 2;
        const r = Math.max(S.W, S.H);
        for (let k = 0; k < n; k++) {
            const a = (2 * Math.PI * k) / n;
            c.beginPath();
            c.moveTo(ccx, ccy);
            c.lineTo(ccx + Math.cos(a) * r, ccy + Math.sin(a) * r);
            c.stroke();
        }
    }
    c.restore();
}

// Composite all layers below `idx` (exclusive) into a fresh canvas. Used by the
// adjustment-layer UI to source pixels for the histogram. Adjustment layers
// below `idx` are honored so the histogram reflects what the layer would see.
function _compositeLayersBelow(idx) {
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.filter = "none"; x.globalAlpha = 1; x.globalCompositeOperation = "source-over";
    const stop = Math.min(idx, S.layers.length);
    for (let i = 0; i < stop; i++) {
        const L = S.layers[i];
        if (!L.visible) continue;
        if (L.type === "adjustment") { _applyAdjustment(x, S.W, S.H, L); continue; }
        x.globalCompositeOperation = L.blendMode || "source-over";
        x.globalAlpha = L.opacity;
        x.drawImage(L.canvas, 0, 0);
    }
    x.globalCompositeOperation = "source-over"; x.globalAlpha = 1;
    return c;
}

/**
 * E0. The active layer as it would look if the in-progress erase were committed
 * right now, drawn at the active layer's own place in the stack.
 *
 * WHAT THIS REPLACES. `_composite2D` used to substitute `S.stroke.canvas` for
 * the active layer during an eraser stroke. That was correct when the eraser
 * PRE-FILLED the stroke canvas with a copy of the layer and erased from the
 * copy. `ade5fde7` ("CT3d: the Eraser is the brush with an erase composite")
 * made the eraser accumulate into the alpha map like the brush and removed the
 * pre-fill -- and left the substitution behind. So the preview drew a nearly
 * empty canvas in place of the layer, and every stroke on that layer vanished
 * for the duration of the drag and came back, correctly erased, on release.
 * Measured before the repair: a pixel far from the eraser read (58,58,58,255) --
 * the checkerboard -- while the layer itself still held (255,0,0,255).
 *
 * WHY NOT SIMPLY `destination-out` ON THE COMPOSITE. Because by the time the
 * active layer is reached, `_compBuffer` already holds every layer beneath it.
 * Erasing there punches a hole through the whole stack. It looks perfect on a
 * single layer over the checkerboard -- which is exactly how such a fix gets
 * shipped -- and reveals nothing but transparency where a lower layer should
 * have shown through.
 *
 * So the erase is applied to a COPY OF THE ACTIVE LAYER ALONE, in a scratch the
 * size of the stroke, and the result is drawn at the layer's position with its
 * own opacity and blend mode. Lower layers are already below it; upper layers
 * still composite over it afterwards.
 *
 * BOUNDED. The scratch covers the stroke's accumulated rectangle, not the
 * document -- `_compBuffer` is rebuilt from scratch on every composite, so the
 * whole erased region must be redrawn each frame and the FRAME rectangle is not
 * enough. That is the same bound the brush's own preview blit uses.
 *
 * Selection is NOT applied here. It is already baked into `S.stroke.canvas` by
 * `alphaMapToImageData` (BE4: "SELECTION IS APPLIED ONCE, HERE"), and applying
 * it again would square it.
 */
function _drawErasedActiveLayer(x, L, w, h) {
    const d = S.stroke.dirty;
    const hasRect = d && d.x1 >= d.x0 && d.y1 >= d.y0;
    if (!hasRect) { x.drawImage(L.canvas, 0, 0); return; }
    const dx = Math.max(0, d.x0), dy = Math.max(0, d.y0);
    const dw = Math.min(S.W, d.x1 + 1) - dx;
    const dh = Math.min(S.H, d.y1 + 1) - dy;
    if (dw <= 0 || dh <= 0) { x.drawImage(L.canvas, 0, 0); return; }

    if (!_eraseScratch || _eraseScratch.width < dw || _eraseScratch.height < dh) {
        const gw = Math.min(S.W, Math.max(dw, _eraseScratch ? _eraseScratch.width : 0));
        const gh = Math.min(S.H, Math.max(dh, _eraseScratch ? _eraseScratch.height : 0));
        _eraseScratch = _createCanvas(gw, gh);
        _eraseScratchCtx = _eraseScratch.getContext("2d", { colorSpace: "srgb" });
    }
    const e = _eraseScratchCtx;
    e.setTransform(1, 0, 0, 1, 0, 0);
    e.globalAlpha = 1;
    e.globalCompositeOperation = "source-over";
    e.clearRect(0, 0, dw, dh);
    e.drawImage(L.canvas, dx, dy, dw, dh, 0, 0, dw, dh);
    // THE SAME ALPHA `commitStroke` USES, so the preview and the committed
    // result are the same picture. Buildup already folded Opacity into the
    // per-dab target, so applying it again here would square it.
    e.globalAlpha = S.brushBuildup ? 1 : S.brushOpacity;
    e.globalCompositeOperation = "destination-out";
    e.drawImage(S.stroke.canvas, dx, dy, dw, dh, 0, 0, dw, dh);
    e.globalAlpha = 1;
    e.globalCompositeOperation = "source-over";

    // The layer everywhere EXCEPT the stroke rectangle, then the erased
    // rectangle. Even-odd over two rects is the region between them, so the two
    // draws never overlap -- which is what keeps a non-normal blend mode
    // blending each pixel exactly once.
    x.save();
    x.beginPath();
    x.rect(0, 0, w, h);
    x.rect(dx, dy, dw, dh);
    x.clip("evenodd");
    x.drawImage(L.canvas, 0, 0);
    x.restore();
    x.drawImage(_eraseScratch, 0, 0, dw, dh, dx, dy, dw, dh);
}

let _maskPreview = null;
function _composite2D(c, w, h, z, eraserActive, AL, strokeDrawCanvas, showMask) {
    if (!_compBuffer || _compBuffer.width !== w || _compBuffer.height !== h) {
        _compBuffer = _createCanvas(w, h);
        _compCtx = _compBuffer.getContext("2d", { colorSpace: "srgb" });
    }
    const x = _compCtx;
    const strokeInStack = strokeDrawCanvas && S.tool === "brush" && !S.editingMask;

    // Cache fast-path: when there's no wet stroke and the version counter
    // hasn't changed since we last built _compBuffer, the layer loop +
    // develop pipeline produce identical pixels. Cursor moves never bump
    // the version, so this elides the full pipeline on every mousemove.
    //
    // Hard-skip while S.drawing is true. Two reasons:
    //   (1) The brush dirty-rect path calls _composite2D with stroke=null
    //       intentionally (to capture pre-stroke display). Without this
    //       guard, that call would save the cache at the post-saveUndo
    //       version with PRE-stroke pixels, then commitStroke at pointerup
    //       wouldn't bump the version → next composite hits the stale
    //       cache and the just-drawn line doesn't appear until the next
    //       saveUndo (i.e., the start of the next stroke).
    //   (2) Smudge / blur / dodge / clone / liquify mutate L.canvas per
    //       dab without per-dab version bumps, so caching mid-stroke
    //       would freeze the display on the first dab.
    const canUseCache = !strokeDrawCanvas
        && !S.drawing
        && _compBufCache
        && _compBufCacheVer === _compositeVersion
        && _compBufCache.width === w
        && _compBufCache.height === h;

    if (canUseCache) {
        x.globalAlpha = 1; x.globalCompositeOperation = "source-over";
        x.clearRect(0, 0, w, h);
        x.drawImage(_compBufCache, 0, 0);
    } else {
        x.clearRect(0, 0, w, h);
        x.filter = "none"; x.globalAlpha = 1; x.globalCompositeOperation = "source-over";

        for (let i = 0; i < S.layers.length; i++) {
            const L = S.layers[i];
            if (!L.visible) continue;
            if (L.type === "adjustment") { _applyAdjustment(x, w, h, L); continue; }
            x.globalCompositeOperation = L.blendMode || "source-over";
            x.globalAlpha = L.opacity;
            if (eraserActive && L === AL && !S.editingMask) {
                _drawErasedActiveLayer(x, L, w, h);
            } else {
                x.drawImage(L.canvas, 0, 0);
                if (strokeInStack && L === AL) {
                    x.save();
                    // R1-A. THE ALPHA `commitStroke` USES. Buildup folded
                    // Opacity into the per-dab target (2532), so applying it
                    // again here previews a different picture from the one that
                    // will land. Off -- thirteen of sixteen presets -- this is
                    // character for character the expression it replaced.
                    x.globalAlpha = (S.brushBuildup ? 1 : S.brushOpacity) * L.opacity;
                    x.globalCompositeOperation = "source-over";
                    x.drawImage(strokeDrawCanvas, 0, 0);
                    x.restore();
                    x.globalCompositeOperation = "source-over";
                    x.globalAlpha = 1;
                }
            }
            // Render AI preview after the reference layer (bottom-most layer)
            // so user paint layers appear on top of the preview
            if (i === 0 && S.livePreview.active && S.livePreview.canvas) {
                x.globalCompositeOperation = "source-over";
                x.globalAlpha = 1;
                x.drawImage(S.livePreview.canvas, 0, 0);
            }
        }
        // Develop: global, always-last, applied to the document-resolution buffer.
        // Runs before UI overlays so HUD elements aren't tinted.
        _applyDevelop(x, w, h, S.developParams);

        // Snapshot the developed result for cursor-move re-use. Skip while
        // a stroke is in progress — see the canUseCache comment above for
        // why this matters even when strokeDrawCanvas itself is null.
        if (!strokeDrawCanvas && !S.drawing) {
            if (!_compBufCache || _compBufCache.width !== w || _compBufCache.height !== h) {
                _compBufCache = _createCanvas(w, h);
            }
            const cacheCtx = _compBufCache.getContext("2d", { colorSpace: "srgb" });
            cacheCtx.globalCompositeOperation = "source-over"; cacheCtx.globalAlpha = 1;
            cacheCtx.clearRect(0, 0, w, h);
            cacheCtx.drawImage(_compBuffer, 0, 0);
            _compBufCacheVer = _compositeVersion;
        }
    }

    c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
    // `S.imagePreviewActive` means the WebGL preview owns the document
    // display, so the display canvas is a transparent UI overlay and the doc
    // blit is skipped.
    //
    // U2 CORRECTED THIS COMMENT. It used to say the composite was routed to an
    // <img> element "see window.StudioCanvasImagePreview", and that
    // "_compBuffer is still built (the preview module reads from it)". Both
    // halves are false. `StudioCanvasImagePreview` does not exist -- the name
    // occurs exactly once in the tree, in the comment itself -- and the WebGL
    // preview reads `getFlattenedImageData()`, which builds its own canvas and
    // never touches `_compBuffer`. The claim justified building a full-document
    // composite on every pointer move for a reader that was not there.
    if (!S.imagePreviewActive) {
        c.drawImage(_compBuffer, 0, 0);
    }

    if (showMask) {
        let overlay = S.mask.canvas;
        if (S.drawing && S.editingMask && S.stroke.alphaMap) {
            // Preview the same single merge as commit. Coverage is not the
            // remaining mask, and two tinted overlays would double its alpha.
            if (!_maskPreview) _maskPreview = _createCanvas(S.W, S.H);
            if (_maskPreview.width !== S.W) _maskPreview.width = S.W;
            if (_maskPreview.height !== S.H) _maskPreview.height = S.H;
            const mx = _maskPreview.getContext("2d");
            mx.clearRect(0, 0, S.W, S.H);
            mx.drawImage(S.mask.canvas, 0, 0);
            mx.globalCompositeOperation = strokeTool() === "eraser" ? "destination-out" : "source-over";
            mx.drawImage(S.stroke.canvas, 0, 0);
            mx.globalCompositeOperation = "source-over";
            overlay = _maskPreview;
        }
        c.globalCompositeOperation = "source-over";
        c.globalAlpha = S.mask.opacity;
        c.drawImage(overlay, 0, 0);
    }
    c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
}

function drawRegionOverlay(ctx) {
    if (!S.regions.length) return;
    ctx.save();
    for (let ri = 0; ri < S.regions.length; ri++) {
        const r = S.regions[ri];
        if (!r.visible) continue;
        const tmp = getTempCanvas("regionOvl_" + ri, S.W, S.H);
        const tc = tmp.getContext("2d", { colorSpace: "srgb" });
        tc.fillStyle = r.color; tc.fillRect(0, 0, S.W, S.H);
        tc.globalCompositeOperation = "destination-in"; tc.drawImage(r.canvas, 0, 0);
        tc.globalCompositeOperation = "source-over";
        ctx.globalAlpha = 0.38; ctx.drawImage(tmp, 0, 0); ctx.globalAlpha = 1;
        if (r.id === S.activeRegionId) {
            ctx.save(); ctx.strokeStyle = r.color; ctx.lineWidth = 2; ctx.setLineDash([5, 3]);
            ctx.strokeRect(1, 1, S.W - 2, S.H - 2); ctx.restore();
        }
    }
    ctx.restore();
}

function composite(dirtyOnly) {
    const c = S.ctx, w = S.W, h = S.H, z = S.zoom;
    if (!c) return;

    // Dirty-rect fast path during brush strokes
    const _canUseDirtyFastPath = (function () {
        if (!dirtyOnly || !S.drawing || S.tool !== "brush" || !S.stroke.alphaMap || !_compositeCache) return false;
        if (S.editingMask) return false;
        for (let i = S.activeLayerIdx + 1; i < S.layers.length; i++) {
            if (S.layers[i].visible) return false;
        }
        return true;
    })();

    // Skip the dirty-rect fast path when image-preview mode is active. The
    // fast path's whole point is to avoid rebuilding _compBuffer during a
    // brush stroke by re-blitting a cached snapshot of the display canvas;
    // in image-preview mode the display canvas isn't holding the document
    // anyway, so the cache is irrelevant and we fall through to the full
    // path (which still builds _compBuffer for the preview <img>).
    if (_canUseDirtyFastPath && !S.imagePreviewActive) {
        // BE8. THE "FAST PATH" WAS DOING FULL-CANVAS WORK EVERY POINTER MOVE.
        //
        // It restored the ENTIRE cached composite with one `putImageData` and
        // then drew the ENTIRE stroke canvas over it. BE0-E measured the
        // consequence: `composite` was 96% of a pointermove dispatch and the
        // "fast" path was barely faster than the full one, because the only
        // thing it skipped was rebuilding the layer stack.
        //
        // Both halves are now scoped to the rectangle that actually changed
        // since the last frame. Restoring the cache needs DEVICE coordinates,
        // because `putImageData` ignores the canvas transform and writes raw
        // pixels; drawing the stroke needs DOCUMENT coordinates, because
        // `drawImage` respects it. Getting those two the same way round is the
        // whole trick, and it is why the conversion is spelled out rather than
        // folded into the call.
        const d = S.stroke.frameDirty || S.stroke.dirty;
        if (d.x1 >= d.x0 && d.y1 >= d.y0) {
            const onMask = S.editingMask;
            const col = onMask ? S.maskColor : S.color;
            const dx = Math.max(0, d.x0), dy = Math.max(0, d.y0);
            const dw = Math.min(S.W, d.x1 + 1) - dx, dh = Math.min(S.H, d.y1 + 1) - dy;
            if (dw > 0 && dh > 0) {
                const dpr = S.displayDpr || 1;
                const sc = z.scale * dpr;
                // One device pixel of margin each side: the display transform
                // is fractional, so a document rect lands between device
                // pixels and antialiasing bleeds outside the exact bounds.
                const cw = S.canvas.width, ch = S.canvas.height;
                let ex = Math.floor(dx * sc + z.ox * dpr) - 2;
                let ey = Math.floor(dy * sc + z.oy * dpr) - 2;
                let ew = Math.ceil(dw * sc) + 4;
                let eh = Math.ceil(dh * sc) + 4;
                if (ex < 0) { ew += ex; ex = 0; }
                if (ey < 0) { eh += ey; ey = 0; }
                if (ex + ew > cw) ew = cw - ex;
                if (ey + eh > ch) eh = ch - ey;
                if (ew > 0 && eh > 0) {
                    c.setTransform(1, 0, 0, 1, 0, 0);
                    c.putImageData(_compositeCache, 0, 0, ex, ey, ew, eh);
                    applyDisplayTransform(c);
                    const img = alphaMapToImageData(col, d);
                    S.stroke.ctx.clearRect(dx, dy, dw, dh);
                    S.stroke.ctx.putImageData(img, 0, 0, dx, dy, dw, dh);
                    // R1-A. See `_composite2D`. The mask overlay keeps its own
                    // display opacity -- that is not brush Opacity and buildup
                    // has nothing to say about it.
                    c.globalAlpha = onMask ? S.mask.opacity
                        : (S.brushBuildup ? 1 : S.brushOpacity);
                    c.globalCompositeOperation = "source-over";
                    c.drawImage(S.stroke.canvas, dx, dy, dw, dh, dx, dy, dw, dh);
                    c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
                    applyDisplayTransform(c);
                }
            }
            // Consumed. The next frame starts from nothing changed, which is
            // what makes this bounded rather than growing with the stroke.
            if (S.stroke.frameDirty) {
                S.stroke.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
            }
            return;
        }
    }

    const eraserActive = S.drawing && S.stroke.canvas && strokeTool() === "eraser";
    const AL = activeLayer();
    const showMask = S.mask.visible && S.mask.canvas;

    // Prepare wet stroke canvas for brush
    //
    // U2. CONVERT ONLY WHAT CHANGED SINCE THE LAST FRAME. This block used to
    // convert the ACCUMULATED stroke rectangle and clear the WHOLE canvas on
    // every pointer move, so a long stroke re-converted its entire bounding box
    // per move and the cost grew with the stroke.
    //
    // BE8 fixed exactly this in the Canvas2D dirty fast path and could not fix
    // it here, because that path is unreachable when the WebGL preview owns the
    // display -- which is the shipping default. So the shipping default kept
    // paying. The stroke canvas PERSISTS between frames and the alpha map only
    // ever accumulates, so re-converting the frame's own rectangle is
    // sufficient: every pixel outside it already holds the value it should.
    //
    // E0. THE ERASER NEEDS THIS BLOCK TOO. It accumulates into the alpha map
    // exactly like the brush, but this conversion was gated on `tool ===
    // "brush"`, so `S.stroke.canvas` stayed EMPTY for an eraser stroke --
    // measured, 0 painted pixels where the preview expected coverage. That is
    // the second half of the vanishing-layer defect: even a correct preview has
    // nothing to erase with if the coverage was never converted.
    //
    // `strokeDrawCanvas` stays brush-only, because every one of its consumers
    // means "there is a wet BRUSH stroke to bake into the stack".
    let strokeDrawCanvas = null;
    const wetTool = (S.drawing && S.stroke.canvas && S.stroke.alphaMap
        && (strokeTool() === "brush" || strokeTool() === "eraser")) ? strokeTool() : null;
    if (wetTool) {
        const onMask = S.editingMask;
        const col = onMask ? S.maskColor : S.color;
        const frame = S.stroke.frameDirty || S.stroke.dirty;
        if (frame.x1 >= frame.x0 && frame.y1 >= frame.y0) {
            const dx = Math.max(0, frame.x0), dy = Math.max(0, frame.y0);
            const dw = Math.min(S.W, frame.x1 + 1) - dx;
            const dh = Math.min(S.H, frame.y1 + 1) - dy;
            if (dw > 0 && dh > 0) {
                const img = alphaMapToImageData(col, frame);
                S.stroke.ctx.clearRect(dx, dy, dw, dh);
                S.stroke.ctx.putImageData(img, 0, 0, dx, dy, dw, dh);
            }
            // CONSUMED, exactly as the Canvas2D fast path consumes it. Without
            // this reset `frameDirty` would accumulate like `dirty` and the
            // bound would silently stop being a bound -- the failure would be
            // invisible because the pixels would still be right.
            if (S.stroke.frameDirty) {
                S.stroke.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 };
            }
        }
        if (wetTool === "brush") strokeDrawCanvas = S.stroke.canvas;
    }

    // Background fill — reads --bg-void from CSS so themes apply.
    // In image-preview mode S.canvas is a transparent UI-only overlay over
    // the <img>, so clear (don't fill) and skip the checker — those visuals
    // are baked into the <img> source by the preview module instead.
    c.setTransform(1, 0, 0, 1, 0, 0);
    if (S.imagePreviewActive) {
        c.clearRect(0, 0, S.canvas.width, S.canvas.height);
    } else {
        if (!S._voidColor) S._voidColor = getComputedStyle(document.documentElement).getPropertyValue("--bg-void").trim() || "#1e2130";
        c.fillStyle = S._voidColor;
        c.fillRect(0, 0, S.canvas.width, S.canvas.height);
    }

    applyDisplayTransform(c);

    // Smoothing on for moderate zoom (matches Lightroom feel); switch to
    // nearest-neighbor only at very high pixel-peeping zoom (>= 8x), where
    // bilinear blur becomes visible and crisp pixels are preferable.
    c.imageSmoothingEnabled = z.scale < 8.0;
    if (c.imageSmoothingEnabled) c.imageSmoothingQuality = "high";

    // Clip checkerboard to exact document bounds — prevents subpixel bleed at edges
    if (!S.imagePreviewActive) {
        c.save();
        c.beginPath(); c.rect(0, 0, w, h); c.clip();
        checker(c, w, h);
        c.restore();
    }

    // Cache for dirty-rect during brush: capture base composite WITHOUT stroke
    // Only when no visible layers above active (same guard as dirty fast path)
    // BE8. DO NOT BUILD A CACHE THAT NOTHING CAN USE.
    //
    // `_compositeCache` exists for one consumer: the dirty-rect fast path at
    // the top of this function. That path is gated on `!S.imagePreviewActive`,
    // because when the WebGL preview owns the display -- which is the SHIPPING
    // DEFAULT -- the Canvas2D snapshot is not what the owner is looking at.
    //
    // The cache was being built anyway, on every pointer move, with a
    // full-canvas `getImageData`. So the default configuration paid for a
    // snapshot that the only code able to read it was forbidden to read.
    //
    // This is also why BE0-E measured the "fast path" as barely faster than
    // the full one: on the default display path it was never reached.
    const _canBuildCache = S.drawing && S.tool === "brush" && strokeDrawCanvas && !S.editingMask &&
        !S.imagePreviewActive &&
        !S.layers.slice(S.activeLayerIdx + 1).some(l => l.visible);
    if (_canBuildCache) {
        _composite2D(c, w, h, z, eraserActive, AL, null, showMask);
        _drawGrid(c, w, h, z);
        c.setTransform(1, 0, 0, 1, 0, 0);
        try { _compositeCache = c.getImageData(0, 0, S.canvas.width, S.canvas.height); } catch (e) { _compositeCache = null; }
        applyDisplayTransform(c);
        // Draw stroke overlay on top for display
        // R1-A. See `_composite2D`.
        c.globalAlpha = S.brushBuildup ? 1 : S.brushOpacity;
        c.globalCompositeOperation = "source-over";
        c.drawImage(strokeDrawCanvas, 0, 0);
        c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
    } else {
        // Default brush path. In normal mode the wet stroke gets baked
        // into _compBuffer inside _composite2D and reaches S.ctx via the
        // _compBuffer blit. When the WebGL preview owns the display that blit
        // is gated off, so we instead pass `null` for the stroke and draw the
        // stroke explicitly on `c` after. The display canvas is a transparent
        // UI overlay over the WebGL surface in that mode, so the stroke renders
        // above the preview.
        var passStroke = S.imagePreviewActive ? null : strokeDrawCanvas;

        // U2. DO NOT BUILD A COMPOSITE NOTHING WILL READ.
        //
        // `_composite2D`'s product is `_compBuffer`, and it has exactly two
        // consumers: the display blit, gated on `!S.imagePreviewActive`, and
        // the `_compBufCache` snapshot, gated on `!S.drawing`. During a brush
        // stroke on the WebGL default BOTH are off, so the function ran a full
        // layer loop plus develop over the whole document on every pointer move
        // and threw the result away.
        //
        // The comment that justified building it anyway said "the preview
        // module reads from it" and pointed at `window.StudioCanvasImagePreview`
        // -- an object that DOES NOT EXIST. Verified: the name occurs exactly
        // once in the whole tree, in that comment. It is a stale reference to a
        // module the WebGL preview replaced, whose flag name it inherited.
        // `canvas-webgl-preview.js` reads `getFlattenedImageData()`, which
        // builds its own canvas and never touches `_compBuffer`.
        //
        // `_composite2D` also draws the mask overlays onto `c`, which ARE
        // visible, so the skip is gated on there being no mask to draw. In
        // practice mask and region editing already route to the Canvas2D live
        // fallback (`canvas-ui.js` `_beginWebGLLiveFallbackIfNeeded`), which
        // clears `imagePreviewActive` -- the explicit gates are belt and braces
        // rather than the only thing standing between this and a wrong screen.
        var _skipDiscardedComposite =
            S.imagePreviewActive && S.drawing && S.tool === "brush"
            && S.stroke.alphaMap && !showMask && !S.editingMask;
        if (!_skipDiscardedComposite) {
            _composite2D(c, w, h, z, eraserActive, AL, passStroke, showMask);
        }
        _drawGrid(c, w, h, z);
        _compositeCache = null;
        if (S.imagePreviewActive && strokeDrawCanvas && !S.editingMask) {
            // R1-A. See `_composite2D`. THIS IS THE SHIPPING DEFAULT -- the
            // WebGL preview owns the display, so this is the site the owner
            // actually meets. Repairing only this one would leave the Canvas2D
            // fallback previewing a different picture.
            c.globalAlpha = S.brushBuildup ? 1 : S.brushOpacity;
            c.globalCompositeOperation = "source-over";
            // BOUNDED TO THE STROKE, not to the document. `c` is cleared at the
            // top of every composite, so the whole wet stroke must be redrawn --
            // but "the whole wet stroke" is its accumulated bounding box, which
            // is not the same thing as the canvas.
            var _sd = S.stroke.dirty;
            if (_sd && _sd.x1 >= _sd.x0 && _sd.y1 >= _sd.y0) {
                var _sx = Math.max(0, _sd.x0), _sy = Math.max(0, _sd.y0);
                var _sw = Math.min(S.W, _sd.x1 + 1) - _sx;
                var _sh = Math.min(S.H, _sd.y1 + 1) - _sy;
                if (_sw > 0 && _sh > 0) {
                    c.drawImage(strokeDrawCanvas, _sx, _sy, _sw, _sh,
                                _sx, _sy, _sw, _sh);
                }
            } else {
                c.drawImage(strokeDrawCanvas, 0, 0);
            }
            c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
        }
    }

    // UI overlays
    c.globalAlpha = 1; c.globalCompositeOperation = "source-over";

    if (S.regions.length) {
        if (!S.regionMode) c.globalAlpha = 0.3;
        drawRegionOverlay(c);
        c.globalAlpha = 1;
    }
    if (S.symmetry !== "none" && (S.tool === "brush" || S.tool === "eraser" || S.tool === "smudge")) symGuides(c);

    // Restore zoom transform + safe state so browser compositor
    // doesn't re-rasterize at identity during layer transactions (Firefox WebRender)
    c.globalAlpha = 1;
    c.globalCompositeOperation = "source-over";
    applyDisplayTransform(c);
}

// ========================================================================
// EXPORT
// ========================================================================
function exportCanvas() {
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.filter = "none"; x.globalAlpha = 1; x.globalCompositeOperation = "source-over";
    // JPEG needs a white background (no alpha channel)
    x.fillStyle = "#ffffff";
    x.fillRect(0, 0, S.W, S.H);
    for (const L of S.layers) {
        if (!L.visible) continue;
        if (L.type === "adjustment") { _applyAdjustment(x, S.W, S.H, L); continue; }
        x.globalCompositeOperation = L.blendMode || "source-over";
        x.globalAlpha = L.opacity;
        x.drawImage(L.canvas, 0, 0);
    }
    x.filter = "none"; x.globalAlpha = 1; x.globalCompositeOperation = "source-over";
    _applyDevelop(x, S.W, S.H, S.developParams);
    // JPEG q=0.95 is visually lossless and ~10x smaller than PNG.
    // The image is only used as an img2img init — it gets denoised anyway.
    return c.toDataURL("image/jpeg", 0.95);
}

function isCanvasBlank() {
    // Check if the composited canvas is all near-white.
    // Used for txt2img routing: blank = txt2img, content = img2img.
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.fillStyle = "#fff"; x.fillRect(0, 0, S.W, S.H);
    for (const L of S.layers) {
        if (!L.visible) continue;
        if (L.type === "adjustment") { _applyAdjustment(x, S.W, S.H, L); continue; }
        x.globalCompositeOperation = L.blendMode || "source-over";
        x.globalAlpha = L.opacity;
        x.drawImage(L.canvas, 0, 0);
    }
    const d = x.getImageData(0, 0, S.W, S.H).data;
    for (let i = 0; i < d.length; i += 4) {
        if (d[i] < 249 || d[i + 1] < 249 || d[i + 2] < 249) return false;
    }
    return true;
}

// Coverage belongs to the document, independently of overlay and tool choice.
function hasGenerationMask() {
    if (!S.mask.ctx) return false;
    const d = S.mask.ctx.getImageData(0, 0, S.W, S.H).data;
    for (let i = 3; i < d.length; i += 4) if (d[i]) return true;
    return false;
}
function clearGenerationMask() {
    if (_strokeTransaction) abortStroke();
    if (!hasGenerationMask()) return false;
    const mask = S.editingMask, region = S.regionMode;
    S.editingMask = true; S.regionMode = false;
    noteStrokeUndo("Clear generation mask");
    S.mask.ctx.clearRect(0, 0, S.W, S.H);
    S.editingMask = mask; S.regionMode = region;
    clearStrokeUndo();
    markCompositeDirty(); composite();
    return true;
}

function exportMask() {
    // The mask is the MASK LAYER, plus visible regions in Regional mode.
    //
    // A third path used to sit above this one, building the mask from the
    // painted layers instead. It served a superseded sketch mode (see
    // INPAINT_SKETCH in PROJECT_STATE.md) and was unreachable: nothing ever
    // set the state it tested for.
    const maskC = _createCanvas(S.W, S.H);
    const mx = maskC.getContext("2d", { colorSpace: "srgb" });
    mx.drawImage(S.mask.canvas, 0, 0);
    if (S.studioMode === "Edit" && S.inpaintMode === "Regional" && S.regions.length) {
        for (const r of S.regions) { if (r.visible) mx.drawImage(r.canvas, 0, 0); }
    }
    const d = mx.getImageData(0, 0, S.W, S.H).data;
    let has = false;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) { has = true; break; }
    if (!has) return "null";
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.fillStyle = "#000"; x.fillRect(0, 0, S.W, S.H);
    const o = x.getImageData(0, 0, S.W, S.H), od = o.data;
    for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0) { od[i] = 255; od[i + 1] = 255; od[i + 2] = 255; od[i + 3] = 255; }
    x.putImageData(o, 0, 0);
    return c.toDataURL("image/png");
}

// Shared flatten logic used by both exportFlattened (saved files) and
// getFlattenedImageData (WebGL preview texture source). Keeping the
// compositing path in one place means saved files and live preview
// always agree on layer order, blend modes, adjustments, and Develop —
// no drift between what the user sees and what gets saved.
function _renderFlattenedToContext(ctx, options) {
    options = options || {};
    // U2. A region-scoped flatten passes a context that is REGION-SIZED and
    // pre-translated by (-x0, -y0), so `drawImage(L.canvas, 0, 0)` lands in the
    // right place and clips to the region for free.
    //
    // The adjustment ops must be told the REGION's dimensions, not the
    // document's, because `getImageData`/`putImageData` ignore the transform
    // and address the backing store directly. All three are strictly per-pixel
    // -- verified: each is one `getImageData(0,0,w,h)` and a `p += 4` loop with
    // no neighbour access -- which is what makes region-scoping them correct.
    const rw = options.regionWidth || S.W;
    const rh = options.regionHeight || S.H;
    if (options.whiteBackground) {
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(options.regionX || 0, options.regionY || 0, rw, rh);
    }
    for (const L of S.layers) {
        if (!L.visible) continue;
        if (L.type === "adjustment") { _applyAdjustment(ctx, rw, rh, L); continue; }
        ctx.globalCompositeOperation = L.blendMode || "source-over";
        ctx.globalAlpha = L.opacity;
        ctx.drawImage(L.canvas, 0, 0);
    }
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    if (options.applyDevelop !== false) {
        _applyDevelop(ctx, rw, rh, S.developParams);
    }
}

/**
 * U2. May the display be updated from a REGION rather than the whole document?
 *
 * NO WHENEVER DEVELOP IS DOING ANYTHING, and that is a correctness rule rather
 * than caution. Develop's own header lists what it contains: "Highlights/
 * shadows: luminance blur", "Spatial: texture USM, clarity USM, sharpening",
 * "Vignette + grain". A luminance blur and an unsharp mask read NEIGHBOURING
 * pixels, so a region computed in isolation differs from the same region
 * computed inside the whole document -- and a vignette depends on the distance
 * to the document's centre, which a region does not know. The result would be a
 * visible seam at the region's edge.
 *
 * Develop is OFF by default (`studio-docs.js` creates every document with
 * `{_version: 1, enabled: false}`), so the bounded path covers the ordinary
 * case and correctness wins the rest.
 *
 * A NARROWER PREDICATE IS POSSIBLE AND IS NOT ATTEMPTED HERE: the per-pixel
 * half of Develop (exposure, contrast, temperature, curves, HSL) is
 * region-safe, and only the spatial and global half is not. Splitting them
 * would extend the fast path, and it needs Develop's own source reviewed
 * op-by-op rather than a guess from its header.
 */
function canPresentRegion() {
    const p = S.developParams;
    if (!p || !p.enabled) return true;
    const SD = window.StudioDevelop;
    if (SD && typeof SD._isIdentity === "function") {
        try { return !!SD._isIdentity(p); } catch (e) { return false; }
    }
    // Develop claims to be enabled and will not say whether it is a no-op.
    // Refuse the bounded path rather than guess.
    return false;
}

/**
 * Flattened RGBA for one half-open document region.
 *
 * Returns `null` when a region flatten would not be faithful, so the caller
 * falls back to the whole document rather than presenting a seam.
 */
function getFlattenedRegionImageData(x0, y0, x1, y1, options) {
    const opts = options || {};
    x0 = Math.max(0, Math.floor(x0));
    y0 = Math.max(0, Math.floor(y0));
    x1 = Math.min(S.W, Math.ceil(x1));
    y1 = Math.min(S.H, Math.ceil(y1));
    const rw = x1 - x0, rh = y1 - y0;
    if (rw <= 0 || rh <= 0) return null;
    if (!canPresentRegion()) return null;
    const c = _createCanvas(rw, rh);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.translate(-x0, -y0);
    _renderFlattenedToContext(x, {
        applyDevelop: opts.applyDevelop !== false,
        whiteBackground: !!opts.whiteBackground,
        regionX: x0, regionY: y0, regionWidth: rw, regionHeight: rh,
    });
    return x.getImageData(0, 0, rw, rh);
}

function exportFlattened(mime) {
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    _renderFlattenedToContext(x, {
        whiteBackground: mime === "image/jpeg" || mime === "image/webp",
        applyDevelop: true,
    });
    // Always encode as PNG for lossless transfer to the backend. The mime
    // arg above only controls the white-bg fill (JPEG/WebP have no alpha);
    // format conversion is the backend's job. Encoding lossy here would
    // double-compress when the backend re-encodes to JPEG/WebP, which
    // visibly desaturates and warm-shifts colors.
    return c.toDataURL("image/png");
}

// Canonical flattened RGBA pixels (preserves alpha, no white bg, no UI
// overlays, no checker). Develop is applied by default — same path as
// exportFlattened — so the WebGL preview texture matches the saved
// file's pixels exactly. Returns an ImageData whose .data is a
// Uint8ClampedArray ready for gl.texImage2D / gl.texSubImage2D.
//
// Options:
//   applyDevelop (default true)   — pass false to get pre-develop
//     pixels. Used by Develop's Before/After split + the eyedropper
//     samplers, which both need the layer composite without the
//     adjustment pipeline.
//   whiteBackground (default false) — fill the doc with white before
//     compositing. Mirrors the JPEG/WebP export path.
//
// Existing zero-arg callers (the WebGL preview texture upload) keep
// receiving developed flattened pixels exactly as before.
function getFlattenedImageData(options) {
    const opts = options || {};
    const applyDevelop = opts.applyDevelop !== false;
    const whiteBackground = !!opts.whiteBackground;
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    _renderFlattenedToContext(x, { applyDevelop: applyDevelop, whiteBackground: whiteBackground });
    return x.getImageData(0, 0, S.W, S.H);
}

// ========================================================================
// LAYER FLIP / ROTATE — Photoshop-style transforms on the active layer
//
// All ops act on activeLayer().canvas in place. The layer canvas size
// is bound to the document (S.W × S.H), so 90°/270° rotations on a
// non-square doc center-crop / center-pad to keep the layer document-
// sized — matches Photoshop's "Layer → Rotate 90° CW" behavior.
//
// 90 / 180 paths use ImageData row/col swaps so they're lossless.
// Arbitrary rotation uses a temp canvas + drawImage for smoothing.
// Each op pushes one undo step before mutating.
// ========================================================================

function _flipLayerHorizontal() {
    const L = activeLayer();
    if (!L || L.type === "adjustment") return;
    saveUndo("Flip Horizontal", { layer: L });
    const w = S.W, h = S.H;
    const src = L.ctx.getImageData(0, 0, w, h);
    const sd = src.data;
    const dst = new ImageData(w, h);
    const dd = dst.data;
    for (let y = 0; y < h; y++) {
        const rowStart = y * w * 4;
        for (let x = 0; x < w; x++) {
            const si = rowStart + x * 4;
            const di = rowStart + (w - 1 - x) * 4;
            dd[di] = sd[si]; dd[di + 1] = sd[si + 1];
            dd[di + 2] = sd[si + 2]; dd[di + 3] = sd[si + 3];
        }
    }
    L.ctx.putImageData(dst, 0, 0);
}

function _flipLayerVertical() {
    const L = activeLayer();
    if (!L || L.type === "adjustment") return;
    saveUndo("Flip Vertical", { layer: L });
    const w = S.W, h = S.H;
    const src = L.ctx.getImageData(0, 0, w, h);
    const sd = src.data;
    const dst = new ImageData(w, h);
    const dd = dst.data;
    const rowBytes = w * 4;
    for (let y = 0; y < h; y++) {
        const srcStart = y * rowBytes;
        const dstStart = (h - 1 - y) * rowBytes;
        for (let i = 0; i < rowBytes; i++) dd[dstStart + i] = sd[srcStart + i];
    }
    L.ctx.putImageData(dst, 0, 0);
}

function _rotateLayer180() {
    const L = activeLayer();
    if (!L || L.type === "adjustment") return;
    saveUndo("Rotate 180°", { layer: L });
    const w = S.W, h = S.H;
    const src = L.ctx.getImageData(0, 0, w, h);
    const sd = src.data;
    const dst = new ImageData(w, h);
    const dd = dst.data;
    const total = w * h;
    for (let i = 0; i < total; i++) {
        const si = i * 4;
        const di = (total - 1 - i) * 4;
        dd[di] = sd[si]; dd[di + 1] = sd[si + 1];
        dd[di + 2] = sd[si + 2]; dd[di + 3] = sd[si + 3];
    }
    L.ctx.putImageData(dst, 0, 0);
}

// 90° rotation — operates on the layer's actual content bounding box
// (non-transparent region) instead of the full S.W × S.H canvas, so
// successive rotations don't compound over letterbox margins from a
// previous rotation. Content is placed centered on the canvas; any
// portion exceeding canvas bounds is clipped at draw time. Callers
// that want overflow preserved should engage the Transform tool via
// canvas-ui's _smartRotate helper before invoking this directly.
function _rotateLayer90Common(direction) {
    const L = activeLayer();
    if (!L || L.type === "adjustment") return;
    saveUndo(direction > 0 ? "Rotate 90° CW" : "Rotate 90° CCW", { layer: L });
    const w = S.W, h = S.H;
    const bounds = getLayerContentBounds(L);
    if (!bounds) return;
    const bx = bounds.x, by = bounds.y, bw = bounds.w, bh = bounds.h;

    const src = L.ctx.getImageData(bx, by, bw, bh);
    const sd = src.data;
    const rotW = bh, rotH = bw;
    const rot = new ImageData(rotW, rotH);
    const rd = rot.data;
    if (direction > 0) {
        for (let y = 0; y < rotH; y++) {
            for (let x = 0; x < rotW; x++) {
                const sx = y;
                const sy = bh - 1 - x;
                const si = (sy * bw + sx) * 4;
                const di = (y * rotW + x) * 4;
                rd[di] = sd[si]; rd[di + 1] = sd[si + 1];
                rd[di + 2] = sd[si + 2]; rd[di + 3] = sd[si + 3];
            }
        }
    } else {
        for (let y = 0; y < rotH; y++) {
            for (let x = 0; x < rotW; x++) {
                const sx = bw - 1 - y;
                const sy = x;
                const si = (sy * bw + sx) * 4;
                const di = (y * rotW + x) * 4;
                rd[di] = sd[si]; rd[di + 1] = sd[si + 1];
                rd[di + 2] = sd[si + 2]; rd[di + 3] = sd[si + 3];
            }
        }
    }
    const tmp = _createCanvas(rotW, rotH);
    tmp.getContext("2d", { colorSpace: "srgb" }).putImageData(rot, 0, 0);

    L.ctx.clearRect(0, 0, w, h);
    // Center the rotated content. May overflow canvas; the drawImage
    // call will clip naturally. Callers expecting to preserve overflow
    // should detect the overflow before calling and route to a
    // Transform-tool flow instead.
    const dx = Math.round((w - rotW) / 2);
    const dy = Math.round((h - rotH) / 2);
    L.ctx.drawImage(tmp, dx, dy);
}

function _rotateLayer90CW() { _rotateLayer90Common(1); }
function _rotateLayer90CCW() { _rotateLayer90Common(-1); }

// Arbitrary rotation — bilinear via canvas. Operates on the content
// bbox; content is rotated around the document center at 1× scale.
// Overflow is clipped at canvas bounds; engage Transform mode for
// non-destructive positioning (see canvas-ui's _smartRotate).
function _rotateLayerArbitrary(degrees) {
    const L = activeLayer();
    if (!L || L.type === "adjustment") return;
    const deg = Number(degrees);
    if (!Number.isFinite(deg) || deg === 0) return;
    saveUndo(`Rotate ${deg}°`, { layer: L });
    const w = S.W, h = S.H;
    const bounds = getLayerContentBounds(L);
    if (!bounds) return;
    const bx = bounds.x, by = bounds.y, bw = bounds.w, bh = bounds.h;

    const snap = _createCanvas(bw, bh);
    snap.getContext("2d", { colorSpace: "srgb" }).drawImage(L.canvas, bx, by, bw, bh, 0, 0, bw, bh);

    const rad = deg * Math.PI / 180;
    L.ctx.save();
    L.ctx.imageSmoothingEnabled = true;
    L.ctx.imageSmoothingQuality = "high";
    L.ctx.clearRect(0, 0, w, h);
    L.ctx.translate(w / 2, h / 2);
    L.ctx.rotate(rad);
    L.ctx.drawImage(snap, -bw / 2, -bh / 2);
    L.ctx.restore();
}

// ========================================================================
// LIVE PAINTING — canvas hooks
// ========================================================================

/**
 * Composite visible layers (excluding AI preview) and downscale to target
 * resolution for Live generation submission.
 * Returns base64 data URL or null if no layers have content.
 */
function compositeForLive(targetW, targetH) {
    // Composite at document resolution first
    const c = _createCanvas(S.W, S.H);
    const x = c.getContext("2d", { colorSpace: "srgb" });
    x.fillStyle = "#ffffff";
    x.fillRect(0, 0, S.W, S.H);
    for (const L of S.layers) {
        if (!L.visible) continue;
        if (L.type === "adjustment") { _applyAdjustment(x, S.W, S.H, L); continue; }
        // Skip AI preview layer — we want user content only
        x.globalCompositeOperation = L.blendMode || "source-over";
        x.globalAlpha = L.opacity;
        x.drawImage(L.canvas, 0, 0);
    }
    // Apply develop so img2img / inpaint sees the developed image
    _applyDevelop(x, S.W, S.H, S.developParams);

    // Downscale to generation resolution
    if (targetW !== S.W || targetH !== S.H) {
        const sc = _createCanvas(targetW, targetH);
        const sx = sc.getContext("2d", { colorSpace: "srgb" });
        sx.drawImage(c, 0, 0, targetW, targetH);
        return sc.toDataURL("image/png");
    }
    return c.toDataURL("image/png");
}

/**
 * Set the AI preview image. Decodes base64 and stores for compositing.
 * The preview canvas is reused across updates.
 */
function setLivePreview(imageB64) {
    if (!imageB64) return;

    // Ensure preview canvas exists at document size
    if (!S.livePreview.canvas || S.livePreview.canvas.width !== S.W || S.livePreview.canvas.height !== S.H) {
        S.livePreview.canvas = _createCanvas(S.W, S.H);
        S.livePreview.ctx = S.livePreview.canvas.getContext("2d", { colorSpace: "srgb" });
    }

    // Decode image
    const img = new window.Image();
    img.onload = () => {
        S.livePreview.ctx.clearRect(0, 0, S.W, S.H);
        S.livePreview.ctx.drawImage(img, 0, 0, S.W, S.H);
        S.livePreview.active = true;
        markCompositeDirty();
        composite();
    };
    img.src = imageB64;
}

/**
 * Clear the AI preview layer and trigger re-composite.
 */
function clearLivePreview() {
    if (S.livePreview.ctx) {
        S.livePreview.ctx.clearRect(0, 0, S.W, S.H);
    }
    S.livePreview.active = false;
    markCompositeDirty();
    composite();
}

/**
 * Commit the AI preview to a new paint layer. Creates a layer named
 * "[Live]" with the current preview content.
 */
function applyLivePreview() {
    if (!S.livePreview.active || !S.livePreview.canvas) return;
    const L = makeLayer("[Live]", "paint");
    L.ctx.drawImage(S.livePreview.canvas, 0, 0);
    // Insert below active layer
    const idx = Math.max(0, S.activeLayerIdx);
    S.layers.splice(idx, 0, L);
    S.activeLayerIdx = idx;
    markCompositeDirty();
    // Trigger UI update if callback exists
    if (S.onUndoRedo) S.onUndoRedo();
    composite();
}

// ========================================================================
// RESIZE
// ========================================================================
function resizeCanvas(nw, nh) {
    if (_strokeTransaction) abortStroke();
    if (nw === S.W && nh === S.H) return;
    selectionClear();
    markCompositeDirty();
    const savedLayers = S.layers.map(L => L.canvas ? L.ctx.getImageData(0, 0, S.W, S.H) : null);
    const savedMask = S.mask.ctx.getImageData(0, 0, S.W, S.H);
    // Save region canvases before dimension change
    // Use each region's actual canvas size (may differ from S.W×S.H if regions
    // were never resized by a prior resizeCanvas call — this is the first fix).
    const savedRegions = S.regions.map(r => ({
        data: r.ctx.getImageData(0, 0, r.canvas.width, r.canvas.height),
        w: r.canvas.width, h: r.canvas.height
    }));
    // Don't clear undo/redo — _restoreStructural handles dimension changes
    // via canvasW/canvasH stored in each snapshot.
    S.W = nw; S.H = nh;
    // Scale undo depth based on resolution to prevent RAM bloat
    // ~4MB per layer snapshot at 1024x1024, structural undos snapshot ALL layers
    const pixels = nw * nh;
    if (pixels > 4000000) S.maxUndo = 25;       // >2K: ~25 steps
    else if (pixels > 2000000) S.maxUndo = 50;   // >~1.5K: ~50 steps
    else S.maxUndo = 100;                         // standard
    // Trim stacks to new limit (resolution increase → smaller maxUndo)
    while (S.undoStack.length > S.maxUndo) S.undoStack.shift();
    while (S.redoStack.length > S.maxUndo) S.redoStack.shift();
    S.stroke.canvas.width = nw; S.stroke.canvas.height = nh;
    S.mask.canvas.width = nw; S.mask.canvas.height = nh;
    S.mask.ctx = S.mask.canvas.getContext("2d", { colorSpace: "srgb" });
    for (let i = 0; i < S.layers.length; i++) {
        const L = S.layers[i];
        if (!L.canvas) continue;
        L.canvas.width = nw; L.canvas.height = nh;
        L.ctx = L.canvas.getContext("2d", { colorSpace: "srgb" });
        if (L.type === "reference") {
            L.ctx.fillStyle = "#fff"; L.ctx.fillRect(0, 0, nw, nh);
        }
        if (savedLayers[i]) {
            const tmpC = _createCanvas(savedLayers[i].width, savedLayers[i].height);
            tmpC.getContext("2d", { colorSpace: "srgb" }).putImageData(savedLayers[i], 0, 0);
            L.ctx.drawImage(tmpC, 0, 0, nw, nh);
        }
    }
    const tmpM = _createCanvas(savedMask.width, savedMask.height);
    tmpM.getContext("2d", { colorSpace: "srgb" }).putImageData(savedMask, 0, 0);
    S.mask.ctx.drawImage(tmpM, 0, 0, nw, nh);
    // Resize region canvases to match new dimensions
    for (let i = 0; i < S.regions.length; i++) {
        const r = S.regions[i];
        r.canvas.width = nw; r.canvas.height = nh;
        r.ctx = r.canvas.getContext("2d", { colorSpace: "srgb" });
        if (savedRegions[i]) {
            const sr = savedRegions[i];
            const tmpR = _createCanvas(sr.w, sr.h);
            tmpR.getContext("2d", { colorSpace: "srgb" }).putImageData(sr.data, 0, 0);
            r.ctx.drawImage(tmpR, 0, 0, nw, nh);
        }
    }
}

// ========================================================================
// FULL RESET
// ========================================================================
// Rebuild the engine state to a clean baseline. Used by the Reset
// Canvas button so a single call brings the document back to a known
// blank state without leaving stale side state behind.
//
// Clears: layers, masks, regions, selection, transform, undo/redo,
// stroke buffers, clone source, dirty flags, composite cache. Leaves
// clipboard alone (the user may want to paste back) and leaves zoom
// state alone (a Reset shouldn't yank the viewport).
//
// If `width` / `height` are passed and differ from the current
// document, the engine resizes first via the existing resizeCanvas
// path so the rebuilt layers come up at the correct dimensions.
function resetCanvasState(opts) {
    if (_strokeTransaction) abortStroke();
    // A reset is a NEW document: new identity, revision back to zero. Restoring
    // the same document must go through the session-restore path instead, which
    // carries its identity with it.
    _startNewDocument();
    opts = opts || {};
    const targetW = (opts.width  | 0) || S.W;
    const targetH = (opts.height | 0) || S.H;

    // Empty undo/redo first so resizeCanvas's stack-trim code below
    // doesn't run over arrays we're about to discard anyway.
    S.undoStack.length = 0;
    S.redoStack.length = 0;

    // Resize the document if requested. resizeCanvas is the existing
    // code path for changing dimensions — it preserves layer pixels,
    // but we overwrite them with a fresh stack right after.
    if (targetW !== S.W || targetH !== S.H) {
        resizeCanvas(targetW, targetH);
    }

    // Selection / marching-ants / poly + magnetic lasso scratch state.
    selectionClear();
    if (S.selection) {
        S.selection.lassoPoints = null;
        S.selection._isLasso = false;
        S.selection._isEllipse = false;
        S.selection._isMaskBased = false;
        S.selection._contour = null;
    }
    S._polyPoints = null;
    S._magAnchors = null;

    // Transform.
    if (S.transform) {
        S.transform.active = false;
        S.transform.bounds = null;
        S.transform.originalData = null;
        S.transform.layerIdx = -1;
        S.transform.canvas = null;
        S.transform.ctx = null;
        S.transform.dragMode = null;
        S.transform.dragStart = null;
        S.transform.origDragBounds = null;
        S.transform.rotation = 0;
        S.transform.flipH = false;
        S.transform.flipV = false;
        S.transform.aspectLock = false;
        S.transform.skewX = 0;
        S.transform.skewY = 0;
        S.transform.skewMode = false;
        S.transform.perspective = false;
        S.transform.grid = null;
        S.transform.dragGridPt = null;
        S.transform.warp = false;
        S.transform.warpGrid = null;
        S.transform.warpOrigGrid = null;
    }

    // Regions.
    S.regions = [];
    S.activeRegionId = null;
    S.regionMode = false;
    S._nextRegionId = 1;

    // Mask.
    if (S.mask && S.mask.ctx) S.mask.ctx.clearRect(0, 0, S.W, S.H);
    S.editingMask = S.tool === "mask";
    S._userMaskMode = false;

    // Stroke buffers.
    if (S.stroke && S.stroke.ctx) S.stroke.ctx.clearRect(0, 0, S.W, S.H);
    if (S.stroke) {
        S.stroke.points = [];
        S.stroke.stampPoints = [];
        S.stroke.alphaMap = null;
        S.stroke.accum = null;
        S.stroke._accumFor = null;
        S.stroke.dirty = { x0: 0, y0: 0, x1: 0, y1: 0 };
        S.stroke._cachedImg = null;
    }

    // Clone source / smudge buffer.
    S._cloneSource = null;
    S._cloneOffset = null;
    S.smudgeBuffer = null;

    // Live preview — if the engine kept any committed pixels around.
    // Self-heal if another path corrupted the shape (e.g. wrote a boolean).
    if (!S.livePreview || typeof S.livePreview !== "object") {
        S.livePreview = { canvas: null, ctx: null, active: false };
    } else {
        S.livePreview.active = false;
        if (S.livePreview.canvas && S.livePreview.ctx) {
            S.livePreview.ctx.clearRect(0, 0, S.livePreview.canvas.width, S.livePreview.canvas.height);
        }
    }

    S.drawing = false;
    S._canvasDirty = false;

    // Rebuild the layer stack to the boot baseline (one white
    // reference background + one empty paint layer; paint is active).
    S.layers.length = 0;
    const refLayer = makeLayer("Background", "reference");
    refLayer.ctx.fillStyle = "#fff";
    refLayer.ctx.fillRect(0, 0, S.W, S.H);
    S.layers.push(refLayer);
    S.layers.push(makeLayer("Layer 1", "paint"));
    S.activeLayerIdx = 1;

    // Invalidate the composite + develop caches so the next composite
    // recomputes from the fresh layer stack instead of serving the
    // pre-reset buffer (canvas-core's _compBufCache and _devBufCache
    // are both keyed on _compositeVersion).
    markCompositeDirty();
}

// ========================================================================
// BOOT
// ========================================================================
function boot(canvasElement) {
    if (S.ready) return;
    S.canvas = canvasElement;
    // Display canvas is explicitly sRGB. We tried `display-p3` here on the
    // theory that the browser would do sRGB→P3 on the final blit and give
    // wide-gamut monitors a richer rendering. In practice, on Firefox mode-2
    // calibrated setups the browser re-tagged the canvas buffer as P3 without
    // converting sRGB values, so sRGB pixel values rendered through P3
    // primaries and the canvas no longer matched the (correctly sRGB-tagged)
    // export. Stay sRGB and let the OS color manager handle sRGB→display via
    // the monitor ICC, which works correctly on both standard sRGB and
    // wide-gamut displays.
    S.ctx = S.canvas.getContext("2d", { colorSpace: "srgb" });
    S.canvas.width = 800; S.canvas.height = 600;

    // Initial layers
    const refLayer = makeLayer("Background", "reference");
    refLayer.ctx.fillStyle = "#fff"; refLayer.ctx.fillRect(0, 0, S.W, S.H);
    S.layers.push(refLayer);
    S.layers.push(makeLayer("Layer 1", "paint"));
    S.activeLayerIdx = 1;

    // Mask + stroke buffers
    S.mask.canvas = createLayerCanvas(); S.mask.ctx = S.mask.canvas.getContext("2d", { colorSpace: "srgb" });
    S.stroke.canvas = _createCanvas(S.W, S.H); S.stroke.ctx = S.stroke.canvas.getContext("2d", { colorSpace: "srgb" });

    S.ready = true;
    console.log("[StudioCore] Ready", S.W + "x" + S.H, "| Canvas 2D");
}

// ========================================================================
// APPLY MODE
// ========================================================================
function applyMode(mode) {
    if (_strokeTransaction) abortStroke();
    S.studioMode = mode;
    if (!S.inpaintMode) S.inpaintMode = "Inpaint";
    const isSketch = mode === "Create", isInpaint = mode === "Edit";
    const ipModeVal = S.inpaintMode || "Inpaint";
    const isIPRegional = isInpaint && ipModeVal === "Regional";

    S.editingMask = S.tool === "mask";
    if (isIPRegional) {
        if (S.tool === "mask" && window.StudioUI) window.StudioUI.setTool(S._maskReturnTool || "brush");
        S.editingMask = false; S.regionMode = true;
        if (!S.regions.length) addRegion("Region " + S._nextRegionId);
    } else if (isInpaint || !isSketch || !S.regions.length) {
        S.regionMode = false;
    }
}

// ========================================================================
// PUBLIC API — window.StudioCore
// ========================================================================
window.StudioCore = {
    // State access
    get state() { return S; },

    // Boot
    boot,

    // Compositor
    composite,

    // Document identity
    documentIdentity: () => {
        if (!S.documentId) _startNewDocument();
        return { document_id: S.documentId, revision: S.canvasRevision };
    },
    // Minting is published so the DOCUMENT system can give every tab its own
    // identity. Without this, two tabs shared one id and per-document crash
    // recovery could not tell them apart.
    newDocumentId: _newDocumentId,
    // Crash recovery subscribes here. `_bumpRevision` is already the single
    // point every generation-affecting mutation passes through, so recovery
    // gets a precise trigger for free and cosmetic changes never reach it.
    onRevisionChange: (fn) => {
        if (typeof fn === "function") _revisionListeners.push(fn);
    },
    // AN ACTION FINISHED. AR4.6.
    //
    // `onRevisionChange` fires on the WRONG EDGE for a brush stroke:
    // `saveUndo` runs at POINTER-DOWN, because undo has to snapshot the
    // pixels BEFORE they are painted over. Recovery hung off that and
    // survived only because a 2.5s debounce meant the stroke had long
    // finished by the time it looked. Remove the debounce and it would have
    // captured the canvas as it was before the stroke.
    //
    // This is the other edge: the moment an action is DONE and the document
    // is coherent. A stroke ends at `commitStroke`; an instantaneous action
    // -- fill, gradient, shape, delete, a layer operation, a result placed on
    // the canvas -- is already finished when its revision bumps.
    onActionComplete: (fn) => {
        if (typeof fn === "function") _actionListeners.push(fn);
    },
    restoreDocumentIdentity: (identity) => {
        // Reopening the SAME document keeps its id. Used by session restore;
        // a new document must go through `resetCanvasState` instead.
        if (!identity || !identity.document_id) return false;
        S.documentId = String(identity.document_id);
        S.canvasRevision = Math.max(0, parseInt(identity.revision, 10) || 0);
        return true;
    },

    // Export
    exportCanvas,
    exportMask,
    exportFlattened,
    getFlattenedImageData,
    serializeRegions,
    isCanvasBlank,

    // Layer flip / rotate
    flipLayerHorizontal: _flipLayerHorizontal,
    flipLayerVertical: _flipLayerVertical,
    rotateLayer90CW: _rotateLayer90CW,
    rotateLayer90CCW: _rotateLayer90CCW,
    rotateLayer180: _rotateLayer180,
    rotateLayerArbitrary: _rotateLayerArbitrary,

    // Resize
    resizeCanvas,
    resetCanvasState,

    // Mode
    applyMode,

    // Zoom/Pan
    zoomAt,
    zoomFit,
    screenToDoc,
    applyDisplayTransform,

    // Layers
    makeLayer,
    makeAdjustLayer,
    createLayerCanvas,
    activeLayer,
    findLayerIdx,
    drawTarget,
    drawColor,
    getLayerContentBounds,

    // Brush engine
    brushPx,
    pSz,
    pOp,
    beginStroke,
    plotTo,
    commitStroke, finishStroke,
    stampWet,
    // BE6 exports the capability contract so a guard can compare the
    // PROMISE against rendered pixels. A matrix nobody checks is a comment.
    TIP_CAPABILITIES,
    // Exported for BE5's continuity acceptance. The brief requires "calculated
    // spacing" to be measured across the hardness sweep alongside coverage, and
    // a test that re-derived the formula would just agree with itself.
    spacingFraction, spacingFor,
    // Exported for BE4's guards. Since selection moved to merge, the alpha map
    // is deliberately PRE-selection and a test reading it would report the
    // bound as missing when it is merely applied later. This is the seam where
    // coverage becomes what the owner actually gets, so it is the seam the
    // tests have to be able to ask.
    alphaMapToImageData,
    stab,

    // Tool algorithms
    floodFill,
    pickColor,
    drawGradient,
    drawShapePath,
    commitShape,
    smudgeInit,
    smudgeDrag,
    smudgeStroke,
    blurAt,
    pixelateAt,
    dodgeBurnAt,
    dodgeBurnStroke,
    liquifyPush,
    cloneStamp,
    regionPaintAt,
    regionPaintMove,
    regionEraseMove,

    // Color
    hexRgb,
    rgbHex,
    hsvToRgb,
    rgbToHsv,
    addColor,

    // Undo/Redo
    saveUndo,
    saveStructuralUndo,
    undo,
    redo,

    // Selection
    selectionAll,
    selectionClear,
    selectionInvert,
    selectionFill,
    selectionDelete,
    selectionToMask,
    selectionCopy,
    selectionCut,
    selectionPaste,
    selectionModify,
    featherSelection,
    fillPolygonMask,
    magicWandSelect,
    magneticEdgeMap,
    magneticPath,

    // Transform
    transformHitTest,
    HANDLE_SIZE,

    // Grid Renderer
    gridRender,
    gridDrawWireframe,
    gridFromRect,
    gridHitTest,
    mlsEvalGrid,

    // Regions
    addRegion,
    deleteRegion,
    clearRegion,
    activeRegion,

    // Live Painting
    compositeForLive,
    setLivePreview,
    clearLivePreview,
    applyLivePreview,

    // Constants
    ALL_BLEND_MODES,
    REGION_COLORS,
    DEFAULT_BRUSH_PRESETS, BRUSH_PRESET_ALIASES, applyBrushPreset,
    PAPER_LIBRARY, PAPER_TILE, DEFAULT_PAPER, paperTile, paperReveal,
    DYN_INPUTS, DYN_TARGETS, DYN_CURVES, DYN_SPEED_REF,
    applyDynamics, noteSample,
    startAirbrush, stopAirbrush, airbrushTick, airbrushInterval,
    pixelWalkActive, pixelPerfectActive, flushPixelPerfect, PP_MAX_WIDTH,
    strokeHeading, dabRotation, flushOpeningDab,
    setAirbrushClock, AIR_MAX_RATE, AIR_MIN_RATE, AIR_STILL_PX, setPivotFill,
    noteStrokeUndo, clearStrokeUndo, abortStroke, strokeTargetIsCurrent,
    hasGenerationMask, clearGenerationMask,
    _blendToPS,
    _blendFromPS,
    _adjustDefaults,

    // Internal accessors for UI layer
    get cursorPos() { return { x: _cx, y: _cy }; },
    set cursorPos(v) { _cx = v.x; _cy = v.y; },
    get strokeAngle() { return _saSmooth; },
    set strokeAngle(v) { _saSmooth = v; },
    get strokeAngleRaw() { return _sa; },

    // Callback hooks for UI layer
    set onUndoRedo(fn) { _onUndoRedo = fn; },
    set onAirbrushDeposit(fn) { _onAirbrushDeposit = fn; },

    // Temp canvas utility (for UI overlays that need scratch space)
    getTempCanvas,

    // Adjustment rendering + helpers (for editor UI in canvas-ui.js)
    _applyAdjustment,
    _migrateAdjustParams,
    _compositeLayersBelow,

    // Develop pipeline hook (algorithm in develop.js)
    _applyDevelop,

    // Composite cache invalidation — call from any code path that mutates
    // layers, develop params, regions, masks, or canvas dims. Cursor moves
    // do NOT bump this, so they hit the cache and skip the pipeline.
    markCompositeDirty,
    getCompositeVersion,
    // U2. The Canvas-owned presentation-dirty contract. `markCompositeDirty`
    // stays the safe default (full); only a caller that can name its region
    // uses `markCompositeDirtyRegion`.
    markCompositeDirtyRegion,
    takePresentationDirty,
    acknowledgePresentation,
    invalidatePresentation,
    canPresentRegion,
    getFlattenedRegionImageData,
};

console.log("[StudioCore] Module loaded — Phase 1 clean engine");

})();
