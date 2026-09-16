"""Contracts for the live-path closure sweep.

Two halves:

* **In-process** tests (NeoStub world, cheap): the runtime-environment
  ownership `StudioStartupGlobals` gained -- sys.argv, the repository root and
  `modules_forge/packages` on sys.path -- plus the safe_open restoration gaps
  around the engine-builder import.

* **Probe-backed** tests: one subprocess (`_live_path_probe.py`) with the GPU
  hidden runs the REAL import chain -- backend.loader, anima,
  huggingface_guess -- through the real product startup, stops at a contained
  sentinel on the first payload open (Stage A), then continues the whole
  lifecycle with actual product classes and only the terminals replaced
  (Stage B + full rehearsal). The probe runs once; the tests here assert its
  recorded facts.

The probe exists because the second live attempt failed on an import the
stub-based rehearsal could not see: intercepting a terminal hides that
terminal's own import prerequisites. Stage A intercepts one level deeper --
at the payload byte boundary -- so every import above it is real.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE in-process. The probe
subprocess imports torch and modules.* for real, with CUDA unavailable by
construction; it opens no real model and generates no image.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.live_bindings import (  # noqa: E402
    LoadConfiguration,
    StudioStartupGlobals,
)
from forge_headless.session_loader import HeadlessSessionLoader  # noqa: E402

EXPECTED_CLOSURE_TESTS = 49

_PACKAGES = str((APP_ROOT / "modules_forge" / "packages").resolve())
_ROOT = str(APP_ROOT.resolve())


# ---------------------------------------------------- runtime environment


class RuntimeEnvironmentTests(unittest.TestCase):
    """StudioStartupGlobals now owns argv and the two managed path entries."""

    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.addCleanup(self.neo.restore)
        self._argv = sys.argv
        self._path = list(sys.path)
        self.addCleanup(self._put_back)

    def _put_back(self) -> None:
        sys.argv = self._argv
        sys.path[:] = self._path

    def _owner(self) -> StudioStartupGlobals:
        return StudioStartupGlobals(repository_root=APP_ROOT)

    def test_install_normalizes_argv(self) -> None:
        sys.argv = ["prog", "--foreign-flag", "value"]
        owner = self._owner().install()
        self.addCleanup(owner.close)
        self.assertEqual([sys.argv[0]], sys.argv)

    def test_close_restores_the_exact_argv_object(self) -> None:
        """Identity AND values -- strengthened for confirmed gap 2.

        The original assertion checked equality only, which is how a restored
        COPY passed review and reached the final live run. The exact original
        object must come back, unmutated.
        """

        hostile = ["prog", "--foreign-flag"]
        sys.argv = hostile
        owner = self._owner().install()
        # The temporary argv is a dedicated object, not the original.
        self.assertIsNot(hostile, sys.argv)
        owner.close()
        self.assertIs(hostile, sys.argv)
        self.assertEqual(("prog", "--foreign-flag"), tuple(sys.argv))

    def test_install_puts_the_packages_directory_first(self) -> None:
        while _PACKAGES in sys.path:
            sys.path.remove(_PACKAGES)
        owner = self._owner().install()
        self.addCleanup(owner.close)
        self.assertEqual(_PACKAGES, sys.path[0])

    def test_close_removes_only_what_install_added(self) -> None:
        while _PACKAGES in sys.path:
            sys.path.remove(_PACKAGES)
        before = list(sys.path)
        owner = self._owner().install()
        owner.close()
        self.assertEqual(before, sys.path)

    def test_a_preexisting_entry_is_neither_duplicated_nor_removed(self) -> None:
        if _PACKAGES not in sys.path:
            sys.path.insert(0, _PACKAGES)
        owner = self._owner().install()
        self.assertEqual(1, sys.path.count(_PACKAGES))
        owner.close()
        self.assertIn(_PACKAGES, sys.path)

    def test_the_repository_root_is_managed_the_same_way(self) -> None:
        had = _ROOT in sys.path
        owner = self._owner().install()
        self.assertIn(_ROOT, sys.path)
        owner.close()
        self.assertEqual(had, _ROOT in sys.path)

    def test_close_is_idempotent_for_the_environment(self) -> None:
        sys.argv = ["prog", "--x"]
        owner = self._owner().install()
        first = owner.close()
        second = owner.close()
        self.assertEqual(first, second)
        self.assertEqual(["prog", "--x"], sys.argv)

    def test_partial_install_failure_restores_argv_and_path(self) -> None:
        sys.argv = ["prog", "--foreign"]
        while _PACKAGES in sys.path:
            sys.path.remove(_PACKAGES)
        del sys.modules["modules.shared_total_tqdm"].TotalTQDM
        owner = self._owner()
        with self.assertRaises(Exception):
            owner.install()
        self.assertEqual(["prog", "--foreign"], sys.argv)
        self.assertNotIn(_PACKAGES, sys.path)

    def test_close_reports_the_environment_restoration(self) -> None:
        owner = self._owner().install()
        report = owner.close()
        self.assertTrue(report["sys_path_restored"])
        self.assertTrue(report["argv_restored"])

    def test_no_environment_variable_is_consulted(self) -> None:
        source = (APP_ROOT / "forge_headless" / "live_bindings.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)

    def test_install_happens_before_any_modules_import(self) -> None:
        """Argv must be clean by the time modules.shared is first imported."""

        source = (APP_ROOT / "forge_headless" / "live_bindings.py").read_text(
            encoding="utf-8"
        )
        env = source.index("self._install_runtime_environment()")
        options = source.index("self._install_options()")
        self.assertLess(env, options)


# ------------------------------------------------- safe_open restoration


class WatchRestorationTests(unittest.TestCase):
    """The two safe_open leak paths the second live attempt exposed."""

    def setUp(self) -> None:
        self.neo = B.NeoStub().install()
        self.addCleanup(self.neo.restore)
        self._argv = sys.argv
        self._path = list(sys.path)
        self.addCleanup(self._put_back)
        self.auth = B.FakeAuthorization()
        self.configuration = LoadConfiguration(
            repository_root=APP_ROOT, authorization=self.auth
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _put_back(self) -> None:
        sys.argv = self._argv
        sys.path[:] = self._path

    def _loader(self, **kwargs) -> HeadlessSessionLoader:
        return HeadlessSessionLoader(
            result_root=Path(self.tmp.name),
            load_configuration=self.configuration,
            **kwargs,
        )

    def _original_safe_open(self):
        import safetensors

        return safetensors.safe_open

    def test_an_import_failure_restores_safe_open(self) -> None:
        """The exact second-live-attempt failure, with the fix in place."""

        original = self._original_safe_open()
        sys.modules["backend.loader"] = None  # import raises ImportError
        with B._Intercepts():
            loader = self._loader()
            with self.assertRaises(Exception) as caught:
                loader.load(B._profile())
        self.assertEqual("engine_built", caught.exception.step)
        import safetensors

        self.assertIs(original, safetensors.safe_open)

    def test_a_cancellation_after_the_open_restores_safe_open(self) -> None:
        """The third gap: cancel between payload-open and engine-build."""

        original = self._original_safe_open()
        calls = {"count": 0}

        def cancelled_at_engine() -> bool:
            # The loader consults the probe at: before_payload_access,
            # payloads, engine, port. Flip to cancelled at the third check.
            calls["count"] += 1
            return calls["count"] >= 3

        with B._Intercepts():
            loader = self._loader()
            with self.assertRaises(Exception) as caught:
                loader.load(B._profile(), cancellation=cancelled_at_engine)
        self.assertEqual("MODEL_LOAD_CANCELLED", caught.exception.code)
        import safetensors

        self.assertIs(original, safetensors.safe_open)

    def test_a_late_step_failure_restores_safe_open(self) -> None:
        original = self._original_safe_open()
        with B._Intercepts():
            loader = self._loader(
                session_factory=lambda **_k: (_ for _ in ()).throw(
                    RuntimeError("session")
                )
            )
            with self.assertRaises(Exception) as caught:
                loader.load(B._profile())
        self.assertEqual("session_built", caught.exception.step)
        import safetensors

        self.assertIs(original, safetensors.safe_open)

    def test_a_successful_load_leaves_safe_open_unpatched(self) -> None:
        """The patch must not persist into the warm session."""

        original = self._original_safe_open()
        with B._Intercepts():
            loaded = self._loader().load(B._profile())
            self.addCleanup(loaded.close)
            import safetensors

            self.assertIs(original, safetensors.safe_open)

    def test_the_import_sits_inside_the_protected_region(self) -> None:
        source = (APP_ROOT / "forge_headless" / "live_bindings.py").read_text(
            encoding="utf-8"
        )
        builder = source.index("def build_forge_engine")
        watch = source.index('watch = getattr(opened, "_watch", None)', builder)
        try_start = source.index("try:", watch)
        # The 8-space indent finds the real statement, not the comment above
        # the try block that quotes it.
        the_import = source.index(
            "        from backend.loader import forge_loader", builder
        )
        self.assertLess(watch, the_import)
        self.assertLess(try_start, the_import)

    def test_the_loader_unwind_owns_the_watch_too(self) -> None:
        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("watch_restore = getattr(", source)


# --------------------------------------------------------- the probe run


def _run_probe() -> dict:
    # The probe also sets CUDA_VISIBLE_DEVICES="-1" internally; passing it
    # here as well keeps the containment visible at the call site. The
    # temporary directory is cleaned up on every exit path.
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="live-path-probe-") as scratch:
        result = subprocess.run(
            [sys.executable, "-B",
             str(APP_ROOT / "tests" / "studio_alpha" / "_live_path_probe.py"),
             scratch],
            capture_output=True, text=True, cwd=str(APP_ROOT), env=env,
            timeout=900,
        )
    stdout = result.stdout
    begin = stdout.find("PROBE_JSON_BEGIN")
    end = stdout.find("PROBE_JSON_END")
    if begin < 0 or end < 0:
        raise AssertionError(
            "probe emitted no report; stderr tail: " + result.stderr[-800:]
        )
    payload = stdout[begin + len("PROBE_JSON_BEGIN"):end].strip()
    report = json.loads(payload)
    report["_returncode"] = result.returncode
    return report


class _ProbeHolder:
    report: dict | None = None

    @classmethod
    def get(cls) -> dict:
        if cls.report is None:
            cls.report = _run_probe()
        return cls.report


class RealTerminalReachabilityTests(unittest.TestCase):
    """Stage A facts: real imports, sentinel at the first payload open."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _ProbeHolder.get()
        cls.phase0 = cls.report["phase0"]
        cls.stage_a = cls.report["stage_a"]

    def test_the_probe_completed(self) -> None:
        self.assertIsNone(self.report["fatal"], self.report["fatal"])

    def test_absence_of_the_managed_path_fails_the_import(self) -> None:
        self.assertFalse(self.phase0["packages_on_path_before"])
        self.assertFalse(self.phase0["import_succeeded_without_path"])

    def test_the_missing_module_is_a_packages_vendored_one(self) -> None:
        """gguf is hit first (backend.utils reaches it before anima reaches
        huggingface_guess); the live attempt's inferred name was downstream of
        the same missing directory. Either name proves the same root cause."""

        self.assertIn(self.phase0["missing_module"],
                      ("gguf", "huggingface_guess"))

    def test_the_failed_import_is_not_left_half_registered(self) -> None:
        self.assertFalse(self.phase0["backend_loader_in_sys_modules"])

    def test_backend_loader_imports_through_the_product_startup(self) -> None:
        self.assertTrue(self.stage_a["backend_loader_imported"])

    def test_anima_imports_through_the_product_startup(self) -> None:
        self.assertTrue(self.stage_a["anima_imported"])

    def test_huggingface_guess_imports_through_the_product_startup(self) -> None:
        self.assertTrue(self.stage_a["huggingface_guess_imported"])

    def test_the_sentinel_fired_exactly_once(self) -> None:
        self.assertEqual(1, self.stage_a["sentinel_calls"])

    def test_the_first_open_consumed_the_authorization(self) -> None:
        self.assertFalse(self.stage_a["authorization_consumed_before"])
        self.assertTrue(self.stage_a["authorization_consumed_after"])

    def test_the_failure_is_scalar_and_names_the_step(self) -> None:
        self.assertFalse(self.stage_a["load_returned"])
        self.assertEqual("engine_built", self.stage_a["error_step"])

    def test_safe_open_was_restored_after_the_unwind(self) -> None:
        self.assertTrue(self.stage_a["safe_open_restored_to_sentinel"])

    def test_the_managed_path_was_removed_by_the_unwind(self) -> None:
        self.assertTrue(self.stage_a["packages_removed_after_unwind"])

    def test_argv_was_restored_by_the_unwind(self) -> None:
        self.assertTrue(self.stage_a["argv_restored"])

    def test_no_cuda_was_initialized(self) -> None:
        self.assertFalse(self.stage_a["cuda_available"])
        self.assertFalse(self.stage_a["cuda_initialized"])

    def test_the_cuda_terminal_was_intercepted_not_reached(self) -> None:
        self.assertEqual(1, self.stage_a["cuda_terminal_calls"])


