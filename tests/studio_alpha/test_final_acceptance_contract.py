"""The final acceptance contract: unload order, residency, runtime paths.

Three findings from the final native-UI owner trial are closed here.

D1  The generation adapter stayed attached until AFTER public ``NO_MODEL``.
    The defect survived because the suite asserted against a hand-written
    MODEL of the unload (in `test_terminal_vram_release.py`) whose adapter
    detached early, rather than against the unload itself. These tests read
    `forge_headless.unload_events`, which the product records as it goes.

D2  A first-to-second-job delta above 1 MiB failed the run by itself. It
    cannot separate Forge staging weight residency across generations from a
    session retaining generation state, and the trial failed on it while
    producing byte-identical images to an earlier passing run and releasing
    everything at unload.

D3  `sys.path` ended one entry longer than it began. The owner is
    `modules/paths.py`, which inserts unguarded at import time; these tests
    prove the entry is app-scoped and that repeated cycles add no more.

SCOPE: no real model, no CUDA, no payload, no network. The load/unload cycles
run against the seam suite's synthetic world.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.memory_report import (  # noqa: E402
    ACCEPTABLE_OUTCOMES,
    MEMORY_ACCEPTED,
    MEMORY_TELEMETRY_UNAVAILABLE,
    MEMORY_THRESHOLD_EXCEEDED,
    OWNERSHIP_STATE_INCONSISTENT,
    STAGED_RESIDENCY,
    WARM_RETENTION_SUSPECTED,
    classify_memory,
)
from forge_headless.runtime_path_audit import (  # noqa: E402
    EXTERNAL,
    POLICY_APP_LIFETIME,
    REPOSITORY_ROOT,
    audit,
    classify_entry,
    difference,
    entry_hash,
    owner_of,
)
from forge_headless.unload_events import (  # noqa: E402
    ADAPTER_DETACHED,
    ENGINE_OWNERSHIP_RELEASED,
    NO_MODEL_PUBLISHED,
    PRODUCT_ORDER,
    RECORDER,
    RELOAD_BOOKKEEPING_RESTORED,
    SENTINEL_RESTORED,
    TERMINAL_CACHE_CLEAR,
    UNLOADING_PUBLISHED,
    UnloadEventRecorder,
)

EXPECTED_ACCEPTANCE_TESTS = 47


def _ok(allocated: int, reserved: int = 0) -> dict:
    return {"status": "ok", "allocated_bytes": allocated,
            "reserved_bytes": reserved}


_OWNERSHIP_CLEAN = {
    "owned_weakrefs_dead": True,
    "registry_restored": True,
    "global_state_restored": True,
}
_RESIDENCY_CLEAN = {
    "per_job_references_dead": True,
    "engine_count": 1,
    "session_count": 1,
    "registry_count_stable": True,
}


def _classify(**extra):
    base = dict(
        job1_post_release=_ok(1_000), job2_post_release=_ok(1_000),
        post_unload=_ok(1_000_000, 2_000_000),
        warm_tolerance_bytes=1_048_576,
        allocated_ceiling_bytes=11_206_656,
        reserved_ceiling_bytes=26_214_400,
        ownership=_OWNERSHIP_CLEAN,
    )
    base.update(extra)
    return classify_memory(**base)


# ------------------------------------------------------- D1: the recorder


class RecorderContractTests(unittest.TestCase):
    """The recorder itself, before anything relies on it."""

    def setUp(self) -> None:
        self.recorder = UnloadEventRecorder()

    def test_it_records_nothing_until_switched_on(self) -> None:
        self.recorder.record(ADAPTER_DETACHED)
        self.assertEqual((), self.recorder.names())
        self.assertFalse(self.recorder.enabled)

    def test_enabling_starts_from_empty(self) -> None:
        self.recorder.enable()
        self.recorder.record(ADAPTER_DETACHED)
        self.recorder.enable()
        self.assertEqual((), self.recorder.names())

    def test_the_declared_product_order_satisfies_itself(self) -> None:
        self.recorder.enable()
        for step in PRODUCT_ORDER:
            self.recorder.record(step)
        self.assertTrue(self.recorder.follows_product_order())
        self.assertEqual((), self.recorder.missing_steps())

    def test_the_owner_trial_defect_is_caught(self) -> None:
        """Adapter after NO_MODEL: exactly what the trial found."""

        self.recorder.enable()
        for step in (UNLOADING_PUBLISHED, ENGINE_OWNERSHIP_RELEASED,
                     TERMINAL_CACHE_CLEAR, NO_MODEL_PUBLISHED,
                     ADAPTER_DETACHED):
            self.recorder.record(step)
        self.assertFalse(self.recorder.follows_product_order())
        self.assertFalse(
            self.recorder.happened_before(ADAPTER_DETACHED, NO_MODEL_PUBLISHED)
        )

    def test_an_absent_step_is_absence_not_disorder(self) -> None:
        """A process with no CUDA has no clear to record, and says so."""

        self.recorder.enable()
        for step in (UNLOADING_PUBLISHED, ADAPTER_DETACHED,
                     ENGINE_OWNERSHIP_RELEASED, NO_MODEL_PUBLISHED):
            self.recorder.record(step)
        self.assertTrue(self.recorder.follows_product_order())
        self.assertEqual((), self.recorder.missing_steps())
        self.assertEqual(-1, self.recorder.index_of(TERMINAL_CACHE_CLEAR))

    def test_missing_required_steps_are_named(self) -> None:
        self.recorder.enable()
        self.recorder.record(UNLOADING_PUBLISHED)
        self.assertEqual(
            (ADAPTER_DETACHED, NO_MODEL_PUBLISHED),
            self.recorder.missing_steps(),
        )

    def test_recording_never_raises(self) -> None:
        self.recorder.enable()
        self.recorder.record(None)  # type: ignore[arg-type]
        self.recorder.record(object())  # type: ignore[arg-type]
        self.assertEqual(2, len(self.recorder.names()))

    def test_happened_before_needs_both_steps(self) -> None:
        self.recorder.enable()
        self.recorder.record(ADAPTER_DETACHED)
        self.assertFalse(
            self.recorder.happened_before(ADAPTER_DETACHED, NO_MODEL_PUBLISHED)
        )

    def test_the_report_carries_a_timeline(self) -> None:
        self.recorder.enable()
        self.recorder.record(UNLOADING_PUBLISHED)
        self.recorder.record(ADAPTER_DETACHED)
        report = self.recorder.report()
        self.assertEqual(2, len(report["timeline"]))
        self.assertEqual(UNLOADING_PUBLISHED, report["timeline"][0]["event"])
        self.assertEqual(0.0, report["timeline"][0]["ms"])
        self.assertEqual(list(PRODUCT_ORDER), report["required_order"])


# --------------------------------------------- D1: the real product order


class ProductUnloadOrderTests(unittest.TestCase):
    """A real load and unload, watched through the canonical recorder."""

    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self.tmp = B._Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)
        self.addCleanup(self.intercepts.__exit__, None, None, None)
        self.addCleanup(RECORDER.disable)

    def _manager(self):
        from forge_studio.model_lifecycle import WarmSessionManager
        from forge_studio.model_profiles import ModelProfileRepository

        profile = B._profile()
        # The manager takes callables, not the loader object: loader(profile).
        loader = B._loader(self.tmp.path, self.neo)
        self.published: list = []
        return WarmSessionManager(
            profiles=ModelProfileRepository([profile]),
            loader=lambda selected: loader.load(selected),
            closer=lambda session: session.close(),
            on_session_changed=self.published.append,
        ), profile

    def _loaded_manager(self):
        manager, profile = self._manager()
        manager.ensure_loaded(profile)
        return manager

    def test_the_real_unload_follows_the_product_order(self) -> None:
        manager = self._loaded_manager()
        RECORDER.enable()
        manager.unload()
        self.assertTrue(
            RECORDER.follows_product_order(),
            f"observed {RECORDER.names()}",
        )

    def test_the_adapter_detaches_before_public_no_model(self) -> None:
        """THE D1 DEFECT. The owner trial saw this the other way round."""

        manager = self._loaded_manager()
        RECORDER.enable()
        manager.unload()
        self.assertTrue(
            RECORDER.happened_before(ADAPTER_DETACHED, NO_MODEL_PUBLISHED),
            f"observed {RECORDER.names()}",
        )

    def test_the_adapter_is_detached_when_no_model_is_published(self) -> None:
        manager = self._loaded_manager()
        manager.unload()
        # The composition's sink saw a session, then saw None.
        self.assertIsNotNone(self.published[0])
        self.assertIsNone(self.published[-1])
        self.assertEqual("no_model", manager.describe()["state"])

    def test_the_adapter_detaches_before_ownership_is_released(self) -> None:
        manager = self._loaded_manager()
        RECORDER.enable()
        manager.unload()
        self.assertTrue(
            RECORDER.happened_before(ADAPTER_DETACHED,
                                     ENGINE_OWNERSHIP_RELEASED),
            f"observed {RECORDER.names()}",
        )

    def test_unloading_is_published_before_the_close(self) -> None:
        manager = self._loaded_manager()
        RECORDER.enable()
        manager.unload()
        self.assertEqual(UNLOADING_PUBLISHED, RECORDER.names()[0])

    def test_the_restores_run_in_the_documented_order(self) -> None:
        """Bookkeeping first, sentinel last -- the safe order, not the neat one.

        Republishing the retained sentinel last is what stops a released
        engine being left as the retained code's current model.
        """

        manager = self._loaded_manager()
        RECORDER.enable()
        manager.unload()
        if RECORDER.index_of(SENTINEL_RESTORED) >= 0:
            self.assertTrue(
                RECORDER.happened_before(RELOAD_BOOKKEEPING_RESTORED,
                                         SENTINEL_RESTORED),
                f"observed {RECORDER.names()}",
            )

    def test_generation_is_refused_while_unloading(self) -> None:
        """The contract names refusal as a step, so it is stated, not implied."""

        from forge_studio.model_lifecycle import _NOT_ACCEPTING
        from forge_studio.model_lifecycle import ModelLifecycleState as S

        self.assertIn(S.UNLOADING, _NOT_ACCEPTING)
        self.assertIn(S.SWITCHING, _NOT_ACCEPTING)

    def test_a_switch_also_detaches_through_the_one_seam(self) -> None:
        manager, profile = self._manager()
        manager.ensure_loaded(profile)
        RECORDER.enable()
        manager.shutdown()
        self.assertIn(ADAPTER_DETACHED, RECORDER.names())
        self.assertIsNone(self.published[-1])

    def test_an_unload_with_no_session_records_no_detach(self) -> None:
        # Nothing is loaded, so there is nothing to detach. This used to
        # select a profile first; selecting is no longer a server-side act, so
        # the arrangement is simply an unload against a cold manager -- which
        # is what the test always actually meant.
        manager, _profile = self._manager()
        RECORDER.enable()
        manager.unload()
        self.assertNotIn(ADAPTER_DETACHED, RECORDER.names())


# ------------------------------------------------- D2: residency verdicts


class ResidencyClassifierTests(unittest.TestCase):
    """The regression matrix the acceptance contract specifies."""

    def test_early_staged_growth_with_a_clean_unload_is_accepted(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(4_198_994_432),
            job2_post_release=_ok(5_060_981_760),
            residency=_RESIDENCY_CLEAN,
        )
        self.assertEqual(STAGED_RESIDENCY, verdict["outcome"])
        self.assertIn(verdict["outcome"], ACCEPTABLE_OUTCOMES)

    def test_flat_warm_memory_with_a_clean_unload_is_accepted(self) -> None:
        self.assertEqual(MEMORY_ACCEPTED, _classify()["outcome"])

    def test_monotonic_growth_with_dirty_refs_is_suspected(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(1_000),
            job2_post_release=_ok(2_000_000_000),
            residency={**_RESIDENCY_CLEAN, "per_job_references_dead": False},
            post_release_series=[1_000, 2_000_000_000, 4_500_000_000],
        )
        self.assertEqual(WARM_RETENTION_SUSPECTED, verdict["outcome"])
        self.assertIn("per_job_references_survived",
                      verdict["retention_signals"])

    def test_clean_numbers_with_dirty_ownership_is_inconsistent(self) -> None:
        verdict = _classify(
            ownership={**_OWNERSHIP_CLEAN, "registry_restored": False})
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])

    def test_missing_telemetry_fails_closed(self) -> None:
        verdict = _classify(job2_post_release=None)
        self.assertEqual(MEMORY_TELEMETRY_UNAVAILABLE, verdict["outcome"])

    def test_high_post_unload_reserved_exceeds(self) -> None:
        verdict = _classify(post_unload=_ok(0, 26_214_401))
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])
        self.assertIn("post_unload_reserved", verdict["thresholds_exceeded"])

    def test_growth_that_never_decelerates_is_suspected(self) -> None:
        """Staging is bounded; growth that keeps accelerating is not staging."""

        verdict = _classify(
            job1_post_release=_ok(1_000_000_000),
            job2_post_release=_ok(2_000_000_000),
            residency=_RESIDENCY_CLEAN,
            post_release_series=[1_000_000_000, 2_000_000_000, 3_500_000_000],
        )
        self.assertEqual(WARM_RETENTION_SUSPECTED, verdict["outcome"])
        self.assertIn("growth_not_decelerating", verdict["retention_signals"])

    def test_decelerating_growth_reads_as_staging(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(1_000_000_000),
            job2_post_release=_ok(2_000_000_000),
            residency=_RESIDENCY_CLEAN,
            post_release_series=[1_000_000_000, 2_000_000_000, 2_000_500_000],
        )
        self.assertEqual(STAGED_RESIDENCY, verdict["outcome"])
        self.assertTrue(verdict["plateau_observable"])

    def test_a_second_engine_is_a_retention_signal(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(0), job2_post_release=_ok(2_000_000_000),
            residency={**_RESIDENCY_CLEAN, "engine_count": 2},
        )
        self.assertEqual(WARM_RETENTION_SUSPECTED, verdict["outcome"])
        self.assertIn("engine_count", verdict["retention_signals"])

    def test_the_peak_ceiling_is_a_hard_gate(self) -> None:
        verdict = _classify(
            peak_allocated_bytes=15 * 1024 ** 3,
            peak_ceiling_bytes=14 * 1024 ** 3,
        )
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])
        self.assertIn("peak_allocated", verdict["thresholds_exceeded"])

    def test_out_of_memory_is_a_hard_gate(self) -> None:
        verdict = _classify(out_of_memory=True)
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])

    def test_a_hard_gate_beats_a_staged_reading(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(4_198_994_432),
            job2_post_release=_ok(5_060_981_760),
            residency=_RESIDENCY_CLEAN,
            post_unload=_ok(0, 26_214_401),
        )
        self.assertEqual(MEMORY_THRESHOLD_EXCEEDED, verdict["outcome"])

    def test_ownership_beats_everything(self) -> None:
        verdict = _classify(
            job1_post_release=_ok(0), job2_post_release=_ok(2_000_000_000),
            residency=_RESIDENCY_CLEAN,
            ownership={**_OWNERSHIP_CLEAN, "owned_weakrefs_dead": False},
        )
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])


# --------------------------------------------------- D3: runtime path policy


class RuntimePathAuditTests(unittest.TestCase):
    def test_the_app_root_is_categorised_and_owned(self) -> None:
        record = classify_entry(str(APP_ROOT), app_root=APP_ROOT)
        self.assertEqual(REPOSITORY_ROOT, record["category"])
        self.assertEqual(".", record["relative"])
        self.assertEqual("modules/paths.py",
                         owner_of(record["hash"], app_root=APP_ROOT))

    def test_an_outside_entry_discloses_no_path(self) -> None:
        record = classify_entry(str(Path(APP_ROOT).parent / "elsewhere"),
                                app_root=APP_ROOT)
        self.assertEqual(EXTERNAL, record["category"])
        self.assertIsNone(record["relative"])
        self.assertEqual(16, len(record["hash"]))

    def test_the_hash_is_stable_across_spelling(self) -> None:
        spellings = [str(APP_ROOT), str(APP_ROOT) + os.sep,
                     os.path.join(str(APP_ROOT), "modules", "..")]
        self.assertEqual({entry_hash(s) for s in spellings},
                         {entry_hash(str(APP_ROOT))})

    def test_an_audit_counts_duplicates(self) -> None:
        root = str(APP_ROOT)
        snapshot = audit([root, root, "/somewhere/else"], app_root=APP_ROOT)
        self.assertEqual(3, snapshot["entry_count"])
        self.assertEqual(2, snapshot["unique_entries"])
        self.assertEqual(1, snapshot["duplicate_count"])

    def test_difference_reports_by_hash_not_by_path(self) -> None:
        before = audit([str(APP_ROOT)], app_root=APP_ROOT)
        after = audit([str(APP_ROOT), str(APP_ROOT / "modules_forge")],
                      app_root=APP_ROOT)
        delta = difference(before, after)
        self.assertEqual(1, delta["delta"])
        self.assertFalse(delta["unchanged"])
        self.assertEqual(1, len(delta["added_hashes"]))


class ThreeCyclePathStabilityTests(unittest.TestCase):
    """Policy B: a stable app-lifetime bootstrap entry, proven not to grow.

    `modules/paths.py` inserts the app root at import time with no guard, and
    Python imports it once per process. The product's own runtime environment
    inserts the app root and its packages directory only when they are absent,
    and removes exactly what it inserted. The prediction is therefore: cycle 1
    may add the bootstrap entry, and cycles 2 and 3 add nothing.
    """

    CYCLES = 3

    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        self.tmp = B._Tmp()
        self.addCleanup(self.tmp.close)
        self.addCleanup(self.neo.restore)
        self.addCleanup(self.intercepts.__exit__, None, None, None)

    def _cycle(self) -> dict:
        loader = B._loader(self.tmp.path, self.neo)
        session = loader.load(B._profile())
        session.close()
        return audit(app_root=APP_ROOT)

    def test_repeated_cycles_add_no_path_entries(self) -> None:
        snapshots = [audit(app_root=APP_ROOT)]
        for _ in range(self.CYCLES):
            snapshots.append(self._cycle())

        counts = [snapshot["entry_count"] for snapshot in snapshots]
        # Cycles 1, 2 and 3 must all leave the same count. The first may
        # differ from the pre-load baseline by the one-time bootstrap insert.
        self.assertEqual(
            counts[1], counts[2],
            f"cycle 2 changed the path count: {counts}")
        self.assertEqual(
            counts[2], counts[3],
            f"cycle 3 changed the path count: {counts}")
        self.assertLessEqual(
            counts[1] - counts[0], 1,
            f"a cycle added more than the one bootstrap entry: {counts}")

    def test_the_managed_entries_are_removed_every_cycle(self) -> None:
        """What the synthetic cycles genuinely prove.

        The seam suite's world never imports `modules/paths.py`, so these
        cycles do NOT exercise the bootstrap insert -- that is what
        `BootstrapEntryPolicyTests` is for. What they do exercise is the
        product's own install/restore accounting across repeated loads, which
        must leave nothing of its own behind.
        """

        before = audit(app_root=APP_ROOT)
        for _ in range(self.CYCLES):
            after = self._cycle()
        packages = entry_hash(str(Path(APP_ROOT) / "modules_forge" / "packages"))
        self.assertEqual(
            [], [r for r in after["entries"] if r["hash"] == packages],
            "the product's managed packages entry survived a cycle")
        self.assertTrue(difference(before, after)["unchanged"])

    def test_the_policy_is_named(self) -> None:
        self.assertEqual("stable-app-lifetime-bootstrap", POLICY_APP_LIFETIME)


class BootstrapEntryPolicyTests(unittest.TestCase):
    """Policy B, against the interaction that actually produces the +1.

    `modules/paths.py` inserts the app root unguarded at import time; the
    product's runtime environment inserts the app root and its packages
    directory only when absent, and removes exactly what it inserted. The
    consequence, which is the whole policy, is that once the bootstrap entry
    exists the product stops managing the app root -- so it can neither add a
    duplicate nor remove somebody else's entry.

    Importing `modules/paths.py` here is avoided deliberately: it mutates
    `sys.argv` from the environment at import time and would leak into every
    later test in the process. The entry it inserts is seeded directly
    instead, which is the same starting condition.
    """

    CYCLES = 3

    #: The cycles run in a CHILD process, for two reasons. `close()` reaches
    #: `modules.shared`, and importing the Neo package here would break the
    #: startup-purity boundary tests that share this process. And the policy
    #: is about what happens across cycles in ONE process, which a child gives
    #: exactly and a shared runner cannot.
    _CHILD = r"""
