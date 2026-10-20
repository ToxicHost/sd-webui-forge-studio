"""Start the packaged Studio app, creating a clean configuration on first run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
def interpreter_for(app: Path) -> Path:
    # The marker also ensures a damaged portable runtime never falls back to a venv.
    # Windows keeps `Scripts\python.exe`; macOS and Linux keep `bin/python`
    # (the checkout launcher reuses this module on every platform, MAC1).
    if os.name == "nt":
        if (app / "portable-runtime.json").is_file():
            return app / "runtime" / "python.exe"
        return app / "venv" / "Scripts" / "python.exe"
    if (app / "portable-runtime.json").is_file():
        return app / "runtime" / "bin" / "python3"
    return app / "venv" / "bin" / "python"


PYTHON = interpreter_for(APP)
LAUNCHER = APP / "launch_studio.py"
CONFIG = ROOT / "studio-config.json"
TEMPLATE = APP / "docs" / "studio" / "internal-alpha" / "studio-config.template.json"


def fail(message: str) -> int:
    print(f"\n  {message}\n", flush=True)
    return 2



def bootstrap_config() -> int:
    """Create current-schema configuration without scanning model locations."""
    if CONFIG.exists():
        return 0
    try:
        config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        config["port"] = 0
        config["model_roots"] = {}
        config["studio_state_root"] = str(ROOT / "Studio-State")
        # Exclusive creation preserves a configuration written by another start.
        with CONFIG.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
    except FileExistsError:
        return 0
    except (OSError, ValueError, TypeError) as exc:
        return fail(f"Could not create configuration: {exc}")
    print("Created studio-config.json. Choose your model folders in Settings.",
          flush=True)
    return 0


#: OOM1. How many unexpected stops in how long before the launcher stops
#: restarting: a server that dies at every start is not helped by a loop.
RESTART_LIMIT = 3
RESTART_WINDOW_SECONDS = 600.0

#: Physical RAM use at or above this, at the last sample before a stop, is
#: reported as running out of memory.
LOW_MEMORY_PERCENT = 90.0

#: RAM use at or above this while a job runs prints one console warning for
#: that job. The owner's 2026-10-19 crash sat at 96-99 %; a busy Windows
#: machine at 85-90 % is normal, so the line is drawn above that. Replaces the
#: pre-Generate estimate (owner, 2026-10-20: "too aggressive" -- it told a
#: Klein job it needed 18 GB): this is a measurement, not a guess.
RAM_WARN_PERCENT = 95.0


def crash_record_path() -> Path:
    """Where the launcher writes why Studio last stopped. Read at call time:
    the checkout launcher (`start_studio.py` at the repo root) rebinds ROOT."""

    return ROOT / "last-crash.json"


class Watch:
    """What the launcher knows about the running server, for the crash record.

    Only facts that carry no prompt: whether the server reached ready, the last
    RAM sample, and the size/steps/Hires/Auto Detail of the job that was
    running, parsed from Studio's own `studio.job:` log lines."""

    def __init__(self) -> None:
        self.ready = False
        self.ram_percent: float | None = None
        self.available_gib: float | None = None
        self.job: dict | None = None
        self.warned = False
        self.done = threading.Event()

    def sample_memory(self) -> None:
        try:
            import psutil
        except Exception:  # noqa: BLE001 - no psutil, no memory facts
            return
        while not self.done.wait(2.0):
            try:
                memory = psutil.virtual_memory()
                self.ram_percent = float(memory.percent)
                self.available_gib = round(memory.available / (1 << 30), 1)
            except Exception:  # noqa: BLE001
                return
            self.warn_if_low()

    def warn_if_low(self) -> None:
        """One console line per job when RAM is nearly full while it runs."""

        if self.job is None or self.warned or self.ram_percent is None:
            return
        if self.ram_percent < RAM_WARN_PERCENT:
            return
        self.warned = True
        try:
            # Leading newline: a progress bar may be mid-line on the console.
            print(f"\n  Warning: RAM is {self.ram_percent:.0f}% full ({self.available_gib} GB free) "
                  "during this job. Studio may slow down a lot, or stop.\n"
                  "  To make room: close other programs, turn off Hires or Auto Detail, "
                  "or pick a smaller model.\n", flush=True)
        except Exception:  # noqa: BLE001 - a warning is never worth the launcher
            pass

    def note(self, line: str) -> None:
        if line.startswith("Forge Studio internal alpha: http"):
            self.ready = True
            return
        if "studio.job:" not in line:
            return
        if " DISPATCH " in line:
            fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
            self.job = {key: fields[key] for key in ("size", "steps", "sampler") if key in fields}
            self.warned = False
        elif self.job is not None and " HIRES " in line:
            fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
            self.job["hires"] = fields.get("scale", "on")
        elif self.job is not None and " AUTODETAIL " in line and " slots=" in line:
            self.job["auto_detail"] = True
        elif " RESULT " in line or " FAILED " in line or "event=cancelled" in line:
            self.job = None


