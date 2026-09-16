"""U3 — the V2 brush painting on a real Canvas raster layer, behind an internal flag.

THE SEAM IS DELIBERATELY NARROW. Legacy's stroke slot already IS a coverage sink
with the properties V2 needs: `S.stroke.alphaMap` is document-sized coverage,
`S.stroke.dirty`/`frameDirty` are its bounds, and `commitStroke` applies
selection exactly once, writes the layer, takes one undo record, bumps one
revision and publishes to U2's presentation contract.

So V2 produces coverage and the Canvas owns everything else. Every property the
earlier units proved keeps holding without being re-implemented -- which is the
only reason a slice this small can be trusted.

THE ADAPTER IS TESTED BY EXECUTION, NOT BY SPELLING. Everything it touches on
the Canvas side is plain state, so `v2_u3_probe.js` stubs it and drives the real
module. That matters: the first draft treated `StrokeFilter.push` as returning a
LIST when it returns one sample, so the loop iterated the sample's own keys and
placed nothing. In a browser it looked almost right, because Legacy's opening
dab still marked the canvas. A counter reading `samples: 3, marks: 0` was the
only thing that showed it.

THE CSP GUARD IS HERE FOR A REASON. `presentation.py` carries the loader's hash
as a hand-maintained constant and calls a wrong one "the one failure the source
tests cannot see" -- the browser silently refuses to execute the loader and
Studio does not boot. It cost a real debugging detour in U2-VF. It is not
unseeable: the hash is a pure function of the file, and `test_the_csp_hash_*`
recomputes it.

Review: `Evidence/source-review/U3-canvas-adapter.md`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.studio_alpha._js_source import code_of  # noqa: E402

EXPECTED_U3_TESTS = 51

PROBE = Path(__file__).with_name("v2_u3_probe.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
ADAPTER = FRONTEND / "v2" / "canvas-adapter.js"
INDEX_HTML = FRONTEND / "index.html"
CANVAS_UI = FRONTEND / "canvas-ui.js"
SETTINGS_PAGE = FRONTEND / "settings-page.js"
PRESENTATION = APP_ROOT / "forge_studio" / "presentation.py"

INLINE_SCRIPT = re.compile(rb"<script>(.*?)</script>", re.S)


def _html_code_only(path: Path) -> str:
    """`index.html` with its HTML comments and inline-script line comments
    removed, so a word ban cannot match the sentence explaining the ban."""

    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"^[ 	]*//.*$", "", text, flags=re.M)


def _probe() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)], cwd=str(APP_ROOT), capture_output=True,
        text=True, check=False, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"the probe did not run:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


class _Probed(unittest.TestCase):
    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _probe()


class TheFlagIsOffAndInternalTests(_Probed):
    """§14. Shipping behaviour stays Legacy until U3 acceptance is complete."""

    def test_it_is_off_at_load(self) -> None:
        self.assertFalse(self.report["flagDefault"]["enabledAtLoad"])

    def test_it_refuses_by_name_while_off(self) -> None:
        self.assertEqual("v2-flag-off", self.report["flagDefault"]["refusal"])

    def test_it_returns_to_off(self) -> None:
        self.assertFalse(self.report["finalFlagState"])

    def test_it_is_not_a_settings_entry(self) -> None:
        if not SETTINGS_PAGE.exists():
            self.skipTest("settings-page.js not present")
        text = SETTINGS_PAGE.read_text(encoding="utf-8")
        self.assertNotIn("StudioBrushV2Adapter", text)
        self.assertNotIn("_setEnabled", text)

    def test_it_has_no_translatable_label(self) -> None:
        """A public choice would need one. Its absence is the evidence that
        this is a migration switch and not a product option.

        Comment-stripped: the loader's own comment NAMES the adapter in order
        to record that it is inert, and a raw-text ban would match the
        explanation of the ban."""

        html = _html_code_only(INDEX_HTML)
        self.assertNotIn("StudioBrushV2Adapter", html)
        self.assertNotIn("brush-engine-v2", html)
        self.assertNotIn("data-i18n=\"settings.brush.engine", html)

    def test_it_is_not_persisted(self) -> None:
        """§14: reversible without changing saved brush preferences. It has no
        storage at all, so a reload returns to Legacy -- the safe direction."""

        code = code_of(ADAPTER)
        self.assertNotIn("localStorage", code)
        self.assertNotIn("sessionStorage", code)


class V2PaintsThroughTheRealPipelineTests(_Probed):
    def test_a_contact_is_accepted(self) -> None:
        self.assertIsNone(self.report["paints"]["refusal"])
        self.assertEqual(1, self.report["paints"]["stats"]["contacts"])

    def test_marks_are_actually_placed(self) -> None:
        """The guard the first draft failed: samples consumed, zero marks."""

        self.assertGreater(self.report["paints"]["stats"]["marks"], 10)

    def test_coverage_reaches_the_canvas_buffer(self) -> None:
        self.assertGreater(self.report["paints"]["painted"], 1000)
        self.assertGreater(self.report["paints"]["stats"]["transfers"], 0)

    def test_the_adapter_is_idle_once_the_contact_ends(self) -> None:
        self.assertTrue(self.report["paints"]["adapterIdleAfterFinish"])

    def test_the_opening_contact_point_is_represented(self) -> None:
        """§17: the first endpoint exactly. Without feeding the pointerdown
        sample, V2's first mark is the first MOVE and the stroke starts late --
        which looked fine only because Legacy's own opening dab was still
        there, and this adapter removes it."""

        dirty = self.report["paints"]["dirty"]
        self.assertLessEqual(dirty["x0"], 25)
        self.assertLessEqual(dirty["y0"], 25)


class TheDirtyConversionIsExactTests(_Probed):
    """V2's `DirtyRegion` is half-open; Legacy's `S.stroke.dirty` is inclusive.
    An off-by-one here is a missing rim that shows as a hairline seam."""

    def test_it_contains_every_painted_pixel(self) -> None:
        self.assertTrue(self.report["dirtyConversion"]["containsEveryPaintedPixel"])

    def test_it_is_exactly_the_painted_bounding_box(self) -> None:
        """Not merely a superset: `x1` is the LAST painted column, not one
        past it."""

        self.assertTrue(self.report["dirtyConversion"]["isExact"])


class DispatchGroupingIsInvisibleTests(_Probed):
    """§19.3. The same path, grouped into different browser dispatches."""

    def test_every_grouping_produces_identical_coverage(self) -> None:
        self.assertTrue(self.report["grouping"]["allEqual"])

    def test_every_grouping_paints_the_same_count(self) -> None:
        painted = self.report["grouping"]["painted"]
        self.assertEqual(1, len(set(painted)))

    def test_the_comparison_is_not_between_empty_strokes(self) -> None:
        self.assertTrue(self.report["grouping"]["nonTrivial"])


class SettingsAreFrozenAtBeginTests(_Probed):
    """§19.9. Dragging a slider mid-contact must not alter the live stroke."""

    def test_a_mid_stroke_change_does_not_alter_the_stroke(self) -> None:
        self.assertTrue(self.report["frozenSettings"]["unaffected"])

    def test_the_frozen_size_is_the_one_resolved_at_begin(self) -> None:
        self.assertEqual(40, self.report["frozenSettings"]["frozenSize"])


class TheTargetCannotBeRedirectedTests(_Probed):
    """§19.10. Clicking another layer mid-contact must not move the stroke."""

    def test_the_stroke_continues_without_refusing(self) -> None:
        self.assertTrue(self.report["stableTarget"]["stillPainted"])
        self.assertTrue(self.report["stableTarget"]["noRefusalDuringStroke"])

    def test_the_active_layer_really_did_change_underneath(self) -> None:
        """Otherwise the test proves nothing."""

        self.assertNotEqual(self.report["stableTarget"]["frozenTarget"],
                            self.report["stableTarget"]["activeAtEnd"])

    def test_the_target_is_resolved_once_at_begin(self) -> None:
        code = code_of(ADAPTER)
        i = code.index("targetIndex:")
        self.assertIn("S.activeLayerIdx", code[i:i + 120])
        # `addFromEvent` must never re-resolve.
        body = code[code.index("function addFromEvent("):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("activeLayerIdx", body)


class UnsupportedCombinationsRefuseByNameTests(_Probed):
    """§13: unsupported kinds must refuse clearly, never silently lose a
    stroke. §19.11 and §19.19."""

    EXPECTED = {
        "hidden": "target-layer-is-hidden",
        "locked": "target-layer-is-locked",
        "adjustment": "target-is-not-a-raster-layer",
        "noLayer": "no-active-layer",
        "maskMode": "mask-and-region-targets-are-not-in-this-slice",
        "regionMode": "mask-and-region-targets-are-not-in-this-slice",
        "wrongTool": "tool-is-not-brush-or-eraser",
        "touch": "touch-is-not-a-paint-contact",
        "flagOff": "v2-flag-off",
    }

    def test_each_case_refuses_with_its_own_reason(self) -> None:
        for case, expected in self.EXPECTED.items():
            with self.subTest(case=case):
                self.assertEqual(expected, self.report["refusals"][case])

    def test_the_eraser_is_accepted(self) -> None:
        """§18: the matching eraser uses the same sampled footprint, so it is
        in the slice."""

        self.assertIsNone(self.report["refusals"]["eraserAccepted"])

    def test_a_touch_is_refused_rather_than_reinterpreted(self) -> None:
        """§17: a touch must not paint merely because nobody made a routing
        decision. The pen/finger product choice belongs to BE8 and is not
        pre-empted here."""

        self.assertEqual("touch-is-not-a-paint-contact",
                         self.report["refusals"]["touch"])


class CancelDropsTheContactTests(_Probed):
    def test_the_adapter_was_active_first(self) -> None:
        self.assertTrue(self.report["cancel"]["activeDuring"])

    def test_it_is_idle_afterwards(self) -> None:
        self.assertFalse(self.report["cancel"]["activeAfter"])

    def test_further_samples_are_ignored(self) -> None:
        self.assertEqual(0, self.report["cancel"]["furtherSamplesIgnored"])

    def test_cancel_touches_no_canonical_state(self) -> None:
        """The Canvas owns the transaction; `abortStroke` does the rollback.
        This function must not reach for pixels, undo or revision."""

        code = code_of(ADAPTER)
        body = code[code.index("function cancel("):]
        body = body[:body.index("\n}\n")]
        for forbidden in ("undo", "revision", "commit", "alphaMap"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)


class V2TakesSoleOwnershipOfTheStrokeBufferTests(_Probed):
    """Driven, not spelled.

    The structural version of this guard asserted the clearing code existed --
    and a mutation that removed only the CALL to it passed. Asserting a name is
    present is not asserting it runs, and this is the second time in this
    programme that exact mistake has been caught by a mutation rather than by
    review."""

    def test_legacy_coverage_is_cleared_from_the_alpha_map(self) -> None:
        self.assertEqual(0, self.report["soleOwnership"]["legacyResiduePixels"])

    def test_the_deferred_opening_dab_is_dropped(self) -> None:
        """It would otherwise be flushed by `commitStroke` AFTER every V2 mark,
        which is the harder half to notice."""

        self.assertTrue(self.report["soleOwnership"]["openingDabCleared"])

    def test_the_pixel_perfect_cell_is_dropped(self) -> None:
        self.assertTrue(self.report["soleOwnership"]["pixelPerfectCleared"])


class PerEventWorkDoesNotGrowTests(_Probed):
    """§19.16. The property U2 established for Legacy, holding through V2."""

    def test_it_does_not_scale_with_stroke_history(self) -> None:
        growth = self.report["noHistoryGrowth"]["growthRatio"]
        self.assertLess(growth, 1.35)

    def test_each_transfer_is_a_small_share_of_the_document(self) -> None:
        largest = self.report["noHistoryGrowth"]["largestTransfer"]
        doc = self.report["noHistoryGrowth"]["documentPixels"]
        self.assertLess(largest, doc * 0.05)

    def test_the_stroke_was_long_enough_to_show_growth(self) -> None:
        self.assertGreaterEqual(
            len(self.report["noHistoryGrowth"]["perEventTransferred"]), 30)


class TheAdapterOwnsCoverageAndNothingElseTests(unittest.TestCase):
    """The boundary the integration gate drew: V2 owns sample-to-coverage;
    Canvas owns documents, layers, selection, undo, revision, presentation."""

    def setUp(self) -> None:
        self.code = code_of(ADAPTER)

    def test_it_never_draws(self) -> None:
        """It produces coverage. Every pixel operation belongs to the Canvas."""

        for token in ("getContext", "drawImage", "putImageData", "getImageData",
                      "createElement"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.code)

    def test_it_never_applies_selection(self) -> None:
        """`alphaMapToImageData` applies it exactly once at merge (BE4).
        Applying it here would square it."""

        self.assertNotIn("S.selection.mask", self.code)

    def test_it_never_takes_an_undo_record_or_bumps_a_revision(self) -> None:
        for token in ("saveUndo", "undoStack", "_bumpRevision",
                      "canvasRevision", "commitStroke"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.code)

    def test_it_does_not_reimplement_the_compositor(self) -> None:
        for token in ("_compBuffer", "composite(", "renderNow"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.code)

    def test_the_ownership_helper_exists(self) -> None:
        self.assertIn("function _takeOwnership(", self.code)
        self.assertIn("_openingDab = null", self.code)
        self.assertIn("_ppPrev = null", self.code)

    def test_the_ownership_clear_is_bounded(self) -> None:
        body = self.code[self.code.index("function _takeOwnership("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("st.dirty", body)
        self.assertNotIn("alphaMap.fill(0)", body)


class TheWiringIsMinimalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code = code_of(CANVAS_UI)

    def test_the_contact_is_offered_after_legacy_opens_the_transaction(self) -> None:
        i = self.code.index("C.beginStroke(p.x, p.y, p.pressure)")
        self.assertIn("_v2.begin(", self.code[i:i + 400])

    def test_v2_receives_the_raw_event(self) -> None:
        """§17: its own normalizer must see the browser's coalesced samples.
        Handing it Legacy's already-normalized stream would put two normalizers
        in series."""

        self.assertIn("_v2.addFromEvent(S, e,", self.code)

    def test_the_final_endpoint_is_flushed_before_commit(self) -> None:
        """After `commitStroke` the alpha map is gone.

        U3-V: asserted at EVERY call site, not just the first one found.
        There are two -- `pointerleave` and `pointerup` -- and while only
        `pointerleave` had the seam, this test passed by looking at it and
        never noticed that the ordinary release path had no `_v2.finish` at
        all. A guard that stops at the first match is a guard on one branch.
        """

        sites = [i for i in range(len(self.code))
                 if self.code.startswith("_v2.finish(", i)]
        self.assertGreaterEqual(len(sites), 2, "both release paths must finish V2")
        for i in sites:
            self.assertIn("C.commitStroke()", self.code[i:i + 500],
                          msg=f"no commit follows the _v2.finish at offset {i}")

    def test_cancel_and_blur_drop_the_transient_contact(self) -> None:
        self.assertEqual(2, self.code.count("StudioBrushV2Adapter.cancel()"))


class ThePageLoadsV2ButNotTheAcceptanceSurfaceTests(unittest.TestCase):
    def test_the_kernel_and_adapter_are_loaded(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for module in ("v2/input.js", "v2/sampler.js", "v2/filters.js",
                       "v2/dynamics.js", "v2/coverage.js",
                       "v2/canvas-adapter.js"):
            with self.subTest(module=module):
                self.assertIn(module, html)

    def test_the_scratchpad_is_NOT_loaded(self) -> None:
        """It is an engineering acceptance surface, not a product module."""

        self.assertNotIn("v2/scratchpad.js",
                         INDEX_HTML.read_text(encoding="utf-8"))

    def test_they_are_optional_so_a_missing_module_degrades_to_legacy(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        i = html.index("optionalScripts")
        self.assertIn("v2/canvas-adapter.js", html[i:i + 900])


class TheCspHashMatchesTheLoaderTests(unittest.TestCase):
    """The guard `presentation.py` says cannot exist.

    Its comment calls a wrong hash "the one failure the source tests cannot
    see": the browser silently refuses to execute the inline loader, Studio does
    not boot, and no console error explains why. It cost a real detour in
    U2-VF. The hash is a pure function of the file."""

    @staticmethod
    def _hashes() -> list[str]:
        bodies = INLINE_SCRIPT.findall(INDEX_HTML.read_bytes())
        out = []
        for body in bodies:
            # CRLF normalised to LF -- established by reproducing the shipped
            # constant. Hashing the file's own CRLF gives a different digest
            # and a page that does not boot.
            digest = hashlib.sha256(body.replace(b"\r\n", b"\n")).digest()
            out.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
        return out

    def test_index_html_has_exactly_one_inline_script(self) -> None:
        self.assertEqual(1, len(self._hashes()))

    def test_the_loader_hash_constant_matches_the_file(self) -> None:
        source = PRESENTATION.read_text(encoding="utf-8")
        self.assertIn(self._hashes()[0], source)

    def test_the_constant_is_actually_used_in_the_header(self) -> None:
        source = PRESENTATION.read_text(encoding="utf-8")
        self.assertIn("_SOURCE_LOADER_CSP_HASH", source)
        self.assertIn("script-src 'self' 'unsafe-hashes'", source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U3_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
