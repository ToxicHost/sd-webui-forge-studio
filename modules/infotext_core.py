"""Infotext value quoting, with no UI, Gradio, Torch, or backend imports.

`quote` and `unquote` are pure `json` string helpers, but they lived in
`modules/infotext_utils.py`, which imports `gradio`, `modules.ui_tempdir`, and
`modules.processing`. `modules/processing.py` reached that whole module for a
single `quote` call while formatting generation parameters.

The functions are unchanged; `modules/infotext_utils.py` re-exports them so
existing callers, including `modules/postprocessing.py`, keep working.
"""

from __future__ import annotations

import json


def quote(text: str) -> str:
    if "," not in str(text) and "\n" not in str(text) and ":" not in str(text):
        return text

    try:
        return json.dumps(text, ensure_ascii=False)
    except Exception:
        return text


def unquote(text: str) -> str:
    if not text or not (text.startswith('"') and text.endswith('"')):
        return text

    try:
        return json.loads(text)
    except Exception:
        return text
