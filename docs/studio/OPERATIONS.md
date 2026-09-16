# Studio operations runbook

How to actually drive this thing. Every command here was run in anger; the
gotchas are ones that cost real time.

> **Reviewed 2026-08-11** against HEAD. Two entries were stale and are
> corrected below: the `generation` field list (nine, actually thirteen) and
> the "one at a time" submission rule (a defect that the coordinator's
> admission gate removed).
>
> Treat this file as a runbook, not scripture. It has described behaviour the
> product no longer had at least twice, which is this codebase's most frequent
> defect in prose as well as in code. Where it disagrees with the source, the
> source wins — and then fix the line.

Paths assume Git Bash. The git repo root is `app/` — `Evidence/`,
`Reference/`, `Private-Local/` and `Studio-Results/` are OUTSIDE it.

Written with placeholders, deliberately. `$WORKSPACE` is wherever this
checkout lives; substitute it. An absolute path here would be the owner's home
directory, and this file ships -- see the privacy rule in
`tests/studio_alpha/test_distributable_privacy.py`.

```text
WORKSPACE   $WORKSPACE
APP         $WORKSPACE/app                                       (git root)
PYTHON      ./venv/Scripts/python.exe                            (from APP)
CONFIG      ../studio-config.json                                (from APP)
RESULTS     $WORKSPACE/Studio-Results
```

---

## 1. Launch Studio

```bash
cd "$WORKSPACE/app"
./venv/Scripts/python.exe launch_studio.py --config "../studio-config.json" > /path/to/studio.log 2>&1
```

**Use the Bash tool's `run_in_background: true`.** A `&`-backgrounded job dies
when the tool call returns — this killed the server mid-generation twice.

The port is **ephemeral** (`"port": 0` in the config). Read it from the log:

```bash
until grep -q "STUDIO_READY" studio.log; do sleep 2; done
PORT=$(grep -o "port=[0-9]*" studio.log | head -1 | cut -d= -f2)
```

Startup takes ~10-15 s. A healthy cold start logs:

```text
Studio internal alpha starting in state: no_model
Ignoring retired configuration keys: profiles, selected_profile_id, autoload
Remembered dropdown selection for checkpoint, text_encoder, vae. Nothing is loaded...
Model directory for checkpoint: 1 found.
STUDIO_READY host=127.0.0.1 port=NNNNN
```

### Shutting down — read this

**Do NOT `Stop-Process -Force` while a model is resident.** It kills the CUDA
context without releasing VRAM. Use `TaskStop` on the background task id.
If you must use PowerShell, check `/api/model/state` shows `no_model` first,
or call `POST /api/model/unload`.

Find strays:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*launch_studio.py*' } |
  Select-Object ProcessId, CreationDate
```

---

## 2. The authorized model ids

```text
checkpoint     21f71e0abb233ccaa555d2d931f4c0c2   Hicks_Anima_Beta
text_encoder   44ff3b65f75fd4b13e984801ada2dd8d   Qwen3-0.6B-heretic-abliterated-uncensored
vae            22f52d209ed0692de175005e7d571ae6   qwen_image_vae
```

**These are only valid while the roots are the current three.** A model id is
`sha256(domain, role, RESOLVED ROOT PATH, relative name)[:32]`, so changing a
root's *path* changes every id under it. Re-read them rather than trusting
this table if the roots have moved:

```bash
curl -s "http://127.0.0.1:$PORT/studio/settings/model_roots"
```

That answers with `roots`, per-role `status`/`entry_count`, and the persisted
`last_model_selection` — which is where the three ids above come from. Roots
unchanged plus `status: ready` means the ids are unchanged, because the id is a
function of the resolved root path and the relative name.

In the page: `Array.from(document.getElementById("paramModel").options).map(o => o.value)`

**Not `/api/models`.** That is the *Neo backend* catalogue, and it answers
`{"models": []}` until a load has initialised the engine — the same reason
`/api/registries` is empty cold. It is not the three role catalogues and it will
mislead you on a cold server.

---

## 3. Run one generation without a browser

This is the fastest live proof and needs no browser pane.

```bash
./venv/Scripts/python.exe - <<'PY'
import json, time, urllib.request
P = 59013          # <- your port
def call(path, body=None):
    url = f"http://127.0.0.1:{P}{path}"
    if body is None:
        return json.loads(urllib.request.urlopen(url, timeout=30).read())
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{P}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

