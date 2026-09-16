/**
 * U3-R R2 probe: the pressure-target contract, through the REAL adapter.
 *
 * WHAT WAS WRONG. `describeStroke` read only `S.pressureSensitivity` and mapped
 * it to width, ignoring `S.pressureAffects` entirely. So an owner who chose
 * `opacity` got WIDTH -- the one case where a setting does the wrong thing
 * rather than nothing. Two of the four documented combinations did not exist.
 *
 * WHAT `opacity` MEANS HERE, established from source rather than guessed.
 * Legacy's `pOp` scales `S.brushFlow`, and its own comment says why: the brush
 * composites its whole stroke at `S.brushOpacity` once at commit, so the
 * per-stamp value is FLOW, an accumulation rate, and using opacity per stamp
 * would apply it twice. V2's `merge` applies opacity exactly once for the same
 * reason, so pressure-to-opacity maps to the per-mark deposition amplitude.
 *
 * WHY WIDTH AND OPACITY ARE MEASURED SEPARATELY. A stroke that got wider would
 * also lay more total paint, so a total-ink measurement cannot tell the two
 * apart -- which is precisely how "opacity secretly uses width" would hide.
 * Width is the painted extent perpendicular to travel; opacity is the alpha ON
 * the centreline, where the tip is always at full coverage.
 *
 *     node tests/studio_alpha/u3r_pressure_probe.js
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

const DOC_W = 900, DOC_H = 240, MID_Y = 120, SIZE_PX = 54;

function makeState(o) {
    o = o || {};
    const layers = [];
    for (let i = 0; i < 2; i++) {
        layers.push({ type: "paint", canvas: { width: DOC_W, height: DOC_H },
                      ctx: {}, visible: true, locked: false, opacity: 1 });
    }
    return {
        W: DOC_W, H: DOC_H, layers, activeLayerIdx: 1,
        tool: o.tool || "brush",
        editingMask: false, regionMode: false,
        selection: { active: false, mask: null, rect: null, dragging: false },
        brushSize: 18, brushOpacity: 1,
        brushHardness: o.hardness === undefined ? 1 : o.hardness,
        brushFlow: o.flow === undefined ? 1 : o.flow,
        brushBuildup: false,
        pressureSensitivity: !!o.pressureSensitivity,
        pressureAffects: o.pressureAffects,
        stroke: {
            alphaMap: new Uint8Array(DOC_W * DOC_H),
            dirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
            frameDirty: { x0: DOC_W, y0: DOC_H, x1: 0, y1: 0 },
        },
    };
}

const CORE = { brushPx: () => SIZE_PX };
const toDoc = (x, y) => ({ x, y });

function event(x, y, t, pressure, over) {
    return Object.assign({
        clientX: x, clientY: y, timeStamp: t,
        pressure: pressure === undefined ? 0.8 : pressure,
        pointerType: "pen", tiltX: 0, tiltY: 0, twist: 0,
        buttons: 1, isPrimary: true,
    }, over || {});
}

function coalesced(points, t0) {
    const last = points[points.length - 1];
    const e = event(last[0], last[1], t0 + points.length * 8, last[2]);
    e.getCoalescedEvents = () =>
        points.map((p, i) => event(p[0], p[1], t0 + i * 8, p[2]));
    return e;
}

/**
 * A straight stroke with a low->high->low pressure ramp, measured at stations
 * along it. Width and centreline alpha are read INDEPENDENTLY.
 */
