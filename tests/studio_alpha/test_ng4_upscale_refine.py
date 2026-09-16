"""NG-4: the Upscale panel's refine and Auto Detail switches reach something.

They had reached nothing since Studio shipped. The panel POSTed `run_refine`
and `run_ad` to `/studio/upscale_and_refine`, and `forge_headless.image_upscale`
refuses both BY NAME -- so every owner who ticked either box got
`UPSCALE_UNSUPPORTED`, and the rest of that payload (the prompt, the sampler,
the three `ad_slots`, the whole save group) described a pipeline that did not
exist.

The refusal is not softened here. What changes is where the second half runs:
the upscaled frame goes onto the Canvas and the CANONICAL generation runs over
it, because the Canvas is already what every image operation collects as its
source. "upscale -> refine -> Auto Detail -> save" is therefore a sequence the
proven path already performs, and cancellation, the public job id, progress,
the result save, the Gallery notification and the metadata are reported at that
path's stages rather than at stages this panel invented.

These guards are written against the INVARIANTS -- the panel never asks for
what the route refuses; the refinement uses the panel's OWN numbers; the
handler does not tidy away a presentation it just started -- rather than
against the literals that happen to express them today.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"

EXPECTED_NG4_TESTS = 30


def _strip_js_comments(source: str) -> str:
    """Comments out, code only.

    Recorded four times in this project: a word-ban guard that matches the
    comment explaining the ban proves nothing. Every assertion below runs
    against stripped source for that reason.
    """

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", source, flags=re.MULTILINE)


APP_JS_RAW = (FRONTEND / "app.js").read_text(encoding="utf-8")
APP_JS = _strip_js_comments(APP_JS_RAW)


def _balanced_block(source: str, opener: str) -> str:
    """From ``opener`` to the brace that closes ITS body.

    Counting is started at the opener's OWN final brace rather than at the
    start of the match, because `doGenerate(overrides = {})` and
    `(force = false) => {` both contain braces in the signature -- and a
    counter that starts before them closes on the default value and returns the
    signature alone. Every assertion against such a block then passes or fails
    for the wrong reason, which is the same class of harness bug as balancing
    `{}` on an array literal.
    """

    if not opener.endswith("{"):
        raise AssertionError(f"opener must end at the body brace: {opener!r}")
    start = source.index(opener)
    depth = 0
    for index in range(start + len(opener) - 1, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced block for {opener!r}")


DO_GENERATE = "async function doGenerate(overrides = {}) {"
AUTO_DETAIL_GROUP = "const _autoDetailGroup = (force = false) => {"


def _object_keys(literal: str) -> set[str]:
    """The key names of a flat object literal, shorthand included.

    A bare `(\\w+)\\s*[:,]` sweep also collects the VALUES that happen to sit
    before a comma, which is how this guard first reported `imageB64` and
    `false` as keys of the request.
    """

    inner = literal.strip()[1:-1]
    keys: set[str] = set()
    for line in inner.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        if ":" in line:
            keys.add(line.split(":", 1)[0].strip())
        else:
            keys.update(part.strip() for part in line.split(",") if part.strip())
    return keys


def _upscale_handler() -> str:
    return _balanced_block(
        APP_JS,
        'document.getElementById("upscaleBtn")?.addEventListener("click", async () => {',
    )


def _upscale_post_body() -> str:
    """The object literal the panel actually sends to the route."""

    handler = _upscale_handler()
    marker = 'body: JSON.stringify({'
    start = handler.index(marker) + len('body: JSON.stringify(')
    depth = 0
    for index in range(start, len(handler)):
        char = handler[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return handler[start:index + 1]
    raise AssertionError("the upscale POST body did not close")


class ThePanelNeverAsksForWhatTheRouteRefusesTests(unittest.TestCase):
    """The refusal stays, so the request must stop contradicting it."""

    def test_the_backend_still_refuses_both_fields_by_name(self):
        from forge_headless.contracts import HeadlessError
        from forge_headless.image_upscale import UPSCALE_UNSUPPORTED, upscale

        for kwargs in ({"run_refine": True}, {"run_ad": True},
                       {"run_refine": True, "run_ad": True}):
            with self.subTest(**kwargs):
                with self.assertRaises(HeadlessError) as caught:
                    upscale("", upscaler="R-ESRGAN 4x+", scale=2.0, **kwargs)
                self.assertEqual(UPSCALE_UNSUPPORTED, caught.exception.code)

    def test_the_refusal_fires_before_anything_expensive(self):
        """An empty image and a nonsense upscaler still refuse on the flags,
        which is what makes the refusal free rather than a late failure."""

        from forge_headless.contracts import HeadlessError
        from forge_headless.image_upscale import UPSCALE_UNSUPPORTED, upscale

        with self.assertRaises(HeadlessError) as caught:
            upscale("", upscaler="not-an-upscaler", scale=-1, run_ad=True)
        self.assertEqual(UPSCALE_UNSUPPORTED, caught.exception.code)

    def test_the_panel_sends_both_flags_off(self):
        body = _upscale_post_body()
        self.assertRegex(body, r"run_refine:\s*false")
        self.assertRegex(body, r"run_ad:\s*false")

    def test_the_panel_does_not_forward_its_toggles_to_the_route(self):
        body = _upscale_post_body()
        for name in ("runRefine", "runAD"):
            self.assertNotIn(
                name, body,
                f"the POST body still reads {name}; the route refuses it, so "
                "sending it is a request that cannot succeed",
            )

    def test_the_panel_stops_sending_fields_the_route_never_reads(self):
        """`upscale()` takes image_b64, upscaler and scale. Everything else in
        the old payload -- prompt, sampler, steps, cfg, seed, ad_slots and the
        entire save group -- was never read by anything."""

        body = _upscale_post_body()
        offenders = sorted(
            name for name in (
                "prompt", "neg_prompt", "steps", "sampler_name",
                "schedule_type", "cfg_scale", "denoising", "seed", "ad_slots",
                "save_outputs", "save_format", "save_quality", "save_lossless",
                "embed_metadata",
            ) if re.search(rf"\b{name}\s*:", body)
        )
        self.assertEqual(
            [], offenders,
            "the upscale request still describes a pipeline the route does "
            "not run",
        )

    def test_the_request_carries_exactly_what_the_route_takes(self):
        body = _upscale_post_body()
        keys = _object_keys(body)
        self.assertEqual(
            {"image_b64", "upscaler", "scale", "run_refine", "run_ad"},
            keys,
        )


class TheRefinementRunsOnTheCanonicalPathTests(unittest.TestCase):
    def test_the_panel_submits_through_do_generate(self):
        handler = _upscale_handler()
        self.assertRegex(
            handler, r"await doGenerate\(",
            "refine and Auto Detail must reuse the generation lifecycle, not "
            "a second pipeline built beside it",
        )

    def test_the_generation_is_only_submitted_when_asked_for(self):
        handler = _upscale_handler()
        submit = handler.index("await doGenerate(")
        preceding = handler[:submit]
        self.assertRegex(
            preceding, r"(runRefine\s*\|\|\s*runAD|refining)",
            "a plain upscale must not queue a generation",
        )

    def test_the_canvas_write_is_awaited_before_the_generation(self):
        """`doGenerate` collects the Canvas as its source. Submitting before
        the upscaled frame has landed would refine the PREVIOUS image."""

        handler = _upscale_handler()
        self.assertRegex(handler, r"await displayOnCanvas\(")
        self.assertLess(
            handler.index("await displayOnCanvas("),
            handler.index("await doGenerate("),
        )

    def test_display_on_canvas_is_awaitable_at_all(self):
        """It did all of its work in an async IIFE and dropped the promise, so
        awaiting it used to be a no-op that looked correct."""

        body = _balanced_block(APP_JS, "function displayOnCanvas(imgSrc, opts) {")
        self.assertRegex(
            body, r"return \(async \(\) => \{",
            "displayOnCanvas discards its promise again; every caller that "
            "awaits it is silently not waiting",
        )


class TheRefinementUsesThePanelsOwnNumbersTests(unittest.TestCase):
    """The panel has its own Steps and Denoise. Inheriting the generation
    panel's would be a visible control reaching nothing, one indirection
    further out."""

    def test_do_generate_accepts_overrides(self):
        self.assertRegex(APP_JS, r"async function doGenerate\(overrides = \{\}\)")

    def test_the_steps_field_honours_the_override(self):
        block = _balanced_block(APP_JS, DO_GENERATE)
        self.assertRegex(
            block,
            r"steps:\s*overrides\.steps\s*\?\?",
            "the submitted steps ignore the override",
        )

    def test_the_denoise_field_honours_the_override(self):
        block = _balanced_block(APP_JS, DO_GENERATE)
        self.assertRegex(
            block,
            r"denoising_strength:\s*overrides\.denoise\s*\?\?",
            "the submitted denoise ignores the override",
        )

    def test_the_overrides_use_nullish_coalescing_not_or(self):
        """A deliberate 0 is the whole point of the Auto-Detail-only case;
        `||` would replace it with the panel default."""

        block = _balanced_block(APP_JS, DO_GENERATE)
        for field in ("overrides.steps", "overrides.denoise"):
            index = block.index(field)
            self.assertRegex(block[index:index + 40], r"\?\?")

    def test_the_panel_passes_its_own_controls(self):
        handler = _upscale_handler()
        self.assertIn("paramUpscaleSteps", handler)
        self.assertIn("paramUpscaleDenoise", handler)
        call = handler[handler.index("await doGenerate("):]
        call = call[:call.index(")")]
        self.assertIn("userSteps", call)
        self.assertIn("userDenoise", call)

    def test_auto_detail_without_refine_leaves_the_frame_alone(self):
        handler = _upscale_handler()
        call = handler[handler.index("await doGenerate("):]
        self.assertRegex(
            call, r"denoise:\s*runRefine\s*\?\s*userDenoise\s*:\s*0",
            "Auto Detail with refine off must not re-render the picture",
        )


class TheAutoDetailGateTests(unittest.TestCase):
    """The panel's own Auto Detail checkbox is the toggle the owner pressed;
    the slots are the same DOM elements, so nothing is duplicated."""

    def test_the_group_builder_takes_a_force_argument(self):
        self.assertRegex(APP_JS, r"const _autoDetailGroup = \(force = false\)")

    def test_only_the_master_gate_is_bypassed(self):
        group = _balanced_block(APP_JS, AUTO_DETAIL_GROUP)
        self.assertRegex(group, r"!force\s*\n?\s*&&\s*!document\.getElementById\(\"checkAD\"\)")
        self.assertIn("checkAD${n}", group,
                      "per-slot enables must still be read from the slots")

    def test_the_submission_forwards_the_force_flag(self):
        block = _balanced_block(APP_JS, DO_GENERATE)
        self.assertIn("_autoDetailGroup(overrides.forceAutoDetail)", block)

    def test_a_normal_generation_is_unchanged(self):
        """`force` defaults to false, so the ordinary Generate click still
        gates on the master toggle exactly as before."""

        group = _balanced_block(APP_JS, AUTO_DETAIL_GROUP)
        self.assertIn("force = false", group)


class TheHandoffDoesNotTidyTheJobAwayTests(unittest.TestCase):
    """`doGenerate` returns at the 202, not at completion, and it has already
    started the generation presentation. A handler that then reset the progress
    bar and the status to "ready" would erase a job that is still running."""

    def test_the_reset_is_conditional_on_not_having_handed_off(self):
        handler = _upscale_handler()
        self.assertIn("handedOff", handler)
        reset = handler[handler.rindex("clearInterval(_vramPoll);"):]
        self.assertRegex(
            reset, r"if \(!handedOff\)",
            "the panel resets a presentation it did not start",
        )

    def test_the_button_is_always_restored(self):
        """Whether or not a job was queued, the panel's own button must come
        back -- it is not part of the generation presentation."""

        handler = _upscale_handler()
        reset = handler[handler.rindex("clearInterval(_vramPoll);"):]
        button_line = reset[:reset.index("if (!handedOff)")]
        self.assertIn("btn.disabled = false", button_line)


class TheBundledDetectorsAreReachableTests(unittest.TestCase):
    """NG-4 asks for evidence that Auto Detail "detects and details at least
    one region". It could not: `adetailer` was an unconfigured role, so
    `scan_configured_detectors` yielded nothing and the Model dropdown was
    empty -- on an install that SHIPS five hash-recorded detectors.

    The only way out was for the owner to open Settings and type a path inside
    Studio's own install directory. That is the chore the standing ruling
    forbids: Studio decides where its own assets live.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        from forge_studio.launch import APP_ROOT

        cls.bundled = APP_ROOT / "models" / "adetailer"
        cls.fixtures = APP_ROOT.parent / "Evidence" / "ng4-detector-root"
        cls.fixtures.mkdir(parents=True, exist_ok=True)
        cls._tmp = tempfile.TemporaryDirectory(dir=cls.fixtures, prefix="cfg-")
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _load(self, document: dict) -> dict:
        """`load_config` with the fields it requires filled in.

        `result_root` is mandatory and unrelated to what is under test, so it
        is supplied rather than asserted about.
        """

        import json

        from forge_studio.launch import load_config

        path = self.tmp / "studio-config.json"
        path.write_text(
            json.dumps({"result_root": "Studio-Results", **document}),
            encoding="utf-8",
        )
        return load_config(path)

    def test_the_bundled_directory_is_where_the_catalogue_says(self):
        from forge_studio.detector_catalogue import BUNDLED_DETECTORS

        self.assertTrue(self.bundled.is_dir(), f"missing: {self.bundled}")
        present = {p.name for p in self.bundled.glob("*.pt")}
        self.assertTrue(
            set(BUNDLED_DETECTORS) <= present,
            "the recorded bundled detectors are not all on disk",
        )

    def test_an_unconfigured_role_is_seeded_with_the_bundled_directory(self):
        config = self._load({"backend": "headless", "model_roots": {}})
        self.assertEqual(
            (str(self.bundled),), config["model_roots"].get("adetailer"))

    def test_a_config_with_no_model_roots_at_all_is_still_seeded(self):
        config = self._load({"backend": "headless"})
        self.assertEqual(
            (str(self.bundled),), config["model_roots"].get("adetailer"))

    def test_an_owner_configured_root_is_not_overridden(self):
        """The seed fills an EMPTY role. An owner who names their own detector
        directory has decided, and Studio does not append to that decision."""

        mine = self.tmp / "my-detectors"
        mine.mkdir(exist_ok=True)
        config = self._load({
            "backend": "headless",
            "model_roots": {"adetailer": str(mine)},
        })
        self.assertEqual(
            (str(mine),), config["model_roots"]["adetailer"])

    def test_the_seed_does_not_disturb_the_other_roles(self):
        other = self.tmp / "ckpt"
        other.mkdir(exist_ok=True)
        config = self._load({
            "backend": "headless",
            "model_roots": {"checkpoint": str(other)},
        })
        self.assertEqual((str(other),), config["model_roots"]["checkpoint"])
        self.assertIn("adetailer", config["model_roots"])

    def test_the_seeded_root_actually_offers_the_bundled_detectors(self):
        """The seam, not the two sides of it. A root that is configured but
        yields nothing would be the same empty dropdown with a different
        cause."""

        from forge_headless.model_roots import ModelRootRegistry
        from forge_studio.detector_catalogue import (
            BUNDLED_DETECTORS,
            scan_configured_detectors,
        )
        from forge_studio.launch import WORKSPACE_ROOT

        config = self._load({"backend": "headless", "model_roots": {}})
        registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
        registry.configure_tolerantly(config["model_roots"])

        offered = scan_configured_detectors(registry)
        by_name = {d.name: d for d in offered}
        for name in BUNDLED_DETECTORS:
            with self.subTest(detector=name):
                self.assertIn(name, by_name)
                self.assertTrue(
                    by_name[name].verified,
                    "a bundled detector whose bytes do not match is refused, "
                    "so an unverified one here means the shipped file changed",
                )

    def test_a_name_the_owner_was_offered_resolves_to_a_file(self):
        """Offering a name that cannot be opened at generation time is the
        two-enumerators disagreement this module was built to prevent."""

        from forge_headless.model_roots import ModelRootRegistry
        from forge_studio.detector_catalogue import resolve_detector_path
        from forge_studio.launch import WORKSPACE_ROOT

        config = self._load({"backend": "headless", "model_roots": {}})
        registry = ModelRootRegistry(workspace_root=WORKSPACE_ROOT)
        registry.configure_tolerantly(config["model_roots"])

        path = resolve_detector_path("face_yolov8n.pt", registry)
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).is_file())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self):
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_NG4_TESTS, found)


if __name__ == "__main__":
    unittest.main()
