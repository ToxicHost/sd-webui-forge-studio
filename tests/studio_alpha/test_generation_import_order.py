"""The `modules.sd_models` <-> `modules.processing` import cycle, both ways.

```text
SCOPE: STATIC_IMPORT_SCOPE, MINIMAL_RUNTIME_SCOPE
```

`modules/sd_models.py` imported `processing` at module scope while
`modules/processing.py:32` imports `apply_token_merging` and
`forge_model_reload` back from it. The cycle resolved only when `processing`
was imported first; the other order raised

```text
ImportError: cannot import name 'apply_token_merging' from partially
initialized module 'modules.sd_models'
```

That is what stopped the first controlled image. `shared.sd_model = engine`
imports `sd_models` through the `Shared` property setter
(`modules/shared_items.py:175`), so simply publishing a loaded engine imposed
the fatal order.

**Every import-order case runs in a fresh interpreter.** A case that passed only
because Python retained a prior import in `sys.modules` would prove nothing, so
`sys.modules` manipulation is not used for these — a real subprocess is.

Nothing here loads a model, initialises a device, or constructs a UI. Torch is
imported by the retained modules themselves, exactly as the repository's other
non-live tests already cause.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SD_MODELS = APP_ROOT / "modules" / "sd_models.py"
PROCESSING = APP_ROOT / "modules" / "processing.py"
MAIN_ENTRY = APP_ROOT / "modules_forge" / "main_entry.py"
INFOTEXT_UTILS = APP_ROOT / "modules" / "infotext_utils.py"

#: The three files whose module-scope `processing` import was deferred, each
#: paired with the single attribute its sole remaining use reads or writes.
DEFERRED_EDGES = (
    (SD_MODELS, "opt_f"),
    (MAIN_ENTRY, "need_global_unload"),
    (INFOTEXT_UTILS, "old_hires_fix_first_pass_dimensions"),
)

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_IMPORT_CYCLE_TESTS = 18

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")


#: Prelude every isolated case runs: repository on the path, Forge's vendored
#: packages available, and argv neutralised because `modules.shared_cmd_options`
#: parses it at import and would otherwise exit on the runner's arguments.
PRELUDE = """
import os, sys
sys.argv = ["import-cycle-case"]
sys.path.insert(0, {app!r})
sys.path.insert(0, os.path.join({app!r}, "modules_forge", "packages"))
import gradio_guard  # noqa: F401  -- refuses UI construction and server launch

