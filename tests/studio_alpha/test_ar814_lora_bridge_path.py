"""AR8.14: `<lora:...>` did nothing on a live server, and said so in the log.

`lora_bridge` exists to close exactly one defect, and its own docstring records
the measurement: 73 LoRAs listed, one of their names in the prompt, same seed,
everything else identical, `0 of 262,144 pixels differing, 0.00%`.

On a live server it had never closed. Every generation logged

```text
WARNING studio.lora: LoRA support could not be armed, so <lora:...> tags will
be ignored: LoraUnavailable: The LoRA extension could not be imported:
cannot import name 'cache' from 'modules' (unknown location)
```

and went on producing the image you would get without the tag. The failure was
REPORTED, which is what `live_generation_port` insists on and is the only
reason this was findable at all -- but it was reported to a log file, and the
owner sees a picture.

## The cause

`app/modules/` has no `__init__.py`, so `modules` is a NAMESPACE package, and a
namespace package's `__path__` is recomputed whenever `sys.path` changes.
`_load_modules` changes `sys.path` -- that is its whole job, because the
extension's files import each other by bare name. If the engine root is not on
`sys.path` at that moment, the recomputation finds nothing, and
`from modules import cache` at `network.py:4` fails with "(unknown location)",
which is what Python says about a namespace package that resolved to nowhere.

Forge never hits this because `load_scripts()` runs with the engine root
importable. The bridge reproduced half of what Forge arranges.

## The fix

Both directories go on for the duration, and exactly what was put on comes off
again -- the `finally` still matters for the reason it always did.
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

EXPECTED_AR814_TESTS = 12

SOURCE = (APP_ROOT / "forge_headless" / "lora_bridge.py").read_text(
    encoding="utf-8")


def _code_only(source: str) -> str:
    """Docstrings and comments removed.

    This module's own docstring quotes the failing import verbatim, and this
    file's guards look for that same text in the code. A word-match that hits
    the prose explaining the ban rather than the ban is a trap this project has
    recorded four times.
    """

    stripped = re.sub(r'"""(?:.|\n)*?"""', "", source)
    return re.sub(r"^\s*#.*$", "", stripped, flags=re.MULTILINE)


CODE = _code_only(SOURCE)


def _load_modules_body() -> str:
    start = CODE.index("def _load_modules(")
    end = CODE.index("\ndef ", start + 1)
    return CODE[start:end]


class TheEngineRootIsOnThePathTests(unittest.TestCase):
    def test_the_extension_directory_is_still_added(self):
        """The original half, which was right and must not be lost."""

        body = _load_modules_body()
        self.assertIn("directory = str(_EXTENSION)", body)

    def test_the_engine_root_is_added_too(self):
        body = _load_modules_body()
        self.assertRegex(
            body, r"root = str\(_EXTENSION\.parents\[1\]\)",
            "the engine root is not put on sys.path, so `from modules import "
            "cache` can resolve to nothing mid-import",
        )

    def test_the_extension_sits_two_levels_under_the_engine_root(self):
        """The arithmetic, checked against the filesystem rather than trusted.

        `parents[1]` is only the engine root while the extension lives at
        `<root>/extensions-builtin/sd_forge_lora`. Moving it would silently put
        the wrong directory on the path.
        """

        from forge_headless.lora_bridge import _EXTENSION

        self.assertEqual(APP_ROOT, _EXTENSION.parents[1])
        self.assertEqual("extensions-builtin", _EXTENSION.parent.name)

    def test_both_are_inserted_before_the_import(self):
        body = _load_modules_body()
        insert = body.index("sys.path.insert(0, entry)")
        self.assertLess(insert, body.index('importlib.import_module("networks")'))

    def test_only_what_was_added_is_removed(self):
        """An entry already on the path belongs to whoever put it there."""

        body = _load_modules_body()
        self.assertRegex(
            body, r"added = \[\s*entry for entry in \(directory, root\)"
                  r" if entry not in sys\.path\s*\]")
        self.assertIn("for entry in added:", body)

    def test_the_removal_is_in_a_finally(self):
        """Leaving either directory behind would let a later `import network`
        anywhere in the process resolve to a LoRA internal."""

        body = _load_modules_body()
        finally_at = body.index("finally:")
        self.assertLess(finally_at, body.index("sys.path.remove(entry)"))

    def test_a_missing_entry_on_removal_is_not_an_error(self):
        body = _load_modules_body()
        self.assertIn("except ValueError:", body)


class TheFailureSaysEnoughToActOnTests(unittest.TestCase):
    """"could not be imported: cannot import name 'cache'" could not
    distinguish "this build has no LoRA code" from "the code is there and
    `modules` briefly stopped resolving", and those need opposite fixes."""

    def test_the_refusal_names_the_search_path(self):
        body = _load_modules_body()
        self.assertIn("modules search path:", body)
        self.assertIn("getattr(modules, '__path__', [])", body)

    def test_a_build_without_the_extension_still_says_so_first(self):
        body = _load_modules_body()
        missing = body.index("The LoRA extension is not in this build.")
        self.assertLess(missing, body.index("sys.path.insert(0, entry)"))


class TheArmingContractIsUnchangedTests(unittest.TestCase):
    def test_arming_twice_registers_once(self):
        """`register_extra_network` appends to a list, so arming twice would
        apply every LoRA twice."""

        self.assertRegex(
            CODE, r"global _ARMED\s*\n\s*if _ARMED:\s*\n\s*return True")

    def test_the_roots_projection_precedes_the_scan(self):
        """The engine scans `[cmd_opts.lora_dir, *cmd_opts.lora_dirs]`, and
        Studio has no command line to put the owner's folders on."""

        arm = CODE[CODE.index("def arm("):]
        self.assertLess(
            arm.index("shared.cmd_opts.lora_dirs = list(roots)"),
            arm.index("networks.list_available_networks()"),
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_AR814_TESTS, found)


if __name__ == "__main__":
    unittest.main()
