"""Why a generation failed must survive the job record.

A live leg spent an entire diagnostic cycle on a generation that reported:

```text
{"code": "GENERATION_FAILED", "message": "HeadlessBackendError"}
```

The backend had in fact refused the request for a named, field-tagged reason
-- `GENERATION_SEED_NOT_FIXED`, "This backend requires an explicit seed;
random seed is not supported." -- built deliberately in
`forge_headless/studio_generation.py` for exactly this moment. It was thrown
away one layer up: `HeadlessBackendError` is not a `StudioError`, so it landed
in `JobCoordinator._run`'s generic handler, which recorded only the exception's
CLASS NAME.

The owner therefore saw a Generate button that failed with no reason, on every
click, for a cause the product already knew and had already phrased.

These tests pin the repair and its boundary: an owned error is preserved, and
anything not owned still reports opaquely.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import StructuredError, StudioError  # noqa: E402
from forge_studio.jobs import _failure_record, _reported_reason  # noqa: E402


class _BackendError(Exception):
    """Shaped like `HeadlessBackendError`: owns an error, is not a StudioError."""

    def __init__(self, error: StructuredError, http_status: int = 400) -> None:
        super().__init__(error.message)
        self.error = error
        self.http_status = http_status


class OwnedErrorIsPreservedTests(unittest.TestCase):
    def test_backend_error_reports_its_own_code_and_message(self) -> None:
        exc = _BackendError(
            StructuredError(
                code="GENERATION_SEED_NOT_FIXED",
                message="This backend requires an explicit seed; random seed "
                        "is not supported.",
                field="seed",
            )
        )
        self.assertEqual(
            {
                "code": "GENERATION_SEED_NOT_FIXED",
                "message": "This backend requires an explicit seed; random "
                           "seed is not supported.",
            },
            _failure_record(exc),
        )

    def test_the_class_name_is_no_longer_the_whole_diagnosis(self) -> None:
        # The exact regression: the record used to be the class name.
        exc = _BackendError(
            StructuredError(code="GENERATION_MODEL_NOT_RESIDENT",
                            message="That model is not resident.")
        )
        record = _failure_record(exc)
        self.assertNotEqual("_BackendError", record["message"])
        self.assertNotEqual("GENERATION_FAILED", record["code"])

    def test_a_studio_error_is_preserved_by_the_same_rule(self) -> None:
        # StudioError has its own branch in _run, but it satisfies the same
        # shape, so the helper must not disagree with that branch.
        exc = StudioError(
            StructuredError(code="MODEL_NOT_READY", message="Load a model first.")
        )
        self.assertEqual(
            {"code": "MODEL_NOT_READY", "message": "Load a model first."},
            _failure_record(exc),
        )


class OnlyOwnedPlainFieldsEscapeTests(unittest.TestCase):
    def test_exactly_two_keys_are_recorded(self) -> None:
        # `field`, `http_status`, args and traceback stay in-process. The
        # record is the wire shape, so it carries no more than the
        # StudioError branch would.
        exc = _BackendError(
            StructuredError(code="GENERATION_STEPS_OUT_OF_RANGE",
                            message="Steps out of range.", field="steps")
        )
        self.assertEqual({"code", "message"}, set(_failure_record(exc)))

    def test_a_path_bearing_runtime_error_stays_opaque(self) -> None:
        # An unowned exception can carry anything at all, including a
        # filesystem path. It must not become the reported message.
        exc = OSError(2, "No such file", r"C:\Users\owner\Private-Local\x.safetensors")
        record = _failure_record(exc)
        self.assertEqual("GENERATION_FAILED", record["code"])
        # OSError(2, ...) specialises to FileNotFoundError; the contract is the
        # class name, whichever class the runtime chose.
        self.assertEqual(type(exc).__name__, record["message"])
        self.assertNotIn("Private-Local", record["message"])
        self.assertNotIn("x.safetensors", record["message"])


class UnownedFailuresStayOpaqueTests(unittest.TestCase):
    def test_plain_exception_falls_back_to_the_class_name(self) -> None:
        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "RuntimeError"},
            _failure_record(RuntimeError("boom")),
        )

    def test_an_error_attribute_that_is_not_structured_is_ignored(self) -> None:
        exc = RuntimeError("boom")
        exc.error = object()  # type: ignore[attr-defined]
        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "RuntimeError"},
            _failure_record(exc),
        )

    def test_blank_owned_fields_do_not_produce_an_empty_reason(self) -> None:
        # A code/message pair that is present but empty is worse than the
        # fallback: it reports a failure with nothing in it.
        exc = _BackendError(StructuredError(code="", message=""))
        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "_BackendError"},
            _failure_record(exc),
        )

    def test_non_string_owned_fields_do_not_reach_the_record(self) -> None:
        exc = RuntimeError("boom")
        exc.error = StructuredError(code=None, message=None)  # type: ignore[arg-type,attr-defined]
        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "RuntimeError"},
            _failure_record(exc),
        )

    def test_base_exception_is_still_handled(self) -> None:
        # _run catches BaseException; the helper must not assume Exception.
        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "KeyboardInterrupt"},
            _failure_record(KeyboardInterrupt()),
        )


class _Progress:
    """Shaped like a ProgressEvent: an observation, not an exception."""

    def __init__(self, error: object) -> None:
        self.error = error


class ReportedFailuresKeepTheirReasonTests(unittest.TestCase):
    """The sibling gap, on the path where nothing is RAISED.

    A backend that catches its own failure and marks its progress FAILED
    returns normally from `submit_generation`, so neither handler in `_run`
    fires. `_terminal_from_backend` then recorded WHICH terminal was reached
    and discarded WHY, leaving `error` at the None it was initialised with.

    That made `error: null` on a failed job mean two different things, and the
    runbook documented only one of them -- so a healthy process with an intact
    log was read as a dead one. Repro at the time: generate with sampler
    "NotASampler", which asserts inside `create_sampler`.
    """

    def test_a_reported_reason_reaches_the_record(self) -> None:
        progress = _Progress(
            StructuredError(
                code="GENERATION_FAILED", message="bad sampler name: NotASampler"
            )
        )
        self.assertEqual(
            {
                "code": "GENERATION_FAILED",
                "message": "bad sampler name: NotASampler",
            },
            _reported_reason(progress),
        )

    def test_no_reported_error_stays_honestly_null(self) -> None:
        """None, not an invented reason.

        A fabricated message here would be the same lie in a new place: the
        record would claim to know something it does not.
        """

        self.assertIsNone(_reported_reason(_Progress(None)))

    def test_an_observation_without_an_error_attribute_is_tolerated(self) -> None:
        self.assertIsNone(_reported_reason(object()))

    def test_blank_or_non_string_owned_fields_do_not_reach_the_record(self) -> None:
        for error in (
            StructuredError(code="", message="something"),
            StructuredError(code="GENERATION_FAILED", message=""),
            StructuredError(code=None, message=None),  # type: ignore[arg-type]
        ):
            with self.subTest(error=error):
                self.assertIsNone(_reported_reason(_Progress(error)))

    def test_the_discipline_matches_the_raised_path(self) -> None:
        """Only `code` and `message` travel -- never a path or a traceback.

        The stub carries extra attributes on purpose: a backend error object is
        not required to be a frozen `StructuredError`, and whatever else it
        holds must not reach the wire.
        """

        class _Rich:
            code = "GENERATION_FAILED"
            message = "denied"
            detail = "C:/Users/someone/secret/path.safetensors"
            traceback = "File ..., line 1"

        self.assertEqual(
            {"code": "GENERATION_FAILED", "message": "denied"},
            _reported_reason(_Progress(_Rich())),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
