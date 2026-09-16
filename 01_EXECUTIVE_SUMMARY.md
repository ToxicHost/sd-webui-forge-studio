# Executive Summary

## Objective

Create a public, AGPL-3.0 Forge Neo–based application that ships Forge Studio as its primary frontend and adds carefully measured backend improvements for:

- model loading;
- Hires checkpoint transitions;
- ADetailer detector and nested-processing overhead;
- memory/cache policy;
- preview scheduling;
- accurate phase timing;
- diagnostics and supportability.

## Product position

The intended product is:

> **Forge Studio — an independent Forge Neo–based creative application.**

It should visibly credit Forge Neo, Forge, AUTOMATIC1111, and relevant bundled projects. It must not imply that it is the official Forge Neo distribution.

## Why a distribution instead of only an extension

Studio already coordinates concerns that cross the extension boundary:

- generation request lifecycle;
- separate Hires and postprocessing phases;
- temporary checkpoint changes;
- ADetailer integration;
- progress and live preview;
- model/VAE/text-encoder loading;
- state persistence and workflows;
- output encoding.

The extension can continue to exist during migration, but backend policy belongs in a controlled distribution once it changes Neo's lifecycle.

## Delivery principles

1. **Parity before optimization.**
2. **Small core patch surface.**
3. **Feature flags for risky behavior.**
4. **Truthful instrumentation before performance claims.**
5. **Same-session A/B performance validation.**
6. **Stock Neo UI remains available as a recovery and comparison path.**
7. **Upstream is merged regularly, not copied once and abandoned.**
8. **Every release records its exact upstream base.**
9. **No bundled model weights.**
10. **No silent behavior changes to seeds, prompts, metadata, or image outputs.**

## Release sequence

| Release | Purpose |
|---|---|
| `0.1.0-alpha` | Integrated distribution boots; Studio and stock Neo UI both work |
| `0.2.0-alpha` | Parity tests, truthful timing, diagnostic report |
| `0.3.0-alpha` | Safe latency wins: detector cache, scan cache, preview correction |
| `0.4.0-alpha` | Model-session and Hires lifecycle improvements |
| `0.5.0-beta` | Packaging, updater, migration, compatibility matrix |
| `0.9.0-beta` | External testing, upstream-sync rehearsal, support hardening |
| `1.0.0` | Stable migration path, tested support matrix, documented rollback |

## Expected planning range

For one primary developer using coding agents, this is approximately:

- **8–12 weeks** to a credible private alpha;
- **12–20 weeks** to a responsible public beta;
- longer if recent-model caching, batch-phase scheduling, or broad extension compatibility is included before beta.

These are planning ranges, not promises. The critical path is correctness and reproducibility, not raw implementation speed.
