"""Shared privacy checks for release archives and local support reports."""
from __future__ import annotations

import re
from pathlib import Path

#: A private absolute path, by SHAPE: a Windows user directory or a POSIX home.
#: Patterns are independent of the developer who builds the release.
#:
#: UNC SHARES ARE DELIBERATELY NOT HERE. `\\\\192.168.1.10\\models`,
#: `\\\\.\\PhysicalDrive0` and `\\\\.\\pipe` appear in
#: `test_external_model_roots.py` as hostile inputs the root policy must
#: REFUSE. A test proving a network path is rejected is hardening evidence,
#: not a leak, and flagging it would push someone to weaken the security test
#: to quiet the privacy test.
PRIVATE_PATH = re.compile(
    r"""(?ix)
    (?: [A-Z]:[\\/]+Users[\\/]+(?!<|\$|%|USERNAME|USER\b)[A-Za-z0-9._-]+ )
    | (?: /home/(?!<|\$|%)[A-Za-z0-9._-]+ )
    | (?: /Users/(?!<|\$|%|shared\b)[A-Za-z0-9._-]+ )
    """)

#: Trees Studio vendors rather than authors. Upstream files legitimately carry
#: their own authors' paths -- the HuggingFace configs under `backend/` name
#: `/home/patrick` and `/home/suraj_huggingface_co`, and mmcv names
#: `/home/kchen`. Rewriting vendored source to satisfy a privacy check would be
#: modifying someone else's code to make our test pass.
VENDORED = ("backend/", "extensions-builtin/", "modules/", "modules_forge/",
            "javascript/", "docker/")

#: User names that are obviously placeholders. A path naming one of these is
#: documentation or a fixture, not a person.
#:
#: An explicit set rather than a heuristic: "is this a real name" cannot be
#: decided by pattern, and the alternative -- allowing anything -- is how a
#: privacy guard quietly stops guarding. Adding a name here is a deliberate
#: one-line act, which is the right amount of friction.
PLACEHOLDERS = {
    "owner", "someone", "example", "user", "username", "me", "name", "test",
    "tester", "alice", "bob", "forge", "shared", "public", "x", "o", "a", "b",
    "server", "host", "share", "fileserver", "nas",
}

#: Secrets, by shape. A friends build has no business carrying any of these.
SECRET = re.compile(
    r"""(?ix)
    (?: \b(?:api[_-]?key|secret[_-]?key|access[_-]?token|bearer)\b\s*[:=]\s*['"][^'"]{8,} )
    | (?: \bhf_[A-Za-z0-9]{20,} )
    | (?: \bsk-[A-Za-z0-9]{20,} )
    """)


def readable_text(path: Path) -> str | None:
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".ico",
                               ".pt", ".safetensors", ".zip", ".pdf", ".docx",
                               ".woff", ".woff2", ".ttf", ".exr"):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def leaks_in(relative: str, text: str) -> list[str]:
    """Every privacy problem in ONE shipping file, or an empty list.

    THE single definition. `scripts/build_distributable.py` calls this too, so
    the repository and the archive built from it cannot be judged by two rules
    that drift apart -- and the one that drifts is always the copy nobody is
    looking at.

    `relative` is the repository-relative POSIX path, which is what the
    vendored-tree check needs; the archive prefixes its own directory, so the
    builder strips that before calling.
    """

    if relative.endswith((Path(__file__).name, "test_distributable_privacy.py")):
        return []          # the patterns themselves live here
    if relative.startswith(VENDORED):
        return []          # someone else's source, carrying their own paths

    problems: list[str] = []
    for found in PRIVATE_PATH.finditer(text):
        who = re.split(r"[\\/]", found.group(0).rstrip("\\/"))[-1]
        if who.lower() in PLACEHOLDERS:
            continue
        problems.append(f"private path {found.group(0)[:60]}")
    if SECRET.search(text):
        problems.append("looks like a secret")
    return problems
