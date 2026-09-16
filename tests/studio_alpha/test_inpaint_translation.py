"""The mask, from an admitted request to the engine's own field names. WP1.4.

THE DEFECT THESE CATCH

Admission froze the mask from the moment WP1 landed, and the request carried it
all the way into `forge_headless` -- where two seams dropped it on the floor:

    `decode_source` hardcoded `mask=None, mask_present=False`, so the slot
    `SourceImage` has always had for a mask was never filled;

    the port chose its processing shape from `source is not None`, which cannot
    tell inpaint from img2img, so an inpaint job built an ordinary img2img
    object and sampled the whole frame.

Nothing failed. The request was valid, the job ran, an image came back, and the
owner's painted region meant nothing. That is the shape of defect this project
keeps finding, and it is why these tests assert on VALUES REACHING THE ENGINE'S
FIELD NAMES rather than on the presence of a dataclass attribute.

WHAT IS DELIBERATELY NOT TESTED HERE

Neo's mask algorithms. Binarisation beyond the one threshold Studio applies,
inversion, the blur, the full-resolution crop and the composite back all happen
inside `StableDiffusionProcessingImg2Img.init()`. Studio supplies settings and a
mask; re-asserting Neo's behaviour here would pin someone else's implementation
and break on their next release.
"""

from __future__ import annotations

import base64
import io
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_INPAINT_TRANSLATION_TESTS = 28


def png_data_url(width: int, height: int, colour) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def studio_request(**overrides):
    """An admitted Studio request, through the real validator."""

    from forge_studio.presentation import _validated_request_payload
    from forge_studio.contracts import GenerationRequest

    body = {"model": "m", "generation": {
        "positive_prompt": "a coat", "negative_prompt": "", "seed": 7,
        "steps": 4, "cfg_scale": 4.0, "width": 64, "height": 64}}
    body["generation"].update(overrides)
    payload = _validated_request_payload(body)
    return GenerationRequest(**payload)


