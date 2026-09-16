# Phase 0 Extension and Asset Inventory

## 1. Audit identity

- Audit date: `2026-07-23`
- Audit method: static metadata inspection
- Product commit:
  `99512f07abaab8b869adb9d356dde303bc6c7f28`
- Branch: `docs/phase0-complete-baseline`
- Local evidence: `../Evidence/phase-0/03-extension-inventory/`
- Application or extension code executed: no
- Model contents opened, deserialized, or hashed: no
- Packages installed, files downloaded, or repositories updated: no

Classifications:

- **Verified** — established directly from inspected source or metadata.
- **Conditional** — depends on later arguments, settings, or runtime state.
- **Inferred** — supported by the source layout but not directly established.
- **Unavailable** — required configuration or metadata is absent.

## 2. Scope and methodology

The audit inspected the approved extension roots, discovery and path source,
small metadata and license files, the configured model-search directories, and
the Studio reference license. It queried directory existence, ordinary file
metadata, Git tree identities, extensions, counts, sizes, and reparse-point
state.

It did not run launch, installer, preload, extension, or inventory code. It
did not open model files, inspect tensors or headers, hash model assets,
inspect generated outputs, or recurse through the user profile.

Public built-in names are retained. Model filenames are omitted; counts and
format classes are used instead.

## 3. Extension-discovery behavior

| Finding | Source/function | Classification |
|---|---|---|
| Built-in and external roots derive from `data_path` and `script_path`. | `modules/paths_internal.py`, module block | Verified |
| Discovery scans built-ins first and external extensions second, sorting each directory. | `modules/extensions.py:list_extensions` | Verified |
| Non-directory entries are ignored. | Same | Verified |
| `metadata.ini` is read when present. | `ExtensionMetadata.__init__` | Verified |
| The metadata `Name` value is overwritten by the lowercased directory name, making canonical identity directory-based. | Same | Verified |
| A later duplicate canonical name is discarded. | `list_extensions` duplicate check | Verified |
| Settings plus `always_disabled_extensions` determine the per-extension `enabled` field. | `list_extensions`; `modules_forge/config.py` | Verified |
| Global all/extra disable modes are applied by `active`. | `modules/extensions.py:active` | Verified |
| External extension installer discovery respects settings and disable arguments. | `modules/launch_utils.py:list_extensions` | Verified |
| Preload discovery imports matching `preload.py` files and calls `preload(parser)`. | `modules/script_loading.py:preload_extensions` | Verified, conditional on launch |

`config.json` and `ui-config.json` are absent. All current built-ins are
enabled under repository defaults, but actual runtime state remains
**conditional** on future arguments and settings.

## 4. Built-in extension inventory

All 19 built-ins are ordinary repository directories and were clean at the
start of Stage C.

| Built-in | Default state | Major hook | Install | Preload | Manifest | License status | Revision |
|---|---|---|---:|---:|---|---|---|
| `extra-options-section` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `forge_legacy_preprocessors` | Enabled | Installer and Python script | Yes | No | Requirements; nested setup | GPL-3.0 plus mixed nested terms | Product-pinned |
| `forge_preprocessor_inpaint` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `forge_preprocessor_reference` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `forge_preprocessor_tile` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `mobile` | Enabled | Browser JavaScript | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `prompt-bracket-checker` | Enabled | Browser JavaScript | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_compile` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_controlllite` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_controlnet` | Enabled | Preload, Python, JavaScript | No | Yes | None | GPL-3.0 | Product-pinned |
| `sd_forge_image_stitch` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_ipadapter` | Enabled | Python script | No | No | None | GPL-3.0 | Product-pinned |
| `sd_forge_lora` | Enabled | Preload and Python script | No | Yes | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_multidiffusion` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_neveroom` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_pid` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_radial` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `sd_forge_spectrum` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |
| `soft-inpainting` | Enabled | Python script | No | No | None | No per-directory declaration; coverage/provenance unresolved | Product-pinned |

Each runtime script or browser script can execute code when loaded
(**conditional, verified hook presence**).

## 5. External extension inventory

`extensions/` exists and contains zero extension directories (**verified**).
There is therefore no external name, enabled state, remote, revision, dirty
state, installer, preload, manifest, or license to report.

The ADetailer recommendation in `modules_forge/config.py` does not establish
an installation. No ADetailer extension is present (**verified**).

## 6. Installer and preload inventory

| Extension | Hook | Behavior | Classification |
|---|---|---|---|
| `forge_legacy_preprocessors` | `install.py` | Can install ten unpinned requirements, insightface, and two wheel URLs. Errors are handled per package. | Verified; conditional on launcher installer path |
| `sd_forge_controlnet` | `preload.py` | Adds the ControlNet log-level argument. | Verified; conditional on preload |
| `sd_forge_lora` | `preload.py` | Adds the LoRA directory argument with `<MODELS>/Lora` default. | Verified; conditional on preload |

Installer processes receive the repository on `PYTHONPATH`.
`--skip-install` suppresses installer execution but not preload execution.
Extension scripts remain executable-code trust boundaries.

