"""Studio's durable documents: the owner's preferences, and their defaults.

Owner decision D1, stage 2. Stage 1 resolved WHERE Studio's state belongs
(`state_root.py`) and wired that answer into `launch.py`. Nothing read or wrote
it. This module is the first thing that does.

The defect it closes is not subtle. Preferences lived in a process-local dict
on the frontend adapter, so custom shortcuts, per-checkpoint text-encoder and
VAE memory, the session limit, panel layout, folder settings and the saved
generation defaults were accepted, echoed back as saved, and then discarded
when Studio exited. The page was told its POST succeeded because it had.

Two documents, two files, on purpose:

```text
<state root>/preferences.json   application preferences   allow-listed
<state root>/defaults.json      saved generation defaults arbitrary shape
```

They are separate because `?reset` -- the emergency flow that DELETEs
`/studio/prefs` -- must not take the owner's saved defaults with it. Folding
defaults in as a fourteenth preference key would make one button destroy two
unrelated things, which is the reference implementation's reasoning too
(`user_prefs.json` and `user_defaults.json` are separate files there).

What this module deliberately does NOT do:

* **It does not migrate anything.** Legacy `localStorage` values are stage 3's
  problem, and half-doing it is worse than not starting: `prefs.js` sets its
  `studio-prefs-migrated` marker after the first successful POST, so existing
  installs already carry that marker against a store that never persisted. A
  migration that trusts the marker finds nothing and concludes there was
  nothing to find.
* **It creates nothing until the first write.** No directory, no empty file.
  That is a constraint stage 3 depends on rather than tidiness: "the
  preferences file does not exist" is the only signal left that distinguishes
  a never-migrated install from one that migrated to nothing, and a store that
  touched the disk at startup would erase it.
* **It does not lock across processes.** The `RLock` covers the whole
  read-merge-write transaction within one Studio, which is what makes two
  browser tabs posting different keys safe. Two Studio processes sharing one
  state root can still lose an update, exactly as the reference can. Said here
  rather than implied by an atomic write, which prevents a torn FILE and not a
  lost UPDATE.

Stdlib only, and no import of anything Studio-owned: a storage document has no
business knowing about a presentation, a backend, or HTTP. Refusals are raised
as this module's own exceptions and the adapter maps them onto status codes.
"""

from __future__ import annotations

import json
import re
import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from threading import RLock

logger = logging.getLogger("studio.preferences")

#: The two documents, named by D1's declared state layout.
PREFERENCES_FILENAME = "preferences.json"
DEFAULTS_FILENAME = "defaults.json"
WILDCARDS_FILENAME = "wildcards.json"
SESSION_FILENAME = "last-session.json"

#: The envelope written into `last-session.json`. Named and versioned so a
#: snapshot from a future Studio is REFUSED rather than half-interpreted --
#: applying six of ten fields and silently dropping the rest is worse than
#: falling back to Saved Defaults, because the owner cannot tell it happened.
SESSION_SCHEMA = "studio-last-session"
SESSION_SCHEMA_VERSION = 1

#: Every key the envelope may carry. `settings` holds the parameter map; the
#: rest is identity and bookkeeping.
SESSION_KEYS = frozenset({
    "schema", "schema_version", "session_revision", "updated_at",
    "document_id", "canvas_revision", "settings",
})

#: Every preference key the frontend is allowed to store.
#:
#: An allow-list rather than a free-form bag, because this file is written from
#: a browser request: without it, any caller reaching the same-origin API could
#: grow an unbounded document on the owner's disk under keys nothing reads.
#:
#: These are exactly the keys the canonical frontend actually uses -- not a
#: superset chosen defensively. `test_preferences.py` derives the same set by
#: scanning the frontend for `Prefs.get`/`Prefs.set` and asserts the two match,
#: so a key added to the page without being added here fails the suite instead
#: of failing silently at runtime.
PREFERENCE_KEYS = frozenset({
    "auto_unload",
    "component_memory",
    "education",
    "gal_send_prompt_version",
    "gallery_folder",
    "gpu_tile_compositing",
    "layout_preset",
    "panel_ui",
    "remember_session",
    "save_dir",
    "save_tree",
    "session_limit",
    "shortcuts",
    "vram_weights",
})

