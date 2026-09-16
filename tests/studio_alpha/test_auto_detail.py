"""Auto Detail orchestration: the rules, without a GPU.

`run_auto_detail` takes `detect` and `detail` as injected callables, so every
rule the pipeline has to obey is provable here with fakes -- slot order, a
disabled slot costing nothing, no detections being a SUCCESS, cancellation
boundaries, and what a result may record about any of it. Those are the parts
that can be silently wrong; the Neo inpaint call is the part that cannot be
silently wrong, because it either produces an image or raises.

Almost nothing here needs Pillow. `mask_preprocess` only reaches it when the
dilate/erode kernel is non-zero, so the fixtures use `dilate_erode=0` and the
masks are opaque sentinels -- which also proves the orchestrator never looks
inside a mask, and has no business doing so.

BOTH SIDES, EVERYWHERE
======================

A disabled slot must not run AND an enabled one must. A cancelled job must
stop AND an uncancelled one must finish all three slots. No detections must be
a success AND must not call the inpaint. Written the other way round, an
orchestrator that did nothing at all would pass half of this file.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU, no detector.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.auto_detail import (  # noqa: E402
    CANCELLED,
    DETECTOR_UNRESOLVED,
    MAX_SLOTS,
    SlotOutcome,
    SlotSpec,
    describe_outcomes,
    run_auto_detail,
)
from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.detector_adapter import DetectionResult  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_AUTO_DETAIL_TESTS = 46

FRAME = (100, 100)


def _slot(index: int, **overrides: Any) -> SlotSpec:
    fields: dict[str, Any] = {
        "index": index,
        "enabled": True,
        "detector": f"face_yolov8n-{index}.pt",
        "detector_path": f"/detectors/face_yolov8n-{index}.pt",
        # Zero keeps `mask_preprocess` on its pure-Python path: no Pillow, no
        # numpy, and no all-black drop, so a mask can stay an opaque sentinel.
        "dilate_erode": 0,
    }
    fields.update(overrides)
    return SlotSpec(**fields)


def _found(count: int, *, sides: list[int] | None = None) -> DetectionResult:
    edges = sides or [10] * count
    return DetectionResult(
        bboxes=[[0.0, 0.0, float(edge), float(edge)] for edge in edges[:count]],
        masks=[f"mask-{i}" for i in range(count)],
        confidences=[0.9] * count,
        image_size=FRAME,
    )


class _Recorder:
    """A `detect`/`detail` pair that records what it was asked to do."""

    def __init__(self, per_call: list[DetectionResult] | None = None) -> None:
        self.detect_calls: list[tuple[str, Any]] = []
        self.detail_calls: list[tuple[Any, Any, SlotSpec]] = []
        self._per_call = per_call
        self._n = 0

    def detect(self, path: str, image: Any, *, confidence: float) -> DetectionResult:
        self.detect_calls.append((path, image))
        if self._per_call is None:
            return _found(1)
        result = self._per_call[min(self._n, len(self._per_call) - 1)]
        self._n += 1
        return result

    def detail(self, image: Any, mask: Any, slot: SlotSpec) -> Any:
        self.detail_calls.append((image, mask, slot))
        return f"{image}+s{slot.index}"


def _run(slots, recorder: _Recorder, **kwargs: Any):
    return run_auto_detail(
        ["base"], slots, detect=recorder.detect, detail=recorder.detail, **kwargs
    )


# ----------------------------------------------------------------- ordering


class SlotOrderTests(unittest.TestCase):
    def test_the_pipeline_is_fixed_at_three_slots(self) -> None:
        self.assertEqual(3, MAX_SLOTS)

    def test_slots_run_in_index_order_however_they_arrive(self) -> None:
        recorder = _Recorder()
        _run([_slot(3), _slot(1), _slot(2)], recorder)
        self.assertEqual(
            [1, 2, 3], [call[2].index for call in recorder.detail_calls]
        )

    def test_each_slot_details_what_the_previous_one_produced(self) -> None:
        """A pipeline, not three independent edits of the same frame. Without
        this, slot 3's output would silently discard slots 1 and 2."""

        recorder = _Recorder()
        images, _ = _run([_slot(1), _slot(2), _slot(3)], recorder)
        self.assertEqual(["base+s1+s2+s3"], images)

    def test_the_returned_images_replace_rather_than_accumulate(self) -> None:
        recorder = _Recorder()
        images, _ = _run([_slot(1)], recorder)
        self.assertEqual(1, len(images))


# ----------------------------------------------------------------- disabled


