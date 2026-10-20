"""Drive Neo's ControlNet from a headless job. CN1.

THE GAP. Studio's panel has carried the Extension's ControlNet units since the
port, hidden, because nothing behind it worked: the model and preprocessor
lists answered "None", the request had no group for units, and the live port
installs no Neo scripts, so the engine's ControlNet never ran. Review:
`Evidence/source-review/CN1-controlnet.md`.

NEO DOES THE WORK. The Extension drives Neo's own `ControlNetForForgeOfficial`
(`sd_forge_controlnet/scripts/controlnet.py`) by writing units into its
script args. Studio drives the same class through a runner on `p.scripts`, the
way `soft_inpainting_bridge` drives Soft Inpainting: Neo preprocesses, resizes,
loads the model, weights the control and patches the model. This file decides
WHEN that runs and with WHICH units, and nothing else.

REGISTRATION. Neo's preprocessors and control-model types (ControlNet,
Control-LoRA, T2I-Adapter, Control-LLLite incl. Anima, IP-Adapter) register as
an import side effect of extension scripts that Forge's `load_scripts()`
imports. Studio never calls that, so `arm()` imports the same files once, in
Forge's order, with their folders on `sys.path` -- and leaves them there, as
`load_scripts()` does (`modules/scripts.py:520`), because annotators import
their own code lazily, the first time they run. Appended rather than
prepended, so nothing in them can shadow a Studio module.

FAMILIES. Neo loads UNet ControlNets and Control-LLLite: SD 1.5, SDXL, and
Anima through kohya's Anima-LLLite. Any other loaded model is refused by name
before sampling rather than handed a control it would ignore.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

_APP = Path(__file__).resolve().parents[1]
_BUILTIN = _APP / "extensions-builtin"
#: A package Forge installs from a GitHub wheel and Studio ships as source
#: (`third_party/README.md`): `depth_anything_v2`.
_VENDOR = _APP / "third_party"

#: The extension scripts that register preprocessors and control-model types,
#: and the ControlNet script itself, in Forge's load order (extensions by
#: folder name). `sd_forge_controlnet` is required; any other that fails to
#: import is skipped and named, as Forge reports and continues.
REGISTRARS: tuple[tuple[str, str], ...] = (
    ("forge_legacy_preprocessors", "legacy_preprocessors.py"),
    ("forge_preprocessor_inpaint", "preprocessor_inpaint.py"),
    ("forge_preprocessor_reference", "forge_reference.py"),
    ("forge_preprocessor_tile", "preprocessor_tile.py"),
    ("sd_forge_controlllite", "forge_controllllite.py"),
    ("sd_forge_controlnet", "controlnet.py"),
    ("sd_forge_ipadapter", "forge_ipadapter.py"),
)
_REQUIRED = "sd_forge_controlnet"

#: What the owner is told when the loaded model cannot take ControlNet.
SUPPORTED_FAMILIES = "SD 1.5, SDXL and Anima"

_LOCK = threading.RLock()
_SCRIPT_MODULE: Any = None
#: Registrars that failed to import, by folder, with the reason.
_SKIPPED: dict[str, str] = {}
#: Registrars imported so far, by folder; each is imported at most once.
_LOADED: dict[str, Any] = {}
#: The model-roots registry, handed over by `model_roots` whenever folders
#: change (the way `upscaler_roots` receives its roots), so a request's
#: catalogue id can be turned into a file without importing Studio here.
_REGISTRY: Any = None


class ControlNetUnavailable(RuntimeError):
    """Neo's ControlNet is missing or will not import. Named, never swallowed:
    an owner whose units silently did nothing could not tell that from a
    control that is merely weak."""


# -- the model folders -------------------------------------------------------

def set_registry(registry: Any) -> None:
    global _REGISTRY
    _REGISTRY = registry


def resolve_model(model_id: str) -> tuple[str, str]:
    """A catalogue id into (absolute path, display name). Raises `HeadlessError`."""

    from .contracts import HeadlessError

    registry = _REGISTRY
    if registry is None:
        raise HeadlessError(
            "CONTROLNET_MODEL_UNKNOWN",
            "No ControlNet folder is configured. Add one in Settings > Model folders.")
    candidate, source = registry.resolve("controlnet", str(model_id))
    return str(source.resolved), str(getattr(candidate, "display_name", "") or "")


# -- registration ------------------------------------------------------------

def _load(name: str, source: Path) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {source.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _decode_base64_to_image(encoding: str) -> Any:
    """Neo's `modules/api/api.py:76-98`, its data-URL branch only.

    The URL branch fetches over the network when Neo's API options allow it;
    nothing in Studio passes a URL here, and a stand-in must not grow a
    network path, so a URL is refused.
    """

    import base64
    from io import BytesIO

    text = str(encoding)
    if text.startswith(("http://", "https://")):
        raise ValueError("A ControlNet image must be inline data, not a URL.")

    from modules import images

    if text.startswith("data:image/"):
        text = text.split(";")[1].split(",")[1]
    return images.read(BytesIO(base64.b64decode(text)))


def _install_api_standin() -> None:
    """Give `modules.api.api` a headless stand-in, if the real one is absent.

    THE COUPLING. `lib_controlnet/external_code.py:9` and `lib_controlnet/
    api.py` import `modules.api.api` at module scope, and that module imports
    Neo's whole UI (`modules.ui`), which needs `shared.options_templates` --
    deliberately absent in Studio (`headless_compat.DELIBERATELY_ABSENT`). So
    the import fails, and with it Neo's ControlNet, in any Gradio-free
    process. Measured: `AttributeError: 'NoneType' object has no attribute
    'update'` from `modules/processing_scripts/comments.py:51`.

    WHAT IS USED. `external_code` calls `api.decode_base64_to_image` in
    `ControlNetUnit.from_dict`, for API callers that send a unit's image as a
    string. Studio builds its units directly and never takes that path;
    `lib_controlnet/api.py` registers HTTP routes only under `--api`, which
    Studio never sets. So the stand-in carries that one function, Neo's own
    data-URL decode, and Neo's files are not edited.
    """

    import types

    if "modules.api.api" in sys.modules:
        return
    standin = types.ModuleType("modules.api.api")
    standin.__doc__ = "Studio's headless stand-in (forge_headless/controlnet_port.py)."
    standin.decode_base64_to_image = _decode_base64_to_image
    sys.modules["modules.api.api"] = standin


def arm() -> Any:
    """Register Neo's preprocessors and control models; return the ControlNet
    script module. Idempotent; raises `ControlNetUnavailable`."""

    global _SCRIPT_MODULE
    with _LOCK:
        if _SCRIPT_MODULE is not None:
            return _SCRIPT_MODULE
        for entry in (str(_APP), *(str(_BUILTIN / folder) for folder, _ in REGISTRARS),
                      str(_VENDOR)):
            if entry not in sys.path:
                sys.path.append(entry)
        _install_api_standin()
        try:
            # The base types: `ControlNetPatcher` and the None / canny
            # preprocessors register when these two are first imported.
            import modules_forge.supported_controlnet  # noqa: F401
            import modules_forge.supported_preprocessor  # noqa: F401
        except Exception as error:  # noqa: BLE001 - named below
            raise ControlNetUnavailable(
                f"ControlNet could not be loaded: {type(error).__name__}: {error}") from error

        for folder, filename in REGISTRARS:
            # A registrar that loaded on an earlier, failed attempt is not
            # loaded again: its import registered preprocessors and control
            # types, and a second import would register the types twice.
            if folder in _LOADED or folder in _SKIPPED:
                continue
            source = _BUILTIN / folder / "scripts" / filename
            try:
                _LOADED[folder] = _load(f"studio_cn_{folder}", source)
            except Exception as error:  # noqa: BLE001 - Forge reports and continues
                if folder == _REQUIRED:
                    raise ControlNetUnavailable(
                        "ControlNet could not be loaded: "
                        f"{type(error).__name__}: {error}") from error
                _SKIPPED[folder] = f"{type(error).__name__}: {error}"
        _SCRIPT_MODULE = _LOADED[_REQUIRED]
        _wrap_downloads()
        return _SCRIPT_MODULE


def skipped() -> dict[str, str]:
    """Registrars that did not import, for diagnostics."""

    return dict(_SKIPPED)


def _slider(parameter: Any) -> dict[str, Any]:
    raw = dict(getattr(parameter, "gradio_update_kwargs", {}) or {})
    out: dict[str, Any] = {"visible": bool(raw.get("visible", False))}
    for key in ("label", "minimum", "maximum", "step", "value"):
        value = raw.get(key)
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            out[key] = value
    return out


_MISSING: dict[str, list[str]] | None = None
#: Set once the background warm-up (`arm` + `missing_packages`) has finished.
_WARM = threading.Event()
_WARMING = False
#: Its own lock, never `_LOCK`: the warm-up holds `_LOCK` through its imports,
#: and a list request must not wait on that just to learn it is under way.
_WARM_LOCK = threading.Lock()


def missing_packages() -> dict[str, list[str]]:
    """Preprocessor name -> the Python packages it needs that are not installed.

    Forge installs them at launch from
    `forge_legacy_preprocessors/install.py` (its requirements, insightface and
    two Depth Anything wheels); Studio never runs that file. Measured once:
    each legacy preprocessor's function names the `annotator` modules it
    imports, and importing those names any missing package. Nothing RUNS and
    nothing downloads, but the import is real, because only the import is
    exact: a static read of the same modules flagged two working
    preprocessors and missed one broken one (Evidence/controlnet-2026-10-20/
    probe_static.txt). So it is done off the request path: the page's first
    list request starts `_warm_up` and answers `loading`; a job waits for it
    under the lock. Cached for the process.
    """

    global _MISSING
    with _LOCK:
        if _MISSING is not None:
            return _MISSING
        import importlib
        import importlib.util
        import inspect
        import re
        import warnings

        # Two deprecation notices from third-party annotator code, printed on
        # every launch by these imports (owner, 2026-10-20) and actionable by
        # nobody running Studio: timm's old `timm.models.layers` path, and
        # OneFormer's `@autocast` decorator. Matched by message, so no other
        # warning is hidden.
        warnings.filterwarnings("ignore", r"Importing from timm\.models\.layers is deprecated",
                                FutureWarning)
        warnings.filterwarnings("ignore", r"`torch\.cuda\.amp\.autocast\(args\.\.\.\)` is deprecated",
                                FutureWarning)
        pattern = re.compile(r"from (annotator(?:\.\w+)+) import|import (annotator(?:\.\w+)+)")
        found: dict[str, list[str]] = {}
        try:
            arm()
            from legacy_preprocessors.preprocessor_compiled import legacy_preprocessors
            from modules_forge.shared import supported_preprocessors
        except Exception:  # noqa: BLE001 - unknown is not "missing"
            return {}
        for key, spec in legacy_preprocessors.items():
            function = spec.get("call_function")
            function = getattr(function, "func", function)
            try:
                source = inspect.getsource(function)
            except (TypeError, OSError):
                continue
            missing = []
            for module in sorted({a or b for a, b in pattern.findall(source)}):
                try:
                    importlib.import_module(module)
                except ModuleNotFoundError as error:
                    if error.name and error.name not in missing:
                        missing.append(error.name)
                except Exception:  # noqa: BLE001 - other failures surface when it runs
                    pass
            if missing:
                found[str(spec.get("label", key))] = missing
        if importlib.util.find_spec("insightface") is None:
            for name in supported_preprocessors:
                if "InsightFace" in name:
                    found[name] = ["insightface"]
        _MISSING = found
        return found


def _warm_up() -> None:
    try:
        arm()
        missing_packages()
    except Exception:  # noqa: BLE001 - a job reports its own failure by name
        pass
    finally:
        _WARM.set()


def _start_warm_up() -> None:
    """Arm and measure on a background thread, once."""

    global _WARMING
    with _WARM_LOCK:
        if _WARMING:
            return
        _WARMING = True
    threading.Thread(target=_warm_up, name="studio-controlnet-warm-up", daemon=True).start()


#: Preprocessors Studio does not run, with the reason the owner is shown.
WITHHELD: dict[str, str] = {
    # Its model code builds DINOv2 with torch.hub.load("facebookresearch/
    # dinov2"), which downloads that GitHub repository and RUNS it. Weights on
    # first use are the owner's decision; executing fetched code is not.
    # depth_anything_v2 does the same job with DINOv2 included.
    "depth_anything": ("depth_anything downloads and runs code from GitHub "
                       "the first time it is used, so Studio does not run it. "
                       "Use depth_anything_v2, which does the same job."),
}


def _unavailable(name: str, missing: list[str]) -> str:
    packages = ", ".join(missing)
    return (f"The {name} preprocessor needs the Python package {packages}, which "
            "this Studio install does not have. Choose another preprocessor.")


def preprocessor_catalogue() -> list[dict[str, Any]] | None:
    """Neo's preprocessors for the page, or None while the engine is not up.

    Each carries what Forge's own UI builds its controls from: tags (the
    control type), whether it needs a model, whether control mode applies,
    and the resolution and two sliders with their labels and ranges.
    """

    if "modules.shared" not in sys.modules:
        return None
    # Registration and the package measurement import many modules; never on
    # the request thread. Until they are done the page is told `loading`.
    if not _WARM.is_set():
        _start_warm_up()
        return None
    try:
        arm()
        from lib_controlnet import global_state
        from modules_forge.shared import supported_preprocessors
    except Exception:  # noqa: BLE001 - a UI list is never fatal
        return None
    missing = missing_packages()
    listing = []
    for name in global_state.get_all_preprocessor_names():
        preprocessor = supported_preprocessors.get(name)
        if preprocessor is None:
            continue
        listing.append({
            "name": name,
            # Packages this install lacks, or why Studio withholds it; the
            # page offers either greyed out.
            "needs": list(missing.get(name, [])),
            "withheld": WITHHELD.get(name, ""),
            "tags": list(getattr(preprocessor, "tags", []) or []),
            "needs_model": not bool(getattr(preprocessor, "do_not_need_model", False)),
            "control_mode": bool(getattr(preprocessor, "show_control_mode", True)),
            "resolution": _slider(getattr(preprocessor, "slider_resolution", None)),
            "slider_1": _slider(getattr(preprocessor, "slider_1", None)),
            "slider_2": _slider(getattr(preprocessor, "slider_2", None)),
        })
    return listing


def preprocessor_listing() -> list[dict[str, Any]]:
    """`/studio/cn_preprocessors`: the catalogue, or "None" marked `loading`
    until the engine is up, so the page knows to ask again."""

    listing = preprocessor_catalogue()
    return listing if listing is not None else [{"name": "None", "loading": True}]


def _default(parameter: Any, fallback: float) -> int:
    """A slider's default, as Neo's `bound_check_params` takes it -- `int()`
    of the value, which is how Forge fills a threshold left at -1."""

    return int(getattr(parameter, "gradio_update_kwargs", {}).get("value", fallback))


def preprocess(payload: Any) -> dict[str, Any]:
    """Run one preprocessor on one picture: the map a unit would condition on.

    Forge's own preview (`controlnet_ui_group.py:636-704` `run_annotator`):
    the picture as HWC RGB, pixel-perfect resolution when asked, the
    preprocessor with its resolution and two sliders, and the input itself
    when the result is not an image (Reference, IP-Adapter). Defaults for -1
    as `bound_check_params` fills them. For the panel's preview and its "Use
    as Klein reference" button. Raises `HeadlessError`.
    """

    from types import SimpleNamespace

    from .contracts import HeadlessError
    from .input_assets import InputAssetRefused, decode_source

    data = payload if isinstance(payload, dict) else {}
    if "modules.shared" not in sys.modules:
        raise HeadlessError("CONTROLNET_NOT_READY",
                            "Studio's engine is still starting. Try again in a moment.")
    name = str(data.get("preprocessor") or "None")
    try:
        source = decode_source(SimpleNamespace(data_url=str(data.get("image") or "")),
                               required=True, keep_alpha=True)
    except InputAssetRefused as error:
        raise HeadlessError("CONTROLNET_IMAGE_INVALID",
                            str(getattr(error, "message", "") or error)) from error
    try:
        arm()
    except ControlNetUnavailable as error:
        raise HeadlessError("CONTROLNET_UNAVAILABLE", str(error)) from error

    import torch
    from lib_controlnet import external_code
    from lib_controlnet.utils import judge_image_type
    from modules_forge.shared import supported_preprocessors

    preprocessor = supported_preprocessors.get(name)
    if preprocessor is None:
        raise HeadlessError("CONTROLNET_PREPROCESSOR_UNKNOWN",
                            f"There is no preprocessor named {name!r}.")
    if name in WITHHELD:
        raise HeadlessError("CONTROLNET_PREPROCESSOR_WITHHELD", WITHHELD[name])

    def number(key: str, fallback: float) -> float:
        value = data.get(key, fallback)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback

    image = _pixels(source.image)
    resolution = int(number("processor_res", -1))
    if resolution < 0:
        resolution = _default(preprocessor.slider_resolution, 512)
    if data.get("pixel_perfect") is True:
        resolution = external_code.pixel_perfect_resolution(
            image,
            target_H=int(number("height", image.shape[0])),
            target_W=int(number("width", image.shape[1])),
            resize_mode=external_code.resize_mode_from_value(
                str(data.get("resize_mode") or "Crop and Resize")))
    threshold_a = number("threshold_a", -1)
    threshold_b = number("threshold_b", -1)
    if threshold_a < 0:
        threshold_a = _default(preprocessor.slider_1, 1.0)
    if threshold_b < 0:
        threshold_b = _default(preprocessor.slider_2, 1.0)

    try:
        with torch.no_grad():
            result = preprocessor(input_image=image, resolution=resolution,
                                  slider_1=threshold_a, slider_2=threshold_b,
                                  input_mask=None, json_pose_callback=None)
    except ModuleNotFoundError as error:
        raise HeadlessError("CONTROLNET_PREPROCESSOR_UNAVAILABLE",
                            _unavailable(name, [str(error.name)])) from error
    except Exception as error:  # noqa: BLE001 - named, not swallowed
        raise HeadlessError("CONTROLNET_FAILED",
                            f"The {name} preprocessor failed: {type(error).__name__}: {error}") from error
    is_map = bool(judge_image_type(result))
    if not is_map:
        result = image
    result = external_code.visualize_inpaint_mask(result)

    import base64
    import io

    import numpy as np
    from PIL import Image

    picture = Image.fromarray(np.ascontiguousarray(np.clip(result, 0, 255).astype(np.uint8)))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return {
        "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width": picture.width,
        "height": picture.height,
        # False for preprocessors whose output is not a picture (Reference,
        # IP-Adapter): the page then has no map to show or hand to Klein.
        "is_map": is_map,
    }


# -- families ----------------------------------------------------------------

def supports(engine: Any) -> bool:
    """Whether Neo can control this engine: a UNet model, or Anima."""

    return bool(getattr(engine, "is_sd1", False) or getattr(engine, "is_sdxl", False)
                or type(engine).__name__ == "Anima")


# -- units -------------------------------------------------------------------

def _pixels(image: Any) -> Any:
    """PIL into the HWC uint8 RGB array a unit carries. Transparency is
    flattened onto white, as the Extension's `build_cn_units` does."""

    import numpy as np
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(white, rgba)
    return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()


