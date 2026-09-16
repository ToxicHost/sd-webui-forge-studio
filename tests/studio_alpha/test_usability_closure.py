"""Contracts for the Internal Alpha usability closure.

Three areas, one suite:

* **Native surfaces** -- the floating Model and Results boxes are gone.
  The Canvas Strip carries a native MODEL / SESSION parameter group
  (profile, lifecycle chip, Load/Unload, queue summary with queued
  cancellation) built from existing parameter classes, and completed
  results are appended to Studio's ONE session registry so the Session
  Strip renders them and the existing Canvas open path shows them.
  ``studio-model-controls.js`` is an adapter that binds to that markup
  and creates no surface of its own. No second job state machine and no
  parallel result store: everything keys off the one public job id.

* **Immediate UNLOADING state** -- the Unload click renders UNLOADING
  synchronously (at least one frame before any network round trip),
  disables the conflicting controls, refuses a duplicate request on
  double-click, and reconciles with the server's authoritative state.

* **Reference-safe unload verification** -- ``forge_headless/
  unload_verification.py`` owns the V2 lesson: capture scalars, keep
  weakrefs, drop every verifier strong reference BEFORE the unload, let
  the product finish, and only then sample registry/global state. The
  regression matrix pins both directions: a held reference fails the
  central verdict; the corrected order passes it.

SCOPE: source pins run in-process with no server; the verification
matrix builds the real composition over the seam-suite synthetic world
(no torch, no payload, no CUDA); the complete browser-driven flow is the
milestone's rehearsal evidence.
"""

from __future__ import annotations

import gc
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import weakref
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.live_bindings import LoadConfiguration  # noqa: E402
from forge_headless.memory_report import (  # noqa: E402
    MEMORY_ACCEPTED,
    OWNERSHIP_STATE_INCONSISTENT,
    classify_memory,
)
from forge_headless.unload_verification import (  # noqa: E402
    ObservedUnload,
    ownership_facts,
)

FRONTEND = APP_ROOT / "forge_studio" / "frontend"


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _valid_sample(allocated: int, reserved: int) -> dict:
    return {"status": "ok", "allocated_bytes": allocated,
            "reserved_bytes": reserved}


# ---------------------------------------------------------------------------
# The native Canvas Strip / Session Strip surfaces
# ---------------------------------------------------------------------------

