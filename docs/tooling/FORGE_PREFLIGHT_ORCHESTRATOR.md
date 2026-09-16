# Forge Preflight Orchestrator

## Status

The Forge Preflight Orchestrator is workspace-bound tooling for validating
launch prerequisites before Forge is allowed to mutate packages or start an
interface.

The current implementation:

- performs no Forge launch;
- imports no Forge, Neo UI, Studio UI, Torch, Gradio, or model code;
- performs no package installation, removal, upgrade, or download;
- performs no command-processor discovery;
- inspects only the preserved workspace virtual environment;
- records reports under `Evidence/preflight/`; and
- leaves executable Resolve and the child pip bootstrap fail closed because
  receipt validation is non-authorizing and preventive destination and
  filesystem controls are not configured.

This tooling does not repair the preserved dependency conflict and does not
authorize Attempt 4.

## Architecture

```text
CLI
 |
 v
PreflightOrchestrator
 |------------------ StaticInspector ---------- source + METADATA
 |                         |
 |                         v
 |                ConstraintDecisionEngine ---- pure decision
 |
 |------------------ Contract validation ------- sanitized data only
 |
 |------------------ ReportRenderer ------------ JSON + Markdown
 |
 |------------------ Verify -------------------- agreement + preserved decision
 |                         |
 |                         `-------------- separate verification JSON + Markdown
 |
 |------------------ Resolve Plan -------------- no-network review digest
 |
 `------------------ Resolver ------------------ validation-only receipt gate

All tool-owned filesystem operations
                |
                v
          WorkspaceBoundary
      app / Reference / Evidence only
```

The modules under `scripts/preflight/` have no dependency on Neo UI
construction, Gradio component IDs, event handlers, JavaScript globals, tabs,
layout modules, Studio UI construction, or generation behavior.

## Filesystem boundary

After the stdlib-only bootstrap validates its own import paths,
`WorkspaceBoundary` is the sole filesystem gateway used by the orchestrator.
It permits only:

- `app/`
- `Reference/`
- `Evidence/`

It rejects before target access:

- lexical paths outside `Studio-Standalone`;
- sibling names that only share an allowed prefix;
- another drive;
- UNC or device paths;
- alternate data streams, reserved Windows device names, and ambiguous
  trailing-dot or trailing-space names;
- traversal into `Private-Local`;
- symlinks;
- Windows reparse points and junctions; and
- regular files with multiple hard links.

`Private-Local` has no generic configuration, environment-variable, CLI, or
document override. A future operation would require a narrowly scoped
capability designed around an exact path and exact human-owner approval. No
such capability is implemented.

Access records contain workspace-relative paths or
`<outside-workspace>`—never an external absolute path.

### Filesystem policy binding

Every tool-owned contract, launch-decision report, verification report,
Resolve plan, and structured pip-report consumption result binds the same
strict policy object:

```text
filesystem_policy_version: forge-filesystem-policy/v1
normalized_permitted_roots: app/, Evidence/, Reference/
private_local_status: PROHIBITED
policy_source: app/AGENTS.md
policy_source_sha256: sha256:<exact raw-byte digest>
outside_access_allowed: false
```

The checked-in contract uses schema `forge-preflight-contract/v2`; launch
reports use `forge-preflight-report/v2`; and verification reports use
`forge-preflight-verification/v2`. Unknown top-level contract, launch-report,
or verification fields; unknown policy fields; wrong JSON types; noncanonical
root order; a different policy source or raw-byte SHA-256; a remapped root; a
changed `Private-Local` boundary; or an enabled outside-access claim fail
closed. Verify also lexically authorizes the schema-defined path-bearing
fields named `path`, `policy_source`, `venv_python`, and `inputs`. It rejects a
report when any such value falls outside `app/`, `Evidence/`, or `Reference/`
without performing target I/O.

`PROHIBITED` means this tool has no `Private-Local` capability. It does not
claim to supersede the human owner's exclusive ability to approve a different
exact operation later.

## Python boundary

The current approved executable is:

```text
app/venv/Scripts/python.exe
```

Static mode compares the currently executing interpreter identity with that
exact workspace path before inspecting local evidence. It does not:

- read `sys.base_prefix`;
- read a base executable attribute;
- read the venv's external bootstrap identity;
- search `PATH`;
- inspect another Python;
- inspect Miniconda;
- inspect the registry; or
- follow an interpreter path outside the workspace.

