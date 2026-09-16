"""R0 baseline-recovery regressions found by the 2026-08-10 truth audit.

SCOPE: MINIMAL_RUNTIME_SCOPE. These tests do not import Torch, initialise Neo,
load a model, use the GPU, open a browser, or contact a network. Engine modules
are represented by in-memory doubles at the existing adapter seams.

Each assertion is intended to fail if its corresponding pre-audit defect is
restored: a failed upscaler scan that latches forever, Auto Detail omitted from
the public capability set, an invented model architecture, or a refusal that a
frontend caller turns into a green success.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless import neo_registries  # noqa: E402
from forge_headless.studio_adapter import HeadlessBackendAdapter  # noqa: E402
from forge_headless.studio_generation import translate_request  # noqa: E402
from forge_studio.contracts import (  # noqa: E402
    AutoDetailSettings,
    AutoDetailSlot,
    GenerationRequest,
)
from forge_studio.source_api_adapter import (  # noqa: E402
    SourceFrontendAdapter,
    SourceFrontendRouteError,
)

EXPECTED_R0_RECOVERY_TESTS = 19

APP_JS = (APP_ROOT / "forge_studio" / "frontend" / "app.js").read_text(
    encoding="utf-8"
)
GALLERY_JS = (APP_ROOT / "forge_studio" / "frontend" / "gallery.js").read_text(
    encoding="utf-8"
)


def _request(**overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "model_id": "model-token",
        "positive_prompt": "a prompt",
        "negative_prompt": "",
        "seed": 7,
        "steps": 4,
        "cfg_scale": 6.0,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


class _Presentation:
    """Only the audited routes are used, so no optimistic behavior is needed."""


class UpscalerRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        neo_registries._UPSCALER_SCAN_DONE.clear()
        self.addCleanup(neo_registries._UPSCALER_SCAN_DONE.clear)

    @staticmethod
    def _modules(loader: object, *, preloaded: tuple[str, ...] = ()):
        package = ModuleType("modules")
        package.__path__ = []  # type: ignore[attr-defined]
        shared = ModuleType("modules.shared")
        shared.latent_upscale_modes = {"Latent": object()}
        shared.sd_upscalers = [SimpleNamespace(name=name) for name in preloaded]
        modelloader = ModuleType("modules.modelloader")
        modelloader.load_upscalers = loader
        package.shared = shared
        package.modelloader = modelloader
        return package, shared, modelloader

    def test_a_failed_scan_is_retryable_and_success_latches_once(self) -> None:
        calls: list[int] = []
        package: ModuleType
        shared: ModuleType

        def load_upscalers() -> None:
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                raise RuntimeError("engine still starting")
            shared.sd_upscalers = [SimpleNamespace(name="Owner Upscaler")]

        package, shared, modelloader = self._modules(load_upscalers)
        fake_modules = {
            "modules": package,
            "modules.shared": shared,
            "modules.modelloader": modelloader,
        }
        with patch.dict(sys.modules, fake_modules):
            first = neo_registries._read_upscalers()
            self.assertEqual(("Latent",), first[0])
            self.assertEqual((), first[1])
            self.assertEqual([], neo_registries._UPSCALER_SCAN_DONE)

            second = neo_registries._read_upscalers()
            self.assertEqual(("Owner Upscaler",), second[1])
            self.assertEqual([True], neo_registries._UPSCALER_SCAN_DONE)

            shared.sd_upscalers = []
            third = neo_registries._read_upscalers()
            self.assertEqual((), third[1])
            self.assertEqual([1, 2], calls)

    def test_a_prepopulated_registry_is_not_rescanned(self) -> None:
        def unexpected_scan() -> None:
            self.fail("a populated upscaler registry must not be rescanned")

        package, shared, modelloader = self._modules(
            unexpected_scan, preloaded=("Already Ready",)
        )
        with patch.dict(
            sys.modules,
            {
                "modules": package,
                "modules.shared": shared,
                "modules.modelloader": modelloader,
            },
        ):
            _latent, image = neo_registries._read_upscalers()
        self.assertEqual(("Already Ready",), image)

    def test_registry_payload_exposes_scan_completion_separately(self) -> None:
        complete = neo_registries.Registries(
            samplers=("Euler",),
            schedulers=("Automatic",),
            available=True,
            upscaler_scan_complete=True,
        ).to_dict()
        incomplete = neo_registries.Registries(
            samplers=("Euler",), schedulers=("Automatic",), available=True
        ).to_dict()
        self.assertTrue(complete["upscaler_scan_complete"])
        self.assertFalse(incomplete["upscaler_scan_complete"])

    def test_a_scan_that_died_after_its_own_del_is_not_retried(self) -> None:
        """The real `load_upscalers()` deletes the attribute FIRST.

        `modules/modelloader.py` opens with `del shared.sd_upscalers` before it
        builds anything. A failure after that point leaves the name gone, and
        every later call re-enters the same `del` and raises AttributeError
        before doing any work -- so retrying can never succeed.

        The sibling retry test above raises BEFORE touching the attribute,
        which is the recoverable failure and must stay retryable. This one
        models the side effect, because a double that only models the raise
        cannot tell the two apart -- and the implementation it was written
        against looped forever on this input.
        """

        calls: list[int] = []

        def load_upscalers() -> None:
            calls.append(len(calls) + 1)
            del shared.sd_upscalers  # exactly what the real loader does first
            raise RuntimeError("died after deleting the registry")

        package, shared, modelloader = self._modules(load_upscalers)
        fake_modules = {
            "modules": package,
            "modules.shared": shared,
            "modules.modelloader": modelloader,
        }
        with patch.dict(sys.modules, fake_modules):
            first = neo_registries._read_upscalers()
            self.assertEqual((), first[1])
            self.assertFalse(hasattr(shared, "sd_upscalers"))

            # Second read must NOT call the loader again: the attribute is
            # gone, so the call could only raise. It must latch instead, so
            # `upscaler_scan_complete` becomes true and app.js stops refreshing.
            second = neo_registries._read_upscalers()
            self.assertEqual((), second[1])
            self.assertEqual([1], calls)
            self.assertEqual([True], neo_registries._UPSCALER_SCAN_DONE)

            # And it stays latched -- no slow drift back into the loop.
            neo_registries._read_upscalers()
            self.assertEqual([1], calls)
            self.assertEqual([True], neo_registries._UPSCALER_SCAN_DONE)


class CapabilityTruthTests(unittest.TestCase):
    def test_public_headless_capabilities_advertise_auto_detail(self) -> None:
        adapter = HeadlessBackendAdapter(None, generation=object())
        self.assertIn("auto_detail", adapter.supported_generation_parameters())

    def test_the_advertised_field_really_changes_translation(self) -> None:
        enabled = AutoDetailSettings(
            enabled=True,
            slots=(AutoDetailSlot(enabled=True, detector="face.pt"),),
        )
        off = translate_request(_request(), request_id="off").headless_request
        on = translate_request(
            _request(auto_detail=enabled),
            request_id="on",
            resolve_detector=lambda _name: "detector-token",
        ).headless_request
        self.assertFalse(off.enable_adetailer)
        self.assertTrue(on.enable_adetailer)
        self.assertEqual(1, len(on.ad_slots))


class AdapterRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = SourceFrontendAdapter(_Presentation())

    def test_unknown_model_architecture_is_named_unknown(self) -> None:
        payload = self.adapter.get("/studio/check_model_te?title=model-token")
        self.assertEqual("unknown", payload["arch"])
        self.assertEqual("model-token", payload["title"])

    def test_missing_gallery_mutations_are_real_404_refusals(self) -> None:
        for route in ("/studio/gallery/scan-folders", "/studio/gallery/scan"):
            with self.subTest(route=route), self.assertRaises(
                SourceFrontendRouteError
            ) as raised:
                self.adapter.post(route, {})
            self.assertEqual(404, raised.exception.error["http_status"])

    def test_missing_workflow_and_layout_deletes_are_real_404_refusals(self) -> None:
        for route in ("/studio/workflows/example", "/studio/layouts/example"):
            with self.subTest(route=route), self.assertRaises(
                SourceFrontendRouteError
            ) as raised:
                self.adapter.delete(route)
            self.assertEqual(404, raised.exception.error["http_status"])


class FrontendTruthTests(unittest.TestCase):
    def test_workflow_delete_uses_the_shared_non_2xx_guard(self) -> None:
        api_block = APP_JS.split("const API = {", 1)[1].split("// UTILITIES", 1)[0]
        self.assertIn("async delete(path)", api_block)
        self.assertIn('this._finish("DELETE", path, r, text)', api_block)
        self.assertIn(
            'deleteWorkflow:  (id)      => API.delete("/studio/workflows/"',
            api_block,
        )

    def test_gallery_first_run_awaits_both_guarded_mutations(self) -> None:
        handler = APP_JS.split(
            'document.getElementById("firstRunGo")?.addEventListener', 1
        )[1].split("// EMERGENCY RESET", 1)[0]
        self.assertIn('const registration = await API.post(', handler)
        self.assertIn('"/studio/gallery/scan-folders", { path: _customPath }', handler)
        self.assertIn('const scan = await API.post("/studio/gallery/scan", {})', handler)
        self.assertIn("registration.ok !== true", handler)
        self.assertIn("if (scan?.error)", handler)
        self.assertNotIn('fetch(API.base + "/studio/gallery/scan', handler)

    def test_gallery_refusal_keeps_the_setup_card_open_without_overclaiming(self) -> None:
        handler = APP_JS.split(
            'document.getElementById("firstRunGo")?.addEventListener', 1
        )[1].split("// EMERGENCY RESET", 1)[0]
        refusal = handler.split('"firstRun.galleryUnavailable"', 1)[1]
        before_persist = refusal.split("_studioSaveDefaults", 1)[0]
        self.assertIn('"error"', before_persist)
        self.assertIn("return;", before_persist)
        self.assertNotIn("_close();", before_persist)
        self.assertIn("other choices may already have been applied", refusal)

    def test_frontend_does_not_latch_a_partial_registry_response(self) -> None:
        loader = APP_JS.split("async function _loadRegistries()", 1)[1].split(
            "const fill =", 1
        )[0]
        self.assertIn("document_.upscaler_scan_complete === true", loader)

    def test_lifecycle_placeholder_is_not_a_usable_architecture(self) -> None:
        restore = APP_JS.split("async function restoreTextEncoderForModel", 1)[1]
        lifecycle = restore.split("const check = await checkModelTE", 1)[0]
        self.assertIn('arch: "unknown"', lifecycle)
        self.assertNotIn('arch: "catalogue"', lifecycle)

    def test_legacy_architecture_placeholders_are_rejected(self) -> None:
        predicate = APP_JS.split("function _isUsableArch(arch)", 1)[1].split(
            "function _normalizeComponentMemory", 1
        )[0]
        for sentinel in ('"unknown"', '"mock"', '"catalogue"'):
            with self.subTest(sentinel=sentinel):
                self.assertIn(sentinel, predicate)

    def test_defaults_success_waits_for_persistence(self) -> None:
        save = APP_JS.split("function saveDefaults()", 1)[1].split(
            "window._studioSaveDefaults", 1
        )[0]
        first_run = APP_JS.split(
            'document.getElementById("firstRunGo")?.addEventListener', 1
        )[1].split("// EMERGENCY RESET", 1)[0]
        self.assertIn("return API.generate", save)
        self.assertIn("await window._studioSaveDefaults()", first_run)
        self.assertLess(first_run.index("await window._studioSaveDefaults()"),
                        first_run.index("_close();"))

    def test_gallery_scan_refusal_stops_before_followup_and_initialization(self) -> None:
        rescan = GALLERY_JS.split("async function rescan()", 1)[1].split(
            "// FOLDER MANAGEMENT", 1
        )[0]
        self.assertIn('if (r.error) { toast("Error: " + r.error); return; }', rescan)
        self.assertLess(rescan.index("if (r.error)"), rescan.index("/rescan-characters"))
        self.assertLess(rescan.index("await Promise.all"), rescan.index("G.initialized = true"))
        self.assertIn("finally", rescan)
        self.assertIn("G.loading = false", rescan)
        self.assertIn("clearTimeout(_scanPollTimer)", rescan)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_R0_RECOVERY_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
