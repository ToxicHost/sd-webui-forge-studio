from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from modules import options, shared_cmd_options, shared_items, util
from modules.paths_internal import data_path, extensions_builtin_dir, extensions_dir, models_path, script_path  # noqa: F401

if TYPE_CHECKING:
    import gradio as gr

    from backend.diffusion_engine.base import ForgeDiffusionEngine
    from modules import face_restoration, memmon, shared_state, shared_total_tqdm, styles, upscaler

cmd_opts = shared_cmd_options.cmd_opts
parser = shared_cmd_options.parser

batch_cond_uncond = True  # old field, unused now in favor of shared.opts.batch_cond_uncond
parallel_processing_allowed = True
styles_filename = cmd_opts.styles_file = cmd_opts.styles_file if len(cmd_opts.styles_file) > 0 else [os.path.join(data_path, "styles.csv"), os.path.join(data_path, "styles_integrated.csv")]
config_filename = cmd_opts.ui_settings_file
hide_dirs = {"visible": not cmd_opts.hide_ui_dir_config}

demo: gr.Blocks = None

device: str = None


state: "shared_state.State" = None

prompt_styles: "styles.StyleDatabase" = None

face_restorers: list["face_restoration.FaceRestoration"] = []

options_templates: dict = None
opts: options.Options = None
restricted_opts: set[str] = None

sd_model: "ForgeDiffusionEngine" = None

settings_components: dict = None
"""assigned from ui.py, a mapping on setting names to gradio components responsible for those settings"""

tab_names: list[str] = []

latent_upscale_default_mode = "Latent"
latent_upscale_modes = {
    "Latent": {"mode": "bilinear", "antialias": False},
    "Latent (antialiased)": {"mode": "bilinear", "antialias": True},
    "Latent (bicubic)": {"mode": "bicubic", "antialias": False},
    "Latent (bicubic antialiased)": {"mode": "bicubic", "antialias": True},
    "Latent (nearest)": {"mode": "nearest", "antialias": False},
    "Latent (nearest-exact)": {"mode": "nearest-exact", "antialias": False},
}

sd_upscalers: list["upscaler.Upscaler"] = []

progress_print_out = sys.stdout

# Assigned by the compatibility/UI layer: `shared_gradio_themes.reload_gradio_theme`
# runs during `initialize_util.configure_opts_onchange()` (`onchange` defaults to
# `call=True`), and both UI read sites call `ensure_gradio_theme()` first. Nothing
# on the Studio headless path reads it, so no theme is constructed there.
gradio_theme: gr.themes.ThemeClass = None

total_tqdm: "shared_total_tqdm.TotalTQDM" = None

mem_mon: "memmon.MemUsageMonitor" = None

options_section = options.options_section
OptionInfo = options.OptionInfo
OptionHTML = options.OptionHTML

natural_sort_key = util.natural_sort_key
listfiles = util.listfiles
html_path = util.html_path
html = util.html
walk_files = util.walk_files

def reload_gradio_theme(theme_name=None):
    """Explicit lazy accessor for the UI-layer theme module.

    `shared_gradio_themes` imports Gradio, so importing it here would put Gradio
    back on every inference import path. Callers are the legacy UI and the
    options `onchange` hook, both of which run after Gradio is available.
    """
    from modules import shared_gradio_themes

    return shared_gradio_themes.reload_gradio_theme(theme_name)


def ensure_gradio_theme():
    """Guarantee `gradio_theme` is a real theme before the UI reads it."""
    from modules import shared_gradio_themes

    return shared_gradio_themes.ensure_gradio_theme()

list_checkpoint_tiles = shared_items.list_checkpoint_tiles
refresh_checkpoints = shared_items.refresh_checkpoints
list_samplers = shared_items.list_samplers

hf_endpoint = os.getenv("HF_ENDPOINT", "https://huggingface.co")


def __getattr__(name: str):
    """Lazily resolve attributes whose answer needs a device.

    `xformers_available` used to be computed at import time from
    `backend.memory_management`, which imports Torch and probes device memory at
    module scope. That single line made importing `modules.shared` -- and so
    every inference path -- initialise an accelerator, even for callers that
    only wanted an option value.

    The value is still read exactly once and then cached as a module global, so
    behaviour for the one reader (`modules/errors.py:111`) is unchanged. Nothing
    on the Studio headless path reads it, so no device is touched there.
    """
    if name == "xformers_available":
        from backend import memory_management

        value = memory_management.xformers_enabled()
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
