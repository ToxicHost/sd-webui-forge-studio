# Forge Neo Baseline

## Verified provenance

- Distribution repository:
  `https://github.com/ToxicHost/sd-webui-forge-studio`
- Upstream repository:
  `https://github.com/Haoming02/sd-webui-forge-classic.git`
- Upstream branch: `neo`
- Baseline commit: `97ff3a4024be2f0d5316f16e868e5ef822768872`
- Commit date: `2026-07-23T14:06:00+08:00`
- Commit subject: `speed`
- Baseline tag: `neo-baseline-2026-07-23`
- Current work branch: `docs/project-bootstrap`
- License: GNU Affero General Public License v3.0

## Repository checks

The Phase 0 pre-edit inspection recorded:

```text
git status
On branch docs/project-bootstrap
Your branch is up to date with 'origin/docs/project-bootstrap'.
nothing to commit, working tree clean

git rev-list --left-right --count neo...upstream/neo
0    0
```

The local `neo` branch tracks `upstream/neo`. The `develop`, `main`, and
`docs/project-bootstrap` branches were also at the baseline commit when Phase 0
started.

## Baseline source points

The verified tree contains these future instrumentation points:

- `modules/sd_models.py::forge_model_reload`
- `modules/devices.py::torch_gc`
- `backend/memory_management.py::soft_empty_cache`
- `backend/memory_management.py::unload_all_models`

They are inventory entries only. Phase 0 does not modify or instrument them.

## Evidence still required

- sanitized Python, PyTorch, CUDA, driver, GPU, operating-system, and memory
  report;
- actual launch command and arguments;
- extension inventory and revisions;
- cold startup and first model load log;
- fixed-seed generation fixtures and complete behavior logs;
- baseline measurements and output metadata.
