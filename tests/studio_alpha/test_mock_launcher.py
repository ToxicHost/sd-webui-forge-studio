"""The double-click mock shortcut reaches the mock parser cleanly."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


APP_ROOT = Path(__file__).resolve().parents[2]


class MockLauncherTests(unittest.TestCase):
    def test_wrapper_config_is_not_forwarded_to_the_mock_parser(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(APP_ROOT / "launch_studio.py"),
                "--config",
                str(APP_ROOT.parent / "studio-config.json"),
                "--mock",
                "--demo",
            ],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)

    def test_equals_form_is_also_removed(self) -> None:
        import launch_studio

        self.assertEqual(
            ["--demo", "--port", "0"],
            launch_studio._mock_arguments(
                ["--config=C:/tmp/studio.json", "--demo", "--port", "0"]
            ),
        )

    def test_missing_config_value_is_refused_deliberately(self) -> None:
        import launch_studio

        with self.assertRaisesRegex(SystemExit, "--config requires a path"):
            launch_studio._mock_arguments(["--demo", "--config"])


if __name__ == "__main__":
    unittest.main()
