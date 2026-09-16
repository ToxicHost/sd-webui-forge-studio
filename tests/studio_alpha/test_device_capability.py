"""B2 — device capability metadata in the owned Studio contract.

No Torch import anywhere in this file. Device values are contract data the
backend reports; Studio transports them and never derives them from the host.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

OWNED_PACKAGE = APP_ROOT / "forge_studio"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")
        self.backend = self.studio.MockBackend(event_interval_seconds=0.001)
        self.application = self.studio.StudioApplication(self.backend)

    def _model_id(self) -> str:
        return self.application.list_models()[0].model_id


class CapabilityContractTests(_Base):
    def test_construction_without_device_fields_still_works(self) -> None:
        """1. Backward compatible: the new fields all have defaults."""

        capability = self.studio.ModelCapability(
            model_id="m",
            operation="txt2img",
            dimension_alignment=8,
            minimum_dimension=64,
            maximum_dimension=2048,
            maximum_pixels=None,
            normalizes_dimensions=False,
            is_mock=False,
        )
        self.assertEqual(self.studio.DeviceType.UNKNOWN, capability.device_type)
        self.assertEqual(
            self.studio.DtypePolicy.UNKNOWN,
            capability.dtype_policy,
        )
        self.assertEqual(
            self.studio.AttentionBackend.UNKNOWN,
            capability.attention_backend,
        )

    def test_defaults_are_unknown_not_a_positive_claim(self) -> None:
        """1. Absent must not be mistaken for 'reported CPU'."""

        capability = self.studio.ModelCapability(
            model_id="m",
            operation="txt2img",
            dimension_alignment=1,
            minimum_dimension=1,
            maximum_dimension=None,
            maximum_pixels=None,
            normalizes_dimensions=False,
            is_mock=False,
        )
        self.assertNotEqual(
            self.studio.DeviceType.CPU,
            capability.device_type,
        )

    def test_serialization_emits_plain_strings(self) -> None:
        """2."""

        payload = self.application.get_capability(self._model_id()).to_dict()
        self.assertEqual("cpu", payload["device_type"])
        self.assertEqual("fp32", payload["dtype_policy"])
        self.assertEqual("backend_default", payload["attention_backend"])
        # Must survive a real JSON round trip, not just repr.
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_values_deserialize_back_to_enum_members(self) -> None:
        """3."""

        payload = self.application.get_capability(self._model_id()).to_dict()
        self.assertIs(
            self.studio.DeviceType.CPU,
            self.studio.DeviceType(payload["device_type"]),
        )
        self.assertIs(
            self.studio.DtypePolicy.FP32,
            self.studio.DtypePolicy(payload["dtype_policy"]),
        )
        self.assertIs(
            self.studio.AttentionBackend.BACKEND_DEFAULT,
            self.studio.AttentionBackend(payload["attention_backend"]),
        )

    def test_mock_reports_honest_values(self) -> None:
        """4. The mock renders SVG strings: CPU, fp32, no attention backend."""

        capability = self.application.get_capability(self._model_id())
        self.assertEqual(self.studio.DeviceType.CPU, capability.device_type)
        self.assertEqual(self.studio.DtypePolicy.FP32, capability.dtype_policy)
        self.assertEqual(
            self.studio.AttentionBackend.BACKEND_DEFAULT,
            capability.attention_backend,
        )
        self.assertTrue(capability.is_mock)

    def test_unknown_future_value_is_rejected_not_coerced(self) -> None:
        """5, 6. An unrecognised value must not silently become UNKNOWN."""

        with self.assertRaises(ValueError):
            self.studio.DeviceType("quantum-tpu")
        with self.assertRaises(ValueError):
            self.studio.DtypePolicy("fp8")
        with self.assertRaises(ValueError):
            self.studio.AttentionBackend("hand-rolled")

    def test_every_enum_has_an_unknown_member(self) -> None:
        """5. Future-safe: a backend can always say 'I do not know'."""

        self.assertEqual("unknown", self.studio.DeviceType.UNKNOWN.value)
        self.assertEqual("unknown", self.studio.DtypePolicy.UNKNOWN.value)
        self.assertEqual("unknown", self.studio.AttentionBackend.UNKNOWN.value)

    def test_expected_device_vocabulary_is_present(self) -> None:
        self.assertEqual(
            {"cuda", "mps", "cpu", "xpu", "directml", "unknown"},
            {member.value for member in self.studio.DeviceType},
        )


class CapabilityTransportTests(_Base):
    def test_presentation_transports_device_fields_unchanged(self) -> None:
        """7. No frontend change, no presentation-side interpretation."""

        presentation = importlib.import_module("forge_studio.presentation")
        surface = presentation.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )
        payload = surface.capability(self._model_id())
        self.assertEqual("cpu", payload["device_type"])
        self.assertEqual("fp32", payload["dtype_policy"])
        self.assertEqual("backend_default", payload["attention_backend"])

    def test_device_type_is_not_inferred_from_sys_platform(self) -> None:
        """8. The reported device must not track the host OS."""

        real = sys.platform
        observed = []
        try:
            for fake in ("darwin", "win32", "linux", "plan9"):
                sys.platform = fake
                observed.append(
                    self.application.get_capability(
                        self._model_id()
                    ).device_type
                )
        finally:
            sys.platform = real
        self.assertEqual(1, len(set(observed)), observed)
        self.assertEqual(self.studio.DeviceType.CPU, observed[0])

    def test_two_adapters_can_report_different_devices(self) -> None:
        """9. CUDA and MPS adapters differ, on one host, with no Torch."""

        import dataclasses

        studio = self.studio

        class _CudaLike(type(self.backend)):
            def get_capability(self, model_id, operation):
                return dataclasses.replace(
                    super().get_capability(model_id, operation),
                    device_type=studio.DeviceType.CUDA,
                    dtype_policy=studio.DtypePolicy.FP16,
                    attention_backend=studio.AttentionBackend.XFORMERS,
                    maximum_dimension=2048,
                )

        class _MpsLike(type(self.backend)):
            def get_capability(self, model_id, operation):
                return dataclasses.replace(
                    super().get_capability(model_id, operation),
                    device_type=studio.DeviceType.MPS,
                    dtype_policy=studio.DtypePolicy.BF16,
                    attention_backend=studio.AttentionBackend.PYTORCH_SDPA,
                    maximum_dimension=1024,
                )

        cuda_app = studio.StudioApplication(_CudaLike(event_interval_seconds=0.001))
        mps_app = studio.StudioApplication(_MpsLike(event_interval_seconds=0.001))
        cuda = cuda_app.get_capability(cuda_app.list_models()[0].model_id)
        mps = mps_app.get_capability(mps_app.list_models()[0].model_id)

        self.assertEqual(studio.DeviceType.CUDA, cuda.device_type)
        self.assertEqual(studio.DeviceType.MPS, mps.device_type)
        self.assertNotEqual(cuda.dtype_policy, mps.dtype_policy)
        self.assertNotEqual(cuda.attention_backend, mps.attention_backend)
        # Per-model limits may legitimately differ by device.
        self.assertNotEqual(cuda.maximum_dimension, mps.maximum_dimension)

    def test_dimension_fields_are_independent_of_device_fields(self) -> None:
        """10."""

        capability = self.application.get_capability(self._model_id())
        self.assertEqual(1, capability.dimension_alignment)
        self.assertEqual(1, capability.minimum_dimension)
        self.assertIsNone(capability.maximum_dimension)
        self.assertFalse(capability.normalizes_dimensions)

    def test_catalog_and_residency_remain_unaffected(self) -> None:
        """11."""

        models = self.application.list_models()
        self.assertTrue(models)
        self.assertFalse(self.application.get_current_model().loaded)
        self.application.load_model(models[0].model_id)
        self.assertTrue(self.application.get_current_model().loaded)
        self.application.get_capability(models[0].model_id)
        self.assertTrue(self.application.get_current_model().loaded)


class OwnedPackagePurityTests(unittest.TestCase):
    """The owned package must stay free of Torch, Gradio, and platform logic."""

    #: Headless modules that carry the same rule as the owned package.
    #:
    #: `forge_headless` is allowed to import the backend, so it was never in
    #: scope here. The P0.4 filesystem stack is different: it decides what a
    #: path IS, on three operating systems, and the whole design rests on
    #: asking what a host can DO rather than guessing from its name. A
    #: `sys.platform` branch in there would be the retrofit this phase exists
    #: to avoid, so the ban follows the code rather than the package.
    HEADLESS_UNDER_THE_SAME_RULE = ("path_identity.py",)

    def _owned_sources(self):
        for path in sorted(OWNED_PACKAGE.rglob("*.py")):
            if "frontend" in path.parts:
                continue
            yield path
        headless = OWNED_PACKAGE.parent / "forge_headless"
        for name in self.HEADLESS_UNDER_THE_SAME_RULE:
            candidate = headless / name
            if candidate.exists():
                yield candidate

    def test_no_torch_or_gradio_import_in_owned_code(self) -> None:
        for path in self._owned_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(path=path.name):
                for banned in ("torch", "torchvision", "gradio", "numpy"):
                    self.assertNotIn(banned, imported)

    def test_owned_code_does_not_branch_on_the_host_platform(self) -> None:
        """No executable read of `sys.platform` or `platform.system`.

        Checked over the AST rather than the raw text, so documentation may
        discuss the rule without tripping it. `os.name` stays permitted: it
        selects between real API differences rather than guessing a device.
        """

        banned = {("sys", "platform"), ("platform", "system")}
        for path in self._owned_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(
                    node.value, ast.Name
                ):
                    pair = (node.value.id, node.attr)
                    if pair in banned:
                        found.add(pair)
            with self.subTest(path=path.name):
                self.assertEqual(set(), found)


class StrictEnumValidationTests(_Base):
    """`ModelCapability` requires enum members, not look-alike strings."""

    BASE = dict(
        model_id="m",
        operation="txt2img",
        dimension_alignment=1,
        minimum_dimension=1,
        maximum_dimension=None,
        maximum_pixels=None,
        normalizes_dimensions=False,
        is_mock=False,
    )

    def _build(self, **overrides):
        return self.studio.ModelCapability(**{**self.BASE, **overrides})

    def test_enum_members_are_accepted(self) -> None:
        capability = self._build(
            device_type=self.studio.DeviceType.MPS,
            dtype_policy=self.studio.DtypePolicy.BF16,
            attention_backend=self.studio.AttentionBackend.PYTORCH_SDPA,
        )
        self.assertEqual(self.studio.DeviceType.MPS, capability.device_type)

    def test_defaults_remain_unknown(self) -> None:
        capability = self._build()
        self.assertEqual(self.studio.DeviceType.UNKNOWN, capability.device_type)
        self.assertEqual(
            self.studio.DtypePolicy.UNKNOWN,
            capability.dtype_policy,
        )
        self.assertEqual(
            self.studio.AttentionBackend.UNKNOWN,
            capability.attention_backend,
        )

    def test_raw_strings_are_rejected(self) -> None:
        """The enums subclass str, so this is the case that used to slip by."""

        for field, value in (
            ("device_type", "cuda"),
            ("dtype_policy", "fp16"),
            ("attention_backend", "xformers"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(TypeError) as raised:
                    self._build(**{field: value})
                self.assertIn(field, str(raised.exception))

    def test_none_is_rejected(self) -> None:
        for field in ("device_type", "dtype_policy", "attention_backend"):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    self._build(**{field: None})

    def test_unrelated_enum_members_are_rejected(self) -> None:
        for field, value in (
            ("device_type", self.studio.DtypePolicy.FP16),
            ("dtype_policy", self.studio.AttentionBackend.SAGE),
            ("attention_backend", self.studio.DeviceType.CUDA),
        ):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    self._build(**{field: value})

    def test_error_message_is_stable_and_names_the_field(self) -> None:
        with self.assertRaises(TypeError) as raised:
            self._build(device_type="cuda")
        message = str(raised.exception)
        self.assertIn("device_type must be a DeviceType member", message)

    def test_mock_still_reports_cpu_fp32_backend_default(self) -> None:
        capability = self.application.get_capability(self._model_id())
        self.assertEqual(self.studio.DeviceType.CPU, capability.device_type)
        self.assertEqual(self.studio.DtypePolicy.FP32, capability.dtype_policy)
        self.assertEqual(
            self.studio.AttentionBackend.BACKEND_DEFAULT,
            capability.attention_backend,
        )

    def test_serialization_is_unchanged_by_validation(self) -> None:
        payload = self.application.get_capability(self._model_id()).to_dict()
        self.assertEqual(
            {
                "attention_backend": "backend_default",
                "device_type": "cpu",
                "dimension_alignment": 1,
                "dtype_policy": "fp32",
                "is_mock": True,
                "maximum_dimension": None,
                "maximum_pixels": None,
                "minimum_dimension": 1,
                "model_id": "studio-mock-illustration-v1",
                "normalizes_dimensions": False,
                "operation": "txt2img",
            },
            payload,
        )


class CapabilityRouteTests(_Base):
    """GET /studio/capability -- read-only, same-origin, no state change."""

    ROUTE = "/studio/capability"

    def setUp(self) -> None:
        super().setUp()
        presentation = importlib.import_module("forge_studio.presentation")
        self.adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        self.surface = presentation.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )
        self.adapter = self.adapter_module.SourceFrontendAdapter(self.surface)

    def _get(self, query: str = ""):
        return self.adapter.get(f"{self.ROUTE}{query}")

    def test_route_returns_the_full_capability_document(self) -> None:
        model_id = self._model_id()
        payload = self._get(f"?model_id={model_id}&operation=txt2img")
        self.assertEqual(
            {
                "attention_backend",
                "device_type",
                "dimension_alignment",
                "dtype_policy",
                "is_mock",
                "maximum_dimension",
                "maximum_pixels",
                "minimum_dimension",
                "model_id",
                "normalizes_dimensions",
                "operation",
            },
            set(payload),
        )
        self.assertEqual(model_id, payload["model_id"])
        self.assertEqual("cpu", payload["device_type"])
        self.assertEqual("fp32", payload["dtype_policy"])
        self.assertEqual("backend_default", payload["attention_backend"])
        self.assertEqual(1, payload["dimension_alignment"])
        self.assertIsNone(payload["maximum_dimension"])

    def test_response_equals_to_dict(self) -> None:
        model_id = self._model_id()
        self.assertEqual(
            self.application.get_capability(model_id).to_dict(),
            self._get(f"?model_id={model_id}"),
        )

    def test_operation_defaults_to_txt2img(self) -> None:
        payload = self._get(f"?model_id={self._model_id()}")
        self.assertEqual("txt2img", payload["operation"])

    def test_missing_model_id_is_a_structured_400(self) -> None:
        for query in ("", "?operation=txt2img", "?model_id=", "?model_id=%20"):
            with self.subTest(query=query):
                with self.assertRaises(
                    self.adapter_module.SourceFrontendRequestError
                ) as raised:
                    self._get(query)
                self.assertEqual(400, raised.exception.error["http_status"])

    def test_oversized_parameters_are_rejected(self) -> None:
        for query in (
            "?model_id=" + "x" * 300,
            f"?model_id={self._model_id()}&operation=" + "y" * 300,
        ):
            with self.subTest(query=query[:40]):
                with self.assertRaises(
                    self.adapter_module.SourceFrontendRequestError
                ) as raised:
                    self._get(query)
                self.assertEqual(400, raised.exception.error["http_status"])

    def test_unknown_model_uses_the_existing_structured_error(self) -> None:
        with self.assertRaises(self.studio.StudioError) as raised:
            self._get("?model_id=absent-model")
        self.assertEqual("MODEL_NOT_AVAILABLE", raised.exception.error.code)

    def test_unsupported_operation_uses_the_existing_structured_error(self) -> None:
        with self.assertRaises(self.studio.StudioError) as raised:
            self._get(f"?model_id={self._model_id()}&operation=img2img")
        self.assertEqual(
            "OPERATION_NOT_SUPPORTED",
            raised.exception.error.code,
        )

    def test_repeated_reads_are_pure(self) -> None:
        model_id = self._model_id()
        first = self._get(f"?model_id={model_id}")
        for _ in range(4):
            self.assertEqual(first, self._get(f"?model_id={model_id}"))

    def test_route_never_selects_or_loads_a_model(self) -> None:
        self.assertFalse(self.application.get_current_model().loaded)
        for _ in range(3):
            self._get(f"?model_id={self._model_id()}")
        self.assertFalse(self.application.get_current_model().loaded)

    def test_route_does_not_disturb_an_existing_selection(self) -> None:
        model_id = self._model_id()
        self.application.load_model(model_id)
        self._get(f"?model_id={model_id}")
        residency = self.application.get_current_model()
        self.assertTrue(residency.loaded)
        self.assertEqual(model_id, residency.model_id)

    def test_route_is_a_read_and_never_a_post(self) -> None:
        """It must not be reachable as a mutation."""

        with self.assertRaises(Exception) as raised:
            self.adapter.post(self.ROUTE, {"model_id": self._model_id()})
        self.assertTrue(hasattr(raised.exception, "error"))

    def test_route_adds_no_torch_or_platform_import(self) -> None:
        source = (
            APP_ROOT / "forge_studio" / "source_api_adapter.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for banned in ("torch", "gradio", "numpy", "platform"):
            self.assertNotIn(banned, imported)


if __name__ == "__main__":
    unittest.main()
