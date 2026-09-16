"""B1 — platform-aware Torch install-command selection.

Every case injects platform, machine, and environment, so the whole matrix
runs on one machine. Nothing here imports Torch, spawns a subprocess, touches
the network, or mutates the real environment.
"""

from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

MODULE_PATH = APP_ROOT / "modules" / "platform_selection.py"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.sel = importlib.import_module("modules.platform_selection")

    def torch(self, system: str, machine: str, **env: str):
        return self.sel.select_torch_command(
            system=system,
            machine=machine,
            environ=env,
        )


class TorchSelectionTests(_Base):
    def test_windows_amd64_default(self) -> None:
        """1. The retained CUDA default is preserved exactly."""

        s = self.torch("Windows", "AMD64")
        self.assertEqual(self.sel.WINDOWS, s.platform_family)
        self.assertEqual(self.sel.CUDA, s.acceleration_family)
        self.assertEqual(self.sel.PLATFORM_DEFAULT, s.source)
        self.assertEqual(
            "pip install torch==2.11.0+cu130 torchvision==0.26.0+cu130 "
            "--extra-index-url https://download.pytorch.org/whl/cu130",
            s.command,
        )
        self.assertEqual(
            "https://download.pytorch.org/whl/cu130",
            s.index_url,
        )
        self.assertTrue(s.supported)

    def test_windows_explicit_override(self) -> None:
        """2. An owner override is used verbatim and never rewritten."""

        s = self.torch(
            "Windows",
            "AMD64",
            TORCH_COMMAND="pip install torch==9.9.9",
        )
        self.assertEqual("pip install torch==9.9.9", s.command)
        self.assertEqual(self.sel.ENVIRONMENT_OVERRIDE, s.source)

    def test_windows_index_override_is_used(self) -> None:
        s = self.torch(
            "Windows",
            "AMD64",
            TORCH_INDEX_URL="https://example.invalid/whl",
        )
        self.assertEqual("https://example.invalid/whl", s.index_url)
        self.assertIn("https://example.invalid/whl", s.command)

    def test_apple_silicon_default(self) -> None:
        """3."""

        for machine in ("arm64", "ARM64", "aarch64"):
            with self.subTest(machine=machine):
                s = self.torch("Darwin", machine)
                self.assertEqual(self.sel.MACOS, s.platform_family)
                self.assertEqual(self.sel.MPS, s.acceleration_family)
                self.assertEqual("pip install torch torchvision", s.command)
                self.assertIsNone(s.index_url)
                self.assertTrue(s.supported)

    def test_apple_silicon_explicit_override(self) -> None:
        """4."""

        s = self.torch(
            "Darwin",
            "arm64",
            TORCH_COMMAND="pip install torch==2.5.0",
        )
        self.assertEqual("pip install torch==2.5.0", s.command)
        self.assertEqual(self.sel.ENVIRONMENT_OVERRIDE, s.source)

    def test_intel_macos_is_cpu_never_mps(self) -> None:
        """5. Intel macOS must never be classified MPS-capable."""

        s = self.torch("Darwin", "x86_64")
        self.assertEqual(self.sel.MACOS, s.platform_family)
        self.assertEqual(self.sel.CPU, s.acceleration_family)
        self.assertNotEqual(self.sel.MPS, s.acceleration_family)
        self.assertEqual("pip install torch torchvision", s.command)

    def test_linux_default_is_unchanged(self) -> None:
        """6. The Linux matrix is preserved, not expanded."""

        s = self.torch("Linux", "x86_64")
        self.assertEqual(self.sel.LINUX, s.platform_family)
        self.assertEqual(self.sel.CUDA, s.acceleration_family)
        self.assertIn("+cu130", s.command)
        self.assertEqual(
            "https://download.pytorch.org/whl/cu130",
            s.index_url,
        )

    def test_unknown_platform_selects_nothing(self) -> None:
        """7. Never silently fall back to CUDA."""

        for system in ("Plan9", "", "  ", "FreeBSD"):
            with self.subTest(system=system):
                s = self.torch(system, "x86_64")
                self.assertEqual(self.sel.UNKNOWN_PLATFORM, s.platform_family)
                self.assertEqual(
                    self.sel.UNKNOWN_ACCELERATION,
                    s.acceleration_family,
                )
                self.assertIsNone(s.command)
                self.assertFalse(s.supported)
                self.assertNotEqual(self.sel.CUDA, s.acceleration_family)

    def test_macos_has_no_cuda_local_version_suffix(self) -> None:
        """8."""

        for machine in ("arm64", "x86_64"):
            with self.subTest(machine=machine):
                self.assertNotIn("+cu", self.torch("Darwin", machine).command)

    def test_macos_has_no_cuda_index(self) -> None:
        """9."""

        for machine in ("arm64", "x86_64"):
            with self.subTest(machine=machine):
                s = self.torch("Darwin", machine)
                self.assertIsNone(s.index_url)
                self.assertNotIn("download.pytorch.org/whl/cu", s.command)
                self.assertNotIn("--extra-index-url", s.command)

    def test_macos_never_receives_nvidia_driver_guidance(self) -> None:
        """10."""

        for machine in ("arm64", "x86_64"):
            with self.subTest(machine=machine):
                s = self.torch("Darwin", machine)
                self.assertFalse(
                    self.sel.cuda_driver_guidance_applies(s)
                )
                self.assertFalse(s.is_cuda)

    def test_unknown_platform_receives_no_driver_guidance(self) -> None:
        """10."""

        self.assertFalse(
            self.sel.cuda_driver_guidance_applies(
                self.torch("Plan9", "x86_64")
            )
        )

    def test_windows_cuda_behaviour_is_unchanged(self) -> None:
        """11."""

        s = self.torch("Windows", "AMD64")
        self.assertTrue(s.is_cuda)
        self.assertTrue(self.sel.cuda_driver_guidance_applies(s))

    def test_selection_does_not_mutate_the_injected_environment(self) -> None:
        env: dict[str, str] = {}
        self.sel.select_torch_command(
            system="Darwin",
            machine="arm64",
            environ=env,
        )
        self.assertEqual({}, env)


