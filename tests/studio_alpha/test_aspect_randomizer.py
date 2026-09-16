"""The aspect randomizer, from a dead UI to a frozen dimension. WP1.5.

THE DEFECT THESE CATCH

The page shipped the whole feature -- base pools, ratio pools, an orientation
checkbox, multi-select indicators, and `↔` on ratios where either orientation
is allowed -- and none of it reached generation. The collector never read
`_isRandMode`, `arRandOrientation` or either pool; the UI never rewrote
width/height itself; and `rg` for `aspect` across the whole backend found one
ffmpeg thumbnail flag. Every job used the fixed size while the controls
confirmed the owner's selection back to them.

Ported from `scripts/studio_ar.py` at `4.10.0-public-beta` / `b316a4d8`, read in
full. Review: `Evidence/source-review/WP1.5-base-controls.md`.

THE CHOOSER IS INJECTED so every case below is exact rather than statistical. A
test that rolls real randomness and asserts a range proves almost nothing.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.aspect_randomizer import (  # noqa: E402
    ALL_BASES,
    ALL_RATIOS,
    AspectRefused,
    resolve,
    resolve_series,
    round8,
)
from forge_studio.presentation import (  # noqa: E402
    _resolved_aspect,
    _validated_request_payload,
)


def refusal():
    """The refusal class, resolved at CALL time. Deliberately not an import.

    `test_import_boundaries` deletes every `forge_studio.*` entry from
    `sys.modules` and re-imports the package fresh, to prove it drags in
    neither PIL nor torch. Any module binding `PresentationError` at import
    time then holds a class the running code no longer raises, and
    `assertRaises` silently stops matching -- the test errors with the correct
    exception sitting in its own traceback.

    THIS FILE WAS NOT BROKEN, and that is the point. Discovery is alphabetical,
    so `test_a...` runs before `test_i...` and the stale binding never had a
    chance to bite. It was passing because of its filename, which is not a
    reason, and a rename or a new neighbour would have converted that into a
    baffling failure. Fixed on the owner's instruction rather than left as a
    trap. `test_soft_inpainting` hit exactly this and cost a full-suite bisect.
    """

    import forge_studio.presentation

    return forge_studio.presentation.PresentationError

EXPECTED_ASPECT_TESTS = 41

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")
INDEX_HTML = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8")


def first(sequence):
    return list(sequence)[0]


def last(sequence):
    return list(sequence)[-1]


def body(**generation) -> dict:
    payload = {"model": "m", "generation": {
        "positive_prompt": "p", "negative_prompt": "", "seed": 7, "steps": 4,
        "cfg_scale": 4.0, "width": 768, "height": 768}}
    payload["generation"].update(generation)
    return payload


class ResolverParityTests(unittest.TestCase):
    """Step for step against `randomize_dimensions` (studio_ar.py:94-157)."""

    def test_an_inactive_randomizer_changes_nothing(self) -> None:
        """Catches: WP1.5 altering a fixed-size request at all."""

        rolled = resolve(1024, 576, chooser=last)
        self.assertEqual((1024, 576), (rolled.width, rolled.height))
        self.assertFalse(rolled.randomized)

    def test_stale_pools_cannot_affect_fixed_mode(self) -> None:
        """The pools stay selected in the page between sessions.

        Catches: a request with every mode off still being re-shaped because a
        pool was left populated.
        """

        rolled = resolve(1024, 576, base_pool=[512], ratio_pool=["1:1"],
                         chooser=last)
        self.assertEqual((1024, 576), (rolled.width, rolled.height))

    def test_a_randomized_base_is_taken_from_the_pool(self) -> None:
        rolled = resolve(768, 768, randomize_base=True, base_pool=[512, 896],
                         chooser=first)
        self.assertEqual(512, rolled.base)

    def test_a_randomized_ratio_is_taken_from_the_pool(self) -> None:
        rolled = resolve(768, 768, randomize_ratio=True,
                         ratio_pool=["16:9"], chooser=first)
        self.assertEqual("16:9", rolled.ratio_label)
        # 768 short, 768 * 16/9 = 1365.33 -> round8 -> 1368
        self.assertEqual((1368, 768), (rolled.width, rolled.height))

    def test_landscape_puts_the_long_side_first(self) -> None:
        rolled = resolve(768, 768, randomize_ratio=True, randomize_orientation=True,
                         ratio_pool=["2:1"], chooser=lambda s: list(s)[-1])
        self.assertEqual("landscape", rolled.orientation)
        self.assertGreater(rolled.width, rolled.height)

    def test_portrait_puts_the_long_side_second(self) -> None:
        rolled = resolve(768, 768, randomize_ratio=True, randomize_orientation=True,
                         ratio_pool=["2:1"], chooser=lambda s: list(s)[0])
        self.assertEqual("portrait", rolled.orientation)
        self.assertGreater(rolled.height, rolled.width)

    def test_orientation_disabled_keeps_the_current_one(self) -> None:
        """Catches: rolling an orientation nobody asked to randomize."""

        rolled = resolve(512, 1024, randomize_base=True, base_pool=[768],
                         chooser=first)
        self.assertEqual("portrait", rolled.orientation)

    def test_a_square_ratio_never_claims_an_orientation(self) -> None:
        """The Extension's special case (`ratio_val > 1.001`).

        Catches: a 1:1 roll producing a coin flip that changes nothing while
        reporting that it did.
        """

        for chooser in (first, last):
            with self.subTest(chooser=chooser.__name__):
                rolled = resolve(768, 768, randomize_ratio=True,
                                 randomize_orientation=True,
                                 ratio_pool=["1:1"], chooser=chooser)
                self.assertEqual("square", rolled.orientation)
                self.assertEqual(rolled.width, rolled.height)

    def test_an_unrandomized_base_snaps_to_the_nearest_canonical(self) -> None:
        """The Extension snaps rather than keeping an arbitrary short side."""

        # 700 is nearer 640 (60) than 768 (68). My first expectation here was
        # 768 by eye; the resolver was right and the arithmetic was not.
        rolled = resolve(700, 700, randomize_ratio=True, ratio_pool=["1:1"],
                         chooser=first)
        self.assertEqual(640, rolled.base)
        self.assertEqual(768, resolve(760, 760, randomize_ratio=True,
                                      ratio_pool=["1:1"], chooser=first).base)

    def test_round8_matches_the_extension(self) -> None:
        """`max(8, round(n / 8) * 8)` -- a floor and NO ceiling."""

        self.assertEqual(8, round8(0))
        self.assertEqual(8, round8(1))
        self.assertEqual(768, round8(766))
        self.assertEqual(1368, round8(1365.33))

    def test_both_sides_land_on_the_vae_alignment(self) -> None:
        for label in ("5:4", "4:3", "3:2", "16:9", "2:1", "2.39:1"):
            with self.subTest(ratio=label):
                rolled = resolve(768, 768, randomize_ratio=True,
                                 ratio_pool=[label], chooser=first)
                self.assertEqual(0, rolled.width % 8)
                self.assertEqual(0, rolled.height % 8)


class PoolRuleTests(unittest.TestCase):
    def test_an_empty_pool_means_all(self) -> None:
        """The Extension's rule, kept exactly."""

        for pool_kind, pool in (("base", []), ("ratio", [])):
            with self.subTest(pool=pool_kind):
                rolled = resolve(768, 768, randomize_base=True,
                                 randomize_ratio=True,
                                 base_pool=pool if pool_kind == "base" else None,
                                 ratio_pool=pool if pool_kind == "ratio" else None,
                                 chooser=first)
                self.assertEqual(ALL_BASES[0], rolled.base)
                self.assertEqual(ALL_RATIOS[0][2], rolled.ratio_label)

    def test_a_malformed_base_is_refused_by_name(self) -> None:
        """DIVERGENCE, deliberate. The Extension drops an invalid entry and
        falls back to the full set; Studio refuses.

        Catches: the owner's chosen pool being silently replaced with every
        base, producing sizes they never selected with nothing to explain it.
        """

        for pool in ([999], ["512"], [None], [True]):
            with self.subTest(pool=pool):
                with self.assertRaises(AspectRefused) as caught:
                    resolve(768, 768, randomize_base=True, base_pool=pool,
                            chooser=first)
                self.assertEqual("ASPECT_BASE_INVALID", caught.exception.code)

    def test_a_malformed_ratio_is_refused_by_name(self) -> None:
        for pool in (["nonsense"], ["16:"], ["0:1"], [""], [None]):
            with self.subTest(pool=pool):
                with self.assertRaises(AspectRefused) as caught:
                    resolve(768, 768, randomize_ratio=True, ratio_pool=pool,
                            chooser=first)
                self.assertEqual("ASPECT_RATIO_INVALID", caught.exception.code)

    def test_a_decimal_ratio_label_parses(self) -> None:
        """`2.39:1` is in the canonical set and must survive the float path."""

        rolled = resolve(768, 768, randomize_ratio=True,
                         ratio_pool=["2.39:1"], chooser=first)
        self.assertEqual("2.39:1", rolled.ratio_label)

    def test_a_non_positive_dimension_is_refused(self) -> None:
        for width, height in ((0, 768), (768, 0), (-8, 768)):
            with self.subTest(size=(width, height)):
                with self.assertRaises(AspectRefused) as caught:
                    resolve(width, height, chooser=first)
                self.assertEqual("ASPECT_DIMENSION_INVALID",
                                 caught.exception.code)


