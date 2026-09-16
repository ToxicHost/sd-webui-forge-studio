# Phase 1 — Repository Bootstrap

## Purpose

Create a fork structure that can merge Neo continuously.

## Steps

1. Configure remotes and branches.
2. Enable `git rerere`.
3. Add documentation scaffold.
4. Add `.gitignore` protection for models/outputs/venv.
5. Add distribution version file.
6. Add upstream-base display.
7. Add initial patch inventory.
8. Add contribution and agent rules.
9. Add issue/PR templates.
10. Perform a no-change upstream merge rehearsal.

## Design constraints

- no performance changes;
- no broad directory move;
- no deletion of stock UI;
- no settings migration yet.

## Exit criteria

- clean clone can be reproduced;
- upstream can be fetched and merged;
- current Studio source is committed;
- release/version metadata is visible;
- patch surface is documented.
