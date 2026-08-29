// Node harness that loads static/pref_setup_model.js + static/onboarding.js with
// minimal browser stubs and drives the real window handlers (Continue/Skip/Back/
// finish/reopen/toggle) exactly the way a browser does via inline onclick →
// globals. The stubs implement just enough DOM for the wizard's post-render
// queries (progress ring + preview) so live updates are observable.
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const MODEL_PATH = path.join(ROOT, "static", "pref_setup_model.js");
const ONBOARD_PATH = path.join(ROOT, "static", "onboarding.js");

function captureRender(overlayHtml) {
  // Pull the current step content + action buttons + progress ring out of the
  // overlay's innerHTML for assertions.
  const has = (re) => new RegExp(re).test(overlayHtml);
  const actions = {};
  const mOrder = /onclick="(prefSetupGo\(-1\)|prefSetupGo\(1\)|prefSetupFinish\(\)|prefSetupSkip\(\)|prefSetupClose\(\))"/g;
  let m;
  const order = [];
  while ((m = mOrder.exec(overlayHtml))) order.push(m[1]);
  actions.order = order;
  actions.hasBack = has(/prefSetupGo\(-1\)/);
  actions.hasContinue = has(/prefSetupGo\(1\)/);
  actions.hasFinish = has(/prefSetupFinish\(\)/);
  actions.hasSkip = has(/prefSetupSkip\(\)/);
  actions.hasClose = has(/prefSetupClose\(\)/);
  actions.hasRing = has(/ob-ring/);
  const nm = /ob-ring-num">(\d+)</.exec(overlayHtml);
  actions.ringPct = nm ? nm[1] : null;
  const bl = /ob-ring-label"[^>]*>([^<]+)</.exec(overlayHtml);
  actions.ringLabel = bl ? bl[1] : null;
  return actions;
}

