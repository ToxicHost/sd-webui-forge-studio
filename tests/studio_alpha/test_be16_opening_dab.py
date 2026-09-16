"""BE16: the opening dab points where the stroke is going.

THE OWNER'S REPORT: "This is a flat brush. See how the first press doesn't
match the angle/rotation of the drag?"

THE MECHANISM. `beginStroke` lays dab number one before any direction exists,
and took its angle from `_saSmooth` -- a MODULE global holding the heading the
PREVIOUS stroke ended on. So the first press of every flat-tipped stroke was
oriented by the last thing the owner drew, and on the first stroke of a session
by a zero that is only correct if you happen to drag east.

BE7 DIAGNOSED THIS AND THE FIX WAS NEVER FINISHED. It added
`S.stroke._headingKnown = false` with a comment that states the failure exactly
-- "`_saSmooth` is a module global, so without this the first dabs of every
stroke inherit the direction the previous stroke ended on" -- set it sixteen
lines AFTER the dab it was meant to protect, and never read it anywhere. A
field with no consumer is the defect class BE1's whole guard set exists to
catch, sitting inside the package that wrote the sentence describing it.

MEASURED BEFORE THE FIX, by intersection-over-union against the dab that should
have been there:

    Flat Chisel    0.31        Marker    0.45        Bristle Rake    0.39

and thirteen presets at 1.0000 -- eleven because a round tip has no
orientation, Calligraphy and Scatter Dust because `followStroke: false` means
they use an absolute angle by design. A fix that gave Calligraphy the travel
direction would break the one preset that is deliberately fixed.

THE FIX IS DEFERRAL, NOT A GUESS. The opening dab is held and stamped with the
first segment's real tangent the moment one exists; a stroke that never moves
flushes it at the end, because a tap must still paint. It cannot be painted and
corrected: the alpha map is an accumulator with no undo of its own, so
correcting a dab means writing 0, which erases whatever else landed there. BE13
refuses that for a pixel-perfect corner and BE4 for the selection.

THE COMPLAINT HAD THREE MECHANISMS AND THIS PACKAGE FIRST FIXED ONE. The BE17
audit found the other two afterwards, one of them inside this package's own new
code:

    rotation jitter applied TWICE -- `plotTo` and `stampWet` -- so a body dab
    turned twice as far as its label while the opening dab, reaching `stampWet`
    directly, turned once;

    `dabIgnoresRotation` deferring the press dab whenever Spikes > 2, on twelve
    presets where a spike fold provably cannot change a pixel: 221 inked pixels
    at press down to zero, for a finished stroke that is byte-identical.

Both are guarded by `TheFirstPressDisagreedInThreeWaysTests`. Measuring that
the mechanism you fixed now works is not the same as measuring that the
complaint is gone.

Source review: Evidence/source-review/BE16-opening-dab-heading.md
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

DRIVER = Path(__file__).with_name("be16_measure.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"
UI = FRONTEND / "canvas-ui.js"

NODE = shutil.which("node")

EXPECTED_BE16_TESTS = 37

#: The three tips whose orientation is visible. Everything else is either round
#: or pinned to an absolute angle.
ANISOTROPIC = ("Flat Chisel", "Marker", "Bristle Rake")

M: dict = {}


def setUpModule() -> None:
    if NODE is None:
        return
    result = subprocess.run(
        [NODE, str(DRIVER), str(CORE)],
        capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise AssertionError(
            f"the driver exited {result.returncode}:\n{result.stderr[:2000]}")
    M.update(json.loads(result.stdout))


needs_node = unittest.skipIf(
    NODE is None, "node is not on PATH; the engine cannot be EXECUTED")


def _code_only(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.M)


def _block(code: str, header: str) -> str:
    body = code[code.index(header):]
    return body[:body.index("\n}")]


@needs_node
class TheOpeningDabFollowsTheStrokeTests(unittest.TestCase):
    """THE OWNER'S COMPLAINT, as a number.

    After a stroke that went east, a stroke that goes north must open with a
    north-facing dab.
    """

    def test_the_three_anisotropic_presets_now_match(self):
        rows = M["theOpeningDabFollowsTheStroke"]
        wrong = {k: rows[k]["matchesTheStroke"] for k in ANISOTROPIC
                 if rows[k]["matchesTheStroke"] < 0.999}
        self.assertEqual(
            {}, wrong,
            "the first press still does not match the drag: " + str(wrong))

    def test_the_measurement_can_tell_the_difference(self):
        """Guards the guard. A metric that returned 1.0 for everything would
        pass the test above on the broken engine too -- and the first version
        of this driver did exactly that for round tips, because a round dab has
        no second moment worth the name.

        Before the fix these three measured 0.31, 0.45 and 0.39. The reference
        dab is stamped at a DIFFERENT angle from the leak, so an engine that
        ignored the heading entirely could not score 1.0 by accident.
        """

        rows = M["theOpeningDabFollowsTheStroke"]
        for name in ANISOTROPIC:
            with self.subTest(preset=name):
                self.assertGreater(rows[name]["openingPixels"], 40)

    def test_a_round_tip_is_unchanged(self):
        rows = M["theOpeningDabFollowsTheStroke"]
        self.assertEqual(1, rows["Basic Round"]["matchesTheStroke"])


@needs_node
class TheFirstStrokeOfASessionTests(unittest.TestCase):
    """Not a milder case -- the same one with zero as the inherited value.

    Any fix that special-cased "no previous stroke" would have fixed half of
    it, and the half it fixed would have been the half nobody hits twice.
    """

    def test_every_direction_opens_correctly(self):
        rows = M["firstStrokeOfASession"]
        wrong = {k: v["matchesTheStroke"] for k, v in rows.items()
                 if v["matchesTheStroke"] < 0.999}
        self.assertEqual({}, wrong, str(rows))

    def test_the_directions_actually_differ(self):
        """East used to pass by luck -- a leaked 0 IS east. The other two are
        what make this test mean anything."""

        self.assertIn("north", M["firstStrokeOfASession"])
        self.assertIn("diagonal", M["firstStrokeOfASession"])


@needs_node
class EveryPresetAgreesTests(unittest.TestCase):

    def test_no_shipped_preset_opens_at_the_wrong_angle(self):
        row = M["everyPreset"]
        self.assertEqual([], row["wrong"], f"{row['wrong']}")

    def test_all_sixteen_were_checked(self):
        self.assertEqual(16, len(M["everyPreset"]["at"]))


@needs_node
class AnAbsoluteAngleIsUntouchedTests(unittest.TestCase):
    """Calligraphy IS a held nib: `followStroke: false` and Angle 45. It was
    never affected by the defect and must not be affected by the fix -- the one
    preset whose whole identity is that its angle does NOT follow the stroke."""

    def test_calligraphy_ignores_the_direction_of_travel(self):
        row = M["anAbsoluteAngleIsUntouched"]
        self.assertTrue(row["both"], f"{row['at']}")

    def test_it_was_checked_in_two_directions(self):
        self.assertEqual({"north", "east"},
                         set(M["anAbsoluteAngleIsUntouched"]["at"]))


@needs_node
class ATapStillPaintsTests(unittest.TestCase):
    """THE HAZARD A DEFERRAL INTRODUCES, and the reason the flush exists.

    Press and release without moving. There is no tangent and there never will
    be, and the mark must still be there.
    """

    def test_a_tap_leaves_a_mark(self):
        row = M["aTapStillPaints"]
        self.assertTrue(row["all"], f"{row['at']}")

    def test_a_tap_leaves_a_mark_on_the_pixel_brush_too(self):
        """Pixel Perfect takes the cell-walk path, which `plotTo` never reaches
        on a tap -- so its opening dab has a second way to go missing."""

        self.assertTrue(M["aTapStillPaints"]["at"]["Pixel Perfect"]["painted"])

    def test_a_commit_without_a_finish_still_paints(self):
        """The backstop. Every headless driver in this suite begins a stroke
        and commits it without calling `finishStroke`, and an owner's
        pointer-up can be swallowed by a lost capture."""

        row = M["aCommitWithoutAFinishStillPaints"]
        self.assertTrue(row["painted"], f"{row}")


@needs_node
class TheAirbrushHasNoDirectionEitherTests(unittest.TestCase):
    """A HELD brush has no direction, and the timer used to deposit at the
    previous stroke's -- measured at IoU 1.0000 against the leaked heading.

    Whatever angle the held deposits take, the opening dab must take the same
    one, or a held flat brush builds up at two angles at once.
    """

    def test_the_held_deposit_does_not_use_the_previous_stroke(self):
        row = M["theAirbrushHasNoDirectionEither"]
        self.assertLess(row["matchesTheLeakedHeading"], 0.999, f"{row}")

    def test_the_held_deposit_uses_no_heading_at_all(self):
        row = M["theAirbrushHasNoDirectionEither"]
        self.assertEqual(1, row["matchesNoHeading"], f"{row}")

    def test_the_timer_actually_deposited(self):
        """Guards the guard: zero dabs would satisfy both tests above."""

        self.assertGreater(M["theAirbrushHasNoDirectionEither"]["dabsLaid"], 0)


@needs_node
class AnAbortedStrokeLeavesNothingTests(unittest.TestCase):
    """CT2's rule, which this package must not bend: a cancelled stroke leaves
    no pixels. A held opening dab is dropped rather than flushed."""

    def test_no_pending_dab_survives_an_abort(self):
        self.assertEqual(0, M["anAbortedStrokeLeavesNothing"]["pendingSurvived"])

    def test_the_next_stroke_does_not_inherit_it(self):
        self.assertEqual(0, M["anAbortedStrokeLeavesNothing"]["strayAt256"])


@needs_node
class TheTrapsADeferralSetsTests(unittest.TestCase):
    """Four ways to defer a dab and still get it wrong. Each was found by
    reading the engine before the change rather than by a test failing after
    it, and each needs its own probe because none of them is reachable through
    the shipped presets alone."""

    def test_a_zero_length_move_settles_nothing(self):
        """`Math.atan2(0, 0)` is 0, and `plotTo` computes the tangent
        unconditionally. A duplicate-coordinate pointermove -- which browsers
        do deliver -- would flush the held dab at zero radians: the same wrong
        answer in a different disguise."""

        row = M["aZeroLengthMoveSettlesNothing"]
        self.assertEqual(
            0, row["paintedAfterTheDuplicate"],
            "a duplicate-coordinate move laid the held dab")
        self.assertEqual(1, row["matchesTheStroke"], f"{row}")

    def test_the_deferred_dab_keeps_the_taper_it_was_pressed_with(self):
        """`_taperK` belongs to distance travelled and the opening dab's is
        zero, but `stampWet` reads one mutable slot that `plotTo` overwrites
        per dab. A flush that re-read it would lay the point of the stroke at
        full width and delete Taper In entirely."""

        row = M["theDeferredDabKeepsItsOwnTaper"]
        self.assertLess(
            row["taperAtPress"], row["taperAfterSixSegments"],
            f"the fixture did not move the taper, so it proves nothing: {row}")
        self.assertLessEqual(
            row["openingDabWidth"], 4,
            f"the opening dab was laid at the wrong taper: {row}")

    def test_a_mask_toggle_cannot_reshape_a_held_dab(self):
        """`stampWet` reads `S.editingMask` LIVE and forces a round,
        hardness-1 tip from it. `q` toggles it, so it can land between the
        press and the first move -- and `beginStroke` already locks
        `_commitTarget` and `_commitMask` against exactly this."""

        row = M["aMaskToggleCannotReshapeAHeldDab"]
        self.assertEqual(1, row["identical"], f"{row}")

    def test_an_aliased_stroke_advances_the_heading_like_any_other(self):
        """The pixel-walk branch of `plotTo` returns before the dab loop, so
        `_advanceHeading` never ran for an aliased stroke: the global held the
        previous stroke's direction for the WHOLE of it, not just its opening
        dab. Hoisting the call fixed a defect nobody had reported."""

        row = M["anAliasedStrokeAdvancesTheHeading"]
        self.assertTrue(row["headingKnown"], f"{row}")
        self.assertEqual(0, row["headingDeg"], f"east is 0 degrees: {row}")


class TheFlagFinallyHasAReaderTests(unittest.TestCase):
    """BE7 wrote the flag and the comment that diagnoses the bug, and never
    read it. This is the reader."""

    def test_the_heading_is_asked_for_rather_than_taken(self):
        code = _code_only(CORE)
        self.assertIn("function strokeHeading()", code)
        body = _block(code, "function strokeHeading()")
        self.assertIn("_headingKnown", body)

    def test_it_answers_null_when_no_heading_exists(self):
        """Null is a different statement from zero. Every caller turns it into
        0 rather than into the last thing the owner drew, and the difference is
        what makes the airbrush and the opening dab agree."""

        body = _block(_code_only(CORE), "function strokeHeading()")
        self.assertIn("? _saSmooth : null", body)

    @needs_node
    def test_the_flag_is_read_at_least_once(self):
        row = M["headingKnownFlag"]
        self.assertGreater(
            row["readsOtherThanWrites"], 0,
            "the flag is still written and never read, which is what BE7 left")


class NothingReadsTheRawGlobalForADabTests(unittest.TestCase):
    """The root cause was that `_saSmooth` has no lifecycle: one live writer,
    nothing that clears it, so its value outlives its stroke by construction.

    The global stays -- it is exported as `strokeAngle` and read outside this
    file -- and the FLAG is what says whether it means anything. These guards
    are what stop a future edit reaching past the flag.
    """

    #: Sites that may legitimately touch the raw global.
    ALLOWED = (
        "let _sa = 0",                       # the declaration
        "_saSmooth = target",                # the one live writer
        "? _saSmooth : null",                # strokeHeading itself
        # `plotTo`'s dab loop is the ONE site where the global is guaranteed
        # current: `_advanceHeading` runs five lines above it on the same
        # iteration, so there is no window in which it can be stale. BE7's
        # "three separate terms" comment is pinned by CT3's
        # `test_the_three_rotation_terms_stay_separate`, which asserts this
        # exact source line -- routing it through the helper would break a
        # guard and buy nothing.
        "(dyn.followStroke ? _saSmooth : 0)",
        "get strokeAngle()", "set strokeAngle(",
    )

    def test_no_dab_angle_is_taken_from_the_global_directly(self):
        code = _code_only(CORE)
        offenders = [ln.strip() for ln in code.split("\n")
                     if "_saSmooth" in ln
                     and not any(a in ln for a in self.ALLOWED)]
        self.assertEqual([], offenders, "these reach past the flag: " + str(offenders))

    def test_the_heading_is_cleared_when_a_stroke_begins(self):
        body = _block(_code_only(CORE), "function beginStroke(")
        self.assertIn("S.stroke._headingKnown = false;", body)

    def test_it_is_cleared_before_any_dab_can_be_laid(self):
        """BE7's version set it as the LAST statement of `beginStroke` -- after
        the opening dab and after `startAirbrush()`, so even a reader would
        have observed the previous stroke's `true`."""

        body = _block(_code_only(CORE), "function beginStroke(")
        cleared = body.index("S.stroke._headingKnown = false;")
        # AGAINST THE DAB, not merely against the airbrush. A mutation moved
        # the clear to the line AFTER `stampWet` -- BE7's mistake exactly -- and
        # an airbrush-only comparison still passed it, because `startAirbrush`
        # is the last statement in the function and almost anything precedes
        # it. The opening dab is the thing the clear exists to protect.
        self.assertLess(
            cleared, body.index("dabIgnoresRotation()"),
            "the flag is cleared after the opening dab has already read it")
        self.assertLess(
            cleared, body.index("startAirbrush()"),
            "the flag is cleared after the airbrush has already started")

    def test_the_heading_dies_with_the_stroke_that_produced_it(self):
        """Cleared at commit as well, so between strokes there is honestly no
        heading -- which is what stops the CURSOR previewing the last one."""

        body = _block(_code_only(CORE), "function commitStroke(")
        self.assertIn("S.stroke._headingKnown = false;", body)


