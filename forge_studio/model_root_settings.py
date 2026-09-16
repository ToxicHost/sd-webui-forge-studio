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
from forge_headless.model_roots import (
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
    ) -> None:
        self._registry = registry
        self._config_path = Path(config_path) if config_path is not None else None
        self._lock = RLock()
        # Seeded from the launch config and updated on write, rather than
        # re-read from disk on every describe. Held here because this object
        # already owns the config file, its lock and its atomic writer -- a
        # second writer to the same file is how two pages end up overwriting
        # each other's keys.
        self._last_selection = dict(last_model_selection or {})
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
        return {
            "roles": [statuses[role].to_dict() for role in sorted(statuses)],
            "roots": configured,
            "persistable": self.persistable,
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
                if len(text) > MAX_ROOT_LENGTH:
                    raise SettingsError(
                        "STUDIO_SETTINGS_ROOT_TOO_LONG",
                        f"{where} path is too long.",
                    )
                if "\x00" in text:
                    raise SettingsError(
                        "STUDIO_SETTINGS_ROOT_MALFORMED",
                        f"{where} path contains a null byte.",
                    )

        with self._lock:
            # All-or-nothing. A refusal on any root leaves the previous
            # configuration serving, and propagates its stable code.
            self._registry.configure(roots)
            persisted = self._persist(roots)
        # AFTER the lock and only on success. A refused write changed nothing,
        # so invalidating handles for it would cost the owner their place in a
        # directory tree for no reason.
        self._notify_change()

        result = self.describe()
        result["persisted"] = persisted
        return result

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

    def _persist(self, roots) -> bool:
        return self._persist_key(CONFIG_KEY, _storable_roots(roots))

    def _persist_key(self, key: str, value: Any) -> bool:
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
        existing[key] = value
        try:
            write_config_atomically(self._config_path, existing)
        except OSError:
            raise SettingsError(
                "STUDIO_SETTINGS_CONFIG_UNWRITABLE",
                "The change was applied but could not be saved for next "
                "launch.",
            ) from None
        return True
