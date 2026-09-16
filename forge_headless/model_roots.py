"""Three owner-configured model roots, held as one replaceable unit.

One catalogue per role. The registry exists so that reconfiguration is a
**transaction**: either all three roots validate and all three catalogues are
replaced, or nothing changes and the previously working configuration keeps
serving. A half-applied configuration -- new checkpoints, stale VAEs -- would be
the kind of state nobody tests and everybody eventually hits.

Nothing here is discovered. A role with no configured root is simply not
configured, and its list is empty; there is no default directory, no fallback to
a Forge or WebUI path, and no environment scan.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .catalogue import MODEL_ROLES, CatalogueSnapshot, ModelCatalogue
from .contracts import HeadlessError, ModelCandidate

#: Roles a checkpoint may carry inside itself. See
#: `forge_studio.model_selection.OPTIONAL_MODEL_ROLES` -- named again
#: here because the headless side may not import the Studio layer.
OPTIONAL_PAYLOAD_ROLES = ("text_encoder", "vae")

#: Config key holding the three roots.
CONFIG_KEY = "model_roots"

#: How many directories one role will enumerate. An IMPLEMENTATION limit,
#: not a format constraint: the schema and the API accept a variable-length
#: list, and raising this is a one-line change with nothing to migrate.
MAX_ROOTS_PER_ROLE = 8

#: Raised when that limit is exceeded, distinct from a malformed document so
#: the owner is told which of the two they hit.
ROOTS_TOO_MANY = "HEADLESS_MODEL_ROOTS_TOO_MANY"

#: A role with roots configured, some of which are serving and some refused.
#: Unreachable with a single root, so every pre-P0.4 status is unchanged.
ROOT_STATUS_PARTIAL = "partial"

ROOT_STATUS_NOT_CONFIGURED = "not_configured"
ROOT_STATUS_READY = "ready"
ROOT_STATUS_REFUSED = "refused"


@dataclass(frozen=True)
class RootStatus:
    """One configured directory within a role, and how it fared.

    Carries an ``ordinal`` rather than a path, for the same reason RoleStatus
    carries neither: the Settings page already knows the paths because the
    owner typed them, and every other surface must be able to render this
    without learning a filesystem layout.
    """

    ordinal: int
    status: str
    entry_count: int = 0
    truncated: bool = False
    reason_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ordinal": self.ordinal,
            "status": self.status,
            "entry_count": self.entry_count,
            "truncated": self.truncated,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        return payload


@dataclass(frozen=True)
class RoleStatus:
    """What the owner needs to see about one role, and nothing more.

    Deliberately carries no path. The Settings page shows the owner the root
    they typed because they typed it; every *other* surface gets this, and this
    cannot leak a filesystem layout.

    ``roots`` is additive. Every field that existed before P0.4 keeps its
    meaning for a role with one directory -- ``entry_count`` is still that
    directory's count, ``status`` is still its status, ``reason_code`` is still
    its refusal -- so nothing reading the old shape has to know lists exist.
    With several directories the totals aggregate and ``roots`` carries the
    detail.
    """

    role: str
    status: str
    entry_count: int = 0
    truncated: bool = False
    #: Stable error code when refused. Never a message containing a path.
    reason_code: str | None = None
    #: Per-directory detail, in the owner's order. Empty when unconfigured.
    roots: tuple[RootStatus, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": self.role,
            "status": self.status,
            "entry_count": self.entry_count,
            "truncated": self.truncated,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        payload["roots"] = [root.to_dict() for root in self.roots]
        return payload


def normalize_roots(raw: object) -> dict[str, tuple[str, ...]]:
    """Validate the shape of a ``model_roots`` mapping. No filesystem access.

    Returns role -> ORDERED path texts for the roles that are configured.
    Raises ``HeadlessError`` for a malformed mapping, so a typo in the config
    file is a named failure rather than a silently ignored key.

    **The list is variable-length by shape.** A role accepts any number of
    directories, in the owner's order, and both spellings are read:

    ```text
    "checkpoint": "C:/Models"          one root, the pre-P0.4 spelling
    "checkpoint": ["C:/A", "D:/B"]     ordered roots
    "checkpoint": []                   not configured
    ```

    ``MAX_ROOTS_PER_ROLE`` is an IMPLEMENTATION limit on how many this build
    will enumerate, not a property of the format. It carries its own refusal
    code so an owner who hits it is told which of the two they hit, and
    raising it later is a one-line change that no stored document has to be
    migrated for. A schema that fixed the length would have made that a
    migration.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HeadlessError(
            "HEADLESS_MODEL_ROOTS_MALFORMED",
            "model_roots must be an object mapping roles to directories.",
        )
    unknown = sorted(set(raw) - set(MODEL_ROLES))
    if unknown:
        raise HeadlessError(
            "HEADLESS_MODEL_ROOTS_UNKNOWN_ROLE",
            f"Unknown model role(s): {', '.join(unknown)}.",
        )
    roots: dict[str, tuple[str, ...]] = {}
    for role in MODEL_ROLES:
        entries = _role_entries(role, raw.get(role))
        if entries:
            roots[role] = entries
    return roots


