/**
 * U2 probe: the Canvas-owned presentation-dirty contract, executed.
 *
 * THIS RUNS THE REAL SOURCE, not a copy of it. The presentation block is
 * extracted from `canvas-core.js` by its own section markers and evaluated
 * against a stub document, so the behaviour asserted is the behaviour that
 * ships. A reimplementation here would test this file instead of that one.
 *
 * `canvas-core.js` cannot be loaded whole outside a browser -- it is 6,900
 * lines against a real DOM and a real 2D context -- so the alternative was
 * source scanning, which cannot tell whether a state machine actually works.
 *
 *     node tests/studio_alpha/u2_presentation_probe.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CORE = path.resolve(__dirname, "..", "..", "forge_studio", "frontend",
                          "canvas-core.js");

const START = "// U2 — PRESENTATION DIRTY, OWNED BY CANVAS";
const END = "function getCompositeVersion()";

function loadPresentation(docW, docH) {
    const source = fs.readFileSync(CORE, "utf8");
    const a = source.indexOf(START);
    const b = source.indexOf(END, a);
    if (a < 0 || b < 0) {
        throw new Error("could not locate the presentation block in canvas-core.js");
    }
    const block = source.slice(a, b);
    for (const name of ["markCompositeDirty", "markCompositeDirtyRegion",
                        "takePresentationDirty", "acknowledgePresentation",
                        "invalidatePresentation"]) {
        if (block.indexOf("function " + name) < 0) {
            throw new Error("the extracted block is missing " + name);
        }
    }
    const g = {
        S: { W: docW, H: docH },
        _compositeVersion: 0,
        Math: Math,
        console: { warn() {}, error() {} },
    };
    vm.createContext(g);
    vm.runInContext('"use strict";' + block + "\n"
        + "this.api = { markCompositeDirty, markCompositeDirtyRegion,"
        + " takePresentationDirty, acknowledgePresentation,"
        + " invalidatePresentation, version: () => _compositeVersion };",
        g, { filename: CORE });
    return g.api;
}

const out = {};

// ── 1. Full is the safe default ─────────────────────────────────────────────

{
    const A = loadPresentation(1000, 800);
    out.initialState = A.takePresentationDirty();
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    out.afterFirstAck = A.takePresentationDirty();
    A.markCompositeDirty();
    out.afterPlainMark = A.takePresentationDirty();
}

// ── 2. A named region stays a region ────────────────────────────────────────

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(100, 100, 150, 140);
    out.oneRegion = A.takePresentationDirty();
    A.markCompositeDirtyRegion(400, 300, 420, 330);
    out.unionedRegion = A.takePresentationDirty();
}

// ── 3. Full dominates a region, in both orders ──────────────────────────────

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(10, 10, 20, 20);
    A.markCompositeDirty();
    out.regionThenFull = A.takePresentationDirty();

    const B = loadPresentation(1000, 800);
    B.acknowledgePresentation(B.takePresentationDirty().generation);
    B.markCompositeDirty();
    B.markCompositeDirtyRegion(10, 10, 20, 20);
    out.fullThenRegion = B.takePresentationDirty();
}

// ── 4. The generation is what stops an update being lost ────────────────────

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(100, 100, 150, 140);

    // A consumer takes the pending work...
    const taken = A.takePresentationDirty();
    // ...and the document changes while the upload is in flight.
    A.markCompositeDirtyRegion(500, 500, 520, 520);
    const acceptedStale = A.acknowledgePresentation(taken.generation);
    const stillPending = A.takePresentationDirty();
    // A consumer that takes the CURRENT generation is accepted.
    const acceptedFresh = A.acknowledgePresentation(stillPending.generation);
    out.lostUpdate = {
        acceptedStale: acceptedStale,
        acceptedFresh: acceptedFresh,
        pendingAfterRefusal: stillPending,
        afterAccept: A.takePresentationDirty(),
        // The edit that arrived mid-upload must still be inside the region
        // that was kept pending.
        keptTheLateEdit: stillPending.x0 <= 500 && stillPending.x1 >= 520,
        // And the earlier one, because a union was kept rather than a replace.
        keptTheEarlyEdit: stillPending.x0 <= 100 && stillPending.x1 >= 150,
    };
}

// ── 5. Clipping, and a degenerate region ────────────────────────────────────

{
    const A = loadPresentation(200, 150);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(-50, -50, 60, 40);
    out.clippedLow = A.takePresentationDirty();

    const B = loadPresentation(200, 150);
    B.acknowledgePresentation(B.takePresentationDirty().generation);
    B.markCompositeDirtyRegion(150, 100, 9999, 9999);
    out.clippedHigh = B.takePresentationDirty();

    const C = loadPresentation(200, 150);
    C.acknowledgePresentation(C.takePresentationDirty().generation);
    C.markCompositeDirtyRegion(50, 50, 50, 80);      // zero width
    out.degenerate = C.takePresentationDirty();

    const D = loadPresentation(200, 150);
    D.acknowledgePresentation(D.takePresentationDirty().generation);
    D.markCompositeDirtyRegion(500, 500, 600, 600);  // entirely outside
    out.entirelyOutside = D.takePresentationDirty();
    // A publish that lands nowhere must still move the generation, or a
    // consumer could acknowledge work it never saw.
    out.outsideStillBumpedVersion = D.version() > 0;
}

// ── 6. Fractional bounds round OUTWARD ──────────────────────────────────────
//
// A region that rounded inward would leave a rim of changed pixels unpresented,
// which is the seam a dirty-region bug actually looks like.

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(10.7, 20.2, 30.1, 40.9);
    out.fractional = A.takePresentationDirty();
}

// ── 7. An explicit invalidation is full, and is not a document edit ─────────

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    const versionBefore = A.version();
    const reason = A.invalidatePresentation("webgl-context-restored");
    out.explicitInvalidation = {
        state: A.takePresentationDirty(),
        reason: reason,
        // It must NOT pretend the document changed -- caches keyed on the
        // composite version would be thrown away for a display-only event.
        versionUnchanged: A.version() === versionBefore,
    };
}

// ── 7b. An explicit invalidation AFTER a region clears the rectangle ─────────
//
// U2-V found this in a browser. The invariant was pinned for
// `markCompositeDirty` and not for `invalidatePresentation`, so it shipped
// half-closed: a refused region flatten and a WebGL context restore both
// reported `full: true` alongside a rectangle that looked live.

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(100, 100, 200, 200);
    const withRegion = A.takePresentationDirty();
    A.invalidatePresentation("region-flatten-unavailable");
    out.invalidateAfterRegion = {
        withRegion: withRegion,
        after: A.takePresentationDirty(),
    };
}

// ── 8. Acknowledging twice is refused the second time ───────────────────────

{
    const A = loadPresentation(1000, 800);
    A.acknowledgePresentation(A.takePresentationDirty().generation);
    A.markCompositeDirtyRegion(1, 1, 5, 5);
    const g = A.takePresentationDirty().generation;
    const first = A.acknowledgePresentation(g);
    A.markCompositeDirtyRegion(9, 9, 11, 11);
    const second = A.acknowledgePresentation(g);
    out.doubleAck = {
        first: first, second: second,
        stillPending: A.takePresentationDirty(),
    };
}

process.stdout.write(JSON.stringify(out, null, 1));
