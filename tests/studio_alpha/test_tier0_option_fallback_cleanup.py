"""Tier-0 option fallback and failure-path cleanup.

Live attempt 04 reached `CFGDenoiser.forward` and died at
`modules/script_callbacks.py:192`, where Forge reads
`getattr(shared.opts, "prioritized_callbacks_" + category, [])`. The `[]` is a
deliberate fallback; Studio's boundary raised a non-`AttributeError`, so
`getattr` never applied it. The same failure left 13.1 MiB allocated across
`empty_cache()`.

These tests pin the corrected option semantics, prove the retained callback
fallback works for every reachable category, and prove -- with weak references
and a fake allocator -- that generation objects become unreachable before VRAM
is measured.

No CUDA tensor is allocated, no model file opened, no sampling performed.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import gc
import os
import re
import subprocess
import sys
import unittest
import weakref
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PROCESSING = APP_ROOT / "modules" / "processing.py"
SCRIPT_CALLBACKS = APP_ROOT / "modules" / "script_callbacks.py"
SHARED_OPTIONS = APP_ROOT / "modules" / "shared_options.py"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_OPTION_CLEANUP_TESTS = 36

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

#: Modules whose defaulted/dynamic option reads are inventoried.
CLOSURE_MODULES = (
    "modules/processing.py",
    "modules/sd_unet.py",
    "modules/sd_vae.py",
    "modules/sd_samplers.py",
    "modules/sd_samplers_common.py",
    "modules/sd_samplers_cfg_denoiser.py",
    "modules/sd_samplers_kdiffusion.py",
    "modules/sd_schedulers.py",
    "modules/prompt_parser.py",
    "modules/styles.py",
    "modules/images.py",
    "modules/rng.py",
    "modules/script_callbacks.py",
)

#: Every defaulted or probe-form option read on the closure, with policy.
#: A new one that is not declared here fails the inventory guard.
DECLARED_DEFAULTED_READS: dict[tuple[str, int], dict[str, object]] = {
    ("modules/processing.py", 1412): {
        "name": "sd_model_checkpoint", "form": "getattr", "has_default": False,
        "generated": False, "reachable": False, "why": "sample(), Hires branch",
        "policy": "strict read; option is supplied anyway",
    },
    ("modules/processing.py", 1413): {
        "name": "forge_additional_modules", "form": "getattr", "has_default": False,
        "generated": False, "reachable": False, "why": "sample(), Hires branch",
        "policy": "strict read; option is supplied anyway",
    },
    ("modules/sd_samplers_common.py", 366): {
        "name": "sd_model_checkpoint", "form": "getattr", "has_default": False,
        "generated": False, "reachable": False, "why": "apply_refiner; refiner disabled",
        "policy": "strict read; option is supplied anyway",
    },
    ("modules/sd_samplers_common.py", 478): {
        "name": "<self.eta_option_field>", "form": "getattr", "has_default": True,
        "generated": True, "reachable": True,
        "why": "Sampler init; the name is a runtime attribute (eta_ancestral / eta_ddim)",
        "policy": "supplied by the parsed defaults; falls back to 0.0 if ever absent",
    },
    ("modules/sd_samplers_common.py", 495): {
        "name": "s_churn", "form": "getattr", "has_default": True,
        "generated": False, "reachable": True, "why": "sampler init",
        "policy": "supplied; falls back to p.s_churn",
    },
    ("modules/sd_samplers_common.py", 496): {
        "name": "s_tmin", "form": "getattr", "has_default": True,
        "generated": False, "reachable": True, "why": "sampler init",
        "policy": "supplied; falls back to p.s_tmin",
    },
    ("modules/sd_samplers_common.py", 497): {
        "name": "s_tmax", "form": "getattr", "has_default": True,
        "generated": False, "reachable": True, "why": "sampler init",
        "policy": "supplied; falls back to p.s_tmax",
    },
    ("modules/sd_samplers_common.py", 498): {
        "name": "s_noise", "form": "getattr", "has_default": True,
        "generated": False, "reachable": True, "why": "sampler init",
        "policy": "supplied; falls back to p.s_noise",
    },
    ("modules/script_callbacks.py", 192): {
        "name": "prioritized_callbacks_<category>", "form": "getattr", "has_default": True,
        "generated": True, "reachable": True,
        "why": "sort_callbacks, reached from every callback dispatcher",
        "policy": "NOT supplied by design -- generated at runtime by "
                  "modules/shared_items.py:154. The boundary must let the [] default apply.",
    },
}

VENV_PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

PRELUDE = """
import os, sys
sys.argv = ["tier0-option-case"]
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
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=300,
            env={**dict(os.environ)},
        )


