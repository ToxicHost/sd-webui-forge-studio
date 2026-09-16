"""Minimum alpha settings: identity and configuration, never payloads.

What persists is the *decision* a user made -- which backend kind, whether to
keep the session warm -- not the model itself and not any path into the private
model directory. Loading settings opens no model and initializes no device; it
reads one small JSON file and validates it.

`selected_profile_id` and `autoload` are still PARSED and still VALIDATED, and
they no longer do anything. They are retired configuration keys: Studio ignores
them while preserving them in the file, because the file belongs to the owner
and silently rewriting someone's config is worse than carrying two dead keys
(`launch.py`, `model_root_settings.py`). There is no owner-facing profile to
select, and generation owns model readiness, so nothing reads either value.

Their validation is deliberately kept too. A key that is tolerated should still
be refused when it is malformed -- tolerating a key is not the same as
accepting anything under it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import StructuredError, StudioError

SETTINGS_INVALID = "STUDIO_SETTINGS_INVALID"

MOCK_BACKEND = "mock"
HEADLESS_BACKEND = "headless"
_BACKEND_KINDS = (MOCK_BACKEND, HEADLESS_BACKEND)

SETTINGS_VERSION = 1


def _invalid(message: str, field_name: str | None = None) -> StudioError:
    return StudioError(
        StructuredError(code=SETTINGS_INVALID, message=message, field=field_name)
    )


@dataclass(frozen=True)
class StudioSettings:
    """The whole alpha configuration surface. Deliberately small."""

    selected_profile_id: str | None = None
    backend_kind: str = HEADLESS_BACKEND
    autoload: bool = False
    result_root: Path | None = None
    keep_session_warm: bool = True

    def __post_init__(self) -> None:
        if self.backend_kind not in _BACKEND_KINDS:
            raise _invalid("That backend kind is not available.", "backend_kind")
        if self.selected_profile_id is not None and not str(
            self.selected_profile_id
        ).strip():
            raise _invalid("The selected profile id is empty.", "selected_profile_id")
        if not isinstance(self.autoload, bool):
            raise _invalid("autoload must be a boolean.", "autoload")
        if not isinstance(self.keep_session_warm, bool):
            raise _invalid("keep_session_warm must be a boolean.", "keep_session_warm")
        if self.result_root is not None and not isinstance(self.result_root, Path):
            object.__setattr__(self, "result_root", Path(str(self.result_root)))

    # -- projections -------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """The public view. Reports **whether** a result root is configured,
        never where it is: a result root is a filesystem path, and no response
        has any business carrying one."""

        return {
            "selected_profile_id": self.selected_profile_id,
            "backend_kind": self.backend_kind,
            "autoload": self.autoload,
            "keep_session_warm": self.keep_session_warm,
            "result_root_configured": self.result_root is not None,
            "version": SETTINGS_VERSION,
        }

    def to_storage(self) -> dict[str, Any]:
        """What is written to disk. The result root is private configuration and
        stays in the file, never in `describe()`."""

        payload: dict[str, Any] = {
            "version": SETTINGS_VERSION,
            "selected_profile_id": self.selected_profile_id,
            "backend_kind": self.backend_kind,
            "autoload": self.autoload,
            "keep_session_warm": self.keep_session_warm,
        }
        if self.result_root is not None:
            payload["result_root"] = str(self.result_root)
        return payload

    # `with_selection` is retired: the one mutator that existed to write a
    # remembered PROFILE id, with no caller anywhere once `_persist_selection`
    # went. The field it set is deliberately kept -- see `selected_profile_id`
    # above. It is one of the retired configuration keys Studio ignores while
    # preserving them in the file, because the file belongs to the owner
    # (`launch.py`, `model_root_settings.py`). Reading and tolerating that key
    # is not the same as offering a way to write it.

    # -- construction ------------------------------------------------------

    @classmethod
    def from_storage(cls, payload: Mapping[str, Any] | None) -> "StudioSettings":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise _invalid("Settings must be a JSON object.")
        version = payload.get("version", SETTINGS_VERSION)
        if not isinstance(version, int) or version > SETTINGS_VERSION:
            raise _invalid("These settings come from a newer Studio.", "version")
        root = payload.get("result_root")
        return cls(
            selected_profile_id=payload.get("selected_profile_id"),
            backend_kind=str(payload.get("backend_kind", HEADLESS_BACKEND)),
            autoload=bool(payload.get("autoload", False)),
            result_root=Path(str(root)) if root else None,
            keep_session_warm=bool(payload.get("keep_session_warm", True)),
        )


class SettingsService:
    """Read and write one settings file. Never loads a model.

    A missing file is not an error -- it is a first run, and it yields defaults.
    A malformed file **is** an error: silently falling back to defaults would
    discard a user's configuration without telling them.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._settings = StudioSettings()
        self._loaded = False

    @property
    def path_configured(self) -> bool:
        return self._path is not None

    @property
    def current(self) -> StudioSettings:
        return self._settings

    def load(self) -> StudioSettings:
        if self._path is None or not self._path.is_file():
            self._loaded = True
            return self._settings
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _invalid(
                f"The settings file could not be read ({type(exc).__name__})."
            ) from None
        self._settings = StudioSettings.from_storage(raw)
        self._loaded = True
        return self._settings

    def save(self, settings: StudioSettings | None = None) -> StudioSettings:
        if settings is not None:
            self._settings = settings
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._settings.to_storage(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        return self._settings

    def describe(self) -> dict[str, Any]:
        described = self._settings.describe()
        described["persisted"] = self._path is not None
        described["loaded"] = self._loaded
        return described


__all__ = (
    "HEADLESS_BACKEND",
    "MOCK_BACKEND",
    "SETTINGS_INVALID",
    "SETTINGS_VERSION",
    "SettingsService",
    "StudioSettings",
)
