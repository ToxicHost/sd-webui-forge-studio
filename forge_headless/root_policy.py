"""Validation for an owner-configured model root.

The catalogue used to be safe by construction: a root had to live inside the
Studio workspace, so nothing outside it was reachable. This milestone widens
that to three owner-named directories, which means the *root itself* now has to
be validated instead of merely located.

The ordering here is the security property, not an implementation detail:

```text
1. syntactic refusal, on the text as supplied      (no filesystem call yet)
2. drive-type classification, on the supplied path (no stat of the target)
3. resolve(strict=True)                            (first real filesystem call)
4. steps 1-2 again, on the resolved path           (resolution can cross a
                                                    junction or subst onto a
                                                    different, remote volume)
```

Steps 1 and 2 run before step 3 because ``Path.resolve(strict=True)`` stats the
target. Stat'ing ``\\\\server\\share\\models`` or a mapped network drive is
itself the harm: it authenticates to a remote host and can hang. Refusing after
resolution would refuse too late.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath

from .contracts import HeadlessError

ROOT_NOT_SUPPLIED = "HEADLESS_CATALOGUE_ROOT_NOT_SUPPLIED"
ROOT_UNAVAILABLE = "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE"
ROOT_NOT_A_DIRECTORY = "HEADLESS_CATALOGUE_ROOT_NOT_A_DIRECTORY"
ROOT_OUTSIDE_WORKSPACE = "HEADLESS_CATALOGUE_ROOT_OUTSIDE_WORKSPACE"
ROOT_DEVICE_NAMESPACE = "HEADLESS_CATALOGUE_ROOT_DEVICE_NAMESPACE"
ROOT_NETWORK_LOCATION = "HEADLESS_CATALOGUE_ROOT_NETWORK_LOCATION"
ROOT_IS_FILESYSTEM_ROOT = "HEADLESS_CATALOGUE_ROOT_IS_FILESYSTEM_ROOT"
ROOT_DRIVE_TYPE_UNRECOGNIZED = "HEADLESS_CATALOGUE_ROOT_DRIVE_TYPE_UNRECOGNIZED"
ROOT_NOT_ABSOLUTE = "HEADLESS_CATALOGUE_ROOT_NOT_ABSOLUTE"

#: ``GetDriveTypeW`` return values (winbase.h).
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

#: Local volume kinds a model root may live on. ``DRIVE_REMOTE`` is refused as a
#: network location; ``DRIVE_UNKNOWN`` and ``DRIVE_NO_ROOT_DIR`` are refused
#: because "we could not tell" must not resolve to "allow".
ACCEPTED_DRIVE_TYPES: frozenset[int] = frozenset(
    {DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_CDROM, DRIVE_RAMDISK}
)


def _refuse_by_syntax(text: str) -> None:
    """Refuse network and device namespaces from the path text alone.

    Both separator spellings are normalized first, so ``//server/share`` is
    refused exactly like ``\\\\server\\share``. On POSIX a leading ``//`` is
    implementation-defined, which is reason enough to refuse it too.
    """
    if not text.strip():
        raise HeadlessError(ROOT_NOT_SUPPLIED, "No model root was supplied.")
    normalized = text.replace("/", "\\")
    if normalized.startswith("\\\\?\\") or normalized.startswith("\\\\.\\"):
        # Extended-length and device namespaces bypass the normal parser, so
        # they are refused wholesale rather than interpreted. \\?\UNC\... is
        # covered here, before the plain-UNC check below can be reached.
        raise HeadlessError(
            ROOT_DEVICE_NAMESPACE,
            "That model root uses a device or extended-length namespace.",
        )
    if normalized.startswith("\\\\"):
        raise HeadlessError(
            ROOT_NETWORK_LOCATION,
            "That model root is a network location.",
        )


def drive_type(path: PurePath) -> int | None:
    """Classify the volume a path names, without touching the path itself.

    Returns ``None`` where the question does not apply: a non-Windows platform,
    or a path with no drive component (a relative path, or a POSIX path).

    ``GetDriveTypeW`` reads the local mount table. It reports ``DRIVE_REMOTE``
    for a mapped network drive without connecting to the server, which is why
    this runs before any stat of the target.
    """
    if os.name != "nt":
        return None
    drive = path.drive
    if not drive:
        return None
    if drive.startswith("\\\\"):
        # A UNC drive component. Refused syntactically before reaching here;
        # classifying it would mean contacting the host.
        return DRIVE_REMOTE
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return int(kernel32.GetDriveTypeW(f"{drive}\\"))


def _refuse_by_volume(path: PurePath, *, allow_unclassified: bool = False) -> None:
    """Refuse a volume this root may not live on, on ANY platform.

    This used to be a no-op off Windows. `drive_type()` returns None where
    there is no drive letter, the function returned immediately, and a test
    pinned that behaviour -- so `/mnt/nas/models` was accepted on Linux while
    the identical share was refused on Windows. The reasoning in this module's
    own docstring ("stat'ing a remote share authenticates to a remote host and
    can hang") was never Windows-specific; only the implementation was.

    POSIX is answered from the mount table instead, read by artifact rather
    than by platform name. A host that cannot be classified degrades to
    UNKNOWN, which is refused by default and overridable per configuration --
    because on such a host EVERY root is unknown, and refusing all of them
    would be worse than the risk.
    """

    kind = drive_type(path)
    if kind is not None:
        if kind == DRIVE_REMOTE:
            raise HeadlessError(
                ROOT_NETWORK_LOCATION,
                "That model root is on a mapped network drive.",
            )
        if kind not in ACCEPTED_DRIVE_TYPES:
            raise HeadlessError(
                ROOT_DRIVE_TYPE_UNRECOGNIZED,
                "The volume holding that model root could not be classified.",
            )
        return

    if os.name == "nt":
        # A Windows path with no drive component: relative, or drive-relative.
        # There is no mount table to consult and no volume named yet, so this
        # returns exactly as it always did. `require_absolute` refuses the
        # relative case for owner-configured roots, and the second pass over
        # the RESOLVED path always has a drive to classify.
        #
        # Stated rather than left to fall through: without it, every
        # drive-less path on Windows would reach the POSIX branch, read no
        # mount table, classify as UNKNOWN and be refused -- a behaviour
        # change on the platform this runs on, introduced by a fix aimed at
        # the two it does not.
        return

    _refuse_by_mount_table(path, allow_unclassified=allow_unclassified)


def _refuse_by_mount_table(
    path: PurePath, *, allow_unclassified: bool
) -> None:
    """The POSIX half. Deferred import so Windows never pays for it."""

    from .volume_policy import (
        ROOT_VOLUME_PSEUDO,
        ROOT_VOLUME_REMOTE,
        VolumeKind,
        admit_as_root,
        read_mount_table,
    )

    mounts = read_mount_table()
    if mounts is None:
        # No mount table could be read at all. Every path on this host is
        # unclassifiable, so the per-root override is the only thing that can
        # distinguish "we could not tell" from "this is fine".
        kind = VolumeKind.UNKNOWN
    else:
        kind = mounts.kind_for(path)

    admitted, reason = admit_as_root(
        kind, allow_unclassified=allow_unclassified
    )
    if admitted:
        return
    if reason == ROOT_VOLUME_REMOTE:
        raise HeadlessError(
            ROOT_NETWORK_LOCATION,
            "That model root is on a network filesystem.",
        )
    if reason == ROOT_VOLUME_PSEUDO:
        raise HeadlessError(
            ROOT_DEVICE_NAMESPACE,
            "That model root is on a system filesystem, not a disk.",
        )
    raise HeadlessError(
        ROOT_DRIVE_TYPE_UNRECOGNIZED,
        "The volume holding that model root could not be classified.",
    )


def _refuse_filesystem_root(resolved: Path) -> None:
    """Refuse ``C:\\`` and ``/``: a typo must not become a filesystem scan."""
    if resolved.parent == resolved:
        raise HeadlessError(
            ROOT_IS_FILESYSTEM_ROOT,
            "A whole filesystem or drive cannot be used as a model root.",
        )


def validate_root(
    root: os.PathLike[str] | str,
    *,
    require_absolute: bool = False,
    allow_unclassified_volume: bool = False,
) -> Path:
    """Return the resolved root, or raise ``HeadlessError``.

    ``require_absolute`` is for owner-configured roots, where a relative path
    would silently depend on the process working directory. Internal callers
    that legitimately resolve against the workspace leave it off.

    ``allow_unclassified_volume`` admits a volume whose kind could not be
    determined. It does NOT admit one positively identified as remote: a dead
    network mount hangs a request handler with no timeout, which is not a
    preference an owner should be able to override. Defaulted off so the
    behaviour of every existing caller is byte-identical.
    """
    text = os.fspath(root) if not isinstance(root, str) else root
    _refuse_by_syntax(text)
    supplied = PurePath(text)
    if require_absolute and not supplied.is_absolute():
        raise HeadlessError(
            ROOT_NOT_ABSOLUTE,
            "A model root must be an absolute path.",
        )
    _refuse_by_volume(supplied, allow_unclassified=allow_unclassified_volume)

    try:
        resolved = Path(text).resolve(strict=True)
    except OSError:
        raise HeadlessError(
            ROOT_UNAVAILABLE,
            "The configured model root is not available.",
        ) from None

    # Resolution can cross a junction, a subst'd drive or a symlinked parent
    # onto a different volume, so the same questions are asked again of the
    # answer. This second pass is the one that catches C:\Link -> \\host\share.
    _refuse_by_syntax(str(resolved))
    _refuse_by_volume(resolved, allow_unclassified=allow_unclassified_volume)
    _refuse_filesystem_root(resolved)

    if not resolved.is_dir():
        raise HeadlessError(
            ROOT_NOT_A_DIRECTORY,
            "The configured model root is not a directory.",
        )
    return resolved