class NoCeilingTests(unittest.TestCase):
    """Binding product policy: Studio imposes no generation-size maximum."""

    def test_the_resolver_does_not_cap_its_output(self) -> None:
        """Catches: `round8` or the composer acquiring an upper bound.

        1024 base at 2.39:1 is 2448 wide -- above the 2048 the UI used to
        clamp to, which is exactly why that clamp had to go.
        """

        rolled = resolve(768, 768, randomize_base=True, randomize_ratio=True,
                         base_pool=[1024], ratio_pool=["2.39:1"], chooser=first)
        self.assertEqual(2448, rolled.width)

    def test_round8_has_a_floor_and_no_ceiling(self) -> None:
        self.assertEqual(8, round8(-100))
        self.assertEqual(100000, round8(99999))

    def test_admission_accepts_a_very_large_fixed_size(self) -> None:
        """Structurally valid means admitted. No pixel-count refusal.

        Allocates nothing: this is the request contract, not a generation.
        """

        admitted = _validated_request_payload(body(width=8192, height=8192))
        self.assertEqual((8192, 8192),
                         (admitted["width"], admitted["height"]))

    def test_the_width_and_height_controls_carry_no_maximum(self) -> None:
        """Catches: a frontend `data-max` reappearing, which DID clamp --
        `_paramScrubDef` applies `Math.min(def.max, v)`."""

        for control in ("paramWidth", "paramHeight"):
            with self.subTest(control=control):
                tag = INDEX_HTML.split(f'id="{control}"')[1].split(">")[0]
                self.assertNotIn("data-max", tag)
                self.assertNotIn(" max=", tag)