class NativeSurfaceSourceTests(unittest.TestCase):
    """The floating boxes are gone; the native surfaces own the work.

    Rewritten by the native Canvas/Session integration. The previous
    ResultsPanelSourceTests and ModelPanelRescopeTests asserted the
    existence and internals of the two floating panels, which is exactly
    what that milestone removed; every behaviour they protected is
    re-asserted here against the native surfaces instead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _read("studio-model-controls.js")
        cls.html = _read("index.html")

    # -- the boxes are gone ------------------------------------------------

    def test_the_floating_results_panel_is_removed(self) -> None:
        self.assertFalse((FRONTEND / "studio-results-panel.js").exists())
        match = re.search(r"const optionalScripts = \[(.*?)\];", self.html)
        scripts = re.findall(r'"([^"]+)"', match.group(1))
        self.assertNotIn("studio-results-panel.js", scripts)
        self.assertNotIn("studio-results-panel", self.html)

    def test_the_module_creates_no_floating_surface(self) -> None:
        # No panel element, no fixed positioning, no docked box of any
        # kind: the module only binds to markup that already exists.
        for forbidden in ('createElement("aside")', "position: fixed",
                          "studio-model-controls\"", "srp-", "smc-"):
            self.assertNotIn(forbidden, self.module)
        self.assertNotIn("document.body.appendChild", self.module)

    # -- the Canvas Strip owns lifecycle ----------------------------------

    def test_canvas_strip_carries_the_native_lifecycle_group(self) -> None:
        self.assertIn('data-block="session" id="studioSessionBlock"',
                      self.html)
        # paramStudioProfile and studioLoadBtn were required here and are now
        # required ABSENT: the model is the three role dropdowns, and Generate
        # makes that selection resident, so neither a profile nor a load step
        # is part of the owner surface.
        for element_id in ("studioStageChip", "studioUnloadBtn",
                           "studioQueueList", "studioRunningJob",
                           "studioSessionNote", "studioSessionError"):
            with self.subTest(element=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        for retired in ("paramStudioProfile", "studioLoadBtn"):
            with self.subTest(retired=retired):
                self.assertNotIn(f'id="{retired}"', self.html)

    def test_the_group_uses_native_parameter_classes(self) -> None:
        block = self.html[self.html.index('id="studioSessionBlock"'):]
        block = block[:block.index('data-block="model"')]
        # `param-select` is gone from this block with the Profile dropdown --
        # the group holds no select of its own now. The remaining classes still
        # prove it is a native parameter block rather than a bespoke panel.
        for native in ("param-section", "section-header", "section-label",
                       "param-grid", "param-cell",
                       "cn-upload-btn"):
            with self.subTest(css_class=native):
                self.assertIn(native, block)

    def test_the_module_binds_rather_than_builds(self) -> None:
        for element_id in ("studioSessionBlock",
                           "studioStageChip",
                           "studioUnloadBtn", "studioQueueList"):
            with self.subTest(element=element_id):
                self.assertIn(f'getElementById("{element_id}")', self.module)
        # An unbound host adapts nothing rather than inventing a surface.
        self.assertIn("if (!bind()) return;", self.module)

    # -- results flow into the ONE session registry ------------------------

    def test_results_go_to_the_existing_session_registry(self) -> None:
        self.assertIn("S.sessionEntries.unshift(entry)", self.module)
        self.assertIn("window.renderOutputGallery", self.module)
        # No parallel store, no second thumbnail surface.
        self.assertNotIn("resultsGrid", self.module)
        self.assertNotIn("state.results", self.module)

    def test_only_completed_jobs_become_session_entries(self) -> None:
        self.assertIn('if (job.state === "completed") {', self.module)
        self.assertIn(
            "Only completed jobs carry an image. Cancelled and failed jobs",
            self.module,
        )

    def test_result_entries_carry_safe_names_and_opaque_handles(self) -> None:
        self.assertIn(
            'String(jobId).replace("studio-job-", "studio-result-")',
            self.module,
        )
        self.assertIn("`/studio/file?path=${encodeURIComponent(handle)}`",
                      self.module)
        # No backing-path FIELD is ever read or written (prose in the
        # module's own comments is not a path).
        for field in ("output_path", "metadata_path", "result_path",
                      ".path", "image_path"):
            with self.subTest(field=field):
                self.assertNotIn(field, self.module)

    def test_the_public_job_id_is_the_result_linkage(self) -> None:
        self.assertIn("entryId: job.job_id", self.module)
        self.assertIn("the public job id IS the linkage", self.module)

    def test_delivery_is_idempotent(self) -> None:
        self.assertIn("state.delivered.has(job.job_id)", self.module)
        self.assertIn("state.delivered.set(job.job_id, true)", self.module)

    # -- preserved behaviour ------------------------------------------------

    def test_queued_cancel_stays_on_the_canonical_route(self) -> None:
        self.assertIn("/api/jobs/${encodeURIComponent(id)}/cancel",
                      self.module)
        self.assertIn("cancelJob(job.job_id)", self.module)
        # A waiting job is no longer identified by reading `job.state` off a
        # flat list and filtering client-side. The server answers /api/queue
        # with `running` and `queued` already separated, because whichever
        # list a job is in IS the thing that decides whether it runs -- and a
        # client re-deriving that from a state string could disagree with the
        # order the queue will actually execute.
        self.assertIn('jobRow(job, "queued"', self.module)
        self.assertIn("removeJob(job.job_id)", self.module)
        self.assertIn("/api/jobs/${encodeURIComponent(id)}/remove",
                      self.module)

    def test_coordinator_transport_surface_is_unchanged(self) -> None:
        self.assertIn('generate: "/api/generate"', self.module)
        self.assertNotIn("/studio/generate", self.module)
        self.assertIn("lifecycleAvailable: false", self.module)
        self.assertIn("window.StudioModelControls = Object.freeze({",
                      self.module)


class UnloadingStateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = _read("studio-model-controls.js")

    def test_unloading_renders_before_the_network_round_trip(self) -> None:
        body = self.model[self.model.index("const unloadModel"):]
        body = body[:body.index("const cancelJob")] \
            if "const cancelJob" in body else body
        optimistic = body.index("state.optimisticUnloading = true")
        first_render = body.index("render();")
        fetch_call = body.index("jsonFetch(API.unload")
        self.assertLess(optimistic, first_render)
        self.assertLess(first_render, fetch_call)

    def test_double_click_cannot_send_a_duplicate_unload(self) -> None:
        self.assertIn(
            "if (state.optimisticUnloading) return; // no duplicate unload",
            self.model,
        )

    def test_conflicting_controls_disable_while_unloading(self) -> None:
        self.assertIn("state.optimisticUnloading || !(", self.model)

    def test_server_state_stays_authoritative(self) -> None:
        # The optimistic flag clears in finally and the next refresh
        # renders whatever the server reports (NO_MODEL or FAILED).
        #
        # `shownState` became `stage` when the chip stopped reporting a
        # SESSION STATE and started reporting the stage of the owner's
        # picture. The property under test is unchanged: the optimistic
        # frame is rendered from the flag, and the server's answer wins as
        # soon as it arrives.
        self.assertIn("state.optimisticUnloading = false", self.model)
        self.assertIn("const stage = state.optimisticUnloading", self.model)


# ---------------------------------------------------------------------------
# Reference-safe unload verification (the V2 lesson, pinned both ways)
# ---------------------------------------------------------------------------

class _SyntheticWarmSession:
    """One loaded synthetic session over the real composition."""

    def __init__(self) -> None:
        self.neo = B.NeoStub().install()
        self.intercepts = B._Intercepts()
        self.intercepts.__enter__()
        import tempfile

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
        self.lifecycle.ensure_loaded(B._profile("alpha"))

    def close(self) -> None:
        try:
            self.composition.shutdown()
        except BaseException:  # noqa: BLE001
            pass
        self.intercepts.__exit__(None, None, None)
        self.neo.restore()
        self.tmp.cleanup()


class UnloadVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = _SyntheticWarmSession()
        self.addCleanup(self.world.close)

    def test_helper_import_reaches_no_model_stack(self) -> None:
        import importlib

        module = importlib.import_module("forge_headless.unload_verification")
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("import torch", "from backend", "import modules"):
            self.assertNotIn(forbidden, source)

    def test_capture_retains_no_strong_reference(self) -> None:
        observation = ObservedUnload()
        wrapper = self.world.lifecycle.session
        observation.capture(wrapper=wrapper)
        probe = weakref.ref(wrapper)
        del wrapper
        self.world.lifecycle.unload()
        gc.collect()
        # Nothing inside the observation keeps the wrapper alive.
        self.assertIsNone(probe())
        report = observation.finish(timeout_seconds=5.0)
        self.assertTrue(report["owned_weakrefs_dead"])

    def test_held_verifier_reference_fails_the_verdict(self) -> None:
        observation = ObservedUnload()
        wrapper = self.world.lifecycle.session
        observation.capture(wrapper=wrapper)
        held_session = wrapper.session  # the V2 mistake, reproduced
        del wrapper
        observation.ready_for_unload()
        self.world.lifecycle.unload()
        report = observation.finish(timeout_seconds=2.0)
        self.assertFalse(report["owned_weakrefs_dead"])
        verdict = classify_memory(
            job1_post_release=_valid_sample(1_000, 2_000),
            job2_post_release=_valid_sample(1_000, 2_000),
            post_unload=_valid_sample(0, 0),
            warm_tolerance_bytes=1_048_576,
            allocated_ceiling_bytes=11_206_656,
            reserved_ceiling_bytes=26_214_400,
            ownership=ownership_facts(
                finish_report=report,
                global_state_restored=True,
            ),
        )
        self.assertEqual(OWNERSHIP_STATE_INCONSISTENT, verdict["outcome"])
        self.assertIsNotNone(held_session)  # the ref stayed ours to drop

    def test_dropped_references_pass_the_verdict(self) -> None:
        observation = ObservedUnload()
        wrapper = self.world.lifecycle.session
        facts = observation.capture(wrapper=wrapper)
        self.assertEqual("LoadedStudioSession", facts["wrapper_type"])
        del wrapper  # every verifier strong reference dies BEFORE unload
        pre = observation.ready_for_unload()
        # The product still owns the session at this point.
        self.assertTrue(any(pre["weakrefs_alive_pre_unload"].values()))
        self.world.lifecycle.unload()
        report = observation.finish(
            timeout_seconds=10.0,
            registry_reader=lambda: 0,
        )
        self.assertTrue(report["owned_weakrefs_dead"])
        verdict = classify_memory(
            job1_post_release=_valid_sample(1_000, 2_000),
            job2_post_release=_valid_sample(1_000, 2_000),
            post_unload=_valid_sample(0, 0),
            warm_tolerance_bytes=1_048_576,
            allocated_ceiling_bytes=11_206_656,
            reserved_ceiling_bytes=26_214_400,
            ownership=ownership_facts(
                finish_report=report,
                global_state_restored=True,
            ),
        )
        self.assertEqual(MEMORY_ACCEPTED, verdict["outcome"])

    def test_sampling_order_is_pinned(self) -> None:
        observation = ObservedUnload()
        wrapper = self.world.lifecycle.session
        observation.capture(wrapper=wrapper)
        del wrapper
        observation.ready_for_unload()
        self.world.lifecycle.unload()
        report = observation.finish(timeout_seconds=10.0,
                                    registry_reader=lambda: 0)
        self.assertEqual(
            ["captured", "verifier_refs_dropped", "cleanup_settled",
             "registry_sampled"],
            report["order"],
        )

    def test_registry_none_fails_closed(self) -> None:
        observation = ObservedUnload()
        wrapper = self.world.lifecycle.session
        observation.capture(wrapper=wrapper)
        del wrapper
        self.world.lifecycle.unload()
        report = observation.finish(timeout_seconds=10.0,
                                    registry_reader=lambda: None)
        facts = ownership_facts(finish_report=report,
                                global_state_restored=True)
        self.assertFalse(facts["registry_restored"])


# ---------------------------------------------------------------------------
# Startup safety with the Results panel present
# ---------------------------------------------------------------------------

class StartupSafetyTests(unittest.TestCase):
    def test_composition_and_native_surfaces_import_no_model_stack(self) -> None:
        script = r"""
