# Phase 0 First-Launch Attempt 2

## 1. Run identity

- Date: `2026-07-23`
- Stage: `D1 Attempt 2 — explicit-Python controlled retry`
- Branch: `docs/phase0-complete-baseline`
- Attempt 1 commit:
  `00409ea91f892f391994d7092eb7dcdf25b6d20e`
- Evidence run: `../Evidence/phase-0/04-cold-start/20260723-200201/`
- Outcome: `FAIL`
- Failure phase: evidence-harness wrapper-process creation
- Wrapper or application launched: no
- Further retry performed: no

## 2. Pre-launch gates

| Gate | Result |
|---|---|
| Branch is `docs/phase0-complete-baseline` | PASS |
| Worktree clean | PASS |
| HEAD contains committed Attempt 1 | PASS |
| `develop...HEAD` is `0 4` | PASS |
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
| Explicit Python candidate identity | PASS |

All authorized pre-launch gates passed.

## 3. Explicit Python-selection gate

The retry resolved:

```powershell
$pythonCandidate = Join-Path $env:USERPROFILE 'miniconda3\python.exe'
```

Verified result:

- file exists: yes;
- Python: 3.13.5;
- implementation: CPython;
- architecture: AMD64;
- pointer size: 64-bit;
- sanitized executable: `<USER>\miniconda3\python.exe`;
- candidate gate: `PASS`.

PATH was not modified. Conda was not activated, `conda run` was not used, and
the base environment was not modified.

## 4. Exact sanitized command

The following approved command was prepared but not executed:

```powershell
$pythonCandidate = Join-Path $env:USERPROFILE 'miniconda3\python.exe'
$env:PYTHON = $pythonCandidate
$env:VENV_DIR = (Join-Path (Get-Location) 'venv')
$env:WEBUI_LAUNCH_LIVE_OUTPUT = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'
$env:GRADIO_ANALYTICS_ENABLED = 'False'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
.\webui.bat --server-name 127.0.0.1 --port 7860 --ui-debug-mode --disable-all-extensions
```

The harness attempted to create its Windows wrapper process using the current
`ComSpec` value. That value was null or empty in the visible harness
environment, so PowerShell rejected the `Start-Process -FilePath` argument
before `webui.bat` could run.

No alternate command processor or explicit `cmd.exe` path was substituted.

## 5. Provisioning result

- Windows wrapper created: no
- repository venv created: no
- wrapper pip upgrade: not attempted
- launcher preparation: not entered
- configuration or runtime directories created: no

## 6. Package transaction

- Torch/torchvision request: not attempted
- packaging/Gradio request: not attempted
- requirements transaction: not attempted
- packages installed: none
- downloads: none
- package-cache mutation attributable to Attempt 2: none

## 7. CUDA validation

Forge did not start, so Torch CUDA validation and Forge CUDA initialization
did not occur.

The retained GPU record is a host-level baseline sample only. It is not
attributed to Forge because no launch-owned Forge process existed.

## 8. Extension and preload behavior

No extension installer, built-in preload, external preload, or runtime
extension code executed. The external extension directory remains empty.

## 9. Network destinations

No package, Git, model, telemetry, share, or tunnel host was contacted.
Package-network and application-network monitoring did not begin because the
wrapper process was never created.

## 10. Listener binding

- Port 7860 before process creation: unused
- Application listener created: no
- Public listener created: no
- Port 7860 after harness exit: unused

## 11. HTTP readiness

No loopback HTTP request was made because no application process existed.
HTTP readiness was not reached.

## 12. GPU result

The pre-process host sample reported 8,105 MiB used and 7,946 MiB free with
31 percent utilization. This was preexisting or unrelated activity. Attempt 2
created no launch-owned GPU process or attributable allocation.

## 13. Shutdown result

No application shutdown was necessary. The harness exited after
`Start-Process` rejected the null/empty wrapper executable. No Ctrl+C, force
termination, cleanup, deletion, or quarantine occurred.

## 14. Repository mutations

Complete before/after repository snapshots each contain 1,868 entries:

- created paths: zero;
- removed paths: zero;
- modified paths: zero.

`venv/`, `tmp/`, `config.json`, `ui-config.json`, and `.uv-cache/` remain
absent. No tracked file or external extension changed. Only evidence files
outside Git were created.

## 15. External mutations

The timestamped evidence directory and harness status/control files were the
only known writes. No pip, package, venv, Forge, model, or Hugging Face cache
mutation occurred.

## 16. Privacy review

Privacy result: `PASS`.

- Variable values were recorded only by absence/presence status.
- The Python identity is sanitized with `<USER>`.
- No credential, token, password, private absolute path, model filename,
  prompt, output, process command line, or network-share name is included.
- No broad packet capture occurred.
- Raw evidence remains outside Git.

## 17. Pass/fail/inconclusive assessment

Stage D1 Attempt 2 result: **FAIL**.

The explicit Python-selection retry solved the Attempt 1 PATH ambiguity and
passed every pre-launch gate. The attempt then failed deterministically in the
evidence harness before creation of the Windows wrapper process.

Provisioning, package installation, CUDA validation, UI-debug startup,
listener binding, HTTP readiness, and application shutdown remain untested.

## 18. Unexpected behavior

The visible foreground harness environment had a null or empty `ComSpec`
value. The reviewed harness used that value as the executable for
`Start-Process`, which PowerShell rejected.

The failure produced no repository runtime mutation and no package or network
activity.

## 19. Remaining blockers

1. Any further attempt requires new owner approval; no additional retry is
   authorized.
2. A future proposal must explicitly identify and review the Windows command
   processor used to invoke `webui.bat`, without changing the approved Python,
   PATH, arguments, versions, or indexes.
3. Provisioning, package resolution, CUDA validation, UI-debug readiness, and
   normal application shutdown remain untested.
4. No approved local model location exists; model loading and generation
   remain prohibited.

## 20. Requirements for any future attempt

- Preserve Attempt 1 and Attempt 2 evidence and documentation.
- Do not retry without explicit owner approval.
- Present a sanitized exact command-processor selection method for review.
- Continue using the verified explicit Python 3.13.5 candidate unless the
  owner directs otherwise.
- Re-run every pre-launch gate.
- Do not change PATH, activate Conda, substitute versions, add flags, change
  indexes, load models, generate images, or begin a later stage.
