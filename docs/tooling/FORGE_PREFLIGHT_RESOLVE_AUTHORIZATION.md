# Forge Preflight Resolve Authorization

> **NOT AUTHORIZATION**
>
> This document describes a validation-only, human-owner-gated design. It is
> not an authorization receipt, it grants no authority, and it does not enable
> Resolve. No live Resolve operation is part of this work.

## Purpose

This document defines the review boundary between a deterministic
workspace-only ResolvePlan and any future human-owner decision about a
candidate-metadata operation.

The current boundary is intentionally asymmetric:

- tooling may produce a non-authorizing ResolvePlan;
- tooling may validate a human-owner-placed receipt as data;
- validation may return `PASS`;
- `execution_authorized` nevertheless remains `false`; and
- executable Resolve and the child pip bootstrap remain disabled.

A planning or validation result does not authorize installation,
uninstallation, upgrade, application launch, model download, model loading,
generation, extension update, or access outside the workspace.

## Threat model

Treat every scope proposal, plan, authorization-shaped JSON file, candidate
record, package index, redirect, pip report, retained log, and mutable
workspace file as potentially malformed, stale, substituted, or hostile.
Relevant threats include:

- an assistant, tool, fixture, template, or project document claiming to speak
  for the human owner;
- a self-asserted JSON value such as `issued_by: human-owner`;
- replay of an expired or previously consumed authorization ID;
- repository, plan, dependency, policy, source, interpreter, or package-state
  drift after review;
- path traversal, alternate data streams, reserved device names, links,
  reparse points, junctions, hard links, and validation-to-use races;
- an untrusted or compromised package index, dependency confusion, malicious
  candidate metadata, direct URLs, redirects, and off-scope hosts;
- DNS, TLS, proxy, or operating-system behavior not represented by a pip
  report;
- credentials, tokens, private paths, URL queries, or fragments appearing in
  raw Evidence or process output;
- output flooding, timeout behavior, and descendant processes surviving after
  the direct child is killed; and
- a nominal pip dry run reading or writing more state than its cache, temp, and
  report settings suggest.

The design aims to prevent tooling from manufacturing authority, bind review
to one exact plan and workspace policy, keep tool-owned paths inside the
workspace, and fail closed on mismatch. It does not prove human identity
cryptographically, provide preventive network confinement, sandbox a child
process, prove full filesystem or registry immutability, establish package
safety, or demonstrate Neo runtime parity.

## Human-owner-only authorization

Only the human owner can issue an authorization receipt. The following are
never authorization:

- a ResolvePlan;
- a plan hash;
- a validation report;
- a consumption or replay marker;
- a template or example;
- a fixture;
- a candidate-metadata report;
- a project document;
- an assistant or agent statement; or
- a receipt created or modified by tooling.

The tool must not generate an authorization receipt. It may only read a receipt
that the human owner placed at the exact approved Evidence path:

```text
Evidence/preflight/reports/authorizations/<human-owner-file>.json
```

Schema validation cannot establish provenance. A structurally valid receipt
that says `issued_by: human-owner` is still a self-asserted JSON document; the
current design has no cryptographic owner signature, trusted public key, or
equivalent proof of human origin. For that reason a receipt-validation result
may be `PASS`, but it must always state:

```text
execution_authorized: false
```

No validation-only result may be promoted to execution authority by a caller,
tool, assistant, plan, report, or document.

## ResolvePlan workflow

ResolvePlan is a deterministic, no-network review step:

1. It reads only workspace-approved source, policy, repository, interpreter,
   and installed-metadata evidence.
2. It evaluates the current local dependency state.
3. It records that dependency result, including the current `NO_GO`, instead
   of hiding or overriding it.
4. It binds the reviewed inputs and proposed scope into a plan digest.
5. It may return `plan_status: PLAN_READY` and
   `authorization_status: NETWORK_NOT_AUTHORIZED`.
6. It records `authorization_eligible` and a canonical
   `authorization_blockers` list. Dirty or incomplete Git evidence produces
   `authorization_eligible: false` with
   `RESOLVE_PLAN_DIRTY_WORKTREE`.
7. Until the owner supplies one exact bounded candidate form, it records
   `candidate_scope_status: OWNER_INPUT_REQUIRED` and invents no range or
   candidate version.

