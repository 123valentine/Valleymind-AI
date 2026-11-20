/*
 * Thin, backward-compatible façade over the native shell.
 *
 * window.vmNative is installed by the Android shim *after* page scripts may
 * have started, so this helper resolves it lazily on every call instead of
 * capturing it at load time. In a browser (or an older APK that only exposes
 * window.vmCompanionAndroid) every capability check simply reports false and
 * the page keeps its existing browser behaviour.
 */
(function () {
  "use strict";

  function current() {
    return (typeof window !== "undefined" && window.vmNative) ? window.vmNative : null;
  }

  function capabilities() {
    var native = current();
    if (!native || typeof native.capabilities !== "function") return [];
    try {
      var list = native.capabilities();
      return (list && typeof list.length === "number") ? list : [];
    } catch (e) {
      return [];
    }
  }

  function has(capability) {
    return capabilities().indexOf(String(capability)) !== -1;
  }

  function regionSupported() {
    var native = current();
    return !!native && has("overlayRegion") &&
      !!native.overlay && typeof native.overlay.setRegion === "function";
  }

  window.VMNative = {
    available: function () { return !!current(); },
    contract: function () {
      var native = current();
      return (native && native.contract) ? native.contract : null;
    },
    capabilities: capabilities,
    has: has,
    appInfo: function () {
      var native = current();
      if (!native || !native.app || typeof native.app.getInfo !== "function") return null;
      try { return native.app.getInfo(); } catch (e) { return null; }
    },
    overlayRegion: {
      supported: regionSupported,
      set: function (widthDp, heightDp) {
        if (!regionSupported()) return { ok: false, reason: "unsupported" };
        try {
          return current().overlay.setRegion(widthDp, heightDp);
        } catch (e) {
          return { ok: false, reason: "bridge-error" };
        }
      },
      get: function () {
        if (!regionSupported()) return { ok: false, reason: "unsupported" };
        try {
          return current().overlay.getRegion();
        } catch (e) {
          return { ok: false, reason: "bridge-error" };
        }
      }
    }
  };
})();
