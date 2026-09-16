# Resolve Preflight Git-State Fix — Semantic `.gitattributes` Support

Branch: `feature/runtime-unblock-preflight-fix`

Records the root cause of the false `RESOLVE_PLAN_DIRTY_WORKTREE` blocker on a
verifiably clean repository, the correction applied, and the fail-closed
boundary that correction preserves.

---

## 1. Symptom

A fresh ResolvePlan on a clean tree reported:

```text
authorization_eligible: false
authorization_blockers: ["RESOLVE_PLAN_DIRTY_WORKTREE"]
clean_worktree_state:   INCONCLUSIVE_GIT_STATE
dirty_bound_paths:      []
untracked_files:        []
```

A dirty-worktree blocker with no named dirty path. Independent Git checks
(`git status --short`, `git diff --check`) reported a clean tree.

---

## 2. Root cause

`scripts/preflight/resolve_authorization.py` does **not** shell out to Git. It
proves cleanliness in-process: it reads `.git/index` directly, parses the
stage-zero entries, computes Git blob SHA-1s over worktree bytes, derives the
index tree object ID, resolves the HEAD commit's tree from loose objects, walks
`.gitignore` rules to enumerate untracked files, and then re-reads everything
to detect mid-proof mutation.

That design has one unavoidable dependency: **the comparator must know how
checked-out bytes relate to blob bytes.**

Git attributes can change that relationship. `text`, `eol`,
`working-tree-encoding`, `filter`, and `ident` all mean a file on disk may
legitimately differ from the blob it came from. The comparator
`_tracked_blob_matches` modelled exactly one such case — a hardcoded CRLF→LF
retry guarded by an index stat-cache check, approximating `core.autocrlf`.

Faced with an attributes file it could not interpret, the tool refused to
guess:

```python
if _repository_attributes_present(fs, app_root, entries):
    raise ValueError("git-attributes-unsupported")
```

`_repository_attributes_present` returned `True` on the mere **existence** of
any candidate attributes path. `raise` propagated to the `except (…​,
ValueError, …​)` handler wrapping `_bound_worktree_state`, which returned
`_inconclusive_worktree_state()` — the all-`null`/`false` placeholder. That
placeholder then failed the `clean_worktree is not True` test in
`_authorization_blockers`, producing `RESOLVE_PLAN_DIRTY_WORKTREE`.

So the chain was:

```text
app/.gitattributes exists
  -> _repository_attributes_present() == True
  -> ValueError("git-attributes-unsupported")
  -> _inconclusive_worktree_state()
  -> clean_worktree = None
  -> _authorization_blockers() -> RESOLVE_PLAN_DIRTY_WORKTREE
  -> authorization_eligible = false
```

The tool was never wrong about the repository. It was wrong about its own
ability to describe it, and the vocabulary it had for saying so was
`DIRTY_WORKTREE`.

The tracked file is `app/.gitattributes`, added by `8e424291 refactor: restore
canonical Studio 4.17 frontend` to hold the canonical frontend's LF bytes and
prevent text conversion of pinned binary and font assets. It is load-bearing
and was not touched.

---

## 3. Why the fix is a parser, not an exception

The repository's attributes are:

```gitattributes
forge_studio/frontend/** text eol=lf
forge_studio/frontend/** whitespace=-trailing-space
forge_studio/frontend/fonts/OFL.txt -text
forge_studio/frontend/**/*.png -text
forge_studio/frontend/**/*.ttf -text
```

Every one of these has exactly modellable checkout semantics:

- `-text` — no conversion. Worktree bytes are blob bytes.
- `text eol=lf` — checkout writes LF; check-in normalizes CRLF→LF.
- `whitespace=…` — affects `git diff`/`git apply` warnings only. It never
  alters checked-out bytes.

Nothing here requires guessing. A filename allowlist was rejected as the
primary fix because it would suppress the signal rather than interpret it: any
future `filter=lfs` or `working-tree-encoding=UTF-16` line added to the same
file would then be silently ignored, and the tool would confidently report a
clean tree it could no longer actually verify.

---

## 4. The correction

### 4.1 Supported subset

`_parse_git_attributes_rules` accepts only:

| Token | Meaning |
|---|---|
| `text` | text conversion applies |
| `-text` | no conversion; exact byte match required |
| `text=auto` | conversion applies unless content contains NUL |
| `eol=lf` | LF checkout (implies `text` since Git 2.10) |
| `eol=crlf` | CRLF checkout (implies `text`) |
| `whitespace`, `-whitespace`, `whitespace=…` | accepted, no checkout effect |

Everything else raises. Explicitly rejected: `[attr]` macro definitions, the
built-in `binary` macro, `filter`, `working-tree-encoding`, `ident`, the
`!attr` unspecified form, unknown attribute names, unrecognised `eol=` values,
negative patterns, quoting, backslash escapes, `..` path components, patterns
with no attributes, non-UTF-8 content, and directory-suffixed patterns.

### 4.2 Pattern matching

`_match_attribute_pattern` mirrors the existing `.gitignore` segment matcher:
per-segment `fnmatch`, with `**` matching zero or more whole segments so
`a/**/b.png` matches both `a/b.png` and `a/c/d/b.png`. Patterns containing `/`
are anchored to the repository root; patterns without `/` match the **basename
only**, which is the attributes rule, not the ignore rule.

### 4.3 Last-match-wins

`_effective_checkout_semantics` walks all rules in file order and lets later
matches override earlier ones per attribute. `OFL.txt` therefore ends with
`text=unset` from rule 3 even though rule 1 matched it first, and is compared
byte-exactly. `.png` and `.ttf` behave the same way via rules 4 and 5.

### 4.4 Case-sensitivity ambiguity

`_bound_checkout_semantics` resolves each path twice on Windows — once
case-sensitively, once case-folded. If the two disagree, it raises
`git-attributes-case-ambiguous` rather than pick one. A path whose applicable
attributes depend on filesystem case behaviour is not something this tool will
adjudicate.

### 4.5 Comparator

`_tracked_blob_matches` gained an optional `semantics` argument:

- exact blob hash match is still tried first, always;
- if attributes **are** specified for the path, `_attribute_normalized_matches`
  decides — normalization is attempted only when text semantics are actually
  set, is refused for `-text` and for `text=auto` content containing NUL, and
  requires UTF-8-decodable content;
- if attributes are **not** specified, the pre-existing `core.autocrlf`
  heuristic runs unchanged, stat-cache guard included.

Paths outside the attributes file's reach behave exactly as before this change.

### 4.6 Trusting the attributes file itself

The attributes file must be tracked and clean by the same definition applied to
every other path. After the tracked loop:

```python
if (
    source_path not in tracked_snapshots
    or f"app/{source_path}" in dirty
    or tracked_snapshots[source_path] != _hash_bytes(source_content)
):
    raise ValueError("git-attributes-tracked-snapshot-changed")
```

An untracked, modified, or unreadable `.gitattributes` fails closed. Reusing
the loop's own verdict rather than re-deriving one keeps the two consistent by
construction.

A byte-exact index comparison was tried first and rejected: `app/.gitattributes`
is not covered by its own rules, so on this Windows checkout with
`core.autocrlf=true` it is stored LF and checked out CRLF. Demanding exact
bytes would have failed closed on the real repository forever — the same class
of error as the original defect.

### 4.7 Nested attributes files

Not modelled. `_repository_attributes_model` raises
`git-attributes-unsupported` if any attributes path other than the repository
root `.gitattributes` exists — including `.git/info/attributes` and any nested
`forge_studio/**/.gitattributes`. Directory-relative precedence is a real part
of the format and implementing it untested would be worse than declining it.
This is the explicit fail-closed option the owner ruling permits.

### 4.8 Snapshot recheck

The end-of-proof recheck previously re-tested attributes *presence*. It now
rebuilds the whole model and compares its digest, so a mid-proof edit to the
attributes content — not merely its appearance — is caught and fails closed
with `snapshot_rechecked: false`.

---

## 5. Test matrix

`GitAttributesCleanlinessTests` in
`scripts/preflight/tests/test_resolve_authorization.py`, 36 tests, mapped to
the owner-approved in-process rows:

| Row | Scenario | Expected | Status |
|---|---|---|---|
| 1 | Clean repository with the current supported attributes | `CLEAN`, eligible | pass |
| 1 | CRLF worktree under `text eol=lf` | `CLEAN` | pass |
| 2 | Modified tracked file under `text eol=lf` | `DIRTY_BOUND_INPUTS` | pass |
| 3 | Modified tracked file under `-text` | `DIRTY_BOUND_INPUTS` | pass |
| 3 | `text=auto` with NUL content not normalized | `DIRTY_BOUND_INPUTS` | pass |
| 4 | Staged change | `DIRTY_STAGED_CHANGES` | pass |
| 5 | Deleted tracked file | `INCONCLUSIVE_GIT_STATE`, ineligible | pass |
| 6 | Renamed tracked file | `INCONCLUSIVE_GIT_STATE`, ineligible | pass |
| 7 | Untracked file | `DIRTY_UNTRACKED_FILES` | pass |
| 8 | Ignored file | `CLEAN` | pass |
| 9 | Supported whitespace-only attribute | `CLEAN` | pass |
| 10 | `filter=lfs` | `INCONCLUSIVE_GIT_STATE` | pass |
| 11 | `working-tree-encoding=UTF-16` | `INCONCLUSIVE_GIT_STATE` | pass |
| 12 | `ident` | `INCONCLUSIVE_GIT_STATE` | pass |
| 13 | `[attr]` macro, `binary`, unknown name, `!text`, `eol=native`, negative pattern, quoted pattern | `INCONCLUSIVE_GIT_STATE` | pass |
| 14 | Pattern with no attributes, non-UTF-8 file, `..` component | `INCONCLUSIVE_GIT_STATE` | pass |
| 15 | Unreadable, untracked, and modified attributes file | `INCONCLUSIVE_GIT_STATE` | pass |
| 16 | Corrupt index, unreadable index | `INCONCLUSIVE_GIT_STATE` | pass |
| 17 | Missing HEAD ref, missing commit object | `INCONCLUSIVE_GIT_STATE` | pass |
| 18 | Nested `.gitattributes`, `.git/info/attributes` | `INCONCLUSIVE_GIT_STATE` | pass |
| — | Attributes content changed mid-proof | `snapshot_rechecked: false` | pass |

### Row 5/6 — a preserved pre-existing behaviour

A deleted tracked file yields `INCONCLUSIVE_GIT_STATE`, not
`DIRTY_BOUND_INPUTS`. The tracked loop does record it as dirty, but the
snapshot-recheck loop re-reads every stage-zero entry with no
`FileNotFoundError` guard, so the missing path raises and the proof fails
closed before returning.

This predates the change and is untouched by it. Both outcomes are
authorization-ineligible, so no eligibility can be wrongly granted, and
correcting it would mean editing recheck logic outside the authorized scope.
The tests assert the actual behaviour — including an explicit
`_authorization_blockers` assertion — so it stays visible and cannot silently
become "clean" later. Recommend a separately scoped follow-up.

---

## 6. Validation

```text
scripts/preflight/bootstrap.py self-test    179 tests   OK   (143 pre-existing + 36 new)
tests/studio_alpha/run_tests.py              63 tests   OK
```

All 143 pre-existing preflight tests pass unchanged. Three that specifically
assert fail-closed behaviour on attributes files —
`test_ignored_untracked_attributes_file_fails_closed`,
`test_local_info_attributes_file_fails_closed`, and
`test_attributes_appearance_during_proof_fails_closed` — still pass without
modification, via the untracked-source, nested-path, and snapshot-recheck
guards respectively.

Against the real repository mid-edit, the proof now completes and names exactly
the files being edited:

```text
clean_worktree        : False
clean_worktree_state  : DIRTY_BOUND_INPUTS
complete_git_status   : True
snapshot_rechecked    : True
dirty                 : ['app/scripts/preflight/resolve_authorization.py',
                         'app/scripts/preflight/tests/test_resolve_authorization.py']
untracked             : []
```

Before the fix this same repository returned `INCONCLUSIVE_GIT_STATE` with an
empty dirty list regardless of its contents.

---

## 7. Boundary preserved

Not done, by rule:

- `.gitattributes` not deleted, edited, untracked, or bypassed;
- no filename allowlist;
- no Git subprocess introduced;
- no owner-override path added;
- dirty, staged, and untracked detection unchanged;
- command, parse, object, index, and snapshot failures all still fail closed;
- canonical frontend files unchanged and still byte-pinned;
- the pre-fix ResolvePlan bundle preserved intact.

Files changed:

```text
app/scripts/preflight/resolve_authorization.py
app/scripts/preflight/tests/test_resolve_authorization.py
```
