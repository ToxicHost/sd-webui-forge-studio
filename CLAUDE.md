# CLAUDE.md — Fable/Claude Code Instructions

Follow `AGENTS.md`.

## First Fable session

The first Fable 5 session is **read-only**.

Do not modify files. Perform an architecture audit:

- read all top-level project documents;
- inspect the diff against `UPSTREAM_BASE`;
- map Studio-owned versus Neo-owned files;
- trace one base → Hires → ADetailer generation;
- inspect tests and patch inventory;
- identify documentation/code mismatches;
- identify high-conflict upstream edits;
- report recommendations ranked by risk.

Only begin implementation after the owner approves one scoped subsystem.

## Ongoing behavior

- prefer coherent subsystem changes over scattered local patches;
- preserve compatibility adapters;
- do not redesign proven foundation merely because a different architecture is aesthetically cleaner;
- convert assumptions into tests or diagnostics;
- produce a phase-aware handoff after every session.