function buildSandbox(initialState) {
  const created = [];
  const sections = {
    language: initialState.language || {},
    preferences: initialState.preferences || {},
    culture: initialState.culture || {},
  };
  let setupStatus = initialState.setup_status || "not_started";
  if (sections.preferences.preferences_setup_status === undefined) {
    sections.preferences.preferences_setup_status = setupStatus;
  }

  function sect(path) { return path.replace("/api/settings/", ""); }

  // get/put through the same per-section endpoints the wizard uses.
  function apiFetch(path, opts) {
    opts = opts || {};
    if (opts.method === "PUT" || (opts.method === "POST" && path.indexOf("setup-status") !== -1)) {
      const data = JSON.parse(opts.body || "{}");
      if (path.indexOf("setup-status") !== -1) {
        setupStatus = data.setup_status || setupStatus;
        sections.preferences.preferences_setup_status = setupStatus;
      } else {
        sections[sect(path)] = Object.assign({}, sections[sect(path)], data);
      }
    }
    if (path.indexOf("setup-status") !== -1) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "success", setup_status: setupStatus }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "success", data: sections[sect(path)] || {} }) });
  }

  // ── Minimal live DOM ───────────────────────────────────────────────
  // Each stub element keeps its own _html string. querySelector returns a
  // "live view" whose setters (style / textContent / innerHTML) patch the
  // owning element's string in place, so subsequent reads reflect changes.
  function liveElement(owner, kind) {
    const lv = {
      style: new Proxy({}, {
        get(t, k) { return t[k] !== undefined ? t[k] : null; },
        set(t, k, v) {
          t[k] = v;
          if (k === "background") {
            const m = owner._html.match(kind.openRe);
            if (!m) return true;
            const open = m[0];
            const updated = open.replace(/background:[^"]*/, "background:" + v);
            owner._html = owner._html.replace(open, updated);
          } else if (k === "display") {
            const m = owner._html.match(kind.openRe);
            if (!m) return true;
            const open = m[0];
            const updated = open.indexOf("display:") !== -1
              ? open.replace(/display:[^";]*/, "display:" + v)
              : open.replace(/>$/, ' style="display:' + v + '">');
            owner._html = owner._html.replace(open, updated);
          }
          return true;
        },
      }),
      get textContent() { const m = owner._html.match(kind.innerRe); return m ? m[2] : ""; },
      set textContent(v) {
        const m = owner._html.match(kind.innerRe);
        if (!m) return;
        owner._html = owner._html.replace(m[0], m[1] + v + m[3]);
      },
      get innerHTML() { const m = owner._html.match(kind.innerRe); return m ? m[2] : ""; },
      set innerHTML(v) {
        const m = owner._html.match(kind.innerRe);
        if (!m) return;
        owner._html = owner._html.replace(m[0], m[1] + v + m[3]);
      },
      querySelector(childSel) {
        return childSelector(owner, childSel);
      },
      querySelectorAll(childSel) {
        if (childSel === ".ob-choice") return [];
        const lv2 = childSelector(owner, childSel);
        return lv2 ? [lv2] : [];
      },
      classList: { _set: {}, add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute() {},
      getAttribute() { return null; },
      focus() {},
    };
    return lv;
  }

  function childSelector(owner, className) {
    const kind = {
      "[data-progress]": { openRe: /<div class="ob-ring-wrap" data-progress>/, innerRe: /(<div class="ob-ring-wrap" data-progress>)([^]*?)(<\/div>)/ },
      ".ob-ring-fill": { openRe: /<div class="ob-ring-fill" style="[^"]*">/, innerRe: /(<div class="ob-ring-fill" style="[^"]*">)([^]*?)(<\/div>)/ },
      ".ob-ring-num": { openRe: /<span class="ob-ring-num">/, innerRe: /(<span class="ob-ring-num">)([^]*?)(<\/span>)/ },
      ".ob-ring-label": { openRe: /<div class="ob-ring-label"[^>]*>/, innerRe: /(<div class="ob-ring-label"[^>]*>)([^]*?)(<\/div>)/ },
      ".ob-preview": { openRe: /<aside class="ob-preview"[^>]*>/, innerRe: /(<aside class="ob-preview"[^>]*>)([^]*?)(<\/aside>)/ },
    }[className];
    if (!kind) return null;
    return liveElement(owner, kind);
  }

  function makeEl() {
    const el = {
      _html: "",
      _styles: {},
      _attrs: {},
      style: undefined,
      listeners: {},
      classList: {
        _set: {},
        add(c) { this._set[c] = true; },
        remove(c) { delete this._set[c]; },
        contains(c) { return !!this._set[c]; },
        toggle(c, f) { if (f === undefined) f = !this._set[c]; if (f) this._set[c] = true; else delete this._set[c]; return f; },
      },
      setAttribute(n, v) { this._attrs[n] = v; if (n === "id") elementsById[v] = this; },
      getAttribute(n) { return this._attrs[n]; },
      focus() {},
      querySelector(sel) { return childSelector(this, sel); },
      querySelectorAll(sel) {
        if (sel === ".ob-choice") return [];
        const lv2 = childSelector(this, sel);
        return lv2 ? [lv2] : [];
      },
    };
    el.style = new Proxy(el._styles, {
      get(t, k) { return t[k]; },
      set(t, k, v) { t[k] = v; return true; },
    });
    Object.defineProperty(el, "innerHTML", {
      get() { return this._html; },
      set(v) { this._html = String(v); },
    });
    return el;
  }

  const elementsById = {};
  const bodyStub = {
    appendChild(child) { created.push(child); return child; },
  };

  const documentStub = {
    addEventListener() {},
    createElement: makeEl,
    getElementById(id) { return elementsById[id] || null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    body: bodyStub,
  };

  const CULTURAL_LANGUAGES = [
    { value: "en", label: "English" }, { value: "ig", label: "Igbo" },
    { value: "yo", label: "Yoruba" }, { value: "ha", label: "Hausa" },
    { value: "pcm", label: "Nigerian Pidgin" }, { value: "sw", label: "Swahili" },
  ];
  const _SH = { select: () => "<select></select>" };

  const sandbox = {
    document: documentStub,
    Promise,
    setTimeout,
    clearTimeout,
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    console,
    apiFetch,
    authHeaders: () => ({}),
    CULTURAL_LANGUAGES,
    _SH,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  // Model MUST load before onboarding.js (mirrors index.html order).
  vm.runInContext(fs.readFileSync(MODEL_PATH, "utf8"), sandbox, { filename: "pref_setup_model.js" });
  vm.runInContext(fs.readFileSync(ONBOARD_PATH, "utf8"), sandbox, { filename: "onboarding.js" });

  const overlayEl = () => created.find((el) => el._attrs.id === "prefSetupOverlay") || created[created.length - 1];
  return { sandbox, overlayEl, sections };
}

async function wait(t) { return new Promise((r) => setTimeout(r, t)); }

async function main() {
  const mode = process.argv[2] || "basic";
  const initialState = JSON.parse(process.env.PREF_STATE || "{}");
  const h = buildSandbox(initialState);
  const S = h.sandbox;

  S.openPreferencesSetup({ source: "settings" });
  await wait(40); // let loadDraft() resolve + re-render

  const overlay = h.overlayEl();
  const html = overlay ? overlay.innerHTML : "";
  const render = captureRender(html);

  const result = {
    mode,
    model: {
      steps: S.PREF_SETUP_MODEL ? S.PREF_SETUP_MODEL.stepIds() : null,
      total: S.PREF_SETUP_MODEL ? S.PREF_SETUP_MODEL.totalSteps() : null,
    },
    box: {
      display: overlay ? overlay.style.display : "(none)",
      htmlLen: html.length,
      firstContent: html.slice(0, 120),
    },
    render,
  };

  if (mode === "nav") {
    const afterIntro = captureRender(overlay.innerHTML);
    // Continue → step 1 (Use).
    S.prefSetupGo(1);
    await wait(5);
    const step1 = captureRender(overlay.innerHTML);
    // Back → step 0.
    S.prefSetupGo(-1);
    await wait(5);
    const back0 = captureRender(overlay.innerHTML);
    // Skip closes (display none)…
    S.prefSetupSkip();
    await wait(30);
    const afterSkipDisplay = overlay.style.display;
    // …and reopening must SHOW the wizard again (display flex) + keep values.
    S.openPreferencesSetup({ source: "settings" });
    await wait(40);
    const reopenedDisplay = overlay.style.display;
    const reopenedHTML = overlay.innerHTML;
    result.nav = {
      afterIntro,
      step1,
      back0,
      afterSkipDisplay,
      reopenedDisplay,
      reopenedHasRing: /ob-ring/.test(reopenedHTML),
      reopenedRingPct: (/ob-ring-num">(\d+)</.exec(reopenedHTML) || [])[1] || null,
    };
  } else if (mode === "live") {
    // Fresh user, move to the Use step, then toggle a use case both ways and
    // confirm the ring + label update WITHOUT a page revisit or re-render.
    S.prefSetupGo(1);
    await wait(5);
    const before = captureRender(overlay.innerHTML);
    S.prefSetupToggle("use_cases", "Learning");
    const after = captureRender(overlay.innerHTML);
    const ringJson = (() => {
      const wrap = overlay.querySelector("[data-progress]");
      return { display: wrap && wrap.style ? wrap.style.display : null };
    })();
    result.live = { before, after, ringJson };
  } else if (mode === "reopen-100") {
    // Finish on a fully-populated user then reopen → 100% persists.
    S.prefSetupFinish();
    await wait(30);
    S.openPreferencesSetup({ source: "settings" });
    await wait(40);
    result.final = captureRender(overlay.innerHTML);
  }

  console.log(JSON.stringify(result));
}

main().catch((e) => { console.error("HARNESS ERROR:", e.stack); process.exit(1); });