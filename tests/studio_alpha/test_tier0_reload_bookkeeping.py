"""Tier-0 direct-load reload bookkeeping.

Live attempt 03 reached prompt setup and then died at
`modules/processing.py:945` -> `modules/sd_models.py:352`, because
`process_images_inner` calls `sd_models.forge_model_reload()` itself and the
direct-loaded session had no bookkeeping to satisfy Forge's early return.

These tests pin the real reload contract, prove the fast path is satisfied
truthfully rather than suppressed, prove a mismatch still enters native reload
logic, and inventory every reload/catalogue call reachable from the exact Tier-0
closure.

Runtime cases run in a fresh interpreter on the workspace venv, because the
canonical runner uses `-I -S` where Forge is not importable in process. No model
file is opened, no tensor allocated, no sampling performed.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SD_MODELS = APP_ROOT / "modules" / "sd_models.py"
PROCESSING = APP_ROOT / "modules" / "processing.py"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_RELOAD_BOOKKEEPING_TESTS = 28

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

#: Modules the minimal txt2img closure reaches. Widening it must be deliberate.
CLOSURE_MODULES = (
    "modules/processing.py",
    "modules/sd_unet.py",
    "modules/sd_vae.py",
    "modules/sd_samplers.py",
    "modules/sd_samplers_common.py",
    "modules/sd_samplers_kdiffusion.py",
    "modules/sd_schedulers.py",
    "modules/prompt_parser.py",
    "modules/styles.py",
    "modules/images.py",
    "modules/rng.py",
)

#: Reload / catalogue / model-selection entry points to inventory.
TARGET_CALLS = frozenset({
    "forge_model_reload",
    "reload_vae_weights",
    "apply_unet",
    "select_checkpoint",
    "get_closet_checkpoint_match",
    "refresh_model_loading_parameters",
    "unload_model_weights",
    "checkpoint_change",
})

#: Every reachable-or-not call site, with the declared direct-load behavior.
#: A new reachable call that is not listed here fails the closure guard.
DECLARED_CALL_BEHAVIOR: dict[tuple[str, int], dict[str, object]] = {
    ("modules/processing.py", 796): {
        "callee": "forge_model_reload", "caller": "manage_model_and_prompt_cache",
        "reachable": False, "why": "outer process_images wrapper only; Tier-0 enters at the inner loop",
    },
    ("modules/processing.py", 845): {
        "callee": "sd_vae.reload_vae_weights", "caller": "process_images",
        "reachable": False, "why": "outer wrapper only",
    },
    ("modules/processing.py", 898): {
        "callee": "sd_models.get_closet_checkpoint_match", "caller": "process_images_inner",
        "reachable": False, "why": "guarded by p.refiner_checkpoint not in (None,'','None','none'); refiner disabled",
    },
    ("modules/processing.py", 940): {
        "callee": "sd_unet.apply_unet", "caller": "process_images_inner",
        "reachable": True, "why": "unconditional; reads sd_checkpoint_info.model_name",
        "direct_load_behavior": "satisfied by the Tier-0 compatibility identity",
    },
    ("modules/processing.py", 956): {
        "callee": "sd_models.forge_model_reload", "caller": "process_images_inner",
        "reachable": True, "why": "guard is true for plain txt2img (no txt2img_upscale attribute)",
        "direct_load_behavior": "native early return: returns the loaded engine with reloaded False",
    },
    ("modules/processing.py", 1315): {
        "callee": "sd_models.get_closet_checkpoint_match", "caller": "init",
        "reachable": False, "why": "guarded by self.enable_hr; Hires disabled",
    },
    ("modules/processing.py", 1433): {
        "callee": "main_entry.checkpoint_change", "caller": "sample",
        "reachable": False, "why": "Hires / checkpoint-change branch",
    },
    ("modules/processing.py", 1445): {
        "callee": "main_entry.refresh_model_loading_parameters", "caller": "sample",
        "reachable": False, "why": "behind `if reload:`, set only by the Hires checkpoint branch",
    },
    ("modules/processing.py", 1446): {
        "callee": "sd_models.forge_model_reload", "caller": "sample",
        "reachable": False, "why": "behind `if reload:`",
    },
    ("modules/processing.py", 1449): {
        "callee": "main_entry.checkpoint_change", "caller": "sample",
        "reachable": False, "why": "Hires restore branch",
    },
    ("modules/processing.py", 1450): {
        "callee": "main_entry.refresh_model_loading_parameters", "caller": "sample",
        "reachable": False, "why": "Hires restore branch",
    },
    ("modules/sd_samplers_common.py", 367): {
        "callee": "main_entry.checkpoint_change", "caller": "apply_refiner",
        "reachable": False, "why": "refiner path; refiner disabled",
    },
    ("modules/sd_samplers_common.py", 375): {
        "callee": "main_entry.refresh_model_loading_parameters", "caller": "apply_refiner",
        "reachable": False, "why": "refiner path",
    },
    ("modules/sd_samplers_common.py", 376): {
        "callee": "sd_models.forge_model_reload", "caller": "apply_refiner",
        "reachable": False, "why": "refiner path",
    },
    ("modules/sd_samplers_common.py", 378): {
        "callee": "main_entry.checkpoint_change", "caller": "apply_refiner",
        "reachable": False, "why": "refiner path",
    },
}

VENV_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

PRELUDE = """
import os, sys
sys.argv = ["tier0-reload-case"]
sys.path.insert(0, {app!r})
sys.path.insert(0, os.path.join({app!r}, "modules_forge", "packages"))
from pathlib import Path as _Path
from forge_headless.headless_options import headless_options as _headless_options
from forge_headless.headless_compat import COMPAT_OPTION_OVERRIDES as _OV
_options_ctx = _headless_options(_Path({app!r}), overrides=dict(_OV))
_options_ctx.__enter__()
"""

EPILOGUE = """
_options_ctx.__exit__(None, None, None)
"""


def case_interpreter() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable


def run_case(body: str) -> subprocess.CompletedProcess:
    import tempfile

    with tempfile.TemporaryDirectory() as work:
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
            env={**dict(os.environ)},
        )


def assert_case_ok(case: unittest.TestCase, result, label: str) -> None:
    case.assertEqual(
        result.returncode,
        0,
        f"{label} failed\nstdout:\n{result.stdout[-2500:]}\n"
        f"stderr:\n{result.stderr[-3000:]}",
    )
    case.assertIn("CASE_OK", result.stdout)


def enclosing(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best.name if best else "<module scope>"


def closure_reload_calls() -> dict[tuple[str, int], str]:
    found = {}
    for rel in CLOSURE_MODULES:
        path = APP_ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name.rsplit(".", 1)[-1] in TARGET_CALLS:
                found[(rel, node.lineno)] = name
    return found


# ------------------------------------------- 7.1 production reload contract


class ForgeReloadContractTests(unittest.TestCase):
    """Pin the real early-return structure. Fail if Forge changes it."""

    def _reload(self) -> ast.FunctionDef:
        tree = ast.parse(SD_MODELS.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "forge_model_reload":
                return node
        self.fail("forge_model_reload not found")

    def test_compared_fields_are_unchanged(self) -> None:
        source = SD_MODELS.read_text(encoding="utf-8")
        self.assertIn("current_hash = str(model_data.forge_loading_parameters)", source)
        self.assertIn("if model_data.forge_hash == current_hash:", source)

    def test_comparison_uses_str_of_the_parameters(self) -> None:
        # The adapter computes its hash the same way; if Forge switched to a
        # digest or structural equality, this fails rather than silently
        # producing a token that never matches.
        node = self._reload()
        first = node.body[0]
        self.assertIsInstance(first, ast.Assign)
        self.assertEqual(ast.unparse(first.value), "str(model_data.forge_loading_parameters)")

    def test_early_return_shape_is_engine_and_false(self) -> None:
        node = self._reload()
        guard = node.body[1]
        self.assertIsInstance(guard, ast.If)
        returned = guard.body[0]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual(ast.unparse(returned.value), "(model_data.sd_model, False)")

    def test_catalogue_lookup_is_after_the_early_return(self) -> None:
        # The whole design depends on this ordering.
        node = self._reload()
        guard_line = node.body[1].lineno
        lookup_lines = [
            sub.lineno
            for sub in ast.walk(node)
            if isinstance(sub, ast.Subscript)
            and ast.unparse(sub).startswith("model_data.forge_loading_parameters[")
        ]
        self.assertTrue(lookup_lines, "catalogue lookup not found")
        self.assertTrue(
            all(line > guard_line for line in lookup_lines),
            f"a catalogue lookup moved before the early return: {lookup_lines} vs {guard_line}",
        )

    def test_model_data_defaults_do_not_accidentally_match(self) -> None:
        # `forge_loading_parameters = {}` and `forge_hash = ""` must NOT satisfy
        # the predicate, or every fresh process would skip a real load.
        source = SD_MODELS.read_text(encoding="utf-8")
        self.assertIn("self.forge_loading_parameters = {}", source)
        self.assertIn('self.forge_hash = ""', source)
        self.assertNotEqual(str({}), "")

    def test_inner_loop_reload_call_still_exists(self) -> None:
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn(
            "sd_models.forge_model_reload()  # model can be changed for example by refiner, hiresfix",
            source,
            "the inner-loop reload call changed; the direct-load contract must be re-derived",
        )

    def test_processing_and_sd_models_are_not_weakened(self) -> None:
        # No suppression: the call is not wrapped, guarded by hasattr, or
        # monkey-patched anywhere in the Studio tree.
        probe = PROBE.read_text(encoding="utf-8")
        for forbidden in (
            "forge_model_reload = ",
            "monkeypatch",
            "except AttributeError",
        ):
            self.assertNotIn(forbidden, probe)


# ------------------------------------ 7.2 / 7.3 installation and fast path


class DirectLoadBookkeepingTests(unittest.TestCase):
    def test_parameters_carry_no_path_filename_or_digest(self) -> None:
        from forge_headless.direct_load_reload import direct_load_parameters

        class _Engine:
            pass

        params = direct_load_parameters(_Engine(), runtime_label="studio-tier0-session")
        rendered = str(params)
        for forbidden in (":", "\\", "/", ".safetensors"):
            self.assertNotIn(forbidden, rendered.replace("':", "'").replace('":', '"'))
        self.assertNotIn("checkpoint_info", params)
        self.assertNotIn("sha256", rendered)
        self.assertNotIn("hash", rendered)

    def test_parameters_are_deterministic_and_engine_specific(self) -> None:
        from forge_headless.direct_load_reload import direct_load_parameters

        class _Engine:
            pass

        one, two = _Engine(), _Engine()
        self.assertEqual(
            direct_load_parameters(one, runtime_label="x"),
            direct_load_parameters(one, runtime_label="x"),
        )
        self.assertNotEqual(
            direct_load_parameters(one, runtime_label="x"),
            direct_load_parameters(two, runtime_label="x"),
        )

    def test_marker_key_distinguishes_it_from_catalogue_parameters(self) -> None:
        from forge_headless.direct_load_reload import DIRECT_LOAD_KEY, direct_load_parameters

        class _Engine:
            pass

        self.assertTrue(direct_load_parameters(_Engine(), runtime_label="x")[DIRECT_LOAD_KEY])

    def test_install_satisfies_the_native_predicate(self) -> None:
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "md = sd_models.model_data\n"
            "assert md.sd_model is shared.sd_model\n"
            "assert md.sd_model is engine\n"
            "assert str(md.forge_loading_parameters) == md.forge_hash\n"
            "assert book.predicate_satisfied()\n"
            "book.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "install satisfies predicate")

    def test_real_forge_model_reload_returns_the_existing_engine(self) -> None:
        # 7.3: the real function, with forbidden doubles proving no catalogue,
        # no selection, no loader, no payload access.
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "import backend.loader as _loader\n"
            "calls = {'select': 0, 'match': 0, 'loader': 0}\n"
            "def _boom_select(*a, **k):\n"
            "    calls['select'] += 1; raise AssertionError('select_checkpoint called')\n"
            "def _boom_match(*a, **k):\n"
            "    calls['match'] += 1; raise AssertionError('catalogue lookup called')\n"
            "def _boom_loader(*a, **k):\n"
            "    calls['loader'] += 1; raise AssertionError('forge_loader called')\n"
            "sd_models.select_checkpoint = _boom_select\n"
            "sd_models.get_closet_checkpoint_match = _boom_match\n"
            "sd_models.forge_loader = _boom_loader\n"
            "_loader.forge_loader = _boom_loader\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "returned, reloaded = sd_models.forge_model_reload()\n"
            "assert returned is engine, returned\n"
            "assert reloaded is False, reloaded\n"
            "assert calls == {'select': 0, 'match': 0, 'loader': 0}, calls\n"
            "book.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "real forge_model_reload no-op")

    def test_verify_fast_path_reports_the_proof(self) -> None:
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "assert book.verify_fast_path(engine) is True\n"
            "r = book.to_dict()\n"
            "assert r['reload_bookkeeping_installed']\n"
            "assert r['reload_engine_matches']\n"
            "assert r['reload_fast_path_verified']\n"
            "assert r['reload_returned_existing_engine']\n"
            "assert r['reload_reported_reloaded_false']\n"
            "book.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "verify_fast_path telemetry")


# --------------------------------------------------- 7.4 mismatch behavior


class MismatchBehaviorTests(unittest.TestCase):
    """Prove the adapter satisfies the real contract rather than neutralizing it."""

    def test_a_mismatched_hash_enters_native_reload_logic(self) -> None:
        # Intercepted before any filesystem or catalogue operation: the native
        # path is entered, which is exactly what must NOT happen when the
        # bookkeeping is correct.
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "sd_models.model_data.forge_hash = 'deliberately-wrong'\n"
            "assert not book.predicate_satisfied()\n"
            "entered = False\n"
            "try:\n"
            "    sd_models.forge_model_reload()\n"
            "except ValueError as exc:\n"
            "    entered = 'Failed to find available model' in str(exc)\n"
            "assert entered, 'native reload logic was not entered on mismatch'\n"
            "book.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "mismatch enters native reload")

    def test_verify_fast_path_refuses_to_call_on_mismatch(self) -> None:
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "sd_models.model_data.forge_hash = 'deliberately-wrong'\n"
            "called = {'n': 0}\n"
            "_real = sd_models.forge_model_reload\n"
            "def _spy(*a, **k):\n"
            "    called['n'] += 1; return _real(*a, **k)\n"
            "sd_models.forge_model_reload = _spy\n"
            "assert book.verify_fast_path(engine) is False\n"
            "assert called['n'] == 0, 'verify_fast_path called reload on a mismatch'\n"
            "sd_models.forge_model_reload = _real\n"
            "book.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "no call on mismatch")


# ------------------------------------------------ 7.5 complete call closure


class ReloadCallClosureTests(unittest.TestCase):
    def test_every_reachable_call_has_declared_behavior(self) -> None:
        found = closure_reload_calls()
        undeclared = {
            key: name for key, name in found.items() if key not in DECLARED_CALL_BEHAVIOR
        }
        self.assertEqual(
            undeclared,
            {},
            "A reload/catalogue/model-selection call appeared in the Tier-0 closure "
            "without a declared direct-load behavior. Declare it and prove what it "
            "does for a direct session -- do NOT suppress the call.",
        )

    def test_declared_sites_still_exist(self) -> None:
        # Guards against the inventory silently going stale.
        found = closure_reload_calls()
        missing = [key for key in DECLARED_CALL_BEHAVIOR if key not in found]
        self.assertEqual(
            missing, [], f"declared call sites no longer found: {missing}"
        )

    def test_the_known_inner_reload_is_still_reachable(self) -> None:
        entry = DECLARED_CALL_BEHAVIOR[("modules/processing.py", 956)]
        self.assertTrue(entry["reachable"])
        self.assertEqual(entry["caller"], "process_images_inner")
        found = closure_reload_calls()
        self.assertEqual(found[("modules/processing.py", 956)], "sd_models.forge_model_reload")

    def test_closure_module_list_has_not_shrunk(self) -> None:
        for rel in ("modules/processing.py", "modules/sd_samplers_common.py",
                    "modules/sd_unet.py", "modules/rng.py"):
            self.assertIn(rel, CLOSURE_MODULES)

    def test_only_two_calls_are_reachable_under_the_exact_profile(self) -> None:
        reachable = {
            key for key, entry in DECLARED_CALL_BEHAVIOR.items() if entry["reachable"]
        }
        self.assertEqual(
            reachable,
            {("modules/processing.py", 940), ("modules/processing.py", 956)},
        )

    def test_cached_params_reads_the_bookkeeping_value(self) -> None:
        # processing.py:425 folds str(forge_loading_parameters) into a cache
        # key, which is why the installed value must carry no path.
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn("str(sd_models.model_data.forge_loading_parameters)", source)


# ------------------------- 7.6 / 7.7 continuation and restoration


class ContinuationAndRestorationTests(unittest.TestCase):
    def test_setup_reaches_through_the_inner_reload(self) -> None:
        # Options, state, startup compatibility, identity, bookkeeping, then the
        # real inner reload call -- stopping before conditioning.
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from forge_headless.model_identity import attach_model_identity, DEFAULT_RUNTIME_LABEL\n"
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "compat = HeadlessCompatibilityContext(); compat.install()\n"
            "from modules import shared, sd_models, sd_unet\n"
            "import modules.processing\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "attach_model_identity(engine)\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label=DEFAULT_RUNTIME_LABEL)\n"
            "from modules.processing import StableDiffusionProcessingTxt2Img\n"
            "p = StableDiffusionProcessingTxt2Img(\n"
            "    prompt='a quiet scene', negative_prompt='', seed=123456789,\n"
            "    sampler_name='Euler', scheduler='Automatic', batch_size=1,\n"
            "    n_iter=1, steps=12, width=768, height=768, enable_hr=False,\n"
            "    do_not_save_samples=True, do_not_save_grid=True)\n"
            "p.scripts = None\n"
            "p.setup_prompts()                                   # :904 -> :407\n"
            "assert p.all_prompts == ['a quiet scene']\n"
            "sd_unet.apply_unet()                                # :929\n"
            "returned, reloaded = sd_models.forge_model_reload() # :945\n"
            "assert returned is engine and reloaded is False\n"
            "book.restore(); compat.restore()\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "continuation through inner reload")

    def test_previous_bookkeeping_is_restored(self) -> None:
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "md = sd_models.model_data\n"
            "before = (md.forge_loading_parameters, md.forge_hash)\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "assert md.forge_hash != before[1]\n"
            "book.restore()\n"
            "after = (md.forge_loading_parameters, md.forge_hash)\n"
            "assert before == after, (before, after)\n"
            "assert book.to_dict()['reload_bookkeeping_restored']\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "bookkeeping restored")

    def test_restoration_happens_when_the_body_raises(self) -> None:
        result = run_case(
            "from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping\n"
            "from modules import shared, sd_models\n"
            "md = sd_models.model_data\n"
            "before = (md.forge_loading_parameters, md.forge_hash)\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "shared.sd_model = engine\n"
            "book = DirectLoadReloadBookkeeping()\n"
            "book.install(engine, runtime_label='studio-tier0-session')\n"
            "raised = False\n"
            "try:\n"
            "    raise RuntimeError('synthetic failure after inner reload')\n"
            "except RuntimeError:\n"
            "    raised = True\n"
            "finally:\n"
            "    book.restore()\n"
            "assert raised\n"
            "after = (md.forge_loading_parameters, md.forge_hash)\n"
            "assert before == after, (before, after)\n"
            "shared.sd_model = None\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "restoration after exception")

    def test_restore_before_install_is_safe(self) -> None:
        from forge_headless.direct_load_reload import DirectLoadReloadBookkeeping

        book = DirectLoadReloadBookkeeping()
        book.restore()
        self.assertFalse(book.to_dict()["reload_bookkeeping_restored"])

    def test_probe_installs_after_publication_and_restores_in_finally(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        worker = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "worker_main"
        )
        restored_in_finally = any(
            isinstance(node, ast.Try)
            and node.finalbody
            and "reload_state.restore()" in "\n".join(ast.unparse(s) for s in node.finalbody)
            for node in ast.walk(worker)
        )
        self.assertTrue(
            restored_in_finally,
            "worker_main must restore the reload bookkeeping in a finally",
        )
        publication = source.index("shared.sd_model = engine")
        install = source.index("reload_state.install(")
        self.assertLess(publication, install, "bookkeeping must install after publication")

    def test_probe_reports_the_required_telemetry_keys(self) -> None:
        from forge_headless.direct_load_reload import DirectLoadReloadState

        keys = set(
            DirectLoadReloadState(
                True, "s", "h", True, True, True, True, False
            ).to_dict()
        )
        for required in (
            "reload_bookkeeping_installed",
            "reload_bookkeeping_source",
            "reload_fast_path_verified",
            "reload_returned_existing_engine",
            "reload_reported_reloaded_false",
            "reload_bookkeeping_restored",
        ):
            self.assertIn(required, keys)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_RELOAD_BOOKKEEPING_TESTS,
            "Reload-bookkeeping test count changed: update "
            "EXPECTED_RELOAD_BOOKKEEPING_TESTS deliberately, or find the test "
            "that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
