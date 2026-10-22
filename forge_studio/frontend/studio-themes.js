// Forge Studio — themes: the built-in list, the applied choice, and custom
// themes.
//
// Exposes window.StudioThemes. Loaded early (after prefs.js) so the saved
// theme is on <html> before the page paints.
//
// Built-in themes are CSS blocks in app.css keyed by data-theme. A custom
// theme is a handful of colours the owner picks (background, text, accent,
// an optional second accent, a region colour); derive() works the rest of
// the palette out and lifts every grey until it reads, and the result is
// written as one [data-theme="custom"] rule in a <style> of its own. A rule,
// not inline styles: the colour-blind modes are `:root[data-cb]` rules and
// must still win over the theme, which an inline style would not let them.
//
// Where the choice lives: Studio's preference store (`theme`,
// `custom_themes`), so it survives a browser-storage clear and follows Studio
// to another browser. localStorage keeps a copy only so the first paint is
// already themed, before the preference GET returns.
//
// Contrast targets are the refresh's (Evidence/source-review/UI1-themes.md):
// hints 6.2:1 and faint text 4.6:1 on the raised ground, 4.5:1 for anything
// read as text, and the label on an accent fill as high as the two
// candidate colours allow.

(function () {
    "use strict";

    var THEME_KEY = "studio-theme";
    var CUSTOM_KEY = "studio-custom-themes";
    var CSS_KEY = "studio-theme-css";
    var DEFAULT_THEME = "liam";
    var STYLE_ID = "studio-custom-theme";

    // Gradient: "drift" (8 s) or "shift" (4 s); see THEME GRADIENTS in app.css.
    var BUILTIN = [
        { id: "liam", name: "Liam", ground: "#141414", accent: "#ff9b00" },
        { id: "studio", name: "Studio", ground: "#161619", accent: "#7b8fff" },
        { id: "oliver", name: "Oliver", ground: "#1b1917", accent: "#c8744a" },
        { id: "toxic", name: "ToxicHost", ground: "#110e16", accent: "#9070c0", accent2: "#a0c8a0", gradient: "drift" },
        { id: "neutral", name: "Neutral", ground: "#222222", accent: "#8ab4f8" },
        { id: "neon", name: "Neon", ground: "#0a0a10", accent: "#ffa9f3", accent2: "#63feff", gradient: "shift" },
        { id: "cream", name: "Cream Paper", ground: "#f4efe4", accent: "#51653e", light: true },
    ];
    // Saved choices from before the refresh: "" was the Studio button;
    // "terracotta" was Oliver's name on development builds for a day.
    var RENAMED = { "": "studio", "terracotta": "oliver" };
    var MOTIONS = ["still", "drift", "shift"];

    // ── Colour arithmetic ───────────────────────────────────────────────

    function parse(hex) {
        var h = String(hex || "").trim().replace(/^#/, "");
        if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
        if (!/^[0-9a-fA-F]{6}$/.test(h.slice(0, 6))) return null;
        return [0, 2, 4].map(function (i) { return parseInt(h.slice(i, i + 2), 16) / 255; });
    }
    function toHex(rgb) {
        return "#" + rgb.map(function (v) {
            var n = Math.max(0, Math.min(255, Math.round(v * 255)));
            return (n < 16 ? "0" : "") + n.toString(16);
        }).join("");
    }
    function normalize(hex) {
        var rgb = parse(hex);
        return rgb ? toHex(rgb) : null;
    }
    function lin(c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    function luminance(hex) {
        var rgb = parse(hex);
        if (!rgb) return 0;
        return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
    }
    function contrast(a, b) {
        var la = luminance(a), lb = luminance(b);
        if (la < lb) { var t = la; la = lb; lb = t; }
        return (la + 0.05) / (lb + 0.05);
    }
    function mix(a, b, t) {
        var x = parse(a), y = parse(b);
        return toHex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * t; }));
    }
    function toHsl(hex) {
        var rgb = parse(hex), r = rgb[0], g = rgb[1], b = rgb[2];
        var max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, h = 0, s = 0;
        if (max !== min) {
            var d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
            else if (max === g) h = (b - r) / d + 2;
            else h = (r - g) / d + 4;
            h /= 6;
        }
        return [h, s, l];
    }
    function fromHsl(h, s, l) {
        function hue(p, q, t) {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        }
        if (s === 0) return toHex([l, l, l]);
        var q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        return toHex([hue(p, q, h + 1 / 3), hue(p, q, h), hue(p, q, h - 1 / 3)]);
    }
    // Move a colour's lightness (keeping hue and saturation) away from the
    // ground until it reaches the target ratio. Returns the closest it got.
    function reach(hex, ground, target) {
        if (contrast(hex, ground) >= target) return hex;
        var hsl = toHsl(hex), up = luminance(ground) < 0.18;
        for (var l = hsl[2]; l >= 0 && l <= 1; l += up ? 0.004 : -0.004) {
            var cand = fromHsl(hsl[0], hsl[1], l);
            if (contrast(cand, ground) >= target) return cand;
        }
        return up ? "#ffffff" : "#000000";
    }
    function hueDistance(a, b) {
        var d = Math.abs(toHsl(a)[0] - toHsl(b)[0]) * 360;
        return Math.min(d, 360 - d);
    }
    function alpha(hex, aa) { return hex + aa; }

    // ── Deriving a palette from a custom theme ──────────────────────────

    var STATUS = {
        dark: { green: "#5dca85", amber: "#efaa27", red: "#e24b4a" },
        light: { green: "#206a63", amber: "#845a0a", red: "#bf201f" },
        // Used instead when the accent is too close to the usual one.
        alt: { green: "#6fc2a6", amber: "#f5d547", red: "#ea5f78" },
    };
    var OKABE_ITO = {
        deutan: { accent: "#0072B2", green: "#56B4E9", red: "#D55E00", amber: "#E69F00" },
        protan: { accent: "#0072B2", green: "#56B4E9", red: "#D55E00", amber: "#E69F00" },
        tritan: { accent: "#CC79A7", green: "#009E73", red: "#D55E00", amber: "#D55E00" },
    };

    function cleanSpec(raw) {
        var s = raw && typeof raw === "object" ? raw : {};
        var motion = MOTIONS.indexOf(s.motion) >= 0 ? s.motion : "still";
        return {
            id: typeof s.id === "string" && /^custom-[a-z0-9-]{1,40}$/.test(s.id) ? s.id : "",
            name: String(s.name || "").trim().slice(0, 40) || "My theme",
            background: normalize(s.background) || "#141414",
            text: normalize(s.text) || "#f0f0f0",
            accent: normalize(s.accent) || "#ff9b00",
            accent2: normalize(s.accent2) || "",
            gradient: !!s.gradient && !!normalize(s.accent2),
            motion: motion,
            regions: normalize(s.regions) || "#d4537e",
        };
    }

    // Returns { vars, light, gradient, report: [{key, ratio, target}], notes: [key] }.
    function derive(rawSpec) {
        var s = cleanSpec(rawSpec);
        var light = luminance(s.background) > 0.35;
        var bg = s.background, text = s.text, v = {};
        var notes = [];
        if (light) {
            v.surface = bg;
            v.void = mix(bg, text, 0.05);
            v.raised = mix(bg, text, 0.045);
            v.input = mix(bg, "#ffffff", 0.6);
            v.borderSubtle = mix(bg, text, 0.1);
            v.border = mix(bg, text, 0.15);
            v.borderHover = mix(bg, text, 0.24);
            v.canvasVoid = mix(text, "#000000", 0.3);
        } else {
            v.void = bg;
            v.surface = mix(bg, text, 0.035);
            v.raised = mix(bg, text, 0.06);
            v.input = mix(bg, "#000000", 0.3);
            v.borderSubtle = mix(bg, text, 0.09);
            v.border = mix(bg, text, 0.12);
            v.borderHover = mix(bg, text, 0.19);
            v.canvasVoid = bg;
        }
        var raised = v.raised, surface = v.surface;
        v.text1 = reach(text, surface, 7);
        v.text2 = reach(mix(text, surface, 0.2), raised, 7);
        v.text3 = reach(mix(text, surface, 0.42), raised, 6.2);
        v.text4 = reach(mix(text, surface, 0.55), raised, 4.6);

        // A light ground needs a darker accent to be read on it at all.
        v.accent = light ? reach(s.accent, raised, 4.5) : s.accent;
        v.accentBright = light ? reach(v.accent, raised, 6) : reach(mix(v.accent, "#ffffff", 0.2), raised, 4.5);
        v.accent2 = s.gradient ? s.accent2 : v.accent;
        v.accentBlend = mix(v.accent, v.accent2, 0.5);
        var fills = s.gradient ? [v.accent, v.accentBlend, v.accent2] : [v.accent];
        var dark = luminance(v.void) < luminance(v.text1) ? v.void : v.text1;
        var lightest = dark === v.void ? v.text1 : v.void;
        var worst = function (c) { return Math.min.apply(null, fills.map(function (f) { return contrast(c, f); })); };
        var candidates = [dark, lightest, "#ffffff", "#0a0a0a"];
        v.onAccent = candidates.reduce(function (best, c) { return worst(c) > worst(best) ? c : best; }, candidates[0]);

        var base = light ? STATUS.light : STATUS.dark;
        ["green", "amber", "red"].forEach(function (k) {
            var c = base[k];
            var close = [v.accent, v.accent2].some(function (a) {
                return hueDistance(a, c) < 28 && toHsl(a)[1] > 0.25;
            });
            if (close) {
                c = STATUS.alt[k];
                notes.push("status." + k);
            }
            v[k] = reach(c, raised, 4.8);
        });
        v.pink = reach(s.regions, raised, 4.5);

        var a1 = light ? "1a" : "22", a2 = light ? "40" : "44";
        var vars = {
            "--bg-void": v.void, "--bg-surface": v.surface, "--bg-raised": v.raised, "--bg-input": v.input,
            "--border": v.border, "--border-subtle": v.borderSubtle, "--border-hover": v.borderHover,
            "--text-1": v.text1, "--text-2": v.text2, "--text-3": v.text3, "--text-4": v.text4,
            "--accent": v.accent, "--accent-dim": alpha(v.accent, a1), "--accent-mid": alpha(v.accent, a2),
            "--accent-bright": v.accentBright, "--on-accent": v.onAccent,
            "--accent-2": v.accent2, "--accent-blend": v.accentBlend, "--accent-active": v.accent,
            "--canvas-void": v.canvasVoid,
            "--dock-shadow": light ? "0 -6px 14px rgba(0, 0, 0, 0.12)" : "0 -8px 16px rgba(0, 0, 0, 0.35)",
            "--overlay": light ? "rgba(0, 0, 0, 0.35)" : "rgba(0, 0, 0, 0.55)",
            "--pink": v.pink, "--pink-dim": alpha(v.pink, a1),
            "--green": v.green, "--green-dim": alpha(v.green, a1),
            "--amber": v.amber, "--amber-dim": alpha(v.amber, a1),
            "--red": v.red, "--red-dim": alpha(v.red, a1),
        };
        // The colour-blind modes on this theme: Okabe-Ito's hues, moved until
        // they read on its raised ground.
        var colorblind = {};
        Object.keys(OKABE_ITO).forEach(function (mode) {
            var o = OKABE_ITO[mode];
            var acc = reach(o.accent, surface, 3);
            var on = [dark, lightest, "#ffffff"].reduce(function (b, c) { return contrast(c, acc) > contrast(b, acc) ? c : b; }, dark);
            var g = reach(o.green, raised, 4.5), r = reach(o.red, raised, 4.5), am = reach(o.amber, raised, 4.5);
            colorblind[mode] = {
                "--accent": acc, "--accent-dim": alpha(acc, a1), "--accent-mid": alpha(acc, a2),
                "--accent-bright": reach(acc, raised, 4.5), "--on-accent": on,
                "--green": g, "--green-dim": alpha(g, a1), "--red": r, "--red-dim": alpha(r, a1),
                "--amber": am, "--amber-dim": alpha(am, a1),
            };
        });
        var report = [
            { key: "label", ratio: worst(v.onAccent), target: 4.5 },
            { key: "text", ratio: contrast(v.text1, surface), target: 7 },
            { key: "hints", ratio: contrast(v.text3, raised), target: 6.2 },
            { key: "faint", ratio: contrast(v.text4, raised), target: 4.5 },
            { key: "accent", ratio: contrast(v.accent, surface), target: 3 },
        ];
        return {
            spec: s, vars: vars, colorblind: colorblind, light: light,
            gradient: s.gradient ? s.motion : "", report: report, notes: notes,
        };
    }

    function cssFor(derived) {
        var body = function (map) {
            return Object.keys(map).map(function (k) { return "  " + k + ": " + map[k] + ";"; }).join("\n");
        };
        var out = ':root[data-theme="custom"] {\n  color-scheme: ' + (derived.light ? "light" : "dark") + ";\n" + body(derived.vars) + "\n}\n";
        Object.keys(derived.colorblind).forEach(function (mode) {
            out += ':root[data-theme="custom"][data-cb="' + mode + '"] {\n' + body(derived.colorblind[mode]) + "\n}\n";
        });
        return out;
    }

    // ── Storage ─────────────────────────────────────────────────────────

    function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* best effort */ } }
    function prefs() { return window.Prefs && typeof window.Prefs.get === "function" ? window.Prefs : null; }

    var _customs = [];
    var _current = DEFAULT_THEME;

    function readLocalCustoms() {
        try {
            var list = JSON.parse(lsGet(CUSTOM_KEY) || "[]");
            return Array.isArray(list) ? list.map(cleanSpec).filter(function (s) { return s.id; }) : [];
        } catch (e) { return []; }
    }
    function announceCustoms() {
        try { document.dispatchEvent(new CustomEvent("studio:custom-themes-changed")); } catch (e) { /* nothing listens */ }
    }
    function writeCustoms() {
        lsSet(CUSTOM_KEY, JSON.stringify(_customs));
        var p = prefs();
        if (p) window.Prefs.set("custom_themes", _customs);
        announceCustoms();
    }
    function resolve(id) {
        if (id === null || id === undefined) return DEFAULT_THEME;
        if (Object.prototype.hasOwnProperty.call(RENAMED, id)) return RENAMED[id];
        if (BUILTIN.some(function (t) { return t.id === id; })) return id;
        if (_customs.some(function (t) { return t.id === id; })) return id;
        return DEFAULT_THEME;
    }

    // ── Applying ────────────────────────────────────────────────────────

    function customStyle(css) {
        var el = document.getElementById(STYLE_ID);
        if (!css) { if (el) el.remove(); return; }
        if (!el) {
            el = document.createElement("style");
            el.id = STYLE_ID;
            document.head.appendChild(el);
        }
        el.textContent = css;
    }

    function paint(id) {
        var root = document.documentElement;
        var builtin = BUILTIN.filter(function (t) { return t.id === id; })[0];
        if (builtin) {
            customStyle("");
            lsSet(CSS_KEY, "");
            root.setAttribute("data-theme", id);
            if (builtin.gradient) root.setAttribute("data-gradient", builtin.gradient);
            else root.removeAttribute("data-gradient");
        } else {
            var spec = _customs.filter(function (t) { return t.id === id; })[0];
            var d = derive(spec);
            var css = cssFor(d);
            customStyle(css);
            // index.html's first-paint script injects this before the page
            // draws, so a custom theme does not flash the default first.
            lsSet(CSS_KEY, css);
            root.setAttribute("data-theme", "custom");
            root.setAttribute("data-custom-theme", id);
            if (d.gradient) root.setAttribute("data-gradient", d.gradient);
            else root.removeAttribute("data-gradient");
        }
        if (builtin) root.removeAttribute("data-custom-theme");
        // The canvas caches its surround colour.
        if (window.StudioCore && window.StudioCore.state) {
            window.StudioCore.state._voidColor = null;
            if (typeof window.StudioCore.markCompositeDirty === "function") window.StudioCore.markCompositeDirty();
            if (typeof window.StudioCore.composite === "function") window.StudioCore.composite();
        }
        try {
            document.dispatchEvent(new CustomEvent("studio:theme-changed", { detail: { theme: id } }));
        } catch (e) { /* no CustomEvent: nothing listens */ }
    }

    function apply(id, options) {
        _current = resolve(id);
        paint(_current);
        lsSet(THEME_KEY, _current);
        if (!options || options.persist !== false) {
            var p = prefs();
            if (p) window.Prefs.set("theme", _current);
        }
        return _current;
    }

    function newId(name) {
        var slug = String(name || "theme").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24) || "theme";
        var id = "custom-" + slug, n = 2;
        while (_customs.some(function (t) { return t.id === id; })) id = "custom-" + slug + "-" + (n++);
        return id;
    }

    function saveCustom(rawSpec) {
        var spec = cleanSpec(rawSpec);
        if (!spec.id || !_customs.some(function (t) { return t.id === spec.id; })) {
            spec.id = spec.id || newId(spec.name);
            _customs.push(spec);
        } else {
            _customs = _customs.map(function (t) { return t.id === spec.id ? spec : t; });
        }
        writeCustoms();
        return spec;
    }

    function deleteCustom(id) {
        _customs = _customs.filter(function (t) { return t.id !== id; });
        writeCustoms();
        if (_current === id) apply(DEFAULT_THEME);
    }

    // A shareable file: the picks, not the derived palette, so it follows
    // any later improvement to derive().
    function exportCustom(id) {
        var spec = _customs.filter(function (t) { return t.id === id; })[0];
        if (!spec) return null;
        var copy = Object.assign({}, spec);
        delete copy.id;
        return JSON.stringify({ format: "forge-studio-theme", version: 1, theme: copy }, null, 2);
    }

    function importCustom(text) {
        var data = JSON.parse(text);
        if (!data || data.format !== "forge-studio-theme" || !data.theme) throw new Error("not a theme file");
        var spec = cleanSpec(data.theme);
        spec.id = "";
        return saveCustom(spec);
    }

    // ── Boot ────────────────────────────────────────────────────────────

    function init() {
        _customs = readLocalCustoms();
        apply(lsGet(THEME_KEY), { persist: false });
        var p = prefs();
        if (p && typeof p.load === "function") {
            p.load().then(function () {
                var stored = window.Prefs.get("custom_themes", null);
                if (Array.isArray(stored)) {
                    _customs = stored.map(cleanSpec).filter(function (s) { return s.id; });
                    lsSet(CUSTOM_KEY, JSON.stringify(_customs));
                    announceCustoms();
                } else if (_customs.length) {
                    window.Prefs.set("custom_themes", _customs);
                }
                if (window.Prefs.has("theme")) {
                    var server = resolve(window.Prefs.get("theme"));
                    if (server !== _current) apply(server, { persist: false });
                } else {
                    window.Prefs.set("theme", _current);
                }
            }).catch(function () { /* stays on the local copy */ });
        }
    }

    window.StudioThemes = {
        BUILTIN: BUILTIN,
        DEFAULT_THEME: DEFAULT_THEME,
        init: init,
        apply: apply,
        current: function () { return _current; },
        customs: function () { return _customs.map(function (t) { return Object.assign({}, t); }); },
        saveCustom: saveCustom,
        deleteCustom: deleteCustom,
        exportCustom: exportCustom,
        importCustom: importCustom,
        derive: derive,
        cssFor: cssFor,
        contrast: contrast,
        resolve: resolve,
    };
    // In a browser, theme the page as soon as this loads. Node tests load
    // the file without a DOM and call derive() directly.
    if (typeof document !== "undefined" && document.documentElement) init();
})();
