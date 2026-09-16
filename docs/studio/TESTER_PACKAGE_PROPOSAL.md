# Studio Standalone: proposed tester package

> Historical proposal recorded before implementation. The owner accepted this
> scope. See [the completed review](RELEASE_PREPARATION_REVIEW.md) for current
> package contents, verification and remaining limits.

Prepared 2026-09-13. This is the requested contents checkpoint before assembly.
No new archive has been built. The full usability review and cleanup are still
to be completed.

Source inspected: docs/phase0-complete-baseline at
930b72ef9d7c46174f1080fce5cb7329e17afe78, with existing uncommitted changes.

## Proposed delivery

A Windows/NVIDIA friends-alpha ZIP containing the application source and launch
scripts. First launch installs Python packages into the extracted app.
Python itself, GPU drivers and generation models are separate prerequisites.
This is a source-and-bootstrap package; a fully offline executable distribution
is not currently implemented. Fresh-install verification is still required.

Alongside the ZIP: a SHA-256 checksum and a manifest identifying the exact
source snapshot, included auxiliary assets and their hashes. Final archive
size/version will be recorded at assembly. The current distribution identifier
is 0.0.0-internal-alpha.

## Included contents

| Component | Contents and purpose |
|---|---|
| Application | Studio browser interface, local HTTP service, headless integration and retained Forge Neo generation code. |
| UI assets | Local scripts/styles, V2 brush code, brand images, fonts/notices, locales, built-in data and help. Existing Canvas edits must be reviewed before selecting the release snapshot. |
| Retained source | Tracked Neo modules, built-in extension code, vendored packages and Studio sampler/scheduler plugins. Source inclusion does not certify every feature as usable through Studio. |
| Windows launch | Start-Studio.bat and start_studio.py, after resolving the fresh-config defect below. |
| Environment setup | app/scripts/bootstrap_environment.py, requirements and platform selector. Packages download during setup; the developer's environment is not copied. |
| Configuration | Clean template and first-run setup, excluding the owner's working configuration. |
| Auto Detail assets | Exactly the five detector files listed below. |
| Upscaling asset | Exactly the Remacri file listed below, following the existing bundled-assets record. |
| Tester documents | Start-here page, installation/start/stop instructions, usable/limited/unavailable/unverified feature matrix, known issues, update/rollback guide and feedback steps. These need refreshing against the candidate. |
| Diagnostics | Support-bundle script and instructions to inspect its output before voluntarily sharing. No existing logs or support bundles. |
| Source and notices | Application source, licenses, credits, upstream identity, patch inventory and release notes. The current builder also includes tracked tests and project documentation. |

The current builder selects every tracked app file, then adds the two workspace
launchers by name. At this inspection: 1,987 tracked files, 110,546,554 bytes
(105.43 MiB), before launchers, additional documents and auxiliary weights.
Tracked Docker/deployment source is included; this does not establish Docker
as a supported tester target.

The six auxiliary files add 132,628,390 bytes (126.48 MiB). Combined source and
asset payload is approximately 232 MiB uncompressed, before additions.
ZIP size and installed environment size have not been measured. The old
guide's approximately 2 GB disk estimate is not a verified installation total.

## Exact auxiliary model list

All six local files were hashed on 2026-09-13 and match
[BUNDLED_MODEL_ASSETS.md](BUNDLED_MODEL_ASSETS.md).

| Location inside app/ | Bytes | Purpose |
|---|---:|---|
| models/adetailer/face_yolov8n.pt | 6,230,011 | Face detection |
| models/adetailer/face_yolov8s.pt | 22,507,707 | Face detection |
| models/adetailer/hand_yolov8n.pt | 6,237,883 | Hand detection |
| models/adetailer/person_yolov8n-seg.pt | 6,777,003 | Person segmentation |
| models/adetailer/person_yolov8s-seg.pt | 23,850,731 | Person segmentation |
| models/ESRGAN/remacri_original.pt | 67,025,055 | 4x upscaling |

The existing asset record identifies Apache-2.0 for the detector weights and
records Remacri bundling as previously owner-approved with attribution, with
license status owner-asserted rather than independently verified. This review
verified file hashes, not those terms independently. Other locally installed
detectors/upscalers are excluded from this proposal.

## Separate prerequisites and setup downloads

- A Python interpreter for bootstrap. The script admits Python 3.10-3.13;
  that entire range has not been installation-tested in this review.
- An NVIDIA GPU and suitable driver for the proposed first target. Minimum
  hardware and a tested driver/runtime combination remain to be established.
- Internet access during environment setup for Torch and other dependencies.
  The Windows selector currently requests Torch 2.11.0+cu130 and torchvision
  0.26.0+cu130. This is a source observation, not a wheel-availability check.
- The tester's checkpoint and, where that model requires them, compatible text
  encoder and VAE files. LoRAs and embeddings are also supplied separately.

No separate Forge Neo installation or hosted backend is proposed: relevant
source is included and Studio serves its interface locally. Candidate runtime
network behavior remains to be verified. Setup downloads must be distinguished
from offline runtime claims in the guide.

## Excluded

- The owner's generation models and every file in Private-Local/. This review
  did not inspect that directory.
- Working studio-config.json, preferences, Gallery database, Canvas documents,
  generated images, saved prompts/state and existing logs.
- The developer's venv, caches, temporary files, Git history, old ZIPs,
  evidence/reference directories and unrelated scratch files.
- Unlisted auxiliary weights, optional accelerator wheels and GPU drivers.

State, results and logs are created fresh on the tester's machine as needed.

## Issues to resolve before assembly

1. **Fresh configuration fails.** start_studio.py::bootstrap_config indexes
   config["profiles"][0], but the template has model_roots and no profiles.
   A new installation would raise KeyError before completing setup. Existing
   configurations skip this branch. Verified from source, not executed here.
2. **Auxiliary assets are omitted.** build_distributable.py selects tracked
   files plus launchers. Git tracks placeholder text files under models/,
   but none of the six documented weights. Packaging needs an explicit,
   hash-verified asset list.
3. **Auto Detail dependency is undeclared.** ultralytics_boundary.import_yolo
   imports ultralytics, while main requirements do not declare it. Clean
   environment verification must establish the complete dependency set.
4. **A release snapshot is needed.** Six tracked files were already modified,
   with two additional untracked test/probe files. The builder refuses dirty
   trees by default. Its override would not give an accurate commit identity.
   Existing work must be preserved and reviewed.
5. **Tester instructions conflict.** The old runbook describes profiles and
   explicit Load; the current product contract describes selection then
   Generate. The guide says no weights are included, while the asset record
   lists six. Refresh these instructions and distinguish setup downloads
   from runtime behavior.

## Usability review still to complete

The final matrix will distinguish verified operation, limitations, unavailable
features and untested features. Historical release notes are not a current pass.

Initial source findings: the adapter returns None for ControlNet model and
preprocessor catalogues, and available: false for Live status. These should not
be advertised as ready based on the presence of their source files or UI alone.

Generation, Img2Img/Inpaint, Hires, Auto Detail, queue/cancellation, Canvas
painting/recovery, Gallery, presets, Wildcards/Lexicon, Develop, Workshop,
Regional workflows and restart behavior remain to be reviewed and checked for
this candidate. No parity, performance or cross-platform claim is made here.

This checkpoint changes documentation only. Runtime code, launchers,
dependencies, existing user changes and package contents are unchanged.
