"""Interrupt and Skip stop the job that is actually running.

Reported by the owner: both buttons under Generate do nothing. They had TWO
independent reasons to, and either alone was enough.

```text
the page   `if (!State.generating) return;` -- the flag the LEGACY generate
           path sets and the lifecycle path never reaches, so the click did
           not even issue the request
the server `interrupt` acted on `self._active`, set by this adapter's own
           `generate`, which the product does not call. It answered
           `{"ok": true, "cancelled": false}` and the page toasted
           "Interrupting..." over it.
           `skip` was a hardcoded `{"ok": true, "skipped": false}`.
```

Both are the shape this phase keeps finding: a check written before there was
a lifecycle, left behind when one arrived.

SKIP AND INTERRUPT COINCIDE, DELIBERATELY

Upstream's Skip abandons one image of a BATCH. Studio generates one image per
job and the queue starts the next as soon as the current reaches a safe
terminal state -- so "skip to the next image" and "cancel the one running"
are the same act here. Skip is implemented as that rather than left lying.

SCOPE: MINIMAL_RUNTIME_SCOPE. No engine; the adapter is driven with a
presentation double, and the page is checked as source.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_INTERRUPT_TESTS = 13

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)

RUNNING = "studio-job-000007"


class _Presentation:
    def __init__(self, running: str | None) -> None:
        self._running = running
        self.cancelled: list[str] = []

    def running_job_id(self) -> str | None:
        return self._running

    def cancel(self, job_id: str) -> dict[str, Any]:
        self.cancelled.append(job_id)
        return {"cancelled": True, "message": "Cancelled.", "job_id": job_id}


def _adapter(running: str | None):
    presentation = _Presentation(running)
    return SourceFrontendAdapter(presentation), presentation


class InterruptTests(unittest.TestCase):
    def test_it_cancels_the_running_coordinator_job(self) -> None:
        adapter, presentation = _adapter(RUNNING)
        got = adapter.post("/studio/interrupt", {})
        self.assertTrue(got["cancelled"])
        self.assertEqual([RUNNING], presentation.cancelled)

    def test_it_reports_truthfully_when_nothing_runs(self) -> None:
        """`ok: true, cancelled: false` was the old answer for EVERY job.
        It must still be the answer when there genuinely is none."""

        adapter, presentation = _adapter(None)
        got = adapter.post("/studio/interrupt", {})
        self.assertFalse(got["cancelled"])
        self.assertEqual([], presentation.cancelled)

    def test_it_says_what_happened(self) -> None:
        adapter, _ = _adapter(None)
        self.assertIn("generating", adapter.post("/studio/interrupt", {})["message"])


class SkipTests(unittest.TestCase):
    def test_skip_cancels_the_running_job(self) -> None:
        adapter, presentation = _adapter(RUNNING)
        got = adapter.post("/studio/skip", {})
        self.assertTrue(got["skipped"])
        self.assertEqual([RUNNING], presentation.cancelled)

    def test_skip_reports_truthfully_when_nothing_runs(self) -> None:
        """It answered `skipped: false` unconditionally, which was right by
        accident and wrong as an answer."""

        adapter, presentation = _adapter(None)
        self.assertFalse(adapter.post("/studio/skip", {})["skipped"])
        self.assertEqual([], presentation.cancelled)

    def test_skip_is_no_longer_a_hardcoded_refusal(self) -> None:
        adapter, _ = _adapter(RUNNING)
        self.assertNotIn("mock mode", adapter.post("/studio/skip", {})["message"])


class PageTests(unittest.TestCase):
    """The click must reach the server at all."""

    @staticmethod
    def _handler(button: str) -> str:
        start = APP_JS.find(f'getElementById("{button}")?.addEventListener')
        assert start != -1, f"{button} lost its handler"
        return APP_JS[start:start + 900]

    @staticmethod
    def _code_only(source: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())

    def test_interrupt_does_not_gate_on_the_dead_flag(self) -> None:
        self.assertNotIn(
            "if (!State.generating) return;",
            self._code_only(self._handler("interruptBtn")),
        )

    def test_skip_does_not_gate_on_the_dead_flag(self) -> None:
        self.assertNotIn(
            "if (!State.generating) return;",
            self._code_only(self._handler("skipBtn")),
        )

    def test_interrupt_reads_the_outcome(self) -> None:
        """Toasting before the answer arrives is how a no-op reported
        success for the life of the feature."""

        self.assertIn("outcome.cancelled", self._handler("interruptBtn"))

    def test_skip_reads_the_outcome(self) -> None:
        self.assertIn("outcome.skipped", self._handler("skipBtn"))

    def test_both_still_ask_the_server(self) -> None:
        """The discriminating half: deleting the handlers would satisfy every
        assertion above."""

        self.assertIn("API.interrupt()", self._handler("interruptBtn"))
        self.assertIn("API.skip()", self._handler("skipBtn"))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_INTERRUPT_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
