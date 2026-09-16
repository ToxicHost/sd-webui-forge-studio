"""Post-generation CUDA residual attribution: telemetry, stages, first-step truth.

Three diagnostic pieces, all inert until something calls them, and all import-safe:
importing this module pulls in no torch, no `backend`, no `modules`, no gradio,
and binds no socket. Every heavy import is inside a function.

**Telemetry classification.** The live-smoke rerun's classifier read
`allocated`/`reserved` while `VramSample.to_dict()` emits `allocated_bytes`/
`reserved_bytes`, so it saw `-1` and reached its verdict by fallthrough rather
than by measurement. `read_sample` now consumes the canonical schema only and
fails closed; there is no alternate spelling and no `-1` default.

**Stage census.** `StageSnapshotRecorder` records S0-S15 with allocated,
reserved and both maxima, plus the ownership counts that decide whether a byte
figure may be classified at all. A stage that was never reached stays
unavailable; nothing is interpolated.

**First-step truth.** Retained Forge writes `state.sampling_step = d["i"]` at
`modules/sd_samplers_common.py:431` -- a **zero-based** index -- and writes its
own one-based translation on the next line, `state.preview_step = step + 1`.
`launch_sampling` (`:435-440`) writes `sampling_step = 0` and `preview_step = 0`
before the loop, so a zero-based reading of `0` is ambiguous between "reset" and
"first step completed" while a `preview_step` of `1` is not. The observer
therefore takes `preview_step >= 1` as the authoritative completed-step event
and cross-checks it against the zero-based counter.

Nothing here is on the product request path. `forge_studio` and `forge_headless`
do not import this module.
"""

from __future__ import annotations

from typing import Any, Callable


# --------------------------------------------------------------- telemetry

#: The canonical serialized shape of `forge_headless.load_telemetry.VramSample`.
ALLOCATED_FIELD = "allocated_bytes"
RESERVED_FIELD = "reserved_bytes"
VRAM_SAMPLE_FIELDS = (ALLOCATED_FIELD, RESERVED_FIELD)

#: Spellings that must never be accepted. They are what the rerun's classifier
#: read, and silently tolerating them is how a schema drift becomes a `-1`.
REJECTED_FIELD_SPELLINGS = ("allocated", "reserved", "allocated_mb", "reserved_mb")

ABSOLUTE_ZERO = "ABSOLUTE_ZERO"
KNOWN_GENERATION_RESIDUAL_MATCH = "KNOWN_GENERATION_RESIDUAL_MATCH"
NEW_OR_GREATER_RESIDUAL = "NEW_OR_GREATER_RESIDUAL"
LOWER_BUT_NONZERO_RESIDUAL = "LOWER_BUT_NONZERO_RESIDUAL"
TELEMETRY_SCHEMA_INVALID = "TELEMETRY_SCHEMA_INVALID"
OWNERSHIP_STATE_INCONSISTENT = "OWNERSHIP_STATE_INCONSISTENT"

CLASSIFICATIONS = (
    ABSOLUTE_ZERO,
    KNOWN_GENERATION_RESIDUAL_MATCH,
    NEW_OR_GREATER_RESIDUAL,
    LOWER_BUT_NONZERO_RESIDUAL,
    TELEMETRY_SCHEMA_INVALID,
    OWNERSHIP_STATE_INCONSISTENT,
)

#: The residual recorded by attempts 05/06 and carried since.
KNOWN_RESIDUAL_ALLOCATED = 9_568_256
KNOWN_RESIDUAL_RESERVED = 23_068_672


class TelemetrySchemaError(ValueError):
    """A sample that cannot be read. Never downgraded to a default."""


