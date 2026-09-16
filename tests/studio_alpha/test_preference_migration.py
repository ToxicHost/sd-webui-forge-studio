"""D1 stage 3 -- why there is no migration, pinned so the answer cannot rot.

Stage 3 was planned as the migration of legacy `localStorage` values into the
durable store stage 2 built. It is not implemented, and this suite is the
reason rather than an excuse for it.

THE SOURCE DOES NOT EXIST. `prefs.js` carries a `LEGACY_MAP` of nine
`localStorage` keys it would migrate from. Nothing in this product writes any
of them -- not the frontend, not the server, not any commit in this
repository's history, and not the reference implementation the frontend was
adopted from. They are the residue of a version older than both, and the
migration path shipped already historical. A migration would read keys no code
has ever created.

THE HAZARD ALSO DOES NOT EXIST. The handoff that scoped stage 3 warned that a
naive migration "finds nothing and silently writes an empty file over whatever
the owner had". `_migrateLegacyIfNeeded` guards its POST on having found
something, so an empty migration sends nothing at all. Both halves are asserted
below, because the second is only safe while that guard stands.

WHAT IS ACTUALLY LOST is a different thing entirely, it is much larger, and it
is not a migration problem: roughly thirty settings the frontend keeps in
`localStorage` -- theme, layout, panel widths, tool settings, locale, tour
state -- vanish on every launch, because `"port": 0` gives each launch a new
ephemeral port and `localStorage` is scoped to an origin, port included.
Observed in a browser: Evidence/r1-d1-origin-scope/. That belongs to the owner
to decide on, not to a test.

If someone later adds a writer for one of those keys, the first test here fails
and migration becomes required again -- on purpose. That is the whole value of
writing this down as a test instead of a paragraph.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.preferences import DefaultsStore, PreferenceStore  # noqa: E402

EXPECTED_MIGRATION_TESTS = 9

FRONTEND_ROOT = APP_ROOT / "forge_studio" / "frontend"
PREFS_JS = FRONTEND_ROOT / "prefs.js"

#: Files that could plausibly write browser storage or the state root.
SEARCHED = (
    tuple(FRONTEND_ROOT.rglob("*.js"))
    + tuple(FRONTEND_ROOT.rglob("*.html"))
    + tuple((APP_ROOT / "forge_studio").glob("*.py"))
)


def _without_comments(path: Path) -> str:
    """Strip line comments before searching.

    Both spellings, because this suite reads JavaScript and Python. Eight
    separate occasions in this codebase have had a check pass or fail on prose
    that documented the very rule being checked -- including a docstring that
    named the thing it forbade.
    """

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    return re.sub(r"//.*$", "", text, flags=re.MULTILINE)


def _legacy_keys() -> tuple[str, ...]:
    """The `localStorage` names a migration would read, from prefs.js itself.

    Derived rather than copied. A hardcoded list here would keep passing after
    someone added a tenth entry, which is the one case where this suite's
    conclusion would need revisiting.
    """

    source = _without_comments(PREFS_JS)
    return tuple(
        sorted(set(re.findall(r'legacy:\s*"([^"]+)"', source)))
    )


class MigrationSourceTests(unittest.TestCase):
    def test_the_legacy_table_was_found(self) -> None:
        """If the table moved or was renamed, every other test here would pass
        vacuously against an empty set."""

        self.assertEqual(9, len(_legacy_keys()))

    def test_nothing_in_this_product_writes_a_legacy_key(self) -> None:
        """The load-bearing fact. A migration reads from these names; if no
        code writes them, there is nothing to read."""

        writers: dict[str, list[str]] = {}
        for key in _legacy_keys():
            pattern = re.compile(
                r"setItem\(\s*['\"]" + re.escape(key) + r"['\"]"
            )
            for path in SEARCHED:
                if pattern.search(_without_comments(path)):
                    writers.setdefault(key, []).append(path.name)
        self.assertEqual({}, writers)

    def test_prefs_js_only_reads_the_legacy_keys(self) -> None:
        """The discriminating half. The test above would also pass if the
        legacy table had been deleted outright -- which would be a different
        change with different consequences, and not what is being asserted."""

        source = _without_comments(PREFS_JS)
        self.assertIn("_lsGet(m.legacy)", source)
        self.assertNotIn("_lsSet(m.legacy", source)

    def test_an_empty_migration_cannot_post(self) -> None:
        """The hazard stage 3 was scoped around, asserted where it lives.

        `_migrateLegacyIfNeeded` may only send a request when it has actually
        collected something. Remove that guard and an install with nothing to
        migrate would POST `{}` -- which, before the store learned to treat an
        empty merge as a no-op, would have created an empty preferences file
        and destroyed the signal that says nothing was ever written.
        """

        source = _without_comments(PREFS_JS)
        body = source[source.index("async function _migrateLegacyIfNeeded"):]
        body = body[: body.index("function _scheduleFlush")]
        self.assertIn("var keys = Object.keys(migrate);", body)
        guard = body.index("if (keys.length)")
        self.assertLess(guard, body.index("_postKeys(migrate)"))


class EmptyWriteTests(unittest.TestCase):
    """A write of nothing must not become a file.

    Whatever stage 3 eventually does, it will have to tell "this install has
    never written preferences" from "this install wrote preferences and they
    are empty". The only thing that distinguishes them is whether the file
    exists, so nothing may create it by accident.
    """

    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.root = Path(self._directory.name) / "ForgeStudio"
        self.addCleanup(self._directory.cleanup)

    def test_an_empty_merge_does_not_create_the_document(self) -> None:
        store = PreferenceStore(self.root)
        self.assertEqual({}, store.merge({}))
        self.assertFalse(self.root.exists())

    def test_an_empty_merge_leaves_an_existing_document_alone(self) -> None:
        store = PreferenceStore(self.root)
        store.merge({"session_limit": 15})
        before = store.path.read_bytes()
        self.assertEqual({"session_limit": 15}, store.merge({}))
        self.assertEqual(before, store.path.read_bytes())

    def test_an_empty_merge_after_a_reset_does_not_resurrect_the_file(
        self,
    ) -> None:
        """`?reset` DELETEs, and the page then reloads and may post again. The
        file must stay gone until there is something to put in it."""

        store = PreferenceStore(self.root)
        store.merge({"session_limit": 15})
        store.clear()
        store.merge({})
        self.assertFalse(store.path.exists())

    def test_saving_empty_defaults_still_writes(self) -> None:
        """The asymmetry is deliberate. A merge of nothing changes nothing; a
        REPLACE with nothing is an owner emptying their saved defaults, and
        refusing to record that would lose an intentional act."""

        store = DefaultsStore(self.root)
        store.replace({"steps": 30})
        self.assertEqual({}, store.replace({}))
        self.assertTrue(store.path.is_file())
        self.assertEqual({}, DefaultsStore(self.root).read())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_MIGRATION_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
