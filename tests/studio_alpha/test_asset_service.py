"""Bounded asset admission behind opaque handles. WP1.2.

WHY A REGISTRY AT ALL, GIVEN THE CONTRACT ARGUED AGAINST ONE

`contracts.InputAsset` says a registry "would add a lifetime, an eviction policy
and a second thing to leak", and that was correct while a generate call consumed
its payload. That is the Studio EXTENSION's model -- `studio_api.py:2882` is one
`async def` returning the images inline, so nothing survives the call.

Studio queues. `/api/generate` returns 202 and the job runs later, so the
premise no longer holds. The objection is answered rather than ignored, and
these tests are where the answer is checked: the lifetime is explicit, the
eviction is bounded, and a released transient asset is really gone.

Reviewed and recorded in `Evidence/source-review/WP1.4-inpaint.md`. The
Extension has no asset endpoint at all -- an absent oracle, which is a finding,
not permission to invent freely.
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

from forge_studio.asset_service import (  # noqa: E402
    HANDLE_PREFIX,
    AdmittedAsset,
    AssetService,
    Retention,
)
from forge_studio.contracts import StudioError  # noqa: E402

EXPECTED_ASSET_SERVICE_TESTS = 22


def png(colour=(1, 2, 3), size=(8, 8)) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def code(caught) -> str:
    return caught.exception.error.code


class AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AssetService()

    def test_admission_returns_an_opaque_handle(self) -> None:
        asset = self.service.admit(png(), role="source")
        self.assertTrue(asset.handle.startswith(HANDLE_PREFIX))
        self.assertEqual(32, len(asset.handle[len(HANDLE_PREFIX):]))

    def test_the_handle_carries_no_path_and_no_payload(self) -> None:
        """The boundary rule, asserted on the projection the browser sees."""

        projection = self.service.admit(png(), role="source").to_dict()
        self.assertNotIn("data_url", projection)
        # "no slash" was the first version of this and it is wrong: a media
        # type legitimately contains one, and so does the handle prefix. What
        # must not appear is a FILESYSTEM path -- a drive letter, a backslash,
        # or a leading separator.
        for name, value in projection.items():
            with self.subTest(field=name):
                text = str(value)
                self.assertNotIn("\\", text)
                self.assertFalse(text.startswith("/"))
                self.assertNotRegex(text, r"^[A-Za-z]:")
                self.assertNotIn(str(APP_ROOT), text)

    def test_the_server_establishes_the_facts(self) -> None:
        """Byte length and hash are computed, not accepted from the caller."""

        asset = self.service.admit(png(), role="source")
        self.assertEqual(64, len(asset.content_hash))
        self.assertGreater(asset.byte_length, 0)
        self.assertEqual("image/png", asset.media_type)

    def test_identical_bytes_hash_identically(self) -> None:
        first = self.service.admit(png(), role="source")
        second = self.service.admit(png(), role="source")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertNotEqual(first.handle, second.handle,
                            "each admission is its own asset")

    def test_different_bytes_do_not_collide(self) -> None:
        first = self.service.admit(png((1, 2, 3)), role="source")
        second = self.service.admit(png((9, 9, 9)), role="source")
        self.assertNotEqual(first.content_hash, second.content_hash)


class RefusalTests(unittest.TestCase):
    """Every refusal named, per WP1.2. None of these may be a 500."""

    def setUp(self) -> None:
        self.service = AssetService()

    def test_a_server_path_is_refused(self) -> None:
        """Catches: a caller naming a file on the server. No downstream check
        recovers from having accepted the name."""

        for value in ("/etc/passwd", "C:\\Windows\\win.ini",
                      "file:///etc/passwd", "http://example.com/x.png"):
            with self.subTest(value=value):
                with self.assertRaises(StudioError) as caught:
                    self.service.admit(value, role="source")
                self.assertEqual("ASSET_MALFORMED", code(caught))

    def test_malformed_base64_is_refused(self) -> None:
        with self.assertRaises(StudioError) as caught:
            self.service.admit("data:image/png;base64,!!!!", role="source")
        self.assertEqual("ASSET_MALFORMED", code(caught))

    def test_an_unsupported_format_is_refused_by_name(self) -> None:
        with self.assertRaises(StudioError) as caught:
            self.service.admit("data:image/gif;base64,AAAA", role="source")
        self.assertEqual("ASSET_MEDIA_TYPE_UNSUPPORTED", code(caught))

    def test_an_oversized_payload_is_refused_before_decoding(self) -> None:
        """The ceiling is checked against the ENCODED string.

        Catches: a decoded ceiling, which can only be applied after spending
        the memory it exists to protect.
        """

        # The CEILING is lowered rather than the payload raised. It went to
        # 256 MB at AR6.10, and allocating a 257 MB string to prove a
        # comparison would make this suite cost a quarter of a gigabyte.
        #
        # The property under test is unchanged and is the important one: the
        # check happens against the ENCODED string, so it can refuse without
        # first spending the memory it exists to protect.
        import forge_studio.asset_service as service_module

        original = service_module.MAX_ASSET_BYTES
        service_module.MAX_ASSET_BYTES = 1024
        try:
            huge = "data:image/png;base64," + ("A" * 2048)
            with self.assertRaises(StudioError) as caught:
                self.service.admit(huge, role="source")
        finally:
            service_module.MAX_ASSET_BYTES = original
        self.assertEqual("ASSET_TOO_LARGE", code(caught))

    def test_the_ceiling_clears_a_real_photograph(self) -> None:
        """Why it moved. 32 MB was refusing the very images the pixel-cap
        removal (AR6.3) existed to allow -- an 8192x8192 PNG is around 100 MB
        before base64, which adds a third again."""

        from forge_studio.asset_service import MAX_ASSET_BYTES

        self.assertGreaterEqual(MAX_ASSET_BYTES, 200 * 1024 * 1024)

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(StudioError) as caught:
            self.service.admit(png(), role="controlnet")
        self.assertEqual("ASSET_ROLE_UNKNOWN", code(caught))

    def test_an_empty_payload_is_refused(self) -> None:
        for value in ("", "data:image/png;base64,"):
            with self.subTest(value=value):
                with self.assertRaises(StudioError) as caught:
                    self.service.admit(value, role="source")
                self.assertEqual("ASSET_MALFORMED", code(caught))


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AssetService()
        self.asset = self.service.admit(png(), role="source")

    def test_a_role_mismatch_is_refused(self) -> None:
        """Catches: a mask satisfying a request for a source. The two are
        verified against each other downstream, so a swap would surface as a
        geometry failure rather than the caller error it is."""

        with self.assertRaises(StudioError) as caught:
            self.service.describe(self.asset.handle, role="mask")
        self.assertEqual("ASSET_ROLE_MISMATCH", code(caught))

    def test_a_malformed_handle_reads_as_unknown(self) -> None:
        """Identical treatment, so probing learns nothing about what exists."""

        for handle in ("nonsense", "studio-asset/zz", "", None, 7):
            with self.subTest(handle=handle):
                with self.assertRaises(StudioError) as caught:
                    self.service.describe(handle)
                self.assertEqual("ASSET_NOT_FOUND", code(caught))

    def test_the_payload_is_reachable_only_by_handle(self) -> None:
        self.assertGreater(len(self.service.payload(self.asset.handle)), 0)


class LifetimeTests(unittest.TestCase):
    """The answer to `InputAsset`'s objection: the lifetime is explicit."""

    def test_a_released_transient_asset_is_gone(self) -> None:
        service = AssetService()
        asset = service.admit(png(), role="source")
        service.retain(asset.handle)
        service.release(asset.handle)
        with self.assertRaises(StudioError) as caught:
            service.describe(asset.handle)
        self.assertEqual("ASSET_EXPIRED", code(caught),
                         "a released asset must read as expired, not missing")

    def test_a_second_reference_keeps_it_alive(self) -> None:
        """Catches: one job's cleanup evicting an asset another still holds."""

        service = AssetService()
        asset = service.admit(png(), role="source")
        service.retain(asset.handle)
        service.retain(asset.handle)
        service.release(asset.handle)
        self.assertEqual(asset.handle,
                         service.describe(asset.handle).handle)

    def test_releasing_an_unknown_handle_is_silent(self) -> None:
        """Release runs on cleanup paths reached because something already
        failed. A cleanup that raises is worse than one that finds nothing."""

        AssetService().release("studio-asset/" + "0" * 32)

    def test_the_registry_is_bounded_and_evicts_the_oldest(self) -> None:
        """Catches: the unbounded registry `InputAsset` warned about."""

        service = AssetService(max_assets=3)
        handles = [service.admit(png((n, n, n)), role="source").handle
                   for n in range(5)]
        self.assertEqual(3, service.count())
        with self.assertRaises(StudioError) as caught:
            service.describe(handles[0])
        self.assertEqual("ASSET_EXPIRED", code(caught))
        self.assertEqual(handles[-1], service.describe(handles[-1]).handle)


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        from forge_studio.contracts import GenerationRequest
        from forge_studio.presentation import StudioPresentation

        self.presentation = StudioPresentation(object(), GenerationRequest)

    def test_the_route_answers_with_facts_and_no_payload(self) -> None:
        answer = self.presentation.admit_asset(
            {"data_url": png(), "role": "mask"})
        self.assertEqual("mask", answer["role"])
        self.assertNotIn("data_url", answer)
        self.assertEqual(Retention.TRANSIENT.value, answer["retention"])

    def test_an_unknown_retention_class_is_refused(self) -> None:
        from forge_studio.presentation import PresentationError

        with self.assertRaises(PresentationError):
            self.presentation.admit_asset(
                {"data_url": png(), "retention": "forever"})


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_ASSET_SERVICE_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
