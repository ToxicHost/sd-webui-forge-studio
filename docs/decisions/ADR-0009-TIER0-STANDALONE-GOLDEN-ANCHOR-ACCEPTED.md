# ADR-0009 — Tier-0 Standalone Backend Golden Anchor Accepted

## Status

```text
Tier-0 standalone backend golden anchor:
ACCEPTED BY COMBINED EVIDENCE
```

Accepted on the combined evidence of live attempts 05 and 06 and the
cleanup-only CUDA baseline diagnostic. Baseline
`0877bc4e8ada1b9f444566a35db322c084e813a5`.

## Decision

Treat the Tier-0 standalone backend as proven for the narrow scope below, and
begin product integration on that basis. Do not treat it as feature parity.

## Proven

```text
Studio-owned standalone startup reaches retained Forge inference
direct model loading works
real prompt setup works
real conditioning works
real denoising works
12/12 sampler steps work
VAE decode works
Studio result publication works
opaque result delivery works
deterministic reproduction works
all twelve measured Gradio counters stay zero
model-only load/unload returns to absolute zero
```

Attempts 05 and 06 produced the same 768x768 PNG byte-for-byte —
sha256 `6c23288148…0607e135`, 754,452 bytes, seed 123456789 — through retained
Forge conditioning, sampling and VAE decode, published through Studio's own
`ResultRegistry` with an opaque handle that still resolved after every
in-memory generation object was released.

The cleanup-only diagnostic loaded one Anima session and unloaded it without
entering generation:

```text
                     allocated        reserved
B0                           0               0
B0_prime                     0               0
MODEL_READY      3,934,860,288   3,944,742,912
B1                           0   3,944,742,912
B2                           0               0
```

`B1.allocated == 0` was reached **before** any cache clear, with all twelve
owned weak references dead and the Forge loaded-model registry at its pre-load
count. `B0_prime == B0` established that importing the retained Forge backend
allocates zero device bytes, so there is no non-zero runtime baseline.

## Deferred to reliability work

```text
fixed 9,568,256-byte post-generation allocation
warm repeated generations
cold unload after repeated generation
model A -> model B switching
cancellation during retained generation
OOM recovery
long-running allocator/fragmentation trend
```

The residual is now localized rather than resolved. Four candidates were
eliminated: the Forge loaded-model registry (entries hold weak references only,
`backend/memory_management.py:446`, `:490`), a post-import runtime baseline
(`B0_prime == B0 == 0/0`), the model session itself (`B1.allocated == 0`), and
`backend/quant_rotation.py:13` `_HADAMARD_CACHE` (empty at every measurement
point). What remains is the generation path: prompt setup, conditioning,
sampling, or decode.

## Not yet proven

```text
full Neo feature parity
scripts/extensions
Hires/refiner
legacy save behavior
complete metadata parity
all samplers/schedulers/model families
production packaging
```

## What this record deliberately does not say

**Attempt 06 did not reach absolute-zero cleanup after generation.** It left
9,568,256 bytes allocated, identical to attempt 05, after releasing strictly
more. The absolute-zero result belongs to the cleanup-only diagnostic, which
performed no generation. Those are two different runs proving two different
things, and combining them is the whole content of "by combined evidence".

One disclosure travels with the acceptance: the owned cleanup performed exactly
one cache clear, and a second `torch.cuda.empty_cache()` occurred inside Forge's
own loader at `backend/loader.py:828` during `forge_loader`. Attempts 05 and 06
reported "exactly one" because they counted only Studio's call; the diagnostic
instruments `torch.cuda.empty_cache` itself and therefore sees both. The same
call happened in those attempts and went unreported. It cannot affect the
teardown proof, because allocated reached zero before any clear.

## Consequences

- product integration may begin, starting with a dual-mode composition root;
- the post-generation residual becomes a dedicated reliability milestone, not a
  blocker;
- no parity claim may cite this record;
- a future live run that generates *and* reaches `0/0/0` would supersede the
  combined-evidence qualifier, and until then the qualifier stays.

## Evidence

```text
Evidence/studio-first-tier0-image/                    attempts 01-06
Evidence/studio-tier0-forge-registry-teardown/        registry rejection
Evidence/studio-tier0-cuda-baseline-diagnostic/       B0/B0'/B1/B2, ABSOLUTE_ZERO
```
