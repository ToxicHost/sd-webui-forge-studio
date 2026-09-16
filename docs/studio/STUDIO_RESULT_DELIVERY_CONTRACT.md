# Studio Result Delivery Contract

How a completed generation reaches the browser without inline base64, without
disclosing a filesystem path, and without modifying the byte-pinned canonical
frontend.

---

## 1. The canonical frontend already defines this contract

The delivery shape was **not** designed here. It was read out of the pinned
frontend, which is immutable, and matched.

| Source | Line | Fact |
|---|---|---|
| `forge_studio/frontend/app.js` | 558-560 | `_studioFileUrl(path)` returns `` `${API.base}/studio/file?path=${encodeURIComponent(path)}` `` |
| `forge_studio/frontend/app.js` | 2566 | `result.images.map((b64, i) => …)` — the images array drives entry count |
| `forge_studio/frontend/app.js` | 2575-2581 | when `session_entries[i].source === "scratch"` and `.path` is set, the display URL is `_studioFileUrl(srv.path)`; the comment states `/studio/file` serves an "exact registered path" and "works in both Gradio and standalone mode, unlike `/file=`" |
| `forge_studio/frontend/app.js` | 2584-2588 | `/studio/file` "allowlists the exact files Studio wrote" |
| `forge_studio/frontend/app.js` | 2572 | the only interpretation of `.path` is a display-filename derivation: `.replace(/\\/g,"/").split("/").pop()` then strip the extension |
| `forge_studio/frontend/app.js` | 2610 | `b64: (i === 0 \|\| source === "live") ? b64 : null` — base64 is retained only for the newest entry, as a canvas fast path |
| `forge_studio/frontend/app.js` | 455-472 | the SessionEntry contract: `url` is a "same-origin file URL for saved/scratch, data: URL for live"; `canDeleteFile` is true only for scratch, and "backend gates deletion on its own registry too; never inferred from a URL" |

The frontend therefore already assumes a **server-side registry of exactly the
files Studio wrote**, addressed through a `path` query parameter.

The handoff's target route `/studio/results/<opaque-id>` was **not** used. Its
own §11 permits a different route "if the canonical frontend already requires a
specific owned URL shape", and §12 directs using the existing response
structure when a same-origin URL works through it. Both conditions hold.

### Reconciling the `path` parameter with "no path may reach the browser"

The value placed in `session_entries[i].path` is an **opaque handle shaped like
a path**, not a filesystem path:

```text
studio-result/<32 lowercase hex characters>.<extension>
```

The frontend round-trips it verbatim into `/studio/file?path=…` and derives
only a display filename from its last segment. No directory, drive, user name,
or real filename ever reaches the browser. Verified by the loopback check
`result_delivery_no_path_leak`, which asserts the serialized response contains
neither `output_path`, `metadata_path`, the result-root path, nor the result
root's directory name, and that no response header contains them either.

---

## 2. Architecture

```text
MockBackend / future ForgeBackendAdapter
    writes the file, returns GeneratedResult(output_path=<real path>, mime_type=…)
        |
StudioApplication  (owns ResultRegistry, injected result_root)
    result_asset(job_id) -> ResultAsset(handle, media_type, byte_length)
        |
StudioPresentation
    poll() adds image_handle / image_media_type / image_byte_length,
    and still strips output_path and metadata_path
        |
SourceFrontendAdapter
    session_entries = [{entry_id, source: "scratch", path: <handle>}]
        |
_StudioRequestHandler._serve_result
    GET /studio/file?path=<handle>  ->  raw bytes
```

`output_path` is now a **real resolved filesystem path**. Before this change
`MockBackend._persist_result` returned a `result_public_root`-prefixed string
that looked like a servable URL but was served by nothing — an ownership record
and a route conflated in one field. That confusion is removed.

---

## 3. Containment

Lookup is an **exact dictionary match**. A handle is never joined onto a
directory, so traversal, absolute paths, encoded separators, double encoding,
and alias tricks cannot reach a file that was never registered.

Layered checks:

1. **Shape** — `\Astudio-result/[0-9a-f]{32}\.[a-z0-9]{1,8}\Z`. Anything else is
   reported exactly like an unknown handle, so probing learns nothing.
2. **Registry membership** — unknown handle → `RESULT_NOT_FOUND` (404).
3. **Registration containment** — the file must `resolve(strict=True)` to a
   regular file beneath the injected root, or `RESULT_OUTSIDE_ROOT`.
4. **Read-time re-verification** — containment is re-checked on every read, so a
   symlink or reparse point swapped in after registration fails closed.
5. **Media type** — from the owned result record, never from a filename
   extension. The handle's extension is *derived from* the media type.
6. **Size** — 64 MiB ceiling at registration and at read.

A registered file's **real filesystem path returns 404** when submitted as a
handle. Confirmed over real HTTP in the loopback run:

```text
$WORKSPACE\...\loopback-results\mock-job-0003.svg      -> 404
studio-result/../../secret.svg                          -> 404
studio-result/..%2f..%2fsecret.svg                      -> 404
studio-result/..%252f..%252fsecret.svg                  -> 404
/etc/passwd                                             -> 404
C:/Windows/win.ini                                      -> 404
studio-result/000…000.svg (well-formed, unregistered)   -> 404
(empty)                                                 -> 404
```

