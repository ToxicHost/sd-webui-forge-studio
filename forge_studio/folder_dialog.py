"""The operating system's own "Select folder" dialog, for every folder setting.

ONE route, `POST /studio/pick_folder`, answers every Browse button in Studio:
the six model-folder roles, Save folder, Gallery folder, the first-run card,
the wildcard folder, the heatmap data folder and the Gallery's Link folder.
Before this, two copies of the same Tk snippet (`wildcard_service.py`,
`gallery_service.py`, each lifted from the Extension) served two of them and an
in-page browser of the server's disk served the rest, so an owner pointing
Studio at their models had to type or paste the path. Owner, 2026-10-18: being
made to type it is "horribly inconvenient".

```text
POST /studio/pick_folder  {start?, purpose?}
  -> {path: "D:\\Models"}       chosen
  -> {path: ""}                  cancelled -- quiet, not an error
  -> {unavailable: true, error}  no desktop to put a dialog on
  -> {busy: true, error}         a dialog is already open on the desktop
```

WHY THE SERVER OPENS IT. Studio binds 127.0.0.1, so in normal use the server
and the browser are the same machine and a dialog the server opens is on the
owner's screen. A browser cannot hand the page a folder PATH at all
(`showDirectoryPicker` returns a handle, never a path), and the path is what
every one of these settings stores. Where there is no desktop -- a container,
a Linux host without a display, a Windows service session -- the answer is
`unavailable` and the page falls back to its in-page browser and the typed
field, which is where a remote owner was always going to type.

THE CHOSEN PATH IS ONLY TEXT IN A FIELD. Nothing here trusts, saves or scans
it: each control posts it to the route that already validates that setting
(the model-root policy with its opt-in network folders, save-root trust, the
wildcard folder check, the heatmap move). The dialog choosing a folder is not
a reason for any of those checks to be skipped.

OFF THE REQUEST THREAD, ONE AT A TIME. The dialog runs on its own daemon
thread -- Tk wants to own the thread it is created on, and a server worker is
reused for the next request -- and the request waits for it with a timeout.
`ThreadingHTTPServer` keeps every other route answering meanwhile. A second
Browse while a dialog is open answers `busy` rather than stacking a second
dialog behind the first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

#: How long a request waits for the owner to choose. Long, because choosing a
#: folder is a person browsing; the dialog stays open past it and the next
#: Browse answers `busy` until it is closed.
PICK_TIMEOUT_SECONDS = 300.0

#: How long the start folder may take to answer. A start folder on a share
#: whose machine is off must not hold the dialog back for a minute.
START_PROBE_SECONDS = 3.0

#: The dialog's title per control. A closed set rather than text from the page:
#: a native window on the owner's desktop says only what Studio wrote.
TITLES = {
    "folder": "Select folder",
    "checkpoint": "Select checkpoint folder",
    "text_encoder": "Select text encoder folder",
    "vae": "Select VAE folder",
    "lora": "Select LoRA folder",
    "embedding": "Select embeddings folder",
    "upscaler": "Select upscaler folder",
    "controlnet": "Select ControlNet folder",
    "adetailer": "Select detector folder",
    "save": "Select save folder",
    "gallery": "Select image folder",
    "wildcards": "Select wildcard folder",
    "heatmap": "Select heatmap data folder",
}

BUSY_MESSAGE = (
    "A folder dialog is already open. Finish choosing there, then press "
    "Browse again."
)
UNAVAILABLE_MESSAGE = (
    "This Studio cannot open a folder dialog here. Type the folder's path "
    "instead."
)


#: Windows has a different API for everything below (the shell dialog, the
#: foreground rule, sessions). `os.name` rather than a platform name, as Studio's
#: owned code asks what a host can DO; the rest is told apart by which chooser
#: exists (`osascript` is the Mac's).
WINDOWS = os.name == "nt"


def _has_osascript() -> bool:
    return not WINDOWS and shutil.which("osascript") is not None


class DialogUnavailable(RuntimeError):
    """There is no desktop to show a dialog on, or no way to show one."""


Chooser = Callable[[str | None, str], "str | None"]


def title_for(purpose: Any) -> str:
    return TITLES.get(str(purpose or ""), TITLES["folder"])


def headless_reason() -> str | None:
    """Why no dialog can appear on this host, or None when one can."""

    if WINDOWS:
        # Session 0 is where services run: it has no interactive desktop, and
        # a dialog opened there is a window nobody can ever see or close.
        try:
            import ctypes
            from ctypes import wintypes

            session = wintypes.DWORD(0)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
                if session.value == 0:
                    return "Studio is running as a service, with no desktop."
        except Exception:  # noqa: BLE001 - unknown is "try the dialog"
            pass
        return None
    if _has_osascript():
        return None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "This machine has no display for a folder dialog."
    return None


def resolve_start(start: Any, *, timeout: float = START_PROBE_SECONDS) -> str | None:
    """The folder the dialog opens in: the one set, or its nearest parent.

    A folder that was typed but not yet created still opens somewhere useful
    -- the place it would go. Nothing that is not an existing directory is
    passed to the dialog, and a start that takes longer than `timeout` to
    answer (a share whose machine is off) is dropped rather than waited on.
    """

    text = str(start or "").strip().strip('"')
    if not text or "\x00" in text or len(text) > 4096:
        return None
    found: dict[str, str] = {}

    def probe() -> None:
        try:
            path = Path(text)
            if not path.is_absolute():
                return
            for candidate in (path, *path.parents):
                if candidate.is_dir():
                    found["path"] = str(candidate)
                    return
        except (OSError, ValueError):
            return

    worker = threading.Thread(target=probe, name="studio-folder-start", daemon=True)
    worker.start()
    worker.join(timeout)
    return found.get("path")


# -- the native choosers -------------------------------------------------------


def _bring_to_front_win32(window: Any) -> None:
    """Make Tk's (invisible) owner window the foreground window on Windows.

    Windows refuses SetForegroundWindow to a process that is not already in
    front, and Studio's server never is -- the browser is. Without this the
    dialog opened BEHIND the browser and only flashed on the taskbar, which an
    owner reads as "Browse did nothing". Attaching to the foreground thread's
    input for the one call is the documented way through: the dialog that
    follows is owned by a foreground, topmost window, so it opens in front and
    takes the keyboard.
    """

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]

    hwnd = wintypes.HWND(int(window.wm_frame(), 16))
    foreground = user32.GetForegroundWindow()
    mine = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(theirs and theirs != mine
                    and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)


def tk_chooser(start: str | None, title: str) -> str | None:
    """Tk's directory chooser: Explorer's own "Select Folder" on Windows.

    Tk 8.6 implements `askdirectory` on Windows with the shell's IFileDialog
    in folder mode, which is the dialog File Explorer shows. The owner window
    is a 1x1, fully transparent, topmost window rather than a withdrawn one,
    because a withdrawn window cannot be made the foreground window and the
    dialog then opens behind the browser.
    """

    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as error:
        raise DialogUnavailable("tkinter is not installed") from error
    try:
        root = tkinter.Tk()
    except Exception as error:  # noqa: BLE001 - TclError: no display
        raise DialogUnavailable(type(error).__name__) from error
    try:
        try:
            root.overrideredirect(True)
            root.attributes("-alpha", 0.0)
            root.attributes("-topmost", True)
            width, height = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"1x1+{width // 2}+{height // 3}")
            root.update()
            if WINDOWS:
                _bring_to_front_win32(root)
            root.lift()
            root.focus_force()
            root.update()
        except Exception:  # noqa: BLE001 - in front is best effort, the dialog is not
            pass
        options: dict[str, Any] = {"parent": root, "title": title, "mustexist": True}
        if start:
            options["initialdir"] = start
        chosen = filedialog.askdirectory(**options)
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    # Cancelled is "" (or an empty tuple on some Tk builds).
    return str(chosen or "") or None


def _run_dialog_tool(command: list[str]) -> str | None:
    """A desktop's own picker as a child process. Exit 1 is a cancel."""

    completed = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        command, capture_output=True, text=True, timeout=PICK_TIMEOUT_SECONDS * 2,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout.strip().rstrip("/") or None
    if completed.returncode == 1:
        return None
    raise DialogUnavailable(f"{command[0]} exited {completed.returncode}")