## 7. Extension revision and pinning status

No built-in contains a nested `.git` directory. Consequently:

- independent remote URL: unavailable;
- independent branch: unavailable;
- independent extension commit SHA: unavailable;
- independent dirty state: unavailable.

Every built-in is tracked as a Git tree at product commit
`99512f07abaab8b869adb9d356dde303bc6c7f28` and was clean relative to the
containing repository before Stage C edits (**verified**). Git tree SHAs for
all 19 directories are retained in local evidence.

This is a reproducible bundled snapshot, not proof of each extension's
upstream provenance. External extensions, if later installed from branches,
may be floating.

## 8. Configured model and asset search paths

The default `data_path` is the repository root. `--data-dir` can replace it.
The default `models_path` is `<DATA>/models`; `--model-ref` can replace it
(`modules/paths_internal.py`, **verified**).

| Category | Default/current source path | Additional input | Classification |
|---|---|---|---|
| Checkpoints | `models/Stable-diffusion` | Repeatable `--ckpt-dirs` | Verified |
| VAEs | `models/VAE` | Repeatable `--vae-dirs` | Verified |
| LoRAs | `models/Lora` | Repeatable `--lora-dirs` | Verified |
| Embeddings | `models/embeddings` | `--embeddings-dir` | Verified |
| Text encoders | `models/text_encoder` placeholder | Repeatable `--text-encoder-dirs` | Verified path; consumer details incomplete |
| ControlNet | `models/ControlNet` | Repeatable `--controlnet-dirs` | Verified |
| ControlNet preprocessors | `models/ControlNetPreprocessor` | Directory argument | Verified |
| Diffusers layouts | `models/diffusers` | Model-root override | Verified |
| ESRGAN | `models/ESRGAN` | Path argument | Verified |
| CodeFormer | `models/Codeformer` | Path argument | Verified |
| GFPGAN | `models/GFPGAN` | Path argument | Verified |
| ADetailer detectors | Not configured; extension absent | Unavailable | Unavailable |

`modules/launch_utils.py` can conditionally append existing A1111 or Comfy
paths from three reference arguments. None was active or available for this
audit, so external search roots were not inspected.

## 9. Asset counts by category

| Category | Exists | Files | Size | Recognized formats | Recognized assets | License colocated |
|---|---:|---:|---:|---|---:|---:|
| Checkpoints | Yes | 1 | 75 B | `.ckpt`, `.safetensors`, `.gguf` | 0 | No |
| VAEs | Yes | 1 | 68 B | `.ckpt`, `.pt`, `.pth`, `.bin`, `.safetensors`, `.sft`, `.gguf` | 0 | No |
| LoRAs | Yes | 1 | 69 B | `.pt`, `.ckpt`, `.safetensors` | 0 | No |
| Embeddings | Yes | 1 | 74 B | `.bin`, `.pt`, `.safetensors`, `.sft` | 0 | No |
| Text encoders | Yes | 1 | 77 B | Not fully established | 0 | No |
| ControlNet | No | 0 | 0 B | `.pt`, `.pth`, `.ckpt`, `.safetensors`, `.bin` | 0 | No |
| ControlNet preprocessors | No | 0 | 0 B | Varies | 0 | No |
| Diffusers layouts | No | 0 | 0 B | Directory layout | 0 | No |
| ESRGAN | Yes | 1 | 27 B | `.pt`, `.pth`, `.safetensors` | 0 | No |
| CodeFormer | No | 0 | 0 B | `.pth` | 0 | No |
| GFPGAN | No | 0 | 0 B | `.pth` | 0 | No |
| ADetailer detectors | No | 0 | 0 B | Unavailable | 0 | No |

The six files are tiny `.txt` placeholders. No model filename is retained.
No generative model asset was found.

No recognized checkpoint, VAE, LoRA, embedding, ControlNet, ADetailer
detector, or upscaler weight is present in the audited repository paths.
Stage D may test environment provisioning and startup, but the first model
load and generation remain blocked until the owner identifies an approved
local model location.

Bundled non-weight assets:

- JavaScript: 36 files, about 184.2 KiB;
- fonts: 15 files, about 429.6 KiB;
- Hugging Face configuration/tokenizer bundle: 224 files, about 64.3 MiB.

## 10. Repository-contained versus external assets

All active default search directories are inside the repository and are
ordinary directories rather than reparse points (**verified**). No configured
outside-repository asset root was established.

Optional A1111 and Comfy reference paths are **conditional** and were not
supplied. The Studio reference directory is not an active model-search path.

No external model directory, external extension directory, or user-profile
asset tree was enumerated.

## 11. License and attribution status

