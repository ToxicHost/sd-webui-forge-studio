"""Civitai metadata lookup for the LoRA browser (opt-in, hash-based). P6.

The Extension's `scripts/studio_civitai.py`, ported nearly verbatim -- it is
self-contained Python (urllib, hashlib, json) and its privacy rules are the
point of it:

  * default OFF: the page never calls these routes until the owner enables
    "Civitai metadata lookup" in Settings;
  * lookups are by SHA-256 of the file -- a filename is NEVER sent;
  * a per-file `private` flag is honoured server-side: a private LoRA is never
    looked up, even if asked.

Cache layout, unchanged so an Extension cache is reused as-is:

    <lora root>/.civitai_cache/
        hashes.json          { rel_path: {mtime, size, hash} }
        <hash>.json          metadata for one LoRA
        <hash>.preview.<ext> downloaded preview

Changed for Standalone, recorded in Evidence/source-review/P6-civitai-lookup.md:
names resolve through the catalogue (`model_browser.lora_model_id`), the cache
root is the configured LoRA root holding the file, and a cached preview is
served by LoRA name (`/studio/civitai_preview?name=`) rather than `/file=`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("studio.civitai")

# civitai.com first; civitai.red ONLY on a clean 404, never on a connection
# error, so a transient failure cannot send a lookup to the other host.
CIVITAI_HOSTS = ("https://civitai.com", "https://civitai.red")
BY_HASH_PATH = "/api/v1/model-versions/by-hash/{hash}"
USER_AGENT = "Forge-Studio (https://github.com/ToxicHost/Forge-Studio)"
REQUEST_TIMEOUT = 30
HASH_CHUNK_BYTES = 1 << 20
CACHE_DIR_NAME = ".civitai_cache"
HASH_INDEX_NAME = "hashes.json"
ALLOWED_PREVIEW_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PREVIEW_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".webp": "image/webp"}
MAX_PREVIEW_BYTES = 8 * 1024 * 1024


# -- cache helpers (Extension :72-135) -------------------------------------

def _cache_dir(root: Path, *, create: bool = True) -> Path:
    directory = Path(root) / CACHE_DIR_NAME
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _meta_path(root: Path, sha: str) -> Path:
    return _cache_dir(root) / f"{sha}.json"


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            loaded = json.load(stream)
        return loaded if isinstance(loaded, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _load_hash_index(root: Path) -> Dict[str, Dict[str, Any]]:
    index = Path(root) / CACHE_DIR_NAME / HASH_INDEX_NAME
    data = _read_json(index) if index.is_file() else None
    return data if isinstance(data, dict) else {}


def _relative_key(root: Path, path: Path) -> str:
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return str(path).replace(os.sep, "/")


def cached_hash(root: Path, path: Path) -> Optional[str]:
    """The indexed hash when the file is unchanged -- never computes."""

    try:
        stat = os.stat(path)
    except OSError:
        return None
    entry = _load_hash_index(root).get(_relative_key(root, path))
    if (isinstance(entry, dict) and entry.get("mtime") == stat.st_mtime
            and entry.get("size") == stat.st_size
            and isinstance(entry.get("hash"), str) and len(entry["hash"]) == 64):
        return entry["hash"]
    return None


def get_or_compute_hash(root: Path, path: Path) -> str:
    """Extension :149-182: the index when valid, otherwise hash and record."""

    known = cached_hash(root, path)
    if known:
        return known
    stat = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    sha = digest.hexdigest()
    index = _load_hash_index(root)
    index[_relative_key(root, path)] = {"mtime": stat.st_mtime, "size": stat.st_size,
                                        "hash": sha}
    try:
        _write_json_atomic(_cache_dir(root) / HASH_INDEX_NAME, index)
    except OSError as error:
        log.warning("Could not write the Civitai hash index: %s", error)
    return sha


# -- Civitai API (Extension :216-266) ---------------------------------------

class CivitaiError(Exception):
    pass


class CivitaiNotFound(CivitaiError):
    pass


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise CivitaiNotFound("Hash not found on Civitai (HTTP 404)") from None
        raise CivitaiError(f"HTTP {error.code}: {error.reason}") from None
    except urllib.error.URLError as error:
        raise CivitaiError(f"Network error: {error.reason}") from None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CivitaiError(f"Invalid JSON from Civitai: {error}") from None


def _http_get_bytes(url: str, byte_cap: int = MAX_PREVIEW_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = response.read(byte_cap + 1)
    except urllib.error.HTTPError as error:
        raise CivitaiError(f"Preview HTTP {error.code}: {error.reason}") from None
    except urllib.error.URLError as error:
        raise CivitaiError(f"Preview network error: {error.reason}") from None
    if len(data) > byte_cap:
        raise CivitaiError(f"Preview exceeds {byte_cap // 1024} KB cap")
    return data


def _ext_from_url(url: str) -> str:
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return ext if ext in ALLOWED_PREVIEW_EXTS else ".jpg"


def _empty_entry(sha: str, fetched_at: float, *, not_found: bool) -> dict:
    return {"schema": 1, "hash": sha, "fetched_at": fetched_at, "private": False,
            "not_found": not_found, "source_host": "", "model_id": None,
            "version_id": None, "model_name": "", "version_name": "", "base_model": "",
            "trigger_words": [], "author": "", "source_url": "", "description": "",
            "preview_url": "", "preview_local": ""}


def _shape_metadata(sha: str, response: dict, fetched_at: float, *, private: bool,
                    source_host: str) -> dict:
    """Extension :269-324."""

    model = response.get("model") or {}
    preview_url = None
    for image in response.get("images") or []:
        if not isinstance(image, dict):
            continue
        if image.get("type") and image["type"] != "image":
            continue
        if image.get("url"):
            preview_url = image["url"]
            break
    model_id, version_id = response.get("modelId"), response.get("id")
    words = response.get("trainedWords") or []
    entry = _empty_entry(sha, fetched_at, not_found=False)
    entry.update({
        "private": bool(private), "source_host": source_host, "model_id": model_id,
        "version_id": version_id, "model_name": model.get("name") or "",
        "version_name": response.get("name") or "",
        "base_model": response.get("baseModel") or "",
        "trigger_words": [str(w) for w in words] if isinstance(words, list) else [],
        "author": (response.get("creator") or {}).get("username") or "",
        "source_url": (f"{source_host}/models/{model_id}?modelVersionId={version_id}"
                       if model_id and version_id else ""),
        "description": response.get("description") or "",
        "preview_url": preview_url or "",
    })
    return entry


def _lookup_by_hash(sha: str) -> Tuple[dict, str]:
    last_404: Optional[CivitaiNotFound] = None
    for host in CIVITAI_HOSTS:
        try:
            return _http_get_json(host + BY_HASH_PATH.format(hash=sha)), host
        except CivitaiNotFound as error:
            last_404 = error
    raise last_404 or CivitaiNotFound("All hosts returned 404")


# -- per-LoRA (Extension :354-475) -------------------------------------------

def load_cached_entry(root: Path, sha: str) -> Optional[dict]:
    path = Path(root) / CACHE_DIR_NAME / f"{sha}.json"
    return _read_json(path) if path.is_file() else None


def set_private_flag(root: Path, sha: str, private: bool) -> dict:
    entry = load_cached_entry(root, sha) or _empty_entry(sha, 0.0, not_found=False)
    entry["private"] = bool(private)
    _write_json_atomic(_meta_path(root, sha), entry)
    return entry


def clear_cache(root: Path, sha: str) -> bool:
    removed = False
    cache = Path(root) / CACHE_DIR_NAME
    for candidate in [cache / f"{sha}.json",
                      *(cache / f"{sha}.preview{ext}" for ext in (".jpg", ".jpeg", ".png", ".webp"))]:
        if candidate.is_file():
            try:
                candidate.unlink()
                removed = True
            except OSError:
                pass
    return removed


def fetch_one(path: Path, root: Path, *, download_preview: bool = True) -> dict:
    sha = get_or_compute_hash(root, path)
    existing = load_cached_entry(root, sha)
    if existing and existing.get("private"):
        return existing          # never touch the network for a private LoRA
    try:
        response, host = _lookup_by_hash(sha)
    except CivitaiNotFound:
        entry = _empty_entry(sha, time.time(), not_found=True)
        _write_json_atomic(_meta_path(root, sha), entry)
        return entry
    entry = _shape_metadata(sha, response, time.time(), private=False, source_host=host)
    if download_preview and entry.get("preview_url"):
        destination = _cache_dir(root) / f"{sha}.preview{_ext_from_url(entry['preview_url'])}"
        try:
            destination.write_bytes(_http_get_bytes(entry["preview_url"]))
            entry["preview_local"] = destination.name
        except CivitaiError as error:
            log.info("Civitai preview download failed for %s: %s", sha[:10], error)
    _write_json_atomic(_meta_path(root, sha), entry)
    return entry


def cached_preview(root: Path, sha: str) -> Optional[Path]:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = Path(root) / CACHE_DIR_NAME / f"{sha}.preview{ext}"
        if candidate.is_file():
            return candidate
    return None


def public_view(entry: dict, name: str, root: Path) -> dict:
    """Extension `_public_view` (:483-508): internals stripped, and the
    preview by LoRA NAME rather than `/file=`."""

    preview = ""
    local = entry.get("preview_local") or ""
    if local and (Path(root) / CACHE_DIR_NAME / local).is_file():
        preview = "/studio/civitai_preview?name=" + urllib.parse.quote(name, safe="")
    return {
        "fetched_at": entry.get("fetched_at") or 0.0,
        "private": bool(entry.get("private")),
        "not_found": bool(entry.get("not_found")),
        "source_host": entry.get("source_host") or "",
        "model_id": entry.get("model_id"),
        "version_id": entry.get("version_id"),
        "model_name": entry.get("model_name") or "",
        "version_name": entry.get("version_name") or "",
        "base_model": entry.get("base_model") or "",
        "trigger_words": list(entry.get("trigger_words") or []),
        "author": entry.get("author") or "",
        "source_url": entry.get("source_url") or "",
        "description": entry.get("description") or "",
        "preview": preview,
    }


# -- Standalone resolution -----------------------------------------------------

def locate(registry: Any, name: str) -> Optional[Tuple[Path, Path]]:
    """`(file, the configured LoRA root holding it)` for an engine LoRA name."""

    from forge_headless.lora_catalogue import configured_roots

    from .model_browser import lora_model_id, model_file

    path = model_file(registry, "lora", lora_model_id(registry, name) or "")
    if path is None:
        return None
    resolved = path.resolve()
    for root in configured_roots(registry):
        base = Path(root).resolve()
        try:
            resolved.relative_to(base)
            return path, base
        except ValueError:
            continue
    return None


def enrich_cards(registry: Any, cards: List[dict]) -> List[dict]:
    """Extension `enrich_lora_entries` (:548-608): cached data only, never a
    network call, and the owner's own preview and sidecar always win."""

    for card in cards:
        found = locate(registry, str(card.get("name") or ""))
        if found is None:
            continue
        path, root = found
        sha = cached_hash(root, path)
        meta = load_cached_entry(root, sha) if sha else None
        if not meta:
            continue
        view = public_view(meta, str(card["name"]), root)
        card["civitai"] = view
        if not card.get("preview") and view.get("preview"):
            card["preview"] = view["preview"]
        if not card.get("activation_text") and view.get("trigger_words"):
            card["activation_text"] = ", ".join(view["trigger_words"])
        if not card.get("base_model") and view.get("base_model"):
            card["base_model"] = view["base_model"]
    return cards


