# Test Strategy

## Test pyramid

### Unit tests

Focus on Studio-owned logic:

- model signatures;
- restoration policy;
- cache key/invalidation;
- timing aggregation;
- preview cadence;
- request schema migration;
- path scrubbing;
- workflow serialization.

### Integration tests

Run against imported Neo modules where feasible:

- route registration;
- model status;
- no-op load;
- temporary model finalizer;
- ADetailer cache lifecycle;
- wildcard resolver parity;
- output encoder settings;
- interrupt cleanup.

### GPU smoke tests

Required on real supported hardware:

- base txt2img;
- same-checkpoint Hires;
- alternate-checkpoint Hires;
- img2img;
- inpaint;
- ADetailer one and multiple detections;
- preview Quality;
- preview disabled;
- interrupt during base/Hires/ADetailer;
- model A → B → A.

### Manual UX tests

- first launch;
- migration;
- route navigation;
- diagnostics export;
- update and rollback;
- duplicate extension warning;
- missing model behavior;
- low-VRAM profile.

## Determinism policy

Pixel-exact equality may not be reliable across driver/PyTorch/kernel changes. Use layered assertions:

1. exact request metadata;
2. exact seeds and resolved prompts;
3. exact dimensions and phase counts;
4. exact model/VAE signatures;
5. no unexpected script/LoRA leakage;
6. perceptual-image comparison with documented threshold;
7. manual review for intentional image-pipeline changes.

Within one process and identical kernels, capture exact hashes where stable, but do not make a brittle cross-environment promise.

## Mandatory smoke matrix

| ID | Scenario | Core assertion |
|---|---|---|
| S01 | Studio startup | `/studio` loads once |
| S02 | Stock UI startup | compatibility UI remains usable |
| S03 | txt2img | output, metadata, seed correct |
| S04 | img2img | source and denoise honored |
| S05 | inpaint | mask isolation correct |
| S06 | Hires same model | no real checkpoint reload |
| S07 | Hires alternate model | policy-consistent transitions |
| S08 | ADetailer warm repeat | detector cache hit |
| S09 | preview disabled | zero image preview decode |
| S10 | preview Quality | coherent frames, bounded count |
| S11 | interrupt | clean next request |
| S12 | model A→B→A | correct active/selected identity |
| S13 | workflow restore | JPEG/WebP quality states independent |
| S14 | wildcard/LoRA | exact generation resolver path |
| S15 | duplicate extension | deterministic warning and disable |
| S16 | migration | backup and receipt created |

## Regression suite for state leakage

Run sequence without server restart:

1. Request with temporary Hires model and ADetailer LoRA.
2. Request without Hires and without ADetailer.
3. Request with different base model.
4. Interrupt during Hires.
5. Request with original base model.

Assert no old model, prompt suffix, LoRA, mask, script, preview, or timing state remains.

## CI limits

CPU-only CI cannot validate actual CUDA lifecycle. It should still test:

- imports;
- schema;
- pure services;
- route construction;
- migrations;
- static checks;
- documentation links;
- packaging manifest.

GPU tests may initially be a documented local release gate rather than hosted CI.
