"""Two owner actions that used to fail without saying so.

Both were found on one live leg against the real triplet, and both had the
same shape: the product knew something had gone wrong and told nobody.

```text
Generate        submitted seed -1 to a backend that refuses seed < 0 by
                design, so every click failed -- and the reason was
                discarded before it reached the owner
                (see test_job_failure_detail.py for that half)

Send to Canvas  returned early on a source it could not decode, leaving no
                layer, no toast, and one console line as the only evidence
```

These read `app.js` as a committed file, the way the Model Folders surface
tests do. Nothing here launches a browser: the claim is about what the shipped
source is allowed to do, which is exactly what regressed.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"
APP_JS = (FRONTEND / "app.js").read_text(encoding="utf-8")
LOCALE_EN = json.loads(
    (FRONTEND / "locales" / "en.json").read_text(encoding="utf-8")
)


def _block(start_marker: str, length: int, *, until: str | None = None) -> str:
    """A slice of app.js starting at a marker.

    `length` is a FALLBACK. Prefer `until`, which bounds the slice by a second
    marker, because a byte count silently stops covering what it was written to
    cover as soon as anything above the target grows.

    That is not hypothetical: `const jobParams = {` was sliced at 700 chars with
    `seed: _resolveSubmittedSeed()` ending at 652 -- 48 characters of headroom.
    Adding a `hires` group with an explanatory comment pushed the seed past the
    cutoff, and the test failed claiming the seed default had regressed. The
    assertion was about the seed; what actually changed was a comment.
    """

    start = APP_JS.index(start_marker)
    if until is not None:
        end = APP_JS.index(until, start)
        return APP_JS[start:end]
    return APP_JS[start:][:length]


# --------------------------------------------------------------------------
# 1. Generate submits a seed this backend will accept
# --------------------------------------------------------------------------


class SubmittedSeedTests(unittest.TestCase):
    """`forge_headless/studio_generation.py` raises GENERATION_SEED_NOT_FIXED
    for `seed < 0`, deliberately: substituting a seed would make the seed
    reported on the result a lie. The resolution therefore belongs on the
    client, where the chosen value can travel with the request."""

    def _job_params(self) -> str:
        # Bounded by the submit call that CONSUMES jobParams, so the
        # window covers the whole literal however it grows.
        return _block(
            "const jobParams = {", 700,
            until="lifecycle.submitGenerate(jobParams)",
        )

    def test_lifecycle_submit_does_not_default_the_seed_to_minus_one(self) -> None:
        # The exact regression: `parseInt(...) || -1` shipped -1 on every
        # click where the field was blank or set to -1.
        params = self._job_params()
        self.assertNotIn("|| -1", params)
        self.assertIn("seed: _resolveSubmittedSeed()", params)

    def test_resolver_exists_and_is_used_by_the_lifecycle_path(self) -> None:
        self.assertIn("function _resolveSubmittedSeed()", APP_JS)
        self.assertEqual(1, APP_JS.count("seed: _resolveSubmittedSeed()"))

    def test_the_resolver_accepts_any_seed_the_owner_typed(self) -> None:
        """AR6.2. This asserted a CEILING, and the ceiling was not a refusal
        -- a larger seed fell through to the random branch below, so the owner
        got a different image with no message.

        The floor stays: a negative value is "surprise me" and is the one
        substitution that is legitimate, because there is no concrete seed
        being taken away.
        """

        body = _block("function _resolveSubmittedSeed()", 400)
        self.assertIn("raw >= 0", body)
        self.assertNotIn("SEED_MAX", body)
        self.assertNotIn("SEED_MAX", APP_JS)

    def test_random_seed_is_drawn_not_left_negative(self) -> None:
        body = _block("function _resolveSubmittedSeed()", 400)
        self.assertIn("crypto.getRandomValues", body)
        # Uint32Array values are 0..4294967295 by construction, so the drawn
        # seed is in range without further clamping.
        self.assertIn("Uint32Array(1)", body)

    def test_no_negative_seed_literal_survives_in_the_resolver(self) -> None:
        body = _block("function _resolveSubmittedSeed()", 400)
        self.assertNotIn("-1", body)


# --------------------------------------------------------------------------
# 2. Send to Canvas reports its own failures
# --------------------------------------------------------------------------


class SendToCanvasFeedbackTests(unittest.TestCase):
    """Every path that ends without an Output layer must say so.

    A stale result handle answers `/studio/file` with a 404 JSON body. An
    `<img>` cannot decode that, so `displayOnCanvas` returned early -- and the
    owner saw a button that did nothing, with no message anywhere in the UI.
    """

    def _display_on_canvas(self) -> str:
        # Wide enough to reach the render-error path at the end of the
        # function; the decode-hang fix lengthened the body.
        start = APP_JS.index("function displayOnCanvas(imgSrc, opts)")
        return APP_JS[start:][:12000]

    def test_image_load_failure_is_shown_to_the_owner(self) -> None:
        body = self._display_on_canvas()
        marker = "displayOnCanvas: failed to load image"
        self.assertIn(marker, body)
        after = body[body.index(marker):][:400]
        self.assertIn("showToast", after)
        self.assertIn("toast.canvas.loadFailed", after)

    def test_render_failure_is_shown_to_the_owner(self) -> None:
        body = self._display_on_canvas()
        marker = "displayOnCanvas: render error"
        self.assertIn(marker, body)
        after = body[body.index(marker):][:400]
        self.assertIn("showToast", after)
        self.assertIn("toast.canvas.renderFailed", after)

    def test_no_console_only_failure_path_remains(self) -> None:
        # Each displayOnCanvas failure console.error must be accompanied by a
        # toast. A console line alone is what made this invisible.
        body = self._display_on_canvas()
        failures = [
            m.start()
            for m in re.finditer(r'console\.error\("\[Studio\] displayOnCanvas', body)
        ]
        self.assertTrue(failures, "expected displayOnCanvas failure logging")
        for at in failures:
            self.assertIn("showToast", body[at:at + 400])

    def test_decode_is_never_the_only_gate_before_drawing(self) -> None:
        """The original symptom, finally attributed.

        `HTMLImageElement.decode()` is tied to the rendering pipeline: while
        `document.visibilityState === "hidden"` Chromium never settles it.
        Awaiting it as the sole gate meant the async body of displayOnCanvas
        stalled forever -- no Output layer, no console error, no toast, and
        `img.complete` already true at full size. Reproduced live at 768x768.

        The load event is the real prerequisite for drawImage. decode only
        avoids a raster hitch, so it must be bounded or skippable.
        """

        body = self._display_on_canvas()
        self.assertNotIn("await imgEl.decode();", body)
        gate = body[body.index("imgEl.src = imgSrc;"):][:1400]
        # The load is awaited...
        self.assertIn("imgEl.onload", gate)
        self.assertIn("imgEl.onerror", gate)
        self.assertIn("imgEl.complete", gate)
        # ...and decode cannot stall it.
        self.assertIn("Promise.race", gate)
        self.assertIn("setTimeout", gate)

    def test_an_already_complete_image_does_not_wait_for_a_load_event(self) -> None:
        # A cached image fires no load event, so waiting for one unconditionally
        # would hang just as badly as decode() did.
        gate = self._display_on_canvas()
        window = gate[gate.index("imgEl.src = imgSrc;"):][:1400]
        self.assertIn("imgEl.complete && imgEl.naturalWidth > 0", window)

    def test_empty_selection_click_is_not_a_silent_no_op(self) -> None:
        block = _block('document.getElementById("outputToCanvas")', 1200)
        self.assertIn("displayOnCanvas(img,", block)
        self.assertIn("else", block)
        self.assertIn("toast.canvas.noResult", block)


# --------------------------------------------------------------------------
# 3. The text encoder can actually be chosen on a lifecycle host
# --------------------------------------------------------------------------


class TextEncoderRowIsContextualTests(unittest.TestCase):
    """The Text Encoder dropdown appears only for a checkpoint that needs one.

    This suite replaces `TextEncoderRowReachabilityTests`, which pinned a
    WORKAROUND. The row ships hidden and `restoreTextEncoderForModel` decides
    from `/studio/check_model_te` -- but that route answered a hardcoded
    constant, so it said `needs_te:false` for every model and hid the row while
    Load went on demanding a text encoder. Load was unreachable, and the fix
    then was to force the row visible on a lifecycle host and re-reveal it on a
    1500 ms timer.

    That inverted the defect rather than removing it: an SDXL checkpoint, which
    carries its own CLIP-L and CLIP-G, was shown a control it has no use for
    and refused to load without one.

    The probe reads the checkpoint header now, so the Extension's own branch
    (`frontend/app.js:1529-1533` in the oracle) decides again, and these tests
    pin that: the probe is asked, its answer is obeyed, and nothing overrides
    it afterwards.
    """

    MODEL_CONTROLS = (FRONTEND / "studio-model-controls.js").read_text(encoding="utf-8")

    def _restore_fn(self) -> str:
        start = APP_JS.index("async function restoreTextEncoderForModel(")
        return APP_JS[start:][:2600]

    def test_the_probe_is_asked_before_anything_decides(self) -> None:
        body = self._restore_fn()
        self.assertIn("await checkModelTE(modelTitle)", body)
        self.assertNotIn(
            "lifecycleAvailable()", body,
            "a lifecycle short-circuit would answer before the header does")

    def test_a_bundled_checkpoint_hides_the_row_and_pins_none(self) -> None:
        body = self._restore_fn()
        branch = body[body.index("if (!needsTE)"):][:220]
        self.assertIn('teRow.style.display = "none"', branch)
        self.assertIn('teSelect.value = "None"', branch)

    def test_a_checkpoint_that_needs_one_gets_the_row(self) -> None:
        body = self._restore_fn()
        after = body[body.index("if (!needsTE)"):]
        self.assertIn('teRow.style.display = ""', after)

    def test_nothing_re_reveals_the_row_on_a_timer(self) -> None:
        # A 1500 ms poll used to put the row back, which would now fight the
        # header's own answer a second and a half after an SDXL checkpoint
        # correctly hid it.
        self.assertNotIn("revealTextEncoderRow", self.MODEL_CONTROLS)

    def test_the_route_reads_a_header_rather_than_answering_a_constant(self) -> None:
        adapter = (APP_ROOT / "forge_studio" / "source_api_adapter.py").read_text(
            encoding="utf-8")
        start = adapter.index('if route == "/studio/check_model_te":')
        body = adapter[start:][:1400]
        self.assertIn("inspect_checkpoint", body)

    def test_the_detector_is_the_extensions_own(self) -> None:
        # Ported, not reinvented: same branch order, same hardcoded block
        # counts. A detector that classifies differently from the Extension
        # would show the row for a model the Extension does not.
        from forge_headless.architecture import detect_architecture

        sdxl = {"model.diffusion_model.input_blocks.0.0.weight",
                "conditioner.embedders.0.transformer.x",
                "first_stage_model.decoder.conv_in.weight"}
        self.assertEqual("sdxl", detect_architecture(sdxl)["arch"])
        self.assertEqual(20, detect_architecture(sdxl)["blocks"])

    def test_an_sdxl_checkpoint_is_told_it_needs_nothing(self) -> None:
        from forge_headless.architecture import component_needs

        verdict = component_needs({
            "model.diffusion_model.input_blocks.0.0.weight",
            "conditioner.embedders.0.transformer.text_model.x",
            "first_stage_model.decoder.conv_in.weight",
        })
        self.assertFalse(verdict["needs_te"])
        self.assertFalse(verdict["needs_vae"])

    def test_an_anima_checkpoint_is_told_it_needs_both(self) -> None:
        from forge_headless.architecture import component_needs

        verdict = component_needs({
            "net.blocks.0.adaln_modulation_cross_attn.1.weight",
            "net.llm_adapter.0.weight",
        })
        self.assertTrue(verdict["needs_te"])
        self.assertTrue(verdict["needs_vae"])
        self.assertEqual("cosmos", verdict["arch"])


# --------------------------------------------------------------------------
# 4. The messages are real strings an owner can read
# --------------------------------------------------------------------------


class FeedbackStringsTests(unittest.TestCase):
    KEYS = (
        "toast.canvas.loadFailed",
        "toast.canvas.renderFailed",
        "toast.canvas.noResult",
    )

    def test_every_new_key_is_translated_in_the_fallback_locale(self) -> None:
        missing = sorted(key for key in self.KEYS if key not in LOCALE_EN)
        self.assertEqual([], missing, f"untranslated keys: {missing}")

    def test_messages_are_plain_and_carry_no_path_or_code(self) -> None:
        for key in self.KEYS:
            text = LOCALE_EN[key]
            self.assertTrue(text.strip(), f"{key} is blank")
            self.assertNotIn("\\", text)
            self.assertNotIn("/studio/file", text)
            self.assertNotIn("undefined", text)

    def test_each_call_site_passes_a_fallback_string(self) -> None:
        # _i18n(key, fallback): a missing locale entry must still produce a
        # readable message rather than the key itself.
        for key in self.KEYS:
            call = APP_JS[APP_JS.index(f'_i18n("{key}",'):][:200]
            self.assertRegex(call, r'_i18n\("[^"]+",\s*\n?\s*"[^"]+"')


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
