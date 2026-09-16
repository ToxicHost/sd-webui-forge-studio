# Source review: tester installation and packaging
2026-09-13; baseline 930b72ef9d7c46174f1080fce5cb7329e17afe78.
Branch release/tester-alpha-prep. Generation and painting behavior unchanged.

Studio personally read: scripts/build_distributable.py (all), bootstrap_environment.py
(all), workspace start_studio.py (bootstrap_config/main/relay), Start-Studio.bat,
forge_studio/launch.py::load_config, detector_catalogue.py constants/admission,
ultralytics_boundary.py::configure_offline/assert_offline/import_yolo.
Findings: obsolete profiles in wrapper; current config supports empty model_roots;
builder omits six recorded auxiliary assets; environment probe checks only Torch;
launcher skips repair when interpreter exists; ultralytics 8.3.119 is present in
developer environment but undeclared in main requirements.

Extension b316a4d87abd69837cd723403a7a41e22f97482d (version.json):
Reference/Forge-Studio-main/Forge-Studio-main/install.py and
scripts/studio_adetailer.py read completely. Install delegates dependencies to Neo;
AD catalogue delegates to the separate ADetailer extension. That implicit dependency
must be made explicit in standalone. No Gallery behavior change inferred.

Neo 97ff3a4024be2f0d5316f16e868e5ef822768872:
launch.py::main, modules/launch_utils.py::prepare_environment, requirements_met,
is_installed, run_pip, check_run_python, _torch_version, run_extension_installer,
run_extensions_installers and listing helpers personally read. Environment setup
checks dependencies, installs selected Torch then requirements/extension dependencies.
Restored byte-exact launch_utils.py from local pinned Git object under
Reference/package-prep-neo-97ff3a40/modules/. Verified blob
9728ff2d8cfcab64d4ec5eff2d2e1616315d8dcb. Recovery copy differed only after newline
normalization; the exact restored source is authoritative for this record.

Change: track release launchers; create current-schema configuration without private
model scans; preserve existing config bytes and fast-fp16 default; declare ultralytics;
repair partial setup; explicitly include six assets with hashes; provide exact ZIP
member/source identity. Keep Studio's standalone bootstrap, no Gradio installation.
No Neo-owned edit required.

Tests: first-run config/unchanged existing config; incomplete environment repair;
missing/tampered assets refuse before output; versioned launcher provenance;
reproducible ZIP and extracted empty-root smoke with isolated state.

No Extension/Neo parity or inference performance claim. Existing Canvas changes
remain owner work. Clean dependency resolution and candidate usability need execution.


## Tester package trimming — 2026-09-13
Baseline d28353146ee46392b12f76b5c4bae1d4297693ed. Owner requested removing
development clutter from the tester distribution. This extends the installation
review above; no model-loading, inference, or painting behavior is edited.

Current Studio personally read: build_distributable.py (all),
build_support_bundle.py (all), test_distributable_privacy.py (all),
backend_bootstrap.py::bootstrap_backend and its module helpers,
compute_plugins.py (all), modules/paths.py and paths_internal.py (all),
presentation.py static-root/alias declarations, launch.py::load_config.
Studio serves forge_studio/frontend, bootstraps its engine without the legacy
WebUI loader, and explicitly discovers studio_plugins. Keep these whole runtime
trees, backend model configuration/tokenizer data, modules, modules_forge and
extensions-builtin (including LoRA/Soft Inpainting and third-party notices).
Remove only top-level legacy UI assets/launchers and development tooling/docs
from the tester archive; no upstream source file is edited or deleted.

The Extension install.py / studio_adetailer.py and pinned Neo launch.py /
launch_utils.py review above remains applicable to setup. Neither defines a
Standalone tester archive or Studio support-report service. Packaging deliberately
uses Studio's launcher rather than Neo's WebUI bootstrap. No new parity claim.

Diagnostics currently imports its scanner from tests.studio_alpha. Extract its
constants/readable_text/leaks_in unchanged into scripts/distribution_privacy.py;
retain the former test-file self-exemption and use the same scanner in tests,
archive building and diagnostics. No change to collected fields or redaction.

