# Observability and Diagnostics

## Goals

- make bug reports actionable;
- distinguish sampler time from transition time;
- expose actual resident model state;
- identify preview overhead;
- support upstream merge regression diagnosis;
- avoid leaking private paths or prompts without consent.

## Trace format

Every generation has a trace ID.

Suggested event fields:

```json
{
  "trace_id": "...",
  "span": "hires.model_transition",
  "start_perf": 0.0,
  "duration_ms": 0.0,
  "overlaps_parent": true,
  "checkpoint_signature": "...",
  "phase": "hires",
  "reason": "temporary_checkpoint",
  "result": "success"
}
```

## Diagnostics panel

Display:

- distribution version;
- upstream commit;
- patch-set version;
- active and selected checkpoints;
- active VAE/text encoder;
- model transition state;
- allocated/reserved/free VRAM;
- cached detectors;
- recent phase durations;
- preview mode and frame count;
- forced synchronization/cache-clear count and reasons;
- last error;
- launch flags.

## Copy diagnostic report

Provide two modes:

### Safe report

Default. Redacts:

- user names;
- absolute home paths;
- prompt text;
- API tokens;
- remote credentials;
- image contents.

### Full local report

Explicit opt-in. May include prompts and full paths for personal debugging. Warn before copying.

## Logs

Use structured prefixes:

```text
[Studio Trace]
[Studio Model]
[Studio Memory]
[Studio Preview]
[Studio ADetailer]
[Studio API]
```

Do not flood the normal console with per-step debug output. Debug level may include it.

## Health assertions

On request completion, optionally assert:

- no generation lock held;
- no stale temporary model lease;
- preview in-flight count is zero or belongs to current request;
- current active model signature can be reconciled;
- no temporary LoRA stack remains;
- wall timer is closed.

Violations produce a diagnostic warning and force conservative cleanup.