---

## 4. Owned result root

Injected through composition: `StudioApplication(backend, result_root=…)`. No
`parents[2]` traversal, no dependence on the working directory, no system temp.
Delivery is opt-in — an application built without a root mints no handles and
`result_asset` returns `None`.

Current roots:

| Context | Root |
|---|---|
| mock serve / demo | `Evidence/studio-alpha-s07/results` |
| loopback validator | `Evidence/studio-real-adapter-contracts/loopback-results` |
| unit tests | a per-test temporary directory |

A real adapter supplies its own owned output root. Choosing a production
user-data path is deliberately deferred: no existing owned configuration
defines one.

---

## 5. Response policy

```text
HTTP/1.1 200 OK
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Content-Type: <media type from the result record>
Cache-Control: private, no-store
Content-Length: <exact byte length>
```

Results carry a **different, stricter CSP than the rest of the application**.
The page CSP permits inline styles and hashed scripts; a result must never
inherit that. `default-src 'none'` plus `sandbox` makes the response inert even
on direct navigation, which matters because the mock's current media type is
`image/svg+xml` and SVG is scriptable in a document context. Loaded through
`<img>` — how the canonical client uses it — SVG cannot execute script anyway;
the sandboxed policy closes the direct-navigation case as well.

No path-bearing header is emitted: no `Content-Disposition`, no `Location`, no
`ETag` derived from a path. No directory listing exists. `HEAD` sends headers
without a body. Range requests are out of scope.

Supported media types are exactly:

```text
image/png   image/jpeg   image/webp   image/svg+xml
```

`image/svg+xml` is present because it is what the current mock result record
actually produces. A real adapter will use `image/png`.

---

## 6. Host and Origin

`/studio/file` is served inside `do_GET` **after** `_reject_untrusted_host()`,
so it inherits Host validation with no bypass. A foreign `Host` returns 403 —
verified over real HTTP and by the loopback check
`result_delivery_foreign_host_rejected`. No CORS header, no wildcard origin, no
separate listener, no separate port.

---

## 7. Lifetime and retention

- Bounded LRU, default 64 entries. Registration is **idempotent per job**, so
  repeated observation cannot grow the registry.
- Eviction removes lookup capability. Evicted handles are remembered briefly
  and report `RESULT_GONE` (410); unknown handles report `RESULT_NOT_FOUND`
  (404). The evicted set is itself bounded.
- Cancelling a job releases its handle.
- `StudioApplication.shutdown()` clears the registry, so no handle survives
  shutdown. `StudioPresentation.shutdown()` already delegates to it.
- Cleanup only ever forgets registry entries. **It never deletes a file.**
- Reads do not consume a handle; repeated access is allowed while retained.

This is not a gallery and not a persistent asset database.

---

## 8. Metadata

Reproduction metadata travels in the generation JSON — resolved seed, effective
dimensions, model, and the `notice` for unsupported settings. `metadata_path`
stays internal and no endpoint accepts a metadata path. Sampler and scheduler
are still omitted rather than fabricated; they appear only once a backend
genuinely reports them. No separate metadata endpoint was added, because the
canonical client does not require one.

---

## 9. Compatibility

Preserved unchanged: inline data URLs, the legacy `/api/*` surface, the
socket-free demo, explicit result retrieval, result-free progress observation,
cancellation and recovery, and two-observer semantics.

The mock still returns its data URL **and** a handle. Data URL support was not
removed to force the new path. A real adapter can return
`image_data_url=None` with `output_path` set; the bridge then emits a
single-element `images` array containing an empty string so the canonical
client's `map` still produces one entry, and that entry renders from the
session-entry URL.

---

## 10. Test coverage

`tests/studio_alpha/test_result_delivery.py` — 33 tests covering registration,
handle opacity, byte fidelity, media-type policy, path-leak absence, unknown
and evicted handles, traversal in four encodings, path separators, absolute
paths, outside-root registration, symlink escape (skipped where the OS forbids
symlink creation), post-registration symlink swap, deletion between register
and read, directories, bounded retention, idempotent registration, repeated
access, result-free progress, cancellation, recovery, inline-data-URL
preservation, the canonical URL shape, header policy, `HEAD`, route status
mapping, and non-owned exceptions propagating rather than being swallowed.

`tests/studio_alpha/run_s07_loopback.py` — four checks over real HTTP:
`result_delivery_same_origin`, `result_delivery_no_path_leak`,
`result_delivery_rejects_paths_and_unknown_ids`, and
`result_delivery_foreign_host_rejected`.

---

## 11. Known limitations

- **Symlink tests skip on this machine.** Windows requires elevation or
  Developer Mode to create symlinks. The tests are written and will run
  wherever symlink creation is permitted. `test_deleted_result_fails_closed_on_read`
  covers the same read-time re-verification path without needing symlinks.
- **`image/svg+xml` is served.** Mitigated by the sandboxed response CSP and by
  `<img>`-context loading. It exists only because the mock produces SVG; drop
  it from the allowlist once no owned result record produces SVG.
- **Retention is in-memory only.** Handles do not survive a process restart.
  Acceptable while results are session-scoped; a persistent gallery would need
  a different design, which is explicitly out of scope.
- **No `Range` support.** Large results are sent whole.
