"""The package a tester receives. AR7.

WHAT THIS EXISTS TO PREVENT

Everything else in this program has been measured against the DEVELOPMENT
tree -- one with a venv, a `.git`, the owner's results, their state root and
their private model folder all sitting inside it. A tester receives none of
that, and until AR7 nobody had built the thing they DO receive.

Two questions the development tree cannot answer:

  1. does the archive carry the owner's private world?
  2. does the code inside it start, with no repository around it?

TRACKED SOURCE, REVIEWED RUNTIME SELECTION

The tester archive intersects git ls-files with packaging/runtime.json.
Development files stay in the repository and the separate source profile.
Untracked files cannot slip into either archive; approved model assets have
an exact, separately verified manifest.

ONE PRIVACY RULE, NOT TWO

The builder calls `scripts.distribution_privacy.leaks_in` -- the same function
the repository scan uses. Two copies of a privacy rule drift, and the copy that
drifts is the one nobody is looking at. `SharedRuleTests` asserts the builder
has not grown its own.

Live proof that it extracts, audits clean and RUNS:
`Evidence/ar7-live/LIVE-PROOF.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2]
WS_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts"))

EXPECTED_BUILD_TESTS = 23

SOURCE = (APP_ROOT / "scripts" / "build_distributable.py").read_text(
    encoding="utf-8")


def builder():
    """Resolved at CALL time: `test_import_boundaries` purges modules."""

    import importlib

    return importlib.import_module("build_distributable")


class VersionBlockTests(unittest.TestCase):
    """`10_RELEASE_PACKAGING_AND_UPDATE.md` names what a release must show."""

    def test_it_reports_every_identifier_the_release_doc_requires(self) -> None:
        block = builder().version_block()
        for key in ("distribution_version", "studio_source_version",
                    "neo_upstream_commit", "built_from_commit"):
            with self.subTest(key=key):
                self.assertTrue(block.get(key), f"{key} is empty")

    def test_the_upstream_is_read_not_restated(self) -> None:
        """A version block that repeats a constant can claim an upstream the
        repository is not actually built on."""

        block = builder().version_block()
        upstream = (APP_ROOT / "UPSTREAM_BASE").read_text(encoding="utf-8")
        self.assertIn(block["neo_upstream_commit"], upstream)

    def test_the_built_commit_is_the_real_head(self) -> None:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(APP_ROOT),
                              capture_output=True, text=True, check=True)
        self.assertEqual(head.stdout.strip(),
                         builder().version_block()["built_from_commit"])


class ProvenanceTests(unittest.TestCase):
    """Clean by construction, not by a maintained list."""

    def test_the_file_list_comes_from_git(self) -> None:
        self.assertIn("git", SOURCE)
        self.assertIn("ls-files", SOURCE)

    def test_there_is_no_exclusion_list_to_rot(self) -> None:
        """Catches the tempting rewrite: copy everything, then subtract. The
        only literal exclusions allowed are the config, which is private and
        regenerated, and nothing else."""

        self.assertEqual(("studio-config.json",), builder().NEVER)

    def test_the_owners_config_can_never_ship(self) -> None:
        """It carries a home directory, private model roots and model
        filenames, and the launcher regenerates it from the tracked template."""

        self.assertIn("studio-config.json", builder().NEVER)
        self.assertTrue(
            (APP_ROOT / "docs" / "studio" / "internal-alpha"
             / "studio-config.template.json").is_file(),
            "the template that replaces it must be tracked")

    def test_the_tracked_launchers_are_named_and_explained(self) -> None:
        """Root launchers are copied from version-controlled templates."""

        self.assertEqual(("start_studio.py", "Start-Studio.bat"),
                         builder().TRACKED_LAUNCHERS)
        self.assertIn("tracked_launchers", SOURCE)


class SharedRuleTests(unittest.TestCase):
    def test_the_builder_uses_the_repository_privacy_scanner(self) -> None:
        self.assertIn("distribution_privacy", SOURCE)
        self.assertIn("leaks_in", SOURCE)

    def test_the_builder_has_not_grown_its_own_patterns(self) -> None:
        """Two copies drift. The builder must own no pattern of its own."""

        for invented in ("re.compile", "PRIVATE_PATH =", "SECRET ="):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, SOURCE)

    def test_it_fails_closed(self) -> None:
        """A build that warns and continues is a build whose warning is read
        once. No archive may be written when the audit finds anything."""

        body = SOURCE[SOURCE.index("problems = _audit(members)"):]
        refuse_at = body.index("REFUSED")
        write_at = body.index("zipfile.ZipFile(archive")
        self.assertLess(refuse_at, write_at)
        self.assertIn("return 3", body[:write_at])


class DirtyTreeTests(unittest.TestCase):
    def test_a_dirty_tree_is_refused_by_default(self) -> None:
        """The manifest names a commit. If uncommitted work went in, that name
        is a lie and the archive cannot be rebuilt from it."""

        self.assertIn("_tree_is_clean", SOURCE)
        self.assertIn("REFUSED: the working tree is dirty", SOURCE)

    def test_the_override_says_what_it_costs(self) -> None:
        self.assertIn("allow_dirty", SOURCE)
        self.assertIn("will not", SOURCE)


class ArchiveShapeTests(unittest.TestCase):
    """Built for real, into a temporary directory."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        out = Path(cls._tmp.name)
        code = builder().build(out, allow_dirty=True)
        cls.code = code
        cls.archives = sorted(out.glob("*.zip"))
        cls.out = out

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_build_succeeds_and_writes_one_archive(self) -> None:
        self.assertEqual(0, self.code, "the privacy audit refused the build")
        self.assertEqual(1, len(self.archives))

    def test_it_publishes_a_checksum_and_a_manifest(self) -> None:
        self.assertTrue((self.out / (self.archives[0].name + ".sha256")).is_file())
        manifests = list(self.out.glob("*.manifest.json"))
        self.assertEqual(1, len(manifests))
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual("passed", data["privacy_scan"])
        self.assertGreater(data["file_count"], 100)

    def test_everything_lands_under_one_directory(self) -> None:
        """Catches a zip that explodes into the tester's current folder."""

        with zipfile.ZipFile(self.archives[0]) as zf:
            tops = {name.split("/", 1)[0] for name in zf.namelist()}
        self.assertEqual(1, len(tops), f"multiple roots: {sorted(tops)}")

    def test_the_archive_is_reproducible(self) -> None:
        """A checksum nobody can reproduce is decoration. Fixed timestamps and
        a sorted member order are what make the digest mean something."""

        second = Path(self._tmp.name) / "again"
        builder().build(second, allow_dirty=True)
        import hashlib

        first_digest = hashlib.sha256(self.archives[0].read_bytes()).hexdigest()
        again = sorted(second.glob("*.zip"))[0]
        self.assertEqual(first_digest,
                         hashlib.sha256(again.read_bytes()).hexdigest())

    def test_no_development_directory_is_inside(self) -> None:
        with zipfile.ZipFile(self.archives[0]) as zf:
            names = zf.namelist()
        for banned in ("/venv/", "/.git/", "/Private-Local/", "/Studio-Results/",
                       "/Studio-State/", "/__pycache__/", "/dist/"):
            with self.subTest(banned=banned):
                self.assertEqual([], [n for n in names if banned in n])

    def test_tester_root_has_only_the_supported_entrypoint_and_required_files(self):
        with zipfile.ZipFile(self.archives[0]) as zf:
            paths = {n.split("/", 1)[1] for n in zf.namelist()}
        root = {n for n in paths if "/" not in n}
        app_root = {n for n in paths if n.startswith("app/") and n.count("/") == 1}
        self.assertEqual({"START-HERE.md", "Start-Studio.bat", "start_studio.py"}, root)
        self.assertEqual({"app/LICENSE", "app/UPSTREAM_BASE", "app/requirements.txt",
                          "app/launch_studio.py", "app/launch.py"}, app_root)
        for prefix in ("app/tests/", "app/.github/", "app/docker/", "app/prompts/",
                       "app/deploy/", "app/javascript/", "app/html/"):
            self.assertFalse(any(n.startswith(prefix) for n in paths), prefix)
        self.assertIn("app/scripts/distribution_privacy.py", paths)
        self.assertIn("app/forge_studio/frontend/index.html", paths)
        self.assertIn("app/extensions-builtin/soft-inpainting/scripts/soft_inpainting.py", paths)
        self.assertIn("app/studio_plugins/samplers_bfs.py", paths)

    def test_tester_document_links_resolve_inside_the_archive(self):
        import posixpath
        import re
        with zipfile.ZipFile(self.archives[0]) as zf:
            names = set(zf.namelist())
            for name in names:
                if not (name.endswith("/START-HERE.md") or "/app/docs/" in name):
                    continue
                if not name.endswith(".md"):
                    continue
                text = zf.read(name).decode("utf-8")
                text = re.sub(r"```[\s\S]*?```|`[^`]*`", "", text)
                for target in re.findall(r"\]\(([^)]+)\)", text):
                    if "://" in target or target.startswith("#"):
                        continue
                    target = target.split("#", 1)[0]
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                    self.assertTrue(resolved in names, (name, target))

    def test_support_report_runs_from_extracted_files_without_the_test_suite(self):
        root = self.out / "diagnostic-runtime"
        with zipfile.ZipFile(self.archives[0]) as zf:
            for name in zf.namelist():
                relative = name.split("/", 1)[1]
                if relative in {"app/scripts/build_support_bundle.py",
                                "app/scripts/distribution_privacy.py", "app/UPSTREAM_BASE"}:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(zf.read(name))
        code = """
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'app'))
from scripts import build_support_bundle as support
support._run = lambda command: None
assert support.build(root / 'report', None) == 0
assert not any(name == 'tests' or name.startswith('tests.') for name in sys.modules)
"""
        run = subprocess.run([sys.executable, "-I", "-c", code, str(root)],
                             cwd=root, capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(1, len(list((root / "report").glob("*.zip"))))

    def test_source_archive_preserves_complete_tracked_checkout_without_weights(self):
        out = self.out / "source"
        with patch.object(builder(), "_asset_members", side_effect=AssertionError("Source must not read weights")):
            self.assertEqual(0, builder().build(out, allow_dirty=True, profile="source"))
        archive = next(out.glob("*-source.zip"))
        with zipfile.ZipFile(archive) as zf:
            actual = {n.split("/app/", 1)[1] for n in zf.namelist()}
        expected = {p.relative_to(APP_ROOT).as_posix() for p in builder().tracked()
                    if p.relative_to(APP_ROOT).as_posix() not in builder().NEVER}
        self.assertEqual(expected, actual)
        self.assertIn("AGENTS.md", actual)
        self.assertIn("webui.py", actual)
        manifest = json.loads(next(out.glob("*.manifest.json")).read_text(encoding="utf-8"))
        self.assertEqual([], manifest["bundled_assets"])
        self.assertEqual("source", manifest["profile"])

    def test_missing_runtime_dependency_refuses_before_output(self):
        out = self.out / "missing-dependency"
        tracked = [p for p in builder().tracked() if p.name != "distribution_privacy.py"]
        with patch.object(builder(), "tracked", return_value=tracked):
            self.assertEqual(4, builder().build(out, allow_dirty=True))
        self.assertFalse(out.exists())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_BUILD_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
