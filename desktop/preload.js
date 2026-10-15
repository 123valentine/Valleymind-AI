"use strict";

/**
 * preload.js -- the ONLY bridge between the transparent renderer and Electron.
 *
 * contextIsolation is on and nodeIntegration is off, so the page never touches
 * Node. Everything below is a narrow, explicit capability channel; there is no
 * generic "invoke arbitrary channel" escape hatch.
 *
 * Two window roles share this preload:
 *   - the login window  (pathname "/", with ?login=1) polls /auth/status until
 *     the user has signed in, then tells the main process to reveal the
 *     companion. In single-app mode the main app window also loads "/" (without
 *     ?login=1) and must NOT trigger that handshake, so the query flag gates it.
 *   - the companion     (pathname "/desktop") asks the main process to open the
 *     login window when it finds no session, and reports readiness.
 */

const { contextBridge, ipcRenderer } = require("electron");

const api = {
  isDesktop: true,
  // Companion -> main
  ready: () => ipcRenderer.send("desktop:ready"),
  openLogin: () => ipcRenderer.send("desktop:open-login"),
  quit: () => ipcRenderer.send("desktop:quit"),
  resetPosition: () => ipcRenderer.send("desktop:reset-position"),
  toggleAlwaysOnTop: () => ipcRenderer.send("desktop:toggle-always-on-top"),
  setIgnoreMouse: (on) => ipcRenderer.send("desktop:set-ignore-mouse", !!on),
  // In-shell control of the floating companion (single-app mode). Works with no
  // background controller: the Settings toggle drives the window directly. The
  // returned promise resolves to the ACTUAL resulting visibility so the toggle
  // never claims the window is on screen when it is not.
  setCompanionVisible: (on) => ipcRenderer.invoke("desktop:set-visible", !!on),
  getCompanionVisible: () => ipcRenderer.invoke("desktop:get-visible"),
  // main -> companion
  onAuthOk: (cb) => {
    if (typeof cb === "function") ipcRenderer.on("auth:ok", () => cb());
  },
  onPauseMovement: (cb) => {
    if (typeof cb === "function") ipcRenderer.on("desktop:pause-movement", () => cb());
  },
  onCompanionVisibility: (cb) => {
    if (typeof cb === "function") ipcRenderer.on("desktop:visibility", (_e, on) => cb(!!on));
  },
};

contextBridge.exposeInMainWorld("vmDesktop", api);

// The login window ("/?login=1") watches for a completed sign-in and reports it
// once. The main app window loads "/" too but without ?login=1, so it is skipped.
if (
  typeof window !== "undefined" &&
  window.location &&
  window.location.pathname === "/" &&
  /[?&]login=1(?:&|$)/.test(window.location.search)
) {
  let done = false;
  const iv = setInterval(async () => {
    if (done) return;
    try {
      const r = await fetch("/auth/status", { credentials: "include" });
      const j = await r.json();
      if (j && j.authenticated) {
        done = true;
        clearInterval(iv);
        ipcRenderer.send("auth:login-done");
      }
    } catch (e) {
      /* server not ready yet / transient — keep polling */
    }
  }, 1500);
}
