"""Saved images carry their generation parameters. AR5.2.

THE DEFECT THIS CLOSES

Owner: "How difficult would it be to implement the Extension's Metadata
behavior?" -- because Studio embedded none. Two halves, one missing string.

    index.html:1641  toggleMetadata, class "on"      -- ships LIT
    app.js:3132      embed_metadata: ...             -- BELOW the `return`
    forge_studio/, forge_headless/                   -- no writer at all
    live_generation_port.py:844  processed = process_images_inner(...)
                                                     -- `.infotexts` ignored

THE SEVENTH THING BELOW THE LIFECYCLE `return`, after the progress messages,
the progress presentation (AR3.5), the inpaint payload (AR4.7), the result swap
(AR4.8), the output format (AR4.9) and the batch fields (AR5.1, removed).

It is the sharpest of the seven. AR4.9 built `_outputGroup()` and lifted
Format, Quality and Lossless out from below that `return`. Embed metadata sits
in the SAME settings card -- index.html:1620-1644 -- and was left behind.

WHY THE STRING IS TAKEN, NEVER BUILT

Neo already builds it, inside the call Studio already makes:

    processing.py:1118   text = infotext(i)
    processing.py:1184   return Processed(..., infotexts=infotexts)

Assembling a second one here would drift from the one the engine used the
moment any extension added a field, and the entire point of the format is that
OTHER programs read it. `ItIsTheEnginesOwnStringTests` is the guard.

WHAT THESE TESTS REFUSE TO ACCEPT

**A file that claims metadata it does not have.** Every assertion below reads
the bytes back out of a real encoded file with a real decoder. Asserting that
`save_kwargs` contains a key proves nothing about the file.

**A metadata failure costing the owner their image.** The picture is the work;
the text describing it is not. An unencodable infotext drops the metadata and
saves the image, which is the Extension's behaviour and the only acceptable
one. `TheImageSurvivesTests`.

**A silent one-way seam.** AR4.9's whole cost was `studio_generation.py` not
carrying `output` across. `TheSeamCarriesItBothWaysTests` guards this field in
both directions, because this is the second time through that join.

Review: `Evidence/source-review/AR5.2-metadata.md`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_METADATA_TESTS = 28

PORT_PY = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
    encoding="utf-8")
TRANSLATOR = (APP_ROOT / "forge_headless" / "studio_generation.py").read_text(
    encoding="utf-8")
CONTROLS_JS = (APP_ROOT / "forge_studio" / "frontend"
               / "studio-model-controls.js").read_text(encoding="utf-8")
APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")
SHELL = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8")

SAMPLE = ("beautiful scenery nature glass bottle landscape, purple galaxy "
          "bottle\nNegative prompt: text, watermark\n"
          "Steps: 30, Sampler: Euler a, Schedule type: Automatic, "
          "CFG scale: 7, Seed: 2453987, Size: 512x512, "
          "Model hash: 6ce0161689, Model: v1-5-pruned-emaonly")


def port():
    import importlib

    return importlib.import_module("forge_headless.live_generation_port")


def settings(**kwargs):
    from forge_studio.contracts import OutputSettings

    return OutputSettings(**kwargs)


def write(directory: Path, fmt: str, infotext: str, *, options=None,
          mode: str = "RGB"):
    """Encode one real file through the shipping writer, and return its path."""

    from PIL import Image

    module = port()
    options = settings(format=fmt) if options is None else options
    encoder, extension, _media = module.resolve_output_format(options)
    target = directory / f"result.{extension}"
    module.save_result_exclusively(
        Image.new(mode, (24, 24), (200, 120, 60)),
        target,
        encoder_format=encoder,
        save_kwargs={
            **module.output_save_kwargs(options, encoder),
            **module.metadata_save_kwargs(infotext, encoder, options),
        },
    )
    return target


def png_parameters(target: Path) -> str:
    from PIL import Image

    with Image.open(target) as opened:
        return opened.info.get("parameters", "")


def exif_user_comment(target: Path) -> str:
    """Decode UserComment back, charset prefix and all."""

    from PIL import Image

    with Image.open(target) as opened:
        raw = opened.getexif().get_ifd(0x8769).get(0x9286)
    if raw is None:
        return ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    prefix = b"ASCII" + bytes(3)
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
    return raw.decode("utf-8", errors="replace")


class ThePngCarriesItTests(unittest.TestCase):
    """PNG is the default format, so this is the path that matters most."""

    def test_the_parameters_chunk_is_there_and_correct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", SAMPLE)
            self.assertEqual(SAMPLE, png_parameters(target))

    def test_the_key_is_the_one_every_reader_looks_for(self) -> None:
        """`parameters` is an ecosystem name, not ours. A tidier key of our own
        would produce files only Studio could read -- including Studio's own
        drag-and-drop reader, which looks for this word."""

        self.assertIn('add_text("parameters", infotext)', PORT_PY)

    def test_a_multiline_infotext_survives_intact(self) -> None:
        """The real format is three lines. A writer that mangled newlines
        would lose the negative prompt, which is the half owners notice."""

        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", SAMPLE)
            self.assertEqual(3, len(png_parameters(target).splitlines()))
            self.assertIn("Negative prompt:", png_parameters(target))

    def test_unicode_survives_the_round_trip(self) -> None:
        prompt = "a cafe, naive style, 90% detail -- ok"
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", prompt)
            self.assertEqual(prompt, png_parameters(target))


class TheLossyFormatsCarryItTests(unittest.TestCase):
    def test_jpeg_carries_a_decodable_user_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "jpeg", SAMPLE)
            self.assertEqual(SAMPLE, exif_user_comment(target))

    def test_webp_carries_a_decodable_user_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "webp", SAMPLE)
            self.assertEqual(SAMPLE, exif_user_comment(target))

    def test_the_charset_prefix_is_present(self) -> None:
        """UserComment without its charset header is malformed, and readers
        show leading garbage instead of the parameters."""

        payload = port()._exif_user_comment("Steps: 30")
        self.assertIsNotNone(payload)
        self.assertIn(b"ASCII", payload)

    def test_the_quality_arguments_still_apply(self) -> None:
        """Metadata is merged into the SAME save call as the encoder settings.
        A merge that clobbered them would silently re-encode every JPEG at the
        wrong quality."""

        module = port()
        options = settings(format="jpeg", quality=40)
        merged = {**module.output_save_kwargs(options, "JPEG"),
                  **module.metadata_save_kwargs(SAMPLE, "JPEG", options)}
        self.assertEqual(40, merged["quality"])
        self.assertEqual(0, merged["subsampling"])
        self.assertIn("exif", merged)


class TheToggleIsObeyedTests(unittest.TestCase):
    """The control ships lit and did nothing. Both positions must now bite."""

    def test_off_writes_no_png_chunk(self) -> None:
        options = settings(format="png", embed_metadata=False)
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", SAMPLE, options=options)
            self.assertEqual("", png_parameters(target))

    def test_off_writes_no_exif(self) -> None:
        for fmt in ("jpeg", "webp"):
            options = settings(format=fmt, embed_metadata=False)
            with self.subTest(format=fmt), \
                    tempfile.TemporaryDirectory() as directory:
                target = write(Path(directory), fmt, SAMPLE, options=options)
                self.assertEqual("", exif_user_comment(target))

    def test_off_still_writes_the_image(self) -> None:
        options = settings(format="png", embed_metadata=False)
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", SAMPLE, options=options)
            self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))

    def test_absent_options_still_embed(self) -> None:
        """`output=None` means THE DEFAULTS, and the default is on.

        This is the one place AR5.2 knowingly changed AR4.9's rule that absent
        means byte-identical. An API caller who sends no group gets what the
        page would have sent, and the page ships the toggle lit.
        """

        self.assertEqual({}, port().metadata_save_kwargs("", "PNG", None))
        self.assertIn("pnginfo", port().metadata_save_kwargs(SAMPLE, "PNG",
                                                             None))


class TheImageSurvivesTests(unittest.TestCase):
    """The picture is the work. The text describing it is not."""

    def test_an_unencodable_infotext_costs_only_the_metadata(self) -> None:
        """A lone surrogate cannot be encoded. The Extension logs and saves
        anyway; anything else would lose a generation to a text problem."""

        broken = "a prompt with a lone surrogate: " + chr(0xD800)
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "jpeg", broken)
            self.assertTrue(target.is_file())
            self.assertTrue(target.read_bytes().startswith(b"\xff\xd8\xff"))

    def test_an_empty_infotext_is_simply_absent(self) -> None:
        """A backend that produces no string embeds nothing, rather than an
        empty chunk that a reader would report as "parameters: "."""

        self.assertEqual({}, port().metadata_save_kwargs("", "PNG"))
        with tempfile.TemporaryDirectory() as directory:
            target = write(Path(directory), "png", "")
            self.assertEqual("", png_parameters(target))


class ItIsTheEnginesOwnStringTests(unittest.TestCase):
    """Taken from `Processed`, never assembled here."""

    def test_the_port_reads_infotexts_off_the_processed_object(self) -> None:
        self.assertIn('getattr(processed, "infotexts", [])', PORT_PY)

    def test_nothing_here_reassembles_one(self) -> None:
        """A second builder would drift from the engine's the first time an
        extension added a field, and the format's whole purpose is that other
        programs read it."""

        self.assertNotIn("Negative prompt:", PORT_PY)
        self.assertNotIn("Schedule type:", PORT_PY)

    def test_the_outcome_carries_it(self) -> None:
        from forge_headless.generation_port import GenerationOutcome

        outcome = GenerationOutcome(
            job_id="j", request_id="r", result_relative_location="x.png",
            media_type="image/png", width=8, height=8, seed=1,
            infotext=SAMPLE)
        self.assertEqual(SAMPLE, outcome.to_dict()["infotext"])

    def test_an_outcome_without_one_is_legal(self) -> None:
        from forge_headless.generation_port import GenerationOutcome

        self.assertEqual("", GenerationOutcome(
            job_id="j", request_id="r", result_relative_location="x.png",
            media_type="image/png", width=8, height=8, seed=1).infotext)


class TheSeamCarriesItBothWaysTests(unittest.TestCase):
    """The join AR4.9 was lost in. Guarded in both directions this time."""

    def test_the_toggle_travels_inbound(self) -> None:
        self.assertIn("embed_metadata=bool(", TRANSLATOR)

    def test_the_string_travels_outbound(self) -> None:
        self.assertIn('"infotext": str(getattr(outcome, "infotext", "")',
                      TRANSLATOR)

    def test_the_headless_request_can_hold_the_flag(self) -> None:
        from forge_headless.generation_request import (
            FirstImageRequest,
            OutputOptions,
        )

        request = FirstImageRequest(
            request_id="x", model_id="m",
            output=OutputOptions(format="png", embed_metadata=False))
        self.assertFalse(request.output.embed_metadata)


class ThePageReadsItBackTests(unittest.TestCase):
    """Four owner surfaces read one string that was hard-coded empty."""

    def test_delivery_no_longer_hard_codes_an_empty_infotext(self) -> None:
        self.assertNotIn('infotext: "",', CONTROLS_JS)
        self.assertIn("result.metadata && result.metadata.infotext",
                      CONTROLS_JS)

    def test_the_resolved_seed_is_taken_from_the_metadata(self) -> None:
        """Not parsed back out of the infotext. Both would work today; a regex
        over prose is the one that breaks."""

        self.assertIn("result.metadata && result.metadata.seed", CONTROLS_JS)
        self.assertIn("S.lastSeed = resolvedSeed", CONTROLS_JS)

    def test_BOTH_recycle_buttons_read_the_delivered_seed(self) -> None:
        """The correction to this suite's own first version.

        AR5.2 claimed both Recycle buttons were fixed. Only `#seedRecycle`
        was: `#varSeedRecycle` read `State.lastResult` alone, which is assigned
        at app.js:3172 -- BELOW the lifecycle `return` -- so it is never set on
        a real install and the button stayed dead.

        Asserted on BOTH handlers rather than on the shared symbol, because the
        original defect was precisely that the two looked equivalent and were
        not.
        """

        for control in ("seedRecycle", "varSeedRecycle"):
            with self.subTest(control=control):
                at = APP_JS.index(f'getElementById("{control}")')
                body = APP_JS[at:APP_JS.index("});", at)]
                self.assertIn("State.lastSeed", body,
                              f"#{control} does not read the delivered seed")

    def test_the_toggle_is_read_from_the_dom_at_submit_time(self) -> None:
        at = APP_JS.index("const _outputGroup = () =>")
        body = APP_JS[at:APP_JS.index("const _hiresGroup", at)]
        self.assertIn('getElementById("toggleMetadata")', body)

    def test_the_group_is_sent_inside_the_lifecycle_branch(self) -> None:
        """Asserted by POSITION, the way AR4.7 and AR4.9 assert it. The whole
        defect was that this value sat below the `return`."""

        branch = APP_JS.index("if (lifecycle && lifecycle.lifecycleAvailable())")
        sent = APP_JS.index("{ output: _outputGroup() }")
        submit = APP_JS.index("await lifecycle.submitGenerate(jobParams)")
        self.assertLess(branch, sent)
        self.assertLess(sent, submit)

    def test_the_toggle_still_ships_lit(self) -> None:
        """It always did, while doing nothing. Now that it does something,
        turning it dark by accident would silently stop embedding."""

        at = SHELL.index('data-setting-key="toggleMetadata"')
        self.assertIn('id="toggleMetadata"',
                      SHELL[at:SHELL.index("</div>", at + 400)])
        self.assertIn('class="toggle-track on" id="toggleMetadata"', SHELL)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_METADATA_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
