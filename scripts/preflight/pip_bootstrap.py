"""Isolated, workspace-validated bootstrap for a future authorized pip dry run.

This module is never imported by Static, Contract, or Verify.  A future Resolve
process executes it with the exact workspace venv Python using ``-I -S -B``.
It validates and exact-loads the workspace pip package without adding the
general site-packages directory to ``sys.path``. Automatic ``site`` and
``.pth`` processing remain disabled.
"""

from __future__ import annotations

from importlib.machinery import all_suffixes
import importlib.util
import os
from pathlib import Path
import re
import runpy
import stat
import sys
from typing import Sequence
from urllib.parse import urlsplit


def _is_reparse_or_symlink(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(attributes & reparse_flag)


def _validate_path(path: Path, *, allowed_root: Path) -> None:
    try:
        relative = path.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError("PIP_BOOTSTRAP_PATH_OUTSIDE_APP") from exc

    current = allowed_root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        path_stat = os.lstat(current)
        if _is_reparse_or_symlink(path_stat):
            raise RuntimeError("PIP_BOOTSTRAP_REPARSE_POINT_REJECTED")
        if (
            os.path.normcase(os.fspath(current))
            == os.path.normcase(os.fspath(path))
            and stat.S_ISREG(path_stat.st_mode)
            and path_stat.st_nlink > 1
        ):
            raise RuntimeError("PIP_BOOTSTRAP_HARD_LINK_REJECTED")


def _validate_tree(root: Path, *, allowed_root: Path) -> None:
    _validate_path(root, allowed_root=allowed_root)
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            child = Path(entry.path)
            child_stat = os.lstat(child)
            if _is_reparse_or_symlink(child_stat):
                raise RuntimeError("PIP_BOOTSTRAP_REPARSE_POINT_REJECTED")
            if stat.S_ISREG(child_stat.st_mode) and child_stat.st_nlink > 1:
                raise RuntimeError("PIP_BOOTSTRAP_HARD_LINK_REJECTED")
            if stat.S_ISDIR(child_stat.st_mode):
                pending.append(child)


def _reject_import_shadow(path: Path) -> None:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return
    if _is_reparse_or_symlink(path_stat):
        raise RuntimeError("PIP_BOOTSTRAP_REPARSE_POINT_REJECTED")
    raise RuntimeError("PIP_BOOTSTRAP_IMPORT_SHADOW_REJECTED")


def _normalize_pip_config_file() -> None:
    """Preserve the owner-facing NUL contract, then satisfy pip's exact sentinel."""

    if os.environ.get("PIP_CONFIG_FILE") != "NUL":
        raise RuntimeError("PIP_BOOTSTRAP_CONFIG_SENTINEL_REJECTED")
    os.environ["PIP_CONFIG_FILE"] = os.devnull


def _require_execution_capability() -> None:
    """Block direct or parent invocation until owner and containment gates exist."""

    raise RuntimeError(
        "PIP_BOOTSTRAP_EXECUTION_DISABLED:"
        "OWNER_SIGNATURE_AND_PREVENTIVE_CONTAINMENT_REQUIRED"
    )


def _normalized_absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _validate_pip_argv(
    argv: Sequence[str],
    app_root: Path,
) -> tuple[Path, Path]:
    """Accept only the exact owner-reviewable pip dry-run command shape."""

    if len(argv) != 10 or tuple(argv[:5]) != (
        "install",
        "--dry-run",
        "--ignore-installed",
        "--only-binary=:all:",
        "--report",
    ):
        raise RuntimeError("PIP_BOOTSTRAP_ARGV_REJECTED")
    if tuple(argv[6:9:2]) != ("--index-url", "-r"):
        raise RuntimeError("PIP_BOOTSTRAP_ARGV_REJECTED")

    workspace_root = app_root.parent
    report_path = _normalized_absolute(argv[5])
    requirements_path = _normalized_absolute(argv[9])
    run_id = report_path.parent.name
    if re.fullmatch(r"[0-9a-f]{16}", run_id) is None:
        raise RuntimeError("PIP_BOOTSTRAP_RUN_ID_REJECTED")
    expected_report = (
        workspace_root
        / "Evidence"
        / "preflight"
        / "reports"
        / run_id
        / "pip-dry-run-report.json"
    )
    expected_requirements = (
        workspace_root
        / "Evidence"
        / "preflight"
        / "temp"
        / run_id
        / "combined-requirements.txt"
    )
    if (
        os.path.normcase(os.fspath(report_path))
        != os.path.normcase(os.fspath(_normalized_absolute(expected_report)))
        or os.path.normcase(os.fspath(requirements_path))
        != os.path.normcase(
            os.fspath(_normalized_absolute(expected_requirements))
        )
    ):
        raise RuntimeError("PIP_BOOTSTRAP_EVIDENCE_PATH_REJECTED")

    try:
        index_url = urlsplit(argv[7])
        port = index_url.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError("PIP_BOOTSTRAP_INDEX_URL_REJECTED") from exc
    if (
        index_url.scheme.casefold() != "https"
        or not index_url.hostname
        or index_url.username is not None
        or index_url.password is not None
        or index_url.query
        or index_url.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise RuntimeError("PIP_BOOTSTRAP_INDEX_URL_REJECTED")
    return report_path, requirements_path


def main() -> int:
    module_path = Path(os.path.abspath(__file__))
    app_root = module_path.parents[2]
    expected_module = app_root / "scripts" / "preflight" / "pip_bootstrap.py"
    expected_python = app_root / "venv" / "Scripts" / "python.exe"
    site_packages = app_root / "venv" / "Lib" / "site-packages"
    pip_package = site_packages / "pip"

    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("PIP_BOOTSTRAP_REQUIRES_ISOLATED_NO_SITE_PYTHON")
    if not sys.dont_write_bytecode:
        raise RuntimeError("PIP_BOOTSTRAP_REQUIRES_NO_BYTECODE_WRITES")
    if os.path.normcase(os.path.abspath(module_path)) != os.path.normcase(
        os.path.abspath(expected_module)
    ):
        raise RuntimeError("PIP_BOOTSTRAP_MODULE_PATH_REJECTED")
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(
        os.path.abspath(expected_python)
    ):
        raise RuntimeError("PIP_BOOTSTRAP_REQUIRES_WORKSPACE_VENV_PYTHON")

    _require_execution_capability()
    report_path, requirements_path = _validate_pip_argv(
        sys.argv[1:],
        app_root,
    )
    for path in (
        app_root,
        module_path,
        app_root / "venv",
        app_root / "venv" / "Lib",
        site_packages,
    ):
        _validate_path(path, allowed_root=app_root)
    _validate_tree(pip_package, allowed_root=app_root)
    _validate_path(pip_package, allowed_root=app_root)
    pip_init = pip_package / "__init__.py"
    pip_main = pip_package / "__main__.py"
    _validate_path(pip_init, allowed_root=app_root)
    _validate_path(pip_main, allowed_root=app_root)
    _validate_path(report_path.parent, allowed_root=app_root.parent)
    _validate_path(requirements_path, allowed_root=app_root.parent)
    for suffix in tuple(dict.fromkeys(all_suffixes())):
        _reject_import_shadow(site_packages / f"pip{suffix}")

    _normalize_pip_config_file()
    spec = importlib.util.spec_from_file_location(
        "pip",
        pip_init,
        submodule_search_locations=[os.fspath(pip_package)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("PIP_BOOTSTRAP_IMPORT_SPEC_REJECTED")
    pip_module = importlib.util.module_from_spec(spec)
    sys.modules["pip"] = pip_module
    spec.loader.exec_module(pip_module)
    runpy.run_path(os.fspath(pip_main), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
