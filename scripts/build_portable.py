"""Build the pinned Windows/NVIDIA portable ZIP; network is build-time only.

Run with Python 3.13 and pip 26.1.2. Inputs may be supplied offline or fetched
explicitly with --download. Existing runtime preparation is reused only after
its lock identity and every recorded file hash have been verified.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from scripts import build_distributable as base

SPEC_DIR = APP / "packaging" / "portable"
LOCK = SPEC_DIR / "windows-x64.lock.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_path(root: Path, name: str) -> Path:
    part = PurePosixPath(name)
    if not name or part.is_absolute() or ".." in part.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    path = root / part
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Archive path escaped destination: {name}")
    return path


def check_zip(bundle: zipfile.ZipFile, root: Path) -> None:
    seen = set()
    for info in bundle.infolist():
        checked_path(root, info.filename)
        key = info.filename.rstrip("/").casefold()
        if key in seen or ((info.external_attr >> 16) & 0o170000) == 0o120000:
            raise ValueError(f"Duplicate or linked archive path: {info.filename}")
        seen.add(key)


def input_file(cache: Path, item: dict, download: bool) -> Path:
    path = checked_path(cache, item["filename"])
    if not path.exists():
        if not download:
            raise ValueError(f"Missing build input: {path.name}. Supply it or use --download.")
        parsed = urllib.parse.urlsplit(item["url"])
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise ValueError("Build inputs must use public HTTPS URLs")
        part = path.with_suffix(path.suffix + ".part")
        print(f"Downloading {path.name}", flush=True)
        with urllib.request.urlopen(item["url"], timeout=60) as response, part.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
        if sha256(part) != item["sha256"]:
            part.unlink()
            raise ValueError(f"Downloaded input hash mismatch: {path.name}")
        part.replace(path)
    if path.is_symlink() or sha256(path) != item["sha256"]:
        raise ValueError(f"Build input hash mismatch or link: {path.name}")
    return path


def build_env(work: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    for name in ("tmp", "hf", "torch", "mpl", "yolo", "numba"):
        (work / name).mkdir(parents=True, exist_ok=True)
    for key, name in {"TEMP":"tmp", "TMP":"tmp", "HF_HOME":"hf", "TORCH_HOME":"torch",
                      "MPLCONFIGDIR":"mpl", "YOLO_CONFIG_DIR":"yolo", "NUMBA_CACHE_DIR":"numba"}.items():
        env[key] = str(work / name)
    env.update(PIP_CONFIG_FILE="NUL" if os.name == "nt" else "/dev/null",
               PIP_DISABLE_PIP_VERSION_CHECK="1", SOURCE_DATE_EPOCH="315532800",
               PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", YOLO_OFFLINE="True")
    return env


def runtime_files(runtime: Path) -> list[Path]:
    result = []
    for path in runtime.rglob("*"):
        if path.is_symlink():
            raise ValueError("Linked files are not allowed in the runtime")
        if not path.is_file():
            continue
        relative = path.relative_to(runtime)
        # pip-generated console wrappers embed the build interpreter; Studio
        # calls Python modules itself. Never ship those wrappers or local URLs.
        if ("__pycache__" in relative.parts or path.suffix == ".pyc"
                or path.name == "direct_url.json"
                or relative.as_posix().startswith("Lib/site-packages/bin/")):
            continue
        result.append(path)
    return sorted(result)


def file_record(path: Path, root: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
            "sha256": sha256(path)}


def prepare_runtime(cache: Path, work: Path, lock: dict, *, download: bool) -> tuple[Path, dict]:
    runtime = work / "runtime"
    receipt_path = work / "runtime-files.json"
    lock_hash = sha256(LOCK)
    if runtime.exists():
        if not receipt_path.is_file():
            raise ValueError("Incomplete previous build. Choose a fresh --work directory.")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["lock_sha256"] != lock_hash:
            raise ValueError("Previous runtime belongs to a different lock. Choose a fresh --work directory.")
        for item in receipt["files"]:
            if sha256(checked_path(runtime, item["path"])) != item["sha256"]:
                raise ValueError(f"Prepared runtime changed: {item['path']}")
        print("Reusing verified prepared runtime", flush=True)
        return runtime, receipt

    env = build_env(work)
    pyzip = input_file(cache, lock["python"], download)
    for package in lock["packages"]:
        input_file(cache, package, download)
    missing_wheels = [p for p in lock["packages"] if p.get("built_wheel")
                      and not (cache / p["built_wheel"]["filename"]).exists()]
    if missing_wheels:
        from importlib.metadata import version
        if version("pip") != lock["build"]["pip"]:
            raise ValueError(f"Source wheel builds require pip {lock['build']['pip']}")
        for tool in lock["build"]["extra_inputs"]:
            input_file(cache, tool, download)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-index",
                        "--find-links", str(cache), "--no-cache-dir", "--wheel-dir", str(cache),
                        *[str(cache / p["filename"]) for p in missing_wheels]],
                       env=env, check=True)
    lines = []
    for package in lock["packages"]:
        item = package.get("built_wheel", package)
        wheel = input_file(cache, item, False)
        with zipfile.ZipFile(wheel) as bundle:
            check_zip(bundle, work / "validation-only")
        lines.append(f"{package['name']}=={package['version']} --hash=sha256:{item['sha256']}")
    requirements = work / "runtime-requirements.txt"
    requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runtime.mkdir()
    with zipfile.ZipFile(pyzip) as bundle:
        check_zip(bundle, runtime)
        bundle.extractall(runtime)
    shutil.copyfile(SPEC_DIR / "python313._pth", runtime / "python313._pth")
    target = runtime / "Lib" / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(target),
                    "--no-index", "--find-links", str(cache), "--no-deps", "--no-cache-dir",
                    "--only-binary=:all:", "--require-hashes", "--no-compile", "-r", str(requirements)],
                   env=env, check=True)
    probe = (
        "import json,sys; from importlib import metadata; "
        "import numpy,PIL,torch,torchvision,cv2,safetensors,fastapi,transformers,diffusers; "
        "print(json.dumps({'python':sys.version.split()[0], 'cuda':torch.version.cuda, "
        "'packages':{d.metadata['Name'].lower().replace('_','-'):d.version for d in metadata.distributions()}}))"
    )
    result = subprocess.run([str(runtime / "python.exe"), "-I", "-B", "-c", probe],
                            cwd=work, env=env, check=True, capture_output=True, text=True)
    found = json.loads(result.stdout)
    if found["python"] != lock["python"]["version"]:
        raise ValueError("Prepared Python has the wrong version")
    for package in lock["packages"]:
        if found["packages"].get(package["name"].lower().replace("_", "-")) != package["version"]:
            raise ValueError(f"Prepared package has the wrong version: {package['name']}")
    receipt = {"schema_version": 1, "lock_sha256": lock_hash, "probe": found,
               "files": [file_record(path, runtime) for path in runtime_files(runtime)]}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(receipt['files'])} runtime files", flush=True)
    return runtime, receipt


def sbom(lock: dict) -> dict:
    components = []
    for package in [lock["python"], *lock["packages"]]:
        artifact = package.get("built_wheel", package)
        components.append({"type":"library", "name":package["name"], "version":package["version"],
                           "hashes":[{"alg":"SHA-256", "content":artifact["sha256"]}],
                           "externalReferences":[{"type":"distribution", "url":package["url"]}]})
    return {"bomFormat":"CycloneDX", "specVersion":"1.6", "version":1, "components":components}


def build(cache: Path, work: Path, out: Path, *, download: bool = False, allow_dirty: bool = False) -> int:
    if os.name != "nt" or sys.version_info[:2] != (3, 13):
        raise ValueError("Build this Windows x64 profile with Python 3.13 on Windows")
    if not allow_dirty and not base._tree_is_clean():
        raise ValueError("Commit source changes before building a release, or use --allow-dirty for development.")
    for path in (cache, work, out):
        path.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    runtime, receipt = prepare_runtime(cache, work, lock, download=download)
    version = base.version_block()
    stem = f"forge-studio-{version['distribution_version']}-windows-nvidia-portable"
    # A short internal directory matters for Windows' extraction path limit.
    top = "Studio-Portable"
    tracked = base.tracked()
    selected = base._runtime_files(tracked)
    members = [(p, f"app/{p.relative_to(APP).as_posix()}") for p in selected]
    extras = [
        (SPEC_DIR / "Start-Studio.bat", "Start-Studio.bat"),
        (SPEC_DIR / "Check-Installation.bat", "Check-Installation.bat"),
        (SPEC_DIR / "START-HERE.md", "START-HERE.md"),
        (APP / "packaging/windows/start_studio.py", "start_studio.py"),
        (APP / "scripts/portable_runtime.py", "app/scripts/portable_runtime.py"),
        (APP / "docs/studio/PORTABLE_RUNTIME.md", "app/docs/studio/PORTABLE_RUNTIME.md"),
        (LOCK, "app/portable-runtime.json"),
    ]
    for path, arc in extras:
        if path not in tracked:
            raise ValueError(f"Required portable source is untracked: {path.relative_to(APP)}")
        members.append((path, arc))
    assets = base._asset_members(top)
    members.extend((p, arc.split("/",1)[1]) for p, arc in assets)
    problems = base._audit([(p, top + "/" + arc) for p, arc in members])
    if problems:
        raise ValueError("Privacy scan failed: " + "; ".join(problems[:10]))
    members.append((work / "runtime-files.json", "app/runtime-files.json"))
    bom = work / "SBOM.cdx.json"
    bom.write_text(json.dumps(sbom(lock), indent=2) + "\n", encoding="utf-8")
    members.append((bom, "app/SBOM.cdx.json"))
    members.extend((checked_path(runtime, item["path"]), "app/runtime/" + item["path"])
                   for item in receipt["files"])
    if len({arc.casefold() for _, arc in members}) != len(members):
        raise ValueError("Duplicate portable member")
    directories = [f"app/models/{name}/" for name in base.EMPTY_MODEL_DIRECTORIES]
    archive = out / (stem + ".zip")
    records = []
    print(f"Writing {archive.name} ({len(members)} files)", flush=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative in directories:
            info = zipfile.ZipInfo(top + "/" + relative, date_time=(1980,1,1,0,0,0))
            info.external_attr = (0o40755 << 16) | 0x10
            bundle.writestr(info, b"")
        for index, (path, relative) in enumerate(sorted(members, key=lambda item:item[1])):
            info = zipfile.ZipInfo(top + "/" + relative, date_time=(1980,1,1,0,0,0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.compress_level = 9
            info.external_attr = 0o644 << 16
            digest = hashlib.sha256(); size = 0
            with path.open("rb") as source, bundle.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk); digest.update(chunk); size += len(chunk)
            records.append({"path":relative, "bytes":size, "sha256":digest.hexdigest()})
            if index and index % 5000 == 0:
                print(f"Archived {index} files", flush=True)
    digest = sha256(archive)
    manifest = {"archive":archive.name, "profile":"windows-nvidia-portable", "top_directory":top,
                "sha256":digest, "bytes":archive.stat().st_size, "source_dirty":not base._tree_is_clean(),
                "file_count":len(records), "directory_count":len(directories), "directories":directories,
                "uncompressed_bytes":sum(item["bytes"] for item in records), "files":records,
                "runtime_lock_sha256":sha256(LOCK), "security_status":lock["security_status"],
                "privacy_scan":"Studio source passed; runtime from verified public artifacts; no development venv copied",
                **version}
    (out / (stem + ".manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / (stem + ".zip.sha256")).write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({key:manifest[key] for key in ("archive","sha256","bytes","uncompressed_bytes","file_count","built_from_commit")}), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=APP / "tmp/portable-inputs")
    parser.add_argument("--work", type=Path, default=APP / "tmp/portable-build")
    parser.add_argument("--out", type=Path, default=APP / "dist")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    try:
        return build(args.inputs.resolve(), args.work.resolve(), args.out.resolve(),
                     download=args.download, allow_dirty=args.allow_dirty)
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"Portable build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
