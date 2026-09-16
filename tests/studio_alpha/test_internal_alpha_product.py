"""Contracts for Internal Alpha Phase 1.

Four areas, one suite:

* **Telemetry schema and classifier** -- the final live run's lost-numbers
  regression, pinned: real ``VramSample`` objects serialize through one
  adapter; missing/invalid/None telemetry fails closed; one classifier owns
  every memory verdict and can never accept unmeasured memory.

* **argv object identity** -- confirmed gap 2, with failure injections before
  and after the first ``modules.*`` import.

* **Job coordinator and canonical cancellation** -- confirmed gap 3: one
  public id from before backend submission to terminal state, and the §7
  race rows that are deterministic at the coordinator seam.

* **Launcher contract and the frontend/product rehearsal** -- configuration
  validation rows in-process; the complete §12 flow in a subprocess probe
  over the real loopback server, with facts asserted individually.

SCOPE in-process: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. The probe
subprocess binds one loopback server; the NeoStub world means no torch, no
real modules, no payload, no CUDA anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.load_telemetry import VramSample  # noqa: E402
from forge_headless.memory_report import (  # noqa: E402
    ACCEPTABLE_OUTCOMES,
    MEMORY_ACCEPTED,
    MEMORY_TELEMETRY_UNAVAILABLE,
    MEMORY_THRESHOLD_EXCEEDED,
    OWNERSHIP_STATE_INCONSISTENT,
    SAMPLE_INVALID,
    SAMPLE_OK,
    SAMPLE_UNAVAILABLE,
    STAGED_RESIDENCY,
    WARM_RETENTION_SUSPECTED,
    classify_memory,
    serialize_vram_sample,
)
from forge_headless.live_bindings import (  # noqa: E402
    LoadConfiguration,
    StudioStartupGlobals,
)
from forge_studio.jobs import JobCoordinator  # noqa: E402
from forge_studio.launch import (  # noqa: E402
    LaunchConfigurationError,
    load_config,
)

# 54 -> 55 in P0.3e. Three launch-config refusals became tolerations, because
# `profiles`, `selected_profile_id` and `autoload` no longer decide anything
# -- refusing to start over a stale key that does nothing would strand an
# owner behind their own config file. One test was added for the whole legacy
# document at once, which is the shape actually on disk.
EXPECTED_INTERNAL_ALPHA_TESTS = 55

_OWNERSHIP_CLEAN = {
    "owned_weakrefs_dead": True,
    "registry_restored": True,
    "global_state_restored": True,
}


def _ok(allocated: int, reserved: int) -> dict:
    return serialize_vram_sample(VramSample(allocated=allocated,
                                            reserved=reserved))


def _classify(job1=None, job2=None, unload=None, ownership=None, **extra):
    return classify_memory(
        job1_post_release=job1 if job1 is not None else _ok(1000, 2000),
        job2_post_release=job2 if job2 is not None else _ok(1000, 2000),
        post_unload=unload if unload is not None else _ok(1_000_000, 2_000_000),
        warm_tolerance_bytes=1_048_576,
        allocated_ceiling_bytes=11_206_656,
        reserved_ceiling_bytes=26_214_400,
        ownership=ownership if ownership is not None else _OWNERSHIP_CLEAN,
        **extra,
    )


# ------------------------------------------------------------- telemetry


class TelemetrySchemaTests(unittest.TestCase):
    def test_a_real_vram_sample_serializes(self) -> None:
        serialized = serialize_vram_sample(VramSample(allocated=7, reserved=9))
        self.assertEqual(
            {"status": SAMPLE_OK, "allocated_bytes": 7, "reserved_bytes": 9},
            serialized,
        )

    def test_the_exact_live_run_regression_is_pinned(self) -> None:
        """An object carrying only the WIRE names must fail closed.

        This is precisely what the final live run's harness read: it asked
        the sample for `.allocated_bytes`, got None everywhere, and the
        numbers died with the process. The adapter refuses that shape.
        """

        class WireShaped:
            allocated_bytes = 5
            reserved_bytes = 6

        serialized = serialize_vram_sample(WireShaped())
        self.assertEqual(SAMPLE_INVALID, serialized["status"])
        self.assertIn("allocated", serialized["reason"])

    def test_none_is_an_explicit_unavailable(self) -> None:
        self.assertEqual(
            {"status": SAMPLE_UNAVAILABLE}, serialize_vram_sample(None)
        )

    def test_negative_and_non_integer_fail_closed(self) -> None:
        self.assertEqual(
            SAMPLE_INVALID,
            serialize_vram_sample(VramSample(allocated=-1, reserved=0))["status"],
        )

        class Floaty:
            allocated = 1.5
            reserved = 2

        self.assertEqual(SAMPLE_INVALID,
                         serialize_vram_sample(Floaty())["status"])

        class Booly:
            allocated = True
            reserved = 2

        self.assertEqual(SAMPLE_INVALID,
                         serialize_vram_sample(Booly())["status"])

    def test_json_round_trip_preserves_exact_integers(self) -> None:
        big = 15_032_385_536  # > 2**33; a float round trip would corrupt it
        serialized = serialize_vram_sample(
            VramSample(allocated=big, reserved=big + 1)
        )
        restored = json.loads(json.dumps(serialized))
        self.assertEqual(big, restored["allocated_bytes"])
        self.assertEqual(big + 1, restored["reserved_bytes"])

    def test_warm_delta_uses_serialized_byte_fields(self) -> None:
        verdict = _classify(job1=_ok(1000, 0), job2=_ok(1000 + 512, 0))
        self.assertEqual(512, verdict["warm_growth_bytes"])
        self.assertEqual(MEMORY_ACCEPTED, verdict["outcome"])

    def test_post_unload_thresholds_use_serialized_byte_fields(self) -> None:
        verdict = _classify(unload=_ok(11_206_657, 0))
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])
        self.assertIn("post_unload_allocated", verdict["thresholds_exceeded"])


class MemoryClassifierTests(unittest.TestCase):
    def test_accepts_only_with_everything_clean(self) -> None:
        self.assertEqual(MEMORY_ACCEPTED, _classify()["outcome"])

    def test_missing_telemetry_can_never_pass(self) -> None:
        verdict = _classify(job2=serialize_vram_sample(None))
        self.assertEqual(MEMORY_TELEMETRY_UNAVAILABLE, verdict["outcome"])
        self.assertIn("job2_post_release", verdict["telemetry_unusable"])

    def test_invalid_telemetry_can_never_pass(self) -> None:
        class WireShaped:
            allocated_bytes = 5
            reserved_bytes = 6

        verdict = _classify(unload=serialize_vram_sample(WireShaped()))
        self.assertEqual(MEMORY_TELEMETRY_UNAVAILABLE, verdict["outcome"])

    def test_unexplained_warm_growth_fails_closed(self) -> None:
        """REPLACES `test_warm_growth_beyond_tolerance_is_exceeded`.

        The old assertion encoded the criterion the final acceptance contract
        invalidated: a first-to-second-job delta above 1 MiB was an automatic
        MEMORY_THRESHOLD_EXCEEDED. It cannot distinguish Forge staging weight
        residency across generations from a session retaining generation
        state, and the final owner trial failed on it while producing
        byte-identical images to an earlier passing run and releasing
        everything at unload.

        What must still hold is that growth is never accepted by DEFAULT.
        With no residency facts to explain it, growth fails closed -- as
        suspected retention, which is what unexplained growth is.
        """

        verdict = _classify(job1=_ok(0, 0), job2=_ok(1_048_577, 0))
        self.assertEqual(WARM_RETENTION_SUSPECTED, verdict["outcome"])
        self.assertEqual(["residency_facts_absent"],
                         verdict["retention_signals"])
        self.assertEqual(1_048_577, verdict["warm_growth_bytes"])
        # The delta is still measured and still reported; it is no longer the
        # verdict by itself.
        self.assertEqual([], verdict["thresholds_exceeded"])

    def test_the_delta_is_diagnostic_not_a_verdict(self) -> None:
        """The same delta, now explained, is acceptable for owner use."""

        verdict = _classify(
            job1=_ok(4_198_994_432, 0), job2=_ok(5_060_981_760, 0),
            residency={"per_job_references_dead": True, "engine_count": 1,
                       "session_count": 1, "registry_count_stable": True},
        )
        self.assertEqual(STAGED_RESIDENCY, verdict["outcome"])
        self.assertIn(verdict["outcome"], ACCEPTABLE_OUTCOMES)
        self.assertEqual(861_987_328, verdict["warm_growth_bytes"])
        self.assertEqual([], verdict["retention_signals"])
        # Two post-release points cannot show a plateau, and the verdict says
        # so rather than implying it was checked.
        self.assertFalse(verdict["plateau_observable"])

    def test_a_retention_signal_beats_a_staged_reading(self) -> None:
        verdict = _classify(
            job1=_ok(0, 0), job2=_ok(2_000_000_000, 0),
            residency={"per_job_references_dead": False, "engine_count": 1,
                       "session_count": 1, "registry_count_stable": True},
        )
        self.assertEqual(WARM_RETENTION_SUSPECTED, verdict["outcome"])
        self.assertIn("per_job_references_survived",
                      verdict["retention_signals"])

    def test_reserved_ceiling_is_enforced(self) -> None:
        verdict = _classify(unload=_ok(0, 26_214_401))
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])

    def test_ownership_inconsistency_beats_clean_numbers(self) -> None:
        verdict = _classify(ownership={
            "owned_weakrefs_dead": True,
            "registry_restored": False,
            "global_state_restored": True,
        })
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])
        self.assertIn("registry_restored", verdict["ownership_facts_failed"])

    def test_a_missing_ownership_fact_is_inconsistency(self) -> None:
        verdict = _classify(ownership={"owned_weakrefs_dead": True})
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])


# ------------------------------------------------------------ argv identity


class ArgvIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.addCleanup(self.neo.restore)
        self._argv = sys.argv
        self._path = list(sys.path)
        self.addCleanup(self._put_back)

    def _put_back(self) -> None:
        sys.argv = self._argv
        sys.path[:] = self._path

    def test_the_exact_object_and_values_come_back(self) -> None:
        original = ["prog", "--foreign", "value"]
        sys.argv = original
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        self.assertIsNot(original, sys.argv)
        report = owner.close()
        self.assertIs(original, sys.argv)
        self.assertEqual(("prog", "--foreign", "value"), tuple(sys.argv))
        self.assertTrue(report["argv_restored"])

    def test_the_original_object_is_never_mutated(self) -> None:
        original = ["prog", "--foreign"]
        snapshot = tuple(original)
        sys.argv = original
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        self.assertEqual(snapshot, tuple(original))
        owner.close()
        self.assertEqual(snapshot, tuple(original))

    def test_failure_before_any_modules_import_restores_exactly(self) -> None:
        original = ["prog", "--x"]
        sys.argv = original

        class FailsBeforeModules(StudioStartupGlobals):
            def _install_options(self) -> None:  # runs before modules.shared
                raise RuntimeError("before modules import")

        owner = FailsBeforeModules(repository_root=APP_ROOT)
        with self.assertRaises(RuntimeError):
            owner.install()
        self.assertIs(original, sys.argv)

    def test_failure_after_the_modules_import_restores_exactly(self) -> None:
        original = ["prog", "--y"]
        sys.argv = original
        # The compatibility step runs AFTER modules.shared is imported by the
        # options/state steps; breaking it injects the post-import failure.
        del sys.modules["modules.shared_total_tqdm"].TotalTQDM
        owner = StudioStartupGlobals(repository_root=APP_ROOT)
        with self.assertRaises(Exception):
            owner.install()
        self.assertIs(original, sys.argv)

    def test_idempotent_close_keeps_the_object(self) -> None:
        original = ["prog"]
        sys.argv = original
        owner = StudioStartupGlobals(repository_root=APP_ROOT).install()
        owner.close()
        owner.close()
        self.assertIs(original, sys.argv)

    def test_import_time_touches_nothing(self) -> None:
        source = (APP_ROOT / "forge_headless" / "live_bindings.py").read_text(
            encoding="utf-8"
        )
        head = source.split("class LoadConfiguration", 1)[0]
        self.assertNotIn("sys.argv =", head)


# ------------------------------------------------- coordinator and cancel


class _Wired:
    """The seam-suite arrangement: real composition, gated synthetic port."""

    def __init__(self) -> None:
        self.neo = B.NeoStub().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        from forge_studio.composition import build_standalone

        self.composition = build_standalone(
            backend_kind="headless",
            profiles=[B._profile("alpha")],
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=B.FakeAuthorization()
            ),
            result_root=Path(self.tmp.name),
        )
        self.lifecycle = self.composition.model_lifecycle

    def close(self) -> None:
        try:
            self.composition.shutdown()
        except BaseException:  # noqa: BLE001
            pass
        self.intercepts.__exit__(None, None, None)
        self.neo.restore()
        self.tmp.cleanup()


def _request(seed: int = 1):
    from forge_studio import GenerationRequest

    return GenerationRequest(
        model_id="alpha", positive_prompt="p", negative_prompt="",
        seed=seed, steps=12, cfg_scale=6.0, width=768, height=768,
    )


class CoordinatorRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wired = _Wired()
        self.addCleanup(self.wired.close)

    def _coordinator(self) -> JobCoordinator:
        return JobCoordinator(self.wired.composition.application)

    def test_submit_into_no_model_refuses_synchronously(self) -> None:
        coordinator = self._coordinator()
        with self.assertRaises(Exception) as caught:
            coordinator.submit(_request())
        self.assertEqual("MODEL_NOT_READY", caught.exception.error.code)

    def test_the_public_id_exists_before_backend_submission(self) -> None:
        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        submitted = coordinator.submit(_request())
        self.assertTrue(submitted["job_id"].startswith("studio-job-"))
        # Wait for the worker to finish; the id never changed.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if coordinator.describe(submitted["job_id"])["state"] == "completed":
                break
            time.sleep(0.02)
        record = coordinator.describe(submitted["job_id"])
        self.assertEqual("completed", record["state"])
        self.assertIsNotNone(record["backend_job_id"])
        self.assertNotEqual(record["backend_job_id"], submitted["job_id"])

    def test_cancel_before_queue_insertion_is_a_stable_refusal(self) -> None:
        coordinator = self._coordinator()
        with self.assertRaises(Exception) as caught:
            coordinator.cancel("studio-job-999999")
        self.assertEqual("JOB_NOT_FOUND", caught.exception.error.code)

    def test_cancel_during_promotion_is_refused_honestly(self) -> None:
        """The window between lease grant and backend identity.

        Deterministically constructed: a record that is QUEUED but no longer
        in the lifecycle queue and has no backend id yet is exactly what the
        promotion window looks like from the coordinator's seam.
        """

        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        with coordinator._lock:  # noqa: SLF001 - constructing the window
            coordinator._jobs["studio-job-000042"] = {
                "job_id": "studio-job-000042", "state": "queued",
                "backend_job_id": None, "error": None,
                "cancel_requested": False,
            }
            coordinator._order.append("studio-job-000042")
        with self.assertRaises(Exception) as caught:
            coordinator.cancel("studio-job-000042")
        self.assertEqual("JOB_CANCEL_TOO_LATE", caught.exception.error.code)

    def test_cancel_immediately_after_backend_submission(self) -> None:
        """A terminal record with a mapped backend id reports already-terminal
        rather than double-cancelling -- the blocking submit means the job has
        finished by the time the id is mapped."""

        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        submitted = coordinator.submit(_request())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if coordinator.describe(submitted["job_id"])["state"] == "completed":
                break
            time.sleep(0.02)
        response = coordinator.cancel(submitted["job_id"])
        self.assertTrue(response["already_terminal"])
        self.assertEqual("completed", response["state"])

    def test_duplicate_cancel_is_idempotent(self) -> None:
        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        with coordinator._lock:  # noqa: SLF001
            coordinator._jobs["studio-job-000077"] = {
                "job_id": "studio-job-000077", "state": "cancelled",
                "backend_job_id": None, "error": None,
                "cancel_requested": True,
            }
            coordinator._order.append("studio-job-000077")
        first = coordinator.cancel("studio-job-000077")
        second = coordinator.cancel("studio-job-000077")
        self.assertEqual(first, second)
        self.assertTrue(second["already_terminal"])

    def test_submit_after_close_is_refused(self) -> None:
        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        coordinator.close()
        with self.assertRaises(Exception) as caught:
            coordinator.submit(_request())
        self.assertEqual("COORDINATOR_CLOSED", caught.exception.error.code)

    def test_cancel_still_works_while_shutting_down(self) -> None:
        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        coordinator = self._coordinator()
        with coordinator._lock:  # noqa: SLF001
            coordinator._jobs["studio-job-000088"] = {
                "job_id": "studio-job-000088", "state": "cancelled",
                "backend_job_id": None, "error": None,
                "cancel_requested": False,
            }
            coordinator._order.append("studio-job-000088")
        coordinator.close()
        response = coordinator.cancel("studio-job-000088")
        self.assertTrue(response["already_terminal"])

    def test_no_duplicate_terminal_event(self) -> None:
        """A record cancelled while queued stays CANCELLED even after the
        worker's refusal lands -- the worker never downgrades a terminal."""

        self.wired.lifecycle.ensure_loaded(B._profile("alpha"))
        # Hold the session so a second submit queues.
        holder = self.wired.lifecycle._manager  # noqa: SLF001
        holder.acquire("studio-hold-1")
        coordinator = self._coordinator()
        submitted = coordinator.submit(_request())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if submitted["job_id"] in self.wired.lifecycle.queued_jobs():
                break
            time.sleep(0.02)
        response = coordinator.cancel(submitted["job_id"])
        self.assertTrue(response["cancelled_while_queued"])
        holder.release("studio-hold-1")
        time.sleep(0.3)
        self.assertEqual(
            "cancelled", coordinator.describe(submitted["job_id"])["state"]
        )


