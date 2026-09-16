"""Live Preview: the toggle, and whether it does anything.

It did not. Three separate things were simultaneously true:

```text
the page          shipped a Live Preview toggle in Settings, and app.js
                  gated its own preview rendering on it
the request       had no preview field, and `preview_enabled` sat in
                  BACKEND_DEFAULTED_FIELDS -- "Studio's request cannot
                  express this"
the backend       hardcoded preview_enabled=False at BOTH HeadlessProgress
                  construction sites, and `"preview": None` was a literal on
                  every /studio/ws progress message
```

Meanwhile Neo was writing a decoded latent into the state bridge on every
sampler step, where it was stored in a dict and read by nothing except the
cleanup that exists to drop it. The pixels were arriving and being discarded
beneath a control that claimed to show them.

This is the third instance of one defect shape, after sampler/scheduler in
P0.6 and Hires in P0.7: a control that renders, reports success, and reaches
nothing. Every assertion about carriage below fails against the pinned-off
implementation.

The preview frame is deliberately NOT on `ProgressSnapshot`. That structure is
small, loggable, and serialised into every job record; a 25 kB base64 string
belongs on the progress socket and nowhere else. There is a test for that too.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model is loaded, no
image is generated, and no latent is decoded -- the decoder is proven to
refuse rather than proven to draw, because drawing needs a resident model.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless import preview_frame as preview_module  # noqa: E402
from forge_headless.headless_progress import (  # noqa: E402
    PREVIEW_MIN_INTERVAL_SECONDS,
    PREVIEW_STATES,
    ForgeStateBridge,
    HeadlessProgress,
    JobState,
)
from forge_headless.studio_generation import (  # noqa: E402
    BACKEND_DEFAULTED_FIELDS,
    STUDIO_REQUEST_FIELDS,
    translate_request,
)
from forge_studio.backend import BackendAdapter  # noqa: E402
from forge_studio.contracts import GenerationRequest  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_request_payload,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_PREVIEW_TESTS = 36

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

BASE = {
    "model": "",
    "positive_prompt": "a cube",
    "negative_prompt": "",
    "seed": 1,
    "steps": 8,
    "cfg_scale": 4.0,
    "width": 512,
    "height": 512,
    "model_selection": {
        "checkpoint_model_id": "a" * 32,
        "text_encoder_model_id": "b" * 32,
        "vae_model_id": "c" * 32,
    },
}


def _body(**generation: object) -> dict[str, object]:
    """One canonical request body with `generation` overrides applied."""

    payload = dict(BASE)
    selection = payload.pop("model_selection")
    model = payload.pop("model")
    return {
        "model": model,
        "model_selection": selection,
        "generation": {**payload, **generation},
    }


def _request(**overrides: object) -> GenerationRequest:
    fields = {
        "model_id": "m",
        "positive_prompt": "a cube",
        "negative_prompt": "",
        "seed": 1,
        "steps": 8,
        "cfg_scale": 4.0,
        "width": 512,
        "height": 512,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


class TheChoiceIsCarriedTests(unittest.TestCase):
    """CARRY. The first of the six obligations an owner-facing control owes."""

    def test_the_contract_has_a_field_defaulting_off(self) -> None:
        self.assertFalse(_request().preview_enabled)
        self.assertTrue(_request(preview_enabled=True).preview_enabled)

    def test_the_translation_reports_it_as_a_studio_choice(self) -> None:
        """Not as a backend default. The report said 'defaulted' while the
        page showed a switch, which is the same lie in the audit trail."""

        self.assertIn("preview_enabled", STUDIO_REQUEST_FIELDS)
        self.assertNotIn("preview_enabled", BACKEND_DEFAULTED_FIELDS)

    def test_an_enabled_request_reaches_the_headless_request(self) -> None:
        translation = translate_request(
            _request(preview_enabled=True), request_id="j"
        )
        self.assertTrue(translation.headless_request.preview_enabled)

    def test_a_disabled_request_reaches_it_too(self) -> None:
        """Disabled must be CARRIED, not merely indistinguishable from the
        pin it replaced -- otherwise the repair cannot be told from the bug."""

        translation = translate_request(
            _request(preview_enabled=False), request_id="j"
        )
        self.assertFalse(translation.headless_request.preview_enabled)

    def test_the_carried_list_appears_in_the_loggable_report(self) -> None:
        translation = translate_request(
            _request(preview_enabled=True), request_id="j"
        )
        self.assertIn("preview_enabled", translation.to_dict()["carried"])


class RequestNormalisationTests(unittest.TestCase):
    """The choice survives the boundary that refuses unknown fields."""

    def test_the_field_is_accepted_inside_generation(self) -> None:
        payload = _validated_request_payload(_body(preview_enabled=True))
        self.assertTrue(payload["preview_enabled"])

    def test_absent_means_off(self) -> None:
        self.assertFalse(_validated_request_payload(_body())["preview_enabled"])

    def test_a_string_toggle_is_coerced_not_refused(self) -> None:
        """Refusing a job over the spelling of a preview flag would cost the
        owner the image they actually asked for."""

        for spelling in ("true", "TRUE", "on", "1", "yes"):
            with self.subTest(spelling=spelling):
                payload = _validated_request_payload(
                    _body(preview_enabled=spelling)
                )
                self.assertTrue(payload["preview_enabled"])
        for spelling in ("false", "off", "0", ""):
            with self.subTest(spelling=spelling):
                payload = _validated_request_payload(
                    _body(preview_enabled=spelling)
                )
                self.assertFalse(payload["preview_enabled"])

    def test_a_flat_body_still_carries_it(self) -> None:
        """The transitional flat shape is rewritten into the canonical one at
        a single point, so both must produce the same kwargs."""

        flat = dict(BASE)
        flat["preview_enabled"] = True
        self.assertTrue(_validated_request_payload(flat)["preview_enabled"])

    def test_the_field_is_still_refused_where_it_does_not_belong(self) -> None:
        """`model_selection` is a SIBLING of `generation`. Accepting a new
        field must not have widened what a nested group may contain."""

        body = _body()
        body["generation"]["hires"] = {"preview_enabled": True}  # type: ignore[index]
        with self.assertRaises(PresentationError):
            _validated_request_payload(body)


class ProgressFrameTests(unittest.TestCase):
    """The frame itself: produced only when asked, only while sampling."""

    def _sampling(self, *, preview: bool) -> HeadlessProgress:
        progress = HeadlessProgress("j", preview_enabled=preview)
        progress.advance_to(JobState.CONDITIONING)
        progress.advance_to(JobState.SAMPLING)
        return progress

    def test_a_disabled_job_decodes_nothing(self) -> None:
        progress = self._sampling(preview=False)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ) as encode:
            progress.offer_latent(object())
        encode.assert_not_called()
        self.assertEqual((0, None), progress.preview_frame())

    def test_an_enabled_job_decodes_and_numbers_the_frame(self) -> None:
        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        frame_id, frame = progress.preview_frame()
        self.assertEqual(1, frame_id)
        self.assertEqual("data:image/jpeg;base64,x", frame)

    def test_a_none_latent_is_not_a_frame(self) -> None:
        progress = self._sampling(preview=True)
        with mock.patch.object(preview_module, "encode_preview") as encode:
            progress.offer_latent(None)
        encode.assert_not_called()

    def test_frames_are_throttled_by_wall_clock(self) -> None:
        """The decode runs on the sampling thread, between two steps of the
        owner's real image. Neo throttles by STEP count, which is the wrong
        unit: a step is milliseconds at 512 and seconds at 2048."""

        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ) as encode:
            for _ in range(10):
                progress.offer_latent(object())
        self.assertEqual(1, encode.call_count)
        self.assertGreater(PREVIEW_MIN_INTERVAL_SECONDS, 0)

    def test_a_job_that_is_not_sampling_decodes_nothing(self) -> None:
        progress = HeadlessProgress("j", preview_enabled=True)
        progress.advance_to(JobState.LOADING)
        with mock.patch.object(preview_module, "encode_preview") as encode:
            progress.offer_latent(object())
        encode.assert_not_called()

    def test_the_hires_second_pass_is_a_preview_state(self) -> None:
        """That pass is exactly where a job looks hung, and
        `preview_available` reported False through the whole of it."""

        self.assertIn(JobState.SAMPLING, PREVIEW_STATES)
        self.assertIn(JobState.HIRES_SAMPLING, PREVIEW_STATES)

    def test_preview_available_means_a_frame_exists(self) -> None:
        """It used to mean `enabled and SAMPLING and step > 0`, which was true
        for the whole of every job that produced no frames at all."""

        progress = self._sampling(preview=True)
        self.assertFalse(progress.snapshot().preview_available)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        self.assertTrue(progress.snapshot().preview_available)

    def test_a_terminal_job_releases_its_frame(self) -> None:
        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        progress.mark_completed()
        self.assertEqual(None, progress.preview_frame()[1])
        self.assertFalse(progress.snapshot().preview_available)

    def test_a_cancelled_job_releases_its_frame(self) -> None:
        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        progress.mark_cancelled()
        self.assertEqual(None, progress.preview_frame()[1])

    def test_a_failed_job_releases_its_frame(self) -> None:
        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        progress.mark_failed("boom")
        self.assertEqual(None, progress.preview_frame()[1])

    def test_the_frame_is_not_in_the_loggable_snapshot(self) -> None:
        """Job records outlive their jobs and snapshots reach the log. A
        25 kB base64 string must not be in either."""

        progress = self._sampling(preview=True)
        with mock.patch.object(
            preview_module, "encode_preview", return_value="data:image/jpeg;base64,x"
        ):
            progress.offer_latent(object())
        serialised = progress.snapshot().to_dict()
        self.assertNotIn("preview", serialised)
        self.assertNotIn("preview_frame", serialised)
        self.assertNotIn("base64", repr(serialised))


class StateBridgeTests(unittest.TestCase):
    """The seam where the pixels were being thrown away."""

    def test_a_published_latent_is_offered_to_the_progress_model(self) -> None:
        progress = HeadlessProgress("j", preview_enabled=True)
        bridge = ForgeStateBridge(progress)
        with mock.patch.object(progress, "offer_latent") as offer:
            bridge.current_latent = "latent"
        offer.assert_called_once_with("latent")

    def test_the_latent_is_still_stored_for_forge_to_read_back(self) -> None:
        """Retained Forge reads `state.current_latent`, and `failure_cleanup`
        clears it by name. Offering it must not have replaced storing it."""

        bridge = ForgeStateBridge(HeadlessProgress("j"))
        bridge.current_latent = "latent"
        self.assertEqual("latent", bridge.current_latent)

    def test_other_writes_do_not_offer_a_frame(self) -> None:
        progress = HeadlessProgress("j", preview_enabled=True)
        bridge = ForgeStateBridge(progress)
        with mock.patch.object(progress, "offer_latent") as offer:
            bridge.job = "something"
            bridge.textinfo = "else"
        offer.assert_not_called()


class DecoderRefusalTests(unittest.TestCase):
    """The decoder answers None rather than importing Neo, or raising."""

    def test_it_refuses_when_the_engine_is_not_standing(self) -> None:
        """`neo_registries` learned this the hard way: importing Neo to
        answer a UI question left import-time state bound to a torn-down
        context and broke the next real generation."""

        self.assertNotIn("modules.sd_samplers_common", sys.modules)
        self.assertIsNone(preview_module.encode_preview(object()))

    def test_a_none_latent_is_refused(self) -> None:
        self.assertIsNone(preview_module.encode_preview(None))

    def test_it_never_raises_on_a_nonsense_latent(self) -> None:
        """This runs between two steps of the owner's real image. A preview
        that can fail a generation is worse than no preview."""

        with mock.patch.dict(
            sys.modules, {"modules.sd_samplers_common": mock.MagicMock()}
        ):
            self.assertIsNone(preview_module.encode_preview("not a tensor"))

    def test_it_imports_no_engine_at_module_scope(self) -> None:
        """A registry that populated itself on import would drag Neo into
        every Studio process, including the ones the purity tests keep clean."""

        source = (
            APP_ROOT / "forge_headless" / "preview_frame.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertNotIn(
                        name.split(".")[0],
                        {"modules", "torch", "PIL", "numpy"},
                        f"{name} is imported at module scope",
                    )

    def test_the_cheap_approximation_is_named_not_defaulted(self) -> None:
        """approximation=2 is the only mode that needs no weights file and no
        download. Leaving it None reads `opts.show_progress_type`, which can
        be TAESD -- a model Neo FETCHES on first use."""

        source = (
            APP_ROOT / "forge_headless" / "preview_frame.py"
        ).read_text(encoding="utf-8")
        self.assertIn("approximation=2", source)


class TransportTests(unittest.TestCase):
    """The socket message that carried `"preview": None` as a literal."""

    def test_a_backend_without_previews_answers_no_frame(self) -> None:
        """Concrete, not abstract: a backend that cannot decode a preview is
        a normal backend, not an incomplete one."""

        self.assertEqual((0, None), BackendAdapter.preview_frame(None, "j"))

    def test_the_socket_message_carries_the_frame(self) -> None:
        source = (
            APP_ROOT / "forge_studio" / "source_api_adapter.py"
        ).read_text(encoding="utf-8")
        block = source[source.index("def websocket_status"):]
        block = block[: block.index("def _preview_frame")]
        self.assertIn('"preview": frame', block)
        self.assertIn('"preview_id": frame_id', block)
        # COMMENTS STRIPPED FIRST. The method's own comment quotes the literal
        # it replaced, so a raw-text search finds the defect in the note
        # explaining that the defect is gone. This suite tripped that on its
        # first run, which is the third time this codebase has done it: a
        # docstring saying "no sys.platform", a comment saying "no
        # shell=True", and now this. Never a raw search over prose that
        # documents its own rule.
        code = "\n".join(
            line for line in block.splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn('"preview": None', code)


class ThePageSendsItTests(unittest.TestCase):
    def test_the_request_carries_the_toggle(self) -> None:
        """It was announced only on a `preview_config` socket message that the
        standalone server does not read."""

        app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("preview_enabled: !!State.livePreview", app_js)

    def test_the_toggle_the_owner_sees_still_exists(self) -> None:
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn("toggleLivePreview", html)

    def test_the_page_renders_what_arrives(self) -> None:
        app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("data.preview", app_js)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_PREVIEW_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
