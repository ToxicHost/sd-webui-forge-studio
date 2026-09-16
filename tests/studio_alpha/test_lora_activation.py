"""`<lora:name:weight>` actually applies a LoRA. AR8.2.

THE DEFECT THIS CLOSES

Measured live, with 73 LoRAs listed and one of their real names in the prompt,
same seed and everything else identical:

    pixels differing   0 of 262,144      0.00%

The engine logged it, once per generation:

    INFO root: Skipping unknown extra network: lora

`extra_networks.activate()` IS reached -- `modules/processing.py:981`, inside
`process_images_inner`, which Studio calls directly -- and
`parse_extra_network_prompts()` runs three lines above it. So the tag was
parsed OUT of the prompt and handed to a registry with no handler for it.

WHY THE HANDLER WAS ABSENT

`lora_script.py` registers it inside `before_ui()`, and Forge fires that from
`webui.py:61` -- `api_only_worker` calls `before_ui_callback()` even with no
Gradio. That is why the Extension gets LoRAs for free: it lives inside Forge.

Studio reaches neither half, both refusals deliberate: `backend_bootstrap` does
not run `load_scripts()`, and a Gradio import is a TERMINAL bootstrap failure.
`lora_script.py` uses `gr.Dropdown` at module scope.

THREE THINGS HAD TO BE TRUE, and each was found by the next one failing:

    1. the modules must import        ModuleNotFoundError: No module named
                                      'networks' -- they import each other by
                                      bare name, so the directory goes on
                                      sys.path for the load and comes off after
    2. the handler must register      "Skipping unknown extra network: lora"
    3. its options must resolve       HEADLESS_OPTION_NOT_AVAILABLE: sd_lora

Only the third was visible from reading. The first two came from running it,
and the second only became visible after the failure stopped being swallowed --
which is why `_arm_lora_support` now logs instead of returning quietly.

WHAT THESE TESTS CANNOT DO

Prove a LoRA applies. That needs the engine, a real file and a comparison, and
it is recorded in `Evidence/gpu-alpha-queue/`:

    with vs without   262,113 of 262,144 differ (99.99%), max delta 230/255
    no-LoRA repeat    0 differ -- no state leaked into the next job

These guard the seams that made it impossible.

Review: `Evidence/source-review/AR8.2-lora-activation.md`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXPECTED_ACTIVATION_TESTS = 14

PORT_PY = (APP_ROOT / "forge_headless" / "live_generation_port.py").read_text(
    encoding="utf-8")
BRIDGE_PY = (APP_ROOT / "forge_headless" / "lora_bridge.py").read_text(
    encoding="utf-8")


class TheOptionsTheHandlerReadsResolveTests(unittest.TestCase):
    """The failure that survived two rounds of fixing the import."""

    def test_every_generation_option_has_a_default(self) -> None:
        """The boundary's own promise: "a missing option is found here instead
        of part-way through a generation"."""

        from forge_headless.headless_options import (
            GENERATION_OPTIONS,
            legacy_defaults,
        )

        defaults = legacy_defaults(APP_ROOT)
        missing = [name for name in GENERATION_OPTIONS if name not in defaults]
        self.assertEqual([], missing)

    def test_sd_lora_resolves(self) -> None:
        """The one that actually failed a generation:
        `extra_networks_lora.py:23` opens with `shared.opts.sd_lora`."""

        from forge_headless.headless_options import legacy_defaults

        self.assertEqual("None", legacy_defaults(APP_ROOT).get("sd_lora"))

    def test_the_other_lora_options_resolve(self) -> None:
        from forge_headless.headless_options import legacy_defaults

        defaults = legacy_defaults(APP_ROOT)
        for name, expected in (("lora_preferred_name", "Alias from file"),
                               ("lora_add_hashes_to_infotext", True),
                               ("lora_preset_filter", False)):
            with self.subTest(option=name):
                self.assertEqual(expected, defaults.get(name))

    def test_the_defaults_come_from_the_engines_own_declaration(self) -> None:
        """Parsed from the extension script, not restated here. The boundary
        walks the AST "without importing it", so no Gradio is touched."""

        from forge_headless.headless_options import option_sources

        sources = [str(path) for path in option_sources(APP_ROOT)]
        self.assertTrue(any("lora_script.py" in path for path in sources))

    def test_reading_the_options_imports_no_gradio(self) -> None:
        """`lora_script.py` uses `gr.Dropdown` at module scope. Parsing it must
        never become importing it -- a Gradio import is a TERMINAL bootstrap
        failure by Studio's own rule."""

        from forge_headless.headless_options import parse_legacy_defaults

        before = {name for name in sys.modules if name.startswith("gradio")}
        parse_legacy_defaults(
            APP_ROOT / "extensions-builtin" / "sd_forge_lora" / "scripts"
            / "lora_script.py")
        after = {name for name in sys.modules if name.startswith("gradio")}
        self.assertEqual(before, after)


