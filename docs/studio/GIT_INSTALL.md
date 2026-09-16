# Install Studio from GitHub — Windows / NVIDIA

This is the source-install workflow: clone Studio, run its launcher, and update
with Git. Python libraries and model weights stay out of the repository. The first
setup still downloads several gigabytes for Torch and its GPU libraries, plus
132.6 MB for six auxiliary models; this is a one-time setup for an unchanged
environment. There is no giant portable ZIP to join or manually extract.

## First run

Install Git for Windows and 64-bit Python 3.13, including the Python launcher.
The existing bootstrap accepts Python 3.11–3.13; 3.13 is the recommended tested
version for this candidate. A supported NVIDIA GPU and driver are required.

Run these commands in the directory where you want Studio:

```powershell
git clone --depth 1 --branch release/tester-alpha-prep https://github.com/ToxicHost/sd-webui-forge-studio.git Studio-Standalone
cd Studio-Standalone
.\Start-Studio.bat
```

The launcher creates a local venv, installs the selected Torch and requirements,
and obtains missing auxiliary models from the repository's studio-assets-v1
release. Every model must match packaging/assets.json in size and SHA-256 before
it is installed. A failed/interrupted download can be retried; existing files with
different contents are preserved and reported. Existing valid assets need no network.

Then Studio opens in your browser. Supply your own checkpoint, VAE and text encoders
and choose their folders in Settings. The launcher creates empty models/Stable-diffusion,
models/VAE and models/text_encoder folders for convenience. Five Auto Detail detectors
and Remacri are prepared by setup; no generation checkpoint is downloaded.

Run Start-Studio.bat again for normal use. It reuses the environment and only
installs dependencies when the existing requirement checks report missing or
incompatible packages. It does not run git pull or update source automatically.
Model enumeration and image generation do not invoke the asset downloader.

## Update

Close Studio, back up your settings/artwork, then run inside the checkout:

```powershell
git pull --ff-only
.\Start-Studio.bat
```

Git transfers source changes, not the venv or model weights. If Git reports local
source edits or diverged history, resolve that explicitly; do not discard changes
with reset/clean commands. Normal updates do not require deleting the venv.

## Settings, files and existing installations

For a checkout named Studio-Standalone, first-run configuration is created at
../Studio-Standalone-data/studio-config.json, with durable state in that sibling
folder's Studio-State directory. The sibling folder survives Git updates and is
never committed. Existing config is preserved byte for byte by the launcher.
Default generated images are in ../Studio-Results, and logs are in ../logs,
beside the checkout. Back up the data/results folders and any separately configured
artwork/model paths. Clones in the same parent directory share these default
results/log locations; use separate parent directories for independent installs.

The clone is a new installation. It does not discover or import another Studio
installation's private configuration/models. Point Settings at existing model
folders yourself. If moving an already configured installation, update its saved
absolute paths. Avoid running two copies against the same state directory.

If Python selection is wrong, set PYTHON to the full path of a supported executable
for that terminal session, then run Start-Studio.bat. An existing incompatible venv
is preserved and reported rather than silently replaced.

## Offline preparation and verification

A maintainer can supply the six exact model filenames in one local directory:

```powershell
$env:STUDIO_ASSET_DIR = 'C:\StudioAssets'
.\Start-Studio.bat
```

This affects auxiliary model setup only; Python dependencies still require internet
or a separately prepared pip wheel cache. The same hashes are checked for local
copies. After dependencies and assets are present, normal launch is offline-capable.
Run Start-Studio.bat --check to perform setup/config validation without opening the
browser or starting a server.

## Release preparation

Publish the six files listed in packaging/assets.json as individual assets on the
repository's studio-assets-v1 release, along with their manifest and provenance.
Their names and bytes are immutable for that asset version. The source branch and
asset release must both be available before sending the clone instructions to testers.
Do not commit weights, environments, configuration or outputs to Git. Any changed
asset set needs a new asset version and reviewed hashes, rather than replacing an
existing download silently. The portable ZIP remains an optional separate profile.

The public tester branch is prepared as a current-source snapshot on the existing
public Neo base. Older local development history contained machine paths that
were removed before release; retain that history locally. Future public updates
must preserve the public branch's ancestry through reviewed commits or snapshots.
Do not merge the private development history into the public branch.

This is still an internal-alpha candidate. Feature limitations are in
[TESTER_FEATURE_STATUS.md](TESTER_FEATURE_STATUS.md), and the existing advisory and
asset-license findings remain documented in [PORTABLE_RUNTIME.md](PORTABLE_RUNTIME.md)
and [BUNDLED_MODEL_ASSETS.md](BUNDLED_MODEL_ASSETS.md). Git installation changes the
delivery method; it does not certify missing features or real-model generation.
