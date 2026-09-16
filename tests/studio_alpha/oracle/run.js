/**
 * Run every contract against one engine and print a machine-readable result.
 *
 *   node run.js legacy <path to canvas-core.js>
 *   node run.js legacy <path> --human
 *
 * The exit code is 0 when every contract is PASS or EVIDENCE and 1 when any is
 * FAIL, so this is usable as a gate as well as a report.
 */

"use strict";

const { validateAdapter } = require("./adapter.js");
const { makeLegacyAdapter } = require("./legacy_adapter.js");
const { CONTRACTS, VacuousRun } = require("./contracts.js");

function buildAdapter(kind, arg) {
    switch (kind) {
        case "legacy":
            return makeLegacyAdapter(arg);
        // A V2 adapter registers here. It must implement `adapter.js` and
        // nothing else; see Reference/V2_ORACLE_CONTRACT.md.
        default:
            throw new Error(`unknown engine "${kind}"`);
    }
}

function main(argv) {
    const kind = argv[2];
    const arg = argv[3];
    const human = argv.includes("--human");
    if (!kind) {
        process.stderr.write("usage: node run.js <engine> [arg] [--human]\n");
        return 2;
    }

    const adapter = validateAdapter(buildAdapter(kind, arg));
    const results = [];
    for (const contract of CONTRACTS) {
        try {
            const r = contract.run(adapter);
            r.title = contract.title;
            results.push(r);
        } catch (err) {
            // A VACUOUS RUN IS NOT A FAILING CONTRACT, and reporting it as one
            // would hide a broken harness inside a plausible red row. It is its
            // own status, and it stops the run from being trusted.
            results.push({
                id: contract.id,
                title: contract.title,
                status: err instanceof VacuousRun ? "VACUOUS" : "ERROR",
                error: String(err && err.message || err),
            });
        }
    }

    const counts = results.reduce((acc, r) => {
        acc[r.status] = (acc[r.status] || 0) + 1;
        return acc;
    }, {});

    const report = { engine: adapter.name, source: arg || null, counts, results };

    if (human) {
        const pad = (s, n) => String(s).padEnd(n);
        process.stdout.write(`\nBRUSH ACCEPTANCE ORACLE  --  engine: ${adapter.name}\n`);
        process.stdout.write("=".repeat(78) + "\n");
        for (const r of results) {
            process.stdout.write(`${pad(r.status, 9)} ${r.id}\n`);
            process.stdout.write(`          ${r.title}\n`);
            if (r.error) {
                process.stdout.write(`          ! ${r.error}\n`);
            } else {
                process.stdout.write(
                    `          metric    ${r.metric} (${r.units})\n`);
                process.stdout.write(
                    `          threshold ${r.threshold}  [${r.thresholdKind}]\n`);
                process.stdout.write(
                    `          observed  ${JSON.stringify(r.observed)}\n`);
            }
            process.stdout.write("\n");
        }
        process.stdout.write(JSON.stringify(counts) + "\n");
    } else {
        process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    }

    return (counts.FAIL || counts.ERROR || counts.VACUOUS) ? 1 : 0;
}

process.exitCode = main(process.argv);
