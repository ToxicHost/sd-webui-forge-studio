"""Workflows and layouts, kept in the state root. F0.

`workflows.js` (Workflow Profiles: saved generation setups) and the layout
customiser (`app.js` `LayoutFiles`) list, read, save and delete at
`/studio/workflows` and `/studio/layouts`. Standalone answered every listing
with `[]` and every save with 404 ("Canonical Studio route is unavailable"),
so the Save buttons on both failed. The note that blocked them -- no durable
state folder -- was answered long ago (Develop presets and the Gallery live
in the state root), so this is the Extension's handlers
(`scripts/studio_api.py`: `_workflow_*` :400-446 with the routes :4006-4104;
`_layout_*` / `_validate_layout_map` :515-588 with the routes :3534-3599),
ported:

- the same ids (`wf_<ms>_<6 hex>`), id and slug rules, field limits (name
  200, description 1000, family 32), layout size cap (64 KB) and shape check,
  the same reply shapes the page reads;
- corrupt or unreadable files are skipped from a listing, never fatal;
- writes are atomic (temp file + replace).

ONE DIVERGENCE, as Develop presets: where they live. The Extension writes
`<studio root>/workflows/` and `<studio root>/studio_layouts/` inside its
install; Standalone keeps durable owner data in `<state root>/workflows/` and
`<state root>/layouts/`, which survive an update. The file formats are the
Extension's, so a file copied across loads as is.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WORKFLOW_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_WORKFLOW_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")
_LAYOUT_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_LAYOUT_MAX_BYTES = 64 * 1024
_LAYOUT_MAX_BLOCK_IDS = 200


class SetupRefusal(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _folder(state_root: Any, name: str, *, create: bool) -> Path | None:
    if not state_root:
        return None
    folder = Path(state_root) / name
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_atomic(folder: Path, target: Path, data: Any) -> None:
    handle, temporary = tempfile.mkstemp(dir=str(folder), prefix=".setup_")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        os.replace(temporary, target)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise SetupRefusal(f"The file could not be written: {error.strerror or error}.", 500) from None


# ---- workflows --------------------------------------------------------------

def _workflow_path(folder: Path, workflow_id: Any) -> Path:
    workflow_id = str(workflow_id or "")
    if not _WORKFLOW_ID_RE.match(workflow_id):
        raise SetupRefusal("Invalid workflow id")
    safe = _WORKFLOW_NAME_RE.sub("-", workflow_id).strip(".-")
    if not safe:
        raise SetupRefusal("Invalid workflow id")
    target = (folder / f"{safe}.json")
    if target.resolve().parent != folder.resolve():
        raise SetupRefusal("Invalid workflow id")
    return target


def _metadata(wf: dict) -> dict:
    return {
        "id": wf.get("id"),
        "name": wf.get("name") or "",
        "description": wf.get("description") or "",
        "family": wf.get("family") or "any",
        "created_at": wf.get("created_at"),
        "updated_at": wf.get("updated_at"),
        "options": wf.get("options") or {},
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def list_workflows(state_root: Any) -> list[dict]:
    folder = _folder(state_root, "workflows", create=False)
    items: list[dict] = []
    if folder is None or not folder.is_dir():
        return items
    for path in sorted(folder.glob("*.json")):
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(wf, dict):
            items.append(_metadata(wf))
    return items


def get_workflow(state_root: Any, workflow_id: str) -> dict:
    folder = _folder(state_root, "workflows", create=False)
    if folder is None:
        raise SetupRefusal("Not found", 404)
    path = _workflow_path(folder, workflow_id)
    if not path.is_file():
        raise SetupRefusal("Not found", 404)
    try:
        wf = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupRefusal("Workflow read failed", 500) from None
    if not isinstance(wf, dict):
        raise SetupRefusal("Malformed workflow", 500)
    return wf


def save_workflow(state_root: Any, req: Any) -> dict:
    if not isinstance(req, dict):
        raise SetupRefusal("Invalid payload")
    settings = req.get("settings")
    if not isinstance(settings, dict):
        raise SetupRefusal("settings must be an object")
    dynamic = req.get("dynamic")
    if dynamic is not None and not isinstance(dynamic, dict):
        raise SetupRefusal("dynamic must be an object")
    options = req.get("options")
    if options is not None and not isinstance(options, dict):
        raise SetupRefusal("options must be an object")
    folder = _folder(state_root, "workflows", create=True)
    if folder is None:
        raise SetupRefusal("Studio has no state folder to keep workflows in.", 503)
    wf_id = req.get("id") or f"wf_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    path = _workflow_path(folder, wf_id)
    now = _now_iso()
    created = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                created = existing.get("created_at")
        except (OSError, ValueError):
            created = None
    try:
        version = int(req.get("version") or 1)
    except (TypeError, ValueError):
        raise SetupRefusal("version must be a number") from None
    wf = {
        "version": version,
        "id": wf_id,
        "name": str(req.get("name") or "")[:200],
        "description": str(req.get("description") or "")[:1000],
        "family": str(req.get("family") or "any")[:32],
        "created_at": created or now,
        "updated_at": now,
        "options": options or {},
        "settings": settings,
        "dynamic": dynamic or {},
    }
    _write_atomic(folder, path, wf)
    return {"ok": True, "id": wf_id, "metadata": _metadata(wf)}


def delete_workflow(state_root: Any, workflow_id: str) -> dict:
    folder = _folder(state_root, "workflows", create=False)
    if folder is None:
        return {"ok": True}
    path = _workflow_path(folder, workflow_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise SetupRefusal("Workflow delete failed", 500) from None
    return {"ok": True}


# ---- layouts ----------------------------------------------------------------

def _layout_path(folder: Path, name: Any) -> tuple[str, Path]:
    slug = str(name or "").strip().lower()
    if not _LAYOUT_NAME_RE.match(slug):
        raise SetupRefusal("invalid layout name")
    target = folder / f"{slug}.json"
    if target.resolve().parent != folder.resolve():
        raise SetupRefusal("invalid layout name")
    return slug, target


def validate_layout_map(m: Any) -> str | None:
    """The Extension's shape check, unchanged: an error string or None."""

    if not isinstance(m, dict):
        return "map must be an object"
    schema = m.get("schema", 1)
    if isinstance(schema, bool) or not isinstance(schema, int):
        return "schema must be an integer"
    n_ids = 0
    zones = m.get("zones", {})
    if not isinstance(zones, dict):
        return "zones must be an object"
    for zname, blocks in zones.items():
        if not isinstance(zname, str) or not isinstance(blocks, list):
            return "zones must map names to lists"
        for b in blocks:
            if not isinstance(b, str) or not _LAYOUT_NAME_RE.match(b):
                return "invalid block id in zones"
            n_ids += 1
    hidden = m.get("hidden", [])
    if not isinstance(hidden, list):
        return "hidden must be a list"
    for b in hidden:
        if not isinstance(b, str) or not _LAYOUT_NAME_RE.match(b):
            return "invalid block id in hidden"
        n_ids += 1
    if n_ids > _LAYOUT_MAX_BLOCK_IDS:
        return "too many block ids"
    pw = m.get("panelWidth", 0)
    if isinstance(pw, bool) or not isinstance(pw, int) or (pw != 0 and not (420 <= pw <= 900)):
        return "panelWidth must be 0 or 420-900"
    strip = m.get("strip", {})
    if not isinstance(strip, dict) or any(not isinstance(v, bool) for v in strip.values()):
        return "strip must be an object of booleans"
    if not isinstance(m.get("module", "canvas"), str) or not isinstance(m.get("base", "classic"), str):
        return "module/base must be strings"
    return None