class TheCursorShowsTheDabItWillMakeTests(unittest.TestCase):
    """The cursor is what tells an owner what the press will be, and for an
    anisotropic tip it was lying in three ways at once."""

    def test_the_flat_cursor_asks_the_engine(self):
        code = _code_only(UI)
        flat = code[code.index('case "flat": {'):]
        flat = flat[:flat.index("break;")]
        self.assertIn("C.dabRotation", flat)
        self.assertNotIn("C.strokeAngle || 0", flat)

    def test_the_marker_cursor_no_longer_hardcodes_0_4_radians(self):
        """CT3 removed this number from the STAMP -- "a hardcoded 0.4 radians,
        about 23 degrees, that nothing explained and nothing could change" --
        and left it in the cursor."""

        code = _code_only(UI)
        marker = code[code.index('case "marker": {'):]
        marker = marker[:marker.index("break;")]
        self.assertNotIn("const ang = 0.4", marker)
        self.assertIn("C.dabRotation", marker)

    def test_the_engine_offers_one_answer_for_both(self):
        code = _code_only(CORE)
        self.assertIn("function dabRotation()", code)
        body = _block(code, "function dabRotation()")
        self.assertIn("strokeHeading()", body)
        self.assertIn("followStroke", body)
        self.assertIn("_brushAngleRad()", body)

    def test_it_is_exported(self):
        self.assertIn("dabRotation", _code_only(CORE)[-3000:])


