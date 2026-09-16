# Runtime Unblock — Acceptance Matrix

Objective gates for the Runtime Unblock milestone. Each gate defines required
evidence, allowed and prohibited operations, a pass condition, a stop
condition, and a rollback requirement.

**No gate in this document has been executed.** G1 is not open: it requires a
valid owner receipt that does not exist. Gates are strictly sequential — no
gate may be entered until every prior gate has passed and its evidence is
recorded.

Notation: **pass** advances. **stop** halts the milestone and returns to the
owner. Any prohibited operation observed at any gate is an immediate stop
regardless of that gate's own result.

---

## G0 — Preflight eligibility (PASSED)

| | |
|---|---|
| **Required evidence** | A ResolvePlan bound to the current HEAD of `feature/runtime-unblock-preflight-fix` with `authorization_eligible: true`, `authorization_blockers: []`; clean `git status --short`; Neo parity `0 0`; 179 preflight tests OK; 63 Studio tests OK. The concrete plan SHA-256 is recorded in `Evidence\studio-runtime-unblock-preflight\OWNER_AUTHORIZATION_TEMPLATE.md`, which is outside this repository and therefore does not invalidate itself on commit. |
| **Allowed** | local read, local test execution, local commit |
| **Prohibited** | network, package mutation, Resolve execution, launch, push |
| **Pass** | plan is `PLAN_READY` / `NETWORK_NOT_AUTHORIZED` / `NO_GO` / eligible on a clean tree |
| **Stop** | eligibility false, or achieving it requires weakening fail-closed behaviour |
| **Rollback** | `git switch docs/phase0-complete-baseline` — the two preflight commits are additive and unpushed |
| **Status** | **PASSED** |

---

## G1 — Metadata research authorized

| | |
|---|---|
| **Required evidence** | Owner receipt matching `OWNER_AUTHORIZATION_TEMPLATE.md` §3: 22 exact fields, `approved_operations: ["CANDIDATE_METADATA"]`, all six `allow_*` exactly `false`, `single_use: true`, `plan_sha256` and `repository_head` bound to G0's plan, valid time window, `owner_statement` prefixed `HUMAN OWNER AUTHORIZATION:` |
| **Allowed** | validating the receipt against the plan bundle; recording the ledger entry |
| **Prohibited** | any network access; filling any blank on the owner's behalf; reusing a prior receipt; proceeding on a partially completed template |
| **Pass** | `validate_receipt_plan_bindings`, `validate_safe_permissions`, `validate_operations`, `validate_hosts`, `validate_requested_indexes`, `validate_candidate_scope`, and `validate_authorization_time_window` all succeed |
| **Stop** | any validation failure; expired or future-dated receipt; scope mismatch; HEAD moved since the plan was generated |
| **Rollback** | discard the receipt; no state changed |
| **Status** | **NOT OPEN — no receipt exists** |

---

## G2 — Metadata research complete

| | |
|---|---|
| **Required evidence** | For each candidate in scope: declared Pillow specifier, declared `Requires-Python`, pinned `gradio-client` version, source URL, retrieval timestamp, response digest. All artifacts under `Evidence/preflight/` only |
| **Allowed** | HTTPS metadata retrieval from approved hosts and index URLs only, limited to `CANDIDATE_METADATA` |
| **Prohibited** | downloading wheels or sdists; hosts or indexes outside the receipt; exceeding `maximum_candidate_count`; writing outside the approved cache/temp/report roots; any pip invocation |
| **Pass** | every candidate in scope has a complete record; every network destination is in the receipt |
| **Stop** | any request to a non-approved host; receipt expiry mid-run; incomplete or ambiguous metadata for any candidate |
| **Rollback** | delete `Evidence/preflight/cache` and `temp` contents for this run; environment untouched by construction |

---

## G3 — Candidate set evaluated

| | |
|---|---|
| **Required evidence** | Per-candidate verdict against all four bounds: (a) Pillow specifier admits `>=11.1.0`; (b) supports Python 3.13.5; (c) within `>=4.0,<6.0` for `gradio_rangeslider 0.0.8`; (d) **retains all twelve patched private APIs** listed in Dependency Source Inventory §5 |
| **Allowed** | static analysis of retrieved metadata; static reading of retained Forge source |
| **Prohibited** | installing a candidate to test it; importing a candidate; inferring (d) from a version number |
| **Pass** | at least one candidate satisfies (a)-(c) **and** has an evidence-backed verdict on (d) |
| **Stop** | no candidate satisfies (a)-(c); or (d) cannot be determined without installing — in which case return to the owner for a widened scope, do not install |
| **Rollback** | none required; analysis only |

