"""Safe accounting for who owns each `sys.path` entry.

The final owner trial measured `sys.path` growing from 8 entries to 9 across
one load/unload cycle. The product's own managed entries restored exactly; the
extra one came from `modules/paths.py`, which does an unguarded
``sys.path.insert(0, script_path)`` at import time and is therefore a one-time
consequence of importing retained Forge code rather than a per-load leak.

This module records that fact without ever writing an absolute path into
evidence. Every entry is reported as a CATEGORY, a repository-relative path
where one applies, and a stable hash of the normalized entry -- enough to prove
"the same entry, not a new one" across cycles, and never enough to disclose a
private location.

Import-safe: stdlib only, no torch, no CUDA, no product imports.
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Iterable

#: Stable category names.
REPOSITORY_ROOT = "repository-root"
REPOSITORY_SUBDIR = "repository-subdir"
EXTERNAL = "external"

#: Stable policy names.
POLICY_MANAGED = "managed-and-restored"
POLICY_APP_LIFETIME = "stable-app-lifetime-bootstrap"


def _normalize(entry: str) -> str:
    """Case- and separator-stable form. Never returned to a caller."""

    try:
        return os.path.normcase(os.path.abspath(entry))
    except BaseException:  # noqa: BLE001 - accounting never raises
        return str(entry)


def entry_hash(entry: str) -> str:
    """A stable, non-reversing identity for one path entry."""

    return hashlib.sha256(_normalize(entry).encode("utf-8")).hexdigest()[:16]


def classify_entry(entry: str, *, app_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Category, safe relative path and stable hash for one entry."""

    normalized = _normalize(entry)
    root = _normalize(os.fspath(app_root))
    record: dict[str, Any] = {"hash": entry_hash(entry)}
    if normalized == root:
        record["category"] = REPOSITORY_ROOT
        record["relative"] = "."
        return record
    if normalized.startswith(root + os.sep):
        record["category"] = REPOSITORY_SUBDIR
        # Relative to the app root, so nothing above it is disclosed.
        record["relative"] = os.path.relpath(normalized, root).replace(
            os.sep, "/")
        return record
    record["category"] = EXTERNAL
    record["relative"] = None
    return record


def audit(entries: Iterable[str] | None = None, *,
          app_root: str | os.PathLike[str]) -> dict[str, Any]:
    """A safe, comparable snapshot of `sys.path`.

    Duplicates are counted rather than collapsed: the whole question is whether
    repeated load cycles add another copy of an entry that is already there.
    """

    values = list(sys.path if entries is None else entries)
    records = [classify_entry(entry, app_root=app_root) for entry in values]
    counts: dict[str, int] = {}
    for record in records:
        counts[record["hash"]] = counts.get(record["hash"], 0) + 1
    return {
        "entry_count": len(values),
        "unique_entries": len(counts),
        "duplicate_count": sum(n - 1 for n in counts.values() if n > 1),
        "by_category": {
            name: sum(1 for r in records if r["category"] == name)
            for name in (REPOSITORY_ROOT, REPOSITORY_SUBDIR, EXTERNAL)
        },
        "entries": records,
        "external_entries": [r for r in records if r["category"] == EXTERNAL],
    }


def difference(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between two audits, by hash rather than by path."""

    def tally(snapshot: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in snapshot.get("entries", []):
            counts[record["hash"]] = counts.get(record["hash"], 0) + 1
        return counts

    before_counts, after_counts = tally(before), tally(after)
    added = {h: after_counts[h] - before_counts.get(h, 0)
             for h in after_counts if after_counts[h] > before_counts.get(h, 0)}
    removed = {h: before_counts[h] - after_counts.get(h, 0)
               for h in before_counts if before_counts[h] > after_counts.get(h, 0)}
    return {
        "entry_count_before": before.get("entry_count"),
        "entry_count_after": after.get("entry_count"),
        "delta": (after.get("entry_count", 0) - before.get("entry_count", 0)),
        "added_hashes": added,
        "removed_hashes": removed,
        "unchanged": not added and not removed,
    }


def owner_of(entry_hash_value: str, *, app_root: str | os.PathLike[str]) -> str:
    """Which module is responsible for an entry, by known insertion sites.

    Only the two sites this repository actually has are named; anything else is
    reported as unknown rather than guessed.
    """

    root_hash = entry_hash(os.fspath(app_root))
    packages_hash = entry_hash(
        os.path.join(os.fspath(app_root), "modules_forge", "packages"))
    if entry_hash_value == root_hash:
        return "modules/paths.py"
    if entry_hash_value == packages_hash:
        return "forge_headless/live_bindings.py"
    return "unknown"


__all__ = (
    "EXTERNAL",
    "POLICY_APP_LIFETIME",
    "POLICY_MANAGED",
    "REPOSITORY_ROOT",
    "REPOSITORY_SUBDIR",
    "audit",
    "classify_entry",
    "difference",
    "entry_hash",
    "owner_of",
)
