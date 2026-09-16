"""The Auto Detail detector adapter, and the offline boundary it runs behind.

Three prohibited behaviours ship enabled in `ultralytics==8.3.119`, and one of
them runs before any Studio code could object:

```text
utils/__init__.py:844   ONLINE = is_online()          at IMPORT
utils/__init__.py:1243  SETTINGS["sync"] default True
utils/checks.py:273     check_pip_update_available()
```

THE CONTROL IS THE WHOLE TEST
=============================

`ONLINE` is False in BOTH of these situations:

```text
boundary configured   is_online() returns before `import socket` -- the
                      YOLO_OFFLINE assert fires first. No call is made.
boundary absent       is_online() calls socket.create_connection, the call
                      FAILS under a guard, and the except returns False.
```

Same observable value, opposite meanings. So asserting `ONLINE is False`
proves nothing at all, and the suite asserts on the ATTEMPT COUNT instead --
measured both ways, in subprocesses, so the unguarded control is real rather
than assumed. This codebase has been caught by that exact shape before: "it
did not raise" is not "it did nothing", and a determinism control is required
before any "different outcome proves it worked" claim.

WHY SUBPROCESSES
================

Importing ultralytics drags torch into the process. `test_import_boundaries`
purges `forge_studio` and asserts a fresh import stays clean, and pollution
that arrives from a DIFFERENT test file is exactly how that suite once failed
in the full run while passing in isolation. The inference legs therefore run
out of process, which also gives them an unpatched socket module to guard
themselves with.

SCOPE: STATIC_IMPORT_SCOPE for the source discipline and the primitives;
SUBPROCESS_SCOPE for the boundary and the real CPU inference. No model is
loaded in this process and no GPU is touched in any of them -- detection runs
on CPU by design, because it must not contend for the card that is mid
generation with the image it is detecting in.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless import detector_adapter as A  # noqa: E402
from forge_headless import ultralytics_boundary as B  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_DETECTOR_TESTS = 51

#: Discovery runs under `-I -S` (isolated, no site-packages), so Pillow and
#: numpy are absent there while the canonical run has them. The primitives are
#: skipped rather than errored in that mode -- the source discipline and the
#: subprocess legs still run, and the subprocess uses the venv interpreter
#: WITHOUT `-S`, so the real proof is unaffected either way.
try:
    from PIL import Image as _PILImage  # noqa: F401

    HAS_IMAGING = True
except Exception:  # noqa: BLE001
    HAS_IMAGING = False

PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"
DETECTOR_ROOT = APP_ROOT / "models" / "adetailer"

#: Long enough for a cold torch import plus a model load on a busy machine.
SUBPROCESS_TIMEOUT = 300


def _run(script: str) -> dict:
    """Run a probe out of process and return its JSON verdict."""

    completed = subprocess.run(
        [str(PYTHON), "-c", script],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("VERDICT "):
            return json.loads(line[len("VERDICT "):])
    raise AssertionError(
        f"probe produced no verdict\nstdout:\n{completed.stdout[-3000:]}"
        f"\nstderr:\n{completed.stderr[-3000:]}"
    )


#: Installed inside every probe. Records rather than merely blocking, because
#: the count is the measurement.
_GUARD = """
import socket, urllib.request
ATTEMPTS = []
def _refuse(name):
    def f(*a, **k):
        ATTEMPTS.append(name)
        raise AssertionError(name + " refused")
    return f