import json, sys
sys.path.insert(0, {app!r})
from forge_headless.runtime_path_audit import audit, entry_hash
from forge_headless.live_bindings import StudioStartupGlobals

APP = {app!r}
root = entry_hash(APP)
# Nothing is seeded to stand in for `modules/paths.py`: it runs for real
# during the first cycle, when `close()` reaches `modules.shared`. The one
# app-root entry already present is this child's own import bootstrap.
out = {{"baseline": audit(app_root=APP)["entry_count"],
        "baseline_app_root_copies": sum(
            1 for r in audit(app_root=APP)["entries"] if r["hash"] == root),
        "baseline_paths_imported": "modules.paths" in sys.modules,
        "cycles": [], "paths_imported": []}}
for _ in range({cycles}):
    handle = StudioStartupGlobals(repository_root=APP)
    handle._install_runtime_environment()
    handle.close()
    snapshot = audit(app_root=APP)
    out["cycles"].append({{
        "entry_count": snapshot["entry_count"],
        "app_root_copies": sum(
            1 for r in snapshot["entries"] if r["hash"] == root),
        "managed_packages_present": any(
            r["hash"] == entry_hash(APP + "/modules_forge/packages")
            for r in snapshot["entries"]),
    }})
    out["paths_imported"].append("modules.paths" in sys.modules)
