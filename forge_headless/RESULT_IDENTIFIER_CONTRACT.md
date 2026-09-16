# Result identifier contract

One value names a produced result. It is minted once per job, travels as
`request_id`, and becomes the stem of the file the result is saved as. This
document says exactly what that value guarantees, because a wrong assumption
about it cost the owner a generation.

## What it is

```text
headless-<uuid4 hex>

example: headless-9c2a73f5f6f842cdb919f63994365365
```

Minted by `forge_headless.studio_generation.default_result_identifier`.

## The path it travels

```text
default_result_identifier()          forge_headless/studio_generation.py
  -> job_id                          HeadlessGenerationSession.submit
  -> request_id                      translate_request(..., request_id=job_id)
  -> FirstImageRequest.request_id
  -> safe_result_name(request_id)    forge_headless/live_generation_port.py
  -> <stem>.png in the result root   save_result_exclusively
  -> GenerationOutcome.request_id
  -> result metadata "request_id"    HeadlessGenerationSession.result
  -> opaque browser handle           forge_studio/result_delivery.py
```

The browser handle is a **separate** opaque token
(`studio-result/<32 hex>.png`), minted by the result registry. It is not this
identifier, and no filesystem path ever reaches a response.

## Guaranteed

```text
uniquely identifies one produced result
the result filename stem is exactly this identifier
unique across processes, restarts and concurrent instances
opaque, and safe as a filename with no escaping
stable for the lifetime of the job that owns it
```

## NOT guaranteed — do not assume

```text
NOT sequential            there is no "next" or "previous" result
NOT numeric               parsing it as an integer will fail
NOT ordered               it says nothing about when a result was produced
NOT the seed              the seed repeats by design; this never does
NOT stable across runs    a repeated generation gets a new identifier
NOT a count of results    it is not derived from the result root
```

If something needs ordering, read the filesystem or the job table. Do not
recover it from the identifier.

## Why uuid4

The identifier used to be `itertools.count(1)` held on the session instance,
formatted `headless-%06d`. The counter lived in memory, so **every process
start reissued `headless-000001`** — over whatever was already in the result
root. The writer was a bare `image.save(path)`, which overwrites.

During live verification a restarted Studio destroyed the canonical anchor
result that way. It survived only because an unrelated probe had copied it
minutes earlier.

uuid4 is restart-, process- and instance-safe with no result-root scan, no
dependence on wall-clock monotonicity, and no relation to the seed.

Rejected alternatives, each collision-prone under restart or concurrency:

| Scheme | Fails because |
|---|---|
| process-local counter | the defect itself |
| seed | seeds repeat by design, and are owner-chosen |
| seconds-resolution timestamp | two jobs in one second collide |
| count of result-root entries | races, and is wrong once anything is archived |

## Placement is independently guarded

A uuid4 makes collision vanishingly unlikely; it does not make it impossible,
and "unlikely" is not a durability guarantee. `save_result_exclusively` claims
the name with `O_CREAT | O_EXCL`, so the filesystem decides atomically. An
`exists()` check followed by a save cannot: two processes can both pass the
check and both write.

On collision the write **fails** with

```text
GENERATION_RESULT_IDENTIFIER_COLLISION  ->  RESULT_PUBLICATION_FAILED
```

rather than retrying under a different name. Retrying would change the
identifier after the job was created, and `request_id` in the metadata would
then no longer name the file it describes. The message carries no path.

A failed encode releases the reservation, so a half-written file cannot
occupy an identifier for a result that was never produced.

## Backward compatibility

Results written under the old scheme (`headless-000001.png`, ...) remain
readable and servable with no migration. Nothing parses the numeric form, so
both generations coexist in one result root. Do not rename existing results to
normalise them.

## Test seam

`HeadlessGenerationSession(identifiers=...)` still accepts an injected
callable, so tests can pin a deterministic identifier. An injected identifier
that collides fails exactly as a production one would — safely, without
overwriting.

Pinned by `tests/studio_alpha/test_result_identifier_persistence.py`.
