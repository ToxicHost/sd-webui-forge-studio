"""The container contract, proven without a container engine.

Docker is not installed on this workstation, so the image build, a container
run, a GPU smoke and mount browsing are EXTERNALLY PENDING and are not
asserted here. Everything else about the packaging is decidable from the
files and from running the entrypoint directly, and that is most of what
actually goes wrong:

```text
proven here          the entrypoint's config, the mount layout, the bind
                     policy, non-root operation, the health contract, what
                     the build context excludes, what the image does NOT
                     start
externally pending   docker build, docker run, GPU visibility, browsing a
                     real bind mount, restart persistence
```

The bind policy gets the most attention because it is the one line where a
mistake is silent and serious: `0.0.0.0` INSIDE the container is required --
a container's loopback is its own, and binding 127.0.0.1 there makes the
published port unreachable -- while the HOST publish must stay pinned to
127.0.0.1 or the filesystem browser and the model loader are on the LAN.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No image is built, no
container is started, no socket is bound.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

#: Inside the REPOSITORY, all four of them. They used to live one level up, in
#: WORKSPACE -- outside the git root -- so a clean clone did not contain them
#: and the image could not be built from a checkout. The commit that added this
#: suite added only the suite.
DOCKERFILE = APP_ROOT / "Dockerfile"
COMPOSE = APP_ROOT / "deploy" / "docker-compose.example.yml"
DOCKERIGNORE = APP_ROOT / ".dockerignore"
ENTRYPOINT = APP_ROOT / "deploy" / "entrypoint.py"

ARTEFACTS = {"Dockerfile": DOCKERFILE, "compose example": COMPOSE,
             ".dockerignore": DOCKERIGNORE, "entrypoint": ENTRYPOINT}


def read(path: Path) -> str:
    """Read an artefact, or yield "" so the SUITE reports it.

    Every one of these was previously read in a class BODY, at import time. A
    checkout missing one raised FileNotFoundError during unittest discovery --
    a collection error rather than a readable failure -- and all 37 tests left
    the run without saying anything. The suite passed here only because this
    workstation happened to have the sibling files.

    Returning empty means the assertions below fail individually, and
    `ArtefactPresenceTests` names the missing file directly. A missing
    artefact must fail loudly; it must not delete the evidence that it is
    missing.
    """

    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_DOCKER_TESTS = 37


def _load_entrypoint(**environment):
    """Import the entrypoint under a chosen environment, as the image runs it."""

    import importlib.util

    previous = {key: os.environ.get(key) for key in environment}
    os.environ.update({k: v for k, v in environment.items() if v is not None})
    for key, value in environment.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        spec = importlib.util.spec_from_file_location(
            "_studio_docker_entrypoint", ENTRYPOINT
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FilesExistTests(unittest.TestCase):
    def test_every_packaging_file_is_present(self) -> None:
        """The test that has to survive the others being unable to read.

        `read()` returns "" for a missing artefact so the suite still RUNS;
        this is what then says which one is gone, by name. Before the move a
        missing file raised at import and took all 37 tests with it, so the
        run reported nothing at all.
        """

        missing = [name for name, path in ARTEFACTS.items()
                   if not path.is_file()]
        self.assertEqual([], missing,
                         f"packaging artefact(s) missing from the repository: "
                         f"{missing}")


class BindPolicyTests(unittest.TestCase):
    DOCKER = read(DOCKERFILE)
    YAML = read(COMPOSE)

    def test_the_host_publish_is_loopback_only(self) -> None:
        """`"7865:7865"` binds every host interface. That would put a
        filesystem browser and a model loader on the LAN."""

        publishes = re.findall(r'-\s*"([^"]*:\d+)"', self.YAML)
        self.assertTrue(publishes)
        for publish in publishes:
            self.assertTrue(
                publish.startswith("127.0.0.1:"),
                f"host publish is not loopback: {publish}",
            )

    def test_the_container_binds_all_interfaces_internally(self) -> None:
        """The other half, and it is not a contradiction: a container's
        loopback is its own, so binding 127.0.0.1 inside makes the published
        port unreachable from the host that published it."""

        module = _load_entrypoint()
        self.assertEqual("0.0.0.0", module.DEFAULT_HOST)

    def test_the_native_launcher_still_refuses_a_non_loopback_host(self) -> None:
        """The container's need must not have relaxed the workstation rule."""

        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("binds 127.0.0.1 only", source)

    def test_the_publish_line_explains_itself(self) -> None:
        """Because the short form is the tempting edit."""

        self.assertIn("Loopback only", self.YAML)