socket.create_connection = _refuse("create_connection")
socket.getaddrinfo = _refuse("getaddrinfo")
socket.socket.connect = _refuse("socket.connect")
urllib.request.urlopen = _refuse("urlopen")
"""


class SourceDisciplineTests(unittest.TestCase):
    """What the adapter may and may not have inherited."""

    ADAPTER = (APP_ROOT / "forge_headless" / "detector_adapter.py").read_text(
        encoding="utf-8"
    )
    BOUNDARY = (APP_ROOT / "forge_headless" / "ultralytics_boundary.py").read_text(
        encoding="utf-8"
    )

    def _module_scope_imports(self, source: str) -> set[str]:
        roots: set[str] = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.Import):
                roots.update(a.name.partition(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        return roots

    def test_the_adapter_imports_no_runtime_at_module_scope(self) -> None:
        """Naming this module must not drag torch into a Studio process."""

        for forbidden in ("torch", "torchvision", "ultralytics", "cv2", "numpy", "PIL"):
            with self.subTest(module=forbidden):
                self.assertNotIn(
                    forbidden, self._module_scope_imports(self.ADAPTER)
                )

    def test_the_boundary_imports_no_runtime_at_module_scope(self) -> None:
        """It configures the environment ultralytics is imported UNDER. A
        module-scope import would run before its own configuration."""

        for forbidden in ("torch", "ultralytics", "numpy"):
            with self.subTest(module=forbidden):
                self.assertNotIn(
                    forbidden, self._module_scope_imports(self.BOUNDARY)
                )

    def test_the_download_machinery_is_not_adapted(self) -> None:
        """`get_models`, `download_models` and `hf_download` live in the SAME
        upstream file as the primitives, which imports huggingface_hub at
        module scope. Reaching the primitives by import would have acquired
        them, which is what copying them out avoids."""

        code = self._code_only(self.ADAPTER)
        for inherited in (
            "hf_download", "download_models", "get_models",
            "huggingface_hub", "hf_hub_download", "REPO_ID",
        ):
            with self.subTest(symbol=inherited):
                self.assertNotIn(inherited, code)

    def test_results_plot_is_never_called(self) -> None:
        """`plot()` renders labels with a font ultralytics FETCHES if it is
        missing, which is a runtime download inside a generation."""

        self.assertNotIn(".plot()", self._code_only(self.ADAPTER))

    def test_the_preview_field_does_not_exist(self) -> None:
        """Upstream's `PredictOutput.preview` is the output of `plot()`.
        Keeping the field would invite something to fill it."""

        self.assertNotIn("preview", self._code_only(self.ADAPTER))

    def test_world_model_classes_are_not_adapted(self) -> None:
        """`apply_classes` only applies to `-world` models, which Studio does
        not bundle and cannot obtain without a download."""

        code = self._code_only(self.ADAPTER)
        self.assertNotIn("apply_classes", code)
        self.assertNotIn("set_classes", code)

    def test_detection_defaults_to_cpu(self) -> None:
        self.assertEqual("cpu", A.DEFAULT_DEVICE)

    def test_the_boundary_is_the_only_door_to_ultralytics(self) -> None:
        """One place that imports it means one place that can get the
        preconditions wrong."""

        self.assertIn("from .ultralytics_boundary import import_yolo", self.ADAPTER)
        self.assertNotIn("from ultralytics import", self._code_only(self.ADAPTER))

    @staticmethod
    def _code_only(source: str) -> str:
        """Comments and docstrings stripped.

        Both files DESCRIBE what they refuse to inherit, by name. A raw search
        finds `hf_download` in the sentence explaining that `hf_download` is
        not adapted -- the trap OPERATIONS.md section 7 records, and which
        this session has now hit five times.
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


