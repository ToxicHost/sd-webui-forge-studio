"""Develop's saved presets. P9.

`develop.js` lists and saves presets at `/studio/develop/presets`, which
Standalone did not serve (it returned 404, already listed in the tester feature
status). This is the Extension's pair of handlers (`studio_api.py:6018-6075`):
one JSON file per preset, named by a 1-64 character name of letters, digits,
space, `_` and `-`, holding the params dict, which must carry `_version`.

ONE DIVERGENCE: where they live. The Extension writes `presets/develop/`
inside its own install folder. Standalone keeps durable owner data in its
state root, which survives an update (`launch.py`'s rule that durable state
never lives inside the installation), so presets are `<state root>/presets/
develop/<name>.json`. The format is unchanged, so an Extension preset file
copied across loads as-is.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")


class PresetRefusal(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _folder(state_root: Any, *, create: bool) -> Path | None:
    if not state_root:
        return None
    folder = Path(state_root) / "presets" / "develop"
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def list_presets(state_root: Any) -> dict[str, Any]:
    """`{"presets": [{name, params}, ...]}`, sorted by file name; an unreadable
    or non-object file is skipped, as the Extension does."""

    folder = _folder(state_root, create=False)
    presets: list[dict[str, Any]] = []
    if folder is not None and folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                presets.append({"name": path.stem, "params": data})
    return {"presets": presets}


def save_preset(state_root: Any, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise PresetRefusal("The preset could not be read.")
    name = str(body.get("name") or "").strip()
    if not _NAME.match(name):
        raise PresetRefusal("Invalid name (1-64 chars: letters, digits, space, _ -)")
    params = body.get("params") or {}
    if not isinstance(params, dict) or "_version" not in params:
        raise PresetRefusal("Missing _version in params")
    folder = _folder(state_root, create=True)
    if folder is None:
        raise PresetRefusal("Studio has no state folder to keep presets in.", 503)
    target = (folder / f"{name}.json").resolve()
    if target.parent != folder.resolve():
        raise PresetRefusal("Invalid path")
    try:
        handle, temporary = tempfile.mkstemp(dir=str(folder), prefix=".preset_")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(params, stream, indent=2, ensure_ascii=False)
        os.replace(temporary, target)
    except OSError as error:
        raise PresetRefusal(f"The preset could not be written: {error.strerror or error}.",
                            500) from None
    return {"ok": True, "name": name}