class DisabledSlotTests(unittest.TestCase):
    def test_a_disabled_slot_never_detects(self) -> None:
        """A true no-op: no detector resolved, nothing loaded, no pass run.
        This is what keeps three slots from costing three detector loads."""

        recorder = _Recorder()
        _run([_slot(1, enabled=False)], recorder)
        self.assertEqual([], recorder.detect_calls)

    def test_a_disabled_slot_never_details(self) -> None:
        recorder = _Recorder()
        _run([_slot(1, enabled=False)], recorder)
        self.assertEqual([], recorder.detail_calls)

    def test_a_disabled_slot_records_no_outcome(self) -> None:
        """An outcome would put a detector name in the metadata of a result
        that detector never touched."""

        recorder = _Recorder()
        _, outcomes = _run([_slot(1, enabled=False), _slot(2)], recorder)
        self.assertEqual([2], [outcome.index for outcome in outcomes])

    def test_an_enabled_slot_beside_it_still_runs(self) -> None:
        """The other side. An orchestrator that ran nothing would satisfy
        every assertion above."""

        recorder = _Recorder()
        images, _ = _run([_slot(1, enabled=False), _slot(2)], recorder)
        self.assertEqual(["base+s2"], images)

    def test_one_two_and_three_enabled_slots_each_run_that_many(self) -> None:
        for count in (1, 2, 3):
            with self.subTest(enabled=count):
                recorder = _Recorder()
                slots = [
                    _slot(index, enabled=index <= count)
                    for index in (1, 2, 3)
                ]
                _, outcomes = _run(slots, recorder)
                self.assertEqual(count, len(outcomes))


# ------------------------------------------------------------ no detections


class NoDetectionTests(unittest.TestCase):
    def test_no_detections_is_a_successful_outcome(self) -> None:
        """Not a failure. There was nothing of that kind in the picture,
        which is a fact about the picture."""

        recorder = _Recorder(per_call=[_found(0)])
        _, outcomes = _run([_slot(1)], recorder)
        self.assertEqual(1, len(outcomes))
        self.assertEqual(0, outcomes[0].candidates)

    def test_no_detections_runs_no_inpaint(self) -> None:
        """No fake inpaint. A pass over an empty mask is a no-op that reports
        success, which is worse than not running."""

        recorder = _Recorder(per_call=[_found(0)])
        _run([_slot(1)], recorder)
        self.assertEqual([], recorder.detail_calls)

    def test_no_detections_leaves_the_image_untouched(self) -> None:
        recorder = _Recorder(per_call=[_found(0)])
        images, _ = _run([_slot(1)], recorder)
        self.assertEqual(["base"], images)

    def test_the_outcome_says_it_did_not_detail(self) -> None:
        recorder = _Recorder(per_call=[_found(0)])
        _, outcomes = _run([_slot(1)], recorder)
        self.assertFalse(outcomes[0].detailed)

    def test_the_pipeline_continues_to_the_next_slot(self) -> None:
        recorder = _Recorder(per_call=[_found(0), _found(1)])
        images, outcomes = _run([_slot(1), _slot(2)], recorder)
        self.assertEqual(["base+s2"], images)
        self.assertEqual(2, len(outcomes))


# ------------------------------------------------------------- the counters


class FilteringTests(unittest.TestCase):
    def test_candidates_counts_detections_before_filtering(self) -> None:
        recorder = _Recorder(per_call=[_found(4)])
        _, outcomes = _run([_slot(1)], recorder)
        self.assertEqual(4, outcomes[0].candidates)

    def test_regions_counts_what_survived_filtering(self) -> None:
        """Both numbers are kept because their DIFFERENCE is the only
        evidence the owner's filter settings did anything."""

        recorder = _Recorder(per_call=[_found(4)])
        _, outcomes = _run([_slot(1, top_k=2)], recorder)
        self.assertEqual(4, outcomes[0].candidates)
        self.assertEqual(2, outcomes[0].regions)

    def test_top_k_limits_how_many_regions_are_detailed(self) -> None:
        recorder = _Recorder(per_call=[_found(4)])
        _run([_slot(1, top_k=2)], recorder)
        self.assertEqual(2, len(recorder.detail_calls))

    def test_top_k_zero_keeps_every_region(self) -> None:
        """The other side of the same control."""

        recorder = _Recorder(per_call=[_found(4)])
        _run([_slot(1, top_k=0)], recorder)
        self.assertEqual(4, len(recorder.detail_calls))

    def test_the_ratio_filter_drops_regions_outside_the_range(self) -> None:
        # 10x10 and 90x90 on a 100x100 frame: 1% and 81% of it.
        recorder = _Recorder(per_call=[_found(2, sides=[10, 90])])
        _, outcomes = _run([_slot(1, min_ratio=0.5, max_ratio=1.0)], recorder)
        self.assertEqual(2, outcomes[0].candidates)
        self.assertEqual(1, outcomes[0].regions)

    def test_a_permissive_ratio_range_keeps_both(self) -> None:
        recorder = _Recorder(per_call=[_found(2, sides=[10, 90])])
        _, outcomes = _run([_slot(1, min_ratio=0.0, max_ratio=1.0)], recorder)
        self.assertEqual(2, outcomes[0].regions)

    def test_one_inpaint_pass_per_region(self) -> None:
        recorder = _Recorder(per_call=[_found(3)])
        _run([_slot(1)], recorder)
        self.assertEqual(3, len(recorder.detail_calls))


