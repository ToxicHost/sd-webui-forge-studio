"""Contracts for the Session Strip's expanded/collapsed layout.

The native Canvas/Session integration reported the strip as a 22 px rail
with a 0 px scroll container and called it a layout defect. It is not:
`app.css` carries a deliberate `@media (max-width: 1600px)` rule whose own
comment says the strip auto-collapses below that width, and the previous
rehearsal ran at a 1600 px window. Above the breakpoint the strip reaches
its intended 192 px with a 191 px scroll container.

What WAS wrong is smaller and real: below the breakpoint the strip still
rendered its collapse control as a rotated expand affordance, but
clicking it only toggles the `collapsed` class that the media query
overrides -- a control claiming an action it cannot perform. This suite
pins the corrected contract on both sides of the breakpoint.

SCOPE: static CSS/JS source. The live measurement across viewport widths
is the milestone's rehearsal evidence; nothing here starts a browser.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CSS = FRONTEND / "app.css"
APP_JS = FRONTEND / "app.js"

#: The documented responsive breakpoint, in CSS pixels.
BREAKPOINT_PX = 1600
#: The strip's intended expanded width and the collapsed rail width.
EXPANDED_WIDTH = "192px"
RAIL_WIDTH = "22px"


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


def _media_block(source: str) -> str:
    """The body of the `@media (max-width: 1600px)` block."""

    start = source.index(f"@media (max-width: {BREAKPOINT_PX}px)")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unterminated media block")


class ExpandedStateTests(unittest.TestCase):
    def test_the_expanded_width_rule_exists_and_is_unscoped(self) -> None:
        source = _css()
        match = re.search(
            r"\[data-strip-col\] \.session-strip \{([^}]*)\}", source
        )
        self.assertIsNotNone(match)
        self.assertIn(f"width: {EXPANDED_WIDTH}", match.group(1))
        # It must live OUTSIDE the responsive block, so it governs every
        # viewport above the breakpoint.
        self.assertNotIn(match.group(0), _media_block(source))

    def test_the_scroll_container_is_not_hidden_when_expanded(self) -> None:
        source = _css()
        scroll = re.search(
            r"\[data-strip-col\] \.session-strip-scroll \{([^}]*)\}", source
        )
        self.assertIsNotNone(scroll)
        self.assertNotIn("display: none", scroll.group(1))

    def test_the_strip_owns_its_grid_column(self) -> None:
        # The deck grid tracks the strip's own width rather than pinning
        # it, which is why the column measured 192px above the breakpoint.
        self.assertIn(
            "[data-strip-col] #app-studio > .session-strip      "
            "{ grid-column: 3; grid-row: 1 / 3; }",
            _css(),
        )
        self.assertIn(
            "grid-template-columns: auto minmax(0, 1fr) auto auto auto auto;",
            _css(),
        )


class CollapsedStateTests(unittest.TestCase):
    def test_the_class_based_rail_keeps_its_width(self) -> None:
        self.assertIn(
            f"[data-strip-col] .session-strip.collapsed "
            f"{{ width: {RAIL_WIDTH}; cursor: pointer; }}",
            _css(),
        )

    def test_the_class_based_rail_hides_its_scroll_container(self) -> None:
        source = _css()
        self.assertIn(
            "[data-strip-col] .session-strip.collapsed .session-strip-scroll",
            source,
        )

    def test_the_class_based_rail_keeps_its_toggle(self) -> None:
        # Above the breakpoint the toggle must remain visible so the rail
        # can be expanded again.
        source = _css()
        rule = re.search(
            r"\[data-strip-col\] \.session-strip\.collapsed "
            r"\.session-strip-collapse \{([^}]*)\}",
            source,
        )
        self.assertIsNotNone(rule)
        self.assertNotIn("display: none", rule.group(1))


class ResponsiveRailTests(unittest.TestCase):
    """Below the breakpoint the layout owns the state, not the class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.block = _media_block(_css())

    def test_the_breakpoint_is_documented(self) -> None:
        self.assertIn("below 1600px the strip auto-collapses", _css())

    def test_the_rail_width_applies_below_the_breakpoint(self) -> None:
        self.assertIn(
            f"[data-strip-col] .session-strip {{ width: {RAIL_WIDTH}; }}",
            self.block,
        )

    def test_the_toggle_is_hidden_where_it_cannot_expand(self) -> None:
        # THE FIX. The media query overrides the class-based presentation,
        # so a visible toggle here would claim an action it cannot perform.
        self.assertIn(
            "[data-strip-col] .session-strip .session-strip-collapse "
            "{ display: none; }",
            self.block,
        )
        # And it must no longer be dressed as an expand affordance.
        self.assertNotIn(
            "[data-strip-col] .session-strip .session-strip-collapse {\n"
            "    order: -1;\n    transform: rotate(180deg);\n  }",
            self.block,
        )

    def test_the_fix_is_scoped_to_the_responsive_block_only(self) -> None:
        source = _css()
        outside = source.replace(self.block, "")
        self.assertNotIn(
            "[data-strip-col] .session-strip .session-strip-collapse "
            "{ display: none; }",
            outside,
        )


class ToggleBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_the_toggle_flips_the_class_and_persists_it(self) -> None:
        self.assertIn('const collapsed = strip.classList.toggle("collapsed");',
                      self.js)
        self.assertIn("LayoutManager.setStripState({ collapsed });", self.js)

    def test_click_to_expand_is_guarded_on_the_collapsed_class(self) -> None:
        # Below the breakpoint the class is absent, so the rail's
        # click-anywhere handler correctly does nothing.
        handler = self.js[self.js.index("// Collapsed rail: clicking anywhere"):]
        handler = handler[:handler.index("},")]
        self.assertIn('if (!strip.classList.contains("collapsed")) return;',
                      handler)

    def test_fresh_profile_default_is_deterministic(self) -> None:
        # One migration read of the legacy key, then the layout map owns
        # the state; a fresh profile therefore starts uncollapsed.
        self.assertIn(
            'if (localStorage.getItem("studio-strip-collapsed") === "true") {',
            self.js,
        )
        self.assertIn('strip.classList.add("collapsed");', self.js)

    def test_layout_sync_does_not_force_the_rail_while_expanded(self) -> None:
        self.assertIn(
            'el.classList.toggle("collapsed", !!strip.collapsed);', self.js
        )


class NoEmergencyOverrideTests(unittest.TestCase):
    """The forbidden shortcuts, pinned as absent."""

    def test_no_inline_width_is_assigned_to_the_strip(self) -> None:
        """No width is written onto the strip from script.

        Scoped to the strip: `style.width` is legitimate elsewhere in
        app.js (the progress fill, for one), so this checks the lines
        that touch the strip rather than the whole file.
        """

        source = APP_JS.read_text(encoding="utf-8")
        strip_object = source[source.index("const SessionStrip = {"):]
        strip_object = strip_object[:strip_object.index("\nconst LayoutSwitcher")]
        self.assertNotIn("style.width", strip_object)
        self.assertNotIn('setAttribute("style"', strip_object)
        offenders = [
            line.strip() for line in source.splitlines()
            if "style.width" in line
            and re.search(r"strip|sessionStrip", line, re.IGNORECASE)
        ]
        self.assertEqual([], offenders)
        adapter = (FRONTEND / "studio-model-controls.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("style.width", adapter)

    def test_the_strip_rules_use_no_important_override(self) -> None:
        source = _css()
        for match in re.finditer(r"\.session-strip[^{]*\{([^}]*)\}", source):
            with self.subTest(rule=match.group(0)[:60]):
                self.assertNotIn("!important", match.group(1))

    def test_no_duplicate_strip_or_floating_gallery_returned(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertEqual(1, html.count('id="sessionStrip"'))
        self.assertNotIn("studio-results-panel", html)
        self.assertFalse((FRONTEND / "studio-results-panel.js").exists())


if __name__ == "__main__":
    unittest.main()
