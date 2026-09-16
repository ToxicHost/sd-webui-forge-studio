"""Neo startup globals the minimal generation closure needs.

`modules/shared_init.py` assigns eight fields on `modules.shared`. The
Gradio-free Studio boundary deliberately does not run it -- that is what keeps
`modules.shared_options` and the UI stack out of the headless path -- so any of
those fields the retained generation closure reads is left at its declared
`None`.

Two authorized Tier-0 attempts each stopped on one such field. This module
supplies the whole reachable set at once, so a third is not spent the same way.

What the closure actually reads
-------------------------------
Walking `process_images_inner` and the modules it calls:

======================  ==========================================  ===========
`shared.opts`           already supplied by the options boundary     supplied
`shared.state`          already supplied by `ForgeStateBridge`       supplied
`shared.prompt_styles`  `modules/processing.py:407-408`, in          HERE
                        `setup_prompts`, called from :904
`shared.device`         `modules/rng.py:167`, the last line of
                        `ImageRNG.first()`; `p.rng` is built at
                        `modules/processing.py:959` and used by
                        `p.sample()` at :993                         HERE
`shared.total_tqdm`     `modules/sd_samplers_common.py:433`, in
                        `callback_state`, one line after the owned
                        bridge receives a real sampling step         HERE
`shared.mem_mon`        no read in the closure                       not needed
`shared.options_templates`  no read in the closure                   not needed
`shared.restricted_opts`    no read in the closure                   not needed
======================  ==========================================  ===========

`shared.device` is the one the previous milestone's narrower sweep missed: it
scanned `processing.py` and a short helper list, where the only `device` reads
sit behind `firstpass_image` and Hires branches, and concluded it was off-path.
`modules/rng.py` was not in that list. It is on the path, at initial noise
creation.

Production classes, not stand-ins
---------------------------------
All three use exactly what `shared_init` uses, because all three are safe under
the strict boundary:

* `styles.StyleDatabase(shared.styles_filename)` -- the default paths carry no
  wildcard, so no directory is enumerated; `reload()` appends only paths that
  already exist and opens them read-only; nothing is written. With no styles
  CSV present the database is simply empty, which is what the Tier-0 request
  wants.
* `shared_total_tqdm.TotalTQDM()` -- the constructor only sets `_tqdm = None`.
  No bar, no thread, no output. Display is disabled through the existing
  `multiple_tqdm` option (see `COMPAT_OPTION_OVERRIDES`), which makes
  `update()` and `updateTotal()` return before touching `tqdm` at all, and
  leaves `clear()` a no-op.
* `devices.device` -- resolved at import from
  `backend.memory_management.get_torch_device()`, the same discovery the
  generation path already performs.

None of the three constructs Gradio, opens a socket, touches the network, reads
a model file, or starts a thread.

This is a compatibility context, not Neo startup parity: five of the eight
`shared_init` fields are deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Option overrides this context depends on. `multiple_tqdm=False` is what
#: keeps the production `TotalTQDM` silent: `update()` and `updateTotal()` both
#: return immediately, so no progress bar is constructed and nothing is written
#: to stdout. The owned progress bridge stays the only progress authority.
COMPAT_OPTION_OVERRIDES: dict[str, object] = {"multiple_tqdm": False}

#: Fields this context installs, in the order the closure first reads them.
SUPPLIED_FIELDS: tuple[str, ...] = ("prompt_styles", "device", "total_tqdm")

#: Fields `shared_init` also assigns, which the closure never reads. Listed so
#: the omission is a recorded decision rather than an oversight.
DELIBERATELY_ABSENT: tuple[str, ...] = (
    "mem_mon",
    "options_templates",
    "restricted_opts",
)

_UNSET = object()


@dataclass(frozen=True)
class CompatibilityInstallation:
    """What was installed and where each object came from."""

    prompt_styles_installed: bool
    total_tqdm_installed: bool
    device_installed: bool
    prompt_styles_source: str
    total_tqdm_source: str
    device_source: str
    prompt_styles_restored: bool
    total_tqdm_restored: bool
    device_restored: bool
    style_count: int
    supplied_fields: tuple[str, ...] = SUPPLIED_FIELDS
    deliberately_absent: tuple[str, ...] = DELIBERATELY_ABSENT

    def to_dict(self) -> dict[str, object]:
        return {
            "prompt_styles_installed": self.prompt_styles_installed,
            "total_tqdm_installed": self.total_tqdm_installed,
            "device_installed": self.device_installed,
            "prompt_styles_source": self.prompt_styles_source,
            "total_tqdm_source": self.total_tqdm_source,
            "device_source": self.device_source,
            "prompt_styles_restored": self.prompt_styles_restored,
            "total_tqdm_restored": self.total_tqdm_restored,
            "device_restored": self.device_restored,
            "style_count": self.style_count,
            "supplied_fields": list(self.supplied_fields),
            "deliberately_absent": list(self.deliberately_absent),
        }


class HeadlessCompatibilityContext:
    """Install the Neo startup globals the closure reads; restore them after.

    Install before `process_images_inner`. Restore in a `finally`: worker
    process exit is not restoration, and a half-installed `modules.shared` would
    outlive the run inside the same interpreter.
    """

    def __init__(self) -> None:
        self._previous: dict[str, object] = {}
        self._installed: dict[str, bool] = {}
        self._sources: dict[str, str] = {}
        self._style_count = 0
        self._restored: dict[str, bool] = {}

    # -- install ----------------------------------------------------------

    def install(self) -> None:
        from modules import shared

        for field in SUPPLIED_FIELDS:
            self._previous[field] = getattr(shared, field, _UNSET)
            self._installed[field] = False
            self._sources[field] = "not_installed"

        # prompt styles -- read at processing.py:407 via setup_prompts
        from modules import styles

        database = styles.StyleDatabase(shared.styles_filename)
        shared.prompt_styles = database
        self._style_count = len(getattr(database, "styles", {}) or {})
        self._installed["prompt_styles"] = True
        self._sources["prompt_styles"] = "modules.styles.StyleDatabase"

        # device -- read at rng.py:167 during initial noise creation
        from modules import devices

        shared.device = devices.device
        self._installed["device"] = True
        self._sources["device"] = "modules.devices.device"

        # total progress -- read at sd_samplers_common.py:433 per sampler step
        from modules import shared_total_tqdm

        shared.total_tqdm = shared_total_tqdm.TotalTQDM()
        self._installed["total_tqdm"] = True
        self._sources["total_tqdm"] = "modules.shared_total_tqdm.TotalTQDM"

    # -- restore ----------------------------------------------------------

    def restore(self) -> None:
        from modules import shared

        for field in SUPPLIED_FIELDS:
            previous = self._previous.get(field, _UNSET)
            if previous is _UNSET:
                self._restored[field] = not hasattr(shared, field)
                continue
            try:
                # `TotalTQDM.clear()` is a no-op when no bar was ever built,
                # which is the case whenever multiple_tqdm stayed False.
                if field == "total_tqdm":
                    current = getattr(shared, field, None)
                    if current is not None and hasattr(current, "clear"):
                        current.clear()
                setattr(shared, field, previous)
                self._restored[field] = getattr(shared, field, _UNSET) is previous
            except Exception:  # noqa: BLE001 - restoration must not raise
                self._restored[field] = False

    # -- report -----------------------------------------------------------

    def result(self) -> CompatibilityInstallation:
        return CompatibilityInstallation(
            prompt_styles_installed=self._installed.get("prompt_styles", False),
            total_tqdm_installed=self._installed.get("total_tqdm", False),
            device_installed=self._installed.get("device", False),
            prompt_styles_source=self._sources.get("prompt_styles", "not_installed"),
            total_tqdm_source=self._sources.get("total_tqdm", "not_installed"),
            device_source=self._sources.get("device", "not_installed"),
            prompt_styles_restored=self._restored.get("prompt_styles", False),
            total_tqdm_restored=self._restored.get("total_tqdm", False),
            device_restored=self._restored.get("device", False),
            style_count=self._style_count,
        )

    def to_dict(self) -> dict[str, object]:
        return self.result().to_dict()