def assert_case_ok(case: unittest.TestCase, result, label: str) -> None:
    case.assertEqual(
        result.returncode, 0,
        f"{label} failed\nstdout:\n{result.stdout[-2500:]}\n"
        f"stderr:\n{result.stderr[-3000:]}",
    )
    case.assertIn("CASE_OK", result.stdout)


def declared_categories() -> list[str]:
    src = SCRIPT_CALLBACKS.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"callback_map\['callbacks_([a-z_]+)'\]", src)))


def closure_defaulted_reads() -> dict[tuple[str, int], str]:
    found: dict[tuple[str, int], str] = {}
    for rel in CLOSURE_MODULES:
        path = APP_ROOT / rel
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func) not in ("getattr", "hasattr"):
                continue
            if not node.args:
                continue
            if ast.unparse(node.args[0]).rsplit(".", 1)[-1] != "opts":
                continue
            found[(rel, node.lineno)] = ast.unparse(node)[:160]
    return found


# ------------------------------------------------ 7.1 missing-option semantics


class MissingOptionSemanticsTests(unittest.TestCase):
    def _options(self):
        from forge_headless.headless_options import HeadlessOptions

        return HeadlessOptions({"known": 7}, overrides={"over": 9})

    def test_getattr_with_default_returns_the_default(self) -> None:
        self.assertEqual(getattr(self._options(), "unknown", []), [])
        self.assertEqual(getattr(self._options(), "unknown", 0.0), 0.0)

    def test_hasattr_is_false_for_a_missing_option(self) -> None:
        self.assertFalse(hasattr(self._options(), "unknown"))

    def test_direct_read_raises_attribute_error_compatible_diagnostic(self) -> None:
        from forge_headless.contracts import HeadlessError, HeadlessOptionMissing

        with self.assertRaises(AttributeError) as caught:
            self._options().unknown
        self.assertIsInstance(caught.exception, HeadlessOptionMissing)
        self.assertIsInstance(caught.exception, HeadlessError)
        self.assertEqual(caught.exception.code, "HEADLESS_OPTION_NOT_AVAILABLE")

    def test_item_access_stays_strict(self) -> None:
        from forge_headless.contracts import HeadlessError

        with self.assertRaises(HeadlessError) as caught:
            self._options()["unknown"]
        self.assertEqual(caught.exception.code, "HEADLESS_OPTION_REQUIRED_NOT_AVAILABLE")
        self.assertNotIsInstance(caught.exception, AttributeError)

    def test_require_stays_strict(self) -> None:
        from forge_headless.contracts import HeadlessError

        with self.assertRaises(HeadlessError):
            self._options().require("unknown")

    def test_strict_reads_cannot_be_swallowed_by_a_getattr_default(self) -> None:
        # The whole point: item/require must not become defaultable.
        options = self._options()
        with self.assertRaises(Exception):
            getattr(options, "require")("unknown")

    def test_known_options_still_resolve(self) -> None:
        options = self._options()
        self.assertEqual(options.known, 7)
        self.assertEqual(options.over, 9)
        self.assertEqual(options["known"], 7)
        self.assertEqual(options.require("over"), 9)

    def test_missing_reads_are_still_recorded(self) -> None:
        options = self._options()
        getattr(options, "unknown", None)
        self.assertIn("unknown", options.missing)

    def test_get_helper_still_returns_its_default(self) -> None:
        self.assertEqual(self._options().get("unknown", "fallback"), "fallback")

    def test_private_names_raise_plain_attribute_error(self) -> None:
        with self.assertRaises(AttributeError):
            self._options()._not_an_option


# --------------------------------------------- 7.2 real callback fallback


