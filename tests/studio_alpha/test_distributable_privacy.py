"""Nothing that ships may carry the owner's private world. AR6.4.

THE DEFECT THIS CAUGHT

`docs/studio/OPERATIONS.md` hardcoded an absolute home directory in four
places -- the workspace path, the app path, the results path and a `cd` in a
launch snippet. It is a TRACKED file under `docs/`, so it would have gone into
a friends distributable carrying the owner's username.

That is the same class of leak the parity review bundle already had, where
`studio-config.json` went in raw with the home directory, the private model
roots and model filenames. `test_parity_bundle.py` guards the bundle; this
guards the repository itself, which is what the distributable is built from.

WHY A PATTERN AND NOT A NAME

The scan looks for the SHAPE of a private absolute path -- a Windows user
directory, a POSIX home, a UNC share -- rather than for one owner's name. A
guard that only knew "heras" would pass for the next person to work on this and
would have to be edited to keep working, which is how a privacy check quietly
stops checking.

WHAT IS DELIBERATELY ALLOWED

Placeholders (`$WORKSPACE`, `<WORKSPACE>`, `%USERPROFILE%`), relative paths,
and paths under a temporary directory in test fixtures. Documentation must be
able to TALK about paths; it may not bake one in.

Test fixtures and this file are exempt from the literal-path rule where they
construct paths at runtime from `Path(__file__)` -- that is the correct way to
be location-independent, and forbidding the word would forbid the fix.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_DISTRIBUTABLE_PRIVACY_TESTS = 8

from scripts.distribution_privacy import (
    PRIVATE_PATH, SECRET, PLACEHOLDERS, VENDORED, readable_text, leaks_in,
)

def tracked_files() -> list[Path]:
    """Every file git actually tracks. The distributable is built from these."""

    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(APP_ROOT),
        capture_output=True, text=True, check=True)
    return [APP_ROOT / name for name in result.stdout.split("\0") if name]


class TrackedFilePrivacyTests(unittest.TestCase):
    """The repository is what the distributable is built from."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = tracked_files()

    def test_no_tracked_file_carries_a_private_absolute_path(self) -> None:
        """Catches the OPERATIONS.md leak, and anything shaped like it."""

        offenders = []
        for path in self.files:
            text = readable_text(path)
            if text is None:
                continue
            relative = path.relative_to(APP_ROOT).as_posix()
            offenders += [f"{relative}: {p}" for p in leaks_in(relative, text)
                          if p.startswith("private path")]
        self.assertEqual([], offenders)

    def test_no_tracked_file_carries_a_secret(self) -> None:
        offenders = []
        for path in self.files:
            if path.name == Path(__file__).name:
                continue
            text = readable_text(path)
            if text is None:
                continue
            if SECRET.search(text):
                offenders.append(str(path.relative_to(APP_ROOT)))
        self.assertEqual([], offenders)

    def test_no_raw_owner_config_is_tracked(self) -> None:
        """`studio-config.json` carries the owner's model roots. It belongs
        outside the repository, and a template belongs inside it."""

        tracked = {p.name for p in self.files}
        self.assertNotIn("studio-config.json", tracked)

    def test_no_model_weights_are_tracked(self) -> None:
        """Catches: a detector or checkpoint being committed, which leaks both
        bytes and the owner's model choices."""

        offenders = [str(p.relative_to(APP_ROOT)) for p in self.files
                     if p.suffix.lower() in (".pt", ".safetensors", ".ckpt")]
        self.assertEqual([], offenders)


class PatternSanityTests(unittest.TestCase):
    """A guard that cannot fail is not a guard."""

    def test_the_pattern_matches_a_real_private_path(self) -> None:
        for leak in (r"C:\Users\someone\Desktop\thing",
                     "C:/Users/someone/Desktop/thing",
                     "/home/someone/models",
                     "/Users/someone/Library"):
            with self.subTest(leak=leak):
                self.assertIsNotNone(PRIVATE_PATH.search(leak))

    def test_a_placeholder_user_is_not_treated_as_a_person(self) -> None:
        """A placeholder user is a fixture; a real one is a person.

        The distinction cannot be made by pattern -- both are the
        same shape -- so it is made by an explicit set.
        """

        self.assertIn("owner", PLACEHOLDERS)
        self.assertNotIn("heras", PLACEHOLDERS)

    def test_the_pattern_allows_placeholders_and_relative_paths(self) -> None:
        """Documentation must be able to TALK about paths."""

        for allowed in ("$WORKSPACE/app", "<WORKSPACE>/app",
                        "%USERPROFILE%\\Studio", "./venv/Scripts/python.exe",
                        "../studio-config.json", "C:/Users/<name>/Studio",
                        r"\\192.168.1.10\models", r"\\.\PhysicalDrive0",
                        "C:\\Users\\%USERNAME%\\Studio"):
            with self.subTest(allowed=allowed):
                self.assertIsNone(PRIVATE_PATH.search(allowed))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_DISTRIBUTABLE_PRIVACY_TESTS,
                         suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
