# Phase 2 — Integrated Distribution

## Purpose

Ship Studio and Neo as one product while preserving both interfaces.

## Steps

1. Place Studio in first-party/built-in location.
2. Register Studio routes exactly once.
3. Make Studio the default landing experience.
4. Preserve stock Neo UI route or launch option.
5. Detect/disable external duplicate Studio installation.
6. Add product-level version/credits/source UI.
7. Consolidate launchers.
8. Validate first-run package install.
9. Validate existing Neo install migration in a disposable copy.
10. Document recovery path.

## Compatibility requirement

At this phase, generation should still use the same Studio/Neo call paths as the existing extension wherever possible.

## Exit criteria

- both interfaces launch;
- one generation succeeds in each;
- no duplicate routes;
- no user model copying;
- source/credits visible;
- clean install documented;
- `0.1.0-alpha` tag candidate.
