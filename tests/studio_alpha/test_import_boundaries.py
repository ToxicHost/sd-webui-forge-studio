"""Import-boundary tests for the independently launchable Studio package."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import json
import subprocess
import sys
import unittest


APP_ROOT = Path(__file__).resolve().parents[2]
STUDIO_PACKAGE_ROOT = APP_ROOT / "forge_studio"
FRONTEND_ROOT = STUDIO_PACKAGE_ROOT / "frontend"

NEO_UI_IMPORT_ROOTS = {
    "gradio",
    "modules",
    "modules_forge",
    "webui",
}

CANONICAL_TOOL_ORDER = (
    "brush",
    "eraser",
    "eyedropper",
    "fill",
    "gradient",
    "shape",
    "text",
    "smudge",
    "blur",
    "dodge",
    "clone",
    "liquify",
    "pixelate",
    "select",
    "ellipse",
    "lasso",
    "wand",
    "crop",
    "transform",
)


def _fresh_import(module_name: str) -> set[str]:
    """Every module resident after importing `module_name` IN A NEW PROCESS.

    This used to purge `forge_studio.*` from `sys.modules`, re-import in
    process, and return `set(sys.modules)` -- which is every module ANY
    earlier test happened to leave behind, not what this import pulled in.

    So the result depended on test ORDER, and the failure it produced was a
    lie in the worst direction: a suite that legitimately touched
    `modules.platform_selection` made this report that `forge_studio` imports
    Neo's UI. That happened, under the canonical runner, when the environment
    bootstrap suite was added -- and the note in this repo's memory says it
    has happened before.

    Measuring the DELTA instead would swap one hole for another: if something
    already imported `modules`, `forge_studio` importing it too would be a
    no-op and invisible. A child interpreter is the only measurement that
    cannot be fooled either way, and it is what the assertion has always
    MEANT -- "importing this, on its own, does not drag in the engine".

    Costs about a fifth of a second per call, four calls in this file.
    """

    probe = (
        "import json, sys;"
        f"import {module_name};"
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(APP_ROOT), capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"importing {module_name} in a fresh interpreter failed:"
            f"{chr(10)}{result.stderr.strip()}")
    return set(json.loads(result.stdout))


def _absolute_import_roots(path: Path) -> set[str]:
    """Return top-level names imported by one Python source file."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
        ):
            roots.add(node.module.partition(".")[0])
    return roots


class StudioImportBoundaryTests(unittest.TestCase):
    def test_package_import_does_not_construct_or_import_neo_ui(self) -> None:
        imported = _fresh_import("forge_studio")
        forbidden = {
            name
            for name in imported
            if name == "webui"
            or name.startswith("webui.")
            or name == "modules"
            or name.startswith("modules.")
            or name == "modules_forge"
            or name.startswith("modules_forge.")
        }
        self.assertEqual(set(), forbidden)

    def test_package_import_does_not_construct_or_import_gradio_ui(self) -> None:
        imported = _fresh_import("forge_studio")
        forbidden = {
            name
            for name in imported
            if name == "gradio" or name.startswith("gradio.")
        }
        self.assertEqual(set(), forbidden)

    def test_launch_import_does_not_load_models(self) -> None:
        imported = _fresh_import("launch_studio")
        forbidden_roots = ("gradio", "modules", "modules_forge", "webui")
        forbidden_fragments = ("model_management", "sd_models")
        forbidden = {
            name
            for name in imported
            if any(
                name == root or name.startswith(f"{root}.")
                for root in forbidden_roots
            )
            or any(fragment in name.casefold() for fragment in forbidden_fragments)
        }
        self.assertEqual(set(), forbidden)

    def test_launch_import_does_not_initialize_cuda(self) -> None:
        imported = _fresh_import("launch_studio")
        forbidden = {
            name
            for name in imported
            if name == "torch"
            or name.startswith("torch.")
            or "cuda" in name.casefold()
        }
        self.assertEqual(set(), forbidden)

    def test_studio_python_sources_do_not_import_neo_or_gradio_ui(self) -> None:
        violations: dict[str, tuple[str, ...]] = {}
        for source_path in sorted(STUDIO_PACKAGE_ROOT.rglob("*.py")):
            forbidden = _absolute_import_roots(source_path) & NEO_UI_IMPORT_ROOTS
            if forbidden:
                relative_path = source_path.relative_to(APP_ROOT).as_posix()
                violations[relative_path] = tuple(sorted(forbidden))
        self.assertEqual({}, violations)

    def test_default_frontend_is_canonical_source_shell(self) -> None:
        html = (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")
        module_system = (FRONTEND_ROOT / "module-system.js").read_text(
            encoding="utf-8"
        )

        for element_id in (
            "appTabs",
            "app-studio",
            "toolstrip",
            "canvasArea",
            "ctxBarWrap",
            "contextBar",
            "studio-viewport",
            "studio-canvas",
            "canvasPreviewWrap",
            "canvasPreview",
            "deckZone",
            "sessionStrip",
            "panelDivider",
            "panelCollapseBtn",
            "panelRight",
            "panelTabs",
            "page-generate",
            "paramModel",
            "paramPrompt",
            "paramNeg",
            "paramSeed",
            "paramSteps",
            "paramCFG",
            "paramWidth",
            "paramHeight",
            "genBtn",
            "interruptBtn",
            "workflowSelect",
            "statusDims",
            "statusVRAM",
            "statusModel",
            "statusDot",
            "statusText",
        ):
            self.assertIn(f'id="{element_id}"', html)

        tools = tuple(re.findall(r'\bdata-tool="([^"]+)"', html))
        self.assertEqual(CANONICAL_TOOL_ORDER, tools)
        self.assertIn(
            'const TAB_ORDER = ["studio", "develop", "gallery", "workshop", '
            '"lexicon", "codex", "settings"];',
            module_system,
        )

        for rejected_contract in (
            "data-studio-surface",
            "data-studio-milestone",
            'id="outputImage"',
            'id="outputMetadata"',
            'id="errorPanel"',
        ):
            with self.subTest(rejected_contract=rejected_contract):
                self.assertNotIn(rejected_contract, html)

        shell_sources = "\n".join((html, module_system)).casefold()
        for forbidden_hook in (
            "gradio",
            "gradioapp(",
            "window.gradio_config",
            "/gradio_api/",
            "#component-",
        ):
            with self.subTest(forbidden_hook=forbidden_hook):
                self.assertNotIn(forbidden_hook, shell_sources)


if __name__ == "__main__":
    unittest.main()