@needs_node
class TheFirstPressDisagreedInThreeWaysTests(unittest.TestCase):
    """Two more mechanisms for the owner's original complaint, found by the
    BE17 audit AFTER this package had shipped its own measurements.

    Both sit inside this package's subject and one sits inside its own new
    code, which is the point worth remembering: measuring that the mechanism
    you fixed now works is not the same as measuring that the complaint is
    gone.
    """

    def test_the_rotation_jitter_is_applied_once(self):
        """`plotTo` applied it and `stampWet` applied it again, so a BODY dab
        turned twice as far as the label promises -- spread sd 17.8 degrees
        against 13.0 at a 13% setting -- while the OPENING dab, which reaches
        `stampWet` directly, took exactly one application.

        `stampWet` is the survivor because every dab reaches it: the body, the
        deferred opening dab, and the airbrush's timed deposits. Keeping
        `plotTo`'s instead would have left the last two unjittered, which is
        this same bug wearing a different hat.
        """

        row = M["rotationJitterApplications"]
        self.assertEqual(
            1, row["inCode"],
            f"rotation jitter is applied {row['inCode']} times; a dab that "
            "passes through two of them turns twice as far as the label says")
        self.assertEqual(
            1, row["inStampWet"],
            "the surviving application is not the one in stampWet, so the "
            "opening dab and the airbrush no longer get any")

    def test_the_jitter_does_not_change_how_many_dabs_a_stroke_lays(self):
        """Rotation Jitter is an ORIENTATION control. `spacingFor` prices the
        gap from `stampRot`, so while the jitter lived in `plotTo` the gap was
        priced from one dab's random draw and the control changed dab DENSITY
        too -- Flat Chisel 60 dabs at 0% and about 130 at 50%.

        THE MAGNITUDE IS PINNED STRUCTURALLY, not by a threshold here. A bound
        like "under 2.5x the unjittered mark" separates one application from two
        on the fixture that produced it and means nothing on any other -- the
        first version of this guard asserted exactly that and failed at 2.61,
        against a number invented rather than derived. What this test owns is
        that the control still DOES something, so the structural guard above
        cannot be satisfied by deleting the feature.
        """

        rows = M["paintedPixelsByJitter"]
        for preset in ("Flat Chisel", "Marker"):
            with self.subTest(preset=preset):
                base = rows[preset]["jitter0"]
                self.assertGreater(
                    rows[preset]["jitter50"], base,
                    "Rotation Jitter no longer turns anything: " +
                    str(rows[preset]))
                self.assertGreater(
                    rows[preset]["jitter13"], base,
                    "a small jitter setting is inert: " + str(rows[preset]))

    def test_a_tap_inks_at_press_whatever_spikes_is_set_to(self):
        """`dabIgnoresRotation` deferred the press dab once Spikes exceeded 2.

        A spike fold is a rotation about the origin and a rotation preserves
        radius, so on a tip where rx == ry the fold cannot change one pixel --
        which BE6's `test_spikes_is_neutral_on_a_perfect_circle` already
        asserts. Twelve presets were paying a pointer event of missing press
        feedback for a control that could not help them.
        """

        rows = M["aTapInksAtPressAtEverySpikes"]
        dead = {k: v for k, v in rows.items() if v["inkedAtPress"] == 0}
        self.assertEqual(
            {}, dead,
            "the press dab is held back on a tip whose rotation cannot change "
            f"a pixel: {dead}")

    def test_deferring_was_never_a_loss(self):
        """Guards the guard. The test above would also pass on an engine that
        never defers anything, so this pins the property the deferral is FOR:
        the finished stroke is the same either way."""

        rows = M["aTapInksAtPressAtEverySpikes"]
        finished = {v["finished"] for v in rows.values()}
        self.assertEqual(
            1, len(finished),
            f"Spikes changed the finished stroke on a circular tip: {rows}")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__).countTestCases()
        self.assertEqual(EXPECTED_BE16_TESTS, found)


if __name__ == "__main__":
    unittest.main()
