# Phase 0 First-Launch Preflight

## 1. Preflight identity

- Date: `2026-07-23`
- Stage: `D0 — first-launch preflight only`
- Branch: `docs/phase0-complete-baseline`
- Product commit at preflight start:
  `9ba709d8e0a07565e7b38c4c358a436446835677`
- Local evidence: `../Evidence/phase-0/04-cold-start/preflight/`
- Application launched: no
- Virtual environment created: no
- Package installed or downloaded: no
- Model loaded or generation run: no

This plan combines verified source behavior, verified local status queries,
and explicitly marked runtime unknowns.

## 2. Current blockers

1. The selected bootstrap Python is 3.13.5. The launcher accepts Python 3.13
   by major/minor, but its message identifies 3.13.12 as tested. The owner has
   approved 3.13.5 only for this controlled provisioning and UI-debug startup
   attempt; this does not establish it as the final supported patch version.
   Failure must not be worked around by changing Python or package versions.
2. No repository virtual environment exists. The selected base environment is
   not a compatible Forge runtime: it is not repository-specific and contains
   CPU-only PyTorch 2.8.0.
3. Launcher-managed preparation, downloads, package installs, cache writes,
   repository mutations, preload execution, and a loopback listener require
   explicit owner approval.
4. Exact pip resolver output, transitive versions, wheel availability, and
   download size remain unknown until runtime.
5. No approved local model location exists. Provisioning and UI startup can be
   tested, but model loading and generation are blocked.

## 3. Intended Python and venv

| Item | Plan | Basis |
|---|---|---|
| Windows entry point | Direct `webui.bat` invocation from the repository root | Verified wrapper path |
| Bootstrap executable | Resolve `python.exe`; current result is `<USER>\miniconda3\python.exe` | Verified, sanitized |
| Bootstrap version | Python 3.13.5, 64-bit | Verified |
| Source requirement | Python 3.13; 3.13.12 named as tested | `modules/launch_utils.py:check_python_version` |
| Venv | `<REPOSITORY>\venv` | Explicit `VENV_DIR`; wrapper default |
| Existing compatible environment | No | No repository venv; base environment is CPU-only |
| Preparation | Required | New venv has no Forge packages |
| uv | Absent; not proposed | Verified command lookup; `modules_forge/uv_hook.py` |

`webui.settings.bat`, `VENV_DIR`, `SKIP_VENV`, `PYTHON`, and
`COMMANDLINE_ARGS` were absent at preflight. Direct wrapper arguments are used
because this repository's `webui.bat` forwards `%*`, while
`webui-user.bat`'s `COMMANDLINE_ARGS` value is not included in that launch
line.

The plan does not use `--skip-prepare-environment` or `--skip-install`.

## 4. Expected package plan

The expected first-run order is:

1. Create `venv/`.
2. Run the wrapper's unpinned `python -m pip install --upgrade pip`.
3. Request exactly:

   `torch==2.11.0+cu130 torchvision==0.26.0+cu130`

   using:

   `https://download.pytorch.org/whl/cu130`

4. Run the local accelerator-access assertion; CUDA is the expected backend.
5. Request `packaging==26.0` if absent.
6. Request `gradio==4.40.0 gradio_rangeslider==0.0.8` if absent.
7. Install `requirements.txt` when its simple pin check fails, then check it
   again after the installer phase.

`requirements.txt` contains 38 nonblank entries: 37 exact pins or marked
exact pins and one unpinned `torch` entry. Important behavior:

- `requirements_met()` accepts versions equal to or newer than a simple pin;
- it skips lines it cannot parse, including unpinned `torch`;
- pip applies the full requirements syntax during installation;
- `requirements_versions.txt` is absent;
- no xformers, SageAttention, FlashAttention, Nunchaku, bitsandbytes, ngrok,
  or ONNX Runtime GPU option is proposed.

The complete manifest is retained in local `package-plan.md`. Transitive
versions and the upgraded pip version are resolver-time unknowns.

## 5. Expected network endpoints

| Endpoint class | Plan | Classification |
|---|---|---|
| Default official PyPI index and official artifact/CDN hosts, including `files.pythonhosted.org` | pip upgrade and Python packages | Approved; exact hosts runtime-verified |
| `download.pytorch.org/whl/cu130` | Torch and torchvision CUDA wheels | Expected, verified source default |
| GitHub release assets | Optional wheels | Not expected; trigger flags and installer disabled |
| Azure ONNX Runtime index | Optional GPU runtime | Not expected; trigger flag omitted |
| User-extension Git remotes | Updates | Not expected; update flag omitted and directory empty |
| Hugging Face services | Model/config downloads | Not expected; offline variables proposed |
| Gradio share or ngrok | Public exposure | Not expected; flags omitted |
| Runtime license sources | License-tab browser fetch | Conditional; browser and tab interaction prohibited |

