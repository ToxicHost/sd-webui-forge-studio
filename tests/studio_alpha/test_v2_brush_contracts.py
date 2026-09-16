"""The Brush V2 seam contracts and their guards. V2-01b, spec BE1.

`Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md` §22 gives BE1 one exit
gate: **types, field enumeration, tip support matrix, preset leak guard.**
V2-01 delivered the Canvas/AI half of the types; this closes the rest.

THE PRESET LEAK GUARD IS THE POINT OF THIS FILE, and it is worth saying why it
is not simply BE14 again. `BE14-preset-rebuild.md` §4 found that
`applyBrushPreset` wrote fourteen fields and not `brushRatio`, `brushSpikes`,
`brushDensity`, `brushAngle`, `brushTaperIn` or `brushFalloff` -- "exactly the
controls BE6 proved alive, and exactly the ones no preset sets. That is not a
coincidence, it is one defect." Crank Ratio to 4 on Scatter Dust, pick Hard
Ink, and Hard Ink is elliptical until the owner notices.

BE14 closed it by making all sixteen presets declare all fifteen fields, proven
from a poisoned state. **That is a convention, and it fails on the day a
sixteenth setting is added and one preset forgets it.** Spec §19.4 asks for the
stronger thing -- "prevent the six known preset-leaking fields by complete V2
preset resolution" -- so V2 resolves every registered setting from ordered
sources and the previous preset is not one of them. The leak becomes
unrepresentable rather than absent, and `test_nothing_leaks_from_a_poisoned_state`
is BE14's own experiment pointed at the mechanism.

THE ONE CARRY-OVER THAT IS BY DESIGN. §14: "Keep Working Size When Switching
MUST default on", while "Opacity and Flow normally load from the selected
preset." So Size surviving a switch is the feature and Ratio surviving it is
the defect -- and both are "a value from before the switch". The registry
declares the scope per setting so the guard can tell them apart instead of
assuming, which is why there is a test for the carry-over as well as against
the leak.

Review: `Evidence/source-review/V2-01b-brush-contracts.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.v2_contracts import TargetKind  # noqa: E402
from tests.studio_alpha._js_source import code_only as _code_only  # noqa: E402

EXPECTED_V2_BRUSH_TESTS = 53

PROBE = Path(__file__).with_name("v2_01b_probe.js")
MODULE = (APP_ROOT / "forge_studio" / "frontend" / "v2"
          / "brush-contracts.js")
INDEX_HTML = APP_ROOT / "forge_studio" / "frontend" / "index.html"

#: The six `BE14-preset-rebuild.md` §4 named. Written here as well as in the
#: module so the guard's SUBJECT cannot be edited away by editing the module.
BE14_LEAKING_FIELDS = ("angle", "density", "falloff", "ratio", "spikes",
                       "taperIn")


def _probe() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)], cwd=str(APP_ROOT), capture_output=True,
        text=True, check=False, timeout=300)
    if result.returncode != 0:
        raise AssertionError(
            f"the probe did not run:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout)


class _Probed(unittest.TestCase):
    """One node run for the whole class. The probe is deterministic and has no
    state, so re-running it per test would buy nothing and cost a process."""

    report: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _probe()


class EveryFieldIsEnumeratedTests(_Probed):
    """§22 BE1's "field enumeration", asserted in BOTH directions.

    A declared field missing from the built object is a contract that lies. A
    built field missing from the declaration is worse: it travels, nothing
    names it, and the first consumer to drop it does so silently.
    """

    def test_no_declared_field_is_missing_from_the_object(self) -> None:
        for name, result in self.report["enumeration"].items():
            with self.subTest(contract=name):
                self.assertEqual([], result["declaredNotPresent"])

    def test_no_object_field_is_missing_from_the_declaration(self) -> None:
        for name, result in self.report["enumeration"].items():
            with self.subTest(contract=name):
                self.assertEqual([], result["presentNotDeclared"])

    def test_all_four_types_were_actually_exercised(self) -> None:
        """A probe that silently built nothing would report empty lists and
        look identical to a pass."""

        self.assertEqual({"sample", "descriptor", "target", "commit"},
                         set(self.report["enumeration"]))


class ASampleCarriesWhatWasMeasuredTests(_Probed):
    """§7.1, plus the two availability flags Studio keeps and the spec omits."""

    def test_an_unknown_pointer_kind_is_refused(self) -> None:
        """`pressureIsMeasured` (`canvas-input.js:64`) switches on exactly pen,
        mouse and touch. A fourth value falls through it to "not measured"
        without saying so."""

        self.assertTrue(self.report["sample"]["unknownPointerRefused"])
        self.assertTrue(self.report["sample"]["emptyPointerRefused"])

    def test_the_three_the_spec_names_are_all_accepted(self) -> None:
        self.assertTrue(self.report["sample"]["everyPointerKindAccepted"])

    def test_availability_is_kept_separately_from_the_value(self) -> None:
        """The INTENTIONAL DIVERGENCE from §7.1, and §8.1 is why: a fallback
        has to know a sensor was missing. A mouse reports a well-formed 0.5 and
        it means nothing."""

        self.assertEqual(0.5, self.report["sample"]["pressureKeptWhenUnavailable"])
        self.assertIs(False, self.report["sample"]["availabilityKept"])

    def test_an_absent_azimuth_is_null_and_not_zero(self) -> None:
        """A defaulted 0 tells an azimuth dynamic it has a reading. Studio's
        normaliser computes none -- see the review record §6."""

        self.assertIsNone(self.report["sample"]["absentAzimuthIsNull"])
        self.assertIsNone(self.report["sample"]["absentBarrelIsNull"])

    def test_pressure_is_clamped_rather_than_trusted(self) -> None:
        """The spec bounds it 0..1 and a driver that disagrees would otherwise
        scale a brush off the canvas."""

        self.assertEqual([0, 1], self.report["sample"]["pressureClamped"])

    def test_a_negative_sequence_is_refused(self) -> None:
        self.assertTrue(self.report["sample"]["negativeSequenceRefused"])

    def test_a_sample_is_frozen(self) -> None:
        self.assertTrue(self.report["sample"]["frozen"])


class ADescriptorFreezesOneContactTests(_Probed):
    """§7.2: "The descriptor MUST freeze behavior for one contact." Structural,
    not conventional -- a mid-stroke write throws instead of taking effect on
    the next dab, which is how a preset change halfway through a stroke would
    otherwise show as a stroke that changes character in the middle."""

    def test_the_descriptor_cannot_be_written_to(self) -> None:
        self.assertTrue(self.report["descriptor"]["frozen"])

    def test_its_resource_list_cannot_be_written_to_either(self) -> None:
        """Freezing the object and leaving a mutable array on it is the usual
        way this property is lost."""

        self.assertTrue(self.report["descriptor"]["resourceHashesFrozen"])

    def test_a_resource_that_is_not_a_content_hash_is_refused(self) -> None:
        """§13: "Resource identity MUST be content-based and MUST NOT expose
        absolute paths.\""""

        self.assertTrue(self.report["descriptor"]["badResourceHashRefused"])

    def test_a_path_shaped_identifier_is_refused(self) -> None:
        self.assertTrue(self.report["descriptor"]["pathStrokeIdRefused"])

    def test_the_refusal_does_not_quote_the_value_back(self) -> None:
        """A malformed identifier and an unknown one must be
        indistinguishable, so probing learns nothing."""

        self.assertTrue(self.report["descriptor"]["refusalDoesNotQuoteTheValue"])

    def test_a_stroke_without_a_seed_is_refused(self) -> None:
        """§13: "Deterministic modes MUST serialize their seed/order." A stroke
        without one cannot be replayed, and §20 Correctness 10 requires replay
        to be pixel-stable."""

        self.assertTrue(self.report["descriptor"]["missingSeedRefused"])

    def test_a_stroke_without_a_target_is_refused(self) -> None:
        self.assertTrue(self.report["descriptor"]["missingTargetRefused"])

    def test_the_three_performance_modes_are_the_only_ones(self) -> None:
        self.assertTrue(self.report["descriptor"]["unknownModeRefused"])
        self.assertTrue(self.report["descriptor"]["everyModeAccepted"])


class ATargetIsIdentityOnlyTests(_Probed):
    """§7.3, and the same rule `v2_contracts.TargetRef` holds on the server."""

    def test_the_six_kinds_match_the_server_contract(self) -> None:
        """TWO DECLARATIONS, ONE SET. The JS module cannot import the Python
        enum, so the join is asserted here rather than assumed -- which is the
        same reason `v2_contracts.ADMITTED_ASSET_DISPOSITION` exists."""

        self.assertEqual({k.value for k in TargetKind},
                         set(self.report["target"]["kinds"]))

    def test_an_unknown_kind_is_refused(self) -> None:
        self.assertTrue(self.report["target"]["unknownKindRefused"])

    def test_a_path_is_refused_in_every_identifier(self) -> None:
        self.assertTrue(self.report["target"]["pathDocumentRefused"])
        self.assertTrue(self.report["target"]["pathChannelRefused"])

    def test_an_empty_channel_is_legal(self) -> None:
        """A raster layer is its own channel."""

        self.assertTrue(self.report["target"]["emptyChannelLegal"])


class ACommitReportsWhatItDidTests(_Probed):
    """§7.5."""

    def test_every_timing_stage_the_spec_names_is_present(self) -> None:
        self.assertTrue(self.report["commit"]["everyTimingStagePresent"])

    def test_an_unreported_stage_is_zero_rather_than_absent(self) -> None:
        """§16 requires full-dispatch attribution. A missing key reads as "not
        measured" at one caller and crashes at the next."""

        self.assertEqual(0, self.report["commit"]["missingStageBecomesZero"])

    def test_a_warning_carrying_a_path_is_refused(self) -> None:
        """§18: private paths must not enter logs or support bundles. By the
        time a path is inside prose no redaction reliably catches it."""

        self.assertTrue(self.report["commit"]["pathWarningRefused"])

    def test_an_unknown_recovery_disposition_is_refused(self) -> None:
        """§17 journals OUTSIDE the paint path, so "queued" is the honest
        answer and "written" is a claim the paint path cannot make."""

        self.assertTrue(self.report["commit"]["unknownDispositionRefused"])


