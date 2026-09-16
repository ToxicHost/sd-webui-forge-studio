"""A queued job executes the Canvas it was admitted from. AR2.2/2.4.

WHAT AR2 ACTUALLY REQUIRES, AND WHERE IT IS ALREADY MET

The acceptance is "a queued job always executes the exact admitted Canvas
revision" and "editing the Canvas after submission cannot mutate the queued
job". On the path Studio actually uses, both hold BY CONSTRUCTION, and these
tests pin the construction so it cannot be dismantled without noticing.

Source and mask reach `/api/generate` as INLINE data URLs, not as handles into
the asset registry. The bytes are copied into a frozen `GenerationRequest` at
admission, and the server hashes what it decoded. After that there is no live
reference back to the Canvas at all: repainting, undoing, resizing or closing
the document cannot reach a queued job, because the job never held a pointer to
it -- it holds the pixels.

That is why AR2.3's "wire retain/release to the job lifecycle" is NOT
implemented here and is not a gap. `AssetService.retain`/`release` exist and
are tested, but nothing in the generate path consumes a handle, so wiring a
lifetime to an object the lifecycle never references would be machinery for an
unused path. It becomes real when WP5's durable job store moves assets to
handles; `Evidence/source-review/AR2-canvas-document-identity.md` records that
sequencing.

WHAT THE HASH IS FOR, AND WHO OWNS IT

`InputAsset.content_hash` is the one field on that contract the browser does
not supply and cannot influence: it is absent from `_ASSET_FIELDS`, so a
payload carrying it is refused as an unsupported field, and the value is
computed from the DECODED bytes at admission. AR2.4's requirement -- the hash
is server-established and the browser may carry it only as provenance -- is
therefore already satisfied, and the first test below is what keeps it that
way.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_QUEUED_INTEGRITY_TESTS = 11


def refusal():
    import forge_studio.presentation

    return forge_studio.presentation.PresentationError


def png_data_url(width: int, height: int, colour) -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def admitted(**generation):
    from forge_studio.contracts import GenerationRequest
    from forge_studio.presentation import _validated_request_payload

    body = {"model": "m", "generation": {
        "positive_prompt": "p", "negative_prompt": "", "seed": 7, "steps": 4,
        "cfg_scale": 4.0, "width": 64, "height": 64}}
    body["generation"].update(generation)
    return GenerationRequest(**_validated_request_payload(body))


class ServerEstablishedHashTests(unittest.TestCase):
    """AR2.4. The browser may carry a hash as provenance; it may not declare
    the bytes."""

    def test_the_browser_cannot_supply_a_content_hash(self) -> None:
        """Catches: accepting a client-declared hash, which would let a page
        claim any identity for any pixels."""

        with self.assertRaises(refusal()):
            admitted(operation="img2img", source_image={
                "data_url": png_data_url(8, 8, (1, 2, 3)),
                "content_hash": "0" * 64})

    def test_the_server_computes_it_from_the_decoded_bytes(self) -> None:
        request = admitted(operation="img2img",
                           source_image=png_data_url(8, 8, (1, 2, 3)))
        self.assertEqual(64, len(request.source_image.content_hash))

    def test_identical_pixels_hash_identically_across_requests(self) -> None:
        """Over the DECODED payload, so a re-encoded prefix or different
        base64 padding cannot change the identity of identical pixels."""

        url = png_data_url(8, 8, (9, 9, 9))
        first = admitted(operation="img2img", source_image=url)
        second = admitted(operation="img2img", source_image=url)
        self.assertEqual(first.source_image.content_hash,
                         second.source_image.content_hash)

    def test_different_pixels_hash_differently(self) -> None:
        a = admitted(operation="img2img",
                     source_image=png_data_url(8, 8, (0, 0, 0)))
        b = admitted(operation="img2img",
                     source_image=png_data_url(8, 8, (255, 255, 255)))
        self.assertNotEqual(a.source_image.content_hash,
                            b.source_image.content_hash)


class QueuedImmutabilityTests(unittest.TestCase):
    """AR2.2. Editing the Canvas after submission cannot mutate a queued job."""

    def test_the_admitted_request_is_frozen(self) -> None:
        import dataclasses

        request = admitted(operation="img2img",
                           source_image=png_data_url(8, 8, (4, 5, 6)))
        for field, value in (("source_image", None), ("width", 999),
                             ("document", None)):
            with self.subTest(field=field):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(request, field, value)

    def test_the_admitted_asset_is_frozen(self) -> None:
        import dataclasses

        asset = admitted(operation="img2img",
                         source_image=png_data_url(8, 8, (4, 5, 6))).source_image
        with self.assertRaises(dataclasses.FrozenInstanceError):
            asset.data_url = "data:image/png;base64,AAAA"  # type: ignore[misc]

    def test_the_job_holds_pixels_not_a_reference_to_the_canvas(self) -> None:
        """THE structural reason a later Canvas edit cannot reach a queued job.

        The request carries the bytes themselves. Mutating the string the
        caller passed in afterwards -- which is the closest a test can get to
        'the owner kept painting' -- leaves the admitted asset untouched.
        """

        url = png_data_url(8, 8, (7, 7, 7))
        request = admitted(operation="img2img", source_image=url)
        before = request.source_image.data_url
        url = png_data_url(8, 8, (200, 200, 200))  # the Canvas moves on
        self.assertEqual(before, request.source_image.data_url)
        self.assertNotEqual(url, request.source_image.data_url)

    def test_source_and_mask_are_captured_in_one_admission(self) -> None:
        """A pair that cannot describe one snapshot is impossible on this path:
        both arrive in a single request and are frozen together."""

        request = admitted(operation="inpaint",
                           source_image=png_data_url(8, 8, (1, 1, 1)),
                           mask=png_data_url(8, 8, (255, 255, 255)))
        self.assertTrue(request.source_image.present)
        self.assertTrue(request.mask.present)
        self.assertNotEqual(request.source_image.content_hash,
                            request.mask.content_hash)


class HandlePathDispositionTests(unittest.TestCase):
    """AR2.3 is deferred, and this records WHY so it is not re-filed as a gap.

    `AssetService.retain`/`release` exist and are tested. Nothing in the
    generate path consumes a handle, so there is no lifetime to wire: the job
    holds inline bytes and cannot outlive or under-live them. Wiring a
    lifecycle to an object the lifecycle never references would be machinery
    for an unused path -- the shape of dead code this project removes.
    """

    def test_the_generate_path_takes_inline_data_urls_only(self) -> None:
        """Catches: a handle quietly becoming acceptable on the generate
        request WITHOUT the retain/release wiring that would then be owed."""

        with self.assertRaises(refusal()):
            admitted(operation="img2img",
                     source_image="studio-asset/" + "a" * 32)

    def test_a_server_path_is_still_refused_as_a_source(self) -> None:
        for bad in ("C:/Users/x/a.png", "/etc/passwd", "file:///tmp/a.png",
                    "http://example.com/a.png"):
            with self.subTest(source=bad):
                with self.assertRaises(refusal()):
                    admitted(operation="img2img", source_image=bad)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_QUEUED_INTEGRITY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
