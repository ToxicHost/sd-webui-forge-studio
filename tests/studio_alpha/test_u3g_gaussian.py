"""U3-G — the gaussian falloff.

Airbrush, Ink Wash and Charcoal declare `falloff: "gaussian"`
(`canvas-core.js:741, 756, 814`). V2 rendered all three with the smoothstep,
because the adapter did not forward the field at all -- the same dropped-field
class as the eleven U3-TF found.

WHAT MADE THIS MORE THAN A FUNCTION SWAP. `buildPassTable` confines its grids
to the shoulder `[hardness, 1]`, on the stated grounds that "inside the plateau
the tip is opaque ... and `rho` is never sampled there anyway". That is true of
the smoothstep, which is exactly 1 for `nd <= hardness`. It is FALSE of a
gaussian:

    hardness 0.50   gauss(0.50) = 0.7629     smoothstep = 1.0000
    hardness 0.85   gauss(0.85) = 0.3081     smoothstep = 1.0000

A gaussian is 1 only at the centre. So a table built on the shoulder reads a
domain the profile does not live on, and swapping the shape function alone
would have produced a plausible, wrong sweep.

AND THE CHEAP ESCAPE WAS WORSE. `rendererFor` already routes `textured ||
scatter` to the stamp renderer, so gaussian could have gone the same way and
skipped the table. All three gaussian presets are round with ratio 1 and
therefore SWEEP -- and the sweep is where C2's crease repair lives. Routing
them to the stamp renderer would have reintroduced the owner's originating
complaint on three presets.

So the table's domain became a property of the PROFILE. `SMOOTHSTEP.floor`
returns `hardness`, which makes every expression in `buildPassTable` reduce to
the arithmetic that was already there -- the thirteen non-gaussian presets are
byte-identical BY CONSTRUCTION, and measured that way below.

THE OWNER'S CONSTRAINT, unchanged: B -- the union rule with Studio's existing
smoothstep -- is the default. Gaussian is a per-preset opt-in and never a
default, which `test_u3tf_tips` guards.

`SuiteIntegrityTests` is last, and its count moves with every added test.
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

EXPECTED_U3G_TESTS = 17

PROBE = Path(__file__).with_name("u3g_gaussian_probe.js")
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"
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


class TheThirteenDidNotMoveTests(unittest.TestCase):
    """§26's stop condition, applied to C2's core.

    This is the test that made it safe to edit the optical-depth table at all.
    Not "within a byte" -- zero differing pixels, across five hardnesses and two
    flows, against a deposition naming no falloff at all.
    """

    def test_naming_the_default_changes_nothing(self) -> None:
        for c in probe()["smoothstepUnchanged"]:
            with self.subTest(hardness=c["hardness"], flow=c["flow"]):
                self.assertEqual(0, c["differingNamed"])
                self.assertEqual(0, c["maxDelta"])

    def test_an_unrecognised_falloff_changes_nothing(self) -> None:
        """A misspelt or future name must fall through to the smoothstep rather
        than throwing at paint time or landing on the bell."""

        for c in probe()["smoothstepUnchanged"]:
            with self.subTest(hardness=c["hardness"], flow=c["flow"]):
                self.assertEqual(0, c["differingUnknown"])

    def test_the_fixture_actually_painted(self) -> None:
        """Zero differing pixels is trivially true of two empty buffers."""

        for c in probe()["smoothstepUnchanged"]:
            with self.subTest(hardness=c["hardness"], flow=c["flow"]):
                self.assertGreater(c["painted"], 20000)

    def test_the_smoothstep_floor_is_the_hardness(self) -> None:
        """The whole byte-identity argument rests on this one return value. If
        it ever returns anything else, every expression in `buildPassTable`
        moves and the thirteen presets move with it."""

        self.assertEqual(0.5, probe()["noPlateau"]["floorSmoothstep"])


class ThePortIsLegacysBellTests(unittest.TestCase):

    def test_it_matches_legacy_to_floating_point_noise(self) -> None:
        """Against an INDEPENDENT re-implementation in the probe, not against
        itself -- so this compares two readings of `canvas-core.js:2178` rather
        than a function to its own definition."""

        self.assertLess(probe()["matchesLegacy"]["worstAbsoluteError"], 1e-12)

    def test_the_krita_constants_are_not_rounded(self) -> None:
        """6761 and 12500 are Krita's `KisGaussCircleMaskGenerator`. Tidying
        them into a neater bell would be a different brush wearing the name."""

        code = code_of(COVERAGE)
        self.assertIn("6761.0", code)
        self.assertIn("12500.0", code)
        self.assertIn("1.0 - hardness * 0.85", code)


class AGaussianHasNoPlateauTests(unittest.TestCase):
    """The finding that decided the design, kept as a test so a later reader
    cannot re-derive the shoulder-only table and think it is safe."""

    def test_the_bell_is_below_one_at_the_hardness(self) -> None:
        n = probe()["noPlateau"]
        self.assertLess(n["gaussAtHardness050"], 0.99)
        self.assertLess(n["gaussAtHardness085"], 0.99)

    def test_the_smoothstep_is_exactly_one_there(self) -> None:
        self.assertEqual(1, probe()["noPlateau"]["smoothstepAtHardness050"])

    def test_the_gaussian_domain_is_the_whole_disc(self) -> None:
        self.assertEqual(0, probe()["noPlateau"]["floorGaussian"])

    def test_the_floor_travels_with_the_table(self) -> None:
        """`passTau` maps a perpendicular distance onto a row. Recomputing the
        floor there from `hardness` would be a second definition of the domain,
        and for a gaussian the two would disagree -- every pixel reading the
        wrong row, which is a crease."""

        self.assertIn("tbl.floor === undefined ? hardness : tbl.floor",
                      code_of(COVERAGE))


class TheCreaseDidNotComeBackTests(unittest.TestCase):
    """The whole risk of this unit, measured rather than argued.

    A crease is a local dip along the stroke's spine. Compared against the same
    self-crossing stroke rendered with the smoothstep, which C2 already proved
    clean, so it is like for like.
    """

    def test_a_swept_gaussian_stroke_does_not_crease(self) -> None:
        for r in probe()["crease"]:
            with self.subTest(hardness=r["hardness"]):
                #: Against the smoothstep's own dip at the same hardness, plus a
                #: small absolute allowance: a softer profile has a gentler
                #: spine, so it should be comparable or better, never a
                #: different order of magnitude.
                self.assertLessEqual(r["gaussianWorstDip"],
                                     max(r["smoothstepWorstDip"], 20) + 10)

    def test_the_gaussian_stroke_painted_a_stroke(self) -> None:
        for r in probe()["crease"]:
            with self.subTest(hardness=r["hardness"]):
                self.assertGreater(r["gaussianPainted"], 15000)


class TheBellReachesThePixelsTests(unittest.TestCase):
    """If the two profiles produced the same pixels, the wiring did not land --
    which is precisely what "V2 rendered every tip with the smoothstep" was."""

    def test_the_bell_deposits_less_than_the_smoothstep(self) -> None:
        for r in probe()["bellIsSofter"]:
            with self.subTest(hardness=r["hardness"]):
                self.assertLess(r["ratio"], 0.95)
                self.assertGreater(r["ratio"], 0.5)

    def test_the_stamp_train_weight_is_gaussian_aware(self) -> None:
        """Legacy integrates `dabAlphaGauss` numerically for this
        (`canvas-core.js:3318`). A smoothstep mean would over-deposit every
        gaussian stamp train."""

        m = probe()["profileMean"]
        self.assertNotEqual(m["smoothstepAt050"], m["gaussianAt050"])

    def test_the_table_cache_cannot_serve_the_wrong_profile(self) -> None:
        """Same hardness, same flow, different falloff. A key without the
        profile hands the second tip the first's table -- a hit that paints the
        wrong brush, and only in a session where both presets are used."""

        t = probe()["tablesAreDistinct"]
        self.assertFalse(t["sameObject"])
        self.assertNotEqual(t["smoothstepFloor"], t["gaussianFloor"])

    def test_the_adapter_forwards_the_field(self) -> None:
        """It forwarded none of it before, which is why three presets asked for
        a bell and every one of them got the smoothstep."""

        self.assertIn("falloff: typeof S.brushFalloff", code_of(ADAPTER))
        self.assertEqual(2, code_of(ADAPTER).count("falloff: spec.falloff"))


class SuiteIntegrityTests(unittest.TestCase):

    def test_every_test_in_this_module_is_counted(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_U3G_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
