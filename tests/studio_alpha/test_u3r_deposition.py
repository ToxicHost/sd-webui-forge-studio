"""U3-R R1 — the soft-brush deposition repair.

THE DEFECT. Stamping a soft round tip takes MAX of overlapping falloffs, and the
max of two smoothsteps dips between their centres. On a straight 54 px stroke at
the shipped spacing that measured 16.6% alpha ripple at hardness 0.25, with the
beads reaching the CENTRELINE at hardness 0 -- which is what the owner
photographed and what made them call V2 not ready to widen.

THE REPAIR IS ONE REMOVED CONDITION. `rendererFor` offered its analytic sweep
only to tips with `hardness >= 0.99`. That was policy, not mathematics: `sweep`
evaluates `shapeAt(distanceToSegment / r)`, which for a round procedural tip IS
the continuous limit of an infinitely dense stamp train. Textured, scattered and
anisotropic tips still stamp, because those are what DiVerdi's "a swept contour
with a constant fill loses the natural media quality" warning is actually about.

AND THE PART THAT IS EASY TO MISS -- `overlapK`. It counts contributions covering
one pixel so `fEff` can divide by them. A stamp train catches a pixel at a
different point on the falloff each time, so the weight is `profileMean`. A
sweep catches it at the SAME perpendicular distance every time, so the weight is
1. Without that correction a swept Flow-0.5 stroke deposited 144 of 255 where the
train deposits 127. The renderer is therefore chosen BEFORE the deposition.

TWO CANDIDATES WERE REJECTED ON MEASUREMENTS, not on taste:

  enabling the union at Flow 1   fixed the ripple and distorted the cross-section
                                 by up to 62 of 255 at hardness 0, flattening
                                 soft tips toward hard. Legacy carries the same
                                 short-circuit and its comment records the same
                                 finding from its own measurement.
  Legacy's hardness-scaled       exact, and costs 2.5x to 8.3x the marks. Legacy
  spacing law                    pays it; the sweep gets the same answer
                                 analytically for nothing.

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

EXPECTED_U3R_TESTS = 35

PROBE = Path(__file__).with_name("u3r_deposition_probe.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
COVERAGE = FRONTEND / "v2" / "coverage.js"
ADAPTER = FRONTEND / "v2" / "canvas-adapter.js"

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


def ripple_at(hardness: float) -> dict:
    for r in probe()["ripple"]:
        if r["hardness"] == hardness:
            return r
    raise AssertionError(f"no ripple row for hardness {hardness}")


def worst_ripple(hardness: float) -> float:
    r = ripple_at(hardness)
    return max(r["centreline"]["pct"], r["r35"]["pct"],
               r["r60"]["pct"], r["r80"]["pct"])


class RendererSelectionTests(unittest.TestCase):
    """A sweep is for round procedural tips. Texture still stamps."""

    def rows(self) -> list[dict]:
        return probe()["rendererSelection"]

    def test_a_soft_round_tip_now_sweeps(self) -> None:
        for row in self.rows():
            with self.subTest(hardness=row["hardness"]):
                self.assertEqual("sweep", row["round"])

    def test_a_hard_round_tip_still_sweeps(self) -> None:
        hard = [r for r in self.rows() if r["hardness"] >= 0.99]
        self.assertTrue(hard)
        for row in hard:
            self.assertEqual("sweep", row["round"])

    def test_a_textured_tip_still_stamps(self) -> None:
        # DiVerdi's warning is about texture, and this is the line that keeps it.
        for row in self.rows():
            with self.subTest(hardness=row["hardness"]):
                self.assertEqual("stamp", row["textured"])

    def test_a_scattered_tip_still_stamps(self) -> None:
        for row in self.rows():
            with self.subTest(hardness=row["hardness"]):
                self.assertEqual("stamp", row["scattered"])

    def test_an_anisotropic_tip_still_stamps(self) -> None:
        for row in self.rows():
            with self.subTest(hardness=row["hardness"]):
                self.assertEqual("stamp", row["anisotropic"])


class LongitudinalRippleTests(unittest.TestCase):
    """§10's gate, measured through the real adapter on the saved fixture."""

    def test_hardness_zero_centreline_is_flat(self) -> None:
        # The owner's screenshot: beads reaching the spine. Was 8.2% in the
        # browser and 6% on this fixture; §10 asks for <= 5%.
        self.assertLessEqual(ripple_at(0)["centreline"]["pct"], 5.0)

    def test_hardness_zero_is_flat_everywhere_across_the_width(self) -> None:
        self.assertLessEqual(worst_ripple(0), 5.0)

    def test_hardness_quarter_meets_the_eight_percent_gate(self) -> None:
        self.assertLessEqual(worst_ripple(0.25), 8.0)

    def test_hardness_half_meets_the_eight_percent_gate(self) -> None:
        self.assertLessEqual(worst_ripple(0.5), 8.0)

    def test_the_default_line_art_hardness_does_not_regress(self) -> None:
        self.assertLessEqual(worst_ripple(0.85), 1.0)

    def test_a_hard_tip_is_still_flat(self) -> None:
        self.assertLessEqual(worst_ripple(1), 1.0)


