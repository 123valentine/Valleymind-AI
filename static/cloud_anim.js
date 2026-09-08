(function () {
  "use strict";

  // ValleyMind Cloud animation/state layer.
  //
  // The visible Cloud is static/cloud.png — a SINGLE flattened transparent
  // image. Eyes, brows, mouth, arms and legs cannot move independently in
  // that asset. This module therefore drives the EXACT character as one rigid
  // whole: every emotional state is expressed through whole-character body
  // articulation (breathing, bob, lean, squash, gaze-lean) applied as a pure
  // CSS transform, so the PNG is never replaced, stretched or redrawn.
  //
  // The per-state face/arms/legs descriptors below are the animation-ready
  // blueprint for part-rigged assets later. Importing such assets is the only
  // way to get genuinely independent part motion; until then the system is
  // honest: it simulates life through body language, not fake part control.
  //
  // This is the ONE centralized Cloud animation system. The future Brain only
  // calls cloudSetState("thinking") etc.; transitions, timing, idle life and
  // gestures all live here. Cleanup/teardown is handled by the element owner
  // (static/cloud.js), which simply removes the character.

  var CHARACTER_ID = "vmCloudCharacter";
  var MINI_ORB_ID = "vmCloudCompanionMiniOrb";
  var TAU = Math.PI * 2;

  // Emotional/interaction state machine. transient states automatically return
  // to the last stable state after `hold` ms.
  var STATES = [
    { key: "idle", label: "Idle", transient: false, hold: 0 },
    { key: "listening", label: "Listening", transient: false, hold: 0 },
    { key: "thinking", label: "Thinking", transient: true, hold: 7000 },
    { key: "happy", label: "Happy", transient: true, hold: 5000 },
    { key: "excited", label: "Excited", transient: true, hold: 4500 },
    { key: "sad", label: "Sad", transient: true, hold: 5500 },
    { key: "surprised", label: "Surprised", transient: true, hold: 2600 },
    { key: "confused", label: "Confused", transient: true, hold: 5000 },
    { key: "concerned", label: "Concerned", transient: true, hold: 5200 },
    { key: "greeting", label: "Greeting", transient: true, hold: 4000 },
    { key: "speaking", label: "Speaking", transient: false, hold: 0 },
    { key: "sleeping", label: "Sleeping", transient: false, hold: 0 }
  ];

  var STATE_KEYS = {};
  STATES.forEach(function (s) { STATE_KEYS[s.key] = s; });

  // Whole-character poses. tx/ty are px, rot degrees, sx/sy scale factors.
  // breath/bob set the amplitude of the breathing/bobbing oscillation. gaze is
  // a normalized eye-direction descriptor (0 = center) that a flattened PNG
  // cannot physically show, so it drives a subtle whole-body lean instead.
  var POSES = {
    idle: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.014, breatheHz: 0.9, bob: 0.9,
      gaze: { x: 0, y: 0 },
      face: { eyes: "center", brows: "neutral", mouth: "neutral" },
      arms: "rest", legs: "stand"
    },
    listening: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.019, breatheHz: 1.2, bob: 1,
      gaze: { x: 0.1, y: 0 },
      face: { eyes: "listening", brows: "gentle", mouth: "neutral" },
      arms: "rest", legs: "stand"
    },
    thinking: {
      tx: 0, ty: -3, rot: -2.5, sx: 1, sy: 0.99,
      breath: 0.011, breatheHz: 0.62, bob: 0.4,
      gaze: { x: 0.4, y: -0.3 },
      face: { eyes: "up_away", brows: "knit", mouth: "thin" },
      arms: "chin", legs: "stand"
    },
    happy: {
      tx: 0, ty: -1, rot: 0, sx: 1.02, sy: 1.02,
      breath: 0.02, breatheHz: 1.35, bob: 1.5,
      gaze: { x: 0, y: -0.15 },
      face: { eyes: "soft", brows: "gentle", mouth: "smile" },
      arms: "rest", legs: "stand"
    },
    excited: {
      tx: 0, ty: -2, rot: 0, sx: 1.03, sy: 1.05,
      breath: 0.026, breatheHz: 1.8, bob: 2.4,
      gaze: { x: 0, y: -0.25 },
      face: { eyes: "wide", brows: "lifted", mouth: "big_smile" },
      arms: "lift", legs: "bounce"
    },
    sad: {
      tx: 0, ty: 1, rot: 0, sx: 0.98, sy: 0.97,
      breath: 0.011, breatheHz: 0.72, bob: 0.4,
      gaze: { x: 0, y: 0.15 },
      face: { eyes: "lowered", brows: "knit", mouth: "frown" },
      arms: "droop", legs: "stand"
    },
    surprised: {
      tx: 0, ty: -2, rot: 2, sx: 1.05, sy: 1.06,
      breath: 0.028, breatheHz: 1.9, bob: 2.6,
      gaze: { x: 0, y: 0 },
      face: { eyes: "wide", brows: "arched", mouth: "open" },
      arms: "recoil", legs: "stand"
    },
    confused: {
      tx: -1, ty: 0, rot: -3, sx: 0.99, sy: 0.99,
      breath: 0.015, breatheHz: 1, bob: 0.7,
      gaze: { x: -0.3, y: -0.1 },
      face: { eyes: "squint", brows: "asym", mouth: "wry" },
      arms: "chin", legs: "shift"
    },
    concerned: {
      tx: 0, ty: 0.5, rot: 1, sx: 0.995, sy: 0.99,
      breath: 0.012, breatheHz: 0.82, bob: 0.5,
      gaze: { x: -0.2, y: 0.1 },
      face: { eyes: "narrow", brows: "worried", mouth: "concern" },
      arms: "clasp", legs: "stand"
    },
    curious: {
      tx: 1, ty: -3, rot: 2.5, sx: 1.01, sy: 1,
      breath: 0.015, breatheHz: 1.1, bob: 1.1,
      gaze: { x: 0.3, y: -0.25 },
      face: { eyes: "wide_half", brows: "one_up", mouth: "open_small" },
      arms: "chin", legs: "lean"
    },
    greeting: {
      tx: 0, ty: -1, rot: 1.5, sx: 1.01, sy: 1.01,
      breath: 0.018, breatheHz: 1.25, bob: 1.6,
      gaze: { x: -0.4, y: 0.1 },
      face: { eyes: "soft", brows: "gentle", mouth: "smile" },
      arms: "wave", legs: "stand"
    },
    speaking: {
      tx: 0, ty: 0, rot: 0, sx: 1, sy: 1,
      breath: 0.024, breatheHz: 1.6, bob: 1.3,
      gaze: { x: 0, y: -0.1 },
      face: { eyes: "center", brows: "neutral", mouth: "speak" },
      arms: "rest", legs: "stand"
    },
    sleeping: {
      tx: 0, ty: 3, rot: 5, sx: 0.99, sy: 0.94,
      breath: 0.03, breatheHz: 0.4, bob: 0.2,
      gaze: { x: 0, y: 1 },
      face: { eyes: "closed", brows: "neutral", mouth: "soft" },
      arms: "rest", legs: "sit"
    }
  };

  // Mood multipliers on breath/bob so emotional states move a little more than
  // neutral without ever bouncing constantly.
  var MOOD = {
    idle: 1, listening: 1.1, thinking: 0.8, happy: 1.3, excited: 1.6,
    sad: 0.7, surprised: 1.5, confused: 1, concerned: 0.85,
    curious: 1.15, greeting: 1.4, speaking: 1.3, sleeping: 0.5
  };

  // One-shot whole-body gestures (idle life). Each is a normalized keyframe
  // list over `dur` ms; fields blend additively on top of the pose. originY
  // (percent) can redirect the squash pivot — "blink" closes a vertical slit
  // at the approximated eye line of the flattened PNG.
  var GESTURES = {
    blink: {
      dur: 150, originY: 35,
      keys: [
        { at: 0, sy: 1 },
        { at: 0.3, sy: 0.93 },
        { at: 0.55, sy: 0.97 },
        { at: 1, sy: 1 }
      ]
    },
    look: {
      dur: 1100,
      keys: [
        { at: 0, rot: 0, tx: 0 },
        { at: 0.2, rot: 3.5, tx: 1.5 },
        { at: 0.8, rot: 3.5, tx: 1.5 },
        { at: 1, rot: 0, tx: 0 }
      ]
    },
    lookAlt: {
      dur: 1100,
      keys: [
        { at: 0, rot: 0, tx: 0 },
        { at: 0.2, rot: -3, tx: -1.5 },
        { at: 0.8, rot: -3, tx: -1.5 },
        { at: 1, rot: 0, tx: 0 }
      ]
    },
    shift: {
      dur: 1500,
      keys: [
        { at: 0, tx: 0, ty: 0 },
        { at: 0.15, tx: 1.5, ty: 0.5 },
        { at: 0.85, tx: 1.5, ty: 0.5 },
        { at: 1, tx: 0, ty: 0 }
      ]
    },
    nod: {
      dur: 850,
      keys: [
        { at: 0, rot: 0, ty: 0 },
        { at: 0.25, rot: 2.5, ty: -0.5 },
        { at: 0.55, rot: -1.5, ty: 0 },
        { at: 1, rot: 0, ty: 0 }
      ]
    },
    tiltHead: {
      dur: 1500,
      keys: [
        { at: 0, rot: 0 },
        { at: 0.2, rot: 4 },
        { at: 0.85, rot: 4 },
        { at: 1, rot: 0 }
      ]
    },
    leanFwd: {
      dur: 1200,
      keys: [
        { at: 0, tx: 0, rot: 0 },
        { at: 0.2, tx: 2, rot: -2 },
        { at: 0.85, tx: 2, rot: -2 },
        { at: 1, tx: 0, rot: 0 }
      ]
    },
    hop: {
      dur: 700,
      keys: [
        { at: 0, ty: 0 },
        { at: 0.25, ty: -5 },
        { at: 0.6, ty: 0 },
        { at: 1, ty: 0 }
      ]
    },
    recoil: {
      dur: 600,
      keys: [
        { at: 0, tx: 0, ty: 0, rot: 0, sy: 1 },
        { at: 0.25, tx: -4, ty: -2, rot: -4, sy: 0.96 },
        { at: 1, tx: 0, ty: 0, rot: 0, sy: 1 }
      ]
    },
    leanBack: {
      dur: 1300,
      keys: [
        { at: 0, tx: 0, ty: 0, rot: 0 },
        { at: 0.2, tx: -2, ty: -1, rot: 3 },
        { at: 0.85, tx: -2, ty: -1, rot: 3 },
        { at: 1, tx: 0, ty: 0, rot: 0 }
      ]
    }
  };

  // Gestures queued when certain states are entered (thinking gets a thoughtful
  // lean; surprised a recoil; greeting a nod; etc.).
  var STATE_ENTRY_GESTURES = {
    thinking: ["leanBack"],
    surprised: ["recoil"],
    greeting: ["nod"],
    curious: ["look"],
    confused: ["tiltHead"],
    happy: ["nod"],
    excited: ["hop"],
    sad: ["leanBack"],
    concerned: ["tiltHead"]
  };

  var IDLE_GESTURES = ["look", "lookAlt", "shift", "nod", "tiltHead", "leanFwd"];

  function rand(min, max) { return min + Math.random() * (max - min); }
  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function lerp(a, b, t) { return a + (b - a) * t; }

  // ---- Engine state -------------------------------------------------------
  var _state = "idle";
  var _stable = "idle";
  var _el = null;
  var _running = false;
  var _dragging = false;
  var _intensity = 0.5;
  var _reduced = false;
  var _rafId = 0;
  var _lastT = 0;
  var _clock = 0;
  var _returnTimer = 0;
  var _onChangeCb = null;
  var _demoTimer = 0;

  // Smoothed pose easing toward the current state's target pose.
  var _cur = {
    tx: 0, ty: 0, rot: 0, sx: 1, sy: 1, breath: 0.014, breatheHz: 0.9,
    bob: 0.9, gx: 0, gy: 0
  };

  // Active one-shot gesture, or null.
  var _gesture = null;
  var _gestureStart = 0;

  // Randomized idle-life scheduler timestamps (ms).
  var _nextBlink = 0;
  var _nextLook = 0;
  var _nextShift = 0;
  var _nextGesture = 0;

  function resolveElement() {
    var a = document.getElementById(CHARACTER_ID);
    if (a && a.isConnected) return a;
    var b = document.getElementById(MINI_ORB_ID);
    if (b && b.isConnected) return b;
    return null;
  }

  function adopt(el) {
    if (el.getAttribute("data-vm-anim") === "1") return;
    el.setAttribute("data-vm-anim", "1");
    el.style.transformOrigin = "50% 100%";
    el.style.willChange = "transform";
    el.addEventListener("pointerdown", function () { _dragging = true; });
    el.addEventListener("pointerup", function () { _dragging = false; });
    el.addEventListener("pointercancel", function () { _dragging = false; });
  }

  function poseFor(key) { return POSES[key] || POSES.idle; }

  function entryGesture(key) {
    var g = STATE_ENTRY_GESTURES[key];
    if (!g) return null;
    return GESTURES[g[Math.floor(Math.random() * g.length)]];
  }

  // Evaluate a gesture at normalized time 0..1 (clamped) into additive fields.
  function evalGesture(gest, at) {
    var out = { tx: 0, ty: 0, rot: 0, sx: 0, sy: 0, originY: null };
    if (!gest) return out;
    if (gest.originY != null) out.originY = gest.originY;
    var keys = gest.keys;
    if (!keys || !keys.length) return out;
    var t = Math.max(0, Math.min(1, at));
    var l = 0, r = keys.length - 1;
    if (t <= keys[0].at) l = r = 0;
    else if (t >= keys[r].at) l = r = r;
    else {
      for (var i = 0; i < keys.length - 1; i++) {
        if (t >= keys[i].at && t <= keys[i + 1].at) { l = i; r = i + 1; break; }
      }
    }
    var k0 = keys[l], k1 = keys[r], span = (k1.at - k0.at) || 1;
    var et = easeInOutCubic((t - k0.at) / span);
    out.tx = lerp(k0.tx || 0, k1.tx || 0, et);
    out.ty = lerp(k0.ty || 0, k1.ty || 0, et);
    out.rot = lerp(k0.rot || 0, k1.rot || 0, et);
    out.sx = lerp((k0.sx || 0) - 1, (k1.sx || 0) - 1, et);
    out.sy = lerp((k0.sy || 0) - 1, (k1.sy || 0) - 1, et);
    return out;
  }

  function isCharacterHidden(el) {
    if (!el || !el.isConnected) return true;
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return true;
    if (cs.opacity === "0") return true;
    return false;
  }

  function scheduleIdleLife(now) {
    _nextBlink = now + rand(2600, 6800);
    _nextLook = now + rand(7000, 16000);
    _nextShift = now + rand(11000, 22000);
    _nextGesture = now + rand(24000, 50000);
  }

  function triggerBlink() {
    _gesture = GESTURES.blink;
    _gestureStart = performance.now();
    _nextBlink = _gestureStart + rand(2600, 6800);
  }

  function triggerIdleGesture(kind) {
    var g = GESTURES[kind];
    if (!g) return;
    _gesture = g;
    _gestureStart = performance.now();
  }

  // ---- Main loop ----------------------------------------------------------
  function frame(now) {
    if (!_running) return;
    if (_rafId) cancelAnimationFrame(_rafId);
    if (_dragging || _reduced || document.hidden || isCharacterHidden(_el)) {
      _rafId = requestAnimationFrame(frame);
      return;
    }
    var dt = Math.min(64, (now - _lastT) || 16);
    _lastT = now;
    _clock += dt;

    if (!_el) {
      _el = resolveElement();
      if (!_el) { _rafId = requestAnimationFrame(frame); return; }
      adopt(_el);
    }

    // Auto-return from transient states to the last stable state.
    if (_returnTimer > 0) {
      _returnTimer -= dt;
      if (_returnTimer <= 0) {
        setState(_stable);
        _returnTimer = 0;
      }
    }

    // Randomized idle life.
    if (STATE_KEYS[_state] && !STATE_KEYS[_state].transient) {
      if (now >= _nextBlink) triggerBlink();
      if (now >= _nextLook && !_gesture) {
        triggerIdleGesture(pick(["look", "lookAlt"]));
        _nextLook = now + rand(7000, 16000);
      }
      if (now >= _nextShift && !_gesture) {
        triggerIdleGesture("shift");
        _nextShift = now + rand(11000, 22000);
      }
      if (now >= _nextGesture && !_gesture && _state === "idle") {
        triggerIdleGesture(pick(IDLE_GESTURES));
        _nextGesture = now + rand(24000, 50000);
      }
    }

    var pose = poseFor(_state);
    var mood = MOOD[_state] || 1;
    var wrap = pose.breath * mood * _intensity;
    var stride = pose.bob * mood * _intensity;

    var gx = pose.gaze.x * 2 * _intensity;
    var gy = pose.gaze.y * 1.5 * _intensity;

    // Soft eased settling on the target pose.
    _cur.tx += ((pose.tx + gx) - _cur.tx) * 0.045;
    _cur.ty += ((pose.ty + gy) - _cur.ty) * 0.045;
    _cur.rot += (pose.rot - _cur.rot) * 0.045;
    _cur.sx += (pose.sx - _cur.sx) * 0.045;
    _cur.sy += (pose.sy - _cur.sy) * 0.045;
    _cur.breath += (wrap - _cur.breath) * 0.045;
    _cur.breatheHz += (pose.breatheHz - _cur.breatheHz) * 0.045;
    _cur.bob += (stride - _cur.bob) * 0.045;

    var breathePx = _cur.breath * 120;
    var bobPx = _cur.bob * (1 + Math.sin(_clock / 1000 * TAU * _cur.breatheHz)) * 0.5;

    var tx = _cur.tx + breathePx * 0.3 * Math.sin(_clock / 1000 * TAU * _cur.breatheHz);
    var ty = _cur.ty + bobPx;
    var rot = _cur.rot;
    var sx = _cur.sx + breathePx * 0.2 * Math.cos(_clock / 1000 * TAU * _cur.breatheHz);
    var sy = _cur.sy;

    var originY = null;

    // One-shot gesture overlay.
    if (_gesture) {
      var dur = _gesture.dur || 600;
      var elapsed = now - _gestureStart;
      var at = elapsed / dur;
      if (at >= 1) {
        _gesture = null;
      } else {
        var g = evalGesture(_gesture, at);
        tx += g.tx;
        ty += g.ty;
        rot += g.rot;
        sx += g.sx;
        sy += g.sy;
        if (g.originY != null) originY = g.originY;
      }
    }

    var trStr = "translate3d(" + tx.toFixed(2) + "px," + ty.toFixed(2) + "px,0)" +
      " rotate(" + rot.toFixed(2) + "deg)" +
      " scale(" + sx.toFixed(3) + "," + sy.toFixed(3) + ")";
    _el.style.transform = trStr;
    _el.style.transformOrigin = originY != null ? ("50% " + originY + "%") : "50% 100%";

    _rafId = requestAnimationFrame(frame);
  }

  function boot() {
    if (_reduced) return;
    _lastT = performance.now();
    scheduleIdleLife(_lastT);
    if (!_running) {
      _running = true;
      _rafId = requestAnimationFrame(frame);
    }
  }

  function halt() {
    _running = false;
    if (_rafId) cancelAnimationFrame(_rafId);
    _rafId = 0;
  }

  function setState(key) {
    if (!STATE_KEYS[key]) return false;
    var was = _state;
    if (key !== was) {
      _state = key;
      var def = STATE_KEYS[key];
      _returnTimer = def.transient ? def.hold : 0;
      if (def.transient) _stable = was;
      var ge = entryGesture(key);
      if (ge) { _gesture = ge; _gestureStart = performance.now(); }
      if (_onChangeCb) _onChangeCb(key, was);
    }
    return true;
  }

  function getCapabilities() {
    return {
      independentEyes: false,
      independentEyebrows: false,
      independentMouth: false,
      independentArms: false,
      independentLegs: false,
      wholeBodyArticulation: true,
      needsPartAssetsForFaceAndLimbs: true,
      asset: "static/cloud.png",
      assetType: "single flattened PNG"
    };
  }

  // ---- Public API ---------------------------------------------------------
  window.cloudSetState = function (key) {
    if (!_running) boot();
    return setState(key);
  };

  window.vmCloudAnim = {
    setState: setState,
    getState: function () { return _state; },
    listStates: function () {
      return STATES.map(function (s) { return s.key; });
    },
    getCapabilities: getCapabilities,
    start: function () { boot(); },
    stop: function () { halt(); },
    reset: function () { setState("idle"); _stable = "idle"; },
    interrupt: function () {
      if (!_running) boot();
    },
    setIntensity: function (v) {
      _intensity = Math.max(0, Math.min(1, v != null ? v : 0.5));
      return _intensity;
    },
    onStateChange: function (cb) { _onChangeCb = typeof cb === "function" ? cb : null; },
    demo: function (total) {
      var self = this;
      var steps = total || 1;
      var all = self.listStates();
      self.setState(all[0]);
      if (_demoTimer) clearInterval(_demoTimer);
      var i = 0;
      _demoTimer = setInterval(function () {
        i++;
        if (i >= all.length) { clearInterval(_demoTimer); return; }
        self.setState(all[i]);
      }, Math.max(800, 2200 / Math.max(1, steps)));
      return all.length;
    }
  };

  function reduceMedia() {
    var mq = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    _reduced = mq.matches;
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", function (e) { _reduced = e.matches; });
    }
  }

  reduceMedia();

  document.addEventListener("visibilitychange", function () {
    // Reset the clock so the frame loop does not jump forward after a hidden
    // tab returns. Idle-life timers simply wait their turn on the next frame.
    _lastT = performance.now();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // The element owner (static/cloud.js) removes the character on logout; the
  // loop self-recovers when a character appears again, so nothing more is
  // needed here for lifecycle.
})();