Routine invocation uses the stdlib-only `bootstrap.py` with `-I -S -B`.
This ignores user environment Python paths, disables automatic site and
`.pth` processing, and disables bytecode writes before the tool imports its
workspace dependencies. The bootstrap uses stdlib-only `lstat` checks to
reject links, reparse points, hard-linked regular files, wrong path types, and
import-shadow candidates throughout the preflight and `packaging` import
trees.

The bootstrap does not prepend `app/` or the general venv `site-packages`
directory to the module search path. It seals the top-level `scripts`
namespace to the validated `app/scripts/` directory, rejecting a preloaded
namespace, so a later installed `scripts` package cannot replace the
preflight package. Standard-library paths retain precedence. The exact
validated `packaging` package is then loaded from its own package path through
an explicit import specification; a preloaded `packaging` namespace or sibling
module shadow is rejected.

The report may state that the venv was bootstrapped during an earlier approved
attempt. It deliberately does not revisit that external origin.

## Command-processor boundary

Static, Contract, and Verify modes execute entirely in the current Python
process. They do not discover, stat, verify, or execute `cmd.exe`, PowerShell,
a POSIX shell, or another command processor.

A contract may retain a human-supplied sanitized identity. Its required
verification state is `OWNER_APPROVAL_REQUIRED`; the tool cannot turn that
identity into runtime approval.

The future resolver uses the exact workspace venv Python with an argument
array and `shell=False`. It does not invoke a batch or shell wrapper.

## Modes and capabilities

| Mode | Local source and metadata reads | Evidence report writes | Process execution | Network | Package mutation |
|---|---:|---:|---:|---:|---:|
| Static | Yes | Yes | No | No | No |
| Contract | Yes | Optional | No | No | No |
| Verify | Existing report pair + current policy | Separate verification pair | No | No | No |
| Resolve Plan | Yes | Yes | No | No | No |
| Resolve | Receipt validation only | Blocked | Blocked | Blocked | No |

`GO` means only that the selected preflight gate passed. It never authorizes a
Forge launch, package mutation, model load, generation, public listener, or
later phase.

### Decision model

Verify has two independent axes. `verification_status` says whether the
source JSON and Markdown are internally consistent, canonical, policy-current
views of one report. The unkeyed report hash does not prove authorship.
`contract_decision` is the decision already present in that validated source
report.
Verify never replaces it with `GO`.

| Verification status | Contract decision | Effective decision | Exit |
|---|---|---|---:|
| `PASS` | `GO` | `GO` | 0 |
| `PASS` | `GO_WITH_WARNINGS` | `GO_WITH_WARNINGS` | 1 |
| `PASS` | `NO_GO` | `NO_GO` | 2 |
| `FAIL` | untrusted / `null` | `NO_GO` | 2 |

The verification model contains `verification_status`, `contract_decision`,
`effective_decision`, and `verification_reason`. It deliberately has no
top-level `decision: GO` shortcut. A verified `NO_GO` Markdown report states:

> Verification passed, but launch remains blocked by the contract decision.

## Static mode

Static mode reads only:

- `app/AGENTS.md` as exact raw bytes for the policy SHA-256;
- `app/requirements.txt`;
- the launcher-owned Gradio default in `app/modules/launch_utils.py`;
- exact installed Gradio, Pillow, and pillow-heif `METADATA`; and
- the sanitized static contract fixture.

It parses files as data. It never imports the launcher or installed product
packages.

The current expected decision is:

```text
NO_GO
DEPENDENCY_CONSTRAINT_INTERSECTION_EMPTY
```

Owners:

```text
repository requirements.txt:2        Pillow==12.3.0
gradio 4.40.0                        pillow>=8.0,<11.0
pillow-heif 1.4.0                    pillow>=11.1.0
```

Two minimal conflicts are retained:

```text
Pillow==12.3.0 AND Pillow<11.0
Pillow<11.0 AND Pillow>=11.1.0
```

The decision does not select or recommend a replacement package version.

Static cannot provision the state that would make its narrow dependency
decision become `GO`. Its source pins must already identify exact versions,
and matching Gradio, Pillow, and pillow-heif `METADATA` must already exist in
the workspace venv. Any transition to that state therefore requires a
separate, explicitly owner-approved provisioning operation. If pins change
without matching locally installed metadata, Static fails closed; it does not
search a package index or infer future metadata. Even when the narrow
dependency decision becomes `GO`, Static's top-level contract decision is
`GO_WITH_WARNINGS` with `STATIC_DEPENDENCY_COVERAGE_INCOMPLETE` until complete
dependency coverage exists.

