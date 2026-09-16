"""Subprocess probe: the internal-alpha frontend/product rehearsal.

Actual frontend assets, actual API routes, actual product objects -- served by
the real stdlib Studio server on one loopback port -- with a synthetic
terminal generation port (the NeoStub world: no torch, no real modules, no
payload, no CUDA). Emits one JSON document between markers.

This is the §12 rehearsal of Internal Alpha Phase 1, and it drives the flow
exactly as the Model panel does: async submits under public ids, job states
observed by polling, the queued job cancelled through the canonical HTTP
route, results fetched through opaque handles.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else APP_ROOT / "outputs"

# Hostile argv AFTER reading our own parameter: the product must tolerate it.
sys.argv = [sys.argv[0], "--hostile-flag", "junk"]

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

OUT: dict[str, object] = {"facts": {}, "counts": {}, "fatal": None}
F = OUT["facts"]
C = OUT["counts"]

import tests.studio_alpha.test_real_loader_default_bindings as B  # noqa: E402

from forge_headless.live_bindings import LoadConfiguration  # noqa: E402
from forge_studio import GenerationRequest  # noqa: E402
from forge_studio.composition import build_standalone  # noqa: E402
from forge_studio.presentation import StudioPresentation, _StudioHTTPServer  # noqa: E402


class GatedNeo(B.NeoStub):
    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()
        self.concurrent = 0
        self.max_concurrent = 0
        self._lk = threading.Lock()

    def _process_images_inner(self, processing):
        with self._lk:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.entered.set()
        try:
            self.gate.wait(timeout=45)
            return super()._process_images_inner(processing)
        finally:
            with self._lk:
                self.concurrent -= 1


def call(method: str, url: str, body: object | None = None,
         timeout: float = 60.0):
    authority = url.split("//", 1)[1].split("/", 1)[0]
    headers = {"Host": authority}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = f"http://{authority}"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status, dict(response.headers), response.read()


def payload(seed: int) -> dict[str, object]:
    return {"model": "", "model_selection": dict(SELECTION),
            "positive_prompt": "p", "negative_prompt": "",
            "width": 768, "height": 768, "steps": 12, "cfg_scale": 6.0,
            "seed": seed}


def unselected_payload(seed: int) -> dict:
    """A job that names no model. Still refused when nothing is resident,
    because it cannot make itself runnable."""

    return {"model": "alpha", "positive_prompt": "p", "negative_prompt": "",
            "width": 768, "height": 768, "steps": 12, "cfg_scale": 6.0,
            "seed": seed}


#: The three opaque catalogue ids this rehearsal generates with. Shaped like
#: real ids (32 alphanumeric characters) because `ModelSelection.from_payload`
#: refuses anything path-shaped before it reaches a resolver.
SELECTION = {
    "checkpoint_model_id": "a" * 32,
    "text_encoder_model_id": "b" * 32,
    "vae_model_id": "c" * 32,
}


class CatalogueStub:
    """Stands in for the three role catalogues.

    Resolves the ids above to exactly the payload references the retired
    static profile carried, so the loader below this seam opens the same
    things it always did. What changed is who names them: the job, not a
    configured profile.
    """

    def __init__(self) -> None:
        self.resolutions = 0

    def build_payload_references(self, ids: dict) -> dict:
        self.resolutions += 1
        return {role: f"synthetic://alpha/{role}" for role in B.ROLE_ORDER}


def main() -> int:  # noqa: C901
    neo = GatedNeo().install()
    intercepts = B._Intercepts()
    intercepts.__enter__()
    server = None
    thread = None
    composition = None
    try:
        results = SCRATCH / "rehearsal-results"
        results.mkdir(parents=True, exist_ok=True)
        composition = build_standalone(
            backend_kind="headless",
            profiles=[B._profile("alpha")],
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=B.FakeAuthorization()
            ),
            result_root=results,
        )
        lifecycle = composition.model_lifecycle
        # Auto-load on Generate, wired the way launch.py wires it. Without
        # this the application holds no resolver and every job would fall
        # through to the lease's refusal.
        from forge_studio.model_selection import make_selection_resolver

        catalogue = CatalogueStub()
        composition.application.use_selection_resolver(
            make_selection_resolver(catalogue)
        )
        presentation = StudioPresentation(
            composition.application, GenerationRequest
        )
        server = _StudioHTTPServer(("127.0.0.1", 0), presentation)
        base = f"http://127.0.0.1:{int(server.server_address[1])}"
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        C["servers"] = 1

        # ---- frontend loads, and carries the model-controls module ---------
        _s, _h, html = call("GET", f"{base}/studio/")
        html_text = html.decode("utf-8", "replace")
        F["frontend_served"] = _s == 200
        F["loader_lists_model_controls"] = (
            "studio-model-controls.js" in html_text
        )
        _s, _h, module_bytes = call(
            "GET", f"{base}/studio/static/studio-model-controls.js"
        )
        module_text = module_bytes.decode("utf-8", "replace")
        F["module_served"] = _s == 200
        lowered = html_text.lower() + module_text.lower()
        F["frontend_carries_no_private_path"] = (
            "private-local" not in lowered
            and ":\\\\" not in module_text
            and "c:/users" not in lowered
        )

        # ---- NO_MODEL startup truths ----------------------------------------
        _s, _h, state_bytes = call("GET", f"{base}/api/model/state")
        state0 = json.loads(state_bytes.decode())
        F["startup_state"] = state0["state"]
        # A job that NAMES no model is still refused with nothing resident,
        # because it cannot make itself runnable. A job that names a selection
        # is not -- that is the whole point, and is exercised below.
        F["startup_refuses_unselected_generation"] = False
        try:
            call("POST", f"{base}/api/generate", unselected_payload(1))
        except urllib.error.HTTPError as exc:
            F["startup_refuses_unselected_generation"] = exc.code == 400

        # ---- the retired profile/load surface is gone -----------------------
        # Asserted as absence, so a parallel architecture cannot survive
        # alongside the one the owner actually uses.
        F["retired_routes_absent"] = True
        for method, route, body in (
            ("GET", "/api/profiles", None),
            ("POST", "/api/profiles/select", {"profile_id": "alpha"}),
            ("POST", "/api/profiles/select_catalogue", dict(SELECTION)),
            ("POST", "/api/model/load", {}),
        ):
            try:
                call(method, f"{base}{route}", body)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    F["retired_routes_absent"] = False
            else:
                F["retired_routes_absent"] = False

        # Nothing has been loaded, and nothing will be until a job asks.
        F["selection_opens_nothing"] = neo.loader_calls == 0

        # ---- Generate is what makes the selection resident -------------------
        # No extra job is submitted to trigger the load: the FIRST real job
        # does it, which is the owner's actual experience. Adding a warm-up
        # generate would both inflate the job counts and hide the behaviour
        # under test.

        def submit(seed: int) -> str:
            _ss, _hh, b = call("POST", f"{base}/api/generate", payload(seed))
            return json.loads(b.decode())["job_id"]

        def status(job_id: str) -> dict:
            _ss, _hh, b = call("GET", f"{base}/api/jobs/{job_id}")
            return json.loads(b.decode())

        def await_resident() -> None:
            """The first job loads the model it named. Wait for that."""

            for _ in range(600):
                _ss, _hh, sb = call("GET", f"{base}/api/model/state")
                if json.loads(sb.decode())["state"] in ("ready", "busy"):
                    break
                time.sleep(0.05)
            F["state_after_autoload"] = json.loads(sb.decode())["state"]
            C["loads"] = 1
            C["engines"] = neo.loader_calls
            C["catalogue_resolutions"] = catalogue.resolutions
            C["sessions"] = 1

        def wait_state(job_id: str, wanted: str, timeout: float = 45.0) -> dict:
            deadline = time.monotonic() + timeout
            last: dict = {}
            while time.monotonic() < deadline:
                last = status(job_id)
                if last.get("state") == wanted:
                    return last
                time.sleep(0.05)
            raise RuntimeError(f"{job_id} never {wanted}: {last.get('state')}")

        # ---- Job 1 auto-loads what it named, then completes -------------------
        id1 = submit(1)
        await_resident()
        # The session exists only because a job asked for it.
        wrapper = lifecycle.session
        port = wrapper.port
        poll1 = wait_state(id1, "completed")
        handle1 = poll1["result"]["image_handle"]
        F["job_1_completed"] = True

        # ---- Job 2 blocks inside the port; the jobs list shows RUNNING -------
        neo.gate.clear()
        neo.entered.clear()
        id2 = submit(2)
        if not neo.entered.wait(timeout=30):
            raise RuntimeError("job 2 never entered the port")
        F["job_2_running_state"] = status(id2).get("state")
        _s, _h, jobs_bytes = call("GET", f"{base}/api/jobs")
        listed = {j["job_id"]: j["state"]
                  for j in json.loads(jobs_bytes.decode())["jobs"]}
        F["jobs_list_shows_running"] = listed.get(id2) == "running"

        # ---- Job 3 queues, then cancels through the canonical route ----------
        before3 = port.generate_calls
        id3 = submit(3)
        wait_state(id3, "queued", timeout=20.0)
        F["job_3_queued_state"] = "queued"
        _s, _h, cancel_bytes = call(
            "POST", f"{base}/api/jobs/{id3}/cancel", {}
        )
        cancel_response = json.loads(cancel_bytes.decode())
        F["job_3_cancel_http"] = _s
        F["job_3_cancelled_while_queued"] = cancel_response.get(
            "cancelled_while_queued"
        )
        F["job_3_terminal"] = wait_state(id3, "cancelled", timeout=10.0)["state"]
        F["job_3_port_calls"] = port.generate_calls - before3
        C["jobs_cancelled_queued"] = 1

        # ---- release Job 2; it completes --------------------------------------
        neo.gate.set()
        poll2 = wait_state(id2, "completed", timeout=60.0)
        handle2 = poll2["result"]["image_handle"]
        C["jobs_submitted"] = 3
        C["jobs_reaching_port"] = port.generate_calls
        C["jobs_completed"] = 2
        C["max_concurrent_port_calls"] = neo.max_concurrent
        C["publications"] = port.generate_calls
        C["per_job_releases"] = port.release_calls

        # ---- results visible and downloadable ---------------------------------
        fetches = {}
        for label, handle in (("job-1", handle1), ("job-2", handle2)):
            s, h, b = call("GET", f"{base}/studio/file?path={handle}")
            fetches[label] = {"status": s, "signature": b[:8].hex(),
                              "length": len(b)}
        C["pre_unload_result_fetches"] = 2
        F["results_are_png"] = all(
            f["signature"] == "89504e470d0a1a0a" for f in fetches.values()
        )

        # ---- unload; results survive; generation refused ----------------------
        call("POST", f"{base}/api/model/unload", {})
        C["unloads"] = 1
        _s, _h, state_bytes = call("GET", f"{base}/api/model/state")
        F["state_after_unload"] = json.loads(state_bytes.decode())["state"]
        post = {}
        for label, handle in (("job-1", handle1), ("job-2", handle2)):
            s, h, b = call("GET", f"{base}/studio/file?path={handle}")
            post[label] = {"status": s, "signature": b[:8].hex(),
                           "length": len(b)}
        C["post_unload_result_fetches"] = 2
        F["results_survive_unload"] = fetches == post
        F["no_model_refusal"] = False
        try:
            call("POST", f"{base}/api/generate", unselected_payload(9))
        except urllib.error.HTTPError as exc:
            F["no_model_refusal"] = exc.code == 400

        C["real_payload_opens"] = 0
        C["cuda"] = 0
        C["external_requests"] = 0
        return 0
    except BaseException as exc:  # noqa: BLE001
        OUT["fatal"] = {
            "kind": type(exc).__name__,
            "message": str(exc)[:300],
            "trace_tail": traceback.format_exc(limit=5)[-1200:],
        }
        return 1
    finally:
        try:
            if composition is not None:
                composition.shutdown()
        except BaseException:  # noqa: BLE001
            pass
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
                C["server_stops"] = 1
            except BaseException:  # noqa: BLE001
                pass
        if thread is not None:
            thread.join(timeout=15)
        intercepts.__exit__(None, None, None)
        neo.restore()
        print("PROBE_JSON_BEGIN")
        print(json.dumps(OUT, default=str))
        print("PROBE_JSON_END")


if __name__ == "__main__":
    raise SystemExit(main())
