"""The real sampler and scheduler lists, read from Forge Neo.

The UI has been offering `DPM++ 2M SDE`, `Euler a`, `Karras` and `Simple` as
literal `<option>` text in `index.html`. None of them reached generation --
the request had no sampler field at all, so every image came out of whatever
default the processing object held, and the controls were decoration. A
control that does nothing is worse than a missing one: it tells the owner
their choice was honoured.

This module is the fix's foundation. It reads the lists Neo actually
dispatches on, and returns plain strings:

```text
samplers    modules.sd_samplers.all_samplers      -> names
schedulers  modules.sd_schedulers.schedulers      -> labels
```

This module READS. It does not initialise, and that restraint was learned the
hard way rather than chosen up front.

Neo's `sd_schedulers` filters its list against `shared.opts.hide_schedulers`
AT IMPORT TIME, so `shared.opts` must exist before the module is first
touched, and Neo expects its own `sys.path` entries and a parseable `argv`.
A first version therefore stood all of that up on demand so a cold server
could answer. It did answer -- 21 samplers, 17 schedulers -- and then the very
next real generation failed, because the read had imported Neo under a
temporary `opts` and then torn that context down, leaving import-time state
bound to options that no longer existed.

So: where the engine is already running -- the process that has performed a
load -- the lists are real. Where it is not, the answer is EMPTY, the page
says the engine default will be used, and that is true. An empty list is a
smaller cost than a second way to initialise one engine.

Nothing is read at import time either: a registry that populated itself on
import would drag Neo into every Studio process, including the ones the
purity tests exist to keep clean.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import HeadlessError

REGISTRY_UNAVAILABLE = "HEADLESS_REGISTRY_UNAVAILABLE"

#: What the product falls back to when Neo cannot be reached at all -- a
#: mock-backend host, or a machine with no engine installed. Deliberately
#: EMPTY rather than a plausible-looking list: an invented sampler name is
#: exactly the lie this module exists to remove, and a page that receives
#: nothing can say "unavailable" instead of offering a choice that will not
#: be honoured.
EMPTY: tuple[str, ...] = ()

#: Set once the image-upscaler scan can no longer usefully be repeated: after
#: it SUCCEEDS, or after a failure that destroyed the registry it needed (see
#: `_read_upscalers`). A failure before that point stays retryable, because an
#: early call against a still-starting engine is the case this latch was made
#: to survive. A list rather than a bool because this module holds no mutable
#: module state elsewhere and a rebind would need a `global`.
_UPSCALER_SCAN_DONE: list = []


@dataclass(frozen=True)
class Registries:
    """What the owner may actually choose, as plain strings."""

    samplers: tuple[str, ...]
    schedulers: tuple[str, ...]
    available: bool
    reason: str | None = None
    #: Hires upscalers, split because the Hires adapter takes a DIFFERENT path
    #: for each: a latent mode resizes the latent in place, an image upscaler
    #: decodes, upscales pixels, and re-encodes. Offering them in one flat list
    #: would hide the distinction that decides which code runs.
    latent_upscalers: tuple[str, ...] = ()
    image_upscalers: tuple[str, ...] = ()
    #: True only after the image-upscaler scan completed (including a valid
    #: empty result). This is separate from `available`: samplers can already
    #: be readable while an early upscaler scan is still retryable.
    upscaler_scan_complete: bool = False

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "samplers": list(self.samplers),
            "schedulers": list(self.schedulers),
            "latent_upscalers": list(self.latent_upscalers),
            "image_upscalers": list(self.image_upscalers),
            "upscaler_scan_complete": self.upscaler_scan_complete,
            "available": self.available,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


def read_registries(*, bindings=None) -> Registries:
    """Read the live lists, or report honestly that they are unavailable.

    `bindings` is an already-open `StudioStartupGlobals`-style object when the
    caller has one -- during a load, Neo is already standing and re-entering
    it would be both wasteful and a second initialisation path. Without one,
    the modules are imported directly and this only succeeds in a process
    where Neo's path and options are already installed.
    """

    try:
        samplers, schedulers = _read()
    except HeadlessError:
        raise
    except BaseException as error:  # noqa: BLE001 - a UI list is never fatal
        return Registries(
            samplers=EMPTY,
            schedulers=EMPTY,
            available=False,
            reason=type(error).__name__,
        )
    upscaler_scan_complete = False
    try:
        latent, image = _read_upscalers()
        # A pre-populated registry needs no scan; otherwise the latch is set
        # only by a successful `load_upscalers()` call. An empty successful
        # scan is therefore distinguishable from a failed early attempt.
        upscaler_scan_complete = bool(image or _UPSCALER_SCAN_DONE)
    except BaseException:  # noqa: BLE001 - an absent list is not a failed read
        latent, image = EMPTY, EMPTY
    return Registries(
        samplers=samplers,
        schedulers=schedulers,
        available=True,
        latent_upscalers=latent,
        image_upscalers=image,
        upscaler_scan_complete=upscaler_scan_complete,
    )


def _read(repository_root=None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read the lists ONLY where Neo is already standing.

    An earlier version stood the engine up on demand -- `sys.path` entries,
    `headless_options`, a neutralised argv -- so the route could answer on a
    cold server. It answered with 21 real samplers, and then the next real
    generation FAILED: the read imports Neo modules under a TEMPORARY `opts`
    and then tears that context down, leaving import-time state captured
    against options that no longer exist. Two ways to initialise one engine,
    which is precisely what this module's own docstring warned about, done by
    this module.

    So it no longer initialises anything. Where Neo is up -- in the process
    that has performed a load -- the lists are real. Where it is not, the
    caller gets EMPTY and the page says the engine default will be used,
    which is true.
    """

    import sys

    if "modules.sd_samplers" not in sys.modules:
        # Not imported by anything else yet, and this module will not be the
        # one to do it. Importing here is what broke generation.
        raise HeadlessError(
            REGISTRY_UNAVAILABLE, "The engine is not running yet."
        )
    return _read_installed()


