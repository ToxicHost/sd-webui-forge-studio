"""The launcher must not die relaying a character. AR7.1.

THE DEFECT THIS CLOSES

`start_studio.py` is the owner's real entry point -- what `Start-Studio.bat`
invokes and what a double-click runs. During AR3.5 it died before an image was
ever produced:

    File "start_studio.py", line 113, in open_browser_when_ready
        sys.stdout.write(line)
    UnicodeEncodeError: 'charmap' codec can't encode character '�'

The chain, in full:

  1. the child is decoded with `errors="replace"`, so ANY byte it emits that is
     not valid UTF-8 arrives in the relay as U+FFFD;
  2. the relay writes that to the parent's stdout, which on Windows is cp1252
     by default and has no mapping for U+FFFD, so the write raises;
  3. nothing caught it, so the launcher exited;
  4. the launcher owned the pipe, so the child's stdout closed with it;
  5. the server's next log write during sampling raised `OSError`, which the
     RUNNING JOB surfaced as `GENERATION_FAILED`.

One unrenderable character, and the owner loses a generation. The decode side
was made tolerant and the encode side was not.

WHY THESE TESTS DRIVE A REAL SUBPROCESS

Because the failure lived in the seam between two processes and their two
encodings. A test that called `open_browser_when_ready` with a fake object
would exercise the loop and never touch the thing that broke -- the actual
encode against an actual stdout. `RelaySurvivesUnrenderableOutputTests` runs
the real function against a real child whose output contains U+FFFD, writing
to a real cp1252 stream.

Review: `Evidence/source-review/AR7.1-launcher-relay.md`.
"""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
LAUNCHER = REPO_ROOT / "start_studio.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXPECTED_RELAY_TESTS = 9

SOURCE = LAUNCHER.read_text(encoding="utf-8")


def _drain(process: subprocess.Popen) -> None:
    """Close the pipe and reap the child, so the suite leaves no warnings."""

    try:
        if process.stdout:
            process.stdout.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


def _relay():
    """Resolved at CALL time, like every other module reference in this suite."""

    import importlib

    module = importlib.import_module("start_studio")
    return module


class RelaySurvivesUnrenderableOutputTests(unittest.TestCase):
    """The decisive one: a REAL child, a REAL cp1252 stream."""

    def _child(self, payload: str) -> subprocess.Popen:
        """A child whose stdout carries `payload`, decoded exactly as the
        launcher decodes the real one."""

        return subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(%r); sys.stdout.flush()"
             % payload.encode("utf-8")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")

    def _with_cp1252_stdout(self, fn):
        """Run `fn` with stdout that cannot encode U+FFFD -- the owner's
        console, reproduced rather than imagined."""

        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
        saved = sys.stdout
        sys.stdout = stream
        try:
            fn()
        finally:
            # Read the bytes BEFORE letting the wrapper go: a TextIOWrapper
            # closes the stream it wraps, and `getvalue()` afterwards raises
            # "I/O operation on closed file".
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
            written = buffer.getvalue()
            sys.stdout = saved
            try:
                stream.detach()
            except Exception:  # noqa: BLE001
                pass
        return written

    def test_an_unrenderable_character_does_not_kill_the_relay(self) -> None:
        """Reproduces the exact failure. Fails against the original code."""

        process = self._child("hello � world\n")
        try:
            self._with_cp1252_stdout(
                lambda: _relay().open_browser_when_ready(process))
        finally:
            _drain(process)

    def test_the_child_is_not_killed_when_the_relay_gives_up(self) -> None:
        """The relay owning the pipe is what turned a display problem into a
        failed generation. Echoing is not worth the server."""

        process = self._child("�\n")
        try:
            self._with_cp1252_stdout(
                lambda: _relay().open_browser_when_ready(process))
            # Never terminated by us; it exits on its own.
            self.assertIsNone(process.returncode,
                              "the relay terminated the child")
        finally:
            _drain(process)

    def test_ordinary_output_still_reaches_the_console(self) -> None:
        """A fix that silences the launcher would be its own defect."""

        process = self._child("Studio backend ready in 6.3s\n")
        try:
            buffer = self._with_cp1252_stdout(
                lambda: _relay().open_browser_when_ready(process))
        finally:
            _drain(process)
        self.assertIn(b"Studio backend ready", buffer)

    def test_the_relay_survives_a_child_that_dies_mid_line(self) -> None:
        process = self._child("partial line with no newline")
        try:
            self._with_cp1252_stdout(
                lambda: _relay().open_browser_when_ready(process))
        finally:
            _drain(process)


class DecodeToleranceTests(unittest.TestCase):
    """The failure must not be able to move back to the READ side."""

    def test_the_child_is_decoded_with_replacement(self) -> None:
        self.assertIn('errors="replace"', SOURCE)
        self.assertIn('encoding="utf-8"', SOURCE)

    def test_the_write_side_is_made_as_tolerant_as_the_read_side(self) -> None:
        """The whole asymmetry, in one assertion."""

        self.assertIn('sys.stdout.reconfigure(errors="replace")', SOURCE)

    def test_the_reconfigure_is_guarded(self) -> None:
        """`reconfigure` is absent when stdout has been wrapped or replaced by
        a host, and the launcher must still start there."""

        at = SOURCE.index('sys.stdout.reconfigure(errors="replace")')
        after = SOURCE[at:at + 200]
        self.assertIn("except (AttributeError, ValueError, OSError)", after)


class ContractTests(unittest.TestCase):
    def test_check_still_exits_without_starting_a_server(self) -> None:
        """The shortcut can be tested without a window; that must survive."""

        result = subprocess.run(
            [sys.executable, str(LAUNCHER), "--check"],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Checks passed", result.stdout)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_RELAY_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
