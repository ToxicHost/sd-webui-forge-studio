# Mac Tester T0/T1 Package

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

**The package is built and reviewed. It has not been sent, and no volunteer has
been contacted. Nothing has been executed on macOS.**

---

## 1. Identity

```text
archive : Evidence\studio-mac-tester-t0-t1\studio-mac-t0-t1.tar.gz
size    : 9,001,297 bytes (8.6 MB)
sha256  : 61d47c160ce5ee897f0c01dc3ca9faac2cc764bd7e8d556e9e94b160ffd180b6
```

One top-level folder, `studio-mac-t0-t1/`, containing 99 files.

The archive is deterministic — uid/gid 0, empty owner names, mtime 0, mode
0644 throughout — so rebuilding from identical inputs reproduces the same
bytes and the same hash.

## 2. Contents

```text
studio-mac-t0-t1/
    README_FIRST.md          what this is, what it never asks for, how to undo
    CONSENT_CHECKLIST.md     per-stage; T0 consent is not T1 consent
    COMMANDS_EXACT.md        every command, verbatim
    FILES_CREATED.md         every file that appears, by path
    EXPECTED_RESULTS.md      Windows reference numbers, and what differs
    DATA_DISCLOSURE.md       exactly what would be shared
    ROLLBACK.md              delete one folder
    mac_t0_verify.sh         POSIX sh, no Python, no writes
    mac_t1_studio_mock.sh    POSIX sh, uses an existing python3
    SHA256SUMS.txt           98 entries covering every other file
    payload/app/
        forge_studio/                 owned application + pinned frontend
        tests/studio_alpha/           the owned test suite
        modules/platform_selection.py imported by the platform tests
        modules/launch_utils.py       read as text for AST analysis, never imported
        launch_studio.py              demo and mock server entry point
```

Payload is 89 files, 23.0 MB uncompressed. 18 MB of that is the canonical
frontend's tag-autocomplete CSVs, which belong to the 64-file pinned manifest
the tests verify — excluding them would silently weaken T1.

Excluded as required: `.git`, `venv`, `models`, `outputs`, caches,
`Private-Local`, `extensions`, unrelated Evidence, and package wheels.

## 3. What each stage does

**T0** — hash and file verification. Runs `id`, `find`, `shasum`, `awk`,
`grep`, `sed`. No Python, no writes, no network. Five checks: required files,
no symlinks, no executables or archives, manifest integrity, and manifest
paths relative and contained. Ends `T0_PASS` or `T0_FAIL`.

**T1** — runs T0 first, then the owned Studio suite, the loopback validator,
and the socket-free demo, using an already-installed `python3`. It imports no
Torch, starts no Forge or Neo process, loads no model, generates no real
image, and transmits nothing.

## 4. Python version gate

T1 requires **Python 3.13 or later**. It never installs Python and never
suggests Homebrew, sudo, or a system installer.

When `python3` is absent or older, it prints the observed version and
architecture, then stops with:

```text
T1_BLOCKED_PYTHON_VERSION
```

and exits 3. This is a realistic outcome — recent macOS ships Python 3.9 as
`/usr/bin/python3` — and is documented to the tester as a valid result to
report rather than a prompt to install anything.

## 5. Safety properties

Both scripts refuse UID 0, support `--dry-run`, print every command before
running it, redact `/Users/<name>/` and `$HOME` from **all** output, stop on
the first unexpected condition, and print the cleanup procedure.

Neither invokes `sudo`, `brew`, `curl`, `wget`, or pip. The word `sudo` appears
only in the sentence promising it is not used — verified by scanning for the
token in command position after stripping quoted strings.

T1 caps each step at 600 seconds using a shell poll loop rather than GNU
`timeout`, which macOS does not ship.

Every write lands under `payload/Evidence/`, because the validator derives its
output root from `Path(__file__).resolve().parents[2]`, which is inside the
package. T0 writes nothing.

## 6. Validation performed

All twelve required static checks pass. Full detail:
`Evidence\studio-fast-mac-hardening\package-security-review.md`.

Additionally run here: `sh -n` on both scripts (parse only — both clean) and
`sh mac_t0_verify.sh --dry-run`, which by design executes nothing and produced
correct output for all five sections.

**Not run:** T0 in real mode, T1 in any mode, anything on macOS.

One correction worth recording: the first forbidden-command scan reported
`sudo` in both scripts. That was a false positive in the checking method — it
matched the reassurance prose. Re-run against command position after stripping
string literals, both scripts are clean.

## 7. Before this is sent

The owner still must decide and fill in:

1. **Delivery method** — how the archive reaches the volunteer, and how they
   receive the SHA-256 out of band to check against.
2. **The consent checklist's `<<FILL>>` fields** — package hash, folder, and
   the volunteer's own confirmations.
3. **Whether to offer T1 at all**, given that stock macOS Python is 3.9 and
   `T1_BLOCKED_PYTHON_VERSION` is the likely first result. T0 is useful
   regardless and carries essentially no risk.

No stage may run until the owner approves the exact package and the volunteer
knowingly consents to that specific stage.

## 8. Status

```text
package built        : yes
package reviewed     : yes, PASS
package sent         : no
volunteer contacted  : no
any stage executed   : no
macOS runtime claim  : none
```