def read_sample(sample: Any) -> tuple[int, int]:
    """Read one VRAM sample, or refuse.

    Bools are rejected explicitly: `True` is an `int` in Python, and a byte
    count of `True` would classify as a non-zero residual.
    """

    if not isinstance(sample, dict):
        raise TelemetrySchemaError("a VRAM sample must be a mapping")
    missing = [name for name in VRAM_SAMPLE_FIELDS if name not in sample]
    if missing:
        legacy = [name for name in REJECTED_FIELD_SPELLINGS if name in sample]
        detail = f"missing {missing}"
        if legacy:
            detail += f"; found legacy spelling {legacy}"
        raise TelemetrySchemaError(detail)
    values = []
    for name in VRAM_SAMPLE_FIELDS:
        value = sample[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TelemetrySchemaError(f"{name} must be an int, got {type(value).__name__}")
        if value < 0:
            raise TelemetrySchemaError(f"{name} must not be negative")
        values.append(value)
    return values[0], values[1]


def ownership_is_clean(ownership: Any) -> bool:
    """Every ownership fact must be present and true. Absence is not cleanliness."""

    if not isinstance(ownership, dict):
        return False
    required = (
        "all_owned_weakrefs_dead",
        "registry_at_pre_load_count",
        "shared_sd_model_no_model",
        "model_data_sd_model_no_model",
    )
    return all(ownership.get(name) is True for name in required)


def classify(sample: Any, ownership: Any) -> dict[str, Any]:
    """Classify one final CUDA state.

    Ownership takes priority over the byte count: a clean-looking figure with an
    inconsistent owner graph is not a clean teardown, and reporting it by bytes
    alone is how the first live run's 264 MB looked like a completed smoke.
    """

    try:
        allocated, reserved = read_sample(sample)
    except TelemetrySchemaError as exc:
        return {
            "classification": TELEMETRY_SCHEMA_INVALID,
            "reason": str(exc),
            "expected_fields": list(VRAM_SAMPLE_FIELDS),
            "classified": False,
        }

    clean = ownership_is_clean(ownership)
    result: dict[str, Any] = {
        "allocated_bytes": allocated,
        "reserved_bytes": reserved,
        "ownership_clean": clean,
        "known_residual_allocated": KNOWN_RESIDUAL_ALLOCATED,
        "known_residual_reserved": KNOWN_RESIDUAL_RESERVED,
        "allocated_delta_vs_known": allocated - KNOWN_RESIDUAL_ALLOCATED,
        "reserved_delta_vs_known": reserved - KNOWN_RESIDUAL_RESERVED,
        "classified": True,
    }

    if not clean:
        result["classification"] = OWNERSHIP_STATE_INCONSISTENT
        result["reason"] = (
            "ownership facts are missing or false; a byte count may not be "
            "classified while the owner graph is inconsistent"
        )
        return result

    if allocated == 0 and reserved == 0:
        result["classification"] = ABSOLUTE_ZERO
    elif (
        allocated == KNOWN_RESIDUAL_ALLOCATED and reserved == KNOWN_RESIDUAL_RESERVED
    ):
        result["classification"] = KNOWN_GENERATION_RESIDUAL_MATCH
    elif allocated > KNOWN_RESIDUAL_ALLOCATED or reserved > KNOWN_RESIDUAL_RESERVED:
        result["classification"] = NEW_OR_GREATER_RESIDUAL
    else:
        result["classification"] = LOWER_BUT_NONZERO_RESIDUAL
    return result


# ------------------------------------------------------------ stage census

S0 = "S0_cuda_baseline_before_load"
S1 = "S1_model_ready"
S2 = "S2_prompt_setup_complete"
S3 = "S3_conditioning_complete"
S4 = "S4_initial_noise_created"
S5 = "S5_denoiser_entered"
S6 = "S6_first_sampler_step_completed"
S7 = "S7_all_sampler_steps_completed"
S8 = "S8_decode_complete"
S9 = "S9_publication_complete"
S10 = "S10_generation_result_objects_released"
S11 = "S11_processing_and_shared_caches_released"
S12 = "S12_port_session_model_references_released"
S13 = "S13_bounded_gc_complete"
S14 = "S14_terminal_cache_clear_complete"
S15 = "S15_post_cleanup_retrieval_complete"

STAGES = (S0, S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14, S15)


class StageSnapshotRecorder:
    """Ordered CUDA snapshots across the generation lifecycle.

    Inert by default: with no sampler injected it records structure and marks
    every byte field unavailable, so it can be constructed, imported and tested
    with no device present. A live run injects
    `forge_headless.load_telemetry.sample_current` and its peak counterpart.

    A stage is recorded at most once. Re-recording is a defect, not a refresh,
    because two different byte figures for one stage cannot both be true.
    """

    def __init__(
        self,
        *,
        sampler: Callable[[], Any] | None = None,
        peak_sampler: Callable[[], Any] | None = None,
        job_id: str = "",
    ) -> None:
        self._sampler = sampler
        self._peak_sampler = peak_sampler
        self._job_scalar = _opaque_job_scalar(job_id)
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._sequence = 0

    @property
    def active(self) -> bool:
        return self._sampler is not None

    def record(self, stage: str, *, ownership: dict[str, Any] | None = None) -> dict[str, Any]:
        if stage not in STAGES:
            raise KeyError(f"unknown stage: {stage}")
        if stage in self._snapshots:
            raise KeyError(f"stage already recorded: {stage}")
        self._sequence += 1
        snapshot: dict[str, Any] = {
            "stage": stage,
            "sequence": self._sequence,
            "job": self._job_scalar,
            "available": False,
        }
        current = self._sampler() if self._sampler is not None else None
        peak = self._peak_sampler() if self._peak_sampler is not None else None
        if current is not None:
            snapshot.update(_as_bytes(current, ALLOCATED_FIELD, RESERVED_FIELD))
            snapshot["available"] = True
        if peak is not None:
            snapshot.update(
                _as_bytes(peak, "max_allocated_bytes", "max_reserved_bytes")
            )
        if ownership:
            snapshot["ownership"] = {
                key: ownership[key] for key in sorted(ownership) if _is_scalar(ownership[key])
            }
        self._snapshots[stage] = snapshot
        return snapshot

    def unavailable(self, stage: str, reason: str) -> None:
        """Name a stage that could not be observed. Never a fabricated value."""

        if stage not in STAGES:
            raise KeyError(f"unknown stage: {stage}")
        if stage in self._snapshots:
            raise KeyError(f"stage already recorded: {stage}")
        self._sequence += 1
        self._snapshots[stage] = {
            "stage": stage,
            "sequence": self._sequence,
            "job": self._job_scalar,
            "available": False,
            "reason": str(reason),
        }

    def deltas(self) -> list[dict[str, Any]]:
        """Allocated change between consecutive *observed* stages."""

        observed = [
            self._snapshots[stage]
            for stage in STAGES
            if stage in self._snapshots and self._snapshots[stage].get("available")
        ]
        out: list[dict[str, Any]] = []
        for previous, current in zip(observed, observed[1:]):
            out.append({
                "from": previous["stage"],
                "to": current["stage"],
                "allocated_delta": current[ALLOCATED_FIELD] - previous[ALLOCATED_FIELD],
                "reserved_delta": current[RESERVED_FIELD] - previous[RESERVED_FIELD],
            })
        return out

    def first_retaining_stage(self, *, baseline_stage: str = S0) -> str | None:
        """The first stage whose allocated bytes exceed the baseline and stay up.

        Answers "where do the retained bytes first appear", which is the whole
        point of the census. Returns None when nothing rises above the baseline.
        """

        baseline = self._snapshots.get(baseline_stage)
        if not baseline or not baseline.get("available"):
            return None
        floor = baseline[ALLOCATED_FIELD]
        for stage in STAGES:
            snapshot = self._snapshots.get(stage)
            if not snapshot or not snapshot.get("available") or stage == baseline_stage:
                continue
            if snapshot[ALLOCATED_FIELD] > floor:
                return stage
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [self._snapshots[s] for s in STAGES if s in self._snapshots],
            "recorded": [s for s in STAGES if s in self._snapshots],
            "missing": [s for s in STAGES if s not in self._snapshots],
            "observed": [
                s for s in STAGES
                if s in self._snapshots and self._snapshots[s].get("available")
            ],
            "deltas": self.deltas(),
            "first_retaining_stage": self.first_retaining_stage(),
            "active": self.active,
            "sequence_is_monotonic": self._sequence == len(self._snapshots),
        }


