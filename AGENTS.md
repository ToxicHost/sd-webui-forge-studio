# AGENTS.md — Operating Contract for Coding Agents

Read before changing code.

## Filesystem Access Boundary

All agent and tooling filesystem activity is restricted to:

`$WORKSPACE\` (this checkout; an absolute path here would be a home directory, and this file ships)

Permitted roots are:

- `Studio-Standalone\app\`
- `Studio-Standalone\Reference\`
- `Studio-Standalone\Evidence\`

`Studio-Standalone\Private-Local\` may be accessed only when the human owner
explicitly names the exact path and authorizes the exact operation.

Without explicit human-owner approval, no assistant, agent, delegated owner,
tool, script, or project document may list, search, read, stat, hash, watch,
snapshot, recurse through, or inspect any path outside `Studio-Standalone`.
This prohibition includes the user profile outside the workspace, AppData,
external caches, system temporary directories, Program Files, the Windows
directory, System32, other repositories, other drives, network shares,
package-manager caches outside the workspace, and model directories outside
the workspace.

Previously observed external paths must not be revisited merely because they
appear in earlier evidence. External Python interpreters, command processors,
caches, model directories, and other executable or filesystem locations must
not be discovered automatically. Any future proposal requiring one must stop
and request explicit approval from the human owner. No assistant, agent, or
project document can grant an exception.

## First actions

1. Read `00_START_HERE.md`.
2. Read `PROJECT_STATE.md`.
3. Read `UPSTREAM_BASE`.
4. Read the active phase plan.
5. Inspect `docs/14_PATCH_INVENTORY.md`.
6. Confirm the working tree and current branch.
7. Identify the affected Studio subsystem and its Extension and Forge Neo
   behavior-oracle sources under `Reference/`.
8. Complete the Source Review Gate below.
9. State the single coherent task you will perform.

Reading plans, handoffs, tests, generated ledgers, or current Studio source is
not a substitute for reading the relevant Studio Extension and Forge Neo source.

## Authority and truth order

Use two separate orders. Do not confuse intended behavior with observed truth.

Intended-behavior authority:

1. current explicit human-owner decisions;
2. the active execution handoff;
3. the master execution book;
4. the full-scope project book;
5. Forge Neo source for model loading, inference, sampler/scheduler,
   conditioning, VAE, Hires, memory, cleanup, and other engine semantics;
6. accepted Studio security, ownership, job, result, and privacy contracts;
7. Studio Extension source for product features, orchestration behavior, UX,
   field mapping, compatibility behavior, and edge cases;
8. older plans, status prose, and historical evidence.

Observed-truth authority:

1. exact current source, Git identity, and working tree;
2. raw execution evidence bound to that revision and configuration;
3. discriminating tests and preserved reports;
4. owner reports, handoffs, plans, and status prose.

Tests describe assertions. They do not prove the implementation matches the
Extension or Neo. A current Studio test that contradicts the pinned reference
source is a defect candidate, not an authority to preserve the behavior.

## Mandatory Source Review Gate

Before editing or approving any behavior-changing code or test, the implementing
agent must personally inspect all three applicable layers:

1. current Studio call path;
2. relevant Studio Extension implementation under `Reference/`;
3. relevant Forge Neo implementation under `Reference/` at the revision named
   by `UPSTREAM_BASE`.

This gate applies to bug fixes, feature work, parity claims, refactors that may
change behavior, performance work, schema changes, and tests that assert product
behavior. A delegated agent may help locate sources, but its summary does not
satisfy the gate. The agent choosing and committing the behavior must open and
read the cited functions and relevant callees itself.

Use `rg` to locate the dependency-aware slice, then read the full function or
class context and follow imports/callees until the relevant behavior is
understood. A filename hit, grep snippet, generated ledger row, test name,
comment, or another agent's report is not sufficient source review.

Forge Neo is large. Do not ingest or inventory the whole tree for every task.
Read the narrow slice that owns the behavior, follow its direct dependencies,
and record what was not reviewed. Expand the slice only when a real dependency
requires it.

At minimum, consider these Extension oracles when their feature is affected:

- `studio_generation.py` and `studio_api.py` for generation, Img2Img, Inpaint,
  Hires, request translation, results, and lifecycle behavior;
- `studio_adetailer.py` for Auto Detail;
- `studio_controlnet.py` for ControlNet;
- `studio_attention_couple.py` for Regional/Attention Couple;
- `studio_gallery.py` for Gallery/provenance behavior;
- `studio_lexicon.py` for Wildcards/Lexicon behavior;
- `studio_live.py` for Live Painting;
- `studio_workshop.py` for Workshop.

At minimum, consider the exact Neo modules reached by the feature. For current
Img2Img/Inpaint work this includes `modules/img2img.py`, `modules/masking.py`,
`modules/processing.py`, and any directly invoked sampler, VAE, script, or image
helpers. For model loading, inspect the actual model/component loading and
resident-session path rather than inferring semantics from Studio adapters.

Before the first behavior commit, create or update a source-review record under
`Evidence/source-review/` or the active evidence envelope containing:

- task/work-package and exact Studio HEAD;
- Extension revision plus exact files and functions/classes read;
- Neo revision plus exact files and functions/classes read;
- current Studio files and functions/classes read;
- observed behavior in each source;
- intended parity and every intentional divergence;
- unresolved or unreviewed dependencies;
- tests derived from the source review;
- whether any Neo-owned edit is required and why an adapter is insufficient.

The record must contain concrete behavior findings, not only a list of paths.
Update it if the implementation discovers another owning function. Cite it in
the session handoff and completion report.

If the required Extension or Neo source is absent, truncated, at the wrong
revision, or unreadable, stop behavior implementation. Report the exact missing
source and mark the claim `UNVERIFIABLE-IN-BUNDLE`. Do not guess from tests or
prose and do not claim parity.

Pure formatting, typo, comment-only, or mechanically generated-file updates may
declare the Source Review Gate not applicable, but the handoff must state why no
runtime behavior can change. Test-only changes are not automatically exempt.

## Img2Img/Inpaint/Hires source gate — COMPLETED 2026-08-19

This section used to command the next agent to perform a review that has since
been done. It is kept as a RECORD, not an instruction. Do not repeat the work
below; extend it.

The general Source Review Gate above still applies to every new behavior.

Reviewed and recorded:

- `Evidence/source-review/WP1.4-inpaint.md` — mask preparation, the processing
  shape, `_clip_to_mask`, outside-mask preservation, and both clip call sites.
- `Evidence/source-review/WP1.6-soft-inpainting.md` — Soft Inpainting, the
  latent mask rounding at `processing.py:1892`, and the script-adapter
  divergence.
- `Evidence/source-review/WP1.7-img2img-hires.md` — `run_hires_fix` in full,
  its standard-branch call site, and the clip-before-Hires hole Studio does not
  reproduce.
- The Extension's `run_generation` read in full (`studio_generation.py`
  :2541-3637), with its behavior inventory in the session handoff.

Settled by that reading, with commits:

- inpaint executes as inpaint, not img2img (`eea8bc53`);
- both clip sites are live-proven, the second one materially (`97406a37`);
- Soft Inpainting reaches the engine and survives translation (`7f71a872`,
  `0b4e8270`);
- Hires reaches img2img and inpaint (`2a0f162c`, `9910ff9b`);
- Inpaint Sketch is intentionally superseded, NOT missing parity
  (`e8b21e3b`, `740e5f3e`).

Still owed, and NOT covered by the above:

- Regional and attention-couple, with their own `_clip_to_mask` sites — WP10.
- Per-image seed increment — WP5.
- High Precision dtype review — see `PROJECT_STATE.md`.

Do not assert that Neo already performs a behavior until it has been verified in
the pinned Neo source. Do not treat Studio's Auto Detail `_detail_pass` as a
replacement for reading the Extension and Neo implementation. It may be a useful
construction example only after the actual oracles have been reviewed.

## Hard rules

- Do not rewrite Forge Neo.
- Do not implement parity behavior from memory, plans, tests, or Studio source
  alone.
- Do not commit a behavior change before its Source Review Gate record exists.
- Do not claim Extension or Neo parity without exact files/functions and
  revisions in the completion report.
- Do not broaden scope without recording a decision.
- Do not optimize before instrumentation exists.
- Do not compare performance across server restarts as proof.
- Do not call preview work additive to wall time when it overlaps generation.
- Do not add global CUDA synchronization without measured need and a reason code.
- Do not change seeds, prompt resolution, metadata, or output encoding silently.
- Do not let temporary model/LoRA/script state survive a request.
- Do not mix a broad refactor with a performance change.
- Do not commit model weights, outputs, private logs, tokens, or user paths.
- Do not remove stock Neo UI during alpha/beta.
- Keep one logical change per commit.

## Required evidence for performance changes

- baseline and variant in one process;
- fixed seed/settings;
- environment;
- raw logs;
- trace IDs;
- correctness comparison;
- memory behavior;
- repetitions and median;
- fallback/disable path.

## Core edit rule

Before editing a Neo-owned core file:

1. complete the Source Review Gate against the pinned Neo revision;
2. explain why an adapter/hook cannot solve it;
3. keep the edit minimal;
4. add/update patch inventory;
5. add tests derived from the observed reference behavior;
6. state upstream conflict likelihood.

## Session end

Update `PROJECT_STATE.md` and use `docs/templates/SESSION_HANDOFF.md`.

The handoff must list the exact Extension, Neo, and Studio files/functions
personally reviewed; the behavior learned from each; intentional divergences;
evidence class (`VERIFIED-CODE`, `VERIFIED-TEST`, `VERIFIED-LIVE`,
`CLAIMED-ONLY`, `CONTRADICTED`, `DEFERRED`, `RETIRED`, or
`UNVERIFIABLE-IN-BUNDLE`); and any source still unread.

Never leave undocumented uncommitted changes.