@unittest.skipUnless(HAS_IMAGING, "Pillow is absent under -S discovery")
class PrimitiveTests(unittest.TestCase):
    """The adapted geometry, exercised without a detector."""

    def setUp(self) -> None:
        from PIL import Image

        self.Image = Image

    def test_a_bbox_becomes_a_filled_rectangle(self) -> None:
        masks = A.create_mask_from_bbox([[10, 20, 40, 60]], (100, 80))
        self.assertEqual(1, len(masks))
        self.assertEqual((100, 80), masks[0].size)
        self.assertEqual("L", masks[0].mode)
        self.assertEqual(255, masks[0].getpixel((20, 30)))
        self.assertEqual(0, masks[0].getpixel((90, 70)))

    def test_the_shape_is_width_height_not_height_width(self) -> None:
        """Getting this backwards yields the right area and the wrong
        orientation, which on a square test image looks correct."""

        masks = A.create_mask_from_bbox([[0, 0, 1, 1]], (120, 40))
        self.assertEqual((120, 40), masks[0].size)

    def test_ensure_pil_image_converts_mode(self) -> None:
        grey = self.Image.new("L", (8, 8), 128)
        self.assertEqual("RGB", A.ensure_pil_image(grey, "RGB").mode)

    def test_ensure_pil_image_passes_through(self) -> None:
        rgb = self.Image.new("RGB", (8, 8))
        self.assertIs(rgb, A.ensure_pil_image(rgb, "RGB"))

    def test_bbox_area(self) -> None:
        self.assertEqual(600, A.bbox_area([10, 20, 40, 40]))

    def test_is_all_black(self) -> None:
        self.assertTrue(A.is_all_black(self.Image.new("L", (4, 4), 0)))
        self.assertFalse(A.is_all_black(self.Image.new("L", (4, 4), 1)))

    def test_dilate_grows_and_erode_shrinks(self) -> None:
        import numpy as np

        mask = self.Image.new("L", (40, 40), 0)
        mask.paste(255, (18, 18, 22, 22))
        before = int(np.count_nonzero(np.asarray(mask)))
        grown = int(np.count_nonzero(np.asarray(A.dilate_erode(mask, 5))))
        shrunk = int(np.count_nonzero(np.asarray(A.dilate_erode(mask, -3))))
        self.assertGreater(grown, before)
        self.assertLess(shrunk, before)

    def test_a_zero_kernel_is_a_no_op(self) -> None:
        mask = self.Image.new("L", (8, 8), 7)
        self.assertIs(mask, A.dilate_erode(mask, 0))

    def test_an_even_kernel_is_rounded_up_not_refused(self) -> None:
        """PIL needs an odd kernel and opencv does not. Rounding DOWN would
        make a dilation of 2 silently do nothing."""

        mask = self.Image.new("L", (40, 40), 0)
        mask.paste(255, (18, 18, 22, 22))
        self.assertIsNotNone(A.dilate_erode(mask, 2))

    def test_offset_treats_y_as_up(self) -> None:
        mask = self.Image.new("L", (10, 10), 0)
        mask.paste(255, (5, 5, 6, 6))
        moved = A.offset(mask, 0, 1)
        self.assertEqual(255, moved.getpixel((5, 4)))


