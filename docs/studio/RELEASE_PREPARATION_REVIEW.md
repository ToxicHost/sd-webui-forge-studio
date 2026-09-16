# Release preparation review — 2026-09-13

Current Standalone work is preserved in commit de0a11ea on
release/tester-alpha-prep. Packaging changes are a separate commit. The final
archive manifest records its exact source commit and every included file hash.

## Changes

- Versioned Windows launchers and explicit, hash-verified release assets.
- Current-schema first-run configuration without private model discovery.
- Existing configurations preserved and incomplete requirements installation repaired.
- ultralytics pinned to 8.3.119; Python minimum corrected to 3.11 because
  the unchanged NumPy pin requires it.
- Standalone README, tester guide, feature matrix and historical-doc warnings.
- No generation, sampler, model-loading or painting implementation changes.

## Verification completed

- 148 existing Canvas tests: 147 passed immediately; all individual file pins
  matched, and regenerating two stale aggregate fingerprints made the final
  source-faithfulness check pass. No Canvas source edits were made by this review.
- 53 final packaging, bootstrap, privacy and archive tests passed, including
  two byte-identical ZIP builds.
- 464 fixture-based feature tests passed for generation request/lifecycle,
  Inpaint, Hires, Auto Detail, queue, recovery, Gallery, Wildcards and preferences.
- Dependency resolution succeeded for 94 packages on Windows/Python 3.13.5
  with the declared CUDA Torch pins. This was a dry run, not a new installation.
  filterpy and antlr4-python3-runtime require source-distribution support; the
  normal launcher supports that. The binary-only diagnostic was not sufficient.
- Initial full archive: extracted 2007 entries and verified every member hash.
  That artifact is superseded by the trimmed tester archive described below.
  The extracted source started using the existing development interpreter,
  detected the NVIDIA GPU, and reported no loaded generation model.
- HTTP checks passed for the interface, status, model state, queue, detectors
  and registries. Five detector entries were found, with 23 samplers and 18
  schedulers registered. ControlNet returned None, Live reported unavailable,
  Workshop and saved Develop presets returned 404, matching the feature matrix.
- Ordinary extraction into a deeply nested evidence folder exceeded Windows
  path limits. Long-path-aware extraction passed. The guide now requests a
  short installation path; no system setting was changed.
- Git diff whitespace checks passed.

## Limits

No fresh environment was installed and no real generation model was loaded.
A real GPU image, model switches under load, interactive browser/tablet use,
clean-machine installation and other platforms still need tester verification.
The developer environment's pip check reports a Gradio/Pillow conflict. Gradio
is absent from standalone requirements and that environment is not bundled.
This candidate should not be described as a certified release.

An ephemeral port can reset browser-local settings; the guide explains how to
choose a stable port. Canvas undo history is not restored after recovery.

## Source provenance and repository update

See [source review](RELEASE_SOURCE_REVIEW.md). The six auxiliary asset hashes
match the recorded asset list. Personal config, models, logs, results and venv
are excluded. Raw evidence remains in the owner's workspace, outside the archive.

GitHub main was still the original Neo baseline when checked; develop also
exists remotely. The source push was rejected by automatic approval review,
which requested explicit approval for the exact source/destination. No source
was uploaded during this preparation. Local commits are ready for that step.


## Tester archive cleanup requested by the owner

The tester profile now intersects tracked source with packaging/runtime.json.
Project plans, agent instructions, root tests, Docker/deployment files, old WebUI
launchers and release tooling remain in Git and the separate -source.zip.
The tester root has Start-Studio.bat, start_studio.py, START-HERE.md and app/.
The app root retains LICENSE, UPSTREAM_BASE, requirements.txt, launch_studio.py
and launch.py. launch.py is required by the engine via shared_cmd_options;
removing it was caught by the extracted startup check and corrected.

The complete engine, built-in extensions, Studio source/frontend and compute
plugins remain byte-for-byte unchanged. Six verified auxiliary assets remain.
The support-report scanner moved from the test module to a shared utility;
collection fields and redaction behavior are unchanged.

76 focused packaging/setup/privacy/diagnostics checks passed after correcting a
link-check false positive on an inline code example. The reduced archive's
extracted startup passed with the existing interpreter: 23 samplers, 18 schedulers,
6 latent and 4 image upscalers, 5 detectors, no generation model loaded. The smoke
check now asserts populated registries instead of accepting HTTP 200 alone.
All 23 final archive checks passed after retaining launch.py.

The full source ZIP includes all tracked files and build controls, without model
weights or personal data. Each archive has its own file manifest and checksum.
The tester/source artifacts are rebuilt from a clean committed tree. Real image
generation and clean-machine installation remain unverified in this pass.