class TheBridgeRunsForgesOwnStatementsTests(unittest.TestCase):
    """Not Studio inventing an initialization."""

    def test_it_registers_the_same_handler_before_ui_does(self) -> None:
        self.assertIn("extra_networks.register_extra_network(", BRIDGE_PY)
        self.assertIn("ExtraNetworkLora()", BRIDGE_PY)

    def test_it_does_not_register_the_gradio_page(self) -> None:
        """`before_ui`'s third statement. Studio has nowhere to put an
        extra-networks page and does not need one to apply a LoRA."""

        self.assertNotIn("register_page", BRIDGE_PY)

    def test_it_scans_after_projecting_the_roots(self) -> None:
        project = BRIDGE_PY.index("cmd_opts.lora_dirs")
        scan = BRIDGE_PY.index("list_available_networks()")
        self.assertLess(project, scan,
                        "the scan would read the engine's default folder")

    def test_arming_twice_does_not_register_twice(self) -> None:
        """`register_extra_network` appends to a list, so a second arm would
        apply every LoRA twice."""

        from forge_headless import lora_bridge

        self.assertIn("if _ARMED:", BRIDGE_PY)
        self.assertFalse(lora_bridge.armed())

    def test_the_path_entries_are_removed_again(self) -> None:
        """Leaving either would let a later `import network` anywhere in the
        process resolve to a LoRA internal.

        Entries, plural, since AR8.14: the engine root goes on beside the
        extension directory, because `network.py` reaches `modules.*` by bare
        name and `modules` is a namespace package whose search path recomputes
        on exactly the `sys.path` change this function makes.
        """

        at = BRIDGE_PY.index("def _load_modules")
        body = BRIDGE_PY[at:BRIDGE_PY.index("def arm(", at)]
        self.assertIn("finally:", body)
        self.assertIn("sys.path.remove(entry)", body)
        self.assertIn("for entry in added:", body)


class AFailureIsReportedNotSwallowedTests(unittest.TestCase):
    """The change that made the second cause visible at all."""

    def test_the_arming_failure_is_logged(self) -> None:
        at = PORT_PY.index("def _arm_lora_support")
        body = PORT_PY[at:PORT_PY.index("def _soft_inpainting_bridge", at)]
        self.assertIn("logging.getLogger", body)
        self.assertIn("will be", body)

    def test_it_still_never_raises(self) -> None:
        """A LoRA that cannot be armed must not cost the owner the image --
        the prompt still generates, without the network applied."""

        at = PORT_PY.index("def _arm_lora_support")
        body = PORT_PY[at:PORT_PY.index("def _soft_inpainting_bridge", at)]
        self.assertIn("except (Exception, SystemExit)", body)
        self.assertIn("return", body)

    def test_it_is_armed_before_the_denoise(self) -> None:
        armed = PORT_PY.index("_arm_lora_support(request)")
        denoise = PORT_PY.index("processed = process_images_inner(processing)")
        self.assertLess(armed, denoise)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(
            sys.modules[__name__])
        self.assertEqual(EXPECTED_ACTIVATION_TESTS, loaded.countTestCases())


if __name__ == "__main__":
    unittest.main()
