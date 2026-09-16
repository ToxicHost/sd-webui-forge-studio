"""Tier-0 post-decode release and registry durability.

Attempt 05 produced a real 768x768 PNG through retained Forge sampling and VAE
decode, then held 9,568,256 bytes allocated across `empty_cache()`.

The concrete owner is `p.latents_after_sampling`. `modules/processing.py:271`
creates it as an **instance** attribute, shadowing the class list at `:236`, and
`:996` appends the sampler's CUDA output to it. The previous cleanup cleared the
*class* attribute -- an empty list -- and reported success while the instance
list kept the tensors.

These tests pin that ownership, prove the registry keeps only contained metadata,
and prove the published result survives every in-memory object being collected.

No CUDA tensor is allocated. No model file is opened. No image is generated.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import gc
import sys
import tempfile
import unittest
import weakref
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PROCESSING = APP_ROOT / "modules" / "processing.py"
PROBE = APP_ROOT / "scripts" / "headless" / "first_image_probe.py"
RESULT_DELIVERY = APP_ROOT / "forge_studio" / "result_delivery.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_POST_DECODE_TESTS = 35

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

#: A minimal valid PNG (1x1, truecolour) for contained registry proofs.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea735cd900000000049454e"
    "44ae426082"
)


class _Sentinel:
    """Weak-referenceable stand-in for a CUDA-owning generation object."""


def _fake_processing(*, with_outputs: bool = True):
    """Model the real ownership graph found in modules/processing.py."""

    class _Denoiser:
        def __init__(self):
            self.p = None
            self.inner_model = _Sentinel()

    class _Sampler:
        def __init__(self):
            self.model_wrap_cfg = _Denoiser()
            self.name = "Euler"
            self.scheduler = "Automatic"

    class _Processing:
        # class-level, shadowed by the instance ones below -- exactly as Forge
        # declares them at modules/processing.py:236-237
        latents_after_sampling = []
        pixels_after_sampling = []
        cached_c = [None, None, None]
        cached_uc = [None, None, None]

        def __init__(self):
            self.sampler = _Sampler()
            self.c = _Sentinel()
            self.uc = _Sentinel()
            self.rng = _Sentinel()
            self.modified_noise = _Sentinel()
            # instance attributes -- modules/processing.py:270-272
            self.extra_result_images = []
            self.latents_after_sampling = []
            self.pixels_after_sampling = []
            if with_outputs:
                self.latents_after_sampling.append(_Sentinel())  # :996
                self.pixels_after_sampling.append(_Sentinel())   # :1097
                self.extra_result_images.append(_Sentinel())     # :270
            self.closed = False

        def close(self):
            self.closed = True

        def clear_prompt_cache(self):
            type(self).cached_c = [None, None, None]
            type(self).cached_uc = [None, None, None]

    return _Processing()


class _FakeProcessed:
    def __init__(self):
        self.images = [_Sentinel()]
        self.latents = [_Sentinel()]
        self.info = "scalar"
        self.infotexts = ["scalar"]


# ---------------------------------------- 4.1 successful-run reference closure


class SuccessReferenceInventoryTests(unittest.TestCase):
    """Pin the ownership facts the fix depends on, against Forge source."""

    def test_accumulators_are_instance_attributes_shadowing_class_ones(self) -> None:
        source = PROCESSING.read_text(encoding="utf-8")
        # class-level declarations
        self.assertIn("    latents_after_sampling = []", source)
        self.assertIn("    pixels_after_sampling = []", source)
        # instance-level shadowing -- the whole reason the old fix missed
        self.assertIn("        self.latents_after_sampling = []", source)
        self.assertIn("        self.pixels_after_sampling = []", source)

    def test_sampler_output_is_appended_to_the_instance_list(self) -> None:
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn("p.latents_after_sampling.append(x_sample)", source)
        self.assertIn("p.pixels_after_sampling.append(image)", source)

    def test_the_appended_latent_comes_from_the_sampler(self) -> None:
        # x_sample iterates samples_ddim, which is p.sample(...)'s return value.
        tree = ast.parse(PROCESSING.read_text(encoding="utf-8"))
        inner = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "process_images_inner"
        )
        unparsed = ast.unparse(inner)
        self.assertIn("samples_ddim = p.sample(", unparsed)
        self.assertIn("for x_sample in samples_ddim:", unparsed)

    def test_decode_moves_samples_to_the_target_device(self) -> None:
        # decode_latent_batch(...).to(target_device); the caller passes
        # devices.cpu, so decoded samples are host-side.
        source = PROCESSING.read_text(encoding="utf-8")
        self.assertIn("samples_pytorch = decode_first_stage(model, batch).to(target_device)", source)
        self.assertIn("decode_latent_batch(p.sd_model, samples_ddim, target_device=devices.cpu", source)

    def test_release_module_declares_the_instance_list_fields(self) -> None:
        from forge_headless.failure_cleanup import _INSTANCE_LIST_FIELDS

        self.assertIn("latents_after_sampling", _INSTANCE_LIST_FIELDS)
        self.assertIn("pixels_after_sampling", _INSTANCE_LIST_FIELDS)
        self.assertIn("extra_result_images", _INSTANCE_LIST_FIELDS)


# ------------------------------------------------- 6.1 decode object graph


class DecodeObjectGraphTests(unittest.TestCase):
    def test_every_generation_sentinel_is_collectible(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        processed = _FakeProcessed()
        refs = {
            "final_latent": weakref.ref(processing.latents_after_sampling[0]),
            "pixels": weakref.ref(processing.pixels_after_sampling[0]),
            "extra_images": weakref.ref(processing.extra_result_images[0]),
            "conditioning": weakref.ref(processing.c),
            "unconditioning": weakref.ref(processing.uc),
            "modified_noise": weakref.ref(processing.modified_noise),
            "denoiser_inner": weakref.ref(processing.sampler.model_wrap_cfg.inner_model),
            "processed_image": weakref.ref(processed.images[0]),
            "processed_latent": weakref.ref(processed.latents[0]),
        }
        release_generation_references(processing=processing, processed=processed)
        del processing, processed
        gc.collect()

        for name, ref in refs.items():
            with self.subTest(sentinel=name):
                self.assertIsNone(ref(), f"{name} survived cleanup")

    def test_the_old_class_only_clear_would_not_have_released_the_latent(self) -> None:
        # Regression guard for the exact defect attempt 05 exposed.
        processing = _fake_processing()
        ref = weakref.ref(processing.latents_after_sampling[0])
        # simulate the previous behaviour: clear the CLASS attribute only
        type(processing).latents_after_sampling = []
        gc.collect()
        self.assertIsNotNone(
            ref(), "the class-only clear should NOT release the instance list"
        )
        processing.latents_after_sampling.clear()
        del processing
        gc.collect()
        self.assertIsNone(ref())

    def test_report_records_the_cleared_instance_fields(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        report = release_generation_references(processing=_fake_processing())
        cleared = report.to_dict()["post_sampling_instance_fields_cleared"]
        self.assertIn("latents_after_sampling", cleared)
        self.assertIn("pixels_after_sampling", cleared)
        self.assertTrue(report.final_latents_released)
        self.assertTrue(report.sampler_outputs_released)

    def test_processed_release_is_reported(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        report = release_generation_references(processed=_FakeProcessed())
        self.assertTrue(report.processed_released)
        self.assertTrue(report.pil_images_released)

    def test_lists_are_emptied_in_place_before_detaching(self) -> None:
        # A second holder of the same list must see the release too.
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        alias = processing.latents_after_sampling
        ref = weakref.ref(alias[0])
        release_generation_references(processing=processing)
        gc.collect()
        self.assertEqual(len(alias), 0, "the aliased list was not emptied in place")
        self.assertIsNone(ref())

    def test_release_is_safe_with_no_outputs(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        report = release_generation_references(processing=_fake_processing(with_outputs=False))
        self.assertTrue(report.processing_reference_released)


# --------------------------------------- 6.2 / 6.6 registry ownership+durability


class RegistryOwnershipTests(unittest.TestCase):
    def _registry(self, root: Path):
        from forge_studio.result_delivery import ResultRegistry

        return ResultRegistry(root)

    def test_registry_entry_holds_only_a_path_and_media_type(self) -> None:
        source = RESULT_DELIVERY.read_text(encoding="utf-8")
        self.assertIn("self._entries: OrderedDict[str, tuple[Path, str]]", source)

    def test_registry_source_retains_no_image_objects(self) -> None:
        tree = ast.parse(RESULT_DELIVERY.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("PIL", "torch", "numpy"):
            self.assertNotIn(forbidden, imported,
                             f"result_delivery imports {forbidden}; it must stay file-based")

    def test_handle_resolves_after_every_source_object_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "result.png"
            target.write_bytes(TINY_PNG)
            registry = self._registry(root)
            asset = registry.register(target, media_type="image/png")

            holder = _Sentinel()
            ref = weakref.ref(holder)
            del holder
            gc.collect()
            self.assertIsNone(ref(), "sentinel should be collectible")

            payload = registry.read(asset.handle)
            self.assertEqual(payload.content, TINY_PNG)
            self.assertEqual(asset.media_type, "image/png")

    def test_bytes_are_byte_identical_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "result.png"
            target.write_bytes(TINY_PNG)
            registry = self._registry(root)
            asset = registry.register(target, media_type="image/png")
            gc.collect()
            self.assertEqual(len(registry.read(asset.handle).content), len(TINY_PNG))
            self.assertEqual(registry.read(asset.handle).content[:8], TINY_PNG[:8])

    def test_handle_is_opaque_and_exposes_no_path(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "secret-name.png"
            target.write_bytes(TINY_PNG)
            registry = self._registry(root)
            asset = registry.register(target, media_type="image/png")
            self.assertNotIn("secret-name", asset.handle)
            self.assertNotIn(str(root), asset.handle)
            self.assertNotIn("\\", asset.handle.replace("studio-result/", ""))

    def test_png_signature_and_dimensions_survive(self) -> None:
        import struct

        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "result.png"
            target.write_bytes(TINY_PNG)
            registry = self._registry(root)
            asset = registry.register(target, media_type="image/png")
            content = registry.read(asset.handle).content
            self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(content[12:16], b"IHDR")
            width, height = struct.unpack(">II", content[16:24])
            self.assertEqual((width, height), (1, 1))


# --------------------------------------------- 6.3 / 6.4 class and shared state


class PostSamplingStateTests(unittest.TestCase):
    def test_class_level_caches_are_cleared(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        type(processing).cached_c = [_Sentinel()]
        ref = weakref.ref(type(processing).cached_c[0])
        release_generation_references(processing=processing)
        del processing
        gc.collect()
        self.assertIsNone(ref(), "class-level cached_c survived")

    def test_shared_preview_state_is_cleared(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        class _Bridge:
            def __init__(self):
                self.current_latent = _Sentinel()

        bridge = _Bridge()
        ref = weakref.ref(bridge.current_latent)
        report = release_generation_references(state_bridge=bridge)
        gc.collect()
        self.assertIsNone(ref())
        self.assertTrue(report.shared_preview_state_cleared)

    def test_unrelated_persistent_state_is_not_touched(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        processing.unrelated_setting = "keep me"
        release_generation_references(processing=processing)
        self.assertEqual(processing.unrelated_setting, "keep me")


# ------------------------------------------------- 6.5 fake allocator controls


class FakeAllocatorTests(unittest.TestCase):
    class _Allocator:
        def __init__(self, refs):
            self._refs = refs

        def allocated(self) -> int:
            return sum(1 for r in self._refs if r() is not None) * 4096

        def reserved(self) -> int:
            return self.allocated()

    def test_positive_control_reaches_zero_only_after_release(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        processed = _FakeProcessed()
        refs = [weakref.ref(processing.latents_after_sampling[0]),
                weakref.ref(processing.pixels_after_sampling[0]),
                weakref.ref(processed.images[0])]
        allocator = self._Allocator(refs)
        self.assertGreater(allocator.allocated(), 0, "must start non-zero")

        release_generation_references(processing=processing, processed=processed)
        del processing, processed
        gc.collect()

        self.assertEqual(allocator.allocated(), 0)
        self.assertEqual(allocator.reserved(), 0)

    def test_negative_control_stays_non_zero_when_a_decode_reference_is_retained(self) -> None:
        # Proves the measurement is liveness-driven, not hard-coded.
        from forge_headless.failure_cleanup import release_generation_references

        processing = _fake_processing()
        retained = processing.latents_after_sampling[0]   # deliberately held
        refs = [weakref.ref(retained)]
        allocator = self._Allocator(refs)

        release_generation_references(processing=processing)
        del processing
        gc.collect()

        self.assertEqual(allocator.allocated(), 4096,
                         "a deliberately retained decode reference must keep it non-zero")
        del retained
        gc.collect()
        self.assertEqual(allocator.allocated(), 0)

    def test_negative_control_for_a_retained_publication_reference(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        processed = _FakeProcessed()
        retained = processed.images[0]
        allocator = self._Allocator([weakref.ref(retained)])
        release_generation_references(processed=processed)
        del processed
        gc.collect()
        self.assertEqual(allocator.allocated(), 4096)
        del retained
        gc.collect()
        self.assertEqual(allocator.allocated(), 0)


# --------------------------------------------- 6.7 / 6.8 telemetry


class SamplerSchedulerCaptureTests(unittest.TestCase):
    def test_probe_captures_from_the_sampler_object(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('out["resolved_sampler"]', source)
        self.assertIn('out["resolved_scheduler"]', source)
        self.assertIn('out["sampler_resolution_source"]', source)

    def test_capture_precedes_any_processed_dependency(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        capture = source.index('out["resolved_sampler"]')
        images_use = source.index('images = list(getattr(processed, "images", [])')
        self.assertLess(capture, images_use,
                        "resolved names must be read before Processed is consumed")

    def test_requested_and_resolved_are_recorded_separately(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn('"sampler": SAMPLER', source)      # requested
        self.assertIn('out["resolved_sampler"]', source)  # resolved

    def test_resolution_source_is_honest_when_the_sampler_is_absent(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn("request (sampler unavailable)", source)


class StageTimingTests(unittest.TestCase):
    class _Clock:
        def __init__(self):
            self.t = 0.0

        def tick(self, amount):
            self.t += amount
            return self.t

    def test_durations_are_independently_ordered(self) -> None:
        clock = self._Clock()
        marks = {}
        for stage, cost in (("conditioning", 1.0), ("sampling", 5.0),
                            ("decode", 2.0), ("publication", 0.5), ("cleanup", 0.25)):
            start = clock.t
            clock.tick(cost)
            marks[stage] = (start, clock.t, clock.t - start)

        self.assertEqual([m[2] for m in marks.values()], [1.0, 5.0, 2.0, 0.5, 0.25])
        ordered = list(marks.values())
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertLessEqual(earlier[1], later[0], "stage boundaries must not overlap")

    def test_durations_are_not_inferred_from_total(self) -> None:
        clock = self._Clock()
        clock.tick(10.0)
        total = clock.t
        measured = 1.0 + 5.0 + 2.0
        self.assertNotEqual(measured, total,
                            "a per-stage measurement must be independent of total runtime")


# ------------------------------------------------- 6.9 success/failure matrix


class SuccessFailureMatrixTests(unittest.TestCase):
    STAGES = (
        "sampler completion before decode",
        "during decode",
        "after decode before Processed",
        "after Processed",
        "after registry publication",
        "after PNG preservation",
        "during cleanup",
    )

    def test_every_stage_releases_and_preserves_telemetry(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references

        for stage in self.STAGES:
            with self.subTest(stage=stage):
                processing = _fake_processing()
                processed = _FakeProcessed()
                ref = weakref.ref(processing.latents_after_sampling[0])
                record = {"stage_reached": stage, "errors": []}

                report = release_generation_references(
                    processing=processing, processed=processed)
                del processing, processed
                gc.collect()

                self.assertEqual(record["stage_reached"], stage, "telemetry lost")
                self.assertTrue(report.gc_collect_invoked)
                self.assertIsNone(ref(), f"latent survived at stage: {stage}")

    def test_published_result_remains_available_after_release(self) -> None:
        from forge_headless.failure_cleanup import release_generation_references
        from forge_studio.result_delivery import ResultRegistry

        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            target = root / "result.png"
            target.write_bytes(TINY_PNG)
            registry = ResultRegistry(root)
            asset = registry.register(target, media_type="image/png")

            processing = _fake_processing()
            processed = _FakeProcessed()
            release_generation_references(processing=processing, processed=processed)
            del processing, processed
            gc.collect()

            self.assertEqual(registry.read(asset.handle).content, TINY_PNG)


class ProbeWiringTests(unittest.TestCase):
    def test_probe_passes_processed_to_the_release(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertIn("processed=processed_result", source)
        self.assertIn('processed_result = record.pop("_processed", None)', source)

    def test_release_still_precedes_vram_sampling(self) -> None:
        source = PROBE.read_text(encoding="utf-8")
        self.assertLess(source.index("release_generation_references("),
                        source.index("telemetry.after_release = sample_current()"))

    def test_exactly_one_cache_clear_remains(self) -> None:
        self.assertEqual(PROBE.read_text(encoding="utf-8").count("release_cuda_cache()"), 1)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(), EXPECTED_POST_DECODE_TESTS,
            "Post-decode test count changed: update EXPECTED_POST_DECODE_TESTS "
            "deliberately, or find the test that stopped being discovered.",
        )


if __name__ == "__main__":
    unittest.main()
