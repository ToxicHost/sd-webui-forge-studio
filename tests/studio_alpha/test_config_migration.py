"""What a pre-P0.3 configuration file must do on a post-P0.3 launch.

The old file said *which model*: a `profiles` list of absolute payload paths,
a `selected_profile_id` naming one of them, and an `autoload` flag guarding an
explicit load. All three are gone as decisions. The file that still contains
them is on real disks right now, so the contract these tests hold is narrow
and load-bearing:

```text
it must start                legacy keys are read and ignored, never refused
it must not load             a config file cannot decide what is resident
it must stay NO_MODEL        a cold launch is identical to a cold launch
nothing may be rewritten     retired keys stay in the owner's file
```

`last_model_selection` replaces `selected_profile_id`, and the whole point is
that it is *not* the same thing. It restores three dropdowns. It is read by
the page and by nothing else. If any code path ever loads from it, these tests
should be the ones that notice.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA,
no torch, no generation, no server.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.model_roots import ModelRootRegistry  # noqa: E402
from forge_studio.launch import load_config  # noqa: E402
from forge_studio.model_root_settings import (  # noqa: E402
    SELECTION_KEY,
    ModelRootSettings,
)

WORKSPACE_ROOT = APP_ROOT.parent

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_MIGRATION_TESTS = 33

#: A well-formed catalogue id: 32 lowercase hex, as `catalogue.py` mints them.
ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
ID_C = "00112233445566778899aabbccddeeff"

#: The exact shape found in a real owner's file before P0.3e.
LEGACY = {
    "autoload": False,
    "backend": "mock",
    "host": "127.0.0.1",
    "port": 0,
    "result_root": "Studio-Results",
    "profiles": [{
        "profile_id": "local",
        "display_name": "Hicks_Anima_Beta",
        "family": "qwen-image",
        "payload_references": {
            "checkpoint": "Z:/private/Models/Checkpoints/Hicks.safetensors",
            "text_encoder": "Z:/private/Models/Text-Encoders/Qwen3.safetensors",
            "vae": "Z:/private/Models/VAE/qwen_image_vae.safetensors",
        },
    }],
    "selected_profile_id": "local",
}


class _ConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="cfg-migration-")).resolve()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.path = self.directory / "studio-config.json"

    def write(self, payload: dict) -> Path:
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        return self.path


# --------------------------------------------------------------------------
# 1. A legacy file starts
# --------------------------------------------------------------------------


class LegacyToleranceTests(_ConfigCase):
    def test_the_real_legacy_document_loads(self) -> None:
        config = load_config(self.write(dict(LEGACY)))
        self.assertEqual("mock", config["backend"])

    def test_it_names_exactly_what_it_ignored(self) -> None:
        config = load_config(self.write(dict(LEGACY)))
        self.assertEqual(
            ("profiles", "selected_profile_id", "autoload"),
            config["legacy_keys"],
        )

    def test_no_retired_key_survives_into_the_process(self) -> None:
        config = load_config(self.write(dict(LEGACY)))
        for key in ("profiles", "selected_profile_id", "autoload"):
            self.assertNotIn(key, config)

    def test_no_payload_path_survives_into_the_process(self) -> None:
        """The strongest form of "it does not load": the paths are not merely
        unused, they are not reachable. Nothing downstream could open one even
        by mistake, because nothing downstream is handed one."""

        config = load_config(self.write(dict(LEGACY)))
        rendered = json.dumps(config, default=str)
        self.assertNotIn("Z:/private", rendered)
        self.assertNotIn("safetensors", rendered)

    def test_a_profile_naming_a_file_that_is_gone_still_loads(self) -> None:
        payload = dict(LEGACY)
        payload["profiles"] = [{
            "profile_id": "stale", "display_name": "Stale", "family": "f",
            "payload_references": {"checkpoint": "Z:/deleted/model.safetensors"},
        }]
        self.assertEqual("mock", load_config(self.write(payload))["backend"])

    def test_a_structurally_broken_profile_still_loads(self) -> None:
        """Deliberately not validated at all. Validating a key that decides
        nothing can only produce refusals that mean nothing."""

        payload = dict(LEGACY)
        payload["profiles"] = "this was never even a list"
        payload["selected_profile_id"] = 17
        payload["autoload"] = "yes please"
        config = load_config(self.write(payload))
        self.assertEqual(
            ("profiles", "selected_profile_id", "autoload"),
            config["legacy_keys"],
        )

    def test_a_file_with_no_retired_keys_reports_none(self) -> None:
        payload = {k: v for k, v in LEGACY.items()
                   if k not in ("profiles", "selected_profile_id", "autoload")}
        self.assertEqual((), load_config(self.write(payload))["legacy_keys"])

    def test_the_live_contract_is_unchanged_for_everything_else(self) -> None:
        """Tolerance is scoped to the retired keys. A wrong host is still a
        refusal, and must stay one."""

        from forge_studio.launch import LaunchConfigurationError

        payload = dict(LEGACY)
        payload["host"] = "0.0.0.0"
        with self.assertRaises(LaunchConfigurationError):
            load_config(self.write(payload))


# --------------------------------------------------------------------------
# 2. The remembered selection is a preference
# --------------------------------------------------------------------------


class RememberedSelectionTests(_ConfigCase):
    def test_a_complete_selection_is_read(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {
            "checkpoint": ID_A, "text_encoder": ID_B, "vae": ID_C,
        }
        self.assertEqual(
            {"checkpoint": ID_A, "text_encoder": ID_B, "vae": ID_C},
            load_config(self.write(payload))["last_model_selection"],
        )

    def test_a_partial_selection_is_kept_partial(self) -> None:
        """Two of three is a real state: the owner picked a checkpoint and a
        VAE and had not chosen an encoder yet. Filling the gap with a default
        would be choosing a model on their behalf."""

        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {"checkpoint": ID_A, "vae": ID_C}
        self.assertEqual(
            {"checkpoint": ID_A, "vae": ID_C},
            load_config(self.write(payload))["last_model_selection"],
        )

    def test_an_id_is_normalised_to_lower_case(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {"checkpoint": ID_A.upper()}
        self.assertEqual(
            {"checkpoint": ID_A},
            load_config(self.write(payload))["last_model_selection"],
        )

    def test_a_malformed_entry_is_dropped_without_refusing(self) -> None:
        """Individually, not wholesale. The worst outcome of a bad preference
        is a dropdown that opens unselected, so it must never be the reason a
        launch fails -- but a good sibling entry should still be honoured."""

        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {
            "checkpoint": ID_A, "text_encoder": "not-an-id", "vae": 12,
        }
        self.assertEqual(
            {"checkpoint": ID_A},
            load_config(self.write(payload))["last_model_selection"],
        )

    def test_an_unknown_role_is_ignored(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {"checkpoint": ID_A, "lora": ID_B}
        self.assertEqual(
            {"checkpoint": ID_A},
            load_config(self.write(payload))["last_model_selection"],
        )

    def test_a_non_object_selection_is_nothing(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = "local"
        self.assertEqual({}, load_config(self.write(payload))["last_model_selection"])

    def test_an_id_naming_a_model_that_no_longer_exists_still_loads(self) -> None:
        """Shape is checked; existence is not. A model deleted since the last
        launch must not stop this one -- the owner needs the app running in
        order to pick a different one."""

        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {"checkpoint": ID_A}
        config = load_config(self.write(payload))
        self.assertEqual({"checkpoint": ID_A}, config["last_model_selection"])


# --------------------------------------------------------------------------
# 3. It is not a load instruction
# --------------------------------------------------------------------------


class NoLoadTests(_ConfigCase):
    def build(self, payload: dict):  # type: ignore[no-untyped-def]
        from forge_studio.composition import build_standalone

        config = load_config(self.write(payload))
        results = self.directory / "results"
        results.mkdir(exist_ok=True)
        return config, build_standalone(
            backend_kind="mock", result_root=results
        )

    def test_a_legacy_config_leaves_the_lifecycle_in_no_model(self) -> None:
        _config, composition = self.build(dict(LEGACY))
        with composition:
            self.assertEqual(
                "no_model", composition.model_lifecycle.state()["state"]
            )

    def test_a_remembered_selection_leaves_the_lifecycle_in_no_model(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {
            "checkpoint": ID_A, "text_encoder": ID_B, "vae": ID_C,
        }
        _config, composition = self.build(payload)
        with composition:
            self.assertEqual(
                "no_model", composition.model_lifecycle.state()["state"]
            )
            self.assertIsNone(composition.model_lifecycle.session)

    def test_the_launcher_never_hands_the_preference_to_the_lifecycle(self) -> None:
        """A source pin, because this is the mistake with no symptom. Reading
        the remembered value anywhere near the lifecycle would recreate
        `selected_profile_id` under a new name, and the only visible
        difference would be that a cold launch stopped being cold."""

        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        run_body = source.split("def run(", 1)[1]
        for line in run_body.splitlines():
            if "last_model_selection" not in line:
                continue
            self.assertNotIn("lifecycle", line)
            self.assertNotIn("ensure_loaded", line)


# --------------------------------------------------------------------------
# 4. The write preserves the owner's file
# --------------------------------------------------------------------------


class PreferencePersistenceTests(_ConfigCase):
    def settings(self, payload: dict) -> ModelRootSettings:
        self.write(payload)
        config = load_config(self.path)
        return ModelRootSettings(
            ModelRootRegistry(workspace_root=WORKSPACE_ROOT),
            config_path=self.path,
            last_model_selection=config["last_model_selection"],
        )

    def test_the_seeded_preference_is_readable(self) -> None:
        payload = dict(LEGACY)
        payload[SELECTION_KEY] = {"checkpoint": ID_A}
        described = self.settings(payload).describe()
        self.assertEqual({"checkpoint": ID_A}, described[SELECTION_KEY])

    def test_a_write_reaches_the_file(self) -> None:
        settings = self.settings(dict(LEGACY))
        result = settings.remember_selection(
            {SELECTION_KEY: {"checkpoint": ID_A, "vae": ID_C}}
        )
        self.assertTrue(result["persisted"])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({"checkpoint": ID_A, "vae": ID_C}, saved[SELECTION_KEY])

    def test_a_write_does_not_rewrite_the_rest_of_the_file(self) -> None:
        """Including the retired keys. They do nothing, but the file belongs
        to the owner, and quietly dropping whatever a new version stopped
        reading is how an upgrade becomes data loss for anyone who downgrades
        or hand-edits."""

        settings = self.settings(dict(LEGACY))
        settings.remember_selection({SELECTION_KEY: {"checkpoint": ID_A}})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(LEGACY["profiles"], saved["profiles"])
        self.assertEqual("local", saved["selected_profile_id"])
        self.assertEqual("Studio-Results", saved["result_root"])

    def test_a_malformed_id_is_refused_at_the_write(self) -> None:
        """Tolerant on read, strict on write. A bad value already on disk is
        the owner's history and must not stop a launch; a bad value arriving
        from the page is a bug being written down."""

        settings = self.settings(dict(LEGACY))
        with self.assertRaises(HeadlessError) as caught:
            settings.remember_selection({SELECTION_KEY: {"checkpoint": "nope"}})
        self.assertEqual("STUDIO_SETTINGS_MALFORMED", caught.exception.code)

    def test_clearing_a_role_is_allowed(self) -> None:
        settings = self.settings(dict(LEGACY))
        settings.remember_selection({SELECTION_KEY: {"checkpoint": ID_A}})
        settings.remember_selection({SELECTION_KEY: {"checkpoint": ""}})
        self.assertEqual({}, settings.describe()[SELECTION_KEY])

    def test_a_write_never_records_a_path(self) -> None:
        settings = self.settings(dict(LEGACY))
        with self.assertRaises(HeadlessError):
            settings.remember_selection(
                {SELECTION_KEY: {"checkpoint": "Z:/private/x.safetensors"}}
            )


# --------------------------------------------------------------------------
# 5. Ordered roots, and the downgrade the narrowing write protects
# --------------------------------------------------------------------------


class RootsShapeMigrationTests(_ConfigCase):
    """`model_roots` became a variable-length list. Neither direction may
    strand an owner.

    The forward direction is easy: a string is read as one root. The backward
    direction is the one worth testing, because it fails at STARTUP and an
    older build has no way to recover -- its `normalize_roots` accepts only
    strings, and until this phase that refusal exited 2.
    """

    def roots_settings(self, payload: dict):  # type: ignore[no-untyped-def]
        self.write(payload)
        return ModelRootSettings(
            ModelRootRegistry(workspace_root=WORKSPACE_ROOT),
            config_path=self.path,
        )

    def test_a_single_root_persists_as_a_string(self) -> None:
        """So a file written by THIS build stays readable to one that has
        never heard of lists."""

        root = self.directory / "models"
        root.mkdir()
        settings = self.roots_settings(dict(LEGACY))
        settings.apply({"model_roots": {"checkpoint": str(root)}})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIsInstance(saved["model_roots"]["checkpoint"], str)

    def test_several_roots_persist_as_a_list(self) -> None:
        first = self.directory / "one"
        second = self.directory / "two"
        first.mkdir()
        second.mkdir()
        settings = self.roots_settings(dict(LEGACY))
        settings.apply(
            {"model_roots": {"checkpoint": [str(first), str(second)]}}
        )
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            [str(first), str(second)], saved["model_roots"]["checkpoint"]
        )

    def test_an_emptied_role_is_omitted_rather_than_written_as_a_list(self) -> None:
        """`[]` is exactly the value that breaks an older build while meaning
        nothing more than 'absent'."""

        root = self.directory / "models2"
        root.mkdir()
        settings = self.roots_settings(dict(LEGACY))
        settings.apply({"model_roots": {"checkpoint": str(root)}})
        settings.apply({"model_roots": {"checkpoint": []}})
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("checkpoint", saved["model_roots"])

    def test_a_roots_list_no_longer_stops_an_older_build_from_starting(self) -> None:
        """The downgrade proof, driven through `load_config` itself.

        A list is what an older `normalize_roots` refuses. That refusal used
        to be fatal at startup -- exit 2, with the app that edits model
        directories being the app that would not launch. It is tolerated now:
        the roles come up unconfigured and the reason is carried through.
        """

        payload = dict(LEGACY)
        payload["model_roots"] = {"checkpoint": ["Z:/one", "Z:/two"]}
        config = load_config(self.write(payload))
        self.assertEqual(("Z:/one", "Z:/two"), config["model_roots"]["checkpoint"])
        self.assertIsNone(config["model_roots_refusal"])

    #: The roles whose directories the OWNER supplies. `adetailer` is not one
    #: of them by default: Studio ships five hash-recorded detectors inside its
    #: own install and seeds that role itself, because requiring the owner to
    #: type a path to reach what shipped is the config-file chore the standing
    #: ruling forbids. A malformed `model_roots` says nothing about Studio's
    #: own directory, so withholding the bundled detectors over it would punish
    #: an unrelated thing.
    OWNER_ROLES = ("checkpoint", "text_encoder", "vae", "lora")

    def assertNoOwnerRootAdopted(self, config) -> None:
        adopted = sorted(
            role for role in self.OWNER_ROLES if config["model_roots"].get(role)
        )
        self.assertEqual([], adopted)

    def test_a_malformed_roots_value_starts_unconfigured_and_says_why(self) -> None:
        payload = dict(LEGACY)
        payload["model_roots"] = "not an object at all"
        config = load_config(self.write(payload))
        self.assertNoOwnerRootAdopted(config)
        self.assertEqual(
            "HEADLESS_MODEL_ROOTS_MALFORMED", config["model_roots_refusal"]
        )

    def test_an_unreadable_roots_value_does_not_load_a_model(self) -> None:
        """Tolerating it must not be mistaken for ignoring it. Startup stays
        NO_MODEL either way."""

        payload = dict(LEGACY)
        payload["model_roots"] = 17
        config = load_config(self.write(payload))
        self.assertNoOwnerRootAdopted(config)
        self.assertTrue(config["model_roots_refusal"])

    def test_a_refusal_still_leaves_the_bundled_detectors_reachable(self) -> None:
        """The seed is not a way of hiding the refusal: the reason is still
        carried, still logged, and every owner-supplied role is still empty."""

        payload = dict(LEGACY)
        payload["model_roots"] = "not an object at all"
        config = load_config(self.write(payload))
        self.assertTrue(config["model_roots_refusal"])
        self.assertEqual(
            ("adetailer",), tuple(sorted(config["model_roots"])))


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_MIGRATION_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