`PLAN_READY` means that a stable review artifact was produced.
`NETWORK_NOT_AUTHORIZED` means exactly that no network operation is authorized.
Neither status is a launch decision or permission to execute Resolve.
`PLAN_READY` does not mean that a plan is eligible for later owner
authorization. Only a clean, complete repository binding with exact branch,
HEAD, and Neo parity may set `authorization_eligible: true`.
`OWNER_INPUT_REQUIRED` means the plan is incomplete for receipt matching; a
receipt cannot validate against that default plan.

The launch contract and effective decision remain `NO_GO`, with exit code 2.
The exact eight-file review bundle is:

```text
Evidence/preflight/resolve-plans/<PLAN_ID>/
|-- resolve-plan.json
|-- resolve-plan.md
|-- authorization-request.json
|-- authorization-request.md
|-- dependency-inputs.json
|-- source-bindings.json
|-- privacy-review.md
`-- tool-version.txt
```

The two `authorization-request` files are non-authorizing templates. Their
machine-readable view must retain `issued_by: NOT_AUTHORIZED` and
`owner_statement: NOT AUTHORIZATION`. Renaming, moving, or editing a generated
template cannot make it human-owner-issued.
`privacy-review.md` is a review checklist and local record, not proof that raw
Evidence is safe to publish and not owner authorization.

### Development artifact disposition

ResolvePlan
`sha256:54dc3a1ab627b55e768b06f0e707750dead9367f2070d216248e9d9f8df339cc`
was produced from an uncommitted repository state and is a
**DEVELOPMENT ARTIFACT — NOT ELIGIBLE FOR AUTHORIZATION**. `PLAN_READY`
records only that its review bundle was created. Cleaning the repository later
cannot retroactively make that plan eligible; it remains development evidence
only. This digest is documentation, not a runtime special case.

The owner-gated transfer is therefore:

1. tooling emits the review bundle with no network authority;
2. the human owner reviews the plan, dependency inputs, source bindings,
   policy, paths, host scope, privacy review, and candidate-scope status;
3. if candidate input is required, the human owner supplies one exact allowed
   candidate form and tooling creates a newly hashed review bundle;
4. the human owner independently decides whether to create a separate v1
   receipt at the exact authorization path; and
5. tooling may validate that receipt without changing it, consuming it, or
   executing Resolve.

A plan does not select a replacement, change requirements, contact an index,
create an authorization receipt, invoke pip, or alter the application. The
human owner must review the complete plan before independently deciding
whether to issue any receipt.

## Plan hashing

The ResolvePlan SHA-256 binds the exact reviewed state, including:

- repository `HEAD`;
- exact active branch, local and upstream Neo references, and Neo parity;
- the complete Git-state result, including tracked modifications, staged
  state, untracked non-ignored files, index/HEAD agreement, repository-local
  ignore digest, and completion of the bounded consistency recheck;
- `authorization_eligible` and the exact blocker list;
- the dependency decision and candidate scope;
- repository and launcher requirements;
- relevant launcher and preflight source hashes;
- the current filesystem-policy binding;
- the workspace interpreter, pip, and packaging identities represented by the
  plan;
- approved host, cache, temp, and report scope;
- the proposed operation set;
- marker-environment assumptions;
- process arguments and environment shape.

Any change to a bound value requires a new plan and a new human-owner review.
The plan digest is an unkeyed integrity and change-detection value. It does not
prove who created, reviewed, or approved the plan.

The workspace-only Git proof invokes no external Git executable, shell, or
command processor. It checksum-validates a SHA-1 Git index v2, derives the
index tree, compares it with the in-workspace loose HEAD commit, hashes all
tracked regular files, evaluates repository-local ignore rules, enumerates
untracked non-ignored files, binds branch and Neo references, and rereads
state inputs for drift. Raw bytes or the repository's established CRLF-to-LF
text form may match a tracked blob only for UTF-8, NUL-free content whose size
and nanosecond modification time still match the checksum-bound index entry,
and only when no repository or local-info attributes file applicable to an
indexed path exists. That prohibition includes ignored or untracked attribute
files at applicable paths. The proof rechecks their absence, rereads every
tracked byte sequence, every ignore source, the index and HEAD, and repeats
untracked enumeration before recording
`snapshot_rechecked: true`. This is a bounded consistency recheck, not an
atomic filesystem snapshot or lock: a local mutation can still occur after
the final read. Any future execution implementation must therefore revalidate
the complete bound state immediately beside process creation and use an
appropriate locking or handle-based design where atomicity is required.
Dirty tracked files, staged changes, or untracked non-ignored files are
recorded explicitly. Unsupported index, object, attribute, ignore, link, or
repository forms and changing snapshots fail closed as
`INCONCLUSIVE_GIT_STATE`; they can never produce
`authorization_eligible: true`.

## Receipt schema

The closed receipt schema is:

```text
forge-resolve-authorization/v1
```

It contains exactly these fields:

| Field | Meaning |
|---|---|
| `schema_version` | Must be `forge-resolve-authorization/v1`. |
| `authorization_id` | Human-owner-assigned identifier for one reviewed use. |
| `issued_by` | Must be `human-owner` for structural validation. |
| `issued_at` | Receipt issue time. |
| `expires_at` | Receipt expiration time. |
| `single_use` | Must be `true`. |
| `repository_head` | Exact reviewed repository `HEAD`. |
| `plan_sha256` | Exact reviewed ResolvePlan SHA-256. |
| `filesystem_policy_sha256` | Exact reviewed filesystem-policy SHA-256. |
| `approved_hosts` | Exact approved host and optional-port scope. |
| `approved_cache_root` | Exact workspace cache root. |
| `approved_temp_root` | Exact workspace temporary root. |
| `approved_report_root` | Exact workspace report root. |
| `approved_operations` | Contains only `CANDIDATE_METADATA` and, if separately reviewed, `PIP_DRY_RUN`. No other operation is valid. |
| `candidate_scope` | Exact bounded Gradio candidate scope bound to the plan. |
| `allow_install` | Must be `false`. |
| `allow_uninstall` | Must be `false`. |
| `allow_upgrade` | Must be `false`. |
| `allow_launch` | Must be `false`. |
| `allow_model_download` | Must be `false`. |
| `allow_extension_update` | Must be `false`. |
| `owner_statement` | Human-owner-supplied statement for this exact receipt. |

Unknown, missing, duplicated, mistyped, invalid, or plan-mismatched fields fail
closed.
The receipt cannot widen the plan. It can only match the exact plan, policy,
repository, paths, hosts, operations, and candidate scope already reviewed.

`candidate_scope` accepts exactly one of these forms:

```json
{
  "package": "gradio",
  "explicit_versions": ["<exact PEP 440 version>"]
}
```

```json
{
  "package": "gradio",
  "version_specifier": "<bounded PEP 440 specifier>",
  "maximum_candidate_count": 1
}
```

The first form requires 1 through 50 unique exact PEP 440 versions and permits
no range or direct URL. The second requires both lower and upper bounds and a
`maximum_candidate_count` from 1 through 50. A scope containing both forms,
neither form, a direct URL, an unbounded range, a non-Gradio package, duplicate
versions, or an invented default fails closed.

The no-network CLI accepts either owner-supplied form while building a new
plan. Placeholders below are documentation, not candidate recommendations:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py resolve-plan
  --repository-root .
  --evidence-root ../Evidence/preflight
  --explicit-version <owner-supplied-exact-version>
  --host <owner-approved-host>
  --index-url https://<owner-approved-host>/simple
  --operation CANDIDATE_METADATA
```

