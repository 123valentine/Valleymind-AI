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

  // Optional screen-context bridge. The helper is deliberately lazy for the
  // same reason as the rest of this file: AndroidBridgeJs may install the shim
  // after this script has already run.
  var SCREEN_PROMPT_KEY = "vmAccessibilityPrompted";
  function screenApi() {
    var native = current();
    return (native && native.screen && typeof native.screen === "object") ? native.screen : null;
  }
  function screenSupported() {
    var api = screenApi();
    return !!(api && typeof api.supported === "function" && api.supported());
  }
  function screenContext() {
    var api = screenApi();
    if (!api || typeof api.context !== "function") {
      return { ok: false, reason: "screen-context-unsupported" };
    }
    try { return api.context(); }
    catch (e) { return { ok: false, reason: "screen-context-error" }; }
  }
  function screenSettings() {
    var api = screenApi();
    if (api && typeof api.settings === "function") {
      try { api.settings(); } catch (e) { /* keep the current permission state */ }
    }
  }
  function promptScreenSettingsOnce() {
    try {
      var api = screenApi();
      // A browser (or an old shell) has no Accessibility settings to open.
      if (!api || typeof api.settings !== "function") return false;
      if (screenSupported()) return false;
      if (typeof localStorage !== "undefined" &&
          localStorage.getItem(SCREEN_PROMPT_KEY) === "1") return false;
      screenSettings();
      if (typeof localStorage !== "undefined") localStorage.setItem(SCREEN_PROMPT_KEY, "1");
      return true;
    } catch (e) { return false; }
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
    },
    screen: {
      supported: screenSupported,
      context: screenContext,
      settings: screenSettings,
      // Opens Accessibility settings at most once when the service is off.
      promptOnce: promptScreenSettingsOnce
    }
  };
})();
