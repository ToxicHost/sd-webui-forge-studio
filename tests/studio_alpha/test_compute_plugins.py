"""Tier-2 compute plugins — a general seam, proven with a bad plugin.

The owner's Forge install registers roughly thirty custom samplers because Forge
runs `load_scripts()` over every extension at startup. Studio does not run that
loader: it scans arbitrary directories, imports arbitrary modules, builds Gradio
UI and mutates global state.

This seam loads only what Studio owns, and the tests that matter are the ones
where a plugin misbehaves. Two samplers appearing in a menu proves very little;
what proves the seam is that a plugin which raises cannot take the registry with
it, and a plugin which imports Gradio is refused rather than reported as
working.

BFS and Beta 57 are not special-cased anywhere. They are the two files in the
directory.

The runtime assertions run in subprocesses: loading a plugin registers into
process-global backend state, and doing that inside the canonical run would leak
into every suite that followed.
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

EXPECTED_PLUGIN_TESTS = 15

PLUGIN_DIR = APP_ROOT / "studio_plugins"
BOOTSTRAP_SOURCE = APP_ROOT / "forge_headless" / "backend_bootstrap.py"

#: Loads the real plugins through the real bootstrap, then reports what the
#: registry actually holds.
_REAL = """
import sys, json
sys.path.insert(0, APP)
from pathlib import Path
from forge_headless.backend_bootstrap import bootstrap_backend

report = bootstrap_backend(Path(APP))
import modules.sd_samplers as S, modules.sd_schedulers as SC

samplers = [str(x.name) for x in S.all_samplers]
schedulers = [str(getattr(x, "label", getattr(x, "name", x))) for x in SC.schedulers]
print("REAL " + json.dumps({
    "state": report.state,
    "plugins_loaded": report.plugins_loaded,
    "plugins_failed": list(report.plugins_failed),
    "gradio": report.gradio_modules,
    "model_loaded": report.model_loaded,
    "samplers": samplers,
    "schedulers": schedulers,
    "dispatchable_bfs": [s for s in samplers if "BFS" in s],
    "dispatchable_beta57": [s for s in schedulers if "57" in s],
}))
"""

#: The negative control. A temporary directory with THREE plugins: one that
#: raises, one that imports Gradio, and one good one -- so the test proves both
#: refusals AND that a healthy neighbour still loads.
_NEGATIVE = '''
import sys, json, pathlib
sys.path.insert(0, APP)
from pathlib import Path
from forge_headless.backend_bootstrap import bootstrap_backend

# Stand the real backend up first: plugins extend a registry that must exist.
bootstrap_backend(Path(APP))
import modules.sd_samplers as S

before = len(S.all_samplers)

bad = pathlib.Path(TMP)
bad.mkdir(parents=True, exist_ok=True)
(bad / "raises.py").write_text("raise RuntimeError('deliberate plugin failure')\\n", encoding="utf-8")
(bad / "imports_gradio.py").write_text("import gradio\\n", encoding="utf-8")
(bad / "healthy.py").write_text(
    "from modules import sd_samplers, sd_samplers_common\\n"
    "import modules.sd_samplers_kdiffusion as K\\n"
    "d = sd_samplers_common.SamplerData('TestOnlySampler',\\n"
    "    lambda model: K.KDiffusionSampler('sample_euler', model), [], {})\\n"
    "sd_samplers.all_samplers.append(d)\\n"
    "sd_samplers.all_samplers_map = {x.name: x for x in sd_samplers.all_samplers}\\n",
    encoding="utf-8")

from forge_headless.compute_plugins import load_compute_plugins

class _Root:
    pass

results = load_compute_plugins(bad.parent)
after = len(S.all_samplers)
print("NEG " + json.dumps({
    "results": {r.name: {"state": r.state, "reason": r.reason[:80]} for r in results},
    "registry_before": before,
    "registry_after": after,
    "registry_still_usable": after >= before,
    "names_after": [str(x.name) for x in S.all_samplers][-3:],
}))
'''


def _probe(source: str, marker: str, **fmt) -> dict:
    body = "\n".join(f"{k} = {v!r}" for k, v in fmt.items()) + "\n" + source
    finished = subprocess.run(
        [sys.executable, "-c", body], cwd=str(APP_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    out = finished.stdout.decode("utf-8", "replace")
    at = out.find(marker + " ")
    if at < 0:
        raise AssertionError(
            f"plugin probe produced no {marker}:\n" + out[-2000:]
            + finished.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[at + len(marker) + 1:].splitlines()[0])


class DiscoveryTests(unittest.TestCase):
    def test_the_plugin_directory_is_studio_owned_and_flat(self) -> None:
        """Inside the install, not under the state root: plugins are code, and
        the state root holds the owner's data."""

        from forge_headless.compute_plugins import discover, plugin_directory

        self.assertEqual(PLUGIN_DIR, plugin_directory(APP_ROOT))
        found = discover(APP_ROOT)
        self.assertTrue(found, "no compute plugins discovered")
        for path in found:
            self.assertEqual(PLUGIN_DIR, path.parent, "discovery must not recurse")

    def test_an_underscored_file_is_skipped(self) -> None:
        """How a plugin is disabled without deleting it."""

        from forge_headless.compute_plugins import discover

        names = {path.name for path in discover(APP_ROOT)}
        self.assertFalse([n for n in names if n.startswith("_")])

    def test_a_missing_directory_is_not_an_error(self) -> None:
        from forge_headless.compute_plugins import discover

        self.assertEqual((), discover(APP_ROOT / "no-such-directory"))


