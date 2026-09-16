# Docker tester setup — Linux / NVIDIA

**Experimental: ready for a tester to try, not yet Docker-validated.** This
profile targets a Linux x86_64 host, Docker Engine with Compose v2, and one
NVIDIA GPU. Configuration and native startup checks were run on Windows; the
Linux image build, GPU passthrough, model generation and restart persistence
still need your test. Existing [feature limitations](../../docs/studio/TESTER_FEATURE_STATUS.md)
also apply. ARM/Jetson, AMD, rootless Docker and Docker Desktop are outside this test.

## What is included

The image contains Studio's runtime source, Python 3.13, CUDA 13.0 PyTorch
2.11.0 / torchvision 0.26.0, current Python requirements, required OS libraries,
licenses, five Auto Detail detectors and the Remacri upscaler. The auxiliary
assets are downloaded and hash-checked during the build. Generation checkpoints,
VAE, text encoders and personal data are supplied through host directories.
No prebuilt image is published; Docker builds it from this checkout. The first
build downloads several GB and can take a while; later builds reuse layers.

The Python base is pinned by digest and direct Python requirements are pinned.
Apt packages and transitive dependencies are not fully locked. This is not a
security certification; see [dependency notes](../../docs/studio/DEPENDENCY_NOTES.md)
and [asset provenance](../../docs/studio/BUNDLED_MODEL_ASSETS.md).

## Prepare the host

