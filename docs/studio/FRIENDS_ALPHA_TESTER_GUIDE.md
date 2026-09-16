# Studio tester notes

Use [GIT_INSTALL.md](GIT_INSTALL.md) to install and update this Windows/NVIDIA
candidate. Read [feature status](TESTER_FEATURE_STATUS.md) before planning work.

## First checks

Choose model folders in Settings, select a checkpoint and any required text
encoder/VAE, and generate one image. Then try a second job with the same model,
Canvas painting and masks, queue cancellation, browser refresh and an app restart.
Try optional Hires and Auto Detail separately after basic generation works.

The launcher retains --fast-fp16. This can change fine detail at the same seed;
removing that option in Start-Studio.bat opts out. No inference defaults were
changed for this delivery cleanup.

## State and backups

Configuration and durable state are beside the clone in <clone-name>-data.
Default results and logs are also beside the clone; the installation guide gives
exact paths. Keep separate backups of important images and Canvas documents.
Recovered Canvas documents do not restore undo history. Browser-local settings
may reset when the default automatic port changes; configure a stable unused
port in studio-config.json if needed.

Use Ctrl+C in the console to stop Studio. Cancel a running job first where
possible. Back up settings/artwork before updating and review saved absolute paths
if you move an installation. Arbitrary-version downgrades are not certified.

## Problem reports

From the clone directory:

```powershell
.\venv\Scripts\python.exe scripts\build_support_bundle.py --dry-run
.\venv\Scripts\python.exe scripts\build_support_bundle.py
```

The report is written locally and is not sent automatically. Inspect it before
sharing. Include the output of `git rev-parse HEAD`, steps to reproduce, model
family and relevant settings. Screenshots and logs can contain personal content.
