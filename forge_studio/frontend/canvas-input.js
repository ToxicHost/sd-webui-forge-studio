/**
 * Forge Studio — Canvas input seam (CT2)
 * by ToxicHost & Moritz
 *
 * ONE normalization point for pointer input, so every tool reads the same
 * sample and no tool has to know what a PointerEvent is.
 *
 * WHY THIS EXISTS
 *
 * `AC2-current-canvas-crosswalk.md` inventoried what the Canvas could see, and
 * the answer was: less than the browser was offering it.
 *
 *     getCoalescedEvents   ZERO occurrences   fast strokes dropped samples
 *     pointerType          ZERO occurrences   mouse, pen and touch identical
 *     isPrimary            ZERO occurrences   a second touch point painted
 *     tiltX/tiltY/twist    ZERO occurrences   no tilt could reach any tool
 *
 * and the whole of the pressure pipeline was one expression:
 *
 *     p.pressure = e.pressure || 0.5;
 *
 * That `|| 0.5` is doing something important and doing it by accident. It is
 * the mouse fallback the handoff requires -- "Mouse fallback must not
 * interpret an unavailable pressure value as zero-opacity paint" -- but it
 * cannot tell an unavailable pressure from a real one:
 *
 *     mouse, button down       0    -> 0.5   right, by luck
 *     pen, barely touching     0.0  -> 0.5   WRONG, and it is the case that
 *                                            pressure dynamics are ABOUT
 *
 * `pointerType` is the discriminator. This module reads it, reports
 * `pressureAvailable` alongside the value, and lets the caller decide.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO, YET
 *
 * CT2's migration discipline is explicit: "First land the input seam without
 * changing rendered output." So `resolvePressure` still returns 0.5 for every
 * device that does not report usable pressure, which is byte-for-byte what
 * `e.pressure || 0.5` returned for a mouse -- the overwhelmingly common case
 * and the only one this install can test without pen hardware. What changes is
 * that the ANSWER now carries whether it was measured or substituted, so CT3
 * can act on the difference instead of guessing.
 *
 * Real pen support is not claimed. There is no pen on this machine, and a
 * synthetic PointerEvent is not hardware evidence.
 *
 * NO DOM AT MODULE SCOPE. Every function here takes what it needs, so the
 * whole file is exercisable from a test that never opens a browser.
 */