# `modules/sd_schedulers.py:287` reads `shared.opts.hide_schedulers` at module
# scope, so importing `modules.processing` at all requires options to exist.
# Installing them keeps these cases measuring the import cycle and nothing else.
from pathlib import Path as _Path
from forge_headless.headless_options import headless_options as _headless_options
_options_ctx = _headless_options(_Path({app!r}))
_options_ctx.__enter__()
"""

EPILOGUE = """
_options_ctx.__exit__(None, None, None)
"""

#: Installed before any Forge import in every case. It does not block the Gradio
#: *library* -- `modules.processing` legitimately imports it -- but it fails
#: closed on UI construction or a server launch, which is the property these
#: tests actually care about.
GRADIO_GUARD = '''
import sys


def _refuse(name):
    def guard(*args, **kwargs):
        raise AssertionError("FORBIDDEN_GRADIO_CALL:" + name)

    return guard


class _Finder:
    def find_module(self, fullname, path=None):
        return None


def _install():
    try:
        import gradio
    except Exception:
        return
    blocks = getattr(gradio, "Blocks", None)
    if blocks is not None:
        blocks.__init__ = _refuse("Blocks.__init__")
        if hasattr(blocks, "launch"):
            blocks.launch = _refuse("Blocks.launch")
        if hasattr(blocks, "queue"):
            blocks.queue = _refuse("Blocks.queue")


_install()
'''


#: The workspace venv is where Forge's dependencies live. `sys.executable` is
#: the wrong interpreter under standard discovery, and the canonical runner's
#: `-S` would hide site-packages, so the venv is named explicitly and the
#: subprocess is launched without `-S`.
VENV_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"


def case_interpreter() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable


def run_case(body: str) -> subprocess.CompletedProcess:
    """Run one import-order case in a fresh interpreter."""
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        guard = Path(work) / "gradio_guard.py"
        guard.write_text(GRADIO_GUARD, encoding="utf-8")
        script = Path(work) / "case.py"
        script.write_text(
            PRELUDE.format(app=str(APP_ROOT)) + body + EPILOGUE, encoding="utf-8"
        )
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [case_interpreter(), "-B", str(script)],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env={**dict(__import__("os").environ), "PYTHONPATH": work},
        )


def assert_case_ok(case: unittest.TestCase, result, label: str) -> None:
    case.assertEqual(
        result.returncode,
        0,
        f"{label} failed\nstdout:\n{result.stdout[-2000:]}\n"
        f"stderr:\n{result.stderr[-3000:]}",
    )
    case.assertNotIn("partially initialized", result.stderr)
    case.assertNotIn("FORBIDDEN_GRADIO_CALL", result.stderr)
    case.assertIn("CASE_OK", result.stdout)


# ---------------------------------------------------- 6.1 / 6.2 import order


class ImportOrderTests(unittest.TestCase):
    """Both orders must work in a fresh interpreter."""

    def test_sd_models_first_then_processing(self) -> None:
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing\n"
            "assert hasattr(modules.sd_models, 'apply_token_merging')\n"
            "assert hasattr(modules.processing, 'process_images_inner')\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "sd_models first")

    def test_processing_first_then_sd_models(self) -> None:
        result = run_case(
            "import modules.processing\n"
            "import modules.sd_models\n"
            "assert hasattr(modules.sd_models, 'apply_token_merging')\n"
            "assert hasattr(modules.processing, 'process_images_inner')\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "processing first")

    def test_from_import_of_the_previously_failing_names(self) -> None:
        # The exact statement that raised: processing.py:32, reached with
        # sd_models already mid-import.
        result = run_case(
            "import modules.sd_models\n"
            "from modules.sd_models import apply_token_merging, forge_model_reload\n"
            "assert callable(apply_token_merging)\n"
            "assert callable(forge_model_reload)\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "from-import after sd_models")


# ------------------------------------------- 6.3 / 6.4 shared.sd_model setter


class SharedModelSetterTests(unittest.TestCase):
    """The assignment that imposed the fatal order, in both directions."""

    SENTINEL = (
        "class _Sentinel:\n"
        "    'Inert stand-in: no Torch, no device, no model.'\n"
        "    forge_objects = None\n"
        "sentinel = _Sentinel()\n"
    )

    RESTORE = (
        "import modules.sd_models as _sdm\n"
        "_sdm.model_data.set_sd_model(_previous)\n"
        "assert _sdm.model_data.get_sd_model() is _previous\n"
    )

    def test_setter_after_sd_models_first(self) -> None:
        result = run_case(
            "import modules.sd_models\n"
            "from modules import shared\n"
            "_previous = shared.sd_model\n"
            + self.SENTINEL
            + "shared.sd_model = sentinel\n"
            "assert shared.sd_model is sentinel\n"
            "import modules.processing\n"
            "assert hasattr(modules.processing, 'process_images_inner')\n"
            + self.RESTORE
            + "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "setter after sd_models first")

    def test_setter_after_processing_first(self) -> None:
        result = run_case(
            "import modules.processing\n"
            "from modules import shared\n"
            "_previous = shared.sd_model\n"
            + self.SENTINEL
            + "shared.sd_model = sentinel\n"
            "assert shared.sd_model is sentinel\n"
            "import modules.sd_models\n"
            "assert hasattr(modules.sd_models, 'apply_token_merging')\n"
            + self.RESTORE
            + "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "setter after processing first")

    def test_setter_alone_reproduces_the_original_trigger(self) -> None:
        # `shared.sd_model = ...` alone imports sd_models via the Shared
        # property setter (modules/shared_items.py:175). Before the fix this
        # was the whole trigger.
        result = run_case(
            "from modules import shared\n"
            "assert 'modules.sd_models' not in __import__('sys').modules\n"
            + self.SENTINEL
            + "_previous = shared.sd_model\n"
            "shared.sd_model = sentinel\n"
            "assert 'modules.sd_models' in __import__('sys').modules\n"
            "import modules.processing\n"
            + self.RESTORE
            + "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "setter alone")


# ------------------------------------------------- 6.5 opt_f behavior preserved


class OptFPreservationTests(unittest.TestCase):
    """The local import must still reach `processing` at the same point."""

    def test_opt_f_assignment_still_reaches_processing(self) -> None:
        # Exercises the exact statement from sd_models.py:391 with a double in
        # place of a loaded model. No model, no device, no Torch tensors.
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing\n"
            "class _Vae:\n"
            "    upscale_ratio = 16\n"
            "class _Objects:\n"
            "    vae = _Vae()\n"
            "class _Model:\n"
            "    forge_objects = _Objects()\n"
            "sd_model = _Model()\n"
            "_before = modules.processing.opt_f\n"
            "from modules import processing\n"
            "processing.opt_f = sd_model.forge_objects.vae.upscale_ratio "
            "if isinstance(sd_model.forge_objects.vae.upscale_ratio, int) else 8\n"
            "assert modules.processing.opt_f == 16, modules.processing.opt_f\n"
            "assert processing is modules.processing\n"
            "modules.processing.opt_f = _before\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "opt_f assignment")

    def test_opt_f_falls_back_to_eight_for_a_non_integer_ratio(self) -> None:
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing\n"
            "class _Vae:\n"
            "    upscale_ratio = 8.0\n"
            "class _Objects:\n"
            "    vae = _Vae()\n"
            "class _Model:\n"
            "    forge_objects = _Objects()\n"
            "sd_model = _Model()\n"
            "_before = modules.processing.opt_f\n"
            "from modules import processing\n"
            "processing.opt_f = sd_model.forge_objects.vae.upscale_ratio "
            "if isinstance(sd_model.forge_objects.vae.upscale_ratio, int) else 8\n"
            "assert modules.processing.opt_f == 8, modules.processing.opt_f\n"
            "modules.processing.opt_f = _before\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "opt_f fallback")


# --------------------------------------- 7.3 main_entry behavior preserved


class NeedGlobalUnloadPreservationTests(unittest.TestCase):
    """`refresh_model_loading_parameters` must still set the flag on the real
    `modules.processing`, through the deferred import, at the same point."""

    def test_refresh_sets_need_global_unload_through_the_deferred_import(self) -> None:
        # Calls the real function. Only `select_checkpoint` is doubled, because
        # it is the one seam that would otherwise require a model file; every
        # statement after it -- including the deferred import and the
        # assignment -- is the real code path.
        result = run_case(
            "import modules.sd_models as sd_models\n"
            "import modules.processing\n"
            "from modules_forge import main_entry\n"
            "from modules import shared\n"
            "class _Info:\n"
            "    filename = 'no-such-file.safetensors'\n"
            "sd_models.select_checkpoint = lambda *a, **k: _Info()\n"
            "shared.opts.forge_additional_modules = []\n"
            "modules.processing.need_global_unload = False\n"
            "main_entry.refresh_model_loading_parameters()\n"
            "assert modules.processing.need_global_unload is True, "
            "modules.processing.need_global_unload\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "need_global_unload after refresh")

    def test_refresh_returns_early_without_touching_the_flag(self) -> None:
        # The guard clause precedes the assignment, so refresh=False must leave
        # the flag alone. Proves the assignment kept its control-flow position.
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing\n"
            "from modules_forge import main_entry\n"
            "modules.processing.need_global_unload = False\n"
            "main_entry.refresh_model_loading_parameters(refresh=False)\n"
            "assert modules.processing.need_global_unload is False, "
            "modules.processing.need_global_unload\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "need_global_unload early return")


# ------------------------------- 7.4 infotext_utils delegation preserved


class OldHiresFixDelegationTests(unittest.TestCase):
    """`restore_old_hires_fix_params` must still delegate to `processing`
    with the exact arguments and use the exact returned values."""

    def test_delegation_matches_processing_directly(self) -> None:
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing as processing\n"
            "from modules import infotext_utils\n"
            "res = {'First pass size-1': 0, 'First pass size-2': 0,\n"
            "       'Size-1': 1024, 'Size-2': 768}\n"
            "expected = processing.old_hires_fix_first_pass_dimensions(1024, 768)\n"
            "infotext_utils.restore_old_hires_fix_params(res)\n"
            "assert (res['Size-1'], res['Size-2']) == expected, (res, expected)\n"
            "assert res['Hires resize-1'] == 1024, res\n"
            "assert res['Hires resize-2'] == 768, res\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "old-hires delegation")

    def test_non_zero_first_pass_does_not_delegate(self) -> None:
        # The delegation sits behind a branch; a non-zero first pass must skip
        # it entirely, proving the deferred import did not change the guard.
        result = run_case(
            "import modules.sd_models\n"
            "import modules.processing as processing\n"
            "from modules import infotext_utils\n"
            "def _boom(*a, **k):\n"
            "    raise AssertionError('delegation should not have been reached')\n"
            "processing.old_hires_fix_first_pass_dimensions = _boom\n"
            "res = {'First pass size-1': 640, 'First pass size-2': 640,\n"
            "       'Size-1': 1024, 'Size-2': 768}\n"
            "infotext_utils.restore_old_hires_fix_params(res)\n"
            "assert res['Size-1'] == 640 and res['Size-2'] == 640, res\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "old-hires non-delegating branch")


# ------------------------------------------------ 6.6 / 7.5 structural assertions


class StructuralTests(unittest.TestCase):
    """Fail loudly if a future change reintroduces the module-scope import."""

    def _tree(self) -> ast.AST:
        return ast.parse(SD_MODELS.read_text(encoding="utf-8"))

    def test_sd_models_does_not_import_processing_at_module_scope(self) -> None:
        from forge_headless.import_graph import module_level_imports

        imports = module_level_imports(self._tree())
        self.assertNotIn("modules.processing", imports)
        self.assertNotIn("modules.processing.opt_f", imports)

    def test_the_local_import_exists_at_the_sole_use_site(self) -> None:
        tree = self._tree()
        local_imports: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "modules":
                if any(alias.name == "processing" for alias in node.names):
                    local_imports.append(node.lineno)
        self.assertEqual(
            len(local_imports), 1, f"expected one local import, found {local_imports}"
        )
        uses = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "opt_f"
            and getattr(node.value, "id", None) == "processing"
        ]
        self.assertEqual(len(uses), 1, f"expected one opt_f use, found {uses}")
        self.assertLess(
            local_imports[0], uses[0], "the local import must precede its use"
        )
        self.assertLess(
            uses[0] - local_imports[0], 6, "import and use should stay adjacent"
        )

    def test_no_second_processing_use_has_appeared(self) -> None:
        tree = self._tree()
        uses = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "processing"
        ]
        # One binding from the local import, one load at the assignment.
        self.assertLessEqual(
            len(uses),
            2,
            f"processing is referenced {len(uses)} times; the fix assumes one use site",
        )

    def test_processing_still_imports_the_names_it_needs(self) -> None:
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn(
            "from modules.sd_models import apply_token_merging, forge_model_reload",
            source,
            "modules/processing.py must remain unmodified by this fix",
        )

    def test_all_three_deferred_edges_keep_their_shape(self) -> None:
        # One module-scope import removed, one local import added, one use --
        # asserted for every edge, so a regression in any of the three fails
        # here rather than only surfacing as an ImportError at runtime.
        from forge_headless.import_graph import module_level_imports

        for path, attribute in DEFERRED_EDGES:
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))

                self.assertNotIn(
                    "modules.processing",
                    module_level_imports(tree),
                    f"{path.name} reintroduced a module-scope processing import",
                )

                local_imports = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                    and node.module == "modules"
                    and any(alias.name == "processing" for alias in node.names)
                ]
                self.assertEqual(
                    len(local_imports),
                    1,
                    f"{path.name}: expected one local import, found {local_imports}",
                )

                references = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and node.id == "processing"
                ]
                self.assertEqual(
                    len(references),
                    1,
                    f"{path.name}: processing is referenced {len(references)} times; "
                    "the deferral assumes exactly one use site",
                )

                uses = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and node.attr == attribute
                    and getattr(node.value, "id", None) == "processing"
                ]
                self.assertEqual(
                    len(uses), 1, f"{path.name}: expected one {attribute} use, found {uses}"
                )
                self.assertLess(
                    local_imports[0],
                    uses[0],
                    f"{path.name}: the local import must precede its use",
                )


# ------------------------------------------------------ 6.7 discovered count


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_IMPORT_CYCLE_TESTS,
            "Import-cycle test count changed: update EXPECTED_IMPORT_CYCLE_TESTS "
            "deliberately, or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
