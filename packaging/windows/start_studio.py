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
    if (app / "portable-runtime.json").is_file():
        return app / "runtime" / "python.exe"
    return app / "venv" / "Scripts" / "python.exe"


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


def open_browser_when_ready(process: subprocess.Popen) -> None:
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
        for line in process.stdout:
            try:
                sys.stdout.write(line)
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
            if not opened and line.startswith("Forge Studio internal alpha: http"):
                url = line.split(": ", 1)[1].strip()
                opened = True
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
    process = subprocess.Popen(
        command, cwd=str(APP), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    try:
        open_browser_when_ready(process)
        return process.wait()
    except KeyboardInterrupt:
        print("\n  Stopping Studio ...", flush=True)
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
