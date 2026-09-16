"""One concise, human-readable console trace per job.

An entire owner session driving FOUR generations produced 38 log lines, 23 of
them INFO and almost all from startup. Nothing at all was logged per job --
not one line. Neo's own output reaches the console fine (a `memory_management`
WARNING came through unchanged), so nothing was being swallowed. Studio simply
emitted almost nothing of its own, and Studio is the layer that knows the
facts an owner actually needs.

That is not cosmetic. When a friend says "it didn't work", the console is the
only artefact you get.

Almost everything printed here was ALREADY COMPUTED and simply never reported:
the `loads`/`reuses`/`switches` counters, the stage labels, the resolved
recipe assembled for metadata, the failure reason that now survives, and the
VRAM figures measured for the OOM message. This is closer to wiring existing
values to a logger than to new instrumentation, which is also what makes it
low-risk.

WHAT MAY NEVER BE PRINTED
=========================

Two rules, enforced here rather than remembered at each call site, because
this module is the only place that formats a job line:

```text
no prompt text        `to_dict` deliberately carries only LENGTHS. A console
                      is copied into bug reports, pasted into chats, and
                      attached to issues. Prompts are the owner's writing.
no filesystem paths   the same rule every public projection in this product
                      obeys. A model NAME is safe; the file it came from is
                      not, and `payload_references` holds real references.
```

`_safe` is the guard. It runs on every formatted line, and it is deliberately
a REFUSAL rather than a redaction: a line that would leak is dropped and
replaced with a marker, so the failure is visible in the console rather than
silently producing a nearly-right message.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable

#: The one logger name for per-job output, under the launcher's existing
#: `studio.` namespace so a host that already filters Studio logging catches
#: this too.
LOGGER_NAME = "studio.job"

logger = logging.getLogger(LOGGER_NAME)

#: Anything that looks like a location. Windows drive letters, UNC prefixes,
#: POSIX absolute paths, and the two separators in any run of non-space.
_PATHISH = re.compile(
    r"(?:[A-Za-z]:[\\/])"      # C:\ or C:/
    r"|(?:\\\\[^\s\\]+)"       # \\server
    r"|(?:(?<![\w.])/(?:[\w.-]+/)+)"   # /usr/share/
    r"|(?:[^\s]*\\[^\s]+)"     # any backslash-joined token
)

#: Field names whose VALUES are the owner's writing and never travel.
FORBIDDEN_FIELDS = ("positive_prompt", "negative_prompt", "prompt")

_REFUSED = "<line withheld: would have leaked a path>"


def _safe(line: str) -> str:
    """Refuse a line that would leak a location. Never silently trim one."""

    return _REFUSED if _PATHISH.search(line) else line


def _pairs(items: Iterable[tuple[str, Any]]) -> str:
    """`key=value` pairs, skipping anything absent.

    Absent is skipped rather than printed as None: a line reading
    `upscaler=None` invites the reader to believe an upscaler was chosen and
    rejected, when in fact none was asked for.
    """

    return " ".join(
        f"{key}={value}" for key, value in items
        if value is not None and value != ""
    )


class JobLog:
    """Per-job console output. One instance per process is enough.

    `names` resolves an opaque catalogue id to the name the owner picked it by.
    Optional, and absent is handled rather than required: a host with no
    catalogue logs a SHORT id instead. The owner chose three things by name and
    the log should say which three -- "three hashes" was the specific
    complaint -- but a truncated id still beats nothing, and it is never a path.
    """

    def __init__(self, names: Callable[[str, str], str | None] | None = None) -> None:
        self._names = names

    # -- naming ------------------------------------------------------------

    def _name(self, role: str, model_id: str | None) -> str | None:
        if not model_id:
            return None
        if self._names is not None:
            try:
                resolved = self._names(role, model_id)
            except Exception:  # noqa: BLE001 - a log line never breaks a job
                resolved = None
            if resolved:
                return str(resolved)
        return f"{model_id[:8]}…"

    # -- the trace ---------------------------------------------------------

    def accepted(
        self,
        job_id: str,
        *,
        queue_position: int | None = None,
        selection: dict[str, str] | None = None,
    ) -> None:
        selection = selection or {}
        logger.info(_safe(
            "JOB ACCEPTED " + _pairs((
                ("job", job_id),
                ("queued_at", queue_position),
                ("checkpoint",
                 self._name("checkpoint", selection.get("checkpoint_model_id"))),
                ("text_encoder",
                 self._name("text_encoder", selection.get("text_encoder_model_id"))),
                ("vae", self._name("vae", selection.get("vae_model_id"))),
            ))
        ))

    def session(self, job_id: str, *, action: str, reason: str = "") -> None:
        """load / reuse / switch, and why.

        "Reusing warm session" is the single most reassuring line a slow app
        can print, and the counters behind it (`loads`, `reuses`, `switches`)
        already existed and were invisible on the console.
        """

        logger.info(_safe(
            "MODEL SESSION " + _pairs((
                ("job", job_id), ("action", action), ("reason", reason),
            ))
        ))

    def dispatch(self, job_id: str, *, resolved: dict[str, Any]) -> None:
        """The RESOLVED values, after defaults are applied.

        Not what was requested. The whole of P0.6 was about the gap between
        those two, and a log that printed the request would have hidden it.
        """

        # `backend=`, not `job=`. This is the BACKEND id -- the translation
        # seam is below the coordinator and never sees the public one -- and
        # printing two different identifiers under the same key makes the
        # console unreadable for the one task it exists for: following one
        # job. Named for what it is instead.
        logger.info(_safe(
            "DISPATCH " + _pairs((
                ("backend", job_id),
                ("sampler", resolved.get("sampler")),
                ("scheduler", resolved.get("scheduler")),
                ("steps", resolved.get("steps")),
                ("size", _size(resolved)),
                ("seed", resolved.get("seed")),
                ("preview", _flag(resolved.get("preview_enabled"))),
            ))
        ))

    def hires(self, job_id: str, *, recipe: dict[str, Any]) -> None:
        """The second-pass recipe, already assembled for metadata."""

        logger.info(_safe(
            "HIRES " + _pairs((
                ("job", job_id),
                ("scale", recipe.get("scale")),
                ("upscaler", recipe.get("upscaler")),
                ("steps", recipe.get("second_pass_steps")),
                ("denoise", recipe.get("denoising_strength")),
                ("target", recipe.get("target")),
            ))
        ))

    def auto_detail(self, job_id: str, *, slots: Any) -> None:
        """Which detectors a job asked for, before any of them runs.

        The console had no Auto Detail line at all: DISPATCH names the
        sampler, the size and the seed, HIRES names the second-pass recipe,
        and a job that ran three detector passes said nothing about them. The
        owner had to infer it from a step count appearing after the second
        pass -- which reads identically to a Hires pass, and was in fact
        mistaken for one.

        Names only. A detector NAME is what the catalogue offers and what the
        owner chose; the resolved path would be refused by `_safe` and take
        the whole line with it.
        """

        if not slots:
            return
        logger.info(_safe(
            "AUTODETAIL " + _pairs((
                ("job", job_id),
                ("slots", len(slots)),
                ("detectors", ",".join(
                    str(getattr(slot, "detector", "")) for slot in slots
                )),
            ))
        ))

    def auto_detail_result(self, job_id: str, *, outcomes: Any) -> None:
        """What the detectors actually FOUND, once the passes are done.

        The pair to the line above, and the more useful of the two: "found
        four, kept one" is the only evidence the owner's confidence and ratio
        settings did anything, and it cannot be reconstructed from the image.
        """

        for outcome in outcomes or ():
            logger.info(_safe(
                "AUTODETAIL " + _pairs((
                    ("job", job_id),
                    ("slot", getattr(outcome, "index", "?")),
                    ("detector", getattr(outcome, "detector", "")),
                    ("candidates", getattr(outcome, "candidates", 0)),
                    ("regions", getattr(outcome, "regions", 0)),
                    ("detailed", getattr(outcome, "detailed", False)),
                ))
            ))

    def stage(self, job_id: str, *, stage: str) -> None:
        """A transition that already existed as a state and a label.

        Printing them costs nothing and makes the upscale gap legible -- that
        gap is exactly where a Hires job looks hung.
        """

        logger.info(_safe("STAGE " + _pairs((("job", job_id), ("stage", stage)))))

    def queue(self, event: str, *, job_id: str = "", depth: int | None = None) -> None:
        logger.info(_safe(
            "QUEUE " + _pairs((
                ("event", event), ("job", job_id), ("depth", depth),
            ))
        ))

    def result(
        self,
        job_id: str,
        *,
        elapsed_seconds: float | None = None,
        width: int | None = None,
        height: int | None = None,
        handle: str | None = None,
    ) -> None:
        """Where it went as a HANDLE, never a path."""

        logger.info(_safe(
            "RESULT " + _pairs((
                ("job", job_id),
                ("elapsed", None if elapsed_seconds is None else f"{elapsed_seconds:.1f}s"),
                ("size", None if not (width and height) else f"{width}x{height}"),
                ("handle", handle),
            ))
        ))

    def failure(self, job_id: str, *, code: str = "", message: str = "") -> None:
        """The reason, which now exists after the `error: null` fix, and which
        the console still did not print."""

        logger.warning(_safe(
            "FAILED " + _pairs((
                ("job", job_id), ("code", code), ("reason", message),
            ))
        ))

    def vram(self, job_id: str, *, moment: str, free: int | None, total: int | None) -> None:
        """Already measured for the OOM message; only reported when things
        went wrong. Reported at load and release instead."""

        if free is None or total is None:
            return
        logger.info(_safe(
            "VRAM " + _pairs((
                ("job", job_id), ("at", moment),
                ("free", _gib(free)), ("total", _gib(total)),
            ))
        ))


def _size(resolved: dict[str, Any]) -> str | None:
    width, height = resolved.get("width"), resolved.get("height")
    return f"{width}x{height}" if width and height else None


def _flag(value: Any) -> str | None:
    return None if value is None else ("on" if value else "off")


def _gib(value: int) -> str:
    return f"{value / (1024 ** 3):.1f}GiB"


def make_name_resolver(registry: Any) -> Callable[[str, str], str | None]:
    """`(role, model_id) -> display name`, over the ONE model root registry.

    Defensive by attribute rather than by type: the candidate's display field
    has had more than one name in this codebase's life, and a logger is the
    last place that should fail because one of them moved.
    """

    def name_for(role: str, model_id: str) -> str | None:
        try:
            for entry in registry.entries(role):
                if getattr(entry, "model_id", None) != model_id:
                    continue
                for field in ("display_name", "name", "stem"):
                    value = getattr(entry, field, None)
                    if value:
                        return str(value)
                return None
        except Exception:  # noqa: BLE001 - a log line never breaks a job
            return None
        return None

    return name_for


__all__ = (
    "FORBIDDEN_FIELDS",
    "LOGGER_NAME",
    "JobLog",
    "make_name_resolver",
)
