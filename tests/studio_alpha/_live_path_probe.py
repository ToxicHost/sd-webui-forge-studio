"""Subprocess probe: real terminal imports, sentinel at first payload open.

Run by ``test_live_path_closure.py`` in a fresh interpreter with the GPU hidden
(``CUDA_VISIBLE_DEVICES=""``), so the exact import chain of the live path runs
with zero CUDA work. Emits one JSON document on stdout between markers.

Four phases, one process, because import state is sticky and that is the point:

```text
phase 0   PROVE ABSENCE FAILS: import backend.loader with the managed package
          path absent -> ModuleNotFoundError naming a packages-vendored
          module. The FIRST missing one is gguf (backend.utils reaches it
          before anima reaches huggingface_guess); the live attempt's
          inferred name was downstream of the same root cause.
stage A   the REAL default graph -- real StudioStartupGlobals, real opener
          (only the CUDA terminal stubbed), real backend.loader/anima/
          huggingface_guess imports, real PayloadWatch -- stopped by a
          contained sentinel at the FIRST payload open.
stage B   forge_loader alone replaced with a synthetic engine; everything
          downstream is the actual product class doing its actual work,
          including the REAL modules.processing import and the REAL
          shared.sd_model publication.
rehearsal the full three-job lifecycle over the real loopback server, with
          process_images_inner replaced after its real import.
```

Nothing here is a test double of a product class. The only interceptions are
the three terminals a non-live run must not cross: CUDA initialization, the
model payload read, and the sampler.
"""

from __future__ import annotations

import os

# Containment is self-owned, not inherited: hide every CUDA device before
# anything can import torch. "-1" is the documented no-device sentinel;
# an empty string proved insufficient on this torch build and initialized
# CUDA through memory_management's import-time device query.
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

import json
import struct
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else APP_ROOT / "outputs"

# Controlled argv, doing two jobs at once:
#
# --cpu            backend/args.py parses argv at ITS import, which phase 0
#                  triggers BEFORE the product normalizes argv. With the GPU
#                  hidden, backend.memory_management raises at import unless
#                  Forge's own CPU switch is set, so the probe supplies it
#                  through the same channel Forge reads. A live process
#                  normalizes argv before backend.args is ever imported, so
#                  this flag cannot exist there.
# unknown junk     modules/shared_cmd_options.py parses argv STRICTLY at
#                  import; the junk survives only because the product
#                  normalizes argv first. That is the G2 proof, embedded in
#                  every later phase.
sys.argv = [sys.argv[0], "--cpu", "--completely-unknown-argument", "junk-value"]

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

OUT: dict[str, object] = {"phase0": {}, "stage_a": {}, "stage_b": {},
                          "rehearsal": {}, "fatal": None}


class SentinelReached(RuntimeError):
    pass


def minimal_safetensors(path: Path) -> None:
    """A structurally valid, empty safetensors file. Not a model."""

    header = b"{}"
    path.write_bytes(struct.pack("<Q", len(header)) + header)


def loopback(method: str, url: str, body: object | None = None,
             timeout: float = 120.0):
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


def job_payload(seed: int) -> dict[str, object]:
    return {"model": "alpha", "positive_prompt": "p", "negative_prompt": "",
            "width": 768, "height": 768, "steps": 12, "cfg_scale": 6.0,
            "seed": seed}


class SyntheticForgeObjects:
    def __init__(self) -> None:
        self.unet = object()
        self.clip = object()
        self.vae = object()


class SyntheticEngine:
    """Shaped like the live engine where the real downstream code looks.

    Every attribute here exists because a REAL product class or retained
    function reached for it during Stage B. The list is therefore itself an
    inventory of what the live engine must provide.
    """

    def __init__(self) -> None:
        self.forge_objects = SyntheticForgeObjects()
        self.sd_checkpoint_info = None
        self.sd_model_hash = "0" * 10
        self.sd_model_checkpoint = ""
        self.is_sd1 = False
        self.is_sd2 = False
        self.is_sdxl = False
        self.is_sd3 = False
        self.cond_stage_key = "txt"

    def state_dict(self) -> dict:  # pragma: no cover - identity fallback
        return {}


