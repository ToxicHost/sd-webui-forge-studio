# ADR-0007 — Full-Latent Quality Preview Default

## Status

Accepted pending future validation.

## Decision

Default preview uses full latent → full-resolution TAESD → RGB resize. Reduced-latent decoding is experimental.

## Rationale

A reduced-latent path produced severely garbled previews for at least one user/model path.

## Consequences

- safe scheduling optimizations remain;
- Fast mode requires model-family validation;
- diagnostic mode compares paths.