Have `nvidia-smi` working on the host, install/configure the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
and use current Docker Engine and Compose v2 with
[GPU reservations](https://docs.docker.com/compose/how-tos/gpu-support/).
The CUDA 13 build needs a compatible GPU and a driver in the 580 family or newer;
the diagnostic below checks a real CUDA operation. NVIDIA documents the
[driver compatibility limits](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
No host Python or host CUDA toolkit installation is required by this image.

Run these commands as your normal Linux user in Bash. If you already cloned
this candidate, enter it and run `git pull --ff-only` instead of cloning again.

```bash
git clone --depth 1 --branch release/tester-alpha-prep https://github.com/ToxicHost/sd-webui-forge-studio.git Studio-Standalone
cd Studio-Standalone

export STUDIO_UID="$(id -u)"
export STUDIO_GID="$(id -g)"
export STUDIO_REVISION="$(git rev-parse HEAD)"
export STUDIO_DATA_DIR="$HOME/Studio-Docker/data"
export STUDIO_MODELS_DIR="$HOME/Studio-Docker/models"
mkdir -p "$STUDIO_DATA_DIR" "$STUDIO_MODELS_DIR"/{Stable-diffusion,VAE,text_encoder,Lora}

# Keep this shell open: Compose reads these variables for each command.
docker compose -f packaging/docker/compose.yaml config --quiet
docker compose -f packaging/docker/compose.yaml build
docker compose -f packaging/docker/compose.yaml run --rm studio --check
docker compose -f packaging/docker/compose.yaml run --rm studio --check-gpu
docker compose -f packaging/docker/compose.yaml up -d
docker compose -f packaging/docker/compose.yaml logs -f studio
```

Open **http://127.0.0.1:7865/studio/** on the Linux host once the log says
`STUDIO_READY`. Ctrl+C exits the log viewer; the container keeps running.
`docker compose -f packaging/docker/compose.yaml ps` shows health status.
The health check confirms the HTTP server, not that generation has succeeded.

If the Linux host is remote, run this on your browser's computer:

```bash
ssh -N -L 7865:127.0.0.1:7865 your-user@your-linux-host
```

Then open that same localhost URL. Keep both ends on the same port so Studio's
Host/Origin checks continue to match. This setup uses Linux
[host networking](https://docs.docker.com/engine/network/drivers/host/): it shares
the host's network namespace, and Studio still binds only 127.0.0.1. Do not add
`ports:`, change the bind address, or expose it as a public/multi-user service.

## Models and persistent files

| Host directory | Container location / purpose |
|---|---|
| `$STUDIO_MODELS_DIR/Stable-diffusion` | `/studio/models/Stable-diffusion`: checkpoints |
| `$STUDIO_MODELS_DIR/VAE` | `/studio/models/VAE`: VAE files |
| `$STUDIO_MODELS_DIR/text_encoder` | `/studio/models/text_encoder`: text encoders |
| `$STUDIO_MODELS_DIR/Lora` | Optional LoRAs |
| `$STUDIO_DATA_DIR/config` | Launch config and remembered model folders |
| `$STUDIO_DATA_DIR/state` | Preferences, recovery, saved defaults and Studio state |
| `$STUDIO_DATA_DIR/results` | Generated results |
| `$STUDIO_DATA_DIR/logs` | Studio log (also visible through Compose logs) |
| `$STUDIO_DATA_DIR/cache` and `home` | Dependency caches and user-level library data |

The process runs with your UID/GID, not root. Host data directories must exist
and be writable by that user. Model directories must be readable/traversable;
the entire model mount is read-only. You may set STUDIO_MODELS_DIR to an existing
model library instead; create any missing role folders yourself or change the
folder settings inside Studio. Compose will not create a missing host mount.
Linux names are case-sensitive. Model-directory symlinks pointing outside the
mount are not a way to grant access to other host directories.

**Browse shows the container filesystem.** Use `/studio/models/...`, not the
host filesystem paths. Wildcard libraries or Gallery folders you want to retain
can live under `/studio/data`; arbitrary host folders are not automatically
visible. Bundled detectors/upscaler live separately inside `/studio/app/models`.
No generation model loads until Generate is pressed.

Configuration is created once and preserved. To change the port before the first
start, `export STUDIO_PORT=7866`; after first start, stop Studio and edit `port`
in `$STUDIO_DATA_DIR/config/studio-config.json`. Reuse a stable port to retain
browser-local settings. The STUDIO_PORT variable never overwrites an existing
configuration. Keep configured results/state/library paths inside the mounted
data area if you want them to survive replacement of the container.

## Stop, update and report

```bash
docker compose -f packaging/docker/compose.yaml stop
# Back up STUDIO_DATA_DIR while stopped, and retain the previous image for rollback.
docker image tag studio-standalone:docker-tester studio-standalone:docker-tester-backup
git pull --ff-only
export STUDIO_REVISION="$(git rev-parse HEAD)"
docker compose -f packaging/docker/compose.yaml build
docker compose -f packaging/docker/compose.yaml up -d
```

Re-export the UID/GID and directory variables if using a new shell. `down` removes
the container but leaves these host bind directories in place. Stop sends
SIGTERM into Studio's existing cooperative cleanup path; Docker forces a stop
after 60 seconds if it cannot finish. Let an important job complete first.

To roll back, stop/down the container, restore a matching stopped-data backup if
needed, tag the saved image back to `studio-standalone:docker-tester`, and run
`up -d --no-build`. Cross-version data compatibility is not yet certified.

For the first test, report:

1. Linux distribution, GPU/VRAM, `nvidia-smi`, Docker and Compose versions, and
   `git rev-parse HEAD` (do not include tokens or private model paths).
2. Whether image build, config check, GPU check and HTTP health succeed.
3. Whether Settings sees the mounted model folders and the five bundled detectors.
4. One small generation with a known working model, then its saved result.
5. A saved preference/result still present after `restart` and after `down`/`up`.
6. Whether `stop` logs `stopped cooperatively`, including a separate cancellation
   test if comfortable doing one.

If a stage fails, send the first relevant error and nearby log lines after
reviewing them for private information. A CUDA error usually needs the host
driver/toolkit/GPU details; a permission error needs mount ownership and UID/GID.
An occupied port needs another stable port. The native Windows launcher remains
available independently of this experimental profile.
