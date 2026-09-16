# Studio Standalone — license and credits

Studio Standalone derives from Forge Studio and the Stable Diffusion WebUI /
Forge Neo lineage. See [LICENSE](../../LICENSE) for the repository's AGPL-3.0
terms and [UPSTREAM_BASE](../../UPSTREAM_BASE) for pinned source identities.

The tester archive includes the runtime source. The accompanying -source.zip
contains the complete tracked checkout, build scripts, tests, patch inventory
and development documentation for the same commit. Its manifest identifies the
revision. Distribute that source archive alongside this candidate.

Standalone modifications provide the local Studio interface, headless backend
integration and installation/packaging described in the [release notes](FRIENDS_ALPHA_RELEASE_NOTES.md).
The source archive includes docs/14_PATCH_INVENTORY.md for inherited core edits.

Original copyright and license notices remain with the engine, vendored
packages, built-in extensions and frontend assets. Those components retain
their own license terms. Studio's Credits panel supplies product attribution.

Five Auto Detail detector weights are credited to Bingsu's ADetailer model
repository; their recorded license is Apache-2.0. Remacri is credited to
FoolhardyVEVO. Its canonical license has not been independently verified; the
owner previously approved inclusion with attribution. See [bundled assets](BUNDLED_MODEL_ASSETS.md)
for provenance, exact hashes and the recorded status of these six assets.
