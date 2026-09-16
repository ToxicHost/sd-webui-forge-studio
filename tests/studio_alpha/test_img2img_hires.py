"""Hires for image operations, and the clip hole it must not reproduce. WP1.7.

THE DEFECT THESE CATCH

`enable_hr` is a `StableDiffusionProcessingTxt2Img` field, so Neo's built-in
second pass cannot be reached from an img2img job. Studio's port set it only in
the txt2img branch and mentioned Hires nowhere else, so the Hires controls
reached NOTHING on any Canvas img2img or inpaint job -- the same dead-control
shape as the aspect randomiser and Soft Inpainting before them.

The Extension does give them a second pass, `run_hires_fix`
(`studio_generation.py:520-676`), called for every non-txt2img job with Hires on
and scale above 1 and with NO mask exclusion (:3456) -- so ordinary img2img and
inpaint both receive it. Verified from the source, not assumed.

AND THE ORDER MATTERS MORE THAN THE FEATURE. The Extension clips at :3431 and
upscales at :3456, so its second pass denoises with nothing clipping it
afterwards. Studio runs the pass BEFORE its existing clip, so that clip lands
after Hires and the post-Auto-Detail clip is untouched.

Review: `Evidence/source-review/WP1.7-img2img-hires.md`.

WHAT IS DELIBERATELY NOT TESTED HERE

Neo's upscalers and its denoise. `shared.sd_upscalers` and
`process_images_inner` are called, not reimplemented. What is tested is whether
they are reached, on which operations, at what size, and in what order.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_IMG2IMG_HIRES_TESTS = 17

PORT_SOURCE = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
    encoding="utf-8")


def png_data_url(width: int, height: int, colour) -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def admitted(**generation):
    """A request through the REAL validator."""

    from forge_studio.contracts import GenerationRequest
    from forge_studio.presentation import _validated_request_payload

    body = {"model": "m", "generation": {
        "positive_prompt": "a coat", "negative_prompt": "", "seed": 7,
        "steps": 4, "cfg_scale": 4.0, "width": 64, "height": 64}}
    body["generation"].update(generation)
    return GenerationRequest(**_validated_request_payload(body))


def translated(**generation):
    """...and through the REAL translation, which is where fields get lost."""

    from forge_headless.studio_generation import translate_request

    return translate_request(admitted(**generation),
                             request_id="rid").headless_request


HIRES = {"enabled": True, "scale": 2.0, "upscaler": "",
         "second_pass_steps": 6, "denoising_strength": 0.4}
IMAGE_OP = {
    "operation": "img2img",
    "source_image": png_data_url(64, 64, (120, 40, 40)),
    "denoising_strength": 0.6,
}
INPAINT_OP = dict(IMAGE_OP, operation="inpaint",
                  mask=png_data_url(64, 64, (255, 255, 255)),
                  inpaint={"mask_blur": 4, "fill": 1, "full_resolution": False,
                           "padding": 32, "invert": False})


class TranslationSeamTests(unittest.TestCase):
    """Contract -> translation, the join that silently dropped `soft`."""

    def test_hires_survives_translation_for_img2img(self) -> None:
        headless = translated(hires=dict(HIRES), **IMAGE_OP)
        self.assertTrue(headless.enable_hr)
        self.assertEqual(2.0, headless.hr_scale)
        self.assertEqual(6, headless.hr_second_pass_steps)
        self.assertEqual(0.4, headless.hr_denoising_strength)

    def test_hires_survives_translation_for_inpaint(self) -> None:
        """The Extension applies no mask exclusion, so this must arrive too."""

        headless = translated(hires=dict(HIRES), **INPAINT_OP)
        self.assertTrue(headless.enable_hr)
        self.assertEqual(2.0, headless.hr_scale)

    def test_absent_hires_stays_absent_on_an_image_operation(self) -> None:
        self.assertFalse(translated(**IMAGE_OP).enable_hr)


class PassRoutingTests(unittest.TestCase):
    """When the second pass runs at all. No engine needed to answer this."""

    def _port(self):
        from forge_headless.live_generation_port import StudioLiveGenerationPort

        return StudioLiveGenerationPort.__new__(StudioLiveGenerationPort)

    def _processed(self):
        from PIL import Image

        return types.SimpleNamespace(images=[Image.new("RGB", (64, 64))])

    def _source(self):
        return types.SimpleNamespace(image=None, mask=None, mask_present=False)

    def _run(self, request, source=None, processed=None):
        processed = processed or self._processed()
        before = list(processed.images)
        self._port()._img2img_hires(request, source, processed, None)
        return before, processed.images

    def test_a_disabled_hires_does_nothing(self) -> None:
        before, after = self._run(
            types.SimpleNamespace(enable_hr=False, hr_scale=2.0,
                                  operation="img2img"),
            self._source())
        self.assertEqual(before, after)

    def test_a_scale_of_one_runs_a_refinement_pass(self) -> None:
        """Inverted 2026-08-20, AR6.5.

        This asserted that scale 1.0 does nothing, and called that catching
        "an upscale to the same size burning a whole denoise". But 1.0 is the
        control's own MINIMUM, so an owner can select it, Hires reads as ON
        everywhere in the UI, and the pass was skipped with no error and no
        metadata. A 1:1 second pass is a refinement denoise -- which is what
        the txt2img path already runs at the same value.

        Below 1.0 still does nothing, because that is a downscale.
        """

        # Asserted on the GUARD, not on the pixels, and deliberately.
        #
        # Every other test in this class works because the method returns
        # EARLY -- that is what "no engine needed" in the class docstring
        # means. A scale of 1.0 now proceeds, which is the whole point, and
        # proceeding needs `modules.processing` and therefore Torch. Running
        # the real pass belongs in a GPU session, not here; what this suite
        # can honestly answer is the routing decision, and that is this line.
        source = (APP_ROOT / "forge_headless"
                  / "live_generation_port.py").read_text(encoding="utf-8")
        self.assertIn("if scale < 1.0:", source)
        self.assertNotIn("if scale <= 1.0:", source)

    def test_a_scale_below_one_still_does_nothing(self) -> None:
        before, after = self._run(
            types.SimpleNamespace(enable_hr=True, hr_scale=0.5,
                                  operation="img2img"),
            self._source())
        self.assertEqual(before, after)

    def test_txt2img_does_nothing_here(self) -> None:
        """Catches: a SECOND second-pass on top of Neo's built-in one, which
        already ran inside `process_images_inner`."""

        before, after = self._run(
            types.SimpleNamespace(enable_hr=True, hr_scale=2.0,
                                  operation="txt2img"),
            self._source())
        self.assertEqual(before, after)

    def test_no_source_does_nothing(self) -> None:
        before, after = self._run(
            types.SimpleNamespace(enable_hr=True, hr_scale=2.0,
                                  operation="img2img"), None)
        self.assertEqual(before, after)


class UpscaleTests(unittest.TestCase):
    """Touching the real upscaler registry means touching Neo.

    `test_import_boundaries` asserts that importing `forge_studio` leaves NO
    `modules.*` in `sys.modules` -- an absolute check, not a delta -- so a test
    that pulls Neo in and walks away fails a LATER module, alphabetically. This
    file sorts before it, so the poisoning would land there and look like a
    defect in the boundary rather than in this fixture.

    Every Neo module this class causes to be imported is therefore removed
    again. Only the ones it added: pre-existing entries belong to whoever
    imported them.
    """

    def setUp(self) -> None:
        self._modules_before = set(sys.modules)
        self.addCleanup(self._forget_neo)

    #: Every root `test_import_boundaries` forbids. `modules_forge` is on the
    #: list because `from modules import shared` drags it in transitively --
    #: cleaning only `modules.*` left `modules_forge`, `modules_forge.config`
    #: and `modules_forge.forge_version` behind, and the failure surfaced two
    #: files later looking like a boundary defect.
    _NEO_ROOTS = ("modules", "modules_forge", "webui")

    def _forget_neo(self) -> None:
        for name in set(sys.modules) - self._modules_before:
            root = name.partition(".")[0]
            if root in self._NEO_ROOTS:
                del sys.modules[name]

    def test_an_unknown_upscaler_still_reaches_the_target_size(self) -> None:
        """Catches: a missing upscaler aborting the pass instead of falling
        back, which would turn a cosmetic setting into a failed generation."""

        from PIL import Image

        from forge_headless.live_generation_port import _upscale_for_hires

        out = _upscale_for_hires(Image.new("RGB", (64, 64)), "no-such", 128, 128)
        self.assertEqual((128, 128), out.size)

    def test_latent_and_empty_fall_back_without_touching_the_registry(self) -> None:
        """A latent upscaler has no latent here -- this pass starts from a
        decoded image."""

        from PIL import Image

        from forge_headless.live_generation_port import _upscale_for_hires

        for name in ("", "Latent", "None"):
            with self.subTest(upscaler=name):
                out = _upscale_for_hires(
                    Image.new("RGB", (64, 64)), name, 96, 96)
                self.assertEqual((96, 96), out.size)


class OrderingTests(unittest.TestCase):
    """The hole. These are about WHERE the pass sits, not whether it works."""

    def test_the_pass_runs_before_the_first_clip(self) -> None:
        """Catches: reproducing the Extension's clip-before-Hires hole, in
        which the second pass denoises with nothing clipping it afterwards."""

        hires_at = PORT_SOURCE.index("self._img2img_hires(request, source")
        clip_at = PORT_SOURCE.index("self._clip_inpaint(request, processed)")
        self.assertLess(hires_at, clip_at)

    def test_both_clips_still_exist(self) -> None:
        self.assertEqual(
            2, PORT_SOURCE.count("self._clip_inpaint(request, processed)"))

    def test_auto_detail_still_runs_after_hires(self) -> None:
        """Studio's contract is base -> Hires -> Auto Detail. The Extension's
        image path fires native AD inside `process_images_inner`, BEFORE its
        separate Hires step; Studio deliberately does not copy that."""

        hires_at = PORT_SOURCE.index("self._img2img_hires(request, source")
        detail_at = PORT_SOURCE.index("self.auto_detail_outcomes = self._auto_detail(")
        self.assertLess(hires_at, detail_at)


class NoCeilingTests(unittest.TestCase):
    """Standing owner policy: no product-defined maximum size, ever."""

    def test_the_target_size_is_floored_to_eight_with_no_upper_bound(self) -> None:
        """The Extension's own arithmetic is `int(w * scale) // 8 * 8` --
        floor-to-8, NOT `round8`'s nearest-8, and unbounded above."""

        self.assertIn("(int(image.width * scale) // 8) * 8", PORT_SOURCE)
        self.assertIn("(int(image.height * scale) // 8) * 8", PORT_SOURCE)

    def test_no_clamp_or_ceiling_appears_in_the_pass(self) -> None:
        start = PORT_SOURCE.index("def _img2img_hires")
        body = PORT_SOURCE[start:PORT_SOURCE.index("def _detail_pass")]
        for banned in ("min(", "MAX_", "clamp", "8192", "4096", "2048"):
            with self.subTest(token=banned):
                self.assertNotIn(banned, body)


class SoftInpaintingTests(unittest.TestCase):
    def test_the_bridge_is_attached_to_the_second_pass(self) -> None:
        """The Extension leaves its alwayson script installed and hands the
        pass an upscaled mask, so soft blending runs at the NEW resolution."""

        self.assertIn(
            "pass_.scripts = _soft_inpainting_bridge(request, mask is not None)",
            PORT_SOURCE)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_IMG2IMG_HIRES_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
