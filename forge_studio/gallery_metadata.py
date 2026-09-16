"""Reading generation parameters out of an image, the way Studio means them.

Ported from the Extension's `scripts/studio_gallery.py::parse_sd_parameters`,
which is TrackImage-derived code that Studio had already modified. The handoff
is emphatic that those modifications must survive any TrackImage merge, so they
are the part of this module worth reading:

```text
prompt              what the model actually saw
negative_prompt
template            the prompt BEFORE wildcards were resolved
negative_template
studio_dynamic_prompts
```

A stock A1111 parser knows only prompt and negative prompt. It reads a
`Template:` line as more positive prompt, so an image generated from a wildcard
template comes back with the template glued onto the end of its prompt, and a
search for the resolved text matches the template too. Studio separates them,
which is why `template` and `negative_template` are columns in the Gallery
schema rather than a formatting detail.

THE CONTINUATION-LINE RULE is the subtle part. Prompts wrap over many lines, so
a line that is not recognised belongs to whatever section is currently open.
Once the settings tail begins -- at `Steps: <digit>`, the A1111 convention --
everything after it is settings, and no later line can fall back into the
positive prompt. Extensions append their own trailers there, and without that
rule every one of them lands in the prompt and then in the search index.

MERGED FROM TRACKIMAGE 3.94, which is genuinely ahead of the Extension here:

* **every `Key: Value` in the tail**, rather than a whitelist of thirteen. The
  Extension silently dropped the rest, so an owner using ADetailer, a named
  VAE, a Hires sampler or any Lora saw none of it and had no way to tell the
  information was in the file all along. Quoted values survive commas, which is
  how an ADetailer prompt and a Lora hash list are written;
* **the whole tail, not its first line** -- an ADetailer block wraps;
* **null bytes stripped**, from a UTF-16 field decoded as UTF-8;
* **`hires_size` derived**, so the size an owner sees is the picture they have
  rather than the base pass it started from.

What was NOT taken is as deliberate. TrackImage splits the head at
`Negative prompt:` and calls everything after it negative -- which would put a
Studio `Template:` line INSIDE the negative prompt. The line-based head
handling above is kept exactly as it was, which is what §19 means by not
reverting Studio-specific parser behaviour.

Pure text in, dict out. No Pillow, no filesystem, no engine: extraction from a
file is a separate concern, and keeping this half pure means the whole parser is
testable against a string.
"""

from __future__ import annotations

import re
from typing import Any

#: Fields never worth searching on. Negatives describe what an owner did NOT
#: want, so indexing them makes every image match every unwanted thing; raw
#: blobs are noise.
_UNSEARCHABLE = frozenset({
    "negative_prompt",
    "negative_template",
    "raw_parameters",
    "comfyui_prompt",
    "comfyui_workflow",
    "file_size",
    "studio_dynamic_prompts",
    "error",
})

#: What a settings key is called once it is ours. TrackImage 3.94's map,
#: merged: it knows names the Extension's fixed table did not (VAE, the Hires
#: sampler and checkpoint, `Scheduler` as well as `Schedule type`).
_FIELD_NAMES = {
    "steps": "steps", "sampler": "sampler",
    "schedule type": "scheduler", "scheduler": "scheduler",
    "cfg scale": "cfg_scale", "seed": "seed", "size": "size",
    "model": "model", "model hash": "model_hash",
    "vae": "vae", "vae hash": "vae_hash",
    "clip skip": "clip_skip", "denoising strength": "denoising",
    "hires upscaler": "hires_upscaler", "hires steps": "hires_steps",
    "hires upscale": "hires_upscale", "hires resize": "hires_resize",
    "hires cfg scale": "hires_cfg", "hires checkpoint": "hires_checkpoint",
    "hires sampler": "hires_sampler",
}

#: One `Key: Value` pair from the settings tail. Values may be quoted, so a
#: comma inside a quoted value does not end it -- which is how A1111 writes an
#: ADetailer prompt or a Lora hash list.
_PAIR = re.compile(r'\s*([\w \-/()]+?):\s*("(?:\\.|[^\\"])*"|[^,]*)\s*(?:,|$)')

#: Where the settings tail starts. Found by string position rather than at a
#: line start, so a single-line infotext splits too.
_SETTINGS_HEAD = re.compile(r"\bSteps:\s*\d")

#: Longest a single captured value may be.
_VALUE_LIMIT = 300

#: Caps, preserved from the Extension. A prompt field is not a document store,
#: and an image with a runaway parameter block must not put megabytes in a row.
_LIMITS = {
    "prompt": 2000,
    "negative_prompt": 1000,
    "template": 2000,
    "negative_template": 1000,
}