# -------------------------------------------------------------- launcher


class LaunchConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, payload: dict) -> Path:
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _valid(self) -> dict:
        return {
            "backend": "mock", "host": "127.0.0.1", "port": 0,
            "result_root": "Studio-Results",
        }

    def test_a_valid_config_loads(self) -> None:
        config = load_config(self._write(self._valid()))
        self.assertEqual("mock", config["backend"])
        self.assertEqual((), config["legacy_keys"])
        self.assertEqual({}, config["last_model_selection"])

    def test_missing_file_names_the_template(self) -> None:
        with self.assertRaises(LaunchConfigurationError) as caught:
            load_config(Path(self.tmp.name) / "absent.json")
        self.assertIn("template", str(caught.exception))

    def test_non_loopback_host_is_refused(self) -> None:
        payload = self._valid()
        payload["host"] = "0.0.0.0"
        with self.assertRaises(LaunchConfigurationError):
            load_config(self._write(payload))

    def test_a_legacy_autoload_flag_is_ignored_rather_than_refused(self) -> None:
        """`autoload: true` used to be a fatal misconfiguration, because
        loading was an explicit owner action it would have bypassed. There is
        no explicit load to bypass now, so the flag has nothing to mean --
        and refusing to start over a key that no longer does anything would
        strand an owner behind their own old config file."""

        payload = self._valid()
        payload["autoload"] = True
        config = load_config(self._write(payload))
        self.assertIn("autoload", config["legacy_keys"])
        self.assertNotIn("autoload", config)

    def test_result_root_must_stay_in_the_workspace(self) -> None:
        payload = self._valid()
        payload["result_root"] = "C:/Windows/Temp/escape"
        with self.assertRaises(LaunchConfigurationError):
            load_config(self._write(payload))

    def test_a_legacy_profile_still_holding_placeholders_still_starts(self) -> None:
        """An unfilled placeholder used to be fatal, because that profile was
        going to be loaded. Nothing loads from `profiles` now, so the file is
        merely stale -- and a stale file the owner needs the running app to
        fix must not be the reason the app will not run."""

        payload = self._valid()
        payload["profiles"] = [{
            "profile_id": "x", "display_name": "X", "family": "f",
            "payload_references": {
                "checkpoint": "<REPLACE-WITH-ABSOLUTE-CHECKPOINT-PATH>",
                "text_encoder": "ok", "vae": "ok",
            },
        }]
        config = load_config(self._write(payload))
        self.assertIn("profiles", config["legacy_keys"])
        self.assertNotIn("profiles", config)

    def test_a_legacy_selection_naming_a_missing_profile_still_starts(self) -> None:
        payload = self._valid()
        payload["selected_profile_id"] = "ghost"
        config = load_config(self._write(payload))
        self.assertIn("selected_profile_id", config["legacy_keys"])
        self.assertNotIn("selected_profile_id", config)

    def test_a_wholly_legacy_config_starts_and_names_what_it_ignored(self) -> None:
        """The shape a real owner has on disk today, verbatim."""

        payload = self._valid()
        payload["autoload"] = False
        payload["selected_profile_id"] = "local"
        payload["profiles"] = [{
            "profile_id": "local", "display_name": "Local", "family": "qwen-image",
            "payload_references": {
                "checkpoint": "Z:/models/a.safetensors",
                "text_encoder": "Z:/models/b.safetensors",
                "vae": "Z:/models/c.safetensors",
            },
        }]
        config = load_config(self._write(payload))
        self.assertEqual(
            ("profiles", "selected_profile_id", "autoload"), config["legacy_keys"]
        )
        # Not merely absent from the result: absent from anything a loader
        # could reach. No path from this file survives into the process.
        self.assertNotIn("Z:/models", json.dumps(config, default=str))

    def test_timeout_above_the_authorization_cap_is_refused(self) -> None:
        payload = self._valid()
        payload["load_access"] = {"timeout_seconds": 601}
        with self.assertRaises(LaunchConfigurationError):
            load_config(self._write(payload))

    def test_the_template_itself_is_placeholder_guarded(self) -> None:
        template = json.loads(
            (APP_ROOT / "docs" / "studio" / "internal-alpha"
             / "studio-config.template.json").read_text(encoding="utf-8")
        )
        rendered = json.dumps(template)
        self.assertIn("<REPLACE", rendered)
        self.assertNotIn("Private-Local", rendered)

    def test_launch_module_import_is_free(self) -> None:
        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        head = source.split("class ExplicitLoadAccess", 1)[0]
        for banned in ("import torch", "from backend", "from modules"):
            self.assertNotIn(banned, head)


