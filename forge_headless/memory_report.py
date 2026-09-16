"""Memory telemetry serialization and the one classifier that judges it.

The final live run lost every CUDA number to a one-line extraction mistake:
the harness read ``.allocated_bytes`` off :class:`VramSample` objects whose
attributes are ``.allocated`` / ``.reserved`` -- the ``_bytes`` names exist
only as ``to_dict()`` keys. The values died with the process, and two of the
run's acceptance thresholds became unverifiable.

This module is the structural answer, in two parts:

* **One schema adapter.** Everything that serializes a sample calls
  :func:`serialize_vram_sample`; nothing else touches the field names again.
  The adapter fails closed: ``None`` is an explicit ``unavailable``, a missing
  attribute or a non-integer is an explicit ``invalid`` -- never a silent
  ``None`` that a report writes and a reader later mistakes for zero.

* **One classifier.** Every memory verdict -- warm growth, the post-unload
  ceilings, ownership, registry, global-state restoration -- goes through
  :func:`classify_memory`, and a verdict can never be ``MEMORY_ACCEPTED``
  while any of its inputs is missing or invalid. "We did not measure" and
  "we measured and it passed" are different sentences, and the live run is
  what happens when a report cannot tell them apart.

Import-safe: no torch, no CUDA, nothing heavy. Scalars in, scalars out.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Stable serialized statuses.
SAMPLE_OK = "ok"
SAMPLE_UNAVAILABLE = "unavailable"
SAMPLE_INVALID = "invalid"

#: Stable classifier outcomes.
MEMORY_ACCEPTED = "MEMORY_ACCEPTED"
STAGED_RESIDENCY = "STAGED_RESIDENCY"
WARM_RETENTION_SUSPECTED = "WARM_RETENTION_SUSPECTED"
MEMORY_TELEMETRY_UNAVAILABLE = "MEMORY_TELEMETRY_UNAVAILABLE"
MEMORY_THRESHOLD_EXCEEDED = "MEMORY_THRESHOLD_EXCEEDED"
OWNERSHIP_STATE_INCONSISTENT = "OWNERSHIP_STATE_INCONSISTENT"

#: The outcomes an owner may run on. `STAGED_RESIDENCY` is acceptable: it is
#: growth that Forge's own residency schedule explains, with every ownership
#: and threshold gate passing.
ACCEPTABLE_OUTCOMES = (MEMORY_ACCEPTED, STAGED_RESIDENCY)

#: The ownership facts the classifier requires. Every one must be present and
#: True for acceptance; a missing fact is inconsistency, not a pass.
OWNERSHIP_FACTS = (
    "owned_weakrefs_dead",
    "registry_restored",
    "global_state_restored",
)

#: Residency facts consulted only when warm memory GREW. Each is a retention
#: signal, and any one of them turns growth into suspicion.
RESIDENCY_FACTS = (
    "per_job_references_dead",
    "engine_count",
    "session_count",
    "registry_count_stable",
)


def _retention_signals(
    residency: Mapping[str, Any],
    post_release_series: list[int],
    tolerance: int,
) -> list[str]:
    """Which retention signals fired. Empty means growth looks like staging.

    Staged residency is BOUNDED: Forge moves more of the model onto the device
    until it is resident, then stops. Retention is not bounded. The signals
    below are the ones that do not need a third measurement to read; the
    deceleration check is the one that does, and it stays quiet with fewer.
    """

    signals: list[str] = []
    if residency.get("per_job_references_dead") is not True:
        signals.append("per_job_references_survived")
    for name in ("engine_count", "session_count"):
        value = residency.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            signals.append(f"{name}_unknown")
        elif value > 1:
            signals.append(name)
    if residency.get("registry_count_stable") is not True:
        signals.append("registry_count_grew")

    deltas = [
        post_release_series[index + 1] - post_release_series[index]
        for index in range(len(post_release_series) - 1)
    ]
    # Three or more post-release points whose growth is not decelerating are
    # not a model becoming resident. Two points cannot show a plateau either
    # way, so they do not fire this signal -- and the verdict detail says so.
    if len(deltas) >= 2 and deltas[-1] > tolerance and deltas[-1] >= deltas[-2]:
        signals.append("growth_not_decelerating")
    return signals


def serialize_vram_sample(sample: Any) -> dict[str, Any]:
    """Serialize one VramSample-shaped object. The ONLY field-name seam.

    Reads ``sample.allocated`` and ``sample.reserved`` -- the attributes --
    and emits ``allocated_bytes`` / ``reserved_bytes`` -- the wire names.
    The live run's exact mistake (reading the wire names as attributes) is
    pinned by test as producing ``invalid``, never a silent ``None``.
    """

    if sample is None:
        return {"status": SAMPLE_UNAVAILABLE}

    missing = [
        name for name in ("allocated", "reserved") if not hasattr(sample, name)
    ]
    if missing:
        return {
            "status": SAMPLE_INVALID,
            "reason": f"missing field: {missing[0]}",
        }

    values: dict[str, int] = {}
    for name in ("allocated", "reserved"):
        value = getattr(sample, name)
        # bool is an int subclass and would silently serialize as 0/1.
        if isinstance(value, bool) or not isinstance(value, int):
            return {
                "status": SAMPLE_INVALID,
                "reason": f"non-integer field: {name}",
            }
        if value < 0:
            return {
                "status": SAMPLE_INVALID,
                "reason": f"negative field: {name}",
            }
        values[name] = value

    return {
        "status": SAMPLE_OK,
        "allocated_bytes": values["allocated"],
        "reserved_bytes": values["reserved"],
    }


def _usable(serialized: Any) -> bool:
    return (
        isinstance(serialized, Mapping)
        and serialized.get("status") == SAMPLE_OK
        and isinstance(serialized.get("allocated_bytes"), int)
        and isinstance(serialized.get("reserved_bytes"), int)
    )


def classify_memory(
    *,
    job1_post_release: Mapping[str, Any] | None,
    job2_post_release: Mapping[str, Any] | None,
    post_unload: Mapping[str, Any] | None,
    warm_tolerance_bytes: int,
    allocated_ceiling_bytes: int,
    reserved_ceiling_bytes: int,
    ownership: Mapping[str, Any],
    residency: Mapping[str, Any] | None = None,
    post_release_series: "list[int] | None" = None,
    peak_allocated_bytes: int | None = None,
    peak_ceiling_bytes: int | None = None,
    out_of_memory: bool = False,
) -> dict[str, Any]:
    """One verdict for the whole memory question.

    Inputs are SERIALIZED samples (from :func:`serialize_vram_sample`), so a
    caller cannot reach this with raw objects and re-make the field mistake.

    Order of judgement, strictest first:

    1. ownership facts -- a clean number over an unclean teardown is not a
       pass, so inconsistency wins over everything;
    2. telemetry usability -- any missing/invalid sample is UNAVAILABLE, and
       UNAVAILABLE can never be ACCEPTED;
    3. hard ceilings -- post-unload allocated and reserved, the peak ceiling,
       and OOM. A breach here is a failure whatever the growth looked like;
    4. growth shape -- flat is ACCEPTED, growth with a retention signal (or
       with no residency facts at all) is SUSPECTED, and growth that every
       signal says is Forge becoming resident is STAGED_RESIDENCY.

    Step 4 is the change this milestone makes. The first-to-second-job delta
    used to fail the run by itself, which cannot distinguish Forge staging
    weight residency across generations from a session retaining generation
    state -- the final owner trial produced byte-identical images to a
    previous run, released everything at unload, and still failed on a delta
    of +822 MiB. The delta is still computed and still reported; it is now one
    input rather than the verdict.
    """

    detail: dict[str, Any] = {
        "warm_tolerance_bytes": warm_tolerance_bytes,
        "allocated_ceiling_bytes": allocated_ceiling_bytes,
        "reserved_ceiling_bytes": reserved_ceiling_bytes,
    }

    missing_facts = [
        name for name in OWNERSHIP_FACTS if ownership.get(name) is not True
    ]
    detail["ownership_facts_failed"] = missing_facts
    if missing_facts:
        return {"outcome": OWNERSHIP_STATE_INCONSISTENT, **detail}

    samples = {
        "job1_post_release": job1_post_release,
        "job2_post_release": job2_post_release,
        "post_unload": post_unload,
    }
    unusable = [name for name, s in samples.items() if not _usable(s)]
    detail["telemetry_unusable"] = unusable
    if unusable:
        return {"outcome": MEMORY_TELEMETRY_UNAVAILABLE, **detail}

    warm_growth = (
        job2_post_release["allocated_bytes"]  # type: ignore[index]
        - job1_post_release["allocated_bytes"]  # type: ignore[index]
    )
    detail["warm_growth_bytes"] = warm_growth
    detail["post_unload_allocated_bytes"] = post_unload["allocated_bytes"]  # type: ignore[index]
    detail["post_unload_reserved_bytes"] = post_unload["reserved_bytes"]  # type: ignore[index]

    # -- 3. hard ceilings ------------------------------------------------
    exceeded: list[str] = []
    if post_unload["allocated_bytes"] > allocated_ceiling_bytes:  # type: ignore[index]
        exceeded.append("post_unload_allocated")
    if post_unload["reserved_bytes"] > reserved_ceiling_bytes:  # type: ignore[index]
        exceeded.append("post_unload_reserved")
    if (
        peak_ceiling_bytes is not None
        and isinstance(peak_allocated_bytes, int)
        and not isinstance(peak_allocated_bytes, bool)
        and peak_allocated_bytes > peak_ceiling_bytes
    ):
        exceeded.append("peak_allocated")
    if out_of_memory:
        exceeded.append("out_of_memory")
    detail["thresholds_exceeded"] = exceeded
    if exceeded:
        return {"outcome": MEMORY_THRESHOLD_EXCEEDED, **detail}

    # -- 4. growth shape --------------------------------------------------
    series = [int(value) for value in (post_release_series or [
        job1_post_release["allocated_bytes"],  # type: ignore[index]
        job2_post_release["allocated_bytes"],  # type: ignore[index]
    ])]
    detail["post_release_series"] = series
    detail["post_release_points"] = len(series)

    if warm_growth <= warm_tolerance_bytes:
        detail["growth_shape"] = "flat"
        return {"outcome": MEMORY_ACCEPTED, **detail}

    detail["growth_shape"] = "grew"
    if residency is None:
        # Growth nobody can explain is retention, not an accepted default.
        detail["retention_signals"] = ["residency_facts_absent"]
        return {"outcome": WARM_RETENTION_SUSPECTED, **detail}

    signals = _retention_signals(residency, series, warm_tolerance_bytes)
    detail["retention_signals"] = signals
    if signals:
        return {"outcome": WARM_RETENTION_SUSPECTED, **detail}

    # Two points cannot show a plateau. Recorded rather than implied, so a
    # reader can see what this verdict did and did not observe.
    detail["plateau_observable"] = len(series) >= 3
    return {"outcome": STAGED_RESIDENCY, **detail}


__all__ = (
    "ACCEPTABLE_OUTCOMES",
    "MEMORY_ACCEPTED",
    "MEMORY_TELEMETRY_UNAVAILABLE",
    "MEMORY_THRESHOLD_EXCEEDED",
    "OWNERSHIP_FACTS",
    "OWNERSHIP_STATE_INCONSISTENT",
    "RESIDENCY_FACTS",
    "STAGED_RESIDENCY",
    "WARM_RETENTION_SUSPECTED",
    "SAMPLE_INVALID",
    "SAMPLE_OK",
    "SAMPLE_UNAVAILABLE",
    "classify_memory",
    "serialize_vram_sample",
)
