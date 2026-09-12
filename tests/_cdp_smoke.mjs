// Real-browser (Chromium/Chrome CDP) smoke harness for the Cloud companion.
// Loads the ACTUAL index.html-equivalent page (static markup + real scripts),
// runs with virtual rAF via headless Chrome, then inspects the live DOM:
//  - is the rig SVG mounted and on top?
//  - does the animation loop tick (framesDrawn)?
//  - do parts get transforms / mouth expressions really change?
//  - does prefers-reduced-motion freeze it?
//  - does a simulated drag then release leave it animating again?
// Usage: node tests/_cdp_smoke.mjs
import { spawn } from "node:child_process";
import http from "node:http";

const CHROME = "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe";
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const BROWSER = process.env.CLOUD_SMOKE_BROWSER === "edge" ? EDGE : CHROME;
const URL = process.env.CLOUD_SMOKE_URL || "http://127.0.0.1:8901/tests/browser_smoke.html";
const PORT = 9332;

const log = (...a) => console.log("[cdp]", ...a);

function getJson(port, path) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: "127.0.0.1", port, path }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => { try { resolve(JSON.parse(body)); } catch (e) { reject(new Error("bad json: " + body.slice(0, 200))); } });
    });
    req.on("error", reject);
    req.setTimeout(4000, () => req.destroy(new Error("timeout")));
  });
}
async function waitFor(ms) { return new Promise((r) => setTimeout(r, ms)); }
async function poll(fn, ms, tries) {
  for (let i = 0; i < tries; i++) { try { const v = await fn(); if (v) return v; } catch (_) { } await waitFor(ms); }
  throw new Error("poll timeout");
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = [];
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.pending.has(msg.id)) { this.pending.get(msg.id)(msg); this.pending.delete(msg.id); }
      else if (msg.method) this.events.push(msg);
    };
  }
  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, (msg) => (msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result)));
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("page exception: " + JSON.stringify(r.exceptionDetails.exception || r.exceptionDetails.text));
    return r.result.value;
  }
}

async function launch() {
  const profile = `${process.env.TEMP}/cdp-cloud-${Date.now()}`;
  const flags = [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-default-apps",
    "--run-all-compositor-stages-before-draw",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--window-size=1280,800", "about:blank"
  ];
  const errFile = `${process.env.TEMP}/cdp_chrome_err.log`;
  const fs = await import("node:fs");
  const child = spawn(BROWSER, flags, { stdio: ["ignore", "ignore", "pipe"], windowsHide: true });
  let errText = "";
  child.stderr.on("data", (d) => { errText += d.toString(); });
  child.on("exit", (code) => fs.writeFileSync(errFile, "chrome exited code=" + code + "\n" + errText));
  await poll(async () => { try { return await getJson(PORT, "/json/version"); } catch (_) { return null; } }, 300, 40);
  if (errText) log("chrome stderr:", errText.slice(0, 500));
  return child;
}

async function newPage() {
  try {
    const list = await getJson(PORT, "/json/new?" + encodeURIComponent("about:blank"));
    return list.webSocketDebuggerUrl;
  } catch (e) {
    // Older Chrome: PUT /json/new
    const http = await import("node:http");
    return new Promise((resolve, reject) => {
      const body = "";
      const req = http.request({ host: "127.0.0.1", port: PORT, path: "/json/new?" + encodeURIComponent("about:blank"), method: "PUT" }, (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => { try { resolve(JSON.parse(data).webSocketDebuggerUrl); } catch (e2) { reject(e2); } });
      });
      req.on("error", reject);
      req.end(body);
    });
  }
}

async function run(url, reduced, doDrag) {
  const pageWs = await newPage();
  const ws = new WebSocket(pageWs);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const cdp = new CDP(ws);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  if (reduced) {
    await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  }
  await cdp.send("Page.navigate", { url });
  await waitFor(4000); // let scripts run + rAF frames happen
  if (doDrag) {
    await cdp.eval(`(function () {
      var el = document.getElementById("vmCloudCharacter");
      if (!el) return "no-el";
      var r = el.getBoundingClientRect();
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var opts = { bubbles: true, cancelable: true, clientX: cx, clientY: cy, pointerId: 1, isPrimary: true };
      el.dispatchEvent(new PointerEvent("pointerdown", Object.assign({}, opts)));
      for (var i = 1; i <= 3; i++) document.dispatchEvent(new PointerEvent("pointermove", Object.assign({}, opts, { clientX: cx + i * 20, clientY: cy + i * 8 })));
      document.dispatchEvent(new PointerEvent("pointerup", Object.assign({}, opts, { clientX: cx + 60, clientY: cy + 24 })));
      return "dragged";
    })()`);
    await waitFor(2000);
  }
  const state = await cdp.eval(`(function () {
    return {
      title: document.title,
      results: (document.getElementById("results") || {}).textContent || null,
      debug: window.vmCloudAnim && window.vmCloudAnim.debug ? window.vmCloudAnim.debug() : null
    };
  })()`);
  ws.close();
  return state;
}

const outcomes = {};
const chrome = await launch();
try {
  outcomes.baseline = await run(URL, false, false);
  outcomes.reducedMotion = await run(URL, true, false);
  outcomes.afterDrag = await run(URL, false, true);
} finally {
  try { await getJson(PORT, "/json/close/" + "0"); } catch (_) { }
  try { chrome.kill(); } catch (_) { }
}
console.log(JSON.stringify({ outcomes }, null, 2));