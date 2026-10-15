"""What the heatmap viewer shows for one picture. HM3.

The owner's choice (2026-10-12): layout A (a word list beside the picture,
each word with a strength bar) and heat colours. No "may be missing" dot in
v0: without word kinds it marked about one in five present words
(`Evidence/heatmap-viewer-options-2026-10-12/v2-dot-rule.txt`); it returns
when a detector can confirm what is in the picture.

From the picture's attention sidecar (HM1), per content word:

- its map: the sum of its pieces' maps -- Anima blocks 2-8 (the research's
  best for placement), SDXL's two finest levels at steps 5-15 -- sent as a
  PNG. Anima's is upsampled to 512 px, snapped to the picture's edges (the
  capture is one cell per 16 pixels; the research's guided filter) and
  levelled by its own spread. SDXL's is its grid over its peak, drawn as
  viewer v0 drew it: the owner tested SDXL on v0 and kept it;
- its strength: the 99th percentile of that map before normalising, as a
  multiple of the median content word's in the same picture (the research's
  readout measure, FINDINGS 4.1/4.4, taken within the picture so it needs no
  per-family calibration).

On Anima, also "text used here": 1 - the padding share, over all blocks.

Words are the research's: T5 pieces joined at U+2581, a bare U+2581 joining
the word after it, punctuation and </s> belonging to none
(`anima-attention-2026-10-11/pm_lib.py` `anima_words`); CLIP pieces ending a
word at "</w>". Function words are left out (the research's STOP list, plus
a few more), and a word that appears twice is one row with both positions.

The arithmetic (maps, strengths, encoding) is
`forge_headless.attention_maps`: owned presentation code imports no
numerical library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .attention_store import relative_for, resolve

#: The research's STOP list (`s4_strength.py`), plus the other words that
#: name no thing in a picture prompt.
FUNCTION_WORDS = frozenset({
    "a", "an", "the", "at", "in", "on", "by", "over", "between", "of", "with", "and",
    "to", "for", "from", "into", "onto", "under", "near", "is", "are", "its", "it",
    "as", "or", "very",
})
_PUNCT = frozenset({",", ".", ";", ":", "!", "?"})
_SPACE = "▁"


def anima_words(pieces: list[str]) -> list[tuple[str, list[int]]]:
    """T5 SentencePiece pieces -> (word, positions)."""
    out: list[tuple[str, list[int]]] = []
    current, positions = "", []

    def close() -> None:
        nonlocal current, positions
        if current:
            out.append((current, positions))
        current, positions = "", []

    for i, piece in enumerate(pieces):
        if piece in ("</s>", "") or piece in _PUNCT:
            close()
            continue
        if piece.startswith(_SPACE) and (current or (positions and pieces[positions[-1]] != _SPACE)):
            close()
        current += piece.replace(_SPACE, "")
        positions.append(i)
    close()
    return out


def clip_words(pieces: list[str]) -> list[tuple[str, list[int]]]:
    """CLIP BPE pieces -> (word, positions): a word ends at "</w>"."""
    out: list[tuple[str, list[int]]] = []
    current, positions = "", []
    for i, piece in enumerate(pieces):
        current += piece.replace("</w>", "")
        positions.append(i)
        if piece.endswith("</w>"):
            out.append((current, positions))
            current, positions = "", []
    if current:
        out.append((current, positions))
    return out


def content_words(family: str, pieces: list[str]) -> list[tuple[str, list[int]]]:
    """The words the viewer lists, in prompt order, each once."""
    grouped = anima_words(pieces) if family == "anima" else clip_words(pieces)
    merged: dict[str, list[int]] = {}
    for word, positions in grouped:
        key = word.lower()
        if not any(ch.isalpha() for ch in key) or key in FUNCTION_WORDS:
            continue
        merged.setdefault(key, []).extend(positions)
    return list(merged.items())


def for_image(store: Any, store_root: Path | str, image_id: int) -> dict[str, Any] | None:
    """The viewer's answer for a Gallery image, or None when there is no
    such image. `{"available": False}` when it has no capture."""
    with store.read() as connection:
        row = connection.execute("SELECT content_hash, filepath FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            return None
        digest = str(row[0] or "").strip()
        filepath = str(row[1] or "")
        named = connection.execute(
            "SELECT attention_path FROM image_metadata WHERE content_hash = ? AND content_hash != ''",
            (digest,)).fetchone()
    relative = str(named[0] or "") if named else ""
    path = resolve(store_root, relative) if relative else None
    if path is None or not path.is_file():
        # The name is an index; the hash is the identity. A record that lost
        # its name (clean-up relinks it) still finds the file.
        fallback = relative_for(digest)
        path = resolve(store_root, fallback) if fallback else None
    if path is None or not path.is_file():
        return {"available": False}
    # The arithmetic lives in `forge_headless`: owned presentation code
    # imports no numerical library.
    from forge_headless.attention_maps import view

    picture = _open_picture(filepath)
    try:
        return view(path, picture)
    finally:
        if picture is not None:
            picture.close()


def _open_picture(filepath: str) -> Any:
    """The Gallery picture, decoded, or None when it cannot be read: the maps
    are then drawn upsampled but not edge-snapped, as they are for a picture
    whose file has gone."""
    if not filepath:
        return None
    try:
        from PIL import Image

        picture = Image.open(filepath)
        picture.load()
        return picture
    except (ImportError, OSError, ValueError):
        return None


__all__ = ("FUNCTION_WORDS", "anima_words", "clip_words", "content_words", "for_image")
