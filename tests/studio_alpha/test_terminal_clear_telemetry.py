"""The terminal-clear counter must observe the seam it exists to measure.

A live unload performed the terminal cache clear, returned memory to baseline,
and reported `terminal_cache_clears = 0`. The clear ran; the telemetry could not
see it, because the composition's session-close wrapper was annotated `-> None`
and discarded the report that carries `terminal_release`.

These tests pin the wiring end to end and the counter's exact-once semantics.
No model is loaded and no payload is opened.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.model_lifecycle import WarmSessionManager  # noqa: E402
from forge_studio.model_profiles import ModelProfile, ModelProfileRepository  # noqa: E402


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id="p", display_name="p", family="test",
        payload_references={"checkpoint": "a", "text_encoder": "b", "vae": "c"},
    )


class _Session:
    """Stands in for LoadedStudioSession: close() returns a report."""

    def __init__(self, called: bool = True) -> None:
        self.called = called
        self.closes = 0

    def close(self) -> dict[str, object]:
        self.closes += 1
        return {"terminal_release": {"called": self.called, "backend": "cuda"}}


class CounterWiringTests(unittest.TestCase):
    def _manager(self, session, closer=None):  # type: ignore[no-untyped-def]
        manager = WarmSessionManager(
            profiles=ModelProfileRepository([_profile()]),
            loader=lambda profile: session,
            closer=closer,
        )
        manager.ensure_loaded(_profile())
        return manager

    def test_the_counter_is_zero_before_an_unload(self) -> None:
        manager = self._manager(_Session())
        self.assertEqual(manager.counters["terminal_cache_clears"], 0)
        self.assertEqual(manager.counters["loads"], 1)

    def test_an_explicit_unload_increments_it_exactly_once(self) -> None:
        session = _Session()
        manager = self._manager(session)
        manager.unload()
        self.assertEqual(manager.counters["terminal_cache_clears"], 1)
        self.assertEqual(manager.counters["unloads"], 1)
        self.assertEqual(session.closes, 1)

    def test_a_second_unload_does_not_double_increment(self) -> None:
        manager = self._manager(_Session())
        manager.unload()
        manager.unload()
        self.assertEqual(manager.counters["terminal_cache_clears"], 1)

    def test_a_report_saying_not_called_does_not_increment(self) -> None:
        manager = self._manager(_Session(called=False))
        manager.unload()
        self.assertEqual(manager.counters["terminal_cache_clears"], 0)

    def test_a_closer_that_drops_the_report_is_visible_as_zero(self) -> None:
        # THE defect, reproduced deliberately: a closer that returns None makes
        # the counter blind even though the session's close() ran.
        session = _Session()
        manager = self._manager(session, closer=lambda s: s.close() and None)
        manager.unload()
        self.assertEqual(session.closes, 1, "the session was closed")
        self.assertEqual(
            manager.counters["terminal_cache_clears"], 0,
            "a report-dropping closer must be observable as a zero counter",
        )

    def test_the_last_terminal_release_report_is_retained(self) -> None:
        manager = self._manager(_Session())
        manager.unload()
        self.assertEqual(manager.last_terminal_release.get("called"), True)


class CompositionWrapperTests(unittest.TestCase):
    """The specific wrapper that dropped the report."""

    def _close_wrapper(self):  # type: ignore[no-untyped-def]
        from forge_studio.composition import _real_session_loader

        _build, closer = _real_session_loader(
            result_root=APP_ROOT, load_configuration=None, closer=None
        )
        return closer

    def test_the_default_closer_returns_the_session_report(self) -> None:
        session = _Session()
        report = self._close_wrapper()(session)
        self.assertIsInstance(report, dict)
        self.assertEqual(report["terminal_release"]["called"], True)
        self.assertEqual(session.closes, 1)

    def test_the_wrapper_is_not_annotated_as_returning_none(self) -> None:
        # Mutation-style guard: reverting the annotation to `-> None` is the
        # exact edit that reintroduces the blindness, and it reads as harmless.
        source = (APP_ROOT / "forge_studio" / "composition.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        # Only the SESSION-close wrapper: the one taking a `session` argument.
        # The host `close(self)` methods legitimately return nothing.
        wrappers = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "close"
            and [a.arg for a in node.args.args] == ["session"]
        ]
        self.assertEqual(
            len(wrappers), 1,
            "expected exactly one session-close wrapper in composition.py",
        )
        for node in wrappers:
            self.assertFalse(
                isinstance(node.returns, ast.Constant) and node.returns.value is None,
                "the session-close wrapper is annotated -> None again; the "
                "terminal-release report would be discarded",
            )
            returns_something = any(
                isinstance(inner, ast.Return) and inner.value is not None
                for inner in ast.walk(node)
            )
            self.assertTrue(
                returns_something,
                "the session-close wrapper returns nothing; the "
                "terminal_cache_clears counter would stay blind",
            )

    def test_a_supplied_closer_still_takes_precedence(self) -> None:
        from forge_studio.composition import _real_session_loader

        marker = {"terminal_release": {"called": True}}
        _build, closer = _real_session_loader(
            result_root=APP_ROOT, load_configuration=None,
            closer=lambda session: marker,
        )
        self.assertIs(closer(_Session()), marker)


if __name__ == "__main__":
    unittest.main()
