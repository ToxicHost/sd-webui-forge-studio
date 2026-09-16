"""The output format setting reaches the file. AR4.9.

THE DEFECT THIS CLOSES

Owner: "File type setting in Settings doesn't work. It saves as PNG no matter
what." Correct, at every layer:

    index.html:1622   <select id="settingSaveFormat">  png | jpeg | webp
    app.js:3095       save_format: ...   BELOW the lifecycle `return`
    presentation.py                      no field at all
    contracts.py                         no group at all
    live_generation_port.py:1270  media_type="image/png"       HARDCODED
    live_generation_port.py:50    return f"{cleaned}.png"      HARDCODED
    live_generation_port.py:379   image.save(..., format="PNG")  HARDCODED

This is the FIFTH thing found below that lifecycle `return` -- after the
progress messages, the progress presentation (AR3.5), the inpaint payload
(AR4.7) and the result swap (AR4.8). It is the first where fixing the `return`
alone would not have been enough.

AND ONE MORE SEAM, FOUND BY RUNNING IT

With the contract, admission, the writer and the page all correct, JPEG STILL
produced a PNG. `studio_generation.py` builds the headless `FirstImageRequest`
and simply did not carry the group across -- both endpoints right, the join
silent. `TranslationCarriesItTests` is the guard on that specific hop, because
it is the one that was missed.

WHAT THESE TESTS REFUSE TO ACCEPT

**A `.jpg` holding PNG bytes.** The extension, the media type and the encoder
name all come from ONE row of `OUTPUT_FORMATS`, so disagreement is not
expressible. `ExtensionAndBytesAgreeTests` proves it on real encoded files.

**A silent fallback.** An unknown format is REFUSED. Coercing it to PNG would
recreate, one layer down, the exact defect being fixed.

**Absent drifting.** No group must write the same bytes it always did.

Live proof: `Evidence/source-review/AR4.9-output-format.md`.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_FORMAT_TESTS = 22

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")
TRANSLATOR = (APP_ROOT / "forge_headless" / "studio_generation.py").read_text(
    encoding="utf-8")

MAGIC = {"PNG": b"\x89PNG", "JPEG": b"\xff\xd8\xff", "WEBP": b"RIFF"}


def port():
    import importlib

    return importlib.import_module("forge_headless.live_generation_port")


def settings(**kwargs):
    from forge_studio.contracts import OutputSettings

    return OutputSettings(**kwargs)


def _output_group_body() -> str:
    """`_outputGroup`'s body, sliced to where it actually ends.

    A fixed character window was used here and broke the moment the function
    gained an explanatory comment -- failing a test that had nothing to do with
    the change. The next declaration is what really ends it.
    """

    at = APP_JS.index("const _outputGroup = () =>")
    return APP_JS[at:APP_JS.index("const _hiresGroup", at)]


class TableIsTheSingleSourceTests(unittest.TestCase):
    """One row decides the encoder, the extension and the media type."""

    def test_every_supported_format_has_a_complete_row(self) -> None:
        for name, row in port().OUTPUT_FORMATS.items():
            with self.subTest(format=name):
                self.assertEqual(3, len(row))
                self.assertTrue(all(row))

    def test_the_delivery_layer_accepts_every_media_type_we_emit(self) -> None:
        """A format the writer produces but the registry cannot hand back is
        a result the owner can never see."""

        from forge_studio.result_delivery import SUPPORTED_MEDIA_TYPES

        for name, (_, extension, media_type) in port().OUTPUT_FORMATS.items():
            with self.subTest(format=name):
                self.assertIn(media_type, SUPPORTED_MEDIA_TYPES)
                self.assertEqual(extension,
                                 SUPPORTED_MEDIA_TYPES[media_type])

    def test_absent_resolves_to_the_png_path(self) -> None:
        """Absent means absent: the file must be what it always was."""

        self.assertEqual(("PNG", "png", "image/png"),
                         port().resolve_output_format(None))

    def test_an_unknown_format_is_refused_not_coerced(self) -> None:
        with self.assertRaises(Exception):
            port().resolve_output_format(settings(format="tiff"))

    def test_the_name_takes_its_extension_from_the_table(self) -> None:
        for name, (_, extension, _m) in port().OUTPUT_FORMATS.items():
            with self.subTest(format=name):
                self.assertTrue(
                    port().safe_result_name("job", index=0,
                                            extension=extension)
                    .endswith("." + extension))


class EncoderArgumentsTests(unittest.TestCase):
    def test_jpeg_is_saved_at_full_chroma(self) -> None:
        """Studio's Settings copy promises "no color smearing on fine edges or
        text". Default subsampling would make that false."""

        kwargs = port().output_save_kwargs(settings(format="jpeg"), "JPEG")
        self.assertEqual(0, kwargs.get("subsampling"))

    def test_webp_is_lossless_or_quality_never_both(self) -> None:
        lossless = port().output_save_kwargs(
            settings(format="webp", lossless=True, quality=70), "WEBP")
        self.assertTrue(lossless.get("lossless"))
        self.assertNotIn("quality", lossless)
        lossy = port().output_save_kwargs(
            settings(format="webp", quality=70), "WEBP")
        self.assertEqual(70, lossy.get("quality"))
        self.assertNotIn("lossless", lossy)

    def test_png_takes_no_quality_arguments(self) -> None:
        self.assertEqual({}, port().output_save_kwargs(
            settings(format="png", quality=50), "PNG"))

    def test_quality_is_clamped_rather_than_trusted(self) -> None:
        for asked, expect in ((0, 1), (1000, 100)):
            with self.subTest(asked=asked):
                got = port().output_save_kwargs(
                    settings(format="jpeg", quality=asked), "JPEG")
                self.assertEqual(expect, got["quality"])