print(json.dumps(out))
"""

    @classmethod
    def setUpClass(cls) -> None:
        import json
        import subprocess

        script = cls._CHILD.format(app=str(APP_ROOT), cycles=cls.CYCLES)
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", script],
            capture_output=True, text=True, cwd=str(APP_ROOT), timeout=300,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"path-cycle child failed: {completed.stderr[-800:]}")
        cls.result = json.loads(completed.stdout.strip().splitlines()[-1])

    def test_repeated_cycles_leave_the_same_entry_count(self) -> None:
        """THE POLICY. Cycles 1, 2 and 3 must agree."""

        counts = [cycle["entry_count"] for cycle in self.result["cycles"]]
        self.assertEqual(counts[0], counts[1],
                         f"cycle 2 changed the path count: {counts}")
        self.assertEqual(counts[1], counts[2],
                         f"cycle 3 changed the path count: {counts}")

    def test_a_cycle_adds_at_most_the_one_bootstrap_entry(self) -> None:
        counts = [cycle["entry_count"] for cycle in self.result["cycles"]]
        self.assertLessEqual(
            counts[0] - self.result["baseline"], 1,
            f"a cycle added more than the one bootstrap entry: "
            f"baseline {self.result['baseline']}, cycles {counts}")

    def test_the_app_root_count_is_stable_across_cycles(self) -> None:
        """It may gain the bootstrap entry once; it must never gain another.

        The child already carries one app-root entry -- the one it needed to
        import the product at all -- so the absolute number is a property of
        the harness. What the policy rests on is that repeating the cycle
        changes nothing.
        """

        copies = [cycle["app_root_copies"] for cycle in self.result["cycles"]]
        self.assertEqual(copies[0], copies[1],
                         f"cycle 2 duplicated the app root: {copies}")
        self.assertEqual(copies[1], copies[2],
                         f"cycle 3 duplicated the app root: {copies}")
        self.assertLessEqual(
            copies[0] - self.result["baseline_app_root_copies"], 1,
            f"more than one bootstrap insert: {copies}")

    def test_the_managed_packages_entry_is_removed_every_cycle(self) -> None:
        present = [cycle["managed_packages_present"]
                   for cycle in self.result["cycles"]]
        self.assertEqual([False, False, False], present,
                         "the product left its own packages entry behind")

    def test_the_bootstrap_owner_imports_once(self) -> None:
        """`modules.paths` is reached during the first cycle and stays."""

        self.assertEqual([True, True, True], self.result["paths_imported"])

    def test_the_bootstrap_insert_is_unguarded_and_therefore_once_only(
        self,
    ) -> None:
        """The mechanism, read from the owner's own source."""

        source = (Path(APP_ROOT) / "modules" / "paths.py").read_text(
            encoding="utf-8")
        self.assertIn("sys.path.insert(0, script_path)", source)
        # No presence check protects it, which is why it is safe only by
        # virtue of running once per process -- the property proved above.
        self.assertNotIn("if script_path not in sys.path", source)

    def test_the_surviving_entry_is_inside_the_app_root(self) -> None:
        record = classify_entry(str(Path(APP_ROOT).resolve()),
                                app_root=APP_ROOT)
        self.assertEqual(REPOSITORY_ROOT, record["category"])
        self.assertNotEqual(EXTERNAL, record["category"])
        self.assertEqual("modules/paths.py",
                         owner_of(record["hash"], app_root=APP_ROOT))


class SuiteIntegrityTests(unittest.TestCase):
    def test_discovered_count_matches_the_declared_constant(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(
            "tests.studio_alpha.test_final_acceptance_contract")
        self.assertEqual(EXPECTED_ACCEPTANCE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
