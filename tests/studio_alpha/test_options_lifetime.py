"""R1.5 Phase B — the options object belongs to the process, not to a load.

Handoff §21 and §45B. The measurement that forces this shape, taken 2026-08-11
by AST over the tree:

```text
from modules.shared import opts     40 modules   bind the VALUE at import time
import modules.shared               79 modules   bind the module; read late
```

Twenty-one in `modules/`, eighteen in `backend/`, one in `modules_forge/` --
including `backend/diffusion_engine/anima.py:11`,
`modules/sd_samplers_kdiffusion.py:12`, `modules/sd_samplers_common.py:29` and
`modules/processing.py:34`.

A name bound that way is resolved once and never re-read. So an options bridge
that installs an object for one load and restores the previous value on close
leaves all forty holding the object it just uninstalled; the next load builds a
second, and the two populations disagree. `neo_registries._read` records a
generation failing for exactly that reason, and `live_bindings` already carries
the same lesson for `shared.state`, where the fix was to adopt the bridge the
samplers already hold rather than build another.

`process_options()` is that fix for `opts`: construct once, install once, never
restore, and update overrides in place.

These tests are about OBJECT IDENTITY, which is the property that matters and
the one a value-equality assertion would miss entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_OPTIONS_LIFETIME_TESTS = 11

#: Runs in a subprocess: `process_options` installs onto the real
#: `modules.shared`, and module state that survives for the life of a process
#: is exactly what is under test. Doing it in-process would leak into every
#: suite that ran afterwards.
_PROBE = """
import sys, os, json
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "modules_forge", "packages"))
sys.argv = ["studio"]
from pathlib import Path
import modules.shared as shared
from forge_headless.headless_options import (
    installed_process_options, output_directory_overrides, process_options,
)

root = Path(APP)
before_any = installed_process_options() is None

# Two "loads", the second with different overrides -- the case that used to
# build a second object.
first = process_options(root, overrides=output_directory_overrides(root / "one"))
captured = shared.opts          # what a module binding at import time would hold
was = str(first.outdir_videos)
second = process_options(root, overrides=output_directory_overrides(root / "two"))
now = str(second.outdir_videos)

print("OPTIONS " + json.dumps({
    "none_before_install": before_any,
    "same_object_across_loads": first is second,
    "still_installed_on_shared": shared.opts is second,
    "import_time_capture_still_current": captured is second,
    "reported_by_reader": installed_process_options() is second,
    # The override moved AND the object did not: read through the FIRST
    # reference, so this fails if a second object were quietly substituted.
    "override_applied_to_same_object": (
        was.endswith("one") and now.endswith("two")
        and str(first.outdir_videos) == now
    ),
    "reader_is_pure": installed_process_options() is installed_process_options(),
}))
"""


def _probe() -> dict:
    finished = subprocess.run(
        [sys.executable, "-c", f"APP = {str(APP_ROOT)!r}\n" + _PROBE],
        cwd=str(APP_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
    )
    out = finished.stdout.decode("utf-8", "replace")
    marker = out.find("OPTIONS ")
    if marker < 0:
        raise AssertionError(
            "options probe produced no result:\n"
            + out[-1500:]
            + finished.stderr.decode("utf-8", "replace")[-1500:]
        )
    return json.loads(out[marker + len("OPTIONS "):].splitlines()[0])


class ProcessLifetimeTests(unittest.TestCase):
    _result: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if ProcessLifetimeTests._result is None:
            ProcessLifetimeTests._result = _probe()
        cls.result = ProcessLifetimeTests._result

    def test_nothing_is_installed_until_asked(self) -> None:
        """Importing the module must not install options as a side effect."""

        self.assertTrue(self.result["none_before_install"])

    def test_two_loads_share_one_object(self) -> None:
        """The property the whole change exists for."""

        self.assertTrue(self.result["same_object_across_loads"])

    def test_the_object_stays_installed_on_shared(self) -> None:
        self.assertTrue(self.result["still_installed_on_shared"])

    def test_a_module_that_captured_opts_still_holds_the_live_object(
        self,
    ) -> None:
        """The forty modules, modelled.

        A module doing `from modules.shared import opts` at import time holds
        whatever was installed then. This asserts that what it holds is still
        the object the second load configured -- which is precisely what the
        restoring bridge broke.
        """

        self.assertTrue(self.result["import_time_capture_still_current"])

    def test_a_second_load_changes_values_without_changing_the_object(
        self,
    ) -> None:
        self.assertTrue(self.result["override_applied_to_same_object"])
        self.assertTrue(self.result["same_object_across_loads"])

    def test_the_reader_reports_the_installed_object(self) -> None:
        self.assertTrue(self.result["reported_by_reader"])

    def test_the_reader_installs_nothing(self) -> None:
        self.assertTrue(self.result["reader_is_pure"])


class LoaderContractTests(unittest.TestCase):
    """That the loader actually USES the process-lifetime installer.

    The first version of this class asserted only that the old attribute name
    `_options_cm` was absent. A mutation guard then reverted the loader to a
    per-load context manager under a DIFFERENT name and the whole suite still
    passed -- because every other test here drives `process_options()`
    directly and never goes through the loader at all. Written, exported,
    never called, one more time.

    So these read the function bodies.
    """

    def _function(self, name: str):
        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_headless" / "live_bindings.py").read_text(
                encoding="utf-8"
            )
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(f"live_bindings has no {name}()")

    def test_the_loader_installs_the_process_options(self) -> None:
        import ast

        node = self._function("_install_options")
        called = {
            getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        }
        self.assertIn(
            "process_options",
            called,
            "_install_options must go through the process-lifetime installer",
        )

    def test_the_loader_opens_no_options_context_manager(self) -> None:
        """Catches the mutation the previous version missed: any `__enter__`
        inside `_install_options`, whatever the attribute is called."""

        import ast

        node = self._function("_install_options")
        entered = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and getattr(call.func, "attr", None) == "__enter__"
        ]
        self.assertEqual([], entered, "options must not be scoped to a load")

    def test_close_tears_no_options_down(self) -> None:
        import ast

        node = self._function("close")
        exited = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and getattr(call.func, "attr", None) == "__exit__"
        ]
        self.assertEqual([], exited, "close() must not restore the options")


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_OPTIONS_LIFETIME_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