#: Ceiling for ONE serialized document.
#:
#: The reference caps the request BODY at this size. Capping the stored
#: document instead is a deliberate translation, not a copy: a per-request cap
#: lets a document grow without limit across many small posts, and the thing
#: worth bounding is what ends up on the owner's disk. A write that would cross
#: the ceiling is refused whole, so the document can never be over it.
MAX_DOCUMENT_BYTES = 256 * 1024


#: What each preference key is allowed to hold.
#:
#: `_refuse_unknown` checks key NAMES, which stops a write inventing a key but
#: not a write giving a real key the wrong thing. The browser converts values
#: on its way out -- `prefs.js` has a per-key converter for every legacy key --
#: but the browser is not the only caller and is not trusted. Without this, a
#: recognised key carries any JSON at all into durable state, and the next
#: reader is the one that breaks.
#:
#: Types are taken from what the product actually writes: the `LEGACY_MAP`
#: converters in `prefs.js` for the migrated keys, and the `Prefs.set` call
#: sites for the rest. Enumerations are only pinned where the source pins them.
PREFERENCE_SHAPES: dict[str, Any] = {
    "auto_unload": dict,
    "component_memory": dict,
    "education": dict,
    "gal_send_prompt_version": ("resolved", "raw"),
    "gallery_folder": str,
    "gpu_tile_compositing": bool,
    "layout_preset": str,
    "panel_ui": dict,
    "remember_session": bool,
    "save_dir": str,
    "save_tree": ("neo", "studio"),
    "session_limit": (int, float),
    "shortcuts": dict,
    "vram_weights": (int, float),
}


class PreferenceError(Exception):
    """A stored document could not be read, written, or accepted."""


class UnknownPreferenceKeys(PreferenceError):
    """A write named keys that are not part of the preference contract."""

    def __init__(self, keys: Iterable[str]) -> None:
        self.keys = tuple(sorted(str(key) for key in keys))
        super().__init__(
            "These preference keys are not recognised: "
            + ", ".join(self.keys)
        )


class MalformedPreferenceValues(PreferenceError):
    """A write named recognised keys and gave one of them the wrong shape."""

    def __init__(self, offences: Iterable[str]) -> None:
        self.offences = tuple(sorted(str(item) for item in offences))
        super().__init__(
            "These preference values have the wrong shape: "
            + ", ".join(self.offences)
        )


class MalformedDocument(PreferenceError):
    """A write was not a JSON object, or held something JSON cannot carry."""


class DocumentTooLarge(PreferenceError):
    """A write would push the stored document past `MAX_DOCUMENT_BYTES`."""


class DocumentUnreadable(PreferenceError):
    """The document exists and could not be read.

    Distinct from a CORRUPT document on purpose. Unreadable bytes are a
    transient or permission fault; reporting them as an empty document would
    tell the page the owner has no preferences, and the next write would then
    replace everything they had with the one key that write carried. A refusal
    keeps the file intact and lets `prefs.js` fall back for that boot.
    """


class SessionSchemaUnsupported(PreferenceError):
    """The stored snapshot is not a schema this Studio understands.

    Raised on read and turned into a fallback rather than an error page: a
    session Studio cannot interpret must never stop it launching.
    """


class SessionRevisionStale(PreferenceError):
    """A browser tried to write over a newer snapshot.

    Two tabs, or one tab that slept while another wrote, would otherwise let
    the older view win by arriving last. Named refusal rather than a merge:
    the handoff permits refusal for Alpha 0.1 and an unreviewed general-purpose
    merge is the larger risk.
    """


class SessionIdentityNotDurable(PreferenceError):
    """Something that dies with the process was offered as durable identity.

    Asset and result handles live in a bounded in-memory registry. Persisting
    one produces a snapshot that looks restorable and resolves to nothing on
    the next launch, which is worse than not restoring at all.
    """


class DocumentNotWritten(PreferenceError):
    """The document could not be written or removed."""


