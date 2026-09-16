"""The compute runtime Studio asks the inherited backend for.

Neo reaches these through `webui-user.bat`:

    --xformers --sage --uv --pin-shared-memory --cuda-malloc
    --cuda-stream --fast-fp16

Studio blanks `sys.argv` before the first Neo import (`backend_bootstrap.py`),
deliberately, so none of that could ever reach it -- Studio ran stock PyTorch
attention with no allocator, stream or pinned-memory policy while the control
it was benchmarked against ran with six flags. That is most of the residual
gap once the resolution difference between the two runs is removed.

The handoff is explicit that the fix must NOT be to copy Neo's command line:
argv isolation stays, and Studio owns validated settings that it PROJECTS into
the inherited modules before those modules read them.

What these tests protect, in order of how quietly each would fail:

**The projection point.** `backend/memory_management.py` reads `args.fast_fp16`
(:331), `args.cuda_stream` (:1382) and `args.pin_shared_memory` (:1456) at
MODULE SCOPE. Written after that import, they do nothing and report nothing --
a run that looks configured and behaves exactly as before.

**Refusal is reported.** Asking for `sage` on a machine without sageattention
must land in `refused`, not in the settings. Otherwise a benchmark credits an
accelerator that was never running, which is the specific failure that made the
first parity comparison meaningless.

**`fast_fp16` is not on by default.** It enables fp16 accumulation, which
changes numerics. Section 48 forbids winning a benchmark by altering what is
computed, so AUTO leaves it off until an owner opts in.

No GPU, no torch import, no Studio launch: every assertion here is about what
Studio DECIDES, which is what regressed.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.runtime_options import (  # noqa: E402
    ATTENTION_ORDER,
    Capability,
    PerformanceMode,
    RuntimeOptions,
    apply_environment,
    capabilities,
    project,
    resolve,
)

EXPECTED_RUNTIME_OPTIONS_TESTS = 48


def _capabilities(**available: bool) -> tuple[Capability, ...]:
    """A machine with exactly the capabilities named."""

    names = ("attention:sage", "attention:xformers", "attention:pytorch",
             "cuda_malloc", "cuda_stream", "pin_shared_memory", "fast_fp16",
             "autotune")
    return tuple(
        Capability(name=name, available=available.get(name.replace(":", "_"),
                                                      False))
        for name in names
    )


_EVERYTHING = dict(attention_sage=True, attention_xformers=True,
                   attention_pytorch=True, cuda_malloc=True, cuda_stream=True,
                   pin_shared_memory=True, fast_fp16=True, autotune=True)
_BARE = dict(attention_pytorch=True)


class CapabilityTests(unittest.TestCase):
    def test_capability_detection_never_imports_torch(self) -> None:
        # It runs BEFORE torch is imported, so that `cuda_malloc` can still
        # set the allocator variable. Importing torch to ask about torch would
        # defeat the one thing this has to be early for.
        before = "torch" in sys.modules
        capabilities()
        self.assertEqual(before, "torch" in sys.modules)

    def test_every_capability_says_why_when_it_is_absent(self) -> None:
        for found in capabilities():
            if not found.available:
                self.assertTrue(found.reason,
                                f"{found.name} is unavailable and silent")

    def test_pytorch_attention_is_always_available(self) -> None:
        self.assertTrue(
            any(c.name == "attention:pytorch" and c.available
                for c in capabilities()))


class ResolutionTests(unittest.TestCase):
    def test_auto_does_not_opt_an_owner_into_a_quantised_kernel(self) -> None:
        """Installed is not the same as beneficial, or as safe.

        SageAttention quantises Q.K to INT8 (`backend/attention.py:43-49`), so
        it changes what is computed -- the same class of decision as
        `fast_fp16`, which AUTO also leaves off. It is NVIDIA-only, it is not
        faster on every card, and `sage_enabled()` checks only import success
        with no compute-capability test, so an unsuitable card falls back per
        call with an error logged.
        """

        settings = resolve(None, _capabilities(**_EVERYTHING))
        self.assertEqual("pytorch", settings.attention)
        self.assertEqual((), settings.refused,
                         "AUTO did not ask for it, so nothing was refused")

    def test_an_owner_who_asks_for_it_gets_it(self) -> None:
        settings = resolve({"attention": "sage"}, _capabilities(**_EVERYTHING))
        self.assertEqual("sage", settings.attention)

    def test_auto_falls_back_when_nothing_faster_is_installed(self) -> None:
        settings = resolve(None, _capabilities(**_BARE))
        self.assertEqual("pytorch", settings.attention)
        self.assertEqual((), settings.refused)

    def test_asking_for_an_uninstalled_backend_is_refused_out_loud(self) -> None:
        settings = resolve({"attention": "sage"}, _capabilities(**_BARE))
        self.assertEqual("pytorch", settings.attention)
        self.assertIn("attention:sage", settings.refused)

    def test_an_unknown_attention_name_is_refused(self) -> None:
        settings = resolve({"attention": "magic"}, _capabilities(**_EVERYTHING))
        self.assertEqual("pytorch", settings.attention)
        self.assertIn("attention:magic", settings.refused)

    def test_fast_fp16_is_off_under_auto(self) -> None:
        # It changes numerics. Section 48: no winning a benchmark by altering
        # what is computed.
        self.assertFalse(resolve(None, _capabilities(**_EVERYTHING)).fast_fp16)

    def test_fast_fp16_is_honoured_when_the_owner_asks(self) -> None:
        settings = resolve({"fast_fp16": True}, _capabilities(**_EVERYTHING))
        self.assertTrue(settings.fast_fp16)

    def test_a_flag_the_machine_cannot_do_is_refused_not_set(self) -> None:
        settings = resolve({"cuda_malloc": True, "fast_fp16": True},
                           _capabilities(**_BARE))
        self.assertFalse(settings.cuda_malloc)
        self.assertFalse(settings.fast_fp16)
        self.assertIn("cuda_malloc", settings.refused)
        self.assertIn("fast_fp16", settings.refused)

    def test_compatibility_asks_for_nothing_beyond_stock_torch(self) -> None:
        settings = resolve({"mode": "compatibility", "cuda_malloc": True},
                           _capabilities(**_EVERYTHING))
        self.assertEqual("pytorch", settings.attention)
        self.assertFalse(settings.cuda_malloc)
        self.assertEqual(0, settings.cuda_stream)

    def test_an_unreadable_mode_falls_back_to_auto(self) -> None:
        self.assertIs(PerformanceMode.AUTO,
                      resolve({"mode": "ludicrous"}, _capabilities(**_BARE)).mode)

    def test_stream_count_is_bounded(self) -> None:
        settings = resolve({"cuda_stream": 999}, _capabilities(**_EVERYTHING))
        self.assertLessEqual(settings.cuda_stream, 8)

    def test_a_nonsense_stream_count_is_refused(self) -> None:
        settings = resolve({"cuda_stream": "many"}, _capabilities(**_EVERYTHING))
        self.assertEqual(0, settings.cuda_stream)
        self.assertIn("cuda_stream", settings.refused)

    def test_an_unknown_setting_is_reported_not_passed_through(self) -> None:
        settings = resolve({"turbo": True}, _capabilities(**_EVERYTHING))
        self.assertIn("unknown:turbo", settings.refused)

    def test_a_malformed_config_never_raises(self) -> None:
        for document in (None, [], "fast", 7):
            with self.subTest(document=document):
                self.assertIsInstance(resolve(document), RuntimeOptions)

    def test_the_projection_carries_no_paths_or_environment(self) -> None:
        described = resolve(None, _capabilities(**_EVERYTHING)).describe()
        self.assertEqual(
            {"mode", "attention", "cuda_malloc", "cuda_stream",
             "pin_shared_memory", "fast_fp16", "autotune",
             "composite_tiles_on_gpu", "refused"},
            set(described),
        )


class ProjectionTests(unittest.TestCase):
    """Where the settings are written, and what happens when they are not."""

    def _namespace(self) -> types.SimpleNamespace:
        # The shape `backend/args.py:122` produces: the attributes exist.
        return types.SimpleNamespace(
            cuda_malloc=False, pin_shared_memory=False,
            fast_fp16=False, cuda_stream=None,
            use_pytorch_cross_attention=False, disable_sage=False,
            disable_flash=False, disable_xformers=False,
        )

    def test_the_settings_reach_the_inherited_namespace(self) -> None:
        args = self._namespace()
        settings = resolve({"cuda_malloc": True, "pin_shared_memory": True,
                            "fast_fp16": True, "cuda_stream": 2},
                           _capabilities(**_EVERYTHING))
        written = project(settings, args)
        self.assertTrue(args.cuda_malloc)
        self.assertTrue(args.pin_shared_memory)
        self.assertTrue(args.fast_fp16)
        self.assertEqual(2, args.cuda_stream)
        self.assertLessEqual({"cuda_malloc", "pin_shared_memory", "fast_fp16",
                              "cuda_stream"}, set(written))

    def test_zero_streams_is_written_as_none(self) -> None:
        # `memory_management.py:1382` tests `args.cuda_stream is None`, not
        # falsiness, so a 0 would enable one stream rather than none.
        args = self._namespace()
        project(resolve({"mode": "compatibility"}, _capabilities(**_EVERYTHING)),
                args)
        self.assertIsNone(args.cuda_stream)

    def test_an_attribute_the_backend_does_not_declare_is_not_invented(self) -> None:
        # A typo would otherwise create an attribute nothing reads, and the
        # run would look configured while behaving exactly as before.
        args = types.SimpleNamespace(cuda_malloc=False)
        written = project(resolve({"cuda_malloc": True},
                                  _capabilities(**_EVERYTHING)), args)
        self.assertEqual({"cuda_malloc"}, set(written))
        self.assertFalse(hasattr(args, "fast_fp16"))

    def test_the_attention_choice_is_enforced_not_merely_reported(self) -> None:
        """Studio must not name a kernel it did not select.

        The backend picks by import success in the order
        sage -> flash -> xformers -> pytorch (`backend/attention.py:331-360`),
        so with `sageattention` installed and nothing projected, AUTO would
        REPORT pytorch while the backend ran sage. A benchmark would then
        credit the wrong kernel.

        `use_pytorch_cross_attention` is the real override:
        `memory_management.py:276-280` clears XFORMERS, SAGE and FLASH
        availability, and runs after the import probes at :211-232.
        """

        args = self._namespace()
        project(resolve(None, _capabilities(**_EVERYTHING)), args)
        self.assertTrue(args.use_pytorch_cross_attention)

    def test_asking_for_sage_disables_nothing_ahead_of_it(self) -> None:
        # Sage is FIRST in the backend's order, so it needs only to not be
        # disabled -- and the pytorch override must not be set.
        args = self._namespace()
        project(resolve({"attention": "sage"}, _capabilities(**_EVERYTHING)),
                args)
        self.assertFalse(args.use_pytorch_cross_attention)
        self.assertFalse(args.disable_sage)

    def test_asking_for_xformers_disables_what_sits_ahead_of_it(self) -> None:
        # Flash sits BETWEEN sage and xformers, so both must go or the
        # request silently gets something else.
        args = self._namespace()
        project(resolve({"attention": "xformers"},
                        _capabilities(**_EVERYTHING)), args)
        self.assertTrue(args.disable_sage)
        self.assertTrue(args.disable_flash)
        self.assertFalse(args.disable_xformers)

    def test_the_allocator_variable_is_not_set_once_torch_is_loaded(self) -> None:
        # It is read when torch first initialises. Setting it afterwards is
        # inert, and reporting it as set would be a lie in the one place a
        # benchmark reads.
        import os

        settings = resolve({"cuda_malloc": True}, _capabilities(**_EVERYTHING))
        before = os.environ.get("PYTORCH_CUDA_ALLOC_CONF")
        loaded = sys.modules.get("torch")
        sys.modules["torch"] = types.ModuleType("torch")  # pretend it is in
        try:
            self.assertEqual({}, apply_environment(settings))
        finally:
            if loaded is None:
                sys.modules.pop("torch", None)
            else:
                sys.modules["torch"] = loaded
        self.assertEqual(before, os.environ.get("PYTORCH_CUDA_ALLOC_CONF"),
                         "the test changed the process environment")

    def test_the_allocator_variable_is_set_while_there_is_still_time(self) -> None:
        import os

        settings = resolve({"cuda_malloc": True}, _capabilities(**_EVERYTHING))
        before = os.environ.get("PYTORCH_CUDA_ALLOC_CONF")
        loaded = sys.modules.pop("torch", None)
        try:
            changed = apply_environment(settings)
            self.assertIn("PYTORCH_CUDA_ALLOC_CONF", changed)
            self.assertIn("cudaMallocAsync", changed["PYTORCH_CUDA_ALLOC_CONF"])
        finally:
            if loaded is not None:
                sys.modules["torch"] = loaded
            if before is None:
                os.environ.pop("PYTORCH_CUDA_ALLOC_CONF", None)
            else:
                os.environ["PYTORCH_CUDA_ALLOC_CONF"] = before


class LauncherFlagTests(unittest.TestCase):
    """Where an owner sets this, if they ever need to: `Start-Studio.bat`.

    NOT a config file. Forge's answer is a `COMMANDLINE_ARGS` line in
    `webui-user.bat`; Studio is standalone and has its own launcher, so the
    equivalent line lives there and uses FORGE'S OWN FLAG NAMES -- a line
    copied out of `webui-user.bat` must work unchanged.

    An owner who never opens that file gets Studio's own decision, which is
    the normal path and the one section 76 requires.
    """

    LAUNCHER = (APP_ROOT.parent / "Start-Studio.bat").read_text(encoding="utf-8")
    LAUNCH_PY = (APP_ROOT / "forge_studio" / "launch.py").read_text(
        encoding="utf-8")

    def _runtime(self, line: str) -> dict:
        from forge_studio import launch

        seen: dict = {}
        original = launch.run
        launch.run = lambda config_path, runtime=None, timeline=False: (
            seen.setdefault("runtime", runtime),
            seen.setdefault("timeline", timeline),
        ) and 0
        try:
            launch.main(line.split() + ["--config", "x.json"])
        finally:
            launch.run = original
        return seen.get("runtime") or {}

    def test_the_launcher_offers_a_place_to_put_them(self) -> None:
        self.assertIn("STUDIO_ARGS", self.LAUNCHER)
        self.assertIn("%STUDIO_ARGS%", self.LAUNCHER)

    def test_the_launcher_declares_the_args_line_exactly_once(self) -> None:
        """The MECHANISM is the contract; the value is the owner's.

        This deliberately does not assert the line is empty. Studio ships it
        empty -- the normal experience is Studio deciding -- but this reads
        the owner's live launcher, and they may have put `--timeline` or an
        accelerator flag in it for a measurement. A test that failed because
        the owner used the escape hatch as designed would be a test telling
        them not to.
        """

        declarations = [line.strip() for line in self.LAUNCHER.splitlines()
                        if line.strip().startswith("set STUDIO_ARGS")]
        self.assertEqual(1, len(declarations))
        self.assertIn("%STUDIO_ARGS%", self.LAUNCHER)

    def test_no_flags_means_studio_decides(self) -> None:
        self.assertEqual({}, self._runtime(""))

    def test_a_webui_user_line_pastes_in_unchanged(self) -> None:
        # Verbatim from the owner's own webui-user.bat, including --uv, which
        # Studio has no use for and must not choke on.
        request = self._runtime(
            "--xformers --sage --uv --pin-shared-memory --cuda-malloc "
            "--cuda-stream --fast-fp16")
        self.assertEqual("sage", request["attention"])
        self.assertTrue(request["cuda_malloc"])
        self.assertTrue(request["pin_shared_memory"])
        self.assertTrue(request["fast_fp16"])
        self.assertEqual(2, request["cuda_stream"],
                         "a bare --cuda-stream means 2, as backend/args.py:94 "
                         "declares it")

    def test_a_flag_can_be_turned_off_as_well_as_on(self) -> None:
        self.assertFalse(self._runtime("--no-pin-shared-memory")
                         ["pin_shared_memory"])

    def test_compatibility_is_reachable_for_diagnosis(self) -> None:
        self.assertEqual({"mode": "compatibility"},
                         self._runtime("--compatibility"))

    def test_an_unrecognised_flag_does_not_stop_studio_booting(self) -> None:
        # A pasted line may carry something only Forge understands. Losing a
        # generation session over an unknown word is worse than ignoring it.
        self.assertEqual({}, self._runtime("--some-forge-only-flag"))

    def test_the_timeline_is_off_unless_asked_for(self) -> None:
        """Per-generation timings are for answering a question, not for every
        run. The handoff requires the normal log stay concise."""

        from forge_studio import launch

        seen: dict = {}
        original = launch.run
        launch.run = lambda config_path, runtime=None, timeline=False: (
            seen.setdefault("timeline", timeline),) and 0
        try:
            launch.main(["--config", "x.json"])
            self.assertFalse(seen["timeline"])
            seen.clear()
            launch.main(["--timeline", "--config", "x.json"])
            self.assertTrue(seen["timeline"])
        finally:
            launch.run = original

    def test_the_timeline_raises_one_logger_not_the_root(self) -> None:
        # Raising the root would turn on every inherited library's debug
        # output, which is not what anyone asked for.
        import logging

        from forge_studio import launch

        root_before = logging.getLogger().level
        launch._configure_logging(timeline=True)
        try:
            self.assertEqual(
                logging.DEBUG,
                logging.getLogger("studio.generation").level)
            self.assertNotEqual(logging.DEBUG, logging.getLogger().level)
        finally:
            logging.getLogger("studio.generation").setLevel(logging.NOTSET)
            logging.getLogger().setLevel(root_before)

    def test_studio_does_not_install_packages_for_sage(self) -> None:
        # In Forge --sage MEANS "pip install sageattention"
        # (backend/args.py:67). Studio selects and reports instead.
        # The AST, not the text. This module DOCUMENTS the divergence in a
        # comment, and a raw search would trip over the sentence explaining
        # the rule -- which OPERATIONS.md names as a mistake made three times
        # already.
        import ast

        tree = ast.parse(self.LAUNCH_PY)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in getattr(node, "names", [])
        }
        imported |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("subprocess", imported,
                         "the launcher can reach a package installer")
        self.assertNotIn("pip", imported)
        self.assertIn("do NOT install", self.LAUNCHER)


class AutotuneTests(unittest.TestCase):
    """cuDNN algorithm search: the one in-tree lever aimed at the UNet.

    The measured job is ~70% UNet forward passes, and every other portable
    exact candidate totals under a second. This is the only remaining broad
    compute lever already in the tree.

    It is NOT bit-identical. `torch.backends.cudnn.benchmark` lets cuDNN pick
    a different convolution algorithm per shape -- the same mathematics with a
    different reduction order, so results can differ in the last ULP. So it
    gets `fast_fp16` treatment: off under AUTO, reachable only when an owner
    asks.
    """

    def test_auto_leaves_it_off(self) -> None:
        self.assertFalse(resolve(None, _capabilities(**_EVERYTHING)).autotune)

    def test_an_owner_who_asks_gets_it(self) -> None:
        self.assertTrue(
            resolve({"autotune": True}, _capabilities(**_EVERYTHING)).autotune)

    def test_a_machine_without_cuda_refuses_it_out_loud(self) -> None:
        settings = resolve({"autotune": True}, _capabilities(**_BARE))
        self.assertFalse(settings.autotune)
        self.assertIn("autotune", settings.refused)

    def test_it_reaches_the_inherited_namespace(self) -> None:
        args = types.SimpleNamespace(
            cuda_malloc=False, pin_shared_memory=False, fast_fp16=False,
            cuda_stream=None, autotune=False,
            use_pytorch_cross_attention=False, disable_sage=False,
            disable_flash=False, disable_xformers=False)
        project(resolve({"autotune": True}, _capabilities(**_EVERYTHING)), args)
        self.assertTrue(args.autotune)

    def test_it_is_projected_before_the_module_that_reads_it(self) -> None:
        """THE failure mode section 19 asks for a test against.

        `backend/memory_management.py:338` reads `args.autotune` at MODULE
        SCOPE. Written after that import, the flag is inert and silent -- the
        log would say requested, torch would say off, and a benchmark would
        measure nothing while appearing configured.

        Checked on the bootstrap's source order, because the ordering is the
        contract and it cannot be observed without a GPU.
        """

        import ast

        source = (APP_ROOT / "forge_headless" / "backend_bootstrap.py").read_text(
            encoding="utf-8")
        project_at = source.index("project(runtime, _backend_args)")
        # `process_options` is what pulls in the inherited modules, and
        # `memory_management` arrives with them.
        options_at = source.index("process_options(root")
        self.assertLess(project_at, options_at,
                        "the settings are projected AFTER the backend imports, "
                        "so memory_management.py:338 has already read a stale "
                        "args.autotune")

    def test_effective_is_read_back_not_assumed(self) -> None:
        # A request is not an outcome: memory_management.py:338 gates on
        # torch.cuda.is_available() and cudnn.is_available(), so a machine
        # without cuDNN asks and correctly gets nothing.
        from forge_headless.runtime_options import effective

        report = effective(resolve({"autotune": True},
                                   _capabilities(**_EVERYTHING)))
        self.assertTrue(report["autotune_requested"])
        self.assertIn("autotune_effective", report)


class GpuTileCompositeTests(unittest.TestCase):
    """Blending upscaler tiles on the device instead of through PIL.

    MEASURED, isolated, on a real 1024x1024 upscale with remacri at tile 256
    overlap 16 (5 runs each, medians):

        CPU composite   6.744 s
        GPU composite   4.587 s      -2.157 s, 32% faster

    NOT bit-identical, and the reason is not the seams. The CPU path rounds
    every tile to 8-bit PIL BEFORE compositing and pastes with an alpha mask;
    the GPU path accumulates a weighted mean in float. So the difference is
    +/-1 LSB rounding spread uniformly -- measured concentration in the
    overlap bands was 1.05x on real content and 0.83x on a seam-hostile
    synthetic, i.e. no seam artefact at all. Max error 2/255 on real content,
    with ZERO pixels above 2.

    ON by default since the working set became preflighted. Asking for it is
    a preference, not a force: `modules/esrgan_model.py` estimates what the
    frame costs and takes the CPU path when the card cannot afford it, so the
    default can no longer put anyone into an allocation that will not fit.
    Changed is still changed, so the owner keeps a way to refuse.
    """

    def test_it_is_on_by_default(self) -> None:
        self.assertTrue(
            resolve(None, _capabilities(**_EVERYTHING)).composite_tiles_on_gpu)

    def test_an_owner_who_refuses_gets_the_host_path(self) -> None:
        # The reason the projection below had to stop being one-sided: with
        # the default on, refusing is the case that needs to travel.
        self.assertFalse(
            resolve({"composite_tiles_on_gpu": False},
                    _capabilities(**_EVERYTHING)).composite_tiles_on_gpu)

    def test_an_owner_who_asks_gets_it(self) -> None:
        self.assertTrue(
            resolve({"composite_tiles_on_gpu": True},
                    _capabilities(**_EVERYTHING)).composite_tiles_on_gpu)

    def test_it_is_not_gated_on_cuda(self) -> None:
        # The tile loop runs wherever torch runs. This is portable in a way
        # the allocator and cuDNN flags are not, so a machine without CUDA
        # must still be able to use it.
        self.assertTrue(
            resolve({"composite_tiles_on_gpu": True},
                    _capabilities(**_BARE)).composite_tiles_on_gpu)

    def test_it_reaches_the_backend_as_an_OPTION_not_an_arg(self) -> None:
        """`upscaler_utils.py:277` branches on `shared.opts`, not on args.

        Projecting it onto `backend.args` would write an attribute nothing
        reads -- configured-looking and inert, which is the failure this
        whole runtime layer exists to prevent.
        """

        source = (APP_ROOT / "forge_headless" / "backend_bootstrap.py").read_text(
            encoding="utf-8")
        self.assertIn(
            'overrides["composite_tiles_on_gpu"] = runtime.composite_tiles_on_gpu',
            source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_RUNTIME_OPTIONS_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
