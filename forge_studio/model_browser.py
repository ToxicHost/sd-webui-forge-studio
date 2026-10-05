"""The Checkpoint browser's data: listing, previews by id, preview sidecars.

P4. `checkpoint-browser.js` fetched `/studio/checkpoints`, which Standalone did
not serve. This is the Extension's browser backend (`studio_api.py:5092-5234`)
over Studio's model catalogue:

  * `title` and `stem` are the catalogue's `model_id` -- the value the model
    dropdown holds (`app.js:1828`), so a card selects exactly what the
    dropdown would, and a preview edit names a model, never a path;
  * previews are found by the Extension's ladder (`_find_lora_preview`,
    `:4824-4832`) and served by id at `/studio/model_preview`, because
    Standalone serves no `/file=` paths;
  * `base_model` is read from a Civitai Helper `<stem>.civitai.info`, exactly
    as `read_ch_info` does (`studio_civitai.py:511-545`).

Source review: Evidence/source-review/P4-checkpoint-browser.md
"""

from __future__ import annotations

import base64
import binascii
import io
import json
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

#: `_find_lora_preview`'s ladder, in its order.
PREVIEW_LADDER = (".preview.png", ".preview.jpg", ".preview.jpeg", ".preview.webp",
                  ".png", ".jpg", ".jpeg", ".webp")
#: What "Remove preview" deletes: the sidecars, never a bare sibling image.
PREVIEW_SIDECARS = PREVIEW_LADDER[:4]
PREVIEW_MEDIA = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp"}
#: The Extension thumbnails a saved preview to this, keeping aspect.
PREVIEW_MAX_SIDE = 512
#: Roles the browser routes serve.
BROWSER_ROLES = ("checkpoint", "lora")
_ROLE_NOUN = {"checkpoint": "Checkpoint", "lora": "LoRA"}


