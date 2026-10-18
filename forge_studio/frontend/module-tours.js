/**
 * Forge Studio — Module Tours
 * by ToxicHost & Moritz
 *
 * First-visit orientation tours for Studio modules.
 * Uses StudioModules.onInit() to fire on first activation.
 * Each tour is 3-4 steps and runs only once (persisted via localStorage).
 */
(function () {
"use strict";

var TAG = "[ModuleTours]";
var PREFIX = "st-module-tour-";

function _seen(id) { return localStorage.getItem(PREFIX + id) === "1"; }
function _markSeen(id) { localStorage.setItem(PREFIX + id, "1"); }
function _t(key, fallback) {
  return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback) : fallback;
}

// ========================================================================
// WORKSHOP TOUR
// ========================================================================

if (window.StudioModules) {

  StudioModules.onInit("workshop", function () {
    if (_seen("workshop") || !window.StudioTour) return;
    _markSeen("workshop");

    setTimeout(function () {
      StudioTour.create({
        id: "tour-workshop",
        steps: [
          {
            id: "ws_merge",
            text: _t("moduleTour.workshop.merge.text", "This is the <em>Workshop</em> — where you merge and combine AI models.\n\nThe <em>merge board</em> has one row per step: pick a <em>Primary</em> and a <em>Secondary</em> model, a <em>Method</em> (Weighted Sum, SLERP or Add Difference) and a blend weight. Add rows to chain steps."),
            spotlight: ".ws-board",
            advance: "click", btn: _t("moduleTour.workshop.merge.btn", "Next")
          },
          {
            id: "ws_actions",
            text: _t("moduleTour.workshop.actions.text", "<em>Begin Merge</em> writes the result as a new checkpoint in your first checkpoint folder. An existing file is never overwritten.\n\nYou can also bake a LoRA or a VAE into the result from the same recipe."),
            spotlight: ".ws-action-row",
            advance: "click", btn: _t("moduleTour.workshop.actions.btn", "Next")
          },
          {
            id: "ws_history",
            text: _t("moduleTour.workshop.history.text", "The <em>History</em> tab keeps a journal of every merge you’ve done — recipe, settings, and notes. You can rate, tag, and search your experiments.\n\nCheck the <em>Codex</em> for details on each merge method."),
            spotlight: ".ws-tabs",
            advance: "click", btn: _t("moduleTour.workshop.history.btn", "Got it")
          },
        ],
        persist: false,
        onComplete: function () { console.log(TAG, "Workshop tour complete"); },
        onCancel: function () {},
        confirmCancel: false,
      }).start(0);
    }, 600);
  });

  // ========================================================================
  // GALLERY TOUR
  // ========================================================================

  StudioModules.onInit("gallery", function () {
    if (_seen("gallery") || !window.StudioTour) return;
    _markSeen("gallery");

    // The Gallery draws itself after its first request, so wait for it to show
    // either its sidebar or its setup card (at most ~4 s) before choosing steps.
    var waited = 0;
    var timer = setInterval(function () {
      waited += 200;
      var populated = !!document.querySelector(".gal-sidebar");
      if (!populated && !document.querySelector(".gal-setup") && waited < 4000) return;
      clearInterval(timer);
      // A Gallery with no linked folder shows only "Choose folder & scan": it has
      // no sidebar, search or grid to point at, so the tour says what it does
      // show instead of highlighting things that are not there.
      var steps = populated ? [
        {
          id: "gal_layout",
          text: _t("moduleTour.gallery.layout.text", "This is the <em>Gallery</em> — a browser for all your generated images.\n\n<em>Sidebar</em> has folders and character tags for filtering. <em>Search</em> at the top matches filenames and metadata."),
          spotlight: ".gal-sidebar",
          advance: "click", btn: _t("moduleTour.gallery.layout.btn", "Next")
        },
        {
          id: "gal_interact",
          text: _t("moduleTour.gallery.interact.text", "Click any image for a detail view with full metadata. Right-click for a context menu (rename, move, delete, send to canvas).\n\nDrag images between folders in the sidebar to organize."),
          spotlight: ".gal-main",
          advance: "click", btn: _t("moduleTour.gallery.interact.btn", "Next")
        },
        {
          id: "gal_live",
          text: _t("moduleTour.gallery.live.text", "Pictures Studio saves appear here on their own. The dot in the topbar shows whether Studio is also watching your folders for files added outside Studio.\n\n<em>Send to Canvas</em> loads any image into Studio for editing."),
          spotlight: null,
          advance: "click", btn: _t("moduleTour.gallery.live.btn", "Got it"),
          position: { bottom: "80px", left: "calc(50% - 190px)" }
        },
      ] : [
        {
          id: "gal_empty",
          text: _t("moduleTour.gallery.empty.text", "This is the <em>Gallery</em> — a browser for your generated images.\n\nIt shows the folders you link. Press <em>Choose folder & scan</em> to link your Studio results folder, or any folder of pictures; the sidebar, search and grid appear once it has images."),
          spotlight: null,
          advance: "click", btn: _t("moduleTour.gallery.empty.btn", "Got it"),
          position: { bottom: "80px", left: "calc(50% - 190px)" }
        },
      ];
      StudioTour.create({
        id: "tour-gallery",
        steps: steps,
        persist: false,
        onComplete: function () { console.log(TAG, "Gallery tour complete"); },
        onCancel: function () {},
        confirmCancel: false,
      }).start(0);
    }, 200);
  });

} else {
  console.warn(TAG, "StudioModules not available");
}

console.log(TAG, "Module tours loaded");
})();