class ASinkIsCompleteOrRefusedTests(_Probed):
    """§7.4. A partially implemented sink produces rows that look like results
    and are holes -- the failure the oracle's `validateAdapter` exists for, one
    layer down."""

    def test_a_complete_sink_and_transaction_are_accepted(self) -> None:
        self.assertTrue(self.report["sink"]["completeSinkAccepted"])
        self.assertTrue(self.report["sink"]["completeTransactionAccepted"])

    def test_an_incomplete_sink_is_refused(self) -> None:
        self.assertTrue(self.report["sink"]["incompleteSinkRefused"])

    def test_a_transaction_missing_one_method_is_refused(self) -> None:
        self.assertTrue(self.report["sink"]["partialTransactionRefused"])


class TheTipMatrixIsDecidedForEveryControlTests(_Probed):
    """§22 BE1's "tip support matrix", and the half of it that cannot be
    checked here."""

    def test_a_family_must_decide_every_control(self) -> None:
        """Silence is what `TIP_CAPABILITIES` never had to express, and it is
        how a control ends up neither supported nor refused."""

        self.assertTrue(self.report["tipMatrix"]["undecidedControlRefused"])

    def test_a_family_cannot_declare_a_control_that_does_not_exist(self) -> None:
        self.assertTrue(self.report["tipMatrix"]["unknownControlRefused"])

    def test_a_declaration_is_true_false_or_a_reason(self) -> None:
        """The inherited engine's `"needs-shape"` is the pattern and it is a
        good one: it tells an owner WHY a control is inert."""

        self.assertTrue(self.report["tipMatrix"]["reasonStringAccepted"])
        self.assertTrue(self.report["tipMatrix"]["nonBooleanRefused"])
        self.assertTrue(self.report["tipMatrix"]["completeRowAccepted"])

    def test_an_empty_matrix_is_refused(self) -> None:
        self.assertTrue(self.report["tipMatrix"]["emptyMatrixRefused"])

    def test_the_unverifiable_half_names_the_package_that_owes_it(self) -> None:
        """BE20 measured `TIP_CAPABILITIES` against pixels and found a control
        that APPEARS to work and does something else: Rotation Jitter is dead
        on every round tip yet moves 35.7% of pixels on Pencil and 89.1% on
        Scatter Dust, because it consumes `Math.random()` and shifts the stream
        feeding the stipple. So a pixel diff cannot prove the renderer READ the
        value, and this package says so instead of pretending otherwise."""

        self.assertEqual("V2-05 (spec BE5, stamp/sweep coverage)",
                         self.report["tipMatrix"]["honestyRuleNamesItsOwner"])


