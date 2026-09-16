"""The inpaint controls are live, and visible. AR4.7.

THE DEFECT THIS CLOSES

Entering mask mode showed an inpaint bar containing a spacer and one button.
Area, Blur, Fill and Padding all carried `hidden`, so the owner saw `Clear` and
nothing else -- while all four values were being sent to the engine and all
four worked.

They were hidden CORRECTLY at `9533093d`: their payload sat below the lifecycle
`return` and never left the browser, so per D8 an absent service is hidden, not
labelled. WP1.4 and WP1.6 then made inpaint live, and nobody un-hid them.

The exact inverse of AR5, where a control had no backend. Here a working
backend had no control.

WHY THESE TESTS ASSERT ON THE RENDERED BAR, NOT THE MARKUP

`9533093d` learned this the hard way and wrote it down:

> `hidden` ALONE WAS NOT ENOUGH... `[hidden]` is a UA-stylesheet rule, so any
> author rule setting display on the same element wins -- `.ctx-inline
> { display: flex }` left Area and Fill on screen while the two `.ctx-scrub`
> controls hid correctly.

So markup alone cannot say whether a control is visible, in either direction.
`app.css:2015` is what makes the attribute mean something, and it is asserted
here because removing it would silently un-hide everything else that uses it.

WHAT THE GPU PROVED

Each control moved, one variable at a time, same seed and mask:

    mask blur       105122 of 589824 pixels differ  (17.82%)
    Only Masked/Whole 31312                          (5.31%)
    padding          27102                           (4.59%)

Mask blur moves nearly a fifth of the image. That is the seam lever the owner
reached for, and it was behind an attribute.

Review: `Evidence/source-review/AR4.7-inpaint-controls.md`.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_INPAINT_TESTS = 14

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
SHELL = (FRONTEND / "index.html").read_text(encoding="utf-8")
APP_CSS = (FRONTEND / "app.css").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
PORT_PY = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
    encoding="utf-8")


def _bar() -> str:
    start = SHELL.index('id="inpaintBar"')
    return SHELL[start:SHELL.index('<div class="brush-presets"', start)]


class ControlsAreNoLongerHiddenTests(unittest.TestCase):
    """The whole defect, in one assertion each."""

    def test_no_inpaint_control_carries_hidden(self) -> None:
        self.assertNotIn(" hidden", _bar(),
                         "an inpaint control is hidden again")

    def test_all_four_controls_are_present(self) -> None:
        bar = _bar()
        for control in ("paramInpaintArea", 'data-key="blur"',
                        "paramFill", 'data-key="padding"'):
            with self.subTest(control=control):
                self.assertIn(control, bar)

    def test_area_offers_both_choices(self) -> None:
        """The owner asked for these by name."""

        bar = _bar()
        self.assertIn("Only Masked", bar)
        self.assertIn("Whole Picture", bar)

    def test_clear_mask_survived(self) -> None:
        """Mask painting is a real local feature; `9533093d` kept it for that
        reason and this must not undo that."""

        self.assertIn("clearMaskBtn", _bar())


class HiddenStillMeansHiddenTests(unittest.TestCase):
    """The rule that makes the attribute enforceable, for everything else."""

    def test_the_important_rule_is_still_present(self) -> None:
        self.assertIn("[hidden] { display: none !important; }", APP_CSS)

    def test_the_rule_is_not_scoped_away(self) -> None:
        """A selector narrowed to one component would quietly un-hide every
        other control that relies on the attribute."""

        at = APP_CSS.index("[hidden] { display: none !important; }")
        line_start = APP_CSS.rfind("\n", 0, at) + 1
        self.assertEqual(at, line_start,
                         "the rule has been given a parent selector")


class ValuesStillReachTheEngineTests(unittest.TestCase):
    """A control that is visible again but wired to nothing would be a worse
    lie than a hidden one."""

    def test_every_control_is_in_the_request(self) -> None:
        for field in ("mask_blur:", "padding:", "fill:", "full_resolution:"):
            with self.subTest(field=field):
                self.assertIn(field, APP_JS)

    def test_the_payload_sits_INSIDE_the_lifecycle_branch(self) -> None:
        """The original reason for hiding them. If the inpaint group ever
        drops below the `return` again, it stops leaving the browser and the
        controls become dead a second time."""

        submit = APP_JS.index("await lifecycle.submitGenerate(jobParams)")
        branch = APP_JS.index("if (lifecycle && lifecycle.lifecycleAvailable())")
        inpaint = APP_JS.index("full_resolution:")
        self.assertLess(branch, inpaint, "the inpaint group is above the branch")
        self.assertLess(inpaint, submit,
                        "the inpaint group fell below the lifecycle submit")

    def test_the_engine_maps_all_of_them(self) -> None:
        for mapping in ('"mask_blur"', '"inpainting_fill"',
                        '"inpaint_full_res"', '"inpaint_full_res_padding"'):
            with self.subTest(mapping=mapping):
                self.assertIn(mapping, PORT_PY)


class LatentNoiseIsWithheldTests(unittest.TestCase):
    """Three fill modes ship; the fourth crashes and does not.

    MEASURED, on the GPU, everything else held fixed:

        fill 0  Fill            completed
        fill 1  Original        completed
        fill 2  Latent Noise    FAILED, IndexError
        fill 3  Latent Nothing  completed

    WHY it fails is NOT known, and this docstring used to claim it was: that
    Studio bypassed `process_images` and its seed bookkeeping. That was wrong.
    `process_images_inner` populates `all_prompts`, `all_seeds` and
    `all_subseeds` itself (`modules/processing.py:913-925`) and passes them
    into `init`, so the seed list IS there.

    The correction matters more than the fact. A plausible cause, asserted
    without being verified, is exactly what this suite exists to catch -- and
    it had been written into a test, which is how a wrong theory becomes
    permanent.

    So these tests now assert only what was OBSERVED: the option fails, and it
    is therefore not offered.
    """

    def test_the_broken_option_is_not_offered(self) -> None:
        self.assertNotIn("latentNoise", _bar())
        self.assertNotIn('value="2"', _bar())

    def test_the_working_options_are_offered(self) -> None:
        bar = _bar()
        for value in ('value="1"', 'value="0"', 'value="3"'):
            with self.subTest(value=value):
                self.assertIn(value, bar)

    def test_the_removal_says_why_where_a_reader_will_look(self) -> None:
        """An option that silently vanishes gets restored by the next person
        doing a parity sweep."""

        bar = _bar()
        self.assertIn("all_seeds", bar)
        self.assertIn("AR4.7-inpaint-controls.md", bar)

    def test_the_reason_is_recorded_as_unknown_not_invented(self) -> None:
        """The guard on the correction itself.

        The previous version of this test asserted a CAUSE -- that the port
        lacked seed bookkeeping -- which was not true and which nothing had
        verified. A test that encodes an unverified theory makes it permanent,
        so this asserts the opposite: that the record says the cause is
        unknown.
        """

        record = (APP_ROOT.parent / "Evidence" / "source-review"
                  / "AR4.7-inpaint-controls.md").read_text(encoding="utf-8")
        self.assertIn("not yet known", record.lower())
        self.assertNotIn("bypassing the outer", record)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_INPAINT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
