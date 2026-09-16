"""Controls that do nothing, and copy that says something untrue.

Three owner-visible falsehoods shipped in the standalone frontend. Each is a
control or a sentence that told the owner something the product does not do:

    Eraser Opacity      moved, changed nothing
    Clone Opacity       moved, changed nothing
    Regional Prompting  "Each region runs as a separate inpaint pass"
    six inpaint controls   drove a request path that does not exist

None of them is a crash, so nothing caught them. They were found by tracing
source for the CT0 crosswalk, and they are the reason this suite exists: a
dead control is a defect even when nothing throws.

SCOPE: that the wiring and the copy stay honest. Not the behaviour of the
stroke engine, not inpaint, not Regional Prompting execution -- none of which
exist to test.
"""

from __future__ import annotations

import json
import sys
import re
import unittest
from pathlib import Path


EXPECTED_PRODUCT_TRUTHFULNESS_TESTS = 23

APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = APP_ROOT / "forge_studio" / "frontend"


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


class EraserAndCloneOpacityTests(unittest.TestCase):
    """The two sliders that moved and did nothing.

    `pOp()` returned `S.brushFlow`, which nothing in the repository assigned,
    so it was permanently 1.0. The brush survived that because it composites
    its whole stroke at `S.brushOpacity` when the stroke commits. The eraser
    replaced its target destructively and the clone draws straight to the
    layer, so neither had a commit-time alpha and the per-stamp value was the
    only opacity they would ever get -- hence `pOpacity`.

    UPDATED BY CT3, twice over. `S.brushFlow` has a control now, so `pOp` is no
    longer a constant. And the ERASER has a commit-time alpha: it accumulates
    into the same stroke alpha map as the brush and `commitStroke` composites
    it `destination-out` at `S.brushOpacity`, so it no longer needs `pOpacity`
    and no longer has a stamp function of its own.

    `pOpacity` remains, and is still exactly right, for the CLONE -- which
    does still draw straight to the layer. CT10 owns that migration.
    """

    def test_a_helper_exists_that_reads_the_owners_opacity(self) -> None:
        source = _read("canvas-core.js")
        self.assertIn("function pOpacity(p)", source)
        body = source.split("function pOpacity(p)")[1].split("}")[0]
        self.assertIn("S.brushOpacity", body)
        self.assertNotIn("S.brushFlow", body)

    def test_the_eraser_no_longer_has_a_stamp_function_of_its_own(self) -> None:
        """CT3 deleted `stampWetErase`. It was a duplicated generic-circle path
        that knew nothing about tip shape, roundness, spikes, density or
        falloff, so those controls did nothing while the eraser was selected --
        which §8.4 forbids keeping once parity is proven."""

        source = _read("canvas-core.js")
        code_only = re.sub(r"^[ 	]*//.*$", "", source, flags=re.MULTILINE)
        self.assertNotIn("function stampWetErase", code_only)

    def test_the_eraser_gets_its_opacity_at_commit_like_the_brush(self) -> None:
        """Which is what made `pOpacity` unnecessary for it: one alpha map,
        one commit, and the only difference is the composite operation."""

        source = _read("canvas-core.js")
        body = source.split("function commitStroke()")[1].split(
            chr(10) + "function ")[0]
        self.assertIn('S.tool === "eraser" ? "destination-out" : "source-over"',
                      body)
        # BE17 ADDED A THIRD CASE and the property is unchanged: the brush
        # applies the owner's Opacity ONCE, at commit. Buildup moves that
        # bound INSIDE the stroke -- its per-dab target is Flow x Opacity --
        # so committing at Opacity as well would square it. Off, which is
        # thirteen of sixteen presets, this is the line it replaced.
        self.assertIn(
            "T.ctx.globalAlpha = wasMask ? 1 : "
            "(S.brushBuildup ? 1 : S.brushOpacity);", body)

    def test_the_clone_stamp_still_reads_it(self) -> None:
        """The clone still draws straight to the layer with no commit-time
        alpha, so `pOpacity` is still the only opacity it will ever get. This
        is the remaining consumer, and the reason the helper stays."""

        source = _read("canvas-core.js")
        self.assertIn("T.ctx.globalAlpha = pOpacity(p);", source)

    def test_the_brush_stamp_still_reads_flow(self) -> None:
        """The brush must NOT be switched over.

        It already applies the owner's opacity once, at commit. Reading it
        per stamp as well would apply it twice and darken every soft stroke.
        This is the test that stops the obvious "just wire brushFlow to
        brushOpacity" fix.
        """
        source = _read("canvas-core.js")
        body = source.split("function stampWet(")[1].split(
            chr(10) + "function ")[0]
        self.assertIn("pOp(p)", body)
        self.assertNotIn("pOpacity(p)", body)

    def test_the_commit_still_applies_opacity_once(self) -> None:
        source = _read("canvas-core.js")
        # BE17 ADDED A THIRD CASE and the property is unchanged: the brush
        # applies the owner's Opacity ONCE, at commit. Buildup moves that
        # bound INSIDE the stroke -- its per-dab target is Flow x Opacity --
        # so committing at Opacity as well would square it. Off, which is
        # thirteen of sixteen presets, this is the line it replaced.
        self.assertIn(
            "T.ctx.globalAlpha = wasMask ? 1 : "
            "(S.brushBuildup ? 1 : S.brushOpacity);", source)

    def test_the_context_bar_still_offers_opacity_for_both_tools(self) -> None:
        """A fix that hid the controls instead would also pass the tests above.

        It must not: the engine can honour them, so the truthful fix is to
        make them work.

        COMMENTS STRIPPED FIRST. This took the first line containing
        "eraser:" and CT3 added a comment inside this very block explaining why
        Flow is not offered to the eraser -- so the guard read the explanation
        instead of the entry and failed on a file that was correct. That is the
        comments-trip-guards trap this codebase has now recorded five times,
        and a guard about CODE must look at code.
        """
        source = _read("canvas-ui.js")
        code_only = re.sub(r"^[ 	]*//.*$", "", source, flags=re.MULTILINE)
        show = code_only.split("const show = {")[1].split("};")[0]
        for tool in ("eraser:", "clone:"):
            with self.subTest(tool=tool):
                rows = [line for line in show.split(chr(10)) if tool in line]
                self.assertEqual(
                    1, len(rows),
                    f"expected exactly one {tool} entry, found {len(rows)}")
                self.assertIn('"opacity"', rows[0])