def _as_bytes(sample: Any, allocated_key: str, reserved_key: str) -> dict[str, int]:
    payload = sample.to_dict() if hasattr(sample, "to_dict") else sample
    allocated, reserved = read_sample(payload)
    return {allocated_key: allocated, reserved_key: reserved}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (bool, int, float, str)) or value is None


def _opaque_job_scalar(job_id: str) -> str:
    """A stable scalar for a job. Never the job id, never a path."""

    import hashlib

    if not job_id:
        return ""
    return hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------- first-step truth

#: Where the zero-based index and its one-based translation are written.
FORGE_STEP_SOURCE = "modules/sd_samplers_common.py:431-432"
FORGE_RESET_SOURCE = "modules/sd_samplers_common.py:435-440"


class SamplerStepRecorder:
    """The first completed sampler step, taken from the first real event.

    The rerun reported `first_completed_sampler_step: 11` because the tracked
    port read `progress.snapshot().step` **once, after** `process_images_inner`
    returned. That is the last zero-based index, not the first step.

    Two signals are observed here, and they must agree:

    * `preview_step`, Neo's own one-based counter, written on the line after the
      zero-based one. Its first value >= 1 is the authoritative first completed
      step, so no arithmetic of ours decides it.
    * `sampling_step`, the zero-based index, kept as a cross-check. A reading of
      `0` is ambiguous -- `launch_sampling` writes it before the loop -- which is
      exactly why it is not the authority.
    """

    def __init__(self, *, on_step: Callable[[int], None] | None = None) -> None:
        #: Notified with each one-based completed step, as it happens. This is
        #: what replaces the single post-hoc read the rerun reported from.
        self._on_step = on_step
        self.denoiser_entered = False
        self.first_step_observed = False
        self.first_completed_step_index: int | None = None
        self.first_completed_step_total: int | None = None
        self.final_completed_steps: int | None = None
        self.final_total_steps: int | None = None
        self.zero_based_first: int | None = None
        self.zero_based_last: int | None = None
        self.one_based_events = 0
        self.zero_based_events = 0
        self.disagreements: list[dict[str, int]] = []
        self._total: int | None = None

    # -- inputs ------------------------------------------------------------

    def enter_denoiser(self, total_steps: int | None = None) -> None:
        self.denoiser_entered = True
        if total_steps:
            self.set_total(int(total_steps))

    def set_total(self, total_steps: int) -> None:
        self._total = int(total_steps)
        self.final_total_steps = int(total_steps)

    def observe_one_based(self, value: int) -> None:
        """A `preview_step` write. Values below 1 are the pre-loop reset."""

        step = int(value)
        if step < 1:
            return
        self.one_based_events += 1
        if not self.first_step_observed:
            self.first_step_observed = True
            self.first_completed_step_index = step
            self.first_completed_step_total = self._total
        if self.final_completed_steps is None or step > self.final_completed_steps:
            self.final_completed_steps = step
        if self._on_step is not None:
            try:
                self._on_step(step)
            except Exception:  # noqa: BLE001 - a reporting sink must not break sampling
                pass

    def observe_zero_based(self, value: int) -> None:
        """A `sampling_step` write, kept only as a cross-check."""

        step = int(value)
        self.zero_based_events += 1
        if self.zero_based_first is None and step >= 0:
            self.zero_based_first = step
        if self.zero_based_last is None or step > self.zero_based_last:
            self.zero_based_last = step

    def note_disagreement(self, one_based: int, zero_based: int) -> None:
        self.disagreements.append(
            {"one_based": int(one_based), "zero_based": int(zero_based)}
        )

    # -- reporting ---------------------------------------------------------

    @property
    def consistent(self) -> bool:
        """One-based must be exactly one more than zero-based, and end on total."""

        if not self.first_step_observed:
            return False
        if self.disagreements:
            return False
        if self.zero_based_last is None or self.final_completed_steps is None:
            return False
        if self.final_completed_steps != self.zero_based_last + 1:
            return False
        if self.final_total_steps is not None:
            return self.final_completed_steps == self.final_total_steps
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "denoiser_entered": self.denoiser_entered,
            "first_step_observed": self.first_step_observed,
            "first_completed_step_index": self.first_completed_step_index,
            "first_completed_step_total": self.first_completed_step_total,
            "final_completed_steps": self.final_completed_steps,
            "final_total_steps": self.final_total_steps,
            "zero_based_first_index": self.zero_based_first,
            "zero_based_last_index": self.zero_based_last,
            "one_based_events": self.one_based_events,
            "zero_based_events": self.zero_based_events,
            "consistent": self.consistent,
            "disagreements": list(self.disagreements),
            "index_base": "one-based, public",
            "translation_source": FORGE_STEP_SOURCE,
            "reset_source": FORGE_RESET_SOURCE,
            "derived_from_final_counter": False,
        }


