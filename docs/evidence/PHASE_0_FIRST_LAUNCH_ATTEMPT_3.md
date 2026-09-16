# Phase 0 First-Launch Attempt 3

## 1. Attempt identity

- Date: `2026-07-23`
- Stage: `D1 Attempt 3 — explicit-Python and explicit-cmd controlled launch`
- Branch: `docs/phase0-complete-baseline`
- Baseline HEAD: `8efc498dfbae0e9f3cf81c0d689c7cd456aba3a4`
- Evidence run: `../Evidence/phase-0/04-cold-start/20260723-201947/`
- Outcome: `FAIL`
- Failure phase: post-install dependency validation
- Retry performed: no
- Model loaded or generation run: no

## 2. Attempt 1 and Attempt 2 references

| Attempt | Documentation commit | Result |
|---|---|---|
| Attempt 1 | `00409ea91f892f391994d7092eb7dcdf25b6d20e` | Failed at PATH-selected Python identity before launch |
| Attempt 2 | `8efc498dfbae0e9f3cf81c0d689c7cd456aba3a4` | Explicit Python passed; evidence wrapper was not created because `ComSpec` was empty |

Both earlier timestamped evidence directories and their documentation were
preserved unchanged.

## 3. Full pre-launch gate table

| Gate | Result |
|---|---|
| Branch is `docs/phase0-complete-baseline` | PASS |
| Worktree clean | PASS |
| HEAD is committed Attempt 2 | PASS |
| `develop...HEAD` is `0 5` | PASS |
| `neo...upstream/neo` is `0 0` | PASS |
| External extension count is zero | PASS |
| Port 7860 unused | PASS |
| Free space at least 15 GiB | PASS; approximately 133.8 GiB |
| `venv/` absent | PASS |
| `tmp/` absent | PASS |
| `config.json` absent | PASS |
| `ui-config.json` absent | PASS |
| `.uv-cache/` absent | PASS |
| Recognized model-weight count is zero | PASS |
| Approved behavior-changing variables absent | PASS |
| Proxy, index, and credential variables absent | PASS |
| Known global/user pip configuration files absent | PASS |
| Explicit Python candidate | PASS |
| Explicit System32 command processor | PASS |
| Exact command contains no credential | PASS |

All authorized gates passed before any repository runtime state was created.

## 4. Explicit Python identity

The approved candidate was resolved without a PATH change:

```powershell
$pythonCandidate = Join-Path $env:USERPROFILE 'miniconda3\python.exe'
```

Verified result:

- sanitized executable: `<USER>\miniconda3\python.exe`;
- Python: 3.13.5;
- implementation: CPython;
- architecture: AMD64;
- pointer size: 64-bit;
- probe exit code: 0.

Conda was not activated, `conda run` was not used, and the base environment was
not modified.

## 5. Explicit command-processor identity

The command processor was resolved only from the Windows System special
folder:

```powershell
$systemDirectory = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::System
)
$cmdCandidate = Join-Path $systemDirectory 'cmd.exe'
```

Verified result:

- System special-folder result: nonempty;
- candidate: ordinary, non-reparse file;
- sanitized identity: `<WINDOWS>\System32\cmd.exe`;
- version: `Microsoft Windows [Version 10.0.26200.8875]`;
- `/d /c ver` probe exit code: 0;
- command-processor AutoRun processing: disabled with `/d`.

`ComSpec` and PATH were neither read for selection nor modified. No alternate
command processor, wrapper, fallback, or executable substitution was used.

## 6. Harness correction

The retained Attempt 2 harness and the root evidence harness were initially
byte-identical. Attempt 3 made these reviewed changes:

1. update the expected baseline to the committed Attempt 2 HEAD and
   `develop...HEAD` count;
2. resolve, validate, sanitize, and version-probe the System32 `cmd.exe`;
3. add the command-processor facts and abort gates to preflight evidence; and
4. replace `Start-Process -FilePath $env:ComSpec` with
   `Start-Process -FilePath $cmdCandidate`.

The working directory, output capture, monitoring, readiness, one-Ctrl+C
shutdown policy, privacy controls, and Forge argument order were unchanged.
The Attempt 3 copy is retained in its timestamped evidence directory.

## 7. Exact sanitized launch command

```powershell
$pythonCandidate = Join-Path $env:USERPROFILE 'miniconda3\python.exe'
$systemDirectory = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::System
)
$cmdCandidate = Join-Path $systemDirectory 'cmd.exe'
$env:PYTHON = $pythonCandidate
$env:VENV_DIR = (Join-Path (Get-Location) 'venv')
$env:WEBUI_LAUNCH_LIVE_OUTPUT = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
& '<WINDOWS>\System32\cmd.exe' /d /c `
    'call webui.bat --server-name 127.0.0.1 --port 7860 --ui-debug-mode --disable-all-extensions'
