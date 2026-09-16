/**
 * U3-V — the pointer-up endpoint seam, executed rather than spelled.
 *
 * THE DEFECT THIS GUARDS. `canvas-ui.js`'s pointer-up handler called Legacy's
 * `C.finishStroke(...)`, which walks from LEGACY's last dab (`S.stroke.lx/ly`)
 * to the release point. During a V2 stroke Legacy never plots -- the pointermove
 * handler returns early -- so its last dab is still the POINTER-DOWN point, and
 * `finishStroke` drew a straight line from where the stroke began to where it
 * ended.
 *
 * Owner-visible as: "I drew a Z and it turned it into an hourglass." Measured in
 * a real browser at 43,909 extra pixels on a 1024-square Z -- exactly one extra
 * full-length diagonal -- and confirmed by suppressing `finishStroke`, which
 * removed it and left V2's own endpoint fully painted.
 *
 * WHY THIS IS NOT A GREP. U3 and E0 each shipped a guard that asserted an
 * identifier was PRESENT while the mutation deleting its CALL went undetected,
 * and `pointerleave` ALREADY contained a correct copy of this seam -- so any
 * test that merely searched the file for `_v2.finish` was green throughout the
 * entire period the defect existed.
 *
 * So the branch's REAL SOURCE TEXT is cut out of the shipped file and executed
 * against stubs. If the two completions stop being mutually exclusive, the
 * recorded calls change and the tests fail.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CANVAS_UI = path.resolve(
    __dirname, "..", "..", "forge_studio", "frontend", "canvas-ui.js");

const ANCHOR = "U3-V. THE TWO ENDPOINT COMPLETIONS ARE MUTUALLY EXCLUSIVE.";

/**
 * The `if ((S.tool === "brush" ...))` block that contains the anchor, cut out by
 * brace matching so the extraction cannot silently take half a statement.
 */
function extractBranch(source) {
    const at = source.indexOf(ANCHOR);
    if (at < 0) throw new Error("anchor comment is missing from canvas-ui.js");
    const head = 'if ((S.tool === "brush" || S.tool === "eraser") && !S.regionMode) {';
    const start = source.lastIndexOf(head, at);
    if (start < 0) throw new Error("could not find the branch head above the anchor");
    let depth = 0, i = source.indexOf("{", start);
    const open = i;
    for (; i < source.length; i++) {
        const c = source[i];
        if (c === "{") depth += 1;
        else if (c === "}") {
            depth -= 1;
            if (depth === 0) return source.slice(start, i + 1);
        }
    }
    throw new Error("unbalanced braces while extracting the branch");
}

const BRANCH = extractBranch(fs.readFileSync(CANVAS_UI, "utf8"));

/** Run the extracted branch once, recording which completion path was taken. */
function run(opts) {
    const calls = [];
    const S = {
        tool: opts.tool || "brush",
        regionMode: !!opts.regionMode,
        stroke: { lx: 10, ly: 10, lp: 0.5, alphaMap: {} },
    };
    const C = {
        screenToDoc: (cx, cy) => ({ x: cx, y: cy }),
        finishStroke: (x, y, p) => { calls.push(["legacy.finishStroke", x, y, p]); return true; },
        commitStroke: () => { calls.push(["commitStroke"]); },
    };
    const adapter = {
        isActive: () => !!opts.v2Active,
        finish: (s, e, toDoc) => {
            calls.push(["v2.finish",
                        s === S,
                        e && e.tag,
                        typeof toDoc === "function" ? "toDoc" : typeof toDoc]);
            return { moved: 1 };
        },
    };
    const sandbox = {
        S, C,
        e: { tag: "the-real-pointerup-event", clientX: 400, clientY: 300, pressure: 0.6 },
        window: {
            StudioBrushV2Adapter: opts.adapterPresent === false ? undefined : adapter,
            StudioInput: {
                normalize: () => ({ x: 400, y: 300, pressure: 0.6 }),
            },
        },
        console,
    };
    vm.createContext(sandbox);
    vm.runInContext(BRANCH, sandbox, { filename: "canvas-ui.js#pointerup-branch" });
    return calls.map(c => (Array.isArray(c) ? c : [c]));
}

const result = {
    branchLength: BRANCH.length,
    // V2 owns the contact: Legacy's completion must not run.
    v2Active: run({ v2Active: true }),
    // No V2 stroke: Legacy's BE9 completion is still exactly right.
    v2Idle: run({ v2Active: false }),
    // The adapter absent entirely -- Studio without the V2 modules loaded.
    adapterAbsent: run({ adapterPresent: false }),
    // The eraser takes the same path, and E0's repair rides on it.
    v2ActiveEraser: run({ v2Active: true, tool: "eraser" }),
    v2IdleEraser: run({ v2Active: false, tool: "eraser" }),
};

process.stdout.write(JSON.stringify(result, null, 1));