def write_crash_record(code: int, watch: Watch) -> dict:
    """The facts the restarted server shows the page. Never raises."""

    import datetime

    low = watch.ram_percent is not None and watch.ram_percent >= LOW_MEMORY_PERCENT
    record = {
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        "exit_code": code,
        "low_memory": low,
        "ram_percent": watch.ram_percent,
        "available_gib": watch.available_gib,
        "job": watch.job,
    }
    try:
        path = crash_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return record


def _relayed(stream):
    """Yield `(line, echo)`: text to write as it arrives, and each finished line.

    The pipe used to be read line by line in text mode, whose universal
    newlines turn every `\\r` into a line break. tqdm redraws its bar with
    `\\r`, so each redraw became a new console line (owner, 2026-10-20: "weird
    things in the console's progress bar"). Here the bytes are passed through
    as they arrive, `\\r` intact, so the bar redraws in place; `\\r\\n` from the
    child becomes `\\n`, which the console writes as its own line end.
    `line` (None when a chunk finishes none) is for the ready line and the
    crash watch, split on both `\\r` and `\\n`.

    A stream without a byte buffer (a host's replacement) is read by lines.
    """

    raw = getattr(stream, "buffer", None)
    if raw is None or not hasattr(raw, "read1"):
        for line in stream:
            yield line, line
        return
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    carry, pending = "", ""
    while True:
        chunk = raw.read1(65536)
        text = carry + decoder.decode(chunk, final=not chunk)
        carry = ""
        if chunk and text.endswith("\r"):
            # Possibly half of a `\r\n`: decided by the next chunk.
            text, carry = text[:-1], "\r"
        text = text.replace("\r\n", "\n")
        if not text and not chunk:
            break
        pending += text
        pieces = pending.replace("\r", "\n").split("\n")
        pending = pieces.pop()
        finished = [piece for piece in pieces if piece]
        yield (finished[0] + "\n" if finished else None), text
        for piece in finished[1:]:
            yield piece + "\n", ""
        if not chunk:
            break
    if pending:
        yield pending, ""


def open_browser_when_ready(process: subprocess.Popen, watch: Watch | None = None,
                            open_browser: bool = True) -> None:
    """Stream the launcher's output; open the browser on the ready line.

    ECHOING IS NOT THE JOB, and it used to be able to take Studio down.

    The child is decoded below with `errors="replace"`, so any byte it emits
    that is not valid UTF-8 arrives here as U+FFFD. Writing that to a Windows
    console -- cp1252 by default -- raised `UnicodeEncodeError`, this function
    propagated it, and the launcher exited. It owned the pipe, so the child's
    stdout closed with it and the server's next log write during sampling
    raised `OSError`, which the running job surfaced as `GENERATION_FAILED`.

    Measured: one generation lost that way, to a single character nobody was
    ever going to read. The decode side was made tolerant and the encode side
    was not.

    So: make the write as tolerant as the read, and treat a relay failure as
    the end of the ECHO rather than the end of Studio. The browser still opens
    if the ready line was already seen.
    """

    # As tolerant as the `Popen` decode below. Guarded: `reconfigure` is
    # missing when stdout has been wrapped or replaced by a host.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

    opened = False
    try:
        for line, echo in _relayed(process.stdout):
            try:
                if echo:
                    sys.stdout.write(echo)
                    sys.stdout.flush()
            except (UnicodeEncodeError, OSError, ValueError):
                # `UnicodeEncodeError` is listed first for the reader, not for
                # the interpreter: it is a subclass of `ValueError`, so the
                # outer handler below would already catch it. Named here
                # because it is the failure this exists for, and because
                # stopping cleanly WITH a message beats unwinding silently.
                #
                # Said once, then the echo stops and the SERVER KEEPS RUNNING.
                # Reported through stderr because stdout is what just failed.
                print("  (console output stopped; Studio is still running)",
                      file=sys.stderr, flush=True)
                break
            if line is None:
                continue
            if watch is not None:
                watch.note(line)
            if not opened and line.startswith("Forge Studio internal alpha: http"):
                url = line.split(": ", 1)[1].strip()
                opened = True
                if open_browser:
                    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    except (OSError, ValueError):
        # The pipe itself failed. Same rule: the child is not ours to kill.
        pass