print(call("/api/generate", {
    "model": "",                       # may be empty when model_selection is present
    "model_selection": {               # a SIBLING of `generation`, never inside it
        "checkpoint_model_id":   "21f71e0abb233ccaa555d2d931f4c0c2",
        "text_encoder_model_id": "44ff3b65f75fd4b13e984801ada2dd8d",
        "vae_model_id":          "22f52d209ed0692de175005e7d571ae6",
    },
    "generation": {
        "positive_prompt": "a red apple on a white plate",
        "negative_prompt": "",
        "seed": 7, "steps": 6, "cfg_scale": 4.0,
        "width": 512, "height": 512,
        "sampler": "Euler", "scheduler": "",
    },
}))

for _ in range(40):
    time.sleep(15)
    jobs = call("/api/jobs")["jobs"]
    print([(j["job_id"][-6:], j["state"]) for j in jobs])
    if jobs and all(j["state"] in ("completed", "failed", "cancelled") for j in jobs):
        break
print(call("/api/model/state")["counters"])
PY
```

A cold generation takes **60-100 s** (load ~40 s, then sampling).

### Request rules that bite

```text
seed >= 0            a negative seed is REFUSED (GENERATION_SEED_NOT_FIXED).
                     The UI resolves "random" client-side for this reason.
model may be ""      when model_selection is present. Requiring both was a
                     real defect fixed in P0.2.
the body shape        canonical is `{model_selection, generation}` -- model
                     IDENTITY beside the request, never inside it. Inside
                     `generation`, THIRTEEN fields as of 2026-08-11:
                       positive_prompt, negative_prompt, seed, steps,
                       cfg_scale, width, height, sampler, scheduler,
                       preview_enabled,
                       hires          nested group, absent means absent
                       auto_detail    nested group, absent means absent
                       variation      nested group, absent means absent
                     The authority is `_GENERATION_FIELDS` in presentation.py;
                     this list has been wrong before and prose is not the
                     contract. The three nested groups each refuse unknown
                     keys BY NAME, e.g. `hires.nonsense`.
                     `model` and `model_selection` sit at the top level.
                     The older FLAT body (everything at the top level) is
                     still accepted and rewritten into the canonical shape at
                     one point, so both produce byte-identical kwargs. It is
                     transitional; write new callers nested.
unknown field        rejected, and told WHERE: `generation.enable_hr` reads
                     differently from `enable_hr`. Four shapes are refused
                     rather than tolerated -- a catalogue id at the top level
                     (the second-spelling trap), `model_selection` inside
                     `generation`, a MIXED body that nests some fields and
                     leaves others flat, and any unknown key either side.
sampler/scheduler    NAMES from /api/registries, or "" for engine default.
rapid cold submits   SAFE, and this rule is retired. It used to say "submit,
                     wait for ready, then submit again", because
                     `application.submit_generation` reconciled the model
                     BEFORE taking the lease, so racing workers interleaved
                     two `ensure_loaded` calls and the second failed
                     MODEL_ALREADY_LOADING.
                     The coordinator's admission gate fixed that: exactly one
                     job passes at a time, as a stated invariant rather than a
                     property emerging from whichever thread won a condition
                     variable. `jobs.py` names this runbook line as the
                     behaviour it removed.
                     Proven: `Evidence/cold_queue_live.py`, 10/10, N
                     submissions off a Barrier, ending loads=1 switches=0.
                     MODEL_ALREADY_LOADING still exists in
                     `model_lifecycle.py` and can still be raised by a caller
                     that bypasses the coordinator. Going through
                     /api/generate, it is no longer reachable by racing.
```

### Reading the outcome

```bash
GET /api/model/state     # state, profile_id, counters
GET /api/jobs            # {"jobs": [{job_id, state, progress, ...}]}
GET /api/jobs/<job_id>   # one record, incl. error {code, message}
GET /api/registries      # {samplers, schedulers, available}
GET /api/status          # health; answers with no model resident
```

Counters worth watching: `loads`, `reuses`, `switches`, `leases`,
`releases`, `terminal_cache_clears`, `closes`, `unloads`.

Results land at `Studio-Results/headless-<uuid4hex>.png`.

`error: null` on a failed job with a `backend_job_id` is **ambiguous**, and the
two readings need different responses:

```text
the backend PROCESS DIED        check GPU free memory. A game or another app
                                holding VRAM presents as exit 139 during load,
                                not as a traceback. The log stops mid-sentence.
