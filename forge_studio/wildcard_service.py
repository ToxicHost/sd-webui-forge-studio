"""The wildcard folder an owner chose, and the routes that read it.

Six routes, and the shapes are the page's rather than this module's -- a
lesson the Gallery port paid for three times over:

```text
GET  /studio/dynamic_prompts/config        wildcard_folder_mode, wildcard_folder,
                                           wildcard_folder_display,
                                           studio_dynamic_prompts_enabled
POST /studio/dynamic_prompts/config        {studio_dynamic_prompts_enabled}
POST /studio/dynamic_prompts/pick_folder   {path} | {unavailable: true}
POST /studio/dynamic_prompts/select_folder {folder} -> stores it
GET  /studio/dynamic_prompts/status        available, enabled, wildcard_count
GET  /studio/wildcards                     [{name, kb}]
GET  /studio/wildcard_content?name=        the lines of one file
POST /studio/wildcard_preview              {prompt, seed, samples}
```

`{"unavailable": true}` from the picker is not a failure: `app.js` reads it and
reveals a manual-path row so an owner on a headless host can paste a path
instead. Answering a 500 there would send them to a toast and no way forward.

THE FOLDER IS STORED ON THE STATE ROOT, in the preferences document D1 already
keeps, so it survives a restart and travels with the owner's state folder. A
choice held in memory would be a setting that quietly forgets itself.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from .wildcards import WildcardLibrary, resolve_root

#: Where the choice lives in the preferences document.
FOLDER_KEY = "wildcard_folder"
ENABLED_KEY = "studio_dynamic_prompts_enabled"



#: How long to wait on the folder chooser before answering.
PICK_TIMEOUT_SECONDS = 300.0

#: Folders to fall back on when the owner has chosen none. Relative to the
#: Studio checkout, and only used when one actually exists.
DEFAULT_FOLDER_NAMES = ("wildcards", "extensions/sd-dynamic-prompts/wildcards")


class WildcardService:
    """What the page asks about wildcards, answered from the owner's folder."""

    def __init__(self, preferences: Any = None,
                 checkout: str | Path | None = None) -> None:
        self._preferences = preferences
        self._checkout = Path(checkout) if checkout else None
        self._lock = threading.RLock()
        self._library: WildcardLibrary | None = None
        self._root: Path | None = None
        self._mode = "disabled"

    # -- state -------------------------------------------------------------

    def _stored(self) -> dict[str, Any]:
        if self._preferences is None:
            return {}
        try:
            return dict(self._preferences.read())
        except Exception:  # noqa: BLE001
            # A preferences document that cannot be read must not take the
            # wildcard panel down with it; the owner simply has no choice
            # stored yet.
            return {}

    def _fallbacks(self) -> list[Path]:
        if self._checkout is None:
            return []
        return [self._checkout / name for name in DEFAULT_FOLDER_NAMES]

    def library(self, *, reload: bool = False) -> WildcardLibrary:
        with self._lock:
            configured = str(self._stored().get(FOLDER_KEY) or "")
            root, mode = resolve_root(configured, self._fallbacks())
            if reload or self._library is None or root != self._root:
                self._root = root
                self._mode = mode
                self._library = WildcardLibrary(root).load(force=True)
            return self._library

    # -- routes ------------------------------------------------------------

    def _capable(self) -> bool:
        """Whether there is anything to expand against at all.

        Was `_default_enabled`, renamed because it answers a question about the
        FOLDER, not about the owner. It is still only consulted as a default;
        see `enabled()` for why it is deliberately not an AND term.
        """

        self.library()
        return self._mode != "disabled"

    def enabled(self) -> bool:
        """The owner's stored choice, defaulting to what the folder allows.

        Both flat constants are wrong. A flat False made the page claim the
        feature was running while the server skipped expansion -- every control
        correct, tokens reaching the sampler verbatim. A flat True is the same
        lie reversed: with no folder resolved there is nothing to expand
        against, and `test_r1_refusal_seam` exists to catch that.

        WP0.4 NOTE -- capability stays a DEFAULT and does not become an AND.
        Until the duplicate POST handler was removed nothing could ever be
        stored, so this fallback was not being fallen back to; it was the whole
        setting, every time. That makes an AND look tempting, since a stored
        True with no folder describes a service that cannot run. It is a trap:
        `config()` feeds the toggle's `on` class, `data-setting-depends` retires
        Browse and Reset from it (settings-page.js:467), and an owner whose
        answer was forced to False would lose the control they need to CHOOSE a
        folder. One unreachable corner is cheaper than a dead Browse button.
        Recorded for the owner rather than decided here.
        """

        return bool(self._stored().get(ENABLED_KEY, self._capable()))

    def config(self) -> dict[str, Any]:
        """What the settings panel renders.

        `wildcard_folder_mode`, `wildcard_folder` and `wildcard_folder_display`
        -- app.js:5879 reads exactly those three. The stub before this answered
        `folder_mode` and `folder`, so the panel said "Default folders" whatever
        the owner had chosen.
        """

        self.library()
        return {
            ENABLED_KEY: self.enabled(),
            "wildcard_folder_mode": self._mode,
            "wildcard_folder": str(self._root) if self._root else "",
            # The DISPLAY name is the folder's own name, not its path. The
            # panel puts it on screen beside "custom:", and a full path there
            # is both unreadable and more than the page needs to know.
            "wildcard_folder_display": self._root.name if self._root else "",
            "wildcard_count": len(self.library()),
        }

    def set_enabled(self, payload: Any) -> dict[str, Any]:
        wanted = bool((payload or {}).get(ENABLED_KEY, False))
        self._store({ENABLED_KEY: wanted})
        return self.config()

    def status(self) -> dict[str, Any]:
        """`available` and `enabled` are DIFFERENT questions, and stay so.

        D8 reads both: an absent service hides its control, a present service
        that cannot run disables it with a reason. Collapsing them would leave
        the page unable to tell an owner who turned wildcards off from one who
        has no folder to turn them on against.
        """

        library = self.library()
        return {
            "available": library.available,
            "enabled": self.enabled(),
            "wildcard_count": len(library),
        }

    def select_folder(self, payload: Any) -> dict[str, Any]:
        """Store the folder the owner chose. An empty one resets to default."""

        folder = str((payload or {}).get("folder") or "").strip()
        if not folder:
            self._store({FOLDER_KEY: ""})
            return {"ok": True, **self.config()}
        path = Path(folder)
        if not path.is_dir():
            return {"ok": False, "error": "There is no folder there.",
                    **self.config()}
        self._store({FOLDER_KEY: str(path)})
        self.library(reload=True)
        return {"ok": True, **self.config()}

    def pick_folder(self) -> dict[str, Any]:
        """Ask the owner for a folder with the system's own dialog.

        `{"unavailable": true}` where there is no desktop to put one on --
        app.js reads that and reveals a manual-path row, so a headless host
        stays usable. A 500 there would leave the owner with a toast and no
        way to set a folder at all.
        """

        result: dict[str, Any] = {}

        def ask() -> None:
            try:
                import tkinter
                from tkinter import filedialog
            except ImportError:
                result["unavailable"] = True
                return
            try:
                window = tkinter.Tk()
                window.withdraw()
                window.wm_attributes("-topmost", 1)
                chosen = filedialog.askdirectory(title="Select wildcard folder")
                window.destroy()
            except Exception:  # noqa: BLE001
                result["unavailable"] = True
                return
            # Cancelled is an empty path, NOT an error: app.js returns quietly
            # on a blank path, so reporting a cancel as a failure would scold
            # the owner for changing their mind.
            result["path"] = str(chosen or "").replace("/", os.sep)

        worker = threading.Thread(target=ask, name="studio-wildcard-pick",
                                  daemon=True)
        worker.start()
        worker.join(timeout=PICK_TIMEOUT_SECONDS)
        if worker.is_alive() or "unavailable" in result:
            return {"unavailable": True}
        return {"path": result.get("path", "")}

    def listing(self) -> list[dict[str, Any]]:
        return self.library().entries()

    def content(self, name: str) -> dict[str, Any]:
        lines = self.library().lines(name)
        return {"name": name, "lines": lines, "count": len(lines)}

    def preview(self, payload: Any) -> dict[str, Any]:
        request = payload if isinstance(payload, dict) else {}
        try:
            seed = int(request.get("seed", -1))
        except (TypeError, ValueError):
            seed = -1
        try:
            samples = int(request.get("samples", 1))
        except (TypeError, ValueError):
            samples = 1
        return self.library().preview(
            str(request.get("prompt") or ""), seed, samples)

    def expand(self, prompt: str, seed: int | None = None) -> Any:
        """Resolve one prompt for a generation. Never raises.

        Called from the generation path, so the rule the Gallery learned holds
        here too: a wildcard folder that cannot be read must not turn a picture
        that would have been made into a failure. The unexpanded prompt is a
        worse result than the expanded one, and a far better one than an error.
        """

        from .wildcards import Expansion

        try:
            return self.library().expand(prompt, seed)
        except Exception:  # noqa: BLE001
            return Expansion(text=prompt or "")

    # -- storage -----------------------------------------------------------

    def _store(self, values: dict[str, Any]) -> None:
        if self._preferences is None:
            return
        try:
            self._preferences.merge(values)
        except Exception:  # noqa: BLE001
            pass


__all__ = (
    "DEFAULT_FOLDER_NAMES",
    "ENABLED_KEY",
    "FOLDER_KEY",
    "WildcardService",
)