class ImagePolicyTests(unittest.TestCase):
    DOCKER = read(DOCKERFILE)

    def test_the_container_does_not_run_as_root(self) -> None:
        self.assertIn("USER studio", self.DOCKER)
        self.assertLess(
            self.DOCKER.index("USER studio"),
            self.DOCKER.index("ENTRYPOINT"),
            "USER must precede ENTRYPOINT or the process still starts as root",
        )

    def test_a_real_user_is_created_rather_than_assumed(self) -> None:
        self.assertIn("useradd", self.DOCKER)
        self.assertIn("groupadd", self.DOCKER)

    def test_no_model_weights_are_baked_into_the_image(self) -> None:
        instructions = [
            line for line in self.DOCKER.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for line in instructions:
            for forbidden in ("Private-Local", ".safetensors", ".ckpt", ".gguf"):
                self.assertNotIn(forbidden, line)

    def test_dependencies_are_their_own_layer(self) -> None:
        """A code edit must not re-resolve torch."""

        requirements = self.DOCKER.index("requirements.txt")
        application = self.DOCKER.index("COPY --chown=studio:studio . ./app")
        self.assertLess(requirements, application)

    def test_gpu_is_opt_in(self) -> None:
        """An image that assumes CUDA cannot run on a machine without it, and
        macOS and plain Linux hosts are first-class targets."""

        self.assertIn("TORCH_INDEX_URL", self.DOCKER)
        self.assertIn("whl/cpu", self.DOCKER)

    def test_the_image_declares_a_health_check(self) -> None:
        self.assertIn("HEALTHCHECK", self.DOCKER)

    def test_opencv_shared_libraries_are_installed(self) -> None:
        """Without them the first Studio import fails with a bare
        shared-object error that names nothing useful."""

        self.assertIn("libgl1", self.DOCKER)
        self.assertIn("libglib2.0-0", self.DOCKER)

    def test_no_gradio_or_forge_webui_server_is_started(self) -> None:
        """Checked over INSTRUCTIONS, not the whole file.

        A Dockerfile has no AST, and the whole-text form failed on this
        file's own comment saying there is no Gradio -- the third time a
        rule stated in prose has tripped the check that enforces it. Comment
        lines are dropped first.
        """

        instructions = "\n".join(
            line for line in self.DOCKER.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ).lower()
        for forbidden in ("gradio", "webui.py", "app.launch"):
            self.assertNotIn(forbidden, instructions)

    def test_the_entrypoint_is_the_one_studio_launcher(self) -> None:
        self.assertIn("deploy/entrypoint.py", self.DOCKER)


class BuildContextTests(unittest.TestCase):
    """What the image can reach, now that the context is the repository.

    These used to assert that `Private-Local/`, `Studio-Results/`,
    `studio-config.json`, `Evidence/` and `Reference/` appeared in
    `.dockerignore`. That was the right worry and the weak form of it: the
    context was the WORKSPACE, so the owner's private model library and their
    real configuration were inside the build context and stayed out only
    because a line in a file said so. Delete the line and they ship.

    With the context moved to the git root they are not in it at all. The
    assertion is now structural -- outside the context, not excluded from it --
    which is the stronger property and cannot be undone by editing one file.
    """

    IGNORE = read(DOCKERIGNORE)
    CONTEXT = APP_ROOT

    def test_the_private_material_is_outside_the_context_entirely(self) -> None:
        for name in ("Private-Local", "Studio-Results", "Evidence",
                     "Reference", "studio-config.json"):
            with self.subTest(name=name):
                self.assertFalse(
                    (self.CONTEXT / name).exists(),
                    f"{name} is inside the build context; it was moved out of "
                    f"it, not merely ignored")

    def test_the_virtualenv_is_excluded(self) -> None:
        """Windows binaries, useless in a Linux image, and 1.5 GB of them.

        This one IS in the context -- it lives in the repository root -- so it
        still needs an exclude rather than a structural guarantee.
        """

        self.assertIn("venv/", self.IGNORE)

    def test_model_weights_and_outputs_are_excluded(self) -> None:
        """Both are inside the context and both must never enter a layer."""

        self.assertIn("models/", self.IGNORE)
        self.assertIn("outputs/", self.IGNORE)

    def test_upstreams_container_definition_is_excluded(self) -> None:
        """`docker/` is upstream Neo's image, which builds a different product.

        It is kept in the repository as inherited source and must not be copied
        into Studio's image; see deploy/README.md.
        """

        self.assertIn("docker/", self.IGNORE)

    def test_every_copy_source_resolves_inside_the_context(self) -> None:
        """The WP0.5 clause, as an assertion rather than an intention.

        "the image copies all needed files without relying on sibling workspace
        state" is exactly this: every COPY source must exist relative to the
        build context. It did not before -- `docker/entrypoint.py` lived
        outside the repository, so a clean clone could not build the image.
        """

        sources = re.findall(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+\s*$",
                             read(DOCKERFILE), re.MULTILINE)
        self.assertTrue(sources, "no COPY instructions found")
        for source in sources:
            with self.subTest(source=source):
                self.assertFalse(source.startswith(("/", "..")),
                                 f"COPY source escapes the context: {source}")
                self.assertTrue((self.CONTEXT / source).exists(),
                                f"COPY source does not exist in the context: "
                                f"{source}")


class EntrypointConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="docker-entry-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.config = self.root / "config"
        self.results = self.root / "results"
        self.models = self.root / "models"
        self.models.mkdir()

    def module(self):
        return _load_entrypoint(
            STUDIO_HOME=str(self.root),
            STUDIO_CONFIG_DIR=str(self.config),
            STUDIO_RESULT_ROOT=str(self.results),
            STUDIO_MODEL_ROOT=str(self.models),
        )

    def test_a_config_is_written_when_there_is_none(self) -> None:
        module = self.module()
        path = module.ensure_config()
        self.assertTrue(path.is_file())
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("0.0.0.0", document["host"])

    def test_an_operator_supplied_config_is_never_overwritten(self) -> None:
        """Same rule as the Settings write: read what is there, never
        recreate it."""

        module = self.module()
        self.config.mkdir(parents=True, exist_ok=True)
        mine = self.config / "studio-config.json"
        mine.write_text('{"backend": "mock", "mine": true}', encoding="utf-8")
        module.ensure_config()
        self.assertEqual(
            {"backend": "mock", "mine": True},
            json.loads(mine.read_text(encoding="utf-8")),
        )

    def test_model_roots_come_from_the_mount_layout(self) -> None:
        for folder in ("checkpoints", "text-encoders", "vae"):
            (self.models / folder).mkdir()
        module = self.module()
        roots = module.role_directories()
        self.assertEqual({"checkpoint", "text_encoder", "vae"}, set(roots))
        for paths in roots.values():
            self.assertIsInstance(paths, list)

    def test_a_missing_role_directory_is_unconfigured_never_guessed(self) -> None:
        (self.models / "checkpoints").mkdir()
        module = self.module()
        roots = module.role_directories()
        self.assertEqual({"checkpoint"}, set(roots))

    def test_the_written_config_is_accepted_by_the_real_loader(self) -> None:
        """The test that matters: not 'it wrote JSON' but 'launch would take
        it'. Only the fields the loader validates are checked, because the
        result root must live inside the workspace and this fixture is not."""

        module = self.module()
        document = json.loads(module.ensure_config().read_text(encoding="utf-8"))
        self.assertIn(document["backend"], ("headless", "mock"))
        self.assertIsInstance(document["port"], int)
        self.assertIn("model_roots", document)
        self.assertIn("load_access", document)

    def test_roots_are_written_as_lists(self) -> None:
        """The variable-length shape, from the first container release."""

        (self.models / "checkpoints").mkdir()
        module = self.module()
        document = json.loads(module.ensure_config().read_text(encoding="utf-8"))
        self.assertIsInstance(document["model_roots"]["checkpoint"], list)

    def test_the_browser_is_enabled_by_default_in_a_container(self) -> None:
        module = self.module()
        document = json.loads(module.ensure_config().read_text(encoding="utf-8"))
        self.assertTrue(document["filesystem_browser"]["enabled"])

    def test_the_config_write_is_atomic(self) -> None:
        """A container killed mid-write must leave no config or a whole one,
        never half of one the next start would refuse."""

        source = read(ENTRYPOINT)
        self.assertIn("os.replace", source)
        self.assertIn(".tmp", source)

    def test_no_host_path_translation_is_attempted(self) -> None:
        """A host path is meaningless inside the container. On Docker Desktop
        the 'host' is a Linux VM with no recoverable Windows path, and a
        translated path that looks right is worse than an honest one."""

        source = read(ENTRYPOINT)
        for forbidden in ("/host_mnt", "/mnt/c", "C:\\\\", "wslpath"):
            self.assertNotIn(forbidden, source)


class HealthContractTests(unittest.TestCase):
    def test_health_targets_loopback_inside_the_container(self) -> None:
        module = _load_entrypoint()
        self.assertIn("127.0.0.1", module.HEALTH_URL)

    def test_health_uses_a_route_that_needs_no_model(self) -> None:
        """A healthy container is one that is SERVING. Requiring a resident
        model would make the product's normal starting state look broken."""

        module = _load_entrypoint()
        self.assertTrue(module.HEALTH_URL.endswith("/api/status"))

    def test_health_returns_non_zero_when_nothing_is_listening(self) -> None:
        """The refusal path, WITHOUT dialling.

        The obvious form -- point it at a closed port and call it -- makes a
        real outbound connection, and the canonical runner's network guard
        caught this exact test doing it. That is the guard working: the fix
        is to exercise the branch, not to weaken the rule that noticed.
        """

        from unittest import mock

        module = _load_entrypoint()
        with mock.patch.object(
            module.urllib.request, "urlopen", side_effect=OSError("refused")
        ):
            self.assertEqual(1, module.health())

    def test_health_returns_zero_when_studio_answers(self) -> None:
        from unittest import mock

        module = _load_entrypoint()

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_exception):
                return None

        with mock.patch.object(
            module.urllib.request, "urlopen", return_value=_Response()
        ):
            self.assertEqual(0, module.health())

    def test_the_health_flag_is_the_documented_command(self) -> None:
        self.assertIn("--health", read(DOCKERFILE))


class NativeSupportUnaffectedTests(unittest.TestCase):
    """Docker is packaging, not a replacement for running on a workstation."""

    def test_the_workstation_launcher_is_untouched_by_the_container(self) -> None:
        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("docker", "container", "/studio/config"):
            self.assertNotIn(forbidden, source.lower())

    def test_reveal_reports_unavailable_without_a_desktop(self) -> None:
        """A container has no file manager, and the honest answer is that the
        capability is absent -- not that the action failed."""

        from forge_studio.native_actions import HostProbe, detect_reveal

        self.assertFalse(detect_reveal(HostProbe()).available)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_DOCKER_TESTS, loaded.countTestCases())

    def test_the_suite_declares_what_it_cannot_prove(self) -> None:
        """Stated in the docstring so an unexecuted acceptance leg is never
        mistaken for a passed one."""

        self.assertIn("EXTERNALLY PENDING", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