# ------------------------------------------------------ the §12 rehearsal


def _run_probe() -> dict:
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="internal-alpha-probe-") as scratch:
        result = subprocess.run(
            [sys.executable, "-B",
             str(APP_ROOT / "tests" / "studio_alpha"
                 / "_internal_alpha_probe.py"),
             scratch],
            capture_output=True, text=True, cwd=str(APP_ROOT), env=env,
            timeout=600,
        )
    stdout = result.stdout
    begin = stdout.find("PROBE_JSON_BEGIN")
    end = stdout.find("PROBE_JSON_END")
    if begin < 0 or end < 0:
        raise AssertionError(
            "probe emitted no report; stderr tail: " + result.stderr[-800:]
        )
    return json.loads(stdout[begin + len("PROBE_JSON_BEGIN"):end].strip())


class _RehearsalHolder:
    report: dict | None = None

    @classmethod
    def get(cls) -> dict:
        if cls.report is None:
            cls.report = _run_probe()
        return cls.report


class InternalAlphaRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _RehearsalHolder.get()
        cls.facts = cls.report["facts"]
        cls.counts = cls.report["counts"]

    def test_the_probe_completed(self) -> None:
        self.assertIsNone(self.report["fatal"], self.report["fatal"])

    def test_the_frontend_serves_with_the_model_controls_module(self) -> None:
        self.assertTrue(self.facts["frontend_served"])
        self.assertTrue(self.facts["loader_lists_model_controls"])
        self.assertTrue(self.facts["module_served"])

    def test_the_frontend_carries_no_private_path(self) -> None:
        self.assertTrue(self.facts["frontend_carries_no_private_path"])

    def test_startup_is_no_model_and_refuses_an_unselected_job(self) -> None:
        # A job naming no model still cannot run with nothing resident. A job
        # naming a selection can, and does -- see below.
        self.assertEqual("no_model", self.facts["startup_state"])
        self.assertTrue(self.facts["startup_refuses_unselected_generation"])

    def test_the_retired_profile_and_load_routes_are_gone(self) -> None:
        # Asserted as absence: an unused-but-live load route is a second way
        # to make a model resident, and two paths into one resident session is
        # the ownership split this lifecycle exists to prevent.
        self.assertTrue(self.facts["retired_routes_absent"])

    def test_generate_is_what_makes_the_selection_resident(self) -> None:
        # Choosing a model opens nothing; asking to generate is what loads.
        self.assertTrue(self.facts["selection_opens_nothing"])
        self.assertIn(self.facts["state_after_autoload"], ("ready", "busy"))

    def test_job_states_appear_through_the_api(self) -> None:
        self.assertTrue(self.facts["job_1_completed"])
        self.assertEqual("running", self.facts["job_2_running_state"])
        self.assertTrue(self.facts["jobs_list_shows_running"])
        self.assertEqual("queued", self.facts["job_3_queued_state"])

    def test_the_queued_job_cancels_through_the_canonical_route(self) -> None:
        self.assertEqual(200, self.facts["job_3_cancel_http"])
        self.assertTrue(self.facts["job_3_cancelled_while_queued"])
        self.assertEqual("cancelled", self.facts["job_3_terminal"])
        self.assertEqual(0, self.facts["job_3_port_calls"])

    def test_results_are_png_and_survive_unload(self) -> None:
        self.assertTrue(self.facts["results_are_png"])
        self.assertTrue(self.facts["results_survive_unload"])

    def test_unload_returns_to_no_model_and_refuses(self) -> None:
        self.assertEqual("no_model", self.facts["state_after_unload"])
        self.assertTrue(self.facts["no_model_refusal"])

    def test_the_required_counts_hold(self) -> None:
        expected = {
            "servers": 1, "server_stops": 1, "loads": 1,
            "engines": 1, "sessions": 1, "jobs_submitted": 3,
            "jobs_reaching_port": 2, "jobs_completed": 2,
            "jobs_cancelled_queued": 1, "max_concurrent_port_calls": 1,
            "publications": 2, "per_job_releases": 2, "unloads": 1,
            "pre_unload_result_fetches": 2, "post_unload_result_fetches": 2,
            "real_payload_opens": 0, "cuda": 0, "external_requests": 0,
        }
        self.assertEqual(
            expected, {key: self.counts[key] for key in expected}
        )


