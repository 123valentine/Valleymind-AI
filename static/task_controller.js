/*
 * task_controller.js -- the companion's SAFE task execution layer.
 *
 * This is a SEPARATE, explicitly-permissioned interaction adapter. It does NOT
 * modify or weaken static/cloud_vision.js (which stays capture-only): screen
 * inspection here is a read-only action that simply calls the existing
 * VMCloudVision.capture() and is gated behind the "vision" permission.
 *
 * What it can do is intentionally narrow and browser-scoped:
 *   respond        - the companion talks (always allowed)
 *   inspect_view   - read the current view via VMCloudVision (permission: vision)
 *   capture_view   - alias of inspect_view
 *   scroll         - scroll the authorized page (permission: scroll)
 *   navigate       - open a same-origin app path (permission: navigate)
 *   click          - click a validated visible control (permission: click, CONFIRM)
 *   type           - type into an authorized editable field (permission: type, CONFIRM)
 *
 * It NEVER runs generated code, NEVER issues shell commands, NEVER touches the
 * OS/mouse/keyboard outside the page, and never bypasses auth/permissions.
 * The server (core.tasks) is the authority on what a plan may contain; this
 * module re-validates defensively, gates consequential actions behind explicit
 * confirmation, drives the EXISTING companion status via opts.onStatus, and
 * records outcomes idempotently so a retried request cannot re-run a step.
 */
