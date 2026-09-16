# Phase 0 First-Launch Attempt 1

## 1. Run identity

- Date: `2026-07-23`
- Stage: `D1 — controlled environment provisioning and UI-debug startup`
- Branch: `docs/phase0-complete-baseline`
- D0 commit:
  `15d64554e4976696c1c67c06b260cfe12d59b743`
- Evidence run: `../Evidence/phase-0/04-cold-start/20260723-193344/`
- Outcome: `FAIL`
- Failure phase: pre-launch gate
- Wrapper or application launched: no
- Retry performed: no

## 2. Pre-launch gates

| Gate | Result |
|---|---|
| Branch is `docs/phase0-complete-baseline` | PASS |
| Worktree clean | PASS |
| HEAD matches committed D0 document | PASS |
| `develop...HEAD` is `0 3` | PASS |
| `neo...upstream/neo` is `0 0` | PASS |
| External extension count is zero | PASS |
| Port 7860 unused | PASS |
| Free space at least 15 GiB | PASS; approximately 133.9 GiB |
| `venv/`, `tmp/`, settings, UI settings, and `.uv-cache/` absent | PASS |
| Recognized model-weight count is zero | PASS |
| Approved behavior-changing variables absent | PASS |
| Approved proxy/index/credential variables absent | PASS |
| Known global/user pip config files absent | PASS |
| Exact command contains no credential | PASS |
| Selected Python is approved 3.13.5 | **FAIL; visible launch shell resolved Python 3.10.11** |

The harness stopped immediately on the failing gate.

## 3. Exact sanitized command

The following approved command was prepared but not executed:

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

No argument, environment override, Python path, version, index, or package was
substituted after the gate failed.

## 4. Python and venv result

The visible foreground PowerShell environment resolved `python.exe` to:

`<USER>\AppData\Local\Programs\Python\Python310\python.exe`

That executable reported Python 3.10.11. The approved interpreter was Python
3.13.5, so the harness aborted. The repository venv remains absent.

The Codex inspection shell had previously resolved Python 3.13.5 from a
different PATH context. This difference was unexpected and is not treated as
authorization to set or substitute an explicit executable.

## 5. Package transaction result

- pip upgrade: not attempted
- Torch/torchvision request: not attempted
- packaging/Gradio request: not attempted
- requirements transaction: not attempted
- packages installed: none
- downloads: none
- package cache mutation attributable to D1: none

## 6. Torch and CUDA result

No repository venv exists, so venv Torch and torchvision were not inspected.
Forge did not initialize Torch or CUDA.

A post-gate host-level `nvidia-smi` sample was retained only to document host
GPU state. Its memory and utilization are not attributed to Forge because no
launch-owned Forge process existed.

## 7. Requirements validation

`requirements.txt` installation and validation did not run. `pip check` was
not run because the repository venv is absent.

## 8. Extension/preload behavior

No extension installer, built-in preload, external preload, or runtime
extension script executed. The wrapper and application import path were never
entered. The external extension directory remains empty.

## 9. Network destinations observed

No package, Git, model, telemetry, share, or tunnel destination was contacted
by Stage D1. Package-network activity never began.

No process-scoped application network timeline exists because there was no
launch-owned application process.

## 10. Repository mutations

No runtime path was created:

- `venv/`: absent
- `tmp/`: absent
- `config.json`: absent
- `ui-config.json`: absent
- `.uv-cache/`: absent

No tracked file changed, no external extension was added, and recognized
model-weight count remained zero. Only evidence files outside Git were
created.

The harness stopped before its full pre-launch filesystem CSV step. A
post-gate snapshot is retained; the missing before snapshot is recorded as an
evidence limitation rather than reconstructed.

## 11. External cache mutations

No pip, venv, model, Hugging Face, or Forge cache mutation was caused by D1.
The repository-external evidence directory and status files were the only
known writes.

## 12. Listener and readiness result

- Port 7860 before gate: unused
- Application listener created: no
- Loopback HTTP request made: no
- HTTP 200 readiness: not reached
- Port 7860 after harness exit: unused

## 13. GPU-memory result

Forge GPU initialization did not occur. The retained post-gate host sample
reported 6,473 MiB used and 9,578 MiB free with 43 percent utilization. This
was preexisting or unrelated host activity; there was no launch-owned Forge
process from which to attribute a change.

## 14. Shutdown result

No application shutdown was required. The evidence harness exited after the
gate failure. No Ctrl+C, force termination, cleanup, deletion, or quarantine
operation was performed, and no launch-owned process survived.

## 15. Privacy review

Privacy result: `PASS`.

- Variable values were not printed or retained.
- The executable path is sanitized with `<USER>`.
- No credential, token, password, private absolute path, model filename,
  prompt, output, or network-share name is included.
- No process command line or broad packet capture was retained.
- Raw evidence remains outside Git.

## 16. Pass/fail/inconclusive assessment

Stage D1 result: **FAIL**.

The failure is deterministic at the approved pre-launch Python-selection
gate. Environment provisioning and UI-debug startup are untested, not
inconclusive: the approved attempt was correctly aborted before them.

## 17. Unexpected behavior

The visible foreground PowerShell created for the approved launch resolved
`python.exe` differently from the prior Codex inspection shell:

- prior inspection context: Python 3.13.5;
- visible launch context: Python 3.10.11.

The harness itself also stopped before its full filesystem-before snapshot.
No product or runtime mutation occurred as a consequence.

## 18. Remaining blockers

1. The intended visible launch environment does not currently resolve the
   owner-approved Python 3.13.5 executable.
2. A retry would require new owner approval and an explicitly reviewed method
   for selecting Python without silently substituting versions.
3. Environment provisioning, exact package resolution, CUDA validation,
   loopback startup, and normal shutdown remain untested.
4. No approved local model location exists; model loading and generation
   remain prohibited.

## 19. Requirements for the next stage

- Do not retry Stage D1 without explicit owner approval.
- Before any retry, identify why the visible shell PATH differs and present a
  sanitized, exact Python-selection proposal.
- Do not change Python, package versions, indexes, arguments, or skip flags
  without approval.
- Re-run every pre-launch gate on any newly approved attempt.
- Preserve this failed-attempt evidence and the clean repository state.
- Do not begin model loading, generation, or a later stage.