class RealPluginTests(unittest.TestCase):
    _result: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if RealPluginTests._result is None:
            RealPluginTests._result = _probe(_REAL, "REAL", APP=str(APP_ROOT))
        cls.result = RealPluginTests._result

    def test_the_bootstrap_still_reaches_ready_with_plugins(self) -> None:
        self.assertEqual("ready", self.result["state"])
        self.assertEqual([], self.result["plugins_failed"])

    def test_bfs_registers_through_the_general_seam(self) -> None:
        """Not hardcoded anywhere: it is a file in the plugin directory."""

        self.assertTrue(self.result["dispatchable_bfs"],
                        f"samplers: {self.result['samplers']}")
        self.assertIn("BFS (Bandwise Flow)", self.result["samplers"])

    def test_beta57_registers_through_the_general_seam(self) -> None:
        self.assertTrue(self.result["dispatchable_beta57"],
                        f"schedulers: {self.result['schedulers']}")
        self.assertIn("Beta 57", self.result["schedulers"])

    def test_the_builtin_registry_is_not_replaced_by_the_plugins(self) -> None:
        """Plugins EXTEND. Euler must still be there afterwards."""

        self.assertIn("Euler", self.result["samplers"])
        self.assertIn("Karras", self.result["schedulers"])
        self.assertGreaterEqual(len(self.result["samplers"]), 22)

    def test_plugins_import_no_gradio(self) -> None:
        self.assertEqual(0, self.result["gradio"])

    def test_plugins_load_no_model(self) -> None:
        self.assertFalse(self.result["model_loaded"])


class NegativeControlTests(unittest.TestCase):
    """A seam that cannot survive a bad plugin is not a seam."""

    _result: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if NegativeControlTests._result is None:
            import tempfile

            temporary = Path(tempfile.mkdtemp()) / "studio_plugins"
            NegativeControlTests._result = _probe(
                _NEGATIVE, "NEG", APP=str(APP_ROOT), TMP=str(temporary))
        cls.result = NegativeControlTests._result

    def test_a_plugin_that_raises_is_reported_failed(self) -> None:
        self.assertEqual("failed", self.result["results"]["raises"]["state"])
        self.assertIn("deliberate", self.result["results"]["raises"]["reason"])

    def test_a_plugin_that_imports_gradio_is_refused(self) -> None:
        """Refused, not merely failed. It loaded fine and broke the product's
        hardest invariant, so reporting it as working would hide that."""

        self.assertEqual("refused_gradio",
                         self.result["results"]["imports_gradio"]["state"])

    def test_a_healthy_plugin_still_loads_beside_broken_ones(self) -> None:
        """The isolation claim. Without this, 'nothing loaded' would pass the
        two assertions above."""

        self.assertEqual("loaded", self.result["results"]["healthy"]["state"])
        self.assertIn("TestOnlySampler", self.result["names_after"])

    def test_the_core_registry_survives_a_bad_plugin(self) -> None:
        self.assertTrue(self.result["registry_still_usable"],
                        f"{self.result['registry_before']} -> "
                        f"{self.result['registry_after']}")


class WiringTests(unittest.TestCase):
    def test_the_bootstrap_loads_compute_plugins(self) -> None:
        """Through the syntax tree, so a comment cannot satisfy it."""

        tree = ast.parse(BOOTSTRAP_SOURCE.read_text(encoding="utf-8"))
        called = {
            getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.assertIn("load_compute_plugins", called)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_PLUGIN_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
