"""Canvas Save/Export: Studio can open the PSD it writes.

Export was already real. Import did not exist — `readPsd` sits in the vendored
`ag-psd` and NOTHING in the frontend called it, so Studio could write a layered
project file and not open one, including its own export.

The round trip is proven in a live page and recorded in
`Evidence/source-review/AC5-psd-round-trip.md`: a four-layer document wiped to
a blank 64×64 first (so a no-op import cannot look like a success), then
reopened with every layer, name, opacity, blend mode, visibility and pixel
intact, as ONE undo entry.

What lives here is the wiring, and one guard that is the point of the file:
`readPsd` must have a caller.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

EXPECTED_PSD_TESTS = 14


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


UI = _strip_js_comments((FRONTEND / "canvas-ui.js").read_text(encoding="utf-8"))
APP = _strip_js_comments((FRONTEND / "app.js").read_text(encoding="utf-8"))
CORE = _strip_js_comments((FRONTEND / "canvas-core.js").read_text(encoding="utf-8"))
HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")


def _open_psd() -> str:
    start = UI.index("async function openPSD(file) {")
    depth = 0
    for index in range(UI.index("{", start), len(UI)):
        if UI[index] == "{":
            depth += 1
        elif UI[index] == "}":
            depth -= 1
            if depth == 0:
                return UI[start:index + 1]
    raise AssertionError("openPSD never closes")


class TheReaderHasACallerTests(unittest.TestCase):
    def test_read_psd_is_actually_called(self):
        """The whole finding, as one assertion. `readPsd` was in the bundle
        and nothing invoked it."""

        self.assertIn("agPsd.readPsd(", UI)

    def test_the_writer_still_has_one_too(self):
        self.assertIn("agPsd.writePsd(", UI)

    def test_the_importer_is_exposed(self):
        self.assertRegex(UI, r"openPSD: openPSD,")


class TheDocumentIsReplacedSafelyTests(unittest.TestCase):
    def test_opening_a_psd_is_one_undo(self):
        """It replaces the size, every layer and the active index. A
        replacement the owner cannot take back is worse than one they never
        made."""

        body = _open_psd()
        self.assertIn('C.saveStructuralUndo("Open PSD");', body)

    def test_the_undo_is_taken_before_anything_is_destroyed(self):
        body = _open_psd()
        self.assertLess(
            body.index("saveStructuralUndo"), body.index("S.layers.length = 0"))

    def test_a_file_that_will_not_parse_says_so(self):
        """Named, not swallowed: a canvas that quietly does not change is the
        defect this program exists to remove."""

        body = _open_psd()
        self.assertIn("catch (error)", body)
        self.assertIn("could not be read", body)

    def test_an_unparseable_file_changes_nothing(self):
        """The refusal must come BEFORE the structural undo, or a bad file
        costs the owner an undo entry and their document."""

        body = _open_psd()
        self.assertLess(body.index("could not be read"),
                        body.index("saveStructuralUndo"))

    def test_a_psd_with_no_readable_layers_is_refused(self):
        body = _open_psd()
        self.assertIn("no layers Studio can read", body)
        self.assertLess(body.index("no layers Studio can read"),
                        body.index("saveStructuralUndo"))


class LayerFidelityTests(unittest.TestCase):
    def test_every_layer_property_is_restored(self):
        body = _open_psd()
        for field in ("layer.visible", "layer.opacity", "layer.blendMode"):
            with self.subTest(field=field):
                self.assertIn(field + " =", body)

    def test_the_blend_map_is_the_existing_one(self):
        """`_blendFromPS` already existed, built from `_blendToPS` and already
        exported — it had simply never had a reader. A second one would be the
        two-spellings-of-one-rule this codebase keeps paying for."""

        self.assertEqual(1, len(re.findall(r"const _blendFromPS = \{", CORE)))
        self.assertIn("C._blendFromPS[node.blendMode]", UI)

    def test_studios_own_adjustment_layers_survive_the_trip(self):
        """`savePSD` encodes them in the layer NAME because a PSD has nowhere
        else to put them. The reader has to know the same tag."""

        self.assertIn('"|ADJ|"', UI)
        self.assertIn('name.indexOf("|ADJ|")', UI)


class BothWaysInTests(unittest.TestCase):
    def test_a_dropped_psd_takes_the_document_route(self):
        """A PSD is a DOCUMENT, not an image to place on the current one."""

        start = APP.index('_canvasArea.addEventListener("drop"')
        block = APP[start:start + 900]
        self.assertIn("openPSD", block)
        self.assertIn(r"/\.psd$/i.test(file.name)", block)

    def test_the_extension_decides_rather_than_the_mime_type(self):
        """Browsers disagree: Chrome reports "image/vnd.adobe.photoshop" on
        some systems and "" on others, and a dropped project that silently did
        nothing would be the worst of the three outcomes."""

        self.assertRegex(HTML, r'id="loadImageInput"[^>]*accept="[^"]*\.psd')
        start = APP.index('getElementById("loadImageInput")?.addEventListener')
        block = APP[start:start + 700]
        self.assertIn(r"/\.psd$/i.test(file.name)", block)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_PSD_TESTS, found)


if __name__ == "__main__":
    unittest.main()