import json, sys
from pathlib import Path
APP_ROOT = Path(sys.argv[1])
sys.path.insert(0, str(APP_ROOT))
import launch_studio
from forge_studio.composition import build_standalone
from forge_studio.model_profiles import ModelProfile
references = {"checkpoint": "synthetic://c", "text_encoder": "synthetic://t",
              "vae": "synthetic://v"}
scratch = Path(sys.argv[2])
composition = build_standalone(
    backend_kind="mock",
    profiles=[ModelProfile(profile_id="alpha", display_name="Alpha",
                            family="qwen-image",
                            payload_references=references)],
    result_root=scratch,
)
shell = (APP_ROOT / "forge_studio" / "frontend"
         / "index.html").read_text(encoding="utf-8")
adapter = (APP_ROOT / "forge_studio" / "frontend"
           / "studio-model-controls.js").read_text(encoding="utf-8")
surfaces = {
    "canvas_strip_group": "studioSessionBlock" in shell,
    "session_strip": "sessionStripScroll" in shell,
    "canvas": "studio-canvas" in shell,
    "adapter_binds": "studioSessionBlock" in adapter,
}
state = composition.model_lifecycle.state()["state"]
composition.shutdown()
forbidden = sorted(
    name for name in sys.modules
    if name == "torch" or name.startswith("torch.")
    or name == "backend" or name.startswith("backend.")
    or name == "modules" or name.startswith("modules.")
    or name == "gradio" or name.startswith("gradio.")
)
print(json.dumps({"state": state, "surfaces": surfaces,
                  "forbidden": forbidden}))
"""
        scratch = APP_ROOT / "outputs" / f"uc-safety-{os.getpid()}"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(APP_ROOT / "venv" / "Scripts" / "python.exe"),
                 "-I", "-S", "-B", "-c", script, str(APP_ROOT), str(scratch)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=str(APP_ROOT),
            )
            self.assertEqual(0, completed.returncode, completed.stderr[-800:])
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual("no_model", report["state"])
        self.assertEqual(
            {"canvas_strip_group": True, "session_strip": True,
             "canvas": True, "adapter_binds": True},
            report["surfaces"],
        )
        self.assertEqual([], report["forbidden"])


if __name__ == "__main__":
    unittest.main()
