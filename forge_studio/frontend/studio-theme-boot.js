// Forge Studio — UI1. The saved theme on <html> before the page paints.
//
// Loaded synchronously from <head> so the default theme does not flash
// first. A file rather than an inline script because the page's CSP allows
// inline scripts only by hash (presentation.py). studio-themes.js owns the
// choice; this only repeats its last answer, and a custom theme's cached
// rule, from localStorage.
(function () {
    "use strict";
    try {
        var root = document.documentElement;
        var t = localStorage.getItem("studio-theme");
        var renamed = { "": "studio", "terracotta": "oliver" };
        t = t === null ? "liam" : (Object.prototype.hasOwnProperty.call(renamed, t) ? renamed[t] : t);
        var css = localStorage.getItem("studio-theme-css");
        if (t.indexOf("custom-") === 0 && css) {
            var style = document.createElement("style");
            style.id = "studio-custom-theme";
            style.textContent = css;
            document.head.appendChild(style);
            t = "custom";
        } else if (t.indexOf("custom-") === 0) {
            t = "liam";
        }
        root.setAttribute("data-theme", t);
        var gradient = { toxic: "drift", neon: "shift" }[t];
        if (gradient) root.setAttribute("data-gradient", gradient);
    } catch (e) { /* storage blocked: the default theme */ }
})();
