"""Phase 2B retry — Gradio-free options bootstrap, telemetry, and hardening.

Synthetic fixtures only. **No test here reads the three authorized model files.**

The bootstrap tests deliberately assert against Forge's own source rather than
against a second copy of its defaults: a duplicated default would drift, and the
whole point of parsing `shared_options.py` is that it cannot.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

WORKSPACE_ROOT = APP_ROOT.parent
FIXTURE_PARENT = WORKSPACE_ROOT / "Evidence" / "studio-controlled-model-load"
PROBE = APP_ROOT / "scripts" / "headless" / "controlled_load_probe.py"

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.headless_options import (  # noqa: E402
    GENERATION_ONLY_OPTIONS,
    INFERENCE_OPTIONS,
    HeadlessOptions,
    headless_options,
    legacy_defaults,
    parse_legacy_defaults,
)
from forge_headless.import_graph import (  # noqa: E402
    module_level_imports,
    paths_to_forbidden,
)
from forge_headless.load_authorization import (  # noqa: E402
    ControlledLoadAuthorization,
)
from forge_headless.load_telemetry import (  # noqa: E402
    NOT_REACHED,
    STAGES,
    PayloadWatch,
    StageTimer,
    VramSample,
    VramTelemetry,
)

from test_controlled_model_load import (  # noqa: E402
    anima_checkpoint_tensors,
    build_safetensors,
    qwen3_tensors,
    wan_vae_tensors,
)


EXPECTED_RETRY_TESTS = 32

SHARED_OPTIONS = APP_ROOT / "modules" / "shared_options.py"


def _calls_to(tree: ast.AST, owner: str, method: str) -> list[int]:
    """Line numbers of real `owner.method(...)` calls, ignoring prose."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != method:
            continue
        base = func.value
        name = getattr(base, "id", None) or getattr(base, "attr", None)
        if name == owner:
            found.append(node.lineno)
    return found


