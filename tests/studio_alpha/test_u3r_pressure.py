"""U3-R R2 — the pressure-target contract.

THE DEFECT. `describeStroke` read only `S.pressureSensitivity` and mapped it to
width, ignoring `S.pressureAffects`. An owner who chose `opacity` got WIDTH --
the one case where a setting does the wrong thing rather than nothing -- and two
of the four documented combinations did not exist at all.

WHAT `opacity` MEANS, established from source rather than chosen. Legacy's `pOp`
scales `S.brushFlow`, and its comment gives the reason: the brush composites its
whole stroke at `S.brushOpacity` once at commit, so the per-stamp value is FLOW,
an accumulation rate, and using opacity per stamp would apply it twice. V2's
`merge` applies opacity exactly once for the same reason, so pressure-to-opacity
maps to the per-mark deposition amplitude.

WHY THE TWO DIMENSIONS ARE MEASURED SEPARATELY. A wider stroke also lays more
total paint, so a total-ink measurement cannot tell width from opacity -- which
is exactly how "opacity secretly uses width" would hide. The probe reads the
painted extent perpendicular to travel for width, and the alpha ON the
centreline for opacity, where the tip is at full coverage regardless of radius.

Review: `Evidence/source-review/U3R-soft-deposition.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_U3R_PRESSURE_TESTS = 35

PROBE = Path(__file__).with_name("u3r_pressure_probe.js")
ADAPTER = APP_ROOT / "forge_studio" / "frontend" / "v2" / "canvas-adapter.js"

_RESULT: dict | None = None


def probe() -> dict:
    global _RESULT
    if _RESULT is None:
        out = subprocess.run(["node", str(PROBE)], capture_output=True,
                             text=True, check=False, cwd=str(APP_ROOT))
        if out.returncode != 0:
            raise AssertionError(f"probe failed: {out.stderr[-2000:]}")
        _RESULT = json.loads(out.stdout)
    return _RESULT


def mode(name: str) -> dict:
    return probe()["modes"][name]


class TheFourCombinationsTests(unittest.TestCase):
    """§15's table, each cell asserted on its own measurement."""

    def test_neither_varies_when_pressure_is_off(self) -> None:
        m = mode("neither")
        self.assertFalse(m["widthVaries"])
        self.assertFalse(m["alphaVaries"])

    def test_size_varies_width_only(self) -> None:
        m = mode("widthOnly")
        self.assertTrue(m["widthVaries"])
        self.assertFalse(m["alphaVaries"])

    def test_opacity_varies_opacity_only(self) -> None:
        # The defect: this used to vary WIDTH and leave alpha flat.
        m = mode("opacityOnly")
        self.assertTrue(m["alphaVaries"])
        self.assertFalse(m["widthVaries"])

    def test_both_varies_both(self) -> None:
        m = mode("both")
        self.assertTrue(m["widthVaries"])
        self.assertTrue(m["alphaVaries"])

    def test_sensitivity_off_beats_any_target(self) -> None:
        m = mode("offButTargetBoth")
        self.assertFalse(m["widthVaries"])
        self.assertFalse(m["alphaVaries"])

    def test_width_only_keeps_full_alpha(self) -> None:
        m = mode("widthOnly")
        self.assertEqual(255, m["alphaMin"])
        self.assertEqual(255, m["alphaMax"])

    def test_opacity_only_keeps_full_width(self) -> None:
        m = mode("opacityOnly")
        self.assertEqual(m["widthMin"], m["widthMax"])

    def test_opacity_only_does_not_secretly_use_width(self) -> None:
        # The two modes must not produce the same stroke.
        self.assertNotEqual(mode("opacityOnly")["widthMin"],
                            mode("widthOnly")["widthMin"])


class TheDescriptorTests(unittest.TestCase):
    """§16: the two dimensions are independent facts in the frozen descriptor."""

    def test_the_descriptor_carries_both_dimensions(self) -> None:
        spec = mode("both")["spec"]
        self.assertIn("pressureToWidth", spec)
        self.assertIn("pressureToFlow", spec)

    def test_each_mode_freezes_the_right_pair(self) -> None:
        cases = {
            "neither": (False, False),
            "widthOnly": (True, False),
            "opacityOnly": (False, True),
            "both": (True, True),
        }
        for name, (width, flow) in cases.items():
            spec = mode(name)["spec"]
            with self.subTest(mode=name):
                self.assertEqual(width, spec["pressureToWidth"])
                self.assertEqual(flow, spec["pressureToFlow"])

    def test_the_descriptor_records_its_provenance(self) -> None:
        spec = mode("opacityOnly")["spec"]
        self.assertEqual("opacity", spec["pressureAffects"])
        self.assertTrue(spec["pressureSensitivity"])

    def test_pressure_to_opacity_is_no_longer_reported_as_absent(self) -> None:
        # U3-V had to record it as "not-implemented". It is implemented now.
        spec = mode("both")["spec"]
        self.assertNotEqual("not-implemented", spec.get("pressureToFlow"))


