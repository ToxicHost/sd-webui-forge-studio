/**
 * Forge Studio — Wildcards Module (Frontend)
 * by ToxicHost & Moritz
 *
 * Wildcard file manager for Dynamic Prompts.
 * Phase 1: file tree, open/edit/save, create, delete, search, context menu
 * Phase 2: syntax highlighting (comments + nested refs), folders, rename, duplicate
 * Phase 3: live preview with wildcard resolution, auto-save toggle
 * Phase 4: drag-to-move files between folders, zip export/import
 *
 * Registers via StudioModules.register("lexicon", {...})
 */
(function () {
"use strict";

// i18n helper — runtime-built strings pass their English source
// through _t(). Markup also gets data-i18n* attrs so applyToDom()
// keeps things in sync on locale switch.
function _t(key, fallback, params) {
  if (window.I18N && window.I18N.t) return window.I18N.t(key, fallback, params);
  return params ? fallback.replace(/\{(\w+)\}/g, function (m, k) { return k in params ? params[k] : m; }) : fallback;
}

const TAG = "[Wildcards]";
const API = "/studio/lexicon";
const VERSION = "1.0.0";

const RE_WILDCARD = /__([a-zA-Z0-9_/\-]+)__/g;

// ========================================================================
// STATE
// ========================================================================

const LX = {
    tree: null,
    openFile: null,
    dirtyFiles: new Map(),   // path → unsaved content
    fileCount: 0,
    searchQuery: "",
    searchMode: "name",  // "name" or "content"
    searchTimer: null,
    contentResults: null, // array of content search results
    autoSave: false,
    autoSaveTimer: null,
    previewOpen: false,
    dragPath: null,
    dragType: null,
};

const AUTOSAVE_DELAY = 1500;
const _els = {};

// ========================================================================
// API HELPERS
// ========================================================================

async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
        const body = await r.json().catch(() => ({ error: r.statusText }));
        throw new Error(body.error || r.statusText);
    }
    return r.json();
}

async function postJSON(url, data) {
    return fetchJSON(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
}

// ========================================================================
// TREE
// ========================================================================

async function loadTree() {
    try {
        LX.tree = await fetchJSON(API + "/tree");
        _renderTree();
    } catch (e) {
        console.error(TAG, "Failed to load tree:", e);
        _showError("Cannot load wildcard files: " + e.message);
    }
}

// `loadInfo` is gone, not stubbed. It fetched /info for `file_count` and
// `_countFilesInTree` overwrote that value from the tree on the very next
// render, so the route served a number nothing read -- and calling a route
// Studio does not implement would 404 on init, on every activate and on every
// Refresh. The footer is fed by the tree, which is the only source that can be
// right.

// ── Tree Keyboard Navigation ─────────────────────────────────
function _getVisibleTreeItems() {
    if (!_els.treeList) return [];
    return Array.from(_els.treeList.querySelectorAll(".lex-tree-item")).filter(el => el.offsetHeight > 0);
}

function _handleTreeKey(e) {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown" && e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Enter") return;
    e.preventDefault();
    e.stopPropagation();

    const items = _getVisibleTreeItems();
    if (!items.length) return;

    const cur = items.findIndex(el => el.classList.contains("kb-focus"));

    if (e.key === "ArrowDown") {
        const next = cur < items.length - 1 ? cur + 1 : 0;
        items.forEach(el => el.classList.remove("kb-focus"));
        items[next].classList.add("kb-focus");
        items[next].scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
    }

    if (e.key === "ArrowUp") {
        const next = cur > 0 ? cur - 1 : items.length - 1;
        items.forEach(el => el.classList.remove("kb-focus"));
        items[next].classList.add("kb-focus");
        items[next].scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
    }

    if (e.key === "ArrowRight" && cur >= 0) {
        const item = items[cur];
        if (item.dataset.type === "folder") {
            const wrapper = item.closest(".lex-tree-folder");
            if (wrapper && !wrapper.classList.contains("open")) {
                wrapper.classList.add("open");
                return;
            }
            // Already open — move to first child
            const nextItems = _getVisibleTreeItems();
            const newCur = nextItems.indexOf(item);
            if (newCur >= 0 && newCur < nextItems.length - 1) {
                items.forEach(el => el.classList.remove("kb-focus"));
                nextItems[newCur + 1].classList.add("kb-focus");
                nextItems[newCur + 1].scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        }
        return;
    }

    if (e.key === "ArrowLeft" && cur >= 0) {
        const item = items[cur];
        if (item.dataset.type === "folder") {
            const wrapper = item.closest(".lex-tree-folder");
            if (wrapper && wrapper.classList.contains("open")) {
                wrapper.classList.remove("open");
                return;
            }
        }
        // Move to parent folder
        const parentFolder = item.closest(".lex-tree-children")?.closest(".lex-tree-folder");
        if (parentFolder) {
            const parentItem = parentFolder.querySelector(":scope > .lex-tree-item");
            if (parentItem) {
                items.forEach(el => el.classList.remove("kb-focus"));
                parentItem.classList.add("kb-focus");
                parentItem.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        }
        return;
    }

    if (e.key === "Enter" && cur >= 0) {
        const item = items[cur];
        if (item.dataset.type === "folder") {
            const wrapper = item.closest(".lex-tree-folder");
            if (wrapper) wrapper.classList.toggle("open");
        } else {
            item.click();  // opens the file
        }
        return;
    }
}

function _renderTree() {
    const list = _els.treeList;
    if (!list || !LX.tree) return;
    list.innerHTML = "";

    const q = LX.searchQuery.toLowerCase();
    _countFilesInTree(LX.tree);

    if (q) {
        const matches = [];
        _collectMatches(LX.tree, q, matches);
        if (matches.length === 0) {
            list.innerHTML = '<div class="lex-tree-item" style="color:var(--text-4);pointer-events:none;" data-i18n="lexicon.empty.noMatches">' + _t("lexicon.empty.noMatches", "No matches") + '</div>';
            return;
        }
        for (const node of matches) list.appendChild(_createFileItem(node, 0));
    } else {
        _renderNode(LX.tree, list, 0, true);
    }

    _updateTreeFooter();
}

function _countFilesInTree(node) {
    if (!node) return 0;
    if (node.type === "file") return 1;
    let n = 0;
    if (node.children) for (const c of node.children) n += _countFilesInTree(c);
    LX.fileCount = n;
    return n;
}

function _collectMatches(node, q, out) {
    if (node.type === "file" && node.name.toLowerCase().includes(q)) out.push(node);
    if (node.children) for (const c of node.children) _collectMatches(c, q, out);
}

function _renderNode(node, parent, depth, isRoot) {
    if (isRoot && node.type === "folder") {
        if (node.children) for (const c of node.children) _renderNode(c, parent, depth, false);
        return;
    }

    if (node.type === "folder") {
        const wrapper = document.createElement("div");
        wrapper.className = "lex-tree-folder";
        wrapper.dataset.path = node.path;

        const item = document.createElement("div");
        item.className = "lex-tree-item";
        item.dataset.depth = depth;
        item.dataset.path = node.path;
        item.dataset.type = "folder";
        item.innerHTML = '<span class="lex-chevron">' + IC.chev + '</span><span class="lex-icon">' + IC.folder + '</span><span class="lex-name">' + _esc(node.name) + '</span>';
        item.addEventListener("click", () => wrapper.classList.toggle("open"));
        item.addEventListener("contextmenu", (e) => { e.preventDefault(); _showContextMenu(e, node.path, "folder"); });

        // Drop target: folders accept drops
        _makeDragTarget(item, node.path);

        wrapper.appendChild(item);

        const children = document.createElement("div");
        children.className = "lex-tree-children";
        if (node.children) for (const c of node.children) _renderNode(c, children, depth + 1, false);
        wrapper.appendChild(children);
        parent.appendChild(wrapper);
    } else {
        parent.appendChild(_createFileItem(node, depth));
    }
}

function _createFileItem(node, depth) {
    const item = document.createElement("div");
    item.className = "lex-tree-item";
    if (LX.openFile && LX.openFile.path === node.path) item.classList.add("active");
    item.dataset.path = node.path;
    item.dataset.depth = depth;
    item.dataset.type = "file";
    // FR-013: Highlight search match in filename
    let nameHTML = _esc(node.name);
    if (LX.searchQuery && LX.searchMode === "name") {
        const q = LX.searchQuery.toLowerCase();
        const idx = node.name.toLowerCase().indexOf(q);
        if (idx >= 0) {
            const before = _esc(node.name.slice(0, idx));
            const match = _esc(node.name.slice(idx, idx + q.length));
            const after = _esc(node.name.slice(idx + q.length));
            nameHTML = before + '<mark class="lex-hl-match">' + match + '</mark>' + after;
        }
    }
    item.innerHTML = '<span class="lex-icon">' + IC.file + '</span><span class="lex-name">' + nameHTML + '</span><span class="lex-count">' + (node.lines || 0) + '</span>';
    item.addEventListener("click", () => openFile(node.path));
    item.addEventListener("contextmenu", (e) => { e.preventDefault(); _showContextMenu(e, node.path, "file"); });

    // Draggable
    item.draggable = true;
    item.addEventListener("dragstart", (e) => {
        LX.dragPath = node.path;
        LX.dragType = "file";
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", node.path);
        item.classList.add("lex-dragging");
    });
    item.addEventListener("dragend", () => {
        item.classList.remove("lex-dragging");
        LX.dragPath = null;
        LX.dragType = null;
        // Clear all drop highlights
        _els.treeList.querySelectorAll(".lex-drop-target").forEach(el => el.classList.remove("lex-drop-target"));
        _els.treeList.classList.remove("lex-drop-target");
    });

    return item;
}

// ========================================================================
// DRAG & DROP
// ========================================================================

function _makeDragTarget(el, folderPath) {
    el.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = (e.dataTransfer.types.includes("Files")) ? "copy" : "move";
        el.classList.add("lex-drop-target");
    });
    el.addEventListener("dragleave", () => {
        el.classList.remove("lex-drop-target");
    });
    el.addEventListener("drop", (e) => {
        e.preventDefault();
        el.classList.remove("lex-drop-target");
        // OS file/folder drop?
        if (e.dataTransfer.items && e.dataTransfer.types.includes("Files")) {
            _handleOSDrop(e.dataTransfer.items, folderPath);
            return;
        }
        // Internal tree move
        const srcPath = LX.dragPath;
        if (!srcPath) return;
        const srcParent = srcPath.split("/").slice(0, -1).join("/");
        if (srcParent === folderPath) return;
        if (srcPath === folderPath) return;
        _toast(_t("lexicon.toast.moveUnavailable", "Moving files is not available yet."));
    });
}

