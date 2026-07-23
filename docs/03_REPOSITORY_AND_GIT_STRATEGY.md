# Repository and Git Strategy

## Remotes

```text
origin    Your Forge Studio distribution repository
upstream  Forge Neo repository
```

Verify rather than assume the upstream branch name. Record it in `UPSTREAM_BASE`.

## Long-lived branches

```text
main                         stable/releasable
develop                      current integration work
integration/upstream-YYYYMM  upstream merge rehearsal
release/X.Y                  release stabilization
```

Feature branches are short-lived:

```text
feature/...
fix/...
perf/...
docs/...
```

## History policy

- Merge upstream into a dedicated integration branch.
- Do not continually rebase published release history.
- Feature branches may be rebased before merge.
- Enable `git rerere` for recurring conflict resolution.
- Use signed tags where practical.
- Record every release's upstream base.

## Commit policy

One logical behavior per commit.

Suggested prefixes:

```text
core:
studio:
perf:
test:
docs:
build:
release:
upstream:
```

A performance commit must not also contain an unrelated frontend redesign.

## Patch inventory

Maintain `docs/14_PATCH_INVENTORY.md`.

Every direct modification under Neo-owned `modules/`, `backend/`, `modules_forge/`, launch, or bootstrap code must be listed.

## Upstream merge procedure

1. Create `integration/upstream-YYYYMMDD`.
2. Fetch upstream.
3. Confirm clean working tree.
4. Record prior base.
5. Merge upstream branch without immediately resolving by deleting Studio behavior.
6. Classify conflicts:
   - textual only;
   - interface changed;
   - behavior changed;
   - feature now upstream;
   - obsolete Studio patch.
7. Run static/import checks.
8. Launch stock UI.
9. Launch Studio UI.
10. Run smoke matrix.
11. Run same-session performance control.
12. Update patch inventory.
13. Update `UPSTREAM_BASE`.
14. Produce an upstream sync report.
15. Merge into `develop`.
16. Stabilize before `main`.

## Never do this

- Copy a new upstream ZIP over the repository.
- Resolve conflicts without reading upstream behavior.
- compare performance before and after a server restart as proof;
- squash away the identity of upstream merges;
- let an agent merge hundreds of files without a diff summary;
- allow generated assets or model files into Git.

## Repository hygiene

Ignore or exclude:

```text
models/
outputs/
venv/
cache/
embeddings/
repositories/
*.safetensors
*.ckpt
*.pt
*.pth
*.onnx
```

Do not ignore release documentation, migrations, dependency lock snapshots, or source-offer material.
