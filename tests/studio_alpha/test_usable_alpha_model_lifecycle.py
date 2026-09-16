"""Contracts for the usable-alpha model and warm-session lifecycle.

Studio owns the decision a user makes -- which checkpoint, text encoder and
VAE -- instead of inferring it from whether a backend happens to hold a model.
Making that selection resident is ONE act: a job names its selection and
`ensure_loaded` reconciles it against what is resident, loading, reusing or
switching exactly once. There is no owner-facing Load, no selected-but-not-
loaded state, and no second identity for "what is loaded". That still turns
"is something loaded" from a boolean into a state machine with named refusals,
and still gives the warm session exactly one owner.

Nothing here opens a model, initializes a device or binds a socket. The loader
and closer are injected synthetic callables, so the whole lifecycle is
exercised at full fidelity with no payload in sight.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE. No model file, no CUDA, no
torch, no generation, no server.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.composition import build_standalone  # noqa: E402
from forge_studio.contracts import StudioError  # noqa: E402
from forge_studio.model_lifecycle import (  # noqa: E402
    MODEL_ALREADY_LOADING,
    MODEL_BUSY,
    MODEL_LOAD_FAILED,
    MODEL_NOT_READY,
    MODEL_NOT_SELECTED,
    MODEL_SESSION_SHUTDOWN,
    MODEL_SWITCH_FAILED,
    MODEL_TRANSITION_REJECTED,
    TRANSITIONS,
    ModelLifecycle,
    ModelLifecycleState,
    WarmSessionManager,
    transition_table,
)
from forge_studio.model_profiles import (  # noqa: E402
    MODEL_PROFILE_DUPLICATE,
    MODEL_PROFILE_INVALID,
    KNOWN_ROLES,
    REQUIRED_ROLES,
    ModelProfile,
    ModelProfileRepository,
)
from forge_studio.model_service import ModelLifecycleService  # noqa: E402
from forge_studio.settings import SettingsService, StudioSettings  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
#: 106 -> 103 with the PROFILE_SELECTED collapse. Three tests were retired
#: outright rather than re-expressed, because their SUBJECT was the two-step
#: `select` -> `load` architecture and not a property that outlived it:
#:
#:   test_selecting_an_unknown_profile_is_not_found
#:   test_switching_to_an_unknown_profile_is_not_found   (replaced in kind)
#:       both read the profile repository by id for an owner action. A job
#:       now CARRIES its selection, so there is no id to look up.
#:   test_selecting_while_ready_is_refused_in_favour_of_switching
#:       the product no longer refuses here -- it switches. That behaviour is
#:       asserted in ChangedSelectionTests instead.
#:   test_selection_is_persisted_through_settings
#:       exercised `_persist_selection`, deleted with its only two callers.
#:       A remembered selection returns in P0.3e as a UI preference, which is
#:       a different claim and must never become a load instruction.
#:
#: 103 -> 98 retiring the profile-shaped surface that had no production caller
#: left. Five more went, and again because their SUBJECT died, not because a
#: property stopped mattering:
#:
#:   test_same_selection_compares_payloads_not_just_the_id
#:       `ModelProfile.same_selection` answered "would selecting this be a
#:       no-op" for a `select` step that no longer exists. The QUESTION still
#:       matters and is still asserted -- of the selection, in ChangedSelection
#:       Tests, which is where load-vs-reuse-vs-switch is actually decided.
#:   test_order_is_declaration_order          (`repository.ids`)
#:   test_an_unknown_id_is_not_found          (`repository.get`)
#:       the repository's lookup surface. Nothing looks anything up in it: the
#:       three model ids travel on the generation request.
#:   test_listing_profiles_reports_configured_identity_only
#:   test_reading_the_selected_profile_before_selecting_is_refused
#:       `list_profiles` / `selected_profile` answered `/api/profiles`, retired.
#:
#: Deliberately NOT deleted: test_no_service_response_carries_a_payload_reference
#: is a LEAK test, so it keeps its subject and merely sweeps two fewer
#: responses; and test_the_resident_selection_is_readable_once_a_job_asks keeps
#: its subject and now reads `state()`, which is what /api/model/state answers.
EXPECTED_ALPHA_TESTS = 99

S = ModelLifecycleState

#: A private-looking reference. It never reaches a public projection; the tests
#: assert that, which is the only reason it looks like a path at all.
_SECRET = "Z:/private/secret-checkpoint.safetensors"


def profile(profile_id: str = "alpha", **overrides) -> ModelProfile:
    payload = {
        "profile_id": profile_id,
        "display_name": f"Profile {profile_id}",
        "family": "anima",
        "payload_references": {
            "checkpoint": f"{_SECRET}#{profile_id}",
            "text_encoder": f"{_SECRET}#te",
            "vae": f"{_SECRET}#vae",
        },
    }
    payload.update(overrides)
    return ModelProfile(**payload)


class SyntheticSession:
    """Stands in for a loaded model session."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.closed = False
        self.jobs: list[str] = []

    def close(self) -> None:
        self.closed = True