function _setupRootDrop() {
    const list = _els.treeList;
    list.addEventListener("dragover", (e) => {
        if (e.target === list || e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            e.dataTransfer.dropEffect = (e.dataTransfer.types.includes("Files")) ? "copy" : "move";
            list.classList.add("lex-drop-target");
        }
    });
    list.addEventListener("dragleave", (e) => {
        if (e.target === list) list.classList.remove("lex-drop-target");
    });
    list.addEventListener("drop", (e) => {
        list.classList.remove("lex-drop-target");
        if (e.dataTransfer.items && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            _handleOSDrop(e.dataTransfer.items, "");
            return;
        }
        if (e.target !== list && !e.target.classList.contains("lex-tree-list")) return;
        e.preventDefault();
        const srcPath = LX.dragPath;
        if (!srcPath) return;
        const srcParent = srcPath.split("/").slice(0, -1).join("/");
        if (srcParent === "") return;
        _toast(_t("lexicon.toast.moveUnavailable", "Moving files is not available yet."));
    });
}

// ── OS file/folder drop handling ──────────────────────────────────

function _handleOSDrop(items, destFolder) {
    // Collect entries before the DataTransferItemList goes stale
    const entries = [];
    for (let i = 0; i < items.length; i++) {
        const entry = items[i].webkitGetAsEntry ? items[i].webkitGetAsEntry()
                    : items[i].getAsEntry ? items[i].getAsEntry()
                    : null;
        if (entry) entries.push(entry);
    }

    if (entries.length === 0) return;
    _toast("Reading files\u2026");

    // Walk all entries (files + directories recursively), collect .txt files
    _collectEntries(entries, "").then(files => {
        if (files.length === 0) {
            _toast("No .txt files found in drop", "error");
            return;
        }
        _uploadCollected(files, destFolder);
    });
}

// Recursively collect { relativePath, file } from FileSystemEntry trees
async function _collectEntries(entries, prefix) {
    const results = [];
    for (const entry of entries) {
        if (entry.isFile) {
            if (entry.name.endsWith(".txt")) {
                const file = await _entryToFile(entry);
                if (file) results.push({ relativePath: prefix ? prefix + "/" + entry.name : entry.name, file });
            }
        } else if (entry.isDirectory) {
            const dirPrefix = prefix ? prefix + "/" + entry.name : entry.name;
            const children = await _readDirectory(entry);
            const sub = await _collectEntries(children, dirPrefix);
            results.push(...sub);
        }
    }
    return results;
}

