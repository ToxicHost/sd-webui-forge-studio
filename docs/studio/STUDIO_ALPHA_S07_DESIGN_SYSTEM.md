# Studio Alpha S0.7 - Corrective source-faithful Canvas port

> **Superseded presentation record:** The handcrafted S0.7 implementation
> described by this milestone was replaced by the exact canonical Studio 4.17
> frontend. The current authoritative record is
> `docs/studio/STUDIO_ALPHA_S07_CANONICAL_FRONTEND.md`. Application, backend,
> lifecycle, cancellation, security, and rollback contracts remain preserved.

## Status and outcome

The owner rejected the existing S0.7 reconstruction as visually
non-equivalent. The problem is structural, not merely a blue-versus-yellow
theme choice: spacing, typography, control density, tool inventory, document
layout, panel proportions, and visible copy all differ from the live Forge
Studio interface.

S0.7 is therefore a corrective source-faithful port. The complete canonical
local Forge Studio 4.17 Canvas frontend has now been copied and served while
preserving the owned Studio application, backend, lifecycle, and loopback
security boundaries. Static source fidelity and the mock-backed runtime path
pass validation. This remains a mock-backed UI milestone, not a
real-inference milestone.

S0.7 is not visually accepted yet. No screenshot-parity pass is claimed.

## Pinned source authority

The primary presentation authority is the local snapshot under:

```text
Reference/Forge-Studio-main/Forge-Studio-main/frontend/**
Reference/Forge-Studio-main/Forge-Studio-main/javascript/ag-psd.js
```

The snapshot records:

- `version.json` source commit:
  `b316a4d87abd69837cd723403a7a41e22f97482d`
- frontend loader/cache identity: `4.17.0`
- frontend files: 63
- mounted source files, including `ag-psd.js`: 64

Pinned SHA-256 values:

```text
index.html
7de97b2a827398f7c1a3888a620e0191e08bb9c5e658a5b67791f6850ac352e8

app.css
396c453f8911be6f4dcf303a1d2130c7adb2989c60699142ea4ad1679e97dca5

studio-docs.css
6ec0d34cb89841f0c1e727803db63fe465f09be4c09f9c2167844db841fa1d4f

complete 63-file frontend manifest
68a3a0b1649782dfab37c3e4b1fe947e9fe67232aa393f4efda0d0c2183fb737

mounted 64-file manifest, including ag-psd.js
0ca2355eab41a9200bccd77fa9aef66fae2b19727ea7f98892b77d36ceefdc90
```

These identifiers pin the workspace snapshot; they do not claim that it is
the latest upstream release.

Authority order for S0.7 is:

1. the pinned local frontend source for exact presentation and interaction
   structure;
2. owner feedback and owner-captured live/reference screenshots for rendered
   validation;
3. owned application, backend, privacy, and loopback security contracts;
4. `Evidence/design/studiodesign.pdf` for broader product intent and
   historical design context.

The PDF is not the primary authority for reconstructing Canvas. Source
authority governs frontend presentation; it does not override the owned
backend or security boundaries.

## Exact source-copy result

The canonical frontend is mirrored as source, not approximated:

- source HTML, CSS, JavaScript, SVG, icons, copy, ordering, spacing, and module
  layout are preserved;
- all 63 files in the canonical frontend tree and the referenced `ag-psd.js`
  asset are present;
- the served canonical `index.html` and `app.css` match the source bytes;
- Studio-owned route translation and backend adaptation remain outside the
  copied frontend tree;
- mock-backend details are not dependencies of the canonical UI.

The static source-copy gate passes. Future changes must not normalize CSS,
substitute icons, reword labels, reorder the DOM, or recreate the interface
from screenshots. Any unavoidable source exception must be documented and
the affected hashes regenerated before the result can continue to be called
source-faithful.

## Canonical Canvas geometry

The pinned CSS establishes these concrete layout values:

| Area | Canonical value |
|---|---:|
| application titlebar | 38 px |
| left tool rail | 42 px |
| document strip | 26 px |
| right generation panel | 360 px base |
| height-responsive panel widths | 380, 400, or 440 px |
| regular control height | 30 px |
| compact control height | 28 px |
| standard control gap | 6 px |
| section padding | 12 px |
| positive prompt at the 960 px reference height | 48 px |
| negative prompt at the 960 px reference height | 32 px |
| default accent | `#7b8fff` |

The canvas is a viewport-filling workspace rather than a centered bounded
card. The canonical source does not add the reconstructed Paint/Inpaint
segmented control.

The exact canonical tool order is:

```text
brush, eraser, eyedropper
fill, gradient, shape, text
smudge, blur, dodge, clone, liquify, pixelate
select, ellipse, lasso, wand, crop, transform
```

The classic Session strip is a conditional 192 px column. Acceptance captures
must state whether it is enabled so that screenshots compare the same layout.

## Owned backend adapter boundary

The presentation and application responsibilities remain separated:

```text
Pinned Forge Studio 4.17 frontend
-> Studio-owned source API/route adapter
-> StudioPresentation
-> StudioApplication
-> BackendAdapter
-> MockBackend
```

