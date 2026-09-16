from __future__ import annotations

import os
from pathlib import Path
import stat
import unittest
from unittest import mock

from scripts.preflight import bootstrap, pip_bootstrap


WORKSPACE = Path(os.path.abspath(__file__)).parents[4]


class FakeStat:
    def __init__(
        self,
        mode: int,
        *,
        attributes: int = 0,
        links: int = 1,
    ) -> None:
        self.st_mode = mode
        self.st_file_attributes = attributes
        self.st_nlink = links


class BootstrapPathValidationTests(unittest.TestCase):
    def test_required_path_rejects_reparse_without_followup_io(self) -> None:
        reparse_directory = FakeStat(
            stat.S_IFDIR,
            attributes=getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
            ),
        )
        with mock.patch.object(
            bootstrap.os, "lstat", return_value=reparse_directory
        ) as lstat, mock.patch.object(
            bootstrap.os,
            "scandir",
            side_effect=AssertionError("scandir followed a reparse point"),
        ) as scandir:
            with self.assertRaises(bootstrap.BootstrapPathViolation) as raised:
                bootstrap._validate_tree(WORKSPACE / "app")
        self.assertEqual(
            "BOOTSTRAP_REPARSE_POINT_REJECTED",
            raised.exception.reason_code,
        )
        lstat.assert_called_once()
        scandir.assert_not_called()

    def test_required_file_rejects_hard_link(self) -> None:
        hard_link = FakeStat(stat.S_IFREG, links=2)
        with mock.patch.object(
            bootstrap.os, "lstat", return_value=hard_link
        ):
            with self.assertRaises(bootstrap.BootstrapPathViolation) as raised:
                bootstrap._checked_lstat(
                    WORKSPACE / "app" / "scripts" / "preflight" / "bootstrap.py",
                    expected_kind="file",
                )
        self.assertEqual(
            "BOOTSTRAP_HARD_LINK_REJECTED",
            raised.exception.reason_code,
        )

    def test_import_shadow_rejects_reparse(self) -> None:
        reparse_file = FakeStat(
            stat.S_IFREG,
            attributes=getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
            ),
        )
        with mock.patch.object(
            bootstrap.os, "lstat", return_value=reparse_file
        ):
            with self.assertRaises(bootstrap.BootstrapPathViolation) as raised:
                bootstrap._reject_import_shadow(
                    WORKSPACE / "app" / "packaging.py"
                )
        self.assertEqual(
            "BOOTSTRAP_REPARSE_POINT_REJECTED",
            raised.exception.reason_code,
        )

    def test_pip_bootstrap_rejects_reparse_before_scanning(self) -> None:
        reparse_directory = FakeStat(
            stat.S_IFDIR,
            attributes=getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
            ),
        )
        with mock.patch.object(
            pip_bootstrap.os,
            "lstat",
            return_value=reparse_directory,
        ), mock.patch.object(
            pip_bootstrap.os,
            "scandir",
            side_effect=AssertionError("scandir followed a reparse point"),
        ) as scandir:
            with self.assertRaises(RuntimeError) as raised:
                pip_bootstrap._validate_tree(
                    WORKSPACE / "app" / "venv" / "Lib" / "site-packages" / "pip",
                    allowed_root=WORKSPACE / "app",
                )
        self.assertEqual(
            "PIP_BOOTSTRAP_REPARSE_POINT_REJECTED",
            str(raised.exception),
        )
        scandir.assert_not_called()

    def test_pip_bootstrap_rejects_import_shadow(self) -> None:
        regular_file = FakeStat(stat.S_IFREG)
        with mock.patch.object(
            pip_bootstrap.os,
            "lstat",
            return_value=regular_file,
        ):
            with self.assertRaises(RuntimeError) as raised:
                pip_bootstrap._reject_import_shadow(
                    WORKSPACE / "app" / "venv" / "Lib" / "site-packages" / "pip.py"
                )
        self.assertEqual(
            "PIP_BOOTSTRAP_IMPORT_SHADOW_REJECTED",
            str(raised.exception),
        )

    def test_pip_config_owner_sentinel_is_normalized_before_import(self) -> None:
        with mock.patch.dict(
            pip_bootstrap.os.environ,
            {"PIP_CONFIG_FILE": "NUL"},
            clear=True,
        ), mock.patch.object(pip_bootstrap.os, "devnull", "nul"):
            pip_bootstrap._normalize_pip_config_file()
            self.assertEqual("nul", pip_bootstrap.os.environ["PIP_CONFIG_FILE"])

        with mock.patch.dict(
            pip_bootstrap.os.environ,
            {"PIP_CONFIG_FILE": "nul"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "PIP_BOOTSTRAP_CONFIG_SENTINEL_REJECTED",
            ):
                pip_bootstrap._normalize_pip_config_file()

    def test_pip_bootstrap_direct_execution_is_unconditionally_blocked(
        self,
    ) -> None:
        with mock.patch.object(
            pip_bootstrap,
            "_validate_pip_argv",
            side_effect=AssertionError("argv validation reached"),
        ) as validate_argv, mock.patch.object(
            pip_bootstrap.runpy,
            "run_path",
            side_effect=AssertionError("pip execution reached"),
        ) as run_path, mock.patch.object(
            pip_bootstrap.os,
            "lstat",
            side_effect=AssertionError("filesystem traversal reached"),
        ) as lstat:
            with self.assertRaises(RuntimeError) as raised:
                pip_bootstrap.main()
        self.assertEqual(
            (
                "PIP_BOOTSTRAP_EXECUTION_DISABLED:"
                "OWNER_SIGNATURE_AND_PREVENTIVE_CONTAINMENT_REQUIRED"
            ),
            str(raised.exception),
        )
        validate_argv.assert_not_called()
        run_path.assert_not_called()
        lstat.assert_not_called()

    def test_pip_bootstrap_does_not_add_general_site_packages(self) -> None:
        source = (
            WORKSPACE
            / "app"
            / "scripts"
            / "preflight"
            / "pip_bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("sys.path.insert", source)
        self.assertNotIn("sys.path.append", source)
        self.assertIn("spec_from_file_location", source)

    def test_main_bootstrap_seals_scripts_namespace(self) -> None:
        source = (
            WORKSPACE
            / "app"
            / "scripts"
            / "preflight"
            / "bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("sys.path.insert", source)
        self.assertNotIn("sys.path.append", source)
        self.assertIn('ModuleSpec("scripts"', source)
        self.assertIn('sys.modules["scripts"]', source)
        self.assertIn('spec_from_file_location(\n        "packaging"', source)


if __name__ == "__main__":
    unittest.main()