function _entryToFile(entry) {
    return new Promise(resolve => {
        entry.file(f => resolve(f), () => resolve(null));
    });
}

function _readDirectory(dirEntry) {
    return new Promise(resolve => {
        const reader = dirEntry.createReader();
        const all = [];
        // readEntries may return partial results — must call repeatedly until empty
        function readBatch() {
            reader.readEntries(entries => {
                if (entries.length === 0) {
                    resolve(all);
                } else {
                    all.push(...entries);
                    readBatch();
                }
            }, () => resolve(all));
        }
        readBatch();
    });
}

async function _uploadCollected(files, destFolder) {
    let uploaded = 0, skipped = 0;
    for (const { relativePath, file } of files) {
        try {
            const content = await file.text();
            // Build the full relative path under destFolder
            const fullRel = destFolder ? destFolder + "/" + relativePath : relativePath;
            const parentDir = fullRel.split("/").slice(0, -1).join("/");
            const fileName = fullRel.split("/").pop();

            // Ensure parent folders exist (create via folder/create, ignore if exists)
            if (parentDir) {
                const parts = parentDir.split("/");
                for (let i = 1; i <= parts.length; i++) {
                    const folderPath = parts.slice(0, i).join("/");
                    try {
                        await postJSON(API + "/folder/create", { path: parts.slice(0, i - 1).join("/"), name: parts[i - 1] });
                    } catch (e) {
                        // Folder may already exist — that's fine
                    }
                }
            }

            // Create file (ignore if exists) then save content
            try {
                await postJSON(API + "/file/create", { path: parentDir, name: fileName });
            } catch (e) {
                if (!e.message.includes("already exists")) throw e;
            }
            await postJSON(API + "/file/save", { path: fullRel, content });
            uploaded++;
        } catch (e) {
            console.error(TAG, "Upload failed for", relativePath, e);
            skipped++;
        }
    }
    if (uploaded > 0) {
        _toast("Uploaded " + uploaded + " file" + (uploaded > 1 ? "s" : "") + (skipped ? " (" + skipped + " skipped)" : ""));
        loadTree();
    } else if (skipped > 0) {
        _toast("Upload failed for all files", "error");
    }
}

// Allow OS file drops anywhere on the tree panel (header, footer, etc.)
function _setupPanelDrop(panel) {
    panel.addEventListener("dragover", (e) => {
        if (e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        }
    });
    panel.addEventListener("drop", (e) => {
        if (e.dataTransfer.items && e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            _handleOSDrop(e.dataTransfer.items, "");
        }
    });
}

async function _moveItem(srcPath, destFolder) {
    const name = srcPath.split("/").pop();
    try {
        const res = await postJSON(API + "/file/move", { path: srcPath, dest: destFolder });
        _toast("Moved " + name);
        // Update open file path if it was the moved file
        if (LX.openFile && LX.openFile.path === srcPath) {
            LX.openFile.path = res.new_path;
            _els.fileName.textContent = res.new_path;
            if (LX.openFile) _updateUsage();
        }
        await loadTree();
        _highlightActiveFile();
    } catch (e) {
        _toast("Move failed: " + e.message, "error");
    }
}

// Also make folder items in the tree draggable
function _makeFolderDraggable(item, folderPath) {
    item.draggable = true;
    item.addEventListener("dragstart", (e) => {
        LX.dragPath = folderPath;
        LX.dragType = "folder";
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", folderPath);
        item.classList.add("lex-dragging");
        e.stopPropagation(); // prevent parent folder from also firing
    });
    item.addEventListener("dragend", () => {
        item.classList.remove("lex-dragging");
        LX.dragPath = null;
        LX.dragType = null;
        _els.treeList.querySelectorAll(".lex-drop-target").forEach(el => el.classList.remove("lex-drop-target"));
        _els.treeList.classList.remove("lex-drop-target");
    });
}

function _highlightActiveFile() {
    if (!_els.treeList) return;
    _els.treeList.querySelectorAll(".lex-tree-item").forEach(el => {
        el.classList.toggle("active", el.dataset.path === (LX.openFile && LX.openFile.path));
    });
}

// ========================================================================
// FILE OPERATIONS
// ========================================================================

async function openFile(path) {
    // Silently buffer any unsaved edits before switching files
    if (LX.openFile && _els.textarea) {
        const cur = _els.textarea.value;
        if (cur !== LX.openFile.content) LX.dirtyFiles.set(LX.openFile.path, cur);
    }
    if (LX.openFile && LX.openFile.path === path) return;
    try {
        const data = await fetchJSON(API + "/file?path=" + encodeURIComponent(path));
        LX.openFile = data;
        _showEditor();
        // Restore any buffered edits for this file
        if (LX.dirtyFiles.has(path) && _els.textarea) {
            _els.textarea.value = LX.dirtyFiles.get(path);
            _updateGutter(); _updateHighlight(); _updateEditorState();
        }
        _highlightActiveFile();
        _updateDirtyIndicators();
        if (_els.previewResult) _els.previewResult.textContent = "";
    } catch (e) {
        console.error(TAG, "Failed to open:", path, e);
        _toast("Failed to open file: " + e.message, "error");
    }
}

async function saveFile() {
    if (!LX.openFile) return;
    const path = LX.openFile.path;
    if (!LX.dirtyFiles.has(path)) return;
    const content = LX.dirtyFiles.get(path);
    try {
        const res = await postJSON(API + "/file/save", { path, content });
        LX.openFile.content = content;
        LX.openFile.lines = res.lines;
        LX.openFile.size = res.size;
        LX.dirtyFiles.delete(path);
        _updateEditorState();
        _updateDirtyIndicators();
        _toast(_t("lexicon.toast.saved", "Saved"));
        await loadTree();
    } catch (e) {
        console.error(TAG, "Save failed:", e);
        _toast("Save failed: " + e.message, "error");
    }
}

async function saveAllDirty() {
    if (LX.dirtyFiles.size === 0) return;
    let saved = 0, failed = 0;
    for (const [path, content] of LX.dirtyFiles) {
        try {
            await postJSON(API + "/file/save", { path, content });
            LX.dirtyFiles.delete(path);
            if (LX.openFile && LX.openFile.path === path) LX.openFile.content = content;
            saved++;
        } catch (e) {
            console.error(TAG, "Save failed for", path, e);
            failed++;
        }
    }
    _updateEditorState();
    _updateDirtyIndicators();
    const label = saved === 1 ? "Saved 1 file" : "Saved " + saved + " files";
    if (saved > 0) _toast(label);
    if (failed > 0) _toast("Save failed for " + failed + " file" + (failed > 1 ? "s" : ""), "error");
    if (saved > 0) await loadTree();
}

