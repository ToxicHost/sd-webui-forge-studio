# Friends Alpha 0.1 — acceptance matrix

Frozen 2026-08-19 at `9910ff9b`. Updated through AR4.4.
Canonical at AR4.9: 4117 OK, exit 0, 4 skipped.
Release candidate: `forge-studio-0.0.0-internal-alpha.zip`, built from
`00913450`, sha256 `16d05bbb...`, 19/19 live verdicts from the EXTRACTED
package on the GPU.
AR1 live evidence: `Evidence/ar1-full-chain/`, 2026-08-19.
AR4.4 live evidence: `Evidence/ar44-live/LIVE-JOURNEYS.md`, 2026-08-19/20.
AR3.5 live evidence: `Evidence/ar35-live/LIVE-MEASUREMENT.md`, 2026-08-19.
AR4.5 live evidence: `Evidence/ar45-live/LIVE-PROOF.md`, 2026-08-19.
AR7 package evidence: `Evidence/ar7-live/LIVE-PROOF.md`, 2026-08-20.
AR9 release candidate: `Evidence/ar9-live/RELEASE-CANDIDATE.md`, 2026-08-20.

Scope and platform boundary: `PROJECT_STATE.md`, "Friends Alpha 0.1 scope".
This is an invite-only Windows/NVIDIA alpha, not Studio 1.0 parity.

## How to read the evidence class

The class is the STRONGEST evidence that exists, and nothing here may claim a
class it has not earned. This project has repeatedly found features where both
endpoints worked and the seam between them silently dropped a field, so
"CODE + TEST" is explicitly not a claim that the feature runs.

| Class | Means |
|---|---|
| `LIVE` | executed on the real GPU through the real HTTP path, with a measured assertion |
| `BROWSER` | driven from the actual page in an executing browser |
| `SEAM` | a test crosses the real join (contract → translation → port), not just the two sides |
| `TEST` | canonical coverage, endpoints only |
| `CODE` | implemented and read, no execution evidence |
| `OWNER` | the owner has used it and confirmed it works. Weaker than `LIVE` because it is not measured or repeatable on demand, and STRONGER than `NONE` because a person watched it happen |
| `NONE` | not evidenced |

`OWNER` exists because the first version of this matrix had no way to record
it, so features the owner uses daily were being filed as "never tested" -- which
is false, and which mis-ranks the remaining work. A row may carry `OWNER` only
when the owner has actually said so; it is not a class to infer from the code
looking finished.

## Matrix

