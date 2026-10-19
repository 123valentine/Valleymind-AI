"use strict";
/**
 * Deterministic regression test for the desktop desired-state poll.
 *
 * It loads the REAL desktop/main.js in a Node VM with a mocked `electron`
 * module and a REAL loopback controller stub on 127.0.0.1:8737, then drives the
 * exact sequence that used to revert the toggle:
 *
 *   OFF -> local ON (controller still reports the STALE value false) -> remain ON
 *   past the 1.5s polling grace window -> OFF -> a genuinely NEW controller
 *   request is still honored.
 *
 * It proves:
 *   - a stale/echoed controller value CANNOT undo a local ON, and
 *   - a real change from the controller (website toggle) IS applied.
 *
 * Usage: node tests/_desired_state_test.js [path-to-main.js]
 *   (default: desktop/main.js; pass an older copy to confirm the test catches
 *    the regression). Prints {"passed":N,"failed":[...]}.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const vm = require("vm");
const { execSync } = require("child_process");

const MAIN = process.argv[2] || path.join(__dirname, "..", "desktop", "main.js");

const results = { passed: 0, failed: [] };
function check(cond, msg) {
  if (cond) results.passed++;
  else results.failed.push(msg);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // ---- loopback controller stub (the app hardcodes http://127.0.0.1:8737) ----
  const ctrl = { desired: false, desiredHits: 0, visHits: 0, syncHits: 0, lastSync: null };
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const send = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
      if (req.url === "/desired") { ctrl.desiredHits++; return send(200, { visible: ctrl.desired, running: true }); }
      if (req.url === "/visibility" && req.method === "POST") {
        ctrl.visHits++;
        // Deliberately a STALE helper: record the POST but DO NOT change the
        // reported desired value (simulates a POST that never landed).
        return send(200, { ok: true, running: true, visible: ctrl.desired, starting: false });
      }
      if (req.url === "/sync" && req.method === "POST") {
        ctrl.syncHits++;
        try { ctrl.lastSync = JSON.parse(body || "{}").visible; } catch (e) { /* ignore */ }
        return send(200, { ok: true, visible: ctrl.desired });
      }
      if (req.url === "/state") return send(200, { running: true, visible: ctrl.desired, desired: ctrl.desired });
      return send(404, { ok: false });
    });
  });
  try {
    await new Promise((res, rej) => { server.once("error", rej); server.listen(8737, "127.0.0.1", res); });
  } catch (e) {
    results.failed.push("could not bind 127.0.0.1:8737 (" + e.message + ") - is a real controller running?");
    return;
  }

  // ---- mocked electron ----
  const sends = [];
  class FakeWin {
    constructor() {
      this._visible = false;
      this._destroyed = false;
      this.webContents = { send: (ch, v) => sends.push([ch, v]) };
      FakeWin.registry.push(this);
    }
    setAlwaysOnTop() {}
    setMenuBarVisibility() {}
    loadURL() {}
    on() {}
    focus() { this._visible = true; }
    show() { this._visible = true; }
    hide() { this._visible = false; }
    isDestroyed() { return this._destroyed; }
    isVisible() { return this._visible; }
    getBounds() { return { x: 0, y: 0, width: 360, height: 520 }; }
    setBounds() {}
    static getAllWindows() { return FakeWin.registry; }
  }
  FakeWin.registry = [];

  const electronMock = {
    app: {
      requestSingleInstanceLock: () => true,
      on() {},
      whenReady: () => new Promise(() => {}), // never resolves -> bootstrap() never runs
      getPath: () => os.tmpdir(),
      quit() {},
      getVersion: () => "0.0.0",
    },
    BrowserWindow: FakeWin,
    ipcMain: { on() {}, handle() {} },
    screen: {
      getPrimaryDisplay: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } }),
      getDisplayMatching: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1080 } }),
    },
    Tray: class { setToolTip() {} setContextMenu() {} on() {} destroy() {} },
    Menu: { buildFromTemplate: () => ({}), setApplicationMenu() {} },
    nativeImage: { createFromPath: () => ({}) },
    session: { defaultSession: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} } },
    dialog: { showErrorBox() {} },
  };

  const fakeRequire = (name) => (name === "electron" ? electronMock : require(name));

  const sandbox = {
    require: fakeRequire,
    console,
    process,
    Buffer,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    __dirname: path.dirname(MAIN),
    __filename: MAIN,
    __now: { value: 1000000 },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  // Controllable clock for the local-change grace guard.
  vm.runInContext("Date.now = function () { return __now.value; };", ctx);

  const run = (code) => vm.runInContext(code, ctx);
  const isVisible = () => vm.runInContext("companionIsVisible()", ctx);
  const advance = (ms) => { sandbox.__now.value += ms; };

  try {
    run(fs.readFileSync(MAIN, "utf8"));
  } catch (e) {
    results.failed.push("failed to evaluate " + MAIN + ": " + e.message);
    return;
  }
  check(typeof run("typeof pollDesiredState") === "string" && run("typeof pollDesiredState") === "function",
        "pollDesiredState should be defined by main.js");

  async function pollAndWait() {
    const before = ctrl.desiredHits;
    run("pollDesiredState();");
    const t0 = Date.now();
    while (ctrl.desiredHits <= before && Date.now() - t0 < 2000) await sleep(10);
    await sleep(60); // allow the res 'end' handler to run
  }

  // Start from the real initial state: window created, hidden.
  run("createCompanionWindow();");
  check(isVisible() === false, "companion starts hidden");
  check(run("typeof setCompanionVisible") === "function", "setCompanionVisible defined");

  // A first poll establishes the controller's baseline (false) -> hidden.
  advance(5000);
  await pollAndWait();
  check(isVisible() === false, "baseline poll keeps hidden");

  // ---- Phase 1: local ON while the controller keeps reporting STALE false ----
  advance(5000);
  run("setCompanionVisible(true);");
  check(isVisible() === true, "local ON shows the window immediately");
  await sleep(200); // let the async /visibility POST complete
  check(ctrl.visHits >= 1, "local ON published intent (/visibility) to the controller");

  let reverted = false;
  for (let i = 0; i < 4; i++) {
    advance(2000); // move well past the 1.5s grace window each time
    await pollAndWait();
    if (isVisible() !== true) reverted = true;
  }
  check(!reverted, "STALE controller value (false) NEVER reverts a local ON, even past the grace window");
  check(isVisible() === true, "window is still visible after stale polls");

  // ---- Phase 2: local OFF restores embedded; stale false keeps it hidden ----
  advance(5000);
  run("setCompanionVisible(false);");
  check(isVisible() === false, "local OFF hides the window");
  advance(2000);
  await pollAndWait();
  check(isVisible() === false, "stale false leaves the window hidden (consistent)");

  // ---- Phase 3: a GENUINELY NEW controller request is still honored ----
  advance(5000);
  ctrl.desired = true; // website toggle -> ON (new value vs last applied false)
  await pollAndWait();
  check(isVisible() === true, "GENUINELY NEW controller request ON is applied");

  advance(5000);
  ctrl.desired = false; // website toggle -> OFF (new value vs last applied true)
  await pollAndWait();
  check(isVisible() === false, "GENUINELY NEW controller request OFF is applied");

  // ---- Phase 4: a request landing DURING the grace window is not lost ----
  advance(5000);
  run("setCompanionVisible(true);");        // local ON (starts grace)
  check(isVisible() === true, "local ON (phase 4) shows the window");
  run("setCompanionVisible(false);");       // local OFF (fresh grace)
  check(isVisible() === false, "local OFF (phase 4) hides the window");
  ctrl.desired = true;                       // new request arrives DURING grace
  await pollAndWait();                       // blocked by grace, must not be forgotten
  check(isVisible() === false, "request during grace is not applied immediately");
  advance(2000);                             // grace elapses
  await pollAndWait();
  check(isVisible() === true, "request seen during grace is applied once the grace passes (not lost)");

  // ---- Broadcast consistency: desktop:visibility echoes the real state ----
  const visBroadcasts = sends.filter((s) => s[0] === "desktop:visibility").map((s) => s[1]);
  check(visBroadcasts.includes(true) && visBroadcasts.includes(false),
        "desktop:visibility broadcasts both ON and OFF to the Settings page");
  const lastBroadcast = visBroadcasts[visBroadcasts.length - 1];
  check(lastBroadcast === isVisible(), "the last broadcast matches the real window state");
  check(ctrl.lastSync === true, "the companion reported its REAL visible state to the controller (/sync)");

  server.close();
}

main()
  .catch((e) => { results.failed.push("FATAL " + (e && e.message)); })
  .finally(() => {
    process.stdout.write(JSON.stringify(results) + "\n");
    process.exit(0);
  });
setTimeout(() => { process.stdout.write(JSON.stringify(results) + "\n"); process.exit(0); }, 25000);
