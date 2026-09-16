"""BE3: dab placement is a function of distance, not of browser event rate.

THE DEFECT.

    const steps = Math.max(1, Math.ceil(dist / sp));
    for (let i = 1; i <= steps; i++) { const t = i / steps; ... }

`Math.max(1, ...)` guaranteed a dab for EVERY pointer event however short, so a
browser delivering 240 events a second laid roughly four times the dabs of one
delivering 60 along the same path. `t = i / steps` then spread those dabs evenly
across whatever segment arrived, making the real gap `dist / steps` rather than
the requested spacing. Nothing was carried between events, so the sub-spacing
remainder was discarded on every one.

It was masked because max-blend is idempotent and the soft path clamps at the
flow ceiling. It stops being masked the moment BE5 removes that ceiling, which
is exactly why the safe order puts BE3 first.

THE FIX carries a DEBT: how much further the stroke must travel before the next
dab is due. Blender does the same thing in `paint_stroke.cc`.

WHY THE OBVIOUS TEST DOES NOT WORK.

Sampling a sine curve at 15/30/60/120 points and comparing output reports
"rate-dependent" on a perfectly correct engine, because those are not the same
path -- a coarsely sampled sine is a different polyline from a finely sampled
one. The geometry differs, so the pixels differ, and the test proves nothing.

So the path is fixed as explicit vertices and then SUBDIVIDED. Every rate walks
the identical polyline and only the event count varies. That is the only
version of this test that can fail for the right reason.
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

DRIVER = Path(__file__).with_name("be3_measure.js")
CORE = APP_ROOT / "forge_studio" / "frontend" / "canvas-core.js"

NODE = shutil.which("node")

EXPECTED_BE3_TESTS = 18

#: Every stroke loop that still carries the inherited spacing expression, and
#: the package that owns migrating it. BE3 deliberately changed ONE of five.
#:
#: The Extension has the same five. Studio is diverging in `plotTo` only, and
#: silently "fixing" the other four inside a brush package would change Smudge,
#: Dodge/Burn and Regional Prompting with no test naming the consequence.
SPACING_LOOP_OWNERS = {
    "smudgeStroke": "CT10 retouch-tool migration",
    "dodgeBurnStroke": "CT10 retouch-tool migration",
    "regionPaintMove": "Regional Prompting",
    "regionEraseMove": "Regional Prompting",
}

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(
            f"the BE3 driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only(source: str) -> str:
    """Strip comments before scanning.

    This file's own explanatory comment quotes the removed expression verbatim.
    A scan that did not strip comments would find it, report `plotTo` as still
    holding the defect, and be wrong -- which is the sixth time in this codebase
    that a guard has matched the comment explaining the thing it bans.
    """

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


def _enclosing_functions(pattern: str) -> dict[str, int]:
    """Every function containing `pattern`, with a count, comments stripped."""

    lines = _code_only(CORE.read_text(encoding="utf-8")).splitlines()
    found: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not re.search(pattern, line):
            continue
        for back in range(index, -1, -1):
            match = re.match(r"^(?:async\s+)?function\s+(\w+)", lines[back])
            if match:
                found[match.group(1)] = found.get(match.group(1), 0) + 1
                break
    return found


@needs_node
class EventRateInvarianceTests(unittest.TestCase):
    """The acceptance criterion: same path, different event rates, same pixels."""

    def test_every_rate_produces_identical_coverage(self):
        digests = {name: row["digest"] for name, row in M["rates"].items()}
        self.assertEqual(
            1, len(set(digests.values())),
            "the same polyline produced different pixels at different event "
            f"rates: {digests}")

    def test_every_rate_produces_the_same_pixel_count(self):
        counts = {name: row["pixels"] for name, row in M["rates"].items()}
        self.assertEqual(1, len(set(counts.values())), f"{counts}")

    def test_the_rates_actually_differ(self):
        """Guards the guard. If every 'rate' were the same number of events the
        invariance above would be vacuously true."""

        rates = {row["eventsPerSegment"] for row in M["rates"].values()}
        self.assertGreaterEqual(len(rates), 5)
        self.assertGreaterEqual(max(rates) / min(rates), 20)

    def test_the_stroke_painted_something(self):
        for name, row in M["rates"].items():
            with self.subTest(rate=name):
                self.assertGreater(row["pixels"], 1000)


@needs_node
class SubSpacingEventsTests(unittest.TestCase):
    """A pointer event shorter than one spacing must emit NOTHING.

    The old code could not express this: `Math.max(1, ...)` forced a dab. It is
    the single clearest symptom of placement following the event stream instead
    of the path.
    """

    def test_twenty_tiny_events_emit_no_dab(self):
        self.assertTrue(
            M["subSpacing"]["coverageUnchanged"],
            f"coverage changed: {M['subSpacing']}")

    def test_the_opening_dab_still_painted(self):
        """So the test above cannot pass by painting nothing at all."""

        self.assertGreater(M["subSpacing"]["openingDabPixels"], 0)

    def test_no_duplicate_dab_at_the_stroke_origin_on_a_soft_tip(self):
        """The seeded opening debt, tested where it is observable.

        A hard tip cannot see this: a missing debt puts the extra dab at
        travelled = 0, exactly on the opening dab, and max-blend absorbs it.
        A soft tip ACCUMULATES, so the duplicate shows as a darker blob at the
        start of every stroke -- which is what an owner would see.
        """

        self.assertTrue(
            M["subSpacingSoftTip"]["coverageUnchanged"],
            f"{M['subSpacingSoftTip']}")

    def test_the_soft_opening_dab_painted(self):
        self.assertGreater(M["subSpacingSoftTip"]["openingDabPixels"], 0)


@needs_node
class SpacingFollowsPressureTests(unittest.TestCase):
    """The gap is priced at the pressure of the dab actually being painted.

    THIS CLASS EXISTS BECAUSE A MUTATION ESCAPED. Replacing the interpolated
    pressure with a constant 1.0 when pricing the next gap changed nothing in
    any test, because every one of them ran with pressure sensitivity OFF,
    where the substitution is a no-op. The guards were unreachable by the thing
    they were supposed to guard.

    Isolated by CONTINUITY rather than by dab positions, which the engine does
    not expose: a light stroke whose dabs are small but whose gaps are priced
    for full-pressure dabs comes out dotted.
    """

    def test_a_light_stroke_is_solid(self):
        light = M["pressureSpacing"]["light"]
        self.assertEqual(
            0, light["longestGapPx"],
            "a light-pressure stroke has gaps: spacing is being priced for a "
            f"dab that is not the one being painted. {light}")

    def test_a_full_pressure_stroke_is_solid(self):
        self.assertEqual(0, M["pressureSpacing"]["full"]["longestGapPx"])

    def test_both_strokes_actually_painted(self):
        """So 'no gaps' cannot be satisfied by painting nothing."""

        for key in ("full", "light"):
            with self.subTest(pressure=key):
                self.assertGreater(
                    M["pressureSpacing"][key]["paintedOnCentreLine"], 300)


class SpacingLoopInventoryTests(unittest.TestCase):
    """Addendum section 6.2: no new private spacing loop without an owner.

    Five functions carried the inherited expression. BE3 migrated exactly one.
    If a sixth appears, someone has written a new stroke loop with its own
    placement rules and this fails until they name who migrates it.
    """

    PATTERN = r"const steps = Math\.max\(1, Math\.ceil"

    def test_plotto_no_longer_carries_the_inherited_expression(self):
        self.assertNotIn("plotTo", _enclosing_functions(self.PATTERN))

    def test_every_remaining_loop_has_a_named_owner(self):
        found = set(_enclosing_functions(self.PATTERN))
        unowned = found - set(SPACING_LOOP_OWNERS)
        self.assertEqual(
            set(), unowned,
            "a stroke loop carries the inherited spacing expression with no "
            f"named owner: {sorted(unowned)}. Add it to SPACING_LOOP_OWNERS "
            "with the package that will migrate it, or migrate it.")

    def test_the_known_loops_are_all_still_there(self):
        """The other half. If one silently disappears, a brush package has
        probably changed Smudge or Regional Prompting as collateral."""

        found = set(_enclosing_functions(self.PATTERN))
        missing = set(SPACING_LOOP_OWNERS) - found
        self.assertEqual(
            set(), missing,
            f"these loops vanished without their own package: {sorted(missing)}")

    def test_the_smudge_spacing_floor_is_untouched(self):
        """CT10 owns it, and halving it would roughly double smudge dab count.
        Named explicitly because it looks exactly like a size floor."""

        code = _code_only(CORE.read_text(encoding="utf-8"))
        self.assertIn("const sp = Math.max(2, brushPx() * 0.25);", code)


class TheDebtIsSeededTests(unittest.TestCase):
    """`beginStroke` must owe a full gap after its opening dab.

    A debt starting at zero would fire a second dab on the very first
    pointermove however small -- the event-rate dependence reintroduced at the
    most visible point in the mark, its start.
    """

    def test_begin_stroke_seeds_the_spacing_debt(self):
        code = _code_only(CORE.read_text(encoding="utf-8"))
        begin = code[code.index("function beginStroke("):]
        begin = begin[:begin.index("\nfunction ")]
        self.assertIn("_spacingDebt", begin)

    def test_the_debt_is_carried_between_events(self):
        code = _code_only(CORE.read_text(encoding="utf-8"))
        self.assertIn("S.stroke._spacingDebt = debt - (dist - travelled);", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE3_TESTS, found)


if __name__ == "__main__":
    unittest.main()
