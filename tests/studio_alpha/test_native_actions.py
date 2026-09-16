"""Reveal: capability by artifact, and a launch that cannot become a shell.

Two properties, and they pull in opposite directions.

The capability must be honest. A container and a headless server have no file
manager, and an owner there should see a disabled control rather than an
action that fails when pressed. Nothing here asks what the operating system is
CALLED -- a Linux desktop and a Linux box over SSH share a name and do not
share an answer, which is exactly why `forge_studio` is banned from reading
`sys.platform` and why that ban is a help rather than an obstacle.

The launch must stay narrow. It is one action, open this directory, with three
vetted executables and one argument the server already resolved. There is no
`shell=True`, no command string, no executable named by the browser, and no
path interpolated into anything.

Every host shape is INJECTED. No test on this machine starts a file manager,
and no test skips because the host is not macOS.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No process is spawned;
the spawn function is replaced. Directories are disposable temporaries.
"""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_studio.native_actions import (  # noqa: E402
    MAX_REVEALS_PER_WINDOW,
    REVEAL_RATE_LIMITED,
    REVEAL_TARGET_INVALID,
    REVEAL_UNAVAILABLE,
    HostProbe,
    NativeActions,
    detect_reveal,
    probe_host,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_NATIVE_TESTS = 26

WINDOWS = HostProbe(
    is_windows=True, system_root="C:\\Windows", windows_explorer_exists=True
)
MACOS = HostProbe(macos_open_exists=True)
LINUX_DESKTOP = HostProbe(linux_opener="/usr/bin/xdg-open", has_display=True)
LINUX_GIO = HostProbe(linux_opener="/usr/bin/gio", has_display=True)
LINUX_HEADLESS = HostProbe(linux_opener="/usr/bin/xdg-open", has_display=False)
CONTAINER = HostProbe()
WINDOWS_WITHOUT_SHELL = HostProbe(is_windows=True, system_root="C:\\Windows")


class _Spawned:
    """Records the argument vector. Starts nothing."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return None


class CapabilityTests(unittest.TestCase):
    def test_windows_with_a_shell_can_reveal(self) -> None:
        capability = detect_reveal(WINDOWS)
        self.assertTrue(capability.available)
        self.assertEqual("explorer", capability.mechanism)

    def test_macos_can_reveal(self) -> None:
        self.assertEqual("finder", detect_reveal(MACOS).mechanism)

    def test_a_linux_desktop_can_reveal(self) -> None:
        self.assertEqual("desktop", detect_reveal(LINUX_DESKTOP).mechanism)

    def test_a_headless_linux_box_cannot(self) -> None:
        """`xdg-open` is installed on plenty of servers with nothing to open
        onto. Trying anyway would fail slowly and confusingly."""

        capability = detect_reveal(LINUX_HEADLESS)
        self.assertFalse(capability.available)
        self.assertIn("desktop", (capability.reason or "").lower())

    def test_a_container_cannot(self) -> None:
        self.assertFalse(detect_reveal(CONTAINER).available)

    def test_windows_without_explorer_cannot(self) -> None:
        """Windows Server core, and the case that makes 'os.name == nt implies
        Explorer' wrong."""

        self.assertFalse(detect_reveal(WINDOWS_WITHOUT_SHELL).available)

    def test_the_mechanism_is_never_an_os_name(self) -> None:
        """The page shows this to say WHERE the window opens. An OS name
        would be both less useful and a fact about the host we do not need to
        publish."""

        for probe in (WINDOWS, MACOS, LINUX_DESKTOP):
            mechanism = detect_reveal(probe).mechanism or ""
            for name in ("windows", "linux", "darwin", "macos", "nt", "posix"):
                self.assertNotIn(name, mechanism.lower())

    def test_an_unavailable_capability_carries_a_reason(self) -> None:
        for probe in (CONTAINER, LINUX_HEADLESS, WINDOWS_WITHOUT_SHELL):
            self.assertTrue(detect_reveal(probe).reason)

    def test_probing_this_host_does_not_raise(self) -> None:
        self.assertIsInstance(detect_reveal(probe_host()).available, bool)


class ArgumentVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="reveal-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def actions(self, probe: HostProbe) -> tuple[NativeActions, _Spawned]:
        spawned = _Spawned()
        return NativeActions(probe=probe, spawn=spawned), spawned

    def test_windows_uses_explorer_under_system_root(self) -> None:
        actions, spawned = self.actions(WINDOWS)
        actions.reveal(self.directory)
        self.assertEqual(
            [str(Path("C:\\Windows") / "explorer.exe"), str(self.directory)],
            spawned.calls[0],
        )

    def test_macos_uses_the_absolute_open_binary(self) -> None:
        actions, spawned = self.actions(MACOS)
        actions.reveal(self.directory)
        self.assertEqual(["/usr/bin/open", str(self.directory)], spawned.calls[0])

    def test_gio_takes_its_open_subcommand(self) -> None:
        actions, spawned = self.actions(LINUX_GIO)
        actions.reveal(self.directory)
        self.assertEqual(
            ["/usr/bin/gio", "open", str(self.directory)], spawned.calls[0]
        )

    def test_the_path_is_one_argument_never_interpolated(self) -> None:
        """A directory whose name contains a quote, a space and a semicolon.
        Under a shell that would be three commands; as an argv element it is
        one directory."""

        awkward = self.directory / "a b; rm -rf x & q"
        awkward.mkdir()
        actions, spawned = self.actions(LINUX_DESKTOP)
        actions.reveal(awkward)
        self.assertEqual(str(awkward), spawned.calls[0][-1])
        self.assertEqual(2, len(spawned.calls[0]))

    def test_the_executable_never_comes_from_the_caller(self) -> None:
        """Every element but the path is chosen inside `_argv`."""

        actions, spawned = self.actions(WINDOWS)
        actions.reveal(self.directory)
        self.assertTrue(spawned.calls[0][0].endswith("explorer.exe"))


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="reveal-refuse-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)

    def test_an_unavailable_host_refuses_rather_than_trying(self) -> None:
        actions = NativeActions(probe=CONTAINER, spawn=_Spawned())
        with self.assertRaises(HeadlessError) as caught:
            actions.reveal(self.directory)
        self.assertEqual(REVEAL_UNAVAILABLE, caught.exception.code)

    def test_a_vanished_directory_is_refused(self) -> None:
        """Revalidated immediately before the call. A handle minted a minute
        ago is not evidence about what this path is now."""

        target = self.directory / "gone"
        target.mkdir()
        actions = NativeActions(probe=WINDOWS, spawn=_Spawned())
        shutil.rmtree(target, ignore_errors=True)
        with self.assertRaises(HeadlessError) as caught:
            actions.reveal(target)
        self.assertEqual(REVEAL_TARGET_INVALID, caught.exception.code)

    def test_a_file_is_not_a_folder(self) -> None:
        target = self.directory / "a.txt"
        target.write_bytes(b"x")
        actions = NativeActions(probe=WINDOWS, spawn=_Spawned())
        with self.assertRaises(HeadlessError) as caught:
            actions.reveal(target)
        self.assertEqual(REVEAL_TARGET_INVALID, caught.exception.code)

    def test_a_relative_path_is_refused(self) -> None:
        actions = NativeActions(probe=WINDOWS, spawn=_Spawned())
        with self.assertRaises(HeadlessError):
            actions.reveal(Path("models"))

    def test_reveals_are_rate_limited(self) -> None:
        """A click opens a window on the owner's desktop. A loop would open
        hundreds."""

        clock = {"now": 0.0}
        actions = NativeActions(
            probe=WINDOWS, spawn=_Spawned(), clock=lambda: clock["now"]
        )
        for _ in range(MAX_REVEALS_PER_WINDOW):
            actions.reveal(self.directory)
        with self.assertRaises(HeadlessError) as caught:
            actions.reveal(self.directory)
        self.assertEqual(REVEAL_RATE_LIMITED, caught.exception.code)

    def test_the_limit_is_a_window_not_a_total(self) -> None:
        clock = {"now": 0.0}
        actions = NativeActions(
            probe=WINDOWS, spawn=_Spawned(), clock=lambda: clock["now"]
        )
        for _ in range(MAX_REVEALS_PER_WINDOW):
            actions.reveal(self.directory)
        clock["now"] = 60.0
        actions.reveal(self.directory)

    def test_a_spawn_failure_is_a_named_refusal(self) -> None:
        def explode(_argv):
            raise OSError("no such executable")

        actions = NativeActions(probe=WINDOWS, spawn=explode)
        with self.assertRaises(HeadlessError):
            actions.reveal(self.directory)


class SourcePolicyTests(unittest.TestCase):
    """Pins on the launch, because the dangerous edit is a plausible one."""

    SOURCE = (APP_ROOT / "forge_studio" / "native_actions.py").read_text(
        encoding="utf-8"
    )

    def test_no_shell_execution_anywhere(self) -> None:
        """Checked over the AST, not the text.

        The text form failed on this module's own docstring, which says
        'no shell=True' -- the same trap  solved the
        same way. A rule stated in prose must not trip the check that
        enforces it.
        """

        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        self.assertIsInstance(keyword.value, ast.Constant)
                        self.assertIs(False, keyword.value.value)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotEqual((node.value.id, node.attr), ("os", "system"))

    def test_the_only_spawn_passes_a_list(self) -> None:
        """Checked over the AST: `Popen` must receive a list, never a string.
        A string argument is the shape that becomes a shell command."""

        tree = ast.parse(self.SOURCE)
        popens = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
        ]
        self.assertEqual(1, len(popens))
        self.assertTrue(popens[0].args)
        self.assertIsInstance(popens[0].args[0], ast.Name)

    def test_the_module_does_not_read_the_platform_name(self) -> None:
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                self.assertNotIn(
                    (node.value.id, node.attr),
                    {("sys", "platform"), ("platform", "system")},
                )


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_NATIVE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
