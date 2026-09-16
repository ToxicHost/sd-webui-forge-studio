"""Auto Detail reaches the boundary, and is refused there rather than ignored.

Task 10a landed the rules. This is the carry up to the point where the runtime
would run them, and the honest refusal that stands in until it does.

```text
name -> path      resolve_detector_path, walking the SAME admission the
                  offered catalogue walks, so a name that was offered
                  resolves and a name that was refused cannot be smuggled
                  through as a path
over HTTP         `auto_detail` was not in _GENERATION_FIELDS, so the field
                  the contract has always declared was refused as unknown and
                  could never arrive at all
membership        is_known_detector, wired at the same seam as
                  is_known_upscaler -- the fifth is_known_* written, and the
                  first to get a caller in the same change as its field
refusal           `enable_adetailer` now follows `auto_detail.enabled`, so
                  asking for Auto Detail is REFUSED with a code and a reason.
                  A hardcoded False would have produced a base-only image that
                  looked like it worked, the moment the field became
                  reachable -- the exact defect shape this phase removes.
```

WHY THE REFUSAL IS THE POINT

It would have been less work to parse `auto_detail`, validate it, and let it
reach a runtime that ignores it. That is how `enable_hr`, the sampler, the
scheduler, the upscaler and Live Preview each spent a phase looking
implemented. The test below asserts the refusal is REAL -- a mapped code, a
named field, and a message that says the pass did not run.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU, no detector weights;
the catalogue is a temporary directory of decoy files.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.auto_detail import MAX_SLOTS  # noqa: E402
from forge_headless.generation_request import (  # noqa: E402
    FirstImageRequest,
    ResidentModel,
    validate_request,
)
from forge_headless.studio_generation import (  # noqa: E402
    PINNED_OFF_FLAGS,
    REQUEST_UNSUPPORTED,
    studio_code_for,
    translate_request,
)
from forge_studio.contracts import (  # noqa: E402
    MAX_AUTO_DETAIL_SLOTS,
    AutoDetailSettings,
    AutoDetailSlot,
    GenerationRequest,
)
from forge_studio.detector_catalogue import (  # noqa: E402
    Detector,
    resolve_detector_path,
    scan_detectors,
)
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_auto_detail,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_CARRY_TESTS = 55

OWNER_FILE = "mine.pt"
BUNDLED_NAME = "face_yolov8n.pt"


class _Registry:
    """Only the one method `scan_configured_detectors` reaches for."""

    def __init__(self, *roots: Path) -> None:
        self._roots = [str(root) for root in roots]

    def configured_roots(self) -> dict[str, tuple[str, ...]]:
        return {"adetailer": tuple(self._roots)}


class _Catalogue(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, relative: str, payload: bytes = b"decoy") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path


class ResolverTests(_Catalogue):
    def test_an_owner_supplied_name_resolves_to_its_file(self) -> None:
        written = self._write(OWNER_FILE)
        self.assertEqual(
            written, resolve_detector_path(OWNER_FILE, _Registry(self.root))
        )

    def test_a_name_the_catalogue_refused_resolves_to_nothing(self) -> None:
        """A bundled NAME carrying bytes Studio did not ship is refused by the
        catalogue. It must not be reachable as a path either, or the refusal
        would only apply to the list the owner sees."""

        self._write(BUNDLED_NAME, b"not the shipped bytes")
        offered = {d.name for d in scan_detectors(self.root)}
        self.assertNotIn(BUNDLED_NAME, offered)
        self.assertIsNone(
            resolve_detector_path(BUNDLED_NAME, _Registry(self.root))
        )

    def test_an_unknown_name_resolves_to_nothing(self) -> None:
        self._write(OWNER_FILE)
        self.assertIsNone(
            resolve_detector_path("absent.pt", _Registry(self.root))
        )

    def test_an_empty_name_resolves_to_nothing(self) -> None:
        self._write(OWNER_FILE)
        for blank in ("", "   ", None):
            with self.subTest(name=blank):
                self.assertIsNone(
                    resolve_detector_path(blank, _Registry(self.root))
                )

    def test_it_resolves_exactly_what_the_catalogue_offers(self) -> None:
        """The claim the whole design rests on. Two enumerators are free to
        disagree about one folder while both believe themselves right, so
        these two walk the same admission."""

        self._write(OWNER_FILE)
        self._write("nested/second.pt")
        self._write(BUNDLED_NAME, b"wrong bytes")
        registry = _Registry(self.root)
        for detector in scan_detectors(self.root):
            with self.subTest(detector=detector.name):
                self.assertIsNotNone(
                    resolve_detector_path(detector.name, registry)
                )

    def test_within_one_root_the_first_sorted_path_wins(self) -> None:
        first = self._write("a/dup.pt")
        self._write("b/dup.pt")
        self.assertEqual(first, resolve_detector_path("dup.pt", _Registry(self.root)))

    def test_across_roots_the_first_root_wins(self) -> None:
        other = Path(self._tmp.name) / "second-root"
        other.mkdir()
        (other / OWNER_FILE).write_bytes(b"second")
        winner = self._write(OWNER_FILE)
        self.assertEqual(
            winner, resolve_detector_path(OWNER_FILE, _Registry(self.root, other))
        )

    def test_a_registry_that_raises_resolves_to_nothing(self) -> None:
        class _Broken:
            def configured_roots(self):  # noqa: ANN202
                raise RuntimeError("no registry")

        self.assertIsNone(resolve_detector_path(OWNER_FILE, _Broken()))

    def test_no_root_resolves_to_nothing(self) -> None:
        self.assertIsNone(resolve_detector_path(OWNER_FILE, _Registry()))


# ------------------------------------------------------------- over the wire


class PayloadTests(unittest.TestCase):
    def test_absent_means_absent(self) -> None:
        """Nothing downstream sets a single Auto Detail field, so a result
        stays byte-identical to a pre-P0.8 one."""

        self.assertIsNone(_validated_auto_detail(None))

    def test_a_slot_is_parsed_with_the_owners_defaults(self) -> None:
        got = _validated_auto_detail(
            {"enabled": True, "slots": [{"enabled": True, "detector": "f.pt"}]}
        )
        self.assertTrue(got.enabled)
        self.assertEqual("f.pt", got.slots[0].detector)
        # 6 and 0.30 are the OWNER's working values, not upstream's 4 and 0.4.
        self.assertEqual(6, got.slots[0].mask_blur)
        self.assertEqual(0.30, got.slots[0].denoising_strength)

    def test_a_fourth_slot_is_refused_rather_than_truncated(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_auto_detail({"slots": [{}, {}, {}, {}]})

    def test_an_inverted_ratio_range_is_refused(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_auto_detail(
                {"slots": [{"min_ratio": 0.9, "max_ratio": 0.1}]}
            )

    def test_an_unknown_key_is_refused_with_its_position(self) -> None:
        with self.assertRaises(PresentationError) as raised:
            _validated_auto_detail({"slots": [{"detecter": "typo.pt"}]})
        self.assertIn("slots[1].detecter", str(raised.exception))

    def test_an_out_of_range_number_is_refused_not_clamped(self) -> None:
        """Clamping would run a generation the owner did not ask for and
        report success."""

        with self.assertRaises(PresentationError):
            _validated_auto_detail({"slots": [{"confidence": 4.0}]})

    def test_the_group_must_be_an_object(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_auto_detail(["not", "an", "object"])


class GenerationFieldTests(unittest.TestCase):
    def test_auto_detail_is_an_accepted_generation_field(self) -> None:
        """Without this the contract's own field could never arrive: the
        payload validator refuses unknown keys, so `auto_detail` was rejected
        one level above the parser that understands it."""

        from forge_studio.presentation import _GENERATION_FIELDS

        self.assertIn("auto_detail", _GENERATION_FIELDS)

    def test_the_slot_count_is_one_rule_not_two(self) -> None:
        """The contract and the runtime each name three. Two constants meaning
        one rule is how they drift."""

        self.assertEqual(MAX_AUTO_DETAIL_SLOTS, MAX_SLOTS)


# ------------------------------------------------------- carry and refusal


def _request(**overrides: Any) -> GenerationRequest:
    fields: dict[str, Any] = {
        "model_id": "m", "positive_prompt": "p", "negative_prompt": "",
        "seed": 1, "steps": 4, "cfg_scale": 6.0, "width": 768, "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)


def _enabled(**slot: Any) -> AutoDetailSettings:
    fields: dict[str, Any] = {"enabled": True, "detector": "face_yolov8n.pt"}
    fields.update(slot)
    return AutoDetailSettings(enabled=True, slots=(AutoDetailSlot(**fields),))


class CarryTests(unittest.TestCase):
    def _headless(self, request: GenerationRequest) -> FirstImageRequest:
        return translate_request(request, request_id="rid").headless_request

    def test_the_flag_follows_the_owners_choice(self) -> None:
        self.assertTrue(
            self._headless(_request(auto_detail=_enabled())).enable_adetailer
        )

    def test_no_group_leaves_the_flag_off(self) -> None:
        self.assertFalse(self._headless(_request()).enable_adetailer)

    def test_a_disabled_group_leaves_the_flag_off(self) -> None:
        settings = AutoDetailSettings(enabled=False, slots=())
        self.assertFalse(
            self._headless(_request(auto_detail=settings)).enable_adetailer
        )

    def test_it_is_no_longer_reported_as_pinned_off(self) -> None:
        """Refused is not pinned. Reporting it as pinned would misdescribe a
        flag that now carries the owner's choice."""

        self.assertNotIn("enable_adetailer", PINNED_OFF_FLAGS)


