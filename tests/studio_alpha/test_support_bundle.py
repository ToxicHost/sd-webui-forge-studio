"""What a tester sends back, and what it must never contain. AR8.

BUILT TO AN EXISTING SPEC

`docs/studio/MAC_DIAGNOSTIC_BUNDLE_SPEC.md` decided how this should behave, for
a Mac milestone that was never implemented. Its rules are not Mac-specific, so
they are followed rather than re-derived, and these tests assert them:

  * an ALLOW-LIST, not a deny-list;
  * redaction applied to the FILE THAT IS WRITTEN, not to what is displayed --
    "a tester must never have to trust that a viewer hid something";
  * one human-readable file inspected BEFORE deciding to send;
  * `--dry-run` collects nothing;
  * no network request of any kind.

THE ONE THAT MATTERS MOST

`ArtworkIsNeverCollectedTests`. Canvas crash recovery (AR4.4) stores COMPLETE
LAYERED DOCUMENTS under the state root. A support bundle that swept the state
root wholesale would post someone's unfinished work into a chat channel, and
the tester would have been told the bundle was safe.

That is also why `test_canvas_recovery.py` has carried an armed, skipping guard
since AR4.4: it fails the moment a bundle module appears that does not exclude
the recovery directory. This suite is that module arriving.

MEASURED, NOT ASSUMED

The launch log carries ZERO prompts and 1718 lines with the owner's home
directory. So the log's creative content is nil and its path content is the
whole risk -- which is why a redacted tail is included rather than the log
being dropped. A traceback is usually the only thing that explains a crash.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
WS_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts"))

EXPECTED_BUNDLE_TESTS = 18

SOURCE = (APP_ROOT / "scripts" / "build_support_bundle.py").read_text(
    encoding="utf-8")


def bundle():
    """Resolved at CALL time: `test_import_boundaries` purges modules."""

    import importlib

    return importlib.import_module("build_support_bundle")


def _fake_state(root: Path) -> Path:
    """A state root shaped like a tester's, artwork included."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "preferences.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (root / "defaults.json").write_text('{"paramSteps": "20"}', encoding="utf-8")
    (root / "last-session.json").write_text(
        '{"settings": {"a": "SESSION-CONTENT-MARKER"}}', encoding="utf-8")
    recovery = root / "recovery" / ("a" * 32)
    recovery.mkdir(parents=True)
    (recovery / "manifest.json").write_text('{"W": 256, "H": 320}',
                                            encoding="utf-8")
    (recovery / "layer-1.png").write_bytes(b"THE OWNER'S ARTWORK")
    gallery = root / "gallery"
    gallery.mkdir()
    (gallery / "thumbnails.db").write_bytes(b"THUMBNAILS")
    return root


