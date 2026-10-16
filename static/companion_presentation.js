/**
 * companion_presentation.js — presentation-mode coordinator for the ONE
 * companion.
 *
 * The companion has two presentations of the same character, session and brain:
 *   * EMBEDDED — visible inside the ValleyMind interface (the normal position)
 *   * EXTERNAL — the floating companion window, shown over other apps
 *
 * The single Settings toggle "Show companion outside ValleyMind" switches
 * between them. This module owns the invariant that exactly ONE presentation is
 * visible and that a switch never leaves the companion invisible.
 *
 * It is deliberately PURE: it does not touch the DOM, the network, the
 * companion engine or the desktop bridge. It only holds the one piece of state
 * the toggle flow needs — "did WE hide the embedded companion?" — and calls
 * injected effects. That keeps it identical for the Windows app (IPC), the
 * browser/controller path and a future Android overlay, and lets it be unit
 * tested in Node without a DOM.
 *
 * Contract used by static/settings.js:
 *   deps.getEmbedded() -> an object { isVisible(), hide(), show(), getMode() }
 *                         or null when the companion engine is absent.
 *   coordinator.externalConfirmedVisible() — call ONLY after external
 *       visibility has been CONFIRMED true. Hides the embedded companion so
 *       only the floating window remains; safe to call repeatedly.
 *   coordinator.externalHidden() — call when external visibility is confirmed
 *       false, when activation failed, when the window closed, or when the
 *       helper is unreachable. Restores the embedded companion we hid.
 *   coordinator.reconcile(externalVisible) — reconcile to a known external
 *       state (panel open, IPC broadcast, controller poll).
 *   coordinator.embeddedHiddenByExternal() — diagnostic.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.VmCompanionPresentation = api;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  "use strict";

  function createCoordinator(deps) {
    deps = deps || {};
    var hidEmbedded = false;

    function embedded() {
      return (typeof deps.getEmbedded === "function") ? deps.getEmbedded() : null;
    }

    // The embedded companion is "the active presentation" when it is actually
    // on screen, or when the VRM mode is the selected one (it may just not have
    // painted yet). We only ever hide the VRM presentation, never the legacy
    // Cloud character.
    function embeddedIsVrm(vm) {
      if (!vm) return false;
      if (typeof vm.getMode !== "function") return true;
      try { return vm.getMode() === "vrm"; } catch (e) { return true; }
    }

    // External confirmed VISIBLE: hide the embedded companion so exactly one
    // companion shows. Remembers that WE hid it so the hide is reversible.
    function externalConfirmedVisible() {
      var vm = embedded();
      if (!hidEmbedded && embeddedIsVrm(vm) && typeof vm.hide === "function") {
        try { vm.hide(); } catch (e) { return hidEmbedded; }
        hidEmbedded = true;
      }
      return hidEmbedded;
    }

    // External hidden (confirmed off / activation failed / window closed /
    // helper unreachable): restore the embedded companion we hid. show() is
    // best-effort and itself respects the user's explicit "Show companion"
    // preference, so a restored companion is never forced against their choice.
    function externalHidden() {
      if (!hidEmbedded) return false;
      hidEmbedded = false;
      var vm = embedded();
      if (vm && typeof vm.show === "function") {
        try { vm.show(); } catch (e) { /* show() is best-effort */ }
      }
      return true;
    }

    // Reconcile to a KNOWN external state (panel open, IPC broadcast, poll).
    function reconcile(externalVisible) {
      return externalVisible ? externalConfirmedVisible() : externalHidden();
    }

    return {
      externalConfirmedVisible: externalConfirmedVisible,
      externalHidden: externalHidden,
      reconcile: reconcile,
      embeddedHiddenByExternal: function () { return hidEmbedded; },
    };
  }

  return { createCoordinator: createCoordinator };
});
