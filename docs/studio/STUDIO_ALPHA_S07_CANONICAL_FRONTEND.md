# Studio Alpha S0.7 - Canonical Studio 4.17 frontend

## Outcome

Studio Alpha S0.7 now uses the canonical Forge Studio 4.17 frontend as its
presentation foundation. The earlier handcrafted S0.7 approximation was
removed because its spacing, control density, copy, tool inventory, document
strip, canvas composition, and inspector geometry did not match the live
product.

This milestone preserves the deterministic mock vertical slice. It does not
launch Forge or Neo, construct Gradio UI, initialize Torch or CUDA, load a
real model, or generate a real image.

The reviewed feature branch was fast-forward integrated into
`docs/phase0-complete-baseline` at
`882c90f3fb587938b300e3d7d7fa5e9d30b1edfd`.

Owner visual acceptance is **APPROVED**. This decision supersedes the earlier
pending and owner-gated language in the milestone documents.

## Canonical authority and manifest

The bounded workspace-local source authority is:

```text
Reference/Forge-Studio-main/Forge-Studio-main/frontend/**
Reference/Forge-Studio-main/Forge-Studio-main/javascript/ag-psd.js
```

The canonical loader/cache identity is `4.17.0`, and the snapshot records
source commit:

```text
b316a4d87abd69837cd723403a7a41e22f97482d
```

Independent byte comparison established:

| Set | Files | Bytes | Outer SHA-256 |
|---|---:|---:|---|
| canonical frontend | 63 | 22,819,099 | `68a3a0b1649782dfab37c3e4b1fe947e9fe67232aa393f4efda0d0c2183fb737` |
| mounted frontend plus `ag-psd.js` | 64 | 23,716,637 | `0ca2355eab41a9200bccd77fa9aef66fae2b19727ea7f98892b77d36ceefdc90` |

All 64 path, byte-size, raw-byte, and SHA-256 comparisons pass. Repository
attributes preserve the canonical LF byte identity and prevent binary assets
from text conversion.

The physical handcrafted `studio.css` and `studio.js` files are deleted.
Their old compatibility URLs map to canonical `app.css` and `app.js`; the
canonical document does not load or depend on the deleted files.

Static graph analysis found 58 of 64 mounted files reachable from the current
startup graph. The six inert files are retained because they belong to the
exact canonical source snapshot:

- both logo lockups;
- `EnglishDictionary.csv`;
- `e621_sfw.csv`;
- `extra-quality-tags.csv`;
- `fonts/OFL.txt`.

## Owned architecture boundary

```text
Canonical Studio 4.17 frontend
-> loopback HTTP/WebSocket presentation and source-route bridge
-> StudioPresentation
-> StudioApplication
-> BackendAdapter
-> MockBackend
```

The browser does not import or call `MockBackend`. HTTP and WebSocket paths
reach backend behavior through `StudioPresentation` and `StudioApplication`.
Studio Python sources do not import Neo UI, Gradio UI construction, Torch,
CUDA, Forge generation services, or model code.

The canonical frontend remains an immutable consumer. Compatibility behavior
is owned by `forge_studio/source_api_adapter.py`, outside the copied frontend
tree.

## Source-compatible route surface

The bridge supplies the boot and demonstrated workflow routes used by the
canonical shell:

- models, current model, model status, load/unload/refresh model;
- samplers, schedulers, upscalers, VAEs, text encoders, and mock VRAM status;
- preferences and defaults;
- generation, progress, task identity, interrupt, and skip;
- LoRA, embedding, wildcard, extension, workflow, layout, and optional-model
  inventories;
- dynamic-prompt status, live-mode status, gallery scans, and update status;
- `/sdapi/v1/options` and `/sdapi/v1/progress`;
- `/studio/ws`.

Unavailable mock-only behavior is explicit. Auto-unload and VRAM reservation
return `available: false`; live mode, update, filesystem selection, and
filesystem-opening actions return controlled unavailable responses. Session
cleanup reports that no server-side session state exists. Unknown source
routes return controlled JSON `404` responses.

Process-local preferences and defaults are deliberate for this milestone; no
new persistence system is created.

## WebSocket and lifecycle behavior

The standard-library RFC 6455 implementation uses the same loopback Host and
Origin boundary as HTTP. Focused validation proves:

1. loopback WebSocket connection succeeds;
2. a foreign Host is rejected;
3. a foreign Origin is rejected;
4. canonical progress is ordered;
5. cancellation becomes terminal;
6. failure is controlled;
7. recovery and a second generation succeed;
8. disconnecting an observer does not consume or corrupt job state;
9. two observers receive non-consuming progress;
10. legacy polling remains functional.

No third-party WebSocket package is introduced.

## Legacy rollback surface

The rollback-compatible endpoints remain:

```text
GET  /api/status
GET  /api/models
POST /api/generate
GET  /api/jobs/<job-id>
POST /api/jobs/<job-id>/cancel
```

They traverse the same owned presentation, application, and backend
contracts. The canonical frontend does not depend on the legacy surface, so
the two shells remain separable.

## Static serving and security

Static requests resolve beneath the canonical frontend root. Traversal
segments, encoded traversal, absolute paths, and resolved paths outside that
root are rejected. Host validation precedes HTTP and WebSocket routing.
Mutation routes and WebSocket upgrades enforce the loopback Origin policy.

The CSP authorizes only the exact canonical runtime requirements:

- local/self scripts plus the pinned loader and single inline-handler hashes;
- local/self fonts;
- local/self connections;
- local images plus `data:` and `blob:`;
- canonical inline style attributes;
- no object, frame-ancestor, or base-URI surface.

