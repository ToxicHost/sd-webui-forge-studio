# Studio Dimension Capability Audit

## Audit status

- Scope: bounded static inspection of the retained Neo/Forge Python launch-to-generation dimension path.
- Runtime activity: none. Forge/Neo, CUDA, models, generation, package tooling, and network access were not invoked.
- Frontend scope: the Studio frontend was not inspected or modified.
- Evidence classification:
  - **Verified** means directly established by the cited retained source.
  - **Derived** means a deterministic consequence of the cited shape arithmetic.
  - **Runtime verification required** means static source does not establish the loaded-model or hardware result.
- Adapter decision: **`FORGE_ADAPTER_CAPABILITY_REQUIRED = YES`**.

## Executive conclusion

The retained Neo/Forge code does **not** establish “width and height must be multiples of 64” as a universal generation rule.

It establishes three different concepts:

1. **Neo UI selection policy.** Width and height use a configurable resolution step. The default is 64, but the supported setting choices are 8, 16, 32, 64, 128, and 256. The source describes 64 as recommended for compatibility, not mandatory (`modules/shared_options.py:161-167`; `modules/ui.py:95-99`).
2. **Latent-grid exactness.** The requested pixel dimensions are floor-divided by the active VAE factor when the initial latent is allocated (`modules/processing.py:953-959`). The factor is loaded from the active VAE when it is an integer (`modules/sd_models.py:381-385`). Retained VAEs declare a factor of 8 normally and 16 for Flux2; Wan uses spatial factors of 8 and therefore takes the processing fallback of 8 (`backend/patcher/vae.py:123-152`).
3. **Operational safety.** The Neo controls impose UI ranges, but the txt2img API does not add width/height minimum, maximum, divisibility, or pixel-count constraints (`modules/api/models.py:37-97`; `modules/api/api.py:437-498`). No general backend safety ceiling was found in the audited path.

Consequently, non-multiples of 64 can be retained exactly:

- **520** is not divisible by 64 but is divisible by 8. In the normal factor-8 path, the retained shape contract allocates 65 latent cells and decodes them to 520 pixels.
- **528** is not divisible by 64 but is divisible by 16. It is aligned for both normal factor-8 and Flux2 factor-16 paths.
- **520 on Flux2 is not exact.** Integer floor allocation produces `520 // 16 = 32` latent cells, corresponding to 512 decoded pixels under the declared factor-16 contract.
- **513 on a factor-8 model is not exact.** Integer floor allocation produces 64 latent cells, corresponding to 512 decoded pixels.

Those examples are deterministic static shape results, subject to the conditional model hook and script mutation points described below. Actual-model smoke tests remain required before Studio advertises a capability.

## Retained dimension path

| Stage | Verified retained behavior | Source |
| --- | --- | --- |
| Resolution setting | `_STEP` is loaded from `opts.res_step`; `sRound(value)` rounds to the nearest configured step using `floor(value / step + 0.5) * step`. | `modules/ui.py:95-99` |
| Setting choices | `res_step` defaults to 64; choices are 8, 16, 32, 64, 128, and 256. The source calls 64 “recommended to prevent compatibility issues.” | `modules/shared_options.py:161-167` |
| Neo txt2img controls | Width and height sliders are 64 through 2048 with step `_STEP`. | `modules/ui.py:226-230` |
| Neo txt2img submission | Gradio submits height then width to the retained txt2img entry point. | `modules/ui.py:341-368`, `modules/ui.py:378-387` |
| Processing construction | `txt2img_create_processing` assigns the received `width` and `height` directly to `StableDiffusionProcessingTxt2Img`; it does not normalize them. | `modules/txt2img.py:18-62` |
| Processing dataclass | Width and height are plain integer fields with defaults of 512 and no embedded range or divisibility constraints. | `modules/processing.py:142-165` |
| Optional model hook | Before processing initialization, a loaded model may replace both dimensions through a dynamically present `fix_dimensions` hook. No implementation of that hook was found in the retained audited source. | `modules/processing.py:886-893` |
| Extension/script hook | Retained scripts run after the optional model hook and before `p.init`, so installed scripts can conditionally mutate processing state. | `modules/processing.py:899-923` |
| Latent allocation | Non-PiD generation allocates height as `p.height // opt_f` and width as `p.width // opt_f`; this is floor division, not nearest-step rounding. | `modules/processing.py:945-959` |
| Active factor | The global processing factor defaults to 8, then becomes the loaded VAE’s integer `upscale_ratio`; non-integer ratios fall back to 8. | `modules/processing.py:42-43`; `modules/sd_models.py:381-385` |
| VAE factors | Ordinary retained VAEs declare 8; Flux2 declares 16; Wan declares tuple ratios whose spatial entries are 8. | `backend/patcher/vae.py:123-152` |
| Decode | SD 1.x, SDXL, Flux, Flux2, and Qwen engines pass the sampled latent through their active VAE decode path. | `backend/diffusion_engine/sd15.py:59-69`; `backend/diffusion_engine/sdxl.py:116-126`; `backend/diffusion_engine/flux.py:88-105`; `backend/diffusion_engine/flux2.py:60-79`; `backend/diffusion_engine/qwen.py:107-128` |
| Final image | The decoded tensor is converted directly to a PIL image and appended/saved. The core path does not resize it back to the requested `p.width` and `p.height`. | `modules/processing.py:993-1017`; `modules/processing.py:1048-1111` |