```

The evidence harness added `< NUL 2>&1` inside its command line for
noninteractive output capture. It did not add, remove, reorder, or replace any
Forge launch argument.

## 8. Environment provisioning

The verified wrapper:

- created `<REPOSITORY>\venv` from the approved Python 3.13.5 candidate;
- upgraded venv pip from 25.1.1 to 26.1.2;
- installed the launcher-requested Torch, packaging, Gradio, and repository
  requirements transactions;
- created `tmp/` and its empty wrapper stdout/stderr files;
- created `config.json` containing the runtime version marker
  `{"VERSION_UID": "PY313"}`;
- created three empty model-search directories; and
- created Python bytecode caches reached during initialization.

It did not create `ui-config.json` or `.uv-cache/`.

## 9. Package transaction

The transaction completed these ordered phases:

1. wrapper pip upgrade;
2. `torch==2.11.0+cu130` and `torchvision==0.26.0+cu130`;
3. `packaging==26.0`;
4. `gradio==4.40.0` and `gradio_rangeslider==0.0.8`; and
5. `requirements.txt`.

The requirements phase successfully installed its requested packages and the
launcher continued to `Launching Web UI`. It also emitted pip's dependency
conflict marker because the repository requirement replaced Pillow 10.4.0
with Pillow 12.3.0 while installed Gradio 4.40.0 declares
`pillow>=8.0,<11.0`.

No version, index, package, or flag was substituted by the evidence harness.

## 10. Installed venv state

| Component | Installed result |
|---|---|
| Python | 3.13.5, CPython, AMD64, 64-bit |
| pip | 26.1.2 |
| setuptools | 69.5.1 |
| packaging | 26.0 |
| Gradio | 4.40.0 |
| Gradio RangeSlider | 0.0.8 |
| Pillow | 12.3.0 |

The complete sanitized package inventory is retained as `pip-list.json`
outside Git.

## 11. Torch and CUDA validation

| Fact | Result |
|---|---|
| Torch | 2.11.0+cu130 |
| torchvision | 0.26.0+cu130 |
| Torch CUDA build | 13.0 |
| `torch.cuda.is_available()` | true |
| Visible CUDA devices | 1 |
| Selected device | NVIDIA GeForce RTX 5060 Ti |

Forge reported 16,311 MiB total VRAM and successfully selected `cuda:0`.
`initialize_forge()` imported Torch and torchvision, selected the CUDA device,
performed its one-element CUDA warm-up, and released its cache before reaching
the later Gradio import.

This validates Torch/CUDA initialization only. No model was discovered,
loaded, or allocated.

## 12. Requirements validation

Post-run `pip check` exited 1:

```text
gradio 4.40.0 requires pillow<11.0,>=8.0, but pillow 12.3.0 is installed.
```

The conflict is reproducible from owned inputs:

- `modules/launch_utils.py` defaults to `gradio==4.40.0`; and
- `requirements.txt` pins `Pillow==12.3.0`.

Requirements validation therefore failed even though pip's requirements
installation command completed and the launcher proceeded.

## 13. Extension and preload behavior

- External extension count was zero before and after the attempt.
- `--disable-all-extensions` made launch-time external and built-in installer
  discovery return no installers; no extension installation or update marker
  was observed.
- Forge was interrupted inside `initialize_forge()` before
  `webui.py` could call `initialize.imports()`.
- The two built-in preload hooks are reached later through shared command
  option initialization. No preload marker or resulting exception was
  observed, so runtime preload execution was not reached in this attempt.

No external extension, built-in asset, or product source file changed.

## 14. Contacted network hosts

Hostnames observed in approved resolver or download output:

| Host | Classification |
|---|---|
| `download.pytorch.org` | Approved official PyTorch distribution host |
| `pypi.org` | Approved official Python package host |

Unapproved hostname count: zero.

Process-scoped connection sampling may retain short-lived IP endpoints whose
hostnames were not printed. No Git, model, telemetry, share, tunnel, or public
application destination was observed.

## 15. Repository mutations

Git state remained clean at the committed Attempt 2 HEAD. Runtime evidence
recorded:

- 52,092 created paths;
- zero removed paths; and
- six modified directory entries caused by new descendants.

Of the created paths, 52,037 are under `venv/`. The remaining 55 are:

- `config.json`;
- `tmp/`, `tmp/stdout.txt`, and `tmp/stderr.txt`;
- three empty model-search directories;
- root, backend, modules, and Forge Python bytecode-cache paths.

`ui-config.json` and `.uv-cache/` remain absent. No tracked content changed.

## 16. External cache mutations

The official package transactions downloaded artifacts and used the
user-local pip cache. The console also records locally built wheels stored
under the sanitized user pip-cache location.

The evidence did not take a complete before/after inventory of that external
cache, so the exact external artifact delta is inconclusive. Offline Hugging
Face and Transformers controls remained set; no model or Hugging Face
download was observed.

## 17. Listener binding

- Port 7860 before launch: unused.
- Launch-owned listener during the attempt: none observed.
- Public listener: none observed.
- Port 7860 after shutdown: unused.

Forge did not reach Gradio server creation, so the intended exclusive
`127.0.0.1:7860` binding was not runtime-validated.

## 18. HTTP readiness

The harness performed only its approved loopback readiness polls. No HTTP 200
was observed, and no browser or API workflow was opened.

HTTP readiness remains unvalidated.

## 19. GPU timeline

The aggregate host GPU samples ranged from 2,926 to 6,750 MiB used and from
0 to 37 percent utilization. The sample started with unrelated or preexisting
GPU use and did not show the greater-than-2,048-MiB launch-era increase that
would have triggered the model-allocation safety gate.

Forge's console output verifies CUDA device selection and the small warm-up.
The exact Forge share of aggregate GPU memory is otherwise inconclusive.

## 20. Shutdown

The harness detected pip's `ERROR:` dependency-conflict marker and issued its
single approved Ctrl+C. The signal interrupted Gradio import while HTTPX was
loading its certificate authorities, producing the retained
`KeyboardInterrupt`.

The interruption is shutdown evidence, not evidence of a separate HTTPX,
certificate, or Forge exception.

- Ctrl+C sent and generated: yes;
- forced termination: no;
- launch-owned survivors: zero;
- approval-required survivor flag: absent;
- listener count after shutdown: zero;
- retry: none.

The wrapper exit-code field was blank in the retained shutdown record, so its
numeric exit code is inconclusive.

## 21. Privacy review

Privacy result: `PASS`.

- Raw console evidence remains outside Git and contains local paths.
- The sanitized console contains no user-specific absolute path.
- No high-confidence secret, credential-bearing URL, bearer value, private
  model filename, prompt, output, or network-share name was found.
- Environment variables were recorded by presence/absence status only.
- Process command lines were not retained.
- Network sampling was restricted to launch-owned process IDs.
- No broad packet capture occurred.
- This document uses `<USER>`, `<WINDOWS>`, and `<REPOSITORY>` placeholders.

## 22. PASS, FAIL, or INCONCLUSIVE outcome

Stage D1 Attempt 3 result: **FAIL**.

Attempt 3 resolved the prior Python and command-processor gates, created the
approved venv, completed package installation, and validated Torch CUDA device
availability. It failed the required dependency-consistency criterion:
`pip check` reports the Gradio 4.40.0 and Pillow 12.3.0 conflict.

The harness stopped on the first observed failure marker. UI readiness,
exclusive loopback binding, built-in preload execution, and normal
post-readiness shutdown remain unvalidated.

## 23. Unexpected behavior

1. The launcher installs Gradio 4.40.0 with a compatible Pillow 10.4.0, then
   the repository requirements transaction replaces it with incompatible
   Pillow 12.3.0.
2. Pip reports the conflict with an `ERROR:` prefix but returns successfully
   from the install transaction, allowing Forge initialization to begin.
3. Because the harness correctly stops on `ERROR:`, its Ctrl+C arrived during
   Gradio/HTTPX import. The resulting traceback is shutdown-induced.
4. The evidence harness did not retain a numeric wrapper exit code.

## 24. Remaining blockers

1. The owned Gradio/Pillow dependency conflict requires owner-reviewed
   resolution; no package or source change is authorized.
2. The current venv and runtime paths are preserved. A future gate must account
   for them explicitly rather than assuming a clean cold-start filesystem.
3. Loopback listener exclusivity and HTTP 200 readiness remain unverified.
4. The two built-in preload hooks remain unverified at runtime.
5. Normal shutdown after readiness remains unverified.
6. The external pip-cache delta and numeric wrapper exit code are
   inconclusive.
7. No approved model location exists; model loading and generation remain
   prohibited.

## 25. Requirements for the next stage

- Preserve all three evidence directories, the current venv, runtime state,
  and this report.
- Do not retry until the owner approves a specific dependency-resolution
  proposal and revised pre-launch gates.
- Do not delete, recreate, repair, or manually alter the venv.
- Do not substitute Python, command processor, package versions, indexes,
  arguments, or flags without explicit approval.
- Re-run dependency validation before any future readiness result can pass.
- Continue to require exclusive `127.0.0.1:7860`, UI-debug mode, disabled
  extensions, offline model controls, and one-signal shutdown.
- Do not load a model, generate output, alter the Neo control surface, push,
  or begin a later phase.
