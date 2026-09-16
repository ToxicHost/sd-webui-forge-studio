"""The directory picker: what it refuses, and what it must not leak.

This is the one Studio surface allowed to put a real filesystem path in a
response body. Everything else is opaque by construction -- a model is three
catalogue ids, a result is a handle -- so the picker is a deliberate widening
of a boundary the rest of the product holds absolutely, and these tests are
where that widening is audited rather than assumed.

Most of them are negatives, because that is where the value is. The client
sends a path exactly ONCE, at `resolve`; after that every move is a NAME
checked against a scan taken in the same request. That is what makes `..`,
`C:foo`, `\\\\?\\`, `%2f`, a decomposed spelling, a reserved device name and a
trailing dot stop being separate attacks and start being one equality test.

Platform behaviour this host cannot physically produce -- a POSIX tree, an NFS
mount, a junction that resolves elsewhere -- is INJECTED through the
`Filesystem` protocol, because a test that skips proves nothing about the
platform it skipped for.

SCOPE: MINIMAL_RUNTIME_SCOPE. Real directories under a disposable temporary
root; a simulated filesystem for everything else. No owner directory is read,
written or used as a fixture.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.fs_browser import (  # noqa: E402
    BROWSE_DIRECTORY_CHANGED,
    BROWSE_ENTRY_UNKNOWN,
    BROWSE_HANDLE_EXPIRED,
    BROWSE_HANDLE_UNKNOWN,
    BROWSE_HANDLE_WRONG_PURPOSE,
    BROWSE_NOT_A_DIRECTORY,
    BROWSE_NO_PARENT,
    BROWSE_REDIRECT_REFUSED,
    HANDLE_TTL_SECONDS,
    MAX_OPEN_HANDLES,
    PURPOSE_MODEL_ROOT,
    DirectoryBrowser,
)
from forge_headless.fs_facts import DirFacts, EntryFacts  # noqa: E402
from forge_studio.fs_service import (  # noqa: E402
    BROWSE_BUSY,
    BROWSE_CONCURRENCY,
    BROWSE_DISABLED,
    FilesystemService,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_FS_TESTS = 47


class FakeFilesystem:
    """A filesystem described by a table, so any platform can be simulated.

    Directories are keys; each maps to a list of `EntryFacts`. `links` maps a
    path to where `realpath` sends it, which is how a junction, a symlink and
    a macOS firmlink are all expressed without needing the privilege to create
    one.
    """

    #: POSIX rules, because the trees below describe a POSIX host. Without
    #: it the browser would parse them with the HOST flavour and refuse
    #: '/home/owner' as drive-relative -- testing this box's rules instead
    #: of the ones being simulated.
    pure_path = PurePosixPath

    def __init__(self, tree, *, links=None, home=None, unreadable=()):
        self.tree = dict(tree)
        self.links = dict(links or {})
        self._home = home
        self.unreadable = set(unreadable)
        self.scans: list[str] = []

    def stat_dir(self, path: Path) -> DirFacts:
        key = str(path)
        if key not in self.tree:
            return DirFacts(exists=False, is_dir=False)
        # A stable synthetic identity, so fingerprint comparison is exercised.
        return DirFacts(
            exists=True, is_dir=True, fingerprint=(1, abs(hash(key)) % 100000)
        )

    def scan(self, path: Path):
        key = str(path)
        self.scans.append(key)
        if key in self.unreadable:
            return ()
        return tuple(self.tree.get(key, ()))

    def realpath(self, path):
        # PurePosixPath, not Path: on Windows a concrete Path would render
        # '/' as '\' and the tree lookup would miss every key.
        key = str(path)
        if key in self.links:
            return PurePosixPath(self.links[key])
        return PurePosixPath(key)

    def home(self):
        return PurePosixPath(self._home) if self._home else None


class FakeWindowsFilesystem(FakeFilesystem):
    """The same table under Windows path rules.

    A separate class rather than a parameter, because the two flavours
    disagree about what "/" even means -- a fixture trying to be both would
    be asserting neither.
    """

    pure_path = PureWindowsPath

    def realpath(self, path):
        key = str(path)
        return PureWindowsPath(self.links.get(key, key))


def _dir(name: str) -> EntryFacts:
    return EntryFacts(name=name, is_dir=True, is_redirecting=False)


def _file(name: str, size: int = 10) -> EntryFacts:
    return EntryFacts(name=name, is_dir=False, is_redirecting=False, size_bytes=size)


def _link(name: str) -> EntryFacts:
    return EntryFacts(name=name, is_dir=True, is_redirecting=True)


POSIX_TREE = {
    "/": [_dir("home"), _dir("mnt"), _dir("proc")],
    "/home": [_dir("owner")],
    "/home/owner": [_dir("Models"), _dir("Documents"), _file("notes.txt")],
    "/home/owner/Models": [_file("Alpha.safetensors", 4096), _dir("sdxl")],
    "/home/owner/Models/sdxl": [_file("Beta.safetensors", 8192)],
    "/mnt": [_dir("nas")],
    "/mnt/nas": [_file("Remote.safetensors")],
}


class _RealRootCase(unittest.TestCase):
    """A disposable root. Never an owner directory, even read-only."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="fs-browser-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "Models").mkdir()
        (self.root / "Models" / "Alpha.safetensors").write_bytes(b"")
        (self.root / "Docs").mkdir()
        (self.root / "readme.txt").write_bytes(b"x")
        self.browser = DirectoryBrowser()

    def open_root(self):
        return self.browser.open_path(str(self.root))