def _model_key(unit: Any, taken: dict[str, str]) -> str:
    """The name Neo's `global_state` looks the file up by.

    The display name, never the path: Neo writes `unit.model` into the
    picture's parameters (`Infotext.write_infotext`). Two different files
    with one display name in the same job get a suffix.
    """

    base = str(unit.model_name or Path(unit.model_path).stem)
    key = base
    number = 2
    while key in taken and taken[key] != unit.model_path:
        key = f"{base} ({number})"
        number += 1
    taken[key] = unit.model_path
    return key


def check(engine: Any, options: tuple[Any, ...], *, has_source: bool = True) -> None:
    """Refuse, by name, what Neo would fail on mid-job. Raises `HeadlessError`.

    `has_source`: whether the job is an image operation. A unit with no
    picture of its own reads the operation's source (Neo's `get_input_data`),
    and a text-to-image job has none -- Neo would fail on `HWC3(None)`.
    """

    from .contracts import HeadlessError

    if not options:
        return
    if not has_source:
        for number, unit in enumerate(options, start=1):
            if unit.image is None:
                raise HeadlessError(
                    "CONTROLNET_IMAGE_MISSING",
                    f"ControlNet unit {number} reads the canvas, which is empty. "
                    "Choose Upload or a layer for it.")
    if not supports(engine):
        raise HeadlessError(
            "CONTROLNET_UNSUPPORTED_MODEL",
            f"ControlNet works with {SUPPORTED_FAMILIES} models; the loaded "
            f"model ({type(engine).__name__}) cannot use it.")
    try:
        arm()
        from modules_forge.shared import supported_preprocessors
    except ControlNetUnavailable as error:
        raise HeadlessError("CONTROLNET_UNAVAILABLE", str(error)) from error
    missing = missing_packages()
    for number, unit in enumerate(options, start=1):
        preprocessor = supported_preprocessors.get(unit.preprocessor)
        if preprocessor is None:
            raise HeadlessError(
                "CONTROLNET_PREPROCESSOR_UNKNOWN",
                f"ControlNet unit {number}: there is no preprocessor "
                f"named {unit.preprocessor!r}.")
        if unit.preprocessor in WITHHELD:
            raise HeadlessError("CONTROLNET_PREPROCESSOR_WITHHELD",
                                f"ControlNet unit {number}: {WITHHELD[unit.preprocessor]}")
        if unit.preprocessor in missing:
            raise HeadlessError(
                "CONTROLNET_PREPROCESSOR_UNAVAILABLE",
                f"ControlNet unit {number}: "
                + _unavailable(unit.preprocessor, missing[unit.preprocessor]))
        if not getattr(preprocessor, "do_not_need_model", False) and not unit.model_path:
            raise HeadlessError(
                "CONTROLNET_MODEL_MISSING",
                f"ControlNet unit {number}: choose a model for "
                f"{unit.preprocessor!r}.")