# ----------------------------------------------------------- cancellation


class CancellationTests(unittest.TestCase):
    def test_a_cancelled_job_stops_before_the_first_slot(self) -> None:
        recorder = _Recorder()
        with self.assertRaises(HeadlessError) as raised:
            _run([_slot(1)], recorder, cancelled=lambda: True)
        self.assertEqual(CANCELLED, raised.exception.code)
        self.assertEqual([], recorder.detect_calls)

    def test_cancelling_between_slots_stops_the_later_ones(self) -> None:
        recorder = _Recorder()
        seen: list[int] = []

        def cancelled() -> bool:
            return len(seen) >= 1

        def on_slot_start(slot: SlotSpec) -> None:
            seen.append(slot.index)

        with self.assertRaises(HeadlessError):
            _run(
                [_slot(1), _slot(2), _slot(3)],
                recorder,
                cancelled=cancelled,
                on_slot_start=on_slot_start,
            )
        self.assertEqual([1], seen)

    def test_cancelling_stops_the_remaining_regions_in_a_slot(self) -> None:
        """Checked BETWEEN regions, so a slot with many detections cancels in
        bounded time rather than after the pass it is in."""

        recorder = _Recorder(per_call=[_found(4)])
        with self.assertRaises(HeadlessError):
            _run(
                [_slot(1)],
                recorder,
                cancelled=lambda: len(recorder.detail_calls) >= 2,
            )
        self.assertEqual(2, len(recorder.detail_calls))

    def test_an_uncancelled_job_runs_every_slot(self) -> None:
        """The other side. Always-cancel would pass every test above."""

        recorder = _Recorder()
        _, outcomes = _run([_slot(1), _slot(2), _slot(3)], recorder)
        self.assertEqual(3, len(outcomes))


# ------------------------------------------------------------- the detector


class DetectorResolutionTests(unittest.TestCase):
    def test_an_unresolved_detector_is_refused(self) -> None:
        recorder = _Recorder()
        with self.assertRaises(HeadlessError) as raised:
            _run([_slot(1, detector_path="")], recorder)
        self.assertEqual(DETECTOR_UNRESOLVED, raised.exception.code)

    def test_it_is_refused_before_anything_is_detected(self) -> None:
        """Refused here rather than allowed to fail inside a detector load
        that has already cost the base pass."""

        recorder = _Recorder()
        with self.assertRaises(HeadlessError):
            _run([_slot(1, detector_path="")], recorder)
        self.assertEqual([], recorder.detect_calls)

    def test_a_disabled_slot_with_no_detector_is_not_refused(self) -> None:
        """The no-op wins. An owner who never enabled slot 3 has no reason to
        have chosen a detector for it."""

        recorder = _Recorder()
        _, outcomes = _run(
            [_slot(1), _slot(3, enabled=False, detector="", detector_path="")],
            recorder,
        )
        self.assertEqual([1], [outcome.index for outcome in outcomes])


# ------------------------------------------------------------ stage report


class StageReportingTests(unittest.TestCase):
    def test_each_enabled_slot_announces_itself_once_in_order(self) -> None:
        recorder = _Recorder()
        seen: list[int] = []
        _run(
            [_slot(1), _slot(2), _slot(3)],
            recorder,
            on_slot_start=lambda slot: seen.append(slot.index),
        )
        self.assertEqual([1, 2, 3], seen)

    def test_a_disabled_slot_announces_nothing(self) -> None:
        recorder = _Recorder()
        seen: list[int] = []
        _run(
            [_slot(1, enabled=False), _slot(2)],
            recorder,
            on_slot_start=lambda slot: seen.append(slot.index),
        )
        self.assertEqual([2], seen)


# ---------------------------------------------------------------- metadata