## Contract mode

Contract mode validates that:

- the workspace identity is represented as `<WORKSPACE>`;
- the only Python identity is `app/venv/Scripts/python.exe`;
- command-processor discovery is prohibited;
- command-processor verification remains owner-controlled;
- network access is not authorized; and
- `Private-Local` access is not authorized;
- the exact normalized permitted roots remain unchanged;
- `app/AGENTS.md` still has the contract-bound raw-byte SHA-256; and
- outside access remains false.

Contract validation cannot grant network, filesystem, or launch authority.

## Verify mode

The source JSON and Markdown are rendered from one sanitized canonical result.
A SHA-256 report ID binds the semantic JSON payload. Markdown retains the same
report ID, mode, decision, contract decision, effective decision, and reason
code in its header.

Source report files are written through same-directory `.partial` paths and
atomically replaced. Verify regenerates the complete canonical Markdown view
from the validated JSON and requires byte-for-byte equality. It then writes a
different sibling pair:

```text
<source-stem>.verification.json
<source-stem>.verification.md
```

Both verification files are rendered from one canonical verification model
and share its SHA-256 fingerprint. Verify never overwrites or repairs the
source pair.

Verify mode fails closed when:

- JSON is malformed;
- JSON contains a non-standard non-finite number such as `NaN` or `Infinity`;
- JSON contains duplicate keys;
- JSON or Markdown exceeds the input size limit;
- JSON is not the exact canonical serialized view;
- the report ID does not match the canonical payload;
- Markdown header semantics differ;
- the report ID comment differs or is absent; or
- a private path or likely secret remains;
- the report schema or closed decision value is unsupported;
- `decision`, `contract_decision`, and `effective_decision` differ;
- any filesystem-policy field differs from the strict contract;
- the current `app/AGENTS.md` raw-byte hash differs; or
- a referenced input or source path falls outside the permitted roots.

On any content verification failure after safe input and output paths have
been accepted—and while a safe current-policy snapshot remains available—the
source contract decision is untrusted:
`verification_status` is `FAIL`, `contract_decision` is `null`, and
`effective_decision` is `NO_GO`. Verify still records that fail-closed outcome
in the separate verification pair. A path that cannot itself be accepted by
the boundary fails before output planning and therefore produces no file.
Verify never repairs, resolves, or launches anything after failure.

## Resolve mode

Resolve planning and dormant execution code are implemented, but Resolve has
not been run. Executable Resolve cannot currently accept any authorization.

The validation-only workflow, receipt contract, threat model, and unresolved
human-owner decision gates are defined in
[Forge Preflight Resolve Authorization](FORGE_PREFLIGHT_RESOLVE_AUTHORIZATION.md).
That document is not authorization and does not enable Resolve.

Resolution uses two separate steps. First, `resolve-plan` accepts an optional
bounded candidate scope through its explicit candidate-scope arguments. It
does not read an authorization receipt or a scope-proposal file. That mode
performs no network or subprocess operation. ResolvePlan records the current
dependency decision,
including `NO_GO`, and may still emit a deterministic review payload and digest
with `plan_status: PLAN_READY` and
`authorization_status: NETWORK_NOT_AUTHORIZED`. Until the human owner supplies
one exact bounded Gradio candidate scope, it records
`candidate_scope_status: OWNER_INPUT_REQUIRED` and invents no range or
candidate version. These are planning statuses, not a launch decision or
execution authority. A plan with owner input still grants no authority.
Each plan also carries `authorization_eligible` and a canonical
`authorization_blockers` list. Dirty tracked files, staged changes, untracked
non-ignored files, incomplete Git evidence, or missing exact Neo parity keep
the plan diagnostic-only. In particular, dirty or incomplete Git state records
`authorization_eligible: false` and
`RESOLVE_PLAN_DIRTY_WORKTREE`; no receipt can override that blocker.

The Resolve authorization document records the owner-identified
`sha256:54dc3a1ab627b55e768b06f0e707750dead9367f2070d216248e9d9f8df339cc`
plan as a **DEVELOPMENT ARTIFACT — NOT ELIGIBLE FOR AUTHORIZATION**.

A validation-only receipt may be placed by the human owner under the approved
report root:

