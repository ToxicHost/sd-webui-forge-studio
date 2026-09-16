"""Every LoRA the ENGINE will resolve, from the owner's configured root.

    "+ LoRAs" -> /studio/loras -> here -> networks.available_networks

WHY THE ENGINE'S REGISTRY AND NOT A DIRECTORY SCAN

`<lora:NAME:weight>` is resolved by the engine, against
`networks.available_networks` (`extensions-builtin/sd_forge_lora/networks.py`).
A name Studio invented from a filename is a name the engine may not match: the
registry keys on its own naming, carries an `alias`, and keeps a hash lookup.

`/studio/upscalers` already works this way -- it reads the engine's registry, so
every name it offers is a name the engine will dispatch. This is the same shape
and gets the same treatment. Two independent scans of one folder would be free
to disagree while both believed themselves authoritative, which is the argument
`detector_catalogue` makes for the detector root.

WHY THE ROOT IS PROJECTED RATHER THAN PASSED

The engine scans `[shared.cmd_opts.lora_dir, *shared.cmd_opts.lora_dirs]`
(`networks.py:157`), and those come from a command line Studio does not have:
`backend_bootstrap.py:191-194` blanks `sys.argv` before the first Neo import, on
purpose, so Neo's parsers cannot consume the launcher's own arguments.

So the configured root is PROJECTED into `cmd_opts`, exactly as
`runtime_options.py` projects the compute flags, and for the reason that module
states: "Studio-owned values, validated here and PROJECTED into the inherited
modules before those modules read them."

WHY THE SCAN IS RE-RUN HERE

`list_available_networks()` is called by the LoRA extension during engine
startup, which happens before Studio has projected anything -- so the registry
would hold whatever the DEFAULT directory contained, and nothing would say so.
Re-running it after the projection is what makes the answer depend on the
owner's root rather than on init ordering. It is a directory scan, and it runs
when the browser asks, not on the generation path.

NAMES, NEVER PATHS. The engine's own API shape includes `path: obj.filename`
(`lora_script.py:32`). That must not leave Studio: `_role_payload`'s rule is
"never an absolute path, never the root, and never `relative_location` -- which
is root-relative and therefore still describes the owner's directory layout."

Studio-owned: the Neo imports are inside the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoraEntry:
    """One offerable LoRA. A name the engine resolves, and nothing locating it."""

    name: str
    alias: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "alias": self.alias}


def configured_roots(registry: Any) -> tuple[str, ...]:
    """The owner's `lora` roots, or none.

    A registry that does not know the role, or has no root for it, yields
    nothing -- the ordinary state before the owner points at a folder, not an
    error. Same contract as `scan_configured_detectors`.
    """

    try:
        roots = registry.configured_roots().get("lora", ())
    except Exception:  # noqa: BLE001 - a missing catalogue is an empty one
        return ()
    return tuple(str(root) for root in roots if str(root))


def project_roots(roots: tuple[str, ...]) -> bool:
    """Point the engine's scan at the owner's roots. True if it took.

    Returns False rather than raising when the engine is not importable: a
    Gallery-only or cold-start Studio must still answer the route.
    """

    if not roots:
        return False
    try:
        from modules import shared

        options = getattr(shared, "cmd_opts", None)
        if options is None:
            return False
        # `lora_dirs` rather than `lora_dir`: Studio's roots are a LIST per
        # role, and `--lora-dirs` is declared `action="append"`
        # (cmd_args.py:81), so the plural is the one shaped for more than one.
        # `lora_dir` is left alone -- it is the engine's own default and
        # overwriting it would hide a folder the owner may also be using.
        options.lora_dirs = list(roots)
        return True
    except (Exception, SystemExit):
        # SystemExit is named for the same reason the port names it: importing
        # `modules.shared` parses argv at module scope and exits on one it does
        # not recognise. A UI list is never worth taking the server down for.
        return False


def catalogued_loras(registry: Any) -> tuple[LoraEntry, ...]:
    """The same names, from Studio's own scan, for a server with no engine.

    THIS IS NOT A SECOND ANSWER. The engine derives a LoRA's name at
    `networks.py:163`:

        name = os.path.splitext(os.path.basename(filename))[0]

    -- the filename stem, nothing else -- and walks exactly `.pt`, `.ckpt` and
    `.safetensors`, which is `LORA_FORMAT_SUPPORT`. So the name Studio's
    catalogue produces for a file is provably the name the engine will resolve
    for that same file, and `<lora:NAME:1>` matches either way.

    This exists because `networks` is an EXTENSION module: it is importable
    only after the engine has loaded its extensions, and browsing LoRAs before
    loading a checkpoint is the ordinary case -- it is what the panel is for.
    Requiring a warm engine to list them would make the browser empty at
    exactly the moment an owner opens it.

    The engine's registry is still preferred when it is there, because it also
    carries `alias` from the file's own metadata, which a filename cannot.
    """

    try:
        entries = registry.entries("lora")
    except Exception:  # noqa: BLE001 - a missing catalogue is an empty one
        return ()

    found: list[LoraEntry] = []
    seen: set[str] = set()
    for entry in entries or ():
        name = str(getattr(entry, "display_name", "") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        found.append(LoraEntry(name=name, alias=name))
    found.sort(key=lambda item: item.name.lower())
    return tuple(found)


def available_loras(registry: Any) -> tuple[LoraEntry, ...]:
    """Every LoRA under the configured root, by whichever route can answer.

    The engine's registry first -- it carries the alias. Studio's own scan when
    the engine is cold, which is the normal state while an owner is browsing.
    """

    roots = configured_roots(registry)
    if not roots:
        return ()
    if not project_roots(roots):
        return catalogued_loras(registry)

    try:
        from importlib import import_module

        networks = import_module("networks")
    except Exception:  # noqa: BLE001
        try:
            import sys

            networks = sys.modules.get("networks")
            if networks is None:
                # `networks` is an extension module and this is a cold server.
                return catalogued_loras(registry)
        except Exception:  # noqa: BLE001
            return catalogued_loras(registry)

    try:
        # AFTER the projection. Startup ran this against the default
        # directory, so without a refresh the answer would ignore the root the
        # owner just configured and say nothing about it.
        networks.list_available_networks()
        found = networks.available_networks
    except (Exception, SystemExit):
        return catalogued_loras(registry)
    if not found:
        # The engine is up but knows of none -- which on a configured root
        # means its scan did not see what Studio's did. Answer from the scan
        # that did rather than report an empty folder.
        return catalogued_loras(registry)

    entries: list[LoraEntry] = []
    seen: set[str] = set()
    for candidate in (found or {}).values():
        name = str(getattr(candidate, "name", "") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        entries.append(LoraEntry(name=name,
                                 alias=str(getattr(candidate, "alias", "") or name)))
    entries.sort(key=lambda entry: entry.name.lower())
    return tuple(entries)


__all__ = ("LoraEntry", "available_loras", "catalogued_loras",
           "configured_roots", "project_roots")
