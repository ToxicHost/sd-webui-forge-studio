# Phase 0 Dependency Conflict Analysis

## Scope and status

This is a filesystem-local, read-only dependency analysis of the preserved
Attempt 3 environment. It used only files under `Studio-Standalone`.

No package operation, network access, dependency change, product-code change,
launch, model load, or generation was performed. All resolution language below
is analysis or recommendation, not an implemented change.

## 1. Exact conflicting requirements

The installed environment has no Pillow version that can satisfy both of its
mandatory bounds:

| Owner | Requirement |
|---|---|
| Gradio 4.40.0 installed metadata | `pillow>=8.0,<11.0` |
| pillow-heif 1.4.0 installed metadata | `pillow>=11.1.0` |
| Repository direct pin | `Pillow==12.3.0` |
| Installed Pillow | 12.3.0 |

The mandatory metadata intersection is empty:

```text
Pillow < 11.0 AND Pillow >= 11.1.0
```

Attempt 3 therefore exposed a genuine dependency-set conflict, not merely a
warning caused by an unrelated package.

## 2. Repository request for Pillow 12.3.0

`requirements.txt:2` directly requests:

```text
Pillow==12.3.0
```

The same repository also directly requests:

- `pillow-heif==1.4.0` at `requirements.txt:22`; and
- `pillow-jxl-plugin==1.3.8` at `requirements.txt:23`.

`modules/launch_utils.py:429-431` installs `requirements.txt` when its owned
requirement check fails. Its parser recognizes `==`, but
`requirements_met()` at lines 250-285 only rejects an installed version when
it is lower than the requested version. It does not enforce equality and does
not validate transitive dependency compatibility.

## 3. Installed package requiring Pillow below 11

The installed file:

```text
venv/Lib/site-packages/gradio-4.40.0.dist-info/METADATA
```

records:

```text
Name: gradio
Version: 4.40.0
Requires-Dist: pillow<11.0,>=8.0
```

The repository does not request Gradio in `requirements.txt`.
`modules/launch_utils.py:295` separately owns the default:

```python
gradio_package = os.environ.get(
    "GRADIO_PACKAGE",
    "gradio==4.40.0 gradio_rangeslider==0.0.8",
)
```

Lines 423-424 install that package set only when Gradio is absent. The
subsequent requirements transaction upgrades Pillow to the repository pin
without reconsidering Gradio's upper bound.

## 4. Other installed Pillow constraints

Mandatory installed constraints are:

| Installed distribution | Pillow constraint | Effect |
|---|---|---|
| pillow-heif 1.4.0 | `pillow>=11.1.0` | Conflicts directly with Gradio |
| scikit-image 0.25.2 | `pillow>=10.1` | Allows 12.3; excludes older Pillow |
| matplotlib 3.11.1 | `pillow>=9` | Allows 12.3 |
| imageio 2.37.4 | `pillow>=8.3.2` | Allows 12.3 |
| torchvision 0.26.0+cu130 | `pillow!=8.3.*,>=5.3.0` | Allows 12.3 |
| diffusers 0.37.1 | `Pillow` | No version bound |
| facexlib 0.3.0 | `Pillow` | No version bound |
| pillow-jxl-plugin 1.3.8 | `pillow` | No version bound |

Optional-extra metadata also mentions Pillow:

| Installed distribution | Optional constraint |
|---|---|
| contourpy 1.3.3 | Unbounded Pillow for the `test` extra |
| huggingface-hub 0.36.2 | Unbounded Pillow for testing, all, and dev extras |
| networkx 3.6.1 | `pillow>=10` for the `doc` extra |
| transformers 4.57.6 | `Pillow>=10.0.1,<=15.0` for vision/development extras |

The optional markers do not create the Attempt 3 conflict. The mandatory
pillow-heif lower bound does.

## 5. Source locations using Pillow APIs

A repository source scan found 54 Python files importing Pillow:

| Source area | Importing files |
|---|---:|
| `backend/` | 2 |
| `modules/` | 16 |
| `modules_forge/` | 2 |
| `scripts/` | 3 |
| `extensions-builtin/` | 31 |

Important owned locations include:

| Source | Relevant behavior |
|---|---|
| `modules/images.py:19-30` | Imports the JXL plugin, registers the HEIF opener, and selects Pillow resampling modes |
| `modules/images.py:575-629` | Saves PNG, JPEG, WebP, AVIF, JXL, GIF, EXIF, and generation metadata |
| `modules/ui_tempdir.py` | Overrides Gradio's Pillow cache-save path and preserves PNG text metadata |
| `modules/api/api.py` | Converts API image payloads to and from Pillow images |
| `backend/misc/image_resize.py:86` | Uses `Image.Resampling.LANCZOS` |
| `modules/masking.py`, `modules/processing.py`, `modules/images.py` | Composite, resize, filter, mask, and format operations |
| `extensions-builtin/sd_forge_controlnet/` | Canvas/API image conversion and preprocessing |
| `extensions-builtin/forge_legacy_preprocessors/` | Broad legacy Pillow transforms and dataset adapters |

Observed Pillow APIs include image open/save/convert, array conversion,
composition, alpha composition, registered formats, PNG text metadata, EXIF
orientation, filters, drawing, fonts, and old and new resampling constants.

One legacy preprocessor still calls `Image.ANTIALIAS` at:

```text
extensions-builtin/forge_legacy_preprocessors/annotator/zoe/zoedepth/utils/misc.py:349
```

That path requires explicit compatibility coverage regardless of which
resolution is selected; it was not reached in Attempt 3.

## 6. Source locations depending on Gradio 4.40 behavior

A repository source scan found 66 Python files importing Gradio:

| Source area | Importing files |
|---|---:|
| `modules/` | 38 |
| `modules_forge/` | 4 |
| `scripts/` | 6 |
| `extensions-builtin/` | 18 |

The dependency is deeper than public components:

| Source | Gradio behavior or internal surface used |
|---|---|
| `modules_forge/patch_basic.py` | Imports and replaces `gradio.networking.url_ok` |
| `modules/gradio_extensions.py` | Patches `gradio.blocks.Block`, `BlockContext`, `Blocks`, component constructors, component metadata, and event dependencies |
| `modules/ui_tempdir.py` | Replaces `gradio.processing_utils` cache functions and reads Gradio file/cache internals |
| `modules/ui_gradio_extensions.py` | Replaces `gr.routes.templates.TemplateResponse` |
| `modules/ui.py` | Replaces Gradio version/IP helpers and constructs the Neo Blocks UI |
| `modules/ui_components.py` | Subclasses Gradio components/layouts and relies on `_js` event compatibility |
| `modules_forge/main_entry.py` | Imports `gradio.context.Context`, accesses `Context.root_block`, and returns `gr.update()`/`gr.skip()` results |
| `webui.py:90-135` | Builds, queues, and launches `Blocks`, then edits returned ASGI middleware |

The rest of the UI and built-ins rely extensively on component constructor
arguments, `elem_id`, layout context managers, event chaining, `gr.update`,
`gr.skip`, queue behavior, Gallery/Image serialization, and JavaScript event
bridges.

Consequently, a Gradio version change is a compatibility migration, not a
metadata-only pin change.

## 7. Forge Studio reference assumptions

The in-workspace reference does not independently pin Pillow or Gradio.
`install.py` installs only `imageio-ffmpeg` and `imagehash` when absent, so it
inherits the Neo environment.

Relevant assumptions are:

| Reference source | Assumption |
|---|---|
| `scripts/studio.py` | Imports real Gradio and creates a minimal redirect `Blocks` tab |
| `scripts/studio_bridge_init.py` | Uses a real `Blocks` context while installing Studio component stubs |
| `scripts/studio_gradio_stub.py` | Monkey-patches the already imported real Gradio module and preserves the original `Blocks` class |
| `scripts/studio_api.py:1619-1709` | Maps real Gradio class names and stub component/layout behavior into Studio manifests |
| `scripts/studio_generation.py` | Imports both Gradio and Pillow while adapting Neo processing |
| `scripts/studio_api.py` | Uses Pillow for previews, EXIF, PNG metadata, JPEG subsampling, ICC parsing/conversion, and raw RGBA transport |
| `scripts/studio_gallery.py` | Uses Pillow for thumbnails, orientation, metadata, hashing inputs, and image export |

`scripts/studio_api.py:65-79` explicitly records that a Pillow upgrade once
changed embedded ICC output. Pillow behavior is therefore artifact-sensitive
for Studio, not just an import prerequisite.

The reference currently assumes coexistence with Neo and contains Gradio
bridge code. That is compatibility evidence, not the intended final
architecture. New Studio code must remain behind owned service, API, state,
and adapter boundaries so the Neo UI and these Gradio-specific bridges can be
removed later.

## 8. Resolution option A: lower the Pillow pin

### Shape

Change the direct Pillow pin to a release accepted by Gradio 4.40.0.

### Constraint consequence

Lowering only `Pillow==12.3.0` cannot succeed. Any Pillow below 11 violates the
installed mandatory `pillow-heif>=11.1.0` requirement.

