"""Build the feature-parity review bundle.

Three parts, assembled on different rules:

  studio/     COMPLETE. The thing under review.
  extension/  COMPLETE. The behavioural oracle, and small enough to carry whole.
  neo/        A SLICE, because the tree is 83 MB and most of it is weights,
              HuggingFace configs and third-party extensions.

The slice is dependency-aware and is seeded from BOTH directions, deliberately:
from Neo's own working entry points (webui.py, launch.py, the API, the UI
wiring) and from the parity areas under review. Seeding only from what
Standalone already calls would produce a slice that can only confirm what
Standalone already does -- which is the opposite of a parity review.

From those seeds it walks `import` edges transitively through the Neo tree and
takes everything it reaches. Every edge that LEAVES the slice is recorded
rather than dropped, so a reviewer can tell a deliberate boundary from an
oversight.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
APP = WS / "app"
EXT = WS / "Reference" / "Forge-Studio-main"
OUT = WS / "Evidence" / "studio-feature-parity-bundle"

#: Neo's OWN entry points. Where the program starts if Studio never existed.
NEO_ENTRY_POINTS = [
    "webui.py", "launch.py", "download_configs.py",
    "modules/launch_utils.py", "modules/cmd_args.py",
    "modules/initialize.py", "modules/initialize_util.py",
    "modules/paths.py", "modules/paths_internal.py",
    "modules/shared.py", "modules/shared_init.py", "modules/shared_options.py",
    "modules/shared_cmd_options.py", "modules/shared_state.py",
    "modules/api/api.py", "modules/api/models.py",
    "modules/ui.py",
]

#: The parity areas named for review. Seeded even where Standalone does not
#: reach them, because "Neo can and Studio cannot" is exactly the finding.
NEO_PARITY_SEEDS = [
    # generation
    "modules/processing.py",
    "modules/sd_samplers.py", "modules/sd_samplers_common.py",
    "modules/sd_samplers_kdiffusion.py", "modules/sd_samplers_timesteps.py",
    "modules/sd_samplers_cfg_denoiser.py", "modules/sd_samplers_extra.py",
    "modules/sd_schedulers.py", "modules/prompt_parser.py",
    "modules/rng.py", "modules/rng_philox.py",
    # Img2Img / Inpaint / masking
    "modules/img2img.py", "modules/masking.py", "modules/txt2img.py",
    # Hires and upscaling
    "modules/upscaler.py", "modules/upscaler_utils.py", "modules/esrgan_model.py",
    "modules/resolution.py",
    # model discovery, loading, switching
    "modules/sd_models.py", "modules/sd_vae.py",
    "modules/sd_unet.py", "modules/modelloader.py",
    "modules/devices.py", "modules/memmon.py",
    "backend/args.py", "backend/loader.py", "backend/memory_management.py",
    "backend/attention.py", "backend/state_dict.py", "backend/utils.py",
    "modules_forge/main_entry.py", "modules_forge/initialization.py",
    "modules_forge/packages/huggingface_guess/model_list.py",
    # job lifecycle: progress, cancel, queue
    "modules/progress.py", "modules/call_queue.py",
    # script/extension hooks -- the mechanism ADetailer-style features use
    "modules/scripts.py", "modules/script_callbacks.py", "modules/script_loading.py",
    "modules/extensions.py",
    # saving, metadata, output
    "modules/images.py", "modules/infotext_utils.py",
    "modules/styles.py",
    # filesystem / model directories
    "modules/ui_common.py", "modules/util.py",
]

#: Whole subtrees taken without an import walk: small, cohesive, and load-bearing
#: for a parity judgement.
NEO_WHOLE_TREES = [
    "backend/diffusion_engine", "backend/nn", "backend/patcher",
    "backend/text_processing", "backend/sampling", "backend/operations",
    "modules_forge/packages/huggingface_guess",
    # NAMESPACE PACKAGES -- no `__init__.py`, so the import walk cannot resolve
    # `import backend.misc` to a file and recorded them as unresolved edges.
    # Real code, reached by real imports; taken whole rather than left out.
    "modules/uni_pc",
    "backend/misc",
    "backend/nn/cnets",
    "backend/nn/llm",
    "modules_forge/packages/comfy",
    "extensions-builtin/soft-inpainting",
    "extensions-builtin/sd_forge_lora",
    "extensions-builtin/forge_preprocessor_inpaint",
    # A DIRECTORY, and it was seeded as `modules/processing_scripts.py`. No
    # such file exists, so the seed resolved to nothing and the whole package
    # -- seed.py, sampler.py, refiner.py, rescale_cfg.py, mahiro.py,
    # comments.py -- was silently absent from a bundle that named it as a
    # parity area. Found by the seed gate on its first run.
    "modules/processing_scripts",
    "docker",
]

#: Named as parity seeds once, and genuinely not present in THIS Neo revision.
#: Recorded rather than deleted: "we looked and it is not there" and "nobody
#: thought of it" are different findings, and the gate must not be silenced by
#: quietly dropping a name.
NEO_SEEDS_ABSENT_UPSTREAM = {
    "modules/sd_models_config.py": "not in forge-classic@neo; config resolution moved into backend/loader.py",
    "modules/sd_hijack.py": "not in forge-classic@neo; Forge replaced the A1111 hijack layer with backend/patcher",
    "modules/infotext_versions.py": "not in forge-classic@neo",
}

#: Named, with the reason, rather than silently absent.
NEO_INTENTIONAL_OMISSIONS = {
    "backend/huggingface": "65 MB of vendored model configs, not code under review",
    "extensions-builtin/sd_forge_controlnet": "ControlNet is post-Gate-3 roadmap; large, and no Standalone counterpart to compare",
    "extensions-builtin/sd_forge_ipadapter": "not in any named parity area",
    "extensions-builtin/sd_forge_controlllite": "not in any named parity area",
    "extensions-builtin/sd_forge_multidiffusion": "not in any named parity area",
    "extensions-builtin/forge_legacy_preprocessors": "ControlNet preprocessors; follows the ControlNet omission",
    "extensions-builtin/forge_preprocessor_reference": "ControlNet preprocessors; follows the ControlNet omission",
    "extensions-builtin/forge_preprocessor_tile": "ControlNet preprocessors; follows the ControlNet omission",
    "extensions-builtin/mobile": "UI-only responsive shim",
    "extensions-builtin/extra-options-section": "UI-only Gradio panel",
    "extensions-builtin/prompt-bracket-checker": "UI-only Gradio panel",
    "extensions-builtin/sd_forge_compile": "torch.compile toggle; not a named parity area",
    "extensions-builtin/sd_forge_neveroom": "VRAM experiment; not a named parity area",
    "extensions-builtin/sd_forge_pid": "sampler experiment; not a named parity area",
    "extensions-builtin/sd_forge_radial": "sampler experiment; not a named parity area",
    "extensions-builtin/sd_forge_spectrum": "sampler experiment; not a named parity area",
    "extensions-builtin/sd_forge_image_stitch": "UI utility; not a named parity area",
    "models": "model weights",
    "outputs": "generated images",
    "venv": "the interpreter and third-party packages",
    "localizations": "translation JSON, no behaviour",
    "repositories": "vendored upstream checkouts, if present",
}

NEO_ROOTS = ("backend", "modules", "modules_forge", "extensions-builtin")

#: The governing documents. Section 8 recorded their absence as a limitation of
#: the bundle -- a reviewer cannot judge parity against a plan they were not
#: given. They live outside the git root, so they are copied in by name.
GOVERNING_DOCUMENTS = [
    "STUDIO_1_0_FULL_SCOPE_EXECUTION_HANDOFF(2).md",
    "CLAUDE_CODE_STUDIO_STANDALONE_MASTER_EXECUTION_BOOK.md",
    "STUDIO_STANDALONE_FULL_SCOPE_PROJECT_BOOK.md",
    "SESSION_BRIDGE_2026-08-17.md",
]


def _looks_like_path(value: str) -> bool:
    """A drive-letter or POSIX absolute path, however it is spelled."""

    if len(value) >= 3 and value[1] == ":" and value[2] in "\\/":
        return True
    return value.startswith("/") and len(value) > 1


def scrub_paths(value):
    """Replace every absolute path in a decoded document, at any depth."""

    if isinstance(value, dict):
        return {key: scrub_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_paths(item) for item in value]
    if isinstance(value, str) and _looks_like_path(value):
        return "<redacted-absolute-path>"
    return value


def gate_problems(copied: list[str], missing_seeds: list[str],
                  documents: list[str], config_record: dict) -> list[str]:
    """What would make this bundle untrustworthy, as a list of reasons.

    Separated from `main` so it can be tested without building 57 MB, and
    because these are the assertions the artefact's credibility rests on. The
    duplicate count survived a release because it was PRINTED and not enforced;
    a number nobody acts on is decoration.
    """

    problems = []
    if len(copied) != len(set(copied)):
        duplicated = sorted({rel for rel in copied if copied.count(rel) > 1})
        problems.append(
            f"{len(copied)} included paths but {len(set(copied))} unique; "
            f"{len(duplicated)} duplicated, e.g. {duplicated[:3]}")
    if missing_seeds:
        problems.append(
            f"{len(missing_seeds)} declared seed(s) do not exist: "
            f"{missing_seeds[:5]} -- a seed that is not there means the slice "
            f"silently omits an area the bundle claims to cover")
    if not documents:
        problems.append("no governing document was included; a reviewer "
                        "cannot judge parity against a plan they lack")
    if config_record.get("redacted") is False:
        problems.append("studio-config.json was not redacted")
    return problems


def module_to_paths(module: str) -> list[Path]:
    """Candidate files for a dotted module name inside the Neo tree."""

    rel = module.replace(".", "/")
    candidates = [APP / f"{rel}.py", APP / rel / "__init__.py"]
    # A namespace package has no `__init__.py`. `import backend.misc` is still
    # a real edge into real code, so the directory counts as resolved and its
    # modules are picked up by the whole-tree pass.
    directory = APP / rel
    if directory.is_dir():
        candidates.extend(sorted(directory.glob("*.py")))
    return candidates


def imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import -- resolve against this file's package.
                package = path.relative_to(APP).parent
                for _ in range(node.level - 1):
                    package = package.parent
                base = str(package).replace("\\", "/").replace("/", ".")
                found.add(f"{base}.{node.module}" if node.module else base)
            elif node.module:
                found.add(node.module)
    return found


def in_neo(module: str) -> bool:
    return module.split(".")[0] in NEO_ROOTS


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    manifest: dict[str, object] = {}
    copied: list[str] = []
    seen: set[str] = set()
    total = [0]

    def take(src: Path, rel: str) -> bool:
        """Copy one file ONCE.

        The `seen` set is the whole fix for the count defect. `take` used to
        append and add bytes unconditionally, and the whole-tree pass re-takes
        files the import walk already reached -- so 80 paths were recorded two
        or three times, `included_paths` carried 923 entries for 837 real
        files, and `total_bytes` was inflated by the overlap. The ZIP was
        always correct, because a second copy just overwrites the first; only
        the BOOKKEEPING was wrong, which is the kind of defect that makes a
        manifest less trustworthy than no manifest at all.
        """

        if not src.is_file() or rel in seen:
            return False
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        seen.add(rel)
        copied.append(rel)
        total[0] += src.stat().st_size
        return True

    def take_redacted(src: Path, rel: str) -> dict:
        """Copy a configuration with every absolute path removed.

        `studio-config.json` was shipped RAW. It carries the owner's home
        directory, the layout of their private model library and the names of
        their model files -- in a bundle whose entire purpose is to be handed
        to someone else. Studio's own rule is that no ordinary output leaks an
        absolute model, result, state or owner path, and a review bundle is not
        an exception to it.

        The shape is what a reviewer needs, so the shape is kept and the values
        are replaced. The original's digest is recorded so the artefact can
        still be tied to one exact configuration.
        """

        if not src.is_file():
            return {"present": False}
        raw = src.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"present": True, "sha256": digest, "redacted": False,
                    "note": "unparseable; deliberately NOT included"}
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(scrub_paths(document), indent=2) + chr(10),
                        encoding="utf-8")
        seen.add(rel)
        copied.append(rel)
        total[0] += dest.stat().st_size
        return {"present": True, "sha256_of_original": digest,
                "redacted": True, "included_as": rel}

    def take_tree(root: Path, prefix: str, *, skip_suffixes=(".pyc",)) -> int:
        count = 0
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix in skip_suffixes:
                continue
            if "__pycache__" in item.parts or ".git" in item.parts:
                continue
            if take(item, f"{prefix}/{item.relative_to(root).as_posix()}"):
                count += 1
        return count

    # -- 1. Studio, complete -------------------------------------------------
    studio_skip = {"venv", "__pycache__", "outputs", "models", "repositories",
                   ".git", "extensions-builtin", "backend", "modules",
                   "modules_forge", "html", "javascript", "localizations"}
    studio_count = 0
    for item in APP.rglob("*"):
        if not item.is_file() or item.suffix == ".pyc":
            continue
        parts = set(item.relative_to(APP).parts)
        if parts & studio_skip:
            continue
        if take(item, f"studio/app/{item.relative_to(APP).as_posix()}"):
            studio_count += 1
    for name in ("Start-Studio.bat", "start_studio.py"):
        take(WS / name, f"studio/{name}")
    # NOT `take`. See `take_redacted`: this file carried the owner's home
    # directory, private model roots and model filenames into a bundle built
    # to be handed to a reviewer.
    config_record = take_redacted(WS / "studio-config.json",
                                  "studio/studio-config.redacted.json")

    # -- 1b. The plan the bundle is reviewed AGAINST ------------------------
    documents = []
    for name in GOVERNING_DOCUMENTS:
        if take(WS / "Reference" / name, f"documents/{name}"):
            documents.append(name)

    # -- 2. Extension, complete ---------------------------------------------
    ext_root = EXT / "Forge-Studio-main" if (EXT / "Forge-Studio-main").is_dir() else EXT
    ext_count = take_tree(ext_root, "extension") if ext_root.is_dir() else 0

    # -- 3. Neo, dependency-aware slice -------------------------------------
    seeds = [APP / rel for rel in NEO_ENTRY_POINTS + NEO_PARITY_SEEDS]
    frontier = [p for p in seeds if p.is_file()]
    missing_seeds = [str(p.relative_to(APP)) for p in seeds if not p.is_file()]

    visited: set[Path] = set()
    external_edges: dict[str, set[str]] = {}
    unresolved_edges: dict[str, set[str]] = {}

    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        for module in imports_of(current):
            origin = current.relative_to(APP).as_posix()
            if not in_neo(module):
                external_edges.setdefault(module.split(".")[0], set()).add(origin)
                continue
            candidates = [c for c in module_to_paths(module) if c.is_file()]
            if not candidates:
                unresolved_edges.setdefault(module, set()).add(origin)
                continue
            for candidate in candidates:
                if candidate not in visited:
                    frontier.append(candidate)

    neo_count = 0
    for path in sorted(visited):
        if take(path, f"neo/{path.relative_to(APP).as_posix()}"):
            neo_count += 1
    for tree in NEO_WHOLE_TREES:
        root = APP / tree
        if root.is_dir():
            neo_count += take_tree(root, f"neo/{tree}")

    # Configuration defaults that are data, not imports.
    for extra in ("webui-user.bat", "webui-user.sh", "webui.bat", "webui.sh",
                  "requirements.txt", "UPSTREAM_BASE"):
        take(APP / extra, f"neo/{extra}")

    # -- 4. Manifest ---------------------------------------------------------
    upstream = (APP / "UPSTREAM_BASE").read_text(encoding="utf-8").strip() \
        if (APP / "UPSTREAM_BASE").is_file() else "unknown"
    studio_head = subprocess.run(
        ["git", "-C", str(APP), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()

    manifest = {
        "studio_head": studio_head,
        "neo_upstream": dict(
            line.split("=", 1) for line in upstream.splitlines() if "=" in line),
        "counts": {
            "studio_files": studio_count,
            "extension_files": ext_count,
            "neo_files": neo_count,
            "total_bytes": total[0],
        },
        "neo_slice_seeds": {
            "entry_points": NEO_ENTRY_POINTS,
            "parity_areas": NEO_PARITY_SEEDS,
            "whole_trees": NEO_WHOLE_TREES,
            "seeds_not_found": missing_seeds,
            "seeds_absent_upstream": NEO_SEEDS_ABSENT_UPSTREAM,
        },
        "neo_intentional_omissions": NEO_INTENTIONAL_OMISSIONS,
        "dependency_edges_leaving_the_slice": {
            "third_party_or_stdlib": {
                k: sorted(v)[:6] for k, v in sorted(external_edges.items())},
            "neo_modules_referenced_but_not_resolved": {
                k: sorted(v) for k, v in sorted(unresolved_edges.items())},
        },
        "included_paths": sorted(copied),
    }
    manifest["counts"]["included_paths"] = len(copied)
    manifest["counts"]["unique_paths"] = len(set(copied))
    manifest["governing_documents"] = documents
    manifest["studio_config"] = config_record
    manifest["extension_identity"] = extension_identity(ext_root)

    (OUT / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")

    archive = OUT.with_suffix(".zip")
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(OUT.rglob("*")):
            if item.is_file():
                bundle.write(item, item.relative_to(OUT).as_posix())
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()

    print(f"studio    : {studio_count} files")
    print(f"extension : {ext_count} files")
    print(f"neo slice : {neo_count} files (from {len(visited)} walked)")
    print(f"documents : {len(documents)}")
    print(f"included / unique paths    : {len(copied)} / {len(set(copied))}")
    print(f"seeds not found            : {len(missing_seeds)}")
    print(f"unresolved neo edges       : {len(unresolved_edges)}")
    print(f"third-party edges recorded : {len(external_edges)}")
    print(f"total bytes                : {total[0]:,}")
    print(f"archive                    : {archive.name} "
          f"sha256 {archive_sha[:16]}")

    # -- 5. Gates ------------------------------------------------------------
    # These FAIL the build rather than printing a number nobody reads. The
    # duplicate count survived a release precisely because it was reported and
    # never enforced.
    problems = gate_problems(copied, missing_seeds, documents, config_record)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0



def extension_identity(root: Path) -> dict:
    """Which Extension this bundle was built against.

    Section 8 recorded the absence of Extension and Neo source identity. Neo's
    is already carried through UPSTREAM_BASE; the Extension's was nowhere, so a
    reviewer comparing behaviour had no way to say WHICH Extension they were
    comparing against.
    """

    version = root / "version.json"
    if not version.is_file():
        return {"present": False}
    raw = version.read_bytes()
    record = {"present": True,
              "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        record.update(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        record["note"] = "version.json is not readable JSON"
    return record


if __name__ == "__main__":
    sys.exit(main())
