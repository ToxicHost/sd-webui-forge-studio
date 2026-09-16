"""Durable preferences: what may enter the store, and what wins after.

`_refuse_unknown` stops a write inventing a key. Nothing stopped a write
giving a REAL key the wrong thing, so any recognised key could carry any JSON
into durable state and the next reader was the one that broke. The browser
converts on its way out -- `prefs.js` has a per-key converter for every
migrated key -- but the browser is not the only caller and is not trusted.

SCOPE: value shapes on the way in, and the one consumer whose behaviour
depends on a stored value surviving correctly. Not migration timing, not the
state-root resolution, both of which are traced in
`Evidence/d1-durable-preferences-2026-08-16/`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from forge_studio.preferences import (
    PREFERENCE_KEYS,
    PREFERENCE_SHAPES,
    MalformedDocument,
    MalformedPreferenceValues,
    PreferenceStore,
)


EXPECTED_D1_TESTS = 17

APP_ROOT = Path(__file__).resolve().parents[2]


class _Rooted(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def store(self) -> PreferenceStore:
        return PreferenceStore(self.root)


class ShapeContractTests(_Rooted):
    """Every recognised key is pinned, and the pins match the product."""

    def test_every_allowed_key_has_a_shape(self) -> None:
        """A key with no rule is a key that accepts anything.

        This is the test that keeps the two lists together: adding a
        preference without a shape silently reopens the hole.
        """
        self.assertEqual(set(PREFERENCE_KEYS), set(PREFERENCE_SHAPES))

    def test_no_shape_exists_for_a_key_that_does_not(self) -> None:
        self.assertEqual(set(), set(PREFERENCE_SHAPES) - set(PREFERENCE_KEYS))


class PerTypeRefusalTests(_Rooted):
    """One case per value type the product actually stores."""

    def test_a_boolean_key_refuses_a_string(self) -> None:
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"gpu_tile_compositing": "yes"})

    def test_a_string_key_refuses_an_object(self) -> None:
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"save_dir": {"path": "C:/somewhere"}})

    def test_an_object_key_refuses_a_scalar(self) -> None:
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"shortcuts": "ctrl+s"})

    def test_a_numeric_key_refuses_a_string(self) -> None:
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"vram_weights": "0.8"})

    def test_a_numeric_key_refuses_a_boolean(self) -> None:
        """`bool` is a subclass of `int`, so this passes isinstance by accident.

        Without the explicit guard, `vram_weights = True` would be stored as a
        number and read back as 1.
        """
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"vram_weights": True})

    def test_an_enum_key_refuses_a_value_outside_it(self) -> None:
        with self.assertRaises(MalformedPreferenceValues):
            self.store().merge({"save_tree": "sideways"})

    def test_an_enum_key_accepts_its_own_values(self) -> None:
        store = self.store()
        for value in ("neo", "studio"):
            with self.subTest(value=value):
                self.assertEqual(value, store.merge({"save_tree": value})["save_tree"])


class RefusalPolicyTests(_Rooted):
    """Refused whole, and the older refusals keep their own cases."""

    def test_a_mixed_write_changes_nothing(self) -> None:
        """Accepting the good half would answer 200 over a partial save."""
        store = self.store()
        store.merge({"save_dir": "C:/keep"})
        with self.assertRaises(MalformedPreferenceValues):
            store.merge({"layout_preset": "wide", "vram_weights": "nonsense"})
        stored = store.read()
        self.assertEqual({"save_dir": "C:/keep"}, stored)

    def test_json_that_cannot_be_carried_keeps_its_own_refusal(self) -> None:
        """Ordering matters. "JSON cannot hold this" is decided first.

        A set is not a shape complaint -- it never gets as far as being the
        wrong shape, because it cannot be written at all.
        """
        with self.assertRaises(MalformedDocument):
            self.store().merge({"education": {1, 2}})

    def test_the_adapter_answers_400(self) -> None:
        from forge_studio.source_api_adapter import _PREFERENCE_REFUSALS

        mapped = {kind: status for kind, status in _PREFERENCE_REFUSALS}
        self.assertEqual(400, mapped[MalformedPreferenceValues])


class DurableWinsTests(_Rooted):
    """What is already stored is not overwritten by a later default."""

    def test_a_stored_value_survives_a_write_that_does_not_name_it(self) -> None:
        store = self.store()
        store.merge({"gpu_tile_compositing": False})
        store.merge({"layout_preset": "wide"})
        self.assertFalse(store.read()["gpu_tile_compositing"])

    def test_a_stored_value_survives_a_reopen(self) -> None:
        """The restart case, without a restart: a second store, same root."""
        self.store().merge({"gpu_tile_compositing": False})
        self.assertFalse(PreferenceStore(self.root).read()["gpu_tile_compositing"])


class GpuCompositeRegressionTests(_Rooted):
    """The one consumer that reads a stored value at job assembly.

    Section 19: persistence is only worth anything if the value still reaches
    the request. This is the same path the GPU-composite package built, tested
    here against a REAL store rather than a fake one -- which is the gap that
    let the translation defect through last time.
    """

    def _policy(self, store):
        from forge_studio.presentation import StudioPresentation

        return StudioPresentation(
            object(), dict, preferences=store
        )._server_execution_policy()

    def test_persisting_false_reaches_request_assembly(self) -> None:
        store = self.store()
        store.merge({"gpu_tile_compositing": False})
        self.assertFalse(
            self._policy(PreferenceStore(self.root))["gpu_tile_compositing_requested"])

    def test_persisting_true_reaches_request_assembly(self) -> None:
        store = self.store()
        store.merge({"gpu_tile_compositing": True})
        self.assertTrue(
            self._policy(PreferenceStore(self.root))["gpu_tile_compositing_requested"])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_D1_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
