"""Where Studio's own writable state lives. Pure resolution, no side effects.

Owner decision D1 (2026-08-11): Studio owns ONE configurable
``STUDIO_STATE_ROOT``. Native installs default to the platform-conventional
per-user application-data location. Portable and Docker deployments may
explicitly override it. The internal layout is platform-independent -- the root
moves, what is inside it does not. Install directories, model roots and result
roots are **not** the state root.

This module answers one question and performs none of the work:

    platform + environment + config override
        -> which directory Studio's state belongs in, and why

It imports nothing beyond the standard library. It creates no directory, reads
no file, writes nothing, and mutates no environment. Every input is injected,
so the whole platform matrix is testable on one machine -- the same discipline
``modules/platform_selection.py`` already uses for install-command selection.

**Resolving a root is not a promise that it works.** This module says where
state belongs. Whether that path exists, is writable, or survives a reboot is a
runtime fact reported elsewhere, and deliberately not asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Mapping

# Platform families. Same vocabulary as modules/platform_selection.py so a
# reader does not have to learn two.
WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"
UNKNOWN_PLATFORM = "unknown"

# Where a resolution came from. Reported so an owner can tell a deliberate
# override from a default, which matters when state appears to be "missing"
# and is in fact somewhere else.
CONFIG_OVERRIDE = "config_override"
ENVIRONMENT_OVERRIDE = "environment_override"
PLATFORM_DEFAULT = "platform_default"
POSIX_FALLBACK = "posix_fallback"

#: The single environment override. Docker and portable installs use this
#: because editing a config file inside an image is the awkward path.
STATE_ROOT_ENV = "STUDIO_STATE_ROOT"

#: The directory name under a platform-conventional parent. One spelling per
#: platform because each convention has its own casing habit; the LAYOUT below
#: this directory is identical everywhere.
WINDOWS_DIR_NAME = "ForgeStudio"
MACOS_DIR_NAME = "ForgeStudio"
POSIX_DIR_NAME = "forge-studio"

#: Everything Studio keeps. Platform-independent by decision: an owner who
#: copies this directory between machines gets a working state root.
STATE_LAYOUT = (
    "preferences.json",
    "workflows/",
    "layouts/",
    "presets/develop/",
    "watermarks/",
    "gallery/",
)


class StateRootError(Exception):
    """A state root could not be resolved, or was refused."""


@dataclass(frozen=True)
class StateRootResolution:
    """Where Studio's state belongs, and how that was decided."""

    #: The resolved directory, as a string. Deliberately not a `Path`: this
    #: module never touches a filesystem and typing it as a path invites
    #: someone to. The caller converts.
    root: str
    source: str
    platform: str
    #: True when the owner chose this explicitly. A default that turns out to
    #: be wrong is a different conversation from an override that does.
    explicit: bool


def _family(system: str) -> str:
    normalized = (system or "").strip().lower()
    if normalized.startswith("win"):
        return WINDOWS
    if normalized == "darwin":
        return MACOS
    if normalized == "linux":
        return LINUX
    return UNKNOWN_PLATFORM


def _joined(base: str, *parts: str, windows: bool) -> str:
    """Join without importing os.path, so the result is platform-independent.

    `PureWindowsPath` and `PurePosixPath` are used explicitly rather than
    `Path`, because this module must produce a macOS answer while running on
    Windows -- which is the entire reason the matrix is testable at all.
    """

    kind = PureWindowsPath if windows else PurePosixPath
    return str(kind(base).joinpath(*parts))