For a bounded range, replace `--explicit-version` with both
`--version-specifier <owner-supplied-lower-and-upper-bounded-specifier>` and
`--maximum-candidate-count <1-through-50>`.

## Receipt validation

Receipt validation is local and non-executing. It must check, at minimum:

- `authorization_eligible: true` and an empty blocker list before reading a
  receipt;
- exact schema and field set;
- `issued_by: human-owner`;
- valid issue and expiration ordering;
- current, unexpired time window;
- `single_use: true`;
- unused authorization ID;
- exact repository `HEAD` and active branch;
- exact local/upstream Neo references and parity;
- a fresh clean, complete Git-state binding;
- exact plan SHA-256;
- exact filesystem-policy SHA-256;
- exact hosts, roots, operations, and candidate scope;
- `candidate_scope_status` is no longer `OWNER_INPUT_REQUIRED`;
- workspace containment of cache, temp, and report roots;
- all six `allow_*` values are exactly `false`; and
- no field changed between validation passes.

Public denial codes include:

- `RESOLVE_AUTHORIZATION_MISSING`;
- `RESOLVE_AUTHORIZATION_NOT_OWNER_ISSUED`;
- `RESOLVE_AUTHORIZATION_EXPIRED`;
- `RESOLVE_AUTHORIZATION_REUSED`;
- `RESOLVE_AUTHORIZATION_SCHEMA_INVALID`;
- `RESOLVE_AUTHORIZATION_PLAN_MISMATCH`;
- `RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE`;
- `RESOLVE_AUTHORIZATION_HEAD_MISMATCH`;
- `RESOLVE_AUTHORIZATION_BRANCH_MISMATCH`;
- `RESOLVE_AUTHORIZATION_NEO_PARITY_MISMATCH`;
- `RESOLVE_AUTHORIZATION_POLICY_MISMATCH`;
- `RESOLVE_AUTHORIZATION_SCOPE_MISMATCH`;
- `RESOLVE_AUTHORIZATION_HOST_NOT_APPROVED`;
- `RESOLVE_AUTHORIZATION_PATH_OUTSIDE_WORKSPACE`;
- `RESOLVE_AUTHORIZATION_UNSAFE_PERMISSION`; and
- `RESOLVE_AUTHORIZATION_CHANGED_AFTER_VALIDATION`.

