"""Register the engine's LoRA handler, so `<lora:name:weight>` does something.

    prompt "<lora:x:1>"  ->  parse_extra_network_prompts  ->  activate  ->  HERE

THE DEFECT THIS CLOSES

Measured live, with 73 LoRAs listed and one of their names in the prompt, same
seed, everything else identical:

    pixels differing   0 of 262,144      0.00%

`extra_networks.activate()` IS reached -- it is at `modules/processing.py:981`,
inside `process_images_inner`, which Studio calls directly, and
`parse_extra_network_prompts()` runs three lines above it. So the tag was
parsed OUT of the prompt and handed to a registry with no handler for it. It
was discarded, silently, and the image was the one you would get without it.

WHY THE HANDLER WAS MISSING

`lora_script.py:51-54` registers it inside `before_ui()`, and Forge fires that
from `webui.py:61` -- `api_only_worker` calls `before_ui_callback()` even with
no Gradio, which is exactly why the Extension gets LoRAs for free by living
inside Forge.

Studio reaches neither half. `backend_bootstrap` states both refusals and means
them: it does not run `load_scripts()` ("Studio boots its own backend; it does
not boot the legacy application and ride on top") and a Gradio import is a
TERMINAL bootstrap failure. `lora_script.py` uses `gr.Dropdown` at module
scope, so importing it would break the second rule -- and without importing it
nothing calls `on_before_ui`, so calling `before_ui_callback()` would fire
nothing at all.

WHY THIS IS NOT STUDIO INVENTING AN INITIALIZATION

The LoRA machinery itself needs no Gradio -- `networks.py`,
`extra_networks_lora.py` and `network.py` import none. Only the script wrapper
does. So this runs the same two statements Forge runs, reached the way Studio
already reaches a builtin extension:

    soft_inpainting_bridge.py:75  "`extensions-builtin/soft-inpainting/scripts/`
    is not a package -- both directory names contain hyphens -- and nothing in
    the headless path puts it on `sys.path`, so a plain import cannot reach it.
    Loading by file location is the honest way in rather than mutating
    `sys.path` for the whole process."

`extensions-builtin` carries the same hyphen. Same technique, same reason.

WHAT THIS DOES NOT DO

Only the LoRA handler. Every other extension's `before_ui` registration is
still absent, and any feature that depends on one is still inert. That is a
separate question and this must not be read as answering it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: Where the engine keeps its LoRA implementation. Hyphenated parent, so it is
#: not importable as a package and has to be loaded by location.
_EXTENSION = (Path(__file__).resolve().parents[1]
              / "extensions-builtin" / "sd_forge_lora")

#: Set once the handler is registered. `register_extra_network` appends to a
#: list, so arming twice would apply every LoRA twice.
_ARMED = False


class LoraUnavailable(RuntimeError):
    """The engine's LoRA modules are missing or will not import.

    Named rather than swallowed, for the reason `SoftInpaintingUnavailable`
    gives: an owner whose LoRA silently did nothing has no way to tell that
    from the LoRA having no effect, and that is the defect class this exists
    to remove.
    """


def _load_modules() -> tuple[Any, Any]:
    """Import the extension's modules with its own directory importable.

    ITS MODULES IMPORT EACH OTHER BY BARE NAME -- `networks.py` does
    `import network`, `extra_networks_lora.py` does `import networks` -- which
    is what a `load_scripts()` run arranges by putting the extension directory
    on the path. A first attempt here aliased them into `sys.modules` one at a
    time and failed with `ModuleNotFoundError: No module named 'networks'`,
    because reproducing an import graph by hand means getting every edge right
    and there is no reason to.

    So the directory goes on `sys.path` for the duration and comes off again.
    `soft_inpainting_bridge` avoids `sys.path` because it loads ONE
    self-contained file; this is a package-shaped set that imports itself, and
    the honest way in is to let it.

    The `finally` matters: leaving the directory on the path would let a later
    `import network` anywhere in the process resolve to a LoRA internal.
    """

    import importlib
    import sys

    if not _EXTENSION.is_dir():
        raise LoraUnavailable("The LoRA extension is not in this build.")

    directory = str(_EXTENSION)
    # THE ENGINE ROOT GOES ON TOO, and this is the half that was missing.
    #
    # `network.py:4` is `from modules import cache, errors, hashes, sd_models,
    # shared`, and `networks.py` reaches `backend.*` through those. Both are
    # bare top-level names that only resolve while the engine root is
    # importable -- which is exactly what `load_scripts()` arranges in Forge,
    # and is the same argument this bridge already makes for the extension's
    # own directory.
    #
    # Without it, arming failed on a live server with
    #
    #     cannot import name 'cache' from 'modules' (unknown location)
    #
    # and "(unknown location)" is what Python says about a NAMESPACE package
    # whose search path resolved to nothing. `app/modules/` has no
    # `__init__.py`, so `modules` is a namespace package, and a namespace
    # package's `__path__` is recomputed whenever `sys.path` changes. The
    # insert below is that change. If the engine root is not on `sys.path` at
    # that moment -- which is the state a launcher that inserts it, imports,
    # and tidies up leaves behind -- the recomputation finds nothing and every
    # `modules.*` name the extension needs disappears mid-import.
    #
    # So the mutation that breaks it also repairs it: put both directories on
    # for the duration, and take off exactly what was put on.
    root = str(_EXTENSION.parents[1])
    added = [
        entry for entry in (directory, root) if entry not in sys.path
    ]
    for entry in added:
        sys.path.insert(0, entry)
    try:
        networks = importlib.import_module("networks")
        extra_networks_lora = importlib.import_module("extra_networks_lora")
    except ImportError as error:
        # The search path is named, because the message alone could not
        # distinguish "this build has no LoRA code" from "the code is there and
        # `modules` briefly stopped resolving", and those need opposite fixes.
        import modules  # noqa: PLC0415 - only to describe the failure

        raise LoraUnavailable(
            f"The LoRA extension could not be imported: {error} "
            f"(modules search path: {list(getattr(modules, '__path__', []))})"
        ) from error
    finally:
        for entry in added:
            try:
                sys.path.remove(entry)
            except ValueError:
                pass
    return networks, extra_networks_lora


def arm(roots: tuple[str, ...] = ()) -> bool:
    """Register the LoRA handler and point its scan at the owner's roots.

    Idempotent and cheap after the first call. Returns True when the handler
    is in place, so a caller can report honestly rather than assume.

    `roots` is projected before the scan for the reason AR8.1 gives: the
    engine scans `[cmd_opts.lora_dir, *cmd_opts.lora_dirs]`, and Studio has no
    command line to put them on.
    """

    global _ARMED
    if _ARMED:
        return True

    networks, extra_networks_lora = _load_modules()

    from modules import extra_networks, shared

    if roots:
        shared.cmd_opts.lora_dirs = list(roots)

    # The two statements `before_ui` runs, minus its third -- the extra-networks
    # PAGE, which is a Gradio surface Studio has no place to put and does not
    # need to apply a LoRA.
    networks.extra_network_lora = extra_networks_lora.ExtraNetworkLora()
    extra_networks.register_extra_network(networks.extra_network_lora)

    # After the projection, so the scan sees the owner's folder rather than the
    # engine's default.
    networks.list_available_networks()

    _ARMED = True
    return True


def armed() -> bool:
    return _ARMED


def available_names() -> tuple[str, ...]:
    """Every name the engine will now resolve. Empty before `arm`."""

    networks = sys.modules.get("networks")
    if networks is None:
        return ()
    try:
        return tuple(sorted(networks.available_networks or ()))
    except Exception:  # noqa: BLE001
        return ()


def _reset_for_tests() -> None:
    """Undo `arm`, so a test can assert the idempotence it claims."""

    global _ARMED
    _ARMED = False


__all__ = ("LoraUnavailable", "arm", "armed", "available_names")
