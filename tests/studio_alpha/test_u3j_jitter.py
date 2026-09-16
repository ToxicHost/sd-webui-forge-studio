"""U3-J — the four per-dab jitters.

Eight of the sixteen shipping presets declare at least one, and V2 applied
none of them. `dynamics.js` models curve-driven rules -- pressure to size,
speed to flow -- which is a different thing from randomness per dab, so there
was nowhere for these to land and `describeStroke` did not forward them.

    sizeJitter      5 presets   Scatter Dust 0.3, Charcoal 0.12, Sketch Light
                                0.08, Pastel 0.06, Pencil 0.05
    opacityJitter   6 presets   Scatter Dust 0.2, Sketch Light 0.15,
                                Pencil 0.1, Charcoal 0.1, Airbrush and
                                Bristle Rake 0.05
    rotationJitter  1 preset    Scatter Dust 0.5
    scatter         3 presets   Scatter Dust 0.5, Airbrush 0.1, Pastel 0.05

Legacy's semantics, ported exactly (`canvas-core.js:2914, 2915, 2921, 3115`),
with `u` uniform on [-1, 1]:

    size      sz *= 1 + u * sizeJitter,      floored at 1 PIXEL
    opacity   op *= 1 + u * opacityJitter,   clamped to [0.01, 1]
    rotation  ang += u * PI * rotationJitter
    scatter   displaced PERPENDICULAR to travel by u * brushPx * scatter

Legacy draws all four from `Math.random()`. These come from the shared
deterministic hash, for the reason U3-D records.

THE ONE STATED GAP. Scatter displaces a dab, and a sweep draws a continuous
segment between two marks -- displacing its endpoints would bend the path
rather than scatter dabs along it. So scatter applies on the STAMP path.
Scatter Dust, whose whole character is this control at 0.5, stamps and is
covered. Airbrush (0.1) and Pastel (0.05) are round, therefore sweep, and do
not scatter. Recorded with its numbers rather than papered over by routing them
to a renderer that would cost them C2's crease repair.

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

EXPECTED_U3J_TESTS = 19

PROBE = Path(__file__).with_name("u3j_jitter_probe.js")
ADAPTER = APP_ROOT / "forge_studio" / "frontend" / "v2" / "canvas-adapter.js"
COVERAGE = APP_ROOT / "forge_studio" / "frontend" / "v2" / "coverage.js"

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


class TheStrokeIsRealTests(unittest.TestCase):
    """The probe drives the adapter's actual entry points against a fake Studio
    and reads what lands in `S.stroke.alphaMap` -- Legacy's sink, which is the
    real seam. A jitter test on a stroke that never happened proves nothing, and
    the first version of this probe produced exactly that: a one-argument
    `toDoc` gave every sample NaN coordinates and the whole contact collapsed to
    its opening mark."""

    def test_the_baseline_stroke_placed_many_marks(self) -> None:
        self.assertGreater(probe()["baseline"]["marks"], 40)

    def test_the_baseline_stroke_painted(self) -> None:
        self.assertGreater(probe()["baseline"]["painted"], 4000)


class EachControlMovesItsOwnQuantityTests(unittest.TestCase):

    def test_size_jitter_widens_the_stroke(self) -> None:
        e = probe()["eachControl"]
        self.assertGreater(e["sizeBand"], e["baselineBand"])

    def test_size_jitter_does_not_change_the_mark_count(self) -> None:
        """Spacing is the sampler's business. A size jitter that moved the mark
        count would be re-deriving spacing from the jittered radius."""

        self.assertTrue(probe()["eachControl"]["sizeKeptTheMarkCount"])

    def test_opacity_jitter_changes_the_deposited_alpha(self) -> None:
        """THE BUG THIS CAUGHT. `_depositionForMark` returned the frozen
        deposition whenever pressure did not drive flow, which was right when
        pressure was the only thing that could vary it -- so the jittered
        multiplier was silently discarded and four presets painted exactly as
        if the control were zero, total alpha identical to the byte."""

        e = probe()["eachControl"]
        self.assertNotEqual(e["baselineTotal"], e["opacityTotal"])

    def test_opacity_jitter_does_not_move_the_footprint(self) -> None:
        """It is a shading control. If the band moved, it would be reaching
        the radius."""

        e = probe()["eachControl"]
        self.assertEqual(e["baselineBand"], e["opacityBand"])


class RotationNeedsAShapeTests(unittest.TestCase):
    """A circle has no orientation -- the same reason `TIP_CAPABILITIES` calls
    spikes "needs-shape"."""

    def test_a_shaped_tip_spins(self) -> None:
        self.assertGreater(probe()["rotationNeedsAShape"]["shapedDiffering"], 500)

    def test_a_round_tip_cannot(self) -> None:
        self.assertEqual(0, probe()["rotationNeedsAShape"]["roundDiffering"])

    def test_it_varies_around_the_tips_own_angle(self) -> None:
        """Calligraphy is a held nib at 45 degrees. Jittering around ZERO
        instead of around its own angle would point it the wrong way and then
        wobble there.

        A mutation campaign proved the first fixture could not see this: it
        used `brushAngle: 0`, where dropping the base term changes nothing."""

        self.assertGreater(
            probe()["rotationNeedsAShape"]["keepsItsBaseAngle"], 500)


class ScatterDisplacesAStampedDabTests(unittest.TestCase):

    def test_a_stamped_tip_scatters(self) -> None:
        s = probe()["scatterOnAStampedTip"]
        self.assertGreater(s["scatteredBand"], s["baselineBand"])
        self.assertGreater(s["differing"], 1000)

    def test_a_swept_tip_does_not_and_that_is_recorded(self) -> None:
        """THE STATED GAP, asserted rather than left as a comment. A sweep
        draws a continuous segment; displacing its endpoints would bend the
        path. Airbrush (0.1) and Pastel (0.05) are affected; Scatter Dust,
        which is what this control is for, stamps."""

        self.assertEqual(0, probe()["eachControl"]["scatterOnASweptTipDiffering"])

    def test_only_the_stamp_branch_can_displace_a_dab(self) -> None:
        """Asserted on the CODE, not on the comment explaining it. The first
        version of this matched the sentence "STAMP ONLY, and that is a real
        limit" -- which `code_of` strips before the test ever sees it, so it
        could only ever fail. The sweep must receive the SAMPLED mark and the
        stamp the displaced one; that is the whole limit, in two call sites."""

        code = code_of(ADAPTER)
        self.assertIn("V.sweep(st.buffer, st.lastMark, mark, r,", code)
        self.assertIn("V.stamp(st.buffer, placed, r, dep, tipAngle, j);", code)
        self.assertEqual(1, code.count("placed = {"))


class TheDrawIsSharedAndIndependentTests(unittest.TestCase):
    """One hash, four channels. Two copies of a hash is two things to get
    subtly different; one draw reused across four channels would correlate
    them, so every big dab would also be the most opaque and the most
    displaced -- a pulse rather than noise."""

    def test_there_is_one_hash_implementation(self) -> None:
        self.assertEqual(1, code_of(COVERAGE).count("function hash01("))

    def test_the_channels_decorrelate(self) -> None:
        self.assertLess(probe()["draw"]["worstChannelCorrelation"], 0.1)

    def test_the_four_salts_are_distinct_in_the_source(self) -> None:
        """Read OUT of the adapter by the probe, not restated in it. The first
        version listed the constants literally, so changing one in the source
        could not fail the test -- it measured a copy of the claim. A mutation
        campaign caught exactly that, and it is the same fault as measuring a
        neighbouring quantity and calling it the one you meant."""

        self.assertTrue(probe()["saltsAreDistinct"])

    def test_the_jitter_does_not_repeat_along_the_stroke(self) -> None:
        """`_placeMarks` runs once per BATCH of dabs, so indexing the draw by
        the batch-local counter makes every batch jitter identically -- a
        pattern at the pointer-event rate rather than noise. Measured as
        self-similarity between the stroke's two halves."""

        r = probe()["indexDoesNotRestart"]
        self.assertGreater(r["columns"], 100)
        #: A constant index gives every dab the same radius, so the ribbon has
        #: one height and the deviation collapses. Measured at 3.48 with a
        #: stroke-long index.
        self.assertGreater(r["heightSd"], 1.5)

    def test_the_draw_is_centred_and_bounded(self) -> None:
        """Uniform on [-1, 1]: Legacy's `Math.random() * 2 - 1`. A biased draw
        would make every stroke drift one way."""

        d = probe()["draw"]
        self.assertLess(abs(d["mean"]), 0.05)
        self.assertGreater(d["min"], -1.0001)
        self.assertLess(d["max"], 1.0001)

    def test_the_channels_do_not_move_together_in_pixels(self) -> None:
        c = probe()["channelsAreIndependent"]
        self.assertTrue(c["sizeChangedBand"])
        self.assertTrue(c["opacityLeftBandAlone"])


class SuiteIntegrityTests(unittest.TestCase):

    def test_every_test_in_this_module_is_counted(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_U3J_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