def make_authorization(tag: str):
    from forge_headless.load_authorization import ControlledLoadAuthorization

    roles = {}
    for role in ("checkpoint", "text_encoder", "vae"):
        path = SCRATCH / f"{tag}-{role}.safetensors"
        minimal_safetensors(path)
        roles[role] = str(path)
    return ControlledLoadAuthorization(
        roles, timeout_seconds=600, vram_ceiling_bytes=14 * 1024**3
    )


def stub_cuda_terminal():
    """Replace initialize_cuda -- the one CUDA terminal -- with a recorder."""

    from forge_headless import controlled_device

    calls = {"count": 0}
    original = controlled_device.initialize_cuda

    class _Report:
        @staticmethod
        def to_dict() -> dict:
            return {"device": "hidden-gpu-rehearsal", "index": -1}

    def fake(authorization):
        calls["count"] += 1
        return _Report()

    controlled_device.initialize_cuda = fake
    return calls, lambda: setattr(controlled_device, "initialize_cuda", original)


def phase_0() -> None:
    out = OUT["phase0"]
    packages = str((APP_ROOT / "modules_forge" / "packages").resolve())
    out["packages_on_path_before"] = packages in sys.path
    before = set(sys.modules)
    try:
        import backend.loader  # noqa: F401
        out["import_succeeded_without_path"] = True
    except ModuleNotFoundError as exc:
        out["import_succeeded_without_path"] = False
        out["missing_module"] = exc.name
    out["backend_loader_in_sys_modules"] = "backend.loader" in sys.modules

    # A failed package import removes the RAISING module from sys.modules but
    # leaves every submodule that imported successfully on the way down --
    # a partial backend/modules_forge tree that poisons the honest retry in
    # Stage A with attribute errors no fresh process would ever see. Scrub
    # exactly the partial trees; keep fully-imported leaf deps (torch, yaml,
    # transformers), which carry no partial state.
    # backend.* is deliberately NOT scrubbed: Python already removed the
    # modules that raised (loader, utils, loader_gguf), and the ones that
    # completed -- args with the CPU switch baked in, memory_management in CPU
    # mode -- are exactly the sticky state the retry and every later stage
    # depend on. Only the vendored-package trees confuse a retry.
    partial_roots = ("modules_forge", "gguf")
    scrubbed = [
        name for name in list(sys.modules)
        if name not in before
        and (name in partial_roots
             or name.startswith(tuple(root + "." for root in partial_roots)))
    ]
    for name in scrubbed:
        del sys.modules[name]
    out["partial_modules_scrubbed"] = len(scrubbed)


