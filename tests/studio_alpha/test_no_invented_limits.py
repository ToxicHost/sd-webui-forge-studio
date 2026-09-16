"""Studio does not invent limits. AR6.1.

THE OWNER'S RULING

    "we should have no restrictions like that. Why the fuck would we want
     something that prevents a user from doing something they want? We are not
     the AI Generation Police"

and, on who put them there:

    "Not we. You. I never once asked for restrictions, Claude added them
     autonomously because it wanted to optimize for my hardware it seems except
     literally not everyone has my hardware."

Both correct. Neither limit removed here was requested, inherited, or
justified in the source.

WHAT WENT

`MAX_STEPS = 40` -- one constant, three walls: base steps, Auto Detail steps
and Hires steps. It bit at EXECUTION while the control and admission both
advertised 150, so a 60-step job was accepted, queued, shown as running, and
only then failed. Studio's own core offers 1-150 on its sampling slider
(`modules/processing_scripts/sampler.py:27`) and asserts nothing about steps
anywhere.

`GENERATION_DIMENSION_MISALIGNED` -- refused any dimension not divisible by 8.
Studio's core computes the latent with floor division
(`modules/processing.py:968`) and refuses nothing. OWNER-VERIFIED end to end:
500x500 produces a 496x496 image, silently, and the ruling on that was "if it
needs to silently adjust that's FINE."

I claimed TWICE that the alignment refusal reflected a real engine constraint.
Both claims were wrong, and both were caught by the owner running the thing
rather than by me reading it. `TheAlignmentClaimWasWrongTests` is the guard on
that specific correction, because a wrong theory in a test is how it becomes
permanent -- which had already happened once this month with Latent Noise.

WHAT STAYED, AND WHY IT IS NOT THE SAME THING

Floors. Fewer than one step is not a number of steps; a non-positive dimension
is not a size. Those are arithmetic, not opinions about what an owner may
want. `TheFloorsAreArithmeticTests`.

`DIMENSION_ALIGNMENT` and `_snap_to_alignment` also stay: the Hires target uses
them to COMPUTE a size, which is a calculation, not a refusal.

Review: `Evidence/source-review/AR6.1-invented-limits.md`.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_LIMIT_TESTS = 37

REQUEST_PY = (APP_ROOT / "forge_headless" / "generation_request.py").read_text(
    encoding="utf-8")
SHELL = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8")


def probe(**kwargs):
    """Every problem code a request produces, through the real validator."""

    from forge_headless.generation_request import (
        FirstImageRequest,
        ResidentModel,
        validate_request,
    )

    request = FirstImageRequest(request_id="p", model_id="m", **kwargs)
    result = validate_request(
        request,
        ResidentModel(model_id="m", family="sd1", resident=True),
        options_available=True, progress_installed=True,
        result_root_writable=True, generation_authorized=True,
    )
    return [problem["code"] for problem in (result.problems or [])]


def control(element_id: str) -> str:
    at = SHELL.index(f'id="{element_id}"')
    return SHELL[SHELL.rfind("<input", 0, at):SHELL.index(">", at) + 1]


class StepsHasNoCeilingTests(unittest.TestCase):
    """The named example. Three walls from one constant."""

    def test_the_constant_is_gone_entirely(self) -> None:
        """Not raised. GONE. A larger arbitrary ceiling is the same defect
        with a longer leash."""

        code = "\n".join(line for line in REQUEST_PY.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("MAX_STEPS", code)

    def test_base_steps_far_past_the_old_cap_are_accepted(self) -> None:
        """Asserted well clear of 40, because 41 would also pass against an
        off-by-one that still capped."""

        for steps in (41, 60, 150, 500, 2000):
            with self.subTest(steps=steps):
                self.assertNotIn("GENERATION_STEPS_OUT_OF_RANGE",
                                 probe(steps=steps))

    def test_hires_steps_have_no_ceiling(self) -> None:
        for steps in (60, 150, 400):
            with self.subTest(hr_steps=steps):
                self.assertNotIn(
                    "GENERATION_HIRES_STEPS_OUT_OF_RANGE",
                    probe(enable_hr=True, hr_scale=1.5,
                          hr_second_pass_steps=steps))

    def test_the_control_no_longer_advertises_a_ceiling(self) -> None:
        """The other half of the same lie. A backend that accepts 500 while
        the control clamps at 150 is the defect pointing the other way."""

        for element in ("paramSteps", "paramHrSteps"):
            with self.subTest(control=element):
                self.assertNotIn("data-max", control(element))


class AdmissionAgreesWithExecutionTests(unittest.TestCase):
    """The gap this suite originally had, and the reason it is here.

    The first version of these tests probed `validate_request` only -- the
    EXECUTION validator -- and passed while `presentation.py` still refused
    steps above 150 at ADMISSION. So the control advertised no ceiling, the
    engine had no ceiling, and the product still said no: the same defect
    moved one layer, which is exactly what a test written against one layer
    cannot see.

    Found by an adversarial sweep, not by this suite. Now it is guarded.
    """

    def admit(self, **overrides):
        from forge_studio.presentation import _validated_request_payload

        payload = {"model": "m", "positive_prompt": "a cat", "negative_prompt": "",
                   "steps": 30, "width": 512, "height": 512, "seed": 1,
                   "cfg_scale": 7.0, "sampler": "Euler", "scheduler": "Simple"}
        payload.update(overrides)
        return _validated_request_payload(payload)

    def test_admission_accepts_steps_far_past_the_old_ceiling(self) -> None:
        for steps in (151, 500, 2000):
            with self.subTest(steps=steps):
                self.assertEqual(steps, self.admit(steps=steps)["steps"])

    def test_admission_still_refuses_the_floor(self) -> None:
        from forge_studio.presentation import PresentationError

        with self.assertRaises(PresentationError):
            self.admit(steps=0)

    def test_admission_accepts_a_misaligned_size(self) -> None:
        admitted = self.admit(width=500, height=777)
        self.assertEqual(500, admitted["width"])
        self.assertEqual(777, admitted["height"])

    def test_no_steps_ceiling_survives_anywhere_in_admission(self) -> None:
        """A grep, deliberately. The three sites that were missed were three
        DIFFERENT helpers -- `_bounded_int`, `_whole` and a hand-rolled
        comparison -- so a test that probed one shape would have missed the
        others."""

        source = (APP_ROOT / "forge_studio" / "presentation.py").read_text(
            encoding="utf-8")
        code = chr(10).join(line for line in source.splitlines()
                            if not line.lstrip().startswith("#"))
        self.assertNotIn('"steps", 1, 150', code)
        self.assertNotIn('f"{where}.steps", 0, 150', code)
        self.assertNotIn("0 <= steps <= 150", code)


class DimensionsAreNotRefusedTests(unittest.TestCase):
    def test_the_values_the_owner_tested_are_accepted(self) -> None:
        """500, 900 and 777 -- the exact numbers from the ruling."""

        for size in (500, 900, 777, 1001, 770):
            with self.subTest(size=size):
                self.assertEqual([], probe(width=size, height=size))

    def test_there_is_still_no_size_ceiling(self) -> None:
        """The 2026-08-16 decision, unchanged and re-asserted here so the two
        rulings are guarded together."""

        for size in (2304, 4096, 8192, 16384):
            with self.subTest(size=size):
                self.assertEqual([], probe(width=size, height=size))

    def test_the_refusal_code_is_gone_from_the_module(self) -> None:
        code = "\n".join(line for line in REQUEST_PY.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("GENERATION_DIMENSION_MISALIGNED", code)

    def test_width_and_height_still_advertise_no_maximum(self) -> None:
        for element in ("paramWidth", "paramHeight"):
            with self.subTest(control=element):
                self.assertNotIn("data-max", control(element))


class TheSeedIsNotSubstitutedTests(unittest.TestCase):
    """AR6.2. The worst class: not refused, quietly REPLACED.

    A seed above 4294967295 fell through `_resolveSubmittedSeed`'s guard into
    the random branch, so the owner got a different image and was told nothing
    -- the exact thing the comment ten lines above it says Studio must not do.
    Nothing in the core wants 32 bits: the seed control is a bare `gr.Number`
    with no bounds, `torch.manual_seed` takes 64, and Philox narrows with
    `uint32(key)`, which WRAPS and stays reproducible from the typed value.
    """

    APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
        encoding="utf-8")

    def admit(self, **overrides):
        from forge_studio.presentation import _validated_request_payload

        payload = {"model": "m", "positive_prompt": "a cat",
                   "negative_prompt": "", "steps": 30, "width": 512,
                   "height": 512, "seed": 1, "cfg_scale": 7.0,
                   "sampler": "Euler", "scheduler": "Simple"}
        payload.update(overrides)
        return _validated_request_payload(payload)

    def test_a_large_seed_arrives_unchanged(self) -> None:
        """Asserted on the VALUE, not on the absence of an error. The defect
        was a substitution, so a test that only checked for a refusal would
        have passed against it."""

        for seed in (4294967295, 4294967296, 5_000_000_000, 10 ** 15):
            with self.subTest(seed=seed):
                self.assertEqual(seed, self.admit(seed=seed)["seed"])

    def test_minus_one_still_means_surprise_me(self) -> None:
        """The one substitution that is legitimate, and must survive: -1 has
        no concrete value to preserve, and resolving it is what keeps the
        reported seed honest."""

        self.assertEqual(-1, self.admit(seed=-1)["seed"])

    def test_below_minus_one_is_still_refused(self) -> None:
        from forge_studio.presentation import PresentationError

        with self.assertRaises(PresentationError):
            self.admit(seed=-2)

    def test_the_browser_guard_has_no_ceiling(self) -> None:
        at = self.APP_JS.index("function _resolveSubmittedSeed()")
        body = self.APP_JS[at:at + 400]
        self.assertIn("Number.isFinite(raw) && raw >= 0) return raw;", body)
        self.assertNotIn("SEED_MAX", body)

    def test_a_variation_seed_of_zero_survives(self) -> None:
        """0 is a legal seed AND falsy, so `|| -1` turned it into "random".
        Asserted on 0 specifically -- it is the only value that expression
        destroyed, so any other number would pass against the bug."""

        self.assertEqual(0, self.admit(variation={"subseed": 0,
                                                  "subseed_strength": 0.5})
                         ["variation"].subseed)

    def test_both_seed_fields_use_one_reader(self) -> None:
        """The bug was that two seed fields in one file disagreed. A shared
        helper is what stops them drifting apart again."""

        self.assertIn("function _seedFieldValue(elementId)", self.APP_JS)
        self.assertNotIn('parseInt(document.getElementById("paramVarSeed")?.value) || -1',
                         self.APP_JS)


class ASourceImageIsNotCappedTests(unittest.TestCase):
    """AR6.3. The last EXECUTION-tier wall.

    `MAX_SOURCE_PIXELS = 4096 * 4096` refused any source image above 16.7
    megapixels, so a photograph from any real camera could not be used for
    img2img or inpaint. Its stated justification -- that the byte ceiling
    "does not bound the allocation" -- was false HERE, because `decode_source`
    calls `load()` to force the decode eleven lines before the check ran. The
    allocation it claimed to prevent had already happened.
    """

    def asset(self, image):
        import base64
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

        class _Asset:
            data_url = "data:image/png;base64," + encoded
            width = image.width
            height = image.height

        return _Asset()

    def test_an_image_past_the_old_cap_is_accepted(self) -> None:
        """Decoded for real. The defect was a refusal of real files, so a
        source-level assertion would not have proved anything.

        Mode "L" keeps this to ~17 MB rather than ~52 MB: the cap was on PIXEL
        COUNT, so one byte per pixel exercises it exactly as three would.
        """

        from PIL import Image

        from forge_headless.input_assets import decode_source

        pixels = 5000 * 3500                       # 17.5 MP, past 16.7 MP
        self.assertGreater(pixels, 4096 * 4096)
        decoded = decode_source(
            self.asset(Image.new("L", (5000, 3500), 128)), required=True)
        self.assertEqual(5000, decoded.width)
        self.assertEqual(3500, decoded.height)

    def test_the_constant_cannot_be_imported_back(self) -> None:
        from forge_headless import input_assets

        self.assertFalse(hasattr(input_assets, "MAX_SOURCE_PIXELS"))

    def test_a_decompression_bomb_is_a_named_refusal_not_a_crash(self) -> None:
        """The mandatory other half of the same change.

        `DecompressionBombError` subclasses none of the three exceptions the
        decoder caught, so Studio's own cap was the only thing keeping
        execution away from it. Removing one without the other turns a named
        refusal into an uncaught error.

        Exercised by lowering PILLOW'S threshold rather than by building a 90
        megapixel file, which would make the suite slow to prove a point about
        exception handling.
        """

        from PIL import Image

        from forge_headless.input_assets import InputAssetRefused, decode_source

        asset = self.asset(Image.new("L", (64, 64), 128))
        original = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 100          # 64x64 is 4096, comfortably past
        try:
            with self.assertRaises(InputAssetRefused) as caught:
                decode_source(asset, required=True)
        finally:
            Image.MAX_IMAGE_PIXELS = original
        self.assertEqual("SOURCE_IMAGE_INVALID", caught.exception.code)
        self.assertIn("Pillow", str(caught.exception))

    def test_the_byte_ceiling_still_applies(self) -> None:
        """It was not what was removed, and it is a TRANSPORT bound rather
        than a statement about how big a picture may be."""

        from forge_studio.asset_service import MAX_ASSET_BYTES
        from forge_studio.contracts import (
            MAX_SOURCE_IMAGE_BYTES as CONTRACT_BYTES,
        )
        from forge_headless.input_assets import MAX_SOURCE_IMAGE_BYTES

        # Asserted as a FLOOR and as agreement, not as a specific number. The
        # value moved once already (32 -> 256 MB at AR6.10) and pinning it
        # exactly just makes the next honest change look like a regression.
        self.assertGreaterEqual(MAX_SOURCE_IMAGE_BYTES, 200 * 1024 * 1024)
        # Three copies, and a ceiling that lives on both sides of a boundary
        # is only useful while they agree.
        self.assertEqual(MAX_SOURCE_IMAGE_BYTES, CONTRACT_BYTES)
        self.assertEqual(MAX_SOURCE_IMAGE_BYTES, MAX_ASSET_BYTES)


class EveryControlRangeIsHonouredTests(unittest.TestCase):
    """Whatever the control offers, admission accepts. AR6.4.

    Derived from the MARKUP rather than from a list of numbers, so widening a
    control without widening admission fails here instead of failing an owner
    mid-generation. Four controls were offering ranges the server refused:

        Hires CFG          control 0-30    admission 1-24   -> 25-30 refused
        Contrast Boost     control 0-32    admission 1-32   -> 0 refused
        Diff Contrast      control 0-16    admission 0-8    -> 9-16 refused
        Only-masked pad    control 0-512   admission 0-256  -> 257-512 refused

    Three were fixed by WIDENING admission, because nothing in the engine
    required the tighter number. One was fixed by narrowing the CONTROL, and
    it is the only genuine constraint in the set: `inpaint_detail_preservation`
    is used as `pow_(1 / value)` at soft_inpainting.py:81, so 0 is a division
    by zero. That is arithmetic, not policy, and there the control is what
    must stop advertising it.
    """

    PNG = "data:image/png;base64,aa"

    def advertised(self, key, attr="id"):
        at = SHELL.index(f'{attr}="{key}"')
        tag = SHELL[SHELL.rfind("<", 0, at):SHELL.index(">", at) + 1]
        found = []
        for names in (("data-min", "min"), ("data-max", "max")):
            value = None
            for name in names:
                match = re.search(r'\b' + name + r'="([-0-9.]+)"', tag)
                if match:
                    value = float(match.group(1))
                    break
            found.append(value)
        self.assertIsNotNone(found[0], f"{key} advertises no minimum")
        # A missing maximum is not a gap -- it is the POINT for the controls
        # whose ceilings were removed (steps, Hires steps, Hires scale, width,
        # height). The caller probes a deliberately large value instead.
        return found

    def admits(self, **overrides):
        from forge_studio.presentation import (
            PresentationError,
            _validated_request_payload,
        )

        payload = {"model": "m", "positive_prompt": "a", "negative_prompt": "",
                   "steps": 30, "width": 512, "height": 512, "seed": 1,
                   "cfg_scale": 7.0, "sampler": "Euler", "scheduler": "Simple"}
        payload.update(overrides)
        try:
            _validated_request_payload(payload)
        except PresentationError as error:
            return str(error)
        return None

    def _inpaint(self, group):
        image = {"data_url": self.PNG, "media_type": "image/png"}
        return self.admits(operation="inpaint", source_image=image,
                           mask=image, inpaint=group)

    def cases(self):
        return (
            ("paramCFG", "id",
             lambda v: self.admits(cfg_scale=v)),
            ("paramHrCFG", "id",
             lambda v: self.admits(hires={"enabled": True, "scale": 2.0,
                                          "cfg": v})),
            ("paramHrScale", "id",
             lambda v: self.admits(hires={"enabled": True, "scale": v})),
            ("paramSoftBias", "id",
             lambda v: self._inpaint({"soft": {"schedule_bias": v}})),
            ("paramSoftPreserve", "id",
             lambda v: self._inpaint({"soft": {"preservation": v}})),
            ("paramSoftContrast", "id",
             lambda v: self._inpaint({"soft": {"transition_contrast": v}})),
            ("paramSoftMaskInf", "id",
             lambda v: self._inpaint({"soft": {"mask_influence": v}})),
            ("paramSoftDiffThresh", "id",
             lambda v: self._inpaint({"soft": {"diff_threshold": v}})),
            ("paramSoftDiffContrast", "id",
             lambda v: self._inpaint({"soft": {"diff_contrast": v}})),
            ("padding", "data-key",
             lambda v: self._inpaint({"padding": int(v)})),
            ("blur", "data-key",
             lambda v: self._inpaint({"mask_blur": int(v)})),
        )

    def test_admission_accepts_both_ends_of_every_control(self) -> None:
        for key, attr, probe in self.cases():
            low, high = self.advertised(key, attr)
            # A control with no advertised ceiling is probed well past any
            # number it ever had, so "no maximum" is asserted rather than
            # merely skipped.
            ends = (("minimum", low), ("maximum", high if high is not None
                                       else max(low, 1) * 1000))
            for label, value in ends:
                with self.subTest(control=key, end=label, value=value):
                    self.assertIsNone(
                        probe(value),
                        f"{key} advertises {label} {value:g} and admission "
                        f"refuses it")

    def test_the_division_guard_is_the_only_narrowed_control(self) -> None:
        """`transition_contrast` is the one control narrowed rather than
        widened, and this pins WHY -- so a future reader does not "restore" a
        0 that divides by zero at soft_inpainting.py:81."""

        low, _high = self.advertised("paramSoftContrast")
        self.assertEqual(1.0, low)
        engine = (APP_ROOT / "extensions-builtin" / "soft-inpainting"
                  / "scripts" / "soft_inpainting.py").read_text(encoding="utf-8")
        self.assertIn("pow_(1 / settings.inpaint_detail_preservation)", engine)


class ModelsInSubfoldersAreVisibleTests(unittest.TestCase):
    """AR6.6. An organised model library showed up empty.

    Two limits stacked, and the canonical suite passed identically before and
    after removing them -- this behaviour had no test at all:

      1. `model_roots.py:294` built the LIVE catalogue without `recursive`,
         which defaults to False. Top level only.
      2. Even with it, `catalogue.py:567` guarded recursion on `depth == 0`,
         so a recursive scan descended exactly ONE level.

    So `Checkpoints/SDXL/Anime/model.safetensors` was invisible, with no
    message and no hint that a subfolder existed.
    """

    def tree(self, directory):
        root = Path(directory) / "models"
        (root / "SDXL" / "Anime" / "Sub").mkdir(parents=True)
        for relative in ("top.safetensors",
                         "SDXL/one_deep.safetensors",
                         "SDXL/Anime/two_deep.safetensors",
                         "SDXL/Anime/Sub/three_deep.safetensors"):
            (root / relative).write_bytes(b"x")
        return root

    def test_a_model_three_levels_down_is_found(self) -> None:
        import tempfile

        from forge_headless.catalogue import ModelCatalogue

        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(directory)
            catalogue = ModelCatalogue(root, workspace_root=Path(directory),
                                       recursive=True, allow_external=True)
            found = {entry.relative_location for entry in catalogue.enumerate()}
        self.assertEqual(
            {"top.safetensors", "SDXL/one_deep.safetensors",
             "SDXL/Anime/two_deep.safetensors",
             "SDXL/Anime/Sub/three_deep.safetensors"},
            found)

    def test_the_live_construction_asks_for_recursion(self) -> None:
        """The half that made the other half moot. `recursive` defaults to
        False, so the depth fix alone would have changed nothing on a real
        install."""

        source = (APP_ROOT / "forge_headless" / "model_roots.py").read_text(
            encoding="utf-8")
        # CODE only. A first version of this sliced to the next ")" and landed
        # inside the comment above the argument, which mentions "Flux/)".
        code = chr(10).join(line for line in source.splitlines()
                            if not line.lstrip().startswith("#"))
        at = code.index("catalogue = ModelCatalogue(")
        call = code[at:code.index(")", at)]
        self.assertIn("recursive=True", call)

    def test_the_walk_is_still_bounded_by_budget_not_depth(self) -> None:
        """Depth was never what made the scan finite, and removing it must not
        have removed the thing that did."""

        from forge_headless.catalogue import (
            MAX_CATALOGUE_ENTRIES,
            MAX_SCANNED_ENTRIES,
        )

        self.assertEqual(20_000, MAX_SCANNED_ENTRIES)
        self.assertEqual(4_096, MAX_CATALOGUE_ENTRIES)
        source = (APP_ROOT / "forge_headless" / "catalogue.py").read_text(
            encoding="utf-8")
        scan = source[source.index("    def _scan("):]
        scan = scan[:scan.index(chr(10) + "    def ", 10)]
        self.assertIn("budget.spend()", scan)
        self.assertIn("MAX_CATALOGUE_ENTRIES", scan)

    def test_a_link_is_still_refused_rather_than_followed(self) -> None:
        """The reason a deep walk is safe: a reparse point is how a caller
        would try to leave the root, and it is rejected before recursion."""

        source = (APP_ROOT / "forge_headless" / "catalogue.py").read_text(
            encoding="utf-8")
        self.assertIn("if _is_reparse_point(entry):", source)


class TheFloorsAreArithmeticTests(unittest.TestCase):
    """What stayed, and why it is a different kind of thing."""

    def test_zero_steps_is_still_refused(self) -> None:
        self.assertIn("GENERATION_STEPS_OUT_OF_RANGE", probe(steps=0))

    def test_one_step_is_accepted(self) -> None:
        self.assertNotIn("GENERATION_STEPS_OUT_OF_RANGE", probe(steps=1))

    def test_a_non_positive_dimension_is_still_refused(self) -> None:
        self.assertIn("GENERATION_DIMENSION_OUT_OF_RANGE", probe(width=0))
        self.assertIn("GENERATION_DIMENSION_OUT_OF_RANGE", probe(height=-8))

    def test_negative_hires_steps_are_still_refused(self) -> None:
        """0 means "inherit the base pass", so the floor here is 0, not 1."""

        self.assertIn("GENERATION_HIRES_STEPS_OUT_OF_RANGE",
                      probe(enable_hr=True, hr_scale=1.5,
                            hr_second_pass_steps=-1))
        self.assertNotIn("GENERATION_HIRES_STEPS_OUT_OF_RANGE",
                         probe(enable_hr=True, hr_scale=1.5,
                               hr_second_pass_steps=0))


class TheCalculationSurvivedTests(unittest.TestCase):
    """Removing a refusal must not remove the arithmetic beside it."""

    def test_snap_to_alignment_still_exists(self) -> None:
        from forge_headless.generation_request import (
            DIMENSION_ALIGNMENT,
            _snap_to_alignment,
        )

        self.assertEqual(8, DIMENSION_ALIGNMENT)
        self.assertEqual(0, _snap_to_alignment(1001 * 1.5) % 8)

    def test_the_hires_target_still_uses_it(self) -> None:
        self.assertIn("_snap_to_alignment(int(value) * float(request.hr_scale))",
                      REQUEST_PY)


class TheAlignmentClaimWasWrongTests(unittest.TestCase):
    """The guard on the correction, not on the behaviour.

    Two separate claims were made that the alignment refusal reflected a real
    constraint in Studio's core:

      1. "the engine requires a multiple of 8"      -- it does not
      2. "the core's own UI snaps before submit"    -- it does not

    Both were asserted from reading structure, never verified, and both were
    disproved by the owner running it. A wrong theory written into a test is
    how it becomes permanent, so this asserts the record says so.
    """

    def test_the_core_floor_divides_rather_than_refusing(self) -> None:
        """The actual behaviour, read from the core itself."""

        processing = (APP_ROOT / "modules" / "processing.py").read_text(
            encoding="utf-8")
        self.assertIn("p.height // opt_f, p.width // opt_f", processing)

    def test_the_record_names_both_wrong_claims(self) -> None:
        record = (APP_ROOT.parent / "Evidence" / "source-review"
                  / "AR6.1-invented-limits.md").read_text(encoding="utf-8")
        self.assertIn("Both claims", record)
        self.assertIn("496x496", record)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_LIMIT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