| Component | Status | Finding |
|---|---|---|
| Neo/product source | Verified | Root AGPL-3.0 text |
| Studio reference source | Verified | AGPL-3.0 text |
| Three built-ins | Verified declaration | GPL-3.0 files in legacy preprocessors, ControlNet, and IP-Adapter |
| Remaining 16 built-ins | Unresolved | No per-directory declaration; repository-root coverage and independent upstream provenance remain unresolved |
| Legacy preprocessor nested components | Mixed | 11 MIT, 2 Apache-2.0, 3 restricted |
| Bundled Forge package copies | Verified declaration | Two GPL-3.0 and one MIT |
| JavaScript set | Ambiguous | No standalone notice in JavaScript directories |
| Fonts | Separate review required | Colocated redistribution evidence not found; provenance and applicable license remain unresolved |
| Hugging Face configuration/tokenizer data | Separate review required | Colocated redistribution evidence not found; provenance and applicable license remain unresolved |
| Model weights | Not applicable currently | No recognized model asset present |

Absence of a per-directory declaration is not proof that redistribution is
prohibited. The root AGPL-3.0 terms may govern code incorporated into the
product, while third-party provenance, attribution and notices, and any
incompatible terms require independent review.

Three nested preprocessor components require specific review:

| Component/directory | Relative license path | License filename | Short restriction | Classification |
|---|---|---|---|---|
| LeReS / BoostingMonocularDepth `leres/pix2pix` | `extensions-builtin/forge_legacy_preprocessors/annotator/leres/pix2pix/LICENSE` | `LICENSE` | Redistribution is limited to academic use and requires credit | Confirmed restriction; legal review required |
| OpenPose `openpose` | `extensions-builtin/forge_legacy_preprocessors/annotator/openpose/LICENSE` | `LICENSE` | Limited to academic or nonprofit, noncommercial research use; distribution is restricted | Confirmed restriction; legal review required |
| PiDiNet `pidinet` | `extensions-builtin/forge_legacy_preprocessors/annotator/pidinet/LICENSE` | `LICENSE` | Research-purpose and commercial-contact preface conflicts with the following broad permissive grant | Ambiguous; legal review required |

These components are potential redistribution blockers pending review, not a
final legal determination.

`javascript/populateLicense.js` fetches 14 upstream license texts at runtime.
Remote display is not equivalent to shipping attribution evidence.

The source-code AGPL license is not treated as covering independent model
weights, datasets, fonts, or third-party assets.

## 12. Privacy controls

- Only configured directories were enumerated.
- No user-profile recursion occurred.
- No extension code or model parser was executed.
- No model was opened, loaded, deserialized, copied, or hashed.
- Only small license files were hashed.
- Model filenames and placeholder filenames are omitted.
- No prompt, output, image metadata, credential, token, or private path was
  collected.
- Built-in names are public source identifiers.
- Local raw evidence remains outside Git.

Privacy review result: `PASS`.

## 13. Security and supply-chain observations

- Every Python or browser hook can execute code when loaded.
- The current installer can execute package-manager commands and obtain remote
  wheels.
- Preloads execute even when installers are skipped.
- Settings and disable arguments affect runtime enablement, but were absent
  from this static baseline.
- `ExtensionMetadata` effectively ignores its declared `Name` value,
  weakening metadata-based canonical identity.
- Built-ins are reproducibly pinned by product Git trees but lack independent
  upstream remote and commit metadata.
- Sixteen built-ins have no per-directory declaration; repository-root
  coverage and independent upstream provenance remain unresolved.
- Two bundled preprocessor components have confirmed restrictions and one has
  ambiguous terms; all three require legal review.
- For the fonts and tokenizer/configuration tree, colocated redistribution
  evidence was not found; provenance and applicable licenses remain
  unresolved.
- Future external Git extensions may be floating and updateable.

## 14. Missing information

- Independent upstream URL, branch, and commit for every built-in.
- Repository-root coverage, independent upstream provenance, attribution,
  notices, and compatibility review for 16 built-ins without per-directory
  declarations.
- Font provenance, applicable license, and redistribution evidence.
- Provenance, applicable license, and redistribution evidence for bundled
  Hugging Face configuration/tokenizer data.
- Final legal interpretation of three restrictive preprocessor licenses.
- Active runtime settings and command-line extension disable state.
- Any future external extension revisions and licenses.
- Consumer format rules for the default text-encoder placeholder directory.
- ADetailer installation, detector path, revision, and license.
- Optional A1111 or Comfy external asset roots.
- Licenses for any model weights added later.

## 15. Requirements for the later first-launch test

Before Stage D:

1. Keep the external extension directory unchanged or record any addition with
   remote, revision, dirty state, and license.
2. Reconfirm the 19 built-in Git tree identities.
3. Resolve or explicitly accept the restrictive preprocessor redistribution
   review.
4. Add or identify attribution for bundled fonts and tokenizer/configuration
   data.
5. Capture the exact launch arguments and extension disable settings without
   exposing credentials or private paths.
6. Obtain the owner's approved local model location before any model load or
   generation; record model paths by alias and count without retaining model
   filenames unless separately approved.
7. Treat any newly discovered asset as independently licensed.
8. Do not enable extension updates during the reproducibility launch.
9. Sanitize launch logs before retention.
10. Verify actual enabled extensions, bind address, and runtime-created paths
    during the controlled first launch.
