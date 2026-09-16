"""Per-load explicit access: a fresh controlled authorization each load.

This lives in `forge_headless` deliberately. `test_no_public_bypass_exists`
pins that nothing under `forge_studio/` -- the HTTP-reachable layer -- can
even name the authorization machinery, and that guard is right: the launcher
hands this factory in as an opaque `payload_opener`, and the studio layer
never learns what it is made of.

The factory is called by the loader's payload step with the SELECTED
profile's role references. Each call constructs a new
`ControlledLoadAuthorization` bound to exactly those references and the
owner-configured timeout/ceiling, so single-attempt-per-load holds and
load -> unload -> load works across one app session. Nothing an HTTP request
carries can reach any of these inputs: the paths come from the owner's local
configuration through the profile object, and the limits are fixed at
construction, before the server exists.

Import-safe: constructing the factory touches nothing.
"""

from __future__ import annotations

from typing import Any


class ExplicitLoadAccess:
    """Grant one fresh controlled authorization per explicit load."""

    def __init__(self, *, timeout_seconds: int, vram_ceiling_bytes: int) -> None:
        self._timeout_seconds = int(timeout_seconds)
        self._vram_ceiling_bytes = int(vram_ceiling_bytes)

    def describe(self) -> dict[str, Any]:
        """Scalars only; never a path."""

        return {
            "access": type(self).__name__,
            "timeout_seconds": self._timeout_seconds,
            "vram_ceiling_bytes": self._vram_ceiling_bytes,
            "fresh_authorization_per_load": True,
        }

    def __call__(self, *, profile: Any = None, references: Any = None,
                 roles: Any = None, **_ignored: Any) -> Any:
        from .live_bindings import ROLE_ORDER, ControlledPayloadOpener
        from .load_authorization import ControlledLoadAuthorization

        ordered = tuple(roles) if roles else ROLE_ORDER
        if not isinstance(references, dict):
            raise ValueError(
                "The selected profile carries no payload references."
            )
        # Only the roles that RESOLVED. A component the checkpoint bundles has
        # no path, so authorizing it would mean authorizing "".
        authorization = ControlledLoadAuthorization(
            {role: str(references[role]) for role in ordered
             if references.get(role)},
            timeout_seconds=self._timeout_seconds,
            vram_ceiling_bytes=self._vram_ceiling_bytes,
        )
        opener = ControlledPayloadOpener(authorization=authorization)
        return opener(profile=profile, references=references, roles=ordered)


__all__ = ("ExplicitLoadAccess",)
