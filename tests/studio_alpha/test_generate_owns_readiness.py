"""Generate is legal from NO_MODEL. The other half of the P0.2 exit gate.

`StudioApplication.submit_generation` used to lease first and refuse by name
when nothing was warm, on the stated reasoning that "a generation must never be
what opens a model". Forge Neo does the reverse: `forge_model_reload()` runs at
generation time. Requiring an explicit load first IS the Load button, whatever
the control is called.

The guarantee underneath that old refusal survives and is pinned below: a
generation still never runs on a model nobody asked for, because the job names
the selection it wants rather than inheriting whatever happened to be resident.

Ordering is the sharp edge. Readiness is reconciled BEFORE the lease is taken,
because `ensure_loaded` may switch, and switching under a held lease would pull
the session out from under the job holding it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.application import StudioApplication  # noqa: E402


class _Backend:
    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_generation(self, request):
        self.submitted.append(request)
        return {"job_id": "backend-1"}


class _Lifecycle:
    """Records the ORDER of readiness and lease, which is the contract."""

    gates_generation = True

    def __init__(self) -> None:
        self.events: list[str] = []
        self.ensured: list[object] = []
        self.refuse_lease = False

    def ensure_loaded(self, resolved):
        self.events.append("ensure")
        self.ensured.append(resolved)
        return {"state": "ready"}

    def begin_job_as(self, token):
        self.events.append("lease")
        if self.refuse_lease:
            raise RuntimeError("MODEL_NOT_READY")
        return token or "job-1"

    def begin_job(self):
        return self.begin_job_as(None)

    def end_job(self, token):
        self.events.append("release")


class _LifecycleWithoutEnsure(_Lifecycle):
    """A pre-P0.2 lifecycle: leases, but cannot reconcile a selection."""

    ensure_loaded = None


class _Request:
    def __init__(self, selection=None) -> None:
        self.model_id = "runtime"
        self.positive_prompt = "p"
        self.negative_prompt = ""
        self.seed = 1
        self.steps = 4
        self.cfg_scale = 5.0
        self.width = 768
        self.height = 768
        self.selection = selection


def application(*, lifecycle=None, resolver=None, backend=None) -> StudioApplication:
    app = StudioApplication(
        backend or _Backend(),
        model_lifecycle=lifecycle,
        selection_resolver=resolver,
    )
    # Request validation is not what this file is about.
    app._validate_request = lambda request: None  # noqa: SLF001
    return app


class GenerateOwnsReadinessTests(unittest.TestCase):
    def test_readiness_is_reconciled_before_the_lease_is_taken(self) -> None:
        lifecycle = _Lifecycle()
        resolved = object()
        app = application(lifecycle=lifecycle, resolver=lambda _r: resolved)
        app.submit_generation(_Request())
        self.assertEqual(["ensure", "lease", "release"], lifecycle.events)

    def test_the_request_names_the_model_it_wants(self) -> None:
        # The surviving guarantee: not "whatever is resident", but "this".
        lifecycle = _Lifecycle()
        resolved = object()
        app = application(lifecycle=lifecycle, resolver=lambda _r: resolved)
        app.submit_generation(_Request())
        self.assertEqual([resolved], lifecycle.ensured)

    def test_generation_reaches_the_backend_from_no_model(self) -> None:
        lifecycle = _Lifecycle()
        backend = _Backend()
        app = application(
            lifecycle=lifecycle, resolver=lambda _r: object(), backend=backend
        )
        app.submit_generation(_Request())
        self.assertEqual(1, len(backend.submitted))

    def test_the_lease_is_still_released_when_the_backend_raises(self) -> None:
        class Exploding(_Backend):
            def submit_generation(self, request):
                raise RuntimeError("boom")

        lifecycle = _Lifecycle()
        app = application(
            lifecycle=lifecycle, resolver=lambda _r: object(), backend=Exploding()
        )
        with self.assertRaises(RuntimeError):
            app.submit_generation(_Request())
        self.assertEqual(["ensure", "lease", "release"], lifecycle.events)

    def test_a_failed_ensure_never_takes_a_lease(self) -> None:
        # If the model cannot be made ready there is nothing to lease, and
        # leasing anyway would hold a session for a job that cannot run.
        lifecycle = _Lifecycle()

        def resolver(_request):
            raise RuntimeError("selection unavailable")

        app = application(lifecycle=lifecycle, resolver=resolver)
        with self.assertRaises(RuntimeError):
            app.submit_generation(_Request())
        self.assertEqual([], lifecycle.events)


class HostsWithoutTheCapabilityAreUnchangedTests(unittest.TestCase):
    """Every guard is an AND, so a host missing any part behaves as before."""

    def test_no_resolver_means_lease_only(self) -> None:
        lifecycle = _Lifecycle()
        app = application(lifecycle=lifecycle, resolver=None)
        app.submit_generation(_Request())
        self.assertEqual(["lease", "release"], lifecycle.events)

    def test_a_lifecycle_without_ensure_loaded_is_left_alone(self) -> None:
        lifecycle = _LifecycleWithoutEnsure()
        app = application(lifecycle=lifecycle, resolver=lambda _r: object())
        app.submit_generation(_Request())
        self.assertEqual(["lease", "release"], lifecycle.events)

    def test_a_request_carrying_no_selection_falls_through_to_the_lease(self) -> None:
        # Nothing named and nothing resident: the lease's refusal is still the
        # right answer, because there is nothing to reconcile.
        lifecycle = _Lifecycle()
        app = application(lifecycle=lifecycle, resolver=lambda _r: None)
        app.submit_generation(_Request())
        self.assertEqual(["lease", "release"], lifecycle.events)

    def test_a_host_with_no_lifecycle_at_all_still_generates(self) -> None:
        backend = _Backend()
        app = application(lifecycle=None, resolver=lambda _r: object(), backend=backend)
        app.submit_generation(_Request())
        self.assertEqual(1, len(backend.submitted))


class TheOldRefusalIsGoneTests(unittest.TestCase):
    def test_the_source_no_longer_claims_generation_cannot_open_a_model(self) -> None:
        source = (APP_ROOT / "forge_studio" / "application.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("A generation must never be what opens a model", source)
        self.assertIn("Generation OWNS model readiness", source)

    def test_ensure_precedes_the_lease_in_source_order(self) -> None:
        # Ordering is the contract, and a later edit that moves the call after
        # begin_job would switch a model under a held lease.
        source = (APP_ROOT / "forge_studio" / "application.py").read_text(
            encoding="utf-8"
        )
        body = source[source.index("def submit_generation") :][:2600]
        self.assertLess(
            body.index("_ensure_requested_model"), body.index("begin_job_as")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
