/**
 * Forge Studio — Searchable Select widget
 *
 * Wraps a native <select> without rewriting its change handlers. The
 * native select stays in the DOM as the source of truth (visually
 * hidden); this component renders a styled trigger button next to it
 * and a floating panel with a filter input + filtered options on
 * click. Selecting an option writes back to the native select via
 * `select.value = …` and dispatches a bubbling "change" event so
 * existing listeners fire untouched.
 *
 * A MutationObserver watches the native select for option changes
 * (callers can repopulate `<option>` lists freely without notifying
 * the wrapper). The panel also lazily re-reads options each time it
 * opens, so even if the observer misses something the user always
 * sees the current set.
 *
 * Optgroups are rendered as non-interactive section headers above
 * their child options. Keyboard nav mirrors the wildcard browser in
 * lexicon.js — ↑/↓ move focus (with wrap), Enter selects, Escape
 * closes.
 *
 * MF1. An optgroup carrying `data-collapsible` is a FOLDER: its header
 * folds the options away on click, and what is folded is remembered per
 * select id and folder in localStorage. The folder holding the selected
 * option opens whenever the panel opens; typing in the filter searches
 * folded folders too and shows the matches open. Plain optgroups (the
 * Hires upscaler's Latent / Image) are unchanged.
 *
 * API:
 *   const handle = StudioSearchableSelect.attach(selectEl, opts?);
 *   handle.refresh();   // force re-read from native select
 *   handle.destroy();   // restore native select
 *   StudioSearchableSelect.groupByFolder(items, folderOf)
 *                       // [{folder: "", items}, {folder: "A", items}, …]
 *
 * Options:
 *   placeholder        — text shown when value is empty/missing
 *   searchPlaceholder  — placeholder for the filter input
 */
