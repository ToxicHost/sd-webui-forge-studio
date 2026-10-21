/**
 * Forge Studio — Develop display worker (DV1)
 * by ToxicHost & Moritz
 *
 * Licensed under the GNU AGPL-3.0-or-later (see LICENSE).
 *
 * Develops the picture ON SCREEN off the page's thread, so a Develop slider
 * never waits for the pipeline. It runs develop.js itself -- the file the page
 * runs -- so the screen and a saved file go through one pipeline. Only
 * `applyToContext` is used; the panel code in that file is never called here.
 *
 * Messages in:  {w, h, pixelsVersion, sig, params, source?, masks?}
 *   source  ArrayBuffer of the pre-Develop RGBA picture, sent once per
 *           picture version (the page tracks what this worker holds)
 *   masks   [{id, coverage: ArrayBuffer}] for local adjustments
 * Messages out: {pixelsVersion, sig, w, h, pixels: ArrayBuffer} or {error}
 */
"use strict";

// What develop.js reads of the page, and nothing else.
self.__STUDIO_DEVELOP_WORKER = true;
self.window = self;
var _state = { W: 0, H: 0, masks: [] };
var _coverage = {};
var _pixelsVersion = -1;
self.StudioCore = {
    state: _state,
    maskCoverage: function (id) { return _coverage[id] || null; },
    getPixelsVersion: function () { return _pixelsVersion; },
};
// The panel registers itself with the page's module system; here there is no
// panel, so the registration is accepted and dropped (instead of a warning).
self.StudioModules = { register: function () {} };
self.document = {
    // the stylesheet check at load: "already there", so nothing is added
    querySelector: function () { return {}; },
    // the proxy and local-adjustment scratch canvases
    createElement: function () { return new OffscreenCanvas(1, 1); },
};

importScripts("develop.js" + self.location.search);

var _source = null, _canvas = null, _ctx = null;

self.onmessage = function (e) {
    var m = e.data || {};
    try {
        if (m.source) {
            _source = new ImageData(new Uint8ClampedArray(m.source), m.w, m.h);
            _pixelsVersion = m.pixelsVersion;
            _state.W = m.w; _state.H = m.h;
            // Picture-keyed caches belong to the previous picture.
            _state._developBlurCache = null;
        }
        if (!_source || _pixelsVersion !== m.pixelsVersion || _source.width !== m.w || _source.height !== m.h) {
            throw new Error("no picture for this job");
        }
        _coverage = {};
        _state.masks = [];
        (m.masks || []).forEach(function (k) {
            _coverage[k.id] = new Uint8Array(k.coverage);
            _state.masks.push({ id: k.id });
        });
        if (!_canvas || _canvas.width !== m.w || _canvas.height !== m.h) {
            _canvas = new OffscreenCanvas(m.w, m.h);
            // Read back on every job: a CPU-backed canvas (willReadFrequently)
            // avoids a GPU readback each time.
            _ctx = _canvas.getContext("2d", { colorSpace: "srgb", willReadFrequently: true });
        }
        var t0 = performance.now();
        _ctx.putImageData(_source, 0, 0);
        var t1 = performance.now();
        self.StudioDevelop.applyToContext(_ctx, m.w, m.h, m.params);
        var t2 = performance.now();
        var out = _ctx.getImageData(0, 0, m.w, m.h);
        var t3 = performance.now();
        self.postMessage({ pixelsVersion: m.pixelsVersion, sig: m.sig, w: m.w, h: m.h, pixels: out.data.buffer,
                           ms: t3 - t0, split: [t1 - t0, t2 - t1, t3 - t2] }, [out.data.buffer]);
    } catch (err) {
        self.postMessage({ error: String((err && err.message) || err) });
    }
};