def install_step_observers(bridge: Any, progress: Any, recorder: SamplerStepRecorder):
    """Watch both step signals for the duration of one generation.

    `preview_step` lands in the bridge's private `_values` dict, so the dict is
    swapped for a notifying subclass on that **instance** only -- no class is
    patched and no product function is wrapped. `report_step` is shadowed on the
    progress instance to catch the zero-based writes.

    Returns a restore callable. It is idempotent and never raises.
    """

    restored = {"done": False}
    original_values = None
    original_report = None

    class _WatchedValues(dict):
        def __setitem__(self, key: str, value: Any) -> None:
            super().__setitem__(key, value)
            if key == "preview_step":
                try:
                    recorder.observe_one_based(int(value))
                except (TypeError, ValueError):
                    pass

    if bridge is not None:
        try:
            original_values = object.__getattribute__(bridge, "_values")
            object.__setattr__(bridge, "_values", _WatchedValues(original_values))
        except Exception:  # noqa: BLE001 - a bridge we cannot watch is reported, not fatal
            original_values = None

    if progress is not None and hasattr(progress, "report_step"):
        original_report = progress.report_step

        def _report_step(step: int) -> None:
            try:
                recorder.observe_zero_based(int(step))
            except (TypeError, ValueError):
                pass
            original_report(step)

        try:
            progress.report_step = _report_step  # type: ignore[method-assign]
        except Exception:  # noqa: BLE001
            original_report = None

    def restore() -> dict[str, Any]:
        if restored["done"]:
            return {"restored": True, "already": True}
        restored["done"] = True
        if original_values is not None:
            try:
                object.__setattr__(bridge, "_values", dict(
                    object.__getattribute__(bridge, "_values")
                ))
            except Exception:  # noqa: BLE001
                pass
        if original_report is not None:
            try:
                del progress.report_step
            except Exception:  # noqa: BLE001
                pass
        return {
            "restored": True,
            "watched_bridge_values": original_values is not None,
            "watched_report_step": original_report is not None,
        }

    return restore