class ThePresetLeakGuardTests(_Probed):
    """BE14's experiment, pointed at a mechanism instead of at sixteen
    conventions."""

    def test_nothing_leaks_from_a_poisoned_state(self) -> None:
        """EVERY registered setting set to an unusual value, then a preset that
        declares none of them. Anything that survives is a leak."""

        self.assertEqual([], self.report["preset"]["leakedFromPoisonedState"])

    def test_every_non_session_setting_lands_on_its_declared_default(self) -> None:
        """The other half. "Nothing leaked" is also true of a resolver that
        returns nonsense; this says where the values actually came from."""

        self.assertTrue(self.report["preset"]["allNonSessionAtFallback"])

    def test_the_six_that_leaked_in_legacy_are_still_the_guard_s_subject(self) -> None:
        """Pinned in the test as well as in the module, so the subject cannot
        be edited away by editing the module."""

        self.assertEqual(sorted(BE14_LEAKING_FIELDS),
                         self.report["preset"]["leakedInLegacy"])
        for name in BE14_LEAKING_FIELDS:
            with self.subTest(setting=name):
                self.assertIn(name, self.report["preset"]["registered"])

    def test_the_working_size_carries_by_declaration(self) -> None:
        """§14: "Keep Working Size When Switching MUST default on." The
        designed carry-over, which is observationally identical to a leak and
        is distinguished by the declared scope rather than by assumption."""

        self.assertEqual(["size"],
                         self.report["preset"]["sessionCarriedFromPoisonedState"])
        self.assertEqual(["size"], self.report["preset"]["sessionScoped"])

    def test_turning_it_off_stops_even_that_one(self) -> None:
        self.assertEqual([], self.report["preset"]["carriedWithKeepWorkingSizeOff"])

    def test_opacity_and_flow_are_not_session_scoped(self) -> None:
        """§14 names them: they "normally load from the selected preset". If
        they were session-scoped, switching presets would keep the previous
        one's Flow and look exactly like the defect this guard is for."""

        self.assertNotIn("opacity", self.report["preset"]["sessionScoped"])
        self.assertNotIn("flow", self.report["preset"]["sessionScoped"])

    def test_the_sources_are_ordered_as_the_spec_says(self) -> None:
        self.assertEqual(
            ["calibration", "global", "preset", "override", "session"],
            self.report["preset"]["resolutionOrder"])

    def test_each_source_wins_over_the_one_before_it(self) -> None:
        """Asserted by MEASUREMENT rather than by reading the list: a resolver
        that ignored the order would still export the right array."""

        p = self.report["preset"]["precedence"]
        self.assertEqual(0.1, p["calibrationOnly"])
        self.assertEqual(0.2, p["globalOverCalibration"])
        self.assertEqual(0.4, p["presetOverGlobal"])
        self.assertEqual(0.6, p["overrideWinsAll"])

    def test_a_global_reaches_a_setting_the_preset_never_mentions(self) -> None:
        """Complete resolution, not a merge over the preset."""

        self.assertEqual(0.3, self.report["preset"]["precedence"][
            "globalReachesAnUntouchedSetting"])

    def test_the_resolved_settings_are_frozen(self) -> None:
        self.assertTrue(self.report["preset"]["frozen"])


