/**
 * The Legacy adapter: the inherited canvas-core.js behind the neutral surface.
 *
 * THIS FILE IS ALLOWED TO KNOW ABOUT LEGACY. Nothing else in the oracle is.
 * Everything Legacy-specific lives here on purpose -- preset names, `S.stroke`,
 * `plotTo`, `alphaMapToImageData`, the airbrush clock -- so that a V2 adapter
 * can be written beside it without inheriting any of it.
 *
 * WHAT "READ COVERAGE" MEANS HERE, and it is the one thing worth getting right:
 * the engine's own `alphaMapToImageData` followed by the single commit-time
 * multiply. Not `S.stroke.alphaMap`. Half the wrong numbers in this programme
 * came from reporting the accumulator as what the owner sees, and an oracle
 * that did the same would certify the mistake it was built to prevent.
 */

"use strict";

const fs = require("fs");
const vm = require("vm");

/** Neutral brush ids, and the Legacy preset each one resolves to. */
const BRUSHES = [
    { id: "round-basic", label: "Basic Round", preset: "Basic Round", tip: "round" },
    { id: "round-soft", label: "Soft Round", preset: "Soft Round", tip: "round" },
    { id: "round-hard", label: "Hard Ink", preset: "Hard Ink", tip: "round" },
    { id: "pencil", label: "Pencil", preset: "Pencil", tip: "round" },
    { id: "flat", label: "Flat Chisel", preset: "Flat Chisel", tip: "flat" },
    { id: "marker", label: "Marker", preset: "Marker", tip: "marker" },
    { id: "airbrush", label: "Airbrush", preset: "Airbrush", tip: "round" },
    { id: "pixel", label: "Pixel Perfect", preset: "Pixel Perfect", tip: "round" },
];

/**
 * One neutral Sample as the normalised pointer sample `noteSample` expects.
 *
 * `canvas-input.js:102 normalize` is the shipping producer, and the two fields
 * that matter here are AVAILABILITY flags rather than values -- `_dynInput`
 * reads them and returns null when a value was not measured, so a rule falls
 * back to its own declared default instead of acting on a substitute
 * (`canvas-core.js:1229-1236`).
 *
 * PRESSURE DEFAULTS TO AVAILABLE, and the default is a claim: a fixture that
 * states a pressure per sample is a recording of a PEN, which is exactly the
 * case `pressureIsMeasured` answers true for (`canvas-input.js:64-65`). A
 * fixture that wants to be a mouse says `pressureAvailable: false` and gets a
 * stroke drawn the way every mouse stroke is drawn.
 *
 * TILT DEFAULTS TO UNAVAILABLE because no fixture records it, and reporting a
 * flat tilt as measured would tell a tilt dynamic it had data.
 */
function penSample(s) {
    return {
        x: s.x,
        y: s.y,
        pressure: s.pressure,
        pressureAvailable: s.pressureAvailable !== undefined
            ? !!s.pressureAvailable : true,
        tiltAvailable: !!s.tiltAvailable,
        tiltX: s.tiltX || 0,
        tiltY: s.tiltY || 0,
        // The speed input exists only when a timestamp arrives, and every
        // fixture carries one in milliseconds from pen-down.
        time: typeof s.t === "number" ? s.t : null,
    };
}

const NOOP_CTX = {
    clearRect() {}, drawImage() {}, putImageData() {}, save() {}, restore() {},
    fillRect() {}, getImageData() { return { data: new Uint8ClampedArray(4) }; },
};