def _imports_of(tree: ast.AST, module: str) -> list[int]:
    """Line numbers of any import of `module`, at any scope."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    found.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module == module:
                found.append(node.lineno)
            elif node.module in ("modules", None):
                for alias in node.names:
                    if alias.name == module.rsplit(".", 1)[-1]:
                        found.append(node.lineno)
    return found


@contextmanager
def neutral_argv():
    """Import `modules.shared` safely, and leave no trace of having done so.

    Two hazards, both learned the hard way. Importing `modules.shared` parses
    `sys.argv` through Forge's cmd_args, and under a test runner that argv is
    unittest's, so argparse exits. And leaving `modules` in `sys.modules`
    breaks the standing guarantee that importing Studio pulls in no `modules` --
    which pre-existing tests check, and which this suite broke twice before.
    """
    saved_argv = sys.argv[:]
    snapshot = dict(sys.modules)
    sys.argv = [saved_argv[0] if saved_argv else "test"]
    try:
        yield
    finally:
        sys.argv = saved_argv
        for name in [n for n in sys.modules if n not in snapshot]:
            del sys.modules[name]
        sys.modules.update(snapshot)


@contextmanager
def stub_safetensors():
    """Provide a minimal `safetensors` module for the duration of a test.

    The canonical runner uses `-S`, so the real package is not importable there.
    What these tests exercise is the wrapper -- role attribution, single-attempt
    consumption, restoration -- not safetensors itself, so a stub keeps them
    running everywhere instead of skipping on one runner.
    """
    import types

    saved = sys.modules.get("safetensors")
    module = types.ModuleType("safetensors")

    def safe_open(filename, *args, **kwargs):  # noqa: ARG001
        class _Handle:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Handle()

    module.safe_open = safe_open  # type: ignore[attr-defined]
    sys.modules["safetensors"] = module
    try:
        yield module
    finally:
        if saved is not None:
            sys.modules["safetensors"] = saved
        else:
            sys.modules.pop("safetensors", None)


def build_openable_safetensors(path: Path) -> None:
    """A safetensors file `safe_open` will actually accept.

    The header-only fixtures elsewhere declare zero-length tensors, which the
    Rust reader rejects on a real open. This one carries a single 4-byte F32
    tensor with matching offsets.
    """
    header = json.dumps(
        {
            "t": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]},
            "__metadata__": {"format": "pt"},
        }
    ).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header)) + header + bytes(4))


class _Fixtures(unittest.TestCase):
    def setUp(self) -> None:
        FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fxr-"))
        self.checkpoint = self.root / "syn-ckpt.safetensors"
        self.text_encoder = self.root / "syn-enc.safetensors"
        self.vae = self.root / "syn-vae.safetensors"
        build_safetensors(self.checkpoint, anima_checkpoint_tensors())
        build_safetensors(self.text_encoder, qwen3_tensors())
        build_safetensors(self.vae, wan_vae_tensors())

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def authorization(self) -> ControlledLoadAuthorization:
        return ControlledLoadAuthorization(
            {
                "checkpoint": str(self.checkpoint),
                "text_encoder": str(self.text_encoder),
                "vae": str(self.vae),
            }
        )


# ------------------------------------------------------- options bootstrap


class OptionsBootstrapTests(unittest.TestCase):
    def test_defaults_come_from_forge_source_not_a_copy(self) -> None:
        parsed = parse_legacy_defaults(SHARED_OPTIONS)
        self.assertGreater(len(parsed), 200)
        # The value this whole boundary exists for, and the literal that defines
        # it in Forge's own file.
        self.assertEqual(parsed["emphasis"], "Original")
        source = SHARED_OPTIONS.read_text(encoding="utf-8")
        self.assertIn('"emphasis": OptionInfo("Original"', source)

    def test_chained_option_declarations_are_unwrapped(self) -> None:
        # `OptionInfo(...).info(...).html(...)` -- the outermost call is `.html`,
        # so a naive walk misses exactly the options that carry documentation.
        parsed = parse_legacy_defaults(SHARED_OPTIONS)
        for name, expected in (
            ("emphasis", "Original"),
            ("anima_do_reference", False),
            ("res_step", 64),
        ):
            self.assertEqual(parsed[name], expected, name)

    def test_inference_option_inventory_is_explicit(self) -> None:
        self.assertEqual(INFERENCE_OPTIONS, ("emphasis",))
        self.assertEqual(GENERATION_ONLY_OPTIONS, ("anima_do_reference",))
        for name in INFERENCE_OPTIONS + GENERATION_ONLY_OPTIONS:
            self.assertIn(name, parse_legacy_defaults(SHARED_OPTIONS))

    def test_inventory_records_reads_with_their_source(self) -> None:
        options = HeadlessOptions({"emphasis": "Original", "other": 1})
        self.assertEqual(options.emphasis, "Original")
        rows = options.inventory()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["option"], "emphasis")
        self.assertEqual(rows[0]["type"], "str")
        self.assertEqual(rows[0]["default_source"], "modules/shared_options.py")
        self.assertTrue(rows[0]["read_before_model_ready"])

    def test_unknown_option_is_a_named_error_not_a_silent_none(self) -> None:
        options = HeadlessOptions({"emphasis": "Original"})
        with self.assertRaises(HeadlessError) as caught:
            _ = options.definitely_not_an_option
        self.assertEqual(caught.exception.code, "HEADLESS_OPTION_NOT_AVAILABLE")
        self.assertIn("definitely_not_an_option", options.missing)

    def test_overrides_take_precedence_and_are_reported(self) -> None:
        options = HeadlessOptions(
            {"emphasis": "Original"}, overrides={"emphasis": "None"}
        )
        self.assertEqual(options.emphasis, "None")
        self.assertEqual(options.inventory()[0]["default_source"], "override")

    def test_bootstrap_is_idempotent_and_deterministic(self) -> None:
        first = legacy_defaults(APP_ROOT)
        second = legacy_defaults(APP_ROOT)
        self.assertEqual(first, second)
        self.assertIsNot(first, second, "callers must not share the cache dict")

    def test_bridge_restores_previous_state_on_success(self) -> None:
        with neutral_argv():
            from modules import shared

            before = shared.opts
            with headless_options(APP_ROOT) as options:
                self.assertIs(shared.opts, options)
            self.assertIs(shared.opts, before)

    def test_bridge_restores_previous_state_on_exception(self) -> None:
        class Boom(Exception):
            pass

        with neutral_argv():
            from modules import shared

            before = shared.opts
            with self.assertRaises(Boom):
                with headless_options(APP_ROOT):
                    self.assertIsInstance(shared.opts, HeadlessOptions)
                    raise Boom
            self.assertIs(shared.opts, before)

    def test_bridge_composes_with_an_existing_options_object(self) -> None:
        with neutral_argv():
            from modules import shared

            sentinel = object()
            before = shared.opts
            shared.opts = sentinel
            try:
                with headless_options(APP_ROOT):
                    self.assertIsInstance(shared.opts, HeadlessOptions)
                self.assertIs(shared.opts, sentinel)
            finally:
                shared.opts = before

    def test_module_imports_no_gradio_torch_or_shared_options(self) -> None:
        imports = module_level_imports(
            ast.parse(
                (APP_ROOT / "forge_headless" / "headless_options.py").read_text(
                    encoding="utf-8"
                )
            )
        )
        for forbidden in ("gradio", "gradio_client", "torch", "modules.shared_options"):
            self.assertNotIn(forbidden, imports)
        self.assertEqual(
            paths_to_forbidden(
                "forge_headless.headless_options",
                APP_ROOT,
                ("gradio", "gradio_client", "torch"),
            ),
            [],
        )

    def test_shared_init_is_never_called_from_the_headless_path(self) -> None:
        # AST, not a substring scan: both files *discuss* shared_init in prose,
        # and a text search would match the explanation of why it is avoided.
        for path in (
            APP_ROOT / "scripts" / "headless" / "controlled_load_probe.py",
            APP_ROOT / "forge_headless" / "headless_options.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(module=path.name):
                self.assertEqual([], _calls_to(tree, "shared_init", "initialize"))
                self.assertEqual([], _imports_of(tree, "modules.shared_options"))
                self.assertEqual([], _imports_of(tree, "shared_options"))


# ------------------------------------------------------------- path safety


class LoaderPathHardeningTests(_Fixtures):
    def test_loader_path_is_str_not_path(self) -> None:
        auth = self.authorization()
        value = auth.loader_path("checkpoint")
        self.assertIsInstance(value, str)
        self.assertNotIsInstance(value, Path)

    def test_path_lower_regression_cannot_recur(self) -> None:
        # The first Phase 2B attempt died on `Path.lower()` inside
        # backend/utils.py::load_torch_file before a byte was read.
        auth = self.authorization()
        for role in ("checkpoint", "text_encoder", "vae"):
            value = auth.loader_path(role)
            self.assertTrue(
                hasattr(value, "lower"),
                f"{role} path must support .lower() the way load_torch_file needs",
            )
            self.assertTrue(value.lower().endswith(".safetensors"))
        self.assertTrue(
            all(isinstance(item, str) for item in auth.loader_paths("text_encoder", "vae"))
        )

    def test_probe_hands_the_loader_only_normalized_paths(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('authorization.loader_path("checkpoint")', source)
        self.assertIn('authorization.loader_paths(', source)
        self.assertNotIn('authorization.file_for("checkpoint").path,', source)

    def test_studio_public_contract_cannot_supply_a_path(self) -> None:
        for path in sorted((APP_ROOT / "forge_studio").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("loader_path", source, path.name)


# --------------------------------------------------------------- telemetry


class TelemetryTests(unittest.TestCase):
    def test_unreached_stages_report_not_reached_not_zero(self) -> None:
        timer = StageTimer()
        timer.start("authorization_validation")
        timer.stop("authorization_validation")
        rendered = timer.to_dict()
        self.assertIsInstance(rendered["authorization_validation"], float)
        self.assertEqual(rendered["engine_construction"], NOT_REACHED)
        self.assertEqual(rendered["cuda_initialization"], NOT_REACHED)
        self.assertIsInstance(rendered["total_worker_runtime"], float)
        self.assertEqual(set(rendered), set(STAGES))

    def test_telemetry_renders_every_sample_slot(self) -> None:
        telemetry = VramTelemetry(ceiling_bytes=14 * 1024**3)
        rendered = telemetry.to_dict()
        for key in (
            "before_load",
            "peak",
            "after_release_before_cache_clear",
            "after_cache_clear",
        ):
            self.assertEqual(rendered[key], NOT_REACHED)
        self.assertEqual(rendered["ceiling_exceeded"], NOT_REACHED)

    def test_ceiling_breach_is_detectable_from_peaks(self) -> None:
        ceiling = 14 * 1024**3
        telemetry = VramTelemetry(ceiling_bytes=ceiling)
        telemetry.peak = VramSample(allocated=ceiling + 1, reserved=0)
        telemetry.ceiling_exceeded = (
            telemetry.peak.allocated > ceiling or telemetry.peak.reserved > ceiling
        )
        self.assertTrue(telemetry.to_dict()["ceiling_exceeded"])

    def test_telemetry_is_collected_in_terminal_handling(self) -> None:
        # Structural: the samples must be taken in `finally`, not on the success
        # path -- that is exactly what the first attempt got wrong.
        source = PROBE.read_text(encoding="utf-8")
        finally_index = source.index("    finally:\n        # Guaranteed terminal")
        for marker in (
            "telemetry.peak = sample_peak()",
            "telemetry.after_release = sample_current()",
            "telemetry.after_cache_clear = sample_current()",
            'record["stage_timings"] = timer.to_dict()',
        ):
            self.assertGreater(source.index(marker), finally_index, marker)

    def test_peaks_are_reset_immediately_before_payload_access(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertLess(source.index("reset_peak()"), source.index("watch.install()"))
        self.assertLess(
            source.index("telemetry.before_load = sample_current()"),
            source.index("reset_peak()"),
        )

    def test_ceiling_is_applied_before_payload_access(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertLess(
            source.index("device = initialize_cuda(authorization)"),
            source.index("watch.install()"),
        )


# ------------------------------------------------------- payload boundary


class PayloadBoundaryTests(_Fixtures):
    def watch(self, auth: ControlledLoadAuthorization) -> PayloadWatch:
        return PayloadWatch(
            {auth.loader_path(role): role for role in ("checkpoint", "text_encoder", "vae")},
            StageTimer(),
        )

    def test_roles_are_attributed_per_file(self) -> None:
        auth = self.authorization()
        watch = self.watch(auth)
        self.assertEqual(watch.role_for(auth.loader_path("vae")), "vae")
        self.assertEqual(
            watch.role_for(str(self.root / "someone-elses.safetensors")),
            "unauthorized",
        )

    def test_first_payload_open_consumes_the_attempt(self) -> None:
        auth = self.authorization()
        watch = self.watch(auth)
        consumed: list[bool] = []

        def on_first() -> None:
            auth.consume()
            consumed.append(True)

        watch.on_first_open = on_first
        with stub_safetensors() as stub:
            watch.install()
            try:
                stub.safe_open(auth.loader_path("checkpoint"), framework="pt")
                stub.safe_open(auth.loader_path("vae"), framework="pt")
            finally:
                watch.restore()
        self.assertTrue(consumed)
        self.assertTrue(auth.consumed)
        rendered = watch.to_dict()
        self.assertTrue(rendered["checkpoint_payload_opened"])
        self.assertTrue(rendered["vae_payload_opened"])
        self.assertFalse(rendered["text_encoder_payload_opened"])
        self.assertFalse(rendered["unauthorized_payload_opened"])
        self.assertTrue(rendered["tensor_boundary_crossed"])

    def test_no_retry_after_tensor_access(self) -> None:
        auth = self.authorization()
        auth.consume()
        with self.assertRaises(HeadlessError) as caught:
            auth.consume()
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_ALREADY_ATTEMPTED")

    def test_per_role_open_detection_is_reported(self) -> None:
        auth = self.authorization()
        watch = self.watch(auth)
        rendered = watch.to_dict()
        self.assertFalse(rendered["tensor_boundary_crossed"])
        for key in (
            "checkpoint_payload_opened",
            "text_encoder_payload_opened",
            "vae_payload_opened",
            "unauthorized_payload_opened",
        ):
            self.assertFalse(rendered[key])

    def test_watch_restores_safe_open(self) -> None:
        with stub_safetensors() as stub:
            original = stub.safe_open
            watch = self.watch(self.authorization())
            watch.install()
            self.assertIsNot(stub.safe_open, original)
            watch.restore()
            self.assertIs(stub.safe_open, original)


# ------------------------------------------------------------ probe shape


class ProbeShapeTests(unittest.TestCase):
    def test_probe_still_generates_nothing(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        for forbidden in (
            "submit_generation",
            "process_images",
            "decode_first_stage",
            "save_image",
            "get_learned_conditioning",
        ):
            self.assertNotIn(forbidden, source)

    def test_probe_preserves_vendored_package_setup(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('APP_ROOT / "modules_forge" / "packages"', source)
        self.assertNotIn("pip install", source)

    def test_probe_reconfirms_preflight_before_loading(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertLess(
            source.index("_reconfirm_preflight(authorization)"),
            source.index("from backend.loader import forge_loader"),
        )

    def test_worker_cleanup_and_options_restoration_are_reported(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        for key in (
            '"options_restored"',
            '"cleanup"',
            '"payload_access"',
            '"options_inventory"',
        ):
            self.assertIn(key, source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_retry_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_RETRY_TESTS,
            "Retry test count changed: update EXPECTED_RETRY_TESTS deliberately, "
            "or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
