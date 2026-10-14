"use strict";

/**
 * main.js -- Electron main process for the ValleyMind Windows desktop companion.
 *
 * Responsibilities (and nothing about the AI):
 *   1. Launch the EXISTING Flask backend (python app.py) as a child process.
 *   2. Wait for it to answer, then open a transparent, frameless, always-on-top
 *      window that loads /desktop from that same local server (same-origin, so
 *      the existing session cookie + /api/cloud/chat + /api/tts are reused
 *      unchanged; API keys never leave the server).
 *   3. Show the character's body without requiring a login; the login window is
 *      opened only when the AI is used (i.e. a request returns 401/403).
 *   4. Provide window shell features: drag (CSS app-region), screen-boundary
 *      clamping, position persistence, always-on-top toggle, tray, clean quit.
 *   5. Follow the background controller's desired-visibility state (poll), so
 *      the website's Settings toggle can show/hide this window. This app is
 *      launched on demand by desktop/controller.py; it does not own the port.
 *   6. Single-app mode (`--app`): also host the main ValleyMind window and let
 *      its Settings toggle drive this window directly over Electron IPC, so the
 *      companion needs no separate controller when running inside the app.
 *
 * The companion is NOT a browser tab: Electron owns a real always-on-top OS
 * window, so the character stays visible over other applications.
 */

const { app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage, session } = require("electron");
const path = require("path");
const fs = require("fs");
const net = require("net");
const http = require("http");
const { spawn } = require("child_process");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const TRAY_ICON = path.join(__dirname, "tray.png");
// The always-on background controller (desktop/controller.py) owns this stable
// loopback endpoint. The website talks to it; this app polls it for the desired
// visibility and reports its own local changes back. Loopback is a secure
// context, so the same mechanism works from the local site and the hosted site.
const CONTROL_URL = "http://127.0.0.1:8737";

// Single-app mode. Run as `electron . --app` and the shell becomes the ONE
// ValleyMind Windows app: a normal window hosting the web UI plus the floating
// companion window it controls directly over IPC. Without the flag the shell
// keeps its original companion-only behaviour (launched by the controller).
const APP_MODE = process.argv.includes("--app");

let serverProc = null;
let serverPort = 0;
let companionVisible = true;
let controllerSeen = false;
let desiredPollTimer = null;
let lastLocalChange = 0;
let mainWin = null;
let appWin = null;
let loginWin = null;
let tray = null;
let quitting = false;
let alwaysOnTop = true;
let lastBroadcast = null;

// ── Backend lifecycle ───────────────────────────────────────────────────────
function findPython() {
  const isWin = process.platform === "win32";
  const candidates = [
    process.env.VM_PYTHON,
    path.join(PROJECT_ROOT, "env311", "Scripts", isWin ? "python.exe" : "python"),
    path.join(PROJECT_ROOT, "env", "Scripts", isWin ? "python.exe" : "python"),
    "python3",
    "python",
  ].filter(Boolean);
  for (const c of candidates) {
    if (c === "python" || c === "python3") return c;
    try {
      if (fs.existsSync(c)) return c;
    } catch (e) {
      /* ignore */
    }
  }
  return "python";
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

function startServer(port) {
  const py = findPython();
  console.log(`[desktop] starting backend: ${py} app.py  (PORT=${port})`);
  serverProc = spawn(py, ["app.py"], {
    cwd: PROJECT_ROOT,
    env: Object.assign({}, process.env, { PORT: String(port), FLASK_DEBUG: "false" }),
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  serverProc.stdout.on("data", (d) => process.stdout.write(`[server] ${d}`));
  serverProc.stderr.on("data", (d) => process.stderr.write(`[server] ${d}`));
  serverProc.on("exit", (code) => {
    console.log(`[desktop] backend exited (${code})`);
    serverProc = null;
    if (!quitting) app.quit();
  });
  serverProc.on("error", (err) => {
    console.error(`[desktop] failed to start backend: ${err.message}`);
  });
}

function waitForServer(port, timeoutMs = 90000) {
  const url = `http://127.0.0.1:${port}/auth/status`;
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      fetch(url)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then(() => resolve(true))
        .catch(() => {
          if (Date.now() - start > timeoutMs) reject(new Error("backend did not become ready in time"));
          else setTimeout(tick, 400);
        });
    };
    tick();
  });
}

