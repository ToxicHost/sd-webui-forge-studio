"""U2 — the dirty WebGL presentation path.

WHAT THIS UNIT REMOVED, and why it was there:

  1. A full-document Canvas2D composite on EVERY pointer move whose result
     nothing read. `_composite2D`'s product is `_compBuffer`, and both of its
     consumers -- the display blit and the `_compBufCache` snapshot -- are gated
     off during a brush stroke on the WebGL default. The comment that justified
     building it anyway pointed at `window.StudioCanvasImagePreview`, an object
     that does not exist anywhere in the tree.
  2. A wet-stroke conversion scoped to the ACCUMULATED stroke rectangle, so a
     long stroke re-converted its whole bounding box on every move. BE8 fixed
     exactly this in the Canvas2D dirty fast path and could not fix it here,
     because that path is unreachable when the WebGL preview owns the display.
  3. Full-canvas blits at commit and in the overlay.
  4. A full document flatten, readback and texture upload for every present.

WHY THE ASSERTIONS ARE COUNTS AND STRUCTURE, NOT MILLISECONDS. BE8's record
measured the same configuration swinging 4x across page states with nothing
changed but heap pressure and GC. A millisecond threshold here would fail on
another machine for no engineering reason. The timings live in `Evidence/`.

THE PRESENTATION CONTRACT IS EXECUTED, NOT SCANNED. `u2_presentation_probe.js`
extracts the real block from `canvas-core.js` by its own section markers and
runs it, so these are behavioural tests of the shipped source rather than
assertions about how it is spelled.

Review: `Evidence/source-review/U2-dirty-presentation.md`.
"""

from __future__ import annotations

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

EXPECTED_U2_TESTS = 59

PROBE = Path(__file__).with_name("u2_presentation_probe.js")
FRONTEND = APP_ROOT / "forge_studio" / "frontend"
CORE = FRONTEND / "canvas-core.js"
PREVIEW = FRONTEND / "canvas-webgl-preview.js"
INDEX_HTML = FRONTEND / "index.html"
SETTINGS_PAGE = FRONTEND / "settings-page.js"


def _probe() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)], cwd=str(APP_ROOT), capture_output=True,
        text=True, check=False, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"the probe did not run:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


def _function_body(code: str, name: str) -> str:
    start = code.index(f"function {name}(")
    end = code.index("\n}\n", start)
    return code[start:end]


class _Probed(unittest.TestCase):
    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _probe()


class FullIsTheSafeDefaultTests(_Probed):
    """Every pre-existing `markCompositeDirty` caller keeps its exact meaning --
    "I changed something, somewhere" -- and gets a full invalidation. Only a
    caller that can NAME its region opts into the bounded path."""

    def test_a_fresh_canvas_owes_the_whole_display(self) -> None:
        self.assertTrue(self.report["initialState"]["full"])

    def test_an_acknowledgement_clears_it(self) -> None:
        after = self.report["afterFirstAck"]
        self.assertFalse(after["full"])
        self.assertTrue(after["empty"])

    def test_an_unnamed_change_invalidates_everything(self) -> None:
        self.assertTrue(self.report["afterPlainMark"]["full"])


class ANamedRegionStaysARegionTests(_Probed):
    def test_one_region_is_reported_exactly(self) -> None:
        r = self.report["oneRegion"]
        self.assertFalse(r["full"])
        self.assertEqual((100, 100, 150, 140), (r["x0"], r["y0"], r["x1"], r["y1"]))

    def test_two_regions_union(self) -> None:
        r = self.report["unionedRegion"]
        self.assertFalse(r["full"])
        self.assertEqual((100, 100, 420, 330), (r["x0"], r["y0"], r["x1"], r["y1"]))

    def test_fractional_bounds_round_outward(self) -> None:
        """Inward rounding would leave a rim of changed pixels unpresented,
        which is what a dirty-region bug actually looks like on screen."""

        r = self.report["fractional"]
        self.assertLessEqual(r["x0"], 10)
        self.assertLessEqual(r["y0"], 20)
        self.assertGreaterEqual(r["x1"], 31)
        self.assertGreaterEqual(r["y1"], 41)


