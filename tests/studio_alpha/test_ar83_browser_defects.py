"""Guards for three defects that only a browser found.

All three survived 4,318 passing tests. Each one is an INSTANCE of a class this
project keeps re-encountering, so each guard here is written against the
structural invariant rather than against the one line that happened to be
wrong:

1. a role set enumerated a fifth time, and one role left out of it;
2. a control whose minimum value the consuming arithmetic cannot survive;
3. boot work that runs before the async state its guard reads has arrived.

Evidence: `Evidence/ar8.3-browser-baseline/findings.md` and
`Evidence/ar8.3-browser-baseline/canvas-baseline.md`.

Every scan strips comments first. This codebase comments its reasoning
heavily, including quoting the very expressions its guards ban, and a guard
that matches its own explanation has already fired falsely here more than once.
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


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


APP_JS = _strip_js_comments(_read("app.js"))
CANVAS_CORE = _strip_js_comments(_read("canvas-core.js"))
DIR_PICKER = _strip_js_comments(_read("studio-dir-picker.js"))
INDEX_HTML = _read("index.html")


def _balanced_block(source: str, opener: str) -> str:
    """The text from ``opener`` to the delimiter that closes it.

    Used instead of a line window because these declarations move, and a guard
    pinned to a line number stops guarding the moment someone inserts a line
    above it.

    The delimiter is chosen from the opener: an array literal is balanced on
    brackets, a function on braces. Balancing an array on braces returns only
    its FIRST element, which silently turned the role-set guard below into a
    guard on one role -- it reported `{'checkpoint'}` and nothing else.
    """

    start = source.index(opener)
    if opener.rstrip().endswith("["):
        open_char, close_char = "[", "]"
    else:
        open_char, close_char = "{", "}"

    depth = 0
    for index in range(start, len(source)):
        character = source[index]
        if character == open_char:
            depth += 1
        elif character == close_char:
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced block for {opener!r}")


class ModelRootRoleSetTests(unittest.TestCase):
    """Every role with a status line must be in the table that renders it.

    `MODEL_ROOT_FIELDS` is the ONLY thing that writes `#modelRootStatus*` or
    fills `#modelRoot*` from the server. When `lora` was left out of it, the
    LoRA row kept its static "Not configured" default while the same server
    response reported `status: "ready", entry_count: 73` and the picker listed
    all 73. The save path had already been repaired separately, which is why
    the root really was configured while Settings denied it.
    """

    def test_every_status_element_has_a_field_entry(self):
        declared = set(re.findall(r'id="modelRootStatus(\w+)"', INDEX_HTML))
        self.assertTrue(declared, "no model-root status elements found in index.html")

        table = _balanced_block(APP_JS, "const MODEL_ROOT_FIELDS = [")
        rendered = set(re.findall(r'status:\s*"modelRootStatus(\w+)"', table))

        missing = declared - rendered
        self.assertEqual(
            missing,
            set(),
            "index.html declares a model-root status line that MODEL_ROOT_FIELDS "
            f"never writes, so it keeps its static default forever: {sorted(missing)}",
        )

    def test_every_field_entry_has_an_input_and_a_browse_button(self):
        table = _balanced_block(APP_JS, "const MODEL_ROOT_FIELDS = [")
        for element_id in re.findall(r'(?:input|browse):\s*"(\w+)"', table):
            self.assertIn(
                f'id="{element_id}"',
                INDEX_HTML,
                f"MODEL_ROOT_FIELDS points at #{element_id}, which index.html does not have",
            )

    def test_the_field_table_agrees_with_the_picker_role_set(self):
        """The fourth spelling and the fifth must not disagree again.

        `studio-dir-picker.js` owns Browse, Add and the roots list; the app.js
        table owns the status line and the single input. A role the picker can
        configure but the table never renders is precisely the defect that
        shipped.
        """

        picker_roles = set(
            re.findall(
                r'"([a-z_]+)"',
                _balanced_block(DIR_PICKER, "const ROLES = ["),
            )
        )
        table_roles = set(
            re.findall(
                r'role:\s*"([a-z_]+)"',
                _balanced_block(APP_JS, "const MODEL_ROOT_FIELDS = ["),
            )
        )
        self.assertTrue(picker_roles, "studio-dir-picker.js ROLES did not parse")
        self.assertTrue(table_roles, "MODEL_ROOT_FIELDS roles did not parse")

        unknown = table_roles - picker_roles
        self.assertEqual(
            unknown,
            set(),
            f"MODEL_ROOT_FIELDS names a role the picker does not know: {sorted(unknown)}",
        )

        # A role that has a status element AND is configurable in the picker
        # must be rendered. Roles the picker handles with a different surface
        # (the detector summary) are not forced into this table.
        status_roles = {
            suffix.lower()
            for suffix in re.findall(r'id="modelRootStatus(\w+)"', INDEX_HTML)
        }
        for role in picker_roles:
            compact = role.replace("_", "")
            if compact in status_roles:
                self.assertIn(
                    role,
                    table_roles,
                    f"role {role!r} has a status line and is configurable, but "
                    "MODEL_ROOT_FIELDS does not render it",
                )


class BrushStabilizerTests(unittest.TestCase):
    """The stabilizer must survive the minimum the slider offers.

    THE DEFECT. `stab()` averaged the last `w` samples and divided by `w`.
    With `w = Math.min(S.smoothing, pts.length)` and a slider whose minimum is
    0, `w` was 0, the loop never ran, and the return was `0 / 0` -- NaN for x,
    y and pressure. Every stamp after the opening dab landed at NaN, so a
    dense 61-sample stroke painted 3,740 pixels instead of 30,990.

    AR83 fixed it with `Math.max(1, Math.min(...))` and guarded the clamp.

    BE9 DELETED THE DIVISOR. The window is now an arc length rather than a
    sample count, the mean is weighted by the path each sample represents, and
    `stab()` returns the raw point before any arithmetic at level 0. There is
    no `w` left to clamp -- the class of bug is removed by construction instead
    of guarded against.

    So these guards are rewritten, not deleted. The invariant AR83 cared about
    is unchanged and still owner-visible: THE MINIMUM THE SLIDER OFFERS MUST
    NOT MAKE THE BRUSH PAINT NOTHING. What changed is which line enforces it.

    The behavioural half lives in `test_be9_stabiliser.py`, which EXECUTES the
    engine at every smoothing level the control exposes and compares painted
    pixels. A structural guard cannot prove a number is finite; it can only
    prove the shape that would make it infinite is absent. Both are kept,
    because AR83's own lesson was that this defect reached a browser through
    4,318 passing tests.
    """

    def _smoothing_minimum(self) -> int:
        match = re.search(
            r'data-key="smoothing"[^>]*?data-min="(-?\d+)"', INDEX_HTML
        )
        if match is None:
            match = re.search(
                r'data-min="(-?\d+)"[^>]*?data-key="smoothing"', INDEX_HTML
            )
        self.assertIsNotNone(match, "the smoothing control's data-min did not parse")
        return int(match.group(1))

    def test_the_minimum_of_the_control_reaches_no_arithmetic(self):
        """The rewrite of AR83's clamp guard.

        The clamp existed because the minimum flowed into a division. BE9's
        `stab()` returns first, so the guard is that the early return is there
        and covers the whole range at or below the control's minimum.
        """

        minimum = self._smoothing_minimum()
        body = _balanced_block(CANVAS_CORE, "function stab(")

        self.assertNotIn(
            "/ w",
            body,
            "stab() divides by a sample-count window again; the AR83 clamp "
            "guard below was removed on the strength of that divisor being "
            "gone, and needs restoring with it",
        )

        if minimum >= 1:
            return  # the control cannot produce a zero window

        self.assertIn(
            "if (level <= 0 || pts.length < 2) return",
            body,
            "the smoothing control offers a value of "
            f"{minimum}, so stab() must return the raw point before it computes "
            "a window at all -- or the brush returns NaN and paints nothing",
        )

    def test_the_weighted_mean_cannot_divide_by_zero(self):
        """The other place a zero divisor could reappear.

        `_arcCentroid` accumulates arc length and divides by it. A stroke whose
        samples are all coincident -- a pen held still, which is exactly what a
        stabilizer is for -- accumulates nothing. That must fall back to the
        raw value rather than divide.
        """

        body = _balanced_block(CANVAS_CORE, "function _arcCentroid(")
        self.assertIn(
            "return total > 0 ? sum / total : fallback;",
            body,
            "_arcCentroid divides by an accumulated length without checking it "
            "is non-zero; coincident samples would return NaN, which is the "
            "AR83 defect in a new divisor",
        )

    def test_the_guard_is_code_and_not_commentary(self):
        """The file quotes the broken expression on purpose, in more than one
        place now: `stab()`'s comment explains what BE9 replaced, and
        `_sampleMean` is kept verbatim with no caller as the mutation
        harness's target.

        A guard that read the file without stripping comments would pass on
        the explanation alone. This project has fired that false positive four
        separate times, which is why AR83 wrote this test in the first place
        and why it is kept through the rewrite.
        """

        raw_body = _balanced_block(_read("canvas-core.js"), "function stab(")
        stripped_body = _balanced_block(CANVAS_CORE, "function stab(")
        self.assertIn("_arcCentroid(pts,", stripped_body)
        self.assertLess(
            len(stripped_body),
            len(raw_body),
            "comments were expected above stab(); the stripper may have stopped working",
        )

    def test_the_retired_expression_survives_only_where_nothing_runs_it(self):
        """`Math.min(S.smoothing, pts.length)` is still in the file, inside
        `_sampleMean`, deliberately. It must not be inside `stab()`.

        Asserted here as well as in `test_be9_stabiliser.py` because AR83 is
        the module that owns "a control whose minimum the consuming arithmetic
        cannot survive", and the retired expression IS that arithmetic.
        """

        body = _balanced_block(CANVAS_CORE, "function stab(")
        self.assertNotIn("Math.min(S.smoothing, pts.length)", body)
        self.assertEqual(
            1, CANVAS_CORE.count("Math.min(S.smoothing, pts.length)"),
            "the retired sample-count window appears somewhere other than the "
            "single mutation target it is kept for",
        )


class LifecycleBootRaceTests(unittest.TestCase):
    """The legacy load must not fire before the lifecycle host has answered.

    `lifecycleAvailable()` starts false and is affirmed only after the first
    `/api/model/state` reply. Two boot paths reached the loader before that:
    the component-restore timer, and the `change` event `restorePreference`
    dispatches on `#paramModel`. Losing that race sent a legacy
    POST /studio/load_model, which the route refuses, and raised a red
    "Model load failed" toast on every launch of a working product.
    """

    def _loader_body(self) -> str:
        return _balanced_block(
            APP_JS, "async function loadSelectedModelComponents("
        )

    def test_the_loader_waits_before_it_decides(self):
        body = self._loader_body()
        self.assertIn(
            "await _awaitLifecycleAnswer()",
            body,
            "loadSelectedModelComponents must wait for the lifecycle host to "
            "answer before choosing between the lifecycle contract and the "
            "legacy POST",
        )

    def test_the_wait_precedes_both_the_guard_and_the_legacy_post(self):
        body = self._loader_body()
        wait_at = body.index("await _awaitLifecycleAnswer()")
        guard_at = body.index("lifecycleAvailable()")
        post_at = body.index("/studio/load_model")
        self.assertLess(
            wait_at,
            guard_at,
            "the lifecycle guard is read before the answer is awaited, which is "
            "the race itself",
        )
        self.assertLess(wait_at, post_at)

    def test_the_wait_is_bounded_and_costs_a_non_lifecycle_host_nothing(self):
        body = _balanced_block(APP_JS, "function _awaitLifecycleAnswer(")
        self.assertRegex(
            body,
            r"timeoutMs\s*=\s*\d+",
            "the wait must be bounded, or a host whose lifecycle never answers "
            "would hang instead of falling back to the legacy path",
        )
        self.assertIn(
            "setTimeout(finish",
            body,
            "the bound must actually resolve the wait",
        )
        self.assertIn(
            "return Promise.resolve()",
            body,
            "a page with no lifecycle module must not wait at all",
        )

    def test_the_wait_is_polled_as_well_as_evented(self):
        """`announceLifecycle()` only fires when the reported state CHANGES.

        An event-only wait would sit until the bound expired on any host whose
        first reply matches the already-announced state, turning a fixed race
        into a fixed four-second delay.
        """

        body = _balanced_block(APP_JS, "function _awaitLifecycleAnswer(")
        self.assertIn("setInterval(check", body)
        self.assertIn('addEventListener("studio:model-state-changed"', body)
        self.assertIn('removeEventListener("studio:model-state-changed"', body)


class ParityLedgerAttributionTests(unittest.TestCase):
    """The ledger must not name a caller that only a comment mentions.

    `_REFERENCE` requires the path to sit inside a quote or backtick so prose
    cannot be mistaken for a call. That is not enough in this codebase, which
    writes its reasoning in markdown inside `//` comments -- and a
    markdown-backticked route is byte-identical to a template literal.

    Three routes were attributed to a file that never calls them before this
    was fixed, each one prose ABOUT another file's caller. A ledger that names
    a caller which does not exist overstates coverage in the one artifact used
    to check coverage.
    """

    @classmethod
    def setUpClass(cls) -> None:
        scripts = APP_ROOT / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import parity_ledger  # noqa: PLC0415

        cls.ledger = parity_ledger

    def test_a_backticked_route_in_a_comment_is_not_a_reference(self):
        source = (
            "// The page asks `/studio/not-a-real-route` elsewhere.\n"
            "/* and `/api/also-not-real` in a block */\n"
            'const real = "/studio/really-called";\n'
        )
        stripped = self.ledger.strip_comments(source)
        found = {
            match.group("path")
            for match in self.ledger._REFERENCE.finditer(stripped)
        }
        self.assertEqual(found, {"/studio/really-called"})

    def test_a_trailing_comment_does_not_take_the_code_with_it(self):
        source = 'const url = "/studio/kept";  // see `/studio/dropped`\n'
        stripped = self.ledger.strip_comments(source)
        self.assertIn("/studio/kept", stripped)

    def test_every_attributed_caller_really_names_its_route(self):
        """The invariant, applied to the whole generated ledger."""

        references, _scanned = self.ledger.frontend_references()
        frontend = APP_ROOT / "forge_studio" / "frontend"
        sources = {
            path.name: self.ledger.strip_comments(self.ledger.read_source(path))
            for path in self.ledger.frontend_files()
        }
        self.assertTrue(frontend.is_dir())

        for route, callers in references.items():
            tail = route.rsplit("/", 1)[-1]
            for caller in callers:
                body = sources.get(caller, "")
                self.assertIn(
                    tail,
                    body,
                    f"the ledger attributes {route} to {caller}, but nothing "
                    "outside a comment in that file names it",
                )


class SuiteIntegrityTests(unittest.TestCase):
    """The count is declared so a silently dropped class is visible."""

    def test_the_declared_count_matches_what_is_discovered(self):
        loader = unittest.defaultTestLoader
        discovered = loader.loadTestsFromName(__name__)
        found = discovered.countTestCases()
        self.assertEqual(
            found,
            EXPECTED_AR83_TESTS,
            f"this module declares {EXPECTED_AR83_TESTS} tests and discovered {found}",
        )


EXPECTED_AR83_TESTS = 15


if __name__ == "__main__":
    unittest.main()
