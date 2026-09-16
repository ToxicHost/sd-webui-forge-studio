"""Tier-2 compute plugins: UI-free samplers and schedulers, loaded on purpose.

The owner's Forge install registers roughly thirty custom samplers and
schedulers -- BFS, Beta 57, Parasite, Grimoire, Adams-Bashforth and the rest --
because Forge runs `load_scripts()` over every installed extension at startup.
Studio does not run that loader and must not: it scans arbitrary directories,
imports arbitrary modules, builds Gradio UI, and mutates global state.

This is the narrow seam instead. A compute plugin is a module that registers
samplers or schedulers into the real backend registry and does nothing else:

```text
no Gradio            no WebUI component construction
no network           no shell
no UI script runner  no uncontrolled filesystem mutation
```

Studio owns the directory, so what loads is what the owner put there rather than
whatever a scan happened to find. Registration happens at module import, which
is how these plugins are written -- `samplers_bfs.py` ends with
`_dedupe_and_register(_ENTRIES)` and `schedulers_beta.py` with `_register()` --
so importing the module IS the registration.

TWO PROPERTIES MATTER MORE THAN LOADING ANYTHING AT ALL:

* **A bad plugin cannot take the registry with it.** Each import is isolated.
  One that raises is recorded and the rest still load; the built-in samplers are
  already registered before this runs and are never at risk.
* **A plugin that imports Gradio is refused, loudly.** It is checked from
  `sys.modules` after the import rather than by reading the source, because the
  defect R1.5 removed was a transitive edge four modules deep. A plugin that
  drags Gradio in has broken the product's hardest invariant, and reporting it
  as "loaded" would hide that.

Nothing here is specific to BFS or Beta 57. Those are simply the two files in
the directory.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where Studio keeps its compute plugins. Studio-owned, inside the install,
#: NOT under the state root: these are code, and the state root holds the
#: owner's data.
PLUGIN_DIRECTORY_NAME = "studio_plugins"

LOADED = "loaded"
FAILED = "failed"
REFUSED_GRADIO = "refused_gradio"


@dataclass(frozen=True)
class PluginResult:
    """What one plugin did. Enough to act on, never a traceback."""

    name: str
    state: str
    samplers_added: int = 0
    schedulers_added: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == LOADED


def _gradio_count() -> int:
    return len([n for n in sys.modules
                if n == "gradio" or n.startswith("gradio.")])


def _registry_sizes() -> tuple[int, int]:
    """Current sampler and scheduler counts, or zeros if unreachable."""

    samplers = sys.modules.get("modules.sd_samplers")
    schedulers = sys.modules.get("modules.sd_schedulers")
    return (
        len(getattr(samplers, "all_samplers", ()) or ()),
        len(getattr(schedulers, "schedulers", ()) or ()),
    )


def plugin_directory(repository_root: Path) -> Path:
    return Path(repository_root) / PLUGIN_DIRECTORY_NAME


def discover(repository_root: Path) -> tuple[Path, ...]:
    """Plugin files, in a stable order. Never recurses.

    Sorted so two machines load in the same order, and flat so a plugin cannot
    smuggle a package of its own alongside it. Files beginning with `_` are
    skipped, which is how a plugin is disabled without deleting it.
    """

    directory = plugin_directory(repository_root)
    if not directory.is_dir():
        return ()
    return tuple(
        path for path in sorted(directory.glob("*.py"))
        if not path.name.startswith("_")
    )


def load_compute_plugins(repository_root: Path) -> tuple[PluginResult, ...]:
    """Import every plugin, isolated. Returns one result per file.

    Call AFTER the built-in registries exist: these modules extend
    `sd_samplers.all_samplers`, so the list has to be there to extend.
    """

    results: list[PluginResult] = []
    for path in discover(repository_root):
        name = path.stem
        before_samplers, before_schedulers = _registry_sizes()
        gradio_before = _gradio_count()

        module_name = f"studio_plugins.{name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("no import spec")
            module = importlib.util.module_from_spec(spec)
            # Registered in sys.modules BEFORE execution so a plugin that
            # imports itself, or is imported twice, resolves to one module.
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except BaseException as error:  # noqa: BLE001 - one plugin never kills the rest
            sys.modules.pop(module_name, None)
            results.append(PluginResult(
                name=name, state=FAILED,
                reason=f"{type(error).__name__}: {error}"[:200],
            ))
            continue

        if _gradio_count() > gradio_before:
            # The one refusal that is not merely a failure. The plugin loaded
            # and registered, and it also broke the product's hardest
            # invariant, so it is reported as refused rather than as working.
            results.append(PluginResult(
                name=name, state=REFUSED_GRADIO,
                reason=(
                    f"{_gradio_count() - gradio_before} real Gradio modules "
                    "were imported; a Studio compute plugin must import none"
                ),
            ))
            continue

        after_samplers, after_schedulers = _registry_sizes()
        results.append(PluginResult(
            name=name, state=LOADED,
            samplers_added=max(0, after_samplers - before_samplers),
            schedulers_added=max(0, after_schedulers - before_schedulers),
        ))
    return tuple(results)


__all__ = (
    "FAILED",
    "LOADED",
    "PLUGIN_DIRECTORY_NAME",
    "REFUSED_GRADIO",
    "PluginResult",
    "discover",
    "load_compute_plugins",
    "plugin_directory",
)