def _role_entries(role: str, value: object) -> tuple[str, ...]:
    """One role's roots, in order, de-duplicated by text."""

    if value is None:
        return ()
    if isinstance(value, str):
        # The pre-P0.4 spelling, read as a one-element list rather than
        # migrated on disk -- so a file written by an older build keeps
        # working, and a file written by this one stays readable to someone
        # who has only ever seen the old shape.
        candidates: list[object] = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        raise HeadlessError(
            "HEADLESS_MODEL_ROOTS_MALFORMED",
            f"model_roots.{role} must be a directory or a list of directories.",
        )

    ordered: list[str] = []
    for entry in candidates:
        if entry is None:
            continue
        if not isinstance(entry, str):
            raise HeadlessError(
                "HEADLESS_MODEL_ROOTS_MALFORMED",
                f"model_roots.{role} must contain only directory paths.",
            )
        text = entry.strip()
        if not text:
            continue
        if text in ordered:
            # Textual duplicates only. Two spellings of ONE directory are
            # caught after resolution, where the filesystem can answer; here
            # there is nothing to compare but the strings.
            continue
        ordered.append(text)

    if len(ordered) > MAX_ROOTS_PER_ROLE:
        raise HeadlessError(
            ROOTS_TOO_MANY,
            f"A role accepts at most {MAX_ROOTS_PER_ROLE} directories.",
        )
    return tuple(ordered)


