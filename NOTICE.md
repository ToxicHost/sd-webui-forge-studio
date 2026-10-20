# Forge Studio — notice

Forge Studio is free software, licensed under the
GNU Affero General Public License version 3 or (at your option) any later
version (see `LICENSE`). SPDX-License-Identifier: AGPL-3.0-or-later.

Copyright (c) the Forge Studio authors: ToxicHost and Moritz.

## A modified version of Forge Neo

Forge Studio is built on **Forge Neo** (Haoming02/sd-webui-forge-classic,
branch `neo`, commit `97ff3a4024be2f0d5316f16e868e5ef822768872`,
2026-07-23), which builds on **Stable Diffusion WebUI Forge** (lllyasviel)
and **Stable Diffusion WebUI** (AUTOMATIC1111). Their copyright lines are kept
in `LICENSE`.

This is a **modified version**, as AGPL-3.0 section 5(a) requires us to say:

- Studio adds its own code in `forge_studio/`, `forge_headless/` and
  `studio_plugins/`.
- Studio modifies files that came from Forge Neo in `modules/`,
  `modules_forge/` and `backend/`. Every modified or added Neo-owned file, why
  it was changed and when, is listed in `docs/14_PATCH_INVENTORY.md`, which
  ships with this package. The exact upstream revision is in `UPSTREAM_BASE`.
- Last modified: 2026-10-20.

## Source

The complete corresponding source is published at
https://github.com/ToxicHost/sd-webui-forge-studio. Studio runs on your own
computer; it is not offered as a network service.

## Third-party components

Code, data and assets from other projects keep their own licences and
copyright notices. They are listed in `docs/studio/THIRD_PARTY_NOTICES.md`.

Model weights are not part of Forge Studio. You choose and download your own
models; each comes with its own licence, which applies to your use of it.
