# Extension-to-Distribution Migration

## Goal

Turn the existing extension into a first-party product component without breaking current users or creating an unnecessary rewrite.

## Migration stages

### Stage 1 — Import unchanged

Copy the tested Studio snapshot into a built-in location and preserve:

- `/studio` route;
- current frontend asset paths;
- current workflow/session storage;
- current launch flags;
- current ADetailer integration expectations;
- current output behavior.

Only make changes required for reliable startup, versioning, duplicate detection, and packaging.

### Stage 2 — Add product bootstrap

Add a distribution bootstrap that:

- identifies Forge Studio distribution version;
- records upstream Neo base;
- chooses default landing page;
- exposes stock UI compatibility path;
- registers Studio once;
- detects an external duplicate Studio extension;
- exposes Source, License, Credits, Diagnostics, and Version.

### Stage 3 — Stabilize API contracts

Inventory every Studio endpoint and WebSocket message:

- route;
- method;
- request schema;
- response schema;
- side effects;
- Neo globals touched;
- error behavior;
- cancellation behavior;
- version compatibility.

Introduce versioned schemas before moving implementations.

### Stage 4 — Extract services

Move one concern at a time from extension scripts into Studio-owned services:

1. timing and trace IDs;
2. file catalog/list caching;
3. detector cache;
4. preview scheduling;
5. model-session policy;
6. generation coordinator.

Keep compatibility adapters so old route shapes continue to work during migration.

### Stage 5 — Optional extension compatibility package

After the distribution is stable, decide whether to maintain:

- the standalone extension;
- a thin compatibility extension;
- only the integrated distribution.

Do not promise both indefinitely until the maintenance cost is measured.

## Existing-user migration

### Detect

Look for an existing external Studio extension and its settings/workflow directories.

### Offer

Show a migration screen:

```text
Existing Forge Studio installation detected.
- Import settings and workflows
- Keep external extension disabled
- Open migration details
```

### Copy rules

- copy user-created settings/workflows;
- do not copy bundled frontend assets over the integrated version;
- do not overwrite newer settings silently;
- create a backup;
- write a migration receipt;
- preserve original files until the user confirms success.

### Duplicate route prevention

If both integrated and external Studio register `/studio`, startup should stop the duplicate component and explain how to disable/remove it. Do not allow nondeterministic route order.

## Model directory policy

Reuse the user's existing Neo model directories. The distribution does not own or delete model weights.

Installer/uninstaller rules:

- never remove `models/`, `outputs/`, user wildcards, workflows, Gallery libraries, or merge journals by default;
- distinguish program files from user data;
- provide a portable-data-path option later if needed.

## Stock UI policy

Retain stock Neo UI at a documented path or launch mode throughout alpha and beta. It is needed for:

- recovery;
- extension compatibility checks;
- same-process A/B comparison;
- proving whether a bug is Studio-specific.

Stable removal is not recommended unless users no longer need it and compatibility is proven.
