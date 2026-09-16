"""BE4: a selection bounds the stroke; it does not meter it.

THE DEFECT.

Selection weight was multiplied into every dab inside `stampAlphaMap`, at all
three coverage branches, BEFORE accumulation:

    a *= S.selection.mask[idx] / 255;

That is harmless on a max-blended hard tip, where 50% stays 50% however many
dabs overlap. It is wrong on an accumulating soft tip, because multiplying
before accumulation turns the selection from an AMOUNT into a RATE: every dab
contributes its share of a reduced value and the total climbs back toward full
with each overlap.

Measured before:

    hard tip, selection 100 / 50 / 25   ->  255 / 128 / 64    correct
    soft tip, selection 100 / 50 / 25   ->  102 / 102 / 102   ignored entirely
    soft tip, 50%, 3 dabs vs 60 dabs    ->   92  vs  102      a RATE

The soft numbers were identical because the flow ceiling clamped first: the
selection was not merely mis-scaled there, it reached nothing at all.

THE FIX applies it once in `alphaMapToImageData`, which already walks only the
stroke's dirty rectangle, already reads accumulated coverage, and already
writes output alpha. It also feeds the live preview in `composite()`, so what
the owner sees while painting is what lands.

`drawGradient` has applied selection at merge in this exact shape since long
before this package. BE4 restores a pattern the file already uses.
"""

from __future__ import annotations

import json
import re
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