A viable form of option A would therefore also require an owner-approved
pillow-heif downgrade, replacement, removal, or isolation. No compatible
pillow-heif candidate exists in the inspected local metadata.

### Risks

- HEIF/AVIF registration and save/open behavior may regress.
- Python 3.13 wheel availability for a different pillow-heif release is
  unverified.
- JXL, EXIF, ICC, PNG metadata, WebP, and image resampling behavior would need
  comparison.
- Studio's color-managed and format-sensitive paths could change artifacts.
- Legacy preprocessors contain mixed-generation Pillow API usage.

### Benefit

It preserves the heavily patched Gradio 4.40 Neo UI surface if a compatible
codec stack can be proven.

## 9. Resolution option B: change the Gradio version

### Shape

Keep Pillow 12.3.0 and pillow-heif 1.4.0, and select a Gradio release whose
installed metadata admits Pillow 12.3.0.

### Current evidence limit

Only Gradio 4.40.0 metadata exists in the approved workspace. This analysis
cannot identify or approve a specific replacement version without separately
approved candidate metadata.

### Risks

- Neo directly patches private Gradio modules, functions, classes, metadata
  generation, events, cache movement, templates, and networking.
- Public component arguments, layout rules, event chaining, `gr.update`,
  `gr.skip`, queueing, launch return values, and ASGI behavior may differ.
- Built-in extensions and the current Studio bridge use real Gradio objects.
- A candidate may solve metadata while breaking runtime construction or
  frontend behavior.

### Benefit

It retains the repository's current image and codec stack and has a single
primary dependency-ownership point in `modules/launch_utils.py`.

## 10. Resolution option C: environment separation or dependency ownership

### Shape

Move interface dependencies behind owned process/service boundaries:

- the inference/application backend owns image and generation behavior;
- Studio owns its independent frontend and API adapter;
- the Neo Gradio shell becomes an optional compatibility adapter; and
- incompatible UI/image dependency sets, if still necessary, live in separate
  environments or processes.

Two Pillow versions cannot be safely owned by one Python process. Actual
version separation therefore requires a process boundary, not import tricks.

### Risks

- This is an architectural change, not a Phase 0 dependency repair.
- IPC/API schemas, state ownership, image serialization, metadata fidelity,
  process lifecycle, GPU ownership, and rollback all require design and tests.
- Premature implementation could disturb the preserved Neo control surface.

### Benefit

It directly supports the required migration:

```text
baseline Neo UI
→ dual-shell period
→ Studio-default with optional Neo compatibility
→ complete Neo UI removal
```

It also prevents the final Studio interface from inheriting Neo UI dependency
constraints.

## 11. Compatibility risks by option

| Option | Primary compatibility risk | Scope |
|---|---|---|
| A — lower Pillow | Codec/plugin incompatibility and changed image artifacts | Image backend, Neo workflows, Studio workflows |
| B — change Gradio | Breakage in patched internals, UI construction, events, routes, cache handling, and built-ins | Neo UI and current Studio compatibility bridge |
| C — separate ownership | New process/API/state topology and duplicated lifecycle concerns | Whole application architecture |

Option A is not a single-pin repair because of pillow-heif. Option B is
locally the narrowest satisfiable dependency direction but has a large
runtime compatibility matrix. Option C best matches the target architecture
but is too broad to substitute for Phase 0 baseline repair.

## 12. Neo-baseline implications

Neo remains the Phase 0 validation interface and rollback surface.

- Option A retains its expected Gradio version but changes image behavior and
  requires at least one additional codec dependency decision.
- Option B preserves the current image backend but can invalidate the Neo UI's
  private Gradio patches and component contracts.
- Option C must not be used to bypass completion of a reproducible Neo
  baseline. It belongs behind a later architecture gate.

Any accepted dependency change needs an explicit rollback to the preserved
Attempt 3 environment and must not add new Neo-only product behavior.

## 13. Studio-interface implications

The reference Studio code is Pillow-sensitive and currently contains both real
Gradio integration and a Gradio stub bridge.

- A Pillow change must preserve ICC, EXIF, PNG/JPEG/WebP/AVIF/JXL behavior and
  pixel fidelity.
- A Gradio change must preserve the current compatibility bridge only long
  enough to support baseline and rollback.
- New Studio architecture must not bind to Neo component IDs, layouts, events,
  JavaScript globals, tabs, or Gradio internals.
- Studio should consume owned services/APIs and become independently
  launchable.

The dependency decision must not make current reference coupling permanent.

## 14. Recommended resolution

**Recommendation only — not implemented: pursue option B as the Phase 0
candidate direction.**