```text
Evidence/preflight/reports/authorizations/
```

The closed schema is `forge-resolve-authorization/v1`. The tool does not
generate a receipt. No Codex process, assistant, agent, test, fixture, template,
plan, report, consumption marker, or project document can issue human-owner
authority. A checked-in example must say `issued_by: NOT_AUTHORIZED` and
`owner_statement: NOT AUTHORIZATION`.

Validation may structurally return `PASS`, but it always retains
`execution_authorized: false`. A self-asserted JSON value does not
cryptographically prove human origin. Executable Resolve and the child pip
bootstrap remain disabled, and preventive host and filesystem enforcement
remain unresolved.

If a future separately approved execution design addresses those controls, an
accepted plan would receive digest-derived run directories.
Before creating those directories or starting the process, Resolve atomically
reserves the consumption-marker name with `O_CREAT | O_EXCL` and writes the
plan digest, dependency-plan digest, repository `HEAD`, and run ID. This is an
exclusive one-time procedural marker, not authorization and not a
durability guarantee: the implementation does not claim a disk-flush or
transactional commit. A partial marker still occupies the exclusive name and
blocks automatic replay. A missing or manually deleted consumption marker
after a claimed attempt cannot establish that the authorization is unused;
operational policy is fail closed, preserve the remaining Evidence, and
require a new explicit human-owner review rather than retrying automatically.

ResolvePlan retains the local known-conflict result as review evidence; it does
not relabel that result as compatible. In E0, validation stops before any
process execution or network access regardless of whether the dependency
decision is `GO`, `NO_GO`, or `INCONCLUSIVE`. Any future candidate-resolution
execution requires a new, separately approved implementation and owner
decision.

The resolver derives launcher requirements directly from the
`GRADIO_PACKAGE` default in `app/modules/launch_utils.py`; a caller does not
supply them. The plan records the derived requirements and binds the complete
launcher source hash.

The plan digest binds:

- the exact repository `HEAD`;
- a separate canonical dependency-plan SHA-256;
- the exact repository requirements content;
- the complete `modules/launch_utils.py` source and requirements derived from
  it;
- the canonical SHA-256 of the exact narrow Static dependency-decision object
  recorded by planning;
- all preflight runtime-source hashes;
- the exact workspace Python launcher bytes;
- complete bounded manifests of the in-workspace pip and `packaging` package
  trees, including cached bytecode, plus their selected distribution metadata;
- the report-coverage mode;
- the complete current filesystem-policy object and `app/AGENTS.md` hash;
- the owner-supplied marker environment;
- the index and declared endpoint scope;
- the command template and minimal environment overlay; and
- the timeout and retained-output limit.

Requirements, launcher source, tool sources, Python, pip, and `packaging` are
checked again before execution. Tool and execution manifests are also compared
again after the process returns.

The protected product manifest is derived dynamically from the plan's exact
Gradio, Pillow, and pillow-heif requirements. It resolves the matching local
distribution `METADATA` rather than relying on fixed Phase 0 audit versions.
The pre/post comparison covers those three metadata
files plus `requirements.txt`, `modules/launch_utils.py`, `launch.py`, and
`webui.bat`. This is a targeted mutation sentinel, not a hash of the full venv,
all product files, registry state, or the wider operating-system environment.

The dormant candidate-resolution shape is limited to:

```text
app/venv/Scripts/python.exe -I -S -B
  app/scripts/preflight/pip_bootstrap.py install
  --dry-run
  --ignore-installed
  --only-binary=:all:
  --report <workspace Evidence report>
  --index-url <owner-approved index>
  -r <workspace combined requirements>
```

The stdlib-only pip bootstrap is independently disabled. Its entry point raises
`PIP_BOOTSTRAP_EXECUTION_DISABLED` before validating arguments, traversing
paths, importing pip, or accessing a network. Direct invocation therefore
cannot bypass the disabled parent execution path.

The dormant post-gate code is designed to validate the exact workspace pip
package and its required entry files, then load that package through an
explicit import specification without adding the general `site-packages`
directory to `sys.path`. It accepts only the exact ordered dry-run argument
shape above, one digest-derived 16-character run ID, matching Evidence report
and requirements paths, and an unauthenticated HTTPS index without a query or
fragment. Extra, reordered, `--upgrade`, uninstall, `--user`, `--target`, or
outside-workspace arguments are rejected. These checks are defense in depth,
not execution authority. Automatic site and `.pth` processing stay disabled.
The resolver does not use a live installation, a shell, a Forge wrapper, or an
application entry point.

