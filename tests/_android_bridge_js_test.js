// Pure-node test runner for the JavaScript shim that native injects into BOTH
// WebViews (android/.../AndroidBridgeJs.kt). No DOM: extracts the REAL shim
// source out of the Kotlin file, runs it in a VM against a stubbed
// window.VmAndroidBridge, and checks the contract the overlay page depends on.
//
// The focus is the speech-recognition bus (window.__vmAsrEvent), because the
// hands-free conversation is driven by events that arrive from native.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const KT = path.join(ROOT, "android", "app", "src", "main", "java", "ai", "valleymind", "companion", "AndroidBridgeJs.kt");
const kt = fs.readFileSync(KT, "utf8");
const match = kt.match(/const val ANDROID_BRIDGE_JS: String = """([\s\S]*?)"""/);
if (!match) {
  console.error("could not extract ANDROID_BRIDGE_JS from AndroidBridgeJs.kt");
  process.exit(1);
}
const SRC = match[1];

const failures = [];
let passes = 0;
function check(cond, name) { if (cond) passes++; else failures.push(name); }
function eq(got, want, name) {
  check(got === want, name + ` (got ${JSON.stringify(got)} expected ${JSON.stringify(want)})`);
}

function makeEnv(bridge) {
  const win = { VmAndroidBridge: bridge };
  const sandbox = { window: win, JSON, Date, setTimeout, clearTimeout, console };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(SRC, ctx, { filename: "AndroidBridgeJs.kt" });
  return {
    win,
    run: (code) => vm.runInContext(code, ctx),
    // Deliver a native recognition event, exactly as the Kotlin side does.
    emit: (payload) => win.__vmAsrEvent(JSON.stringify(payload)),
  };
}

function stubBridge(over) {
  const calls = [];
  const bridge = Object.assign({
    isOverlaySupported: () => true,
    hasPermission: () => true,
    requestPermission: () => { calls.push(["requestPermission"]); },
    getStateJson: () => '{"granted":true,"running":true,"visible":true}',
    setVisible: (on) => { calls.push(["setVisible", on]); },
    notifyBridgeReady: () => { calls.push(["notifyBridgeReady"]); },
    asrSupported: () => true,
    asrStart: (locale) => { calls.push(["asrStart", locale]); return true; },
    asrStop: () => { calls.push(["asrStop"]); },
    asrCancel: () => { calls.push(["asrCancel"]); },
    ttsSpeak: () => true,
    ttsStop: () => { calls.push(["ttsStop"]); },
  }, over || {});
  bridge.calls = calls;
  return bridge;
}

// ── 1. the existing bridge contract is untouched ───────────────────────────
{
  const env = makeEnv(stubBridge());
  const api = env.win.vmCompanionAndroid;
  check(!!api, "the shim installs window.vmCompanionAndroid");
  eq(typeof api.setVisible, "function", "setVisible is exposed");
  eq(typeof api.getState, "function", "getState is exposed");
  eq(api.getState().visible, true, "the state comes from native");
}

// ── 2. the recogniser is reported honestly ─────────────────────────────────
{
  const env = makeEnv(stubBridge({ asrSupported: () => false }));
  eq(env.win.vmCompanionAndroid.hasNativeAsr(), false, "no platform recogniser is reported as unsupported");
  eq(env.win.vmCompanionAndroid.asrStart("en-GB"), false, "asrStart refuses when there is no recogniser");
}
{
  const bridge = stubBridge();
  const env = makeEnv(bridge);
  eq(env.win.vmCompanionAndroid.hasNativeAsr(), true, "an available recogniser is reported");
  eq(env.win.vmCompanionAndroid.asrStart("en-GB"), true, "asrStart asks native to listen");
  eq(env.win.vmCompanionAndroid.asrStop(), undefined, "asrStop is callable");
  env.win.vmCompanionAndroid.asrCancel();
  const kinds = bridge.calls.map((c) => c[0]);
  check(kinds.indexOf("asrStart") !== -1, "asrStart reached native");
  check(kinds.indexOf("asrStop") !== -1, "asrStop reached native");
  check(kinds.indexOf("asrCancel") !== -1, "asrCancel reached native");
}

// ── 3. recognition events reach the page ───────────────────────────────────
{
  const env = makeEnv(stubBridge());
  const seen = [];
  env.win.vmCompanionAndroid.onAsrEvent((ev) => { seen.push(ev); });

  env.emit({ type: "ready" });
  env.emit({ type: "partial", text: "hel" });
  env.emit({ type: "end" });
  env.emit({ type: "final", text: "hello" });
  env.emit({ type: "error", reason: "no-match" });

  eq(seen.length, 5, "every native event reaches the listener");
  eq(seen[1].text, "hel", "the partial transcript is delivered");
  eq(seen[3].text, "hello", "the final transcript is delivered");
  eq(seen[4].reason, "no-match", "the failure reason is delivered");
}

// ── 4. an event that arrives BEFORE the page subscribes is not lost ────────
{
  const env = makeEnv(stubBridge());
  env.emit({ type: "final", text: "early" });

  const seen = [];
  env.win.vmCompanionAndroid.onAsrEvent((ev) => { seen.push(ev); });

  eq(seen.length, 1, "the queued event is flushed on subscription");
  eq(seen[0].text, "early", "the queued event keeps its payload");

  env.emit({ type: "final", text: "later" });
  eq(seen.length, 2, "later events are delivered immediately");
}

// ── 5. re-injecting the shim (page start AND page finish) keeps the bus ────
{
  const env = makeEnv(stubBridge());
  const seen = [];
  env.win.vmCompanionAndroid.onAsrEvent((ev) => { seen.push(ev); });
  env.emit({ type: "final", text: "first" });

  // The native side injects the shim again on every page lifecycle event.
  vm.runInContext(SRC, vm.createContext({ window: env.win, JSON, Date, setTimeout, clearTimeout, console }));

  env.emit({ type: "final", text: "second" });
  eq(seen.length, 2, "the first subscriber still receives events after a re-injection");
  eq(seen[1].text, "second", "the event after the re-injection is the new one");
}

// ── 6. a malformed payload never breaks the page ───────────────────────────
{
  const env = makeEnv(stubBridge());
  const seen = [];
  env.win.vmCompanionAndroid.onAsrEvent((ev) => { seen.push(ev); });
  env.win.__vmAsrEvent("{not json");
  eq(seen.length, 1, "a malformed payload becomes an error event instead of throwing");
  eq(seen[0].type, "error", "the malformed payload is reported as an error");
}

// ── 7. a throwing listener never blocks the others ─────────────────────────
{
  const env = makeEnv(stubBridge());
  const seen = [];
  env.win.vmCompanionAndroid.onAsrEvent(() => { throw new Error("boom"); });
  env.win.vmCompanionAndroid.onAsrEvent((ev) => { seen.push(ev); });
  env.emit({ type: "final", text: "still here" });
  eq(seen.length, 1, "one bad listener never blocks the others");
}

console.log(JSON.stringify({ passed: passes, failed: failures }));
if (failures.length) process.exit(1);
