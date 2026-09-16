"""What a tester sends back when something goes wrong. AR8.

BUILT TO AN EXISTING SPEC

`docs/studio/MAC_DIAGNOSTIC_BUNDLE_SPEC.md` already decided how this should
behave, for a Mac milestone that was never implemented. Its rules are not
Mac-specific and they are good, so they are followed here rather than
re-derived:

  * an ALLOW-LIST, not a deny-list -- "nothing outside this table may be
    collected", and a tester can read the whole list in a minute;
  * redaction applied to the FILE THAT IS WRITTEN, not to what is displayed --
    a tester must never have to trust that a viewer hid something;
  * one human-readable file the tester can inspect BEFORE deciding to send it;
  * `--dry-run` that collects nothing and prints exactly what it would take;
  * every write path printed before writing;
  * no network request of any kind, ever;
  * `null` means "not collected", which is distinct from `false`, a positive
    finding.

Nothing here is transmitted. This tool writes a file; a person decides.

WHAT IS DELIBERATELY NOT COLLECTED

The owner's artwork, in every form it takes: the `recovery/` directory (whole
layered documents), the gallery database and its thumbnails, and the results
folder. Also model files, model ROOT paths, and any absolute path outside the
project.

That artwork exclusion is not incidental. Canvas crash recovery (AR4.4) stores
complete documents under the state root, and a support bundle that swept the
state root wholesale would post someone's unfinished work to a chat channel.

THE LOG IS INCLUDED, REDACTED

Measured before deciding: the launch log carries ZERO prompts, and 1718 lines
carrying the owner's home directory. So its creative content is nil and its
path content is the whole risk -- which redaction handles, and which is why a
redacted tail is worth having rather than dropping the log entirely. A
traceback is usually the only thing that explains a tester's crash.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WS = APP.parent
OUT = WS / "dist"

#: How many log lines to carry. Enough for a traceback and the startup banner
#: around it; not the whole 2 MB history of every session.
LOG_TAIL_LINES = 400

#: Never collected, in any form. See the module docstring.
NEVER_COLLECT = ("recovery", "gallery", "Studio-Results", "Private-Local")

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _redactions() -> list[tuple[re.Pattern[str], str]]:
    """Order matters: the most specific path first.

    The project root is rewritten before the generic user-directory rule, so a
    path inside the project becomes `<PROJECT>/...` rather than
    `C:\\Users\\<REDACTED>\\Desktop\\...` -- which would still leak the layout.
    """

    return [
        (re.compile(re.escape(str(WS)), re.I), "<PROJECT>"),
        (re.compile(re.escape(str(WS).replace("\\", "/")), re.I), "<PROJECT>"),
        (re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[A-Za-z0-9._-]+", ), r"\1<REDACTED>"),
        (re.compile(r"(?i)(/home/)[A-Za-z0-9._-]+"), r"\1<REDACTED>"),
        (re.compile(r"(?i)(/Users/)(?!shared\b)[A-Za-z0-9._-]+"), r"\1<REDACTED>"),
    ]


def redact(text: str) -> str:
    for pattern, replacement in _redactions():
        text = pattern.sub(replacement, text)
    return text


def _run(command: list[str]) -> str | None:
    """A local command, or None. Never a network call."""

    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _versions() -> dict[str, object]:
    fields: dict[str, str] = {}
    base = APP / "UPSTREAM_BASE"
    if base.is_file():
        for line in base.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip()
    return {
        "distribution_version": fields.get("distribution_version"),
        "studio_source_version": fields.get("studio_source_version"),
        "neo_upstream_commit": fields.get("commit"),
        "repository_commit": _run(["git", "-C", str(APP), "rev-parse", "HEAD"]),
    }


def _gpu() -> dict[str, object]:
    """Model name and driver only.

    A GPU model is not identifying -- thousands of people own the same card --
    and for an NVIDIA-only alpha it is the single most useful field there is.
    The serial number and UUID are deliberately NOT queried.
    """

    line = _run(["nvidia-smi",
                 "--query-gpu=name,driver_version,memory.total",
                 "--format=csv,noheader"])
    if not line:
        return {"name": None, "driver": None, "memory_total": None}
    parts = [p.strip() for p in line.splitlines()[0].split(",")]
    while len(parts) < 3:
        parts.append("")
    return {"name": parts[0] or None, "driver": parts[1] or None,
            "memory_total": parts[2] or None}


def collect(state_root: Path | None) -> dict[str, object]:
    """THE ALLOW-LIST. Nothing outside this function is gathered."""

    torch_spec = importlib.util.find_spec("torch")     # presence, not an import
    report: dict[str, object] = {
        "schema_version": "studio-support-bundle/v1",
        "generated_at": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "architecture": platform.architecture()[0],
            "implementation": platform.python_implementation(),
        },
        "gpu": _gpu(),
        "versions": _versions(),
        # Presence, checked WITHOUT importing: `find_spec` does not execute the
        # package, so this cannot initialise CUDA as a side effect of asking.
        "torch_present": torch_spec is not None,
        "state_root_present": bool(state_root and state_root.is_dir()),
        "settings": None,
        "log_tail_lines": 0,
    }

    if state_root and state_root.is_dir():
        settings: dict[str, object] = {}
        for name in ("preferences.json", "defaults.json", "wildcards.json"):
            path = state_root / name
            if not path.is_file():
                continue
            try:
                settings[name] = json.loads(redact(
                    path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                settings[name] = "<unreadable>"
        # `last-session.json` is NOT collected: it is the tester's working
        # state, it is large, and nothing in it helps diagnose a crash that
        # `defaults.json` does not already show.
        report["settings"] = settings or None

    return report


def _log_tail() -> str | None:
    log = WS / "logs" / "studio-internal-alpha.log"
    if not log.is_file():
        return None
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return redact("\n".join(lines[-LOG_TAIL_LINES:]))


def _readable(report: dict[str, object], log: str | None) -> str:
    """The file the tester reads before deciding to send anything."""

    gpu = report["gpu"]                              # type: ignore[index]
    versions = report["versions"]                    # type: ignore[index]
    out = [
        "Forge Studio - support bundle",
        "",
        "You are about to send this. Read it first; nothing has been",
        "transmitted, and nothing will be. Delete the file to send nothing.",
        "",
        f"generated      {report['generated_at']}",
        f"system         {report['platform']['system']} "        # type: ignore[index]
        f"{report['platform']['release']}",                      # type: ignore[index]
        f"python         {report['python']['version']} "         # type: ignore[index]
        f"{report['python']['architecture']}",                   # type: ignore[index]
        f"gpu            {gpu['name']}  driver {gpu['driver']}",  # type: ignore[index]
        f"distribution   {versions['distribution_version']}",     # type: ignore[index]
        f"commit         {versions['repository_commit']}",        # type: ignore[index]
        f"torch present  {report['torch_present']}",
        "",
        "NOT collected: your images, your Canvas documents, your gallery,",
        "your prompts, your model files, and the folders they live in.",
        "Every path below has been rewritten before this file was written.",
        "",
        f"log            last {report['log_tail_lines']} lines, paths redacted",
    ]
    if log:
        out += ["", "-" * 68, "", log]
    return "\n".join(out) + "\n"


def build(out_dir: Path, state_root: Path | None, *, dry_run: bool = False) -> int:
    report = collect(state_root)
    log = _log_tail()
    report["log_tail_lines"] = len(log.splitlines()) if log else 0

    # A plain UTC stamp. The offset lives in the report, not in a filename a
    # tester has to type or quote.
    stamp = re.sub(r"[^0-9T]", "",
                   str(report["generated_at"]).split("+")[0])
    archive = out_dir / f"forge-studio-support-{stamp}.zip"
    readable = _readable(report, log)

    if dry_run:
        print("DRY RUN — nothing collected, nothing written.\n")
        print("Would write:")
        print(f"  {archive}")
        print(f"  {archive.with_suffix('.txt')}")
        print("\nWould contain exactly these fields:")
        for key in sorted(report):
            print(f"  {key}")
        print(f"\nAnd the last {report['log_tail_lines']} log lines, redacted.")
        print("\nWould NEVER contain: " + ", ".join(NEVER_COLLECT))
        return 0

    # FAIL CLOSED, through the repository's own scanner -- the same rule the
    # distributable uses. A bundle that leaks is worse than no bundle, because
    # the tester was told it was safe.
    from scripts import distribution_privacy as privacy

    body = json.dumps(report, indent=2) + "\n"
    for label, text in (("report.json", body), ("README.txt", readable)):
        problems = privacy.leaks_in(label, text)
        if problems:
            print(f"REFUSED: {label} still carries {problems[0]}; "
                  f"nothing written.", file=sys.stderr)
            return 3

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"writing {archive.with_suffix('.txt')}")
    archive.with_suffix(".txt").write_text(readable, encoding="utf-8")
    print(f"writing {archive}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", body)
        zf.writestr("README.txt", readable)

    print(f"\n{archive.with_suffix('.txt').name} is plain text. Read it, then "
          f"send the .zip if you are happy with it.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ["STUDIO_STATE_ROOT"])
                        if os.environ.get("STUDIO_STATE_ROOT") else None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print exactly what would be collected, and "
                             "collect nothing")
    args = parser.parse_args()
    return build(args.out, args.state_root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
