"""NG-2: Clip Skip, across the seam it actually has to cross.

The trap this guards is specific and it is in Neo, not in Studio. Neo
conditions the model from `opts.CLIP_stop_at_last_layers`
(`modules/processing.py:467`) and writes the infotext from `p.clip_skip`
(`modules/processing.py:712`). An implementation that sets `p.clip_skip` --
the obvious one, and the one a field-name port produces -- would change the
RECORDED metadata and leave the image alone. Studio would report a Clip Skip
the model never used.

So the tests below assert the option scope, not the processing object, and one
of them exists purely to stop the trap being reintroduced.

Review: `Evidence/source-review/AR8.6-clip-skip.md`.

Distinctive non-default values throughout, per §13: 7 and 11, never 1 or 2,
because 2 is the engine default and 1 is what a stray boolean would coerce to.
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

from forge_headless.studio_generation import (  # noqa: E402
    STUDIO_REQUEST_FIELDS,
    translate_request,
)
from forge_studio.contracts import GenerationRequest  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    PresentationError,
    _validated_clip_skip,
    _validated_request_payload,
)

EXPECTED_NG2_TESTS = 19

FRONTEND = APP_ROOT / "forge_studio" / "frontend"


def _request(**overrides) -> GenerationRequest:
    fields = {
        "model_id": "model-token",
        "positive_prompt": "a lighthouse",
        "negative_prompt": "",
        "seed": 7,
        "steps": 4,
        "cfg_scale": 5.0,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


def _body(**generation) -> dict:
    """The canonical wire shape: identity outside, request inside.

    `model` is an identity field and lives at the TOP level; putting it inside
    `generation` is refused by name, which is the mixed-body shape
    `_canonical_body` exists to catch.
    """

    fields = {
        "positive_prompt": "a lighthouse",
        "negative_prompt": "",
        "seed": 7,
        "steps": 4,
        "cfg_scale": 5.0,
        "width": 768,
        "height": 768,
    }
    fields.update(generation)
    return {"model": "model-token", "generation": fields}


class AdmissionTests(unittest.TestCase):
    """Bounds are the ENGINE'S, read off `modules/shared_options.py`."""

    def test_absent_stays_absent(self) -> None:
        self.assertIsNone(_validated_clip_skip(None))
        self.assertIsNone(_validated_request_payload(_body())["clip_skip"])

    def test_the_engine_range_is_accepted_at_both_ends(self) -> None:
        self.assertEqual(1, _validated_clip_skip(1))
        self.assertEqual(12, _validated_clip_skip(12))

    def test_a_distinctive_value_survives_admission(self) -> None:
        self.assertEqual(
            7, _validated_request_payload(_body(clip_skip=7))["clip_skip"]
        )

    def test_below_the_range_is_refused(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_clip_skip(0)

    def test_above_the_range_is_refused(self) -> None:
        with self.assertRaises(PresentationError):
            _validated_clip_skip(13)

    def test_a_non_integer_is_refused(self) -> None:
        for value in ("2", 2.5, [2], {"clip_skip": 2}):
            with self.subTest(value=value):
                with self.assertRaises(PresentationError):
                    _validated_clip_skip(value)

    def test_a_boolean_is_refused_rather_than_read_as_one(self) -> None:
        """`True` is an `int` in Python, and 1 is a legal Clip Skip.

        Without the explicit bool check, `clip_skip: true` would be admitted
        as "disable Clip Skip" -- a value the owner never asked for, arriving
        from a client that plainly meant something else.
        """

        with self.assertRaises(PresentationError):
            _validated_clip_skip(True)
        with self.assertRaises(PresentationError):
            _validated_clip_skip(False)

    def test_the_field_is_accepted_inside_generation(self) -> None:
        """Without the whitelist entry the request is refused as unknown."""

        payload = _validated_request_payload(_body(clip_skip=11))
        self.assertEqual(11, payload["clip_skip"])


class TranslationTests(unittest.TestCase):
    def test_the_value_crosses_the_boundary(self) -> None:
        headless = translate_request(
            _request(clip_skip=7), request_id="rid"
        ).headless_request
        self.assertEqual(7, headless.clip_skip)

    def test_absent_crosses_as_absent(self) -> None:
        """None must not become 2 here.

        Coercing it would start overriding an engine option on every job that
        never asked, which is the difference between an additive field and a
        behaviour change for everyone.
        """

        headless = translate_request(
            _request(), request_id="rid"
        ).headless_request
        self.assertIsNone(headless.clip_skip)

    def test_it_is_reported_as_carried_and_not_as_a_backend_default(
        self,
    ) -> None:
        self.assertIn("clip_skip", STUDIO_REQUEST_FIELDS)
        translation = translate_request(_request(clip_skip=7), request_id="rid")
        self.assertIn("clip_skip", translation.carried)


class OptionScopeTests(unittest.TestCase):
    """The half that decides whether the image actually changes."""

    def _overrides_for(self, request) -> dict:
        """What `generate()` would put in the job scope for this request.

        Read out of the source rather than by running a generation, because
        running one needs an engine and a model. The behaviour under test is
        the mapping from request field to override key, and that is what this
        reads.
        """

        source = (
            APP_ROOT / "forge_headless" / "live_generation_port.py"
        ).read_text(encoding="utf-8")
        body = source[source.index("    def generate(self, request"):]
        body = body[: body.index("with job_scope(")]
        return body

    def test_generate_maps_clip_skip_to_the_engine_option_name(self) -> None:
        body = self._overrides_for(None)
        self.assertIn('overrides["CLIP_stop_at_last_layers"]', body)
        self.assertIn('getattr(request, "clip_skip", None)', body)

    def test_the_override_is_omitted_when_the_job_names_none(self) -> None:
        """`if clip_skip is not None` and not a falsy test.

        A falsy test would drop a legitimate value only if 0 were legal, which
        it is not -- but it would also read as if 0 meant absent, and the next
        person to widen the range would inherit the bug.
        """

        body = self._overrides_for(None)
        self.assertIn("if clip_skip is not None:", body)

    def test_the_option_name_is_the_one_neo_reads(self) -> None:
        """Asserted against Neo's own declaration, not against a literal here.

        If the engine ever renames the option, this fails rather than silently
        overriding a key nothing reads.
        """

        options = (APP_ROOT / "modules" / "shared_options.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"CLIP_stop_at_last_layers": OptionInfo(', options)

        headless = (
            APP_ROOT / "forge_headless" / "headless_options.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"CLIP_stop_at_last_layers",', headless)


class TheTrapTests(unittest.TestCase):
    def test_studio_never_sets_clip_skip_on_the_processing_object(self) -> None:
        """The whole reason this work needed a source review first.

        `modules/processing.py:712` writes the infotext from `p.clip_skip` when
        it is set, while `:467` conditions the model from the option. Setting
        the attribute would make Studio record a Clip Skip the model never
        used -- silently changing metadata, which this project forbids by name.
        """

        for name in (
            "forge_headless/live_generation_port.py",
            "forge_headless/studio_generation.py",
            "forge_studio/presentation.py",
        ):
            source = (APP_ROOT / name).read_text(encoding="utf-8")
            source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
            source = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)
            source = re.sub(r'"""(?:.|\n)*?"""', "", source)
            # `assertTrue` on a boolean rather than `assertNotIn` on the file:
            # `assertNotIn` puts the whole haystack in the failure message, and
            # a 34 KB failure is one people learn to skim past. Measured while
            # mutation-proving this guard.
            with self.subTest(file=name):
                offenders = [
                    line.strip()
                    for line in source.splitlines()
                    if ".clip_skip =" in line
                    or 'setattr(processing, "clip_skip"' in line
                ]
                self.assertEqual(
                    offenders, [],
                    f"{name} assigns clip_skip onto an object. If that object "
                    "is the processing object, Neo writes the value into the "
                    "infotext (modules/processing.py:712) while conditioning "
                    "the model from the OPTION (:467) -- so the recorded "
                    "metadata would disagree with the image. Pass it through "
                    f"job_scope instead. Offending line(s): {offenders}",
                )


class ControlTests(unittest.TestCase):
    """The control the PNG-import path has always targeted and never had."""

    def test_the_control_exists_with_the_engine_range(self) -> None:
        markup = (FRONTEND / "index.html").read_text(encoding="utf-8")
        match = re.search(r'id="paramClipSkip"[^>]*', markup)
        self.assertIsNotNone(match, "#paramClipSkip is missing from index.html")
        cell = match.group(0)
        self.assertIn('data-min="1"', cell)
        self.assertIn('data-max="12"', cell)
        self.assertIn('value="2"', cell)

    def test_the_png_import_path_now_has_something_to_write_to(self) -> None:
        """`app.js` has targeted `#paramClipSkip` since before it existed."""

        app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.assertIn("paramClipSkip", app_js)

    def test_the_collector_sits_above_the_lifecycle_return(self) -> None:
        """A generation field below the return reaches nothing.

        Nine fields were found down there over this project's life. This one
        must not become the tenth, so its position is asserted rather than
        assumed.
        """

        app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
        start = app_js.index("async function doGenerate(")
        anchor = app_js.index("lifecycle.submitGenerate(", start)
        boundary = app_js.index("\n    return;", anchor)
        live = app_js[start:boundary]
        self.assertIn("clip_skip", live)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_declared_count_matches_what_is_discovered(self) -> None:
        found = unittest.defaultTestLoader.loadTestsFromName(
            __name__
        ).countTestCases()
        self.assertEqual(EXPECTED_NG2_TESTS, found)


if __name__ == "__main__":
    unittest.main()