class ArtworkIsNeverCollectedTests(unittest.TestCase):
    """The one that matters most. See the module docstring."""

    def _built(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        state = _fake_state(root / "state")
        out = root / "out"
        code = bundle().build(out, state)
        self.assertEqual(0, code)
        archive = sorted(out.glob("*.zip"))[0]
        with zipfile.ZipFile(archive) as zf:
            blob = b"".join(zf.read(n) for n in zf.namelist())
        return blob, zf, archive

    def test_a_recovered_document_never_reaches_the_bundle(self) -> None:
        blob, _, _ = self._built()
        self.assertNotIn(b"THE OWNER'S ARTWORK", blob)

    def test_the_gallery_never_reaches_the_bundle(self) -> None:
        blob, _, _ = self._built()
        self.assertNotIn(b"THUMBNAILS", blob)

    def test_the_recovery_directory_is_named_as_never_collected(self) -> None:
        """A guard on the DECISION, not only on this fixture: a future field
        that swept the state root would still pass the two tests above if the
        fixture happened not to match."""

        self.assertIn("recovery", bundle().NEVER_COLLECT)
        self.assertIn("gallery", bundle().NEVER_COLLECT)
        self.assertIn("Studio-Results", bundle().NEVER_COLLECT)
        self.assertIn("Private-Local", bundle().NEVER_COLLECT)

    def test_the_working_session_is_not_collected(self) -> None:
        """`last-session.json` is the tester's working state and explains no
        crash that defaults.json does not.

        Asserted on its CONTENT, not its filename: the log legitimately names
        the file while writing it, and a filename check would fail on a log
        line that proves nothing about what was collected.
        """

        blob, _, _ = self._built()
        self.assertNotIn(b"SESSION-CONTENT-MARKER", blob)


class AllowListTests(unittest.TestCase):
    """Nothing outside `collect()` is gathered."""

    def test_the_report_carries_only_declared_fields(self) -> None:
        report = bundle().collect(None)
        self.assertEqual(
            {"schema_version", "generated_at", "platform", "python", "gpu",
             "versions", "torch_present", "state_root_present", "settings",
             "log_tail_lines"},
            set(report))

    def test_torch_presence_does_not_import_torch(self) -> None:
        """`find_spec` does not execute the package, so asking cannot
        initialise CUDA as a side effect."""

        self.assertIn("find_spec", SOURCE)
        self.assertNotIn("import torch", SOURCE)

    def test_the_gpu_serial_and_uuid_are_not_queried(self) -> None:
        """A model name is not identifying; a serial is.

        Scans the QUERY ARGUMENT rather than the file. The module comment
        explaining that UUID is deliberately not requested would otherwise trip
        a scan for the word "uuid" -- the same trap as a no-LRU guard matching
        the comment that says "no LRU".
        """

        queries = [line for line in SOURCE.splitlines()
                   if "--query-gpu" in line]
        self.assertEqual(1, len(queries), "more than one GPU query")
        self.assertIn("name,driver_version,memory.total", queries[0])
        for identifying in ("uuid", "serial", "index"):
            with self.subTest(field=identifying):
                self.assertNotIn(identifying, queries[0].lower())

    def test_no_state_root_still_produces_a_report(self) -> None:
        """A tester whose Studio never started still has something to send."""

        report = bundle().collect(None)
        self.assertFalse(report["state_root_present"])
        self.assertIsNone(report["settings"])


class RedactionTests(unittest.TestCase):
    def test_a_windows_user_directory_is_rewritten(self) -> None:
        out = bundle().redact(r"C:\Users\someone\Desktop\thing.png")
        self.assertNotIn("someone", out)
        self.assertIn("<REDACTED>", out)

    def test_a_posix_home_is_rewritten(self) -> None:
        self.assertNotIn("someone", bundle().redact("/home/someone/x"))

    def test_the_project_root_becomes_a_placeholder(self) -> None:
        """Rewritten BEFORE the generic user rule, or a path inside the
        project would still leak the layout."""

        self.assertIn("<PROJECT>", bundle().redact(str(WS_ROOT) + "/app"))

    def test_redaction_is_applied_to_what_is_written(self) -> None:
        """The spec's load-bearing rule: not to what is displayed."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        self.assertEqual(0, bundle().build(out, None))
        for path in list(out.glob("*.txt")) + list(out.glob("*.zip")):
            with self.subTest(path=path.name):
                blob = path.read_bytes()
                self.assertNotIn(str(WS_ROOT).encode(), blob)


class ConsentTests(unittest.TestCase):
    """Nothing is transmitted. A person decides."""

    def test_dry_run_writes_nothing(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "never"
        self.assertEqual(0, bundle().build(out, None, dry_run=True))
        self.assertFalse(out.exists(), "a dry run created files")

    def test_it_writes_a_plain_text_file_to_read_first(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        bundle().build(out, None)
        readable = sorted(out.glob("*.txt"))
        self.assertEqual(1, len(readable))
        text = readable[0].read_text(encoding="utf-8")
        self.assertIn("nothing has been", text)
        self.assertIn("NOT collected", text)

    def test_it_makes_no_network_request(self) -> None:
        for reaching in ("urllib", "requests", "http.client", "socket",
                         "urlopen"):
            with self.subTest(reaching=reaching):
                self.assertNotIn(reaching, SOURCE)

    def test_its_console_output_survives_a_cp1252_terminal(self) -> None:
        """The launcher already died once on a character it could not print
        (AR7.1). A tester-facing tool must not repeat it."""

        for line in SOURCE.splitlines():
            if "print(" not in line:
                continue
            with self.subTest(line=line.strip()[:50]):
                line.encode("cp1252")   # raises if it cannot be shown


class FailClosedTests(unittest.TestCase):
    def test_it_refuses_rather_than_shipping_a_leak(self) -> None:
        """A bundle that leaks is worse than no bundle, because the tester was
        told it was safe."""

        self.assertIn("distribution_privacy", SOURCE)
        self.assertIn("leaks_in", SOURCE)
        body = SOURCE[SOURCE.index("problems = privacy.leaks_in"):]
        self.assertIn("REFUSED", body[:400])
        self.assertIn("return 3", body[:400])


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_BUNDLE_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