class CrossSectionalFidelityTests(unittest.TestCase):
    """§10: low ripple must not be bought by flattening every hardness.

    This is the gate the union candidate failed -- it shifted the falloff by up
    to 62 of 255 at hardness 0, turning a soft brush into a nearly hard one.
    """

    def test_each_hardness_keeps_a_distinct_profile(self) -> None:
        profiles = {r["hardness"]: tuple(r["crossSection"])
                    for r in probe()["ripple"]}
        self.assertEqual(len(profiles), len(set(profiles.values())),
                         "two hardnesses produced the same cross-section")

    def test_a_hard_tip_is_a_flat_disc(self) -> None:
        cross = ripple_at(1)["crossSection"]
        inside = [v for v in cross if v > 0]
        self.assertTrue(all(v == 255 for v in inside), cross)

    def test_softness_increases_monotonically_as_hardness_falls(self) -> None:
        # Alpha at 40% of the radius must fall as the tip softens.
        at40 = [(r["hardness"], r["crossSection"][4]) for r in probe()["ripple"]]
        at40.sort(key=lambda p: -p[0])
        values = [v for _, v in at40]
        self.assertEqual(values, sorted(values, reverse=True), at40)

    def test_the_softest_tip_still_falls_off_to_nothing(self) -> None:
        cross = ripple_at(0)["crossSection"]
        self.assertGreater(cross[0], 200)
        self.assertEqual(0, cross[-1])

    def test_the_soft_profile_is_not_flattened_toward_hard(self) -> None:
        # The rejected union candidate raised this sample from 138 to 200.
        # The swept form leaves it at the stamp train's value.
        self.assertLess(ripple_at(0)["crossSection"][4], 170)


class FlowFidelityTests(unittest.TestCase):
    """The swept overlap weight must not change what one pass deposits."""

    def rows(self) -> list[dict]:
        return probe()["flowFidelity"]

    def test_full_flow_still_reaches_full_alpha(self) -> None:
        for r in self.rows():
            if r["flow"] == 1:
                with self.subTest(hardness=r["hardness"]):
                    self.assertGreaterEqual(r["centreAlpha"], 253)

    def test_reduced_flow_deposits_what_it_asks_for(self) -> None:
        # Without the swept overlap correction this read 144 where 127 is due.
        for r in self.rows():
            with self.subTest(hardness=r["hardness"], flow=r["flow"]):
                self.assertLessEqual(abs(r["centreAlpha"] - r["expectedApprox"]), 6)

    def test_flow_is_monotonic(self) -> None:
        for hardness in (0.85, 0.5, 0):
            rows = sorted([r for r in self.rows() if r["hardness"] == hardness],
                          key=lambda r: r["flow"])
            alphas = [r["centreAlpha"] for r in rows]
            self.assertEqual(alphas, sorted(alphas), f"hardness {hardness}")

    def test_soft_tips_are_flat_at_every_flow(self) -> None:
        for r in self.rows():
            if r["hardness"] <= 0.5:
                with self.subTest(hardness=r["hardness"], flow=r["flow"]):
                    self.assertLessEqual(r["worstRipplePct"], 8.0)


