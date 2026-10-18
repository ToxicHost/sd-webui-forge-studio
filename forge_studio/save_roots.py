"""Which folders Studio may save into, and the owner's trusted list.

OF1. Settings > Save folder had a Trust button, an Open button and a trusted
list, and all three were answered by `source_api_adapter.py` with "unavailable
in mock mode" -- on the live server too. This is the Extension's trust model
(`scripts/studio_api.py:736-960`, routes `:5292-5321`) on Studio's own roots:

```text
safe write roots   Studio's result root
                   + the owner's trusted folders (persisted, Trust / Browse)
                   + the Gallery's linked folders
                   + STUDIO_SAVE_ROOTS (the operator's, e.g. a Docker mount)
trust refuses      a drive or filesystem root, the home folder itself, system
                   folders and anything under them, Studio's install folder or
                   any folder that CONTAINS it, and Studio's state folder
                   (config, Gallery index) or anything inside it
```

A path is never trusted by appearing in a generation request: a folder that
is not inside a safe root is replaced by the default and the owner is told
why, exactly as the Extension does (`:3071-3095`). The review is
`Evidence/source-review/OF1-output-folder-and-save-structure.md`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

#: The persisted list, in Studio's state folder. The Extension's file name.
TRUSTED_FILE = "trusted_save_roots.json"

#: The notice the Extension sends when it skipped an untrusted folder
#: (`studio_api.py:3092-3093`), in Studio's words for the same buttons.
UNTRUSTED_NOTICE = (
    "Your custom save folder isn't trusted yet, so images were saved to the "
    "default output folder. Open Settings → Save folder and click "
    "“Trust folder”."
)
UNWRITABLE_NOTICE = (
    "Your save folder could not be written to (is the drive connected?), so "
    "images were saved to the default output folder."
)
RELATIVE_NOTICE = (
    "Your save folder must be a full path, so images were saved to the "
    "default output folder."
)

#: POSIX system folders the Extension refuses, and anything under them.
_POSIX_SYSTEM = ("/etc", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev",
                 "/lib", "/lib64", "/usr", "/var", "/root", "/System", "/Library")
_WINDOWS_SYSTEM_ENV = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                       "ProgramData")


@dataclass(frozen=True)
class SaveDecision:
    """What admission does with the owner's folder.

    `folder` is the resolved, trusted folder, or "" for the default. `notice`
    is the sentence the page shows when the owner's choice was not used.
    """

    folder: str = ""
    notice: str = ""


def _resolved(value: Any) -> Path | None:
    try:
        text = str(value or "").strip()
        return Path(text).expanduser().resolve() if text else None
    except (OSError, RuntimeError, ValueError):
        return None


def _inside(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def _writable(folder: Path) -> bool:
    """A real write probe, as the Extension does: `os.access` lies on Windows."""

    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".saveprobe_",
                                         delete=True):
            pass
        return True
    except OSError:
        return False


def environment_roots(environ: Any = None) -> list[str]:
    """`STUDIO_SAVE_ROOTS`, the operator's extra roots (`studio_api.py:913`)."""

    source = os.environ if environ is None else environ
    return [chunk.strip() for chunk in str(source.get("STUDIO_SAVE_ROOTS", "") or "")
            .split(os.pathsep) if chunk.strip()]


