# Forge Studio Distribution — Start Here

> **Current as of 2026-08-11.** This document is the original bootstrap plan and
> parts of it are now history rather than instruction — the sections below say
> which. For what the product actually is today, read in this order:
>
> 1. `Reference/FORGE_STUDIO_OVERNIGHT_RESULT_2026-08-11.md` — latest session result
> 2. `Reference/CHATGPT_PROJECT_PLANNER_HANDOFF.md` — verified state of the project
> 3. `CHANGELOG.md` — milestone history with evidence classes
> 4. `docs/14_PATCH_INVENTORY.md` — what this distribution changes in Neo
>
> The two governing books named in `CLAUDE.md` remain the authority on intended
> scope.

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

Do not rewrite Forge Neo. **That part held, and is still true.** `backend/`,
`ldm_patched/` and `extensions-builtin/` are untouched; the only Neo-owned
changes are the two import-graph and install-time patches inventoried in
`docs/14_PATCH_INVENTORY.md`.

> **SUPERSEDED — the paragraph below describes a route that was not taken.**
> The Studio extension was never embedded as a built-in component, and Neo's
> interface was never carried as a live compatibility path in this repository.
> What happened instead: the extension's **frontend** was adopted as the
> standalone shell (2026-07-24, `882c90f3`), and a Studio-owned HTTP,
> application and headless layer was written beneath it. The extension's
> backend was not ported.
>
> That difference is the single largest source of misreading here. The UI is the
> shipping extension's, so it looks feature-complete while much of what it calls
> has no service behind it. Capability gating (2026-08-11) is the beginning of
> making that visible rather than confusing.

Start by embedding the current Studio extension almost unchanged as a built-in component, preserve Neo's original interface as a compatibility path, establish parity and tests, then move backend responsibilities into narrowly scoped Studio-owned services.

The repository—not any model conversation—is the source of truth.

## Planned agent sequence

- **GPT-5.6 Sol in Codex:** repository bootstrap, parity, instrumentation, tests, isolated low-risk improvements.
- **Fable 5 in Claude Code:** read-only architecture audit, then larger coherent subsystem work.
- Either agent may review the other's work. A risky CUDA/model-lifecycle change must not be authored and approved by the same agent without independent review.

## Immediate next action

> **As of 2026-08-11 this is: answer the writable-state-directory decision**
> (`Evidence/r0-r1-overnight-2026-08-11/R2_OWNER_DECISION_PACKET.md`, D1). It
> blocks durable preferences, workflows, layouts, presets and the Gallery
> database — the largest remaining owner-visible defect class, since settings
> silently reset on every launch.
>
> The Phase 0 checklist below is **complete except for the last two items**: the
> patch inventory was empty until 2026-08-11 and is now reconstructed, and
> baseline performance measurements remain partial. Docker was claimed and has
> been reopened — its artifacts live outside the Git root, so a clean clone does
> not contain them.

Complete Phase 0. The first deliverable is not an optimization. It is a reproducible repository with:

- exact upstream commit recorded;
- current Studio snapshot inventoried without importing implementation code;
- clean working tree;
- launch instructions;
- baseline logs;
- test fixtures;
- initial performance measurements;
- a documented patch inventory.