EXPECTED_BE4_TESTS = 16

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(
            f"the measurement driver exited {result.returncode}:\n"
            f"{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only() -> str:
    source = CORE.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


@needs_node
class SelectionIsAnAmountTests(unittest.TestCase):
    """The bound must not depend on how long the owner paints."""

    def test_a_soft_tip_bound_is_stable_across_dab_counts(self):
        """AS A RATIO, because BE17 made the MARK rise with dab count.

        This used to compare the bounded value at 3 dabs against the value at
        60 and require them equal. Under a max-blend that was a fair test: the
        underlying mark was the same either way, so any movement was the
        selection leaking into the per-dab path. Under accumulation the mark
        legitimately darkens with dab count -- 102 to 170 unbounded -- so the
        old comparison had stopped testing the selection at all.

        What still separates an AMOUNT from a RATE is proportion: half the
        selection must be half of whatever the mark is. A rate fails it,
        because each dab contributes its share of a reduced value and the total
        climbs back toward full with every overlap, which is exactly the defect
        BE4 was opened for. Measured: 51/102 and 85/170, both 0.5000.
        """

        few = M["selection"]["soft_50_few"] / M["selection"]["soft_100_few"]
        many = M["selection"]["soft_50_many"] / M["selection"]["soft_100_many"]
        self.assertAlmostEqual(
            0.5, few, delta=0.02,
            msg=f"3 dabs: 50% selection gave {few:.4f} of the unbounded mark")
        self.assertAlmostEqual(
            0.5, many, delta=0.02,
            msg=f"60 dabs: 50% selection gave {many:.4f} of the unbounded mark")
        self.assertAlmostEqual(
            few, many, delta=0.02,
            msg="the selection's share of the mark moved between 3 dabs and "
                "60: it is behaving as a rate")

    def test_the_fixture_actually_accumulates(self):
        """Guards the guard. The ratio test above would pass on an engine where
        3 dabs and 60 produced an identical mark -- which is the engine BE17
        replaced. This is what says the two cases differ at all."""

        self.assertGreater(
            M["selection"]["soft_100_many"],
            M["selection"]["soft_100_few"] * 1.2,
            "60 dabs did not darken a soft mark more than 3 did, so the "
            "proportionality test above is measuring nothing")

    def test_a_hard_tip_bound_is_stable_across_dab_counts(self):
        self.assertEqual(
            M["selection"]["hard_50_few"], M["selection"]["hard_50_many"])

    def test_a_soft_tip_scales_with_the_selection(self):
        full = M["selection"]["soft_100"]
        self.assertAlmostEqual(full * 0.50, M["selection"]["soft_50"], delta=1)
        self.assertAlmostEqual(full * 0.25, M["selection"]["soft_25"], delta=1)

    def test_a_hard_tip_scales_with_the_selection(self):
        self.assertEqual(255, M["selection"]["hard_100"])
        self.assertEqual(128, M["selection"]["hard_50"])
        self.assertEqual(64, M["selection"]["hard_25"])

    def test_a_full_selection_changes_nothing(self):
        """100% must be EXACTLY a no-op, not one step short.

        The soft value is repinned by BE17 from 102 to 108: thirty dabs of a
        soft tip now accumulate slightly past what one dab's max reached. The
        HARD value is untouched at 255, because a Flow-100 tip short-circuits
        the accumulator entirely -- which is the check that the repin is the
        accumulator arriving and not the bound slipping.
        """

        self.assertEqual(255, M["selection"]["hard_100"])
        self.assertEqual(108, M["selection"]["soft_100"])

    def test_the_bound_rounds_rather_than_truncates(self):
        """Pinned by an exact value, because a tolerance hides it.

        AN EARLIER VERSION OF THIS FILE CLAIMED the `+ 127` is what makes a
        full selection a no-op. That was wrong and a mutation proved it:
        255 * 255 / 255 is exactly 255 whether you round or truncate, so the
        full case cannot see the difference at all.

        Where it bites is the middle. A soft tip at 102 under a 25% selection
        gives 102 * 64 = 6528; rounded that is 26, truncated it is 25. One
        step, consistently downward, on every partially selected pixel -- a
        soft edge that is quietly darker than the geometry says.

        The neighbouring scale test uses delta=1 and therefore accepts both,
        which is correct for a scale test and useless for this. Hence an exact
        assertion, and hence this docstring: the reason for a magic number has
        to be written down where the next person will remove it.
        """

        # Repinned with the soft mark: 108 * 64 = 6912, rounded 27,
        # truncated 26. The property is unchanged and so is the arithmetic
        # that makes it visible -- only the mark it is measured on is darker.
        self.assertEqual(
            27, M["selection"]["soft_25"],
            "the merge is truncating instead of rounding; every partially "
            "selected pixel lands one step dark")


class SelectionIsAppliedOnceTests(unittest.TestCase):
    """Structural: it must not creep back into the per-dab path."""

    def test_no_coverage_branch_multiplies_selection_per_dab(self):
        code = _code_only()
        self.assertEqual(
            0, code.count("a *= S.selection.mask[idx]"),
            "selection is being multiplied into dabs again; that converts the "
            "bound into a rate on any accumulating tip")

    def test_the_merge_applies_it(self):
        code = _code_only()
        self.assertIn("const sel = (S.selection.active && S.selection.mask)", code)
        self.assertIn("data[j + 3] = sel ?", code)

    def test_the_merge_uses_the_same_rounding_as_the_accumulator(self):
        """`+ 127) / 255` rather than a bare divide -- the same convention the
        coverage accumulator uses, so the two agree about what a byte means.

        NOT, as an earlier version of this docstring claimed, "so a full
        selection is a no-op": the full case is exact either way. See
        `test_the_bound_rounds_rather_than_truncates` for where it actually
        matters.
        """

        code = _code_only()
        self.assertIn("(((a * sel[i] + 127) / 255) | 0)", code)

    def test_the_obsolete_ct3d_comment_was_corrected(self):
        """CT3d left a comment justifying the per-dab approach. Source that
        argues the corrected behaviour is wrong is worse than no comment."""

        code = CORE.read_text(encoding="utf-8")
        self.assertIn("BE4 CORRECTS THAT", code)
        self.assertNotIn(
            "so the selection is in the alpha map before this runs", code)


class TheGradientPrecedentTests(unittest.TestCase):
    """The in-file precedent must stay, since BE4's design cites it."""

    def test_draw_gradient_still_applies_selection_at_merge(self):
        code = _code_only()
        self.assertIn("gd.data[i * 4 + 3] * S.selection.mask[i] / 255", code)


@needs_node
class TheBoundIsAffordableTests(unittest.TestCase):
    """Addendum section 7.4 -- timed, and timed against the right thing.

    The comparison is the SAME function with and without a mask. Comparing it
    to the whole-document eraser path CT3d removed would flatter it, which is
    why the brief forbids exactly that.

    These assert ORDERS OF MAGNITUDE, not milliseconds: the numbers move with
    the machine, and a tight threshold here would fail on someone else's
    laptop for no engineering reason.
    """

    def test_a_bounded_stroke_region_stays_sub_millisecond_at_24mp(self):
        cost = M["selectionCost"]["mp24_boundedRegion_fractional"]
        self.assertLess(
            cost, 5.0,
            f"the merge-time bound cost {cost} ms over a bounded stroke region "
            "on a 24 MP document")

    def test_the_bound_does_not_multiply_the_cost(self):
        """A full-document dirty rect is the worst case. The bound may add to
        it; it must not dominate it."""

        base = M["selectionCost"]["large_none"]
        bound = M["selectionCost"]["large_fractional"]
        self.assertLess(
            bound, base * 3,
            f"selection turned {base} ms into {bound} ms")

    def test_the_measurement_ran_on_a_real_document(self):
        """So the timings above cannot pass by measuring nothing."""

        self.assertGreater(M["selectionCost"]["large_none"], 0.1)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE4_TESTS, found)


if __name__ == "__main__":
    unittest.main()