class ExtensionAndBytesAgreeTests(unittest.TestCase):
    """Encoded for real. A `.jpg` holding PNG is the likeliest failure."""

    def _write(self, tmp: Path, fmt: str, mode: str = "RGB"):
        from PIL import Image

        options = settings(format=fmt)
        encoder, extension, media_type = port().resolve_output_format(options)
        target = tmp / f"result.{extension}"
        port().save_result_exclusively(
            Image.new(mode, (16, 16), (200, 120, 60) if mode == "RGB"
                      else (200, 120, 60, 255)),
            target,
            encoder_format=encoder,
            save_kwargs=port().output_save_kwargs(options, encoder))
        return target, media_type, encoder

    def test_each_format_writes_its_own_magic_bytes(self) -> None:
        import tempfile

        for fmt, expect in (("png", "PNG"), ("jpeg", "JPEG"), ("webp", "WEBP")):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory() as d:
                target, _media, _enc = self._write(Path(d), fmt)
                self.assertTrue(target.read_bytes().startswith(MAGIC[expect]),
                                f"{target.name} does not contain {expect}")

    def test_the_media_type_matches_the_bytes(self) -> None:
        import tempfile

        for fmt, expect in (("png", "image/png"), ("jpeg", "image/jpeg"),
                            ("webp", "image/webp")):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory() as d:
                _target, media, _enc = self._write(Path(d), fmt)
                self.assertEqual(expect, media)

    def test_an_rgba_image_can_be_saved_as_jpeg(self) -> None:
        """JPEG has no alpha and a Canvas result legitimately can. Without the
        flatten this raises, which would fail the owner's job outright."""

        import tempfile

        with tempfile.TemporaryDirectory() as d:
            target, _m, _e = self._write(Path(d), "jpeg", mode="RGBA")
            self.assertTrue(target.read_bytes().startswith(MAGIC["JPEG"]))

    def test_the_srgb_profile_survives_every_format(self) -> None:
        """The writer's own comment explains why results are tagged. That must
        not have become PNG-only."""

        import tempfile

        from PIL import Image

        if not port()._srgb_icc_bytes():
            self.skipTest("this host has no ImageCms; tagging is optional")
        for fmt in ("png", "jpeg", "webp"):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory() as d:
                target, _m, _e = self._write(Path(d), fmt)
                with Image.open(target) as opened:
                    self.assertTrue(opened.info.get("icc_profile"),
                                    f"{fmt} lost its sRGB tag")


class TranslationCarriesItTests(unittest.TestCase):
    """The hop that was missed, and cost a whole GPU run to find."""

    def test_the_headless_request_can_hold_the_group(self) -> None:
        from forge_headless.generation_request import (
            FirstImageRequest,
            OutputOptions,
        )

        request = FirstImageRequest(request_id="x", model_id="m",
                                    output=OutputOptions(format="jpeg"))
        self.assertEqual("jpeg", request.output.format)

    def test_absent_stays_absent_across_the_boundary(self) -> None:
        from forge_headless.generation_request import FirstImageRequest

        self.assertIsNone(FirstImageRequest(request_id="x",
                                            model_id="m").output)

    def test_the_translator_actually_passes_it(self) -> None:
        """Every layer was correct and JPEG still wrote PNG, because this one
        line was missing."""

        self.assertIn("output=_output,", TRANSLATOR)
        self.assertIn('getattr(request, "output", None)', TRANSLATOR)


class ThePageSendsItOnTheRealPathTests(unittest.TestCase):
    def test_the_group_is_sent_inside_the_lifecycle_branch(self) -> None:
        """The whole point. `save_format` has sat below the `return` since
        Studio shipped."""

        branch = APP_JS.index("if (lifecycle && lifecycle.lifecycleAvailable())")
        sent = APP_JS.index("{ output: _outputGroup() }")
        submit = APP_JS.index("await lifecycle.submitGenerate(jobParams)")
        self.assertLess(branch, sent)
        self.assertLess(sent, submit)

    def test_a_pure_default_sends_no_group_at_all(self) -> None:
        """No group when EVERY field is at its default.

        This used to say "byte-identical to before this existed", and AR5.2
        made that false on purpose: metadata is now embedded by default, so a
        default PNG gains a `parameters` chunk it did not carry before. The
        PAYLOAD claim still holds and is what is asserted -- nothing is sent
        when nothing differs -- but the bytes deliberately changed, and a test
        still claiming otherwise would be the misleading half of a true
        statement.
        """

        self.assertIn(
            'if (format !== "jpeg" && format !== "webp" && embedMetadata)'
            " return null;",
            _output_group_body())

    def test_metadata_off_makes_the_group_travel(self) -> None:
        """The other half, and the one that would silently regress.

        If PNG kept returning null unconditionally, an owner who turned the
        toggle OFF would have that choice dropped in the browser and get
        metadata anyway -- the same class of defect as the format itself.
        """

        self.assertIn("if (!embedMetadata) group.embed_metadata = false;",
                      _output_group_body())

    def test_true_is_not_sent(self) -> None:
        """True is the server default. Sending it on every request would add a
        field that says nothing, and would make a default install's payload
        differ from the empty one this suite pins."""

        self.assertNotIn("group.embed_metadata = true", _output_group_body())

    def test_quality_is_read_per_format(self) -> None:
        """The panel keeps JPEG and WebP quality separately; sending the wrong
        one re-encodes at a number the owner never chose."""

        self.assertIn("getOutputQualityForFormat(format)",
                      _output_group_body())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_FORMAT_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