class RegionalPromptingCopyTests(unittest.TestCase):
    """The sentence that claimed an execution path that does not exist."""

    FALSE_CLAIM = "separate inpaint pass"

    def test_the_false_claim_is_gone_from_the_script(self) -> None:
        self.assertNotIn(self.FALSE_CLAIM, _read("canvas-ui.js"))

    def test_the_false_claim_is_gone_from_the_locale(self) -> None:
        self.assertNotIn(self.FALSE_CLAIM, _read("locales/en.json"))

    def test_no_locale_anywhere_still_carries_it(self) -> None:
        """It was in two files. A partial fix leaves the other one shipping."""
        for locale in sorted((FRONTEND / "locales").glob("*.json")):
            with self.subTest(locale=locale.name):
                self.assertNotIn(
                    self.FALSE_CLAIM, locale.read_text(encoding="utf-8"))

    def test_the_replacement_says_regions_are_not_sent(self) -> None:
        strings = json.loads(_read("locales/en.json"))
        hint = strings.get("canvas.regions.hint.unavailable", "")
        self.assertTrue(hint, "the replacement string is missing")
        self.assertIn("not available", hint.lower())
        self.assertIn("not sent", hint.lower())


class InpaintControlTests(unittest.TestCase):
    """Once controls for a request path that did not exist. AR4.7 ended that.

    THE ORIGINAL RULE, WHICH STILL STANDS

    D8: an absent service is HIDDEN, not disabled. When this class was written
    `Operation` had one member, `TXT2IMG`, and the payload carrying these
    fields was assembled BELOW the lifecycle `return` in app.js, so it never
    executed on this host. Hiding them was correct.

    WHY THE ASSERTION INVERTED

    WP1.4 and WP1.6 made inpaint live. The inpaint group is now built INSIDE
    the lifecycle branch, `forge_headless/live_generation_port.py` maps every
    field to Neo's canonical names, and the GPU says they work: mask blur moves
    17.82% of the image, Only Masked vs Whole Picture 5.31%, padding 4.59%.

    D8 says an ABSENT service is hidden. It does not say a PRESENT one should
    be. So the rule did not change -- the premise did, and this class now
    asserts the other side of the same rule.

    Kept rather than deleted, because the reasoning is the valuable part: the
    next person to see a hidden control needs to find out whether it is dead or
    merely forgotten. See `Evidence/source-review/AR4.7-inpaint-controls.md`.
    """

    def test_the_four_controls_are_no_longer_hidden(self) -> None:
        """The inversion. These drive a live path; hiding them now would be
        the falsehood."""

        markup = _read("index.html")
        bar = markup.split('id="inpaintBar"')[1].split("</div>" + chr(10) + "      </div>")[0]
        for control in ('data-key="blur"', 'data-key="padding"',
                        'id="paramInpaintArea"', 'id="paramFill"'):
            with self.subTest(control=control):
                fragment = bar.split(control)[0].rsplit("<span", 1)[-1]
                self.assertNotIn("hidden", fragment)

    def test_they_are_only_visible_because_they_reach_the_engine(self) -> None:
        """The condition under which showing them is honest. If the inpaint
        group ever falls below the lifecycle `return` again, it stops leaving
        the browser and these controls must go back into hiding."""

        app_js = _read("app.js")
        branch = app_js.index("if (lifecycle && lifecycle.lifecycleAvailable())")
        inpaint = app_js.index("full_resolution:")
        submit = app_js.index("await lifecycle.submitGenerate(jobParams)")
        self.assertLess(branch, inpaint)
        self.assertLess(inpaint, submit)

    def test_the_hidden_attribute_actually_hides(self) -> None:
        """`hidden` alone was not enough, and the browser proved it.

        `[hidden] { display: none }` is a UA-stylesheet rule, so ANY author
        rule setting display on the same element wins. `.ctx-inline` sets
        `display: flex`, so two of the four controls carried the attribute and
        stayed on screen. Marking them hidden and shipping would have looked
        correct in the markup and been false on the page.
        """
        css = _read("app.css")
        self.assertIn("[hidden] { display: none !important; }", css)

    def test_they_are_hidden_rather_than_labelled_unavailable(self) -> None:
        """D8, and the design system says the same thing as a test.

        `test_s07_design_system.py` asserts no element carries
        `data-unavailable`, and "Settings is not a roadmap". An absent service
        is hidden; a grey placeholder explaining what the product cannot do is
        the thing that rule exists to prevent.
        """
        self.assertNotIn("data-unavailable", _read("index.html"))

    def test_clearing_the_mask_is_still_offered(self) -> None:
        """Mask painting is a real local feature and keeps its one real action."""
        markup = _read("index.html")
        self.assertIn('id="clearMaskBtn"', markup)
        clear = markup.split('id="clearMaskBtn"')[1].split(">")[0]
        self.assertNotIn("hidden", clear)

    def test_no_inpaint_field_entered_the_canonical_request(self) -> None:
        """The fix must not become the beginning of P0.9.

        Hiding a control is truthfulness. Adding a server field to make the
        control mean something is a second generation path, which the existing
        contract forbids and P0.9 owns.
        """
        contracts = (APP_ROOT / "forge_studio" / "contracts.py").read_text(
            encoding="utf-8")
        translator = (APP_ROOT / "forge_headless" / "studio_generation.py").read_text(
            encoding="utf-8")
        for field in ("canvas_b64", "mask_b64", "inpaint_mode",
                      "inpainting_fill", "inpaint_full_res", "regions_json"):
            with self.subTest(field=field):
                self.assertNotIn(field, contracts)
                self.assertNotIn(field, translator)


