"""NG-3: nothing new may hide below the lifecycle `return`.

`doGenerate` takes the lifecycle branch and returns from it on every real
install. Everything after that return still parses, still reads the DOM, and
still builds a request -- and never runs. NINE separate things were found down
there, one at a time, each because an owner hit a symptom or an audit tripped
over it:

    progress messages, progress presentation, inpaint payload, result swap,
    output format, batch fields, metadata toggle, Gallery notification,
    LoRA compilation and prompt cleanup

This is the guard that turns that from a discovery into a failure. It does not
re-find the nine; earlier commits fixed those. It fails when a TENTH appears.

The audit behind the dispositions below is
`Evidence/source-review/AR8.5-below-the-return.md`. Every id listed here was
read in source, not inferred from its name.
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

EXPECTED_NG3_TESTS = 6


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


#: Every way `app.js` names an element it READS.
#:
#: `getElementById` alone is not enough, and getting this wrong is not
#: hypothetical: the first run of this audit matched only `getElementById` and
#: reported `paramFill`, `paramMaskBlur` and `paramPadding` as read solely
#: below the return, when the live inpaint collector reads all three through
#: `_num(id, fallback)`. A scanner that produces a confident wrong number is
#: the thing this audit replaces, so the pattern covers the helpers too.
_ID_READ = re.compile(
    r'(?:getElementById|_num|_seedFieldValue)\(\s*["\'`]([A-Za-z0-9_\-]+)["\'`]'
)


#: Ids read ONLY below the return, each with why that is not a defect.
#:
#: To add an entry here you must have read its live reader. "It is probably
#: fine" is how the first nine got there.
DISPOSITIONED_BELOW_RETURN = {
    "genBtn": "not generation-affecting -- the button's own label and busy class",
    "progressFill": "not generation-affecting -- the progress bar element",
    "statusModel": "not generation-affecting -- a status-bar label",
    "paramHrCheckpoint": (
        "honestly disabled -- the cell carries `hidden` in index.html and the "
        "owner's decision to keep it hidden is recorded since P0.7"
    ),
    "paramModel": (
        "reachable by another route -- the live request carries "
        "`model_selection.checkpoint_model_id`, built outside this function"
    ),
    "paramVAE": (
        "reachable by another route -- the live request carries "
        "`model_selection.vae_model_id`"
    ),
    "paramSeed": (
        "reachable by another route -- the live half calls "
        "`_resolveSubmittedSeed()`, which reads this element and resolves a "
        "random seed to a concrete one"
    ),
    "toggleLivePreview": (
        "reachable by another route -- its click handler maintains "
        "`State.livePreview`, and the live payload sends "
        "`preview_enabled: !!State.livePreview`"
    ),
    "toggleStudioDynPrompts": (
        "reachable by another route -- its click handler POSTs "
        "`dynPromptsSetConfig`, and the live path gates expansion on the "
        "STORED config via `WildcardService.enabled()` rather than on a "
        "request field"
    ),
}


class _Region(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = (FRONTEND / "app.js").read_text(encoding="utf-8")
        cls.source = _strip_js_comments(cls.raw)

        start = cls.source.index("async function doGenerate(")
        # Counting begins at the FUNCTION BODY's brace, not at the start of
        # the match. NG-4 gave `doGenerate` a defaulted parameter, and a
        # counter that started before `overrides = {}` closed on that empty
        # object and handed every test below a body one line long -- which
        # every one of them then passed against, silently.
        body_brace = cls.source.index(") {", start) + 2
        depth = 0
        opened = False
        end = None
        for index in range(body_brace - 1, len(cls.source)):
            character = cls.source[index]
            if character == "{":
                depth += 1
                opened = True
            elif character == "}":
                depth -= 1
                if opened and depth == 0:
                    end = index + 1
                    break
        if end is None:
            raise AssertionError("doGenerate never closes")

        cls.body = cls.source[start:end]
        anchor = cls.body.index("lifecycle.submitGenerate(")
        boundary = cls.body.index("\n    return;", anchor) + len("\n    return;")
        cls.live = cls.body[:boundary]
        cls.dead = cls.body[boundary:]

    def ids(self, text: str) -> set[str]:
        return set(_ID_READ.findall(text))


class BoundaryTests(_Region):
    def test_the_boundary_is_still_findable(self):
        """A guard that cannot find its own boundary is not guarding.

        Both halves must be substantial. If a refactor moves the submit call or
        deletes the early return, this fails loudly instead of quietly passing
        over an empty region.
        """

        self.assertGreater(len(self.live), 2000)
        self.assertGreater(len(self.dead), 2000)
        self.assertIn("lifecycle.submitGenerate(", self.live)
        self.assertNotIn("lifecycle.submitGenerate(", self.dead)

    def test_the_live_half_still_builds_the_request(self):
        """The fields whose absence was the original defect.

        Each of these was found below the return once. Asserting they are ABOVE
        it means a refactor cannot quietly send them back down.
        """

        for field in (
            "positive_prompt",
            "negative_prompt",
            "seed",
            "steps",
            "cfg_scale",
            "width",
            "height",
            "operation",
        ):
            self.assertIn(field, self.live, f"{field} left the live request")


class NoTenthThingTests(_Region):
    def test_every_below_return_only_id_is_dispositioned(self):
        """The whole point. A tenth thing must fail here, not in a browser."""

        below_only = self.ids(self.dead) - self.ids(self.live)
        undispositioned = sorted(below_only - set(DISPOSITIONED_BELOW_RETURN))
        self.assertEqual(
            undispositioned,
            [],
            "these elements are read only BELOW the lifecycle return, where "
            "nothing runs on a real install. Read the live reader for each, "
            "then add it to DISPOSITIONED_BELOW_RETURN with what you found -- "
            f"or fix it: {undispositioned}",
        )

    def test_the_dispositions_have_not_gone_stale(self):
        """A disposition for an id nobody reads down there any more is noise.

        It also hides the next one: an allowlist nobody prunes stops meaning
        anything.
        """

        below_only = self.ids(self.dead) - self.ids(self.live)
        stale = sorted(set(DISPOSITIONED_BELOW_RETURN) - below_only)
        self.assertEqual(
            stale,
            [],
            "these ids are dispositioned as read-only-below-the-return but no "
            f"longer are; drop them from the table: {stale}",
        )

    def test_every_disposition_says_something(self):
        for key, reason in DISPOSITIONED_BELOW_RETURN.items():
            self.assertGreater(
                len(reason), 30,
                f"{key} needs a reason that names its live reader",
            )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_NG3_TESTS, found)


if __name__ == "__main__":
    unittest.main()
