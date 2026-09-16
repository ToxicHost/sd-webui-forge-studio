"""What kind of volume a path lives on, and whether a model root may sit there.

Two separate questions, deliberately kept apart:

```text
classify_*()        WHAT this volume is          a fact about the host
admit_as_root()     whether a root may live      the current security policy
                    on that kind of volume
```

The split is the point. "Network filesystems are refused" is where the product
stands today, not a permanent architectural prohibition -- an explicitly
supported network root is a change to the policy function and its reason codes,
with the classifier untouched. Folding the two together is how a temporary
decision becomes a structural one nobody can find later.

**Why refuse a network volume at all.** `root_policy` already refuses UNC paths
and mapped drives on Windows, and says why: stat'ing `\\\\server\\share\\models`
authenticates to a remote host and can hang, with no socket timeout and no
cancellation, inside a request handler. That reasoning is not Windows-specific.
A dead NFS mount at `/mnt/nas/models` hangs a Linux handler exactly the same
way -- but `drive_type()` returns None off Windows, so nothing refused it. The
same share was refused on one platform and accepted on another.

**How the platform is identified.** By artifact, never by name. This module
asks whether `/proc/self/mountinfo` can be read, then whether `getmntinfo` can
be called; it does not ask what the operating system is called. That keeps it
under the same `sys.platform` ban as the rest of the filesystem stack, and it
is also simply more accurate: a host that provides mountinfo is a host whose
mountinfo can be read, whatever it calls itself.

**Failure is degraded, not fatal.** Every reader returns None rather than
raising, and a `None` table means "could not classify", which the policy treats
as UNKNOWN. A missing symbol on some future macOS must not be able to make
Studio refuse every root on the machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePath

#: Stable refusal codes. Named per KIND, so an owner-facing message can say
#: which property of the volume was the problem.
ROOT_VOLUME_REMOTE = "HEADLESS_CATALOGUE_ROOT_VOLUME_REMOTE"
ROOT_VOLUME_PSEUDO = "HEADLESS_CATALOGUE_ROOT_VOLUME_PSEUDO"
ROOT_VOLUME_UNCLASSIFIED = "HEADLESS_CATALOGUE_ROOT_VOLUME_UNCLASSIFIED"


class VolumeKind(str, Enum):
    """What a volume IS. Carries no permission, only a classification."""

    LOCAL = "local"
    REMOVABLE = "removable"
    REMOTE = "remote"
    PSEUDO = "pseudo"
    UNKNOWN = "unknown"


#: Filesystem types that reach another machine. Refusing these is the whole
#: point of the module, so the list errs toward inclusion: a type named here
#: that turns out to be local costs an owner one config entry, while a type
#: missing from it costs a hung request handler.
#:
#: `fuse.*` is matched by prefix as well, because FUSE network filesystems are
#: named after their transport (`fuse.sshfs`, `fuse.s3fs`, `fuse.rclone`) and
#: the set is open-ended.
NETWORK_FSTYPES = frozenset({
    "9p", "afp", "afpfs", "afs", "ceph", "cifs", "coda", "davfs", "ftpfs",
    "fuse.cephfs", "fuse.davfs", "fuse.glusterfs", "fuse.rclone", "fuse.s3fs",
    "fuse.sshfs", "fuseblk.cifs", "gfs2", "glusterfs", "lustre", "ncpfs",
    "nfs", "nfs3", "nfs4", "nfsd", "ocfs2", "orangefs", "prl_fs", "pvfs2",
    "smb", "smb2", "smbfs", "vboxsf", "vmhgfs", "webdav",
})

#: Kernel bookkeeping surfaces. Never a place models live, and enumerating one
#: can be pathological -- `/proc` alone presents a directory per process.
PSEUDO_FSTYPES = frozenset({
    "autofs", "bpf", "binfmt_misc", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs", "mqueue",
    "nsfs", "proc", "procfs", "pstore", "rpc_pipefs", "securityfs", "selinuxfs",
    "sysfs", "tracefs",
})

#: Explicitly local, listed so the classifier says LOCAL rather than UNKNOWN
#: for the cases that matter most.
#:
#: `overlay` is here because it is what a container's own root filesystem is.
#: Classifying it as anything else would refuse every root inside Docker, which
#: is the deployment this phase exists to support. `tmpfs` is here for the same
#: reason Windows accepts DRIVE_RAMDISK: a RAM disk is a strange place to keep
#: models and an entirely local one.
LOCAL_FSTYPES = frozenset({
    "apfs", "bcachefs", "btrfs", "exfat", "ext2", "ext3", "ext4", "f2fs",
    "fuseblk", "hfs", "hfsplus", "jfs", "msdos", "ntfs", "ntfs3", "overlay",
    "overlayfs", "reiserfs", "squashfs", "tmpfs", "ufs", "vfat", "xfs", "zfs",
})

#: `GetDriveTypeW` return values (winbase.h). Mirrored from `root_policy` so
#: this module can classify without importing the refusal logic that uses it.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_WINDOWS_KINDS = {
    DRIVE_REMOVABLE: VolumeKind.REMOVABLE,
    DRIVE_FIXED: VolumeKind.LOCAL,
    DRIVE_CDROM: VolumeKind.REMOVABLE,
    DRIVE_RAMDISK: VolumeKind.LOCAL,
    DRIVE_REMOTE: VolumeKind.REMOTE,
    DRIVE_UNKNOWN: VolumeKind.UNKNOWN,
    DRIVE_NO_ROOT_DIR: VolumeKind.UNKNOWN,
}


def classify_fstype(fstype: str) -> VolumeKind:
    """Classify by filesystem type name. Pure: no I/O, no platform question."""

    name = (fstype or "").strip().lower()
    if not name:
        return VolumeKind.UNKNOWN
    if name in NETWORK_FSTYPES:
        return VolumeKind.REMOTE
    if name in PSEUDO_FSTYPES:
        return VolumeKind.PSEUDO
    if name in LOCAL_FSTYPES:
        return VolumeKind.LOCAL
    # Open-ended by construction: FUSE filesystems are named after their
    # transport and new ones appear faster than any table is updated. An
    # unrecognised `fuse.*` is treated as remote rather than unknown, because
    # the ones that are not local are the ones that hang.
    if name.startswith("fuse.") or name.startswith("fuseblk."):
        return VolumeKind.REMOTE
    return VolumeKind.UNKNOWN


def classify_windows_drive(drive_type: int) -> VolumeKind:
    """Classify a `GetDriveTypeW` answer. Pure."""

    return _WINDOWS_KINDS.get(drive_type, VolumeKind.UNKNOWN)


@dataclass(frozen=True)
class Mount:
    """One mount point and what is mounted there."""

    mount_point: str
    fstype: str
    kind: VolumeKind


class MountTable:
    """Mount points, resolved by longest matching prefix.

    Longest-prefix and not first-match: `/mnt` may be local while
    `/mnt/nas` is an NFS mount underneath it, and a first-match lookup would
    call the NFS path local. The deepest mount whose point is a prefix of the
    candidate is the one the path actually lives on.
    """

    def __init__(self, mounts) -> None:
        # Sorted deepest-first so the first prefix hit is also the longest.
        self._mounts = tuple(
            sorted(mounts, key=lambda m: len(m.mount_point), reverse=True)
        )

    def __len__(self) -> int:
        return len(self._mounts)

    @property
    def mounts(self):
        return self._mounts

    def kind_for(self, path: os.PathLike[str] | str) -> VolumeKind:
        candidate = PurePath(os.fspath(path)).as_posix().rstrip("/") or "/"
        for mount in self._mounts:
            point = mount.mount_point.rstrip("/") or "/"
            if candidate == point:
                return mount.kind
            prefix = point if point.endswith("/") else point + "/"
            if candidate.startswith(prefix):
                return mount.kind
        return VolumeKind.UNKNOWN


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    """Parse `/proc/self/mountinfo`. Pure, so it is tested on any host.

    Format, per `Documentation/filesystems/proc.rst`:

    ```text
    36 35 98:0 /mnt1 /mnt rw,noatime - ext3 /dev/root rw,errors=continue
                     ^ mount point            ^ fstype
                                    ^ optional fields end at the lone "-"
    ```

    The separator matters: the optional-fields section between field 6 and the
    `-` is variable length, so the fstype cannot be read from a fixed index.
    Splitting on the separator is the documented way and the only stable one.
    """

    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        separator = fields.index("-")
        if separator < 5 or len(fields) <= separator + 1:
            continue
        mount_point = _unescape_octal(fields[4])
        fstype = fields[separator + 1]
        mounts.append(
            Mount(
                mount_point=mount_point,
                fstype=fstype,
                kind=classify_fstype(fstype),
            )
        )
    return tuple(mounts)


def _unescape_octal(field: str) -> str:
    r"""Decode the `\040`-style escapes mountinfo uses for space, tab and more.

    A mount point containing a space is written `\040`, so a path is otherwise
    silently truncated at the first space -- and the longest-prefix lookup then
    matches the wrong mount, or none.
    """

    if "\\" not in field:
        return field
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\" and index + 3 < len(field) + 1:
            digits = field[index + 1 : index + 4]
            if len(digits) == 3 and all(d in "01234567" for d in digits):
                out.append(chr(int(digits, 8)))
                index += 4
                continue
        out.append(char)
        index += 1
    return "".join(out)


def read_mount_table(*, mountinfo_path: str = "/proc/self/mountinfo"):
    """The host's mount table, or None if it could not be read.

    Artifact detection, in order of certainty. `/proc/self/mountinfo` is asked
    for first because reading a file is cheaper and far more predictable than
    binding a libc symbol, and because it is per-process -- inside a container
    it describes the container's view, which is the view that matters.

    None means "could not classify", never "nothing is mounted". The
    difference decides whether the policy refuses or degrades.
    """

    table = _read_mountinfo(mountinfo_path)
    if table is not None:
        return table
    return _read_getmntinfo()


def _read_mountinfo(path: str):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            text = handle.read()
    except (OSError, ValueError):
        return None
    mounts = parse_mountinfo(text)
    return MountTable(mounts) if mounts else None


def _read_getmntinfo():
    """macOS and the BSDs, through libc. None on any doubt whatsoever.

    `getmntinfo$INODE64` is asked for first: on macOS the plain `getmntinfo`
    symbol is bound to the legacy 32-bit-inode `statfs` layout, so reading the
    modern structure through it mis-parses every field after the first few --
    and `f_fstypename` comes out as garbage that would classify as UNKNOWN at
    best and as a wrong answer at worst.

    Every parsed name is validated as printable ASCII before it is trusted,
    because a mis-parse that happens to produce plausible bytes is the failure
    this cannot detect any other way.
    """

    try:
        import ctypes
        import ctypes.util
    except ImportError:  # pragma: no cover - ctypes is always present
        return None

    try:
        library_name = ctypes.util.find_library("c")
        if not library_name:
            return None
        libc = ctypes.CDLL(library_name, use_errno=True)
    except (OSError, AttributeError):
        return None

    entry_point = None
    for symbol in ("getmntinfo$INODE64", "getmntinfo64", "getmntinfo"):
        entry_point = getattr(libc, symbol, None)
        if entry_point is not None:
            break
    if entry_point is None:
        return None

    # struct statfs (macOS, _DARWIN_FEATURE_64_BIT_INODE):
    #   uint32 bsize, int32 iosize, uint64 blocks/bfree/bavail,
    #   uint64 files/ffree, fsid_t(2x int32) fsid, uid_t owner,
    #   uint32 type/flags/fssubtype, char f_fstypename[16],
    #   char f_mntonname[1024], char f_mntfromname[1024], uint32 reserved[8]
    MFSTYPENAMELEN = 16
    MAXPATHLEN = 1024

    class _Statfs(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32),
            ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64),
            ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64),
            ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64),
            ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32),
            ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32),
            ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * MFSTYPENAMELEN),
            ("f_mntonname", ctypes.c_char * MAXPATHLEN),
            ("f_mntfromname", ctypes.c_char * MAXPATHLEN),
            ("f_reserved", ctypes.c_uint32 * 8),
        ]

    entry_point.restype = ctypes.c_int
    entry_point.argtypes = [
        ctypes.POINTER(ctypes.POINTER(_Statfs)),
        ctypes.c_int,
    ]

    buffer = ctypes.POINTER(_Statfs)()
    MNT_NOWAIT = 2  # Never block on an unresponsive mount to describe it.
    try:
        count = entry_point(ctypes.byref(buffer), MNT_NOWAIT)
    except (OSError, ValueError, ctypes.ArgumentError):
        return None
    if count <= 0:
        return None

    mounts: list[Mount] = []
    for index in range(count):
        try:
            record = buffer[index]
            fstype = record.f_fstypename.decode("ascii", "strict")
            mount_point = record.f_mntonname.decode("utf-8", "surrogateescape")
        except (UnicodeDecodeError, ValueError, IndexError):
            # A mis-parsed structure, which is exactly what a wrong symbol
            # produces. Abandon the whole table rather than trust part of it.
            return None
        if not fstype.isprintable() or not mount_point.startswith("/"):
            return None
        mounts.append(
            Mount(
                mount_point=mount_point,
                fstype=fstype,
                kind=classify_fstype(fstype),
            )
        )
    return MountTable(mounts) if mounts else None


def admit_as_root(
    kind: VolumeKind, *, allow_unclassified: bool = False
) -> tuple[bool, str | None]:
    """CURRENT POLICY: may a model root live on a volume of this kind?

    Returns `(admitted, reason_code)`. This function is the whole of the
    decision, so a future release that supports explicitly configured network
    roots changes it and nothing else -- the classifier above states facts and
    has no opinion.

    ```text
    LOCAL, REMOVABLE   admitted
    REMOTE             refused, and not overridable. A dead mount hangs a
                       request handler with no timeout and no cancellation;
                       that is an availability failure, not a preference.
    PSEUDO             refused. Kernel bookkeeping is never where models live
                       and enumerating it can be pathological.
    UNKNOWN            refused by default, overridable. "Could not tell" must
                       not resolve to "allow" -- but on a host whose mount
                       table cannot be read at all, every root is UNKNOWN, and
                       an owner must have a way to say "this one is fine".
    ```
    """

    if kind in (VolumeKind.LOCAL, VolumeKind.REMOVABLE):
        return True, None
    if kind is VolumeKind.REMOTE:
        return False, ROOT_VOLUME_REMOTE
    if kind is VolumeKind.PSEUDO:
        return False, ROOT_VOLUME_PSEUDO
    if allow_unclassified:
        return True, None
    return False, ROOT_VOLUME_UNCLASSIFIED


def kind_for_path(
    path: os.PathLike[str] | str,
    *,
    mounts: MountTable | None,
    windows_drive_type=None,
) -> VolumeKind:
    """Classify one path, using whichever evidence this host provides.

    The Windows answer is preferred where there is one, because
    `GetDriveTypeW` reads the local mount table and reports a mapped network
    drive as remote WITHOUT contacting the server -- which is the only way to
    ask the question safely on that platform.
    """

    if windows_drive_type is not None:
        drive = PurePath(os.fspath(path)).drive
        if drive:
            return classify_windows_drive(windows_drive_type(PurePath(path)))
    if mounts is None:
        return VolumeKind.UNKNOWN
    return mounts.kind_for(Path(os.fspath(path)))


__all__ = (
    "LOCAL_FSTYPES",
    "NETWORK_FSTYPES",
    "PSEUDO_FSTYPES",
    "ROOT_VOLUME_PSEUDO",
    "ROOT_VOLUME_REMOTE",
    "ROOT_VOLUME_UNCLASSIFIED",
    "Mount",
    "MountTable",
    "VolumeKind",
    "admit_as_root",
    "classify_fstype",
    "classify_windows_drive",
    "kind_for_path",
    "parse_mountinfo",
    "read_mount_table",
)