def read_cached_preview(registry: Any, name: str) -> Optional[Tuple[bytes, str]]:
    found = locate(registry, name)
    if found is None:
        return None
    path, root = found
    sha = cached_hash(root, path)
    preview = cached_preview(root, sha) if sha else None
    if preview is None:
        return None
    try:
        return preview.read_bytes(), PREVIEW_MEDIA[preview.suffix.lower()]
    except (OSError, KeyError):
        return None


def refresh_one(registry: Any, name: str) -> dict:
    """Extension `_refresh_one` (:632-654)."""

    found = locate(registry, name)
    if found is None:
        return {"ok": False, "name": name, "error": "LoRA file not found"}
    path, root = found
    try:
        entry = fetch_one(path, root)
    except CivitaiError as error:
        return {"ok": False, "name": name, "error": str(error)}
    except OSError as error:
        return {"ok": False, "name": name, "error": f"File read error: {error}"}
    view = public_view(entry, name, root)
    return {"ok": True, "name": name, "private": view["private"],
            "not_found": view["not_found"], "civitai": view}


def batch_names(registry: Any, body: dict) -> Optional[List[str]]:
    """`names`, or every LoRA in `folder`, or `all` -- None when none given."""

    if isinstance(body.get("names"), list):
        return [str(n) for n in body["names"] if isinstance(n, str)]
    if not (body.get("all") or body.get("folder")):
        return None
    folder = str(body.get("folder") or "").strip("/")
    names: List[str] = []
    try:
        entries = registry.entries("lora") if registry is not None else ()
    except Exception:  # noqa: BLE001
        entries = ()
    for entry in entries:
        relative = str(getattr(entry, "relative_location", "") or "").replace("\\", "/")
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        if folder and parent != folder and not parent.startswith(folder + "/"):
            continue
        names.append(str(entry.display_name))
    return names


def fetch_batch(registry: Any, body: dict) -> dict:
    """Extension `civitai_fetch_batch` (:663-757)."""

    names = batch_names(registry, body)
    if names is None:
        return {"ok": False, "error": "Need one of: names, folder, all"}
    skip_existing = body.get("skip_existing", True)
    fetched = skipped = not_found = 0
    errors: List[Dict[str, str]] = []
    for name in names:
        found = locate(registry, name)
        if found is None:
            errors.append({"name": name, "error": "Not found on disk"})
            continue
        path, root = found
        if skip_existing:
            try:
                sha = get_or_compute_hash(root, path)
            except OSError as error:
                errors.append({"name": name, "error": f"Hash failed: {error}"})
                continue
            existing = load_cached_entry(root, sha)
            if existing and (existing.get("fetched_at") or existing.get("not_found")
                             or existing.get("private")):
                skipped += 1
                continue
        try:
            entry = fetch_one(path, root)
        except (CivitaiError, OSError) as error:
            errors.append({"name": name, "error": str(error)})
            continue
        if entry.get("not_found"):
            not_found += 1
        else:
            fetched += 1
    return {"ok": True, "fetched": fetched, "not_found": not_found, "skipped": skipped,
            "errors": errors[:50], "total": len(names)}
