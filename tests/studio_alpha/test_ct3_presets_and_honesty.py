"""CT3 stages B and C: the preset contract, and three controls that stopped lying.

§8.3: "Use one `BrushPreset` contract. Wire the existing eight preset
definitions rather than creating a parallel format." They were defined at
`canvas-core.js:371`, exported, and read by NOTHING — and each one carried a
`flow`, which was a constant until stage A of this package.

§8.6: "Every currently visible instance must be implemented truthfully,
removed, or disabled with a specific reason. Do not leave it clickable and
document it only in Known Issues."

Three instances answered here:

```text
Custom Tip        DISABLED, owner's decision. `S.customTip.data` is null and
                  nothing writes it, so pressing it painted a plain Round.
mask Opacity/Flow REMOVED IN MASK MODE. `exportMask` binarises at alpha > 0,
mask Hardness     so no opacity or edge softness could survive the export.
```
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
LOCALES = FRONTEND / "locales"

EXPECTED_CT3BC_TESTS = 23


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


CORE_RAW = (FRONTEND / "canvas-core.js").read_text(encoding="utf-8")
CORE = _strip_js_comments(CORE_RAW)
UI = _strip_js_comments((FRONTEND / "canvas-ui.js").read_text(encoding="utf-8"))
HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")


def _preset_literal() -> str:
    start = CORE.index("const DEFAULT_BRUSH_PRESETS = [")
    # +2, not +3. The third character is the statement's semicolon, and
    # trailing it makes json.loads report "Extra data" a long way from the
    # actual cause.
    return CORE[start:CORE.index("\n];", start) + 2]


def _presets() -> list[dict]:
    """The definitions, parsed rather than pattern-matched.

    Reading the real values is the only way to assert things like "every
    preset's flow is inside the control's range" — a regex could only say the
    word `flow` appears.
    """

    literal = _preset_literal()
    body = literal[literal.index("["):]
    # JS object literal -> JSON: quote the bare keys, drop trailing commas.
    body = re.sub(r"(\w+):", r'"\1":', body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


class ThePresetContractHasAConsumerTests(unittest.TestCase):
    def test_the_definitions_are_read_by_something(self):
        """The assertion that would have caught eight dead definitions."""

        self.assertIn("function applyBrushPreset(", CORE)
        self.assertIn("DEFAULT_BRUSH_PRESETS.find(", CORE)

    def test_the_applier_is_exported_and_called(self):
        # BE14 added `BRUSH_PRESET_ALIASES` to the same export line, so the old
        # adjacency regex no longer matched. Both names are asserted, because
        # the alias table is what keeps a renamed preset resolving and an
        # export that lost it would fail silently.
        self.assertRegex(
            CORE, r"DEFAULT_BRUSH_PRESETS, BRUSH_PRESET_ALIASES, applyBrushPreset,")
        self.assertIn("C.applyBrushPreset(chosen)", UI)

    def test_the_picker_exists_and_is_populated_from_the_contract(self):
        self.assertIn('id="brushPresetPicker"', HTML)
        self.assertIn("DEFAULT_BRUSH_PRESETS", UI)
        self.assertRegex(UI, r'getElementById\("brushPresetPicker"\)')

    def test_the_picker_writes_names_as_text_not_markup(self):
        """A preset name is data. `textContent`, never `innerHTML`."""

        start = UI.index('getElementById("brushPresetPicker")')
        block = UI[start:start + 1200]
        self.assertIn("option.textContent = entry.name", block)
        self.assertNotIn("innerHTML", block)

    def test_choosing_a_preset_moves_the_controls_it_changed(self):
        """A picker that changed the stroke without moving the controls would
        be the same lie pointing the other way."""

        start = UI.index("C.applyBrushPreset(chosen)")
        block = UI[start:start + 700]
        self.assertIn("_syncCtxBar();", block)
        self.assertIn("_syncDynamicsPanel();", block)


class EveryPresetFieldReachesTheStrokeTests(unittest.TestCase):
    """§8.3: "Built-in presets must not advertise a custom tip, texture or
    dynamic that the engine ignores.\""""

    def setUp(self):
        self.presets = _presets()

    def test_there_are_presets_at_all(self):
        self.assertGreaterEqual(len(self.presets), 8)

    def test_every_declared_tip_is_one_the_engine_draws(self):
        drawable = {"round", "flat", "marker", "scatter"}
        for p in self.presets:
            with self.subTest(preset=p["name"]):
                self.assertIn(p["preset"], drawable)

    def test_no_preset_advertises_the_disabled_custom_tip(self):
        for p in self.presets:
            with self.subTest(preset=p["name"]):
                self.assertNotEqual("custom", p["preset"])

    def test_every_numeric_field_is_inside_its_control_range(self):
        """A preset that set a value the control cannot show would leave the
        bar disagreeing with the brush."""

        for p in self.presets:
            with self.subTest(preset=p["name"]):
                self.assertGreaterEqual(p["size"], 1)
                self.assertLessEqual(p["size"], 500)
                self.assertTrue(0 <= p["hardness"] <= 100)
                self.assertTrue(0 <= p["opacity"] <= 100)
                self.assertTrue(1 <= p["flow"] <= 100)
                self.assertTrue(0 <= p["smoothing"] <= 20)

    def test_every_dynamics_key_is_one_the_engine_reads(self):
        known = {"sizeJitter", "opacityJitter", "scatter", "rotationJitter",
                 "followStroke", "spacing"}
        for p in self.presets:
            with self.subTest(preset=p["name"]):
                self.assertTrue(set(p["dynamics"]) <= known,
                                f"unknown dynamics: {set(p['dynamics']) - known}")

    def test_the_applier_sets_buildup_for_every_preset_not_only_those_declaring_it(self):
        """Otherwise a preset that does not mention buildup inherits whatever
        the last one set, and "Hard Ink behaves differently depending on what
        you picked before it" is not a preset system."""

        body = CORE[CORE.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        self.assertIn("S.brushBuildup = !!p.buildup;", body)

    #: BE14 made Buildup a shipped BEHAVIOUR rather than one preset's quirk.
    #: Ink Wash pools where the hand lingers and Pastel builds as chalk does;
    #: both need accumulation within a single stroke.
    BUILDUP_PRESETS = {"Airbrush", "Ink Wash", "Pastel"}

    def test_only_the_declared_presets_declare_buildup(self):
        """WAS "exactly one", AND THE ORIGINAL SAID HOW TO CHANGE IT: "If a
        second one appears it should be a decision, not a copy-paste."

        BE14 made that decision. It also moved the flag INTO the table -- the
        brief requires every preset to explicitly reset every supported field,
        so the post-assignment that used to set Airbrush's is gone.

        Enumerated rather than loosened. This guard exists because buildup
        silently inherited between presets, and "some presets have it" would
        not catch that coming back; this list would.
        """

        declared = {p["name"] for p in self.presets if p.get("buildup")}
        self.assertEqual(self.BUILDUP_PRESETS, declared)
        self.assertNotIn(
            'DEFAULT_BRUSH_PRESETS.find(p => p.name === "Airbrush").buildup = true;',
            CORE,
            "the post-assignment is back, so the table no longer states every "
            "field it is required to state")

    def test_the_applier_writes_every_field_the_contract_declares(self):
        body = CORE[CORE.index("function applyBrushPreset("):]
        body = body[:body.index("\n}")]
        for field in ("S.brushPreset", "S.brushSize", "S.brushHardness",
                      "S.brushOpacity", "S.brushFlow", "S.brushBuildup",
                      "S.smoothing", "S.brushDynamics"):
            with self.subTest(field=field):
                self.assertIn(field + " =", body)


class CustomTipIsDisabledHonestlyTests(unittest.TestCase):
    def test_the_field_still_has_no_writer(self):
        """The premise. If someone builds the importer, this fails and the
        button should be re-enabled -- which is the right way round."""

        self.assertEqual(
            [], re.findall(r"S\.customTip\.data\s*=", CORE + UI),
            "customTip.data has a writer now; re-enable the control",
        )

    def test_the_button_is_disabled_rather_than_clickable(self):
        at = HTML.index('data-i18n-title="brushPreset.custom"')
        tag = HTML[HTML.rfind("<button", 0, at):HTML.index(">", at) + 1]
        self.assertIn("disabled", tag)

    def test_the_reason_is_shown_and_says_what_is_missing(self):
        """"Do not leave it clickable and document it only in Known Issues"
        cuts both ways: a disabled control still owes an explanation."""

        english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
        reason = english["brushPreset.custom"]
        self.assertIn("not available", reason.lower())
        self.assertIn("importer", reason.lower())

    def test_every_locale_carries_the_reason(self):
        """The tooltip is set from the locale, so a locale left at "Custom"
        would show the owner nothing."""

        for path in sorted(LOCALES.glob("*.json")):
            with self.subTest(locale=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                if "brushPreset.custom" not in data:
                    continue
                self.assertIn("importer", data["brushPreset.custom"].lower())

    def test_the_engine_side_is_kept(self):
        """Disabled is not deleted. The stamp branch stays, so the day a tip
        can be loaded there is nothing to rebuild."""

        self.assertIn('preset === "custom" && S.customTip.data', CORE)


class MaskModeDropsTheControlsItOverridesTests(unittest.TestCase):
    def test_the_export_really_does_binarise(self):
        """The premise for hiding them, checked rather than asserted."""

        body = CORE[CORE.index("function exportMask() {"):]
        body = body[:body.index("\n}")]
        self.assertRegex(
            body,
            r"if \(d\[i \+ 3\] > 0\) \{ od\[i\] = 255; od\[i \+ 1\] = 255; "
            r"od\[i \+ 2\] = 255; od\[i \+ 3\] = 255; \}",
            "exportMask no longer binarises; the mask controls may now be "
            "able to mean something, so revisit hiding them",
        )

    def test_the_three_controls_are_removed_in_mask_mode(self):
        start = UI.index("const visible = new Set(show[t]")
        block = UI[start:start + 700]
        self.assertIn("if (S.editingMask) {", block)
        for key in ("opacity", "flow", "hardness"):
            with self.subTest(control=key):
                self.assertIn(f'visible.delete("{key}");', block)

    def test_size_and_smoothing_survive_because_they_do_reach_the_mask(self):
        """Removing controls that WORK would be the opposite error."""

        start = UI.index("const visible = new Set(show[t]")
        block = UI[start:start + 700]
        self.assertNotIn('visible.delete("size")', block)
        self.assertNotIn('visible.delete("smoothing")', block)

    def test_toggling_mask_mode_re_applies_the_bar(self):
        """Without this the controls only appear or disappear the next time
        the owner changes tool -- a bar describing the previous mode."""

        body = UI[UI.index("function toggleMaskMode() {"):]
        body = body[:body.index("\n}")]
        self.assertIn("setTool(S.tool);", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_CT3BC_TESTS, found)


if __name__ == "__main__":
    unittest.main()
