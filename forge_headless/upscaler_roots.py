"""Where the owner's image upscalers are, told to the engine. UP1.

THE GAP. Neo lists image upscalers (ESRGAN and the other Spandrel
architectures) from ONE folder, `cmd_opts.esrgan_models_path`, default
`models/ESRGAN` (`modules/cmd_args.py`, `modules/modelloader.py`
`load_upscalers` -> `modules/esrgan_model.py` `UpscalerESRGAN`). Studio had no
setting for it, so a tester's existing Forge upscaler folder could not be used
without copying files into Studio's own install. The Extension runs inside
Forge and uses Forge's folder.

WHAT THIS DOES. `model_roots` hands the configured upscaler roots here (no Neo
import -- a registry built in a test must not drag the engine in). `scan()`
(Neo imported, called from `neo_registries` and after a folder change) then:

- points `cmd_opts.esrgan_models_path` at the first root and runs Neo's own
  `load_upscalers()`, so Neo builds its list exactly as under Forge;
- adds every further root, and Studio's own `models/ESRGAN` (where the bundled
  upscaler lives), with Neo's own `UpscalerESRGAN` per folder. A name already
  listed is not listed twice; the first folder wins, as the order on screen.

No root configured: Neo's default folder, unchanged.

ON1b. Upscalers from the owner's library folders arrive as FILES
(`set_files`): `UpscalerESRGAN(folder)` would list every .pt/.pth/
.safetensors in a mixed folder as an upscaler. Each file becomes Neo's own
`UpscalerData(name, file, scaler, scale)` on an ESRGAN upscaler, named and
scaled as `UpscalerESRGAN.__init__` names and scales them.
"""

from __future__ import annotations

from typing import Any

_ROOTS: tuple[str, ...] = ()
_FILES: tuple[str, ...] = ()
#: Neo's own default, captured the first time a scan runs, before Studio
#: changes it.
_DEFAULT: list[str] = []


def set_roots(roots: Any) -> None:
    """The configured upscaler roots, in order (from `model_roots`)."""

    global _ROOTS
    _ROOTS = tuple(str(r) for r in (roots or ()) if str(r))


def roots() -> tuple[str, ...]:
    return _ROOTS


def set_files(files: Any) -> None:
    """The library's upscaler files, in order (from `model_roots`)."""

    global _FILES
    _FILES = tuple(str(f) for f in (files or ()) if str(f))


def files() -> tuple[str, ...]:
    return _FILES


def _same(a: str, b: str) -> bool:
    import os

    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


#: `scan()` outcomes. RETRY: failed before Neo touched its list, so another
#: try can work. FINAL: Neo's `load_upscalers()` had already deleted the list
#: (its first statement) when it failed, which repeats on every try; the
#: previous list is put back and the caller stops retrying
#: (`neo_registries._read_upscalers` explains the loop that prevents).
SCANNED, RETRY, FINAL = "scanned", "retry", "final"


def scan() -> str:
    """Rebuild `shared.sd_upscalers` from the roots; one of the outcomes above.

    Never leaves the registry missing: the previous list is put back on any
    failure, so the menus keep what they had.
    """

    from modules import modelloader, shared

    options = getattr(shared, "cmd_opts", None)
    primary = extras = None
    if options is not None:
        if not _DEFAULT:
            _DEFAULT.append(str(getattr(options, "esrgan_models_path", "") or ""))
        default = _DEFAULT[0]
        primary = _ROOTS[0] if _ROOTS else default
        extras = [r for r in (*_ROOTS[1:], default) if r and not _same(r, primary)]

    previous = list(getattr(shared, "sd_upscalers", []) or [])
    try:
        if primary is not None:
            options.esrgan_models_path = primary
        modelloader.load_upscalers()
        if extras:
            _add_folders(shared, extras)
        if _FILES:
            _add_files(shared, _FILES, primary or "")
    except BaseException:  # noqa: BLE001 - a list of upscalers is never fatal
        deleted = not hasattr(shared, "sd_upscalers")
        shared.sd_upscalers = previous
        return FINAL if deleted else RETRY
    return SCANNED


def _add_folders(shared: Any, folders: list[str]) -> None:
    """Neo's `UpscalerESRGAN` for each further folder, merged by name."""

    import os

    from modules.esrgan_model import UpscalerESRGAN

    listed = list(shared.sd_upscalers)
    names = {str(getattr(s, "name", "")).lower() for s in listed}
    added = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for scaler in UpscalerESRGAN(folder).scalers:
            path = str(getattr(scaler, "data_path", "") or "")
            name = str(getattr(scaler, "name", ""))
            # With no model in a folder Neo lists a download placeholder;
            # Studio never downloads, so it is not offered.
            if not path or path.startswith("http") or name.lower() in names:
                continue
            names.add(name.lower())
            added.append(scaler)
    _merge(shared, listed, added)


def _add_files(shared: Any, paths: tuple[str, ...], folder: str) -> None:
    """Each library file as Neo's `UpscalerData`, on an ESRGAN upscaler
    already listed (or one built for `folder`), merged by name."""

    import os
    import re

    from modules import modelloader
    from modules.esrgan_model import UpscalerESRGAN
    from modules.upscaler import UpscalerData

    listed = list(shared.sd_upscalers)
    owner = next((s.scaler for s in listed if isinstance(getattr(s, "scaler", None), UpscalerESRGAN)), None)
    if owner is None:
        if not folder:
            return
        owner = UpscalerESRGAN(folder)
    names = {str(getattr(s, "name", "")).lower() for s in listed}
    added = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        name = modelloader.friendly_name(path)
        if name.lower() in names:
            continue
        # `UpscalerESRGAN.__init__`, exactly.
        if match := re.search(r"(\d)[xX]|[xX](\d)", name):
            scale = int(match.group(1) or match.group(2))
        else:
            scale = 4
        names.add(name.lower())
        added.append(UpscalerData(name, path, owner, scale))
    _merge(shared, listed, added)


def _merge(shared: Any, listed: list, added: list) -> None:
    if not added:
        return
    # Neo's order: None, Lanczos, Nearest, then the model upscalers by name.
    fixed = [s for s in listed if getattr(s, "data_path", None) is None]
    models = [s for s in listed if getattr(s, "data_path", None) is not None
              and not str(s.data_path).startswith("http")]
    shared.sd_upscalers = [*fixed, *sorted([*models, *added], key=lambda s: s.name.lower())]


__all__ = ("FINAL", "RETRY", "SCANNED", "files", "roots", "scan", "set_files", "set_roots")
