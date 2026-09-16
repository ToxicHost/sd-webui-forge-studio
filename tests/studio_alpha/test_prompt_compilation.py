"""The prompt that is sent is the prompt the owner composed. AR6.7.

THE DEFECT THIS CLOSES

Two owner-facing features compiled into a prompt that was never sent, because
both lived below the lifecycle `return` in the legacy collector:

    app.js:3096   LoraStack.compilePrompt(...)   the LoRA stack
    app.js:3080   const _clean = ...             "Tidy prompt on generate"

while the live branch sent the raw textarea value:

    app.js:2934   positive_prompt: document.getElementById("paramPrompt")?.value

THE EIGHTH THING FOUND BELOW THAT RETURN, after the progress messages, the
progress presentation, the inpaint payload, the result swap, the output format,
the batch fields, the metadata toggle and the Gallery notification.

It is also the most owner-visible. The LoRA stack is a headline feature: an
owner browses LoRAs, adds them, sets weights, toggles trigger words, watches a
live preview of the compiled prompt -- and none of it reached the engine on any
real install. `compileTags()` and `compilePrompt()` were correct the whole time
and had exactly one consumer, a preview label at `lora-stack.js:360`.

"Tidy prompt on generate" (`index.html:1541`) promises to collapse "duplicate
commas, extra spaces, and empty attention groups in the copy sent to
generation". It did none of that, and its own description is what makes the
promise checkable.

WHAT THESE TESTS REFUSE TO ACCEPT

**A definition that drifts back below the return.** `_preparedPrompt` and
`_clean` are asserted to sit ABOVE the branch, which is what makes one
definition serve both paths. Position, not presence -- the same way AR4.7,
AR4.9 and AR5.2 assert it, because presence is what was never in doubt.

**A prompt that is rewritten when the owner did not ask.** `compilePrompt`
returns its input unchanged with an empty stack, and `_clean` is a no-op when
the toggle is off. An owner with neither sends exactly what they typed.

Review: `Evidence/source-review/AR6.7-prompt-compilation.md`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_PROMPT_TESTS = 18

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
LORA_JS = (FRONTEND / "lora-stack.js").read_text(encoding="utf-8")
SHELL = (FRONTEND / "index.html").read_text(encoding="utf-8")


def line_of(needle: str) -> int:
    return APP_JS[:APP_JS.index(needle)].count(chr(10)) + 1


def branch_bounds() -> tuple[int, int]:
    opens = APP_JS.index("if (lifecycle && lifecycle.lifecycleAvailable())")
    closes = APP_JS.index("    return;" + chr(10) + "  }" + chr(10), opens)
    return (APP_JS[:opens].count(chr(10)) + 1,
            APP_JS[:closes].count(chr(10)) + 1)


class ThePreparedPromptReachesTheLivePathTests(unittest.TestCase):
    """Asserted by POSITION. Presence was never the problem."""

    def test_the_helper_is_defined_above_the_branch(self) -> None:
        opens, _closes = branch_bounds()
        self.assertLess(line_of("const _preparedPrompt = () =>"), opens,
                        "the helper fell into one path's scope")
        self.assertLess(line_of("const _clean = (s) =>"), opens)

    def test_the_live_branch_sends_the_prepared_prompt(self) -> None:
        opens, closes = branch_bounds()
        sent = line_of("positive_prompt: _preparedPrompt(),")
        self.assertLess(opens, sent)
        self.assertLess(sent, closes,
                        "the prompt send fell below the lifecycle return")

    def test_the_live_branch_cleans_the_negative_prompt_too(self) -> None:
        opens, closes = branch_bounds()
        at = APP_JS.index("negative_prompt: _clean(")
        line = APP_JS[:at].count(chr(10)) + 1
        self.assertLess(opens, line)
        self.assertLess(line, closes)

    def test_the_raw_textarea_read_is_gone_from_the_send(self) -> None:
        """The exact expression that shipped the defect."""

        self.assertNotIn(
            'positive_prompt: document.getElementById("paramPrompt")?.value',
            APP_JS)

    def test_both_paths_use_one_definition(self) -> None:
        """Two definitions is how they drift apart. One `const` in one scope
        is also why the legacy declaration had to go first -- a second one
        would be a SyntaxError, not a shadow."""

        self.assertEqual(1, APP_JS.count("const _preparedPrompt = () =>"))
        self.assertEqual(1, APP_JS.count("const _clean = (s) =>"))
        self.assertEqual(2, APP_JS.count("_preparedPrompt()"))

    def test_the_auto_detail_slots_are_cleaned_on_the_live_path(self) -> None:
        """The legacy slots cleaned their prompts and the live ones did not,
        so the same toggle applied to one path and not the other.

        `_autoDetailGroup` is DEFINED above the branch and SPREAD inside it,
        so the position test here is on the spread, not on the definition.
        """

        opens, closes = branch_bounds()
        self.assertIn("prompt: _clean(", APP_JS)
        at = APP_JS.index("auto_detail: _autoDetailGroup(")
        line = APP_JS[:at].count(chr(10)) + 1
        self.assertLess(opens, line)
        self.assertLess(line, closes)

    def test_the_definitions_precede_every_helper_that_uses_them(self) -> None:
        """Reading order matches dependency order. `_autoDetailGroup` used
        `_clean` from 140 lines above its definition and worked only because
        the arrow runs later -- correct, and not something a reader should
        have to prove to themselves."""

        self.assertLess(line_of("const _clean = (s) =>"),
                        line_of("const _autoDetailGroup = ("))
        self.assertLess(line_of("const _preparedPrompt = () =>"),
                        line_of("const _autoDetailGroup = ("))


class TheCompilerWasAlwaysCorrectTests(unittest.TestCase):
    """Nothing about `lora-stack.js` needed fixing. It had one consumer, and
    that consumer drew a label."""

    def test_it_compiles_the_tag_syntax_the_engine_reads(self) -> None:
        self.assertIn('"<lora:" + r.name + ":" + w + ">"', LORA_JS)

    def test_it_returns_the_prompt_unchanged_when_nothing_applies(self) -> None:
        """So an owner with an empty stack sends exactly what they typed --
        which is what makes calling it unconditionally safe."""

        at = LORA_JS.index("function compilePrompt(prompt)")
        body = LORA_JS[at:LORA_JS.index(chr(10) + "}", at)]
        self.assertIn("return appended ? out : raw;", body)

    def test_it_is_exported(self) -> None:
        self.assertIn("compilePrompt: compilePrompt,", LORA_JS)


class TheAutoDetailStackReachesTheEngineTests(unittest.TestCase):
    """AR6.8. Dead twice over, and it said so itself.

    `ad-lora-stack.js` was written against a structured `loras` field on the
    request. There is no such field: `grep -n loras` over presentation.py,
    contracts.py and auto_detail.py returns nothing. And the only two sends
    were `app.js:3178`, below the lifecycle `return`, and `app.js:5183`, aimed
    at `/studio/upscale_and_refine` -- a route Studio does not serve.

    Its own header claimed "The backend performs its own validated
    compilation", which was the opposite of true, and its `compileSuffix` was
    labelled "Display/debug compile only". That function was in fact the only
    thing capable of getting these LoRAs anywhere, because `<lora:...>` is
    parsed out of PROMPT TEXT and there is no separate transport.
    """

    AD_JS = (FRONTEND / "ad-lora-stack.js").read_text(encoding="utf-8")

    def test_the_slot_prompt_carries_the_slot_stack(self) -> None:
        self.assertIn("prompt: _clean(_adPromptWithLoras(n)),", APP_JS)

    def test_the_helper_uses_the_modules_own_compiler(self) -> None:
        at = APP_JS.index("const _adPromptWithLoras")
        body = APP_JS[at:at + 420]
        self.assertIn("window.ADLoRAStack?.compileSuffix?.(n)", body)

    def test_an_empty_stack_leaves_the_prompt_alone(self) -> None:
        """Same property that makes the base-prompt call safe."""

        at = APP_JS.index("const _adPromptWithLoras")
        self.assertIn("if (!suffix) return typed;", APP_JS[at:at + 420])

    def test_the_module_no_longer_claims_a_backend_that_never_existed(self) -> None:
        self.assertNotIn("The backend performs its own validated compilation",
                         self.AD_JS)
        self.assertNotIn("Display/debug compile only", self.AD_JS)

    def test_neither_invented_cap_survives_in_code(self) -> None:
        """Both claimed a backend cap behind them and there was no backend.
        Scanned as CODE -- the comment explaining the removal names them."""

        code = chr(10).join(line for line in self.AD_JS.splitlines()
                            if not line.lstrip().startswith("//"))
        self.assertNotIn("MAX_PER_SLOT", code)
        self.assertNotIn("MAX_ACT_LEN", code)


class TheTidyToggleKeepsItsPromiseTests(unittest.TestCase):
    def test_the_setting_exists_and_says_what_it_does(self) -> None:
        at = SHELL.index('data-setting-key="togglePromptCleanup"')
        item = SHELL[at:at + 900]
        self.assertIn("copy sent to generation", item)

    def test_the_toggle_still_turns_it_off(self) -> None:
        """A cleanup that could not be declined would be a rewrite."""

        self.assertIn("(State.promptCleanup ?? true) ? _cleanupPromptText(s) : s",
                      APP_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_PROMPT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
