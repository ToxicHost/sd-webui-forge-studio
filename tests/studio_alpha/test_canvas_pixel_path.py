"""The colour-managed pixel path: server decode, opaque handles, honest colour.

The browser's own image decode bakes the DISPLAY profile into the pixels on a
calibrated setup, and once those pixels reach a canvas layer every downstream
export and img2img carries the damage. So Studio decodes server-side and hands
the browser raw RGBA it cannot reinterpret.

Two halves had to meet for that to work, and neither did:

* the client called `/studio/image_pixels` and `/studio/import_pixels`, which
  had ZERO Python references -- the routes did not exist here at all. This is
  a PORT from the extension, not the undo of a removal;
* the guard that chose the raw path matched `/file=`, the extension's
  absolute-filesystem-path shape. Standalone abolished those, so every real
  result URL is `/studio/file?path=<handle>` and the branch never fired. The
  routes alone would have changed nothing.

The contract is now the OPAQUE HANDLE. Porting `{"source": "<url with
/file=/abs/path>"}` verbatim would have looked faithful while reintroducing
browser-supplied filesystem paths, undoing the invariant `result_delivery`
exists to hold.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model is loaded and no
image is generated; images are synthesised in memory.
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

from forge_studio.pixel_import import (  # noqa: E402
    BYTES_PER_PIXEL,
    PROFILE_STATES,
    DecodedPixels,
    PixelImportError,
    decode_to_srgb_rgba,
    sample_grid,
)

#: Asserted against the discovered count so a silently dropped test fails.
#: Skips still COUNT, so the pin holds under both runners.
EXPECTED_PIXEL_TESTS = 21

#: The canonical runner has site-packages; the `-I -S -B` discovery validator
#: deliberately does NOT, so Pillow is absent there. The production decoder
#: imports PIL lazily for exactly that reason -- a module-scope import in
#: `forge_studio` would fail collection for the WHOLE suite. These tests
#: synthesise images, so they skip rather than error when it is hidden.
try:  # pragma: no cover - environment probe
    from PIL import Image as _Image  # noqa: F401

    _PILLOW = True
except Exception:  # noqa: BLE001
    _PILLOW = False

needs_pillow = unittest.skipUnless(
    _PILLOW, "Pillow is hidden under the -I -S -B validator"
)


def _png(width: int = 4, height: int = 3, colour=(200, 100, 50), **save) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG", **save)
    return buffer.getvalue()


@needs_pillow
class DecodeContractTests(unittest.TestCase):
    def test_the_byte_length_is_exactly_width_times_height_times_four(self) -> None:
        """The client asserts this before trusting the buffer, so a truncated
        response has to be a refusal rather than a corrupt canvas."""

        decoded = decode_to_srgb_rgba(_png(7, 5))
        self.assertEqual(7, decoded.width)
        self.assertEqual(5, decoded.height)
        self.assertEqual(7 * 5 * BYTES_PER_PIXEL, len(decoded.rgba))

    def test_the_pixels_survive_the_round_trip(self) -> None:
        decoded = decode_to_srgb_rgba(_png(2, 2, (10, 20, 30)))
        self.assertEqual([10, 20, 30, 255], list(decoded.rgba[:4]))

    def test_an_untagged_image_is_reported_missing_not_assumed_srgb(self) -> None:
        """An untagged image is not an sRGB image; it is one whose author did
        not say. Claiming otherwise is a guess wearing a measurement's
        clothes."""

        self.assertEqual("missing", decode_to_srgb_rgba(_png()).profile_state)

    def test_every_reported_state_is_one_the_client_knows(self) -> None:
        self.assertEqual(
            ("srgb", "converted", "missing", "unavailable"), PROFILE_STATES
        )

    def test_unreadable_bytes_are_refused_not_raised_through(self) -> None:
        with self.assertRaises(PixelImportError):
            decode_to_srgb_rgba(b"this is not an image")

    def test_empty_input_is_refused(self) -> None:
        for value in (b"", None, "not bytes"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(PixelImportError):
                    decode_to_srgb_rgba(value)  # type: ignore[arg-type]

    def test_a_declared_srgb_profile_is_recognised_not_reconverted(self) -> None:
        from PIL import ImageCms

        buffer = io.BytesIO()
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        from PIL import Image

        Image.new("RGB", (2, 2), (128, 128, 128)).save(
            buffer, format="PNG", icc_profile=profile.tobytes()
        )
        self.assertEqual("srgb", decode_to_srgb_rgba(buffer.getvalue()).profile_state)

    def test_a_transparent_image_keeps_its_alpha(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGBA", (2, 2), (10, 20, 30, 64)).save(buffer, format="PNG")
        decoded = decode_to_srgb_rgba(buffer.getvalue())
        self.assertEqual(64, decoded.rgba[3])


class DecodedPixelsInvariantTests(unittest.TestCase):
    def test_a_length_that_disagrees_with_the_dimensions_is_refused(self) -> None:
        """The invariant is enforced at construction, so no route can send a
        buffer whose length contradicts its own headers."""

        with self.assertRaises(PixelImportError):
            DecodedPixels(width=2, height=2, rgba=b"\x00" * 8, profile_state="srgb")

    def test_an_unknown_profile_state_is_refused(self) -> None:
        with self.assertRaises(PixelImportError):
            DecodedPixels(
                width=1, height=1, rgba=b"\x00" * 4, profile_state="probably-srgb"
            )


@needs_pillow
class SampleInstrumentTests(unittest.TestCase):
    """Acceptance is 'the raw path was exercised', which a picture cannot show.

    The fallback produces a plausible image too. Numbers can be compared;
    appearances cannot -- which is why this instrument exists at all.
    """

    def test_the_grid_reports_addressable_pixel_values(self) -> None:
        decoded = decode_to_srgb_rgba(_png(8, 8, (1, 2, 3)))
        samples = sample_grid(decoded, step=4)
        self.assertEqual(4, len(samples))
        self.assertEqual({"x": 0, "y": 0, "r": 1, "g": 2, "b": 3, "a": 255},
                         samples[0])

    def test_a_step_larger_than_the_image_still_samples_the_origin(self) -> None:
        decoded = decode_to_srgb_rgba(_png(4, 4))
        self.assertEqual(1, len(sample_grid(decoded, step=999)))

    def test_a_zero_or_negative_step_does_not_hang(self) -> None:
        decoded = decode_to_srgb_rgba(_png(2, 2))
        self.assertEqual(4, len(sample_grid(decoded, step=0)))


class UploadDecodingTests(unittest.TestCase):
    """`import_pixels` accepts base64, with or without a data-URL prefix."""

    @staticmethod
    def _decode(payload):
        from forge_studio.presentation import _StudioRequestHandler as StudioRequestHandler

        return StudioRequestHandler._decoded_image_upload(payload)

    @needs_pillow
    def test_a_bare_base64_payload_decodes(self) -> None:
        raw = _png()
        self.assertEqual(
            raw, self._decode({"image_b64": base64.b64encode(raw).decode()})
        )

    @needs_pillow
    def test_a_data_url_prefix_is_stripped(self) -> None:
        raw = _png()
        encoded = "data:image/png;base64," + base64.b64encode(raw).decode()
        self.assertEqual(raw, self._decode({"image_b64": encoded}))

    def test_a_missing_payload_is_refused(self) -> None:
        from forge_studio.presentation import PresentationError

        for payload in ({}, {"image_b64": ""}, {"image_b64": 7}):
            with self.subTest(payload=payload):
                with self.assertRaises(PresentationError):
                    self._decode(payload)

    def test_payload_that_is_not_base64_is_refused(self) -> None:
        from forge_studio.presentation import PresentationError

        with self.assertRaises(PresentationError):
            self._decode({"image_b64": "!!!! not base64 !!!!"})

    def test_a_data_url_with_no_comma_body_is_refused(self) -> None:
        from forge_studio.presentation import PresentationError

        with self.assertRaises(PresentationError):
            self._decode({"image_b64": "data:image/png;base64,"})


class BoundedBodyTests(unittest.TestCase):
    """An oversized body must not wedge the NEXT request.

    `_read_json` refuses without reading, and the generic drain is bounded at
    64 KiB. For a multi-MiB route those disagree: the refusal would leave
    megabytes in the socket and the following request on the same keep-alive
    connection is parsed starting inside them. Draining on demand is not the
    answer either -- that makes a refusal a way to force an unbounded read. So
    the connection closes, and the unread remainder becomes harmless.
    """

    def test_the_pixel_cap_exceeds_the_json_cap_by_a_wide_margin(self) -> None:
        from forge_studio.presentation import (
            _MAX_PIXEL_REQUEST_BYTES,
            _MAX_REQUEST_BYTES,
        )

        self.assertGreater(_MAX_PIXEL_REQUEST_BYTES, _MAX_REQUEST_BYTES * 100)

    def test_an_oversized_body_closes_the_connection_rather_than_draining(self) -> None:
        import ast

        source = (
            APP_ROOT / "forge_studio" / "presentation.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        reader = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_read_json_bounded"
        )
        body = ast.dump(reader)
        # Checked against the AST rather than the prose above it: this file
        # documents its own rule, and a raw-text search would pass on the
        # comment alone.
        self.assertIn("close_connection", body)
        self.assertIn("_body_consumed", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PIXEL_TESTS, loaded.countTestCases())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
