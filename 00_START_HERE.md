# Forge Studio Distribution — Start Here

This package is the working project plan for turning **Forge Studio v4.10.0** from a Forge Neo extension into an independently branded, continuously upstream-tracking Forge Neo distribution.

The plan is based on the supplied source snapshots:

- Forge Studio `version.json` commit: `b316a4d87abd69837cd723403a7a41e22f97482d`
- Studio README version: `v4.10.0 — public beta`
- Working Forge Neo branch: `neo`
- Verified Forge Neo commit: `97ff3a4024be2f0d5316f16e868e5ef822768872`
- Verified Forge Neo commit date: `2026-07-23T14:06:00+08:00`
- Baseline tag: `neo-baseline-2026-07-23`
- Both supplied projects contain AGPL-3.0 license text.

## Read in this order

1. `01_EXECUTIVE_SUMMARY.md`
2. `02_MASTER_PROJECT_PLAN.md`
3. `03_RELEASE_ROADMAP.md`
4. `docs/01_PROJECT_CHARTER.md`
5. `docs/02_TARGET_ARCHITECTURE.md`
6. `docs/04_EXTENSION_TO_DISTRIBUTION_MIGRATION.md`
7. `docs/07_TEST_STRATEGY.md`
8. `docs/08_PERFORMANCE_PROGRAM.md`
9. `AGENTS.md`
10. `prompts/01_CODEX_SOL_KICKOFF.md`

## Core strategy

Do not rewrite Forge Neo.

Start by embedding the current Studio extension almost unchanged as a built-in component, preserve Neo's original interface as a compatibility path, establish parity and tests, then move backend responsibilities into narrowly scoped Studio-owned services.

The repository—not any model conversation—is the source of truth.

## Planned agent sequence

- **GPT-5.6 Sol in Codex:** repository bootstrap, parity, instrumentation, tests, isolated low-risk improvements.
- **Fable 5 in Claude Code:** read-only architecture audit, then larger coherent subsystem work.
- Either agent may review the other's work. A risky CUDA/model-lifecycle change must not be authored and approved by the same agent without independent review.

## Immediate next action

Complete Phase 0. The first deliverable is not an optimization. It is a reproducible repository with:

- exact upstream commit recorded;
- current Studio snapshot inventoried without importing implementation code;
- clean working tree;
- launch instructions;
- baseline logs;
- test fixtures;
- initial performance measurements;
- a documented patch inventory.
