# third_party

Third-party Python packages Studio ships as source, because they are not on
PyPI and Studio's dependency preflight refuses direct-URL requirements
(`scripts/preflight/resolver.py`: the source of a URL requirement cannot be
proven). Each folder is copied verbatim from the wheel named below, its
licence beside it. `forge_headless/controlnet_port.py` puts this folder on
`sys.path` when it registers Neo's ControlNet preprocessors (CN1). It sits
outside `forge_headless/` because that package must import with no side
effects (`test_headless_forge_boundary`), and this is third-party model code
that imports torch at module scope; nothing imports it as part of Studio.

| Package | Used by | Wheel (as Forge's `forge_legacy_preprocessors/install.py` installs it) | sha256 | Licence |
|---|---|---|---|---|
| `depth_anything_v2` | Neo's `depth_anything_v2` preprocessor | `depth_anything_v2-2024.7.1.0-py2.py3-none-any.whl`, github.com/MackinationsAi/UDAV2-ControlNet release v1.0.0 (code from DepthAnything/Depth-Anything-V2) | `6848128867d1f7c7519d88df0f88bfab89100dc5225259c4d7cb90325c308c9f` | Apache-2.0 |

The model WEIGHTS are not here. Neo downloads them on first use
(`depth_anything_v2_vitl.safetensors`, CC BY-NC 4.0 like every Depth
Anything V2 Large release).

Not vendored: Forge's `depth_anything` (V1) wheel. Its model code builds
DINOv2 with `torch.hub.load("facebookresearch/dinov2")`, which downloads that
GitHub repository and runs it, so Studio withholds that preprocessor
(`controlnet_port.WITHHELD`); V2 does the same job with DINOv2 included.

Remove a folder once its package is installable from PyPI or upstream Neo
stops needing it.
