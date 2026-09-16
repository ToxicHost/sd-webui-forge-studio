# CLAUDE.md — Fable/Claude Code Instructions

Follow `AGENTS.md`.

## Current authority — read before changing Studio

```text
Reference/CLAUDE_CODE_STUDIO_STANDALONE_MASTER_EXECUTION_BOOK.md
Reference/STUDIO_STANDALONE_FULL_SCOPE_PROJECT_BOOK.md
```

These are the master plan. They supersede earlier handoffs and evidence
documents wherever the two disagree.

They do not replace source review. Before choosing, implementing, approving, or
changing product behavior, complete the mandatory Source Review Gate in
`AGENTS.md`. Read the relevant current Studio path, Studio Extension functions,
and pinned Forge Neo functions yourself. Tests, plans, grep snippets, generated
ledgers, and delegated-agent summaries cannot satisfy that gate.

The product contract they establish:

```text
owner chooses Checkpoint / Text Encoder / VAE -> presses Generate
  -> Studio ensures the selection is resident (load / reuse / switch)
  -> base -> Hires -> Auto Detail -> one final result
```

No owner-facing model profile. No mandatory Load button. No
`PROFILE_SELECTED` product state. Forge Neo is the behavior oracle for model
loading and generation: selecting a dropdown updates desired state without
loading, and generation reconciles desired against resident — unchanged
reuses the warm session, changed reloads once.

Earlier documents that describe runtime profiles or an explicit Load step as
product requirements are describing transitional plumbing. Do not defend that
plumbing because tests or handoffs mention it. `PROJECT_STATE.md` records
which of its own claims are superseded.

## Source-oracle contract

Studio is not being designed from a blank page:

- current Studio source is the implementation being changed;
- Forge Neo source is the behavior oracle for loading, inference, model-family
  behavior, sampler/scheduler handling, conditioning, VAE, Hires, memory,
  cleanup, and low-level processing semantics;
- Studio Extension source is the feature/product oracle for generation field
  mapping, Img2Img, Inpaint, Hires, Auto Detail, ControlNet, Regional, Gallery,
  Lexicon/Wildcards, Live, Workshop, UX behavior, and edge cases.

Do not substitute one oracle for another. Current Studio code cannot prove that
Studio matches the Extension. The Extension cannot override Neo's actual engine
semantics. A current test cannot make a contradicted implementation correct.

For every behavior task, before the first edit:

1. identify the current Studio entry point and trace it to the real runtime;
2. locate the corresponding Extension behavior and read the full owning
   function(s), not only search matches;
3. locate the corresponding pinned Neo behavior and read the dependency-aware
   owning slice;
4. write the source-review record required by `AGENTS.md` with exact revisions,
   paths, functions/classes, findings, divergences, unknowns, and tests to add;
5. state the proposed behavior in terms of what the sources actually do;
6. only then edit code or behavior-asserting tests.

A read-only subagent may map candidate files or challenge the interpretation,
but Claude must personally inspect the cited source before adopting or
committing the behavior. Do not write “reviewed Neo” or “matches Extension”
without the exact revision, files, functions/classes, and concrete finding.

Neo is huge. Use targeted `rg` discovery, then read the full relevant functions
and their direct callees. Do not attempt a ceremonial whole-tree read, and do
not use Neo's size as a reason to skip it. If the relevant source cannot be
resolved at the pinned revision, stop that behavior change and report
`UNVERIFIABLE-IN-BUNDLE`.

## First Fable session

The first Fable 5 session is **read-only**.

Do not modify files. Perform an architecture audit:

- read all top-level project documents;
- inspect the diff against `UPSTREAM_BASE`;
- map Studio-owned versus Neo-owned files;
- trace one base → Hires → ADetailer generation;
- trace the corresponding Studio Extension and Forge Neo source paths, recording
  exact files, functions/classes, and revisions;
- inspect tests and patch inventory;
- identify documentation/code mismatches;
- identify high-conflict upstream edits;
- report recommendations ranked by risk.

Only begin implementation after the owner approves one scoped subsystem.

## WP1.4 Img2Img/Inpaint gate — COMPLETED 2026-08-19

This section used to block Inpaint work behind a review. That review is done and
recorded; the section is now a pointer, not a gate. The general Source Review
Gate in `AGENTS.md` still applies to every new behavior.

Records: `Evidence/source-review/WP1.4-inpaint.md`,
`WP1.6-soft-inpainting.md`, `WP1.7-img2img-hires.md`.

What the review established, so it is not re-derived:

- Txt2Img builds `StableDiffusionProcessingTxt2Img`; every image operation
  builds `StableDiffusionProcessingImg2Img`. Inpaint is distinguished by a
  REQUIRED mask, not by a flag.
- Neo owns binarisation, inversion, blur, full-resolution crop/paste and the
  overlay composite, all inside `init()`. Studio supplies settings and a mask
  and must not reimplement any of it.
- Outside-mask preservation is Studio's `_clip_to_mask`, applied after the base
  pass and again after Auto Detail. Both sites are live-proven; the second was
  proven MATERIAL by a controlled mutation (44,222 pixels).
- Hires for image operations is a Studio-run second pass, placed BEFORE the
  first clip so the Extension's clip-before-Hires hole is not reproduced.
- Auto Detail runs after Hires. The Extension's image path fires native
  ADetailer before its Hires step; Studio deliberately diverges.

`_detail_pass` may now be used as a local construction example, the review it
was gated behind having been completed.

## Ongoing behavior

- prefer coherent subsystem changes over scattered local patches;
- preserve compatibility adapters;
- do not redesign proven foundation merely because a different architecture is aesthetically cleaner;
- convert assumptions into tests or diagnostics;
- treat an absent source-review record as a hard pre-commit failure for behavior
  work;
- include exact Extension/Neo/Studio source citations and concrete findings in
  every phase-aware handoff;
- produce a phase-aware handoff after every session.