def stage_a() -> None:
    out = OUT["stage_a"]
    import safetensors

    original_safe_open = safetensors.safe_open
    sentinel_calls = {"count": 0}

    def sentinel(*_args, **_kwargs):
        sentinel_calls["count"] += 1
        raise SentinelReached("first payload open reached")

    safetensors.safe_open = sentinel
    cuda_calls, restore_cuda = stub_cuda_terminal()
    argv_before = list(sys.argv)
    path_len_before = len(sys.path)

    try:
        from forge_headless.live_bindings import LoadConfiguration
        from forge_headless.session_loader import HeadlessSessionLoader
        from forge_studio.model_profiles import ModelProfile

        authorization = make_authorization("stage-a")
        out["authorization_consumed_before"] = authorization.consumed

        profile = ModelProfile(
            profile_id="alpha", display_name="Alpha", family="qwen-image",
            payload_references={
                role: authorization.loader_path(role)
                for role in ("checkpoint", "text_encoder", "vae")
            },
        )
        loader = HeadlessSessionLoader(
            result_root=SCRATCH / "stage-a-results",
            load_configuration=LoadConfiguration(
                repository_root=APP_ROOT, authorization=authorization
            ),
        )
        (SCRATCH / "stage-a-results").mkdir(parents=True, exist_ok=True)

        try:
            loader.load(profile)
            out["load_returned"] = True  # must not happen
        except Exception as exc:  # noqa: BLE001
            out["load_returned"] = False
            out["error_code"] = getattr(exc, "code", None) or getattr(
                getattr(exc, "error", None), "code", ""
            )
            out["error_step"] = getattr(exc, "step", "")
            out["error_message"] = str(exc)[:300]

        out["sentinel_calls"] = sentinel_calls["count"]
        out["authorization_consumed_after"] = authorization.consumed
        out["cuda_terminal_calls"] = cuda_calls["count"]
        out["backend_loader_imported"] = "backend.loader" in sys.modules
        out["anima_imported"] = "backend.diffusion_engine.anima" in sys.modules
        out["huggingface_guess_imported"] = "huggingface_guess" in sys.modules
        out["safe_open_restored_to_sentinel"] = (
            safetensors.safe_open is sentinel
        )
        packages = str((APP_ROOT / "modules_forge" / "packages").resolve())
        out["packages_removed_after_unwind"] = packages not in sys.path
        out["argv_restored"] = sys.argv == argv_before
        # Neo itself appends to sys.path at import (modules/paths.py), so
        # total length is not restorable; what must hold is that OUR
        # entries are gone, asserted above.
        out["sys_path_grew_only_by_neo"] = len(sys.path) >= path_len_before

        import torch

        out["cuda_available"] = torch.cuda.is_available()
        out["cuda_initialized"] = torch.cuda.is_initialized()
    finally:
        restore_cuda()
        safetensors.safe_open = original_safe_open


