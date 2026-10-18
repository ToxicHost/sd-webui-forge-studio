"""The shift (or distilled guidance) Neo would use for the loaded model.

Neo's processing calls `sd_model.set_shift(shift=distilled_cfg_scale)` before
every pass, and for a flow model that value IS the noise schedule's shift
(`backend/modules/k_prediction.py` `set_parameters`). Neo's own front end
fills it from the UI preset (`modules_forge/main_entry.py`
`on_preset_change`, pushing `{preset}_t2i_dcfg`, `_t2i_hr_dcfg` and
`_i2i_dcfg`, whose defaults are `abs(SHIFT[preset])` or `DISTILL[preset]` in
`modules_forge/presets.py`).

Studio has no preset selector and sent 3.0 -- Anima's value -- for every
family, so Lumina/Neta (6), Z-Image (9), rectified-flow SDXL and Mugen (9),
Wan (5), PiD (1.5) and Krea 2 (1.15) generated on the wrong schedule. This
names the preset from the loaded ENGINE's class, as an owner would pick it in
Neo, and returns that preset's value. An engine it does not know keeps the
fallback, which is what Studio always sent.
"""

from __future__ import annotations

from typing import Any

#: Neo's engine class (`backend/diffusion_engine/*.py`) -> its UI preset
#: (`modules_forge/presets.py` `PresetArch`). Chroma has no preset of its own
#: and runs under Flux's; Mugen is SDXL-based and runs under `xl`.
ENGINE_PRESET = {
    "StableDiffusion": "sd",
    "StableDiffusionXL": "xl",
    "StableDiffusionXLRefiner": "xl",
    "Mugen": "xl",
    "Flux": "flux",
    "Chroma": "flux",
    "Flux2": "klein",
    "QwenImage": "qwen",
    "Lumina2": "lumina",
    "ZImage": "zit",
    "Wan": "wan",
    "Anima": "anima",
    "ErnieImage": "ernie",
    "PiD": "pid",
    "Krea2": "krea",
}


def preset_for(engine: Any) -> str | None:
    """Neo's UI preset for this engine, or None for one it does not list."""

    return ENGINE_PRESET.get(type(engine).__name__) if engine is not None else None


def family_shift(engine: Any, fallback: float) -> float:
    """The value Neo's preset gives this engine's family: `abs(SHIFT[...])`
    for a shift family, `DISTILL[...]` for a distilled-guidance one. Any
    other engine, or a preset in neither table (sd, klein, qwen -- where Neo
    hides the slider and the value does nothing), keeps `fallback`."""

    preset = preset_for(engine)
    if preset is None:
        return float(fallback)
    from modules_forge import presets

    for table in (presets.SHIFT, presets.DISTILL):
        for arch, value in table.items():
            if arch.name == preset:
                return abs(float(value))
    return float(fallback)
