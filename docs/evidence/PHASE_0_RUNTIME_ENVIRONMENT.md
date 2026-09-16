# Phase 0 Runtime Environment

## Evidence identity

- Capture date: `2026-07-23`
- Capture window: `16:33:43-04:00` through `16:35:50-04:00`
- Local timezone: Eastern Time (`UTC-04:00` during capture)
- Repository commit: `6db89619f5db2c2f97eee097176c09edfb947fdb`
- Active branch: `docs/phase0-complete-baseline`
- Neo baseline tag: `neo-baseline-2026-07-23`
- Local evidence: `../Evidence/phase-0/`
- Application launched: no
- Models loaded: no
- Dependencies installed or upgraded: no

## Host summary

- Operating system: Microsoft Windows 11 Home
- Version/build: `10.0.26200` / `26200`
- Architecture: 64-bit
- PowerShell: Windows PowerShell `5.1.26100.8875`, Desktop edition
- CPU: Intel Core i7-14700F
- CPU topology: 20 CPU cores, 28 logical processors
- Total visible system memory: 33,385,968 KiB (approximately 31.8 GiB)
- Git: `2.50.0.windows.1`

PowerShell 5.1 does not populate the `Platform` and `OS` keys exposed by newer
PowerShell editions. The targeted Windows OS query supplied those facts.

## GPU summary

- `nvidia-smi` available: yes
- GPU: NVIDIA GeForce RTX 5060 Ti
- Driver: `610.74`
- nvidia-smi-reported CUDA compatibility: `13.3`
- VRAM: 16,311 MiB (approximately 15.9 GiB)

No GPU serial number, UUID, or unrelated process list was retained.

## Python summary

- Selected executable: `<USER>\miniconda3\python.exe`
- Python: `3.13.5`, Anaconda distribution, 64-bit AMD64
- Active virtual environment: no
- Repository virtual-environment Python present: no
- pip: `25.1`

The selected interpreter is the base Miniconda environment. It is not a
repository-specific Forge environment.

## PyTorch summary

- PyTorch: `2.8.0+cpu`
- CUDA build: none
- CUDA available: no
- Visible CUDA device count: `0`
- Selected device: none

This result describes the currently selected shell Python only. It does not
show that the NVIDIA GPU is unavailable to the host; `nvidia-smi` detects the
GPU normally. A CUDA-capable Forge Python environment remains unverified.

## Repository summary

- Origin: `https://github.com/ToxicHost/sd-webui-forge-studio.git`
- Upstream: `https://github.com/Haoming02/sd-webui-forge-classic.git`
- Current product commit:
  `6db89619f5db2c2f97eee097176c09edfb947fdb`
- Merged foundation commit:
  `06579943ccafa1471f43b90ed7ca9842644b3896`
- Neo baseline:
  `97ff3a4024be2f0d5316f16e868e5ef822768872`
- `develop...HEAD`: `0 0`
- `neo...upstream/neo`: `0 0`
- Working tree before evidence documentation: clean

## Launch surface

Existing launch entry points were inspected but not executed:

- `webui-user.bat` configures Windows user arguments and calls `webui.bat`;
- `webui.bat` selects Python, creates or activates `venv`, and invokes
  `launch.py`;
- `webui-user.sh` and `webui.sh` provide the Unix launch path;
- `launch.py` performs version and environment preparation before calling the
  Web UI or API-only entry point;
- `webui.py` contains the Web UI and API-only runtime entry functions.

Launching can create a virtual environment, install or upgrade dependencies,
run extension installers, create configuration files, and access package
networks. None of those behaviors occurred during Stage A.

## Privacy review

- User-profile prefixes were replaced with `<USER>`.
- Absolute workspace paths are represented as `<WORKSPACE>` or generic
  relative paths.
- No access token, API key, password, cookie, credential, authorization value,
  model filename, prompt, generated image, GPU serial number, or UUID was
  retained.
- No full environment-variable or unrelated process-list capture was taken.
- Local evidence remains outside Git under
  `../Evidence/phase-0/`.

## Limitations

- The intended Forge runtime environment is not active and no repository
  virtual environment exists.
- A CUDA-enabled PyTorch build for Forge has not been verified.
- Storage-device details were not captured.
- Active launch arguments and runtime environment variables were not captured
  because the application was not started.
- Attention backend, memory mode, model families, extensions, and runtime
  dependency state remain unverified.
- Startup, generation, timing, and memory behavior were not tested in Stage A.
