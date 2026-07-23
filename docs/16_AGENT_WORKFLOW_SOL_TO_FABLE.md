# Agent Workflow: GPT-5.6 Sol to Fable 5

## Operating model

### Sol phase

Use GPT-5.6 Sol in Codex for:

- repository bootstrap;
- source import;
- branch/remotes;
- parity and smoke harness;
- trace/timer implementation;
- narrow caches;
- small adapter hooks;
- deterministic refactors;
- benchmark automation;
- merge conflict resolution.

### Fable phase

When available, Fable 5 begins with a read-only audit.

Use it for:

- architectural consistency review;
- subsystem extraction;
- model-session design;
- memory policy;
- large coherent migrations;
- long-running upstream integration planning;
- documentation synthesis.

### Independent review

A risky change should be reviewed by the other agent/model or by a human familiar with the subsystem.

## Handoff prerequisites

Before changing primary agents:

- clean or committed working tree;
- `PROJECT_STATE.md` current;
- active phase and next task documented;
- raw benchmark/log locations recorded;
- patch inventory current;
- unresolved decisions listed;
- no hidden local configuration required to launch.

## Fable onboarding sequence

1. Read repository docs.
2. Inspect `git diff` from upstream base.
3. Map Studio-owned and Neo-owned modules.
4. Review tests and patch inventory.
5. Trace one generation end-to-end.
6. Produce an audit without edits.
7. Compare audit with documented architecture.
8. Agree on one subsystem before coding.

## Session closure

Every agent session ends with:

- summary of changes;
- commands/tests run;
- results;
- files changed;
- commits;
- new risks;
- next single task;
- project-state update.

Use `docs/templates/SESSION_HANDOFF.md`.
