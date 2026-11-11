"""Studio runs without Gradio, everywhere, from the first import. GF1.

A tester's install has no Gradio (`requirements.txt`; Neo itself dropped it in
80fb8b33), but a development venv can still carry one from an older Forge. Then
vendored code that imports Gradio works on the developer's machine and fails
on every tester's. That is exactly how Neo's ControlNet was broken on fresh
installs from CN1 until 0.9.16 (Evidence/source-review/CN-G-controlnet-
without-gradio.md) while every test passed.

`install()` puts a finder at the front of `sys.meta_path` that refuses Gradio's
packages with the same `ModuleNotFoundError` a tester's Python raises. Code
written for an absent Gradio (`try: import gradio / except ImportError`)
behaves as it does for testers, and code that needs Gradio fails here first.
A module already imported before `install()` is left alone. Nothing refuses it
twice, and `application.gradio_in_process` still reports it.

Installed by `launch_studio.py` (the server process's entry), by
`forge_studio.launch.main` (which harnesses call directly) and by the test
package (`tests/__init__.py`).
"""

from __future__ import annotations

import importlib.abc
import sys
from typing import Any

#: Gradio and the packages that exist only alongside it.
REFUSED = frozenset({"gradio", "gradio_client", "gradio_rangeslider", "gradio_imageslider"})


class RefuseGradio(importlib.abc.MetaPathFinder):
    """Answers every Gradio import as a Python without Gradio would."""

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        root = name.partition(".")[0]
        if root in REFUSED:
            raise ModuleNotFoundError(
                f"No module named {root!r} (Studio runs without Gradio; see forge_studio/no_gradio.py)",
                name=root)
        return None


def install() -> RefuseGradio:
    """Idempotent: one finder, first in `sys.meta_path`."""

    for finder in sys.meta_path:
        if isinstance(finder, RefuseGradio) or type(finder).__name__ == "RefuseGradio":
            return finder
    finder = RefuseGradio()
    sys.meta_path.insert(0, finder)
    return finder
