"""Compatibility API for the canonical Forge Studio frontend.

The source frontend is intentionally treated as an immutable consumer.  This
adapter presents the subset of its ``/studio/*`` contract needed by the
standalone mock milestone and translates generation requests into the smaller
Studio-owned application contract exposed through ``StudioPresentation``.

Nothing in this module imports Forge, Torch, CUDA, Gradio, or model code.

Preferences and saved defaults are DURABLE, on the state root owner decision D1
resolves (`preferences.py`). They were process-local through the visual-parity
milestone, which was the right call while there was nowhere to put them and the
wrong thing to keep once there was: the page posted, this adapter answered with
the saved document, and every setting was gone at exit. Selected-model state
stays process-local, because what is resident is a runtime fact and not a
setting.

A host that passes no store gets a memory-backed one with the same contract, so
the mock server and the real server accept and refuse exactly the same writes.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from dataclasses import dataclass
from threading import Lock, RLock
from time import monotonic, sleep
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from forge_studio.preferences import (
    DefaultsStore,
    DocumentTooLarge,
    MalformedDocument,
    PreferenceError,
    PreferenceStore,
    MalformedPreferenceValues,
    SessionIdentityNotDurable,
    SessionRevisionStale,
    UnknownPreferenceKeys,
    WildcardSettings,
)


_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_RECOGNIZED_GENERATION_KEYS = (
    "sampler",
    "scheduler",
    "enable_hr",
    "hr_scale",
    "hr_upscaler",
    "adetailer",
    "loras",
    "regions",
    "controlnet",
    "watermark",
    "extension_args",
    "disabled_extensions",
)
_STATE_ALIASES = {
    "canceled": "cancelled",
    "complete": "completed",
    "done": "completed",
    "error": "failed",
    "success": "completed",
    "succeeded": "completed",
}
#: How a storage refusal reaches the caller.
#:
#: The mapping lives HERE and not on the exceptions, because a document store
#: has no business knowing about HTTP -- this adapter is the layer that already
#: owns status codes. Anything unlisted is a fault rather than a bad request:
#: an unreadable or unwritable file is Studio's problem, not the caller's, and
#: reporting it as 400 would send the page looking for a mistake it did not
#: make.
_PREFERENCE_REFUSALS = (
    (UnknownPreferenceKeys, 400),
    (MalformedPreferenceValues, 400),
    (MalformedDocument, 400),
    (DocumentTooLarge, 413),
    # AR4.3. A stale session write is a CONFLICT, not a bad request: the
    # browser sent a well-formed view of a session that has since moved, and
    # 409 is what tells it to re-read rather than retry the same bytes.
    (SessionRevisionStale, 409),
    # Offering a registry handle as durable identity is a malformed request:
    # the value dies with the process and would restore to nothing.
    (SessionIdentityNotDurable, 400),
)


class _Presentation(Protocol):
    """Structural type used to avoid a presentation-module import cycle."""

    def backend_status(self) -> dict[str, Any]: ...

    def models(self) -> dict[str, Any]: ...

    def submit(self, payload: Any) -> dict[str, Any]: ...

    def poll(
        self,
        job_id: str,
        *,
        include_result: bool = True,
    ) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def load_model(self, model_id: str) -> dict[str, Any]: ...

    def unload_model(self) -> dict[str, Any]: ...

    def current_model(self) -> dict[str, Any]: ...

    def resident_selection(self) -> dict[str, Any] | None: ...

    def supported_generation_parameters(self) -> frozenset[str]: ...

    def capability(
        self,
        model_id: str,
        operation: str = "txt2img",
    ) -> dict[str, Any]: ...

    def runtime_status(self) -> dict[str, Any]: ...


#: What the component probe answers when it cannot tell. "unknown", NOT a
#: made-up name: `_isUsableArch` in app.js accepts any string except that one,
#: and app.js keys its per-checkpoint text-encoder/VAE memory by it -- so a
#: placeholder that LOOKS like an architecture put every checkpoint in one
#: bucket and carried a Flux VAE onto an SDXL model.
_NEUTRAL_COMPONENT_NEEDS = {
    "needs_te": False, "needs_vae": False, "arch": "unknown",
}


@contextmanager
def _editor_refusals():
    """Turn a refusal into a non-2xx carrying the keys the page reads.

    RAISED, not returned. A returned body renders at HTTPStatus.OK and the page
    toasts a refusal in green -- which this project shipped once for layouts,
    and the comment on the DELETE catch-all still records it.

    `error` is the only key `fetchJSON` reads, but `deleteItem` additionally
    reads `not_empty` and `file_count` from the body to offer its force-delete
    confirmation, so extras travel too.
    """

    from .wildcard_editor import EditorRefused

    try:
        yield
    except EditorRefused as refused:
        raise SourceFrontendRefusalError(
            refused.message, http_status=400, **refused.extra
        ) from refused


class SourceFrontendAdapterError(Exception):
    """Client-safe adapter failure understood by the presentation layer."""

    def __init__(self, message: str, *, http_status: int) -> None:
        super().__init__(message)
        self.error = {
            "http_status": http_status,
            "message": message,
        }


class SourceFrontendRefusalError(SourceFrontendAdapterError):
    """A refusal whose body the page reads keys off, not just a message.

    `deleteItem` in lexicon.js reads `not_empty` and `file_count` from the
    TOP LEVEL of the response body to offer "delete anyway". The shared error
    renderer nests the owned dict under `detail`, so a refusal carrying them
    only there would make a non-empty folder undeletable rather than guarded.
    """

    def __init__(self, message: str, *, http_status: int, **extra: Any) -> None:
        super().__init__(message, http_status=http_status)
        self.error["extra"] = extra


class SourceFrontendRouteError(SourceFrontendAdapterError, LookupError):
    """Raised when the copied frontend requests an unsupported route."""

    def __init__(self, method: str, path: str) -> None:
        super().__init__(
            f"Canonical Studio route is unavailable: {method} {path}",
            http_status=404,
        )


class SourceFrontendRequestError(SourceFrontendAdapterError, ValueError):
    """Raised for a malformed source-frontend request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, http_status=400)


@dataclass(frozen=True)
class _Model:
    model_id: str
    title: str
    name: str
    description: str

    def source_payload(self) -> dict[str, str]:
        return {
            "title": self.title,
            "name": self.name,
            "hash": "",
            "filename": "",
        }


@dataclass
class _ActiveGeneration:
    job_id: str
    steps: int
    model_title: str
    request: dict[str, Any]
    ignored_parameters: tuple[str, ...]
    #: What the owner typed before wildcards resolved, and which line each
    #: wildcard produced. NOT part of `request`: the generation payload has a
    #: strict allow-list and these are metadata about the prompt rather than
    #: parameters of the picture.
    prompt_template: str = ""
    wildcard_choices: str = ""


@dataclass(frozen=True)
class _ProgressSnapshot:
    job_id: str
    state: str
    fraction: float
    step: int
    total_steps: int
    message: str
    #: The BACKEND id, when the public id differs from it.
    #:
    #: Two identifiers exist for one job and they are not interchangeable:
    #: `job_id` is the public lifecycle token the owner and the queue use,
    #: while previews and raw progress are held by the backend adapter under
    #: its own id. Defaulted to empty so the legacy path -- where the two are
    #: the same string -- constructs unchanged.
    backend_job_id: str = ""


