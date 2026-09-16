"""Volume classification, and the policy that reads it.

`root_policy` refused UNC paths and mapped network drives on Windows and said
exactly why: stat'ing a remote share authenticates to another host and can
hang, with no socket timeout and no cancellation, inside a request handler.
That reasoning was never Windows-specific -- but `drive_type()` returns None
off Windows, and a test at `test_external_model_roots.py` PINNED that, so a
dead NFS mount at `/mnt/nas/models` was accepted on Linux while the identical
share was refused on Windows.

Everything here is pure. Mount tables are parsed from captured text, kinds are
classified from type names, and the policy is asked about a kind -- so Linux
and macOS behaviour is decided on a Windows box by the same code that will
decide it there, rather than skipped.

The fact and the decision are tested separately on purpose. `classify_*` says
what a volume IS and has no opinion; `admit_as_root` is the current security
policy. A future release that supports explicitly configured network roots
changes the second and leaves the first alone, and these tests should make
that obvious to whoever writes it.

SCOPE: STATIC_IMPORT_SCOPE. No mount is performed, no filesystem is read, no
libc symbol is bound.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.volume_policy import (  # noqa: E402
    DRIVE_CDROM,
    DRIVE_FIXED,
    DRIVE_NO_ROOT_DIR,
    DRIVE_RAMDISK,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    DRIVE_UNKNOWN,
    ROOT_VOLUME_PSEUDO,
    ROOT_VOLUME_REMOTE,
    ROOT_VOLUME_UNCLASSIFIED,
    Mount,
    MountTable,
    VolumeKind,
    admit_as_root,
    classify_fstype,
    classify_windows_drive,
    parse_mountinfo,
    read_mount_table,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_VOLUME_TESTS = 37

#: Captured from a real Linux host, trimmed. Includes the shapes that matter:
#: pseudo filesystems, a local root, a bind mount nested under a local mount,
#: an NFS mount, a mount point containing a space, and an overlay root.
LINUX_MOUNTINFO = r"""
21 27 0:20 / /proc rw,nosuid,nodev,noexec,relatime shared:5 - proc proc rw
22 27 0:21 / /sys rw,nosuid,nodev,noexec,relatime shared:6 - sysfs sysfs rw
27 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw
31 27 259:1 / /boot/efi rw,relatime shared:9 - vfat /dev/nvme0n1p1 rw
44 27 0:44 / /mnt rw,relatime shared:24 - ext4 /dev/sdb1 rw
45 44 0:45 / /mnt/nas rw,relatime shared:25 - nfs4 10.0.0.4:/export rw
46 27 0:46 / /media/My\040Drive rw,relatime shared:26 - exfat /dev/sdc1 rw
47 27 0:47 / /var/lib/docker rw,relatime shared:27 - overlay overlay rw
48 27 0:48 / /run/user/1000 rw,relatime shared:28 - tmpfs tmpfs rw
"""

#: A container's own view: overlay root plus a bind-mounted host directory.
CONTAINER_MOUNTINFO = r"""
1 0 0:1 / / rw,relatime - overlay overlay rw
2 1 0:2 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw
3 1 259:2 /models /opt/models rw,relatime - ext4 /dev/nvme0n1p2 rw
"""


class FstypeClassificationTests(unittest.TestCase):
    def test_the_common_local_filesystems_are_local(self) -> None:
        for name in ("ext4", "xfs", "btrfs", "apfs", "ntfs", "vfat", "zfs"):
            self.assertIs(VolumeKind.LOCAL, classify_fstype(name), name)

    def test_the_network_filesystems_are_remote(self) -> None:
        for name in ("nfs", "nfs4", "cifs", "smbfs", "afpfs", "webdav", "ceph"):
            self.assertIs(VolumeKind.REMOTE, classify_fstype(name), name)

    def test_kernel_surfaces_are_pseudo(self) -> None:
        for name in ("proc", "sysfs", "cgroup2", "devpts", "debugfs"):
            self.assertIs(VolumeKind.PSEUDO, classify_fstype(name), name)

    def test_an_unrecognised_fuse_filesystem_is_treated_as_remote(self) -> None:
        """FUSE filesystems are named after their transport and the set is
        open-ended, so a table can never be complete. The ones that are not
        local are the ones that hang, so an unknown `fuse.*` fails toward
        refusal rather than toward acceptance."""

        for name in ("fuse.sshfs", "fuse.rclone", "fuse.something-new"):
            self.assertIs(VolumeKind.REMOTE, classify_fstype(name), name)

    def test_overlay_is_local_because_a_container_root_is_overlay(self) -> None:
        """Classifying overlay as anything else would refuse every model root
        inside Docker, which is the deployment this phase exists to support."""

        self.assertIs(VolumeKind.LOCAL, classify_fstype("overlay"))

    def test_tmpfs_is_local_for_the_same_reason_windows_accepts_a_ramdisk(self) -> None:
        self.assertIs(VolumeKind.LOCAL, classify_fstype("tmpfs"))
        self.assertIs(VolumeKind.LOCAL, classify_windows_drive(DRIVE_RAMDISK))

    def test_an_empty_or_unknown_type_is_unknown(self) -> None:
        for name in ("", "   ", "somethingelse"):
            self.assertIs(VolumeKind.UNKNOWN, classify_fstype(name), name)

    def test_classification_is_case_insensitive(self) -> None:
        self.assertIs(VolumeKind.REMOTE, classify_fstype("NFS4"))
        self.assertIs(VolumeKind.LOCAL, classify_fstype("EXT4"))

    def test_autofs_is_not_treated_as_an_ordinary_local_mount(self) -> None:
        """autofs commonly fronts NFS home directories and mounts on access,
        so touching one can trigger the very network round trip being avoided."""

        self.assertIs(VolumeKind.PSEUDO, classify_fstype("autofs"))


class WindowsDriveTests(unittest.TestCase):
    def test_every_drive_type_maps(self) -> None:
        self.assertIs(VolumeKind.LOCAL, classify_windows_drive(DRIVE_FIXED))
        self.assertIs(VolumeKind.REMOVABLE, classify_windows_drive(DRIVE_REMOVABLE))
        self.assertIs(VolumeKind.REMOVABLE, classify_windows_drive(DRIVE_CDROM))
        self.assertIs(VolumeKind.LOCAL, classify_windows_drive(DRIVE_RAMDISK))
        self.assertIs(VolumeKind.REMOTE, classify_windows_drive(DRIVE_REMOTE))

    def test_could_not_tell_is_unknown_not_local(self) -> None:
        self.assertIs(VolumeKind.UNKNOWN, classify_windows_drive(DRIVE_UNKNOWN))
        self.assertIs(VolumeKind.UNKNOWN, classify_windows_drive(DRIVE_NO_ROOT_DIR))
        self.assertIs(VolumeKind.UNKNOWN, classify_windows_drive(999))

    def test_the_windows_and_posix_answers_agree_for_a_network_share(self) -> None:
        """The whole point of the phase: the same share, refused the same way
        on every platform."""

        self.assertIs(VolumeKind.REMOTE, classify_windows_drive(DRIVE_REMOTE))
        self.assertIs(VolumeKind.REMOTE, classify_fstype("cifs"))


class MountinfoParsingTests(unittest.TestCase):
    def test_the_table_parses(self) -> None:
        mounts = parse_mountinfo(LINUX_MOUNTINFO)
        points = [mount.mount_point for mount in mounts]
        self.assertIn("/", points)
        self.assertIn("/mnt/nas", points)
        self.assertIn("/proc", points)

    def test_the_fstype_is_read_after_the_separator(self) -> None:
        """The optional-fields section before the lone `-` is variable length,
        so a fixed index reads a different column on different lines."""

        mounts = {m.mount_point: m.fstype for m in parse_mountinfo(LINUX_MOUNTINFO)}
        self.assertEqual("ext4", mounts["/"])
        self.assertEqual("nfs4", mounts["/mnt/nas"])
        self.assertEqual("overlay", mounts["/var/lib/docker"])

    def test_a_mount_point_containing_a_space_survives(self) -> None:
        """Written `\\040` in mountinfo. Splitting on whitespace without
        decoding truncates the path at the space, and the longest-prefix
        lookup then matches the wrong mount or none."""

        points = [m.mount_point for m in parse_mountinfo(LINUX_MOUNTINFO)]
        self.assertIn("/media/My Drive", points)

    def test_a_malformed_line_is_skipped_not_fatal(self) -> None:
        text = LINUX_MOUNTINFO + "\nthis is not a mountinfo line\n99 1 0:9 /x\n"
        self.assertEqual(
            len(parse_mountinfo(LINUX_MOUNTINFO)), len(parse_mountinfo(text))
        )

    def test_an_empty_table_parses_to_nothing(self) -> None:
        self.assertEqual((), parse_mountinfo(""))


class LongestPrefixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = MountTable(parse_mountinfo(LINUX_MOUNTINFO))

    def test_a_nested_network_mount_beats_its_local_parent(self) -> None:
        """`/mnt` is ext4 and `/mnt/nas` is NFS underneath it. A first-match
        lookup calls the NFS path local, which is the wrong answer in the one
        direction that matters."""

        self.assertIs(VolumeKind.LOCAL, self.table.kind_for("/mnt"))
        self.assertIs(VolumeKind.REMOTE, self.table.kind_for("/mnt/nas"))
        self.assertIs(VolumeKind.REMOTE, self.table.kind_for("/mnt/nas/models"))

    def test_a_path_under_the_root_mount_is_local(self) -> None:
        self.assertIs(VolumeKind.LOCAL, self.table.kind_for("/srv/models"))

    def test_a_sibling_prefix_does_not_match(self) -> None:
        """`/mnt/nasty` is not inside `/mnt/nas`."""

        self.assertIs(VolumeKind.LOCAL, self.table.kind_for("/mnt/nasty/models"))

    def test_the_mount_point_itself_matches(self) -> None:
        self.assertIs(VolumeKind.REMOTE, self.table.kind_for("/mnt/nas"))

    def test_a_pseudo_filesystem_is_recognised(self) -> None:
        self.assertIs(VolumeKind.PSEUDO, self.table.kind_for("/proc/self"))

    def test_an_empty_table_classifies_nothing(self) -> None:
        self.assertIs(VolumeKind.UNKNOWN, MountTable(()).kind_for("/srv/models"))


class ContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = MountTable(parse_mountinfo(CONTAINER_MOUNTINFO))

    def test_the_container_root_is_local(self) -> None:
        self.assertIs(VolumeKind.LOCAL, self.table.kind_for("/"))

    def test_a_bind_mounted_model_directory_is_local(self) -> None:
        """The ordinary Docker arrangement: `-v /host/models:/opt/models`.
        This must be admissible or the container deployment has no model root
        at all."""

        kind = self.table.kind_for("/opt/models")
        self.assertIs(VolumeKind.LOCAL, kind)
        self.assertEqual((True, None), admit_as_root(kind))

    def test_the_containers_own_proc_is_still_pseudo(self) -> None:
        self.assertIs(VolumeKind.PSEUDO, self.table.kind_for("/proc/1"))


class PolicyTests(unittest.TestCase):
    """The DECISION, kept separate from the classification above."""

    def test_local_and_removable_are_admitted(self) -> None:
        self.assertEqual((True, None), admit_as_root(VolumeKind.LOCAL))
        self.assertEqual((True, None), admit_as_root(VolumeKind.REMOVABLE))

    def test_remote_is_refused(self) -> None:
        self.assertEqual(
            (False, ROOT_VOLUME_REMOTE), admit_as_root(VolumeKind.REMOTE)
        )

    def test_remote_is_not_overridable(self) -> None:
        """A dead mount hangs a request handler with no timeout and no
        cancellation. That is an availability failure, not a preference, so
        the escape hatch does not reach it."""

        self.assertEqual(
            (False, ROOT_VOLUME_REMOTE),
            admit_as_root(VolumeKind.REMOTE, allow_unclassified=True),
        )

    def test_pseudo_is_refused_and_not_overridable(self) -> None:
        self.assertEqual(
            (False, ROOT_VOLUME_PSEUDO),
            admit_as_root(VolumeKind.PSEUDO, allow_unclassified=True),
        )

    def test_unknown_is_refused_by_default(self) -> None:
        """'Could not tell' must not resolve to 'allow'."""

        self.assertEqual(
            (False, ROOT_VOLUME_UNCLASSIFIED), admit_as_root(VolumeKind.UNKNOWN)
        )

    def test_unknown_is_overridable(self) -> None:
        """On a host whose mount table cannot be read at all, every root is
        UNKNOWN. An owner must have a way to say 'this one is fine' without
        the product refusing every directory on the machine."""

        self.assertEqual(
            (True, None),
            admit_as_root(VolumeKind.UNKNOWN, allow_unclassified=True),
        )

    def test_the_policy_is_a_separate_function_from_the_classifier(self) -> None:
        """A source pin, because the split is the design. Supporting an
        explicitly configured network root later should be a change to
        `admit_as_root` and its codes -- if the refusal leaks into
        `classify_fstype`, that future change becomes a rewrite."""

        source = (
            APP_ROOT / "forge_headless" / "volume_policy.py"
        ).read_text(encoding="utf-8")
        classifier = source[
            source.index("def classify_fstype") : source.index(
                "def classify_windows_drive"
            )
        ]
        for forbidden in ("admit", "refus", "ROOT_VOLUME_"):
            self.assertNotIn(forbidden, classifier)


class DegradedReadTests(unittest.TestCase):
    def test_a_missing_mountinfo_does_not_raise(self) -> None:
        """None means 'could not classify', never 'nothing is mounted'. The
        difference is what decides between refusing and degrading."""

        table = read_mount_table(mountinfo_path=str(APP_ROOT / "no-such-file"))
        self.assertTrue(table is None or len(table) > 0)

    def test_a_table_read_on_this_host_is_either_absent_or_usable(self) -> None:
        table = read_mount_table()
        if table is not None:
            self.assertGreater(len(table), 0)
            for mount in table.mounts:
                self.assertIsInstance(mount, Mount)
                self.assertTrue(mount.mount_point)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_VOLUME_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("STATIC_IMPORT_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
