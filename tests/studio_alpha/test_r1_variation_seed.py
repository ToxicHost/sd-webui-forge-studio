"""R1 stretch -- variation seed, carried end to end.

Four controls shipped in the Extra panel, remembered their values, and reached
nothing: the lifecycle payload carried no variation of any kind while the legacy
payload read all four. `live_generation_port` pinned `subseed=-1` at
construction.

This is the only control group in the product that was genuinely one payload
field away from working. Everything else that looked like it -- batch, denoise,
LoRA -- needs a subsystem or an owner decision first.

The acceptance criterion, and the reason `_variation_fields` returns an EMPTY
dict rather than a disabled object: a request without a `variation` group must
produce the byte-identical image it did before this existed. Hires and Auto
Detail were each held to the same rule.

NOT PROVEN LIVE. No GPU leg ran for this. Classified VERIFIED-CODE /
CONTRACT-TESTED, LIVE UNVERIFIED.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.studio_generation import (  # noqa: E402
    STUDIO_REQUEST_FIELDS,
    translate_request,
)
from forge_studio.contracts import GenerationRequest  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_variation,
)

EXPECTED_R1_VARIATION_TESTS = 18

FRONTEND = APP_ROOT / "forge_studio" / "frontend"


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


APP_JS = _strip_js_comments((FRONTEND / "app.js").read_text(encoding="utf-8"))
WORKFLOW_JS = _strip_js_comments(
    (FRONTEND / "workflow-state.js").read_text(encoding="utf-8")
)


def _request(**overrides):
    fields = {
        "model_id": "model-token",
        "positive_prompt": "p",
        "negative_prompt": "",
        "seed": 7,
        "steps": 4,
        "cfg_scale": 5.0,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


class AbsenceTests(unittest.TestCase):
    def test_no_variation_group_sets_no_subseed(self) -> None:
        """Absent must be indistinguishable from absent."""

        headless = translate_request(_request(), request_id="rid").headless_request
        self.assertEqual(-1, headless.subseed)
        self.assertEqual(0.0, headless.subseed_strength)
        self.assertEqual(-1, headless.seed_resize_from_w)
        self.assertEqual(-1, headless.seed_resize_from_h)

    def test_the_validator_returns_none_rather_than_a_disabled_object(self) -> None:
        self.assertIsNone(_validated_variation(None))


class CarryTests(unittest.TestCase):
    def test_every_field_reaches_the_headless_request(self) -> None:
        variation = _validated_variation(
            {
                "subseed": 42,
                "subseed_strength": 0.35,
                "seed_resize_from_w": 512,
                "seed_resize_from_h": 512,
            }
        )
        headless = translate_request(
            _request(variation=variation), request_id="rid"
        ).headless_request
        self.assertEqual(42, headless.subseed)
        self.assertEqual(0.35, headless.subseed_strength)
        self.assertEqual(512, headless.seed_resize_from_w)
        self.assertEqual(512, headless.seed_resize_from_h)

    def test_the_backend_advertises_that_it_can_vary_this(self) -> None:
        """The same correction `auto_detail` needed, for the same reason."""

        self.assertIn("variation", STUDIO_REQUEST_FIELDS)


class ValidationTests(unittest.TestCase):
    def test_an_unknown_field_is_refused_by_name(self) -> None:
        with self.assertRaises(PresentationError) as raised:
            _validated_variation({"subseed": 1, "nonsense": 2})
        self.assertIn("variation.nonsense", str(raised.exception))

    def test_a_large_subseed_is_accepted(self) -> None:
        """Inverted 2026-08-20, AR6.2. There is no 32-bit seed space: the
        core's seed control carries no bounds, `torch.manual_seed` takes 64
        bits, and Philox wraps with `uint32(key)` rather than failing."""

        self.assertEqual(4294967296,
                         _validated_variation({"subseed": 4294967296}).subseed)

    def test_a_subseed_below_minus_one_is_still_refused(self) -> None:
        """The floor survives. -1 means "engine draws"; -2 means nothing."""

        with self.assertRaises(PresentationError):
            _validated_variation({"subseed": -2})

    def test_minus_one_remains_a_legal_subseed(self) -> None:
        """Unlike `seed`, which must be explicit, -1 here means 'engine draws'."""

        self.assertEqual(-1, _validated_variation({"subseed": -1}).subseed)

    def test_a_strength_outside_zero_to_one_is_refused(self) -> None:
        for bad in (-0.1, 1.1):
            with self.subTest(strength=bad), self.assertRaises(PresentationError):
                _validated_variation({"subseed_strength": bad})

    def test_zero_disables_resize_without_being_an_error(self) -> None:
        """Neo gates the pair on `<= 0`, so 0 is 'unset', not invalid.

        The page sends 0 and Neo's own default is -1; both mean the same thing
        and neither may be refused.
        """

        settings = _validated_variation(
            {"seed_resize_from_w": 0, "seed_resize_from_h": 0}
        )
        self.assertEqual(0, settings.seed_resize_from_w)
        self.assertEqual(0, settings.seed_resize_from_h)

    def test_a_nonsense_resize_dimension_is_refused(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_variation({"seed_resize_from_w": 7})

    def test_a_boolean_is_not_an_integer(self) -> None:
        """`True` is an int in Python and would otherwise pass silently."""

        with self.assertRaises(PresentationError):
            _validated_variation({"subseed": True})


class FrontendTests(unittest.TestCase):
    def test_the_group_is_omitted_when_it_would_change_nothing(self) -> None:
        """Neo records no variation when strength is 0.

        Sending a subseed with no strength would put a field in the job record
        that the engine discards -- reporting a choice that had no effect.
        """

        group = APP_JS.split("const _variationGroup = ()", 1)[1]
        group = group.split("const _hiresGroup", 1)[0]
        self.assertIn('document.getElementById("checkExtra")?.checked', group)
        self.assertIn("if (strength <= 0 && !resizing) return null;", group)

    def test_resize_needs_both_dimensions(self) -> None:
        group = APP_JS.split("const _variationGroup = ()", 1)[1]
        group = group.split("const _hiresGroup", 1)[0]
        self.assertIn("const resizing = w > 0 && h > 0;", group)

    def test_the_lifecycle_payload_sends_the_group(self) -> None:
        self.assertIn(
            "...(_variationGroup() ? { variation: _variationGroup() } : {})", APP_JS
        )

    def test_workflows_persist_both_resize_dimensions(self) -> None:
        """The asymmetry that would have restored half a configuration."""

        self.assertIn("resize_seed_w:", WORKFLOW_JS)
        self.assertIn("resize_seed_h:", WORKFLOW_JS)


class AutoDetailBoundaryTests(unittest.TestCase):
    def test_the_detail_pass_does_not_inherit_the_variation(self) -> None:
        """Deliberate, and asserted so it cannot drift silently.

        Whether an owner's variation seed should also perturb each detected
        region's inpaint is a product question -- the book gives Auto Detail
        slots their own settings. Carrying it there by accident would answer
        that question without anyone deciding it.
        """

        source = (
            APP_ROOT / "forge_headless" / "live_generation_port.py"
        ).read_text(encoding="utf-8")
        source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
        # The base pass reads the request; the detail pass keeps the constant.
        self.assertIn('subseed=int(getattr(request, "subseed", -1))', source)
        self.assertIn("subseed=-1", source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_R1_VARIATION_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