class RefusalTests(unittest.TestCase):
    def _problems(self, request: GenerationRequest) -> list[dict[str, str]]:
        headless = translate_request(request, request_id="rid").headless_request
        return validate_request(
            headless,
            ResidentModel(model_id="m", family="anima", resident=True),
            options_available=True,
            progress_installed=True,
            result_root_writable=True,
            generation_authorized=True,
        ).problems

    def _codes(self, request: GenerationRequest) -> set[str]:
        return {problem["code"] for problem in self._problems(request)}

    def test_the_blanket_refusal_is_gone(self) -> None:
        """It stood in while the runtime was unwired, exactly as `enable_hr`
        was pinned off before P0.7. The slots travel now and the port runs
        them, so the question is no longer whether Auto Detail is permitted
        but whether the pass ASKED FOR is dispatchable.

        The unresolved-detector code below is that new question: this fixture
        has no resolver, so the name cannot become a file."""

        codes = self._codes(_request(auto_detail=_enabled()))
        self.assertNotIn("GENERATION_ADETAILER_NOT_PERMITTED", codes)
        self.assertIn("GENERATION_ADETAILER_DETECTOR_UNRESOLVED", codes)

    def test_not_asking_for_it_is_not_refused(self) -> None:
        """The discriminating half. A blanket refusal would pass the test
        above and break every base-only generation."""

        self.assertNotIn(
            "GENERATION_ADETAILER_NOT_PERMITTED", self._codes(_request())
        )

    def test_the_refusal_names_the_detector_it_could_not_find(self) -> None:
        """A refusal that does not say WHICH detector cannot be acted on --
        the same rule the steps bound now follows."""

        detail = next(
            problem["detail"]
            for problem in self._problems(_request(auto_detail=_enabled()))
            if problem["code"] == "GENERATION_ADETAILER_DETECTOR_UNRESOLVED"
        )
        self.assertIn("face_yolov8n.pt", detail)
        self.assertIn("slot 1", detail)

    def test_it_reaches_the_owner_as_an_unsupported_request(self) -> None:
        self.assertEqual(
            REQUEST_UNSUPPORTED,
            studio_code_for("GENERATION_ADETAILER_NOT_PERMITTED"),
        )