# --------------------------------------------------------------------------
# 1. Handles are authority, and the client cannot mint one
# --------------------------------------------------------------------------


class HandleTests(_RealRootCase):
    def test_a_forged_handle_is_refused(self) -> None:
        with self.assertRaises(HeadlessError) as caught:
            self.browser.listing("clearly-not-a-real-handle")
        self.assertEqual(BROWSE_HANDLE_UNKNOWN, caught.exception.code)

    def test_a_path_shaped_string_is_not_a_handle(self) -> None:
        """The whole point of opaque handles: a client that knows a path
        cannot turn that knowledge into navigation authority."""

        for forged in (str(self.root), "C:/", "/", "../..", str(self.root / "Docs")):
            with self.assertRaises(HeadlessError) as caught:
                self.browser.listing(forged)
            self.assertEqual(BROWSE_HANDLE_UNKNOWN, caught.exception.code)

    def test_an_expired_handle_is_refused(self) -> None:
        clock = {"now": 0.0}
        browser = DirectoryBrowser(clock=lambda: clock["now"])
        listing = browser.open_path(str(self.root))
        clock["now"] = HANDLE_TTL_SECONDS + 1
        with self.assertRaises(HeadlessError) as caught:
            browser.listing(listing.handle)
        self.assertEqual(BROWSE_HANDLE_EXPIRED, caught.exception.code)

    def test_a_wrong_purpose_handle_is_refused(self) -> None:
        """A handle minted for choosing a model root must not be replayable
        against a surface added later."""

        listing = self.open_root()
        with self.assertRaises(HeadlessError) as caught:
            self.browser.listing(listing.handle, purpose="something_else")
        self.assertEqual(BROWSE_HANDLE_WRONG_PURPOSE, caught.exception.code)

    def test_invalidation_drops_every_handle(self) -> None:
        first = self.open_root()
        second = self.browser.open_path(str(self.root / "Models"))
        self.browser.invalidate()
        for token in (first.handle, second.handle):
            with self.assertRaises(HeadlessError):
                self.browser.listing(token)

    def test_the_handle_table_is_bounded(self) -> None:
        """A client cannot grow server memory by navigating."""

        for index in range(MAX_OPEN_HANDLES + 20):
            target = self.root / f"d{index}"
            target.mkdir()
            self.browser.open_path(str(target))
        self.assertLessEqual(
            len(self.browser._handles), MAX_OPEN_HANDLES  # noqa: SLF001
        )

    def test_a_handle_survives_ordinary_reuse(self) -> None:
        listing = self.open_root()
        for _ in range(5):
            again = self.browser.listing(listing.handle)
            self.assertEqual(listing.path, again.path)

    def test_a_directory_replaced_between_requests_is_caught(self) -> None:
        """Revalidation at USE, not merely at mint. A directory swapped for
        something else between two requests must not be followed."""

        tree = dict(POSIX_TREE)
        fake = FakeFilesystem(tree)
        browser = DirectoryBrowser(filesystem=fake)
        listing = browser.open_path("/home/owner/Models")
        # The same path, a different identity.
        original = fake.stat_dir

        def swapped(path: Path) -> DirFacts:
            facts = original(path)
            if str(path) == "/home/owner/Models":
                return DirFacts(exists=True, is_dir=True, fingerprint=(9, 9))
            return facts

        fake.stat_dir = swapped  # type: ignore[assignment]
        with self.assertRaises(HeadlessError) as caught:
            browser.listing(listing.handle)
        self.assertEqual(BROWSE_DIRECTORY_CHANGED, caught.exception.code)

    def test_a_deleted_directory_is_caught_at_use(self) -> None:
        target = self.root / "vanishing"
        target.mkdir()
        listing = self.browser.open_path(str(target))
        shutil.rmtree(target, ignore_errors=True)
        with self.assertRaises(HeadlessError) as caught:
            self.browser.listing(listing.handle)
        self.assertEqual(BROWSE_DIRECTORY_CHANGED, caught.exception.code)


