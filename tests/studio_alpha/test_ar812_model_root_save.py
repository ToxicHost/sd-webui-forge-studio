"""Saving model folders must not delete the folders it was not told about.

`/studio/settings/model_roots` is all-or-nothing by contract:
`ModelRootSettings.apply` hands the payload straight to
`ModelRootRegistry.configure`, whose docstring is "Replace every role at once.
All-or-nothing", and `_storable_roots` then omits absent roles from
studio-config.json. There is no merge anywhere on that path.

So the browser must post the COMPLETE mapping. It used to post only the
non-empty text boxes, and `studio-dir-picker` clears the box after "Add
folder" -- so the ordinary sequence of adding a folder and pressing Save
destroyed the folder that had just been added.

Guards are written against the invariant, not the literal that was wrong.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

EXPECTED_AR812_TESTS = 9


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


APP_JS = _strip_js_comments((FRONTEND / "app.js").read_text(encoding="utf-8"))
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
EN_JSON = json.loads(
    (FRONTEND / "locales" / "en.json").read_text(encoding="utf-8")
)


def _balanced(source: str, opener: str) -> str:
    start = source.index(opener)
    depth = 0
    opened = False
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced block for {opener!r}")


SAVE_HANDLER = _balanced(
    APP_JS, 'document.getElementById("modelFoldersSave")?.addEventListener'
)
READ_ROOTS = _balanced(APP_JS, "async function _readModelRoots()")


class SavePostsEverythingTests(unittest.TestCase):
    def test_the_handler_reads_what_is_configured_before_posting(self):
        """Without this the payload can only describe the text boxes."""

        self.assertIn(
            "_readModelRoots()", SAVE_HANDLER,
            "the save handler must find out what is currently configured "
            "before replacing it, because the route replaces every role at once",
        )

    def test_the_payload_is_built_from_the_configured_roots(self):
        self.assertRegex(
            SAVE_HANDLER, r"current\s*&&\s*current\.roots",
            "the posted mapping must start from the server's configured roots",
        )

    def test_a_blank_box_cannot_drop_a_role(self):
        """The specific defect: `if (value) roots[role] = value` and nothing
        else, so an empty box omitted the role and the omission deleted it."""

        # Every assignment into the posted mapping, in order.
        assignments = re.findall(r"roots\[[^\]]+\]\s*=\s*([^;]+);", SAVE_HANDLER)
        self.assertTrue(assignments, "no assignment into the posted mapping")
        self.assertFalse(
            any(a.strip() in ("value", "typed[role]") for a in assignments),
            "the mapping is assigned a single typed value, which is the shape "
            f"that dropped every unmentioned role: {assignments}",
        )

    def test_typed_values_are_added_rather_than_replacing(self):
        self.assertIn("indexOf(", SAVE_HANDLER)
        self.assertRegex(SAVE_HANDLER, r"list\.push\(")

    def test_the_boxes_are_cleared_once_accepted(self):
        self.assertRegex(
            SAVE_HANDLER, r'input\.value\s*=\s*""',
            "an entry box that keeps its contents re-adds the same path on the "
            "next save",
        )


class TheBoxIsAnEntryFieldTests(unittest.TestCase):
    def test_the_read_no_longer_fills_the_box_from_the_server(self):
        """`document_.roots[role]` is an ARRAY.

        Assigning it to an input stringifies it, so a role with two folders
        rendered as `C:/a,C:/b` -- a string that is not a path, which the save
        then posted as one. What is configured is shown by the roots list
        beneath each row, which `studio-dir-picker` renders with a path, a
        status and a Remove per root.
        """

        self.assertNotRegex(
            READ_ROOTS, r"input\.value\s*=\s*roots\[",
            "the entry box is being filled from the server's root ARRAY again",
        )

    def test_the_placeholder_does_not_promise_that_blank_unconfigures(self):
        """The old text described the behaviour that was destroying folders.

        en.json wins over the markup, so the locale is what must be right.
        """

        placeholder = EN_JSON["settings.modelFolders.placeholder"]
        lowered = placeholder.lower()
        self.assertNotIn("unconfigured", lowered)
        self.assertNotIn("leave blank", lowered)

    def test_the_markup_fallback_agrees_with_the_locale(self):
        """A fallback that contradicts the locale is a sentence nobody sees
        and everybody trusts."""

        placeholder = EN_JSON["settings.modelFolders.placeholder"]
        self.assertIn(f'placeholder="{placeholder}"', INDEX_HTML)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_AR812_TESTS, found)


if __name__ == "__main__":
    unittest.main()
