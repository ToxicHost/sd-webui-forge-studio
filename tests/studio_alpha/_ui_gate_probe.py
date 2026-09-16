"""Subprocess probe: launcher readiness facts for the UI-gate fixes.

Runs the DOCUMENTED launcher command -- the venv interpreter, no ``-u`` --
with stdout/stderr piped, which is exactly the arrangement that exposed
defect D3: the pre-fix URL announcement sat in a block buffer while a
consumer waited. Three scenarios, one JSON document between markers:

1. a valid mock-backend configuration: exactly one flushed ``STUDIO_READY``
   line within a bounded wait, a loopback connect against the announced
   port, and a bounded process stop;
2. a refused configuration: exit 2, a plain-sentence error, and NO ready
   line -- a failed startup must never look ready;
3. ``onboarding: "suppressed"``: the announced URL carries
   ``?onboarding=off`` (defect D4's smoke mode).

Mock backend throughout: no torch, no payload, no CUDA anywhere near this
probe. The loopback socket work lives here, in a subprocess, because the
canonical runner forbids dialling and serving in the test process itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path(sys.argv[1]).resolve()

OUT: dict[str, object] = {"ready": {}, "refused": {}, "suppressed": {},
                          "fatal": None}

PYTHON = str(APP_ROOT / "venv" / "Scripts" / "python.exe")
LAUNCHER = str(APP_ROOT / "launch_studio.py")


def _config(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "backend": "mock",
        "host": "127.0.0.1",
        "port": 0,
        "result_root": str(SCRATCH / "probe-results"),
        # No `profiles`, `selected_profile_id` or `autoload`. The launcher
        # tolerates them but honours none of them, and a probe that keeps
        # writing them would be rehearsing a config shape no owner should be
        # given any more.
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _launch(config: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, LAUNCHER, "--config", str(config)],
        cwd=str(APP_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


def _read_until_ready(process: subprocess.Popen,
                      timeout: float = 25.0) -> tuple[str | None, list[str], float]:
    started = time.monotonic()
    deadline = started + timeout
    lines: list[str] = []
    ready = None
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue
        lines.append(line.rstrip())
        if line.startswith("STUDIO_READY "):
            ready = line.strip()
            break
    return ready, lines, time.monotonic() - started


def _stop(process: subprocess.Popen) -> dict[str, object]:
    process.terminate()
    try:
        process.wait(timeout=20)
        return {"stopped": True, "returncode": process.returncode}
    except subprocess.TimeoutExpired:
        process.kill()
        return {"stopped": False, "returncode": None}


def main() -> int:  # noqa: C901
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)

        # ---- scenario 1: ready line, flushed, bound, connectable ----------
        process = _launch(_config(SCRATCH / "good.json"))
        try:
            ready, lines, elapsed = _read_until_ready(process)
            facts: dict[str, object] = {
                "ready_line": ready,
                "seconds_to_ready": round(elapsed, 2),
                "read_without_dash_u": True,
                "lines_before_ready": lines[:-1] if ready else lines,
            }
            if ready:
                fields = dict(part.split("=", 1)
                              for part in ready.split(" ")[1:] if "=" in part)
                facts["host"] = fields.get("host")
                facts["port_is_integer"] = fields.get("port", "").isdigit()
                base = f"http://{fields['host']}:{fields['port']}"
                with urllib.request.urlopen(
                    f"{base}/api/model/state", timeout=10
                ) as response:
                    facts["loopback_connect_status"] = response.status
                    facts["state"] = json.loads(response.read().decode())["state"]
                # The URL line follows the ready line; default mode carries
                # no onboarding flag.
                url_line = process.stdout.readline().strip()
                facts["url_line_present"] = url_line.startswith(
                    "Forge Studio internal alpha: http://127.0.0.1:"
                )
                facts["default_mode_has_no_flag"] = (
                    "onboarding=off" not in url_line
                )
                facts["single_ready_line"] = True  # a second would be read below
            facts.update(_stop(process))
            remainder = process.stdout.read() or ""
            facts["extra_ready_lines"] = remainder.count("STUDIO_READY ")
            OUT["ready"] = facts
        finally:
            if process.poll() is None:
                process.kill()

        # ---- scenario 2: refused configuration emits no ready line --------
        #
        # `autoload: true` used to be the refusal driven here. It is tolerated
        # now -- there is no explicit load for it to bypass -- so the scenario
        # is anchored on a refusal that cannot become tolerable: internal
        # alpha binds loopback only, and a config asking for 0.0.0.0 must
        # never reach a bind, let alone announce one.
        # The `finally` is not decoration. Scenarios 1 and 3 have always had
        # one; this scenario did not, because a refused configuration exits on
        # its own and there was nothing to clean up. That held only while the
        # configuration really was refused. When `autoload: true` became
        # TOLERATED, this launch started serving instead of exiting,
        # `communicate` hit its timeout, the exception left the scenario -- and
        # the process kept running. Three of them were still listening on
        # loopback thirteen hours later, found by looking at the machine rather
        # than at the test result.
        #
        # A probe that starts a server owes a kill on every path out of the
        # block, including the ones it does not expect to take.
        process = _launch(_config(SCRATCH / "bad.json", host="0.0.0.0"))
        try:
            stdout, stderr = process.communicate(timeout=30)
            OUT["refused"] = {
                "returncode": process.returncode,
                "ready_line_emitted": "STUDIO_READY " in (stdout or ""),
                "plain_sentence": "binds 127.0.0.1 only" in (stderr or ""),
            }
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

        # ---- scenario 3: suppressed onboarding flags the URL ---------------
        process = _launch(
            _config(SCRATCH / "suppressed.json", onboarding="suppressed")
        )
        try:
            ready, _lines, _elapsed = _read_until_ready(process)
            url_line = process.stdout.readline().strip() if ready else ""
            OUT["suppressed"] = {
                "ready_line": bool(ready),
                "url_carries_flag": url_line.endswith("/studio/?onboarding=off"),
            }
            OUT["suppressed"].update(_stop(process))
        finally:
            if process.poll() is None:
                process.kill()
        return 0
    except BaseException as exc:  # noqa: BLE001
        OUT["fatal"] = {"kind": type(exc).__name__, "message": str(exc)[:300]}
        return 1
    finally:
        print("PROBE_JSON_BEGIN")
        print(json.dumps(OUT, default=str))
        print("PROBE_JSON_END")


if __name__ == "__main__":
    raise SystemExit(main())
