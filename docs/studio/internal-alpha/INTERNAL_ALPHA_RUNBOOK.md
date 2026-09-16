> Historical runbook. Its profiles and explicit Load instructions are superseded.
> Use [the current tester guide](../FRIENDS_ALPHA_TESTER_GUIDE.md).

# Forge Studio — Internal Alpha Runbook

Owner-operated, Windows, one machine, loopback only. This is the minimum
packaging Internal Alpha Phase 1 declares: one entry point, one configuration
file, explicit loading, contained results and logs.

## 1. Launch

```text
cd $WORKSPACE\app
venv\Scripts\python.exe launch_studio.py --config <path-to-config.json>
```

Default configuration path: `<workspace>\studio-config.json` (next to `app\`).
The mock UI shell (no backend at all) remains available with `--mock`.

Once the server is bound, the launcher prints one machine-readable line,
flushed immediately (a piped consumer needs no `python -u`):

```text
STUDIO_READY host=127.0.0.1 port=<number>
```

The line appears only after a successful bind -- a refused configuration or
bind failure never prints it -- followed by the human-readable URL line.

## 2. Configuration

Copy `studio-config.template.json` from this directory to the workspace root
as `studio-config.json` and fill in your profile's three payload paths. The
template ships with `<REPLACE-...>` placeholders and the launcher refuses to
start while any placeholder remains, naming the roles still unfilled.

The bounded contract, validated before anything is constructed:

```text
backend               "headless" or "mock"
host                  must be 127.0.0.1 (internal alpha binds loopback only)
port                  0..65535; 0 picks an ephemeral port
result_root           a directory INSIDE the workspace; created if absent
autoload              must be false; loading is an explicit Model-panel action
onboarding            "default" (owner mode) or "suppressed" (smoke mode)
selected_profile_id   optional; pre-selects (never loads) a profile
load_access           timeout_seconds (max 600), vram_ceiling_gib (default 14)
profiles              profile_id, display_name, family, payload_references
```

First-run behavior is deterministic in both onboarding modes:

```text
default      owner mode. On a fresh browser profile the education path
             picker and the first-run preferences card each show once;
             both are dismissible and remember the choice in browser
             localStorage only. The Canvas Strip group is present
             underneath either card.
suppressed   smoke mode. The announced URL carries ?onboarding=off; both
             nonessential overlays stay away for that visit and the
             Canvas Strip group is visible on first render. Per-visit only: nothing is
             written anywhere, and opening the URL without the flag
             behaves like owner mode.
```

Every validation failure prints one plain sentence naming the key to fix.
Private payload paths live only in your local configuration file -- they are
never served, logged, projected, or stored in browser storage.

## 3. What startup does — and does not — do

```text
does        validate configuration; create result root and logs directory;
            build the application in NO_MODEL; start one loopback server
does not    scan any model directory; stat, open, or hash any payload;
            import torch; initialize CUDA; start Neo or Gradio anything
```

Loading happens only when you press **Load** in the Canvas Strip. Explicit
load access is granted per load from the selected profile's references, so
load, unload, and load again works within one session.

## 4. Using Studio's native surfaces

The floating Model and Results boxes were rejected and removed. Studio's
own surfaces own this work.

### Canvas Strip — the MODEL / SESSION group

It sits with the other generation parameters in the rail:

```text
select a profile      opens nothing; state becomes PROFILE_SELECTED
Load                  enabled when a profile is selected and load access
                      is configured; state LOADING then READY
generate              the main Generate button submits through the
                      coordinator API (POST /api/generate, one public job
                      id from QUEUED through its terminal state)
queue summary         "N active - N queued", with the full list behind
                      the group's disclosure
Cancel (queued job)   the row's Cancel button, POST /api/jobs/{id}/cancel
                      on the SAME public id; terminal CANCELLED; never
                      reaches the backend
failed row            a stable scalar error code with recovery guidance
Unload                renders UNLOADING immediately, disables both
                      lifecycle buttons, refuses a duplicate request, and
                      settles at the server's NO_MODEL or FAILED
```

### Session Strip — completed results

The strip is expanded at viewport widths above 1600 px and auto-collapses
to a 22 px rail at or below that width (a deliberate responsive rule).
Above the breakpoint its header control collapses and re-expands it;
below it, the layout owns the state and the control is hidden.

Completed results become ordinary Studio session entries, newest first:

```text
thumbnail             appears automatically when a job completes
name                  safe generated form, studio-result-000001
delivery              opaque handles only; no filesystem path anywhere
durability            entries survive an explicit unload
cancelled / failed    never receive a thumbnail
```

### Canvas — the opened image

Double-click a session result to open Studio's existing detail view, then
use **Send to Canvas** to place it. That is the same path the rest of
Studio uses; nothing separate was added.

There is no parallel frontend state machine and no second result store:
lifecycle state comes from the product, job state from the coordinator
records, and results live in the one session registry Studio already
renders. The terminal VRAM-release behaviour is unchanged.

The legacy blocking route `/studio/generate` remains for source-compatible
clients only; the lifecycle frontend never calls it and never falls back
to it.

## 5. Logs and evidence policy

```text
logs        <workspace>\logs\studio-internal-alpha.log (plus console)
results     under your configured result_root, served only through opaque
            studio-result/ handles
evidence    milestone evidence lives under <workspace>\Evidence and is never
            written by the running product
```

Logs contain lifecycle states and scalar error codes only -- no payload
paths, no prompt text.

## 6. Shutdown

`Ctrl+C` in the launcher console. The server stops accepting work, the
application shuts down (any active job is bounded by the lifecycle's
shutdown policy), and the composition releases what it owns. If a model was
loaded, prefer pressing **Unload** first; shutdown will close the session
regardless.

## 7. Known limitations (internal alpha)

```text
one Studio instance per machine (no port sharing or multi-user)
one warm model session; switching profiles live is deferred
running jobs show state, not step-by-step progress, in the Canvas Strip
active-job cancellation is not exposed (queued cancellation is)
numeric memory telemetry is captured by the live smoke, not displayed
  in the Canvas Strip
no installer; run from the repository checkout with the project venv
```

## 8. Rollback

The launcher touches nothing outside `result_root` and `logs\`. To roll back
a Studio build, use git on the repository:

```text
git -C app log --oneline -10        # find the milestone to return to
git -C app reset --hard <commit>    # the integrated HEADs are recorded in
                                    # PROJECT_STATE.md per milestone
```

Configuration files and results are yours and are never migrated or deleted
by a rollback.

## 9. Licensing and notices

See `NOTICES.md` in this directory for AGPL source obligations and the
inventory of reused Neo/Forge assets.
