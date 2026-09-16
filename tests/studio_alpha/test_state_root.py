"""D1 -- where Studio's writable state lives.

Owner decision, 2026-08-11: one configurable `STUDIO_STATE_ROOT`; native
installs default to the platform-conventional per-user application-data
location; portable and Docker deployments may explicitly override; the internal
layout is platform-independent; install directories, model roots and result
roots are not the state root.

Every input is injected, so the macOS and Linux answers are asserted from
Windows. That is not a convenience -- it is the only way this matrix gets
tested at all before the product has ever run on those platforms, and the
standing cross-platform requirement says architecture must be compatible now
rather than at release cleanup.

What these tests deliberately do NOT assert: that any resolved path exists, is
writable, or survives a reboot. Resolving a root is not a promise that it
works, and this module touches no filesystem.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.state_root import (  # noqa: E402
    CONFIG_OVERRIDE,
    ENVIRONMENT_OVERRIDE,
    LINUX,
    MACOS,
    PLATFORM_DEFAULT,
    POSIX_FALLBACK,
    STATE_ROOT_ENV,
    UNKNOWN_PLATFORM,
    WINDOWS,
    StateRootError,
    refuse_conflicting_root,
    resolve_state_root,
)

EXPECTED_STATE_ROOT_TESTS = 32


def _resolve(system, environ=None, home="/home/o", config_value=None):
    return resolve_state_root(
        system=system,
        environ=environ or {},
        home=home,
        config_value=config_value,
    )


class PlatformDefaultTests(unittest.TestCase):
    def test_windows_uses_localappdata(self) -> None:
        """LOCALAPPDATA, not APPDATA.

        Roaming profiles synchronise. This directory holds a database, caches
        and machine-specific state; replicating that across machines is a
        corruption risk, not a feature.
        """

        resolution = _resolve(
            "Windows",
            {"LOCALAPPDATA": r"C:\Users\o\AppData\Local"},
            home=r"C:\Users\o",
        )
        self.assertEqual(r"C:\Users\o\AppData\Local\ForgeStudio", resolution.root)
        self.assertEqual(PLATFORM_DEFAULT, resolution.source)
        self.assertEqual(WINDOWS, resolution.platform)
        self.assertFalse(resolution.explicit)

    def test_windows_falls_back_to_the_home_appdata_path(self) -> None:
        resolution = _resolve("Windows", {}, home=r"C:\Users\o")
        self.assertEqual(r"C:\Users\o\AppData\Local\ForgeStudio", resolution.root)

    def test_macos_uses_application_support(self) -> None:
        resolution = _resolve("Darwin", {}, home="/Users/o")
        self.assertEqual(
            "/Users/o/Library/Application Support/ForgeStudio", resolution.root
        )
        self.assertEqual(MACOS, resolution.platform)

    def test_linux_prefers_xdg_data_home(self) -> None:
        resolution = _resolve(
            "Linux", {"XDG_DATA_HOME": "/custom/share"}, home="/home/o"
        )
        self.assertEqual("/custom/share/forge-studio", resolution.root)

    def test_linux_falls_back_to_local_share(self) -> None:
        resolution = _resolve("Linux", {}, home="/home/o")
        self.assertEqual("/home/o/.local/share/forge-studio", resolution.root)
        self.assertEqual(LINUX, resolution.platform)

    def test_the_answer_is_computed_not_read_from_this_host(self) -> None:
        """Three platforms, three different answers, one machine.

        If this ever starts depending on the running host, the macOS and Linux
        cases become untestable until someone owns those machines -- which is
        exactly the position the cross-platform requirement forbids.
        """

        roots = {
            _resolve("Windows", {"LOCALAPPDATA": "C:/L"}, home="C:/U").root,
            _resolve("Darwin", {}, home="/Users/o").root,
            _resolve("Linux", {}, home="/home/o").root,
        }
        self.assertEqual(3, len(roots))


class PrecedenceTests(unittest.TestCase):
    def test_config_beats_environment(self) -> None:
        resolution = _resolve(
            "Linux",
            {STATE_ROOT_ENV: "/from/env"},
            config_value="/from/config",
        )
        self.assertEqual("/from/config", resolution.root)
        self.assertEqual(CONFIG_OVERRIDE, resolution.source)
        self.assertTrue(resolution.explicit)

    def test_environment_beats_the_platform_default(self) -> None:
        resolution = _resolve("Linux", {STATE_ROOT_ENV: "/srv/state"})
        self.assertEqual("/srv/state", resolution.root)
        self.assertEqual(ENVIRONMENT_OVERRIDE, resolution.source)
        self.assertTrue(resolution.explicit)

    def test_an_override_is_trusted_verbatim(self) -> None:
        """An owner who names a path has answered the question this function
        exists to answer -- the same stance TORCH_COMMAND already gets."""

        resolution = _resolve("Windows", {}, config_value="D:/portable/state")
        self.assertEqual("D:/portable/state", resolution.root)

    def test_whitespace_only_overrides_are_not_overrides(self) -> None:
        resolution = _resolve("Linux", {STATE_ROOT_ENV: "   "}, config_value="  ")
        self.assertEqual(PLATFORM_DEFAULT, resolution.source)


class UnknownPlatformTests(unittest.TestCase):
    def test_an_unrecognized_platform_gets_a_labelled_fallback(self) -> None:
        """Deliberately NOT the install selector's behaviour.

        `platform_selection` refuses on an unknown platform because installing
        the wrong Torch build is worse than not starting. This is the opposite
        trade: a POSIX-shaped home works on every unusual host anyone is likely
        to run on, and refusing would brick Studio over a `platform.system()`
        string nobody anticipated.
        """

        resolution = _resolve("FreeBSD", {}, home="/home/o")
        self.assertEqual("/home/o/.forge-studio", resolution.root)
        self.assertEqual(UNKNOWN_PLATFORM, resolution.platform)

    def test_the_fallback_never_claims_to_be_a_convention(self) -> None:
        """The label is the whole point: an owner reading `platform_default`
        would reasonably believe this path is where the OS says it belongs."""

        self.assertEqual(POSIX_FALLBACK, _resolve("Haiku", {}).source)
        self.assertNotEqual(PLATFORM_DEFAULT, _resolve("Haiku", {}).source)


class MissingHomeTests(unittest.TestCase):
    def test_each_platform_refuses_by_name_rather_than_guessing(self) -> None:
        for system in ("Windows", "Darwin", "Linux", "Plan9"):
            with self.subTest(system=system):
                with self.assertRaises(StateRootError) as raised:
                    _resolve(system, {}, home=None)
                self.assertIn(STATE_ROOT_ENV, str(raised.exception))

    def test_an_override_still_works_without_a_home(self) -> None:
        """A container with no home is exactly the case the override exists
        for; refusing there would make Docker unrunnable."""

        resolution = _resolve("Linux", {STATE_ROOT_ENV: "/state"}, home=None)
        self.assertEqual("/state", resolution.root)


class ConflictTests(unittest.TestCase):
    def test_the_install_directory_is_refused(self) -> None:
        with self.assertRaises(StateRootError):
            refuse_conflicting_root("/opt/studio", install_root="/opt/studio")

    def test_a_directory_inside_the_install_is_refused(self) -> None:
        """The hazard is concrete: an update or clean reinstall takes the
        owner's settings with it -- the exact event they expect to survive."""

        with self.assertRaises(StateRootError) as raised:
            refuse_conflicting_root("/opt/studio/state", install_root="/opt/studio")
        self.assertIn("reinstall", str(raised.exception))

    def test_the_result_root_is_refused(self) -> None:
        with self.assertRaises(StateRootError):
            refuse_conflicting_root("/data/results", result_root="/data/results")

    def test_a_model_root_is_refused(self) -> None:
        with self.assertRaises(StateRootError):
            refuse_conflicting_root(
                "/models/ckpt", model_roots=("/models/ckpt", "/models/vae")
            )

    def test_a_sibling_of_the_install_is_allowed(self) -> None:
        """Refusing anything NEAR the install would rule out the portable
        layout the decision explicitly permits."""

        refuse_conflicting_root("/opt/studio-state", install_root="/opt/studio")

    def test_windows_paths_compare_as_windows_paths(self) -> None:
        with self.assertRaises(StateRootError):
            refuse_conflicting_root(
                r"C:\Studio\state", install_root=r"C:\Studio", windows=True
            )