The future environment overlay is:

```text
PIP_CACHE_DIR=Evidence/preflight/cache/<run-id>
TEMP=Evidence/preflight/temp/<run-id>
TMP=Evidence/preflight/temp/<run-id>
PIP_CONFIG_FILE=NUL
PIP_DISABLE_PIP_VERSION_CHECK=1
PIP_KEYRING_PROVIDER=disabled
PIP_NO_INPUT=1
NETRC=Evidence/preflight/temp/<run-id>/empty.netrc
PYTHONDONTWRITEBYTECODE=1
PYTHONNOUSERSITE=1
NO_PROXY=*
no_proxy=*
```

`NUL` is the human-owner-mandated Windows null-device sentinel. The process
starts with the exact owner-facing value `PIP_CONFIG_FILE=NUL`; the validated
pip bootstrap requires that value and then normalizes it internally to
`os.devnull` before pip is loaded. This satisfies installed pip's
case-sensitive null-sentinel comparison and suppresses configuration-file
loading. `WorkspaceFS` never opens, stats, or treats `NUL` as a workspace path.

Both proxy-bypass spellings are set to `*` in the digest-bound child
environment. This prevents the vendored Requests path from falling back to
Windows user proxy settings when no explicit proxy environment is present. It
does not turn the declared endpoint list into preventive network confinement.

`SystemRoot`, `WINDIR`, and `PATH` are deliberately omitted. The executable is
an exact absolute workspace path and no shell is used, but whether that minimal
environment can start the venv Python on this Windows host remains runtime
`INCONCLUSIVE` because Resolve has never run. The tool must not discover,
inherit, or add those variables automatically. If a future start requires any
of them, stop before retrying and obtain new human-owner approval for the exact
values and external runtime implications.

The structured-report consumer is bound to the reviewed plan and a closed
coverage value, `pip-dry-run-selected-candidate-set`. It:

- accepts only pip report schema version `1`;
- merges trusted repository and launcher root requirements;
- retains each candidate's original `Requires-Dist` source index;
- preserves full round-trippable owner, source, direct, mandatory, and marker
  records in addition to presentation rows;
- requires the complete string-valued PEP 508 marker environment to equal the
  owner-reviewed plan exactly;
- requires a valid archive SHA-256 for every selected candidate;
- seeds reachability only from active trusted roots, propagates selected extras
  through active dependency edges, and treats disconnected candidates,
  including orphan-only dependency cycles, as unjustified rather than allowing
  them to legitimize one another;
- requires one selected candidate for every active mandatory dependency;
- validates concrete candidates with packaging's PEP 440 implementation;
- returns `NO_GO` when a selected candidate violates an active constraint;
- returns `NO_GO` when a selected download URL contains credentials, is not
  HTTPS, is outside declared endpoint scope, or contains a query or fragment;
- retains no selected or recommended replacement; and
- audits reported download endpoints after the run.

Endpoint inspection is post-hoc evidence, not preventive network confinement.
The required owner acknowledgement and documentation make that limitation
explicit; a proxy, mirror, firewall, or equivalent approved control would be
required to enforce destinations before access.

The raw `pip-dry-run-report.json` is local Evidence, not a sanitized Git
document. A query or fragment produces `NO_GO` because it may carry a private
token even though the consumer does not reproduce that suffix in its summary.
Retained process logs sanitize absolute paths, credentials, bearer values, URL
paths, queries, and fragments before writing.
Before any raw Resolve Evidence is shared, copied, or proposed for Git, the
human owner must review the raw pip report and retained logs for private paths,
URLs, credentials, tokens, and other likely secrets. Passing the structured
consumer is not a substitute for that privacy review.

## Evidence layout

```text
Evidence/preflight/
|-- cache/
|   `-- <run-id>/
|-- temp/
|   `-- <run-id>/
|       |-- combined-requirements.txt
|       `-- empty.netrc
|-- reports/
|   |-- static-preflight.json
|   |-- static-preflight.md
|   |-- static-preflight.verification.json
|   |-- static-preflight.verification.md
|   |-- authorizations/
|   |   `-- <human-owner-placed-authorization-v1>.json
|   `-- <run-id>/
|       |-- resolve-attempt.json
|       |-- pip-dry-run-report.json
|       |-- pip-dry-run-stdout.txt
|       `-- pip-dry-run-stderr.txt
|-- resolve-plans/
|   `-- <PLAN_ID>/
|       |-- resolve-plan.json
|       |-- resolve-plan.md
|       |-- authorization-request.json
|       |-- authorization-request.md
|       |-- dependency-inputs.json
|       |-- source-bindings.json
|       |-- privacy-review.md
|       `-- tool-version.txt
`-- authorization-ledger/
    `-- <sha256-of-authorization-id>.json
```

