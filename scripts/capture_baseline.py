"""Bind a claim to the exact source that produced it. WP0.1.

    venv/Scripts/python.exe scripts/capture_baseline.py

Writes `Evidence/baseline/<date>/` containing `environment.json`,
`commands.txt` and `verdict.md`.

`scripts/capture-environment.sh` already dumps a flat text summary of the
machine. This is a different artefact and does not replace it: section 17 of
the execution handoff requires an evidence envelope that IDENTIFIES ITSELF --
full HEAD, tree state, both Neo comparisons, dependency hashes, and the
explicit non-claims -- so that a preserved result can be re-bound to source
later, or shown to be unbindable. The shell script answers "what machine is
this"; this answers "which source is this claim about".

TWO COMPARISONS, NOT ONE. `neo...upstream/neo = 0 0` says the tracking branch
has not drifted from upstream. It says NOTHING about the product branch, and
confusing the two already produced a false claim that Studio was Neo-identical.
Both are recorded here, separately, with what each proves.

PRIVACY. Section 17 wants private values redacted, and Studio's own rule is
that no ordinary output leaks an owner path. Every recorded path is made
relative to the workspace, and anything still containing the user's home
directory is replaced with `<home>`. `redact()` is tested.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = APP_ROOT.parent

#: What the baseline deliberately does NOT assert. Kept in the artefact itself,
#: because a bare environment dump reads as an endorsement of whatever sits
#: beside it in the directory.
NON_CLAIMS = [
    "This records the source a claim was made against. It is not itself "
    "evidence that any feature works.",
    "No live generation, model load, or GPU work is performed by this capture.",
    "`neo...upstream/neo = 0 0` proves only that the TRACKING branch matches "
    "upstream. It is not a statement that Studio HEAD is Neo-identical.",
    "Docker, Linux and macOS remain uncertified; nothing here changes that.",
]


def redact(text: str) -> str:
    """Remove the owner's home directory from anything recorded.

    Both separators, because a Windows path reaches here spelled either way
    depending on which tool produced it.
    """

    home = str(Path.home())
    for spelling in (home, home.replace("\\", "/")):
        if spelling:
            text = text.replace(spelling, "<home>")
            text = text.replace(spelling.replace("\\", "\\\\"), "<home>")
    return text


class Recorder:
    """Every command, its exit code, and its output -- kept for the artefact."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def run(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        """Run one git command. `git` is prepended here, not by the caller.

        It was missing in the first draft, so every call raised
        FileNotFoundError, the recorder stored that text as output, and the
        one-line error counted as a dirty path -- producing a confident
        `tree=dirty (1 paths)` from a repository that was clean. A capture tool
        that reports a plausible wrong answer when its own command is broken is
        worse than one that crashes, which is why the exit code is now part of
        the artefact.
        """

        try:
            done = subprocess.run(
                ("git", *args), cwd=cwd or APP_ROOT, capture_output=True,
                text=True, timeout=120, check=False)
            code, out = done.returncode, (done.stdout + done.stderr)
        except (OSError, subprocess.SubprocessError) as error:
            code, out = -1, f"{type(error).__name__}: {error}"
        out = redact(out.strip())
        self.entries.append({"command": " ".join(args), "exit_code": code,
                             "output": out})
        return code, out

    def value(self, *args: str) -> str:
        """The single-line answer to a command, or "" when it failed."""

        code, out = self.run(*args)
        return out.splitlines()[0].strip() if code == 0 and out else ""


def file_digest(path: Path) -> dict:
    if not path.is_file():
        return {"present": False}
    data = path.read_bytes()
    return {"present": True, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def torch_facts() -> dict:
    """Accelerator facts, probed in a SUBPROCESS.

    Importing torch into this process would make a documentation tool depend on
    the inference stack, and a broken torch would then take the baseline down
    with it. The subprocess reports a failure as data instead.
    """

    probe = (
        "import json,torch;"
        "d={'torch':torch.__version__,'cuda_runtime':torch.version.cuda,"
        "'cuda_available':torch.cuda.is_available()};"
        "d.update({'device':torch.cuda.get_device_name(0),"
        "'device_count':torch.cuda.device_count()}"
        " if torch.cuda.is_available() else {});"
        "print(json.dumps(d))"
    )
    try:
        done = subprocess.run((sys.executable, "-c", probe),
                              capture_output=True, text=True, timeout=180,
                              check=False)
        if done.returncode == 0:
            return json.loads(done.stdout.strip().splitlines()[-1])
        return {"error": redact(done.stderr.strip()[:400])}
    except Exception as error:  # noqa: BLE001 - a probe must not break capture
        return {"error": f"{type(error).__name__}: {error}"}


def build(recorder: Recorder) -> dict:
    porcelain_code, porcelain = recorder.run("status", "--porcelain")
    dirty = [line for line in porcelain.splitlines() if line.strip()]

    head = recorder.value("rev-parse", "HEAD")
    tracking = recorder.value("rev-list", "--left-right", "--count",
                              "neo...upstream/neo")
    product = recorder.value("rev-list", "--left-right", "--count",
                             "neo...HEAD")
    _, neo_owned = recorder.run("diff", "--shortstat",
                                "neo-baseline-2026-07-23..HEAD", "--",
                                "modules/", "modules_forge/")
    _, untouched = recorder.run("diff", "--name-only",
                                "neo-baseline-2026-07-23..HEAD", "--",
                                "backend/", "ldm_patched/",
                                "extensions-builtin/")

    upstream_base = {}
    for line in (APP_ROOT / "UPSTREAM_BASE").read_text(
            encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            upstream_base[key.strip()] = value.strip()

    return {
        "schema_version": 1,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "branch": recorder.value("branch", "--show-current"),
            "head": head,
            "tree": "clean" if not dirty else f"dirty ({len(dirty)} paths)",
            "dirty_paths": [redact(line) for line in dirty],
            "porcelain_exit": porcelain_code,
            "remotes": redact(recorder.run("remote", "-v")[1]).splitlines(),
            "describe": recorder.value("describe", "--tags", "--always"),
            "submodules": recorder.run("submodule", "status")[1].splitlines(),
        },
        "neo": {
            # Deliberately two fields with different meanings. See the module
            # docstring; collapsing them has produced a false claim before.
            "tracking_vs_upstream": tracking,
            "tracking_vs_product_head": product,
            "tracking_proves": "the neo branch has not drifted from upstream",
            "product_proves": "the real divergence of Studio HEAD from Neo",
            "baseline_tag": upstream_base.get("baseline_tag", ""),
            "upstream_commit": upstream_base.get("commit", ""),
            "neo_owned_diffstat": neo_owned,
            "untouched_directories_changed": [
                line for line in untouched.splitlines() if line.strip()],
        },
        "dependencies": {
            "requirements.txt": file_digest(APP_ROOT / "requirements.txt"),
            "pyproject.toml": file_digest(APP_ROOT / "pyproject.toml"),
            "config.json": file_digest(APP_ROOT / "config.json"),
        },
        "runtime": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            **torch_facts(),
        },
        "documents": {
            "patch_inventory": file_digest(
                APP_ROOT / "docs" / "14_PATCH_INVENTORY.md"),
            "parity_ledger": file_digest(
                APP_ROOT / "docs" / "15_PARITY_LEDGER.json"),
        },
        "non_claims": NON_CLAIMS,
    }


def verdict(envelope: dict) -> str:
    source, neo = envelope["source"], envelope["neo"]
    clean = source["tree"] == "clean"
    untouched_ok = not neo["untouched_directories_changed"]
    lines = [
        "# Baseline verdict",
        "",
        f"- captured: {envelope['captured_utc']}",
        f"- branch: `{source['branch']}`",
        f"- HEAD: `{source['head']}`",
        f"- tree: **{source['tree']}**",
        "",
        "## Neo, as two separate comparisons",
        "",
        f"- `neo...upstream/neo` = `{neo['tracking_vs_upstream']}` — "
        f"{neo['tracking_proves']}",
        f"- `neo...HEAD` = `{neo['tracking_vs_product_head']}` — "
        f"{neo['product_proves']}",
        f"- Neo-owned diff: {neo['neo_owned_diffstat'] or '(none)'}",
        f"- inference directories modified: "
        f"{'NONE' if untouched_ok else neo['untouched_directories_changed']}",
        "",
        "## Verdict",
        "",
    ]
    if clean and untouched_ok:
        lines.append("**BINDABLE.** A claim made at this moment can be bound "
                     "to this HEAD: the tree is clean and the inference "
                     "directories are untouched.")
    else:
        lines.append("**NOT BINDABLE AS-IS.** " + (
            "The tree is dirty, so 'this HEAD' does not describe what ran. "
            if not clean else "") + (
            "Neo inference directories are modified, which the patch "
            "inventory's central claim denies. " if not untouched_ok else ""))
    lines += ["", "## Explicit non-claims", ""]
    lines += [f"- {item}" for item in envelope["non_claims"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None,
                        help="output directory (default Evidence/baseline/<date>)")
    args = parser.parse_args(argv)

    recorder = Recorder()
    envelope = build(recorder)
    stamp = envelope["captured_utc"][:10]
    out = Path(args.out) if args.out else WORKSPACE / "Evidence" / "baseline" / stamp
    out.mkdir(parents=True, exist_ok=True)

    (out / "environment.json").write_text(
        json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    (out / "commands.txt").write_text(
        "\n\n".join(
            f"$ git {entry['command']}\n[exit {entry['exit_code']}]\n"
            f"{entry['output']}" for entry in recorder.entries) + "\n",
        encoding="utf-8")
    (out / "verdict.md").write_text(verdict(envelope), encoding="utf-8")

    print(f"head={envelope['source']['head'][:12]} "
          f"tree={envelope['source']['tree']} "
          f"neo_head={envelope['neo']['tracking_vs_product_head']}")
    print(f"wrote {out.relative_to(WORKSPACE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
