# Studio Standalone

A local image-generation application built on Forge Neo, with Studio's Canvas,
model selection, queue and Gallery. This is a Windows/NVIDIA tester candidate,
with an experimental Docker setup for Linux/NVIDIA testers.

Install Git for Windows and 64-bit Python 3.13, then run:

```powershell
git clone --depth 1 --branch release/tester-alpha-prep https://github.com/ToxicHost/sd-webui-forge-studio.git Studio-Standalone
cd Studio-Standalone
.\Start-Studio.bat
```

First launch prepares a local Python environment and downloads six verified
auxiliary models. Supply your own checkpoint, VAE and text encoders, then select
their folders in Settings. Later launches reuse the installed environment/models.

To update, close Studio and run `git pull --ff-only` inside the checkout.

- [Installation, updates and file locations](docs/studio/GIT_INSTALL.md)
- [Experimental Docker setup (Linux/NVIDIA)](packaging/docker/README.md)
- [What is usable and what is not](docs/studio/TESTER_FEATURE_STATUS.md)
- [Testing, backups and problem reports](docs/studio/FRIENDS_ALPHA_TESTER_GUIDE.md)
- [Release notes](docs/studio/FRIENDS_ALPHA_RELEASE_NOTES.md)
- [Dependency review notes](docs/studio/DEPENDENCY_NOTES.md)
- [License](LICENSE), [credits](docs/studio/THIRD_PARTY_NOTICES.md) and [model provenance](docs/studio/BUNDLED_MODEL_ASSETS.md)

The launcher creates empty models/Stable-diffusion, models/VAE and
models/text_encoder folders. Generation models and personal settings are not in Git.