# --------------------------------------------------------------- membership


def _Application(detectors: tuple[Detector, ...]):
    """A real application with only the catalogue attached.

    `__new__` rather than a stub, so the reader indirection under test --
    `_detectors`, and its swallow-everything guard -- is the real one. A stub
    with its own `_detectors` would have proven that the stub works.
    """

    from forge_studio.application import StudioApplication

    app = StudioApplication.__new__(StudioApplication)
    app._detector_catalogue = (lambda: detectors) if detectors else None
    return app


class MembershipTests(unittest.TestCase):
    def setUp(self) -> None:
        from forge_studio.application import StudioApplication

        self.validate = StudioApplication._validate_detectors
        self.offered = (
            Detector(name="face_yolov8n.pt", size=1, verified=True,
                     owner_supplied=False),
        )

    def test_an_unknown_detector_is_refused(self) -> None:
        from forge_studio.contracts import StudioError

        app = _Application(self.offered)
        with self.assertRaises(StudioError) as raised:
            self.validate(app, _request(auto_detail=_enabled(detector="nope.pt")))
        self.assertEqual(
            "auto_detail.slots[1].detector", raised.exception.error.field
        )

    def test_an_offered_detector_is_accepted(self) -> None:
        app = _Application(self.offered)
        self.validate(app, _request(auto_detail=_enabled()))

    def test_an_enabled_slot_naming_nothing_is_refused(self) -> None:
        from forge_studio.contracts import StudioError

        app = _Application(self.offered)
        with self.assertRaises(StudioError):
            self.validate(app, _request(auto_detail=_enabled(detector="")))

    def test_a_disabled_slot_is_not_checked(self) -> None:
        """A disabled slot is a true no-op, and an owner who never turned it
        on has no reason to have chosen a detector for it."""

        app = _Application(self.offered)
        settings = AutoDetailSettings(
            enabled=True,
            slots=(AutoDetailSlot(enabled=False, detector="nope.pt"),),
        )
        self.validate(app, _request(auto_detail=settings))

    def test_an_empty_catalogue_refuses_nothing(self) -> None:
        """Matching `if not registries.available: return` one function up. An
        unconfigured root and a configured empty one are the same observation,
        and refusing every generation over it would break base-only jobs."""

        app = _Application(())
        self.validate(app, _request(auto_detail=_enabled(detector="anything.pt")))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_CARRY_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------- the resolved slots travel


