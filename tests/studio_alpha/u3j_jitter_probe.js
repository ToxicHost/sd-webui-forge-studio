/**
 * U3-J probe — the four per-dab jitters, through the real adapter.
 *
 * These live in the ADAPTER rather than the kernel, because they perturb where
 * and how big a dab is rather than how a dab is shaded. So this probe drives
 * `beginStroke` / `moveStroke` / `endStroke` against a fake Studio state, which
 * also exercises `describeStroke` -- the function that has now dropped fields
 * three times.
 *
 *     node tests/studio_alpha/u3j_jitter_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const V2 = path.resolve(__dirname, "..", "..", "forge_studio", "frontend", "v2");
const MODULES = ["brush-contracts.js", "input.js", "sampler.js", "filters.js",
                 "dynamics.js", "coverage.js", "canvas-adapter.js"];

const DOC = 360;

function load() {
    const g = {};
    g.window = g;
    g.console = { log() {}, warn() {}, error() {} };
    vm.createContext(g);
    for (const name of MODULES) {
        const f = path.join(V2, name);
        vm.runInContext('"use strict";' + fs.readFileSync(f, "utf8"), g, { filename: f });
    }
    return g.window;
}

const W = load();
const K = W.StudioBrushCoverageV2;
const A = W.StudioBrushV2Adapter;

/**
 * The smallest Studio the adapter will accept a contact from.
 *
 * `S.stroke.alphaMap` is the real seam -- the adapter transfers its coverage
 * into Legacy's sink -- so measuring there rather than reaching into the
 * adapter's private buffer tests the join as well as the jitter.
 */
function makeState(over) {
    const layer = { type: "paint", visible: true, locked: false,
                    canvas: {}, ctx: {} };
    const big = 1 << 28;
    return Object.assign({
        tool: "brush",
        layers: [layer], activeLayerIdx: 0,
        editingMask: false, regionMode: false,
        W: DOC, H: DOC,
        brushSize: 24, brushSizeMode: "document_pixels",
        brushHardness: 0.85, brushOpacity: 1, brushFlow: 1,
        brushSpacing: 0.15, brushDensity: 1, brushFalloff: "default",
        brushPreset: "round", brushRatio: 1, brushAngle: 0, brushSpikes: 2,
        smoothing: 0, pressureSensitivity: false, pressureAffects: "none",
        brushDynamics: { spacing: 0.15, followStroke: false },
        zoom: { scale: 1 },
        stroke: {
            alphaMap: new Uint8Array(DOC * DOC),
            dirty: { x0: big, y0: big, x1: -1, y1: -1 },
            frameDirty: { x0: big, y0: big, x1: -1, y1: -1 },
        },
    }, over || {});
}

const CORE = { brushPx: () => 24 };
//: TWO SCALARS, not an event. `normalize` calls `toDoc(raw.clientX,
//: raw.clientY)` (`input.js:130`); a one-argument mapper silently produced
//: NaN coordinates and the whole stroke collapsed to its opening mark.
const toDoc = (cx, cy) => ({ x: cx, y: cy });

let _t = 0;
function ev(x, y) {
    //: A REAL TIMESTAMP. `normalize` converts it to microseconds and the speed
    //: dynamic reads the interval; leaving it at 0 makes every sample
    //: simultaneous.
    _t += 8;
    return { pressure: 0.5, pointerType: "pen",
             clientX: x, clientY: y, timeStamp: _t, isTrusted: false };
}

/** Paint one straight stroke; hand back what landed in Legacy's sink. */
function paint(over) {
    const S = makeState(over);
    A._setEnabled(true);
    A.resetStats();
    //: PIN THE SEED. Every stroke deliberately takes a fresh one, so two
    //: strokes always differ and any comparison expecting equality is
    //: vacuously satisfied -- which is how two mutations survived this probe
    //: on its first campaign.
    A._resetStippleSeed(1234);
    const refusal = A.begin(S, CORE, ev(40, DOC / 2), toDoc);
    if (refusal) { A._setEnabled(false); return { refusal }; }
    for (let i = 1; i <= 40; i++) A.addFromEvent(S, ev(40 + i * 7, DOC / 2), toDoc);
    A.finish(S, ev(40 + 40 * 7, DOC / 2), toDoc);
    const marks = A.stats().marks;
    A._setEnabled(false);
    return { alpha: S.stroke.alphaMap, marks };
}

function dyn(o) { return { brushDynamics: Object.assign({ spacing: 0.15, followStroke: false }, o) }; }

