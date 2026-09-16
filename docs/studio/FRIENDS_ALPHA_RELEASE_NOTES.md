# Studio Standalone — tester preparation, 2026-09-13

Distribution identifier: 0.0.0-internal-alpha. See the package manifest for the
exact source commit. This is a private tester candidate, not a certified release.

The repository now records the current Standalone Canvas changes separately
from installation/packaging cleanup. This cleanup does not modify generation,
sampling or painting behavior.

Changes for a new installation:

- Windows release launchers are tracked in Git and included at the ZIP root.
- First run creates the current model-roots configuration and lets users choose
  directories in Settings, without probing the owner's private model folders.
- Existing configuration is preserved; partial dependency setup is checked again.
- Auto Detail's ultralytics dependency is explicitly pinned to 8.3.119.
- The six documented detector/upscaler assets are included only after size/hash
  verification. Other local weights and personal state are excluded.
- The manifest records every packaged file and its hash.

Read [the tester guide](FRIENDS_ALPHA_TESTER_GUIDE.md) and
[feature status](TESTER_FEATURE_STATUS.md). ControlNet, Live generation, Workshop,
regional inference and automatic updates are not supported in this candidate.
GPU generation and cross-platform certification remain unverified in this pass.

The tester ZIP now omits project plans, agent instructions, tests, Docker files,
legacy WebUI launchers and release tooling. These remain in Git and in a separate
source ZIP. Engine/Studio source, setup/diagnostics, user documentation and
notices remain. The diagnostics privacy checker is a shared utility, so support
reports do not require the test suite. Generation and painting code is unchanged.

The tester ZIP supplies empty Stable-diffusion, VAE and text_encoder folders
under app/models. They are explicit directory entries, with no placeholder files.
Select these folders in Settings if you use them for your own generation models.

First-run setup now uses the Windows Python launcher to locate an installed
3.13/3.12/3.11 when the default interpreter is unsupported. Explicit PYTHON
overrides and existing Studio environments are respected. Dependency versions
are unchanged; NumPy 2.3.5 requires Python >=3.11. A machine with only 3.10 still
needs a supported Python installed.