| Feature | Status | Evidence | Blocking defect | Required final journey | Disposition |
|---|---|---|---|---|---|
| Launch with no resident model | works | `LIVE` cold launches all session | — | AR4.1 cold launch in browser | ship |
| Checkpoint / TE / VAE selection | works | `LIVE` every harness selects all three | — | AR3.2 select → generate → switch | ship |
| Auto load / reuse / switch at Generate | works | `LIVE` loads=1 reuses=2 switches=0 | a deliberate component SWITCH not run | AR3.2 | ship after AR3.2 |
| Visible job queue | works | `LIVE` 12/12 incl. a reorder control | — | AR9 | ship |
| Progress — whole job span | works | `LIVE` + `BROWSER` base→Hires→Auto Detail→publish, 5 Hz sampling | longest silence 3.2 s, was 41 s | AR3.5 | ship |
| Progress — stage on `/api/jobs/<id>` and WS | absent | `LIVE` measured: `stage` only on `/api/queue` | page unaffected; polling clients get the raw backend label | owner decision | not an alpha blocker |
| Send/Open Result → Canvas | works | `LIVE` real 2048×1536 result placed as a layer, identity preserved | — | AR4.4 | ship |
| `start_studio.py` stdout relay | works | `TEST` real subprocess + real cp1252 stream; mutation-proven against the original | — | AR7.1 | ship |
| Server survives its stdout closing | untested | `NONE` | with the relay fixed the descriptor no longer dies under the owner | AR7 | not an alpha blocker |
| Launcher files under version control | **no** | `CODE` git toplevel is `app/`; `start_studio.py` and `Start-Studio.bat` sit above it | included in bundles by path, but no history, no diff review, no revert | owner decision | not an alpha blocker |
| Cancellation | works | `LIVE` queued/running/Hires + recovery each time | not exercised during Auto Detail | AR9 | ship |
| Failure + cleanup | works | `LIVE` admission + model-load, recovery each time | generation/Hires/save failures not induced | AR3.4 partial | ship |
| txt2img | works | `LIVE` | — | AR9 | ship |
| Canvas img2img | works | `LIVE` | — | AR4.4 browser journey | ship |
| Canvas inpaint | works | `LIVE`, preservation exact | — | AR4.4 | ship |
| Inpaint controls (Area/Blur/Fill/Padding) | works | `LIVE` blur moves 17.82% of pixels, Area 5.31%, padding 4.59% | were hidden as dead since 9533093d; live since WP1.4/1.6 | AR4.7 | ship |
| Inpaint fill — Latent Noise | **broken** | `LIVE` GENERATION_FAILED / IndexError | only mode reading `p.all_seeds`, which Studio never populates; option withheld | follow-up | not shipped |
| Tab notification on completion | works | `CODE` bound to result delivery | `_notifyTab` existed uncalled in Studio and the Extension | AR4.7 | ship |
| Finished result replaces the live preview | works | `BROWSER` none → latent preview → server result | the swap sat below the lifecycle return | AR4.8 | ship |
| Result drag-out (name + type) | works | `CODE` DownloadURL + uri-list + moz-url, type from the handle | still a copy; a page cannot drag a file on disk | AR4.8 | ship |
| Output format (PNG/JPEG/WebP) | works | `LIVE` .jpg/.webp on disk, magic bytes and media type agree | was dead at all four layers | AR4.9 | ship |
| Soft Inpainting | works | `LIVE` + `SEAM` | — | AR4.4 | ship |
| Hires — txt2img | works | `LIVE` (P0.7, 4x/2048) | — | AR9 | ship |
| Hires — img2img | works | `LIVE` 768x768, real denoise | — | AR1.3 override | ship |
| Hires — inpaint | works | `LIVE` + preservation exact | — | AR1.1 | ship |
| Hires overrides (sampler) | works | `LIVE` mean&#124;d&#124;=2.01 | scheduler/prompt still unvaried | AR9 | ship |
| Hires named Neo upscaler | works | `LIVE` (registry path ran) | the entry chosen WAS 'Lanczos', so pixels do not discriminate it from the fallback | re-run AR1 with an ESRGAN-class entry | ship, weakly evidenced |
| Auto Detail after Hires | works | `LIVE` mean&#124;d&#124;=2.90 vs Hires-only | — | AR9 | ship |
| Auto Detail — one slot | works | `LIVE` candidates=1 regions=1 | — | AR1.2 | ship |
| Auto Detail — three slots | works | `LIVE` 3 explicit outcomes | detailing slot was 3, not the one designed | AR9 | ship |
| Auto Detail per-slot LoRA | unknown | `NONE` | visibility in UI unconfirmed | AR1.2 | disable if unproven |
| Aspect randomisation | works | `SEAM`, per-image rolls | no browser journey | AR4.5 | ship after AR4.5 |
| Dynamic Prompts / Wildcards | works | `TEST` | restart persistence unproven | AR4.2 (GA-BROWSER-01) | ship after AR4.2 |
| Canvas painting + mask workflows | works | `LIVE` via API | not driven from the page | AR4.4 | ship after AR4.4 |
| Send / Open Result in Canvas | works | `BROWSER` — opens, resizes, adds a layer, undoable, cannot destroy dirty work | not driven end-to-end from a completed job (handles are process-local) | AR9 | ship |
| Output saving | works | `LIVE` every result + `TEST` anchoring | AR6.2 overwrite/format checks not swept | AR6.2 | ship after AR6.2 |
| Save Defaults | works | `OWNER` + `BROWSER` survives restart + new origin | — | AR9 | ship |
| Remember Last Session — settings | works | `BROWSER` cross-port A→B→C + `TEST` | no second OS browser profile | AR9 | ship |
| Canvas crash recovery — layered documents | works | `BROWSER` F5 + cross-port restart, every field byte-identical | selection is not in Studio's document model at all; undo history deliberately not persisted | AR4.4 | ship |
| Canvas document — selection across restart | **absent** | `CODE` (`_saveDoc` omits it, `_loadDoc` clears it) | already lost on an ordinary tab switch, so this is the document format, not recovery | owner decision | not a recovery blocker |
| Canvas tab switch — pixel fidelity | works | `BROWSER` 20000 → 20000, ten round trips stable | inherited upstream defect; deliberate divergence from the Extension | AR4.5 | ship |
| Canvas document — saved zoom across a switch | **absent** | `CODE` the handler calls `zoomFit` on the last field write | pre-existing; preserved deliberately rather than changed beside AR4.5 | owner decision | not an alpha blocker |
| Result / gallery retrieval | works | `LIVE` by opaque handle | gallery UI unproven | AR4.4 | ship after AR4.4 |
| High Precision control | **removed** | `CODE` + `TEST` zero Python readers anywhere, Neo included | a porting gap; the Extension implements it at `studio_api.py:1440` | AR5 | ship without it |
| Develop float32 sidecar reader | present, inert | `CODE` complete reader, independent of the removed toggle | waits on a producer; kept for a future port | post-alpha | ship (inert) |
| Document identity + revision | works | `BROWSER` survives F5 and a port change; per-TAB since AR4.4 | — | AR4.4 | ship |
| Source/mask consistency | works | `SEAM` | handle-path refusals N/A: generate takes inline bytes only | AR4.4 | ship |
| Asset retain/release lifecycle | deferred | `TEST` | nothing consumes handles; no lifetime to wire until WP5 | WP5 | not an alpha blocker |
| Asset hash enforcement | works | `SEAM` | — | AR9 | ship |
| Fresh package / launcher | works | `LIVE` built, extracted clean, started, served a page, saw its catalogue | no tester has extracted it on their own machine | AR7 | ship |
| Distributable privacy (built package) | works | `LIVE` every extracted file scanned; build fails closed | — | AR7 | ship |
| Archive reproducibility | works | `TEST` two builds byte-identical | — | AR7 | ship |
| Tester guide | works | `TEST` every factual claim asserted against the product | no tester has read it yet | AR8 | ship |
| Support bundle | works | `LIVE` built; 0 owner-name occurrences, 26 redactions applied | — | AR8 | ship |
| Support bundle excludes artwork | works | `TEST` recovery, gallery and results proven absent from a built bundle | — | AR8 | ship |

