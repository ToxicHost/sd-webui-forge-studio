"""R1.5 — the Studio backend reaches its own registries without Gradio.

Owner requirement (Backend Ownership Reset handoff, 2026-08-11 §14): no real
Gradio may be imported into the Studio product runtime. §25 of that handoff is
explicit that a source-level check is not acceptable evidence, because the
defect was never a literal `import gradio` in Studio code -- it was a
transitive edge four modules deep:

```text
modules.sd_samplers
  -> modules.sd_samplers_common      :30  from modules_forge import main_entry
    -> modules_forge.main_entry      :4   import gradio as gr
      -> gradio                           119 modules
```

Measured on 2026-08-11: importing `modules.sd_samplers` loaded **119** Gradio
modules. Of the 112 direct Gradio import sites in that graph, 111 were Gradio
importing its own submodules; exactly ONE was outside the package, and it was
`main_entry.py:4`. The whole load entered through a single statement.

`sd_samplers_common` needs `main_entry` for four calls -- one logger line, two
`checkpoint_change`, one `refresh_model_loading_parameters` -- and all four sit
inside `apply_refiner`. Deferring the import into that function removes the edge
at no behavioural cost. Studio never sets `refiner_switch_at`, so the function
returns at its first line and the import is unreachable in this product; a host
that does use a refiner imports it on first use instead of at module scope.

The runtime assertions here run in subprocesses, because `sys.modules` for the
process under test is the only thing that can answer the question, and this
suite's own interpreter has already imported whatever it imported.
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

EXPECTED_GRADIO_BOUNDARY_TESTS = 12

SAMPLERS_COMMON = APP_ROOT / "modules" / "sd_samplers_common.py"
MAIN_ENTRY = APP_ROOT / "modules_forge" / "main_entry.py"
VENDORED = APP_ROOT / "modules_forge" / "packages"

#: One subprocess answers every runtime question, because standing the backend
#: up costs ~4000 module imports and doing it once per assertion would add
#: minutes to the canonical run for no extra information.
_PROBE = """
import sys, os, json
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, "modules_forge", "packages"))
sys.argv = ["studio"]
from pathlib import Path
import modules.shared as shared
from forge_headless.headless_options import HeadlessOptions, legacy_defaults

# ONE options object, installed and never removed. Forty modules bind
# `from modules.shared import opts` at import time, so an install-then-restore
# bridge strands every one of them; this is the process-lifetime shape.
shared.opts = HeadlessOptions(legacy_defaults(Path(APP)))

error = ""
samplers, schedulers = [], []
gradio_after_registry = None
try:
    import modules.sd_samplers as S
    import modules.sd_schedulers as SC
    S.set_samplers()
    samplers = [str(x.name) for x in S.all_samplers]
    schedulers = [str(getattr(x, "label", getattr(x, "name", x))) for x in SC.schedulers]
    gradio_after_registry = sorted(
        n for n in sys.modules if n == "gradio" or n.startswith("gradio.")
    )
    # The GENERATION path, imported separately and measured after the registry
    # so the two are distinguishable. `modules.processing` reaches Gradio
    # through a different chain than the sampler registry does, and cutting one
    # says nothing about the other -- which is how the first version of this
    # work concluded too early.
    import modules.processing  # noqa: F401
except BaseException as exc:
    error = f"{type(exc).__name__}: {exc}"

print("PROBE_RESULT " + json.dumps({
    "error": error,
    "gradio": sorted(n for n in sys.modules if n == "gradio" or n.startswith("gradio.")),
    "samplers": samplers,
    "schedulers": schedulers,
    # NOT `sd_model is not None`. Forge seeds `shared.sd_model` with a
    # `FakeInitialModel` sentinel when `modules.shared` is imported, so a
    # not-None check reports "a model is loaded" on a process that has loaded
    # nothing. The class name distinguishes the sentinel from a real engine.
    "model_class": type(getattr(shared, "sd_model", None)).__name__,
    "gradio_after_registry": gradio_after_registry,
}))
"""


def _probe() -> dict:
    """Stand the backend up in a fresh interpreter and report what it imported."""

    source = f"APP = {str(APP_ROOT)!r}\n" + _PROBE
    finished = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(APP_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
    )
    out = finished.stdout.decode("utf-8", "replace")
    marker = out.find("PROBE_RESULT ")
    if marker < 0:
        raise AssertionError(
            "backend probe produced no result:\n"
            + out[-2000:]
            + finished.stderr.decode("utf-8", "replace")[-2000:]
        )
    return json.loads(out[marker + len("PROBE_RESULT "):].splitlines()[0])


class _ProbeOnce(unittest.TestCase):
    """Shares one subprocess across the runtime assertions in this module."""

    _result: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        if _ProbeOnce._result is None:
            _ProbeOnce._result = _probe()
        cls.result = _ProbeOnce._result


class RuntimeBoundaryTests(_ProbeOnce):
    def test_standing_the_backend_up_imports_no_gradio(self) -> None:
        """The invariant. Not "no `import gradio` appears in Studio source" --
        that was always true and the process still loaded 119 of them."""

        self.assertEqual([], self.result["gradio"])

    def test_importing_the_generation_path_imports_no_gradio(self) -> None:
        """The invariant that actually matters to an owner.

        Before this work, every Studio generation loaded 119 Gradio modules --
        `modules.processing` reaches Gradio through `modules.scripts` and
        `modules.profiling`, which is a different chain from the sampler
        registry's. A clean registry proves nothing about it.

        Five module-scope imports carried the whole thing, and a static walk
        over the 121 modules reachable from `modules.processing` found no
        sixth.
        """

        self.assertEqual([], self.result["gradio"])

    def test_the_registry_is_clean_before_the_generation_path_is_imported(
        self,
    ) -> None:
        """Discriminating: recorded at the point between the two imports, so a
        regression can be attributed to the registry chain or the generation
        chain rather than to 'something'."""

        self.assertEqual([], self.result["gradio_after_registry"])

    def test_the_backend_stands_up_without_error(self) -> None:
        """A boundary that holds only because the import crashed early would
        satisfy the assertion above while proving nothing."""

        self.assertEqual("", self.result["error"])

    def test_the_real_sampler_registry_is_populated(self) -> None:
        """§26: real registries before the first generation. The count is a
        lower bound rather than an equality -- a custom sampler plugin
        registering additional entries is the intended future, and must not
        fail this."""

        self.assertGreaterEqual(len(self.result["samplers"]), 20)
        self.assertIn("Euler", self.result["samplers"])
        self.assertIn("Euler a", self.result["samplers"])

    def test_the_real_scheduler_registry_is_populated(self) -> None:
        self.assertGreaterEqual(len(self.result["schedulers"]), 15)
        self.assertIn("Karras", self.result["schedulers"])

    def test_no_model_is_loaded_to_populate_the_registries(self) -> None:
        """§35: backend bootstrap and model load stay separate. Menus must not
        cost the owner a checkpoint load.

        The first version of this test asserted `sd_model is not None` and
        failed against a correct implementation: Forge assigns a
        `FakeInitialModel` sentinel at import, so the attribute is populated on
        a process holding no weights at all. Measured alongside it,
        `torch.cuda.memory_allocated()` was 0. The class name is what separates
        the sentinel from a real engine.
        """

        self.assertIn(
            self.result["model_class"], {"FakeInitialModel", "NoneType"}
        )


_REPORTS_PROBE = """
import sys, os, json
sys.path.insert(0, APP)
from forge_studio.source_api_adapter import SourceFrontendAdapter
from forge_studio.presentation import StudioPresentation
import forge_studio as fs

