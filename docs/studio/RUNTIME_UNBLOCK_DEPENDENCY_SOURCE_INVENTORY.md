# Runtime Unblock — Dependency Source Inventory

Complete workspace-local inventory of every file that establishes the Pillow /
Gradio / pillow-heif constraint set, the Python version expectation, installer
behaviour, and launch-time dependency assumptions.

**No network access, package index query, or metadata download was performed.**
Every value below was read from a file inside `Studio-Standalone\`.

---

## 1. Authoritative install inputs

These are the files that actually determine what gets installed.

| Path | Line | Exact line | Value | Classification |
|---|---|---|---|---|
| `app/requirements.txt` | 2 | `Pillow==12.3.0` | Pillow pinned to 12.3.0 | **authoritative install input** |
| `app/requirements.txt` | 22 | `pillow-heif==1.4.0` | pillow-heif pinned to 1.4.0 | **authoritative install input** |
| `app/requirements.txt` | 23 | `pillow-jxl-plugin==1.3.8` | Pillow plugin pinned | **authoritative install input** |
| `app/requirements.txt` | 4 | `audioop-lts==0.2.2;python_version>="3.13"` | confirms 3.13 is the intended target | **authoritative install input** |
| `app/modules/launch_utils.py` | 295 | `gradio_package = os.environ.get("GRADIO_PACKAGE", "gradio==4.40.0 gradio_rangeslider==0.0.8")` | **Gradio 4.40.0** | **authoritative install input** |

**The Gradio pin is not in `requirements.txt`.** It is a hardcoded default
inside the launcher, overridable by the `GRADIO_PACKAGE` environment variable.
Any owner-authorized Gradio change must target `launch_utils.py:295` or set
`GRADIO_PACKAGE`; editing `requirements.txt` alone would not move Gradio.

---

## 2. Installer behaviour

| Path | Line | Behaviour | Classification |
|---|---|---|---|
| `app/modules/launch_utils.py` | 423-424 | `if not is_installed("gradio"): run_pip(f"install {gradio_package}", "gradio")` | **authoritative install input** |
| `app/modules/launch_utils.py` | 426-431 | resolves `REQS_FILE` then `if not requirements_met(...): run_pip(f'install -r "{requirements_file}"')` | **authoritative install input** |
| `app/modules/launch_utils.py` | 448-450 | second `requirements_met` enforcement pass after extension setup | **authoritative install input** |
| `app/modules/launch_utils.py` | 253-263 | `requirements_met()` does a *simple* parse of the requirements file | **authoritative install input** |
| `app/modules/launch_utils.py` | 296 | `requirements_file = os.environ.get("REQS_FILE", "requirements.txt")` | **authoritative install input** |

Two consequences that matter for planning:

1. **Gradio is install-once.** The `is_installed("gradio")` guard means an
   already-present Gradio 4.40.0 is never upgraded by launch, regardless of
   what `gradio_package` says. Changing the pin alone will not move an existing
   installation — an explicit uninstall or upgrade is required, and both are
   currently prohibited.
2. **`requirements_met` is a simple parser, not a resolver.** It checks the
   requirements file only. It has no visibility into installed-distribution
   `Requires-Dist` metadata, which is precisely where the conflict lives. The
   conflict is therefore invisible to launch-time enforcement and will not be
   reported by it.

---

## 3. Python version expectations

| Path | Line | Exact content | Value | Classification |
|---|---|---|---|---|
| `app/modules/launch_utils.py` | 35, 45-47 | `check_python_version()` — "This program is tested with 3.13.12 Python" | expects 3.13.x, tested at 3.13.12 | **authoritative install input** |
| `app/venv/pyvenv.cfg` | — | `version = 3.13.5` | interpreter is 3.13.5 | **generated** |
| `app/requirements.txt` | 4 | `python_version>="3.13"` marker | 3.13 target confirmed | **authoritative install input** |

The workspace interpreter reports:

```text
3.13.5 | packaged by Anaconda, Inc. | (main, Jun 12 2025, 16:37:03) [MSC v.1929 64 bit (AMD64)]
```

`check_python_version` warns rather than hard-fails on a 3.13.x mismatch, so
3.13.5 versus the tested 3.13.12 is a soft difference. It is recorded because
it narrows any candidate set: a Gradio release requiring `>=3.14` or `<3.13` is
out of scope regardless of its Pillow constraint.

No external Python installation was inspected. The workspace venv was not
followed to its base runtime.

---

## 4. Transitive metadata — the complete mandatory Pillow constraint set

Read from `Requires-Dist` in `app/venv/Lib/site-packages/*.dist-info/METADATA`.
Extra-gated constraints are excluded because no extra is installed.

| Owner | Requirement | Classification |
|---|---|---|
| `gradio 4.40.0` | `pillow<11.0,>=8.0` | **transitive metadata** |
| `pillow_heif 1.4.0` | `pillow>=11.1.0` | **transitive metadata** |
| `scikit_image 0.25.2` | `pillow>=10.1` | **transitive metadata** |
| `matplotlib 3.11.1` | `pillow>=9` | **transitive metadata** |
| `imageio 2.37.4` | `pillow>=8.3.2` | **transitive metadata** |
| `torchvision 0.26.0+cu130` | `pillow!=8.3.*,>=5.3.0` | **transitive metadata** |
| `diffusers 0.37.1` | `pillow` (unbounded) | **transitive metadata** |
| `facexlib 0.3.0` | `pillow` (unbounded) | **transitive metadata** |
| `pillow-jxl-plugin 1.3.8` | `pillow` (unbounded) | **transitive metadata** |

Extra-gated only, therefore **not** binding: `contourpy` (`test`),
`huggingface_hub` (`testing`/`all`/`dev`), `networkx` (`doc`), `transformers`
(`vision`/`torch-vision`/`all`/`dev*`, `Pillow<=15.0,>=10.0.1`).

### Intersection arithmetic

With Gradio excluded, the mandatory lower bounds resolve to `>=11.1.0`
(pillow-heif dominates) with no upper bound. Installed Pillow 12.3.0 satisfies
that, and satisfies `requirements.txt`.

Adding `gradio 4.40.0`'s `pillow<11.0` makes the intersection **empty**.
Gradio 4.40.0 is the single binding conflict; nothing else in the environment
disagrees with Pillow 12.3.0.

The preflight tool independently derived exactly two minimal unsatisfiable
cores, both containing Gradio:

```text
[gradio:4.40.0:pillow, pillow-heif:1.4.0:pillow]
[gradio:4.40.0:pillow, repository:Pillow:requirements.txt:2]
```

---

## 5. Constraints bounding any Gradio candidate set

These are workspace-local facts, not researched metadata. They bound the
candidate set without naming a version.

| Source | Constraint | Classification |
|---|---|---|
| `app/venv/Lib/site-packages/gradio_rangeslider-0.0.8.dist-info/METADATA` | `Requires-Dist: gradio<6.0,>=4.0` | **transitive metadata** |
| `app/venv/Lib/site-packages/gradio-4.40.0.dist-info/METADATA` | `Requires-Dist: gradio-client==1.2.0` | **transitive metadata** |
| `app/venv/Lib/site-packages/gradio_client-1.2.0.dist-info/METADATA` | installed at 1.2.0, `Requires-Python: >=3.8` | **generated** |

`gradio_rangeslider 0.0.8` accepts any Gradio `>=4.0,<6.0`, so it does not by
itself block a 4.x or 5.x move. `gradio-client` is pinned exactly by Gradio and
will move with it.

### Retained Gradio coupling — the real upper bound on how far Gradio can move

63 tracked files import Gradio. Component-level usage is conventional
(`gr.Slider`, `gr.Blocks`, `gr.update`, `gr.skip`, …). The blocking risk is
**private internal API monkeypatching**:

| Path | Line | Patched internal | Classification |
|---|---|---|---|
| `app/modules/gradio_extensions.py` | 89 | `gradio.blocks.Block.get_config` | **authoritative install input** (constrains version) |
| `app/modules/gradio_extensions.py` | 90 | `gradio.blocks.BlockContext.__init__` | **authoritative install input** |
| `app/modules/gradio_extensions.py` | 91 | `gradio.blocks.Blocks.get_config_file` | **authoritative install input** |
| `app/modules/ui_tempdir.py` | 122-123 | `gradio.processing_utils.save_pil_to_cache`, `async_move_files_to_cache` | **authoritative install input** |
| `app/modules/ui_tempdir.py` | 70-71 | imports `gradio.data_classes.GradioModel/GradioRootModel`, `gradio.utils.get_upload_folder/is_in_or_equal/is_static_file` | **authoritative install input** |
| `app/modules_forge/patch_basic.py` | 95 | `gradio.networking.url_ok` | **authoritative install input** |
| `app/modules/ui.py` | 47-48 | `gradio.utils.version_check`, `get_local_ip_address` | **authoritative install input** |
| `app/modules_forge/forge_canvas/canvas.py` | 41 | `gradio.context.Context` | **authoritative install input** |
| `app/modules_forge/main_entry.py` | 6 | `gradio.context.Context` | **authoritative install input** |

None of these are public, versioned API. Each is a hard compatibility gate on
any Gradio version change, and each must be verified against the candidate
before a mutation is authorized. `ui_tempdir.py` is doubly relevant: it patches
Gradio's *Pillow* handling, so it sits exactly on the Pillow/Gradio seam.

---

## 6. Documentation-only and historical references

| Path | Line | Content | Classification |
|---|---|---|---|
| `app/PROJECT_STATE.md` | 86 | "The Gradio 4.40.0 / Pillow 12.3.0 dependency conflict remains unresolved." | **documentation only** |
| `app/README.md` | 24 | narrative reference to Gradio 4.40.0 | **documentation only** |
| `app/README.md` | 250 | `- [X] Update Pillow` checklist entry | **historical** |
| `app/docs/evidence/PHASE_0_DEPENDENCY_CONFLICT_ANALYSIS.md` | — | prior conflict analysis | **documentation only** |
| `app/extensions-builtin/forge_legacy_preprocessors/requirements.txt` | 1-10 | `addict`, `ftfy`, `fvcore`, `mediapipe`, `onnx`, `onnxruntime`, `opencv-python`, `svglib`, `timm`, `yapf` | **authoritative install input** (no Pillow or Gradio constraint) |
| `app/pyproject.toml` | — | no `dependencies`, `pillow`, or `gradio` entry | **not a dependency source** |

Absent from the workspace: no constraints file, no lock file, no requirements
override, no `setup.py`/`setup.cfg` for the application itself. The two
`setup.py` and `environment.yaml` files under
`extensions-builtin/forge_legacy_preprocessors/annotator/` belong to vendored
third-party annotators, declare no Pillow or Gradio constraint, and are
classified **historical**.

---

## 7. What is still unknown

Everything below requires external metadata and is therefore **unknown**:

- which Gradio versions declare a Pillow specifier admitting `>=11.1.0`;
- which of those support Python 3.13;
- which of those retain `gradio.blocks.Block.get_config`,
  `gradio.blocks.BlockContext.__init__`, `gradio.blocks.Blocks.get_config_file`,
  `gradio.processing_utils.save_pil_to_cache`,
  `gradio.processing_utils.async_move_files_to_cache`,
  `gradio.data_classes.GradioModel`, `gradio.data_classes.GradioRootModel`,
  `gradio.utils.get_upload_folder`, `gradio.utils.is_in_or_equal`,
  `gradio.utils.is_static_file`, `gradio.networking.url_ok`, and
  `gradio.context.Context` in compatible form;
- the matching `gradio-client` version for any candidate.

**No replacement Gradio version is selected, recommended, or implied by this
document.** Section 5 bounds the search space using workspace-local facts only;
it does not name a candidate. Naming one requires the metadata research
authorized by
[`OWNER_AUTHORIZATION_TEMPLATE.md`](../../../Evidence/studio-runtime-unblock-preflight/OWNER_AUTHORIZATION_TEMPLATE.md).

---

## 8. Alternatives the owner may prefer to a Gradio move

Recorded for completeness, not recommended, and none authorized:

1. **Move Gradio** — highest compatibility risk (§5 internal patches).
2. **Downgrade Pillow below 11.0** — breaks `pillow-heif 1.4.0`
   (`>=11.1.0`) and contradicts `requirements.txt:2`. Would need pillow-heif
   downgraded too, and `scikit-image>=10.1` still applies.
3. **Drop or replace `pillow-heif`** — removes the `>=11.1.0` floor, letting
   Pillow fall below 11.0 to satisfy Gradio 4.40.0. Requires an owner decision
   about HEIF support and a `requirements.txt` change.
4. **Accept the violated constraint** — Pillow 12.3.0 is already installed and
   the mock Studio runtime passes 63 tests and 21 loopback checks against it.
   The violation is a metadata assertion, not an observed runtime failure. What
   is *unverified* is whether Gradio 4.40.0's actual Pillow usage breaks at
   12.3.0 — and `ui_tempdir.py` already replaces part of that surface.

Option 4 is the only path that requires no package mutation, and therefore the
only one reachable without a network authorization. It would still require
owner acceptance of a knowingly unsatisfied constraint plus a controlled launch
authorization to test.
