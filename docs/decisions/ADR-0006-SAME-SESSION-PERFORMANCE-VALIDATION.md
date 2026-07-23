# ADR-0006 — Same-Session Performance Validation

## Status

Accepted.

## Decision

Performance A/B comparisons must run in one server process with fixed settings. Cross-session results may be recorded but not used as proof.

## Rationale

Existing Studio testing observed large session-to-session drift.

## Consequences

- benchmark tooling must support toggles without restart;
- reports include run order and trace IDs.