class MetadataTests(unittest.TestCase):
    def test_nothing_ran_means_no_section_at_all(self) -> None:
        """Absence is the claim being made, exactly as it is for Hires: it is
        what lets presence answer "was this detailed?"."""

        self.assertEqual({}, describe_outcomes(()))

    def test_a_slot_that_ran_is_described_by_safe_facts(self) -> None:
        outcome = SlotOutcome(
            index=1, detector="face_yolov8n.pt", candidates=4,
            regions=2, detailed=True,
        )
        self.assertEqual(
            {
                "slot": 1,
                "detector": "face_yolov8n.pt",
                "candidates": 4,
                "regions": 2,
                "detailed": True,
            },
            outcome.describe(),
        )

    def test_the_metadata_carries_no_path_and_no_prompt(self) -> None:
        recorder = _Recorder(per_call=[_found(2)])
        _, outcomes = _run(
            [_slot(1, prompt="a secret phrase", negative_prompt="another")],
            recorder,
        )
        rendered = repr(describe_outcomes(outcomes))
        self.assertNotIn("secret", rendered)
        self.assertNotIn("/detectors/", rendered)


# -------------------------------------------------------- source discipline


class SourceDisciplineTests(unittest.TestCase):
    MODULE = (APP_ROOT / "forge_headless" / "auto_detail.py").read_text(
        encoding="utf-8"
    )

    @staticmethod
    def _code_only(source: str) -> str:
        """Comments and docstrings stripped.

        This file DESCRIBES what it refuses to do, by name. A raw search finds
        the forbidden word in the sentence forbidding it -- the trap
        OPERATIONS.md section 7 records, and which this project has now hit
        seven times.
        """

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
        return ast.unparse(tree)

    def test_no_heavy_runtime_is_imported_at_module_scope(self) -> None:
        """It must stay importable under `-I -S -B`, where site-packages is
        absent, so the rules above can be proven in discovery mode."""

        roots: set[str] = set()
        for node in ast.parse(self.MODULE).body:
            if isinstance(node, ast.Import):
                roots.update(a.name.partition(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        for banned in ("torch", "torchvision", "numpy", "PIL", "ultralytics"):
            self.assertNotIn(banned, roots)

    def test_it_does_not_reach_for_upstreams_download_helpers(self) -> None:
        code = self._code_only(self.MODULE)
        for banned in ("hf_download", "download_models", "get_models",
                       "huggingface_hub", "hf_hub_download"):
            self.assertNotIn(banned, code)

    def test_it_does_not_use_the_annotated_debug_render(self) -> None:
        self.assertNotIn(".plot()", self._code_only(self.MODULE))

    def test_it_publishes_nothing(self) -> None:
        """Only `_publish` may produce a public result. An intermediate AD
        pass is not a result."""

        code = self._code_only(self.MODULE)
        for banned in ("save_result_exclusively", "GenerationOutcome",
                       "result_relative_location"):
            self.assertNotIn(banned, code)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_AUTO_DETAIL_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()


# ----------------------------------------------------- the port's own wiring


class PortWiringTests(unittest.TestCase):
    """`live_generation_port` calls the orchestrator, between the two lines.

    Source assertions, because the alternative needs a GPU and a model. What
    they pin is the ORDER: Auto Detail must run after the images exist and
    before anything is published, or an intermediate pass becomes a result.
    """

    PORT = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
        encoding="utf-8"
    )

    def _code(self) -> str:
        return SourceDisciplineTests._code_only(self.PORT)

    def test_the_orchestrator_is_called(self) -> None:
        self.assertIn("run_auto_detail", self._code())

    def test_it_runs_after_the_images_exist(self) -> None:
        code = self._code()
        self.assertLess(
            code.find("process_images_inner(processing)"),
            code.find("self._auto_detail("),
        )

    def test_it_runs_before_anything_is_published(self) -> None:
        """The whole reason the seam is where it is: only `_publish` may
        produce a public result, so an intermediate pass must precede it."""

        code = self._code()
        self.assertLess(
            code.find("self._auto_detail("),
            code.find("outcome = self._publish("),
        )

    def test_the_mask_is_passed_as_mask_not_image_mask(self) -> None:
        """`image_mask` is `init=False`; `__post_init__` moves `mask` into it.
        Naming it directly is a TypeError, not a different spelling."""

        code = self._code()
        self.assertIn("mask=mask", code)
        self.assertNotIn("image_mask=", code)

    def test_the_detail_pass_keeps_the_original_masked_content(self) -> None:
        """`inpainting_fill=1`. The default, 0, FILLS the region first and
        discards the face the pass was asked to improve."""

        self.assertIn("inpainting_fill=1", self._code())

    def test_the_outcomes_travel_on_the_outcome_not_the_port(self) -> None:
        """The port is per-session and outlives the job. Reading detections
        off it is the `_in_flight_job_id` shape, which reported the previous
        job's progress under a new job's id in production."""

        self.assertIn("auto_detail=tuple(", self._code())

    def test_a_job_with_no_slots_does_not_enter_the_pipeline(self) -> None:
        """Absent stays absent: no stage advance, no detector, no metadata."""

        self.assertRegex(
            self._code(), r"if not getattr\(request, .enable_adetailer., False\)"
        )