class StampedTipFlowTests(unittest.TestCase):
    """The stamp train keeps ITS overlap weight, and this class exists because a
    mutation proved nothing was watching.

    Round tips sweep now, so every other flow test above exercises the swept
    weight. A mutation that handed the swept weight to EVERY renderer therefore
    survived the whole suite -- textured, scattered and anisotropic tips still
    stamp, and nothing measured them. `overlapK` for a stamp train must stay
    `(2/step) * profileMean(hardness)`, because each dab catches a pixel at a
    different point on the falloff.
    """

    def rows(self) -> list[dict]:
        return probe()["stampedFlowFidelity"]

    def test_a_stamped_tip_deposits_what_its_flow_asks_for(self) -> None:
        for r in self.rows():
            with self.subTest(hardness=r["hardness"], flow=r["flow"]):
                self.assertLessEqual(abs(r["centreAlpha"] - r["expectedApprox"]), 6)

    def test_the_stamped_overlap_weight_still_varies_with_hardness(self) -> None:
        # The swept weight is a constant `2/step`; the stamped one is not. If
        # these ever collapse to one value the weights have been merged.
        ks = {r["hardness"]: r["overlapK"] for r in self.rows()}
        self.assertEqual(len(set(ks.values())), len(ks), ks)

    def test_a_stamped_tip_reaches_full_alpha_at_full_flow(self) -> None:
        for r in self.rows():
            if r["flow"] == 1:
                with self.subTest(hardness=r["hardness"]):
                    self.assertGreaterEqual(r["centreAlpha"], 253)


class DispatchInvarianceTests(unittest.TestCase):
    """§9: the same path must land identically however the browser groups it."""

    def test_grouping_does_not_change_a_single_pixel(self) -> None:
        d = probe()["dispatchInvariance"]
        self.assertTrue(d["identical"], d)

    def test_grouping_does_not_change_the_mark_count(self) -> None:
        d = probe()["dispatchInvariance"]
        counts = {d["onePerEvent"]["marks"], d["threePerEvent"]["marks"],
                  d["allInOne"]["marks"]}
        self.assertEqual(1, len(counts), d)


class TapAndBoundsTests(unittest.TestCase):
    def test_a_tap_still_places_exactly_one_mark(self) -> None:
        # A tap has no segment, so it cannot sweep -- it must stamp, and the
        # repair must not have changed a dot.
        self.assertEqual(1, probe()["tap"]["marks"])

    def test_a_tap_keeps_its_soft_footprint(self) -> None:
        tap = probe()["tap"]
        self.assertGreater(tap["centre"], 240)
        self.assertGreater(tap["atR60"], 0)
        self.assertEqual(0, tap["outsideRim"])

    def test_work_stays_bounded_to_the_stroke(self) -> None:
        b = probe()["bounded"]
        self.assertLess(b["sharePct"], 100.0)
        self.assertGreater(b["documentPixels"], b["area"])

    def test_the_mark_count_did_not_explode(self) -> None:
        # §11: no 3x-7x workaround. The sweep adds no marks at all.
        for r in probe()["ripple"]:
            with self.subTest(hardness=r["hardness"]):
                self.assertLessEqual(r["marks"], 95)


class DepositionContractTests(unittest.TestCase):
    """Spelling checks, labelled as such, on the two seams that must not drift."""

    def setUp(self) -> None:
        self.coverage = code_of(COVERAGE)
        self.adapter = code_of(ADAPTER)

    def test_the_renderer_no_longer_gates_on_hardness(self) -> None:
        # Behaviourally covered by RendererSelectionTests; this catches a
        # reintroduction by a future edit that also changes the probe.
        self.assertNotIn("hardness === undefined ? 0 : t.hardness) >= 0.99",
                         self.coverage)

    def test_the_swept_overlap_weight_exists(self) -> None:
        """A sweep's along-travel weight is 1; a stamp train's is the profile's
        mean. U3-G gave `profileMean` a second argument, because a gaussian's
        mean is a numeric integral rather than `(1 + h) / 2` -- so the spelling
        moved while the contract this pins did not."""

        self.assertIn("swept ? 1 : profileMean(hardness, profile)",
                      self.coverage)

    def test_the_adapter_chooses_the_renderer_before_the_deposition(self) -> None:
        i = self.adapter.index("rendererFor(")
        j = self.adapter.index("depositionFor(")
        self.assertLess(i, j, "deposition must know which renderer will run")

    def test_the_adapter_tells_the_deposition_which_renderer_won(self) -> None:
        self.assertIn("swept: renderer === V.RENDER_SWEEP", self.adapter)

    def test_the_spacing_default_was_not_quietly_tightened(self) -> None:
        # §11: primary spacing stays 0.15, and the repair is the renderer rather
        # than a smaller gap. Legacy reaches smoothness by scaling spacing with
        # hardness -- `presetSpacing * (0.3 + 0.7 * hardness)` -- and that law is
        # deliberately NOT copied here, because it costs 2.5x to 8.3x the marks
        # for a result the sweep obtains analytically.
        self.assertIn("0.15", self.adapter)
        self.assertNotIn("0.3 + 0.7", self.adapter)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3R_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
