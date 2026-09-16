"""Layout-aware result discovery and the native Canvas Tier A oracle.

Codifies what a live leg proved the hard way, twice.

Two verifier defects invalidated a Canvas acceptance gate:

```text
1. #sessionStripScroll was assumed universally. In CLASSIC layout the Session
   Strip is display:none and renders nothing; results go to #outputGrid. A
   broad selector over the strip container matched #sessionStripClear -- the
   bin button -- and cleared the session instead of selecting the result.

2. #studio-canvas was hashed as the equality oracle. It is the SCALED VIEWPORT
   (observed 519x327), not the document raster, so it can never equal a 768x768
   result no matter what is on Canvas.
```

The browser-side code lives here as text rather than in an ad-hoc snippet so it
can be reviewed, guarded by tests, and reused unchanged by the live leg.

Nothing here touches a model payload.
"""

from __future__ import annotations

#: Result containers, per layout. Discovery must resolve the ACTIVE one.
CLASSIC_CONTAINER = "#outputGrid"
CLASSIC_ENTRY = ".output-thumb"
DECK_CONTAINER = "#sessionStripScroll"
DECK_ENTRY = ".session-thumb"

#: Controls that can never be a generated result. The first of these is what a
#: broad selector actually hit.
EXCLUDED_CONTROL_IDS = (
    "sessionStripClear",
    "sessionStripHide",
    "sessionStripToggle",
)
EXCLUDED_ANCESTOR = ".session-strip-head"

#: The one product-supported Send-to-Canvas action. The Session Strip dblclick
#: opens the Gallery lightbox and is NOT Canvas proof.
SEND_TO_CANVAS_CONTROL = "#outputToCanvas"

#: The layer displayOnCanvas creates for a user-initiated send.
OUTPUT_LAYER_NAME = "Output"

#: Discovery. Resolves the active layout, then finds exactly the entry whose
#: data-idx maps to the expected result, refusing every excluded control.
DISCOVER_RESULT_JS = r"""
(function discoverGeneratedResult(expectedIdx) {
  var deckScroll = document.querySelector("#sessionStripScroll");
  var deckActive = !!(deckScroll && deckScroll.offsetParent !== null);
  var container = document.querySelector(deckActive ? "#sessionStripScroll" : "#outputGrid");
  var entrySel  = deckActive ? ".session-thumb" : ".output-thumb";
  var layout    = deckActive ? "deck" : "classic";
  if (!container) return { ok: false, reason: "no result container", layout: layout };

  var entries = Array.prototype.slice.call(container.querySelectorAll(entrySel));
  var matches = entries.filter(function (el) {
    if (!el.matches(entrySel)) return false;
    if (!container.contains(el)) return false;
    if (el.id === "sessionStripClear" || el.id === "sessionStripHide"
        || el.id === "sessionStripToggle") return false;
    if (el.closest(".session-strip-head")) return false;
    if (el.dataset.idx === undefined) return false;
    return parseInt(el.dataset.idx, 10) === expectedIdx;
  });
  if (matches.length !== 1) {
    return { ok: false, reason: "expected exactly one match, got " + matches.length,
             layout: layout, container: deckActive ? "#sessionStripScroll" : "#outputGrid" };
  }
  var el = matches[0];
  return {
    ok: true, layout: layout,
    container: deckActive ? "#sessionStripScroll" : "#outputGrid",
    entrySelector: entrySel,
    dataIdx: parseInt(el.dataset.idx, 10),
    tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 60),
    element: el
  };
})
"""

#: Tier A oracle. Reads the NATIVE Output-layer raster, never the viewport, and
#: refuses to compare unless the document dimensions match the result -- the
#: displayOnCanvas fallback scales with drawImage(img, 0, 0, S.W, S.H), so a
#: hash comparison without that guard is not evidence of identity.
NATIVE_CANVAS_ORACLE_JS = r"""
(async function nativeCanvasOracle(expectedW, expectedH, expectedPixelSha, sha256) {
  // StudioCore.state is an OBJECT in the shipped path -- displayOnCanvas itself
  // does `const S = Core.state`. An earlier version of this oracle required a
  // function and rejected the real build; the working rehearsal had handled
  // both and the regression was introduced while codifying it.
  var raw = (typeof StudioCore !== "undefined") ? StudioCore.state : undefined;
  var s = (typeof raw === "function") ? raw() : raw;
  if (!s || typeof s !== "object" || !Array.isArray(s.layers)) {
    return { CANVAS_OPEN_CONFIRMED: false, reason: "StudioCore state unavailable or invalid" };
  }
  var out = (s.layers || []).filter(function (l) { return l.name === "Output"; }).pop();
  if (!out || !out.canvas || !out.ctx) {
    return { CANVAS_OPEN_CONFIRMED: false, reason: "no Output layer with a native canvas" };
  }
  if (s.W !== expectedW || s.H !== expectedH) {
    return { CANVAS_OPEN_CONFIRMED: false,
             reason: "document " + s.W + "x" + s.H + " != result " + expectedW + "x" + expectedH
                     + "; displayOnCanvas would have scaled" };
  }
  if (out.canvas.width !== expectedW || out.canvas.height !== expectedH) {
    return { CANVAS_OPEN_CONFIRMED: false,
             reason: "Output layer " + out.canvas.width + "x" + out.canvas.height + " != result" };
  }
  var px = out.ctx.getImageData(0, 0, out.canvas.width, out.canvas.height).data;
  var nativeSha = await sha256(px.buffer);
  return {
    CANVAS_OPEN_CONFIRMED: nativeSha === expectedPixelSha,
    layerName: out.name, nativeDims: [out.canvas.width, out.canvas.height],
    documentDims: [s.W, s.H], nativeSha: nativeSha, expectedSha: expectedPixelSha
  };
})
"""

__all__ = (
    "CLASSIC_CONTAINER", "CLASSIC_ENTRY", "DECK_CONTAINER", "DECK_ENTRY",
    "DISCOVER_RESULT_JS", "EXCLUDED_ANCESTOR", "EXCLUDED_CONTROL_IDS",
    "NATIVE_CANVAS_ORACLE_JS", "OUTPUT_LAYER_NAME", "SEND_TO_CANVAS_CONTROL",
)