Gate (d) is the one most likely to stop this milestone. `gradio.blocks`,
`gradio.processing_utils`, `gradio.data_classes`, `gradio.utils`,
`gradio.networking`, and `gradio.context` are private API. A candidate that
satisfies the Pillow arithmetic but drops any of them is not viable.

---

## G4 — Dependency plan satisfiable

| | |
|---|---|
| **Required evidence** | A regenerated ResolvePlan showing a non-empty normalized intersection across the full mandatory constraint set from Dependency Source Inventory §4, with the selected candidate substituted for Gradio 4.40.0; empty `minimal_unsatisfiable_cores` |
| **Allowed** | plan regeneration; local test execution |
| **Prohibited** | applying the plan; any package mutation; ignoring or suppressing a constraint to force satisfiability |
| **Pass** | `normalized_intersection` is non-empty and `decision` is not `DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY` |
| **Stop** | intersection still empty; or satisfiability requires dropping a mandatory constraint — that is an owner decision (see Dependency Source Inventory §8), not an agent one |
| **Rollback** | none; no mutation performed |

---

## G5 — Package mutation authorized

| | |
|---|---|
| **Required evidence** | A **new** owner receipt, distinct from G1, naming the exact distributions and versions, and setting only the specific `allow_*` flags required. A full `app/venv` backup or a verified reinstall procedure, recorded before any mutation |
| **Allowed** | receipt validation only |
| **Prohibited** | reusing the G1 receipt; treating G1 as implying mutation permission; mutation before the rollback artifact exists and is verified |
| **Pass** | new receipt validates and names exactly the G4 candidate set |
| **Stop** | no new receipt; scope broader than G4's outcome; no verified rollback artifact |
| **Rollback** | n/a — this gate authorizes, it does not act |

Note the installer behaviour from Dependency Source Inventory §2: the
`is_installed("gradio")` guard means a pin change alone will not move an
installed Gradio. A mutation authorization must therefore explicitly cover
uninstall or upgrade, not just install.

---

## G6 — Package mutation applied

| | |
|---|---|
| **Required evidence** | Exact commands run, full pip output, before/after `pip freeze` or dist-info inventory, resulting versions of `gradio`, `gradio-client`, `gradio_rangeslider`, `pillow`, `pillow-heif`, `pillow-jxl-plugin` |
| **Allowed** | only the exact operations named in the G5 receipt |
| **Prohibited** | any distribution not named in G5; `--upgrade` beyond the named scope; resolver-driven collateral upgrades; touching an external Python installation; any network host outside the receipt |
| **Pass** | every resulting version matches G4's plan exactly, with no unexpected collateral change |
| **Stop** | any unexpected version change; any resolver conflict; any non-zero pip exit |
| **Rollback** | **mandatory** — restore the G5 venv backup and re-verify with a fresh ResolvePlan before any further gate |

---

## G7 — Environment consistency verified

| | |
|---|---|
| **Required evidence** | Fresh ResolvePlan on a clean tree; 63 Studio tests; 179 preflight tests; 21 loopback checks; `git status --short` empty; `git diff --check` empty; Neo parity `0 0`; canonical frontend manifest identity unchanged |
| **Allowed** | local tests; mock Studio server on IPv4 loopback |
| **Prohibited** | Forge launch; CUDA init; model load; real generation |
| **Pass** | all suites pass; canonical frontend byte-identical; tree clean; parity `0 0` |
| **Stop** | any test regression; any canonical frontend hash change |
| **Rollback** | restore the G5 venv backup; re-run this gate |

Canonical frontend identity must be re-verified here specifically: a Gradio
change touches `ui_tempdir.py`'s Pillow handling, which is adjacent to static
asset serving.

---

## G8 — Forge launch authorized

| | |
|---|---|
| **Required evidence** | A **third** owner receipt with `allow_launch: true`, naming the exact launch command, port, bind address, and a maximum runtime; explicit statement on whether CUDA initialization and model loading are permitted |
| **Allowed** | receipt validation only |
| **Prohibited** | reusing G1 or G5 receipts; launching on any interface other than the named one; launching before G7 passes |
| **Pass** | receipt validates; G7 recorded as passed |
| **Stop** | no receipt; G7 not passed; requested bind address is not loopback without explicit owner acknowledgement |
| **Rollback** | n/a |

---

## G9 — Controlled Forge startup

