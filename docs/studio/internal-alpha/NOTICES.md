> Historical milestone notes. This tester package now includes auxiliary assets;
> see [their current provenance](../BUNDLED_MODEL_ASSETS.md) and retain all licenses.

# Internal Alpha — Licensing and Asset Notices

## AGPL obligations

This repository descends from AGPL-3.0-licensed work (Stable Diffusion WebUI
lineage through Forge/Neo). The internal alpha is owner-operated on one
machine and is not distributed; no conveyance obligations are triggered by
internal use. Before ANY distribution or network provision to third parties:

```text
the complete corresponding source must be offered under AGPL-3.0
license texts and copyright notices must be preserved
modifications must carry prominent notices
```

The repository's LICENSE files are authoritative; this note records the
obligation, it does not restate the license.

## Reused Neo/Forge assets in the product surface

```text
retained generation stack     backend/, modules/, modules_forge/ -- upstream
                              code, tracked against UPSTREAM_BASE with Neo
                              parity 0 0 enforced per milestone
vendored Python packages      modules_forge/packages (gguf, huggingface_guess,
                              comfy, k_diffusion) -- vendored upstream
                              packages under their own licenses
frontend                      forge_studio/frontend is the adopted canonical
                              source set (provenance pinned by SHA-256 in
                              test_s07_design_system), plus exactly one
                              Studio-authored addition:
                              studio-model-controls.js (Internal Alpha
                              Phase 1; UI-gate-fixes routed it into the
                              main Generate action)
fonts                         forge_studio/frontend/fonts under OFL
                              (OFL.txt preserved verbatim). ALL frontend
                              assets are local: the UI-gate-fixes milestone
                              removed the only external reference (a
                              font-CDN stylesheet); text falls back to
                              system faces
brand assets                  forge_studio/frontend/brand -- adopted from the
                              canonical source; branding is NOT finalized for
                              any public release
```

## What this milestone did not do

No installer, no venv modification, no bundled models or fonts, no final
branding, no third-party service, no telemetry leaving the machine.
