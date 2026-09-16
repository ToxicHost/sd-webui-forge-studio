"""Stdlib-only bootstrap for the workspace preflight tool."""

from __future__ import annotations

import importlib.util
import json
from importlib.machinery import all_suffixes, ModuleSpec
import os
from pathlib import Path
import stat
import sys
import types


_PREFLIGHT_REQUIRED_MODULES = (
    "__init__.py",
    "__main__.py",
    "boundary.py",
    "cli.py",
    "constraints.py",
    "contracts.py",
    "inspection.py",
    "orchestrator.py",
    "pip_bootstrap.py",
    "reports.py",
    "resolver.py",
)
_PACKAGING_REQUIRED_MODULES = (
    "__init__.py",
    "_elffile.py",
    "_manylinux.py",
    "_musllinux.py",
    "_parser.py",
    "_structures.py",
    "_tokenizer.py",
    "markers.py",
    "requirements.py",
    "specifiers.py",
    "tags.py",
    "utils.py",
    "version.py",
)


class BootstrapPathViolation(RuntimeError):
    """Fail-closed bootstrap path validation error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _roots() -> tuple[Path, Path, Path]:
    module_path = Path(os.path.abspath(__file__))
    app_root = module_path.parents[2]
    workspace_root = module_path.parents[3]
    site_packages = app_root / "venv" / "Lib" / "site-packages"
    return workspace_root, app_root, site_packages


def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(attributes & reparse_flag)


def _checked_lstat(path: Path, *, expected_kind: str | None = None) -> os.stat_result:
    """Inspect one required path without following its final component."""

    try:
        path_stat = os.lstat(path)
    except FileNotFoundError as exc:
        raise BootstrapPathViolation("BOOTSTRAP_REQUIRED_PATH_MISSING") from exc
    except OSError as exc:
        raise BootstrapPathViolation("BOOTSTRAP_PATH_UNREADABLE") from exc

    if _is_reparse_or_symlink(path_stat):
        raise BootstrapPathViolation("BOOTSTRAP_REPARSE_POINT_REJECTED")

    is_directory = stat.S_ISDIR(path_stat.st_mode)
    is_regular = stat.S_ISREG(path_stat.st_mode)
    if expected_kind == "directory" and not is_directory:
        raise BootstrapPathViolation("BOOTSTRAP_PATH_TYPE_REJECTED")
    if expected_kind == "file" and not is_regular:
        raise BootstrapPathViolation("BOOTSTRAP_PATH_TYPE_REJECTED")
    if expected_kind is None and not (is_directory or is_regular):
        raise BootstrapPathViolation("BOOTSTRAP_PATH_TYPE_REJECTED")

    if is_regular and getattr(path_stat, "st_nlink", 1) != 1:
        raise BootstrapPathViolation("BOOTSTRAP_HARD_LINK_REJECTED")
    return path_stat


def _validate_tree(root: Path) -> None:
    """Validate a small import tree without following links or reparse points."""

    _checked_lstat(root, expected_kind="directory")
    try:
        with os.scandir(root) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
    except OSError as exc:
        raise BootstrapPathViolation("BOOTSTRAP_PATH_UNREADABLE") from exc

    for entry in children:
        child = root / entry.name
        child_stat = _checked_lstat(child)
        if stat.S_ISDIR(child_stat.st_mode):
            _validate_tree(child)


def _reject_import_shadow(path: Path) -> None:
    """Require an import-shadow candidate to be absent without following it."""

    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BootstrapPathViolation("BOOTSTRAP_PATH_UNREADABLE") from exc
    if _is_reparse_or_symlink(path_stat):
        raise BootstrapPathViolation("BOOTSTRAP_REPARSE_POINT_REJECTED")
    raise BootstrapPathViolation("BOOTSTRAP_IMPORT_SHADOW_REJECTED")


def _validate_bootstrap_paths(app_root: Path, site_packages: Path) -> None:
    """Validate every workspace path used to bootstrap preflight imports."""

    workspace_root = app_root.parent
    preflight_root = app_root / "scripts" / "preflight"
    module_path = Path(os.path.abspath(__file__))
    expected_module = preflight_root / "bootstrap.py"
    if os.path.normcase(os.path.normpath(os.fspath(module_path))) != os.path.normcase(
        os.path.normpath(os.fspath(expected_module))
    ):
        raise BootstrapPathViolation("BOOTSTRAP_MODULE_PATH_REJECTED")

    directory_chain = (
        workspace_root,
        app_root,
        app_root / "scripts",
        preflight_root,
        app_root / "venv",
        app_root / "venv" / "Scripts",
        app_root / "venv" / "Lib",
        site_packages,
    )
    for directory in directory_chain:
        _checked_lstat(directory, expected_kind="directory")

    _checked_lstat(module_path, expected_kind="file")
    _checked_lstat(
        app_root / "venv" / "Scripts" / "python.exe",
        expected_kind="file",
    )

    packaging_root = site_packages / "packaging"
    _validate_tree(preflight_root)
    _validate_tree(packaging_root)
    for module_name in _PREFLIGHT_REQUIRED_MODULES:
        _checked_lstat(preflight_root / module_name, expected_kind="file")
    for module_name in _PACKAGING_REQUIRED_MODULES:
        _checked_lstat(packaging_root / module_name, expected_kind="file")

    module_suffixes = tuple(dict.fromkeys(all_suffixes()))
    _reject_import_shadow(app_root / "packaging")
    for import_root in (app_root, site_packages):
        for suffix in module_suffixes:
            _reject_import_shadow(import_root / f"packaging{suffix}")


def _bootstrap_error(reason_code: str) -> int:
    print(
        json.dumps(
            {
                "mode": "BOOTSTRAP",
                "decision": "NO_GO",
                "reason_code": reason_code,
            },
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 2


def main() -> int:
    _, app_root, site_packages = _roots()
    expected_python = app_root / "venv" / "Scripts" / "python.exe"
    expected = os.path.normcase(
        os.path.normpath(os.path.abspath(os.fspath(expected_python)))
    )
    observed = os.path.normcase(
        os.path.normpath(os.path.abspath(sys.executable))
    )

    if not sys.flags.isolated or not sys.flags.no_site:
        return _bootstrap_error("ISOLATED_NO_SITE_PYTHON_REQUIRED")
    if not sys.dont_write_bytecode:
        return _bootstrap_error("BYTECODE_WRITES_MUST_BE_DISABLED")
    if observed != expected:
        return _bootstrap_error("WORKSPACE_VENV_PYTHON_REQUIRED")

    try:
        _validate_bootstrap_paths(app_root, site_packages)
    except BootstrapPathViolation as exc:
        return _bootstrap_error(exc.reason_code)

    if "scripts" in sys.modules:
        return _bootstrap_error("BOOTSTRAP_SCRIPTS_NAMESPACE_PRELOADED")
    scripts_root = app_root / "scripts"
    scripts_module = types.ModuleType("scripts")
    scripts_module.__package__ = "scripts"
    scripts_module.__path__ = [os.fspath(scripts_root)]
    scripts_spec = ModuleSpec("scripts", loader=None, is_package=True)
    scripts_spec.submodule_search_locations = [os.fspath(scripts_root)]
    scripts_module.__spec__ = scripts_spec
    sys.modules["scripts"] = scripts_module

    if "packaging" in sys.modules:
        return _bootstrap_error("BOOTSTRAP_PACKAGING_NAMESPACE_PRELOADED")
    packaging_root = site_packages / "packaging"
    packaging_init = packaging_root / "__init__.py"
    packaging_spec = importlib.util.spec_from_file_location(
        "packaging",
        packaging_init,
        submodule_search_locations=[os.fspath(packaging_root)],
    )
    if packaging_spec is None or packaging_spec.loader is None:
        return _bootstrap_error("BOOTSTRAP_PACKAGING_IMPORT_SPEC_REJECTED")
    packaging_module = importlib.util.module_from_spec(packaging_spec)
    sys.modules["packaging"] = packaging_module
    packaging_spec.loader.exec_module(packaging_module)

    if len(sys.argv) > 1 and sys.argv[1] == "self-test":
        import unittest

        test_root = app_root / "scripts" / "preflight" / "tests"
        suite = unittest.defaultTestLoader.discover(os.fspath(test_root))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    from scripts.preflight.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