Plan integrity uses the stable
`RESOLVE_PLAN_SCHEMA_INVALID`, `RESOLVE_PLAN_CROSS_BINDING_MISMATCH`,
`RESOLVE_PLAN_HASH_MISMATCH`, `RESOLVE_PLAN_ID_MISMATCH`,
`RESOLVE_PLAN_ARTIFACT_CHANGED`, `RESOLVE_PLAN_ARTIFACT_SET_INVALID`, and
`RESOLVE_PLAN_DIRECTORY_ALREADY_EXISTS` codes. Current-state drift uses
`RESOLVE_REQUIREMENTS_CHANGED_AFTER_PLAN`,
`RESOLVE_LAUNCHER_SOURCE_CHANGED_AFTER_PLAN`,
`RESOLVE_TOOL_CHANGED_AFTER_PLAN`,
`RESOLVE_MARKER_ENVIRONMENT_CHANGED_AFTER_PLAN`,
`RESOLVE_PYTHON_CHANGED_AFTER_PLAN`, `RESOLVE_PIP_CHANGED_AFTER_PLAN`,
`RESOLVE_PACKAGING_CHANGED_AFTER_PLAN`, and
`RESOLVE_DEPENDENCY_INPUTS_CHANGED_AFTER_PLAN`. Candidate metadata and a
future ledger report identity use `RESOLVE_CANDIDATE_METADATA_INVALID` and
`RESOLVE_RESULT_REPORT_IDENTITY_INVALID`, respectively.

A structurally successful validation may report `PASS`, but it must still
report `execution_authorized: false`. This is a validation result, not a
cryptographic proof of owner identity and not an execution gate. Its effective
decision remains `NO_GO` with exit code 2. Missing authorization specifically
returns `RESOLVE_AUTHORIZATION_MISSING`; every other denial uses its stable
case-specific code and also remains `NO_GO`.

Validation without a receipt is an explicit, non-authorizing check:

```text
venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py
  validate-authorization
  --plan Evidence/preflight/resolve-plans/<PLAN_ID>/resolve-plan.json
```

A later human-owner-supplied receipt would add:

```text
--authorization Evidence/preflight/reports/authorizations/<human-owner-file>.json
```

Both forms retain `execution_authorized: false`; neither invokes Resolve.

## Single-use behavior

`authorization_id` is single-use by contract. A receipt that is expired,
previously used, ambiguously consumed, changed, or associated with existing
attempt Evidence fails closed.

The receipt validator itself is read-only and repeatable. The public
validation-only mode may write its sanitized JSON/Markdown result pair under
the approved report root, but it must not create a receipt, consumption
marker, subprocess, socket, or used-ID record. Only a future, separately
approved execution attempt could consume the single use.
An ineligible plan is rejected before receipt input is read and cannot create
or reach a consumption marker. No receipt can override its blocker.

Validation must not silently reset or clear single-use state. Deleting a
marker or Evidence file does not prove that an authorization was unused.
Failures do not permit an automatic retry. A later attempt requires a new
ResolvePlan, a new owner review, and a new human-owner-issued authorization ID.

No consumption marker is authorization. Any future marker must be written only
after a valid owner receipt is present and must remain an audit/replay record,
never a substitute receipt.

The reserved local ledger path is:

```text
Evidence/preflight/authorization-ledger/<sha256-of-authorization-id>.json
```

A future implementation may create that file with exclusive-create semantics
only immediately before the separately approved network operation. Its
contents are limited to the authorization ID and digest, plan digest,
validation timestamp, consumption state, and a `sha256:<64 lowercase hex>`
result-report content identity; they must not contain credentials or other
secrets.

## Workspace cache and temp controls

The only proposed mutable roots are:

```text
Evidence/preflight/cache/<run-id>
Evidence/preflight/temp/<run-id>
Evidence/preflight/reports/<run-id>
```

