"""Brush controls whose consuming arithmetic does not honour their range.

The same class as the Smoothing 0 divide-by-zero fixed in `f6cc4bdc`: a legal
value of a visible control that the code cannot survive. Three were found by a
sweep and confirmed in a browser; two are fixed here and guarded, and the third
is an owner decision recorded in
`Evidence/source-review/AR8.9-brush-range-defects.md`.

Guards are written against the INVARIANT, not the literal that happened to be
wrong, because that is what caught the sixth role-set instance.
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

EXPECTED_AR89_TESTS = 10


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


INDEX_HTML = _read("index.html")
CANVAS_UI = _strip_js_comments(_read("canvas-ui.js"))
CANVAS_CORE = _strip_js_comments(_read("canvas-core.js"))


def _balanced_block(source: str, opener: str) -> str:
    """From ``opener`` to the brace that closes it.

    Located by declaration rather than by line number, so inserting lines above
    a function does not quietly stop the guarding.
    """

    start = source.index(opener)
    depth = 0
    opened = False
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced block for {opener!r}")


def _control_range(key: str) -> tuple[int, int]:
    match = re.search(
        rf'data-key="{re.escape(key)}"[^>]*?data-min="(-?\d+)"[^>]*?data-max="(-?\d+)"',
        INDEX_HTML,
    )
    if match is None:
        raise AssertionError(f"the {key!r} control's range did not parse")
    return int(match.group(1)), int(match.group(2))


class BrushSizeKeyTests(unittest.TestCase):
    """A key called "increase" must never make the brush smaller.

    It was `Math.min(100, S.brushSize + 1)` while the Size control offers up to
    500, so a brush set to 300 with the scrub jumped down to 100 on the first
    press -- measured live, brushPx 3991 -> 768.
    """

    def _action(self, name: str) -> str:
        marker = f'case "canvas.brushSize.{name}":'
        start = CANVAS_UI.index(marker)
        return CANVAS_UI[start:start + 420]

    def test_increase_does_not_hardcode_a_ceiling(self):
        body = self._action("increase")
        self.assertNotRegex(
            body, r"Math\.min\(\s*\d+\s*,",
            "the increase action restates a maximum as a literal; read it from "
            "the control instead, or the two drift apart again",
        )

    def test_increase_reads_the_bound_from_the_control(self):
        body = self._action("increase")
        self.assertIn('data-key="size"', body)
        self.assertIn("dataset.max", body)

    def test_increase_cannot_move_the_size_downwards(self):
        """The direction guarantee, independent of what the bound turns out
        to be: whatever the control says, the value may not decrease."""

        body = self._action("increase")
        self.assertRegex(body, r"Math\.max\(\s*S\.brushSize\s*,")

    def test_decrease_still_stops_at_the_control_minimum(self):
        body = self._action("decrease")
        minimum, _maximum = _control_range("size")
        self.assertIn(f"Math.max({minimum},", body)


class CloneStampTests(unittest.TestCase):
    """A tool that silently does nothing at a legal setting.

    At minimum Size the stamp canvas is 1x1. Its only pixel sits
    hypot(0.5, 0.5) = 0.707 from the middle while the radius is 0.5, so the
    softness falloff is evaluated OUTSIDE the dab and multiplies the one pixel
    there is by zero. At Hardness 100% the block is skipped entirely, which is
    why the tool appeared to work and the defect survived.
    """

    def _falloff_condition(self) -> str:
        start = CANVAS_CORE.index("function cloneStamp(")
        body = CANVAS_CORE[start:start + 2000]
        match = re.search(r"if \(S\.brushHardness < 1[^)]*\)", body)
        self.assertIsNotNone(
            match, "cloneStamp no longer gates its falloff on hardness")
        return match.group(0)

    def test_the_falloff_is_skipped_when_there_is_no_edge_to_soften(self):
        condition = self._falloff_condition()
        self.assertRegex(
            condition, r"sz\s*>=\s*2",
            "cloneStamp applies its softness falloff to stamps too small to "
            "have an edge, which erases the dab entirely",
        )

    def test_the_minimum_size_the_control_allows_still_paints(self):
        """Tied to the control, so raising its minimum keeps this honest.

        With the control's minimum at 1, `brushPx` floors at 1 and the stamp
        is 1x1 -- below the size the falloff can handle, so the guard must be
        present. If the minimum ever rises above that, the guard is belt and
        braces rather than load-bearing, and this still passes.
        """

        minimum, _maximum = _control_range("size")
        self.assertGreaterEqual(minimum, 1)
        self.assertIn("sz >= 2", self._falloff_condition())


class SizeLabelTellsTheTruthTests(unittest.TestCase):
    """The third AR8.9 finding, answered by disclosure rather than by range.

    The Size slider is not pixels: `brushPx` runs it through a power curve
    against the document's short side, so 100 is already the whole canvas and
    everything above it is larger than the image. Rather than remove that range
    -- which would take capability away from a control, against a standing
    ruling -- the control now shows what it actually produces.
    """

    def _scrub_display(self) -> str:
        return _balanced_block(CANVAS_UI, "function _scrubDisplay(")

    def test_the_size_label_shows_the_effective_pixel_width(self):
        body = self._scrub_display()
        self.assertRegex(
            body, r'key\s*===\s*"size"',
            "the size scrub must be distinguished so its pixel width can be shown",
        )
        self.assertIn("brushPx()", body)
        self.assertIn("px)", body)

    def test_only_the_size_scrub_gains_a_trailing_figure(self):
        """Opacity, Hardness, Smoothing and the inpaint scrubs are already in
        their own units and must not grow a second number."""

        body = self._scrub_display()
        self.assertRegex(body, r'if \(key === "size"')

    def test_the_label_cannot_go_stale_when_the_document_resizes(self):
        """`resizeCanvas` bumps no revision and fires no action-complete, so
        there is nothing to subscribe to -- and eight call sites resize a
        document. The bar re-checks where a resize is always followed by a
        repaint, so a wrong number cannot survive on screen.
        """

        self.assertIn("_refreshCtxBarIfDocumentResized", CANVAS_UI)
        redraw = _balanced_block(CANVAS_UI, "function _redraw(")
        self.assertIn(
            "_refreshCtxBarIfDocumentResized()", redraw,
            "the size-follows-document check must run on repaint",
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_AR89_TESTS, found)


if __name__ == "__main__":
    unittest.main()