class BrowserRefusal(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def model_file(registry: Any, role: str, model_id: str) -> Path | None:
    """The file behind one catalogue id, or None. Never leaves the server."""

    if registry is None or role not in BROWSER_ROLES or not model_id:
        return None
    try:
        _, source = registry.resolve(role, str(model_id))
        return Path(source.resolved)
    except Exception:  # noqa: BLE001 - unknown or unavailable is "none"
        return None


def _stem_path(path: Path) -> str:
    """`os.path.splitext(path)[0]`: only the last suffix goes."""

    text = str(path)
    dot = text.rfind(".")
    slash = max(text.rfind("/"), text.rfind("\\"))
    return text[:dot] if dot > slash + 1 else text


def find_preview(path: Path | None) -> Path | None:
    if path is None:
        return None
    base = _stem_path(path)
    for suffix in PREVIEW_LADDER:
        candidate = Path(base + suffix)
        if candidate.is_file():
            return candidate
    return None


def base_model_of(path: Path | None) -> str:
    """`read_ch_info(...)["base_model"]`, or "" -- never raises."""

    if path is None:
        return ""
    try:
        info = Path(_stem_path(path) + ".civitai.info")
        if not info.is_file():
            return ""
        text = info.read_text(encoding="utf-8").strip()
        raw = json.loads(text) if text else None
        return str(raw.get("baseModel") or "") if isinstance(raw, dict) else ""
    except Exception:  # noqa: BLE001 - "no data", as the Extension
        return ""


def preview_url(role: str, model_id: str, preview: Path) -> str:
    """By id, with the file's mtime so a new preview is not served stale."""

    try:
        version = int(preview.stat().st_mtime)
    except OSError:
        version = 0
    return (f"/studio/model_preview?role={quote(role)}&id={quote(str(model_id), safe='')}"
            f"&v={version}")


def checkpoint_listing(registry: Any) -> list[dict[str, Any]]:
    """The browser's cards, in the Extension's shape, ids instead of paths."""

    if registry is None:
        return []
    try:
        entries = registry.entries("checkpoint")
    except Exception:  # noqa: BLE001 - a browser never breaks the page
        return []
    cards: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for entry in entries:
        label = str(entry.display_name)
        count = seen.get(label, 0)
        seen[label] = count + 1
        if count:
            label = f"{label} ({count + 1})"
        path = model_file(registry, "checkpoint", entry.model_id)
        relative = PurePosixPath(str(getattr(entry, "relative_location", "") or "")
                                 .replace("\\", "/"))
        subfolder = "" if str(relative.parent) == "." else str(relative.parent)
        try:
            mtime = path.stat().st_mtime if path else 0
        except OSError:
            mtime = 0
        preview = find_preview(path)
        cards.append({
            "title": entry.model_id,
            "name": label,
            "hash": None,
            "stem": entry.model_id,
            "subfolder": subfolder,
            "size": int(getattr(entry, "size_bytes", 0) or 0),
            "mtime": mtime,
            "preview": preview_url("checkpoint", entry.model_id, preview) if preview else None,
            "base_model": base_model_of(path),
            "arch": "",
        })
    cards.sort(key=lambda card: (card["subfolder"].casefold(), card["name"].casefold()))
    return cards


# -- LoRA browser (P5) ----------------------------------------------------
#
# The Extension's `/studio/loras` (`studio_api.py:4848-4938`) walks the LoRA
# folders and reads, per file: the preview ladder, the a1111 user sidecar
# `<stem>.json` ("activation text", "preferred weight", "sd version"), then the
# Civitai Helper `<stem>.civitai.info` for whatever the user sidecar left
# empty. Standalone keeps its engine-resolved names (`available_loras`) and
# adds those same fields from the catalogue entry with the same name -- the
# engine's name IS the file stem (`lora_catalogue.catalogued_loras`). No path.


def lora_model_id(registry: Any, name: str) -> str | None:
    """The catalogue id of the LoRA the engine calls `name`."""

    if registry is None or not name:
        return None
    try:
        for entry in registry.entries("lora"):
            if str(entry.display_name) == name:
                return str(entry.model_id)
    except Exception:  # noqa: BLE001 - no catalogue is no match
        return None
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        loaded = json.loads(text) if text else {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001 - an unreadable sidecar is an empty one
        return {}


def lora_listing(registry: Any, names: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`names` is `/studio/loras`' existing `{name, alias}` list, enriched."""

    by_name: dict[str, Any] = {}
    try:
        for entry in (registry.entries("lora") if registry is not None else ()):
            by_name.setdefault(str(entry.display_name), entry)
    except Exception:  # noqa: BLE001
        by_name = {}
    out: list[dict[str, Any]] = []
    for item in names:
        name = str(item.get("name") or "")
        entry = by_name.get(name)
        card: dict[str, Any] = {"name": name, "alias": item.get("alias") or name,
                                "subfolder": "", "size": 0, "mtime": 0, "preview": None,
                                "activation_text": "", "preferred_weight": 0.0,
                                "base_model": ""}
        if entry is not None:
            path = model_file(registry, "lora", entry.model_id)
            relative = PurePosixPath(str(getattr(entry, "relative_location", "") or "")
                                     .replace("\\", "/"))
            card["subfolder"] = "" if str(relative.parent) == "." else str(relative.parent)
            card["size"] = int(getattr(entry, "size_bytes", 0) or 0)
            if path is not None:
                try:
                    card["mtime"] = path.stat().st_mtime
                except OSError:
                    pass
                preview = find_preview(path)
                if preview is not None:
                    card["preview"] = preview_url("lora", entry.model_id, preview)
                user = _read_json(Path(_stem_path(path) + ".json"))
                card["activation_text"] = str(user.get("activation text", "") or "")
                try:
                    card["preferred_weight"] = float(user.get("preferred weight", 0.0) or 0.0)
                except (TypeError, ValueError):
                    card["preferred_weight"] = 0.0
                card["base_model"] = str(user.get("sd version") or "").strip()
                helper = _read_json(Path(_stem_path(path) + ".civitai.info"))
                if helper:
                    if not card["base_model"]:
                        card["base_model"] = str(helper.get("baseModel") or "")
                    words = helper.get("trainedWords") or []
                    if not card["activation_text"] and isinstance(words, list):
                        card["activation_text"] = ", ".join(
                            str(w).strip() for w in words if str(w).strip())
        out.append(card)
    return out


def save_lora_metadata(registry: Any, body: dict[str, Any]) -> None:
    """The Extension's `/studio/lora_metadata` (`studio_api.py:5043-5086`):
    only the provided keys are written into `<stem>.json`, everything else in
    it is kept, an empty string clears a field."""

    import os
    import tempfile

    name = str(body.get("name") or "")
    if not name:
        raise BrowserRefusal("Missing name")
    path = model_file(registry, "lora", lora_model_id(registry, name) or "")
    if path is None:
        raise BrowserRefusal(f"LoRA not found: {name}", 404)
    sidecar = Path(_stem_path(path) + ".json")
    meta = _read_json(sidecar)
    if "activation_text" in body:
        meta["activation text"] = str(body.get("activation_text") or "")
    if "base_model" in body:
        meta["sd version"] = str(body.get("base_model") or "")
    if "preferred_weight" in body:
        try:
            meta["preferred weight"] = float(body.get("preferred_weight") or 0.0)
        except (TypeError, ValueError):
            meta["preferred weight"] = 0.0
    # Atomic, as `_atomic_write_json`: a half-written sidecar would lose the
    # owner's trigger words on the next read.
    try:
        handle, temporary = tempfile.mkstemp(dir=str(sidecar.parent), prefix=".lorameta_")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(meta, stream, indent=4, ensure_ascii=False)
        os.replace(temporary, sidecar)
    except OSError as error:
        raise BrowserRefusal(f"The metadata could not be written: {error.strerror or error}.",
                             500) from None


def read_preview(registry: Any, role: str, model_id: str) -> tuple[bytes, str] | None:
    preview = find_preview(model_file(registry, role, model_id))
    if preview is None:
        return None
    media = PREVIEW_MEDIA.get(preview.suffix.lower())
    if media is None:
        return None
    try:
        return preview.read_bytes(), media
    except OSError:
        return None


def save_preview(registry: Any, role: str, model_id: str, image_b64: Any) -> None:
    """`<stem>.preview.png` beside the model, at most 512 px, tagged sRGB."""

    from PIL import Image

    from forge_headless.live_generation_port import _srgb_icc_bytes

    if not model_id or not image_b64:
        raise BrowserRefusal("Missing name or image_b64")
    path = model_file(registry, role, model_id)
    if path is None:
        raise BrowserRefusal(f"{_ROLE_NOUN.get(role, 'Model')} not found: {model_id}", 404)
    text = str(image_b64)
    if "," in text:
        text = text.split(",", 1)[1]
    try:
        image = Image.open(io.BytesIO(base64.b64decode(text, validate=True)))
        image.load()
    except (binascii.Error, ValueError, OSError) as error:
        raise BrowserRefusal(f"Invalid image data: {error}") from None
    if image.width > PREVIEW_MAX_SIDE or image.height > PREVIEW_MAX_SIDE:
        image.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.LANCZOS)
    target = Path(_stem_path(path) + ".preview.png")
    profile = _srgb_icc_bytes()
    try:
        if profile:
            image.save(target, format="PNG", icc_profile=profile)
        else:
            image.save(target, format="PNG")
    except OSError as error:
        raise BrowserRefusal(f"The preview could not be written: {error.strerror or error}.",
                             500) from None


def delete_previews(registry: Any, role: str, model_id: str) -> bool:
    """Remove the `.preview.*` sidecars only. True when one was removed."""

    if not model_id:
        raise BrowserRefusal("Missing name")
    path = model_file(registry, role, model_id)
    if path is None:
        raise BrowserRefusal(f"{_ROLE_NOUN.get(role, 'Model')} not found: {model_id}", 404)
    base = _stem_path(path)
    removed = False
    for suffix in PREVIEW_SIDECARS:
        candidate = Path(base + suffix)
        if candidate.is_file():
            try:
                candidate.unlink()
                removed = True
            except OSError:
                pass
    return removed
