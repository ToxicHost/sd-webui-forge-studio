# Phase 4 — Safe Performance Wins

## Purpose

Reduce overhead without redesigning model residency.

## Work packages

### 4A — ADetailer detector cache

- cache YOLO/detector objects;
- key by file/device/version identity;
- bounded eviction;
- invalidation;
- telemetry;
- low-VRAM fallback.

### 4B — File catalog cache

- avoid `refresh_vae_list()` on ordinary generation/status;
- explicit refresh;
- one fallback refresh on lookup failure;
- checkpoint/VAE metadata cache;
- filesystem-change invalidation where practical.

### 4C — Preview

- full-latent Quality default;
- demand gating;
- hidden-tab suppression;
- bounded cadence;
- one in flight;
- stale drop;
- normal-priority Studio stream;
- GPU RGB resize before CPU transfer;
- bounded CPU encoder;
- Fast reduced-latent experimental only.

### 4D — Load request coalescing

- canonical requested signature;
- no-op duplicate;
- debounce model/VAE/text-encoder selection;
- serialize active transitions;
- cancel superseded pending requests.

## Exit criteria

- each optimization has its own A/B report;
- no output or state regression;
- feature flags available;
- memory soak passes;
- `0.3.0-alpha` candidate.