@unittest.skipUnless(HAS_IMAGING, "Pillow is absent under -S discovery")
class FilteringTests(unittest.TestCase):
    """Every filter reads `image_size`, because `preview` does not exist."""

    def result(self) -> A.DetectionResult:
        from PIL import Image

        boxes = [[0, 0, 10, 10], [0, 0, 50, 50], [60, 60, 70, 70]]
        return A.DetectionResult(
            bboxes=boxes,
            masks=[Image.new("L", (100, 100)) for _ in boxes],
            confidences=[0.9, 0.5, 0.7],
            image_size=(100, 100),
        )

    def test_filters_do_not_need_a_preview(self) -> None:
        """Upstream reads `pred.preview.size` here. With `plot()` refused,
        that attribute is None and upstream raises on the first filter."""

        filtered = A.filter_by_ratio(self.result(), 0.0, 0.10)
        self.assertEqual(2, len(filtered))

    def test_filter_by_ratio_keeps_the_middle(self) -> None:
        filtered = A.filter_by_ratio(self.result(), 0.02, 0.50)
        self.assertEqual([[0, 0, 50, 50]], filtered.bboxes)

    def test_a_zero_area_image_filters_nothing(self) -> None:
        empty = A.DetectionResult(bboxes=[[0, 0, 1, 1]], image_size=(0, 0))
        self.assertEqual(1, len(A.filter_by_ratio(empty, 0.5, 0.6)))

    def test_filter_k_largest(self) -> None:
        filtered = A.filter_k_largest(self.result(), 1)
        self.assertEqual([[0, 0, 50, 50]], filtered.bboxes)

    def test_filter_k_most_confident(self) -> None:
        filtered = A.filter_k_most_confident(self.result(), 1)
        self.assertEqual([0.9], filtered.confidences)

    def test_k_of_zero_keeps_everything(self) -> None:
        self.assertEqual(3, len(A.filter_k_largest(self.result(), 0)))
        self.assertEqual(3, len(A.filter_k_most_confident(self.result(), 0)))

    def test_every_parallel_list_is_reindexed_together(self) -> None:
        """Dropping a bbox without its mask and confidence produces a result
        that is internally inconsistent and still looks valid."""

        filtered = A.filter_k_largest(self.result(), 2)
        self.assertEqual(
            len(filtered.bboxes), len(filtered.masks), len(filtered.confidences)
        )
        self.assertEqual(2, len(filtered.masks))

    def test_sort_left_to_right(self) -> None:
        sorted_ = A.sort_bboxes(self.result(), A.SortBy.LEFT_TO_RIGHT)
        self.assertEqual([0, 0, 60], [b[0] for b in sorted_.bboxes])

    def test_sort_by_area_is_largest_first(self) -> None:
        sorted_ = A.sort_bboxes(self.result(), A.SortBy.AREA)
        self.assertEqual([0, 0, 50, 50], sorted_.bboxes[0])

    def test_sort_center_to_edge_needs_no_preview_either(self) -> None:
        sorted_ = A.sort_bboxes(self.result(), A.SortBy.CENTER_TO_EDGE)
        self.assertEqual(3, len(sorted_.bboxes))

    def test_an_unknown_sort_order_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            A.sort_bboxes(self.result(), 99)

    def test_merge_combines_into_one(self) -> None:
        from PIL import Image

        a = Image.new("L", (10, 10), 0)
        a.paste(255, (0, 0, 3, 3))
        b = Image.new("L", (10, 10), 0)
        b.paste(255, (6, 6, 9, 9))
        merged = A.mask_merge([a, b])
        self.assertEqual(1, len(merged))
        self.assertEqual(255, merged[0].getpixel((1, 1)))
        self.assertEqual(255, merged[0].getpixel((7, 7)))

    def test_merge_invert(self) -> None:
        from PIL import Image

        mask = Image.new("L", (10, 10), 0)
        mask.paste(255, (0, 0, 3, 3))
        out = A.mask_merge_invert([mask], A.MergeInvert.MERGE_INVERT)
        self.assertEqual(0, out[0].getpixel((1, 1)))
        self.assertEqual(255, out[0].getpixel((8, 8)))

    def test_preprocess_drops_a_mask_eroded_out_of_existence(self) -> None:
        """An all-black mask passed to an inpaint is a no-op that reports
        success -- the shape of defect this whole project keeps removing."""

        from PIL import Image

        tiny = Image.new("L", (40, 40), 0)
        tiny.paste(255, (20, 20, 21, 21))
        self.assertEqual([], A.mask_preprocess([tiny], kernel=-9))

    def test_preprocess_of_nothing_is_nothing(self) -> None:
        self.assertEqual([], A.mask_preprocess([]))

    def test_an_unknown_merge_mode_is_refused(self) -> None:
        from PIL import Image

        with self.assertRaises(ValueError):
            A.mask_merge_invert([Image.new("L", (4, 4))], 99)


class BoundaryConfigurationTests(unittest.TestCase):
    """The environment, without importing anything."""

    def test_the_offline_switch_is_the_one_upstream_honours(self) -> None:
        """`is_online` asserts on YOLO_OFFLINE BEFORE `import socket`, so this
        prevents the call rather than making it fail."""

        self.assertEqual("YOLO_OFFLINE", B.OFFLINE_ENV)

    def test_the_config_dir_is_studio_owned(self) -> None:
        """Never AppData\\Roaming\\Ultralytics: that path is outside the
        workspace and does not exist in a container."""

        self.assertEqual("YOLO_CONFIG_DIR", B.CONFIG_DIR_ENV)
        configured = B.configure_offline()
        self.assertIn("tmp", configured.parts)
        self.assertTrue(str(configured).startswith(str(APP_ROOT)))
        self.assertNotIn("AppData", str(configured))

    def test_configure_is_idempotent(self) -> None:
        self.assertEqual(B.configure_offline(), B.configure_offline())


