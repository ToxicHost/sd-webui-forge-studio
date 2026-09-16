# Mac Diagnostic Bundle — Specification

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

**Specification only. Not implemented in this milestone.** No diagnostic was
collected, no Mac was contacted, and no MPS probe exists in the codebase.

---

## 1. Purpose

If a volunteer ever runs Studio on Apple Silicon, this defines the *maximum*
they would be asked to send back. The design goal is that a tester can read the
whole allow-list in a minute and see nothing that identifies them.

---

## 2. Allow-list — the complete set

Nothing outside this table may be collected.

| Field | Source | Stage |
|---|---|---|
| macOS version | `platform.mac_ver()[0]` | T1 |
| architecture | `platform.machine()` | T1 |
| CPU brand | `platform.processor()` or the `sysctl` brand string | T1 |
| total unified memory | OS-reported total, GB | T1 |
| Python version | `sys.version_info` | T1 |
| Python architecture | `platform.architecture()[0]` | T1 |
| repository commit | `git rev-parse HEAD` | T1 |
| filesystem case result | `detect_case_policy(project_folder)` | T1 |
| Torch present in project venv | import-spec check, **not** an import | T2 |
| MPS availability | `torch.backends.mps.is_available()` | **T2, only if separately approved** |
| Studio test counts | run/failed/skipped | T1 |
| mock-runtime results | loopback check pass/fail per check | T1 |
| output hashes | SHA-256 of generated artifacts | T1 |
| elapsed times | wall clock per stage | T1 |

---

## 3. Deny-list — never collected

```text
serial number          Apple ID            username
full home path         hostname            IP address
network interfaces     installed apps      browser data
shell history          keychain data       unrelated file listings
environment variables  process list        disk layout
Wi-Fi networks         Bluetooth devices   location
```

Hostname may be included **only** if the tester opts in explicitly. It is
excluded by default.

---

## 4. Redaction

Paths are the leak risk: `/Users/<name>/...` contains the username, and the
username is often the person's real name.

- every absolute path is rewritten relative to the project folder;
- the project folder itself is reported as `<PROJECT>`;
- any residual `/Users/<something>/` is replaced with `/Users/<REDACTED>/`;
- tracebacks are redacted with the same rules before inclusion;
- redaction is applied to the file that is written, not only to what is
  displayed — a tester must never have to trust that a viewer hid something.

---

## 5. Behaviour

The bundle generator must:

- write only inside the project folder;
- run entirely in user space, never requiring `sudo` or an administrator
  account;
- support `--dry-run`, printing exactly what would be collected and where it
  would be written, collecting nothing;
- print every write path before writing;
- refuse to run as root;
- make no network request of any kind, including telemetry and update checks;
- import nothing outside the standard library at T1;
- **not import Torch at T1** — presence is checked with `importlib.util.find_spec`,
  which does not execute the package;
- not initialise MPS, CUDA, or any accelerator unless the MPS field is
  separately approved for T2;
- not download a model, ever;
- terminate on any unexpected condition rather than continuing;
- produce one human-readable file the tester can inspect **before** deciding to
  send it.

That last point is the load-bearing one. The tester reads the output and
chooses whether to share it. Nothing is transmitted by the tool.

---

## 6. Staging

| Stage | Fields | Requires |
|---|---|---|
| T0 | none — hashes and file listing only | package receipt |
| T1 | everything except Torch presence and MPS | tester consent for T1 |
| T2 | adds Torch presence; MPS **only** with separate approval | tester consent for T2 |
| T3 | adds generation timings and output hashes | tester consent for T3 |

Consent for one stage never implies the next.

---

## 7. Why MPS is gated separately

`torch.backends.mps.is_available()` initialises Metal. That allocates GPU
resources and is a real runtime action, not an inspection — which is why the
handoff prohibits implementing MPS probing in this milestone and why the field
needs its own approval even at T2.

Everything else in the allow-list is a string lookup or a test count.

---

## 8. Example output shape

Illustrative only; no such file has been produced.

```json
{
  "schema_version": "studio-mac-diagnostic/v1",
  "stage": "T1",
  "macos_version": "15.x",
  "architecture": "arm64",
  "cpu_brand": "Apple M4 Pro",
  "unified_memory_gb": 24,
  "python_version": "3.13.x",
  "python_architecture": "64bit",
  "repository_commit": "<40 hex>",
  "filesystem_case_policy": "case_insensitive",
  "project_root": "<PROJECT>",
  "studio_tests": {"run": 215, "failed": 0, "skipped": 0},
  "mock_runtime_checks": {"total": 25, "passed": 25},
  "output_hashes": [],
  "elapsed_seconds": {"studio_tests": 0.0},
  "torch_present": null,
  "mps_available": null
}
```

`null` means not collected at this stage — distinct from `false`, which would
be a positive finding. The same distinction the capability contract makes
between `UNKNOWN` and a reported value.

Note `"skipped": 0`: macOS permits symlink creation, so the three tests that
skip on this Windows machine would run there. A macOS run would have slightly
**more** effective coverage than the Windows baseline.

---

## 9. Not in scope

- no implementation;
- no MPS probing;
- no upload, telemetry, or transmission of any kind;
- no crash reporter;
- no persistent identifier;
- no comparison against other testers.
