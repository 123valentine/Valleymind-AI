"use strict";

/**
 * preload.js -- the ONLY bridge between the transparent renderer and Electron.
 *
 * contextIsolation is on and nodeIntegration is off, so the page never touches
 * Node. Everything below is a narrow, explicit capability channel; there is no
 * generic "invoke arbitrary channel" escape hatch.
 *
 * Two window roles share this preload:
 *   - the login window  (pathname "/"      ) polls /auth/status until the user
 *     has signed in, then tells the main process to reveal the companion.
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
  // main -> companion
  onAuthOk: (cb) => {
    if (typeof cb === "function") ipcRenderer.on("auth:ok", () => cb());
  },
  onPauseMovement: (cb) => {
    if (typeof cb === "function") ipcRenderer.on("desktop:pause-movement", () => cb());
  },
};

contextBridge.exposeInMainWorld("vmDesktop", api);

// The login window ("/") watches for a completed sign-in and reports it once.
if (typeof window !== "undefined" && window.location && window.location.pathname === "/") {
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
