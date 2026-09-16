"""GA-BROWSER-01 — the Dynamic Prompts choice, through a browser and a restart.

    python Evidence/ga_browser_01_dynamic_prompts.py \
        --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe"

WHAT THIS PROVES, AND WHAT IT DOES NOT

The write was repaired in `0b698b48`: two `POST /studio/dynamic_prompts/config`
branches existed and the second -- the only one that stored anything -- was
unreachable, so the toggle could not be turned off. The proof so far reaches a
fresh `WildcardService` reading the stored choice back. That is not the row.

The row is: change it IN THE BROWSER, restart the OWNING PROCESS, see it
restored in both API and UI, and prove the restored choice reaches inference.

NO GPU, NO MODEL. Expansion happens at ADMISSION -- `presentation._resolve_prompts`
reads the durable stored flag long before any engine is involved -- so the mock
backend is sufficient and the card stays free. The mock also renders
`positive_prompt[:72]` as visible SVG text, so the resolved prompt is both
machine-readable in the poll body and legible in a screenshot.

THE GENERATION IS ISSUED FROM PAGE CONTEXT, NOT BY CLICKING GENERATE, and that
limitation is deliberate and reported. On a mock host the button's body carries
`model: ""` with identity under `model_selection`; mock does not gate generation,
so validation rejects the empty model id before expansion is ever reached. A
page-context call to the same public `/api/generate` route exercises the
identical admission path with a valid mock identity. It is sufficient for a
Dynamic Prompts persistence gate and it is NOT WP1's Generate-button acceptance.

PRIVACY. Everything is synthetic and lives under this run's own directory. The
owner's `Studio-State/wildcards.json` points at a folder outside the workspace
and is never read, never written, and never referenced.

The REPORTED artefacts -- transcript, report, generated images -- carry no home
directory: `redact()` strips it, and a check asserts that. `studio-config.mock.json`
is the exception and is a runtime INPUT rather than a report: `launch_studio.py`
needs real absolute paths, so it keeps them. It is a fixture, not evidence, and
it is regenerated on every run.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
EVIDENCE_ROOT = WORKSPACE_ROOT / "Evidence"
PYTHON = APP_ROOT / "venv" / "Scripts" / "python.exe"

RUN_ROOT = EVIDENCE_ROOT / "ga-browser-01"
STATE_ROOT = RUN_ROOT / "state"
RESULT_ROOT = RUN_ROOT / "results"
WILDCARD_ROOT = RUN_ROOT / "wildcards"
CONFIG_PATH = RUN_ROOT / "studio-config.mock.json"
TRANSCRIPT = RUN_ROOT / "ga-browser-01.json"
REPORT = RUN_ROOT / "ga-browser-01.txt"

READY = re.compile(r"STUDIO_READY host=(\S+) port=(\d+)")

#: One line, so the expansion is deterministic and the assertion is exact.
#: `__colour__ hair` becomes `red hair` or stays literal -- there is no third
#: outcome to explain away.
WILDCARD_FILE = "colour.txt"
WILDCARD_LINES = "red\n"
PROMPT = "a __colour__ hair"
RESOLVED = "a red hair"

#: A real id from the mock catalogue. See `mock_backend.py`.
MOCK_MODEL = "studio-mock-illustration-v1"

sys.path.insert(0, str(Path(__file__).resolve().parent))


class LiveFailure(RuntimeError):
    pass


def reap(process: subprocess.Popen) -> None:
    """Stop a child and WAIT for it.

    `terminate()` only asks. Windows holds the profile directory open until the
    process is really gone, so a run that merely asked left `.chrome-profile`
    locked and the next run died trying to clear its own evidence root -- with
    a PermissionError that names the directory and not the cause. The session
    bridge records six Studio processes accumulating this way once.
    """

    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)


CHECKS: list[dict[str, Any]] = []


def redact(value: Any) -> Any:
    """Strip the home directory from anything written to an artefact.

    Every path here is a synthetic fixture under this run's own directory --
    there is no private model or wildcard location anywhere near it. But the
    workspace path still spells out the owner's home directory, and a report
    carrying it is not one you can hand to anybody. The same rule
    `scripts/capture_baseline.py` follows, for the same reason.
    """

    home = str(Path.home())
    if isinstance(value, str):
        for spelling in (home, home.replace("\\", "/")):
            value = value.replace(spelling, "<home>")
        return value
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def check(name: str, expected: Any, actual: Any) -> bool:
    ok = expected == actual
    CHECKS.append({"check": name, "expected": redact(expected),
                   "actual": redact(actual), "ok": ok})
    print(f"   [{'ok' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         expected {expected!r}")
        print(f"         actual   {actual!r}")
    return ok


def request(port: int, method: str, path: str,
            payload: Any = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if body is None else {"Content-Type": "application/json"}
    call = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, headers=headers,
        method=method)
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw


class Studio:
    """One launched Studio PROCESS. Its pid is part of the evidence.

    A real subprocess, not an in-process server on a daemon thread. That is the
    whole point of the row: `run_r0_browser_truth.py` builds its server in
    process and passes no state root, so it cannot restart anything and its
    preferences die with it.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.port = 0
        self.pid = 0
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> "Studio":
        print(f"\n-- {self.label} ---------------------------------------")
        self.process = subprocess.Popen(
            [str(PYTHON), "launch_studio.py", "--config", str(CONFIG_PATH)],
            cwd=str(APP_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace")
        self.pid = self.process.pid
        deadline = time.monotonic() + 120
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                if self.process.poll() is not None:
                    raise LiveFailure(f"{self.label} exited before ready")
                continue
            found = READY.search(line)
            if found:
                self.port = int(found.group(2))
                print(f"   pid {self.pid} ready on port {self.port}")
                return self
        raise LiveFailure(f"{self.label} never announced STUDIO_READY")

    def __exit__(self, *_exception: object) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=30)
        print(f"   pid {self.pid} stopped (exit {self.process.returncode})")


async def open_browser(browser: Path, base: str):
    """Headless Chrome over CDP, with its profile inside this run."""

    import websockets
    from run_r0_browser_truth import CDP  # noqa: E402

    profile = RUN_ROOT / ".chrome-profile"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True)
    chrome = subprocess.Popen(
        [str(browser), "--headless=new", "--remote-debugging-port=0",
         f"--user-data-dir={profile}", "--no-first-run", "--disable-extensions",
         "--disable-background-networking", "--disable-component-update",
         "--disable-sync", "--metrics-recording-only", "--disable-default-apps",
         "--disable-gpu", "--window-size=1400,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not port_file.exists():
        await asyncio.sleep(0.1)
    if not port_file.exists():
        chrome.terminate()
        raise LiveFailure("Chrome never wrote DevToolsActivePort")
    devtools = int(port_file.read_text(encoding="utf-8").splitlines()[0])
    targets = None
    for _ in range(100):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{devtools}/json/list", timeout=3
            ) as response:
                targets = json.loads(response.read())
            break
        except OSError:
            await asyncio.sleep(0.1)
    if not targets:
        chrome.terminate()
        raise LiveFailure("Chrome exposed no debugging targets")
    page = next(t for t in targets if t.get("type") == "page")
    socket = await websockets.connect(page["webSocketDebuggerUrl"],
                                      max_size=32 * 1024 * 1024)
    cdp = CDP(socket)
    for domain in ("Page.enable", "Runtime.enable", "Log.enable",
                   "Network.enable"):
        await cdp.call(domain)
    await cdp.call("Page.navigate", {"url": f"{base}/studio/?onboarding=off"})
    await cdp.drain(3.0)
    return chrome, socket, cdp


async def toggle_state(cdp: Any) -> Any:
    """What the PAGE shows, read from the toggle's own class."""

    return await cdp.evaluate(
        "(() => { const t = document.getElementById('toggleStudioDynPrompts');"
        " return t ? t.classList.contains('on') : null; })()")


async def click_toggle(cdp: Any) -> None:
    await cdp.evaluate(
        "document.getElementById('toggleStudioDynPrompts')?.click()")
    await cdp.drain(1.5)


async def generate_in_page(cdp: Any, prompt: str) -> dict:
    """The real public route, from the page's own origin.

    NOT the Generate button. See the module docstring: on a mock host the
    button submits an empty `model` and is refused before expansion. This
    exercises the same admission path with a valid mock identity.
    """

    expression = (
        "(async () => {"
        "  const submit = await fetch('/api/generate', {method:'POST',"
        "    headers:{'Content-Type':'application/json'},"
        "    body: JSON.stringify({model: '%s', generation: {"
        "      positive_prompt: %s, negative_prompt: '', seed: 7, steps: 4,"
        "      cfg_scale: 4.0, width: 256, height: 256}})});"
        "  const admitted = await submit.json();"
        "  if (!submit.ok) return {error: admitted, status: submit.status};"
        "  const id = admitted.job_id || admitted.id;"
        "  for (let i = 0; i < 120; i++) {"
        "    const poll = await fetch('/api/jobs/' + id);"
        "    const job = await poll.json();"
        "    if (['completed','failed','cancelled'].includes(job.state))"
        "      return {job};"
        "    await new Promise(r => setTimeout(r, 250));"
        "  }"
        "  return {timeout: true};"
        "})()"
    ) % (MOCK_MODEL, json.dumps(prompt))
    result = await cdp.call("Runtime.evaluate", {
        "expression": expression, "awaitPromise": True,
        "returnByValue": True})
    return result.get("result", {}).get("value") or {}


def prompt_in_result(port: int, job: dict) -> str:
    """The prompt the mock actually rendered, read out of the image.

    `mock_backend.py` draws `positive_prompt[:72]` into the SVG, so this is the
    prompt inference received -- not a metadata echo of what was asked for.
    """

    result = (job or {}).get("result") or {}
    handle = result.get("image_handle") or result.get("handle") or ""
    data_url = result.get("image_data_url") or ""
    if not data_url and handle:
        status, body = request(port, "GET", f"/studio/file?path={handle}")
        if status == 200 and isinstance(body, str):
            data_url = body
    if data_url.startswith("data:"):
        payload = data_url.split(",", 1)[1]
        return base64.b64decode(payload).decode("utf-8", "replace")
    return json.dumps(result)[:400]


async def screenshot(cdp: Any, name: str) -> None:
    """Capture the TOGGLE, not the page.

    The first version captured the viewport. In process A that happened to show
    the control and the before/after shots differed; in process B the toggle was
    outside the captured area, so `03-restored-off` and `04-enabled` came out
    BYTE-IDENTICAL -- two files with contradictory names and the same contents,
    filed as evidence of a state change.

    The DOM assertions were always the real proof. These are supplementary, and
    supplementary evidence that cannot show the thing it is named after is worse
    than none: it invites a reader to believe a picture that says nothing.
    Scrolled into view and clipped to the element, so the file either shows the
    control or the run fails on the distinctness check below.
    """

    # The toggle lives on the EXTENSIONS page, which is not the one Studio
    # opens on. Without this the element exists (so the class assertions are
    # still valid) but is never rendered, every clip captures the same hidden
    # region, and four contradictory filenames end up byte-identical.
    #
    # Clicked through the real `.panel-tab` the app itself listens on
    # (app.js:4248), not by forcing `.active` onto the page -- a screenshot of a
    # state the UI cannot actually reach is not evidence of anything.
    # ...and inside a COLLAPSED `.ext-group` once that page is up, so it still
    # measures zero and the clip still silently falls back to the whole
    # viewport. Opened the way the app opens them (app.js:8747).
    await cdp.evaluate(
        "(() => { const tab = document.querySelector("
        "'#panelTabs .panel-tab[data-panel=\"extensions\"]');"
        " if (tab) tab.click();"
        " document.querySelectorAll('#page-extensions .ext-group:not(.open)')"
        "   .forEach(g => g.classList.add('open'));"
        " document.querySelectorAll('#page-extensions .ext-group-body')"
        "   .forEach(b => { b.classList.add('open');"
        "                   b.style.maxHeight = 'none'; });"
        " return !!tab; })()")
    await cdp.drain(0.8)
    box = await cdp.evaluate(
        "(() => { const t ="
        " document.getElementById('toggleStudioDynPrompts');"
        " if (!t) return null; t.scrollIntoView({block:'center'});"
        " const r = t.getBoundingClientRect();"
        " if (!r.width || !r.height) return null;"
        " return {x:Math.max(0,r.x-16), y:Math.max(0,r.y-16),"
        "         width:r.width+32, height:r.height+32};"
        " })()")
    await cdp.drain(0.4)
    options: dict[str, Any] = {"format": "png"}
    if isinstance(box, dict) and box.get("width"):
        options["clip"] = {**box, "scale": 2}
    shot = await cdp.call("Page.captureScreenshot", options)
    data = shot.get("data")
    if data:
        (RUN_ROOT / name).write_bytes(base64.b64decode(data))


def check_rendered_results_discriminate() -> None:
    """The two GENERATED images must differ, and they are the real evidence.

    Three attempts went into photographing the toggle: it lives on the
    Extensions page, inside a collapsed group, and every clip fell back to the
    whole viewport -- producing four byte-identical files with four
    contradictory names. A picture of a switch was never the point.

    The mock draws `positive_prompt[:72]` into the result, so the image the
    owner would look at literally spells out which prompt reached inference.
    That artefact discriminates, it is what the row is about, and it fails
    loudly if the two runs ever agree.

    The viewport screenshots are kept as CONTEXT and are named as context. They
    are not offered as proof of the toggle's state; the DOM assertions are.
    """

    import hashlib

    off = RUN_ROOT / "05-generated-with-choice-off.svg"
    on = RUN_ROOT / "06-generated-with-choice-on.svg"
    if not (off.is_file() and on.is_file()):
        check("both generated results were preserved", True, False)
        return
    check("the two generated results are different images", True,
          hashlib.sha256(off.read_bytes()).hexdigest()
          != hashlib.sha256(on.read_bytes()).hexdigest())
    check("the OFF result renders the unexpanded token",
          True, PROMPT in off.read_text(encoding="utf-8", errors="replace"))
    check("the ON result renders the resolved prompt",
          True, RESOLVED in on.read_text(encoding="utf-8", errors="replace"))


async def run(browser: Path) -> int:
    if RUN_ROOT.exists():
        for attempt in range(5):
            try:
                shutil.rmtree(RUN_ROOT)
                break
            except PermissionError:
                # Something from a previous run still holds it. Say so rather
                # than writing this run's evidence on top of the last one's.
                if attempt == 4:
                    raise LiveFailure(
                        f"{RUN_ROOT} is locked by another process -- reap "
                        f"stray chrome.exe/python.exe before re-running")
                time.sleep(2)
    RUN_ROOT.mkdir(parents=True)
    WILDCARD_ROOT.mkdir(parents=True)
    (WILDCARD_ROOT / WILDCARD_FILE).write_text(WILDCARD_LINES, encoding="utf-8")
    CONFIG_PATH.write_text(json.dumps({
        "backend": "mock",
        "host": "127.0.0.1",
        "port": 0,
        "onboarding": "suppressed",
        "result_root": str(RESULT_ROOT),
        "model_roots": {},
        "studio_state_root": str(STATE_ROOT),
    }, indent=2), encoding="utf-8")

    identities: dict[str, Any] = {}

    # -- process A: choose the folder, then turn the toggle OFF in the UI ----
    with Studio("process A -- toggle in the browser") as studio:
        identities["process_a_pid"] = studio.pid
        status, body = request(
            studio.port, "POST", "/studio/dynamic_prompts/select_folder",
            {"folder": str(WILDCARD_ROOT)})
        check("the fixture wildcard folder is accepted", 200, status)
        check("the folder is the fixture, not the owner's",
              True, str(WILDCARD_ROOT) == body.get("wildcard_folder"))
        check("a default-on service starts enabled",
              True, body.get("studio_dynamic_prompts_enabled"))

        chrome, socket, cdp = await open_browser(browser, f"http://127.0.0.1:{studio.port}")
        try:
            check("the UI renders the toggle ON", True, await toggle_state(cdp))
            await screenshot(cdp, "ui-01-process-a-before.png")

            await click_toggle(cdp)
            check("the UI renders the toggle OFF after the click",
                  False, await toggle_state(cdp))
            await screenshot(cdp, "ui-02-process-a-after.png")

            # The write's truthful success is a NETWORK fact. app.js swallows
            # both outcomes silently, so a toast would prove nothing.
            written = await cdp.evaluate(
                "(async () => { const r = await"
                " fetch('/studio/dynamic_prompts/config');"
                " return {status: r.status, body: await r.json()}; })()")
            check("the API reports the choice while A is still running",
                  False, (written or {}).get("body", {})
                  .get("studio_dynamic_prompts_enabled"))
        finally:
            await socket.close()
            reap(chrome)

    # -- process B: a DIFFERENT process, same state root --------------------
    with Studio("process B -- after restart") as studio:
        identities["process_b_pid"] = studio.pid
        check("process B is genuinely a different process",
              True, identities["process_a_pid"] != identities["process_b_pid"])

        status, body = request(studio.port, "GET",
                               "/studio/dynamic_prompts/config")
        check("the API restores the stored choice after restart",
              (200, False),
              (status, body.get("studio_dynamic_prompts_enabled")))
        check("the folder survived the restart too",
              str(WILDCARD_ROOT), body.get("wildcard_folder"))

        chrome, socket, cdp = await open_browser(browser, f"http://127.0.0.1:{studio.port}")
        try:
            check("the UI restores the toggle OFF", False, await toggle_state(cdp))
            await screenshot(cdp, "ui-03-process-b-restored.png")

            disabled = await generate_in_page(cdp, PROMPT)
            rendered = prompt_in_result(studio.port, disabled.get("job") or {})
            (RUN_ROOT / "05-generated-with-choice-off.svg").write_text(
                rendered, encoding="utf-8")
            check("with the choice OFF the token reaches inference unexpanded",
                  True, PROMPT in rendered)
            check("and it is NOT expanded", False, RESOLVED in rendered)

            await click_toggle(cdp)
            check("the UI turns the toggle back ON", True, await toggle_state(cdp))
            await screenshot(cdp, "ui-04-process-b-re-enabled.png")

            enabled = await generate_in_page(cdp, PROMPT)
            rendered_on = prompt_in_result(studio.port, enabled.get("job") or {})
            (RUN_ROOT / "06-generated-with-choice-on.svg").write_text(
                rendered_on, encoding="utf-8")
            check("with the choice ON the token is expanded before inference",
                  True, RESOLVED in rendered_on)
            check("and the raw token is gone", False, PROMPT in rendered_on)
        finally:
            await socket.close()
            reap(chrome)

    check_rendered_results_discriminate()

    home = str(Path.home())
    reported = TRANSCRIPT.parent
    leaked = sorted(
        path.name for path in reported.iterdir()
        if path.is_file() and path.suffix in (".json", ".txt", ".svg")
        and path.name != "studio-config.mock.json"
        and home in path.read_text(encoding="utf-8", errors="replace"))
    check("no reported artefact carries the owner's home directory",
          [], leaked)

    failures = [row for row in CHECKS if not row["ok"]]
    verdict = "GA-BROWSER-01 PASSED" if not failures else "GA-BROWSER-01 FAILED"
    TRANSCRIPT.write_text(json.dumps({
        "row": "GA-BROWSER-01",
        "verdict": verdict,
        "identities": identities,
        "state_root": str(STATE_ROOT.relative_to(WORKSPACE_ROOT)),
        "wildcard_root": str(WILDCARD_ROOT.relative_to(WORKSPACE_ROOT)),
        "checks": CHECKS,
        "non_claims": [
            "The generation is issued from page context against the real "
            "/api/generate route, NOT by clicking Generate. On a mock host the "
            "button submits an empty model id and is refused before expansion. "
            "This is not WP1's Generate-button acceptance.",
            "Mock backend only. No model was loaded and no GPU was used.",
            "Proves persistence and admission-time expansion, not inference "
            "quality.",
        ],
    }, indent=2), encoding="utf-8")
    REPORT.write_text(
        f"{verdict}\n\n" + "\n".join(
            f"[{'ok' if row['ok'] else 'FAIL'}] {row['check']}"
            for row in CHECKS) + "\n", encoding="utf-8")
    print(f"\n{verdict} -- {len(CHECKS) - len(failures)}/{len(CHECKS)} checks")
    print(f"evidence: {RUN_ROOT.relative_to(WORKSPACE_ROOT)}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", required=True)
    args = parser.parse_args()
    browser = Path(args.browser_executable)
    if not browser.is_file():
        print(f"browser not found: {browser}", file=sys.stderr)
        return 2
    return asyncio.run(run(browser))


if __name__ == "__main__":
    sys.exit(main())
