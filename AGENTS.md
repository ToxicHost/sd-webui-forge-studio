# AGENTS.md — Operating Contract for Coding Agents

Read before changing code.

## First actions

1. Read `00_START_HERE.md`.
2. Read `PROJECT_STATE.md`.
3. Read `UPSTREAM_BASE`.
4. Read the active phase plan.
5. Inspect `docs/14_PATCH_INVENTORY.md`.
6. Confirm the working tree and current branch.
7. State the single task you will perform.

## Hard rules

- Do not rewrite Forge Neo.
- Do not broaden scope without recording a decision.
- Do not optimize before instrumentation exists.
- Do not compare performance across server restarts as proof.
- Do not call preview work additive to wall time when it overlaps generation.
- Do not add global CUDA synchronization without measured need and a reason code.
- Do not change seeds, prompt resolution, metadata, or output encoding silently.
- Do not let temporary model/LoRA/script state survive a request.
- Do not mix a broad refactor with a performance change.
- Do not commit model weights, outputs, private logs, tokens, or user paths.
- Do not remove stock Neo UI during alpha/beta.
- Keep one logical change per commit.

## Required evidence for performance changes

- baseline and variant in one process;
- fixed seed/settings;
- environment;
- raw logs;
- trace IDs;
- correctness comparison;
- memory behavior;
- repetitions and median;
- fallback/disable path.

## Core edit rule

Before editing a Neo-owned core file:

1. explain why an adapter/hook cannot solve it;
2. keep the edit minimal;
3. add/update patch inventory;
4. add tests;
5. state upstream conflict likelihood.

## Session end

Update `PROJECT_STATE.md` and use `docs/templates/SESSION_HANDOFF.md`.

Never leave undocumented uncommitted changes.