The receipt must match the plan's exact cache, temp, and report roots. Tooling
must reject external paths, another drive, UNC or device paths, path traversal,
`Private-Local`, links, reparse points, junctions, and unsafe hard links before
target I/O.

A future child environment would route pip cache and temporary state to these
roots, disable interactive input, keyring use, pip's version check, user site,
and bytecode writes, and use an empty Evidence-local netrc. These environment
values reduce ambient inputs; they do not provide preventive filesystem
containment for a child process.

The current minimal environment omits `SystemRoot`, `WINDIR`, and `PATH`.
Process-start viability is unresolved. Any proposed external runtime path or
environment value requires a separate, exact human-owner decision.

## Network-host policy

`approved_hosts` is an exact host and optional-port review scope. The reviewed
index must use unauthenticated HTTPS, contain no query or fragment, and match
that scope exactly. Wildcards, URL credentials, path patterns, queries,
fragments, and implicit expansion to related hosts are not approved scope.

This allowlist is not preventive network enforcement. Current host inspection
is post-hoc and can assess only endpoints represented in retained evidence. It
does not prove that index discovery, metadata fetches, redirects, DNS
resolution, TLS handling, or every connection stayed within the declared
scope.

The proposed child environment sets `NO_PROXY=*` and `no_proxy=*`; therefore an
approved proxy cannot become preventive containment without a separately
reviewed change to the plan and environment. A dedicated mirror, firewall, or
equivalent control would likewise require an exact human-owner design and
approval before executable Resolve could be considered.

## Candidate metadata research

Workspace-local metadata inspection remains the default. It may describe the
current installed candidates and dependency conflict without contacting a
network.

Any future online candidate research requires a separately reviewed plan and
human-owner receipt whose `candidate_scope`, `approved_operations`, and
`approved_hosts` match exactly. `approved_operations` is limited to
`CANDIDATE_METADATA` and the separately optional `PIP_DRY_RUN`; no other
operation is valid.

Candidate scope is limited to package `gradio` and exactly one bounded form:
1 through 50 unique exact PEP 440 versions with no range or direct URL, or a
PEP 440 specifier with both lower and upper bounds and
`maximum_candidate_count` from 1 through 50. Tooling must not invent a default
range. Without owner input, the plan remains
`candidate_scope_status: OWNER_INPUT_REQUIRED` and cannot match a receipt.

Candidate data is untrusted evidence. It cannot authorize itself, choose a
replacement, or become a requirements change.

A structured candidate result should remain bound to its plan and marker
environment, retain package and dependency provenance, require archive
SHA-256 values, evaluate concrete versions under PEP 440, reject unjustified
candidates, and report direct-URL dependencies as inconclusive. Reported
download endpoints are audited only after contact.

## Optional pip dry-run

A pip dry run is a possible future evidence-gathering operation, not part of
the current executable workflow. Even with a structurally valid receipt:

- `execution_authorized` remains `false`;
- executable Resolve remains disabled;
- the child pip bootstrap remains unconditionally disabled;
- no package-network access is permitted; and
- no pip process may be started.

Enabling a dry run would require a separate human-owner-approved implementation
that resolves the future decision points below. No runnable Resolve command is
provided here.

If eventually approved, a dry run would still be limited to candidate
resolution with installation, uninstallation, upgrade, launch, model download,
and extension update all denied. A dry run may nevertheless download
substantial wheel metadata or artifacts and write cache or temporary data; it
is not side-effect-free.

## Privacy controls

Authorization receipts, raw pip reports, candidate metadata, and retained
process output are local Evidence. They must not be committed or shared before
the human owner reviews them for:

- private or user-specific paths;
- credentials and bearer values;
- URL usernames, passwords, paths, queries, and fragments;
- tokens, cookies, keys, and environment data; and
- unexpected or truncated output.

Sanitized summaries must use workspace-relative paths and must not reproduce
private URL suffixes. Sanitization and structural validation reduce exposure
but do not prove that raw Evidence is safe to publish.

## Failure behavior

Every missing, stale, unsafe, or mismatched condition fails closed. A failure
must preserve:

- `execution_authorized: false`;
- no application launch;
- no package installation, uninstallation, or upgrade;
- no model download, model loading, or generation;
- no extension update;
- no automatic retry; and
- no authority inferred from a partial plan, receipt, report, or marker.

A dirty or Git-inconclusive diagnostic plan returns
`RESOLVE_AUTHORIZATION_PLAN_NOT_ELIGIBLE` during receipt validation and cannot
enter receipt consumption.