class SaveRoots:
    """The trust policy and the persisted trusted list. Thread-safe.

    `linked_folders` and `adopt_folder` come from the Gallery: its linked
    folders are save roots (as in the Extension), and a trusted folder Studio
    saves into is registered there so the Gallery finds what was saved. Both
    are optional; a host with no Gallery simply has neither.
    """

    def __init__(
        self,
        *,
        state_root: Any = None,
        result_root: Any = None,
        install_roots: Iterable[Any] = (),
        linked_folders: Callable[[], Iterable[str]] | None = None,
        adopt_folder: Callable[[Path], Any] | None = None,
        environ: Any = None,
    ) -> None:
        self._state_root = _resolved(state_root)
        self._result_root = _resolved(result_root)
        self._install_roots = [p for p in (_resolved(r) for r in install_roots) if p]
        self.linked_folders = linked_folders
        self.adopt_folder = adopt_folder
        self._environ = environ
        self._lock = threading.Lock()
        #: Memory-only when there is no state folder (the demo server, tests).
        self._memory: set[str] = set()

    # -- the persisted list ------------------------------------------------

    @property
    def result_root(self) -> Path | None:
        return self._result_root

    def _file(self) -> Path | None:
        return self._state_root / TRUSTED_FILE if self._state_root else None

    def trusted(self) -> list[str]:
        """The owner's trusted folders, sorted. Never raises."""

        with self._lock:
            return sorted(self._load())

    def _load(self) -> set[str]:
        path = self._file()
        if path is None:
            return set(self._memory)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        items = data.get("trusted_save_roots", []) if isinstance(data, dict) else []
        out = set()
        for item in items if isinstance(items, list) else []:
            resolved = _resolved(item)
            if resolved is not None:
                out.add(str(resolved))
        return out

    def _store(self, roots: set[str]) -> None:
        path = self._file()
        if path is None:
            self._memory = set(roots)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"trusted_save_roots": sorted(roots)}, indent=2)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".trusted_", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(body)
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def trust(self, path: Any) -> tuple[bool, str]:
        """Validate and persist one folder: `(True, resolved)` or `(False, why)`.

        The Extension's `_add_trusted_root` (`studio_api.py:836-850`).
        """

        raw = str(path or "").strip()
        if not raw:
            return False, "No path provided."
        if not Path(raw).expanduser().is_absolute():
            return False, "Type the folder's full path."
        resolved = _resolved(raw)
        if resolved is None:
            return False, "Path could not be resolved."
        if self.is_dangerous(resolved):
            return False, ("That folder is too broad or system-level to trust. "
                           "Pick a dedicated output subfolder.")
        if not _writable(resolved):
            return False, ("Folder does not exist / could not be created, or isn't "
                           "writable by Forge Studio.")
        with self._lock:
            roots = self._load()
            roots.add(str(resolved))
            try:
                self._store(roots)
            except OSError:
                return False, "The trusted list could not be saved."
        return True, str(resolved)

    def untrust(self, path: Any) -> None:
        resolved = _resolved(path)
        with self._lock:
            roots = self._load()
            if resolved is not None:
                roots.discard(str(resolved))
            roots.discard(str(path or "").strip())
            try:
                self._store(roots)
            except OSError:
                pass

    # -- the policy --------------------------------------------------------

    def is_dangerous(self, folder: Path) -> bool:
        """The Extension's `_is_dangerous_root` (`studio_api.py:785-833`),
        with Studio's state folder added: it holds the owner's config and the
        Gallery index, and images do not belong inside it."""

        rp = _resolved(folder)
        if rp is None:
            return True
        if rp == rp.parent:
            return True
        try:
            if rp == Path.home().resolve():
                return True
        except (OSError, RuntimeError):
            pass
        system = list(_POSIX_SYSTEM)
        for name in _WINDOWS_SYSTEM_ENV:
            value = os.environ.get(name)
            if value:
                system.append(value)
        for entry in system:
            dp = _resolved(entry)
            if dp is not None and (rp == dp or _inside(rp, dp)):
                return True
        for install in self._install_roots:
            # The folder itself or anything that CONTAINS it; a dedicated
            # subfolder under it is allowed, as in the Extension.
            if rp == install or _inside(install, rp):
                return True
        state = self._state_root
        if state is not None and (rp == state or _inside(rp, state) or _inside(state, rp)):
            return True
        return False

    def roots(self) -> list[Path]:
        """Every folder a save may go into (`studio_api.py:879-920`)."""

        found: list[Path] = []
        if self._result_root is not None:
            found.append(self._result_root)
        with self._lock:
            trusted = self._load()
        candidates: list[Any] = list(trusted)
        candidates.extend(environment_roots(self._environ))
        if self.linked_folders is not None:
            try:
                candidates.extend(self.linked_folders() or ())
            except Exception:  # noqa: BLE001 - a Gallery fault adds no roots
                pass
        for item in candidates:
            resolved = _resolved(item)
            if resolved is not None and resolved not in found:
                found.append(resolved)
        return found

    def allows(self, path: Any) -> bool:
        resolved = _resolved(path)
        return resolved is not None and any(_inside(resolved, root) for root in self.roots())

    def decide(self, save_dir: str) -> SaveDecision:
        """The folder a job saves into, or the default and a notice."""

        raw = str(save_dir or "").strip()
        if not raw:
            return SaveDecision()
        if not Path(raw).expanduser().is_absolute():
            return SaveDecision(notice=RELATIVE_NOTICE)
        resolved = _resolved(raw)
        if resolved is None or not self.allows(resolved):
            # Permanent diagnostic, as the Extension's (`:3082-3090`): a
            # rejection says what was asked and what was allowed.
            logger.warning(
                "save_dir rejected (not a trusted root) -- using the default "
                "output folder. candidate=%r resolved=%r roots=%r",
                raw, str(resolved) if resolved else None,
                [str(root) for root in self.roots()],
            )
            return SaveDecision(notice=UNTRUSTED_NOTICE)
        if not _writable(resolved):
            return SaveDecision(notice=UNWRITABLE_NOTICE)
        self._adopt(resolved)
        return SaveDecision(folder=str(resolved))

    def _adopt(self, folder: Path) -> None:
        """Make the Gallery scan this folder, unless it already does.

        Skipped when the folder is inside the result root or a linked folder:
        those are scanned already, and a nested scan folder would index the
        same file twice. Never raises -- the Gallery must not cost a save.
        """

        adopt = self.adopt_folder
        if adopt is None:
            return
        scanned: list[Path] = []
        if self._result_root is not None:
            scanned.append(self._result_root)
        if self.linked_folders is not None:
            try:
                scanned.extend(p for p in (_resolved(x) for x in self.linked_folders() or ())
                               if p is not None)
            except Exception:  # noqa: BLE001
                return
        if any(_inside(folder, root) for root in scanned):
            return
        try:
            adopt(folder)
        except Exception:  # noqa: BLE001 - see the docstring
            return

    def default_folder(self) -> Path | None:
        """What Open shows for a blank field: Studio's own output folder."""

        return self._result_root


__all__ = (
    "RELATIVE_NOTICE",
    "SaveDecision",
    "SaveRoots",
    "TRUSTED_FILE",
    "UNTRUSTED_NOTICE",
    "UNWRITABLE_NOTICE",
    "environment_roots",
)
