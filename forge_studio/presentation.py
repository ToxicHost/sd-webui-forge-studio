"""Standalone Alpha S0.7 presentation and loopback HTTP entry point.

This module owns only presentation concerns.  It talks to the Studio
application through the public ``StudioApplication`` contract and serves a
small, dependency-free frontend from the local package.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import json
import math
import random
import re
import select
import socket
import sys
import struct
import threading
import time
from collections.abc import Iterator, Mapping
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit


_LOOPBACK_HOST = "127.0.0.1"
_DEFAULT_PORT = 7865
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_SOURCE_REQUEST_BYTES = 16 * 1024 * 1024
#: An encoded image, base64-inflated by 4/3, plus JSON overhead. Bounded
#: because the decode that follows is the expensive part: an unbounded body
#: here is an unbounded Pillow allocation. Refusals above this close the
#: connection rather than drain megabytes on demand.
_MAX_PIXEL_REQUEST_BYTES = 24 * 1024 * 1024
_FRONTEND_DIRECTORY = Path(__file__).resolve().parent / "frontend"
_EVIDENCE_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "Evidence" / "studio-alpha-s07"
)
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_STATE_ALIASES = {
    "canceled": "cancelled",
    "complete": "completed",
    "done": "completed",
    "error": "failed",
    "success": "completed",
    "succeeded": "completed",
}
_STATIC_ALIASES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/studio": "index.html",
    "/studio/": "index.html",
    "/studio.css": "app.css",
    "/studio.js": "app.js",
}
_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
}
_UNCACHED_STATIC_PATHS = frozenset({"/", "/index.html", "/studio", "/studio/"})
_SOURCE_LOADER_CSP_HASH = (
    # Native Canvas/Session integration: the floating results panel was
    # removed, so the loader's optionalScripts array is byte-identical to
    # its Internal Alpha Phase 1 form again and this hash returns to that
    # value. The s06 CSP test recomputes it from the served file, so the
    # constant and index.html move together.
    #
    # P0.4f moves it again: the loader array gains studio-dir-picker.js.
    # Repinned in the SAME commit as index.html, because a stale hash here
    # blocks the page's own loader and Studio does not boot at all -- the
    # one failure the source tests cannot see.
    #
    # AR4.4 moves it once more: the loader array gains canvas-recovery.js,
    # placed BEFORE studio-docs.js because the document system waits on
    # `StudioRecovery.whenResolved()` at boot and a module that has not
    # loaded yet cannot be waited on.
    #
    # CT2 moves it again: the loader array gains canvas-input.js, placed
    # BEFORE canvas-ui.js because `bindCanvas` reads `window.StudioInput` on
    # the first pointer event.
    #
    # Recomputed rather than guessed, and the METHOD was checked first: the
    # same expression run against the previously committed index.html
    # reproduces the previous constant exactly, so this one is trustworthy for
    # the same reason. That check matters more here than almost anywhere --
    # a wrong hash blocks the page's own loader and Studio does not boot at
    # all, which is the one failure the source tests cannot see.
    #
    # U3 adds the V2 kernel and its Canvas adapter to the optional list, so the
    # hash moves again.
    #
    # THIS TIME THE METHOD IS A TEST, not a note. `test_u3_canvas_adapter.py`
    # recomputes both constants from `index.html` and fails if either drifts,
    # which closes the hole the paragraph above describes as one the source
    # tests cannot see. It can be seen; it just was not being looked at. The
    # rule the test encodes, established by reproducing the previous constant:
    # hash the bytes BETWEEN the tags with CRLF normalised to LF. Hashing the
    # file's own CRLF gives a different digest and a page that does not boot.
    "'sha256-YqDufItmaa6qc0c14aQGFWm+qcKyx6TszbQeL16rcDE='"
)
_UPDATE_HANDLER_CSP_HASH = (
    "'sha256-q6WhThBFsQIP9RODqeJp+tqgTSG3Y20BYxMivfQlkX0='"
)
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class PresentationError(Exception):
    """A client-safe presentation-layer error."""

    def __init__(self, message: str, *, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _plain(value: Any) -> Any:
    """Convert owned contract values into JSON-compatible values."""

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _as_mapping(value: Any) -> dict[str, Any]:
    plain = _plain(value)
    if isinstance(plain, dict):
        return plain
    if isinstance(plain, str):
        return {"value": plain}
    return {"value": plain}


def _state_of(value: Any) -> str:
    plain = _plain(value)
    if isinstance(plain, dict):
        candidate = plain.get("state", plain.get("status", "unknown"))
    else:
        candidate = plain
    state = str(candidate or "unknown").strip().lower()
    return _STATE_ALIASES.get(state, state)


def _job_id_of(value: Any) -> str:
    plain = _plain(value)
    if isinstance(plain, dict):
        candidate = plain.get("job_id", plain.get("id"))
    else:
        candidate = plain
    if candidate is None or not str(candidate).strip():
        raise PresentationError(
            "Studio did not return a job identifier.",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return str(candidate)


#: No upper bound. Named rather than written as a number at each call site, so
#: that "this parameter has no ceiling" is a statement the code makes rather
#: than something a reader must infer from a suspiciously large constant.
#:
#: Steps used to be admitted as 1-150 here while execution refused above 40 and
#: the control advertised 150 -- three numbers, no two agreeing, and the
#: tightest one invisible until the job had already been queued and shown as
#: running. Owner ruling, 2026-08-20: Studio does not invent limits.
UNBOUNDED = sys.maxsize


def _bounded_int(
    payload: Mapping[str, Any], name: str, minimum: int, maximum: int
) -> int:
    value = payload.get(name)
    if type(value) is not int:
        raise PresentationError(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        # A floor-only bound says so, rather than reciting the sentinel. An
        # owner told "steps must be between 1 and 9223372036854775807" learns
        # nothing except that something is wrong with us.
        raise PresentationError(
            f"{name} must be at least {minimum}."
            if maximum is UNBOUNDED
            else f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _positive_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if type(value) is not int or value <= 0:
        raise PresentationError(f"{name} must be a positive integer.")
    return value


def _bounded_float(
    payload: Mapping[str, Any], name: str, minimum: float, maximum: float
) -> float:
    value = payload.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise PresentationError(f"{name} must be a number.")
    converted = float(value)
    if converted < minimum or converted > maximum:
        raise PresentationError(
            f"{name} must be between {minimum:g} and {maximum:g}."
        )
    return converted


#: The request proper. These live under `generation` in the canonical body.
#: Sampler and scheduler are names, checked for shape here and for membership
#: at the boundary that actually dispatches on them -- accepting them without
#: carrying them through would recreate the exact defect P0.6 exists to fix.
_GENERATION_FIELDS = (
    "positive_prompt",
    "negative_prompt",
    "seed",
    "steps",
    "cfg_scale",
    "width",
    "height",
    "sampler",
    "scheduler",
    # The Live Preview toggle, which the page has always shown and the backend
    # always pinned off. Accepted here so the choice can be CARRIED; whether a
    # frame is actually produced is decided where the latent exists, not here.
    "preview_enabled",
    # Clip Skip. A scalar rather than a group because it is one number with no
    # siblings that only mean anything together. Absent means absent: the job
    # names no Clip Skip and the engine keeps whatever the option already says,
    # so a client that predates this field is byte-identical to before.
    "clip_skip",
    # The Hires second pass, as ONE nested group. P0.7's arrival is additive:
    # a sibling key inside `generation`, so nothing about the base request was
    # re-cut to make room for it.
    "hires",
    # Auto Detail, the same way and for the same reason. Without this key in
    # this tuple the field is refused as unknown, so the contract that already
    # declares `auto_detail` could never have received one over HTTP.
    "auto_detail",
    # Variation seed, as ONE nested group for the same reason Hires is one: its
    # four fields are meaningless apart. Neo reports no variation at all when
    # `subseed_strength` is 0, so a subseed without a strength is a value the
    # engine discards.
    "variation",
    # WP1. The four fields that made the shipping Generate button unable to
    # submit an image operation at all: the lower contract has carried
    # `Operation`, `InputAsset` and an img2img processing branch since Gate 2A,
    # and `test_img2img_contract.py` proved all of it green -- against
    # `translate_request` called directly. No such request could be ADMITTED,
    # because these names were refused here as unknown fields. Thirty passing
    # tests described a feature no owner could reach.
    "operation",
    "source_image",
    "mask",
    "denoising_strength",
    # Ordinary inpaint, as ONE group, for the reason `hires` is one: these
    # fields are incoherent apart. A `mask_blur` with no mask is not a weaker
    # request.
    "inpaint",
    # WP1.5. The aspect randomizer's inputs. The page has shown base pools,
    # ratio pools and an orientation toggle since it shipped, and none of it
    # reached the server -- the controls confirmed a selection back to the
    # owner while every job used the fixed width and height.
    "aspect",
    # AR2.1. Which Canvas state the job captured.
    "document",
    # AR4.9. How the finished picture is written. The Settings control has
    # offered PNG/JPEG/WebP since Studio shipped and reached NOTHING: the field
    # was assembled below the lifecycle `return`, no contract carried it, and
    # the writer hardcoded PNG in three places.
    "output",
)

#: The three operations this product admits. A string here rather than the
#: headless `Operation` enum: `forge_studio` does not import `forge_headless`,
#: and the translation boundary is where the two vocabularies meet.
_OPERATIONS = ("txt2img", "img2img", "inpaint")

#: Every field an inpaint group may carry.
_INPAINT_FIELDS = ("mask_blur", "fill", "full_resolution", "padding",
                   "invert", "soft")

#: Every field the soft-inpainting group may carry, with the engine's own
#: slider bounds (`soft_inpainting.py:518-526`). Studio does not invent a
#: range: a value the engine would clamp or misread is refused by name here
#: instead of being silently accepted.
_SOFT_INPAINT_BOUNDS = {
    "schedule_bias": (0.0, 8.0),
    "preservation": (0.0, 8.0),
    "transition_contrast": (1.0, 32.0),
    "mask_influence": (0.0, 1.0),
    "diff_threshold": (0.0, 8.0),
    # 0-16, matching `#paramSoftDiffContrast`. The engine's slider stops
    # at 8, but that is a slider stop and not a capability: the value is
    # used as `1 / (1 + mask ** c)` (soft_inpainting.py:151), whose
    # denominator is >= 1 for any non-negative c. Higher just sharpens.
    "diff_contrast": (0.0, 16.0),
}

#: Every field an inline image may carry. The browser sends bytes, never a
#: path, and there is no key here that could hold one.
_ASSET_FIELDS = ("data_url", "media_type", "width", "height", "byte_length",
                 "origin")

#: Every field a variation group may carry. Neo's own spelling, because these
#: map one-to-one onto the base processing class and inventing Studio names for
#: them would buy nothing but a translation table.
_VARIATION_FIELDS = (
    "subseed",
    "subseed_strength",
    "seed_resize_from_w",
    "seed_resize_from_h",
)

#: The PRODUCT range for a Hires target: Forge's own, not one machine's.
#:
#: A tighter bound briefly lived here, sized to what fits on a 16 GB card. It
#: was the wrong place for a hardware constraint -- it would have capped every
#: install at the smallest one anybody tested on. Whether a target actually
#: fits is a question about the machine, and is answered by measuring the
#: machine at dispatch, not by a constant chosen here.
HIRES_SCALE_MIN = 1.0
#: No ceiling. The SECOND copy of this bound -- `generation_request.py` carries
#: its own -- and removing one without the other is precisely the mistake AR6.1
#: made with steps and AR6.2 with the seed. Found here by probing admission
#: after the other copy was removed, not by reading.
HIRES_SCALE_MAX = float("inf")

#: Every field a Hires group may carry. Unknown keys are refused with their
#: position, exactly as they are one level up.
_HIRES_FIELDS = (
    "enabled",
    "scale",
    "upscaler",
    "second_pass_steps",
    "denoising_strength",
    "sampler",
    "scheduler",
    "prompt",
    "negative_prompt",
    "cfg",
)

#: Every field an Auto Detail group may carry, and every field one slot may.
#: Unknown keys are refused with their position, as at every other level.
_AUTO_DETAIL_FIELDS = ("enabled", "slots")
_AUTO_DETAIL_SLOT_FIELDS = (
    "enabled",
    "detector",
    "confidence",
    "top_k",
    "min_ratio",
    "max_ratio",
    "dilate_erode",
    "mask_blur",
    "denoising_strength",
    "prompt",
    "negative_prompt",
    "inpaint_padding",
    "steps",
    "cfg",
)

#: Model IDENTITY, which the book makes a SIBLING of `generation`, not a member
#: of it. `model_selection` holds the three opaque ids this job wants resident;
#: their field-level validation belongs to `ModelSelection.from_payload`, which
#: refuses anything path-shaped before it can reach a catalogue.
_IDENTITY_FIELDS = ("model", "model_selection")

#: The three catalogue ids have exactly ONE spelling and one home: inside
#: `model_selection`. Accepting them at the top level as well is precisely the
#: "second permanent flat-ID schema" this change exists to avoid, so they are
#: refused there by name rather than quietly ignored.
_SELECTION_ID_FIELDS = (
    "checkpoint_model_id",
    "text_encoder_model_id",
    "vae_model_id",
)


def _canonical_body(payload: Any) -> dict[str, Any]:
    """The one wire shape, flattened for the validator below.

    Canonical (the book, Part II 5.4):

        {"model_selection": {...three ids...}, "generation": {...request...}}

    The older flat body is still accepted and is rewritten into that shape
    HERE, at one point, so no code past this line ever sees two shapes. That
    is what keeps it an input dialect rather than a second schema: there is one
    canonical body, one translation, and no branch downstream.

    What is refused, rather than tolerated:

      * the three catalogue ids at the top level -- the actual trap;
      * `model_selection` inside `generation` -- it is a sibling;
      * a MIXED body, nesting some fields while leaving others flat, which is
        the shape a half-finished migration produces and the one most likely
        to silently drop a parameter.
    """

    if not isinstance(payload, Mapping):
        raise PresentationError("The generation request must be a JSON object.")

    for name in _SELECTION_ID_FIELDS:
        if name in payload:
            raise PresentationError(
                f"{name} belongs inside model_selection, not at the top level."
            )

    if "generation" not in payload:
        stray = sorted(
            str(key)
            for key in payload
            if key not in _IDENTITY_FIELDS and key not in _GENERATION_FIELDS
        )
        if stray:
            raise PresentationError(f"Unsupported generation field: {stray[0]}.")
        return dict(payload)

    generation = payload["generation"]
    if not isinstance(generation, Mapping):
        raise PresentationError("generation must be a JSON object.")

    misplaced = sorted(
        str(key)
        for key in payload
        if key not in _IDENTITY_FIELDS and key != "generation"
    )
    if misplaced:
        raise PresentationError(
            f"{misplaced[0]} belongs inside generation, not at the top level."
        )
    unexpected = sorted(
        str(key) for key in generation if key not in _GENERATION_FIELDS
    )
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: generation.{unexpected[0]}."
        )

    flattened: dict[str, Any] = {
        key: payload[key] for key in _IDENTITY_FIELDS if key in payload
    }
    flattened.update(generation)
    return flattened


def _validated_request_payload(payload: Any) -> dict[str, Any]:
    payload = _canonical_body(payload)

    model_value = payload.get("model")
    positive_value = payload.get("positive_prompt")
    negative_value = payload.get("negative_prompt")
    selection_value = payload.get("model_selection")
    # A job that NAMES its selection has already said which model it wants.
    # Requiring `model` as well is asking the client to report what is
    # currently resident -- which, when generating from NO_MODEL, is nothing.
    # That refusal was the last thing standing between the owner and a
    # Load-free Generate, and it fired only in the live UI: every unit test
    # supplied a model id because a model was always already loaded.
    if selection_value is not None:
        model_value = model_value if isinstance(model_value, str) else ""
    elif not isinstance(model_value, str):
        raise PresentationError("A model must be selected.")
    if not isinstance(positive_value, str):
        raise PresentationError("Positive prompt must be text.")
    for name in ("sampler", "scheduler"):
        value = payload.get(name)
        if value is not None and not isinstance(value, str):
            raise PresentationError(f"{name} must be text.")
    if not isinstance(negative_value, str):
        raise PresentationError("Negative prompt must be text.")
    model = model_value.strip()
    positive_prompt = positive_value.strip()
    negative_prompt = negative_value.strip()
    if not model and selection_value is None:
        raise PresentationError("A model must be selected.")
    if len(model) > 200:
        raise PresentationError("The model identifier is too long.")
    # NO PROMPT LENGTH LIMIT. This refused anything past 4000 characters and
    # called it "the Alpha S0.7 size limit" -- a phase name, which told an
    # owner nothing about what they had done or what to do instead. Nothing in
    # the core caps prompt length, and a prompt with several LoRA tags and an
    # expanded wildcard reaches 4000 without trying.

    seed = _bounded_int(payload, "seed", -1, UNBOUNDED)
    # A FLOOR only. See UNBOUNDED above.
    steps = _bounded_int(payload, "steps", 1, UNBOUNDED)
    width = _positive_int(payload, "width")
    height = _positive_int(payload, "height")

    selection = payload.get("model_selection")
    if selection is not None and not isinstance(selection, Mapping):
        raise PresentationError("model_selection must be a JSON object.")

    request = {
        "model_id": model,
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "steps": steps,
        "cfg_scale": _bounded_float(payload, "cfg_scale", 0, 30),
        "width": width,
        "height": height,
        # Carried verbatim. Field-level validation belongs to
        # `ModelSelection.from_payload`, so there is one definition of what a
        # selection may contain rather than two that can drift apart.
        "model_selection": dict(selection) if selection is not None else None,
        # Trimmed and carried. Membership is checked where dispatch happens,
        # not here: this layer would have to import Neo to know the answer,
        # and a presentation module that imports the engine is the boundary
        # this product is built around.
        "sampler": str(payload.get("sampler") or "").strip(),
        "scheduler": str(payload.get("scheduler") or "").strip(),
        # Absent means off. Coerced rather than type-checked because a toggle
        # is the one field where every client spelling -- true, 1, "on" -- means
        # the same thing, and refusing a job over the shape of a preview flag
        # would cost the owner the image they actually asked for.
        "preview_enabled": _preview_flag(payload.get("preview_enabled")),
        "clip_skip": _validated_clip_skip(payload.get("clip_skip")),
        "hires": _validated_hires(payload.get("hires")),
        "output": _validated_output(payload.get("output")),
        "auto_detail": _validated_auto_detail(payload.get("auto_detail")),
        "variation": _validated_variation(payload.get("variation")),
        # WP1. Validated as a set rather than field by field, because the
        # coherence rule spans them: which of source and mask must be present
        # is a property of the OPERATION, and checking each in isolation cannot
        # see that.
        **_validated_image_operation(payload),
    }
    # AFTER the operation is decided, because the randomizer is txt2img-only,
    # and BEFORE the request leaves admission, because the concrete answer has
    # to be what gets queued.
    request["document"] = _validated_document(payload.get("document"))
    request.update(_resolved_aspect(
        payload,
        operation=str(request.get("operation") or "txt2img"),
        width=int(request["width"]), height=int(request["height"]),
        image_count=_submission_image_count(payload)))
    return request


#: Every field an aspect group may carry.
_ASPECT_FIELDS = ("randomize_base", "randomize_ratio", "randomize_orientation",
                  "base_pool", "ratio_pool")


def _submission_image_count(payload: Mapping[str, Any]) -> int:
    """How many images this submission covers. One.

    A single named function rather than a literal `1` scattered through the
    aspect path, because the per-image rolling above is written against a
    count and reads better for having one.

    It used to say "one, until WP5", and to describe a Batch control the page
    had but nothing read. Both halves are now out of date: the owner removed
    Batch Count and Batch Size on 2026-08-20, so there is no control to read
    and no batch admission coming. `batch_count` is what the queue already
    does -- with per-job cancel and reorder -- and `batch_size` would need a
    multi-result contract Studio does not have.

    So this returns one because Studio produces one, not because a larger
    number is still being waited for.
    `Evidence/source-review/AR5.1-batch-removal.md`.
    """

    return 1


def _resolved_aspect(payload: Mapping[str, Any], *, operation: str,
                     width: int, height: int,
                     image_count: int = 1,
                     chooser: Any = None) -> dict[str, Any]:
    """Roll the dimensions NOW, one per image, or leave them as they arrived.

    Runs at admission so the concrete answers are frozen before the job is
    queued. A roll at execution would leave the recipe saying "randomize" and
    the result saying 1024x576 with nothing tying them together, and re-running
    the recipe would produce a different picture.

    ONE ROLL PER IMAGE, chained, matching the Extension's batch loop. Moving
    the roll to admission changed when it happens, not how often -- see
    `aspect_randomizer.resolve_series`.

    `chooser` is the same injection seam the resolver already exposes, carried
    one level up so a test can pin what a MULTI-IMAGE admission produced rather
    than assert that three real draws happened to differ. Three draws from a
    three-entry pool repeat often; a test written that way fails on the runs
    where randomness is merely being random, which teaches the reader to
    distrust it. None means the resolver's own default.

    TXT2IMG ONLY, which is the Extension's own guard (`studio_generation.py`
    :3247, `if is_txt2img:`). An img2img or inpaint job takes its geometry from
    the source image; rolling a shape for one would resize the owner's picture
    to something nobody asked for.

    Returns the effective width and height plus the provenance, so a caller
    writes both into the request in one step.
    """

    from .aspect_randomizer import AspectRefused, resolve_series
    from .contracts import AspectRandomization, AspectRoll

    raw = payload.get("aspect")
    if raw is None:
        return {"width": width, "height": height, "aspect": None}
    if not isinstance(raw, Mapping):
        raise PresentationError("aspect must be a JSON object.")
    unexpected = sorted(str(key) for key in raw if key not in _ASPECT_FIELDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: aspect.{unexpected[0]}.")

    modes = {}
    for name in ("randomize_base", "randomize_ratio", "randomize_orientation"):
        value = raw.get(name, False)
        if not isinstance(value, bool):
            raise PresentationError(f"aspect.{name} must be true or false.")
        modes[name] = value

    base_pool = raw.get("base_pool")
    ratio_pool = raw.get("ratio_pool")
    try:
        rolled_series = resolve_series(
            width, height, image_count, base_pool=base_pool,
            ratio_pool=ratio_pool,
            **({"chooser": chooser} if chooser is not None else {}),
            **modes)
    except AspectRefused as error:
        raise PresentationError(error.message) from None
    rolled = rolled_series[0]

    if operation != "txt2img":
        # Validated above so a malformed pool is still reported, then ignored
        # for geometry: the source image decides the shape.
        return {"width": width, "height": height, "aspect": None}

    return {
        # The FIRST image's frozen size. `rolls` carries the rest; the flat
        # pair stays because every consumer downstream of admission still
        # describes one image, and it must agree with rolls[0] rather than
        # being a second, separately-derived answer.
        "width": rolled.width,
        "height": rolled.height,
        "aspect": AspectRandomization(
            base_pool=tuple(base_pool or ()),
            ratio_pool=tuple(str(label) for label in (ratio_pool or ())),
            rolls=tuple(
                AspectRoll(width=entry.width, height=entry.height,
                           base=entry.base, ratio=entry.ratio_label,
                           orientation=entry.orientation)
                for entry in rolled_series),
            **modes) if rolled.randomized else None,
    }


def _validated_image_operation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Operation, source, mask and inpaint settings, checked together."""

    operation = _validated_operation(payload.get("operation"))
    source = _validated_asset(payload.get("source_image"), "source_image")
    mask = _validated_asset(payload.get("mask"), "mask")
    _enforce_operation_invariants(operation, source, mask)

    inpaint = _validated_inpaint(payload.get("inpaint"))
    if inpaint is not None and operation != "inpaint":
        raise PresentationError(
            "inpaint settings were supplied for a "
            f"{operation} job.")
    if operation == "inpaint" and inpaint is None:
        # Neo's own defaults, so a request carrying only a mask behaves as the
        # engine would with an untouched panel.
        from .contracts import InpaintSettings

        inpaint = InpaintSettings()

    return {
        "operation": operation,
        "source_image": source,
        "mask": mask,
        "denoising_strength": _validated_denoise(
            payload.get("denoising_strength")),
        "inpaint": inpaint,
    }


