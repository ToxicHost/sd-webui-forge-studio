"""The one privileged operation in Studio: repointing the model roots.

Every other Studio route takes ids, numbers and prompt text. This one takes
**filesystem paths** from the browser, on purpose, because there is no other way
for an owner to say where their models live. That makes it the only route where
a cross-origin page could do real damage, so it carries controls the rest of the
surface does not need:

```text
POST only              a repoint is never a GET, so a link cannot trigger one
application/json only  a simple cross-origin form POST cannot set this
Origin present + match absent Origin is refused here, unlike elsewhere
Host validated         reused from the handler
CSRF token required    in a custom header, which a simple form POST cannot set
```

The custom header is doing more work than it looks. A form POST can carry a
body, but it cannot set a custom header, and setting one forces the browser into
a CORS preflight -- which Studio never answers with permissive headers, so the
real request is never sent. The token then covers the non-browser case.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from threading import RLock
from typing import Any

from forge_headless.contracts import HeadlessError
from forge_headless.model_library import (
    CORRECTIONS_KEY,
    LIBRARY_KEY,
    LibraryFolder,
    install_models_folder,
    normalize_library,
    storable_library,
)
from forge_headless.model_roots import (
    ALLOW_NETWORK_KEY,
    CONFIG_KEY,
    ModelRootRegistry,
    normalize_roots,
    write_config_atomically,
)

#: Custom request header carrying the process-start token.
CSRF_HEADER = "X-Studio-Settings-Token"

#: Longest path text accepted from the browser. Generous for a real directory,
#: far short of anything that could be used to exhaust memory through the route.
MAX_ROOT_LENGTH = 4096

#: Config key holding the dropdown choices to restore next launch.
SELECTION_KEY = "last_model_selection"

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


def _storable_roots(roots) -> dict[str, object]:
    """Narrow each role to the smallest shape that still says the same thing.

    ```text
    one directory     written as a STRING   -- the pre-P0.4 spelling
    several           written as a LIST
    none              the role is OMITTED   -- never written as []
    ```

    Narrowing is what keeps a downgrade survivable. An older build reads
    `model_roots` through a `normalize_roots` that accepts only strings and
    raises on anything else -- and at startup that refusal is fatal. So an
    owner who configures one directory here and then runs an older build finds
    a file it can still read, and an owner who configures several finds one it
    refuses to start on ONLY if they actually used the new capability.

    Omitting an emptied role rather than writing `[]` is the same rule at the
    other end: `[]` is exactly the value that would break that older build
    while meaning nothing more than "absent".
    """

    document: dict[str, object] = {}
    for role, paths in roots.items():
        values = tuple(paths)
        if not values:
            continue
        document[role] = values[0] if len(values) == 1 else list(values)
    return document


class SettingsError(HeadlessError):
    """A settings-specific refusal, carrying a stable code."""


class ModelRootSettings:
    """Owns the registry, the config file, and the per-process token."""

    def __init__(
        self,
        registry: ModelRootRegistry,
        *,
        config_path: Path | None = None,
        last_model_selection: dict[str, str] | None = None,
        studio_models_folder: Path | None = None,
    ) -> None:
        self._registry = registry
        self._config_path = Path(config_path) if config_path is not None else None
        #: ON2. Studio's own `models` folder (`app/models`), offered to an
        #: owner starting fresh. None where the host has none.
        self._studio_models = Path(studio_models_folder) if studio_models_folder else None
        self._lock = RLock()
        # Seeded from the launch config and updated on write, rather than
        # re-read from disk on every describe. Held here because this object
        # already owns the config file, its lock and its atomic writer -- a
        # second writer to the same file is how two pages end up overwriting
        # each other's keys.
        self._last_selection = dict(last_model_selection or {})
        #: ON1b. The owner's corrections from the Unrecognized list, absolute
        #: path -> kind, as saved under `model_library_corrections`.
        self._corrections: dict[str, str] = {}
        # Notified after a successful roots write. A directory handle IS
        # authority over a directory, and authority granted under one
        # configuration must not survive into the next -- otherwise
        # repointing the roots, or switching the browser off, leaves live
        # capability behind that nothing is watching.
        self._on_change: list = []
        # Fresh per process. Nothing persists it, so a restart invalidates every
        # token a page might still be holding.
        self._token = secrets.token_urlsafe(32)

    @property
    def registry(self) -> ModelRootRegistry:
        return self._registry

    @property
    def token(self) -> str:
        return self._token

    @property
    def persistable(self) -> bool:
        return self._config_path is not None

    def on_change(self, callback) -> None:
        """Register something to run after a successful roots write."""

        self._on_change.append(callback)

    def _notify_change(self) -> None:
        for callback in tuple(self._on_change):
            try:
                callback()
            except Exception:  # noqa: BLE001
                # An observer must never be able to fail a write that already
                # succeeded. The registry is reconfigured and the file is
                # saved; a listener that raises is its own problem.
                pass

    def token_matches(self, supplied: str | None) -> bool:
        if not supplied:
            return False
        return secrets.compare_digest(supplied, self._token)

    # -- reads ------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Status for the Settings page, including the configured paths.

        This is the one place a path is returned, and only to the page the owner
        typed it into. It is a same-origin GET; a cross-origin page cannot read
        the response because Studio sets no permissive CORS headers.
        """
        statuses = self._registry.describe()
        configured = self._registry.configured_roots()
        with self._lock:
            remembered = dict(self._last_selection)
        library = self._registry.configured_library()
        return {
            "roles": [statuses[role].to_dict() for role in sorted(statuses)],
            "roots": configured,
            # ON1b. The owner's library folders (paths: this page only) and,
            # by ordinal, what each one holds.
            LIBRARY_KEY: [folder.to_dict() for folder in library],
            "library_status": self._registry.describe_library(),
            "studio_models_folder": str(self._studio_models) if self._studio_models else "",
            "persistable": self.persistable,
            ALLOW_NETWORK_KEY: bool(getattr(self._registry, "allow_network", False)),
            # Three opaque catalogue ids, or fewer. The page pre-selects its
            # dropdowns from these. Nothing else may read them: the moment
            # something loads from this value it stops being a preference and
            # becomes `selected_profile_id` again.
            SELECTION_KEY: remembered,
        }

    # -- the privileged write ---------------------------------------------

    def apply(self, payload: Any) -> dict[str, Any]:
        """Validate, reconfigure atomically, then persist atomically.

        Order matters. Configuration is applied to the live registry *first*, so
        a root that cannot be enumerated never reaches the config file. If the
        write then fails, the running app is correct and the file is stale --
        which is recoverable. The reverse would persist a configuration that
        does not work.
        """
        if not isinstance(payload, dict):
            raise SettingsError(
                "STUDIO_SETTINGS_MALFORMED",
                "Expected a JSON object.",
            )
        raw_roots = payload.get(CONFIG_KEY, payload.get("roots"))
        library = None
        if LIBRARY_KEY in payload:
            # ON1b. The library list, validated like the roots; the role
            # folders (Studio's bundled detectors) stay as they are unless
            # the payload names them too.
            library = normalize_library(payload.get(LIBRARY_KEY))
            for ordinal, folder in enumerate(library):
                self._check_path(folder.path, f"Model folder {ordinal + 1}")
            if raw_roots is None:
                raw_roots = self._registry.configured_roots()
        if raw_roots is None:
            raise SettingsError(
                "STUDIO_SETTINGS_MALFORMED",
                "A model_roots object is required.",
            )
        roots = normalize_roots(raw_roots)
        for role, paths in roots.items():
            for ordinal, text in enumerate(paths):
                # Per ENTRY, not per role. A role now carries several
                # directories, and a length or null-byte problem belongs to
                # one of them; saying only which role would leave the owner
                # hunting through a list for the one that is wrong.
                where = f"The {role} directory" + (
                    f" at position {ordinal + 1}" if len(paths) > 1 else ""
                )
                self._check_path(text, where)

        # "Allow network model folders". Absent keeps the current value, so a
        # page that predates the checkbox cannot switch it off by omission.
        # Only a real boolean is accepted: this opens a path that touches
        # other machines, and "false" (a string) is truthy.
        allow_network = payload.get(ALLOW_NETWORK_KEY)
        if allow_network is not None and not isinstance(allow_network, bool):
            raise SettingsError(
                "STUDIO_SETTINGS_MALFORMED",
                f"{ALLOW_NETWORK_KEY} must be true or false.",
            )

        with self._lock:
            # All-or-nothing. A refusal on any root leaves the previous
            # configuration serving, and propagates its stable code. The
            # opt-in changes in the SAME transaction: a refused save leaves
            # it as it was.
            self._registry.configure(roots, library=library, allow_network=allow_network)
            if library is not None and CONFIG_KEY not in payload and "roots" not in payload:
                # A library save leaves the file's `model_roots` alone: it is
                # what an older build reads, and the roots served now are
                # Studio's own seeds, which are never written.
                persisted = self._persist_keys({
                    LIBRARY_KEY: storable_library(library),
                    ALLOW_NETWORK_KEY: bool(self._registry.allow_network),
                })
            else:
                persisted = self._persist(roots, self._registry.allow_network, library)
        # AFTER the lock and only on success. A refused write changed nothing,
        # so invalidating handles for it would cost the owner their place in a
        # directory tree for no reason.
        self._notify_change()

        result = self.describe()
        result["persisted"] = persisted
        return result

    def _check_path(self, text: str, where: str) -> None:
        if len(text) > MAX_ROOT_LENGTH:
            raise SettingsError("STUDIO_SETTINGS_ROOT_TOO_LONG", f"{where} path is too long.")
        if "\x00" in text:
            raise SettingsError("STUDIO_SETTINGS_ROOT_MALFORMED", f"{where} path contains a null byte.")

    # -- the library (ON1b) -------------------------------------------------

    def persist_library(self) -> bool:
        """Save the library as configured now -- the one-time conversion of
        the role folders, written by `launch.run`. Never raises."""

        try:
            with self._lock:
                return self._persist_keys(
                    {LIBRARY_KEY: storable_library(self._registry.configured_library())}
                )
        except HeadlessError:
            return False

    def load_corrections(self, corrections: Any) -> None:
        """The saved corrections, at startup, before the first scan."""

        self._corrections = {
            str(path): str(kind) for path, kind in (corrections or {}).items()
            if isinstance(path, str) and isinstance(kind, str)
        } if isinstance(corrections, dict) else {}
        self._registry.set_corrections(self._corrections)

    def unrecognized(self, payload: Any) -> dict[str, Any]:
        """The files in one library folder that no role took. Paths are
        relative to that folder; this page already knows the folder."""

        ordinal = self._ordinal(payload)
        root = self._registry.library_root(ordinal)
        corrected = []
        for path, kind in sorted(self._corrections.items()):
            try:
                relative = Path(path).relative_to(root).as_posix()
            except ValueError:
                continue
            corrected.append({"relative": relative, "kind": kind})
        return {"folder": ordinal, "files": self._registry.unrecognized(ordinal),
                "corrected": corrected}

    def correct(self, payload: Any) -> dict[str, Any]:
        """The owner says what one library file is ("" = let Studio decide)."""

        ordinal = self._ordinal(payload)
        relative = payload.get("relative") if isinstance(payload, dict) else None
        kind = payload.get("kind", "") if isinstance(payload, dict) else None
        if not isinstance(relative, str) or not relative.strip() or not isinstance(kind, str):
            raise SettingsError("STUDIO_SETTINGS_MALFORMED", "A file and a kind are required.")
        path = self._registry.library_file_path(ordinal, relative)
        with self._lock:
            corrections = dict(self._corrections)
            if kind:
                corrections[path] = kind
            else:
                corrections.pop(path, None)
            self._registry.set_corrections(corrections)
            self._corrections = corrections
            persisted = self._persist_keys({CORRECTIONS_KEY: dict(corrections)})
        self._notify_change()
        result = self.describe()
        result["persisted"] = persisted
        return result

    def inspect(self, payload: Any) -> dict[str, Any]:
        """Whether a folder the owner picked is an install root (ON2), and
        its models folder. Looks only inside that folder."""

        path = payload.get("path") if isinstance(payload, dict) else None
        if not isinstance(path, str) or not path.strip():
            raise SettingsError("STUDIO_SETTINGS_MALFORMED", "A folder path is required.")
        self._check_path(path, "The folder")
        found = install_models_folder(path.strip())
        if found is None:
            return {"install": None}
        kind, models = found
        return {"install": kind, "models": models}

    def use_studio_folder(self) -> dict[str, Any]:
        """Starting fresh (ON2): add Studio's own models folder to the library,
        with a `Stable-diffusion` folder inside for checkpoints -- Forge's
        layout, which the classifier's folder hint reads -- and say where
        that is so the page can open it. Studio's own folder, so making a
        subfolder there writes nothing of the owner's."""

        if self._studio_models is None:
            raise SettingsError("STUDIO_SETTINGS_NO_STUDIO_FOLDER", "This Studio has no models folder of its own.")
        checkpoints = self._studio_models / "Stable-diffusion"
        try:
            checkpoints.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise SettingsError("STUDIO_SETTINGS_NO_STUDIO_FOLDER", "Studio's models folder could not be prepared.") from None
        library = tuple(self._registry.configured_library())
        if not any(Path(f.path) == self._studio_models for f in library):
            library = library + (LibraryFolder(str(self._studio_models)),)
            with self._lock:
                self._registry.configure(self._registry.configured_roots(), library=library)
                persisted = self._persist_keys({LIBRARY_KEY: storable_library(library)})
            self._notify_change()
        else:
            persisted = True
        result = self.describe()
        result["persisted"] = persisted
        result["checkpoints"] = str(checkpoints)
        return result

    def folder_to_open(self, payload: Any) -> Path:
        """The library folder (by number) the page asked to open."""

        return self._registry.library_root(self._ordinal(payload))

    @staticmethod
    def _ordinal(payload: Any) -> int:
        value = payload.get("folder") if isinstance(payload, dict) else None
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SettingsError("STUDIO_SETTINGS_MALFORMED", "A model folder number is required.")
        return value

    # -- the unprivileged write --------------------------------------------

    def remember_selection(self, payload: Any) -> dict[str, Any]:
        """Record which models the dropdowns were left on. Loads nothing.

        This carries no path and triggers no load, so the worst a forged write
        could achieve is a wrong pre-selection at the next launch. It is still
        gated by the same token as the roots write, because it is still a
        write to the owner's configuration file, and there is no reason to
        hold two different answers to "may this page change the config".
        """

        if not isinstance(payload, dict):
            raise SettingsError(
                "STUDIO_SETTINGS_MALFORMED",
                "Expected a JSON object.",
            )
        raw = payload.get(SELECTION_KEY, payload.get("selection", payload))
        if not isinstance(raw, dict):
            raise SettingsError(
                "STUDIO_SETTINGS_MALFORMED",
                "A last_model_selection object is required.",
            )
        selection: dict[str, str] = {}
        for role in RESIDENT_MODEL_ROLES:
            value = raw.get(role)
            if value is None or value == "":
                continue
            if not isinstance(value, str):
                raise SettingsError(
                    "STUDIO_SETTINGS_MALFORMED",
                    f"The {role} selection must be a string.",
                )
            candidate = value.strip().lower()
            if len(candidate) != 32 or any(
                character not in "0123456789abcdef" for character in candidate
            ):
                raise SettingsError(
                    "STUDIO_SETTINGS_MALFORMED",
                    f"The {role} selection is not a catalogue id.",
                )
            selection[role] = candidate

        with self._lock:
            self._last_selection = selection
            persisted = self._persist_key(SELECTION_KEY, dict(selection))

        return {SELECTION_KEY: dict(selection), "persisted": persisted}

    def _persist(self, roots, allow_network: bool = False, library=None) -> bool:  # type: ignore[no-untyped-def]
        # One read-modify-write for both keys, so the roots and the switch
        # they were validated under can never be saved half each.
        values: dict[str, Any] = {
            CONFIG_KEY: _storable_roots(roots),
            ALLOW_NETWORK_KEY: bool(allow_network),
        }
        if library is not None:
            values[LIBRARY_KEY] = storable_library(library)
        return self._persist_keys(values)

    def _persist_key(self, key: str, value: Any) -> bool:
        return self._persist_keys({key: value})

    def _persist_keys(self, values: dict[str, Any]) -> bool:
        """Read-modify-write ONE key. Every other key is carried through.

        Including the retired ones. `profiles`, `selected_profile_id` and
        `autoload` do nothing now, but the file belongs to the owner, the keys
        are harmless where they sit, and silently rewriting a configuration to
        drop whatever a new version stopped reading is how a downgrade turns
        into data loss.
        """

        if self._config_path is None:
            return False
        import json

        try:
            existing = json.loads(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, UnicodeDecodeError, ValueError):
            # A config file that cannot be read is not silently replaced with a
            # document containing only the key being written -- that would
            # discard the owner's port, result_root and model roots.
            raise SettingsError(
                "STUDIO_SETTINGS_CONFIG_UNREADABLE",
                "The Studio configuration file could not be read, so the "
                "change was applied but not saved.",
            ) from None
        existing.update(values)
        try:
            write_config_atomically(self._config_path, existing)
        except OSError:
            raise SettingsError(
                "STUDIO_SETTINGS_CONFIG_UNWRITABLE",
                "The change was applied but could not be saved for next "
                "launch.",
            ) from None
        return True