class FullDominatesARegionTests(_Probed):
    def test_a_region_then_a_full_is_full(self) -> None:
        self.assertTrue(self.report["regionThenFull"]["full"])

    def test_a_full_then_a_region_is_still_full(self) -> None:
        """A consumer that owes the whole display already owes this rectangle,
        so a later region must not narrow it."""

        self.assertTrue(self.report["fullThenRegion"]["full"])

    def test_a_full_invalidation_reports_no_misleading_rectangle(self) -> None:
        """A full invalidation that still carried the last region's coordinates
        would hand a consumer a box that looks meaningful and is not.

        Added because a mutation of the full-dominance shortcut changed nothing
        observable -- which is what a shortcut that is only an optimisation
        looks like, and the real invariant was this one."""

        for case in ("afterPlainMark", "regionThenFull", "fullThenRegion"):
            with self.subTest(case=case):
                r = self.report[case]
                self.assertTrue(r["full"])
                self.assertEqual((0, 0, 0, 0),
                                 (r["x0"], r["y0"], r["x1"], r["y1"]))


class RegionsAreClippedToTheDocumentTests(_Probed):
    def test_negative_bounds_clip_to_the_origin(self) -> None:
        r = self.report["clippedLow"]
        self.assertEqual(0, r["x0"])
        self.assertEqual(0, r["y0"])

    def test_oversized_bounds_clip_to_the_document(self) -> None:
        r = self.report["clippedHigh"]
        self.assertEqual(200, r["x1"])
        self.assertEqual(150, r["y1"])

    def test_a_degenerate_region_is_empty_not_a_sliver(self) -> None:
        self.assertTrue(self.report["degenerate"]["empty"])

    def test_a_region_entirely_outside_is_empty(self) -> None:
        self.assertTrue(self.report["entirelyOutside"]["empty"])

    def test_a_publish_that_landed_nowhere_still_moved_the_version(self) -> None:
        """Otherwise a consumer could acknowledge work it never saw."""

        self.assertTrue(self.report["outsideStillBumpedVersion"])


class NoLostUpdateTests(_Probed):
    """§5.6. An edit that arrives while an upload is in flight must be
    presented on the next frame, not dropped."""

    def test_a_stale_acknowledgement_is_refused(self) -> None:
        self.assertFalse(self.report["lostUpdate"]["acceptedStale"])

    def test_a_current_acknowledgement_is_accepted(self) -> None:
        self.assertTrue(self.report["lostUpdate"]["acceptedFresh"])

    def test_the_edit_that_arrived_mid_upload_is_still_pending(self) -> None:
        self.assertTrue(self.report["lostUpdate"]["keptTheLateEdit"])

    def test_the_earlier_edit_is_kept_too(self) -> None:
        """A union was kept rather than a replace: the refused acknowledgement
        must not discard what the consumer failed to present."""

        self.assertTrue(self.report["lostUpdate"]["keptTheEarlyEdit"])

    def test_an_accepted_acknowledgement_finally_clears_it(self) -> None:
        self.assertTrue(self.report["lostUpdate"]["afterAccept"]["empty"])

    def test_acknowledging_the_same_generation_twice_is_refused(self) -> None:
        d = self.report["doubleAck"]
        self.assertTrue(d["first"])
        self.assertFalse(d["second"])
        self.assertFalse(d["stillPending"]["empty"])


class AnExplicitInvalidationIsNotADocumentEditTests(_Probed):
    """Context restore, a rollback toggle and a refused region are display
    events. Bumping the composite version for them would throw away every cache
    keyed on it for something the document did not do."""

    def test_it_invalidates_the_whole_display(self) -> None:
        self.assertTrue(self.report["explicitInvalidation"]["state"]["full"])

    def test_it_does_not_bump_the_composite_version(self) -> None:
        self.assertTrue(self.report["explicitInvalidation"]["versionUnchanged"])

    def test_it_reports_its_reason(self) -> None:
        self.assertEqual("webgl-context-restored",
                         self.report["explicitInvalidation"]["reason"])

    def test_it_clears_a_pending_rectangle(self) -> None:
        """U2-V REGRESSION. This invariant was pinned for `markCompositeDirty`
        and not here, so it shipped half-closed -- and a half-closed invariant
        is worse than an absent one, because a reader who checks one site
        reasonably assumes the other.

        Found in a browser, twice: a region flatten refused under a non-local
        Develop setting, and a WebGL context restore, both reporting
        `full: true` beside a rectangle that looked live."""

        case = self.report["invalidateAfterRegion"]
        self.assertFalse(case["withRegion"]["full"])
        self.assertEqual((100, 100, 200, 200),
                         (case["withRegion"]["x0"], case["withRegion"]["y0"],
                          case["withRegion"]["x1"], case["withRegion"]["y1"]))
        after = case["after"]
        self.assertTrue(after["full"])
        self.assertEqual((0, 0, 0, 0),
                         (after["x0"], after["y0"], after["x1"], after["y1"]))


