# Risk Register

| ID | Risk | Probability | Impact | Mitigation | Trigger |
|---|---|---:|---:|---|---|
| R01 | Upstream redesign breaks hooks | Medium | High | Small patch surface, adapters, merge rehearsal | Conflicts in model/processing internals |
| R02 | Optimization changes output | Medium | Critical | Feature flags, fixed-seed parity, independent review | Prompt/seed/hash mismatch |
| R03 | Reduced cleanup causes OOM | Medium | Critical | Pressure telemetry, Compatible fallback, soak tests | VRAM growth or allocation failures |
| R04 | Temporary model leaks to next request | Medium | Critical | Explicit session lease/finalizer, state assertions | Active/selected mismatch |
| R05 | ADetailer/LoRA state leaks | Medium | High | Per-request structured state, nested cleanup tests | Unexpected LoRA in next image |
| R06 | Preview race corrupts frames | Medium | Medium | Immutable snapshot/event, full-latent default | Garbled or phase-stale previews |
| R07 | Timing remains misleading | Medium | High | Perf-counter wall timer, overlap-aware spans | Console differs from observed wall time |
| R08 | Duplicate extension routes | High during migration | High | Startup detection and deterministic disable | `/studio` registered twice |
| R09 | Installer damages user data | Low | Critical | Separate data roots, backups, rollback tests | In-place destructive migration |
| R10 | AGPL/third-party compliance gap | Low/Medium | High | License inventory and source link | Release bundles unknown component |
| R11 | Support scope grows too quickly | High | High | Tiered matrix and explicit non-goals | Unverified configurations treated as blockers |
| R12 | Agent performs broad rewrite | Medium | High | AGENTS rules, small tasks, diff budget | Large unreviewed cross-core diff |
| R13 | Upstream merges become rare | Medium | High | Scheduled sync cadence and reports | More than two months behind |
| R14 | Model cache consumes system RAM | Medium | High | Disabled by default, budget and eviction | Paging or system instability |
| R15 | Background restore races next request | Medium | Critical | Serialized transition manager, cancellation contract | Concurrent load attempts |
| R16 | Metrics overhead affects generation | Low/Medium | Medium | Lightweight spans, sampling, debug gating | Throughput regression with tracing |
| R17 | Fable handoff causes redesign churn | Medium | Medium | Read-only audit first, repository docs authoritative | Proposed rewrite before audit |
| R18 | Stock UI behavior regresses | Medium | High | Compatibility smoke suite | Stock route/generation fails |

## Risk review

Review the register:

- at each phase gate;
- before enabling an experimental feature by default;
- after an upstream merge;
- after a production bug;
- before public release.