No cache, temporary file, or report is directed to the user profile, system
temporary directory, external pip cache, another repository, or another
drive.

## Invocation

Working directory:

```text
Studio-Standalone/app
```

Exact executable:

```text
venv/Scripts/python.exe
```

Static:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py static
```

Contract without report writes:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py contract
```

Verify an existing pair:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py verify
  --json Evidence/preflight/reports/static-preflight.json
  --markdown Evidence/preflight/reports/static-preflight.md
```

Report and validation inputs beginning with `app`, `Evidence`, or `Reference`
are workspace-relative. The `resolve-plan` repository and Evidence roots are
resolved from `app/`, as shown below, and then must match the exact owned roots.

Resolve invocation is intentionally omitted from routine instructions. It
remains a separately reviewed human-owner operation.

The no-network planning handshake is available for a later reviewed operation:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py resolve-plan
  --repository-root .
  --evidence-root ../Evidence/preflight
```

This command is limited to the eight-file ResolvePlan review bundle. It cannot
execute pip or grant network authority.

## Tests

The standard-library unittest suite covers:

1. allowed-root containment;
2. outside-path rejection before target I/O;
3. `Private-Local` rejection before target I/O;
4. Static mode through the existing workspace venv;
5. absence of base-interpreter inspection;
6. absence of command-processor discovery;
7. Resolve rejection without owner authorization;
8. Evidence-only resolver cache, temporary, and report paths;
9. deterministic conflict-fixture `NO_GO`;
10. the complete Verify decision and exit-code matrix;
11. JSON, Markdown, and verification-view semantic agreement;
12. verified `NO_GO` remaining `NO_GO`;
13. JSON and Markdown tamper failure;
14. filesystem-policy schema, hash, root, `Private-Local`, and drift gates;
15. outside referenced-path rejection before target I/O;
16. report path and likely-secret sanitization; and
17. no launch or protected product/package mutation.

Additional cases cover bootstrap reparse and import-shadow rejection, reserved
Windows device aliases, exclusive one-time consumption markers, strict
duplicate-key report parsing, full canonical views, sealed import namespaces,
plan-scoped
Python/pip/packaging identity including cached bytecode, source-derived
launcher requirements, exact marker-environment matching, round-trippable pip
constraints, archive hashes, rooted candidate reachability, concrete candidate
validation, source provenance, private URL suffix rejection, and post-hoc
endpoint auditing. The E0 receipt contract requires validation coverage to
preserve `execution_authorized: false` even after a structural `PASS`, reject
every unsafe `allow_*` value, and prove that a documentation template marked
`NOT AUTHORIZATION` cannot validate as human-owner-issued. The pip bootstrap
must continue to reject every tested deviation from its exact dry-run argument
shape.

The Git eligibility regression matrix separately proves clean complete state,
tracked-file dirtiness, staged changes, untracked non-ignored files, and
incomplete evidence. It verifies diagnostic-plan blockers, rejection before
receipt reads, zero consumption-marker writes for ineligible plans, and
post-plan HEAD, branch, Neo-parity, and cleanliness drift.

Tests run with bytecode generation disabled:

```text
venv/Scripts/python.exe -I -S -B
  scripts/preflight/bootstrap.py self-test
```

Tests exercise only Resolve's validation-only or rejected-authorization paths
and pure planning/report adapters. No valid execution authorization can
currently be created or supplied. No test invokes pip, Forge, a command
processor, model loading, generation, or a package network.

## Limitations

- The workspace venv may internally depend on the base runtime used when it was
  created. That origin is deliberately uninspected.
- The constraint engine proves supported mandatory PEP 440 interval and exact
  conflicts. Ordered pre/dev/post/local symbolic cases and unsupported marker
  inputs return `INCONCLUSIVE` instead of guessing `GO`. A complete pip
  candidate set is checked concretely with PEP 440 rather than the symbolic
  approximation.