class TheDiscardedCompositeIsGoneTests(unittest.TestCase):
    """The dominant per-move cost on the shipping default: a full-document
    layer loop plus develop, built into a buffer nothing read."""

    def test_the_composite_is_skipped_on_the_webgl_brush_path(self) -> None:
        code = code_of(CORE)
        self.assertIn("_skipDiscardedComposite", code)
        self.assertIn("if (!_skipDiscardedComposite) {", code)

    def test_the_skip_is_gated_on_there_being_no_mask_to_draw(self) -> None:
        """`_composite2D` also draws mask overlays onto the display canvas, and
        those ARE visible. Skipping it with a mask on screen would blank them."""

        code = code_of(CORE)
        start = code.index("_skipDiscardedComposite =")
        clause = code[start:start + 260]
        self.assertIn("!showMask", clause)
        self.assertIn("!S.editingMask", clause)
        self.assertIn("S.imagePreviewActive", clause)
        self.assertIn("S.drawing", clause)

    def test_no_code_anywhere_references_that_object(self) -> None:
        """Checked across the whole frontend as CODE, because the justification
        would have been sound if anything installed it.

        Comment-stripped deliberately: the corrected comment in `canvas-core.js`
        NAMES the object in order to record that it does not exist, and a
        raw-text ban would match the explanation of the ban."""

        hits = []
        for path in FRONTEND.rglob("*.js"):
            if "StudioCanvasImagePreview" in code_of(path):
                hits.append(path.name)
        self.assertEqual([], hits)

    def test_the_correction_is_recorded_rather_than_deleted(self) -> None:
        """The stale claim justified real behaviour, so the record of its being
        false is worth as much as its removal."""

        source = CORE.read_text(encoding="utf-8")
        i = source.index("StudioCanvasImagePreview")
        near = source[max(0, i - 900):i + 900]
        self.assertIn("does not exist", near)


class TheWetStrokeIsBoundedTests(unittest.TestCase):
    #: A CODE anchor, not a comment one. `code_of` strips comments, so
    #: anchoring on the block's own comment finds nothing at all.
    WET_ANCHOR = "let strokeDrawCanvas = null;"

    def _wet_block(self) -> str:
        code = code_of(CORE)
        i = code.index(self.WET_ANCHOR)
        return code[i:i + 1200]

    def test_the_conversion_uses_the_frame_rectangle(self) -> None:
        """Not the accumulated one. With the accumulated rectangle the cost of
        a move grows with the length of the stroke."""

        block = self._wet_block()
        self.assertIn("S.stroke.frameDirty", block)
        self.assertIn("alphaMapToImageData(col, frame)", block)

    def test_the_frame_rectangle_is_consumed(self) -> None:
        """Unconsumed it would accumulate like `dirty` and the bound would
        silently stop being a bound -- invisible, because the pixels would
        still be right."""

        self.assertIn("S.stroke.frameDirty = { x0: S.W, y0: S.H, x1: 0, y1: 0 }",
                      self._wet_block())

    def test_the_full_canvas_clear_is_gone_from_that_block(self) -> None:
        self.assertNotIn("S.stroke.ctx.clearRect(0, 0, S.W, S.H)",
                         self._wet_block())

    def test_the_overlay_blit_is_bounded(self) -> None:
        code = code_of(CORE)
        self.assertIn("c.drawImage(strokeDrawCanvas, _sx, _sy, _sw, _sh,", code)

    def test_the_commit_blit_is_bounded(self) -> None:
        body = _function_body(code_of(CORE), "commitStroke")
        self.assertIn("T.ctx.drawImage(S.stroke.canvas, dx, dy, dw, dh, dx, dy, dw, dh)",
                      body)


