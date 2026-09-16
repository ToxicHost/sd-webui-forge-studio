"""The Upscale panel has a backend. AR7.2.

THE DEFECT THIS CLOSES

The whole panel shipped, and nothing answered it:

    index.html:815-838   upscaler, scale, refine, Auto Detail, steps, denoise,
                         and an UPSCALE CANVAS button. NOT hidden.
    app.js:5134          composites the layers, relabels the button
                         "Upscaling 1024x1024 -> 2048x2048...", starts an
                         indeterminate bar and VRAM polling
    app.js:5213          POST /studio/upscale_and_refine
    forge_studio/, forge_headless/    zero occurrences of that route

`parity_ledger.py:80` recorded it as `("missing", "WP6.4")` the whole time. The
tester saw a convincing progress display and then "Upscale failed: Studio route
not found" -- which reads like a Studio bug rather than a feature nobody built.

WHY THIS WAS BUILT AND THE HIRES CHECKPOINT WAS HIDDEN

Both are controls with no backend, and they get opposite treatment because the
owner named a use for one of them:

    one alpha tester is on a 6gb card and cannot do Hires, Adetailer, and Gen.
    He NEEDS the full upscale tool working

A pure ESRGAN pass needs NO resident checkpoint and does NO sampling, so it is
the one large-image path that card can afford. Hiding it would have removed the
workflow that makes Studio usable there. `AnEngineIsNotRequiredTests` is the
guard on that specific property, because it is the reason the feature exists.

WHAT THESE TESTS REFUSE TO ACCEPT

**A silent LANCZOS.** `_upscale_for_hires` falls back when an upscaler cannot
be found, which is right inside a generation -- a failed upscaler must not fail
an image the owner already waited for. Here the upscale IS the job, so a
fallback would hand back a soft image and report success. It refuses instead.

**refine and Auto Detail accepted and ignored.** They are the second half of
WP6.4 and are refused BY NAME, with their controls hidden. Accepting a field
and quietly not doing it is the defect this program spent a week removing.

Review: `Evidence/source-review/AR7.2-standalone-upscale.md`.
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

EXPECTED_UPSCALE_TESTS = 22

SHELL = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8")
APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")


def canvas(width: int = 64, height: int = 48) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def decoded(image_b64: str):
    from PIL import Image

    return Image.open(io.BytesIO(
        base64.b64decode(image_b64.split(",", 1)[1])))


def route(payload: dict) -> dict:
    """Through the adapter's OWN dispatch, not the handler directly.

    The defect was that nothing routed here. A test calling
    `upscale_and_refine()` would have passed the day before this was built.
    """

    from forge_studio.source_api_adapter import SourceFrontendAdapter

    adapter = object.__new__(SourceFrontendAdapter)
    return adapter.post("/studio/upscale_and_refine", payload)


class TheRouteAnswersTests(unittest.TestCase):
    def test_the_panel_gets_a_reply_instead_of_a_404(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0})
        self.assertTrue(reply["ok"], reply.get("error"))

    def test_the_reply_has_the_shape_the_page_reads(self) -> None:
        """`app.js:5219` branches on `data.ok` and reads `image`, `width` and
        `height`. A different shape would be a second silent failure."""

        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0})
        for key in ("ok", "image", "width", "height"):
            with self.subTest(key=key):
                self.assertIn(key, reply)

    def test_a_refusal_carries_a_sentence_the_page_can_toast(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "NotAThing",
                       "scale": 2.0})
        self.assertFalse(reply["ok"])
        self.assertTrue(str(reply["error"]).strip())


class AnEngineIsNotRequiredTests(unittest.TestCase):
    """The property the feature exists for."""

    def test_it_upscales_with_no_model_and_no_engine(self) -> None:
        """`object.__new__` gives an adapter with no presentation, no backend
        and no resident checkpoint -- which is the 6 GB card's whole state."""

        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0})
        self.assertTrue(reply["ok"])
        self.assertEqual((128, 96), decoded(reply["image"]).size)

    def test_the_passthrough_names_need_nothing_at_all(self) -> None:
        from forge_headless.image_upscale import PASSTHROUGH_UPSCALERS

        for name in PASSTHROUGH_UPSCALERS:
            with self.subTest(upscaler=name):
                reply = route({"image_b64": canvas(), "upscaler": name,
                               "scale": 2.0})
                self.assertTrue(reply["ok"], reply.get("error"))


class TheSizeIsRealTests(unittest.TestCase):
    """The result replaces the owner's canvas, so the number has to be true."""

    def test_the_reported_size_matches_the_bytes(self) -> None:
        reply = route({"image_b64": canvas(100, 80), "upscaler": "Lanczos",
                       "scale": 2.0})
        self.assertEqual((reply["width"], reply["height"]),
                         decoded(reply["image"]).size)

    def test_a_scale_of_one_is_honoured(self) -> None:
        """Not treated as "nothing to do". An owner may be changing upscaler
        rather than size."""

        reply = route({"image_b64": canvas(64, 48), "upscaler": "Lanczos",
                       "scale": 1.0})
        self.assertEqual((64, 48), decoded(reply["image"]).size)

    def test_the_target_is_aligned_to_the_vae_factor(self) -> None:
        """The result goes back on the canvas and is very often generated from
        next; an odd canvas would make every later pass carry the rounding."""

        from forge_headless.image_upscale import target_size

        for width, height, scale in ((100, 80, 1.5), (101, 83, 2.0),
                                     (64, 48, 1.05)):
            with self.subTest(size=(width, height), scale=scale):
                got = target_size(width, height, scale)
                self.assertEqual((0, 0), (got[0] % 8, got[1] % 8))

    def test_a_tiny_image_does_not_round_to_nothing(self) -> None:
        from forge_headless.image_upscale import target_size

        self.assertEqual((8, 8), target_size(4, 4, 0.5))


