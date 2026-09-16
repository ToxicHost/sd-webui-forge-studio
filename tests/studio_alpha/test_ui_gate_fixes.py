"""Contracts for the Internal Alpha UI gate fixes (defects D1-D4).

The first live UI smoke stopped BEFORE payload access because the shipped
frontend could not satisfy the smoke's own success criteria. Each defect was
bounded, and each fix here is pinned:

* **D1 -- coordinator transport.** The lifecycle Generate action submits
  through ``POST /api/generate`` under one public job id; the Model panel
  owns the job rows, the queued Cancel, and the opaque result handles. No
  lifecycle frontend source calls ``/studio/generate``, and the legacy
  route survives only for source-compatible hosts, without silent fallback.

* **D2 -- local-only assets.** The font-CDN stylesheet is gone; no frontend
  file references an external asset of any kind.

* **D3 -- flushed readiness.** ``STUDIO_READY host=... port=...`` is
  emitted exactly once, flushed, only after a successful bind, and a piped
  consumer without ``-u`` reads it promptly. A refused configuration exits
  2 with no ready line. Proven over the documented command by
  ``_ui_gate_probe.py`` in a subprocess (the canonical runner forbids
  dialling and serving in-process).

* **D4 -- deterministic first run.** The launcher validates an
  ``onboarding`` mode; ``suppressed`` announces ``?onboarding=off`` and the
  frontend honours the flag per-visit without writing anything, so a fresh
  profile reaches the Model panel on first render in smoke mode while owner
  mode keeps its once-only overlays.

SCOPE: source pins and configuration rows run in-process with no server;
the launcher facts come from the probe subprocess; the complete
browser-driven flow is the milestone's rehearsal evidence.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FRONTEND = APP_ROOT / "forge_studio" / "frontend"


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# D1 -- the coordinator transport in the shipped frontend source
# ---------------------------------------------------------------------------

class CoordinatorTransportSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_js = _read("app.js")
        cls.panel = _read("studio-model-controls.js")

    def test_lifecycle_panel_speaks_only_the_coordinator_api(self) -> None:
        self.assertIn('generate: "/api/generate"', self.panel)
        self.assertIn("submitGenerate", self.panel)
        # The pin the handoff names: no lifecycle frontend source calls the
        # legacy blocking route.
        self.assertNotIn("/studio/generate", self.panel)

    def test_panel_lifecycle_availability_starts_false(self) -> None:
        # A Generate click must never route to a coordinator this host has
        # not yet proven to exist; only a successful state poll affirms.
        self.assertIn("lifecycleAvailable: false", self.panel)
        self.assertIn("state.lifecycleAvailable === true", self.panel)

    def test_panel_exposes_a_frozen_transport_surface(self) -> None:
        self.assertIn("window.StudioModelControls = Object.freeze({", self.panel)
        for member in ("lifecycleAvailable:", "loadedModelId:",
                       "submitGenerate,"):
            self.assertIn(member, self.panel)

    def test_generate_routes_lifecycle_hosts_before_any_legacy_work(self) -> None:
        body = self.app_js[self.app_js.index("async function doGenerate("):]
        lifecycle_branch = body.index("StudioModelControls")
        legacy_call = body.index("API.generate(")
        self.assertLess(
            lifecycle_branch, legacy_call,
            "the lifecycle branch must run before the legacy transport",
        )
        # The branch submits through the panel and RETURNS; the legacy
        # preflight guard only applies after it.
        branch = body[:body.index("if (State.generating || State._preflighting)")]
        self.assertIn("lifecycle.submitGenerate(jobParams)", branch)
        self.assertNotIn("/studio/generate", branch)
        self.assertNotIn("API.generate(", branch)

    def test_lifecycle_branch_never_falls_back_to_the_legacy_route(self) -> None:
        body = self.app_js[self.app_js.index("async function doGenerate("):]
        branch = body[:body.index("if (State.generating || State._preflighting)")]
        # The error path surfaces a toast and returns -- no reroute.
        self.assertIn("catch (error)", branch)
        self.assertIn("showToast(String(error.message || error)", branch)
        self.assertIn("return;", branch)

    def test_legacy_route_survives_for_source_compatible_hosts(self) -> None:
        # Exactly one transport binding to the legacy route: the API map
        # entry that non-lifecycle hosts still use.
        self.assertEqual(
            1,
            self.app_js.count('API.post("/studio/generate"'),
        )

    def test_submission_payload_matches_the_validated_contract(self) -> None:
        body = self.app_js[self.app_js.index("async function doGenerate("):]
        branch = body[:body.index("if (State.generating || State._preflighting)")]
        for field in ("model:", "positive_prompt:", "negative_prompt:",
                      "seed:", "steps:", "cfg_scale:", "width:", "height:"):
            self.assertIn(field, branch)


# ---------------------------------------------------------------------------
# D2 -- every frontend asset is local
# ---------------------------------------------------------------------------

class LocalAssetTests(unittest.TestCase):
    def test_index_references_no_external_asset(self) -> None:
        html = _read("index.html")
        for marker in ("https://fonts.googleapis.com",
                       "https://fonts.gstatic.com"):
            self.assertNotIn(marker, html)
        references = re.findall(
            r'(?:href|src)\s*=\s*"([^"]+)"', html
        )
        external = [ref for ref in references
                    if ref.startswith(("http:", "https:", "//"))]
        self.assertEqual([], external)

    def test_no_stylesheet_pulls_an_external_url(self) -> None:
        for css_path in sorted(FRONTEND.glob("*.css")):
            with self.subTest(stylesheet=css_path.name):
                source = css_path.read_text(encoding="utf-8")
                self.assertEqual(
                    [],
                    re.findall(r"url\(\s*['\"]?(?:https?:)?//", source),
                )

    def test_font_stacks_fall_back_to_system_faces(self) -> None:
        css = _read("app.css")
        self.assertIn(
            "--font: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;",
            css,
        )
        self.assertIn("--mono: 'JetBrains Mono', 'Fira Code', monospace;", css)
        # The only bundled faces stay the OFL-licensed local files.
        self.assertTrue((FRONTEND / "fonts" / "OFL.txt").is_file())


# ---------------------------------------------------------------------------
# D4 -- launcher onboarding modes and the per-visit frontend guard
# ---------------------------------------------------------------------------

class OnboardingPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        from forge_studio.launch import LaunchConfigurationError, load_config

        self.load_config = load_config
        self.error = LaunchConfigurationError
        self.scratch = APP_ROOT / "outputs" / f"uigate-cfg-{os.getpid()}"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def _write(self, **overrides: object) -> Path:
        payload: dict[str, object] = {
            "backend": "mock",
            "host": "127.0.0.1",
            "port": 0,
            "result_root": str(self.scratch),
            "autoload": False,
            "profiles": [],
        }
        payload.update(overrides)
        path = self.scratch / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_onboarding_defaults_to_owner_mode(self) -> None:
        config = self.load_config(self._write())
        self.assertEqual("default", config["onboarding"])

    def test_onboarding_accepts_both_documented_modes(self) -> None:
        for mode in ("default", "suppressed"):
            with self.subTest(mode=mode):
                config = self.load_config(self._write(onboarding=mode))
                self.assertEqual(mode, config["onboarding"])

    def test_onboarding_refuses_anything_else_plainly(self) -> None:
        for wrong in ("off", "smoke", True, 1, None):
            with self.subTest(value=wrong):
                with self.assertRaises(self.error) as caught:
                    self.load_config(self._write(onboarding=wrong))
                self.assertIn("onboarding must be 'default'",
                              str(caught.exception))

    def test_frontend_guards_are_per_visit_and_write_nothing(self) -> None:
        for name in ("app.js", "education.js"):
            with self.subTest(module=name):
                source = _read(name)
                index = source.index("_onboardingSuppressedByUrl")
                guard = source[index:index + 400]
                self.assertIn('.get("onboarding") === "off"', guard)
                self.assertNotIn("localStorage.setItem", guard)

    def test_education_overlay_honours_the_smoke_flag(self) -> None:
        education = _read("education.js")
        self.assertEqual(
            2,
            education.count("!_onboardingSuppressedByUrl()"),
            "both first-run trigger sites carry the guard",
        )

    def test_first_run_card_honours_the_smoke_flag(self) -> None:
        app_js = _read("app.js")
        first_run = app_js[app_js.index("function _maybeShowFirstRun"):]
        first_run = first_run[:first_run.index("}")]
        self.assertIn("_onboardingSuppressedByUrl()", first_run)


# ---------------------------------------------------------------------------
# D3 -- launcher readiness over the documented command (probe subprocess)
# ---------------------------------------------------------------------------

class LauncherReadinessTests(unittest.TestCase):
    """One probe run shared by every assertion; launcher facts by scenario."""

    _report: dict | None = None

    @classmethod
    def setUpClass(cls) -> None:
        scratch = APP_ROOT / "outputs" / f"uigate-probe-{os.getpid()}"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(APP_ROOT / "venv" / "Scripts" / "python.exe"), "-I", "-B",
                 str(Path(__file__).resolve().parent / "_ui_gate_probe.py"),
                 str(scratch)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180, cwd=str(APP_ROOT),
            )
            output = completed.stdout
            begin = output.index("PROBE_JSON_BEGIN") + len("PROBE_JSON_BEGIN")
            end = output.index("PROBE_JSON_END")
            cls._report = json.loads(output[begin:end].strip())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def setUp(self) -> None:
        self.assertIsNotNone(self._report, "probe produced no report")
        self.assertIsNone(self._report.get("fatal"), self._report.get("fatal"))

    def test_ready_line_is_read_promptly_without_dash_u(self) -> None:
        ready = self._report["ready"]
        self.assertTrue(ready["read_without_dash_u"])
        self.assertRegex(
            ready["ready_line"], r"^STUDIO_READY host=127\.0\.0\.1 port=\d+$"
        )
        self.assertLess(ready["seconds_to_ready"], 20.0)

    def test_ready_line_is_emitted_exactly_once(self) -> None:
        ready = self._report["ready"]
        self.assertEqual(0, ready["extra_ready_lines"])

    def test_ready_line_carries_scalars_only_and_binds_loopback(self) -> None:
        ready = self._report["ready"]
        self.assertEqual("127.0.0.1", ready["host"])
        self.assertTrue(ready["port_is_integer"])
        self.assertNotIn("\\", ready["ready_line"])
        self.assertNotIn("/", ready["ready_line"].replace("127.0.0.1", ""))

    def test_announced_port_accepts_a_loopback_connect(self) -> None:
        ready = self._report["ready"]
        self.assertEqual(200, ready["loopback_connect_status"])
        self.assertEqual("no_model", ready["state"])

    def test_launcher_stops_within_bounds(self) -> None:
        self.assertTrue(self._report["ready"]["stopped"])
        self.assertTrue(self._report["suppressed"]["stopped"])

    def test_refused_configuration_emits_no_false_ready_line(self) -> None:
        refused = self._report["refused"]
        self.assertEqual(2, refused["returncode"])
        self.assertFalse(refused["ready_line_emitted"])
        self.assertTrue(refused["plain_sentence"])

    def test_default_mode_announces_an_unflagged_url(self) -> None:
        ready = self._report["ready"]
        self.assertTrue(ready["url_line_present"])
        self.assertTrue(ready["default_mode_has_no_flag"])

    def test_suppressed_mode_announces_the_smoke_url(self) -> None:
        suppressed = self._report["suppressed"]
        self.assertTrue(suppressed["ready_line"])
        self.assertTrue(suppressed["url_carries_flag"])

    def test_shutdown_announcements_are_flushed_in_source(self) -> None:
        # Windows cannot deliver a console Ctrl+C to a piped child reliably,
        # so the Ctrl+C shutdown line is pinned at source level: every
        # launcher print flushes.
        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        prints = re.findall(r"print\([^)]*\)", source, flags=re.DOTALL)
        for statement in prints:
            with self.subTest(statement=statement[:60]):
                self.assertIn("flush=True", statement)


# ---------------------------------------------------------------------------
# Section 9 -- startup safety of the launcher composition path
# ---------------------------------------------------------------------------

class StartupSafetyTests(unittest.TestCase):
    def test_launcher_validation_and_composition_import_no_model_stack(self) -> None:
        script = r"""