class CommitPublishesItsRegionTests(unittest.TestCase):
    def test_commit_stroke_names_the_region_it_changed(self) -> None:
        body = _function_body(code_of(CORE), "commitStroke")
        self.assertIn("markCompositeDirtyRegion", body)

    def test_a_stroke_that_painted_nothing_publishes_nothing(self) -> None:
        """A full invalidation for a stroke that changed no pixel is the same
        confident-wrong-number failure the V2 execution guard exists to
        prevent, wearing a different hat."""

        body = _function_body(code_of(CORE), "commitStroke")
        i = body.index("markCompositeDirtyRegion")
        guard = body[max(0, i - 300):i]
        self.assertIn("S.stroke.dirty.x1 >= S.stroke.dirty.x0", guard)

    def test_it_publishes_before_the_stroke_state_is_torn_down(self) -> None:
        body = _function_body(code_of(CORE), "commitStroke")
        self.assertLess(body.index("markCompositeDirtyRegion"),
                        body.index("S.stroke.alphaMap = null"))


class TheUploadIsBoundedAllTheWayDownTests(unittest.TestCase):
    """§5.3: "Do not merely crop the GPU upload while still flattening,
    compositing, and reading the entire document on CPU"."""

    def test_the_region_drives_the_flatten_as_well_as_the_upload(self) -> None:
        code = code_of(PREVIEW)
        self.assertIn("Core.getFlattenedRegionImageData(", code)

    def test_a_bounded_upload_uses_a_sub_rectangle(self) -> None:
        code = code_of(PREVIEW)
        self.assertIn("texSubImage2D(_gl.TEXTURE_2D, 0, pending.x0, pending.y0, rw, rh",
                      code)

    def test_a_failed_region_flatten_fails_closed_to_a_full_invalidation(self) -> None:
        """The region path is an optimisation; the display being right is not."""

        code = code_of(PREVIEW)
        self.assertIn('invalidatePresentation("region-flatten-unavailable")', code)

    def test_the_upload_reports_whether_it_finished(self) -> None:
        """`_pixelsDirty` used to be cleared unconditionally, so a failed
        present looked exactly like a successful one."""

        code = code_of(PREVIEW)
        self.assertIn("if (_uploadTexture()) {", code)


class TheRegionFlattenRefusesWhenDevelopIsSpatialTests(unittest.TestCase):
    """Develop contains a luminance blur, unsharp masks, a vignette and grain.
    A region computed in isolation differs from the same region computed inside
    the whole document, and the difference shows as a seam."""

    def test_a_region_flatten_is_refused_while_develop_is_active(self) -> None:
        body = _function_body(code_of(CORE), "canPresentRegion")
        self.assertIn("_isIdentity", body)
        self.assertIn("return false", body)

    def test_it_refuses_rather_than_guesses_when_develop_will_not_say(self) -> None:
        body = _function_body(code_of(CORE), "canPresentRegion")
        tail = body[body.index("_isIdentity"):]
        self.assertIn("return false;", tail)

    def test_the_region_flatten_consults_the_gate(self) -> None:
        body = _function_body(code_of(CORE), "getFlattenedRegionImageData")
        self.assertIn("canPresentRegion()", body)
        self.assertIn("return null", body)

    def test_the_region_flatten_scopes_the_adjustment_ops(self) -> None:
        """`getImageData`/`putImageData` ignore the transform and address the
        backing store, so the adjustment ops must be told the REGION's size."""

        body = _function_body(code_of(CORE), "_renderFlattenedToContext")
        self.assertIn("_applyAdjustment(ctx, rw, rh, L)", body)