class CollectorTests(unittest.TestCase):
    """The half that was missing entirely."""

    def setUp(self) -> None:
        start = APP_JS.index("async function doGenerate")
        self.collector = APP_JS[start:start + 30000]
        self.collector = self.collector[
            :self.collector.index("lifecycle.submitGenerate")]

    def test_every_randomizer_input_is_collected(self) -> None:
        """Catches the original defect exactly: none of these were read."""

        group = APP_JS[APP_JS.index("function _aspectGroup"):]
        group = group[:group.index("\n}\n")]
        for control in ("arRandBase", "arRandRatio", "arRandOrientation",
                        "arBasePoolData", "arRatioPoolData"):
            with self.subTest(control=control):
                self.assertIn(control, group)

    def test_the_group_reaches_the_submitted_body(self) -> None:
        self.assertIn("_aspectGroup()", self.collector)
        self.assertIn("aspect:", self.collector)

    def test_it_is_sent_for_txt2img_only(self) -> None:
        """The Extension guards its call site with `if is_txt2img:` because an
        image operation takes its geometry from the source."""

        self.assertIn('_source.operation === "txt2img" && _aspectGroup()',
                      self.collector)

    def test_the_existing_controls_are_reused_not_duplicated(self) -> None:
        """Catches: a second set of pool state diverging from the visible one."""

        self.assertEqual(1, APP_JS.count("function _aspectGroup"))
        for control in ("arBasePoolData", "arRatioPoolData"):
            with self.subTest(control=control):
                self.assertIn(f'id="{control}"', INDEX_HTML)