class UnknownTargetTests(unittest.TestCase):
    """§16: an unrecognised value must not become width silently."""

    def test_an_unknown_target_changes_nothing(self) -> None:
        u = probe()["unknownTarget"]
        self.assertFalse(u["widthVaries"])
        self.assertFalse(u["alphaVaries"])

    def test_an_unknown_target_is_normalised_to_none(self) -> None:
        self.assertEqual("none", probe()["unknownTarget"]["spec"]["pressureAffects"])

    def test_a_missing_target_changes_nothing(self) -> None:
        m = probe()["missingTarget"]
        self.assertFalse(m["widthVaries"])
        self.assertFalse(m["alphaVaries"])

    def test_an_unknown_target_does_not_enable_either_dimension(self) -> None:
        spec = probe()["unknownTarget"]["spec"]
        self.assertFalse(spec["pressureToWidth"])
        self.assertFalse(spec["pressureToFlow"])


class FrozenAtBeginTests(unittest.TestCase):
    def test_changing_the_setting_mid_contact_does_not_alter_the_stroke(self) -> None:
        f = probe()["frozenAtBegin"]
        self.assertTrue(f["widthsMatch"])
        self.assertTrue(f["alphasMatch"])


class GroupingInvarianceTests(unittest.TestCase):
    """§18: coalesced-event grouping must not change the result."""

    def test_width_is_grouping_invariant_in_every_mode(self) -> None:
        for row in probe()["groupingInvariance"]:
            with self.subTest(target=row["target"]):
                self.assertTrue(row["widthsMatch"])

    def test_opacity_is_grouping_invariant_in_every_mode(self) -> None:
        for row in probe()["groupingInvariance"]:
            with self.subTest(target=row["target"]):
                self.assertTrue(row["alphasMatch"])


class ExtremePressureTests(unittest.TestCase):
    """§18: 0, low, medium and 1 must all be finite and bounded."""

    def test_every_pressure_produces_finite_bounded_coverage(self) -> None:
        for row in probe()["extremes"]:
            with self.subTest(pressure=row["pressure"]):
                self.assertTrue(row["allFinite"])

    def test_no_pressure_produces_a_negative_or_zero_radius(self) -> None:
        for row in probe()["extremes"]:
            with self.subTest(pressure=row["pressure"]):
                self.assertGreater(row["widthMin"], 0)

    def test_zero_pressure_still_marks(self) -> None:
        # A floor, not a vanishing act: a light touch must still paint.
        zero = [r for r in probe()["extremes"] if r["pressure"] == 0][0]
        self.assertGreater(zero["alphaMax"], 0)
        self.assertGreater(zero["marks"], 0)

    def test_pressure_response_is_monotonic_in_both_dimensions(self) -> None:
        rows = sorted(probe()["extremes"], key=lambda r: r["pressure"])
        widths = [r["widthMax"] for r in rows]
        alphas = [r["alphaMax"] for r in rows]
        self.assertEqual(widths, sorted(widths), widths)
        self.assertEqual(alphas, sorted(alphas), alphas)

    def test_full_pressure_reaches_the_unmodulated_stroke(self) -> None:
        full = [r for r in probe()["extremes"] if r["pressure"] == 1][0]
        self.assertEqual(255, full["alphaMax"])


class EraserTests(unittest.TestCase):
    """§18: paint and eraser use the intended common rule."""

    def test_the_eraser_honours_the_width_target(self) -> None:
        e = probe()["eraser"]["widthOnly"]
        self.assertTrue(e["widthVaries"])
        self.assertFalse(e["alphaVaries"])

    def test_the_eraser_honours_the_opacity_target(self) -> None:
        e = probe()["eraser"]["opacityOnly"]
        self.assertTrue(e["alphaVaries"])
        self.assertFalse(e["widthVaries"])


class ContractSpellingTests(unittest.TestCase):
    """Labelled spelling checks on the seams that must not silently drift."""

    def setUp(self) -> None:
        self.adapter = code_of(ADAPTER)

    def test_the_adapter_reads_the_owners_target(self) -> None:
        self.assertIn("S.pressureAffects", self.adapter)

    def test_the_known_targets_are_named_rather_than_assumed(self) -> None:
        for value in ("none", "size", "opacity", "both"):
            self.assertIn(f'"{value}"', self.adapter)

    def test_the_settings_are_read_once_at_begin_not_per_move(self) -> None:
        # §16: a mutable Canvas setting must not be reread on every move.
        i = self.adapter.index("function addFromEvent")
        self.assertNotIn("S.pressureAffects", self.adapter[i:])

    def test_the_flow_rule_targets_flow_and_not_size(self) -> None:
        self.assertIn('ruleFromFeel("flow", "pressure"', self.adapter)

    def test_the_deposition_cache_is_bounded(self) -> None:
        self.assertIn("FLOW_BUCKETS", self.adapter)

    def test_the_width_floor_setter_is_internal_like_the_flag(self) -> None:
        # It exists so the lightest-touch width can be chosen by painting. It
        # must stay the same category as `_setEnabled`: underscore-prefixed,
        # unpersisted, and absent from every settings surface.
        self.assertIn("_setPressureWidthFloor", self.adapter)
        self.assertNotIn("localStorage", self.adapter)
        self.assertNotIn("sessionStorage", self.adapter)

    def test_the_width_floor_default_is_still_the_shipped_one(self) -> None:
        # A reload returns to this. Changing the DEFAULT is a product decision
        # and belongs in its own commit, not in a test harness.
        self.assertIn("DEFAULT_PRESSURE_WIDTH_FLOOR = 0.35", self.adapter)

    def test_no_settings_surface_reads_the_width_floor(self) -> None:
        settings = APP_ROOT / "forge_studio" / "frontend" / "settings-page.js"
        if settings.is_file():
            self.assertNotIn("PressureWidthFloor", code_of(settings))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3R_PRESSURE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
