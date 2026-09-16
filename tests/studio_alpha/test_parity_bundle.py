"""The review bundle's gates, and the redaction that keeps it shareable.

`scripts/build_parity_bundle.py` assembles the artefact a reviewer judges
parity from. Two things make such an artefact worth trusting, and neither was
true of it before:

1. **It must refuse to build when it is wrong about itself.** Its manifest
   advertised 923 included paths for 837 real files, because the whole-tree
   pass re-copied what the import walk had already taken and `take` counted
   every copy. That number was PRINTED for anyone to read and enforced by
   nothing, so it shipped. `gate_problems` is the enforcement.

2. **It must not carry the owner's private world.** `studio-config.json` went
   in raw: home directory, private model roots, model filenames -- in a bundle
   built to be handed to someone else.

These exercise the logic directly rather than building 57 MB, so the suite
stays fast and tests the rules rather than this machine's tree.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
if str(APP_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(APP_ROOT / "scripts"))

import build_parity_bundle as collector  # noqa: E402

EXPECTED_PARITY_BUNDLE_TESTS = 11

OK_CONFIG = {"redacted": True}


class GateTests(unittest.TestCase):
    def test_a_clean_build_has_no_problems(self) -> None:
        self.assertEqual([], collector.gate_problems(
            ["a", "b"], [], ["BOOK.md"], OK_CONFIG))

    def test_a_duplicated_path_fails_the_build(self) -> None:
        """The defect that shipped. 923 advertised, 837 real."""

        problems = collector.gate_problems(
            ["a", "b", "a"], [], ["BOOK.md"], OK_CONFIG)
        self.assertEqual(1, len(problems))
        self.assertIn("3 included paths but 2 unique", problems[0])

    def test_the_duplicate_message_names_the_offenders(self) -> None:
        """A count alone sends the next reader back to the whole tree."""

        problems = collector.gate_problems(
            ["x", "x"], [], ["BOOK.md"], OK_CONFIG)
        self.assertIn("'x'", problems[0])

    def test_a_seed_that_does_not_exist_fails_the_build(self) -> None:
        """`modules/processing_scripts.py` was seeded and is a DIRECTORY, so
        the seed resolved to nothing and six real files were silently absent
        from a bundle that named the area as under review."""

        problems = collector.gate_problems(
            ["a"], ["modules/processing_scripts.py"], ["BOOK.md"], OK_CONFIG)
        self.assertEqual(1, len(problems))
        self.assertIn("silently omits an area", problems[0])

    def test_a_bundle_with_no_governing_document_fails(self) -> None:
        problems = collector.gate_problems(["a"], [], [], OK_CONFIG)
        self.assertIn("cannot judge parity against a plan they lack",
                      problems[0])

    def test_an_unredacted_config_fails_the_build(self) -> None:
        problems = collector.gate_problems(
            ["a"], [], ["BOOK.md"], {"redacted": False})
        self.assertIn("not redacted", problems[0])

    def test_problems_accumulate_rather_than_short_circuiting(self) -> None:
        """One run should reveal the whole list, not the first item."""

        problems = collector.gate_problems(
            ["a", "a"], ["missing.py"], [], {"redacted": False})
        self.assertEqual(4, len(problems))


class RedactionTests(unittest.TestCase):
    def test_absolute_paths_are_replaced_at_any_depth(self) -> None:
        document = {
            "model_roots": {"checkpoint": "C:\\Users\\someone\\Models"},
            "profiles": [{"payload_references": {"vae": "/home/x/vae.st"}}],
        }
        scrubbed = collector.scrub_paths(document)
        self.assertEqual("<redacted-absolute-path>",
                         scrubbed["model_roots"]["checkpoint"])
        self.assertEqual(
            "<redacted-absolute-path>",
            scrubbed["profiles"][0]["payload_references"]["vae"])

    def test_the_shape_and_the_ordinary_values_survive(self) -> None:
        """A reviewer needs the structure; only the private values go."""

        document = {"host": "127.0.0.1", "port": 0, "autoload": False,
                    "root": "C:/Users/someone"}
        scrubbed = collector.scrub_paths(document)
        self.assertEqual("127.0.0.1", scrubbed["host"])
        self.assertEqual(0, scrubbed["port"])
        self.assertIs(False, scrubbed["autoload"])
        self.assertEqual({"host", "port", "autoload", "root"},
                         set(scrubbed))

    def test_a_relative_path_is_not_treated_as_private(self) -> None:
        """`result_root: "Studio-Results"` is configuration, not disclosure."""

        for value in ("Studio-Results", "models/vae", "", "a"):
            with self.subTest(value=value):
                self.assertFalse(collector._looks_like_path(value))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PARITY_BUNDLE_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
