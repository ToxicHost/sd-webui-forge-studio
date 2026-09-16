"""A later Studio process must not destroy an earlier one's results.

The identifier a job is given is also the stem of the file its result is
saved as. It used to come from `itertools.count(1)` held on the
`StudioGeneration` instance, so every process start reissued
`headless-000001`, `headless-000002`, ... over whatever was already in the
result root -- and the writer was a bare `image.save(path)`, which overwrites.

That is not hypothetical. During live verification a restarted Studio
overwrote `headless-000001.png`, the canonical anchor result. It was
recovered only because an unrelated probe had copied it minutes earlier.

Two independent guarantees are pinned here, because either alone is thin:

```text
allocation   a uuid4 stem, so a restart cannot reissue a live name
placement    O_CREAT|O_EXCL, so even a collision cannot overwrite
```

No PIL: the contract suite runs with `-S`, and the writer only needs an
object with `.save(path, format=...)` -- the same shape PIL is called with, so
the existing port test doubles keep working. A stub also lets a failing encode
be tested, which a real encoder makes awkward.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.live_generation_port import (  # noqa: E402
    safe_result_name,
    save_result_exclusively,
)
from forge_headless.studio_generation import (  # noqa: E402
    RESULT_IDENTIFIER_PREFIX,
    HeadlessGenerationSession,
    default_result_identifier,
    studio_code_for,
)
from forge_studio.result_delivery import ResultRegistry  # noqa: E402

LEGACY_NAME = "headless-000001.png"


class _StubImage:
    """Only what the writer uses: `save(path, format=...)`, as PIL is called."""

    def __init__(self, payload: bytes = b"PNG-BYTES", explode: bool = False) -> None:
        self.payload = payload
        self.explode = explode

    def save(self, path: str, format: str = "PNG") -> None:  # noqa: A002
        if self.explode:
            Path(path).write_bytes(b"partial")
            raise RuntimeError("encoder failed")
        Path(path).write_bytes(self.payload)


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


# --------------------------------------------------------------------------
# 1. Allocation: a restart cannot reissue a live name
# --------------------------------------------------------------------------


class DefaultIdentifierTests(unittest.TestCase):
    def test_two_fresh_instances_do_not_agree_on_a_first_identifier(self) -> None:
        # The regression exactly: two processes each started at
        # `headless-000001`.
        first = HeadlessGenerationSession()._identifiers()  # noqa: SLF001
        second = HeadlessGenerationSession()._identifiers()  # noqa: SLF001
        self.assertNotEqual(first, second)

    def test_a_restart_never_reissues_the_name_it_destroyed(self) -> None:
        seen = {HeadlessGenerationSession()._identifiers() for _ in range(25)}  # noqa: SLF001
        self.assertEqual(25, len(seen))
        self.assertNotIn("headless-000001", seen)

    def test_identifiers_are_unique_within_one_instance_too(self) -> None:
        engine = HeadlessGenerationSession()
        seen = {engine._identifiers() for _ in range(200)}  # noqa: SLF001
        self.assertEqual(200, len(seen))

    def test_the_identifier_has_the_documented_opaque_shape(self) -> None:
        value = default_result_identifier()
        self.assertTrue(value.startswith(RESULT_IDENTIFIER_PREFIX))
        suffix = value[len(RESULT_IDENTIFIER_PREFIX):]
        self.assertEqual(32, len(suffix))
        self.assertTrue(all(c in "0123456789abcdef" for c in suffix))

    def test_the_identifier_is_not_sequential(self) -> None:
        # A mutation guard: restoring `itertools.count(1)` as the production
        # default makes this fail, because the suffix would be an integer.
        suffix = default_result_identifier()[len(RESULT_IDENTIFIER_PREFIX):]
        self.assertFalse(suffix.isdigit())

    def test_the_production_default_is_not_a_process_local_counter(self) -> None:
        source = (
            APP_ROOT / "forge_headless" / "studio_generation.py"
        ).read_text(encoding="utf-8")
        # The module still NAMES itertools, in the docstring explaining why it
        # is gone. What must not come back is the dependency and the call.
        self.assertNotIn("import itertools", source)
        self.assertNotIn("self._counter", source)
        self.assertIn("uuid4().hex", source)


class InjectedIdentifierSeamTests(unittest.TestCase):
    def test_a_deterministic_identifier_can_still_be_injected(self) -> None:
        # Existing tests depend on this seam; the fix must not remove it.
        engine = HeadlessGenerationSession(identifiers=lambda: "fixed-id")
        self.assertEqual("fixed-id", engine._identifiers())  # noqa: SLF001


# --------------------------------------------------------------------------
# 2. Placement: even a collision cannot overwrite
# --------------------------------------------------------------------------


class ExclusiveWriteTests(_Root):
    def test_a_result_is_written(self) -> None:
        target = self.root / "headless-abc.png"
        save_result_exclusively(_StubImage(b"first"), target)
        self.assertEqual(b"first", target.read_bytes())

    def test_an_existing_result_is_never_overwritten(self) -> None:
        target = self.root / LEGACY_NAME
        target.write_bytes(b"the-owners-earlier-generation")
        with self.assertRaises(Exception) as caught:
            save_result_exclusively(_StubImage(b"the-restart"), target)
        self.assertEqual(
            "GENERATION_RESULT_IDENTIFIER_COLLISION", caught.exception.code
        )
        # The whole point: the earlier bytes are still there.
        self.assertEqual(b"the-owners-earlier-generation", target.read_bytes())

    def test_the_collision_is_reported_as_a_publication_failure(self) -> None:
        self.assertEqual(
            "RESULT_PUBLICATION_FAILED",
            studio_code_for("GENERATION_RESULT_IDENTIFIER_COLLISION"),
        )

    def test_the_collision_message_carries_no_path(self) -> None:
        target = self.root / LEGACY_NAME
        target.write_bytes(b"x")
        with self.assertRaises(Exception) as caught:
            save_result_exclusively(_StubImage(), target)
        message = str(caught.exception.message)
        self.assertNotIn(str(self.root), message)
        self.assertNotIn(".png", message)

    def test_a_failed_encode_leaves_no_file_occupying_the_identifier(self) -> None:
        # A partial file would answer exists() for a result never produced,
        # and would then collide with its own retry.
        target = self.root / "headless-partial.png"
        with self.assertRaises(RuntimeError):
            save_result_exclusively(_StubImage(explode=True), target)
        self.assertFalse(target.exists())

    def test_the_write_does_not_depend_on_a_prior_exists_check(self) -> None:
        # Two processes can both pass exists() and then both save. The
        # filesystem must be the one that decides, so the guard is O_EXCL.
        source = (
            APP_ROOT / "forge_headless" / "live_generation_port.py"
        ).read_text(encoding="utf-8")
        self.assertIn("os.O_EXCL", source)
        self.assertNotIn("images[0].save(str(self._result_root", source)


# --------------------------------------------------------------------------
# 3. Concurrency: N writers, zero losses
# --------------------------------------------------------------------------


class ConcurrentAllocationTests(_Root):
    def test_parallel_writers_produce_distinct_files_and_lose_nothing(self) -> None:
        sentinel = self.root / LEGACY_NAME
        sentinel.write_bytes(b"pre-existing")

        writers = 24
        identifiers: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(writers)
        lock = threading.Lock()

        def write_one() -> None:
            identifier = default_result_identifier()
            name = safe_result_name(identifier, index=0)
            barrier.wait()
            try:
                save_result_exclusively(_StubImage(identifier.encode()), self.root / name)
            except BaseException as exc:  # noqa: BLE001 - recorded, not swallowed
                with lock:
                    errors.append(exc)
                return
            with lock:
                identifiers.append(identifier)

        threads = [threading.Thread(target=write_one) for _ in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors)
        self.assertEqual(writers, len(set(identifiers)))
        # One file per writer, plus the sentinel, and the sentinel untouched.
        self.assertEqual(writers + 1, len(list(self.root.iterdir())))
        self.assertEqual(b"pre-existing", sentinel.read_bytes())
        for identifier in identifiers:
            self.assertEqual(
                identifier.encode(), (self.root / f"{identifier}.png").read_bytes()
            )

    def test_only_one_writer_can_claim_a_contended_identifier(self) -> None:
        # Directly exercises the reservation rather than trusting timing: all
        # writers are handed the SAME name on purpose.
        name = safe_result_name(default_result_identifier(), index=0)
        winners: list[int] = []
        collisions: list[int] = []
        writers = 12
        barrier = threading.Barrier(writers)
        lock = threading.Lock()

        def claim(n: int) -> None:
            barrier.wait()
            try:
                save_result_exclusively(_StubImage(str(n).encode()), self.root / name)
            except Exception:  # noqa: BLE001 - collision is the expected outcome
                with lock:
                    collisions.append(n)
                return
            with lock:
                winners.append(n)

        threads = [threading.Thread(target=claim, args=(n,)) for n in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, len(winners))
        self.assertEqual(writers - 1, len(collisions))
        self.assertEqual(str(winners[0]).encode(), (self.root / name).read_bytes())


# --------------------------------------------------------------------------
# 4. request_id identity and both filename generations stay readable
# --------------------------------------------------------------------------


class IdentifierNameRelationTests(unittest.TestCase):
    def test_the_filename_stem_is_the_request_id(self) -> None:
        identifier = default_result_identifier()
        self.assertEqual(
            f"{identifier}.png", safe_result_name(identifier, index=7)
        )

    def test_the_identifier_survives_the_name_sanitiser_unchanged(self) -> None:
        # uuid4 hex plus the prefix is 41 characters, inside the 64 cap, and
        # every character is already safe -- so the stem cannot be truncated
        # into a collision.
        identifier = default_result_identifier()
        self.assertLess(len(identifier), 64)
        self.assertEqual(identifier, safe_result_name(identifier, index=0)[:-4])

    def test_a_legacy_numeric_identifier_still_names_a_file(self) -> None:
        self.assertEqual(LEGACY_NAME, safe_result_name("headless-000001", index=0))


class BothFilenameGenerationsAreServableTests(_Root):
    def _register_and_read(self, name: str, payload: bytes) -> bytes:
        path = self.root / name
        path.write_bytes(payload)
        registry = ResultRegistry(self.root)
        asset = registry.register(path, media_type="image/png")
        return registry.read(asset.handle).content

    def test_legacy_numeric_results_remain_readable(self) -> None:
        # Existing owner results must keep working with no migration.
        self.assertEqual(
            b"legacy", self._register_and_read(LEGACY_NAME, b"legacy")
        )

    def test_new_uuid_results_are_readable(self) -> None:
        name = safe_result_name(default_result_identifier(), index=0)
        self.assertEqual(b"new", self._register_and_read(name, b"new"))

    def test_both_generations_coexist_in_one_root(self) -> None:
        legacy = self.root / LEGACY_NAME
        legacy.write_bytes(b"legacy")
        modern = self.root / safe_result_name(default_result_identifier(), index=0)
        modern.write_bytes(b"modern")
        registry = ResultRegistry(self.root)
        legacy_handle = registry.register(legacy, media_type="image/png")
        modern_handle = registry.register(modern, media_type="image/png")
        self.assertNotEqual(legacy_handle.handle, modern_handle.handle)
        self.assertEqual(b"legacy", registry.read(legacy_handle.handle).content)
        self.assertEqual(b"modern", registry.read(modern_handle.handle).content)


# --------------------------------------------------------------------------
# 5. Restart persistence rehearsal (synthetic)
# --------------------------------------------------------------------------


class RestartPersistenceRehearsalTests(_Root):
    def test_a_second_process_preserves_the_first_processes_result(self) -> None:
        """The exact live sequence that destroyed the anchor, synthetically.

        Session A writes a result. A brand-new engine is constructed, standing
        in for a restarted process. Session B writes its result. A must still
        be there, byte for byte, and B must be a different file.
        """

        engine_a = HeadlessGenerationSession()
        identifier_a = engine_a._identifiers()  # noqa: SLF001
        name_a = safe_result_name(identifier_a, index=0)
        save_result_exclusively(_StubImage(b"session-A-pixels"), self.root / name_a)

        # Restart: nothing carried over but the result root itself.
        engine_b = HeadlessGenerationSession()
        identifier_b = engine_b._identifiers()  # noqa: SLF001
        name_b = safe_result_name(identifier_b, index=0)
        save_result_exclusively(_StubImage(b"session-B-pixels"), self.root / name_b)

        self.assertNotEqual(identifier_a, identifier_b)
        self.assertNotEqual(name_a, name_b)
        self.assertEqual(b"session-A-pixels", (self.root / name_a).read_bytes())
        self.assertEqual(b"session-B-pixels", (self.root / name_b).read_bytes())
        self.assertEqual(2, len(list(self.root.iterdir())))

    def test_the_rehearsal_also_holds_when_the_root_already_has_legacy_results(
        self,
    ) -> None:
        legacy = self.root / LEGACY_NAME
        legacy.write_bytes(b"the-canonical-anchor")
        for _ in range(5):
            identifier = HeadlessGenerationSession()._identifiers()  # noqa: SLF001
            save_result_exclusively(
                _StubImage(b"x"),
                self.root / safe_result_name(identifier, index=0),
            )
        self.assertEqual(b"the-canonical-anchor", legacy.read_bytes())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
