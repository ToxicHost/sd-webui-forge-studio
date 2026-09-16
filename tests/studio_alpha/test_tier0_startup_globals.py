"""Tier-0 startup globals: the Neo `shared` fields the generation closure reads.

Attempt 01 died on missing model identity. Attempt 02 died at
`modules/processing.py:407` on `shared.prompt_styles`. A closure walk then found
two more the same path needs -- `shared.device` at `modules/rng.py:167` and
`shared.total_tqdm` at `modules/sd_samplers_common.py:451`.

These tests pin the whole set, so a third authorization is not spent finding the
next one. Runtime cases run in a fresh interpreter on the workspace venv,
because the canonical runner uses `-I -S` where Forge is not importable in
process. No model file is opened, no tensor allocated, no sampling performed.

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

SHARED = APP_ROOT / "modules" / "shared.py"
SHARED_INIT = APP_ROOT / "modules" / "shared_init.py"
STYLES = APP_ROOT / "modules" / "styles.py"
TOTAL_TQDM = APP_ROOT / "modules" / "shared_total_tqdm.py"
PROCESSING = APP_ROOT / "modules" / "processing.py"
RNG = APP_ROOT / "modules" / "rng.py"
SAMPLERS_COMMON = APP_ROOT / "modules" / "sd_samplers_common.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_STARTUP_GLOBAL_TESTS = 23

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

#: Modules the minimal txt2img closure actually reaches. Used by the closure
#: guard; kept here so widening the closure is a deliberate edit.
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
    "modules/sd_models.py",
    "modules/shared_total_tqdm.py",
    "modules/rng.py",
)

#: Supplied by boundaries that already existed before this milestone.
PRE_EXISTING_STUDIO_FIELDS = frozenset({"opts", "state"})

VENV_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

PRELUDE = """
import os, sys
sys.argv = ["tier0-globals-case"]
sys.path.insert(0, {app!r})
sys.path.insert(0, os.path.join({app!r}, "modules_forge", "packages"))
from pathlib import Path as _Path
from forge_headless.headless_options import headless_options as _headless_options
from forge_headless.headless_compat import COMPAT_OPTION_OVERRIDES as _COMPAT_OVERRIDES
_options_ctx = _headless_options(_Path({app!r}), overrides=dict(_COMPAT_OVERRIDES))
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


def fields_assigned_by_shared_init() -> set[str]:
    tree = ast.parse(SHARED_INIT.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "shared"
        and isinstance(node.ctx, ast.Store)
    }


def closure_reads_of_shared() -> dict[str, list[str]]:
    reads: dict[str, list[str]] = {}
    for rel in CLOSURE_MODULES:
        path = APP_ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "shared"
                and isinstance(node.ctx, ast.Load)
            ):
                reads.setdefault(node.attr, []).append(f"{rel}:{node.lineno}")
    return reads


# ------------------------------------------ 7.1 production constructor analysis


