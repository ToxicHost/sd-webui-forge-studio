/**
 * U3 probe: the V2 → Canvas raster-layer adapter, executed.
 *
 * THE ADAPTER IS TESTABLE HEADLESSLY AND THE FIRST DRAFT OF THIS UNIT WAS NOT.
 * Everything the adapter touches on the Canvas side is plain state -- document
 * dimensions, a layer list, an alpha map, two dirty rectangles -- so a stub is
 * enough and no 2D context is needed. `L.canvas`/`L.ctx` only have to be
 * truthy, because the adapter never draws: it produces coverage and the Canvas
 * owns every pixel operation.
 *
 * That matters more than it sounds. The first version of `addFromEvent` treated
 * `StrokeFilter.push` as returning a LIST when it returns one sample, so the
 * loop iterated the sample's own keys and placed nothing. In a browser it
 * looked almost right -- Legacy's opening dab still marked the canvas -- and it
 * took a counter reading `samples: 3, marks: 0` to see it. This probe fails on
 * that in milliseconds.
 *
 *     node tests/studio_alpha/v2_u3_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const V2 = path.resolve(__dirname, "..", "..", "forge_studio", "frontend", "v2");
const MODULES = ["brush-contracts.js", "input.js", "sampler.js", "filters.js",
                 "dynamics.js", "coverage.js", "canvas-adapter.js"];

function load() {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    vm.createContext(g);
    for (const name of MODULES) {
        const file = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(file, "utf8"), g,
                        { filename: file });
    }
    return g.window;
}

const W = load();
const A = W.StudioBrushV2Adapter;

// ─────────────────────────────────────────────────────────── the Canvas stub

function makeState(w, h, opts) {
    const o = opts || {};
    const layers = [];
    for (let i = 0; i < (o.layers === undefined ? 2 : o.layers); i++) {
        layers.push({ type: "paint", canvas: { width: w, height: h },
                      ctx: {}, visible: true, locked: false, opacity: 1 });
    }
    return {
        W: w, H: h,
        layers: layers,
        activeLayerIdx: o.activeLayerIdx === undefined ? 1 : o.activeLayerIdx,
        tool: o.tool || "brush",
        editingMask: !!o.editingMask,
        regionMode: !!o.regionMode,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSize: 18, brushOpacity: 1, brushHardness: 0.5, brushFlow: 1,
        brushBuildup: false, brushSpacing: 0.15,
        pressureSensitivity: false,
        stroke: {
            alphaMap: new Uint8Array(w * h),
            dirty: { x0: w, y0: h, x1: 0, y1: 0 },
            frameDirty: { x0: w, y0: h, x1: 0, y1: 0 },
        },
    };
}

const CORE = { brushPx: function () { return 40; } };
const toDoc = (x, y) => ({ x: x, y: y });

function event(x, y, t, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t, pressure: 0.8,
        pointerType: "pen", tiltX: 0, tiltY: 0, twist: 0,
        buttons: 1, isPrimary: true,
    }, over || {});
}

/** One event carrying several coalesced samples, as a fast stroke produces. */
function coalescedEvent(points, t0, over) {
    const last = points[points.length - 1];
    const e = event(last[0], last[1], t0 + points.length * 8, over);
    e.getCoalescedEvents = function () {
        return points.map((p, i) => event(p[0], p[1], t0 + i * 8, over));
    };
    return e;
}

function paintedPixels(state) {
    let n = 0;
    for (let i = 0; i < state.stroke.alphaMap.length; i++) {
        if (state.stroke.alphaMap[i]) n += 1;
    }
    return n;
}

