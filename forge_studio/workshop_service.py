"""Workshop's routes: listings, the job runner, chains, and the History journal.

W1. `workshop.js` calls `/studio/workshop/*`, none of which Standalone served,
so the tab was gated off. This is the Extension's `setup_workshop_routes`
(`scripts/studio_workshop.py:4204-5224`) over Studio's model roots, cut to the
owner's scope (see `workshop_engine`).

What differs from the Extension, each deliberately:

  * Names, not paths. A model is named by its path RELATIVE to its configured
    root (the Extension's own display rule, `:4232-4243`), resolved back
    through the model registry, which re-checks containment. `/models` carries
    no `path` field, and error text is scrubbed of the configured folders.
  * Results are written to the FIRST configured checkpoint folder (the
    Extension used `models/Stable-diffusion`), which is rescanned when a job
    finishes, so the new model appears in every model list.
  * An output name is a bare file name; one that already exists is refused for
    every step, not only for a single merge -- the Extension's chain steps
    would overwrite.
  * A chain is validated before it starts (names resolve, references point
    backwards), so a typo is a 4xx now rather than an error two steps in.
  * The journal lives in `<state root>/workshop/`.
  * Progress is polled at `/status` (the page already polls every 2 s); the
    Extension also pushed it over its WebSocket.

Source review: Evidence/source-review/W1-workshop.md
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from forge_headless import workshop_engine as engine

_LISTED_FORMATS = ("safetensors", "sft")
_OUTPUT_DTYPES = ("auto", "fp16", "bf16", "fp32")
_STEP_TYPES = ("merge", "lora_bake", "vae_bake")
_REF = re.compile(r"^__O(\d+)__$")
_JOURNAL_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_JOURNAL_IMAGE = re.compile(r"^[A-Za-z0-9_-]+\.(png|jpg|webp)$")
_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NAT = re.compile(r"(\d+)")


class WorkshopRefusal(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _natural(text: str) -> list[Any]:
    return [int(c) if c.isdigit() else c for c in _NAT.split(text.lower())]


class WorkshopJob:
    """The one running operation: progress for `/status`, and Cancel."""

    IDLE = {"active": False, "progress": 0.0, "current_key": "", "keys_done": 0,
            "keys_total": 0, "status": "idle", "error": None, "elapsed": 0,
            "result": None, "chain_step": 0, "chain_total": 0}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state = dict(self.IDLE)

    def begin(self, **fields: Any) -> bool:
        """Claim the runner. False when something is already running."""

        with self._lock:
            if self._state["active"]:
                return False
            self._cancel.clear()
            self._state = {**self.IDLE, "active": True, "status": "running",
                           "started": datetime.now(timezone.utc).isoformat(), **fields}
            return True

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> bool:
        with self._lock:
            if not self._state["active"]:
                return False
        self._cancel.set()
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: self._state.get(k) for k in (
                "active", "progress", "keys_done", "keys_total", "status", "error",
                "elapsed", "result", "chain_step", "chain_total")}


class WorkshopService:
    def __init__(self, registry: Any, state_root: Any = None) -> None:
        self._registry = registry
        self._state_root = Path(state_root) if state_root else None
        self._journal_lock = threading.Lock()
        self.job = WorkshopJob()

    # -- listings and names -------------------------------------------------

    def _named(self, role: str) -> list[tuple[str, Any]]:
        """`[(name, entry)]`: the root-relative path, `(2)`-suffixed when two
        roots hold the same one. Merge inputs are safetensors only."""

        try:
            entries = self._registry.entries(role)
        except Exception:  # noqa: BLE001 - an unconfigured role lists nothing
            return []
        named: list[tuple[str, Any]] = []
        seen: dict[str, int] = {}
        for entry in entries:
            if str(getattr(entry, "format", "")).lower() not in _LISTED_FORMATS:
                continue
            name = str(PurePosixPath(str(entry.relative_location).replace("\\", "/")))
            count = seen.get(name, 0)
            seen[name] = count + 1
            named.append((f"{name} ({count + 1})" if count else name, entry))
        named.sort(key=lambda pair: _natural(pair[0]))
        return named

    def listing(self, role: str) -> list[dict[str, Any]]:
        out = []
        for name, entry in self._named(role):
            size = int(getattr(entry, "size_bytes", 0) or 0)
            item = {"filename": name, "basename": PurePosixPath(name).name}
            if role == "checkpoint":
                item["size_gb"] = round(size / (1024 ** 3), 2)
            else:
                item["size_mb"] = round(size / (1024 ** 2), 2)
            out.append(item)
        return out

    def resolve(self, role: str, name: Any) -> Path:
        noun = {"checkpoint": "Model", "lora": "LoRA", "vae": "VAE"}[role]
        text = str(name or "").strip()
        for candidate, entry in self._named(role):
            if candidate == text:
                try:
                    _, source = self._registry.resolve(role, entry.model_id)
                    return Path(source.resolved)
                except Exception:  # noqa: BLE001 - gone since the listing
                    break
        raise WorkshopRefusal(f"{noun} not found: {text}", 404)

    def output_folder(self) -> Path:
        try:
            catalogues = self._registry.catalogues("checkpoint")
        except Exception:  # noqa: BLE001
            catalogues = ()
        if not catalogues:
            raise WorkshopRefusal("No checkpoint folder is configured to save into.", 503)
        return Path(catalogues[0].root_key)

    def _output_path(self, requested: Any, default: str) -> Path:
        name = str(requested or "").strip() or default
        if not name.lower().endswith(".safetensors"):
            name += ".safetensors"
        stem = name[: -len(".safetensors")]
        if (not stem.strip(" .") or _BAD_NAME.search(name) or ".." in name
                or len(name) > 200):
            raise WorkshopRefusal("The output name must be a plain file name.")
        path = self.output_folder() / name
        if path.exists():
            raise WorkshopRefusal(f"Output file already exists: {name}", 409)
        return path

    def _scrub(self, text: str) -> str:
        """Error text never carries a configured folder."""

        roots: set[str] = set()
        for role in ("checkpoint", "lora", "vae"):
            try:
                roots.update(str(c.root_key) for c in self._registry.catalogues(role))
            except Exception:  # noqa: BLE001
                pass
        if self._state_root is not None:
            roots.add(str(self._state_root))
        for root in sorted(roots, key=len, reverse=True):
            text = re.sub(re.escape(root) + r"[\\/]?", "", text, flags=re.IGNORECASE)
        return text

    def _refresh(self, *roles: str) -> dict[str, int]:
        counts = {}
        for role in roles:
            try:
                self._registry.refresh(role)
            except Exception:  # noqa: BLE001 - a rescan never fails the route
                pass
            counts[role] = len(self._named(role))
        return counts

    # -- running a job --------------------------------------------------------

    def _start(self, work: Callable[[], dict[str, Any]], *, chain_total: int = 0) -> None:
        if not self.job.begin(chain_total=chain_total):
            raise WorkshopRefusal("A merge or bake is already in progress.", 409)
        started = time.time()

        def run() -> None:
            try:
                result = work()
                self.job.update(active=False, status="complete", progress=1.0, current_key="",
                                elapsed=round(time.time() - started, 1), result=result)
            except engine.Cancelled:
                self.job.update(active=False, status="cancelled",
                                elapsed=round(time.time() - started, 1))
            except Exception as error:  # noqa: BLE001 - reported to the page
                self.job.update(active=False, status="error",
                                error=self._scrub(str(error) or type(error).__name__),
                                elapsed=round(time.time() - started, 1))
            finally:
                self._refresh("checkpoint")

        threading.Thread(target=run, name="studio-workshop", daemon=True).start()

    # -- validation -----------------------------------------------------------

    @staticmethod
    def _number(value: Any, name: str, low: float, high: float, default: float) -> float:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise WorkshopRefusal(f"{name} must be a number.")
        if not low <= value <= high:
            raise WorkshopRefusal(f"{name} must be between {low:g} and {high:g}.")
        return float(value)

    def _merge_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        method = str(params.get("method") or "weighted_sum")
        if method not in engine.METHODS:
            raise WorkshopRefusal(f"Method '{method}' is not available. "
                                  f"Available: {', '.join(engine.METHODS)}.")
        weights = params.get("block_weights")
        if weights is not None:
            if not isinstance(weights, dict):
                raise WorkshopRefusal("block_weights must be an object.")
            weights = {str(k): self._number(v, f"Block {k}", 0.0, 1.0, 0.5)
                       for k, v in weights.items()}
        return {"method": method,
                "alpha": self._number(params.get("alpha"), "alpha", 0.0, 1.0, 0.5),
                "block_weights": weights or None,
                **self._dtype(params)}

    @staticmethod
    def _dtype(params: dict[str, Any]) -> dict[str, Any]:
        output_dtype = str(params.get("output_dtype") or "auto").lower()
        if output_dtype not in _OUTPUT_DTYPES:
            raise WorkshopRefusal(f"output_dtype must be one of {', '.join(_OUTPUT_DTYPES)}.")
        return {"save_fp16": params.get("save_fp16") is not False, "output_dtype": output_dtype}

    def _input(self, role: str, value: Any, outputs: dict[int, Path], step: int) -> tuple[Path, str]:
        """A concrete name, or `__O<n>__` naming an EARLIER step's output."""

        found = _REF.match(str(value or ""))
        if found:
            ref = int(found.group(1))
            if ref not in outputs or ref >= step:
                raise WorkshopRefusal(f"Step {step} refers to step {ref}, which comes later or does not exist.")
            return outputs[ref], outputs[ref].name
        if role == "checkpoint" and not str(value or "").strip():
            raise WorkshopRefusal(f"Step {step} is missing a model.")
        return self.resolve(role, value), str(value).strip()

    def _same_architecture(self, paths: list[tuple[str, Path]]) -> dict[str, Any]:
        """The Extension's cross-architecture refusal (`:4394-4414`)."""

        arch_a = engine.architecture_of(paths[0][1])
        for label, path in paths[1:]:
            other = engine.architecture_of(path)
            if other["arch"] != arch_a["arch"]:
                raise WorkshopRefusal(f"{label} architecture mismatch: "
                                      f"{arch_a['details']} ↔ {other['details']}")
        return arch_a

    # -- routes ---------------------------------------------------------------

    def handle_get(self, path: str, query: dict[str, list[str]]) -> tuple[int, Any]:
        def arg(key: str) -> str:
            return (query.get(key) or [""])[0]

        route = path[len("/studio/workshop"):]
        if route == "/models":
            return 200, self.listing("checkpoint")
        if route == "/loras":
            return 200, self.listing("lora")
        if route == "/vaes":
            return 200, self.listing("vae")
        if route == "/status":
            return 200, self.job.snapshot()
        if route == "/presets":
            arch = arg("arch") or "sdxl"
            return 200, {"arch": arch, "presets": engine.PRESETS.get(arch, engine.PRESETS["sdxl"]),
                         "blocks": engine.get_block_list(arch)}
        if route == "/inspect":
            name = arg("filename")
            return 200, engine.inspect_model(self.resolve("checkpoint", name), name.strip())
        if route in ("/preflight", "/compatibility"):
            a = self.resolve("checkpoint", arg("model_a"))
            b = self.resolve("checkpoint", arg("model_b"))
            return 200, (engine.estimate_merge_ram(a, b) if route == "/preflight"
                         else engine.check_compatibility(a, b))
        if route == "/journal":
            needle = arg("search").lower()
            try:
                limit = max(1, min(500, int(arg("limit") or 50)))
            except ValueError:
                limit = 50
            entries = self._load_journal()
            if needle:
                entries = [e for e in entries if needle in str(e.get("name", "")).lower()
                           or needle in str(e.get("notes", "")).lower()
                           or needle in json.dumps(e.get("recipe", {})).lower()]
            return 200, entries[:limit]
        raise WorkshopRefusal("Not found.", 404)

    def journal_image(self, filename: str) -> tuple[bytes, str] | None:
        if not _JOURNAL_IMAGE.match(filename or ""):
            return None
        folder = self._journal_images(create=False)
        target = folder / filename if folder else None
        if target is None or not target.is_file():
            return None
        return target.read_bytes(), _IMAGE_TYPES[filename.rsplit(".", 1)[1]]

    def handle_post(self, path: str, body: Any) -> tuple[int, Any]:
        route = path[len("/studio/workshop"):]
        body = body if isinstance(body, dict) else {}
        if route == "/merge":
            return 200, self._merge(body)
        if route == "/chain":
            return 200, self._chain(body)
        if route == "/cancel":
            return 200, ({"cancelled": True} if self.job.cancel()
                         else {"cancelled": False, "reason": "No active merge"})
        if route == "/refresh_checkpoints":
            return 200, {"ok": True, "count": self._refresh("checkpoint")["checkpoint"]}
        if route == "/refresh":
            counts = self._refresh("checkpoint", "lora", "vae")
            return 200, {"ok": True, "checkpoints": counts["checkpoint"],
                         "loras": counts["lora"], "vaes": counts["vae"]}
        if route.startswith("/journal/"):
            return 200, self._journal_post(route[len("/journal/"):], body)
        raise WorkshopRefusal("Not found.", 404)

    def _merge(self, body: dict[str, Any]) -> dict[str, Any]:
        """`/merge` (`:4371-4456`): one row, two concrete models."""

        settings = self._merge_settings(body)
        a = self.resolve("checkpoint", body.get("model_a"))
        b = self.resolve("checkpoint", body.get("model_b"))
        c = None
        if settings["method"] in engine.METHODS_NEEDING_C:
            if not str(body.get("model_c") or "").strip():
                raise WorkshopRefusal(f"{settings['method']} requires Model C "
                                      "(base model for difference)")
            c = self.resolve("checkpoint", body.get("model_c"))
        labelled = [("Model B", b)] + ([("Model C", c)] if c else [])
        architecture = self._same_architecture([("Model A", a), *labelled])
        output = self._output_path(body.get("output_name"), f"workshop_merge_{int(time.time())}")
        ram = engine.estimate_merge_ram(a, b)
        names = {"a": str(body.get("model_a")).strip(), "b": str(body.get("model_b")).strip()}
        if c is not None:
            names["c"] = str(body.get("model_c")).strip()

        def work() -> dict[str, Any]:
            result = engine.merge_models(self.job, a, b, output, path_c=c, names=names, **settings)
            self._journal_entry(f"merge_{int(time.time())}", result["filename"],
                                result["recipe"].get("method", "merge"), result["recipe"],
                                result["elapsed"])
            return {"filename": result["filename"], "elapsed": result["elapsed"],
                    "recipe": result["recipe"]}

        self._start(work)
        return {"started": True, "output": output.name, "method": settings["method"],
                "alpha": settings["alpha"], "block_weights": settings["block_weights"],
                "ram_estimate": ram, "architecture": architecture}

    def _chain(self, body: dict[str, Any]) -> dict[str, Any]:
        """`/chain` (`:5069-5094`) and `run_chain` (`:3683-3840`)."""

        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            raise WorkshopRefusal("No steps provided")
        save_intermediates = body.get("save_intermediates") is True
        board = body.get("workshop_board") if isinstance(body.get("workshop_board"), dict) else None
        stamp = int(time.time())
        planned: list[tuple[int, str, Callable[[], dict[str, Any]], Path]] = []
        outputs: dict[int, Path] = {}
        claimed: set[str] = set()
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or step.get("step") != index + 1:
                raise WorkshopRefusal(f"Step numbers must be sequential (expected {index + 1}).")
            kind = step.get("type")
            if kind not in _STEP_TYPES:
                raise WorkshopRefusal(f"Invalid step type: {kind}")
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            number = index + 1
            output = self._output_path(params.get("output_name"), f"chain_step{number}_{stamp}")
            if output.name.lower() in claimed:
                raise WorkshopRefusal(f"Two steps would both write {output.name}.")
            claimed.add(output.name.lower())
            planned.append((number, kind, self._plan_step(kind, number, params, outputs, output), output))
            outputs[number] = output

        def work() -> dict[str, Any]:
            total = len(planned)
            began = time.time()
            results: list[dict[str, Any]] = []
            written: list[Path] = []
            try:
                for number, kind, run, _output in planned:
                    if self.job.cancelled():
                        raise engine.Cancelled()
                    self.job.update(current_key=f"Step {number}/{total}: {kind}",
                                    chain_step=number, chain_total=total,
                                    progress=(number - 1) / total)
                    try:
                        result = run()
                    except engine.Cancelled:
                        raise
                    except Exception as error:  # noqa: BLE001
                        raise RuntimeError(f"Step {number} ({kind}) failed: {error}") from error
                    written.append(_output)
                    results.append({"step": number, "type": kind, **result})
            finally:
                if not save_intermediates:
                    # Only what THIS chain wrote, and never the final output.
                    final = planned[-1][3] if len(results) == len(planned) else None
                    for path in written:
                        if path != final:
                            try:
                                path.unlink()
                            except OSError:
                                pass
            final_name = results[-1]["filename"] if results else None
            self._journal_chain(results, round(time.time() - began, 1),
                                save_intermediates, final_name, board)
            return {"steps_completed": len(results), "total_steps": total,
                    "outputs": {r["step"]: r["filename"] for r in results},
                    "final_output": final_name}

        self._start(work, chain_total=len(planned))
        return {"started": True, "total_steps": len(planned)}

    def _plan_step(self, kind: str, number: int, params: dict[str, Any],
                   outputs: dict[int, Path], output: Path) -> Callable[[], dict[str, Any]]:
        """Resolve one step NOW, so a bad name refuses before anything runs."""

        dtype = self._dtype(params)
        if kind == "merge":
            settings = self._merge_settings(params)
            a, name_a = self._input("checkpoint", params.get("model_a"), outputs, number)
            b, name_b = self._input("checkpoint", params.get("model_b"), outputs, number)
            c = name_c = None
            if settings["method"] in engine.METHODS_NEEDING_C:
                if not params.get("model_c"):
                    raise WorkshopRefusal(f"Step {number} (Add Difference) needs Model C.")
                c, name_c = self._input("checkpoint", params.get("model_c"), outputs, number)
            names = {"a": name_a, "b": name_b, **({"c": name_c} if name_c else {})}

            def run() -> dict[str, Any]:
                result = engine.merge_models(self.job, a, b, output, path_c=c, names=names, **settings)
                return {"filename": result["filename"], "elapsed": result["elapsed"],
                        "recipe": {"type": "merge", **result["recipe"]}}
            return run
        checkpoint, checkpoint_name = self._input("checkpoint", params.get("checkpoint"), outputs, number)
        if kind == "lora_bake":
            loras = []
            for item in params.get("loras") or []:
                if not isinstance(item, dict):
                    raise WorkshopRefusal(f"Step {number}: each LoRA must be an object.")
                name = str(item.get("filename") or "").strip()
                strength = self._number(item.get("strength"), "LoRA strength", -2.0, 2.0, 1.0)
                loras.append((self.resolve("lora", name), strength, name))
            if not loras:
                raise WorkshopRefusal(f"No LoRAs specified in step {number}")

            def run() -> dict[str, Any]:
                result = engine.bake_lora(self.job, checkpoint, loras, output,
                                          checkpoint_name=checkpoint_name, **dtype)
                return {"filename": result["filename"], "elapsed": result["elapsed"],
                        "recipe": {"type": "lora_bake", **result["recipe"]}}
            return run
        vae_name = str(params.get("vae") or "").strip()
        vae = self.resolve("vae", vae_name)

        def run() -> dict[str, Any]:
            result = engine.bake_vae(self.job, checkpoint, vae, output,
                                     checkpoint_name=checkpoint_name, vae_name=vae_name, **dtype)
            return {"filename": result["filename"], "elapsed": result["elapsed"],
                    "recipe": {"type": "vae_bake", **result["recipe"]}}
        return run

    # -- journal (`:3990-4166`, `:5100-5224`) --------------------------------

    def _journal_folder(self, *, create: bool) -> Path | None:
        if self._state_root is None:
            return None
        folder = self._state_root / "workshop"
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _journal_images(self, *, create: bool) -> Path | None:
        folder = self._journal_folder(create=create)
        if folder is None:
            return None
        images = folder / "journal_images"
        if create:
            images.mkdir(parents=True, exist_ok=True)
        return images

    def _load_journal(self) -> list[dict[str, Any]]:
        folder = self._journal_folder(create=False)
        target = folder / "workshop_journal.json" if folder else None
        if target is None or not target.is_file():
            return []
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            return [e for e in loaded if isinstance(e, dict)] if isinstance(loaded, list) else []
        except (OSError, ValueError):
            return []

    def _save_journal(self, entries: list[dict[str, Any]]) -> None:
        folder = self._journal_folder(create=True)
        if folder is None:
            raise WorkshopRefusal("Studio has nowhere to keep the History.", 503)
        target = folder / "workshop_journal.json"
        partial = target.with_name(target.name + ".tmp")
        partial.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, target)

    def _journal_entry(self, entry_id: str, name: str, kind: str, recipe: dict[str, Any],
                       elapsed: float) -> None:
        if self._state_root is None:
            return
        with self._journal_lock:
            entries = self._load_journal()
            entries.insert(0, {"id": entry_id, "name": name, "type": kind, "recipe": recipe,
                               "date": datetime.now(timezone.utc).isoformat(), "elapsed": elapsed,
                               "rating": 0, "notes": "", "image": None})
            self._save_journal(entries)

    def _journal_chain(self, results: list[dict[str, Any]], elapsed: float, save_intermediates: bool,
                       final_name: str | None, board: dict[str, Any] | None) -> None:
        """`_journal_add_chain`: one entry per kept file, or one combined entry."""

        stamp = int(time.time())
        if save_intermediates:
            for result in results:
                self._journal_entry(f"chain_{stamp}_{result['step']}", result.get("filename", ""),
                                    result.get("type", "unknown"), result.get("recipe", {}), elapsed)
            return
        if not results:
            return
        steps = [{**r.get("recipe", {}), "step": r.get("step"), "type": r.get("type"),
                  "filename": r.get("filename", "")} for r in results]
        recipe: dict[str, Any] = {"type": "chain", "steps": steps, "final": results[-1].get("recipe", {})}
        if board:
            recipe["workshop_board"] = board
        self._journal_entry(f"chain_{stamp}", final_name or results[-1].get("filename", ""),
                            "chain", recipe, elapsed)

    def _journal_post(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._journal_lock:
            entries = self._load_journal()
            if action == "add":
                entry_id = str(body.get("id") or f"manual_{int(time.time())}")
                if not _JOURNAL_ID.match(entry_id):
                    raise WorkshopRefusal("Invalid id (allowed: letters, digits, _ and -)")
                entries.insert(0, {"id": entry_id, "name": str(body.get("name") or "New Entry")[:200],
                                   "type": str(body.get("type") or "note")[:40],
                                   "recipe": body.get("recipe") if isinstance(body.get("recipe"), dict) else {},
                                   "date": datetime.now(timezone.utc).isoformat(), "elapsed": 0,
                                   "rating": 0, "notes": str(body.get("notes") or "")[:20000],
                                   "image": None})
                self._save_journal(entries)
                return {"ok": True, "id": entry_id}
            entry_id = str(body.get("id") or "")
            if not entry_id:
                raise WorkshopRefusal("No id provided")
            entry = next((e for e in entries if e.get("id") == entry_id), None)
            if entry is None:
                raise WorkshopRefusal("Entry not found", 404)
            if action == "update":
                if body.get("rating") is not None:
                    entry["rating"] = int(self._number(body.get("rating"), "rating", 0, 5, 0))
                if body.get("notes") is not None:
                    entry["notes"] = str(body["notes"])[:20000]
                if body.get("name") is not None:
                    entry["name"] = str(body["name"])[:200]
            elif action == "delete":
                entries = [e for e in entries if e.get("id") != entry_id]
                self._remove_images(entry_id)
            elif action == "image":
                self._remove_images(entry_id)
                entry["image"] = self._write_image(entry_id, body.get("image")) if body.get("image") else None
            else:
                raise WorkshopRefusal("Not found.", 404)
            self._save_journal(entries)
            return {"ok": True}

    def _remove_images(self, entry_id: str) -> None:
        images = self._journal_images(create=False)
        if images is None or not _JOURNAL_ID.match(entry_id):
            return
        for ext in _IMAGE_TYPES:
            try:
                (images / f"{entry_id}.{ext}").unlink()
            except OSError:
                pass

    def _write_image(self, entry_id: str, data: Any) -> str:
        """A sample image for an entry: decoded and checked, then written."""

        from PIL import Image

        if not _JOURNAL_ID.match(entry_id):
            raise WorkshopRefusal("Invalid id")
        text = str(data or "")
        header, _, payload = text.partition(",")
        if not header.startswith("data:image") or not payload:
            raise WorkshopRefusal("The image must be a data URL.")
        try:
            raw = base64.b64decode(payload, validate=True)
            with Image.open(io.BytesIO(raw)) as probe:
                kind = (probe.format or "").lower()
        except (binascii.Error, ValueError, OSError):
            raise WorkshopRefusal("That is not a readable image.") from None
        ext = {"png": "png", "jpeg": "jpg", "webp": "webp"}.get(kind)
        if ext is None:
            raise WorkshopRefusal("Use a PNG, JPEG or WebP image.")
        images = self._journal_images(create=True)
        (images / f"{entry_id}.{ext}").write_bytes(raw)
        return f"{entry_id}.{ext}"
