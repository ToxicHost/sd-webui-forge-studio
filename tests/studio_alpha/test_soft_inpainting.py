"""Soft Inpainting, from a dead checkbox to a cleared latent-mask flag. WP1.6.

THE DEFECT THESE CATCH

The owner reported rough inpaint edges immediately after inpaint started
working at all (`eea8bc53`). The cause was not the mask, the blur, or the
clip-to-mask composite: `processing.py:1892` rounds the LATENT mask to 0 or 1
by default, and a latent cell covers an 8x8 pixel block, so the seam is a
staircase at 8px pitch. Soft Inpainting clears that flag and blends the latent
gradually instead.

The page has carried the whole group -- a checkbox and six sliders, with the
engine's exact defaults -- since before the standalone existed. Only the LEGACY
collector read it (app.js:3003-3009); the lifecycle body that actually submits
sent four inpaint fields, and `rg soft_inpaint` across the Studio backend
returned nothing. Switching it on changed nothing.

Review: `Evidence/source-review/WP1.6-soft-inpainting.md`.

WHAT IS DELIBERATELY NOT TESTED HERE

Neo's blending mathematics. `latent_blend`, `get_modified_nmask`, `apply_masks`
and `apply_adaptive_masks` are called, not reimplemented, and asserting on their
output would pin someone else's algorithm and break on their next release. What
is tested is WHETHER they are reached, with WHICH numbers, and on which jobs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import (  # noqa: E402
    InpaintSettings,
    SoftInpainting,
)


def refusal():
    """The refusal class, resolved at CALL time. Deliberately not an import.

    `test_import_boundaries` deletes every `forge_studio.*` entry from
    `sys.modules` and re-imports the package fresh, to prove it drags in
    neither PIL nor torch. That is a good test, and it means any module which
    binds `PresentationError` at import time holds a class object the running
    code no longer raises -- `assertRaises` then stops matching and every
    refusal test errors with the correct exception in the traceback.

    It bites HERE and not in `test_aspect_randomizer`, which binds the same
    class at module level, purely because discovery is alphabetical:
    `test_a...` runs before `test_i...` and `test_s...` runs after. A test that
    passes because of its own filename is not passing for a reason.

    A fresh `import` inside the function re-reads `sys.modules` every call, so
    this always returns the class the code will actually raise.
    """

    import forge_studio.presentation

    return forge_studio.presentation.PresentationError

EXPECTED_SOFT_INPAINTING_TESTS = 24

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8")
INDEX_HTML = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8")

ENGINE_SOURCE = (APP_ROOT / "extensions-builtin" / "soft-inpainting"
                 / "scripts" / "soft_inpainting.py")


def png_data_url(width: int, height: int, colour) -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return ("data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode("ascii"))


def admitted(**inpaint):
    """An inpaint request through the REAL validator."""

    from forge_studio.contracts import GenerationRequest
    from forge_studio.presentation import _validated_request_payload

    settings = {"mask_blur": 4, "fill": 1, "full_resolution": False,
                "padding": 32, "invert": False}
    settings.update(inpaint)
    payload = _validated_request_payload({"model": "m", "generation": {
        "positive_prompt": "a coat", "negative_prompt": "", "seed": 7,
        "steps": 4, "cfg_scale": 4.0, "width": 64, "height": 64,
        "operation": "inpaint",
        "source_image": png_data_url(64, 64, (120, 40, 40)),
        "mask": png_data_url(64, 64, (255, 255, 255)),
        "inpaint": settings}})
    return GenerationRequest(**payload)


class AdmissionTests(unittest.TestCase):
    def test_the_group_reaches_the_request(self) -> None:
        request = admitted(soft={"preservation": 0.7})
        self.assertIsNotNone(request.inpaint.soft)
        self.assertEqual(0.7, request.inpaint.soft.preservation)

    def test_absent_means_absent(self) -> None:
        """Catches: six defaults being asserted over the engine's own edge
        behaviour on a job whose owner never switched the feature on."""

        self.assertIsNone(admitted().inpaint.soft)

    def test_an_omitted_field_keeps_its_default(self) -> None:
        soft = admitted(soft={"preservation": 0.7}).inpaint.soft
        self.assertEqual(1.0, soft.schedule_bias)
        self.assertEqual(4.0, soft.transition_contrast)

    def test_a_value_outside_the_engines_slider_is_refused_by_name(self) -> None:
        """Catches: a number the engine would clamp or misread being accepted
        silently, so the owner's setting and the engine's behaviour differ."""

        for field, bad in (("schedule_bias", 8.5), ("preservation", -0.1),
                           ("transition_contrast", 0.5),
                           ("transition_contrast", 33.0),
                           ("mask_influence", 1.5), ("diff_threshold", 9.0),
                           ("diff_contrast", -1.0)):
            with self.subTest(field=field, value=bad):
                with self.assertRaises(refusal()) as raised:
                    admitted(soft={field: bad})
                self.assertIn(field, str(raised.exception))

    def test_the_boundaries_themselves_are_accepted(self) -> None:
        soft = admitted(soft={"schedule_bias": 0.0, "preservation": 8.0,
                              "transition_contrast": 1.0,
                              "mask_influence": 1.0}).inpaint.soft
        self.assertEqual(0.0, soft.schedule_bias)
        self.assertEqual(8.0, soft.preservation)

    def test_a_boolean_is_not_a_slider(self) -> None:
        """`True` is an int in Python; a checkbox arriving where a slider
        belongs is a page defect worth naming, not reading as 1.0."""

        with self.assertRaises(refusal()):
            admitted(soft={"preservation": True})

    def test_an_unknown_field_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            admitted(soft={"nonsense": 1.0})

    def test_a_non_object_group_is_refused(self) -> None:
        with self.assertRaises(refusal()):
            admitted(soft=[1, 2, 3])

    def test_an_integer_is_accepted_as_a_number(self) -> None:
        """The page sends 1 and 2 for sliders whose step is fractional."""

        self.assertEqual(2.0, admitted(soft={"diff_contrast": 2}).inpaint.soft
                         .diff_contrast)


class EngineParityTests(unittest.TestCase):
    """Studio's defaults are the ENGINE'S, and this reads the engine to say so.

    Catches: an engine update moving `default` while Studio quietly keeps the
    old six numbers -- which would look like nothing at all until an owner
    compared output against the Extension.
    """

    def test_the_contract_defaults_match_the_engines_own(self) -> None:
        import ast

        tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"))
        found = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", "") == "default"
                            for t in node.targets)
                    and isinstance(node.value, ast.Call)):
                found = [ast.literal_eval(arg) for arg in node.value.args]
                break
        self.assertIsNotNone(found, "engine `default` assignment not found")

        studio = SoftInpainting()
        self.assertEqual(
            [studio.schedule_bias, studio.preservation,
             studio.transition_contrast, studio.mask_influence,
             studio.diff_threshold, studio.diff_contrast],
            [float(value) for value in found])

    def test_the_field_order_matches_the_engines(self) -> None:
        """The bridge builds the engine's settings POSITIONALLY, so an order
        that drifts would silently swap two sliders."""

        import ast

        tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"))
        fields = [node.target.id
                  for node in ast.walk(tree)
                  if isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name)
                  and node.target.id.startswith(
                      ("mask_blend", "inpaint_detail", "composite_"))]
        self.assertEqual(
            ["mask_blend_power", "mask_blend_scale",
             "inpaint_detail_preservation", "composite_mask_influence",
             "composite_difference_threshold", "composite_difference_contrast"],
            fields[:6])


class BridgeRoutingTests(unittest.TestCase):
    """When the bridge is installed at all. No engine needed to answer this."""

    def _bridge(self, *, inpaint, is_inpaint):
        import types

        from forge_headless.live_generation_port import _soft_inpainting_bridge

        return _soft_inpainting_bridge(
            types.SimpleNamespace(inpaint=inpaint), is_inpaint)

    def test_an_inpaint_job_with_the_group_gets_a_bridge(self) -> None:
        bridge = self._bridge(
            inpaint=InpaintSettings(soft=SoftInpainting()), is_inpaint=True)
        self.assertIsNotNone(bridge)

    def test_an_inpaint_job_without_the_group_keeps_none(self) -> None:
        """Catches: WP1.6 changing a job whose owner did not ask for it."""

        self.assertIsNone(
            self._bridge(inpaint=InpaintSettings(), is_inpaint=True))

    def test_a_non_inpaint_job_never_gets_one(self) -> None:
        """Catches: arming a latent-mask blend on a job that has no mask."""

        self.assertIsNone(self._bridge(
            inpaint=InpaintSettings(soft=SoftInpainting()), is_inpaint=False))
        self.assertIsNone(self._bridge(inpaint=None, is_inpaint=False))


class BridgeSafetyTests(unittest.TestCase):
    """A non-None `scripts` makes 36 engine branches execute. They must no-op."""

    def setUp(self) -> None:
        from forge_headless.soft_inpainting_bridge import SoftInpaintingBridge

        self.bridge = SoftInpaintingBridge(SoftInpainting())

    def test_every_unimplemented_hook_does_nothing(self) -> None:
        for hook in ("before_process", "before_process_batch", "process_batch",
                     "postprocess", "postprocess_batch",
                     "postprocess_batch_list", "postprocess_image",
                     "postprocess_image_after_composite", "before_hr",
                     "process_before_every_sampling", "setup_scripts",
                     "before_process_init_images"):
            with self.subTest(hook=hook):
                self.assertIsNone(getattr(self.bridge, hook)(object()))

    def test_a_hook_the_engine_adds_later_also_no_ops(self) -> None:
        """Catches: a future engine release calling a method a hand-written
        list of stubs does not have, raising mid-generation where
        `scripts = None` would simply have skipped it."""

        self.assertIsNone(self.bridge.some_hook_added_in_2027(1, 2, x=3))

    def test_dunders_are_refused(self) -> None:
        """Catches: the bridge answering "yes, and it returns None" to
        `hasattr(x, "__iter__")` and friends, which is how an object starts
        lying about what it is."""

        # NOT `__getstate__`: `object` really does define it from 3.11, so
        # normal lookup finds it and `__getattr__` is never consulted. Asking
        # this test to refuse it would be asserting the bridge should shadow a
        # method it correctly inherits.
        for name in ("__iter__", "__len__", "__deepcopy__", "__await__"):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    getattr(self.bridge, name)


class CollectorTests(unittest.TestCase):
    def test_the_lifecycle_body_sends_the_group(self) -> None:
        """The mutation guard. Severing the collector fails here.

        Asserted against the LIFECYCLE body specifically -- the legacy
        collector read these controls all along, and that is exactly why the
        feature looked wired and was not.
        """

        self.assertIn("_softInpaintGroup", APP_JS)
        self.assertIn("{ soft: _softInpaintGroup() }", APP_JS)

    def test_the_collector_reads_every_control_the_page_shows(self) -> None:
        """BOTH SIDES of the join, deliberately.

        A collector naming an id the page does not have reads `undefined`
        forever and the feature stays dead while this file looks green -- which
        is exactly how `supported_operations` hid a broken inpaint path. So the
        control must exist in the DOM and be read by the collector.
        """

        for control in ("checkSoftInpaint", "paramSoftBias",
                        "paramSoftPreserve", "paramSoftContrast",
                        "paramSoftMaskInf", "paramSoftDiffThresh",
                        "paramSoftDiffContrast"):
            with self.subTest(control=control):
                self.assertIn(f'id="{control}"', INDEX_HTML)
                self.assertIn(control, APP_JS)

    def test_the_inpaint_fallbacks_agree_with_the_contract(self) -> None:
        """Catches the defect found while wiring this: `|| 0` fallbacks that
        contradicted the documented defaults, so a renamed element id would
        have sent no feather and a flat fill instead of 4 and 1."""

        defaults = InpaintSettings()
        for control, value in (("paramMaskBlur", defaults.mask_blur),
                               ("paramPadding", defaults.padding),
                               ("paramFill", defaults.fill)):
            with self.subTest(control=control):
                self.assertIn(f'_num("{control}", {value})', APP_JS)


class TranslationSeamTests(unittest.TestCase):
    """The boundary that actually broke, on live hardware.

    THE DEFECT THESE CATCH: admission carried the group and the port read it
    correctly, and the feature still did nothing, because `translate_request`
    copies `InpaintOptions` field by field and WP1.6 did not add `soft` to that
    copy. The port therefore received None from the headless request and never
    installed the bridge. Two live jobs differing only in this setting came
    back byte-identical.

    Both sides were tested. The join was not. So these tests start at an
    admitted request and end at the object the port actually receives.
    """

    def _headless(self, **inpaint):
        from forge_headless.studio_generation import translate_request

        return translate_request(admitted(**inpaint),
                                 request_id="rid").headless_request

    def test_every_field_survives_translation(self) -> None:
        """Field by field, by NAME, against the contract itself.

        Not a spot-check of one value: a copy that drops a field is the failure
        mode here, so the assertion enumerates the contract rather than a list
        someone has to remember to extend.
        """

        import dataclasses

        sent = {name.name: round(0.25 + index * 0.5, 2)
                for index, name in enumerate(dataclasses.fields(SoftInpainting))}
        # Keep each inside the engine's slider range.
        sent["transition_contrast"] = 6.0
        sent["mask_influence"] = 0.75
        arrived = self._headless(soft=sent).inpaint.soft
        self.assertIsNotNone(arrived, "the whole group was dropped")
        for name, value in sent.items():
            with self.subTest(field=name):
                self.assertEqual(value, getattr(arrived, name))

    def test_the_port_installs_the_bridge_from_the_translated_request(self) -> None:
        """The exact call the port makes, on the exact object it receives."""

        from forge_headless.live_generation_port import _soft_inpainting_bridge

        headless = self._headless(soft={"preservation": 0.7})
        self.assertIsNotNone(_soft_inpainting_bridge(headless, True))

    def test_no_group_still_means_no_bridge_after_translation(self) -> None:
        from forge_headless.live_generation_port import _soft_inpainting_bridge

        headless = self._headless()
        self.assertIsNone(headless.inpaint.soft)
        self.assertIsNone(_soft_inpainting_bridge(headless, True))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_SOFT_INPAINTING_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