class ResolvedSlotTests(unittest.TestCase):
    """`_auto_detail_fields`: Studio's group onto the runtime's slots."""

    def _fields(self, settings, resolver=lambda name: "/detectors/" + name):
        from forge_headless.studio_generation import _auto_detail_fields

        return _auto_detail_fields(settings, resolve_detector=resolver)

    def test_absent_means_absent(self) -> None:
        got = self._fields(None)
        self.assertFalse(got["enable_adetailer"])
        self.assertEqual((), got["ad_slots"])

    def test_an_enabled_slot_carries_its_resolved_path(self) -> None:
        got = self._fields(_enabled())
        self.assertTrue(got["enable_adetailer"])
        self.assertEqual("/detectors/face_yolov8n.pt", got["ad_slots"][0].detector_path)

    def test_the_catalogue_name_travels_beside_the_path(self) -> None:
        """The name is what may be logged; the path is what `detect` needs."""

        self.assertEqual("face_yolov8n.pt", self._fields(_enabled())["ad_slots"][0].detector)

    def test_a_disabled_slot_does_not_travel(self) -> None:
        settings = AutoDetailSettings(
            enabled=True,
            slots=(
                AutoDetailSlot(enabled=False, detector="off.pt"),
                AutoDetailSlot(enabled=True, detector="on.pt"),
            ),
        )
        got = self._fields(settings)
        self.assertEqual(["on.pt"], [s.detector for s in got["ad_slots"]])

    def test_enabled_with_no_enabled_slot_is_off(self) -> None:
        """Otherwise the runtime advances into an Auto Detail stage, runs
        nothing, and reports that it detailed."""

        got = self._fields(AutoDetailSettings(enabled=True, slots=()))
        self.assertFalse(got["enable_adetailer"])

    def test_an_unresolvable_name_travels_with_an_empty_path(self) -> None:
        """Dropped would be worse: the slot would vanish and the owner would
        get a base image. Empty is refused by index, with the name in it."""

        got = self._fields(_enabled(), resolver=lambda name: None)
        self.assertEqual("", got["ad_slots"][0].detector_path)

    def test_slots_are_numbered_from_one(self) -> None:
        self.assertEqual(1, self._fields(_enabled())["ad_slots"][0].index)


