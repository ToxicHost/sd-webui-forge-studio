# Phase 7 — Packaging, Migration, and Release Engineering

## Purpose

Make the product safely installable and maintainable by other users.

## Steps

1. Define application and user-data roots.
2. Create clean install path.
3. Create existing-extension migration.
4. Add settings schema/version migration.
5. Add backup and receipt.
6. Add update mechanism or documented update workflow.
7. Add rollback path.
8. Produce source archive and checksums.
9. Add license/credits/source UI.
10. Audit dependencies and bundled assets.
11. Test standard and low-VRAM launchers.
12. Test clean machine/install.
13. Publish compatibility and known-issues docs.

## Exit criteria

- install/upgrade/rollback pass;
- user data protected;
- source and provenance published;
- support matrix complete;
- `0.5.0-beta` candidate.