Keep the repository-owned Pillow 12.3.0, pillow-heif 1.4.0, and JXL/image
stack. Evaluate an owner-approved Gradio candidate whose local metadata
explicitly permits Pillow 12.3.0, then require the complete Neo/Studio
compatibility test matrix below before changing the default.

Reasons:

1. Option A has no valid one-pin solution because pillow-heif requires
   Pillow 11.1 or newer.
2. Option A would expand into a codec-stack downgrade and threaten image
   format and Studio color/metadata behavior.
3. Option B keeps inference/image dependencies aligned with the current
   repository intent.
4. The Gradio risk is substantial but can be isolated as compatibility-shell
   work and prevented from entering new Studio architecture.

If no Gradio candidate passes, stop rather than combining unreviewed package
changes. Option C should then be designed as the longer-term architecture,
not improvised as a retry fix.

No exact Gradio candidate version is recommended by this local-only analysis.

## 15. Required tests before accepting a dependency change

### Dependency and provisioning

1. Provision a clean repository venv from the approved Python.
2. Record every resolved version and distribution source.
3. Require `pip check` exit 0 before importing `webui`.
4. Verify `requirements_met()` cannot mask an incompatible environment.
5. Repeat the Torch 2.11.0+cu130, torchvision 0.26.0+cu130, CUDA 13.0, and
   RTX 5060 Ti checks without model loading.

### Pillow and image codecs

6. Import Pillow, pillow-heif, pillow-jxl-plugin, imageio, torchvision,
   scikit-image, diffusers, and facexlib.
7. Open and save representative PNG, JPEG, WebP, AVIF/HEIF, JXL, and GIF
   fixtures entirely inside the approved workspace.
8. Verify PNG generation text, JPEG/WebP EXIF, orientation, ICC retention and
   conversion, JPEG subsampling, alpha, 16-bit handling, and pixel equality.
9. Exercise core resize, mask, composite, grid, API encode/decode, and temp
   cache paths.
10. Test the legacy preprocessor paths, including the remaining
    `Image.ANTIALIAS` call.

### Neo Gradio compatibility

11. Import `modules_forge.patch_basic`, `modules.gradio_extensions`,
    `modules.ui_tempdir`, `modules.ui_components`,
    `modules.ui_gradio_extensions`, and `modules_forge.main_entry`.
12. Verify every private patched attribute exists before patching.
13. Build the full Neo Blocks tree with built-in preloads and extensions
    disabled.
14. Validate component constructors, layouts, `elem_id`/classes,
    `gr.update`, `gr.skip`, `_js` translation, event chaining, queueing,
    Gallery/Image serialization, temp-file registration, template injection,
    networking patch, and middleware handling.
15. Require exclusive `127.0.0.1:7860`, loopback HTTP 200, no share/public
    exposure, and normal one-signal shutdown.

### Studio compatibility and architecture

16. Test the reference minimal redirect tab only as a compatibility surface.
17. Test standalone stub installation, real-Blocks preservation, extension
    manifest extraction, Studio routes, and `--nowebui` behavior.
18. Verify Studio preview/final JPEG policy, ICC conversion, raw RGBA import,
    Gallery thumbnails/metadata, and generation image handoff.
19. Confirm new Studio code uses owned API/state/adapters and introduces no
    dependency on Neo UI construction or Gradio internals.
20. Demonstrate rollback to the preserved Neo baseline before declaring the
    dependency change accepted.

## Inspected-path inventory

Explicitly read or searched for this analysis:

- `AGENTS.md`
- `requirements.txt`
- `launch.py`
- `modules/launch_utils.py`
- `webui.py`
- `modules/images.py`
- `modules/ui.py`
- `modules/ui_components.py`
- `modules/ui_gradio_extensions.py`
- `modules/ui_tempdir.py`
- `modules/gradio_extensions.py`
- `modules_forge/main_entry.py`
- `modules_forge/patch_basic.py`
- repository Python-source scans under `backend/`, `modules/`,
  `modules_forge/`, `scripts/`, and `extensions-builtin/`
- installed `venv/Lib/site-packages/*.dist-info/METADATA` constraint scan
- the 14 directly relevant installed metadata files named in sections 3 and 4
- `Reference/Forge-Studio-main/Forge-Studio-main/install.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/README.md`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_api.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_bridge_init.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_gallery.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_generation.py`
- `Reference/Forge-Studio-main/Forge-Studio-main/scripts/studio_gradio_stub.py`
- dependency/source text scans under the two permitted `Reference/` trees

No path outside `Studio-Standalone` was accessed.
