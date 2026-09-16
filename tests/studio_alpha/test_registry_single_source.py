"""One source of truth for what the engine will dispatch.

Found by the owner, live, 2026-08-10: the sampler dropdown showed the real
twenty-one entries after the first generation and then dropped back to two --
"Euler a" and "DPM++ 2M SDE" -- after the next one.

Two fillers were writing the same element:

```text
_loadRegistries      /api/registries    21 samplers, 17 schedulers   REAL
populateDropdowns    /studio/samplers    2 samplers,  2 schedulers   STUB
```

A race decided by whichever ran last, and `populateDropdowns` re-runs on a
model-root save and on several post-generation paths. So the owner watched a
correct list be replaced by a fabricated one by continuing to use the product.

This is the P0.6 defect in its purest form -- a control offering names the
engine will never accept -- and the codebase had ALREADY diagnosed it for the
Hires upscaler dropdown, in a comment sitting three lines below the sampler
filler that still had it. The fix there removed the second filler; the sampler
and scheduler were left behind.

BOTH HALVES ARE ASSERTED HERE, because either alone comes back:

```text
the ROUTES now serve the real registry, so there is no hardcoded list left
  for some other caller to repopulate from
the PAGE has one filler, so a second cannot win a race by running later
```

Removing the filler without fixing the routes would leave a fabricated list
one caller away. Fixing the routes without removing the filler would leave two
writers agreeing today and free to disagree the moment either changes.

SCOPE: MINIMAL_RUNTIME_SCOPE. No engine; the adapter is driven with a
presentation double, and the page is checked as source.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_SINGLE_SOURCE_TESTS = 15

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)
ADAPTER = (APP_ROOT / "forge_studio" / "source_api_adapter.py").read_text(
    encoding="utf-8"
)

#: The exact strings the stub answered with.
#:
#: Note what was wrong with them: NOT that the names are fake -- "Euler a",
#: "DPM++ 2M SDE" and "Karras" are all real engine names. The defect was that
#: they were a hardcoded SUBSET, fixed in source and independent of what the
#: engine actually offers, so the list was wrong by omission on every install
#: and could not follow a model that dispatches something else.
#:
#: So these may legitimately appear in a route's OUTPUT when the engine really
#: offers them. What must never reappear is a literal in the adapter's source,
#: which is what the discipline test below checks.
HARDCODED_IN_THE_STUB = ("DPM++ 2M SDE", "Euler a", "Karras", "Simple")

REAL = {
    "available": True,
    "samplers": ["Euler", "Heun", "DPM++ 2M", "LMS", "er_sde"],
    "schedulers": ["Automatic", "Karras", "Exponential"],
    "latent_upscalers": ["Latent", "Latent (nearest)"],
    "image_upscalers": ["remacri_original", "R-ESRGAN 4x+"],
}

COLD = {
    "available": False,
    "samplers": [],
    "schedulers": [],
    "latent_upscalers": [],
    "image_upscalers": [],
}


class _Presentation:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def registries(self) -> Any:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _adapter(payload: Any) -> SourceFrontendAdapter:
    return SourceFrontendAdapter(_Presentation(payload))


class WarmEngineTests(unittest.TestCase):
    """What the routes answer when the engine is standing."""

    def setUp(self) -> None:
        self.adapter = _adapter(REAL)

    def test_samplers_are_the_engines_own(self) -> None:
        self.assertEqual(
            REAL["samplers"],
            [entry["name"] for entry in self.adapter.get("/studio/samplers")],
        )

    def test_schedulers_are_the_engines_own(self) -> None:
        got = self.adapter.get("/studio/schedulers")
        self.assertEqual(REAL["schedulers"], [entry["name"] for entry in got])

    def test_a_scheduler_carries_a_label_the_page_can_render(self) -> None:
        """The page reads `label` for this select and `name` for the others."""

        got = self.adapter.get("/studio/schedulers")
        self.assertTrue(all(entry["label"] == entry["name"] for entry in got))

    def test_upscalers_are_both_kinds_the_engine_dispatches(self) -> None:
        got = [entry["name"] for entry in self.adapter.get("/studio/upscalers")]
        self.assertEqual(
            REAL["latent_upscalers"] + REAL["image_upscalers"], got
        )

    def test_nothing_is_offered_that_the_registry_did_not_name(self) -> None:
        """The real guarantee: the routes ADD nothing. A hardcoded entry
        merged in beside the engine's own would be invisible in the equality
        checks above if it happened to be appended."""

        registry_names = set(
            REAL["samplers"] + REAL["schedulers"]
            + REAL["latent_upscalers"] + REAL["image_upscalers"]
        )
        offered = {
            entry["name"]
            for route in ("/studio/samplers", "/studio/schedulers",
                          "/studio/upscalers")
            for entry in self.adapter.get(route)
        }
        self.assertEqual(set(), offered - registry_names)


class ColdEngineTests(unittest.TestCase):
    """The other side. Empty, never a plausible-looking list."""

    def test_a_cold_engine_offers_nothing(self) -> None:
        adapter = _adapter(COLD)
        for route in ("/studio/samplers", "/studio/schedulers",
                      "/studio/upscalers"):
            with self.subTest(route=route):
                self.assertEqual([], adapter.get(route))

    def test_a_presentation_that_raises_offers_nothing(self) -> None:
        adapter = _adapter(RuntimeError("no engine"))
        self.assertEqual([], adapter.get("/studio/samplers"))

    def test_a_presentation_without_registries_offers_nothing(self) -> None:
        """Duck-typed seam: doubles predate this method, and a missing
        capability is answered rather than raised."""

        class _Older:
            pass

        self.assertEqual(
            [], SourceFrontendAdapter(_Older()).get("/studio/samplers")
        )

    def test_an_empty_answer_is_not_silently_replaced_by_a_default(self) -> None:
        """A dropdown that receives nothing says "Engine default". A dropdown
        that receives one invented name offers a choice that will be ignored,
        which is strictly worse."""

        self.assertEqual([], _adapter(COLD).get("/studio/samplers"))


class SourceDisciplineTests(unittest.TestCase):
    """Neither half of the fix may quietly return."""

    @staticmethod
    def _code_only(source: str) -> str:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
        return ast.unparse(tree)

    def test_the_adapter_hardcodes_no_engine_name(self) -> None:
        """Checked against code with comments and docstrings stripped: this
        module NAMES the fabricated values in prose to explain what it
        refuses, and a raw search would match the explanation."""

        code = self._code_only(ADAPTER)
        for invented in HARDCODED_IN_THE_STUB:
            self.assertNotIn(invented, code)

    def test_the_page_no_longer_fills_the_sampler_select(self) -> None:
        """The second writer. Its return is what the owner would see."""

        self.assertNotIn("samplerSelect.innerHTML", APP_JS)

    def test_the_page_no_longer_fills_the_scheduler_select(self) -> None:
        self.assertNotIn("schedSelect.innerHTML", APP_JS)

    def test_the_registry_filler_is_still_there(self) -> None:
        """The discriminating half. Deleting BOTH fillers would satisfy every
        assertion above and leave the dropdowns permanently empty."""

        self.assertIn('fill("paramSampler"', APP_JS)
        self.assertIn('fill("paramScheduler"', APP_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_SINGLE_SOURCE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