async function createFile(parentPath) {
    if (parentPath === undefined) {
        parentPath = "";
        if (LX.openFile) {
            const parts = LX.openFile.path.split("/");
            if (parts.length > 1) parentPath = parts.slice(0, -1).join("/");
        }
    }
    const name = prompt("New wildcard file name:", "untitled.txt");
    if (!name) return;
    try {
        const res = await postJSON(API + "/file/create", { path: parentPath, name });
        _toast("Created " + name);
        await loadTree();
        openFile(res.path);
    } catch (e) {
        _toast("Create failed: " + e.message, "error");
    }
}

async function createFolder(parentPath) {
    if (parentPath === undefined) parentPath = "";
    const name = prompt("New folder name:");
    if (!name) return;
    try {
        await postJSON(API + "/folder/create", { path: parentPath, name });
        _toast("Created folder: " + name);
        await loadTree();
    } catch (e) {
        _toast("Folder create failed: " + e.message, "error");
    }
}

async function deleteItem(path) {
    if (!path) return;
    const name = path.split("/").pop();
    if (!(await studioConfirm(_t("lexicon.deleteConfirm", "Delete \u201c{name}\u201d?", { name: name }),
        { confirm: _t("dialog.delete", "Delete"), danger: true }))) return;
    try {
        const resp = await fetch(API + "/file?path=" + encodeURIComponent(path), { method: "DELETE" });
        const data = await resp.json();
        if (!resp.ok) {
            // Non-empty folder — ask for confirmation with file count
            if (data.not_empty) {
                const count = data.file_count || "some";
                if (!(await studioConfirm(count === 1
                    ? _t("lexicon.deleteFolderConfirm.one", "Delete the folder \u201c{name}\u201d and everything in it? It holds 1 wildcard file.", { name: name })
                    : _t("lexicon.deleteFolderConfirm.other", "Delete the folder \u201c{name}\u201d and everything in it? It holds {count} wildcard files.", { name: name, count: count }),
                    { confirm: _t("dialog.delete", "Delete"), danger: true }))) return;
                // Retry with force=true
                const forceResp = await fetch(API + "/file?path=" + encodeURIComponent(path) + "&force=true", { method: "DELETE" });
                if (!forceResp.ok) {
                    const err = await forceResp.json();
                    throw new Error(err.error || "Delete failed");
                }
            } else {
                throw new Error(data.error || "Delete failed");
            }
        }
        _toast("Deleted " + name);
        // Purge any buffered edits for deleted paths
        for (const p of LX.dirtyFiles.keys()) {
            if (p === path || p.startsWith(path + "/")) LX.dirtyFiles.delete(p);
        }
        if (LX.openFile && (LX.openFile.path === path || LX.openFile.path.startsWith(path + "/"))) {
            LX.openFile = null;
            _showEmpty();
        }
        loadTree();
    } catch (e) {
        _toast("Delete failed: " + e.message, "error");
    }
}

async function renameItem(path, type) {
    if (!path) return;
    const oldName = path.split("/").pop();
    const newName = prompt(type === "folder" ? "Rename folder:" : "Rename file:", oldName);
    if (!newName || newName === oldName) return;
    try {
        const res = await postJSON(API + "/file/rename", { path, new_name: newName });
        _toast("Renamed to " + newName);
        if (LX.openFile && LX.openFile.path === path) LX.openFile.path = res.new_path;
        await loadTree();
        _highlightActiveFile();
        if (LX.openFile) { _els.fileName.textContent = LX.openFile.path; _updateUsage(); _updateFooter(); }
    } catch (e) {
        _toast("Rename failed: " + e.message, "error");
    }
}

async function duplicateFile(path) {
    if (!path) return;
    try {
        const res = await postJSON(API + "/file/duplicate", { path });
        _toast("Duplicated as " + res.path.split("/").pop());
        await loadTree();
        openFile(res.path);
    } catch (e) {
        _toast("Duplicate failed: " + e.message, "error");
    }
}

// ========================================================================
// SORT & DEDUPLICATE
// ========================================================================

function sortEntries() {
    if (!LX.openFile || !_els.textarea) return;
    const btn = _els.sortBtn;
    const mode = btn?.dataset.sort || "az";
    const text = _els.textarea.value;
    const lines = text.split("\n");
    // Separate comments at the top from entries
    const header = [];
    const entries = [];
    let pastHeader = false;
    for (const line of lines) {
        if (!pastHeader && (line.trimStart().startsWith("#") || line.trim() === "")) {
            header.push(line);
        } else {
            pastHeader = true;
            entries.push(line);
        }
    }
    // Remove trailing empty lines from entries before sort
    while (entries.length && entries[entries.length - 1].trim() === "") entries.pop();

    let label, nextMode;
    if (mode === "az") {
        entries.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
        label = _t("lexicon.sort.za", "Sort Z\u2013A"); nextMode = "za";
        _toast("Sorted A\u2192Z (" + entries.length + " entries)");
    } else if (mode === "za") {
        entries.sort((a, b) => b.localeCompare(a, undefined, { sensitivity: "base" }));
        label = _t("lexicon.sort.shuffle", "Shuffle"); nextMode = "shuffle";
        _toast("Sorted Z\u2192A (" + entries.length + " entries)");
    } else {
        // Fisher-Yates shuffle
        for (let i = entries.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [entries[i], entries[j]] = [entries[j], entries[i]];
        }
        label = _t("lexicon.sort.az", "Sort A\u2013Z"); nextMode = "az";
        _toast("Shuffled " + entries.length + " entries");
    }
    if (btn) { (btn.querySelector(".lex-btn-label") || btn).textContent = label; btn.dataset.sort = nextMode; }
    // Use execCommand to preserve native undo stack
    _els.textarea.focus();
    _els.textarea.select();
    document.execCommand("insertText", false, header.concat(entries).join("\n"));
}

function deduplicateEntries() {
    if (!LX.openFile || !_els.textarea) return;
    const text = _els.textarea.value;
    const lines = text.split("\n");
    const seen = new Set();
    const result = [];
    let removed = 0;
    for (const line of lines) {
        const trimmed = line.trim();
        // Keep comments, blanks, and first occurrence of each entry
        if (trimmed === "" || trimmed.startsWith("#")) {
            result.push(line);
        } else {
            const key = trimmed.toLowerCase();
            if (seen.has(key)) {
                removed++;
            } else {
                seen.add(key);
                result.push(line);
            }
        }
    }
    if (removed === 0) {
        _toast("No duplicates found");
        return;
    }
    _els.textarea.focus();
    _els.textarea.select();
    document.execCommand("insertText", false, result.join("\n"));
    _toast("Removed " + removed + " duplicate" + (removed > 1 ? "s" : "") + " (Ctrl+Z to undo)");
}

