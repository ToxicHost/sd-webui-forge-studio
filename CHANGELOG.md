# Changelog

Milestone-level history. Each entry names the commit that closed it and the
evidence class of the claim, using the project's vocabulary:
`VERIFIED-EVIDENCE` (a preserved artifact records a real run) ·
`VERIFIED-CODE` (the mechanism exists and is tested) ·
`CLAIMED-ONLY` · `CONTRADICTED`.

**A milestone label is not an acceptance.** Several below closed narrower than
their name suggests, and that is recorded rather than smoothed.

---

## Unreleased

### 2026-08-18 — WP0.2 bundle, WP0.5 Docker, and the ledger's second half

`VERIFIED-TEST` throughout; no live, browser, GPU or container evidence.

**Bundle** (`25ad73ce`). The collector moves into the repository. Its manifest
advertised 923 included paths for 837 real files because `take()` counted every
copy and the whole-tree pass re-takes what the import walk reached — now
854/854, enforced rather than printed. More seriously, `studio-config.json` was
shipped RAW, carrying the owner's home directory, private model roots and model
filenames in an artefact built to be handed to a reviewer; it now ships
redacted with the original's digest. The new seed gate found a real omission on
its first run: `modules/processing_scripts.py` is a DIRECTORY, so the seed
resolved to nothing and six files were absent from an area the bundle named as
under review.

**Docker** (`14293fdf`). The artefacts lived outside the git root, so a clean
clone could not build the image; `cb9c76a6` had added only the test. Now
tracked, context at the repository root, with `deploy/` chosen over `docker/`
because the latter is upstream Neo's image for a different product. The compose
file mounted real private directories, so the tracked artefact is an example
with placeholders. The test read all four files in class bodies at import time —
a missing one raised during discovery and took all 37 tests with it, silently.

**Ledger statuses and controls** (`9c1dfb3a`, `219ab07a`). `exact` is gone:
routes now report service-backed / capability-gated / prefix-service /
scan-uncertain, and none of those claims a feature works. The control half adds
146 Generate-tab controls; eight are visible, clickable and bound to nothing,
each landing on a feature already reported absent (AD slot LoRAs, ControlNet,
Develop layers). Undisposed routes and undisposed visible controls both fail
the build.

### 2026-08-18 — the patch inventory was wrong, and nothing could tell

`VERIFIED-TEST`. `docs/14_PATCH_INVENTORY.md` said 15 Neo-owned files,
+570 -59. The truth is 21 files, +868 -74, and it had been wrong since
2026-08-14. `UPSTREAM_BASE` still said `neo...HEAD` was 0/249; it is 0/324.

Six files were uncatalogued. Four were the same deferred-import work and fold
into PATCH-001. The other two are **PATCH-003**, a third patch family the
document's own summary denied could exist: `modules/esrgan_model.py` and
`modules/upscaler_utils.py` change upscale pixel behaviour, and Neo-owned code
now imports `forge_headless` — the only patch that does. The load-bearing
claim survives (`backend/`, `ldm_patched/`, `extensions-builtin/` remain at
exactly zero changed files), but "Studio patches only the import graph" had
quietly stopped being true.

Root cause: nothing in the repository referenced either document.
`test_patch_inventory.py` now recomputes every figure from git, and
`test_every_changed_neo_file_is_named_in_the_inventory` fails on any Neo-owned
edit that is not catalogued — which would have caught this on the first
upscale commit.

### 2026-08-18 — WP0.3, the route ledger

`VERIFIED-TEST`. `scripts/parity_ledger.py` generates
`docs/15_PARITY_LEDGER.{md,json}`, and the canonical suite regenerates and
compares, so a new frontend route with no disposition now fails CI.

150 frontend route references: 77 answered, 34 behind a prefix service, 38
missing with an owning packet, 1 retired by design. The bundle's scan reported
140/73/18/49; both numbers were wrong. It matched literals against two server
files, so it could not see `presentation.py`'s manual dispatch or the prefix
routers, and it was a text grep, so it silently skipped a file whose cache keys
are NUL-separated. The servers are now PARSED, not grepped, and the frontend is
read as bytes — coverage is reported rather than assumed.

Statuses are deliberately narrow: `exact` means a handler answers, NOT that the
feature works. Separating capability-gated from working needs the response
shapes and is the next increment.

### 2026-08-17 — WP0.4, the Dynamic Prompts write

`VERIFIED-TEST`. A duplicate `POST /studio/dynamic_prompts/config` branch
shadowed the only caller of `set_enabled`, so the toggle could never be turned
off — the write had no reachable handler. Wildcards themselves always worked;
the missing capability was the OFF switch. Canonical 3657 OK exit 0. Restoring
the stub fails two tests by name.

The capability-as-default rule the session bridge flagged was examined and
deliberately kept: an AND term was implemented and reverted, because the
toggle's `on` class gates Browse and refusing a folderless owner would remove
the control that would have given them a folder.