The copied source expects Forge Studio routes such as `/studio/models`,
`/studio/generate`, `/studio/interrupt`, `/studio/prefs`, and `/studio/ws`,
plus conditional model, workflow, progress, extension, and settings routes.
The existing Studio mock presentation exposes an owned `/api/*` contract and
an asynchronous job lifecycle. The implemented source API/route adapter
translates the behavior needed for the demonstrated mock workflow while
returning honest unavailable responses for unsupported behavior.

`StudioApplication`, `BackendAdapter`, and `MockBackend` remain UI-independent.
New Studio behavior must not couple to Neo UI construction, Gradio component
IDs, Neo JavaScript globals, tabs, events, or layout modules. Neo remains the
baseline and rollback surface.

## Behavior in S0.7

The accepted workflow remains deliberately narrow:

- select a deterministic mock model;
- enter positive and negative prompts;
- set the existing contract-backed generation values;
- submit one mock generation;
- observe queued, running, and terminal state;
- interrupt a cancellable mock job;
- display deterministic mock result and metadata;
- recover after cancellation or controlled failure.

Canonical controls without an owned behavior contract may be visible because
they are part of the exact source. They must not silently mutate state or
enter the generation payload. Unsupported routes and actions require an
honest, deterministic unavailable treatment at the adapter boundary.

## Security, privacy, and local assets

Existing loopback Host validation, same-origin mutation checks, security
headers, cancellation semantics, and import isolation remain requirements.

The source-compatible Content Security Policy is implemented without
rewriting the canonical frontend:

- canonical inline loader and inline event behavior are authorized by their
  exact hashes;
- inline styles are allowed because the canonical source uses inline style
  attributes;
- style, font, script, and connection sources remain local/self;
- `connect-src` remains restricted to `'self'`.

The byte-identical source retains its Google Fonts link, but policy blocks
both Google origins before loading. DM Sans and JetBrains Mono are not present
in the pinned local snapshot, so system fallbacks change text metrics. This is
a visual-fidelity limitation rather than a functional blocker; final
typography remains owner-gated.

No network access, package installation, dependency change, or external cache
inspection is authorized by this milestone.

## Validation and owner visual gate

The socket-forbidden validation suite passes all 49 tests. It verifies the
complete source copy, pinned manifests, canonical geometry and copy, exact
19-tool ordering, adapter behavior, application lifecycle, cancellation, Host
and Origin validation, security headers, and import isolation.

The separate loopback source runtime probe also passes:

- canonical `index.html` and `app.css` are served byte-for-byte;
- all 19 tools are present in canonical order;
- model discovery routes respond through the mock adapter;
- generation, cancellation, controlled failure, recovery, and repeated
  generation succeed;
- the WebSocket upgrade returns `101`;
- a foreign Host is rejected with `403`;
- shutdown is clean and the port is released.

No external network was contacted. No real Forge, Torch, CUDA, model, or
generation runtime was imported or initialized.

These static and runtime passes do not prove rendered visual equivalence.

Final acceptance requires an owner-captured full-window comparison using a
pinned viewport, browser zoom, device-pixel ratio, theme, panel width, Session
strip state, prompt contents, model selection, and document state. The
recommended comparison viewport is 2048 x 960 at 100% zoom. The capture must
be compared with both the canonical live view and the owner-provided
screenshots.

For the owner's black-and-amber live reference, select the source `Liam`
theme, use the Classic layout with the Classic Session strip enabled, and
dismiss the source first-run overlay before capture. The source `Studio`
theme is the blue/periwinkle default and should not be mistaken for a
geometry difference.

No suitable browser renderer is currently available inside the workspace.
The unresolved local-font condition also affects text metrics. Until the owner
supplies and accepts the rendered comparison, the visual gate remains open
and no screenshot or pixel-parity pass is claimed.

## Evidence

- `Evidence/studio-alpha-s07-canonical/source-manifest.txt`
- `Evidence/studio-alpha-s07-canonical/source-audit.md`
- `Evidence/studio-alpha-s07-canonical/websocket-validation.json`
- `Evidence/studio-alpha-s07-canonical/runtime-validation.md`
- `Evidence/studio-alpha-s07-canonical/test-summary.txt`
- `Evidence/studio-alpha-s07-canonical/visual-acceptance.md`
- `Evidence/studio-alpha-s07-canonical/privacy-review.md`
- `Evidence/studio-alpha-s07/design-authority.md`
- `Evidence/studio-alpha-s07/visual-acceptance-checklist.md`
- `Evidence/studio-alpha-s07/reference-manifest.txt`
- `Evidence/studio-alpha-s07/runtime-validation.json`
- `Evidence/studio-alpha-s07/test-summary.txt`
- `Evidence/studio-alpha-s07/demo-report.json`
- `Evidence/studio-alpha-s07/results/`
- `Evidence/design/rendered/`

## Architecture and Neo migration

Studio remains independently launchable and interface-independent from Neo.
The migration direction remains:

```text
baseline Neo UI
-> dual-shell period
-> Studio default with optional Neo compatibility
-> complete Neo UI removal
```

Neo UI removal must not require replacing the Neo inference backend at the
same time.

## Explicitly outside S0.7

S0.7 does not authorize:

- real Forge adapter implementation;
- Forge or Neo launch;
- Torch or CUDA initialization;
- model discovery or loading;
- real image generation;
- dependency installation or modification;
- network metadata research;
- later Studio phases.