class LoggableViewTests(unittest.TestCase):
    """`to_dict` feeds the dispatch log. A path there is not a leak -- it is
    a SUPPRESSED line, because `job_log._safe` refuses anything path-shaped
    and would take the sampler, size and seed with it."""

    def _headless(self):
        return translate_request(
            _request(auto_detail=_enabled()),
            request_id="rid",
            resolve_detector=lambda name: "/detectors/" + name,
        ).headless_request

    def test_the_slot_count_is_reported(self) -> None:
        self.assertEqual(1, self._headless().to_dict()["auto_detail_slots"])

    def test_detector_names_are_reported(self) -> None:
        self.assertEqual(
            ["face_yolov8n.pt"], self._headless().to_dict()["auto_detail_detectors"]
        )

    def test_no_resolved_path_reaches_the_loggable_view(self) -> None:
        rendered = json.dumps(self._headless().to_dict())
        self.assertNotIn("/detectors/", rendered)


class RunnableRefusalTests(unittest.TestCase):
    """The blanket refusal is gone; the pass ASKED FOR is checked instead."""

    def _problems(self, settings, resolver=lambda name: "/detectors/" + name):
        headless = translate_request(
            _request(auto_detail=settings), request_id="rid",
            resolve_detector=resolver,
        ).headless_request
        return validate_request(
            headless,
            ResidentModel(model_id="m", family="anima", resident=True),
            options_available=True, progress_installed=True,
            result_root_writable=True, generation_authorized=True,
        ).problems

    def _codes(self, settings, **kw):
        return {p["code"] for p in self._problems(settings, **kw)}

    def test_a_resolvable_slot_is_no_longer_refused(self) -> None:
        """`enable_adetailer` LEFT the blanket refusal loop, as `enable_hr`
        did in P0.7. This is the whole point of the commit."""

        self.assertNotIn(
            "GENERATION_ADETAILER_NOT_PERMITTED", self._codes(_enabled())
        )

    def test_an_unresolved_detector_is_refused_before_the_base_pass(self) -> None:
        self.assertIn(
            "GENERATION_ADETAILER_DETECTOR_UNRESOLVED",
            self._codes(_enabled(), resolver=lambda name: None),
        )

    def test_an_out_of_range_denoise_is_refused(self) -> None:
        self.assertIn(
            "GENERATION_ADETAILER_DENOISE_OUT_OF_RANGE",
            self._codes(_enabled(denoising_strength=4.0)),
        )

    def test_a_base_only_job_is_untouched_by_any_of_it(self) -> None:
        """The discriminating half: these checks run only under the flag."""

        headless = translate_request(_request(), request_id="rid").headless_request
        self.assertFalse(headless.enable_adetailer)
        self.assertEqual((), headless.ad_slots)


