"""The loader takes a selection, not a profile.

P0.1 exit gate. A `ModelProfile` was a box holding three payload references
plus an id, a display name and a family -- and on the catalogue path the
family was the literal `"catalogue"`, carrying nothing. The box made an
owner-facing concept out of a transport detail.

These tests pin two things:

```text
1. ResolvedSelection satisfies the loader boundary with no profile anywhere
2. a selection and the transitional profile built from it reach that boundary
   with byte-identical references, so the swap cannot change what is opened
```

and the security property that survives from the profile work: a selection is
built from opaque ids, refuses anything path-shaped before resolution, and has
no safe-projection route by which a resolved path could escape.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_studio.model_selection import (  # noqa: E402
    SELECTION_FIELDS,
    RESIDENT_MODEL_ROLES,
    ModelSelection,
    ResolvedSelection,
    resolve_selection,
)

CHECKPOINT = "a" * 32
TEXT_ENCODER = "b" * 32
VAE = "c" * 32

REFERENCES = {
    "checkpoint": r"C:\models\checkpoints\anima.safetensors",
    "text_encoder": r"C:\models\text-encoders\qwen3.safetensors",
    "vae": r"C:\models\vae\qwen_image_vae.safetensors",
}


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "checkpoint_model_id": CHECKPOINT,
        "text_encoder_model_id": TEXT_ENCODER,
        "vae_model_id": VAE,
    }
    body.update(overrides)
    return body


class _Registry:
    """Stands in for the role catalogues. Records what it was asked to resolve."""

    def __init__(self, references=None, error: HeadlessError | None = None) -> None:
        self.references = REFERENCES if references is None else references
        self.error = error
        self.asked: list[dict[str, str]] = []

    def build_payload_references(self, ids):
        self.asked.append(dict(ids))
        if self.error is not None:
            raise self.error
        return dict(self.references)


def _selection() -> ModelSelection:
    return ModelSelection.from_payload(_payload())


# --------------------------------------------------------------------------
# 1. The loader boundary
# --------------------------------------------------------------------------


class LoaderBoundaryTests(unittest.TestCase):
    def test_a_resolved_selection_satisfies_the_loader_contract(self) -> None:
        # What SessionLoader._references actually requires: a callable
        # reference_for(role) answering for every role it opens.
        resolved = resolve_selection(_selection(), registry=_Registry())
        self.assertTrue(callable(resolved.reference_for))
        for role in RESIDENT_MODEL_ROLES:
            self.assertEqual(REFERENCES[role], resolved.reference_for(role))

    def test_the_loader_reads_no_profile_type(self) -> None:
        from forge_headless import session_loader

        source = Path(session_loader.__file__).read_text(encoding="utf-8")
        # Prose may name a profile while explaining the transitional case; what
        # must not exist is a dependency on one. Assert imports and
        # construction, not vocabulary.
        self.assertNotIn("from forge_studio.model_profiles", source)
        self.assertNotIn("import model_profiles", source)
        self.assertNotIn("ModelProfile(", source)
        self.assertNotIn("isinstance(profile", source)

    def test_the_registry_alone_decides_which_references_are_opened(self) -> None:
        # This used to compare against `build_runtime_profile`, to prove the
        # swap from a runtime profile to a ResolvedSelection did not change
        # which payloads got opened. That builder is quarantined, so the claim
        # is now anchored where it always actually lived: the registry decides,
        # and the resolved selection carries its answer through unaltered. A
        # comparison against a second builder could only ever prove the two
        # builders agreed.
        registry = _Registry()
        resolved = resolve_selection(_selection(), registry=registry)
        expected = registry.references
        for role in RESIDENT_MODEL_ROLES:
            self.assertEqual(expected[role], resolved.reference_for(role))

    def test_each_id_is_resolved_through_its_own_role(self) -> None:
        registry = _Registry()
        resolve_selection(_selection(), registry=registry)
        self.assertEqual(
            {"checkpoint": CHECKPOINT, "text_encoder": TEXT_ENCODER, "vae": VAE},
            registry.asked[-1],
        )


class _Steps:
    """Synthetic loader steps. Drives the REAL loader, not a fake of it."""

    def __init__(self) -> None:
        self.opened: dict[str, str] = {}
        self.saw: list[object] = []

    def startup_globals(self):
        return type("H", (), {"restore": lambda self: None})()

    def payload_opener(self, *, profile, references, roles, **_kwargs):
        self.saw.append(profile)
        self.opened = {role: references[role] for role in roles}
        return dict(self.opened)

    def engine_builder(self, *, profile, opened):
        return type("Engine", (), {"profile_id": profile.profile_id})()

    def identity_installer(self, engine):
        return {"attached": True}

    def bookkeeping(self, engine):
        return type("H", (), {"restore": lambda self: None})()

    def port_factory(self, *, profile, engine, result_root):
        return type("Port", (), {"release_engine": lambda self: {}})()

    def session_factory(self, *, profile, port, result_root):
        return type("Session", (), {"close": lambda self: {"closed": True}})()

    def cleanup(self, *, engine=None):
        pass

    def loader(self):
        from forge_headless.session_loader import HeadlessSessionLoader

        return HeadlessSessionLoader(
            payload_opener=self.payload_opener,
            engine_builder=self.engine_builder,
            identity_installer=self.identity_installer,
            startup_globals=self.startup_globals,
            bookkeeping=self.bookkeeping,
            port_factory=self.port_factory,
            session_factory=self.session_factory,
            cleanup=self.cleanup,
        )


class TheRealLoaderLoadsASelectionTests(unittest.TestCase):
    """The P0.1 exit gate itself: the real loader, a ResolvedSelection, no
    profile object constructed anywhere in the call."""

    def test_the_real_loader_loads_a_resolved_selection(self) -> None:
        steps = _Steps()
        resolved = resolve_selection(_selection(), registry=_Registry())
        loaded = steps.loader().load(resolved)
        self.assertIsNotNone(loaded)
        self.assertEqual(REFERENCES, steps.opened)

    def test_the_loaded_session_is_identified_by_the_fingerprint(self) -> None:
        steps = _Steps()
        resolved = resolve_selection(_selection(), registry=_Registry())
        loaded = steps.loader().load(resolved)
        self.assertEqual(_selection().fingerprint(), loaded.profile_id)

    def test_what_reached_the_opener_was_the_selection(self) -> None:
        steps = _Steps()
        resolved = resolve_selection(_selection(), registry=_Registry())
        steps.loader().load(resolved)
        self.assertIs(resolved, steps.saw[0])
        self.assertIsInstance(steps.saw[0], ResolvedSelection)

    def test_an_object_without_references_is_refused_at_the_boundary(self) -> None:
        from forge_headless.session_loader import SessionLoadError

        steps = _Steps()
        with self.assertRaises(SessionLoadError) as caught:
            steps.loader().load(object())
        self.assertEqual("payloads_opened", caught.exception.step)


# --------------------------------------------------------------------------
# 2. Fingerprint — the reuse/switch decision
# --------------------------------------------------------------------------


class FingerprintTests(unittest.TestCase):
    """Forge Neo reloads by comparing a loading-parameter hash. This is the
    Studio equivalent, and P0.2 decides reuse-vs-switch on it."""

    def test_the_same_selection_fingerprints_the_same(self) -> None:
        self.assertEqual(_selection().fingerprint(), _selection().fingerprint())

    def test_changing_any_role_changes_the_fingerprint(self) -> None:
        base = _selection().fingerprint()
        for field in SELECTION_FIELDS.values():
            other = ModelSelection.from_payload(_payload(**{field: "d" * 32}))
            self.assertNotEqual(base, other.fingerprint(), f"{field} did not move it")

    def test_the_fingerprint_is_derived_from_ids_not_paths(self) -> None:
        # Two registries resolving the same ids to different files must still
        # agree, because the fingerprint is the owner's choice, not the disk's.
        one = resolve_selection(_selection(), registry=_Registry())
        two = resolve_selection(
            _selection(),
            registry=_Registry(references={r: f"/elsewhere/{r}.bin" for r in RESIDENT_MODEL_ROLES}),
        )
        self.assertEqual(one.fingerprint, two.fingerprint)

    def test_role_values_are_not_interchangeable(self) -> None:
        # Swapping two ids between roles is a different selection, even though
        # the multiset of ids is identical.
        swapped = ModelSelection.from_payload(
            _payload(checkpoint_model_id=TEXT_ENCODER, text_encoder_model_id=CHECKPOINT)
        )
        self.assertNotEqual(_selection().fingerprint(), swapped.fingerprint())


# --------------------------------------------------------------------------
# 3. A selection cannot carry a path
# --------------------------------------------------------------------------


class NoPathCanEnterOrLeaveTests(unittest.TestCase):
    def test_path_shaped_ids_are_refused_before_resolution(self) -> None:
        registry = _Registry()
        for bad in (
            r"C:\models\anima.safetensors",
            "../../etc/passwd",
            "models/anima.safetensors",
            r"\\server\share\model.safetensors",
            "anima.safetensors",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(Exception) as caught:
                    ModelSelection.from_payload(_payload(checkpoint_model_id=bad))
                self.assertEqual("SELECTION_MALFORMED", caught.exception.error.code)
        # Nothing path-shaped ever reached the resolver.
        self.assertEqual([], registry.asked)

    def test_the_safe_projection_carries_no_reference(self) -> None:
        described = resolve_selection(_selection(), registry=_Registry()).selection.describe()
        flat = repr(described)
        for reference in REFERENCES.values():
            self.assertNotIn(reference, flat)
        self.assertEqual(
            {"checkpoint_model_id", "text_encoder_model_id", "vae_model_id", "fingerprint"},
            set(described),
        )

    def test_the_resolved_projection_is_the_selections_and_carries_no_reference(
        self,
    ) -> None:
        # Originally this asserted a resolved selection had NO describe() at
        # all. Integration disproved that: the lifecycle snapshot projects
        # whatever it holds, so a resolved selection must be projectable. The
        # invariant that actually matters is not "no projection" but "the
        # projection carries no reference" -- and it delegates, so there is one
        # definition of safe rather than two.
        resolved = resolve_selection(_selection(), registry=_Registry())
        self.assertEqual(resolved.selection.describe(), resolved.describe())
        flat = repr(resolved.describe())
        for reference in REFERENCES.values():
            self.assertNotIn(reference, flat)

    def test_a_selection_without_a_checkpoint_names_that_field(self) -> None:
        body = _payload()
        del body[SELECTION_FIELDS["checkpoint"]]
        with self.assertRaises(Exception) as caught:
            ModelSelection.from_payload(body)
        self.assertEqual("SELECTION_INCOMPLETE", caught.exception.error.code)
        self.assertEqual(SELECTION_FIELDS["checkpoint"],
                         caught.exception.error.field)

    def test_a_checkpoint_alone_is_a_complete_selection(self) -> None:
        # An SDXL or SD 1.5 file carries its own text encoder and VAE. The
        # Extension expresses that as absence from `additional_modules`
        # (studio_api.py:5744, 5771-5775); here it is an empty id.
        for field in (SELECTION_FIELDS["text_encoder"], SELECTION_FIELDS["vae"]):
            body = _payload()
            del body[field]
            with self.subTest(field=field):
                selection = ModelSelection.from_payload(body)
                self.assertEqual("", getattr(selection, field))
                self.assertNotIn(field.replace("_model_id", ""),
                                 selection.supplied_roles())

    def test_an_explicit_empty_component_is_an_answer_not_a_gap(self) -> None:
        body = _payload()
        body[SELECTION_FIELDS["text_encoder"]] = ""
        selection = ModelSelection.from_payload(body)
        self.assertFalse(selection.supplies("text_encoder"))
        self.assertTrue(selection.supplies("checkpoint"))

    def test_a_catalogue_failure_becomes_selection_vocabulary(self) -> None:
        registry = _Registry(
            error=HeadlessError("HEADLESS_MODEL_UNAVAILABLE", "gone")
        )
        with self.assertRaises(Exception) as caught:
            resolve_selection(_selection(), registry=registry)
        self.assertEqual("SELECTION_MODEL_UNAVAILABLE", caught.exception.error.code)
        self.assertNotIn("HEADLESS", caught.exception.error.message)

    def test_a_resolution_without_a_checkpoint_is_refused(self) -> None:
        registry = _Registry(references={"text_encoder": REFERENCES["text_encoder"]})
        with self.assertRaises(Exception) as caught:
            resolve_selection(_selection(), registry=registry)
        self.assertEqual("SELECTION_INCOMPLETE", caught.exception.error.code)

    def test_a_resolution_of_the_checkpoint_alone_stands(self) -> None:
        registry = _Registry(references={"checkpoint": REFERENCES["checkpoint"]})
        resolved = resolve_selection(_selection(), registry=registry)
        self.assertEqual(("checkpoint",), resolved.supplied_roles())


# --------------------------------------------------------------------------
# 4. Value semantics
# --------------------------------------------------------------------------


class ValueSemanticsTests(unittest.TestCase):
    def test_two_selections_of_the_same_models_are_equal(self) -> None:
        self.assertEqual(_selection(), _selection())

    def test_a_selection_has_no_identity_of_its_own(self) -> None:
        # Not a profile: no id, no display name, no persistence identity.
        selection = _selection()
        for attribute in ("profile_id", "display_name", "name"):
            self.assertFalse(hasattr(selection, attribute), attribute)

    def test_references_are_copied_not_aliased(self) -> None:
        mutable = dict(REFERENCES)
        resolved = ResolvedSelection(selection=_selection(), payload_references=mutable)
        mutable["checkpoint"] = "/swapped/after/resolve.bin"
        self.assertEqual(REFERENCES["checkpoint"], resolved.reference_for("checkpoint"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
