# Studio Standalone — Git tester candidate

Distribution identifier: 0.0.0-internal-alpha. Run `git rev-parse HEAD` for the
exact installed revision. This is an alpha candidate for Windows/NVIDIA testing.

## Canvas painting update — 2026-09-30

Update with `git pull --ff-only`, then restart Studio. Only the Studio interface
and its local page server change; the generation engine, launcher, installer,
requirements and models are unchanged.

- A new painting engine (Brush V2) is now the default for the brush and eraser.
  Presets have their own materials: Pencil, Charcoal and Pastel lay strands that
  follow the paper's grain, Bristle Rake lays separate bristles, Scatter Dust
  scatters particles, and Airbrush builds while held still and shows the build
  as it happens. Flat tips turn cleanly through corners.
- Hard Ink, Fine Liner and Sketch Light were removed from the preset list:
  Basic Round covers the first two, and Pencil at a lower Density the third.
- The Flow control is gone: Opacity is the only strength setting, and saved
  tool settings are converted.
- Density is on the brush's context bar. Pencil starts at 70%; use 100% for a
  plain line.
- Settings > Canvas > Mouse press: a mouse paints at medium pressure by default,
  so paper grain shows. Switch it off for full pressure.
- Symmetry, Taper In and each preset's pressure, speed and tilt response work in
  the new engine. A mirrored calligraphy or chisel stroke is a true mirror image.
- Aliased brushes and Pixel Perfect, touch input, and Inpaint Mask or region
  painting still use the previous engine.
- Inpaint has a dedicated Mask tool with its own settings and history. Undo or
  redo pressed mid-stroke cancels only that stroke, and crash recovery waits
  until a held stroke ends.

The presets were reviewed with a mouse. Pen pressure and tilt have automated
checks only; there is no tablet certification yet. Keep backups of artwork.

## Git delivery cleanup — 2026-09-15

The tester branch now contains runtime source, installation helpers, user guides
and licenses. Project plans, agent instructions, Docker/deployment files, tests,
legacy WebUI entry points and release-build tools are removed from this branch.
The retained engine, Studio frontend, launchers, installers and requirements are
unchanged. Existing installations can receive this cleanup with `git pull --ff-only`.

Start-Studio.bat creates or repairs the local Python environment, verifies the
six auxiliary models, and opens Studio. Python 3.11–3.13 is accepted; use 3.13 for
this candidate. Existing settings are preserved. The launcher creates empty
models/Stable-diffusion, models/VAE and models/text_encoder folders; users supply
their own generation models and choose model directories in Settings.

The five Auto Detail detectors and Remacri are downloaded from studio-assets-v1
only when missing, with pinned size/hash verification. This cleanup does not
change that release or any generation, sampling or painting behavior.

See [installation](GIT_INSTALL.md), [tester notes](FRIENDS_ALPHA_TESTER_GUIDE.md)
and [feature status](TESTER_FEATURE_STATUS.md). ControlNet, Live generation,
Workshop and regional inference are unavailable in this Standalone candidate.
Updates are manual. Real generation, interactive browser/tablet acceptance and
cross-platform certification remain separate from these installation checks.
