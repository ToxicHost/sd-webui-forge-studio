"""Headless Forge Phase 2B — authorization, intake, preflight, and cleanup.

Every fixture here is a synthetic safetensors container built in-test: an 8-byte
little-endian header length, a JSON header, and a payload of zero bytes. They
carry real key names and shapes so the preflight rules are genuinely exercised,
and no tensor is ever materialized from them.

**None of these tests touch the three owner-authorized model files.** The paths
they use are all inside the workspace fixture directory.
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
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

TEST_ROOT = Path(__file__).resolve().parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

WORKSPACE_ROOT = APP_ROOT.parent
FIXTURE_PARENT = WORKSPACE_ROOT / "Evidence" / "studio-controlled-model-load"

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.load_authorization import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_VRAM_CEILING_BYTES,
    ControlledLoadAuthorization,
    opaque_file_id,
)
from forge_headless.model_intake import (  # noqa: E402
    MAX_HEADER_BYTES,
    PREFLIGHT_COMPATIBLE,
    PREFLIGHT_INCOMPATIBLE,
    PREFLIGHT_UNSUPPORTED,
    read_safetensors_header,
    summarize_header,
    validate_exact_path,
)
from forge_headless.triplet_preflight import (  # noqa: E402
    evaluate_triplet,
    inspect_checkpoint,
    inspect_text_encoder,
    inspect_vae,
    unet_prefix,
)


#: Asserted against the discovered count so a loader failure cannot hide the suite.
EXPECTED_PHASE2B_TESTS = 42


def build_safetensors(path: Path, tensors: dict[str, tuple[list[int], str]]) -> None:
    """Write a syntactically valid safetensors file with a zero-byte payload."""
    header: dict[str, object] = {}
    offset = 0
    for name, (shape, dtype) in tensors.items():
        header[name] = {
            "dtype": dtype,
            "shape": shape,
            "data_offsets": [offset, offset],
        }
    header["__metadata__"] = {"format": "pt"}
    payload = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(payload)) + payload)


def anima_checkpoint_tensors(
    *, prefix: str = "net.", model_channels: int = 2048, in_width: int = 68
) -> dict[str, tuple[list[int], str]]:
    tensors = {
        f"{prefix}blocks.0.mlp.layer1.weight": ([8192, model_channels], "BF16"),
        f"{prefix}llm_adapter.blocks.0.cross_attn.q_proj.weight": ([1024, 1024], "BF16"),
        f"{prefix}x_embedder.proj.1.weight": ([model_channels, in_width], "BF16"),
    }
    for index in range(1, 8):
        tensors[f"{prefix}blocks.{index}.mlp.layer1.weight"] = ([8192, model_channels], "BF16")
    return tensors


def qwen3_tensors(hidden: int = 1024) -> dict[str, tuple[list[int], str]]:
    return {
        "model.layers.0.post_attention_layernorm.weight": ([hidden], "BF16"),
        "model.layers.0.self_attn.q_norm.weight": ([128], "BF16"),
        "model.embed_tokens.weight": ([151936, hidden], "BF16"),
    }


def wan_vae_tensors(latent: int = 16) -> dict[str, tuple[list[int], str]]:
    return {
        "decoder.middle.0.residual.0.gamma": ([384, 1, 1, 1], "BF16"),
        "decoder.conv1.weight": ([384, latent, 3, 3, 3], "BF16"),
        "encoder.downsamples.0.residual.0.gamma": ([96, 1, 1, 1], "BF16"),
    }


class _FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=FIXTURE_PARENT, prefix="fx2b-"))
        self.checkpoint = self.root / "synthetic-checkpoint.safetensors"
        self.text_encoder = self.root / "synthetic-encoder.safetensors"
        self.vae = self.root / "synthetic-vae.safetensors"
        build_safetensors(self.checkpoint, anima_checkpoint_tensors())
        build_safetensors(self.text_encoder, qwen3_tensors())
        build_safetensors(self.vae, wan_vae_tensors())

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def authorization(self, **overrides: object) -> ControlledLoadAuthorization:
        files = {
            "checkpoint": str(self.checkpoint),
            "text_encoder": str(self.text_encoder),
            "vae": str(self.vae),
        }
        files.update(overrides.pop("files", {}))  # type: ignore[arg-type]
        return ControlledLoadAuthorization(files, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------- authorization


class AuthorizationTests(_FixtureCase):
    def test_exact_paths_are_bound_to_roles(self) -> None:
        auth = self.authorization()
        self.assertEqual(auth.role_for(self.checkpoint), "checkpoint")
        self.assertEqual(auth.role_for(self.text_encoder), "text_encoder")
        self.assertEqual(auth.role_for(self.vae), "vae")

    def test_sibling_in_the_same_directory_is_refused(self) -> None:
        sibling = self.root / "sibling.safetensors"
        build_safetensors(sibling, wan_vae_tensors())
        with self.assertRaises(HeadlessError) as caught:
            self.authorization().role_for(sibling)
        self.assertEqual(
            caught.exception.code, "CONTROLLED_LOAD_PATH_NOT_AUTHORIZED"
        )

    def test_basename_alone_never_matches(self) -> None:
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        impostor = elsewhere / self.checkpoint.name
        build_safetensors(impostor, anima_checkpoint_tensors())
        with self.assertRaises(HeadlessError):
            self.authorization().role_for(impostor)

    def test_authorization_is_single_use(self) -> None:
        auth = self.authorization()
        self.assertFalse(auth.consumed)
        auth.consume()
        self.assertTrue(auth.consumed)
        with self.assertRaises(HeadlessError) as caught:
            auth.consume()
        self.assertEqual(
            caught.exception.code, "CONTROLLED_LOAD_ALREADY_ATTEMPTED"
        )

    def test_incomplete_or_unknown_roles_are_refused(self) -> None:
        # A checkpoint ALONE is complete: an SDXL file carries its own text
        # encoder and VAE, and authorizing a file that does not exist is not a
        # safety property -- it is a refusal to load a model Forge supports.
        authorized = ControlledLoadAuthorization({"checkpoint": str(self.checkpoint)})
        self.assertTrue(authorized.has_role("checkpoint"))
        self.assertFalse(authorized.has_role("text_encoder"))

        with self.assertRaises(HeadlessError) as missing:
            ControlledLoadAuthorization({"text_encoder": str(self.text_encoder)})
        self.assertEqual(
            missing.exception.code, "CONTROLLED_LOAD_AUTHORIZATION_INCOMPLETE"
        )
        with self.assertRaises(HeadlessError) as unknown:
            ControlledLoadAuthorization(
                {
                    "checkpoint": str(self.checkpoint),
                    "text_encoder": str(self.text_encoder),
                    "vae": str(self.vae),
                    "lora": str(self.vae),
                }
            )
        self.assertEqual(
            unknown.exception.code, "CONTROLLED_LOAD_AUTHORIZATION_UNKNOWN_ROLE"
        )

    def test_limits_cannot_be_widened(self) -> None:
        with self.assertRaises(HeadlessError):
            self.authorization(timeout_seconds=DEFAULT_TIMEOUT_SECONDS + 1)
        with self.assertRaises(HeadlessError):
            self.authorization(vram_ceiling_bytes=DEFAULT_VRAM_CEILING_BYTES + 1)
        tightened = self.authorization(timeout_seconds=60, vram_ceiling_bytes=1024)
        self.assertEqual(tightened.timeout_seconds, 60)

    def test_public_view_carries_no_path_or_name(self) -> None:
        auth = self.authorization()
        rendered = json.dumps(auth.to_public_dict())
        for needle in (str(self.root), self.checkpoint.name, Path.home().name):
            self.assertNotIn(needle, rendered)
        self.assertIn("checkpoint", rendered)
        self.assertEqual(len(auth.to_public_dict()["files"]), 3)  # type: ignore[arg-type]

    def test_file_ids_are_stable_and_opaque(self) -> None:
        first = self.authorization().file_for("vae").file_id
        second = self.authorization().file_for("vae").file_id
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertNotIn("synthetic", first)
        self.assertNotEqual(
            first, opaque_file_id("checkpoint", self.vae), "role must scope the id"
        )

    def test_authorization_does_not_persist(self) -> None:
        first = self.authorization()
        first.consume()
        second = self.authorization()
        self.assertFalse(second.consumed)
        self.assertNotEqual(first.authorization_id, second.authorization_id)

    def test_no_public_bypass_exists(self) -> None:
        """Nothing reachable from the HTTP surface can build an authorization."""
        for path in sorted((APP_ROOT / "forge_studio").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("ControlledLoadAuthorization", source, path.name)
            self.assertNotIn("load_authorization", source, path.name)


# ---------------------------------------------------------------------- intake


class IntakeTests(_FixtureCase):
    def test_regular_file_is_accepted_and_summarized(self) -> None:
        record = validate_exact_path(self.authorization(), "checkpoint")
        self.assertEqual(record.role, "checkpoint")
        self.assertEqual(record.suffix, ".safetensors")
        self.assertGreater(record.size_bytes, 0)
        self.assertNotIn("synthetic", record.file_id)

    def test_missing_file_is_reported_not_crashed(self) -> None:
        self.checkpoint.unlink()
        with self.assertRaises(HeadlessError) as caught:
            validate_exact_path(self.authorization(), "checkpoint")
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_FILE_MISSING")

    def test_directory_is_refused(self) -> None:
        target = self.root / "a-directory.safetensors"
        target.mkdir()
        auth = ControlledLoadAuthorization(
            {
                "checkpoint": str(target),
                "text_encoder": str(self.text_encoder),
                "vae": str(self.vae),
            }
        )
        with self.assertRaises(HeadlessError) as caught:
            validate_exact_path(auth, "checkpoint")
        self.assertEqual(
            caught.exception.code, "CONTROLLED_LOAD_NOT_A_REGULAR_FILE"
        )

    def test_link_guard_refuses_on_every_platform(self) -> None:
        # Exercises the guard itself, so the property holds where symlink
        # creation needs a privilege this host does not have.
        real_islink = os.path.islink
        os.path.islink = lambda path: True  # type: ignore[assignment]
        try:
            with self.assertRaises(HeadlessError) as caught:
                validate_exact_path(self.authorization(), "checkpoint")
        finally:
            os.path.islink = real_islink  # type: ignore[assignment]
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_PATH_IS_LINK")

    def test_reparse_point_guard_refuses(self) -> None:
        import stat as stat_module

        real_lstat = os.lstat
        flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

        class _Stat:
            def __init__(self, inner: object) -> None:
                self._inner = inner
                self.st_file_attributes = flag

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        os.lstat = lambda path: _Stat(real_lstat(path))  # type: ignore[assignment]
        try:
            with self.assertRaises(HeadlessError) as caught:
                validate_exact_path(self.authorization(), "checkpoint")
        finally:
            os.lstat = real_lstat  # type: ignore[assignment]
        self.assertEqual(
            caught.exception.code, "CONTROLLED_LOAD_PATH_IS_REPARSE_POINT"
        )

    def test_real_symlink_is_refused_where_permitted(self) -> None:
        link = self.root / "linked.safetensors"
        try:
            link.symlink_to(self.checkpoint)
        except (OSError, NotImplementedError):
            return  # guard already proven above
        auth = ControlledLoadAuthorization(
            {
                "checkpoint": str(link),
                "text_encoder": str(self.text_encoder),
                "vae": str(self.vae),
            }
        )
        with self.assertRaises(HeadlessError) as caught:
            validate_exact_path(auth, "checkpoint")
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_PATH_IS_LINK")

    def test_intake_does_not_enumerate_the_parent_directory(self) -> None:
        calls: list[str] = []
        real_listdir, real_scandir = os.listdir, os.scandir

        def spy_listdir(path="."):
            calls.append(str(path))
            return real_listdir(path)

        def spy_scandir(path="."):
            calls.append(str(path))
            return real_scandir(path)

        os.listdir, os.scandir = spy_listdir, spy_scandir  # type: ignore[assignment]
        try:
            for role in ("checkpoint", "text_encoder", "vae"):
                validate_exact_path(self.authorization(), role)
        finally:
            os.listdir, os.scandir = real_listdir, real_scandir  # type: ignore[assignment]
        self.assertEqual(calls, [], f"parent directory was enumerated: {calls}")


# -------------------------------------------------------------------- headers


class HeaderTests(_FixtureCase):
    def test_header_is_read_without_the_payload(self) -> None:
        header = read_safetensors_header(self.checkpoint)
        self.assertIn("net.blocks.0.mlp.layer1.weight", header)
        for spec in header.values():
            if isinstance(spec, dict) and "data_offsets" in spec:
                self.assertEqual(spec["data_offsets"][0], spec["data_offsets"][1])

    def test_truncated_file_is_refused(self) -> None:
        broken = self.root / "broken.safetensors"
        broken.write_bytes(b"\x04\x00\x00")
        with self.assertRaises(HeadlessError) as caught:
            read_safetensors_header(broken)
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_HEADER_TRUNCATED")

    def test_implausible_header_length_is_refused(self) -> None:
        hostile = self.root / "hostile.safetensors"
        hostile.write_bytes(struct.pack("<Q", MAX_HEADER_BYTES + 1) + b"{}")
        with self.assertRaises(HeadlessError) as caught:
            read_safetensors_header(hostile)
        self.assertEqual(
            caught.exception.code, "CONTROLLED_LOAD_HEADER_IMPLAUSIBLE"
        )

    def test_malformed_header_is_refused(self) -> None:
        bad = self.root / "bad.safetensors"
        payload = b"not json at all"
        bad.write_bytes(struct.pack("<Q", len(payload)) + payload)
        with self.assertRaises(HeadlessError) as caught:
            read_safetensors_header(bad)
        self.assertEqual(caught.exception.code, "CONTROLLED_LOAD_HEADER_MALFORMED")

    def test_summary_carries_no_raw_key_inventory(self) -> None:
        header = read_safetensors_header(self.checkpoint)
        summary = summarize_header("checkpoint", "id0", header).to_dict()
        rendered = json.dumps(summary)
        self.assertNotIn("blocks.7.mlp.layer1.weight", rendered)
        self.assertGreater(summary["tensor_count"], 0)
        self.assertIn("BF16", summary["dtypes"])  # type: ignore[operator]


# ------------------------------------------------------------------ preflight


class PreflightTests(_FixtureCase):
    def headers(self) -> tuple[dict, dict, dict]:
        return (
            read_safetensors_header(self.checkpoint),
            read_safetensors_header(self.text_encoder),
            read_safetensors_header(self.vae),
        )

    def test_matching_triplet_is_compatible(self) -> None:
        ckpt, enc, vae = self.headers()
        verdict = evaluate_triplet(
            inspect_checkpoint("c", ckpt),
            inspect_text_encoder("t", enc),
            inspect_vae("v", vae),
        )
        self.assertEqual(verdict.verdict, PREFLIGHT_COMPATIBLE)
        self.assertEqual(verdict.family, "anima")
        self.assertIn("anima.Anima", verdict.selected_loader)

    def test_net_prefix_is_recognized_like_detection_py(self) -> None:
        ckpt, _, _ = self.headers()
        self.assertEqual(unet_prefix(ckpt), "net.")
        plain = self.root / "plain.safetensors"
        build_safetensors(
            plain, anima_checkpoint_tensors(prefix="model.diffusion_model.")
        )
        self.assertEqual(
            unet_prefix(read_safetensors_header(plain)), "model.diffusion_model."
        )

    def test_unrecognized_checkpoint_is_unsupported(self) -> None:
        alien = self.root / "alien.safetensors"
        build_safetensors(alien, {"some.other.weight": ([4, 4], "F32")})
        _, enc, vae = self.headers()
        verdict = evaluate_triplet(
            inspect_checkpoint("c", read_safetensors_header(alien)),
            inspect_text_encoder("t", enc),
            inspect_vae("v", vae),
        )
        self.assertEqual(verdict.verdict, PREFLIGHT_UNSUPPORTED)
        self.assertEqual(verdict.selected_loader, "none")

    def test_wrong_encoder_width_is_incompatible(self) -> None:
        wrong = self.root / "wrong-encoder.safetensors"
        build_safetensors(wrong, qwen3_tensors(hidden=2560))
        ckpt, _, vae = self.headers()
        verdict = evaluate_triplet(
            inspect_checkpoint("c", ckpt),
            inspect_text_encoder("t", read_safetensors_header(wrong)),
            inspect_vae("v", vae),
        )
        self.assertEqual(verdict.verdict, PREFLIGHT_INCOMPATIBLE)
        self.assertTrue(any("qwen3_06b" in item for item in verdict.blocking))

    def test_non_wan_vae_is_incompatible(self) -> None:
        wrong = self.root / "wrong-vae.safetensors"
        build_safetensors(
            wrong,
            {
                "decoder.conv_in.weight": ([512, 4, 3, 3], "F32"),
                "encoder.conv_out.weight": ([8, 512, 3, 3], "F32"),
            },
        )
        ckpt, enc, _ = self.headers()
        verdict = evaluate_triplet(
            inspect_checkpoint("c", ckpt),
            inspect_text_encoder("t", enc),
            inspect_vae("v", read_safetensors_header(wrong)),
        )
        self.assertEqual(verdict.verdict, PREFLIGHT_INCOMPATIBLE)
        self.assertTrue(any("Wan" in item for item in verdict.blocking))

    def test_wrong_latent_width_is_incompatible(self) -> None:
        wrong = self.root / "wrong-latent.safetensors"
        build_safetensors(wrong, wan_vae_tensors(latent=4))
        ckpt, enc, _ = self.headers()
        verdict = evaluate_triplet(
            inspect_checkpoint("c", ckpt),
            inspect_text_encoder("t", enc),
            inspect_vae("v", read_safetensors_header(wrong)),
        )
        self.assertEqual(verdict.verdict, PREFLIGHT_INCOMPATIBLE)

    def test_wrong_model_channels_is_rejected(self) -> None:
        wrong = self.root / "wrong-dit.safetensors"
        build_safetensors(wrong, anima_checkpoint_tensors(model_channels=1536))
        finding = inspect_checkpoint("c", read_safetensors_header(wrong))
        self.assertFalse(finding.recognized)
        self.assertTrue(any("model_channels" in item for item in finding.mismatches))

    def test_preflight_rules_match_retained_source(self) -> None:
        detection = (
            APP_ROOT
            / "modules_forge"
            / "packages"
            / "huggingface_guess"
            / "detection.py"
        ).read_text(encoding="utf-8")
        self.assertIn('blocks.0.mlp.layer1.weight".format(key_prefix)', detection)
        self.assertIn('"net.",  # cosmos', detection)
        loader = (APP_ROOT / "backend" / "loader.py").read_text(encoding="utf-8")
        self.assertIn("decoder.middle.0.residual.0.gamma", loader)
        self.assertIn("post_attention_layernorm.weight", loader)
        model_list = (
            APP_ROOT
            / "modules_forge"
            / "packages"
            / "huggingface_guess"
            / "model_list.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"qwen3_06b.transformer": "text_encoder"', model_list)
        self.assertIn("latent_format = latent.Wan21", model_list)


# ----------------------------------------------------------- device and probe


class ControlledDeviceTests(unittest.TestCase):
    def test_device_module_imports_no_torch_at_module_scope(self) -> None:
        from forge_headless.import_graph import module_level_imports

        for name in (
            "forge_headless/controlled_device.py",
            "forge_headless/load_authorization.py",
            "forge_headless/model_intake.py",
            "forge_headless/triplet_preflight.py",
        ):
            imports = module_level_imports(
                ast.parse((APP_ROOT / name).read_text(encoding="utf-8"))
            )
            with self.subTest(module=name):
                for forbidden in ("torch", "gradio", "gradio_client"):
                    self.assertNotIn(forbidden, imports)

    def test_only_cuda_is_ever_initialized(self) -> None:
        source = (
            APP_ROOT / "forge_headless" / "controlled_device.py"
        ).read_text(encoding="utf-8")
        for other in ("torch.backends.mps", "torch.xpu", "directml"):
            self.assertNotIn(other, source)
        self.assertIn("torch.cuda.is_available()", source)

    def test_ceiling_is_enforced_not_merely_intended(self) -> None:
        source = (
            APP_ROOT / "forge_headless" / "controlled_device.py"
        ).read_text(encoding="utf-8")
        self.assertIn("set_per_process_memory_fraction", source)
        self.assertIn("CONTROLLED_LOAD_CEILING_NOT_ENFORCEABLE", source)

    def test_empty_cache_is_separate_from_reference_release(self) -> None:
        source = (
            APP_ROOT / "forge_headless" / "controlled_device.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def release_cuda_cache", source)
        probe = (
            APP_ROOT / "scripts" / "headless" / "controlled_load_probe.py"
        ).read_text(encoding="utf-8")
        # Owned release is sampled before the cache is touched, and the
        # post-cache numbers are reported under their own key.
        self.assertLess(
            probe.index("telemetry.after_release = sample_current()"),
            probe.index("release_cuda_cache()"),
        )
        self.assertIn("telemetry.after_cache_clear = sample_current()", probe)


class ProbeContractTests(unittest.TestCase):
    script = APP_ROOT / "scripts" / "headless" / "controlled_load_probe.py"

    def test_script_parses_and_takes_paths_locally(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("--checkpoint", source)
        self.assertIn("--consume-single-load-attempt", source)

    def test_probe_has_no_http_or_network_surface(self) -> None:
        from forge_headless.import_graph import module_level_imports

        imports = module_level_imports(
            ast.parse(self.script.read_text(encoding="utf-8"))
        )
        for forbidden in ("socket", "urllib", "http", "requests", "torch", "gradio"):
            self.assertNotIn(forbidden, imports)

    def test_probe_never_generates(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        for forbidden in (
            "submit_generation",
            "process_images",
            "sample(",
            "decode_first_stage",
            "save_image",
        ):
            self.assertNotIn(forbidden, source)

    def test_probe_enforces_the_declared_limits(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        self.assertIn("TIMEOUT_SECONDS = 600", source)
        self.assertIn("VRAM_CEILING_BYTES = 14 * 1024**3", source)
        self.assertIn("timeout=timeout", source)

    def test_probe_redacts_paths_and_user(self) -> None:
        module = _load_probe_module()
        sample = {
            "a": str(APP_ROOT / "x.safetensors"),
            "b": [str(Path.home())],
        }
        rendered = json.dumps(module.redact(sample))
        self.assertNotIn(str(APP_ROOT), rendered)
        self.assertNotIn(Path.home().name, rendered)
        self.assertIn("<REDACTED>", rendered)

    def test_worker_uses_a_killable_subprocess(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        self.assertIn("subprocess.run", source)
        self.assertIn("subprocess.TimeoutExpired", source)
        self.assertIn("worker_exited", source)

    def test_worker_runs_offline(self) -> None:
        source = self.script.read_text(encoding="utf-8")
        for flag in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
            self.assertIn(flag, source)


def _load_probe_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_controlled_load_probe_under_test",
        APP_ROOT / "scripts" / "headless" / "controlled_load_probe.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_phase2b_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__]
        )
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_PHASE2B_TESTS,
            "Phase 2B test count changed: update EXPECTED_PHASE2B_TESTS "
            "deliberately, or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