class ProductionInitializationTests(unittest.TestCase):
    """Pin what `shared_init` does, and that the classes are safe to reuse."""

    def test_shared_init_assignment_sites_are_unchanged(self) -> None:
        source = SHARED_INIT.read_text(encoding="utf-8")
        self.assertIn("shared.prompt_styles = styles.StyleDatabase(shared.styles_filename)", source)
        self.assertIn("shared.total_tqdm = shared_total_tqdm.TotalTQDM()", source)
        self.assertIn("shared.device = devices.device", source)

    def test_the_three_fields_are_declared_none(self) -> None:
        tree = ast.parse(SHARED.read_text(encoding="utf-8"))
        declared = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                declared[node.target.id] = node.value
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        declared[target.id] = node.value
        for field in ("prompt_styles", "total_tqdm", "device"):
            with self.subTest(field=field):
                value = declared.get(field)
                self.assertIsInstance(value, ast.Constant, f"{field} not declared at module scope")
                self.assertIsNone(value.value, f"{field} default is no longer None")

    def test_style_database_constructor_does_no_prohibited_io(self) -> None:
        tree = ast.parse(STYLES.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("gradio", "urllib", "requests", "socket", "http"):
            self.assertNotIn(forbidden, imported, f"modules/styles.py imports {forbidden}")

    def test_style_database_only_loads_paths_that_exist(self) -> None:
        # reload() appends a non-wildcard path only when it already exists, and
        # load_from_csv opens read-only. That is what makes construction safe
        # with no styles CSV present.
        source = STYLES.read_text(encoding="utf-8")
        self.assertIn("if os.path.exists(pattern):", source)
        self.assertIn('open(path, "r"', source)

    def test_total_tqdm_constructor_is_inert(self) -> None:
        tree = ast.parse(TOTAL_TQDM.read_text(encoding="utf-8"))
        init = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "TotalTQDM":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        init = item
        self.assertIsNotNone(init)
        calls = {ast.unparse(n.func) for n in ast.walk(init) if isinstance(n, ast.Call)}
        self.assertEqual(calls, set(), "TotalTQDM.__init__ must not call anything")

    def test_total_tqdm_display_is_gated_by_multiple_tqdm(self) -> None:
        # This gate is what the compatibility context relies on to stay silent.
        source = TOTAL_TQDM.read_text(encoding="utf-8")
        self.assertEqual(
            source.count("if not shared.opts.multiple_tqdm or shared.cmd_opts.disable_console_progressbars:"),
            2,
            "update() and updateTotal() must both early-return on multiple_tqdm",
        )

    def test_compat_overrides_disable_multiple_tqdm(self) -> None:
        from forge_headless.headless_compat import COMPAT_OPTION_OVERRIDES

        self.assertIs(COMPAT_OPTION_OVERRIDES["multiple_tqdm"], False)


# -------------------------------------------- 7.2 startup-global closure guard


class StartupGlobalClosureTests(unittest.TestCase):
    """Fail if the Tier-0 path gains a new unsupplied `shared_init` field."""

    def test_no_unsupplied_shared_init_field_is_reachable(self) -> None:
        from forge_headless.headless_compat import SUPPLIED_FIELDS

        assigned = fields_assigned_by_shared_init()
        reads = closure_reads_of_shared()
        supplied = set(SUPPLIED_FIELDS) | PRE_EXISTING_STUDIO_FIELDS

        unsupplied = {
            field: sorted(set(sites))
            for field, sites in reads.items()
            if field in assigned and field not in supplied
        }
        self.assertEqual(
            unsupplied,
            {},
            "A shared_init-supplied field is read on the Tier-0 closure but not "
            "installed by HeadlessCompatibilityContext. Supply it there -- do "
            "NOT guard the retained consumer.",
        )

    def test_the_inventory_scope_is_the_real_closure(self) -> None:
        # Guards the guard: if the closure list silently loses the modules where
        # the known reads live, the test above would pass vacuously.
        for rel in ("modules/processing.py", "modules/rng.py",
                    "modules/sd_samplers_common.py"):
            self.assertIn(rel, CLOSURE_MODULES)
        reads = closure_reads_of_shared()
        for field in ("prompt_styles", "device", "total_tqdm"):
            self.assertIn(field, reads, f"closure scan no longer sees shared.{field}")

    def test_known_read_sites_are_still_present(self) -> None:
        reads = closure_reads_of_shared()
        self.assertIn("modules/processing.py:418", reads["prompt_styles"])
        self.assertIn("modules/rng.py:167", reads["device"])
        self.assertIn("modules/sd_samplers_common.py:451", reads["total_tqdm"])

    def test_supplied_and_absent_sets_partition_shared_init(self) -> None:
        from forge_headless.headless_compat import (
            DELIBERATELY_ABSENT,
            SUPPLIED_FIELDS,
        )

        assigned = fields_assigned_by_shared_init()
        accounted = set(SUPPLIED_FIELDS) | set(DELIBERATELY_ABSENT) | PRE_EXISTING_STUDIO_FIELDS
        self.assertEqual(
            assigned - accounted,
            set(),
            "shared_init assigns a field the compatibility module neither "
            "supplies nor records as deliberately absent",
        )

    def test_rng_device_read_is_on_the_txt2img_path(self) -> None:
        # p.rng is built inside process_images_inner and used by p.sample.
        tree = ast.parse(PROCESSING.read_text(encoding="utf-8"))
        inner = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "process_images_inner"
        )
        constructs = [
            n.lineno for n in ast.walk(inner)
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "rng.ImageRNG"
        ]
        self.assertEqual(len(constructs), 1, "ImageRNG construction moved out of the inner loop")
        rng_source = RNG.read_text(encoding="utf-8")
        self.assertIn("torch.stack(xs).to(shared.device)", rng_source)


# ---------------------------------------------------- 7.3 prompt styles


