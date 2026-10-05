"""Model identity for the direct headless load path.

Neo's production loader attaches catalogue identity to the model in
`modules/sd_models.py:373-375`, inside `forge_model_reload()`::

    sd_model.sd_checkpoint_info = checkpoint_info
    sd_model.filename = checkpoint_info.filename
    sd_model.sd_model_hash = checkpoint_info.calculate_shorthash()

The Tier-0 path deliberately does not call `forge_model_reload()` -- that is the
whole reason it enters at `process_images_inner` rather than `process_images`,
since the wrapper would re-resolve a checkpoint from Forge's own catalogue
instead of using three exact authorized files. Skipping the wrapper also skips
the attachment, and `process_images_inner` reads that identity unconditionally.

That is a contract defect at the direct-loader boundary, not a reason to weaken
Neo's assumptions, so nothing in `modules/` is patched to tolerate a missing
field. The identity is supplied here instead, before the engine is published.

Why not Forge's own `CheckpointInfo`
------------------------------------
`CheckpointInfo.__init__` reads the safetensors metadata header and computes
`model_hash(filename)`, and `calculate_shorthash()` hashes the entire payload --
for this checkpoint, roughly 4.2 GB. Those are payload reads: a second full read
of a file that has already been loaded, for no benefit the minimal path can use.
A future milestone with a catalogue projection may prefer the real type; the
`source` field on the attachment exists so that change is visible in evidence
rather than silent.

What is supplied, and why exactly this much
-------------------------------------------
Three fields, being every identity read reachable from `process_images_inner`
on the minimal closure:

===================================================  ===========================
`sd_checkpoint_info.name_for_extra`                  `modules/processing.py:894`
`sd_model_hash`                                      `modules/processing.py:895`
`sd_checkpoint_info.model_name`                      `modules/sd_unet.py:20`,
                                                     reached from
                                                     `modules/processing.py:929`
                                                     because `opts.sd_unet`
                                                     defaults to `"Automatic"`
===================================================  ===========================

`sd_model_hash` is `None`. That is not a placeholder: Forge's own
`calculate_shorthash()` returns `None` when no sha256 is available
(`modules/sd_models.py:94-95`), and the consumers downstream are already written
for it -- `create_infotext` gates on `opts.add_model_hash_to_info`, and
`images.py:406` uses `getattr` with a fallback. No cryptographic hash is
fabricated.

Everything else `CheckpointInfo` carries -- `filename`, `sha256`, `shorthash`,
`metadata`, `ids`, `title`, `register()` -- is deliberately absent. An off-path
consumer reaching for one should fail loudly rather than receive an invented
value or a leaked absolute path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Stand-in for the checkpoint name when the loader was given no checkpoint
#: reference. MI1 (2026-10-03): this used to be the label for EVERY load, so
#: every image's metadata read `Model: studio-tier0-session` where Neo and the
#: Extension write the checkpoint's name. The loader now passes
#: `checkpoint_label(...)`; this remains the fallback.
DEFAULT_RUNTIME_LABEL = "studio-tier0-session"


def checkpoint_label(reference: object) -> str:
    """The name Neo writes as `Model:` for a checkpoint file.

    Neo's own derivation, `CheckpointInfo.name_for_extra` at
    `modules/sd_models.py:73`: the file name without its folder or extension.
    Only the basename, so no folder -- and no user directory -- reaches
    metadata, which keeps the reason the placeholder existed while giving the
    owner the name the Extension shows. Separators of either kind are handled,
    so a Windows reference resolves the same on any host.

    String operations only, with `os.path.splitext`'s semantics (the last dot,
    and a leading dot is not an extension): this module is pinned to import
    nothing that could touch a file.
    """

    name = str(reference or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    stem = (name[:dot] if dot > 0 else name).strip()
    return stem or DEFAULT_RUNTIME_LABEL

#: Every identity field the Tier-0 closure reads, in `<object>.<field>` form.
#: `tests/studio_alpha/test_tier0_model_identity.py` pins this against current
#: Forge source, so a newly introduced required read fails loudly here rather
#: than at the next authorized live attempt.
REQUIRED_IDENTITY_FIELDS: tuple[str, ...] = (
    "sd_checkpoint_info.name_for_extra",
    "sd_checkpoint_info.model_name",
    "sd_model_hash",
)

FORGE_CHECKPOINT_INFO = "forge_checkpoint_info"
STUDIO_COMPATIBILITY_IDENTITY = "studio_compatibility_identity"

HASH_REAL = "real"
HASH_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StudioCompatibilityIdentity:
    """The minimum `sd_checkpoint_info` the retained inner loop reads.

    Frozen, so a consumer cannot quietly mutate identity mid-generation, and
    deliberately narrow -- see the module docstring for what is omitted.
    """

    name_for_extra: str
    model_name: str

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"StudioCompatibilityIdentity(label={self.name_for_extra!r})"


@dataclass(frozen=True)
class ModelIdentityAttachment:
    """What was attached, so evidence can state it without re-deriving it."""

    attached: bool
    source: str
    runtime_label: str
    hash_status: str
    required_fields: tuple[str, ...] = field(default=REQUIRED_IDENTITY_FIELDS)

    def to_dict(self) -> dict[str, object]:
        return {
            "attached": self.attached,
            "source": self.source,
            "runtime_label": self.runtime_label,
            "hash_status": self.hash_status,
            "required_fields": list(self.required_fields),
        }


def attach_model_identity(
    engine: object, *, runtime_label: str = DEFAULT_RUNTIME_LABEL
) -> ModelIdentityAttachment:
    """Give a directly loaded engine the identity `process_images_inner` reads.

    Must be called after the loader returns and **before** the engine reaches
    `shared.sd_model`, because publication is what makes it visible to
    processing.

    An engine that already carries a real `sd_checkpoint_info` -- one that came
    through Neo's own loader -- is left alone and reported as
    `forge_checkpoint_info`, so this never overwrites production identity.
    """

    existing = getattr(engine, "sd_checkpoint_info", None)
    if existing is not None and not isinstance(existing, StudioCompatibilityIdentity):
        return ModelIdentityAttachment(
            attached=False,
            source=FORGE_CHECKPOINT_INFO,
            runtime_label=str(getattr(existing, "name_for_extra", "") or ""),
            hash_status=(
                HASH_REAL if getattr(engine, "sd_model_hash", None) else HASH_UNAVAILABLE
            ),
        )

    engine.sd_checkpoint_info = StudioCompatibilityIdentity(
        name_for_extra=runtime_label,
        model_name=runtime_label,
    )
    # Truthfully unavailable rather than fabricated; Forge produces None here
    # too when no sha256 is cached. See the module docstring.
    engine.sd_model_hash = None

    return ModelIdentityAttachment(
        attached=True,
        source=STUDIO_COMPATIBILITY_IDENTITY,
        runtime_label=runtime_label,
        hash_status=HASH_UNAVAILABLE,
    )


def detach_model_identity(engine: object) -> bool:
    """Drop a Studio-attached identity as part of releasing the session.

    Only removes what this module attached. A real `CheckpointInfo` belongs to
    Neo's catalogue and is left untouched.
    """

    identity = getattr(engine, "sd_checkpoint_info", None)
    if not isinstance(identity, StudioCompatibilityIdentity):
        return False
    try:
        engine.sd_checkpoint_info = None
        engine.sd_model_hash = None
    except Exception:  # noqa: BLE001 - cleanup must not raise
        return False
    return True