(function () {
"use strict";

// A mouse reports 0.5 while a button is down and 0 while it is not. Neither
// number is a measurement, so both resolve to this.
var FALLBACK_PRESSURE = 0.5;

/**
 * Is this device's pressure a MEASUREMENT or a placeholder?
 *
 * Deliberately conservative. A device Studio cannot identify is treated as
 * having no pressure, because substituting a known-safe 0.5 is recoverable and
 * painting a stroke at zero opacity is not.
 */
function pressureIsMeasured(pointerType, rawPressure) {
    if (pointerType === "pen") return true;
    // Touch digitisers are split: some report a real force, many report a
    // constant 1 (contact) or 0 (no contact). A value strictly between the two
    // could only come from a measurement.
    if (pointerType === "touch") {
        return typeof rawPressure === "number"
            && rawPressure > 0 && rawPressure < 1;
    }
    return false;
}

/**
 * The pressure a stroke should use, and whether it was measured.
 *
 * Returns { pressure, available, raw }. `available` is the field CT3 needs:
 * a pressure dynamic must not act on a substituted value, or every mouse
 * stroke would be drawn as though the owner pressed exactly half way.
 */
function resolvePressure(event) {
    var raw = (event && typeof event.pressure === "number") ? event.pressure : 0;
    var type = event && event.pointerType;
    if (!pressureIsMeasured(type, raw)) {
        return { pressure: FALLBACK_PRESSURE, available: false, raw: raw };
    }
    // Clamped rather than trusted: the spec bounds it to 0..1 and a driver
    // that disagrees would otherwise scale a brush off the canvas.
    var clamped = raw < 0 ? 0 : (raw > 1 ? 1 : raw);
    return { pressure: clamped, available: true, raw: raw };
}

/**
 * One PointerEvent as a tool-facing sample.
 *
 * `toDoc` is injected rather than imported so this file has no opinion about
 * zoom, pan or device pixel ratio -- `screenToDoc` owns all three and is
 * already correct, and duplicating it here is how the two would drift.
 */
function normalize(event, toDoc, extra) {
    var doc = toDoc ? toDoc(event.clientX, event.clientY) : { x: 0, y: 0 };
    var pressure = resolvePressure(event);
    return {
        pointerId: event.pointerId,
        pointerType: event.pointerType || "",
        isPrimary: event.isPrimary !== false,

        // Document space, which is what every tool wants.
        x: doc.x,
        y: doc.y,
        // CSS pixels, which is what pan, zoom-drag and the brush-resize drag
        // want. Both are carried so no caller has to keep the raw event to get
        // the other one.
        clientX: event.clientX,
        clientY: event.clientY,

        pressure: pressure.pressure,
        pressureAvailable: pressure.available,
        rawPressure: pressure.raw,

        // Zero rather than absent. A tool asking for tilt on a mouse gets
        // "flat", which is the honest answer, and does not have to branch.
        tiltX: typeof event.tiltX === "number" ? event.tiltX : 0,
        tiltY: typeof event.tiltY === "number" ? event.tiltY : 0,
        twist: typeof event.twist === "number" ? event.twist : 0,
        tiltAvailable: event.pointerType === "pen"
            && (typeof event.tiltX === "number" || typeof event.tiltY === "number"),

        buttons: event.buttons,
        button: typeof event.button === "number" ? event.button : -1,
        time: typeof event.timeStamp === "number" ? event.timeStamp : 0,

        coalesced: !!(extra && extra.coalesced),
        source: event
    };
}

/**
 * Every sample a move event actually carries.
 *
 * A pointermove fired once per frame can stand for a dozen physical samples,
 * and the browser keeps them: `getCoalescedEvents()` returns the ones it
 * merged. Nothing in Studio has ever asked, so a fast stroke has always been
 * drawn from whatever survived the frame boundary -- which is exactly the
 * "corners and curves" and "fast mouse stroke" case CT3 has to pass.
 *
 * Falls back to the event itself where the method is missing (older WebKit)
 * or throws, so this can never return an empty stroke.
 */
function samplesFrom(event, toDoc) {
    var list = null;
    if (event && typeof event.getCoalescedEvents === "function") {
        try { list = event.getCoalescedEvents(); } catch (e) { list = null; }
    }
    if (!list || !list.length) return [normalize(event, toDoc, null)];

    var out = [];
    var lastX = NaN, lastY = NaN, lastT = NaN;
    for (var i = 0; i < list.length; i++) {
        var raw = list[i];
        // Deduplicated on the RAW client coordinates and the timestamp, before
        // the document transform: two samples that differ only after a divide
        // by the zoom scale are the same sample, and at high zoom the
        // transform can separate them.
        if (raw.clientX === lastX && raw.clientY === lastY
            && raw.timeStamp === lastT) continue;
        lastX = raw.clientX; lastY = raw.clientY; lastT = raw.timeStamp;
        out.push(normalize(raw, toDoc, { coalesced: true }));
    }
    // A coalesced list is documented to end at the dispatched event, but a
    // browser that returned an empty-after-dedupe list must still paint.
    if (!out.length) return [normalize(event, toDoc, null)];
    return out;
}

/**
 * Should this event drive a tool at all?
 *
 * `isPrimary` is false for every finger after the first in a multi-touch
 * gesture. Nothing checked it, so a second finger painted a second stroke into
 * the same buffer -- and the same is true of a second pen on a shared display.
 */
function isDrivingPointer(event) {
    return !!event && event.isPrimary !== false;
}

/**
 * Take pointer capture, and say whether it was taken.
 *
 * Wrapped for the reason every release in this codebase is already wrapped:
 * a pointer that has already been released throws, and a tool that lets that
 * escape leaves the stroke half-begun.
 */
function capture(element, pointerId) {
    if (!element || typeof element.setPointerCapture !== "function") return false;
    try { element.setPointerCapture(pointerId); return true; }
    catch (e) { return false; }
}

function release(element, pointerId) {
    if (!element || typeof element.releasePointerCapture !== "function") return false;
    try { element.releasePointerCapture(pointerId); return true; }
    catch (e) { return false; }
}

window.StudioInput = {
    FALLBACK_PRESSURE: FALLBACK_PRESSURE,
    pressureIsMeasured: pressureIsMeasured,
    resolvePressure: resolvePressure,
    normalize: normalize,
    samplesFrom: samplesFrom,
    isDrivingPointer: isDrivingPointer,
    capture: capture,
    release: release
};

})();
