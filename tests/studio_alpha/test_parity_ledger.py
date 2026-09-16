"""The route ledger is regenerated here, so it cannot quietly go stale.

WP0.3 asks for a ledger that FAILS when a visible frontend route has no
disposition. This suite is that gate: it rebuilds the ledger from source and
compares it to the committed documents, so adding a `fetch("/studio/new")` and
committing without regenerating breaks the canonical run rather than shipping a
document that describes a product that no longer exists.

The coverage assertions matter as much as the comparison. The scan this ledger
replaces silently skipped a file and reported a smaller, tidier, wrong number;
what makes this one trustworthy is not that it found more, but that it can say
which files it read.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts"))

import parity_ledger  # noqa: E402

EXPECTED_PARITY_LEDGER_TESTS = 20

LEDGER_JSON = APP_ROOT / "docs" / "15_PARITY_LEDGER.json"
LEDGER_MD = APP_ROOT / "docs" / "15_PARITY_LEDGER.md"


class GeneratedLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.built = parity_ledger.build()
        self.committed = json.loads(LEDGER_JSON.read_text(encoding="utf-8"))

    def test_the_committed_ledger_matches_the_source(self) -> None:
        """Regenerate and commit. `scripts/parity_ledger.py` writes both files."""

        self.assertEqual(
            self.committed, self.built,
            "docs/15_PARITY_LEDGER.json is stale -- run "
            "`venv/Scripts/python.exe scripts/parity_ledger.py`")

    def test_the_markdown_matches_the_json(self) -> None:
        self.assertEqual(LEDGER_MD.read_text(encoding="utf-8"),
                         parity_ledger.render(self.built))

    def test_the_markdown_actually_presents_what_the_ledger_holds(self) -> None:
        """`render()` silently dropped the entire control half once.

        The comparison above cannot catch that: it checks the document against
        `render()`, so when both omit the same section they agree perfectly and
        the suite stays green. The JSON carried 146 control rows while the
        document a human reads carried none.

        This asserts against the DATA instead -- every section the ledger holds
        has to reach the page.
        """

        text = LEDGER_MD.read_text(encoding="utf-8")
        self.assertIn("## Routes", text)
        self.assertIn("## Generate-tab controls", text)
        for row in self.built["routes"]:
            self.assertIn(f"`{row['path']}`", text)
        for row in self.built["controls"]:
            if row["key"]:
                self.assertIn(f"`{row['key']}`", text)

    def test_every_route_has_a_disposition(self) -> None:
        """The gate. An unresolved row is a route nobody has ruled on."""

        unresolved = [row["path"] for row in self.built["routes"]
                      if row["status"] == "unresolved"]
        self.assertEqual(
            [], unresolved,
            "add these to DISPOSITIONS/PREFIX_DISPOSITIONS with the packet "
            "that owns them, or wire them")

    def test_every_status_is_one_of_the_declared_kinds(self) -> None:
        allowed = {"service-backed", "capability-gated", "prefix-service",
                   "scan-uncertain", "shadowed", "missing", "retired",
                   "unresolved"}
        for row in self.built["routes"]:
            with self.subTest(path=row["path"]):
                self.assertIn(row["status"], allowed)

    def test_every_row_names_who_calls_it(self) -> None:
        """A route with no caller is a scan artefact, not a finding."""

        for row in self.built["routes"]:
            with self.subTest(path=row["path"]):
                self.assertTrue(row["callers"])


class CoverageTests(unittest.TestCase):
    """What the previous scan could not say about itself."""

    def test_every_frontend_file_was_read(self) -> None:
        expected = {item.name for item in parity_ledger.frontend_files()}
        _, scanned = parity_ledger.frontend_references()
        self.assertEqual(expected, set(scanned))

    def test_the_nul_separated_file_is_not_skipped(self) -> None:
        """The regression guard, named after the defect.

        `wildcard-preview.js` uses NUL as a cache-key separator, so `file(1)`
        and every text-mode grep call it binary and pass over it -- taking two
        real call sites with it, without an error. Reading bytes and decoding
        explicitly is the whole reason this file appears at all.
        """

        references, scanned = parity_ledger.frontend_references()
        self.assertIn("wildcard-preview.js", scanned)
        callers = {path for path, names in references.items()
                   if "wildcard-preview.js" in names}
        self.assertIn("/studio/wildcard_preview", callers)
        self.assertIn("/studio/wildcard_content", callers)

    def test_an_undecodable_file_raises_rather_than_being_skipped(self) -> None:
        """Fail loudly. A ledger that omits an input is worse than none."""

        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "broken.js"
            bad.write_bytes(b'fetch("/studio/\xff\xfe");')
            with self.assertRaises(parity_ledger.UnreadableSource):
                parity_ledger.read_source(bad)


class ClassifierTests(unittest.TestCase):
    """Guards for two wrong answers this classifier actually produced.

    Both were plausible, both were confidently reported, and both were the
    same mistake in different clothes: treating ROUTING as ANSWERING.
    """

    def test_a_delegating_prefix_is_not_an_owner(self) -> None:
        """`presentation.py` forwards /studio/ and /sdapi/v1/ to the adapter.

        Counting those as owners gave two separate false results: /studio/
        resolved every route in the product (a ledger with nothing missing),
        and /sdapi/v1 made the adapter's own /sdapi/v1/options branch look
        shadowed by the file that forwards to it.
        """

        source = parity_ledger.read_source(
            parity_ledger.SERVERS["presentation.py"][0])
        routing = parity_ledger.delegating_prefixes(source)
        self.assertIn("/studio", routing)
        self.assertIn("/sdapi/v1", routing)

    def test_a_branch_nested_in_its_own_router_is_not_shadowed(self) -> None:
        """`/studio/lexicon/file/save` sits INSIDE the adapter's own
        `startswith("/studio/lexicon/")` block. That is ordinary dispatch.

        Ignoring the owning file reported nine shadowed routes and every one
        was wrong."""

        shadowed = parity_ledger.shadowed_paths(
            {"/studio/lexicon/file/save": "source_api_adapter.py"},
            {"/studio/lexicon": "source_api_adapter.py"})
        self.assertEqual({}, shadowed)

    def test_a_cross_file_shadow_is_still_reported(self) -> None:
        """The real defect class must survive the fix for the false positives."""

        shadowed = parity_ledger.shadowed_paths(
            {"/studio/gallery/pick-folder": "source_api_adapter.py"},
            {"/studio/gallery": "gallery_service.py"})
        self.assertIn("/studio/gallery/pick-folder", shadowed)
        self.assertIn("UNREACHABLE", shadowed["/studio/gallery/pick-folder"])

    def test_a_property_read_is_not_mistaken_for_a_stub(self) -> None:
        """`/studio/task_id` returns `{"task_id": self.active_job_id}` -- a
        read, not a call -- so a "contains no call" rule files a working route
        as a hardcoded stub. `scan-uncertain` is the honest answer."""

        import ast as _ast

        body = _ast.parse("x = {'task_id': self.active_job_id}").body
        status, _note = parity_ledger._body_kind(body)
        self.assertEqual("scan-uncertain", status)


class ControlLedgerTests(unittest.TestCase):
    """The control half's gate, and a guard for its own false positives."""

    def setUp(self) -> None:
        self.rows = parity_ledger.controls()

    def test_every_visible_control_has_a_disposition(self) -> None:
        """WP0.3's gate for controls.

        An `unbound` row is a control an owner can see and click that nothing
        in the frontend even names. That is the dead-control class this whole
        program is built to eliminate, so it fails the run rather than being
        counted in a report.
        """

        unbound = [row["key"] for row in self.rows
                   if row["status"] == "unbound"]
        self.assertEqual(
            [], unbound,
            "wire these, hide them, or add a CONTROL_DISPOSITIONS entry naming "
            "the packet that owns them")

    def test_attribute_keyed_controls_are_not_judged_by_their_value(self) -> None:
        """The aspect-ratio buttons are keyed `1:1`, `16:9`, `768`.

        They are selected collectively by `[data-ar]` / `[data-base]`, so
        asking whether the string "16:9" appears in a script is meaningless.
        It reported twelve confident false findings before the fix.
        """

        delegated = {row["key"] for row in self.rows
                     if row["status"] == "delegated"}
        for key in ("1:1", "16:9", "768"):
            with self.subTest(key=key):
                self.assertIn(key, delegated)

    def test_the_generate_region_is_found_and_bounded(self) -> None:
        """A parser, not a line regex: four control tags span lines.

        If the region markers moved, this collapses to 0 or swallows the whole
        document -- both of which would make every other control assertion
        vacuous.
        """

        self.assertGreater(len(self.rows), 100)
        self.assertLess(len(self.rows), 400)
        keys = {row["key"] for row in self.rows}
        self.assertIn("paramPrompt", keys)
        self.assertNotIn("galleryFolderBrowse", keys)