- Complete minimal-core enumeration is capped at 12 constraints per
  dependency. Larger unsatisfiable groups receive one deterministic
  deletion-minimized core and an explicit incomplete-enumeration flag.
- Static is intentionally a narrow known-Pillow-conflict gate. It may prove
  `NO_GO` from the audited owners, but a compatible result remains
  `GO_WITH_WARNINGS` until complete dependency coverage is supplied.
- Only the locally installed Gradio 4.40.0 metadata is available. No
  replacement Gradio version has been evaluated.
- A pip dry run can contact networks and write cache or temporary data.
  `--only-binary=:all:` prevents source-distribution build execution but does
  not make a future dry run side-effect-free.
- Declared endpoints do not prevent pip from following index links or
  redirects. The structured report detects out-of-scope download endpoints
  only after contact. Preventive destination enforcement remains an unresolved
  runtime-control item requiring a separately approved proxy, mirror, firewall,
  or equivalent boundary.
- Self-asserted receipt JSON does not cryptographically prove human origin.
  Structural validation is therefore non-authorizing and always retains
  `execution_authorized: false`. A consumption marker is only a replay
  tombstone and can never become authorization.
- The future process runner drains both output pipes while retaining only the
  owner-approved character limit. Truncation is recorded, but discarded output
  cannot be recovered from the Evidence logs.
- A timeout kills the direct pip process and bounds subsequent output-drain
  waits. The implementation does not create a Windows Job Object and therefore
  does not prove containment or termination of descendant processes. Adding a
  Job Object or another process-tree control is a separate runtime change
  requiring owner review.
- The current plan uses the full repository requirements with
  `--ignore-installed`; an approved dry run may download substantial wheel
  metadata or artifacts into its Evidence-scoped cache.
- Repository requirement directives and direct URL requirements are rejected
  before a future network process. Candidate metadata containing a direct URL
  dependency returns `INCONCLUSIVE`; this implementation does not attempt to
  prove or authorize arbitrary source locations.
- The protected product manifest is deliberately dynamic but narrow. Its clean
  comparison, together with clean tool and Python/pip/packaging post-checks,
  does not prove that every file in the venv, product tree, registry, or
  operating-system environment remained unchanged.
- ResolvePlan's independent pre-plan Git proof covers all indexed regular files,
  staged index/HEAD agreement, and untracked non-ignored files without a Git
  subprocess. It supports the repository's SHA-1 index-v2, loose-HEAD, and
  accepted repository-local ignore forms. Unsupported Git objects, extensions,
  filters, links, or ignore syntax fail closed and keep
  `authorization_eligible: false`. Established CRLF checkout bytes are
  accepted only for index-stat-bound, UTF-8, NUL-free content when repository
  and local-info attributes applicable to indexed paths are absent. Ignored or
  untracked attribute files at applicable paths also fail closed, and their
  absence, tracked bytes, and untracked enumeration are rechecked before
  `snapshot_rechecked: true` is recorded. This
  bounded consistency recheck is not an atomic snapshot or lock; a local
  mutation can still occur after the final read, so any future execution path
  must revalidate immediately beside process creation and add locking or
  handle-based protection wherever atomicity is required.
- The minimal child environment omits `SystemRoot`, `WINDIR`, and `PATH`.
  Process-start viability remains runtime-inconclusive. Any proposal to add
  them must stop for new exact human-owner approval.
- Command-processor verification remains blocked for a later exact
  human-owner approval.
- Filesystem checks reject links and recheck process paths, but a residual
  local race remains between validation and operating-system file access.
- Before any future removal of the unconditional child gate, authorization
  lifetime must be rechecked immediately beside process creation, and the
  final pip-report output needs a no-follow/reserved-handle design or
  equivalent preventive containment. Parent-path validation alone does not
  close the final-target race.
- `PIP_CONFIG_FILE=NUL` is an explicit owner-required Windows device sentinel,
  not an Evidence-local regular file. It is never passed through
  `WorkspaceFS`; the validated child bootstrap requires the exact initial value
  and normalizes it to `os.devnull` before pip's configuration loader runs.
- Nothing in this tool demonstrates Neo UI runtime parity or authorizes a
  launch. The preflight tooling imports no Neo construction or event code,
  changes no Neo file, and adds no Neo-only product behavior. Neo remains the
  unchanged baseline validation, compatibility, and rollback surface.