class ConsoleTests(unittest.TestCase):
    """The console must say a detector ran.

    Found by the owner reading their own log: DISPATCH names the sampler, the
    size and the seed, HIRES names the second-pass recipe, and a job that ran
    a detector pass said NOTHING about it. The evidence was a step count
    appearing after the second pass -- which is indistinguishable from another
    Hires pass, and was in fact mistaken for one.
    """

    def _lines(self, call) -> list[str]:
        import logging
        from forge_studio.job_log import JobLog

        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record):  # noqa: ANN001
                records.append(record.getMessage())

        logger = logging.getLogger("studio.job")
        handler = _Capture()
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.INFO)
        try:
            call(JobLog())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        return records

    def test_the_requested_detectors_are_announced(self) -> None:
        from forge_headless.auto_detail import SlotSpec

        lines = self._lines(lambda log: log.auto_detail(
            "j", slots=(SlotSpec(index=1, enabled=True,
                                 detector="face_yolov8n.pt",
                                 detector_path="/x/face_yolov8n.pt"),)))
        self.assertTrue(any("AUTODETAIL" in line for line in lines))
        self.assertTrue(any("face_yolov8n.pt" in line for line in lines))

    def test_the_announcement_carries_no_path(self) -> None:
        """A path would be refused by `_safe` and take the line with it."""

        from forge_headless.auto_detail import SlotSpec

        lines = self._lines(lambda log: log.auto_detail(
            "j", slots=(SlotSpec(index=1, enabled=True,
                                 detector="face_yolov8n.pt",
                                 detector_path="/detectors/face_yolov8n.pt"),)))
        self.assertNotIn("/detectors/", " ".join(lines))

    def test_a_job_without_auto_detail_says_nothing(self) -> None:
        self.assertEqual([], self._lines(lambda log: log.auto_detail("j", slots=())))

    def test_what_the_detectors_found_is_reported(self) -> None:
        """The more useful of the two lines: 'found four, kept one' is the
        only evidence the owner's filter settings did anything."""

        from forge_headless.auto_detail import SlotOutcome

        lines = self._lines(lambda log: log.auto_detail_result(
            "j", outcomes=(SlotOutcome(index=1, detector="face_yolov8n.pt",
                                       candidates=4, regions=1, detailed=True),)))
        joined = " ".join(lines)
        self.assertIn("candidates=4", joined)
        self.assertIn("regions=1", joined)
        self.assertIn("detailed=True", joined)

    def test_no_outcomes_reports_nothing(self) -> None:
        self.assertEqual(
            [], self._lines(lambda log: log.auto_detail_result("j", outcomes=()))
        )


class DetailPassResolutionTests(unittest.TestCase):
    """Auto Detail denoises at the BASE size, not the Hires frame's.

    This is where 2.25x of Auto Detail's 4.43x slowdown against Neo came from.
    Neo crops to the masked region and then resizes that crop to
    `self.width` x `self.height` before denoising
    (`modules/processing.py:1767-1770`), so a pass told `width=image.width`
    denoises a FACE at the full 1536x1536 Hires resolution.

    ADetailer is the oracle and is on disk at `Reference/adetailer.zip`:
    `scripts/!adetailer.py:352-363` takes `init_images=[image]` from the same
    post-Hires frame but sizes the pass from `p.width`/`p.height`. Hires never
    touches those -- it writes its target to `hr_upscale_to_x`
    (`modules/processing.py:1283`) -- so the oracle details at the base size.

    A pure-source test on purpose: the arithmetic is what regressed, and it
    regresses without a GPU.
    """

    SOURCE = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
        encoding="utf-8")

    def _detail_pass(self) -> str:
        start = self.SOURCE.index("def _detail_pass(")
        return self.SOURCE[start:][:6000]

    def test_the_pass_is_not_sized_from_the_image_it_was_handed(self) -> None:
        body = self._detail_pass()
        self.assertNotIn("width=image.width", body)
        self.assertNotIn("height=image.height", body)

    def test_the_pass_is_sized_from_the_request(self) -> None:
        body = self._detail_pass()
        self.assertIn('getattr(request, "width"', body)
        self.assertIn('getattr(request, "height"', body)

    def test_the_image_is_still_what_gets_detailed(self) -> None:
        # Only the SIZE moved. The post-Hires frame is still the init image,
        # exactly as the oracle passes it.
        body = self._detail_pass()
        self.assertIn("init_images=[image]", body)


