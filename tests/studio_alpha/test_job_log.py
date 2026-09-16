"""What Studio prints per job, and what it must never print.

An entire owner session driving FOUR generations produced 38 log lines, 23 of
them INFO and almost all from startup. Nothing was logged per job -- not one
line. Neo's own output reaches the console unchanged, so nothing was being
swallowed; Studio simply emitted almost nothing of its own, and Studio is the
layer that knows the facts an owner needs.

That matters because when a friend says "it didn't work", the console is the
only artefact you get.

Almost everything asserted here was ALREADY COMPUTED and never reported: the
loads/reuses/switches counters, the stage labels, the recipe assembled for
metadata, the failure reason that now survives the record, and the VRAM
figures measured for the OOM message.

The two prohibitions are the point of the module and most of this suite:

```text
no prompt text        `to_dict` deliberately carries only LENGTHS. A console
                      is pasted into bug reports and chats.
no filesystem paths   the rule every public projection here obeys. A model
                      NAME is safe; the file it came from is not.
```

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. Nothing is generated;
the logger is driven directly and its output captured.
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.job_log import (  # noqa: E402
    LOGGER_NAME,
    JobLog,
    _safe,
    make_name_resolver,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_JOB_LOG_TESTS = 27


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = _Capture()
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.addHandler(self.capture)
        self.logger.setLevel(logging.INFO)
        self.addCleanup(self.logger.removeHandler, self.capture)
        self.log = JobLog()

    @property
    def text(self) -> str:
        return "\n".join(self.capture.lines)


class TheTraceExistsTests(_Base):
    """Nothing was logged per job. Every line below is one that was missing."""

    def test_a_job_announces_itself(self) -> None:
        self.log.accepted("studio-job-000001", queue_position=2)
        self.assertIn("JOB ACCEPTED", self.text)
        self.assertIn("job=studio-job-000001", self.text)
        self.assertIn("queued_at=2", self.text)

    def test_the_resolved_selection_travels_by_name(self) -> None:
        """"The owner picked three things; the log should say which three,
        not three hashes.\""""

        log = JobLog(names=lambda role, model_id: {
            "checkpoint": "Hicks_Anima_Beta",
            "text_encoder": "Qwen3-0.6B",
            "vae": "qwen_image_vae",
        }[role])
        log.accepted("j", selection={
            "checkpoint_model_id": "2" * 32,
            "text_encoder_model_id": "4" * 32,
            "vae_model_id": "2" * 32,
        })
        self.assertIn("checkpoint=Hicks_Anima_Beta", self.text)
        self.assertIn("text_encoder=Qwen3-0.6B", self.text)
        self.assertIn("vae=qwen_image_vae", self.text)
        self.assertNotIn("2" * 32, self.text)

    def test_an_unresolvable_id_is_shortened_not_dumped(self) -> None:
        self.log.accepted("j", selection={"checkpoint_model_id": "a" * 32})
        self.assertIn("checkpoint=aaaaaaaa…", self.text)
        self.assertNotIn("a" * 32, self.text)

    def test_a_broken_name_resolver_never_breaks_the_line(self) -> None:
        def boom(role, model_id):
            raise RuntimeError("catalogue gone")

        JobLog(names=boom).accepted("j", selection={"vae_model_id": "b" * 32})
        self.assertIn("JOB ACCEPTED", self.text)

    def test_load_reuse_or_switch_is_reported(self) -> None:
        """"Reusing warm session" is the single most reassuring line a slow
        app can print, and the counters behind it were invisible."""

        self.log.session("j", action="reuse", reason="selection unchanged")
        self.assertIn("MODEL SESSION", self.text)
        self.assertIn("action=reuse", self.text)
        self.assertIn("reason=selection", self.text)

    def test_dispatch_reports_resolved_values(self) -> None:
        self.log.dispatch("j", resolved={
            "sampler": "Euler", "scheduler": "Karras", "steps": 20,
            "width": 512, "height": 768, "seed": 7, "preview_enabled": True,
        })
        self.assertIn("DISPATCH", self.text)
        self.assertIn("sampler=Euler", self.text)
        self.assertIn("size=512x768", self.text)
        self.assertIn("seed=7", self.text)
        self.assertIn("preview=on", self.text)

    def test_an_absent_field_is_omitted_not_printed_as_none(self) -> None:
        """`upscaler=None` invites the reader to believe one was chosen and
        rejected, when none was asked for."""

        self.log.dispatch("j", resolved={"sampler": "Euler", "scheduler": ""})
        self.assertNotIn("None", self.text)
        self.assertNotIn("scheduler=", self.text)

    def test_the_hires_recipe_names_its_target(self) -> None:
        """That gap is exactly where a Hires job looks hung."""

        self.log.hires("j", recipe={
            "scale": 1.5, "upscaler": "remacri_original",
            "second_pass_steps": 15, "denoising_strength": 0.3,
            "target": "768x768",
        })
        self.assertIn("HIRES", self.text)
        self.assertIn("upscaler=remacri_original", self.text)
        self.assertIn("target=768x768", self.text)

    def test_stages_are_printed(self) -> None:
        self.log.stage("j", stage="Hires sampling")
        self.assertIn("STAGE", self.text)
        self.assertIn("stage=Hires sampling", self.text)

    def test_queue_transitions_carry_depth(self) -> None:
        self.log.queue("enqueued", job_id="j", depth=3)
        self.assertIn("QUEUE", self.text)
        self.assertIn("event=enqueued", self.text)
        self.assertIn("depth=3", self.text)

    def test_a_result_reports_elapsed_size_and_a_handle(self) -> None:
        self.log.result("j", elapsed_seconds=42.549, width=768, height=768,
                        handle="opaque-handle-1")
        self.assertIn("RESULT", self.text)
        self.assertIn("elapsed=42.5s", self.text)
        self.assertIn("size=768x768", self.text)
        self.assertIn("handle=opaque-handle-1", self.text)

    def test_a_failure_prints_its_reason(self) -> None:
        """The reason now EXISTS, after the `error: null` repair, and the
        console still did not print it."""

        self.log.failure("j", code="GENERATION_SEED_NOT_FIXED",
                         message="This backend requires an explicit seed")
        self.assertIn("FAILED", self.text)
        self.assertIn("code=GENERATION_SEED_NOT_FIXED", self.text)
        self.assertIn("explicit seed", self.text)

    def test_vram_is_reported_when_known(self) -> None:
        self.log.vram("j", moment="load", free=6 * 1024 ** 3, total=16 * 1024 ** 3)
        self.assertIn("VRAM", self.text)
        self.assertIn("free=6.0GiB", self.text)

    def test_vram_is_silent_when_unmeasured(self) -> None:
        """Better absent than invented -- the same rule the progress model
        applies to `fraction`."""

        self.log.vram("j", moment="load", free=None, total=None)
        self.assertEqual("", self.text)


class NothingSensitiveEscapesTests(_Base):
    """The two prohibitions, tested as prohibitions."""

    def test_a_windows_path_is_refused(self) -> None:
        self.assertIn("withheld", _safe(r"RESULT handle=C:\Users\o\x.png"))

    def test_a_unc_path_is_refused(self) -> None:
        self.assertIn("withheld", _safe(r"RESULT at=\\server\share\x"))

    def test_a_posix_path_is_refused(self) -> None:
        self.assertIn("withheld", _safe("RESULT handle=/home/o/models/x.pt"))

    def test_an_ordinary_line_passes(self) -> None:
        line = "DISPATCH job=studio-job-000001 sampler=Euler size=512x512"
        self.assertEqual(line, _safe(line))

    def test_a_model_name_with_dots_is_not_mistaken_for_a_path(self) -> None:
        """A NAME is safe. Refusing every dotted token would withhold the
        very line this module exists to print."""

        line = "JOB ACCEPTED job=j checkpoint=Hicks_Anima_Beta.v2 vae=qwen.vae"
        self.assertEqual(line, _safe(line))

    def test_a_leaking_line_is_refused_whole_not_trimmed(self) -> None:
        """A redaction would produce a nearly-right message and hide that
        anything went wrong. A refusal is visible in the console."""

        self.assertNotIn("Users", _safe(r"RESULT handle=C:\Users\o\x.png"))

    def test_the_result_line_carries_a_handle_and_no_path(self) -> None:
        self.log.result("j", handle="opaque-1", width=512, height=512)
        self.assertNotIn("\\", self.text)
        self.assertNotIn("/", self.text)

    def test_no_method_accepts_a_prompt(self) -> None:
        """Enforced by SHAPE: there is no parameter to pass one to. The
        request's own `to_dict` carries only lengths for the same reason."""

        import inspect

        from forge_studio.job_log import FORBIDDEN_FIELDS

        for name, method in inspect.getmembers(JobLog, inspect.isfunction):
            if name.startswith("_"):
                continue
            parameters = set(inspect.signature(method).parameters)
            for forbidden in FORBIDDEN_FIELDS:
                with self.subTest(method=name, field=forbidden):
                    self.assertNotIn(forbidden, parameters)


class NameResolverTests(unittest.TestCase):
    def test_it_finds_the_display_name(self) -> None:
        class _Entry:
            model_id = "a" * 32
            display_name = "Hicks_Anima_Beta"

        class _Registry:
            def entries(self, role):
                return (_Entry(),)

        resolver = make_name_resolver(_Registry())
        self.assertEqual("Hicks_Anima_Beta", resolver("checkpoint", "a" * 32))

    def test_an_unknown_id_resolves_to_nothing(self) -> None:
        class _Registry:
            def entries(self, role):
                return ()

        self.assertIsNone(make_name_resolver(_Registry())("vae", "z" * 32))

    def test_a_raising_registry_resolves_to_nothing(self) -> None:
        class _Registry:
            def entries(self, role):
                raise RuntimeError("root vanished")

        self.assertIsNone(make_name_resolver(_Registry())("vae", "z" * 32))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_JOB_LOG_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
