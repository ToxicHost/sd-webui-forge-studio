"""The LoRA browser lists the owner's LoRAs. AR8.1.

THE DEFECT THIS CLOSES

On a live server with four checkpoints catalogued, `/studio/loras` returned
**0 entries** -- by construction:

    source_api_adapter.py:492   "/studio/loras" sat in a set of routes that
                                returned [] UNCONDITIONALLY
    catalogue.py:134            MODEL_ROLES had no `lora`, so no root could be
                                configured or validated

So "+ LoRAs" opened a browser that listed nothing, and the stack could only be
filled by typing `<lora:name:1>` into the prompt by hand -- which already
worked, because the prompt travels verbatim.

Owner decision, 2026-08-21: build the catalogue.

WHY THE ENGINE'S REGISTRY AND NOT A SCAN

`<lora:NAME:weight>` is resolved against `networks.available_networks`. A name
Studio invents from a filename is a name the engine may not match -- the
registry keys on its own naming and carries an alias. `/studio/upscalers`
already reads the engine for exactly this reason.

WHAT THESE TESTS REFUSE TO ACCEPT

**A path in the payload.** The engine's own API shape includes
`path: obj.filename`. `_role_payload`'s rule is "never an absolute path, never
the root, and never `relative_location`". Asserted against the whole serialized
reply rather than field by field, because that is the privacy boundary.

**A projection that lands after the scan.** The engine scans at startup, before
Studio has projected anything, so a refresh that runs first would answer about
the DEFAULT directory and say nothing about it -- the silent-no-op shape
`runtime_options.py` exists to prevent. `TheOrderingIsTheWholePointTests`
records the call order and asserts it.

Review: `Evidence/source-review/AR8.1-lora-catalogue.md`.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_LORA_TESTS = 17

ADAPTER_PY = (APP_ROOT / "forge_studio" / "source_api_adapter.py").read_text(
    encoding="utf-8")


class _Network:
    """Shaped like `network.NetworkOnDisk`, including the field that must not
    escape."""

    def __init__(self, name, alias=None, filename=None):
        self.name = name
        self.alias = alias or name
        self.filename = filename or rf"C:\Users\someone\Private\Loras\{name}.safetensors"
        self.metadata = {}


class _Registry:
    def __init__(self, roots=("C:/loras",)):
        self._roots = tuple(roots)

    def configured_roots(self):
        return {"lora": self._roots}


def install_engine(monkey, networks_module, cmd_opts=None):
    """Put a fake `networks` and `modules.shared` in place for one test."""

    shared = types.SimpleNamespace(cmd_opts=cmd_opts if cmd_opts is not None
                                   else types.SimpleNamespace(lora_dirs=[]))
    modules_pkg = sys.modules.get("modules") or types.ModuleType("modules")
    monkey(sys.modules, "modules", modules_pkg)
    monkey(sys.modules, "modules.shared", shared)
    monkey(modules_pkg, "shared", shared, attribute=True)
    monkey(sys.modules, "networks", networks_module)
    return shared


class _Patcher:
    """Minimal, explicit save/restore. `unittest.mock` would also do, and this
    keeps what is being replaced visible in the test that replaces it."""

    def __init__(self):
        self._undo = []

    def __call__(self, target, key, value, attribute=False):
        if attribute:
            had = hasattr(target, key)
            old = getattr(target, key, None)
            setattr(target, key, value)
            self._undo.append(
                lambda: setattr(target, key, old) if had else delattr(target, key))
        else:
            had = key in target
            old = target.get(key)
            target[key] = value
            self._undo.append(
                lambda: target.__setitem__(key, old) if had else target.pop(key, None))

    def restore(self):
        for undo in reversed(self._undo):
            undo()
        self._undo.clear()


def fake_networks(names, order=None):
    module = types.ModuleType("networks")
    module.available_networks = {}

    def list_available_networks():
        if order is not None:
            order.append("scan")
        module.available_networks = {n: _Network(n) for n in names}

    module.list_available_networks = list_available_networks
    return module


class TheRouteServesRealNamesTests(unittest.TestCase):
    def setUp(self):
        self.patch = _Patcher()
        self.addCleanup(self.patch.restore)

    def route(self):
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        adapter = object.__new__(SourceFrontendAdapter)
        adapter._model_roots = _Registry()
        return adapter.get("/studio/loras")

    def test_it_lists_the_engines_names(self) -> None:
        install_engine(self.patch, fake_networks(["detail_slider", "add_noise"]))
        names = [entry["name"] for entry in self.route()]
        self.assertEqual(["add_noise", "detail_slider"], names)

    def test_each_entry_carries_a_name_and_alias(self) -> None:
        install_engine(self.patch, fake_networks(["one"]))
        entry = self.route()[0]
        self.assertEqual({"name", "alias"}, set(entry))

    def test_names_are_sorted_for_the_browser(self) -> None:
        install_engine(self.patch, fake_networks(["Zeta", "alpha", "Mid"]))
        self.assertEqual(["alpha", "Mid", "Zeta"],
                         [e["name"] for e in self.route()])

    def test_duplicates_collapse(self) -> None:
        module = fake_networks([])
        module.available_networks = {"a": _Network("same"), "b": _Network("same")}
        module.list_available_networks = lambda: None
        install_engine(self.patch, module)
        self.assertEqual(1, len(self.route()))


class NoPathEverLeavesStudioTests(unittest.TestCase):
    """The privacy boundary, asserted against the whole reply."""

    def setUp(self):
        self.patch = _Patcher()
        self.addCleanup(self.patch.restore)

    def test_the_payload_contains_no_filesystem_path(self) -> None:
        import json

        from forge_studio.source_api_adapter import SourceFrontendAdapter

        install_engine(self.patch, fake_networks(["secret_lora"]))
        adapter = object.__new__(SourceFrontendAdapter)
        adapter._model_roots = _Registry()
        serialized = json.dumps(adapter.get("/studio/loras"))

        self.assertIn("secret_lora", serialized)
        for fragment in ("C:", "Private", "Loras", ".safetensors", "/", "\\\\"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, serialized)

    def test_the_entry_contract_has_no_path_field(self) -> None:
        from forge_headless.lora_catalogue import LoraEntry

        self.assertEqual({"name", "alias"},
                         set(LoraEntry(name="a", alias="a").to_dict()))


class TheOrderingIsTheWholePointTests(unittest.TestCase):
    """Projection BEFORE the scan, or the answer describes the wrong folder."""

    def setUp(self):
        self.patch = _Patcher()
        self.addCleanup(self.patch.restore)

    def test_the_roots_are_projected_before_the_scan_runs(self) -> None:
        order: list[str] = []

        class _Opts:
            def __init__(self):
                self._dirs = []

            @property
            def lora_dirs(self):
                return self._dirs

            @lora_dirs.setter
            def lora_dirs(self, value):
                order.append("project")
                self._dirs = value

        from forge_headless.lora_catalogue import available_loras

        install_engine(self.patch, fake_networks(["x"], order=order),
                       cmd_opts=_Opts())
        available_loras(_Registry())
        self.assertEqual(["project", "scan"], order,
                         "the scan ran before the root was projected")

    def test_the_configured_roots_are_what_gets_projected(self) -> None:
        from forge_headless.lora_catalogue import available_loras

        shared = install_engine(self.patch, fake_networks(["x"]))
        available_loras(_Registry(roots=("D:/mine", "E:/more")))
        self.assertEqual(["D:/mine", "E:/more"], shared.cmd_opts.lora_dirs)

    def test_lora_dir_singular_is_left_alone(self) -> None:
        """The engine's own default. Overwriting it would hide a folder the
        owner may also be using."""

        from forge_headless.lora_catalogue import available_loras

        shared = install_engine(self.patch, fake_networks(["x"]))
        shared.cmd_opts.lora_dir = "ENGINE_DEFAULT"
        available_loras(_Registry())
        self.assertEqual("ENGINE_DEFAULT", shared.cmd_opts.lora_dir)


class AnEmptyAnswerIsNotAnErrorTests(unittest.TestCase):
    """A page with no LoRAs must still load."""

    def adapter(self, registry):
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        adapter = object.__new__(SourceFrontendAdapter)
        adapter._model_roots = registry
        return adapter

    def test_no_registry_yields_an_empty_list(self) -> None:
        self.assertEqual([], self.adapter(None).get("/studio/loras"))

    def test_no_configured_root_yields_an_empty_list(self) -> None:
        self.assertEqual([], self.adapter(_Registry(roots=())).get("/studio/loras"))

    def test_a_registry_that_raises_yields_an_empty_list(self) -> None:
        class _Broken:
            def configured_roots(self):
                raise RuntimeError("no catalogue")

        self.assertEqual([], self.adapter(_Broken()).get("/studio/loras"))

    def test_an_engine_that_parses_argv_and_exits_yields_an_empty_list(self) -> None:
        """The named hazard, exercised for real.

        With no fake in place this reaches the actual `from modules import
        shared`, which pulls in `shared_cmd_options` -- and that calls
        `parser.parse_args()` AT MODULE SCOPE, on whatever argv the test runner
        happens to have. It prints a usage block and raises SystemExit, which
        is NOT an Exception.

        `live_generation_port` names this same trap in its own comment: a bare
        `except Exception` let it kill a job, which is how it was found. So the
        catch here is `(Exception, SystemExit)`, and this test is what proves
        it -- the usage text on stderr during this run is the hazard firing and
        being contained.
        """

        from forge_headless.lora_catalogue import available_loras

        self.assertEqual((), available_loras(_Registry()))


class TheRouteLeftTheDeadSetTests(unittest.TestCase):
    def test_loras_is_no_longer_unconditionally_empty(self) -> None:
        """It sat beside /studio/embeddings and friends, all returning []."""

        at = ADAPTER_PY.index('if route in {')
        block = ADAPTER_PY[at:ADAPTER_PY.index("}", at)]
        self.assertNotIn('"/studio/loras"', block)

    def test_its_neighbours_are_untouched(self) -> None:
        """Only the LoRA route was built. The others are still honestly
        empty, and pretending otherwise would be the defect this fixes."""

        self.assertIn('"/studio/embeddings"', ADAPTER_PY)
        self.assertIn('"/studio/workflows"', ADAPTER_PY)

    def test_the_role_is_configurable(self) -> None:
        from forge_headless.catalogue import MODEL_ROLES

        self.assertIn("lora", MODEL_ROLES)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_LORA_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