the backend FAILED and the      the process is fine and the log is intact. The
reason was discarded            reason is on the progress object and never
                                reaches the record: submit_generation returns
                                normally, so jobs.py:167-186 never fires, and
                                _terminal_from_backend (jobs.py:198-210) reads
                                only the state, leaving record["error"] at the
                                None set on jobs.py:149.
```

Reproduce the second reading in one call: generate with
`"sampler": "NotASampler"`. `create_sampler` asserts (`sd_samplers.py:37`), the
job reports `failed`, and `error` is `null` with the process healthy.

So check whether the log ENDS abruptly before concluding the process died.

---

## 4. Drive the browser

```text
mcp__Claude_Browser__preview_start  {url: "http://127.0.0.1:PORT/studio/"}
    ^ REQUIRED FIRST. `navigate` errors with "No preview is open".
mcp__Claude_Browser__navigate       for subsequent URLs (new port = new nav)
mcp__Claude_Browser__javascript_tool  the workhorse
```

`computer {action:"screenshot"}` fails unless the pane is displayed. Use
`javascript_tool` or `read_page` instead — they always work.

The page takes **6-9 seconds** to finish booting. `await new Promise(r =>
setTimeout(r, 8000))` before asserting on the DOM.

### Element ids

```text
paramModel  paramTextEncoder  paramVAE      the three role dropdowns
paramPrompt paramNeg          paramSeed
paramSteps  paramCFG          paramWidth    paramHeight
paramSampler paramScheduler                 populated from /api/registries
genBtn                                      Generate
studioUnloadBtn                             optional Unload
modelRootCheckpoint / ...TextEncoder / ...Vae         Settings text inputs
modelRootBrowseCheckpoint / ...                        Browse buttons
modelRootAddCheckpoint / ...                           Add buttons
modelRootListCheckpoint / ...                          ordered root rows
studioDirPicker  studioDirCrumbs  studioDirPlaces
studioDirListing studioDirChoose  studioDirReveal      the picker overlay
```

### The dropdown race — this WILL catch you

Setting `paramModel` makes app.js asynchronously rebuild the dependent
dropdowns, which **resets `paramVAE` to "Automatic"**. Set the checkpoint
first, wait, then the others, then *verify* before clicking Generate:

```javascript
const set = (id, v) => { const e = document.getElementById(id); e.value = v;
  e.dispatchEvent(new Event("change", {bubbles:true})); };
set("paramModel", "21f71e...");        await wait(2500);
set("paramTextEncoder", "44ff3b...");  await wait(1200);
set("paramVAE", "22f52d...");          await wait(1500);
if (document.getElementById("paramVAE").value !== "22f52d...") throw new Error("reverted");
document.getElementById("genBtn").click();
```

`window.StudioModelControls` and `window.StudioDirPicker` are the two frozen
page APIs; `StudioDirPicker.save()` performs the roots write.

---

## 5. Settings, the token, and the filesystem browser

Every config write and every browse route needs the per-process CSRF token,
a matching `Origin`, and `Content-Type: application/json`.

```bash
GET  /studio/settings/model_roots      # -> {roles, roots, persistable,
                                       #     last_model_selection, token}
POST /studio/settings/model_roots      # {"model_roots": {role: [paths]}}
POST /studio/settings/model_selection  # {"last_model_selection": {...}}
POST /studio/fs/capabilities|places|resolve|list|reveal|diagnostics
```

Header: `X-Studio-Settings-Token: <token from the GET>`

Browse flow: `resolve {path}` is the ONLY text→path entry point; after that
navigate with `list {handle}`, `list {handle, child}`, `list {handle,
parent:true}`. Handles are invalidated by any roots write.

---

## 6. Validation

```bash
# canonical — the gate. Exit code matters, not just the OK line.
./venv/Scripts/python.exe tests/studio_alpha/run_tests.py ; echo "EXIT: $?"

# discovery
./venv/Scripts/python.exe -I -S -B -m unittest discover -s tests/studio_alpha -t . -p "test_*.py"

# preflight — the -I -S -B are REQUIRED, else NO_GO / ISOLATED_NO_SITE_PYTHON_REQUIRED
./venv/Scripts/python.exe -I -S -B scripts/preflight/bootstrap.py self-test

