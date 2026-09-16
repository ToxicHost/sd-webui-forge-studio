# Studio Alpha S0.6 — Foundation and parity shell

> **Superseded visual milestone:** S0.6 structural, application, concurrency,
> Host-validation, accessibility, and mock-lifecycle work is preserved by
> S0.7. Its orange dashboard visual system was rejected and replaced by the
> owner-supplied PDF design language. Do not integrate S0.6 as the final
> presentation. See `STUDIO_ALPHA_S07_DESIGN_SYSTEM.md`.

## Goal

S0.6 preserves the standalone Studio application boundary, corrects the two
concurrency defects identified by the independent Opus review, hardens the
loopback HTTP surface, and replaces the reduced S0.5 presentation with a
substantially complete Canvas workspace shell.

This remains a deterministic mock milestone. It adds no Forge adapter or real
inference behavior.

## Opus review disposition

Implemented now:

- F-01: removed the presentation-wide application lock;
- F-02: changed mock progress to an idempotent current-state observation;
- F-06: validate Host on GET, HEAD, and POST;
- F-07: removed the invented Create/Edit/img2img product switcher;
- F-08: separated AA secondary-label color from disabled text;
- F-09: made key unavailable controls focusable and described;
- F-10: kept live prompt counters outside their labels; and
- F-15: enter busy state before awaiting submission.

Deferred, as directed: F-03, F-04, F-05, F-11 through F-14, and F-16 through
F-19. F-20 is bounded by the local Reference manifest below; historic upstream
identity and current-live visual identity remain unverified without network or
owner screenshot evidence.

## Concurrency and polling semantics

`StudioPresentation` no longer serializes application calls. Each
`BackendAdapter` implementation owns its synchronization and must support
concurrent public calls, including status and cancellation during submission.

`MockBackend` uses monotonic elapsed time, with an injectable deterministic
clock for tests. A progress read:

- does not consume or advance an event;
- gives concurrent observers the same logical snapshot;
- never moves sequence or state backward;
- is independent of poll count;
- does not gate result availability; and
- cannot produce completion after a terminal cancellation.

The default interval keeps queued/running/completed states visible in the mock
interface without a background worker or event framework.

## Loopback trust boundary

Every GET, HEAD, and POST request must carry exactly one Host value matching the
active listener:

- `127.0.0.1:<active-port>`; or
- `localhost:<active-port>`.

Other, missing, or wrong-port Host values are rejected before static or API
data is served. The existing same-origin POST rule and security headers remain.

## Active Reference source and identity

The bounded active source is:

- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/index.html`;
- its directly mounted `frontend/**` assets; and
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_api.py` for the
  `/studio` and `/studio/static` route chain.

Local identity evidence:

- `version.json` commit:
  `b316a4d87abd69837cd723403a7a41e22f97482d`;
- loader/cache version: `4.17.0`;
- canonical `index.html` SHA-256:
  `7de97b2a827398f7c1a3888a620e0191e08bb9c5e658a5b67791f6850ac352e8`;
- complete 63-file frontend manifest SHA-256:
  `68a3a0b1649782dfab37c3e4b1fe947e9fe67232aa393f4efda0d0c2183fb737`;
  and
- 64-file mounted-core manifest SHA-256:
  `0ca2355eab41a9200bccd77fa9aef66fae2b19727ea7f98892b77d36ceefdc90`.

The snapshot has no `.git` metadata. These facts pin the supplied local files,
not a historic upstream repository revision or a current-live product build.

## UI port strategy

S0.6 ports the Reference hierarchy and owner-directed orange-accent desktop
language while keeping behavior behind owned boundaries:

```text
Static Studio shell
        |
StudioPresentation HTTP/JSON adapter
        |
StudioApplication
        |
BackendAdapter
        |
MockBackend
```

The frontend calls only the four existing presentation routes. It has no Neo,
Gradio, Forge, CUDA, model-runtime, or backend-implementation dependency.
Reference scripts that assume Forge routes, WebSockets, canvas globals, remote
fonts, or optional modules were not copied.

Visible S0.6 regions:

- Forge Studio identity and Canvas, Develop, Gallery, Workshop, Wildcards,
  Codex, and Settings navigation;
- document strip, context toolbar, left tool rail, framed white canvas, bottom
  canvas toolbar, and synchronized dimensions;
- Session workspace with History, Layers, and Queue;
- Generate and Extensions tabs plus parameter search;
- positive prompt, wildcard affordance, LoRA stack, and negative prompt;
- Generate, Live, Interrupt, and Skip;
- workflow, Load Image, Reset, and Regions;
- Model, VAE, and Text Encoder;
- sampler, scheduler, steps, CFG, denoise, and seed;
- canvas width and height;
- Hires, Refiner, ADetailer, ControlNet, and Scripts; and
- backend, model, dimensions, state, and honest mock VRAM status.

## Enabled and unavailable behavior

Enabled through the existing application contract:

- mock model selection;
- positive and negative prompts;
- seed, steps, CFG, width, and height;
- Generate;
- Interrupt while a cancellable job exists;
- queued/running/completed/failed/cancelled projection;
- result preview and deterministic metadata.

All other product areas and controls are visible but unavailable. Key controls
remain keyboard focusable with `aria-disabled="true"` and
`aria-describedby`; native-disabled secondary fields also show an adjacent
Unavailable label. JavaScript prevents every `data-unavailable` action and
announces why it is not connected. No future behavior is simulated.

## Accessibility corrections

- Enabled labels use `--text-label` or `--text-muted`, both tested at 4.5:1 or
  greater against all selected control/panel/root backgrounds.
- `--text-disabled` is separate and is not used for enabled labels.
- Key future controls are focusable and described; the tool rail uses a shared
  accessible explanation.
- Positive and negative prompt counters sit outside their labels.
- Supported fields enter busy state before the submit request is awaited.
- Focus-visible and reduced-motion behavior are retained.

This is a focused correction, not an accessibility certification.

## Launch and validation

From `app/`:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py
```

Socket-free demonstration:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py --demo
```

Complete focused suite:

```powershell
.\venv\Scripts\python.exe -I -S -B .\tests\studio_alpha\run_tests.py
```

Result: 34 tests passed. The suite covers import isolation, application
behavior, non-consuming progress, two observers, slow-submit responsiveness,
concurrent cancellation, Host and Origin rules, the S0.6 shell contract,
enabled-control mapping, unavailable-control accessibility, contrast, busy
ordering, and local-only assets.

Runtime evidence also demonstrates the parity shell, success, running
progress, cancellation, controlled failure, recovery, repeat generation, two
concurrent HTTP observers, Host rejection, and clean shutdown.

## Evidence

- `Evidence/studio-alpha-s06/demo-report.json`
- `Evidence/studio-alpha-s06/loopback-validation.json`
- `Evidence/studio-alpha-s06/concurrency-probe.json`
- `Evidence/studio-alpha-s06/reference-identity.md`
- `Evidence/studio-alpha-s06/validation-summary.md`
- `Evidence/studio-alpha-s06/visual-acceptance.md`
- `Evidence/studio-alpha-s06/results/`
- `Evidence/studio-alpha-s06/loopback-results/`

## Visual acceptance

No current-live or S0.5 screenshot was present in the Opus review bundle, and
no permitted workspace-local renderer was available. No rendering tool was
installed. Every visual checklist item is therefore
`OWNER_REVIEW_REQUIRED`; static structure is not claimed as pixel proof.

Known review points are exact density and proportions, the owner-directed
orange theme against the live product, system-font metrics, and the mock canvas
projection. The full checklist is in
`Evidence/studio-alpha-s06/visual-acceptance.md`.

## Performance evidence

Not applicable. S0.6 performs no real inference and makes no performance claim.
The bounded concurrency timings are correctness evidence only.

## New or changed risks

- The interface is structurally fuller but still mock-only.
- The current-live visual target and Reference historic upstream identity are
  not independently pinned.
- The preserved Gradio/Pillow conflict still blocks Runtime Unblock.
- Deferred Opus findings remain documented above.

## Decisions made

- Preserve the owned application architecture.
- Keep synchronization inside adapters and progress reads non-consuming.
- Port product hierarchy without copying Forge-coupled runtime modules.
- Enable only the eight request fields plus Generate and conditional
  Interrupt.
- Keep all future surface area honest and accessible.
- Use workspace-local/system fonts and no CDN.
- Require human rendered acceptance before integration.

## Open questions

- Does the owner accept the rendered S0.6 shell against the current-live
  product reference?
- Which approved dependency correction will permit Runtime Unblock?

## Uncommitted changes

None are intended at milestone handoff; verify with `git status --short`.

## Next single task

After owner visual acceptance, complete Runtime Unblock. Then implement
`ForgeBackendAdapter` through the preserved application seam and prove the
first real image without coupling Studio to Neo UI construction.

Do not integrate S0.6 or begin the real adapter before that acceptance.
