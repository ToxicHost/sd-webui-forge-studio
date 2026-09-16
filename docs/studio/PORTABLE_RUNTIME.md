# Studio portable runtime

This Windows x64/NVIDIA profile includes private CPython 3.13.15 and the 95
libraries pinned in `packaging/portable/windows-x64.lock.json`. Torch is
2.11.0+cu130 and TorchVision is 0.26.0+cu130. The Studio functionality and six
auxiliary model assets match the existing tester profile. Feature limitations
remain as described in [TESTER_FEATURE_STATUS.md](TESTER_FEATURE_STATUS.md).

## Run and verify

Extract the complete portable ZIP to a short writable folder and run
Start-Studio.bat. It invokes app/runtime/python.exe with an isolated import path;
it does not search for a system Python, create a venv or run pip. GitPython uses
its optional-Git import mode; users do not need Git for this packaged workflow.
Git operations remain unavailable when the executable is absent. Keep the
configuration, Studio-State and any user models/artwork when moving/updating.
Use a new extraction for updates and keep the old folder for rollback.

Check-Installation.bat checks interpreter/library versions and every recorded
runtime file hash. It reports damage without modifying user files. It is an
integrity check, not a malware scanner. A trusted release hash is needed to
establish which release was downloaded.

The package includes Python's LICENSE.txt and each library's original notices
and package data. app/SBOM.cdx.json identifies components; app/portable-runtime.json
records the exact input archives and hashes; app/runtime-files.json records the
prepared runtime's file hashes. Models retain the provenance described in
[BUNDLED_MODEL_ASSETS.md](BUNDLED_MODEL_ASSETS.md). Its Remacri license provenance
gap remains unresolved; this build does not supply a new license determination.

## Known security review limits

The unchanged dependency baseline has known-advisory matches for accelerate,
diffusers, diskcache, GitPython, h11, protobuf, setuptools, starlette, torch and
transformers. CVE identifiers are retained in the lock. No blanket security
approval is claimed; matching a version does not prove Studio exposes the issue.
This candidate preserves the existing runtime versions for functional comparison.
Full applicability review and any necessary dependency upgrades remain release
work, with regression testing required before changing the inference stack.

Confirmed boundaries: Studio's presentation server uses Python ThreadingHTTPServer,
not h11/Starlette; portable startup does not run package installers or Git operations.
These facts narrow specific network-parser/build-tool concerns but do not establish
that all transitive uses are safe. The private runtime has the ordinary user's
permissions and is not an operating-system sandbox. Use trusted model files and
keep the server local. Updating installed system Python does not update this copy;
runtime fixes are delivered as a new Studio package.

DiskCache is used by modules/cache.py for local metadata caching. An attacker
who can modify those cache files may exploit its pickle deserialization. Keep
application/cache folders under your control and do not import untrusted cache
files. This is a retained baseline risk, not an issue resolved by bundling Python.
The reviewed model-loader branches use bundled local configs and component
classes rather than DiffusionPipeline.from_pretrained; other dynamic model paths
remain unreviewed.

## Build from source

Use Windows x64, Python 3.13 and pip 26.1.2 for the build. From the app checkout:

```powershell
python scripts/build_portable.py --download
```

The build also requires the six auxiliary assets listed in packaging/assets.json
at their specified app/models paths. Their provenance is documented in
BUNDLED_MODEL_ASSETS.md; --download fetches runtime libraries, not model weights.

The builder downloads hash-pinned inputs into app/tmp/portable-inputs, builds the
two source-only packages in a local isolated build environment when necessary,
installs the reviewed wheels into a fresh private runtime, verifies package versions,
and writes the portable ZIP, manifest and checksum into app/dist. Build caches and
binary outputs are ignored by Git. A completed prepared runtime is reusable only
with its matching lock and file hashes; use a new --work path after changing locks.
An interrupted incomplete runtime is preserved for inspection; choose a fresh
--work directory to retry. Supply --inputs with the exact archives to build offline.

The two generated wheels use setuptools 69.5.1, wheel 0.45.1 and SOURCE_DATE_EPOCH
315532800. They are hash checked against the recorded build. Preserve these build
tools when reproducing this lock. pip and build-only wheel are not included as
end-user package managers. Original third-party package data/native libraries are
retained; generated console wrappers, local install URL records and bytecode caches
are excluded. Dependency package versions are not upgraded during first launch.

Create the matching source archive using scripts/build_distributable.py --profile
source --out dist. Commit the build code/locks to Git; distribute large ZIPs through
the repository's Releases assets. GitHub limits each release asset to less than 2 GiB. For a larger ZIP, run:

```powershell
python scripts/split_portable_release.py dist/forge-studio-0.0.0-internal-alpha-windows-nvidia-portable.zip --out dist/github-release
```

Upload both numbered ZIP parts, Combine-Portable.bat and release-downloads.json,
plus source and release verification/notes. Users download the parts and combiner
to one folder, run the combiner, then extract the resulting ordinary ZIP.
The combiner verifies SHA-256 using Windows' built-in .NET cryptography and does
not change PowerShell execution policy. A pre-existing ZIP is never overwritten.
Host limit: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases

## Verification

The private interpreter and nine key-library imports passed. The extracted
portable candidate started with an empty PATH and deliberately invalid Python
environment variables in a folder with spaces. Its registries contained 23
samplers, 18 schedulers, six latent upscalers, four image upscalers and five
verified detectors. Configuration creation and preservation passed. A CUDA tensor
computation passed on an NVIDIA RTX 5060 Ti; that is not an image-generation test.
Fresh extraction caught an undeclared engine dependency: pytz. It is now pinned
explicitly to 2026.2, matching the working environment, so Pandas changing its
transitive dependencies cannot remove timezone support from a clean installation.
The independent package verification report and exact release checksum accompany
the built artifact. Startup success must not be read as certification of real
model generation, every GPU/driver combination or a clean Windows installation.
Those acceptance checks remain separate from packaging and fixture tests.