class SourceFrontendAdapter:
    """Serve canonical frontend payloads over the owned presentation contract.

    ``get``, ``post``, and ``delete`` are deliberately transport-neutral.  The
    HTTP handler remains responsible for Host/Origin validation, request-size
    limits, response status, and serialization.
    """

    def __init__(
        self,
        presentation: _Presentation,
        *,
        model_roots: Any = None,
        preferences: Any = None,
        defaults: Any = None,
        gallery: Any = None,
    ) -> None:
        self._presentation = presentation
        # Optional, and only ever written to. The Gallery is told what a
        # generation produced so an owner finds the prompt on an image they
        # made thirty seconds ago, before any scan has seen the file. Absent
        # means generation simply does not tell anyone -- it must never mean
        # generation fails.
        self._gallery = gallery
        # Optional. Without it the three model lists keep their previous
        # behaviour, which is what the mock backend and every existing test
        # rely on. With it, they serve the owner's configured directories.
        self._model_roots = model_roots
        self._state_lock = RLock()
        self._generation_gate = Lock()
        # Optional in the same sense, and defaulted to a store rather than to
        # None so there is ONE code path through here. A host that supplies
        # nothing gets memory-backed documents that accept and refuse exactly
        # what the durable ones do; a host that supplies them gets the owner's
        # settings back after a restart.
        self._preferences = (
            preferences if preferences is not None else PreferenceStore()
        )
        self._defaults = defaults if defaults is not None else DefaultsStore()
        # The working session, beside Defaults and deliberately NOT inside it.
        # Defaults are the baseline the owner chose; this is the state they
        # happened to leave behind, and merging the two would let an
        # afternoon of experiments quietly rewrite the baseline.
        #
        # Resolved from the defaults document's own state root rather than
        # guessed, so all three files land in the same place a host actually
        # configured. Memory-only when that host supplied nothing, which keeps
        # one code path through here.
        from .preferences import LastSessionStore

        self._session = LastSessionStore(
            self._defaults.path.parent if self._defaults.path else None
        )
        # Canvas crash recovery. Its own store because it holds BINARY artwork
        # rather than settings: `_Document` carries a 256 KB ceiling that is
        # right for preferences and would either break or inline megabytes of
        # base64 here. Same state root, so all of it lands where the host
        # configured.
        from .canvas_recovery import CanvasRecoveryStore

        self._recovery = CanvasRecoveryStore(
            self._defaults.path.parent if self._defaults.path else None
        )
        # Wildcards. The folder the owner chose lives in the preferences
        # document, so the service reads its state from the same place every
        # other setting does rather than holding a copy that forgets itself.
        from .wildcard_service import WildcardService

        # The SAME state root the preferences document resolved, taken from
        # its path rather than guessed: a document is memory-only when it has
        # no path, and asking for a `state_root` attribute it does not have
        # would have made the wildcard folder silently forget itself on every
        # restart while looking perfectly fine in a test.
        preferences_path = getattr(self._preferences, "path", None)
        self._wildcards = WildcardService(
            WildcardSettings(
                preferences_path.parent if preferences_path else None
            ),
            checkout=Path(__file__).resolve().parents[1],
        )
        self._active: _ActiveGeneration | None = None
        self._last_expansion: Any = None
        self._last_template = ""
        self._last_progress = _ProgressSnapshot(
            job_id="",
            state="idle",
            fraction=0.0,
            step=0,
            total_steps=0,
            message="Ready",
        )

    @property
    def active_job_id(self) -> str | None:
        with self._state_lock:
            if self._active is not None:
                return self._active.job_id
        return self._coordinated_job_id()

    def _coordinated_job_id(self) -> str | None:
        """The running job the QUEUE owns, for work this adapter did not start.

        `_active` is set by this adapter's own `generate`, and by nothing
        else. The product does not call it: the page submits to
        `/api/generate`, which mints a public id in the coordinator and never
        touches this object. So `active_job_id` answered None for every job
        Studio can actually create.

        That was not a cosmetic gap. `presentation._serve_websocket` gates
        EVERY progress send on this value, so `/studio/ws` sent nothing at
        all for those jobs -- and the Live Preview frame travels only on that
        socket, because a 25 kB data URL does not belong in the job record
        that gets serialised into every poll. Preview was carried correctly
        end to end and then delivered through a channel that was never open.
        Proven live 2026-08-10: during a running job, `/api/queue` reported
        it running while `/studio/task_id` answered "" for its whole life.
        """

        running = getattr(self._presentation, "running_job_id", None)
        if running is None:
            return None
        try:
            job_id = running()
        except Exception:  # noqa: BLE001 - observation never breaks progress
            return None
        return str(job_id).strip() or None if job_id else None

    def get(self, path: str) -> Any:
        """Return a canonical GET payload for ``path``."""

        route, query = _split_path(path)

        if route == "/studio/prefs":
            return self.preferences()
        if route == "/studio/models":
            catalogued = self._role_payload("checkpoint")
            if catalogued is not None:
                return catalogued
            return [model.source_payload() for model in self._models()]
        if route == "/studio/current_model":
            return self.current_model()
        if route == "/studio/model_status":
            return self.model_status()
        # The REAL registry, from the same source `/api/registries` reads.
        #
        # These three answered with hardcoded lists -- two samplers, two
        # schedulers, one upscaler -- against an engine that dispatches
        # twenty-one, seventeen and ten. That is the P0.6 defect in its purest
        # form: a control offering names the engine will never accept, and
        # silently ignoring what was chosen.
        #
        # Empty when the engine is not standing, never a plausible-looking
        # list. `presentation.registries` already guarantees the shape either
        # way, so a cold answer is [] and the page says "Engine default"
        # rather than naming a sampler it cannot dispatch.
        if route == "/studio/samplers":
            return [{"name": name} for name in self._registries().get("samplers", ())]
        if route == "/studio/schedulers":
            return [
                {"name": name, "label": name}
                for name in self._registries().get("schedulers", ())
            ]
        if route == "/studio/upscalers":
            registries = self._registries()
            return [
                {"name": name}
                for name in (
                    tuple(registries.get("latent_upscalers", ()))
                    + tuple(registries.get("image_upscalers", ()))
                )
            ]
        if route == "/studio/vaes":
            catalogued = self._role_payload("vae")
            # "Automatic" stays first and keeps its sentinel id, so a session
            # that saved it restores unchanged whether or not a VAE root is
            # configured.
            automatic = {"name": "Automatic", "model_id": "Automatic"}
            if catalogued is None:
                return [automatic]
            return [automatic, *catalogued]
        if route == "/studio/current_vae":
            # What is ACTUALLY loaded, read from the lifecycle. This answered a
            # hardcoded "Automatic" whatever was resident, so the one control
            # whose whole job is reporting engine state reported a constant.
            #
            # `name` carries the model_id because that is what the caller
            # compares against: app.js fills the VAE <select> with
            # `value="${v.model_id || v.name}"` and then tests
            # `_optionExists(vaeSelect, current.name)`. Sending a display name
            # here would never match an option and would silently do nothing --
            # the failure mode this route already had, in a new spelling.
            resident = self._resident_selection()
            vae = (resident or {}).get("vae_model_id") or None
            if vae is None:
                # Nothing resident, so there is no "current" VAE. Null rather
                # than "Automatic": the caller's `if (current.name && ...)`
                # then leaves the control alone, which is correct -- this route
                # is priority 3 behind a stashed value and per-model memory,
                # and it must not overwrite either with a guess.
                return {"name": None, "model_id": None, "resident": False}
            return {"name": vae, "model_id": vae, "resident": True}
        if route == "/studio/text_encoders":
            catalogued = self._role_payload("text_encoder")
            return [] if catalogued is None else catalogued
        if route == "/studio/model_roots":
            return self.model_roots()
        if route == "/studio/runtime_status":
            return self.runtime_status()
        if route == "/studio/capability":
            return self.capability(
                _first_query_value(query, "model_id"),
                _first_query_value(query, "operation"),
            )
        if route == "/studio/check_model_te":
            # A REAL header scan, as the Extension does
            # (`studio_api.py:4769-4822`). This answered a constant, and that
            # constant is why the Text Encoder dropdown never hid: a probe that
            # cannot tell one checkpoint from another cannot say that an SDXL
            # model already carries its own CLIP.
            #
            # `title` here is Studio's OPAQUE CATALOGUE ID, not a Forge
            # checkpoint title -- that substitution is why the Extension's
            # `get_closet_checkpoint_match` had no equivalent and the stub was
            # written instead. The registry resolves an id, so it does.
            #
            # Every failure answers neutrally, never an error status. Three of
            # the Extension's five return paths are the same neutral triple.
            title = _first_query_value(query, "title")
            registry = self._model_roots
            answer = dict(_NEUTRAL_COMPONENT_NEEDS)
            if registry is not None and title:
                try:
                    answer = dict(registry.inspect_checkpoint(title))
                except Exception:  # noqa: BLE001 - a dropdown, not a load
                    answer = dict(_NEUTRAL_COMPONENT_NEEDS)
            answer["title"] = title
            return answer
        if route == "/studio/loras":
            # THE REAL LoRA CATALOGUE. This sat in the unconditional-empty set
            # below, so "+ LoRAs" opened a browser that listed nothing while
            # the owner had a folder full of them -- and the LoRA stack could
            # only be filled by typing a tag by hand.
            #
            # Names come from the ENGINE's registry, not from a scan here,
            # because `<lora:NAME:weight>` is resolved against that registry.
            # Same reason `/studio/upscalers` reads the engine. And names ONLY:
            # the engine's own API shape carries `path`, which must never leave
            # Studio.
            from forge_headless.lora_catalogue import available_loras

            registry = self._model_roots
            if registry is None:
                return []
            try:
                return [entry.to_dict() for entry in available_loras(registry)]
            except Exception:  # noqa: BLE001 - a UI list is never fatal
                return []
        if route in {
            "/studio/embeddings",
            "/studio/extensions",
            "/studio/workflows",
            "/studio/layouts",
            "/studio/watermarks",
            "/studio/trusted-save-roots",
        }:
            return []
        # The REAL detector catalogue, hash-checked and names-never-paths.
        #
        # This answered `[{"name": "None"}]` while five verified detectors sat
        # in the configured root, so the three ADetailer slot dropdowns each
        # offered a single word meaning "off". `/api/detectors` has served the
        # real thing since P0.8(2) and only the folder picker consulted it.
        if route == "/studio/ad_models":
            from .detector_catalogue import scan_configured_detectors

            registry = self._model_roots
            if registry is None:
                return []
            try:
                detectors = scan_configured_detectors(registry)
            except Exception:  # noqa: BLE001 - a UI list is never fatal
                return []
            # Names only. `Detector` deliberately carries no path, and the
            # dropdown sends the name straight back as the slot's choice.
            return [{"name": detector.name} for detector in detectors]
        # ControlNet is NOT wired: no catalogue, no runtime, and its panel
        # does not reach the request. Left answering "None" deliberately --
        # offering real names for a feature that cannot dispatch them would
        # be the defect this file has just finished removing three times.
        if route in {"/studio/cn_models", "/studio/cn_preprocessors"}:
            return [{"name": "None"}]
        if route == "/studio/vram":
            return {
                "available": False,
                "allocated_gb": 0,
                "reserved_gb": 0,
                "total_gb": 0,
                "vram_reserve_gb": 0,
                "gpu_name": "",
            }
        if route == "/studio/task_id":
            return {"task_id": self.active_job_id or ""}
        if route == "/studio/live/status":
            return {
                "active": False,
                "generating": False,
                "pending": False,
                # An explicit declaration of ABSENCE, and the negative form is
                # deliberate. The page hides the Live entry point on
                # `available === false`, so a backend that has never heard of
                # this key -- including the Extension's, whose `get_status()`
                # answers `{"status": ...}` -- keeps Live visible. Truthfulness
                # here must not make a future real Live impossible, which is
                # exactly what an `available === true` requirement would do.
                "available": False,
            }
        if route == "/studio/dynamic_prompts/config":
            # The page reads `wildcard_folder_mode`, `wildcard_folder` and
            # `wildcard_folder_display` (app.js:5879). The stub that stood here
            # answered `folder_mode` and `folder`, so the panel said "Default
            # folders" whatever the owner had chosen -- and the toggle, which
            # only syncs when `studio_dynamic_prompts_enabled` is present, sat
            # permanently on for a service that did not exist.
            return self._wildcards.config()
        if route == "/studio/dynamic_prompts/status":
            return self._wildcards.status()
        if route == "/studio/wildcards":
            # Was in the empty-list group beside `/studio/loras`. That was
            # honest while there was no wildcard service; it is now a lie.
            return self._wildcards.listing()
        if route == "/studio/lexicon/tree":
            with _editor_refusals():
                return self._editor().tree()
        if route == "/studio/lexicon/file":
            with _editor_refusals():
                return self._editor().read(_first_query_value(query, "path") or "")
        if route == "/studio/wildcard_content":
            return self._wildcards.content(_first_query_value(query, "name") or "")
        if route == "/studio/api/check-update":
            return {
                "available": False,
                "current": "standalone-mock",
                "latest": "standalone-mock",
            }
        if route == "/studio/api/update-status":
            return {"state": "idle", "message": "Updates are unavailable in mock mode."}
        if route == "/sdapi/v1/options":
            current = self.current_model()
            return {"sd_model_checkpoint": current["title"]}
        if route == "/sdapi/v1/progress":
            return self.progress()

        raise SourceFrontendRouteError("GET", route)

    def post(self, path: str, payload: Any) -> Any:
        """Return a canonical POST payload for ``path``."""

        route, _query = _split_path(path)

        if route == "/studio/upscale_and_refine":
            return self.upscale_and_refine(payload)
        if route == "/studio/prefs":
            return self.update_preferences(payload)
        if route == "/studio/generate":
            return self.generate(payload)
        if route == "/studio/interrupt":
            return self.interrupt()
        if route == "/studio/skip":
            # "Skip to the next image" and "cancel the one running" are the
            # same act in this product: one image per job, and the queue
            # starts the next as soon as the current one reaches a safe
            # terminal state. Upstream's skip abandons one image of a BATCH,
            # which is a distinction Studio does not have.
            #
            # This answered `skipped: false` with `ok: true` unconditionally,
            # and the page toasted "Skipping to next image..." over it.
            outcome = self.interrupt()
            return {
                "ok": True,
                "skipped": bool(outcome.get("cancelled", False)),
                "message": str(outcome.get("message", "")),
            }
        if route == "/studio/load_model":
            data = _mapping(payload)
            return self.load_model(data.get("title"))
        if route == "/studio/refresh_models":
            # Actually re-enumerate. This answered {"ok": true, "count": N}
            # without touching a disk: `_models()` reads the catalogue, and
            # `catalogue.snapshot()` returns the CACHED scan. So the page
            # toasted "Models refreshed", re-fetched /studio/models, and got
            # back exactly what it already had -- an owner who dropped a
            # checkpoint into a configured root and pressed the button was told
            # it had worked.
            #
            # `ModelRootRegistry.refresh(role)` (model_roots.py:404) has existed
            # the whole time and had no caller outside its own class. This is
            # that seam finally being used, not new machinery.
            registry = self._model_roots
            if registry is None:
                # No configured roots means there is nothing to rescan. A
                # refusal, not a cheerful count of zero -- 409 rather than a
                # 200 body saying ok:false, because the whole point of this
                # batch is that refusals must arrive as refusals.
                raise SourceFrontendAdapterError(
                    "No model folders are configured, so there is nothing "
                    "to rescan.",
                    http_status=409,
                )
            # Roles come from `describe()`, which is already the registry's own
            # per-role view. Iterating it keeps this duck-typed against whatever
            # registry is injected, rather than importing the role tuple and
            # pulling forge_headless into forge_studio's import surface.
            roles = tuple(registry.describe())
            for role in roles:
                registry.refresh(role)
            return {
                "ok": True,
                "count": len(self._models()),
                "roles_rescanned": len(roles),
            }
        if route == "/studio/unload_model":
            self._presentation.unload_model()
            return {"ok": True, "unloaded": True}
        # NO /studio/load_vae stub. It answered {"ok": True, "loaded":
        # "Automatic"} without ever reading the requested `name` or touching a
        # backend. The caller's guard is correct -- app.js:5506 checks
        # `r.ok && data.ok` -- so the route lied to a caller that was asking
        # honestly: it toasted "VAE: Automatic" over whatever the owner picked,
        # and then `rememberVAE` persisted that pick as backend-confirmed.
        #
        # Falling through to the 404 lets the existing else-branch at
        # app.js:5519-5521 fire, which toasts `toast.vae.loadFailed`. The real
        # VAE selection is unaffected: it travels as
        # `model_selection.vae_model_id` on the canonical request and is applied
        # at load. This route was a second, out-of-band mutation of resident
        # state, which is the thing "one canonical request" forbids.
        if route == "/studio/auto_unload":
            data = _mapping(payload)
            return {
                "ok": False,
                "available": False,
                "enabled": False,
                "minutes": _coerce_int(data.get("minutes"), 10),
                "error": "Auto-unload is unavailable in mock mode.",
            }
        if route == "/studio/tokens":
            prompt = str(_mapping(payload).get("prompt", ""))
            chunks = max(1, len(prompt.split("BREAK")))
            return {"tokens_l": None, "tokens_g": None, "chunks": chunks}
        if route in {"/studio/session_evict", "/studio/session_clear"}:
            return {
                "ok": True,
                "persisted": False,
                "message": "No server-side session state exists in mock mode.",
            }
        if route == "/studio/vram_reserve":
            return {
                "ok": False,
                "available": False,
                "error": "VRAM reservation is unavailable in mock mode.",
            }
        if route == "/studio/live/start":
            return {"ok": False, "active": False, "error": "Live is unavailable in mock mode."}
        if route == "/studio/live/stop":
            return {"ok": True, "active": False}
        if route == "/studio/live/submit":
            return {"ok": False, "error": "Live is unavailable in mock mode."}
        if route == "/studio/dynamic_prompts/config":
            # This branch was UNREACHABLE. An earlier one answered the same
            # route with a hardcoded not-enabled body, and its comment -- "there
            # is no wildcard service to persist a preference to" -- was true
            # when written and became false when the service was built. Nothing
            # failed, because the stub's answer was the same one the capability
            # default produced, so the toggle looked merely stuck rather than
            # unwired. It also answered in the OLD key names (`folder_mode`,
            # `folder`), which the page stopped reading when the GET was fixed.
            #
            # The stub is gone. The write now runs, and `set_enabled` returns
            # `config()` -- so the response is the state the server will
            # actually act on, not an echo of what was asked for.
            return self._wildcards.set_enabled(payload)
        if route == "/studio/dynamic_prompts/select_folder":
            return self._wildcards.select_folder(payload)
        if route == "/studio/dynamic_prompts/pick_folder":
            return self._wildcards.pick_folder()
        if route.startswith("/studio/lexicon/"):
            body = payload if isinstance(payload, dict) else {}
            editor = self._editor()
            with _editor_refusals():
                if route == "/studio/lexicon/file/save":
                    return editor.save(str(body.get("path") or ""),
                                       str(body.get("content") or ""))
                if route == "/studio/lexicon/file/create":
                    return editor.create_file(str(body.get("path") or ""),
                                              str(body.get("name") or ""))
                if route == "/studio/lexicon/folder/create":
                    return editor.create_folder(str(body.get("path") or ""),
                                                str(body.get("name") or ""))
                if route == "/studio/lexicon/file/rename":
                    return editor.rename(str(body.get("path") or ""),
                                         str(body.get("new_name") or ""))
        if route == "/studio/wildcard_preview":
            return self._wildcards.preview(payload)
        if route in {
            "/studio/gallery/pick-folder",
            "/studio/open_folder",
            "/studio/watermarks/open_folder",
            "/studio/trust-save-root",
            "/studio/untrust-save-root",
        }:
            return {"ok": False, "error": "Filesystem actions are unavailable in mock mode."}
        # NO gallery scan stub. It answered {"ok": True, "items": []}, and the
        # catch-all below renders every adapter return at HTTPStatus.OK -- so
        # the Gallery's own success branch fired and interpolated fields that
        # were never sent: `Scan: undefined new, undefined removed`, styled as
        # success. Falling through to the 404 is what makes it honest: gallery.js
        # `api()` sets `.error` on any non-ok response and `rescan()` branches on
        # it, so the owner now sees the request failed. The first-run card also
        # uses the guarded API client and stays open when these routes refuse.
        if route == "/studio/api/update":
            return {"ok": False, "error": "Updates are unavailable in mock mode."}

        raise SourceFrontendRouteError("POST", route)

    def delete(self, path: str) -> Any:
        """Return a canonical DELETE payload for ``path``."""

        route, _query = _split_path(path)
        if route == "/studio/prefs":
            # The `?reset` emergency flow. It removes preferences and nothing
            # else -- saved defaults are a separate document precisely so this
            # button cannot take them too.
            with _preference_refusals():
                self._preferences.clear()
            return {"ok": True}
        if route == "/studio/lexicon/file":
            with _editor_refusals():
                return self._editor().delete(
                    _first_query_value(_query, "path") or "",
                    force=str(_first_query_value(_query, "force") or "").lower()
                    in ("1", "true", "yes"),
                )
        # NO workflow/layout DELETE stubs. They returned {"ok": False, ...} and
        # the DELETE catch-all renders that at HTTPStatus.OK, so the page saw a
        # 200 and toasted "Layout deleted" in green over a refusal. The 404 that
        # this raise produces is what `deleteActive` in app.js already checks
        # (`if (!r.ok)`), so the layout path now reports the truth.
        #
        # The workflow caller now goes through API.delete, which applies the
        # same non-2xx guard as GET and POST. Both callers therefore reject this
        # refusal instead of converting a parsed 404 body into a green success.
        raise SourceFrontendRouteError("DELETE", route)

    def _editor(self) -> Any:
        """A writer over whatever folder is chosen RIGHT NOW.

        Built per call rather than held, so a folder the owner changes in
        Settings takes effect on the next click instead of on the next launch.
        The read side already re-reads its stored folder on every call for the
        same reason.
        """

        from .wildcard_editor import WildcardEditor

        library = self._wildcards.library()
        return WildcardEditor(getattr(library, "root", None))

    def preferences(self) -> dict[str, Any]:
        with _preference_refusals():
            return self._preferences.read()

    def upscale_and_refine(self, payload: Any) -> dict[str, Any]:
        """The standalone Upscale panel. ESRGAN only -- no checkpoint, no
        sampling.

        THE PANEL SHIPPED WITHOUT THIS ROUTE. `index.html:815-838` has been
        offering an upscaler, a scale, a refine toggle, an Auto Detail toggle
        and an UPSCALE CANVAS button since Studio shipped; `app.js:5213` POSTed
        here; and nothing answered. The owner got a convincing progress
        display and then "Upscale failed: Studio route not found", which reads
        like a Studio bug rather than a feature nobody built.
        `parity_ledger.py:80` has recorded it as missing all along.

        BUILT RATHER THAN HIDDEN, which is the opposite of the ruling on the
        Hires checkpoint control, and for a stated reason: an alpha tester on a
        6 GB card cannot run Hires, Auto Detail and a generation together. A
        pure ESRGAN pass needs NO resident checkpoint and does no sampling, so
        it is the one large-image path that card can afford. Hiding it would
        have removed the workflow that makes Studio usable there.

        The work happens in `forge_headless`, not here: `test_import_boundaries`
        forbids `forge_studio` from importing `modules.*`, and it now measures
        that in a child interpreter. The import is inside this method so the
        module graph stays clean either way.
        """

        from forge_headless.contracts import HeadlessError
        from forge_headless.image_upscale import upscale

        data = payload if isinstance(payload, dict) else {}
        try:
            result = upscale(
                data.get("image_b64", ""),
                upscaler=str(data.get("upscaler", "") or ""),
                scale=data.get("scale", 2.0),
                run_refine=bool(data.get("run_refine", False)),
                run_ad=bool(data.get("run_ad", False)),
            )
        except HeadlessError as error:
            # `ok: false` with a sentence, which is the shape `app.js:5219`
            # already branches on -- it reads `data.error` and toasts it.
            return {"ok": False, "error": error.message, "code": error.code}

        return {
            "ok": True,
            "image": result.image_b64,
            "width": result.width,
            "height": result.height,
            "upscaler": result.upscaler,
        }

    def update_preferences(self, payload: Any) -> dict[str, Any]:
        # The store's own lock covers read-merge-write, so `_state_lock` is
        # deliberately NOT taken here: it guards generation state, and holding
        # it across a disk write would put a file system in the path of a
        # progress poll.
        data = _mapping(payload)
        with _preference_refusals():
            return self._preferences.merge(data)

    def current_model(self) -> dict[str, str]:
        selected = self._selected_model()
        if selected is None:
            return {"title": "", "name": "", "hash": ""}
        return {
            "title": selected.title,
            "name": selected.name,
            "hash": "",
        }

    def runtime_status(self) -> dict[str, Any]:
        """Report which backend is selected and what it can do. Pure read.

        Never reports a path or a traceback. `gradio_imported` is False only
        when observed; a backend that cannot establish it reports "UNKNOWN"
        rather than guessing.
        """

        status = self._presentation.runtime_status()
        return {
            "selected_backend": status.get("selected_backend", "mock"),
            "backend_selection_honoured": bool(
                status.get("backend_selection_honoured", True)
            ),
            "headless_state": status.get("headless_state", "not_selected"),
            "legacy_compatibility_state": status.get(
                "legacy_compatibility_state", "disabled"
            ),
            "gradio_imported": status.get("gradio_imported", "UNKNOWN"),
            "model_loaded": bool(status.get("model_loaded", False)),
            "generation_available": bool(
                status.get("generation_available", False)
            ),
            "blocking_reason": status.get("blocking_reason", ""),
        }

    def capability(
        self,
        model_id: Any,
        operation: Any = "",
    ) -> dict[str, Any]:
        """Read selected-model capability. Pure: no selection, no load.

        Parameters are bounded before they reach the application so an
        oversized query string is rejected here rather than deeper in.
        """

        requested_model = _bounded_identifier(model_id, "model_id")
        requested_operation = (
            _bounded_identifier(operation, "operation")
            if str(operation or "").strip()
            else "txt2img"
        )
        return self._presentation.capability(
            requested_model,
            requested_operation,
        )

    def model_status(self) -> dict[str, Any]:
        selected = self._selected_model()
        return {
            "loaded": selected is not None,
            "title": selected.title if selected is not None else "",
            "external_text_encoder": None,
            "external_vae": None,
            "is_mock": True,
        }

    def load_model(self, title: Any) -> dict[str, Any]:
        requested = str(title or "").strip()
        if not requested:
            raise SourceFrontendRequestError("A model title is required.")

        for model in self._models():
            if requested in {model.title, model.name, model.model_id}:
                residency = self._presentation.load_model(model.model_id)
                return {
                    "ok": bool(residency.get("loaded")),
                    "loaded": model.title,
                    "title": model.title,
                    "is_mock": bool(residency.get("is_mock")),
                }
        raise SourceFrontendRequestError("The selected mock model is unavailable.")

    def generate(self, payload: Any) -> dict[str, Any]:
        data = _mapping(payload)
        action = str(data.get("action", "generate") or "generate")
        if action != "generate":
            return self._defaults_action(action, data)

        if not self._generation_gate.acquire(blocking=False):
            return _empty_generation_response("A mock generation is already running.")

        active: _ActiveGeneration | None = None
        try:
            ignored_parameters = _ignored_generation_parameters(
                data,
                self._presentation.supported_generation_parameters(),
            )
            request = self._translate_generation(data)
            submitted = self._presentation.submit(request)
            job_id = str(submitted.get("job_id", submitted.get("id", ""))).strip()
            if not job_id:
                raise SourceFrontendRequestError(
                    "Studio did not return a mock job identifier."
                )

            active = _ActiveGeneration(
                job_id=job_id,
                steps=request["steps"],
                model_title=self.current_model()["title"],
                request=request,
                ignored_parameters=ignored_parameters,
                prompt_template=self._last_template,
                wildcard_choices=(
                    self._last_expansion.summary if self._last_expansion else ""
                ),
            )
            with self._state_lock:
                self._active = active
                self._last_progress = _ProgressSnapshot(
                    job_id=job_id,
                    state=_state_of(submitted),
                    fraction=_fraction_of(submitted),
                    step=0,
                    total_steps=request["steps"],
                    message=str(submitted.get("message", "Queued")),
                )

            # The wait-to-terminal policy is shared with the standalone host
            # rather than kept here, so a second host cannot drift from it.
            from forge_studio.composition import TIMED_OUT, run_to_terminal

            observed: list[Any] = []

            def _observe(event: Any) -> str:
                snapshot = self._record_progress(active, event)
                observed.append(snapshot)
                return snapshot.state

            state, event = run_to_terminal(
                poll=lambda: self._presentation.poll(job_id),
                observe=_observe,
                cancel=lambda: self._presentation.cancel(job_id),
            )
            if state == "completed":
                return self._canonical_result(active, event)
            if state == "cancelled":
                message = observed[-1].message if observed else ""
                return _empty_generation_response(
                    message or "Mock generation cancelled."
                )
            if state == "failed":
                return _empty_generation_response(_error_message(event))
            if state == TIMED_OUT:
                return _empty_generation_response("Mock generation timed out.")
            return _empty_generation_response("Mock generation did not complete.")
        finally:
            if active is not None:
                with self._state_lock:
                    if self._active is not None and self._active.job_id == active.job_id:
                        self._active = None
            self._generation_gate.release()

    def interrupt(self) -> dict[str, Any]:
        """Stop the generation that is running, whoever started it.

        `self._active` is set by this adapter's own `generate`, which the
        product does not call -- so this answered "No mock generation is
        active" for every job Studio can create, with `ok: True`, and the page
        showed "Interrupting..." over a no-op. Same shape as the progress
        guard and the sampler list: a lifecycle host was left out of a check
        written before there was one.
        """

        with self._state_lock:
            active = self._active
        if active is None:
            job_id = self._coordinated_job_id()
            if job_id is None:
                return {
                    "ok": True,
                    "cancelled": False,
                    "message": "Nothing is generating.",
                }
            result = self._presentation.cancel(job_id)
            return {
                "ok": True,
                # The real outcome. The comment here used to say exactly that
                # while defaulting a MISSING key to True -- and no branch of
                # `JobCoordinator.cancel` ever returned `cancelled`; it returns
                # `cancelled_while_queued` and `already_terminal`. So every
                # Interrupt reported "Cancelled." including the idempotent
                # no-op against a job that had already finished. The key is now
                # supplied by every branch, and the default is the safe one:
                # absent means we do not know that anything was cancelled.
                "cancelled": bool(result.get("cancelled", False)),
                "message": str(result.get("message", "Cancelled.")),
            }

        result = self._presentation.cancel(active.job_id)
        self._record_progress(active, result)
        return {
            "ok": True,
            "cancelled": bool(result.get("cancelled", False)),
            "task_id": active.job_id,
            "state": _state_of(result),
            "message": str(result.get("message", "Mock generation cancelled.")),
        }

    def progress(self) -> dict[str, Any]:
        """Return the canonical HTTP progress-fallback payload."""

        snapshot = self._observe_active()
        return {
            "progress": snapshot.fraction,
            "state": {
                "sampling_step": snapshot.step,
                "sampling_steps": snapshot.total_steps,
                "job_no": 0,
                "job_count": 1 if snapshot.job_id else 0,
            },
            "current_image": None,
            "textinfo": snapshot.message,
        }

    def websocket_status(self) -> dict[str, Any]:
        """Return one canonical ``/studio/ws`` progress message."""

        snapshot = self._observe_active()
        # `"preview": None` was a literal here for the whole life of this
        # method, on a message the page already knew how to render: app.js
        # reads `data.preview` and draws it into #canvasPreview. The client
        # half of Live Preview has always worked. This is the other half.
        frame_id, frame = (0, None)
        # The BACKEND id when there is one. `application.preview_frame`
        # forwards straight to `BackendAdapter.preview_frame`, which holds
        # frames under the id it minted -- asking it for a public
        # `studio-job-NNNNNN` returns no frame and no error, which is the
        # quietest possible way for a live preview to be absent.
        frame_key = snapshot.backend_job_id or snapshot.job_id
        if frame_key:
            frame_id, frame = self._preview_frame(frame_key)
        return {
            "type": "progress",
            "progress": snapshot.fraction,
            "step": snapshot.step,
            "total_steps": snapshot.total_steps,
            "job": 0,
            "job_count": 1 if snapshot.job_id else 0,
            "preview": frame,
            "preview_id": frame_id,
            "textinfo": snapshot.message,
            "state": snapshot.state,
            "task_id": snapshot.job_id,
        }

    def _registries(self) -> dict[str, Any]:
        """What the engine will actually dispatch on, or empty.

        Duck-typed and never fatal, matching `_preview_frame` below: a double
        that predates this method is answered the same way a cold engine is,
        with nothing, and a dropdown that receives nothing can say so.
        """

        reader = getattr(self._presentation, "registries", None)
        if reader is None:
            return {}
        try:
            payload = reader()
        except Exception:  # noqa: BLE001 - a UI list is never fatal
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def _preview_frame(self, job_id: str) -> tuple[int, str | None]:
        getter = getattr(self._presentation, "preview_frame", None)
        if getter is None:
            return (0, None)
        try:
            return getter(job_id)
        except Exception:  # noqa: BLE001 - a preview never breaks progress
            return (0, None)

    def _defaults_action(
        self,
        action: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if action == "load_defaults":
            with _preference_refusals():
                stored = self._defaults.read()
            return _empty_generation_response(settings=stored)
        if action == "save_defaults":
            defaults = payload.get("defaults_data")
            if not isinstance(defaults, Mapping):
                raise SourceFrontendRequestError(
                    "defaults_data must be a JSON object."
                )
            # `replace`, not `merge`: Settings sends the complete defaults
            # document, so merging would keep a parameter the owner had just
            # removed from it.
            with _preference_refusals():
                self._defaults.replace(defaults)
            return _empty_generation_response(settings={"defaults_saved": True})
        if action == "delete_defaults":
            with _preference_refusals():
                self._defaults.clear()
            return _empty_generation_response(settings={"defaults_deleted": True})
        if action == "load_session":
            # Never raises for a bad snapshot: an unreadable or
            # future-schema session is a reason to fall back to Saved
            # Defaults, not a reason to fail the page.
            snapshot = self._session.read_snapshot()
            return _empty_generation_response(settings={
                "session_present": bool(snapshot),
                "session_revision": snapshot.get("session_revision", 0),
                "schema_version": snapshot.get("schema_version"),
                "document_id": snapshot.get("document_id"),
                "canvas_revision": snapshot.get("canvas_revision"),
                "session": snapshot.get("settings") or {},
            })
        if action == "save_session":
            data = payload.get("session_data")
            if not isinstance(data, Mapping):
                raise SourceFrontendRequestError(
                    "session_data must be a JSON object."
                )
            with _preference_refusals():
                stored = self._session.write_snapshot(
                    data,
                    base_revision=payload.get("base_revision"),
                    document_id=payload.get("document_id"),
                    canvas_revision=payload.get("canvas_revision"),
                )
            return _empty_generation_response(settings={
                "session_saved": True,
                "session_revision": stored["session_revision"],
            })
        if action == "delete_session":
            # Disabling must not merely stop RESTORING. A snapshot left on
            # disk would come back the moment the owner re-enabled the
            # feature, which is the "old state resurrects" defect the handoff
            # names. The file is removed.
            with _preference_refusals():
                self._session.clear()
            return _empty_generation_response(settings={"session_deleted": True})
        if action.startswith("recovery_"):
            return self._recovery_action(action, payload)
        raise SourceFrontendRequestError(f"Unsupported generation action: {action}")

    def _recovery_action(
        self,
        action: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Canvas crash recovery. AR4.4.

        Staged across requests -- begin, one blob per call, then commit --
        because a whole document does not fit in one 16 MB source request and
        a single-request design would BE the document-size ceiling the owner
        forbade. Nothing here bounds how many layers a document may have or
        how large one may be.
        """

        from .canvas_recovery import RecoveryError

        document_id = str(payload.get("document_id") or "")
        try:
            if action == "recovery_list":
                return _empty_generation_response(settings={
                    "documents": [
                        {"document_id": e.get("document_id"),
                         "updated_at": e.get("updated_at"),
                         "manifest": e.get("manifest") or {}}
                        for e in self._recovery.list()
                    ]})
            if action == "recovery_load":
                loaded = self._recovery.load(document_id)
                return _empty_generation_response(settings={
                    "document_id": loaded.get("document_id"),
                    "manifest": loaded.get("manifest") or {},
                    "blob_data": loaded.get("blob_data") or {},
                })
            if action == "recovery_begin":
                self._recovery.begin(document_id)
                return _empty_generation_response(settings={"recovery_open": True})
            if action == "recovery_blob":
                # `append` lets one blob arrive across several requests, so
                # the 16 MB request bound never becomes a document-size limit.
                written = self._recovery.put_blob(
                    document_id, str(payload.get("name") or ""),
                    payload.get("data"),
                    append=bool(payload.get("append")))
                return _empty_generation_response(settings={"bytes": written})
            if action == "recovery_commit":
                sealed = self._recovery.commit(
                    document_id, payload.get("manifest"))
                return _empty_generation_response(settings={
                    "recovery_saved": True,
                    "updated_at": sealed.get("updated_at"),
                    "blobs": sealed.get("blobs") or []})
            if action == "recovery_discard":
                # ONLY an explicit discard or close reaches here. An ordinary
                # image export is not a layered project save and must not
                # clear recovery.
                self._recovery.discard(document_id)
                return _empty_generation_response(
                    settings={"recovery_discarded": True})
        except RecoveryError as error:
            # VISIBLE, never a silent success. A recovery system that reports
            # OK while writing nothing is worse than none at all, because the
            # owner stops saving by hand.
            raise SourceFrontendAdapterError(
                str(error), http_status=_recovery_status(error)) from error
        raise SourceFrontendRequestError(f"Unsupported generation action: {action}")

    def _translate_generation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        selected = self._selected_model()
        if selected is None:
            raise SourceFrontendRequestError("Select an available mock model.")

        seed = _coerce_int(payload.get("seed"), -1)
        prompt = str(payload.get("prompt", ""))
        negative = str(payload.get("neg_prompt", ""))

        # Wildcards resolve HERE, before the request is built, so everything
        # downstream -- the engine, the infotext, the Gallery -- sees the
        # prompt the model actually received. The page sends the toggle with
        # each request; the stored preference is the fallback for a client
        # that does not.
        expansion = None
        wants = payload.get("studio_dynamic_prompts_enabled")
        if (bool(wants) if wants is not None else self._wildcards.enabled()):
            expansion = self._wildcards.expand(prompt, seed)
            prompt = expansion.text

        # Recorded for the infotext, and deliberately NOT put in the request.
        self._last_expansion = expansion
        self._last_template = str(payload.get("prompt", ""))

        return {
            "model": selected.model_id,
            "positive_prompt": prompt,
            "negative_prompt": negative,
            "seed": seed,
            "steps": _coerce_int(payload.get("steps"), 30),
            "cfg_scale": _coerce_float(payload.get("cfg_scale"), 5.0),
            "width": _coerce_int(payload.get("width"), 768),
            "height": _coerce_int(payload.get("height"), 768),
        }

    def _canonical_result(
        self,
        active: _ActiveGeneration,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_value = event.get("result")
        result = dict(result_value) if isinstance(result_value, Mapping) else {}
        image = result.get("image_data_url", result.get("image", ""))
        images = [str(image)] if isinstance(image, str) and image else []
        metadata_value = result.get("metadata")
        metadata = (
            deepcopy(dict(metadata_value))
            if isinstance(metadata_value, Mapping)
            else {}
        )
        resolved_seed = _resolved_seed(metadata.get("seed"))
        handle = result.get("image_handle")
        if isinstance(handle, str) and handle and not images:
            # A backend that returns no inline data still yields one displayed
            # image: the canonical client renders it from the session entry.
            images = [""]
        settings = {
            **metadata,
            "width": active.request["width"],
            "height": active.request["height"],
            "is_mock": bool(metadata.get("is_mock", True)),
        }
        infotext = _infotext(
            active.request,
            active.model_title,
            resolved_seed,
            template=active.prompt_template,
            choices=active.wildcard_choices,
        )
        # Of the DELIVERED PIXELS, not of `metadata["image_sha256"]`. See
        # `_delivered_content_hash`: that key is the mock's SVG-source digest
        # and was never a content hash, so this write could not fire on a real
        # generation and could not have linked on a mock one.
        content_hash = _delivered_content_hash(image)
        if self._gallery is not None and content_hash and infotext:
            # Gated on the INFOTEXT, as each of the Extension's three writes
            # is (`studio_api.py:3266`, `:3342`, `:4578`), and on
            # `embed_metadata` nowhere: that toggle governs the saved file and
            # the database records regardless of it. Its OTHER gate is
            # `save_outputs`, which encloses all three writes (`:3215`,
            # `:3279`, `:4513`) -- Studio has no such field on any request, so
            # there is nothing here to honour yet. See
            # `presentation.py::_record_generation_metadata`.
            #
            # Fire-and-forget by contract: `record_generation` never raises,
            # so a Gallery that cannot write its index cannot turn a picture
            # that was made into a generation that failed.
            self._gallery.record_generation(content_hash, infotext, settings)
        if self._gallery is not None and images:
            # DELIBERATELY outside the `content_hash` guard above, and still
            # so after AR5.4 repaired that guard. An image whose pixels cannot
            # be hashed -- the mock's SVG -- is still an image that appeared in
            # a linked folder, and the refresh must not hang off the identity.
            #
            # Never raises: a picture that was made must not be reported as a
            # generation that failed because an index could not be refreshed.
            self._gallery.note_generation()
        # `path` here is the opaque delivery handle, not a filesystem path.
        # The canonical client round-trips it into /studio/file?path=... and
        # only derives a display filename from its last segment.
        session_entries = (
            [
                {
                    "entry_id": active.job_id,
                    "source": "scratch",
                    "path": handle,
                }
            ]
            if isinstance(handle, str) and handle and images
            else []
        )

        return {
            "images": images,
            "image_paths": [],
            "float_paths": [],
            "mask_paths": [],
            "float_stats": [],
            "content_hashes": [content_hash] if images else [],
            "infotexts": [infotext] if images else [],
            "session_entries": session_entries,
            "settings": settings,
            "seed": resolved_seed if resolved_seed is not None else -1,
            "task_id": active.job_id,
            "error": None if images else "Mock generation returned no image.",
            "notice": _ignored_parameters_notice(
                active.ignored_parameters
            ),
        }

    def _role_payload(self, role: str) -> list[dict[str, Any]] | None:
        """Project one role's catalogue for the browser, or None if unconfigured.

        What leaves this method is the whole privacy boundary for model lists:
        an opaque id, a sanitized display name, a size and format. Never an
        absolute path, never the root, and never ``relative_location`` -- which
        is root-relative and therefore still describes the owner's directory
        layout.

        Duplicate display names are disambiguated for the human, while the id
        stays the thing the browser actually sends back.
        """
        registry = self._model_roots
        if registry is None:
            return None
        # `entries`, not `snapshot`. `snapshot` answers about ONE directory --
        # it is what a single-root registry always had -- and with ordered
        # roots that silently showed the owner the first root's models and
        # hid the rest. The server reported "checkpoint: ready, 2 models"
        # while the dropdown listed one, which is the shape of bug that a
        # green suite cannot see and pressing Browse finds immediately.
        entries = registry.entries(role)
        if not entries and registry.snapshot(role) is None:
            return None
        truncated = any(
            catalogue.snapshot() is not None and catalogue.snapshot().truncated
            for catalogue in registry.catalogues(role)
        )
        payload: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for entry in entries:
            label = entry.display_name
            count = seen.get(label, 0)
            seen[label] = count + 1
            if count:
                label = f"{label} ({count + 1})"
            payload.append(
                {
                    "name": label,
                    "model_id": entry.model_id,
                    "format": entry.format,
                    "size_bytes": entry.size_bytes,
                    "availability": entry.availability.value,
                    "truncated": truncated,
                }
            )
        return payload

    def _resident_selection(self) -> dict[str, Any] | None:
        """The lifecycle's resident components, or None.

        Optional on the presentation surface: a mock or legacy host may not
        implement it, and this route degrades to "nothing resident" rather than
        failing. It is NOT filled in from a desired selection -- absence of
        residency is the honest answer, and inventing one here is precisely the
        defect the hardcoded "Automatic" was.
        """

        reader = getattr(self._presentation, "resident_selection", None)
        if not callable(reader):
            return None
        try:
            resident = reader()
        except Exception:  # noqa: BLE001 - a control fill never breaks a page
            return None
        return dict(resident) if isinstance(resident, Mapping) else None

    def model_roots(self) -> dict[str, Any]:
        """Per-role configuration status. Carries counts, never paths."""
        registry = self._model_roots
        if registry is None:
            return {"configured": False, "roles": []}
        statuses = registry.describe()
        return {
            "configured": True,
            "roles": [statuses[role].to_dict() for role in sorted(statuses)],
        }

    def _models(self) -> list[_Model]:
        response = self._presentation.models()
        values: Any = response.get("models", []) if isinstance(response, Mapping) else response
        if not isinstance(values, (list, tuple)):
            values = []

        models: list[_Model] = []
        used_titles: set[str] = set()
        for index, value in enumerate(values):
            if isinstance(value, Mapping):
                model_id = str(
                    value.get(
                        "model_id",
                        value.get("id", value.get("value", f"mock-model-{index + 1}")),
                    )
                )
                name = str(
                    value.get(
                        "name",
                        value.get("label", value.get("title", model_id)),
                    )
                )
                description = str(value.get("description", ""))
            else:
                model_id = str(value)
                name = model_id
                description = ""
            title = name or model_id
            if title in used_titles:
                title = f"{title} [{model_id}]"
            used_titles.add(title)
            models.append(
                _Model(
                    model_id=model_id,
                    title=title,
                    name=name or model_id,
                    description=description,
                )
            )

        return models

    def _resident_model_id(self) -> str | None:
        """Read residency from the backend, never from adapter-local state."""

        residency = self._presentation.current_model()
        model_id = residency.get("model_id")
        return model_id if isinstance(model_id, str) and model_id else None

    def _selected_model(self) -> _Model | None:
        models = self._models()
        selected_id = self._resident_model_id()
        return next((model for model in models if model.model_id == selected_id), None)

    def _observe_active(self) -> _ProgressSnapshot:
        with self._state_lock:
            active = self._active
            last = self._last_progress
        if active is not None:
            try:
                event = self._presentation.poll(
                    active.job_id,
                    include_result=False,
                )
            except Exception:  # noqa: BLE001 - observation never raises
                return last
            return self._record_progress(active, event)

        # The coordinator's job. Opening the WebSocket gate for it and then
        # reporting the idle placeholder behind that gate would be the same
        # defect one layer down, so the snapshot follows the gate.
        job_id = self._coordinated_job_id()
        if job_id is None:
            return last
        try:
            # `job_status`, not `poll`: `poll` forwards to
            # `poll_or_stream_progress`, which is keyed by the BACKEND id --
            # `jobs.describe` calls it with `backend_job_id` for exactly that
            # reason. This id is the public one, and `job_status` is the read
            # that understands public ids and merges live backend progress
            # into them.
            event = self._presentation.job_status(job_id)
        except Exception:  # noqa: BLE001 - observation never raises
            return last
        return self._record_progress(
            _ActiveGeneration(
                job_id=job_id,
                # 0, not a guess. `_record_progress` prefers the backend's
                # own `total_steps` and falls back to this only when there is
                # none; inventing a denominator here would show the owner a
                # step budget no engine ever reported.
                steps=0,
                model_title="",
                request={},
                ignored_parameters=(),
            ),
            event,
        )

    def _record_progress(
        self,
        active: _ActiveGeneration,
        event: Mapping[str, Any],
    ) -> _ProgressSnapshot:
        with self._state_lock:
            previous = self._last_progress
        fraction = (
            _fraction_of(event)
            if "progress" in event or "progress_fraction" in event
            else previous.fraction
        )
        # Prefer the backend's own step counters. Deriving steps from an
        # integer percent cannot express "step 7 of 20" without rounding, so
        # the contract values win whenever the backend reports them.
        reported_total = _optional_int(event.get("total_steps"))
        total_steps = (
            reported_total
            if reported_total is not None
            else max(0, active.steps)
        )
        reported_step = _optional_int(event.get("step"))
        step = (
            min(total_steps, reported_step)
            if reported_step is not None
            else min(total_steps, max(0, round(fraction * total_steps)))
        )
        snapshot = _ProgressSnapshot(
            job_id=active.job_id,
            state=_state_of(event),
            fraction=fraction,
            step=step,
            total_steps=total_steps,
            message=str(event.get("message", _state_of(event).title())),
            backend_job_id=str(event.get("backend_job_id") or ""),
        )
        with self._state_lock:
            self._last_progress = snapshot
        return snapshot


@contextmanager
def _preference_refusals() -> Iterator[None]:
    """Turn a storage refusal into one this adapter's callers understand.

    Every stored-document call goes through here, so a new route cannot
    accidentally let a `PreferenceError` escape as a bare exception -- which
    `_send_api_error` would render as a generic 500 with no message the owner
    could act on.
    """

    try:
        yield
    except PreferenceError as error:
        for kind, status in _PREFERENCE_REFUSALS:
            if isinstance(error, kind):
                raise SourceFrontendAdapterError(
                    str(error), http_status=status
                ) from error
        raise SourceFrontendAdapterError(str(error), http_status=500) from error


def _recovery_status(error: Exception) -> int:
    """A recovery refusal, mapped to something the page can act on."""

    from .canvas_recovery import (
        MalformedRecovery,
        RecoveryNotWritten,
        UnknownDocument,
    )

    if isinstance(error, UnknownDocument):
        return 404
    if isinstance(error, MalformedRecovery):
        return 400
    if isinstance(error, RecoveryNotWritten):
        # The disk refused. Not the caller's fault and not something a retry
        # of the same bytes fixes, so it is a server error and says so.
        return 500
    return 500


def _split_path(path: str) -> tuple[str, dict[str, list[str]]]:
    parsed = urlsplit(str(path))
    return parsed.path, parse_qs(parsed.query, keep_blank_values=True)


def _bounded_identifier(value: Any, field: str, *, maximum: int = 200) -> str:
    """Require a non-empty, bounded string parameter or fail with a 400."""

    if not isinstance(value, str):
        raise SourceFrontendRequestError(f"{field} must be text.")
    trimmed = value.strip()
    if not trimmed:
        raise SourceFrontendRequestError(f"{field} is required.")
    if len(trimmed) > maximum:
        raise SourceFrontendRequestError(f"{field} is too long.")
    return trimmed


def _first_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    return values[0] if values else ""


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceFrontendRequestError("Expected a JSON object.")
    return dict(value)


def _state_of(value: Mapping[str, Any]) -> str:
    state = str(value.get("state", value.get("status", "unknown")) or "unknown")
    state = state.strip().lower()
    return _STATE_ALIASES.get(state, state)


def _fraction_of(value: Mapping[str, Any]) -> float:
    if "progress_fraction" in value:
        raw = value.get("progress_fraction", 0)
        scale = 1.0
    else:
        raw = value.get("progress", 0)
        scale = 100.0
    try:
        return min(1.0, max(0.0, float(raw) / scale))
    except (TypeError, ValueError):
        return 0.0


def _error_message(value: Mapping[str, Any]) -> str:
    error = value.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message", error.get("error", "Mock generation failed.")))
    if error:
        return str(error)
    return str(value.get("message", "Mock generation failed."))


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    """Read a non-negative integer contract field, or None when absent."""

    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _delivered_content_hash(image_data_url: Any) -> str:
    """The Gallery's key for one delivered image, or empty. AR5.4.

    ONE hash function, `gallery_index.content_hash` -- the same one the scan
    runs when it later meets the file, and the only value
    `gallery_service.py:1623-1630` will link an orphan metadata row on.

    THIS USED TO READ `metadata["image_sha256"]`, and that could never have
    worked. Its only writer in the tree is `mock_backend.py:441`, so on a real
    generation the value was `""` and the write beneath it never happened at
    all. On the mock it was worse than absent: a digest of SVG SOURCE TEXT,
    `sha256:`-prefixed -- wrong subject, wrong form, and unreadable by the real
    algorithm -- so even the mock's row could not have linked to anything.

    Empty for anything Pillow cannot decode, which includes the mock's SVG.
    That is honest rather than degraded: an image the Gallery cannot hash is an
    image its scan cannot index either.
    """

    if not isinstance(image_data_url, str) or not image_data_url.startswith("data:"):
        return ""
    _, _, encoded = image_data_url.partition(",")
    if not encoded:
        return ""
    import base64
    import binascii

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return ""
    try:
        # Deferred: `gallery_index` reaches Pillow, and it RAISES when Pillow
        # is absent rather than reporting every image as having no metadata.
        # A host without it must still be able to generate.
        from .gallery_index import content_hash

        return content_hash(raw)
    except Exception:  # noqa: BLE001 - no Gallery capability is not a failure
        return ""


def _resolved_seed(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        seed = int(value)
    except (TypeError, ValueError):
        return None
    return seed if seed >= 0 else None


def _ignored_generation_parameters(
    payload: Mapping[str, Any],
    supported: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Recognized settings the payload sets that the backend does not honour.

    The recognized vocabulary is fixed, but support comes from the backend, so
    a backend that gains sampler support stops being reported as ignoring it.
    Unknown payload keys are never reported, so unrelated metadata is silent.
    """

    return tuple(
        key
        for key in _RECOGNIZED_GENERATION_KEYS
        if key not in supported and key in payload and bool(payload[key])
    )


def _ignored_parameters_notice(
    ignored_parameters: tuple[str, ...],
) -> str | None:
    if not ignored_parameters:
        return None
    return "Ignored unsupported settings: " + ", ".join(
        ignored_parameters
    ) + "."


def _empty_generation_response(
    error: str | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "images": [],
        "image_paths": [],
        "float_paths": [],
        "mask_paths": [],
        "float_stats": [],
        "content_hashes": [],
        "infotexts": [],
        "session_entries": [],
        "settings": deepcopy(dict(settings or {})),
        "seed": -1,
        "task_id": "",
        "error": error,
        "notice": None,
    }


def _infotext(
    request: Mapping[str, Any],
    model_title: str,
    seed: int | None,
    *,
    template: str = "",
    choices: str = "",
) -> str:
    prompt = str(request.get("positive_prompt", "")).strip()
    negative = str(request.get("negative_prompt", "")).strip()
    lines = [prompt]
    if negative:
        lines.append(f"Negative prompt: {negative}")

    # The Studio-specific fields, written in the order `gallery_metadata`
    # parses them and only when they say something. `Template:` is the prompt
    # BEFORE wildcards resolved, which is why the Gallery keeps it in its own
    # column: glue it onto the prompt and every search for the resolved text
    # matches the template too.
    written = (template or "").strip()
    if written and written != prompt:
        lines.append(f"Template: {written}")
    if (choices or "").strip():
        lines.append(f"Studio dynamic prompts: {choices.strip()}")
    fields = [
        f"Steps: {request.get('steps', 30)}",
        f"CFG scale: {request.get('cfg_scale', 5.0)}",
    ]
    if seed is not None:
        fields.append(f"Seed: {seed}")
    fields.extend(
        (
            "Size: {width}x{height}".format(
                width=request.get("width", 768),
                height=request.get("height", 768),
            ),
            f"Model: {model_title}",
        )
    )
    lines.append(", ".join(fields))
    return "\n".join(lines)