def _validated_asset(value: Any, role: str) -> Any:
    """One inline image, or None. Shape only -- the pixels are not read here.

    `forge_studio` may not import PIL, so this checks that the thing is an
    object with the keys an asset has and a `data:` URL that is not a path. The
    DECODE, and every judgement that needs real pixels, belongs to
    `forge_headless/input_assets.py`. Declared dimensions are carried and
    deliberately not believed; they exist so a mismatch can be refused by name
    instead of crashing inside the sampler.
    """

    from .contracts import InputAsset

    if value is None:
        return None
    if isinstance(value, str):
        # The Canvas's own shape: a bare data URL. Normalised here so exactly
        # one form reaches everything downstream.
        value = {"data_url": value}
    if not isinstance(value, Mapping):
        raise PresentationError(f"{role} must be a JSON object or a data URL.")
    unexpected = sorted(str(key) for key in value if key not in _ASSET_FIELDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: {role}.{unexpected[0]}.")

    data_url = str(value.get("data_url") or "")
    if not data_url:
        raise PresentationError(f"{role} carries no image data.")
    if not data_url.startswith("data:"):
        # The whole point of the rule. A browser that can name a server file
        # is a browser that can read one, and no amount of downstream checking
        # recovers from accepting the name in the first place.
        raise PresentationError(
            f"{role} must be inline image data, not a path or URL.")

    numbers = {}
    for name in ("width", "height", "byte_length"):
        raw = value.get(name, 0)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise PresentationError(f"{role}.{name} must be an integer.")
        if raw < 0:
            raise PresentationError(f"{role}.{name} must not be negative.")
        numbers[name] = raw

    return InputAsset(
        data_url=data_url,
        media_type=str(value.get("media_type") or ""),
        origin=str(value.get("origin") or "canvas"),
        content_hash=_asset_content_hash(data_url),
        **numbers)


def _asset_content_hash(data_url: str) -> str:
    """sha256 of the decoded payload, or "" if it will not decode.

    `hashlib` is stdlib, so this does not touch the ban `forge_studio` is under
    -- that ban is on PIL, torch, numpy and gradio, and hashing bytes needs
    none of them. The DECODE for verification still belongs to
    `forge_headless`; this only establishes identity.

    Over the decoded bytes rather than the string, so the same pixels sent with
    a different base64 padding or media-type prefix produce the same identity.
    A payload that will not decode returns "" and is left to the headless layer
    to refuse by name -- computing identity is not the place to invent a second
    validation error.
    """

    import base64
    import binascii
    import hashlib

    _, _, payload = data_url.partition(",")
    if not payload:
        return ""
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return ""
    return hashlib.sha256(raw).hexdigest() if raw else ""


def _validated_operation(value: Any) -> str:
    if value is None:
        return "txt2img"
    if not isinstance(value, str) or value not in _OPERATIONS:
        raise PresentationError(
            f"operation must be one of {', '.join(_OPERATIONS)}.")
    return value


def _validated_denoise(value: Any) -> float:
    if value is None:
        return 0.75
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresentationError("denoising_strength must be a number.")
    if not 0.0 <= float(value) <= 1.0:
        raise PresentationError(
            "denoising_strength must be between 0 and 1.")
    return float(value)


def _validated_clip_skip(value: Any) -> int | None:
    """Clip Skip, or None when the job does not name one.

    Bounds are the ENGINE'S OWN, not Studio's: `CLIP_stop_at_last_layers` is
    declared in `modules/shared_options.py` as a 1..12 slider with a default of
    2, where 1 disables and 2 skips one layer. AR6.4's standing rule is that
    every control range must be one the backend honours, so the range is read
    off the engine rather than invented here.

    None means the request says nothing about Clip Skip, and the engine keeps
    whatever its option already holds. That is what makes this field additive:
    a client that never sends it produces the same image it produced before the
    field existed.

    `bool` is rejected before `int` because `True` is an `int` in Python and
    would otherwise be accepted as Clip Skip 1 -- the same trap `_bounded_int`
    guards with `type(value) is not int`.
    """

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PresentationError("clip_skip must be an integer.")
    if not 1 <= value <= 12:
        raise PresentationError("clip_skip must be between 1 and 12.")
    return int(value)


def _validated_inpaint(value: Any) -> Any:
    """The ordinary inpaint group, or None. Bounds are Neo's own."""

    from .contracts import InpaintSettings

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PresentationError("inpaint must be a JSON object.")
    unexpected = sorted(str(key) for key in value if key not in _INPAINT_FIELDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: inpaint.{unexpected[0]}.")

    settings: dict[str, Any] = {}
    # `padding` is 0-512, matching the control. The engine's slider stops at
    # 256, but only-masked padding is a crop MARGIN with no mathematical
    # bound -- a bigger number simply crops less tightly, up to the whole
    # image. The control has always offered 512.
    for name, ceiling in (("mask_blur", 64), ("padding", 512)):
        raw = value.get(name)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise PresentationError(f"inpaint.{name} must be an integer.")
        if not 0 <= raw <= ceiling:
            raise PresentationError(
                f"inpaint.{name} must be between 0 and {ceiling}.")
        settings[name] = raw

    fill = value.get("fill")
    if fill is not None:
        # Neo's four modes. A fifth would be silently ignored by the engine,
        # which is the one outcome this contract refuses everywhere else.
        if isinstance(fill, bool) or not isinstance(fill, int):
            raise PresentationError("inpaint.fill must be an integer.")
        if fill not in (0, 1, 2, 3):
            raise PresentationError(
                "inpaint.fill must be 0 (fill), 1 (original), "
                "2 (latent noise) or 3 (latent nothing).")
        settings["fill"] = fill

    for name in ("full_resolution", "invert"):
        raw = value.get(name)
        if raw is None:
            continue
        if not isinstance(raw, bool):
            raise PresentationError(f"inpaint.{name} must be true or false.")
        settings[name] = raw

    settings["soft"] = _validated_soft_inpaint(value.get("soft"))
    return InpaintSettings(**settings)


#: A document id is 32 hex characters and nothing else. Anchored, so a value
#: carrying a path separator, a drive letter or a filename cannot match.
_DOCUMENT_ID = re.compile(r"\A[0-9a-f]{32}\Z")


def _validated_document(value: Any) -> Any:
    """The Canvas document and revision a job captured, or None.

    None is legitimate: an API caller has no Canvas. What is NOT legitimate is
    a malformed id or a negative revision, because both would make the pair
    useless for the one thing it exists to do -- telling a queued job apart
    from the document it was built on.

    The id is checked against a strict 32-hex pattern rather than merely being
    non-empty. That is what keeps identity from becoming a filesystem channel:
    a title, a filename or a path cannot satisfy it, so none can arrive here
    and be stored on a job or echoed into a result.
    """

    from .contracts import CanvasDocument

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PresentationError("document must be a JSON object.")
    unexpected = sorted(str(key) for key in value
                        if key not in ("document_id", "revision"))
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: document.{unexpected[0]}.")

    document_id = value.get("document_id")
    if not isinstance(document_id, str) or not _DOCUMENT_ID.match(document_id):
        raise PresentationError(
            "document.document_id must be 32 hexadecimal characters.")

    revision = value.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise PresentationError("document.revision must be an integer.")
    if revision < 0:
        raise PresentationError("document.revision must not be negative.")

    return CanvasDocument(document_id=document_id, revision=revision)


def _validated_soft_inpaint(value: Any) -> Any:
    """Gradual boundary blending, or None when it was not asked for.

    ABSENT MEANS ABSENT, as with `hires`, `variation` and `aspect`. With no
    group the engine keeps its own behaviour -- including the latent mask
    rounding at `processing.py:1892` that this feature exists to switch off --
    so an omitted group is byte-identical to a pre-WP1.6 job rather than
    quietly asserting six defaults over it.

    The bounds are the engine's sliders, not Studio's opinion.
    """

    from .contracts import SoftInpainting

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PresentationError("inpaint.soft must be a JSON object.")
    unexpected = sorted(str(key) for key in value
                        if key not in _SOFT_INPAINT_BOUNDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: inpaint.soft.{unexpected[0]}.")

    settings: dict[str, Any] = {}
    for name, (low, high) in _SOFT_INPAINT_BOUNDS.items():
        raw = value.get(name)
        if raw is None:
            continue
        # bool first: `True` is an int in Python, and a checkbox arriving where
        # a slider belongs is a page defect worth naming rather than reading
        # as 1.0.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PresentationError(f"inpaint.soft.{name} must be a number.")
        if not low <= float(raw) <= high:
            raise PresentationError(
                f"inpaint.soft.{name} must be between {low:g} and {high:g}.")
        settings[name] = float(raw)

    return SoftInpainting(**settings)


def _enforce_operation_invariants(operation: str, source: Any,
                                  mask: Any) -> None:
    """Refuse an incoherent combination BY NAME, before anything is admitted.

    The alternative -- inferring the operation from whether a source happens to
    be present -- is what the live port does today, and it is why a txt2img
    request that carried a stray source would quietly become an img2img job.
    An owner who asks for one thing and supplies the input for another has made
    a mistake worth telling them about.
    """

    if operation == "txt2img":
        if source is not None:
            raise PresentationError(
                "A source image was supplied for a text-to-image job.")
        if mask is not None:
            raise PresentationError(
                "A mask was supplied for a text-to-image job.")
        return
    if source is None:
        raise PresentationError(
            f"{operation} requires a source image.")
    if operation == "img2img" and mask is not None:
        raise PresentationError(
            "A mask was supplied for an image-to-image job. Use inpaint.")
    if operation == "inpaint" and mask is None:
        raise PresentationError("inpaint requires a mask.")


def _validated_variation(value: Any) -> Any:
    """One variation group, or None when the owner did not ask for one.

    Absent means absent. With no `variation` key nothing downstream sets a
    subseed, so the base pass stays byte-identical to what it was before this
    field existed -- the same acceptance criterion Hires and Auto Detail were
    each held to.

    Ranges are Neo's. `subseed_strength` is 0..1; a subseed outside the 32-bit
    seed space is refused for the same reason `seed` is, since Neo draws both
    from the same generator.
    """

    if value is None:
        return None
    from .contracts import VariationSettings

    if not isinstance(value, Mapping):
        raise PresentationError("variation must be a JSON object.")
    unexpected = sorted(str(key) for key in value if key not in _VARIATION_FIELDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: variation.{unexpected[0]}."
        )

    subseed = value.get("subseed", -1)
    if not isinstance(subseed, int) or isinstance(subseed, bool):
        raise PresentationError("variation.subseed must be an integer.")
    if subseed < -1:
        raise PresentationError(
            "variation.subseed must be -1 or a non-negative whole number."
        )

    strength = value.get("subseed_strength", 0.0)
    if isinstance(strength, bool) or not isinstance(strength, (int, float)):
        raise PresentationError("variation.subseed_strength must be a number.")
    if not (0.0 <= float(strength) <= 1.0):
        raise PresentationError(
            "variation.subseed_strength must be between 0 and 1."
        )

    def _resize(name: str) -> int:
        raw = value.get(name, -1)
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise PresentationError(f"variation.{name} must be an integer.")
        # Neo gates the pair on `<= 0`, so 0 and -1 both mean "unset" and
        # neither is an error. Anything above that is a real dimension and is
        # bounded like width/height are.
        if raw > 0 and not (64 <= raw <= 8192):
            raise PresentationError(
                f"variation.{name} must be 0 to disable, or between 64 and 8192."
            )
        return raw

    return VariationSettings(
        subseed=subseed,
        subseed_strength=float(strength),
        seed_resize_from_w=_resize("seed_resize_from_w"),
        seed_resize_from_h=_resize("seed_resize_from_h"),
    )


def _preview_flag(value: Any) -> bool:
    """The Live Preview toggle, as a bool, from whatever the client sent."""

    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _number(value: Any, field: str, low: float, high: float) -> float:
    """One bounded number, refused rather than clamped.

    Clamping would run a generation the owner did not ask for and report
    success, which is the same class of lie as a control that does nothing.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresentationError(f"{field} must be a number.")
    number = float(value)
    if not low <= number <= high:
        raise PresentationError(
            f"{field} must be between {low} and {high}; {number} was requested."
        )
    return number


def _whole(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PresentationError(f"{field} must be a whole number.")
    if not low <= value <= high:
        raise PresentationError(f"{field} must be {low}-{high}.")
    return value


def _text(value: Any, field: str, *, limit: int | None = None) -> str:
    """One text field. `limit` is for IDENTIFIERS, not for prose.

    The default was 4000 and applied to Auto Detail's per-slot prompts,
    which are prose and have no business being bounded. Callers that name a
    limit are naming one for a NAME -- a detector id, a model id -- where a
    bound is about what the value IS rather than about how much an owner is
    allowed to say.
    """

    if value is None:
        return ""
    if not isinstance(value, str):
        raise PresentationError(f"{field} must be text.")
    trimmed = value.strip()
    if limit is not None and len(trimmed) > limit:
        raise PresentationError(
            f"{field} must be at most {limit} characters.")
    return trimmed


def _validated_auto_detail(value: Any) -> Any:
    """The Auto Detail group, or None when the owner did not ask for one.

    Absent means absent, exactly as it does for Hires: with no `auto_detail`
    key nothing downstream sets a single Auto Detail field and the result is
    byte-identical to a pre-P0.8 one. So this returns None rather than a
    disabled settings object.

    A slot's `detector` is a catalogue NAME and is only shape-checked here.
    Membership is checked where the catalogue actually is -- the same split
    sampler and scheduler already use, and the reason a name that is merely
    well-formed cannot reach a detector load.
    """

    if value is None:
        return None
    from .contracts import (
        MAX_AUTO_DETAIL_SLOTS,
        AutoDetailSettings,
        AutoDetailSlot,
    )

    if not isinstance(value, Mapping):
        raise PresentationError("auto_detail must be a JSON object.")
    unexpected = sorted(str(key) for key in value if key not in _AUTO_DETAIL_FIELDS)
    if unexpected:
        raise PresentationError(
            f"Unsupported generation field: auto_detail.{unexpected[0]}."
        )

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PresentationError("auto_detail.enabled must be true or false.")

    raw_slots = value.get("slots", [])
    if not isinstance(raw_slots, (list, tuple)):
        raise PresentationError("auto_detail.slots must be a list.")
    if len(raw_slots) > MAX_AUTO_DETAIL_SLOTS:
        # Refused rather than truncated. Three is the pipeline contract, and
        # silently dropping a fourth would run something other than what was
        # asked for while reporting success.
        raise PresentationError(
            f"auto_detail.slots may hold at most {MAX_AUTO_DETAIL_SLOTS} slots; "
            f"{len(raw_slots)} were sent."
        )

    slots: list[Any] = []
    for position, raw in enumerate(raw_slots, start=1):
        where = f"auto_detail.slots[{position}]"
        if not isinstance(raw, Mapping):
            raise PresentationError(f"{where} must be a JSON object.")
        unknown = sorted(
            str(key) for key in raw if key not in _AUTO_DETAIL_SLOT_FIELDS
        )
        if unknown:
            raise PresentationError(
                f"Unsupported generation field: {where}.{unknown[0]}."
            )
        slot_enabled = raw.get("enabled", False)
        if not isinstance(slot_enabled, bool):
            raise PresentationError(f"{where}.enabled must be true or false.")

        cfg = _number(raw.get("cfg", 0.0), f"{where}.cfg", 0.0, 24.0)
        if cfg != 0.0 and cfg < 1.0:
            raise PresentationError(
                f"{where}.cfg must be 0 (inherit the base CFG) or "
                "between 1.0 and 24.0."
            )
        minimum = _number(raw.get("min_ratio", 0.0), f"{where}.min_ratio", 0.0, 1.0)
        maximum = _number(raw.get("max_ratio", 1.0), f"{where}.max_ratio", 0.0, 1.0)
        if minimum > maximum:
            raise PresentationError(
                f"{where}.min_ratio must not exceed {where}.max_ratio."
            )

        slots.append(
            AutoDetailSlot(
                enabled=bool(slot_enabled),
                detector=_text(raw.get("detector", ""), f"{where}.detector", limit=200),
                confidence=_number(
                    raw.get("confidence", 0.3), f"{where}.confidence", 0.0, 1.0
                ),
                top_k=_whole(raw.get("top_k", 0), f"{where}.top_k", 0, 100),
                min_ratio=minimum,
                max_ratio=maximum,
                dilate_erode=_whole(
                    raw.get("dilate_erode", 4), f"{where}.dilate_erode", -256, 256
                ),
                mask_blur=_whole(raw.get("mask_blur", 6), f"{where}.mask_blur", 0, 256),
                denoising_strength=_number(
                    raw.get("denoising_strength", 0.30),
                    f"{where}.denoising_strength", 0.0, 1.0,
                ),
                prompt=_text(raw.get("prompt", ""), f"{where}.prompt"),
                negative_prompt=_text(
                    raw.get("negative_prompt", ""), f"{where}.negative_prompt"
                ),
                inpaint_padding=_whole(
                    raw.get("inpaint_padding", 32), f"{where}.inpaint_padding", 0, 256
                ),
                # 0 means "inherit the base pass"; no ceiling above it.
                steps=_whole(raw.get("steps", 0), f"{where}.steps",
                             0, UNBOUNDED),
                cfg=cfg,
            )
        )

    return AutoDetailSettings(enabled=bool(enabled), slots=tuple(slots))


#: The output formats this product writes. Studio's names; the encoder's
#: spellings live in `forge_headless/live_generation_port.py::OUTPUT_FORMATS`,
#: which is the one module allowed to hold them.
_OUTPUT_FORMATS = ("png", "jpeg", "webp")


def _validated_output(value: Any) -> Any:
    """One output group, or None for "written as it always was".

    Absent means THE DEFAULTS, and this is the one place in the phase where
    that is not the same as "byte-identical to what it was before the field
    existed". It used to be: AR4.9 returned None here so a default install
    wrote exactly the bytes it always had.

    `embed_metadata` broke that tie deliberately. It defaults ON, because the
    Settings toggle ships lit and the Extension embeds by default, so a default
    PNG now gains a `parameters` chunk that it did not carry before. Returning
    None still means "the defaults" -- the writer reads the same True through
    `getattr(options, "embed_metadata", True)` -- so the payload stays empty on
    a default install and only the BYTES changed, which is the whole point of
    the feature.

    An UNKNOWN format is REFUSED. Coercing it to PNG would recreate, one layer
    down, exactly the defect this field exists to fix -- an owner asking for
    one thing and silently receiving another.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PresentationError("output settings must be a JSON object.")
    unknown = sorted(str(k) for k in value if k not in
                     ("format", "quality", "lossless", "embed_metadata"))
    if unknown:
        raise PresentationError(
            f"Unknown output settings: {', '.join(unknown)}.")

    fmt = str(value.get("format", "png") or "png").strip().lower()
    if fmt not in _OUTPUT_FORMATS:
        raise PresentationError(
            f"{fmt!r} is not an output format Studio can write; "
            f"expected one of {', '.join(_OUTPUT_FORMATS)}.")

    quality = value.get("quality", 92)
    if isinstance(quality, bool) or not isinstance(quality, int):
        raise PresentationError("output quality must be a whole number.")
    if not 1 <= quality <= 100:
        raise PresentationError("output quality must be between 1 and 100.")

    lossless = value.get("lossless", False)
    if not isinstance(lossless, bool):
        raise PresentationError("output lossless must be true or false.")

    embed_metadata = value.get("embed_metadata", True)
    if not isinstance(embed_metadata, bool):
        raise PresentationError(
            "output embed_metadata must be true or false.")

    from .contracts import OutputSettings

    return OutputSettings(format=fmt, quality=quality, lossless=lossless,
                          embed_metadata=embed_metadata)


def _validated_hires(value: Any) -> Any:
    """One Hires group, or None when the owner did not ask for one.

    Absent means absent, and that is the phase's first acceptance criterion:
    with no `hires` key the base pass must be byte-identical to what it was
    before the field existed. So this returns None rather than a disabled
    settings object, and nothing downstream sets a single `hr_*` field.

    Numeric ranges are Neo's own, except `scale`, which is tighter by owner
    instruction -- see HIRES_SCALE_MIN/MAX.
    """

    if value is None:
        return None
    from .contracts import HiresSettings

    if not isinstance(value, Mapping):
        raise PresentationError("hires must be a JSON object.")
    unexpected = sorted(str(key) for key in value if key not in _HIRES_FIELDS)
    if unexpected:
        raise PresentationError(f"Unsupported generation field: hires.{unexpected[0]}.")

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise PresentationError("hires.enabled must be true or false.")

    # 2.0, which is what `contracts.py:640` declares and what the control
    # ships. This was HIRES_SCALE_MAX -- so a caller who omitted `scale`
    # silently got the MAXIMUM, and the product carried three different
    # defaults for one field.
    scale = value.get("scale", 2.0)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise PresentationError("hires.scale must be a number.")
    scale = float(scale)
    # Refused rather than clamped. Clamping would run a generation the owner
    # did not ask for and report success, which is the same class of lie as a
    # control that does nothing.
    if enabled and scale < HIRES_SCALE_MIN:
        raise PresentationError(
            f"hires.scale must be at least {HIRES_SCALE_MIN}; "
            f"{scale} was requested."
        )

    steps = value.get("second_pass_steps", 0)
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
        raise PresentationError(
            "hires.second_pass_steps must be 0 or more.")

    denoise = value.get("denoising_strength", 0.7)
    if (
        isinstance(denoise, bool)
        or not isinstance(denoise, (int, float))
        or not 0.0 <= float(denoise) <= 1.0
    ):
        raise PresentationError("hires.denoising_strength must be 0.0-1.0.")

    cfg = value.get("cfg", 0.0)
    if isinstance(cfg, bool) or not isinstance(cfg, (int, float)):
        raise PresentationError("hires.cfg must be a number.")
    cfg = float(cfg)
    # 0 inherits the base CFG. Above 0 the range is Neo's own UI range, whose
    # FLOOR is 1.0 -- a value between 0 and 1 is one the engine's own controls
    # cannot produce, so accepting it would mean Studio can ask for something
    # no Forge user can.
    # 1.0-30.0, matching Studio's BASE CFG control and admission. This was
    # 1.0-24.0 -- Neo's own slider maximum -- while `#paramHrCFG` advertised 30,
    # so an owner who set Hires CFG above 24 was refused a value the control
    # offered and the base pass accepts. CFG is a guidance multiplier with no
    # hard bound in the sampler; 24 is a slider stop, not a capability.
    if cfg != 0.0 and not 1.0 <= cfg <= 30.0:
        raise PresentationError(
            "hires.cfg must be 0 (inherit the base CFG) or between 1.0 and 30.0."
        )

    text: dict[str, str] = {}
    for name in ("upscaler", "sampler", "scheduler", "prompt", "negative_prompt"):
        raw = value.get(name, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            raise PresentationError(f"hires.{name} must be text.")
        text[name] = raw.strip()

    return HiresSettings(
        enabled=enabled,
        scale=scale,
        upscaler=text["upscaler"],
        second_pass_steps=steps,
        denoising_strength=float(denoise),
        sampler=text["sampler"],
        scheduler=text["scheduler"],
        prompt=text["prompt"],
        negative_prompt=text["negative_prompt"],
        cfg=cfg,
    )


class StudioPresentation:
    """JSON-facing adapter over the owned Studio application contract."""

    def __init__(
        self,
        application: Any,
        request_factory: Callable[..., Any],
        preferences: Any = None,
        wildcards: Any = None,
    ) -> None:
        self._application = application
        self._request_factory = request_factory
        #: Read at assembly for server-owned execution policy. Passed in
        #: rather than constructed here, and rather than reached for from
        #: `_validated_request_payload`, which validates owner-supplied
        #: generation fields and should not also read application state.
        self._preferences = preferences
        #: The wildcard service, for the same reason and on the same terms.
        #: Optional: a host without one generates exactly as it did before,
        #: which is every mock and most tests.
        self._wildcards = wildcards
        #: WP1.2. Constructed here rather than injected, because unlike the two
        #: above it holds no owner configuration and reads no durable state --
        #: it is a bounded in-memory registry whose whole lifetime is this
        #: process. A host that never posts an asset never fills it.
        from .asset_service import AssetService

        self._assets = AssetService()

    @property
    def assets(self) -> Any:
        return self._assets

    def admit_asset(self, payload: Any) -> dict[str, Any]:
        """`POST /api/assets`: one inline image in, one opaque handle out.

        The browser may still send a data URL on the generate request --
        WP1.2 keeps inline as a normalisation path and this route is the
        explicit form of the same admission. Either way the bytes become an
        asset record before the job is admitted, and the response carries a
        handle and facts, never the payload back and never a path.
        """

        from .asset_service import Retention

        body = payload if isinstance(payload, Mapping) else {}
        role = str(body.get("role") or "source")
        retention = str(body.get("retention") or Retention.TRANSIENT.value)
        try:
            wanted = Retention(retention)
        except ValueError:
            raise PresentationError(
                f"{retention!r} is not a retention class.") from None
        asset = self._assets.admit(body.get("data_url"), role=role,
                                   retention=wanted)
        return asset.to_dict()

    def _server_execution_policy(self) -> dict[str, Any]:
        """Server-owned job policy, snapshotted now.

        Composed AFTER validation, not through it: these are not fields the
        owner typed into the generate form, and putting them in the payload
        allow-list would invite a browser to supply them.

        A store that cannot be read falls back to the same default an absent
        key gets. The alternative -- failing the generation because a
        preference file was unreadable -- would cost the owner the image over
        a setting.
        """
        requested = True
        store = self._preferences
        if store is not None:
            try:
                document = store.read()
            except Exception:  # noqa: BLE001 - a preference is not worth a job
                document = {}
            value = document.get("gpu_tile_compositing")
            if value is not None:
                requested = bool(value)
        return {"gpu_tile_compositing_requested": requested}

    #: Decorrelates the negative prompt's draw from the positive one. Without
    #: it, a template that puts the same token in both boxes draws the SAME
    #: line index for each, so "__colour__ hair" against "__colour__ eyes"
    #: always agrees -- which looks like a bug and is not one. The Extension
    #: uses this constant for the same reason; it is the golden-ratio word,
    #: chosen only because it scatters adjacent seeds well.
    _NEGATIVE_SEED_OFFSET = 0x9E3779B9

    def _resolve_prompts(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Expand wildcards, server-side, before the request is built.

        This is the whole fix for the defect that made the feature inert: the
        engine, the routes, the browser and the preview all worked, and nothing
        called `expand` on the path the Generate button uses. A token reached
        the sampler as the literal text `__colour__`.

        Composed AFTER validation and never THROUGH it, exactly like
        `_server_execution_policy`: the enabled flag and the wildcard folder
        are durable server state, not generation fields, so the payload is not
        consulted for either. A browser may preview an expansion. It does not
        get to be the authority on what the model was asked for.

        MUTATES the payload's prompts, and returns the record of what it did.
        Both halves are kept -- see `PromptExpansion`.

        Never raises into a generation. A wildcard is worth an unexpanded
        prompt; it is not worth the owner's image.
        """

        from .contracts import PromptExpansion

        original = str(payload.get("positive_prompt", "") or "")
        original_negative = str(payload.get("negative_prompt", "") or "")
        # The seed is resolved to a concrete number FIRST, and written back, so
        # a "-1" job expands reproducibly. Expanding against -1 would draw from
        # an unseeded RNG and record a seed that reproduces the sampling but
        # not the prompt -- the one combination that looks reproducible and is
        # not. The Extension resolves the seed before expanding for this reason.
        seed = payload.get("seed")
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = -1
        if seed < 0:
            seed = random.SystemRandom().randrange(0, 2 ** 32)
            payload["seed"] = seed

        service = self._wildcards
        record = PromptExpansion(
            original_prompt=original,
            original_negative_prompt=original_negative,
            resolved_prompt=original,
            resolved_negative_prompt=original_negative,
            seed=seed,
        )
        if service is None:
            return {"prompt_expansion": record}
        try:
            if not service.enabled():
                return {"prompt_expansion": record}
            positive = service.expand(original, seed)
            negative = service.expand(
                original_negative,
                (seed ^ self._NEGATIVE_SEED_OFFSET) & 0xFFFFFFFF,
            )
        except Exception:  # noqa: BLE001 - a wildcard is not worth the image
            return {"prompt_expansion": record}

        payload["positive_prompt"] = positive.text
        payload["negative_prompt"] = negative.text
        return {"prompt_expansion": PromptExpansion(
            original_prompt=original,
            original_negative_prompt=original_negative,
            resolved_prompt=positive.text,
            resolved_negative_prompt=negative.text,
            choices=tuple(f"{name}={value}" for name, value
                          in (*positive.choices, *negative.choices)),
            missing=tuple(sorted({*positive.missing, *negative.missing})),
            truncated=bool(positive.truncated or negative.truncated),
            warnings=tuple(sorted({*positive.warnings, *negative.warnings})),
            seed=seed,
        )}

    def backend_status(self) -> dict[str, Any]:
        status = _as_mapping(self._application.get_backend_status())
        state = _state_of(status)
        status["state"] = state
        status.setdefault(
            "label",
            status.get("backend_name", status.get("backend", state.title())),
        )
        return status

    def models(self) -> dict[str, Any]:
        models = _plain(self._application.list_models())
        if isinstance(models, dict) and "models" in models:
            return models
        if not isinstance(models, list):
            raise PresentationError(
                "Studio returned an invalid model catalog.",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return {"models": models}

    def load_model(self, model_id: str) -> dict[str, Any]:
        return _as_mapping(self._application.load_model(model_id))

    def unload_model(self) -> dict[str, Any]:
        return _as_mapping(self._application.unload_model())

    def current_model(self) -> dict[str, Any]:
        return _as_mapping(self._application.get_current_model())

    def capability(
        self,
        model_id: str,
        operation: str = "txt2img",
    ) -> dict[str, Any]:
        return _as_mapping(
            self._application.get_capability(model_id, operation)
        )

    def supported_generation_parameters(self) -> frozenset[str]:
        return self._application.supported_generation_parameters()

    def runtime_status(self) -> dict[str, Any]:
        """Report which backend is selected and whether it can generate.

        Pure read. Reports what the application actually knows: a backend that
        cannot establish whether Gradio is loaded reports ``"UNKNOWN"`` rather
        than ``False``. Never emits a path or a traceback.
        """

        reporter = getattr(self._application, "runtime_status", None)
        if callable(reporter):
            return _as_mapping(reporter())
        # An application with no headless integration is still reportable.
        # `gradio_imported` is OBSERVED from `sys.modules` rather than reported
        # as UNKNOWN. The earlier reasoning -- that Studio could only speak for
        # its own imports -- had it backwards: `sys.modules` is precisely the
        # whole-process answer, and an import Studio did not perform is the one
        # that matters.
        from forge_studio.application import gradio_in_process

        return {
            "selected_backend": "mock",
            "backend_selection_honoured": True,
            "headless_state": "not_selected",
            "legacy_compatibility_state": "disabled",
            "gradio_imported": gradio_in_process(),
            "model_loaded": bool(
                getattr(
                    self._application.get_current_model(), "loaded", False
                )
            ),
            "generation_available": True,
            "blocking_reason": "",
        }

    # -- model lifecycle surface (internal alpha) ---------------------------

    @property
    def _lifecycle(self):
        return getattr(self._application, "model_lifecycle", None)

    @property
    def lifecycle_available(self) -> bool:
        lifecycle = self._lifecycle
        return bool(lifecycle is not None
                    and getattr(lifecycle, "gates_generation", False))

    #: Guards the construction of `_coordinator`, and nothing else.
    #:
    #: On the CLASS rather than the instance on purpose. Several hosts and
    #: doubles reach this method on objects built through `__new__` or with a
    #: constructor that predates the field, and a lock created in `__init__`
    #: would be missing on exactly those. First construction is the only
    #: contended moment, so one lock for all presentations costs nothing.
    _coordinator_construction = threading.Lock()

    def _coordinator_or_none(self):
        """The ONE coordinator for this presentation. Built at most once.

        This was an unsynchronized lazy init, on a `ThreadingHTTPServer`.
        Three concurrent `/api/generate` calls each evaluated the None check
        before any of them assigned, so each built its OWN `JobCoordinator`
        with its own `_pending` list and its own `_admitted` slot. The last
        assignment won and the other coordinators became unreachable, taking
        their jobs with them.

        Live, from a cold start, 2026-08-10: three simultaneous submissions
        were all admitted in the same millisecond -- `depth=1` on every
        enqueue, because each queue contained only its own job -- two died on
        MODEL_ALREADY_LOADING because the real lifecycle is a genuine
        singleton, and afterwards `/api/jobs` listed ONE of the three. The
        other two were gone from every surface, uncancellable, while their
        worker threads ran on and one of them published a result nothing
        could observe.

        The admission logic was never at fault; it is correct and properly
        locked. It was being asked to serialize three jobs that were in three
        different queues. A second burst against the same live server, once a
        coordinator existed, serialized perfectly -- peak concurrency 1, all
        three visible, all three completed -- which is what identified the
        construction window rather than the queue as the cause.
        """

        if not self.lifecycle_available:
            return None
        coordinator = getattr(self, "_coordinator", None)
        if coordinator is not None:
            return coordinator
        with StudioPresentation._coordinator_construction:
            # Re-read INSIDE the lock. The whole defect is that the first read
            # can be stale by the time the caller acts on it.
            coordinator = getattr(self, "_coordinator", None)
            if coordinator is None:
                from .jobs import JobCoordinator

                coordinator = JobCoordinator(self._application)
                self._coordinator = coordinator
        return coordinator

    def _require_lifecycle(self):
        lifecycle = self._lifecycle
        if lifecycle is None:
            raise PresentationError(
                "Model lifecycle is not available on this host.",
                status=HTTPStatus.NOT_FOUND,
            )
        return lifecycle

    # `profiles`, `select_profile` and `lifecycle_load` are gone with the
    # routes that reached them (/api/profiles, /api/profiles/select,
    # /api/model/load) and with the service methods they called. Residency is
    # reached by generating, never by a handler of its own.

    def lifecycle_unload(self) -> dict[str, Any]:
        return self._require_lifecycle().unload()

    def resident_selection(self) -> dict[str, Any] | None:
        """Which components are ACTUALLY loaded, or None when nothing is.

        Read from the lifecycle, which owns residency. Never derived from a
        desired `ModelSelection`: the owner's choice and the engine's state are
        different questions, and answering the second with the first is how a
        control reports a model that was never loaded.

        Returns the selection's own safe projection -- three opaque ids and a
        fingerprint, never a path or a payload reference.

        A host with no lifecycle answers None rather than raising: this is a
        read used to fill a control, and a mock host having no residency is an
        honest answer, not an error.
        """

        lifecycle = self._lifecycle
        if lifecycle is None:
            return None
        state = lifecycle.state()
        resident = state.get("resident_selection")
        return dict(resident) if isinstance(resident, Mapping) else None

    def registries(self) -> dict[str, Any]:
        """What the engine will actually dispatch on.

        Empty when Neo is unreachable -- a mock host, or a machine with no
        engine -- rather than a plausible-looking list. An invented sampler
        name is the exact lie this route exists to remove, and a page that
        receives nothing can say so instead of offering a choice that will be
        silently ignored.
        """

        try:
            from forge_headless.neo_registries import read_registries

            return read_registries().to_dict()
        except BaseException:  # noqa: BLE001 - a UI list is never fatal
            # Every key `Registries.to_dict` produces, so the payload SHAPE is
            # the same whether the engine answered or not. A fallback that
            # omits keys makes the page's absent-list handling depend on which
            # branch it got, which is a second contract by accident.
            return {
                "samplers": [],
                "schedulers": [],
                "latent_upscalers": [],
                "image_upscalers": [],
                "upscaler_scan_complete": False,
                "available": False,
            }

    def model_state(self) -> dict[str, Any]:
        lifecycle = self._require_lifecycle()
        state = lifecycle.state()
        state["readiness"] = lifecycle.readiness()
        return state

    def submit_async(self, payload: Any) -> dict[str, Any]:
        """Queue a job under a public id that exists BEFORE backend work.

        The id is the lifecycle token, minted first and stable through
        queued/running/terminal -- which is what makes queued cancellation
        addressable over HTTP at all.
        """

        coordinator = self._coordinator_or_none()
        if coordinator is None:
            raise PresentationError(
                "Asynchronous jobs need the model lifecycle.",
                status=HTTPStatus.NOT_FOUND,
            )
        request_payload = _validated_request_payload(payload)
        request_payload.update(self._server_execution_policy())
        request_payload.update(self._resolve_prompts(request_payload))
        try:
            request = self._request_factory(**request_payload)
        except (TypeError, ValueError) as exc:
            raise PresentationError(str(exc) or "Invalid generation request.") from exc
        return coordinator.submit(request)

    def jobs(self) -> dict[str, Any]:
        coordinator = self._coordinator_or_none()
        return {"jobs": coordinator.list_jobs() if coordinator else []}

    # -- the owner-facing queue -------------------------------------------------
    #
    # A separate surface from `/api/jobs`, which is a flat list of every job
    # this process has seen and is the diagnostic read. This one answers the
    # four questions the panel exists for: what is running, what is waiting,
    # what finished, and what can I do about it.

    @staticmethod
    def detectors(registry: Any = None) -> dict[str, Any]:
        """The Auto Detail detector catalogue, as names.

        Answers on a cold server and loads nothing: enumeration reads file
        names, sizes and bytes-for-hashing, and never opens a weight. That is
        what keeps a `/api/detectors` poll from being the thing that drags a
        detector runtime into a Studio process.

        Takes the registry rather than reaching for one. It lives on the
        SERVER, via `model_root_settings`, because a host may serve Studio
        without configuring model roots at all -- and a presentation object
        that went looking for it would have to know that, which is the
        handler's business.
        """

        from .detector_catalogue import describe_catalogue, scan_configured_detectors

        if registry is None:
            return describe_catalogue(())
        return describe_catalogue(scan_configured_detectors(registry))

    def queue(self) -> dict[str, Any]:
        coordinator = self._coordinator_or_none()
        if coordinator is None:
            return {"running": None, "queued": [], "recent": [], "queue_depth": 0}
        return coordinator.queue_view()

    def running_job_id(self) -> str | None:
        """The public id of the executing job, for callers that need only that.

        The progress WebSocket is the caller. It gates every send on "is
        something running", and the only answer it had was the source
        adapter's own `_active` -- which the current product never sets.
        """

        coordinator = self._coordinator_or_none()
        if coordinator is None:
            return None
        return coordinator.running_job_id()

    def _require_coordinator(self):  # type: ignore[no-untyped-def]
        coordinator = self._coordinator_or_none()
        if coordinator is None:
            raise PresentationError(
                "The job queue needs the model lifecycle.",
                status=HTTPStatus.NOT_FOUND,
            )
        return coordinator

    def reorder_queue(self, payload: Any) -> dict[str, Any]:
        coordinator = self._require_coordinator()
        order = payload.get("order") if isinstance(payload, Mapping) else None
        if not isinstance(order, (list, tuple)):
            raise PresentationError("order must be a list of job ids.")
        if not all(isinstance(job_id, str) for job_id in order):
            raise PresentationError("order must be a list of job ids.")
        return coordinator.reorder(list(order))

    def remove_queued_job(self, job_id: str) -> dict[str, Any]:
        return self._require_coordinator().remove(job_id)

    def clear_queue(self) -> dict[str, Any]:
        """Remove the waiting jobs. The running one is deliberately spared."""

        return self._require_coordinator().clear_queued()

    def cancel_all_jobs(self) -> dict[str, Any]:
        """Clear the queue AND stop the running job. Named, not flagged."""

        return self._require_coordinator().cancel_all()

    def job_status(self, job_id: str) -> dict[str, Any]:
        """Status by PUBLIC id, falling back to backend ids for compat."""

        coordinator = self._coordinator_or_none()
        if coordinator is not None:
            try:
                record = coordinator.describe(job_id)
            except Exception as exc:  # noqa: BLE001 - unknown id falls through
                if getattr(exc, "error", None) is None:
                    raise
            else:
                backend_id = record.get("backend_job_id")
                if record["state"] == "completed" and backend_id:
                    merged = self.poll(backend_id, include_result=True)
                    merged["job_id"] = job_id
                    merged["state"] = record["state"]
                    # The completed answer is built from the BACKEND's poll
                    # object, so everything Studio itself recorded about the
                    # job was dropped at exactly the moment a reader wants it:
                    # a field on the coordinator's record was visible while the
                    # job ran and gone once it finished. Carried explicitly
                    # rather than by merging the whole record, because the two
                    # disagree about `state` and `progress` on purpose.
                    expansion = record.get("prompt_expansion")
                    if expansion is not None:
                        merged["prompt_expansion"] = expansion
                    return merged
                record.setdefault("progress", None)
                return record
        return self.poll(job_id)

    def submit(self, payload: Any) -> dict[str, Any]:
        request_payload = _validated_request_payload(payload)
        request_payload.update(self._server_execution_policy())
        request_payload.update(self._resolve_prompts(request_payload))
        try:
            request = self._request_factory(**request_payload)
        except (TypeError, ValueError) as exc:
            raise PresentationError(str(exc) or "Invalid generation request.") from exc

        submitted = self._application.submit_generation(request)
        response = _as_mapping(submitted)
        response["job_id"] = _job_id_of(submitted)
        response["state"] = _state_of(response)
        return response

    def poll(
        self,
        job_id: str,
        *,
        include_result: bool = True,
    ) -> dict[str, Any]:
        progress = self._application.poll_or_stream_progress(job_id)
        if isinstance(progress, Iterator):
            try:
                progress = next(progress)
            except StopIteration as exc:
                raise PresentationError(
                    "Studio returned an empty progress stream.",
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                ) from exc
        response = _as_mapping(progress)
        response["job_id"] = job_id
        response["state"] = _state_of(response)
        if include_result and response["state"] == "completed":
            result = _as_mapping(self._application.get_result(job_id))
            # Backend paths are internal ownership references. Delivery goes
            # through an opaque handle instead; the generic HTTP contract must
            # never disclose filesystem paths to the browser.
            result.pop("output_path", None)
            result.pop("metadata_path", None)
            asset = self.result_asset(job_id)
            if asset is not None:
                result["image_handle"] = asset.handle
                result["image_media_type"] = asset.media_type
                result["image_byte_length"] = asset.byte_length
            response["result"] = result
        return response

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        """The latest live-preview frame for one job, and its id.

        `getattr` rather than a direct call, matching `result_asset` below: the
        application object is duck-typed at this seam and several test doubles
        predate this method. A double that does not offer one is answered the
        same way a backend that cannot decode one is -- no frame.
        """

        getter = getattr(self._application, "preview_frame", None)
        if getter is None:
            return (0, None)
        try:
            frame_id, frame = getter(job_id)
        except Exception:  # noqa: BLE001 - a preview never breaks progress
            return (0, None)
        return int(frame_id or 0), frame if isinstance(frame, str) else None

    def result_asset(self, job_id: str) -> Any:
        """Return the opaque delivery handle for a completed job, if any."""

        getter = getattr(self._application, "result_asset", None)
        if getter is None:
            return None
        try:
            return getter(job_id)
        except Exception as exc:
            # A result that cannot be delivered must not break observation.
            # Owned failures carry a structured `error`; anything else is a
            # real defect and must keep propagating.
            if getattr(exc, "error", None) is None:
                raise
            return None

    def read_result_asset(self, handle: str) -> Any:
        return self._application.read_result_asset(handle)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """One cancel verb. Coordinator ids cover queued AND running jobs;
        backend ids keep the pre-existing behaviour for hosts without a
        lifecycle."""

        coordinator = self._coordinator_or_none()
        if coordinator is not None:
            try:
                return coordinator.cancel(job_id)
            except Exception as exc:  # noqa: BLE001 - unknown id falls through
                error = getattr(exc, "error", None)
                if error is None:
                    raise
                if getattr(error, "code", "") != "JOB_NOT_FOUND":
                    raise
        cancelled = self._application.cancel_generation(job_id)
        response = _as_mapping(cancelled)
        response["job_id"] = job_id
        response["state"] = _state_of(response)
        return response

    def shutdown(self) -> None:
        shutdown = getattr(self._application, "shutdown", None)
        if callable(shutdown):
            shutdown()


class _StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        presentation: StudioPresentation,
        *,
        model_root_settings: Any = None,
        filesystem_service: Any = None,
        preferences: Any = None,
        defaults: Any = None,
        state_root: Any = None,
        result_root: Any = None,
    ) -> None:
        from forge_studio.source_api_adapter import SourceFrontendAdapter

        self.presentation = presentation
        # Optional: hosts that do not configure model roots simply do not expose
        # the settings route, rather than exposing a route that refuses.
        self.model_root_settings = model_root_settings
        registry = getattr(model_root_settings, "registry", None)
        # Optional for the same reason, and separate from the settings object
        # so a host can serve the roots write without serving a directory
        # browser. Absent means the routes answer 404, not "disabled" -- a
        # host without the feature should look like a host without the
        # feature.
        self.filesystem_service = filesystem_service
        # Optional, and absent means memory-backed rather than missing: this
        # server is constructed by the demo path and by the browser-truth
        # harness as well as by `launch.py`, and only the launcher has resolved
        # a state root to keep documents on.
        # The Gallery, when this host has somewhere to keep an index.
        #
        # Absent without a state root rather than backed by a temporary one:
        # the demo path and the browser-truth harness both build this server,
        # and a Gallery that indexed into a directory it then discarded would
        # look like a Gallery that loses everything on restart. Constructing it
        # opens no database and reads no disk -- the store opens on the first
        # request that needs it.
        self.gallery_service = None
        if state_root:
            from forge_studio.gallery_service import GalleryService

            # The result root travels with it so the Gallery can adopt
            # Studio's own output directory as a scan folder (NG-1). Absent on
            # a host that owns no output directory, which is every host that is
            # not the launcher -- and absent means the Gallery simply adopts
            # nothing, exactly as before.
            self.gallery_service = GalleryService(
                state_root, result_root=result_root
            )
        # Built after the Gallery so a finished generation can tell it what it
        # produced. The adapter only ever WRITES there, and the write cannot
        # raise, so the Gallery has no way to affect whether a picture is made.
        self.source_adapter = SourceFrontendAdapter(
            presentation,
            model_roots=registry,
            preferences=preferences,
            defaults=defaults,
            gallery=self.gallery_service,
        )
        super().__init__(server_address, _StudioRequestHandler)

    def server_close(self) -> None:
        gallery = getattr(self, "gallery_service", None)
        if gallery is not None:
            # Closes the SQLite handle and stops any running scan. On Windows
            # a live handle keeps the file locked, so a harness that restarts
            # Studio in the same directory would fail to open it again.
            gallery.close()
        super().server_close()


class _StudioRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ForgeStudioAlphaS07"
    sys_version = ""

    #: Whether this request's body has already been taken off the socket.
    #: Per-request, and reset below, because the handler INSTANCE is reused for
    #: every request on a keep-alive connection -- a flag left standing from
    #: the previous request would suppress exactly the drain it exists to
    #: trigger.
    _body_consumed = False

    def handle_one_request(self) -> None:
        self._body_consumed = False
        super().handle_one_request()

    @property
    def _presentation(self) -> StudioPresentation:
        server = self.server
        if not isinstance(server, _StudioHTTPServer):
            raise RuntimeError("Studio handler is attached to an invalid server.")
        return server.presentation

    @property
    def _source_adapter(self) -> Any:
        server = self.server
        if not isinstance(server, _StudioHTTPServer):
            raise RuntimeError("Studio handler is attached to an invalid server.")
        adapter = getattr(server, "source_adapter", None)
        if adapter is None:
            from forge_studio.source_api_adapter import SourceFrontendAdapter

            adapter = SourceFrontendAdapter(server.presentation)
            server.source_adapter = adapter
        return adapter

    @property
    def _gallery(self) -> Any:
        server = self.server
        if not isinstance(server, _StudioHTTPServer):
            return None
        return getattr(server, "gallery_service", None)

    def _record_generation_metadata(self, status: Any) -> None:
        """Write the Gallery's own copy of what made this picture. AR5.4.

        THE TOGGLE GOVERNS THE FILE; THE DATABASE RECORDS REGARDLESS OF IT.
        That is the Extension's contract for `embed_metadata`, and it is what
        the owner asked about. `req.embed_metadata` appears only where
        `save_kwargs` is built (`studio_api.py:3227`, `:4542`, `:4558`,
        `:4568`) and in no Gallery-write condition anywhere; each write is
        gated on that image's infotext being non-empty (`:3266`, `:3342`,
        `:4578`). Neo agrees about its half -- `opts.enable_pnginfo` at
        `modules/images.py:589` and `:607` reaches only the file -- and has no
        database of its own to disagree about, so there is no second policy to
        invent.

        SAID PRECISELY, because an earlier draft of this docstring said "the
        database ALWAYS records" and the Extension does not. Its Gallery writes
        are NESTED INSIDE the save branches -- `if req.save_outputs and
        req.save_format == "png":` at `:3215`, the lossy `elif` at `:3279`, and
        `if req.save_outputs:` at `:4513` -- and the `else` at `:3353` returns
        base64 with no file, no hash and no row. So `save_outputs` gates both
        halves; `embed_metadata` gates only the file.

        Studio's write is unconditional and that is not yet a divergence:
        nothing under `forge_studio/` or `forge_headless/` has a `save_outputs`
        field at all, the page's toggle sits below the lifecycle `return`
        (`app.js:3279` against `:3143`), and Studio always writes the file. The
        day that control is wired, this write has to follow it --
        `ASaveOutputsToggleWouldChangeThisTests` in the AR5.4 suite fails on
        that day and says so.

        WHAT WAS BROKEN. `record_generation` had exactly one caller in the
        tree, `source_api_adapter.py:1375`, and it was dead twice over: guarded
        on `metadata["image_sha256"]`, whose only writer is
        `mock_backend.py:441`, and sitting inside `_canonical_result`, which
        only the blocking `SourceFrontendAdapter.generate` reaches. So no real
        generation has ever written a row. With Embed Metadata ON the scan
        parses the parameters back out of the file and nothing looks wrong;
        with it OFF the file carries nothing, the row that should have covered
        it was never written, and the owner's parameters are gone permanently.

        WHY THE HASH IS TAKEN FROM THE DELIVERED BYTES. `image_metadata` is
        keyed by content hash because the row is written before any scan has
        met the file, and `gallery_service.py:1623-1630` later links the two on
        equality of that value alone. The scan computes it from the file on
        disk; the bytes here ARE that file, resolved through the same registry
        the browser is served from. Hashing anything else -- the pre-encode
        image, an admission digest, the mock's SVG digest -- produces a row
        that is written, reports success, and never links, with no symptom
        until an owner looks for an old picture. The Extension takes the same
        care from the other side: it hashes the PIL image for PNG and
        RE-READS THE FILE for JPEG and WebP, because "post-encode pixels
        differ from the original" (`studio_api.py:3335-3341`).

        MARKED SEEN BEFORE THE WORK, not after. One attempt per job: reading
        and decoding the result costs real time, and a job whose asset cannot
        be read now will not become readable on the next poll.

        BEFORE `_note_generation_to_gallery`, deliberately. The notification
        starts a scan; a row that already exists is linked by that very scan
        (`gallery_service.py:1623`) instead of waiting for the next one.

        SAME LIMIT AS AR5.3, and it is worse here: this rides on the browser
        taking delivery, and a client that never polls loses the parameters
        rather than merely refreshing late. Closing that means moving the write
        to publication behind an injected sink -- recorded in
        `Evidence/source-review/AR5.4-gallery-metadata-write.md` §7.1 as an
        owner decision, not taken here.

        NEVER RAISES, and it is separate from the notification below so that
        neither can suppress the other.
        """

        try:
            if _state_of(status) != "completed":
                return
            if not isinstance(status, Mapping):
                return
            result = status.get("result")
            if not isinstance(result, Mapping):
                return
            raw_metadata = result.get("metadata")
            metadata = (
                dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
            )
            # The engine's own parameter string. A backend that produces none
            # has nothing to record, which is exactly how the Extension gates
            # it -- and is a different case from one that produced an empty
            # picture.
            infotext = str(metadata.get("infotext", "") or "")
            if not infotext:
                return
            job_id = str(status.get("job_id") or "")
            handle = str(result.get("image_handle") or "")
            if not job_id or not handle:
                return
            gallery = self._gallery
            if gallery is None:
                return
            server = self.server
            # On the SERVER and on its OWN set. A handler is built per request,
            # so a set on `self` would be empty every time; a set shared with
            # AR5.3's would let whichever ran first silence the other.
            seen = getattr(server, "_gallery_metadata_jobs", None)
            if seen is None:
                seen = set()
                server._gallery_metadata_jobs = seen
            if job_id in seen:
                return
            if len(seen) >= 4096:
                seen.clear()
            seen.add(job_id)
            payload = self._presentation.read_result_asset(handle)
            # Imported here rather than at module scope: `gallery_index` reaches
            # Pillow, and `forge_studio` is the purity-locked package -- a
            # child interpreter asserts what a bare `import forge_studio`
            # drags in (`test_import_boundaries.py:104`).
            from forge_studio.gallery_index import content_hash

            digest = content_hash(getattr(payload, "content", b""))
            if not digest:
                # Nothing Pillow can decode has no pixel identity, so a scan
                # could not index it either. The mock's SVG lands here.
                return
            # `metadata` as the settings mapping: where the infotext and the
            # engine disagree the engine wins, which is what
            # `gallery_actions._parsed_fields` is built to express -- a seed of
            # -1 in the prompt box became a real number by the time the picture
            # existed.
            gallery.record_generation(digest, infotext, metadata)
        except Exception:  # noqa: BLE001 - see the docstring
            return

    def _note_generation_to_gallery(self, status: Any) -> None:
        """Tell the Gallery a picture arrived, once per job.

        THE WIRE THAT WAS NEVER CONNECTED. `GalleryService.note_generation`
        exists, `AutoSync` behind it is careful and correct, and `gallery.js`
        has been listening on an SSE stream the whole time -- but the only
        caller was `source_api_adapter.py:1315`, inside `_canonical_result`,
        inside the BLOCKING `SourceFrontendAdapter.generate`. A real install
        posts `/api/generate` and polls this route instead, so that adapter is
        never entered and the notification never fired. The owner had to press
        Scan by hand for every image they made.

        The same shape as the lifecycle `return` in `app.js`, one layer down:
        `_infotext()` is stranded in the identical way, at `:1751`, called once
        from the same dead function.

        WHY HERE. This route is fetched exactly once per generation --
        `deliverResult` (`studio-model-controls.js:494`) is guarded by
        `state.delivered` (`:493`) -- so the natural shape is already one
        notification per image, and the guard below only has to hold the line
        against anything else that asks. `forge_headless` publishes the result
        earlier and more directly, and must not know the Gallery exists;
        `StudioPresentation` holds no reference to it.

        WHAT THIS DOES NOT COVER, stated because it is a real limit and not a
        detail: the notification rides on the browser taking delivery. A client
        that never polls is not covered, and picks the images up on its next
        scan. Every real install runs the poll loop regardless of which tab is
        showing, so the owner's case is covered; a headless one is not.

        NEVER RAISES. `note_generation` is documented not to, but this is on
        the response path for a picture that has already been made, and a
        Gallery that cannot refresh must not turn a finished generation into a
        failed request.
        """

        try:
            if _state_of(status) != "completed":
                return
            if not isinstance(status, Mapping) or not status.get("result"):
                return
            job_id = str(status.get("job_id") or "")
            if not job_id:
                return
            gallery = self._gallery
            if gallery is None:
                return
            server = self.server
            # Kept on the SERVER, not the handler: a handler is built per
            # request, so a set here would be empty every time and notify on
            # every poll -- which the debounce would absorb by resetting its
            # own quiet window, delaying the refresh to the 10 s deadline
            # instead of 1.5 s. Working, and slower for no reason.
            seen = getattr(server, "_gallery_noted_jobs", None)
            if seen is None:
                seen = set()
                server._gallery_noted_jobs = seen
            if job_id in seen:
                return
            # Bounded, and bounded by DISCARDING rather than by evicting. An
            # LRU would silently drop the oldest id and re-notify that job if
            # it were polled again; clearing outright is visible in the code
            # and costs one extra scan in a session that has generated 4096
            # images, which is the cheaper mistake.
            if len(seen) >= 4096:
                seen.clear()
            seen.add(job_id)
            gallery.note_generation()
        except Exception:  # noqa: BLE001
            return

    def _serve_gallery(self, method: str, payload: Any = None) -> bool:
        """Answer a Gallery route, if this is one and there is a Gallery.

        Returns False for anything else, so the ordinary dispatch continues.
        A host with no state root has no Gallery, and those routes fall
        through to the 404 they had before -- which is what the frontend
        already knows how to report.
        """

        gallery = self._gallery
        if gallery is None or not gallery.handles(self.path):
            return False
        if method == "GET" and self._path().endswith("/gallery/events"):
            self._stream_gallery_events(gallery)
            return True
        reply = (
            gallery.get(self.path) if method == "GET"
            else gallery.post(self.path, payload)
        )
        if reply.body is not None:
            self._send_bytes(
                HTTPStatus(reply.status),
                reply.media_type or "application/octet-stream",
                reply.body,
                extra_headers=reply.headers,
            )
        else:
            self._send_json(HTTPStatus(reply.status), reply.payload)
        return True

    def _stream_gallery_events(self, gallery: Any) -> None:
        """Hold an SSE connection open for as long as the page wants it.

        Safe here because the server is a `ThreadingHTTPServer` with daemon
        threads: this occupies one connection thread, and shutdown does not
        wait for it.

        `Connection: close` and no `Content-Length`, because a stream has no
        length -- under HTTP/1.1 keep-alive the client would otherwise wait for
        a body that never ends and never fire a single listener.
        """

        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Named explicitly: a reverse proxy that buffers this stream turns a
        # live channel into a connection that appears to hang.
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            for chunk in gallery.events():
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            # The owner closed the tab. Not an error worth reporting.
            return

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'unsafe-hashes' {_SOURCE_LOADER_CSP_HASH} "
            f"{_UPDATE_HANDLER_CSP_HASH}; "
            "font-src 'self'; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
            "object-src 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _if_none_match(self, etag: str) -> bool:
        """Does the client already hold exactly this representation?

        `If-None-Match` is a comma-separated LIST, `*` matches anything the
        server has, and a `W/` prefix marks a weak tag. The comparison a
        conditional GET is defined to use is the WEAK one -- drop the prefix,
        then compare. A bare `==` against the raw header is the mutation that
        costs nothing visible: a client that sends two tags, or a proxy that
        weakens one, simply gets a full 200 carrying the correct bytes. Nothing
        is ever WRONG, revalidation just quietly stops happening, and no symptom
        points back at this line.

        `headers` is read defensively because the contract tests build handlers
        with `object.__new__` -- those never ran
        `BaseHTTPRequestHandler.__init__` and have no such attribute.
        """

        headers = getattr(self, "headers", None)
        raw = headers.get("If-None-Match") if headers else None
        if not raw:
            return False
        wanted = etag[2:] if etag.startswith("W/") else etag
        for candidate in raw.split(","):
            candidate = candidate.strip()
            if candidate == "*":
                return True
            if candidate.startswith("W/"):
                candidate = candidate[2:]
            if candidate == wanted:
                return True
        return False

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        content: bytes,
        *,
        cache: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        extra = dict(extra_headers or {})
        # THE VALIDATOR IS HASHED FROM THE BYTES ALREADY IN HAND, never from
        # `stat()`. An mtime-or-size tag can repeat across two DIFFERENT
        # contents -- `Evidence/u3r2f-owner/harness.py restore` rewrites the
        # recorded bytes and rewinds `debug.js` to a state it held before, and
        # two writes inside one filesystem tick share a stamp. A repeated
        # validator on changed content is a PERMANENT 304, which is the defect
        # below again: unbounded instead of 300 seconds, and silent instead of
        # merely slow. Hashing the response body cannot do that, because the tag
        # and the body are the same object.
        #
        # Computed HERE rather than in `_serve_static` on purpose:
        # `test_s06_frontend_contract.py:454` stubs this method with the exact
        # signature `(status, content_type, content, *, cache=False)` and NO
        # `**kwargs`, so passing `extra_headers=` from the caller would raise
        # `TypeError` there. Keeping it inside leaves `_serve_static`
        # byte-identical and that stub never sees the change.
        if cache and status == HTTPStatus.OK and "ETag" not in extra:
            extra["ETag"] = f'"{hashlib.sha256(content).hexdigest()}"'
        # A caller that set its own Cache-Control means it: a thumbnail is
        # immutable for its ETag and should be cached for a week, which the
        # default here would otherwise overwrite with `no-store`.
        #
        # `no-cache` REPLACED `public, max-age=300`, and it is NOT `no-store`:
        # the browser still STORES the file, it just has to ask before reusing
        # it. `max-age` was the whole defect. A response inside its freshness
        # window is reused WITHOUT ASKING, so an edited file on disk stayed
        # invisible behind the unchanged `?v=4.17.0` URL until the window
        # lapsed -- and no validator could have changed that, because a
        # validator is only consulted once freshness has. Measured before the
        # change, over loopback against this handler: `debug.js` came back
        # `public, max-age=300` with no ETag and no Last-Modified, so the
        # browser had nothing to revalidate WITH and no reason to try.
        # The freshness directive is what makes the browser ask; the ETag above
        # is what makes asking cost a 304 instead of the file.
        #
        # `public` went with it. Studio answers 127.0.0.1 and localhost only, so
        # there is no shared cache to invite and `public` could only license one
        # to hoard a per-user copy it can never serve.
        #
        # NO `Last-Modified`, deliberately. HTTP-date is second-resolution, and
        # installing the harness and reloading is a sub-second edit -- so a
        # date-based validator would answer 304 for a file that had changed,
        # which is precisely the bug this replaces, wearing a different header.
        cache_control = extra.pop(
            "Cache-Control",
            "no-cache" if cache else "no-store",
        )
        etag = extra.get("ETag")
        if cache and etag is not None and self._if_none_match(etag):
            # A 304 carries no body, so it carries no `Content-Length` and no
            # `Content-Type`. `protocol_version` is HTTP/1.1, so the connection
            # is keep-alive, and a declared length with no bytes behind it
            # desynchronises the NEXT request on that connection -- the failure
            # then lands on some unrelated asset and gets diagnosed there.
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self._security_headers()
            self.send_header("Cache-Control", cache_control)
            for name, value in extra.items():
                self.send_header(name, value)
            self.end_headers()
            return
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        self._drain_request_body()
        content = (
            json.dumps(_plain(payload), sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        )
        self._send_bytes(status, "application/json; charset=utf-8", content)

    def _drain_request_body(self) -> None:
        """Consume an unread request body before answering.

        A refusal that answers WITHOUT reading the body leaves those bytes in
        the socket, and the next request on the same keep-alive connection is
        then parsed starting inside them -- so the failure lands on whatever
        the browser asks for next, not on the request that caused it. That is
        exactly what a 404 on a retired POST route was doing: the route
        refused correctly, and the following GET came back 501 "Unsupported
        method ('{}GET')".

        Done here rather than at each refusal, because there are many
        refusals -- untrusted host, cross-origin, missing token, unknown route
        -- and a drain that has to be remembered at each one is a drain that
        gets forgotten at the next one added.
        """

        if self._body_consumed or self.command in ("GET", "HEAD"):
            return
        self._body_consumed = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (ValueError, AttributeError):
            return
        # Bounded by the same limit a real read would enforce: a refusal must
        # not become a way to make the server read an unlimited body.
        remaining = min(max(length, 0), _MAX_REQUEST_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                return
            remaining -= len(chunk)

    def _read_json_bounded(self, *, maximum_bytes: int) -> Any:
        """Read a body that may legitimately be multi-MiB, refusing SAFELY.

        `_read_json` refuses an oversized body WITHOUT reading it, and the
        generic `_drain_request_body` is bounded at `_MAX_REQUEST_BYTES`. For a
        64 KiB route those agree. For a multi-MiB one they do not: the refusal
        would leave megabytes in the socket, and the NEXT request on the same
        keep-alive connection is then parsed starting inside them -- the exact
        "501 Unsupported method" symptom `_drain_request_body` documents,
        landing on whatever the browser asks for next.

        Draining megabytes on demand is not the fix either; that turns a
        refusal into a way to make the server read an unbounded body. So an
        over-limit request is refused AND the connection is closed, which makes
        the unread remainder harmless because there is no next request on it.
        """

        if self.headers.get_content_type() != "application/json":
            raise PresentationError("Expected an application/json request.")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise PresentationError("Invalid Content-Length header.") from exc
        if content_length < 0:
            raise PresentationError("Invalid Content-Length header.")
        if content_length > maximum_bytes:
            # Mark consumed so the generic drain does not attempt a partial
            # read on a connection that is about to close anyway.
            self._body_consumed = True
            self.close_connection = True
            raise PresentationError(
                "That image is larger than Studio will accept in one request.",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        body = self.rfile.read(content_length)
        self._body_consumed = True
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PresentationError("Request body is not valid UTF-8 JSON.") from exc

    def _send_pixel_bytes(self, pixels: Any) -> None:
        """Raw RGBA under the same inert policy result bytes get.

        These are decoded from attacker-influenceable input and served
        same-origin, so they carry the sandboxed, script-free policy rather
        than inheriting the page's. The dimensions travel in headers because
        the body is deliberately nothing but pixels -- the client asserts
        `width * height * 4` against the length it actually received, which is
        what makes a truncated response a refusal instead of a corrupt canvas.
        """

        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; sandbox",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("X-Width", str(pixels.width))
        self.send_header("X-Height", str(pixels.height))
        self.send_header("X-Color-Profile", pixels.profile_state)
        self.send_header("Content-Length", str(len(pixels.rgba)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(pixels.rgba)

    def _pixel_route(self, path: str) -> None:
        """Decode to sRGB RGBA on the server, from a HANDLE or from bytes.

        `/studio/image_pixels` takes `{"handle": "<opaque>"}` -- Studio's own
        result identifier, never a URL and never a filesystem path. The
        canonical extension took `{"source": "<url with /file=/abs/path>"}`,
        and porting that verbatim would have looked faithful while
        reintroducing browser-supplied filesystem paths, undoing the invariant
        `result_delivery` exists to hold.

        `/studio/import_pixels` takes `{"image_b64": "<data URL or base64>"}`
        for images that exist only in the browser.

        `/studio/sample_image_pixels` is the acceptance INSTRUMENT: the same
        decode, returning addressable pixel values as JSON. Acceptance for this
        path is "the raw path was actually exercised", and a picture that looks
        right proves nothing -- the fallback also produces a plausible image.
        Numbers can be compared; appearances cannot.

        Every failure is a refusal, never a traceback, and the client treats
        any non-200 as "fall back to <img> + drawImage". The fallback is
        preserved deliberately: a host without Pillow still shows the owner
        their picture, in the browser's colours, rather than nothing.
        """

        from .pixel_import import PixelImportError, decode_to_srgb_rgba, sample_grid

        payload = self._read_json_bounded(maximum_bytes=_MAX_PIXEL_REQUEST_BYTES)
        if not isinstance(payload, Mapping):
            raise PresentationError("The pixel request must be a JSON object.")

        if path == "/studio/import_pixels":
            data = self._decoded_image_upload(payload)
        else:
            data = self._result_bytes_for_handle(payload)

        try:
            pixels = decode_to_srgb_rgba(data)
        except PixelImportError as exc:
            raise PresentationError(str(exc)) from None

        if path == "/studio/sample_image_pixels":
            self._send_json(
                HTTPStatus.OK, self._pixel_samples(payload, pixels, sample_grid)
            )
            return
        self._send_pixel_bytes(pixels)

    @staticmethod
    def _pixel_samples(payload: Mapping[str, Any], pixels: Any, grid: Any) -> dict:
        """Ground truth for the acceptance instrument.

        Two shapes, because the diagnostic asks two different questions. Named
        points answer "is THIS pixel what the raw path says it is", which is
        how `debug.js` compares backend truth against what the canvas actually
        holds. A sparse grid answers "was the raw path exercised at all" without
        the caller having to know where to look.
        """

        requested = payload.get("samples")
        common = {
            "ok": True,
            "width": pixels.width,
            "height": pixels.height,
            "profile_state": pixels.profile_state,
            "byte_length": len(pixels.rgba),
        }
        if requested is None:
            step = payload.get("step", 8)
            if isinstance(step, bool) or not isinstance(step, int) or step < 1:
                raise PresentationError("step must be a positive integer.")
            return {**common, "samples": grid(pixels, step=step)}

        if not isinstance(requested, list):
            raise PresentationError("samples must be a list.")
        out = []
        for entry in requested:
            if not isinstance(entry, Mapping):
                raise PresentationError("Each sample must be a JSON object.")
            x, y = entry.get("x"), entry.get("y")
            for value in (x, y):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise PresentationError("Sample x and y must be integers.")
            # Out-of-range is reported, not refused: a diagnostic that dies on
            # one stale coordinate tells you nothing about the other nine.
            if not (0 <= x < pixels.width and 0 <= y < pixels.height):
                out.append({"label": entry.get("label"), "x": x, "y": y,
                            "in_bounds": False})
                continue
            offset = (y * pixels.width + x) * 4
            out.append(
                {
                    "label": entry.get("label"),
                    "x": x,
                    "y": y,
                    "in_bounds": True,
                    "r": pixels.rgba[offset],
                    "g": pixels.rgba[offset + 1],
                    "b": pixels.rgba[offset + 2],
                    "a": pixels.rgba[offset + 3],
                }
            )
        return {**common, "samples": out}

    def _result_bytes_for_handle(self, payload: Mapping[str, Any]) -> bytes:
        """The owned bytes behind one opaque handle. No path, ever."""

        handle = payload.get("handle")
        if not isinstance(handle, str) or not handle.strip():
            raise PresentationError("A result handle is required.")
        try:
            asset = self._presentation.read_result_asset(handle.strip())
        except PresentationError:
            raise
        except Exception:  # noqa: BLE001 - an unknown handle is a refusal
            raise PresentationError(
                "That result is not available.", status=HTTPStatus.NOT_FOUND
            ) from None
        return bytes(getattr(asset, "content", b"") or b"")

    @staticmethod
    def _decoded_image_upload(payload: Mapping[str, Any]) -> bytes:
        """Base64 (optionally a data URL) to bytes, refusing anything else."""

        encoded = payload.get("image_b64")
        if not isinstance(encoded, str) or not encoded.strip():
            raise PresentationError("An image payload is required.")
        text = encoded.strip()
        if text.startswith("data:"):
            _, _, text = text.partition(",")
            if not text:
                raise PresentationError("That data URL carries no image.")
        try:
            return base64.b64decode(text, validate=True)
        except Exception:  # noqa: BLE001
            raise PresentationError("The image payload is not valid base64.") from None

    def _send_result_bytes(self, media_type: str, content: bytes) -> None:
        """Send owned result bytes under a deliberately inert policy.

        Results are attacker-influenced content served same-origin. The page
        CSP would let an SVG behave as a document on direct navigation, so this
        response carries its own sandboxed, script-free policy instead. No
        filesystem path appears in any header.
        """

        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Type", media_type)
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _serve_result(self, path: str) -> bool:
        """Serve one opaque result handle, or report it missing."""

        parsed = urlsplit(str(path))
        if parsed.path != "/studio/file":
            return False
        handles = parse_qs(parsed.query, keep_blank_values=True).get("path", [])
        handle = handles[0] if handles else ""
        try:
            payload = self._presentation.read_result_asset(handle)
        except Exception as exc:
            owned = getattr(exc, "error", None)
            if owned is None:
                raise
            code = getattr(owned, "code", "")
            status = (
                HTTPStatus.GONE
                if code == "RESULT_GONE"
                else HTTPStatus.NOT_FOUND
            )
            self._send_json(status, {"error": _plain(owned)})
            return True
        self._send_result_bytes(payload.media_type, payload.content)
        return True

    def _send_api_error(self, exc: Exception) -> None:
        if isinstance(exc, PresentationError):
            self._send_json(exc.status, {"error": str(exc)})
            return

        owned_error = getattr(exc, "error", None)
        owned = _plain(owned_error if owned_error is not None else exc)
        if isinstance(owned, dict):
            status_value = owned.get("http_status", HTTPStatus.BAD_REQUEST)
            try:
                status = HTTPStatus(int(status_value))
            except (TypeError, ValueError):
                status = HTTPStatus.BAD_REQUEST
            message = owned.get("message", owned.get("error", str(exc)))
            body: dict[str, Any] = {"error": str(message), "detail": owned}
            # A refusal may carry keys the PAGE reads rather than a person --
            # lexicon.js reads `not_empty` and `file_count` off the top level
            # to offer "delete anyway". Nested under `detail` they are
            # invisible to it. Only an explicit `extra` is promoted, so a
            # refusal cannot leak its internals by accident.
            extra = owned.get("extra")
            if isinstance(extra, dict):
                body.update({str(key): value for key, value in extra.items()})
            self._send_json(status, body)
            return

        if isinstance(exc, (KeyError, LookupError)):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Studio job not found."})
            return
        self._send_json(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"error": "Studio request could not be completed."},
        )

    def _path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def _allowed_origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }

    def _is_allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in self._allowed_origins()

    def _is_allowed_origin_strict(self) -> bool:
        """As above, but a *missing* Origin is refused rather than allowed.

        The permissive form exists because Studio's own same-origin fetches and
        non-browser callers legitimately omit Origin. For the one route that
        accepts filesystem paths, that tolerance is not worth its cost: the
        owner's browser always sends Origin on a cross-origin POST, so requiring
        it costs a real client nothing and removes the "just omit the header"
        bypass entirely.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return False
        return origin in self._allowed_origins()

    @property
    def _settings(self) -> Any:
        server = self.server
        return getattr(server, "model_root_settings", None)

    def _is_allowed_host(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1:
            return False
        port = self.server.server_address[1]
        return hosts[0].strip().casefold() in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }

    def _reject_untrusted_host(self) -> bool:
        if self._is_allowed_host():
            return False
        self._send_json(
            HTTPStatus.FORBIDDEN,
            {"error": "The Studio Host header is not allowed."},
        )
        return True

    def _read_json(self, *, maximum_bytes: int = _MAX_REQUEST_BYTES) -> Any:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise PresentationError("Expected an application/json request.")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise PresentationError("Invalid Content-Length header.") from exc
        if content_length < 0 or content_length > maximum_bytes:
            raise PresentationError(
                "Request body exceeds the Alpha S0.7 size limit.",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        body = self.rfile.read(content_length)
        self._body_consumed = True
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PresentationError("Request body is not valid UTF-8 JSON.") from exc

    def _read_json_optional(self) -> Any:
        """Drain an optional body. Load/unload need no payload, but a client
        that sends an empty JSON object must not wedge the connection."""

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if 0 < content_length <= _MAX_REQUEST_BYTES:
            body = self.rfile.read(content_length)
            self._body_consumed = True
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
        return None

    def _read_source_json(self) -> Any:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise PresentationError("Invalid Content-Length header.") from exc
        if content_length == 0:
            return {}
        return self._read_json(maximum_bytes=_MAX_SOURCE_REQUEST_BYTES)

    def _static_filename(self, path: str) -> str | None:
        path = unquote(path)
        alias = _STATIC_ALIASES.get(path)
        if alias is not None:
            return alias
        prefix = "/studio/static/"
        if not path.startswith(prefix):
            return None
        relative = path.removeprefix(prefix).replace("\\", "/")
        resource = PurePosixPath(relative)
        if (
            not relative
            or resource.is_absolute()
            or any(part in {"", ".", ".."} for part in resource.parts)
        ):
            return None
        return resource.as_posix()

    def _serve_static(self, path: str) -> bool:
        filename = self._static_filename(path)
        if filename is None:
            return False
        root = _FRONTEND_DIRECTORY.resolve()
        candidate = (root / filename).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        try:
            content = candidate.read_bytes()
        except FileNotFoundError:
            return False
        except OSError:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Studio frontend resource is unavailable."},
            )
            return True
        content_type = _CONTENT_TYPES.get(
            candidate.suffix.casefold(),
            "application/octet-stream",
        )
        self._send_bytes(
            HTTPStatus.OK,
            content_type,
            content,
            cache=path not in _UNCACHED_STATIC_PATHS,
        )
        return True

    def _websocket_frame(self, opcode: int, payload: bytes = b"") -> bytes:
        first = 0x80 | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = bytes((first, length))
        elif length <= 0xFFFF:
            header = bytes((first, 126)) + struct.pack("!H", length)
        else:
            header = bytes((first, 127)) + struct.pack("!Q", length)
        return header + payload

    def _recv_exact(self, length: int) -> bytes | None:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.connection.recv(length - len(chunks))
            if not chunk:
                return None
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_websocket_frame(self) -> tuple[int, bytes] | None:
        header = self._recv_exact(2)
        if header is None:
            return None
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            extended = self._recv_exact(2)
            if extended is None:
                return None
            length = struct.unpack("!H", extended)[0]
        elif length == 127:
            extended = self._recv_exact(8)
            if extended is None:
                return None
            length = struct.unpack("!Q", extended)[0]
        if length > _MAX_SOURCE_REQUEST_BYTES:
            raise PresentationError(
                "WebSocket frame exceeds the Studio size limit.",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        mask = self._recv_exact(4) if masked else None
        if masked and mask is None:
            return None
        payload = self._recv_exact(length)
        if payload is None:
            return None
        if mask is not None:
            payload = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(payload)
            )
        return opcode, payload

    def _serve_websocket(self) -> None:
        if not self._is_allowed_origin():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Cross-origin Studio requests are not allowed."},
            )
            return
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if (
            self.headers.get("Upgrade", "").casefold() != "websocket"
            or "upgrade" not in self.headers.get("Connection", "").casefold()
            or self.headers.get("Sec-WebSocket-Version") != "13"
            or not key
        ):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "A valid Studio WebSocket upgrade is required."},
            )
            return

        accept = base64.b64encode(
            hashlib.sha1(
                f"{key}{_WEBSOCKET_GUID}".encode("ascii")
            ).digest()
        ).decode("ascii")
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True

        next_ping = time.monotonic() + 15.0
        next_progress = time.monotonic() + 0.1
        last_progress = b""
        try:
            while True:
                now = time.monotonic()
                timeout = max(
                    0.0,
                    min(next_ping, next_progress) - now,
                )
                readable, _, _ = select.select(
                    [self.connection],
                    [],
                    [],
                    min(timeout, 1.0),
                )
                if readable:
                    frame = self._read_websocket_frame()
                    if frame is None:
                        return
                    opcode, payload = frame
                    if opcode == 0x8:
                        self.connection.sendall(
                            self._websocket_frame(0x8, payload[:125])
                        )
                        return
                    if opcode == 0x9:
                        self.connection.sendall(
                            self._websocket_frame(0xA, payload[:125])
                        )
                now = time.monotonic()
                if (
                    now >= next_progress
                    and self._source_adapter.active_job_id is not None
                ):
                    progress = json.dumps(
                        self._source_adapter.websocket_status(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if progress != last_progress:
                        self.connection.sendall(
                            self._websocket_frame(0x1, progress)
                        )
                        last_progress = progress
                    next_progress = now + 0.1
                elif now >= next_progress:
                    next_progress = now + 0.25
                if now >= next_ping:
                    self.connection.sendall(self._websocket_frame(0x9))
                    next_ping = now + 15.0
        except (ConnectionError, OSError, PresentationError, socket.timeout):
            return

    def do_HEAD(self) -> None:
        if self._reject_untrusted_host():
            return
        path = self._path()
        if not self._serve_static(path):
            self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"")

    def do_GET(self) -> None:
        if self._reject_untrusted_host():
            return
        path = self._path()
        if path == "/studio/ws":
            self._serve_websocket()
            return
        if self._serve_static(path):
            return
        if path == "/favicon.ico":
            self._send_bytes(HTTPStatus.NO_CONTENT, "image/x-icon", b"")
            return
        try:
            # Result bytes are served before the generic /studio/ JSON
            # dispatch, which would otherwise JSON-encode them.
            if self._serve_result(self.path):
                return
            # Gallery routes, before the same generic dispatch, and for the
            # same reason: thumbnails and full images are bytes.
            if self._serve_gallery("GET"):
                return
            if path == "/studio/settings/model_roots":
                settings = self._settings
                if settings is None:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Model directory settings are unavailable."},
                    )
                    return
                # Same-origin read. A cross-origin page can issue this request
                # but cannot read the response, because Studio answers with no
                # permissive CORS headers -- which is also what keeps the token
                # below out of a hostile page's reach.
                document = settings.describe()
                document["token"] = settings.token
                self._send_json(HTTPStatus.OK, document)
                return
            if path == "/api/status":
                self._send_json(HTTPStatus.OK, self._presentation.backend_status())
                return
            if path == "/api/models":
                self._send_json(HTTPStatus.OK, self._presentation.models())
                return
            # /api/profiles is retired. There is no owner-facing profile: the
            # model is three catalogue selections carried on the generation
            # request. Leaving it answering would keep a parallel way to
            # describe "what model" alive beside the one the owner uses.
            if path == "/api/registries":
                self._send_json(HTTPStatus.OK, self._presentation.registries())
                return
            if path == "/api/model/state":
                self._send_json(HTTPStatus.OK, self._presentation.model_state())
                return
            if path == "/api/jobs":
                self._send_json(HTTPStatus.OK, self._presentation.jobs())
                return
            if path == "/api/queue":
                self._send_json(HTTPStatus.OK, self._presentation.queue())
                return
            if path == "/api/detectors":
                self._send_json(
                    HTTPStatus.OK,
                    self._presentation.detectors(
                        getattr(self._settings, "registry", None)
                    ),
                )
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                status = self._presentation.job_status(parts[2])
                # The metadata row FIRST, then the notification. The
                # notification starts a scan, and a row that already exists is
                # linked by that same scan rather than waiting for the next
                # one. Both are before the response is sent, so the scan is
                # already settling while the browser decodes the picture.
                #
                # COST, stated rather than waved at. `notify` sets two fields
                # and an event and is free. The metadata write reads the
                # result file and decodes it once to hash its pixels, and that
                # DOES delay this one reply -- once per job, on a file the OS
                # has just written, against a generation that took seconds.
                # The alternative is writing after the answer, which gives up
                # the ordering above and the guarantee that the row exists by
                # the time anything goes looking for it.
                self._record_generation_metadata(status)
                self._note_generation_to_gallery(status)
                self._send_json(HTTPStatus.OK, status)
                return
            if (
                path.startswith("/sdapi/v1/")
                or (
                    path.startswith("/studio/")
                    and not path.startswith("/studio/static/")
                )
            ):
                self._send_json(
                    HTTPStatus.OK,
                    self._source_adapter.get(self.path),
                )
                return
        except Exception as exc:
            self._send_api_error(exc)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Studio route not found."})

    # `_select_catalogue_models` and `_profile_repository` are gone with
    # /api/profiles/select_catalogue. They turned three role ids into a runtime
    # profile so that `select` and `load` had something to name -- two calls
    # that no longer exist. The three ids now travel on the generation request
    # and `ensure_loaded` resolves them, so there is nothing left to build in
    # advance. This was the last live import of `forge_studio.runtime_profile`.

    def _settings_writer(self):  # type: ignore[no-untyped-def]
        """The gate every config write passes. None means it already refused.

        Order is deliberate: refuse on identity before spending anything on the
        request, so an unauthorized caller never reaches parsing or the
        filesystem. Shared by both writes rather than copied, because a gate
        that exists twice is a gate that gets updated once.
        """
        from forge_studio.model_root_settings import CSRF_HEADER

        settings = self._settings
        if settings is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Model directory settings are unavailable."},
            )
            return None
        if not self._is_allowed_origin_strict():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {
                    "error": "Model directory settings require a same-origin "
                    "request with an Origin header."
                },
            )
            return None
        if not settings.token_matches(self.headers.get(CSRF_HEADER)):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Model directory settings require a valid token."},
            )
            return None
        return settings

    def _apply_model_root_settings(self) -> None:
        """The privileged write: it takes filesystem paths from the browser."""
        from forge_headless.contracts import HeadlessError

        settings = self._settings_writer()
        if settings is None:
            return
        payload = self._read_json()
        try:
            self._send_json(HTTPStatus.OK, settings.apply(payload))
        except HeadlessError as error:
            # A stable code, so the Settings page can say which folder failed in
            # owner wording. The message is Studio's own, never a raw traceback
            # and never a path.
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "Those model folders could not be used.",
                 "code": error.code},
            )

    def _filesystem_browse(self, path: str) -> None:
        """The directory picker, behind the same gate as the roots write.

        It reads rather than writes, so it is not privileged in the way that
        write is -- but it RETURNS FILESYSTEM STRUCTURE, which is a widening of
        a boundary the rest of the product holds absolutely. It therefore
        carries the strongest gate already in the codebase rather than a
        weaker one invented for reads: strict same-origin with an Origin
        header required, plus the process token. A cross-origin page cannot
        read the response anyway; the token is what stops a blind POST from
        walking the disk.
        """

        from forge_headless.contracts import HeadlessError

        settings = self._settings_writer()
        if settings is None:
            return
        service = getattr(self.server, "filesystem_service", None)
        if service is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Folder browsing is unavailable on this host."},
            )
            return

        action = path[len("/studio/fs/") :]
        payload = self._read_json_optional() or {}
        try:
            if action == "capabilities":
                self._send_json(HTTPStatus.OK, service.capabilities())
                return
            if action == "places":
                self._send_json(HTTPStatus.OK, service.places())
                return
            if action == "resolve":
                self._send_json(HTTPStatus.OK, service.resolve(payload))
                return
            if action == "list":
                self._send_json(HTTPStatus.OK, service.list(payload))
                return
            if action == "reveal":
                self._send_json(HTTPStatus.OK, service.reveal(payload))
                return
            if action == "diagnostics":
                self._send_json(HTTPStatus.OK, service.diagnostics())
                return
        except HeadlessError as error:
            # Owner wording plus a stable code. The message never contains a
            # path: the refusal says WHAT went wrong, and the page already
            # knows where it was.
            status = (
                HTTPStatus.SERVICE_UNAVAILABLE
                if error.code == "STUDIO_FS_BUSY"
                else HTTPStatus.BAD_REQUEST
            )
            self._send_json(
                status, {"error": "That folder could not be opened.",
                         "code": error.code}
            )
            return
        self._send_json(
            HTTPStatus.NOT_FOUND, {"error": "Studio route not found."}
        )

    def _remember_model_selection(self) -> None:
        """Store the dropdown choices for next launch. It loads nothing.

        Deliberately a separate route from generation. Folding "remember this"
        into `/api/generate` would put a config write on the hot path, where a
        read-only config directory would start failing generations for a
        reason the owner cannot connect to what they did.
        """
        from forge_headless.contracts import HeadlessError

        settings = self._settings_writer()
        if settings is None:
            return
        payload = self._read_json()
        try:
            self._send_json(HTTPStatus.OK, settings.remember_selection(payload))
        except HeadlessError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "That selection could not be remembered.",
                 "code": error.code},
            )

    def do_POST(self) -> None:
        if self._reject_untrusted_host():
            return
        if not self._is_allowed_origin():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Cross-origin Studio requests are not allowed."},
            )
            return
        try:
            path = self._path()
            if path == "/studio/settings/model_roots":
                self._apply_model_root_settings()
                return
            if path == "/studio/settings/model_selection":
                self._remember_model_selection()
                return
            # The picker. Registered HERE, above the `/studio/` catch-all
            # further down, because that catch-all hands anything beginning
            # `/studio/` to the source adapter -- a route added below it is
            # not a route, it is a 501 from a stub.
            if path.startswith("/studio/fs/"):
                self._filesystem_browse(path)
                return
            # The colour-managed pixel path, registered above the same
            # catch-all and for the same reason.
            if path in (
                "/studio/image_pixels",
                "/studio/import_pixels",
                "/studio/sample_image_pixels",
            ):
                self._pixel_route(path)
                return
            if path == "/api/assets":
                # Same bound as generate: this route exists to carry exactly
                # the payload that made generate outgrow 64 KB.
                payload = self._read_json_bounded(
                    maximum_bytes=_MAX_SOURCE_REQUEST_BYTES)
                self._send_json(
                    HTTPStatus.CREATED,
                    self._presentation.admit_asset(payload),
                )
                return
            if path == "/api/generate":
                # 64 KB was right while this route carried only text. WP1 gave
                # it a source image and a mask, and nobody raised the ceiling --
                # so the owner's first real inpaint click was refused with
                # "Request body exceeds the Alpha S0.7 size limit", which names
                # a limit rather than the reason it was hit.
                #
                # The legacy `/studio/generate` route was raised to this exact
                # bound for this exact payload, so the precedent already
                # existed. `_read_json_bounded`, not `_read_json`: its docstring
                # records that refusing a multi-MiB body WITHOUT draining it
                # leaves the remainder in the socket, and the next request on
                # the same keep-alive connection is then parsed starting inside
                # it. Bounded refuses AND closes the connection, which makes the
                # unread remainder harmless.
                payload = self._read_json_bounded(
                    maximum_bytes=_MAX_SOURCE_REQUEST_BYTES)
                # Lifecycle hosts submit asynchronously under a public id that
                # exists before backend work -- the id a queued cancel can
                # name. Hosts without a lifecycle keep the original blocking
                # semantics unchanged.
                if getattr(self._presentation, "lifecycle_available", False):
                    self._send_json(
                        HTTPStatus.ACCEPTED,
                        self._presentation.submit_async(payload),
                    )
                    return
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self._presentation.submit(payload),
                )
                return
            # /api/profiles/select_catalogue, /api/profiles/select and
            # /api/model/load are retired together. They existed to build a
            # profile from three selections, select it, then load it -- three
            # calls to say "use these models". The selection now travels on
            # the generation request and the server reconciles it before
            # leasing, so an explicit load has nothing left to do.
            #
            # Retired rather than left answering: an unused-but-live load
            # route is a second way to make a model resident, and two paths
            # into one resident session is exactly the ownership split this
            # lifecycle exists to prevent.
            if path == "/api/model/unload":
                self._read_json_optional()
                self._send_json(
                    HTTPStatus.OK, self._presentation.lifecycle_unload()
                )
                return
            # The queue verbs. Registered above the `/api/jobs/<id>/...` walk
            # below so a fixed path is matched as a fixed path.
            if path == "/api/queue/reorder":
                self._send_json(
                    HTTPStatus.OK,
                    self._presentation.reorder_queue(self._read_json()),
                )
                return
            if path == "/api/queue/clear":
                self._read_json_optional()
                self._send_json(HTTPStatus.OK, self._presentation.clear_queue())
                return
            if path == "/api/queue/cancel_all":
                self._read_json_optional()
                self._send_json(
                    HTTPStatus.OK, self._presentation.cancel_all_jobs()
                )
                return
            parts = [part for part in path.split("/") if part]
            if (
                len(parts) == 4
                and parts[:2] == ["api", "jobs"]
                and parts[3] == "cancel"
            ):
                payload = self._read_json()
                self._send_json(
                    HTTPStatus.OK,
                    self._presentation.cancel(parts[2]),
                )
                return
            # Remove is NOT cancel. It refuses a job that has already started,
            # so a client cannot ask to drop a waiting job and instead stop
            # the picture being made. Two verbs because the difference costs
            # an image when it is got wrong.
            if (
                len(parts) == 4
                and parts[:2] == ["api", "jobs"]
                and parts[3] == "remove"
            ):
                self._read_json_optional()
                self._send_json(
                    HTTPStatus.OK,
                    self._presentation.remove_queued_job(parts[2]),
                )
                return
            if path.startswith("/studio/"):
                payload = self._read_source_json()
                # The Gallery gets first refusal on its own routes. Its body is
                # already read here, so it is passed in rather than read again
                # -- a second read on a consumed body would block.
                if self._serve_gallery("POST", payload):
                    return
                self._send_json(
                    HTTPStatus.OK,
                    self._source_adapter.post(path, payload),
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "Studio route not found."},
            )
        except Exception as exc:
            self._send_api_error(exc)

    def do_DELETE(self) -> None:
        if self._reject_untrusted_host():
            return
        if not self._is_allowed_origin():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Cross-origin Studio requests are not allowed."},
            )
            return
        path = self._path()
        try:
            if path.startswith("/studio/"):
                # `self.path`, not `path`. `_path()` strips the query string,
                # so every DELETE reached the adapter with its parameters gone
                # -- `/studio/lexicon/file?path=climate.txt` arrived as
                # `/studio/lexicon/file`, resolved to the wildcard root, and
                # was refused by the guard that stops the root being deleted.
                # `do_GET` has always passed the full path; this did not, and
                # nothing noticed because the only prior DELETE route ignored
                # its query. Found by clicking Delete in a real browser.
                self._send_json(
                    HTTPStatus.OK,
                    self._source_adapter.delete(self.path),
                )
                return
        except Exception as exc:
            self._send_api_error(exc)
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": "Studio route not found."},
        )