class OfflineBoundarySubprocessTests(unittest.TestCase):
    """The measurement that matters, with its control."""

    def test_the_unguarded_import_does_reach_for_the_network(self) -> None:
        """THE CONTROL. Without this, a zero-attempt reading below would be
        consistent with ultralytics simply never dialling out, and the
        boundary would be proven to do nothing."""

        verdict = _run(_GUARD + """
import os, json
os.environ.pop("YOLO_OFFLINE", None)
os.environ.pop("YOLO_CONFIG_DIR", None)
import ultralytics.utils as uu
print("VERDICT " + json.dumps({
    "attempts": ATTEMPTS,
    "online": bool(uu.ONLINE),
    "config_dir": str(uu.USER_CONFIG_DIR),
}))
""")
        self.assertGreater(
            len(verdict["attempts"]), 0,
            "unguarded ultralytics made no network attempt; the boundary "
            "below would prove nothing",
        )

    def test_the_unguarded_import_writes_outside_the_workspace(self) -> None:
        verdict = _run(_GUARD + """
import os, json
os.environ.pop("YOLO_CONFIG_DIR", None)
os.environ["YOLO_OFFLINE"] = "True"
import ultralytics.utils as uu
print("VERDICT " + json.dumps({"config_dir": str(uu.USER_CONFIG_DIR)}))
""")
        self.assertNotIn(str(APP_ROOT), verdict["config_dir"])

    def test_online_is_false_either_way_so_it_proves_nothing(self) -> None:
        """Pinned as a fact about the measurement, so a later reader does not
        'simplify' this suite into asserting ONLINE and calling it a proof."""

        verdict = _run(_GUARD + """
import os, json
os.environ.pop("YOLO_OFFLINE", None)
import ultralytics.utils as uu
print("VERDICT " + json.dumps({"online": bool(uu.ONLINE)}))
""")
        self.assertFalse(verdict["online"])

    def test_the_guarded_import_attempts_nothing(self) -> None:
        verdict = _run(_GUARD + """
import sys, json
sys.path.insert(0, ".")
from forge_headless.ultralytics_boundary import import_yolo, assert_offline
import_yolo()
state = assert_offline()
print("VERDICT " + json.dumps({"attempts": ATTEMPTS, **state}))
""")
        self.assertEqual([], verdict["attempts"])
        self.assertFalse(verdict["online"])
        self.assertFalse(verdict["sync"])
        self.assertTrue(verdict["config_dir"].startswith(str(APP_ROOT)))

    def test_importing_before_configuring_is_detected_not_ignored(self) -> None:
        """Configuring a guard and trusting its silence is how this project
        got helpers with no callers. The boundary re-reads what ultralytics
        actually computed and refuses if it disagrees."""

        verdict = _run(_GUARD + """
import os, sys, json
sys.path.insert(0, ".")
os.environ.pop("YOLO_OFFLINE", None)
import ultralytics.utils as uu
uu.ONLINE = True                      # what an unconfigured import looks like
from forge_headless.ultralytics_boundary import assert_offline, DetectorBoundaryError
try:
    assert_offline()
    refused = False
except DetectorBoundaryError:
    refused = True
print("VERDICT " + json.dumps({"refused": refused}))
""")
        self.assertTrue(verdict["refused"])


class RealInferenceSubprocessTests(unittest.TestCase):
    """Real weights, real inference, on CPU, with nothing reaching out.

    The handoff lists these legs as CPU work, and they are: a YOLO detector is
    a few megabytes and `device='cpu'` never touches VRAM. So the detector
    half of Auto Detail is PROVEN rather than assumed before the card is ever
    needed -- what remains unproven is the inpaint integration, which is a
    different claim and is marked as such.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not (DETECTOR_ROOT / "face_yolov8n.pt").exists():
            raise unittest.SkipTest("bundled detectors are not present")
        cls.verdict = _run(_GUARD + """
