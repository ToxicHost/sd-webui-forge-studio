"""Static module-level import-graph analysis, standard library only.

Used to answer "would importing this package pull in Gradio?" *without*
importing it. That distinction matters: importing
``backend.memory_management`` probes device memory at module scope, so a claim
about it must be reachable statically or not made at all.

Only import-time edges count. Skipped deliberately:

    - bodies of ``if TYPE_CHECKING:``          -- never executed
    - imports inside a function or class body -- lazy, not import-time

A ``try``/``except`` import *is* counted: the attempt happens at import time.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GraphResult:
    """Outcome of a reachability question over one package tree."""

    modules_parsed: int
    unparsable: tuple[str, ...]
    offenders: tuple[tuple[str, str], ...]

    @property
    def clean(self) -> bool:
        return not self.offenders and not self.unparsable


def _is_type_checking(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def module_level_imports(tree: ast.Module) -> set[str]:
    """Every module named by an import that runs at import time."""

    found: set[str] = set()

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                continue
            if isinstance(node, ast.If):
                if _is_type_checking(node):
                    walk(node.orelse)
                    continue
                walk(node.body)
                walk(node.orelse)
                continue
            if isinstance(node, ast.Try):
                walk(node.body)
                for handler in node.handlers:
                    walk(handler.body)
                walk(node.orelse)
                walk(node.finalbody)
                continue
            if isinstance(node, (ast.With, ast.AsyncWith, ast.For, ast.While)):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and not node.level:
                    found.add(node.module)
                    # `from modules import ui_tempdir` imports a *module*, and
                    # recording only "modules" would miss it. Names that are
                    # ordinary objects rather than modules produce a harmless
                    # extra entry that no caller matches.
                    for alias in node.names:
                        if alias.name != "*":
                            found.add(f"{node.module}.{alias.name}")

    walk(tree.body)
    return found


def module_name_for(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def scan_package(
    package_root: Path,
    repository_root: Path,
    forbidden: tuple[str, ...],
) -> GraphResult:
    """Report every module in a tree whose import-time graph names a forbidden top-level package."""

    offenders: list[tuple[str, str]] = []
    unparsable: list[str] = []
    parsed = 0

    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = module_name_for(path, repository_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            unparsable.append(name)
            continue
        parsed += 1
        for imported in sorted(module_level_imports(tree)):
            head = imported.split(".", 1)[0]
            if head in forbidden:
                offenders.append((name, imported))

    return GraphResult(
        modules_parsed=parsed,
        unparsable=tuple(unparsable),
        offenders=tuple(offenders),
    )


def resolve_module_path(name: str, repository_root: Path) -> Path | None:
    """Locate a module's source file inside the repository, or ``None``."""

    relative = name.replace(".", "/")
    for candidate in (
        repository_root / f"{relative}.py",
        repository_root / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def module_edges(name: str, repository_root: Path) -> set[str]:
    """Module-level imports of one module, plus each import's parent package."""

    path = resolve_module_path(name, repository_root)
    if path is None:
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    edges: set[str] = set()
    for imported in module_level_imports(tree):
        edges.add(imported)
        if "." in imported:
            edges.add(imported.rsplit(".", 1)[0])
    return edges


def paths_to_forbidden(
    seed: str,
    repository_root: Path,
    forbidden: tuple[str, ...],
    *,
    limit: int = 40,
) -> list[list[str]]:
    """Every module-level import chain from ``seed`` into a forbidden package.

    Breadth-first, so the shortest chains come first, and each module is expanded
    once. An empty result is the proof that no module-level path exists.
    """

    found: list[list[str]] = []
    seen: set[str] = set()
    queue: deque[list[str]] = deque([[seed]])
    while queue and len(found) < limit:
        chain = queue.popleft()
        current = chain[-1]
        if current in seen:
            continue
        seen.add(current)
        for nxt in sorted(module_edges(current, repository_root)):
            if nxt in chain:
                continue
            if nxt.split(".", 1)[0] in forbidden:
                found.append(chain + [nxt])
                continue
            if resolve_module_path(nxt, repository_root) is not None:
                queue.append(chain + [nxt])
    return found