function stats(alpha) {
    let painted = 0, total = 0, minY = DOC, maxY = -1;
    for (let y = 0; y < DOC; y++) {
        for (let x = 0; x < DOC; x++) {
            const v = alpha[y * DOC + x];
            if (!v) continue;
            painted += 1; total += v;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        }
    }
    return { painted, total, band: maxY < 0 ? 0 : maxY - minY + 1 };
}

function differing(a, b) {
    let n = 0;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) n += 1;
    return n;
}

const out = {};

const base = paint(dyn({}));
out.baseline = base.refusal ? { refusal: base.refusal }
                            : Object.assign({ marks: base.marks }, stats(base.alpha));

if (!base.refusal) {
    const b = stats(base.alpha);

    // ── 1. each control moves its own quantity ────────────────────────────
    const size = paint(dyn({ sizeJitter: 0.5 }));
    const opacity = paint(dyn({ opacityJitter: 0.5 }));
    const scatterRound = paint(dyn({ scatter: 0.5 }));
    out.eachControl = {
        baselineBand: b.band, baselineTotal: b.total,
        //: A size jitter widens the band and must not change the MARK COUNT,
        //: which is the sampler's business and not the jitter's.
        sizeBand: stats(size.alpha).band,
        sizeKeptTheMarkCount: size.marks === base.marks,
        //: Opacity is a shading control: total alpha moves, the footprint does
        //: not.
        opacityTotal: stats(opacity.alpha).total,
        opacityBand: stats(opacity.alpha).band,
        //: A ROUND tip sweeps, and a sweep cannot scatter its dabs. Recorded
        //: rather than hidden -- this IS the stated gap for Airbrush and Pastel.
        scatterOnASweptTipDiffering: differing(base.alpha, scatterRound.alpha),
    };

    // ── 2. scatter on a tip that actually stamps ──────────────────────────
    //: Scatter Dust's kind routes to the stamp renderer, so its dabs are
    //: placed individually and can be displaced.
    const flat = paint(Object.assign({ brushPreset: "scatter" }, dyn({})));
    const scattered = paint(Object.assign({ brushPreset: "scatter" },
                                          dyn({ scatter: 0.5 })));
    out.scatterOnAStampedTip = {
        baselineBand: stats(flat.alpha).band,
        scatteredBand: stats(scattered.alpha).band,
        differing: differing(flat.alpha, scattered.alpha),
    };

    // ── 2b. rotation jitter, on a tip that HAS an orientation ────────────
    //: A circle has none, so rotation jitter cannot show on a round tip -- the
    //: same reason `TIP_CAPABILITIES` calls spikes "needs-shape". Scatter Dust
    //: is the only preset declaring it (0.5), and it is not round.
    //: A NON-ZERO FROZEN ANGLE. With `brushAngle: 0` the jitter varies around
    //: zero either way, so dropping the base term is invisible -- a mutation
    //: campaign proved that fixture could not fail. Calligraphy's 45 is the
    //: real case: a held nib must jitter around its own angle.
    const flatPlain = paint(Object.assign(
        { brushPreset: "flat", brushRatio: 0.4, brushAngle: 45 }, dyn({})));
    const flatSpun = paint(Object.assign(
        { brushPreset: "flat", brushRatio: 0.4, brushAngle: 45 },
        dyn({ rotationJitter: 0.15 })));
    //: The same jitter around ZERO. If the base angle were dropped, the spun
    //: stroke would land here instead -- so this is the discriminator.
    const flatSpunAtZero = paint(Object.assign(
        { brushPreset: "flat", brushRatio: 0.4, brushAngle: 0 },
        dyn({ rotationJitter: 0.15 })));
    const roundPlain = paint(dyn({}));
    const roundSpun = paint(dyn({ rotationJitter: 0.5 }));
    out.rotationNeedsAShape = {
        shapedDiffering: differing(flatPlain.alpha, flatSpun.alpha),
        roundDiffering: differing(roundPlain.alpha, roundSpun.alpha),
        //: Jittering around 45 must not equal jittering around 0.
        keepsItsBaseAngle: differing(flatSpun.alpha, flatSpunAtZero.alpha),
    };

    // ── 3. the channels do not move together ──────────────────────────────
    const sizeOnly = paint(dyn({ sizeJitter: 0.4 }));
    const opacityOnly = paint(dyn({ opacityJitter: 0.4 }));
    out.channelsAreIndependent = {
        sizeChangedBand: stats(sizeOnly.alpha).band !== b.band,
        opacityLeftBandAlone: stats(opacityOnly.alpha).band === b.band,
    };

    // ── 3b. the dab index does not restart ────────────────────────────────
    //: `_placeMarks` runs once per BATCH of dabs. Indexing the draw by the
    //: batch-local `i` instead of a stroke-long counter makes every batch
    //: jitter identically -- a pattern at the pointer-event rate rather than
    //: noise. Measured as self-similarity between the stroke's two halves:
    //: with a stroke-long index they differ, with a per-batch one they repeat.
    (function () {
        //: A CONSTANT INDEX MAKES EVERY DAB THE SAME SIZE, so the stroke is a
        //: uniform ribbon. A stroke-long index varies it dab to dab. So the
        //: discriminator is the VARIATION IN COLUMN HEIGHT along the stroke,
        //: not self-similarity -- which a uniform ribbon also has plenty of,
        //: and which is why the first version of this could not fail.
        const a = paint(dyn({ sizeJitter: 0.6 })).alpha;
        const heights = [];
        for (let x = 60; x < DOC - 60; x++) {
            let lo = -1, hi = -1;
            for (let y = 0; y < DOC; y++) {
                if (a[y * DOC + x]) { if (lo < 0) lo = y; hi = y; }
            }
            if (lo >= 0) heights.push(hi - lo + 1);
        }
        let mean = 0;
        for (const h of heights) mean += h;
        mean /= (heights.length || 1);
        let varsum = 0;
        for (const h of heights) varsum += (h - mean) * (h - mean);
        const sd = Math.sqrt(varsum / (heights.length || 1));
        out.indexDoesNotRestart = {
            columns: heights.length,
            meanHeight: +mean.toFixed(2),
            heightSd: +sd.toFixed(3),
        };
    })();

    // ── 4. the draw is the shared hash, and its channels decorrelate ──────
    //: Four salts over the same (seed, index). If two channels correlated, a
    //: big dab would always also be the most opaque -- a pulse, not noise.
    const N = 4096;
    //: READ OUT OF THE ADAPTER, not restated here. The first version listed the
    //: four constants literally, so changing one in the source could not fail
    //: this -- it measured a copy of the claim rather than the claim. A
    //: mutation campaign caught exactly that.
    const adapterSrc = fs.readFileSync(path.join(V2, "canvas-adapter.js"), "utf8");
    const salts = ["JITTER_SIZE", "JITTER_OPACITY", "JITTER_ROTATION",
                   "JITTER_SCATTER"].map((name) => {
        //: Parsed by hand rather than by regex, because every escape in this
        //: file has to survive being written through a shell heredoc, and the
        //: first version's did not -- the pattern arrived as `consts+` and
        //: matched nothing.
        const needle = "const " + name + " = ";
        const at = adapterSrc.indexOf(needle);
        if (at < 0) throw new Error("no constant " + name + " in canvas-adapter.js");
        const from = at + needle.length;
        const end = adapterSrc.indexOf(";", from);
        return parseInt(adapterSrc.slice(from, end).trim(), 16) | 0;
    });
    out.saltsAreDistinct = new Set(salts).size === salts.length;
    const series = salts.map((salt) => {
        const a = new Float64Array(N);
        for (let i = 0; i < N; i++) a[i] = K.hash01(i, (12345 ^ salt) | 0) * 2 - 1;
        return a;
    });
    function corr(a, c) {
        let ma = 0, mc = 0;
        for (let i = 0; i < N; i++) { ma += a[i]; mc += c[i]; }
        ma /= N; mc /= N;
        let num = 0, da = 0, dc = 0;
        for (let i = 0; i < N; i++) {
            const x = a[i] - ma, y = c[i] - mc;
            num += x * y; da += x * x; dc += y * y;
        }
        return num / Math.sqrt(da * dc);
    }
    let worst = 0;
    for (let i = 0; i < salts.length; i++) {
        for (let j = i + 1; j < salts.length; j++) {
            const c = Math.abs(corr(series[i], series[j]));
            if (c > worst) worst = c;
        }
    }
    let mean = 0, lo = 1, hi = -1;
    for (let i = 0; i < N; i++) { mean += series[0][i]; lo = Math.min(lo, series[0][i]); hi = Math.max(hi, series[0][i]); }
    out.draw = {
        worstChannelCorrelation: +worst.toFixed(4),
        mean: +(mean / N).toFixed(4), min: +lo.toFixed(4), max: +hi.toFixed(4),
    };
}

process.stdout.write(JSON.stringify(out, null, 1));
