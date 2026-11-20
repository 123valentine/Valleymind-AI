// Pure-node compatibility test for the versioned native façade.
//
// Extracts the REAL injected shim out of AndroidBridgeJs.kt and runs it against
// two shells:
//   * a current shell (contract + overlay region), and
//   * an older shell that only has the legacy methods.
// Newer pages must keep working on both, and the frozen
// window.vmCompanionAndroid surface must not change. Also exercises the
// static/native_bridge.js helper, including the load-order race (the helper may
// run before native injects the shim).
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const KT = path.join(ROOT, "android", "app", "src", "main", "java", "ai", "valleymind", "companion", "AndroidBridgeJs.kt");
const HELPER_PATH = path.join(ROOT, "static", "native_bridge.js");

const kt = fs.readFileSync(KT, "utf8");
const match = kt.match(/const val ANDROID_BRIDGE_JS: String = """([\s\S]*?)"""/);
if (!match) {
  console.error("could not extract ANDROID_BRIDGE_JS from AndroidBridgeJs.kt");
  process.exit(1);
}
const SRC = match[1];
const HELPER = fs.readFileSync(HELPER_PATH, "utf8");

const failures = [];
let passes = 0;
function check(cond, name) { if (cond) passes++; else failures.push(name); }
function eq(got, want, name) {
  check(got === want, name + ` (got ${JSON.stringify(got)} expected ${JSON.stringify(want)})`);
}

function makeEnv(bridge, opts) {
  opts = opts || {};
  const win = bridge ? { VmAndroidBridge: bridge } : {};
  const sandbox = {
    window: win,
    JSON,
    Date,
    Number,
    String,
    setTimeout,
    clearTimeout,
    console,
  };
  const ctx = vm.createContext(sandbox);
  if (opts.helperFirst) vm.runInContext(HELPER, ctx, { filename: "native_bridge.js" });
  if (bridge) vm.runInContext(SRC, ctx, { filename: "AndroidBridgeJs.kt" });
  if (!opts.helperFirst) vm.runInContext(HELPER, ctx, { filename: "native_bridge.js" });
  return { win, run: (code) => vm.runInContext(code, ctx) };
}

function baseBridge(over) {
  const calls = [];
  const bridge = Object.assign({
    isOverlaySupported: () => true,
    hasPermission: () => true,
    requestPermission: () => { calls.push(["requestPermission"]); },
    getStateJson: () => '{"granted":true,"running":true,"visible":true}',
    setVisible: (on) => { calls.push(["setVisible", on]); },
    notifyBridgeReady: () => { calls.push(["notifyBridgeReady"]); },
  }, over || {});
  bridge.calls = calls;
  return bridge;
}

function currentBridge(over) {
  return baseBridge(Object.assign({
    contractJson: () => JSON.stringify({
      version: 2,
      ready: true,
      shell: { versionCode: 7, versionName: "1.1.0" },
      capabilities: ["overlay", "overlayRegion", "tts", "asr", "files"],
    }),
    capabilitiesJson: () => '["overlay","overlayRegion"]',
    ttsSpeak: () => true,
    ttsStop: () => {},
    asrSupported: () => true,
    asrStart: () => true,
    asrStop: () => {},
    asrCancel: () => {},
    setOverlayRegion: (w, h) => {
      (over && over.regionCalls ? over.regionCalls : []).push([w, h]);
      return '{"ok":true,"widthDp":176,"heightDp":270,"clamped":false}';
    },
    getOverlayRegion: () => '{"ok":true,"widthDp":176,"heightDp":270}',
  }, over || {}));
}