class ItIsBuiltBesideLegacyTests(unittest.TestCase):
    """§19.7: "Build V2 beside Legacy behind an internal development flag", and
    §4: no public Legacy/V2 selector."""

    def test_the_page_does_not_load_it(self) -> None:
        """CHANGED BY U3, recorded rather than quietly relaxed.

        Until U3 this asserted the page did not load the module at all, which
        was the right invariant while V2 had no consumer. U3 gives it one, so
        the module IS loaded -- and the invariant that now carries the weight
        is that the ADAPTER is off by default, which
        `test_u3_canvas_adapter.py` drives rather than spells.

        The acceptance surface is still not shipped."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("v2/brush-contracts.js", html)
        self.assertNotIn("v2/scratchpad.js", html)

    def test_it_renders_nothing_and_touches_no_document(self) -> None:
        """A contracts module that reached for a canvas would be a kernel.

        ON THE CODE, NOT THE TEXT. The module's own comments cite
        `canvas-input.js` and `canvas-core.js` by name, which is exactly what a
        text scan matches -- see `_code_only`."""

        code = _code_only(MODULE.read_text(encoding="utf-8"))
        for banned in ("document.", "getContext", "ImageData",
                       "requestAnimationFrame", "canvas", "fetch(",
                       "localStorage", "XMLHttpRequest"):
            with self.subTest(token=banned):
                self.assertNotIn(banned, code)

    def test_it_loads_with_no_browser_around_it(self) -> None:
        """The behavioural half, and the stronger one. The probe loads this
        module in a `vm` context whose only globals are `window` and `console`,
        then calls every exported function. Anything reaching for a DOM would
        have thrown a ReferenceError before a single assertion ran."""

        self.assertEqual(1, _probe()["schemaVersion"])


class TheCommentStripperWorksTests(unittest.TestCase):
    """`_code_only` is an instrument, so it is calibrated rather than trusted.

    A stripper that returned "" would make every guard above pass.
    """

    def test_a_line_comment_goes_and_the_code_stays(self) -> None:
        # The space before the marker stays: this removes comments, it does not
        # reformat code, and a stripper that also trimmed would be doing two
        # things where one is asserted.
        self.assertEqual("const a = 1; \n",
                         _code_only("const a = 1; // canvas\n"))

    def test_a_block_comment_goes(self) -> None:
        self.assertEqual("const a = 1;",
                         _code_only("/* canvas\n * document.\n */const a = 1;"))

    def test_a_string_containing_a_comment_marker_survives(self) -> None:
        """The case a naive regex gets wrong, and the reason this is a scanner
        rather than a `re.sub`."""

        self.assertEqual('const u = "https://x//y";',
                         _code_only('const u = "https://x//y";'))

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        self.assertEqual('const s = "a\\"// b";',
                         _code_only('const s = "a\\"// b";'))

    def test_it_leaves_the_real_module_s_code_intact(self) -> None:
        """The calibration that matters: on the file it actually guards, the
        exported surface must survive and a comment-only word must not."""

        code = _code_only(MODULE.read_text(encoding="utf-8"))
        self.assertIn("window.StudioBrushV2", code)
        self.assertIn("function resolvePreset", code)
        self.assertIn("RESOLUTION_ORDER", code)
        # Present in the module, only ever inside a comment.
        self.assertIn("canvas-input.js",
                      MODULE.read_text(encoding="utf-8"))
        self.assertNotIn("canvas-input.js", code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_V2_BRUSH_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
