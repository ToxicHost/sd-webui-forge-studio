# Result Root Case Policy — B3

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

---

## 1. The problem

Result-delivery containment used `Path.relative_to()` and inherited a
case-folding decision gated on `os.name == "nt"`.

That gets macOS wrong. macOS exposes a **POSIX** API over a
**case-insensitive** filesystem by default, so `os.name` reports `posix` and
the code takes the case-sensitive branch on a volume that is not
case-sensitive. The reverse also exists: a case-sensitive APFS volume can be
mounted anywhere, including on macOS.

**Case sensitivity is a property of the volume, not of the operating system.**
No OS name answers it correctly.

---

## 2. The policy

```python
class CasePolicy(str, Enum):
    CASE_SENSITIVE   = "case_sensitive"
    CASE_INSENSITIVE = "case_insensitive"
    AUTO_DETECT      = "auto_detect"
    INCONCLUSIVE     = "inconclusive"
```

`ResultRegistry(root, case_policy=...)` resolves the policy **once, at
construction**, and stores the result. Every later containment decision reads
that stored value. `AUTO_DETECT` (the default) runs the probe; an injected
policy is used verbatim.

`registry.case_policy` never returns `AUTO_DETECT` — by construction it has
already resolved to one of the other three.

---

## 3. The probe

`detect_case_policy(root)`:

1. resolve `root` strictly; a missing path or non-directory is `INCONCLUSIVE`;
2. build two names from one 32-hex token, differing only in case;
3. if either already exists, return `INCONCLUSIVE` — never risk an existing path;
4. create the lowercase name with `open(path, "xb")` — exclusive creation,
   which refuses to clobber and never follows an existing link;
5. look up the uppercase variant;
6. if found, confirm `samefile()` — a coincidence is not evidence;
7. remove the probe in a `finally` block.

Safety properties:

| Requirement | How |
|---|---|
| at most one tiny empty file | one `open(..., "xb")`, zero bytes written |
| random opaque name | `token_hex(16)`, prefixed `.studio-case-probe-` |
| tests a case-variant lookup | lower vs upper of the same token |
| removes all artifacts | `finally: probe.unlink()` |
| never clobbers | pre-existence check plus `x` mode |
| never follows a link out | `x` mode fails on an existing symlink |
| leaves nothing persistent | verified by a before/after directory listing test |
| stays under the configured root | verified by a sibling-directory test |
| no external temp | no `tempfile`, `gettempdir`, `mkstemp`, or `TMPDIR` in the module |
| no elevated permissions | ordinary file create in a directory Studio already owns |
| deterministic for tests | inject a policy, or patch `Path.exists` / `Path.samefile` |
| fails closed on uncertainty | **every** step is inside one `except OSError` |

The last row was a real defect found by its own test: the pre-existence check
initially sat outside the guarded block, so an `OSError` there would have
propagated out of the probe instead of yielding `INCONCLUSIVE`. The whole
sequence is now inside the guard.

Cleanup failure is the one case that cannot fail closed — the verdict is
already computed. It leaves at most one empty, randomly-named,
obviously-prefixed file inside the owned result root, and never raises.

---

## 4. Fail-closed behaviour

`INCONCLUSIVE` blocks containment entirely. `_contained()` checks it first, so:

- **registration** fails with `RESULT_OUTSIDE_ROOT`;
- **reads** fail with `RESULT_NOT_FOUND`, even for a handle registered earlier
  by a working registry.

Case behaviour that could not be established means containment cannot be
decided safely, and a delivery boundary that cannot decide must refuse.

---

## 5. Containment under each policy

`_within_root()` replaces the bare `relative_to()`:

- **`CASE_SENSITIVE`** — exact `relative_to()` only. A case-differing prefix is
  a different path and is rejected.
- **`CASE_INSENSITIVE`** — exact match first; failing that, a case-folded
  component-wise prefix comparison. On such a volume two spellings genuinely
  name the same path, so this is correct containment, not a relaxation.
- **`INCONCLUSIVE`** — refused before the check is reached.

The case-folded comparison is **component-wise**, not a string prefix, so
`/tmp/RESULTS_OTHER/x.png` is not treated as inside `/tmp/results`. Pinned by
`test_case_variant_sibling_root_is_still_outside`.

Handles themselves are unaffected: they are opaque tokens matched by exact
dictionary lookup, so a case-variant handle is rejected under **both**
policies. Case policy governs filesystem containment only.

---

## 6. Test coverage

27 tests in `tests/studio_alpha/test_result_root_case_policy.py` covering the
20 required cases. Both policies are exercised on one Windows machine by
injection, so the macOS-shaped combination — POSIX API over a case-insensitive
volume — is tested without a Mac.

`test_auto_detect_agrees_with_observed_behaviour` is the one that matters most:
it runs the probe, then independently creates a case-variant file and checks
the volume's real behaviour, asserting the probe's verdict matches. The probe
is validated against reality rather than assumed correct.

`test_containment_no_longer_consults_os_name` walks the AST of `_contained`
and `_within_root` and asserts neither reads `os.name` — the guarantee holds
structurally, not just for the paths the tests happen to take.

One test skips where the OS forbids symlink creation, joining the two existing
skips.

---

## 7. Scope

`.gitattributes` was not modified. The canonical frontend was not modified.
Result delivery's opaque handles, MIME allowlist, retention, sandboxed response
CSP, and traversal rejection are all unchanged — regression-tested under both
policies.

The preflight Git-state proof has its own `os.name`-gated case folding. It is
**deliberately out of scope** here and recorded in
[`MAC_PREFLIGHT_CASE_SENSITIVITY_FOLLOWUP.md`](MAC_PREFLIGHT_CASE_SENSITIVITY_FOLLOWUP.md).

---

## 8. What is still unverified

- **No macOS filesystem was probed.** The insensitive path is covered by
  injection, not by a real case-insensitive volume.
- **`AUTO_DETECT` has only ever resolved on NTFS**, which reported
  case-insensitive on this machine.
- **No case-sensitive APFS volume was tested.**
