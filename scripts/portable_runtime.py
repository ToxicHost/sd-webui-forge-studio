"""Validate the private portable runtime without installing or repairing packages."""
from __future__ import annotations
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import sys


def check(app: Path, *, verify: bool = False) -> list[str]:
    errors = []
    try:
        spec = json.loads((app / "portable-runtime.json").read_text(encoding="utf-8"))
        expected_python = app / "runtime" / "python.exe"
        if Path(sys.executable).resolve() != expected_python.resolve():
            errors.append("Start Studio with Start-Studio.bat to use its private Python.")
        actual = ".".join(map(str, sys.version_info[:3]))
        if actual != spec["python"]["version"]:
            errors.append(f"Python version mismatch: {actual}; expected {spec['python']['version']}.")
        site = app / "runtime" / "Lib" / "site-packages"
        found = {d.metadata["Name"].lower().replace("_", "-"): d.version
                 for d in metadata.distributions(path=[str(site)])}
        for package in spec["packages"]:
            version = found.get(package["name"].lower().replace("_", "-"))
            if version != package["version"]:
                errors.append(f"Missing or changed library: {package['name']} {package['version']}.")
        if verify:
            manifest = json.loads((app / "runtime-files.json").read_text(encoding="utf-8"))
            for item in manifest["files"]:
                relative = PurePosixPath(item["path"])
                if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"] or ":" in item["path"]:
                    raise ValueError("Invalid runtime manifest path")
                path = app / "runtime" / relative
                if not path.resolve().is_relative_to((app / "runtime").resolve()):
                    raise ValueError("Runtime manifest path escaped its directory")
                if not path.is_file():
                    errors.append(f"Missing runtime file: {relative}")
                    continue
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != item["sha256"]:
                    errors.append(f"Changed runtime file: {relative}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"Incomplete or invalid portable installation: {exc}")
    return errors


def main() -> int:
    app = Path(__file__).resolve().parents[1]
    errors = check(app, verify="--verify" in sys.argv)
    if errors:
        print("Studio portable installation needs attention:")
        for error in errors[:20]:
            print("  " + error)
        print("Extract a fresh complete release folder. Your existing files have not been changed.")
        return 1
    print("Portable runtime checks passed." + (" All recorded runtime file hashes match." if "--verify" in sys.argv else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