function stopServer() {
  if (!serverProc) return;
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", String(serverProc.pid), "/f", "/t"], { windowsHide: true });
    } else {
      serverProc.kill("SIGTERM");
    }
  } catch (e) {
    /* best effort */
  }
  serverProc = null;
}

// ── Desktop visibility bridge (client half) ──────────────────────────────────
// This app does NOT own the control port. The background controller
// (desktop/controller.py) does, because it must already be listening when the
// user flips the website toggle — a web page cannot launch an .exe. Here we:
//   * poll GET /desired (desired state + liveness heartbeat), and
//   * POST /sync when the user changes visibility locally (tray / relaunch)
// so the website toggle and the window never disagree. If no controller is
// running (e.g. the app was started by hand), we simply keep the window
// visible and nothing else changes.
function applyVisibility() {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (companionVisible) showCompanion();
  else if (mainWin.isVisible()) mainWin.hide();
  if (lastBroadcast !== companionVisible) {
    lastBroadcast = companionVisible;
    broadcastVisibility();
  }
}

// Tell every open window (the main app window, the companion surface) the
// current visibility so the Settings toggle always reflects reality. Used by
// the in-shell IPC control path, independent of any HTTP controller.
function broadcastVisibility() {
  const v = !!companionVisible;
  for (const w of BrowserWindow.getAllWindows()) {
    if (w && !w.isDestroyed()) w.webContents.send("desktop:visibility", v);
  }
}

// The in-shell control entry point: show/hide the floating window and, if a
// background controller happens to be running (browser/hosted case), keep it in
// sync. No controller is required for this to work.
function setCompanionVisible(on) {
  companionVisible = !!on;
  applyVisibility();
  syncDesiredState(companionVisible);
}

function pollDesiredState() {
  const req = http.get(`${CONTROL_URL}/desired`, { timeout: 1500 }, (res) => {
    let raw = "";
    res.on("data", (c) => { raw += c; if (raw.length > 4096) req.destroy(); });
    res.on("end", () => {
      let j = null;
      try { j = JSON.parse(raw); } catch (e) { /* ignore */ }
      if (res.statusCode === 200 && j && typeof j.visible === "boolean") {
        controllerSeen = true;
        // A very recent local change wins for a moment so the controller's
        // slightly older value can't immediately undo the user's tray toggle.
        if (Date.now() - lastLocalChange > 1500) {
          companionVisible = j.visible;
          applyVisibility();
        }
      }
    });
  });
  req.on("timeout", () => req.destroy());
  req.on("error", () => { /* no controller — leave visibility as-is */ });
}

function startDesiredPolling() {
  if (desiredPollTimer) return;
  pollDesiredState();
  desiredPollTimer = setInterval(pollDesiredState, 700);
}

function stopDesiredPolling() {
  if (desiredPollTimer) {
    clearInterval(desiredPollTimer);
    desiredPollTimer = null;
  }
}

// Best-effort: tell the controller about a locally-initiated change so the
// website toggle stays in sync. Silent when no controller is running.
function syncDesiredState(visible) {
  lastLocalChange = Date.now();
  const payload = JSON.stringify({ visible: !!visible });
  const req = http.request(
    `${CONTROL_URL}/sync`,
    {
      method: "POST",
      timeout: 1500,
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) },
    },
    (res) => res.resume()
  );
  req.on("timeout", () => req.destroy());
  req.on("error", () => { /* no controller */ });
  req.write(payload);
  req.end();
}

// ── Window geometry ─────────────────────────────────────────────────────────
function stateFile() {
  return path.join(app.getPath("userData"), "desktop-window.json");
}

function defaultBounds() {
  const wa = screen.getPrimaryDisplay().workArea;
  const width = 360;
  const height = 520;
  return { width, height, x: wa.x + wa.width - width - 24, y: wa.y + wa.height - height - 24 };
}

function loadBounds() {
  try {
    const raw = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    if (raw && Number.isFinite(raw.width) && Number.isFinite(raw.height)) return raw;
  } catch (e) {
    /* no saved state */
  }
  return defaultBounds();
}