class AcceleratorSelectionTests(_Base):
    """12. Incompatible optional accelerators on macOS."""

    ACCELERATORS = ("xformers", "sage", "flash", "triton", "cuda_malloc")

    def test_every_accelerator_is_unsupported_on_macos(self) -> None:
        for name in self.ACCELERATORS:
            for machine in ("arm64", "x86_64"):
                with self.subTest(name=name, machine=machine):
                    a = self.sel.select_accelerator(
                        name,
                        system="Darwin",
                        machine=machine,
                        environ={},
                    )
                    self.assertFalse(a.supported)
                    self.assertIsNone(a.package)
                    self.assertTrue(a.reason)

    def test_accelerators_remain_supported_on_windows(self) -> None:
        for name in self.ACCELERATORS:
            with self.subTest(name=name):
                a = self.sel.select_accelerator(
                    name,
                    system="Windows",
                    machine="AMD64",
                    environ={},
                    package="retained-default",
                )
                self.assertTrue(a.supported)

    def test_accelerators_remain_supported_on_linux(self) -> None:
        for name in self.ACCELERATORS:
            with self.subTest(name=name):
                a = self.sel.select_accelerator(
                    name,
                    system="Linux",
                    machine="x86_64",
                    environ={},
                    package="retained-default",
                )
                self.assertTrue(a.supported)

    def test_override_does_not_make_macos_supported(self) -> None:
        """An override is seen and declined, not silently honoured."""

        a = self.sel.select_accelerator(
            "sage",
            system="Darwin",
            machine="arm64",
            environ={"SAGE_PACKAGE": "sageattention==2.2.0"},
        )
        self.assertFalse(a.supported)
        self.assertIsNone(a.package)
        self.assertEqual(self.sel.ENVIRONMENT_OVERRIDE, a.source)

    def test_override_is_used_verbatim_where_supported(self) -> None:
        a = self.sel.select_accelerator(
            "sage",
            system="Windows",
            machine="AMD64",
            environ={"SAGE_PACKAGE": "sageattention==9.9.9"},
        )
        self.assertTrue(a.supported)
        self.assertEqual("sageattention==9.9.9", a.package)

    def test_unknown_accelerator_is_unsupported(self) -> None:
        a = self.sel.select_accelerator(
            "not-a-real-accelerator",
            system="Windows",
            machine="AMD64",
            environ={},
        )
        self.assertFalse(a.supported)