def parse_generation_parameters(text: str) -> dict[str, Any]:
    """Parse an A1111/Forge parameters block, keeping Studio's own fields."""

    result: dict[str, Any] = {}
    if not text:
        return result
    # Stray null bytes, from a UTF-16 field decoded as if it were UTF-8. Merged
    # from TrackImage 3.94: without this every later match fails on a file that
    # otherwise looks perfectly normal.
    text = text.replace("\x00", "").strip()
    if not text:
        return result

    buckets: dict[str, list[str]] = {
        "prompt": [],
        "negative": [],
        "template": [],
        "neg_template": [],
    }
    mode = "prompt"
    settings_line = ""

    for line in text.strip().split("\n"):
        # Order matters: "Negative Template:" must be tested before
        # "Template:", or it is matched as a template whose text begins
        # "Negative".
        if line.startswith("Negative prompt:"):
            mode = "negative"
            buckets["negative"].append(line[len("Negative prompt:"):].strip())
        elif line.startswith("Negative Template:"):
            mode = "neg_template"
            buckets["neg_template"].append(
                line[len("Negative Template:"):].strip()
            )
        elif line.startswith("Template:"):
            mode = "template"
            buckets["template"].append(line[len("Template:"):].strip())
        elif line.startswith("Studio dynamic prompts:"):
            mode = "meta"
            result["studio_dynamic_prompts"] = line[
                len("Studio dynamic prompts:"):
            ].strip()
        elif mode == "meta":
            # Already inside the settings tail. Kept, not dropped: an ADetailer
            # block wraps over several lines, and the Extension's version threw
            # every line after the first away -- so an owner using ADetailer saw
            # none of its settings. It cannot reach the prompt from here.
            settings_line = f"{settings_line}\n{line}"
        else:
            inline = _SETTINGS_HEAD.search(line)
            if inline:
                # The tail begins here, possibly sharing a line with the end of
                # a prompt. Anchored on `Steps: <digit>` -- the A1111
                # convention -- rather than on any of a dozen key names, so a
                # prompt line that happens to start "Model: ..." is still
                # prompt.
                settings_line = line[inline.start():]
                before = line[: inline.start()].strip()
                if before and mode in buckets:
                    buckets[mode].append(before)
                mode = "meta"
            elif mode in buckets:
                buckets[mode].append(line)

    for key, bucket in (
        ("prompt", "prompt"),
        ("negative_prompt", "negative"),
        ("template", "template"),
        ("negative_template", "neg_template"),
    ):
        if buckets[bucket]:
            joined = "\n".join(buckets[bucket]).strip()
            if joined:
                result[key] = joined[: _LIMITS[key]]

    if settings_line:
        _read_settings(settings_line, result)
    return result


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _read_settings(settings_line: str, result: dict[str, Any]) -> None:
    """Every `Key: Value` in the tail, not a fixed list of thirteen.

    Merged from TrackImage 3.94, and the single biggest improvement in it. The
    Extension matched a whitelist of thirteen patterns and silently dropped
    everything else -- so an owner looking at an image made with ADetailer, a
    named VAE, a Hires sampler, or any Lora saw none of it, and had no way to
    tell the information was in the file all along.

    Recognised keys get a canonical name; anything else is kept under a
    normalised one so the details panel can still list it.
    """

    # Newlines inside the tail become commas, so a wrapped settings block is
    # read as the single comma-separated list it means to be.
    settings_line = re.sub(r"\s*\n\s*", ", ", settings_line)
    for found in _PAIR.finditer(settings_line):
        key = found.group(1).strip()
        value = _unquote(found.group(2))
        if not key or value == "":
            continue
        lowered = key.lower()
        name = _FIELD_NAMES.get(lowered)
        if name is None:
            name = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
        if name and name not in result:
            result[name] = value[:_VALUE_LIMIT]
    _derive_hires_size(result)


def _derive_hires_size(result: dict[str, Any]) -> None:
    """What the image actually came out at when Hires fix ran.

    An owner sorting or searching by size means the picture they have, not the
    base pass it started from. An explicit `Hires resize` wins; otherwise the
    base size times the upscale factor.
    """

    if "hires_size" in result:
        return
    resized = re.match(r"(\d+)\s*x\s*(\d+)", str(result.get("hires_resize", "")))
    if resized:
        result["hires_size"] = f"{resized.group(1)}x{resized.group(2)}"
        return
    base = re.match(r"(\d+)\s*x\s*(\d+)", str(result.get("size", "")))
    if not base or not result.get("hires_upscale"):
        return
    try:
        factor = float(result["hires_upscale"])
    except (TypeError, ValueError):
        return
    result["hires_size"] = (
        f"{round(int(base.group(1)) * factor)}x"
        f"{round(int(base.group(2)) * factor)}"
    )


def clean_for_search(text: str) -> str:
    """Reduce prompt syntax to plain words.

    Weights, LoRA invocations, and bracket emphasis are how a prompt is
    WRITTEN, not what it is about. An owner searching for "castle" should find
    `(castle:1.4)`, and should not have to remember which weight they used.
    """

    if not text:
        return ""
    cleaned = re.sub(r"<[^>]*>", " ", text)          # <lora:name:1>
    cleaned = re.sub(r"[()\[\]{}]", " ", cleaned)    # emphasis brackets
    cleaned = re.sub(r":\s*[0-9.]+", " ", cleaned)   # trailing weights
    cleaned = re.sub(r"[,_]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def search_text_for(metadata: dict[str, Any]) -> str:
    """The searchable text for one image's metadata.

    Negatives are excluded deliberately: they say what the owner did not want,
    and indexing them makes a search for "blurry" return every image that asked
    not to be blurry -- which is most of them.
    """

    parts: list[str] = []
    for key, value in metadata.items():
        if key in _UNSEARCHABLE:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    return clean_for_search(" ".join(parts))


__all__ = (
    "clean_for_search",
    "parse_generation_parameters",
    "search_text_for",
)