def macos_chooser(start: str | None, title: str) -> str | None:
    """Finder's own chooser, through osascript (Tk must own the main thread on
    macOS, and a server worker is not it)."""

    script = f'choose folder with prompt "{title}"'
    if start:
        escaped = start.replace("\\", "\\\\").replace('"', '\\"')
        script += f' default location (POSIX file "{escaped}")'
    completed = subprocess.run(  # noqa: S603, S607 - a fixed argv, no shell
        ["osascript", "-e", f"POSIX path of ({script})"],
        capture_output=True, text=True, timeout=PICK_TIMEOUT_SECONDS * 2,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout.strip().rstrip("/") or None
    if "-128" in completed.stderr:  # "User canceled."
        return None
    raise DialogUnavailable("osascript could not show a folder dialog")


def linux_chooser(start: str | None, title: str) -> str | None:
    """The desktop's own chooser where there is one (GTK's, KDE's), else Tk's."""

    if shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory", f"--title={title}"]
        if start:
            command.append(f"--filename={start.rstrip('/')}/")
        return _run_dialog_tool(command)
    if shutil.which("kdialog"):
        command = ["kdialog", "--title", title, "--getexistingdirectory"]
        if start:
            command.append(start)
        return _run_dialog_tool(command)
    return tk_chooser(start, title)


def native_chooser(start: str | None, title: str) -> str | None:
    if WINDOWS:
        return tk_chooser(start, title)
    if _has_osascript():
        return macos_chooser(start, title)
    return linux_chooser(start, title)


# -- the service ---------------------------------------------------------------


class FolderDialog:
    """One native folder dialog at a time, waited for with a timeout."""

    def __init__(
        self,
        *,
        chooser: Chooser | None = None,
        timeout: float = PICK_TIMEOUT_SECONDS,
        headless: Callable[[], str | None] = headless_reason,
    ) -> None:
        self._chooser = chooser or native_chooser
        self._timeout = timeout
        self._headless = headless
        self._lock = threading.Lock()
        self._open = False

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def pick(self, start: Any = None, purpose: Any = None) -> dict[str, Any]:
        reason = self._headless()
        if reason:
            return {"unavailable": True, "error": UNAVAILABLE_MESSAGE, "reason": reason}
        with self._lock:
            if self._open:
                return {"busy": True, "error": BUSY_MESSAGE}
            self._open = True

        title = title_for(purpose)
        result: dict[str, Any] = {}
        done = threading.Event()

        def ask() -> None:
            try:
                initial = resolve_start(start)
                chosen = self._chooser(initial, title)
                result["path"] = str(chosen or "").replace("/", os.sep) \
                    if WINDOWS else str(chosen or "")
            except DialogUnavailable as error:
                result["unavailable"] = str(error)
            except Exception as error:  # noqa: BLE001 - never a 500 for a dialog
                result["unavailable"] = type(error).__name__
            finally:
                with self._lock:
                    self._open = False
                done.set()

        threading.Thread(target=ask, name="studio-folder-dialog", daemon=True).start()
        if not done.wait(self._timeout):
            # Still open on the desktop. Its answer can no longer reach this
            # request, so the page is told to finish there and press Browse
            # again -- which answers `busy` until the dialog is closed.
            return {"busy": True, "error": BUSY_MESSAGE}
        if "unavailable" in result:
            return {"unavailable": True, "error": UNAVAILABLE_MESSAGE,
                    "reason": result["unavailable"]}
        # Cancelled is an empty path and NOT an error: every caller returns
        # quietly on a blank path, so reporting a cancel as a failure would
        # scold the owner for changing their mind.
        return {"path": result.get("path", "")}


_shared: FolderDialog | None = None
_shared_lock = threading.Lock()


def shared_dialog() -> FolderDialog:
    """The process's one dialog. There is one desktop, so there is one lock."""

    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = FolderDialog()
        return _shared