class AdmissionTests(unittest.TestCase):
    """Resolution happens here, before anything is queued."""

    def test_the_admitted_request_carries_concrete_dimensions(self) -> None:
        """Catches: a queued job that still has to roll, whose recipe and
        result would then disagree about what was asked for."""

        admitted = _validated_request_payload(body(
            aspect={"randomize_ratio": True, "ratio_pool": ["2:1"]}))
        self.assertIsInstance(admitted["width"], int)
        self.assertNotEqual(admitted["width"], admitted["height"])

    def test_both_the_request_and_the_decision_are_recorded(self) -> None:
        admitted = _validated_request_payload(body(
            aspect={"randomize_base": True, "base_pool": [1024],
                    "randomize_ratio": True, "ratio_pool": ["16:9"]}))
        aspect = admitted["aspect"]
        self.assertEqual((1024,), aspect.base_pool)
        self.assertEqual(("16:9",), aspect.ratio_pool)
        self.assertEqual(1, len(aspect.rolls))
        self.assertEqual(1024, aspect.rolls[0].base)
        self.assertEqual("16:9", aspect.rolls[0].ratio)
        self.assertEqual((admitted["width"], admitted["height"]),
                         (aspect.rolls[0].width, aspect.rolls[0].height))

    def test_a_fixed_request_records_no_aspect_at_all(self) -> None:
        """Absent means absent, as with hires, variation and inpaint."""

        self.assertIsNone(_validated_request_payload(body())["aspect"])

    def test_an_image_operation_keeps_the_source_geometry(self) -> None:
        """Catches: rolling a random shape for a job whose size comes from the
        owner's picture."""

        tiny = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAA"
                "AfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        admitted = _validated_request_payload(body(
            operation="img2img", source_image=tiny,
            aspect={"randomize_ratio": True, "ratio_pool": ["2:1"]}))
        self.assertEqual((768, 768), (admitted["width"], admitted["height"]))
        self.assertIsNone(admitted["aspect"])

    def test_a_malformed_pool_is_refused_at_admission(self) -> None:
        with self.assertRaises(refusal()):
            _validated_request_payload(body(
                aspect={"randomize_base": True, "base_pool": [999]}))

    def test_an_unknown_aspect_field_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            _validated_request_payload(body(aspect={"nonsense": True}))

    def test_the_seed_is_untouched_by_the_roll(self) -> None:
        """Dimension randomness must not perturb diffusion reproducibility."""

        rolled = _validated_request_payload(body(
            seed=12345,
            aspect={"randomize_base": True, "randomize_ratio": True}))
        fixed = _validated_request_payload(body(seed=12345))
        self.assertEqual(12345, rolled["seed"])
        self.assertEqual(fixed["seed"], rolled["seed"])


class Cycling:
    """A chooser that walks the pool in order, so a batch is fully determined.

    Deliberately not `random.choice` with a fixed seed: that would prove the
    rolls differ without proving WHICH roll each image got, and a bug that
    reused image one's answer for image two would still look random overall.
    """

    def __init__(self, *, start: int = 0) -> None:
        self.calls = start

    def __call__(self, sequence):
        items = list(sequence)
        chosen = items[self.calls % len(items)]
        self.calls += 1
        return chosen


