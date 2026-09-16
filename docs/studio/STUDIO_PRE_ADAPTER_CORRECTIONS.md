# Studio pre-adapter corrections

## Status

The reviewed canonical Studio 4.17 feature branch was fast-forward integrated
into `docs/phase0-complete-baseline` at:

```text
882c90f3fb587938b300e3d7d7fa5e9d30b1edfd
```

Owner visual acceptance is **APPROVED**. It supersedes older pending language.
The canonical frontend was not changed by the correction set.

The owned corrections are on:

```text
feature/studio-pre-adapter-corrections
```

The Opus verdict found the canonical frontend, owned `StudioApplication`
boundary, and contained HTTP/WebSocket presentation safe to preserve and
integrate. It required the following foundation corrections before a real
`ForgeBackendAdapter`; the current correction set applies them.

## A1-A7 disposition

| Item | Correction |
|---|---|
| A1 — infotext | Report only owned request/result facts. Use a backend-resolved seed when present, omit unresolved `-1`, and do not fabricate sampler or schedule values. |
| A2 — progress scales | Keep backend percent progress (`0`–`100`) distinct from canonical fractional progress (`0.0`–`1.0`). One percent maps to `0.01`. |
| A3 — unsupported parameters | Return a visible notice for recognized canonical parameters that the mock cannot honor. Ignore unknown payload metadata rather than creating noise. |
| A4 — observation | Progress polling remains non-consuming and does not materialize or serialize a completed image. Explicit result retrieval remains available. |
| A5 — model state | Model catalog and current-model reads are pure. Only an explicit load establishes selection. |
| A6 — prompts | Empty positive prompts are valid, including negative-only generation. Prompt values remain strings with the existing 4,000-character limit. |
| A7 — dimensions | Remove the invented 256–2048 and multiple-of-64 Studio-wide rule. The mock accepts positive-integer axes and preserves their exact values. |

## A7 capability decision

The bounded retained-source audit found:

- Neo uses a configurable recommended UI step, not a mandatory universal 64;
- exact latent alignment depends on the selected backend/VAE, commonly factor
  8 and factor 16 for Flux2;
- entry-point ranges differ across txt2img, img2img, hires, and API paths;
- no universal safe maximum was established statically;
- model hooks, enabled scripts, operation, device memory, and mode can change
  supported dimensions.

Decision:

```text
FORGE_ADAPTER_CAPABILITY_REQUIRED = YES
```

The deterministic mock validates only that width and height are positive
integers and preserves them exactly. Before real dispatch, the Forge adapter
must expose supported operation, model/backend identity, alignment,
recommended selection step, known safe axis/pixel limits, and requested versus
effective dimensions. Studio must reject unsupported input with a structured
error or disclose explicit normalization before dispatch. It must not inherit
silent latent-floor behavior.

## Result delivery and model identity

`GeneratedResult.image_data_url` is optional only when the backend supplies an
owned internal `output_path`. `output_path` and `metadata_path` are internal
ownership records, not browser URLs. The generic presentation boundary strips
both paths, and this correction set adds no file-serving route.

`ModelSummary.is_mock` defaults to `false`. Deterministic mock catalog entries
declare `is_mock: true` explicitly.

## Validation state

- Current correction test suite: **63 green**.
- Correction loopback/WebSocket runtime: **PASS**, 21 of 21 checks.
- Exact mock standalone launch on `127.0.0.1:17865`: **PASS**, including
  explicit mock-model selection, empty-prompt generation, ignored-setting
  notice, exact 520x776 dimensions, legacy polling, and port release.
- Socket-free deterministic demo: **PASS**.
- No Forge/Neo/Gradio launch, CUDA initialization, real model loading, real
  generation, package operation, dependency change, or external network
  access occurred.

## Evidence

- `Evidence/studio-pre-adapter-corrections/opus-findings-disposition.md`
- `Evidence/studio-pre-adapter-corrections/contract-readiness.md`
- `Evidence/studio-pre-adapter-corrections/dimension-capability-audit-summary.md`
- `Evidence/studio-pre-adapter-corrections/runtime-validation.json`
- `Evidence/studio-pre-adapter-corrections/test-summary.txt`
- `docs/studio/STUDIO_DIMENSION_CAPABILITY_AUDIT.md`

## Remaining gates

The Gradio 4.40.0 / Pillow 12.3.0 dependency conflict remains unresolved. No
real `ForgeBackendAdapter` exists, and Runtime Unblock has not begun. Runtime
capability checks for representative architectures, unaligned dimensions,
hires/img2img behavior, extension mutation, and safe model/device ceilings
remain future owner-gated work.

The interface migration direction remains:

```text
baseline Neo UI
-> dual-shell period
-> Studio default with optional Neo compatibility
-> complete Neo UI removal
```

These corrections strengthen the owned service, contract, capability, and
presentation boundaries without coupling Studio to Neo UI construction.