# ------------------------------------------------------- ownership sampling


#: Everything that must have been released before owned weakrefs mean anything.
REQUIRED_RELEASES = (
    "port_engine_cleared",
    "session_closed",
    "gateway_released",
    "diagnostic_locals_released",
)

NOT_READY = "OWNERSHIP_NOT_SAMPLEABLE"


class OwnershipSampler:
    """Read owned weakrefs only after every named release has happened.

    The three-cycle live run reported one surviving `engine` weakref while all
    seven of its components were dead. The likely holder was the diagnostic
    driver's own frame local, still bound when the report was taken -- but the
    report was taken too early to tell, so the run could not prove it.

    This makes the ordering a precondition instead of a convention: `sample()`
    refuses until every release in `REQUIRED_RELEASES` is marked, so a report
    cannot be produced from a moment when a diagnostic local was still alive.
    A refusal is `OWNERSHIP_NOT_SAMPLEABLE`, never a clean-looking result.
    """

    def __init__(self, references: dict[str, Any] | None = None) -> None:
        self._references: dict[str, Any] = dict(references or {})
        self._marked: set[str] = set()

    def track(self, name: str, obj: Any) -> None:
        """Weakly track one owned object. Non-weakrefable objects are named."""

        import weakref

        if obj is None:
            self._references[name] = "absent"
            return
        try:
            self._references[name] = weakref.ref(obj)
        except TypeError:
            self._references[name] = "not_weakrefable"

    def mark(self, release: str) -> None:
        if release not in REQUIRED_RELEASES:
            raise KeyError(f"unknown release: {release}")
        self._marked.add(release)

    @property
    def outstanding(self) -> list[str]:
        return [name for name in REQUIRED_RELEASES if name not in self._marked]

    @property
    def ready(self) -> bool:
        return not self.outstanding

    def sample(self) -> dict[str, Any]:
        """Collect, then read. Refuses while any release is outstanding."""

        if not self.ready:
            return {
                "sampled": False,
                "status": NOT_READY,
                "outstanding": self.outstanding,
                "reason": (
                    "owned weakrefs were read before every release was marked; a "
                    "reference alive at this point may be the diagnostic's own"
                ),
            }
        import gc

        gc.collect()
        alive, dead, skipped = [], [], []
        for name, reference in sorted(self._references.items()):
            if not callable(reference):
                skipped.append({"name": name, "reason": str(reference)})
            elif reference() is None:
                dead.append(name)
            else:
                alive.append(name)
        return {
            "sampled": True,
            "status": "OK",
            "dead": dead,
            "alive": alive,
            "skipped": skipped,
            "all_owned_weakrefs_dead": not alive,
            "observed": len(dead) + len(alive),
            "releases_marked": sorted(self._marked),
        }

    def ownership_facts(self, **facts: Any) -> dict[str, Any]:
        """Build the classifier's ownership dict from a sample plus given facts.

        `all_owned_weakrefs_dead` is taken from the sample, never from the
        caller: a caller that could assert it would not need to sample.
        """

        sampled = self.sample()
        out = dict(facts)
        out["all_owned_weakrefs_dead"] = bool(
            sampled.get("sampled") and sampled.get("all_owned_weakrefs_dead")
        )
        out["_sample"] = sampled
        return out


__all__ = (
    "ABSOLUTE_ZERO",
    "ALLOCATED_FIELD",
    "CLASSIFICATIONS",
    "KNOWN_GENERATION_RESIDUAL_MATCH",
    "KNOWN_RESIDUAL_ALLOCATED",
    "KNOWN_RESIDUAL_RESERVED",
    "LOWER_BUT_NONZERO_RESIDUAL",
    "NEW_OR_GREATER_RESIDUAL",
    "NOT_READY",
    "OWNERSHIP_STATE_INCONSISTENT",
    "OwnershipSampler",
    "REQUIRED_RELEASES",
    "RESERVED_FIELD",
    "STAGES",
    "SamplerStepRecorder",
    "StageSnapshotRecorder",
    "TELEMETRY_SCHEMA_INVALID",
    "TelemetrySchemaError",
    "classify",
    "install_step_observers",
    "ownership_is_clean",
    "read_sample",
)
