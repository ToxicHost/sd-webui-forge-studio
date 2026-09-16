"""Install the six approved auxiliary weights before Studio starts.

Generation and catalogue code never call this installer. Completed local assets
are verified and reused offline. Downloads are bounded and hash checked before
publication, and existing files are never replaced. --from-directory accepts a
flat offline cache containing the six exact filenames, not an arbitrary tree.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
BASE_URL = "https://github.com/ToxicHost/sd-webui-forge-studio/releases/download/studio-assets-v1/"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(app: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (relative.is_absolute() or len(relative.parts) < 3
            or relative.parts[0] != "models" or ".." in relative.parts
            or "\\" in name or ":" in name):
        raise ValueError("Invalid model asset path")
    path = app / relative
    if not path.resolve().is_relative_to(app.resolve() / "models") or path.is_symlink():
        raise ValueError("Model asset path escapes models or is linked")
    return path


def valid(path: Path, asset: dict) -> bool:
    return (not path.is_symlink() and path.is_file()
            and path.stat().st_size == asset["bytes"] and sha256(path) == asset["sha256"])


def install_asset(app: Path, asset: dict, *, from_directory: Path | None = None,
                  opener=None) -> bool:
    """Return whether a file was installed; never scan other model locations."""
    path = destination(app, asset["path"])
    if path.exists():
        if valid(path, asset):
            return False
        raise ValueError(f"Existing {asset['path']} differs from the approved asset; preserved. "
                         "Move it aside before retrying setup.")
    path.parent.mkdir(parents=True, exist_ok=True)
    url = BASE_URL + path.name
    if from_directory is None:
        print(f"Downloading {path.name} ({asset['bytes'] / 1_000_000:.1f} MB) ...", flush=True)
        source = (opener or urlopen)(url, timeout=60)
    else:
        source = (from_directory / path.name).open("rb")
    part = None
    try:
        with source:
            if from_directory is None and urlsplit(source.geturl()).scheme != "https":
                raise ValueError("Asset download redirected away from HTTPS")
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".",
                                             suffix=".download", delete=False) as target:
                part = Path(target.name)
                digest = hashlib.sha256()
                size = 0
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > asset["bytes"]:
                        raise ValueError(f"Download is larger than expected: {path.name}")
                    digest.update(chunk)
                    target.write(chunk)
        if size != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
            raise ValueError(f"Asset size/hash mismatch: {path.name}")
        # Windows rename and POSIX link both refuse an existing destination.
        # The latter is also atomic: readers only see a complete verified file.
        try:
            if os.name == "nt":
                os.rename(part, path)
            else:
                os.link(part, path)
        except FileExistsError:
            if valid(path, asset):
                return False
            raise ValueError(f"Another file appeared at {asset['path']}; preserved.")
        print(f"Verified {path.name}", flush=True)
        return True
    finally:
        if part is not None:
            part.unlink(missing_ok=True)


def ensure(app: Path = APP, *, from_directory: Path | None = None) -> int:
    assets = json.loads((app / "packaging/assets.json").read_text(encoding="utf-8"))["assets"]
    # Validate every destination before any download or directory creation.
    for asset in assets:
        destination(app, asset["path"])
    installed = sum(install_asset(app, asset, from_directory=from_directory) for asset in assets)
    print(f"Auxiliary models ready ({len(assets)} verified, {installed} installed).", flush=True)
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-directory", type=Path, help="Use exact local asset files instead of downloading")
    args = parser.parse_args()
    try:
        ensure(from_directory=args.from_directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Studio asset setup could not finish: {exc}")
        print("Existing files were preserved. Check the connection/cache and run Start-Studio.bat again.")
        print("The repository's studio-assets-v1 release must be published before first-time online setup.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