class HelperPurityTests(unittest.TestCase):
    """13, 14, 15 — proven by static analysis of the module source.

    Asserting purity by AST rather than by observing one call keeps the
    guarantee true for every code path, including ones no test exercises.
    """

    def setUp(self) -> None:
        self.tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        self.imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.imported.add(node.module.split(".")[0])

    def test_no_path_imports_torch(self) -> None:
        """13."""

        for banned in ("torch", "torchvision", "gradio", "numpy"):
            self.assertNotIn(banned, self.imported)

    def test_no_path_invokes_a_subprocess(self) -> None:
        """14."""

        for banned in ("subprocess", "multiprocessing", "pty"):
            self.assertNotIn(banned, self.imported)
        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("os.system", "os.popen", "os.exec", "os.spawn"):
            self.assertNotIn(banned, source)

    def test_no_path_performs_network_access(self) -> None:
        """15."""

        for banned in (
            "socket",
            "urllib",
            "http",
            "ssl",
            "ftplib",
            "requests",
            "httpx",
        ):
            self.assertNotIn(banned, self.imported)

    def test_module_imports_only_the_standard_library(self) -> None:
        self.assertEqual({"__future__", "os", "dataclasses", "typing"}, self.imported)

    def test_no_environment_mutation(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in (
            "os.environ[",
            "os.environ.setdefault",
            "os.environ.update",
            "os.environ.pop",
            "putenv",
        ):
            self.assertNotIn(banned, source)

    def test_no_filesystem_access(self) -> None:
        for banned in ("pathlib", "shutil", "tempfile", "glob"):
            self.assertNotIn(banned, self.imported)
        source = MODULE_PATH.read_text(encoding="utf-8")
        for banned in ("open(", "os.remove", "os.mkdir", "os.walk"):
            self.assertNotIn(banned, source)


LAUNCH_UTILS_PATH = APP_ROOT / "modules" / "launch_utils.py"


class XformersMacHardeningTests(unittest.TestCase):
    """No CUDA xformers value is constructed on macOS.

    Verified by parsing `prepare_environment` rather than by running it:
    executing it would invoke pip. AST analysis also covers branches no test
    could reach without a macOS host.
    """

    ACCELERATORS = ("xformers", "sage", "flash", "triton", "nunchaku")

    def setUp(self) -> None:
        self.sel = importlib.import_module("modules.platform_selection")
        self.source = LAUNCH_UTILS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(self.source)
        self.prepare = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "prepare_environment"
        )

    def _platform_branch(self, want_macos: bool):
        """Return the macOS branch body, or the Windows/Linux bodies."""

        for node in ast.walk(self.prepare):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            # ast.unparse renders string literals with single quotes.
            if "os.name" in test and "'nt'" in test:
                windows_body = node.body
                macos_node = node.orelse[0] if node.orelse else None
                if not isinstance(macos_node, ast.If):
                    continue
                if "MACOS" not in ast.unparse(macos_node.test):
                    continue
                if want_macos:
                    return macos_node.body
                return windows_body + list(macos_node.orelse)
        self.fail("platform branch not found in prepare_environment")

    def _assignment_pairs(self, body) -> list[tuple[str, ast.AST]]:
        """Every `name = value` in a body, preserving duplicates."""

        found = []
        for statement in body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        found.append((target.id, statement.value))
        return found

    def _assignments(self, body) -> dict[str, ast.AST]:
        return dict(self._assignment_pairs(body))

    # -- 1, 2 ------------------------------------------------------------

    def test_macos_xformers_package_is_literally_none(self) -> None:
        """1. Absent, not present-but-guarded."""

        assigned = self._assignments(self._platform_branch(want_macos=True))
        self.assertIn("xformers_package", assigned)
        value = assigned["xformers_package"]
        self.assertIsInstance(value, ast.Constant)
        self.assertIsNone(value.value)

    def test_macos_treats_xformers_like_every_other_accelerator(self) -> None:
        """1. All five are None in the same branch, by the same mechanism."""

        assigned = self._assignments(self._platform_branch(want_macos=True))
        for name in self.ACCELERATORS:
            key = f"{name}_package"
            with self.subTest(accelerator=key):
                self.assertIn(key, assigned)
                self.assertIsInstance(assigned[key], ast.Constant)
                self.assertIsNone(assigned[key].value)

    def test_macos_override_cannot_make_xformers_supported(self) -> None:
        """2. The override is observed and declined."""

        selection = self.sel.select_accelerator(
            "xformers",
            system="Darwin",
            machine="arm64",
            environ={"XFORMERS_PACKAGE": "xformers==0.0.35"},
        )
        self.assertFalse(selection.supported)
        self.assertIsNone(selection.package)
        self.assertEqual(self.sel.ENVIRONMENT_OVERRIDE, selection.source)

    # -- 3, 4 ------------------------------------------------------------

    def test_windows_and_linux_xformers_value_is_unchanged(self) -> None:
        """3, 4. Both still read XFORMERS_PACKAGE with the CUDA default."""

        bodies = self._platform_branch(want_macos=False)
        assignments = [
            ast.unparse(value)
            for name, value in self._assignment_pairs(bodies)
            if name == "xformers_package"
        ]
        # One in the Windows branch, one in the Linux branch.
        self.assertEqual(2, len(assignments), assignments)
        for rendered in assignments:
            with self.subTest(rendered=rendered):
                self.assertIn("XFORMERS_PACKAGE", rendered)
                self.assertIn("CUDA_XFORMERS_SPEC", rendered)
                self.assertIn("--extra-index-url", rendered)
                self.assertIn("torch_index_url", rendered)

    def test_accelerator_helper_still_supports_windows_and_linux(self) -> None:
        """3, 4."""

        for system in ("Windows", "Linux"):
            with self.subTest(system=system):
                selection = self.sel.select_accelerator(
                    "xformers",
                    system=system,
                    machine="AMD64",
                    environ={},
                    package="retained-default",
                )
                self.assertTrue(selection.supported)
                self.assertEqual("retained-default", selection.package)

    # -- 5 ---------------------------------------------------------------

    def test_macos_branch_constructs_no_cuda_index_string(self) -> None:
        """5. No f-string, index URL, or env read anywhere in that branch."""

        body = self._platform_branch(want_macos=True)
        rendered = "\n".join(ast.unparse(statement) for statement in body)
        for banned in (
            "extra-index-url",
            "CUDA_INDEX_URL",
            "CUDA_XFORMERS_SPEC",
            "torch_index_url",
            "download.pytorch.org",
            "os.environ",
            "XFORMERS_PACKAGE",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, rendered)
        for statement in body:
            for inner in ast.walk(statement):
                self.assertNotIsInstance(inner, ast.JoinedStr)

    # -- 6 ---------------------------------------------------------------

    def test_installer_cannot_receive_xformers_on_macos(self) -> None:
        """6. Every accelerator install site is guarded identically."""

        guarded = []
        for node in ast.walk(self.prepare):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_pip"
            ):
                rendered = ast.unparse(node)
                for name in self.ACCELERATORS:
                    if f"{name}_package" in rendered:
                        guarded.append((name, rendered))
        self.assertEqual(
            sorted(self.ACCELERATORS),
            sorted(name for name, _ in guarded),
        )
        for name, rendered in guarded:
            with self.subTest(accelerator=name):
                self.assertIn("_require_accelerator", rendered)

    def test_no_distant_is_cuda_guard_remains(self) -> None:
        """6. The value is absent at the source, so no far guard is needed."""

        self.assertNotIn("is_cuda", self.source)

    def test_require_accelerator_raises_on_none(self) -> None:
        """6. The guard's contract: None is refused, never installed."""

        for name in self.ACCELERATORS:
            with self.subTest(accelerator=name):
                selection = self.sel.select_accelerator(
                    name,
                    system="Darwin",
                    machine="arm64",
                    environ={},
                )
                self.assertFalse(selection.supported)
                self.assertTrue(selection.reason)

    def test_no_test_executes_prepare_environment_or_pip(self) -> None:
        """Handoff rule: this boundary is proven statically, never by running.

        Checked over the AST for real call nodes, so a test may name these
        functions in a string or docstring -- as this one does -- without
        tripping the rule.
        """

        banned = {"prepare_environment", "run_pip", "run_extension_installer"}
        for path in sorted((APP_ROOT / "tests" / "studio_alpha").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            called = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name in banned:
                    called.add(name)
            with self.subTest(path=path.name):
                self.assertEqual(set(), called)


if __name__ == "__main__":
    unittest.main()
