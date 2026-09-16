"""AR8.13: the model panel must follow the selected model, not the booted one.

`loadSelectedModelComponents` returned true for every LIFECYCLE host before it
reached the Text Encoder restore, the VAE restore or the architecture rules --
and every Studio install is a lifecycle host. So changing the Checkpoint
dropdown changed the dropdown and nothing else.

Measured in a live page before the fix, on this install's own catalogue:

```text
pick a Cosmos checkpoint, then pick the SDXL one
  State._currentModelArch  stayed "cosmos"
  Clip Skip                stayed HIDDEN on a model that has CLIP
  Text Encoder row         stayed VISIBLE with the Cosmos encoder selected
```

and the mirror case leaves Clip Skip visible and inert on a model with no CLIP
at all. Per-model component memory was never written on a change either.

The early return was placed for the LOAD, and the load is genuinely
lifecycle-exclusive. What it also sat in front of was the PANEL, which loads
nothing. That is the same shape as the below-the-return sweep in
`test_ng3_below_the_return.py`, one function further out, which is why the
guards here are positional rather than behavioural: nothing about the panel
work is wrong, and nothing about it was reachable.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

EXPECTED_AR813_TESTS = 13

OPENER = 'async function loadSelectedModelComponents(reason = "model-change") {'


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


APP_JS = _strip_js_comments((FRONTEND / "app.js").read_text(encoding="utf-8"))


def _body() -> str:
    """`loadSelectedModelComponents` from its signature to its closing brace.

    Counting starts at the function body's own brace. Started any earlier it
    would close on a default parameter, which is how a sibling guard in this
    suite silently reduced a whole module to a one-line body.
    """

    start = APP_JS.index(OPENER)
    depth = 0
    for index in range(start + len(OPENER) - 1, len(APP_JS)):
        char = APP_JS[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return APP_JS[start:index + 1]
    raise AssertionError("loadSelectedModelComponents never closes")


BODY = _body()


def _lifecycle_return() -> int:
    """Where the function gives up on a lifecycle host.

    Found by the RETURN, not by a line number or a comment, so moving the
    reasoning around it cannot make this guard stop guarding.
    """

    match = re.search(
        r"if \(onLifecycle\) \{\s*return true;\s*\}", BODY)
    if match is None:
        raise AssertionError(
            "the lifecycle short-circuit is no longer recognisable; if it was "
            "renamed, rename it here too rather than deleting this guard")
    return match.start()


class ThePanelRunsOnEveryHostTests(unittest.TestCase):
    """Each of these is a control the owner can see answering for the wrong
    model. They are checked one at a time so a failure names which one."""

    PANEL_WORK = (
        ("the Text Encoder restore", "restoreTextEncoderForModel("),
        ("the VAE restore", "restoreVAEForModel("),
        ("the architecture record", "State._currentModelArch ="),
        ("the architecture rules", "_applyArchRules("),
        ("per-model component memory", "rememberExternalTE("),
    )

    def test_each_piece_of_panel_work_precedes_the_lifecycle_return(self):
        boundary = _lifecycle_return()
        for name, needle in self.PANEL_WORK:
            with self.subTest(work=name):
                self.assertIn(needle, BODY, f"{name} is gone from the function")
                self.assertLess(
                    BODY.index(needle), boundary,
                    f"{name} sits below the lifecycle return, so it never runs "
                    "on any Studio install",
                )

    def test_the_lifecycle_return_still_exists(self):
        """It is correct and must stay: there is no explicit load on a
        lifecycle host. Only its POSITION was wrong."""

        self.assertGreater(_lifecycle_return(), 0)

    def test_the_legacy_load_stays_below_it(self):
        """The one thing that genuinely is lifecycle-exclusive. If this ever
        rose above the return, every model change would POST a load."""

        boundary = _lifecycle_return()
        for needle in ('"/studio/load_model"', "const loadBody = {"):
            with self.subTest(needle=needle):
                self.assertGreater(BODY.index(needle), boundary)


class TheGenerationQueueGuardTests(unittest.TestCase):
    """`forge_model_reload()` would destroy `shared.sd_model` under
    `process_images()`. That is what the guard is for -- and a lifecycle host
    never calls it, so queueing there only leaves the panel describing a model
    the owner has already moved off."""

    def test_the_queue_guard_only_applies_where_something_reloads(self):
        match = re.search(
            r"if \(!onLifecycle && State\.generating", BODY)
        self.assertIsNotNone(
            match,
            "the in-flight queue guard no longer excludes lifecycle hosts, so "
            "a model change during a generation silently does nothing",
        )

    def test_the_guard_still_defers_on_a_legacy_host(self):
        self.assertIn("State._pendingModelSwitch = title;", BODY)
        self.assertIn('reason !== "post-generation"', BODY)

    def test_the_guard_precedes_the_panel_work(self):
        """A deferred change must not half-apply: on the host where the load is
        queued, the panel is queued with it."""

        guard = BODY.index("if (!onLifecycle && State.generating")
        self.assertLess(guard, BODY.index("restoreTextEncoderForModel("))


class TheBootRaceFixSurvivesTests(unittest.TestCase):
    """AR8.3 put the lifecycle wait at the top of this function precisely
    because it is the single funnel. Restructuring the body must not move it."""

    def test_the_wait_is_still_the_first_statement(self):
        head = BODY[:BODY.index("const modelSelect")]
        self.assertIn("await _awaitLifecycleAnswer();", head)

    def test_the_wait_precedes_the_host_question(self):
        self.assertLess(
            BODY.index("await _awaitLifecycleAnswer()"),
            BODY.index("const onLifecycle"),
            "asking which host this is before waiting for the answer is the "
            "boot race AR8.3 fixed",
        )


class TheArchRulesAreStillWhatTheyWereTests(unittest.TestCase):
    """The fix makes the rules REACHABLE. It must not change which they are.

    Clip Skip stops at the Nth-from-last CLIP layer. FLUX and Cosmos-Predict2
    condition from an LLM text encoder and have no CLIP for it to count, so
    hiding it there is a control that reaches nothing being removed -- not a
    capability being taken away.
    """

    def test_the_hidden_set_is_unchanged(self):
        block = APP_JS[APP_JS.index("const ARCH_UI_RULES = {"):]
        block = block[:block.index("};") + 2]
        for arch in ("flux1", "flux2", "cosmos"):
            with self.subTest(arch=arch):
                self.assertRegex(
                    block, rf'{arch}:\s*\{{ hide: \["paramClipSkip"\]')

    def test_an_unrecognised_architecture_fails_open(self):
        rules = APP_JS[APP_JS.index("function _applyArchRules("):]
        rules = rules[:rules.index("\n}\n") + 3]
        self.assertRegex(
            rules, r"const rules = ARCH_UI_RULES\[arch\];\s*\n\s*if \(!rules\) return;",
            "an architecture with no entry must hide nothing",
        )

    def test_the_escape_hatch_still_disables_the_whole_mechanism(self):
        rules = APP_JS[APP_JS.index("function _applyArchRules("):]
        rules = rules[:rules.index("\n}\n") + 3]
        self.assertIn("if (_archShowAll()) return;", rules)

    def test_clearing_happens_before_reapplying(self):
        """Otherwise a switch away from a hiding architecture leaves the class
        behind -- which is the symptom this whole record is about, arriving by
        a second route."""

        rules = APP_JS[APP_JS.index("function _applyArchRules("):]
        rules = rules[:rules.index("\n}\n") + 3]
        self.assertLess(
            rules.index('.arch-hidden"'), rules.index("ARCH_UI_RULES[arch]"))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_AR813_TESTS, found)


if __name__ == "__main__":
    unittest.main()
