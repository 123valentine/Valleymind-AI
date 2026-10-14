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
 *   3. Show the app's own login window once if there is no session.
 *   4. Provide window shell features: drag (CSS app-region), screen-boundary
 *      clamping, position persistence, always-on-top toggle, tray, clean quit.
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
// Fixed loopback port the website's Settings toggle talks to. Loopback is a
// secure context, so this works from the local site and the hosted site alike.
const CONTROL_PORT = 8737;

let serverProc = null;
let serverPort = 0;
let controlServer = null;
let companionVisible = true;
let mainWin = null;
let loginWin = null;
let tray = null;
let quitting = false;
let alwaysOnTop = true;

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

// ── Desktop visibility bridge ────────────────────────────────────────────────
// The website's Settings toggle ("Show companion outside ValleyMind") talks to
// this small loopback-only HTTP server. It ONLY shows / hides the existing
// companion window; it never starts a second process or a duplicate character.
function applyVisibility() {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (companionVisible) showCompanion();
  else if (mainWin.isVisible()) mainWin.hide();
}

function startControlServer() {
  controlServer = http.createServer((req, res) => {
    res.setHeader("Access-Control-Allow-Origin", req.headers.origin || "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    res.setHeader("Access-Control-Allow-Private-Network", "true");
    res.setHeader("Vary", "Origin");

    if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

    const url = (req.url || "/").split("?")[0];
    if (req.method === "GET" && url === "/state") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ running: true, visible: companionVisible }));
      return;
    }
    if (req.method === "POST" && url === "/visibility") {
      let body = "";
      req.on("data", (chunk) => { body += chunk; if (body.length > 4096) req.destroy(); });
      req.on("end", () => {
        let visible = true;
        try { visible = JSON.parse(body || "{}").visible !== false; } catch (e) { /* default: show */ }
        companionVisible = !!visible;
        applyVisibility();
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, visible: companionVisible }));
      });
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: "not_found" }));
  });
  controlServer.on("error", (err) => {
    console.warn(`[desktop] visibility bridge unavailable on ${CONTROL_PORT}: ${err.message}`);
  });
  controlServer.listen(CONTROL_PORT, "127.0.0.1", () => {
    console.log(`[desktop] visibility bridge on http://127.0.0.1:${CONTROL_PORT}`);
  });
}

function stopControlServer() {
  if (!controlServer) return;
  try { controlServer.close(); } catch (e) { /* ignore */ }
  controlServer = null;
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
  loginWin.loadURL(`http://127.0.0.1:${serverPort}/`);
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
  if (mainWin.isVisible()) { companionVisible = false; mainWin.hide(); }
  else { companionVisible = true; showCompanion(); }
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
}

// ── Boot ────────────────────────────────────────────────────────────────────
async function bootstrap() {
  wireIpc();
  try {
    serverPort = await freePort();
    startServer(serverPort);
    await waitForServer(serverPort);
    hardenSession();
    createCompanionWindow();
    createTray();
    startControlServer();
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
  app.on("second-instance", () => { companionVisible = true; showCompanion(); });
  app.whenReady().then(bootstrap);
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  stopControlServer();
  stopServer();
  if (tray) {
    try {
      tray.destroy();
    } catch (e) {
      /* ignore */
    }
  }
});