import sys, json, time
sys.path.insert(0, ".")
from PIL import Image, ImageDraw
from forge_headless.detector_adapter import (
    detect, release_detectors, resident_detectors)

img = Image.new("RGB", (512, 512), (235, 235, 240))
d = ImageDraw.Draw(img)
d.ellipse((228, 70, 288, 132), fill=(226, 194, 168))
d.rounded_rectangle((205, 132, 312, 320), 18, fill=(60, 80, 140))
d.rounded_rectangle((214, 320, 250, 452), 14, fill=(40, 44, 60))
d.rounded_rectangle((266, 320, 302, 452), 14, fill=(40, 44, 60))
d.rounded_rectangle((176, 140, 208, 300), 13, fill=(60, 80, 140))
d.rounded_rectangle((309, 140, 341, 300), 13, fill=(60, 80, 140))

t = time.monotonic()
bbox = detect("models/adetailer/face_yolov8n.pt", img, confidence=0.25)
cold = time.monotonic() - t
t = time.monotonic()
again = detect("models/adetailer/face_yolov8n.pt", img, confidence=0.25)
warm = time.monotonic() - t

seg = detect("models/adetailer/person_yolov8n-seg.pt", img, confidence=0.20)
mask_kinds = sorted({type(m).__name__ for m in seg.masks})
mask_modes = sorted({m.mode for m in seg.masks})
mask_sizes = sorted({m.size for m in seg.masks})

residents = list(resident_detectors())
released = release_detectors()
print("VERDICT " + json.dumps({
    "attempts": ATTEMPTS,
    "cold_seconds": cold, "warm_seconds": warm,
    "bbox_count": len(bbox), "bbox_size": list(bbox.image_size),
    "repeatable": bbox.describe() == again.describe(),
    "seg_count": len(seg),
    "seg_describe": seg.describe(),
    "mask_kinds": mask_kinds, "mask_modes": mask_modes,
    "mask_sizes": [list(s) for s in mask_sizes],
    "residents": residents, "released": released,
    "after_release": list(resident_detectors()),
}))
""")

    def test_nothing_reached_the_network(self) -> None:
        self.assertEqual([], self.verdict["attempts"])

    def test_a_bbox_detector_returns_a_well_formed_result(self) -> None:
        self.assertEqual([512, 512], self.verdict["bbox_size"])

    def test_a_segmentation_detector_returns_real_masks(self) -> None:
        """The mask branch, not the bbox branch. `mask_to_pil` is only
        exercised when the detector actually produces segmentation data."""

        self.assertGreater(self.verdict["seg_count"], 0)
        self.assertEqual(["Image"], self.verdict["mask_kinds"])
        self.assertEqual(["L"], self.verdict["mask_modes"])
        self.assertEqual([[512, 512]], self.verdict["mask_sizes"])

    def test_a_repeated_warm_run_agrees_with_the_first(self) -> None:
        self.assertTrue(self.verdict["repeatable"])

    def test_the_warm_run_reuses_the_loaded_detector(self) -> None:
        """Upstream constructs a fresh YOLO per call. Three Auto Detail slots
        would spend that three times over on weights already in memory."""

        self.assertLess(self.verdict["warm_seconds"], self.verdict["cold_seconds"])

    def test_detectors_are_resident_by_name_and_released(self) -> None:
        self.assertEqual(
            ["face_yolov8n.pt", "person_yolov8n-seg.pt"],
            sorted(self.verdict["residents"]),
        )
        self.assertEqual(2, self.verdict["released"])
        self.assertEqual([], self.verdict["after_release"])

    def test_no_resident_detector_is_reported_as_a_path(self) -> None:
        for name in self.verdict["residents"]:
            with self.subTest(name=name):
                self.assertNotIn("/", name)
                self.assertNotIn("\\", name)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_DETECTOR_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("SUBPROCESS_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
