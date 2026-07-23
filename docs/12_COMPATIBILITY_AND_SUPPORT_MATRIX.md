# Compatibility and Support Matrix

## Support tiers

### Tier A — Release blocking

The configurations the project owner can test regularly. These define stable support.

Initial recommendation:

- Windows 10/11;
- NVIDIA CUDA;
- one 16 GB development GPU;
- one 24 GB GPU;
- SDXL/Pony/Illustrious-class workflow used daily;
- base, Hires, ADetailer, ControlNet, canvas/inpaint;
- standard and low-VRAM launch profiles.

### Tier B — Best effort

- Linux;
- 8 GB GPUs;
- selected SD1.5 models;
- selected Flux or newer architectures;
- less common extensions.

### Tier C — Experimental

- 6 GB configurations;
- multi-GPU;
- remote/multi-user operation;
- model caching beyond one active model;
- Fast reduced-latent previews;
- phase-batched ADetailer/Hires.

## Matrix dimensions

Track:

- OS;
- Python;
- PyTorch;
- driver;
- GPU and VRAM;
- system RAM;
- model family;
- precision/storage dtype;
- VAE;
- text encoder;
- memory profile;
- Hires checkpoint policy;
- ADetailer detector;
- preview mode;
- extension set.

## Compatibility promises

Do not claim "whatever Neo supports" as a tested guarantee.

Preferred wording:

> Forge Studio inherits broad architecture compatibility from its Forge Neo base. Stable support is limited to the configurations listed in the tested matrix; other Neo-supported configurations are best effort.

## Extension policy

Classify extensions:

- works unchanged;
- works only in stock UI;
- supported by Studio adapter;
- conflicts with integrated behavior;
- unsupported.

ADetailer should be treated as a first-party integration dependency only if its exact fork/version and compatibility contract are maintained.

## Deprecation

A supported configuration can be deprecated only with:

- release-note notice;
- reason;
- replacement path where possible;
- at least one release of warning unless security/correctness requires immediate removal.