class _Document:
    """One JSON object, held either on disk or in this process only.

    `state_root=None` means memory-only, which is what a host without a
    resolved state root gets -- the mock demo server, and every existing test
    that constructs a bare adapter. It is the SAME class with the same
    allow-list, size ceiling and refusals, so a mock host and a real host
    cannot disagree about what the contract accepts. Two implementations that
    drift is how this codebase acquired a frontend whose backend was missing.
    """

    #: Set by the two concrete stores below.
    FILENAME: str = ""
    ALLOWED_KEYS: frozenset[str] | None = None

    def __init__(self, state_root: str | Path | None = None) -> None:
        self._path: Path | None = (
            None if state_root is None else Path(state_root) / self.FILENAME
        )
        self._memory: dict[str, Any] | None = {} if self._path is None else None
        self._lock = RLock()

    @property
    def path(self) -> Path | None:
        """Where this document is kept, or None when it is memory-only."""

        return self._path

    @property
    def durable(self) -> bool:
        """Whether this document survives the process that wrote it."""

        return self._path is not None

    # -- reads ------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """The stored document. `{}` when nothing has been written yet."""

        with self._lock:
            return self._read_unlocked()

    # -- writes -----------------------------------------------------------

    def merge(self, posted: Any) -> dict[str, Any]:
        """Apply `posted` over the stored document and return the result.

        A shallow merge by top-level key: a posted key REPLACES that key
        entirely and unposted keys are left alone. That is the contract the
        frontend is written against -- it owns complete objects like
        `component_memory` and `shortcuts` and posts them whole, so a deep
        merge would resurrect entries the owner had just deleted.
        """

        incoming = _object(posted)
        self._refuse_unknown(incoming)
        self._refuse_malformed(incoming)
        with self._lock:
            document = self._read_unlocked()
            if not incoming:
                # A merge of nothing changes nothing, so it must not WRITE
                # anything. Without this, an empty POST creates an empty
                # preferences.json -- and the existence of that file is the
                # signal that separates "never written" from "written and
                # empty". A no-op that quietly destroys a signal is worse than
                # a no-op.
                return document
            document.update(incoming)
            return self._write_unlocked(document)

    def replace(self, document: Any) -> dict[str, Any]:
        """Store `document` in place of whatever was there."""

        incoming = _object(document)
        self._refuse_unknown(incoming)
        self._refuse_malformed(incoming)
        with self._lock:
            return self._write_unlocked(incoming)

    def clear(self) -> None:
        """Remove the document entirely.

        The file is REMOVED rather than rewritten as `{}`. An absent file is
        the "nothing has ever been written here" signal stage 3 reads, and
        leaving an empty one behind would turn one state into two that a reader
        has to tell apart.
        """

        with self._lock:
            if self._memory is not None:
                self._memory = {}
                return
            assert self._path is not None
            try:
                self._path.unlink(missing_ok=True)
            except OSError as error:
                raise DocumentNotWritten(
                    f"{self._path.name} could not be removed: "
                    f"{type(error).__name__}"
                ) from error

    # -- internals --------------------------------------------------------

    def _refuse_malformed(self, incoming: Mapping[str, Any]) -> None:
        """Refuse a recognised key carrying the wrong shape. Whole, not partly.

        Same policy as `_refuse_unknown`: accepting the well-formed half of a
        write and dropping the rest answers 200 over a partial save.
        """
        offences = []
        for key, value in incoming.items():
            shape = PREFERENCE_SHAPES.get(key)
            if shape is None:
                continue
            # "JSON cannot carry this at all" is a different and older
            # refusal, and it owns its case. A set reaching here must still
            # come back as MalformedDocument rather than as a shape
            # complaint, so anything that is not a JSON-native value falls
            # through to the encoder.
            if not isinstance(value, (dict, list, str, bool, int, float, type(None))):
                continue
            if isinstance(shape, tuple) and shape and isinstance(shape[0], str):
                if value not in shape:
                    offences.append(
                        f"{key} must be one of {', '.join(shape)}")
                continue
            # `bool` is a subclass of `int`, so a numeric key would silently
            # accept True without this.
            if shape in ((int, float),) and isinstance(value, bool):
                offences.append(f"{key} must be a number")
                continue
            if not isinstance(value, shape):
                names = (shape.__name__ if isinstance(shape, type)
                         else " or ".join(t.__name__ for t in shape))
                offences.append(f"{key} must be {names}")
        if offences:
            raise MalformedPreferenceValues(offences)

    def _refuse_unknown(self, incoming: Mapping[str, Any]) -> None:
        if self.ALLOWED_KEYS is None:
            return
        unknown = set(incoming) - self.ALLOWED_KEYS
        if unknown:
            # Refused WHOLE. Accepting the recognised half of a write and
            # dropping the rest would answer 200 over a partial save, which is
            # the shape of defect this milestone exists to remove.
            raise UnknownPreferenceKeys(unknown)

    def _read_unlocked(self) -> dict[str, Any]:
        if self._memory is not None:
            return json.loads(json.dumps(self._memory))
        assert self._path is not None
        if not self._path.is_file():
            return {}
        try:
            raw = self._path.read_bytes()
        except OSError as error:
            raise DocumentUnreadable(
                f"{self._path.name} could not be read: {type(error).__name__}"
            ) from error
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        if not isinstance(document, dict):
            # Corrupt, not unreadable: the bytes arrived and are not a JSON
            # object. There is no way forward except to start again, so the
            # old bytes are moved aside FIRST -- otherwise the next write
            # replaces the only copy of something a person might still want.
            self._preserve_unreadable()
            return {}
        return document

    def _preserve_unreadable(self) -> None:
        assert self._path is not None
        spoiled = self._path.with_name(self._path.name + ".unreadable")
        try:
            self._path.replace(spoiled)
        except OSError:
            logger.warning(
                "%s is not a JSON object and could not be set aside; it will "
                "be replaced by the next write.", self._path.name
            )
            return
        logger.warning(
            "%s was not a JSON object. It has been kept as %s and Studio is "
            "starting from empty %s.",
            self._path.name, spoiled.name, self._path.stem,
        )

    def _write_unlocked(self, document: Mapping[str, Any]) -> dict[str, Any]:
        payload = _encode(document)
        if self._memory is not None:
            self._memory = json.loads(payload.decode("utf-8"))
            return json.loads(payload.decode("utf-8"))
        assert self._path is not None
        # The state root is created HERE, on the first write, and nowhere
        # earlier. See the module docstring: an empty directory made at
        # startup destroys the signal stage 3 needs.
        temporary = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(payload)
            # Atomic on POSIX and on Windows: a crash mid-write leaves the old
            # file whole or the new one whole, never half of either. The
            # temporary name carries this process's id so two Studios sharing
            # a state root cannot write the same scratch file.
            temporary.replace(self._path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DocumentNotWritten(
                f"{self._path.name} could not be written: "
                f"{type(error).__name__}"
            ) from error
        return json.loads(payload.decode("utf-8"))


class PreferenceStore(_Document):
    """The owner's application preferences. Allow-listed, durable."""

    FILENAME = PREFERENCES_FILENAME
    ALLOWED_KEYS = PREFERENCE_KEYS


class DefaultsStore(_Document):
    """The owner's saved generation defaults.

    No allow-list: this document holds whatever generation parameters the page
    chose to save, and enumerating them here would mean a new slider silently
    stops being saveable. The size ceiling still applies, which is the bound
    that matters for something written from a browser request.
    """

    FILENAME = DEFAULTS_FILENAME
    ALLOWED_KEYS = None


class WildcardSettings(_Document):
    """Where the owner's wildcard folder and expansion toggle live.

    Its OWN document, not `preferences.json`. That one is defined as what the
    frontend stores through `/studio/prefs`, and a test derives its allow-list
    by reading the frontend source -- so a key the page never posts does not
    belong in it, however convenient the file would be. Adding two there broke
    that test immediately, which is the check working.

    Same state root, same atomic write, same size ceiling.
    """

    FILENAME = WILDCARDS_FILENAME
    ALLOWED_KEYS = frozenset({"wildcard_folder",
                              "studio_dynamic_prompts_enabled"})


class LastSessionStore(_Document):
    """The owner's most recent working state. Server-owned, durable. AR4.3.

    WHY THIS IS NOT `localStorage`, which is where it started and where the
    Extension still keeps it. The Extension runs inside Forge's Gradio app on a
    FIXED port, so an origin-scoped store persists across restarts and its
    design is sound. Studio chooses an EPHEMERAL port, so the same design hands
    every launch a new origin and an empty store: the feature appears broken
    while working perfectly, and what it wrote sits at an address nothing will
    visit again. Owner decision, 2026-08-19: the state moves server-side rather
    than the port being frozen to prop it up.

    Saved Defaults were already here, which is exactly why they survived the
    restart that lost the session. That contrast is the whole argument.

    SEPARATE FROM `defaults.json`, deliberately. Defaults are the baseline the
    owner chose; this is the working state they happened to leave behind.
    Merging them would mean an afternoon of experiments quietly rewriting the
    baseline -- and the owner could never get back to it.
    """

    FILENAME = SESSION_FILENAME
    ALLOWED_KEYS = SESSION_KEYS

    def read_snapshot(self) -> dict[str, Any]:
        """The stored session, or `{}` when there is nothing usable.

        Every failure returns `{}` rather than raising. A snapshot that cannot
        be read is a reason to fall back to Saved Defaults, never a reason to
        fail startup -- and `_Document.read` has already moved unparseable
        bytes aside as `.unreadable` by the time this sees them.
        """

        document = self.read()
        if not document:
            return {}
        if document.get("schema") != SESSION_SCHEMA:
            logger.warning(
                "%s is not a Studio session document; ignoring it.",
                self.FILENAME)
            return {}
        version = document.get("schema_version")
        if not isinstance(version, int) or version > SESSION_SCHEMA_VERSION:
            logger.warning(
                "%s is schema version %r and this Studio understands up to %d; "
                "starting from saved defaults.",
                self.FILENAME, version, SESSION_SCHEMA_VERSION)
            return {}
        if not isinstance(document.get("settings"), dict):
            logger.warning(
                "%s carries no settings object; ignoring it.", self.FILENAME)
            return {}
        return document

    def write_snapshot(
        self,
        settings: Any,
        *,
        base_revision: Any = None,
        document_id: Any = None,
        canvas_revision: Any = None,
    ) -> dict[str, Any]:
        """Replace the snapshot, refusing a stale write by name."""

        if not isinstance(settings, Mapping):
            raise MalformedDocument("settings must be a JSON object.")
        _refuse_ephemeral_identity(document_id)

        with self._lock:
            current = self.read_snapshot()
            held = current.get("session_revision")
            held = held if isinstance(held, int) else 0
            if base_revision is not None:
                if isinstance(base_revision, bool) or not isinstance(
                    base_revision, int
                ):
                    raise MalformedDocument(
                        "base_revision must be an integer.")
                if base_revision != held:
                    raise SessionRevisionStale(
                        f"This view is based on session revision "
                        f"{base_revision}; the stored session is at {held}."
                    )
            document = {
                "schema": SESSION_SCHEMA,
                "schema_version": SESSION_SCHEMA_VERSION,
                "session_revision": held + 1,
                "updated_at": _utc_now(),
                "settings": dict(settings),
            }
            if document_id is not None:
                document["document_id"] = str(document_id)
            if canvas_revision is not None:
                if isinstance(canvas_revision, bool) or not isinstance(
                    canvas_revision, int
                ):
                    raise MalformedDocument(
                        "canvas_revision must be an integer.")
                document["canvas_revision"] = canvas_revision
            return self._write_unlocked(document)


#: A handle from the in-memory asset or result registry. Both die with the
#: process, so neither can be durable identity.
_EPHEMERAL_HANDLE = re.compile(r"\A(?:studio-asset|studio-result)/", re.I)


def _refuse_ephemeral_identity(document_id: Any) -> None:
    if document_id is None:
        return
    text = str(document_id)
    if _EPHEMERAL_HANDLE.match(text) or "/" in text or "\\" in text:
        raise SessionIdentityNotDurable(
            "A session may only reference a durable document identity, not a "
            "registry handle or a path."
        )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MalformedDocument("A stored document must be a JSON object.")
    return {str(key): item for key, item in value.items()}


def _encode(document: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(dict(document), indent=2, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise MalformedDocument(
            f"That value cannot be stored as JSON: {type(error).__name__}"
        ) from error
    payload = text.encode("utf-8")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f"Stored preferences are limited to {MAX_DOCUMENT_BYTES} bytes; "
            f"that write would make them {len(payload)}."
        )
    return payload
