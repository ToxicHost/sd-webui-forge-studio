"""The body the PAGE actually sends, asserted as source.

Every defect this file guards was invisible to the server tests, because the
server was always correct about the body it received. The question these ask
is the other one: does the page send what the owner chose?

Found live 2026-08-10, by switching model in the UI:

```text
09:58:04  job 6  checkpoint=08ae73d8   (a switch)
09:58:11  FAILED  INVALID_GENERATION_REQUEST  "Select an available Studio model."
09:58:13  job 7  checkpoint=21f71e0a   (back to the one that just worked)
09:58:15  FAILED  same
09:58:23  job 8  checkpoint=21f71e0a   -> succeeded
```

The page sent `model: lifecycle.loadedModelId()`, and that accessor returns
`state.model.profile_id` -- a profile FINGERPRINT like "e1fd6bc2d2eb935e",
not a catalogue model id. `application._validate_request` checks that field
against real ids, so it only ever passed because the selection resolver
overwrote `model_id` first. On a switch it does not, the fingerprint survives,
and the job is refused -- taking the following job with it.

The canonical body carries identity in `model_selection`; `model` may be ""
whenever that is present, and requiring both was itself a defect fixed in
P0.2. So "" is the correct value and the fingerprint bought nothing.

WHY THIS IS ASSERTED AS SOURCE

There is no other place to assert it. The payload is built in a browser, and
the server cannot tell a body that omitted a control from a body whose owner
left that control alone. A source assertion is a poor instrument, but it is
the only one that fails when a field silently stops being sent.

SCOPE: MINIMAL_RUNTIME_SCOPE. No browser, no server; app.js is read as text.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_PAYLOAD_TESTS = 11

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)


def _job_params() -> str:
    """The `jobParams` literal, from `const` to the `submitGenerate` call.

    Sliced rather than parsed: this is JavaScript, and the alternative is a JS
    parser in a Python suite. The slice is anchored on both ends, so a rename
    or a move fails loudly here instead of quietly matching nothing.
    """

    start = APP_JS.find("const jobParams = {")
    assert start != -1, "the generate payload literal was renamed or removed"
    end = APP_JS.find("lifecycle.submitGenerate(jobParams)", start)
    assert end != -1, "the payload is no longer submitted through submitGenerate"
    return APP_JS[start:end]


def _code_only(source: str) -> str:
    """`//` comments stripped.

    The payload literal DESCRIBES what it refuses to send, by name -- so a
    raw search finds `loadedModelId()` inside the comment explaining why
    `loadedModelId()` is not used. That is the trap OPERATIONS.md section 7
    records, and this suite hit it on its first run, which is the seventh time
    in this project.

    Line comments only. The slice contains no block comments and no `//`
    inside a string literal; a parser would be the honest answer if it ever
    does, and this assertion failing loudly is how that would be discovered.
    """

    return "\n".join(
        re.sub(r"//.*$", "", line) for line in source.splitlines()
    )


class ModelIdentityTests(unittest.TestCase):
    def test_the_model_field_is_empty(self) -> None:
        """Identity travels in `model_selection`. Anything else in `model` is
        a second spelling of the same fact, free to disagree with it."""

        self.assertRegex(_code_only(_job_params()), r'model:\s*""')

    def test_the_profile_fingerprint_is_not_sent_as_a_model_id(self) -> None:
        """The defect itself: that accessor returns a profile fingerprint.

        Asserted against CODE, not raw source -- the payload's own comment
        names the accessor in order to explain why it is not used."""

        self.assertNotIn("loadedModelId()", _code_only(_job_params()))

    def test_the_three_role_ids_are_sent(self) -> None:
        params = _job_params()
        for role in (
            "checkpoint_model_id",
            "text_encoder_model_id",
            "vae_model_id",
        ):
            with self.subTest(role=role):
                self.assertIn(role, APP_JS)
        self.assertIn("model_selection", params)

    def test_model_selection_is_a_sibling_of_generation(self) -> None:
        """The book's shape. `model_selection` inside `generation` is one of
        the four bodies the server refuses outright."""

        params = _job_params()
        selection_at = params.find("model_selection:")
        generation_at = params.find("generation: {")
        self.assertNotEqual(-1, selection_at)
        self.assertNotEqual(-1, generation_at)
        self.assertLess(selection_at, generation_at)


class CarriedControlTests(unittest.TestCase):
    """Controls the owner can see must reach the request."""

    def test_the_sampler_is_read_from_the_control(self) -> None:
        self.assertIn('getElementById("paramSampler")', _job_params())

    def test_the_scheduler_is_read_from_the_control(self) -> None:
        self.assertIn('getElementById("paramScheduler")', _job_params())

    def test_the_live_preview_toggle_is_carried(self) -> None:
        """Confirmed reaching the backend live: every DISPATCH line in the
        owner's console reads `preview=on`."""

        self.assertRegex(_job_params(), r"preview_enabled:\s*!!State\.livePreview")

    def test_preview_enabled_sits_inside_generation(self) -> None:
        """A request field, not a sibling. At the top level it is refused as
        an unknown key rather than honoured."""

        params = _job_params()
        self.assertLess(
            params.find("generation: {"), params.find("preview_enabled:")
        )

    def test_hires_is_sent_only_when_switched_on(self) -> None:
        """Absent is not disabled: with no `hires` key the server sets no
        hr_* field at all, so the base pass stays byte-identical."""

        self.assertRegex(
            _job_params(), r"\.\.\.\(_hiresGroup\(\)\s*\?\s*\{\s*hires:"
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_PAYLOAD_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
