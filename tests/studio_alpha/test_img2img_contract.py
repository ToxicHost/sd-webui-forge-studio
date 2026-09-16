"""Img2Img: the operation, its source, and whether either survives the seam.

Phase 2A. The engine is not new -- `StableDiffusionProcessingImg2Img` has been
constructed in this tree the whole time for Auto Detail's detail pass. What is
new is an owner-supplied image reaching it, which means the risk is not the
sampler but the BOUNDARY: five recorded defects in this project reached Studio's
own request object and stopped at the translation into the headless one, and a
sixth never reached Studio's request at all.

So the load-bearing tests here are the ones that follow a value all the way to
the object handed to Neo, and the mutation guards that fail when it stops
short.

SCOPE: contract, decode, translation, and which processing class gets built.
Not pixels -- that is the real-pixel proof, and it needs a GPU.

AND NOT THE BROWSER. This suite drives `translate_request` directly. It does
NOT exercise the lifecycle collector or the HTTP allow-list, and both of those
are currently missing the operation:

    presentation.py `_GENERATION_FIELDS` carries no `operation`,
    `source_image`, `mask` or `denoising_strength` -- they are refused as
    unknown fields;
    app.js `doGenerate` submits `jobParams` and RETURNS from the lifecycle
    branch before the later branch that exports Canvas pixels.

So the shipping Generate button cannot submit an Img2Img job. Everything green
below is true of the seam and says nothing about the owner's path to it.
Green here is not "Img2Img works" -- WP1 owns closing that, and until it does,
this suite passing is exactly the kind of evidence this project keeps
mistaking for a feature.
"""

from __future__ import annotations

import base64
import io
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.generation_request import Operation  # noqa: E402
from forge_headless.input_assets import (  # noqa: E402
    MAX_SOURCE_IMAGE_BYTES,
    InputAssetRefused,
    decode_source,
)
from forge_headless.studio_generation import (  # noqa: E402
    BACKEND_DEFAULTED_FIELDS,
    STUDIO_REQUEST_FIELDS,
    translate_request,
)
from forge_studio.contracts import GenerationRequest, InputAsset  # noqa: E402


EXPECTED_IMG2IMG_TESTS = 30


