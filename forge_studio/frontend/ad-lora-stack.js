/*
 * ad-lora-stack.js — per-slot ADetailer LoRA state
 *
 * Each ADetailer slot (1-3) can carry its own list of LoRAs, applied ONLY
 * during that slot's inpaint pass. This state is intentionally SEPARATE from
 * the global main-prompt LoRA Stack (lora-stack.js): a slot LoRA must never
 * leak into base generation or another slot.
 *
 * The canonical state lives here (per-slot arrays); the chip DOM is only a
 * render target. State is mirrored to a hidden input per slot (#adLoraStack{n})
 * so the existing workflow/defaults/session capture picks it up like any other
 * field, and the payload builders read getSlot(n) directly.
 *
 * Naming mirrors the main LoRA Stack exactly (name = lora.name.split("/").pop()),
 * so a LoRA picked into a slot compiles to the same <lora:name:weight> tag it
 * would in the main stack.
 *
 * `compileSuffix()` is what app.js appends to the slot's prompt, and it is
 * how these LoRAs reach the engine: `<lora:...>` is parsed out of prompt
 * text and there is no separate transport. This file used to say the
 * backend performed "its own validated compilation" from `payloadForSlot`
 * -- there was never a `loras` field in the request contract to compile
 * from, so nothing an owner picked here ever reached a generation.
 */