class ModelRootRegistry:
    """The three role catalogues, replaced atomically or not at all."""

    def __init__(self, *, workspace_root: os.PathLike[str] | str) -> None:
        self._workspace = Path(workspace_root)
        self._lock = RLock()
        self._catalogues: dict[str, ModelCatalogue] = {}
        self._configured: dict[str, str] = {}
        self._refusals: dict[str, str] = {}
        #: Architecture per checkpoint `model_id`, populated only by an actual
        #: header read. The Extension caches by absolute filename
        #: (`studio_api.py:2814`, written at `:4818`); a `model_id` already
        #: digests role, root and relative location, so it is the same identity
        #: without carrying a path across the boundary. Same discipline as the
        #: Extension's: never written for "unknown", never written for GGUF,
        #: never invalidated in-process.
        self._architectures: dict[str, str] = {}

    # -- configuration ----------------------------------------------------

    def _project_lora_roots(self) -> None:
        """Tell the engine where the owner's LoRAs are, whenever roots change.

        The engine scans `[cmd_opts.lora_dir, *cmd_opts.lora_dirs]`
        (`sd_forge_lora/networks.py:157`), and those come from a command line
        Studio does not have -- `backend_bootstrap` blanks `sys.argv` before
        the first Neo import so Neo's parsers cannot eat the launcher's
        arguments. So the value is PROJECTED, exactly as `runtime_options`
        projects the compute flags.

        HERE, rather than on the generation path or in the browse route,
        because this is the one place that knows the roots and is called by
        BOTH of them -- startup uses `configure_tolerantly`, a settings save
        uses `configure`. Projecting from the browse route alone would leave a
        hand-typed `<lora:...>` unresolvable until someone opened the browser.

        Never raises, and never imports Neo just to try: a Studio with no
        engine yet, or none at all, configures its roots exactly as before.
        """

        import sys

        shared = sys.modules.get("modules.shared")
        if shared is None:
            # NOT imported on purpose. Doing so here would drag the engine into
            # every construction of this registry, including the ones in tests
            # that only check directory validation.
            return
        options = getattr(shared, "cmd_opts", None)
        if options is None:
            return
        try:
            roots = self._configured.get("lora", ())
            options.lora_dirs = [str(root) for root in roots if str(root)]
        except Exception:  # noqa: BLE001 - a model root is not worth a crash
            return

    def configure(self, roots: dict[str, tuple[str, ...]]) -> dict[str, RoleStatus]:
        """Replace every role at once. All-or-nothing.

        Construction validates and enumerates every directory *before*
        anything is published, so a refusal on the last directory of the last
        role leaves the previous configuration exactly as it was.
        """
        built, _refused = self._build(roots, tolerant=False)

        with self._lock:
            self._catalogues = built
            self._configured = {
                r: _role_entries(r, v) for r, v in roots.items()
            }
            self._refusals = {}
        self._project_lora_roots()
        return self.describe()

    def configure_tolerantly(
        self, roots: dict[str, tuple[str, ...]]
    ) -> dict[str, RoleStatus]:
        """Apply what works and record why the rest did not.

        Used at **startup only**. A model directory that has been renamed since
        last launch must not stop Studio from starting -- the owner needs the
        app running in order to fix it in Settings. The privileged Settings
        write uses ``configure`` instead, because there the owner is watching
        and a partial success would be a lie.

        With ordered roots this is per DIRECTORY rather than per role: one dead
        directory out of three leaves the other two serving, and the role says
        ``partial`` instead of pretending it is whole or refusing the two that
        work.
        """
        built, refused = self._build(roots, tolerant=True)

        with self._lock:
            self._catalogues = built
            self._configured = {
                r: _role_entries(r, v) for r, v in roots.items()
            }
            self._refusals = refused
        self._project_lora_roots()
        return self.describe()

    def _build(self, roots, tolerant: bool):
        """Construct every catalogue, or raise. Publishes nothing."""

        built: dict[str, tuple[ModelCatalogue, ...]] = {}
        refused: dict[str, dict[int, str]] = {}
        for role, paths in roots.items():
            if role not in MODEL_ROLES:
                raise HeadlessError(
                    "HEADLESS_MODEL_ROOTS_UNKNOWN_ROLE",
                    f"Unknown model role: {role}.",
                )
            # Coerced, not assumed. A caller holding the pre-P0.4 shape passes
            # `{"checkpoint": "C:/Models"}`, and `len()` on that string is its
            # character count -- which sailed past the roots-per-role cap and
            # refused a single perfectly good directory for having a long
            # path. One coercion here means every in-process caller keeps
            # working and the cap counts directories.
            paths = _role_entries(role, paths)
            catalogues: list[ModelCatalogue] = []
            seen: set[str] = set()
            for ordinal, text in enumerate(paths):
                try:
                    catalogue = ModelCatalogue(
                        text,
                        workspace_root=self._workspace,
                        role=role,
                        allow_external=True,
                        # `recursive` defaults to False, and this is the LIVE
                        # construction -- so every owner-facing dropdown listed
                        # the top level of a model folder and nothing else. An
                        # organised library (SDXL/, SD15/, Flux/) showed up
                        # empty, with no message and no hint that a subfolder
                        # existed. Reparse points are still rejected outright
                        # in `_scan`, so this walks the owner's tree and not
                        # out of it.
                        recursive=True,
                    )
                    # Two spellings of ONE directory resolve to one root, and
                    # listing it twice would show every model twice under the
                    # SAME id -- ids are keyed on the resolved root, so the
                    # duplicate is indistinguishable rather than merely
                    # untidy. Dropped quietly: the owner asked for a directory
                    # that is already there.
                    key = str(catalogue.root_key)
                    if key in seen:
                        continue
                    catalogue.refresh()
                except HeadlessError as error:
                    if not tolerant:
                        raise
                    refused.setdefault(role, {})[ordinal] = error.code
                    continue
                seen.add(key)
                catalogues.append(catalogue)
            if catalogues:
                built[role] = tuple(catalogues)
        return built, refused

    # -- reads ------------------------------------------------------------

    def describe(self) -> dict[str, RoleStatus]:
        with self._lock:
            return {role: self._describe_role(role) for role in MODEL_ROLES}

    def _describe_role(self, role: str) -> RoleStatus:
        catalogues = self._catalogues.get(role, ())
        refusals = self._refusals.get(role, {})
        configured = self._configured.get(role, ())
        if not configured:
            return RoleStatus(role=role, status=ROOT_STATUS_NOT_CONFIGURED)

        details: list[RootStatus] = []
        served = iter(catalogues)
        total = 0
        truncated = False
        for ordinal in range(len(configured)):
            code = refusals.get(ordinal)
            if code is not None:
                details.append(
                    RootStatus(
                        ordinal=ordinal,
                        status=ROOT_STATUS_REFUSED,
                        reason_code=code,
                    )
                )
                continue
            catalogue = next(served, None)
            if catalogue is None:
                # A duplicate spelling, dropped during construction. Reported
                # ready with nothing of its own rather than refused: the
                # directory IS configured and IS being served, under the
                # ordinal that first named it.
                details.append(
                    RootStatus(ordinal=ordinal, status=ROOT_STATUS_READY)
                )
                continue
            snapshot = catalogue.snapshot()
            count = len(snapshot.entries) if snapshot is not None else 0
            cut = bool(snapshot.truncated) if snapshot is not None else False
            total += count
            truncated = truncated or cut
            details.append(
                RootStatus(
                    ordinal=ordinal,
                    status=ROOT_STATUS_READY,
                    entry_count=count,
                    truncated=cut,
                )
            )

        first_reason = next((d.reason_code for d in details if d.reason_code), None)
        ready = [d for d in details if d.status == ROOT_STATUS_READY]
        if not ready:
            # Every directory refused. The role-level reason is the FIRST one,
            # so a single-root role reports exactly what it always did.
            return RoleStatus(
                role=role,
                status=ROOT_STATUS_REFUSED,
                reason_code=first_reason,
                roots=tuple(details),
            )
        status = (
            ROOT_STATUS_READY if len(ready) == len(details) else ROOT_STATUS_PARTIAL
        )
        return RoleStatus(
            role=role,
            status=status,
            entry_count=total,
            truncated=truncated,
            reason_code=None if status == ROOT_STATUS_READY else first_reason,
            roots=tuple(details),
        )

    def configured_roots(self) -> dict[str, tuple[str, ...]]:
        """The paths as configured, in order. For the Settings page only."""
        with self._lock:
            return {r: tuple(v) for r, v in self._configured.items()}

    def catalogues(self, role: str) -> tuple[ModelCatalogue, ...]:
        """Every serving catalogue for a role, in the owner's order."""

        with self._lock:
            return self._catalogues.get(role, ())

    def snapshot(self, role: str) -> CatalogueSnapshot | None:
        """The FIRST serving catalogue's snapshot, or None.

        Kept because callers that only ever had one root still ask this. It
        answers about one directory, which is why `entries` exists separately
        and is what anything listing a role should use.
        """

        catalogues = self.catalogues(role)
        return catalogues[0].snapshot() if catalogues else None

    def refresh(self, role: str) -> CatalogueSnapshot | None:
        """Re-enumerate every directory in the role. Returns the first."""

        catalogues = self.catalogues(role)
        snapshots = [catalogue.refresh() for catalogue in catalogues]
        return snapshots[0] if snapshots else None

    def entries(self, role: str) -> tuple[ModelCandidate, ...]:
        """Every model in the role, roots in order, first spelling wins.

        Order is presentation, not identity. Two directories may hold files of
        the same NAME, and those are two different models with two different
        ids -- the digest is keyed on the resolved root -- so both are listed
        and neither shadows the other. Reordering the roots therefore changes
        the order of this list and nothing else: no id moves, and a remembered
        selection still resolves.
        """

        found: list[ModelCandidate] = []
        seen: set[str] = set()
        for catalogue in self.catalogues(role):
            snapshot = catalogue.snapshot()
            if snapshot is None:
                continue
            for entry in snapshot.entries:
                if entry.model_id in seen:
                    continue
                seen.add(entry.model_id)
                found.append(entry)
        return tuple(found)

    def resolve(self, role: str, model_id: str):  # type: ignore[no-untyped-def]
        """Resolve within one role, across its roots. A role with no root
        resolves nothing.

        Searched in order, but the search is ORDER-INDEPENDENT in the only way
        that matters: an id names exactly one root, because the root is a
        digest input. Reordering changes which directory is asked first and
        never which one answers.
        """

        if role not in MODEL_ROLES:
            raise HeadlessError(
                "HEADLESS_MODEL_ROOTS_UNKNOWN_ROLE",
                f"Unknown model role: {role}.",
            )
        catalogues = self.catalogues(role)
        if not catalogues:
            raise HeadlessError(
                "HEADLESS_MODEL_ROOT_NOT_CONFIGURED",
                "No directory is configured for that model type.",
            )
        last: HeadlessError | None = None
        for catalogue in catalogues:
            try:
                return catalogue.resolve(model_id)
            except HeadlessError as error:
                # Not-found in THIS directory is not an answer for the role;
                # keep asking. Any other refusal -- containment, availability,
                # a root that changed under us -- is about the directory that
                # owns the id, so it is remembered and raised if nothing
                # resolves.
                last = error
                continue
        raise last if last is not None else HeadlessError(
            "HEADLESS_MODEL_ROOT_NOT_CONFIGURED",
            "No directory is configured for that model type.",
        )

    def inspect_checkpoint(self, model_id: str) -> dict[str, Any]:
        """What one checkpoint needs, by its opaque id. Never raises.

        The Studio-shaped half of the Extension's `/studio/check_model_te`
        (`studio_api.py:4769-4822`). That route resolves a Forge checkpoint
        title through `sd_models.get_closet_checkpoint_match`; here the id is
        resolved through the role's own catalogue, which re-verifies
        containment and format on the way. The answer carries booleans and an
        architecture name -- never the path it read.
        """

        from .architecture import GGUF, NEUTRAL, inspect_checkpoint_file

        text = str(model_id or "").strip()
        if not text:
            return dict(NEUTRAL)
        try:
            candidate, source = self.resolve("checkpoint", text)
        except HeadlessError:
            # The Extension answers neutrally for a title it cannot match
            # (`studio_api.py:4784-4785`) rather than refusing. A dropdown that
            # errors when a model is merely unknown is worse than one that
            # shows a control the owner did not need.
            return dict(NEUTRAL)
        if str(getattr(candidate, "format", "")).lower() == "gguf":
            # Returned BEFORE the cache write: "unknown" is never remembered.
            return dict(GGUF)
        verdict = inspect_checkpoint_file(Path(source.resolved))
        architecture = str(verdict.get("arch") or "")
        if architecture and architecture != "unknown":
            with self._lock:
                self._architectures[text] = architecture
        return verdict

    def architecture_for(self, model_id: str) -> str:
        """The remembered architecture, or "" if nothing has read that header.

        Read by the catalogue projection so a browse does not open every file.
        The Extension is explicit about this too (`studio_api.py:5136-5141`):
        the browser shows what was learned, not what a bulk scan would cost.
        """

        with self._lock:
            return self._architectures.get(str(model_id or "").strip(), "")

    def build_payload_references(self, selection: dict[str, str]) -> dict[str, str]:
        """Turn a per-role ``model_id`` selection into the existing load input.

        This is the whole of the load integration. Each id is resolved and
        revalidated by its own role's catalogue -- so containment, availability,
        format support and the root-unchanged check all run again here -- and
        the result is the same ``payload_references`` mapping the certified
        warm-session loader already consumes. No second loader, no new Forge
        call site, and nothing bypasses the load guard.

        Absolute paths appear only in the returned mapping, which stays inside
        the headless boundary. ``ModelProfile.describe()`` does not project it.
        """
        references: dict[str, str] = {}
        for role, model_id in selection.items():
            requested = str(model_id or "").strip()
            if not requested:
                if role in OPTIONAL_PAYLOAD_ROLES:
                    # The checkpoint bundles this component, so there is no
                    # file to resolve. It is left OUT of the mapping rather
                    # than mapped to an empty path -- absence is what the
                    # loader is given, matching how the Extension builds
                    # `additional_modules` (`studio_api.py:5744, 5771-5775`).
                    continue
                raise HeadlessError(
                    "HEADLESS_MODEL_ID_REQUIRED",
                    f"A {role} selection is required.",
                )
            _, source = self.resolve(role, requested)
            references[role] = str(source.resolved)
        return references


def write_config_atomically(path: Path, document: dict[str, object]) -> None:
    """Replace a config file without ever leaving a half-written one.

    Temp file in the *same directory* so the final rename is same-filesystem and
    therefore atomic, flush plus fsync before the rename so the bytes are on
    disk and not merely in a buffer, and ``os.replace`` because it overwrites
    atomically on both POSIX and Windows.

    The temp file is removed if anything fails, so a crashed write leaves the
    previous configuration intact rather than a stray partial file next to it.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
