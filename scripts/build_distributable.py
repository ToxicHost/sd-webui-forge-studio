"""Build a lean tester ZIP or a separate complete tracked-source ZIP.

Release launchers are versioned under packaging/windows and copied to the ZIP
root. No workspace launcher, configuration, environment or user data is copied.
Auxiliary weights stay outside Git; packaging/assets.json pins their exact bytes.
The repository privacy scan must pass before any archive is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WS = APP.parent
OUT = WS / "dist"

#: Versioned templates copied to the extraction root.
TRACKED_LAUNCHERS = ("start_studio.py", "Start-Studio.bat")
ASSET_MANIFEST = APP / "packaging" / "assets.json"
RUNTIME_MANIFEST = APP / "packaging" / "runtime.json"

#: Explicit ZIP entries: no placeholder files or local model-directory scans.
EMPTY_MODEL_DIRECTORIES = ("Stable-diffusion", "VAE", "text_encoder")

#: Never, at any price. Regenerated on first run from the tracked template.
NEVER = ("studio-config.json",)

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def version_block() -> dict[str, str]:
    """The identifiers `10_RELEASE_PACKAGING_AND_UPDATE.md` says to show.

    Read from `UPSTREAM_BASE` rather than restated here, so the archive cannot
    claim a different upstream than the repository it was built from.
    """

    fields: dict[str, str] = {}
    for line in (APP / "UPSTREAM_BASE").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return {
        "distribution_version": fields.get("distribution_version", "unknown"),
        "studio_source_version": fields.get("studio_source_version", "unknown"),
        "neo_upstream_commit": fields.get("commit", "unknown"),
        "neo_baseline_tag": fields.get("baseline_tag", "unknown"),
        "built_from_commit": _head(),
    }


def _head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(APP),
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _tree_is_clean() -> bool:
    """A package built from uncommitted work cannot be reproduced from its own
    recorded commit, and the manifest would be a lie."""

    result = subprocess.run(["git", "status", "--porcelain"], cwd=str(APP),
                            capture_output=True, text=True, check=True)
    return not result.stdout.strip()


def tracked() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=str(APP),
                            capture_output=True, text=True, check=True)
    return [APP / name for name in result.stdout.split("\0")
            if name and (APP / name).is_file()]


def _runtime_files(paths: list[Path]) -> list[Path]:
    """Intersect tracked source with the reviewed runtime allowlist."""
    spec = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    files = set(spec["files"])
    directories = tuple(name + "/" for name in spec["directories"])
    by_name = {path.relative_to(APP).as_posix(): path for path in paths}
    missing = files - by_name.keys()
    missing.update(name for name in spec["directories"]
                   if not any(relative.startswith(name + "/") for relative in by_name))
    if missing:
        raise ValueError("Required runtime source is missing/untracked: " + ", ".join(sorted(missing)))
    return [path for relative, path in by_name.items()
            if relative in files or relative.startswith(directories)]


def _audit(members: list[tuple[Path, str]]) -> list[str]:
    """Every shipping file, through the repository's OWN privacy scanner.

    Imported rather than reimplemented: two copies of a privacy rule drift, and
    the copy that drifts is always the one nobody is looking at.
    """

    from scripts import distribution_privacy as privacy

    problems: list[str] = []
    for source, arc in members:
        text = privacy.readable_text(source)
        if text is None:
            continue          # binary, or unreadable: nothing to leak in text
        # `leaks_in` wants the REPOSITORY-relative path, because that is what
        # its vendored-tree and self-exemption checks are written against. The
        # archive prefixes its own directory name, so strip it.
        relative = arc.split("/app/", 1)[-1] if "/app/" in arc else arc.split("/", 1)[-1]
        problems += [f"{arc}: {p}" for p in privacy.leaks_in(relative, text)]
    return problems


def _asset_members(stem: str) -> list[tuple[Path, str]]:
    """Admit only named, contained, hash-matching release assets."""
    result = []
    for asset in json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))["assets"]:
        relative = asset["path"]
        source = APP / relative
        resolved = source.resolve()
        try:
            resolved.relative_to((APP / "models").resolve())
        except ValueError as exc:
            raise ValueError(f"Asset is outside models/: {relative}") from exc
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Required asset is missing or linked: {relative}")
        data = source.read_bytes()
        if len(data) != asset["bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError(f"Required asset hash/size mismatch: {relative}")
        result.append((source, f"{stem}/app/{relative}"))
    return result


def build(out_dir: Path, *, allow_dirty: bool = False, profile: str = "tester") -> int:
    if profile not in ("tester", "source"):
        raise ValueError(f"Unknown package profile: {profile}")
    if not _tree_is_clean() and not allow_dirty:
        print("REFUSED: the working tree is dirty. A package built from "
              "uncommitted work cannot be rebuilt from the commit it names.",
              file=sys.stderr)
        return 2

    version = version_block()
    stem = f"forge-studio-{version['distribution_version']}"
    if profile == "source":
        stem += "-source"
    directories = [f"{stem}/app/models/{name}/" for name in EMPTY_MODEL_DIRECTORIES] if profile == "tester" else []
    paths = tracked()
    members: list[tuple[Path, str]] = []
    try:
        selected = _runtime_files(paths) if profile == "tester" else paths
        for path in selected:
            relative = path.relative_to(APP).as_posix()
            if relative not in NEVER:
                members.append((path, f"{stem}/app/{relative}"))
        if profile == "tester":
            for name in TRACKED_LAUNCHERS:
                source = APP / "packaging" / "windows" / name
                if source not in paths:
                    raise ValueError(f"Required launcher is missing/untracked: {name}")
                members.append((source, f"{stem}/{name}"))
            members.extend(_asset_members(stem))
            start_here = APP / "packaging" / "START-HERE.md"
            if start_here not in paths:
                raise ValueError("Required tester guide is missing/untracked")
            members.append((start_here, f"{stem}/START-HERE.md"))
        if len({arc for _, arc in members}) != len(members):
            raise ValueError("Duplicate archive member.")
    except (OSError, ValueError, KeyError) as exc:
        print(f"REFUSED: {exc}; no archive written.", file=sys.stderr)
        return 4

    problems = _audit(members)
    if problems:
        print(f"REFUSED: {len(problems)} privacy problem(s); no archive written:",
              file=sys.stderr)
        for line in problems[:20]:
            print(f"  {line}", file=sys.stderr)
        return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{stem}.zip"
    # Sorted, and with a fixed timestamp, so two builds of the same commit
    # produce the same bytes. A checksum nobody can reproduce is decoration.
    members.sort(key=lambda pair: pair[1])
    file_manifest = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc in sorted(directories):
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o40755 << 16) | 0x10
            zf.writestr(info, b"")
        for source, arc in members:
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = source.read_bytes()
            zf.writestr(info, data)
            file_manifest.append({
                "path": arc.split("/", 1)[1],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out_dir / f"{stem}.zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8")

    manifest = {
        "archive": archive.name,
        "sha256": digest,
        "bytes": archive.stat().st_size,
        "file_count": len(members),
        "directory_count": len(directories),
        "entry_count": len(members) + len(directories),
        "directories": [arc.split("/", 1)[1] for arc in sorted(directories)],
        "tracked_file_count": len(paths),
        "profile": profile,
        "runtime_selection": json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8")) if profile == "tester" else None,
        "source_dirty": not _tree_is_clean(),
        "files": file_manifest,
        "bundled_assets": json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))["assets"] if profile == "tester" else [],
        # Root launchers are copies of templates covered by the source commit.
        "tracked_launchers": list(TRACKED_LAUNCHERS) if profile == "tester" else [],
        "excluded_always": list(NEVER),
        "privacy_scan": "passed",
        **version,
    }
    (out_dir / f"{stem}.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"{archive}")
    print(f"  {len(members)} files, {len(directories)} empty directories, "
          f"{archive.stat().st_size / 1_048_576:.1f} MB")
    print(f"  sha256 {digest}")
    print(f"  distribution {version['distribution_version']}  "
          f"neo {version['neo_upstream_commit'][:8]}  "
          f"built from {version['built_from_commit'][:8]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--allow-dirty", action="store_true",
                        help="build anyway; the manifest's commit will not "
                             "describe the contents")
    parser.add_argument("--profile", choices=("tester", "source"), default="tester",
                        help="tester: runtime and assets; source: complete tracked checkout")
    args = parser.parse_args()
    return build(args.out, allow_dirty=args.allow_dirty, profile=args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
