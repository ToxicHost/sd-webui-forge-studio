"""The path the Generate button actually takes. WP1.

WHAT THIS CATCHES THAT NOTHING ELSE DOES

`test_img2img_contract.py` is green, 30 tests, and proves `translate_request`
builds the right processing object from an `Operation.IMG2IMG` request. It
drives that function directly. It cannot see that no such request can be
CONSTRUCTED by the shipping product, because two links earlier in the chain are
missing:

    presentation.py `_GENERATION_FIELDS` has no `operation`, `source_image`,
    `mask` or `denoising_strength`, so those fields are refused as unknown;

    app.js `doGenerate()` builds a txt2img-shaped body, submits it through
    `lifecycle.submitGenerate`, and RETURNS -- while the Canvas/source/mask
    export lives in the later legacy branch, thousands of characters past the
    return and unreachable on every lifecycle host.

So the seam is proven and the owner's Generate button cannot submit Img2Img.
That is the defect these tests exist for, and the reason they assert on the
SHIPPING path rather than on a helper: a suite can be entirely green about a
feature that no user can reach.

Handoff sections 10.2 and 31.4; WP1 packet 44.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_request_payload,
)

EXPECTED_LIFECYCLE_PATH_TESTS = 21

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")

#: A 1x1 PNG, so the shape of the request is under test rather than a decoder.
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg==")

#: A second, DIFFERENT image, so a hash comparison cannot pass by accident.
WHITE_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/"
    "PchI7wAAAABJRU5ErkJggg==")


def body(**overrides) -> dict:
    payload = {
        "model": "m",
        "generation": {
            "positive_prompt": "a coat",
            "negative_prompt": "",
            "seed": 7,
            "steps": 20,
            "cfg_scale": 4.0,
            "width": 64,
            "height": 64,
        },
    }
    payload["generation"].update(overrides)
    return payload


class HttpAdmissionTests(unittest.TestCase):
    """The allow-list is the first place an Img2Img request dies."""

    def test_an_image_operation_is_accepted(self) -> None:
        """`operation` was refused as an unknown field.

        Catches: the allow-list silently narrowing the product to txt2img while
        the lower contract advertises three operations.
        """

        accepted = _validated_request_payload(
            body(operation="img2img", source_image=TINY_PNG))
        self.assertEqual("img2img", accepted["operation"])

    def test_an_operation_without_its_input_is_refused_by_name(self) -> None:
        """The first draft of the test above sent `img2img` with no source and
        the invariant caught it, which is the invariant working.

        Catches: inferring the operation from whether a source happens to be
        present -- the behaviour the live port still has, and the reason a
        txt2img request carrying a stray source silently became img2img.
        """

        for operation, extra in (("img2img", {}), ("inpaint", {}),
                                 ("inpaint", {"source_image": TINY_PNG})):
            with self.subTest(operation=operation, extra=sorted(extra)):
                with self.assertRaises(PresentationError):
                    _validated_request_payload(body(operation=operation,
                                                    **extra))

    def test_a_txt2img_job_may_not_carry_an_image(self) -> None:
        """Catches: a stray source being silently dropped, or silently
        promoting the job to img2img."""

        for field in ("source_image", "mask"):
            with self.subTest(field=field):
                with self.assertRaises(PresentationError):
                    _validated_request_payload(body(**{field: TINY_PNG}))

    def test_a_server_path_is_refused_where_an_image_belongs(self) -> None:
        """The boundary rule, asserted rather than assumed.

        Catches: a client naming a file on the server. Downstream checking
        cannot recover from accepting the name in the first place.
        """

        with self.assertRaises(PresentationError):
            _validated_request_payload(
                body(operation="img2img",
                     source_image="/etc/passwd"))

    def test_a_source_image_is_accepted(self) -> None:
        accepted = _validated_request_payload(
            body(operation="img2img", source_image=TINY_PNG,
                 denoising_strength=0.6))
        self.assertIsNotNone(accepted["source_image"])
        self.assertEqual(0.6, accepted["denoising_strength"])

    def test_a_mask_is_accepted_for_inpaint(self) -> None:
        accepted = _validated_request_payload(
            body(operation="inpaint", source_image=TINY_PNG, mask=TINY_PNG,
                 denoising_strength=0.6))
        self.assertIsNotNone(accepted["mask"])
        self.assertIsNotNone(accepted["inpaint"])

    def test_an_unknown_field_is_still_refused(self) -> None:
        """The allow-list must widen for real fields, not stop being one.

        Catches: a fix that accepts the new fields by accepting everything.
        """

        with self.assertRaises(PresentationError):
            _validated_request_payload(body(not_a_real_field=1))


class FrozenAssetIdentityTests(unittest.TestCase):
    """What the server computed, not what the browser claimed.

    Studio QUEUES: `/api/generate` returns 202 and the job runs later, so the
    Canvas can be painted on between admission and execution. The Extension has
    no equivalent because its generate route consumes the payload in the same
    call -- reviewed and recorded in
    `Evidence/source-review/WP1.4-inpaint.md`, which is why this is a
    Studio-owned concept rather than a ported one.
    """

    def admitted(self, **overrides):
        return _validated_request_payload(body(**overrides))

    def test_the_source_carries_a_server_computed_hash(self) -> None:
        """Catches: a queued job with no record of which pixels it admitted."""

        asset = self.admitted(operation="img2img",
                              source_image=TINY_PNG)["source_image"]
        self.assertEqual(64, len(asset.content_hash))

    def test_source_and_mask_hash_independently(self) -> None:
        admitted = self.admitted(operation="inpaint", source_image=TINY_PNG,
                                 mask=WHITE_PNG)
        self.assertNotEqual(admitted["source_image"].content_hash,
                            admitted["mask"].content_hash)

    def test_identical_pixels_hash_the_same_however_they_are_spelled(self) -> None:
        """Over the DECODED payload, not the data URL string.

        Catches: hashing the string, which would give the same image two
        identities whenever the prefix or padding differed -- making the
        identity useless for the comparison it exists to support.
        """

        from forge_studio.presentation import _asset_content_hash

        payload = TINY_PNG.partition(",")[2]
        self.assertEqual(
            _asset_content_hash(TINY_PNG),
            _asset_content_hash("data:image/png;base64," + payload))

    def test_a_payload_that_will_not_decode_yields_no_identity(self) -> None:
        """Not a second validation error. `forge_headless` refuses it by name;
        this only establishes identity when there is something to identify."""

        from forge_studio.presentation import _asset_content_hash

        self.assertEqual("", _asset_content_hash("data:image/png;base64,!!!!"))
        self.assertEqual("", _asset_content_hash("data:image/png;base64,"))

    def test_a_txt2img_request_carries_no_asset_identity(self) -> None:
        admitted = self.admitted()
        self.assertIsNone(admitted["source_image"])
        self.assertIsNone(admitted["mask"])


class ShippingBrowserPathTests(unittest.TestCase):
    """Source-order assertions on `doGenerate`, which no runtime test reaches.

    A DOM test would be better and is WP1's browser evidence. This is the cheap
    guard that fails the moment the lifecycle branch goes back to returning
    before the Canvas is read -- the exact regression that made 30 green
    img2img tests describe an unreachable feature.
    """

    def setUp(self) -> None:
        start = APP_JS.index("async function doGenerate")
        self.source = APP_JS[start:start + 30000]

    def _offset(self, needle: str) -> int:
        position = self.source.find(needle)
        self.assertNotEqual(-1, position, f"{needle!r} is gone from doGenerate")
        return position

    def test_the_canvas_is_exported_before_the_lifecycle_submits(self) -> None:
        """Catches: `doGenerate` submitting a txt2img body and returning while
        the Canvas export sits in the later legacy branch, unreachable."""

        submit = self._offset("lifecycle.submitGenerate")
        export = self._offset("canvasB64")
        self.assertLess(
            export, submit,
            "the Canvas is read AFTER the lifecycle submission, so the "
            "shipping Generate button cannot send a source image")

    def test_the_empty_mask_sentinel_is_not_read_as_a_mask(self) -> None:
        """A regression in the commit that introduced the collector.

        `exportMask()` returns the STRING "null" when mask mode is on and
        nothing has been painted (canvas-core.js:3250). It is truthy, so
        `maskB64 ? "inpaint" : "img2img"` called an unpainted document an
        inpaint job and sent `mask: "null"` -- which admission then refused,
        correctly, with a message about inline image data that says nothing
        about the real cause.

        Catches: any classifier that tests the mask for truthiness alone.
        """

        collector = APP_JS[APP_JS.index("function _collectCanvasSource"):
                           APP_JS.index("async function doGenerate")]
        self.assertIn('maskB64 !== "null"', collector,
                      "the empty-mask sentinel is being treated as a mask")
        operation_line = [line for line in collector.splitlines()
                          if "const operation =" in line]
        self.assertTrue(operation_line)
        self.assertIn("maskPainted", operation_line[0],
                      "the operation is decided from the raw mask string")

    def test_admission_refuses_the_sentinel_if_it_ever_arrives(self) -> None:
        """Belt and braces: the server must not accept it either.

        Catches: a future collector regression reaching a server that shrugs.
        """

        with self.assertRaises(PresentationError):
            _validated_request_payload(
                body(operation="inpaint", source_image=TINY_PNG, mask="null"))

    def test_every_visible_inpaint_control_is_submitted(self) -> None:
        """The inpaint bar's four settings must reach the request.

        The server accepted `inpaint` from the moment admission landed, and the
        collector sent none of it -- so mask blur, padding, fill and area were
        four visible controls reaching nothing while inpaint quietly ran on
        server defaults. Exactly the dead-control class this program exists to
        remove, introduced by wiring half a path.

        `paramInpaintArea` is the one that would have hurt. Its default is
        `Only Masked`, which is full-resolution; the contract defaults the
        other way, so omitting it inverted the owner's own default and returned
        a WRONG picture rather than an unchanged one.

        Catches: any of the four being dropped from the submitted body.
        """

        submit = self._offset("lifecycle.submitGenerate")
        before = self.source[:submit]
        for control in ("paramMaskBlur", "paramPadding", "paramFill",
                        "paramInpaintArea"):
            with self.subTest(control=control):
                self.assertIn(control, before,
                              f"{control} is visible but never submitted")

    def test_the_lifecycle_body_carries_the_operation(self) -> None:
        """Catches: a collector that exports pixels but still posts a
        txt2img-shaped body with no declared operation."""

        submit = self._offset("lifecycle.submitGenerate")
        self.assertIn("operation", self.source[:submit],
                      "no operation is decided before the lifecycle submit")


class BlankCanvasRoutingTests(unittest.TestCase):
    """A blank canvas routes to txt2img -- but not over a painted mask.

    THE DEFECT THESE CATCH, found reading `run_generation` in full:

    The blank TEST matched the Extension exactly (every channel >= 249 here,
    `px.min() > 248` at `studio_generation.py:2660`). The GUARD around it did
    not. The Extension only auto-routes when there is no mask and no region
    data (:2657); Studio let the blank test win outright, so a near-white
    canvas with a painted mask was submitted as `txt2img` and the mask was
    dropped before admission ever saw it. The job ran, an image came back, and
    the owner's painted region meant nothing.

    Both halves were individually correct -- the blank test and the mask
    collector each did their job. Only their combination was wrong, which is
    the seam class this session has now hit four times.
    """

    def test_a_painted_mask_defeats_the_blank_canvas_route(self) -> None:
        self.assertIn("eng.isCanvasBlank() && !maskPainted", APP_JS)

    def test_the_guard_is_declared_after_the_mask_it_reads(self) -> None:
        """Not pedantry: `const` is not hoisted, so reading `maskPainted`
        above its declaration is a TemporalDeadZone ReferenceError that would
        break Generate outright. Source order is the proof."""

        mask_at = APP_JS.index("const maskPainted =")
        route_at = APP_JS.index("const isTxt2img = eng.isCanvasBlank()")
        self.assertLess(mask_at, route_at)

    def test_the_operation_still_distinguishes_all_three(self) -> None:
        self.assertIn(
            'const operation = isTxt2img ? "txt2img" '
            ': (maskPainted ? "inpaint" : "img2img");', APP_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_LIFECYCLE_PATH_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