class PerImageRollTests(unittest.TestCase):
    """One roll per image, chained, frozen. WP1.5 correction.

    THE DEFECT THESE CATCH: WP1.5 moved the roll from execution to admission --
    authorized -- but also collapsed it to ONE roll for the whole submission,
    which was not. The Extension calls `randomize_dimensions` inside
    `for img_num in range(total_images)` (`studio_generation.py:3136`, :3250),
    so a batch of four randomized images is four different shapes. Resolving
    once would have shipped four copies of one shape while the controls said
    otherwise -- the same class of silent-substitution defect the pools already
    refuse.
    """

    def test_each_image_rolls_separately(self) -> None:
        rolls = resolve_series(768, 768, 3, randomize_ratio=True,
                               ratio_pool=["1:1", "16:9", "2:1"],
                               chooser=Cycling())
        self.assertEqual(3, len(rolls))
        self.assertEqual(["1:1", "16:9", "2:1"], [r.ratio_label for r in rolls])
        shapes = {(r.width, r.height) for r in rolls}
        self.assertEqual(3, len(shapes), "a batch collapsed to one shape")

    def test_the_next_image_rolls_from_the_previous_result(self) -> None:
        """Catches: feeding every roll the SUBMITTED size instead of the last
        image's result.

        Upstream writes back with `gp.width, gp.height = new_w, new_h`, so
        image two derives its ratio from image one's rounded output. This case
        is chosen because the two readings genuinely disagree: from 1024x576
        with bases 640 then 768, chaining gives 1360x768 and starting over each
        time gives 1368x768. A test on a combination where they happen to
        agree would prove nothing.
        """

        rolls = resolve_series(1024, 576, 2, randomize_base=True,
                               base_pool=[640, 768], chooser=Cycling())
        self.assertEqual((1136, 640), (rolls[0].width, rolls[0].height))
        self.assertEqual((1360, 768), (rolls[1].width, rolls[1].height))
        self.assertNotEqual((1368, 768), (rolls[1].width, rolls[1].height))

    def test_a_single_image_submission_is_unchanged(self) -> None:
        """The correction must not alter the one-image behaviour in use now."""

        single = resolve_series(1024, 576, 1, randomize_ratio=True,
                                ratio_pool=["2:1"], chooser=first)
        direct = resolve(1024, 576, randomize_ratio=True,
                         ratio_pool=["2:1"], chooser=first)
        self.assertEqual(1, len(single))
        self.assertEqual(direct, single[0])

    def test_a_count_below_one_is_refused(self) -> None:
        with self.assertRaises(AspectRefused):
            resolve_series(768, 768, 0, randomize_ratio=True)

    def test_admission_freezes_one_roll_per_image(self) -> None:
        """The whole admission path, not just the resolver: three images in,
        three frozen entries out, each a concrete integer pair."""

        resolved = _resolved_aspect(
            {"aspect": {"randomize_ratio": True,
                        "ratio_pool": ["1:1", "16:9", "2:1"]}},
            operation="txt2img", width=768, height=768, image_count=3,
            chooser=Cycling())
        rolls = resolved["aspect"].rolls
        self.assertEqual(3, len(rolls))
        for roll in rolls:
            self.assertIsInstance(roll.width, int)
            self.assertIsInstance(roll.height, int)
        self.assertEqual(["1:1", "16:9", "2:1"], [r.ratio for r in rolls])
        self.assertEqual(3, len({(r.width, r.height) for r in rolls}))

    def test_the_flat_dimensions_agree_with_the_first_roll(self) -> None:
        """Catches: a second, separately-derived answer drifting from the
        recorded one, so the recipe and the executed size disagree."""

        resolved = _resolved_aspect(
            {"aspect": {"randomize_base": True, "base_pool": [640, 1024],
                        "randomize_ratio": True, "ratio_pool": ["16:9"]}},
            operation="txt2img", width=768, height=768, image_count=4,
            chooser=Cycling())
        first_roll = resolved["aspect"].rolls[0]
        self.assertEqual((resolved["width"], resolved["height"]),
                         (first_roll.width, first_roll.height))

    def test_execution_draws_no_dimension_randomness(self) -> None:
        """Catches: a re-roll at execution or retry, which would make a job
        produce a size its own recipe does not record.

        Enforced structurally -- the headless side cannot reach the randomizer
        at all -- rather than by asserting one code path behaves.
        """

        headless = APP_ROOT / "forge_headless"
        offenders = []
        for module in sorted(headless.glob("*.py")):
            text = module.read_text(encoding="utf-8")
            if ("aspect_randomizer" in text or "resolve_series" in text
                    or "randomize_dimensions" in text):
                offenders.append(module.name)
        self.assertEqual([], offenders)

    def test_an_admitted_roll_cannot_be_rewritten(self) -> None:
        """Catches: a later stage "adjusting" a decided size in place, which
        would leave the recipe describing an image that was never made.

        Asserting that re-reading the tuple returns the same numbers would
        prove nothing -- it is the same object. What is worth pinning is that
        the numbers are not writable at all.
        """

        payload = {"aspect": {"randomize_ratio": True,
                              "ratio_pool": ["1:1", "16:9", "2:1"]}}
        admitted = _resolved_aspect(payload, operation="txt2img",
                                    width=768, height=768, image_count=3,
                                    chooser=Cycling())
        roll = admitted["aspect"].rolls[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            roll.width = 4096  # type: ignore[misc]
        self.assertIsInstance(admitted["aspect"].rolls, tuple)
        self.assertEqual((roll.width, roll.height),
                         (admitted["width"], admitted["height"]))

    def test_the_long_side_uses_the_extensions_double_rounding(self) -> None:
        """Catches the port defect this correction surfaced.

        Upstream is `_round8(round(base * ratio_val))`; dropping the inner
        round looks harmless but Python rounds halves to even, so base 768 at
        2.39:1 (1835.52) gives 1832 without it and 1840 with it. A selectable
        pool combination, so the two are not interchangeable.
        """

        rolled = resolve(768, 768, randomize_base=True, base_pool=[768],
                         randomize_ratio=True, ratio_pool=["2.39:1"],
                         chooser=first)
        self.assertEqual((1840, 768), (rolled.width, rolled.height))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_ASPECT_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