// ========================================================================
// CONTENT SEARCH
// ========================================================================

async function _doContentSearch(query) {
    if (!query || query.length < 2) {
        LX.contentResults = null;
        _renderTree();
        return;
    }
    try {
        const results = await fetchJSON(API + "/search_content?q=" + encodeURIComponent(query));
        LX.contentResults = results;
        _renderContentResults();
    } catch (e) {
        console.error(TAG, "Content search failed:", e);
    }
}

function _renderContentResults() {
    const list = _els.treeList;
    if (!list || !LX.contentResults) return;
    list.innerHTML = "";

    if (LX.contentResults.length === 0) {
        list.innerHTML = '<div class="lex-tree-item" style="color:var(--text-4);pointer-events:none;" data-i18n="lexicon.empty.noMatches">' + _t("lexicon.empty.noMatches", "No matches") + '</div>';
        return;
    }

    for (const r of LX.contentResults) {
        const item = document.createElement("div");
        item.className = "lex-tree-item lex-content-result";
        if (LX.openFile && LX.openFile.path === r.path) item.classList.add("active");
        item.dataset.path = r.path;
        item.innerHTML =
            '<span class="lex-icon">' + IC.file + '</span>'
          + '<span class="lex-content-hit">'
          +   '<span class="lex-name">' + _esc(r.name) + '</span>'
          +   '<span class="lex-content-match">' + _esc(r.text) + '</span>'
          + '</span>'
          + '<span class="lex-count">L' + r.line + '</span>';
        item.addEventListener("click", () => openFile(r.path));
        list.appendChild(item);
    }
}

// `_toggleSearchMode` and its "Aa" button are gone. Content search called
// /search_content, which the standalone does not serve, and its only
// failure path was a console.error -- so the owner typed, nothing
// changed, and the tree kept showing unfiltered results. Silent is the
// worst of the failures in this file; the other dead controls at least
// toasted. Filename filtering is untouched and is what the box does now.

function _onSearchInput() {
    const q = _els.treeSearch.value.trim();
    LX.searchQuery = q;

    if (LX.searchMode === "content") {
        clearTimeout(LX.searchTimer);
        if (!q || q.length < 2) {
            LX.contentResults = null;
            _renderTree();
            return;
        }
        // Debounce content search (hits the backend)
        LX.searchTimer = setTimeout(() => _doContentSearch(q), 300);
    } else {
        LX.contentResults = null;
        _renderTree();
    }
}

// ========================================================================
// IMPORT / EXPORT
// ========================================================================

// REMOVED, not disabled. D8: an ABSENT service is hidden, and there is
// no reason to show an owner that would not just be "Studio does not do
// this". These called routes the standalone has no backend for, and the
// tab gate was the only thing keeping them off the screen -- so serving
// /tree re-arms every one of them on the same commit. That is the
// incident this file already documents: Export clicked a synthetic
// <a download="wildcards_export.zip"> at a route that does not exist and
// saved a 404 JSON body to disk under a .zip name.
function exportZip() {
    _toast("Exporting wildcards\u2026");
    // Trigger download via a hidden link
    const a = document.createElement("a");
    a.href = API + "/export";
    a.download = "wildcards_export.zip";
    a.click();
}

async function importZip() {
    // Open file picker
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".zip";
    input.addEventListener("change", async () => {
        const file = input.files[0];
        if (!file) return;

        _toast("Importing " + file.name + "\u2026");
        try {
            const buf = await file.arrayBuffer();
            const r = await fetch(API + "/import", {
                method: "POST",
                headers: { "Content-Type": "application/zip" },
                body: buf,
            });
            if (!r.ok) {
                const body = await r.json().catch(() => ({ error: r.statusText }));
                throw new Error(body.error || r.statusText);
            }
            const res = await r.json();
            _toast("Imported " + res.imported + " files" + (res.skipped ? " (" + res.skipped + " skipped)" : ""));
            loadTree();
        } catch (e) {
            _toast("Import failed: " + e.message, "error");
        }
    });
    input.click();
}

// ========================================================================
// AUTO-SAVE
// ========================================================================

function _scheduleAutoSave() {
    if (!LX.autoSave || LX.dirtyFiles.size === 0) return;
    clearTimeout(LX.autoSaveTimer);
    LX.autoSaveTimer = setTimeout(() => {
        if (LX.autoSave && LX.dirtyFiles.size > 0) {
            console.log(TAG, "Auto-saving", LX.dirtyFiles.size, "file(s)");
            saveAllDirty();
        }
    }, AUTOSAVE_DELAY);
}

function _toggleAutoSave() {
    LX.autoSave = !LX.autoSave;
    _els.autoSaveToggle.classList.toggle("active", LX.autoSave);
    _els.autoSaveToggle.title = LX.autoSave ? "Autosave ON (click to disable)" : "Autosave OFF (click to enable)";
    if (LX.autoSave && LX.dirtyFiles.size > 0) _scheduleAutoSave();
}

// ========================================================================
// PREVIEW
// ========================================================================

function _togglePreview() {
    LX.previewOpen = !LX.previewOpen;
    _els.previewPanel.classList.toggle("open", LX.previewOpen);
    _els.previewToggle.classList.toggle("active", LX.previewOpen);
    if (LX.previewOpen) _rollPreview();
}

async function _rollPreview() {
    if (!LX.openFile || !_els.textarea) return;
    const lines = _els.textarea.value.split("\n").filter(l => l.trim() && !l.trim().startsWith("#"));
    if (lines.length === 0) { _els.previewResult.textContent = "(no entries)"; return; }

    const raw = lines[Math.floor(Math.random() * lines.length)];
    _els.previewRaw.textContent = raw;
    _els.previewResult.textContent = "resolving\u2026";
    _els.previewResult.classList.remove("lex-preview-error");

    if (raw.includes("__") || raw.includes("{")) {
        try {
            const res = await fetchJSON(API + "/resolve?text=" + encodeURIComponent(raw));
            _els.previewResult.textContent = res.result;
        } catch (e) {
            _els.previewResult.textContent = "resolve error: " + e.message;
            _els.previewResult.classList.add("lex-preview-error");
        }
    } else {
        _els.previewResult.textContent = raw;
    }
}

// ========================================================================
// CONTEXT MENU
// ========================================================================