class NothingIsSilentlySubstitutedTests(unittest.TestCase):
    """Where this deliberately differs from the Hires helper it calls."""

    def test_an_unknown_upscaler_is_refused_not_resized(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "TotallyMadeUp",
                       "scale": 2.0})
        self.assertFalse(reply["ok"])
        self.assertIn("TotallyMadeUp", reply["error"])

    def test_the_refusal_says_it_will_not_resize_instead(self) -> None:
        """So the owner knows the soft image they did not get was a choice."""

        reply = route({"image_b64": canvas(), "upscaler": "TotallyMadeUp",
                       "scale": 2.0})
        self.assertIn("will not quietly resize", reply["error"])

    def test_refine_is_refused_by_name(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0, "run_refine": True})
        self.assertFalse(reply["ok"])
        self.assertEqual("UPSCALE_UNSUPPORTED", reply["code"])
        self.assertIn("refine", reply["error"])

    def test_auto_detail_is_refused_by_name(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0, "run_ad": True})
        self.assertFalse(reply["ok"])
        self.assertIn("Auto Detail", reply["error"])

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                       "scale": 2.0, "run_refine": True})
        self.assertIn("Upscale without it", reply["error"])

    def test_a_bad_scale_is_refused(self) -> None:
        for scale in (0, -2, "wide"):
            with self.subTest(scale=scale):
                reply = route({"image_b64": canvas(), "upscaler": "Lanczos",
                               "scale": scale})
                self.assertFalse(reply["ok"])

    def test_a_missing_image_is_refused(self) -> None:
        reply = route({"upscaler": "Lanczos", "scale": 2.0})
        self.assertFalse(reply["ok"])


class TheControlsMatchWhatStudioCanDoTests(unittest.TestCase):
    """D8 cuts both ways: an absent service is hidden, and a PRESENT one is
    not.

    This class used to assert the opposite -- that the refine row carried
    `hidden` -- and it was right to, because the route refuses `run_refine` and
    `run_ad` by name and nothing else ran them. NG-4 built the second half
    somewhere else: the upscaled frame goes onto the Canvas and the canonical
    generation runs over it. The refusal above is unchanged and still tested;
    what changed is that the owner can now ask for the thing the control names.

    Leaving the row hidden would be the mirror of the defect this program
    exists to remove -- a capability that exists and cannot be reached.
    """

    def test_the_refine_row_is_visible(self) -> None:
        at = SHELL.index('data-help="help.upscale.refine"')
        tag = SHELL[SHELL.rfind("<", 0, at):SHELL.index(">", at) + 1]
        self.assertNotIn(" hidden", tag)

    def test_auto_detail_is_not_gated_behind_refine(self) -> None:
        """Auto Detail alone -- re-render the faces, leave the frame -- is the
        cheapest of the three combinations and was the one unreachable
        combination, because its row was gated on the refine checkbox."""

        at = SHELL.index('id="upscaleADRow"')
        tag = SHELL[SHELL.rfind("<", 0, at):SHELL.index(">", at) + 1]
        self.assertNotIn(" hidden", tag)
        self.assertNotIn("data-gate=", tag)
        self.assertNotIn("display:none", tag)

    def test_the_refine_parameters_stay_gated_on_refine(self) -> None:
        """Steps and Denoise describe the img2img pass and nothing else. With
        refine off they would be two controls with no effect, which is the
        defect in the other direction."""

        at = SHELL.index('id="upscaleRefineParams"')
        tag = SHELL[SHELL.rfind("<", 0, at):SHELL.index(">", at) + 1]
        self.assertIn('data-gate="checkUpscaleRefine"', tag)

    def test_the_upscale_controls_that_work_are_still_visible(self) -> None:
        """The guard worth having: the refine half must not take the
        upscaler, the scale or the button with it."""

        for element in ("paramUpscaleModel", "paramUpscaleScale", "upscaleBtn"):
            with self.subTest(control=element):
                at = SHELL.index(f'id="{element}"')
                tag = SHELL[SHELL.rfind("<", 0, at):SHELL.index(">", at) + 1]
                self.assertNotIn(" hidden", tag)

    def test_the_ledger_no_longer_calls_the_route_missing(self) -> None:
        source = (APP_ROOT / "scripts" / "parity_ledger.py").read_text(
            encoding="utf-8")
        at = source.index('"/studio/upscale_and_refine"')
        self.assertIn("service-backed", source[at:at + 120])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_UPSCALE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