`CLAIMED-ONLY` for the browser half — WP0.4's executing-browser
toggle-and-restart journey has not been run.

### 2026-08-11 — R0 baseline recovery, and R1 truthfulness

R0 **CLOSED** (`7177fefe`) — `VERIFIED-EVIDENCE`.
Five uncommitted audit edits reviewed independently; one was harmful and
corrected before landing. FULL live regression scope 40/40 on the owner's GPU
across two cold processes; two browser journeys 13/13; every retained fix has a
test that fails against the reverted implementation.

R1 **OPEN**, seven slices accepted. Fourteen owner-visible lies removed, six of
them by making an entry point honest rather than repairing the control.

- `2e8e2b30` a failed upscaler scan is retried only while retrying can still work
- `47085707` refusals stop arriving as HTTP 200 with a success-shaped body
- `c4f0fa5a` the page stops reporting success it was never told about
- `7177fefe` tests that fail when reverted, and a browser that watches the page
- `1a121992` three controls stop reporting work that never happened
- `727b428a` tabs for services that do not exist stop advertising themselves
- `24130518` Live stops turning itself on over a refusal, and stops locking Generate
- `a17bfc73` ControlNet and the Hires checkpoint override stop pretending to be wired
- `fe7f5102` refresh actually rescans, instead of re-reading the same cache
- `bd2341ad` GPU Weights and Auto-unload stop persisting settings nothing honours
- `68ac47ab` variation seed reaches the engine — `VERIFIED-EVIDENCE`, live 3/3
- `5cbc2f2e` the Neo patch inventory, reconstructed

Not done, deliberately: the response-envelope architecture was **withdrawn**
after the case justifying it turned out to be unreachable code.

### 2026-08-10 — Auto Detail

`629c6210` Auto Detail runs — `CLAIMED-ONLY` for the live half. Three-slot
plumbing, detector catalogue and offline boundary exist and are tested; only
slot 1 has ever executed and the raw record was not preserved. The full
six-case matrix is absent.

### 2026-08-09 — P0.4 through P0.7

- `7abe5d83` P0.4 model roots and filesystem browser — **core** `VERIFIED-CODE`.
  The broad "every Browse/Open works" claim is `CONTRADICTED`: Dynamic Prompts,
  Gallery, save, watermark and LoRA surfaces remained dead.
- `cb9c76a6` P0.5 Docker — **`CONTRADICTED` and REOPENED.** The commit added only
  a test, and the artifacts that test reads live outside the Git root, so a clean
  clone lacks them. No image was built and no container was run.
- `f70ca5ca` P0.6 canonical request, live 10/10 — `VERIFIED-EVIDENCE` for the
  **base txt2img slice**. Not the full generation contract: variation (added
  2026-08-11), img2img, inpaint, batch, LoRA, ControlNet and output settings
  were all absent.
- `cdfcc436`, `83b20c7b` P0.7 Hires carried end to end, proven live at 4x —
  `VERIFIED-EVIDENCE` for txt2img Hires. Does not extend to img2img Hires or to
  temporary checkpoint override and restoration.

### 2026-08-08 — P0.0 through P0.3, the profile retirement

- `6f765167` P0.0 authority reset
- `c8e8ad26` P0.1 ModelSelection at the loader boundary
- `f6a9b099` P0.2 Generate owns model readiness — `VERIFIED-EVIDENCE`
- `e3bf3547` P0.3 the Profile/Load workflow removed, live acceptance

After this the product contract is: choose checkpoint / text encoder / VAE, press
Generate, and Studio loads, reuses or switches as needed. No owner-facing
profile, no mandatory Load.

### 2026-07-24 — canonical frontend integration

`882c90f3` the Forge Studio 4.17 frontend integrated as the standalone shell.
`VERIFIED-CODE` for source identity. **Not feature parity** — the frontend is
the shipping Extension's, and much of what it calls has no backend here. That
distinction is the single largest source of misreading in this project.

### 2026-07-23 — baseline

Forked from `Haoming02/sd-webui-forge-classic@neo` at `97ff3a40`, tagged
`neo-baseline-2026-07-23`. `backend/`, `ldm_patched/` and `extensions-builtin/`
remain untouched; see `docs/14_PATCH_INVENTORY.md` for the two patch families
that do modify Neo-owned files.

---

## Not yet true

Carried here so a reader does not infer them from the entries above:

- no packaging, installer or clean-install claim
- Docker unproven; P0.5 reopened
- nothing exercised on macOS or Linux
- one Windows/RTX host, one tested model triplet
- durable preferences absent — settings reset on every launch
- Gallery, Workshop, Wildcards, Live, ControlNet, img2img and LoRA have no
  backing service; their entry points are capability-gated rather than implemented
