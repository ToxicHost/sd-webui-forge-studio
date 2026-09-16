# Fable 5 Read-Only Onboarding Prompt

Perform a read-only architecture and project-state audit. Do not edit files.

Read:

- `AGENTS.md`
- `CLAUDE.md`
- `PROJECT_STATE.md`
- `UPSTREAM_BASE`
- `02_MASTER_PROJECT_PLAN.md`
- `docs/02_TARGET_ARCHITECTURE.md`
- `docs/14_PATCH_INVENTORY.md`
- the active phase plan;
- all accepted ADRs.

Then:

1. Run/inspect `git status`, branch/remotes, log graph, and diff from the recorded upstream base.
2. Classify each changed file as:
   - upstream/core patch;
   - Studio-owned;
   - packaging/branding;
   - test;
   - documentation.
3. Trace one generation from Studio API entry through model loading, base sampling, Hires, ADetailer, preview, output encoding, and cleanup.
4. Verify active/selected/temporary model state assumptions.
5. Assess test coverage against the patch inventory.
6. Identify documentation that disagrees with code.
7. Identify the five highest-risk maintenance points.
8. Recommend the next single subsystem and a minimal implementation sequence.
9. State what should *not* be refactored yet.

Return an audit report only. Do not create or modify files until explicitly authorized.
