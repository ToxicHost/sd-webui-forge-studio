// Forge Studio — Settings › Appearance › Theme: the theme tiles and the
// custom theme editor. The palette arithmetic is studio-themes.js's; this
// file only reads the form, shows derive()'s result and its readability
// report, and saves through StudioThemes.
//
// Markup lives in index.html (#themeSelector, #themeEditor) so every label
// is a data-i18n string like the rest of Settings.

(function () {
    "use strict";

    var T = window.StudioThemes;
    if (!T) return;

    function _i18n(key, fallback, params) {
        return (window.I18N && window.I18N.t) ? window.I18N.t(key, fallback, params) : fallback;
    }
    function toast(text, kind) {
        if (typeof window.showToast === "function") window.showToast(text, kind || "info");
    }
    function $(id) { return document.getElementById(id); }

    var FIELDS = ["background", "text", "accent", "accent2", "regions"];
    var _editing = null;   // spec being edited, or null when the editor is shut

    // ── Tiles ───────────────────────────────────────────────────────────

    function swatch(ground, accent, accent2) {
        var sw = document.createElement("span");
        sw.className = "theme-swatch";
        sw.setAttribute("aria-hidden", "true");
        sw.style.background = ground;
        var bar = document.createElement("span");
        bar.className = "theme-swatch-bar";
        bar.style.background = accent2 ? "linear-gradient(90deg, " + accent + ", " + accent2 + ")" : accent;
        sw.appendChild(bar);
        return sw;
    }

    function renderTiles() {
        var host = $("themeSelector");
        if (!host) return;
        // Built-in buttons are static markup; give each its swatch once.
        T.BUILTIN.forEach(function (t) {
            var btn = host.querySelector('.theme-btn[data-theme="' + t.id + '"]');
            if (btn && !btn.querySelector(".theme-swatch")) btn.insertBefore(swatch(t.ground, t.accent, t.accent2), btn.firstChild);
        });
        host.querySelectorAll(".theme-btn-custom, .theme-btn-new").forEach(function (el) { el.remove(); });
        T.customs().forEach(function (spec) {
            var d = T.derive(spec);
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "theme-btn theme-btn-custom";
            btn.dataset.theme = spec.id;
            btn.appendChild(swatch(d.vars["--bg-surface"], d.vars["--accent"], d.gradient ? d.vars["--accent-2"] : ""));
            btn.appendChild(document.createTextNode(spec.name));
            host.appendChild(btn);
        });
        var add = document.createElement("button");
        add.type = "button";
        add.className = "theme-btn theme-btn-new";
        add.id = "themeNewBtn";
        add.appendChild(Object.assign(document.createElement("span"), { className: "theme-swatch theme-swatch-new", textContent: "+" }));
        add.appendChild(document.createTextNode(_i18n("settings.theme.custom.new", "New theme")));
        host.appendChild(add);
        markActive();
    }

    function markActive() {
        var current = T.current();
        document.querySelectorAll("#themeSelector .theme-btn").forEach(function (btn) {
            var on = btn.dataset.theme === current;
            btn.classList.toggle("active", on);
            btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        var edit = $("themeEditBtn");
        if (edit) edit.hidden = !T.customs().some(function (t) { return t.id === current; });
    }

    // ── Editor ──────────────────────────────────────────────────────────

    function seedFrom(id) {
        var custom = T.customs().filter(function (t) { return t.id === id; })[0];
        if (custom) return custom;
        var b = T.BUILTIN.filter(function (t) { return t.id === id; })[0] || T.BUILTIN[0];
        var css = getComputedStyle(document.documentElement);
        var read = function (name, fallback) { return (css.getPropertyValue(name) || "").trim() || fallback; };
        return {
            id: "",
            name: _i18n("settings.theme.custom.defaultName", "My theme"),
            background: read("--bg-void", b.ground),
            text: read("--text-1", b.light ? "#2a251e" : "#f0f0f0"),
            accent: b.accent,
            accent2: b.accent2 || "",
            gradient: !!b.accent2,
            motion: b.gradient || "still",
            regions: read("--pink", "#d4537e"),
        };
    }

    function openEditor(spec) {
        _editing = Object.assign({}, spec);
        var box = $("themeEditor");
        if (!box) return;
        $("themeEditorName").value = _editing.name || "";
        FIELDS.forEach(function (f) {
            var v = _editing[f] || (f === "accent2" ? _editing.accent : "#000000");
            var pick = $("themeEditor-" + f), hex = $("themeEditor-" + f + "-hex");
            if (pick) pick.value = v;
            if (hex) hex.value = v;
        });
        $("themeEditorGradient").checked = !!_editing.gradient;
        $("themeEditorMotion").value = _editing.motion || "still";
        $("themeEditorDelete").hidden = !_editing.id;
        $("themeEditorExport").hidden = !_editing.id;
        box.hidden = false;
        update();
        $("themeEditorName").focus();
    }

    function closeEditor() {
        _editing = null;
        var box = $("themeEditor");
        if (box) box.hidden = true;
    }

    function readForm() {
        var s = Object.assign({}, _editing);
        s.name = $("themeEditorName").value;
        FIELDS.forEach(function (f) { s[f] = $("themeEditor-" + f + "-hex").value; });
        s.gradient = $("themeEditorGradient").checked;
        s.motion = $("themeEditorMotion").value;
        return s;
    }

    function update() {
        if (!_editing) return;
        var spec = readForm();
        var d = T.derive(spec);
        var gradientOn = !!d.gradient;
        $("themeEditorMotionRow").hidden = !gradientOn;
        $("themeEditorMotionHint").hidden = !gradientOn;
        // Preview: the derived tokens set on the preview box only.
        var prev = $("themePreview");
        if (prev) {
            Object.keys(d.vars).forEach(function (k) { prev.style.setProperty(k, d.vars[k]); });
            var gen = $("themePreviewGenerate");
            gen.style.background = gradientOn
                ? "linear-gradient(135deg, " + d.vars["--accent"] + ", " + d.vars["--accent-blend"] + ", " + d.vars["--accent-2"] + ")"
                : d.vars["--accent"];
            gen.style.backgroundSize = gradientOn ? "200% 200%" : "";
            gen.dataset.motion = gradientOn ? d.gradient : "";
        }
        var list = $("themeEditorReport");
        if (list) {
            list.textContent = "";
            d.report.forEach(function (r) {
                var row = document.createElement("li");
                row.className = "theme-check " + (r.ratio >= r.target ? "pass" : "fail");
                var label = document.createElement("span");
                label.textContent = _i18n("settings.theme.custom.check." + r.key, r.key);
                var num = document.createElement("span");
                num.className = "theme-check-num";
                num.textContent = r.ratio.toFixed(1) + ":1";
                row.appendChild(label);
                row.appendChild(num);
                list.appendChild(row);
            });
            d.notes.forEach(function (n) {
                var row = document.createElement("li");
                row.className = "theme-check note";
                row.textContent = _i18n("settings.theme.custom.note." + n, n);
                list.appendChild(row);
            });
        }
    }

    function syncPair(field, fromPicker) {
        var pick = $("themeEditor-" + field), hex = $("themeEditor-" + field + "-hex");
        if (fromPicker) hex.value = pick.value;
        else if (/^#[0-9a-fA-F]{6}$/.test(hex.value.trim())) pick.value = hex.value.trim();
        update();
    }

    function save() {
        var spec = T.saveCustom(readForm());
        T.apply(spec.id);
        closeEditor();
        renderTiles();
        toast(_i18n("toast.theme.saved", "Theme saved"), "success");
    }

    function exportTheme() {
        if (!_editing || !_editing.id) return;
        var json = T.exportCustom(_editing.id);
        if (!json) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([json], { type: "application/json" }));
        a.download = (_editing.name || "theme").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-") + ".studio-theme.json";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    }

    function importTheme(file) {
        var reader = new FileReader();
        reader.onload = function () {
            try {
                var spec = T.importCustom(String(reader.result || ""));
                T.apply(spec.id);
                renderTiles();
                toast(_i18n("toast.theme.imported", "Theme imported: {name}", { name: spec.name }), "success");
            } catch (e) {
                toast(_i18n("toast.theme.importFailed", "That file is not a Forge Studio theme"), "error");
            }
        };
        reader.readAsText(file);
    }

    async function remove() {
        if (!_editing || !_editing.id) return;
        var name = _editing.name;
        if (!(await window.studioConfirm(_i18n("settings.theme.custom.deleteConfirm", "Delete the theme “{name}”?", { name: name }),
            { confirm: _i18n("dialog.delete", "Delete"), danger: true }))) return;
        T.deleteCustom(_editing.id);
        closeEditor();
        renderTiles();
        toast(_i18n("toast.theme.deleted", "Theme deleted"), "info");
    }

    // ── Wiring ──────────────────────────────────────────────────────────

    function bind() {
        var host = $("themeSelector");
        if (!host || host.dataset.themesBound) return;
        host.dataset.themesBound = "1";
        host.addEventListener("click", function (e) {
            var btn = e.target.closest(".theme-btn");
            if (!btn) return;
            if (btn.classList.contains("theme-btn-new")) { openEditor(seedFrom(T.current())); return; }
            T.apply(btn.dataset.theme);
            markActive();
        });
        var edit = $("themeEditBtn");
        if (edit) edit.addEventListener("click", function () { openEditor(seedFrom(T.current())); });
        var imp = $("themeImportBtn"), file = $("themeImportFile");
        if (imp && file) {
            imp.addEventListener("click", function () { file.click(); });
            file.addEventListener("change", function () {
                if (file.files && file.files[0]) importTheme(file.files[0]);
                file.value = "";
            });
        }
        if (!$("themeEditor")) return;
        $("themeEditorName").addEventListener("input", update);
        FIELDS.forEach(function (f) {
            $("themeEditor-" + f).addEventListener("input", function () { syncPair(f, true); });
            $("themeEditor-" + f + "-hex").addEventListener("input", function () { syncPair(f, false); });
        });
        $("themeEditorGradient").addEventListener("change", update);
        $("themeEditorMotion").addEventListener("change", update);
        $("themeEditorSave").addEventListener("click", save);
        $("themeEditorClose").addEventListener("click", closeEditor);
        $("themeEditorExport").addEventListener("click", exportTheme);
        $("themeEditorDelete").addEventListener("click", remove);
    }

    function init() {
        bind();
        renderTiles();
    }

    document.addEventListener("studio:theme-changed", markActive);
    document.addEventListener("studio:custom-themes-changed", renderTiles);
    window.addEventListener("i18n:change", renderTiles);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();

    window.StudioThemesUI = { render: renderTiles, open: openEditor, close: closeEditor };
})();