function coverageSignature(state) {
    let h = 2166136261 >>> 0;
    const m = state.stroke.alphaMap;
    for (let i = 0; i < m.length; i++) {
        if (!m[i]) continue;
        h ^= i & 255; h = Math.imul(h, 16777619) >>> 0;
        h ^= m[i]; h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
}

const out = {};

// ── 1. The flag is off by default and refuses ───────────────────────────────

out.flagDefault = {
    enabledAtLoad: A.isEnabled(),
    refusal: A.refusalFor(makeState(256, 256), event(10, 10, 0)),
};

// ── 2. Flag on: V2 produces coverage into the Canvas's own buffer ───────────

A._setEnabled(true);
{
    const S = makeState(256, 256);
    A.resetStats();
    const refusal = A.begin(S, CORE, event(40, 40, 0), toDoc);
    const moved = [];
    const path = [[40, 40], [80, 60], [120, 90], [160, 130]];
    for (let i = 1; i < path.length; i++) {
        moved.push(A.addFromEvent(S, event(path[i][0], path[i][1], i * 16), toDoc));
    }
    const summary = A.finish(S, event(160, 130, 64), toDoc);
    out.paints = {
        refusal: refusal,
        movedPerEvent: moved,
        summary: summary,
        painted: paintedPixels(S),
        dirty: Object.assign({}, S.stroke.dirty),
        stats: A.stats(),
        adapterIdleAfterFinish: !A.isActive(),
    };
}

// ── 3. The dirty conversion: half-open in, INCLUSIVE out ────────────────────
//
// The rectangle Legacy is handed must contain every painted pixel and no more.

{
    const S = makeState(256, 256);
    A.begin(S, CORE, event(60, 60, 0), toDoc);
    A.addFromEvent(S, event(100, 90, 16), toDoc);
    A.finish(S, event(100, 90, 32), toDoc);
    const d = S.stroke.dirty;
    let minX = 1e9, minY = 1e9, maxX = -1, maxY = -1;
    for (let y = 0; y < 256; y++) {
        for (let x = 0; x < 256; x++) {
            if (S.stroke.alphaMap[y * 256 + x]) {
                if (x < minX) minX = x;
                if (y < minY) minY = y;
                if (x > maxX) maxX = x;
                if (y > maxY) maxY = y;
            }
        }
    }
    out.dirtyConversion = {
        reported: { x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1 },
        actualInclusive: { x0: minX, y0: minY, x1: maxX, y1: maxY },
        containsEveryPaintedPixel:
            d.x0 <= minX && d.y0 <= minY && d.x1 >= maxX && d.y1 >= maxY,
        // Inclusive means the last painted column IS x1, not one past it.
        isExact: d.x0 === minX && d.y0 === minY && d.x1 === maxX && d.y1 === maxY,
    };
}

// ── 4. Dispatch grouping is invisible (§19.3) ───────────────────────────────

function strokeGrouped(perEvent) {
    const S = makeState(256, 256);
    const pts = [];
    for (let i = 0; i <= 24; i++) pts.push([40 + i * 6, 40 + i * 4]);
    A.begin(S, CORE, event(pts[0][0], pts[0][1], 0), toDoc);
    let t = 16;
    for (let i = 1; i < pts.length; i += perEvent) {
        const group = pts.slice(i, i + perEvent);
        if (!group.length) break;
        A.addFromEvent(S, coalescedEvent(group, t), toDoc);
        t += 8 * group.length;
    }
    const last = pts[pts.length - 1];
    A.finish(S, event(last[0], last[1], t), toDoc);
    return S;
}
{
    const one = strokeGrouped(1), three = strokeGrouped(3), all = strokeGrouped(99);
    out.grouping = {
        painted: [paintedPixels(one), paintedPixels(three), paintedPixels(all)],
        signatures: [coverageSignature(one), coverageSignature(three),
                     coverageSignature(all)],
        allEqual: coverageSignature(one) === coverageSignature(three)
               && coverageSignature(three) === coverageSignature(all),
        nonTrivial: paintedPixels(one) > 500,
    };
}

// ── 5. Settings are frozen at begin (§19.9) ─────────────────────────────────

{
    const S = makeState(256, 256);
    A.begin(S, CORE, event(40, 40, 0), toDoc);
    const frozen = A.describeStroke(S, CORE);
    S.brushSize = 90;                      // the owner drags a slider mid-stroke
    S.brushHardness = 1.0;
    S.brushOpacity = 0.1;
    A.addFromEvent(S, event(120, 100, 16), toDoc);
    A.finish(S, event(120, 100, 32), toDoc);
    const same = makeState(256, 256);
    A.begin(same, CORE, event(40, 40, 0), toDoc);
    A.addFromEvent(same, event(120, 100, 16), toDoc);
    A.finish(same, event(120, 100, 32), toDoc);
    out.frozenSettings = {
        frozenSize: frozen.sizePx,
        signatureWithMidStrokeChange: coverageSignature(S),
        signatureUntouched: coverageSignature(same),
        unaffected: coverageSignature(S) === coverageSignature(same),
    };
}

// ── 6. The target cannot be redirected mid-contact (§19.10) ─────────────────

{
    const S = makeState(256, 256, { layers: 3, activeLayerIdx: 1 });
    A.begin(S, CORE, event(40, 40, 0), toDoc);
    const frozenTarget = 1;
    S.activeLayerIdx = 2;                  // the owner clicks another layer
    A.addFromEvent(S, event(120, 100, 16), toDoc);
    A.finish(S, event(120, 100, 32), toDoc);
    out.stableTarget = {
        activeAtEnd: S.activeLayerIdx,
        frozenTarget: frozenTarget,
        // The adapter writes to the SHARED stroke buffer; the Canvas resolves
        // the layer from its own locked `_commitTarget`. What must hold here is
        // that the adapter never re-resolved and never refused mid-stroke.
        stillPainted: paintedPixels(S) > 100,
        noRefusalDuringStroke: A.stats().lastRefusal === null,
    };
}

// ── 7. Refusals (§19.11, §19.19) ────────────────────────────────────────────

function refusalWith(mutate, ev) {
    const S = makeState(256, 256);
    mutate(S);
    return A.refusalFor(S, ev || event(10, 10, 0));
}
out.refusals = {
    hidden: refusalWith(S => { S.layers[1].visible = false; }),
    locked: refusalWith(S => { S.layers[1].locked = true; }),
    adjustment: refusalWith(S => { S.layers[1].type = "adjustment"; }),
    noLayer: refusalWith(S => { S.activeLayerIdx = 9; }),
    maskMode: refusalWith(S => { S.editingMask = true; }),
    regionMode: refusalWith(S => { S.regionMode = true; }),
    wrongTool: refusalWith(S => { S.tool = "smudge"; }),
    touch: refusalWith(() => {}, event(10, 10, 0, { pointerType: "touch" })),
    eraserAccepted: refusalWith(S => { S.tool = "eraser"; }),
};
A._setEnabled(false);
out.refusals.flagOff = refusalWith(() => {});
A._setEnabled(true);

// ── 8. Cancel drops the transient contact ───────────────────────────────────

{
    const S = makeState(256, 256);
    A.begin(S, CORE, event(40, 40, 0), toDoc);
    A.addFromEvent(S, event(90, 70, 16), toDoc);
    const duringActive = A.isActive();
    A.cancel();
    out.cancel = {
        activeDuring: duringActive,
        activeAfter: A.isActive(),
        furtherSamplesIgnored: A.addFromEvent(S, event(140, 110, 32), toDoc),
    };
}

// ── 8b. V2 takes SOLE ownership of the stroke buffer ────────────────────────
//
// Legacy leaves coverage behind when `beginStroke` runs: either a dab already
// stamped into the alpha map, or a DEFERRED one in `_openingDab` that
// `commitStroke` flushes at the very end. Two producers writing one buffer
// gives a V2 stroke with a Legacy dab welded to its start, and the deferred
// case lands AFTER every V2 mark, so it survives the transfer.
//
// Asserting that the clearing CODE exists is not enough -- a mutation that
// removed only the CALL sailed past exactly that guard. This drives it.

{
    const S = makeState(256, 256);
    // Stand in for what `beginStroke` leaves behind.
    for (let y = 200; y < 210; y++) {
        for (let x = 200; x < 210; x++) S.stroke.alphaMap[y * 256 + x] = 255;
    }
    S.stroke.dirty = { x0: 200, y0: 200, x1: 209, y1: 209 };
    S.stroke._openingDab = { x: 205, y: 205, p: 0.8, taperK: 1, mask: false };
    S.stroke._ppPrev = { x: 205, y: 205 };

    A.begin(S, CORE, event(40, 40, 0), toDoc);

    let legacyResidue = 0;
    for (let y = 200; y < 210; y++) {
        for (let x = 200; x < 210; x++) {
            if (S.stroke.alphaMap[y * 256 + x]) legacyResidue += 1;
        }
    }
    A.cancel();
    out.soleOwnership = {
        legacyResiduePixels: legacyResidue,
        openingDabCleared: S.stroke._openingDab === null,
        pixelPerfectCleared: S.stroke._ppPrev === null,
        clean: legacyResidue === 0 && S.stroke._openingDab === null
            && S.stroke._ppPrev === null,
    };
}

// ── 9. Per-event work does not grow with stroke history (§19.16) ────────────

{
    const S = makeState(512, 512);
    A.begin(S, CORE, event(20, 20, 0), toDoc);
    const moved = [];
    for (let i = 1; i <= 40; i++) {
        moved.push(A.addFromEvent(S, event(20 + i * 11, 20 + i * 9, i * 16), toDoc));
    }
    A.finish(S, event(460, 380, 700), toDoc);
    const firstTen = moved.slice(0, 10).reduce((a, b) => a + b, 0) / 10;
    const lastTen = moved.slice(-10).reduce((a, b) => a + b, 0) / 10;
    out.noHistoryGrowth = {
        perEventTransferred: moved,
        firstTenMean: +firstTen.toFixed(1),
        lastTenMean: +lastTen.toFixed(1),
        growthRatio: +(lastTen / Math.max(1, firstTen)).toFixed(2),
        documentPixels: 512 * 512,
        // Each transfer must be a small fraction of the document, and must not
        // climb as the stroke's accumulated box grows.
        largestTransfer: Math.max.apply(null, moved),
        largestShareOfDocument:
            +(100 * Math.max.apply(null, moved) / (512 * 512)).toFixed(3) + "%",
    };
}

A._setEnabled(false);
out.finalFlagState = A.isEnabled();

process.stdout.write(JSON.stringify(out, null, 1));
