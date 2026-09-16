# Studio Standalone Portable — Windows / NVIDIA

Extract the complete ZIP to a short writable path, for example `C:\Studio`.
Double-click **Start-Studio.bat**. Studio opens in your browser and uses the
Python and libraries already included in this folder. No Python installation,
package download or administrator launch is required.

Use a supported NVIDIA GPU with an up-to-date driver. This internal alpha has
not yet been certified on a clean Windows machine or through a full generation
soak. A driver or Windows native-runtime prerequisite can still need attention.

Put your own generation models in `app/models/Stable-diffusion`, `app/models/VAE`
and `app/models/text_encoder`, then select folders/models in Studio Settings.
Five Auto Detail detectors and the Remacri upscaler are supplied. No generation
checkpoint, VAE or text encoder is included.

Keep the whole folder together. Configuration and Studio-State are created on
first launch. Back up your artwork and settings. For updates, extract a new
release beside the old one and keep the old folder for rollback.

If startup reports a damaged/missing library, run **Check-Installation.bat**.
Extract a fresh complete release to repair it; the launcher does not modify your
installed Python or download replacement packages.

Read `app/docs/studio/TESTER_FEATURE_STATUS.md` for current feature limitations.
Read `app/docs/studio/PORTABLE_RUNTIME.md` for this runtime's versions, known
advisory findings and verification limits. This is an internal testing build.
