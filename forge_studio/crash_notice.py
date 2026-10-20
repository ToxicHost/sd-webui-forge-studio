"""Why Studio last stopped, for the page to say once (OOM1).

The launcher (`packaging/windows/start_studio.py` `supervise`) writes
`last-crash.json` when the server stops unexpectedly and starts it again,
passing the path in STUDIO_CRASH_RECORD. The restarted server reads it here at
startup, renames it so it is shown once per stop, and the page asks for it
when it reconnects (`GET /api/crash_notice`) and dismisses it
(`POST /api/crash_notice/dismiss`).

Holds no prompt and no path: the record has an exit code, a RAM sample and the
running job's size, steps, sampler and Hires/Auto Detail, which is what the
launcher could see in Studio's `studio.job:` log lines.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_notice: dict[str, Any] | None = None

#: Fields passed to the page; anything else in the file is ignored.
_FIELDS = ("time", "exit_code", "low_memory", "ram_percent", "available_gib", "job")
_JOB_FIELDS = ("size", "steps", "sampler", "hires", "auto_detail")


def load_once(environ: Any = None) -> dict[str, Any] | None:
    """Read the launcher's record if there is one, and set it aside. Never
    raises: a missing or unreadable record means nothing to say."""

    global _notice
    environ = os.environ if environ is None else environ
    raw = str(environ.get("STUDIO_CRASH_RECORD", "") or "")
    if not raw:
        return None
    path = Path(raw)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        path.replace(path.with_name("last-crash.seen.json"))
    except OSError:
        pass
    if not isinstance(record, dict):
        return None
    notice = {key: record.get(key) for key in _FIELDS}
    job = notice.get("job")
    notice["job"] = {key: job[key] for key in _JOB_FIELDS if key in job} if isinstance(job, dict) else None
    with _lock:
        _notice = notice
    return notice


def current() -> dict[str, Any] | None:
    with _lock:
        return dict(_notice) if _notice is not None else None


def dismiss() -> None:
    global _notice
    with _lock:
        _notice = None


__all__ = ("current", "dismiss", "load_once")
