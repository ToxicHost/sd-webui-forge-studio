# Phase 6 — Memory and Postprocessing Lifecycle

## Purpose

Optimize nested processing and memory cleanup after lifecycle state is explicit.

## Steps

1. Instrument all forced CUDA synchronization/cache-clear reasons.
2. Count cleanup operations per request and phase.
3. Create nested postprocess context for ADetailer.
4. Avoid duplicate global setup/cleanup in nested calls where safe.
5. Perform one pressure-aware cleanup at an appropriate boundary.
6. Add Compatible/Balanced/Aggressive memory profiles.
7. Implement OOM recovery escalation.
8. Run long memory soak.
9. Integrate structured per-slot ADetailer LoRA state.
10. Preserve prompt inheritance and regional resolution order.
11. Audit ControlNet and regional lifecycle for repeated setup.
12. Research crop batching only after simpler changes.

## High-risk gate

Pressure-aware changes to `torch_gc` or `soft_empty_cache` require:

- VRAM telemetry;
- same-session A/B;
- low/medium/high VRAM tests;
- OOM recovery;
- independent review;
- compatibility fallback.

## Exit criteria

- nested processing does not leak state;
- cleanup count reduced with no OOM increase;
- memory profiles behave as documented;
- ADetailer warm sequence is measurably improved;
- soak passes.
