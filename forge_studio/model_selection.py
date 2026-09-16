"""What the owner wants loaded, and what the server resolved it to.

Two types, and the split between them is the whole point:

```text
ModelSelection      three opaque catalogue ids. DESIRED state.
                    Safe to log, to return to a browser, to persist.
                    Contains no path and cannot be made to contain one.

ResolvedSelection   the same selection plus the private payload references
                    the catalogues resolved it to. SERVER-OWNED.
                    Never projected, never logged, never persisted.
```

This replaces `ModelProfile` at the loader boundary. A profile was a box
holding three payload references plus an id, a display name and a family --
and on the catalogue path the family was the literal string `"catalogue"`, so
it carried nothing. The box outlived its purpose: it made an owner-facing
concept ("choose a profile, then Load") out of a transport detail.

`fingerprint` is the piece that matters next. Forge Neo decides whether to
reload by hashing its loading parameters and comparing against the resident
hash: same hash reuses the model, different hash unloads and reloads once.
This is Studio's equivalent, derived from the selected ids rather than from
paths, so a fingerprint never encodes a filesystem location and two owners
choosing the same three models agree on it.

Nothing here opens a payload. Resolution is metadata work; the only
filesystem contact is the containment revalidation the catalogue already
performs on every resolve.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from forge_headless.contracts import HeadlessError

from .contracts import StructuredError, StudioError

#: The roles that together make up the RESIDENT model a job runs on.
#:
#: Renamed from `RESIDENT_MODEL_ROLES`. That name lived in two files and sat one
#: import away from `catalogue.MODEL_ROLES`, which is a DIFFERENT set: every
#: role with a configurable root directory. The two were identical while there
#: were exactly three of each, so nothing distinguished them and the shared
#: shape read as a shared meaning.
#:
#: They diverge with Auto Detail. A detector has a root, so it joins
#: `MODEL_ROLES`; it is never part of the resident session, because it is
#: loaded per slot and released, so it does not join this one. The rename
#: happened BEFORE the fourth role was added rather than after -- the point at
#: which the names would otherwise have started lying.
RESIDENT_MODEL_ROLES = ("checkpoint", "text_encoder", "vae")

#: A checkpoint is the model. The other two are COMPONENTS, and whether they
#: are needed is a property of the checkpoint, not of the product: an SDXL or
#: SD 1.5 file carries its own CLIP and VAE inside it, while Anima and Flux
#: need a separate text encoder supplied alongside.
#:
#: The Extension expresses "nothing external" as ABSENCE FROM A LIST -- it
#: rebuilds `additional_modules` on every load and leaves it empty when the
#: owner chose nothing (`studio_api.py:5744, 5771-5775`). An empty id here is
#: that same absence, and Neo already accepts it: `backend/loader.py:822`
#: declares `additional_state_dicts: list = None` and `:843` only merges when
#: it IS a list, so an empty one simply skips.
REQUIRED_MODEL_ROLES = ("checkpoint",)
OPTIONAL_MODEL_ROLES = ("text_encoder", "vae")

#: Request field per role, so a browser never has to know about paths.
SELECTION_FIELDS = {
    "checkpoint": "checkpoint_model_id",
    "text_encoder": "text_encoder_model_id",
    "vae": "vae_model_id",
}

#: Domain-separated so a fingerprint cannot collide with another hash in the
#: product that happens to be built from the same ids.
_FINGERPRINT_DOMAIN = b"forge-studio/model-selection/v1"

SELECTION_INCOMPLETE = "SELECTION_INCOMPLETE"
SELECTION_MALFORMED = "SELECTION_MALFORMED"
SELECTION_UNKNOWN_MODEL = "SELECTION_UNKNOWN_MODEL"
SELECTION_MODEL_UNAVAILABLE = "SELECTION_MODEL_UNAVAILABLE"
SELECTION_ROOT_NOT_CONFIGURED = "SELECTION_ROOT_NOT_CONFIGURED"
SELECTION_ROOT_UNAVAILABLE = "SELECTION_ROOT_UNAVAILABLE"

#: Catalogue failures mapped to this module's vocabulary. The catalogue's codes
#: are precise but describe a catalogue; these describe a *selection*, which is
#: what the owner actually made.
_CATALOGUE_CODE_MAP = {
    "HEADLESS_MODEL_UNKNOWN": SELECTION_UNKNOWN_MODEL,
    "HEADLESS_MODEL_UNAVAILABLE": SELECTION_MODEL_UNAVAILABLE,
    "HEADLESS_MODEL_ID_REQUIRED": SELECTION_INCOMPLETE,
    "HEADLESS_MODEL_ROOT_NOT_CONFIGURED": SELECTION_ROOT_NOT_CONFIGURED,
    "HEADLESS_CATALOGUE_ROOT_UNAVAILABLE": SELECTION_ROOT_UNAVAILABLE,
    "HEADLESS_CATALOGUE_ROOT_CHANGED": SELECTION_ROOT_UNAVAILABLE,
    "HEADLESS_CATALOGUE_CASE_POLICY_INCONCLUSIVE": SELECTION_ROOT_UNAVAILABLE,
}

_MESSAGES = {
    SELECTION_INCOMPLETE: "Choose a checkpoint.",
    SELECTION_MALFORMED: "That model selection could not be read.",
    SELECTION_UNKNOWN_MODEL: (
        "One of those models is no longer in the list. Refresh and pick again."
    ),
    SELECTION_MODEL_UNAVAILABLE: "One of those model files is no longer available.",
    SELECTION_ROOT_NOT_CONFIGURED: (
        "A model folder for one of those roles is not configured."
    ),
    SELECTION_ROOT_UNAVAILABLE: "One of the configured model folders is unavailable.",
}


def _fail(code: str, field: str | None = None) -> StudioError:
    return StudioError(StructuredError(code=code, message=_MESSAGES[code], field=field))


@dataclass(frozen=True)
class ModelSelection:
    """Three opaque catalogue ids. The owner's desired model state.

    Deliberately not a profile: no id of its own, no display name, no family,
    no persistence identity. It is a value, and two selections naming the same
    three models are equal and share a fingerprint.
    """

    checkpoint_model_id: str
    text_encoder_model_id: str
    vae_model_id: str

    def __post_init__(self) -> None:
        for role in RESIDENT_MODEL_ROLES:
            value = getattr(self, SELECTION_FIELDS[role])
            if not isinstance(value, str):
                raise _fail(SELECTION_INCOMPLETE, SELECTION_FIELDS[role])
            if role in REQUIRED_MODEL_ROLES and not value.strip():
                raise _fail(SELECTION_INCOMPLETE, SELECTION_FIELDS[role])

    def supplies(self, role: str) -> bool:
        """Whether the owner named a model for this role at all."""

        return bool(str(getattr(self, SELECTION_FIELDS[role], "") or "").strip())

    def supplied_roles(self) -> tuple[str, ...]:
        """The roles with a model, in `RESIDENT_MODEL_ROLES` order."""

        return tuple(r for r in RESIDENT_MODEL_ROLES if self.supplies(r))

    @classmethod
    def from_payload(cls, payload: Any) -> "ModelSelection":
        """Read a selection from a request body. Touches no filesystem.

        Ids are validated for *shape* here so a path can never reach the
        resolver: catalogue ids are alphanumeric, and anything containing a
        separator, a dot or a drive letter is refused before resolution.
        """

        if not isinstance(payload, Mapping):
            raise _fail(SELECTION_MALFORMED)
        values: dict[str, str] = {}
        for role in RESIDENT_MODEL_ROLES:
            field = SELECTION_FIELDS[role]
            raw = payload.get(field)
            optional = role in OPTIONAL_MODEL_ROLES
            if raw is None:
                if not optional:
                    raise _fail(SELECTION_INCOMPLETE, field)
                values[field] = ""
                continue
            if not isinstance(raw, str):
                raise _fail(SELECTION_MALFORMED, field)
            value = raw.strip()
            if not value:
                # The page sends "" for its "None" and "Automatic" sentinels.
                # For a component that is the owner saying the checkpoint needs
                # nothing external, which is a complete answer, not a gap.
                if not optional:
                    raise _fail(SELECTION_INCOMPLETE, field)
                values[field] = ""
                continue
            if len(value) > 128 or not value.isalnum():
                raise _fail(SELECTION_MALFORMED, field)
            values[field] = value
        return cls(**values)

    def id_for(self, role: str) -> str:
        return getattr(self, SELECTION_FIELDS[role])

    def fingerprint(self) -> str:
        """Stable identity for "is this the same selection?".

        The Studio equivalent of Forge Neo's loading-parameter hash. Derived
        from ids, never from resolved paths, so it is safe to report and
        cannot leak a location. Order-independent of dict iteration because
        the roles are walked in a fixed order.
        """

        digest = hashlib.sha256()
        digest.update(_FINGERPRINT_DOMAIN)
        for role in RESIDENT_MODEL_ROLES:
            digest.update(b"\x00")
            digest.update(role.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(self.id_for(role).encode("utf-8"))
        return digest.hexdigest()[:16]

    def describe(self) -> dict[str, Any]:
        """The safe projection. Every field here may reach a browser."""

        return {
            "checkpoint_model_id": self.checkpoint_model_id,
            "text_encoder_model_id": self.text_encoder_model_id,
            "vae_model_id": self.vae_model_id,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class ResolvedSelection:
    """A selection plus what the catalogues resolved it to.

    Server-owned. `payload_references` holds private references, and the only
    projection offered is the selection's own -- ids and fingerprint, never a
    reference. `describe()` delegates rather than building its own dict, so
    there is exactly one definition of what is safe to report and no second
    place for a reference to be added by accident.

    `reference_for` and `profile_id` exist because the session loader reads
    them off whatever it is given. `profile_id` is the fingerprint under the
    loader's existing attribute name, kept until the loader's own vocabulary
    is renamed, so this phase changes one thing at a time.
    """

    selection: ModelSelection
    payload_references: Mapping[str, str]

    def __post_init__(self) -> None:
        missing = [r for r in REQUIRED_MODEL_ROLES
                   if not self.payload_references.get(r)]
        if missing:
            raise _fail(SELECTION_INCOMPLETE, SELECTION_FIELDS[missing[0]])
        object.__setattr__(self, "payload_references", dict(self.payload_references))

    def reference_for(self, role: str) -> str:
        return self.payload_references[role]

    def supplied_roles(self) -> tuple[str, ...]:
        """The roles that actually resolved to a payload, in role order."""

        return tuple(r for r in RESIDENT_MODEL_ROLES
                     if self.payload_references.get(r))

    def describe(self) -> dict[str, Any]:
        """The safe projection: the selection's, never the references'."""

        return self.selection.describe()

    @property
    def fingerprint(self) -> str:
        return self.selection.fingerprint()

    @property
    def profile_id(self) -> str:
        # The loader's attribute name, not a profile. See the class docstring.
        return self.selection.fingerprint()

    @property
    def family(self) -> str:
        # The catalogue path never carried a real family; the loader reads this
        # for telemetry only. Capabilities come from the loaded engine.
        return "catalogue"


def resolve_selection(selection: ModelSelection, *, registry: Any) -> ResolvedSelection:
    """Resolve three ids to payload references. Opens nothing.

    Each id is resolved through *its own role's* catalogue, which is what makes
    a cross-role id fail: a checkpoint id is simply not present in the VAE
    catalogue's snapshot. Containment is revalidated here, at resolve time,
    not merely when the root was configured.
    """

    ids = {role: selection.id_for(role) for role in RESIDENT_MODEL_ROLES}
    try:
        references = registry.build_payload_references(ids)
    except HeadlessError as error:
        raise _fail(
            _CATALOGUE_CODE_MAP.get(error.code, SELECTION_UNKNOWN_MODEL)
        ) from None
    return ResolvedSelection(selection=selection, payload_references=references)


def make_selection_resolver(registry: Any) -> Any:
    """A `request -> ResolvedSelection | None` callable over ONE registry.

    Bound to the registry instance the host already owns. There is exactly one
    `ModelRootRegistry` in the product, constructed in `launch.py` and held by
    `ModelRootSettings`; a second would mean a second case-policy probe and a
    second containment snapshot, so the two could disagree about what is
    inside a root while both believed themselves authoritative.

    Returns `None` when the request names no selection, which is not an error:
    a host with no catalogue, or a caller that has not adopted the field,
    keeps its previous behaviour and the lease's own refusal.
    """

    def resolve(request: Any) -> "ResolvedSelection | None":
        payload = getattr(request, "model_selection", None)
        if payload is None:
            return None
        return resolve_selection(ModelSelection.from_payload(payload), registry=registry)

    return resolve


__all__ = (
    "SELECTION_FIELDS",
    "SELECTION_INCOMPLETE",
    "SELECTION_MALFORMED",
    "SELECTION_MODEL_UNAVAILABLE",
    "RESIDENT_MODEL_ROLES",
    "OPTIONAL_MODEL_ROLES",
    "REQUIRED_MODEL_ROLES",
    "SELECTION_ROOT_NOT_CONFIGURED",
    "SELECTION_ROOT_UNAVAILABLE",
    "SELECTION_UNKNOWN_MODEL",
    "ModelSelection",
    "ResolvedSelection",
    "make_selection_resolver",
    "resolve_selection",
)
