"""The page must render progress for the job the SERVER says is running.

The last hop of Live Preview, and the one no server test could reach.

`handleProgress` opened with:

```js
if (!State.generating && !(window.Live && Live.generating)) return;
```

`State.generating = true` is set at app.js:2687, in the LEGACY generate path.
`doGenerate` takes the lifecycle branch above it and RETURNS from there, so on
a queue host the flag is never set and that guard rejected every progress
message the product can produce. It killed the live preview and the progress
bar together.

It is also why both still work in the extension build: there is no lifecycle
there, the legacy path runs, and the flag gets set.

WHY THE FLAG IS NOT SIMPLY SET IN THE LIFECYCLE BRANCH

Because `doGenerate` itself returns early on `State.generating` (app.js:2661
and :2682), as do a dozen other controls. Latching it would forbid queueing a
second job -- breaking the feature the queue exists to provide. On a queue
host the right question is not "did this page start a job" but "is a job
running", which the message itself reports.

PROVEN LIVE, in a browser, before the fix was loaded: with the guard forced
open on otherwise-unchanged code, one generation produced

```text
canvasPreview.src   2523 chars, data:image/...
wrap display        visible
_previewShown       true          _lastPreviewId 7
progress bar        100%
```

so the guard was the entire cause of both symptoms.

SCOPE: MINIMAL_RUNTIME_SCOPE. No browser and no server; app.js is read as
text, which is the only place this can be asserted.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_GUARD_TESTS = 26

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)


def _handle_progress() -> str:
    """`handleProgress` from its declaration to the preview block."""

    start = APP_JS.find("function handleProgress(data) {")
    assert start != -1, "handleProgress was renamed or removed"
    end = APP_JS.find("var liveGuard", start)
    assert end != -1, "the preview block moved out of handleProgress"
    return APP_JS[start:end]


def _code_only(source: str) -> str:
    """`//` comments stripped, because this guard documents itself by name."""

    return "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())


class GuardTests(unittest.TestCase):
    def test_a_running_lifecycle_job_passes_the_guard(self) -> None:
        """The fix. Without it every message from a queued job is dropped."""

        self.assertIn("lifecycleActive", _code_only(_handle_progress()))

    def test_the_guard_reads_the_running_state_off_the_message(self) -> None:
        code = _code_only(_handle_progress())
        self.assertRegex(code, r'data\.state\s*===\s*"running"')

    def test_it_requires_a_job_id_not_merely_a_state(self) -> None:
        """An idle socket message must not open the guard: the placeholder
        snapshot carries no task_id, and rendering progress for nothing would
        leave the bar and the preview from the previous job on screen."""

        self.assertIn("data.task_id", _code_only(_handle_progress()))

    def test_the_legacy_and_live_painting_paths_still_pass(self) -> None:
        """The discriminating half. Replacing the guard rather than widening
        it would break the extension build and Live Painting, neither of
        which sets a lifecycle task_id."""

        code = _code_only(_handle_progress())
        self.assertIn("State.generating", code)
        self.assertIn("Live.generating", code)

    def test_the_guard_still_rejects_something(self) -> None:
        """Deleting the guard entirely would satisfy every test above. It
        must still return early -- an idle message has no task_id."""

        self.assertRegex(_code_only(_handle_progress()), r"\breturn;")


class GeneratingFlagTests(unittest.TestCase):
    def test_the_lifecycle_branch_does_not_latch_the_flag(self) -> None:
        """Setting it there would be the obvious fix and the wrong one:
        `doGenerate` returns early on this flag, so latching it forbids
        queueing a second job."""

        start = APP_JS.find("const jobParams = {")
        end = APP_JS.find("lifecycle.submitGenerate(jobParams)", start)
        self.assertNotIn("State.generating = true", _code_only(APP_JS[start:end]))

    def test_the_flag_still_guards_re_entry(self) -> None:
        """It keeps its real job: one page-initiated generation at a time."""

        self.assertRegex(APP_JS, r"if \(State\.generating\) return;")


CONTROLS_JS = (APP_ROOT / "forge_studio" / "frontend"
               / "studio-model-controls.js").read_text(encoding="utf-8")


class GenerationPresentationTests(unittest.TestCase):
    """The SAME early return, a second symptom. AR3.5.

    The guard above closed the branch that discarded progress MESSAGES. This
    closes the branch that discarded the progress PRESENTATION: the status
    word, the button label, the bar reset and the inter-pass elapsed timer all
    sat below the lifecycle `return` too, so on every Studio install the owner
    was never told a job had started or ended.

    Measured on the real GPU, clicking the real button:

        t=46.81  btn "5 / 5"  bar 100%  chip "Auto Detail"  status "Ready"
                 ... nothing changes for 41 SECONDS ...
        t=87.81  btn "5 / 5"  bar 100%  chip "Idle"         status "Ready"

    Three indicators said finished while Auto Detail ran. `statusText` never
    left "Ready" across the whole 102-second job.
    """

    def test_the_lifecycle_branch_starts_the_presentation(self) -> None:
        """Catches the whole defect returning: the setup must appear BEFORE
        the lifecycle `return`, not merely somewhere in the file."""

        submit_at = APP_JS.index("await lifecycle.submitGenerate(jobParams)")
        setup_at = APP_JS.index("_beginGenerationPresentation();", submit_at)
        return_at = APP_JS.index("\n    return;", submit_at)
        self.assertLess(setup_at, return_at,
                        "the presentation setup is unreachable again")

    def test_the_elapsed_timer_is_inside_the_setup(self) -> None:
        """The sharpest part of the loss. Its own comment says it exists to
        replace a stale step count during a gap of more than two seconds --
        which is exactly the 41-second freeze -- and it never ran."""

        start = APP_JS.index("function _beginGenerationPresentation()")
        end = APP_JS.index("function _endGenerationPresentation()")
        body = APP_JS[start:end]
        self.assertIn("State._genTimerInterval = setInterval(", body)
        self.assertIn("_lastProgressTime", body)

    def test_a_new_job_does_not_inherit_the_previous_bar(self) -> None:
        """Measured: a job began with the previous job's "7 / 7" and a bar
        still at 100%, until its first frame arrived."""

        start = APP_JS.index("function _beginGenerationPresentation()")
        end = APP_JS.index("function _endGenerationPresentation()")
        body = APP_JS[start:end]
        for reset in ("State._peakProgress = 0;", "State._lastTotalSteps = 0;"):
            with self.subTest(reset=reset):
                self.assertIn(reset, body)

    def test_the_status_word_is_reachable_on_a_lifecycle_host(self) -> None:
        start = APP_JS.index("function _beginGenerationPresentation()")
        end = APP_JS.index("function _endGenerationPresentation()")
        self.assertIn('StatusBar.setStatus("generating")', APP_JS[start:end])

    def test_the_teardown_restores_the_button_and_the_status(self) -> None:
        start = APP_JS.index("function _endGenerationPresentation()")
        body = APP_JS[start:start + 1400]
        self.assertIn('StatusBar.setStatus("ready")', body)
        self.assertIn("actions.generate", body)
        self.assertIn("clearInterval(State._genTimerInterval)", body)

    def test_the_presentation_is_only_dressed_on_a_successful_submission(self) -> None:
        """A refused submission leaves nothing to watch. Dressing the page as
        busy would be a lie with no job behind it."""

        submit_at = APP_JS.index("await lifecycle.submitGenerate(jobParams)")
        setup_at = APP_JS.index("_beginGenerationPresentation();", submit_at)
        catch_at = APP_JS.index("} catch (error) {", submit_at)
        self.assertLess(setup_at, catch_at,
                        "the setup must sit in the try, before the catch")

    def test_the_terminal_signal_comes_from_the_queue_view(self) -> None:
        """The same source that feeds the stage chip -- which was the ONE
        indicator telling the truth during the freeze. Driving the words from
        anywhere else lets them disagree with the chip."""

        self.assertIn("studio:generation-running-changed", CONTROLS_JS)
        self.assertIn("state.queue && state.queue.running", CONTROLS_JS)
        self.assertIn("studio:generation-running-changed", APP_JS)

    def test_the_running_signal_is_announced_on_change_only(self) -> None:
        """At 1500 ms a per-poll event would fire about forty times a minute
        for no transition."""

        start = CONTROLS_JS.index("function announceRunning()")
        body = CONTROLS_JS[start:start + 600]
        self.assertIn("if (running === state.announced.running) return;", body)

    def test_the_running_flag_starts_unknown_rather_than_false(self) -> None:
        """A page that loads mid-job must still announce on its first poll."""

        self.assertIn("running: null", CONTROLS_JS)

    def test_the_teardown_yields_to_the_non_lifecycle_path(self) -> None:
        """`doGenerate`'s own tail owns the legacy path. Both running would
        clear the timer twice and fight over the label."""

        start = APP_JS.index('document.addEventListener("studio:generation-running-changed"')
        body = APP_JS[start:start + 420]
        self.assertIn("if (State.generating) return;", body)


class ResultReplacesTheLivePreviewTests(unittest.TestCase):
    """The FOURTH thing found below the lifecycle `return`. AR4.8.

    THE DEFECT THIS CLOSES

    `#canvasPreview` is ONE element shared by two jobs: the progress thumbnail
    during sampling, and the finished picture afterwards. The legacy generate
    path swapped it over --

        // Show result in viewport preview (replaces live preview)
        _showResultPreview(0);

    -- and that call sits below the lifecycle `return`, so on every real
    install it never ran. The owner was left looking at the last mid-diffusion
    frame: a low-resolution latent decode, washed out and noisy, presented as
    their finished picture. Their words: "the finished preview looks like
    shiiiit".

    THE PATTERN

    That one early return has now cost four separate things:

        AR3.5   the progress presentation -- status, button, elapsed timer
        (this file's original guard) the progress MESSAGES
        AR4.7   the inpaint payload, which made four controls dead
        AR4.8   the result swap, and the tab notification

    Which is why the fix is bound to an EVENT the lifecycle path actually
    emits, rather than moved to another line that a future branch could return
    above.

    Measured live, watching `#canvasPreview.src` across one generation:

        none -> data-url (live latent preview) -> server result url

    Before, it stopped at the middle one.
    """

    def test_the_result_swap_is_bound_to_delivery(self) -> None:
        """Not to a line in `doGenerate`, which is how it was lost."""

        at = APP_JS.index('document.addEventListener("studio:result-delivered"')
        body = APP_JS[at:at + 1600]
        self.assertIn("_showResultPreview(0)", body)

    def test_the_delivery_event_is_actually_emitted(self) -> None:
        """A listener for an event nobody fires is the same defect wearing a
        different hat."""

        controls = (APP_ROOT / "forge_studio" / "frontend"
                    / "studio-model-controls.js").read_text(encoding="utf-8")
        self.assertIn('new CustomEvent("studio:result-delivered"', controls)

    def test_it_is_announced_only_once_the_result_is_on_screen(self) -> None:
        """Announced after the gallery renders, not before -- anywhere earlier
        promises a picture the owner cannot look at yet."""

        controls = (APP_ROOT / "forge_studio" / "frontend"
                    / "studio-model-controls.js").read_text(encoding="utf-8")
        render_at = controls.index("window.renderOutputGallery();")
        announce_at = controls.index('new CustomEvent("studio:result-delivered"')
        self.assertLess(render_at, announce_at)

    def test_a_failed_swap_cannot_take_the_page_down(self) -> None:
        at = APP_JS.index('document.addEventListener("studio:result-delivered"')
        body = APP_JS[at:at + 1600]
        self.assertIn("try {", body)
        self.assertIn("catch", body)

    def test_the_result_is_draggable_with_a_real_name(self) -> None:
        """A bare `<img>` drag hands over a browser-synthesised copy named
        after the URL path. `gallery.js` solved this already; the same three
        formats are set here for the same reasons."""

        self.assertIn("function _makeResultDraggable", APP_JS)
        at = APP_JS.index("function _makeResultDraggable")
        body = APP_JS[at:at + 2200]
        for fmt in ("DownloadURL", "text/uri-list", "text/x-moz-url"):
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, body)

    def test_the_drag_type_comes_from_the_server_not_a_guess(self) -> None:
        """The delivery handle carries its own extension. Assuming PNG is how
        a JPEG result gets handed over mislabelled."""

        at = APP_JS.index("function _makeResultDraggable")
        body = APP_JS[at:at + 2200]
        self.assertIn("entry.url", body)
        self.assertIn("image/jpeg", body)

    def test_the_drag_is_attached_wherever_the_preview_is_shown(self) -> None:
        at = APP_JS.index("function _showResultPreview")
        body = APP_JS[at:at + 900]
        self.assertIn("_makeResultDraggable(img, idx)", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_GUARD_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
