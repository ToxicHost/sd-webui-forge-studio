"""Contained model catalogue for the headless Forge boundary.

Enumerates model candidates from an **explicitly configured** root, under one
**role**: checkpoint, text encoder or VAE. Nothing is auto-discovered: no Forge
or WebUI default directory, no user profile, no Downloads, no system temp, no
environment scan, no network location.

A root may now live outside the Studio workspace, which is a real widening of
what this process can reach. It requires ``allow_external=True`` at the call
site, and the root itself is validated by ``root_policy.validate_root`` before
anything touches it.

What this module never does:

* open, read, mmap, hash, parse, or deserialize a model -- ``size_bytes`` comes
  from a directory-entry ``stat`` and nothing else;
* recurse by default -- one directory level unless the caller opts in;
* follow a symlink or reparse point (they are skipped, not resolved, so a
  symlinked model inside a bind mount is not listed at all);
* decide containment when case behaviour is inconclusive;
* let an id from one role resolve under another -- role is a digest input.

Extension filtering here is a usability and scan-bounding measure. **It is not
the deserialization boundary**; that remains the retained Forge loader, which
catalogue-backed loads still enter unchanged.

Recognized formats are derived from the retained backend's own source, not from
familiar Stable Diffusion filename conventions. See ``FORMAT_SUPPORT`` and
``MODULE_FORMAT_SUPPORT`` below.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
from dataclasses import dataclass
from pathlib import Path

from forge_studio.result_delivery import CasePolicy

# Containment, case behaviour, redirection and name acceptability moved to
# `path_identity` when the directory browser became a second consumer of them.
# Re-bound to the private names this module has always used, so every call site
# below -- and every test that monkeypatches one -- is unchanged. The
# definitions moved; the behaviour did not.
from .path_identity import (  # noqa: F401  (re-exported under legacy names)
    detect_case_policy_readonly,
    has_entries as _has_entries,
    is_prefix as _is_prefix,
    is_redirecting as _is_reparse_point,
    name_is_acceptable,
    nfc as _nfc,
)

from .contracts import (
    HeadlessError,
    LoadSupport,
    ModelAvailability,
    ModelCandidate,
)
from .root_policy import (
    ROOT_OUTSIDE_WORKSPACE,
    ROOT_UNAVAILABLE,
    validate_root,
)

CASE_POLICY_INCONCLUSIVE = "HEADLESS_CATALOGUE_CASE_POLICY_INCONCLUSIVE"

#: Domain separator for catalogue ids, so they cannot coincide with any other
#: SHA-256-over-role-and-path identifier in this package.
_ID_DOMAIN = b"forge-studio/model-catalogue/v1"


#: Formats and how far the retained loader actually takes them.
#:
#: ``modules/sd_models.py:136`` enumerates checkpoints with
#: ``ext_filter=[".ckpt", ".safetensors", ".gguf"]``.
#: ``backend/utils.py::load_torch_file`` dispatches ``.safetensors``/``.sft`` to
#: safetensors, ``.gguf`` to the GGUF reader, and anything else to
#: ``torch.load(weights_only=True)``.
#:
#: ``.sft`` is therefore *recognized by the loader but never enumerated by
#: Forge's own catalogue* -- a real half-supported case, recorded rather than
#: promoted to "loadable".
FORMAT_SUPPORT: dict[str, LoadSupport] = {
    ".safetensors": LoadSupport.LOAD_PLUMBED,
    ".ckpt": LoadSupport.LOAD_PLUMBED,
    ".gguf": LoadSupport.LOAD_PLUMBED,
    ".sft": LoadSupport.RECOGNIZED_NOT_PLUMBED,
}

#: Text encoders and VAEs are one namespace to Forge. ``refresh_models`` at
#: ``modules_forge/main_entry.py:97`` enumerates *both* directories with
#: ``("ckpt", "pt", "pth", "bin", "safetensors", "sft", "gguf")`` and no
#: blacklist, then merges them into a single ``module_list``.
#:
#: Two differences from the checkpoint table are real, not oversights:
#: ``.sft`` *is* enumerated here, and ``.pt``/``.pth``/``.bin`` exist at all.
#: Those three reach ``torch.load(weights_only=True)`` in
#: ``backend/utils.py::load_torch_file`` -- the retained loader's own behaviour,
#: entered only on load. Enumeration still never opens them.
MODULE_FORMAT_SUPPORT: dict[str, LoadSupport] = {
    ".safetensors": LoadSupport.LOAD_PLUMBED,
    ".sft": LoadSupport.LOAD_PLUMBED,
    ".ckpt": LoadSupport.LOAD_PLUMBED,
    ".pt": LoadSupport.LOAD_PLUMBED,
    ".pth": LoadSupport.LOAD_PLUMBED,
    ".bin": LoadSupport.LOAD_PLUMBED,
    ".gguf": LoadSupport.LOAD_PLUMBED,
}

#: Suffixes Forge itself excludes from the checkpoint catalogue
#: (``ext_blacklist`` at ``modules/sd_models.py:136``). These are VAE files that
#: happen to share a checkpoint extension.
BLACKLISTED_SUFFIXES: tuple[str, ...] = (".vae.ckpt", ".vae.safetensors")

MODEL_ROLE_CHECKPOINT = "checkpoint"
MODEL_ROLE_TEXT_ENCODER = "text_encoder"
MODEL_ROLE_VAE = "vae"
MODEL_ROLE_ADETAILER = "adetailer"
MODEL_ROLE_LORA = "lora"

#: Every role with a configurable ROOT DIRECTORY, in presentation order.
#:
#: Not the same set as `model_selection.RESIDENT_MODEL_ROLES`, and P0.8 is
#: where they stop coinciding. A detector has a root the owner points at, so
#: it belongs here; it is never part of the resident session, because it is
#: loaded per Auto Detail slot and released, so it does not belong there.
#:
#: The other list was renamed FROM `SELECTION_ROLES` before this line was
#: added. While both were three-long and identically spelled, nothing marked
#: them as different concepts.
MODEL_ROLES: tuple[str, ...] = (
    MODEL_ROLE_CHECKPOINT,
    MODEL_ROLE_TEXT_ENCODER,
    MODEL_ROLE_VAE,
    MODEL_ROLE_ADETAILER,
    #: Added 2026-08-21 for the same reason `adetailer` was: the root has to be
    #: configurable and validated before anything can enumerate it. A LoRA is
    #: never part of the resident session either -- it is named in the prompt
    #: and applied per generation -- so it belongs here and NOT in
    #: `RESIDENT_MODEL_ROLES`.
    MODEL_ROLE_LORA,
)

#: Retained name for the checkpoint role. ``model_kind`` on a candidate is the
#: role it was catalogued under.
MODEL_KIND_CHECKPOINT = MODEL_ROLE_CHECKPOINT

#: A detector is a YOLO `.pt` and nothing else.
#:
#: Deliberately NOT `MODULE_FORMAT_SUPPORT`, which also admits `.safetensors`,
#: `.ckpt`, `.bin` and `.gguf`. Those are real model formats and none of them
#: is a detector: offering one in the Auto Detail dropdown would be a control
#: presenting a choice that cannot work, which is the defect this phase keeps
#: finding. `.pt` matches upstream's own scan, so a directory already prepared
#: for ADetailer enumerates unchanged.
DETECTOR_FORMAT_SUPPORT: dict[str, LoadSupport] = {
    ".pt": LoadSupport.LOAD_PLUMBED,
}

#: What a LoRA file may be. `.safetensors`, `.pt` and `.ckpt` are what the
#: engine's own scan accepts (`sd_forge_lora/networks.py`), so a directory
#: already prepared for Forge enumerates unchanged.
#:
#: NOT `MODULE_FORMAT_SUPPORT`: that also admits `.bin` and `.gguf`, which are
#: real model formats and are not LoRAs. Offering one would be a dropdown entry
#: that cannot work.
LORA_FORMAT_SUPPORT: dict[str, LoadSupport] = {
    ".safetensors": LoadSupport.LOAD_PLUMBED,
    ".pt": LoadSupport.LOAD_PLUMBED,
    ".ckpt": LoadSupport.LOAD_PLUMBED,
}

ROLE_FORMAT_SUPPORT: dict[str, dict[str, LoadSupport]] = {
    MODEL_ROLE_CHECKPOINT: FORMAT_SUPPORT,
    MODEL_ROLE_TEXT_ENCODER: MODULE_FORMAT_SUPPORT,
    MODEL_ROLE_VAE: MODULE_FORMAT_SUPPORT,
    MODEL_ROLE_ADETAILER: DETECTOR_FORMAT_SUPPORT,
    MODEL_ROLE_LORA: LORA_FORMAT_SUPPORT,
}

#: The checkpoint blacklist exists to keep VAE files out of the *checkpoint*
#: list. Applying it to the VAE role would hide exactly the files that role is
#: for, so it is scoped to the role it came from.
ROLE_BLACKLISTED_SUFFIXES: dict[str, tuple[str, ...]] = {
    MODEL_ROLE_CHECKPOINT: BLACKLISTED_SUFFIXES,
    MODEL_ROLE_TEXT_ENCODER: (),
    MODEL_ROLE_VAE: (),
    MODEL_ROLE_ADETAILER: (),
    MODEL_ROLE_LORA: (),
}

#: Bounds on a single scan. A root is now owner-supplied and may be outside the
#: workspace, so a mistyped root must degrade into a truncated list rather than
#: an unbounded directory walk.
MAX_SCANNED_ENTRIES = 20_000
MAX_CATALOGUE_ENTRIES = 4_096

#: Phase 2A refuses every real load, after full validation.
LOAD_NOT_AUTHORIZED = "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED"
LOAD_FORMAT_NOT_PLUMBED = "HEADLESS_MODEL_FORMAT_NOT_PLUMBED"

_MAX_DISPLAY_NAME = 120
_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\x7f\\/]+")
_COLLAPSE_SPACE = re.compile(r"\s+")


def sanitize_display_name(stem: str) -> str:
    """Make a filename stem safe to show, without inventing information."""
    cleaned = _CONTROL_OR_SEPARATOR.sub(" ", stem)
    cleaned = _COLLAPSE_SPACE.sub(" ", cleaned).strip()
    if not cleaned:
        return "unnamed model"
    if len(cleaned) > _MAX_DISPLAY_NAME:
        cleaned = cleaned[:_MAX_DISPLAY_NAME].rstrip() + "..."
    return cleaned


def _model_id(role: str, root_key: str, relative_location: str) -> str:
    """Digest role, root and location into one opaque id.

    Role is a digest *input*, not a field checked afterwards. That is the whole
    point: a checkpoint id and a VAE id for the same filename under the same
    root are different strings, so a checkpoint id cannot be made to resolve in
    the VAE catalogue by omitting a comparison somewhere. Cross-role resolution
    fails as "unknown id", by construction.
    """
    digest = hashlib.sha256()
    # Domain tag first. load_authorization.opaque_file_id digests
    # role + NUL + path with the same primitive; without a distinguishing
    # prefix the two id spaces would merely happen not to collide.
    digest.update(_ID_DOMAIN)
    digest.update(b"\x00")
    digest.update(role.encode("utf-8", "surrogatepass"))
    digest.update(b"\x00")
    # NFC before digesting, on both halves. macOS is the reason: HFS+ stores
    # decomposed names and APFS compares normalization-insensitively, so ONE
    # file can arrive decomposed from a directory scan and composed from a path
    # the owner typed. Digesting the raw strings minted two ids for it, and a
    # remembered selection then matched neither. Identity only -- the name that
    # gets opened and the name that gets displayed stay exactly as the
    # filesystem gave them, because normalizing a path before opening it can
    # name a different file, or none.
    digest.update(_nfc(root_key).encode("utf-8", "surrogatepass"))
    digest.update(b"\x00")
    digest.update(_nfc(relative_location).encode("utf-8", "surrogatepass"))
    return digest.hexdigest()[:32]


def classify_format(
    name: str,
    role: str = MODEL_ROLE_CHECKPOINT,
) -> tuple[str, LoadSupport]:
    """Return ``(format, support)`` for a filename under a role's tables."""
    formats = ROLE_FORMAT_SUPPORT.get(role, FORMAT_SUPPORT)
    lowered = name.lower()
    for blacklisted in ROLE_BLACKLISTED_SUFFIXES.get(role, ()):
        if lowered.endswith(blacklisted):
            return blacklisted.lstrip("."), LoadSupport.UNSUPPORTED
    suffix = os.path.splitext(lowered)[1]
    support = formats.get(suffix, LoadSupport.UNSUPPORTED)
    return suffix.lstrip("."), support


