# Forge Studio user guide: backups, network and problem reports

Use [GIT_INSTALL.md](GIT_INSTALL.md) to install and update Studio on
Windows/NVIDIA. See [feature status](FEATURE_STATUS.md) for what works and what
doesn't.

## First checks

Add your model folders (the welcome screen asks on a fresh install; later in
Settings > Model folders), select a checkpoint and any required text
encoder/VAE, and generate one image. Then try Hires and Auto Detail separately.

Start-Studio.bat starts Studio with --fast-fp16. This can change fine detail at
the same seed; removing that option in Start-Studio.bat opts out.

## State and backups

Configuration and durable state are beside the clone in <clone-name>-data.
Default results and logs are also beside the clone; the installation guide gives
exact paths. Keep separate backups of important images and Canvas documents.
Recovered Canvas documents do not restore undo history. Browser-local settings
may reset if Studio has to use another port because its usual one is busy.

Use Ctrl+C in the console to stop Studio. Cancel a running job first where
possible. Back up settings/artwork before updating and review saved absolute paths
if you move an installation. Going back to an older version isn't supported.

## Network

Studio serves its pages to this computer only. It goes online only for:

- Civitai lookup in the Checkpoint and LoRA browsers (civitai.com), and Settings
  > About > Check for Updates (Git, to the repository you cloned), when you press
  them;
- a ControlNet preprocessor's model, downloaded the first time you use that
  preprocessor.

Nothing updates automatically. Setup downloads dependencies.

## Problem reports

Send ToxicHost a message on the site where you found Studio.

A support report helps. From the clone directory:

```powershell
.\venv\Scripts\python.exe scripts\build_support_bundle.py --dry-run
.\venv\Scripts\python.exe scripts\build_support_bundle.py
```

The report is written locally and is not sent automatically. Inspect it before
sharing. Include the output of `git rev-parse HEAD`, steps to reproduce, model
family and relevant settings. Screenshots and logs can contain personal content.
