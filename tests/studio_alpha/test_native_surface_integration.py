"""Negative and ownership contracts for the native Studio surfaces.

The floating Model and Results boxes were rejected as owner-facing
surfaces. This suite is deliberately adversarial about their absence and
about the ONE-owner rule that replaced them:

```text
generation/session parameters -> Canvas Strip
lifecycle state               -> product lifecycle, rendered in the strip
job state                     -> coordinator records
queued cancellation           -> coordinator API, invoked from the strip
result collection             -> the one session registry, drawn by the
                                 Session Strip
active image                  -> Canvas
```

Every test here fails if a floating box returns, if a second result store
or lifecycle state machine appears, or if a result reaches the DOM by any
route other than an opaque handle under a safe generated name.

SCOPE: static source and shell inspection. No server, no browser, no
torch, no payload.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
SHELL = FRONTEND / "index.html"
ADAPTER = FRONTEND / "studio-model-controls.js"
APP_JS = FRONTEND / "app.js"

#: Every identifier the removed floating surfaces used.
FLOATING_MARKERS = (
    "studio-results-panel",
    "studio-model-controls\"",     # the old panel element id
    "srp-card", "srp-preview", "srp-results", "srp-filters",
    "smc-jobs", "smc-results", "smc-result", "smc-state", "smc-body",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = {k: v or "" for k, v in attrs}
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if block := attributes.get("data-block"):
            self.blocks.append(block)


class FloatingSurfacesAreGoneTests(unittest.TestCase):
    def test_no_frontend_file_mentions_a_floating_surface(self) -> None:
        offenders: list[str] = []
        for path in sorted(FRONTEND.rglob("*")):
            if not path.is_file() or path.suffix not in (".js", ".html", ".css"):
                continue
            text = _read(path)
            for marker in FLOATING_MARKERS:
                if marker in text:
                    offenders.append(f"{path.name}: {marker}")
        self.assertEqual([], offenders)

    def test_the_results_panel_module_no_longer_exists(self) -> None:
        self.assertFalse((FRONTEND / "studio-results-panel.js").exists())

    def test_the_adapter_appends_nothing_to_the_body(self) -> None:
        source = _read(ADAPTER)
        tree_text = source
        self.assertNotIn("document.body.appendChild", tree_text)
        self.assertNotIn("position: fixed", tree_text)
        # `aside` was the floating panel's element; nothing creates one.
        self.assertNotIn('createElement("aside")', tree_text)
        self.assertNotIn('el("aside"', tree_text)

    def test_no_module_declares_an_independent_panel_id(self) -> None:
        parser = _Ids()
        parser.feed(_read(SHELL))
        for gone in ("studio-model-controls", "studio-results-panel"):
            self.assertNotIn(gone, parser.ids)


class CanvasStripOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shell = _read(SHELL)
        cls.adapter = _read(ADAPTER)
        cls.parser = _Ids()
        cls.parser.feed(cls.shell)

    def test_the_lifecycle_group_is_a_canvas_strip_parameter_block(self) -> None:
        self.assertIn("session", self.parser.blocks)
        self.assertIn("studioSessionBlock", self.parser.ids)
        # It sits with the other parameter blocks, not in a layer above.
        self.assertIn("model", self.parser.blocks)

    def test_the_group_exposes_every_required_control(self) -> None:
        # `paramStudioProfile` and `studioLoadBtn` were required here. They are
        # now required to be ABSENT -- see the test below. The model is the
        # three role dropdowns, and Generate makes that selection resident.
        #
        # The group is now "Jobs" rather than "Model / Session lifecycle
        # (internal alpha)". `studioLifecycleChip` became `studioStageChip`
        # because it stopped reporting a SESSION STATE and started reporting
        # the stage of the owner's picture; `studioQueueSummary` and its
        # disclosure became a running row, an ordered waiting list and a
        # bounded Recent list, because "1 active - 0 queued" is a count, not
        # an answer to what is happening.
        for element_id in ("studioStageChip", "studioUnloadBtn",
                           "studioRunningJob", "studioQueuedSection",
                           "studioQueueList", "studioRecentList",
                           "studioSessionNote", "studioSessionError"):
            with self.subTest(element=element_id):
                self.assertIn(element_id, self.parser.ids)

    def test_no_profile_or_mandatory_load_control_exists(self) -> None:
        """The owner-facing half of the profile removal.

        Asserted against the shipped markup rather than a screenshot, and
        stated as absence so a later edit cannot quietly reinstate either
        control. The three role dropdowns remain, because they ARE the model.
        """

        for retired in ("paramStudioProfile", "studioLoadBtn"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, self.parser.ids)
        self.assertNotIn("select a profile", self.shell)
        for role in ("paramModel", "paramTextEncoder", "paramVAE"):
            with self.subTest(role=role):
                self.assertIn(role, self.parser.ids)

    def test_queue_details_use_an_existing_disclosure_pattern(self) -> None:
        # The disclosure now wraps RECENT rather than the whole queue. What
        # is running and what is waiting are the two things an owner needs
        # without a click; the finished list is the one worth folding away.
        self.assertIn('<details class="studio-recent"', self.shell)
        self.assertIn("<summary>", self.shell)

    def test_lifecycle_state_comes_from_the_product_only(self) -> None:
        # One source of lifecycle truth: the state endpoint. The adapter
        # never computes a state of its own beyond the optimistic
        # UNLOADING frame, which is explicitly reconciled.
        self.assertIn('state: "/api/model/state"', self.adapter)
        self.assertIn("state.model = modelState", self.adapter)
        self.assertIn("state.optimisticUnloading = false", self.adapter)

    def test_job_state_comes_from_coordinator_records_only(self) -> None:
        self.assertIn('jobs: "/api/jobs"', self.adapter)
        self.assertIn("state.jobs = jobs.jobs || []", self.adapter)
        # The queue is ASSIGNED WHOLE from the server, never assembled here.
        # The old spelling of this rule was `assertNotIn("state.queue =")`,
        # from when holding a queue at all would have meant computing one.
        # There is a queue now and the coordinator owns it -- what must stay
        # true is that the adapter does not decide the ORDER, because the
        # displayed order being the execution order depends on exactly one
        # list existing.
        self.assertIn("state.queue = await jsonFetch(API.queue)", self.adapter)
        for local_ordering in (".sort(", "state.queue.push", "state.queue.splice"):
            with self.subTest(computed=local_ordering):
                self.assertNotIn(local_ordering, self.adapter)
        self.assertNotIn("nextJobId", self.adapter)

    def test_queued_cancellation_is_invoked_from_the_strip(self) -> None:
        self.assertIn("cancelJob(job.job_id)", self.adapter)
        self.assertIn("/api/jobs/${encodeURIComponent(id)}/cancel",
                      self.adapter)


class SessionStripAndCanvasOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = _read(ADAPTER)
        cls.app_js = _read(APP_JS)
        cls.shell = _read(SHELL)

    def test_results_land_in_the_one_session_registry(self) -> None:
        self.assertIn("S.sessionEntries.unshift(entry)", self.adapter)
        # The registry the rest of Studio already renders.
        self.assertIn("State.sessionEntries", self.app_js)

    def test_no_parallel_result_store_exists(self) -> None:
        for forbidden in ("state.results", "resultsGrid", "resultStore",
                          "this.results"):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, self.adapter)

    def test_the_session_strip_renders_from_that_registry(self) -> None:
        self.assertIn("sessionStripScroll", self.shell)
        strip = self.app_js[self.app_js.index("const SessionStrip = {"):]
        strip = strip[:strip.index("isCollapsed()")]
        self.assertIn("State.sessionEntries.map", strip)

    def test_the_adapter_triggers_the_existing_renderer(self) -> None:
        self.assertIn("window.renderOutputGallery", self.adapter)

    def test_canvas_opening_uses_the_existing_path(self) -> None:
        # Double-click on a session thumb opens Canvas through Gallery's
        # ephemeral view; the adapter adds no viewer of its own.
        self.assertIn("_openCanvasOutput", self.app_js)
        self.assertIn("StudioGallery.openEphemeral", self.app_js)
        for invented in ("openEphemeral", "lightbox", "viewer"):
            with self.subTest(symbol=invented):
                self.assertNotIn(invented, self.adapter)

    def test_only_completed_jobs_can_produce_an_entry(self) -> None:
        deliver = self.adapter[self.adapter.index("async function deliverResult"):]
        deliver = deliver[:deliver.index("// ---- polling")]
        self.assertIn("if (!result.image_handle) return false;", deliver)
        poll = self.adapter[self.adapter.index("const jobs = await jsonFetch"):]
        poll = poll[:poll.index("render();")]
        self.assertIn('job.state === "completed"', poll)
        for terminal in ('job.state === "cancelled"', 'job.state === "failed"'):
            with self.subTest(state=terminal):
                self.assertNotIn(f"{terminal}) {{\n            try {{ await deliverResult",
                                 poll)

    def test_entries_carry_no_filesystem_detail(self) -> None:
        deliver = self.adapter[self.adapter.index("const entry = {"):]
        deliver = deliver[:deliver.index("};")]
        self.assertIn('filename: name', deliver)
        self.assertIn('floatPath: ""', deliver)
        self.assertIn('maskPath: ""', deliver)
        for leak in ("output_path", "metadata_path", "result.path"):
            with self.subTest(field=leak):
                self.assertNotIn(leak, deliver)


class OwnershipMapTests(unittest.TestCase):
    """One authoritative owner per concept, asserted as a whole."""

    def test_the_map_holds(self) -> None:
        adapter = _read(ADAPTER)
        shell = _read(SHELL)
        app_js = _read(APP_JS)
        owners = {
            "generation_parameters_in_canvas_strip":
                'data-block="session"' in shell and 'data-block="model"' in shell,
            "lifecycle_rendered_in_canvas_strip":
                "studioStageChip" in shell and "refs.chip" in adapter,
            "job_state_from_coordinator":
                'jobs: "/api/jobs"' in adapter and 'queue: "/api/queue"' in adapter,
            "cancel_via_coordinator_api":
                "/cancel" in adapter,
            "results_in_session_registry":
                "S.sessionEntries.unshift(entry)" in adapter,
            "canvas_owns_active_image":
                "StudioGallery.openEphemeral" in app_js,
            "no_second_lifecycle_machine":
                "TRANSITIONS" not in adapter,
            "no_second_result_store":
                "state.results" not in adapter,
        }
        self.assertEqual(
            {name: True for name in owners}, owners,
            f"ownership map violated: {json.dumps(owners)}",
        )


class PreservedBehaviourTests(unittest.TestCase):
    """The accepted behaviour the integration must not have disturbed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = _read(ADAPTER)
        cls.app_js = _read(APP_JS)

    def test_the_coordinator_transport_is_intact(self) -> None:
        self.assertIn('generate: "/api/generate"', self.adapter)
        self.assertNotIn("/studio/generate", self.adapter)
        self.assertIn("window.StudioModelControls = Object.freeze({",
                      self.adapter)
        body = self.app_js[self.app_js.index("async function doGenerate("):]
        branch = body[:body.index("if (State.generating || State._preflighting)")]
        self.assertIn("lifecycle.submitGenerate(jobParams)", branch)
        self.assertNotIn("/studio/generate", branch)

    def test_the_immediate_unloading_frame_is_intact(self) -> None:
        body = self.adapter[self.adapter.index("const unloadModel"):]
        body = body[:body.index("// The coordinator transport")]
        optimistic = body.index("state.optimisticUnloading = true")
        first_render = body.index("render();")
        fetch_call = body.index("jsonFetch(API.unload")
        self.assertLess(optimistic, first_render)
        self.assertLess(first_render, fetch_call)
        self.assertIn(
            "if (state.optimisticUnloading) return; // no duplicate unload",
            self.adapter,
        )

    def test_the_terminal_vram_seam_is_untouched(self) -> None:
        # Presentation-only milestone: the release seam and its ordering
        # comment must be byte-present exactly as the closure left them.
        loader = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from .terminal_release import release_terminal_cache",
                      loader)
        self.assertIn('report["terminal_release"] = release_terminal_cache()',
                      loader)
        tree = ast.parse(
            (APP_ROOT / "forge_headless" / "terminal_release.py")
            .read_text(encoding="utf-8")
        )
        clears = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("empty_cache", "soft_empty_cache")
        ]
        self.assertEqual([], clears)

    def test_no_per_job_cache_clear_was_introduced(self) -> None:
        cleanup = (APP_ROOT / "forge_headless" / "failure_cleanup.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(cleanup)
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for forbidden in ("empty_cache", "ipc_collect", "synchronize"):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
