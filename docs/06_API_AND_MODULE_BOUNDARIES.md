# API and Module Boundaries

## API rules

- Version request and response schemas.
- Avoid exposing raw Neo globals.
- Return stable identifiers rather than internal object representations.
- Every write endpoint declares its side effects.
- Every long-running operation supports cancellation or states why it cannot.
- Model-load endpoints serialize or coalesce operations.
- Errors use structured codes plus human-readable messages.
- Diagnostics include a trace ID.

## Suggested route groups

```text
/studio/api/v1/generation
/studio/api/v1/models
/studio/api/v1/assets
/studio/api/v1/workflows
/studio/api/v1/diagnostics
/studio/api/v1/system
/studio/ws/v1/progress
```

Existing routes may remain as compatibility aliases during migration.

## Model API state

A model status response should distinguish:

```json
{
  "selected_signature": "...",
  "active_signature": "...",
  "temporary_phase_signature": null,
  "transition_state": "idle",
  "last_transition_seconds": 0.0,
  "restoration_policy": "when_required"
}
```

Do not report only the dropdown selection when a different model is resident.

## Generation request identity

Each request receives:

- `request_id`;
- monotonically increasing generation sequence;
- frontend document/tab ID;
- optional workflow ID;
- start timestamp;
- cancellation token.

Preview frames include request ID and sequence so stale frames can be dropped.

## Adapter rules

The Neo adapter is the only Studio module that directly reads/writes:

- `shared.state`;
- `shared.opts`;
- `sd_models.model_data.forge_loading_parameters`;
- script-runner internals;
- Neo processing object internals.

Application services depend on adapter interfaces, not Neo module globals.

## Error recovery contract

After any failed request:

- generation lock released;
- preview workers canceled;
- temporary LoRA/script state cleared;
- model-session state reconciled with actual loaded model;
- UI receives structured error;
- next request can run without restart;
- diagnostic trace remains exportable.
