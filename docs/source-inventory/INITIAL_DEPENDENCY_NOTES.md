# Initial Dependency Notes

This is a source-level inventory. It is not yet a complete dependency lock or
license audit, and no dependency was installed during Phase 0.

## Studio-declared installation behavior

| Dependency | Observed use | Source behavior |
|---|---|---|
| `imageio-ffmpeg` | Gallery video thumbnail extraction | Installed by `install.py` when absent |
| `imagehash` | Gallery perceptual-hash duplicate detection | Installed by `install.py` when absent |
| `watchdog` | Gallery filesystem auto-sync | `studio_gallery.py` may invoke pip at runtime when absent |

The runtime `watchdog` installation path should be replaced or explicitly
governed during packaging work. It was not executed during this inventory.

## Dependencies expected from Forge Neo

Studio imports packages already present in or expected from the Neo runtime,
including:

- FastAPI and Pydantic for APIs and request models;
- Pillow and NumPy for image handling;
- Gradio for host integration;
- PyTorch for generation and Workshop tensor operations;
- safetensors for model inspection and output;
- psutil for Workshop resource checks.

The Neo baseline requirements currently pin FastAPI `0.127.1`, Pillow `12.3.0`,
NumPy `2.3.5`, Pydantic `2.10.6`, psutil `6.1.1`, and safetensors `0.8.0`;
PyTorch is unpinned in `requirements.txt`. Compatibility with the Studio
snapshot has not yet been exercised.

## Optional ecosystem integrations

- ADetailer Studio fork `26.2.0-studio.1` is recommended by the Studio README,
  but its exact repository URL is still a TODO in the snapshot.
- ControlNet is detected through extension modules and disabled when absent.
- Dynamic Prompts wildcard directories are discovered from extension paths.
- Attention Couple and regional prompting use Forge/extension script hooks.
- Civitai metadata lookup is opt-in and uses hash-based network requests.

## Third-party and data-license follow-up

- Forge Neo and Studio both carry AGPL-3.0 license text.
- Playfair Display includes SIL Open Font License 1.1 text.
- `ag-psd.js` includes several bundled license notices that require a formal
  attribution inventory.
- Gallery credits TrackImage v6.8 as integrated with permission, but the
  snapshot lacks a separate license record.
- The licensing and redistribution status of bundled autocomplete CSV data
  has not been established.

## Open items

- produce a machine-readable dependency manifest with versions and hashes;
- decide whether all installation occurs before launch and supports offline
  operation;
- verify optional-extension versions and licenses;
- verify all bundled JavaScript, font, and dataset attributions;
- test the snapshot against the pinned Neo baseline in a controlled runtime.