def main() -> int:
    print("\n  Forge Studio\n", flush=True)
    if not PYTHON.exists():
        return fail(f"Missing interpreter:\n  {PYTHON}")
    if not LAUNCHER.exists():
        return fail(f"Missing launcher:\n  {LAUNCHER}")

    if (APP / "portable-runtime.json").is_file():
        # GitPython is imported by inherited engine helpers, but portable users
        # need no Git operations. Permit import when the optional executable is absent.
        os.environ["GIT_PYTHON_REFRESH"] = "quiet"
        sys.path.insert(0, str(APP))
        from scripts.portable_runtime import check
        errors = check(APP)
        if errors:
            return fail("\n  ".join(errors) + "\n  Extract a fresh complete portable release.")

    arguments = sys.argv[1:]
    # `--check` validates everything and exits WITHOUT starting a server or
    # opening a browser, so the shortcut can be tested without a window
    # appearing on someone's screen.
    checking = "--check" in arguments
    if checking:
        arguments.remove("--check")

    status = bootstrap_config()
    if status != 0:
        return status

    if checking:
        sys.path.insert(0, str(APP))
        from forge_studio.launch import LaunchConfigurationError, load_config
        try:
            load_config(CONFIG)
        except LaunchConfigurationError as exc:
            return fail(str(exc))
        print("  Checks passed:")
        print(f"    interpreter  {PYTHON.name}")
        print(f"    launcher     {LAUNCHER.name}")
        print(f"    config       {CONFIG.name}")
        print("\n  Ready. Double-click to start Studio for real.\n", flush=True)
        return 0

    command = [str(PYTHON)]
    if (APP / "portable-runtime.json").is_file():
        command.extend(["-I", "-B"])
    command.extend([str(LAUNCHER), "--config", str(CONFIG)])
    command.extend(arguments)             # e.g. --mock
    return supervise(command)


def supervise(command: list[str], *, popen=None) -> int:
    """Run the server; if it stops unexpectedly AFTER it was ready, record why
    and start it again (OOM1).

    The server is a child so that this process outlives it. A native crash --
    torch dying inside an allocation when physical RAM is exhausted, or the
    Linux OOM killer -- ends the server without a Python traceback, and the
    page used to be left talking to nothing. Now the launcher writes
    `last-crash.json` (exit code, last RAM sample, the job's size and passes),
    restarts the server on the same port, and the page -- which reconnects
    every 3 s by itself -- shows what happened. The browser is not reopened.

    Not restarted: a clean exit (0), a server that never reached ready (a
    configuration or startup failure a restart would only repeat), Ctrl+C,
    or more than RESTART_LIMIT stops in RESTART_WINDOW_SECONDS."""

    import time

    # Looked up at call time, so a test that patches `subprocess.Popen` on
    # this module reaches the spawn.
    popen = popen or subprocess.Popen
    environment = dict(os.environ)
    environment["STUDIO_CRASH_RECORD"] = str(crash_record_path())
    # The pipe is decoded as UTF-8 below, but a child writing into a pipe uses
    # the ANSI code page (cp1252), so non-ASCII log text arrived as U+FFFD and
    # tqdm, seeing a non-UTF-8 stream, drew its ASCII bar ("10%|#2"). An
    # owner's own setting wins.
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    stops: list[float] = []
    first = True
    while True:
        watch = Watch()
        process = popen(
            command, cwd=str(APP), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env=environment,
        )
        sampler = threading.Thread(target=watch.sample_memory, daemon=True)
        sampler.start()
        try:
            open_browser_when_ready(process, watch, open_browser=first)
            code = process.wait()
        except KeyboardInterrupt:
            watch.done.set()
            print("\n  Stopping Studio ...", flush=True)
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            return 0
        watch.done.set()
        # The sampler's last reading is the one the record reports.
        sampler.join(timeout=5)
        if code == 0 or not watch.ready:
            return code
        record = write_crash_record(code, watch)
        now = time.monotonic()
        stops = [t for t in stops if now - t < RESTART_WINDOW_SECONDS] + [now]
        reason = ("the computer ran out of memory (RAM)" if record["low_memory"]
                  else f"exit code {code}")
        if len(stops) > RESTART_LIMIT:
            print(f"\n  Studio stopped unexpectedly again ({reason}).\n"
                  f"  It stopped {len(stops)} times in {int(RESTART_WINDOW_SECONDS // 60)} minutes,"
                  " so it was not restarted. Start it again when ready.\n", flush=True)
            return code
        print(f"\n  Studio stopped unexpectedly ({reason}). Restarting ...\n", flush=True)
        first = False


if __name__ == "__main__":
    raise SystemExit(main())
