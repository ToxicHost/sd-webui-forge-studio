"""Whether a tiled upscale may composite on the device, and why.

GPU tile compositing is 32% faster on the upscale step and about 2 s faster on
a warm full pipeline. It buys that by holding the whole upscaled frame on the
device instead of one tile at a time, so its device cost grows with the square
of the base resolution while the CPU path's does not grow at all:

    base        CPU peak     GPU peak     delta
    512^2        1024.9M      1095.1M     +70.2M
    1024^2       1024.9M      1296.1M    +271.2M
    1536^2       1024.9M      1631.1M    +606.2M
    2048^2       1024.9M      2100.1M   +1075.2M

The reservation that was supposed to cover this reserved +15.4 MiB, computed
from the tile size, with no image-size term at all. So the flag was safe on
the machine it was measured on and unsafe on a smaller card, and nothing in
the code knew the difference.

This suite covers the replacement: an estimate built from runtime values, and
a decision that prefers the fast path only when the frame demonstrably fits.

The estimate lives in `forge_headless/upscale_preflight.py` as pure arithmetic
with no torch, no numpy and no PIL, specifically so it can be tested here. The
canonical suite runs every test in ONE process and `test_import_boundaries`
asserts torch is absent from `sys.modules`, so a suite that imported torch --
even a synthetic stand-in left behind -- would fail a suite it never read.

SCOPE: the working-set formula, the fit decision, and the reasons reported for
each outcome. Not the torch-side glue in `modules/esrgan_model.py`, which
needs a device and is covered by evidence runs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from forge_headless.upscale_preflight import (
    MAX_TILES_WITHOUT_ACCUMULATOR,
    MODE_CPU,
    MODE_GPU,
    REASON_ALLOCATION_FALLBACK,
    REASON_FIT,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_LAUNCH_DISABLED,
    REASON_OWNER_DISABLED,
    REASON_UNSUPPORTED_BACKEND,
    REASONS,
    choose_composite_mode,
    composite_working_set,
    disable_for_process,
    process_disabled,
    describe_decision,
    tile_count,
)


EXPECTED_UPSCALE_PREFLIGHT_TESTS = 43

APP_ROOT = Path(__file__).resolve().parents[2]

MiB = 1024 * 1024

#: Measured on the reference machine: RTX 5060 Ti, remacri (native 4x), fp32,
#: tile 256 overlap 16, warm allocator, `torch.cuda.max_memory_allocated`
#: against a post-`empty_cache` baseline. Base edge -> measured marginal cost
#: of the GPU composite over the CPU composite, in MiB.
MEASURED_DELTA_MIB = {
    512: 70.2,
    1024: 271.2,
    1536: 606.2,
    1792: 824.0,
    2048: 1075.2,
    2304: 1360.0,
    2560: 1678.2,
}

REFERENCE_SCALE = 4
REFERENCE_ITEMSIZE = 4
REFERENCE_TILE = 256
REFERENCE_OVERLAP = 16


def _working_set(edge, scale=REFERENCE_SCALE, itemsize=REFERENCE_ITEMSIZE):
    return composite_working_set(
        width=edge,
        height=edge,
        scale=scale,
        itemsize=itemsize,
        tiles=tile_count(edge, edge, REFERENCE_TILE, REFERENCE_OVERLAP),
    )


class TileCountTests(unittest.TestCase):
    """The accumulator only exists when the compositor decides to tile."""

    def test_it_reproduces_the_tile_count_the_reference_run_reported(self) -> None:
        # The reference log shows 25 tiles for a 1024x1024 base at tile 256
        # with overlap 16. If this arithmetic drifts from
        # `upscale_tensor_tiles`, the estimate silently starts sizing an
        # accumulator for a loop that is not running.
        self.assertEqual(25, tile_count(1024, 1024, 256, 16))

    def test_no_tiling_when_the_tile_size_is_zero(self) -> None:
        self.assertEqual(0, tile_count(1024, 1024, 0, 16))

    def test_no_tiling_when_the_overlap_swallows_the_tile(self) -> None:
        # Stride <= 0 would step nowhere and loop forever. The compositor
        # treats it as untiled and so must the estimate.
        self.assertEqual(0, tile_count(1024, 1024, 16, 16))

    def test_a_rectangle_counts_both_axes_independently(self) -> None:
        self.assertEqual(tile_count(1024, 512, 256, 16), 5 * 3)


class WorkingSetTests(unittest.TestCase):
    """The formula, against the numbers it was derived from."""

    def test_the_accumulator_is_four_channels_of_the_native_output(self) -> None:
        self.assertEqual(256 * MiB, _working_set(1024)["accumulator"])

    def test_the_accumulator_grows_with_the_square_of_the_base_edge(self) -> None:
        self.assertEqual(64 * MiB, _working_set(512)["accumulator"])
        self.assertEqual(576 * MiB, _working_set(1536)["accumulator"])

    def test_the_accumulator_grows_with_the_square_of_the_native_scale(self) -> None:
        # An 8x upscaler asks for four times what a 4x asks at the same input.
        four = _working_set(1024, scale=4)["accumulator"]
        eight = _working_set(1024, scale=8)["accumulator"]
        self.assertEqual(4 * four, eight)

    def test_half_precision_halves_the_accumulator(self) -> None:
        full = _working_set(1024, itemsize=4)["accumulator"]
        half = _working_set(1024, itemsize=2)["accumulator"]
        self.assertEqual(full // 2, half)

    def test_there_is_no_accumulator_when_the_image_is_not_tiled(self) -> None:
        # `upscale_tensor_tiles` puts the whole image through the model in one
        # pass at or below this many tiles, and allocates nothing to blend
        # into.
        small = composite_working_set(
            width=256, height=256, scale=4, itemsize=4,
            tiles=MAX_TILES_WITHOUT_ACCUMULATOR)
        self.assertEqual(0, small["accumulator"])

    def test_the_input_tensor_is_counted_separately(self) -> None:
        # It is the residual that closed the model: accumulator alone
        # under-predicted every measured row by exactly this term.
        self.assertEqual(3 * 1024 * 1024 * 4, _working_set(1024)["input_tensor"])

    def test_the_conversion_copy_is_counted_separately(self) -> None:
        # One byte per channel per output pixel. The float copy that used to
        # sit beside it -- four times this -- is gone.
        working_set = _working_set(1024)
        self.assertEqual(3 * 4096 * 4096, working_set["uint8_copy"])
        self.assertNotIn("bgr_copy", working_set)

    def test_the_peak_covers_the_conversion_phase_not_only_the_tile_loop(self) -> None:
        """The two phases still do not hold the same buffers.

        The value `upscale_tensor_tiles` returns is a VIEW into the
        accumulator, so the accumulator is alive while the frame is converted
        for the host. The conversion no longer dominates -- moving the channel
        swap to the host removed the float copy that made it dominate -- but
        the estimate should not depend on that staying true.
        """
        working_set = _working_set(2560)
        self.assertGreater(working_set["conversion"], working_set["tile_loop"])
        self.assertEqual(working_set["conversion"], working_set["peak"])


class MeasuredAgreementTests(unittest.TestCase):
    """The formula against the hardware it was validated on."""

    def test_the_estimate_never_under_reserves_a_measured_case(self) -> None:
        # The safety property. If this fails the preflight is promising
        # headroom that the allocator will not honour.
        for edge, measured in sorted(MEASURED_DELTA_MIB.items()):
            with self.subTest(base=edge):
                self.assertGreaterEqual(
                    _working_set(edge)["peak"] / MiB, measured)

    def test_the_tile_loop_term_matches_measurement_to_a_constant(self) -> None:
        """The measured cost IS the tile-loop term, everywhere.

        The residual is 3.2 MiB at every one of these resolutions -- constant,
        not a trend, which is what says the model has no missing size-dependent
        term rather than a lucky fit.

        It used to hold only up to 2048: above that the conversion phase
        overtook the tile loop and the measured cost jumped. Removing the
        float copy from the conversion removed the crossover, so one law now
        covers the whole range.
        """
        for edge in sorted(MEASURED_DELTA_MIB):
            with self.subTest(base=edge):
                residual = (
                    MEASURED_DELTA_MIB[edge] - _working_set(edge)["tile_loop"] / MiB)
                self.assertAlmostEqual(3.2, residual, delta=0.05)

    def test_the_estimate_is_conservative_without_being_wasteful(self) -> None:
        """Section 31. Over-reserving disables the fast path for no reason.

        Before the conversion copy was removed the estimate ran about 87% over
        the measured marginal, because it reserved a float frame that the
        allocator was holding only briefly. It is now a flat ~17%.
        """
        for edge, measured in sorted(MEASURED_DELTA_MIB.items()):
            with self.subTest(base=edge):
                over = (_working_set(edge)["peak"] / MiB - measured) / measured
                self.assertLess(over, 0.25)


class DecisionTests(unittest.TestCase):
    """Which path runs, and the reason reported for it."""

    def test_the_owner_switch_off_wins_before_anything_is_measured(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=False,
            device_reports_free_memory=True,
            free_bytes=64 * 1024 * MiB,
            reserve_bytes=0,
            working_set_bytes=1,
        )
        self.assertEqual((MODE_CPU, REASON_OWNER_DISABLED), (mode, reason))

    def test_a_device_that_cannot_answer_falls_back(self) -> None:
        """`get_free_memory` answers for every device; not every answer is VRAM.

        It returns host RAM on CPU and MPS and a hardcoded 1 GiB on DirectML,
        so it is confidently wrong rather than unavailable. Trusting it there
        would size a device allocation against the wrong pool.
        """
        mode, reason = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=False,
            free_bytes=64 * 1024 * MiB,
            reserve_bytes=0,
            working_set_bytes=1,
        )
        self.assertEqual((MODE_CPU, REASON_UNSUPPORTED_BACKEND), (mode, reason))

    def test_room_to_spare_takes_the_fast_path(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=9000 * MiB,
            reserve_bytes=1519 * MiB,
            working_set_bytes=508 * MiB,
        )
        self.assertEqual((MODE_GPU, REASON_FIT), (mode, reason))

    def test_too_little_headroom_falls_back(self) -> None:
        # The reference constrained run: 2524 MB free, 1519 MB reserve,
        # 1143 MB working set. 1005 < 1143.
        mode, reason = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=2524 * MiB,
            reserve_bytes=1519 * MiB,
            working_set_bytes=1143 * MiB,
        )
        self.assertEqual((MODE_CPU, REASON_INSUFFICIENT_HEADROOM), (mode, reason))

    def test_the_exact_boundary_is_allowed(self) -> None:
        mode, _ = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=2000,
            reserve_bytes=1000,
            working_set_bytes=1000,
        )
        self.assertEqual(MODE_GPU, mode)

    def test_one_byte_inside_the_boundary_falls_back(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=2000,
            reserve_bytes=1000,
            working_set_bytes=1001,
        )
        self.assertEqual((MODE_CPU, REASON_INSUFFICIENT_HEADROOM), (mode, reason))

    def test_the_reserve_participates_in_the_decision(self) -> None:
        """The old bug, as a test.

        The previous accounting reserved tile memory and nothing else, so the
        decision was blind to the margin the rest of the generation needs.
        Same free memory, same working set, opposite answers.
        """
        common = dict(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=2000 * MiB,
            working_set_bytes=1500 * MiB,
        )
        self.assertEqual(
            MODE_GPU, choose_composite_mode(reserve_bytes=0, **common)[0])
        self.assertEqual(
            MODE_CPU, choose_composite_mode(reserve_bytes=1000 * MiB, **common)[0])

    def test_it_never_raises_on_absurd_input(self) -> None:
        # The decision sits in the middle of a generation. A preflight that
        # threw would turn a memory question into a failed job.
        mode, _ = choose_composite_mode(
            requested_gpu=True,
            device_reports_free_memory=True,
            free_bytes=0,
            reserve_bytes=0,
            working_set_bytes=0,
        )
        self.assertIn(mode, (MODE_GPU, MODE_CPU))


class PrecedenceTests(unittest.TestCase):
    """The hierarchy, from the top down. Section 21.

        1  process-level hard disable
        2  capability
        3  the job's preference
        4  the memory preflight
    """

    ROOMY = dict(free_bytes=9000 * MiB, reserve_bytes=1519 * MiB,
                 working_set_bytes=316 * MiB)

    def test_the_launch_flag_beats_a_job_that_asked_for_gpu(self) -> None:
        """The inversion this replaces.

        The job's preference is read FIRST inside
        `HeadlessOptions.__getattr__`, so without a rung above it the process
        ceiling would be the one thing a job could climb over.
        """
        mode, reason = choose_composite_mode(
            requested_gpu=True, device_reports_free_memory=True,
            process_disabled=True, **self.ROOMY)
        self.assertEqual((MODE_CPU, REASON_LAUNCH_DISABLED), (mode, reason))

    def test_the_launch_flag_beats_a_fitting_frame(self) -> None:
        mode, _ = choose_composite_mode(
            requested_gpu=True, device_reports_free_memory=True,
            process_disabled=True,
            free_bytes=64 * 1024 * MiB, reserve_bytes=0, working_set_bytes=1)
        self.assertEqual(MODE_CPU, mode)

    def test_capability_beats_the_job_preference(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=True, device_reports_free_memory=False,
            process_disabled=False, **self.ROOMY)
        self.assertEqual((MODE_CPU, REASON_UNSUPPORTED_BACKEND), (mode, reason))

    def test_the_job_preference_beats_the_memory_preflight(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=False, device_reports_free_memory=True,
            process_disabled=False, **self.ROOMY)
        self.assertEqual((MODE_CPU, REASON_OWNER_DISABLED), (mode, reason))

    def test_a_permitted_job_with_room_gets_the_fast_path(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=True, device_reports_free_memory=True,
            process_disabled=False, **self.ROOMY)
        self.assertEqual((MODE_GPU, REASON_FIT), (mode, reason))

    def test_a_permitted_job_without_room_falls_back(self) -> None:
        mode, reason = choose_composite_mode(
            requested_gpu=True, device_reports_free_memory=True,
            process_disabled=False,
            free_bytes=2524 * MiB, reserve_bytes=1519 * MiB,
            working_set_bytes=1143 * MiB)
        self.assertEqual((MODE_CPU, REASON_INSUFFICIENT_HEADROOM), (mode, reason))

    def test_the_ceiling_is_off_unless_the_launcher_sets_it(self) -> None:
        self.assertFalse(process_disabled())

    def test_the_ceiling_is_set_and_cleared_by_the_launcher_alone(self) -> None:
        # Module state, not an option: an option is exactly what a job can
        # override. Restored so the shared process is left as it was found.
        try:
            disable_for_process(True)
            self.assertTrue(process_disabled())
        finally:
            disable_for_process(False)
        self.assertFalse(process_disabled())

    def test_the_launcher_sets_the_ceiling_at_boot(self) -> None:
        source = (APP_ROOT / "forge_headless" / "backend_bootstrap.py").read_text(
            encoding="utf-8")
        self.assertIn(
            "disable_for_process(not runtime.composite_tiles_on_gpu)", source)

    def test_the_upscaler_reads_the_ceiling_from_the_module_not_the_options(self) -> None:
        source = (APP_ROOT / "modules" / "esrgan_model.py").read_text(
            encoding="utf-8")
        self.assertIn("process_disabled=preflight.process_disabled()", source)


class DiagnosticTests(unittest.TestCase):
    """What the log says happened."""

    def test_the_line_names_the_request_the_outcome_and_the_cause(self) -> None:
        line = describe_decision(
            requested="gpu", effective="cpu",
            reason=REASON_INSUFFICIENT_HEADROOM,
            working_set_bytes=1143 * MiB,
            free_bytes=2524 * MiB,
            reserve_bytes=1519 * MiB)
        self.assertIn("requested=gpu", line)
        self.assertIn("effective=cpu", line)
        self.assertIn(f"reason={REASON_INSUFFICIENT_HEADROOM}", line)
        self.assertIn("working_set=1143.0MB", line)

    def test_the_line_carries_no_paths(self) -> None:
        line = describe_decision(
            requested="gpu", effective="gpu", reason=REASON_FIT,
            working_set_bytes=1, free_bytes=2, reserve_bytes=3)
        for fragment in ("\\", "/", ":\\", "C:"):
            self.assertNotIn(fragment, line)

    def test_every_reason_the_module_can_report_is_declared(self) -> None:
        self.assertEqual(
            {REASON_FIT, REASON_OWNER_DISABLED, REASON_UNSUPPORTED_BACKEND,
             REASON_INSUFFICIENT_HEADROOM, REASON_ALLOCATION_FALLBACK,
             REASON_LAUNCH_DISABLED},
            set(REASONS))


class CallerContractTests(unittest.TestCase):
    """The glue this policy depends on, asserted at the source."""

    def test_the_upscaler_passes_an_effective_mode_rather_than_reading_the_option(self) -> None:
        """The option is a preference; the decision is per image.

        `upscale_with_model` reading `shared.opts` directly is what made the
        flag unsafe: the same option produced a 70 MiB allocation on one image
        and a 1075 MiB allocation on another, with nothing in between to say
        no.
        """
        source = (APP_ROOT / "modules" / "esrgan_model.py").read_text(
            encoding="utf-8")
        self.assertIn("composite_on_gpu=self.composite_on_gpu_for(img,model)",
                      source.replace(" ", "").replace("\n", ""))

    def test_the_discarded_reservation_factor_is_gone(self) -> None:
        # 1.1x of a tile-sized number, against a cost that is image-sized and
        # quadratic. Leaving it would double-count a term that never covered
        # anything.
        source = (APP_ROOT / "modules" / "esrgan_model.py").read_text(
            encoding="utf-8")
        self.assertNotIn("1.1 if opts.composite_tiles_on_gpu", source)

    def test_the_conversion_does_not_copy_the_frame_in_float(self) -> None:
        """Section 22/52: the storage-lifetime property, as a guard.

        `tensor[:, [2, 1, 0], ...]` is advanced indexing, so putting the
        channel swap before the elementwise steps copied the whole frame in
        float while the accumulator it viewed was still alive. That copy was
        1200 MiB at a 2560 base and made the conversion the peak of the entire
        upscale. The swap belongs on the host, after the uint8 narrowing.

        Byte-identical either way -- elementwise operations commute with a
        permutation of the channel axis -- which is why this is a memory guard
        and not an output guard.
        """
        source = (APP_ROOT / "modules" / "upscaler_utils.py").read_text(
            encoding="utf-8")
        body = source.split("def tensor_bgr_to_pil_rgb")[1].split(chr(10) + "def ")[0]
        self.assertNotIn("[:, [2, 1, 0], ...]", body)
        self.assertIn("[:, :, ::-1]", body)

    def test_the_conversion_runs_in_the_mode_its_input_was_built_in(self) -> None:
        """Working in place on the accumulator needs inference mode.

        `upscale_tensor_tiles` is decorated `@torch.inference_mode()`, so what
        it returns is an inference tensor and torch refuses in-place updates
        to one from outside. The old conversion never noticed, because its
        first act was to copy the frame into a normal tensor -- the very copy
        this removed.
        """
        source = (APP_ROOT / "modules" / "upscaler_utils.py").read_text(
            encoding="utf-8")
        body = source.split("def upscale_with_model_gpu")[1].split(chr(10) + "def ")[0]
        self.assertIn("with torch.inference_mode():", body)

    def test_the_allocation_fallback_catches_only_allocation_failures(self) -> None:
        """Section 13: a known resource failure, not arbitrary exceptions.

        A broad catch here would report an upscaler bug as a memory fallback
        and quietly produce a different image instead of failing.
        """
        source = (APP_ROOT / "modules" / "esrgan_model.py").read_text(
            encoding="utf-8")
        self.assertIn("except _ALLOCATION_ERRORS:", source)
        self.assertNotIn("except Exception:  # composite", source)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_UPSCALE_PREFLIGHT_TESTS, suite.countTestCases())

    def test_neither_this_suite_nor_the_policy_imports_an_accelerator_library(self) -> None:
        """Importing torch here would fail a suite this one never reads.

        `test_import_boundaries` asserts torch is absent from `sys.modules`,
        and the canonical run loads every test module into one process before
        the first test executes -- so filename ordering is no protection, and
        a synthetic stand-in left behind counts too.

        Checked by parsing imports rather than by inspecting `sys.modules`,
        which would fail this suite for somebody else's leak.
        """
        import ast

        forbidden = {"torch", "numpy", "PIL"}
        sources = (
            Path(__file__).resolve(),
            APP_ROOT / "forge_headless" / "upscale_preflight.py",
        )
        for source_path in sources:
            with self.subTest(source=source_path.name):
                tree = ast.parse(source_path.read_text(encoding="utf-8"))
                roots = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        roots.update(alias.name.split(".")[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        roots.add((node.module or "").split(".")[0])
                self.assertEqual(set(), roots & forbidden)


if __name__ == "__main__":
    unittest.main()
