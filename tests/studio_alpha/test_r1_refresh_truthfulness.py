"""R1 Tier 2 -- controls that report work against a service that DOES exist.

Batch A and Batch E dealt with absent services. This is the other kind: the
backend is present and capable, and the control still reported something that
did not happen.

`POST /studio/refresh_models` answered `{"ok": true, "count": N}` without
touching a disk. `_models()` reads the catalogue and `catalogue.snapshot()`
returns the CACHED scan, so the page toasted "Models refreshed", re-fetched
`/studio/models`, and received exactly what it already had. An owner who
dropped a checkpoint into a configured root and pressed the button was told it
had worked.

`ModelRootRegistry.refresh(role)` has existed at `model_roots.py:404` the whole
time with no caller outside its own class.
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
    SourceFrontendAdapterError,
)

EXPECTED_R1_REFRESH_TESTS = 6


class _Presentation:
    def models(self):
        return {"models": [{"title": "a.safetensors"}, {"title": "b.safetensors"}]}


class _Catalogue:
    def __init__(self, owner, role):
        self._owner = owner
        self._role = role

    def refresh(self):
        self._owner.refreshed.append(self._role)
        return None

    def snapshot(self):
        return None


class _Registry:
    """A registry that RECORDS whether it was actually asked to re-enumerate.

    The old implementation would pass any test that only checked the response
    body, because the body was already truthful about the count -- it was the
    absence of a rescan that was the lie. So the double has to observe the
    call, not the answer.
    """

    def __init__(self, roles=("checkpoint", "vae", "text_encoder")):
        self._roles = tuple(roles)
        self.refreshed: list[str] = []

    def describe(self):
        return {role: object() for role in self._roles}

    def refresh(self, role):
        self.refreshed.append(role)
        return None

    def catalogues(self, role):
        return ()

    def entries(self, role):
        return ()

    def snapshot(self, role):
        return None


class RefreshModelsTests(unittest.TestCase):
    def test_refreshing_actually_re_enumerates_every_role(self) -> None:
        registry = _Registry()
        adapter = SourceFrontendAdapter(_Presentation(), model_roots=registry)

        payload = adapter.post("/studio/refresh_models", {})

        self.assertEqual(
            ["checkpoint", "vae", "text_encoder"], registry.refreshed
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(3, payload["roles_rescanned"])

    def test_it_reports_how_many_roles_it_rescanned(self) -> None:
        """So the answer distinguishes a real rescan from a no-op."""

        registry = _Registry(roles=("checkpoint",))
        adapter = SourceFrontendAdapter(_Presentation(), model_roots=registry)
        payload = adapter.post("/studio/refresh_models", {})
        self.assertEqual(1, payload["roles_rescanned"])
        self.assertEqual(["checkpoint"], registry.refreshed)

    def test_an_unconfigured_host_refuses_rather_than_reporting_success(self) -> None:
        """Nothing to rescan is a refusal, not a cheerful count of zero.

        And it must be a real status, not a 200 carrying ok:false -- that
        pairing is the defect class this whole milestone exists to remove.
        """

        adapter = SourceFrontendAdapter(_Presentation(), model_roots=None)
        with self.assertRaises(SourceFrontendAdapterError) as raised:
            adapter.post("/studio/refresh_models", {})
        self.assertEqual(409, raised.exception.error["http_status"])

    def test_the_count_still_answers_from_the_presentation(self) -> None:
        registry = _Registry()
        adapter = SourceFrontendAdapter(_Presentation(), model_roots=registry)
        payload = adapter.post("/studio/refresh_models", {})
        self.assertEqual(2, payload["count"])

    def test_the_registry_seam_it_uses_is_the_one_that_already_existed(self) -> None:
        """Guard against this being re-implemented as a parallel scan path.

        `ModelRootRegistry.refresh` was dead code, not missing code. If a future
        change adds a second way to re-enumerate, this is the test that should
        make someone stop and ask why.
        """

        import re

        source = (APP_ROOT / "forge_headless" / "model_roots.py").read_text(
            encoding="utf-8"
        )
        source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
        self.assertIn("def refresh(self, role: str)", source)

        adapter_source = (
            APP_ROOT / "forge_studio" / "source_api_adapter.py"
        ).read_text(encoding="utf-8")
        block = adapter_source.split('route == "/studio/refresh_models"', 1)[1]
        block = block.split("if route ==", 1)[0]
        self.assertIn("registry.refresh(role)", block)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_R1_REFRESH_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
