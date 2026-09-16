"""Reload bookkeeping for a directly loaded Tier-0 session.

`modules.processing.process_images_inner` calls `sd_models.forge_model_reload()`
itself, at `modules/processing.py:945`, inside the `n_iter` batch loop:

```python
if not getattr(p, "txt2img_upscale", False) or p.hr_checkpoint_name is None:
    sd_models.forge_model_reload()  # model can be changed for example by refiner, hiresfix
```

For plain txt2img that guard is true, so the call is effectively unconditional.
Entering at `process_images_inner` avoids only the *outer* wrapper's call at
`:785`; it does not avoid reload. Every Tier-0 document claimed otherwise until
live attempt 03 proved it.

The native fast path
--------------------
`forge_model_reload` (`modules/sd_models.py:322`) opens with::

    current_hash = str(model_data.forge_loading_parameters)
    if model_data.forge_hash == current_hash:
        return model_data.sd_model, False

The catalogue lookup that raised in attempt 03 --
`model_data.forge_loading_parameters["checkpoint_info"]` at `:349` -- sits
*after* that return. So a direct-loaded session does not need a catalogue entry;
it needs its own bookkeeping to agree with itself.

This module installs that agreement. Nothing in `modules/` is modified: the
reload call stays, unwrapped and unpatched, and satisfies Forge's own condition
truthfully.

What is installed, and why this shape
-------------------------------------
`forge_loading_parameters` is a plain `dict` in production (`{}` at
`SdModelData.__init__`, a dict of checkpoint/module/dtype entries from
`modules_forge/main_entry.py:128`). The comparison is `str()` of that dict
against `forge_hash`, so any stable mapping works.

The installed value describes the direct-load session and nothing else:

* no absolute path and no model filename -- this value is read back into a cache
  key at `modules/processing.py:425`, so anything in it can surface in logs;
* no cryptographic hash -- `forge_hash` here is Forge's own `str(dict)` token,
  not a digest of a payload, and nothing pretends otherwise;
* no `checkpoint_info` key -- it does not impersonate a catalogue checkpoint,
  and if the fast path were ever missed the absence would raise loudly rather
  than silently load something wrong;
* the engine's object id, so the token changes if a different session is ever
  published, and stays stable for the life of this one.

No model file is read, no directory enumerated, no catalogue mutated.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Marker key. Present only for a Studio direct-load session, so a reader can
#: tell this bookkeeping from Neo's catalogue-derived parameters at a glance.
DIRECT_LOAD_KEY = "studio_direct_load"

_UNSET = object()


@dataclass(frozen=True)
class DirectLoadReloadState:
    """What was installed, and whether the native fast path was satisfied."""

    installed: bool
    source: str
    installed_forge_hash: str
    engine_matches: bool
    fast_path_verified: bool
    returned_existing_engine: bool
    reported_reloaded_false: bool
    restored: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "reload_bookkeeping_installed": self.installed,
            "reload_bookkeeping_source": self.source,
            "reload_installed_forge_hash": self.installed_forge_hash,
            "reload_engine_matches": self.engine_matches,
            "reload_fast_path_verified": self.fast_path_verified,
            "reload_returned_existing_engine": self.returned_existing_engine,
            "reload_reported_reloaded_false": self.reported_reloaded_false,
            "reload_bookkeeping_restored": self.restored,
        }


def direct_load_parameters(engine: object, *, runtime_label: str) -> dict[str, object]:
    """The loading-parameters mapping for an already-loaded direct session.

    Deterministic within the worker, stable while the engine lives, and
    different for a different engine.
    """

    return {
        DIRECT_LOAD_KEY: True,
        "session": runtime_label,
        "engine_class": type(engine).__name__,
        "roles": ["checkpoint", "text_encoder", "vae"],
        "engine_object_id": id(engine),
    }


class DirectLoadReloadBookkeeping:
    """Make `forge_model_reload()` a truthful no-op for a direct session.

    Install after the engine is published through `shared.sd_model`, so
    `model_data.sd_model` is already the engine the fast path will return.
    Restore in a `finally`: process exit is not restoration.
    """

    def __init__(self) -> None:
        self._previous_parameters: object = _UNSET
        self._previous_hash: object = _UNSET
        self._installed = False
        self._installed_hash = ""
        self._engine_matches = False
        self._fast_path_verified = False
        self._returned_existing = False
        self._reported_false = False
        self._restored = False

    # -- install ----------------------------------------------------------

    def install(self, engine: object, *, runtime_label: str) -> None:
        from modules import sd_models

        model_data = sd_models.model_data
        self._previous_parameters = getattr(model_data, "forge_loading_parameters", _UNSET)
        self._previous_hash = getattr(model_data, "forge_hash", _UNSET)

        parameters = direct_load_parameters(engine, runtime_label=runtime_label)
        model_data.forge_loading_parameters = parameters
        # Forge compares `str(dict)` against this field, so compute it the same
        # way rather than assuming a formatting.
        self._installed_hash = str(model_data.forge_loading_parameters)
        model_data.forge_hash = self._installed_hash

        self._engine_matches = model_data.sd_model is engine
        self._installed = True

    # -- verification -----------------------------------------------------

    def predicate_satisfied(self) -> bool:
        """Forge's own early-return condition, evaluated the way Forge does."""
        from modules import sd_models

        model_data = sd_models.model_data
        return model_data.forge_hash == str(model_data.forge_loading_parameters)

    def verify_fast_path(self, engine: object) -> bool:
        """Call the real `forge_model_reload()` and prove it was a no-op.

        The predicate is checked first, so the call cannot fall through into
        catalogue resolution. If it is not satisfied, no call is made and this
        reports failure rather than risking a real reload.
        """

        if not self.predicate_satisfied():
            self._fast_path_verified = False
            return False

        from modules import sd_models

        returned, reloaded = sd_models.forge_model_reload()
        self._returned_existing = returned is engine
        self._reported_false = reloaded is False
        self._fast_path_verified = self._returned_existing and self._reported_false
        return self._fast_path_verified

    # -- restore ----------------------------------------------------------

    def restore(self) -> None:
        if not self._installed:
            # Nothing was captured, so there is nothing to put back -- and no
            # reason to import Forge to discover that.
            self._restored = False
            return

        from modules import sd_models

        model_data = sd_models.model_data
        ok = True
        try:
            if self._previous_parameters is not _UNSET:
                model_data.forge_loading_parameters = self._previous_parameters
                ok = ok and model_data.forge_loading_parameters is self._previous_parameters
            if self._previous_hash is not _UNSET:
                model_data.forge_hash = self._previous_hash
                ok = ok and model_data.forge_hash == self._previous_hash
        except Exception:  # noqa: BLE001 - restoration must not raise
            ok = False
        self._restored = ok and self._installed

    # -- report -----------------------------------------------------------

    def result(self) -> DirectLoadReloadState:
        return DirectLoadReloadState(
            installed=self._installed,
            source="studio_direct_load_parameters" if self._installed else "not_installed",
            installed_forge_hash=self._installed_hash,
            engine_matches=self._engine_matches,
            fast_path_verified=self._fast_path_verified,
            returned_existing_engine=self._returned_existing,
            reported_reloaded_false=self._reported_false,
            restored=self._restored,
        )

    def to_dict(self) -> dict[str, object]:
        return self.result().to_dict()