# one suite
./venv/Scripts/python.exe -I -S -B -m unittest discover -s tests/studio_alpha -t . -p "test_fs_browser.py"
```

Canonical runs ~6-9 minutes. **Never run it while Studio is live** — CPU
contention makes it look hung (once: 20+ minutes at 20 CPU-seconds).

Redirect to a file and grep, or the output floods:

```bash
./venv/Scripts/python.exe tests/studio_alpha/run_tests.py > out.txt 2>&1
grep -E "^Ran |^OK|^FAILED|network operation" out.txt
grep -E "^(FAIL|ERROR):" out.txt | head
```

---

## 7. Editing this codebase — the tooling traps

**Bash heredocs eat backslashes.** `<<'PY'` with `\n`, `\x00`, `\\` or
apostrophe-heavy prose has broken repeatedly. For any non-trivial patch:
write the patch script with the **Write tool** into the scratchpad, then run
it. That always works.

**The Edit tool cannot match lines that mix literal Unicode with `\uXXXX`
escapes.** Replace by line index from a Python script instead.

Write files with `newline="\n"` — the repo is LF and git warns on CRLF.

### Conventions you must honour

```text
count pins        every suite has EXPECTED_*_TESTS + SuiteIntegrityTests.
                  Adding or removing a test means updating the constant.
frontend hashes   ANY frontend edit requires repinning
                  tests/studio_alpha/test_s07_design_system.py:
                    CANONICAL_SHA256[...] per changed file
                    FRONTEND_MANIFEST_SHA256, MOUNTED_MANIFEST_SHA256
                    canonical/mounted counts (currently 65 / 66)
                    OPTIONAL_SCRIPT_ORDER if a script was added
                  AND _SOURCE_LOADER_CSP_HASH in forge_studio/presentation.py
                  if index.html's inline <script> changed. A stale CSP hash
                  blocks the page's own loader and Studio DOES NOT BOOT while
                  every source test still passes.
```

Recompute everything at once:

```bash
./venv/Scripts/python.exe -I -S -B -c "
import sys,re,hashlib,base64; sys.path.insert(0,'tests/studio_alpha')
import test_s07_design_system as T
mounted=tuple(p for p in T.FRONTEND_ROOT.rglob('*') if p.is_file())
canon=tuple(p for p in mounted if p.name!='ag-psd.js')
fr=[f'{p.relative_to(T.FRONTEND_ROOT).as_posix()}|{T._sha256(p)}' for p in canon]
mr=[('javascript/ag-psd.js' if p.name=='ag-psd.js' else 'frontend/'+p.relative_to(T.FRONTEND_ROOT).as_posix())+f'|{T._sha256(p)}' for p in mounted]
print('COUNTS', len(canon), len(mounted))
for n in ('index.html','app.css','app.js'): print(n, T._sha256(T.FRONTEND_ROOT/n))
print('FRONTEND', T._manifest_sha256(fr)); print('MOUNTED', T._manifest_sha256(mr))
html=(T.FRONTEND_ROOT/'index.html').read_text(encoding='utf-8')
m=re.search(r'<script>([\s\S]*?)</script>', html)
print('CSP', base64.b64encode(hashlib.sha256(m.group(1).encode('utf-8')).digest()).decode())
"
```

The CSP value must be computed **exactly that way** — the s06 test recomputes
it from the served file, so any other slice gives a hash that looks right and
fails.

### A rule stated in prose trips the check that enforces it

Three times: a docstring saying "no `sys.platform`", a comment saying "no
`shell=True`", a Dockerfile comment saying "no gradio". Check the **AST**, or
filter comment lines. Never a raw-text search over a file that documents its
own rule.

---

## 8. Machine safety

```text
Private-Local/     the owner's model library. READ ONLY. Never copy from it,
                   write to it, or use it as a test fixture.
test fixtures      disposable directories you create, with a containment
                   assertion before anything destructive.
cleanup            move to Evidence/_quarantine/<run-id>/, never delete.
network            loopback only. Never rebind to 0.0.0.0 to make a test
                   pass; never weaken Origin/CSRF checks.
privileges         no sudo, no Administrator, no driver or CUDA changes, no
                   registry/PATH/profile edits, no system package manager.
GPU                a game or another app can hold most of the VRAM. Check
                   `nvidia-smi --query-gpu=memory.used,memory.total
                   --format=csv,noheader` before blaming the code for an
                   exit-139 crash during load.
```