The corrected loader hash is:

```text
sha256-XSQ68riRv/PGZ/2WU/cJQKiJSH+ki53ZjqMsIQyR/eU=
```

The previous wrong hash allowed the HTML and CSS to render but blocked the
inline loader. The visible symptom was a static shell with non-contextual
controls, absent optional modules, and a model selector stuck in its loading
state. Pinning the exact loader bytes restored the canonical scripts without
adding `unsafe-inline` to `script-src`.

## Font limitation

The byte-identical `index.html` retains its Google Fonts stylesheet link, but
the local-only response CSP does not authorize either Google origin. No
remote font request or download is part of this milestone.

DM Sans and JetBrains Mono are not bundled in the canonical snapshot. The
canonical CSS therefore uses its existing system fallbacks. This does not
block application behavior. The owner accepted the presentation with fallback
metrics as a documented non-blocking limitation. The bundled Playfair Display
assets remain local.

## Validation result

- 49 socket-forbidden tests pass.
- Complete 63-file and mounted 64-file manifest assertions pass.
- All 64 workspace-local source comparisons are byte-identical.
- A separate 16-check loopback/WebSocket validator passes.
- Two deterministic mock models populate the canonical selector.
- Deterministic mock success, progress, cancellation, controlled failure,
  recovery, observer disconnect, repeated generation, and legacy polling
  pass.
- Exact standalone launch on unused `127.0.0.1:17865` serves the canonical
  shell and model route.
- Socket-free `launch_studio.py --demo` passes.
- In-process shutdown and port-release checks pass.
- Neo parity remains unchanged.

No test contacted an external network or used a package manager. No new
rendered screenshot was captured.

## Owner visual acceptance

Status: **APPROVED**

The owner accepted the canonical layout, contextual toolbar, module tabs,
model population, viewport, inspector structure, and overall presentation as
the Studio foundation. That direct decision, recorded in the Opus review
bundle, supersedes the earlier `OWNER_REVIEW_REQUIRED` checklist state.

For a like-for-like black-and-amber comparison, use the canonical Liam theme,
Classic layout, and Classic Session strip. The blue/periwinkle Studio theme
is the canonical default and is not evidence of a geometry difference.

## Opus review and pre-adapter disposition

The Opus review found the canonical frontend preservation, owned application
boundary, and contained HTTP/WebSocket surface safe to preserve and integrate.
It also required contract-foundation corrections before a real
`ForgeBackendAdapter`; the current correction set applies them.

The pre-adapter correction set records:

- A1: infotext contains only owned request/result facts and a resolved seed;
- A2: percent and fractional progress use explicit independent scales;
- A3: recognized unsupported canonical parameters produce a visible notice;
- A4: observer polling does not materialize or serialize completed images;
- A5: model catalog and current-model reads are pure; explicit load selects;
- A6: empty positive prompts, including negative-only requests, are valid;
- A7: dimensions are backend capabilities, not a universal multiple-of-64 or
  fixed-maximum rule.

The deterministic mock accepts positive-integer width and height and preserves
them exactly. A future real adapter must report operation/model capability,
alignment, safe limits, requested and effective dimensions, and must reject
unsupported requests or disclose normalization before dispatch:

```text
FORGE_ADAPTER_CAPABILITY_REQUIRED = YES
```

`GeneratedResult` may carry an inline data URL or an owned internal output
path. Internal paths are stripped at the browser boundary and no file-serving
route was added. `ModelSummary.is_mock` defaults to `false`; mock catalog
entries opt in with `is_mock: true`.

The current correction suite is **63 green**. The expanded contained
loopback/WebSocket runtime is **PASS** with 21 of 21 checks. Exact mock
standalone launch on `127.0.0.1:17865`, socket-free demo, and clean port
release also pass. These are deterministic mock results, not a Forge launch
or real image claim.

## Evidence

- `Evidence/studio-alpha-s07-canonical/source-manifest.txt`
- `Evidence/studio-alpha-s07-canonical/source-audit.md`
- `Evidence/studio-alpha-s07-canonical/websocket-validation.json`
- `Evidence/studio-alpha-s07-canonical/runtime-validation.md`
- `Evidence/studio-alpha-s07-canonical/test-summary.txt`
- `Evidence/studio-alpha-s07-canonical/visual-acceptance.md`
- `Evidence/studio-alpha-s07-canonical/privacy-review.md`
- `Evidence/studio-pre-adapter-corrections/opus-findings-disposition.md`
- `Evidence/studio-pre-adapter-corrections/contract-readiness.md`
- `Evidence/studio-pre-adapter-corrections/dimension-capability-audit-summary.md`
- `Evidence/studio-pre-adapter-corrections/runtime-validation.json`
- `Evidence/studio-pre-adapter-corrections/test-summary.txt`

## Supersession and next boundary

This document supersedes the handcrafted presentation portions of the prior
S0.7 design-system record. `StudioApplication`, `BackendAdapter`,
`MockBackend`, concurrency, cancellation, and rollback contracts remain
preserved.

The Neo migration direction remains:

```text
baseline Neo UI
-> dual-shell period
-> Studio default with optional Neo compatibility
-> complete Neo UI removal
```

The owner visual gate, reviewed-branch integration, and deterministic
pre-adapter mock runtime are complete. The Gradio/Pillow dependency conflict
remains unresolved, and no real adapter or Runtime Unblock has begun.
Controlled Forge launch, `ForgeBackendAdapter`, real model loading, CUDA
initialization, first real image generation, and any fresh ResolvePlan remain
separately gated.
