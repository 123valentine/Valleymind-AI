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
  var __vmCameraSeq = 0;

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

  // A process-wide speech bus so BOTH WebViews (main app + overlay) can observe
  // native TTS lifecycle events for lip-sync without owning the per-call
  // callback. Per-call handlers still work; this is purely additive.
  var speechBus = window.__vmSpeechBus;
  if (!speechBus) { speechBus = { listeners: [] }; window.__vmSpeechBus = speechBus; }
  function emitSpeech(ev) {
    for (var i = 0; i < speechBus.listeners.length; i++) {
      try { speechBus.listeners[i](ev); } catch (e) { /* ignore */ }
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

  // The versioned façade used by static/native_bridge.js (window.VMNative).
  // Installed once so older pages can feature-detect without knowing the shell.
  if (!window.vmNative) {
    var contract = null;
    var caps = [];
    try { contract = JSON.parse(B.contractJson()); } catch (e) { contract = null; }
    try { var parsed = JSON.parse(B.capabilitiesJson()); if (parsed && parsed.length) caps = parsed; } catch (e) {}
    window.vmNative = {
      contract: contract,
      capabilities: function () { return caps.slice(); },
      app: { getInfo: function () { return state(); } },
      overlay: {
        setRegion: function (w, h) {
          try { return JSON.parse(B.setOverlayRegion(w, h)); }
          catch (e) { return { ok: false, reason: "bridge-error" }; }
        },
        getRegion: function () {
          try { return JSON.parse(B.getOverlayRegion()); }
          catch (e) { return { ok: false, reason: "bridge-error" }; }
        }
      }
    };
  }

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

    // ── native TTS with real start/end events + a lip-sync level ──────────
    // The site's server TTS (when available) still wins. This path is used only
    // for the native fallback, where Android's engine reports real start/end but
    // has no amplitude stream: `onLevel` is a bounded speech-shaped envelope,
    // never presented as measured amplitude.
    hasNativeTtsV2: function () { try { return typeof B.ttsSpeakV2 === "function"; } catch (e) { return false; } },
    ttsSpeakV2: function (text, voiceId, handlers) {
      handlers = handlers || {};
      if (typeof B.ttsSpeakV2 !== "function") {
        return window.vmCompanionAndroid.ttsSpeak(text, voiceId, handlers.onEnd);
      }
      var name = "vmTtsV2" + (++__vmTtsSeq);
      window["__vmSpeechEvent_" + name] = function (event, value) {
        emitSpeech({ name: name, event: event, value: value });
        if (event === "start") {
          if (typeof handlers.onStart === "function") handlers.onStart(value);
        } else if (event === "level") {
          if (typeof handlers.onLevel === "function") handlers.onLevel(parseFloat(value) || 0);
        } else if (event === "end") {
          try { delete window["__vmSpeechEvent_" + name]; } catch (e) { window["__vmSpeechEvent_" + name] = undefined; }
          if (typeof handlers.onEnd === "function") handlers.onEnd(value === "true");
        }
      };
      // Keep the legacy completion callback too, so both bridges resolve once.
      window["__vmTtsDone_" + name] = function () {
        try { delete window["__vmTtsDone_" + name]; } catch (e) { window["__vmTtsDone_" + name] = undefined; }
      };
      try { return !!B.ttsSpeakV2(String(text || ""), voiceId || null, name); }
      catch (e) { if (typeof handlers.onEnd === "function") handlers.onEnd(false); return false; }
    },
    onSpeechEvent: function (cb) {
      if (typeof cb === "function") speechBus.listeners.push(cb);
    },

    // ── native camera eyes ────────────────────────────────────────────────
    // Native code only captures JPEG frames. The PAGE posts them to the same
    // authenticated /api/vision/analyze endpoint, so no session is duplicated.
    hasNativeCamera: function () {
      try { return typeof B.cameraSupported === "function" && !!B.cameraSupported(); }
      catch (e) { return false; }
    },
    cameraStart: function (facing) {
      try { return typeof B.cameraStart === "function" ? !!B.cameraStart(facing || "back") : false; }
      catch (e) { return false; }
    },
    cameraStop: function () { try { if (typeof B.cameraStop === "function") B.cameraStop(); } catch (e) { /* ignore */ } },
    cameraSwitch: function () {
      try { return typeof B.cameraSwitch === "function" ? !!B.cameraSwitch() : false; }
      catch (e) { return false; }
    },
    cameraState: function () {
      try { return JSON.parse(B.cameraStateJson()); }
      catch (e) { return { active: false, facing: "back" }; }
    },
    cameraCapture: function () {
      return new Promise(function (resolve, reject) {
        if (typeof B.cameraCapture !== "function") { reject(new Error("camera-unsupported")); return; }
        var name = "vmCam" + (++__vmCameraSeq);
        window["__vmCameraResult_" + name] = function (payload) {
          try { delete window["__vmCameraResult_" + name]; } catch (e) { window["__vmCameraResult_" + name] = undefined; }
          var res;
          try { res = (typeof payload === "string") ? JSON.parse(payload) : payload; } catch (e) { res = null; }
          if (res && res.ok && res.dataUri) resolve(res.dataUri);
          else reject(new Error((res && res.error) || "camera-capture-failed"));
        };
        try { B.cameraCapture(name); } catch (e) { reject(e); }
      });
    },

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