(function (root) {
  "use strict";

  var STATES = ["idle", "planning", "awaiting_confirmation", "working",
                "paused", "completed", "failed", "cancelled"];
  var TERMINAL = ["completed", "failed", "cancelled"];

  var VALID = {
    idle: ["planning"],
    planning: ["awaiting_confirmation", "working", "completed", "failed", "cancelled"],
    awaiting_confirmation: ["working", "completed", "failed", "cancelled"],
    working: ["awaiting_confirmation", "paused", "completed", "failed", "cancelled"],
    paused: ["working", "cancelled", "failed"],
    completed: [],
    failed: [],
    cancelled: []
  };

  // Map task lifecycle -> the EXISTING companion status vocabulary so the
  // avatar/face controller is driven through window.vmVrmCompanion.applyStatus.
  var COMPANION_STATUS = {
    idle: "idle",
    planning: "thinking",
    awaiting_confirmation: "listening",
    working: "working",
    paused: "idle",
    completed: "happy",
    failed: "confused",
    cancelled: "idle"
  };

  var CONSEQUENTIAL = { click: true, type: true };
  var PERMISSION = {
    inspect_view: "vision",
    capture_view: "vision",
    scroll: "scroll",
    navigate: "navigate",
    goto_workspace: "navigate",
    click: "click",
    type: "type"
  };

  // In-app workspaces the companion may move the interface to. Mirrors the
  // vmWsGo() tab allowlist in index.html — never an arbitrary target.
  var WORKSPACES = ["studio", "website", "sketch", "threed", "roundtable",
    "music", "editing", "analytics"];

  // The planner runs the same brain cluster the chat uses (core.brain), which can
  // take up to ~30s per provider and may try several. The old 15s client budget
  // fired "action timed out" while the planner was still working; the plan
  // budget now comfortably exceeds the backend's own timeout.
  var PLANNER_TIMEOUT_MS = 90000;
  var DEFAULT_ACTION_TIMEOUT_MS = 30000;

  // ── pure helpers (mirrored + tested) ──────────────────────────────────────
  function statusFor(state) { return COMPANION_STATUS[state] || "idle"; }

  function isConsequential(action) {
    return !!(action && CONSEQUENTIAL[action.type]);
  }

  function permissionFor(action) {
    return action ? (PERMISSION[action.type] || null) : null;
  }

  function canTransition(from, to) {
    return !!(VALID[from] && VALID[from].indexOf(to) !== -1);
  }

  function transition(from, to) {
    if (!VALID[from] || STATES.indexOf(to) === -1 || !canTransition(from, to)) {
      throw new Error("illegal task transition: " + from + " -> " + to);
    }
    return to;
  }

  function isTerminal(state) { return TERMINAL.indexOf(state) !== -1; }

  // A selector must target an id/class/attribute and contain no dangerous token.
  var SELECTOR_ALLOWED = /^[A-Za-z0-9_\-.#[\]="'*:\s]+$/;
  var SELECTOR_BLOCKED = ["<", ">", "{", "}", "(", ")", ";", "`", "\\",
    "eval", "function", "javascript:", "onerror", "onload", "onclick",
    "document", "window", "constructor", "prototype", "__proto__"];
  function selectorOk(selector) {
    var sel = String(selector || "").trim();
    if (!sel || sel.length > 200) return false;
    if (!SELECTOR_ALLOWED.test(sel)) return false;
    var low = sel.toLowerCase();
    for (var i = 0; i < SELECTOR_BLOCKED.length; i++) {
      if (low.indexOf(SELECTOR_BLOCKED[i]) !== -1) return false;
    }
    if (!/[#.[]/.test(sel)) return false;
    return true;
  }

  function urlOk(url, allowExternal) {
    var raw = String(url || "").trim();
    if (!raw || raw.length > 2048) return false;
    if (/[\x00-\x1f\x7f]/.test(raw)) return false;
    if (raw.indexOf("//") === 0) return false;
    if (raw.charAt(0) === "/") return true;
    var low = raw.toLowerCase();
    if (low.indexOf("javascript:") === 0 || low.indexOf("data:") === 0 ||
        low.indexOf("file:") === 0 || low.indexOf("vbscript:") === 0) return false;
    var m = /^(https?):\/\/([^\s/?#]+)([^\s]*)$/i.exec(raw);
    if (!m) return false;
    if (!allowExternal) return false;
    if (m[1].toLowerCase() !== "https") return false;
    if (m[2].indexOf("@") !== -1) return false;
    return true;
  }

  function workspaceOk(name) {
    return WORKSPACES.indexOf(String(name || "").trim().toLowerCase()) !== -1;
  }

  // ── state ─────────────────────────────────────────────────────────────────
  var opts = {};
  var active = null;
  var driveToken = 0;      // invalidates in-flight async work on cancel/stop
  var reqSeq = 0;

  function hasDom() { return typeof document !== "undefined"; }

  function nowMs() { return Date.now(); }

  function emit(kind) {
    if (typeof opts.onEvent === "function") {
      try { opts.onEvent(kind || "update", snapshot()); } catch (e) { /* non-fatal */ }
    }
  }

  function getPermissions() {
    if (typeof opts.getPermissions !== "function") return [];
    try { return opts.getPermissions() || []; } catch (e) { return []; }
  }

  function setState(next) {
    if (!active) return;
    if (active.state === next) { emit("state"); return; }
    try {
      transition(active.state, next);
    } catch (e) {
      // Ignore an impossible edge rather than corrupting the lifecycle.
      return;
    }
    active.state = next;
    if (typeof opts.onStatus === "function") {
      try { opts.onStatus(statusFor(next)); } catch (e2) { /* non-fatal */ }
    }
    emit("state");
  }

  function snapshot() {
    if (!active) {
      return { state: "idle", active: false, steps: [] };
    }
    return {
      active: !isTerminal(active.state),
      task_id: active.task_id,
      goal: active.goal,
      state: active.state,
      index: active.index,
      total: active.steps.length,
      message: active.message,
      error: active.error,
      pending: active.pending ? {
        step_id: active.pending.step_id,
        type: active.pending.type,
        label: active.pending.label || active.pending.type,
        permission: active.pending.permission || null,
        blocked: !!active.pending.blocked,
        params: active.pending.params
      } : null,
      steps: active.steps.map(function (s, i) {
        return {
          step_id: s.step_id,
          type: s.type,
          label: s.label || s.type,
          permission: s.permission || null,
          consequential: !!s.consequential,
          blocked: !!s.blocked,
          done: !!active.executed[s.step_id],
          current: i === active.index
        };
      })
    };
  }

  // ── outcome recording (idempotent, fire-and-forget) ───────────────────────
  function record(step, outcome, message) {
    if (!active || !step || !step.step_id || !active.task_id) return;
    if (typeof opts.apiPost !== "function") return;
    var body = {
      task_id: active.task_id,
      step_id: step.step_id,
      state: active.state,
      outcome: outcome || "ok",
      message: String(message || "").slice(0, 120)
    };
    try {
      var p = opts.apiPost("/api/task/outcome", body);
      if (p && typeof p.catch === "function") p.catch(function () { /* non-fatal */ });
    } catch (e) { /* non-fatal */ }
  }

  // ── action executor (DOM adapter) ─────────────────────────────────────────
  function withTimeout(promise, ms, timeoutMessage) {
    return new Promise(function (resolve) {
      var settled = false;
      var timer = (typeof root.setTimeout === "function")
        ? root.setTimeout(function () {
            if (settled) return;
            settled = true;
            resolve({ outcome: "failed", timeout: true,
              message: String(timeoutMessage || "action timed out") });
          }, ms)
        : null;
      Promise.resolve(promise).then(function (res) {
        if (settled) return;
        settled = true;
        if (timer) root.clearTimeout(timer);
        resolve(res || { outcome: "ok" });
      }, function (e) {
        if (settled) return;
        settled = true;
        if (timer) root.clearTimeout(timer);
        resolve({ outcome: "failed", message: "action error" });
      });
    });
  }

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return true; // unknown -> allow (control ptr)
    try {
      var rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) return false;
      var style = root.getComputedStyle ? root.getComputedStyle(el) : null;
      if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    } catch (e) { return true; }
    return true;
  }

  function fireEvent(el, type) {
    try {
      var ev = new root.Event(type, { bubbles: true });
      el.dispatchEvent(ev);
    } catch (e) { /* non-fatal */ }
  }

  function executeStep(step) {
    var type = step.type;
    var p = step.params || {};

    if (type === "respond") {
      if (typeof opts.onSpeak === "function") {
        try { opts.onSpeak(String(p.text || "")); } catch (e) { /* non-fatal */ }
      }
      return Promise.resolve({ outcome: "ok" });
    }

    if (type === "inspect_view" || type === "capture_view") {
      if (typeof opts.captureView !== "function") {
        return Promise.resolve({ outcome: "blocked", message: "screen capture unavailable" });
      }
      var frame = "";
      try { frame = String(opts.captureView(Number(p.max_width) || 720) || ""); } catch (e) { frame = ""; }
      if (frame) return Promise.resolve({ outcome: "ok" });
      return Promise.resolve({ outcome: "blocked", message: "no screen frame (permission or capture unavailable)" });
    }

    if (type === "scroll") {
      if (!hasDom()) return Promise.resolve({ outcome: "skipped", message: "no page" });
      var amount = Number(p.amount) || 600;
      var dx = 0, dy = 0;
      if (p.direction === "up") dy = -amount;
      else if (p.direction === "down") dy = amount;
      else if (p.direction === "left") dx = -amount;
      else dx = amount;
      try {
        if (typeof root.scrollBy === "function") root.scrollBy(dx, dy);
        else return Promise.resolve({ outcome: "failed", message: "page cannot scroll" });
      } catch (e) { return Promise.resolve({ outcome: "failed", message: "scroll failed" }); }
      return Promise.resolve({ outcome: "ok" });
    }

    if (type === "navigate") {
      if (!hasDom()) return Promise.resolve({ outcome: "skipped", message: "no page" });
      var url = String(p.url || "");
      if (!urlOk(url, false)) return Promise.resolve({ outcome: "blocked", message: "unsafe navigation target" });
      try { root.location.assign(url); } catch (e) { return Promise.resolve({ outcome: "failed", message: "navigation failed" }); }
      return Promise.resolve({ outcome: "ok" });
    }

    if (type === "goto_workspace") {
      if (!workspaceOk(p.workspace)) {
        return Promise.resolve({ outcome: "blocked", message: "unknown workspace" });
      }
      if (typeof opts.onGuide !== "function") {
        return Promise.resolve({ outcome: "blocked", message: "navigation unavailable" });
      }
      try { opts.onGuide(String(p.workspace)); }
      catch (e) { return Promise.resolve({ outcome: "failed", message: "navigation failed" }); }
      return Promise.resolve({ outcome: "ok" });
    }

    if (type === "click") {
      if (!hasDom()) return Promise.resolve({ outcome: "skipped", message: "no page" });
      if (!selectorOk(p.selector)) return Promise.resolve({ outcome: "blocked", message: "unsafe selector" });
      var el;
      try { el = document.querySelector(p.selector); } catch (e) { el = null; }
      if (!el) return Promise.resolve({ outcome: "failed", message: "control not found" });
      if (el.disabled) return Promise.resolve({ outcome: "failed", message: "control disabled" });
      if (!isVisible(el)) return Promise.resolve({ outcome: "failed", message: "control not visible" });
      try { el.click(); } catch (e) { return Promise.resolve({ outcome: "failed", message: "click failed" }); }
      return Promise.resolve({ outcome: "ok" });
    }

    if (type === "type") {
      if (!hasDom()) return Promise.resolve({ outcome: "skipped", message: "no page" });
      if (!selectorOk(p.selector)) return Promise.resolve({ outcome: "blocked", message: "unsafe selector" });
      var field;
      try { field = document.querySelector(p.selector); } catch (e) { field = null; }
      if (!field) return Promise.resolve({ outcome: "failed", message: "field not found" });
      var tag = (field.tagName || "").toLowerCase();
      var editable = tag === "input" || tag === "textarea" || field.isContentEditable;
      if (!editable) return Promise.resolve({ outcome: "failed", message: "field is not editable" });
      try {
        var text = String(p.text || "");
        if (tag === "input" || tag === "textarea") field.value = text;
        else field.textContent = text;
        fireEvent(field, "input");
        fireEvent(field, "change");
      } catch (e) { return Promise.resolve({ outcome: "failed", message: "typing failed" }); }
      return Promise.resolve({ outcome: "ok" });
    }

    return Promise.resolve({ outcome: "blocked", message: "unsupported action" });
  }

  // ── lifecycle driver ──────────────────────────────────────────────────────
  function newTask(goal) {
    return {
      task_id: "",
      goal: goal,
      state: "idle",
      steps: [],
      index: 0,
      executed: {},
      pending: null,
      resumeState: "working",
      message: "",
      error: ""
    };
  }

  function adoptPlan(plan) {
    active.task_id = String(plan.task_id || "");
    active.steps = Array.isArray(plan.steps) ? plan.steps.slice(0, 12) : [];
    for (var i = 0; i < active.steps.length; i++) {
      var s = active.steps[i] || {};
      if (!s.step_id) s.step_id = "step_" + i + "_" + nowMs();
      if (!s.permission) s.permission = permissionFor(s);
      if (s.consequential === undefined) s.consequential = isConsequential(s);
    }
  }

  function fail(message) {
    if (!active || isTerminal(active.state)) return;
    active.error = String(message || "task failed");
    active.message = active.error;
    setState("failed");
    emit("failed");
  }

  function finish(message) {
    if (!active || isTerminal(active.state)) return;
    active.message = String(message || "");
    setState("completed");
    emit("completed");
  }

  function drive(token) {
    if (token !== driveToken) return;
    var task = active;
    if (!task || isTerminal(task.state) || task.state === "paused") return;
    if (task.state === "awaiting_confirmation") return;

    var step = task.steps[task.index];
    if (!step) { finish("All steps done."); return; }

    if (task.executed[step.step_id]) { task.index++; drive(token); return; }

    if (step.blocked) {
      task.pending = step;
      task.message = "Permission required: " + (step.permission || "unknown") +
        " — grant it in the task panel, then Approve.";
      setState("awaiting_confirmation");
      return;
    }

    if (isConsequential(step)) {
      task.pending = step;
      task.message = "Confirm: " + (step.label || step.type);
      setState("awaiting_confirmation");
      return;
    }

    runStep(token, step);
  }

  function runStep(token, step) {
    setState("working");
    active.message = "";
    var budget = step && step.type === "respond" ? 5000 : DEFAULT_ACTION_TIMEOUT_MS;
    withTimeout(executeStep(step), budget, "action timed out").then(function (res) {
      if (token !== driveToken || !active) return;
      if (active.state === "cancelled") return;
      active.executed[step.step_id] = true;
      record(step, res.outcome, res.message);
      if (res.outcome === "failed") { fail(res.message || "step failed"); return; }
      if (res.outcome === "blocked") {
        active.pending = step;
        active.message = res.message || "Step blocked — needs permission.";
        setState("awaiting_confirmation");
        return;
      }
      active.index++;
      var task = active;
      if (task.state !== "paused") {
        if (task.state === "awaiting_confirmation") return; // shouldn't happen
        drive(token);
      }
    }).catch(function () {
      if (token !== driveToken) return;
      fail("action error");
    });
    emit("working");
  }

  // ── public actions ────────────────────────────────────────────────────────
  function submit(request) {
    var text = String(request || "").trim();
    if (!text) return false;
    // Starting a new task cancels any prior in-flight work (token bump).
    if (active && !isTerminal(active.state)) cancel("replaced by a new task");
    active = newTask(text);
    var token = ++driveToken;
    setState("planning");
    emit("planning");

    if (typeof opts.apiPost !== "function") { fail("task API unavailable"); return true; }
    var reqId = "req_" + (++reqSeq) + "_" + nowMs();
    var pending;
    try {
      pending = opts.apiPost("/api/task/plan", {
        request: text,
        permissions: getPermissions(),
        request_id: reqId
      });
    } catch (e) { fail("task API unavailable"); return true; }

    withTimeout(
      Promise.resolve(pending).then(function (resp) {
        return resp && typeof resp.json === "function" ? resp.json() : resp;
      }),
      PLANNER_TIMEOUT_MS,
      "planner timed out"
    ).then(function (data) {
      if (token !== driveToken) return;
      if (data && data.timeout) {
        fail("planning took too long — please try again");
        return;
      }
      if (data && data.status === "success" && data.plan) {
        adoptPlan(data.plan);
        drive(token);
      } else {
        fail((data && data.message) || "planner returned no plan");
      }
    }).catch(function () {
      if (token !== driveToken) return;
      fail("planner unreachable");
    });
    return true;
  }

  function approve() {
    var task = active;
    if (!task || task.state !== "awaiting_confirmation" || !task.pending) return false;
    var step = task.pending;
    // Re-check the permission gate at approval time (server is authoritative).
    if (step.permission && getPermissions().indexOf(step.permission) === -1) {
      task.message = "Still missing permission: " + step.permission;
      emit("state");
      return false;
    }
    task.pending = null;
    var token = driveToken;
    runStep(token, step);
    return true;
  }

  function deny() {
    var task = active;
    if (!task || task.state !== "awaiting_confirmation") return false;
    if (task.pending) record(task.pending, "denied", "denied by user");
    task.pending = null;
    cancel("denied by user");
    return true;
  }

  function pause() {
    var task = active;
    if (!task || isTerminal(task.state) || task.state === "paused") return false;
    task.resumeState = task.state === "awaiting_confirmation" ? "awaiting_confirmation" : "working";
    setState("paused");
    emit("paused");
    return true;
  }

  function resume() {
    var task = active;
    if (!task || task.state !== "paused") return false;
    if (task.pending) {
      setState("awaiting_confirmation");
    } else {
      setState("working");
      drive(driveToken);
    }
    emit("resumed");
    return true;
  }

  function cancel(reason) {
    ++driveToken; // invalidate any in-flight plan fetch / step execution
    var task = active;
    if (!task || isTerminal(task.state)) return false;
    task.pending = null;
    task.error = "";
    task.message = String(reason || "cancelled");
    setState("cancelled");
    if (typeof opts.onStop === "function") { try { opts.onStop(); } catch (e) { /* non-fatal */ } }
    emit("cancelled");
    return true;
  }

  function current() { return snapshot(); }

  var api = {
    STATES: STATES.slice(),
    TERMINAL: TERMINAL.slice(),
    CONSEQUENTIAL: CONSEQUENTIAL,
    PERMISSION: PERMISSION,
    init: function (o) { opts = Object.assign({}, opts, o || {}); return api; },
    submit: submit,
    approve: approve,
    deny: deny,
    pause: pause,
    resume: resume,
    cancel: cancel,
    current: current,
    getState: function () { return active ? active.state : "idle"; },
    // pure helpers exposed for testing/reuse
    statusFor: statusFor,
    isConsequential: isConsequential,
    permissionFor: permissionFor,
    canTransition: canTransition,
    transition: transition,
    selectorOk: selectorOk,
    urlOk: urlOk,
    workspaceOk: workspaceOk,
    WORKSPACES: WORKSPACES.slice(),
    PLANNER_TIMEOUT_MS: PLANNER_TIMEOUT_MS
  };

  root.VMTaskController = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window
   : (typeof globalThis !== "undefined" ? globalThis : this));
