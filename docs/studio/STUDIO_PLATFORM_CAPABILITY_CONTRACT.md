# Studio Platform Capability Contract — B2

```text
MAC ARCHITECTURE READY — RUNTIME UNVERIFIED
```

How the owned Studio contract describes the active inference environment
without putting platform logic in presentation code.

---

## 1. The fields

`ModelCapability` gains three fields, each defaulting to `UNKNOWN`:

```python
device_type:       DeviceType        = DeviceType.UNKNOWN
dtype_policy:      DtypePolicy       = DtypePolicy.UNKNOWN
attention_backend: AttentionBackend  = AttentionBackend.UNKNOWN
```

| Enum | Members |
|---|---|
| `DeviceType` | `cuda`, `mps`, `cpu`, `xpu`, `directml`, `unknown` |
| `DtypePolicy` | `fp32`, `fp16`, `bf16`, `mixed`, `backend_default`, `unknown` |
| `AttentionBackend` | `pytorch_sdpa`, `xformers`, `sage`, `flash`, `backend_default`, `unknown` |

All three subclass `str`, so they serialize to plain strings and survive a JSON
round trip unchanged.

---

## 2. Semantics

**`device_type` describes the selected backend device, not the host OS.** A
macOS host may report `cpu`; a Linux host may report `cuda`. Studio never
derives one from the other. This is enforced by test, not only by convention:
`test_device_type_is_not_inferred_from_sys_platform` mutates `sys.platform`
across four values and asserts the reported device does not move.

**MPS is an acceleration device, not an installer guarantee.** The install-side
`acceleration_family: mps` from B1 says which build to fetch. `device_type:
mps` says a Metal device is actually in use. An ARM64 Mac can report the first
and not the second — for instance if Torch installed but no MPS device is
reachable. Conflating them would let an install decision masquerade as a
runtime fact.

**`dtype_policy` is the effective adapter policy**, not a request and not a
preference. It is what the adapter will actually do.

**`attention_backend` is the active implementation**, not what is installed.
A build with xformers present that is running SDPA reports `pytorch_sdpa`.

**Per-model limits may differ by device and dtype.** The same checkpoint can
carry different safe maxima on CUDA and MPS. The dimension fields and the
device fields travel together in one contract so the pairing stays coherent —
a consumer never has to guess which device a limit was measured on.

**Studio transports declared facts and does not invent them.** Presentation
serializes whatever the backend reported. It does not fill gaps, normalise
values, or infer a device from the host.

---

## 3. Why `UNKNOWN` is the default

A backend that predates these fields stays contract-valid, and its silence is
recorded as silence. Defaulting to `cpu` would have been convenient and wrong:
"did not say" and "said CPU" are different claims, and only one of them is
evidence.

`test_defaults_are_unknown_not_a_positive_claim` asserts this directly.

Unrecognised values are **rejected, not coerced**. `DeviceType("quantum-tpu")`
raises `ValueError` rather than silently becoming `UNKNOWN` — a typo in a
future adapter must fail loudly instead of quietly reporting ignorance.

---

## 4. What the mock reports

```text
device_type:       cpu
dtype_policy:      fp32
attention_backend: backend_default
```

These are honest, not placeholders. `MockBackend` renders SVG strings with no
tensor library: it genuinely runs on the CPU at fp32 with no attention
implementation of its own. It reports `cpu` because that is true of the mock,
not because of the host it happens to run on.

---

## 5. What a real adapter must do

`ForgeBackendAdapter` translates Forge runtime facts into this contract:

| Field | Source |
|---|---|
| `device_type` | `backend.memory_management.get_torch_device()` / `is_device_mps` / `is_device_cuda` / `is_device_xpu` |
| `dtype_policy` | `should_use_fp16()` / `should_use_bf16()` for the resident model |
| `attention_backend` | `memory_management.xformers_enabled()`, `sage_enabled()`, else SDPA |

The translation lives **in the adapter**. No Torch import, no
`memory_management` call, and no platform branch is added to `forge_studio/`.
Enforced by `OwnedPackagePurityTests`, which walks the AST of every owned
module and asserts no `torch`, `torchvision`, `gradio`, or `numpy` import and
no executable read of `sys.platform` or `platform.system`.

---

## 5.5 Strict validation and the read-only route

Two gaps closed after the delta review.

**Validation.** `ModelCapability.__post_init__` requires the enum member
itself for all three fields. Because the enums subclass `str`, a raw string
like `device_type="cuda"` previously serialized identically and passed
unnoticed. It now raises a stable `TypeError` naming the field:

```text
device_type must be a DeviceType member, got 'cuda'
```

`None`, raw strings, and members of the wrong enum are all rejected. Defaults
stay `UNKNOWN`, the mock stays `cpu`/`fp32`/`backend_default`, and
serialization is byte-identical.

**Reachability.** The contract was previously unreachable: `capability()`
existed with no caller. It is now exposed as a read:

```text
GET /studio/capability?model_id=<id>&operation=txt2img
```

- `operation` defaults to `txt2img` when omitted;
- dispatched through the existing owned route table, so it inherits Host
  validation with no bypass and adds no CORS header;
- parameters are bounded to 200 characters before reaching the application;
- missing, empty, or oversized parameter -> structured **400**;
- unknown model -> `MODEL_NOT_AVAILABLE`; unsupported operation ->
  `OPERATION_NOT_SUPPORTED`;
- the response is exactly `ModelCapability.to_dict()`.

The route is a **pure read**: it never selects or loads a model, and repeated
reads leave residency exactly as found. No Torch, Forge, Gradio, CUDA, or MPS
is touched, and the canonical frontend is unchanged -- it does not consume the
route.

Covered by 20 tests plus a loopback check asserting 200 with the full
document, 400 for a missing parameter, 400 for an unknown model, 403 for a
foreign Host, and residency unchanged across the reads.

---

## 6. Backward compatibility

Existing construction keeps working — every new field has a default, so no
call site changed. The mock's five original capability assertions are
unmodified and still green. Dimension behaviour is untouched:
`test_dimension_fields_are_independent_of_device_fields` pins that.

No UI feature is required. The canonical frontend is untouched and does not
consume these fields; they ride in the owned JSON for a future consumer.

---

## 7. Test coverage

17 tests in `tests/studio_alpha/test_device_capability.py` covering the 11
required cases: backward-compatible construction, serialization,
deserialization, mock truthfulness, future-safe unknown handling, invalid-value
rejection, presentation transport, device-not-inferred-from-platform, differing
CUDA and MPS adapters on one host, dimension independence, and unaffected
catalog and residency behaviour.

`test_two_adapters_can_report_different_devices` builds two `MockBackend`
subclasses that report CUDA/fp16/xformers and MPS/bf16/SDPA with different
maximum dimensions — proving the contract can express real device divergence
without importing Torch or needing either device.

---

## 8. What is still unverified

- **No real device was queried.** Every value in the test suite is contract
  data, not a measurement.
- **No MPS device was initialised.**
- **The adapter translation in §5 is specified, not implemented.**
  `ForgeBackendAdapter` does not exist.