Proxy variables, pip/uv index variables, launcher index overrides, and checked
credential variables were absent. Common pip config files were absent.
Uninspected config layers, keyrings, netrc files, and credential stores remain
indeterminate; no values were captured.

Official hosts reached through documented redirects from the approved PyPI
and PyTorch services are also approved. Any unrelated package index, Git
repository, arbitrary wheel URL, extension remote, model host, telemetry
service, share service, or tunnel service is not approved.

## 6. Expected repository mutations

| Target | Expectation |
|---|---|
| `tmp/`, `tmp/stdout.txt`, `tmp/stderr.txt` | Created/overwritten by wrapper probes |
| `venv/` | Created and populated |
| `config.json` | Created with `VERSION_UID=PY313` |
| Python caches | Conditional import byproducts |
| `models/ControlNet/` | Created during Forge shared-module import |
| `models/ControlNetPreprocessor/` | Created during Forge shared-module import |
| `models/diffusers/` | Created during Forge shared-module import |
| `ui-config.json` | Creation not statically established |
| `.uv-cache/` | Not expected |
| `extensions/` | Must remain present and empty |
| Tracked product files | Must remain unchanged |

No output or generation mutation is expected.

## 7. Expected external cache and environment mutations

- pip cache: `%LOCALAPPDATA%\pip\cache`
- temporary build/extraction files: `%TEMP%`
- timestamped raw launch evidence outside Git
- possible dependency-, OS-, TLS-, or GPU-driver-managed caches, with exact
  paths unresolved

All package installs should target the repository venv. Base-Python package
mutation is not expected. uv cache writes are not expected because uv is
absent and no uv mode is proposed.

Forge sets several Hugging Face cache variables to `models/diffusers/` when
they are initially absent. The plan also sets Hugging Face and Transformers
offline modes so no model/config acquisition is expected.

## 8. Disk-space assessment

The repository, selected Python/venv, and pip cache are all on `C:`. They
therefore share one capacity figure:

- available: approximately 138.3 GiB;
- total: approximately 930.6 GiB.

Exact resolved download and installed sizes are unknown. The plan uses 15 GiB
free as a conservative abort threshold for CUDA wheels, environment files,
temporary extraction, and caches. Current headroom above that threshold is
approximately 123.3 GiB.

## 9. Extension execution plan

The proposed `--disable-all-extensions` argument causes launcher external and
built-in installer discovery to return empty lists. The current legacy
preprocessor installer must not run; its ten unpinned requirements,
`insightface`, and two GitHub wheels are outside the proposed transaction.

Two built-in preload files are still imported because
`modules/shared_cmd_options.py` scans all built-in preload files directly.
They add ControlNet log-level and LoRA-directory arguments. This is expected
code execution and requires owner approval.

Runtime extension scripts are intended to remain disabled. The external
extension directory must remain empty, and extension updates are not enabled.

## 10. Proposed local-only launch command

Run from the repository root through the logging harness:

```powershell
$env:PYTHON = (Get-Command python.exe -ErrorAction Stop).Source
$env:VENV_DIR = (Join-Path (Get-Location) 'venv')
$env:WEBUI_LAUNCH_LIVE_OUTPUT = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\webui.bat --server-name 127.0.0.1 --port 7860 --ui-debug-mode --disable-all-extensions
```

Port 7860 was unused at preflight. Explicit `127.0.0.1` removes reliance on
Gradio's default bind. The command omits listen, share, ngrok, API, public
server-name, autolaunch, extension-update, skip-install, and
skip-preparation modes. It contains no credential.

`--ui-debug-mode` returns from application initialization after loading UI
scripts and the built-in Lanczos upscaler definition; it does not load model
weights.

## 11. Logging and timing plan

Create a timestamped run directory outside Git. Before invocation, record UTC
and local time, branch, HEAD, worktree, parity, sanitized Python identity,
port status, disk space, extension count, process baseline, GPU baseline, and
a relative repository snapshot.

Pipe combined wrapper output through `Tee-Object` to `console.log`.
`WEBUI_LAUNCH_LIVE_OUTPUT=1` makes launcher pip activity visible. Record the
wrapper start and exit times plus exit code, then record venv `pip list
--format=json` and `pip check`.

In a second PowerShell session, poll `http://127.0.0.1:7860/` once per second.
The first HTTP 200 is `UI_READY`; retain timestamp and elapsed time. Do not
open a browser. Abort after 30 minutes without readiness; do not retry failed
install or accelerator steps.