(function () {
"use strict";

const ATTR_KEY = "_studioSSelHandle";

function _esc(s) {
    if (s === null || s === undefined) return "";
    const d = document.createElement("div");
    d.textContent = String(s);
    return d.innerHTML;
}

function _readOptions(select) {
    // Walks the native select's children, collecting flat options +
    // optgroup boundaries. Output is a flat list where each entry has
    // either {kind: "group", label} or {kind: "option", value, text,
    // disabled, title, selected}.
    const out = [];
    for (const node of select.children) {
        if (node.tagName === "OPTGROUP") {
            out.push({
                kind: "group",
                label: node.label || "",
                collapsible: node.hasAttribute("data-collapsible"),
            });
            for (const opt of node.children) {
                if (opt.tagName !== "OPTION") continue;
                out.push({
                    kind: "option",
                    value: opt.value,
                    text: opt.textContent,
                    disabled: !!opt.disabled,
                    title: opt.title || "",
                    selected: !!opt.selected,
                    inGroup: true,
                });
            }
        } else if (node.tagName === "OPTION") {
            out.push({
                kind: "option",
                value: node.value,
                text: node.textContent,
                disabled: !!node.disabled,
                title: node.title || "",
                selected: !!node.selected,
            });
        }
    }
    return out;
}

function _visibleItems(all, state) {
    // `_readOptions`' list -> what the panel shows. A group whose every
    // option is filtered out does not render. A FOLDER group (collapsible)
    // shows its header with a count, and hides its options while folded;
    // a filter query opens every folder, and `forcedOpen` -- the folder
    // holding the selected option -- is open whatever is stored.
    state = state || {};
    const q = (state.query || "").trim().toLowerCase();
    const folded = state.folded || new Set();
    const out = [];
    let group = null;
    for (const item of all) {
        if (item.kind === "group") {
            group = { label: item.label, collapsible: !!item.collapsible, entry: null };
            continue;
        }
        if (!item.inGroup) group = null;
        if (q && !item.text.toLowerCase().includes(q)) continue;
        if (group && !group.entry) {
            group.entry = { kind: "group", label: group.label, collapsible: group.collapsible,
                            folded: false, searching: !!q, count: 0 };
            if (group.collapsible && !q && folded.has(group.label)
                && group.label !== state.forcedOpen) {
                group.entry.folded = true;
            }
            out.push(group.entry);
        }
        if (group) {
            group.entry.count++;
            if (group.entry.folded) continue;
        }
        out.push(group && group.collapsible ? Object.assign({ inFolder: true }, item) : item);
    }
    return out;
}

function _folderOf(all, value) {
    // The folder (collapsible group) an option value sits in, or null.
    let group = null;
    for (const item of all) {
        if (item.kind === "group") { group = item.collapsible ? item.label : null; continue; }
        if (!item.inGroup) group = null;
        if (item.value === value) return group;
    }
    return null;
}

function groupByFolder(items, folderOf) {
    // MF1. Top-level items first (folder ""), then one entry per folder,
    // folders sorted case-insensitively; items keep their order inside a
    // folder. The first entry is always the top level, possibly empty.
    const top = [];
    const byFolder = new Map();
    for (const item of items || []) {
        const folder = String(folderOf(item) || "");
        if (!folder) { top.push(item); continue; }
        if (!byFolder.has(folder)) byFolder.set(folder, []);
        byFolder.get(folder).push(item);
    }
    const folders = [...byFolder.keys()].sort((a, b) => {
        const x = a.toLowerCase(), y = b.toLowerCase();
        if (x !== y) return x < y ? -1 : 1;
        return a < b ? -1 : a > b ? 1 : 0;
    });
    return [{ folder: "", items: top }].concat(
        folders.map(folder => ({ folder, items: byFolder.get(folder) })));
}

function _nextOptIdx(visible, from, dir) {
    // Wrap around, skipping group headers and disabled options. A folded
    // folder's options are not in `visible`, so they are skipped too.
    const n = visible.length;
    if (n === 0) return -1;
    let i = from < 0 ? (dir > 0 ? -1 : 0) : from;
    for (let step = 0; step < n; step++) {
        i = ((i + dir) % n + n) % n;
        const it = visible[i];
        if (it.kind === "option" && !it.disabled) return i;
    }
    return -1;
}

const FOLDED_KEY_PREFIX = "studio-ssel-folded:";

function _loadFolded(id) {
    // Never throws: a private window or blocked storage folds nothing.
    if (!id) return new Set();
    try {
        const raw = window.localStorage.getItem(FOLDED_KEY_PREFIX + id);
        const list = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(list) ? list.filter(x => typeof x === "string") : []);
    } catch (_) {
        return new Set();
    }
}

function _saveFolded(id, folded) {
    if (!id) return;
    try {
        window.localStorage.setItem(FOLDED_KEY_PREFIX + id, JSON.stringify([...folded]));
    } catch (_) { /* in-memory only */ }
}

function _currentLabel(select, placeholder) {
    const opt = select.options[select.selectedIndex];
    if (!opt || opt.value === "") return placeholder || "— Select —";
    return opt.textContent;
}

function attach(select, opts) {
    if (!select || !(select instanceof HTMLSelectElement)) return null;
    if (select[ATTR_KEY]) return select[ATTR_KEY];   // already wrapped
    opts = opts || {};
    const placeholder = opts.placeholder || "— Select —";
    const searchPlaceholder = opts.searchPlaceholder || "Search…";

    // Build the trigger element. We carry over the native select's
    // class list (minus a couple of layout-only classes that don't
    // make sense on a button) so existing CSS still positions the
    // wrapper the same as the original select.
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "ssel-trigger " + (select.className || "");
    if (select.title) trigger.title = select.title;
    trigger.innerHTML = '<span class="ssel-trigger-label"></span>'
        + '<span class="ssel-trigger-chevron" aria-hidden="true">▾</span>';
    const triggerLabel = trigger.querySelector(".ssel-trigger-label");
    triggerLabel.textContent = _currentLabel(select, placeholder);

    // Inline-block the wrapper alongside the (hidden) native select
    // so any flex/grid layout the select sat in still flows the same.
    select.classList.add("ssel-native-hidden");
    select.parentNode.insertBefore(trigger, select);

    let panel = null;
    let listEl = null;
    let searchEl = null;
    let kbIdx = -1;     // index into the *flat visible* option list
    // MF1. Folded folders, remembered per select id; and the folder held
    // open for this opening of the panel because it holds the selection.
    const folded = _loadFolded(select.id);
    let forcedOpen = null;

    function _close() {
        if (!panel) return;
        document.removeEventListener("click", _outsideClick, true);
        document.removeEventListener("keydown", _hostKey, true);
        panel.remove();
        panel = null; listEl = null; searchEl = null; kbIdx = -1;
        trigger.classList.remove("ssel-trigger-open");
        // Mirror native select focus/blur so callers that bind
        // `select.addEventListener("focus" | "blur", …)` (e.g. the
        // workshop's row-reference highlight) keep working without
        // having to know about the wrapper.
        select.dispatchEvent(new Event("blur", { bubbles: false }));
    }

    function _outsideClick(e) {
        if (!panel) return;
        if (panel.contains(e.target) || trigger.contains(e.target)) return;
        _close();
    }

    function _hostKey(e) {
        if (!panel) return;
        if (e.key === "Escape") {
            e.preventDefault();
            _close();
            trigger.focus();
            return;
        }
    }

    function _renderList() {
        const visible = _visibleItems(_readOptions(select), {
            query: searchEl?.value || "",
            folded,
            forcedOpen,
        });

        if (visible.length === 0) {
            var noMatches = (window.I18N && window.I18N.t) ? window.I18N.t("searchableSelect.noMatches", "No matches") : "No matches";
            listEl.innerHTML = '<div class="ssel-empty">' + noMatches + '</div>';
            kbIdx = -1;
            return;
        }

        const toggleTip = (window.I18N && window.I18N.t)
            ? window.I18N.t("searchableSelect.toggleFolder", "Show or hide this folder")
            : "Show or hide this folder";
        let html = "";
        for (let i = 0; i < visible.length; i++) {
            const it = visible[i];
            if (it.kind === "group" && it.collapsible) {
                html += '<div class="ssel-optgroup-header ssel-folder'
                    + (it.folded ? " folded" : "")
                    + (it.searching ? " searching" : "") + '"'
                    + (it.searching ? "" : ' role="button" title="' + _esc(toggleTip) + '"')
                    + ' aria-expanded="' + (it.folded ? "false" : "true") + '"'
                    + ' data-idx="' + i + '">'
                    + '<span class="ssel-folder-chevron" aria-hidden="true">' + (it.folded ? "▸" : "▾") + '</span>'
                    + '<span class="ssel-folder-label">' + _esc(it.label) + '</span>'
                    + '<span class="ssel-folder-count">' + it.count + '</span>'
                    + '</div>';
            } else if (it.kind === "group") {
                html += '<div class="ssel-optgroup-header">' + _esc(it.label) + '</div>';
            } else {
                const cls = "ssel-option"
                    + (it.inFolder ? " in-folder" : "")
                    + (it.value === select.value ? " selected" : "")
                    + (it.disabled ? " disabled" : "");
                html += '<div class="' + cls + '" role="option"'
                    + ' data-idx="' + i + '"'
                    + ' data-value="' + _esc(it.value) + '"'
                    + (it.title ? ' title="' + _esc(it.title) + '"' : "")
                    + '>' + _esc(it.text) + '</div>';
            }
        }
        listEl.innerHTML = html;

        // Pick an initial keyboard focus: the currently-selected
        // option if it's still visible, else the first option.
        kbIdx = visible.findIndex(it => it.kind === "option" && it.value === select.value);
        if (kbIdx < 0) kbIdx = visible.findIndex(it => it.kind === "option");
        _paintKbFocus();

        // Bind once per render — listEl.innerHTML wipes prior children.
        listEl.querySelectorAll(".ssel-option:not(.disabled)").forEach(el => {
            el.addEventListener("click", () => {
                const idx = parseInt(el.dataset.idx, 10);
                _commit(visible, idx);
            });
            el.addEventListener("mouseenter", () => {
                kbIdx = parseInt(el.dataset.idx, 10);
                _paintKbFocus();
            });
        });
        // A folder header toggles its folder. Not while filtering: the
        // query has every folder open, and a click there would change a
        // stored state the owner cannot see. mousedown keeps the focus in
        // the search box, where the keyboard navigation lives.
        listEl.querySelectorAll(".ssel-folder:not(.searching)").forEach(el => {
            el.addEventListener("mousedown", (e) => e.preventDefault());
            el.addEventListener("click", () => {
                const it = visible[parseInt(el.dataset.idx, 10)];
                if (it) _toggleFolder(it.label, it.folded);
            });
        });

        // Keyboard nav lives on the search input so typing + arrows
        // chain naturally. The list itself doesn't take focus.
        searchEl.onkeydown = (e) => {
            if (e.key === "ArrowDown") {
                e.preventDefault();
                kbIdx = _nextOptIdx(visible, kbIdx, +1);
                _paintKbFocus(); _scrollKbIntoView();
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                kbIdx = _nextOptIdx(visible, kbIdx, -1);
                _paintKbFocus(); _scrollKbIntoView();
            } else if (e.key === "Enter") {
                e.preventDefault();
                if (kbIdx >= 0) _commit(visible, kbIdx);
            } else if (e.key === "Escape") {
                e.preventDefault();
                _close();
                trigger.focus();
            }
        };
    }

    function _toggleFolder(label, wasFolded) {
        if (wasFolded) folded.delete(label);
        else folded.add(label);
        // Folding the selection's own folder is the owner's choice and
        // wins over the open-on-open rule for the rest of this opening.
        if (label === forcedOpen) forcedOpen = null;
        _saveFolded(select.id, folded);
        _renderList();
    }

    function _paintKbFocus() {
        if (!listEl) return;
        listEl.querySelectorAll(".kb-focus").forEach(el => el.classList.remove("kb-focus"));
        if (kbIdx < 0) return;
        const el = listEl.querySelector('.ssel-option[data-idx="' + kbIdx + '"]');
        if (el) el.classList.add("kb-focus");
    }

    function _scrollKbIntoView() {
        const el = listEl?.querySelector(".kb-focus");
        if (el) el.scrollIntoView({ block: "nearest" });
    }

    function _commit(visible, idx) {
        const it = visible[idx];
        if (!it || it.kind !== "option" || it.disabled) return;
        if (select.value !== it.value) {
            select.value = it.value;
            select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        // Outside the `if`: picking the option that is already the value
        // must still correct a label that has fallen behind it.
        triggerLabel.textContent = _currentLabel(select, placeholder);
        _close();
        trigger.focus();
    }

    function _open() {
        if (panel) { _close(); return; }
        panel = document.createElement("div");
        panel.className = "ssel-panel";
        panel.innerHTML = '<div class="ssel-search-row">'
            + '<input class="ssel-search" type="text" placeholder="' + _esc(searchPlaceholder) + '" autocomplete="off" spellcheck="false">'
            + '</div>'
            + '<div class="ssel-list" role="listbox"></div>';
        document.body.appendChild(panel);
        listEl = panel.querySelector(".ssel-list");
        searchEl = panel.querySelector(".ssel-search");

        // Lock the panel to the trigger's width and let the option list
        // ellipsize anything longer instead of growing the whole panel
        // when long names appear (model titles with [hash] suffixes
        // were ballooning the dropdown past the side panel boundary
        // when the canvas wasn't full size). A floor prevents tiny
        // triggers (icon-only buttons) from creating an unusable
        // ~30px-wide list. A separate cap on the list height uses the
        // viewport space below the trigger so users on shorter windows
        // can still see more of the list.
        const r = trigger.getBoundingClientRect();
        const lockedWidth = Math.max(r.width, 200);
        panel.style.width = lockedWidth + "px";
        panel.style.minWidth = lockedWidth + "px";
        panel.style.maxWidth = lockedWidth + "px";
        const maxLeft = window.innerWidth - 8 - lockedWidth;
        panel.style.left = Math.max(8, Math.min(r.left, maxLeft)) + "px";
        panel.style.top = (r.bottom + 4) + "px";

        // Reserve room for the search row + a little padding, then
        // give the rest of the available viewport height to the
        // option list so a small Studio window doesn't truncate the
        // list to the CSS-default 320px.
        const searchRowHeight = 38;
        const viewportPadding = 12;
        const available = Math.max(160, window.innerHeight - r.bottom - viewportPadding - searchRowHeight);
        listEl.style.maxHeight = available + "px";

        trigger.classList.add("ssel-trigger-open");
        forcedOpen = _folderOf(_readOptions(select), select.value);
        _renderList();
        searchEl.addEventListener("input", _renderList);
        searchEl.focus();

        document.addEventListener("click", _outsideClick, true);
        document.addEventListener("keydown", _hostKey, true);

        // See the matching dispatch in _close() — keeps existing
        // focus/blur listeners on the native select working.
        select.dispatchEvent(new Event("focus", { bubbles: false }));
    }

    trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        _open();
    });

    // Refresh the trigger label whenever the native select's value
    // is set programmatically (which doesn't fire "change"). Also
    // refresh on options-list changes.
    const observer = new MutationObserver((mutations) => {
        let optionsChanged = false;
        for (const m of mutations) {
            if (m.type === "childList") optionsChanged = true;
            if (m.type === "attributes" && m.attributeName === "value") optionsChanged = true;
        }
        triggerLabel.textContent = _currentLabel(select, placeholder);
        if (optionsChanged && panel) _renderList();
    });
    observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["value"] });

    // `select.value = x` and `select.selectedIndex = n` set a PROPERTY, which
    // no MutationObserver sees, so a value set by code (a model-family switch
    // restoring Text Encoder, VAE, Sampler, Scheduler) left the old label on
    // the trigger while the list showed the real value. Wrap both setters on
    // this element so every programmatic set repaints the label.
    const proto = HTMLSelectElement.prototype;
    for (const key of ["value", "selectedIndex"]) {
        const native = Object.getOwnPropertyDescriptor(proto, key);
        Object.defineProperty(select, key, {
            configurable: true,
            get() { return native.get.call(this); },
            set(v) {
                native.set.call(this, v);
                triggerLabel.textContent = _currentLabel(select, placeholder);
            },
        });
    }

    // Native "change" can also be triggered by other code paths (e.g.
    // keyboard) — keep the trigger label in sync.
    select.addEventListener("change", () => {
        triggerLabel.textContent = _currentLabel(select, placeholder);
    });

    const handle = {
        refresh() {
            triggerLabel.textContent = _currentLabel(select, placeholder);
            if (panel) _renderList();
        },
        destroy() {
            _close();
            observer.disconnect();
            delete select.value;
            delete select.selectedIndex;
            trigger.remove();
            select.classList.remove("ssel-native-hidden");
            delete select[ATTR_KEY];
        },
    };
    select[ATTR_KEY] = handle;
    return handle;
}

function attachAll(root, opts) {
    const scope = root || document;
    const out = [];
    scope.querySelectorAll("select.ssel").forEach(sel => {
        out.push(attach(sel, opts));
    });
    return out;
}

// The underscored ones are exported for the Node tests, not for callers.
window.StudioSearchableSelect = {
    attach, attachAll, groupByFolder,
    _visibleItems, _folderOf, _nextOptIdx, _loadFolded, _saveFolded,
};

})();