def _png(width: int = 64, height: int = 48, mode: str = "RGB") -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 30, 60)[: 1 if mode == "L" else 3]).save(
        buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _asset(**overrides) -> InputAsset:
    fields = dict(data_url=_png(), media_type="image/png", width=64, height=48)
    fields.update(overrides)
    return InputAsset(**fields)


def _request(**overrides):
    fields = dict(
        positive_prompt="a cat", negative_prompt="", seed=7, steps=20,
        cfg_scale=4.0, width=64, height=48, sampler="Euler a",
        scheduler="Karras", preview_enabled=False,
        gpu_tile_compositing_requested=True, model_id="m",
        hires=None, auto_detail=None, variation=None,
        operation="txt2img", source_image=None, denoising_strength=0.75,
    )
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


def _translated(**overrides):
    return translate_request(_request(**overrides), request_id="t").headless_request


class OperationContractTests(unittest.TestCase):
    """Three members, and the reason there are not four."""

    def test_the_three_operations_exist(self) -> None:
        self.assertEqual(
            {"txt2img", "img2img", "inpaint"},
            {member.value for member in Operation})

    def test_soft_inpaint_is_not_an_operation(self) -> None:
        """It is a settings mode under INPAINT.

        A fourth member would make every consumer branch on a distinction the
        engine does not draw -- soft inpainting changes how the mask is
        applied, not what the job is.
        """
        self.assertNotIn("soft_inpaint", {member.value for member in Operation})

    def test_the_operation_is_carried_not_defaulted(self) -> None:
        """`BACKEND_DEFAULTED_FIELDS` means "Studio cannot express this".

        That stopped being true when the enum gained members, and the tuple is
        what `supported_generation_parameters()` returns -- so leaving it there
        would have told the page it may not choose.
        """
        self.assertIn("operation", STUDIO_REQUEST_FIELDS)
        self.assertNotIn("operation", BACKEND_DEFAULTED_FIELDS)

    def test_the_source_and_denoise_are_carried_too(self) -> None:
        for field in ("source_image", "denoising_strength"):
            with self.subTest(field=field):
                self.assertIn(field, STUDIO_REQUEST_FIELDS)

    def test_the_studio_request_can_hold_all_three(self) -> None:
        request = GenerationRequest(
            model_id="m", positive_prompt="p", negative_prompt="", seed=1,
            steps=1, cfg_scale=1.0, width=64, height=64,
            operation="img2img", source_image=_asset(), denoising_strength=0.3)
        self.assertEqual("img2img", request.operation)
        self.assertTrue(request.source_image.present)


class DecodeTests(unittest.TestCase):
    """What the browser said, against what the bytes are."""

    def test_a_valid_png_decodes_to_its_real_size(self) -> None:
        source = decode_source(_asset(), required=True)
        self.assertEqual((64, 48), (source.width, source.height))

    def test_the_decoded_image_is_rgb(self) -> None:
        """An alpha channel on an init image is a plane the sampler will not
        read. Dropped in one place rather than wherever it first matters."""
        source = decode_source(_asset(data_url=_png(mode="RGBA")), required=True)
        self.assertEqual("RGB", source.image.mode)

    def test_an_absent_source_is_refused_when_required(self) -> None:
        with self.assertRaises(InputAssetRefused) as raised:
            decode_source(None, required=True)
        self.assertEqual("SOURCE_IMAGE_REQUIRED", raised.exception.code)

    def test_an_absent_source_is_fine_when_not_required(self) -> None:
        self.assertIsNone(decode_source(None, required=False))

    def test_a_filesystem_path_is_not_a_source(self) -> None:
        """The browser names no file on the server. Ever."""
        with self.assertRaises(InputAssetRefused) as raised:
            decode_source(InputAsset(data_url="/etc/passwd"), required=True)
        self.assertEqual("SOURCE_IMAGE_INVALID", raised.exception.code)

    def test_an_unreadable_media_type_is_refused(self) -> None:
        with self.assertRaises(InputAssetRefused):
            decode_source(
                InputAsset(data_url="data:image/gif;base64,AAAA"), required=True)

    def test_malformed_base64_is_refused(self) -> None:
        with self.assertRaises(InputAssetRefused):
            decode_source(
                InputAsset(data_url="data:image/png;base64,!!!!"), required=True)

    def test_bytes_that_are_not_an_image_are_refused(self) -> None:
        payload = base64.b64encode(b"nonsense").decode()
        with self.assertRaises(InputAssetRefused):
            decode_source(
                InputAsset(data_url="data:image/png;base64," + payload),
                required=True)

    def test_a_declared_size_that_disagrees_is_refused(self) -> None:
        """Reported, not silently corrected.

        A page that disagrees with its own export is a bug worth surfacing,
        and quietly fixing it would hide the next one.
        """
        with self.assertRaises(InputAssetRefused) as raised:
            decode_source(_asset(width=999, height=999), required=True)
        self.assertIn("999", raised.exception.message)

    def test_the_byte_ceiling_is_checked_before_decoding(self) -> None:
        """A decoded ceiling can only be applied after spending the memory it
        exists to prevent."""
        oversized = InputAsset(
            data_url="data:image/png;base64," + ("A" * (MAX_SOURCE_IMAGE_BYTES + 8)))
        with self.assertRaises(InputAssetRefused) as raised:
            decode_source(oversized, required=True)
        self.assertIn("larger than", raised.exception.message)


class TranslationTests(unittest.TestCase):
    """The step five defects died at."""

    def test_txt2img_carries_no_source(self) -> None:
        headless = _translated()
        self.assertIs(Operation.TXT2IMG, headless.operation)
        self.assertIsNone(headless.source)

    def test_img2img_carries_the_decoded_source(self) -> None:
        headless = _translated(operation="img2img", source_image=_asset())
        self.assertIs(Operation.IMG2IMG, headless.operation)
        self.assertEqual((64, 48), (headless.source.width, headless.source.height))

    def test_the_source_arrives_as_a_pil_image(self) -> None:
        """Not a data URL, not bytes. The port hands this straight to Neo."""
        headless = _translated(operation="img2img", source_image=_asset())
        self.assertTrue(hasattr(headless.source.image, "size"))

    def test_the_denoise_survives(self) -> None:
        headless = _translated(
            operation="img2img", source_image=_asset(), denoising_strength=0.42)
        self.assertAlmostEqual(0.42, headless.denoising_strength)

    def test_img2img_without_a_source_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as raised:
            _translated(operation="img2img")
        self.assertEqual("SOURCE_IMAGE_REQUIRED", raised.exception.code)

    def test_txt2img_with_a_source_is_refused(self) -> None:
        """A contradiction, not a spare field.

        Dropping it silently is how a control comes to reach nothing.
        """
        with self.assertRaises(HeadlessError) as raised:
            _translated(operation="txt2img", source_image=_asset())
        self.assertEqual("UNSUPPORTED_OPERATION_COMBINATION", raised.exception.code)

    def test_an_unknown_operation_is_refused_by_name(self) -> None:
        with self.assertRaises(HeadlessError) as raised:
            _translated(operation="teleport")
        self.assertEqual("GENERATION_OPERATION_UNSUPPORTED", raised.exception.code)

    def test_an_absent_operation_still_means_txt2img(self) -> None:
        """Every existing caller and every double predates this field."""
        bare = types.SimpleNamespace(
            positive_prompt="p", negative_prompt="", seed=1, steps=1,
            cfg_scale=1.0, width=64, height=64, sampler="", scheduler="",
            preview_enabled=False, model_id="m",
            hires=None, auto_detail=None, variation=None)
        self.assertIs(
            Operation.TXT2IMG,
            translate_request(bare, request_id="t").headless_request.operation)


class ProcessingClassTests(unittest.TestCase):
    """Which Neo class the port builds, asserted over source.

    Constructing one for real needs a loaded model, so this reads the branch
    instead -- and the mutation guards below are what make that worth having.
    """

    PORT = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
        encoding="utf-8")

    def test_both_processing_classes_are_reachable(self) -> None:
        self.assertIn("StableDiffusionProcessingImg2Img", self.PORT)
        self.assertIn("StableDiffusionProcessingTxt2Img", self.PORT)

    def test_the_img2img_class_is_imported_inside_its_branch(self) -> None:
        """A txt2img job must not depend on a symbol it never uses.

        Importing both at the top broke 38 tests with nothing to do with
        img2img: the suite stubs `modules.processing`, and most doubles supply
        only the txt2img name. `_detail_pass` already imports it this way.
        """
        branch = self.PORT.split("if is_img2img:")[1].split("else:")[0]
        self.assertIn(
            "from modules.processing import StableDiffusionProcessingImg2Img",
            branch)

    def test_the_class_is_chosen_by_whether_a_source_exists(self) -> None:
        self.assertIn("is_img2img = source is not None", self.PORT)
        self.assertIn("processing_class = StableDiffusionProcessingImg2Img", self.PORT)

    def test_the_init_image_is_passed_as_a_list(self) -> None:
        """Neo iterates `init_images`. A bare image would iterate its pixels."""
        self.assertIn('"init_images": [source.image]', self.PORT)

    def test_img2img_hires_is_refused_KNOWN_DEFECT(self) -> None:
        """KNOWN DEFECT. This pins behaviour that is WRONG. WP2 replaces it.

        The refusal was justified as "Studio does not invent an img2img Hires."
        That premise is false and the owner said so: the EXTENSION implements
        it, as a per-image post-pass -- upscale, then a second
        `StableDiffusionProcessingImg2Img` refinement, with the base loading
        parameters snapshotted and restored around any temporary Hires
        components. Implementing it is not inventing it.

        The claim came from grepping ONE Extension function
        (`_run_attention_couple_image`) and generalising to the whole Extension.
        At least four other `enable_hr` sites exist -- `studio_api.py:4378`,
        `studio_generation.py:2922` and `:3262` among them -- none of which
        were read before the conclusion was written into a commit message.

        What survives of the original reasoning is only the narrow half:
        refusing BY NAME beats silently dropping the flag. That is why this
        test still asserts something rather than being deleted -- it keeps the
        current behaviour honest until WP2 arrives.

        INVERTED, WP1.7. The post-pass landed, so this is now the regression
        guard its previous self asked for: it fails if the refusal comes back,
        and equally if the refusal is merely deleted without `_img2img_hires`
        replacing it.

        The reading that retired it: `run_generation` calls `run_hires_fix` at
        `studio_generation.py:3456` for every non-txt2img job with Hires on and
        scale above 1, with NO mask exclusion.

        Handoff section 10.1, 45 (packet WP2.3), and the session bridge's
        "corrections owed".
        """
        self.assertNotIn(
            "Hires is not available for image-to-image yet", self.PORT)
        # The replacement behaviour, so this cannot pass merely because the
        # string was deleted and nothing put in its place.
        self.assertIn("def _img2img_hires", self.PORT)
        self.assertIn("self._img2img_hires(request, source", self.PORT)

    def test_one_construction_serves_both(self) -> None:
        """Two call sites would be two places for the next field to be added
        to, and only one of them would get it."""
        self.assertEqual(1, self.PORT.count("processing = processing_class("))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_IMG2IMG_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