function _showContextMenu(e, path, type) {
    e.stopPropagation();
    const menu = _els.contextMenu;
    if (!menu) return;
    menu.innerHTML = "";

    const items = [];
    if (type === "folder") {
        items.push({ label: _t("lexicon.menu.newFileHere", "New File Here"), action: () => createFile(path) });
        items.push({ label: _t("lexicon.menu.newFolderHere", "New Folder Here"), action: () => createFolder(path) });
        items.push(null);
        items.push({ label: _t("lexicon.menu.rename", "Rename"), action: () => renameItem(path, "folder") });
        items.push({ label: _t("lexicon.menu.deleteFolder", "Delete Folder"), action: () => deleteItem(path), cls: "danger" });
    } else {
        const parentPath = path.split("/").slice(0, -1).join("/");
        items.push({ label: _t("lexicon.menu.newFileHere", "New File Here"), action: () => createFile(parentPath) });
        items.push({ label: _t("lexicon.menu.newFolderHere", "New Folder Here"), action: () => createFolder(parentPath) });
        items.push(null);
        items.push({ label: _t("lexicon.menu.rename", "Rename"), action: () => renameItem(path, "file") });
        items.push(null);
        items.push({ label: _t("lexicon.menu.delete", "Delete"), action: () => deleteItem(path), cls: "danger" });
    }

    for (const item of items) {
        if (item === null) {
            const sep = document.createElement("div"); sep.className = "lex-context-sep"; menu.appendChild(sep); continue;
        }
        const div = document.createElement("div");
        div.className = "lex-context-item" + (item.cls ? " " + item.cls : "");
        div.textContent = item.label;
        div.addEventListener("click", () => { _hideContextMenu(); item.action(); });
        menu.appendChild(div);
    }

    menu.style.left = e.clientX + "px";
    menu.style.top = e.clientY + "px";
    menu.classList.add("open");
    setTimeout(() => {
        document.addEventListener("click", _hideContextMenu, { once: true });
        document.addEventListener("contextmenu", _hideContextMenu, { once: true });
    }, 0);
}

function _hideContextMenu() { if (_els.contextMenu) _els.contextMenu.classList.remove("open"); }

// ========================================================================
// SYNTAX HIGHLIGHTING
// ========================================================================

function _highlightContent(text) {
    const lines = text.split("\n");
    const out = [];
    for (const line of lines) {
        if (line.trimStart().startsWith("#")) {
            out.push('<span class="lex-hl-comment">' + _escHTML(line) + '</span>');
        } else {
            out.push(_highlightWildcards(line));
        }
    }
    return out.join("\n") + "\n";
}

function _highlightWildcards(line) {
    let result = "", lastIdx = 0;
    RE_WILDCARD.lastIndex = 0;
    let match;
    while ((match = RE_WILDCARD.exec(line)) !== null) {
        result += _escHTML(line.slice(lastIdx, match.index));
        result += '<span class="lex-hl-ref">' + _escHTML(match[0]) + '</span>';
        lastIdx = RE_WILDCARD.lastIndex;
    }
    return result + _escHTML(line.slice(lastIdx));
}

function _updateHighlight() {
    if (_els.highlight && _els.textarea) _els.highlight.innerHTML = _highlightContent(_els.textarea.value);
}

function _syncScroll() {
    if (!_els.textarea) return;
    const st = _els.textarea.scrollTop, sl = _els.textarea.scrollLeft;
    if (_els.highlight) { _els.highlight.scrollTop = st; _els.highlight.scrollLeft = sl; }
    if (_els.gutter) _els.gutter.scrollTop = st;
}

// ========================================================================
// EDITOR RENDERING
// ========================================================================

function _showEditor() {
    if (!LX.openFile) return;
    _els.empty.style.display = "none";
    _els.editorWrap.style.display = "";
    _els.fileName.textContent = LX.openFile.path;
    _updateUsage();
    _els.textarea.value = LX.openFile.content;
    _updateGutter(); _updateHighlight(); _updateEditorState();
}

function _updateUsage() {
    if (!_els.fileUsage || !LX.openFile) return;
    _els.fileUsage.innerHTML = _esc(_t("lexicon.usage", "Use in a prompt as {token}", { token: "\u0000" }))
        .replace("\u0000", '<code>' + _esc(_promptToken(LX.openFile.path)) + '</code>');
}

function _showEmpty() { _els.empty.style.display = ""; _els.editorWrap.style.display = "none"; }

function _updateEditorState() {
    const dirty = LX.openFile ? LX.dirtyFiles.has(LX.openFile.path) : false;
    _els.dirtyDot.classList.toggle("visible", dirty);
    _els.saveBtn.classList.toggle("enabled", dirty);
    _updateFooter();
}

function _updateGutter() {
    const n = _els.textarea.value.split("\n").length;
    let g = ""; for (let i = 1; i <= n; i++) g += i + "\n";
    _els.gutterInner.textContent = g;
}

function _updateFooter() {
    if (!_els.footerPath) return;
    if (LX.openFile) {
        _els.footerPath.textContent = LX.openFile.path;
        const entries = _els.textarea.value.split("\n").filter(l => l.trim() && !l.trim().startsWith("#")).length;
        _els.footerEntries.textContent = entries === 1 ? _t("lexicon.footer.entries.one", "1 entry") : _t("lexicon.footer.entries.other", "{n} entries", { n: entries });
        const dirty = LX.dirtyFiles.has(LX.openFile.path);
        _els.footerStatus.textContent = dirty ? _t("lexicon.status.unsaved", "Unsaved changes") : _t("lexicon.status.saved", "Saved");
        _els.footerStatus.classList.toggle("dirty", dirty);
    } else {
        _els.footerPath.textContent = ""; _els.footerEntries.textContent = ""; _els.footerStatus.textContent = "";
    }
}

function _updateTreeFooter() {
    if (!_els.treeFooter) return;
    _els.treeFooter.textContent = LX.fileCount === 1 ? _t("lexicon.footer.files.one", "1 wildcard file")
        : _t("lexicon.footer.files.other", "{n} wildcard files", { n: LX.fileCount });
}

// ========================================================================
// ERROR / TOAST
// ========================================================================

function _showError(msg) { if (_els.errorBanner) { _els.errorBanner.textContent = msg; _els.errorBanner.classList.add("visible"); } }

function _toast(msg, type) {
    const fn = window.showToast;
    if (fn) fn(msg, type === "error" ? "error" : "success"); else console.log(TAG, msg);
}

function _updateDirtyIndicators() {
    if (!_els.treeList) return;
    _els.treeList.querySelectorAll(".lex-tree-item[data-type='file']").forEach(el => {
        el.classList.toggle("lex-tree-item-dirty", LX.dirtyFiles.has(el.dataset.path));
    });
}