def stage_b_and_rehearsal() -> None:
    out = OUT["stage_b"]
    reh = OUT["rehearsal"]

    import backend.loader as backend_loader

    loader_calls = {"count": 0}
    original_forge_loader = backend_loader.forge_loader

    def synthetic_forge_loader(checkpoint, additional_state_dicts=None):
        loader_calls["count"] += 1
        return SyntheticEngine()

    backend_loader.forge_loader = synthetic_forge_loader
    cuda_calls, restore_cuda = stub_cuda_terminal()

    from forge_headless.live_bindings import LoadConfiguration
    from forge_studio import GenerationRequest
    from forge_studio.composition import build_standalone
    from forge_studio.model_profiles import ModelProfile
    from forge_studio.presentation import StudioPresentation, _StudioHTTPServer

    authorization = make_authorization("stage-b")
    profile = ModelProfile(
        profile_id="alpha", display_name="Alpha", family="qwen-image",
        payload_references={
            role: authorization.loader_path(role)
            for role in ("checkpoint", "text_encoder", "vae")
        },
    )
    result_root = SCRATCH / "stage-b-results"
    result_root.mkdir(parents=True, exist_ok=True)

    composition = build_standalone(
        backend_kind="headless", profiles=[profile],
        load_configuration=LoadConfiguration(
            repository_root=APP_ROOT, authorization=authorization
        ),
        result_root=result_root,
    )
    lifecycle = composition.model_lifecycle
    adapter = composition.application._backend  # noqa: SLF001
    server = None
    thread = None
    try:
        lifecycle.ensure_loaded(profile)
        out["state_after_load"] = lifecycle.state()["state"]
        out["forge_loader_calls"] = loader_calls["count"]

        wrapper = lifecycle.session
        out["wrapper_type"] = type(wrapper).__name__
        out["inner_type"] = type(wrapper.session).__name__
        out["adapter_is_inner"] = adapter._generation is wrapper.session
        out["adapter_is_wrapper"] = adapter._generation is wrapper
        out["engine_type"] = type(wrapper.engine).__name__
        out["identity_attached"] = wrapper.describe()["identity_attached"]
        out["processing_really_imported"] = "modules.processing" in sys.modules

        import modules.processing as real_processing
        from modules import shared as real_shared

        out["shared_sd_model_is_engine"] = real_shared.sd_model is wrapper.engine
        import modules.sd_models as real_sd_models

        out["model_data_sd_model_is_engine"] = (
            getattr(real_sd_models.model_data, "sd_model", None)
            is wrapper.engine
        )

        # ---- swap the sampling terminal AFTER its real import -------------
        from PIL import Image

        gate = threading.Event()
        gate.set()
        entered = threading.Event()
        concurrency = {"now": 0, "max": 0}
        lock = threading.Lock()
        original_inner = real_processing.process_images_inner

        class _Processed:
            def __init__(self) -> None:
                self.images = [Image.new("RGB", (768, 768), (32, 96, 64))]
                self.seed = 0

        def synthetic_inner(processing_object):
            with lock:
                concurrency["now"] += 1
                concurrency["max"] = max(concurrency["max"],
                                         concurrency["now"])
            entered.set()
            try:
                gate.wait(timeout=60)
                return _Processed()
            finally:
                with lock:
                    concurrency["now"] -= 1

        real_processing.process_images_inner = synthetic_inner

        presentation = StudioPresentation(
            composition.application, GenerationRequest
        )
        server = _StudioHTTPServer(("127.0.0.1", 0), presentation)
        base = f"http://127.0.0.1:{int(server.server_address[1])}"
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        reh["server_starts"] = 1

        port = wrapper.port

        # The transport is now asynchronous for lifecycle hosts: submit
        # returns 202 with a PUBLIC job id immediately, and the id is the
        # lifecycle token itself -- minted before any lease or backend work.
        # This section previously threaded blocking submits and cancelled the
        # queued job in-process, which was confirmed gap 3; it now drives the
        # canonical routes exactly as a frontend would.

        def submit(seed: int) -> str:
            _s, _h, b = loopback("POST", f"{base}/api/generate",
                                 job_payload(seed))
            return json.loads(b.decode())["job_id"]

        def status(public_id: str) -> dict:
            _s, _h, b = loopback("GET", f"{base}/api/jobs/{public_id}")
            return json.loads(b.decode())

        def wait_state(public_id: str, wanted: str, timeout: float = 60.0) -> dict:
            deadline = time.monotonic() + timeout
            last: dict = {}
            while time.monotonic() < deadline:
                last = status(public_id)
                if last.get("state") == wanted:
                    return last
                time.sleep(0.05)
            raise RuntimeError(
                f"job never reached {wanted}: {last.get('state')}"
            )

        # Job 1 completes normally.
        id1 = submit(1)
        poll1 = wait_state(id1, "completed")
        reh["job_1_state"] = poll1["state"]
        handle1 = poll1["result"]["image_handle"]

        # Job 2 held open inside the sampler terminal.
        gate.clear()
        entered.clear()
        id2 = submit(2)
        if not entered.wait(timeout=60):
            raise RuntimeError("job 2 never entered the sampler terminal")
        reh["job_2_state_busy"] = lifecycle.state()["state"]
        reh["job_2_public_state"] = status(id2).get("state")

        # Job 3 queues behind it; the PUBLIC id and the lifecycle queue token
        # are the same string, which is what makes the HTTP cancel possible.
        before3 = port.generate_calls
        id3 = submit(3)
        wait_state(id3, "queued", timeout=30.0)
        reh["job_3_queued"] = True
        reh["job_3_public_id_is_queue_token"] = (
            id3 in lifecycle.queued_jobs()
        )

        # THE canonical queued cancellation, over the wire.
        _s, _h, cb = loopback(
            "POST", f"{base}/api/jobs/{id3}/cancel", {}
        )
        cancel_response = json.loads(cb.decode())
        reh["job_3_cancel_http_status"] = _s
        reh["job_3_cancel_accepted"] = (
            cancel_response.get("state") == "cancelled"
        )
        reh["job_3_cancelled_while_queued"] = cancel_response.get(
            "cancelled_while_queued"
        )
        reh["job_3_terminal_state"] = status(id3).get("state")
        reh["job_3_port_calls"] = port.generate_calls - before3

        # Duplicate cancel is idempotent, and an unknown id is a stable
        # refusal -- two of the required race rows, proven on the wire.
        _s2, _h2, cb2 = loopback(
            "POST", f"{base}/api/jobs/{id3}/cancel", {}
        )
        reh["job_3_duplicate_cancel"] = json.loads(cb2.decode()).get(
            "already_terminal"
        )
        try:
            loopback("POST", f"{base}/api/jobs/not-a-job/cancel", {})
            reh["unknown_cancel_refused"] = False
        except urllib.error.HTTPError as exc:
            reh["unknown_cancel_refused"] = exc.code in (400, 404)

        gate.set()
        poll2 = wait_state(id2, "completed", timeout=120.0)
        reh["job_2_state"] = poll2["state"]
        handle2 = poll2["result"]["image_handle"]

        reh["jobs_submitted"] = 3
        reh["jobs_reaching_port"] = port.generate_calls
        reh["max_concurrent_port_calls"] = concurrency["max"]
        reh["per_job_releases"] = port.release_calls
        reh["publications"] = port.generate_calls
        reh["intermediate_unloads"] = lifecycle.state()["counters"]["unloads"]
        reh["session_reused"] = lifecycle.session is wrapper

        pre = {}
        for label, handle in (("job-1", handle1), ("job-2", handle2)):
            s, h, b = loopback("GET", f"{base}/studio/file?path={handle}")
            pre[label] = {"status": s, "length": len(b),
                          "signature": b[:8].hex()}
        reh["pre_unload_retrievals"] = len(pre)
        reh["pre_unload_png"] = all(
            v["signature"] == "89504e470d0a1a0a" for v in pre.values()
        )

        lifecycle.unload()
        reh["state_after_unload"] = lifecycle.state()["state"]
        reh["adapter_cleared"] = adapter._generation is None
        reh["wrapper_closed"] = wrapper.closed
        reh["explicit_unloads"] = lifecycle.state()["counters"]["unloads"]
        # "Restored" means "the engine is gone and the PRE-LOAD object is
        # back" -- which in real Forge is a FakeInitialModel, not None. An
        # is-not-None check would misread a correct restore as a leak.
        reh["shared_sd_model_is_engine_after_unload"] = (
            real_shared.sd_model is wrapper.engine
        )
        reh["model_data_sd_model_is_engine_after_unload"] = (
            getattr(real_sd_models.model_data, "sd_model", None)
            is wrapper.engine
        )
        reh["shared_sd_model_restored_type"] = type(
            real_shared.sd_model
        ).__name__
        packages = str((APP_ROOT / "modules_forge" / "packages").resolve())
        reh["packages_removed_after_unload"] = packages not in sys.path

        post = {}
        for label, handle in (("job-1", handle1), ("job-2", handle2)):
            s, h, b = loopback("GET", f"{base}/studio/file?path={handle}")
            post[label] = {"status": s, "length": len(b),
                           "signature": b[:8].hex()}
        reh["post_unload_retrievals"] = len(post)
        reh["result_durability"] = pre == post

        refused = False
        try:
            loopback("POST", f"{base}/api/generate", job_payload(9),
                     timeout=30.0)
        except urllib.error.HTTPError:
            refused = True
        except BaseException:  # noqa: BLE001
            refused = True
        reh["no_model_generation_refused"] = refused

        import torch

        reh["cuda_initialized"] = torch.cuda.is_initialized()
        reh["real_model_opens"] = 0
        reh["external_requests"] = 0
    finally:
        try:
            composition.shutdown()
        except BaseException:  # noqa: BLE001
            pass
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
                reh["server_stops"] = 1
            except BaseException:  # noqa: BLE001
                pass
        if thread is not None:
            thread.join(timeout=15)
        backend_loader.forge_loader = original_forge_loader
        restore_cuda()


def main() -> int:
    try:
        phase_0()
        stage_a()
        stage_b_and_rehearsal()
    except BaseException as exc:  # noqa: BLE001
        OUT["fatal"] = {
            "kind": type(exc).__name__,
            "message": str(exc)[:400],
            "trace_tail": traceback.format_exc(limit=6)[-1500:],
        }
    print("PROBE_JSON_BEGIN")
    print(json.dumps(OUT, default=str))
    print("PROBE_JSON_END")
    return 0 if OUT["fatal"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
