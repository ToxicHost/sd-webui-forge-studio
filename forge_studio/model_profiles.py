"""Studio-owned model profiles: what a user selects, before anything loads.

A profile is *configuration*, not a catalogue entry. Studio does not scan
directories, does not stat a payload, and does not open a file to build one --
selecting a profile is a pure decision about identity that costs nothing and
cannot fail on I/O.

The split that matters is between the two views:

* `ModelProfile` holds the private payload references. It never leaves the
  process boundary.
* `describe()` is the only thing a response may carry: scalars, an id, a display
  name, a family, and booleans saying which roles are configured. **No path, no
  filename, no directory, no stem.**

That split exists because every prior milestone found the same failure mode --
a path reaching a place it should not -- and the cheapest guarantee is that the
public projection simply has nowhere to put one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .contracts import StructuredError, StudioError

#: Every role a profile may name.
KNOWN_ROLES = ("checkpoint", "text_encoder", "vae")

#: Of those, the ones it MUST name. A checkpoint that carries its own text
#: encoder and VAE -- SDXL, SD 1.5 -- names exactly one, and a profile that
#: refused it would be refusing a model Forge loads. Naming an unrecognised
#: role is still a configuration error.
REQUIRED_ROLES = ("checkpoint",)

MODEL_PROFILE_INVALID = "MODEL_PROFILE_INVALID"
MODEL_PROFILE_DUPLICATE = "MODEL_PROFILE_DUPLICATE"

#: Scalar metadata a profile may carry. Anything else is refused rather than
#: silently serialized, because "optional metadata" is exactly where an object
#: repr or a path would otherwise slip in.
_SCALAR_TYPES = (bool, int, float, str)


def _invalid(message: str, field_name: str | None = None) -> StudioError:
    return StudioError(
        StructuredError(
            code=MODEL_PROFILE_INVALID, message=message, field=field_name
        )
    )


@dataclass(frozen=True)
class ModelProfile:
    """One configured, selectable model. Immutable and comparable by value.

    `payload_references` maps each required role to a private reference string.
    It is deliberately not a `Path`: this module never touches the filesystem,
    and typing it as a path would invite someone to.
    """

    profile_id: str
    display_name: str
    family: str
    payload_references: Mapping[str, str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("profile_id", self.profile_id),
            ("display_name", self.display_name),
            ("family", self.family),
        ):
            if not isinstance(value, str) or not value.strip():
                raise _invalid(f"A profile needs a non-empty {name}.", name)
        if len(self.profile_id) > 120:
            raise _invalid("That profile id is too long.", "profile_id")

        if not isinstance(self.payload_references, Mapping):
            raise _invalid("Payload references must be a mapping.", "payload_references")
        missing = [role for role in REQUIRED_ROLES if not self.payload_references.get(role)]
        if missing:
            raise _invalid(
                f"That profile is missing a required role: {missing[0]}.", missing[0]
            )
        unknown = [role for role in self.payload_references if role not in KNOWN_ROLES]
        if unknown:
            raise _invalid(f"Unknown payload role: {sorted(unknown)[0]}.", "payload_references")
        for role in KNOWN_ROLES:
            if role not in self.payload_references:
                continue  # the checkpoint carries this one
            reference = self.payload_references[role]
            if not isinstance(reference, str) or not reference.strip():
                raise _invalid(f"The {role} reference must be a non-empty string.", role)

        if not isinstance(self.metadata, Mapping):
            raise _invalid("Profile metadata must be a mapping.", "metadata")
        for key, value in self.metadata.items():
            if not isinstance(key, str):
                raise _invalid("Profile metadata keys must be strings.", "metadata")
            if not isinstance(value, _SCALAR_TYPES) and value is not None:
                raise _invalid(
                    f"Profile metadata must be scalar; {key!r} is not.", "metadata"
                )

        # Freeze the mappings so a caller cannot mutate a profile after it has
        # been validated. `frozen=True` protects the attribute, not the dict.
        object.__setattr__(self, "payload_references", dict(self.payload_references))
        object.__setattr__(self, "metadata", dict(self.metadata))

    # -- public projection -------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """The only view a response may carry. Never a path or a filename."""

        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "family": self.family,
            "roles_configured": sorted(
                role for role in KNOWN_ROLES if self.payload_references.get(role)
            ),
            "complete": all(self.payload_references.get(r) for r in REQUIRED_ROLES),
            "metadata": dict(self.metadata),
        }

    def reference_for(self, role: str) -> str:
        """Private accessor for a loader. Never used to build a response."""

        if role not in KNOWN_ROLES:
            raise _invalid(f"Unknown payload role: {role}.", "role")
        # A KeyError for a role the checkpoint carries itself: the session
        # loader treats that as "not supplied" rather than as a failure.
        return self.payload_references[role]

    # `same_selection` is retired. It answered "would selecting this be a
    # no-op?" for a `select` step that no longer exists. The question it asked
    # is still asked -- but of the SELECTION, not of a profile: `ensure_loaded`
    # compares the requested triplet against what is resident, which is where
    # load-vs-reuse-vs-switch is actually decided.


def _looks_like_profile(candidate: object) -> bool:
    """Whether an object satisfies the profile shape, by surface not identity.

    `isinstance(candidate, ModelProfile)` is the obvious check and it is wrong
    here, for the reason `composition._implements_backend_port` already
    documents: `tests/studio_alpha/test_import_boundaries.py` purges
    `forge_studio` and every submodule from `sys.modules` to prove a fresh
    import stays clean, so a later deferred import produces a **second**
    `ModelProfile` class object. A profile built from one fails an identity
    check against the other, under the canonical runner only.
    """

    return all(
        hasattr(candidate, name)
        for name in ("profile_id", "display_name", "family",
                     "payload_references", "describe", "reference_for")
    )


def looks_like_profile_repository(candidate: object) -> bool:
    """Same reasoning, one level up: a repository by surface, not identity.

    The surface it names must be a surface that still EXISTS. This checked
    `("get", "list", "describe", "ids")` -- three of which were the retired
    lookup methods, so removing them silently turned a real repository into
    something that fails its own shape check, and `composition` would have
    stopped accepting one.

    `describe` and `__len__` are what survive, and the pair still discriminates
    the two things this is asked to tell apart: a LIST of profiles has `__len__`
    but no `describe`, and a bare `ModelProfile` has `describe` but no
    `__len__`.
    """

    return all(hasattr(candidate, name) for name in ("describe", "__len__"))


class ModelProfileRepository:
    """The configured profiles, in declaration order. Reads nothing from disk.

    Construction validates every profile and rejects duplicate ids, so an
    invalid configuration fails at assembly rather than at first load.
    """

    def __init__(self, profiles: Iterable[ModelProfile] = ()) -> None:
        ordered: list[ModelProfile] = []
        seen: set[str] = set()
        for profile in profiles:
            if not _looks_like_profile(profile):
                raise _invalid("A profile repository holds ModelProfile values.")
            if profile.profile_id in seen:
                raise StudioError(
                    StructuredError(
                        code=MODEL_PROFILE_DUPLICATE,
                        message="Two profiles share the same id.",
                        field="profile_id",
                    )
                )
            seen.add(profile.profile_id)
            ordered.append(profile)
        # The configured profiles. This tuple is never mutated.
        #
        # A separately-held "runtime profile" used to sit beside it, holding
        # what the owner had just picked from a folder so that `select` and
        # `load` had a profile id to name. Both of those are retired, and the
        # only thing that ever installed one -- `select_from_catalogue` -- is
        # quarantined with them. A selection is now three ids on a generation
        # request, resolved at the loader boundary, and never registered
        # anywhere.
        self._profiles = tuple(ordered)

    def _all(self) -> tuple[ModelProfile, ...]:
        return self._profiles

    def __len__(self) -> int:
        return len(self._all())

    # `ids`, `list`, `get` and `contains` are retired -- the lookup surface of a
    # repository nothing looks anything up in. They existed to answer
    # `/api/profiles` and to resolve the `profile_id` a `select` call named;
    # both are gone, and no production caller of any of the four survived them.
    #
    # `describe()` below now has no production caller either, since
    # `ModelLifecycleService.list_profiles` was its only one. It is kept, with
    # `__len__`, because `readiness()` still reports `profiles_configured` and
    # because this module as a whole awaits an owner decision: the execution
    # book requires approval before a profile module is hard-deleted, so
    # thinning it to a husk first would be the same removal by instalments.

    def describe(self) -> list[dict[str, Any]]:
        """The public list. Redacted by construction."""

        return [profile.describe() for profile in self._all()]

__all__ = (
    "MODEL_PROFILE_DUPLICATE",
    "MODEL_PROFILE_INVALID",
    "KNOWN_ROLES",
    "REQUIRED_ROLES",
    "ModelProfile",
    "ModelProfileRepository",
    "looks_like_profile_repository",
)