| | |
|---|---|
| **Required evidence** | Full startup log; exact command; wall-clock to ready; any warning or traceback; confirmation that no model was loaded if G8 withheld that permission |
| **Allowed** | exactly the G8 command, once |
| **Prohibited** | extension updates; font or asset downloads; `--listen` or any non-loopback bind unless G8 names it; model download; repeated restarts to "get a clean log" |
| **Pass** | process reaches ready state with no unhandled exception |
| **Stop** | any traceback; any attempted network fetch not authorized by G8; exceeding the G8 maximum runtime |
| **Rollback** | terminate the process; verify port released; verify no file written outside `Evidence/` and the app's own expected output roots |

---

## G10 — Loopback readiness

| | |
|---|---|
| **Required evidence** | HTTP response from the bound loopback port; served route inventory; confirmation that foreign `Host`/`Origin` are rejected; CSP header capture |
| **Allowed** | requests to the G8 loopback port from the local machine |
| **Prohibited** | requests from any other interface; exposing the port externally; browser automation against a non-loopback origin |
| **Pass** | loopback responds; foreign Host and Origin rejected; CSP is local-only |
| **Stop** | any external interface reachable; foreign Host or Origin accepted |
| **Rollback** | terminate; verify port released |

---

## G11 — CUDA identity

| | |
|---|---|
| **Required evidence** | Reported device name, compute capability, driver and CUDA runtime versions, total and free VRAM, and the `torch` build tag — all captured from the running process |
| **Allowed** | CUDA initialization only if G8 explicitly permits it |
| **Prohibited** | CUDA init without explicit G8 permission; model load; any generation; global CUDA synchronization added for measurement (AGENTS.md hard rule) |
| **Pass** | device identity captured and matches owner expectation |
| **Stop** | no device; driver mismatch; VRAM below the owner's stated minimum |
| **Rollback** | terminate the process |

---

## G12 — Clean shutdown

| | |
|---|---|
| **Required evidence** | Shutdown method; exit code; confirmation the port is released; no orphaned child process; no temp file left outside approved roots |
| **Allowed** | graceful shutdown, then verification |
| **Prohibited** | forced kill as the first attempt; leaving the process running between gates |
| **Pass** | exit is clean, port released, no orphan, no stray file |
| **Stop** | port retained; orphaned process; files written outside approved roots |
| **Rollback** | manual cleanup, then re-verify |

---

## G13 — Repeat launch

| | |
|---|---|
| **Required evidence** | A second G9-G12 cycle producing equivalent results; a diff of the two startup logs |
| **Allowed** | one further launch under the same G8 receipt, if its validity window and single-use terms permit — otherwise a new receipt is required |
| **Prohibited** | treating a single successful launch as proof of stability; reusing a spent single-use receipt |
| **Pass** | second cycle is equivalent; no first-run-only side effect (no download, no cache write outside approved roots, no config mutation) |
| **Stop** | divergent behaviour between cycles; first run mutated state the second depends on |
| **Rollback** | full G6 rollback and re-verify from G7 |

Per AGENTS.md: performance must not be compared across server restarts as
proof. This gate tests *reproducibility of startup*, not performance.

---

## G14 — Rollback verified

| | |
|---|---|
| **Required evidence** | Rollback executed on purpose, not as incident response: venv restored, versions confirmed back at Gradio 4.40.0 / Pillow 12.3.0, 63 Studio tests OK, 179 preflight tests OK, tree clean, Neo parity `0 0`, canonical frontend byte-identical |
| **Allowed** | restoring the G5 backup; local tests |
| **Prohibited** | declaring rollback capability without exercising it; leaving the environment in the rolled-back state without owner direction on which state to keep |
| **Pass** | pre-mutation state fully restored and verified green |
| **Stop** | rollback does not restore a green state — the milestone is not safely reversible and must halt for owner decision |
| **Rollback** | this gate *is* the rollback proof |

---

## Cross-gate invariants

These hold at every gate. Violation is an immediate stop.

```text
no push
no access outside Studio-Standalone
no Private-Local access
canonical frontend files byte-identical
.gitattributes preserved and tracked
Neo UI present and removable, not modified
neo...upstream/neo = 0 0
the three pre-adapter correction commits unamended
prior ResolvePlan bundles preserved
no receipt self-issued, inferred, or reused across gates
no fail-closed behaviour weakened to pass a gate
```

## Current position

```text
G0   PASSED
G1   NOT OPEN — awaiting owner receipt
G2+  BLOCKED
```
