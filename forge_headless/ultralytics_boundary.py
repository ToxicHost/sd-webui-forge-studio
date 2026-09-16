"""The only place in Studio that imports ultralytics, and the terms it does.

Three prohibited behaviours ship enabled in `ultralytics==8.3.119`, and one of
them runs BEFORE any Studio code would get a chance to object:

```text
utils/__init__.py:844   ONLINE = is_online()          at IMPORT. Opens a
                        socket to 1.1.1.1 and 8.8.8.8.
utils/__init__.py:1243  SETTINGS["sync"] default True  analytics/crash sync
utils/checks.py:273     check_pip_update_available()   an update check
```

"Neutralise BEFORE import" is therefore not a preference; there is no
after-import moment for the first one.

WHY ENVIRONMENT VARIABLES AND NOT MONKEYPATCHES
===============================================

`is_online` is not patched, it is ASKED not to run:

```python
def is_online() -> bool:
    try:
        assert str(os.getenv("YOLO_OFFLINE", "")).lower() != "true"
        import socket
        ...
```

The assert fires before `import socket`. With `YOLO_OFFLINE=True` there is no
DNS lookup, no socket, and no connection that merely FAILS -- the call is
never attempted. A monkeypatch would have produced a suppressed failure and
looked identical from the outside, which is the distinction this codebase has
already paid for once: "it did not raise" is not "it did nothing".

`YOLO_CONFIG_DIR` (`utils/__init__.py:854`) moves `settings.json` off
`AppData\\Roaming\\Ultralytics`. That path will not exist in Docker, and a
boundary that depends on a file outside the workspace is a boundary that holds
on one machine. Studio owns the directory instead.

THE VERIFICATION IS THE POINT
=============================

Setting the variables is half of it. `assert_offline()` re-reads the values
ULTRALYTICS ACTUALLY COMPUTED, after the import, and raises if they are not
what was asked for. Configuring a guard and trusting its silence is how this
project got `is_known_sampler` with no callers, a preview that never rendered,
and a progress label nothing populated. An instrument is proven to be in the
path or it is not an instrument.

Nothing here imports ultralytics, torch, or numpy at module scope.

WHY THIS LIVES IN `forge_headless`
==================================

`OwnedPackagePurityTests` forbids torch, torchvision, gradio and numpy
anywhere in `forge_studio`, at ANY scope -- not merely at module scope. That
rule is correct and this module is exactly what it is aimed at: the detector
runtime IS the torch boundary, lazy imports or not.

So the split follows the packages' existing meaning rather than the feature:
`forge_studio/detector_catalogue.py` keeps the policy -- which detectors
exist, which are admitted, names never paths, and it imports nothing heavier
than hashlib -- while the runtime lives here beside `preview_frame.py`, for
the same reason that one does. Studio still owns the integration; owning it
does not mean hosting torch in the package that must import without an engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: Studio-owned. Never `AppData\\Roaming\\Ultralytics`, which does not exist in
#: a container and is outside the workspace besides.
DEFAULT_CONFIG_DIRNAME = "ultralytics"

#: Set to "True" (the string ultralytics compares against, case-insensitively)
#: BEFORE the package is first imported.
OFFLINE_ENV = "YOLO_OFFLINE"
CONFIG_DIR_ENV = "YOLO_CONFIG_DIR"

#: Ultralytics reads this to decide whether to print its own banner and to
#: gate several conveniences. Not a network switch, but it keeps a library
#: from writing to a console Studio owns.
VERBOSE_ENV = "YOLO_VERBOSE"


class DetectorBoundaryError(RuntimeError):
    """The offline boundary is not in the state it was configured for."""


def _runtime_root(app_root: Path | str | None = None) -> Path:
    base = Path(app_root) if app_root is not None else Path(__file__).resolve().parent.parent
    return base / "tmp" / DEFAULT_CONFIG_DIRNAME


def configure_offline(*, app_root: Path | str | None = None) -> Path:
    """Set the environment ultralytics must be imported under. Idempotent.

    Returns the Studio-owned config directory, created if absent.

    MUST run before the first `import ultralytics` anywhere in the process.
    `import_yolo()` calls it, so the ordering is enforced by the only door
    rather than remembered at each call site.
    """

    config_dir = _runtime_root(app_root)
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # A read-only workspace is not a reason to fall back to AppData. The
        # env var is still set, so ultralytics will fail to write settings
        # rather than write them somewhere Studio does not own.
        pass
    os.environ[OFFLINE_ENV] = "True"
    os.environ[CONFIG_DIR_ENV] = str(config_dir)
    os.environ.setdefault(VERBOSE_ENV, "False")
    return config_dir


def assert_offline() -> dict[str, Any]:
    """Re-read what ultralytics COMPUTED, and refuse if it is not offline.

    Called after the import, deliberately. The environment says what was
    asked for; these values say what was believed. They have to agree, and
    nothing else in this module is allowed to assume they do.
    """

    import ultralytics.utils as uu

    online = bool(getattr(uu, "ONLINE", True))
    settings = getattr(uu, "SETTINGS", {}) or {}
    sync = bool(settings.get("sync", True))
    config_dir = str(getattr(uu, "USER_CONFIG_DIR", ""))

    if online:
        raise DetectorBoundaryError(
            "ultralytics computed ONLINE=True; it was imported before the "
            "offline boundary was configured."
        )
    if sync:
        raise DetectorBoundaryError(
            "ultralytics settings still have sync enabled."
        )
    expected = str(_runtime_root())
    if config_dir and Path(config_dir) != Path(expected):
        raise DetectorBoundaryError(
            "ultralytics is using a config directory Studio does not own."
        )
    return {"online": online, "sync": sync, "config_dir": config_dir}


def import_yolo(*, app_root: Path | str | None = None) -> Any:
    """The one door. Configure, import, verify, then hand back `YOLO`.

    `sync` is written off after the import as well as before: it lives in a
    settings FILE, so a directory carried over from an earlier run could
    arrive with it already true. Defence in depth rather than belt and braces
    -- the analytics gate at `utils/__init__.py:1055` needs `SETTINGS["sync"]`
    AND `ONLINE` AND `IS_PIP_PACKAGE` AND argv[0] named `yolo`, so with
    `ONLINE` false it is already unreachable; this closes the one input that
    persists across runs.
    """

    configure_offline(app_root=app_root)

    from ultralytics import YOLO
    import ultralytics.utils as uu

    settings = getattr(uu, "SETTINGS", None)
    if settings is not None:
        try:
            if settings.get("sync", False):
                settings["sync"] = False
        except Exception:  # noqa: BLE001 - a read-only settings store is fine
            pass

    assert_offline()
    return YOLO


def boundary_report(*, app_root: Path | str | None = None) -> dict[str, Any]:
    """What the boundary is, without importing anything. For diagnostics."""

    return {
        "offline_env": os.environ.get(OFFLINE_ENV, ""),
        "config_dir_env": os.environ.get(CONFIG_DIR_ENV, ""),
        "expected_config_dir": str(_runtime_root(app_root)),
        "imported": "ultralytics" in _imported_modules(),
    }


def _imported_modules() -> set[str]:
    import sys

    return set(sys.modules)


__all__ = (
    "CONFIG_DIR_ENV",
    "DEFAULT_CONFIG_DIRNAME",
    "OFFLINE_ENV",
    "VERBOSE_ENV",
    "DetectorBoundaryError",
    "assert_offline",
    "boundary_report",
    "configure_offline",
    "import_yolo",
)
