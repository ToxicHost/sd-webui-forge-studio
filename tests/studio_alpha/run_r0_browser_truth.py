"""Executing-browser truth harness for R0 baseline recovery.

Runs the canonical frontend against the owned mock/CPU presentation server and
drives two real DOM journeys over Chrome DevTools Protocol:

* Gallery first-run registration refusal cannot close with a success claim.
* Workflow DELETE refusal cannot close with a success claim.

The browser executable is an explicit owner input. It is never discovered,
downloaded, or persisted in evidence. The disposable profile lives under the
requested Evidence directory. Chrome is launched with GPU and background
networking disabled. No Forge runtime, model, CUDA path, or generation runs.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time
import urllib.request
from urllib.parse import urlsplit
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_ROOT.parent
DEFAULT_EVIDENCE = WORKSPACE_ROOT / "Evidence" / "r0-baseline-recovery"
HOST = "127.0.0.1"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio import GenerationRequest, MockBackend, StudioApplication  # noqa: E402
from forge_studio.presentation import (  # noqa: E402
    StudioPresentation,
    _StudioHTTPServer,
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=", *args],
        cwd=APP_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _repo_binding() -> dict[str, Any]:
    status = _git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    diff = subprocess.run(
        ["git", "-c", "core.excludesFile=", "diff", "--binary", "--", "."],
        cwd=APP_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    return {
        "head": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "status": status,
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "diff_is_empty": not diff,
    }


class _Server:
    def __init__(self) -> None:
        self.application = StudioApplication(MockBackend())
        self.presentation = StudioPresentation(self.application, GenerationRequest)
        self.server = _StudioHTTPServer((HOST, 0), self.presentation)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://{HOST}:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutdown = getattr(self.application, "shutdown", None)
        if callable(shutdown):
            shutdown()


class CDP:
    def __init__(self, socket: Any) -> None:
        self.socket = socket
        self.next_id = 1
        self.events: list[dict[str, Any]] = []

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        call_id = self.next_id
        self.next_id += 1
        await self.socket.send(
            json.dumps({"id": call_id, "method": method, "params": params or {}})
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(
                self.socket.recv(), timeout=max(0.1, deadline - time.monotonic())
            )
            message = json.loads(raw)
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)
        raise TimeoutError(method)

    async def drain(self, seconds: float = 0.5) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self.socket.recv(), timeout=0.1)
                self.events.append(json.loads(raw))
            except asyncio.TimeoutError:
                pass

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        detail = result.get("result", {})
        if detail.get("subtype") == "error":
            raise RuntimeError(str(detail.get("description", "browser evaluation failed")))
        return detail.get("value")


async def _wait_for(cdp: CDP, expression: str, *, timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await cdp.evaluate(expression)
        if last:
            return last
        await asyncio.sleep(0.15)
    raise TimeoutError(f"browser condition did not become true: {expression}; last={last!r}")


async def _capture(cdp: CDP, path: Path) -> None:
    shot = await cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=30)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(shot["data"]))


def _safe_text(value: Any, base: str) -> str:
    text = str(value or "").replace(base, "<loopback>")
    text = re.sub(r"(?i)[a-z]:[\\/]users[\\/][^\\/\s]+", "<private-root>", text)
    return text[:500]


async def _network_records(cdp: CDP, base: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    finished: set[str] = set()
    for event in list(cdp.events):
        method = event.get("method")
        params = event.get("params", {})
        request_id = str(params.get("requestId", ""))
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = str(request.get("url", ""))
            parsed = urlsplit(url)
            by_id[request_id] = {
                "method": str(request.get("method", "GET")),
                "path": parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                "origin": (
                    "loopback"
                    if url.startswith(base + "/")
                    else "internal"
                    if parsed.scheme in {"data", "about", "chrome", "blob"}
                    else "external"
                ),
            }
        elif method == "Network.responseReceived" and request_id in by_id:
            response = params.get("response", {})
            by_id[request_id]["status"] = int(response.get("status", 0))
        elif method == "Network.loadingFinished":
            finished.add(request_id)

    target_prefixes = ("/studio/gallery/", "/studio/workflows")
    records: list[dict[str, Any]] = []
    for request_id, record in by_id.items():
        if not str(record.get("path", "")).startswith(target_prefixes):
            continue
        body: Any = None
        if request_id in finished:
            try:
                response = await cdp.call(
                    "Network.getResponseBody", {"requestId": request_id}, timeout=5
                )
                raw = response.get("body", "")
                try:
                    body = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    body = _safe_text(raw, base)
            except (RuntimeError, TimeoutError):
                body = "<unavailable>"
        records.append({**record, "response": body})
    return records


def _console_and_external(
    events: list[dict[str, Any]], base: str
) -> tuple[list[str], list[str], list[str]]:
    console: list[str] = []
    csp: list[str] = []
    external: list[str] = []
    for event in events:
        method = event.get("method", "")
        params = event.get("params", {})
        if method == "Runtime.exceptionThrown":
            console.append(
                _safe_text(params.get("exceptionDetails", {}).get("text", ""), base)
            )
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args", [])
            console.append(_safe_text(args[0].get("value", "") if args else "", base))
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            text = _safe_text(entry.get("text", ""), base)
            if "content security policy" in text.casefold():
                csp.append(text)
            elif entry.get("level") == "error":
                console.append(text)
        elif method == "Network.requestWillBeSent":
            url = str(params.get("request", {}).get("url", ""))
            parsed = urlsplit(url)
            if not (
                url.startswith(base + "/")
                or parsed.scheme in {"data", "about", "chrome", "blob"}
            ):
                external.append(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    return sorted(set(console)), sorted(set(csp)), sorted(set(external))


async def _run(browser: Path, evidence: Path) -> int:
    import websockets

    evidence.mkdir(parents=True, exist_ok=True)
    gallery_dir = evidence / "browser-gallery-first-run"
    workflow_dir = evidence / "browser-workflow-delete"
    dynprompts_dir = evidence / "browser-dynamic-prompts"
    capability_dir = evidence / "browser-capability-gating"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    dynprompts_dir.mkdir(parents=True, exist_ok=True)
    capability_dir.mkdir(parents=True, exist_ok=True)

    binding = _repo_binding()
    server = _Server()
    chrome: subprocess.Popen[bytes] | None = None
    socket: Any = None
    result: dict[str, Any] = {
        "classification": "VERIFIED-EVIDENCE",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "repository": binding,
        "command": (
            "venv Python run_r0_browser_truth.py "
            "--browser-executable <owner-approved> --evidence-root <workspace-evidence>"
        ),
        "runtime": {
            "python": sys.version.split()[0],
            "driver": f"Chrome headless CDP + websockets {websockets.__version__}",
            "server": "owned mock/CPU presentation server",
            "gpu": "disabled; not used",
            "network": "loopback only; Chrome background networking disabled",
        },
        "gallery_first_run": {},
        "workflow_delete": {},
        "requests": [],
        "console_errors": [],
        "csp_violations": [],
        "external_requests": [],
        "checks": {},
        "fatal": None,
    }

    try:
        server.start()
        # `ignore_cleanup_errors` because this context manager exits BEFORE the
        # `finally` below terminates Chrome, so its own cleanup necessarily runs
        # against a live browser holding handles inside the profile. On Windows
        # that raises WinError 32, which propagated out and recorded the run as
        # `fatal` -- a teardown detail presented as a failed truth journey. The
        # real delete is the bounded retry after Chrome exits; this call is now
        # only a fallback for the paths that never got that far.
        with tempfile.TemporaryDirectory(
            prefix=".chrome-profile-", dir=evidence, ignore_cleanup_errors=True
        ) as profile_name:
            profile = Path(profile_name)
            chrome = subprocess.Popen(
                [
                    str(browser),
                    "--headless=new",
                    "--remote-debugging-port=0",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--disable-default-apps",
                    "--disable-gpu",
                    "--window-size=1400,900",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not port_file.exists():
                if chrome.poll() is not None:
                    raise RuntimeError("owner-approved browser exited before CDP was ready")
                await asyncio.sleep(0.1)
            if not port_file.exists():
                raise TimeoutError("DevToolsActivePort was not created")
            devtools_port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
            targets: list[dict[str, Any]] | None = None
            for _ in range(100):
                try:
                    with urllib.request.urlopen(
                        f"http://{HOST}:{devtools_port}/json/list", timeout=3
                    ) as response:
                        targets = json.loads(response.read().decode("utf-8"))
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            if not targets:
                raise RuntimeError("Chrome DevTools endpoint did not answer")
            page = next(target for target in targets if target.get("type") == "page")
            socket = await websockets.connect(
                page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024
            )
            cdp = CDP(socket)
            for domain in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
                await cdp.call(domain)

            await cdp.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
(() => {
  const nativeFetch = window.fetch.bind(window);
  window.__r0SyntheticCalls = [];
  window.fetch = async function(input, init) {
    const raw = typeof input === "string" ? input : input.url;
    const url = new URL(raw, window.location.href);
    const method = String(
      (init && init.method) || (input && input.method) || "GET"
    ).toUpperCase();
    if (method === "GET" && url.pathname === "/studio/workflows") {
      window.__r0SyntheticCalls.push({method, path: url.pathname, status: 200});
      return new Response(
        JSON.stringify([{id: "r0-fixture", name: "R0 Fixture", family: "any"}]),
        {status: 200, headers: {"Content-Type": "application/json"}}
      );
    }
    if (method === "POST" && url.pathname === "/studio/trust-save-root") {
      window.__r0SyntheticCalls.push({method, path: url.pathname, status: 200});
      return new Response(
        JSON.stringify({ok: true}),
        {status: 200, headers: {"Content-Type": "application/json"}}
      );
    }
    return nativeFetch(input, init);
  };
})();
"""
                },
            )
            await cdp.call(
                "Page.navigate", {"url": f"{server.base}/studio/?onboarding=off"}
            )
            await _wait_for(
                cdp,
                "document.readyState === 'complete' && "
                "typeof window.showToast === 'function' && "
                "typeof window._studioSaveDefaults === 'function'",
                timeout=20,
            )
            await _wait_for(
                cdp,
                """!!document.querySelector('#workflowSelect option[value=\"r0-fixture\"]')""",
                timeout=20,
            )
            await cdp.evaluate(
                """
(() => {
  window.__r0Toasts = [];
  const nativeToast = window.showToast;
  window.showToast = function(message, kind) {
    window.__r0Toasts.push({message: String(message), kind: String(kind || "info")});
    return nativeToast.apply(this, arguments);
  };
  return true;
})()
"""
            )

            gallery = await cdp.evaluate(
                """
(async () => {
  window.__r0Toasts = [];
  _showFirstRunModal();
  await new Promise(resolve => setTimeout(resolve, 100));
  const overlay = document.getElementById("firstRunOverlay");
  overlay.querySelector('[data-fr="saveloc"] [data-val="custom"]').click();
  overlay.querySelector('[data-fr="monitor"] [data-val="on"]').click();
  const path = overlay.querySelector("#firstRunPath");
  path.value = "C:/R0-Fixture";
  path.dispatchEvent(new Event("input", {bubbles: true}));
  overlay.querySelector("#firstRunGo").click();
  await new Promise(resolve => setTimeout(resolve, 1200));
  return {
    overlay_present: !!document.getElementById("firstRunOverlay"),
    onboarded: localStorage.getItem("studio-onboarded"),
    toasts: window.__r0Toasts.slice(),
    synthetic_calls: window.__r0SyntheticCalls.slice(),
  };
})()
"""
            )
            await _capture(cdp, gallery_dir / "gallery-refusal.png")

            workflow = await cdp.evaluate(
                """
(async () => {
  window.__r0Toasts = [];
  window.confirm = () => true;
  const select = document.getElementById("workflowSelect");
  select.value = "r0-fixture";
  select.dispatchEvent(new Event("change", {bubbles: true}));
  document.getElementById("workflowDeleteBtn").click();
  await new Promise(resolve => setTimeout(resolve, 1000));
  return {
    selected: select.value,
    fixture_still_present: !!select.querySelector('option[value="r0-fixture"]'),
    toasts: window.__r0Toasts.slice(),
    synthetic_calls: window.__r0SyntheticCalls.slice(),
  };
})()
"""
            )
            await _capture(cdp, workflow_dir / "workflow-delete-refusal.png")

            # R1 Batch A. `index.html:1537` ships the toggle hardcoded
            # `class="toggle-track on"`, and `app.js:5818` only clears it when
            # the config body carries `studio_dynamic_prompts_enabled`. The
            # adapter used to send `enabled`, so the names never met and the
            # owner saw wildcard expansion permanently ON for a service that
            # does not exist.
            #
            # `_loadDynPromptsConfig()` runs at boot (app.js:5906), so the
            # settled DOM is the whole assertion -- no clicking required. The
            # dependent buttons are the second half: they carry
            # `data-setting-depends="toggleStudioDynPrompts"` and are disabled
            # by settings-page.js's MutationObserver when the class clears. If
            # only the toggle is asserted, a future refactor could drop the
            # dependency wiring and silently re-expose two clickable liars.
            dynprompts = await cdp.evaluate(
                """
(() => {
  const toggle = document.getElementById("toggleStudioDynPrompts");
  const browse = document.getElementById("dynPromptsFolderBrowse");
  const reset = document.getElementById("dynPromptsFolderReset");
  const tab = document.querySelector('button[data-module="lexicon"]');
  return {
    toggle_present: !!toggle,
    toggle_on: !!toggle && toggle.classList.contains("on"),
    browse_disabled: !!browse && browse.disabled === true,
    reset_disabled: !!reset && reset.disabled === true,
    // The Wildcards tab. Gated shut while the service was absent; the gate
    // is the module system's own probe on GET /studio/lexicon/tree, so a
    // visible tab IS the statement that the backend answered.
    tab_present: !!tab,
    tab_hidden: !!tab && !!tab.hidden,
    // Controls with no backend. Each was live behind the gate, and serving
    // /tree re-arms anything left behind -- Export used to save a 404 JSON
    // body to disk under a .zip name.
    dead_controls: ["export", "import"].filter(
      (a) => !!document.querySelector(`[data-action="${a}"]`)
    ).concat(
      document.querySelector(".lex-search-toggle") ? ["content-search"] : [],
      document.querySelector(".lex-preview-roll") ? ["roll"] : []
    ),
  };
})()
"""
            )
            await _capture(cdp, dynprompts_dir / "wildcards-available.png")

            # R1 Batch E. Two things at once, and the second is the one that
            # matters: the absent-service tabs are hidden, AND the Canvas
            # lightbox that lives in gallery.js still opens.
            #
            # Gating Gallery is the only capability gate in this product that
            # can break something that works. `window.StudioGallery` is
            # exported before registration and reached from Canvas
            # (app.js:4930 `_openCanvasOutput`), so hiding the tab must leave it
            # untouched. Asserting the tab is gone without asserting the
            # lightbox still opens would pass while having broken the one path
            # the gate was required to preserve.
            capability = await cdp.evaluate(
                """
(async () => {
  const tab = (id) => document.querySelector(`button[data-module="${id}"]`);
  const seen = {};
  for (const id of ["gallery", "workshop", "lexicon"]) {
    const b = tab(id);
    seen[id] = b ? {present: true, hidden: !!b.hidden, capability: b.dataset.capability || null}
                 : {present: false};
  }
  // A 1x1 PNG is enough: openEphemeral refuses anything without b64Url, and
  // the overlay renders whatever it is given.
  const b64Url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
  const shared_export = typeof (window.StudioGallery || {}).openEphemeral === "function";
  let opened = false, overlay_src = null;
  if (shared_export) {
    opened = window.StudioGallery.openEphemeral({b64Url, name: "r0-fixture.png"}, null);
    await new Promise(r => setTimeout(r, 300));
    const img = document.getElementById("gal-detail-img");
    overlay_src = img ? img.getAttribute("src") : null;
  }
  const liveBtn = document.querySelector("#liveToggleBtn");
  const genBtn = document.querySelector("#genBtn");
  const livePanel = document.querySelector("#livePanel");
  return {
    tabs: seen, shared_export, opened, overlay_matches: overlay_src === b64Url,
    live_hidden: !!liveBtn && liveBtn.hidden === true,
    live_capability: liveBtn ? (liveBtn.dataset.capability || null) : null,
    live_panel_hidden: !livePanel || livePanel.style.display === "none",
    generate_enabled: !!genBtn && genBtn.disabled === false,
    cn_hidden: (() => { const e = document.querySelector('[data-block="controlnet"]'); return !e || e.hidden === true; })(),
    hr_ckpt_hidden: (() => { const e = document.getElementById("paramHrCheckpoint"); return !e || !!e.closest("[hidden]"); })(),
    vram_hidden: (() => { const e = document.getElementById("vramReserveSlider"); return !e || !!e.closest("[hidden]"); })(),
    autounload_hidden: (() => { const e = document.getElementById("toggleAutoUnload"); return !e || !!e.closest("[hidden]"); })(),
    autounload_minutes_hidden: (() => { const e = document.getElementById("autoUnloadMinutesRow"); return !e || e.style.display === "none" || !!e.closest("[hidden]"); })(),
  };
})()
"""
            )
            await _capture(cdp, capability_dir / "tabs-gated-lightbox-intact.png")
            await cdp.drain(1.0)

            requests = await _network_records(cdp, server.base)
            console, csp, external = _console_and_external(cdp.events, server.base)
            result["gallery_first_run"] = gallery
            result["workflow_delete"] = workflow
            result["dynamic_prompts"] = dynprompts
            result["capability_gating"] = capability
            result["requests"] = requests
            result["console_errors"] = console
            result["csp_violations"] = csp
            result["external_requests"] = external

            gallery_requests = [
                item for item in requests
                if str(item.get("path", "")).startswith("/studio/gallery/")
            ]
            delete_requests = [
                item for item in requests
                if item.get("method") == "DELETE"
                and str(item.get("path", "")).startswith("/studio/workflows/")
            ]
            gallery_toasts = gallery.get("toasts", []) if isinstance(gallery, dict) else []
            workflow_toasts = (
                workflow.get("toasts", []) if isinstance(workflow, dict) else []
            )
            synthetic = (
                workflow.get("synthetic_calls", [])
                if isinstance(workflow, dict)
                else []
            )
            dyn = dynprompts if isinstance(dynprompts, dict) else {}
            cap = capability if isinstance(capability, dict) else {}
            cap_tabs = cap.get("tabs") or {}
            result["checks"] = {
                # R1 Batch E.
                "absent_service_tabs_are_hidden": all(
                    (not entry.get("present"))
                    or (entry.get("hidden") and entry.get("capability") == "absent")
                    for entry in cap_tabs.values()
                ),
                # The preservation half. Non-negotiable: this is the path the
                # gate was required not to break.
                # Live: hidden, its panel closed, and -- the one that would
                # actually stop an owner working -- Generate still enabled.
                "live_entry_point_is_hidden": bool(
                    cap.get("live_hidden") and cap.get("live_capability") == "absent"
                ),
                "live_does_not_disable_generate": bool(
                    cap.get("generate_enabled") and cap.get("live_panel_hidden")
                ),
                "vram_and_autounload_hidden": bool(
                    cap.get("vram_hidden")
                    and cap.get("autounload_hidden")
                    and cap.get("autounload_minutes_hidden")
                ),
                "controlnet_and_hires_override_hidden": bool(
                    cap.get("cn_hidden") and cap.get("hr_ckpt_hidden")
                ),
                "canvas_lightbox_survives_gating": bool(
                    cap.get("shared_export")
                    and cap.get("opened")
                    and cap.get("overlay_matches")
                ),
                # Gate 1. These asserted the PRE-FEATURE state -- toggle off,
                # Browse and Reset disabled, Wildcards tab hidden -- which was
                # correct while there was no service and became a falsehood the
                # moment one existed. They were green on a host with no
                # wildcard folder, which is not the same as being right.
                #
                # What replaces them is the capability itself: the folder
                # control is reachable, and the tab the module gate opens on a
                # successful GET /studio/lexicon/tree is visible.
                "dynprompts_folder_control_is_reachable": bool(
                    dyn.get("toggle_present") and not dyn.get("browse_disabled")
                ),
                "wildcards_tab_is_available": bool(
                    dyn.get("tab_present") and not dyn.get("tab_hidden")
                ),
                # A rendered control with no route behind it is a failure, not
                # a cosmetic issue: that is the incident lexicon.js documents.
                "no_unbacked_wildcard_controls": dyn.get("dead_controls") == [],
                "gallery_registration_refused_404": any(
                    item.get("method") == "POST"
                    and item.get("path") == "/studio/gallery/scan-folders"
                    and item.get("status") == 404
                    for item in gallery_requests
                ),
                "gallery_did_not_issue_followup_scan": not any(
                    item.get("path") == "/studio/gallery/scan"
                    for item in gallery_requests
                ),
                "gallery_card_stayed_open": bool(
                    isinstance(gallery, dict) and gallery.get("overlay_present")
                ),
                "gallery_not_marked_onboarded": bool(
                    isinstance(gallery, dict) and gallery.get("onboarded") is None
                ),
                "gallery_error_visible": any(
                    toast.get("kind") == "error"
                    and "gallery monitoring" in str(toast.get("message", "")).casefold()
                    for toast in gallery_toasts
                    if isinstance(toast, dict)
                ),
                # The claim under test is the FIRST-RUN COMPLETION claim, and any
                # success claim about Gallery itself. Not "no success toast at
                # all": reaching the Gallery block requires the save-folder trust
                # step to SUCCEED (`if (useCustom && !(await
                # _trustSaveFolder(...))) return;` guards it), and that step
                # truthfully toasts "Folder trusted". Asserting on toast KIND
                # alone therefore fails against a correct product -- it is
                # unsatisfiable by construction, because the journey cannot reach
                # the code under test without producing a success toast first.
                "gallery_no_success_claim": not any(
                    "welcome aboard" in str(toast.get("message", "")).casefold()
                    or (
                        toast.get("kind") == "success"
                        and "gallery" in str(toast.get("message", "")).casefold()
                    )
                    for toast in gallery_toasts
                    if isinstance(toast, dict)
                ),
                "workflow_delete_refused_404": any(
                    item.get("status") == 404 for item in delete_requests
                ),
                "workflow_error_visible": any(
                    toast.get("kind") == "error"
                    and "delete failed" in str(toast.get("message", "")).casefold()
                    for toast in workflow_toasts
                    if isinstance(toast, dict)
                ),
                "workflow_no_success_claim": not any(
                    toast.get("kind") == "success"
                    or "workflow deleted" in str(toast.get("message", "")).casefold()
                    for toast in workflow_toasts
                    if isinstance(toast, dict)
                ),
                "workflow_fixture_remained": bool(
                    isinstance(workflow, dict)
                    and workflow.get("fixture_still_present")
                    and workflow.get("selected") == "r0-fixture"
                ),
                "workflow_no_success_refresh": sum(
                    1
                    for item in synthetic
                    if isinstance(item, dict)
                    and item.get("method") == "GET"
                    and item.get("path") == "/studio/workflows"
                ) == 1,
                "no_csp_violation": not csp,
                "no_external_request": not external,
            }

            common = {
                "classification": result["classification"],
                "captured_utc": result["captured_utc"],
                "repository": binding,
                "runtime": result["runtime"],
            }
            (gallery_dir / "result.json").write_text(
                json.dumps(
                    {
                        **common,
                        "journey": result["gallery_first_run"],
                        "requests": gallery_requests,
                        "checks": {
                            key: value
                            for key, value in result["checks"].items()
                            if key.startswith("gallery_")
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            (workflow_dir / "result.json").write_text(
                json.dumps(
                    {
                        **common,
                        "journey": result["workflow_delete"],
                        "requests": delete_requests,
                        "checks": {
                            key: value
                            for key, value in result["checks"].items()
                            if key.startswith("workflow_")
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

    except BaseException as error:  # noqa: BLE001
        result["fatal"] = {
            "kind": type(error).__name__,
            "message": _safe_text(error, server.base),
        }
    finally:
        if socket is not None:
            try:
                await socket.close()
            except BaseException:  # noqa: BLE001
                pass
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome.kill()
                chrome.wait(timeout=5)
            # `chrome.wait()` returning does NOT mean the profile is free. The
            # parent exits first; its renderer, GPU and network-service children
            # keep handles inside the profile directory open for a moment
            # longer, and on Windows a delete against an open handle raises
            # WinError 32 rather than waiting. `TemporaryDirectory` cleanup then
            # raised through the context manager and the whole run recorded
            # `fatal`, reporting a browser-teardown detail as if a truth journey
            # had failed. Give the children a bounded moment to release.
            for attempt in range(10):
                try:
                    shutil.rmtree(profile)
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.2 * (attempt + 1))
                else:
                    break
        server.close()

    output = evidence / "browser-truth.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    passed = result["fatal"] is None and all(result["checks"].values())
    print("R0_BROWSER_TRUTH_PASS" if passed else "R0_BROWSER_TRUTH_FAIL")
    print(json.dumps(result["checks"], sort_keys=True))
    print(f"evidence={output.relative_to(WORKSPACE_ROOT).as_posix()}")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-executable", required=True, type=Path)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    browser = args.browser_executable
    if not browser.is_file():
        parser.error("the explicitly approved browser executable is not a file")
    evidence = args.evidence_root.resolve()
    allowed = (WORKSPACE_ROOT / "Evidence").resolve()
    if not evidence.is_relative_to(allowed):
        parser.error("evidence-root must remain under workspace Evidence")
    return asyncio.run(_run(browser, evidence))


if __name__ == "__main__":
    raise SystemExit(main())