# --------------------------------------------------------------------------
# 2. Navigation is by name, so a whole attack class does not exist
# --------------------------------------------------------------------------


class NavigationTests(_RealRootCase):
    def test_descend_enters_a_real_child(self) -> None:
        listing = self.open_root()
        child = self.browser.descend(listing.handle, "Models")
        self.assertEqual("Models", child.display_name)

    def test_a_traversal_name_is_simply_not_here(self) -> None:
        """`..` is refused because it is not a name this scan returned -- not
        because a parser recognised it as dangerous."""

        listing = self.open_root()
        for attempt in ("..", ".", "../..", "..\\..", "%2e%2e"):
            with self.assertRaises(HeadlessError) as caught:
                self.browser.descend(listing.handle, attempt)
            self.assertEqual(BROWSE_ENTRY_UNKNOWN, caught.exception.code)

    def test_a_separator_in_a_name_is_refused(self) -> None:
        listing = self.open_root()
        for attempt in ("Models/Alpha.safetensors", "Models\\x", "/etc/passwd"):
            with self.assertRaises(HeadlessError) as caught:
                self.browser.descend(listing.handle, attempt)
            self.assertEqual(BROWSE_ENTRY_UNKNOWN, caught.exception.code)

    def test_an_absolute_path_as_a_child_is_refused(self) -> None:
        listing = self.open_root()
        for attempt in ("C:/Windows", r"\\?\C:\Windows", r"\\server\share"):
            with self.assertRaises(HeadlessError):
                self.browser.descend(listing.handle, attempt)

    def test_a_file_is_not_descendable(self) -> None:
        listing = self.open_root()
        with self.assertRaises(HeadlessError) as caught:
            self.browser.descend(listing.handle, "readme.txt")
        self.assertEqual(BROWSE_NOT_A_DIRECTORY, caught.exception.code)

    def test_a_missing_child_is_refused(self) -> None:
        listing = self.open_root()
        with self.assertRaises(HeadlessError) as caught:
            self.browser.descend(listing.handle, "no-such-folder")
        self.assertEqual(BROWSE_ENTRY_UNKNOWN, caught.exception.code)

    def test_ascend_reaches_the_parent(self) -> None:
        listing = self.open_root()
        child = self.browser.descend(listing.handle, "Models")
        back = self.browser.ascend(child.handle)
        self.assertEqual(str(self.root), back.path)

    def test_the_top_has_no_parent(self) -> None:
        fake = FakeFilesystem(POSIX_TREE)
        browser = DirectoryBrowser(filesystem=fake)
        listing = browser.open_path("/")
        with self.assertRaises(HeadlessError) as caught:
            browser.ascend(listing.handle)
        self.assertEqual(BROWSE_NO_PARENT, caught.exception.code)

    def test_repeated_listing_is_stable(self) -> None:
        listing = self.open_root()
        names = [entry.name for entry in self.browser.listing(listing.handle).entries]
        for _ in range(3):
            again = self.browser.listing(listing.handle)
            self.assertEqual(names, [entry.name for entry in again.entries])

    def test_directories_sort_before_files(self) -> None:
        listing = self.open_root()
        kinds = [entry.is_dir for entry in listing.entries]
        self.assertEqual(sorted(kinds, reverse=True), kinds)