def list_layouts(state_root: Any) -> dict:
    folder = _folder(state_root, "layouts", create=False)
    items: list[dict] = []
    if folder is not None and folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            if not _LAYOUT_NAME_RE.match(path.stem):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append({"name": path.stem, "module": data.get("module", "canvas"),
                              "base": data.get("base", "classic"), "mtime": int(path.stat().st_mtime)})
            except (OSError, ValueError, AttributeError):
                continue
    return {"layouts": items}


def get_layout(state_root: Any, name: str) -> dict:
    folder = _folder(state_root, "layouts", create=False)
    if folder is None:
        raise SetupRefusal("not found", 404)
    _, path = _layout_path(folder, name)
    if not path.is_file():
        raise SetupRefusal("not found", 404)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupRefusal("unreadable layout file", 500) from None


def save_layout(state_root: Any, req: Any) -> dict:
    if not isinstance(req, dict):
        raise SetupRefusal("Invalid payload")
    folder = _folder(state_root, "layouts", create=True)
    if folder is None:
        raise SetupRefusal("Studio has no state folder to keep layouts in.", 503)
    slug, path = _layout_path(folder, req.get("name"))
    layout = req.get("map", {})
    try:
        size = len(json.dumps(layout))
    except (TypeError, ValueError):
        raise SetupRefusal("map is not JSON-serializable") from None
    if size > _LAYOUT_MAX_BYTES:
        raise SetupRefusal("layout too large", 413)
    error = validate_layout_map(layout)
    if error:
        raise SetupRefusal(error)
    data = dict(layout)
    data["name"] = slug
    _write_atomic(folder, path, data)
    return {"ok": True, "name": slug}


def delete_layout(state_root: Any, name: str) -> dict:
    folder = _folder(state_root, "layouts", create=False)
    if folder is None:
        raise SetupRefusal("not found", 404)
    _, path = _layout_path(folder, name)
    if not path.is_file():
        raise SetupRefusal("not found", 404)
    try:
        path.unlink()
    except OSError:
        raise SetupRefusal("delete failed", 500) from None
    return {"ok": True}


# ---- one dispatcher for the server ------------------------------------------

def route(method: str, path: str, state_root: Any, body: Any = None) -> tuple[int, Any] | None:
    """(status, reply) for a workflows / layouts route, or None for any other
    path. Refusals come back as `{"error": message}` with their status, the
    shape the page reads."""

    from urllib.parse import unquote

    try:
        if path == "/studio/workflows":
            if method == "GET":
                return 200, list_workflows(state_root)
            if method == "POST":
                return 200, save_workflow(state_root, body)
        elif path.startswith("/studio/workflows/"):
            wf_id = unquote(path[len("/studio/workflows/"):])
            if method == "GET":
                return 200, get_workflow(state_root, wf_id)
            if method == "DELETE":
                return 200, delete_workflow(state_root, wf_id)
        elif path == "/studio/layouts":
            if method == "GET":
                return 200, list_layouts(state_root)
            if method == "POST":
                return 200, save_layout(state_root, body)
        elif path.startswith("/studio/layouts/"):
            name = unquote(path[len("/studio/layouts/"):])
            if method == "GET":
                return 200, get_layout(state_root, name)
            if method == "DELETE":
                return 200, delete_layout(state_root, name)
        else:
            return None
    except SetupRefusal as refusal:
        return refusal.status, {"error": refusal.message}
    return None


__all__ = ("SetupRefusal", "route", "list_workflows", "get_workflow", "save_workflow", "delete_workflow",
           "list_layouts", "get_layout", "save_layout", "delete_layout", "validate_layout_map")