(function () {
  "use strict";

  var TAG = "[AD LoRA]";
  var SLOTS = [1, 2, 3];
  // NEITHER CAP HAS A BACKEND BEHIND IT. Both comments used to claim one
  // ("backend enforces its own cap too", "backend caps too") and there is no
  // `loras` field in the request contract at all, so there was nothing to
  // enforce anything. Removed 2026-08-20 by owner ruling: Studio does not
  // invent limits.
  //
  //   MAX_PER_SLOT = 8    capped LoRAs per slot. They compile into the slot
  //                       prompt now, which bounds nothing.
  //   MAX_ACT_LEN = 512   silently truncated a LoRA's OWN trigger words. Some
  //                       LoRAs ship a long activation string, and cutting it
  //                       mid-phrase changes what the prompt says.

  var slots = { 1: [], 2: [], 3: [] };
  var _idSeq = 0;
  var _selfWrite = false;   // true while WE write a hidden input (ignore the echo)

  function _newId() { return "adl" + (++_idSeq).toString(36) + Date.now().toString(36); }

  // Mirror lora-stack.js _normalizeLoraName: the inserted tag is just the
  // filename, matching manual inserts and the main stack.
  function _norm(lora) {
    if (typeof lora === "string") return lora.split("/").pop();
    if (lora && typeof lora.name === "string") return lora.name.split("/").pop();
    return "";
  }
  function _alias(name) {
    return String(name || "").replace(/\.(safetensors|ckpt|pt|pth|bin)$/i, "");
  }
  function _clampWeight(w) {
    var n = parseFloat(w);
    if (!isFinite(n)) n = 1.0;
    if (n < -10) n = -10;
    if (n > 10) n = 10;
    return Math.round(n * 100) / 100;
  }

  function getSlot(n) { return (slots[n] || []).slice(); }

  function _hidden(n) { return document.getElementById("adLoraStack" + n); }
  function _writeHidden(n) {
    var el = _hidden(n);
    if (el) {
      _selfWrite = true;
      el.value = JSON.stringify(slots[n] || []);
      // Mirror to workflow/defaults capture. dispatch is synchronous, so the
      // _selfWrite guard is still set when our own input listener fires.
      el.dispatchEvent(new Event("input", { bubbles: true }));
      _selfWrite = false;
    }
  }
  function _commit(n) { _writeHidden(n); render(n); }

  function _sanitizeList(items) {
    if (!Array.isArray(items)) return [];
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i] || {};
      var name = _norm(it);
      if (!name && typeof it.name === "string") name = it.name.split("/").pop();
      if (!name) continue;
      out.push({
        id: it.id || _newId(),
        name: name,
        alias: _alias(name),
        weight: _clampWeight(it.weight),
        activationText: String(it.activationText || it.activation_text || ""),
        includeActivation: !!(it.includeActivation || it.include_activation),
      });
    }
    return out;
  }

  function add(n, lora, weight) {
    n = Number(n);
    if (SLOTS.indexOf(n) === -1) return;
    var name = _norm(lora);
    if (!name) return;
    var list = slots[n] || (slots[n] = []);
    if (list.some(function (it) { return it.name === name; })) {
      if (window.showToast) window.showToast(name + " is already in ADetailer slot " + n, "info");
      render(n);
      return;
    }
    var w;
    if (typeof weight === "number") w = weight;
    else if (lora && lora.preferred_weight > 0) w = lora.preferred_weight;
    else w = 1.0;
    var act = (lora && typeof lora.activation_text === "string")
      ? lora.activation_text.trim() : "";
    list.push({
      id: _newId(), name: name, alias: _alias(name),
      weight: _clampWeight(w), activationText: act, includeActivation: false,
    });
    _commit(n);
  }

  function remove(n, id) {
    var list = slots[n];
    if (!list) return;
    slots[n] = list.filter(function (it) { return it.id !== id; });
    _commit(n);
  }

  function setWeight(n, id, weight) {
    var list = slots[n];
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) { list[i].weight = _clampWeight(weight); break; }
    }
    _writeHidden(n);   // no full re-render — preserve the weight input's focus
  }

  function setIncludeActivation(n, id, on) {
    var list = slots[n];
    if (!list) return;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) { list[i].includeActivation = !!on; break; }
    }
    _commit(n);
  }

  function clearSlot(n) { slots[n] = []; _commit(n); }
  function setSlot(n, items) { slots[n] = _sanitizeList(items); _commit(n); }

  // THE REAL PATH. app.js appends this to the slot's prompt.
  function compileSuffix(n) {
    var list = slots[n] || [];
    var out = list
      .filter(function (it) { return it.name; })
      .map(function (it) {
        var w = Number.isFinite(it.weight) ? it.weight : 1.0;
        return "<lora:" + it.name + ":" + w + ">";
      })
      .join(", ");
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      if (it.includeActivation && it.activationText) {
        var t = it.activationText.trim();
        if (t && out.toLowerCase().indexOf(t.toLowerCase()) === -1) {
          out = out ? (out + ", " + t) : t;
        }
      }
    }
    return out;
  }

  // Structured per-slot payload. Kept for session save/restore and for the
  // legacy collector; it is NOT how the LoRAs reach a generation.
  function payloadForSlot(n) {
    return getSlot(n).map(function (it) {
      return {
        name: it.name,
        weight: it.weight,
        activation_text: it.activationText || "",
        include_activation: !!it.includeActivation,
      };
    });
  }

  function serialize() { return { 1: getSlot(1), 2: getSlot(2), 3: getSlot(3) }; }
  function restore(obj) {
    obj = obj || {};
    SLOTS.forEach(function (n) { slots[n] = _sanitizeList(obj[n]); _writeHidden(n); render(n); });
  }
  // Rehydrate from the hidden inputs after a workflow/session restore set them.
  function restoreFromHidden() {
    SLOTS.forEach(function (n) {
      var el = _hidden(n);
      if (el && el.value) {
        try { slots[n] = _sanitizeList(JSON.parse(el.value)); }
        catch (e) { slots[n] = []; }
      } else {
        slots[n] = [];
      }
      render(n);
    });
  }

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render(n) {
    var box = document.getElementById("adLoraChips" + n);
    if (!box) return;
    var list = slots[n] || [];
    box.innerHTML = "";
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      var chip = document.createElement("span");
      chip.className = "ad-lora-chip";
      chip.dataset.id = it.id;
      var hasAct = !!(it.activationText && it.activationText.trim());
      chip.innerHTML =
        '<span class="ad-lora-name" title="' + _esc(it.name) + '">' + _esc(it.alias || it.name) + '</span>' +
        '<input class="ad-lora-weight" type="number" step="0.05" value="' + it.weight + '" aria-label="LoRA weight">' +
        (hasAct
          ? '<button type="button" class="ad-lora-trig' + (it.includeActivation ? ' on' : '') +
            '" title="Include this LoRA\'s trigger words in the ADetailer prompt">T</button>'
          : '') +
        '<button type="button" class="ad-lora-remove" title="Remove" aria-label="Remove LoRA">×</button>';
      box.appendChild(chip);

      (function (item) {
        var wEl = chip.querySelector(".ad-lora-weight");
        if (wEl) wEl.addEventListener("change", function () { setWeight(n, item.id, wEl.value); });
        var tEl = chip.querySelector(".ad-lora-trig");
        if (tEl) tEl.addEventListener("click", function () { setIncludeActivation(n, item.id, !item.includeActivation); });
        var rEl = chip.querySelector(".ad-lora-remove");
        if (rEl) rEl.addEventListener("click", function () { remove(n, item.id); });
      })(it);
    }
  }
  function renderAll() { SLOTS.forEach(render); }

  function _wireButtons() {
    SLOTS.forEach(function (n) {
      var btn = document.getElementById("adLoraAdd" + n);
      if (btn && !btn._adWired) {
        btn._adWired = true;
        btn.addEventListener("click", function () {
          if (window.LoraBrowser && typeof window.LoraBrowser.openPick === "function") {
            window.LoraBrowser.openPick(function (lora, weight) { add(n, lora, weight); });
          } else if (window.showToast) {
            window.showToast("LoRA Browser not available", "error");
          }
        });
      }
    });
  }

  // Rehydrate chips when a hidden input is written EXTERNALLY (workflow / tab /
  // defaults / session restore all set #adLoraStack{n} and dispatch input). Our
  // own writes are ignored via the _selfWrite guard so user edits don't re-render
  // and steal focus.
  function _wireHiddenInputs() {
    SLOTS.forEach(function (n) {
      var el = _hidden(n);
      if (el && !el._adWired) {
        el._adWired = true;
        el.addEventListener("input", function () {
          if (_selfWrite) return;
          try { slots[n] = _sanitizeList(JSON.parse(el.value || "[]")); }
          catch (e) { slots[n] = []; }
          render(n);
        });
      }
    });
  }

  function init() {
    _wireButtons();
    _wireHiddenInputs();
    restoreFromHidden();  // pick up any value already present (e.g. session)
    console.log(TAG + " Initialized");
  }

  window.ADLoRAStack = {
    add: add, remove: remove, setWeight: setWeight, setIncludeActivation: setIncludeActivation,
    getSlot: getSlot, setSlot: setSlot, clearSlot: clearSlot,
    serialize: serialize, restore: restore, restoreFromHidden: restoreFromHidden,
    compileSuffix: compileSuffix, payloadForSlot: payloadForSlot,
    render: render, renderAll: renderAll,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
