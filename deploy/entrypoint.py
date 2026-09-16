"""Container entry point: write a config if there is none, then launch Studio.

A container has no owner sitting in front of it to fill in a config file, and
`launch_studio.py` requires one -- correctly, because guessing a result root
on a workstation would be worse than refusing. So this bridges the two: it
derives a config from the mount layout the image documents, writes it ONCE if
it is absent, and then hands over.

It writes exactly one file, only when that file does not exist, and only
inside `/studio/config`. An operator who bind-mounts their own
`studio-config.json` gets theirs used verbatim and never rewritten -- the same
rule the Settings write follows, for the same reason.

Container-visible paths are the ones that go in the file. A host path is
meaningless inside the container and a container path is meaningless outside
it, so nothing here tries to translate between them. That is not a gap: on
Docker Desktop the "host" is a Linux VM with no recoverable Windows path, and
a translated path that looks right is strictly worse than an honest one.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", "/studio"))
CONFIG_DIR = Path(os.environ.get("STUDIO_CONFIG_DIR", STUDIO_HOME / "config"))
CONFIG_PATH = CONFIG_DIR / "studio-config.json"
RESULTS_DIR = Path(os.environ.get("STUDIO_RESULT_ROOT", STUDIO_HOME / "results"))
MODELS_DIR = Path(os.environ.get("STUDIO_MODEL_ROOT", STUDIO_HOME / "models"))

#: Inside the container. The host publish decides who can reach it.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = int(os.environ.get("STUDIO_PORT", "7865"))

#: Where the health probe looks. Loopback, because it runs INSIDE the
#: container -- a health check that had to leave would be checking the
#: network rather than the app.
HEALTH_URL = f"http://127.0.0.1:{DEFAULT_PORT}/api/status"


def role_directories() -> dict[str, list[str]]:
    """Model roots from the mount layout, as a variable-length list per role.

    `/studio/models/<role>` if it exists, so an operator can mount one
    directory per role; otherwise nothing, and the role comes up unconfigured
    and is set in Settings. Nothing is invented: a role with no directory is
    unconfigured, never pointed at a guess.
    """

    roles: dict[str, list[str]] = {}
    for role, folder in (
        ("checkpoint", "checkpoints"),
        ("text_encoder", "text-encoders"),
        ("vae", "vae"),
    ):
        candidate = MODELS_DIR / folder
        if candidate.is_dir():
            roles[role] = [str(candidate)]
    return roles


def build_config() -> dict[str, object]:
    return {
        "backend": os.environ.get("STUDIO_BACKEND", "headless"),
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "result_root": str(RESULTS_DIR),
        "onboarding": "default",
        "model_roots": role_directories(),
        "filesystem_browser": {
            # On by default here as everywhere. The browser shows the
            # CONTAINER's filesystem, which is the filesystem the operator is
            # configuring, and the picker says so on its face.
            "enabled": os.environ.get("STUDIO_FS_BROWSER", "1") != "0"
        },
        "load_access": {
            "timeout_seconds": int(os.environ.get("STUDIO_LOAD_TIMEOUT", "600")),
            "vram_ceiling_gib": int(os.environ.get("STUDIO_VRAM_CEILING_GIB", "14")),
        },
    }


def ensure_config() -> Path:
    """Write the config only if there is none. Never overwrite an operator's."""

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    # Written to a sibling and replaced, so a container killed mid-write
    # leaves either no config or a complete one -- never half of one that the
    # next start would refuse.
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(build_config(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, CONFIG_PATH)
    return CONFIG_PATH


def health() -> int:
    """Serving, not loaded. A container that has no model resident is healthy;
    that is the normal state and the product's whole starting point."""

    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=4) as response:
            return 0 if 200 <= response.status < 400 else 1
    except (urllib.error.URLError, OSError, ValueError):
        return 1


def main(argv: list[str]) -> int:
    if "--health" in argv:
        return health()

    config = ensure_config()
    sys.path.insert(0, str(STUDIO_HOME / "app"))
    from forge_studio.launch import main as studio_main

    return int(studio_main(["--config", str(config)]) or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
