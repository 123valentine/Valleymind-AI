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
    onCompanionVisibility: function (cb) { if (typeof cb === "function") { listeners.push(cb); } }
  };

  try { B.notifyBridgeReady(); } catch (e) { /* ignore */ }
})();
"""
}
