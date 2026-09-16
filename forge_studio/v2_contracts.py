"""Versioned Brush V2 / Canvas 2.0 seam contracts. V2-01.

`03_SHARED_BRUSH_CANVAS_CONTRACT.md` §§2, 5, 6, 10, 11, implemented as an
EXTENSION of `contracts.py` rather than as a replacement for it. Everything here
is a frozen dataclass projected by the same `Contract.to_dict`, so a consumer
that already reads Studio's contracts reads these the same way.

WHAT THIS MODULE IS NOT.

It does not decode, hash, open, or write anything. `forge_studio` is the
purity-locked package -- `test_import_boundaries.py:104` imports it in a CHILD
INTERPRETER and asserts nothing under `gradio`, `modules`, `modules_forge` or
`webui` came with it, and `forge_headless/input_assets.py:6` records the related
rule that `forge_studio` may not import PIL. So `InputAssetRef` is a VALUE
carrying facts somebody else established, exactly as `AdmittedAsset` already is.

WHAT IS ALREADY STUDIO'S, and is named here rather than invented.

The bundle's §5 asks that asset facts be server-established and that a client
cannot claim a hash or a dimension and have it trusted. Studio has held that
line since WP1.2: `contracts.InputAsset:161-165` keeps `width`/`height` as
"What the browser SAYS. Verified against the decode, never believed", and
`forge_headless/input_assets.py:155-168` performs the verification and refuses a
mismatch by name. `content_hash` is computed by the server over the decoded
payload and is documented as "the one field here the browser does not supply and
cannot influence".

The gap this module marks is narrower and real: those decoded dimensions are
computed for the refusal and then DISCARDED, so every later consumer that needs
an asset's true size decodes it again. `InputAssetRef` is where they belong.
Wiring the producer to record them is V2-P4.1's, which owns the import path.

VERSIONING, three decisions (`Evidence/source-review/V2-01-contract-schemas.md`
§3):

  * one version per contract, not one for the bundle -- the bundle itself
    versions its formats independently, and a single number would make an
    additive field in one contract claim another had changed;
  * `schema_version` is a FIELD, not a wrapper -- every existing consumer reads
    Studio's contracts as flat JSON, and a wrapper makes that a two-step read
    for a value almost nothing branches on;
  * round-trip preservation is `unknown_fields`, KEPT AND RE-EMITTED. An
    intermediate layer that drops a field a newer producer sent is exactly what
    the enumeration tests exist to catch; carrying the unrecognised keys means
    it cannot happen silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Mapping

from .contracts import Contract

#: Current schema version of every contract in this module.
#:
#: One CONSTANT, one version per CONTRACT: each class carries its own
#: `schema_version` defaulted from here, so a later breaking change to one of
#: them pins its own number without moving the others.
V2_SCHEMA_VERSION = 1

#: The asset handle form Studio already mints (`asset_service.py:78-79`).
#:
#: DELIBERATELY NOT A NEW IDENTIFIER SPACE. The bundle asks for an opaque
#: `asset_id`; Studio has one, and minting a second identity for the same
#: object is how two of them drift out of step.
ASSET_ID_PATTERN = re.compile(r"\Astudio-asset/[0-9a-f]{32}\Z")

#: Anything that could be, or could become, a filesystem or network reference.
#:
#: The bundle says a `TargetRef` contains "never a path". Studio makes it
#: UNREPRESENTABLE instead of merely discouraged, which is the same rule the
#: asset service already holds at `asset_service.py:163-167`: a browser that can
#: name a server file is a browser that can read one, and no downstream check
#: recovers from having accepted the name.
_PATHISH = re.compile(r"""[/\\]|\.\.|\A[A-Za-z]:|\A[A-Za-z][A-Za-z0-9+.-]*:""")

#: 32 hex characters, the shape every opaque id in this tree already uses
#: (`CanvasDocument.document_id`, the asset and result handles).
_OPAQUE_ID = re.compile(r"\A[0-9a-f]{32}\Z")


class SchemaError(ValueError):
    """A contract value that cannot be represented. Never carries the input."""


class TargetKind(str, Enum):
    """The six the bundle's §2 `TargetRef` names, and no others.

    An enum rather than a string so an unknown kind is a refusal at
    construction instead of a branch nobody wrote falling through to "raster
    layer" three layers down.
    """

    RASTER_LAYER = "raster_layer"
    LAYER_MASK = "layer_mask"
    GENERATION_MASK = "generation_mask"
    SELECTION = "selection"
    REGION = "region"
    CENSORSHIP = "censorship"


class Retryable(str, Enum):
    """Whether a refusal is worth trying again, per §10.

    Three states rather than a boolean. "The same call will fail the same way"
    and "this will work once something changes" are different things to tell an
    owner, and collapsing them produces either a pointless retry loop or a
    dead end presented as permanent.
    """

    #: Nothing about repeating this call can help.
    NEVER = "never"
    #: The same call may succeed once a transient condition clears.
    TRANSIENT = "transient"
    #: Only after the owner changes something -- freeing memory, reopening the
    #: document, choosing a different target.
    AFTER_OWNER_ACTION = "after_owner_action"


@dataclass(frozen=True)
class Refusal:
    """One entry in the §10 table: a code, what to say, and whether to retry."""

    code: str
    message: str
    retryable: Retryable

    def __post_init__(self) -> None:
        # The message is shown to an owner and attached to support bundles. A
        # path in it is a leak that no later redaction reliably catches,
        # because by then it is prose.
        if _PATHISH.search(self.message):
            raise SchemaError(
                f"refusal {self.code} carries a path-shaped message")


#: The §10 required cases, each with a user-safe message and a retryability.
#:
#: NOT A MIGRATION. The codes in use today -- `ASSET_NOT_FOUND`, `RESULT_GONE`,
#: `GENERATION_NO_RESULT` and the rest -- are stable strings consumers already
#: match on, and rewriting them is a breaking change with its own fixtures.
#: This is the enumerated set V2 consumers speak; joining the two is recorded as
#: open work in the review record §7.
REFUSALS: dict[str, Refusal] = {
    r.code: r for r in (
        Refusal("V2_STALE_REVISION",
                "The document changed since this edit began. Reopen it and try "
                "again.", Retryable.AFTER_OWNER_ACTION),
        Refusal("V2_ASSET_MISSING",
                "That image is not available.", Retryable.NEVER),
        Refusal("V2_ASSET_EXPIRED",
                "That image was released to make room for newer ones.",
                Retryable.AFTER_OWNER_ACTION),
        Refusal("V2_ASSET_MALFORMED",
                "That image could not be read.", Retryable.NEVER),
        Refusal("V2_TARGET_UNAVAILABLE",
                "That layer is locked, hidden, or cannot be painted on.",
                Retryable.AFTER_OWNER_ACTION),
        Refusal("V2_RESOURCE_UNSUPPORTED",
                "Studio does not read that brush resource.", Retryable.NEVER),
        Refusal("V2_RESOURCE_INTEGRITY",
                "That resource does not match what it says it is.",
                Retryable.NEVER),
        Refusal("V2_COLOR_PROFILE_UNSUPPORTED",
                "That colour profile cannot be converted.", Retryable.NEVER),
        Refusal("V2_STORAGE_UNAVAILABLE",
                "Studio cannot reach its own storage, so recovery is off.",
                Retryable.TRANSIENT),
        Refusal("V2_PROVIDER_UNAVAILABLE",
                "That provider is not responding.", Retryable.TRANSIENT),
        Refusal("V2_INSUFFICIENT_MEMORY",
                "There is not enough memory for this operation. Close other "
                "documents, or try a smaller area.",
                Retryable.AFTER_OWNER_ACTION),
    )
}


def _require_opaque(value: Any, what: str, *, allow_empty: bool = False) -> str:
    text = str(value or "")
    if not text:
        if allow_empty:
            return ""
        raise SchemaError(f"{what} is required")
    if _PATHISH.search(text):
        raise SchemaError(f"{what} must be an opaque id, not a path")
    return text


class _Versioned(Contract):
    """`to_dict`/`from_dict` with unknown-field preservation.

    THE ROUND TRIP IS THE POINT. `to_dict` re-emits whatever `from_dict` did not
    recognise, so a field a newer producer added survives passing through an
    older consumer instead of being silently dropped at the join -- which is
    the exact failure the bundle's §11 enumeration tests exist to catch.
    """

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        unknown = payload.pop("unknown_fields", None) or {}
        # The unknown keys are re-emitted FLAT, as they arrived. Nesting them
        # under a key of our own would make a round trip through two versions
        # produce a different document than a round trip through one.
        for key, value in unknown.items():
            payload.setdefault(key, value)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]):  # type: ignore[no-untyped-def]
        if not isinstance(payload, Mapping):
            raise SchemaError(f"{cls.__name__} needs a mapping")
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        taken = {k: v for k, v in payload.items() if k in known
                 and k != "unknown_fields"}
        rest = {k: v for k, v in payload.items() if k not in known}
        return cls(**taken, unknown_fields=rest)  # type: ignore[call-arg]


@dataclass(frozen=True)
class TargetRef(_Versioned):
    """WHICH surface a stroke is going to, as opaque identity only.

    The bundle: "It contains opaque document/layer/channel identifiers, never a
    path, canvas DOM node, mutable layer object, or base64 payload." The first
    three of those are unrepresentable here because this is a frozen dataclass
    of strings; the path is refused at construction.
    """

    kind: TargetKind = TargetKind.RASTER_LAYER
    document_id: str = ""
    layer_id: str = ""
    #: The channel within the layer, where the kind has one. Empty is legal:
    #: a raster layer is its own channel.
    channel_id: str = ""
    schema_version: int = V2_SCHEMA_VERSION
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", TargetKind(self.kind))
        object.__setattr__(self, "document_id",
                           _require_opaque(self.document_id, "document_id"))
        object.__setattr__(self, "layer_id",
                           _require_opaque(self.layer_id, "layer_id"))
        object.__setattr__(self, "channel_id",
                           _require_opaque(self.channel_id, "channel_id",
                                           allow_empty=True))


@dataclass(frozen=True)
class InputAssetRef(_Versioned):
    """One image, described by facts the SERVER established. Bundle §5.

    Every field here is something the server measured or minted. Nothing on it
    can be claimed by a browser and believed, which is the property
    `contracts.InputAsset` documents from the other side: it keeps the client's
    `width`/`height` precisely so a mismatch can be NAMED, and
    `forge_headless/input_assets.py:155-168` names it.

    `asset_id` is Studio's existing handle (`asset_service.py:78`), not a new
    identifier space -- see the module docstring.

    DIMENSIONS ARE REQUIRED AND ARE THE POINT. `AdmittedAsset` has none:
    `input_assets.py` decodes them, uses them for the refusal, and throws them
    away, so every later consumer that needs the true size decodes again. This
    is where they belong. Wiring the producer to record them is V2-P4.1's.
    """

    asset_id: str = ""
    #: Of the DECODED payload, computed at admission
    #: (`asset_service.py:196`). Bare lowercase hex, 64 characters.
    sha256: str = ""
    media_type: str = ""
    width: int = 0
    height: int = 0
    byte_length: int = 0
    schema_version: int = V2_SCHEMA_VERSION
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not ASSET_ID_PATTERN.match(str(self.asset_id or "")):
            # The value is NOT quoted back. A malformed id and an unknown one
            # are reported identically so probing learns nothing, which is the
            # rule `asset_service._entry` already holds.
            raise SchemaError("asset_id is not a Studio asset handle")
        if not re.fullmatch(r"[0-9a-f]{64}", str(self.sha256 or "")):
            raise SchemaError("sha256 must be 64 lowercase hex characters")
        for name in ("width", "height", "byte_length"):
            value = int(getattr(self, name) or 0)
            if value <= 0:
                raise SchemaError(f"{name} must be a positive server-measured "
                                  "value")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class GenerationSnapshot(_Versioned):
    """What a job was admitted against, frozen. Bundle §6.

    "At admission, resolve all assets and dimensions and freeze the snapshot. A
    job never rereads the live Canvas. A Canvas revision change after admission
    does not alter the job."

    STUDIO ALREADY FREEZES THIS, across two objects rather than one:
    `CanvasDocument` (`contracts.py:110`) carries the document id and revision
    and exists for exactly this reason -- "`/api/generate` returns 202 and the
    job runs later, so between admission and execution the owner can paint,
    undo, resize" -- and `GenerationRequest` carries the source, mask and
    regions. This is the single named object the bundle asks later phases to
    speak; nothing produces one yet, and V2-P5.1 owns making the admission path
    do so.
    """

    document_id: str = ""
    canvas_revision: int = 0
    #: `txt2img`, `img2img` or `inpaint`. Studio's own vocabulary, not the
    #: engine's -- `test_product_truthfulness` forbids the engine's spellings
    #: in contract modules.
    operation: str = "txt2img"
    source: InputAssetRef | None = None
    mask: InputAssetRef | None = None
    regions: tuple[InputAssetRef, ...] = ()
    width: int = 0
    height: int = 0
    #: Bumped by a transform that changes placement without changing pixels, so
    #: a moved layer is a different snapshot from the same layer where it was.
    transform_revision: int = 0
    included_layer_ids: tuple[str, ...] = ()
    excluded_layer_ids: tuple[str, ...] = ()
    schema_version: int = V2_SCHEMA_VERSION
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id",
                           _require_opaque(self.document_id, "document_id"))
        if self.operation not in ("txt2img", "img2img", "inpaint"):
            raise SchemaError("operation is not one Studio performs")
        object.__setattr__(self, "regions", tuple(self.regions or ()))
        object.__setattr__(self, "included_layer_ids",
                           tuple(self.included_layer_ids or ()))
        object.__setattr__(self, "excluded_layer_ids",
                           tuple(self.excluded_layer_ids or ()))
        overlap = set(self.included_layer_ids) & set(self.excluded_layer_ids)
        if overlap:
            # A layer that is both in and out has no defined contribution, and
            # discovering that at composite time means discovering it as a
            # wrong picture.
            raise SchemaError("a layer is both included and excluded")


@dataclass(frozen=True)
class GeneratedLayerInfo(_Versioned):
    """What one generated layer is, and where it came from. Bundle §6.

    "Includes job id, snapshot revision, result asset, operation, batch index,
    untouched-preview flag, and metadata/embed disposition. It excludes raw
    checkpoint paths."

    `untouched_preview` is the flag V2-P5.2's Generated Preview turns on:
    a result the owner has not edited may be replaced by a later batch member,
    and the first edit promotes it to an ordinary layer. Recording it on the
    layer rather than in a side table is what makes the promotion a property of
    the thing being promoted.
    """

    job_id: str = ""
    #: The `canvas_revision` of the snapshot this was admitted against, NOT the
    #: document's revision now. The difference is the whole late-result
    #: problem: a result that arrives after the document moved on must be
    #: recognisable as such.
    snapshot_revision: int = 0
    result: InputAssetRef | None = None
    operation: str = "txt2img"
    batch_index: int = 0
    untouched_preview: bool = True
    #: Whether the SAVED FILE carries the parameters. The Gallery records them
    #: regardless -- AR5.4, and the Extension's contract
    #: (`studio_api.py:3227` against its Gallery writes).
    embed_metadata: bool = True
    schema_version: int = V2_SCHEMA_VERSION
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _PATHISH.search(str(self.job_id or "")):
            raise SchemaError("job_id must be an opaque id, not a path")


#: How every field of `AdmittedAsset` reaches `InputAssetRef`, or why it does
#: not. Read by the join test, so a field added to either side without a
#: decision fails rather than passing unnoticed.
#:
#: A TABLE RATHER THAN A FUNCTION, because the thing being asserted is that a
#: decision EXISTS for every field. A mapping function would silently ignore
#: what it did not mention, which is the failure this is aimed at.
ADMITTED_ASSET_DISPOSITION: dict[str, str] = {
    "handle": "asset_id",
    "content_hash": "sha256",
    "media_type": "media_type",
    "byte_length": "byte_length",
    # NAMED NON-MAPPINGS. Each is a decision, not an oversight.
    "role": "dropped: a role is how one request USES an asset, not a fact "
            "about the image. The same bytes are a source in one job and a "
            "mask in the next.",
    "retention": "dropped: a lifetime is the registry's policy, not the "
                 "asset's identity, and a ref that outlived its entry would "
                 "carry a stale claim about it.",
    "references": "dropped: a live count is not a fact about the image and "
                  "would make two refs to the same asset unequal.",
}

#: The fields of `InputAssetRef` that `AdmittedAsset` cannot supply today.
INPUT_ASSET_REF_UNSOURCED: dict[str, str] = {
    "width": "server-decoded; `input_assets.py:155` computes it for the "
             "refusal and discards it. V2-P4.1 records it.",
    "height": "server-decoded; see `width`.",
    "schema_version": "constant for this module's version.",
    "unknown_fields": "round-trip preservation only; never produced.",
}


__all__ = (
    "ADMITTED_ASSET_DISPOSITION",
    "ASSET_ID_PATTERN",
    "GeneratedLayerInfo",
    "GenerationSnapshot",
    "INPUT_ASSET_REF_UNSOURCED",
    "InputAssetRef",
    "REFUSALS",
    "Refusal",
    "Retryable",
    "SchemaError",
    "TargetKind",
    "TargetRef",
    "V2_SCHEMA_VERSION",
)