class LayoutTests(unittest.TestCase):
    def test_the_layout_is_platform_independent(self) -> None:
        """D1: the root moves, what is inside it does not. A layout carrying a
        platform-specific separator would break that on the first copy between
        machines."""

        from forge_studio.state_root import STATE_LAYOUT

        for entry in STATE_LAYOUT:
            with self.subTest(entry=entry):
                self.assertNotIn("\\", entry)


class PurityTests(unittest.TestCase):
    def test_the_module_touches_no_filesystem_and_no_environment(self) -> None:
        """Asserted through AST, not text: a docstring saying "no side effects"
        is exactly the kind of claim this codebase has been wrong about."""

        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "state_root.py").read_text(encoding="utf-8")
        )
        banned = {"mkdir", "open", "write_text", "touch", "rmtree", "makedirs",
                  "getenv", "putenv", "system", "run", "Popen"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None
                )
                self.assertNotIn(name, banned, f"{name}() is a side effect")

    def test_it_imports_only_the_standard_library(self) -> None:
        import ast

        tree = ast.parse(
            (APP_ROOT / "forge_studio" / "state_root.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith(("forge_studio", "forge_headless",
                                            "modules", "backend")),
                    f"non-stdlib import: {node.module}",
                )


class WiringTests(unittest.TestCase):
    """The resolver passing its own tests proves nothing about it being CALLED.

    This codebase records three separate occasions where a chooser was written,
    exported, and never wired -- and the first version of this very change did
    exactly that: a `str.replace` whose target did not match failed SILENTLY,
    the module was perfect, and `load_config` returned no state root at all. It
    was caught by exercising the real loader, not by any test that existed.
    """

    def test_load_config_resolves_a_state_root(self) -> None:
        from forge_studio.launch import load_config

        config = load_config(APP_ROOT.parent / "studio-config.json")
        self.assertTrue(config["studio_state_root"])
        self.assertIn(
            config["studio_state_root_source"],
            {CONFIG_OVERRIDE, ENVIRONMENT_OVERRIDE, PLATFORM_DEFAULT,
             POSIX_FALLBACK},
        )

    def test_the_state_root_is_not_the_result_root(self) -> None:
        """D1, asserted where it can actually regress."""

        from forge_studio.launch import load_config

        config = load_config(APP_ROOT.parent / "studio-config.json")
        self.assertNotEqual(
            str(config["result_root"]), config["studio_state_root"]
        )

    def test_the_state_root_is_outside_the_install_directory(self) -> None:
        """Containment here would put settings inside what an update deletes.

        Note this is the OPPOSITE of `result_root`, which launch.py forces
        INSIDE the workspace. Two roots, two rules, on purpose.
        """

        from forge_studio.launch import load_config

        config = load_config(APP_ROOT.parent / "studio-config.json")
        self.assertFalse(config["studio_state_root"].startswith(str(APP_ROOT)))