#: Neo's Union ControlNet types (`backend/nn/cnets/control_types.py`), read
#: by substring as `convert_control_type` does.
_UNION_TYPES = ("OpenPose", "Depth", "Scribble/SoftEdge/Sketch", "Canny/Lineart/MLSD",
                "NormalMap", "Segmentation", "Tile", "Inpaint")


def type_filter(preprocessor: Any) -> str:
    """The Control Type a unit's preprocessor belongs to, for Union models.

    Forge's UI has the owner pick a Control Type, which filters the
    preprocessor list by tag and becomes `unit.type_filter`; Neo turns that
    into a Union model's control type (`convert_control_type`). Studio's panel
    picks the preprocessor directly, so the type is its first tag that names
    a Union type -- the pairing Forge's filter makes. "All" (no Union type)
    when it has none, which is Neo's default.
    """

    for tag in getattr(preprocessor, "tags", None) or ():
        if any(str(tag) in union for union in _UNION_TYPES):
            return str(tag)
    return "All"


def neo_units(options: tuple[Any, ...]) -> list[Any]:
    """Studio's units as Neo's `ControlNetUnit`s, their files registered with
    Neo's `global_state` under display names. Fresh objects every call: Neo
    mutates a unit while processing it (`bound_check_params`, pixel-perfect)."""

    arm()
    from lib_controlnet import global_state
    from lib_controlnet.external_code import ControlNetUnit
    from modules_forge.shared import supported_preprocessors

    taken: dict[str, str] = {}
    units = []
    for unit in options:
        model = "None"
        if unit.model_path:
            model = _model_key(unit, taken)
            global_state.controlnet_filename_dict[model] = unit.model_path
        units.append(ControlNetUnit(
            type_filter=type_filter(supported_preprocessors.get(unit.preprocessor)),
            enabled=True,
            module=unit.preprocessor,
            model=model,
            weight=float(unit.weight),
            # None: Neo reads the operation's own source image (and, for an
            # "only masked" inpaint, crops it as the mask does).
            image=_pixels(unit.image) if unit.image is not None else None,
            resize_mode=unit.resize_mode,
            processor_res=int(unit.processor_res),
            threshold_a=float(unit.threshold_a),
            threshold_b=float(unit.threshold_b),
            guidance_start=float(unit.guidance_start),
            guidance_end=float(unit.guidance_end),
            pixel_perfect=bool(unit.pixel_perfect),
            control_mode=unit.control_mode,
            hr_option=unit.hires_option,
        ))
    return units