Plan: tracked runtime allowlist, separate full tracked-source archive, exact six
asset hashes, tester-only guide/status/notes plus attribution and template.
Verify both archive profiles, exclusions and all retained bytes; extract and
start Studio without a repository; generate a support report without tests.
Unreviewed: real generation with model weights, dynamic feature branches beyond
startup. Full engine and extension trees are retained to avoid speculative pruning.
Evidence class: VERIFIED-CODE; execution results to follow. No Neo-owned edit.


Extraction finding: initial lean candidate served HTTP but engine bootstrap failed
because modules/shared_cmd_options.py imports root launch.py. Personally read both
full files; launch.py re-exports launch_utils used by the engine and is required.
Retain launch.py in the runtime allowlist, while webui.py/.bat/.sh stay source-only.
The startup check now requires populated registries at the prior observed counts
(23 samplers, 18 schedulers, 6 latent and 4 image upscalers), not HTTP 200 alone.


## Empty model folders — owner follow-up, 2026-09-13
Baseline 68b9c41abd261dc95a3af2f537d11088e0a93e88. Owner requested empty
checkpoint, VAE and text-encoder directories in the tester archive. The current
builder and archive tests were read again; Git tracks no empty directories and
the runtime selection deliberately omits the old model-folder placeholder files.
Write explicit empty ZIP directory entries for app/models/Stable-diffusion/,
app/models/VAE/ and app/models/text_encoder/, using existing tracked folder names.
No local model-directory contents are read or copied. No model-discovery, engine,
configuration or frontend implementation changes. Extension/Neo setup review above
remains applicable; neither owns this Studio ZIP layout. No new parity claim.
Verify extraction creates three genuinely empty directories; preserve file_count
as files and add separate directory/entry counts to the manifest. Rebuild both
profiles from a clean commit. No Neo-owned edit or new runtime behavior.


## Python selection failure — owner follow-up, 2026-09-13
Baseline 967b0b04ff6bccc97c42f18a44e7e0dbd01a1696. Tester launcher selected
Python 3.10 from PATH and bootstrap refused it. Current Start-Studio.bat,
bootstrap_environment.py (all), root release launcher and setup tests personally
read. Existing launcher assigns PYTHON=python when unset, hiding whether the owner
set an override. It checks only that Python executes, not that it is supported.
The recorded resolver metadata confirms unchanged NumPy 2.3.5 requires >=3.11.

Extension b316a4d8 install.py read completely again: delegates dependency install
to Neo's launch.run_pip. Neo 97ff3a40 exact launch_utils.py: module imports,
check_python_version, run, requirements_met, prepare_environment setup section
through Torch/CUDA check read again. Neo checks the interpreter already executing
and uses sys.executable; it does not choose a suitable Windows runtime for Studio.
Its Python warning targets 3.13. Studio must keep its own standalone setup path.

Change: retain explicit PYTHON override; when unset, bootstrap may re-execute
with installed Python 3.13/3.12/3.11 via Windows py launcher if the initial Python
is unsupported. Probe version and executable using stdlib, no shell or filesystem
crawl. Disable legacy py automatic installation during discovery. Preserve an
existing Studio venv rather than rebuilding an incompatible one automatically.
Batch can use py when python is unavailable. Dependency pins, config, engine,
model selection and Canvas remain unchanged. No Neo-owned edit or parity claim.

Tests: PATH 3.10 plus registered supported runtime; newest unsupported runtime;
missing/failed/timed-out probes; explicit override; existing venv preservation;
child setup failure propagates; supported interpreter needs no discovery;
read-only --check-python never installs. Actual global Python installations are
not enumerated during agent work (workspace boundary); use mocked probes and
existing in-workspace interpreter. Official Windows py command syntax checked:
https://docs.python.org/3.13/using/windows.html#python-launcher-for-windows

Python launcher discovery disables both legacy install-on-demand flags and the
modern PYTHON_MANAGER_AUTOMATIC_INSTALL setting; official Windows docs verified
this setting at https://docs.python.org/3/using/windows.html#configuration.
