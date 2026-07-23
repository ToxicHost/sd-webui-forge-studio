# ADR-0002 — Studio Default, Stock UI Retained

## Status

Accepted for alpha and beta.

## Decision

Forge Studio is the default experience. Neo's stock UI remains available as compatibility and recovery mode.

## Rationale

It supports comparison, extension compatibility, and debugging while the integrated product matures.

## Consequences

- both startup paths must be tested;
- route ownership must be deterministic;
- stock UI regressions are release blockers during beta.
