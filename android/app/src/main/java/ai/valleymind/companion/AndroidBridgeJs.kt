package ai.valleymind.companion

/**
 * AndroidBridgeJs — the JavaScript shim injected into BOTH WebViews (the main
 * ValleyMind app WebView and the overlay WebView).
 *
 * It presents the exact object the web layer already looks for
 * (window.vmCompanionAndroid, see static/settings.js -> _vmCompanionAndroidBridge
 * and vmCompanionControl). The Java side only exposes synchronous calls, so the
 * shim turns them into Promises and pushes asynchronous state changes back via
 * window.__vmAndroidState(json), which settings.js subscribes to.
 *
 * Every value comes from REAL Android state:
 *   granted  — Settings.canDrawOverlays()
 *   running  — the overlay service is started
 *   visible  — the overlay window is actually attached
 * never from the request the page just made.
 */
object AndroidBridgeJs {
    const val ANDROID_BRIDGE_JS: String = """
(function () {
  var B = window.VmAndroidBridge;
  if (!B) { return; }
  var listeners = [];
  var __vmTtsSeq = 0;

  // ── native speech recognition bus ────────────────────────────────────
  // The platform recogniser reports back through window.__vmAsrEvent(json).
  // The bus lives on the window so re-injecting this shim (page start and
  // page finish both inject it) never loses a queued event or duplicates a
  // listener.
  var bus = window.__vmAsrBus;
  if (!bus) {
    bus = { listeners: [], queue: [] };
    window.__vmAsrBus = bus;
  }

  function emitAsr(ev) {
    for (var i = 0; i < bus.listeners.length; i++) {
      try { bus.listeners[i](ev); } catch (e) { /* one bad listener never blocks others */ }
    }
  }

  window.__vmAsrEvent = function (payload) {
    var ev;
    try {
      ev = (typeof payload === "string") ? JSON.parse(payload) : payload;
    } catch (e) {
      ev = { type: "error", reason: "bad-payload" };
    }
    if (!ev || typeof ev !== "object") return;
    // Events can arrive before the page has subscribed: hold them, then flush.
    if (!bus.listeners.length) { if (bus.queue.length < 20) bus.queue.push(ev); return; }
    emitAsr(ev);
  };

  function state() {
    try { return JSON.parse(B.getStateJson()); }
    catch (e) { return { granted: false, running: false, visible: false }; }
  }

  function emit() {
    var v = !!state().visible;
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](v); } catch (e) { /* one bad listener never blocks others */ }
    }
  }

  // Called from native when the service/overlay state changes.
  window.__vmAndroidState = function () { emit(); };

  window.vmCompanionAndroid = {
    isSupported: function () { try { return !!B.isOverlaySupported(); } catch (e) { return false; } },
    hasPermission: function () { try { return !!B.hasPermission(); } catch (e) { return false; } },
    requestPermission: function () { try { B.requestPermission(); } catch (e) { /* ignore */ } },
    getState: function () { return state(); },
    getVisible: function () { return Promise.resolve(!!state().visible); },
    setVisible: function (on) {
      return new Promise(function (resolve, reject) {
        var want = !!on;
        if (want && !B.hasPermission()) { reject(new Error("overlay-permission")); return; }
        try { B.setVisible(want); } catch (e) { reject(e); return; }
        // Resolve only when the overlay ACTUALLY matches the request, or after a
        // bounded wait — never claim a state the platform did not reach.
        var started = Date.now();
        (function tick() {
          var s = state();
          var ok = want ? (s.running && s.visible) : !s.visible;
          if (ok) { resolve(!!s.visible); return; }
          if (Date.now() - started > 12000) { resolve(!!s.visible); return; }
          setTimeout(tick, 250);
        })();
      });
    },
    onCompanionVisibility: function (cb) { if (typeof cb === "function") { listeners.push(cb); } },

    // ── Native TTS fallback ──────────────────────────────────────────────
    // Android's WebView has no speechSynthesis, so the companion's replies are
    // spoken by the device's own TTS engine. The completion callback is named
    // here and invoked by native as window.__vmTtsDone_<name>(ok).
    hasNativeTts: function () { try { return typeof B.ttsSpeak === "function"; } catch (e) { return false; } },
    ttsSpeak: function (text, voiceId, done) {
      if (typeof B.ttsSpeak !== "function") { if (done) done(false); return false; }
      var name = "vmTts" + (++__vmTtsSeq);
      window["__vmTtsDone_" + name] = function (ok) {
        try { delete window["__vmTtsDone_" + name]; } catch (e) { window["__vmTtsDone_" + name] = undefined; }
        if (typeof done === "function") done(!!ok);
      };
      try { return !!B.ttsSpeak(String(text || ""), voiceId || null, name); }
      catch (e) { if (done) done(false); return false; }
    },
    ttsStop: function () { try { if (typeof B.ttsStop === "function") B.ttsStop(); } catch (e) { /* ignore */ } },

    // ── native speech recognition ─────────────────────────────────────────
    // The overlay page prefers the platform recogniser (it reports the end of
    // speech itself and the audio never leaves the device). When the platform
    // has none, the page falls back to its own recording path.
    hasNativeAsr: function () {
      try { return typeof B.asrSupported === "function" && !!B.asrSupported(); }
      catch (e) { return false; }
    },
    asrStart: function (locale) {
      try {
        // Never ask native to listen when the platform has no recogniser: the
        // page would otherwise wait for an event that can never arrive.
        if (typeof B.asrSupported !== "function" || !B.asrSupported()) return false;
        return typeof B.asrStart === "function" ? !!B.asrStart(locale || null) : false;
      } catch (e) { return false; }
    },
    asrStop: function () { try { if (typeof B.asrStop === "function") B.asrStop(); } catch (e) { /* ignore */ } },
    asrCancel: function () { try { if (typeof B.asrCancel === "function") B.asrCancel(); } catch (e) { /* ignore */ } },
    onAsrEvent: function (cb) {
      if (typeof cb !== "function") return;
      bus.listeners.push(cb);
      while (bus.queue.length) emitAsr(bus.queue.shift());
    }
  };

  try { B.notifyBridgeReady(); } catch (e) { /* ignore */ }
})();
"""
}
