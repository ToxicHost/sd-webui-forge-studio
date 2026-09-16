# Gradio Excision Plan

```text
Gradio is a temporary legacy compatibility dependency.
It is not part of the target Studio architecture.
```

Phases 1 and 2A are complete and integrated; the rest are planned, not
authorized.

Full source-grounded audit:
`Evidence\studio-headless-forge\GRADIO_DEPENDENCY_CLASSIFICATION.md`.

---

## The shape of the problem

37 module-level Gradio imports, **all** in `modules/` and `modules_forge/`.
`backend/` — the actual inference engine, 91 modules — has **zero**.

Four edges block headless inference:

```text
modules.shared            direct import: a gr.Blocks annotation and gr.themes.Base()
modules.script_callbacks  direct import                (reached by sd_models)
modules.infotext_utils    direct import                (reached by processing)
modules.ui                reached only for sRound, a 5-line rounding helper
```

That last one is worth restating: `modules/processing.py:35` imports the entire
Gradio UI tree — including twelve monkeypatches installed as an import side
effect — to obtain `math.floor(val / _STEP + 0.5) * _STEP`.

---

## Phase 1 — headless boundary and import isolation ✅ COMPLETE

**Entry:** integrated baseline, Studio 245 / preflight 179 / loopback 26 green.

**Exit:** owned `forge_headless/` facade reaching `READY_NO_MODEL` with Gradio
blocked; classification complete; result seam proven; status route live.

**Isolated:** the Studio path. No module under `forge_studio/` or
`forge_headless/` imports Gradio, `gradio_client`, `modules.ui`, or
`modules.ui_tempdir`.

**Removed:** nothing.

**Rollback:** `git switch` to `8843c718`. Additive; the legacy path is untouched.

**Capability gained:** Studio can report real backend readiness, identity, and
capability without Gradio. No user-visible generation change.

---

## Phase 2A — blocker removal and catalogue/load plumbing ✅ COMPLETE

**Exit reached:** the four named blockers are removed; a contained model
catalogue enumerates from an explicitly configured root; a load request is
validated all the way to the loader port and refused there by policy. No
checkpoint was opened, no device initialised, no model loaded.

**Done:**

1. `sRound`/`_STEP` relocated into the pure `modules/resolution.py`;
   `modules/processing.py` repointed; `modules/ui.py` re-exports;
2. `modules/shared.py`'s `demo: gr.Blocks` annotation made lazy via postponed
   annotations plus a `TYPE_CHECKING`-only import;
3. eager `gr.themes.Base()` and the `shared_gradio_themes` import removed from
   `modules/shared.py`; `ensure_gradio_theme()` added at the two UI read sites;
   `reload_gradio_theme` became an explicit lazy accessor;
4. `modules/script_callbacks.py`'s `from gradio import Blocks` moved under
   `TYPE_CHECKING` — no split was needed, because the name only ever appeared in
   an annotation and the file already had postponed annotations. `quote`/`unquote`
   extracted from `modules/infotext_utils.py` into the pure
   `modules/infotext_core.py`.

**Three edges the Phase 1 plan did not name, and had to be cleared anyway:**
`modules/options.py` (Gradio + `FormRow`), `modules/shared_items.py` (three UI
modules), `modules/extensions.py` (`scripts`). Phase 1's four-edge count came
from a shortest-path walk that recorded one path per module;
`modules.shared` actually had **fifteen** paths through **nine** importers. All
three extras were the same small kind of change.

**Result:** `modules.shared`, `modules.script_callbacks`, `modules.extensions`,
`modules.options`, and `modules.shared_items` now have **zero** module-level
paths to Gradio.

---

## Phase 2B — controlled real checkpoint load

**Entry:** Phase 2A integrated. Owner authorizes the exact permissions below.

**Exit:** a checkpoint becomes resident through the facade; `READY` reachable.

**The remaining source-level step, which needs no new permission:**
`modules/shared.py:7` imports `backend.memory_management` solely for
`xformers_available` at line 30. Its only reader is `modules/errors.py:111`.
Making it lazy is the last thing between `modules.shared` and a fully headless
import — `backend/memory_management.py` calls `torch.device`,
`torch.xpu.device_count`, `get_total_memory(get_torch_device())` (line 187), and
`get_torch_device_name(...)` (line 397) at module scope.

**Exact permissions still required:**

```text
one exact model root/path
permission to read/open one checkpoint
permission to import Torch
permission to initialize the selected device
timeout
memory/VRAM ceiling
rollback and cleanup behaviour
```

Not requested in Phase 2A.

**Rollback:** revert the phase branch; the catalogue and refusal path survive.

