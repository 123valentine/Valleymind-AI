// Pure-node test runner for the Android companion bridge wiring in
// static/settings.js. No DOM: loads the REAL settings.js + the REAL
// companion_presentation.js into a VM with a minimal window/document/navigator
// and a stubbed window.vmCompanionAndroid, then exercises the exact control flow
// the Settings toggle uses. Prints a JSON summary and exits non-zero on failure.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const SETTINGS_SRC = fs.readFileSync(path.join(ROOT, "static", "settings.js"), "utf8");
const PRESENTATION_SRC = fs.readFileSync(path.join(ROOT, "static", "companion_presentation.js"), "utf8");

const failures = [];
let passes = 0;
function check(cond, name) { if (cond) passes++; else failures.push(name); }
function eq(got, want, name) { check(got === want, name + ` (got ${JSON.stringify(got)} expected ${JSON.stringify(want)})`); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fakeVM() {
  return {
    _v: true,
    _mode: "vrm",
    isVisible() { return this._v; },
    getMode() { return this._mode; },
    hide() { this._v = false; },
    show() { this._v = true; },
  };
}

function makeEnv(opts) {
  opts = opts || {};
  const dom = {};
  const windowObj = { vmVrmCompanion: fakeVM() };
  if (opts.desktop) windowObj.vmDesktop = opts.desktop;
  if (opts.android) windowObj.vmCompanionAndroid = opts.android;

  const documentObj = {
    getElementById(id) {
      if (id === "vmSetDesktopVisible" || id === "vmCompanionDesktopStatus") {
        if (!dom[id]) dom[id] = { checked: false, textContent: "", style: {} };
        return dom[id];
      }
      return null;
    },
  };

  const sandbox = {
    window: windowObj,
    document: documentObj,
    navigator: { userAgent: opts.ua || "" },
    location: { origin: "https://valleymind-ai.onrender.com" },
    console: { warn() {}, log() {} },
    setTimeout,
    clearTimeout,
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(PRESENTATION_SRC, ctx, { filename: "companion_presentation.js" });
  vm.runInContext(SETTINGS_SRC, ctx, { filename: "settings.js" });

  return {
    window: windowObj,
    dom,
    embedded: () => windowObj.vmVrmCompanion,
    toggle: () => dom["vmSetDesktopVisible"].checked,
    status: () => dom["vmCompanionDesktopStatus"].textContent,
    run: (code) => vm.runInContext(code, ctx),
    control: () => vm.runInContext("vmCompanionControl()", ctx),
    set: (on) => vm.runInContext(`vmCompanionSetDesktopVisible(${on ? "true" : "false"})`, ctx),
    refresh: () => vm.runInContext("vmCompanionRefreshDesktopState()", ctx),
    limitation: () => vm.runInContext("vmCompanionLimitationMessage()", ctx),
  };
}

async function main() {
  // ── 1. Android, no bridge -> honest "needs the Android app" limitation ───
  {
    const env = makeEnv({ ua: "Android" });
    eq(env.control().kind, "unsupported", "android without a bridge is unsupported");
    check(/ValleyMind Android app/.test(env.limitation()), "limitation names the ValleyMind Android app");
  }

  // ── 2. Android bridge present -> android-overlay adapter selected ────────
  {
    const env = makeEnv({ ua: "Android", android: { setVisible: () => Promise.resolve(true) } });
    const c = env.control();
    eq(c.kind, "android-overlay", "a vmCompanionAndroid bridge selects android-overlay");
    eq(c.subscribe, null, "no onCompanionVisibility -> no subscribe");
    eq(c.get, null, "no getVisible -> no get");
  }

  // ── 3. Permission missing -> never ON, open settings, keep embedded ──────
  {
    let requested = 0;
    const env = makeEnv({
      ua: "Android",
      android: {
        setVisible: () => Promise.resolve(true),
        hasPermission: () => false,
        requestPermission: () => { requested++; },
      },
    });
    env.set(true);
    await sleep(20);
    eq(requested, 1, "missing overlay permission opens Android settings once");
    eq(env.toggle(), false, "switch stays OFF without permission");
    eq(env.embedded()._v, true, "embedded companion stays visible without permission");
    check(/permission/i.test(env.status()), "an honest permission message is shown");
  }

  // ── 4. Permission granted -> ON hides embedded, OFF restores it ──────────
  {
    const env = makeEnv({
      ua: "Android",
      android: { setVisible: (on) => Promise.resolve(!!on), hasPermission: () => true, getVisible: () => false, onCompanionVisibility: () => {} },
    });
    env.set(true);
    await sleep(20);
    eq(env.embedded()._v, false, "confirmed ON hides the embedded companion");
    eq(env.toggle(), true, "switch reflects confirmed ON");
    env.set(false);
    await sleep(20);
    eq(env.embedded()._v, true, "OFF restores the embedded companion");
    eq(env.toggle(), false, "switch reflects confirmed OFF");
  }

  // ── 5. Activation failure (reject) -> embedded visible, switch OFF ───────
  {
    const env = makeEnv({
      ua: "Android",
      android: { setVisible: () => Promise.reject(new Error("service-start-failed")), hasPermission: () => true },
    });
    env.set(true);
    await sleep(20);
    eq(env.embedded()._v, true, "a rejected activation keeps the embedded companion visible");
    eq(env.toggle(), false, "a rejected activation keeps the switch OFF");
  }

  // ── 6. Bridge reports ACTUAL false when asked true (revoked) ─────────────
  {
    const env = makeEnv({ ua: "Android", android: { setVisible: () => Promise.resolve(false), hasPermission: () => true } });
    env.set(true);
    await sleep(20);
    eq(env.embedded()._v, true, "actual=false keeps the embedded companion visible");
    eq(env.toggle(), false, "switch follows ACTUAL state, not the request");
  }

  // ── 7. Rapid ON->OFF: a slow stale response cannot win ───────────────────
  {
    const env = makeEnv({
      ua: "Android",
      android: { setVisible: (on) => new Promise((res) => setTimeout(() => res(!!on), on ? 60 : 5)), hasPermission: () => true },
    });
    env.set(true);   // slow, resolves true later
    env.set(false);  // fast, resolves false now
    await sleep(140);
    eq(env.toggle(), false, "the newest toggle (OFF) wins over a stale slow ON");
    eq(env.embedded()._v, true, "embedded companion matches the final OFF");
  }

  // ── 8. Native broadcast + get reconciles the real state ──────────────────
  {
    let captured = null;
    const env = makeEnv({
      ua: "Android",
      android: {
        setVisible: () => Promise.resolve(true),
        hasPermission: () => true,
        getVisible: () => true,
        onCompanionVisibility: (cb) => { captured = cb; },
      },
    });
    env.refresh();
    await sleep(20);
    check(typeof captured === "function", "refresh subscribes to native visibility changes");
    eq(env.toggle(), true, "initial get() shows the confirmed ON");
    eq(env.embedded()._v, false, "confirmed ON hides the embedded companion");
    if (typeof captured === "function") {
      captured(false);
      await sleep(5);
      eq(env.toggle(), false, "a native OFF broadcast flips the switch");
      eq(env.embedded()._v, true, "a native OFF broadcast restores the embedded companion");
    }
  }

  // ── 9. Windows IPC bridge still selected as windows-app (regression) ─────
  {
    const env = makeEnv({
      ua: "Windows",
      desktop: { isDesktop: true, setCompanionVisible: () => Promise.resolve(true), getCompanionVisible: () => false, onCompanionVisibility: () => {} },
    });
    eq(env.control().kind, "windows-app", "the Windows IPC bridge still wins when present");
  }

  // ── 10. Desktop, no bridge -> controller fallback remains ────────────────
  {
    eq(makeEnv({ ua: "Windows" }).control().kind, "controller", "desktop without a bridge falls back to the controller");
  }

  // ── 11. Android bridge WITHOUT hasPermission: no gate, still works ───────
  {
    const env = makeEnv({ ua: "Android", android: { setVisible: (on) => Promise.resolve(!!on) } });
    const c = env.control();
    eq(c.kind, "android-overlay", "android bridge without hasPermission is still android-overlay");
    eq(c.needsPermission, null, "no hasPermission -> no permission gate");
    env.set(true);
    await sleep(20);
    eq(env.toggle(), true, "android bridge without hasPermission can turn ON");
  }

  // ── 12. A bridge without setVisible is NOT a usable adapter ──────────────
  {
    eq(makeEnv({ ua: "Android", android: { hasPermission: () => true } }).control().kind, "unsupported",
       "a bridge without setVisible is not a usable adapter");
  }

  console.log(JSON.stringify({ passed: passes, failed: failures }));
  if (failures.length) process.exit(1);
}

main();