def _read_installed() -> tuple[tuple[str, ...], tuple[str, ...]]:
    from modules import sd_samplers, sd_schedulers

    samplers = tuple(
        str(entry.name) for entry in getattr(sd_samplers, "all_samplers", ())
    )
    schedulers = tuple(
        str(getattr(entry, "label", entry))
        for entry in getattr(sd_schedulers, "schedulers", ())
    )
    if not samplers:
        raise HeadlessError(
            REGISTRY_UNAVAILABLE, "Neo reported no samplers."
        )
    return samplers, schedulers


def _read_upscalers() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The Hires upscalers Neo can actually dispatch, latent and image.

    The latent modes are a literal dict in `modules.shared` and cost nothing.

    The image list is EMPTY until `modelloader.load_upscalers()` runs, and
    Studio's headless boot never calls it -- Neo's own `initialize.py` does, on
    a path Studio deliberately does not take. So the page was offering
    `R-ESRGAN 4x+` on an install with zero dispatchable image upscalers: the
    same "control that does nothing" defect P0.6 removed for sampler and
    scheduler, sitting in the Hires panel.

    Calling it here is a scan of a directory, not an initialisation of the
    engine: it imports nothing Neo has not already imported by the time this is
    reachable, and `shared.sd_upscalers` is not one of the globals
    `StudioStartupGlobals` snapshots and restores. That is why this is
    permitted where standing Neo up to answer a list is not.

    It is latched after the first SUCCESS per process -- a rescan per request
    would re-stat the model directory on every page load for a list that
    cannot change without a restart.

    A failure is retried, but only while retrying can still work. Neo's
    `load_upscalers()` deletes `shared.sd_upscalers` as its first statement,
    so a failure after that point leaves no registry for the next call to
    rebuild and every retry raises on the same `del`. That case latches too.
    """

    import sys

    # The SAME gate `_read` applies, and for the same reason. Without it this
    # function performs a real `from modules import shared` in any process that
    # reaches it -- including one where `_read` was mocked out -- which imports
    # Neo into a Studio process that had deliberately avoided it.
    #
    # That is not hypothetical. Wrapping the call in `except BaseException` made
    # it look harmless: nothing raised, an empty list came back, and the import
    # still happened. `test_import_boundaries` purges `forge_studio` and asserts
    # a fresh import stays clean, so it failed in the FULL run and passed in
    # isolation -- the pollution arrived from a different test file.
    #
    # "It did not raise" is not the same as "it did nothing".
    if "modules.shared" not in sys.modules:
        return EMPTY, EMPTY

    from modules import shared

    latent = tuple(str(name) for name in getattr(shared, "latent_upscale_modes", {}))

    # `not getattr(shared, "sd_upscalers", None)` is true for TWO states that
    # mean opposite things, and conflating them is what makes the retry unsafe:
    #
    #   attribute PRESENT but empty -- no scan has run, or one found nothing.
    #                                  Retrying is free and may succeed.
    #   attribute MISSING           -- `load_upscalers()` opens with
    #                                  `del shared.sd_upscalers`
    #                                  (modules/modelloader.py, its first
    #                                  statement after the import) and then
    #                                  failed AFTER it. The name is gone.
    #
    # In the second state every retry re-enters that same `del` and raises
    # AttributeError before doing any work, so the scan can never succeed
    # again in this process. Retrying forever would be futile on its own; it
    # is worse than that, because `_loadRegistries` in app.js only stops
    # refreshing once `upscaler_scan_complete` is true, so a latch that never
    # sets rebuilds four <select>s on every busy -> ready transition for the
    # life of the process. `shared.sd_upscalers` is defined at module scope
    # (modules/shared.py), so `hasattr` distinguishes the two cleanly.
    if not hasattr(shared, "sd_upscalers"):
        # Unrecoverable. Latch so the UI is told the scan is over and stops
        # asking. The list stays empty, which is the truth. Restoring the
        # attribute here would make a retry possible but writes Neo state from
        # a read path; that is a bigger decision than this fix.
        if not _UPSCALER_SCAN_DONE:
            _UPSCALER_SCAN_DONE.append(True)
    elif not shared.sd_upscalers and not _UPSCALER_SCAN_DONE:
        try:
            from modules import modelloader

            modelloader.load_upscalers()
        except BaseException:  # noqa: BLE001 - a UI list is never fatal
            pass
        else:
            # Latch on SUCCESS only. Setting it before the try meant one early
            # call -- /api/registries against a server whose engine had not
            # finished coming up -- latched the scan as done after it had
            # failed, and the image-upscaler list stayed empty for the life of
            # the process. The latch exists to stop repeat scans, not to record
            # that one was attempted.
            _UPSCALER_SCAN_DONE.append(True)

    image = tuple(
        str(getattr(entry, "name", entry))
        for entry in (getattr(shared, "sd_upscalers", None) or ())
    )
    return latent, image


def is_known_sampler(name: str, registries: Registries) -> bool:
    """Whether a requested sampler is one Neo will actually dispatch.

    Compared case-insensitively because the owner's page shows a label and the
    registry holds a name, and a mismatch of case is not a mismatch of intent.
    """

    wanted = (name or "").strip().casefold()
    return any(wanted == entry.casefold() for entry in registries.samplers)


def is_known_scheduler(name: str, registries: Registries) -> bool:
    wanted = (name or "").strip().casefold()
    return any(wanted == entry.casefold() for entry in registries.schedulers)


def is_known_upscaler(name: str, registries: Registries) -> bool:
    """Either kind. The adapter decides WHICH path; this decides whether the
    engine can dispatch it at all."""

    wanted = (name or "").strip().casefold()
    return any(
        wanted == entry.casefold()
        for entry in registries.latent_upscalers + registries.image_upscalers
    )


def is_latent_upscaler(name: str, registries: Registries) -> bool:
    """Latent modes resize the latent in place; image upscalers decode,
    upscale pixels and re-encode. Studio must take the right one."""

    wanted = (name or "").strip().casefold()
    return any(wanted == entry.casefold() for entry in registries.latent_upscalers)


__all__ = (
    "EMPTY",
    "REGISTRY_UNAVAILABLE",
    "Registries",
    "is_known_sampler",
    "is_known_scheduler",
    "is_known_upscaler",
    "is_latent_upscaler",
    "read_registries",
)
