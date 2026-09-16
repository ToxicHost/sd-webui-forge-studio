# Studio Standalone — tester guide

For the recommended Git clone/pull installation, follow [GIT_INSTALL.md](GIT_INSTALL.md).
The installation and file paths below describe the original source/venv ZIP.
The optional self-contained portable profile has separate [instructions](PORTABLE_RUNTIME.md).
The testing notes and feature limitations apply to all three delivery methods.

This is an unfinished Windows/NVIDIA tester candidate. Its distribution version
is 0.0.0-internal-alpha; the adjacent manifest identifies the exact source commit.
Read [feature status](TESTER_FEATURE_STATUS.md) before planning work around it.

## What is supplied

The application source, Studio interface, local server, Forge Neo engine code,
Windows launchers, setup script, five face/hand/person Auto Detail detectors,
Remacri upscaler, licenses and diagnostics tool. The detailed list and hashes
are in app/packaging/assets.json and the adjacent release manifest.
The separate -source.zip preserves development files and is not needed to run
Studio. The tester app folder contains runtime source and user documentation.

Python, GPU drivers, checkpoints, text encoders, VAEs, LoRAs and embeddings are
not supplied. The app builds its own Python environment on first launch by
downloading dependencies. This is not an offline installer.

The ZIP also supplies three empty folders for your own generation models:

- app/models/Stable-diffusion — checkpoints
- app/models/VAE — VAEs
- app/models/text_encoder — text encoders

You can put models there and select those directories in Settings, or select
existing model folders elsewhere. The supplied folders contain no placeholder
files or generation-model weights.

## Install and start

1. Extract the ZIP into a writable folder. Keep the whole extracted directory
   together. Use a short local path: Windows may refuse deeply nested upstream
   files in a long extraction path. Prefer a folder that is not being synchronized.
2. Install Python 3.13 with its Windows launcher if no supported Python is
   available. The bootstrap admits 3.11-3.13; this candidate's local tests used 3.13.5. Other versions have not
   been certified. Use an NVIDIA GPU with a driver suitable for the selected
   Torch/CUDA build; the default setup selects Torch 2.11.0+cu130.
3. Double-click Start-Studio.bat. On first launch it creates app/venv and
   installs requirements. Later launches check for incomplete dependencies.
   Leave the console open; a setup error is printed there.
4. Studio creates a clean studio-config.json and opens the browser on an
   available local port. In Settings, choose your model folders. Select a
   checkpoint and, when needed by that checkpoint, its text encoder and VAE.
5. Press Generate. Model loading occurs as part of generation. The first job
   after a model change will take longer. Configure optional Hires/Auto Detail
   only after a basic image works.

An existing Studio environment is reused. For first setup, the launcher can
use python on PATH or the Windows py launcher. If the initial interpreter is
unsupported (such as 3.10), setup asks py for installed 3.13, then 3.12, then 3.11.
It does not change your system's default Python or downgrade dependencies.

An explicit PYTHON environment variable overrides automatic selection. Set it
to a supported executable, or clear it to let Studio select one. If only Python
3.10 is installed, install Python 3.13 with its launcher and start again.
An incompatible existing app/venv is preserved; use a fresh extraction to create
a new environment. If setup fails, keep the error text and rerun the launcher
after correcting the cause. Do not copy another app's virtual environment.

The preserved launcher default includes --fast-fp16. It can change fine image
detail at the same seed. Removing that option in Start-Studio.bat opts out.
This preparation did not change that existing default.

The first-run configuration chooses an available port automatically. A changed
port is a new browser origin, so theme, panel sizes, tool settings and tour
progress stored in the browser may reset between launches. Set a stable unused
port in studio-config.json if you need those settings to persist. Server-stored
documents and preferences remain in Studio-State.

## Stop, files and backups

Use Ctrl+C in the console to stop Studio. Cancel a running job first where
possible. Your portable state is in Studio-State, generated results are in
Studio-Results, configuration is studio-config.json, and logs are in logs.
Keep backups of finished images and important Canvas work.

Recovered Canvas documents do not restore undo history. Selection and zoom
persistence have historical limitations; do not rely on them for recovery.
A successful service test does not establish recovery under every browser,
disk failure or very large document.

## Update or roll back

Stop Studio and back up studio-config.json, Studio-State and Studio-Results.
Extract a new candidate into a separate folder. Retain the previous folder.
Copy configuration/state/results only after making that backup, and check
absolute folder paths in Settings. A copied configuration may still point at
the previous Studio-State location; adjust it deliberately if moving the data.

Do not overwrite an existing environment with a different candidate's venv.
Let its launcher build its own. To roll back, stop the new candidate and use
the retained prior folder with the corresponding data backup. Automated
migration/downgrade across arbitrary builds has not been certified.

## What to test

Start with model selection and one generated image. Try a second job with the
same model, Canvas painting and masks, queued-job cancellation, a refresh,
and a restart. Then try optional Hires and Auto Detail separately.
Keep a record of the model family, settings, expected result and actual result.

## Report a problem

From the extracted directory, run:

    app\venv\Scripts\python.exe app\scripts\build_support_bundle.py --dry-run
    app\venv\Scripts\python.exe app\scripts\build_support_bundle.py

The tool writes a text report and ZIP in dist; it does not send them.
Inspect the text report before sharing. Include the source commit from the
manifest and concise steps to reproduce the issue. Screenshots and logs may
contain information you choose not to share.

Studio binds to the local machine. There is no hosted account service or
automatic updater in this candidate. Setup downloads dependencies; claims about
normal runtime network behavior must not be read as promises of offline setup.