// ========================================================================
// ICONS (WC1: line icons instead of emoji)
// ========================================================================

function _svg(d, w, sw) {
    return '<svg width="' + (w || 13) + '" height="' + (w || 13) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' + (sw || 2) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
}
const IC = {
    chev: _svg('<polyline points="9 6 15 12 9 18"/>', 10, 2.6),
    folder: _svg('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'),
    file: _svg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'),
    plus: _svg('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>', 12, 2.4),
    refresh: _svg('<polyline points="23 4 23 10 17 10"/><path d="M20.5 15a9 9 0 1 1-2.1-9.4L23 10"/>', 12),
    sort: _svg('<path d="M3 6h11M3 12h8M3 18h5"/><path d="M18 4v16M15 17l3 3 3-3"/>'),
    dedup: _svg('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
    eye: _svg('<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>'),
    trash: _svg('<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>'),
    dice: _svg('<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="15.5" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/>'),
    edit: _svg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>', 32, 1.5),
};

// The token a prompt uses for a file: "styles/painting.txt" -> "__styles/painting__".
function _promptToken(path) { return "__" + String(path || "").replace(/\.txt$/i, "") + "__"; }

// ========================================================================
// BUILD UI
// ========================================================================

function _buildUI(container) {
    container.innerHTML =
        '<div class="lex-layout">'
      +   '<div class="lex-error-banner"></div>'
      +   '<div class="lex-main">'
      +   '<div class="lex-tree-panel">'
      +     '<div class="lex-tree-header">'
      +       '<div class="lex-search-row">'
      +         '<input type="text" class="lex-tree-search" data-i18n-placeholder="lexicon.search.filenames.placeholder" placeholder="' + _t("lexicon.search.filenames.placeholder", "Filter files\u2026") + '">'
      +       '</div>'
      +       '<div class="lex-tree-actions">'
      +         '<button data-action="new-file">' + IC.plus + '<span data-i18n="lexicon.newFileLabel">' + _t("lexicon.newFileLabel", "New file") + '</span></button>'
      +         '<button data-action="new-folder">' + IC.plus + '<span data-i18n="lexicon.newFolderLabel">' + _t("lexicon.newFolderLabel", "New folder") + '</span></button>'
      +         '<button data-action="refresh" class="lex-icon-btn" data-i18n-title="lexicon.refresh.tooltip" title="' + _t("lexicon.refresh.tooltip", "Refresh file tree") + '" aria-label="' + _t("lexicon.refresh.tooltip", "Refresh file tree") + '">' + IC.refresh + '</button>'
      +       '</div>'
      +     '</div>'
      +     '<div class="lex-tree-list"></div>'
      +   '</div>'
      +   '<div class="lex-editor-panel">'
      +     '<div class="lex-empty">'
      +       '<div class="lex-empty-icon">' + IC.edit + '</div>'
      +       '<div data-i18n="lexicon.empty.selectFile">' + _t("lexicon.empty.selectFile", "Select a wildcard file to edit") + '</div>'
      +     '</div>'
      +     '<div class="lex-editor-wrap" style="display:none">'
      +       '<div class="lex-editor-header">'
      +         '<div class="lex-file-head"><div class="lex-file-line"><span class="lex-file-name"></span><span class="lex-dirty-dot"></span></div><div class="lex-file-usage"></div></div>'
      +         '<button class="lex-sort-btn" data-sort="az" data-i18n-title="lexicon.sort.tooltip" title="' + _t("lexicon.sort.tooltip", "Sort entries (click to cycle: A→Z, Z→A, Shuffle)") + '">' + IC.sort + '<span class="lex-btn-label">' + _t("lexicon.sort.az", "Sort A\u2013Z") + '</span></button>'
      +         '<button class="lex-dedup-btn" data-i18n-title="lexicon.dedup.tooltip" title="' + _t("lexicon.dedup.tooltip", "Remove duplicate entries") + '">' + IC.dedup + '<span data-i18n="lexicon.removeDuplicates">' + _t("lexicon.removeDuplicates", "Remove duplicates") + '</span></button>'
      +         '<button class="lex-preview-toggle" data-i18n-title="lexicon.previewToggle.tooltip" title="' + _t("lexicon.previewToggle.tooltip", "Toggle preview panel") + '">' + IC.eye + '<span data-i18n="lexicon.previewToggle">' + _t("lexicon.previewToggle", "Preview") + '</span></button>'
      +         '<span class="lex-header-sep"></span>'
      +         '<button class="lex-autosave-toggle" role="switch" data-i18n-title="lexicon.autosave.off.tooltip" title="' + _t("lexicon.autosave.off.tooltip", "Autosave OFF (click to enable)") + '"><span class="lex-switch" aria-hidden="true"></span><span data-i18n="lexicon.autosave">' + _t("lexicon.autosave", "Autosave") + '</span></button>'
      +         '<button class="lex-save-btn" data-i18n="lexicon.save">' + _t("lexicon.save", "Save") + '</button>'
      +         '<button class="lex-delete-btn" data-i18n-title="lexicon.delete" title="' + _t("lexicon.delete", "Delete") + '" aria-label="' + _t("lexicon.delete", "Delete") + '">' + IC.trash + '</button>'
      +       '</div>'
      +       '<div class="lex-editor-body">'
      +         '<div class="lex-gutter"><div class="lex-gutter-inner"></div></div>'
      +         '<div class="lex-editor-area">'
      +           '<pre class="lex-highlight" aria-hidden="true"></pre>'
      +           '<textarea class="lex-textarea" spellcheck="false" data-i18n-placeholder="lexicon.empty.entries" placeholder="' + _t("lexicon.empty.entries", "One entry per line\u2026") + '"></textarea>'
      +         '</div>'
      +       '</div>'
      +       '<div class="lex-preview-panel">'
      +         '<div class="lex-preview-body">'
      +           '<span class="lex-preview-title" data-i18n="lexicon.previewTitle">' + _t("lexicon.previewTitle", "Preview") + '</span>'
      +           '<span class="lex-preview-label" data-i18n="lexicon.preview.picked">' + _t("lexicon.preview.picked", "Picked:") + '</span>'
      +           '<span class="lex-preview-raw"></span>'
      +           '<span class="lex-preview-label" data-i18n="lexicon.preview.resolved">' + _t("lexicon.preview.resolved", "Resolved:") + '</span>'
      +           '<span class="lex-preview-result"></span>'
      +           '<button class="lex-preview-roll">' + IC.dice + '<span data-i18n="lexicon.preview.again">' + _t("lexicon.preview.again", "Pick again") + '</span></button>'
      +         '</div>'
      +       '</div>'
      +     '</div>'
      +   '</div>'
      +   '</div>'
      +   '<div class="lex-footer">'
      +     '<span class="lex-tree-footer">\u2014</span>'
      +     '<span class="lex-editor-footer"><span class="lex-footer-path"></span><span class="lex-footer-entries"></span><span class="lex-footer-status"></span></span>'
      +   '</div>'
      + '</div>'
      + '<div class="lex-context-menu"></div>';

    const root = container.querySelector(".lex-layout");
    _els.errorBanner     = root.querySelector(".lex-error-banner");
    _els.treeList        = root.querySelector(".lex-tree-list");
    _els.treeFooter      = root.querySelector(".lex-tree-footer");
    _els.treeSearch      = root.querySelector(".lex-tree-search");
    _els.empty           = root.querySelector(".lex-empty");
    _els.editorWrap      = root.querySelector(".lex-editor-wrap");
    _els.fileName        = root.querySelector(".lex-file-name");
    _els.fileUsage       = root.querySelector(".lex-file-usage");
    _els.dirtyDot        = root.querySelector(".lex-dirty-dot");
    _els.saveBtn         = root.querySelector(".lex-save-btn");
    _els.autoSaveToggle  = root.querySelector(".lex-autosave-toggle");
    _els.previewToggle   = root.querySelector(".lex-preview-toggle");
    _els.sortBtn         = root.querySelector(".lex-sort-btn");
    _els.dedupBtn        = root.querySelector(".lex-dedup-btn");
    _els.deleteBtn       = root.querySelector(".lex-delete-btn");
    _els.gutter          = root.querySelector(".lex-gutter");
    _els.gutterInner     = root.querySelector(".lex-gutter-inner");
    _els.highlight       = root.querySelector(".lex-highlight");
    _els.textarea        = root.querySelector(".lex-textarea");
    _els.previewPanel    = root.querySelector(".lex-preview-panel");
    _els.previewRaw      = root.querySelector(".lex-preview-raw");
    _els.previewResult   = root.querySelector(".lex-preview-result");
    _els.footerPath      = root.querySelector(".lex-footer-path");
    _els.footerEntries   = root.querySelector(".lex-footer-entries");
    _els.footerStatus    = root.querySelector(".lex-footer-status");
    _els.contextMenu     = container.querySelector(".lex-context-menu");

    // Wire events
    _els.saveBtn.addEventListener("click", saveFile);
    _els.deleteBtn.addEventListener("click", () => { if (LX.openFile) deleteItem(LX.openFile.path); });
    _els.autoSaveToggle.addEventListener("click", _toggleAutoSave);
    _els.previewToggle.addEventListener("click", _togglePreview);
    root.querySelector(".lex-preview-roll").addEventListener("click", _rollPreview);
    _els.sortBtn.addEventListener("click", sortEntries);
    _els.dedupBtn.addEventListener("click", deduplicateEntries);

    root.querySelector('[data-action="new-file"]').addEventListener("click", () => createFile());
    root.querySelector('[data-action="new-folder"]').addEventListener("click", () => createFolder());
    root.querySelector('[data-action="refresh"]').addEventListener("click", () => { loadTree(); _toast(_t("lexicon.toast.refreshed", "Refreshed")); });

    // Textarea
    _els.textarea.addEventListener("input", () => {
        if (!LX.openFile) return;
        const content = _els.textarea.value;
        if (content !== LX.openFile.content) {
            LX.dirtyFiles.set(LX.openFile.path, content);
        } else {
            LX.dirtyFiles.delete(LX.openFile.path);
        }
        _updateGutter(); _updateHighlight(); _updateEditorState(); _updateDirtyIndicators(); _scheduleAutoSave();
    });
    _els.textarea.addEventListener("scroll", _syncScroll);
    _els.textarea.addEventListener("keydown", (e) => {
        if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.altKey) {
            e.preventDefault();
            const ta = _els.textarea, s = ta.selectionStart, end = ta.selectionEnd;
            ta.value = ta.value.substring(0, s) + "\t" + ta.value.substring(end);
            ta.selectionStart = ta.selectionEnd = s + 1;
            ta.dispatchEvent(new Event("input"));
        }
    });
    container.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveFile(); }
    });

    // ── Arrow key navigation for the tree ──
    _els.treeList.setAttribute("tabindex", "0");
    _els.treeList.style.outline = "none";
    _els.treeList.addEventListener("keydown", _handleTreeKey);
    _els.treeSearch.addEventListener("input", _onSearchInput);

    // Root-level drop target + panel-wide OS file drop
    _setupRootDrop();
    _setupPanelDrop(root.querySelector(".lex-tree-panel"));

    // Tag autocomplete — attach if the module is loaded
    if (window.TagComplete && window.TagComplete.attach) {
        window.TagComplete.attach(_els.textarea);
    }
}

