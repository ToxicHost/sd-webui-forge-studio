# ADR-0003 — Embed First, Extract Later

## Status

Accepted.

## Decision

Initially import the tested Studio extension as a built-in component with minimal change. Extract services only after parity tests exist.

## Rationale

Combining integration and architectural rewrite would obscure regressions.

## Consequences

- temporary duplication/legacy structure is tolerated;
- migration proceeds concern by concern;
- broad cleanup is deferred.