class FastFp16HelpTests(unittest.TestCase):
    """The launcher flag described one of the two things it does.

    `--fast-fp16` said "allow fp16 accumulation ... the images differ
    slightly". Both halves were wrong. `memory_management` sets
    `allow_fp16_accumulation` from it AND `PRIORITIZE_FP16`, and the second
    moves any bf16-first architecture to fp16 storage -- which is the whole
    reason Anima ran in a different dtype from the Extension. And the
    difference is not slight: at a fixed seed, 25.5% of pixels came back
    identical.

    Measured in `Evidence/anima-extension-parity-2026-08-16/`.
    """

    def _help(self) -> str:
        """The assembled string, not the source that spells it.

        argparse help is written as adjacent literals across several lines, so
        a raw substring search finds neither "fp16 storage" nor any other
        phrase that happens to straddle a line break. `ast` joins them the way
        Python does, which is what the owner actually reads.
        """
        import ast

        tree = ast.parse((APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            literals = [a.value for a in node.args
                        if isinstance(a, ast.Constant)]
            if "--fast-fp16" not in literals:
                continue
            for keyword in node.keywords:
                if keyword.arg == "help":
                    return ast.literal_eval(keyword.value)
        self.fail("--fast-fp16 is no longer offered by the launcher")

    def test_it_admits_it_changes_the_model_dtype(self) -> None:
        """Accumulation alone does not explain the dtype the log reports."""
        self.assertIn("fp16 storage", self._help())

    def test_it_does_not_understate_the_difference(self) -> None:
        """It said "the images differ slightly". 25.5% of pixels matched."""
        self.assertNotIn("differ slightly", self._help())
        self.assertIn("CHANGES THE IMAGE", self._help())

    def test_the_flag_still_drives_both_effects(self) -> None:
        """The copy is only true while the source is.

        If a future change stops `fast_fp16` selecting the dtype, the help
        text becomes an overstatement and this fails rather than shipping.
        """
        memory = (APP_ROOT / "backend" / "memory_management.py").read_text(
            encoding="utf-8")
        window = memory.split("if args.fast_fp16")[1].split("except")[0]
        self.assertIn("allow_fp16_accumulation", window)
        self.assertIn("PRIORITIZE_FP16 = True", window)

    def test_the_launcher_script_says_the_same_thing(self) -> None:
        """The copy exists TWICE and the first fix caught one of them.

        `Start-Studio.bat` documents the same flags for the owner who opens it,
        and it carried the identical understatement. A fix that lands in only
        one of the two still ships the false one.
        """
        path = APP_ROOT.parent / "Start-Studio.bat"
        if not path.exists():
            self.skipTest("Start-Studio.bat is not in this checkout")
        block = path.read_text(encoding="utf-8").split(
            "--fast-fp16")[1].split("--autotune")[0]
        self.assertNotIn("differ slightly", block)
        self.assertIn("CHANGES THE IMAGE", block)

    def test_anima_still_declares_bfloat16_first(self) -> None:
        """The other half of why the dtype differed at all.

        `unet_dtype` walks `supported_inference_dtypes` in order, so the FIRST
        entry wins when nothing prioritises fp16.
        """
        listing = (APP_ROOT / "modules_forge" / "packages" / "huggingface_guess"
                   / "model_list.py").read_text(encoding="utf-8")
        anima = listing.split("class Anima(")[1].split("class ")[0]
        declared = anima.split("supported_inference_dtypes = [")[1].split("]")[0]
        self.assertTrue(declared.strip().startswith("torch.bfloat16"), declared)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            EXPECTED_PRODUCT_TRUTHFULNESS_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