## Neo/Gradio behavior

### Primary txt2img dimensions

The Neo UI exposes a user-selectable range of 64–2048 for each axis, stepping by the configured `_STEP` (`modules/ui.py:226-230`). Because `_STEP` may be 8 or 16, the retained UI can intentionally submit dimensions that are not multiples of 64.

This range is a Gradio control boundary, not a validated backend contract. The request builder transfers the values without further checks (`modules/txt2img.py:18-62`).

### Hires dimensions

Neo exposes:

- upscale scale from 1.0 through 4.0;
- explicit target width and height from 0 through 4096, where 0 is a sentinel;
- target slider step `_STEP`.

These controls are at `modules/ui.py:249-265`.

The processing path rounds all calculated hires targets with `sRound`:

- scale-based targets use `sRound(base * hr_scale)`;
- a missing explicit axis is derived from aspect ratio;
- both resulting axes are then rounded to the configured resolution step.

See `StableDiffusionProcessingTxt2Img.calculate_target_resolution` at `modules/processing.py:1260-1288`. The old first-pass algorithm also rounds both axes to `_STEP` (`modules/processing.py:1197-1206`).

The hires latent-upscale path again floor-divides the rounded target by `opt_f` (`modules/processing.py:1453-1483`). A pixel-upscaler path resizes to the rounded target before re-encoding (`modules/processing.py:1491-1518`). Therefore the same active-VAE alignment concern remains relevant even when the target originated in a hires control.

### Img2img dimensions

Img2img’s “Resize to” controls also expose 64–2048 with `_STEP` (`modules/ui.py:581-597`). “Resize by” values are rounded through `sRound` before processing (`modules/img2img.py:215-223`). Batch inputs whose source dimensions are not divisible by `_STEP` are resized to their nearest `_STEP` dimensions (`modules/img2img.py:75-83`).

The latent-upscale img2img mode interpolates to `(height // opt_f, width // opt_f)` (`modules/processing.py:1852-1857`). These img2img rules show that Neo applies UI-step normalization in selected image workflows, but they do not turn 64 into a universal backend requirement.

### Existing-image txt2img upscale

The selected gallery image’s actual dimensions replace `p.width` and `p.height` for the retained txt2img upscale path (`modules/txt2img.py:88-103`). That path can therefore inherit an image’s dimensions independently of the original requested metadata.

## API validation behavior

`StableDiffusionTxt2ImgProcessingAPI` is generated from the processing dataclass. Its generated fields use the dataclass annotations wrapped as optional types and plain `Field(default=...)` declarations (`modules/api/models.py:37-97`). Unlike the separate extras upscaler request, whose target dimensions explicitly use `ge=1` (`modules/api/models.py:131-140`), txt2img width and height receive no numeric constraints.

The txt2img API handler copies the request fields into `StableDiffusionProcessingTxt2Img` and invokes processing without adding a dimension check (`modules/api/api.py:437-498`).

Verified consequences:

- The API is not bounded to Neo’s 64–2048 slider range.
- The API does not require a multiple of `_STEP`, 8, 16, 32, or 64.
- Type-valid zero, negative, `None`, excessively large, or non-aligned values are not rejected by a dimension-specific API rule in this path.
- Such values are **not thereby supported**. They may create invalid tensor shapes, silent floor truncation, memory exhaustion, or model-specific failures.
- If hires is enabled, the later hires calculation still applies `sRound` to its calculated target.

