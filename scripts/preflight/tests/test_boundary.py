from __future__ import annotations

import os
from pathlib import Path
import stat
import unittest
from unittest import mock

from scripts.preflight.boundary import (
    BoundaryViolation,
    WorkspaceBoundary,
    WorkspaceFS,
)


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boundary = WorkspaceBoundary(WORKSPACE)
        self.fs = WorkspaceFS(self.boundary)

    def test_only_approved_workspace_roots_are_allowed(self) -> None:
        allowed = (
            WORKSPACE / "app" / "requirements.txt",
            WORKSPACE / "Reference",
            WORKSPACE / "Evidence",
        )
        for path in allowed:
            with self.subTest(path=path):
                authorized = self.boundary.authorize(path, "test")
                self.assertEqual(
                    os.path.normcase(os.fspath(authorized)),
                    os.path.normcase(os.path.normpath(os.fspath(path))),
                )

        denied = (
            WORKSPACE / "unapproved-root-file.txt",
            WORKSPACE / "app2" / "file.txt",
            WORKSPACE / "Reference-old" / "file.txt",
            WORKSPACE / "EvidenceBackup" / "file.txt",
            WORKSPACE / "app" / ".." / "Private-Local" / "file.txt",
            WORKSPACE / "app" / "file.txt:alternate-stream",
            WORKSPACE / "Evidence" / "NUL.txt",
            WORKSPACE / "Evidence" / "CONIN$.txt",
            WORKSPACE / "Evidence" / "CONOUT$.log",
            WORKSPACE / "Evidence" / "COM¹.txt",
            WORKSPACE / "Evidence" / "COM²",
            WORKSPACE / "Evidence" / "COM³.json",
            WORKSPACE / "Evidence" / "LPT¹.txt",
            WORKSPACE / "Evidence" / "LPT²",
            WORKSPACE / "Evidence" / "LPT³.json",
            Path("Z:\\outside-preflight-test\\file.txt"),
            Path("\\\\outside-preflight-test\\share\\file.txt"),
        )
        for path in denied:
            with self.subTest(path=path):
                with self.assertRaises(BoundaryViolation):
                    self.boundary.authorize(path, "test")

    def test_outside_path_is_rejected_before_target_io(self) -> None:
        outside = WORKSPACE.parent / "outside-preflight-test.txt"
        with mock.patch.object(
            Path, "read_text", side_effect=AssertionError("target I/O occurred")
        ) as read_text:
            with self.assertRaises(BoundaryViolation) as raised:
                self.fs.read_text(outside)
        read_text.assert_not_called()
        self.assertEqual(
            raised.exception.reason_code,
            "FILESYSTEM_PATH_OUTSIDE_WORKSPACE_BOUNDARY",
        )
        self.assertNotIn(str(WORKSPACE.parent), str(raised.exception))

    def test_every_filesystem_operation_rejects_outside_before_backend_io(self) -> None:
        outside = WORKSPACE.parent / "outside-preflight-test.txt"
        operations = (
            lambda: self.fs.exists(outside),
            lambda: self.fs.is_file(outside),
            lambda: self.fs.is_dir(outside),
            lambda: self.fs.read_text(outside),
            lambda: self.fs.read_bytes(outside),
            lambda: self.fs.read_json(outside),
            lambda: self.fs.mkdir(outside),
            lambda: self.fs.write_text(outside, "blocked"),
            lambda: self.fs.write_json(outside, {"blocked": True}),
            lambda: self.fs.list_children(outside),
            lambda: self.fs.find_child(
                outside, prefix="blocked", suffix=".json"
            ),
        )
        with mock.patch.object(
            Path, "exists", side_effect=AssertionError("exists backend called")
        ) as exists, mock.patch.object(
            Path, "is_file", side_effect=AssertionError("is_file backend called")
        ) as is_file, mock.patch.object(
            Path, "is_dir", side_effect=AssertionError("is_dir backend called")
        ) as is_dir, mock.patch.object(
            Path, "read_text", side_effect=AssertionError("read backend called")
        ) as read_text, mock.patch.object(
            Path, "mkdir", side_effect=AssertionError("mkdir backend called")
        ) as mkdir, mock.patch.object(
            Path, "write_text", side_effect=AssertionError("write backend called")
        ) as write_text, mock.patch(
            "scripts.preflight.boundary.os.scandir",
            side_effect=AssertionError("scandir backend called"),
        ) as scandir:
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(BoundaryViolation):
                        operation()
        for backend in (
            exists,
            is_file,
            is_dir,
            read_text,
            mkdir,
            write_text,
            scandir,
        ):
            backend.assert_not_called()

    def test_replace_rejects_blocked_paths_before_lstat_or_replace(self) -> None:
        inside = WORKSPACE / "Evidence" / "preflight" / "temp" / "inside.txt"
        outside = WORKSPACE.parent / "outside-preflight-test.txt"
        private = WORKSPACE / "Private-Local" / "blocked.txt"
        cases = (
            (outside, inside),
            (inside, outside),
            (private, inside),
            (inside, private),
        )
        with mock.patch(
            "scripts.preflight.boundary.os.lstat",
            side_effect=AssertionError("lstat backend called"),
        ) as lstat, mock.patch(
            "scripts.preflight.boundary.os.replace",
            side_effect=AssertionError("replace backend called"),
        ) as replace:
            for source, destination in cases:
                with self.subTest(source=source, destination=destination):
                    with self.assertRaises(BoundaryViolation):
                        self.fs.replace(source, destination)
        lstat.assert_not_called()
        replace.assert_not_called()

    def test_private_local_is_rejected_before_target_io(self) -> None:
        candidates = (
            WORKSPACE / "Private-Local" / "blocked.txt",
            WORKSPACE / "private-local" / "nested" / "blocked.txt",
            WORKSPACE / "app" / ".." / "Private-Local" / "blocked.txt",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with mock.patch.object(
                    Path,
                    "read_text",
                    side_effect=AssertionError("target I/O occurred"),
                ) as read_text:
                    with self.assertRaises(BoundaryViolation) as raised:
                        self.fs.read_text(candidate)
                read_text.assert_not_called()
                self.assertEqual(
                    raised.exception.reason_code,
                    "PRIVATE_LOCAL_OWNER_AUTHORIZATION_REQUIRED",
                )

    def test_access_ledger_contains_only_sanitized_paths(self) -> None:
        self.boundary.authorize(WORKSPACE / "app" / "requirements.txt", "test")
        with self.assertRaises(BoundaryViolation):
            self.boundary.authorize(
                WORKSPACE.parent / "outside-preflight-test.txt", "test"
            )
        for event in self.boundary.events:
            self.assertNotIn(str(WORKSPACE), event.path)
            self.assertNotIn(str(WORKSPACE.parent), event.path)

    def test_reparse_point_is_rejected_without_following(self) -> None:
        class FakeStat:
            def __init__(self, mode, attributes=0, links=1):
                self.st_mode = mode
                self.st_file_attributes = attributes
                self.st_nlink = links

        normal_directory = FakeStat(stat.S_IFDIR)
        reparse_directory = FakeStat(
            stat.S_IFDIR,
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400),
        )
        with mock.patch(
            "scripts.preflight.boundary.os.lstat",
            side_effect=(normal_directory, reparse_directory),
        ):
            with self.assertRaises(BoundaryViolation) as raised:
                self.boundary.authorize(
                    WORKSPACE / "app" / "reparse-target" / "file.txt",
                    "test",
                )
        self.assertEqual(
            "WORKSPACE_REPARSE_POINT_REJECTED",
            raised.exception.reason_code,
        )

    def test_read_bytes_is_exact_and_optionally_bounded(self) -> None:
        fixture = (
            WORKSPACE
            / "app"
            / "scripts"
            / "preflight"
            / "tests"
            / "fixtures"
            / "duplicate-key.json"
        )
        expected = fixture.read_bytes()
        self.assertEqual(expected, self.fs.read_bytes(fixture))
        self.assertEqual(
            expected,
            self.fs.read_bytes(fixture, max_bytes=len(expected)),
        )
        with self.assertRaisesRegex(
            ValueError, "BINARY_INPUT_EXCEEDS_SIZE_LIMIT"
        ):
            self.fs.read_bytes(fixture, max_bytes=len(expected) - 1)
        with self.assertRaisesRegex(ValueError, "BINARY_SIZE_LIMIT_INVALID"):
            self.fs.read_bytes(fixture, max_bytes=-1)

    def test_read_text_is_exact_and_preallocation_bounded(self) -> None:
        fixture = (
            WORKSPACE
            / "app"
            / "scripts"
            / "preflight"
            / "tests"
            / "fixtures"
            / "duplicate-key.json"
        )
        expected = fixture.read_text(encoding="utf-8")
        self.assertEqual(
            expected,
            self.fs.read_text(fixture, max_chars=len(expected)),
        )
        with self.assertRaisesRegex(
            ValueError,
            "TEXT_INPUT_EXCEEDS_SIZE_LIMIT",
        ):
            self.fs.read_text(fixture, max_chars=len(expected) - 1)
        with self.assertRaisesRegex(ValueError, "TEXT_SIZE_LIMIT_INVALID"):
            self.fs.read_text(fixture, max_chars=-1)

    def test_exclusive_creation_uses_create_and_exclusive_flags(self) -> None:
        target = (
            WORKSPACE
            / "Evidence"
            / "preflight"
            / "temp"
            / "boundary-exclusive-unit-test.txt"
        )

        def complete_write(_descriptor, content):
            return len(content)

        with mock.patch.object(self.fs, "mkdir") as mkdir, mock.patch(
            "scripts.preflight.boundary.os.open",
            return_value=41,
        ) as open_file, mock.patch(
            "scripts.preflight.boundary.os.write",
            side_effect=complete_write,
        ) as write, mock.patch(
            "scripts.preflight.boundary.os.close"
        ) as close:
            created = self.fs.create_text_exclusive(target, "exact\n")

        self.assertEqual(target, created)
        mkdir.assert_called_once_with(target.parent)
        flags = open_file.call_args.args[1]
        self.assertTrue(flags & os.O_CREAT)
        self.assertTrue(flags & os.O_EXCL)
        write.assert_called_once()
        self.assertEqual(b"exact\n", bytes(write.call_args.args[1]))
        close.assert_called_once_with(41)

    def test_exclusive_json_creation_is_deterministic_and_never_overwrites(self) -> None:
        target = (
            WORKSPACE
            / "Evidence"
            / "preflight"
            / "temp"
            / "boundary-exclusive-unit-test.json"
        )
        captured: list[bytes] = []

        def capture_write(_descriptor, content):
            captured.append(bytes(content))
            return len(content)

        with mock.patch.object(self.fs, "mkdir"), mock.patch(
            "scripts.preflight.boundary.os.open",
            return_value=42,
        ), mock.patch(
            "scripts.preflight.boundary.os.write",
            side_effect=capture_write,
        ), mock.patch(
            "scripts.preflight.boundary.os.close"
        ):
            self.fs.create_json_exclusive(target, {"b": 2, "a": 1})
        self.assertEqual(
            [b'{\n  "a": 1,\n  "b": 2\n}\n'],
            captured,
        )

        with mock.patch.object(self.fs, "mkdir"), mock.patch(
            "scripts.preflight.boundary.os.open",
            side_effect=FileExistsError("already exists"),
        ) as open_file, mock.patch(
            "scripts.preflight.boundary.os.write"
        ) as write:
            with self.assertRaises(FileExistsError):
                self.fs.create_json_exclusive(target, {"b": 2, "a": 1})
        open_file.assert_called_once()
        write.assert_not_called()

    def test_exclusive_creation_rejects_blocked_path_before_lstat_or_open(
        self,
    ) -> None:
        candidates = (
            WORKSPACE.parent / "outside-preflight-test.txt",
            WORKSPACE / "Private-Local" / "blocked.txt",
        )
        with mock.patch(
            "scripts.preflight.boundary.os.lstat",
            side_effect=AssertionError("lstat backend called"),
        ) as lstat, mock.patch(
            "scripts.preflight.boundary.os.open",
            side_effect=AssertionError("open backend called"),
        ) as open_file:
            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    with self.assertRaises(BoundaryViolation):
                        self.fs.create_text_exclusive(candidate, "blocked")
        lstat.assert_not_called()
        open_file.assert_not_called()

    def test_json_duplicate_keys_are_rejected(self) -> None:
        fixture = (
            WORKSPACE
            / "app"
            / "scripts"
            / "preflight"
            / "tests"
            / "fixtures"
            / "duplicate-key.json"
        )
        with self.assertRaises(ValueError) as raised:
            self.fs.read_json(fixture)
        self.assertIn("JSON_DUPLICATE_KEY_REJECTED", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