class PromptStylesTests(unittest.TestCase):
    def test_empty_style_selection_preserves_both_prompts(self) -> None:
        result = run_case(
            "from modules import styles\n"
            "db = styles.StyleDatabase(['does-not-exist.csv'])\n"
            "p = 'a quiet scene, natural colors'\n"
            "n = ''\n"
            "assert db.apply_styles_to_prompt(p, []) == p\n"
            "assert db.apply_negative_styles_to_prompt(n, []) == n\n"
            "assert db.styles == {}, db.styles\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "empty style selection")

    def test_real_setup_prompts_no_longer_raises(self) -> None:
        # Exercises the actual failure from attempt 02: processing.py:407,
        # reached through the real setup_prompts(). Stops before conditioning.
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "from modules.processing import StableDiffusionProcessingTxt2Img\n"
            "p = StableDiffusionProcessingTxt2Img(\n"
            "    prompt='a quiet scene, natural colors', negative_prompt='',\n"
            "    seed=123456789, sampler_name='Euler', scheduler='Automatic',\n"
            "    batch_size=1, n_iter=1, steps=12, width=768, height=768,\n"
            "    enable_hr=False, do_not_save_samples=True, do_not_save_grid=True)\n"
            "p.scripts = None\n"
            "p.setup_prompts()\n"
            "assert p.all_prompts == ['a quiet scene, natural colors'], p.all_prompts\n"
            "assert p.all_negative_prompts == [''], p.all_negative_prompts\n"
            "assert p.main_prompt == 'a quiet scene, natural colors'\n"
            "compat.restore()\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "real setup_prompts")


# ---------------------------------------------------- 7.4 total progress


class TotalProgressTests(unittest.TestCase):
    def test_full_reachable_method_surface_is_safe_and_silent(self) -> None:
        result = run_case(
            "import io, contextlib\n"
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from modules import shared\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "t = shared.total_tqdm\n"
            "buf = io.StringIO()\n"
            "with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):\n"
            "    t.updateTotal(12)\n"
            "    for _ in range(12):\n"
            "        t.update()\n"
            "    t.clear()\n"
            "assert t._tqdm is None, 'a progress bar was constructed'\n"
            "assert buf.getvalue() == '', repr(buf.getvalue()[:200])\n"
            "compat.restore()\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "total_tqdm method surface")

    def test_it_is_not_a_second_progress_authority(self) -> None:
        # The owned bridge must remain the only source of Studio progress
        # events; total_tqdm must not advance the owned progress model.
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from forge_headless.headless_progress import HeadlessProgress, ForgeStateBridge\n"
            "from modules import shared\n"
            "progress = HeadlessProgress('compat-case', preview_enabled=False)\n"
            "shared.state = ForgeStateBridge(progress)\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "progress.set_total_steps(12)\n"
            "before = progress.snapshot().step\n"
            "shared.total_tqdm.update()\n"
            "shared.total_tqdm.updateTotal(99)\n"
            "after = progress.snapshot().step\n"
            "assert before == after, (before, after)\n"
            "assert progress.snapshot().total_steps == 12, progress.snapshot().total_steps\n"
            "compat.restore()\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "no duplicate progress authority")


# ------------------------------- 7.5 / 7.6 continuation and first-step callback


class PreSamplingContinuationTests(unittest.TestCase):
    def test_setup_reaches_past_the_former_failures(self) -> None:
        # Identity + compatibility together: the exact reads that stopped
        # attempt 01 (894-895) and attempt 02 (407 via 904), plus the sd_unet
        # lookup at 929. Stops before conditioning and tensor work.
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from forge_headless.model_identity import attach_model_identity\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "from modules import shared, sd_unet\n"
            "import modules.processing\n"
            "class _Engine:\n"
            "    pass\n"
            "engine = _Engine()\n"
            "attach_model_identity(engine)\n"
            "shared.sd_model = engine\n"
            "name = shared.sd_model.sd_checkpoint_info.name_for_extra   # 894\n"
            "hash_ = shared.sd_model.sd_model_hash                      # 895\n"
            "sd_unet.get_unet_option()                                  # 929\n"
            "from modules.processing import StableDiffusionProcessingTxt2Img\n"
            "p = StableDiffusionProcessingTxt2Img(\n"
            "    prompt='a quiet scene', negative_prompt='', seed=123456789,\n"
            "    sampler_name='Euler', scheduler='Automatic', batch_size=1,\n"
            "    n_iter=1, steps=12, width=768, height=768, enable_hr=False,\n"
            "    do_not_save_samples=True, do_not_save_grid=True)\n"
            "p.scripts = None\n"
            "p.setup_prompts()                                          # 904 -> 407\n"
            "assert name == 'studio-tier0-session'\n"
            "assert hash_ is None\n"
            "assert p.all_prompts == ['a quiet scene']\n"
            "assert shared.device is not None\n"
            "shared.sd_model = None\n"
            "compat.restore()\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "pre-sampling continuation")

    def test_first_sampler_callback_is_safe(self) -> None:
        # The real Sampler.callback_state seam with doubles: it writes
        # state.sampling_step and then calls shared.total_tqdm.update().
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from forge_headless.headless_progress import HeadlessProgress, ForgeStateBridge\n"
            "from modules import shared\n"
            "progress = HeadlessProgress('callback-case', preview_enabled=False)\n"
            "shared.state = ForgeStateBridge(progress)\n"
            "progress.set_total_steps(12)\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            # Import through modules.processing, as production does. Importing
            # sd_samplers_common directly trips a partially-initialised cycle
            # with sd_samplers_kdiffusion.
            "import modules.processing\n"
            "from modules.sd_samplers_common import Sampler\n"
            "s = Sampler.__new__(Sampler)\n"
            "s.stop_at = None\n"
            "assert progress.snapshot().step == 0\n"
            "s.callback_state({'i': 0})\n"
            "s.callback_state({'i': 1})\n"
            "snap = progress.snapshot()\n"
            "assert snap.step == 1, snap.step\n"
            "assert shared.total_tqdm._tqdm is None, 'a bar was built'\n"
            "compat.restore()\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "first sampler callback")


# --------------------------------------------------- 7.7 restoration on failure


class RestorationOnFailureTests(unittest.TestCase):
    def test_restore_returns_all_three_fields_to_their_previous_values(self) -> None:
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from modules import shared\n"
            "before = (shared.prompt_styles, shared.device, shared.total_tqdm)\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "assert shared.prompt_styles is not None\n"
            "assert shared.total_tqdm is not None\n"
            "assert shared.device is not None\n"
            "compat.restore()\n"
            "after = (shared.prompt_styles, shared.device, shared.total_tqdm)\n"
            "assert before == after, (before, after)\n"
            "r = compat.to_dict()\n"
            "assert r['prompt_styles_restored'] and r['total_tqdm_restored'] "
            "and r['device_restored'], r\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "restoration of all three")

    def test_restoration_happens_when_the_body_raises(self) -> None:
        result = run_case(
            "from forge_headless.headless_compat import HeadlessCompatibilityContext\n"
            "from modules import shared\n"
            "before = (shared.prompt_styles, shared.device, shared.total_tqdm)\n"
            "compat = HeadlessCompatibilityContext()\n"
            "compat.install()\n"
            "raised = False\n"
            "try:\n"
            "    raise RuntimeError('synthetic failure during prompt setup')\n"
            "except RuntimeError:\n"
            "    raised = True\n"
            "finally:\n"
            "    compat.restore()\n"
            "assert raised\n"
            "after = (shared.prompt_styles, shared.device, shared.total_tqdm)\n"
            "assert before == after, (before, after)\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, result, "restoration after exception")

    def test_probe_installs_before_generation_and_restores_in_finally(self) -> None:
        source = (APP_ROOT / "scripts" / "headless" / "first_image_probe.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        worker = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "worker_main"
        )
        restored_in_finally = False
        for node in ast.walk(worker):
            if isinstance(node, ast.Try) and node.finalbody:
                block = "\n".join(ast.unparse(s) for s in node.finalbody)
                if "compat.restore()" in block:
                    restored_in_finally = True
        self.assertTrue(
            restored_in_finally,
            "worker_main must restore the compatibility context in a finally",
        )
        self.assertIn("compat.install()", source)
        self.assertIn("record['startup_compatibility'] = compat.to_dict()".replace("'", '"'), source)

    def test_installation_is_reported_for_partial_telemetry(self) -> None:
        from forge_headless.headless_compat import CompatibilityInstallation

        keys = set(
            CompatibilityInstallation(
                True, True, True, "a", "b", "c", False, False, False, 0
            ).to_dict()
        )
        for required in (
            "prompt_styles_installed",
            "total_tqdm_installed",
            "prompt_styles_source",
            "total_tqdm_source",
            "prompt_styles_restored",
            "total_tqdm_restored",
        ):
            self.assertIn(required, keys)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_STARTUP_GLOBAL_TESTS,
            "Startup-global test count changed: update "
            "EXPECTED_STARTUP_GLOBAL_TESTS deliberately, or find the test that "
            "stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
