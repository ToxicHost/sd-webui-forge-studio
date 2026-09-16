"""A refused generation must say WHAT was wrong, not that something was.

Found live 2026-08-10, against the 40-step cap of the time. That cap was
REMOVED on 2026-08-20 by owner ruling and steps now has no ceiling, so this
suite refuses with the FLOOR instead -- the subject was never the cap, it was
the QUALITY of the refusal. The original defect: a request for 60 steps was
correctly refused, and reached the owner as:

```text
{"code": "GENERATION_FAILED", "message": "60"}
```

Both halves of that are wrong, and both had a correct answer sitting in the
same process at the same moment.

```text
THE CODE     `poll` already preferred `record.failure` -- which carries
             `studio_code_for(exc.code)` AND `_CODE_FIELD`'s field name --
             but only `if event.error is None`. `project_progress` builds a
             non-None error for EVERY failed job, hardcoded to
             GENERATION_FAILED because all it can see is a HeadlessProgress,
             whose `error` is the bare string `mark_failed` stored. So the
             branch was unreachable for every failure there has ever been.

THE MESSAGE  `_problem("GENERATION_STEPS_OUT_OF_RANGE", str(request.steps))`
             made the offending VALUE the whole message. An owner was
             answered with the number they had just typed, and the bound the
             product enforces was never named.
```

WHY NO EXISTING TEST CAUGHT IT
==============================

The suites assert that a bad request FAILS, and it did. `test_hires_pass`
asserts the OOM conversion maps onto GENERATION_FAILED -- and GENERATION_FAILED
is what everything produced, so that assertion passed for the wrong reason.
Nothing asserted that two different refusals produce two different codes.

That is what this file adds, and it is why the discriminating tests below
compare refusals AGAINST EACH OTHER rather than against a constant: a
regression that reverts to one generic code would satisfy any single-code
assertion written in isolation.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU; the gateway runs
against the recording port and the validator is called directly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.generation_port import (  # noqa: E402
    GenerationGateway,
    RecordingGenerator,
)
from forge_headless.generation_request import (  # noqa: E402
    FirstImageRequest,
    ResidentModel,
    validate_request,
)
from forge_headless.studio_generation import (  # noqa: E402
    GENERATION_FAILED,
    REQUEST_UNSUPPORTED,
    HeadlessGenerationSession,
)
from forge_studio.contracts import GenerationRequest  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_REFUSAL_TESTS = 18

MODEL_ID = "headless-session-model"


def _request(**overrides: Any) -> GenerationRequest:
    fields: dict[str, Any] = {
        "model_id": MODEL_ID,
        "positive_prompt": "a contained synthetic subject",
        "negative_prompt": "",
        "seed": 123456789,
        "steps": 4,
        "cfg_scale": 6.0,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)


def _headless(**overrides: Any) -> FirstImageRequest:
    fields: dict[str, Any] = {
        "request_id": "rid",
        "model_id": MODEL_ID,
        "positive_prompt": "a contained synthetic subject",
        "negative_prompt": "",
        "seed": 1,
        "steps": 4,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return FirstImageRequest(**fields)


def _problems(**overrides: Any) -> list[dict[str, str]]:
    return validate_request(
        _headless(**overrides),
        ResidentModel(model_id=MODEL_ID, family="anima", resident=True),
        options_available=True,
        progress_installed=True,
        result_root_writable=True,
        generation_authorized=True,
    ).problems


def _detail(code: str, **overrides: Any) -> str:
    for problem in _problems(**overrides):
        if problem["code"] == code:
            return problem["detail"]
    raise AssertionError(f"{code} was not reported for {overrides!r}")


def _codes(**overrides: Any) -> set[str]:
    return {problem["code"] for problem in _problems(**overrides)}


# ------------------------------------------------------- what the owner reads


class RefusedJobReportingTests(unittest.TestCase):
    """The code and field that reach `/api/jobs/<id>`."""

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / "_refusal_tmp"
        self.root.mkdir(exist_ok=True)
        self.addCleanup(self._clean)

    def _clean(self) -> None:
        for child in self.root.glob("*"):
            child.unlink()
        self.root.rmdir()

    def _refuse(self, **overrides: Any):
        session = HeadlessGenerationSession(
            gateway=GenerationGateway(RecordingGenerator()),
            resident_model=ResidentModel(
                model_id=MODEL_ID, family="anima", resident=True
            ),
            result_root=self.root,
        )
        identity = session.submit(_request(**overrides))
        return session.poll(identity.job_id)

    def test_too_many_steps_is_reported_as_an_unsupported_request(self) -> None:
        """The live defect. This read GENERATION_FAILED."""

        event = self._refuse(steps=TOO_FEW_STEPS)
        self.assertEqual(REQUEST_UNSUPPORTED, event.error.code)

    def test_too_many_steps_names_the_field(self) -> None:
        """Without the field, a client cannot highlight the control that is
        wrong, which is the whole reason `_CODE_FIELD` exists."""

        event = self._refuse(steps=TOO_FEW_STEPS)
        self.assertEqual("steps", event.error.field)

    def test_the_message_is_not_merely_the_offending_value(self) -> None:
        event = self._refuse(steps=TOO_FEW_STEPS)
        self.assertNotEqual(str(TOO_FEW_STEPS), event.error.message)

    def test_the_message_names_the_bound_that_was_broken(self) -> None:
        event = self._refuse(steps=TOO_FEW_STEPS)
        self.assertIn(str(STEPS_FLOOR), event.error.message)

    def test_two_different_refusals_do_not_collapse_onto_one_code(self) -> None:
        """The discriminating test. Any single-code assertion above would
        also pass against the defect if the constant happened to match, so
        this compares refusals with each other."""

        steps = self._refuse(steps=TOO_FEW_STEPS)
        # An UNUSABLE dimension, not a misaligned one: the alignment refusal
        # was removed 2026-08-20 and 770 is now perfectly acceptable. And not
        # a negative seed either -- a random seed is refused earlier, in
        # `translate_request`, and raises out of `submit` rather than becoming
        # a job record. Two refusals have to travel the SAME path for their
        # reports to be comparable.
        width = self._refuse(width=0)
        self.assertEqual("steps", steps.error.field)
        self.assertEqual("width", width.error.field)
        self.assertNotEqual(steps.error.message, width.error.message)

    def test_an_unusable_dimension_reports_its_own_field(self) -> None:
        event = self._refuse(width=0)
        self.assertEqual(REQUEST_UNSUPPORTED, event.error.code)
        self.assertEqual("width", event.error.field)

    def test_a_refused_job_still_reaches_a_failed_state(self) -> None:
        """The behaviour that was already right stays right: reporting the
        reason better must not change whether the job fails."""

        event = self._refuse(steps=TOO_FEW_STEPS)
        self.assertEqual("failed", event.state.value)

    def test_a_generic_backend_failure_is_still_generic(self) -> None:
        """The other side. `record.failure` now always wins, so a failure
        with no more specific code must not acquire one -- otherwise the fix
        would be relabelling everything rather than reporting truthfully."""

        session = HeadlessGenerationSession(
            gateway=GenerationGateway(RecordingGenerator(fail_with="the card fell over")),
            resident_model=ResidentModel(
                model_id=MODEL_ID, family="anima", resident=True
            ),
            result_root=self.root,
        )
        identity = session.submit(_request())
        event = session.poll(identity.job_id)
        self.assertEqual(GENERATION_FAILED, event.error.code)

    def test_the_bound_itself_is_not_refused(self) -> None:
        """The floor value itself is ALLOWED. A fix that tightened the bound
        instead of reporting it would pass every test above.

        Asserted on the CODE, not on success: `RecordingGenerator` computes
        no pixels, so this job fails at publication either way. What must not
        happen is that it fails as a bad request.
        """

        event = self._refuse(steps=STEPS_FLOOR)
        self.assertNotEqual(REQUEST_UNSUPPORTED, event.error.code)


# ------------------------------------------------- what the validator reports


#: The steps value this suite refuses with, and the bound it breaks.
TOO_FEW_STEPS = 0
STEPS_FLOOR = 1


class ProblemDetailTests(unittest.TestCase):
    """Every range refusal names its value AND its bound."""

    def test_steps_names_both_ends_of_the_range(self) -> None:
        detail = _detail("GENERATION_STEPS_OUT_OF_RANGE", steps=TOO_FEW_STEPS)
        self.assertIn(str(TOO_FEW_STEPS), detail)
        self.assertIn(str(STEPS_FLOOR), detail)

    def test_steps_is_accepted_at_the_bound(self) -> None:
        """The discriminating half of the range itself."""

        self.assertNotIn("GENERATION_STEPS_OUT_OF_RANGE", _codes(steps=STEPS_FLOOR))

    def test_steps_is_refused_past_the_bound(self) -> None:
        self.assertIn("GENERATION_STEPS_OUT_OF_RANGE", _codes(steps=TOO_FEW_STEPS))

    def test_a_dimension_refusal_names_the_value(self) -> None:
        """There is no permitted RANGE any more -- owner decision, no ceiling.

        What remains is a validity check, so the detail names the value that
        was not usable rather than a window it fell outside.
        """

        detail = _detail("GENERATION_DIMENSION_OUT_OF_RANGE", width=0)
        self.assertIn("width", detail)
        self.assertIn("0", detail)

    def test_hires_scale_names_the_floor_it_broke(self) -> None:
        """99.0 used to be refused here. The ceiling went at AR6.9, so the
        only hires-scale refusal left is the FLOOR -- and it still has to name
        the value AND the bound rather than just repeat the number."""

        detail = _detail(
            "GENERATION_HIRES_SCALE_OUT_OF_RANGE", enable_hr=True, hr_scale=0.25
        )
        self.assertIn("0.25", detail)
        self.assertNotEqual("0.25", detail)

    def test_hires_denoise_names_its_range(self) -> None:
        detail = _detail(
            "GENERATION_HIRES_DENOISE_OUT_OF_RANGE",
            enable_hr=True,
            hr_scale=1.5,
            hr_denoising_strength=4.0,
        )
        self.assertIn("0.0-1.0", detail)

    def test_no_refusal_detail_is_a_bare_number(self) -> None:
        """The class, asserted as a class. Any future range check that
        answers with just the value fails here rather than in production."""

        for overrides in (
            {"steps": TOO_FEW_STEPS},
            {"width": 0},
            {"batch_size": 3},
            {"output_count": 2},
            {"enable_hr": True, "hr_scale": 99.0},
            {"enable_hr": True, "hr_scale": 1.5, "hr_second_pass_steps": 900},
            {"enable_hr": True, "hr_scale": 1.5, "hr_denoising_strength": 4.0},
        ):
            for problem in _problems(**overrides):
                detail = problem["detail"].strip()
                with self.subTest(code=problem["code"]):
                    self.assertFalse(
                        detail.replace(".", "").replace("-", "").isdigit(),
                        f"{problem['code']} answers with the bare value {detail!r}",
                    )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_REFUSAL_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
