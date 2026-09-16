"""R1 Batch E -- the capability probe, and the one thing it must not break.

`StudioModules.register` was unconditional: a module whose backend does not
exist still drew a tab full of controls that 404. It now accepts an optional
async `probe`, and the tab stays hidden until that probe resolves truthy.

The behaviour is proven in a real browser by `run_r0_browser_truth.py`
(`absent_service_tabs_are_hidden`, `canvas_lightbox_survives_gating`). These
tests are the source-contract half: they exist so the wiring cannot be
refactored away without something going red, because the browser harness is not
part of the canonical suite.

The load-bearing one is `test_gallery_exports_the_shared_lightbox_outside_the_gate`.
Gallery is the only gate in this product that can break something that works:
Canvas reaches the ephemeral lightbox through `window.StudioGallery`
(app.js `_openCanvasOutput`), and that export must stay outside the gated
registration. A mutation suppressing it was run through the browser harness and
observed to fail `canvas_lightbox_survives_gating` while
`absent_service_tabs_are_hidden` still passed -- so the two assertions are
independent, and this one is not redundant.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
EXPECTED_R1_CAPABILITY_TESTS = 11


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _strip_js_comments(source: str) -> str:
    """Comments have satisfied seven assertions in this codebase already."""

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


MODULE_SYSTEM = _strip_js_comments(_read("module-system.js"))
GALLERY = _read("gallery.js")
GALLERY_CODE = _strip_js_comments(GALLERY)
WORKSHOP = _strip_js_comments(_read("workshop.js"))
LEXICON = _strip_js_comments(_read("lexicon.js"))


class ProbeMechanismTests(unittest.TestCase):
    def test_a_probed_tab_starts_hidden(self) -> None:
        """Hidden first, revealed on success -- not the reverse.

        Revealing on failure would flash a tab the owner cannot use, and on a
        slow probe would leave it clickable for the length of the request.
        """

        block = MODULE_SYSTEM.split("typeof config.probe === \"function\"", 1)[1]
        block = block.split("console.log", 1)[0]
        self.assertIn("btn.hidden = true;", block.split(".then(", 1)[0])

    def test_a_probe_that_rejects_leaves_the_tab_hidden(self) -> None:
        block = MODULE_SYSTEM.split("typeof config.probe === \"function\"", 1)[1]
        catch = block.split(".catch(", 1)[1]
        self.assertIn("btn.hidden = true;", catch)
        self.assertIn('"absent"', catch)

    def test_registration_without_a_probe_is_unchanged(self) -> None:
        """Every module that does not opt in must behave exactly as before."""

        self.assertIn('typeof config.probe === "function"', MODULE_SYSTEM)


class AbsentServiceGateTests(unittest.TestCase):
    def test_gallery_workshop_and_wildcards_all_declare_a_probe(self) -> None:
        for name, source in (
            ("gallery.js", GALLERY_CODE),
            ("workshop.js", WORKSHOP),
            ("lexicon.js", LEXICON),
        ):
            with self.subTest(module=name):
                self.assertIn("async probe()", source)

    def test_each_probe_returns_false_rather_than_throwing(self) -> None:
        """A probe that escapes is handled, but a probe that answers is better."""

        for name, source in (
            ("gallery.js", GALLERY_CODE),
            ("workshop.js", WORKSHOP),
            ("lexicon.js", LEXICON),
        ):
            with self.subTest(module=name):
                probe = source.split("async probe()", 1)[1].split("},", 1)[0]
                self.assertIn("catch", probe)
                self.assertIn("return false", probe)


class GalleryPreservationTests(unittest.TestCase):
    def test_gallery_exports_the_shared_lightbox_outside_the_gate(self) -> None:
        """The assertion this whole batch was required not to violate.

        `window.StudioGallery` must be assigned BEFORE, and outside, the
        `StudioModules.register` call -- otherwise gating the tab would take
        Canvas's lightbox with it.
        """

        export_at = GALLERY_CODE.index("window.StudioGallery = {")
        register_at = GALLERY_CODE.index('StudioModules.register("gallery"')
        self.assertLess(export_at, register_at)
        self.assertIn(
            "window.StudioGallery = { openByHash, openEphemeral, upgradeByHash };",
            GALLERY_CODE,
        )

    def test_the_gallery_stylesheet_also_loads_outside_the_gate(self) -> None:
        """The overlay renders unstyled without it, and it loads eagerly."""

        css_at = GALLERY_CODE.index("gallery.css")
        register_at = GALLERY_CODE.index('StudioModules.register("gallery"')
        self.assertLess(css_at, register_at)

    def test_canvas_still_reaches_the_lightbox_through_that_export(self) -> None:
        """The consumer half. If Canvas stops using it, this gate's constraint
        changes -- and that should be a deliberate decision, not a silent one."""

        app_js = _strip_js_comments(_read("app.js"))
        self.assertIn("window.StudioGallery.openEphemeral(slot, ctx);", app_js)


class LiveEntryPointTests(unittest.TestCase):
    """Live is gated on an explicit declaration of ABSENCE, not of presence."""

    def test_the_adapter_declares_live_unavailable(self) -> None:
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        class _P:
            pass

        payload = SourceFrontendAdapter(_P()).get("/studio/live/status")
        self.assertIs(False, payload["available"])

    def test_the_page_hides_on_false_rather_than_requiring_true(self) -> None:
        """The direction is the whole point.

        Requiring `available === true` would hide Live against every backend
        that has never heard of the key -- including the Extension's, whose
        `get_status()` does not send it. Truthfulness here must not make a
        future real Live impossible.

        The same reason `Live.start` is not guarded with `!started.ok`: a real
        start answers `{"status": "started"}` with no `ok` field.
        """

        app_js = _strip_js_comments(_read("app.js"))
        # Anchor on the gate's own call, not on `liveToggleBtn` -- that id is
        # also looked up inside Live._updateUI, which is earlier in the file.
        gate = app_js.split("await API.liveStatus()", 1)[1]
        gate = gate.split("addEventListener", 1)[0]
        self.assertIn("status.available === false", gate)
        self.assertNotIn("available === true", gate)
        self.assertNotIn("!started.ok", app_js)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_R1_CAPABILITY_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
