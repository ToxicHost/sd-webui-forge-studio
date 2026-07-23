# Performance Program

## Rule zero

Measure before optimizing. Performance claims require paired runs in the same server process.

The existing Studio performance protocol already warns that cross-session drift can exceed the effects under investigation. Preserve that rule.

## Metric model

Use `time.perf_counter()` for elapsed durations.

Record:

```text
request_total_wall
preflight
asset_resolution
model_transition
base_setup
base_sampling
base_decode
hires_upscale
hires_model_transition
hires_setup
hires_sampling
hires_decode
ad_detector_load
ad_detection
ad_model_transition
ad_inpaint
postprocess
final_encode
response_serialization
preview_gpu_work_sum
preview_cpu_work_sum
```

Preview sums overlap request wall time and must be labeled non-additive.

## Timer correctness acceptance

- External click/request-to-response wall time and reported `request_total_wall` agree within `max(0.25 s, 2%)` for normal runs.
- Each `process_images()` call has its own core span.
- Forge's `shared.state.time_start` is reset immediately before the corresponding `process_images()` call if Neo's console timer depends on it.
- Concurrent phase durations are never arithmetically summed into wall time.
- A trace export shows nesting and overlap.

## Benchmark classes

### Cold start

- application just launched;
- model not yet loaded;
- report model file location and storage type.

### Warm same-model

- same process;
- same model resident;
- fixed request repeated.

### Model swap

```text
A warm → B → A
```

Report each transition separately.

### Hires transition

- same checkpoint;
- alternate checkpoint;
- restoration immediately;
- restoration when required;
- restoration after response if implemented.

### ADetailer

- first detector use;
- second detector use;
- one detection;
- multiple detections;
- same and different ADetailer slots.

### Preview

- disabled;
- tab hidden;
- Quality;
- Fast experimental;
- stock UI control where comparable.

## Standard environment record

- distribution version;
- upstream commit;
- Studio commit/patch set;
- Python;
- PyTorch;
- CUDA runtime;
- NVIDIA driver;
- GPU and VRAM;
- system RAM;
- storage device;
- launch arguments;
- model architecture, file size, dtype;
- VAE/text encoder;
- sampler/scheduler;
- dimensions/steps/batch;
- Hires settings;
- ADetailer detector;
- preview mode.

## Initial optimization experiments

### Experiment A — Detector cache

Hypothesis: repeated ADetailer runs avoid YOLO construction/loading.

Pass:

- second run logs cache hit;
- same detections within tolerance;
- no stale detector after file replacement;
- low-VRAM eviction works;
- warm transition time improves.

### Experiment B — VAE/file-list caching

Hypothesis: generation preflight avoids recursive scans when files did not change.

Pass:

- no scan on ordinary generation;
- manual refresh works;
- missing requested asset triggers one fallback refresh;
- startup and explicit refresh remain correct.

### Experiment C — Hires restoration policy

Hypothesis: deferring base restoration reduces result latency without corrupting next request.

Pass:

- result returns before unnecessary reload;
- UI reports active versus selected model correctly;
- next request triggers required transition exactly once;
- interruption/error restores valid state.

### Experiment D — Cache-cleanup pressure policy

High risk. Do not begin before truthful metrics and VRAM telemetry exist.

Pass:

- no OOM increase across support matrix;
- no stale allocation growth over soak;
- no output difference;
- fewer forced synchronizations/allocator purges;
- fallback to Compatible mode works.

## Performance claim format

```text
Claim:
Controlled variable:
Server session:
Run order:
A results:
B results:
Environment:
Trace IDs:
Raw logs:
Output correctness result:
Conclusion:
Known uncertainty:
```

Never report only the fastest run. Report all paired repetitions and median.