Repository, plan, policy, scope, host, path, permission, time, or receipt drift
after validation returns the corresponding denial and requires a new
human-owner review.

## Known limitations

- Self-asserted JSON does not cryptographically prove human origin.
- The tool does not generate an authorization receipt.
- Receipt validation is deliberately non-authorizing.
- Executable Resolve and the child pip bootstrap remain disabled.
- Preventive network destination enforcement is not implemented.
- Host auditing is post-hoc and incomplete as a connection inventory.
- Preventive child-process filesystem containment is not implemented.
- A final output-target race requires a no-follow reserved-handle design or an
  equivalent control before any future execution.
- The process runner does not provide Windows Job Object or equivalent
  descendant-process containment.
- The workspace venv may rely on an external base runtime or operating-system
  state that remains deliberately uninspected.
- The clean-state reader supports SHA-1 repositories with a checksum-valid
  Git index v2, regular tracked files, a loose HEAD commit, and the
  repository-local ignore forms accepted by the parser. Packed-only commits,
  alternates, linked worktrees, unsupported index extensions, negated or
  escaped ignore rules, submodules, filters, and other unsupported forms fail
  closed as ineligible rather than invoking Git or guessing.
- CRLF-to-LF comparison is accepted only for index-stat-bound, UTF-8,
  NUL-free tracked content when repository and local-info attributes are
  absent. The proof does not read global Git configuration or global
  attributes. Other clean/smudge filters are not interpreted and fail closed
  when bytes do not match.
- Cache and temp environment variables do not prove that all child I/O remains
  inside those directories.
- Candidate metadata and a resolver dry-run do not establish package safety or
  justify an automatic dependency change.
- Nothing in this workflow demonstrates Neo runtime parity or authorizes Forge
  or Studio launch.

## Example marked NOT AUTHORIZATION

The following deliberately invalid template cannot authorize anything:

```json
{
  "schema_version": "forge-resolve-authorization/v1",
  "authorization_id": "NOT-AUTHORIZATION",
  "issued_by": "NOT_AUTHORIZED",
  "issued_at": "NOT_AUTHORIZED",
  "expires_at": "NOT_AUTHORIZED",
  "single_use": false,
  "repository_head": "NOT_AUTHORIZED",
  "plan_sha256": "NOT_AUTHORIZED",
  "filesystem_policy_sha256": "NOT_AUTHORIZED",
  "approved_hosts": [],
  "approved_cache_root": "NOT_AUTHORIZED",
  "approved_temp_root": "NOT_AUTHORIZED",
  "approved_report_root": "NOT_AUTHORIZED",
  "approved_operations": [],
  "candidate_scope": [],
  "allow_install": false,
  "allow_uninstall": false,
  "allow_upgrade": false,
  "allow_launch": false,
  "allow_model_download": false,
  "allow_extension_update": false,
  "owner_statement": "NOT AUTHORIZATION"
}
```

The template is documentation-only. Changing its strings or booleans cannot
turn a tool-, assistant-, or project-authored file into human-owner authority.

## Exact future owner decision points

Executable Resolve must remain disabled until the human owner separately
decides every applicable item:

1. whether and how human origin will be authenticated rather than merely
   self-asserted;
2. the canonical receipt bytes, trusted key or equivalent trust anchor,
   verification implementation, rotation, revocation, and recovery policy;
3. the exact bounded Gradio candidate scope and whether the operation set is
   only `CANDIDATE_METADATA` or also includes `PIP_DRY_RUN`;
4. the exact plan SHA-256, repository `HEAD`, filesystem-policy SHA-256, and
   authorization lifetime;
5. the exact index, hosts, optional ports, redirect behavior, DNS assumptions,
   and TLS policy;
6. the preventive network mechanism, such as an approved mirror, firewall, or
   equivalent boundary;
7. the exact cache, temp, report, and receipt paths;
8. preventive child-filesystem containment and a race-safe final report
   handle;
9. direct-child and descendant-process timeout and termination controls;
10. any exact external runtime path or environment value needed to start the
    workspace interpreter;
11. privacy review, Evidence retention, truncation, and cleanup policy;
12. whether one optional pip dry run may occur;
13. the response to any failed, partial, timed-out, or ambiguous attempt;
14. the tests and evidence required before removing the executable Resolve
    gate; and
15. the separate tests and evidence required before removing the child
    bootstrap gate.

None of these decisions has been delegated to tooling by this document.
