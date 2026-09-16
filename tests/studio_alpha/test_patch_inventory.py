"""The patch inventory is recomputed from git, not trusted.

`AGENTS.md` requires updating `docs/14_PATCH_INVENTORY.md` before editing a
Neo-owned core file. Nothing enforced it. Nothing in the repository referenced
the inventory or `UPSTREAM_BASE` at all, so when the 2026-08-14 upscale work
patched two more Neo files, every number in both documents went stale in
silence -- and stayed stale until a review went looking four days later.

The counts were wrong by six files. Worse, the inventory's own summary line
said every Neo-owned change was "about import graph or install-time platform
choice", and that had become false: PATCH-003 changes upscale pixel behaviour
and imports `forge_headless` from inside Neo-owned code.

`test_every_changed_neo_file_is_named_in_the_inventory` is the one that matters.
A count can be corrected after the fact; an uncatalogued patch is a change to
someone else's code that nobody agreed to. That test would have failed on the
first upscale commit.

These tests read git. Where git, the repository or the baseline tag is
unavailable -- a source archive rather than a checkout -- they skip with a named
reason rather than passing vacuously.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_PATCH_INVENTORY_TESTS = 7

INVENTORY = APP_ROOT / "docs" / "14_PATCH_INVENTORY.md"
UPSTREAM_BASE = APP_ROOT / "UPSTREAM_BASE"

BASELINE_TAG = "neo-baseline-2026-07-23"

#: Studio does not patch these, and that is the load-bearing claim of the whole
#: document: no diffusion inference, sampling, memory-management or
#: model-loading behaviour is touched.
UNTOUCHED = ("backend/", "ldm_patched/", "extensions-builtin/")

#: Where Neo-owned edits are allowed to be.
NEO_OWNED = ("modules/", "modules_forge/")


def git(*args: str) -> str:
    """Run git in the repository, or skip the test with the reason why."""

    try:
        done = subprocess.run(
            ("git", *args), cwd=APP_ROOT, capture_output=True, text=True,
            timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise unittest.SkipTest(f"git unavailable: {error}") from error
    if done.returncode != 0:
        raise unittest.SkipTest(
            f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def require_baseline() -> None:
    git("rev-parse", "--verify", f"{BASELINE_TAG}^{{commit}}")


def changed_neo_files() -> list[str]:
    require_baseline()
    out = git("diff", "--name-only", f"{BASELINE_TAG}..HEAD", "--", *NEO_OWNED)
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def diff_totals() -> tuple[int, int, int]:
    """(files, insertions, deletions) for Neo-owned paths since the baseline."""

    require_baseline()
    out = git("diff", "--shortstat", f"{BASELINE_TAG}..HEAD", "--", *NEO_OWNED)
    files = int(re.search(r"(\d+) files? changed", out).group(1))
    inserted = re.search(r"(\d+) insertions?", out)
    deleted = re.search(r"(\d+) deletions?", out)
    return (files,
            int(inserted.group(1)) if inserted else 0,
            int(deleted.group(1)) if deleted else 0)


def documented_totals() -> tuple[int, int, int]:
    text = INVENTORY.read_text(encoding="utf-8")
    match = re.search(r"(\d+) files, \+(\d+) -(\d+)", text)
    if match is None:  # pragma: no cover - guard
        raise AssertionError("the inventory's scope block no longer states "
                             "`N files, +N -N`; the test cannot check it")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def upstream_base_field(name: str) -> str:
    for line in UPSTREAM_BASE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"UPSTREAM_BASE has no {name} field")


class InventoryAgreesWithGitTests(unittest.TestCase):
    def test_the_documented_file_count_matches_git(self) -> None:
        self.assertEqual(diff_totals()[0], documented_totals()[0],
                         "docs/14_PATCH_INVENTORY.md scope block is stale")

    def test_the_documented_line_counts_match_git(self) -> None:
        _, inserted, deleted = diff_totals()
        _, doc_inserted, doc_deleted = documented_totals()
        self.assertEqual((inserted, deleted), (doc_inserted, doc_deleted),
                         "docs/14_PATCH_INVENTORY.md scope block is stale")

    def test_every_changed_neo_file_is_named_in_the_inventory(self) -> None:
        """The guard that would have caught the 2026-08-14 upscale patches.

        AGENTS.md requires inventorying a Neo-core edit. An uncatalogued patch
        is a change to upstream's code that nobody signed off, and it is far
        more serious than a wrong count -- an upstream merge resolves against
        this document.
        """

        text = INVENTORY.read_text(encoding="utf-8")
        missing = [name for name in changed_neo_files() if name not in text]
        self.assertEqual(
            [], missing,
            "these Neo-owned files are modified but appear nowhere in the "
            "patch inventory -- add them to a patch family with a reason, "
            "minimal-hook note, upstream conflict likelihood and removal "
            "criteria")

    def test_the_inference_directories_are_still_untouched(self) -> None:
        """The load-bearing claim, checked rather than repeated."""

        require_baseline()
        out = git("diff", "--name-only", f"{BASELINE_TAG}..HEAD", "--",
                  *UNTOUCHED)
        self.assertEqual(
            [], [line for line in out.splitlines() if line.strip()],
            "backend/, ldm_patched/ or extensions-builtin/ has been modified. "
            "The inventory's central claim -- that no diffusion inference, "
            "sampling, memory-management or model-loading behaviour is "
            "patched -- is no longer true and must be rewritten, not amended")


class UpstreamBaseAgreesTests(unittest.TestCase):
    def test_the_divergence_count_matches_git_at_its_recorded_commit(self) -> None:
        """Checked against the commit it was MEASURED at, never against HEAD.

        The first version of this compared to `neo...HEAD` and was wrong by
        construction: every commit advances the right-hand number, so the value
        was stale the moment it was committed -- it failed on the next commit,
        which is the only reason the flaw was caught rather than shipped as a
        test that quietly needed editing forever.

        `neo_head_left_right_at` names the commit the figure describes, so the
        assertion stays true for good while still failing if someone edits the
        number without re-measuring.
        """

        at = upstream_base_field("neo_head_left_right_at")
        out = git("rev-list", "--left-right", "--count", f"neo...{at}")
        left, right = (int(part) for part in out.split())
        self.assertEqual(
            f"{left} {right}", upstream_base_field("neo_head_left_right"),
            f"UPSTREAM_BASE neo_head_left_right does not match git at {at}")

    def test_the_neo_owned_file_count_matches_git(self) -> None:
        self.assertEqual(
            str(diff_totals()[0]),
            upstream_base_field("neo_owned_files_modified"),
            "UPSTREAM_BASE neo_owned_files_modified is stale")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PATCH_INVENTORY_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