For launch-owned PIDs only, sample TCP connections and UDP endpoints every
250 ms. This may miss very short-lived DNS or socket activity; broad packet
capture is excluded to avoid collecting unrelated private traffic.

Sanitize all retained logs before summarization.

## 12. File-mutation monitoring plan

Capture before/after repository trees as relative path, item type, length, and
UTC modification time, excluding `.git/`. Compare the snapshots and retain
created, deleted, and changed paths.

Also retain before/after:

- `git status --short`;
- `git diff --stat`;
- full tracked diff if any;
- venv package inventory;
- external extension directory count.

Any tracked change, extension addition, or unexplained write is an abort and
review condition.

## 13. GPU-memory monitoring plan

Before launch and once per second during the launch-owned process lifetime,
record:

```text
nvidia-smi --query-gpu=timestamp,index,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits -l 1
```

Retain baseline, peak used memory, post-shutdown memory, and errors. CUDA
initialization and a small warm-up allocation are expected. Sustained growth
consistent with weight loading is an abort condition.

## 14. Shutdown and rollback plan

After readiness evidence, send one Ctrl+C to the foreground wrapper and allow
30 seconds for its process tree to exit. Capture final process, network, GPU,
filesystem, package, and Git state.

If shutdown hangs, capture state and stop only the recorded launch-owned
process tree after separate owner approval.

A successful `venv/`, configuration, expected runtime-directory state, pip
cache, and temporary cache state must be preserved as part of the later
baseline. Do not quarantine or delete successful expected state.

Rollback after failure or unexpected mutation remains recoverable: do not
delete or reset. Quarantine requires separate owner approval after each target
is resolved and verified. Do not move anything that existed before launch.
Preserve external caches by default. Report any tracked change without using
Git reset or checkout.

## 15. Abort conditions

Abort if:

- HEAD/worktree, selected Python, extension count, settings state, or approved
  environment-variable status differs from the launch baseline;
- Python is not the approved 64-bit 3.13 interpreter;
- port 7860 is occupied or free space is below 15 GiB;
- an unsafe flag, credential, credential prompt, unapproved host/package,
  update, extension installer, or model/config download appears;
- venv creation, pip upgrade, Torch install, CUDA validation, requirements
  install, or dependency validation fails;
- the listener is not exclusively `127.0.0.1:7860`;
- a tracked file or external extension changes;
- model loading, generation, unexpected GPU growth, or an unexplained external
  write occurs;
- loopback HTTP 200 is not received within 30 minutes.

Do not retry, change versions/indexes, bypass checks, add extensions, or
introduce skip flags without a new owner review.

## 16. Model-load status

No recognized checkpoint, VAE, LoRA, embedding, ControlNet model, ADetailer
detector, or upscaler weight is present in the audited repository paths.

Stage D may provision the environment and test loopback UI startup under
`--ui-debug-mode`. First model load and generation remain blocked until the
owner identifies and separately approves a local model location.

## 17. Owner approvals still required

For the controlled Stage D1 attempt, the owner has approved:

1. Python 3.13.5 for this provisioning and UI-debug attempt only.
2. Creating `venv/`, `tmp/`, `config.json`, runtime model/cache directories,
   Python caches, and external evidence.
3. The unpinned pip upgrade.
4. Network resolution and installation of exact Torch/torchvision, Gradio,
   packaging, the 38-entry requirements manifest, and transitive dependencies.
5. Writes to pip/temp/dependency/GPU caches outside the repository.
6. Import and execution of the two built-in preload files.
7. Binding a local service to `127.0.0.1:7860`.
8. CUDA initialization, GPU monitoring, process/network sampling, filesystem
   snapshots, and sanitized logging.
9. The proposed offline model-service variables and the no-model boundary.
10. One normal Ctrl+C shutdown request after readiness evidence.

Separate approval is still required for forced termination of surviving
processes, quarantine or deletion, model loading, generation, public exposure,
extension installation/update, retry, version substitution, arbitrary
network destinations, later stages, or push.

## 18. Unknowns

- Exact upgraded pip and transitive dependency versions.
- Python 3.13.5 wheel availability for every resolved package.
- Download bytes, installed bytes, cache hits, and final transaction duration.
- Effective default pip hostname and any uninspected configuration/keyring
  influence.
- Short-lived network activity that process-scoped sampling may miss.
- Runtime-created paths beyond the statically verified list, including whether
  `ui-config.json` is created.
- Actual loopback bind, process tree, UI-ready time, GPU peak, and shutdown
  behavior.
- Whether any dependency attempts background version, telemetry, certificate,
  or cache network activity despite the proposed controls.