function makeLegacyAdapter(corePath) {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    g.ImageData = class ImageData {
        constructor(w, h) {
            this.width = w; this.height = h;
            this.data = new Uint8ClampedArray(w * h * 4);
        }
    };
    // DETERMINISM IS A PRECONDITION, not a nicety: a contract that compared two
    // runs of a scattering preset would report noise as a finding.
    let seed = 1;
    g.Math = Object.create(Math);
    g.Math.random = function () {
        seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
        let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    vm.createContext(g);
    const src = fs.readFileSync(corePath, "utf8");
    vm.runInContext(src, g, { filename: corePath });

    const C = g.window.StudioCore;
    const S = C.state;

    // The airbrush's clock, so `advanceTime` is wall time the harness controls
    // rather than whatever the machine's mood was.
    //
    // OPTIONAL, AND ABSENCE IS REPORTED RATHER THAN THROWN. Engines older than
    // BE12 have no injectable clock, and a V2 adapter may not have one on the
    // day it is first written. A harness that crashed on a missing optional
    // capability could not be used to compare engines, which is its entire job.
    let clock = 0;
    const canAdvanceTime = typeof C.setAirbrushClock === "function"
        && typeof C.startAirbrush === "function"
        && typeof C.airbrushInterval === "function";
    if (canAdvanceTime) C.setAirbrushClock(() => clock);

    /** Which commit-time bound this build uses. Read off the source, not assumed. */
    const buildupCommitsAtOne = /S\.brushBuildup \? 1 : S\.brushOpacity/.test(src);

    let target = null;

    function reset(width, height) {
        seed = 1;
        clock = 0;
        if (typeof C.stopAirbrush === "function") C.stopAirbrush();
        S.W = width; S.H = height;
        S.tool = "brush";
        S.layers = [{
            id: "t", name: "t", visible: true, opacity: 1,
            blendMode: "source-over",
            canvas: { width, height }, ctx: NOOP_CTX,
        }];
        S.activeLayerIdx = 0;
        S.stroke = {
            alphaMap: new Uint8Array(width * height), accum: null, _accumFor: null,
            dirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
            frameDirty: { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 },
            _taperK: 1, lx: 0, ly: 0, lp: 1, points: [],
            ctx: NOOP_CTX, canvas: { width, height }, _cachedImg: null,
        };
        S.selection = { active: false, mask: null, rect: null, dragging: false };
        S.editingMask = false;
        S.symmetry = "none";
        S.pressureSensitivity = false;
        S.zoom = { scale: 1, ox: 0, oy: 0 };
        S.paper = { texture: "none", scale: 1, depth: 0 };
        S.undoStack = []; S.redoStack = [];
        S.drawing = true;
    }

    /** The airbrush timer, driven by the harness instead of by setInterval. */
    let airQueued = null;
    function armAirbrush() {
        if (!canAdvanceTime) return;
        C.stopAirbrush();
        airQueued = null;
        C.startAirbrush(fn => { airQueued = fn; return () => { airQueued = null; }; });
    }

    return {
        name: "legacy",

        capabilities() {
            const caps = C.TIP_CAPABILITIES || {};
            return {
                canAdvanceTime,
                brushes: BRUSHES.map(b => ({
                    id: b.id,
                    label: b.label,
                    tip: b.tip,
                    // Declared support, copied verbatim. The honesty contract
                    // compares this against rendered output, so an engine that
                    // over-declares fails rather than passes.
                    supports: Object.assign({}, caps[b.tip] || {}),
                })),
            };
        },

        createTarget(opts) {
            const width = opts.width, height = opts.height;
            reset(width, height);
            target = { width, height, acceptedSamples: 0, brush: null };
            return target;
        },

        selectBrush(t, spec) {
            const entry = BRUSHES.find(b => b.id === spec.id);
            if (!entry) throw new Error(`unknown brush id: ${spec.id}`);
            C.applyBrushPreset(entry.preset);
            // The stabiliser is not the subject of any contract here; every
            // fixture supplies its own geometry and expects it back.
            S.smoothing = spec.smoothing !== undefined ? spec.smoothing : 0;
            S.brushDynamics = Object.assign({}, S.brushDynamics, {
                sizeJitter: 0, opacityJitter: 0, scatter: 0, rotationJitter: 0,
            });
            // `sizeMode` FIRST, because `brushSize` means a different thing in
            // each mode. `brushPx()` reads the RELATIVE curve by default --
            // `(v^1.5 / 10) / 100 * shortSide` -- so on a small target every
            // small `sizePx` collapses to the 1 px floor and an engine
            // comparison silently measures the floor against itself. U3-R2F F1
            // read "Legacy paints 1 px at 255" for sizes 1, 2, 3 AND 4 before
            // this line existed. Absent, the behaviour is exactly as before.
            if (spec.sizeMode !== undefined) S.brushSizeMode = spec.sizeMode;
            if (spec.sizePx !== undefined) S.brushSize = spec.sizePx;
            if (spec.flow !== undefined) S.brushFlow = spec.flow;
            if (spec.opacity !== undefined) S.brushOpacity = spec.opacity;
            if (spec.hardness !== undefined) S.brushHardness = spec.hardness;
            if (spec.buildup !== undefined) S.brushBuildup = spec.buildup;
            if (spec.spacing !== undefined) {
                S.brushDynamics = Object.assign({}, S.brushDynamics,
                    { spacing: spec.spacing });
            }
            if (spec.angleDeg !== undefined) S.brushAngle = spec.angleDeg;
            if (spec.spikes !== undefined) S.brushSpikes = spec.spikes;
            if (spec.ratio !== undefined) S.brushRatio = spec.ratio;
            if (spec.followStroke !== undefined) {
                S.brushDynamics = Object.assign({}, S.brushDynamics,
                    { followStroke: spec.followStroke });
            }
            if (spec.rotationJitter !== undefined) {
                S.brushDynamics = Object.assign({}, S.brushDynamics,
                    { rotationJitter: spec.rotationJitter });
            }
            t.brush = spec;
        },

        beginStroke(t, s) {
            S.drawing = true;
            C.beginStroke(s.x, s.y, s.pressure);
            if (typeof C.stopAirbrush === "function") C.stopAirbrush();
            armAirbrush();
            clock = s.t || 0;
            t.acceptedSamples = 1;
        },

        addSample(t, s) {
            clock = s.t;
            // THE SHIPPING INPUT PATH, in the shipping order.
            //
            // This used to be `C.plotTo(s.x, s.y, s.pressure)` alone, and that
            // one line made every number in this oracle a measurement of a
            // path the owner never takes. `canvas-ui.js:1605-1608` runs
            // `noteSample` -> `stab` -> `plotTo` for every sample of every
            // stroke, and the two calls that were missing are not incidental:
            //
            //   `stab` IS the stabiliser (`canvas-core.js:2679`). Without it
            //   the "stabilised pass" in the endpoint contract was not
            //   stabilised, so a mutation that made a stabilised stroke end
            //   short could not be detected -- there was no lag to leave
            //   behind. `stab` also PUSHES to `S.stroke.points`, which the
            //   direct path left empty for the whole stroke.
            //
            //   `noteSample` carries the pen state and the timestamp
            //   (`canvas-core.js:1213`), and they reach the engine here and
            //   nowhere else. Without it `_dynInput` reports pressure AND
            //   speed unavailable on every sample, so every pressure or speed
            //   dynamic silently fell back to its declared default -- the
            //   engine was judged with two of its four dynamic inputs dark.
            //
            // BE3's own comment predicted this: "its own tests call `plotTo`
            // directly and bypass the stabiliser entirely, so the defect was
            // invisible to them", and the defect it was hiding was LARGER than
            // the one BE3 fixed.
            //
            // Pen down is NOT given a `noteSample`, and that is correct rather
            // than an omission: `canvas-ui.js:1301` calls `beginStroke` with
            // raw coordinates and no sample, and `beginStroke` resets `_pen`.
            // The opening dab has no pen state on a real install either.
            if (C.noteSample) C.noteSample(penSample(s));
            const sp = (typeof C.stab === "function")
                ? C.stab(s.x, s.y, s.pressure)
                : { x: s.x, y: s.y, p: s.pressure };
            C.plotTo(sp.x, sp.y, sp.p);
            t.acceptedSamples += 1;
        },

        advanceTime(t, ms) {
            if (!canAdvanceTime) return 0;
            // ONE TICK PER SCHEDULED INTERVAL, not one per millisecond: the
            // engine decides its own rate from Flow, and a harness that fired
            // continuously would be measuring itself.
            const step = Math.max(1, C.airbrushInterval());
            let fired = 0;
            const until = clock + ms;
            while (clock + step <= until) {
                clock += step;
                if (airQueued) { airQueued(); fired += 1; }
            }
            clock = until;
            return fired;
        },

        endStroke(t, s) {
            // `finishStroke` is BE9's. An engine without it ends where its last
            // sample landed, which the endpoint contract then judges honestly
            // rather than the harness papering over.
            if (s && typeof C.finishStroke === "function") {
                C.finishStroke(s.x, s.y, s.pressure);
            }
            if (typeof C.stopAirbrush === "function") C.stopAirbrush();
            airQueued = null;
        },

        cancelStroke(t) {
            if (typeof C.stopAirbrush === "function") C.stopAirbrush();
            airQueued = null;
            if (C.abortStroke) C.abortStroke();
        },

        readCoverage(t) {
            if (!S.stroke.alphaMap) return new Uint8Array(t.width * t.height);
            const img = C.alphaMapToImageData("#000000",
                { x0: 0, y0: 0, x1: t.width - 1, y1: t.height - 1 });
            const bound = (buildupCommitsAtOne && S.brushBuildup)
                ? 1 : S.brushOpacity;
            const out = new Uint8Array(t.width * t.height);
            for (let i = 0; i < out.length; i++) {
                out[i] = Math.round(img.data[i * 4 + 3] * bound);
            }
            return out;
        },

        diagnostics(t) {
            const cov = this.readCoverage(t);
            let changed = 0, total = 0;
            let x0 = t.width, y0 = t.height, x1 = -1, y1 = -1;
            for (let i = 0; i < cov.length; i++) {
                if (!cov[i]) continue;
                changed += 1;
                total += cov[i];
                const x = i % t.width, y = (i / t.width) | 0;
                if (x < x0) x0 = x;
                if (y < y0) y0 = y;
                if (x > x1) x1 = x;
                if (y > y1) y1 = y;
            }
            return {
                acceptedSamples: t.acceptedSamples,
                changedPixels: changed,
                totalCoverage: total,
                bounds: x1 < 0 ? null : { x0, y0, x1, y1 },
            };
        },
    };
}

module.exports = { makeLegacyAdapter, BRUSHES };
