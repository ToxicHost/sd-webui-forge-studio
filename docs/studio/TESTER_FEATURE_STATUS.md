# Studio Standalone — feature status for testers

Features reviewed 2026-09-13; Git installation verified 2026-09-15; Canvas painting updated 2026-09-30. This describes the current Standalone source and the
verification performed for this candidate, not the inherited Neo README.

**Available** means there is an implemented Standalone path. **Contract-tested**
means existing automated tests passed with fixtures; it does not mean that a
real GPU or every browser workflow was exercised. Real model generation has
historical evidence, but was not rerun in this preparation because no private
generation-model paths were accessed.

## Available, with the stated limits

| Area | Current status | Evidence / limits |
|---|---|---|
| Studio interface and local server | Available | A fresh Git installation with cached dependencies, source and private-runtime portable extractions passed startup and registry checks. Portable startup also passed with an empty PATH; Windows/NVIDIA is the initial target. |
| Model folders and selection | Available | Select directories in Settings, then checkpoint/components; Generate owns loading. Lifecycle contract tests passed. |
| Basic image generation | Implemented; GPU retest required | Request/lifecycle tests passed. No new model-family, VRAM, quality or performance certification. |
| Img2Img and Inpaint | Implemented; GPU/browser retest required | Source image, mask and translation contracts tested. These do not prove every Canvas interaction or final pixel result. |
| Hires | Implemented; GPU retest required | Hires and Img2Img/Hires contracts passed. Requires a suitable upscaler/model and enough memory. |
| Auto Detail | Implemented; GPU retest required | Detector/slot contracts passed. Five pinned detectors included and ultralytics declared. A detector finding no target can leave the image unchanged. |
| Queue and cancellation | Available | Queue/lifecycle contracts passed. Cancellation is bounded by safe execution points, not necessarily immediate. |
| Canvas brushes, layers and masks | Available for testing | Brush Engine V2 is the default painting engine (2026-09-30): automated brush checks passed and every preset was reviewed with a mouse. Aliased/Pixel Perfect brushes, touch input and mask/region painting use the previous engine. No tablet certification. |
| Canvas recovery | Implemented | Recovery-store tests passed. Undo history is not restored. Keep separate artwork backups. |
| Gallery | Implemented with limitations | Service contract tests passed; fresh browser behavior still needs tester feedback. Image similarity/metadata are not universal format guarantees. |
| Preferences | Available with persistence limits | Server persistence tests passed. An ephemeral port changes browser origin, resetting browser-local theme/tool/layout state; use a stable port when needed. |
| Wildcards / Lexicon | Available | Editor and preference tests passed. Configured local folders are required for user content. |
| LoRAs / embeddings | Implemented; not certified in this pass | Catalogue/bridge source exists. Model-family and combination-specific behavior still needs testing. |
| Develop | Partial / not certified in this pass | Browser editing code exists. The saved-presets endpoint returns 404; float sidecar workflows are not certified. |
| Support report | Available | Script is supplied; report is created locally for inspection before sharing. |

## Unavailable, incomplete or outside this candidate

| Area | Status |
|---|---|
| ControlNet | Unavailable in the Standalone generation path. The adapter returns only None for model/preprocessor catalogues. Retained Neo code does not make this a working Studio feature. |
| Live Painting generation | Unavailable. Live status explicitly reports available: false. Ordinary Canvas painting is separate and available. |
| Workshop | Unavailable backend. Its UI requests /studio/workshop routes that have no service in the current Standalone router. |
| Regional / attention-couple generation | Unavailable in the supported generation request. Saving/editing regional document state does not prove regional inference. |
| High Precision float output | Not available as a supported workflow. A Develop panel does not prove genuine float generation capture. |
| Separate Inpaint Sketch mode | Intentionally superseded by Canvas painting/masks; not an outstanding promised mode. |
| Batch Count / Batch Size | Not provided as true batched generation. Submit separate jobs to use the queue. |
| Automatic updates | Unavailable. Git installations support manual `git pull --ff-only`; see [GIT_INSTALL.md](GIT_INSTALL.md). ZIP updates use a separate candidate folder and backups. |
| Multi-user / public network service | Outside the Windows local-use candidate. |
| Linux/NVIDIA Docker tester | Experimental [setup supplied](../../packaging/docker/README.md); config and native adapter checks only. Image build, GPU passthrough and container generation await the Linux tester. |
| macOS, Linux, Docker, AMD/Intel GPU certification | Not established by this review. Some platform/source support exists, but is not a tested release promise. |

## Verification scope

- Git delivery: 49 focused checks passed, one Windows symlink test skipped.
  Fresh venv setup from cached dependencies/assets, `git pull --ff-only`, offline
  reuse, settings preservation and `pip check` passed. The non-mock backend
  exposed 23 samplers, 18 schedulers, six latent/four image upscalers and five
  verified detectors. Public internet setup and real generation remain untested.
- Existing Canvas set: 148 tests; 147 initially passed and one aggregate
  fingerprint failed. Individual file pins already matched. Only the two
  aggregate fingerprints were regenerated; the failing test then passed.
- Initial packaging/bootstrap/privacy/archive set: 53 tests passed.
- Tester cleanup: 76 packaging/setup/privacy/diagnostics checks passed; the
  extracted app exposed the expected sampler/upscaler registries and 5 detectors.
- Fixture-based feature set: 464 tests passed across generation, Inpaint,
  Hires, Auto Detail, jobs, recovery, Gallery, Wildcards, preferences,
  lifecycle and capability gating.
- The [historical preparation review](https://github.com/ToxicHost/sd-webui-forge-studio/blob/7543faf5cf11cabbb8648afecdbe2c1bf76cb4b4/docs/studio/RELEASE_PREPARATION_REVIEW.md) records the earlier fixture/archive checks.

No fresh real-GPU image was generated in this preparation. Historical release
notes are not evidence that this exact candidate passed those live scenarios.
