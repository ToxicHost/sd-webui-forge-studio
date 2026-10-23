"""Studio application service, independent of presentation and Forge."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from threading import Lock
from typing import Any

from .backend import BackendAdapter
from .contracts import (
    BackendStatus,
    CancellationResult,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
    StructuredError,
    StudioError,
)
from .result_delivery import ResultAsset, ResultPayload, ResultRegistry


def gradio_in_process() -> bool:
    """Whether any real Gradio module is loaded in THIS process.

    The owner requirement is that Studio's runtime imports no Gradio, and the
    defect it guards against was never a literal `import gradio` in Studio
    source -- it was a transitive edge four modules deep through inherited
    backend code. So the check is `sys.modules`, which sees every import
    however it arrived, rather than anything Studio knows about its own
    behaviour.

    Reported by `runtime_status`, so an owner can ask a running Studio and get
    an answer rather than "UNKNOWN".
    """

    return any(
        name == "gradio" or name.startswith("gradio.") for name in sys.modules
    )


#: No seed ceiling. Named rather than written as a number, so that "this has
#: no upper bound" is something the code says rather than something a reader
#: infers from a large constant.
#:
#: The old bound was 4294967295, and it was not a refusal: the browser silently
#: replaced a larger seed with a fresh random one, so the owner got a different
#: image and no message. Nothing in the core wants 32 bits.
#: `Evidence/source-review/AR6.2-seed-substitution.md`.
_UNBOUNDED_SEED = sys.maxsize

#: BM1. How many result handles the registry keeps: the session strip's
#: largest setting (200) plus one full batch (16 x 8 = 128), rounded up.
RESULT_HANDLES = 400


class StudioApplication:
    def __init__(
        self,
        backend: BackendAdapter,
        *,
        result_root: Path | None = None,
        headless_runtime: object | None = None,
        model_lifecycle: object | None = None,
        selected_backend: str = "mock",
        selection_resolver: object | None = None,
    ) -> None:
        self._backend = backend
        # BM1. Room for the session strip at its largest (200 entries) and a
        # whole Batch-mode job on top (16 x 8). The registry holds paths, not
        # pixels; at the old 64 one large batch evicted its own first pictures
        # before the page could show them.
        self._results = (
            ResultRegistry(result_root, max_entries=RESULT_HANDLES)
            if result_root is not None else None
        )
        self._assets: dict[str, ResultAsset] = {}
        #: BM1. A Batch-mode job's handles for pictures 2..N, in order.
        self._picture_assets: dict[str, list[ResultAsset | None]] = {}
        self._assets_lock = Lock()
        # Optional headless integration, injected. Studio holds it as an opaque
        # object and reads only its declared reporting surface -- never Forge
        # globals, and never a Torch or Gradio import.
        self._headless_runtime = headless_runtime
        # The product model lifecycle, injected. When present it gates
        # generation: a job leases the warm session it owns, so the
        # application and the backend adapter can never end up using two
        # different sessions. When absent, behaviour is exactly as before.
        self._model_lifecycle = model_lifecycle
        self._selected_backend = str(selected_backend or "mock")
        # Turns the three opaque ids on a request into a ResolvedSelection,
        # revalidating containment through the role catalogues. Injected, so
        # the application never reaches for a registry itself and hosts
        # without one behave exactly as before.
        self._selection_resolver = selection_resolver
        #: Attached after construction, for the same reason as the resolver.
        self._detector_catalogue: object | None = None
        #: MH1. Lease tokens of model-handoff jobs that are running, and those
        #: a cancel reached (`cancel_handoff`). Between the two halves no
        #: backend job is in flight, so this is the only thing a cancel there
        #: can reach.
        self._handoffs: set[str] = set()
        self._handoffs_cancelled: set[str] = set()
        self._handoff_lock = Lock()

    def use_selection_resolver(self, resolver: object | None) -> None:
        """Attach the resolver after construction.

        The host builds its application before it builds the model-root
        registry -- the registry needs the configured roots, and configuring
        them tolerantly is a startup step that must not prevent Studio from
        starting. Rather than reorder that, the resolver is attached once the
        registry exists.

        Explicit method rather than a public attribute, so the wiring is
        greppable and a host that never calls it is visibly a host without
        auto-load rather than one that silently lost it.
        """

        self._selection_resolver = resolver

    def use_detector_catalogue(self, reader: object | None) -> None:
        """Attach the detector catalogue reader after construction.

        Same shape and same reason as `use_selection_resolver`: the catalogue
        needs the model-root registry, which the host builds after the
        application. A zero-argument callable answering the admitted
        detectors.

        A host that never calls it refuses nothing, exactly as an unavailable
        engine registry refuses no sampler. An empty catalogue is
        indistinguishable from an unconfigured root, and refusing a
        generation because the owner has not pointed at a detector folder
        would be worse than letting the slot fail where it runs.
        """

        self._detector_catalogue = reader

    def use_detector_resolver(self, resolver: object | None) -> None:
        """Forward the name -> path resolver to the backend that needs it.

        The application does not use it: resolution happens at request
        translation, inside the headless runtime, which has no registry of its
        own. This is the hop that gets it there.
        """

        forward = getattr(self._backend, "use_detector_resolver", None)
        if forward is not None:
            forward(resolver)

    def _detectors(self) -> tuple:
        reader = getattr(self, "_detector_catalogue", None)
        if reader is None:
            return ()
        try:
            return tuple(reader())
        except Exception:  # noqa: BLE001 - a missing catalogue is an empty one
            return ()

    def runtime_status(self) -> dict[str, object]:
        """Report the selected backend and what it can currently do.

        Pure read. Reports only what is established: a value the application
        cannot determine is reported as ``"UNKNOWN"``, never guessed.
        """

        residency = self.get_current_model()
        runtime = self._headless_runtime
        if runtime is None:
            return {
                "selected_backend": self._selected_backend,
                "backend_selection_honoured": True,
                "headless_state": "not_selected",
                "legacy_compatibility_state": "disabled",
                # OBSERVED, not "UNKNOWN". Whether Gradio is loaded is a
                # property of this process, and `sys.modules` answers it
                # exactly -- including imports Studio did not perform itself,
                # which is the case that matters. Reporting UNKNOWN here was
                # not caution; the answer was always one lookup away, and it
                # is the acceptance evidence for the no-Gradio requirement.
                "gradio_imported": gradio_in_process(),
                "model_loaded": bool(residency.loaded),
                "generation_available": True,
                "blocking_reason": "",
                "catalogue_configured": False,
                "catalogue_ready": False,
                "catalogue_count": 0,
                "model_load_plumbing_ready": False,
                "real_model_load_authorized": False,
            }

        identity = runtime.get_runtime_identity()
        capability = runtime.get_runtime_capability()
        configured = bool(getattr(runtime, "catalogue_configured", False))
        count = 0
        if configured:
            try:
                count = len(runtime.list_models())
            except Exception:
                # A catalogue that cannot be read reports zero rather than
                # failing the status route or leaking why.
                configured = False
        reason = ""
        readiness = getattr(runtime, "_readiness", None)
        if readiness is not None and getattr(readiness, "blockers", ()):
            first = readiness.blockers[0]
            reason = f"{first.code} at {first.module}"
        elif not capability.generation_implemented:
            reason = "HEADLESS_GENERATION_NOT_IMPLEMENTED"

        return {
            "selected_backend": self._selected_backend,
            "backend_selection_honoured": True,
            "headless_state": runtime.state.value,
            "legacy_compatibility_state": (
                "enabled" if identity.compatibility_mode else "disabled"
            ),
            "gradio_imported": bool(identity.gradio_imported),
            "model_loaded": bool(identity.model_loaded),
            "generation_available": bool(capability.generation_implemented),
            "blocking_reason": reason,
            # Phase 2A additions. Counts only -- never a root, a path, a
            # filename, a traceback, a user name, or an environment value.
            "catalogue_configured": configured,
            "catalogue_ready": configured and count > 0,
            "catalogue_count": count,
            "model_load_plumbing_ready": configured,
            "real_model_load_authorized": False,
        }

    def get_backend_status(self) -> BackendStatus:
        return self._backend.get_backend_status()

    def list_models(self) -> tuple[ModelSummary, ...]:
        return self._backend.list_models()

    def load_model(self, model_id: str) -> ModelResidency:
        return self._backend.load_model(model_id)

    def unload_model(self) -> ModelResidency:
        return self._backend.unload_model()

    def get_current_model(self) -> ModelResidency:
        return self._backend.get_current_model()

    def get_capability(
        self,
        model_id: str,
        operation: str = "txt2img",
    ) -> ModelCapability:
        return self._backend.get_capability(model_id, operation)

    def supported_generation_parameters(self) -> frozenset[str]:
        return self._backend.supported_generation_parameters()

    @property
    def model_lifecycle(self):
        """The lifecycle service this application gates through, or None."""

        return self._model_lifecycle

    def submit_generation(
        self,
        request: GenerationRequest,
        *,
        job_token: str | None = None,
    ) -> GenerationJobIdentity:
        # The lifecycle gate runs BEFORE request validation on purpose. With no
        # model loaded there is nothing a request could be valid *for*, and
        # reporting INVALID_GENERATION_REQUEST there tells the user their
        # request is malformed when the real answer is that nothing is warm.
        lifecycle = self._model_lifecycle
        if lifecycle is None or not getattr(lifecycle, "gates_generation", False):
            self._hires_model(request, None)    # MH1: refuses a Hires model here
            self._validate_request(request)
            # No lifecycle wired: the pre-Phase-2 arrangement, where the
            # backend owns whatever session it was given. Unchanged so the
            # mock host and every existing suite behave exactly as before.
            return self._backend.submit_generation(request)
        # Generation OWNS model readiness. This used to be the opposite: the
        # lease refused by name when nothing was warm, on the reasoning that a
        # generation must never open a model. Forge Neo does the reverse --
        # `forge_model_reload()` runs *at* generation time -- and requiring an
        # explicit load first is the Load button the product is removing.
        #
        # The guarantee underneath the old refusal survives, and is why the
        # selection travels on the request: a generation still never runs on a
        # model nobody asked for, because the job names the model it wants
        # instead of inheriting whatever happened to be resident.
        #
        # Order matters. Readiness is reconciled BEFORE the lease is taken:
        # `ensure_loaded` may switch, and switching under a held lease would
        # pull the session out from under the job that holds it.
        request = self._ensure_requested_model(request, lifecycle)
        hires_model = self._hires_model(request, lifecycle)    # MH1, before the lease
        # `job_token` lets the coordinator queue this job under the SAME
        # public id it handed the caller before submission; without one the
        # lifecycle mints its own, exactly as before.
        begin_as = getattr(lifecycle, "begin_job_as", None)
        token = (
            begin_as(job_token) if callable(begin_as) else lifecycle.begin_job()
        )
        try:
            self._validate_request(request)
            if hires_model is not None:
                return self._run_handoff(request, hires_model, token, lifecycle)
            return self._backend.submit_generation(request)
        finally:
            lifecycle.end_job(token)

    # -- model handoff (MH1) -------------------------------------------------

    @staticmethod
    def _hires_model_named(request: Any) -> bool:
        hires = getattr(request, "hires", None)
        return bool(
            hires is not None and getattr(hires, "enabled", False)
            and getattr(hires, "model", None)
        )

    def _hires_model(self, request: Any, lifecycle: Any) -> Any:
        """The Hires model as a `ResolvedSelection`, or None for "Same as base".

        None also when it names exactly the base selection: that is the same
        model, and a switch to it would be a no-op paid for in bookkeeping.

        Resolved BEFORE the lease, like the base selection, so an unknown or
        vanished Hires model is refused before any pass is paid for. A host
        that cannot switch sessions (no lifecycle, no catalogue) refuses a
        named Hires model by name rather than running Hires on the base model
        and reporting it done.
        """

        if not self._hires_model_named(request):
            return None
        resolver = self._selection_resolver
        if resolver is None or not callable(getattr(lifecycle, "switch_for_job", None)):
            self._invalid(
                "hires.model",
                "Hires on another model needs Studio's own model folders; "
                "this host has none. Set Hires Model to Same as base.",
            )
        operation = str(getattr(request, "operation", "") or "txt2img").lower()
        if operation != "txt2img":
            self._invalid(
                "hires.model",
                "Hires on another model works for text-to-image jobs. For a "
                "picture operation, set Hires Model to Same as base.",
            )
        from types import SimpleNamespace

        resolved = resolver(SimpleNamespace(model_selection=dict(request.hires.model)))
        base = resolver(request)
        if resolved is None:
            return None
        if base is not None and base.profile_id == resolved.profile_id:
            return None
        return resolved

    def _run_handoff(self, request: Any, hires_model: Any, token: str,
                     lifecycle: Any) -> GenerationJobIdentity:
        """One job on two models: the base pass on this job's model, then the
        Hires pass and Auto Detail on the Hires model.

        Neo does this inside `StableDiffusionProcessingTxt2Img.sample` by
        reloading through its checkpoint catalogue (`hr_checkpoint_name`),
        which Studio's directly loaded sessions cannot do. Studio switches its
        own session instead, between two backend jobs under ONE lease:

        1. the whole job -- every picture of a batch -- on the base model,
           with Hires and Auto Detail off, kept in memory and never saved;
        2. `switch_for_job` to the Hires model (the owner's call, D3: swap,
           never two models resident; one switch per job, not per picture);
        3. the same request on the Hires model, whose main pass is each
           picture from (1) -- Neo's `firstpass_image` -- then Auto Detail
           (D1: on the Hires model) and the save.

        The base model is not restored afterwards. The next job names its
        own model and `ensure_loaded` reconciles it, which is the switch Neo
        makes at its next iteration anyway (`processing.py:943-945`).

        The returned identity is the second job's, on the session that holds
        it, so progress, result and cancel all read from the right table. A
        first half that did not complete is returned as it is.
        """

        from .contracts import HandoffStage

        base_request = dataclasses.replace(
            request,
            hires=None,
            auto_detail=None,
            # Nothing of the base half is published, so nothing may be left
            # pending for a picture that will never be adopted.
            attention_capture_requested=False,
            handoff=HandoffStage(stage="base"),
        )
        with self._handoff_lock:
            self._handoffs.add(token)
            self._handoffs_cancelled.discard(token)
        import time

        started = time.monotonic()
        try:
            first = self._backend.submit_generation(base_request)
            if not self._backend_completed(first.job_id):
                return first
            result = self._backend.get_result(first.job_id)
            pictures = tuple(
                str(picture.image_data_url or "")
                for picture in (result, *tuple(result.pictures or ()))
            )
            metadata = dict(result.metadata or {})
            self._raise_if_handoff_cancelled(token)
            lifecycle.switch_for_job(token, hires_model)
            self._raise_if_handoff_cancelled(token)
        finally:
            with self._handoff_lock:
                self._handoffs.discard(token)
                self._handoffs_cancelled.discard(token)

        hires_request = dataclasses.replace(
            request,
            model_id=str(hires_model.profile_id),
            model_selection=dict(request.hires.model),
            # The composition is the base half's and is already in the
            # picture. ControlNet models, regional runners and Klein
            # references belong to one family; none is carried across.
            controlnet=(),
            regions=(),
            reference_images=(),
            attention_capture_requested=False,
            handoff=HandoffStage(
                stage="hires",
                pictures=pictures,
                base_model=str(metadata.get("model_label", "") or ""),
                base_family=str(metadata.get("model_family", "") or ""),
                base_infotext=str(metadata.get("infotext", "") or ""),
                base_seconds=round(time.monotonic() - started, 3),
            ),
        )
        return self._backend.submit_generation(hires_request)

    def _validate_refiner(self, request: Any) -> None:
        """MH2. What the Refiner cannot be combined with, refused by name.

        Text-to-image only: the switch is in the main pass's sampling, and the
        image operations' second passes are Studio's own and were not built
        for it. Not with a Hires Model: the Refiner already decides which
        model finishes the picture (D1), and two switches in one job is a
        recipe nobody asked for.
        """

        refiner = getattr(request, "refiner", None)
        if refiner is None or not getattr(refiner, "enabled", False):
            return
        operation = str(getattr(request, "operation", "") or "txt2img").lower()
        if operation != "txt2img":
            self._invalid(
                "refiner",
                "The Refiner works for text-to-image jobs. Turn it off for a "
                "picture operation.")
        if self._hires_model_named(request):
            self._invalid(
                "refiner",
                "Use the Refiner or Hires Fix > Model, not both: with the "
                "Refiner on, Hires already runs on the Refiner's model.")
        if not 1 <= int(refiner.switch_at_step) < int(request.steps):
            self._invalid(
                "refiner.switch_at_step",
                f"The Refiner switch step must be between 1 and "
                f"{int(request.steps) - 1} for {int(request.steps)} steps.")

    def _backend_completed(self, job_id: str) -> bool:
        try:
            state = self._backend.poll_or_stream_progress(job_id).state
        except StudioError:
            return False
        return str(getattr(state, "value", state)).lower() == "completed"

    def _raise_if_handoff_cancelled(self, token: str) -> None:
        with self._handoff_lock:
            cancelled = token in self._handoffs_cancelled
        if cancelled:
            raise StudioError(
                StructuredError(
                    code="MODEL_JOB_CANCELLED",
                    message="The job was cancelled between its base and Hires models.",
                    field="job_id",
                )
            )

    def cancel_handoff(self, token: str) -> bool:
        """MH1. Stop a model-handoff job at its next boundary.

        True when `token` is a running handoff, which then stops before the
        switch, or before the Hires half begins. A cancel that arrives while a
        half is sampling also reaches that half's backend job through the
        ordinary route; this flag is what stops the job from going on to the
        next half.
        """

        with self._handoff_lock:
            if token not in self._handoffs:
                return False
            self._handoffs_cancelled.add(token)
            return True

    def _ensure_requested_model(self, request: Any, lifecycle: Any) -> Any:
        """Make the resident session match what this request asked for.

        Returns the request to submit. When the job named a selection but no
        `model_id` -- which is what generating from `NO_MODEL` looks like,
        because there is nothing resident for the client to name -- the
        resolved selection's identity is filled in, so every downstream reader
        still sees a request that names its model.

        Silent no-op unless three things line up: a resolver was injected, the
        lifecycle offers `ensure_loaded`, and the request actually carries a
        selection. Any host missing one of those keeps its previous behaviour
        exactly, including the pre-lifecycle mock arrangement -- so this adds a
        capability without changing what already worked.

        A request that carries no selection is left to the lease, which still
        refuses when nothing is warm. That is the correct answer for it: with
        no model named and none resident, there is nothing to reconcile.
        """

        resolver = self._selection_resolver
        ensure_loaded = getattr(lifecycle, "ensure_loaded", None)
        if resolver is None or not callable(ensure_loaded):
            return request
        resolved = resolver(request)
        if resolved is None:
            return request
        ensure_loaded(resolved)
        if not str(getattr(request, "model_id", "") or "").strip():
            identity = str(getattr(resolved, "profile_id", "") or "")
            if identity:
                request = dataclasses.replace(request, model_id=identity)
        return request

    def poll_or_stream_progress(self, job_id: str) -> ProgressEvent:
        return self._backend.poll_or_stream_progress(job_id)

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        """The latest live-preview frame for one job. Never raises.

        Every backend answers, because `BackendAdapter.preview_frame` is
        concrete: one that cannot decode a preview returns no frame rather
        than failing the socket that asked.
        """

        return self._backend.preview_frame(job_id)

    def cancel_generation(self, job_id: str) -> CancellationResult:
        result = self._backend.cancel_generation(job_id)
        self._release_result(job_id)
        return result

    def get_result(self, job_id: str) -> GeneratedResult:
        return self._backend.get_result(job_id)

    def forget_job(self, job_id: str) -> bool:
        """LV1. Drop a finished job the caller has read (a Live frame). A
        backend without `forget_job` keeps it, as every backend did before."""

        self._release_result(job_id)
        forget = getattr(self._backend, "forget_job", None)
        return bool(forget(job_id)) if callable(forget) else False

    def result_asset(self, job_id: str) -> ResultAsset | None:
        """Mint or return the opaque browser handle for a completed result.

        Returns ``None`` when no result root is configured or the backend kept
        the result inline. Registration is idempotent per job so repeated
        observation does not grow the registry.
        """

        if self._results is None:
            return None
        with self._assets_lock:
            existing = self._assets.get(job_id)
        if existing is not None:
            return existing
        try:
            result = self._backend.get_result(job_id)
        except StudioError:
            # Cancelled, failed, and unknown jobs have nothing to deliver.
            # Registration failures below still raise: those are defects.
            return None
        if not result.output_path:
            return None
        asset = self._results.register(
            Path(result.output_path),
            media_type=result.mime_type,
        )
        with self._assets_lock:
            # Another thread may have registered first; keep one handle per job
            # and release the loser so the registry does not leak an entry.
            winner = self._assets.setdefault(job_id, asset)
        if winner is not asset:
            self._results.forget(asset.handle)
        return winner

    def result_picture_assets(self, job_id: str) -> list[ResultAsset | None]:
        """BM1. Handles for a Batch-mode job's pictures after the first, in
        the order `GeneratedResult.pictures` holds them.

        Registered once per job, like `result_asset`. A picture whose file
        cannot be registered is None in its place rather than failing the
        others. Empty for a job of one picture, and with no registry.
        """

        if self._results is None:
            return []
        with self._assets_lock:
            existing = self._picture_assets.get(job_id)
        if existing is not None:
            return list(existing)
        try:
            result = self._backend.get_result(job_id)
        except StudioError:
            return []
        assets: list[ResultAsset | None] = []
        for picture in tuple(getattr(result, "pictures", ()) or ()):
            try:
                assets.append(self._results.register(
                    Path(picture.output_path), media_type=picture.mime_type)
                    if picture.output_path else None)
            except StudioError:
                assets.append(None)
        with self._assets_lock:
            winner = self._picture_assets.setdefault(job_id, assets)
        if winner is not assets:
            for asset in assets:
                if asset is not None:
                    self._results.forget(asset.handle)
        return list(winner)

    def allow_result_roots(self, roots: Any) -> None:
        """OF1. Let the registry serve files in the owner's trusted save
        folders, as well as in the result root. `roots` is called on every
        registration and read, so untrusting a folder stops serving it."""

        if self._results is not None:
            self._results.allow_roots(roots)

    def register_saved_file(self, path: Path, *, media_type: str) -> ResultAsset | None:
        """P3. A handle for a file Studio SAVED into its result root.

        Save and Canvas Export write there, and the page opens the result by
        handle -- the Extension opened `/file=<absolute path>`, which Standalone
        does not serve. None when there is no registry, or the file is outside
        every root the registry serves (the result root and, since OF1, the
        save roots -- trusted and Gallery-linked folders): the save still
        happened, there is just nothing to open.
        """

        if self._results is None:
            return None
        try:
            return self._results.register(Path(path), media_type=media_type)
        except StudioError:
            return None

    def read_result_asset(self, handle: str) -> ResultPayload:
        if self._results is None:
            raise StudioError(
                StructuredError(
                    code="RESULT_NOT_FOUND",
                    message="That result is not available.",
                )
            )
        return self._results.read(handle)

    def shutdown(self) -> None:
        """Release backend and delivery resources. Safe to call repeatedly."""

        try:
            self._backend.shutdown()
        finally:
            if self._results is not None:
                self._results.clear()
            with self._assets_lock:
                self._assets.clear()
                self._picture_assets.clear()

    def _release_result(self, job_id: str) -> None:
        """Drop any delivery handle held for one job."""

        with self._assets_lock:
            asset = self._assets.pop(job_id, None)
            pictures = self._picture_assets.pop(job_id, None) or []
        if self._results is not None:
            for held in (asset, *pictures):
                if held is not None:
                    self._results.forget(held.handle)

    def _validate_request(self, request: GenerationRequest) -> None:
        model_ids = {model.model_id for model in self.list_models()}
        if request.model_id not in model_ids:
            self._invalid("model_id", "Select an available Studio model.")
        # TYPE only, no length. The 4000-character cap that was here is gone
        # for the same reason as the one in `presentation.py`: nothing in the
        # core bounds a prompt, and removing it in one layer while leaving it
        # in another is the mistake AR6.1 already made once with steps.
        if not isinstance(request.positive_prompt, str):
            self._invalid("positive_prompt", "Positive prompt must be text.")
        if not isinstance(request.negative_prompt, str):
            self._invalid("negative_prompt", "Negative prompt must be text.")
        # A FLOOR only. -1 is "surprise me"; there is no ceiling, because
        # nothing in the core wants one and the old one was not refused --
        # it was silently replaced with a different seed.
        self._integer_between("seed", request.seed, -1, _UNBOUNDED_SEED)
        # A floor only, via the same helper width and height use. The
        # 1-150 range here disagreed with the execution cap of 40 below
        # it, so admission accepted values it then let fail downstream.
        self._positive_integer("steps", request.steps)
        if (
            isinstance(request.cfg_scale, bool)
            or not isinstance(request.cfg_scale, (int, float))
            or not 0 <= float(request.cfg_scale) <= 30
        ):
            self._invalid("cfg_scale", "CFG must be between 0 and 30.")
        for field, value in (
            ("width", request.width),
            ("height", request.height),
        ):
            self._positive_integer(field, value)
        self._validate_refiner(request)
        self._validate_engine_choices(request)

    def _validate_engine_choices(self, request: GenerationRequest) -> None:
        """Refuse a sampler or scheduler the engine will not dispatch.

        An unknown SCHEDULER was accepted, reported as a success, and silently
        discarded. Neo looks it up with `sd_schedulers.schedulers_map.get(name)`
        and, on a miss, falls through to `self.model_wrap.get_sigmas(steps)` --
        the model's default sigmas -- with no exception and no log line
        (`modules/sd_samplers_kdiffusion.py`, `get_sigmas`). Proven live: the
        two different nonsense values `"Karrass"` and `"ZZZNonsense"` produced
        one byte-identical image, `error: null`, and not one line of diagnostic.
        That is the "control that does nothing" this phase exists to remove.

        Neo's own normaliser cannot help here. `fix_p_invalid_sampler_and_scheduler`
        runs inside `process_images` (`modules/processing.py`), and Studio calls
        `process_images_inner` DIRECTLY, so it never executes.

        The SAMPLER path already refuses loudly -- `create_sampler` asserts
        `bad sampler name` -- so this mainly closes an asymmetry. It is checked
        anyway, because "loudly" there means an AssertionError from inside the
        backend rather than a named field the owner can act on.

        Gated on the registry being REAL. It is legitimately EMPTY on a mock
        host, and on any process where Neo has not been stood up, because
        `neo_registries` refuses to initialise the engine merely to answer a
        list. Rejecting a valid value there would be a worse bug than the one
        this closes. On a live host the gate is already satisfied by the time
        this runs: `submit_generation` reconciles readiness via `ensure_loaded`
        BEFORE it validates, so the engine is standing and the lists are real.

        An empty value is the owner declining to choose, which is always valid
        and becomes the engine default downstream.
        """

        # Local import, matching `presentation.registries`: a module-scope
        # import would drag Neo's namespace into every process that merely
        # imports the application, including the ones the purity tests keep
        # clean.
        try:
            from forge_headless.neo_registries import (
                is_known_sampler,
                is_known_scheduler,
                is_known_upscaler,
                read_registries,
            )

            registries = read_registries()
        except BaseException:  # noqa: BLE001 - see below; never fatal
            # `read_registries` RE-RAISES HeadlessError rather than reporting
            # unavailability: `_read` raises REGISTRY_UNAVAILABLE whenever
            # `modules.sd_samplers` is absent from sys.modules, and the handler
            # above the generic one lets it through. So on a mock host, and in
            # every suite that does not stand Neo up, calling it RAISES.
            # `presentation.registries` has always wrapped it in exactly this
            # catch; reading `.available` without one refuses every generation
            # on a host that has no engine. Caught by the canonical suite.
            return
        if not registries.available:
            return
        for field, known in (
            ("sampler", is_known_sampler),
            ("scheduler", is_known_scheduler),
        ):
            chosen = str(getattr(request, field, "") or "")
            if chosen and not known(chosen, registries):
                self._invalid(
                    field,
                    f"This engine does not dispatch the {field} {chosen!r}. "
                    f"Choose one it offers, or leave it empty for the default.",
                )

        # The Hires upscaler, for the same reason and found the same way: by
        # sending a name the engine does not have and watching what happened.
        #
        # Unchecked, it reached dispatch and died there as a bare `ValueError`
        # -- AFTER the base pass had already been paid for -- and reached the
        # owner as "GENERATION_FAILED: ValueError", which names nothing they
        # can act on. Loud but uninformative is only marginally better than
        # silent, and it costs a whole generation to discover.
        #
        # The offered list is included in the message because an upscaler name
        # is not guessable: it is whatever file happens to sit in the model
        # directory.
        hires = getattr(request, "hires", None)
        if hires is not None and getattr(hires, "enabled", False):
            chosen = str(getattr(hires, "upscaler", "") or "")
            if chosen and not is_known_upscaler(chosen, registries):
                offered = ", ".join(
                    registries.latent_upscalers + registries.image_upscalers
                )
                self._invalid(
                    "hires.upscaler",
                    f"This engine does not dispatch the upscaler {chosen!r}. "
                    f"Offered: {offered}. Leave it empty for the default.",
                )

        self._validate_detectors(request)

    def _validate_detectors(self, request: object) -> None:
        """Each ENABLED Auto Detail slot names a detector the catalogue offers.

        `is_known_detector` was the fifth `is_known_*` written, exported and
        left with no callers; its own docstring says it "exists to be wired at
        the same time as the field it guards, not afterwards". This is that
        wiring, at the same seam and in the same shape as `hires.upscaler`.

        Only enabled slots are checked. A disabled slot is a true no-op, and an
        owner who never turned slot 3 on has no reason to have chosen a
        detector for it.

        An EMPTY catalogue refuses nothing, matching `if not
        registries.available: return` one function up. A detector catalogue has
        no `available` flag, so an unconfigured root and a configured empty one
        are the same observation -- and refusing every generation because the
        owner has not pointed at a folder would break base-only jobs that never
        asked for Auto Detail.
        """

        from .detector_catalogue import is_known_detector

        auto_detail = getattr(request, "auto_detail", None)
        if auto_detail is None or not getattr(auto_detail, "enabled", False):
            return
        detectors = self._detectors()
        if not detectors:
            return
        offered = ", ".join(detector.name for detector in detectors)
        for position, slot in enumerate(getattr(auto_detail, "slots", ()), start=1):
            if not getattr(slot, "enabled", False):
                continue
            # Word Detail (S8b): a slot that details words names no detector by
            # design -- its region comes from the job's own heatmap capture.
            # `presentation` already refuses words AND a detector together.
            if getattr(slot, "words", ()):
                continue
            chosen = str(getattr(slot, "detector", "") or "")
            if not chosen:
                self._invalid(
                    f"auto_detail.slots[{position}].detector",
                    f"Auto Detail slot {position} is on but names no detector. "
                    f"Offered: {offered}.",
                )
            if not is_known_detector(chosen, detectors):
                self._invalid(
                    f"auto_detail.slots[{position}].detector",
                    f"No admitted detector is named {chosen!r}. "
                    f"Offered: {offered}.",
                )

    def _positive_integer(self, field: str, value: object) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            self._invalid(
                field,
                f"{field.title()} must be a positive integer.",
            )

    def _integer_between(
        self,
        field: str,
        value: object,
        minimum: int,
        maximum: int,
    ) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            self._invalid(
                field,
                f"{field.title()} must be an integer from "
                f"{minimum} through {maximum}.",
            )

    @staticmethod
    def _invalid(field: str, message: str) -> None:
        raise StudioError(
            StructuredError(
                code="INVALID_GENERATION_REQUEST",
                message=message,
                field=field,
            )
        )
