# Preflight Case-Sensitivity — Follow-Up

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

**The preflight tool is not Mac-validated.** Nothing in this document claims
otherwise. It records exactly what would need attention if the preflight
cleanliness proof were ever run on macOS.

**Not changed in this milestone**, by instruction: B3 fixed the owned
result-delivery boundary only. No current test fails because of that change,
so no preflight change was warranted. This is a specification for a future
bounded phase.

---

## 1. The six sites

All in `app/scripts/preflight/resolve_authorization.py`. Every one folds case
if and only if `os.name == "nt"`.

| # | Line | Function | Role |
|---|---|---|---|
| P1 | 2564 | `_match_git_pattern` | folds the observed path segment |
| P2 | 2565 | `_match_git_pattern` | folds the `.gitignore` pattern segment |
| P3 | 2640 | `_git_path_is_ignored` | folds a path component for a non-anchored rule |
| P4 | 2645 | `_git_path_is_ignored` | folds the rule pattern for the same comparison |
| P5 | 2700 | `_untracked_repository_files` | builds the tracked-path set, folded |
| P6 | 2723 | `_untracked_repository_files` | folds a walked path before the membership test |

P1-P4 govern `.gitignore` matching. P5-P6 govern untracked-file detection.

The semantic `.gitattributes` parser added earlier already handles this
correctly: `_bound_checkout_semantics` computes semantics both folded and
unfolded on Windows and raises `git-attributes-case-ambiguous` when they
disagree. That pattern is the model for a fix here.

---

## 2. What goes wrong on macOS

macOS reports `os.name == "posix"`, so all six take the **unfolded** branch —
on a filesystem that is case-insensitive by default.

### False-inconclusive (safe, annoying)

P1-P4: a `.gitignore` rule written `*.PNG` would not match `photo.png` under
unfolded comparison, though Git on that volume would match it. The file is
then treated as **not ignored**, so it is reported untracked, so the tree is
`DIRTY_UNTRACKED_FILES` and the plan is ineligible.

Wrong, but wrong in the safe direction: it refuses to certify a repository
that is actually clean. This is the same class of defect as the original
`.gitattributes` bug — a clean tree reported unusable.

### False-clean (unsafe, and the reason this matters)

P5-P6 are the dangerous pair. The tracked set is built from index paths
unfolded; walked paths are compared unfolded.

On a case-insensitive volume, a file created as `Forge_Studio/App.py` while the
index holds `forge_studio/app.py` is **the same file**. Unfolded comparison
puts the walked spelling outside the tracked set, so it is reported as an
untracked file. That is again false-dirty, not false-clean.

The genuine false-clean risk is the mirror case: two index entries differing
only in case — `README.md` and `readme.md` — cannot both exist as distinct
files on a case-insensitive volume. One shadows the other. The proof would
compare the surviving file against **one** index entry, find it matching, and
never notice that the second tracked path has no distinct file behind it. A
tracked path would be silently unverified while the tree reports `CLEAN`.

That is a soundness hole, not merely an inconvenience. It requires a repository
containing case-colliding tracked paths, which this one does not have — but the
proof must not depend on that.

### Not affected

The `.gitattributes` semantic parser (`_bound_checkout_semantics`) already
fails closed on case ambiguity. Blob comparison is content-hash based and
case-independent. Index parsing, HEAD resolution, and the snapshot recheck do
not fold case.

---

## 3. Required fix, when authorized

Mirror the owned B3 design: resolve the policy **once** and pass it in, rather
than deriving it six times from `os.name`.

1. Add a case-policy parameter threaded from `_bound_worktree_state` into
   `_match_git_pattern`, `_git_path_is_ignored`, and
   `_untracked_repository_files`.
2. Resolve it with a contained probe inside `app/`, matching
   `detect_case_policy` — exclusive create, random opaque name, cleanup in
   `finally`, `INCONCLUSIVE` on any uncertainty.
3. On `INCONCLUSIVE`, return `_inconclusive_worktree_state()`. The tool already
   has exactly the right vocabulary for "I could not tell".
4. **Detect index case collisions explicitly.** Under
   `CASE_INSENSITIVE`, two stage-zero entries whose paths casefold equal must
   raise `git-index-case-collision` and fail closed. This is the false-clean
   hole and no policy value makes it safe to ignore.
5. Preserve Windows behaviour exactly — `os.name == "nt"` volumes resolve to
   `CASE_INSENSITIVE`, giving today's folding.

The probe cannot write inside `app/` casually: preflight treats an untracked
file as dirtying. It must run **before** the untracked scan and its own artifact
must never be observed by that scan, or the probe must use a directory the scan
excludes (`.git/` is already skipped). Placing the probe under `.git/` is the
simplest correct answer and is worth deciding deliberately, because getting it
wrong makes the tool report itself dirty.

---

## 4. Future test matrix

| # | Scenario | Policy | Expected |
|---|---|---|---|
| 1 | clean repo | `CASE_SENSITIVE` | `CLEAN` |
| 2 | clean repo | `CASE_INSENSITIVE` | `CLEAN` |
| 3 | clean repo | `INCONCLUSIVE` | `INCONCLUSIVE_GIT_STATE` |
| 4 | `.gitignore` `*.PNG` vs file `photo.png` | `CASE_SENSITIVE` | untracked → ineligible |
| 5 | same | `CASE_INSENSITIVE` | ignored → `CLEAN` |
| 6 | tracked `a/b.txt`, walked `A/B.txt` | `CASE_INSENSITIVE` | recognised as tracked |
| 7 | same | `CASE_SENSITIVE` | reported untracked |
| 8 | index holds `README.md` **and** `readme.md` | `CASE_INSENSITIVE` | **`git-index-case-collision`, fail closed** |
| 9 | same | `CASE_SENSITIVE` | both verified normally |
| 10 | probe create failure | `AUTO_DETECT` | `INCONCLUSIVE` |
| 11 | probe cleanup failure | `AUTO_DETECT` | verdict returned, no raise |
| 12 | probe artifact never counted untracked | `AUTO_DETECT` | scan unaffected |
| 13 | Windows regression | `AUTO_DETECT` | identical to today, all 179 green |
| 14 | ignore-rule digest stability | both | `ignore_rules_sha256` unchanged by policy |
| 15 | `.gitattributes` ambiguity | `CASE_INSENSITIVE` | still fails closed |

Rows 8 and 12 are the ones that would most likely be missed.

---

## 5. Blast radius

`resolve_authorization.py` is 4,100+ lines and holds the eligibility gate for
every ResolvePlan. A change here can make a clean repository ineligible — which
already happened once with `.gitattributes` — or, worse, make a dirty one look
clean.

It also invalidates the current plan binding: any commit moves HEAD and forces
plan regeneration.

Recommended sequencing: do it as its own bounded milestone, with the 15-row
matrix written **before** the implementation, exactly as the `.gitattributes`
fix was done. Not bundled with adapter work.

---

## 6. Current status

```text
sites identified:     6
changed:              0
Windows behaviour:    unchanged, 179 preflight tests green
macOS behaviour:      UNVERIFIED — never executed
false-clean risk:     index case collision under a case-insensitive volume
false-inconclusive:   .gitignore case mismatch under an unfolded comparison
```