class MaskDecodeTests(unittest.TestCase):
    """The first dropped seam: `decode_source` never filled the mask slot."""

    def setUp(self) -> None:
        from forge_headless.studio_generation import translate_request

        self.translate = translate_request
        self.source = png_data_url(64, 64, (128, 90, 40))
        self.mask = png_data_url(64, 64, (255, 255, 255))

    def headless(self, **overrides):
        request = studio_request(**overrides)
        return self.translate(request, request_id="t").headless_request

    def test_an_inpaint_request_carries_a_decoded_mask(self) -> None:
        """Catches: the mask reaching translation and being discarded."""

        headless = self.headless(operation="inpaint", source_image=self.source,
                                 mask=self.mask)
        self.assertIsNotNone(headless.source.mask)
        self.assertTrue(headless.source.mask_present)

    def test_the_mask_is_single_channel_and_binary(self) -> None:
        """Studio applies Neo's own >128 threshold and nothing else.

        A soft edge here would be blurred a second time by `mask_blur` and the
        two would compound.
        """

        # BOTH sides of the threshold in one image: 200 is above it, 100 below.
        # A flat mask could not tell a real threshold from a no-op, which is
        # what the first version of this test actually asserted.
        from PIL import Image

        painted = Image.new("L", (64, 64), 100)
        painted.paste(200, (0, 0, 32, 64))
        buffer = io.BytesIO()
        painted.convert("RGB").save(buffer, format="PNG")
        mask_url = ("data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode("ascii"))

        headless = self.headless(operation="inpaint", source_image=self.source,
                                 mask=mask_url)
        mask = headless.source.mask
        self.assertEqual("L", mask.mode)
        # `tobytes` rather than `getdata`: same values, no deprecation, and it
        # reads as "every byte in the image" which is what is being asserted.
        self.assertEqual({0, 255}, set(mask.tobytes()),
                         "the mask is not binarised at Neo's own threshold")

    def test_an_img2img_request_carries_no_mask(self) -> None:
        """Absent means absent: an img2img job must be unchanged by WP1.4."""

        headless = self.headless(operation="img2img", source_image=self.source)
        self.assertIsNone(headless.source.mask)
        self.assertFalse(headless.source.mask_present)
        self.assertIsNone(headless.inpaint)

    def test_a_mask_at_a_different_size_is_resized_not_refused(self) -> None:
        """REPLACES a test that pinned a contradicted shape.

        The first implementation raised MASK_DIMENSION_MISMATCH, reasoning that
        a mask which does not line up is "a mask for a different image". That
        is wrong, and this test asserted it. The Canvas exports at document size
        while the owner generates at whatever width and height they chose, so
        the two differ as a matter of course, and the Extension resizes with
        LANCZOS (`_prepare_mask`, studio_generation.py:786).

        Catches: Studio refusing a job the Extension runs.
        """

        headless = self.headless(operation="inpaint", source_image=self.source,
                                 mask=png_data_url(32, 32, (255, 255, 255)))
        self.assertIsNotNone(headless.source.mask)
        self.assertEqual((64, 64), headless.source.mask.size,
                         "the mask was not resized to the source geometry")

    def test_an_empty_mask_is_refused_by_name(self) -> None:
        """A mask can be painted and then erased.

        The result is a black image that is NOT the frontend's `"null"`
        sentinel, so nothing upstream catches it and the job would inpaint
        against nothing. The Extension asks the same question
        (`np.array(mask).max() >= 10`).

        INTENTIONAL DIVERGENCE, recorded: the Extension downgrades to img2img,
        because its operation is derived. Studio's operation is DECLARED and
        admission already refuses inpaint-without-mask by name, so silently
        rewriting the declared operation would contradict the rule that no
        field is quietly ignored.
        """

        from forge_headless.input_assets import InputAssetRefused

        with self.assertRaises(InputAssetRefused) as caught:
            self.headless(operation="inpaint", source_image=self.source,
                          mask=png_data_url(64, 64, (0, 0, 0)))
        self.assertEqual("MASK_EMPTY", caught.exception.code)

    def test_a_mask_fault_is_not_reported_as_a_source_fault(self) -> None:
        """Catches: reusing SOURCE_IMAGE_* codes, which would tell an owner
        whose mask is wrong that their source image is wrong."""

        from forge_headless.input_assets import InputAssetRefused

        for mask in (png_data_url(64, 64, (0, 0, 0)), "data:image/png;base64,!!"):
            with self.subTest(mask=mask[:32]):
                with self.assertRaises(InputAssetRefused) as caught:
                    self.headless(operation="inpaint",
                                  source_image=self.source, mask=mask)
                self.assertTrue(caught.exception.code.startswith("MASK_"),
                                caught.exception.code)

    def test_the_supported_parameters_advertise_the_mask(self) -> None:
        """`supported_generation_parameters()` returns this tuple verbatim, so
        a field translated but unlisted is a capability the page is never told
        it has."""

        from forge_headless.studio_generation import STUDIO_REQUEST_FIELDS

        self.assertIn("mask", STUDIO_REQUEST_FIELDS)
        self.assertIn("inpaint", STUDIO_REQUEST_FIELDS)


class EngineFieldNameTests(unittest.TestCase):
    """The second dropped seam: the port never named the mask to the engine."""

    def setUp(self) -> None:
        from forge_headless.generation_request import InpaintOptions
        from forge_headless.live_generation_port import _inpaint_kwargs

        self.kwargs = _inpaint_kwargs
        self.options = InpaintOptions

    def build(self, **overrides):
        class _Request:
            inpaint = self.options(**overrides)

        class _Source:
            mask = "MASK-SENTINEL"

        return self.kwargs(_Request(), _Source())

    def test_the_mask_reaches_the_engine_under_its_own_name(self) -> None:
        """`mask=`, never `image_mask=`.

        Catches the trap directly: `image_mask` is declared `init=False` on
        `StableDiffusionProcessingImg2Img`, so passing it to the constructor is
        a TypeError rather than a mask. `init()` populates it from `mask`.
        """

        built = self.build()
        self.assertEqual("MASK-SENTINEL", built["mask"])
        self.assertNotIn("image_mask", built)

    def test_every_studio_name_maps_to_the_engine_spelling(self) -> None:
        """The whole point of the port being the translation boundary."""

        built = self.build(mask_blur=9, fill=2, full_resolution=True,
                           padding=48)
        self.assertEqual(9, built["mask_blur"])
        self.assertEqual(2, built["inpainting_fill"])
        self.assertIs(True, built["inpaint_full_res"])
        self.assertEqual(48, built["inpaint_full_res_padding"])

    def test_inversion_is_an_int_because_the_engine_reads_an_int(self) -> None:
        """Catches: passing a bool where Neo compares against 1."""

        self.assertEqual(0, self.build(invert=False)["inpainting_mask_invert"])
        self.assertEqual(1, self.build(invert=True)["inpainting_mask_invert"])

    def test_the_defaults_are_the_engines_own(self) -> None:
        """A request carrying only a mask must behave as an untouched panel."""

        built = self.build()
        self.assertEqual(4, built["mask_blur"])
        self.assertEqual(1, built["inpainting_fill"])
        self.assertIs(False, built["inpaint_full_res"])
        self.assertEqual(32, built["inpaint_full_res_padding"])


class OperationSelectionTests(unittest.TestCase):
    """The port must read the DECLARED operation, not guess from the source."""

    def test_the_port_branches_on_the_declared_operation(self) -> None:
        """Catches the original defect exactly: `source is not None` cannot
        tell inpaint from img2img, so an admitted mask was dropped and inpaint
        executed as plain img2img with nothing reporting it."""

        source = (APP_ROOT / "forge_headless"
                  / "live_generation_port.py").read_text(encoding="utf-8")
        self.assertIn('operation_name == "inpaint"', source)
        self.assertIn("_inpaint_kwargs(request, source) if is_inpaint", source)


class OutsideMaskPreservationTests(unittest.TestCase):
    """The pass Studio did not have at all.

    Neo composites its own output against the original using the blurred mask,
    but the whole frame still goes through the autoencoder, so pixels OUTSIDE
    the painted region come back slightly changed. The Extension clips that
    (`_clip_to_mask`, studio_generation.py:1900) and Studio did not, which made
    "nothing outside your mask changed" false.

    These use synthetic images, so they prove the COMPOSITE. The VAE-leakage
    half is only observable live and is not claimed here.
    """

    def setUp(self) -> None:
        from forge_headless.live_generation_port import _clip_to_mask
        from PIL import Image

        self.clip = _clip_to_mask
        # Original is black; the "result" is entirely white, as if every pixel
        # changed. Only the masked half may survive that.
        self.original = Image.new("RGB", (64, 64), (0, 0, 0))
        self.result = Image.new("RGB", (64, 64), (255, 255, 255))
        self.mask = Image.new("L", (64, 64), 0)
        self.mask.paste(255, (0, 0, 32, 64))

    def test_pixels_outside_the_mask_come_from_the_original(self) -> None:
        """Catches: the absence of the clip entirely, which is what shipped."""

        clipped = self.clip(self.result, self.original, self.mask, 0)
        self.assertEqual((0, 0, 0), clipped.getpixel((60, 32)),
                         "a pixel outside the mask was allowed to change")

    def test_pixels_inside_the_mask_come_from_the_result(self) -> None:
        """The other half: clipping must not undo the generation."""

        clipped = self.clip(self.result, self.original, self.mask, 0)
        self.assertEqual((255, 255, 255), clipped.getpixel((4, 32)))

    def test_the_clip_boundary_is_dilated_past_the_blur(self) -> None:
        """The Extension dilates by `mask_blur * 3` so the clip sits outside
        Forge's Gaussian tail, leaving Forge's own transition untouched.

        With a blur the boundary moves OUTWARD, so strictly more of the
        result survives than with no blur -- never less.
        """

        tight = self.clip(self.result, self.original, self.mask, 0)
        dilated = self.clip(self.result, self.original, self.mask, 6)
        white = lambda image: sum(  # noqa: E731
            1 for pixel in image.convert("L").getdata() if pixel > 128)
        self.assertGreater(white(dilated), white(tight),
                           "the blur did not dilate the clip boundary")

    def test_a_failure_returns_the_unclipped_result(self) -> None:
        """A slightly leaky image beats losing one the owner waited for."""

        self.assertIs(self.result,
                      self.clip(self.result, None, self.mask, 0))

    def test_the_clip_runs_after_generation_and_after_auto_detail(self) -> None:
        """Both call points, because Auto Detail inpaints regions of its own
        and can move pixels outside the owner's mask.

        Catches: clipping once before Auto Detail, which Auto Detail undoes.
        """

        source = (APP_ROOT / "forge_headless"
                  / "live_generation_port.py").read_text(encoding="utf-8")
        self.assertEqual(2, source.count("self._clip_inpaint(request, processed)"),
                         "the clip must run after the base pass AND after "
                         "Auto Detail, as the Extension does")


class CleanupTests(unittest.TestCase):
    """An inpaint job attaches six more tensors and two more lists."""

    def test_every_mask_attribute_is_released(self) -> None:
        """Catches: an inpaint job leaving mask state on the processing object
        for the next clean txt2img to inherit."""

        from forge_headless.failure_cleanup import (
            _INSTANCE_FIELDS, _INSTANCE_LIST_FIELDS)

        for name in ("image_mask", "latent_mask", "mask", "nmask",
                     "mask_for_overlay", "image_conditioning"):
            with self.subTest(field=name):
                self.assertIn(name, _INSTANCE_FIELDS)
        for name in ("init_images", "overlay_images"):
            with self.subTest(field=name):
                self.assertIn(name, _INSTANCE_LIST_FIELDS)


class AdmissionToSessionCapabilityTests(unittest.TestCase):
    """The gate BETWEEN the two layers these tests already covered.

    THE DEFECT THESE CATCH, observed live on 2026-08-18:

        FAILED job=studio-job-000006 code=REQUEST_UNSUPPORTED reason=inpaint

    `ResidentModel.supported_operations` defaulted to txt2img only -- a leftover
    from when `Operation` had one member -- and NOTHING in production ever
    assigned it. The one real construction site, `HeadlessSessionLoader.
    _default_session_factory`, omitted the field, so every live session
    declared itself txt2img-only and `validate_request` refused every inpaint
    and img2img job before the mask decode, the field mapping or the composite
    could run.

    Every other test in this file passed throughout. They build their own
    `ResidentModel`, or start below validation, or stop at admission -- so the
    break sat precisely in the gap between the layers under test. That is the
    reason this class exercises the REAL FACTORY rather than a fixture: a
    capability default is only meaningful in the object the product actually
    builds.
    """

    def _real_resident_model(self):
        import tempfile
        import types

        from forge_headless.session_loader import HeadlessSessionLoader

        with tempfile.TemporaryDirectory() as root:
            session = HeadlessSessionLoader._default_session_factory(
                profile=types.SimpleNamespace(profile_id="pid-1",
                                              family="anima"),
                port=types.SimpleNamespace(),
                result_root=Path(root))
        return session.resident_model

    def test_a_real_session_admits_every_operation_studio_offers(self) -> None:
        from forge_headless.generation_request import Operation

        declared = set(self._real_resident_model().supported_operations)
        self.assertEqual({member.value for member in Operation}, declared)

    def test_a_real_session_does_not_refuse_inpaint(self) -> None:
        """The exact live failure, at the exact line that produced it."""

        from forge_headless.generation_request import Operation

        model = self._real_resident_model()
        self.assertIn(Operation.INPAINT.value, model.supported_operations)
        self.assertIn(Operation.IMG2IMG.value, model.supported_operations)

    def test_the_capability_is_stated_and_not_inherited(self) -> None:
        """Catches: the factory going back to relying on the dataclass
        default, which is how one silent default disabled the feature."""

        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8")
        self.assertIn("supported_operations=", source)


class SupersededScopeTests(unittest.TestCase):
    """Inpaint Sketch is ruled OUT, and the ruling needs guarding.

    Owner decision, 2026-08-19: Studio's Canvas supersedes Forge/Neo's separate
    Inpaint Sketch workflow. Painting directly on the image is already a native
    Canvas operation, so a second mode would duplicate it and rebuild a
    Gradio-era flow Studio exists to replace.

    THE DEFECT THESE CATCH runs the opposite way to most tests in this suite.
    Everywhere else the risk is a feature that looks present and does nothing;
    here the risk is a future session reading the Extension, finding
    `if inpaint_mode == "Inpaint Sketch"`, filing it as missing parity, and
    building it. Superseded is not missing, and a decision that lives only in
    prose gets re-litigated.

    What was checked before ruling: `_prepare_mask`
    (`studio_generation.py:778-800`) is SHARED by every inpaint mode, and its
    sketch-exclusive part is one block -- a MaxFilter dilation of 1.5% of the
    short side (:787-792). Everything else in it -- the LANCZOS resize, the
    `>= 10` emptiness check, the `> 128` binarisation -- is ordinary inpaint
    behaviour and is already ported in `input_assets.decode_mask`. So nothing
    shared is lost by declining the mode.
    """

    def test_no_sketch_operation_exists(self) -> None:
        from forge_headless.generation_request import Operation

        self.assertEqual({"txt2img", "img2img", "inpaint"},
                         {member.value for member in Operation})

    def test_no_studio_module_implements_it(self) -> None:
        """Catches: the dilation, a mode field, or a sketch branch arriving in
        Studio's own code."""

        offenders = []
        for package in ("forge_studio", "forge_headless"):
            for module in sorted((APP_ROOT / package).rglob("*.py")):
                text = module.read_text(encoding="utf-8").lower()
                if "inpaint_sketch" in text or "inpaint sketch" in text:
                    offenders.append(str(module.relative_to(APP_ROOT)))
        self.assertEqual([], offenders)

    def test_no_frontend_file_reintroduces_the_mode(self) -> None:
        """The residual branches were REMOVED, so the page must stay clean.

        Catches: the mode literal, or either dead flag, coming back into any
        frontend script -- which is how a superseded feature returns, since a
        branch keyed on a value nothing sets looks harmless in review.

        The mode literal is checked EXACTLY, case-sensitively. The ledger key
        `INPAINT_SKETCH` is a different string and is deliberately still
        allowed, so documentation and comments can name the decision without
        tripping a guard aimed at behaviour.
        """

        offenders = []
        frontend = APP_ROOT / "forge_studio" / "frontend"
        for script in sorted(frontend.rglob("*.js")):
            text = script.read_text(encoding="utf-8")
            for banned in ("Inpaint Sketch", "isIPSketch", "isInpaintSketch"):
                if banned in text:
                    offenders.append(f"{script.relative_to(APP_ROOT)}:{banned}")
        self.assertEqual([], offenders)

    def test_the_regional_mask_path_survived_the_removal(self) -> None:
        """The other half: removing the sketch branch from `exportMask` must
        not take Regional compositing or the empty-mask sentinel with it.

        `"null"` is the sentinel the collector tests for, and it is a STRING --
        a truthy one. Losing it here would mean an empty mask silently
        submitting as a painted one, which is a defect this project has
        already had once.
        """

        source = (APP_ROOT / "forge_studio" / "frontend"
                  / "canvas-core.js").read_text(encoding="utf-8")
        self.assertIn('S.inpaintMode === "Regional"', source)
        self.assertIn('return "null";', source)

    def test_the_shared_mask_behaviour_is_still_ported(self) -> None:
        """The other half of the ruling: declining the MODE must not drop the
        parts of `_prepare_mask` every inpaint job uses."""

        source = (APP_ROOT / "forge_headless" / "input_assets.py").read_text(
            encoding="utf-8")
        self.assertIn("LANCZOS", source)
        self.assertIn("getextrema", source)
        self.assertIn("128", source)

    def test_the_ledger_records_the_decision(self) -> None:
        """Catches: the ruling being dropped, or downgraded to `missing`."""

        import json

        ledger = json.loads(
            (APP_ROOT / "docs" / "15_PARITY_LEDGER.json").read_text(
                encoding="utf-8"))
        rows = {row["feature"]: row
                for row in ledger.get("feature_decisions", [])}
        self.assertIn("INPAINT_SKETCH", rows)
        self.assertEqual("superseded", rows["INPAINT_SKETCH"]["status"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_INPAINT_TRANSLATION_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