## Exact-retention rule supported by the source

For ordinary txt2img processing with no dimension-fixing model hook and no script mutation, the retained code supports this shape rule:

```text
latent_width  = floor(requested_width  / F)
latent_height = floor(requested_height / F)
```

`F` is the active processing factor derived from the loaded VAE. Under that VAE’s declared spatial scale contract, exact retention requires:

```text
requested_width  % F == 0
requested_height % F == 0
```

This is the supported general rule. It is **not** a fixed multiple-of-64 rule.

If either dimension is not aligned, the core path does not round the stored request dimension before conditioning or metadata:

- prompt conditioning receives `self.width` and `self.height` (`modules/processing.py:472-474`);
- generation metadata reports `p.width x p.height` (`modules/processing.py:713-733`);
- latent allocation separately floor-divides those values (`modules/processing.py:953-959`);
- the decoded image is not resized back in the core output path (`modules/processing.py:993-1017`, `modules/processing.py:1048-1111`).

Therefore an unaligned API request can create a requested-size/conditioning/metadata value that differs from the actual decoded image extent. Studio must reject or explicitly normalize such a request before dispatch; it must not rely on this silent floor behavior.

## Model and backend differences

| Model/backend class | Verified dimension behavior | Capability consequence |
| --- | --- | --- |
| Ordinary retained VAE path, including inspected SD 1.x, SDXL, Flux, and Qwen decode entries | VAE factor is 8 unless the VAE is constructed as Flux2; processing uses that integer factor. | Non-multiples of 64 can be exact when both axes are multiples of 8. Runtime smoke testing is still required per loaded architecture. |
| Flux2 | VAE factor is explicitly changed to 16. | Exact axes must be multiples of 16. A Studio rule based only on 8 would be incorrect. |
| Wan | VAE ratios are tuples with spatial factor 8; processing falls back to 8 for a non-integer ratio. The latent additionally has a time axis. | Spatial axes use factor-8 allocation, but video/time capability is a separate concern. |
| PiD | Core processing rejects txt2img when PiD mode is active. PiD’s encode validator warns below 512² area and rounds input images to `res_step`, but that applies to its image encode path. | PiD must be reported as unsupported for txt2img rather than presented as evidence for a global output-size rule. |
| Qwen reference-image path | Vision input is resized toward 384²; optional VAE reference resize rounds both axes to 32 near 1024². | These are reference-image preprocessing rules, not txt2img output-dimension constraints. |
| Patch-based transformer backends | The shared helper pads latent tensors to patch size. Inspected Flux, Qwen, Wan, and Chroma implementations crop the result back to the original latent extent. | Transformer patch divisibility is internally accommodated and does not independently require public pixel dimensions to be multiples of 64. |
| Dynamic `fix_dimensions` model hook | Processing honors the hook if present, but no retained implementation was found in the audited source. | Exact behavior must be queried or tested for a loaded model; it cannot be inferred from the base class alone. |
| Scripts/extensions | Script callbacks execute before initialization and can mutate processing. | Adapter capability must account for enabled runtime extensions or declare the supported extension-free baseline. |

Model-specific sources:

- VAE factors: `backend/patcher/vae.py:123-152`.
- PiD txt2img rejection: `modules/processing.py:860-868`.
- PiD input validation: `backend/diffusion_engine/pid.py:67-84`.
- Qwen reference preprocessing: `backend/diffusion_engine/qwen.py:79-105`.
- Shared patch padding: `backend/utils.py:300-309`.
- Pad-and-crop examples: `backend/nn/flux.py:558-634`; `backend/nn/qwen.py:371-389`, `backend/nn/qwen.py:478-483`; `backend/nn/wan.py:389-419`; `backend/nn/chroma.py:244-262`.

## Minimums, maximums, divisibility, and safety ceiling