class StartupSafetyTests(unittest.TestCase):
    PROBE = """
import sys, os
sys.path.insert(0, os.getcwd())
sys.argv = [sys.argv[0]]
from forge_studio.composition import build_standalone
from forge_studio.presentation import StudioPresentation, _StudioHTTPServer
from forge_studio import GenerationRequest
from forge_studio.launch import load_config
c = build_standalone(backend_kind="headless")
c.model_lifecycle.readiness(); c.model_lifecycle.state()
p = StudioPresentation(c.application, GenerationRequest)
s = _StudioHTTPServer(("127.0.0.1", 0), p)
s.server_close()
c.shutdown()
heavy = sorted({m.split('.')[0] for m in sys.modules
                if m.split('.')[0] in ('torch','backend','modules','gradio')})
print("HEAVY=" + ",".join(heavy))
"""

    def test_construction_frontend_and_server_import_nothing_heavy(self) -> None:
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = "-1"
        result = subprocess.run(
            [sys.executable, "-B", "-c", self.PROBE],
            capture_output=True, text=True, cwd=str(APP_ROOT), env=env,
            timeout=180,
        )
        self.assertEqual(0, result.returncode, result.stderr[-500:])
        for line in result.stdout.splitlines():
            if line.startswith("HEAVY="):
                self.assertEqual("", line[len("HEAVY="):])
                return
        self.fail("probe reported no HEAVY line")


class SuiteIntegrityTests(unittest.TestCase):
    def test_discovered_count_matches_the_declared_constant(self) -> None:
        loaded = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_INTERNAL_ALPHA_TESTS, loaded.countTestCases())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
