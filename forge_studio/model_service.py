"""The service surface for the warm-session lifecycle.

One object the transport can call, returning scalar dictionaries only. It owns
no state of its own: the repository holds the configured profiles, the manager
holds the session, the settings service holds persisted preferences. This is
the seam that keeps the transport from reaching into any of them.

No response here describes a profile any more -- the two that did are retired
below. What survives reports the RESIDENT session through the manager's own
`describe()`, which is redacted by construction, so there is still no code path
here that could carry a payload path even if someone wanted one.
"""

from __future__ import annotations

from typing import Any

from .model_lifecycle import WarmSessionManager
from .model_profiles import ModelProfileRepository
from .settings import SettingsService

# `_persist_selection` is retired with `select`/`clear_selection`, its only two
# callers. `settings` is still accepted and held: P0.3e reintroduces a
# remembered selection as a UI preference, which is a different thing from a
# load instruction and must never become one.


class ModelLifecycleService:
    """Report, make resident, unload, shut down -- nothing else.

    There is no `select` and no `load`: a job names its selection and
    `ensure_loaded` reconciles it against what is resident. `unload` is the
    one optional owner action that remains.
    """

    def __init__(
        self,
        *,
        profiles: ModelProfileRepository,
        manager: WarmSessionManager,
        settings: SettingsService | None = None,
    ) -> None:
        self._profiles = profiles
        self._manager = manager
        self._settings = settings

    # -- reads -------------------------------------------------------------

    # `profiles`, `list_profiles` and `selected_profile` are gone. All three
    # described a model the way the RETIRED profile product did, and none had a
    # caller outside the tests that covered them.
    #
    # `profiles` existed so a catalogue selection could install its runtime
    # profile into the same repository `select` consulted -- but
    # `_select_catalogue_models` and `/api/profiles/select_catalogue` are
    # themselves gone (presentation.py records their removal), so it guarded a
    # drift between two references that can no longer both exist.
    #
    # `list_profiles` and `selected_profile` answered no route: `/api/profiles`
    # is retired, because there is no owner-facing profile. The three model ids
    # travel on the generation request instead, and what is RESIDENT is read
    # from `/api/model/state`.

    def state(self) -> dict[str, Any]:
        return self._manager.describe()

    def readiness(self) -> dict[str, Any]:
        described = self._manager.describe()
        return {
            "state": described["state"],
            "model_loaded": described["model_loaded"],
            "accepting_jobs": described["accepting_jobs"],
            "session_active": described["session_active"],
            "profile_id": described["profile_id"],
            "queued_jobs": described["queued_jobs"],
            "pending_action": described["pending_action"],
            "profiles_configured": len(self._profiles),
            # Readable without attempting a load, which is the point: a caller
            # can tell "select something first" from "configure access first"
            # from "loading" from "ready" before it commits to anything.
            "load_configuration_required": self.load_configuration_required,
        }

    @property
    def load_configuration_required(self) -> bool:
        """True when a load would refuse for want of controlled access.

        Bound loader defaults are not readiness. Reporting ready because the
        steps exist would move the refusal from selection time to payload time,
        which is the opposite of what an explicit load boundary is for.
        """

        return self._manager.load_configuration_required

    # -- writes ------------------------------------------------------------

    # select / clear_selection / load / switch are retired with the manager
    # methods they delegated to. Making a model resident is `ensure_loaded`,
    # driven by a job that names its selection; unload survives as the one
    # optional owner action.

    def unload(self) -> dict[str, Any]:
        return self._manager.unload()

    def shutdown(self, *, wait_seconds: float = 5.0) -> dict[str, Any]:
        return self._manager.shutdown(wait_seconds=wait_seconds)

    # -- job gating --------------------------------------------------------

    @property
    def gates_generation(self) -> bool:
        """Only a lifecycle that can load anything may refuse a job."""

        return bool(self._manager.manages_sessions)

    def ensure_loaded(self, selection: Any) -> dict[str, Any]:
        """Make the resident session match the selection this job asked for.

        Delegated to the manager, which owns the one resident session and
        therefore the one decision about load-vs-reuse-vs-switch. Exposed here
        because the application gates on this service, not on the manager: a
        service that could not answer `ensure_loaded` would make
        `_ensure_requested_model` fall through silently, and auto-load would be
        proven by unit tests while never running in the product.
        """

        return self._manager.ensure_loaded(selection)

    def begin_job(self) -> str:
        """Take the warm session for one job, or refuse by name.

        The token is issued here rather than taken from the backend, because
        the lease has to exist *before* the backend is asked to submit --
        there is no job id yet at that point, and inventing one from the
        request would collide across identical requests.
        """

        return self.begin_job_as(None)

    def begin_job_as(self, token: str | None) -> str:
        """Take the session under a caller-minted token, or mint one.

        The externally minted token is what lets ONE public job id exist
        before backend submission and stay stable through queued, running and
        terminal states: the coordinator mints it, the lifecycle queues under
        it, and queued cancellation names it -- the same id everywhere.
        """

        if token is None:
            token = self._manager.next_job_token()
        # `acquire`, not `lease`: the caller is about to submit to the backend,
        # so it must actually hold the session, not merely be recorded as
        # wanting it. Waiting here is what keeps one engine to one generation.
        self._manager.acquire(token)
        return token

    def mint_job_token(self) -> str:
        """A public job token, minted BEFORE any lease or backend work."""

        return self._manager.next_job_token()

    def cancel_queued_job(self, token: str) -> bool:
        """Cancel a job that is still waiting. It never reaches the backend."""

        return self._manager.cancel_queued(token)

    def queued_jobs(self) -> tuple[str, ...]:
        """Waiting job tokens, FIFO. Opaque and process-local."""

        return self._manager.queued_tokens()

    def end_job(self, token: str) -> None:
        """Give the session back. It stays warm."""

        self._manager.release(token)

    @property
    def session(self) -> Any:
        """The live warm session, or None."""

        return self._manager.session

__all__ = ("ModelLifecycleService",)