function ramp(opts) {
    const o = opts || {};
    A._setEnabled(true);
    const S = makeState(o);
    const x0 = 120, x1 = 780, n = 60;
    const pts = [];
    for (let i = 0; i <= n; i++) {
        const t = i / n;
        const p = o.constantPressure !== undefined
            ? o.constantPressure : 0.05 + 0.95 * Math.sin(t * Math.PI);
        pts.push([x0 + (x1 - x0) * t, MID_Y, p]);
    }
    A.begin(S, CORE, event(pts[0][0], pts[0][1], 0, pts[0][2]), toDoc);
    let t = 16;
    const per = o.perEvent || 1;
    for (let i = 1; i < pts.length; i += per) {
        const group = pts.slice(i, Math.min(pts.length, i + per));
        if (group.length === 1) {
            A.addFromEvent(S, event(group[0][0], group[0][1], t, group[0][2]), toDoc);
        } else {
            A.addFromEvent(S, coalesced(group, t), toDoc);
        }
        t += 16;
        if (o.mutateMidStroke && i === 20) {
            // §18: changing the setting mid-contact must not alter this stroke.
            S.pressureSensitivity = !S.pressureSensitivity;
            S.pressureAffects = "both";
            S.brushSize = 4;
        }
    }
    const last = pts[pts.length - 1];
    const summary = A.finish(S, event(last[0], last[1], t, last[2]), toDoc);

    const map = S.stroke.alphaMap;
    const stations = [];
    for (let s = 0; s < 9; s++) {
        const x = Math.round(x0 + 40 + (x1 - x0 - 80) * (s / 8));
        let width = 0, top = null, bottom = null;
        for (let y = MID_Y - 45; y <= MID_Y + 45; y++) {
            if (map[y * DOC_W + x] > 8) {
                width += 1;
                if (top === null) top = y;
                bottom = y;
            }
        }
        stations.push({
            x, width,
            centreAlpha: map[MID_Y * DOC_W + x],
            symmetric: top === null ? null
                : Math.abs((MID_Y - top) - (bottom - MID_Y)) <= 1,
        });
    }
    const widths = stations.map(s => s.width);
    const alphas = stations.map(s => s.centreAlpha);
    const finite = map.every(v => v >= 0 && v <= 255);
    return {
        spec: A.describeStroke(S, CORE),
        marks: summary ? summary.marks : 0,
        stations,
        widthMin: Math.min(...widths), widthMax: Math.max(...widths),
        widthVaries: Math.max(...widths) - Math.min(...widths) > 4,
        alphaMin: Math.min(...alphas), alphaMax: Math.max(...alphas),
        alphaVaries: Math.max(...alphas) - Math.min(...alphas) > 8,
        allFinite: finite,
    };
}

const out = { modes: {} };

// ── the four documented combinations ───────────────────────────────────────
out.modes.neither = ramp({ pressureSensitivity: false, pressureAffects: "none" });
out.modes.widthOnly = ramp({ pressureSensitivity: true, pressureAffects: "size" });
out.modes.opacityOnly = ramp({ pressureSensitivity: true, pressureAffects: "opacity" });
out.modes.both = ramp({ pressureSensitivity: true, pressureAffects: "both" });

// Sensitivity off must beat any target.
out.modes.offButTargetBoth = ramp({ pressureSensitivity: false,
                                    pressureAffects: "both" });

// ── an unknown target must not become width ────────────────────────────────
out.unknownTarget = ramp({ pressureSensitivity: true, pressureAffects: "wobble" });
out.missingTarget = ramp({ pressureSensitivity: true, pressureAffects: undefined });

// ── settings are frozen at begin ───────────────────────────────────────────
(function () {
    const stable = ramp({ pressureSensitivity: true, pressureAffects: "size" });
    const mutated = ramp({ pressureSensitivity: true, pressureAffects: "size",
                           mutateMidStroke: true });
    out.frozenAtBegin = {
        widthsMatch: JSON.stringify(stable.stations.map(s => s.width))
                  === JSON.stringify(mutated.stations.map(s => s.width)),
        alphasMatch: JSON.stringify(stable.stations.map(s => s.centreAlpha))
                  === JSON.stringify(mutated.stations.map(s => s.centreAlpha)),
    };
})();

// ── grouping invariance, per mode ──────────────────────────────────────────
out.groupingInvariance = ["size", "opacity", "both"].map(target => {
    const one = ramp({ pressureSensitivity: true, pressureAffects: target,
                       perEvent: 1 });
    const many = ramp({ pressureSensitivity: true, pressureAffects: target,
                        perEvent: 4 });
    return {
        target,
        widthsMatch: JSON.stringify(one.stations.map(s => s.width))
                  === JSON.stringify(many.stations.map(s => s.width)),
        alphasMatch: JSON.stringify(one.stations.map(s => s.centreAlpha))
                  === JSON.stringify(many.stations.map(s => s.centreAlpha)),
    };
});

// ── extreme pressures stay finite and bounded ──────────────────────────────
out.extremes = [0, 0.001, 0.5, 1].map(p => {
    const r = ramp({ pressureSensitivity: true, pressureAffects: "both",
                     constantPressure: p });
    return { pressure: p, marks: r.marks, widthMin: r.widthMin,
             widthMax: r.widthMax, alphaMax: r.alphaMax,
             allFinite: r.allFinite };
});

// ── the eraser takes the same contract ─────────────────────────────────────
out.eraser = {
    widthOnly: ramp({ tool: "eraser", pressureSensitivity: true,
                      pressureAffects: "size" }),
    opacityOnly: ramp({ tool: "eraser", pressureSensitivity: true,
                        pressureAffects: "opacity" }),
};

process.stdout.write(JSON.stringify(out, null, 1));