# --------------------------------------------------------------------------
# 3. Links, junctions and reparse points
# --------------------------------------------------------------------------


class RedirectTests(unittest.TestCase):
    def browser(self, **kwargs):
        return DirectoryBrowser(filesystem=FakeFilesystem(**kwargs))

    def test_a_link_child_is_refused_rather_than_followed(self) -> None:
        """Entering one is exactly how a caller reaches somewhere the
        admission checks never saw."""

        tree = dict(POSIX_TREE)
        tree["/home/owner"] = [_dir("Models"), _link("Elsewhere")]
        browser = self.browser(tree=tree)
        listing = browser.open_path("/home/owner")
        with self.assertRaises(HeadlessError) as caught:
            browser.descend(listing.handle, "Elsewhere")
        self.assertEqual(BROWSE_REDIRECT_REFUSED, caught.exception.code)

    def test_a_junction_is_refused_the_same_way_as_a_symlink(self) -> None:
        """On Windows a directory junction reports `is_symlink() == False`.
        The facts layer reports both as redirecting, so the browser needs one
        rule rather than two."""

        tree = dict(POSIX_TREE)
        tree["/home/owner"] = [
            EntryFacts(name="Junction", is_dir=True, is_redirecting=True)
        ]
        browser = self.browser(tree=tree)
        listing = browser.open_path("/home/owner")
        with self.assertRaises(HeadlessError) as caught:
            browser.descend(listing.handle, "Junction")
        self.assertEqual(BROWSE_REDIRECT_REFUSED, caught.exception.code)

    def test_a_redirecting_entry_is_still_listed(self) -> None:
        """Refused to ENTER, not hidden. An owner who cannot see the folder
        they are looking for concludes the picker is broken."""

        tree = dict(POSIX_TREE)
        tree["/home/owner"] = [_dir("Models"), _link("Elsewhere")]
        browser = self.browser(tree=tree)
        listing = browser.open_path("/home/owner")
        names = [entry.name for entry in listing.entries]
        self.assertIn("Elsewhere", names)

    def test_a_typed_path_that_resolves_elsewhere_is_admitted_at_the_target(
        self,
    ) -> None:
        """`resolve` follows the link -- once, deliberately -- and then admits
        the TARGET. `/var` -> `/private/var` on macOS has to work, and the
        volume policy must see where it actually landed."""

        tree = dict(POSIX_TREE)
        tree["/private/var"] = [_dir("models")]
        browser = self.browser(tree=tree, links={"/var": "/private/var"})
        listing = browser.open_path("/var")
        self.assertEqual("/private/var", listing.path)


# --------------------------------------------------------------------------
# 4. Volume policy, POSIX and Windows semantics
# --------------------------------------------------------------------------