# -- the runner on p.scripts --------------------------------------------------

def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


class ControlNetRunner:
    """A ScriptRunner holding Neo's ControlNet script and this job's units.

    Each hook is the runner's call to the script, `script.hook(p, *pos,
    *units, **kw)` (`modules/scripts.py:864-980`). The units are built in
    `process`, fresh for each processing object, so the image-operation Hires
    pass gets its own copy (as the Extension's re-run of `process_images` on
    the same `p` did). Every other hook is a no-op, which is what Neo's base
    `Script` does for the hooks ControlNet does not override.
    """

    def __init__(self, options: tuple[Any, ...], *,
                 announce: Callable[[str | None], None] | None = None) -> None:
        self._options = tuple(options)
        self._announce = announce
        self._script: Any = None
        self._units: list[Any] = []

    def process(self, p: Any, *_args: Any, **_kwargs: Any) -> None:
        from .contracts import HeadlessError

        # Neo's failures reach the owner by name. A bare exception ends as its
        # TYPE alone ("ModuleNotFoundError") in the job record, which is what
        # the first live run showed -- so arming and building the units are
        # inside the same net as Neo's own processing.
        try:
            script = arm().ControlNetForForgeOfficial()
            self._units = neo_units(self._options)
            self._script = script
            with _announcing_downloads(self._announce):
                self._script.process(p, *self._units)
        except HeadlessError:
            raise
        except ControlNetUnavailable as error:
            raise HeadlessError("CONTROLNET_UNAVAILABLE", str(error)) from error
        except ModuleNotFoundError as error:
            raise HeadlessError("CONTROLNET_PREPROCESSOR_UNAVAILABLE",
                                f"ControlNet needs the Python package {error.name}, which "
                                "this Studio install does not have.") from error
        except Exception as error:  # noqa: BLE001 - named, not swallowed
            raise HeadlessError("CONTROLNET_FAILED",
                                f"ControlNet failed: {type(error).__name__}: {error}") from error

    def process_before_every_sampling(self, p: Any, **kwargs: Any) -> None:
        if self._script is not None:
            self._script.process_before_every_sampling(p, *self._units, **kwargs)

    def postprocess_batch_list(self, p: Any, pp: Any, **kwargs: Any) -> None:
        if self._script is not None:
            self._script.postprocess_batch_list(p, pp, *self._units, **kwargs)

    def postprocess(self, p: Any, processed: Any, *_args: Any) -> None:
        if self._script is not None:
            self._script.postprocess(p, processed, *self._units)

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _noop