| Question | Static audit result |
| --- | --- |
| Neo primary minimum | 64 pixels per axis, enforced by the Gradio slider only. |
| Neo primary maximum | 2048 pixels per axis, enforced by the Gradio slider only. |
| Neo hires explicit maximum | 4096 pixels per axis, enforced by the Gradio slider only; 0 is a sentinel. |
| API minimum/maximum | No dimension-specific minimum, maximum, or pixel-count ceiling is declared for txt2img. |
| UI selection divisibility | Multiple of configured `res_step`, default 64 and configurable down to 8. |
| Core exactness divisibility | Multiple of the loaded integer VAE factor: 8 normally, 16 for Flux2 in the retained constructors. |
| Transformer patch divisibility | Internally padded and cropped in the inspected patch backends; not a public multiple-of-64 rule. |
| Universal safe maximum | None established by the audited static path. |

The VAE decoder estimates memory, batches according to free memory, and retries an out-of-memory decode with tiled decoding (`backend/patcher/vae.py:202-230`). This is recovery behavior, not an acceptance limit, and it does not establish that the sampler or the full request is safe. The usable ceiling varies with model, VAE, device memory, batch size, hires mode, extensions, and other runtime state.

Studio must not translate “the API has no maximum” into “unbounded dimensions are supported.”

## Required Forge adapter capability

**Decision: `FORGE_ADAPTER_CAPABILITY_REQUIRED = YES`.**

A static Studio-wide constant cannot correctly represent:

- configurable Neo selection step;
- factor-8 versus factor-16 VAE alignment;
- PiD’s lack of txt2img support;
- optional model `fix_dimensions`;
- extension mutation;
- mode-specific hires/img2img normalization;
- hardware- and model-dependent safe limits.

Before the first real Forge dispatch, the owned adapter boundary should expose a capability result for the selected operation and loaded backend. At minimum it should distinguish:

- `supported` and an explanatory reason;
- operation/mode (`txt2img`, `img2img`, hires, video);
- loaded model/backend identity;
- exact pixel alignment for each axis;
- recommended UI selection step, separately from exact alignment;
- minimum axis and minimum pixel-area policy, if known;
- maximum axis and maximum pixel-area policy, if known;
- requested dimensions;
- accepted dimensions after any explicit normalization;
- whether normalization is reject, floor, ceil, or nearest;
- whether the result is statically known, runtime-probed, or owner policy.

Studio should use one of two explicit behaviors:

1. reject an unsupported or unaligned request with an actionable capability error; or
2. show the owner the normalized dimensions before dispatch and record both requested and effective values.

It should not reproduce the retained API’s silent latent floor.

## Runtime verification required

The following remain intentionally unresolved by this static audit:

1. Confirm actual output dimensions for representative non-64 values on each supported loaded architecture, including 520 on a factor-8 model and 528 on Flux2.
2. Confirm rejection or normalization behavior for unaligned axes such as 513 and Flux2 520.
3. Determine whether any selected runtime model supplies `fix_dimensions`.
4. Determine whether enabled extensions mutate width or height.
5. Establish safe maximum axes and pixel area for each supported model/mode/device policy.
6. Verify hires target and final decoded dimensions for latent and pixel-upscaler paths.
7. Verify img2img behavior for arbitrary source image sizes.
8. Confirm API error behavior for zero, negative, null, and excessive values without treating failure as capability.

These checks belong behind the future owner-gated Forge adapter/runtime test boundary. They were not executed during this audit.

## Audited retained source

The conclusions above are based on the following in-repository paths:

- `modules/ui.py`
- `modules/shared_options.py`
- `modules/txt2img.py`
- `modules/img2img.py`
- `modules/processing.py`
- `modules/rng.py`
- `modules/images.py`
- `modules/sd_models.py`
- `modules/sd_samplers_common.py`
- `modules/api/models.py`
- `modules/api/api.py`
- `backend/patcher/vae.py`
- `backend/utils.py`
- `backend/diffusion_engine/sd15.py`
- `backend/diffusion_engine/sdxl.py`
- `backend/diffusion_engine/flux.py`
- `backend/diffusion_engine/flux2.py`
- `backend/diffusion_engine/pid.py`
- `backend/diffusion_engine/qwen.py`
- `backend/nn/flux.py`
- `backend/nn/qwen.py`
- `backend/nn/wan.py`
- `backend/nn/chroma.py`

Targeted static searches also checked the retained `modules`, `backend`, and built-in extension Python trees for a concrete `fix_dimensions` implementation and general width/height bounds. No retained implementation of `fix_dimensions` and no general txt2img backend safety ceiling were found.