class VolumeTests(unittest.TestCase):
    def test_a_network_location_is_refused_by_syntax_before_any_stat(self) -> None:
        browser = DirectoryBrowser(filesystem=FakeFilesystem(POSIX_TREE))
        for attempt in (r"\\server\share", "//server/share", r"\\?\C:\x"):
            with self.assertRaises(HeadlessError):
                browser.open_path(attempt)

    def test_the_refusal_happens_before_the_filesystem_is_touched(self) -> None:
        """Stat'ing a remote share authenticates to a remote host and can
        hang. Refusing after resolution would refuse too late."""

        fake = FakeFilesystem(POSIX_TREE)
        browser = DirectoryBrowser(filesystem=fake)
        with self.assertRaises(HeadlessError):
            browser.open_path(r"\\server\share\models")
        self.assertEqual([], fake.scans)

    def test_a_relative_path_is_refused(self) -> None:
        browser = DirectoryBrowser(filesystem=FakeFilesystem(POSIX_TREE))
        for attempt in ("models", "./models", ""):
            with self.assertRaises(HeadlessError):
                browser.open_path(attempt)

    def test_the_posix_root_is_navigable_but_not_choosable(self) -> None:
        """`/opt/models` in a container must be reachable, so `/` has to be
        navigable -- and a typo must not become a filesystem-wide scan, so it
        must not be selectable."""

        browser = DirectoryBrowser(filesystem=FakeFilesystem(POSIX_TREE))
        listing = browser.open_path("/")
        self.assertTrue(listing.entries)
        self.assertFalse(listing.usable)
        self.assertEqual(
            "HEADLESS_CATALOGUE_ROOT_IS_FILESYSTEM_ROOT", listing.usable_reason
        )

    def test_an_ordinary_directory_is_choosable(self) -> None:
        browser = DirectoryBrowser(filesystem=FakeFilesystem(POSIX_TREE))
        listing = browser.open_path("/home/owner/Models")
        self.assertTrue(listing.usable)
        self.assertIsNone(listing.usable_reason)

    def test_a_windows_drive_root_is_navigable_but_not_choosable(self) -> None:
        tree = {"C:\\": [_dir("Users")], "C:\\Users": [_dir("owner")]}
        browser = DirectoryBrowser(filesystem=FakeWindowsFilesystem(tree))
        listing = browser.open_path("C:\\")
        self.assertTrue(listing.entries)
        self.assertFalse(listing.usable)


# --------------------------------------------------------------------------
# 5. Bounds, and awkward names
# --------------------------------------------------------------------------


class BoundsTests(unittest.TestCase):
    def test_a_large_directory_is_truncated_and_says_so(self) -> None:
        tree = {"/big": [_file(f"model{index:05d}.safetensors") for index in range(4000)]}
        browser = DirectoryBrowser(filesystem=FakeFilesystem(tree), max_entries=100)
        listing = browser.open_path("/big")
        self.assertEqual(100, len(listing.entries))
        self.assertTrue(listing.truncated)
        self.assertEqual(4000, listing.total_seen)

    def test_a_small_directory_is_not_marked_truncated(self) -> None:
        browser = DirectoryBrowser(filesystem=FakeFilesystem(POSIX_TREE))
        self.assertFalse(browser.open_path("/home/owner").truncated)

    def test_an_unreadable_directory_lists_nothing_and_does_not_raise(self) -> None:
        """It is a folder the owner can see and cannot enter. Reporting it as
        empty is honest; refusing the whole browse is not."""

        fake = FakeFilesystem(POSIX_TREE, unreadable={"/home/owner/Models"})
        browser = DirectoryBrowser(filesystem=fake)
        listing = browser.open_path("/home/owner/Models")
        self.assertEqual((), listing.entries)
        self.assertEqual(0, listing.total_seen)

    def test_unicode_names_survive_intact(self) -> None:
        tree = {
            "/u": [
                _dir("模型"),
                _dir("Ordner mit Leerzeichen"),
                _dir("café"),
                _file("emoji-\U0001f600.safetensors"),
            ]
        }
        browser = DirectoryBrowser(filesystem=FakeFilesystem(tree))
        names = [entry.name for entry in browser.open_path("/u").entries]
        self.assertIn("模型", names)
        self.assertIn("café", names)
        self.assertIn("Ordner mit Leerzeichen", names)

    def test_a_decomposed_name_can_still_be_entered(self) -> None:
        """macOS hands back decomposed names. The page shows one spelling and
        sends it back; comparing raw bytes would refuse the owner's own
        folder, on one platform only."""

        composed = "caf\u00e9"
        decomposed = "cafe\u0301"
        tree = {"/u": [_dir(decomposed)], f"/u/{decomposed}": []}
        browser = DirectoryBrowser(filesystem=FakeFilesystem(tree))
        listing = browser.open_path("/u")
        entered = browser.descend(listing.handle, composed)
        self.assertTrue(entered.path.endswith(decomposed))

    def test_an_over_long_name_is_dropped_from_the_listing(self) -> None:
        tree = {"/u": [_dir("ok"), _dir("x" * 400)]}
        browser = DirectoryBrowser(filesystem=FakeFilesystem(tree))
        names = [entry.name for entry in browser.open_path("/u").entries]
        self.assertEqual(["ok"], names)

    def test_names_with_punctuation_are_ordinary(self) -> None:
        tree = {"/u": [_dir("a b"), _dir("a-b"), _dir("a.b"), _dir("a'b")]}
        browser = DirectoryBrowser(filesystem=FakeFilesystem(tree))
        self.assertEqual(4, len(browser.open_path("/u").entries))


