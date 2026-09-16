"""R1.5 Phase D — Studio initialises its own backend, on purpose, at startup.

Before this, the sampler, scheduler and upscaler registries were empty until the
first generation imported the engine as a SIDE EFFECT. An owner who opened
Studio saw "Engine default" in every menu and had to generate once to get real
choices. Measured on a cold server:

```text
COLD  {"available": false, "samplers": 0, "schedulers": 0,
       "latent": 0, "image": 0, "scan_complete": false}
```

`bootstrap_backend()` replaces that accident with an intention. What it must
never do is as important as what it does, so each invariant is asserted rather
than described: no model load, no real Gradio, no second options object, and a
failure that stays retryable instead of latching as success.

The runtime assertions run in a subprocess. Bootstrapping mutates `sys.path`,
`sys.argv` and `sys.modules` for the life of a process, and doing that inside
the canonical run would leak into every suite that came after.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_BOOTSTRAP_TESTS = 12

LAUNCH_SOURCE = APP_ROOT / "forge_studio" / "launch.py"

#: One subprocess answers every runtime question: standing the backend up costs
#: several thousand imports, and paying that per assertion would add minutes to
#: the canonical run for no extra information.
_PROBE = """
import sys, json
sys.path.insert(0, APP)
from pathlib import Path
from forge_headless.backend_bootstrap import (
    FAILED_RETRYABLE, NOT_STARTED, READY, bootstrap_backend, bootstrap_state,
    completed_report,
)

before_state = bootstrap_state()

# A failure must NOT latch. A root with no engine in it fails the import, and
# the next call must still be free to succeed.
bad = bootstrap_backend(Path(APP) / "does-not-exist-anywhere")
after_bad = bootstrap_state()

first = bootstrap_backend(Path(APP))
second = bootstrap_backend(Path(APP))

import modules.shared as shared
from forge_headless.headless_options import installed_process_options

model = getattr(shared, "sd_model", None)
print("PROBE " + json.dumps({
    "state_before": before_state,
    "bad_state": bad.state,
    "bad_reason_present": bool(bad.reason),
    "state_after_failure": after_bad,
    "first": first.to_dict(),
    "second": second.to_dict(),
    "idempotent_same_report": first is second,
    "model_class": type(model).__name__,
    "options_is_process_options": shared.opts is installed_process_options(),
    "gradio": sorted(n for n in sys.modules
                     if n == "gradio" or n.startswith("gradio.")),
}))
"""


def _probe() -> dict:
    finished = subprocess.run(
        [sys.executable, "-c", f"APP = {str(APP_ROOT)!r}\n" + _PROBE],
        cwd=str(APP_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=1800,
    )
    out = finished.stdout.decode("utf-8", "replace")
    marker = out.find("PROBE ")
    if marker < 0:
        raise AssertionError(
            "bootstrap probe produced no result:\n" + out[-2000:]
            + finished.stderr.decode("utf-8", "replace")[-2000:]
        )
    return json.loads(out[marker + len("PROBE "):].splitlines()[0])


class BootstrapRuntimeTests(unittest.TestCase):
    _result: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if BootstrapRuntimeTests._result is None:
            BootstrapRuntimeTests._result = _probe()
        cls.result = BootstrapRuntimeTests._result

    def test_nothing_is_bootstrapped_merely_by_importing(self) -> None:
        self.assertEqual("not_started", self.result["state_before"])

    def test_the_bootstrap_reaches_ready(self) -> None:
        self.assertEqual("ready", self.result["first"]["state"])

    def test_the_sampler_registry_is_real(self) -> None:
        """A lower bound, not an equality. The current test host reports 21;
        a compute plugin registering more must not fail this."""

        self.assertGreaterEqual(self.result["first"]["samplers"], 20)

    def test_the_scheduler_registry_is_real(self) -> None:
        self.assertGreaterEqual(self.result["first"]["schedulers"], 15)

    def test_the_upscaler_registries_are_real(self) -> None:
        self.assertGreaterEqual(self.result["first"]["latent_upscalers"], 1)
        self.assertGreaterEqual(self.result["first"]["image_upscalers"], 1)

    def test_no_model_is_loaded_to_populate_the_menus(self) -> None:
        """The hard invariant. `FakeInitialModel` is Forge's import-time
        sentinel; a real engine would be any other class."""

        self.assertIn(self.result["model_class"],
                      {"FakeInitialModel", "NoneType"})
        self.assertFalse(self.result["first"]["model_loaded"])

    def test_no_real_gradio_is_imported(self) -> None:
        self.assertEqual([], self.result["gradio"])
        self.assertEqual(0, self.result["first"]["gradio_modules"])

    def test_the_bootstrap_uses_the_canonical_process_options(self) -> None:
        """Phase B must not be regressed by Phase D: one options object, not a
        temporary scope opened for registry setup and torn down after."""

        self.assertTrue(self.result["options_is_process_options"])

    def test_a_second_bootstrap_changes_nothing(self) -> None:
        """Idempotence. The second call returns the SAME report rather than
        re-scanning the upscaler directory on every invocation."""

        self.assertTrue(self.result["idempotent_same_report"])
        self.assertEqual(self.result["first"], self.result["second"])

    def test_a_failed_bootstrap_is_not_latched_as_success(self) -> None:
        """The upscaler-latch lesson. A failure must stay retryable, must carry
        a reason, and must not leave the state claiming READY -- otherwise the
        run that follows it inherits a lie."""

        self.assertEqual("failed_retryable", self.result["bad_state"])
        self.assertTrue(self.result["bad_reason_present"])
        self.assertEqual("not_started", self.result["state_after_failure"])


class WiringTests(unittest.TestCase):
    def test_the_launcher_bootstraps_the_backend_at_startup(self) -> None:
        """Read through the syntax tree: a comment naming the function would
        satisfy a text search while nothing called it, which is this
        codebase's signature failure."""

        tree = ast.parse(LAUNCH_SOURCE.read_text(encoding="utf-8"))
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        self.assertIn("bootstrap_backend", called)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_BOOTSTRAP_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