pres = StudioPresentation(fs.StudioApplication(fs.MockBackend()), fs.GenerationRequest)
adapter = SourceFrontendAdapter(pres)
before = adapter.get("/studio/runtime_status")["gradio_imported"]
import gradio  # the real package, deliberately
after = adapter.get("/studio/runtime_status")["gradio_imported"]
pres.shutdown()
print("REPORTS " + json.dumps({"before": before, "after": after}))
"""


class ReportedHonestlyTests(unittest.TestCase):
    """`/studio/runtime_status` must OBSERVE Gradio, not assert a constant.

    This field read `"UNKNOWN"` until the owner opened it on a live Studio and
    got no answer. Replacing one constant with another -- `False` -- would look
    identical from the outside while proving nothing, and would be a worse lie
    than UNKNOWN, because it would read as evidence.

    So the test imports the real Gradio package mid-process and requires the
    same route on the same adapter to change its answer. A hardcoded value
    cannot pass, in either direction.
    """

    def test_the_route_changes_its_answer_when_gradio_is_imported(self) -> None:
        source = f"APP = {str(APP_ROOT)!r}\n" + _REPORTS_PROBE
        finished = subprocess.run(
            [sys.executable, "-c", source],
            cwd=str(APP_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=900,
        )
        out = finished.stdout.decode("utf-8", "replace")
        marker = out.find("REPORTS ")
        self.assertGreaterEqual(
            marker,
            0,
            "probe produced no result:\n"
            + out[-1500:]
            + finished.stderr.decode("utf-8", "replace")[-1500:],
        )
        result = json.loads(out[marker + len("REPORTS "):].splitlines()[0])
        self.assertIs(False, result["before"])
        self.assertIs(True, result["after"])


class DeferredImportTests(unittest.TestCase):
    """The shape of the edit, asserted through the syntax tree.

    Text searching would match the explanatory comment that names the very
    import it forbids -- a trap this codebase has recorded eight times.
    """

    def _module_level_imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:  # module level ONLY, not ast.walk
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
        return names

    def test_the_compute_module_does_not_import_the_ui_module_at_module_scope(
        self,
    ) -> None:
        names = self._module_level_imports(SAMPLERS_COMMON)
        self.assertNotIn("modules_forge.main_entry", names)
        self.assertNotIn("modules_forge", {n.split(".")[0] for n in names} & {"modules_forge"})

    def test_the_import_still_exists_inside_apply_refiner(self) -> None:
        """The discriminating half. Deleting the import outright would satisfy
        the test above and break the refiner path for any host that uses it."""

        tree = ast.parse(SAMPLERS_COMMON.read_text(encoding="utf-8"))
        target = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "apply_refiner"
        )
        deferred = [
            node
            for node in ast.walk(target)
            if isinstance(node, ast.ImportFrom) and node.module == "modules_forge"
        ]
        self.assertEqual(1, len(deferred))

    def test_the_ui_module_is_still_the_one_that_imports_gradio(self) -> None:
        """Records WHY the deferral is load-bearing. If upstream ever removes
        this import, the deferral becomes unnecessary and this test says so by
        failing -- which is the moment to reconsider it, not silently keep it.
        """

        names = self._module_level_imports(MAIN_ENTRY)
        self.assertTrue(
            any(n == "gradio" or n.startswith("gradio") for n in names),
            "modules_forge/main_entry.py no longer imports gradio; the "
            "deferred import in sd_samplers_common may no longer be needed",
        )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_GRADIO_BOUNDARY_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