# --------------------------------------------------------------------------
# 6. The service seam: kill switch, concurrency, and path privacy
# --------------------------------------------------------------------------


class ServiceTests(_RealRootCase):
    def service(self, **kwargs):
        return FilesystemService(browser=self.browser, **kwargs)

    def test_the_kill_switch_refuses_every_route(self) -> None:
        service = self.service(enabled=False)
        for call in (
            lambda: service.places(),
            lambda: service.resolve({"path": str(self.root)}),
            lambda: service.list({"handle": "x"}),
        ):
            with self.assertRaises(HeadlessError) as caught:
                call()
            self.assertEqual(BROWSE_DISABLED, caught.exception.code)

    def test_capabilities_answers_even_when_disabled(self) -> None:
        """So the page can render the control as unavailable rather than
        appearing broken."""

        self.assertFalse(self.service(enabled=False).capabilities()["enabled"])

    def test_switching_off_invalidates_outstanding_handles(self) -> None:
        service = self.service()
        listing = service.resolve({"path": str(self.root)})
        service.set_enabled(False)
        service.set_enabled(True)
        with self.assertRaises(HeadlessError):
            service.list({"handle": listing["handle"]})

    def test_concurrency_is_bounded(self) -> None:
        service = self.service()
        held = [service._slot().__enter__() for _ in range(BROWSE_CONCURRENCY)]  # noqa: SLF001
        try:
            with self.assertRaises(HeadlessError) as caught:
                service.resolve({"path": str(self.root)})
            self.assertEqual(BROWSE_BUSY, caught.exception.code)
        finally:
            for slot in held:
                slot.__exit__()

    def test_a_malformed_payload_is_refused_by_name(self) -> None:
        service = self.service()
        for payload in ([], "path", 3, None, {}, {"path": 7}):
            with self.assertRaises(HeadlessError):
                service.resolve(payload)

    def test_asking_for_a_child_and_the_parent_at_once_is_refused(self) -> None:
        service = self.service()
        listing = service.resolve({"path": str(self.root)})
        with self.assertRaises(HeadlessError):
            service.list(
                {"handle": listing["handle"], "child": "Models", "parent": True}
            )

    def test_places_is_conservative(self) -> None:
        """A starting set, not an inventory of the host."""

        places = self.service().places()["places"]
        self.assertLessEqual(len(places), 32)
        self.assertTrue(all(place["kind"] in
                            ("configured", "home", "volume") for place in places))

    def test_the_document_carries_a_path_only_here(self) -> None:
        """A source pin on the declared exception. `fs_service` is the ONE
        module allowed to serialize a filesystem path into a response body;
        if a second one appears, the boundary has moved and nobody decided
        to move it."""

        document = self.service().resolve({"path": str(self.root)})
        self.assertEqual(str(self.root), document["path"])
        self.assertTrue(document["breadcrumb"])

        owned = APP_ROOT / "forge_studio"
        offenders = []
        for source in sorted(owned.rglob("*.py")):
            if source.name == "fs_service.py":
                continue
            text = source.read_text(encoding="utf-8")
            if '"breadcrumb"' in text:
                offenders.append(source.name)
        self.assertEqual([], offenders)

    def test_no_response_carries_a_password_or_token(self) -> None:
        service = self.service()
        rendered = json.dumps(
            [
                service.capabilities(),
                service.places(),
                service.resolve({"path": str(self.root)}),
            ]
        )
        for forbidden in ("token", "secret", "password"):
            self.assertNotIn(forbidden, rendered.lower())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_FS_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