def _create_mock_presentation() -> StudioPresentation:
    """The served application, assembled through the product composition root.

    This used to build `StudioApplication(MockBackend(...))` inline, which is
    the hardcoded-mock gap `docs/studio/FORGE_BACKEND_ADAPTER_READINESS.md`
    records. Routing it through `build_standalone` means there is one seam
    where a real backend gets wired, instead of two inline arrangements that
    have to be found and changed together.

    No clock is injected, so `MockBackend` keeps `_wait_for_progress` true and
    the served progress pacing is unchanged.
    """

    from forge_studio import GenerationRequest
    from forge_studio.composition import build_standalone

    composition = build_standalone(result_root=_EVIDENCE_DIRECTORY / "results")
    from forge_studio.preferences import PreferenceStore

    return StudioPresentation(
        composition.application, GenerationRequest, preferences=PreferenceStore()
    )


def _demo_request(
    request_factory: Callable[..., Any],
    *,
    positive_prompt: str,
    seed: int,
) -> Any:
    return request_factory(
        model_id="studio-mock-illustration-v1",
        positive_prompt=positive_prompt,
        negative_prompt="text, watermark, malformed",
        seed=seed,
        steps=4,
        cfg_scale=7.0,
        width=512,
        height=512,
    )


def _demo_poll_to_terminal(
    application: Any,
    job_id: str,
    *,
    maximum_polls: int = 64,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    last: dict[str, Any] = {"state": "unknown", "progress": 0}
    for _ in range(maximum_polls):
        progress = application.poll_or_stream_progress(job_id)
        if isinstance(progress, Iterator):
            try:
                progress = next(progress)
            except StopIteration:
                progress = {"state": "unknown", "progress": 0}
        last = _as_mapping(progress)
        last["state"] = _state_of(last)
        observation = {
            "state": last["state"],
            "progress": float(last.get("progress", last.get("progress_fraction", 0))),
        }
        if not observations or observations[-1] != observation:
            observations.append(observation)
        if last["state"] in _TERMINAL_STATES:
            break
    return last, observations


def _stable_metadata(value: Any) -> dict[str, Any]:
    metadata = _as_mapping(value)
    unstable_fragments = (
        "created",
        "duration",
        "elapsed",
        "job_id",
        "path",
        "time",
        "trace",
    )
    return {
        key: item
        for key, item in sorted(metadata.items())
        if not any(fragment in key.lower() for fragment in unstable_fragments)
    }


def _demo_result_summary(result: Any) -> dict[str, Any]:
    plain = _as_mapping(result)
    image = plain.get("image_data_url", plain.get("image", plain.get("preview")))
    image_data = image if isinstance(image, str) else ""
    return {
        "has_image_data_url": image_data.startswith("data:image/"),
        "image_data_url_sha256": (
            hashlib.sha256(image_data.encode("utf-8")).hexdigest()
            if image_data
            else None
        ),
        "metadata": _stable_metadata(plain.get("metadata", {})),
    }


def _run_demo_scenario(
    application: Any,
    request_factory: Callable[..., Any],
    *,
    name: str,
    prompt: str,
    seed: int,
    expected_state: str,
    cancel_immediately: bool = False,
) -> dict[str, Any]:
    submitted = application.submit_generation(
        _demo_request(request_factory, positive_prompt=prompt, seed=seed)
    )
    job_id = _job_id_of(submitted)
    observations: list[dict[str, Any]] = []
    if cancel_immediately:
        for _ in range(2):
            progress = _as_mapping(
                application.poll_or_stream_progress(job_id)
            )
            observations.append(
                {
                    "state": _state_of(progress),
                    "progress": float(progress.get("progress", 0)),
                }
            )
        application.cancel_generation(job_id)
    terminal, remaining = _demo_poll_to_terminal(application, job_id)
    for observation in remaining:
        if not observations or observations[-1] != observation:
            observations.append(observation)
    observed_state = _state_of(terminal)
    scenario: dict[str, Any] = {
        "name": name,
        "expected_terminal_state": expected_state,
        "observed_terminal_state": observed_state,
        "passed": observed_state == expected_state,
        "observations": observations,
    }
    if observed_state == "completed":
        scenario["result"] = _demo_result_summary(application.get_result(job_id))
    if observed_state == "failed":
        scenario["controlled_error"] = _plain(
            terminal.get(
                "error",
                {"message": terminal.get("message", "controlled failure")},
            )
        )
    return scenario


def run_socket_free_demo() -> int:
    """Exercise the mock application without creating a listening socket."""

    from forge_studio import GenerationRequest
    from forge_studio.composition import build_standalone

    composition = build_standalone(result_root=_EVIDENCE_DIRECTORY / "results")
    application = composition.application
    scenarios: list[dict[str, Any]] = []
    try:
        scenarios.append(
            _run_demo_scenario(
                application,
                GenerationRequest,
                name="first_success",
                prompt="Alpha S0 deterministic first success",
                seed=101,
                expected_state="completed",
            )
        )
        scenarios.append(
            _run_demo_scenario(
                application,
                GenerationRequest,
                name="cancellation",
                prompt="Alpha S0 deterministic cancellation",
                seed=202,
                expected_state="cancelled",
                cancel_immediately=True,
            )
        )
        scenarios.append(
            _run_demo_scenario(
                application,
                GenerationRequest,
                name="controlled_failure",
                prompt="__mock_fail__",
                seed=303,
                expected_state="failed",
            )
        )
        scenarios.append(
            _run_demo_scenario(
                application,
                GenerationRequest,
                name="recovery_success",
                prompt="Alpha S0 deterministic recovery success",
                seed=404,
                expected_state="completed",
            )
        )
    finally:
        shutdown = getattr(application, "shutdown", None)
        if callable(shutdown):
            shutdown()

    report = {
        "schema_version": "studio-alpha-demo/v1",
        "title": "Forge Studio Alpha S0.7 socket-free demo",
        "network_used": False,
        "scenario_order": [
            "first_success",
            "cancellation",
            "controlled_failure",
            "recovery_success",
        ],
        "passed": all(scenario["passed"] for scenario in scenarios),
        "scenarios": scenarios,
    }
    _EVIDENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    report_path = _EVIDENCE_DIRECTORY / "demo-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Alpha S0.7 demo evidence: {report_path}")
    print("PASS" if report["passed"] else "FAIL")
    return 0 if report["passed"] else 1


def run_loopback_ui(port: int = _DEFAULT_PORT) -> int:
    """Serve the Studio frontend on IPv4 loopback until interrupted."""

    if port < 0 or port > 65535:
        raise ValueError("port must be between 0 and 65535")
    presentation = _create_mock_presentation()
    server: _StudioHTTPServer | None = None
    try:
        server = _StudioHTTPServer((_LOOPBACK_HOST, port), presentation)
        actual_port = int(server.server_address[1])
        print(
            f"Forge Studio Alpha S0.7: "
            f"http://{_LOOPBACK_HOST}:{actual_port}/studio/"
        )
        print("Loopback only. Press Ctrl+C to stop.")
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nForge Studio Alpha S0.7 stopped.")
    finally:
        if server is not None:
            server.server_close()
        presentation.shutdown()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the standalone Forge Studio Alpha S0.7 mock shell."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run deterministic mock scenarios without opening a socket",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"loopback UI port (default: {_DEFAULT_PORT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.demo:
        return run_socket_free_demo()
    return run_loopback_ui(arguments.port)


__all__ = [
    "PresentationError",
    "StudioPresentation",
    "main",
    "run_loopback_ui",
    "run_socket_free_demo",
]
