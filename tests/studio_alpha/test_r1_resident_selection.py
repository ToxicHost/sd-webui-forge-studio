"""D7 -- resident component state belongs to the model lifecycle.

Owner decision, 2026-08-11: expose desired and resident selection distinctly;
presentation reads resident component state from the lifecycle and must NOT
independently derive it from the desired `ModelSelection`.

The concrete defect this closes: `/studio/current_vae` answered a hardcoded
`{"name": "Automatic"}` whatever was actually loaded. The one control whose
entire job is reporting engine state reported a constant.

Why the derivation ban is the load-bearing half. The owner's choice and the
engine's state are different questions, and they diverge in the cases that
matter -- before the first load, during a deferred switch, and after a failed
one. Answering the second with the first would make the control report a model
that had never been loaded, which is a worse lie than the constant because it
would look right most of the time.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402

EXPECTED_R1_RESIDENT_TESTS = 10

RESIDENT = {
    "checkpoint_model_id": "ckpt-aaa",
    "text_encoder_model_id": "te-bbb",
    "vae_model_id": "vae-ccc",
    "fingerprint": "0123456789abcdef",
}


class _Presentation:
    """Reports residency, and records whether it was asked."""

    def __init__(self, resident=None, *, raises=False, omit=False):
        self._resident = resident
        self._raises = raises
        self.calls = 0
        if omit:
            # A host that does not implement the reader at all -- a mock or a
            # legacy presentation. The adapter must degrade, not fail.
            del self.resident_selection

    def resident_selection(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("lifecycle unavailable")
        return self._resident


class _PresentationWithoutReader:
    """No `resident_selection` at all."""


class CurrentVaeTests(unittest.TestCase):
    def test_it_reports_the_resident_vae(self) -> None:
        presentation = _Presentation(RESIDENT)
        payload = SourceFrontendAdapter(presentation).get("/studio/current_vae")

        self.assertEqual("vae-ccc", payload["model_id"])
        self.assertTrue(payload["resident"])
        self.assertEqual(1, presentation.calls, "it must ASK the lifecycle")

    def test_name_carries_the_model_id_because_that_is_what_the_caller_matches(
        self,
    ) -> None:
        """app.js fills the <select> with `value="${v.model_id || v.name}"`
        and then tests `_optionExists(vaeSelect, current.name)`. A display name
        here would never match an option and would silently do nothing -- the
        same failure this route already had, in a new spelling."""

        payload = SourceFrontendAdapter(_Presentation(RESIDENT)).get(
            "/studio/current_vae"
        )
        self.assertEqual(payload["model_id"], payload["name"])

    def test_nothing_resident_reports_nothing_not_automatic(self) -> None:
        """Null, so the caller's `if (current.name && ...)` leaves the control
        alone. This route is priority 3 behind a stashed value and per-model
        memory; it must not overwrite either with a guess."""

        payload = SourceFrontendAdapter(_Presentation(None)).get(
            "/studio/current_vae"
        )
        self.assertIsNone(payload["name"])
        self.assertIsNone(payload["model_id"])
        self.assertFalse(payload["resident"])

    def test_it_never_answers_the_old_constant(self) -> None:
        for presentation in (
            _Presentation(RESIDENT),
            _Presentation(None),
            _PresentationWithoutReader(),
        ):
            with self.subTest(presentation=type(presentation).__name__):
                payload = SourceFrontendAdapter(presentation).get(
                    "/studio/current_vae"
                )
                self.assertNotEqual("Automatic", payload.get("model_id"))

    def test_a_host_without_the_reader_degrades_rather_than_failing(self) -> None:
        payload = SourceFrontendAdapter(_PresentationWithoutReader()).get(
            "/studio/current_vae"
        )
        self.assertFalse(payload["resident"])

    def test_a_lifecycle_that_raises_does_not_break_the_page(self) -> None:
        """Filling a control is never worth a 500."""

        payload = SourceFrontendAdapter(_Presentation(RESIDENT, raises=True)).get(
            "/studio/current_vae"
        )
        self.assertFalse(payload["resident"])


class LifecycleOwnershipTests(unittest.TestCase):
    def test_the_snapshot_names_residency_as_residency(self) -> None:
        """`profile`/`profile_id` are retained legacy vocabulary. A reader
        cannot tell from those names whether the value is what was ASKED for or
        what is LOADED, and only one of those is answerable there."""

        source = (APP_ROOT / "forge_studio" / "model_lifecycle.py").read_text(
            encoding="utf-8"
        )
        source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
        self.assertIn('"resident_selection"', source)

    def test_presentation_reads_the_lifecycle_and_not_a_desired_selection(
        self,
    ) -> None:
        """The derivation ban, asserted at the seam that would violate it.

        Read through AST, not text. The first version of this test stripped
        `#` comments with a regex and was then satisfied by the DOCSTRING that
        explains why deriving from a desired ModelSelection is forbidden -- the
        assertion passed on the sentence forbidding the thing. That is the
        eighth instance of comments tripping a check in this codebase, and the
        operating handoff says exactly this: use AST for Python negative
        assertions.
        """

        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "presentation.py").read_text(
                encoding="utf-8"
            )
        )
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resident_selection":
                target = node
                break
        self.assertIsNotNone(target, "resident_selection() is gone")

        body = list(target.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # drop the docstring
        code = " ".join(ast.dump(statement) for statement in body)

        self.assertIn("resident_selection", code, "it must read that key")
        self.assertNotIn("ModelSelection", code)
        self.assertNotIn("last_model_selection", code)
        self.assertNotIn("desired", code)

    def test_no_pending_field_was_added_that_could_never_populate(self) -> None:
        """`_pending_profile` lives on WarmSessionManager, not on the class that
        builds the snapshot. A `getattr` default there would have produced a
        field that is permanently None -- present in the payload, never
        populated, indistinguishable from 'no switch pending'."""

        source = (APP_ROOT / "forge_studio" / "model_lifecycle.py").read_text(
            encoding="utf-8"
        )
        stripped = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
        self.assertNotIn('"pending_selection"', stripped)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_R1_RESIDENT_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
