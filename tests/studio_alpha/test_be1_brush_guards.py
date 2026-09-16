"""BE1: guards that make a dead brush control impossible to report as wired.

WHY THESE EXECUTE INSTEAD OF READING SOURCE.

Most frontend guards in this suite read source text, because most of what they
protect is structural. These cannot. The defect they exist to catch is a
control that is present, assigned, persisted, passed to the engine -- and
changes no pixels. Every text-based check passes on that code.

So these load the REAL `canvas-core.js` into Node and stamp into a real
`Uint8Array`. The file has zero module-scope browser references, so the shipped
engine runs headless behind a two-line `window` shim. The addendum forbids "a
test that compares a renderer against output produced by the same incomplete
renderer": a Python transcription of the dab maths would agree with the
engine's bugs, so the engine itself is executed and INDEPENDENT properties of
its pixels are asserted.

THE ERROR THIS PACKAGE EXISTS TO PREVENT.

`brushAngle` was verified in CT3e using a flat tip -- the one tip family whose
shape function reads the angle -- and reported as wired. It is dead on round
and scatter, which is eight of the ten shipping presets. The owner found it by
painting. Every matrix test below therefore measures each control against EVERY
tip family separately; proving one representative and assuming the rest is the
exact mistake.

EXPECTED FAILURES ARE DELIBERATE.

Several guards describe behaviour a later BE package will deliver. They are
marked `expectedFailure` with the owning package named, so the suite stays
green and the defect stays documented. When the fix lands, unittest reports an
unexpected success as a FAILURE -- which forces whoever fixed it to come back
here and promote the guard. That is the point: a defect cannot be quietly
fixed without updating its own guard.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DRIVER = Path(__file__).with_name("be1_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE1_TESTS = 33

#: Filled by `setUpModule`. One Node process answers every assertion, because
#: spawning per test would pay the engine's load cost thirty times.
M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise AssertionError(
            f"the BE1 driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


@needs_node
class TheMeasurementIsTrustworthyTests(unittest.TestCase):
    """Before believing any verdict, prove the instrument works.

    The first version of this driver reported all five controls working on all
    four tips, which is false. The engine calls `Math.random()` for scatter
    placement, jitter and the density skip, so two stamps at IDENTICAL settings
    differed -- and a naive "did the output change?" comparison answered
    "works" every time. A guard that cannot fail is worse than no guard.
    """

    def test_the_engine_actually_loaded(self):
        # BE14 rebuilt the set from ten to sixteen. Pinned rather than
        # loosened: an accidental deletion should still be loud here.
        self.assertGreater(M["engine"]["exports"], 100)
        self.assertEqual(16, M["engine"]["presets"])

    def test_identical_settings_produce_identical_pixels(self):
        """If this fails, every verdict in this file is meaningless."""

        for tip, deterministic in M["selfCheck"].items():
            if tip == "allDeterministic":
                continue
            with self.subTest(tip=tip):
                self.assertTrue(
                    deterministic,
                    f"stamping {tip} twice at the same settings differed; "
                    "the comparison cannot distinguish a working control from "
                    "a random one")

    def test_the_jitter_heavy_preset_is_also_pinned(self):
        self.assertTrue(M["selfCheck"]["scatterDustPreset"])


@needs_node
class PresetResetTests(unittest.TestCase):
    """A preset must own every field it can influence.

    Six character fields are not written by `applyBrushPreset`, and they are
    also saved and restored across sessions (`canvas-ui.js` :125-130 and
    :152-157). So a Ratio nudged weeks ago still bends every round preset
    today, and the same preset name paints differently on two machines.
    """

    CORE_FIELDS = ("brushSize", "brushHardness", "brushOpacity",
                   "brushFlow", "brushBuildup", "smoothing")
    CHARACTER_FIELDS = ("brushAngle", "brushRatio", "brushSpikes",
                        "brushDensity", "brushFalloff", "brushTaperIn")

    def test_the_core_fields_are_reset(self):
        for field in self.CORE_FIELDS:
            with self.subTest(field=field):
                self.assertFalse(
                    M["presetLeaks"][field],
                    f"S.{field} survived applyBrushPreset")

    def test_every_character_field_is_named_in_the_leak_report(self):
        """The report must cover them whether or not they currently leak, so
        a field cannot drop out of scrutiny by being renamed."""

        for field in self.CHARACTER_FIELDS:
            self.assertIn(field, M["presetLeaks"])

    def test_the_character_fields_are_reset(self):
        """WAS AN expectedFailure OWNED BY BE14, AND BE14 PROMOTED IT.

        The original note said: "Currently fails for all six. When a preset
        gains explicit resets this becomes an unexpected success, the suite
        goes red, and the decorator must be removed -- which is how the guard
        gets promoted rather than forgotten."

        That is exactly what happened. The six were `brushRatio`,
        `brushSpikes`, `brushDensity`, `brushAngle`, `brushTaperIn` and
        `brushFalloff` -- and they turned out to be exactly the six BE6 had
        proved alive by rendering and exactly the six no shipped preset had
        ever set. One defect, not two coincidences: they leaked, so no preset
        dared use them, so the set felt flat.
        """

        leaked = [f for f in self.CHARACTER_FIELDS if M["presetLeaks"][f]]
        self.assertEqual([], leaked, f"these survive a preset change: {leaked}")


@needs_node
class PerTipSupportMatrixTests(unittest.TestCase):
    """Each control against EVERY tip family, measured separately.

    The corrected matrix from BE0, reproduced here by execution rather than
    quoted. `works` means two settings produced different pixels.
    """

    def test_falloff_reaches_every_tip(self):
        for tip in ("round", "flat", "marker", "scatter"):
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushFalloff"])

    def test_ratio_reaches_round(self):
        """BE0 corrected an earlier claim that Ratio was dead on round. It is
        not: `shapeDistRound` divides dy by the ratio before the norm, and the
        ellipse survives. Only the spike fold is annihilated."""

        self.assertEqual("works", M["supportMatrix"]["round"]["brushRatio"])

    def test_angle_reaches_the_directional_tips(self):
        for tip in ("flat", "marker"):
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushAngle"])

    def test_density_reaches_the_standard_branch(self):
        for tip in ("round", "flat", "marker"):
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushDensity"])

    def test_the_scatter_branch_now_shares_the_tip_contract(self):
        """FIXED BY BE6, rewritten from the guard that pinned the defect.

        This used to assert that Ratio, Spikes, Angle and Density were all DEAD
        on scatter -- a fact pinned deliberately so the fix would have something
        to move. The scatter branch measured its own inline circular distance
        and skipped the density test entirely, so the one preset whose whole
        character is stipple density could not see the Density control.

        Scatter now runs the shared coverage loop with the shared tip frame.
        Angle and Spikes stay `needs-shape` here for the same reason they do on
        round: they act on a circle's orientation, and a circle has none.
        """

        for control in ("brushRatio", "brushDensity"):
            with self.subTest(control=control):
                self.assertEqual("works", M["supportMatrix"]["scatter"][control])
        for control in ("brushAngle", "brushSpikes"):
            with self.subTest(control=control, note="needs a non-circular tip"):
                self.assertEqual("works", M["needsShapeMatrix"]["scatter"][control])

    def test_angle_reaches_round(self):
        """FIXED BY BE6, promoted from expectedFailure.

        THIS IS THE GUARD THAT WOULD HAVE CAUGHT THE CT3e ERROR: `brushAngle`
        was verified on a flat tip and reported wired, while `shapeDistRound`
        took no angle parameter at all -- and round is seven of the ten presets.

        Measured at Ratio 0.4. On a circle, rotating changes nothing and no
        implementation can make it otherwise; asserting it there would be
        asserting a falsehood to make a table look tidy.
        """

        self.assertEqual("works", M["needsShapeMatrix"]["round"]["brushAngle"])

    def test_ratio_reaches_the_flat_family(self):
        """FIXED BY BE6, promoted from expectedFailure.

        Flat hardcoded `ry = r * 0.3` and Marker `0.8 / 0.35`, so neither read
        brushRatio at all. Those are now the tips' declared ASPECT, which Ratio
        modulates -- and because Ratio 1.0 leaves the aspect alone, both tips
        are byte-identical at neutral.
        """

        for tip in ("flat", "marker"):
            with self.subTest(tip=tip):
                self.assertEqual("works", M["supportMatrix"][tip]["brushRatio"])

    def test_spikes_reaches_an_anisotropic_tip(self):
        """FIXED BY BE6, promoted from expectedFailure.

        Measured on a round tip at Ratio 0.4, because on a circle the answer is
        mathematically "no" and always will be. See SpikesTests.
        """

        self.assertEqual("works", M["needsShapeMatrix"]["round"]["brushSpikes"])


@needs_node
class SpikesTests(unittest.TestCase):
    """The one intentional no-difference case, asserted WITH its reason.

    `shapeDistRound` applies anisotropy, then folds the angle, then takes an
    isotropic norm. Rotation preserves a norm, so the fold changes nothing --
    whatever the ratio. Spikes is not merely unwired; it is algebraically
    incapable of doing anything in the current order of operations.

    BE6's fix is to fold FIRST, in the tip's circular local frame, and apply
    anisotropy AFTER. Then the fold lands on a shape that rotation does not
    preserve, and Spikes becomes visible.
    """

    def test_spikes_is_neutral_on_a_perfect_circle(self):
        """Correct and must stay correct: a circle has no orientation, so
        folding its angle cannot change which pixels it covers. This is the
        'prove intentional no-difference cases explicitly' requirement."""

        self.assertTrue(M["spikes"]["neutralOnCircle"])

    def test_spikes_bites_once_the_tip_is_not_circular(self):
        """FIXED BY BE6, promoted from expectedFailure.

        Was false even at Ratio 0.4, because the norm was taken last and was
        isotropic -- so the fold could not survive it whatever the aspect. BE6
        folds BEFORE applying anisotropy, and the spikes appear.
        """

        self.assertTrue(M["spikes"]["bitesOnEllipse"])


@needs_node
class SelectionIsAnAmountTests(unittest.TestCase):
    """Selection weight must bound a stroke, not accumulate into it.

    It is pre-multiplied into every dab before accumulation. On a max-blended
    hard tip that is harmless -- 50% stays 50%. On an accumulating soft tip the
    repeated multiply turns the bound into a RATE.
    """

    def test_a_hard_tip_treats_selection_as_an_amount(self):
        self.assertEqual(255, M["selection"]["hard_100"])
        self.assertEqual(128, M["selection"]["hard_50"])
        self.assertEqual(64, M["selection"]["hard_25"])

    def test_a_hard_tip_bound_does_not_rise_with_dab_count(self):
        self.assertEqual(
            M["selection"]["hard_50_few"], M["selection"]["hard_50_many"])

    def test_a_soft_tip_honours_selection_at_all(self):
        """FIXED BY BE4, promoted from expectedFailure.

        Was 102 / 102 / 102 at 100 / 50 / 25 percent -- the selection reached
        nothing, because it was multiplied into each dab before the soft path
        clamped at the flow ceiling. Now 102 / 51 / 26: applied once at merge,
        where it bounds the accumulated coverage instead of each contribution
        to it.
        """

        self.assertNotEqual(
            M["selection"]["soft_100"], M["selection"]["soft_50"])
        self.assertNotEqual(
            M["selection"]["soft_50"], M["selection"]["soft_25"])

    def test_a_soft_tip_bound_does_not_rise_with_dab_count(self):
        """FIXED BY BE4, promoted from expectedFailure.

        The amount-becomes-a-rate defect as a number: 92 at three dabs, 102 at
        sixty, so painting longer defeated the selection. Now 51 either way.
        """

        # AS A RATIO since BE17: accumulation makes the mark itself darker at
        # 60 dabs than at 3, so equality of the bounded values stopped being a
        # statement about the selection. Half the selection must still be half
        # the mark. The full rationale is on BE4's copy of this guard.
        few = M["selection"]["soft_50_few"] / M["selection"]["soft_100_few"]
        many = M["selection"]["soft_50_many"] / M["selection"]["soft_100_many"]
        self.assertAlmostEqual(few, many, delta=0.02)
        self.assertAlmostEqual(0.5, few, delta=0.02)

    def test_the_soft_bound_is_proportional(self):
        """New with BE4. The bound must SCALE, not merely differ -- half a
        selection is half the coverage, within one step of 8-bit rounding."""

        full = M["selection"]["soft_100"]
        self.assertAlmostEqual(full * 0.50, M["selection"]["soft_50"], delta=1)
        self.assertAlmostEqual(full * 0.25, M["selection"]["soft_25"], delta=1)


@needs_node
class PresetConstructionTests(unittest.TestCase):
    """Ask the ENGINE what a preset is, never the source text.

    `Airbrush.buildup = true` is applied by a statement AFTER the array
    literal. A parser that reads only the literal reports Airbrush as
    Buildup-off, and that is precisely how "removing the flow ceiling fixes
    Airbrush" came to be claimed -- the ceiling is already 255 for it.
    """

    def test_airbrush_is_built_with_buildup_on(self):
        self.assertTrue(M["construction"]["Airbrush"]["buildup"])

    #: BE14 made Buildup a shipped BEHAVIOUR rather than a single preset's
    #: quirk. Ink Wash pools where the hand lingers and Pastel builds as chalk
    #: does; both need accumulation within one stroke.
    #:
    #: Enumerated rather than loosened. The original guard existed because
    #: buildup silently inherited between presets, and "some presets have it"
    #: would not catch that coming back -- this list would.
    BUILDUP_PRESETS = {"Airbrush", "Ink Wash", "Pastel"}

    def test_only_the_declared_presets_are_built_with_buildup_on(self):
        for name, built in M["construction"].items():
            with self.subTest(preset=name):
                self.assertEqual(name in self.BUILDUP_PRESETS, built["buildup"])

    def test_every_preset_declares_an_explicit_buildup(self):
        for name, built in M["construction"].items():
            with self.subTest(preset=name):
                self.assertIsInstance(built["buildup"], bool)

    def test_the_tip_families_are_the_ones_the_matrix_covers(self):
        tips = {b["tip"] for b in M["construction"].values()}
        self.assertTrue(
            tips.issubset({"round", "flat", "marker", "scatter"}),
            f"a preset uses a tip the support matrix does not measure: {tips}")

    def test_flow_is_within_range_for_every_preset(self):
        for name, built in M["construction"].items():
            with self.subTest(preset=name):
                self.assertGreater(built["flow"], 0.0)
                self.assertLessEqual(built["flow"], 1.0)


@needs_node
class PresetsAreDistinctTests(unittest.TestCase):
    """Nearest-neighbour presets must produce different pixels.

    The owner's complaint was that ten presets 'functionally look identical'.
    This is the objective half of that: two presets whose single dab is
    byte-identical are two names for one brush.
    """

    def test_no_two_presets_stamp_identical_pixels(self):
        self.assertEqual(
            [], M["distinctness"]["collisions"],
            "these preset pairs produce identical coverage: "
            f"{M['distinctness']['collisions']}")

    def test_every_preset_paints_something(self):
        for name, fp in M["distinctness"]["fingerprints"].items():
            with self.subTest(preset=name):
                self.assertGreater(fp["count"], 0, f"{name} painted nothing")


@needs_node
class SizeTests(unittest.TestCase):
    """Size is relative to the document short side, and BE2 owns the contract.

    Pinned here so BE2's change is visible rather than silent.
    """

    def test_a_one_pixel_mark_is_already_reachable(self):
        """The superseded BE2 claimed the `Math.max(2, ...)` floor prevented a
        one-pixel mark. It does not: size 1 paints exactly one pixel, floor
        intact. BE2 was rewritten because of this measurement."""

        smallest = M["size"][0]
        self.assertEqual(1, smallest["requested"])
        self.assertEqual(1, smallest["widthPx"])

    def test_size_is_monotonic(self):
        widths = [r["widthPx"] for r in M["size"]]
        self.assertEqual(sorted(widths), widths,
                         f"a larger Size painted a smaller mark: {widths}")

    def test_every_size_step_changes_the_mark(self):
        """WAS AN expectedFailure ATTRIBUTED TO BE2, AND BE13 FLIPPED IT.

        The original note blamed "the 1.5-power relative curve", and that was
        already wrong when BE2 landed: this probe applies the PIXEL preset, so
        it measures literal document pixels and no relative curve is involved
        at all. What actually collapsed the steps was even diameters -- at an
        integer centre a diameter-d dab covers 2*ceil(d/2)-1 cells, so Size 2
        painted 1 and Size 4 painted 3, and the width list had duplicates.

        BE13 gave the aliased path cell-centre placement: an odd diameter is
        symmetric about an integer index, an even one about a half-integer.
        Measured 1/2/3/4/5/6/8/12 for the same requests.

        The misattribution is worth leaving on the record. An expectedFailure
        carries a claim about WHY, and this one was believed for two packages
        because nothing re-derives a docstring.
        """

        widths = [r["widthPx"] for r in M["size"]]
        self.assertEqual(len(set(widths)), len(widths),
                         f"distinct Sizes produced identical marks: {widths}")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE1_TESTS, found)

    def test_the_driver_and_the_engine_both_exist(self):
        self.assertTrue(DRIVER.exists(), f"missing BE1 driver at {DRIVER}")
        self.assertTrue(CORE.exists(), f"missing engine at {CORE}")


if __name__ == "__main__":
    unittest.main()