class PostImportContinuationTests(unittest.TestCase):
    """Stage B facts: actual product classes over the really imported module."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _ProbeHolder.get()
        cls.stage_b = cls.report["stage_b"]

    def test_the_load_reached_ready(self) -> None:
        self.assertEqual("ready", self.stage_b["state_after_load"])

    def test_exactly_one_synthetic_engine_was_built(self) -> None:
        self.assertEqual(1, self.stage_b["forge_loader_calls"])

    def test_the_lifecycle_kept_the_wrapper(self) -> None:
        self.assertEqual("LoadedStudioSession", self.stage_b["wrapper_type"])

    def test_the_adapter_received_the_inner_session(self) -> None:
        self.assertTrue(self.stage_b["adapter_is_inner"])
        self.assertFalse(self.stage_b["adapter_is_wrapper"])

    def test_identity_was_attached_by_the_real_installer(self) -> None:
        self.assertTrue(self.stage_b["identity_attached"])

    def test_the_real_processing_module_was_imported(self) -> None:
        self.assertTrue(self.stage_b["processing_really_imported"])

    def test_the_engine_was_published_to_real_shared_state(self) -> None:
        self.assertTrue(self.stage_b["shared_sd_model_is_engine"])
        self.assertTrue(self.stage_b["model_data_sd_model_is_engine"])


class ClosureRehearsalTests(unittest.TestCase):
    """The §7 full rehearsal, counted fact by fact."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _ProbeHolder.get()
        cls.reh = cls.report["rehearsal"]

    def test_the_required_counts_hold(self) -> None:
        reh = self.reh
        self.assertEqual(
            {
                "jobs_submitted": 3,
                "jobs_reaching_port": 2,
                "max_concurrent_port_calls": 1,
                "per_job_releases": 2,
                "publications": 2,
                "intermediate_unloads": 0,
                "explicit_unloads": 1,
                "pre_unload_retrievals": 2,
                "post_unload_retrievals": 2,
                "server_starts": 1,
                "server_stops": 1,
                "real_model_opens": 0,
                "external_requests": 0,
            },
            {key: reh[key] for key in (
                "jobs_submitted", "jobs_reaching_port",
                "max_concurrent_port_calls", "per_job_releases",
                "publications", "intermediate_unloads", "explicit_unloads",
                "pre_unload_retrievals", "post_unload_retrievals",
                "server_starts", "server_stops", "real_model_opens",
                "external_requests",
            )},
        )

    def test_both_jobs_completed(self) -> None:
        self.assertEqual("completed", self.reh["job_1_state"])
        self.assertEqual("completed", self.reh["job_2_state"])

    def test_job_two_was_authoritatively_busy(self) -> None:
        self.assertEqual("busy", self.reh["job_2_state_busy"])

    def test_job_three_queued_cancelled_and_never_reached_the_port(self) -> None:
        self.assertTrue(self.reh["job_3_queued"])
        self.assertTrue(self.reh["job_3_cancel_accepted"])
        self.assertEqual(0, self.reh["job_3_port_calls"])

    def test_the_session_was_reused_throughout(self) -> None:
        self.assertTrue(self.reh["session_reused"])

    def test_results_were_real_png_and_durable_across_unload(self) -> None:
        self.assertTrue(self.reh["pre_unload_png"])
        self.assertTrue(self.reh["result_durability"])

    def test_unload_restored_the_shared_model_state(self) -> None:
        self.assertEqual("no_model", self.reh["state_after_unload"])
        self.assertTrue(self.reh["adapter_cleared"])
        self.assertTrue(self.reh["wrapper_closed"])
        # Real Forge idles on a FakeInitialModel, so "restored" is proven by
        # identity against the engine, not by None-ness.
        self.assertFalse(self.reh["shared_sd_model_is_engine_after_unload"])
        self.assertFalse(
            self.reh["model_data_sd_model_is_engine_after_unload"]
        )
        self.assertTrue(self.reh["packages_removed_after_unload"])

    def test_generation_in_no_model_was_refused(self) -> None:
        self.assertTrue(self.reh["no_model_generation_refused"])

    def test_no_cuda_was_initialized_anywhere(self) -> None:
        self.assertFalse(self.reh["cuda_initialized"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_discovered_count_matches_the_declared_constant(self) -> None:
        loaded = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_CLOSURE_TESTS, loaded.countTestCases())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