@dataclass(frozen=True)
class ContainedSource:
    """A resolved, re-verified path inside the configured root.

    Constructed only by the catalogue. The Studio layer never supplies one and
    never sees one: it supplies a ``model_id``.
    """

    root: Path
    relative_location: str
    resolved: Path


@dataclass(frozen=True)
class CatalogueSnapshot:
    """One coherent listing of a root.

    Ids are only meaningful against the snapshot that produced them.
    ``generation`` makes that checkable rather than assumed: a client holding an
    id from an older generation gets ``HEADLESS_MODEL_UNKNOWN`` if the entry is
    gone, not a silently different file.
    """

    role: str
    generation: int
    entries: tuple[ModelCandidate, ...]
    #: ``model_id`` -> root-relative location, for O(1) resolution.
    entries_by_id: dict[str, str]
    scanned_entries: int
    truncated: bool


class _ScanBudget:
    """Mutable scan accounting. Separate so truncation is reported, not hidden."""

    def __init__(self) -> None:
        self.scanned = 0
        self.truncated = False

    def spend(self) -> bool:
        """Count one examined entry. False once the scan cap is reached."""
        if self.scanned >= MAX_SCANNED_ENTRIES:
            self.truncated = True
            return False
        self.scanned += 1
        return True


class ModelCatalogue:
    """Explicit-root model enumeration with fail-closed containment."""

    def __init__(
        self,
        root: os.PathLike[str] | str,
        *,
        workspace_root: os.PathLike[str] | str,
        recursive: bool = False,
        case_policy: CasePolicy | None = None,
        role: str = MODEL_ROLE_CHECKPOINT,
        allow_external: bool = False,
    ) -> None:
        if role not in ROLE_FORMAT_SUPPORT:
            raise HeadlessError(
                "HEADLESS_CATALOGUE_ROLE_UNKNOWN",
                "That model role does not exist.",
            )
        workspace = Path(workspace_root).resolve()
        # Network, device-namespace, drive-root and remote-volume refusals all
        # happen inside validate_root, and the ones that could otherwise reach a
        # remote host happen before it stats anything.
        resolved_root = validate_root(root, require_absolute=allow_external)

        if not allow_external:
            # Default remains the Phase 2A restriction. Reaching outside the
            # workspace is not a property a call site acquires by accident: it
            # has to pass allow_external, which is greppable.
            inside = _is_prefix(
                workspace, resolved_root, CasePolicy.CASE_SENSITIVE, allow_equal=True
            ) or _is_prefix(
                workspace, resolved_root, CasePolicy.CASE_INSENSITIVE, allow_equal=True
            )
            if not inside:
                raise HeadlessError(
                    ROOT_OUTSIDE_WORKSPACE,
                    "The configured model root is outside the workspace.",
                )

        self._root = resolved_root
        self._workspace = workspace
        self._recursive = recursive
        self._role = role
        self._external = allow_external
        # A caller-supplied policy is a decision and is never re-probed; a
        # detected one may be revisited when the root's contents change.
        self._policy_pinned = case_policy is not None
        self._case_policy = (
            case_policy
            if case_policy is not None
            else detect_case_policy_readonly(resolved_root)
        )
        if self._case_policy is CasePolicy.INCONCLUSIVE and _has_entries(resolved_root):
            # Handoff section 9: an inconclusive probe must not guess, enumerate
            # or resolve. An *empty* root is exempt because there is nothing to
            # enumerate and nothing to resolve -- refusing it would turn "I made
            # a folder for my VAEs" into a hard error. resolve() still refuses
            # under an inconclusive policy regardless.
            raise HeadlessError(
                CASE_POLICY_INCONCLUSIVE,
                "Case behaviour of that model root could not be determined.",
            )
        # The digest key is the resolved root, so the same filename under a
        # different root yields a different id, as required.
        self._root_key = str(resolved_root)
        self._generation = 0
        self._snapshot: CatalogueSnapshot | None = None

    @property
    def root_key(self) -> str:
        """The resolved root, as the id digest sees it.

        Public so the registry can tell two spellings of ONE directory apart
        when a role lists several. It has to be this exact value: ids are
        keyed on it, so two catalogues sharing a root_key would produce
        identical ids for the same file and be indistinguishable downstream
        rather than merely duplicated.
        """

        return self._root_key

    @property
    def root_configured(self) -> bool:
        return True

    @property
    def case_policy(self) -> CasePolicy:
        return self._case_policy

    @property
    def recursive(self) -> bool:
        return self._recursive

    @property
    def role(self) -> str:
        return self._role

    @property
    def generation(self) -> int:
        return self._generation

    def refresh(self) -> CatalogueSnapshot:
        """Re-read the root and publish a new snapshot.

        An empty root is allowed to exist with an inconclusive case policy, so
        this is where the first model arriving gets handled. If the policy was
        *detected* and was inconclusive, probe again -- the root may now hold an
        entry that settles the question. If it settles, catalogue normally. If
        it does not, publish nothing: listing models that ``resolve()`` will
        then refuse would be worse than an honest empty list.
        """
        if self._case_policy is CasePolicy.INCONCLUSIVE and not self._policy_pinned:
            probed = detect_case_policy_readonly(self._root)
            if probed is not CasePolicy.INCONCLUSIVE:
                self._case_policy = probed
        if self._case_policy is CasePolicy.INCONCLUSIVE:
            self._generation += 1
            snapshot = CatalogueSnapshot(
                role=self._role,
                generation=self._generation,
                entries=(),
                entries_by_id={},
                scanned_entries=0,
                truncated=False,
            )
            self._snapshot = snapshot
            return snapshot

        found, budget = self._walk()
        candidates: list[ModelCandidate] = []
        for relative, entry_path in found:
            candidate = self._describe(relative, entry_path)
            if candidate is not None:
                candidates.append(candidate)
        # Deterministic ordering that does not depend on directory order or on
        # locale: root-relative location, then id as a tiebreak.
        candidates.sort(key=lambda item: (item.relative_location, item.model_id))
        self._generation += 1
        snapshot = CatalogueSnapshot(
            role=self._role,
            generation=self._generation,
            entries=tuple(candidates),
            entries_by_id={
                item.model_id: item.relative_location for item in candidates
            },
            scanned_entries=budget.scanned,
            truncated=budget.truncated,
        )
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> CatalogueSnapshot:
        """The current snapshot, reading the root once if there is none yet."""
        if self._snapshot is None:
            return self.refresh()
        return self._snapshot

    def enumerate(self) -> tuple[ModelCandidate, ...]:
        """List candidates deterministically. Per-entry failures stay per-entry."""
        return self.refresh().entries

    def resolve(self, model_id: str) -> tuple[ModelCandidate, ContainedSource]:
        """Translate an opaque id into a re-verified contained source.

        Resolution goes through the current snapshot, so an id names the entry
        the client was actually shown. Everything the snapshot asserted is then
        re-established against the filesystem -- the root is still the root, the
        entry is still a contained regular file of a supported format -- because
        a snapshot is a record of the past, not a permission.
        """
        if self._case_policy is CasePolicy.INCONCLUSIVE:
            raise HeadlessError(
                CASE_POLICY_INCONCLUSIVE,
                "Case behaviour of that model root could not be determined.",
            )
        snapshot = self.snapshot()
        relative = snapshot.entries_by_id.get(model_id)
        if relative is None:
            raise HeadlessError(
                "HEADLESS_MODEL_UNKNOWN",
                "That model is not in the catalogue.",
            )
        self._assert_root_unchanged()
        entry_path = self._root.joinpath(*relative.split("/"))
        candidate = self._describe(relative, entry_path)
        if candidate is None:
            # Format or containment changed under us since the snapshot.
            raise HeadlessError(
                "HEADLESS_MODEL_UNKNOWN",
                "That model is not in the catalogue.",
            )
        if candidate.availability is not ModelAvailability.AVAILABLE:
            raise HeadlessError(
                "HEADLESS_MODEL_UNAVAILABLE",
                "That model is no longer available.",
            )
        resolved = self._contained(entry_path)
        return candidate, ContainedSource(
            root=self._root,
            relative_location=relative,
            resolved=resolved,
        )

    # -- internals ---------------------------------------------------------

    def _assert_root_unchanged(self) -> None:
        """Catch a root replaced by a link since construction.

        Entry containment would catch the escape anyway, since it tests against
        the stored root. This exists so the failure names the real cause.
        """
        try:
            current = self._root.resolve(strict=True)
        except OSError:
            raise HeadlessError(
                ROOT_UNAVAILABLE,
                "The configured model root is not available.",
            ) from None
        if current != self._root or not current.is_dir():
            raise HeadlessError(
                "HEADLESS_CATALOGUE_ROOT_CHANGED",
                "The configured model root changed since it was listed.",
            )

    def _walk(self) -> tuple[list[tuple[str, Path]], _ScanBudget]:
        found: list[tuple[str, Path]] = []
        budget = _ScanBudget()
        self._scan(self._root, "", found, depth=0, budget=budget)
        return found, budget

    def _scan(
        self,
        directory: Path,
        prefix: str,
        found: list[tuple[str, Path]],
        *,
        depth: int,
        budget: _ScanBudget,
    ) -> None:
        try:
            # `with`, and bounded. `sorted(os.scandir(...))` left the iterator
            # unclosed AND materialized every entry in the directory before the
            # scan budget could refuse anything -- so a folder holding half a
            # million files built half a million DirEntry objects first and
            # applied the 20 000 cap afterwards. `nsmallest` keeps the same
            # name order while holding only the bound.
            with os.scandir(directory) as scan:
                entries = heapq.nsmallest(
                    MAX_SCANNED_ENTRIES, scan, key=lambda item: item.name
                )
        except OSError:
            # A directory that cannot be listed is skipped, not fatal: one bad
            # subdirectory must not collapse the whole catalogue.
            return
        for entry in entries:
            if not budget.spend():
                return
            if len(found) >= MAX_CATALOGUE_ENTRIES:
                budget.truncated = True
                return
            name = entry.name
            if not name_is_acceptable(name):
                continue
            relative = f"{prefix}{name}"
            if _is_reparse_point(entry):
                # Reject rather than resolve: a link is exactly how a caller
                # would try to reach outside the root.
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                # ALL THE WAY DOWN. This was `self._recursive and depth == 0`,
                # so a recursive scan descended exactly ONE level and a model
                # in `Checkpoints/SDXL/Anime/` was invisible with nothing said.
                #
                # Bounded by the same budget as before rather than by depth:
                # `budget.spend()` above caps entries WALKED at
                # MAX_SCANNED_ENTRIES and `len(found)` caps entries KEPT at
                # MAX_CATALOGUE_ENTRIES, both inside this recursion. Depth was
                # never what made the walk finite.
                if self._recursive:
                    self._scan(
                        Path(entry.path),
                        f"{relative}/",
                        found,
                        depth=depth + 1,
                        budget=budget,
                    )
                continue
            # Format-filtered HERE, so MAX_CATALOGUE_ENTRIES caps catalogue
            # entries rather than candidate files. It used to cap everything
            # this loop appended and let `_describe` discard the unsupported
            # ones afterwards -- so 4 096 readme and thumbnail files sorting
            # ahead of the checkpoints filled the budget and the owner got a
            # truncated catalogue containing no models at all.
            _, support = classify_format(name, self._role)
            if support is LoadSupport.UNSUPPORTED:
                continue
            found.append((relative, Path(entry.path)))
        return

    def _describe(self, relative: str, path: Path) -> ModelCandidate | None:
        name = relative.rsplit("/", 1)[-1]
        fmt, support = classify_format(name, self._role)
        if support is LoadSupport.UNSUPPORTED:
            return None
        availability = ModelAvailability.AVAILABLE
        size_bytes = 0
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            availability = ModelAvailability.MISSING
        except OSError:
            availability = ModelAvailability.UNREADABLE
        else:
            size_bytes = int(stat_result.st_size)
        try:
            # Unconditional, not only for AVAILABLE entries. A MISSING or
            # UNREADABLE row was previously emitted without ever being proven
            # inside the root; the scan makes that safe by construction, but an
            # unverified row should not be published on that reasoning alone.
            self._contained(path)
        except HeadlessError:
            # Resolves outside the root, is gone, or containment is undecidable.
            return None
        blocking = (
            LOAD_NOT_AUTHORIZED
            if support is LoadSupport.LOAD_PLUMBED
            else LOAD_FORMAT_NOT_PLUMBED
        )
        stem = name[: -(len(fmt) + 1)] if fmt else name
        return ModelCandidate(
            model_id=_model_id(self._role, self._root_key, relative),
            display_name=sanitize_display_name(stem),
            model_kind=self._role,
            format=fmt,
            relative_location=relative,
            size_bytes=size_bytes,
            availability=availability,
            load_support=support,
            load_blocking_reason=blocking,
        )

    def _contained(self, path: Path) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError:
            raise HeadlessError(
                "HEADLESS_MODEL_UNAVAILABLE",
                "That model is no longer available.",
            ) from None
        if not resolved.is_file():
            raise HeadlessError(
                "HEADLESS_MODEL_UNAVAILABLE",
                "That model is no longer available.",
            )
        if _is_prefix(self._root, resolved, CasePolicy.CASE_SENSITIVE):
            return resolved
        if self._case_policy is CasePolicy.CASE_INSENSITIVE and _is_prefix(
            self._root, resolved, CasePolicy.CASE_INSENSITIVE
        ):
            return resolved
        raise HeadlessError(
            "HEADLESS_MODEL_OUTSIDE_ROOT",
            "That model is outside the configured model root.",
        )
