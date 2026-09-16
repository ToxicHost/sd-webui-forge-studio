"""R1 Batch A -- the refusal/status seam, for the reachable cases only.

`presentation.py`'s /studio/ catch-all renders every adapter return at a
hardcoded HTTPStatus.OK, and `API._finish` in app.js throws only on non-2xx.
Anything that encodes a refusal in a 200 body therefore arrives as a success
unless its caller separately inspects that body.

This suite covers the three cases where an owner can reach the lie today. It
deliberately does NOT cover every `{"ok": false}`-at-200 return: several are
behind controls nothing can currently click, and one -- the Dynamic Prompts
folder picker -- must not be converted at all, because its caller is a raw
`fetch(...).then(r => r.json())` that resolves on a 404 just as happily.

Each test asserts BOTH sides of its boundary and fails against the reverted
implementation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.source_api_adapter import (  # noqa: E402
    SourceFrontendAdapter,
    SourceFrontendRouteError,
)

EXPECTED_R1_REFUSAL_SEAM_TESTS = 12

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)
INDEX_HTML = (APP_ROOT / "forge_studio" / "frontend" / "index.html").read_text(
    encoding="utf-8"
)
SETTINGS_JS = (
    APP_ROOT / "forge_studio" / "frontend" / "settings-page.js"
).read_text(encoding="utf-8")


class _Presentation:
    """Only the audited routes are exercised, so nothing optimistic is needed.

    `running_job_id` is not decoration. `interrupt()` reads `self._active`
    first -- which only this adapter's own `generate` ever sets, and the
    product never calls it -- and otherwise asks `_coordinated_job_id()`,
    which reads exactly this attribute. A double without it makes every cancel
    test take the "Nothing is generating." branch and pass without ever
    reaching the code under test. That tautology is the failure mode this whole
    suite exists to catch, so the double has to be able to lose.
    """

    def __init__(self, cancel_result=None, running=None) -> None:
        self._cancel_result = cancel_result or {}
        self.cancel_calls: list[str] = []
        if running is not None:
            # A CALLABLE: `_coordinated_job_id` does `job_id = running()`.
            # Setting a bare string here makes the attribute truthy, the call
            # raise TypeError, the `except Exception` swallow it, and the test
            # silently take the no-op branch again.
            self.running_job_id = lambda: running

    def cancel(self, job_id: str):
        self.cancel_calls.append(job_id)
        return dict(self._cancel_result)


# --------------------------------------------------------------------------
# 1. Interrupt / Skip -- `cancelled` was defaulted to True for a key that no
#    branch of JobCoordinator.cancel has ever returned.
# --------------------------------------------------------------------------


class CancelTruthTests(unittest.TestCase):
    def test_an_already_terminal_job_does_not_report_a_cancellation(self) -> None:
        """The idempotent no-op is the case the old default got wrong.

        `JobCoordinator.cancel` answers `already_terminal` for a job that had
        already finished, failed, or been cancelled. Nothing is cancelled by
        that call. The adapter used to read a MISSING `cancelled` key with a
        default of True and report "Cancelled." anyway.
        """

        adapter = SourceFrontendAdapter(
            _Presentation(
                {"job_id": "studio-job-1", "state": "completed",
                 "already_terminal": True, "cancelled": False},
                running="studio-job-1",
            )
        )
        payload = adapter.post("/studio/interrupt", {"job_id": "studio-job-1"})
        self.assertFalse(payload["cancelled"])

    def test_a_real_cancellation_still_reports_one(self) -> None:
        adapter = SourceFrontendAdapter(
            _Presentation(
                {"job_id": "studio-job-2", "state": "cancelled",
                 "cancelled_while_queued": True, "cancelled": True},
                running="studio-job-2",
            )
        )
        payload = adapter.post("/studio/interrupt", {"job_id": "studio-job-2"})
        self.assertTrue(payload["cancelled"])

    def test_an_absent_key_is_not_read_as_a_cancellation(self) -> None:
        """The default itself, tested directly.

        Any coordinator that omits the key -- including one written before this
        contract existed -- must not be reported as having cancelled something.
        """

        adapter = SourceFrontendAdapter(
            _Presentation({"state": "running"}, running="studio-job-3")
        )
        payload = adapter.post("/studio/interrupt", {"job_id": "studio-job-3"})
        self.assertFalse(payload["cancelled"])

    def test_every_cancel_branch_supplies_the_key_it_is_read_by(self) -> None:
        """Source contract: the adapter's default must never be load-bearing.

        Read from `jobs.py` rather than exercised, because reaching all four
        branches needs a live coordinator. Comments are stripped first: this
        codebase has been bitten seven times by an assertion a comment could
        satisfy.
        """

        import re

        source = (APP_ROOT / "forge_studio" / "jobs.py").read_text(encoding="utf-8")
        body = source.split("def cancel(self, public_id: str)", 1)[1]
        body = body.split("# -- teardown", 1)[0]
        body = re.sub(r"#.*$", "", body, flags=re.MULTILINE)

        returns = [
            block for block in body.split("return ")[1:]
        ]
        self.assertEqual(4, len(returns), "cancel() branch count changed")
        for index, block in enumerate(returns):
            with self.subTest(branch=index):
                self.assertIn('"cancelled"', block)


# --------------------------------------------------------------------------
# 2. Dynamic Prompts config -- a key mismatch that pinned a toggle ON, and
#    whose repair RETIRES two dependent controls through existing wiring.
# --------------------------------------------------------------------------


class DynamicPromptsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = SourceFrontendAdapter(_Presentation())

    def test_the_config_uses_the_key_the_page_actually_reads(self) -> None:
        for payload in (
            self.adapter.get("/studio/dynamic_prompts/config"),
            self.adapter.post("/studio/dynamic_prompts/config", {"enabled": True}),
        ):
            with self.subTest(payload=payload):
                self.assertIn("studio_dynamic_prompts_enabled", payload)
                self.assertFalse(payload["studio_dynamic_prompts_enabled"])
                self.assertNotIn("enabled", payload)

    def test_the_post_reaches_the_service_rather_than_a_stub(self) -> None:
        """The WP0.4 defect: a duplicate branch shadowed the real handler.

        TWO `if route == "/studio/dynamic_prompts/config"` blocks answered this
        path. The first returned a hardcoded body and won every time, so
        `set_enabled` -- eleven lines below it -- had no reachable caller and
        the owner's choice was never stored. Nothing failed, because the stub's
        answer happened to match what the capability default produced anyway.

        The stub is identified by its KEYS: it answered `folder_mode`/`folder`,
        the pair the page stopped reading when the GET was repaired, while
        `config()` answers the `wildcard_*` names. Restoring it fails this test
        by name rather than somewhere downstream.

        The service-level suites never caught this because every one of them
        calls `set_enabled` directly. Only the route was broken.
        """

        payload = self.adapter.post(
            "/studio/dynamic_prompts/config",
            {"studio_dynamic_prompts_enabled": True})
        self.assertIn("wildcard_folder_mode", payload)
        self.assertIn("wildcard_count", payload)
        self.assertNotIn("folder_mode", payload)
        self.assertNotIn("folder", payload)

    def test_an_explicit_choice_is_honoured_before_a_folder_exists(self) -> None:
        """A DELIBERATE change to what R1 pinned here, stated rather than hidden.

        R1 asserted this answered False. It did, because the stub could not do
        otherwise -- not because anything weighed the owner's request. With the
        write reachable, an owner who turns the toggle on with no folder yet is
        answered on, and `available` carries the truth the page needs.

        Forcing False instead was tried and reverted. `config()` feeds the
        toggle's `on` class and `data-setting-depends` retires Browse from it,
        so an owner refused here would lose the control that would have given
        them a folder -- a worse dead control than the one WP0.4 set out to fix.
        """

        payload = self.adapter.post(
            "/studio/dynamic_prompts/config",
            {"studio_dynamic_prompts_enabled": True})
        self.assertTrue(payload["studio_dynamic_prompts_enabled"])

        # The capability is reported separately and is still honest, which is
        # what lets the panel disable with a reason instead of lying.
        status = self.adapter.get("/studio/dynamic_prompts/status")
        self.assertFalse(status["available"])
        self.assertEqual(0, status["wildcard_count"])

    def test_the_page_reads_that_key_and_only_that_key(self) -> None:
        self.assertIn(
            "typeof cfg.studio_dynamic_prompts_enabled === \"boolean\"", APP_JS
        )

    def test_the_dependent_controls_are_wired_to_the_toggle_class(self) -> None:
        """The retirement path, asserted so it cannot be refactored away silently.

        Clearing the toggle's `on` class must disable Browse and Reset. That
        happens through `data-setting-depends` + the MutationObserver in
        settings-page.js, not through anything this change added -- so if either
        end is removed, the adapter fix silently stops retiring the controls and
        they become reachable liars again.
        """

        self.assertIn('data-setting-depends="toggleStudioDynPrompts"', INDEX_HTML)
        self.assertIn("MutationObserver(sync).observe(toggle", SETTINGS_JS)
        self.assertIn('classList.contains("on")', SETTINGS_JS)


# --------------------------------------------------------------------------
# 3. /studio/load_vae -- a stub that lied to a caller whose guard was correct.
# --------------------------------------------------------------------------


class VaeLoadRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = SourceFrontendAdapter(_Presentation())

    def test_loading_a_vae_is_a_real_refusal_not_an_invented_success(self) -> None:
        with self.assertRaises(SourceFrontendRouteError) as raised:
            self.adapter.post("/studio/load_vae", {"name": "sdxl_vae.safetensors"})
        self.assertEqual(404, raised.exception.error["http_status"])

    def test_the_caller_guard_that_makes_the_refusal_visible_still_exists(self) -> None:
        """The other half of the boundary.

        A 404 is only honest because app.js checks `r.ok && data.ok` and has an
        else-branch that toasts. Asserting the refusal without asserting the
        guard is the one-layer-short failure this project keeps repeating.
        """

        self.assertIn("if (r.ok && data.ok) {", APP_JS)
        self.assertIn("toast.vae.loadFailed", APP_JS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_R1_REFUSAL_SEAM_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