def resolve_state_root(
    *,
    system: str,
    environ: Mapping[str, str],
    home: str | None,
    config_value: str | None = None,
) -> StateRootResolution:
    """Decide where Studio's state root is. Pure.

    Precedence, highest first:

    1. an explicit config value  -- the portable/Docker path;
    2. ``STUDIO_STATE_ROOT``     -- the same, for hosts where editing config is
                                    awkward;
    3. the platform convention;
    4. ``~/.forge-studio``       -- a labelled FALLBACK on an unrecognized
                                    platform, not a convention claim.

    An override is trusted verbatim, exactly as ``TORCH_COMMAND`` is in the
    install selector: an owner who names a path has answered the question this
    function exists to answer.
    """

    family = _family(system)
    windows = family is WINDOWS

    explicit = (config_value or "").strip()
    if explicit:
        return StateRootResolution(explicit, CONFIG_OVERRIDE, family, True)

    from_env = (environ.get(STATE_ROOT_ENV) or "").strip()
    if from_env:
        return StateRootResolution(from_env, ENVIRONMENT_OVERRIDE, family, True)

    if family is WINDOWS:
        # LOCALAPPDATA, not APPDATA. Roaming profiles synchronise, and this
        # directory holds a database, caches and machine-specific state --
        # replicating that across machines is a corruption risk, not a feature.
        base = (environ.get("LOCALAPPDATA") or "").strip()
        if not base and home:
            base = _joined(home, "AppData", "Local", windows=True)
        if not base:
            raise StateRootError(
                "Cannot resolve a Windows state root: neither LOCALAPPDATA nor "
                f"a home directory is available. Set {STATE_ROOT_ENV}."
            )
        return StateRootResolution(
            _joined(base, WINDOWS_DIR_NAME, windows=True),
            PLATFORM_DEFAULT,
            family,
            False,
        )

    if family is MACOS:
        if not home:
            raise StateRootError(
                "Cannot resolve a macOS state root without a home directory. "
                f"Set {STATE_ROOT_ENV}."
            )
        return StateRootResolution(
            _joined(home, "Library", "Application Support", MACOS_DIR_NAME,
                    windows=False),
            PLATFORM_DEFAULT,
            family,
            False,
        )

    if family is LINUX:
        # XDG_DATA_HOME rather than XDG_CONFIG_HOME or XDG_STATE_HOME. What
        # lives here is a mix -- preferences, workflows, a database -- and the
        # XDG spec puts application-owned data in DATA_HOME. Splitting one
        # coherent root across three XDG directories to satisfy a taxonomy
        # would break D1's "one configurable root" outright.
        base = (environ.get("XDG_DATA_HOME") or "").strip()
        if not base:
            if not home:
                raise StateRootError(
                    "Cannot resolve a Linux state root: neither XDG_DATA_HOME "
                    f"nor a home directory is available. Set {STATE_ROOT_ENV}."
                )
            base = _joined(home, ".local", "share", windows=False)
        return StateRootResolution(
            _joined(base, POSIX_DIR_NAME, windows=False),
            PLATFORM_DEFAULT,
            family,
            False,
        )

    # Unrecognized platform. The install selector REFUSES here, because
    # installing the wrong Torch build is worse than not starting. This is the
    # opposite trade: a POSIX-shaped home directory works on every unusual host
    # anyone is likely to run this on, and refusing would brick Studio over a
    # `platform.system()` string nobody anticipated.
    #
    # It is labelled POSIX_FALLBACK rather than PLATFORM_DEFAULT so the answer
    # never claims to be a convention it is not.
    if not home:
        raise StateRootError(
            f"Cannot resolve a state root on platform {system!r} without a "
            f"home directory. Set {STATE_ROOT_ENV}."
        )
    return StateRootResolution(
        _joined(home, "." + POSIX_DIR_NAME, windows=False),
        POSIX_FALLBACK,
        family,
        False,
    )


def refuse_conflicting_root(
    root: str,
    *,
    install_root: str | None = None,
    result_root: str | None = None,
    model_roots: tuple[str, ...] = (),
    windows: bool | None = None,
) -> None:
    """Raise when a state root collides with a root that is not it.

    D1 is explicit: install directories, model roots and result roots are NOT
    the Studio state root. Each collision is a distinct hazard rather than a
    tidiness rule:

    * inside the INSTALL directory, state is destroyed by an update or a clean
      reinstall -- the exact event an owner most expects to preserve settings;
    * equal to a MODEL root, Studio writes into a directory the product
      otherwise treats as read-only, and a bounded catalogue scan then walks
      its own state files;
    * equal to the RESULT root, state shares a directory whose contents an
      owner reasonably deletes in bulk.

    Comparison is lexical on normalized paths. This module does not resolve
    symlinks or touch the filesystem, so two spellings of one directory can
    still slip past -- a real containment check belongs where the filesystem
    is, and `root_policy` already owns that.
    """

    kind = PureWindowsPath if windows else PurePosixPath
    candidate = kind(root)

    def _same_or_inside(other: str) -> bool:
        target = kind(other)
        if candidate == target:
            return True
        try:
            candidate.relative_to(target)
        except ValueError:
            return False
        return True

    if install_root and _same_or_inside(install_root):
        raise StateRootError(
            "The Studio state root must not sit inside the install directory: "
            "an update or a clean reinstall would take the owner's settings "
            f"with it. Set {STATE_ROOT_ENV} to a directory outside it."
        )
    if result_root and candidate == kind(result_root):
        raise StateRootError(
            "The Studio state root must not be the result root: results are a "
            "directory owners delete in bulk."
        )
    for model_root in model_roots:
        if model_root and candidate == kind(model_root):
            raise StateRootError(
                "The Studio state root must not be a model root: model roots "
                "are read-only by default and are scanned as catalogues."
            )
