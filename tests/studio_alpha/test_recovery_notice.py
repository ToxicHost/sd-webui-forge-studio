"""A skipped recovery no longer tells the owner nothing.

Three paths in `canvas-recovery.js` `resolve()` used to give up quietly, and
each one costs the owner work they believe is safe:

```text
one document fails to load    they get the other three back and never learn a
                              fourth existed
every document fails          they get a blank canvas
the resolution throws         they get a blank canvas
```

All three logged to the console and stopped. The console is not where somebody
who just lost a painting looks.

Owner ruling on record: "Surface, users should know why something failed."

The wording of all three was exercised in a live page and is recorded in the
2026-08-22 handoff; what lives here is that the paths cannot go quiet again.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

EXPECTED_NOTICE_TESTS = 10


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


RECOVERY = _strip_js_comments(
    (FRONTEND / "canvas-recovery.js").read_text(encoding="utf-8"))
APP = _strip_js_comments((FRONTEND / "app.js").read_text(encoding="utf-8"))


def _resolve_body() -> str:
    start = RECOVERY.index("async function resolve(sessionIdentity) {")
    depth = 0
    for index in range(RECOVERY.index("{", start), len(RECOVERY)):
        if RECOVERY[index] == "{":
            depth += 1
        elif RECOVERY[index] == "}":
            depth -= 1
            if depth == 0:
                return RECOVERY[start:index + 1]
    raise AssertionError("resolve never closes")


class EverySilentPathSpeaksTests(unittest.TestCase):
    def test_a_document_that_will_not_load_is_counted(self):
        """It used to be a bare `console.warn` and nothing else."""

        body = _resolve_body()
        self.assertIn("skipped++", body)

    def test_some_restored_and_some_skipped_says_so(self):
        body = _resolve_body()
        self.assertIn("if (skipped > 0)", body)
        self.assertIn("could not be read", body)

    def test_none_restored_says_so(self):
        body = _resolve_body()
        self.assertIn("could not be restored", body)

    def test_a_failed_resolution_says_so(self):
        body = _resolve_body()
        self.assertIn("Crash recovery could not run", body)

    def test_every_notice_says_nothing_was_deleted(self):
        """The most important sentence in all three. Recovery declining to
        restore does NOT remove anything, and an owner who thinks it did will
        do something rash."""

        body = _resolve_body()
        # Every argument to `_tellTheOwner`, whole. Four, not three: the
        # none-restored path has a singular and a plural wording, and asserting
        # a COUNT would have hidden that rather than checked it.
        calls = re.findall(r"_tellTheOwner\(([\s\S]*?)\);", body)
        self.assertEqual(
            3, len(calls),
            "expected one notice per silent path; the parse or the code moved")
        for call in calls:
            with self.subTest(notice=re.sub(r"\s+", " ", call)[:60]):
                self.assertRegex(
                    call, r"[Nn]othing was deleted",
                    "a recovery notice must say nothing was removed -- an "
                    "owner who thinks it was will do something rash",
                )


class TheNoticeCannotVanishTests(unittest.TestCase):
    def test_a_notice_with_nowhere_to_go_is_queued(self):
        """Recovery resolves inside the boot chain, where the toast surface
        may not exist yet. A notice that vanishes is the silence again."""

        self.assertIn("_pendingNotices.push", RECOVERY)
        self.assertIn("function takeNotices()", RECOVERY)

    def test_the_queue_is_exposed(self):
        self.assertIn("takeNotices: takeNotices,", RECOVERY)

    def test_the_page_drains_it(self):
        start = APP.index("window.StudioRecovery.resolve(_restoredIdentity)")
        block = APP[start:start + 800]
        self.assertIn("takeNotices", block)
        self.assertIn("showToast(notice.message, notice.kind)", block)

    def test_showing_a_notice_can_never_cost_the_recovery(self):
        """A toast surface that throws must not take the restored document
        with it."""

        body = RECOVERY[RECOVERY.index("function _tellTheOwner("):]
        body = body[:body.index("\n}")]
        self.assertIn("try {", body)
        self.assertIn("catch (_)", body)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_NOTICE_TESTS, found)


if __name__ == "__main__":
    unittest.main()