class LauncherStateRootTests(unittest.TestCase):
    """The owner surface for this, and the shape it has to keep.

    D1 makes the platform location the DEFAULT, not the only answer, and names
    portable installs as the case for an override. This tree is that case: a
    folder on a desktop that an owner copies about. Keeping state beside the
    thing it belongs to is also the difference between "Studio is one folder"
    and "Studio is one folder plus somewhere under AppData you have to know
    about" -- which is how the Gallery index came to be somewhere nobody
    expected it.

    `Start-Studio.bat`, not a config file. Surface 2 of the owner surfaces.
    """

    #: Read in setUp, not at class scope. `Start-Studio.bat` lives ABOVE the
    #: git root -- the repository is `app/` -- so a checkout of the repository
    #: alone does not have it, and reading it while the class body executes
    #: turns "the launcher is not here" into a collection error for the whole
    #: module. `LauncherFlagTests` in `test_runtime_options.py` still does
    #: that; this does not make it worse.
    def setUp(self) -> None:
        launcher = APP_ROOT.parent / "Start-Studio.bat"
        if not launcher.exists():
            self.skipTest("Start-Studio.bat is not in this checkout")
        self.LAUNCHER = launcher.read_text(encoding="utf-8")

    def _assignment(self) -> str:
        for line in self.LAUNCHER.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("set studio_state_root="):
                return stripped.split("=", 1)[1]
        self.fail("Start-Studio.bat no longer sets STUDIO_STATE_ROOT")

    def test_the_launcher_sets_it(self) -> None:
        self.assertTrue(self._assignment())

    def test_it_is_relative_to_the_launcher_rather_than_absolute(self) -> None:
        """`%~dp0` is what makes the folder movable. A literal path is not."""
        self.assertTrue(self._assignment().startswith("%~dp0"))

    def test_the_env_name_is_the_one_the_resolver_reads(self) -> None:
        self.assertIn("set " + STATE_ROOT_ENV, self.LAUNCHER.replace("SET ", "set "))

    def test_what_the_launcher_asks_for_is_not_refused(self) -> None:
        """The guard and the launcher must agree, or Studio will not start.

        Not a hypothetical: the same directory tree holds the install, the
        results and the models, and three of those four are refusals.
        """
        workspace = APP_ROOT.parent
        root = str(workspace / self._assignment().replace("%~dp0", ""))
        refuse_conflicting_root(
            root,
            install_root=str(APP_ROOT),
            result_root=str(workspace / "Studio-Results"),
            model_roots=(),
            windows=True,
        )

    def test_it_is_still_outside_the_install_directory(self) -> None:
        """The one refusal that would cost an owner their settings silently."""
        workspace = APP_ROOT.parent
        root = str(workspace / self._assignment().replace("%~dp0", ""))
        with self.assertRaises(StateRootError):
            refuse_conflicting_root(root, install_root=root, windows=True)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loader = unittest.defaultTestLoader
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_STATE_ROOT_TESTS, suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