// ========================================================================
// HELPERS
// ========================================================================

function _esc(s) { if (!s) return ""; const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
function _escHTML(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

// ========================================================================
// PUBLIC API — for cross-module access (e.g., wildcard browser → open file)
// ========================================================================

// `refresh()` is invoked by app.js after the user changes the native
// wildcard folder so the editor's tree reflects the new active root.
// Safe no-op if the module hasn't built its UI yet (StudioModules calls
// loadTree on activate anyway).
window.LexiconAPI = {
    openFile,
    refresh: () => { try { loadTree(); } catch (_) {} },
};

// ========================================================================
// MODULE REGISTRATION
// ========================================================================

if (window.StudioModules) {
    StudioModules.register("lexicon", {
        label: "Wildcards", icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/></svg>',
        // No probe, as in the Extension. One was added while `/studio/lexicon`
        // had no handler; Studio serves it now, and the probe hid the tab
        // whenever /tree refused -- no folder chosen, a folder not reachable,
        // or one request failing at page load -- so Wildcards vanished with
        // nothing saying why. The panel shows the refusal instead, and
        // loadTree() runs again every time the tab is opened. Export/Import,
        // which have no route, are not in the toolbar (see IMPORT / EXPORT).
        init(container, services) {
            console.log(TAG, "Initializing Wildcards module v" + VERSION);
            if (!document.querySelector('link[href*="lexicon.css"]')) {
                const link = document.createElement("link"); link.rel = "stylesheet";
                link.href = "/studio/static/lexicon.css?v=" + VERSION; document.head.appendChild(link);
            }
            _buildUI(container); loadTree();
        },
        activate(container, services) { loadTree(); },
        deactivate() {},
    });
} else { console.warn(TAG, "StudioModules not available \u2014 Wildcards cannot register"); }

})();