class Loader:
    """Records every load and close, and can be made to fail on demand."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.loaded: list[str] = []
        self.closed: list[str] = []
        self.live: list[SyntheticSession] = []
        self.overlap = 0

    def load(self, model_profile: ModelProfile) -> SyntheticSession:
        if model_profile.profile_id in self.fail_on:
            raise RuntimeError("synthetic load failure")
        if any(not s.closed for s in self.live):
            self.overlap += 1
        session = SyntheticSession(model_profile.profile_id)
        self.loaded.append(model_profile.profile_id)
        self.live.append(session)
        return session

    def close(self, session: SyntheticSession) -> None:
        session.close()
        self.closed.append(session.profile_id)


def manager(*, profiles=None, loader: Loader | None = None, **kwargs) -> WarmSessionManager:
    loader = loader or Loader()
    repository = profiles or ModelProfileRepository([profile("a"), profile("b")])
    return WarmSessionManager(
        profiles=repository, loader=loader.load, closer=loader.close, **kwargs
    )


def ready_manager(loader: Loader | None = None, profile_id: str = "a"):
    loader = loader or Loader()
    warm = manager(loader=loader)
    warm.ensure_loaded(profile(profile_id))
    return warm, loader


# ------------------------------------------------------------------ profiles


class ModelProfileTests(unittest.TestCase):
    def test_a_complete_profile_validates(self) -> None:
        self.assertEqual("alpha", profile().profile_id)

    def test_every_required_role_is_required(self) -> None:
        for role in REQUIRED_ROLES:
            references = {r: "x" for r in REQUIRED_ROLES}
            del references[role]
            with self.assertRaises(StudioError) as caught:
                profile(payload_references=references)
            self.assertEqual(MODEL_PROFILE_INVALID, caught.exception.error.code)
            self.assertEqual(role, caught.exception.error.field)

    def test_an_empty_reference_is_refused(self) -> None:
        references = {r: "x" for r in REQUIRED_ROLES}
        references["vae"] = "   "
        with self.assertRaises(StudioError):
            profile(payload_references=references)

    def test_an_unknown_role_is_refused(self) -> None:
        references = {r: "x" for r in REQUIRED_ROLES}
        references["lora"] = "x"
        with self.assertRaises(StudioError):
            profile(payload_references=references)

    def test_identity_fields_must_be_non_empty(self) -> None:
        for field_name in ("profile_id", "display_name", "family"):
            with self.assertRaises(StudioError) as caught:
                profile(**{field_name: "  "})
            self.assertEqual(field_name, caught.exception.error.field)

    def test_metadata_must_be_scalar(self) -> None:
        with self.assertRaises(StudioError):
            profile(metadata={"session": object()})

    def test_scalar_metadata_is_accepted(self) -> None:
        described = profile(metadata={"steps": 12, "beta": True, "note": "x"}).describe()
        self.assertEqual({"steps": 12, "beta": True, "note": "x"}, described["metadata"])

    def test_the_public_projection_carries_no_payload_reference(self) -> None:
        described = profile().describe()
        serialized = json.dumps(described)
        self.assertNotIn(_SECRET, serialized)
        self.assertNotIn("safetensors", serialized)
        self.assertNotIn("checkpoint=", serialized)

    def test_the_public_projection_reports_which_roles_are_configured(self) -> None:
        # `roles_configured` reports what the profile NAMES, which is now a
        # variable set: a checkpoint that bundles its own text encoder and VAE
        # names one role and is complete. `complete` still means "has what it
        # must", so it stays keyed on REQUIRED_ROLES.
        described = profile().describe()
        self.assertEqual(sorted(KNOWN_ROLES), described["roles_configured"])
        self.assertTrue(described["complete"])

    def test_a_checkpoint_only_profile_is_complete(self) -> None:
        subject = ModelProfile(
            profile_id="sdxl", display_name="SDXL", family="sdxl",
            payload_references={"checkpoint": "x"},
        )
        described = subject.describe()
        self.assertEqual(["checkpoint"], described["roles_configured"])
        self.assertTrue(described["complete"])

    def test_a_profile_is_immutable(self) -> None:
        subject = profile()
        with self.assertRaises(Exception):
            subject.profile_id = "other"  # type: ignore[misc]

    def test_mutating_the_source_mapping_cannot_change_the_profile(self) -> None:
        references = {r: "x" for r in REQUIRED_ROLES}
        subject = ModelProfile(
            profile_id="a", display_name="A", family="anima",
            payload_references=references,
        )
        references["checkpoint"] = "tampered"
        self.assertEqual("x", subject.reference_for("checkpoint"))

    def test_equality_is_by_value(self) -> None:
        self.assertEqual(profile("a"), profile("a"))
        self.assertNotEqual(profile("a"), profile("b"))

    def test_reference_for_refuses_an_unknown_role(self) -> None:
        with self.assertRaises(StudioError):
            profile().reference_for("lora")


class ModelProfileRepositoryTests(unittest.TestCase):
    def test_an_empty_repository_is_valid(self) -> None:
        self.assertEqual(0, len(ModelProfileRepository()))

    def test_duplicate_ids_are_refused(self) -> None:
        with self.assertRaises(StudioError) as caught:
            ModelProfileRepository([profile("a"), profile("a")])
        self.assertEqual(MODEL_PROFILE_DUPLICATE, caught.exception.error.code)

    def test_the_public_list_leaks_no_reference(self) -> None:
        repository = ModelProfileRepository([profile("a"), profile("b")])
        self.assertNotIn(_SECRET, json.dumps(repository.describe()))

    def test_a_non_profile_is_refused(self) -> None:
        with self.assertRaises(StudioError):
            ModelProfileRepository(["a"])  # type: ignore[list-item]

    def test_a_profile_from_another_class_object_is_accepted(self) -> None:
        """Regression: the canonical runner purges `forge_studio`.

        `test_import_boundaries` removes the package from `sys.modules` to prove
        a fresh import stays clean, so a later deferred import inside
        `composition.build` produces a **second** `ModelProfile` class object.
        An `isinstance` check against the first one failed there and only there
        -- five composition tests errored under the canonical runner while
        passing under discovery. The repository now checks the surface.
        """

        class ForeignProfile:
            profile_id = "a"
            display_name = "A"
            family = "anima"
            payload_references = {role: "x" for role in REQUIRED_ROLES}

            def describe(self):
                return {"profile_id": "a"}

            def reference_for(self, role):
                return "x"

        repository = ModelProfileRepository([ForeignProfile()])
        self.assertEqual(1, len(repository))
        self.assertEqual([{"profile_id": "a"}], repository.describe())

    def test_an_object_missing_the_profile_surface_is_refused(self) -> None:
        class NotAProfile:
            profile_id = "a"

        with self.assertRaises(StudioError):
            ModelProfileRepository([NotAProfile()])

    def test_a_repository_is_recognised_by_surface_not_identity(self) -> None:
        from forge_studio.model_profiles import looks_like_profile_repository

        self.assertTrue(looks_like_profile_repository(ModelProfileRepository()))
        self.assertFalse(looks_like_profile_repository([profile("a")]))
        self.assertFalse(looks_like_profile_repository(None))


# ------------------------------------------------------------- state machine


class StateMachineTests(unittest.TestCase):
    def test_every_declared_state_appears_in_the_table(self) -> None:
        self.assertEqual(set(ModelLifecycleState), set(TRANSITIONS))

    def test_shutdown_is_reachable_from_every_state(self) -> None:
        for state, targets in TRANSITIONS.items():
            if state is S.SHUTDOWN:
                continue
            self.assertIn(S.SHUTDOWN, targets, state.value)

    def test_shutdown_is_terminal(self) -> None:
        self.assertEqual(frozenset(), TRANSITIONS[S.SHUTDOWN])

    def test_the_required_transitions_are_all_allowed(self) -> None:
        # A job that names its selection takes NO_MODEL straight to LOADING,
        # and recovers a FAILED lifecycle the same way -- there is no
        # intermediate selected state to pass through.
        required = [
            (S.NO_MODEL, S.LOADING),
            (S.LOADING, S.READY), (S.LOADING, S.FAILED), (S.READY, S.BUSY),
            (S.BUSY, S.READY), (S.READY, S.UNLOADING), (S.UNLOADING, S.NO_MODEL),
            (S.READY, S.SWITCHING), (S.SWITCHING, S.READY), (S.SWITCHING, S.FAILED),
            (S.FAILED, S.LOADING), (S.FAILED, S.NO_MODEL),
        ]
        for source, target in required:
            self.assertIn(target, TRANSITIONS[source], f"{source.value}->{target.value}")

    def test_impossible_transitions_are_rejected_by_name(self) -> None:
        # NO_MODEL -> LOADING is now legal and has moved to `required`.
        forbidden = [
            (S.NO_MODEL, S.READY), (S.NO_MODEL, S.BUSY),
            (S.LOADING, S.BUSY), (S.READY, S.LOADING), (S.BUSY, S.UNLOADING),
            (S.BUSY, S.SWITCHING), (S.UNLOADING, S.READY),
        ]
        for source, target in forbidden:
            lifecycle = ModelLifecycle()
            lifecycle._state = source  # noqa: SLF001 - placing the machine, not testing it
            with self.assertRaises(StudioError, msg=f"{source.value}->{target.value}") as c:
                lifecycle.transition(target)
            self.assertEqual(MODEL_TRANSITION_REJECTED, c.exception.error.code)

    def test_nothing_leaves_shutdown(self) -> None:
        lifecycle = ModelLifecycle()
        lifecycle.transition(S.SHUTDOWN)
        for target in ModelLifecycleState:
            with self.assertRaises(StudioError):
                lifecycle.transition(target)

    def test_a_snapshot_is_scalar_and_carries_no_reference(self) -> None:
        lifecycle = ModelLifecycle()
        lifecycle.transition(S.LOADING, profile=profile())
        self.assertNotIn(_SECRET, json.dumps(lifecycle.snapshot()))

    def test_readiness_is_not_reduced_to_a_boolean(self) -> None:
        snapshot = ModelLifecycle().snapshot()
        for key in ("state", "accepting_jobs", "model_loaded", "profile_id"):
            self.assertIn(key, snapshot)

    def test_history_records_every_transition(self) -> None:
        lifecycle = ModelLifecycle()
        lifecycle.transition(S.LOADING, profile=profile())
        lifecycle.transition(S.READY)
        lifecycle.transition(S.BUSY)
        self.assertEqual(3, len(lifecycle.history()))
        self.assertEqual("busy", lifecycle.snapshot()["state"])

    def test_the_exported_table_matches_the_declared_graph(self) -> None:
        table = transition_table()
        self.assertEqual({s.value for s in ModelLifecycleState}, set(table))
        self.assertEqual([], table["shutdown"])

    def test_state_updates_are_serialized_under_concurrency(self) -> None:
        lifecycle = ModelLifecycle()
        accepted, rejected = [], []

        def attempt() -> None:
            try:
                lifecycle.transition(S.LOADING)
                accepted.append(1)
            except StudioError:
                rejected.append(1)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len(accepted))
        self.assertEqual(7, len(rejected))


# ------------------------------------------------------- warm session manager


class ResidencyTests(unittest.TestCase):
    """Making a model resident is ONE act, driven by a job that names its
    selection. The two-step `select` -> `load` separation this class used to
    describe is gone from the product, so the tests whose subject *was* that
    separation are gone with it. Everything that was really about cold-state
    safety, readiness, reuse, failure and concurrency is kept and re-expressed
    against `ensure_loaded`."""

    def test_studio_starts_with_no_model(self) -> None:
        self.assertIs(S.NO_MODEL, manager().state)

    def test_nothing_autoloads_merely_because_a_profile_exists(self) -> None:
        warm = manager()
        self.assertIsNone(warm.session)
        self.assertEqual(0, warm.counters["loads"])

    def test_nothing_opens_until_a_job_asks(self) -> None:
        """What `select` used to prove -- configuration is not residency --
        now has to be proven at the only boundary that remains. Reading state
        and holding a configured repository must still open nothing; the ask
        is what opens the file."""

        loader = Loader()
        warm = manager(loader=loader)
        warm.describe()
        warm.describe()
        self.assertEqual([], loader.loaded)
        self.assertEqual("no_model", warm.describe()["state"])
        warm.ensure_loaded(profile("a"))
        self.assertEqual(["a"], loader.loaded)

    def test_making_an_unidentifiable_selection_resident_is_refused(self) -> None:
        """`load()` with nothing selected raised MODEL_NOT_SELECTED. With one
        act the same refusal guards the same mistake: something asked for a
        model without saying which. It must be named, not defaulted."""

        loader = Loader()
        warm = manager(loader=loader)
        with self.assertRaises(StudioError) as caught:
            warm.ensure_loaded(object())
        self.assertEqual(MODEL_NOT_SELECTED, caught.exception.error.code)
        self.assertEqual([], loader.loaded)
        self.assertIs(S.NO_MODEL, warm.state)

    def test_the_ask_makes_the_session_ready(self) -> None:
        warm, loader = ready_manager()
        self.assertIs(S.READY, warm.state)
        self.assertEqual(["a"], loader.loaded)
        self.assertIsNotNone(warm.session)

    def test_asking_again_for_the_same_selection_reuses_the_warm_session(self) -> None:
        """This replaces the second-load-is-refused-as-busy test. A second ask
        is no longer a mistake to refuse -- it is the ordinary second
        generation, and the contract is that it reloads nothing. Counting
        reuses separately is what stops a silent reload from passing here."""

        warm, loader = ready_manager()
        first = warm.session
        warm.ensure_loaded(profile("a"))
        self.assertEqual(["a"], loader.loaded)
        self.assertEqual(1, warm.counters["loads"])
        self.assertEqual(1, warm.counters["reuses"])
        self.assertIs(first, warm.session)
        self.assertIs(S.READY, warm.state)

    def test_a_failed_load_lands_in_failed_with_a_scalar_error(self) -> None:
        loader = Loader(fail_on={"a"})
        warm = manager(loader=loader)
        with self.assertRaises(StudioError) as caught:
            warm.ensure_loaded(profile("a"))
        self.assertEqual(MODEL_LOAD_FAILED, caught.exception.error.code)
        self.assertIs(S.FAILED, warm.state)
        self.assertNotIn("Traceback", caught.exception.error.message)

    def test_a_failed_load_is_recovered_by_asking_for_a_working_selection(self) -> None:
        loader = Loader(fail_on={"a"})
        warm = manager(loader=loader)
        with self.assertRaises(StudioError):
            warm.ensure_loaded(profile("a"))
        self.assertIs(S.FAILED, warm.state)
        warm.ensure_loaded(profile("b"))
        self.assertIs(S.READY, warm.state)
        self.assertEqual(["b"], loader.loaded)

    def test_a_failed_load_can_be_cleared_to_no_model(self) -> None:
        loader = Loader(fail_on={"a"})
        warm = manager(loader=loader)
        with self.assertRaises(StudioError):
            warm.ensure_loaded(profile("a"))
        warm.unload()
        self.assertIs(S.NO_MODEL, warm.state)

    def test_a_loader_returning_nothing_is_a_load_failure(self) -> None:
        warm = WarmSessionManager(
            profiles=ModelProfileRepository([profile("a")]), loader=lambda p: None
        )
        with self.assertRaises(StudioError) as caught:
            warm.ensure_loaded(profile("a"))
        self.assertEqual(MODEL_LOAD_FAILED, caught.exception.error.code)

    def test_concurrent_asks_produce_exactly_one_session(self) -> None:
        loader = Loader()
        warm = manager(loader=loader)
        errors: list[str] = []

        def attempt() -> None:
            try:
                warm.ensure_loaded(profile("a"))
            except StudioError as exc:
                errors.append(exc.error.code)

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len(loader.loaded))
        self.assertEqual(1, warm.counters["loads"])
        self.assertTrue(set(errors) <= {MODEL_ALREADY_LOADING, MODEL_BUSY})


class JobLeaseTests(unittest.TestCase):
    def test_two_jobs_reuse_one_session(self) -> None:
        warm, loader = ready_manager()
        first = warm.session
        warm.lease("j1")
        warm.release("j1")
        warm.lease("j2")
        self.assertIs(first, warm.session)
        warm.release("j2")
        self.assertEqual(1, len(loader.loaded))
        self.assertEqual([], loader.closed)

    def test_a_job_moves_the_session_to_busy_and_back(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        self.assertIs(S.BUSY, warm.state)
        warm.release("j1")
        self.assertIs(S.READY, warm.state)

    def test_a_second_job_queues_rather_than_starting(self) -> None:
        warm, _loader = ready_manager()
        self.assertTrue(warm.lease("j1"))
        self.assertFalse(warm.lease("j2"))
        self.assertEqual(1, warm.describe()["queued_jobs"])

    def test_the_queue_is_fifo(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        warm.lease("j2")
        warm.lease("j3")
        warm.release("j1")
        self.assertEqual("j2", warm.describe()["active_job"])
        warm.release("j2")
        self.assertEqual("j3", warm.describe()["active_job"])

    def test_a_job_before_load_is_refused_not_dropped(self) -> None:
        warm = manager()
        with self.assertRaises(StudioError) as caught:
            warm.lease("j1")
        self.assertEqual(MODEL_NOT_READY, caught.exception.error.code)

    def test_releasing_an_unknown_job_is_harmless(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        warm.release("other")
        self.assertEqual("j1", warm.describe()["active_job"])

    def test_a_queued_job_can_be_cancelled_before_it_starts(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        warm.lease("j2")
        warm.release("j2")
        self.assertEqual(0, warm.describe()["queued_jobs"])
        warm.release("j1")
        self.assertIsNone(warm.describe()["active_job"])

    def test_a_duplicate_job_id_is_refused(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        with self.assertRaises(StudioError):
            warm.lease("j1")

    def test_per_job_release_never_closes_the_session(self) -> None:
        warm, loader = ready_manager()
        for index in range(5):
            warm.lease(f"j{index}")
            warm.release(f"j{index}")
        self.assertEqual([], loader.closed)
        self.assertIs(S.READY, warm.state)


class UnloadTests(unittest.TestCase):
    def test_unload_closes_the_session_and_returns_to_no_model(self) -> None:
        warm, loader = ready_manager()
        warm.unload()
        self.assertIs(S.NO_MODEL, warm.state)
        self.assertEqual(["a"], loader.closed)
        self.assertIsNone(warm.session)

    def test_unload_with_no_model_is_a_no_op(self) -> None:
        warm = manager()
        warm.unload()
        self.assertIs(S.NO_MODEL, warm.state)

    def test_a_double_unload_is_idempotent(self) -> None:
        warm, loader = ready_manager()
        warm.unload()
        warm.unload()
        self.assertEqual(["a"], loader.closed)

    def test_unload_during_a_job_is_deferred_not_refused(self) -> None:
        warm, loader = ready_manager()
        warm.lease("j1")
        described = warm.unload()
        self.assertEqual("unload", described["pending_action"])
        self.assertEqual([], loader.closed)
        warm.release("j1")
        self.assertIs(S.NO_MODEL, warm.state)
        self.assertEqual(["a"], loader.closed)

    def test_a_job_is_refused_while_an_unload_is_pending(self) -> None:
        warm, _loader = ready_manager()
        warm.lease("j1")
        warm.unload()
        with self.assertRaises(StudioError) as caught:
            warm.lease("j2")
        self.assertEqual(MODEL_BUSY, caught.exception.error.code)


class ChangedSelectionTests(unittest.TestCase):
    """A switch is no longer an owner action: it is what asking for a
    different selection *means*. The teardown ordering, the single-session
    invariant and the deferral under a lease are unchanged -- only who
    triggers them is."""

    def test_a_changed_selection_closes_a_before_loading_b(self) -> None:
        warm, loader = ready_manager()
        warm.ensure_loaded(profile("b"))
        self.assertEqual(["a", "b"], loader.loaded)
        self.assertEqual(["a"], loader.closed)
        self.assertEqual(0, loader.overlap)
        self.assertIs(S.READY, warm.state)

    def test_no_two_sessions_are_ever_live_at_once(self) -> None:
        loader = Loader()
        warm, _ = ready_manager(loader)
        for target in ("b", "a", "b"):
            warm.ensure_loaded(profile(target))
        self.assertEqual(0, loader.overlap)
        self.assertEqual(1, len([s for s in loader.live if not s.closed]))

    def test_a_failed_switch_does_not_resurrect_the_previous_model(self) -> None:
        loader = Loader(fail_on={"b"})
        warm, _ = ready_manager(loader)
        with self.assertRaises(StudioError) as caught:
            warm.ensure_loaded(profile("b"))
        self.assertEqual(MODEL_SWITCH_FAILED, caught.exception.error.code)
        self.assertIs(S.FAILED, warm.state)
        self.assertIsNone(warm.session)
        self.assertEqual(["a"], loader.closed)

    def test_a_failed_switch_leaves_nothing_resident(self) -> None:
        loader = Loader(fail_on={"b"})
        warm, _ = ready_manager(loader)
        with self.assertRaises(StudioError):
            warm.ensure_loaded(profile("b"))
        self.assertIsNone(warm.describe()["profile_id"])

    def test_a_changed_selection_during_a_job_is_deferred(self) -> None:
        loader = Loader()
        warm, _ = ready_manager(loader)
        warm.lease("j1")
        described = warm.ensure_loaded(profile("b"))
        self.assertEqual("switch", described["pending_action"])
        self.assertEqual(["a"], loader.loaded)
        warm.release("j1")
        self.assertEqual(["a", "b"], loader.loaded)
        self.assertIs(S.READY, warm.state)

    def test_asking_with_nothing_resident_simply_loads(self) -> None:
        loader = Loader()
        warm = manager(loader=loader)
        warm.ensure_loaded(profile("b"))
        self.assertEqual(["b"], loader.loaded)
        self.assertEqual([], loader.closed)
        self.assertIs(S.READY, warm.state)

    def test_an_unidentifiable_selection_never_disturbs_the_resident_one(self) -> None:
        """The repository lookup that `switch(profile_id)` performed is gone --
        a job carries its selection. What must survive is that an unusable ask
        is refused by name and the warm session it could not replace is left
        exactly as it was, rather than torn down for a switch that never
        happened."""

        warm, loader = ready_manager()
        with self.assertRaises(StudioError) as caught:
            warm.ensure_loaded(object())
        self.assertEqual(MODEL_NOT_SELECTED, caught.exception.error.code)
        self.assertEqual(["a"], loader.loaded)
        self.assertIs(S.READY, warm.state)


class ShutdownTests(unittest.TestCase):
    def test_shutdown_with_no_model_is_clean(self) -> None:
        warm = manager()
        warm.shutdown()
        self.assertIs(S.SHUTDOWN, warm.state)

    def test_shutdown_closes_a_ready_session(self) -> None:
        warm, loader = ready_manager()
        warm.shutdown()
        self.assertEqual(["a"], loader.closed)
        self.assertIs(S.SHUTDOWN, warm.state)

    def test_shutdown_is_idempotent(self) -> None:
        warm, loader = ready_manager()
        warm.shutdown()
        warm.shutdown()
        self.assertEqual(["a"], loader.closed)

    def test_shutdown_during_a_job_requests_cancellation_and_closes(self) -> None:
        cancelled: list[int] = []
        loader = Loader()
        warm = manager(loader=loader, cancel_active=lambda: cancelled.append(1) or True)
        warm.ensure_loaded(profile("a"))
        warm.lease("j1")
        warm.shutdown(wait_seconds=0.05)
        self.assertEqual([1], cancelled)
        self.assertEqual(["a"], loader.closed)
        self.assertIs(S.SHUTDOWN, warm.state)

    def test_shutdown_does_not_wait_forever(self) -> None:
        import time as _time

        warm, _loader = ready_manager()
        warm.lease("j1")
        started = _time.monotonic()
        warm.shutdown(wait_seconds=0.05)
        self.assertLess(_time.monotonic() - started, 3.0)

    def test_a_refusing_cancel_hook_does_not_block_shutdown(self) -> None:
        def refuse() -> bool:
            raise RuntimeError("cancel refused")

        loader = Loader()
        warm = manager(loader=loader, cancel_active=refuse)
        warm.ensure_loaded(profile("a"))
        warm.lease("j1")
        warm.shutdown(wait_seconds=0.05)
        self.assertIs(S.SHUTDOWN, warm.state)

    def test_every_operation_after_shutdown_is_refused(self) -> None:
        warm, _loader = ready_manager()
        warm.shutdown()
        for call in (lambda: warm.ensure_loaded(profile("a")),
                     lambda: warm.ensure_loaded(profile("b")),
                     warm.unload, lambda: warm.lease("j9")):
            with self.assertRaises(StudioError) as caught:
                call()
            self.assertEqual(MODEL_SESSION_SHUTDOWN, caught.exception.error.code)

    def test_a_closer_that_raises_does_not_prevent_shutdown(self) -> None:
        def explode(_session) -> None:
            raise RuntimeError("close failed")

        warm = WarmSessionManager(
            profiles=ModelProfileRepository([profile("a")]),
            loader=lambda p: SyntheticSession(p.profile_id),
            closer=explode,
        )
        warm.ensure_loaded(profile("a"))
        warm.shutdown()
        self.assertIs(S.SHUTDOWN, warm.state)


# ------------------------------------------------------------------ settings


class SettingsTests(unittest.TestCase):
    def test_autoload_defaults_off(self) -> None:
        self.assertFalse(StudioSettings().autoload)

    def test_the_public_view_never_carries_the_result_root(self) -> None:
        settings = StudioSettings(result_root=Path("Z:/private/results"))
        described = settings.describe()
        self.assertTrue(described["result_root_configured"])
        self.assertNotIn("Z:", json.dumps(described))

    def test_storage_keeps_the_result_root_but_describe_does_not(self) -> None:
        settings = StudioSettings(result_root=Path("Z:/private/results"))
        self.assertIn("result_root", settings.to_storage())
        self.assertNotIn("result_root", settings.describe())

    def test_an_unknown_backend_kind_is_refused(self) -> None:
        with self.assertRaises(StudioError):
            StudioSettings(backend_kind="something")

    def test_a_round_trip_preserves_the_decision(self) -> None:
        original = StudioSettings(selected_profile_id="a", autoload=True)
        restored = StudioSettings.from_storage(original.to_storage())
        self.assertEqual("a", restored.selected_profile_id)
        self.assertTrue(restored.autoload)

    def test_a_newer_settings_version_is_refused(self) -> None:
        with self.assertRaises(StudioError):
            StudioSettings.from_storage({"version": 99})

    def test_a_missing_file_is_a_first_run_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = SettingsService(Path(temporary) / "settings.json")
            self.assertFalse(service.load().autoload)

    def test_a_malformed_file_is_an_error_not_a_silent_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(StudioError):
                SettingsService(path).load()

    def test_saving_and_reloading_keeps_the_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            service = SettingsService(path)
            service.save(StudioSettings(selected_profile_id="b"))
            self.assertEqual("b", SettingsService(path).load().selected_profile_id)

    def test_settings_without_a_path_still_work_in_memory(self) -> None:
        service = SettingsService(None)
        service.save(StudioSettings(selected_profile_id="a"))
        self.assertEqual("a", service.current.selected_profile_id)
        self.assertFalse(service.describe()["persisted"])


# ------------------------------------------------------------- service layer


def service(loader: Loader | None = None, settings: SettingsService | None = None):
    loader = loader or Loader()
    repository = ModelProfileRepository([profile("a"), profile("b")])
    warm = WarmSessionManager(
        profiles=repository, loader=loader.load, closer=loader.close
    )
    return (
        ModelLifecycleService(profiles=repository, manager=warm, settings=settings),
        warm,
        loader,
    )


class ServiceContractTests(unittest.TestCase):
    def test_the_resident_selection_is_readable_once_a_job_asks(self) -> None:
        """Through `state()`, which is what /api/model/state answers.

        `list_profiles` and `selected_profile` used to be asserted here. Both
        are retired: there is no owner-facing profile, so the question is not
        "which profile is selected" but "what is RESIDENT".
        """

        api, _warm, _loader = service()
        api.ensure_loaded(profile("a"))
        self.assertEqual("a", api.state()["profile_id"])

    def test_readiness_is_structured_not_a_boolean(self) -> None:
        api, _warm, _loader = service()
        readiness = api.readiness()
        for key in ("state", "model_loaded", "accepting_jobs", "session_active",
                    "queued_jobs", "pending_action", "profiles_configured"):
            self.assertIn(key, readiness)

    def test_the_full_service_flow_reports_each_state(self) -> None:
        api, _warm, _loader = service()
        self.assertEqual("no_model", api.state()["state"])
        # `ensure_loaded` is asserted through the SERVICE, not the manager:
        # the application gates on this object, and a service that could not
        # answer it would leave auto-load proven in units and absent in the
        # product.
        api.ensure_loaded(profile("a"))
        self.assertEqual("ready", api.state()["state"])
        api.unload()
        self.assertEqual("no_model", api.state()["state"])

    def test_no_service_response_carries_a_payload_reference(self) -> None:
        api, _warm, _loader = service()
        api.ensure_loaded(profile("a"))
        for payload in (api.state(), api.readiness()):
            self.assertNotIn(_SECRET, json.dumps(payload))

    def test_shutdown_through_the_service_closes_the_session(self) -> None:
        api, _warm, loader = service()
        api.ensure_loaded(profile("a"))
        api.shutdown()
        self.assertEqual(["a"], loader.closed)


# -------------------------------------------------------- composition wiring


class CompositionIntegrationTests(unittest.TestCase):
    def build(self, **kwargs):
        return build_standalone(backend_kind="mock", **kwargs)

    def test_the_composition_root_owns_the_lifecycle_services(self) -> None:
        with self.build() as composition:
            self.assertIsNotNone(composition.profiles)
            self.assertIsNotNone(composition.settings)
            self.assertIsNotNone(composition.model_lifecycle)

    def test_construction_loads_no_model(self) -> None:
        loader = Loader()
        with self.build(
            profiles=[profile("a")], session_loader=loader.load,
            session_closer=loader.close,
        ) as composition:
            self.assertEqual([], loader.loaded)
            self.assertEqual("no_model", composition.model_lifecycle.state()["state"])

    def test_profiles_reach_the_service(self) -> None:
        with self.build(profiles=[profile("a"), profile("b")]) as composition:
            self.assertEqual(
                2, composition.model_lifecycle.readiness()["profiles_configured"]
            )

    def test_a_job_ask_loads_through_the_composition(self) -> None:
        loader = Loader()
        with self.build(
            profiles=[profile("a")], session_loader=loader.load,
            session_closer=loader.close,
        ) as composition:
            composition.model_lifecycle.ensure_loaded(profile("a"))
            self.assertEqual(["a"], loader.loaded)

    def test_composition_shutdown_closes_the_warm_session(self) -> None:
        loader = Loader()
        composition = self.build(
            profiles=[profile("a")], session_loader=loader.load,
            session_closer=loader.close,
        )
        composition.model_lifecycle.ensure_loaded(profile("a"))
        composition.shutdown()
        self.assertEqual(["a"], loader.closed)

    def test_a_composition_with_no_profiles_still_builds(self) -> None:
        with self.build() as composition:
            self.assertEqual(
                0, composition.model_lifecycle.readiness()["profiles_configured"]
            )

    def test_settings_are_loaded_without_opening_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps({"selected_profile_id": "a"}), encoding="utf-8")
            with self.build(profiles=[profile("a")], settings_path=path) as composition:
                described = composition.settings.describe()
                self.assertEqual("a", described["selected_profile_id"])
                self.assertTrue(described["loaded"])


# ------------------------------------------------------------- import safety


PROBE = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from forge_studio.composition import build_standalone\n"
    "from forge_studio.model_profiles import ModelProfile\n"
    "p = ModelProfile(profile_id='a', display_name='A', family='anima',\n"
    "                 payload_references={'checkpoint': 'x', 'text_encoder': 'y',\n"
    "                                     'vae': 'z'})\n"
    "c = build_standalone(backend_kind='mock', profiles=[p])\n"
    "state = c.model_lifecycle.state()['state']\n"
    "c.shutdown()\n"
    "names = set(sys.modules)\n"
    "print(json.dumps({\n"
    "  'state': state,\n"
    "  'torch': int(any(n == 'torch' or n.startswith('torch.') for n in names)),\n"
    "  'cuda': int(any('cuda' in n for n in names)),\n"
    "  'forge_backend': int(any(n == 'backend' or n.startswith('backend.') for n in names)),\n"
    "  'neo': int(any(n.split('.')[0] in ('modules', 'modules_forge', 'webui') for n in names)),\n"
    "  'gradio': int(any(n.split('.')[0] in ('gradio', 'gradio_client') for n in names)),\n"
    "  'socketserver': int('socketserver' in names),\n"
    "}))\n"
)


class ImportSafetyTests(unittest.TestCase):
    def probe(self) -> dict:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", "-S", "-B", "-c", PROBE, str(APP_ROOT)],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_building_pulls_in_nothing_heavy(self) -> None:
        report = self.probe()
        self.assertEqual("no_model", report["state"])
        for key in ("torch", "cuda", "forge_backend", "neo", "gradio", "socketserver"):
            self.assertEqual(0, report[key], f"{key} was imported")

    def test_the_lifecycle_modules_import_no_backend_at_module_scope(self) -> None:
        import ast

        forbidden = {"torch", "backend", "modules", "modules_forge", "webui",
                     "gradio", "forge_headless"}
        for name in ("model_profiles", "model_lifecycle", "model_service", "settings"):
            path = APP_ROOT / "forge_studio" / f"{name}.py"
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden, name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden, name)

    def test_no_lifecycle_module_embeds_a_private_path(self) -> None:
        for name in ("model_profiles", "model_lifecycle", "model_service", "settings"):
            source = (APP_ROOT / "forge_studio" / f"{name}.py").read_text(encoding="utf-8")
            for leak in ("Private-Local", ".safetensors", "C:\\Users", "/Users/"):
                self.assertNotIn(leak, source, name)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(EXPECTED_ALPHA_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("STATIC_IMPORT_SCOPE", __doc__ or "")
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
