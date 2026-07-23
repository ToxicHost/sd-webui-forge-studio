# ADR-0008 — Explicit Model Session State

## Status

Proposed for Phase 5.

## Decision

Represent selected, active, temporary Hires, and postprocessing model state explicitly and serialize transitions through a model-session manager.

## Rationale

A single global selection cannot describe deferred restoration or temporary phase models safely.

## Consequences

- UI shows active versus selected;
- request finalizers reconcile state;
- load endpoints use canonical signatures.
