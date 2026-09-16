"""Ordered roots: several directories per role, and what must not move.

The shape is variable-length in the schema and the API. `MAX_ROOTS_PER_ROLE`
is an implementation limit on what this build will enumerate, with its own
refusal code, so raising it later is a one-line change and no stored document
needs migrating -- a schema that fixed the length would have made that a
migration.

The property this suite exists to protect is id stability. A model id is
`sha256(domain, role, RESOLVED ROOT, relative name)`, and order is not an
input, so:

```text
reorder the roots       no id moves
add or remove a root    no OTHER root's ids move
change a root's PATH    every id under it moves -- and that is the one case
                        the owner can actually notice, so it is tested
                        end to end rather than reasoned about
```

SCOPE: MINIMAL_RUNTIME_SCOPE. Real directories under a temporary root, holding
empty files with model extensions. Nothing is opened, loaded or served.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.model_roots import (  # noqa: E402
    MAX_ROOTS_PER_ROLE,
    ROOT_STATUS_NOT_CONFIGURED,
    ROOT_STATUS_PARTIAL,
    ROOT_STATUS_READY,
    ROOT_STATUS_REFUSED,
    ROOTS_TOO_MANY,
    ModelRootRegistry,
    normalize_roots,
)

WORKSPACE_ROOT = APP_ROOT.parent

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_ORDERED_TESTS = 35


def _root(prefix: str, names) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    for name in names:
        (root / name).write_bytes(b"")
    return root


class _RegistryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.a = _root("ord-a-", ("Alpha.safetensors",))
        self.b = _root("ord-b-", ("Beta.safetensors",))
        self.c = _root("ord-c-", ("Gamma.safetensors",))
        for root in (self.a, self.b, self.c):
            self.addCleanup(shutil.rmtree, root, True)
        self.registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)

    def configure(self, *roots, role: str = "checkpoint"):
        return self.registry.configure({role: tuple(str(r) for r in roots)})

    def ids(self, role: str = "checkpoint"):
        return [entry.model_id for entry in self.registry.entries(role)]

    def names(self, role: str = "checkpoint"):
        return [entry.relative_location for entry in self.registry.entries(role)]


# --------------------------------------------------------------------------
# 1. Shape: variable length, both spellings
# --------------------------------------------------------------------------


class ShapeTests(unittest.TestCase):
    def test_a_bare_string_is_read_as_one_root(self) -> None:
        """The pre-P0.4 spelling still means what it always meant."""

        self.assertEqual(
            {"checkpoint": ("C:/Models",)},
            normalize_roots({"checkpoint": "C:/Models"}),
        )

    def test_a_list_is_read_in_order(self) -> None:
        self.assertEqual(
            {"checkpoint": ("C:/A", "D:/B", "E:/C")},
            normalize_roots({"checkpoint": ["C:/A", "D:/B", "E:/C"]}),
        )

    def test_an_empty_list_is_not_configured(self) -> None:
        self.assertEqual({}, normalize_roots({"checkpoint": []}))

    def test_blank_entries_are_dropped_not_refused(self) -> None:
        self.assertEqual(
            {"checkpoint": ("C:/A",)},
            normalize_roots({"checkpoint": ["C:/A", "  ", ""]}),
        )

    def test_textual_duplicates_collapse_and_keep_the_first_position(self) -> None:
        self.assertEqual(
            {"checkpoint": ("C:/A", "D:/B")},
            normalize_roots({"checkpoint": ["C:/A", " C:/A ", "D:/B", "C:/A"]}),
        )

    def test_a_non_string_entry_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            normalize_roots({"checkpoint": ["C:/A", 17]})
        self.assertEqual("HEADLESS_MODEL_ROOTS_MALFORMED", caught.exception.code)

    def test_a_non_list_non_string_role_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            normalize_roots({"checkpoint": {"path": "C:/A"}})
        self.assertEqual("HEADLESS_MODEL_ROOTS_MALFORMED", caught.exception.code)

    def test_the_limit_is_an_implementation_limit_with_its_own_code(self) -> None:
        """Distinct from MALFORMED, so an owner is told which of the two they
        hit -- and so raising the limit later cannot be confused with relaxing
        the shape."""

        too_many = [f"C:/Models{index}" for index in range(MAX_ROOTS_PER_ROLE + 1)]
        with self.assertRaises(HeadlessError) as caught:
            normalize_roots({"checkpoint": too_many})
        self.assertEqual(ROOTS_TOO_MANY, caught.exception.code)

    def test_exactly_the_limit_is_accepted(self) -> None:
        at_limit = [f"C:/Models{index}" for index in range(MAX_ROOTS_PER_ROLE)]
        self.assertEqual(
            MAX_ROOTS_PER_ROLE,
            len(normalize_roots({"checkpoint": at_limit})["checkpoint"]),
        )

    def test_the_schema_does_not_fix_the_length(self) -> None:
        """A source pin. The limit must stay a runtime check against a named
        constant -- if a fixed-length shape appears in the parser, raising the
        limit stops being a one-line change."""

        source = (
            APP_ROOT / "forge_headless" / "model_roots.py"
        ).read_text(encoding="utf-8")
        self.assertIn("MAX_ROOTS_PER_ROLE = 8", source)
        self.assertIn("len(ordered) > MAX_ROOTS_PER_ROLE", source)


# --------------------------------------------------------------------------
# 2. Enumeration across roots
# --------------------------------------------------------------------------


class EnumerationTests(_RegistryCase):
    def test_every_root_contributes(self) -> None:
        self.configure(self.a, self.b, self.c)
        self.assertEqual(
            ["Alpha.safetensors", "Beta.safetensors", "Gamma.safetensors"],
            self.names(),
        )

    def test_the_owners_order_is_the_listed_order(self) -> None:
        self.configure(self.c, self.a)
        self.assertEqual(["Gamma.safetensors", "Alpha.safetensors"], self.names())

    def test_two_roots_holding_the_same_filename_both_appear(self) -> None:
        """They are two different files. The id is keyed on the resolved root,
        so they have different ids, and hiding one behind the other would make
        a model the owner can see in their folder unreachable."""

        (self.b / "Alpha.safetensors").write_bytes(b"")
        self.configure(self.a, self.b)
        self.assertEqual(2, self.names().count("Alpha.safetensors"))
        self.assertEqual(len(set(self.ids())), len(self.ids()))

    def test_the_same_directory_twice_is_listed_once(self) -> None:
        """Two spellings of ONE directory resolve to one root, so the entries
        would carry identical ids -- indistinguishable downstream rather than
        merely untidy."""

        self.configure(self.a, self.a)
        self.assertEqual(["Alpha.safetensors"], self.names())

    def test_an_unconfigured_role_enumerates_nothing(self) -> None:
        self.configure(self.a)
        self.assertEqual((), self.registry.entries("vae"))

    def test_entry_counts_aggregate_across_roots(self) -> None:
        status = self.configure(self.a, self.b, self.c)["checkpoint"]
        self.assertEqual(3, status.entry_count)
        self.assertEqual(3, len(status.roots))


# --------------------------------------------------------------------------
# 3. Id stability -- the property, stated three ways
# --------------------------------------------------------------------------


class IdStabilityTests(_RegistryCase):
    def test_reordering_moves_no_id(self) -> None:
        self.configure(self.a, self.b, self.c)
        before = set(self.ids())
        self.configure(self.c, self.b, self.a)
        self.assertEqual(before, set(self.ids()))

    def test_reordering_changes_only_the_order(self) -> None:
        self.configure(self.a, self.b)
        first = self.names()
        self.configure(self.b, self.a)
        self.assertEqual(list(reversed(first)), self.names())

    def test_adding_a_root_moves_no_existing_id(self) -> None:
        self.configure(self.a, self.b)
        before = set(self.ids())
        self.configure(self.a, self.b, self.c)
        self.assertTrue(before.issubset(set(self.ids())))

    def test_removing_a_root_moves_no_surviving_id(self) -> None:
        self.configure(self.a, self.b, self.c)
        keep = {
            entry.model_id
            for entry in self.registry.entries("checkpoint")
            if entry.relative_location != "Gamma.safetensors"
        }
        self.configure(self.a, self.b)
        self.assertEqual(keep, set(self.ids()))

    def test_an_id_resolves_regardless_of_its_position(self) -> None:
        """Resolution searches the roots in order, but an id names exactly one
        root because the root is a digest input. Order decides which directory
        is asked first, never which one answers."""

        self.configure(self.a, self.b, self.c)
        wanted = self.ids()[2]
        _candidate, source = self.registry.resolve("checkpoint", wanted)
        self.configure(self.c, self.b, self.a)
        _again, source_again = self.registry.resolve("checkpoint", wanted)
        self.assertEqual(str(source.resolved), str(source_again.resolved))

    def test_a_remembered_selection_survives_a_reorder(self) -> None:
        """The end-to-end version of the property: the id P0.3e wrote into the
        config file still resolves after the owner drags a root."""

        self.configure(self.a, self.b)
        remembered = self.ids()[0]
        self.configure(self.b, self.a)
        _candidate, source = self.registry.resolve("checkpoint", remembered)
        self.assertTrue(source.resolved.name.startswith("Alpha"))


class StaleSelectionTests(_RegistryCase):
    """The case where an id genuinely DOES move: the root's own path changes.

    This is the only way a remembered selection can go stale, and it is worth
    testing end to end rather than reasoning about, because the owner-visible
    consequence is a dropdown that silently opens unselected.
    """

    def test_moving_a_root_changes_every_id_under_it(self) -> None:
        self.configure(self.a)
        before = set(self.ids())
        moved = Path(str(self.a) + "-moved")
        self.addCleanup(shutil.rmtree, moved, True)
        shutil.move(str(self.a), str(moved))
        self.configure(moved)
        self.assertEqual(set(), before & set(self.ids()))

    def test_the_old_id_stops_resolving_and_says_so(self) -> None:
        self.configure(self.a)
        stale = self.ids()[0]
        moved = Path(str(self.a) + "-moved2")
        self.addCleanup(shutil.rmtree, moved, True)
        shutil.move(str(self.a), str(moved))
        self.configure(moved)
        with self.assertRaises(HeadlessError) as caught:
            self.registry.resolve("checkpoint", stale)
        self.assertTrue(caught.exception.code.startswith("HEADLESS_"))

    def test_a_stale_id_is_refused_rather_than_resolved_to_a_namesake(self) -> None:
        """The dangerous failure would be silently matching a file of the same
        NAME under a different root. The id names the root, so it cannot."""

        self.configure(self.a)
        stale = self.ids()[0]
        (self.b / "Alpha.safetensors").write_bytes(b"")
        self.configure(self.b)
        with self.assertRaises(HeadlessError):
            self.registry.resolve("checkpoint", stale)

    def test_the_surviving_roots_are_untouched_when_one_moves(self) -> None:
        self.configure(self.a, self.b)
        keep = {
            entry.model_id
            for entry in self.registry.entries("checkpoint")
            if entry.relative_location == "Beta.safetensors"
        }
        moved = Path(str(self.a) + "-moved3")
        self.addCleanup(shutil.rmtree, moved, True)
        shutil.move(str(self.a), str(moved))
        self.configure(moved, self.b)
        self.assertTrue(keep.issubset(set(self.ids())))


# --------------------------------------------------------------------------
# 4. Status: partial, and back-compatible
# --------------------------------------------------------------------------


class StatusTests(_RegistryCase):
    def test_one_root_reports_exactly_what_it_always_did(self) -> None:
        status = self.configure(self.a)["checkpoint"]
        self.assertEqual(ROOT_STATUS_READY, status.status)
        self.assertEqual(1, status.entry_count)
        self.assertIsNone(status.reason_code)

    def test_a_dead_root_among_live_ones_is_partial(self) -> None:
        gone = str(self.a.parent / "definitely-not-there")
        statuses = self.registry.configure_tolerantly(
            {"checkpoint": (str(self.a), gone, str(self.b))}
        )
        status = statuses["checkpoint"]
        self.assertEqual(ROOT_STATUS_PARTIAL, status.status)
        self.assertEqual(2, status.entry_count)
        self.assertEqual(
            [ROOT_STATUS_READY, ROOT_STATUS_REFUSED, ROOT_STATUS_READY],
            [root.status for root in status.roots],
        )

    def test_partial_is_unreachable_with_a_single_root(self) -> None:
        """So every pre-P0.4 status keeps its meaning."""

        gone = str(self.a.parent / "definitely-not-there-either")
        statuses = self.registry.configure_tolerantly({"checkpoint": (gone,)})
        self.assertEqual(ROOT_STATUS_REFUSED, statuses["checkpoint"].status)

    def test_every_root_refused_reports_the_first_reason(self) -> None:
        gone_a = str(self.a.parent / "nope-a")
        gone_b = str(self.a.parent / "nope-b")
        statuses = self.registry.configure_tolerantly(
            {"checkpoint": (gone_a, gone_b)}
        )
        status = statuses["checkpoint"]
        self.assertEqual(ROOT_STATUS_REFUSED, status.status)
        self.assertEqual(
            "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE", status.reason_code
        )

    def test_an_unconfigured_role_is_unconfigured(self) -> None:
        statuses = self.configure(self.a)
        self.assertEqual(ROOT_STATUS_NOT_CONFIGURED, statuses["vae"].status)
        self.assertEqual((), statuses["vae"].roots)

    def test_the_projection_carries_no_path(self) -> None:
        import json

        statuses = self.configure(self.a, self.b)
        rendered = json.dumps(
            {role: status.to_dict() for role, status in statuses.items()}
        )
        self.assertNotIn(str(self.a), rendered)
        self.assertNotIn(self.a.name, rendered)

    def test_a_refused_write_leaves_the_previous_configuration_serving(self) -> None:
        self.configure(self.a)
        before = self.ids()
        with self.assertRaises(HeadlessError):
            self.registry.configure(
                {"checkpoint": (str(self.b), str(self.a.parent / "missing"))}
            )
        self.assertEqual(before, self.ids())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_ORDERED_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