---

## Phase 3 — real generation and progress

**Entry:** Phase 2 integrated, a model loads headlessly.

**Exit:** one real txt2img image produced through the owned result seam, with
real `step`/`total_steps` progress and working cancellation.

**Isolated or removed:**

1. split `modules/infotext_utils.py` so `modules.processing` stops reaching
   Gradio;
2. provide a headless progress source. Forge progress lives on
   `modules.shared.state` (`sampling_step`, `sampling_steps`, `interrupt`,
   `skip`) and has no `backend/` equivalent — this is the substantive work;
3. route output through `ForgeResultDescriptor`, never the Gradio file cache;
4. retire `modules/progress.py` from the Studio path.

**Rollback:** revert; Phase 2 readiness and load survive.

**Capability gained:** **the first real image.** This is the milestone that
matters to a user.

---

## Phase 4 — extension compatibility separation

**Entry:** Phase 3 integrated.

**Exit:** built-in extensions classified; Studio-relevant ones reachable without
Gradio; the rest explicitly legacy-only.

**Isolated or removed:** `modules_forge/patch_basic.py`
(`gradio.networking.url_ok`) becomes removable. The ~15 `extensions-builtin/`
packages that import Gradio are classified, not yet moved.

**Rollback:** revert; extensions return to legacy-only.

**Capability gained:** LoRA, ControlNet, and ADetailer become reachable from the
Studio path — or are consciously deferred.

**Risk:** the largest unknown in the plan. Extension count and coupling have not
been audited.

---

## Phase 5 — legacy Neo UI removal

**Entry:** Phase 4 integrated. Owner confirms the Neo shell is no longer needed
as a rollback surface.

**Exit:** the 18 class-A presentation modules and 5 class-B compatibility
modules deleted; `modules/gradio_extensions.py` and its twelve monkeypatches
gone.

**Removed:** `modules/ui*.py`, `modules_forge/main_entry.py`,
`forge_canvas/`, `modules/api/api.py`, `modules/progress.py`,
`modules/gradio_extensions.py`.

**Rollback:** the last point at which reverting restores a working legacy UI.
After this, rollback means reverting the deletion commit specifically. Worth
tagging.

**Capability gained:** none directly — this is debt removal. Startup gets
faster and the twelve monkeypatches stop running.

**Precondition worth stating:** every prior project handoff has treated Neo UI
as the rollback surface. Removing it ends that, so it needs an explicit owner
decision, not just a green test suite.

---

## Phase 6 — remove remaining Gradio pins

**Entry:** Phase 5 integrated. No module imports Gradio.

**Exit:** `gradio` and `gradio_rangeslider` removed from
`modules/launch_utils.py:295`; `gradio-client` no longer installed; the
Pillow conflict dissolves.

**Removed:** the pin itself, and with it the constraint that has blocked Runtime
Unblock throughout this project.

**Rollback:** re-add the pin; nothing imports it, so this is low risk.

**Capability gained:** **Pillow 12.3.0 becomes uncontested.** The empty
constraint intersection that has gated every ResolvePlan is caused solely by
Gradio 4.40.0's `pillow<11.0`. Excluding Gradio, the mandatory intersection is
`>=11.1.0`, which the installed 12.3.0 already satisfies.

That makes the parked `CANDIDATE_METADATA` plan moot: there would be no Gradio
version to research. Worth weighing against Phases 2-5, which are a great deal
of work.

---

## Dependency order

```text
Phase 1  ✅ headless boundary and import isolation
   v
Phase 2A ✅ blocker removal, catalogue and load plumbing
   v
Phase 2B needs: lazy xformers_available, then owner authorization for
   v             one checkpoint read + Torch import + device init
Phase 3  needs: modules.scripts / modules_forge.main_entry / modules.profiling
   v             Gradio edges cleared, plus a headless progress source
Phase 4  needs: extension audit (not yet done)
   v
Phase 5  needs: owner decision to give up the rollback surface
   v
Phase 6  needs: nothing importing Gradio
```

Phases 2B and 3 unblock user-visible capability. Phases 4-6 are consolidation.

## What is not recommended

**Gradio version research.** The parked plan
(`sha256:04697b62…`) remains valid and unused, and Phase 2A strengthened the case
against needing it: seven `modules/` files were cleared of module-level Gradio
imports without touching Gradio's version, and the edges that remain
(`modules.scripts`, `modules_forge.main_entry`, `modules.profiling`) are ordinary
`modules/` refactors too.

Recommend it only if Phase 2B or 3 turns up a blocker that genuinely requires a
newer Gradio — which, on the evidence so far, it should not.