| Distributable privacy (repo) | works | `TEST` scans every tracked file; one rule shared with the builder | — | AR7 | ship |


## Rows that may be OWNER-observed and are currently filed as NONE

The owner uses Studio daily, so some of these are probably not "never tested"
at all -- they are unmeasured, which is a different and lesser problem. Ask
before the release gate rather than inferring:

- Send / Open Result in Canvas
- Result / gallery retrieval through the UI
- Canvas painting and mask workflows driven from the page

Recording an `OWNER` class where it applies changes what is left to do. It does
not change what may be CLAIMED: no `OWNER` row may be described as proven, and
none of them substitutes for the AR9 journey the matrix already requires.

## Known issues the alpha MAY ship with

Recorded so they are not mistaken for blockers: visual polish defects;
performance variability; limited detector coverage; untested combinations of
sampler/scale/mask/format; hidden deferred modules; Windows+NVIDIA-only live
evidence; no Docker
proof; no macOS/Linux live proof; no Regional/attention-couple; no Hires
checkpoint switching.

## Deferred — NOT alpha debt

These are recorded elsewhere and must not be re-filed as parity gaps:

- Regional and attention-couple, with their `_clip_to_mask` sites — WP10.
- Per-image seed increment — WP5.
- Durable queue recovery across process termination — post-alpha.
- Hires checkpoint swap — needs intra-job model switching; owner decision.

## Superseded — must never return as parity debt

- **Inpaint Sketch.** Canvas painting and Canvas mask workflows replace it.
  Decision `e8b21e3b`, removal `740e5f3e`, guarded by `SupersededScopeTests`.
- Gradio-only presentation behavior.
- Legacy UI workflows replaced by Canvas.

## AR9 — the release candidate, and the one gap left

`forge-studio-0.0.0-internal-alpha.zip`, built from `00913450`, was extracted
into a directory with no repository around it and exercised end to end on the
GPU: 19 verdicts, all pass. It starts, resolves its catalogues, generates a
real 1536x1536 image through base -> Hires -> Auto Detail, reports every stage
WHILE running, decodes its result through an opaque handle to the exact byte
count, reuses the resident model on a second job, and produces a clean support
bundle -- all from the packaged copy.

Three defects surfaced during that run and all three were in the HARNESS, not
the product: a guessed GET route the product is right not to have, a comparison
of `None` to `None` that passed without measuring anything, and JSON-decoding a
route that answers in raw bytes. Recorded in
`Evidence/ar9-live/RELEASE-CANDIDATE.md` rather than quietly fixed, because a
vacuous pass is the failure mode this matrix exists to prevent.

**The one gap that remains cannot be closed from here:** no one but the author
has run this build. Extraction and execution happened on the machine that built
it. Everything else on this page is measured; that is not, and only a tester
can change it.