class CompositeRunner:
    """Several runners on one `p.scripts`, each called in order."""

    def __init__(self, *runners: Any) -> None:
        self._runners = tuple(runners)

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> None:
            for runner in self._runners:
                getattr(runner, name)(*args, **kwargs)

        return call


def compose(*runners: Any) -> Any:
    """None for no runner (the job keeps `scripts = None`, byte-identical to
    before), the runner itself for one, a composite for more."""

    present = tuple(runner for runner in runners if runner is not None)
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return CompositeRunner(*present)


# -- first-use downloads -------------------------------------------------------

#: Who to tell about a download on THIS thread: the running job's progress,
#: set only for the length of its `process`. Thread-local, so a download on
#: any other thread (a preview, a startup scan) tells nobody.
_ANNOUNCER = threading.local()
_DOWNLOADS_WRAPPED = False


def _wrap_downloads() -> None:
    """Install, once and for good, a `load_file_from_url` that names its file.

    Neo fetches preprocessor weights on first use through
    `modules.util.load_file_from_url` (owner's decision: as Forge does).
    Annotators import it by name, many of them for the first time DURING a
    job, so a wrapper swapped in and out around each job was picked up by
    those modules mid-swap and kept: later jobs then announced to the first
    job's progress. Permanent instead: modules already holding Neo's function
    get the wrapper now, and every later `from modules.util import ...` gets
    it too. With no announcer set it is Neo's function, unchanged.
    """

    global _DOWNLOADS_WRAPPED
    if _DOWNLOADS_WRAPPED:
        return
    import os

    from modules import util

    original = util.load_file_from_url

    def load_file_from_url(url: str, *, model_dir: str, progress: bool = True,
                           file_name: str | None = None, **kwargs: Any) -> str:
        announce = getattr(_ANNOUNCER, "announce", None)
        name = file_name or os.path.basename(str(url).split("?")[0])
        fetching = announce is not None and not os.path.exists(os.path.join(model_dir, name))
        if fetching:
            announce(name)
        try:
            return original(url, model_dir=model_dir, progress=progress,
                            file_name=file_name, **kwargs)
        finally:
            if fetching:
                announce(None)

    for module in list(sys.modules.values()):
        try:
            holds = getattr(module, "load_file_from_url", None) is original
        except Exception:  # noqa: BLE001 - a lazy module that will not answer
            continue
        if holds:
            module.load_file_from_url = load_file_from_url
    _DOWNLOADS_WRAPPED = True


class _announcing_downloads:
    """Tell `announce` about each file downloaded on this thread meanwhile."""

    def __init__(self, announce: Callable[[str | None], None] | None) -> None:
        self._announce = announce

    def __enter__(self) -> "_announcing_downloads":
        if self._announce is not None:
            _wrap_downloads()
            _ANNOUNCER.announce = self._announce
        return self

    def __exit__(self, *_exc: Any) -> None:
        _ANNOUNCER.announce = None


__all__ = (
    "CompositeRunner",
    "ControlNetRunner",
    "ControlNetUnavailable",
    "SUPPORTED_FAMILIES",
    "WITHHELD",
    "arm",
    "check",
    "compose",
    "neo_units",
    "missing_packages",
    "preprocess",
    "preprocessor_catalogue",
    "preprocessor_listing",
    "resolve_model",
    "set_registry",
    "skipped",
    "supports",
)