import json, sys
from pathlib import Path
APP_ROOT = Path(sys.argv[1])
sys.path.insert(0, str(APP_ROOT))
import launch_studio  # the entry point
from forge_studio.launch import load_config
scratch = Path(sys.argv[2])
config_path = scratch / "probe.json"
references = {"checkpoint": "synthetic://c", "text_encoder": "synthetic://t",
              "vae": "synthetic://v"}
config_path.write_text(json.dumps({
    "backend": "mock", "host": "127.0.0.1", "port": 0,
    "result_root": str(scratch), "autoload": False,
    "onboarding": "suppressed",
    "profiles": [{"profile_id": "alpha", "display_name": "Alpha",
                   "family": "qwen-image",
                   "payload_references": references}],
}), encoding="utf-8")
config = load_config(config_path)
from forge_studio.composition import build_standalone
from forge_studio.model_profiles import ModelProfile
composition = build_standalone(
    backend_kind="mock",
    profiles=[ModelProfile(profile_id="alpha", display_name="Alpha",
                            family="qwen-image",
                            payload_references=references)],
    result_root=config["result_root"],
)
state = composition.model_lifecycle.state()["state"]
composition.shutdown()
forbidden = sorted(
    name for name in sys.modules
    if name == "torch" or name.startswith("torch.")
    or name == "backend" or name.startswith("backend.")
    or name == "modules" or name.startswith("modules.")
    or name == "gradio" or name.startswith("gradio.")
)
print(json.dumps({"state": state, "forbidden": forbidden}))
"""
        scratch = APP_ROOT / "outputs" / f"uigate-safety-{os.getpid()}"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(APP_ROOT / "venv" / "Scripts" / "python.exe"),
                 "-I", "-S", "-B", "-c", script, str(APP_ROOT), str(scratch)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, cwd=str(APP_ROOT),
            )
            self.assertEqual(0, completed.returncode, completed.stderr[-800:])
            report = json.loads(completed.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        # Selection is not a step the probe can take any more, so what it
        # measures is the cold build alone -- which is the stronger claim:
        # nothing has been asked of the lifecycle when the module set is read.
        self.assertEqual("no_model", report["state"])
        self.assertEqual([], report["forbidden"])


if __name__ == "__main__":
    unittest.main()
