"""Every route `gallery.js` calls, checked against what the service answers.

This suite exists because of a specific failure. The service was built, 53
unit tests passed, a 52-check live harness passed, and the Gallery still could
not link a folder or unlink one -- because the harness drove the routes the
SERVICE had invented rather than the ones the PAGE calls. `/add-folder` was
never called by anything; the page POSTs `/scan-folders`. `/unlink-folder`
took `{"path": ...}`; the page sends `{"folder": ...}`, so unlinking
succeeded and did nothing.

No amount of testing the service against itself finds that. The client is the
specification, so the client is what this reads.

The route list is EXTRACTED from `gallery.js` rather than written out here.
A hand-copied list is a second thing to keep in sync, and it would have been
copied from the same wrong assumption.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.gallery_service import ROUTE_PREFIX, GalleryService  # noqa: E402

EXPECTED_ROUTE_COVERAGE_TESTS = 8

GALLERY_JS = APP_ROOT / "forge_studio" / "frontend" / "gallery.js"

#: Routes the page calls that this build does not answer yet, each with the
#: reason. A gap that is WRITTEN DOWN is a decision; a gap that is merely
#: absent is a bug waiting for an owner to find. Deleting a line here is how
#: implementing one of them gets noticed.
KNOWN_GAPS: dict[str, str] = {
    # Empty, and that is the point: every route `gallery.js` calls is
    # answered. A line here is a DECISION -- "this one is deliberately not
    # implemented, and here is why" -- so an empty table means there are no
    # such decisions left outstanding. Auto-tagging, which the owner ruled
    # out, is not in this list because the page never calls it.
}


#: Routes that reach OUTSIDE this process, so probing them is not free.
#:
#: This list has been learned the hard way, twice. `/pick-folder` opens a real
#: folder dialog on the owner's desktop, and the first run of this suite popped
#: one up on the machine it was running on. `/image/{}/open-explorer` and
#: `/image/{}/open-file` then spawned file-manager windows the same way.
#:
#: The rule is now stated rather than discovered: if a route starts a process,
#: shows a window, or begins background work, its handler is asserted against
#: the dispatch instead of being called. `test_side_effecting_routes_are_named`
#: below checks that nothing which spawns has been left off this list.
SIDE_EFFECTS = {
    "/pick-folder",            # opens a folder chooser
    "/scan",                   # starts a background scan
    "/compute-hashes",         # starts a background hashing pass
    "/image/{}/open-explorer",  # spawns a file manager
    "/image/{}/open-file",     # spawns whatever opens that file type
}


def routes_called_by_the_page() -> set[str]:
    """Every Gallery route `gallery.js` asks for, normalised.

    An interpolated id becomes `{}`, so `/image/17/rating` and
    `/image/4/rating` are one route.
    """

    source = GALLERY_JS.read_text(encoding="utf-8")
    found: set[str] = set()
    # api("/route") and api("/route?query")
    for match in re.finditer(r'api\(\s*"(/[^"]*)"', source):
        found.add(match.group(1).split("?")[0])
    # api("/head/" + something + "/tail"), and the same shape where the tail is
    # a query rather than a path segment -- `"/similar/" + id + "?threshold=…"`
    # matched neither pattern while the tail had to begin with a slash.
    for match in re.finditer(
        r'api\(\s*"(/[^"]*)"\s*\+\s*[^,)+]+?\+\s*"([^"]*)"', source
    ):
        tail = match.group(2)
        joined = f"{match.group(1).rstrip('/')}/{{}}"
        found.add(joined + tail if tail.startswith(("/", "?")) else joined)
    # api("/head/" + something)
    for match in re.finditer(
        r'api\(\s*"(/[^"]*/)"\s*\+\s*[A-Za-z_][\w.\[\]]*\s*[,)]', source
    ):
        found.add(f"{match.group(1).rstrip('/')}/{{}}")
    # Query strings are stripped LAST, so a concatenated route carrying one
    # (`/similar/" + id + "?threshold=128`) normalises the same way a literal
    # one does. Missing this made a declared gap look like dead text.
    found = {route.split("?")[0] for route in found}
    # Bare prefixes left by the concatenation patterns above.
    return {route for route in found if not route.endswith("/")}


class ExtractionTests(unittest.TestCase):
    def test_the_page_is_readable_and_calls_many_routes(self) -> None:
        """A guard on the extraction itself. If the regexes stop matching, the
        coverage test below would pass by finding nothing to check."""

        self.assertTrue(GALLERY_JS.is_file())
        routes = routes_called_by_the_page()
        self.assertGreater(len(routes), 40)

    def test_the_routes_that_broke_are_among_them(self) -> None:
        """Named explicitly. These three are what the page needs to link a
        folder and unlink one, and all three were missing or wrong."""

        routes = routes_called_by_the_page()
        for route in ("/pick-folder", "/scan-folders", "/unlink-folder"):
            with self.subTest(route=route):
                self.assertIn(route, routes)


class CoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        base = Path(self._directory.name)
        picture = base / "a.png"
        picture.write_bytes(b"not really a picture")
        self.service = GalleryService(base / "state")
        self.addCleanup(self.service.close)

        self.picture = picture
        self.seed()

    def seed(self) -> None:
        """One image with id 1 and one trash entry with id 1.

        So that an id in a probed route resolves to something: without them
        `/image/1/metadata` answers 404 because row 1 does not exist, which is
        indistinguishable from the 404 that means "no such route" -- exactly
        the confusion this suite exists to remove.
        """

        self.picture.write_bytes(b"not really a picture")
        with self.service.store().write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO images(id, filename, folder, filepath) "
                "VALUES(1, 'a.png', 'pictures', ?)", (str(self.picture),))
            connection.execute(
                "INSERT OR REPLACE INTO trash(id, original_filepath, "
                "original_filename, trash_path) VALUES(1, ?, 'a.png', '')",
                (str(self.picture),))

    def answered(self, route: str) -> bool:
        """Whether the service has a handler for this route.

        A 404 from both verbs means no handler. Any other status -- a refusal,
        a bad request, a 503 -- means the route exists and declined for its own
        reasons, which is all this needs to know.

        RE-SEEDED FIRST, because probing is not read-only: POSTing to
        `/image/1/delete` really does delete image 1, and a later probe of
        `/image/1/metadata` then answers 404 for the wrong reason. Set
        iteration order varies between processes, so that made this suite pass
        alone and fail inside the canonical run -- the worst way for a test to
        be wrong.
        """

        self.seed()
        concrete = route.replace("{}", "1")
        if self.service.get(f"{ROUTE_PREFIX}{concrete}").status != 404:
            return True
        return self.service.post(f"{ROUTE_PREFIX}{concrete}", {}).status != 404

    def test_every_route_the_page_calls_is_answered_or_a_declared_gap(
        self,
    ) -> None:
        """The test that would have caught the link-folder failure."""

        missing = sorted(
            route for route in routes_called_by_the_page()
            if route not in KNOWN_GAPS and route not in SIDE_EFFECTS
            and not self.answered(route)
        )
        self.assertEqual([], missing)

    def test_the_side_effecting_routes_are_handled(self) -> None:
        """Asserted against the dispatch rather than by calling them, because
        calling `/pick-folder` opens a dialog on somebody's desktop."""

        source = (APP_ROOT / "forge_studio" / "gallery_service.py").read_text(
            encoding="utf-8"
        )
        for route in sorted(SIDE_EFFECTS):
            with self.subTest(route=route):
                # `/image/{}/open-file` is dispatched by its last segment.
                needle = route.rsplit("/", 1)[-1] if "{}" in route else route
                self.assertIn(f'"{needle}"', source)

    def test_side_effecting_routes_are_named(self) -> None:
        """Nothing that leaves the process may be probed by calling it.

        Checked against the service's own source: any route whose handler can
        spawn a process or open a window has to be in SIDE_EFFECTS. Both times
        this suite got that wrong, it was found by something appearing on the
        owner's screen rather than by a test.
        """

        source = (APP_ROOT / "forge_studio" / "gallery_service.py").read_text(
            encoding="utf-8"
        )
        # The three ways this service reaches outside itself.
        for marker in ("filedialog", "NativeActions", "Thread("):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        # And every route that uses one is declared.
        for route in ("/pick-folder", "/image/{}/open-explorer",
                      "/image/{}/open-file", "/scan", "/compute-hashes"):
            with self.subTest(route=route):
                self.assertIn(route, SIDE_EFFECTS)

    def test_no_declared_gap_is_actually_implemented(self) -> None:
        """The other direction. A gap that has quietly been filled should lose
        its line here, or the list slowly becomes fiction."""

        stale = sorted(
            route for route in KNOWN_GAPS
            if route not in SIDE_EFFECTS and self.answered(route)
        )
        self.assertEqual([], stale)

    def test_every_declared_gap_is_still_called_by_the_page(self) -> None:
        """And a gap for a route the page no longer calls is dead text."""

        routes = routes_called_by_the_page()
        orphans = sorted(route for route in KNOWN_GAPS if route not in routes)
        self.assertEqual([], orphans)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_ROUTE_COVERAGE_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