// ── 1. current shell advertises the versioned contract ─────────────────────
{
  const regionCalls = [];
  const env = makeEnv(currentBridge({ regionCalls }));
  const native = env.win.vmNative;
  check(!!native, "the current shell installs window.vmNative");
  eq(native.contract.version, 2, "the contract version is reported");
  eq(native.contract.ready, true, "the shim reports itself ready");
  eq(native.isReady(), true, "isReady reflects the contract");
  eq(native.has("overlayRegion"), true, "overlayRegion is advertised");
  eq(native.has("not-a-capability"), false, "unknown capabilities are false");
  eq(native.app.getInfo().shell.versionName, "1.1.0", "app info carries the shell version");

  const caps = native.capabilities();
  caps.push("mutated");
  eq(native.has("mutated"), false, "capabilities() returns a copy");

  const result = native.overlay.setRegion(176, 270);
  eq(result.ok, true, "setRegion reports success");
  eq(result.widthDp, 176, "setRegion returns the applied width");
  eq(regionCalls.length, 1, "setRegion reaches native exactly once");
  eq(regionCalls[0][0], 176, "the requested width reaches native");
  eq(regionCalls[0][1], 270, "the requested height reaches native");
  eq(native.overlay.getRegion().heightDp, 270, "getRegion returns native state");
}

// ── 2. legacy window.vmCompanionAndroid is untouched ───────────────────────
{
  const env = makeEnv(currentBridge());
  const api = env.win.vmCompanionAndroid;
  check(!!api, "window.vmCompanionAndroid still exists");
  for (const name of [
    "isSupported", "hasPermission", "requestPermission", "getState", "getVisible",
    "setVisible", "onCompanionVisibility", "hasNativeTts", "ttsSpeak", "ttsStop",
    "hasNativeAsr", "asrStart", "asrStop", "asrCancel", "onAsrEvent",
  ]) {
    eq(typeof api[name], "function", `legacy ${name} is still a function`);
  }
  eq(api.getState().visible, true, "legacy state still comes from native");
}

// ── 3. an OLDER shell without a contract is described honestly ─────────────
{
  const legacy = baseBridge({
    ttsSpeak: () => true,
    ttsStop: () => {},
    // No asrSupported, no contractJson, no region API.
  });
  const env = makeEnv(legacy);
  const native = env.win.vmNative;
  check(!!native, "the reduced shell still gets a vmNative façade");
  eq(native.contract.version, 1, "a shell without a contract is version 1");
  eq(native.contract.ready, true, "the legacy façade is usable");
  eq(native.has("overlay"), true, "legacy overlay capability is reported");
  eq(native.has("tts"), true, "legacy TTS capability is detected from the method");
  eq(native.has("asr"), false, "missing ASR is reported honestly");
  eq(native.has("overlayRegion"), false, "missing region support is reported honestly");
  eq(native.overlay.setRegion(176, 270).reason, "unsupported", "setRegion refuses on a legacy shell");
  eq(native.overlay.getRegion().reason, "unsupported", "getRegion refuses on a legacy shell");
  eq(env.win.vmCompanionAndroid.getState().visible, true, "the legacy page path still works");
}

// ── 4. malformed contract JSON falls back instead of breaking the page ─────
{
  const env = makeEnv(baseBridge({
    contractJson: () => "{ definitely not json",
    ttsSpeak: () => true,
  }));
  eq(env.win.vmNative.contract.version, 1, "a malformed contract falls back to version 1");
  eq(env.win.vmNative.has("tts"), true, "the fallback still describes real methods");
}

// ── 5. a throwing region call is contained ─────────────────────────────────
{
  const env = makeEnv(currentBridge({
    setOverlayRegion: () => { throw new Error("bridge died"); },
  }));
  eq(env.win.vmNative.overlay.setRegion(176, 270).reason, "bridge-error",
     "a throwing native call becomes a safe result");
}

// ── 6. the helper is lazy: it works even when loaded BEFORE the shim ───────
{
  const env = makeEnv(currentBridge(), { helperFirst: true });
  eq(env.win.VMNative.available(), true, "the helper resolves the shim lazily");
  eq(env.win.VMNative.has("overlayRegion"), true, "lazy capability detection works");
  eq(env.win.VMNative.overlayRegion.set(176, 270).ok, true, "lazy region call works");
}

// ── 7. browsers and shells without the bridge degrade cleanly ──────────────
{
  const env = makeEnv(null);
  eq(env.win.VMNative.available(), false, "no shim means no native bridge");
  eq(env.win.VMNative.has("overlayRegion"), false, "no shim means no capabilities");
  eq(env.win.VMNative.overlayRegion.set(176, 270).reason, "unsupported",
     "the browser path skips the region call");
}

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);