class SubmissionAnchorsAreRealTests(unittest.TestCase):
    """The guard that would have caught the anchor bug. AR6.7.

    `_SUBMISSION_ANCHORS` listed `function _hiresGroup`,
    `function _autoDetailGroup` and `function _variationGroup` while the source
    declares all three as arrow consts. Three of four anchors matched NOTHING,
    and the ledger still produced the right answer -- because the dead
    `const params` collector, which was also in the list, read the same ids.

    So a broken anchor was invisible: the ledger was correct by accident,
    through a route no install takes. That is the worst way for a
    build-gating tool to be right.
    """

    def app_js(self) -> str:
        return (parity_ledger.FRONTEND / "app.js").read_text(encoding="utf-8")

    def test_every_anchor_matches_the_source(self) -> None:
        source = self.app_js()
        for anchor in parity_ledger._SUBMISSION_ANCHORS:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, source,
                              f"anchor {anchor!r} matches nothing, so the ids "
                              f"it should cover are credited elsewhere")

    def test_every_anchor_extracts_a_non_trivial_block(self) -> None:
        """Matching is not enough: an anchor whose block cannot be balanced
        yields nothing useful. `_preparedPrompt` had to be given a braced body
        for exactly this reason."""

        source = self.app_js()
        for anchor in parity_ledger._SUBMISSION_ANCHORS:
            with self.subTest(anchor=anchor):
                start = source.find(anchor)
                block = parity_ledger._balanced_block(source, start)
                self.assertGreater(
                    len(block), len(anchor) + 20,
                    f"anchor {anchor!r} extracts an empty or trivial block")

    def test_the_dead_collector_is_not_a_submission_route(self) -> None:
        """`const params = {` sits below the lifecycle `return`. Counting it
        is how this ledger reported both Batch controls as healthy until the
        day they were deleted."""

        self.assertNotIn("const params = {", parity_ledger._SUBMISSION_ANCHORS)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PARITY_LEDGER_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