function clampBounds(b) {
  const width = Math.max(220, Math.min(b.width, screen.getPrimaryDisplay().workArea.width));
  const height = Math.max(300, Math.min(b.height, screen.getPrimaryDisplay().workArea.height));
  const probe = { x: b.x, y: b.y, width, height };
  const wa = screen.getDisplayMatching(probe).workArea;
  const x = Math.min(Math.max(b.x, wa.x), wa.x + wa.width - width);
  const y = Math.min(Math.max(b.y, wa.y), wa.y + wa.height - height);
  return { x, y, width, height };
}

let savingBounds = false;
function persistBounds() {
  if (!mainWin || mainWin.isDestroyed() || savingBounds) return;
  savingBounds = true;
  try {
    const clamped = clampBounds(mainWin.getBounds());
    const cur = mainWin.getBounds();
    if (clamped.x !== cur.x || clamped.y !== cur.y) mainWin.setBounds(clamped);
    fs.writeFileSync(stateFile(), JSON.stringify(clamped));
  } catch (e) {
    /* ignore */
  }
  savingBounds = false;
}

// ── Windows ─────────────────────────────────────────────────────────────────
function createCompanionWindow() {
  const bounds = clampBounds(loadBounds());
  mainWin = new BrowserWindow(
    Object.assign({}, bounds, {
      minWidth: 220,
      minHeight: 300,
      transparent: true,
      frame: false,
      resizable: true,
      hasShadow: false,
      alwaysOnTop: alwaysOnTop,
      fullscreenable: false,
      maximizable: false,
      show: false,
      backgroundColor: "#00000000",
      title: "ValleyMind Companion",
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
        backgroundThrottling: false,
      },
    })
  );

  mainWin.setAlwaysOnTop(alwaysOnTop, "screen-saver");
  mainWin.setMenuBarVisibility(false);
  mainWin.loadURL(`http://127.0.0.1:${serverPort}/desktop`);

  mainWin.on("moved", persistBounds);
  mainWin.on("resized", persistBounds);
  mainWin.on("closed", () => {
    mainWin = null;
  });
}

function showCompanion() {
  if (mainWin && !mainWin.isDestroyed()) {
    mainWin.show();
    mainWin.focus();
  }
}

