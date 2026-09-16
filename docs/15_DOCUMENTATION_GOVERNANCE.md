# Documentation Governance

## Source-of-truth hierarchy

1. Repository code and tests.
2. Architecture decisions (`docs/decisions/`).
3. `PROJECT_STATE.md`.
4. Phase plan and exit report.
5. Release notes.
6. Agent conversation.

A conversation may suggest a change, but it is not authoritative until reflected in the repository.

## Required living documents

- `PROJECT_STATE.md`
- `UPSTREAM_BASE`
- `ROADMAP.md`
- `docs/14_PATCH_INVENTORY.md`
- `docs/13_RISK_REGISTER.md`
- `docs/12_COMPATIBILITY_AND_SUPPORT_MATRIX.md`
- performance baselines/results
- release notes

## Update rules

- Update docs in the same PR as behavior.
- Do not leave "TODO: document later" for model lifecycle or migration behavior.
- Performance claims include environment and raw evidence.
- Every phase closes with an exit report.
- Every upstream merge produces a sync report.
- Every architecture choice with long-term consequences gets an ADR.

## Documentation quality

A new contributor or agent should be able to answer:

- What upstream commit is this based on?
- How do I launch it?
- Which Neo core files are modified?
- What is the current active phase?
- What works?
- What is broken?
- How do I run smoke tests?
- How are performance numbers collected?
- What is the next safe task?
