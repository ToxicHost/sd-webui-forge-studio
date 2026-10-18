# Install Studio on a Mac — Apple Silicon

```text
STATUS: built and tested on Windows with simulated Mac devices.
        Not yet run on a real Mac. Your first run IS the test.
```

Studio runs on Macs with Apple Silicon (M1, M2, M3, M4 or newer). It uses the
Mac's built-in GPU through Apple's Metal ("MPS"). Intel Macs are not supported:
they have no GPU Studio can use, and the launcher says so before downloading
anything.

## What you need

- A Mac with Apple Silicon and **macOS 14 (Sonoma) or newer**. macOS 13 may
  work but has not been tried, and it cannot use the faster bf16 number format.
- **16 GB of memory or more.** On a Mac the GPU shares the computer's memory,
  so close large apps (browsers with many tabs, games, video editors) while you
  generate.
- **About 10 GB of free disk space** for Python libraries and the auxiliary
  models, plus your own model files.
- **Python 3.13** from python.org. Download the "macOS 64-bit universal2
  installer" from <https://www.python.org/downloads/macos/> and run it. The
  `python3` that comes with macOS is too old (3.9). You do not need Homebrew,
  `sudo` or an administrator password beyond what the Python installer asks.
- Your own model files (checkpoint, text encoder, VAE). Studio includes none.

## Get Studio

Pick one.

**A. With Git** (recommended: updates are one command, and macOS does not mark
the files as "downloaded from the internet"). Open Terminal and run:

```bash
git clone --depth 1 --branch release/tester-alpha-prep https://github.com/ToxicHost/sd-webui-forge-studio.git ~/Studio-Standalone
```

If macOS offers to install "command line developer tools" when you type `git`,
accept: that is Apple's own installer and it provides Git.

**B. As a ZIP.** Download the ZIP of the `release/tester-alpha-prep` branch
from GitHub, double-click it to unpack, and move the folder to your home folder
as `Studio-Standalone`. Files from a browser download are "quarantined" by
macOS; see the next section.

## Start Studio

The simplest way, which works whatever macOS thinks of the download:

```bash
cd ~/Studio-Standalone
sh start-studio.sh
```

Or double-click **Start-Studio.command** in Finder. It opens a Terminal window
and runs the same script. Keep that window open: closing it stops Studio.

The first start prepares everything once. It downloads PyTorch and the other
Python libraries (several hundred MB), then seven small auxiliary models (about
170 MB) that are checked against fixed fingerprints before they are used. This
can take 10–30 minutes. Later starts take seconds and need no internet.

When it is ready, your browser opens at `http://127.0.0.1:7865` (or the next
free port). Studio only listens on your own computer.

### "Start-Studio.command can't be opened" (Gatekeeper)

Studio is not signed by an Apple-registered developer, so macOS may refuse to
open `Start-Studio.command` the first time, saying it is from an "unidentified
developer" or "could not be verified". Any one of these is fine:

1. **Use Terminal instead:** `cd ~/Studio-Standalone` then
   `sh start-studio.sh`. This does not trigger the check.
2. **Allow it once:** try to open it, then open **System Settings → Privacy &
   Security**, scroll down, and click **Open Anyway** next to the message about
   Start-Studio.command. (On macOS 14 you can also Control-click the file,
   choose **Open**, then **Open** again.)
3. **Remove the download mark from the folder** (only for a folder you got
   from this project): `xattr -dr com.apple.quarantine ~/Studio-Standalone`

Studio never asks you to turn Gatekeeper off, and you should not.

## First use

1. Open **Settings** and choose the folders that hold your checkpoints, text
   encoders and VAEs. Studio creates empty `models/Stable-diffusion`,
   `models/VAE` and `models/text_encoder` folders you can use.
2. Pick a model and press **Generate**. The first picture after a start is
   slower, because the model is loaded into memory.

## What is different on a Mac

- **Speed.** Expect a picture to take several times longer than on a recent
  NVIDIA card.
- **Same seed, different picture.** On a Mac the starting noise is made on the
  processor, as in Forge, so a seed does not reproduce a picture made on an
  NVIDIA PC.
- **fp8 model files** are converted to 16-bit while they load (Apple GPUs have
  no fp8), which uses more memory. "Scaled fp8" files cannot be converted
  safely and are refused with a message: use the fp16 or bf16 version.
- **Not available on a Mac:** the GPU selector, the "GPU weights" slider, and
  the NVIDIA-only speed options. Word heatmaps and word masks may be slower;
  if they fail, the picture is still made and saved.

## Update

Close Studio, back up your pictures and settings, then in Terminal:

```bash
cd ~/Studio-Standalone
git pull --ff-only
sh start-studio.sh
```

## Where your files are

For a checkout at `~/Studio-Standalone`, settings live beside it in
`~/Studio-Standalone-data`, pictures in `~/Studio-Results` and logs in
`~/logs`. Removing Studio means deleting those folders and the checkout.

## If something goes wrong

Run this in Terminal and read the `.txt` file it writes before sending it to
whoever gave you Studio. It contains no pictures, prompts or model files:

```bash
cd ~/Studio-Standalone
venv/bin/python scripts/build_support_bundle.py --dry-run
venv/bin/python scripts/build_support_bundle.py
```

Feature limitations are listed in
[TESTER_FEATURE_STATUS.md](TESTER_FEATURE_STATUS.md).
