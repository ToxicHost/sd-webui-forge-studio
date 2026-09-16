# Dependency review notes

Studio setup installs the direct versions in requirements.txt and selects its
Torch build through the existing platform selector. This cleanup does not change
those versions, model weights, inference code or application features.

The dependency baseline reviewed during release preparation had known-advisory
matches for accelerate, diffusers, diskcache, GitPython, h11, protobuf, setuptools,
starlette, torch and transformers. A matching version does not establish that
Studio exposes a particular issue. Applicability review and dependency upgrades
remain open. The [recorded runtime lock](https://github.com/ToxicHost/sd-webui-forge-studio/blob/7543faf5cf11cabbb8648afecdbe2c1bf76cb4b4/packaging/portable/windows-x64.lock.json)
contains the reviewed versions and advisory identifiers. Git setup can resolve
different transitive versions; this record is not a scan of an individual install.

Studio's presentation server uses Python ThreadingHTTPServer. The app runs with
the user's ordinary permissions. Keep it local and use trusted model files.
DiskCache is used for local metadata: do not import untrusted cache files or allow
untrusted users to modify the application's cache directories.

Model provenance and the existing Remacri license-provenance gap are recorded in
[BUNDLED_MODEL_ASSETS.md](BUNDLED_MODEL_ASSETS.md). Current feature and verification
limits are in [TESTER_FEATURE_STATUS.md](TESTER_FEATURE_STATUS.md).