// The main ValleyMind window (single-app mode only). It loads the SAME web app
// (/), so every existing feature, the session cookie and the in-app companion
// are unchanged. Its Settings toggle drives the floating window over IPC.
function createAppWindow() {
  if (appWin && !appWin.isDestroyed()) {
    appWin.focus();
    return;
  }
  appWin = new BrowserWindow({
    width: 1200,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    title: "ValleyMind",
    backgroundColor: "#0b141a",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  appWin.setMenuBarVisibility(false);
  appWin.loadURL(`http://127.0.0.1:${serverPort}/`);
  appWin.once("ready-to-show", () => appWin && appWin.show());
  appWin.on("closed", () => {
    appWin = null;
  });
}

function openLogin() {
  if (loginWin && !loginWin.isDestroyed()) {
    loginWin.focus();
    return;
  }
  loginWin = new BrowserWindow({
    width: 460,
    height: 640,
    minWidth: 360,
    minHeight: 520,
    title: "Sign in to ValleyMind",
    backgroundColor: "#0b0d14",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  loginWin.loadURL(`http://127.0.0.1:${serverPort}/?login=1`);
  loginWin.on("closed", () => {
    loginWin = null;
    // Logged-out exit: if the companion never authenticated, nothing to show.
    if (!quitting && (!mainWin || !mainWin.isVisible())) {
      // Give the companion a moment; if still hidden, quit cleanly.
      setTimeout(() => {
        if (!quitting && mainWin && !mainWin.isVisible()) app.quit();
      }, 400);
    }
  });
}

function createTray() {
  let icon = nativeImage.createEmpty();
  try {
    if (fs.existsSync(TRAY_ICON)) icon = nativeImage.createFromPath(TRAY_ICON);
  } catch (e) {
    /* empty icon is acceptable */
  }
  tray = new Tray(icon);
  tray.setToolTip("ValleyMind Companion");
  const rebuild = () =>
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Show / Hide", click: toggleCompanion },
        { label: "Pause movement", click: () => mainWin && mainWin.webContents.send("desktop:pause-movement") },
        { label: "Reset position", click: resetPosition },
        { type: "separator" },
        {
          label: "Always on top",
          type: "checkbox",
          checked: alwaysOnTop,
          click: (mi) => {
            alwaysOnTop = mi.checked;
            if (mainWin && !mainWin.isDestroyed()) mainWin.setAlwaysOnTop(alwaysOnTop, "screen-saver");
            rebuild();
          },
        },
        { type: "separator" },
        { label: "Quit ValleyMind Companion", click: () => app.quit() },
      ])
    );
  rebuild();
  tray.on("click", toggleCompanion);
}

function toggleCompanion() {
  if (!mainWin || mainWin.isDestroyed()) return;
  setCompanionVisible(!companionVisible);
}

function resetPosition() {
  if (!mainWin || mainWin.isDestroyed()) return;
  const b = defaultBounds();
  mainWin.setBounds(b);
  try {
    fs.writeFileSync(stateFile(), JSON.stringify(b));
  } catch (e) {
    /* ignore */
  }
}

// ── Security: microphone only, only for our own local origin ────────────────
function hardenSession() {
  const s = session.defaultSession;
  s.setPermissionRequestHandler((wc, permission, callback) => {
    const url = (wc && wc.getURL && wc.getURL()) || "";
    const local = url.startsWith(`http://127.0.0.1:${serverPort}`) || url.startsWith(`http://localhost:${serverPort}`);
    if (permission === "media" && local) return callback(true);
    if (permission === "fullscreen" || permission === "clipboard-sanitized-write") return callback(true);
    return callback(false);
  });
  s.setPermissionCheckHandler((wc, permission) => permission === "media" || permission === "clipboard-sanitized-write");
}

// ── IPC ─────────────────────────────────────────────────────────────────────
function wireIpc() {
  ipcMain.on("desktop:ready", () => applyVisibility());
  ipcMain.on("desktop:open-login", () => openLogin());
  ipcMain.on("auth:login-done", () => {
    if (loginWin && !loginWin.isDestroyed()) loginWin.close();
    if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send("auth:ok");
    showCompanion();
  });
  ipcMain.on("desktop:quit", () => app.quit());
  ipcMain.on("desktop:reset-position", resetPosition);
  ipcMain.on("desktop:toggle-always-on-top", () => {
    alwaysOnTop = !alwaysOnTop;
    if (mainWin && !mainWin.isDestroyed()) mainWin.setAlwaysOnTop(alwaysOnTop, "screen-saver");
  });
  ipcMain.on("desktop:set-ignore-mouse", (_e, on) => {
    if (mainWin && !mainWin.isDestroyed()) mainWin.setIgnoreMouseEvents(!!on, { forward: true });
  });
  // In-shell control of the floating companion (no controller, no port).
  ipcMain.handle("desktop:get-visible", () => !!companionVisible);
  ipcMain.on("desktop:set-visible", (_e, on) => setCompanionVisible(!!on));
}

// ── Boot ────────────────────────────────────────────────────────────────────
async function bootstrap() {
  wireIpc();
  try {
    serverPort = await freePort();
    startServer(serverPort);
    await waitForServer(serverPort);
    hardenSession();
    // In single-app mode the floating companion starts hidden: the user opts in
    // from Settings (the in-app companion is always available regardless).
    if (APP_MODE) companionVisible = false;
    createCompanionWindow();
    createTray();
    if (APP_MODE) createAppWindow();
    // Start following the controller only once the window exists, so the
    // desired-state poll (which is also the liveness heartbeat) begins when the
    // companion is actually ready.
    startDesiredPolling();
  } catch (err) {
    console.error(`[desktop] bootstrap failed: ${err.message}`);
    const { dialog } = require("electron");
    try {
      dialog.showErrorBox(
        "ValleyMind Companion could not start",
        `The local backend did not start.\n\n${err.message}\n\n` +
          "Check that Python and the project dependencies are installed, then try again."
      );
    } catch (e) {
      /* ignore */
    }
    app.quit();
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (appWin && !appWin.isDestroyed()) appWin.focus();
    setCompanionVisible(true);
  });
  app.whenReady().then(bootstrap);
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  stopDesiredPolling();
  stopServer();
  if (tray) {
    try {
      tray.destroy();
    } catch (e) {
      /* ignore */
    }
  }
});