class ContextLossIsHandledTests(unittest.TestCase):
    """There was no handling at all before U2. That was survivable while every
    present re-uploaded the whole document -- a restored context got a complete
    texture by accident. With bounded uploads a restored context would show one
    correct rectangle on a blank document, permanently."""

    def test_both_context_events_are_listened_for(self) -> None:
        code = code_of(PREVIEW)
        self.assertIn('addEventListener("webglcontextlost"', code)
        self.assertIn('addEventListener("webglcontextrestored"', code)

    def test_loss_is_prevented_from_being_final(self) -> None:
        """Without `preventDefault` the context is never restored at all."""

        code = code_of(PREVIEW)
        i = code.index('addEventListener("webglcontextlost"')
        self.assertIn("preventDefault()", code[i:i + 400])

    def test_restoration_forces_a_full_upload(self) -> None:
        code = code_of(PREVIEW)
        i = code.index('addEventListener("webglcontextrestored"')
        block = code[i:i + 700]
        self.assertIn('invalidatePresentation("webgl-context-restored")', block)
        self.assertIn("_texW = 0", block)

    def test_rendering_is_refused_while_the_context_is_lost(self) -> None:
        code = code_of(PREVIEW)
        body = code[code.index("function renderNow()"):]
        body = body[:1200]
        self.assertIn("if (_contextLost) return;", body)
        self.assertIn("isContextLost", body)


class TheRollbackIsInternalOnlyTests(unittest.TestCase):
    """§5.9 requires the previous behaviour to stay selectable for one Alpha
    cycle. §5.3 requires no new public engine choice."""

    def test_the_rollback_flag_exists(self) -> None:
        code = code_of(PREVIEW)
        self.assertIn("_forceFullUploads", code)
        self.assertIn("_setForceFullUploads", code)

    def test_it_forces_a_full_upload_when_set(self) -> None:
        code = code_of(PREVIEW)
        self.assertIn("|| _forceFullUploads", code)

    def test_it_is_not_a_settings_entry(self) -> None:
        if not SETTINGS_PAGE.exists():
            self.skipTest("settings-page.js not present")
        text = SETTINGS_PAGE.read_text(encoding="utf-8")
        self.assertNotIn("forceFullUploads", text)
        self.assertNotIn("ForceFullUploads", text)

    def test_it_has_no_translatable_label(self) -> None:
        """A public choice would need one, so its absence is the evidence that
        this is a migration detail rather than a product option."""

        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("forceFullUploads", html)
        self.assertNotIn("fullUploads", html)

    def test_it_is_not_persisted(self) -> None:
        code = code_of(PREVIEW)
        i = code.index("_setForceFullUploads")
        self.assertNotIn("localStorage", code[i:i + 500])


class TheEvidenceSurfaceExistsTests(unittest.TestCase):
    """A bounded upload is proved by the pixels it moved. No wall-clock can
    tell a small upload from a large one on a fast enough machine."""

    def test_upload_counts_are_exposed(self) -> None:
        code = code_of(PREVIEW)
        for field in ("fullUploads", "subUploads", "uploadedPixels",
                      "flattenedPixels", "fullReasons", "refusedAcks"):
            with self.subTest(field=field):
                self.assertIn(field, code)

    def test_every_full_upload_records_a_named_reason(self) -> None:
        """§5.4: an operation may choose full invalidation for correctness, but
        it must be NAMED and measured."""

        code = code_of(PREVIEW)
        self.assertIn("_noteFullReason(reason)", code)
        for reason in ("texture-size", "rollback-flag", "canvas-requested-full"):
            with self.subTest(reason=reason):
                self.assertIn(reason, code)

    def test_the_stats_are_resettable(self) -> None:
        self.assertIn("resetUploadStats", code_of(PREVIEW))


class NothingAboutThisIsOwnerFacingTests(unittest.TestCase):
    def test_the_committed_pixels_path_is_untouched(self) -> None:
        """Commit is `commitStroke` into a layer canvas and is independent of
        display. U2 changed WHERE the display reads from, never what a layer
        holds -- which is why a Canvas2D/WebGL parity test is cheap."""

        body = _function_body(code_of(CORE), "commitStroke")
        self.assertNotIn("imagePreviewActive", body)
        self.assertNotIn("WebGL", body)

    def test_export_still_flattens_the_whole_document(self) -> None:
        body = _function_body(code_of(CORE), "exportFlattened")
        self.assertNotIn("region", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_U2_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
