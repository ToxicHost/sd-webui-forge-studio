"""A load failure must say which stage failed, and never say where the file is.

The defect this pins: the lifecycle caught ``BaseException`` and kept only
``type(exc).__name__``, so a ``SessionLoadError`` that already knew it failed at
``engine_built`` was reported as "The model failed to load (SessionLoadError)".
A live confirmation could not then say which stage failed.

The opposite risk is just as real, so it is pinned too: an exception message
from Forge or Torch can contain an absolute payload path, and must not reach the
browser.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.session_loader import SessionLoadError  # noqa: E402
from forge_studio.load_diagnostics import load_failure_from  # noqa: E402
from forge_studio.model_profiles import ModelProfile, ModelProfileRepository  # noqa: E402

PRIVATE = r"C:\Users\owner\Desktop\Studio-Standalone\Private-Local\Models\x.safetensors"


class StagePreservationTests(unittest.TestCase):
    def test_the_stage_survives_the_lifecycle_catch(self) -> None:
        failure = load_failure_from(
            SessionLoadError("MODEL_LOAD_FAILED", "boom", step="engine_built")
        )
        self.assertEqual(failure.detail["load_stage"], "engine_built")

    def test_every_loader_stage_maps_to_owner_wording(self) -> None:
        # The stages the loader actually notes. If a stage is added and not
        # mapped, this fails rather than silently producing a bare code.
        source = (APP_ROOT / "forge_headless" / "session_loader.py").read_text(
            encoding="utf-8"
        )
        import re

        stages = set(re.findall(r'note\("([a-z_]+)"\)', source))
        self.assertTrue(stages, "no stages found; the guard would be vacuous")
        for stage in stages:
            failure = load_failure_from(
                SessionLoadError("MODEL_LOAD_FAILED", "boom", step=stage)
            )
            self.assertEqual(failure.detail["load_stage"], stage)
            self.assertNotIn(
                "(", failure.message,
                f"stage {stage} fell through to the generic class-name form",
            )

    def test_the_stable_code_survives(self) -> None:
        failure = load_failure_from(
            SessionLoadError("MODEL_LOAD_CANCELLED", "x", step="payloads_opened")
        )
        self.assertEqual(failure.code, "MODEL_LOAD_CANCELLED")

    def test_recoverability_is_reported(self) -> None:
        after_open = load_failure_from(
            SessionLoadError("MODEL_LOAD_FAILED", "x", step="engine_built")
        )
        self.assertEqual(after_open.detail["recoverability"], "not_recoverable")
        generic = load_failure_from(RuntimeError("x"))
        self.assertEqual(generic.detail["recoverability"], "retryable")

    def test_an_unknown_exception_still_produces_a_safe_failure(self) -> None:
        failure = load_failure_from(RuntimeError("something went wrong"))
        self.assertEqual(failure.code, "MODEL_LOAD_FAILED")
        self.assertEqual(failure.detail["inner_error_class"], "RuntimeError")
        self.assertIn("failed to load", failure.message)


class NoLeakTests(unittest.TestCase):
    def _rendered(self, exc: BaseException) -> str:
        return json.dumps(load_failure_from(exc).to_dict())

    def test_a_path_bearing_exception_message_is_not_forwarded(self) -> None:
        rendered = self._rendered(RuntimeError(f"cannot open {PRIVATE}"))
        for token in ("Private-Local", "C:\\", ".safetensors", "Users"):
            self.assertNotIn(token, rendered)

    def test_a_path_bearing_session_error_message_is_not_forwarded(self) -> None:
        rendered = self._rendered(
            SessionLoadError("MODEL_LOAD_FAILED", f"bad file {PRIVATE}",
                             step="payloads_opened")
        )
        self.assertNotIn("Private-Local", rendered)
        self.assertNotIn(".safetensors", rendered)

    def test_no_traceback_or_repr_reaches_the_projection(self) -> None:
        try:
            raise ValueError(PRIVATE)
        except ValueError as exc:
            rendered = self._rendered(exc)
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn("ValueError(", rendered)
        self.assertNotIn(PRIVATE, rendered)

    def test_a_hostile_exception_class_name_cannot_inject_text(self) -> None:
        hostile = type("Bad Name With Spaces", (Exception,), {})
        rendered = self._rendered(hostile("x"))
        self.assertIn("Exception", rendered)
        self.assertNotIn("Bad Name With Spaces", rendered)


class LifecycleIntegrationTests(unittest.TestCase):
    """The failure the lifecycle actually publishes, through a real manager."""

    def _manager(self, loader):  # type: ignore[no-untyped-def]
        from forge_studio.model_lifecycle import WarmSessionManager

        profile = ModelProfile(
            profile_id="p",
            display_name="p",
            family="test",
            payload_references={"checkpoint": "a", "text_encoder": "b", "vae": "c"},
        )
        repo = ModelProfileRepository([profile])
        manager = WarmSessionManager(profiles=repo, loader=loader,
                                     closer=lambda s: None)
        # No select step: the model is named by the thing that asks for it.
        return manager, profile

    def test_the_published_failure_carries_the_stage(self) -> None:
        def failing(profile):  # type: ignore[no-untyped-def]
            raise SessionLoadError("MODEL_LOAD_FAILED", "boom", step="engine_built")

        manager, profile = self._manager(failing)
        with self.assertRaises(Exception):
            manager.ensure_loaded(profile)
        failure = manager.lifecycle.snapshot().get("failure") or {}
        detail = failure.get("detail") or {}
        self.assertEqual(detail.get("load_stage"), "engine_built")
        # The old masked form must not come back.
        self.assertNotIn("(SessionLoadError)", json.dumps(failure))

    def test_a_path_in_a_loader_exception_never_reaches_the_snapshot(self) -> None:
        def failing(profile):  # type: ignore[no-untyped-def]
            raise RuntimeError(f"could not read {PRIVATE}")

        manager, profile = self._manager(failing)
        with self.assertRaises(Exception):
            manager.ensure_loaded(profile)
        rendered = json.dumps(manager.lifecycle.snapshot())
        self.assertNotIn("Private-Local", rendered)
        self.assertNotIn(".safetensors", rendered)


if __name__ == "__main__":
    unittest.main()
