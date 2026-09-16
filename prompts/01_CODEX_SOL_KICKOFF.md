# GPT-5.6 Sol / Codex Kickoff Prompt

You are bootstrapping an upstream-tracking Forge Studio distribution from the current repository.

Read, in order:

1. `00_START_HERE.md`
2. `AGENTS.md`
3. `PROJECT_STATE.md`
4. `UPSTREAM_BASE`
5. `02_MASTER_PROJECT_PLAN.md`
6. `docs/phases/PHASE_0_BASELINE_AND_PROVENANCE.md`
7. `docs/17_INITIAL_SOURCE_FINDINGS.md`

Your first task is Phase 0 only.

Do not optimize code. Do not restructure the Studio frontend. Do not change model loading.

Produce:

- exact upstream Neo branch/commit/date and dirty status;
- exact Studio source identity;
- remotes and branch setup;
- environment capture instructions/output;
- baseline launch and test commands;
- a complete baseline run checklist;
- populated `UPSTREAM_BASE`;
- populated `PROJECT_STATE.md`;
- updated source findings where evidence differs;
- one commit containing only provenance/project bootstrap.

Before modifying files, show the proposed file list and commands. At completion, report tests/commands, commit hash, remaining unknowns, and next single task.
