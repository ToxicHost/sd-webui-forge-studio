"""CT3 stage A: Opacity, Flow and Buildup are three things, not one.

§8.2 is blunt about what this package is not allowed to do:

> Do not preserve the current constant `pOp()` behavior while claiming flow
> works.

It was a constant. `S.brushFlow` had **no writer anywhere in the frontend** and
no control in the UI, so `pOp()` returned 1.0 forever and handed it to every
stamp. Buildup did not exist at all — no field, no control, no code.

The rendered proof is in `Evidence/source-review/AC4-CT3-brush.md`, measured in
a live page, because an alpha value is not something source text can assert.
What lives here is the wiring that makes those measurements possible and the
guards that stop it rotting back:

```text
opacity   S.brushOpacity, applied ONCE at commitStroke as a globalAlpha
flow      the per-stamp contribution
buildup   whether stamps accumulate inside one stroke
```

and one guard that is worth more than the rest of the file put together —
`test_every_context_bar_scrub_reaches_a_setting`, which fails if any visible
scrub has no consumer. That is the whole defect class §8.6 is about.
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

EXPECTED_CT3A_TESTS = 20


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


CORE = _strip_js_comments((FRONTEND / "canvas-core.js").read_text(encoding="utf-8"))
UI = _strip_js_comments((FRONTEND / "canvas-ui.js").read_text(encoding="utf-8"))
HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")


def _stamp_alpha_map() -> str:
    start = CORE.index("function stampAlphaMap(")
    depth = 0
    for index in range(CORE.index("{", start), len(CORE)):
        if CORE[index] == "{":
            depth += 1
        elif CORE[index] == "}":
            depth -= 1
            if depth == 0:
                return CORE[start:index + 1]
    raise AssertionError("stampAlphaMap never closes")


class FlowIsNoLongerAConstantTests(unittest.TestCase):
    def test_the_flow_field_now_has_a_writer(self):
        """It had none. This is the assertion that would have caught it."""

        writers = re.findall(r"S\.brushFlow\s*=", CORE + UI)
        self.assertTrue(
            writers,
            "S.brushFlow has no writer, so it is a constant again and every "
            "stamp is receiving 1.0 whatever the control says",
        )

    def test_the_flow_control_exists_and_is_reachable(self):
        self.assertRegex(HTML, r'data-key="flow"')
        self.assertIn("flow:", UI[UI.index("const _scrubMap = {"):
                                  UI.index("function _scrubDisplay(")])

    def test_flow_cannot_be_dragged_to_zero(self):
        """A zero-flow brush paints nothing, which an owner cannot tell from a
        broken one. The control and the setter agree on the floor."""

        match = re.search(r'data-key="flow"[^>]*data-min="(\d+)"', HTML)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 1)
        scrub = UI[UI.index("flow:"):]
        self.assertIn("Math.max(0.01", scrub[:200])

    def test_flow_is_shown_for_the_brush(self):
        show = UI[UI.index("const show = {"):UI.index("const visible = new Set")]
        brush = show[show.index("brush:"):show.index("\n", show.index("brush:"))]
        self.assertIn('"flow"', brush)

    def test_flow_is_shown_for_the_eraser_too(self):
        """It was withheld one stage ago, for a real reason: the eraser had no
        commit-time alpha, so its per-stamp value already WAS the Opacity the
        owner set, and a second multiplier would have meant something different
        from the control beside it.

        CT3 stage D removed that reason rather than working around it. The
        eraser accumulates into the same alpha map as the brush and
        `commitStroke` composites it at `S.brushOpacity`, so Opacity bounds the
        stroke and Flow is the per-stamp contribution -- in both tools, meaning
        the same thing in each.
        """

        show = UI[UI.index("const show = {"):UI.index("const visible = new Set")]
        eraser = show[show.index("eraser:"):show.index("\n", show.index("eraser:"))]
        self.assertIn('"flow"', eraser)


class BuildupExistsTests(unittest.TestCase):
    def test_the_field_exists_and_defaults_to_what_shipped(self):
        self.assertRegex(CORE, r"brushBuildup:\s*false")

    def test_it_has_a_control(self):
        self.assertIn('id="dynBuildup"', HTML)
        self.assertRegex(UI, r'getElementById\("dynBuildup"\)\?\.addEventListener')

    def test_the_control_writes_the_field(self):
        """Anchored on the LISTENER, not on the first mention of the id: the
        sync in `_syncDynamicsPanel` mentions it first and only reads it."""

        start = UI.index('getElementById("dynBuildup")?.addEventListener')
        self.assertIn("S.brushBuildup = e.target.checked", UI[start:start + 300])

    def test_the_control_is_synced_from_the_field(self):
        """A panel that shows a stale checkbox is a control that lies about
        what the next stroke will do."""

        self.assertRegex(
            UI, r'buildupBox\.checked = !!S\.brushBuildup')

    def test_the_blend_rule_reads_it(self):
        self.assertIn("S.brushBuildup", _stamp_alpha_map())


class TheThreeQuantitiesStayDistinctTests(unittest.TestCase):
    def test_opacity_is_applied_once_at_commit(self):
        commit = CORE[CORE.index("function commitStroke() {"):]
        commit = commit[:3000]
        # BE17 ADDED A THIRD CASE, and the property -- Opacity applied ONCE --
        # is what it protects. Buildup now moves that bound INSIDE the stroke:
        # its per-dab target is Flow x Opacity, so committing at Opacity as
        # well would apply it twice. With Buildup off, which is thirteen of the
        # sixteen shipped presets, this is the line it replaced.
        self.assertIn(
            "globalAlpha = wasMask ? 1 : (S.brushBuildup ? 1 : S.brushOpacity)",
            commit)

    def test_buildup_is_the_only_thing_that_decides_accumulation(self):
        """SUPERSEDED BY BE5, rewritten rather than deleted.

        This asserted the accumulation CEILING -- `const ceiling =
        (useSoftBlend && !S.brushBuildup) ? min(255, flow*255) : 255` -- on the
        grounds that without it a buildup stroke could never exceed one stamp's
        contribution. Sound reasoning for the engine it described.

        BE5 removed the ceiling, and could only do so because the ceiling
        existed to contain a DIFFERENT bug: soft tips were forced to accumulate
        because `dabAlpha`'s soft branch peaked at 0.4, so a max-blend would
        have capped them at 40% grey. With the falloff reaching full strength a
        max-blended soft dab caps at FLOW, forced accumulation is unnecessary,
        and the ceiling has nothing left to contain.

        The property protected here is unchanged: Buildup must be what
        distinguishes accumulating from not. It is now the ONLY thing that does.
        """

        body = _stamp_alpha_map()
        self.assertIn("const useSoftBlend = S.brushBuildup;", body)
        self.assertNotIn("const ceiling", body)

    def test_a_soft_tip_no_longer_ignores_the_buildup_setting(self):
        """SUPERSEDED BY BE5, and this one asserted the defect outright.

        It required `useSoftBlend = softTip || S.brushBuildup` -- that a fully
        soft tip accumulate WHATEVER Buildup says. So the control was pinned as
        having no effect on precisely the tips it matters most for, and the
        docstring justified it as "a property of the TIP, not a paint setting".
        That was true of the invented 0.4 curve and of nothing else.
        """

        body = _stamp_alpha_map()
        self.assertNotIn("softTip", body)
        self.assertNotIn("hard < 0.01", body)

    def test_the_max_blend_survives_as_the_floor_under_accumulation(self):
        """WHAT SURVIVED BE17, and it is not the whole rule any more.

        This used to pin the literal `if (a255 > map[idx]) map[idx] = a255;`
        and called it "the hard path". BE17 replaced the max as the coverage
        RULE -- paint has to accumulate within a stroke or Flow is a second
        Opacity slider, which is what the owner reported -- but kept the max as
        a FLOOR, because pure accumulation gets the cross-section wrong.

        Measured at hardness 1.0, Flow 50, across the band:

            with the floor   127 127 127 127 127 127 127
            without it        62  91 109 116 124 124 124

        -- a soft shoulder on a brush whose whole point is that it has none.
        With the floor the single-pass profile is IDENTICAL at hardness 1.0,
        0.85 and 0.5, and the six Flow-100 presets are byte-identical, because
        a target at or above 0.995 short-circuits the accumulator entirely.

        So the guarantee this test was written for still holds -- an existing
        hard-tip document renders as it did -- and it now holds by two named
        mechanisms rather than by one line.
        """

        body = _stamp_alpha_map()
        core = CORE[CORE.index("function _depositAt("):]
        core = core[:core.index(chr(10) + "}")]
        # The floor: the old max-blend value, computed per pixel as before.
        self.assertIn("const peak = (cov * target * 255) | 0;", core)
        self.assertIn("return built > peak ? built : peak;", core)
        # ...and the short-circuit that keeps a Flow-100 tip on it alone.
        self.assertIn("const accumulating = target < 0.995;", body)
        self.assertIn("if (!acc) return peak;", core)


class PerToolMemoryTests(unittest.TestCase):
    def test_both_settings_are_remembered_per_tool(self):
        self.assertIn("brushFlow: S.brushFlow,", UI)
        self.assertIn("brushBuildup: S.brushBuildup,", UI)

    def test_a_deliberate_low_flow_survives_the_restore(self):
        """`||` would replace a saved 0.01 with 1. `??` preserves it and still
        treats absent as the default."""

        self.assertRegex(UI, r"S\.brushFlow = saved\.brushFlow \?\? 1\.0;")
        self.assertRegex(UI, r"S\.brushBuildup = saved\.brushBuildup \?\? false;")


class NoVisibleControlWithoutAConsumerTests(unittest.TestCase):
    """§8.6, as a guard rather than a promise.

    "Every currently visible instance must be implemented truthfully, removed,
    or disabled with a specific reason. Do not leave it clickable and document
    it only in Known Issues."

    Flow was visible in eight preset definitions and reachable from nowhere.
    This is the shape of that defect, caught mechanically.
    """

    def test_every_context_bar_scrub_reaches_a_setting(self):
        keys = set(re.findall(r'class="ctx-scrub[^"]*"[^>]*data-key="([^"]+)"', HTML))
        keys |= set(re.findall(r'data-key="([^"]+)"[^>]*class="ctx-scrub', HTML))
        self.assertTrue(keys, "no context-bar scrubs found; the parse is wrong")
        registry = UI[UI.index("const _scrubMap = {"):UI.index("function _scrubDisplay(")]
        orphans = sorted(k for k in keys
                         if not re.search(rf"^\s*{re.escape(k)}:", registry, re.M))
        self.assertEqual(
            [], orphans,
            "these scrubs are visible and reach no setting at all",
        )

    #: Registry entries reached by a control that is NOT a `data-key` scrub.
    #: Each one is driven by a range input in the Brush Dynamics flyout, so it
    #: is reachable -- just not through the context bar. Named individually,
    #: because "some of them are fine" is how an orphan hides in a list.
    SCRUBLESS_BUT_REACHABLE = {
        "ratio":   "dynRatio",
        "density": "dynDensity",
        "spikes":  "dynSpikes",
        "angle":   "dynAngle",
        "taperIn": "dynTaperIn",
    }

    def test_the_registry_has_no_entry_without_a_control(self):
        """The mirror: a setter nothing can call is dead weight that reads as
        a feature to the next person."""

        registry = UI[UI.index("const _scrubMap = {"):UI.index("function _scrubDisplay(")]
        entries = set(re.findall(r"^\s*(\w+):\s*\{", registry, re.M))
        rendered = set(re.findall(r'data-key="([^"]+)"', HTML))
        orphans = sorted(entries - rendered - set(self.SCRUBLESS_BUT_REACHABLE))
        self.assertEqual(
            [], orphans,
            "these registry entries have no control in the page",
        )

    def test_the_named_exceptions_really_do_have_their_controls(self):
        """An allow-list that is never checked is just a list."""

        for key, element in sorted(self.SCRUBLESS_BUT_REACHABLE.items()):
            with self.subTest(setting=key):
                self.assertIn(f'id="{element}"', HTML)
                self.assertIn(f'getElementById("{element}")', UI)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_CT3A_TESTS, found)


if __name__ == "__main__":
    unittest.main()
