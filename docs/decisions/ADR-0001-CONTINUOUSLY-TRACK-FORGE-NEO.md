# ADR-0001 — Continuously Track Forge Neo

## Status

Accepted.

## Decision

Maintain Forge Studio as a distribution fork with Forge Neo configured as an upstream remote. Merge upstream regularly through an integration branch.

## Rationale

The product depends on Neo's architecture support and fixes. A one-time copy would create unnecessary maintenance.

## Consequences

- upstream provenance must be recorded;
- core patch surface must remain small;
- sync tests and reports are required;
- behavioral conflicts require deliberate review.