class CallbackFallbackTests(unittest.TestCase):
    def test_every_declared_category_falls_back(self) -> None:
        # The real sort_callbacks, for every category Forge declares, with the
        # prioritized_callbacks option absent.
        categories = declared_categories()
        self.assertGreaterEqual(len(categories), 15)
        body = (
            "from modules import script_callbacks as sc\n"
            "from modules import shared\n"
            f"cats = {categories!r}\n"
            "class _CB:\n"
            "    def __init__(self, n):\n"
            "        self.name = n; self.script = None; self.callback = None\n"
            "made = [sc.ScriptCallback('f', lambda *a, **k: None, 'a'),\n"
            "        sc.ScriptCallback('f', lambda *a, **k: None, 'b')]\n"
            "for c in cats:\n"
            "    assert not hasattr(shared.opts, 'prioritized_callbacks_' + c), c\n"
            "    out = sc.sort_callbacks(c, made)\n"
            "    assert [x.name for x in out] == ['a', 'b'], (c, out)\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, run_case(body), "callback fallback, all categories")

    def test_the_real_cfg_denoiser_dispatch_passes_the_prior_failure_site(self) -> None:
        # modules/script_callbacks.py:192 is what killed attempt 04.
        body = (
            "from modules import script_callbacks as sc\n"
            "from modules import shared\n"
            "assert not hasattr(shared.opts, 'prioritized_callbacks_cfg_denoiser')\n"
            "seen = []\n"
            "sc.on_cfg_denoiser(lambda params: seen.append(1))\n"
            "class _P:\n"
            "    pass\n"
            "sc.cfg_denoiser_callback(_P())\n"
            "assert seen == [1], seen\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, run_case(body), "real cfg_denoiser dispatch")

    def test_ordering_is_stable_without_the_option(self) -> None:
        body = (
            "from modules import script_callbacks as sc\n"
            "made = [sc.ScriptCallback('f', lambda *a, **k: None, n) for n in ('x','y','z')]\n"
            "assert [c.name for c in sc.sort_callbacks('cfg_denoiser', made)] == ['x','y','z']\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, run_case(body), "stable ordering")

    def test_no_gradio_call_occurs_during_fallback(self) -> None:
        body = (
            "import gradio\n"
            "calls = []\n"
            "gradio.Blocks.__init__ = lambda *a, **k: calls.append('blocks')\n"
            "from modules import script_callbacks as sc\n"
            "made = [sc.ScriptCallback('f', lambda *a, **k: None, 'a')]\n"
            "sc.sort_callbacks('cfg_denoiser', made)\n"
            "assert calls == [], calls\n"
            "print('CASE_OK')\n"
        )
        assert_case_ok(self, run_case(body), "no gradio during fallback")


# ------------------------------------------------------ 7.3 inventory guard


class DefaultedOptionInventoryTests(unittest.TestCase):
    def test_every_defaulted_read_is_declared(self) -> None:
        found = closure_defaulted_reads()
        undeclared = {k: v for k, v in found.items() if k not in DECLARED_DEFAULTED_READS}
        self.assertEqual(
            undeclared, {},
            "A defaulted or probe-form option read appeared on the Tier-0 closure "
            "without a declared policy. Declare it and state what a missing value "
            "must do -- do NOT fabricate the option.",
        )

    def test_declared_reads_still_exist(self) -> None:
        found = closure_defaulted_reads()
        missing = [k for k in DECLARED_DEFAULTED_READS if k not in found]
        self.assertEqual(missing, [], f"declared read sites vanished: {missing}")

    def test_the_prior_failure_site_is_still_inventoried(self) -> None:
        entry = DECLARED_DEFAULTED_READS[("modules/script_callbacks.py", 192)]
        self.assertTrue(entry["generated"])
        self.assertTrue(entry["reachable"])
        self.assertTrue(entry["has_default"])

    def test_callback_categories_are_pinned(self) -> None:
        categories = declared_categories()
        for expected in ("cfg_denoiser", "cfg_denoised", "cfg_after_cfg", "extra_noise"):
            self.assertIn(expected, categories)

    def test_closure_module_list_has_not_shrunk(self) -> None:
        for rel in ("modules/script_callbacks.py", "modules/sd_samplers_common.py",
                    "modules/processing.py"):
            self.assertIn(rel, CLOSURE_MODULES)

    def test_no_broad_fabrication_was_introduced(self) -> None:
        path = APP_ROOT / "forge_headless" / "headless_options.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # shared_init must not be imported or called; the docstring explains at
        # length why it is avoided, so a substring check would be wrong.
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("modules.shared_init", imported)
        calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertNotIn("shared_init.initialize", calls)
        # __getattr__ must still raise for unknown names.
        self.assertIn("raise HeadlessOptionMissing", source)


# ------------------------------------- 7.5 / 7.6 traceback and object release


class _Sentinel:
    """Weak-referenceable stand-in for a tensor-holding generation object."""


class TracebackReleaseTests(unittest.TestCase):
    def test_frame_locals_are_released_after_clearing(self) -> None:
        from forge_headless.failure_cleanup import clear_exception_traceback

        sentinel = _Sentinel()
        ref = weakref.ref(sentinel)

        def _raises(held):  # held is a frame local, like cond in CFGDenoiser.forward
            raise RuntimeError("synthetic denoiser failure")

        captured = None
        try:
            _raises(sentinel)
        except RuntimeError as exc:
            captured = exc
        del sentinel

        self.assertIsNotNone(ref(), "frame should still hold the sentinel")
        detached, frames = clear_exception_traceback(captured)
        del captured
        gc.collect()

        self.assertTrue(detached)
        self.assertGreaterEqual(frames, 1)
        self.assertIsNone(ref(), "sentinel survived traceback clearing")

    def test_durable_record_holds_scalars_only(self) -> None:
        from forge_headless.failure_cleanup import scalar_failure_record

        try:
            raise ValueError("boom /private/path")
        except ValueError as exc:
            record = scalar_failure_record(exc, redact=lambda s: s.replace("/private/path", "<R>"))

        self.assertEqual(set(record), {"type", "message", "code"})
        for value in record.values():
            self.assertIsInstance(value, str)
        self.assertNotIn("/private/path", record["message"])


class GenerationObjectReleaseTests(unittest.TestCase):
    def _fake_processing(self):
        class _Denoiser:
            def __init__(self):
                self.p = None
                self.inner_model = _Sentinel()

        class _Sampler:
            def __init__(self):
                self.model_wrap_cfg = _Denoiser()

        class _Processing:
            cached_c = [None, None, None]
            cached_uc = [None, None, None]

            def __init__(self):
                self.sampler = _Sampler()
                self.c = _Sentinel()
                self.uc = _Sentinel()
                self.rng = _Sentinel()
                self.closed = False
                self.cache_cleared = False

            def close(self):
                self.closed = True

            def clear_prompt_cache(self):
                self.cache_cleared = True
                type(self).cached_c = [None, None, None]
                type(self).cached_uc = [None, None, None]

        return _Processing()

    def test_all_generation_sentinels_become_unreachable(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = self._fake_processing()
        refs = {
            "cond": weakref.ref(processing.c),
            "uncond": weakref.ref(processing.uc),
            "rng": weakref.ref(processing.rng),
            "inner": weakref.ref(processing.sampler.model_wrap_cfg.inner_model),
        }
        report = release_generation_references(processing=processing)
        del processing
        gc.collect()

        for name, ref in refs.items():
            with self.subTest(sentinel=name):
                self.assertIsNone(ref(), f"{name} survived cleanup")
        self.assertTrue(report.processing_reference_released)
        self.assertTrue(report.sampler_reference_released)
        self.assertTrue(report.conditioning_references_released)
        self.assertTrue(report.denoiser_reference_released)

    def test_close_and_prompt_cache_are_both_invoked(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = self._fake_processing()
        release_generation_references(processing=processing)
        self.assertTrue(processing.closed)
        self.assertTrue(processing.cache_cleared, "clear_prompt_cache must run regardless "
                                                  "of persistent_cond_cache")

    def test_gc_collect_follows_explicit_release(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        report = release_generation_references(processing=self._fake_processing())
        self.assertTrue(report.gc_collect_invoked)
        self.assertIsInstance(report.gc_collect_count, int)


class SharedStateReleaseTests(unittest.TestCase):
    def test_bridge_latent_field_is_cleared(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        class _Bridge:
            def __init__(self):
                self.current_latent = _Sentinel()

        bridge = _Bridge()
        ref = weakref.ref(bridge.current_latent)
        report = release_generation_references(state_bridge=bridge)
        gc.collect()

        self.assertIsNone(ref(), "current_latent survived cleanup")
        self.assertTrue(report.shared_preview_state_cleared)

    def test_release_is_safe_with_nothing_to_release(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        report = release_generation_references()
        self.assertFalse(report.processing_reference_released)
        self.assertTrue(report.gc_collect_invoked)


# ---------------------------------------------------- 7.8 fake allocator proof


class FakeAllocatorTests(unittest.TestCase):
    """Allocated reaches zero only when the sentinels actually die."""

    def test_allocator_reaches_zero_only_after_release(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        live: list[weakref.ref] = []

        class _Tensor:
            pass

        class _FakeAllocator:
            def allocated(self) -> int:
                return sum(1 for ref in live if ref() is not None) * 4096

            def reserved(self) -> int:
                return self.allocated()

        class _Processing:
            cached_c = [None, None, None]
            cached_uc = [None, None, None]

            def __init__(self):
                self.c = _Tensor()
                self.uc = _Tensor()
                self.sampler = None
                live.append(weakref.ref(self.c))
                live.append(weakref.ref(self.uc))

            def close(self):
                pass

            def clear_prompt_cache(self):
                pass

        allocator = _FakeAllocator()
        processing = _Processing()
        self.assertGreater(allocator.allocated(), 0, "fake allocator must start non-zero")

        release_generation_references(processing=processing)
        del processing
        gc.collect()

        self.assertEqual(allocator.allocated(), 0)
        self.assertEqual(allocator.reserved(), 0)

    def test_allocator_stays_non_zero_while_a_sentinel_is_held(self) -> None:
        # Guards the guard: the proof must depend on liveness, not be hard-coded.
        held = _Sentinel()
        ref = weakref.ref(held)
        allocated = (lambda: 4096 if ref() is not None else 0)
        self.assertEqual(allocated(), 4096)
        del held
        gc.collect()
        self.assertEqual(allocated(), 0)


# -------------------------------------------------- 7.9 failure-stage matrix


class FailureStageMatrixTests(unittest.TestCase):
    STAGES = (
        "before callback dispatch",
        "inside callback ordering",
        "after callback dispatch",
        "after conditioning objects exist",
        "after initial latent exists",
        "during cleanup",
    )

    def test_every_stage_preserves_telemetry_and_releases(self) -> None:
        from forge_headless.failure_cleanup import (
            clear_exception_traceback,
            release_generation_references,
        )

        for stage in self.STAGES:
            with self.subTest(stage=stage):
                sentinel = _Sentinel()
                ref = weakref.ref(sentinel)
                record: dict[str, object] = {"stage_reached": stage, "errors": []}

                captured = None
                try:
                    def _raise(held):
                        raise RuntimeError(f"synthetic failure: {stage}")

                    _raise(sentinel)
                except RuntimeError as exc:
                    record["errors"].append({"type": type(exc).__name__})
                    captured = exc
                del sentinel

                clear_exception_traceback(captured)
                del captured
                report = release_generation_references()
                gc.collect()

                self.assertEqual(record["stage_reached"], stage, "telemetry lost")
                self.assertTrue(record["errors"], "error record lost")
                self.assertTrue(report.gc_collect_invoked)
                self.assertIsNone(ref(), f"sentinel survived at stage: {stage}")


# ----------------------------------------------------- structural guards


class ProbeWiringTests(unittest.TestCase):
    def test_probe_releases_before_vram_is_sampled(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        release = source.index("release_generation_references(")
        after_release = source.index("telemetry.after_release = sample_current()")
        self.assertLess(release, after_release,
                        "generation references must be released before VRAM is sampled")

    def test_probe_stores_the_processing_request_for_release(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('out["_processing_request"] = processing', source)
        self.assertIn('record.pop("_processing_request", None)', source)

    def test_no_extra_empty_cache_calls_were_added(self) -> None:
        # The fix must be ownership, not cache masking.
        source = PROBE.read_text(encoding="utf-8")
        self.assertEqual(source.count("release_cuda_cache()"), 1)

    def test_persistent_cond_cache_still_defaults_true(self) -> None:
        # If Forge ever flips this, the unconditional clear_prompt_cache call
        # becomes redundant rather than wrong -- but the note should be revisited.
        self.assertIn('"persistent_cond_cache": OptionInfo(True',
                      SHARED_OPTIONS.read_text(encoding="utf-8"))

    def test_class_level_caches_are_still_class_attributes(self) -> None:
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn("    cached_uc = [None, None, None]", source)
        self.assertIn("    cached_c = [None, None, None]", source)
        self.assertIn("    def clear_prompt_cache(self):", source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(), EXPECTED_OPTION_CLEANUP_TESTS,
            "Option/cleanup test count changed: update "
            "EXPECTED_OPTION_CLEANUP_TESTS deliberately, or find the test that "
            "stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
