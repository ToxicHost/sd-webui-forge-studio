# Container deployment

The tracked, canonical container definition for Studio Standalone.

```text
../Dockerfile                 the image; build context is the repository root
../.dockerignore              relative to that same root
entrypoint.py                 writes a config once, then launches Studio
docker-compose.example.yml    reference run; copy and edit the three mounts
```

Build from the repository root:

```bash
docker build -t forge-studio .
```

## Why `deploy/` and not `docker/`

`../docker/` is **upstream Forge Classic Neo's** container definition, inherited
with the fork and untouched. It builds a different product: it `git clone`s
`sd-webui-forge-classic` from GitHub at build time and runs the Gradio WebUI on
7860. Studio's image installs from this checkout, starts no Gradio, and serves
on 7865.

Two images with opposite purposes cannot share one directory name without one of
them being read as the other's. Upstream's is left exactly where upstream put
it, because deleting inherited source needs owner approval and a patch-inventory
entry, and neither is worth spending to reclaim a directory name.

## What changed in WP0.5, and what did not

Before this, `Dockerfile`, `.dockerignore`, `docker-compose.yml` and
`docker/entrypoint.py` lived one level **above** the git root. They were not
merely untracked — they were outside the repository, so a clean clone did not
contain them, `git ls-files` could not see them, and the image could not be
built from a checkout at all. The commit that added the Docker test added only
the test.

Two consequences that were not obvious:

- `test_docker_foundation.py` read all four files in **class bodies**, at import
  time. In a checkout without the siblings that raises `FileNotFoundError`
  during unittest discovery — a collection error, not a readable failure — and
  37 tests leave the run without reporting anything. It now resolves from the
  repository root and reports a missing artefact as a named failing test.
- The compose file's mounts pointed at one developer's real directories,
  including a private model library. Tracking it verbatim would have published
  that layout as the product default, so the tracked file is an **example** with
  neutral placeholders.

The owner's working copies at the workspace root are deliberately left in place.
Retiring them is a deletion, and deletions need explicit approval — but they are
now duplicates, and the tracked ones here are canonical.

## Not certified

Nothing here has been built or run. No Docker engine is installed on the
development workstation (`docker --version` → not found), which is why WP0.5 is
scoped to a static, self-contained layout and the build/run/GPU legs stay
externally pending. Section 17 of the master execution book lists all eleven
gates; every one of them remains unexecuted.

Static correctness is checked: every `COPY` source resolves inside the build
context, and `test_docker_foundation.py` enforces it from a clean checkout.
